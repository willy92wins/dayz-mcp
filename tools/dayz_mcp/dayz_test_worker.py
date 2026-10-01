"""Private, non-launching dayz-test worker driven only through the native broker."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import ntpath
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dayz_mcp import (
    dayz_test_readiness,
    dayz_test_request,
    dayz_test_storage,
    native_broker_protocol,
)

try:
    import _winapi
except ImportError:  # not Windows: a build stage cannot be made, and the build fails closed
    _winapi = None  # type: ignore[assignment]


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
        "build_source_link_outside",
        "build_source_unavailable",
        "build_stage_unavailable",
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
) | PRE_ADMISSION_REJECTION_CODES | LIFECYCLE_REJECTION_CODES | dayz_test_readiness.READINESS_ERROR_CODES


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
    # M15: one implementation of the rule, not two. The seal that decides
    # whether storage_1 rotates and the argv the engine receives must never
    # be able to disagree about which folder a mod entry names, so the argv
    # side delegates instead of mirroring (the defect class of ficha 9d46).
    try:
        return dayz_test_storage.normalize_mod_path(value, runtime.mods_root)
    except dayz_test_storage.StorageError:
        raise _failed("runtime_policy_invalid") from None


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
                project_mod="@" + runtime.mod,
                extra_mods=list(payload.get("extra_mods", [])),
                server_mods=list(payload.get("server_mods", [])),
                mods_root=runtime.mods_root,
            )
        )
    except dayz_test_storage.StorageError:
        raise _failed("runtime_policy_invalid") from None


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
            raise _failed(pre_admission_rejection,
                          run_id=target_run_id if degraded else None,
                          cleanup_degraded=degraded)
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
            raise error from None
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
    # Same suffix set as dayz_mcp.pack_only.BINARIZABLE_SUFFIXES / pack-addon.ps1.
    try:
        return any(
            path.suffix.casefold() in {".p3d", ".paa", ".rvmat"}
            for path in Path(source).rglob("*")
            if path.is_file()
        )
    except OSError:
        raise _failed("build_source_unavailable") from None


# bf5c / 8cf9. AddonBuilder hands binarize -addon="<parent of the source>", and
# binarize parses every config.cpp under that folder, through junctions too: a
# broken one anywhere next to the source failed the build, and a source in P:\
# or DayZ Projects made it walk every sibling project. When binarize runs, the
# worker builds from <stage>\<basename> instead, a fresh folder in the
# launcher's private TEMP that holds only junctions: the source, the vanilla
# roots DayZ Tools extracts next to it, and the parent's folders the source's
# files reference. The vanilla roots are not optional: binarize resolves
# \dz\... paths and the vanilla config classes through -addon (2026-10-01,
# SimpleGroup: T1_FlagKit.p3d was 55 221 B built from DayZ Projects, 43 376 B
# alone in a parent, and byte-identical to the former with a DZ junction beside
# the mod). binarize follows links, so the stage refuses anything that would
# lead it out again: a root or the source that resolves to the parent or above,
# and a junction or symbolic link, in the source or in a root it scans, that
# resolves outside the source and the stage's roots (reviews R1 and R2).
#
# Trust boundary: the vanilla roots are DayZ Tools' own extraction, tens of GB.
# Each is checked where it resolves, like any root, but never scanned: neither
# their files nor the links inside them are examined.
_BUILD_STAGE_PREFIX = "dayz-mcp-build-"
_VANILLA_ROOTS = frozenset(
    {"dz", "bin", "scripts", "gui", "graphics", "system", "languagecore"}
)
# The files whose text can name another root, and the suffixes that make a
# path-like string in them an asset path. The config and header suffixes are
# for #include, which binarize's preprocessor resolves through -addon as well.
_REFERENCE_SCAN_SUFFIXES = frozenset(
    {".p3d", ".rvmat", ".bisurf", ".emat", ".ptc", ".cpp", ".hpp", ".h", ".cfg"}
)
_REFERENCE_ASSET_SUFFIXES = (
    b"anm", b"bisurf", b"cfg", b"cpp", b"emat", b"hpp", b"h", b"p3d",
    b"paa", b"png", b"ptc", b"rtm", b"rvmat", b"tga", b"wrp",
)
_PATH_CHARACTERS = rb"A-Za-z0-9_.\-"
# A run of path components separated by \ or /, the last one ending in an asset
# suffix, that does not start or end inside a longer run. Group 1 is the first
# component, the root binarize looks up under -addon. The text is read as
# bytes: .p3d files are binary, with their paths as NUL-terminated strings.
_ASSET_PATH = re.compile(
    rb"(?<![" + _PATH_CHARACTERS + rb"\\/])"
    rb"[\\/]*([" + _PATH_CHARACTERS + rb"]+)"
    rb"(?:[\\/]+[" + _PATH_CHARACTERS + rb"]+)*?"
    rb"[\\/]+[" + _PATH_CHARACTERS + rb"]*?\.(?:"
    + rb"|".join(_REFERENCE_ASSET_SUFFIXES)
    + rb")(?![" + _PATH_CHARACTERS + rb"])",
    re.IGNORECASE,
)


# The reparse points Windows follows as links. A OneDrive placeholder is a
# reparse point too, under a cloud tag: a file or a folder like any other.
_LINK_REPARSE_TAGS = frozenset(
    {stat.IO_REPARSE_TAG_MOUNT_POINT, stat.IO_REPARSE_TAG_SYMLINK}
)


def _is_link(info: os.stat_result) -> bool:
    """True for the lstat of a junction or a symbolic link, to a folder or a file."""
    return (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_reparse_tag", 0) in _LINK_REPARSE_TAGS
    )


def _scan_tree(
    tree: str, skip: tuple[str, ...] = ()
) -> tuple[frozenset[str], tuple[tuple[str, str], ...]]:
    """What one folder tree says about the stage it needs.

    The first components, casefolded, of the asset paths its scanned files
    name; and (path, resolved target) for every junction and symbolic link in
    it. A link is recorded and never followed: whether binarize may follow it
    is decided once the stage's roots are known, in _build_stage_plan. A folder
    in skip (a tree scanned already, or a vanilla root) is not entered, so
    every file is read once at most. OSError when a listing or a read fails.
    """
    skipped = {ntpath.normcase(folder) for folder in skip}
    roots: set[str] = set()
    links: list[tuple[str, str]] = []
    pending = [tree]
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                if _is_link(entry.stat(follow_symlinks=False)):
                    links.append((entry.path, os.path.realpath(entry.path)))
                elif entry.is_dir(follow_symlinks=False):
                    if ntpath.normcase(entry.path) not in skipped:
                        pending.append(entry.path)
                elif (
                    ntpath.splitext(entry.name)[1].casefold() in _REFERENCE_SCAN_SUFFIXES
                    and entry.is_file(follow_symlinks=False)
                ):
                    with open(entry.path, "rb") as handle:
                        data = handle.read()
                    roots.update(
                        match.group(1).decode("ascii").casefold()
                        for match in _ASSET_PATH.finditer(data)
                    )
    return frozenset(roots), tuple(links)


def _is_within(path: str, folder: str) -> bool:
    path = ntpath.normcase(path)
    folder = ntpath.normcase(folder).rstrip("\\")
    return path == folder or path.startswith(folder + "\\")


def _build_stage_plan(source: str) -> tuple[tuple[str, str], ...]:
    """(junction name, resolved folder) for each junction of the stage, the source first.

    The source keeps its own name, so its \\<name>\\... paths resolve as they
    did, and every root keeps its name on disk, sorted. The roots are the
    vanilla roots present directly in the source's parent, and the closure of
    the references: every folder directly in the parent whose name is the
    first component of an asset path in the source's files, then in that
    folder's own files, and so on until no new one appears. Each tree is
    scanned once, where it resolves, and only the parent entries a vanilla
    name or a reference names are looked at, so a sibling nothing needs plays
    no part, readable or not. The vanilla roots are never scanned (the trust
    boundary above).

    binarize follows links. Before any stage exists, the build is refused with
    build_source_link_outside when the source or a root resolves to the parent
    or above (the parent the source path names, or the one the source resolves
    in), which would put every sibling and its config.cpp back in view, or when
    a junction or symbolic link in the source or in a scanned root resolves
    outside the source and the roots. Each junction of the stage then points at
    the folder that was checked. OSError when the source has no parent or a
    listing or a read fails.
    """
    name = ntpath.basename(source)
    parent = ntpath.dirname(source)
    if not name or ntpath.normcase(parent) == ntpath.normcase(source):
        raise OSError("the build source has no parent folder")
    parent_real = os.path.realpath(parent)
    source_real = os.path.realpath(source)
    # The parent where the source path names it, and the one it resolves in: a
    # folder resolving to either, or above, holds the siblings.
    guarded = (parent_real, ntpath.dirname(source_real))

    def exposes_siblings(folder: str) -> bool:
        return any(_is_within(parent_folder, folder) for parent_folder in guarded)

    if exposes_siblings(source_real):
        raise _failed("build_source_link_outside")
    siblings: dict[str, list[os.DirEntry[str]]] = {}
    with os.scandir(parent) as entries:
        for entry in entries:
            if entry.name.casefold() != name.casefold():
                siblings.setdefault(entry.name.casefold(), []).append(entry)
    roots: dict[str, str] = {}
    trusted: list[str] = []
    pending: list[str] = [source_real]

    def admit(key: str) -> None:
        # Checked before anything inside the root is read.
        for entry in siblings.pop(key, ()):
            if not entry.is_dir():
                continue
            resolved = os.path.realpath(entry.path)
            if exposes_siblings(resolved):
                raise _failed("build_source_link_outside")
            roots[entry.name] = resolved
            (trusted if key in _VANILLA_ROOTS else pending).append(resolved)

    for key in sorted(_VANILLA_ROOTS):
        admit(key)
    scanned: list[str] = []
    links: list[tuple[str, str]] = []
    while pending:
        tree = pending.pop(0)
        known = (*scanned, *trusted)
        if scanned and any(_is_within(tree, folder) for folder in known):
            continue  # read already as part of a scanned tree, or a vanilla root
        referenced, found = _scan_tree(tree, known)
        scanned.append(tree)
        links.extend(found)
        for key in sorted(referenced):
            admit(key)
    reachable = (source_real, *roots.values())
    if any(
        not any(_is_within(target, folder) for folder in reachable)
        for _link, target in links
    ):
        raise _failed("build_source_link_outside")
    ordered = sorted(roots, key=lambda root: (root.casefold(), root))
    return ((name, source_real),) + tuple((root, roots[root]) for root in ordered)


def _is_junction(path: str) -> bool:
    # os.path.isjunction is new in Python 3.12; the bundled runtime is 3.14.
    # The suite also runs on 3.11, where the reparse tag is read directly, the
    # test 3.12's ntpath.isjunction made.
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        return bool(isjunction(path))
    try:
        return os.lstat(path).st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT
    except (AttributeError, OSError, ValueError):
        return False


def _create_verified_junction(target: str, link: str) -> None:
    """Make link a junction to the folder target, or raise OSError.

    _winapi.CreateJunction stores the full path of target behind the native
    "\\??\\" prefix, which os.readlink returns as "\\\\?\\" + target, and it
    accepts a file as its target. So the link must be a junction, point at
    exactly target and reach a folder.
    """
    if _winapi is None:
        raise OSError("directory junctions need Windows")
    _winapi.CreateJunction(target, link)
    pointed = os.readlink(link) if _is_junction(link) else ""
    if pointed.startswith("\\\\?\\"):
        pointed = pointed[4:]
    if ntpath.normcase(pointed) != ntpath.normcase(target) or not os.path.isdir(link):
        raise OSError("the build stage junction did not verify: " + link)


def _remove_build_stage(stage: str, links: list[str]) -> None:
    # os.rmdir removes a junction itself, never the folder it points at, and
    # refuses a folder that is not empty. Nothing here walks a link or removes
    # recursively; what cannot be removed stays for the private TEMP's owner.
    for link in reversed(links):
        try:
            os.rmdir(link)
        except (OSError, ValueError):
            pass
    try:
        os.rmdir(stage)
    except (OSError, ValueError):
        pass


def _private_temp() -> str:
    """The launcher's private folder, the only place a build stage may be made.

    The launcher starts the worker in its private working folder with TEMP and
    TMP set to that folder, and the MCP side removes it after the launch. So
    TEMP must be a local folder that is the working folder itself. There is no
    other candidate: tempfile.gettempdir() falls back to other folders when TEMP
    is unusable (review R1, F1), and a stage there, with its junctions to real
    folders, could outlive a failed cleanup where nobody removes it.
    """
    temp = os.environ.get("TEMP")
    try:
        if (
            isinstance(temp, str)
            and _local_path(temp)
            and os.path.isdir(temp)
            and os.path.samefile(temp, os.getcwd())
        ):
            return temp
    except (OSError, ValueError):
        pass
    raise _failed("build_stage_unavailable")


@contextlib.contextmanager
def _staged_build_source(source: str) -> Iterator[str]:
    """The path AddonBuilder builds from when binarize runs: <stage>\\<basename>.

    The stage is a fresh folder in the launcher's private TEMP and nowhere
    else, and it holds only the junctions of _build_stage_plan, each verified
    after it is made. Nothing is written into the source or its parent. Before
    AddonBuilder starts, and never falling back to the source itself, the build
    fails with build_stage_unavailable when the private TEMP is unusable or a
    junction cannot be made or verified, with build_source_unavailable when the
    source or a root it needs cannot be listed or read, and with
    build_source_link_outside when the source, a root or a link in them would
    lead binarize out of the stage. On the way out, whatever the
    build did, the junctions and then the emptied stage are removed; a removal
    that fails does not replace the build's own result.
    """
    stage: str | None = None
    junctions: list[str] = []
    try:
        private_temp = _private_temp()
        try:
            plan = _build_stage_plan(source)
        except (OSError, ValueError):
            raise _failed("build_source_unavailable") from None
        try:
            stage = tempfile.mkdtemp(prefix=_BUILD_STAGE_PREFIX, dir=private_temp)
            for name, target in plan:
                junction = ntpath.join(stage, name)
                junctions.append(junction)
                _create_verified_junction(target, junction)
            staged = ntpath.join(stage, plan[0][0])
        except (OSError, ValueError):
            raise _failed("build_stage_unavailable") from None
        if not _local_path(staged):
            raise _failed("build_stage_unavailable")
        yield staged
    finally:
        if stage is not None:
            _remove_build_stage(stage, junctions)


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
    stage_build_source: Callable[
        [str], contextlib.AbstractContextManager[str]
    ] = _staged_build_source,
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
        # bf5c / 8cf9: only a binarizing build is staged; -packonly runs no
        # binarize and keeps building from the source itself.
        staging = (
            contextlib.nullcontext(source) if pack_only else stage_build_source(source)
        )
        with staging as build_source:
            result = await _invoke(
                broker,
                native_broker_protocol.BrokerKind.ADDON_BUILDER,
                {
                    "clear": bool(payload["clean"]),
                    "pack_only": pack_only,
                    "prefix": runtime.mod,
                    "source": build_source,
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
