from __future__ import annotations

import argparse
import dataclasses
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS_ROOT.parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from dayz_mcp.native_process_guard import IDENTITY_SCHEME, NativeProcessGuard
from dayz_mcp.process_lifecycle import ProcessRecord
from dayz_mcp.runtime_state import atomic_write_json


IDENTITY_FIELDS = (
    "pid",
    "creation_time_utc",
    "executable_sha256",
    "command_line_sha256",
)
FORGED_FIELDS = (
    "pid",
    "creation_time_utc",
    "executable_sha256",
    "command_line_sha256",
)


def _result_is(
    result: object,
    *,
    error: str | None,
    exit_code: int,
    pid: int | None,
    identity_complete: bool,
) -> bool:
    return (
        isinstance(result, dict)
        and result.get("error") == error
        and result.get("exit_code") == exit_code
        and result.get("terminated") is False
        and result.get("identity_scheme") == IDENTITY_SCHEME
        and result.get("identity_complete") is identity_complete
        and result.get("pid") == pid
    )


def validate_result_shape(payload: object) -> list[str]:
    """Validate the durable evidence contract without executing any process."""

    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload_not_object"]
    if payload.get("schema_version") != 1:
        errors.append("schema_version")
    if payload.get("gate") != "task7_process_guard_registered_vs_foreign":
        errors.append("gate")
    if not isinstance(payload.get("passed"), bool):
        errors.append("passed")
    steps = payload.get("steps")
    if not isinstance(steps, dict):
        return errors + ["steps"]
    children = payload.get("children")
    child_pids: dict[str, int] = {}
    if not isinstance(children, list) or len(children) != 2:
        errors.append("children")
    else:
        slots = {item.get("slot") for item in children if isinstance(item, dict)}
        if slots != {"registered", "foreign"}:
            errors.append("child_slots")
        for item in children:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("pid"), int)
                or isinstance(item.get("pid"), bool)
                or item.get("pid") <= 0
                or item.get("final_state") != "exited"
            ):
                errors.append("child_final_state")
                break
            child_pids[str(item["slot"])] = int(item["pid"])
    if (
        "registered" in child_pids
        and "foreign" in child_pids
        and child_pids["registered"] == child_pids["foreign"]
    ):
        errors.append("child_pid_alias")
    for slot in ("registered", "foreign"):
        snapshot = steps.get(f"{slot}_snapshot")
        if not (
            isinstance(snapshot, dict)
            and isinstance(snapshot.get("pid"), int)
            and not isinstance(snapshot.get("pid"), bool)
            and snapshot.get("pid") > 0
            and snapshot.get("identity_scheme") == IDENTITY_SCHEME
            and snapshot.get("identity_complete") is True
            and snapshot.get("exit_code") == 0
        ):
            errors.append(f"{slot}_snapshot")
        elif child_pids.get(slot) != snapshot.get("pid"):
            errors.append(f"{slot}_pid_consistency")
    missing = steps.get("missing_field_rejections")
    if not isinstance(missing, dict) or set(missing) != set(IDENTITY_FIELDS):
        errors.append("missing_field_rejections")
    elif not all(
        _result_is(
            value,
            error="invalid_expected_identity",
            exit_code=3,
            pid=None,
            identity_complete=False,
        )
        for value in missing.values()
    ):
        errors.append("missing_field_contract")
    forged = steps.get("forged_identity_rejections")
    if not isinstance(forged, dict) or set(forged) != set(FORGED_FIELDS):
        errors.append("forged_identity_rejections")
    elif not all(
        _result_is(
            value,
            error="process_identity_mismatch",
            exit_code=4,
            pid=child_pids.get("foreign"),
            identity_complete=True,
        )
        for value in forged.values()
    ):
        errors.append("forged_identity_contract")
    registered = steps.get("registered_termination")
    if not (
        isinstance(registered, dict)
        and registered.get("terminated") is True
        and registered.get("exit_code") == 0
        and registered.get("identity_scheme") == IDENTITY_SCHEME
        and registered.get("identity_complete") is True
    ):
        errors.append("registered_termination")
    elif registered.get("pid") != child_pids.get("registered"):
        errors.append("registered_termination_pid")
    if not _result_is(
        steps.get("terminated_identity_recheck"),
        error="process_not_found",
        exit_code=4,
        pid=child_pids.get("registered"),
        identity_complete=False,
    ):
        errors.append("terminated_identity_recheck")
    elif steps["terminated_identity_recheck"].get("pid") != child_pids.get(
        "registered"
    ):
        errors.append("terminated_identity_recheck_pid")
    if steps.get("foreign_alive_after_rejections") is not True:
        errors.append("foreign_alive_after_rejections")
    cleanup = steps.get("foreign_exact_cleanup")
    if not (
        isinstance(cleanup, dict)
        and cleanup.get("terminated") is True
        and cleanup.get("exit_code") == 0
        and cleanup.get("identity_scheme") == IDENTITY_SCHEME
        and cleanup.get("identity_complete") is True
    ):
        errors.append("foreign_exact_cleanup")
    elif cleanup.get("pid") != child_pids.get("foreign"):
        errors.append("foreign_exact_cleanup_pid")
    if payload.get("errors") != []:
        errors.append("runtime_errors")
    return errors


def _record_from_snapshot(
    guard: NativeProcessGuard, pid: int, role: str
) -> tuple[ProcessRecord, dict[str, object]]:
    result = guard.snapshot(pid)
    if (
        result.get("exit_code") != 0
        or result.get("identity_complete") is not True
        or result.get("identity_scheme") != IDENTITY_SCHEME
    ):
        raise RuntimeError(f"snapshot_failed:{role}:{result.get('error', 'unknown')}")
    value = dict(result)
    value["role"] = role
    return ProcessRecord.from_payload(value), result


def _forged_record(record: ProcessRecord, field: str) -> ProcessRecord:
    if field == "creation_time_utc":
        return dataclasses.replace(record, creation_time_utc="forged:" + record.creation_time_utc)
    current = getattr(record, field)
    replacement = ("1" if current[0] != "1" else "0") + current[1:]
    return dataclasses.replace(record, **{field: replacement})


def _public_result(result: dict[str, object]) -> dict[str, object]:
    allowed = {
        "terminated",
        "error",
        "exit_code",
        "pid",
        "identity_complete",
        "identity_scheme",
    }
    return {key: value for key, value in result.items() if key in allowed}


def run_gate(output_path: Path) -> dict[str, object]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "gate": "task7_process_guard_registered_vs_foreign",
        "platform": os.name,
        "passed": False,
        "steps": {},
        "children": [],
        "errors": [],
    }
    children: dict[str, subprocess.Popen[bytes]] = {}
    records: dict[str, ProcessRecord] = {}
    guard = NativeProcessGuard()
    command = [
        sys.executable,
        "-c",
        "import time; time.sleep(300)",
        "task7-process-guard-gate",
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    try:
        if os.name != "nt":
            raise RuntimeError("windows_required")
        for slot in ("registered", "foreign"):
            children[slot] = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                close_fds=True,
            )
            records[slot], snapshot = _record_from_snapshot(
                guard, children[slot].pid, f"task7_gate_{slot}"
            )
            payload["steps"][f"{slot}_snapshot"] = {
                "pid": children[slot].pid,
                "identity_scheme": snapshot.get("identity_scheme"),
                "identity_complete": snapshot.get("identity_complete"),
                "exit_code": snapshot.get("exit_code"),
            }

        foreign = records["foreign"]
        missing_results: dict[str, object] = {}
        for field in IDENTITY_FIELDS:
            incomplete = dataclasses.replace(foreign, **{field: None})
            result = guard.terminate(incomplete)
            missing_results[field] = _public_result(result)
            if not _result_is(
                result,
                error="invalid_expected_identity",
                exit_code=3,
                pid=None,
                identity_complete=False,
            ):
                raise RuntimeError(f"missing_field_contract_failed:{field}")
            if children["foreign"].poll() is not None:
                raise RuntimeError(f"foreign_terminated_by_missing_field:{field}")
        payload["steps"]["missing_field_rejections"] = missing_results

        forged_results: dict[str, object] = {}
        for field in FORGED_FIELDS:
            forged = (
                dataclasses.replace(records["registered"], pid=foreign.pid)
                if field == "pid"
                else _forged_record(foreign, field)
            )
            result = guard.terminate(forged)
            forged_results[field] = _public_result(result)
            if not _result_is(
                result,
                error="process_identity_mismatch",
                exit_code=4,
                pid=foreign.pid,
                identity_complete=True,
            ):
                raise RuntimeError(f"forged_identity_contract_failed:{field}")
            if children["foreign"].poll() is not None:
                raise RuntimeError(f"foreign_terminated_by_forgery:{field}")
            if children["registered"].poll() is not None:
                raise RuntimeError(f"registered_terminated_by_forgery:{field}")
        payload["steps"]["forged_identity_rejections"] = forged_results
        payload["steps"]["foreign_alive_after_rejections"] = True

        registered_result = guard.terminate(records["registered"])
        payload["steps"]["registered_termination"] = _public_result(registered_result)
        if registered_result.get("terminated") is not True or registered_result.get("exit_code") != 0:
            raise RuntimeError("registered_exact_termination_failed")
        children["registered"].wait(timeout=5.0)

        not_found = guard.terminate(records["registered"])
        payload["steps"]["terminated_identity_recheck"] = _public_result(not_found)
        if not _result_is(
            not_found,
            error="process_not_found",
            exit_code=4,
            pid=records["registered"].pid,
            identity_complete=False,
        ):
            raise RuntimeError("process_not_found_contract_failed")

        foreign_cleanup = guard.terminate(foreign)
        payload["steps"]["foreign_exact_cleanup"] = _public_result(foreign_cleanup)
        if foreign_cleanup.get("terminated") is not True or foreign_cleanup.get("exit_code") != 0:
            raise RuntimeError("foreign_exact_cleanup_failed")
        children["foreign"].wait(timeout=5.0)
    except Exception as exc:
        payload["errors"].append(str(exc))
    finally:
        for slot, child in children.items():
            if child.poll() is None:
                record = records.get(slot)
                if record is None:
                    try:
                        record, _ = _record_from_snapshot(guard, child.pid, f"task7_gate_{slot}")
                        records[slot] = record
                    except Exception as exc:
                        payload["errors"].append(f"cleanup_snapshot_failed:{slot}:{exc}")
                if record is not None:
                    cleanup = guard.terminate(record)
                    if cleanup.get("terminated") is not True:
                        payload["errors"].append(
                            f"cleanup_termination_failed:{slot}:{cleanup.get('error', 'unknown')}"
                        )
                    else:
                        try:
                            child.wait(timeout=5.0)
                        except subprocess.TimeoutExpired:
                            payload["errors"].append(f"cleanup_wait_failed:{slot}")
            payload["children"].append(
                {
                    "slot": slot,
                    "pid": child.pid,
                    "final_state": "exited" if child.poll() is not None else "alive",
                }
            )

        payload["passed"] = not validate_result_shape({**payload, "passed": True})
        if not payload["passed"] and not payload["errors"]:
            payload["errors"].append("result_contract_failed")
        atomic_write_json(output_path.resolve(), payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Opt-in native Task 7 guard safety gate; launches only two owned Python children."
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Explicitly authorize launching and exactly terminating the two gate children.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / ".superpowers" / "sdd" / "task-7-process-guard-gate.json",
    )
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("gate_disabled_without_--run")
    result = run_gate(args.output)
    print(f"gate={'PASS' if result['passed'] else 'FAIL'} output={args.output.resolve()}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
