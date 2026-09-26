"""T2: bounded remediation; all process, registry, and clock operations are mocks."""
import unittest
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace
from dayz_mcp import steam_preflight as sp
from tools.tests.test_steam_preflight import _MutableSteamProvider, _FakeRemediationHost


class SteamWaitT2Tests(unittest.TestCase):
    def cycle(self):
        provider = _MutableSteamProvider()
        host = _FakeRemediationHost()
        def invoke(args):
            if args == ("-shutdown",):
                provider.shut_down()
            else:
                provider.come_back()
                provider.active_user = 0
        host.on_invoke = invoke
        return provider, host

    def test_waits_for_both_active_user_and_live_matching_image(self):
        for defect in ("user", "dead", "image", "pid"):
            with self.subTest(defect=defect):
                provider, host = self.cycle()
                original = host.on_invoke
                def invoke(args):
                    original(args)
                    if args == ("-silent",):
                        provider.active_user = 0 if defect == "user" else 7
                        if defect == "dead": provider.existing.clear()
                        if defect == "image": provider.images[41] = "steamwebhelper.exe"
                        if defect == "pid": provider.pid = 99
                host.on_invoke = invoke
                def advance(seconds):
                    host._now += seconds
                    if host._now >= 0.6:
                        provider.active_user = 7
                        provider.pid = 41
                        provider.existing = {41}
                        provider.images[41] = r"C:\Steam\steam.exe"
                host.sleep = advance
                result = sp.remediate_stale_steam_session(provider, host)
                self.assertIsNone(result.error_code)
                self.assertGreaterEqual(host._now, 0.6)
                self.assertLess(host._now, 1)
                self.assertIsNone(getattr(result, "steam_remediation_reason", None))

    def test_timeout_is_stale_with_reason_and_bounded_clock(self):
        provider, host = self.cycle()
        with patch.object(sp, "_ACTIVE_WAIT_S", 0.35):
            result = sp.remediate_stale_steam_session(provider, host)
        self.assertEqual(result.error_code, sp.STEAM_SESSION_STALE)
        self.assertEqual(getattr(result, "steam_remediation_reason", None), "active_process_timeout")
        self.assertLessEqual(host._now, 0.35)
        self.assertEqual(host.extra_args(), [("-shutdown",), ("-silent",)])

    def test_does_not_recheck_after_timeout_and_promote_late_success(self):
        provider, host = self.cycle()
        stale = sp.SteamSessionResult(sp.STEAM_SESSION_STALE, 41, (41,), sp.REMEDIATION)
        healthy = sp.SteamSessionResult(None, 41, (41,), sp.REMEDIATION)
        with patch.object(sp, "_ACTIVE_WAIT_S", 0), patch.object(
            sp, "evaluate_steam_session", side_effect=[stale, healthy]
        ) as evaluate:
            result = sp.remediate_stale_steam_session(provider, host)
        self.assertEqual(result.error_code, sp.STEAM_SESSION_STALE)
        self.assertEqual(evaluate.call_count, 1)

    def test_failed_shutdown_cannot_be_reported_as_remediated(self):
        provider = _MutableSteamProvider()
        host = _FakeRemediationHost()
        with patch.object(sp, "_SHUTDOWN_WAIT_S", 0):
            result = sp.remediate_stale_steam_session(provider, host)
        self.assertEqual(result.error_code, sp.STEAM_SESSION_STALE)
        self.assertEqual(getattr(result, "steam_remediation_reason", None), "shutdown_timeout")


class SteamEnvelopeT2Tests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_reason_and_false_are_exposed_without_launch(self):
        from tools.tests import test_dayz_test_tool as fixtures
        tool = fixtures.dayz_test_tool
        stale = sp.SteamSessionResult(sp.STEAM_SESSION_STALE, 41, (41,), sp.REMEDIATION)
        failed = SimpleNamespace(error_code=sp.STEAM_SESSION_STALE,
            steam_registered_pid=41, steam_live_pids=(41,), remediation=sp.REMEDIATION,
            steam_left_down=False, steam_remediation_reason="active_process_timeout")
        launch = AsyncMock()
        with patch.object(tool, "open_approved_launcher", return_value=fixtures._Opened()), patch.object(
            tool.secure_launcher, "load_verified_bundle", return_value=fixtures._Bundle(fixtures._sealed(fixtures._policy()))
        ), patch.object(tool, "preflight_vpp_request", return_value=SimpleNamespace(error_code=None, missing=(), warnings=(), hint="")), patch.object(
            tool, "evaluate_steam_session", return_value=stale
        ), patch.object(tool, "remediate_stale_steam_session", return_value=failed), patch.object(
            tool.secure_launcher, "execute_secure_launcher_request", new=launch
        ):
            result = await tool.execute_dayz_test_run(fixtures._Runtime(), project="ExampleMod",
                mode="client", preflight=False, run_id=fixtures.RUN_ID,
                extra_mods=["@DayZ_MCP"], auto_remediate_steam=True)
        launch.assert_not_awaited()
        self.assertIs(result["steam_remediated"], False)
        self.assertEqual(result.get("steam_remediation_reason"), "active_process_timeout")
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
