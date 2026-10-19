"""Item 855c: a server start that answered storage_recovery_required is not replayed, so the refusal pair reaches the caller."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
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

    def test_mission_is_inside_its_project_roots_and_no_alias_names_it(self) -> None:
        # d9c8: the build writes the mission roots into request-policy.json and the
        # aliases into worker-runtime.json only. The gate looked for aliases in
        # request-policy.json, so it always stopped, and an alias mission is shared
        # by every launch through that alias. These are the bytes the staging writes,
        # under the names it writes them to.
        import build_native_launcher
        from tests._source_snapshot import source_of
        from tests.launcher_policy_helpers import make_fixture_source

        # Every statement that names a document or its bytes, compared whole: a later
        # write over either file counts too, and the layout of a line does not
        # (Codex review of d9c8 v3, G1).
        staging = ast.parse(source_of(build_native_launcher._prepare_staging)).body[0]
        words = ("request-policy.json", "worker-runtime.json", "policy_bytes", "runtime_bytes")
        self.assertEqual(
            [
                ast.unparse(node)
                for node in ast.walk(staging)
                if isinstance(node, ast.stmt)
                and node is not staging
                and any(word in ast.unparse(node) for word in words)
            ],
            [
                "policy_bytes, runtime_bytes = _emit_policy_documents(host)",
                "(staging / 'request-policy.json').write_bytes(policy_bytes)",
                "(staging / 'worker-runtime.json').write_bytes(runtime_bytes)",
            ],
        )

        gate = self._gate()
        self.assertEqual(gate._bundle_dir(), build_native_launcher.CANONICAL_BUNDLE)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = make_fixture_source(root, mods=("OtherMod", gate.PROJECT))
        other = source["projects"][0]
        missions = Path(other["mission_roots"][0]["path"])
        foreign = root / "other-missions"
        heli = missions / "dayzOffline.heli"
        deerisle = missions / "dayzOffline.deerisle"
        made = missions / "storage-joint-gate"
        for path in (foreign / "storage-joint-gate", heli, deerisle, made):
            path.mkdir(parents=True)
        other["mission_roots"].append({"path": str(foreign), "allow_root_junction": False})
        other["mission_aliases"]["heli"] = str(heli)
        source["projects"][1]["mission_aliases"]["deerisle"] = str(deerisle)
        policy_bytes, runtime_bytes = build_native_launcher._emit_policy_documents(source)
        bundle = root / "bundle"
        bundle.mkdir()
        (bundle / "request-policy.json").write_bytes(policy_bytes)
        with self.assertRaisesRegex(SystemExit, "worker-runtime.json"):
            gate.admitted_mission(made, bundle)
        (bundle / "worker-runtime.json").write_bytes(runtime_bytes)

        self.assertEqual(gate.admitted_mission(made, bundle), str(made))
        for refused in (
            missions / "dayzOffline.chernarusplus",  # the chernarus alias of both projects
            heli,  # an alias of the other project only
            deerisle,  # an alias of DayZ_MCP only, the second project
            foreign / "storage-joint-gate",  # inside a mission root of the other project only
            root / "source",  # inside no mission root
        ):
            with self.subTest(mission=refused), self.assertRaises(SystemExit):
                gate.admitted_mission(refused, bundle)

    def test_main_passes_the_bundle_and_refuses_a_missing_mission(self) -> None:
        gate = self._gate()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        mission = Path(temporary.name)
        bundles: list[Path] = []

        async def run(_mission, _command, bundle, _audit) -> int:
            bundles.append(bundle)
            return 0

        argv = ["--run", "--mission", str(mission), "--mcp-command", "server"]
        with patch.object(gate, "_run_async", run), patch.dict(
            "os.environ", {"DAYZ_MCP_STORAGE_RECOVERY_JOINT": "1"}
        ):
            self.assertEqual(gate.main(argv), 0)
            self.assertEqual(gate.main(argv + ["--bundle", str(mission)]), 0)
            with self.assertRaises(SystemExit):
                gate.main(["--run", "--mission", str(mission / "missing"), "--mcp-command", "server"])
        self.assertEqual(bundles, [gate._bundle_dir(), mission])

    def _fake_run(
        self,
        answer: dict[str, object] | None = None,
        *,
        audited: bool = True,
        touched: bool = False,
        existing: str | None = None,
    ) -> SimpleNamespace:
        """_run_async against a fake server: what it called, and what it left behind."""
        gate = self._gate()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        run = SimpleNamespace(
            gate=gate,
            given=root / "given",
            mission=root / "admitted",
            bundle=root / "bundle",
            admitted=[],
            calls=[],
            planted=[],
        )
        for path in (run.given, run.mission):
            path.mkdir()
        if existing is not None:
            (run.mission / gate.PLANTED_NAME).write_text(existing, encoding="utf-8")
        audit = root / "events.jsonl"
        operation = self._one_operation()
        refusal = {
            "status": "failed",
            "error_code": "storage_recovery_required",
            "storage_recovery_reason": "journal_name_invalid",
            "remediation": gate.canonical_remediation(),
            "run_id": None,
            "cleanup_degraded": False,
        }

        def admit(mission: Path, path: Path, project: str) -> str:
            run.admitted.append((mission, path, project))
            return str(run.mission)

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc) -> None:
                return None

            async def initialize(self) -> None:
                return None

            async def call_tool(self, name, arguments, read_timeout_seconds=None):
                run.calls.append((name, arguments))
                payload: dict[str, object] = {}
                if name == "session_acquire_wait":
                    payload = {"lease_token": "T"}
                elif name == "session_status":
                    payload = {"owner": {"client": {"session": "S"}}}
                elif name == "dayz_test_run":
                    run.planted.append((run.mission / gate.PLANTED_NAME).is_file())
                    if audited:
                        audit.write_text(
                            "".join(json.dumps(item) + "\n" for item in operation),
                            encoding="utf-8",
                        )
                    if touched:
                        (run.mission / "types.xml").write_text("a", encoding="utf-8")
                    payload = refusal if answer is None else answer
                return SimpleNamespace(structuredContent=payload)

        @contextlib.asynccontextmanager
        async def stdio_client(_server):
            yield None, None

        with patch.object(gate, "admitted_mission", admit), patch(
            "mcp.client.stdio.stdio_client", stdio_client
        ), patch("mcp.ClientSession", lambda _read, _write: Session()), contextlib.redirect_stdout(
            io.StringIO()
        ):
            try:
                run.result = asyncio.run(gate._run_async(run.given, "server", run.bundle, audit))
            except (Exception, SystemExit) as error:
                run.result = error
        return run

    def test_run_sends_the_admitted_path_and_plants_there_for_the_call_only(self) -> None:
        # d9c8: dayz_test_run gets the path admitted_mission returns, not an alias,
        # and the journal is planted in that directory while the call runs. The
        # lease that names this caller goes first: the server refuses dayz_test_run
        # while the session holds one (Codex review of d9c8 v3, G3).
        run = self._fake_run()
        self.assertEqual(run.result, 0)
        self.assertEqual(run.admitted, [(run.given, run.bundle, run.gate.PROJECT)])
        self.assertEqual(
            run.calls,
            [
                ("session_acquire_wait", {"purpose": "storage recovery joint gate: caller identity"}),
                ("session_status", {}),
                ("session_release", {"lease_token": "T"}),
                (
                    "dayz_test_run",
                    {"project": run.gate.PROJECT, "mode": "all", "mission": str(run.mission)},
                ),
                ("session_status", {}),
            ],
        )
        self.assertEqual(run.planted, [True])
        self.assertEqual(list(run.given.iterdir()) + list(run.mission.iterdir()), [])

    def test_each_check_fails_the_run_and_the_journal_still_goes(self) -> None:
        # Codex review of d9c8 v3, G2: a server that refuses the path's authority, an
        # audit without the operation and a storage change each fail the run alone,
        # and none of them leaves the journal behind.
        authority = {"status": "failed", "error_code": "invalid_dayz_test_path_authority"}
        for case, options, message in (
            ("public", {"answer": authority}, "error_code is not storage_recovery_required"),
            ("operation", {"audited": False}, "expected exactly one lifecycle start request"),
            ("storage", {"touched": True}, "storage tree changed besides the planted journal"),
        ):
            with self.subTest(case=case):
                run = self._fake_run(**options)
                self.assertIsInstance(run.result, SystemExit)
                self.assertIn(message, str(run.result))
                self.assertEqual(run.planted, [True])
                self.assertFalse((run.mission / run.gate.PLANTED_NAME).exists())

    def test_a_journal_write_that_fails_halfway_is_removed(self) -> None:
        # Codex review of d9c8 v3, F1: the journal is written inside the try, so a
        # write that fails after making the file leaves nothing behind.
        planted = self._gate().PLANTED_NAME
        write_text = Path.write_text

        def halfway(path: Path, text: str, *args, **kwargs) -> int:
            if path.name != planted:
                return write_text(path, text, *args, **kwargs)
            write_text(path, text[:1], *args, **kwargs)
            raise OSError("disk full")

        with patch.object(Path, "write_text", halfway):
            run = self._fake_run()
        self.assertIsInstance(run.result, OSError)
        self.assertEqual(run.planted, [])
        self.assertEqual(list(run.mission.iterdir()), [])

    def test_a_journal_the_cleanup_cannot_see_is_still_removed(self) -> None:
        # Codex review of #241, W1: the cleanup asked is_file() first, and an
        # observation that fails answers False, so the run passed and left the journal.
        planted = self._gate().PLANTED_NAME
        is_file = Path.is_file

        def blind(path: Path, *args, **kwargs) -> bool:
            return False if path.name == planted else is_file(path, *args, **kwargs)

        with patch.object(Path, "is_file", blind):
            run = self._fake_run()
        self.assertEqual(run.result, 0)
        self.assertEqual(list(run.mission.iterdir()), [])

    def test_a_journal_already_there_is_named_and_kept(self) -> None:
        # Codex review of #241, W5: the refusal names the file to remove by hand.
        run = self._fake_run(existing="left by an earlier run\n")
        journal = run.mission / run.gate.PLANTED_NAME
        self.assertIsInstance(run.result, SystemExit)
        self.assertIn(str(journal), str(run.result))
        self.assertEqual(journal.read_text(encoding="utf-8"), "left by an earlier run\n")
        self.assertEqual(run.calls, [])

    def test_a_journal_exists_cannot_see_is_named_and_kept(self) -> None:
        # 127f: exists() answers False when the observation fails, and the plant then
        # wrote over the journal it had asked about. It is made exclusively now.
        planted = self._gate().PLANTED_NAME
        exists = Path.exists

        def blind(path: Path, *args, **kwargs) -> bool:
            return False if path.name == planted else exists(path, *args, **kwargs)

        with patch.object(Path, "exists", blind):
            run = self._fake_run(existing="left by an earlier run\n")
        journal = run.mission / run.gate.PLANTED_NAME
        self.assertIsInstance(run.result, SystemExit)
        self.assertIn(str(journal), str(run.result))
        self.assertEqual(journal.read_text(encoding="utf-8"), "left by an earlier run\n")
        self.assertNotIn("dayz_test_run", [name for name, _arguments in run.calls])

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
