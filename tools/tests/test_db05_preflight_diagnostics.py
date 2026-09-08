"""db05: public diagnostics with an in-memory launcher, lifecycle and Steam."""
from __future__ import annotations

import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dayz_mcp import dayz_test_tool as tool, steam_preflight as steam
from tests import test_dayz_test_tool as fixtures
from tests.test_steam_preflight import _MutableSteamProvider


class Db05DiagnosticsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.policy = fixtures._policy()
        self.provider = _MutableSteamProvider()
        self.runtime = fixtures._Runtime({"runs": [{
            "run_id": fixtures.RUN_ID, "state": "RUNNING_IDLE",
            "mod": "@ExampleMod", "processes": [],
        }]})
        self.launch_error = None
        self.vpp = SimpleNamespace(error_code=None, missing=(), warnings=(), hint="")
        self.launch = AsyncMock(side_effect=self._launch)
        self.evaluate = self._patch(tool, "evaluate_steam_session",
                                    side_effect=lambda: steam.evaluate_steam_session(self.provider))
        self.remediate = self._patch(tool, "remediate_stale_steam_session")
        self._patch(tool, "open_approved_launcher", return_value=fixtures._Opened())
        self._patch(tool.secure_launcher, "load_verified_bundle",
                    return_value=fixtures._Bundle(fixtures._sealed(self.policy)))
        self._patch(tool, "preflight_vpp_request", return_value=self.vpp)
        self._patch(tool.secure_launcher, "execute_secure_launcher_request", new=self.launch)

    def _patch(self, owner, name, **kwargs):
        patcher = patch.object(owner, name, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    async def _launch(self, raw_request, **kwargs):
        request = json.loads(raw_request)
        await kwargs["execution_started_cb"]()
        exit_code = int(self.launch_error is not None)
        kwargs["output_sink"]("stdout", fixtures._terminal({
            "cleanup_degraded": False, "error_code": self.launch_error,
            "exit_code": exit_code, "ok": exit_code == 0,
            "run_id": request.get("run_id") if request["preflight"] else fixtures.RUN_ID,
        }))
        return exit_code

    async def run_tool(self, *, mode="client", **kwargs):
        if mode == "client":
            kwargs.setdefault("run_id", fixtures.RUN_ID)
        return await tool.execute_dayz_test_run(
            self.runtime, project="ExampleMod", mode=mode,
            extra_mods=["@DayZ_MCP"], **kwargs)

    async def test_preflight_green_explicitly_discloses_steam_and_attach_checks(self):
        self.provider.active_user = 0
        dry = await self.run_tool(preflight=True, auto_remediate_steam=True)
        self.assertEqual(dry["status"], "succeeded")
        self.assertEqual(dry.get("preflight_skipped_checks"),
                         ["steam_session", "extension_run", "client_replacement"])
        self.evaluate.assert_not_called()
        self.remediate.assert_not_called()
        self.assertEqual(self.runtime.lifecycle_calls, 0)
        self.assertEqual(self.runtime.bridge_calls, 0)
        real = await self.run_tool()
        self.assertEqual(real["error_code"], "steam_session_stale")
        self.assertNotIn("preflight_skipped_checks", real)
        self.assertEqual(self.launch.await_count, 1)  # only the fake preflight worker

    async def test_preflight_all_discloses_only_applicable_host_checks(self):
        result = await self.run_tool(mode="all", preflight=True)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result.get("preflight_skipped_checks"), ["steam_session"])
        self.evaluate.assert_not_called()

    async def test_preflight_server_publishes_empty_list(self):
        result = await self.run_tool(mode="server", preflight=True)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result.get("preflight_skipped_checks"), [])
        self.evaluate.assert_not_called()

    async def test_preflight_vpp_refusal_still_publishes_omissions(self):
        self.vpp.error_code = "vpp_preflight_failed"
        for mode, skipped in (("client", ["steam_session", "extension_run", "client_replacement"]),
                              ("server", [])):
            with self.subTest(mode=mode):
                result = await self.run_tool(mode=mode, preflight=True)
                self.assertEqual(result["error_code"], self.vpp.error_code)
                self.assertEqual(result.get("preflight_skipped_checks"), skipped)
        self.launch.assert_not_awaited()
        self.evaluate.assert_not_called()

    async def test_preflight_worker_failure_still_publishes_omissions(self):
        self.launch_error = "operation_cancelled"
        result = await self.run_tool(mode="all", preflight=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result.get("preflight_skipped_checks"), ["steam_session"])

    async def test_real_success_keeps_existing_envelope(self):
        result = await self.run_tool()
        self.assertEqual(result["status"], "succeeded")
        self.assertNotIn("preflight_skipped_checks", result)
        self.assertNotIn("run_not_extensible_cause", result)
        self.assertNotIn("steam_session_cause", result)
        self.launch.assert_awaited_once()

    async def test_real_replacement_refusal_keeps_existing_envelope(self):
        self.runtime.lifecycle["runs"][0]["processes"] = "unreadable"
        result = await self.run_tool()
        self.assertEqual(result["error_code"], "lifecycle_status_invalid")
        self.assertNotIn("preflight_skipped_checks", result)
        self.launch.assert_not_awaited()

    def test_extension_helper_keeps_token_and_preserves_actual_state(self):
        for state in ("STARTING", "RUNNING", "STOPPING", "EXITED", "UNRECONCILED"):
            with self.subTest(state=state):
                self.runtime.lifecycle["runs"][0]["state"] = state
                with self.assertRaises(tool.DayzTestToolError) as caught:
                    tool.require_extension_run(self.runtime.lifecycle, self.policy, fixtures.RUN_ID)
                self.assertEqual(str(caught.exception), "run_not_extensible")
                self.assertEqual(caught.exception.code, "run_not_extensible")
                self.assertEqual(getattr(caught.exception, "cause", None), state)

    async def test_extension_refusal_publishes_state_on_result(self):
        for state in ("STARTING", "RUNNING", "STOPPING", "EXITED", "UNRECONCILED"):
            with self.subTest(state=state):
                self.runtime.lifecycle["runs"][0]["state"] = state
                try:
                    result = await self.run_tool()
                except tool.DayzTestToolError as exc:
                    self.fail(f"opaque exception instead of diagnostic envelope: {exc.code}")
                self.assertEqual(result["error_code"], "run_not_extensible")
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["phase"], "validating")
                self.assertEqual(result["run_id"], fixtures.RUN_ID)
                self.assertEqual(result.get("run_not_extensible_cause"), state)
                self.assertNotIn("preflight_skipped_checks", result)
        self.launch.assert_not_awaited()
        self.assertEqual(self.runtime.bridge_calls, 0)

    async def test_missing_state_is_unknown_without_changing_rejection_token(self):
        self.runtime.lifecycle["runs"][0].pop("state")
        try:
            result = await self.run_tool()
        except tool.DayzTestToolError as exc:
            self.fail(f"opaque exception instead of diagnostic envelope: {exc.code}")
        self.assertEqual(result["error_code"], "run_not_extensible")
        self.assertEqual(result.get("run_not_extensible_cause"), "unknown")
        self.launch.assert_not_awaited()

    async def test_other_extension_errors_keep_their_contract(self):
        self.runtime.lifecycle["runs"][0]["mod"] = "@DifferentProject"
        with self.assertRaises(tool.DayzTestToolError) as caught:
            await self.run_tool()
        self.assertEqual(caught.exception.code, "run_project_mismatch")
        self.launch.assert_not_awaited()

    async def test_ordinary_stale_session_does_not_invent_exception_cause(self):
        self.provider.active_user = 0
        result = await self.run_tool()
        self.assertEqual(result["error_code"], "steam_session_stale")
        self.assertNotIn("steam_session_cause", result)
        self.launch.assert_not_awaited()

    async def test_repeated_steam_rejections_do_not_consume_attach_or_mutate_run(self):
        self.provider.active_user = 0
        before = copy.deepcopy(self.runtime.lifecycle)
        for _ in range(6):
            result = await self.run_tool()
            self.assertEqual(result["error_code"], "steam_session_stale")
        self.assertEqual(self.runtime.lifecycle, before)
        self.assertEqual(self.runtime.lifecycle_calls, 0)
        self.assertEqual(self.runtime.bridge_calls, 0)
        self.assertEqual(self.runtime.reconcile_calls, 0)
        self.launch.assert_not_awaited()
        self.remediate.assert_not_called()
        self.provider.active_user = 7
        result = await self.run_tool()
        self.assertEqual(result["status"], "succeeded")
        self.launch.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
