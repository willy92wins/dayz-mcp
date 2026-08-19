"""Synthetic fencing probe (gate D-55.9).

The old canary launched a second DayZDiag with a copied profile so two
PIDs would present the same instance. That is unworkable: both clients
share a Steam ID, so connecting the second disconnects the first.

The fence is decided in the daemon. ``ServerState.resolve_poll_pid``
attributes a /poll from the TCP table with no check that the process is
DayZDiag, and ``_note_poll_pid_locked`` flips a BOUND binding to
AMBIGUOUS on a single poll whose ``source_pid`` differs from
``binding.pid``. This process is that second PID.

Does not launch or kill DayZ. Unit tests drive ``run_probe_on_state``
against an in-process ServerState and must not open port 8765.

Invocation (live daemon; the operator fires this, not the unit suite)::

    .\\.venv-mcp\\Scripts\\python.exe checks\\fence_canary_probe.py --client-profiles <client-profiles-dir> --port 8765 --key <daemon-key> --json-out fence-canary-verdict.json

If ``--key`` is omitted, the key is read from ``.dayz_mcp.key`` next to
this ``checks\\`` directory. Default port is 8765.

Exit codes: 0=PASS, 1=FAIL, 2=UNMEASURABLE.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from dayz_mcp.core import EXPECTED_BRIDGE_VERSION, build_status
from dayz_mcp.instance_fence import (
    BINDING_AMBIGUOUS,
    BINDING_BOUND,
    instance_prefix,
)


VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_UNMEASURABLE = "UNMEASURABLE"
DEFAULT_PORT = 8765
DEFAULT_PEER = "client"
DEFAULT_TIMEOUT_S = 5.0
MUTATION_CMD = "camera_set"
MUTATION_ARGS = {"cam_mode": "orient"}
_AUTH_OR_LEASE = frozenset(
    {"lease_required", "invalid_identity", "unauthorized", "lease_invalid"}
)


def default_key_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".dayz_mcp.key"


def load_key(explicit: str | None, key_file: str | None = None) -> str:
    if explicit:
        return explicit
    path = Path(key_file) if key_file else default_key_path()
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("empty_key")
    return text


def read_profile_instance(client_profiles: str | Path) -> str:
    path = Path(client_profiles) / "dayz_mcp.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("profile_unreadable") from exc
    if not isinstance(payload, dict):
        raise ValueError("profile_unreadable")
    instance = payload.get("instance")
    if not isinstance(instance, str) or instance_prefix(instance) is None:
        raise ValueError("instance_missing")
    return instance


def status_url(port: int, key: str) -> str:
    return _url(port, "/status", key)


def poll_url(
    port: int,
    key: str,
    *,
    peer: str,
    ver: str,
    inst: str,
) -> str:
    return _url(port, "/poll", key, {"peer": peer, "ver": ver, "inst": inst})


def enqueue_url(port: int, key: str) -> str:
    return _url(port, "/enqueue", key)


def _url(
    port: int,
    path: str,
    key: str,
    extra: dict[str, str] | None = None,
) -> str:
    params = {"key": key}
    if extra:
        params.update(extra)
    return f"http://127.0.0.1:{int(port)}{path}?{urllib.parse.urlencode(params)}"


def client_binding_state(status: dict) -> str | None:
    if not isinstance(status, dict):
        return None
    peer = status.get("client_peer")
    if not isinstance(peer, dict):
        return None
    state = peer.get("binding_state")
    if isinstance(state, str) and state:
        return state
    return None


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def fence_view(status: dict | None) -> dict[str, object]:
    if not isinstance(status, dict):
        return {
            "client_binding_state": None,
            "instance_ambiguous": 0,
            "unaccredited_mutation_enqueues": 0,
        }
    fence = status.get("fence")
    if not isinstance(fence, dict):
        fence = {}
    rejects = fence.get("mutation_rejects_by_code")
    if not isinstance(rejects, dict):
        rejects = {}
    return {
        "client_binding_state": client_binding_state(status),
        "instance_ambiguous": _as_int(rejects.get("instance_ambiguous")),
        "unaccredited_mutation_enqueues": _as_int(
            fence.get("unaccredited_mutation_enqueues")
        ),
    }


def evaluate_verdict(
    *,
    before: dict | None,
    after: dict | None,
    mutation_status: int | None,
    mutation_body: dict | None = None,
    reachable: bool = True,
) -> dict:
    if not reachable:
        return {"verdict": VERDICT_UNMEASURABLE, "reasons": ["daemon_unreachable"]}
    if not isinstance(before, dict):
        return {"verdict": VERDICT_UNMEASURABLE, "reasons": ["status_unreadable"]}
    before_view = fence_view(before)
    starting = before_view["client_binding_state"]
    if starting is None:
        return {"verdict": VERDICT_UNMEASURABLE, "reasons": ["no_client_peer"]}
    if starting != BINDING_BOUND:
        return {
            "verdict": VERDICT_UNMEASURABLE,
            "reasons": [f"starting_state_{starting}"],
        }
    if not isinstance(after, dict):
        return {"verdict": VERDICT_UNMEASURABLE, "reasons": ["after_status_unreadable"]}
    after_view = fence_view(after)
    mutation_error = None
    if isinstance(mutation_body, dict):
        error = mutation_body.get("error")
        if isinstance(error, str):
            mutation_error = error
    reject_incremented = (
        after_view["instance_ambiguous"] > before_view["instance_ambiguous"]
    )
    mutation_at_fence = (
        mutation_status == 409 and mutation_error == "instance_ambiguous"
    ) or reject_incremented
    if not mutation_at_fence:
        if mutation_status is None:
            return {
                "verdict": VERDICT_UNMEASURABLE,
                "reasons": ["mutation_not_attempted"],
            }
        if mutation_error in _AUTH_OR_LEASE or mutation_status in {401, 403, 423}:
            return {
                "verdict": VERDICT_UNMEASURABLE,
                "reasons": [
                    f"mutation_not_at_fence:{mutation_error or mutation_status}"
                ],
            }
    reasons: list[str] = []
    if after_view["client_binding_state"] != BINDING_AMBIGUOUS:
        reasons.append("binding_not_ambiguous")
    if not reject_incremented:
        reasons.append("instance_ambiguous_did_not_increment")
    if (
        after_view["unaccredited_mutation_enqueues"]
        > before_view["unaccredited_mutation_enqueues"]
    ):
        reasons.append("unaccredited_mutation_enqueues_incremented")
    if mutation_status == 200:
        reasons.append("mutation_accepted")
    if reasons:
        return {"verdict": VERDICT_FAIL, "reasons": reasons}
    return {"verdict": VERDICT_PASS, "reasons": []}


def public_status(state: object) -> dict:
    snapshot = state.status_snapshot()  # type: ignore[attr-defined]
    return build_status(
        snapshot,
        require_version=False,
        expected_game_version=None,
    )


def _finalize(
    verdict: dict,
    *,
    before: dict | None,
    after: dict | None,
    mutation_status: int | None,
    mutation_body: dict | None,
    source_pid: int | None,
    instance: str,
) -> dict:
    result = dict(verdict)
    result["before"] = fence_view(before)
    result["after"] = fence_view(after)
    result["mutation_status"] = mutation_status
    error = None
    if isinstance(mutation_body, dict):
        value = mutation_body.get("error")
        if isinstance(value, str):
            error = value
    result["mutation_error"] = error
    result["source_pid"] = source_pid
    result["instance_prefix"] = instance_prefix(instance)
    return result


def run_probe_on_state(
    state: object,
    *,
    instance: str,
    peer: str = DEFAULT_PEER,
    version: str | None = None,
    source_pid: int | None = None,
    sock: object = None,
) -> dict:
    """Drive the gate against a production ServerState (no HTTP).

    ``source_pid`` is what Handler._handle_poll passes to record_poll after
    resolve_poll_pid. When omitted, this calls resolve_poll_pid itself — the
    path a live /poll uses.
    """
    try:
        before = public_status(state)
    except Exception:
        return _finalize(
            evaluate_verdict(
                before=None,
                after=None,
                mutation_status=None,
                reachable=False,
            ),
            before=None,
            after=None,
            mutation_status=None,
            mutation_body=None,
            source_pid=None,
            instance=instance,
        )
    if client_binding_state(before) != BINDING_BOUND:
        verdict = evaluate_verdict(
            before=before,
            after=before,
            mutation_status=None,
            mutation_body=None,
        )
        return _finalize(
            verdict,
            before=before,
            after=before,
            mutation_status=None,
            mutation_body=None,
            source_pid=None,
            instance=instance,
        )
    if source_pid is not None:
        pid = source_pid
    else:
        pid = state.resolve_poll_pid(instance, sock)  # type: ignore[attr-defined]
    state.record_poll(  # type: ignore[attr-defined]
        peer, version, instance=instance, source_pid=pid
    )
    mutation_status, mutation_body = state.enqueue_command(  # type: ignore[attr-defined]
        MUTATION_CMD, dict(MUTATION_ARGS), peer=peer
    )
    after = public_status(state)
    body = mutation_body if isinstance(mutation_body, dict) else None
    verdict = evaluate_verdict(
        before=before,
        after=after,
        mutation_status=mutation_status,
        mutation_body=body,
    )
    return _finalize(
        verdict,
        before=before,
        after=after,
        mutation_status=mutation_status,
        mutation_body=body,
        source_pid=pid if isinstance(pid, int) else None,
        instance=instance,
    )


class DaemonUnreachable(Exception):
    pass


def http_json(
    method: str,
    url: str,
    payload: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> tuple[int, dict]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8") or "{}"
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            return int(response.status), body
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8") or "{}"
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            return int(exc.code), body
        finally:
            exc.close()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DaemonUnreachable(str(exc)) from exc


def _probe_identity() -> dict[str, object]:
    started = datetime.now(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    pid = os.getpid()
    ppid = os.getppid() if hasattr(os, "getppid") else 0
    return {
        "platform": "unknown",
        "pid": pid,
        "ppid": ppid if isinstance(ppid, int) and ppid >= 0 else 0,
        "started_at_utc": started,
        "session_id": f"fence-canary-probe-{pid}",
        "task_label": "fence-canary-probe",
    }


def _poll_version(status: dict) -> str:
    peer = status.get("client_peer")
    if isinstance(peer, dict):
        version = peer.get("version")
        if isinstance(version, str) and "~" in version:
            return version
    return f"{EXPECTED_BRIDGE_VERSION}~unknown"


def _enqueue_mutation_http(
    port: int,
    key: str,
    peer: str,
    timeout: float,
) -> tuple[int, dict]:
    identity = _probe_identity()
    url = enqueue_url(port, key)
    status, body = http_json(
        "POST",
        url,
        {
            "cmd": MUTATION_CMD,
            "args": dict(MUTATION_ARGS),
            "peer": peer,
            "identity": identity,
        },
        timeout=timeout,
    )
    if body.get("error") != "lease_required":
        return status, body
    acquire_url = _url(port, "/session/acquire", key)
    acq_status, acq_body = http_json(
        "POST",
        acquire_url,
        {"identity": identity, "purpose": "fence-canary-probe"},
        timeout=timeout,
    )
    token = acq_body.get("lease_token") if acq_status == 200 else None
    if not isinstance(token, str) or not token:
        return status, body
    try:
        return http_json(
            "POST",
            url,
            {
                "cmd": MUTATION_CMD,
                "args": dict(MUTATION_ARGS),
                "peer": peer,
                "identity": identity,
                "lease_token": token,
            },
            timeout=timeout,
        )
    finally:
        http_json(
            "POST",
            _url(port, "/session/release", key),
            {"identity": identity, "lease_token": token},
            timeout=timeout,
        )


def run_http_probe(
    *,
    port: int,
    key: str,
    instance: str,
    peer: str = DEFAULT_PEER,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict:
    try:
        status_code, before = http_json(
            "GET", status_url(port, key), timeout=timeout
        )
    except DaemonUnreachable:
        return _finalize(
            evaluate_verdict(
                before=None,
                after=None,
                mutation_status=None,
                reachable=False,
            ),
            before=None,
            after=None,
            mutation_status=None,
            mutation_body=None,
            source_pid=os.getpid(),
            instance=instance,
        )
    if status_code != 200:
        return _finalize(
            evaluate_verdict(
                before=None,
                after=None,
                mutation_status=None,
                reachable=False,
            ),
            before=None,
            after=None,
            mutation_status=None,
            mutation_body=None,
            source_pid=os.getpid(),
            instance=instance,
        )
    if client_binding_state(before) != BINDING_BOUND:
        verdict = evaluate_verdict(
            before=before,
            after=before,
            mutation_status=None,
            mutation_body=None,
        )
        return _finalize(
            verdict,
            before=before,
            after=before,
            mutation_status=None,
            mutation_body=None,
            source_pid=os.getpid(),
            instance=instance,
        )
    version = _poll_version(before)
    try:
        http_json(
            "GET",
            poll_url(port, key, peer=peer, ver=version, inst=instance),
            timeout=timeout,
        )
    except DaemonUnreachable:
        return _finalize(
            evaluate_verdict(
                before=before,
                after=None,
                mutation_status=None,
                reachable=False,
            ),
            before=before,
            after=None,
            mutation_status=None,
            mutation_body=None,
            source_pid=os.getpid(),
            instance=instance,
        )
    try:
        mutation_status, mutation_body = _enqueue_mutation_http(
            port, key, peer, timeout
        )
    except DaemonUnreachable:
        mutation_status, mutation_body = None, None
    try:
        after_code, after = http_json(
            "GET", status_url(port, key), timeout=timeout
        )
    except DaemonUnreachable:
        after_code, after = 0, None
    if after_code != 200:
        after = None
    verdict = evaluate_verdict(
        before=before,
        after=after,
        mutation_status=mutation_status,
        mutation_body=mutation_body,
        reachable=True,
    )
    return _finalize(
        verdict,
        before=before,
        after=after,
        mutation_status=mutation_status,
        mutation_body=mutation_body,
        source_pid=os.getpid(),
        instance=instance,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Synthetic fencing probe: one /poll with the client's instance "
            "from this PID, then one mutation, then a PASS/FAIL/UNMEASURABLE "
            "verdict from /status."
        )
    )
    parser.add_argument("--client-profiles", required=True)
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--peer", default=DEFAULT_PEER)
    parser.add_argument("--key", default=None)
    parser.add_argument("--key-file", default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    return parser


def _emit(result: dict, json_out: str | None) -> None:
    text = json.dumps(result, indent=2, ensure_ascii=False)
    sys.stdout.write(text + "\n")
    if json_out:
        Path(json_out).write_text(text + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        key = load_key(args.key, args.key_file)
        instance = read_profile_instance(args.client_profiles)
    except (OSError, ValueError) as exc:
        result = {
            "verdict": VERDICT_UNMEASURABLE,
            "reasons": [str(exc)],
        }
        _emit(result, args.json_out)
        return 2
    result = run_http_probe(
        port=int(args.port),
        key=key,
        instance=instance,
        peer=str(args.peer),
        timeout=float(args.timeout),
    )
    _emit(result, args.json_out)
    verdict = result.get("verdict")
    if verdict == VERDICT_PASS:
        return 0
    if verdict == VERDICT_UNMEASURABLE:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
