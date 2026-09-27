from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server
from dayz_mcp.server import ServerConfig, build_app
from tests.client_helpers import _fixture_client_runtime
from tests.mcp_helpers import _content_json
from tests.test_control_client import _policy

_STARTED_AT = "2026-09-14T12:48:11Z"
_STALE_WARNING = "tool_registry_stale_reopen_client"
_RUN_RESULT = {
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


def _identity(control, started_at_utc: str = _STARTED_AT):
    return control.ControlIdentity(
        platform="unknown",
        pid=123,
        ppid=45,
        started_at_utc=started_at_utc,
        session_id="12345678-1234-4234-8234-1234567890ab",
        task_label="fb-050e",
    )


def _untrusted_client(control, identity):
    ready = False

    def revalidate() -> None:
        if ready:
            raise ValueError("daemon_provenance_conflict")

    temporary = tempfile.TemporaryDirectory()
    keyfile = Path(temporary.name) / "daemon.key"
    keyfile.write_text("fixture-key\n", encoding="utf-8")
    policy = _policy(keyfile)
    object.__setattr__(policy, "_revalidation_hook", revalidate)
    client = control.ControlClient(policy=policy, identity=identity)
    ready = True
    return client, temporary


def _fresh_snapshot() -> dict:
    return {
        "stale": [],
        "unreadable": [],
        "unreadable_reasons": {},
        "watched_count": 3,
        "server_pid": 1,
        "server_started_at": 0.0,
    }


def _stale_snapshot() -> dict:
    payload = _fresh_snapshot()
    payload["stale"] = ["dayz_mcp.server"]
    return payload


def _unknown_snapshot() -> dict:
    payload = _fresh_snapshot()
    payload["unreadable"] = ["dayz_mcp.server"]
    payload["unreadable_reasons"] = {
        "dayz_mcp.server": "source_unreadable_now",
    }
    return payload


class Fb050eUntrustedHintTests(unittest.TestCase):
    def test_fb_050e_untrusted_hint_starts_with_policy_cause_and_contains_started_at_utc(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = _identity(control)
        client, temporary = _untrusted_client(control, identity)
        try:
            with self.assertRaises(control.ControlClientError) as raised:
                client._request_once("/session/status", {}, 1.0)
        finally:
            temporary.cleanup()
        error = raised.exception
        self.assertEqual(error.code, "client_policy_untrusted_open_new_session")
        self.assertEqual(error.request_stage, "pre_request")
        self.assertEqual(error.http_bytes_sent, 0)
        hint = error.hint
        self.assertIsInstance(hint, str)
        self.assertTrue(hint.startswith("policy_cause="))
        self.assertIn(f"client registered at {_STARTED_AT}. ", hint)
        self.assertIn("If the tool list includes server_reload", hint)

    def test_fb_050e_untrusted_hint_omits_registered_clause_when_started_at_utc_missing(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = _identity(control)
        client, temporary = _untrusted_client(control, identity)
        try:
            for missing in ("", None, 0):
                with self.subTest(started_at_utc=missing):
                    object.__setattr__(identity, "started_at_utc", missing)
                    with self.assertRaises(control.ControlClientError) as raised:
                        client._request_once("/session/status", {}, 1.0)
                    hint = raised.exception.hint
                    self.assertIsInstance(hint, str)
                    self.assertTrue(hint.startswith("policy_cause="))
                    self.assertNotIn("client registered at", hint)
                    self.assertIn("If the tool list includes server_reload", hint)
        finally:
            temporary.cleanup()

    def test_fb_050e_untrusted_hint_never_interpolates_a_non_timestamp(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = _identity(control)
        client, temporary = _untrusted_client(control, identity)
        leaks = (
            r"C:\Users\alice\secret.txt",
            r"C:\Users\alice\Z",
            "registered by alice",
            "2026-09-14T12:48:11",
            "2026-09-14T12:48:11Z C:\\Users\\alice",
            "2026-09-14\n12:48:11Z",
            "2026-09-14 12:48:11Z",
            "2026-09-14T12:48:11Z\n",
            "\uff12\uff10\uff12\uff16-09-14T12:48:11Z",
        )
        try:
            for value in leaks:
                with self.subTest(started_at_utc=value):
                    object.__setattr__(identity, "started_at_utc", value)
                    with self.assertRaises(control.ControlClientError) as raised:
                        client._request_once("/session/status", {}, 1.0)
                    error = raised.exception
                    self.assertTrue(error.hint.startswith("policy_cause="))
                    self.assertNotIn("client registered at", error.hint)
                    self.assertNotIn(value, error.hint)
                    self.assertNotIn(value, str(error))
        finally:
            temporary.cleanup()

    def test_fb_050e_untrusted_hint_keeps_an_isoformat_stamp_with_microseconds(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        stamp = "2026-09-14T12:48:11.123456Z"
        identity = _identity(control, started_at_utc=stamp)
        client, temporary = _untrusted_client(control, identity)
        try:
            with self.assertRaises(control.ControlClientError) as raised:
                client._request_once("/session/status", {}, 1.0)
        finally:
            temporary.cleanup()
        self.assertIn(f"client registered at {stamp}. ", raised.exception.hint)


class Fb050eDayzTestRunTests(unittest.IsolatedAsyncioTestCase):
    async def _call_run(self, *, snapshot=None, error=None, execute_payload=None):
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        payload = dict(_RUN_RESULT if execute_payload is None else execute_payload)

        async def execute_run(*_args: object, **_kwargs: object) -> dict:
            outgoing = dict(payload)
            warnings = outgoing.get("warnings")
            if isinstance(warnings, list):
                outgoing["warnings"] = list(warnings)
            return outgoing

        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)
        snapshot_kw = {}
        if error is not None:
            snapshot_kw["side_effect"] = error
        else:
            snapshot_kw["return_value"] = snapshot
        with (
            patch.object(server._SERVER_SOURCES, "snapshot", **snapshot_kw),
            patch.object(
                server.dayz_test_tool, "execute_dayz_test_run", side_effect=execute_run
            ),
            patch.object(
                runtime,
                "session_status",
                new=AsyncMock(
                    return_value={
                        "self": {"state": "none"},
                        "box": {
                            "occupied": False,
                            "runs": [],
                            "foreign": [],
                            "ports_in_use": [],
                            "queue": [],
                        },
                    }
                ),
            ),
        ):
            return payload, _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {"project": "ExampleMod", "mode": "server"},
                )
            )

    def _assert_unchanged(self, original: dict, result: dict) -> None:
        remainder = dict(result)
        remainder.pop("caller_tool_registry_stale")
        remainder.pop("warnings", None)
        expected = dict(original)
        expected.pop("warnings", None)
        self.assertEqual(remainder, expected)

    async def test_fb_050e_dayz_test_run_stale_observation_warns_and_keeps_result(
        self,
    ) -> None:
        original = dict(_RUN_RESULT)
        original["warnings"] = ["already_here"]
        _seed, result = await self._call_run(
            snapshot=_stale_snapshot(), execute_payload=original
        )
        self.assertIs(result["caller_tool_registry_stale"], True)
        self.assertEqual(
            result["warnings"],
            ["already_here", _STALE_WARNING],
        )
        self._assert_unchanged(original, result)

    async def test_fb_050e_dayz_test_run_unknown_observation_warns_and_keeps_result(
        self,
    ) -> None:
        original = dict(_RUN_RESULT)
        _seed, result = await self._call_run(snapshot=_unknown_snapshot())
        self.assertIs(result["caller_tool_registry_stale"], True)
        self.assertEqual(result["warnings"], [_STALE_WARNING])
        self._assert_unchanged(original, result)

    async def test_fb_050e_dayz_test_run_observe_exception_is_unknown_and_warns(
        self,
    ) -> None:
        original = dict(_RUN_RESULT)
        _seed, result = await self._call_run(error=RuntimeError("fixture observe"))
        self.assertIs(result["caller_tool_registry_stale"], True)
        self.assertEqual(result["warnings"], [_STALE_WARNING])
        self._assert_unchanged(original, result)

    async def test_fb_050e_dayz_test_run_fresh_observation_has_no_stale_warning(
        self,
    ) -> None:
        original = dict(_RUN_RESULT)
        _seed, result = await self._call_run(snapshot=_fresh_snapshot())
        self.assertIs(result["caller_tool_registry_stale"], False)
        self.assertNotIn("warnings", result)
        self.assertNotIn(_STALE_WARNING, result.values())
        self._assert_unchanged(original, result)

    async def test_fb_050e_dayz_test_run_failure_envelope_marks_stale(self) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        box = {
            "occupied": True,
            "runs": [
                {
                    "run_id": "run-live",
                    "mod": "@LFHeli",
                    "label": "heli",
                    "age_s": 44.0,
                }
            ],
            "foreign": [],
            "ports_in_use": [2302],
            "queue": [],
        }

        async def wait_box(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"ok": False, "ticket": "box-ticket", "box": box}

        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)
        with (
            patch.object(
                server._SERVER_SOURCES, "snapshot", return_value=_stale_snapshot()
            ),
            patch.object(
                server.dayz_test_tool, "execute_dayz_test_run", execute
            ),
            patch.object(server, "execute_wait_for_box", side_effect=wait_box),
            patch.object(
                runtime, "session_box_status", new=AsyncMock(return_value={})
            ),
        ):
            result = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "wait_for_box_s": 5.0,
                    },
                )
            )
        execute.assert_not_awaited()
        self.assertEqual(result.get("error_code"), "active_run_exists")
        self.assertIs(result["caller_tool_registry_stale"], True)
        self.assertIn(_STALE_WARNING, result.get("warnings") or [])

    def test_fb_050e_dayz_test_run_description_names_client_tool_timeout(self) -> None:
        app, _runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _message: None)
        )
        description = app._tool_manager.get_tool("dayz_test_run").description or ""
        self.assertIn("wait_for_box_s plus the launch", description)
        self.assertIn("Antigravity CLI", description)
        self.assertIn("180 s", description)
        self.assertIn("smaller wait_for_box_s", description)
        self.assertIn("repeat the call", description)


if __name__ == "__main__":
    unittest.main()
