"""Phase 3A: one shared box FIFO."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from dayz_mcp import shared_box_queue as queue
from dayz_mcp.box_holder import public_holder
from dayz_mcp.session_coordination import (
    BOX_CLAIM_TTL_S,
    ClientIdentity,
    SessionCoordinator,
)


def _entry(ticket: str, state: str) -> dict:
    return {
        "ticket": ticket,
        "kind": "mcp",
        "instance": "i",
        "holder": "h",
        "purpose": "p",
        "session_id": "s",
        "owner_pid": 1,
        "owner_created": "T",
        "state": state,
        "run_id": None,
        "created_at": 0,
        "touched_at": 0,
        "claimed_at": 0 if state == "claimed" else None,
        "ttl_s": 600.0 if state == "claimed" else 120.0,
    }


def _identity(name: str) -> ClientIdentity:
    return ClientIdentity(
        "claude", 11, 1, "2026-07-15T00:00:00Z", name, "box"
    )


def _coordinator(now: list[float]) -> SessionCoordinator:
    seq = {"n": 0}

    def next_id() -> str:
        seq["n"] += 1
        return f"ticket-{seq['n']}"

    return SessionCoordinator(
        time_fn=lambda: 0.0,
        wall_time_fn=lambda: now[0],
        id_fn=next_id,
        token_fn=lambda: "unused",
    )


class SharedBoxQueueF3(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            key: os.environ.get(key)
            for key in ("DAYZ_MCP_SHARED_ROOT", "DAYZ_MCP_FAST_TESTS")
        }
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "shared"
        self.root.mkdir()
        os.environ["DAYZ_MCP_SHARED_ROOT"] = str(self.root)

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def _lifecycle(self, now: list[float], launcher=None, name: str = "rt"):
        from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore
        from dayz_mcp.runtime_state import RuntimePaths
        from tests.process_lifecycle_helpers import (
            AuditSink,
            FakeGuard,
            FakeLauncher,
            process,
            snapshot,
        )
        from tests.steam_helpers import FakeSteamGate

        game = self.root / "DayZ"
        game.mkdir(exist_ok=True)
        (game / "DayZDiag_x64.exe").write_bytes(b"")
        coordinator = _coordinator(now)
        paths = RuntimePaths(
            self.root / name,
            self.root / name / "audit",
            self.root / name / "c.json",
            self.root / name / "r.json",
        )
        guard = FakeGuard()
        if launcher is None:
            launcher = FakeLauncher()
        launched = process(launcher.pid)
        guard.snapshots[launched.pid] = snapshot(launched)
        lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=coordinator,
            manifest=RunManifestStore(paths),
            audit=AuditSink(),
            guard=guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=game,
            launcher=launcher,
            id_fn=lambda: f"run-{len(launcher.calls) + 1}",
        )
        return coordinator, lifecycle, launcher, game

    def _request(self, game: Path, **extra: object) -> dict:
        body = {
            "argv": [str(game / "DayZDiag_x64.exe"), "-mission=test"],
            "cwd": str(game),
            "role": "client",
            "window_style": "normal",
            "label": "l",
            "mod": "@M",
            "profiles": "p",
            "mission": "m",
        }
        body.update(extra)
        return body

    def test_f3_pos1_immediate_start_cannot_cut_the_queue(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        owner = _identity("instance-1-owner")
        waiter_b = _identity("instance-2-b")
        waiter_c = _identity("instance-1-c")
        held = coordinator.box_wait_touch(owner, claim=True)
        _, owner_lease = coordinator.acquire(owner, "run")
        started = lifecycle.start_run(owner, owner_lease["lease_token"], self._request(game))
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(len(launcher.calls), 1)
        for run in lifecycle.manifest.list_runs():
            run.state = "EXITED"
        coordinator.box_wait_touch(owner, held["box_ticket"], done=True)
        first = coordinator.box_wait_touch(waiter_b, purpose="b")
        second = coordinator.box_wait_touch(waiter_c, purpose="c")
        self.assertEqual(
            [row["holder"] for row in coordinator.box_queue_public()],
            [waiter_b.session_id[:12], waiter_c.session_id[:12]],
        )
        refused = lifecycle.start_run(
            owner, owner_lease["lease_token"], self._request(game)
        )
        self.assertFalse(refused.get("ok"))
        self.assertEqual(len(launcher.calls), 1)
        fresh = self._lifecycle(now, launcher, name="rt-b")[1]
        fresh.coordinator = coordinator
        coordinator.release(owner, owner_lease["lease_token"])
        claimed_b = coordinator.box_wait_touch(
            waiter_b, first["box_ticket"], claim=True
        )
        status_b, lease_b = coordinator.acquire(waiter_b, "run")
        self.assertEqual(status_b, 200)
        self.assertTrue(claimed_b["box_claimed"])
        started_b = fresh.start_run(
            waiter_b, lease_b["lease_token"], self._request(game)
        )
        self.assertTrue(started_b.get("ok"), started_b)
        self.assertEqual(len(launcher.calls), 2)
        coordinator.box_wait_touch(waiter_b, first["box_ticket"], done=True)
        coordinator.release(waiter_b, lease_b["lease_token"])
        claimed_c = coordinator.box_wait_touch(
            waiter_c, second["box_ticket"], claim=True
        )
        status_c, lease_c = coordinator.acquire(waiter_c, "run")
        self.assertEqual(status_c, 200)
        self.assertTrue(claimed_c["box_claimed"])
        later = self._lifecycle(now, launcher, name="rt-c")[1]
        later.coordinator = coordinator
        started_c = later.start_run(
            waiter_c, lease_c["lease_token"], self._request(game)
        )
        self.assertTrue(started_c.get("ok"), started_c)
        self.assertEqual(len(launcher.calls), 3)

    def test_f3_pos1b_failed_enqueue_then_retry_keeps_order(self) -> None:
        now = [0.0]
        coordinator = _coordinator(now)
        b = _identity("b-session")
        c = _identity("c-session")
        coordinator.box_wait_touch(b, purpose="b")
        real = queue.atomic_write_json
        calls = {"n": 0}

        def fail_once(path, payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("replace failed")
            return real(path, payload)

        with patch.object(queue, "atomic_write_json", fail_once):
            with self.assertRaises(OSError):
                coordinator.box_wait_touch(c, purpose="c")
        again = coordinator.box_wait_touch(c, purpose="c")
        holders = [row["holder"] for row in coordinator.box_queue_public()]
        self.assertEqual(holders, [b.session_id[:12], c.session_id[:12]])
        self.assertTrue(again["box_ticket"])
        tickets = json.loads((self.root / "box-queue.json").read_text(encoding="utf-8"))
        self.assertEqual(len(tickets["entries"]), 2)
        self.assertEqual(len({item["ticket"] for item in tickets["entries"]}), 2)

    def test_f3_pos2_owner_identity_purge(self) -> None:
        root = self.root
        live = {"error": None, "identity_complete": True, "creation_time_utc": "T1"}

        def snapshot(_pid: int) -> dict:
            if live["error"]:
                return {"error": live["error"], "identity_complete": False}
            return {
                "identity_complete": True,
                "creation_time_utc": live["creation_time_utc"],
            }

        def write(created: str = "T1") -> None:
            queue._write(
                root / "box-queue.json",
                {
                    "version": 1,
                    "entries": [
                        {
                            "ticket": "t",
                            "kind": "mcp",
                            "instance": "dayz-mcp",
                            "holder": "holder",
                            "purpose": "p",
                            "session_id": "session",
                            "owner_pid": 4242,
                            "owner_created": created,
                            "state": "claimed",
                            "run_id": "run",
                            "created_at": 0.0,
                            "touched_at": 0.0,
                            "claimed_at": 0.0,
                            "ttl_s": 600.0,
                        }
                    ],
                },
            )

        write("T1")
        view = queue.read_view(now=1.0, snapshot_fn=snapshot, root=root)
        self.assertTrue(view["claimed"])
        self.assertEqual(len(view["entries"]), 1)
        live["error"] = "process_not_found"
        view = queue.read_view(now=1.0, snapshot_fn=snapshot, root=root)
        self.assertEqual(view["entries"], [])
        live["error"] = None
        live["creation_time_utc"] = "T2"
        write("T1")
        view = queue.read_view(now=1.0, snapshot_fn=snapshot, root=root)
        self.assertEqual(view["entries"], [])
        live["error"] = "identity_unavailable"
        live["creation_time_utc"] = "T1"
        write("T1")
        view = queue.read_view(
            now=1.0, session_id="other", snapshot_fn=snapshot, root=root
        )
        self.assertEqual(len(view["entries"]), 1)
        self.assertTrue(view["occupies"])
        external = {
            "version": 1,
            "entries": [
                {
                    "ticket": "ext",
                    "kind": "external",
                    "instance": "external",
                    "holder": "hb",
                    "purpose": "t",
                    "session_id": "external",
                    "owner_pid": 7,
                    "owner_created": "T1",
                    "state": "claimed",
                    "run_id": None,
                    "created_at": 0.0,
                    "touched_at": 0.0,
                    "claimed_at": 0.0,
                    "ttl_s": 600.0,
                }
            ],
        }
        queue._write(root / "box-queue.json", external)
        live["error"] = "identity_unavailable"
        view = queue.read_view(now=1.0, snapshot_fn=snapshot, root=root)
        holder = public_holder(view["external_holder"])
        self.assertIsNotNone(holder)
        self.assertEqual(holder["holder"], "hb")
        self.assertEqual(holder["purpose"], "t")
        self.assertIsNone(holder["run_id"])
        self.assertEqual(holder["instance"], "external")

    def test_f3_pos5_two_processes_lock_and_replace(self) -> None:
        root = self.root
        script = Path(self._tmp.name) / "child.py"
        script.write_text(
            textwrap.dedent(
                """
                import os, sys, time
                from pathlib import Path
                os.environ["DAYZ_MCP_SHARED_ROOT"] = sys.argv[1]
                role = sys.argv[2]
                from dayz_mcp import shared_box_queue as queue
                from dayz_mcp.runtime_state import atomic_write_json
                root = Path(sys.argv[1])
                snap = lambda pid: {
                    "identity_complete": True,
                    "creation_time_utc": "T",
                    "pid": pid,
                }
                if role == "hold":
                    lock = queue._Lock(root, 5.0)
                    lock.__enter__()
                    print("held", flush=True)
                    time.sleep(2.0)
                    lock.__exit__(None, None, None)
                    print("released", flush=True)
                elif role == "join":
                    def mutator(document):
                        document["entries"].append({
                            "ticket": "from-child",
                            "kind": "mcp",
                            "instance": "dayz-mcp-b",
                            "holder": "child",
                            "purpose": "wait",
                            "session_id": "child-session",
                            "owner_pid": os.getpid(),
                            "owner_created": "T",
                            "state": "waiting",
                            "run_id": None,
                            "created_at": 1.0,
                            "touched_at": 1.0,
                            "claimed_at": None,
                            "ttl_s": 120.0,
                        })
                        return document, {}
                    queue.locked_update(
                        mutator, now=1.0, snapshot_fn=snap, root=root, wait_s=5.0
                    )
                    print("joined", flush=True)
                elif role == "replace":
                    import os as _os
                    real = _os.replace
                    def boom(src, dst):
                        if str(dst).endswith("box-queue.json"):
                            raise OSError("replace failed")
                        return real(src, dst)
                    _os.replace = boom
                    try:
                        queue._write(
                            root / "box-queue.json",
                            {"version": 1, "entries": [{"no": "x"}]},
                        )
                    except OSError:
                        print("replace-failed", flush=True)
                    else:
                        print("replace-ok", flush=True)
                """
            ),
            encoding="utf-8",
        )
        python = sys.executable
        env = os.environ.copy()
        env["DAYZ_MCP_SHARED_ROOT"] = str(root)
        env["PYTHONPATH"] = str(_TOOLS)
        hold = subprocess.Popen(
            [python, str(script), str(root), "hold"],
            cwd=str(_TOOLS),
            env=env,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(hold.stdout.readline().strip(), "held")
        try:
            lock_file = root / "box-queue.lock"
            with self.assertRaises(OSError):
                lock_file.unlink()
            started = time.monotonic()
            join = subprocess.run(
                [python, str(script), str(root), "join"],
                cwd=str(_TOOLS),
                env=env,
                capture_output=True,
                text=True,
                timeout=8,
            )
            waited = time.monotonic() - started
            self.assertGreaterEqual(waited, 1.5)
            self.assertEqual(join.returncode, 0, join.stderr)
            self.assertIn("joined", join.stdout)
        finally:
            hold.wait(timeout=8)
        document = json.loads((root / "box-queue.json").read_text(encoding="utf-8"))
        self.assertEqual(document["entries"][0]["ticket"], "from-child")
        before = (root / "box-queue.json").read_bytes()
        replaced = subprocess.run(
            [python, str(script), str(root), "replace"],
            cwd=str(_TOOLS),
            env=env,
            capture_output=True,
            text=True,
            timeout=8,
        )
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        self.assertIn("replace-failed", replaced.stdout)
        self.assertEqual((root / "box-queue.json").read_bytes(), before)

    def test_f3_claim_boundary_and_late_heartbeat(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        owner = _identity("owner-a")
        other = _identity("owner-b")
        joined = coordinator.box_wait_touch(owner, claim=True)
        _, owner_lease = coordinator.acquire(owner, "run")
        now[0] = BOX_CLAIM_TTL_S - 1
        started = lifecycle.start_run(
            owner, owner_lease["lease_token"], self._request(game)
        )
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(len(launcher.calls), 1)
        for run in lifecycle.manifest.list_runs():
            run.state = "EXITED"
        now[0] = (BOX_CLAIM_TTL_S - 1) + BOX_CLAIM_TTL_S
        lost = lifecycle.start_run(
            owner, owner_lease["lease_token"], self._request(game)
        )
        self.assertFalse(lost.get("ok"), lost)
        self.assertEqual(len(launcher.calls), 1)

        async def late() -> None:
            class Client:
                async def session_box_status(self, **_kwargs):
                    return {
                        "box_ticket": None,
                        "box_claimed": False,
                        "box_wait_error": "box_ticket_invalid",
                    }

            async def no_wait(_seconds: float) -> None:
                return None

            await queue_heartbeat(Client(), joined["box_ticket"], sleep_fn=no_wait)

        from dayz_mcp.server import BoxClaimLost
        from dayz_mcp.server import _heartbeat_box_claim as queue_heartbeat

        with self.assertRaises(BoxClaimLost):
            asyncio.run(late())
        self.assertEqual(len(launcher.calls), 1)
        entered = coordinator.box_wait_touch(other, claim=True)
        self.assertTrue(entered["box_claimed"])
        coordinator.box_wait_touch(other, entered["box_ticket"], done=True)
        resumed = lifecycle.start_run(
            owner, owner_lease["lease_token"], self._request(game)
        )
        self.assertFalse(resumed.get("ok"), resumed)
        self.assertEqual(len(launcher.calls), 1)

    def test_f3_sib_does_not_claim_or_release_another_ticket(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        session = _identity("same-session")
        first = coordinator.box_wait_touch(session, claim=True)
        _, lease = coordinator.acquire(session, "run")
        second = coordinator.box_wait_touch(session)
        self.assertTrue(first["box_claimed"])
        sibling = coordinator.box_wait_touch(session, second["box_ticket"], claim=True)
        self.assertFalse(sibling["box_claimed"])
        stolen_start = lifecycle.start_run(
            session,
            lease["lease_token"],
            self._request(game, box_ticket=second["box_ticket"]),
        )
        self.assertFalse(stolen_start.get("ok"), stolen_start)
        self.assertEqual(len(launcher.calls), 0)
        owned = lifecycle.start_run(
            session, lease["lease_token"], self._request(game)
        )
        self.assertTrue(owned.get("ok"), owned)
        self.assertEqual(len(launcher.calls), 1)
        coordinator.box_wait_touch(session, second["box_ticket"], done=True)
        self.assertTrue(coordinator.box_is_claimed())
        self.assertEqual(len(coordinator.box_queue_public()), 1)

    def test_n2_1_done_on_tombstone_releases_the_binding(self) -> None:
        now = [0.0]
        coordinator, lifecycle, _launcher, game = self._lifecycle(now)
        owner = _identity("sess-a")
        claimed = coordinator.box_wait_touch(owner, claim=True)
        _, lease = coordinator.acquire(owner, "run")
        now[0] = BOX_CLAIM_TTL_S
        self.assertEqual(coordinator.box_queue_public(), [])
        coordinator.box_wait_touch(owner, claimed["box_ticket"], done=True)
        started = lifecycle.start_run(
            owner, lease["lease_token"], self._request(game)
        )
        self.assertTrue(started.get("ok"), started)

    def test_n2_2_sibling_sweep_keeps_the_lease_claim(self) -> None:
        now = [0.0]
        coordinator, lifecycle, _launcher, game = self._lifecycle(now)
        session = _identity("same-session")
        coordinator.box_wait_touch(session, claim=True)
        _, lease = coordinator.acquire(session, "run")
        coordinator.box_wait_touch(session)
        coordinator.box_wait_touch(session, None, done=True)
        self.assertTrue(coordinator.box_is_claimed())
        started = lifecycle.start_run(
            session, lease["lease_token"], self._request(game)
        )
        self.assertTrue(started.get("ok"), started)

    def test_n4_real_order_claim_then_acquire_then_start(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        owner = _identity("sess-a")
        other = _identity("sess-b")
        claimed = coordinator.box_wait_touch(owner, claim=True)
        self.assertTrue(claimed.get("box_claimed"))
        status_b, lease_b = coordinator.acquire(other, "run")
        self.assertEqual(status_b, 200)
        refused = lifecycle.start_run(
            other, lease_b["lease_token"], self._request(game)
        )
        self.assertFalse(refused.get("ok"), refused)
        reasons = [
            event.get("reason")
            for event in lifecycle.audit.events
            if event.get("event") == "lifecycle_start_rejected"
        ]
        self.assertEqual(reasons, ["box_claimed"])
        self.assertEqual(len(launcher.calls), 0)
        coordinator.release(other, lease_b["lease_token"])
        status, lease = coordinator.acquire(owner, "run")
        self.assertEqual(status, 200)
        started = lifecycle.start_run(
            owner, lease["lease_token"], self._request(game)
        )
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(len(launcher.calls), 1)

    def test_n4_1_claim_before_acquire_then_start(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        owner = _identity("sess-a")
        claimed = coordinator.box_wait_touch(owner, claim=True)
        self.assertTrue(claimed.get("box_claimed"))
        status, lease = coordinator.acquire(owner, "dayz-test")
        self.assertEqual(status, 200)
        started = lifecycle.start_run(
            owner, lease["lease_token"], self._request(game)
        )
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(len(launcher.calls), 1)

    def test_n4_2_done_after_another_instance_purges(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        other = _coordinator(now)
        owner = _identity("sess-a")
        ticket = coordinator.box_wait_touch(owner, claim=True)["box_ticket"]
        now[0] = BOX_CLAIM_TTL_S
        self.assertEqual(other.box_queue_public(), [])
        coordinator.box_wait_touch(owner, ticket, done=True)
        status, lease = coordinator.acquire(owner, "run")
        self.assertEqual(status, 200)
        started = lifecycle.start_run(
            owner, lease["lease_token"], self._request(game)
        )
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(len(launcher.calls), 1)

    def test_n5_1_foreign_done_keeps_an_expired_claim_binding(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        owner = _identity("sess-a")
        other = _identity("sess-b")
        claimed = coordinator.box_wait_touch(owner, claim=True)
        self.assertTrue(claimed.get("box_claimed"))
        status, lease = coordinator.acquire(owner, "run")
        self.assertEqual(status, 200)
        now[0] = BOX_CLAIM_TTL_S
        coordinator.box_wait_touch(other, None, done=True)
        entered = coordinator.box_wait_touch(other, claim=True)
        self.assertTrue(entered.get("box_claimed"))
        coordinator.box_wait_touch(other, entered["box_ticket"], done=True)
        resumed = lifecycle.start_run(
            owner, lease["lease_token"], self._request(game)
        )
        self.assertFalse(resumed.get("ok"), resumed)
        self.assertEqual(len(launcher.calls), 0)

    def test_n5_1_reaped_other_session_keeps_an_expired_claim_binding(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        owner = _identity("sess-a")
        other = _identity("sess-b")
        claimed = coordinator.box_wait_touch(owner, claim=True)
        self.assertTrue(claimed.get("box_claimed"))
        status, lease = coordinator.acquire(owner, "run")
        self.assertEqual(status, 200)
        now[0] = BOX_CLAIM_TTL_S
        coordinator.note_run_reaped(other.session_id)
        entered = coordinator.box_wait_touch(other, claim=True)
        self.assertTrue(entered.get("box_claimed"))
        coordinator.box_wait_touch(other, entered["box_ticket"], done=True)
        resumed = lifecycle.start_run(
            owner, lease["lease_token"], self._request(game)
        )
        self.assertFalse(resumed.get("ok"), resumed)
        self.assertEqual(len(launcher.calls), 0)

    def test_n4_3_build_failed_is_not_rewritten(self) -> None:
        from dayz_mcp import server
        from dayz_mcp.server import ServerConfig
        from tests.client_helpers import _fixture_client_runtime
        from tests.mcp_helpers import _content_json

        now = [0.0]
        seq = {"n": 0}

        def next_id() -> str:
            seq["n"] += 1
            return f"t-{seq['n']}"

        coordinator = SessionCoordinator(
            time_fn=lambda: 0.0,
            wall_time_fn=lambda: now[0],
            id_fn=next_id,
            token_fn=lambda: "tok",
        )
        other = _identity("sess-other-1234")
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        caller = runtime.identity
        calls: list[object] = []
        other_ticket = [None]

        def box_dict() -> dict[str, object]:
            view = coordinator.box_status_snapshot(caller.session_id)
            out: dict[str, object] = {
                "occupied": view.get("occupies") is True or view.get("claimed") is True,
                "runs": [],
                "foreign": [],
                "ports_in_use": [],
                "queue": view.get("queue", []),
                "scan_known": True,
                "port_scan_known": True,
            }
            if view.get("claimed") and view.get("claimed_s") is not None:
                out["claimed_s"] = view.get("claimed_s")
            return out

        async def session_status() -> dict[str, object]:
            return {"box": box_dict()}

        async def session_box_status(
            wait: bool = False,
            done: bool = False,
            claim: bool = False,
            ticket: object = None,
        ) -> dict[str, object]:
            if wait and ticket is None and other_ticket[0] is not None:
                coordinator.box_wait_touch(other, other_ticket[0], done=True)
                other_ticket[0] = None
            touched = (
                coordinator.box_wait_touch(caller, ticket, done=done, claim=claim)
                if (wait or done or claim or ticket)
                else {}
            )
            return {"box": box_dict(), **touched}

        runtime.session_status = session_status
        runtime.session_box_status = session_box_status

        async def main() -> dict[str, object]:
            with patch.object(server, "ClientRuntime", return_value=runtime):
                app, _built = server.build_app(config)

            async def fake_execute(_client: object, **kwargs: object) -> dict[str, object]:
                calls.append(kwargs.get("build"))
                if len(calls) == 1:
                    now[0] = BOX_CLAIM_TTL_S
                    other_ticket[0] = coordinator.box_wait_touch(other, claim=True)[
                        "box_ticket"
                    ]
                    await asyncio.sleep(0.05)
                    return {
                        "status": "failed",
                        "project": "ExampleMod",
                        "mode": "server",
                        "run_id": None,
                        "error_code": "build_failed",
                        "cleanup_degraded": False,
                    }
                return {
                    "status": "succeeded",
                    "project": "ExampleMod",
                    "mode": "server",
                    "run_id": "run-stale-pbo",
                    "server_alive": True,
                    "client_alive": None,
                    "server_boot": {"ready": True},
                }

            with patch.object(server, "BOX_CLAIM_HEARTBEAT_S", 0.01), patch.object(
                server.dayz_test_tool,
                "execute_dayz_test_run",
                side_effect=fake_execute,
            ):
                return _content_json(
                    await app.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "build": True,
                            "on_busy": "queue",
                            "wait_for_box_s": 2.0,
                        },
                    )
                )

        out = asyncio.run(main())
        self.assertEqual(out.get("error_code"), "build_failed", out)
        self.assertEqual(calls, [True])

    def test_n2_3_lost_claim_does_not_overwrite_a_live_run(self) -> None:
        from dayz_mcp import server
        from dayz_mcp.server import ServerConfig
        from tests.client_helpers import _fixture_client_runtime
        from tests.mcp_helpers import _content_json

        now = [0.0]
        seq = {"n": 0}

        def next_id() -> str:
            seq["n"] += 1
            return f"t-{seq['n']}"

        coordinator = SessionCoordinator(
            time_fn=lambda: 0.0,
            wall_time_fn=lambda: now[0],
            id_fn=next_id,
            token_fn=lambda: "tok",
        )
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        caller = runtime.identity
        runs: list[dict[str, object]] = []

        def box_dict() -> dict[str, object]:
            view = coordinator.box_status_snapshot(caller.session_id)
            out: dict[str, object] = {
                "occupied": bool(runs)
                or view.get("occupies") is True
                or view.get("claimed") is True,
                "runs": list(runs),
                "foreign": [],
                "ports_in_use": [],
                "queue": view.get("queue", []),
            }
            if view.get("claimed") and view.get("claimed_s") is not None:
                out["claimed_s"] = view.get("claimed_s")
            return out

        async def session_status() -> dict[str, object]:
            return {"box": box_dict()}

        async def session_box_status(
            wait: bool = False,
            done: bool = False,
            claim: bool = False,
            ticket: object = None,
        ) -> dict[str, object]:
            touched = (
                coordinator.box_wait_touch(caller, ticket, done=done, claim=claim)
                if (wait or done or claim or ticket)
                else {}
            )
            return {"box": box_dict(), **touched}

        runtime.session_status = session_status
        runtime.session_box_status = session_box_status

        async def main() -> dict[str, object]:
            with patch.object(server, "ClientRuntime", return_value=runtime):
                app, _built = server.build_app(config)

            async def fake_execute(_client: object, **_kwargs: object) -> dict[str, object]:
                runs.append(
                    {
                        "run_id": "run-live-1",
                        "state": "RUNNING",
                        "owner_session": caller.session_id[:12],
                        "mod": "@ExampleMod",
                        "label": "l",
                        "age_s": 1.0,
                    }
                )
                now[0] = BOX_CLAIM_TTL_S
                await asyncio.sleep(0.05)
                return {
                    "status": "ok",
                    "project": "ExampleMod",
                    "mode": "server",
                    "run_id": "run-live-1",
                    "server_alive": True,
                    "client_alive": None,
                    "server_boot": {"ready": True},
                }

            with patch.object(server, "BOX_CLAIM_HEARTBEAT_S", 0.01), patch.object(
                server.dayz_test_tool, "execute_dayz_test_run", side_effect=fake_execute
            ):
                return _content_json(
                    await app.call_tool(
                        "dayz_test_run",
                        {
                            "project": "ExampleMod",
                            "mode": "server",
                            "on_busy": "queue",
                        },
                    )
                )

        out = asyncio.run(main())
        self.assertEqual(out.get("status"), "ok", out)
        self.assertEqual(out.get("run_id"), "run-live-1", out)

    def test_n2_p2_enrich_keeps_unreadable(self) -> None:
        from dayz_mcp.box_occupancy import _enrich_active_run_result

        out = _enrich_active_run_result(
            {"error_code": "active_run_exists"},
            {
                "reason": "box_queue_unreadable",
                "runs": [
                    {
                        "run_id": "other-run",
                        "state": "RUNNING",
                        "owner_session": "other",
                    }
                ],
            },
            caller_session="me",
        )
        self.assertEqual(out.get("reason"), "box_queue_unreadable")
        self.assertIn("box-queue.json", str(out.get("hint")))
        self.assertIsNone(out.get("queue_offer"))
        self.assertNotEqual(out.get("error_code"), "takeover_required")

    def test_n2_p3_touch_oserror_is_unreadable(self) -> None:
        from types import SimpleNamespace

        from dayz_mcp.loopback import Handler
        from dayz_mcp.shared_box_queue import QueueUnreadable

        captured: dict[str, object] = {}
        handler = Handler.__new__(Handler)
        handler._read_json = lambda: {
            "identity": {
                "platform": "claude",
                "pid": 11,
                "ppid": 1,
                "started_at_utc": "2026-07-15T00:00:00Z",
                "session_id": "sess-p3",
                "task_label": "box",
            },
            "box_wait": True,
        }
        handler._json = lambda status, payload: captured.update(
            status=status, payload=payload
        )
        handler._log = lambda *_args, **_kwargs: None
        handler._persist_coordination = lambda payload: payload
        coordinator = SessionCoordinator(
            time_fn=lambda: 0.0, wall_time_fn=lambda: 0.0, token_fn=lambda: "t"
        )

        def boom(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise QueueUnreadable()

        coordinator.box_wait_touch = boom  # type: ignore[method-assign]
        coordinator.status = lambda _client: {}  # type: ignore[method-assign]
        handler.server = SimpleNamespace(state=SimpleNamespace(
            coordination=coordinator,
            coordination_store=None,
            daemon_generation="g",
            pending_for_owner=lambda _session: [],
            lifecycle=None,
            _pending_profile_test_remains=lambda: False,
        ))
        with patch(
            "dayz_mcp.loopback._box_payload",
            return_value={"occupied": True, "queue": [], "runs": [], "foreign": []},
        ), patch(
            "dayz_mcp.loopback._retired_run_diagnostics_payload",
            return_value=[],
        ):
            handler._handle_session("status")
        payload = captured["payload"]
        self.assertIsInstance(payload, dict)
        assert isinstance(payload, dict)
        self.assertEqual(payload.get("box_wait_error"), "box_queue_unreadable")
        self.assertIsNone(payload.get("box_ticket"))

    def test_f3_neg1_unreadable_occupies_and_blocks_takeover(self) -> None:
        from dayz_mcp import dayz_test_tool
        from dayz_mcp.server import _failed_active_run_result

        root = self.root
        path = root / "box-queue.json"
        body = b"\xff\xfe\x00\x00"
        path.write_bytes(body)
        view = queue.read_view(now=time.time(), session_id="caller", root=root)
        self.assertTrue(view["unreadable"])
        self.assertEqual(path.read_bytes(), body)
        reason = queue.admit("caller", "ticket", now=time.time(), root=root)
        self.assertEqual(reason, "box_queue_unreadable")
        self.assertEqual(path.read_bytes(), body)
        stops: list[str] = []

        def stop(*_args, **_kwargs):
            stops.append("stop")
            raise AssertionError("takeover stop")

        box = {
            "occupied": True,
            "reason": "box_queue_unreadable",
            "runs": [
                {
                    "run_id": "r1",
                    "state": "RUNNING",
                    "owner_session": "other",
                    "mod": "@M",
                    "label": "l",
                    "age_s": 1.0,
                }
            ],
            "foreign": [],
            "ports_in_use": [],
            "queue": [{"holder": "other"}],
        }
        with patch.object(dayz_test_tool, "execute_dayz_test_stop", stop):
            out = _failed_active_run_result(
                project="P",
                mode="server",
                box=box,
                started=0.0,
                caller_session="me",
            )
        self.assertEqual(stops, [])
        self.assertEqual(out.get("reason"), "box_queue_unreadable")
        self.assertIn("box-queue.json", out.get("hint", ""))
        self.assertIsNone(out.get("queue_offer"))
        self.assertNotEqual(out.get("error_code"), "takeover_required")

    def test_wall_clock_survives_a_monotonic_lease_clock(self) -> None:
        coordinator = SessionCoordinator(time_fn=lambda: 50_000.0)
        owner = _identity("wall")
        coordinator.box_wait_touch(owner, claim=True, kind="external", holder="hb", purpose="t")
        view = queue.read_view(now=time.time(), root=self.root)
        self.assertTrue(view["entries"])

    def test_read_does_not_create_the_document(self) -> None:
        path = self.root / "box-queue.json"
        view = queue.read_view(now=1.0, root=self.root)
        self.assertFalse(view.get("unreadable"))
        self.assertFalse(path.exists())

    def test_permission_error_does_not_fail_acquire(self) -> None:
        coordinator = SessionCoordinator()
        owner = _identity("perm")
        stale = _entry("old", "waiting")
        stale["touched_at"] = 0.0
        stale["created_at"] = 0.0
        queue._write(
            self.root / "box-queue.json",
            {"version": 1, "entries": [stale]},
        )
        with patch.object(queue, "atomic_write_json", side_effect=PermissionError("AV")):
            status, _body = coordinator.acquire(owner, "work")
        self.assertEqual(status, 200)

    def test_broken_read_is_unreadable_not_empty(self) -> None:
        path = self.root / "box-queue.json"
        path.write_bytes(b"\xff\xfe")
        real_open = open

        def boom(name, mode="r", *args, **kwargs):
            if str(name).endswith("box-queue.json"):
                raise PermissionError("stat")
            return real_open(name, mode, *args, **kwargs)

        with patch("builtins.open", boom):
            view = queue.read_view(now=1.0, root=self.root)
        self.assertTrue(view["unreadable"])
        self.assertEqual(path.read_bytes(), b"\xff\xfe")

    def test_lock_waits_instead_of_leaking_the_descriptor(self) -> None:
        held = queue._Lock(self.root, 1.0)
        held.__enter__()
        try:
            other = queue._Lock(self.root, 0.2)

            class Empty:
                st_size = 0

            with patch("os.fstat", return_value=Empty()):
                with self.assertRaises(TimeoutError):
                    other.__enter__()
            self.assertIsNone(other.descriptor)
        finally:
            held.__exit__(None, None, None)

    def test_queue_detects_a_unittest_or_pytest_process(self) -> None:
        previous_pytest = os.environ.pop("PYTEST_CURRENT_TEST", None)
        argv = sys.argv
        hidden = {}
        for name in ("pytest", "unittest"):
            if name in sys.modules:
                hidden[name] = sys.modules.pop(name)
        try:
            sys.argv = ["dayz-mcp.exe"]
            self.assertFalse(queue._process_is_test())
            sys.argv = ["python.exe -m unittest", "tests.test_shared_box_queue_f3"]
            self.assertTrue(queue._process_is_test())
            sys.argv = ["dayz-mcp.exe"]
            sys.modules["unittest"] = hidden.get("unittest") or unittest
            self.assertTrue(queue._process_is_test())
            os.environ["PYTEST_CURRENT_TEST"] = "tests/test_shared_box_queue_f3.py"
            self.assertTrue(queue._process_is_test())
        finally:
            sys.argv = argv
            for name in ("pytest", "unittest"):
                sys.modules.pop(name, None)
            sys.modules.update(hidden)
            if previous_pytest is None:
                os.environ.pop("PYTEST_CURRENT_TEST", None)
            else:
                os.environ["PYTEST_CURRENT_TEST"] = previous_pytest

    def test_parse_rejects_nan_duplicate_and_claimed_tail(self) -> None:
        nan = (
            '{"version":1,"entries":[{"ticket":"t1","kind":"mcp","instance":"i",'
            '"holder":"h","purpose":"p","session_id":"s","owner_pid":1,'
            '"owner_created":"T","state":"claimed","run_id":null,"created_at":0,'
            '"touched_at":NaN,"claimed_at":0,"ttl_s":600.0}]}'
        )
        self.assertIsNone(queue.parse_document(nan))
        duplicate = {
            "version": 1,
            "entries": [
                _entry("a", "claimed"),
                _entry("a", "waiting"),
            ],
        }
        self.assertIsNone(queue.parse_document(json.dumps(duplicate)))
        tail = {
            "version": 1,
            "entries": [_entry("a", "waiting"), _entry("b", "claimed")],
        }
        self.assertIsNone(queue.parse_document(json.dumps(tail)))

    def test_bind_timeout_is_unreadable(self) -> None:
        with patch.object(queue, "locked_update", side_effect=TimeoutError("box_queue_lock_timeout")):
            reason = queue.bind_run("s", "t", "run", now=1.0, root=self.root)
        self.assertEqual(reason, "box_queue_unreadable")

    def test_extension_of_own_run_ignores_a_waiter(self) -> None:
        now = [0.0]
        coordinator, lifecycle, launcher, game = self._lifecycle(now)
        owner = _identity("owner-ext")
        waiter = _identity("waiter-ext")
        coordinator.box_wait_touch(owner, claim=True)
        _, lease = coordinator.acquire(owner, "run")
        started = lifecycle.start_run(owner, lease["lease_token"], self._request(game))
        self.assertTrue(started.get("ok"), started)
        coordinator.box_wait_touch(owner, done=True)
        coordinator.box_wait_touch(waiter, purpose="wait")
        seen: list[object] = []

        def spy(client, ticket):
            seen.append(ticket)
            return "box_queue_ahead"

        coordinator.box_admit_start = spy
        extended = lifecycle.start_run(
            owner,
            lease["lease_token"],
            self._request(game, run_id=started["run_id"], role="client"),
        )
        self.assertEqual(seen, [])
        self.assertNotEqual(extended.get("error"), "active_run_exists")

    def test_test_process_refuses_real_profile(self) -> None:
        local = os.environ.get("LOCALAPPDATA", "").strip()
        self.assertTrue(local)
        real = Path(local) / "DayZ_MCP_shared"
        os.environ["DAYZ_MCP_FAST_TESTS"] = "1"
        os.environ["DAYZ_MCP_SHARED_ROOT"] = str(real)
        with self.assertRaises(OSError) as raised:
            queue.queue_root()
        self.assertIn("shared_root_uses_real_profile", str(raised.exception))
        with self.assertRaises(OSError):
            queue.locked_update(
                lambda document: (document, {"write": True}),
                now=1.0,
                root=real,
            )
        self.assertFalse((real / "box-queue.json").exists())
        self.assertFalse((real / "box-queue.lock").exists())


if __name__ == "__main__":
    unittest.main()
