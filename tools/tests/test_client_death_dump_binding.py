"""296b r2: a client dump is bound to the run whose launch it follows.

B2: the decision is a set difference against a snapshot of the CLIENT profile
roots taken before the launch, never a wall-clock window, and a server dump is
never a candidate. B1: a client that dies after dayz_test_run returned
succeeded is named in runs_retired_recently once the daemon retires the run.
r3: the baseline and the later-launch ceiling live in the daemon's
ClientDumpRegistry (cross-process cases: test_client_dumps_cross_process).
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import mcp_capture
from dayz_mcp import (
    dayz_test_request,
    dayz_test_tool,
    native_launcher_transaction,
    steam_preflight,
)
from dayz_mcp.client_dump_registry import ClientDumpRegistry
from dayz_mcp.client_steam_bootstrap import (
    ClientDumpBaseline,
    MAX_WIRE_DUMPS_PER_ROOT,
    baseline_from_wire,
    baseline_to_wire,
    diagnose_client_steam_bootstrap,
    diagnose_retired_client_death,
    snapshot_client_dumps,
)
from tests.test_dayz_test_tool import (
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)


_MARKED = b"MDMP\x00SteamInternal_SetMinidumpSteamID:  Caching Steam ID:  1 [API loaded no]\x00"
_UNMARKED = b"MDMP\x00access violation, no steam line\x00"
_GEN = "a" * 32


def _dump(root: Path, stamp: str, body: bytes) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"ErrorMessage_DayZDiag_x64_2026-09-24_{stamp}.mdmp"
    path.write_bytes(body)
    return path


def _dead_after_ack(baseline: object) -> str | None:
    return diagnose_client_steam_bootstrap(
        error_code="client_dead_after_ack",
        client_alive=False,
        steam_startup="observed",
        dump_baseline=baseline,  # type: ignore[arg-type]
    )


def _retired_row(run_id: str) -> dict[str, object]:
    return {
        "run_id": run_id,
        "daemon_generation_at_launch": _GEN,
        "daemon_generation_current": _GEN,
        "generation_changed": False,
        "event": "run_reaped",
        "reason": "all_processes_gone_or_foreign",
        "decision": "reaped",
        "state": "EXITED",
    }


def _launch(registry: ClientDumpRegistry, roots: list[Path], run_id: str) -> None:
    """dayz_test_tool's order: open with the snapshot, daemon start, bind."""
    token = registry.open(baseline_to_wire(snapshot_client_dumps(roots)))
    assert token is not None
    registry.note_launch(run_id)
    assert registry.bind(token, run_id)


class _Isolated(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ClientDumpRegistry()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.client = self.root / "_client" / "profiles"
        self.server = self.root / "_server" / "profiles"
        self.client.mkdir(parents=True)
        self.server.mkdir(parents=True)


class SolR1ReproTests(_Isolated):
    def test_a_marked_dump_written_1s_before_the_call_is_not_evidence(self) -> None:
        # Written one second before the launch: inside the old mtime window,
        # but present in the snapshot, so it cannot speak for this death.
        dump = _dump(self.client, "03-04-45", _MARKED)
        stamp = time.time() - 1
        os.utime(dump, (stamp, stamp))
        baseline = snapshot_client_dumps([self.client])
        self.assertIsNone(_dead_after_ack(baseline))

    def test_b_mode_all_server_marked_dump_does_not_name_a_client_death(self) -> None:
        policy = _policy(dev_root=str(self.root))
        roots = dayz_test_tool._client_profile_roots(policy, "all")
        self.assertEqual(
            [os.path.normcase(r) for r in roots],
            [os.path.normcase(str(self.client))],
        )
        baseline = snapshot_client_dumps(roots)
        _dump(self.server, "03-04-46", _MARKED)
        _dump(self.client, "03-04-47", _UNMARKED)
        self.assertIsNone(_dead_after_ack(baseline))

    def test_c_wall_clock_jump_after_a_new_marked_dump_still_names_it(self) -> None:
        baseline = snapshot_client_dumps([self.client])
        dump = _dump(self.client, "03-04-46", _MARKED)
        for jump in (+60.0, -3600.0):
            stamp = time.time() + jump
            os.utime(dump, (stamp, stamp))
            self.assertEqual(_dead_after_ack(baseline), "steam_bootstrap")

    def test_positive_new_marked_client_dump_names_steam_bootstrap(self) -> None:
        _dump(self.client, "03-00-00", _UNMARKED)
        baseline = snapshot_client_dumps([self.client])
        _dump(self.client, "03-04-46", _MARKED)
        self.assertEqual(_dead_after_ack(baseline), "steam_bootstrap")

    def test_a_root_created_after_the_snapshot_holds_only_new_dumps(self) -> None:
        missing = self.root / "_fresh" / "profiles"
        baseline = snapshot_client_dumps([missing])
        _dump(missing, "03-04-46", _MARKED)
        self.assertEqual(_dead_after_ack(baseline), "steam_bootstrap")

    def test_client_roots_of_every_client_mode_exclude_the_server(self) -> None:
        policy = _policy(dev_root=str(self.root))
        for mode in ("all", "client", "offline"):
            roots = dayz_test_tool._client_profile_roots(policy, mode)
            self.assertTrue(roots, mode)
            for root in roots:
                self.assertNotIn("_server", root, mode)
        self.assertEqual(dayz_test_tool._client_profile_roots(policy, "server"), [])


class RetiredRunBindingTests(_Isolated):
    def _diagnose(self, run_id: str) -> str | None:
        return diagnose_retired_client_death(
            run_id, self.registry.bindings_for([run_id])
        )

    def _rows(self, *run_ids: str) -> list[dict[str, object]]:
        rows = dayz_test_tool._runs_retired_recently(
            [_retired_row(run_id) for run_id in run_ids],
            self.registry.bindings_for(list(run_ids)),
        )
        assert rows is not None
        return rows

    def test_late_death_with_new_marked_dump_is_named_on_the_retired_row(self) -> None:
        run_id = str(uuid.uuid4())
        _launch(self.registry, [self.client], run_id)
        _dump(self.client, "03-04-46", _MARKED)
        self.assertEqual(
            self._rows(run_id)[0]["client_death_diagnosis"], "steam_bootstrap"
        )

    def test_late_death_without_new_dump_is_not_steam_bootstrap(self) -> None:
        _dump(self.client, "03-00-00", _MARKED)
        run_id = str(uuid.uuid4())
        _launch(self.registry, [self.client], run_id)
        self.assertIsNone(self._rows(run_id)[0]["client_death_diagnosis"])

    def test_unbound_run_publishes_null(self) -> None:
        _dump(self.client, "03-04-46", _MARKED)
        rows = self._rows(str(uuid.uuid4()))
        self.assertIn("client_death_diagnosis", rows[0])
        self.assertIsNone(rows[0]["client_death_diagnosis"])

    def test_no_bindings_from_the_daemon_publishes_null(self) -> None:
        run_id = str(uuid.uuid4())
        _launch(self.registry, [self.client], run_id)
        _dump(self.client, "03-04-46", _MARKED)
        for bindings in (None, {}, {run_id: None}, {run_id: {"baseline": {}}}):
            rows = dayz_test_tool._runs_retired_recently(
                [_retired_row(run_id)], bindings
            )
            self.assertIsNone(rows[0]["client_death_diagnosis"], bindings)

    def test_a_later_launch_on_the_same_roots_owns_the_dumps_after_it(self) -> None:
        first = str(uuid.uuid4())
        _launch(self.registry, [self.client], first)
        second = str(uuid.uuid4())
        _launch(self.registry, [self.client], second)
        _dump(self.client, "03-04-46", _MARKED)
        self.assertIsNone(self._diagnose(first))
        self.assertEqual(self._diagnose(second), "steam_bootstrap")

    def test_a_dump_between_two_launches_stays_with_the_first(self) -> None:
        first = str(uuid.uuid4())
        _launch(self.registry, [self.client], first)
        _dump(self.client, "03-04-46", _MARKED)
        second = str(uuid.uuid4())
        _launch(self.registry, [self.client], second)
        self.assertEqual(self._diagnose(first), "steam_bootstrap")
        self.assertIsNone(self._diagnose(second))


class ClientDumpRegistryTests(_Isolated):
    def _diagnose(self, run_id: str) -> str | None:
        return diagnose_retired_client_death(
            run_id, self.registry.bindings_for([run_id])
        )

    def _open(self) -> str | None:
        return self.registry.open(
            baseline_to_wire(snapshot_client_dumps([self.client]))
        )

    def test_a_launch_without_a_snapshot_closes_every_uncapped_run(self) -> None:
        run_id = str(uuid.uuid4())
        _launch(self.registry, [self.client], run_id)
        _dump(self.client, "03-04-46", _MARKED)
        self.registry.note_launch()
        self.assertEqual(self.registry.bindings_for([run_id]), {})
        self.assertIsNone(self._diagnose(run_id))

    def test_a_capped_run_survives_later_launches(self) -> None:
        first = str(uuid.uuid4())
        _launch(self.registry, [self.client], first)
        _dump(self.client, "03-04-46", _MARKED)
        _launch(self.registry, [self.client], str(uuid.uuid4()))
        self.registry.note_launch()
        self.assertEqual(self._diagnose(first), "steam_bootstrap")

    def test_the_launch_being_started_is_not_closed_by_its_own_start(self) -> None:
        token = self._open()
        run_id = str(uuid.uuid4())
        self.registry.note_launch(run_id)
        self.assertTrue(self.registry.bind(token, run_id))
        self.assertIn(run_id, self.registry.bindings_for([run_id]))

    def test_r4_b1_a_second_launch_before_the_bind_makes_it_unattributable(
        self,
    ) -> None:
        run_id = str(uuid.uuid4())
        for foreign in (None, str(uuid.uuid4())):
            token = self._open()
            self.registry.note_launch(run_id)  # the run's own start
            self.registry.note_launch(foreign)  # another launch before the bind
            self.registry.bind(token, run_id)
            self.assertEqual(self.registry.bindings_for([run_id]), {})

    def test_r4_b1_every_start_of_the_own_run_keeps_it_attributable(self) -> None:
        # One dayz_test_run starts the server, may replay it, then starts the
        # client with the same run_id; the daemon may only learn the run_id
        # from the start's answer.
        token = self._open()
        run_id = str(uuid.uuid4())
        self.registry.note_launch(run_id)
        self.registry.note_launch(run_id)
        ticket = self.registry.note_launch()
        self.registry.launch_started(ticket, run_id)
        self.assertTrue(self.registry.bind(token, run_id))
        self.assertIn(run_id, self.registry.bindings_for([run_id]))

    def test_r4_b1_a_start_whose_run_stays_unknown_is_foreign(self) -> None:
        token = self._open()
        run_id = str(uuid.uuid4())
        self.registry.note_launch(run_id)
        ticket = self.registry.note_launch()
        self.registry.launch_started(ticket, None)
        self.registry.bind(token, run_id)
        self.assertEqual(self.registry.bindings_for([run_id]), {})

    def test_r4_b1_a_bind_with_no_launch_since_the_open_is_unattributable(
        self,
    ) -> None:
        token = self._open()
        run_id = str(uuid.uuid4())
        self.registry.bind(token, run_id)
        self.assertEqual(self.registry.bindings_for([run_id]), {})

    def test_r4_b2_every_change_moves_the_revision(self) -> None:
        seen = [self.registry.bindings_with_revision([])[1]]
        token = self._open()
        seen.append(self.registry.bindings_with_revision([])[1])
        self.registry.note_launch()
        seen.append(self.registry.bindings_with_revision([])[1])
        self.registry.bind(token, str(uuid.uuid4()))
        seen.append(self.registry.bindings_with_revision([])[1])
        self.assertEqual(len(set(seen)), 4)
        self.assertEqual(
            self.registry.bindings_with_revision([])[1], seen[-1]
        )
        # A restarted daemon never repeats a revision of the previous one.
        self.assertNotEqual(
            ClientDumpRegistry().bindings_with_revision([])[1],
            ClientDumpRegistry().bindings_with_revision([])[1],
        )

    def test_an_unbound_open_is_never_listed(self) -> None:
        self._open()
        self.assertEqual(self.registry.bindings_for([None, "", 7]), {})
        self.assertEqual(self.registry.bindings_for("not-a-list"), {})

    def test_an_evicted_run_is_null(self) -> None:
        registry = ClientDumpRegistry(limit=2)
        first = str(uuid.uuid4())
        _launch(registry, [self.client], first)
        _launch(registry, [self.root / "a"], str(uuid.uuid4()))
        _launch(registry, [self.root / "b"], str(uuid.uuid4()))
        self.assertEqual(registry.bindings_for([first]), {})

    def test_a_run_id_bound_twice_is_not_attributable(self) -> None:
        run_id = str(uuid.uuid4())
        first, second = self._open(), self._open()
        self.assertTrue(self.registry.bind(first, run_id))
        self.assertTrue(self.registry.bind(second, run_id))
        self.assertEqual(self.registry.bindings_for([run_id]), {})
        self.assertFalse(self.registry.bind(second, str(uuid.uuid4())))
        self.assertFalse(self.registry.bind("unknown", run_id))

    def test_a_malformed_snapshot_records_and_caps_nothing(self) -> None:
        run_id = str(uuid.uuid4())
        _launch(self.registry, [self.client], run_id)
        for bad in (
            None,
            {},
            {"roots": [], "before": []},
            {"roots": ["x"], "before": []},
            {"roots": ["x"], "before": [[["notadump.txt", 1, 1, 1]]]},
            {"roots": ["x"], "before": [[["ErrorMessage_a.mdmp", True, 1, 1]]]},
            {"roots": [str(self.client)], "before": [None], "extra": 1},
        ):
            self.assertIsNone(self.registry.open(bad), bad)
        _dump(self.client, "03-04-46", _MARKED)
        self.assertEqual(self._diagnose(run_id), "steam_bootstrap")

    def test_wire_round_trip_and_oversized_root(self) -> None:
        _dump(self.client, "03-00-00", _UNMARKED)
        baseline = snapshot_client_dumps([self.client, self.root / "missing"])
        self.assertEqual(baseline_from_wire(baseline_to_wire(baseline)), baseline)
        many = frozenset(
            (f"ErrorMessage_{i}.mdmp", i, 1, 1)
            for i in range(MAX_WIRE_DUMPS_PER_ROOT + 1)
        )
        wire = baseline_to_wire(ClientDumpBaseline(roots=("r",), before=(many,)))
        self.assertEqual(wire, {"roots": ["r"], "before": [None]})


class _DaemonBackedRuntime(_Runtime):
    """The fake runtime plus the two daemon calls, on a real registry."""

    def __init__(self, registry: ClientDumpRegistry, lifecycle: dict) -> None:
        super().__init__(lifecycle)
        self.registry = registry

    async def client_dumps_open(self, baseline: dict) -> dict:
        token = self.registry.open(baseline)
        if token is None:
            raise RuntimeError("invalid_baseline")
        return {"token": token}

    async def client_dumps_bind(self, token: str, run_id: str) -> dict:
        return {"bound": self.registry.bind(token, run_id)}


class LateDeathEndToEndTest(unittest.IsolatedAsyncioTestCase):
    """dayz_test_run(mode=all) -> succeeded, client alive -> death -> reap.

    The reap and the dumps are synthetic: the retired row is built here and
    the dumps are written by the test, not by DayZ.
    """

    RUN_ID = "12345678-1234-4234-8234-1234567890ab"

    def setUp(self) -> None:
        self.registry = ClientDumpRegistry()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.client = self.root / "_client" / "profiles"
        self.server = self.root / "_server" / "profiles"
        self.client.mkdir(parents=True)
        self.server.mkdir(parents=True)
        patchers = [
            patch.object(
                dayz_test_tool,
                "evaluate_steam_session",
                return_value=steam_preflight.SteamSessionResult(
                    error_code=None,
                    steam_registered_pid=1,
                    steam_live_pids=(1,),
                    remediation=steam_preflight.REMEDIATION,
                ),
            ),
            patch.object(
                dayz_test_tool,
                "preflight_vpp_request",
                return_value=native_launcher_transaction.VppPreflightResult(
                    error_code=None,
                    missing=(),
                    warnings=(),
                    hint=native_launcher_transaction.VPP_PREFLIGHT_HINT,
                ),
            ),
            patch.object(
                dayz_test_tool,
                "evaluate_prerun_desktop",
                return_value=mcp_capture.PrerunDesktopResult(
                    error_code=None,
                    desktop="unlocked",
                    mean_brightness=80.0,
                    nonblack_ratio=0.9,
                    waited_s=0.01,
                    remediation="",
                ),
            ),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _run_all(
        self,
        *,
        client_alive: bool,
        write_during_launch: list[tuple[Path, bytes]],
        write_while_queued: list[tuple[Path, bytes]] = (),  # type: ignore[assignment]
        daemon_backed: bool = True,
    ) -> dict[str, object]:
        policy = _policy(dev_root=str(self.root))
        lifecycle = {
                "runs": [
                    {
                        "run_id": self.RUN_ID,
                        "processes": [
                            {"role": "server", "pid": 11},
                            {"role": "client", "pid": 22},
                        ],
                    }
                ]
            }
        runtime = (
            _DaemonBackedRuntime(self.registry, lifecycle)
            if daemon_backed
            else _Runtime(lifecycle)
        )

        async def launch(raw_request: bytes, **kwargs: object) -> int:
            parsed = dayz_test_request.parse_dayz_test_request(
                raw_request, policies=(policy,)
            )
            self.assertEqual(parsed.payload["mode"], "all")
            # Another client's run writes while this call waits in the queue.
            for index, (root, body) in enumerate(write_while_queued):
                _dump(root, f"03-03-{index:02d}", body)
            # secure_launcher: the lease is granted, the launch executes, and
            # the daemon's /lifecycle/start follows.
            await kwargs["execution_started_cb"]()  # type: ignore[operator]
            self.registry.note_launch(self.RUN_ID)
            for index, (root, body) in enumerate(write_during_launch):
                _dump(root, f"03-04-{index:02d}", body)
            kwargs["output_sink"](  # type: ignore[operator]
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": self.RUN_ID,
                    }
                ),
            )
            return 0

        def alive(pid: object) -> bool:
            return True if pid == 11 else client_alive

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
        ), patch.object(dayz_test_tool, "_pid_alive", side_effect=alive):
            return await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP"],
            )

    async def test_296b_success_then_death_then_reap_names_steam_bootstrap(self) -> None:
        # A marked dump from an earlier run is already in the client profile.
        _dump(self.client, "02-00-00", _MARKED)
        result = await self._run_all(client_alive=True, write_during_launch=[])
        self.assertEqual(result["status"], "succeeded")
        self.assertIs(result["client_alive"], True)
        self.assertIsNone(result["client_death_diagnosis"])
        # ~1 s later the client dies and writes its own marked dump; the
        # daemon reaps the run with all_processes_gone_or_foreign.
        _dump(self.client, "03-05-00", _MARKED)
        rows = self._retired_rows()
        self.assertEqual(rows[0]["run_id"], self.RUN_ID)
        self.assertEqual(rows[0]["client_death_diagnosis"], "steam_bootstrap")

    def _retired_rows(self) -> list[dict[str, object]]:
        rows = dayz_test_tool._runs_retired_recently(
            [_retired_row(self.RUN_ID)], self.registry.bindings_for([self.RUN_ID])
        )
        assert rows is not None
        return rows

    async def test_296b_late_death_without_a_daemon_record_is_null(self) -> None:
        result = await self._run_all(
            client_alive=True, write_during_launch=[], daemon_backed=False
        )
        self.assertEqual(result["status"], "succeeded")
        _dump(self.client, "03-05-00", _MARKED)
        self.assertIsNone(self._retired_rows()[0]["client_death_diagnosis"])

    async def test_a_dump_written_while_queued_is_in_the_baseline(self) -> None:
        # Taken when the launch leaves the queue: another run's marked dump
        # written during the wait cannot name this run's death.
        result = await self._run_all(
            client_alive=False,
            write_during_launch=[],
            write_while_queued=[(self.client, _MARKED)],
        )
        self.assertEqual(result["error_code"], "client_dead_after_ack")
        self.assertIsNone(result["client_death_diagnosis"])
        self.assertIsNone(self._retired_rows()[0]["client_death_diagnosis"])

    async def test_296b_late_death_without_new_marked_dump_is_not_named(self) -> None:
        _dump(self.client, "02-00-00", _MARKED)
        result = await self._run_all(client_alive=True, write_during_launch=[])
        self.assertEqual(result["status"], "succeeded")
        _dump(self.server, "03-05-00", _MARKED)
        _dump(self.client, "03-05-01", _UNMARKED)
        self.assertIsNone(self._retired_rows()[0]["client_death_diagnosis"])

    async def test_death_before_return_reads_only_the_new_client_dump(self) -> None:
        result = await self._run_all(
            client_alive=False,
            write_during_launch=[(self.client, _MARKED)],
        )
        self.assertEqual(result["error_code"], "client_dead_after_ack")
        self.assertEqual(result["client_death_diagnosis"], "steam_bootstrap")

    async def test_death_before_return_ignores_a_new_server_dump(self) -> None:
        result = await self._run_all(
            client_alive=False,
            write_during_launch=[(self.server, _MARKED), (self.client, _UNMARKED)],
        )
        self.assertEqual(result["error_code"], "client_dead_after_ack")
        self.assertIsNone(result["client_death_diagnosis"])


if __name__ == "__main__":
    unittest.main()
