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
from typing import Protocol

from dayz_mcp import (
    dayz_test_attestation,
    dayz_test_readiness,
    dayz_test_request,
    dayz_test_storage,
    native_broker_protocol,
    pack_only,
)


_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


PRE_ADMISSION_REJECTION_CODES = frozenset({"active_run_exists"})

STEAM_PREPARATION_REJECTION_CODES = frozenset({
    "steam_session_stale", "steam_prepare_busy", "steam_prepare_cancelled",
    "steam_prepare_timeout", "steam_prepare_failed", "steam_cleanup_degraded",
    "steam_client_active", "steam_identity_changed",
    "steam_probe_pending",
})


# fb-20260904-200816-79e2 / A3-F3. A lifecycle refusal used to reach the caller
# as worker_failed, because a code outside WORKER_ERROR_CODES cannot be raised
# here: the operator got "the worker failed" over a DayZ that had been killed,
# or over a replacement the daemon refused for a reason it could name exactly.
# This is a CLOSED list of the lifecycle codes the worker is allowed to carry
# through verbatim; anything else still collapses to worker_failed.
LIFECYCLE_REJECTION_CODES = frozenset(
    {
        "bridge_state_unreadable",
        "client_polling_since_decision",
        "port_still_held",
        "replace_witness_missing",
        "replace_witness_stale",
        "storage_recovery_required",
        "storage_rotate_failed",
    }
) | STEAM_PREPARATION_REJECTION_CODES


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
        "project_attestation_artifact_missing",
        "project_attestation_initialization_missing",
        "project_attestation_unverifiable",
        "worker_failed",
        "worker_identity_failed",
    }
) | PRE_ADMISSION_REJECTION_CODES | LIFECYCLE_REJECTION_CODES | dayz_test_readiness.READINESS_ERROR_CODES


class DayzTestWorkerError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        run_id: str | None = None,
        cleanup_degraded: bool = False,
        attempt_run_id: str | None = None,
        launch_operation_id: str | None = None,
        attestation: dict[str, object] | None = None,
    ) -> None:
        if code not in WORKER_ERROR_CODES or type(cleanup_degraded) is not bool:
            raise ValueError("invalid_worker_error")
        if cleanup_degraded and not isinstance(run_id, str):
            raise ValueError("invalid_worker_error")
        if attempt_run_id is not None and _UUID4.fullmatch(attempt_run_id) is None:
            raise ValueError("invalid_worker_error")
        if launch_operation_id is not None and (
            attempt_run_id is None or _UUID4.fullmatch(launch_operation_id) is None
        ):
            raise ValueError("invalid_worker_error")
        super().__init__(code)
        self.code = code
        self.run_id = run_id
        self.cleanup_degraded = cleanup_degraded
        # Independent of run_id. run_id stays null when cleanup succeeded.
        self.attempt_run_id = attempt_run_id
        self.launch_operation_id = launch_operation_id
        self.attestation = attestation


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
    attestation: dict[str, object] | None = None


def _failed(
    code: str = "worker_failed",
    *,
    run_id: str | None = None,
    cleanup_degraded: bool = False,
    attempt_run_id: str | None = None,
    launch_operation_id: str | None = None,
    attestation: dict[str, object] | None = None,
) -> DayzTestWorkerError:
    return DayzTestWorkerError(
        code,
        run_id=run_id,
        cleanup_degraded=cleanup_degraded,
        attempt_run_id=attempt_run_id,
        launch_operation_id=launch_operation_id,
        attestation=attestation,
    )


def _failure_after_cleanup(
    error: BaseException,
    *,
    run_id: str | None,
    cleanup_degraded: bool,
    attempt_run_id: str | None = None,
    launch_operation_id: str | None = None,
) -> DayzTestWorkerError:
    attestation = (
        error.attestation if isinstance(error, DayzTestWorkerError) else None
    )
    if isinstance(error, DayzTestWorkerError):
        if error.attempt_run_id is not None:
            attempt_run_id = error.attempt_run_id
        if error.launch_operation_id is not None:
            launch_operation_id = error.launch_operation_id
        if error.cleanup_degraded:
            # A degraded cleanup keeps its own code and run id, and still carries
            # the attempt context so the caller correlates exactly (c8c7).
            return _failed(
                error.code,
                run_id=error.run_id,
                cleanup_degraded=True,
                attempt_run_id=attempt_run_id,
                launch_operation_id=(
                    launch_operation_id if attempt_run_id is not None else None
                ),
                attestation=attestation,
            )
        code = error.code
    elif isinstance(error, asyncio.CancelledError):
        code = "operation_cancelled"
    else:
        code = "worker_failed"
    return _failed(
        code,
        run_id=run_id if cleanup_degraded else None,
        cleanup_degraded=cleanup_degraded,
        attempt_run_id=attempt_run_id,
        launch_operation_id=launch_operation_id,
        attestation=attestation,
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


_REQUIRED_MISSION_ALIASES = frozenset({"chernarus", "livonia", "sakhal"})


def runtime_policy_acceptable(runtime: WorkerRuntimePolicy) -> bool:
    """Semantic runtime rules shared with the sealed bundle accessor.

    Required mission aliases, absolute local paths, and the build-source
    basename. A dictionary of aliases is not enough: the worker refuses the
    same document the accessor must refuse.
    """
    aliases = dict(runtime.mission_aliases)
    return (
        type(runtime) is WorkerRuntimePolicy
        and _REQUIRED_MISSION_ALIASES.issubset(aliases)
        and all(type(key) is str and key for key in aliases)
        and len(aliases) == len(runtime.mission_aliases)
        and all(
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
        and _valid_build_source_basename(runtime.build_source_basename)
    )


_WORKER_RUNTIME_PROJECT_KEYS = frozenset(
    {
        "build_source_basename",
        "build_temp_root",
        "dev_root",
        "diag_executable",
        "game_directory",
        "mission_aliases",
        "mod",
        "mods_root",
    }
)


def worker_runtime_from_document(
    document: object, mod: str, dev_root: str
) -> WorkerRuntimePolicy:
    """Closed worker-runtime document shared by the bundle and the bootstrap.

    Required keys, unknown keys, and the semantic rules in
    ``runtime_policy_acceptable`` are one decision. A missing
    ``build_source_basename`` is a refusal, not an implicit null. The public
    preflight maps this error to ``runtime_policy_invalid``.
    """
    if (
        type(document) is not dict
        or set(document) != {"format_version", "projects"}
        or type(document.get("format_version")) is not int
        or document["format_version"] != 1
        or type(document.get("projects")) is not list
        or not 1 <= len(document["projects"]) <= 128
    ):
        raise ValueError("worker_runtime_invalid")
    seen: set[tuple[str, str]] = set()
    selected: WorkerRuntimePolicy | None = None
    for item in document["projects"]:
        if type(item) is not dict or set(item) != _WORKER_RUNTIME_PROJECT_KEYS:
            raise ValueError("worker_runtime_invalid")
        if type(item["mod"]) is not str or not item["mod"]:
            raise ValueError("worker_runtime_invalid")
        if type(item["dev_root"]) is not str:
            raise ValueError("worker_runtime_invalid")
        identity = (item["mod"].casefold(), item["dev_root"].casefold())
        if identity in seen:
            raise ValueError("worker_runtime_invalid")
        seen.add(identity)
        aliases = item["mission_aliases"]
        if type(aliases) is not dict or any(
            type(key) is not str or not key or type(path) is not str
            for key, path in aliases.items()
        ):
            raise ValueError("worker_runtime_invalid")
        if not _REQUIRED_MISSION_ALIASES.issubset(aliases):
            raise ValueError("worker_runtime_invalid")
        if not _valid_build_source_basename(item["build_source_basename"]):
            raise ValueError("worker_runtime_invalid")
        policy = WorkerRuntimePolicy(
            dev_root=item["dev_root"],
            mod=item["mod"],
            diag_executable=item["diag_executable"],
            game_directory=item["game_directory"],
            mission_aliases=tuple(sorted(aliases.items())),
            mods_root=item["mods_root"],
            build_temp_root=item["build_temp_root"],
            build_source_basename=item["build_source_basename"],
        )
        if not runtime_policy_acceptable(policy):
            raise ValueError("worker_runtime_invalid")
        if item["mod"] == mod and item["dev_root"] == dev_root:
            selected = policy
    if selected is None:
        raise ValueError("worker_runtime_invalid")
    return selected


def _validate_runtime(
    runtime: WorkerRuntimePolicy,
    parsed: dayz_test_request.ParsedDayzTestRequest,
) -> WorkerRuntimePolicy:
    if (
        not runtime_policy_acceptable(runtime)
        or runtime.dev_root != parsed.payload.get("dev_root")
        or runtime.mod != parsed.payload.get("mod")
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
    # M15: one implementation of the rule, not two. The seal that decides
    # whether storage_1 rotates and the argv the engine receives must never
    # be able to disagree about which folder a mod entry names, so the argv
    # side delegates instead of mirroring (the defect class of ficha 9d46).
    try:
        return dayz_test_storage.normalize_mod_path(value, runtime.mods_root)
    except dayz_test_storage.StorageError:
        raise _failed("runtime_policy_invalid") from None


def _override(payload: dict[str, object]) -> bool:
    return payload.get("project_mod_override") is True


def _mod_entries(payload: dict[str, object], runtime: WorkerRuntimePolicy) -> list[str]:
    values = [*list(payload.get("base_mods", []))]
    if not _override(payload):
        values.append("@" + runtime.mod)
    values.extend(list(payload.get("extra_mods", [])))
    return [_mod_path(value, runtime) for value in values]


def _reject_override_alias(payload: dict[str, object], runtime: WorkerRuntimePolicy) -> None:
    dayz_test_request.reject_original_project_directory(payload, runtime.mods_root)


def _mods(payload: dict[str, object], runtime: WorkerRuntimePolicy) -> str:
    return ";".join(_mod_entries(payload, runtime))


def _start_core(
    payload: dict[str, object],
    runtime: WorkerRuntimePolicy,
    *,
    role: str,
    run_id: str | None,
) -> dict[str, object]:
    _reject_override_alias(payload, runtime)
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
        if payload.get("navmesh_data_server", False):
            argv.append("-startNavmeshDataServer")
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
    witness = payload.get("replace_if_not_polling_since")
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
    if role in {"client", "offline"}:
        core["auto_remediate_steam"] = payload.get("auto_remediate_steam", False)
    if run_id is not None:
        core["run_id"] = run_id
        # 79e2. Only the client relaunch over a live run can supersede a
        # process, so only it carries the witness of the gate that authorised
        # it. A launch that creates its own run has nothing to supersede, and
        # the key would only widen the bytes the launch hash covers.
        if role == "client" and type(witness) is int:
            core["replace_if_not_polling_since"] = witness
    elif role in {"server", "offline"}:
        # M15. Only a launch that CREATES its run hands the engine a fresh
        # mission storage; a client, or an offline that extends a run, attaches
        # to a tree the engine already chose. The seal is added before _start
        # hashes the core, so launch_request_sha256 covers it.
        core["storage_seal"] = _modset_seal(payload, runtime)
    return core

def _modset_seal(
    payload: dict[str, object], runtime: WorkerRuntimePolicy
) -> str:
    """The fingerprint of the mod set this launch is about to hand the engine.

    Computed here because this is the only layer that holds both the request
    payload and the runtime policy, and it travels INSIDE the sealed launch
    request. It is not applied here: rotating before the daemon has admitted the
    launch could rename the storage of a run that was alive and that the daemon
    was about to protect with active_run_exists (Codex F-01). The daemon rotates,
    after every admission and before it spawns anything.
    """
    try:
        return dayz_test_storage.modset_seal(
            dayz_test_storage.modset_roles(
                base_mods=list(payload.get("base_mods", [])),
                project_mod=None if _override(payload) else "@" + runtime.mod,
                extra_mods=list(payload.get("extra_mods", [])),
                server_mods=list(payload.get("server_mods", [])),
                mods_root=runtime.mods_root,
            )
        )
    except dayz_test_storage.StorageError:
        raise _failed("runtime_policy_invalid") from None


def _selected_attestation(
    policies: tuple[dayz_test_request.RequestProjectPolicy, ...],
    payload: dict[str, object],
) -> dayz_test_attestation.ProjectAttestation | None:
    for policy in policies:
        if policy.mod == payload.get("mod") and policy.dev_root == payload.get("dev_root"):
            return policy.attestation
    return None


def _effective_directories(
    payload: dict[str, object], runtime: WorkerRuntimePolicy
) -> tuple[str, ...]:
    """Directories the requested roles actually load, including -serverMod."""
    directories = list(_mod_entries(payload, runtime))
    if payload.get("mode") in {"server", "all"}:
        directories.extend(
            _mod_path(value, runtime) for value in list(payload.get("server_mods", []))
        )
    return tuple(directories)


def _capture_role_boundary(
    attestation: dayz_test_attestation.ProjectAttestation,
    payload: dict[str, object],
    runtime: WorkerRuntimePolicy,
    role: str,
) -> dict[str, tuple[int, bool]]:
    filenames = tuple(
        requirement.filename
        for requirement in attestation.initialization
        if requirement.role == role and requirement.filename is not None
    )
    return dayz_test_attestation.capture_log_boundaries(
        dayz_test_attestation.profile_directory(runtime.dev_root, role),
        filenames,
    )


def assess_preflight(
    payload: dict[str, object],
    runtime: WorkerRuntimePolicy,
    attestation: dayz_test_attestation.ProjectAttestation | None,
) -> dict[str, object] | None:
    """Checks shared with the host that do not need the box or a child process.

    Mission resolution and the build-source basename gate run here. Asset
    traversal does not. Attestation artifacts are read only when no build was
    requested; a future build stays pending, and initialization is always
    pending because preflight does not start the role.
    """
    _mission(payload, runtime)
    _reject_override_alias(payload, runtime)
    required = runtime.build_source_basename
    if payload.get("build") and required is not None:
        source = str(payload.get("source"))
        if ntpath.basename(ntpath.normpath(source)).casefold() != required.casefold():
            raise _failed("build_source_unavailable")
    if attestation is None:
        return None
    pending_artifacts = bool(payload.get("build"))
    artifact_status, artifacts = dayz_test_attestation.verify_artifacts(
        attestation,
        _effective_directories(payload, runtime),
        pending=pending_artifacts,
    )
    init_status, initialization = dayz_test_attestation.initialization_rows(
        attestation,
        mode=str(payload.get("mode")),
        dev_root=runtime.dev_root,
        boundaries_by_role={},
        pending=True,
    )
    document = dayz_test_attestation.report(
        attestation,
        artifact_status=artifact_status,
        artifacts=artifacts,
        initialization_status=init_status,
        initialization=initialization,
    )
    code = dayz_test_attestation.failure_code(
        document["status"] if isinstance(document.get("status"), str) else "unverifiable",
        initialization_failed=False,
    )
    if code is not None:
        raise _failed(code, attestation=document)
    return document


def _attest_artifacts_or_raise(
    attestation: dayz_test_attestation.ProjectAttestation,
    payload: dict[str, object],
    runtime: WorkerRuntimePolicy,
) -> tuple[str, list[dict[str, object]]]:
    status, rows = dayz_test_attestation.verify_artifacts(
        attestation, _effective_directories(payload, runtime), pending=False
    )
    if status != "passed":
        init_status, initialization = dayz_test_attestation.initialization_rows(
            attestation,
            mode=str(payload.get("mode")),
            dev_root=runtime.dev_root,
            boundaries_by_role={},
            pending=True,
        )
        document = dayz_test_attestation.report(
            attestation,
            artifact_status=status,
            artifacts=rows,
            initialization_status=init_status,
            initialization=initialization,
        )
        raise _failed(
            dayz_test_attestation.failure_code(status, initialization_failed=False)
            or "project_attestation_unverifiable",
            attestation=document,
        )
    return status, rows


async def _attest_initialization(
    attestation: dayz_test_attestation.ProjectAttestation,
    *,
    payload: dict[str, object],
    runtime: WorkerRuntimePolicy,
    artifact_status: str,
    artifacts: list[dict[str, object]],
    boundaries: dict[str, dict[str, int]],
    cancel_event: asyncio.Event | None,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + attestation.timeout_s
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        init_status, initialization = dayz_test_attestation.initialization_rows(
            attestation,
            mode=str(payload.get("mode")),
            dev_root=runtime.dev_root,
            boundaries_by_role=boundaries,
            pending=False,
        )
        document = dayz_test_attestation.report(
            attestation,
            artifact_status=artifact_status,
            artifacts=artifacts,
            initialization_status=init_status,
            initialization=initialization,
        )
        if document["status"] == "passed":
            return document
        if document["status"] == "unverifiable" or asyncio.get_running_loop().time() >= deadline:
            code = dayz_test_attestation.failure_code(
                str(document["status"]) if document["status"] != "pending" else "failed",
                initialization_failed=init_status == "failed" or document["status"] != "unverifiable",
            )
            raise _failed(code or "project_attestation_initialization_missing", attestation=document)
        await asyncio.sleep(0.05)


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


# fb-20260918-165857-744a: a stop whose processes are already gone used to
# fail overall (run_stop_failed + cleanup_degraded) even when CIM later
# showed no DayZDiag. Treat that observed-gone outcome as success.
_ALREADY_GONE_ERRORS = frozenset(
    {
        "run_processes_gone",
        "run_not_found",
    }
)


def _run_already_gone(result: dict[str, object] | None, run_id: str) -> bool:
    if result is None:
        return False
    if result.get("state") == "EXITED" and result.get("run_id") in {run_id, None}:
        return True
    error = result.get("error")
    if error in _ALREADY_GONE_ERRORS:
        return True
    return result.get("stop_method") == "no_live_owned"


def _status_run_already_gone(status: dict[str, object], run_id: str) -> bool:
    runs = status.get("runs")
    if not isinstance(runs, list):
        return False
    matches = [
        item
        for item in runs
        if isinstance(item, dict) and item.get("run_id") == run_id
    ]
    if not matches:
        return True
    if len(matches) != 1:
        return False
    row = matches[0]
    if row.get("state") == "EXITED":
        return True
    processes = row.get("processes")
    if not isinstance(processes, list) or processes:
        return False
    return row.get("state") in {"UNRECONCILED", "STOPPING", "RUNNING_IDLE"}


async def _status_already_gone(broker: Broker, run_id: str) -> bool:
    try:
        status = await _lifecycle(broker, "status", run_id=run_id)
    except DayzTestWorkerError:
        return False
    return _status_run_already_gone(status, run_id)


def _lifecycle_rejection(result: object) -> str | None:
    """The reason the daemon gave, when it is one the worker may republish.

    Not the whole envelope: only a declared code. An undeclared one keeps the
    legacy collapse, so the daemon cannot widen this vocabulary by itself.
    """
    if not isinstance(result, dict):
        return None
    code = result.get("error")
    if not isinstance(code, str) or code not in LIFECYCLE_REJECTION_CODES:
        return None
    return code


def _pre_admission_rejection(result: dict[str, object]) -> str | None:
    # A named preparation refusal has created no run. Retrying it would repeat
    # the wait/repair and reset the absolute Steam budget. Post-commit settlement
    # carries run_id and must retain the normal cleanup path instead.
    code = result.get("error")
    if isinstance(code, str) and code in STEAM_PREPARATION_REJECTION_CODES and "run_id" not in result:
        return code
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
) -> tuple[str, bool, str | None]:
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
            # A lost client/offline response may follow a whole Steam wait or
            # mutation. Reissuing start would allocate a fresh preparation budget.
            # Only server creation retains transport replay; exact-run cleanup
            # handles an uncertain new offline launch below.
            if operation_id is None or role != "server":
                raise
            result = await invoke_start()
        pre_admission_rejection = _pre_admission_rejection(result)
        if pre_admission_rejection is not None:
            degraded = bool(result.get("cleanup_degraded")) or pre_admission_rejection == "steam_cleanup_degraded"
            raise _failed(
                pre_admission_rejection,
                run_id=target_run_id if degraded else None,
                cleanup_degraded=degraded,
                attempt_run_id=target_run_id,
                launch_operation_id=operation_id,
            )
        if operation_id is not None and role == "server" and not _successful_run(
            result, target_run_id, "RUNNING"
        ) and _lifecycle_rejection(result) not in STEAM_PREPARATION_REJECTION_CODES:
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
            raise _failed(_lifecycle_rejection(result) or "worker_failed")
        if operation_id is None:
            return target_run_id, True, None
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
            raise error from None
        cleanup_degraded = False
        if operation_id is not None:
            cleanup_degraded = not await _stop_best_effort(broker, target_run_id)
        raise _failure_after_cleanup(
            error,
            run_id=target_run_id,
            cleanup_degraded=cleanup_degraded,
            attempt_run_id=target_run_id,
            launch_operation_id=operation_id,
        ) from None
    return target_run_id, True, operation_id


def _default_has_assets(source: str) -> bool:
    # Same suffix set as dayz_mcp.pack_only.BINARIZABLE_SUFFIXES / pack-addon.ps1.
    # The shared walk does not follow junctions or symbolic links (dbe0).
    try:
        return pack_only.has_binarizable_assets(source)
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
        if _run_already_gone(adopted, run_id):
            return WorkerResult(0, run_id)
        if adopted is None or not _successful_run(adopted, run_id, "RUNNING"):
            try:
                reconciled = await _lifecycle(broker, "stop", run_id=run_id)
            except DayzTestWorkerError:
                raise _failed("run_not_adoptable") from None
            if _successful_run(reconciled, run_id, "EXITED") or _run_already_gone(
                reconciled, run_id
            ):
                return WorkerResult(0, run_id)
            raise _failed("run_not_adoptable")
        try:
            result = await _lifecycle(broker, "stop", run_id=run_id)
        except DayzTestWorkerError:
            if await _status_already_gone(broker, run_id):
                return WorkerResult(0, run_id)
            raise _failed(
                "run_stop_failed", run_id=run_id, cleanup_degraded=True
            ) from None
        if _successful_run(result, run_id, "EXITED") or _run_already_gone(
            result, run_id
        ):
            return WorkerResult(0, run_id)
        if await _status_already_gone(broker, run_id):
            return WorkerResult(0, run_id)
        raise _failed(
            "run_stop_failed", run_id=run_id, cleanup_degraded=True
        )
    attestation = _selected_attestation(request_policies, payload)
    if payload["preflight"]:
        # No child, no storage write. The same assess_preflight the host uses.
        document = assess_preflight(payload, runtime, attestation)
        return WorkerResult(0, run_id, document)

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

    artifact_status = "passed"
    artifact_rows: list[dict[str, object]] = []
    boundaries: dict[str, dict[str, tuple[int, bool]]] = {}
    if attestation is not None:
        artifact_status, artifact_rows = _attest_artifacts_or_raise(
            attestation, payload, runtime
        )

    def capture(role: str) -> None:
        if attestation is None:
            return
        try:
            boundaries[role] = _capture_role_boundary(
                attestation, payload, runtime, role
            )
        except ValueError:
            init_status, initialization = dayz_test_attestation.initialization_rows(
                attestation,
                mode=str(payload.get("mode")),
                dev_root=runtime.dev_root,
                boundaries_by_role=boundaries,
                pending=True,
            )
            document = dayz_test_attestation.report(
                attestation,
                artifact_status=artifact_status,
                artifacts=artifact_rows,
                initialization_status="unverifiable",
                initialization=initialization,
            )
            raise _failed(
                "project_attestation_unverifiable", attestation=document
            ) from None

    mode = str(payload["mode"])
    created_run_id: str | None = None
    # The run this attempt created or targeted, and the launch operation it
    # allocated: a failure terminal carries both whatever cleanup did, so the
    # caller correlates exactly (c8c7). created_run_id stays the run to stop.
    attempt_run_id: str | None = run_id if mode in {"client", "offline"} else None
    attempt_operation_id: str | None = None
    try:
        if mode in {"client", "offline"} and run_id is not None:
            adopted = await _lifecycle(broker, "adopt", run_id=run_id)
            if not _successful_run(adopted, run_id, "RUNNING"):
                raise _failed()
        if mode == "server":
            capture("server")
            run_id, _acknowledged, attempt_operation_id = await _start(
                broker, payload, runtime, role="server", existing_run_id=None, id_fn=id_fn
            )
            created_run_id = attempt_run_id = run_id
        elif mode == "client":
            if run_id is None:
                raise _failed()
            capture("client")
            run_id, _acknowledged, _operation = await _start(
                broker, payload, runtime, role="client", existing_run_id=run_id, id_fn=id_fn
            )
        elif mode == "offline":
            capture("offline")
            run_id, _acknowledged, operation = await _start(
                broker,
                payload,
                runtime,
                role="offline",
                existing_run_id=run_id,
                id_fn=id_fn,
            )
            attempt_run_id = run_id
            if supplied_run_id is None:
                created_run_id = run_id
                attempt_operation_id = operation
        elif mode == "all":
            capture("server")
            run_id, _acknowledged, attempt_operation_id = await _start(
                broker, payload, runtime, role="server", existing_run_id=None, id_fn=id_fn
            )
            created_run_id = attempt_run_id = run_id
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
            capture("client")
            run_id, _acknowledged, _operation = await _start(
                broker, payload, runtime, role="client", existing_run_id=run_id, id_fn=id_fn
            )
        else:
            raise _failed("runtime_policy_invalid")
        if attestation is not None:
            attested = await _attest_initialization(
                attestation,
                payload=payload,
                runtime=runtime,
                artifact_status=artifact_status,
                artifacts=artifact_rows,
                boundaries=boundaries,
                cancel_event=cancel_event,
            )
        else:
            attested = None
    except BaseException as error:
        cleanup_degraded = False
        if created_run_id is not None:
            cleanup_degraded = not await _stop_best_effort(broker, created_run_id)
        raise _failure_after_cleanup(
            error,
            run_id=created_run_id,
            cleanup_degraded=cleanup_degraded,
            attempt_run_id=attempt_run_id,
            launch_operation_id=attempt_operation_id if attempt_run_id is not None else None,
        ) from None
    return WorkerResult(0, run_id, attested if attestation is not None else None)
