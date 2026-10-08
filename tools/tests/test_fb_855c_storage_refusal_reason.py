"""Item 855c: a server start that answered storage_recovery_required is not replayed, so the refusal pair reaches the caller."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dayz_mcp import dayz_test_request, dayz_test_storage, dayz_test_tool, dayz_test_worker
from dayz_mcp.native_broker_protocol import decode_request


RUN_ID = "run-existing"
TERMINATION = "--- Termination successfully completed ---\n"


HINT = dayz_test_storage.storage_recovery_hint("journal_name_invalid")


def _runtime_policy() -> dayz_test_worker.WorkerRuntimePolicy:
    return dayz_test_worker.WorkerRuntimePolicy(
        dev_root=r"P:\ExampleMod_Suite",
        mod="ExampleMod",
        diag_executable=r"C:\DayZDiag_x64.exe",
        game_directory=r"C:\DayZ",
        mission_aliases=(("chernarus", r"C:\missions\dayzOffline.chernarusplus"),),
        mods_root=r"P:\Mods",
        build_temp_root=r"P:\temp",
        build_source_basename=None,
    )


def _payload() -> dict[str, object]:
    return {
        "mission": "chernarus",
        "port": 2302,
        "no_file_patching": True,
        "base_mods": ["@CF"],
    }


def _ids():
    issued = {"n": 0}

    def _id() -> str:
        issued["n"] += 1
        return f"{issued['n']:08x}-1111-4111-8111-111111111111"

    return _id


class _Broker:
    def __init__(self, starts: list[object]) -> None:
        self._starts = list(starts)
        self.commands: list[str] = []

    async def invoke(self, frame: bytes) -> dict[str, object]:
        request = decode_request(frame)
        command = str(request.payload.get("command"))
        self.commands.append(command)
        if command == "start":
            step = self._starts.pop(0)
            if isinstance(step, BaseException):
                raise step
            assert isinstance(step, dict)
            return step
        if command == "stop":
            return {
                "ok": True,
                "state": "EXITED",
                "run_id": request.payload.get("run_id"),
            }
        return {"ok": True}


def _refusal(**extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "ok": False,
        "error": "storage_recovery_required",
    }
    body.update(extra)
    return body


class StorageRecoveryReplayTest(unittest.IsolatedAsyncioTestCase):
    async def _start(self, broker: _Broker) -> None:
        await dayz_test_worker._start(
            broker,
            _payload(),
            _runtime_policy(),
            role="server",
            existing_run_id=None,
            id_fn=_ids(),
        )

    async def test_storage_recovery_required_is_not_replayed_and_keeps_the_pair(self) -> None:
        broker = _Broker(
            [
                _refusal(
                    storage_recovery_reason="journal_name_invalid",
                    storage_recovery_hint=HINT,
                )
            ]
        )
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as caught:
            await self._start(broker)
        error = caught.exception
        self.assertEqual(error.code, "storage_recovery_required")
        self.assertEqual(error.storage_recovery_reason, "journal_name_invalid")
        self.assertEqual(error.storage_recovery_hint, HINT)
        self.assertIsNone(error.run_id)
        self.assertFalse(error.cleanup_degraded)
        self.assertEqual(broker.commands, ["start", "stop"])
        self.assertNotIn("launch_identity_conflict", broker.commands)

    async def test_lost_start_response_is_still_replayed(self) -> None:
        broker = _Broker(
            [
                RuntimeError("lost"),
                _refusal(
                    storage_recovery_reason="journal_name_invalid",
                    storage_recovery_hint=HINT,
                ),
            ]
        )
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as caught:
            await self._start(broker)
        self.assertEqual(caught.exception.storage_recovery_reason, "journal_name_invalid")
        self.assertEqual(broker.commands, ["start", "start", "stop"])

    async def test_other_lifecycle_rejection_is_still_replayed(self) -> None:
        broker = _Broker(
            [
                {"ok": False, "error": "port_still_held"},
                {"ok": False, "error": "port_still_held"},
            ]
        )
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as caught:
            await self._start(broker)
        self.assertEqual(caught.exception.code, "port_still_held")
        self.assertIsNone(caught.exception.storage_recovery_reason)
        self.assertEqual(broker.commands, ["start", "start", "stop"])

    async def test_absent_incomplete_unknown_and_wrong_hint_collapse_without_replay(self) -> None:
        cases = (
            _refusal(),
            _refusal(storage_recovery_reason="journal_name_invalid"),
            _refusal(
                storage_recovery_reason="not_a_reason",
                storage_recovery_hint="storage recovery: no",
            ),
            _refusal(
                storage_recovery_reason="journal_name_invalid",
                storage_recovery_hint="not-the-canonical-hint",
            ),
        )
        for response in cases:
            with self.subTest(response=response):
                broker = _Broker([response])
                with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as caught:
                    await self._start(broker)
                self.assertEqual(caught.exception.code, "storage_recovery_required")
                self.assertIsNone(caught.exception.storage_recovery_reason)
                self.assertIsNone(caught.exception.storage_recovery_hint)
                self.assertEqual(broker.commands, ["start", "stop"])


class JointGateAssertionTest(unittest.TestCase):
    def _gate(self):
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "dev" / "storage_recovery_joint_gate.py"
        spec = importlib.util.spec_from_file_location("storage_recovery_joint_gate", path)
        if spec is None or spec.loader is None:
            self.fail("joint gate module is missing")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_fragment_remediation_and_running_status_are_rejected(self) -> None:
        gate = self._gate()
        hint = gate.canonical_remediation()
        bad = {
            "status": "running",
            "error_code": "storage_recovery_required",
            "storage_recovery_reason": "journal_name_invalid",
            "remediation": "journal entry is not spelled as the canonical single name",
            "run_id": None,
        }
        with self.assertRaises(SystemExit):
            gate._assert_public(bad, hint)
        wrong_code = {
            "status": "failed",
            "error_code": "other",
            "storage_recovery_reason": "journal_name_invalid",
            "remediation": hint,
            "run_id": None,
            "cleanup_degraded": False,
        }
        with self.assertRaises(SystemExit):
            gate._assert_public(wrong_code, hint)

    def test_storage_inventory_sees_a_change_inside_a_directory(self) -> None:
        gate = self._gate()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        nested = root / "storage_1"
        nested.mkdir()
        (nested / "types.xml").write_text("a", encoding="utf-8")
        before = gate._inventory(root)
        (nested / "types.xml").write_text("b", encoding="utf-8")
        after = gate._inventory(root)
        with self.assertRaises(SystemExit):
            gate._assert_storage(before, after)

    def test_supplied_mission_must_be_the_policy_alias(self) -> None:
        gate = self._gate()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        mission = root / "exclusive"
        other = root / "chernarus"
        mission.mkdir()
        other.mkdir()
        policy = root / "request-policy.json"
        policy.write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "mod": "DayZ_MCP",
                            "mission_aliases": {
                                "chernarus": str(other),
                                "exclusive": str(mission),
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(gate.resolve_policy_mission(mission, policy), "exclusive")
        with self.assertRaises(SystemExit):
            gate.resolve_policy_mission(root / "missing", policy)

    def _one_operation(self, *, session: str = "S", run_id: str = "R") -> list[dict[str, object]]:
        return [
            {
                "event": "lifecycle_start",
                "reason": "lease_valid",
                "decision": "allowed",
                "client": {"session": session},
            },
            {
                "event": "lifecycle_storage_recovery_required",
                "run_id": run_id,
                "reason": "journal_name_invalid",
                "decision": "rejected",
            },
            {
                "event": "lifecycle_start_outcome",
                "run_id": run_id,
                "reason": "storage_recovery_required",
                "decision": "storage_recovery_required",
                "state": "EXITED",
                "client": {"session": session},
            },
        ]

    def test_retired_count_is_not_start_or_spawn_evidence(self) -> None:
        gate = self._gate()
        one_start = self._one_operation()
        gate._assert_operation(one_start, "S")
        conflict = one_start + [
            {
                "event": "lifecycle_start_rejected",
                "reason": "launch_identity_conflict",
                "decision": "rejected",
                "client": {"session": "S"},
            }
        ]
        with self.assertRaises(SystemExit):
            gate._assert_operation(conflict, "S")
        spawned = one_start + [
            {"event": "note", "reason": "launched", "state": "RUNNING", "pid": 9}
        ]
        with self.assertRaises(SystemExit):
            gate._assert_operation(spawned, "S")
        self.assertFalse(hasattr(gate, "_assert_lifecycle"))

    def test_second_rejected_start_and_uncorrelated_runs_fail(self) -> None:
        """Round 4 F5. Rejected starts count, and run ids must match."""
        gate = self._gate()
        replayed = [
            {
                "event": "lifecycle_start",
                "reason": "lease_valid",
                "decision": "allowed",
                "client": {"session": "S"},
            },
            {
                "event": "lifecycle_storage_recovery_required",
                "run_id": "R",
                "reason": "journal_name_invalid",
                "decision": "rejected",
            },
            {
                "event": "lifecycle_start_outcome",
                "run_id": "R",
                "reason": "storage_recovery_required",
                "decision": "storage_recovery_required",
                "state": "EXITED",
                "client": {"session": "S"},
            },
            {
                "event": "lifecycle_start_rejected",
                "client": {"session": "S"},
                "reason": "invalid_start_request",
                "decision": "rejected",
            },
        ]
        with self.assertRaises(SystemExit):
            gate._assert_operation(replayed, "S")
        crossed = self._one_operation()
        crossed[2] = dict(crossed[2])
        crossed[2]["run_id"] = "OTHER"
        with self.assertRaises(SystemExit):
            gate._assert_operation(crossed, "S")
        foreign = self._one_operation(session="other-session")
        foreign[2] = dict(foreign[2])
        foreign[2]["client"] = {"session": "S"}
        with self.assertRaises(SystemExit):
            gate._assert_operation(foreign, "S")

    def test_audit_path_is_the_named_instance_root(self) -> None:
        """Round 4 F4. Isolated instance audit, not the default DayZ_MCP store."""
        gate = self._gate()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        default_audit = root / "DayZ_MCP" / "audit"
        named_audit = root / "DayZ_MCP_r2a-review" / "audit"
        default_audit.mkdir(parents=True)
        named_audit.mkdir(parents=True)
        (default_audit / "events.jsonl").write_text('{"event":"unrelated"}\n', encoding="utf-8")
        operation = self._one_operation()
        (named_audit / "events.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in operation),
            encoding="utf-8",
        )
        env = {"LOCALAPPDATA": str(root)}
        token = gate.instance_token_from_mcp_command(
            "python -m dayz_mcp.server --instance r2a-review"
        )
        self.assertEqual(token, "r2a-review")
        selected = gate.audit_events_path(token, env)
        self.assertEqual(selected, named_audit / "events.jsonl")
        self.assertNotEqual(selected, gate.audit_events_path(None, env))
        gate._assert_operation(gate._parse_audit_lines(gate._audit_lines(selected)), "S")
        with self.assertRaises(SystemExit):
            gate._assert_operation(gate._parse_audit_lines(gate._audit_lines(gate.audit_events_path(None, env))), "S")


class JointGateCallerBindingTest(unittest.TestCase):
    """gpt-6.1-sol review of the joint gate: another caller's coherent operation is not evidence for this call."""

    def test_joint_gate_rejects_another_callers_operation(self) -> None:
        case = JointGateAssertionTest()
        gate = case._gate()
        events = case._one_operation(session="FOREIGN", run_id="FOREIGN-R")
        with self.assertRaises(SystemExit):
            gate._assert_operation(events, "S")


if __name__ == "__main__":
    unittest.main()
