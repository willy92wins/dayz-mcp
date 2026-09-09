"""Lote V product coverage: bridge_status open reason set, entities_query wire,
wait_for timeout rejection, action_use class-name contract."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server
from dayz_mcp.server import ServerConfig, ToolError, build_app


def _content_json(result) -> dict:
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)


def _tool_desc(app, name: str) -> str:
    return app._tool_manager.get_tool(name).description or ""


class BridgeStatusDescriptionTest(unittest.TestCase):
    def test_description_lists_authority_and_declares_open_set(self) -> None:
        app, _ = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        desc = _tool_desc(app, "bridge_status")
        reasons = sorted(server.READY_REASONS | set(server._FENCE_BLOCK_READY.values()))
        for reason in reasons:
            self.assertIn(reason, desc)
        low = desc.lower()
        self.assertIn("open set", low)
        self.assertIn("whitelist", low)
        self.assertIn("shape", low)

    def test_fence_mutant_surfaces_in_new_app_only(self) -> None:
        with patch.dict(server._FENCE_BLOCK_READY, {"ZZZ_PROBE": "zzz_fence_probe"}):
            app_fence, _ = build_app(
                ServerConfig(key="k", port=0, log_sink=lambda _m: None)
            )
            desc_fence = _tool_desc(app_fence, "bridge_status")
        app_after, _ = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )
        desc_after = _tool_desc(app_after, "bridge_status")
        self.assertIn("zzz_fence_probe", desc_fence)
        self.assertNotIn("zzz_fence_probe", desc_after)


class EntitiesQueryWireTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_players_probe_names_reason(self) -> None:
        app, runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )

        async def fake_call(cmd, args, peer, timeout_s):
            if cmd == "entities_query":
                return {"ok": 1, "count_total": 0, "entities": []}
            if cmd == "query_all_players":
                return {"ok": 1, "players": []}
            raise AssertionError(cmd)

        with patch.object(runtime, "call_bridge", fake_call):
            result = _content_json(
                await app.call_tool(
                    "entities_query",
                    {"pos": [1.0, 2.0, 3.0], "radius": 10.0},
                )
            )
        self.assertEqual(result["reason"], "no_player_connected")
        self.assertEqual(result["reliability"], "remote_unverified")


class WaitForWireTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_601_via_public_tool(self) -> None:
        app, runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )
        calls: list[str] = []

        async def spy(cmd, args, peer, timeout_s):
            calls.append(cmd)
            return {"ok": 1, "players": [{}]}

        with patch.object(runtime, "call_bridge", spy):
            with self.assertRaises(ToolError) as ctx:
                await app.call_tool(
                    "wait_for",
                    {
                        "condition": "players_at_least",
                        "value": 1,
                        "timeout_s": 601,
                        "poll_interval_s": 0.5,
                    },
                )
        self.assertIn("timeout_s must be <= 600", str(ctx.exception))
        self.assertEqual(calls, [])


class ActionUseDescriptionTest(unittest.TestCase):
    def test_description_names_class_name_contract(self) -> None:
        app, _ = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        desc = _tool_desc(app, "action_use")
        low = desc.lower()
        self.assertIn("class name", low)
        self.assertIn("gettype()", low)
        self.assertIn("not the visible", low)


class RunIdMatrixDescriptionTest(unittest.TestCase):
    """fb-20260829-104625-7c88: the client-requires-run_id / server|all-forbid-run_id matrix
    is published on the tool prose AND on the two properties, not only enforced by
    dayz_test_request.py with a bare bad_dayz_test_request."""

    def test_tool_and_property_descriptions_publish_the_matrix(self) -> None:
        app, _ = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        desc = _tool_desc(app, "dayz_test_run")
        self.assertIn("mode=client requires run_id", desc)
        self.assertIn("preserving the server", desc)
        self.assertIn("must NOT pass run_id", desc)
        props = app._tool_manager.get_tool("dayz_test_run").parameters["properties"]
        self.assertEqual(props["mode"]["description"], server.RUN_ID_MATRIX_MODE_DESCRIPTION)
        self.assertEqual(props["run_id"]["description"], server.RUN_ID_MATRIX_RUN_ID_DESCRIPTION)
        # The enum published from the authority survives the description patch.
        self.assertIn("server", props["mode"]["enum"])
        self.assertNotIn("offline", props["mode"]["enum"])


class PublishedExtraModsNameFormTest(unittest.TestCase):
    """fb-20260909-213257-49a9: extra_mods name-form is on the tool and the property."""

    def test_tool_and_property_descriptions_name_the_accepted_form(self) -> None:
        app, _ = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        desc = _tool_desc(app, "dayz_test_run")
        self.assertIn("single folder name", desc)
        self.assertIn("@DayZ_MCP", desc)
        self.assertIn("bad_mod", desc)
        self.assertIn("bridge_mod_missing", desc)
        self.assertNotIn("extra_mods accepts any folder", desc)
        props = app._tool_manager.get_tool("dayz_test_run").parameters["properties"]
        self.assertEqual(props["extra_mods"]["description"], server.EXTRA_MODS_DESCRIPTION)
        self.assertIn("single folder name", props["extra_mods"]["description"])
        self.assertIn("bridge_mod_missing", props["extra_mods"]["description"])


class ReachableCapabilityTest(unittest.TestCase):
    """Two capabilities were built, tested and left unreachable from the wire.

    ``auto_remediate_steam`` was accepted by execute_dayz_test_run and never
    exposed by the tool; the startup budget could only be changed by the
    environment of a process the caller does not launch. Both were complete
    features with no path from the surface the caller actually has.
    """

    def _props(self) -> dict:
        app, _ = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        return app._tool_manager.get_tool("dayz_test_run").parameters["properties"]

    def test_dayz_test_run_publishes_both_with_a_description(self) -> None:
        props = self._props()
        for field in ("auto_remediate_steam", "client_start_budget_s"):
            with self.subTest(field):
                self.assertIn(field, props)
                self.assertTrue((props[field].get("description") or "").strip())

    def test_the_budget_description_names_the_unit_and_the_range(self) -> None:
        description = self._props()["client_start_budget_s"]["description"]
        self.assertIn("second", description.lower())
        self.assertIn("3600", description)


class ReachableCapabilityWireTest(unittest.IsolatedAsyncioTestCase):
    """The schema publishing a field does not prove the value travels.

    A description attached to a parameter the handler never forwards reads
    exactly like a working feature, so this walks the path the caller actually
    has -- call_tool -> dayz_test_run -> execute_dayz_test_run -- and reads the
    kwargs that arrived at the far end.
    """

    async def _kwargs_from(self, args: dict) -> dict:
        # Client mode, like the caller's own session: dayz_test_run is a session
        # tool and a daemon-mode app refuses it before any parameter is read.
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)
        seen: dict = {}

        async def spy(*_a: object, **kwargs: object) -> dict:
            seen.update(kwargs)
            return {
                "status": "succeeded",
                "project": "ExampleMod",
                "mode": "server",
                "run_id": "12345678-1234-4234-8234-1234567890ab",
                "phase": "completed",
                "elapsed_s": 0.1,
                "artifacts_paths": [],
                "error_code": None,
                "cleanup_degraded": False,
            }

        with patch.object(server.dayz_test_tool, "execute_dayz_test_run", spy):
            await app.call_tool("dayz_test_run", {"project": "ExampleMod", **args})
        return seen

    async def test_both_values_reach_the_executor(self) -> None:
        seen = await self._kwargs_from(
            {"mode": "server", "client_start_budget_s": 5.0, "auto_remediate_steam": True}
        )
        self.assertEqual(seen.get("client_start_budget_s"), 5.0)
        self.assertIs(seen.get("auto_remediate_steam"), True)

    async def test_a_coerced_bool_cannot_disable_the_startup_guard(self) -> None:
        """P1 of the cross-family review, reproduced before it was fixed.

        pydantic coerces before any of our code runs: ``false`` used to arrive
        as ``0.0`` and ``"5"`` as ``5.0``, and a budget of zero passes every
        range check while disabling the guard entirely. Fail-open, reached by
        the input that most looks like "no". StrictFloat|StrictInt is what stops
        it, so this test dies the moment someone relaxes the annotation.
        """
        for bad in (False, True, "5", "abc"):
            with self.subTest(bad=bad), self.assertRaises(ToolError):
                await self._kwargs_from({"mode": "server", "client_start_budget_s": bad})

    async def test_an_out_of_range_budget_names_the_field_and_the_range(self) -> None:
        """P2: a bare ValueError from the executor reached the caller as
        ``dayz_test_failed:ValueError``, naming neither."""
        for bad in (-1, 3601, 4000.5):
            with self.subTest(bad=bad):
                with self.assertRaises(ToolError) as ctx:
                    await self._kwargs_from(
                        {"mode": "server", "client_start_budget_s": bad}
                    )
                message = str(ctx.exception)
                self.assertIn("client_start_budget_s", message)
                self.assertIn("3600", message)
                self.assertIn("bad_args", message)

    async def test_an_invalid_budget_never_reaches_the_box_queue(self) -> None:
        """P2: the rejection used to happen inside the executor, so an already
        invalid request could wait in the queue, take a slot and come back as
        box_queue_saturated with its real defect never reported."""
        from tests.test_client_mode import _fixture_client_runtime

        config = ServerConfig(
            mode="client", key="k", port=12345, client_platform="codex",
            log_sink=lambda _m: None,
        )
        runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)
        waits: list[object] = []

        async def spy_wait(*a: object, **k: object) -> dict:
            waits.append(a)
            return {"ok": False, "error": "box_queue_saturated", "box": {}}

        with patch.object(server, "execute_wait_for_box", spy_wait):
            with self.assertRaises(ToolError):
                await app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "client_start_budget_s": -1,
                        "wait_for_box_s": 10.0,
                    },
                )
        self.assertEqual(waits, [], "la cola no debe llegar a consultarse")

    async def test_the_defaults_are_off_and_absent(self) -> None:
        """Omitting them must not invent a budget: None is what makes the
        environment and then the measured default still apply."""
        seen = await self._kwargs_from({"mode": "server"})
        self.assertIsNone(seen.get("client_start_budget_s"))
        self.assertIs(seen.get("auto_remediate_steam"), False)


if __name__ == "__main__":
    unittest.main()
