from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid

from dayz_mcp import accredited_daemon_transport as transport
from dayz_mcp import daemon_policy
from dayz_mcp import pinned_keyfile
from dayz_mcp.daemon_policy import AccreditedDaemonPolicy


MAX_REQUEST_STDIN_BYTES = 64 * 1024
_LAUNCH_FIELDS = frozenset(
    {"new_run_id", "launch_operation_id", "launch_request_sha256"}
)


def _request(
    policy: AccreditedDaemonPolicy,
    path: str,
    payload: dict[str, object],
) -> tuple[int, dict[str, object]]:
    if type(policy) is not AccreditedDaemonPolicy:
        raise ValueError("invalid_daemon_policy")
    policy.revalidate()
    key = pinned_keyfile.read_pinned_keyfile(policy.keyfile)
    status, response_body = transport.verified_daemon_http_request(
        host=policy.host,
        port=policy.port,
        key=key,
        method="POST",
        path=path,
        query={},
        body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        deadline=time.monotonic() + 15.0,
        expected_executable=policy.native_executable,
        expected_argv=list(policy.argv),
        expected_cwd=policy.cwd,
        max_response_bytes=transport.MAX_AUTHENTICATED_RESPONSE_BYTES,
    )
    decoded = json.loads(response_body.decode("utf-8") or "{}")
    if not isinstance(decoded, dict):
        raise ValueError("invalid_lifecycle_response")
    return status, decoded


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DayZ MCP managed lifecycle client")
    parser.add_argument(
        "--daemon-policy", choices=("normal", "bootstrap"), required=True
    )
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--request-stdin", action="store_true", required=True)
    stop = commands.add_parser("stop")
    stop.add_argument("--run-id", required=True)
    adopt = commands.add_parser("adopt")
    adopt.add_argument("--run-id", required=True)
    reap = commands.add_parser("reap")
    reap.add_argument("--run-id", required=True)
    commands.add_parser("status")
    return parser


def _read_start_request() -> dict[str, object]:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    raw = stream.read(MAX_REQUEST_STDIN_BYTES + 1)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > MAX_REQUEST_STDIN_BYTES:
        raise ValueError("request_stdin_too_large")
    request = json.loads(raw.decode("utf-8"))
    if not isinstance(request, dict):
        raise ValueError("invalid_request_stdin")
    if isinstance(request.get("run_id"), str) and request.get("run_id"):
        if _LAUNCH_FIELDS.intersection(request):
            raise ValueError("invalid_request_stdin")
        return request
    new_run_id = request.get("new_run_id")
    launch_operation_id = request.get("launch_operation_id")
    supplied_sha256 = request.get("launch_request_sha256")
    try:
        parsed_run_id = uuid.UUID(str(new_run_id))
        parsed_operation_id = uuid.UUID(str(launch_operation_id))
    except ValueError as exc:
        raise ValueError("invalid_request_stdin") from exc
    if (
        parsed_run_id.version != 4
        or parsed_operation_id.version != 4
        or str(parsed_run_id) != new_run_id
        or str(parsed_operation_id) != launch_operation_id
        or new_run_id == launch_operation_id
        or not isinstance(supplied_sha256, str)
        or len(supplied_sha256) != 64
        or supplied_sha256 != supplied_sha256.casefold()
    ):
        raise ValueError("invalid_request_stdin")
    canonical = json.dumps(
        {
            key: value
            for key, value in request.items()
            if key != "launch_request_sha256"
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != supplied_sha256:
        raise ValueError("invalid_request_stdin")
    return request


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    identity_text = os.environ.pop("DAYZ_MCP_CLIENT_ID_JSON", None)
    lease_token = os.environ.pop("DAYZ_MCP_LEASE_TOKEN", None)
    if not identity_text or not lease_token:
        print("missing_lifecycle_environment", file=sys.stderr)
        return 2
    try:
        policy = daemon_policy.load_daemon_policy(args.daemon_policy)
        identity = json.loads(identity_text)
        if not isinstance(identity, dict):
            raise ValueError("invalid_identity")
        payload: dict[str, object] = {
            "identity": identity,
            "lease_token": lease_token,
        }
        if args.command == "start":
            request = _read_start_request()
            payload["request"] = request
        elif args.command in {"stop", "adopt", "reap"}:
            payload["run_id"] = args.run_id
        status, result = _request(policy, f"/lifecycle/{args.command}", payload)
        if (
            args.command == "start"
            and 200 <= status < 300
            and "launch_operation_id" in request
        ):
            if (
                result.get("state") != "RUNNING"
                or result.get("run_id") != request["new_run_id"]
            ):
                raise ValueError("invalid_lifecycle_response")
            status, result = _request(
                policy,
                "/lifecycle/ack",
                {
                    "identity": identity,
                    "lease_token": lease_token,
                    "run_id": request["new_run_id"],
                    "launch_operation_id": request["launch_operation_id"],
                },
            )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        transport.AccreditedTransportError,
    ):
        print("lifecycle_request_failed", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
