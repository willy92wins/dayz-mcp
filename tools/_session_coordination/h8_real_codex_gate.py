"""Real H8 gate using four fresh Codex-mode MCP stdio proxies and one DayZ game.

Lease tokens and full client identities remain in this process only.  Lifecycle
children receive them through their private environment; evidence is redacted.
No DayZ or daemon process is terminated directly.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from e2e_agent_sessions import StdioProxy, _listener_pids


HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
ROOT = TOOLS.parent
VENV_PYTHON = TOOLS / ".venv-mcp" / "Scripts" / "python.exe"
KEYFILE = TOOLS / ".dayz_mcp.key"
FIXTURE_LAUNCHER = (
    ROOT.parent / "MilitaryBunker_dev" / "tools" / "dayz-test.ps1"
)
RESULT_PATH = ROOT / "reviews" / "2026-07-16-h8-real-4-codex.json"
PORT = 8765
GAME_PORT = 2302
OWNER_TTL_S = 120.0
WAIT_SLICE_S = 30.0
EXPIRY_HARD_LIMIT_S = 155.0
ACTIVE_RUN_STATES = {"STARTING", "RUNNING", "STOPPING", "RUNNING_IDLE"}
SECRET_KEYS = {
    "key",
    "api_key",
    "keyfile",
    "lease_token",
    "password",
    "token",
    "client_identity_json",
    "pid",
    "ppid",
    "args",
    "request_args",
}
EXPECTED_AUDIT_EVENTS = {
    "session_acquire",
    "session_queued",
    "session_granted",
    "session_wait",
    "session_authorized",
    "session_rejected",
    "session_release_started",
    "session_release_finished",
    "session_expired",
    "lifecycle_start_outcome",
    "lifecycle_adopt",
    "lifecycle_stop_outcome",
    "lifecycle_start_rejected",
}


class GateFailure(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require(condition: object, message: str) -> None:
    if not condition:
        raise GateFailure(message)


def _proxy_argv(label: str) -> list[str]:
    return [
        str(VENV_PYTHON),
        "-m",
        "dayz_mcp",
        "--client",
        "--no-daemon-autospawn",
        "--port",
        str(PORT),
        "--keyfile",
        str(KEYFILE),
        "--require-version",
        "--idle-timeout",
        "1800",
        "--client-platform",
        "codex",
        "--task-label",
        label,
    ]


def _tool_result(proxy: StdioProxy, name: str, arguments: dict, timeout: float = 25.0) -> dict:
    raw = proxy.request(
        "tools/call",
        {"name": name, "arguments": arguments},
        timeout=timeout,
    )
    if raw.get("isError"):
        raise GateFailure(f"{name}:{_tool_error_code(raw)}")
    structured = raw.get("structuredContent")
    if isinstance(structured, dict):
        value = structured.get("result", structured)
        if isinstance(value, dict):
            return value
    content = raw.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                try:
                    value = json.loads(item["text"])
                except ValueError:
                    continue
                if isinstance(value, dict):
                    return value
    raise GateFailure(f"{name}:bad_payload")


def _tool_error_code(raw: dict) -> str:
    text = json.dumps(raw.get("content", []), ensure_ascii=False)
    for code in (
        "lease_required",
        "lease_invalid",
        "ticket_invalid",
        "retail_quarantine",
        "version_blocked",
    ):
        if code in text:
            return code
    return "tool_error"


def _expect_tool_error(proxy: StdioProxy, name: str, arguments: dict, expected: str) -> str:
    raw = proxy.request(
        "tools/call",
        {"name": name, "arguments": arguments},
        timeout=25.0,
    )
    _require(raw.get("isError") is True, f"{name}:expected_error_missing")
    actual = _tool_error_code(raw)
    _require(actual == expected, f"{name}:expected_{expected}_got_{actual}")
    return actual


def _identity_from(acquire: dict, expected_pid: int, gate_started: datetime) -> dict:
    identity_text = acquire.get("client_identity_json")
    _require(isinstance(identity_text, str) and identity_text, "identity_missing")
    identity = json.loads(identity_text)
    _require(identity.get("platform") == "codex", "identity_platform_not_codex")
    runtime_pid = identity.get("pid")
    _require(isinstance(runtime_pid, int) and runtime_pid > 0, "identity_pid_invalid")
    if runtime_pid != expected_pid:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Process -Filter "
                f"'ProcessId = {runtime_pid}' -ErrorAction Stop).ParentProcessId",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        _require(completed.returncode == 0, "identity_parent_scan_failed")
        parent_text = completed.stdout.strip()
        _require(
            parent_text.isdigit() and int(parent_text) == expected_pid,
            "identity_pid_not_proxy_child",
        )
    started_text = identity.get("started_at_utc")
    _require(isinstance(started_text, str), "identity_started_missing")
    started = datetime.fromisoformat(started_text.replace("Z", "+00:00"))
    _require(started >= gate_started, "identity_not_fresh")
    session_id = identity.get("session_id")
    _require(isinstance(session_id, str) and session_id, "identity_session_missing")
    return {
        "raw": identity,
        "json": identity_text,
        "session_id": session_id,
        "session_prefix": session_id[:12],
        "session_sha256_12": hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12],
        "pid": identity["pid"],
        "proxy_wrapper_pid": expected_pid,
        "started_at_utc": started_text,
        "platform": identity["platform"],
        "task_label": identity.get("task_label", ""),
    }


def _lease_context(acquire: dict, identity: dict) -> dict:
    token = acquire.get("lease_token")
    _require(isinstance(token, str) and token, "lease_token_missing")
    return {
        "lease_token": token,
        "identity_json": identity["json"],
        "lease_id": acquire.get("lease_id"),
    }


def _lifecycle_env(context: dict) -> dict[str, str]:
    env = os.environ.copy()
    env["DAYZ_MCP_CLIENT_ID_JSON"] = context["identity_json"]
    env["DAYZ_MCP_LEASE_TOKEN"] = context["lease_token"]
    return env


def _lifecycle_call(
    context: dict,
    command: str,
    *,
    run_id: str | None = None,
    request: dict | None = None,
    temp_dir: Path,
) -> tuple[int, dict]:
    argv = [
        str(VENV_PYTHON),
        "-m",
        "dayz_mcp.lifecycle_cli",
        "--keyfile",
        str(KEYFILE),
        "--port",
        str(PORT),
        command,
    ]
    request_path: Path | None = None
    if command == "start":
        _require(isinstance(request, dict), "lifecycle_start_request_missing")
        request_path = temp_dir / f"lifecycle-request-{time.monotonic_ns()}.json"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        argv.extend(["--request-file", str(request_path)])
    elif command in {"stop", "adopt"}:
        _require(isinstance(run_id, str) and run_id, f"lifecycle_{command}_run_missing")
        argv.extend(["--run-id", run_id])
    try:
        completed = subprocess.run(
            argv,
            cwd=str(TOOLS),
            env=_lifecycle_env(context),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
    finally:
        if request_path is not None:
            request_path.unlink(missing_ok=True)
    try:
        payload = json.loads(completed.stdout.strip())
    except ValueError as exc:
        raise GateFailure(f"lifecycle_{command}:invalid_json") from exc
    _require(isinstance(payload, dict), f"lifecycle_{command}:bad_payload")
    return completed.returncode, payload


def _start_fixture(
    proxy: StdioProxy,
    context: dict,
    *,
    temp_dir: Path,
    tag: str,
) -> tuple[str, dict]:
    argv = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(FIXTURE_LAUNCHER),
        "-Mod",
        "MilitaryBunker",
        "-Mode",
        "all",
        "-NoBaseMods",
        "-ExtraMods",
        "@DayZ_MCP",
        "-ServerWait",
        "240",
    ]
    started = time.monotonic()
    process = subprocess.Popen(
        argv,
        cwd=str(FIXTURE_LAUNCHER.parent.parent),
        env=_lifecycle_env(context),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    heartbeats = 0
    stdout = ""
    try:
        while True:
            try:
                stdout, _ = process.communicate(timeout=20)
                break
            except subprocess.TimeoutExpired:
                heartbeat = _tool_result(
                    proxy,
                    "session_heartbeat",
                    {"lease_token": context["lease_token"]},
                )
                _require(
                    heartbeat.get("status") == "active",
                    "launcher_heartbeat_not_active",
                )
                heartbeats += 1
            if time.monotonic() - started > 360:
                raise GateFailure("fixture_launcher_timeout")
        _require(process.returncode == 0, f"fixture_launcher_exit_{process.returncode}")
        match = re.search(r"Managed run_id:\s*([^\.\s]+)", stdout)
        _require(match is not None, "fixture_run_id_missing")
        run_id = match.group(1)
        return run_id, {
            "tag": tag,
            "exit_code": process.returncode,
            "duration_s": round(time.monotonic() - started, 3),
            "heartbeats": heartbeats,
            "run_id": run_id,
        }
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


def _wait_bridge_ready(proxy: StdioProxy, timeout_s: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = _tool_result(proxy, "bridge_status", {})
        server = last.get("server_peer", {})
        client = last.get("client_peer", {})
        if (
            server.get("version_state") == "ok"
            and client.get("version_state") == "ok"
            and isinstance(server.get("last_poll_age_s"), (int, float))
            and isinstance(client.get("last_poll_age_s"), (int, float))
            and server["last_poll_age_s"] < 10.0
            and client["last_poll_age_s"] < 10.0
        ):
            return {
                "server_version_state": "ok",
                "client_version_state": "ok",
                "server_poll_age_s": server["last_poll_age_s"],
                "client_poll_age_s": client["last_poll_age_s"],
                "daemon_generation": last.get("daemon_generation"),
            }
        time.sleep(2)
    raise GateFailure(
        "bridge_not_ready:"
        f"{last.get('version_state')}:{last.get('server_peer')}:{last.get('client_peer')}"
    )


def _parallel_reads(proxies: list[StdioProxy]) -> dict:
    started: list[float] = [0.0] * len(proxies)

    def one(index: int) -> dict:
        started[index] = time.monotonic()
        return _tool_result(proxies[index], "bridge_status", {})

    begin = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(one, range(4)))
    _require(
        all(
            value.get("server_peer", {}).get("version_state") == "ok"
            and value.get("client_peer", {}).get("version_state") == "ok"
            for value in values
        ),
        "parallel_read_failed",
    )
    return {
        "count": len(values),
        "dispatch_skew_s": round(max(started) - min(started), 4),
        "duration_s": round(time.monotonic() - begin, 3),
        "tools": ["bridge_status"] * 4,
        "all_ok": True,
    }


def _mutate(proxy: StdioProxy, role: str) -> dict:
    result = _tool_result(
        proxy,
        "notify_players",
        {
            "title": f"DayZ MCP H8 {role}",
            "detail": "FIFO coordination gate",
            "show_time": 1.0,
            "timeout_s": 20.0,
        },
        timeout=35.0,
    )
    _require(result.get("ok"), f"mutation_{role}_failed")
    return {"role": role, "command": "notify_players", "ok": True}


def _close_proxy(proxy: StdioProxy) -> dict:
    eof_sent = False
    graceful = False
    if proxy.process.poll() is None and proxy.process.stdin is not None:
        try:
            proxy.process.stdin.close()
            eof_sent = True
        except OSError:
            pass
        try:
            proxy.process.wait(timeout=10)
            graceful = eof_sent
        except subprocess.TimeoutExpired:
            proxy.process.terminate()
            try:
                proxy.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.process.kill()
                proxy.process.wait(timeout=5)
    return {
        "exit_code": proxy.process.returncode,
        "stdin_eof_exit": graceful,
        "eof_sent": eof_sent,
    }


def _doctor() -> dict:
    completed = subprocess.run(
        [str(VENV_PYTHON), "-m", "dayz_mcp.doctor", "--json", "--require-clean"],
        cwd=str(TOOLS),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        raise GateFailure("doctor_invalid_json") from exc
    return {
        "exit_code": completed.returncode,
        "ok": payload.get("ok"),
        "finding_codes": [
            item.get("code")
            for item in payload.get("findings", [])
            if isinstance(item, dict)
        ],
    }


def _udp_owners(port: int) -> list[int]:
    command = (
        "ConvertTo-Json -Compress -InputObject @("
        f"Get-NetUDPEndpoint -LocalPort {port} -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty OwningProcess)"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    _require(completed.returncode == 0, "udp_scan_failed")
    text = completed.stdout.strip()
    _require(bool(text), "udp_scan_empty")
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise GateFailure("udp_scan_invalid_json") from exc
    values = value if isinstance(value, list) else [value]
    return sorted({int(item) for item in values})


def _process_counts() -> dict[str, int]:
    names = [
        "DayZDiag_x64",
        "DayZServer_x64",
        "DayZ_x64",
        "DayZ_BE",
        "DZSALauncher",
    ]
    counts: dict[str, int] = {}
    for name in names:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"@(Get-Process -Name '{name}' -ErrorAction SilentlyContinue).Count",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        _require(completed.returncode == 0, f"process_scan_failed:{name}")
        text = completed.stdout.strip()
        _require(text.isdigit(), f"process_scan_invalid:{name}")
        counts[name] = int(text)
    return counts


def _wait_process_counts_zero(timeout_s: float = 90.0) -> dict[str, int]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, int] = {}
    while time.monotonic() < deadline:
        last = _process_counts()
        if not any(last.values()) and not _udp_owners(GAME_PORT):
            return last
        time.sleep(2)
    raise GateFailure(f"dayz_processes_not_zero:{last}")


def _audit_documents(started_at: datetime, generation: str, prefixes: set[str]) -> tuple[list[dict], dict]:
    audit_dir = Path(os.environ["LOCALAPPDATA"]) / "DayZ_MCP" / "audit"
    documents: list[dict] = []
    raw_texts: list[str] = []
    for path in sorted(audit_dir.glob("events.jsonl*")):
        text = path.read_text(encoding="utf-8")
        raw_texts.append(text)
        for line in text.splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                continue
            timestamp = value.get("timestamp_utc")
            if not isinstance(timestamp, str):
                continue
            when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            client = value.get("client")
            session = client.get("session") if isinstance(client, dict) else None
            if (
                when >= started_at
                and value.get("daemon_generation") == generation
                and session in prefixes
            ):
                documents.append(value)
    documents.sort(key=lambda item: str(item.get("timestamp_utc", "")))
    return documents, {"raw_texts": raw_texts}


def _contains_value(value: object, needles: list[str]) -> bool:
    if isinstance(value, dict):
        return any(
            _contains_value(key, needles) or _contains_value(item, needles)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_value(item, needles) for item in value)
    if isinstance(value, str):
        return any(needle and needle in value for needle in needles)
    return False


def _forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        if any(str(key).casefold() in SECRET_KEYS for key in value):
            return True
        return any(_forbidden_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_forbidden_key(item) for item in value)
    return False


def _audit_summary(events: list[dict]) -> dict:
    counts = Counter(str(event.get("event")) for event in events)
    timeline = []
    for event in events:
        client = event.get("client")
        timeline.append(
            {
                "event": event.get("event"),
                "session": client.get("session") if isinstance(client, dict) else None,
                "command": event.get("command"),
                "decision": event.get("decision"),
                "reason": event.get("reason"),
                "lease_id": event.get("lease_id"),
                "run_id": event.get("run_id"),
                "duration_s": event.get("duration_s"),
            }
        )
    return {"counts": dict(sorted(counts.items())), "timeline": timeline}


def _manifest_shape(status: dict) -> list[dict]:
    return sorted(
        [
            {
                "run_id": run.get("run_id"),
                "state": run.get("state"),
                "owner_session_id": run.get("owner_session_id"),
                "owner_lease_id": run.get("owner_lease_id"),
                "processes": len(run.get("processes", [])),
            }
            for run in status.get("runs", [])
            if isinstance(run, dict)
        ],
        key=lambda item: str(item["run_id"]),
    )


def _clean_status(status: dict) -> bool:
    return (
        status.get("owner") is None
        and status.get("queue") == []
        and status.get("self", {}).get("state") == "none"
        and status.get("pending_commands") == 0
        and status.get("cleanup_degraded") == []
    )


def _release_http(context: dict) -> tuple[int, dict]:
    key = KEYFILE.read_text(encoding="utf-8").strip()
    request = urllib.request.Request(
        "http://127.0.0.1:"
        f"{PORT}/session/release?{urllib.parse.urlencode({'key': key})}",
        data=json.dumps(
            {
                "identity": json.loads(context["identity_json"]),
                "lease_token": context["lease_token"],
            },
            separators=(",", ":"),
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20.0) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), json.loads(exc.read().decode("utf-8") or "{}")
        finally:
            exc.close()


def _cleanup_failed_gate(
    proxies: list[StdioProxy],
    acquired: dict[str, dict],
    contexts: dict[str, dict],
    identities: dict[str, dict],
    temp_dir: Path,
) -> dict:
    roles = ("C1", "C2", "C3", "C4")
    stopped: list[str] = []
    released: set[str] = set()
    try:
        for _cycle in range(20):
            for index, role in enumerate(roles):
                if (
                    role in contexts
                    or role not in acquired
                    or index >= len(proxies)
                    or proxies[index].process.poll() is not None
                ):
                    continue
                ticket = acquired[role].get("ticket")
                if not isinstance(ticket, str):
                    continue
                try:
                    waited = _tool_result(
                        proxies[index],
                        "session_wait",
                        {"ticket": ticket, "timeout_s": 0.0},
                    )
                except Exception:
                    continue
                if waited.get("status") == "active":
                    contexts[role] = _lease_context(waited, identities[role])

            lifecycle_status: dict = {"runs": []}
            for role in reversed(roles):
                if role not in contexts:
                    continue
                code, payload = _lifecycle_call(
                    contexts[role], "status", temp_dir=temp_dir
                )
                if code == 0:
                    lifecycle_status = payload
                    break

            active_runs = [
                run
                for run in lifecycle_status.get("runs", [])
                if isinstance(run, dict)
                and run.get("mod") == "@MilitaryBunker"
                and run.get("state") in ACTIVE_RUN_STATES
            ]
            for run in active_runs:
                run_id = run.get("run_id")
                if not isinstance(run_id, str):
                    continue
                for role in reversed(roles):
                    if role not in contexts:
                        continue
                    context = contexts[role]
                    state = run.get("state")
                    if state == "RUNNING_IDLE":
                        adopt_code, adopt = _lifecycle_call(
                            context, "adopt", run_id=run_id, temp_dir=temp_dir
                        )
                        if adopt_code != 0 or adopt.get("state") != "RUNNING":
                            continue
                    elif not (
                        state == "RUNNING"
                        and run.get("owner_session_id")
                        == identities.get(role, {}).get("session_id")
                    ):
                        continue
                    stop_code, stop = _lifecycle_call(
                        context, "stop", run_id=run_id, temp_dir=temp_dir
                    )
                    if stop_code == 0 and stop.get("state") == "EXITED":
                        stopped.append(run_id)
                        break

            for role in roles:
                if role not in contexts or role in released:
                    continue
                status, payload = _release_http(contexts[role])
                if status == 200 and payload.get("released") is True:
                    released.add(role)

            alive = [
                proxy for proxy in proxies if proxy.process.poll() is None
            ]
            public = _tool_result(alive[0], "session_status", {}) if alive else {}
            counts = _process_counts()
            if _clean_status(public) and not any(counts.values()) and not _udp_owners(GAME_PORT):
                return {
                    "clean": True,
                    "stopped_run_ids": sorted(set(stopped)),
                    "released_roles": sorted(released),
                }
            time.sleep(1.0)
        return {
            "clean": False,
            "stopped_run_ids": sorted(set(stopped)),
            "released_roles": sorted(released),
            "manual_cleanup_required": True,
        }
    except Exception as exc:
        return {
            "clean": False,
            "stopped_run_ids": sorted(set(stopped)),
            "released_roles": sorted(released),
            "manual_cleanup_required": True,
            "cleanup_error": type(exc).__name__,
        }


def _run() -> dict:
    gate_started_text = _utc_now()
    gate_started = datetime.fromisoformat(gate_started_text.replace("Z", "+00:00"))
    result: dict[str, Any] = {
        "schema": "dayz-mcp-h8-real-v1",
        "composition": "4-Codex substitution",
        "started_at_utc": gate_started_text,
        "overall_pass": False,
    }
    transient_secrets: list[str] = []
    proxies: list[StdioProxy] = []
    contexts: dict[str, dict] = {}
    identities: dict[str, dict] = {}
    acquired: dict[str, dict] = {}
    run_ids: list[str] = []
    closed_roles: set[str] = set()

    doctor_before = _doctor()
    _require(doctor_before == {"exit_code": 0, "ok": True, "finding_codes": []}, "doctor_before_not_clean")
    listener_before = _listener_pids(PORT)
    _require(len(listener_before) == 1, "listener_before_not_exactly_one")
    _require(_udp_owners(GAME_PORT) == [], "udp_before_not_free")
    _require(not any(_process_counts().values()), "processes_before_not_zero")

    with tempfile.TemporaryDirectory(prefix="dayz-mcp-h8-") as raw_temp:
        temp_dir = Path(raw_temp)
        try:
            for role in ("C1", "C2", "C3", "C4"):
                proxy = StdioProxy(
                    _proxy_argv(f"h8-{role}"),
                    TOOLS,
                    temp_dir / f"{role}.stderr.log",
                )
                proxy.initialize()
                proxies.append(proxy)

            statuses_initial = [
                _tool_result(proxy, "session_status", {}) for proxy in proxies
            ]
            generations = {status.get("daemon_generation") for status in statuses_initial}
            _require(len(generations) == 1 and None not in generations, "initial_generation_mismatch")
            generation = str(next(iter(generations)))
            _require(all(_clean_status(status) for status in statuses_initial), "initial_session_not_clean")

            negative = _expect_tool_error(
                proxies[1],
                "notify_players",
                {
                    "title": "H8 negative",
                    "detail": "must not enter game",
                    "show_time": 1.0,
                    "timeout_s": 5.0,
                },
                "lease_required",
            )

            acquired["C1"] = _tool_result(
                proxies[0], "session_acquire", {"purpose": "H8 fixture and FIFO"}
            )
            identities["C1"] = _identity_from(
                acquired["C1"], proxies[0].process.pid, gate_started
            )
            transient_secrets.extend(
                [identities["C1"]["session_id"], identities["C1"]["json"]]
            )
            contexts["C1"] = _lease_context(acquired["C1"], identities["C1"])
            transient_secrets.append(contexts["C1"]["lease_token"])

            run0, launch0 = _start_fixture(
                proxies[0], contexts["C1"], temp_dir=temp_dir, tag="R0"
            )
            run_ids.append(run0)
            ready0 = _wait_bridge_ready(proxies[0])
            _require(ready0["daemon_generation"] == generation, "generation_changed_after_launch")
            _require(_listener_pids(PORT) == listener_before, "listener_changed_after_launch")
            in_game_counts = _process_counts()
            _require(in_game_counts.get("DayZDiag_x64") == 2, "fixture_not_two_diag_processes")
            parallel_reads = _parallel_reads(proxies)

            for index, role in enumerate(("C2", "C3", "C4"), start=1):
                acquired[role] = _tool_result(
                    proxies[index],
                    "session_acquire",
                    {"purpose": f"H8 FIFO {role}"},
                )
                identities[role] = _identity_from(
                    acquired[role], proxies[index].process.pid, gate_started
                )
                transient_secrets.extend(
                    [identities[role]["session_id"], identities[role]["json"]]
                )
                _require(acquired[role].get("status") == "queued", f"{role}_not_queued")

            _require(
                [acquired[role].get("position") for role in ("C2", "C3", "C4")]
                == [1, 2, 3],
                "fifo_initial_positions_wrong",
            )
            all_sessions = {identity["session_id"] for identity in identities.values()}
            all_pids = {identity["pid"] for identity in identities.values()}
            _require(len(all_sessions) == 4 and len(all_pids) == 4, "identities_not_unique")

            mutations = [_mutate(proxies[0], "C1")]
            release1 = _tool_result(
                proxies[0],
                "session_release",
                {"lease_token": contexts["C1"]["lease_token"]},
            )
            _require(
                release1.get("released") is True
                and release1.get("cleanup_degraded") == [],
                "C1_release_failed",
            )

            active2 = _tool_result(
                proxies[1],
                "session_wait",
                {"ticket": acquired["C2"]["ticket"], "timeout_s": 5.0},
            )
            _require(active2.get("status") == "active", "C2_not_granted")
            contexts["C2"] = _lease_context(active2, identities["C2"])
            transient_secrets.append(contexts["C2"]["lease_token"])
            mutations.append(_mutate(proxies[1], "C2"))
            release2 = _tool_result(
                proxies[1],
                "session_release",
                {"lease_token": contexts["C2"]["lease_token"]},
            )
            _require(
                release2.get("released") is True
                and release2.get("cleanup_degraded") == [],
                "C2_release_failed",
            )

            active3 = _tool_result(
                proxies[2],
                "session_wait",
                {"ticket": acquired["C3"]["ticket"], "timeout_s": 5.0},
            )
            _require(active3.get("status") == "active", "C3_not_granted")
            contexts["C3"] = _lease_context(active3, identities["C3"])
            transient_secrets.append(contexts["C3"]["lease_token"])
            adopt3_code, adopt3 = _lifecycle_call(
                contexts["C3"], "adopt", run_id=run0, temp_dir=temp_dir
            )
            _require(
                adopt3_code == 0 and adopt3.get("state") == "RUNNING",
                "C3_adopt_failed",
            )
            mutations.append(_mutate(proxies[2], "C3"))

            heartbeat3 = _tool_result(
                proxies[2],
                "session_heartbeat",
                {"lease_token": contexts["C3"]["lease_token"]},
            )
            _require(heartbeat3.get("status") == "active", "C3_final_heartbeat_failed")

            owner_exit_started = time.monotonic()
            c3_exit = _close_proxy(proxies[2])
            closed_roles.add("C3")
            _require(
                c3_exit["exit_code"] == 0 and c3_exit["stdin_eof_exit"] is True,
                "C3_proxy_exit_not_natural_eof",
            )
            _require(_listener_pids(PORT) == listener_before, "listener_changed_on_owner_exit")
            _require(_process_counts().get("DayZDiag_x64") == 2, "game_changed_on_owner_exit")

            wait_slices: list[dict] = []
            active4: dict | None = None
            while time.monotonic() - owner_exit_started < EXPIRY_HARD_LIMIT_S:
                slice_started = time.monotonic()
                waited = _tool_result(
                    proxies[3],
                    "session_wait",
                    {
                        "ticket": acquired["C4"]["ticket"],
                        "timeout_s": WAIT_SLICE_S,
                    },
                    timeout=WAIT_SLICE_S + 15.0,
                )
                elapsed = time.monotonic() - owner_exit_started
                wait_slices.append(
                    {
                        "requested_s": WAIT_SLICE_S,
                        "duration_s": round(time.monotonic() - slice_started, 3),
                        "status": waited.get("status"),
                        "elapsed_s": round(elapsed, 3),
                    }
                )
                if waited.get("status") == "active":
                    active4 = waited
                    break
            _require(active4 is not None, "C4_not_granted_after_expiry")
            expiry_elapsed = time.monotonic() - owner_exit_started
            _require(
                OWNER_TTL_S - 2.0 <= expiry_elapsed <= EXPIRY_HARD_LIMIT_S,
                "expiry_outside_deadline",
            )
            contexts["C4"] = _lease_context(active4, identities["C4"])
            transient_secrets.append(contexts["C4"]["lease_token"])

            lifecycle_code, lifecycle_after_expiry = _lifecycle_call(
                contexts["C4"], "status", temp_dir=temp_dir
            )
            _require(lifecycle_code == 0, "lifecycle_status_after_expiry_failed")
            run0_state = next(
                (run for run in lifecycle_after_expiry.get("runs", []) if run.get("run_id") == run0),
                None,
            )
            _require(
                isinstance(run0_state, dict)
                and run0_state.get("state") == "RUNNING_IDLE"
                and run0_state.get("owner_session_id") is None,
                "run_not_idle_after_owner_expiry",
            )

            adopt4_code, adopt4 = _lifecycle_call(
                contexts["C4"], "adopt", run_id=run0, temp_dir=temp_dir
            )
            _require(
                adopt4_code == 0 and adopt4.get("state") == "RUNNING",
                "C4_adopt_failed",
            )
            mutations.append(_mutate(proxies[3], "C4"))

            retail_before_code, retail_before = _lifecycle_call(
                contexts["C4"], "status", temp_dir=temp_dir
            )
            _require(retail_before_code == 0, "retail_manifest_before_failed")

            retail_request = {
                "argv": [
                    str(
                        Path(
                            r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZ_x64.exe"
                        )
                    )
                ],
                "cwd": r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
                "role": "client",
                "window_style": "normal",
                "label": "H8 retail negative",
                "mod": "@MilitaryBunker",
                "profiles": str(ROOT.parent / "MilitaryBunker_dev" / "_client" / "profiles"),
                "mission": "dayzOffline.chernarusplus",
            }
            retail_code, retail = _lifecycle_call(
                contexts["C4"],
                "start",
                request=retail_request,
                temp_dir=temp_dir,
            )
            _require(
                retail_code == 1
                and retail.get("error") == "retail_manual_lifecycle_required",
                "retail_negative_failed",
            )
            retail_after_code, retail_after = _lifecycle_call(
                contexts["C4"], "status", temp_dir=temp_dir
            )
            _require(retail_after_code == 0, "retail_manifest_after_failed")
            _require(
                _manifest_shape(retail_after) == _manifest_shape(retail_before),
                "retail_manifest_changed",
            )
            _require(_process_counts().get("DayZ_x64") == 0, "retail_process_was_created")

            stop0_code, stop0 = _lifecycle_call(
                contexts["C4"], "stop", run_id=run0, temp_dir=temp_dir
            )
            _require(
                stop0_code == 0 and stop0.get("state") == "EXITED",
                "run0_stop_failed",
            )
            _wait_process_counts_zero()

            run1, launch1 = _start_fixture(
                proxies[3], contexts["C4"], temp_dir=temp_dir, tag="R1"
            )
            _require(run1 != run0, "replacement_run_id_not_new")
            run_ids.append(run1)
            ready1 = _wait_bridge_ready(proxies[3])
            _require(ready1["daemon_generation"] == generation, "generation_changed_on_replace")
            _require(_listener_pids(PORT) == listener_before, "listener_changed_on_replace")
            _require(_process_counts().get("DayZDiag_x64") == 2, "replacement_not_two_diag")

            stop1_code, stop1 = _lifecycle_call(
                contexts["C4"], "stop", run_id=run1, temp_dir=temp_dir
            )
            _require(
                stop1_code == 0 and stop1.get("state") == "EXITED",
                "run1_stop_failed",
            )
            zero_counts = _wait_process_counts_zero()
            lifecycle_final_code, lifecycle_final = _lifecycle_call(
                contexts["C4"], "status", temp_dir=temp_dir
            )
            _require(lifecycle_final_code == 0, "lifecycle_final_status_failed")
            active_runs = [
                run
                for run in lifecycle_final.get("runs", [])
                if run.get("state") in ACTIVE_RUN_STATES
            ]
            _require(active_runs == [], "active_runs_remain")

            release4 = _tool_result(
                proxies[3],
                "session_release",
                {"lease_token": contexts["C4"]["lease_token"]},
            )
            _require(
                release4.get("released") is True
                and release4.get("cleanup_degraded") == [],
                "C4_release_failed",
            )
            final_statuses = {
                role: _tool_result(proxies[index], "session_status", {})
                for index, role in ((0, "C1"), (1, "C2"), (3, "C4"))
            }
            _require(
                all(_clean_status(status) for status in final_statuses.values()),
                "final_session_status_not_clean",
            )

            prefixes = {identity["session_prefix"] for identity in identities.values()}
            events, audit_raw = _audit_documents(gate_started, generation, prefixes)
            audit_summary = _audit_summary(events)
            event_names = set(audit_summary["counts"])
            _require(
                EXPECTED_AUDIT_EVENTS.issubset(event_names),
                f"audit_events_missing:{sorted(EXPECTED_AUDIT_EVENTS - event_names)}",
            )
            _require(
                any(
                    event.get("event") == "session_expired"
                    and isinstance(event.get("client"), dict)
                    and event["client"].get("session")
                    == identities["C3"]["session_prefix"]
                    for event in events
                ),
                "C3_expiry_audit_missing",
            )
            mutation_order = [
                event.get("client", {}).get("session")
                for event in events
                if event.get("event") == "session_authorized"
                and event.get("command") == "notify_players"
            ]
            expected_order = [
                identities[role]["session_prefix"]
                for role in ("C1", "C2", "C3", "C4")
            ]
            _require(mutation_order[:4] == expected_order, "audit_mutation_order_wrong")
            _require(
                not _contains_value(audit_raw["raw_texts"], transient_secrets),
                "secret_value_found_in_audit",
            )
            _require(not _forbidden_key(events), "forbidden_field_found_in_audit")

            for index, role in ((0, "C1"), (1, "C2"), (3, "C4")):
                proxy_exit = _close_proxy(proxies[index])
                closed_roles.add(role)
                _require(proxy_exit["exit_code"] == 0, f"{role}_proxy_exit_not_clean")

            doctor_after = _doctor()
            _require(
                doctor_after == {"exit_code": 0, "ok": True, "finding_codes": []},
                "doctor_after_not_clean",
            )
            _require(_listener_pids(PORT) == listener_before, "listener_final_changed")
            _require(_udp_owners(GAME_PORT) == [], "udp_final_not_free")
            _require(not any(_process_counts().values()), "processes_final_not_zero")

            result.update(
                {
                    "finished_at_utc": _utc_now(),
                    "daemon_generation": generation,
                    "listener": {
                        "count": 1,
                        "pid": next(iter(listener_before)),
                        "stable_through_owner_exit": True,
                    },
                    "doctor_before": doctor_before,
                    "doctor_after": doctor_after,
                    "identities": {
                        role: {
                            key: identity[key]
                            for key in (
                                "session_prefix",
                                "session_sha256_12",
                                "pid",
                                "proxy_wrapper_pid",
                                "started_at_utc",
                                "platform",
                                "task_label",
                            )
                        }
                        for role, identity in identities.items()
                    },
                    "negative_without_lease": negative,
                    "launches": [launch0, launch1],
                    "bridge_ready": [ready0, ready1],
                    "parallel_reads": parallel_reads,
                    "fifo": {
                        "initial_positions": [1, 2, 3],
                        "mutations": mutations,
                        "normal_releases": ["C1", "C2", "C4"],
                        "audit_mutation_order": mutation_order[:4],
                    },
                    "owner_exit": {
                        **c3_exit,
                        "role": "C3",
                        "last_heartbeat_immediately_before_exit": True,
                        "grant_elapsed_s": round(expiry_elapsed, 3),
                        "wait_slices": wait_slices,
                        "run_state_after_expiry": "RUNNING_IDLE",
                    },
                    "lifecycle": {
                        "run0": run0,
                        "C3_adopt": adopt3.get("state"),
                        "C4_adopt": adopt4.get("state"),
                        "retail_negative": retail.get("error"),
                        "run0_stop": stop0.get("state"),
                        "run1": run1,
                        "run1_stop": stop1.get("state"),
                        "active_runs_final": 0,
                    },
                    "final": {
                        "status_clients_checked": ["C1", "C2", "C4"],
                        "owner": None,
                        "queue": [],
                        "pending_commands": 0,
                        "cleanup_degraded": [],
                        "process_counts": zero_counts,
                        "udp_2302_owners": [],
                    },
                    "audit": audit_summary,
                    "audit_secret_values_found": 0,
                    "audit_forbidden_fields_found": 0,
                    "overall_pass": True,
                }
            )
        except Exception as exc:
            cleanup = _cleanup_failed_gate(
                proxies, acquired, contexts, identities, temp_dir
            )
            raise GateFailure(
                f"{exc};failure_cleanup="
                + json.dumps(cleanup, ensure_ascii=False, separators=(",", ":"))
            ) from exc
        finally:
            for index, role in enumerate(("C1", "C2", "C3", "C4")):
                if index < len(proxies) and role not in closed_roles:
                    _close_proxy(proxies[index])
    return result


def main() -> int:
    try:
        result = _run()
    except Exception as exc:
        result = {
            "schema": "dayz-mcp-h8-real-v1",
            "composition": "4-Codex substitution",
            "finished_at_utc": _utc_now(),
            "overall_pass": False,
            "error": str(exc),
        }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "overall_pass": result.get("overall_pass"),
                "error": result.get("error"),
                "result_path": str(RESULT_PATH),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0 if result.get("overall_pass") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
