"""Private, non-launching dayz-test worker driven only through the native broker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import ntpath
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dayz_mcp import dayz_test_readiness, dayz_test_request, native_broker_protocol


_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


PRE_ADMISSION_REJECTION_CODES = frozenset({"active_run_exists"})


WORKER_ERROR_CODES = frozenset(
    {
        "build_failed",
        "build_source_unavailable",
        "internal_failure",
        "operation_cancelled",
        "readiness_failed",
        "request_integrity_failed",
        "run_not_adoptable",
        "run_stop_failed",
        "runtime_policy_invalid",
        "worker_failed",
        "worker_identity_failed",
    }
) | PRE_ADMISSION_REJECTION_CODES | dayz_test_readiness.READINESS_ERROR_CODES


class DayzTestWorkerError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        run_id: str | None = None,
        cleanup_degraded: bool = False,
    ) -> None:
        if code not in WORKER_ERROR_CODES or type(cleanup_degraded) is not bool:
            raise ValueError("invalid_worker_error")
        if cleanup_degraded and not isinstance(run_id, str):
            raise ValueError("invalid_worker_error")
        super().__init__(code)
        self.code = code
        self.run_id = run_id
        self.cleanup_degraded = cleanup_degraded


class Broker(Protocol):
    async def invoke(self, frame: bytes) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class WorkerRuntimePolicy:
    dev_root: str
    mod: str
    diag_executable: str
    game_directory: str
    mission_aliases: tuple[tuple[str, str], ...]
    mods_root: str
    build_temp_root: str
    # Optional per-project build gate: when set, build requests must name a
    # source directory with exactly this basename (case-insensitive).
    build_source_basename: str | None


@dataclass(frozen=True, slots=True)
class WorkerResult:
    exit_code: int
    run_id: str | None


def _failed(
    code: str = "worker_failed",
    *,
    run_id: str | None = None,
    cleanup_degraded: bool = False,
) -> DayzTestWorkerError:
    return DayzTestWorkerError(
        code, run_id=run_id, cleanup_degraded=cleanup_degraded
    )


def _failure_after_cleanup(
    error: BaseException,
    *,
    run_id: str | None,
    cleanup_degraded: bool,
) -> DayzTestWorkerError:
    if isinstance(error, DayzTestWorkerError):
        if error.cleanup_degraded:
            return error
        code = error.code
    elif isinstance(error, asyncio.CancelledError):
        code = "operation_cancelled"
    else:
        code = "worker_failed"
    return _failed(
        code,
        run_id=run_id if cleanup_degraded else None,
        cleanup_degraded=cleanup_degraded,
    )


def _local_path(value: object) -> bool:
    if not isinstance(value, str) or not 3 <= len(value) <= 520:
        return False
    drive, tail = ntpath.splitdrive(value)
    return (
        len(drive) == 2
        and drive[0].isascii()
        and drive[0].isalpha()
        and drive[1] == ":"
        and tail.startswith("\\")
        and ":" not in tail
        and ntpath.normpath(value) == value
    )


_BUILD_SOURCE_BASENAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")


def _valid_build_source_basename(value: object) -> bool:
    if value is None:
        return True
    return type(value) is str and _BUILD_SOURCE_BASENAME.fullmatch(value) is not None


def _validate_runtime(
    runtime: WorkerRuntimePolicy,
    parsed: dayz_test_request.ParsedDayzTestRequest,
) -> WorkerRuntimePolicy:
    aliases = dict(runtime.mission_aliases)
    if (
        type(runtime) is not WorkerRuntimePolicy
        or runtime.dev_root != parsed.payload.get("dev_root")
        or runtime.mod != parsed.payload.get("mod")
        or not {"chernarus", "livonia", "sakhal"}.issubset(aliases)
        or not all(type(key) is str and key for key in aliases)
        or len(aliases) != len(runtime.mission_aliases)
        or not all(
            _local_path(path)
            for path in (
                runtime.dev_root,
                runtime.diag_executable,
                runtime.game_directory,
                runtime.mods_root,
                runtime.build_temp_root,
                *aliases.values(),
            )
        )
        or not _valid_build_source_basename(runtime.build_source_basename)
    ):
        raise _failed("runtime_policy_invalid")
    return runtime


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _new_id(id_fn: Callable[[], str]) -> str:
    value = id_fn()
    if not isinstance(value, str) or _UUID4.fullmatch(value) is None:
        raise _failed("worker_identity_failed")
    return value


def _mission(payload: dict[str, object], runtime: WorkerRuntimePolicy) -> str:
    value = payload.get("mission")
    if isinstance(value, str) and value in dict(runtime.mission_aliases):
        return dict(runtime.mission_aliases)[value]
    if not _local_path(value):
        raise _failed("runtime_policy_invalid")
    return str(value)


def _mod_path(value: object, runtime: WorkerRuntimePolicy) -> str:
    if not isinstance(value, str) or not value:
        raise _failed("runtime_policy_invalid")
    if ntpath.isabs(value):
        return ntpath.normpath(value)
    return ntpath.join(runtime.mods_root, value)


def _mods(payload: dict[str, object], runtime: WorkerRuntimePolicy) -> str:
    values = [
        *list(payload.get("base_mods", [])),
        "@" + runtime.mod,
        *list(payload.get("extra_mods", [])),
    ]
    return ";".join(_mod_path(value, runtime) for value in values)


def _start_core(
    payload: dict[str, object],
    runtime: WorkerRuntimePolicy,
    *,
    role: str,
    run_id: str | None,
) -> dict[str, object]:
    mission = _mission(payload, runtime)
    server_root = ntpath.join(runtime.dev_root, "_server")
    client_root = ntpath.join(runtime.dev_root, "_client")
    server_profiles = ntpath.join(server_root, "profiles")
    client_profiles = ntpath.join(client_root, "profiles")
    mod_string = _mods(payload, runtime)
    port = int(payload["port"])
    if role == "server":
        argv = [
            runtime.diag_executable,
            "-server",
            "-config=" + ntpath.join(server_root, "serverDZ.cfg"),
            "-profiles=" + server_profiles,
            "-mission=" + mission,
            "-mod=" + mod_string,
        ]
        if not payload["no_file_patching"]:
            argv.append("-filePatching")
        argv.append(f"-port={port}")
        server_mods = list(payload.get("server_mods", []))
        if server_mods:
            argv.append(
                "-serverMod="
                + ";".join(_mod_path(value, runtime) for value in server_mods)
            )
        profiles = server_profiles
    elif role == "client":
        argv = [
            runtime.diag_executable,
            "-mod=" + mod_string,
            "-connect=127.0.0.1",
            f"-port={port}",
            "-profiles=" + client_profiles,
            "-name=" + str(payload["player_name"]),
            "-window",
            f"-x={int(payload['width'])}",
            f"-y={int(payload['height'])}",
            "-noPause",
        ]
        if not payload["no_file_patching"]:
            argv.append("-filePatching")
        profiles = client_profiles
    elif role == "offline":
        argv = [
            runtime.diag_executable,
            "-mod=" + mod_string,
            "-mission=" + mission,
            "-profiles=" + client_profiles,
            "-window",
            f"-x={int(payload['width'])}",
            f"-y={int(payload['height'])}",
        ]
        if not payload["no_file_patching"]:
            argv.append("-filePatching")
        profiles = client_profiles
    else:
        raise _failed("runtime_policy_invalid")
    core: dict[str, object] = {
        "argv": argv,
        "cwd": runtime.game_directory,
        "label": f"@{runtime.mod} {role}",
        "mission": mission,
        "mod": "@" + runtime.mod,
        "profiles": profiles,
        "role": role,
        "window_style": "normal",
    }
    if run_id is not None:
        core["run_id"] = run_id
    return core


async def _invoke(
    broker: Broker,
    kind: native_broker_protocol.BrokerKind,
    payload: dict[str, object],
    *,
    stdin: bytes = b"",
) -> dict[str, object]:
    try:
        result = await broker.invoke(
            native_broker_protocol.encode_request(kind, payload, stdin=stdin)
        )
    except BaseException as error:
        if isinstance(error, asyncio.CancelledError):
            raise
        raise _failed() from None
    if not isinstance(result, dict):
        raise _failed()
    return result


async def _lifecycle(
    broker: Broker,
    command: str,
    *,
    run_id: str | None,
    operation_id: str | None = None,
    request: bytes = b"",
) -> dict[str, object]:
    return await _invoke(
        broker,
        native_broker_protocol.BrokerKind.LIFECYCLE_CLI,
        {
            "command": command,
            "launch_operation_id": operation_id,
            "run_id": run_id,
        },
        stdin=request,
    )


def _successful_run(result: dict[str, object], run_id: str, state: str) -> bool:
    return (
        result.get("ok") is True
        and result.get("run_id") == run_id
        and result.get("state") == state
    )


def _pre_admission_rejection(result: dict[str, object]) -> str | None:
    if set(result) != {"error"}:
        return None
    code = result.get("error")
    if not isinstance(code, str) or code not in PRE_ADMISSION_REJECTION_CODES:
        return None
    return code


def _running_role_pids(
    result: dict[str, object], run_id: str, role: str
) -> frozenset[int] | None:
    runs = result.get("runs")
    if not isinstance(runs, list):
        return None
    matching = [
        item
        for item in runs
        if isinstance(item, dict) and item.get("run_id") == run_id
    ]
    if len(matching) != 1 or matching[0].get("state") != "RUNNING":
        return None
    processes = matching[0].get("processes")
    if not isinstance(processes, list):
        return None
    pids: list[int] = []
    for process in processes:
        if not isinstance(process, dict):
            return None
        if process.get("role") != role:
            continue
        pid = process.get("pid")
        if type(pid) is not int or pid <= 0 or pid in pids:
            return None
        pids.append(pid)
    return frozenset(pids)


async def _stop_best_effort(broker: Broker, run_id: str) -> bool:
    try:
        result = await _lifecycle(broker, "stop", run_id=run_id)
    except BaseException:
        return False
    return _successful_run(result, run_id, "EXITED")


async def _start(
    broker: Broker,
    payload: dict[str, object],
    runtime: WorkerRuntimePolicy,
    *,
    role: str,
    existing_run_id: str | None,
    id_fn: Callable[[], str],
) -> tuple[str, bool]:
    core = _start_core(payload, runtime, role=role, run_id=existing_run_id)
    operation_id: str | None = None
    new_run_id: str | None = None
    if existing_run_id is None:
        new_run_id = _new_id(id_fn)
        operation_id = _new_id(id_fn)
        core["new_run_id"] = new_run_id
        core["launch_operation_id"] = operation_id
        core["launch_request_sha256"] = hashlib.sha256(_canonical(core)).hexdigest()
    request = _canonical(core)
    target_run_id = existing_run_id or new_run_id
    if target_run_id is None:
        raise _failed()
    pre_admission_rejection: str | None = None
    try:
        async def invoke_start() -> dict[str, object]:
            return await _lifecycle(
                broker,
                "start",
                run_id=target_run_id,
                operation_id=operation_id,
                request=request,
            )

        try:
            result = await invoke_start()
        except DayzTestWorkerError:
            if operation_id is None:
                raise
            result = await invoke_start()
        else:
            pre_admission_rejection = _pre_admission_rejection(result)
            if pre_admission_rejection is not None:
                raise _failed(pre_admission_rejection)
        if operation_id is not None and not _successful_run(
            result, target_run_id, "RUNNING"
        ):
            result = await invoke_start()
        elif (
            operation_id is None
            and result.get("ok") is False
            and result.get("error") == "broker_child_failed"
        ):
            observed_role_pids = _running_role_pids(
                await _lifecycle(broker, "status", run_id=target_run_id),
                target_run_id,
                role,
            )
            if observed_role_pids is not None and len(observed_role_pids) == 1:
                result = {
                    "ok": True,
                    "run_id": target_run_id,
                    "state": "RUNNING",
                }
        if not _successful_run(result, target_run_id, "RUNNING"):
            raise _failed()
        if operation_id is None:
            return target_run_id, True
        ack = await _lifecycle(
            broker,
            "ack",
            run_id=target_run_id,
            operation_id=operation_id,
        )
        if not _successful_run(ack, target_run_id, "RUNNING"):
            raise _failed()
    except BaseException as error:
        if pre_admission_rejection is not None:
            raise _failed(pre_admission_rejection) from None
        cleanup_degraded = False
        if operation_id is not None:
            cleanup_degraded = not await _stop_best_effort(broker, target_run_id)
        raise _failure_after_cleanup(
            error,
            run_id=target_run_id,
            cleanup_degraded=cleanup_degraded,
        ) from None
    return target_run_id, True


def _default_has_assets(source: str) -> bool:
    try:
        return any(
            path.suffix.casefold() in {".p3d", ".paa", ".rvmat"}
            for path in Path(source).rglob("*")
            if path.is_file()
        )
    except OSError:
        raise _failed("build_source_unavailable") from None


async def execute_dayz_test_worker(
    canonical_request: bytes,
    *,
    request_sha256: str,
    request_policies: tuple[dayz_test_request.RequestProjectPolicy, ...],
    runtime_policy: WorkerRuntimePolicy,
    broker: Broker,
    id_fn: Callable[[], str] = lambda: str(uuid.uuid4()),
    readiness_probe: Callable[
        [str, int, int],
        Awaitable[dayz_test_readiness.ReadinessResult],
    ] | None = None,
    has_binarizable_assets: Callable[[str], bool] = _default_has_assets,
    cancel_event: asyncio.Event | None = None,
) -> WorkerResult:
    if (
        type(canonical_request) is not bytes
        or not isinstance(request_sha256, str)
        or hashlib.sha256(canonical_request).hexdigest() != request_sha256
    ):
        raise _failed("request_integrity_failed")
    try:
        parsed = dayz_test_request.parse_dayz_test_request(
            canonical_request, policies=request_policies
        )
    except ValueError:
        raise _failed("request_integrity_failed") from None
    if parsed.canonical_bytes != canonical_request or parsed.sha256 != request_sha256:
        raise _failed("request_integrity_failed")
    runtime = _validate_runtime(runtime_policy, parsed)
    payload = parsed.payload

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    if cancelled():
        raise _failed("operation_cancelled")
    supplied_run_id = payload.get("run_id")
    run_id = str(supplied_run_id) if isinstance(supplied_run_id, str) else None
    if payload["kill"]:
        if run_id is None:
            raise _failed()
        try:
            adopted = await _lifecycle(broker, "adopt", run_id=run_id)
        except DayzTestWorkerError:
            adopted = None
        if adopted is None or not _successful_run(adopted, run_id, "RUNNING"):
            try:
                reconciled = await _lifecycle(broker, "stop", run_id=run_id)
            except DayzTestWorkerError:
                raise _failed("run_not_adoptable") from None
            if _successful_run(reconciled, run_id, "EXITED"):
                return WorkerResult(0, run_id)
            raise _failed("run_not_adoptable")
        result = await _lifecycle(broker, "stop", run_id=run_id)
        if not _successful_run(result, run_id, "EXITED"):
            raise _failed(
                "run_stop_failed", run_id=run_id, cleanup_degraded=True
            )
        return WorkerResult(0, run_id)
    if payload["preflight"]:
        # Preflight must fail exactly where a real launch would: resolve the
        # mission now so an alias missing from this project's runtime is not
        # reported as success.
        _mission(payload, runtime)
        return WorkerResult(0, run_id)

    if payload["build"]:
        source = str(payload["source"])
        required = runtime.build_source_basename
        # Policy-declared build gate: when the project pins a basename, only a
        # source directory with exactly that name may be built. Reject before
        # the asset scan and before any broker frame is composed.
        if (
            required is not None
            and ntpath.basename(ntpath.normpath(source)).casefold() != required.casefold()
        ):
            raise _failed("build_source_unavailable")
        pack_only = bool(payload["pack_only"]) or not has_binarizable_assets(source)
        result = await _invoke(
            broker,
            native_broker_protocol.BrokerKind.ADDON_BUILDER,
            {
                "clear": bool(payload["clean"]),
                "pack_only": pack_only,
                "prefix": runtime.mod,
                "source": source,
                "target": ntpath.join(runtime.mods_root, "@" + runtime.mod, "Addons"),
                "temp": ntpath.join(runtime.build_temp_root, runtime.mod),
            },
        )
        if (
            result.get("ok") is not True
            or type(result.get("exit_code")) is not int
            or result.get("exit_code") != 0
            or type(result.get("pbo_size")) is not int
            or int(result["pbo_size"]) <= 0
        ):
            raise _failed("build_failed")
    if cancelled():
        raise _failed("operation_cancelled")

    mode = str(payload["mode"])
    created_run_id: str | None = None
    try:
        if mode in {"client", "offline"} and run_id is not None:
            adopted = await _lifecycle(broker, "adopt", run_id=run_id)
            if not _successful_run(adopted, run_id, "RUNNING"):
                raise _failed()
        if mode == "server":
            run_id, _acknowledged = await _start(
                broker, payload, runtime, role="server", existing_run_id=None, id_fn=id_fn
            )
            created_run_id = run_id
        elif mode == "client":
            if run_id is None:
                raise _failed()
            run_id, _acknowledged = await _start(
                broker, payload, runtime, role="client", existing_run_id=run_id, id_fn=id_fn
            )
        elif mode == "offline":
            run_id, _acknowledged = await _start(
                broker,
                payload,
                runtime,
                role="offline",
                existing_run_id=run_id,
                id_fn=id_fn,
            )
            if supplied_run_id is None:
                created_run_id = run_id
        elif mode == "all":
            run_id, _acknowledged = await _start(
                broker, payload, runtime, role="server", existing_run_id=None, id_fn=id_fn
            )
            created_run_id = run_id
            if readiness_probe is None:
                raise _failed("readiness_failed")
            readiness = await readiness_probe(
                run_id,
                int(payload["port"]),
                int(payload["server_wait_s"]),
            )
            if type(readiness) is not dayz_test_readiness.ReadinessResult:
                raise _failed("readiness_failed")
            if not readiness.ready:
                if readiness.error_code not in dayz_test_readiness.READINESS_ERROR_CODES:
                    raise _failed("readiness_failed")
                raise _failed(readiness.error_code)
            if cancelled():
                raise asyncio.CancelledError
            run_id, _acknowledged = await _start(
                broker, payload, runtime, role="client", existing_run_id=run_id, id_fn=id_fn
            )
        else:
            raise _failed("runtime_policy_invalid")
    except BaseException as error:
        cleanup_degraded = False
        if created_run_id is not None:
            cleanup_degraded = not await _stop_best_effort(broker, created_run_id)
        raise _failure_after_cleanup(
            error,
            run_id=created_run_id,
            cleanup_degraded=cleanup_degraded,
        ) from None
    return WorkerResult(0, run_id)
