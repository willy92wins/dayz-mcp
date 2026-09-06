"""Distributed H8 gate: one real Codex task owns exactly one MCP proxy.

Roles A/B/C/D are launched by four separate Codex tasks. Coordination files are
redacted and contain no key, lease token, full session id, or identity JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from e2e_agent_sessions import StdioProxy, _listener_pids
from h8_real_codex_gate import (
    ACTIVE_RUN_STATES,
    EXPECTED_AUDIT_EVENTS,
    FIXTURE_LAUNCHER,
    GAME_PORT,
    KEYFILE,
    PORT,
    ROOT,
    TOOLS,
    GateFailure,
    _audit_documents,
    _audit_summary,
    _clean_status,
    _close_proxy,
    _contains_value,
    _doctor,
    _forbidden_key,
    _identity_from,
    _lease_context,
    _lifecycle_call,
    _manifest_shape,
    _mutate,
    _process_counts,
    _proxy_argv,
    _require,
    _start_fixture,
    _tool_result,
    _udp_owners,
    _utc_now,
    _wait_bridge_ready,
    _wait_process_counts_zero,
)


RESULT_PATH = ROOT / "reviews" / "2026-07-16-h8-real-4-codex.json"
ROLE_OFFSETS_S = {"B": 0.0, "C": 1.0, "D": 2.0}
EXPECTED_POSITIONS = {"B": 1, "C": 2, "D": 3}
TTL_MIN_S = 119.0
TTL_MAX_S = 126.0
ROLE_SECRET_KEYS = {
    "api_key",
    "client_identity_json",
    "key",
    "keyfile",
    "lease_token",
    "password",
    "token",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--role",
        choices=("A", "B", "C", "D", "FINALIZE", "CLEANUP"),
        required=True,
    )
    parser.add_argument("--gate-dir", type=Path, required=True)
    parser.add_argument("--task-path", default="")
    return parser


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temp, path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"bad_json:{path.name}")
    return value


def _load_roster(
    gate_dir: Path, timeout_s: float = 90.0
) -> tuple[dict[str, Any], str]:
    path = gate_dir / "roster.json"
    roster = _wait_json(path, timeout_s)
    _require(
        roster.get("schema") == "dayz-mcp-h8-task-roster-v1",
        "roster_schema_invalid",
    )
    _require(
        roster.get("source") == "collaboration.spawn_agent",
        "roster_source_invalid",
    )
    nonce = roster.get("nonce")
    roles = roster.get("roles")
    _require(isinstance(nonce, str) and bool(nonce), "roster_nonce_invalid")
    _require(isinstance(roles, dict) and set(roles) == set("ABCD"), "roster_roles_invalid")
    paths: list[str] = []
    task_ids: list[str] = []
    for role in "ABCD":
        entry = roles.get(role)
        _require(isinstance(entry, dict), f"roster_{role}_invalid")
        task_path = entry.get("task_path")
        task_id = entry.get("task_id")
        _require(
            isinstance(task_path, str) and task_path.startswith("/root"),
            f"roster_{role}_task_path_invalid",
        )
        _require(
            isinstance(task_id, str) and bool(task_id),
            f"roster_{role}_task_id_invalid",
        )
        paths.append(task_path)
        task_ids.append(task_id)
    _require(len(set(paths)) == 4, "roster_task_paths_not_unique")
    _require(len(set(task_ids)) == 4, "roster_task_ids_not_unique")
    return roster, hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _task_from_roster(
    gate_dir: Path, role: str, task_path: str
) -> dict[str, Any]:
    _require(role in "ABCD", "roster_role_invalid")
    _require(bool(task_path), f"{role}_task_path_missing")
    roster, roster_sha256 = _load_roster(gate_dir)
    entry = roster["roles"][role]
    _require(entry["task_path"] == task_path, f"{role}_task_path_not_rostered")
    return {
        "task_path": entry["task_path"],
        "task_id": entry["task_id"],
        "roster_source": roster["source"],
        "roster_nonce": roster["nonce"],
        "roster_sha256": roster_sha256,
    }


def _wait_json(path: Path, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.is_file():
            return _read_json(path)
        time.sleep(0.1)
    raise GateFailure(f"wait_file_timeout:{path.name}")


def _wait_until(when: datetime) -> None:
    while True:
        remaining = (when - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.25))


def _wait_ready_with_heartbeat(
    gate_dir: Path,
    proxy: StdioProxy,
    lease_token: str,
    timeout_s: float = 90.0,
) -> None:
    pending = {"B", "C", "D"}
    deadline = time.monotonic() + timeout_s
    next_heartbeat = time.monotonic() + 20.0
    while pending and time.monotonic() < deadline:
        pending = {
            role for role in pending if not (gate_dir / f"ready-{role}.json").is_file()
        }
        if pending and time.monotonic() >= next_heartbeat:
            heartbeat = _tool_result(
                proxy,
                "session_heartbeat",
                {"lease_token": lease_token},
            )
            _require(heartbeat.get("status") == "active", "A_ready_heartbeat_failed")
            next_heartbeat = time.monotonic() + 20.0
        time.sleep(0.1)
    _require(not pending, f"ready_roles_missing:{sorted(pending)}")


def _identity_public(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_prefix": identity["session_prefix"],
        "session_sha256_12": identity["session_sha256_12"],
        "pid": identity["pid"],
        "proxy_wrapper_pid": identity["proxy_wrapper_pid"],
        "started_at_utc": identity["started_at_utc"],
        "platform": identity["platform"],
        "task_label": identity["task_label"],
    }


def _status_evidence(status: dict[str, Any]) -> dict[str, Any]:
    self_state = status.get("self")
    state = self_state.get("state") if isinstance(self_state, dict) else None
    owner = status.get("owner")
    public_owner = None
    if isinstance(owner, dict):
        public_owner = {
            "state": owner.get("state"),
            "purpose": owner.get("purpose"),
            "client": owner.get("client"),
            "expires_in_s": owner.get("expires_in_s"),
        }
    public_queue = []
    for item in status.get("queue", []):
        if isinstance(item, dict):
            public_queue.append(
                {
                    "purpose": item.get("purpose"),
                    "position": item.get("position"),
                    "client": item.get("client"),
                    "age_s": item.get("age_s"),
                }
            )
    public_self = {
        "state": state,
        "position": self_state.get("position") if isinstance(self_state, dict) else None,
    }
    return {
        "owner": public_owner,
        "queue": public_queue,
        "self": public_self,
        "cleanup_degraded": status.get("cleanup_degraded"),
        "daemon_generation": status.get("daemon_generation"),
        "pending_commands": status.get("pending_commands"),
        "own_lease": "present" if state in {"active", "releasing"} else "none",
        "own_ticket": "present" if state == "queued" else "none",
    }


def _expiry_audit_evidence(
    events: list[dict[str, Any]],
    session_prefix: str,
    heartbeat_finished_at_utc: str,
) -> dict[str, Any]:
    matching = [
        event
        for event in events
        if isinstance(event.get("client"), dict)
        and event["client"].get("session") == session_prefix
    ]
    expired = [event for event in matching if event.get("event") == "session_expired"]
    release_started = [
        event
        for event in matching
        if event.get("event") == "session_release_started"
    ]
    release_finished = [
        event
        for event in matching
        if event.get("event") == "session_release_finished"
    ]
    _require(len(expired) == 1, f"C_expiry_events:{len(expired)}")
    _require(
        expired[0].get("reason") == "lease_ttl",
        f"C_expiry_reason:{expired[0].get('reason')}",
    )
    _require(
        not release_started,
        f"C_release_started_events:{len(release_started)}",
    )
    _require(
        len(release_finished) == 1
        and release_finished[0].get("reason") == "lease_ttl",
        f"C_expiry_cleanup_finished_events:{len(release_finished)}",
    )
    expired_at = expired[0].get("timestamp_utc")
    _require(isinstance(expired_at, str), "C_expiry_timestamp_missing")
    elapsed = (
        _parse_time(expired_at) - _parse_time(heartbeat_finished_at_utc)
    ).total_seconds()
    _require(TTL_MIN_S <= elapsed <= TTL_MAX_S, f"C_audit_ttl_elapsed:{elapsed}")
    return {
        "session_prefix": session_prefix,
        "expired_events": 1,
        "release_started_events": 0,
        "expiry_cleanup_finished_events": 1,
        "expired_at_utc": expired_at,
        "heartbeat_to_expiry_s": round(elapsed, 3),
    }


def _distributed_context(
    acquired: dict[str, Any],
    identity: dict[str, Any],
    daemon_generation: str,
) -> dict[str, Any]:
    context = _lease_context(acquired, identity)
    _require(
        isinstance(daemon_generation, str) and bool(daemon_generation),
        "lease_daemon_generation_missing",
    )
    response_generation = acquired.get("daemon_generation")
    _require(
        response_generation is None or response_generation == daemon_generation,
        "lease_daemon_generation_changed",
    )
    context["daemon_generation"] = daemon_generation
    return context


def _delete_failure_stderr(path: Path) -> None:
    path.unlink(missing_ok=True)
    _require(not path.exists(), f"failure_stderr_not_deleted:{path.name}")


def _scan_and_delete_stderr(path: Path, needles: list[str]) -> dict[str, int]:
    text = (
        path.read_text(encoding="utf-8", errors="replace")
        if path.is_file()
        else ""
    )
    found = sum(1 for needle in needles if needle and needle in text)
    _delete_failure_stderr(path)
    _require(found == 0, f"secret_in_deleted_stderr:{found}")
    return {"values_found": 0}


def _has_forbidden_role_key(value: object) -> bool:
    if isinstance(value, dict):
        if any(str(key).casefold() in ROLE_SECRET_KEYS for key in value):
            return True
        return any(_has_forbidden_role_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_forbidden_role_key(item) for item in value)
    return False


def _all_audit_documents(raw_texts: list[str]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for text in raw_texts:
        for line in text.splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                documents.append(value)
    return documents


def _write_role(
    gate_dir: Path,
    role: str,
    artifact: dict[str, Any],
    *,
    identity: dict[str, Any],
    lease_token: str,
    stderr_path: Path,
) -> None:
    artifact["overall_pass"] = True
    wire = json.dumps(artifact, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.is_file() else ""
    key = KEYFILE.read_text(encoding="utf-8").strip()
    needles = [key, lease_token, identity["session_id"], identity["json"]]
    _require(not any(needle and needle in wire for needle in needles), f"{role}_secret_in_result")
    _require(not any(needle and needle in stderr for needle in needles), f"{role}_secret_in_stderr")
    _require(not _has_forbidden_role_key(artifact), f"{role}_forbidden_secret_key")
    artifact["secret_scan"] = {
        "result_values_found": 0,
        "stderr_values_found": 0,
        "forbidden_secret_keys_found": 0,
    }
    _atomic_json(gate_dir / f"role-{role}.json", artifact)


def _register_run(
    gate_dir: Path,
    label: str,
    run_id: str,
    context: dict[str, Any],
) -> None:
    _require(label in {"R0", "R1"}, "registered_run_label_invalid")
    _require(isinstance(run_id, str) and bool(run_id), "registered_run_id_invalid")
    roster, roster_sha256 = _load_roster(gate_dir, timeout_s=10.0)
    identity = json.loads(context["identity_json"])
    _atomic_json(
        gate_dir / f"registered-{label}.json",
        {
            "schema": "dayz-mcp-h8-registered-run-v2",
            "label": label,
            "run_id": run_id,
            "gate_nonce": roster["nonce"],
            "roster_sha256": roster_sha256,
            "daemon_generation": context["daemon_generation"],
            "owner_session_sha256_12": hashlib.sha256(
                identity["session_id"].encode("utf-8")
            ).hexdigest()[:12],
            "owner_lease_sha256_12": hashlib.sha256(
                context["lease_id"].encode("utf-8")
            ).hexdigest()[:12],
        },
    )


def _registered_runs(
    gate_dir: Path,
    context: dict[str, Any],
) -> list[dict[str, str]]:
    roster, roster_sha256 = _load_roster(gate_dir, timeout_s=10.0)
    runs: list[dict[str, str]] = []
    for path in sorted(gate_dir.glob("registered-*.json")):
        value = _read_json(path)
        label = value.get("label")
        run_id = value.get("run_id")
        _require(
            value.get("schema") == "dayz-mcp-h8-registered-run-v2"
            and label in {"R0", "R1"}
            and isinstance(run_id, str)
            and bool(run_id),
            f"registered_run_invalid:{path.name}",
        )
        _require(
            value.get("gate_nonce") == roster["nonce"]
            and value.get("roster_sha256") == roster_sha256
            and value.get("daemon_generation") == context["daemon_generation"],
            f"registered_run_stale:{path.name}",
        )
        for field in ("owner_session_sha256_12", "owner_lease_sha256_12"):
            digest = value.get(field)
            _require(
                isinstance(digest, str)
                and len(digest) == 12
                and all(char in "0123456789abcdef" for char in digest.casefold()),
                f"registered_run_digest_invalid:{path.name}:{field}",
            )
        runs.append({"label": label, "run_id": run_id})
    _require(
        len({item["run_id"] for item in runs}) == len(runs),
        "registered_run_ids_not_unique",
    )
    return runs


def _cleanup_registered_runs(
    context: dict[str, Any],
    gate_dir: Path,
) -> dict[str, Any]:
    registered = _registered_runs(gate_dir, context)
    identity = json.loads(context["identity_json"])
    session_id = identity["session_id"]
    status_code, status = _lifecycle_call(context, "status", temp_dir=gate_dir)
    _require(status_code == 0, "cleanup_lifecycle_status_failed")
    manifest_runs = [
        item for item in status.get("runs", []) if isinstance(item, dict)
    ]
    targets: list[dict[str, str]] = [
        {
            "label": item["label"],
            "run_id": item["run_id"],
            "discovery": "marker",
        }
        for item in registered
    ]
    marked_ids = {item["run_id"] for item in registered}
    owner_discovered = 0
    for run in manifest_runs:
        run_id = run.get("run_id")
        if (
            isinstance(run_id, str)
            and run_id not in marked_ids
            and run.get("owner_session_id") == session_id
            and run.get("owner_lease_id") == context["lease_id"]
        ):
            _require(
                run.get("mod") == "@MilitaryBunker",
                f"cleanup_owned_run_wrong_mod:{run_id}",
            )
            targets.append(
                {
                    "label": "UNREGISTERED",
                    "run_id": run_id,
                    "discovery": "owner_manifest",
                }
            )
            owner_discovered += 1
    actions: list[dict[str, Any]] = []
    for target in targets:
        run_id = target["run_id"]
        run = next(
            (
                item
                for item in manifest_runs
                if isinstance(item, dict) and item.get("run_id") == run_id
            ),
            None,
        )
        _require(isinstance(run, dict), f"cleanup_run_missing:{run_id}")
        _require(run.get("mod") == "@MilitaryBunker", f"cleanup_run_wrong_mod:{run_id}")
        state = run.get("state")
        if state == "EXITED":
            actions.append(
                {
                    "label": target["label"],
                    "run_id": run_id,
                    "discovery": target["discovery"],
                    "initial_state": state,
                    "final_state": "EXITED",
                }
            )
            continue
        if state == "RUNNING_IDLE":
            adopt_code, adopted = _lifecycle_call(
                context,
                "adopt",
                run_id=run_id,
                temp_dir=gate_dir,
            )
            _require(
                adopt_code == 0 and adopted.get("state") == "RUNNING",
                f"cleanup_adopt_failed:{run_id}",
            )
        else:
            _require(
                state in {"STARTING", "RUNNING"}
                and run.get("owner_session_id") == session_id,
                f"cleanup_run_not_owned:{run_id}:{state}",
            )
        stop_code, stopped = _lifecycle_call(
            context,
            "stop",
            run_id=run_id,
            temp_dir=gate_dir,
        )
        _require(
            stop_code == 0 and stopped.get("state") == "EXITED",
            f"cleanup_stop_failed:{run_id}",
        )
        actions.append(
            {
                "label": target["label"],
                "run_id": run_id,
                "discovery": target["discovery"],
                "initial_state": state,
                "final_state": stopped.get("state"),
            }
        )
        status_code, status = _lifecycle_call(context, "status", temp_dir=gate_dir)
        _require(status_code == 0, "cleanup_lifecycle_refresh_failed")
        manifest_runs = [
            item for item in status.get("runs", []) if isinstance(item, dict)
        ]
    return {
        "registered": len(registered),
        "owner_discovered": owner_discovered,
        "actions": actions,
    }


def _read_timing(proxy: StdioProxy, target: datetime) -> dict[str, Any]:
    _wait_until(target)
    started = _utc_now()
    status = _tool_result(proxy, "bridge_status", {})
    finished = _utc_now()
    _require(status.get("daemon_generation"), "read_generation_missing")
    version = status.get("version_state")
    _require(
        isinstance(version, dict)
        and version.get("server") == "ok"
        and version.get("client") == "ok",
        "read_peers_not_ready",
    )
    return {
        "started_at_utc": started,
        "finished_at_utc": finished,
        "daemon_generation": status["daemon_generation"],
        "server": version["server"],
        "client": version["client"],
    }


def _wait_for_active(
    proxy: StdioProxy, ticket: str, *, hard_limit_s: float = 180.0
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    deadline = time.monotonic() + hard_limit_s
    slices: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        started = time.monotonic()
        started_utc = _utc_now()
        result = _tool_result(
            proxy,
            "session_wait",
            {"ticket": ticket, "timeout_s": 30.0},
            timeout=45.0,
        )
        slices.append(
            {
                "started_at_utc": started_utc,
                "finished_at_utc": _utc_now(),
                "duration_s": round(time.monotonic() - started, 3),
                "status": result.get("status"),
                "position": result.get("position"),
            }
        )
        if result.get("status") == "active":
            return result, slices
        _require(result.get("status") == "queued", "wait_bad_status")
    raise GateFailure("wait_active_timeout")


def _kill_own_proxy(identity: dict[str, Any]) -> dict[str, Any]:
    pid = int(identity["pid"])
    wrapper = int(identity["proxy_wrapper_pid"])
    expected = identity["started_at_utc"]
    script = (
        "$p=Get-Process -Id "
        + str(pid)
        + " -ErrorAction Stop;"
        + "$c=Get-CimInstance Win32_Process -Filter 'ProcessId="
        + str(pid)
        + "' -ErrorAction Stop;"
        + "$expected=[DateTimeOffset]::Parse('"
        + expected
        + "').UtcDateTime;"
        + "$delta=[Math]::Abs(($p.StartTime.ToUniversalTime()-$expected).TotalSeconds);"
        + "if($c.ParentProcessId -ne "
        + str(wrapper)
        + " -or $delta -gt 2){throw 'proxy_identity_mismatch'};"
        + "$killed=(Get-Date).ToUniversalTime().ToString('o');"
        + "Stop-Process -Id "
        + str(pid)
        + " -Force -ErrorAction Stop;"
        + "Start-Sleep -Milliseconds 200;"
        + "[pscustomobject]@{pid="
        + str(pid)
        + ";wrapper_pid="
        + str(wrapper)
        + ";precheck_match=$true;start_delta_s=$delta;killed_at_utc=$killed;"
        + "process_absent=(-not(Get-Process -Id "
        + str(pid)
        + " -ErrorAction SilentlyContinue))}|ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    _require(completed.returncode == 0, "proxy_kill_failed")
    result = json.loads(completed.stdout.strip())
    _require(result.get("process_absent") is True, "proxy_still_alive")
    return result


def _new_proxy(role: str, gate_dir: Path) -> tuple[StdioProxy, Path, datetime]:
    stderr_path = gate_dir / f"role-{role}.stderr.log"
    gate_started = datetime.now(timezone.utc)
    proxy = StdioProxy(
        _proxy_argv(f"h8-task-{role}"),
        TOOLS,
        stderr_path,
    )
    try:
        proxy.initialize()
    except Exception:
        try:
            if proxy.process.poll() is None:
                _close_proxy(proxy)
        finally:
            _delete_failure_stderr(stderr_path)
        raise
    return proxy, stderr_path, gate_started


def _cleanup_gate(gate_dir: Path) -> dict[str, Any]:
    proxy, stderr_path, gate_started = _new_proxy("cleanup", gate_dir)
    context: dict[str, Any] | None = None
    identity: dict[str, Any] | None = None
    try:
        acquired = _tool_result(
            proxy,
            "session_acquire",
            {"purpose": "H8 distributed failure cleanup"},
        )
        identity = _identity_from(acquired, proxy.process.pid, gate_started)
        if acquired.get("status") == "queued":
            active, _ = _wait_for_active(proxy, acquired["ticket"], hard_limit_s=420.0)
        else:
            _require(acquired.get("status") == "active", "cleanup_not_active")
            active = acquired
        context = _lease_context(active, identity)
        observed_status = _tool_result(proxy, "session_status", {})
        observed_generation = observed_status.get("daemon_generation")
        _require(
            observed_status.get("self", {}).get("state") == "active",
            "cleanup_self_not_active",
        )
        context = _distributed_context(
            active,
            identity,
            observed_generation,
        )
        cleanup = _cleanup_registered_runs(context, gate_dir)
        released = _tool_result(
            proxy,
            "session_release",
            {"lease_token": context["lease_token"]},
        )
        _require(
            released.get("released") is True
            and released.get("cleanup_degraded") == [],
            "cleanup_release_failed",
        )
        final_status = _tool_result(proxy, "session_status", {})
        _require(_clean_status(final_status), "cleanup_final_status_not_clean")
        _close_proxy(proxy)
        zero_counts = _wait_process_counts_zero()
        _require(_udp_owners(GAME_PORT) == [], "cleanup_udp2302_remains")
        doctor = _doctor()
        _require(
            doctor == {"exit_code": 0, "ok": True, "finding_codes": []},
            "cleanup_doctor_not_clean",
        )
        result = {
            "overall_pass": True,
            "cleanup": cleanup,
            "final_status": _status_evidence(final_status),
            "process_counts": zero_counts,
            "udp_2302_owners": [],
            "doctor": doctor,
            "finished_at_utc": _utc_now(),
        }
        key = KEYFILE.read_text(encoding="utf-8").strip()
        wire = json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        _require(key not in wire, "cleanup_key_in_result")
        _require(
            context["lease_token"] not in wire
            and identity["session_id"] not in wire
            and identity["json"] not in wire,
            "cleanup_secret_in_result",
        )
        stderr_scan = _scan_and_delete_stderr(
            stderr_path,
            [
                key,
                context["lease_token"],
                identity["session_id"],
                identity["json"],
            ],
        )
        result["secret_scan"] = {
            "result_values_found": 0,
            "stderr_values_found": stderr_scan["values_found"],
        }
        _atomic_json(gate_dir / "cleanup.json", result)
        return result
    except Exception as exc:
        cleanup_errors: list[str] = []
        if context is not None:
            try:
                _tool_result(
                    proxy,
                    "session_release",
                    {"lease_token": context["lease_token"]},
                )
            except Exception as release_exc:
                cleanup_errors.append(
                    f"release:{type(release_exc).__name__}:{release_exc}"
                )
        try:
            if proxy.process.poll() is None:
                _close_proxy(proxy)
        except Exception as close_exc:
            cleanup_errors.append(f"proxy:{type(close_exc).__name__}:{close_exc}")
        try:
            _delete_failure_stderr(stderr_path)
        except Exception as stderr_exc:
            cleanup_errors.append(
                f"stderr:{type(stderr_exc).__name__}:{stderr_exc}"
            )
        if cleanup_errors:
            raise GateFailure(
                "cleanup_gate_failed:"
                f"{type(exc).__name__}:{exc};"
                f"cleanup_failed:{'|'.join(cleanup_errors)}"
            ) from exc
        raise


def _run_a(gate_dir: Path, task_path: str) -> dict[str, Any]:
    task = _task_from_roster(gate_dir, "A", task_path)
    proxy, stderr_path, gate_started = _new_proxy("A", gate_dir)
    context: dict[str, Any] | None = None
    identity: dict[str, Any] | None = None
    run0: str | None = None
    try:
        initial_status = _tool_result(proxy, "session_status", {})
        _require(_clean_status(initial_status), "A_initial_status_not_clean")
        generation = initial_status.get("daemon_generation")
        _require(
            isinstance(generation, str) and bool(generation),
            "A_initial_generation_missing",
        )
        acquired = _tool_result(proxy, "session_acquire", {"purpose": "H8 distributed owner A"})
        _require(acquired.get("status") == "active", "A_not_active")
        identity = _identity_from(acquired, proxy.process.pid, gate_started)
        context = _lease_context(acquired, identity)
        context = _distributed_context(acquired, identity, generation)
        run0, launch0 = _start_fixture(
            proxy, context, temp_dir=gate_dir, tag="R0-distributed"
        )
        _register_run(gate_dir, "R0", run0, context)
        ready0 = _wait_bridge_ready(proxy)
        _require(
            ready0["daemon_generation"] == generation,
            "A_context_generation_changed",
        )
        listener = _listener_pids(PORT)
        _require(len(listener) == 1, "A_listener_not_one")
        _require(_process_counts().get("DayZDiag_x64") == 2, "A_fixture_not_two_diag")

        _wait_ready_with_heartbeat(
            gate_dir,
            proxy,
            context["lease_token"],
        )
        heartbeat = _tool_result(
            proxy,
            "session_heartbeat",
            {"lease_token": context["lease_token"]},
        )
        _require(heartbeat.get("status") == "active", "A_schedule_heartbeat_failed")
        now = datetime.now(timezone.utc)
        control = {
            "schema": "dayz-mcp-h8-distributed-v1",
            "gate_started_at_utc": gate_started.isoformat().replace("+00:00", "Z"),
            "read_at_utc": (now + timedelta(seconds=6)).isoformat().replace("+00:00", "Z"),
            "acquire_at_utc": (now + timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
            "run0": run0,
            "daemon_generation": generation,
            "listener_pid": next(iter(listener)),
        }
        _atomic_json(gate_dir / "control.json", control)

        read = _read_timing(proxy, _parse_time(control["read_at_utc"]))
        _wait_until(_parse_time(control["acquire_at_utc"]) + timedelta(seconds=4.0))
        queue_status = _tool_result(proxy, "session_status", {})
        positions = [item.get("position") for item in queue_status.get("queue", [])]
        _require(positions == [1, 2, 3], f"A_queue_positions:{positions}")
        mutation = _mutate(proxy, "A")
        released = _tool_result(
            proxy,
            "session_release",
            {"lease_token": context["lease_token"]},
        )
        _require(
            released.get("released") is True
            and released.get("cleanup_degraded") == [],
            "A_release_failed",
        )
        final_status = _tool_result(proxy, "session_status", {})
        _require(final_status.get("self", {}).get("state") == "none", "A_self_not_none")
        artifact = {
            "role": "A",
            **task,
            "runner_pid": os.getpid(),
            "identity": _identity_public(identity),
            "daemon_generation": generation,
            "run0": run0,
            "launch": launch0,
            "bridge_ready": ready0,
            "parallel_read": read,
            "queue_snapshot": {
                "positions": positions,
                "sessions": [
                    item.get("client", {}).get("session")
                    for item in queue_status.get("queue", [])
                ],
            },
            "mutation": mutation,
            "release": {
                "released": released.get("released"),
                "cleanup_degraded": released.get("cleanup_degraded"),
                "runs_released": released.get("cleanup", {}).get("runs_released"),
            },
            "final_status": _status_evidence(final_status),
            "finished_at_utc": _utc_now(),
        }
        _write_role(
            gate_dir,
            "A",
            artifact,
            identity=identity,
            lease_token=context["lease_token"],
            stderr_path=stderr_path,
        )
        _close_proxy(proxy)
        return artifact
    except Exception as exc:
        cleanup_errors: list[str] = []
        if context is not None:
            try:
                _cleanup_registered_runs(context, gate_dir)
            except Exception as cleanup_exc:
                cleanup_errors.append(
                    f"runs:{type(cleanup_exc).__name__}:{cleanup_exc}"
                )
            try:
                _tool_result(
                    proxy,
                    "session_release",
                    {"lease_token": context["lease_token"]},
                )
            except Exception as release_exc:
                cleanup_errors.append(
                    f"release:{type(release_exc).__name__}:{release_exc}"
                )
        try:
            _close_proxy(proxy)
        except Exception as close_exc:
            cleanup_errors.append(f"proxy:{type(close_exc).__name__}:{close_exc}")
        try:
            _delete_failure_stderr(stderr_path)
        except Exception as stderr_exc:
            cleanup_errors.append(
                f"stderr:{type(stderr_exc).__name__}:{stderr_exc}"
            )
        if cleanup_errors:
            raise GateFailure(
                f"A_failed:{type(exc).__name__}:{exc};cleanup_failed:{'|'.join(cleanup_errors)}"
            ) from exc
        raise


def _run_queued(role: str, gate_dir: Path, task_path: str) -> dict[str, Any]:
    task = _task_from_roster(gate_dir, role, task_path)
    proxy, stderr_path, gate_started = _new_proxy(role, gate_dir)
    identity: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    crash_committed = False
    _atomic_json(
        gate_dir / f"ready-{role}.json",
        {
            "role": role,
            "task_path": task["task_path"],
            "task_id": task["task_id"],
            "runner_pid": os.getpid(),
        },
    )
    try:
        control = _wait_json(gate_dir / "control.json", 180.0)
        read = _read_timing(proxy, _parse_time(control["read_at_utc"]))
        _wait_until(
            _parse_time(control["acquire_at_utc"])
            + timedelta(seconds=ROLE_OFFSETS_S[role])
        )
        acquired_at = _utc_now()
        acquired = _tool_result(
            proxy,
            "session_acquire",
            {"purpose": f"H8 distributed FIFO {role}"},
        )
        identity = _identity_from(acquired, proxy.process.pid, gate_started)
        _require(acquired.get("status") == "queued", f"{role}_not_queued")
        _require(
            acquired.get("position") == EXPECTED_POSITIONS[role],
            f"{role}_wrong_position:{acquired.get('position')}",
        )
        active, wait_slices = _wait_for_active(proxy, acquired["ticket"])
        context = _lease_context(active, identity)
        context = _distributed_context(
            active,
            identity,
            read["daemon_generation"],
        )
        _require(
            context["daemon_generation"] == control["daemon_generation"],
            f"{role}_context_generation_changed",
        )
        active_at = _utc_now()

        if role == "B":
            mutation = _mutate(proxy, role)
            released = _tool_result(
                proxy,
                "session_release",
                {"lease_token": context["lease_token"]},
            )
            _require(
                released.get("released") is True
                and released.get("cleanup_degraded") == [],
                "B_release_failed",
            )
            final_status = _tool_result(proxy, "session_status", {})
            _require(final_status.get("self", {}).get("state") == "none", "B_self_not_none")
            artifact = {
                "role": role,
                **task,
                "runner_pid": os.getpid(),
                "identity": _identity_public(identity),
                "daemon_generation": read["daemon_generation"],
                "parallel_read": read,
                "acquire": {
                    "at_utc": acquired_at,
                    "status": "queued",
                    "position": acquired["position"],
                },
                "wait_slices": wait_slices,
                "active_at_utc": active_at,
                "mutation": mutation,
                "release": {
                    "released": released.get("released"),
                    "cleanup_degraded": released.get("cleanup_degraded"),
                },
                "final_status": _status_evidence(final_status),
                "finished_at_utc": _utc_now(),
            }
            _write_role(
                gate_dir,
                role,
                artifact,
                identity=identity,
                lease_token=context["lease_token"],
                stderr_path=stderr_path,
            )
            _close_proxy(proxy)
            return artifact

        if role == "C":
            run0 = control["run0"]
            adopt_code, adopted = _lifecycle_call(
                context,
                "adopt",
                run_id=run0,
                temp_dir=gate_dir,
            )
            _require(
                adopt_code == 0 and adopted.get("state") == "RUNNING",
                "C_adopt_failed",
            )
            mutation = _mutate(proxy, role)
            heartbeat_started = _utc_now()
            heartbeat = _tool_result(
                proxy,
                "session_heartbeat",
                {"lease_token": context["lease_token"]},
            )
            heartbeat_finished = _utc_now()
            _require(
                heartbeat.get("status") == "active"
                and float(heartbeat.get("expires_in_s", 0.0)) >= 119.0,
                "C_heartbeat_not_fresh",
            )
            listener_before = _listener_pids(PORT)
            counts_before = _process_counts()
            killed = _kill_own_proxy(identity)
            crash_committed = True
            try:
                proxy.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proxy.process.terminate()
                proxy.process.wait(timeout=10)
            listener_after = _listener_pids(PORT)
            counts_after = _process_counts()
            _require(listener_after == listener_before, "C_listener_changed")
            _require(
                counts_before.get("DayZDiag_x64") == 2
                and counts_after.get("DayZDiag_x64") == 2,
                "C_game_changed",
            )
            artifact = {
                "role": role,
                **task,
                "runner_pid": os.getpid(),
                "identity": _identity_public(identity),
                "daemon_generation": read["daemon_generation"],
                "parallel_read": read,
                "acquire": {
                    "at_utc": acquired_at,
                    "status": "queued",
                    "position": acquired["position"],
                },
                "wait_slices": wait_slices,
                "active_at_utc": active_at,
                "run0_adopt": adopted.get("state"),
                "mutation": mutation,
                "last_heartbeat": {
                    "started_at_utc": heartbeat_started,
                    "finished_at_utc": heartbeat_finished,
                    "status": heartbeat.get("status"),
                    "expires_in_s": heartbeat.get("expires_in_s"),
                },
                "abrupt_proxy_exit": {
                    **killed,
                    "no_session_release": True,
                    "wrapper_exit_code": proxy.process.returncode,
                },
                "listener_pid_before": next(iter(listener_before)),
                "listener_pid_after": next(iter(listener_after)),
                "dayzdiag_before": counts_before.get("DayZDiag_x64"),
                "dayzdiag_after": counts_after.get("DayZDiag_x64"),
                "finished_at_utc": _utc_now(),
            }
            _write_role(
                gate_dir,
                role,
                artifact,
                identity=identity,
                lease_token=context["lease_token"],
                stderr_path=stderr_path,
            )
            return artifact

        c_artifact_path = gate_dir / "role-C.json"
        c_artifact = _wait_json(c_artifact_path, 30.0)
        elapsed = (
            _parse_time(active_at)
            - _parse_time(c_artifact["last_heartbeat"]["finished_at_utc"])
        ).total_seconds()
        _require(TTL_MIN_S <= elapsed <= TTL_MAX_S, f"D_expiry_elapsed:{elapsed}")
        run0 = control["run0"]
        lifecycle_before_code, lifecycle_before = _lifecycle_call(
            context, "status", temp_dir=gate_dir
        )
        _require(lifecycle_before_code == 0, "D_lifecycle_before_failed")
        run0_before = next(
            (item for item in lifecycle_before.get("runs", []) if item.get("run_id") == run0),
            None,
        )
        _require(
            isinstance(run0_before, dict)
            and run0_before.get("state") == "RUNNING_IDLE"
            and run0_before.get("owner_session_id") is None,
            "D_run0_not_idle",
        )
        adopt_code, adopt = _lifecycle_call(
            context, "adopt", run_id=run0, temp_dir=gate_dir
        )
        _require(adopt_code == 0 and adopt.get("state") == "RUNNING", "D_adopt_failed")
        mutation = _mutate(proxy, role)

        retail_before_code, retail_before = _lifecycle_call(
            context, "status", temp_dir=gate_dir
        )
        _require(retail_before_code == 0, "D_retail_before_failed")
        retail_request = {
            "argv": [
                r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZ_x64.exe"
            ],
            "cwd": r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
            "role": "client",
            "window_style": "normal",
            "label": "H8 distributed retail negative",
            "mod": "@MilitaryBunker",
            "profiles": str(
                ROOT.parent / "MilitaryBunker_dev" / "_client" / "profiles"
            ),
            "mission": "dayzOffline.chernarusplus",
        }
        retail_code, retail = _lifecycle_call(
            context,
            "start",
            request=retail_request,
            temp_dir=gate_dir,
        )
        _require(
            retail_code == 1
            and retail.get("error") == "retail_manual_lifecycle_required",
            "D_retail_negative_failed",
        )
        retail_after_code, retail_after = _lifecycle_call(
            context, "status", temp_dir=gate_dir
        )
        _require(retail_after_code == 0, "D_retail_after_failed")
        _require(
            _manifest_shape(retail_before) == _manifest_shape(retail_after),
            "D_retail_manifest_changed",
        )

        stop0_code, stop0 = _lifecycle_call(
            context, "stop", run_id=run0, temp_dir=gate_dir
        )
        _require(stop0_code == 0 and stop0.get("state") == "EXITED", "D_stop0_failed")
        _wait_process_counts_zero()
        heartbeat = _tool_result(
            proxy,
            "session_heartbeat",
            {"lease_token": context["lease_token"]},
        )
        _require(heartbeat.get("status") == "active", "D_heartbeat_before_replace_failed")
        run1, launch1 = _start_fixture(
            proxy, context, temp_dir=gate_dir, tag="R1-distributed"
        )
        _register_run(gate_dir, "R1", run1, context)
        _require(run1 != run0, "D_run1_not_new")
        ready1 = _wait_bridge_ready(proxy)
        _require(
            ready1["daemon_generation"] == control["daemon_generation"],
            "D_generation_changed",
        )
        stop1_code, stop1 = _lifecycle_call(
            context, "stop", run_id=run1, temp_dir=gate_dir
        )
        _require(stop1_code == 0 and stop1.get("state") == "EXITED", "D_stop1_failed")
        zero_counts = _wait_process_counts_zero()
        lifecycle_final_code, lifecycle_final = _lifecycle_call(
            context, "status", temp_dir=gate_dir
        )
        _require(lifecycle_final_code == 0, "D_lifecycle_final_failed")
        active_runs = [
            item
            for item in lifecycle_final.get("runs", [])
            if item.get("state") in ACTIVE_RUN_STATES
        ]
        _require(active_runs == [], "D_active_runs_remain")
        released = _tool_result(
            proxy,
            "session_release",
            {"lease_token": context["lease_token"]},
        )
        _require(
            released.get("released") is True
            and released.get("cleanup_degraded") == [],
            "D_release_failed",
        )
        final_status = _tool_result(proxy, "session_status", {})
        _require(_clean_status(final_status), "D_final_status_not_clean")
        artifact = {
            "role": role,
            **task,
            "runner_pid": os.getpid(),
            "identity": _identity_public(identity),
            "daemon_generation": read["daemon_generation"],
            "parallel_read": read,
            "acquire": {
                "at_utc": acquired_at,
                "status": "queued",
                "position": acquired["position"],
            },
            "wait_slices": wait_slices,
            "active_at_utc": active_at,
            "expiry_elapsed_s": round(elapsed, 3),
            "run0_state_before_adopt": run0_before.get("state"),
            "adopt": adopt.get("state"),
            "mutation": mutation,
            "retail_negative": retail.get("error"),
            "run0_stop": stop0.get("state"),
            "run1": run1,
            "launch1": launch1,
            "bridge_ready1": ready1,
            "run1_stop": stop1.get("state"),
            "active_runs_final": 0,
            "zero_process_counts": zero_counts,
            "release": {
                "released": released.get("released"),
                "cleanup_degraded": released.get("cleanup_degraded"),
            },
            "final_status": _status_evidence(final_status),
            "finished_at_utc": _utc_now(),
        }
        _write_role(
            gate_dir,
            role,
            artifact,
            identity=identity,
            lease_token=context["lease_token"],
            stderr_path=stderr_path,
        )
        _close_proxy(proxy)
        return artifact
    except Exception as exc:
        cleanup_errors: list[str] = []
        if context is not None and not crash_committed:
            try:
                _cleanup_registered_runs(context, gate_dir)
            except Exception as cleanup_exc:
                cleanup_errors.append(
                    f"runs:{type(cleanup_exc).__name__}:{cleanup_exc}"
                )
            try:
                _tool_result(
                    proxy,
                    "session_release",
                    {"lease_token": context["lease_token"]},
                )
            except Exception as release_exc:
                cleanup_errors.append(
                    f"release:{type(release_exc).__name__}:{release_exc}"
                )
        try:
            if proxy.process.poll() is None:
                _close_proxy(proxy)
        except Exception as close_exc:
            cleanup_errors.append(f"proxy:{type(close_exc).__name__}:{close_exc}")
        try:
            _delete_failure_stderr(stderr_path)
        except Exception as stderr_exc:
            cleanup_errors.append(
                f"stderr:{type(stderr_exc).__name__}:{stderr_exc}"
            )
        if cleanup_errors:
            raise GateFailure(
                f"{role}_failed:{type(exc).__name__}:{exc};cleanup_failed:{'|'.join(cleanup_errors)}"
            ) from exc
        raise


def _finalize(gate_dir: Path) -> dict[str, Any]:
    roster, roster_sha256 = _load_roster(gate_dir, timeout_s=10.0)
    roles = {role: _wait_json(gate_dir / f"role-{role}.json", 10.0) for role in "ABCD"}
    _require(all(item.get("overall_pass") is True for item in roles.values()), "role_failed")
    for role in "ABCD":
        expected_task = roster["roles"][role]
        _require(
            roles[role].get("task_path") == expected_task["task_path"]
            and roles[role].get("task_id") == expected_task["task_id"]
            and roles[role].get("roster_source") == roster["source"]
            and roles[role].get("roster_nonce") == roster["nonce"]
            and roles[role].get("roster_sha256") == roster_sha256,
            f"{role}_task_roster_mismatch",
        )
    task_paths = [roles[role]["task_path"] for role in "ABCD"]
    task_ids = [roles[role]["task_id"] for role in "ABCD"]
    runner_pids = [roles[role]["runner_pid"] for role in "ABCD"]
    _require(len(set(task_paths)) == 4, "task_paths_not_unique")
    _require(len(set(task_ids)) == 4, "task_ids_not_unique")
    _require(len(set(runner_pids)) == 4, "runner_pids_not_unique")
    pids = [roles[role]["identity"]["pid"] for role in "ABCD"]
    sessions = [roles[role]["identity"]["session_sha256_12"] for role in "ABCD"]
    _require(len(set(pids)) == 4, "identity_pids_not_unique")
    _require(len(set(sessions)) == 4, "identity_sessions_not_unique")
    generations = {roles[role]["daemon_generation"] for role in "ABCD"}
    _require(len(generations) == 1, "role_generation_mismatch")
    generation = next(iter(generations))
    read_times = [
        _parse_time(roles[role]["parallel_read"]["started_at_utc"]).timestamp()
        for role in "ABCD"
    ]
    read_skew = max(read_times) - min(read_times)
    _require(read_skew <= 0.5, f"parallel_read_skew:{read_skew}")
    _require(roles["A"]["queue_snapshot"]["positions"] == [1, 2, 3], "queue_not_123")
    _require(roles["C"]["abrupt_proxy_exit"]["no_session_release"] is True, "C_released")
    _require(roles["C"]["abrupt_proxy_exit"]["process_absent"] is True, "C_proxy_alive")
    _require(roles["C"]["run0_adopt"] == "RUNNING", "C_run0_not_adopted")
    _require(roles["D"]["run0_state_before_adopt"] == "RUNNING_IDLE", "C_expiry_run_not_idle")
    _require(
        TTL_MIN_S <= float(roles["D"]["expiry_elapsed_s"]) <= TTL_MAX_S,
        "expiry_bad",
    )
    _require(
        all(
            roles[role]["final_status"]["own_lease"] == "none"
            and roles[role]["final_status"]["own_ticket"] == "none"
            for role in ("A", "B", "D")
        ),
        "actor_final_state_not_none",
    )
    _require(
        roles["D"]["final_status"]["owner"] is None
        and roles["D"]["final_status"]["queue"] == []
        and roles["D"]["final_status"]["pending_commands"] == 0
        and roles["D"]["final_status"]["cleanup_degraded"] == [],
        "global_final_not_clean",
    )

    control = _read_json(gate_dir / "control.json")
    prefixes = {roles[role]["identity"]["session_prefix"] for role in "ABCD"}
    events, audit_raw = _audit_documents(
        _parse_time(control["gate_started_at_utc"]),
        generation,
        prefixes,
    )
    audit = _audit_summary(events)
    event_names = set(audit["counts"])
    _require(
        EXPECTED_AUDIT_EVENTS.issubset(event_names),
        f"audit_events_missing:{sorted(EXPECTED_AUDIT_EVENTS-event_names)}",
    )
    mutation_order = [
        event.get("client", {}).get("session")
        for event in events
        if event.get("event") == "session_authorized"
        and event.get("command") == "notify_players"
    ]
    expected_order = [roles[role]["identity"]["session_prefix"] for role in "ABCD"]
    _require(mutation_order[:4] == expected_order, "audit_mutation_order_wrong")
    _require(not _forbidden_key(events), "forbidden_field_in_audit")
    expiry_audit = _expiry_audit_evidence(
        events,
        roles["C"]["identity"]["session_prefix"],
        roles["C"]["last_heartbeat"]["finished_at_utc"],
    )

    doctor = _doctor()
    _require(doctor == {"exit_code": 0, "ok": True, "finding_codes": []}, "doctor_not_clean")
    process_counts = _process_counts()
    _require(not any(process_counts.values()), "dayz_processes_remain")
    _require(_udp_owners(GAME_PORT) == [], "udp2302_remains")
    listener = _listener_pids(PORT)
    _require(len(listener) == 1, "listener_not_one")

    candidate = {
        "schema": "dayz-mcp-h8-distributed-v1",
        "composition": "4-Codex tasks distributed",
        "overall_pass": True,
        "gate_started_at_utc": control["gate_started_at_utc"],
        "finished_at_utc": _utc_now(),
        "daemon_generation": generation,
        "listener_pid": next(iter(listener)),
        "task_roster": {
            "schema": roster["schema"],
            "source": roster["source"],
            "nonce": roster["nonce"],
            "sha256": roster_sha256,
            "task_ids_unique": True,
            "runner_pids_unique": True,
        },
        "task_mapping": {
            role: {
                "task_path": roles[role]["task_path"],
                "task_id": roles[role]["task_id"],
                "runner_pid": roles[role]["runner_pid"],
                **roles[role]["identity"],
            }
            for role in "ABCD"
        },
        "parallel_reads": {
            "count": 4,
            "dispatch_skew_s": round(read_skew, 3),
            "all_ok": True,
            "roles": {
                role: roles[role]["parallel_read"]
                for role in "ABCD"
            },
        },
        "fifo": {
            "initial_positions": roles["A"]["queue_snapshot"]["positions"],
            "mutation_order": mutation_order[:4],
            "normal_releases": ["A", "B", "D"],
        },
        "owner_crash": {
            "role": "C",
            "heartbeat": roles["C"]["last_heartbeat"],
            "abrupt_proxy_exit": roles["C"]["abrupt_proxy_exit"],
            "grant_elapsed_s": roles["D"]["expiry_elapsed_s"],
            "audit_expiry": expiry_audit,
            "listener_stable": (
                roles["C"]["listener_pid_before"]
                == roles["C"]["listener_pid_after"]
                == next(iter(listener))
            ),
            "dayz_stable": (
                roles["C"]["dayzdiag_before"] == 2
                and roles["C"]["dayzdiag_after"] == 2
            ),
        },
        "lifecycle": {
            "run0": control["run0"],
            "run0_state_before_adopt": roles["D"]["run0_state_before_adopt"],
            "D_adopt": roles["D"]["adopt"],
            "retail_negative": roles["D"]["retail_negative"],
            "run0_stop": roles["D"]["run0_stop"],
            "run1": roles["D"]["run1"],
            "run1_stop": roles["D"]["run1_stop"],
            "active_runs_final": roles["D"]["active_runs_final"],
        },
        "final": {
            "implementing_actor_A": roles["A"]["final_status"],
            "actor_B": roles["B"]["final_status"],
            "actor_C_after_crash": {
                "snapshot_source": "audit_expiry_and_successor_grant",
                "own_lease": "none_after_audited_expiry",
                "own_ticket": "none_after_grant",
                "proxy_process_absent": roles["C"]["abrupt_proxy_exit"][
                    "process_absent"
                ],
            },
            "actor_D_and_global": roles["D"]["final_status"],
            "process_counts": process_counts,
            "udp_2302_owners": [],
            "doctor": doctor,
        },
        "audit": audit,
        "audit_mutation_order": mutation_order[:4],
        "role_secret_scans": {
            role: roles[role]["secret_scan"] for role in "ABCD"
        },
    }
    key = KEYFILE.read_text(encoding="utf-8").strip()
    candidate_wire = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    _require(key not in candidate_wire, "api_key_in_result")
    _require(not _contains_value(audit_raw["raw_texts"], [key]), "api_key_in_audit")
    _require(
        not _has_forbidden_role_key(_all_audit_documents(audit_raw["raw_texts"])),
        "forbidden_field_in_full_audit",
    )
    _require(not _has_forbidden_role_key(candidate), "forbidden_secret_key_in_result")
    candidate["secret_scan"] = {
        "api_key_in_full_audit": 0,
        "api_key_in_serialized_result": 0,
        "forbidden_secret_keys_in_result": 0,
        "forbidden_fields_in_filtered_audit": 0,
        "forbidden_fields_in_full_audit": 0,
    }
    _atomic_json(RESULT_PATH, candidate)
    return candidate


def main() -> int:
    args = _parser().parse_args()
    args.gate_dir = args.gate_dir.resolve()
    try:
        if args.role == "A":
            result = _run_a(args.gate_dir, args.task_path)
        elif args.role in {"B", "C", "D"}:
            result = _run_queued(args.role, args.gate_dir, args.task_path)
        elif args.role == "CLEANUP":
            result = _cleanup_gate(args.gate_dir)
        else:
            try:
                result = _finalize(args.gate_dir)
            except Exception as finalize_exc:
                try:
                    cleanup = _cleanup_gate(args.gate_dir)
                except Exception as cleanup_exc:
                    raise GateFailure(
                        "finalize_failed:"
                        f"{type(finalize_exc).__name__}:{finalize_exc};"
                        "cleanup_failed:"
                        f"{type(cleanup_exc).__name__}:{cleanup_exc}"
                    ) from finalize_exc
                raise GateFailure(
                    "finalize_failed:"
                    f"{type(finalize_exc).__name__}:{finalize_exc};"
                    f"cleanup_pass:{cleanup.get('overall_pass')}"
                ) from finalize_exc
        if args.role == "FINALIZE":
            result_path = RESULT_PATH
        elif args.role == "CLEANUP":
            result_path = args.gate_dir / "cleanup.json"
        else:
            result_path = args.gate_dir / f"role-{args.role}.json"
        print(
            json.dumps(
                {
                    "overall_pass": result.get("overall_pass"),
                    "role": args.role,
                    "result_path": str(result_path),
                },
                separators=(",", ":"),
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "overall_pass": False,
                    "role": args.role,
                    "error": f"{type(exc).__name__}:{exc}",
                },
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
