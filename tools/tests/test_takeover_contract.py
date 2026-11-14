"""Takeover of ownerless runs and dayz_test_run's takeover contract.

Moved verbatim from test_0ab2_grace.py, test_0ab2_r9.py
(review 2026-09-25, W4d step 3).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import server as server_module
from dayz_mcp.process_lifecycle import occupancy_error_fields, takeover_target_run_id
from dayz_mcp.server import ServerConfig, TAKEOVER_REQUIRED
from tests.client_helpers import _fixture_client_runtime
from tests.mcp_helpers import _content_json
from tests._tiers import slow_test


# --- from test_0ab2_grace.py ---
# fb-20260909-213002-0ab2: lease grace S1 + takeover_required.
#
# Fixtures from docs/plan-0ab2.md (EXACT).


class TakeoverBTests(unittest.TestCase):
    def test_n5_ownerless_idle_is_not_owned(self) -> None:
        box = {
            "runs": [
                {
                    "run_id": "abc",
                    "mod": "@M",
                    "label": "lab",
                    "age_s": 1.0,
                    "state": "RUNNING_IDLE",
                    "owner_session": None,
                }
            ]
        }
        fields = occupancy_error_fields(box, caller_session="me")
        hint = str(fields.get("hint") or "")
        self.assertIn("run_protected", hint)
        self.assertNotIn("takeover=true", hint)
        self.assertNotIn("stop it with dayz_test_stop", hint)
        self.assertEqual(takeover_target_run_id(box, caller_session="me"), "abc")

    def test_owned_running_is_not_a_takeover_target(self) -> None:
        box = {
            "runs": [
                {
                    "run_id": "abc",
                    "state": "RUNNING",
                    "owner_session": "me",
                }
            ]
        }
        self.assertIsNone(takeover_target_run_id(box, caller_session="me"))


class DayzTestRunTakeoverTests(unittest.IsolatedAsyncioTestCase):
    def _config(self) -> ServerConfig:
        return ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )

    def _foreign_box(self, *, state: str, owner: str | None) -> dict[str, object]:
        return {
            "occupied": True,
            "runs": [
                {
                    "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "mod": "@M",
                    "label": "lab",
                    "age_s": 12.0,
                    "state": state,
                    "owner_session": owner,
                }
            ],
            "foreign": [],
            "ports_in_use": [2302],
            "queue": [],
        }

    async def _call(
        self,
        *,
        box: dict[str, object],
        takeover: bool,
        execute: object | None = None,
        stop: object | None = None,
    ) -> dict:
        from tests.client_helpers import _fixture_client_runtime

        runtime = _fixture_client_runtime(self._config())
        status = AsyncMock(return_value={"box": box, "self": {"state": "none"}})
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(self._config())
        args = {
            "project": "ExampleMod",
            "mode": "server",
            "takeover": takeover,
        }
        with (
            patch.object(runtime, "session_status", new=status),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_run",
                new=execute
                or AsyncMock(side_effect=AssertionError("must not launch")),
            ),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_stop",
                new=stop or AsyncMock(side_effect=AssertionError("must not stop")),
            ),
        ):
            return _content_json(await app.call_tool("dayz_test_run", args))

    async def test_p5_foreign_running_is_takeover_required(self) -> None:
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        payload = await self._call(
            box=self._foreign_box(state="RUNNING", owner="other"),
            takeover=False,
            execute=execute,
        )
        execute.assert_not_awaited()
        self.assertEqual(payload.get("status"), "failed")
        self.assertEqual(payload.get("error_code"), TAKEOVER_REQUIRED)
        self.assertEqual(
            payload.get("occupied_by_run_id"),
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )
        hint = str(payload.get("hint") or "")
        self.assertIn("takeover=true", hint)
        self.assertNotIn("stop it with dayz_test_stop", hint)

    @slow_test
    async def test_n6_ownerless_idle_is_takeover_required(self) -> None:
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        payload = await self._call(
            box=self._foreign_box(state="RUNNING_IDLE", owner=None),
            takeover=False,
            execute=execute,
        )
        execute.assert_not_awaited()
        self.assertEqual(payload.get("error_code"), "run_protected")
        hint = str(payload.get("hint") or "")
        self.assertIn("run_protected", hint)
        self.assertNotIn("takeover=true", hint)
        self.assertNotIn("stop it with dayz_test_stop", hint)

    async def test_p6_takeover_stops_then_launches(self) -> None:
        run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        stop = AsyncMock(
            return_value={
                "status": "succeeded",
                "run_id": run_id,
                "error_code": None,
            }
        )
        execute = AsyncMock(
            return_value={
                "status": "succeeded",
                "project": "ExampleMod",
                "mode": "server",
                "run_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "phase": "completed",
                "elapsed_s": 0.1,
                "artifacts_paths": [],
                "error_code": None,
                "cleanup_degraded": False,
            }
        )
        with patch("psutil.Process") as process_cls:
            kill = process_cls.return_value.kill
            payload = await self._call(
                box=self._foreign_box(state="RUNNING", owner="other"),
                takeover=True,
                execute=execute,
                stop=stop,
            )
        stop.assert_awaited()
        execute.assert_awaited()
        self.assertEqual(payload.get("status"), "succeeded")
        self.assertEqual(payload.get("evicted_run_id"), run_id)
        kill.assert_not_called()

    async def test_takeover_is_published_on_the_tool(self) -> None:
        app, _ = server_module.build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )
        props = app._tool_manager.get_tool("dayz_test_run").parameters["properties"]
        self.assertIn("takeover", props)
        self.assertTrue((props["takeover"].get("description") or "").strip())


# --- from test_0ab2_r9.py ---
# W7a DZ-R9 offline for 0ab2 (state-machine, race, identity, data-loss).
#
# In-game H8 is W7b / I3 — not this module.


class DayzTestRunIdentityR9Tests(unittest.IsolatedAsyncioTestCase):
    def _config(self) -> ServerConfig:
        return ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )

    def _box(self, *, state: str, owner: str | None, run_id: str) -> dict[str, object]:
        return {
            "occupied": True,
            "runs": [
                {
                    "run_id": run_id,
                    "mod": "@M",
                    "label": "lab",
                    "age_s": 12.0,
                    "state": state,
                    "owner_session": owner,
                }
            ],
            "foreign": [],
            "ports_in_use": [2302],
            "queue": [],
        }

    async def test_takeover_defaults_false_and_does_not_stop(self) -> None:
        run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        stop = AsyncMock(side_effect=AssertionError("must not stop"))
        runtime = _fixture_client_runtime(self._config())
        status = AsyncMock(
            return_value={
                "box": self._box(state="RUNNING", owner="other", run_id=run_id),
                "self": {"state": "none"},
            }
        )
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(self._config())
        with (
            patch.object(runtime, "session_status", new=status),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_run",
                new=execute,
            ),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_stop",
                new=stop,
            ),
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {"project": "ExampleMod", "mode": "server"},
                )
            )
        execute.assert_not_awaited()
        stop.assert_not_awaited()
        self.assertEqual(payload.get("error_code"), TAKEOVER_REQUIRED)

    async def test_owned_running_does_not_takeover_stop(self) -> None:
        run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        execute = AsyncMock(
            return_value={
                "status": "failed",
                "error_code": "active_run_exists",
                "project": "ExampleMod",
                "mode": "server",
                "run_id": None,
                "phase": "executing",
                "elapsed_s": 0.0,
                "artifacts_paths": [],
                "cleanup_degraded": False,
            }
        )
        stop = AsyncMock(side_effect=AssertionError("must not stop"))
        runtime = _fixture_client_runtime(self._config())
        caller = str(runtime.identity.session_id)
        status = AsyncMock(
            return_value={
                "box": self._box(state="RUNNING", owner=caller, run_id=run_id),
                "self": {"state": "none"},
            }
        )
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(self._config())
        with (
            patch.object(runtime, "session_status", new=status),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_run",
                new=execute,
            ),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_stop",
                new=stop,
            ),
            patch("psutil.Process") as process_cls,
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {
                        "project": "ExampleMod",
                        "mode": "server",
                        "takeover": True,
                    },
                )
            )
        stop.assert_not_awaited()
        execute.assert_awaited()
        process_cls.return_value.kill.assert_not_called()
        self.assertEqual(payload.get("error_code"), "active_run_exists")
        self.assertNotIn("evicted_run_id", payload)


# --- server_boot on the answers server.py builds itself ---
# Every dayz_test_run answer carries server_boot. The admission and refusal
# answers built in server.py, which never reach the launch, carry it null.


class DayzTestRunAdmissionServerBootTests(unittest.IsolatedAsyncioTestCase):
    _OTHER_RUN = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    def _config(self) -> ServerConfig:
        return ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )

    def _box(self, *runs: dict[str, object], **fields: object) -> dict[str, object]:
        box: dict[str, object] = {
            "occupied": bool(runs),
            "runs": list(runs),
            "foreign": [],
            "ports_in_use": [2302] if runs else [],
            "queue": [],
        }
        box.update(fields)
        return box

    def _run(self, *, state: str, owner: str | None) -> dict[str, object]:
        return {
            "run_id": self._OTHER_RUN,
            "mod": "@M",
            "label": "lab",
            "age_s": 12.0,
            "state": state,
            "owner_session": owner,
        }

    async def _call(
        self,
        *,
        box: dict[str, object],
        args: dict[str, object] | None = None,
        execute: object | None = None,
        stop: object | None = None,
        wait: object | None = None,
    ) -> dict:
        runtime = _fixture_client_runtime(self._config())
        status = AsyncMock(return_value={"box": box, "self": {"state": "none"}})
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(self._config())
        with (
            patch.object(runtime, "session_status", new=status),
            patch.object(
                runtime,
                "session_box_status",
                new=AsyncMock(return_value={"box": box}),
            ),
            patch.object(
                server_module,
                "execute_wait_for_box",
                new=wait or AsyncMock(side_effect=AssertionError("must not wait")),
            ),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_run",
                new=execute
                or AsyncMock(side_effect=AssertionError("must not launch")),
            ),
            patch.object(
                server_module.dayz_test_tool,
                "execute_dayz_test_stop",
                new=stop or AsyncMock(side_effect=AssertionError("must not stop")),
            ),
            patch("psutil.Process") as process_cls,
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {"project": "ExampleMod", "mode": "server", **(args or {})},
                )
            )
        process_cls.return_value.kill.assert_not_called()
        return payload

    def _assert_null_server_boot(self, payload: dict) -> None:
        self.assertEqual(payload.get("status"), "failed")
        self.assertIn("server_boot", payload)
        self.assertIsNone(payload["server_boot"])

    async def test_takeover_required_answer_has_null_server_boot(self) -> None:
        payload = await self._call(
            box=self._box(self._run(state="RUNNING", owner="other")),
            args={"takeover": False},
        )
        self.assertEqual(payload.get("error_code"), TAKEOVER_REQUIRED)
        self._assert_null_server_boot(payload)

    async def test_protected_run_answer_has_null_server_boot(self) -> None:
        payload = await self._call(
            box=self._box(self._run(state="RUNNING_IDLE", owner=None)),
            args={"takeover": True},
        )
        self.assertEqual(payload.get("error_code"), "run_protected")
        self._assert_null_server_boot(payload)

    async def test_queue_refusal_has_null_server_boot(self) -> None:
        payload = await self._call(
            box=self._box(port_scan_known=False),
            args={"on_busy": "queue"},
        )
        self.assertEqual(payload.get("error_code"), "port_scan_unknown")
        self._assert_null_server_boot(payload)

    async def test_failed_box_wait_has_null_server_boot(self) -> None:
        box = self._box(self._run(state="RUNNING", owner="other"))
        wait = AsyncMock(return_value={"ok": False, "ticket": None, "box": box})
        payload = await self._call(box=box, args={"wait_for_box_s": 1.0}, wait=wait)
        wait.assert_awaited_once()
        self.assertEqual(payload.get("error_code"), TAKEOVER_REQUIRED)
        self._assert_null_server_boot(payload)

    async def test_launch_refused_as_active_run_has_null_server_boot(self) -> None:
        execute = AsyncMock(
            side_effect=server_module.dayz_test_tool.DayzTestToolError(
                "active_run_exists"
            )
        )
        payload = await self._call(box=self._box(), execute=execute)
        execute.assert_awaited_once()
        self.assertEqual(payload.get("error_code"), "active_run_exists")
        self._assert_null_server_boot(payload)

    async def test_failed_takeover_stop_gains_only_null_server_boot(self) -> None:
        envelope = server_module.dayz_test_tool._failed_stop_envelope(
            self._OTHER_RUN,
            "run_not_found",
            {
                "daemon_generation_at_launch": None,
                "daemon_generation_current": "",
                "generation_changed": None,
            },
        )
        # dayz_test_stop's own envelope stays without the key.
        self.assertNotIn("server_boot", envelope)
        stop = AsyncMock(return_value=dict(envelope))
        payload = await self._call(
            box=self._box(self._run(state="RUNNING", owner="other")),
            args={"takeover": True},
            stop=stop,
        )
        stop.assert_awaited_once()
        self._assert_null_server_boot(payload)
        for key in (
            "caller_tool_registry_stale",
            "warnings",
            server_module._TOOL_REGISTRY_STALE_HINT_KEY,
        ):
            payload.pop(key, None)
        self.assertEqual(payload, {**envelope, "server_boot": None})


if __name__ == "__main__":
    unittest.main()
