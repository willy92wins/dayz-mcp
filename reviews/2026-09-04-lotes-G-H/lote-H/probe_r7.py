# -*- coding: utf-8 -*-
"""Receipt probe for cierre 4 (lote H round 7): the §2 criteria the gate does not encode.

Run:  cd <ws>/tools && PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 <python> ../../probe_r7.py
Run it against ws-frozen-r6/tools too: every line marked RED-BEFORE must fail there.

  (A) every registered process foreign (pid reused)      -> adopt rejects run_processes_gone
  (B1) normal release -> adopt with a BOUND binding       -> ok + dispatchable True
  (B2) adopt with bindings None                           -> ok + dispatchable False + hint
  (C) the volatile marker is gone from the two modules    -> grep count 0
  (D) lock order: computing dispatchable never takes _operation_lock from inside ServerState
      (static: the ServerState helper must not reference lifecycle._operation_lock)
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from dayz_mcp import loopback
from dayz_mcp.process_lifecycle import ProcessLifecycle, ProcessRecord, RunManifestStore
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator

IDENTITY = ClientIdentity("codex", 11, 1, "2026-07-15T00:00:00Z", "A", "owner")
CREATED_UTC = "2026-07-15T00:00:41.0000000Z"
HASH_A = "a" * 64
INST_SERVER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
INST_CLIENT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
LINES: list[str] = []


def report(tag: str, ok: bool, detail: str) -> None:
    LINES.append(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


class Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> bool:
        self.events.append(event)
        return True


class Guard:
    def __init__(self) -> None:
        self.snapshots: dict[int, dict] = {}

    def snapshot(self, pid: int) -> dict:
        return dict(self.snapshots.get(pid, {"error": "identity_unavailable"}))

    def terminate(self, record: ProcessRecord) -> dict:
        return {"terminated": True}


class Launcher:
    pid = 9001
    confirmed_exit = True

    def __call__(self, argv, cwd, window_style):
        return self

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float) -> None:
        return None


def build(tmp: Path, bindings=None):
    game = tmp / "game"
    game.mkdir(parents=True, exist_ok=True)
    (game / "DayZDiag_x64.exe").write_bytes(b"")
    paths = RuntimePaths(tmp / "runtime", tmp / "runtime" / "audit",
                         tmp / "runtime" / "coordination.json", tmp / "runtime" / "runs.json")
    sink = Sink()
    coord = SessionCoordinator(token_fn=lambda: "token-A", id_fn=lambda: "lease-A", audit=sink)
    status, acquired = coord.acquire(IDENTITY, "lifecycle")
    assert status == 200, status
    guard = Guard()
    guard.snapshots[9001] = {
        "pid": 9001, "creation_time_utc": CREATED_UTC, "executable_sha256": HASH_A,
        "command_line_sha256": "b" * 64, "identity_scheme": "psutil-argv-v2",
        "identity_complete": True,
    }
    life = ProcessLifecycle(
        coordinator=coord, manifest=RunManifestStore(paths), audit=sink, guard=guard,
        retail_probe=lambda: {"known": True, "processes": []},
        diag_probe=lambda: {"known": True, "processes": []},
        game_path=game, launcher=Launcher(), id_fn=lambda: "run-1",
        argv_of=lambda pid: [str(game / "DayZDiag_x64.exe"), "-mission=test"],
        bindings=bindings,
    )
    started = life.start_run(IDENTITY, acquired["lease_token"], {
        "argv": [str(game / "DayZDiag_x64.exe"), "-mission=test"], "cwd": str(game),
        "role": "client", "window_style": "normal", "label": "probe", "mod": "@SameMod",
        "profiles": "profiles", "mission": "test",
    })
    assert started.get("ok") is True, started
    return life, sink


def bound_state(life):
    state = loopback.ServerState("clave-probe")
    state.install_bound_peer(instance=INST_SERVER, role="server", pid=41001, run_id="run-1")
    state.install_bound_peer(instance=INST_CLIENT, role="client", pid=41002, run_id="run-1")
    state.lifecycle = life
    life.bindings = state
    return state


def release(life):
    run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
    life.release_owner(run.owner_session_id, run.owner_lease_id)


# (A) all registered processes foreign -> run_processes_gone           RED-BEFORE
with tempfile.TemporaryDirectory() as d:
    life, _ = build(Path(d))
    bound_state(life)
    release(life)
    life.guard.snapshots[9001] = {
        "pid": 9001, "creation_time_utc": "2026-09-04T00:00:00.0000000Z",
        "executable_sha256": "f" * 64, "command_line_sha256": "f" * 64,
        "identity_scheme": "psutil-argv-v2", "identity_complete": True,
    }
    adopted = life.adopt_run(IDENTITY, "token-A", "run-1")
    durable = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0].state
    report("A-todos-foreign RED-BEFORE",
           adopted.get("ok") is not True and adopted.get("error") == "run_processes_gone"
           and "reap" in str(adopted.get("hint", "")).lower() and durable == "RUNNING_IDLE",
           f"adopt={adopted!r} durable={durable!r}")

# (B1) normal release -> adopt with BOUND bindings -> dispatchable True   RED-BEFORE
with tempfile.TemporaryDirectory() as d:
    life, _ = build(Path(d))
    state = bound_state(life)
    release(life)
    adopted = life.adopt_run(IDENTITY, "token-A", "run-1")
    st, payload = state.enqueue_command("world_spawn", {"classname": "SurvivorM_Mirek"}, peer="server")
    report("B1-adopt-normal-dispatchable-True RED-BEFORE",
           adopted.get("ok") is True and adopted.get("dispatchable") is True and st == 200,
           f"adopt={adopted!r} enqueue={st} {payload!r}")

# (B2) adopt with bindings None -> ok + dispatchable False + hint          RED-BEFORE
with tempfile.TemporaryDirectory() as d:
    life, _ = build(Path(d))
    release(life)
    adopted = life.adopt_run(IDENTITY, "token-A", "run-1")
    report("B2-bindings-None-dispatchable-False RED-BEFORE",
           adopted.get("ok") is True and adopted.get("dispatchable") is False
           and isinstance(adopted.get("hint"), str) and adopted.get("hint"),
           f"adopt={adopted!r}")

# (C) the volatile marker is gone                                          RED-BEFORE
raiz = Path(__file__).resolve().parent
ws_tools = Path.cwd()
patron = re.compile(r"_retired_run_ids|run_bindings_retired|binding_retired_pending_reap|_BINDING_RETIRED_PENDING_REAP_HINT")
hits = {}
for rel in ("dayz_mcp/loopback.py", "dayz_mcp/process_lifecycle.py"):
    texto = (ws_tools / rel).read_text(encoding="utf-8", errors="replace")
    hits[rel] = len(patron.findall(texto))
report("C-marca-volatil-ausente RED-BEFORE", all(v == 0 for v in hits.values()), f"hits={hits}")

# (D) static lock order: the ServerState side never reaches for the lifecycle's operation lock
texto = (ws_tools / "dayz_mcp/loopback.py").read_text(encoding="utf-8", errors="replace")
# A comment naming the lock is not an acquisition: count `with ..._operation_lock` and
# `_operation_lock.acquire(` only (the r6 tree carries exactly one mention, a comment).
tomas = re.findall(r"with\s+[\w.]*_operation_lock|_operation_lock\.acquire\(", texto)
report("D-loopback-no-toma-_operation_lock", not tomas,
       f"adquisiciones de _operation_lock en loopback.py={len(tomas)} menciones={texto.count('_operation_lock')}")

print("\n".join(LINES))
print("PROBE-R7:", "VERDE" if all(l.startswith("[PASS]") for l in LINES) else "ROJO")
sys.exit(0 if all(l.startswith("[PASS]") for l in LINES) else 1)
