from __future__ import annotations

import argparse
import json
import sys
import time

from dayz_mcp import accredited_daemon_transport as transport
from dayz_mcp import daemon_policy
from dayz_mcp import pinned_keyfile
from dayz_mcp.daemon_policy import AccreditedDaemonPolicy


def _request(
    policy: AccreditedDaemonPolicy,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    if type(policy) is not AccreditedDaemonPolicy:
        raise ValueError("invalid_daemon_policy")
    policy.revalidate()
    key = pinned_keyfile.read_pinned_keyfile(policy.keyfile)
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    status, response_body = transport.verified_daemon_http_request(
        host=policy.host,
        port=policy.port,
        key=key,
        method=method,
        path=path,
        query={},
        body=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        deadline=time.monotonic() + 10.0,
        expected_executable=policy.native_executable,
        expected_argv=list(policy.argv),
        expected_cwd=policy.cwd,
        max_response_bytes=transport.MAX_AUTHENTICATED_RESPONSE_BYTES,
    )
    decoded = json.loads(response_body.decode("utf-8") or "{}")
    if not isinstance(decoded, dict):
        raise ValueError("invalid_admin_response")
    return status, decoded


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive DayZ MCP administration")
    parser.add_argument(
        "--daemon-policy", choices=("normal", "bootstrap"), required=True
    )
    commands = parser.add_subparsers(dest="command", required=True)
    release = commands.add_parser("release")
    release.add_argument("--reason", required=True)
    audit_repair = commands.add_parser("audit-repair")
    audit_repair.add_argument("--reason", required=True)
    lifecycle_repair = commands.add_parser("lifecycle-recovery-repair")
    lifecycle_repair.add_argument("--reason", required=True)
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--reason", required=True)
    reconcile.add_argument("--run-id", required=True)
    target = reconcile.add_mutually_exclusive_group(required=True)
    target.add_argument("--pid", type=int)
    target.add_argument("--empty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not sys.stdin.isatty():
        print("interactive_tty_required", file=sys.stderr)
        return 2
    if not args.reason.strip():
        print("nonempty_reason_required", file=sys.stderr)
        return 2
    try:
        policy = daemon_policy.load_daemon_policy(args.daemon_policy)
        status_code, status = _request(policy, "GET", "/status")
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        transport.AccreditedTransportError,
    ):
        print("admin_request_failed", file=sys.stderr)
        return 2
    if status_code != 200:
        print("admin_status_unavailable", file=sys.stderr)
        return 2

    if args.command == "release":
        coordination = status.get("coordination")
        active = coordination.get("active") if isinstance(coordination, dict) else None
        lease_id = active.get("lease_id") if isinstance(active, dict) else None
        if not isinstance(lease_id, str) or not lease_id:
            print("no_active_lease", file=sys.stderr)
            return 2
        confirmation = f"FORCE {lease_id}"
        entered = input(f"Type {confirmation} to continue: ")
        if entered != confirmation:
            print("confirmation_mismatch", file=sys.stderr)
            return 3
        path = "/admin/release"
        payload: dict[str, object] = {
            "lease_id": lease_id,
            "reason": args.reason.strip(),
            "confirmation": confirmation,
        }
    elif args.command == "audit-repair":
        coordination = status.get("coordination")
        audit_fault = (
            coordination.get("audit_fault")
            if isinstance(coordination, dict)
            else None
        )
        fault_id = audit_fault.get("fault_id") if isinstance(audit_fault, dict) else None
        if not isinstance(fault_id, str) or not fault_id:
            print("no_coordination_audit_fault", file=sys.stderr)
            return 2
        confirmation = f"REPAIR {fault_id}"
        entered = input(f"Type {confirmation} to continue: ")
        if entered != confirmation:
            print("confirmation_mismatch", file=sys.stderr)
            return 3
        path = "/admin/audit-repair"
        payload = {
            "fault_id": fault_id,
            "reason": args.reason.strip(),
            "confirmation": confirmation,
        }
    elif args.command == "lifecycle-recovery-repair":
        coordination = status.get("coordination")
        recovery = (
            coordination.get("lifecycle_recovery_fault")
            if isinstance(coordination, dict)
            else None
        )
        header = recovery.get("fault") if isinstance(recovery, dict) else None
        pointer = recovery.get("pointer") if isinstance(recovery, dict) else None
        fault_id = header.get("fault_id") if isinstance(header, dict) else None
        expected_head = (
            pointer.get("head_event_sha256")
            if isinstance(pointer, dict)
            else None
        )
        if (
            not isinstance(fault_id, str)
            or not fault_id
            or not isinstance(expected_head, str)
            or len(expected_head) != 64
        ):
            print("no_lifecycle_recovery_fault", file=sys.stderr)
            return 2
        confirmation = f"REPAIR LIFECYCLE {fault_id}"
        entered = input(f"Type {confirmation} to continue: ")
        if entered != confirmation:
            print("confirmation_mismatch", file=sys.stderr)
            return 3
        path = "/admin/lifecycle-recovery-repair"
        payload = {
            "fault_id": fault_id,
            "expected_head_sha256": expected_head,
            "reason": args.reason.strip(),
            "confirmation": confirmation,
        }
    else:
        lifecycle = status.get("lifecycle")
        runs = lifecycle.get("runs") if isinstance(lifecycle, dict) else None
        matching = [
            run
            for run in runs or []
            if isinstance(run, dict) and run.get("run_id") == args.run_id
        ]
        registered = matching[0] if matching else None
        processes = registered.get("processes") if isinstance(registered, dict) else None
        exact_state = (
            isinstance(registered, dict)
            and (
                registered.get("state")
                in {"UNRECONCILED", "STARTING", "STOPPING"}
                or (
                    registered.get("state") == "RUNNING_IDLE"
                    and registered.get("owner_session_id") is None
                    and registered.get("owner_lease_id") is None
                )
            )
            and isinstance(processes, list)
        )
        target_registered = bool(
            exact_state
            and (
                (args.empty and not processes)
                or (
                    not args.empty
                    and any(
                        isinstance(item, dict) and item.get("pid") == args.pid
                        for item in processes
                    )
                )
            )
        )
        if not target_registered:
            print("reconcile_target_not_registered", file=sys.stderr)
            return 2
        confirmation = (
            f"FORCE {args.run_id} EMPTY"
            if args.empty
            else f"FORCE {args.run_id} {args.pid}"
        )
        entered = input(f"Type {confirmation} to continue: ")
        if entered != confirmation:
            print("confirmation_mismatch", file=sys.stderr)
            return 3
        path = "/admin/reconcile"
        payload = {
            "run_id": args.run_id,
            "reason": args.reason.strip(),
            "confirmation": confirmation,
        }
        if args.empty:
            payload["empty"] = True
        else:
            payload["pid"] = args.pid

    try:
        result_code, result = _request(policy, "POST", path, payload)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        transport.AccreditedTransportError,
    ):
        print("admin_request_failed", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if 200 <= result_code < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
