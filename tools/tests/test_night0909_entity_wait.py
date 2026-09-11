"""Offline tests of the delivered c82e candidate, NOT deployed wait_for.

NIGHT0909_SERVER_SOURCE selects the immutable BEFORE for the red control.
No sockets, daemon, registry, Steam or DayZ processes are used.
"""
from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(os.environ.get("NIGHT0909_SERVER_SOURCE", str(ROOT / "tools/dayz_mcp/server.py")))
loader = importlib.machinery.SourceFileLoader("night0909_server_subject", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
subject = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = subject
loader.exec_module(subject)


def predicate(field="cargo_count", equals=1):
    return {"type": "WoodenCrate", "pos": [7500.0, 10.0, 7500.0], "radius": 3.0, "field": field, "equals": equals}


def reading(**fields):
    return {"ok": 1, "telemetry": {"found": 1, **fields}}


class Runtime:
    def __init__(self, responses):
        self.tool_lock = asyncio.Lock()
        self.responses = list(responses)
        self.calls = []

    async def call_bridge(self, cmd, args, peer, timeout_s):
        assert self.tool_lock.locked(), "probe must hold the runtime lock"
        self.calls.append((cmd, args, peer, timeout_s))
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


class Clock:
    def __init__(self, runtime):
        self.now = 0.0
        self.runtime = runtime
        self.sleeps = []

    async def sleep(self, seconds):
        assert not self.runtime.tool_lock.locked(), "sleep must release the runtime lock"
        self.sleeps.append(seconds)
        self.now += seconds


class EntityWaitTest(unittest.IsolatedAsyncioTestCase):
    async def run_wait(self, runtime, entity=None, **kwargs):
        clock = Clock(runtime)
        self.clock = clock
        with patch.object(subject, "time", types.SimpleNamespace(monotonic=lambda: clock.now)), patch.object(subject, "asyncio", types.SimpleNamespace(sleep=clock.sleep)):
            return await subject.execute_wait_for(runtime, "entity_state", entity=predicate() if entity is None else entity, timeout_s=kwargs.pop("timeout_s", 2.0), poll_interval_s=kwargs.pop("poll_interval_s", 0.5), **kwargs)

    async def test_real_polling_uses_existing_server_verb_and_satisfies_third_probe(self):
        runtime = Runtime([reading(cargo_count=0), reading(cargo_count=0), reading(cargo_count=1)])
        result = await self.run_wait(runtime)
        self.assertTrue(result["satisfied"])
        self.assertEqual(result["probes"], 3)
        self.assertEqual(result["observed"], {"found": True, "field": "cargo_count", "value": 1})
        self.assertEqual(runtime.calls[0][:3], ("telemetry_read", {"mode": "object_at", "type": "WoodenCrate", "pos": [7500.0, 10.0, 7500.0], "radius": 3.0}, "server"))
        self.assertEqual(self.clock.sleeps, [0.5, 0.5])
        self.assertFalse(runtime.tool_lock.locked())

    async def test_timeout_is_unsatisfied_and_bounded(self):
        runtime = Runtime([reading(cargo_count=0)])
        result = await self.run_wait(runtime, timeout_s=0.6, poll_interval_s=20.0)
        self.assertTrue(result["ok"])
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["elapsed_s"], 0.6)
        self.assertLessEqual(runtime.calls[0][3], 0.6)

    async def test_absence_cannot_satisfy_zero_cargo(self):
        result = await self.run_wait(Runtime([reading(found=0, cargo_count=0)]), predicate(equals=0), timeout_s=0.5)
        self.assertFalse(result["satisfied"])
        self.assertNotIn("value", result["observed"])

    async def test_absence_can_satisfy_explicit_found_false(self):
        result = await self.run_wait(Runtime([reading(found=0)]), predicate("found", False))
        self.assertTrue(result["satisfied"])
        self.assertIs(result["observed"]["value"], False)

    async def test_every_supported_field(self):
        for field, value in [("found", True), ("health01", 0.5), ("attachment_count", 2), ("cargo_count", 0), ("items_total", 3)]:
            with self.subTest(field=field):
                result = await self.run_wait(Runtime([reading(**{field: value})]), predicate(field, value))
                self.assertTrue(result["satisfied"])

    async def test_bad_predicates_are_rejected_before_any_probe(self):
        invalid = [{**predicate(), "pos": [True, 0.0, 0.0]}, {**predicate(), "pos": [float("nan"), 0.0, 0.0]}, {**predicate(), "radius": 10**400}, predicate("health01", 10**400), None, {}, {**predicate(), "extra": 1}, {**predicate(), "radius": 51}, {**predicate(), "radius": True}, {**predicate(), "radius": float("nan")}, {**predicate(), "pos": [0.0, 0.0]}, {**predicate(), "type": ""}, predicate("m_IsPowered", True), predicate("engine_on_server", True), predicate("cargo_count", False), predicate("health01", float("inf")), predicate("health01", 2.0), predicate("found", 0), predicate("cargo_count", -1)]
        for value in invalid:
            runtime = Runtime([reading(cargo_count=1)])
            with self.subTest(value=value), self.assertRaisesRegex(subject.ToolError, "bad_args|entity.pos"):
                await subject.execute_wait_for(runtime, "entity_state", entity=value, timeout_s=1.0)
            self.assertEqual(runtime.calls, [])

    async def test_missing_or_invalid_data_never_becomes_false(self):
        invalid = [{}, {"ok": 1}, {"ok": 1, "telemetry": {}}, reading(cargo_count=None), reading(cargo_count=False), reading(cargo_count=-1), reading(found=2, cargo_count=1), {"ok": "true", "telemetry": {"found": 1, "cargo_count": 1}}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(subject.ToolError, "entity_state_unavailable"):
                await self.run_wait(Runtime([value]))

    async def test_bridge_error_and_ownership_error_abort_first_probe(self):
        for error in [{"ok": 0, "error": "ambiguous_fixture"}, subject.ToolError("run_not_owned"), subject.ToolError("version_blocked")]:
            runtime = Runtime([error])
            with self.subTest(error=error), self.assertRaises(subject.ToolError):
                await self.run_wait(runtime)
            self.assertEqual(len(runtime.calls), 1)
            self.assertFalse(runtime.tool_lock.locked())

    async def test_legacy_players_condition_still_works(self):
        runtime = Runtime([{"ok": 1, "players": [{}, {}]}])
        result = await subject.execute_wait_for(runtime, "players_at_least", value=2, timeout_s=1.0)
        self.assertTrue(result["satisfied"])
        self.assertEqual(runtime.calls[0][0], "query_all_players")

    async def test_public_tool_schema_and_handler_forward_predicate(self):
        runtime = Runtime([reading(cargo_count=1)])
        config = subject.ServerConfig(mode="client", key="offline-test", client_platform="codex", log_sink=lambda _: None)
        # Only construction and authority overlay are replaced. Actual registered
        # FastMCP schema, argument validation, handler and polling body execute.
        with patch.object(subject, "ClientRuntime", return_value=runtime), patch.object(subject, "_frozen_tool_registry_overlay", return_value={}):
            app, _ = subject.build_app(config)
        tool = app._tool_manager.get_tool("wait_for")
        self.assertIn("entity_state", tool.parameters["properties"]["condition"]["enum"])
        self.assertIn("entity", tool.parameters["properties"])
        result = await tool.run({"condition": "entity_state", "entity": predicate(), "timeout_s": 1.0})
        self.assertTrue(result["satisfied"])
        self.assertEqual(runtime.calls[0][0], "telemetry_read")


if __name__ == "__main__":
    unittest.main()
