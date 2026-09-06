from __future__ import annotations

import asyncio
import json
import msvcrt
import os
import struct
import sys
import threading
import time
from pathlib import Path

from dayz_mcp import dayz_test_readiness, dayz_test_request, dayz_test_worker
from dayz_mcp import native_broker_protocol


_MAX_MESSAGE = 65_536
_IDENTITY = "DAYZ_MCP_CLIENT_ID_JSON"
_TOKEN = "DAYZ_MCP_LEASE_TOKEN"
_CANCEL_HANDLE = "DAYZ_MCP_CANCEL_HANDLE"
_BROKER_HEADER = struct.Struct("<4sBBHII32s")


def _read_exact(stream: object, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not isinstance(chunk, bytes) or not chunk:
            raise RuntimeError("broker_closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_frame(stream: object) -> bytes:
    header = _read_exact(stream, _BROKER_HEADER.size)
    _magic, _version, _kind, _flags, payload_size, stdin_size, _sha = _BROKER_HEADER.unpack(header)
    size = _BROKER_HEADER.size + payload_size + stdin_size
    if not _BROKER_HEADER.size <= size <= _MAX_MESSAGE:
        raise RuntimeError("broker_frame_invalid")
    return header + _read_exact(stream, size - _BROKER_HEADER.size)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_worker_terminal(
    exit_code: int,
    run_id: str | None,
    error_code: str | None,
    cleanup_degraded: bool,
) -> None:
    terminal = b"DZW1" + _canonical(
        {
            "cleanup_degraded": cleanup_degraded,
            "error_code": error_code,
            "exit_code": exit_code,
            "ok": exit_code == 0,
            "run_id": run_id,
        }
    )
    sys.stdout.buffer.write(struct.pack("<I", len(terminal)))
    sys.stdout.buffer.write(terminal)
    sys.stdout.buffer.flush()


def _take_cancel_handle() -> int:
    raw = os.environ.pop(_CANCEL_HANDLE, None)
    if (
        not isinstance(raw, str)
        or not raw
        or not raw.isascii()
        or not raw.isdecimal()
    ):
        raise RuntimeError("cancel_handle_invalid")
    value = int(raw, 10)
    if value <= 0 or str(value) != raw:
        raise RuntimeError("cancel_handle_invalid")
    return value


def _start_cancel_watcher(
    handle: int,
    loop: asyncio.AbstractEventLoop,
    cancel_event: asyncio.Event,
) -> None:
    def watch() -> None:
        descriptor = -1
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
            os.read(descriptor, 1)
        except OSError:
            pass
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        try:
            loop.call_soon_threadsafe(cancel_event.set)
        except RuntimeError:
            pass

    threading.Thread(
        target=watch,
        name="dayz-native-cancel",
        daemon=True,
    ).start()


class _PipeBroker:
    def __init__(self) -> None:
        self._request = sys.stdout.buffer
        self._response = sys.stdin.buffer
        self._lock = threading.Lock()

    def _roundtrip(self, frame: bytes) -> dict[str, object]:
        if not isinstance(frame, bytes) or not 48 <= len(frame) <= _MAX_MESSAGE:
            raise RuntimeError("broker_frame_invalid")
        with self._lock:
            self._request.write(struct.pack("<I", len(frame)))
            self._request.write(frame)
            self._request.flush()
            size = struct.unpack("<I", _read_exact(self._response, 4))[0]
            if not 2 <= size <= _MAX_MESSAGE:
                raise RuntimeError("broker_response_invalid")
            raw = _read_exact(self._response, size)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict) or _canonical(value) != raw:
            raise RuntimeError("broker_response_invalid")
        return value

    async def invoke(self, frame: bytes) -> dict[str, object]:
        return await asyncio.to_thread(self._roundtrip, frame)


def _bundle_root() -> Path:
    return Path(sys.executable).resolve(strict=True).parent.parent


def _semantic_policies() -> tuple[dayz_test_request.RequestProjectPolicy, ...]:
    raw = (_bundle_root() / "request-policy.json").read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict) or set(value) != {"format_version", "projects"} or value["format_version"] != 1:
        raise RuntimeError("request_policy_invalid")
    projects = value["projects"]
    if not isinstance(projects, list) or not projects:
        raise RuntimeError("request_policy_invalid")
    policies: list[dayz_test_request.RequestProjectPolicy] = []
    for project in projects:
        if not isinstance(project, dict) or set(project) != {
            "default_base_mods", "default_source", "dev_root", "mission_roots", "mod", "mod_roots"
        }:
            raise RuntimeError("request_policy_invalid")
        policies.append(
            dayz_test_request.RequestProjectPolicy(
                mod=project["mod"],
                dev_root=project["dev_root"]["path"],
                default_source=project["default_source"]["path"],
                default_base_mods=tuple(project["default_base_mods"]),
                mission_roots=tuple(item["path"] for item in project["mission_roots"]),
                mod_roots=tuple(item["path"] for item in project["mod_roots"]),
            )
        )
    return tuple(policies)


def _worker_runtime(mod: str, dev_root: str) -> dayz_test_worker.WorkerRuntimePolicy:
    value = json.loads((_bundle_root() / "worker-runtime.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"format_version", "projects"} or value["format_version"] != 1:
        raise RuntimeError("worker_runtime_invalid")
    matches = [
        item for item in value["projects"]
        if isinstance(item, dict) and item.get("mod") == mod and item.get("dev_root") == dev_root
    ]
    if len(matches) != 1:
        raise RuntimeError("worker_runtime_invalid")
    item = matches[0]
    if set(item) != {
        "build_source_basename", "build_temp_root", "dev_root", "diag_executable",
        "game_directory", "mission_aliases", "mod", "mods_root"
    } or not isinstance(item["mission_aliases"], dict) or not {"chernarus", "livonia", "sakhal"}.issubset(item["mission_aliases"]) or not all(type(key) is str and key for key in item["mission_aliases"]):
        raise RuntimeError("worker_runtime_invalid")
    return dayz_test_worker.WorkerRuntimePolicy(
        dev_root=item["dev_root"],
        mod=item["mod"],
        diag_executable=item["diag_executable"],
        game_directory=item["game_directory"],
        mission_aliases=tuple(sorted(item["mission_aliases"].items())),
        mods_root=item["mods_root"],
        build_temp_root=item["build_temp_root"],
        build_source_basename=item["build_source_basename"],
    )


async def _worker_main() -> int:
    if os.environ.pop(_IDENTITY, None) is not None or os.environ.pop(_TOKEN, None) is not None:
        raise RuntimeError("worker_received_secret")
    cancel_handle = _take_cancel_handle()
    cancel_event = asyncio.Event()
    _start_cancel_watcher(cancel_handle, asyncio.get_running_loop(), cancel_event)
    bootstrap = native_broker_protocol.decode_request(_read_frame(sys.stdin.buffer))
    if bootstrap.kind is not native_broker_protocol.BrokerKind.PRIVATE_WORKER:
        raise RuntimeError("request_invalid")
    request = bootstrap.stdin
    policies = _semantic_policies()
    parsed = dayz_test_request.parse_dayz_test_request(request, policies=policies)
    if bootstrap.payload.get("request_sha256") != parsed.sha256:
        raise RuntimeError("request_invalid")
    selected = next(
        policy
        for policy in policies
        if policy.mod == parsed.payload["mod"] and policy.dev_root == parsed.payload["dev_root"]
    )
    broker = _PipeBroker()
    runtime = _worker_runtime(selected.mod, selected.dev_root)

    async def readiness(
        run_id: str,
        port: int,
        timeout_s: int,
    ) -> dayz_test_readiness.ReadinessResult:
        import psutil

        return await dayz_test_readiness.wait_for_owned_udp(
            broker, run_id, port, timeout_s, psutil_module=psutil
        )

    worker_task = asyncio.create_task(
        dayz_test_worker.execute_dayz_test_worker(
            parsed.canonical_bytes,
            request_sha256=parsed.sha256,
            request_policies=policies,
            runtime_policy=runtime,
            broker=broker,
            readiness_probe=readiness,
            cancel_event=cancel_event,
        )
    )
    cancel_task = asyncio.create_task(cancel_event.wait())
    await asyncio.sleep(0)
    try:
        done, _pending = await asyncio.wait(
            (worker_task, cancel_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_task in done and cancel_task.result() and not worker_task.done():
            worker_task.cancel()
        result = await worker_task
    finally:
        cancel_task.cancel()
        try:
            await cancel_task
        except asyncio.CancelledError:
            pass
    _write_worker_terminal(result.exit_code, result.run_id, None, False)
    return result.exit_code


def _lifecycle_main() -> int:
    from dayz_mcp import accredited_daemon_transport, normal_daemon_policy
    from dayz_mcp import pinned_keyfile

    identity_text = os.environ.pop(_IDENTITY, None)
    lease_token = os.environ.pop(_TOKEN, None)
    if not identity_text or not lease_token:
        raise RuntimeError("lifecycle_secret_missing")
    identity = json.loads(identity_text)
    if not isinstance(identity, dict):
        raise RuntimeError("lifecycle_identity_invalid")
    request = native_broker_protocol.decode_request(_read_frame(sys.stdin.buffer))
    if request.kind is not native_broker_protocol.BrokerKind.LIFECYCLE_CLI:
        raise RuntimeError("lifecycle_frame_invalid")
    command = request.payload["command"]
    payload: dict[str, object] = {"identity": identity, "lease_token": lease_token}
    if command == "start":
        lifecycle_request = json.loads(request.stdin.decode("utf-8"))
        if not isinstance(lifecycle_request, dict) or _canonical(lifecycle_request) != request.stdin:
            raise RuntimeError("lifecycle_request_invalid")
        payload["request"] = lifecycle_request
    elif command in {"stop", "adopt", "reap"}:
        payload["run_id"] = request.payload["run_id"]
    elif command == "ack":
        payload["run_id"] = request.payload["run_id"]
        payload["launch_operation_id"] = request.payload["launch_operation_id"]
    elif command == "status":
        if request.payload["run_id"] is not None:
            payload["run_id"] = request.payload["run_id"]
    else:
        raise RuntimeError("lifecycle_command_invalid")
    policy = normal_daemon_policy.load_inherited_normal_daemon_policy()
    policy.revalidate()
    key = pinned_keyfile.read_pinned_keyfile(policy.keyfile)
    status, response_body = accredited_daemon_transport.verified_daemon_http_request(
        host=policy.host,
        port=policy.port,
        key=key,
        method="POST",
        path=f"/lifecycle/{command}",
        query={},
        body=_canonical(payload),
        headers={"Content-Type": "application/json"},
        deadline=time.monotonic() + 15.0,
        expected_executable=policy.native_executable,
        expected_argv=list(policy.argv),
        expected_cwd=policy.cwd,
        max_response_bytes=accredited_daemon_transport.MAX_AUTHENTICATED_RESPONSE_BYTES,
    )
    result = json.loads(response_body.decode("utf-8") or "{}")
    if not isinstance(result, dict):
        raise RuntimeError("lifecycle_response_invalid")
    sys.stdout.buffer.write(_canonical(result))
    sys.stdout.buffer.flush()
    return 0 if 200 <= status < 300 else 1


def main() -> int:
    arguments = sys.argv[1:]
    try:
        if arguments == ["--lifecycle-child"]:
            return _lifecycle_main()
        if arguments:
            return 2
        return asyncio.run(_worker_main())
    except BaseException as error:
        if isinstance(error, KeyboardInterrupt):
            return 130
        if not arguments:
            try:
                if isinstance(error, dayz_test_worker.DayzTestWorkerError):
                    error_code = error.code
                    run_id = error.run_id
                    cleanup_degraded = error.cleanup_degraded
                elif isinstance(error, asyncio.CancelledError):
                    error_code = "operation_cancelled"
                    run_id = None
                    cleanup_degraded = False
                else:
                    error_code = "internal_failure"
                    run_id = None
                    cleanup_degraded = False
                _write_worker_terminal(
                    2, run_id, error_code, cleanup_degraded
                )
            except BaseException:
                pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
