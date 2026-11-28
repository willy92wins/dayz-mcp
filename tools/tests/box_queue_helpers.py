"""Shared box-queue test fixtures (not itself a collected test module)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator


def box_entry(ticket: str, state: str) -> dict:
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


def box_identity(name: str) -> ClientIdentity:
    return ClientIdentity(
        "claude", 11, 1, "2026-07-15T00:00:00Z", name, "box"
    )


def box_coordinator(now: list[float]) -> SessionCoordinator:
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


class BoxQueueFixture:
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
        coordinator = box_coordinator(now)
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
