"""Fix-forward tests for #479 post-merge findings (Sol RED, P1-1..P1-6, P2, P3)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from dayz_mcp import shared_box_queue as queue
from tests.box_queue_helpers import (
    BoxQueueFixture,
    box_coordinator as _coordinator,
    box_entry as _entry,
    box_identity as _identity,
)


def _bad_kind_document(entry: dict | None = None) -> dict:
    row = dict(entry or _entry("t1", "claimed"))
    row["kind"] = []
    return {"version": 1, "entries": [row]}


class EntrySaneAndUnreadableTest(BoxQueueFixture, unittest.TestCase):
    """P1-6: a typed-wrong field must not raise; stop must still be authorizable."""

    def _write_bad(self, document: dict) -> Path:
        path = self.root / "box-queue.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_p1_6_kind_list_is_unreadable_not_typeerror(self) -> None:
        path = self._write_bad(_bad_kind_document())
        before = path.read_bytes()
        parsed = queue.parse_document(path.read_text(encoding="utf-8"))
        self.assertIsNone(parsed)
        view = queue.read_view(now=1.0, session_id="caller", root=self.root)
        self.assertTrue(view.get("unreadable"))
        self.assertEqual(path.read_bytes(), before)

    def test_p1_6_status_and_lifecycle_stop_survive_a_bad_kind(self) -> None:
        path = self._write_bad(_bad_kind_document())
        before = path.read_bytes()
        coordinator = _coordinator([1.0])
        owner = _identity("sane-stop")
        try:
            coordinator.status(owner)
        except TypeError as error:
            self.fail(f"status raised TypeError: {error}")
        decision = coordinator.authorize(owner, None, "lifecycle_stop")
        self.assertIsNotNone(decision)
        self.assertEqual(path.read_bytes(), before)

    def test_p1_6_a_list_state_is_also_unreadable(self) -> None:
        row = _entry("t1", "claimed")
        row["state"] = []
        path = self._write_bad({"version": 1, "entries": [row]})
        before = path.read_bytes()
        self.assertIsNone(queue.parse_document(path.read_text(encoding="utf-8")))
        view = queue.read_view(now=1.0, root=self.root)
        self.assertTrue(view.get("unreadable"))
        self.assertEqual(path.read_bytes(), before)


class BoundTicketTest(BoxQueueFixture, unittest.TestCase):
    """P1-1: a start without a ticket must not consume another claim in the session."""

    def test_p1_1_start_without_a_ticket_does_not_consume_the_sessions_claim(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        session = _identity("same-session")
        first = coordinator.box_wait_touch(session, claim=True)
        self.assertTrue(first.get("box_claimed"))
        _, lease = coordinator.acquire(session, "run")
        started = lifecycle.start_run(
            session, lease["lease_token"], self._request(game)
        )
        self.assertFalse(started.get("ok"), started)
        self.assertEqual(len(launcher.calls), 0)
        self.assertTrue(coordinator.box_is_claimed())
        owned = lifecycle.start_run(
            session,
            lease["lease_token"],
            self._request(game, box_ticket=first["box_ticket"]),
        )
        self.assertTrue(owned.get("ok"), owned)
        self.assertEqual(len(launcher.calls), 1)


class BindClockTest(BoxQueueFixture, unittest.TestCase):
    """P1-2: TTL is judged with a clock taken after the queue lock is held."""

    def test_p1_2_a_claim_that_expires_while_waiting_for_the_lock_is_refused(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        owner = _identity("ttl-owner")
        claimed = coordinator.box_wait_touch(owner, claim=True)
        ticket = claimed["box_ticket"]
        _, lease = coordinator.acquire(owner, "run")
        now[0] = queue.CLAIM_TTL_S - 1.0
        lock = queue._Lock(self.root, 5.0)
        lock.__enter__()
        started = threading.Event()
        outcome: list[object] = []

        def bind() -> None:
            started.set()
            outcome.append(
                coordinator.box_bind_run(owner, ticket, "run-late")
            )

        worker = threading.Thread(target=bind)
        worker.start()
        self.assertTrue(started.wait(timeout=2.0))
        time.sleep(0.2)
        now[0] = queue.CLAIM_TTL_S + 1.0
        lock.__exit__(None, None, None)
        worker.join(timeout=8.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsNotNone(outcome[0])
        started_run = lifecycle.start_run(
            owner,
            lease["lease_token"],
            self._request(game, box_ticket=ticket),
        )
        self.assertFalse(started_run.get("ok"), started_run)
        self.assertEqual(len(launcher.calls), 0)


class RotationBeforeBindTest(BoxQueueFixture, unittest.TestCase):
    """P1-3: a FIFO rejection after admission must not leave storage on the backup."""

    def _server_request(self, game: Path, mission: Path, seal: str, **extra: object) -> dict:
        body = {
            "argv": [str(game / "DayZDiag_x64.exe"), "-server", "-mission=test"],
            "cwd": str(game),
            "role": "server",
            "window_style": "normal",
            "label": "l",
            "mod": "@M",
            "profiles": "p",
            "mission": str(mission),
            "storage_seal": seal,
        }
        body.update(extra)
        return body

    def test_p1_3_fifo_rejection_does_not_leave_storage_rotated(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        mission = self.root / "mpmissions" / "dayzOffline"
        storage = mission / "storage_1"
        (storage / "players").mkdir(parents=True)
        (storage / "data.bin").write_bytes(b"world-and-characters")
        (storage / "players" / "p1.bin").write_bytes(b"survivor")
        seal = "a" * 64
        owner = _identity("server-a")
        waiter = _identity("server-b")
        _, lease = coordinator.acquire(owner, "run")
        admitted = {"ok": False}
        enqueued = {"done": False}
        real_admit = coordinator.box_admit_start
        real_rotate = lifecycle._rotate_storage_for_launch
        real_bind = coordinator.box_bind_run

        def enqueue_once() -> None:
            if admitted["ok"] and not enqueued["done"]:
                enqueued["done"] = True
                coordinator.box_wait_touch(waiter)

        def admit_wrap(*args, **kwargs):
            reason = real_admit(*args, **kwargs)
            admitted["ok"] = reason is None
            return reason

        def rotate_wrap(*args, **kwargs):
            enqueue_once()
            return real_rotate(*args, **kwargs)

        def bind_wrap(*args, **kwargs):
            enqueue_once()
            return real_bind(*args, **kwargs)

        with patch.object(coordinator, "box_admit_start", admit_wrap), patch.object(
            lifecycle, "_rotate_storage_for_launch", rotate_wrap
        ), patch.object(coordinator, "box_bind_run", bind_wrap):
            started = lifecycle.start_run(
                owner,
                lease["lease_token"],
                self._server_request(game, mission, seal),
            )
        self.assertFalse(started.get("ok"), started)
        self.assertEqual(len(launcher.calls), 0)
        self.assertTrue(admitted["ok"])
        self.assertTrue(enqueued["done"])
        self.assertTrue(
            storage.is_dir(),
            "storage_1 must still be in place after a FIFO rejection",
        )
        backups = [
            path
            for path in mission.iterdir()
            if path.name.startswith("storage_1.modset-") and path.is_dir()
        ]
        self.assertEqual(backups, [])


class M1WindowTest(BoxQueueFixture, unittest.TestCase):
    """P1-4: the last m1.espera read is after bind, immediately before the launcher."""

    def test_p1_4_reservation_appearing_during_bind_blocks_the_spawn(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        owner = _identity("m1-owner")
        claimed = coordinator.box_wait_touch(owner, claim=True)
        _, lease = coordinator.acquire(owner, "run")
        real_bind = coordinator.box_bind_run
        reserved: dict[str, object] = {}

        def bind_wrap(*args, **kwargs):
            reserved["row"] = {"path": "m1.espera", "age_s": 0.0}
            return real_bind(*args, **kwargs)

        def current() -> dict | None:
            row = reserved.get("row")
            return row if isinstance(row, dict) else None

        with patch.object(coordinator, "box_bind_run", bind_wrap), patch(
            "dayz_mcp.box_reservation.current_reservation", current
        ):
            started = lifecycle.start_run(
                owner,
                lease["lease_token"],
                self._request(game, box_ticket=claimed["box_ticket"]),
            )
        self.assertFalse(started.get("ok"), started)
        self.assertEqual(len(launcher.calls), 0)


class TakeoverLosesTheTurnTest(unittest.IsolatedAsyncioTestCase):
    """P1-5: takeover must reserve the turn before it stops the live run."""

    async def test_p1_5_a_waiter_that_arrives_during_status_blocks_stop(self) -> None:
        from unittest.mock import AsyncMock, patch

        from dayz_mcp import server as server_module
        from dayz_mcp.server import ServerConfig
        from tests.client_helpers import _fixture_client_runtime
        from tests.mcp_helpers import _content_json

        def _config() -> ServerConfig:
            return ServerConfig(
                mode="client",
                key="k",
                port=12345,
                client_platform="codex",
                log_sink=lambda _message: None,
            )

        def _foreign_box(*, state: str, owner: str | None) -> dict[str, object]:
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

        stale = _foreign_box(state="RUNNING", owner="other")
        stale["queue"] = []
        live = _foreign_box(state="RUNNING", owner="other")
        live["queue"] = [
            {"holder": "waiter-b", "session_id": "sess-b", "position": 1}
        ]
        runtime = _fixture_client_runtime(_config())
        calls = {"status": 0}

        async def status() -> dict:
            # First snapshot is stale: it reports an empty queue even though
            # B has already enqueued by the time the answer returns.
            calls["status"] += 1
            payload = stale if calls["status"] == 1 else live
            return {"box": payload, "self": {"state": "none"}}

        stop = AsyncMock(
            return_value={
                "status": "succeeded",
                "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "error_code": None,
            }
        )
        execute = AsyncMock(side_effect=AssertionError("must not launch"))
        with patch.object(server_module, "ClientRuntime", return_value=runtime):
            app, _built = server_module.build_app(_config())
        with (
            patch.object(runtime, "session_status", new=status),
            patch.object(server_module.dayz_test_tool, "execute_dayz_test_run", new=execute),
            patch.object(server_module.dayz_test_tool, "execute_dayz_test_stop", new=stop),
        ):
            payload = _content_json(
                await app.call_tool(
                    "dayz_test_run",
                    {"project": "ExampleMod", "mode": "server", "takeover": True},
                )
            )
        stop.assert_not_awaited()
        execute.assert_not_awaited()
        self.assertEqual(payload.get("status"), "failed")


class PosixLockStabilityTest(BoxQueueFixture, unittest.TestCase):
    """P2 (cheap): unlinking a held POSIX lock must not drop mutual exclusion."""

    @unittest.skipUnless(sys.platform != "win32", "POSIX lock replacement")
    def test_p2_unlink_of_a_held_lock_does_not_admit_a_second_writer(self) -> None:
        root = self.root
        first = queue._Lock(root, 5.0)
        first.__enter__()
        try:
            lock_path = root / "box-queue.lock"
            # A second opener must not get the lock even if the directory
            # entry is removed while the first holder is inside.
            try:
                lock_path.unlink()
            except OSError:
                first.__exit__(None, None, None)
                self.skipTest("unlink of a held lock is refused on this host")
                return
            second = queue._Lock(root, 0.2)
            with self.assertRaises((TimeoutError, OSError)):
                second.__enter__()
                second.__exit__(None, None, None)
        finally:
            first.__exit__(None, None, None)


class InstallGuideReleaseWordingTest(unittest.TestCase):
    """P3 from #482: guides must not say the wheel is unpublished."""

    _GUIDES = ("llms-install.md", "README.md", "QUICKSTART.md")

    def test_p3_guides_do_not_say_the_release_has_not_published_the_wheel(self) -> None:
        repo = _TOOLS.parent
        forbidden = (
            "until a GitHub release publishes",
            "is not published yet",
            "are not in a published release",
            "not published yet",
        )
        for name in self._GUIDES:
            text = (repo / name).read_text(encoding="utf-8")
            lowered = text
            for phrase in forbidden:
                with self.subTest(doc=name, phrase=phrase):
                    self.assertNotIn(
                        phrase,
                        lowered,
                        f"{name} still says {phrase!r}",
                    )
            with self.subTest(doc=name, phrase="clone path still used"):
                self.assertTrue(
                    "clone" in text.casefold(),
                    f"{name} must still document the clone install path",
                )


if __name__ == "__main__":
    unittest.main()
