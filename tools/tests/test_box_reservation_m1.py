"""The DayZ Studio (M1) reservation of the DayZ test box (2026-11-17).

M1 goes first in the box: while C:\\tmp\\caja-dayz\\m1.espera exists nobody
else launches. M1's harness chains several DayZ servers of its own, with gaps
of about 8 s in which no DayZ holds a game port. On 2026-11-17 two
dayz_test_run calls queued with on_busy="queue" launched in those gaps, and M1
lost its 7th and 8th S-persist runs.

Each test writes the reservation file in a temporary folder and points the
daemon's reader at it (tests/__init__.py gives every other test a reader that
finds none). The box the dayz_test_run tests read is the daemon's own
/session/status composition (loopback._box_payload) over a box with no DayZ in
it, the gap between two of M1's servers, and a real box FIFO.
"""

from __future__ import annotations

import asyncio
import functools
import os
import tempfile
import time
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dayz_mcp import box_occupancy, box_reservation, loopback, server
from dayz_mcp.server import ServerConfig
from dayz_mcp.session_coordination import SessionCoordinator
from tests._tiers import slow_test
from tests.client_helpers import _fixture_client_runtime
from tests.dayz_test_tool_helpers import RUN_ID
from tests.lifecycle_helpers import IDENTITY, LifecycleFixture, identity, record
from tests.mcp_helpers import _content_json

_REAL_EXECUTE_WAIT_FOR_BOX = server.execute_wait_for_box
_AGE_S = 754.0
# A run another session launched and still drives.
_OTHER_RUN = {
    "run_id": "db23a1df-a49c-4407-a4e4-eb62b895b342",
    "mod": "@BattleRoyale",
    "label": "@BattleRoyale server",
    "age_s": 40.0,
    "owner_session": "a4317999-9c5",
    "state": "RUNNING",
    "activity_state": "active",
    "last_activity_age_s": 0.0,
}
# One of M1's servers: its harness launches it, so it is foreign to the daemon.
_M1_SERVER = {"pid": 4242, "port": 2302, "mods": ["@DayZSandbox_QA"], "age_s": 2.0}


def _write_flag(path: Path, *, age_s: float = _AGE_S) -> None:
    """The file as M1's watcher leaves it, last written age_s seconds ago."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "M1 (F0.3, S-persist) espera la caja de DayZ\n", encoding="utf-8"
    )
    written = time.time() - age_s
    os.utime(path, (written, written))


def _flag_folder(test: unittest.TestCase) -> Path:
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    return Path(folder.name) / "caja-dayz" / "m1.espera"


def _read_flag_at(test: unittest.TestCase, flag: Path) -> None:
    """The daemon reads the reservation from ``flag`` for this test."""
    patcher = patch.object(
        box_reservation,
        "read_box_reservation",
        functools.partial(box_reservation.reservation_at, str(flag)),
    )
    patcher.start()
    test.addCleanup(patcher.stop)


# --- the predicate ----------------------------------------------------------


class ReservationPredicateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.flag = _flag_folder(self)

    def test_the_file_is_m1s(self) -> None:
        self.assertEqual(
            box_reservation.BOX_RESERVATION_PATH, r"C:\tmp\caja-dayz\m1.espera"
        )

    def test_no_file_is_no_reservation(self) -> None:
        self.flag.parent.mkdir(parents=True)
        self.assertIsNone(box_reservation.reservation_at(str(self.flag)))

    def test_no_folder_is_no_reservation(self) -> None:
        self.assertIsNone(box_reservation.reservation_at(str(self.flag)))

    def test_the_file_reserves_the_box_and_says_how_old_it_is(self) -> None:
        _write_flag(self.flag)
        now = self.flag.stat().st_mtime + _AGE_S
        self.assertEqual(
            box_reservation.reservation_at(str(self.flag), now=now),
            {"path": str(self.flag), "age_s": _AGE_S},
        )

    def test_an_empty_file_reserves_it_too(self) -> None:
        self.flag.parent.mkdir(parents=True)
        self.flag.write_bytes(b"")
        reservation = box_reservation.reservation_at(str(self.flag))
        self.assertIsNotNone(reservation)
        self.assertEqual(reservation["path"], str(self.flag))

    def test_it_never_expires(self) -> None:
        _write_flag(self.flag, age_s=30 * 24 * 3600.0)
        reservation = box_reservation.reservation_at(str(self.flag))
        self.assertIsNotNone(reservation)
        self.assertGreater(reservation["age_s"], 29 * 24 * 3600.0)

    def test_a_file_written_in_the_future_is_zero_seconds_old(self) -> None:
        _write_flag(self.flag, age_s=-3600.0)
        reservation = box_reservation.reservation_at(str(self.flag))
        self.assertEqual(reservation, {"path": str(self.flag), "age_s": 0.0})

    def test_the_entry_itself_counts_not_what_it_points_to(self) -> None:
        # A reader that followed links would see no file here and free the box.
        _write_flag(self.flag)
        gone = FileNotFoundError(2, "The system cannot find the file specified")
        with patch.object(box_reservation.os, "stat", side_effect=gone):
            reservation = box_reservation.reservation_at(str(self.flag))
        self.assertIsNotNone(reservation)
        self.assertEqual(reservation["path"], str(self.flag))

    def test_a_link_whose_target_is_gone_is_a_reservation(self) -> None:
        self.flag.parent.mkdir(parents=True)
        try:
            os.symlink(str(self.flag.parent / "removed-target"), str(self.flag))
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"this host cannot create a symbolic link: {error}")
        self.assertFalse(self.flag.exists())
        reservation = box_reservation.reservation_at(str(self.flag))
        self.assertIsNotNone(reservation)
        self.assertEqual(reservation["path"], str(self.flag))

    def test_a_path_that_cannot_be_examined_is_a_reservation(self) -> None:
        denied = PermissionError(13, "Access is denied")
        with patch.object(box_reservation.os, "lstat", side_effect=denied):
            reservation = box_reservation.reservation_at(str(self.flag))
        self.assertEqual(reservation, {"path": str(self.flag), "age_s": None})

    def test_a_reader_that_fails_is_a_reservation(self) -> None:
        with patch.object(
            box_reservation, "read_box_reservation", side_effect=RuntimeError("x")
        ):
            reservation = box_reservation.current_reservation()
        self.assertEqual(
            reservation,
            {"path": box_reservation.BOX_RESERVATION_PATH, "age_s": None},
        )

    def test_the_hint_names_the_file_its_age_and_who_removes_it(self) -> None:
        hint = box_reservation.reservation_hint(
            {"path": str(self.flag), "age_s": 754.4}
        )
        self.assertIn(str(self.flag), hint)
        self.assertIn("last written 754 s ago", hint)
        self.assertIn("a person removes it by hand, not an agent", hint)
        unknown = box_reservation.reservation_hint(
            {"path": str(self.flag), "age_s": None}
        )
        self.assertIn("its age could not be read", unknown)


# --- the box the daemon serves ------------------------------------------------


def _gap_occupancy() -> dict[str, object]:
    """The lifecycle's box between two of M1's servers: no DayZ in it."""
    return {
        "occupied": False,
        "runs": [],
        "foreign": [],
        "ports_in_use": [],
        "scan_known": True,
        "port_scan_known": True,
        "foreign_ports": [],
    }


class StatusBoxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.flag = _flag_folder(self)
        _read_flag_at(self, self.flag)
        self.state = types.SimpleNamespace(
            lifecycle=types.SimpleNamespace(box_occupancy=_gap_occupancy),
            coordination=None,
        )

    def test_the_gap_reads_occupied_while_the_file_exists(self) -> None:
        _write_flag(self.flag)

        box = loopback._box_payload(self.state)

        self.assertIs(box["occupied"], True)
        self.assertEqual(box["reservation"]["path"], str(self.flag))
        self.assertAlmostEqual(box["reservation"]["age_s"], _AGE_S, delta=60.0)
        self.assertEqual((box["runs"], box["foreign"]), ([], []))

    def test_without_the_file_the_gap_reads_free(self) -> None:
        box = loopback._box_payload(self.state)

        self.assertIs(box["occupied"], False)
        self.assertNotIn("reservation", box)


# --- the lifecycle's admission and its last look before the spawn ------------


class LifecycleStartTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = LifecycleFixture()
        self.addCleanup(self.fixture.close)
        self.flag = _flag_folder(self)
        _read_flag_at(self, self.flag)
        # The launch registers when nothing refuses it.
        launched = record(self.fixture.launcher.handle.pid)
        self.fixture.guard.snapshots[launched.pid] = identity(launched)

    def _start(self) -> dict[str, object]:
        return self.fixture.lifecycle.start_run(
            IDENTITY, self.fixture.token, self.fixture.request()
        )

    def _rejections(self) -> list[tuple[object, object]]:
        return [
            (event.get("reason"), event.get("stage"))
            for event in self.fixture.audit.events
            if event.get("event") == "lifecycle_start_rejected"
        ]

    def test_without_the_file_the_start_launches(self) -> None:
        result = self._start()

        self.assertEqual(result.get("state"), "RUNNING", result)
        self.assertEqual(len(self.fixture.launcher.calls), 1)
        self.assertEqual(self._rejections(), [])

    def test_admission_refuses_a_start_while_the_file_exists(self) -> None:
        _write_flag(self.flag)

        result = self._start()

        self.assertEqual(result.get("error"), "active_run_exists", result)
        self.assertEqual(self.fixture.launcher.calls, [])
        self.assertEqual(self._rejections(), [("box_reserved", None)])
        self.assertEqual(self.fixture.lifecycle.box_occupancy()["runs"], [])

    def test_the_last_look_before_the_spawn_reads_the_file_again(self) -> None:
        # M1 writes the file after the admission: audit, reserve and the
        # instance preparation sit between it and the last look.
        original_prepare = self.fixture.lifecycle._prepare_instance

        def prepare_then_reserve(*args: object, **kwargs: object) -> object:
            _write_flag(self.flag, age_s=0.0)
            return original_prepare(*args, **kwargs)

        self.fixture.lifecycle._prepare_instance = prepare_then_reserve

        result = self._start()

        self.assertEqual(result.get("error"), "active_run_exists", result)
        self.assertEqual(self.fixture.launcher.calls, [])
        self.assertEqual(self._rejections(), [("box_reserved", "pre_launch")])
        self.assertEqual(self.fixture.lifecycle.box_occupancy()["runs"], [])

    def test_the_file_is_read_once_more_right_before_the_spawn(self) -> None:
        # M1 writes the file after the last socket-table read: the Steam check
        # this client launch repeats after the storage rotation and before the
        # launch intent is the last step before the spawn's own look.
        lifecycle = self.fixture.lifecycle
        gate = lifecycle.steam_gate
        original_check = gate.final_check

        def check_then_reserve(prepared: object) -> bool:
            passed = original_check(prepared)
            if gate.checks == 2:
                _write_flag(self.flag, age_s=0.0)
            return passed

        gate.final_check = check_then_reserve

        result = self._start()

        self.assertEqual(gate.checks, 2)
        self.assertEqual(result.get("error"), "active_run_exists", result)
        self.assertEqual(self.fixture.launcher.calls, [])
        self.assertEqual(self._rejections(), [("box_reserved", "spawn")])
        self.assertEqual(lifecycle.box_occupancy()["runs"], [])
        intent = lifecycle._launch_intent_path()
        self.assertIsNotNone(intent)
        self.assertFalse(intent.exists(), "the refused launch left its intent")


# --- dayz_test_run ------------------------------------------------------------


def _refused() -> dict[str, object]:
    """dayz_test_tool's envelope for a launch the daemon refused."""
    return {
        "status": "failed",
        "project": "ExampleMod",
        "mode": "all",
        "run_id": None,
        "phase": "executing",
        "elapsed_s": 3.0,
        "artifacts_paths": [],
        "error_code": "active_run_exists",
        "cleanup_degraded": False,
        "server_alive": None,
        "client_alive": None,
        "reason": None,
    }


def _launched() -> dict[str, object]:
    return {
        "status": "succeeded",
        "project": "ExampleMod",
        "mode": "all",
        "run_id": RUN_ID,
        "phase": "completed",
        "elapsed_s": 30.0,
        "artifacts_paths": [],
        "error_code": None,
        "cleanup_degraded": False,
    }


async def _launch_ok(_attempt: int) -> dict[str, object]:
    return _launched()


class _DaemonBox:
    """The daemon's /session/status box over a real box FIFO.

    loopback._box_payload composes it as the daemon does. The lifecycle's
    occupancy is the gap between two of M1's servers unless a test puts a
    managed run in the box.
    """

    def __init__(self) -> None:
        self.seq = 0
        self.runs: list[dict[str, object]] = []
        # DayZ this daemon does not manage, such as M1's own servers.
        self.foreign: list[dict[str, object]] = []
        self.joins = 0
        self.busy_answers = 0
        self.coordinator = SessionCoordinator(
            time_fn=lambda: 0.0,
            id_fn=self._next_id,
            token_fn=lambda: "unused",
        )
        self.state = types.SimpleNamespace(
            lifecycle=self, coordination=self.coordinator
        )

    def _next_id(self) -> str:
        self.seq += 1
        return f"ticket-{self.seq}"

    def box_occupancy(self) -> dict[str, object]:
        occupancy = _gap_occupancy()
        if self.runs:
            occupancy.update(
                occupied=True,
                runs=[dict(run) for run in self.runs],
                ports_in_use=[2302],
            )
        if self.foreign:
            occupancy.update(
                occupied=True,
                foreign=[dict(item) for item in self.foreign],
                ports_in_use=[2302],
            )
        return occupancy

    def snapshot(self) -> dict[str, object]:
        return {"box": loopback._box_payload(self.state)}

    def bind(self, runtime: object) -> None:
        caller = runtime.identity  # type: ignore[attr-defined]

        async def session_status() -> dict[str, object]:
            await asyncio.sleep(0)
            return self.snapshot()

        async def session_box_status(**kwargs: object) -> dict[str, object]:
            await asyncio.sleep(0)
            if kwargs.get("wait") and not kwargs.get("ticket") and not kwargs.get("done"):
                self.joins += 1
            touched = self.coordinator.box_wait_touch(
                caller,
                kwargs.get("ticket"),
                done=bool(kwargs.get("done")),
                claim=bool(kwargs.get("claim")),
            )
            snap = self.snapshot()
            polled = kwargs.get("wait") and not (kwargs.get("done") or kwargs.get("claim"))
            if polled and snap["box"]["occupied"]:
                self.busy_answers += 1
            snap["box_ticket"] = touched.get("box_ticket")
            if "box_claimed" in touched:
                snap["box_claimed"] = touched["box_claimed"]
            if touched.get("box_wait_error"):
                snap["box_wait_error"] = touched["box_wait_error"]
            return snap

        runtime.session_status = session_status  # type: ignore[attr-defined]
        runtime.session_box_status = session_box_status  # type: ignore[attr-defined]


@contextmanager
def _fast_box_wait():
    async def wrapped(client, wait_s, **kwargs):
        kwargs.setdefault("poll_interval_s", 0.05)
        return await _REAL_EXECUTE_WAIT_FOR_BOX(client, wait_s, **kwargs)

    with patch.object(server, "execute_wait_for_box", wrapped):
        yield


async def _until(predicate, *, timeout_s: float = 3.0, message: str = "condition") -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(message)


class DayzTestRunReservationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        self.runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            self.app, _built = server.build_app(config)
        self.box = _DaemonBox()
        self.box.bind(self.runtime)
        self.launches: list[dict[str, object]] = []
        # Whether the reservation file existed when each launch was sent.
        self.reserved_at_launch: list[bool] = []
        self.flag = _flag_folder(self)
        _read_flag_at(self, self.flag)

    async def _call(self, execute, **arguments: object) -> dict[str, object]:
        async def recording(_client, **kwargs: object) -> dict[str, object]:
            self.launches.append(
                {key: kwargs[key] for key in ("build", "clean", "pack_only")}
            )
            self.reserved_at_launch.append(self.flag.exists())
            return await execute(len(self.launches))

        with patch.object(
            server.dayz_test_tool, "execute_dayz_test_run", side_effect=recording
        ), _fast_box_wait():
            return _content_json(
                await self.app.call_tool(
                    "dayz_test_run",
                    {"project": "ExampleMod", "mode": "all", **arguments},
                )
            )

    def _fifo_empty(self) -> bool:
        return self.box.coordinator.box_queue_public() == []

    async def _end_reservation_once_waiting(self, joins: int) -> None:
        """M1 ends once this call has joined the FIFO ``joins`` times with the
        file there and waited on the reserved box: its server goes, then its file."""
        await _until(
            lambda: self.box.joins >= joins and self.flag.exists(),
            message=f"the call did not join the FIFO {joins} times with the file there",
        )
        seen = self.box.busy_answers
        await _until(
            lambda: self.box.busy_answers > seen,
            message="the call did not wait on the reserved box",
        )
        self.box.foreign = []
        self.flag.unlink()

    async def test_without_the_file_the_call_launches(self) -> None:
        payload = await self._call(_launch_ok)

        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(self.reserved_at_launch, [False])
        self.assertNotIn("reservation", payload)

    async def test_an_immediate_call_is_refused_naming_the_file_and_its_age(
        self,
    ) -> None:
        _write_flag(self.flag)

        payload = await self._call(_launch_ok, build=True)

        # No worker: no build and no launch.
        self.assertEqual(self.launches, [])
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error_code"], "active_run_exists")
        self.assertIsNone(payload["run_id"])
        self.assertEqual(payload["reason"], "box_reserved")
        reservation = payload["reservation"]
        self.assertEqual(reservation["path"], str(self.flag))
        self.assertAlmostEqual(reservation["age_s"], _AGE_S, delta=60.0)
        self.assertIn(str(self.flag), payload["hint"])
        self.assertIn(
            f"last written {reservation['age_s']:.0f} s ago", payload["hint"]
        )
        self.assertIn("a person removes it by hand", payload["hint"])
        self.assertEqual(
            payload["queue_offer"]["retry"],
            {"tool": "dayz_test_run", "same_args": True, "on_busy": "queue"},
        )
        self.assertTrue(self._fifo_empty())

    @slow_test
    async def test_a_queued_call_waits_out_the_gap_while_the_file_exists(
        self,
    ) -> None:
        _write_flag(self.flag)

        async def remove_once_it_waited() -> None:
            await _until(
                lambda: self.box.busy_answers >= 3,
                message="the queued call did not wait on the reserved box",
            )
            self.assertEqual(self.launches, [])
            self.flag.unlink()

        payload, _removed = await asyncio.gather(
            self._call(_launch_ok, on_busy="queue", wait_for_box_s=5.0),
            remove_once_it_waited(),
        )

        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(self.reserved_at_launch, [False])
        self.assertNotIn("box_requeued", payload)
        self.assertTrue(self._fifo_empty())

    @slow_test
    async def test_a_file_that_appears_before_the_launch_sends_a_queued_call_back_to_wait(
        self,
    ) -> None:
        async def execute(attempt: int) -> dict[str, object]:
            if attempt == 1:
                # M1 writes the file after this call's FIFO grant: the
                # daemon's last look before the spawn refuses the launch.
                _write_flag(self.flag, age_s=0.0)
                return _refused()
            return _launched()

        async def remove_once_it_waits_again() -> None:
            await _until(
                lambda: self.box.joins >= 2 and self.flag.exists(),
                message="the refused call did not join the FIFO again",
            )
            seen = self.box.busy_answers
            await _until(
                lambda: self.box.busy_answers > seen,
                message="the re-queued call did not wait on the reserved box",
            )
            self.flag.unlink()

        payload, _removed = await asyncio.gather(
            self._call(execute, build=True, on_busy="queue", wait_for_box_s=5.0),
            remove_once_it_waits_again(),
        )

        self.assertEqual(
            self.launches,
            [
                {"build": True, "clean": False, "pack_only": False},
                {"build": False, "clean": False, "pack_only": False},
            ],
        )
        self.assertEqual(self.reserved_at_launch, [False, False])
        self.assertEqual(payload["status"], "succeeded")
        self.assertIs(payload["box_requeued"], True)
        self.assertTrue(self._fifo_empty())

    @slow_test
    async def test_a_reserved_refusal_sends_the_call_back_whatever_the_box_read_before_it(
        self,
    ) -> None:
        # M1 writes its file and starts its first server between this call's
        # FIFO grant and the box read right before the launch, so that read
        # is no longer free, and the daemon refuses the launch.
        status = self.runtime.session_status
        reads = 0

        async def status_as_m1_starts() -> dict[str, object]:
            nonlocal reads
            reads += 1
            if reads == 2:
                _write_flag(self.flag, age_s=0.0)
                self.box.foreign = [dict(_M1_SERVER)]
            return await status()

        self.runtime.session_status = status_as_m1_starts  # type: ignore[method-assign]

        async def execute(attempt: int) -> dict[str, object]:
            return _refused() if attempt == 1 else _launched()

        payload, _ended = await asyncio.gather(
            self._call(execute, build=True, on_busy="queue", wait_for_box_s=5.0),
            self._end_reservation_once_waiting(2),
        )

        self.assertEqual(
            self.launches,
            [
                {"build": True, "clean": False, "pack_only": False},
                {"build": False, "clean": False, "pack_only": False},
            ],
        )
        # The first attempt reached the worker with the file there, and the
        # daemon refused it; the second ran after M1 ended.
        self.assertEqual(self.reserved_at_launch, [True, False])
        self.assertEqual(payload["status"], "succeeded")
        self.assertIs(payload["box_requeued"], True)
        self.assertTrue(self._fifo_empty())

    @slow_test
    async def test_each_reserved_refusal_sends_a_queued_call_back_to_wait(
        self,
    ) -> None:
        async def execute(attempt: int) -> dict[str, object]:
            if attempt < 3:
                # M1 writes its file again after each of this call's grants.
                _write_flag(self.flag, age_s=0.0)
                return _refused()
            return _launched()

        async def end_each_reservation() -> None:
            await self._end_reservation_once_waiting(2)
            await self._end_reservation_once_waiting(3)

        payload, _ended = await asyncio.gather(
            self._call(execute, on_busy="queue", wait_for_box_s=10.0),
            end_each_reservation(),
        )

        self.assertEqual(len(self.launches), 3)
        self.assertEqual(
            self.launches[1:],
            [{"build": False, "clean": False, "pack_only": False}] * 2,
        )
        self.assertEqual(self.reserved_at_launch, [False, False, False])
        self.assertEqual(payload["status"], "succeeded")
        self.assertIs(payload["box_requeued"], True)
        self.assertTrue(self._fifo_empty())

    async def test_a_reserved_box_offers_no_takeover(self) -> None:
        self.box.runs = [dict(_OTHER_RUN)]
        _write_flag(self.flag)

        payload = await self._call(_launch_ok)

        self.assertEqual(self.launches, [])
        self.assertEqual(payload["error_code"], "active_run_exists")
        self.assertEqual(payload["reason"], "box_reserved")
        self.assertEqual(payload["occupied_by_run_id"], _OTHER_RUN["run_id"])

    async def test_takeover_evicts_nothing_while_the_file_exists(self) -> None:
        self.box.runs = [dict(_OTHER_RUN)]
        _write_flag(self.flag)
        stop = AsyncMock(
            return_value={"status": "succeeded", "run_id": _OTHER_RUN["run_id"]}
        )

        with patch.object(server.dayz_test_tool, "execute_dayz_test_stop", stop):
            payload = await self._call(_launch_ok, takeover=True)

        stop.assert_not_called()
        self.assertEqual(self.launches, [])
        self.assertEqual(payload["error_code"], "active_run_exists")
        self.assertEqual(payload["reason"], "box_reserved")
        self.assertNotIn("evicted_run_id", payload)


class BoxHeldByAnotherTest(unittest.TestCase):
    _CALLER = "c0ffee00-1111-4222-8333-444455556666"

    def test_the_reservation_holds_the_box_for_someone_else(self) -> None:
        # Only the caller's own claim besides the file: the re-queue reads
        # the file as the box taken while the call waited for its launch.
        box = {
            "occupied": True,
            "runs": [],
            "foreign": [],
            "queue": [{"session": self._CALLER[:12], "waiting_s": 0.0}],
            "claimed_s": 0.0,
            "reservation": {"path": r"C:\tmp\caja-dayz\m1.espera", "age_s": 9.0},
        }
        self.assertTrue(box_occupancy._box_was_free_for(box, self._CALLER))
        self.assertTrue(box_occupancy._box_held_by_another(box, self._CALLER))
        del box["reservation"]
        self.assertFalse(box_occupancy._box_held_by_another(box, self._CALLER))


if __name__ == "__main__":
    unittest.main()
