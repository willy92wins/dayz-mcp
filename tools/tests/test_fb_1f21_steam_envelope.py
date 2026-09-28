from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from dayz_mcp import dayz_test_request, dayz_test_tool, native_launcher_transaction
from dayz_mcp import steam_preflight
from dayz_mcp.steam_launch_guard import Preparation, SteamIdentity
from tests.dayz_test_tool_helpers import (
    RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)
from tests import test_process_lifecycle as lifecycle
from dayz_mcp.session_coordination import ClientIdentity
from tests import process_lifecycle_helpers as lifecycle_fakes
from tests._tiers import slow_test


class SteamPreparationDaemonEnvelopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.f = lifecycle.ProcessLifecycleTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.life = self.f.lifecycle
        self.gate = self.life.steam_gate
        self.f.guard.snapshots[9001] = lifecycle_fakes.snapshot(lifecycle_fakes.process(9001))

    def start(self, **overrides):
        return self.life.start_run(
            lifecycle_fakes.IDENTITY_A, self.f.token_a, self.f.request() | overrides
        )

    def test_fb_1f21_status_keeps_wire_safe_projection_for_launching_session(self) -> None:
        prepared = Preparation(
            identity=SteamIdentity(41, 134000000000000000),
            startup="old_stable_unobserved",
            pid_repair="applied",
            restarted=True,
        )
        self.gate.action = lambda **kwargs: prepared
        self.assertTrue(self.start()["ok"])
        own = self.life.status(lifecycle_fakes.IDENTITY_A)
        projection = own.get("steam_preparation")
        self.assertEqual(
            projection,
            {
                "run_id": "run-1",
                "startup": "old_stable_unobserved",
                "pid_repair": "applied",
                "restarted": True,
            },
        )
        self.assertNotIn("identity", projection)
        self.assertNotIn("pid", projection)
        self.assertNotIn("creation_ticks", projection)
        self.assertNotIn("steam_preparation", self.life.public_status())

    def test_fb_1f21_status_for_another_session_lacks_steam_preparation(self) -> None:
        self.assertTrue(self.start()["ok"])
        self.assertIn("steam_preparation", self.life.status(lifecycle_fakes.IDENTITY_A))
        other = self.life.status(lifecycle_fakes.IDENTITY_B)
        self.assertNotIn("steam_preparation", other)
        self.assertEqual(
            other["client"]["session"], lifecycle_fakes.IDENTITY_B.session_id[:12]
        )

    def test_fb_1f21_r2_failed_relaunch_same_run_does_not_reuse_previous_preparation(
        self,
    ) -> None:
        server = lifecycle_fakes.process(701, "server")
        self.f.add_run(server)
        self.f.guard.snapshots[701] = lifecycle_fakes.snapshot(server)
        first_prep = Preparation(
            identity=SteamIdentity(41, 134000000000000000),
            startup="old_stable_unobserved",
            pid_repair="applied",
            restarted=True,
        )
        self.gate.action = lambda **kwargs: first_prep
        self.assertTrue(self.start(run_id="run-existing")["ok"])
        self.assertIn("steam_preparation", self.life.status(lifecycle_fakes.IDENTITY_A))

        second_prep = Preparation(
            identity=SteamIdentity(41, 134000000000000000),
            startup="observed",
            pid_repair="applied",
            restarted=False,
        )
        self.gate.action = lambda **kwargs: second_prep

        launcher_attempts: list[tuple[list[str], str, str]] = []

        def boom(_argv: list[str], _cwd: str, _window_style: str) -> object:
            launcher_attempts.append((_argv, _cwd, _window_style))
            raise OSError("spawn failed")

        self.life.launcher = boom
        second = self.start(run_id="run-existing")
        self.assertEqual(second.get("error"), "lifecycle_start_failed")
        self.assertEqual(len(launcher_attempts), 1)
        own = self.life.status(lifecycle_fakes.IDENTITY_A)
        self.assertNotIn("steam_preparation", own)
        startup, repair, restarted = dayz_test_tool._steam_fields_from_status(
            own, "run-existing"
        )
        self.assertIsNone(startup)
        self.assertIsNone(repair)
        self.assertIsNone(restarted)

    def test_fb_1f21_r2_rejected_relaunch_clears_previous_preparation(self) -> None:
        first_prep = Preparation(
            identity=SteamIdentity(41, 134000000000000000),
            startup="old_stable_unobserved",
            pid_repair="applied",
            restarted=True,
        )
        self.gate.action = lambda **kwargs: first_prep
        self.assertTrue(self.start()["ok"])
        self.gate.claimed = True
        self.start(run_id="run-1")
        self.assertNotIn(
            "steam_preparation", self.life.status(lifecycle_fakes.IDENTITY_A)
        )

    def test_fb_1f21_r2_daemon_projection_drops_hostile_values(self) -> None:
        prepared = Preparation(
            identity=SteamIdentity(41, 134000000000000000),
            startup=r"C:\Users\x",
            pid_repair="has space",
            restarted=1,
        )
        self.gate.action = lambda **kwargs: prepared
        self.assertTrue(self.start()["ok"])
        projection = self.life.status(lifecycle_fakes.IDENTITY_A)["steam_preparation"]
        self.assertEqual(
            projection,
            {
                "run_id": "run-1",
                "startup": None,
                "pid_repair": None,
                "restarted": None,
            },
        )

    @slow_test
    def test_fb_1f21_r2_projection_is_bounded(self) -> None:
        self.f.coordinator.release(lifecycle_fakes.IDENTITY_A, self.f.token_a)
        minted = {"n": 0}

        def next_run_id() -> str:
            minted["n"] += 1
            return f"run-{minted['n']}"

        self.life.id_fn = next_run_id
        identities: list[ClientIdentity] = []
        run_ids: list[str] = []
        for index in range(40):
            identity = ClientIdentity(
                "codex",
                2000 + index,
                1,
                "2026-07-15T00:00:00Z",
                f"sess-{index:02d}",
                "owner",
            )
            status, acquired = self.f.coordinator.acquire(identity, "lifecycle")
            self.assertEqual(status, 200, acquired)
            token = acquired["lease_token"]
            pid = 9100 + index
            self.f.launcher.pid = pid
            self.f.guard.snapshots[pid] = lifecycle_fakes.snapshot(
                lifecycle_fakes.process(pid)
            )
            started = self.life.start_run(identity, token, self.f.request())
            self.assertTrue(started.get("ok"), started)
            run_id = str(started["run_id"])
            identities.append(identity)
            run_ids.append(run_id)
            stopped = self.life.stop_run(identity, token, run_id)
            self.assertTrue(stopped.get("ok"), stopped)
            released, payload = self.f.coordinator.release(identity, token)
            self.assertEqual(released, 200, payload)
        for identity in identities[:8]:
            self.assertNotIn("steam_preparation", self.life.status(identity))
        for identity, run_id in zip(identities[-32:], run_ids[-32:]):
            projection = self.life.status(identity)["steam_preparation"]
            self.assertEqual(projection["run_id"], run_id)

    def test_fb_1f21_r3_status_does_not_wait_for_the_operation_lock(self) -> None:
        prepared = Preparation(
            identity=SteamIdentity(41, 134000000000000000),
            startup="old_stable_unobserved",
            pid_repair="applied",
            restarted=True,
        )
        self.gate.action = lambda **kwargs: prepared
        self.assertTrue(self.start()["ok"])
        held = threading.Event()
        release = threading.Event()

        def hold_operation_lock() -> None:
            self.life._operation_lock.acquire()
            held.set()
            release.wait(timeout=5.0)
            self.life._operation_lock.release()

        captured: dict[str, object] = {}

        def call_status() -> None:
            captured["payload"] = self.life.status(lifecycle_fakes.IDENTITY_A)

        holder = threading.Thread(target=hold_operation_lock)
        status_thread = threading.Thread(target=call_status)
        holder.start()
        try:
            self.assertTrue(held.wait(timeout=2.0))
            status_thread.start()
            status_thread.join(timeout=1.0)
            self.assertFalse(status_thread.is_alive())
            payload = captured["payload"]
            self.assertIn("steam_preparation", payload)
        finally:
            release.set()
            holder.join(timeout=2.0)
            if status_thread.ident is not None:
                status_thread.join(timeout=1.0)

    def test_fb_1f21_r3_long_session_id_is_not_kept_as_a_key(self) -> None:
        self.f.coordinator.release(lifecycle_fakes.IDENTITY_A, self.f.token_a)
        identity = ClientIdentity(
            "codex",
            3000,
            1,
            "2026-07-15T00:00:00Z",
            "x" * 100000,
            "owner",
        )
        status, acquired = self.f.coordinator.acquire(identity, "lifecycle")
        self.assertEqual(status, 200, acquired)
        prepared = Preparation(
            identity=SteamIdentity(41, 134000000000000000),
            startup="old_stable_unobserved",
            pid_repair="applied",
            restarted=True,
        )
        self.gate.action = lambda **kwargs: prepared
        started = self.life.start_run(
            identity, acquired["lease_token"], self.f.request()
        )
        self.assertTrue(started.get("ok"), started)
        for key in self.life._steam_preparation_by_session:
            self.assertLessEqual(len(key), 64)
        self.assertIn("steam_preparation", self.life.status(identity))


class SteamPreparationToolEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        patcher = patch.object(
            dayz_test_tool,
            "evaluate_steam_session",
            return_value=steam_preflight.SteamSessionResult(
                error_code=None,
                steam_registered_pid=1,
                steam_live_pids=(1,),
                remediation=steam_preflight.REMEDIATION,
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        vpp_patcher = patch.object(
            dayz_test_tool,
            "preflight_vpp_request",
            return_value=native_launcher_transaction.VppPreflightResult(
                error_code=None,
                missing=(),
                warnings=(),
                hint=native_launcher_transaction.VPP_PREFLIGHT_HINT,
            ),
        )
        vpp_patcher.start()
        self.addCleanup(vpp_patcher.stop)

    async def _run_with_status(self, status: dict[str, object]) -> dict[str, object]:
        policy = _policy()
        runtime = _Runtime(status)

        async def launch(raw_request: bytes, **kwargs: object) -> int:
            parsed = dayz_test_request.parse_dayz_test_request(
                raw_request, policies=(policy,)
            )
            self.assertEqual(parsed.payload["mode"], "all")
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            return await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP"],
            )

    @slow_test
    async def test_fb_1f21_tool_result_carries_fields_only_for_matching_run_id(self) -> None:
        matching = await self._run_with_status(
            {
                "runs": [],
                "steam_preparation": {
                    "run_id": RUN_ID,
                    "startup": "observed",
                    "pid_repair": "applied",
                    "restarted": True,
                },
            }
        )
        self.assertEqual(matching.get("steam_startup"), "observed")
        self.assertEqual(matching.get("steam_pid_repair"), "applied")
        self.assertIs(matching.get("steam_restarted"), True)
        mismatched = await self._run_with_status(
            {
                "runs": [],
                "steam_preparation": {
                    "run_id": "other-run",
                    "startup": "observed",
                    "pid_repair": "applied",
                    "restarted": True,
                },
            }
        )
        self.assertIsNone(mismatched.get("steam_startup"))
        self.assertIsNone(mismatched.get("steam_pid_repair"))
        self.assertIsNone(mismatched.get("steam_restarted"))

    @slow_test
    async def test_fb_1f21_hostile_preparation_values_never_reach_the_result(self) -> None:
        cases = (
            {
                "run_id": RUN_ID,
                "startup": r"C:\Steam\logs\bootstrap_log.txt",
                "pid_repair": "applied pid",
                "restarted": 1,
                "identity": {"pid": 41, "creation_ticks": 1},
                "pid": 41,
            },
            {
                "run_id": RUN_ID,
                "startup": 1,
                "pid_repair": "old stable",
                "restarted": "true",
            },
            {
                "run_id": RUN_ID,
                "startup": "observed;drop",
                "pid_repair": "applied/extra",
                "restarted": True,
            },
        )
        for prep in cases:
            with self.subTest(startup=prep.get("startup")):
                result = await self._run_with_status(
                    {"runs": [], "steam_preparation": prep}
                )
                self.assertIsNone(result.get("steam_startup"))
                self.assertIsNone(result.get("steam_pid_repair"))
                if prep.get("restarted") is True:
                    self.assertIs(result.get("steam_restarted"), True)
                else:
                    self.assertIsNone(result.get("steam_restarted"))
                self.assertNotIn("identity", result)
                self.assertNotIn("pid", result)
                self.assertNotIn("creation_ticks", result)
                self.assertNotIn("steam_preparation", result)

    async def test_fb_1f21_r2_uncorrelated_top_level_startup_is_ignored(self) -> None:
        result = await self._run_with_status(
            {"runs": [], "steam_startup": "observed"}
        )
        self.assertIsNone(result.get("steam_startup"))
        self.assertIsNone(result.get("steam_pid_repair"))
        self.assertIsNone(result.get("steam_restarted"))


if __name__ == "__main__":
    unittest.main()
