from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import process_lifecycle as process_lifecycle_mod
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.test_process_lifecycle import (
    IDENTITY_A,
    AuditSink,
    FakeGuard,
    FakeLauncher,
    process,
    snapshot,
)

_PRODUCTION_LIFECYCLE = (_TOOLS_DIR / "dayz_mcp" / "process_lifecycle.py").resolve()

_KILL_ATTRS = frozenset(
    {
        "terminate",
        "kill",
        "_terminate_open_handle",
        "terminateprocess",
        "terminateprocessw",
        "createprocess",
        "createprocessw",
    }
)
_KILL_OS = frozenset({"kill", "system", "popen"})


def _production_source_path() -> Path:
    return Path(process_lifecycle_mod.__file__).resolve()


def _attr_chain(node: ast.AST) -> list[str]:
    parts: list[str] = []
    current: ast.AST | None = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    parts.reverse()
    return parts


def _class_methods(tree: ast.AST, class_name: str) -> dict[str, ast.FunctionDef]:
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return {
        node.name: node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _value_is_empty_list(value: ast.AST | None) -> bool:
    if value is None:
        return False
    if isinstance(value, ast.List) and not value.elts:
        return True
    if isinstance(value, ast.IfExp):
        return _value_is_empty_list(value.body) or _value_is_empty_list(value.orelse)
    return False


def _reachable_kill_hits(
    methods: dict[str, ast.FunctionDef], start: str
) -> tuple[set[str], list[str]]:
    visited: set[str] = set()
    stack = [start]
    hits: list[str] = []
    while stack:
        name = stack.pop()
        if name in visited:
            continue
        visited.add(name)
        func = methods.get(name)
        if func is None:
            continue
        for node in ast.walk(func):
            if isinstance(node, ast.Attribute) and node.attr.casefold() in _KILL_ATTRS:
                hits.append(f"{name}:{node.lineno}:{node.attr}")
            if not isinstance(node, ast.Call):
                continue
            chain = _attr_chain(node.func)
            if not chain:
                continue
            last = chain[-1].casefold()
            if last in _KILL_ATTRS:
                hits.append(f"{name}:{node.lineno}:call .{chain[-1]}")
            if chain[0] == "os" and last in _KILL_OS:
                hits.append(f"{name}:{node.lineno}:os.{chain[-1]}")
            if chain[0] == "subprocess":
                hits.append(f"{name}:{node.lineno}:subprocess.{chain[-1]}")
            if chain[0] == "self" and len(chain) == 2:
                stack.append(chain[1])
            elif chain[0] == "ProcessLifecycle" and len(chain) == 2:
                stack.append(chain[1])
            elif len(chain) == 1 and chain[0] in methods:
                stack.append(chain[0])
    return visited, hits


class ReapDeadRunsLockedTerminateInvariantTest(unittest.TestCase):
    """The locked reaper must stay a no-terminate path, or quarantine
    cannot be allowed to let it run."""

    DECLARED_EXCEPTIONS: dict[str, str] = {}

    def test_reap_dead_runs_locked_cannot_reach_terminate(self) -> None:
        source_path = _production_source_path()
        self.assertEqual(source_path, _PRODUCTION_LIFECYCLE)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        methods = _class_methods(tree, "ProcessLifecycle")
        self.assertIn("_reap_dead_runs_locked", methods)
        visited, hits = _reachable_kill_hits(methods, "_reap_dead_runs_locked")
        allowed = set(self.DECLARED_EXCEPTIONS)
        self.assertIn("_reap_run_locked", visited)
        self.assertIn("_run_all_dead", visited)
        self.assertIn("_classify_registered_process", visited)
        leftover = [hit for hit in hits if hit.split(":", 1)[0] not in allowed]
        self.assertEqual(
            leftover,
            [],
            "terminate/kill reachable from _reap_dead_runs_locked: "
            + "; ".join(leftover),
        )

    def test_reap_dead_runs_does_not_early_return_under_quarantine(self) -> None:
        source_path = _production_source_path()
        self.assertEqual(source_path, _PRODUCTION_LIFECYCLE)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        methods = _class_methods(tree, "ProcessLifecycle")
        func = methods["reap_dead_runs"]
        calls = {
            chain[-1]
            for node in ast.walk(func)
            if isinstance(node, ast.Call)
            for chain in [_attr_chain(node.func)]
            if chain
        }
        empty_returns = [
            node.lineno
            for node in ast.walk(func)
            if isinstance(node, ast.Return) and _value_is_empty_list(node.value)
        ]
        self.assertIn("_reap_dead_runs_locked", calls)
        self.assertIn("_quarantined", calls)
        self.assertEqual(
            empty_returns,
            [],
            "reap_dead_runs returns [] (quarantine skip) at lines "
            + str(empty_returns),
        )


class ReapUnderQuarantineBehaviorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        for name in (
            "DayZDiag_x64.exe",
            "DayZ_BE.exe",
            "DayZ_x64.exe",
            "DayZServer_x64.exe",
        ):
            (self.game / name).write_bytes(b"")
        paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.audit = AuditSink()
        self.coordinator = SessionCoordinator(
            token_fn=lambda: "token-A",
            id_fn=lambda: "lease-A",
            audit=self.audit,
        )
        status, acquired = self.coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token_a = acquired["lease_token"]
        self.store = RunManifestStore(paths)
        self.guard = FakeGuard()
        self.launcher = FakeLauncher()
        self.probe_result: dict[str, object] = {"known": True, "processes": []}
        self.lifecycle = ProcessLifecycle(
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: self.probe_result,
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=self.launcher,
            id_fn=lambda: "run-1",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_run(self, record, *, owner: str | None = "A", state: str = "RUNNING"):
        from dayz_mcp.process_lifecycle import RunRecord

        run = RunRecord(
            "run-existing",
            owner,
            "lease-A" if owner else None,
            state,
            "same",
            "@SameMod",
            "profiles",
            "mission",
            [record],
        )
        self.store.add(run)
        return run

    def _dead(self, pid: int) -> None:
        self.guard.snapshots[pid] = {"error": "process_not_found", "exit_code": 4}

    def _persisted_run(self, run_id: str) -> dict[str, object]:
        payload = json.loads(self.store.paths.runs_path.read_bytes())
        for run in payload["runs"]:
            if run.get("run_id") == run_id:
                return run
        raise AssertionError(f"run {run_id!r} missing from persisted manifest")

    def _assert_bound_to_production(self) -> None:
        self.assertEqual(_production_source_path(), _PRODUCTION_LIFECYCLE)
        self.assertIs(type(self.lifecycle), ProcessLifecycle)
        self.assertIs(
            self.lifecycle.reap_dead_runs.__func__, ProcessLifecycle.reap_dead_runs
        )
        self.assertIs(
            self.lifecycle._quarantined.__func__, ProcessLifecycle._quarantined
        )
        self.assertIs(
            self.lifecycle._reap_dead_runs_locked.__func__,
            ProcessLifecycle._reap_dead_runs_locked,
        )
        self.assertIs(type(self.lifecycle.manifest), RunManifestStore)
        self.assertIs(self.lifecycle.manifest, self.store)

    def _arm_retail_quarantine(self) -> None:
        self.probe_result = {
            "known": True,
            "processes": [{"pid": 5, "name": "DayZ_x64.exe"}],
        }
        self.assertTrue(ProcessLifecycle._quarantined(self.lifecycle))

    def test_quarantined_fails_closed_when_no_retail_probe_is_wired(self) -> None:
        """No probe means no evidence retail is clear, so the answer is quarantined.

        Pre-existing branch, uncovered until now: flipping it to False left the whole
        suite green, and it is the guard that keeps start/stop/adopt from acting on a
        machine whose retail state nobody can read.
        """
        self._assert_bound_to_production()
        self.lifecycle.retail_probe = None
        self.assertTrue(ProcessLifecycle._quarantined(self.lifecycle))

    def test_production_quarantined_reads_instance_retail_probe(self) -> None:
        self._assert_bound_to_production()
        self.assertFalse(ProcessLifecycle._quarantined(self.lifecycle))
        self.probe_result = {
            "known": True,
            "processes": [{"pid": 5, "name": "DayZ_x64.exe"}],
        }
        self.assertTrue(ProcessLifecycle._quarantined(self.lifecycle))
        self.probe_result = {"known": True, "processes": []}
        self.assertFalse(ProcessLifecycle._quarantined(self.lifecycle))

    def test_reaper_retires_dead_run_under_retail_quarantine(self) -> None:
        self._assert_bound_to_production()
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        self.assertFalse(ProcessLifecycle._quarantined(self.lifecycle))
        self._arm_retail_quarantine()
        reaped = ProcessLifecycle.reap_dead_runs(self.lifecycle)
        self.assertEqual(reaped, ["run-existing"])
        stored = self.store.get("run-existing")
        self.assertEqual(stored.state, "EXITED")
        self.assertEqual(stored.processes, [])
        self.assertIsNone(stored.owner_session_id)
        persisted = self._persisted_run("run-existing")
        self.assertEqual(persisted["state"], "EXITED")
        self.assertEqual(persisted["processes"], [])
        self.assertEqual(self.guard.terminate_calls, [])
        quarantine_events = [
            event
            for event in self.audit.events
            if event.get("event") == "reap_under_quarantine"
        ]
        self.assertEqual(len(quarantine_events), 1)
        self.assertEqual(quarantine_events[0].get("reason"), "retail_quarantine")
        self.assertEqual(quarantine_events[0].get("decision"), "continued")
        self.assertIn(
            "run_reaped", [event.get("event") for event in self.audit.events]
        )

    def test_reaper_does_not_reap_live_run_under_retail_quarantine(self) -> None:
        rec = process(48976)
        self.add_run(rec, owner=None, state="RUNNING_IDLE")
        self.guard.snapshots[48976] = snapshot(rec)
        self._arm_retail_quarantine()
        reaped = ProcessLifecycle.reap_dead_runs(self.lifecycle)
        self.assertEqual(reaped, [])
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")
        self.assertEqual(self._persisted_run("run-existing")["state"], "RUNNING_IDLE")
        self.assertEqual(self.guard.terminate_calls, [])

    def test_reaper_does_not_emit_quarantine_audit_when_retail_is_clear(self) -> None:
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        self.assertFalse(ProcessLifecycle._quarantined(self.lifecycle))
        ProcessLifecycle.reap_dead_runs(self.lifecycle)
        self.assertEqual(
            [
                event
                for event in self.audit.events
                if event.get("event") == "reap_under_quarantine"
            ],
            [],
        )

    def test_quarantine_audit_failure_does_not_block_reap(self) -> None:
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        self._arm_retail_quarantine()
        self.audit.fail_events.add("reap_under_quarantine")
        reaped = ProcessLifecycle.reap_dead_runs(self.lifecycle)
        self.assertEqual(reaped, ["run-existing"])
        self.assertEqual(self.store.get("run-existing").state, "EXITED")
        self.assertEqual(self._persisted_run("run-existing")["state"], "EXITED")
        self.assertEqual(self.guard.terminate_calls, [])

    def test_run_reaped_audit_still_fail_closed_under_quarantine(self) -> None:
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        self._arm_retail_quarantine()
        self.audit.fail_events.add("run_reaped")
        reaped = ProcessLifecycle.reap_dead_runs(self.lifecycle)
        self.assertEqual(reaped, [])
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")
        self.assertEqual(self._persisted_run("run-existing")["state"], "RUNNING_IDLE")
        self.assertEqual(self.guard.terminate_calls, [])

    def test_agent_reap_dead_run_still_blocked_under_retail_quarantine(self) -> None:
        self.add_run(process(48976), owner=None, state="RUNNING_IDLE")
        self._dead(48976)
        self._arm_retail_quarantine()
        result = ProcessLifecycle.reap_dead_run(
            self.lifecycle, IDENTITY_A, self.token_a, "run-existing"
        )
        self.assertEqual(result.get("error"), "retail_quarantine")
        self.assertEqual(self.store.get("run-existing").state, "RUNNING_IDLE")
        self.assertEqual(self._persisted_run("run-existing")["state"], "RUNNING_IDLE")
        self.assertEqual(self.guard.terminate_calls, [])


if __name__ == "__main__":
    unittest.main()
