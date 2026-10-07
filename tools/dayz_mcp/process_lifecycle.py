from __future__ import annotations

# Every transition into RUNNING_IDLE goes through the fence; the poll
# re-validates after re-acquiring the lock.

import contextvars
import dataclasses
import hashlib
import io
import json
import logging
import math
import os
import re
import subprocess
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, TypeVar

from dayz_mcp import dayz_test_storage, window_close
from dayz_mcp.box_admission import box_admission
from dayz_mcp.child_environment import whitelisted_child_environment
from dayz_mcp.input_activity import InputAttributor, InputSample
from dayz_mcp.instance_fence import BindingPrepareError, format_creation_time_utc
from dayz_mcp.native_process_guard import identity_hashes
from dayz_mcp.steam_launch_guard import Preparation
from dayz_mcp.steam_prepare_supervisor import SteamPreparationGate
from dayz_mcp.runtime_state import RuntimePaths, atomic_write_bytes, atomic_write_json
from dayz_mcp.session_coordination import (
    AuthorizationDecision,
    CleanupDisposition,
    ClientIdentity,
    SessionCoordinator,
    public_audit_stage,
)


# Set by the idle warden around close_run / stop_run. A wrapper that forwards
# only the public arguments still runs the guarded body, because the
# authorization travels with the calling thread. Applied only when the client
# session, the operation, and the run id all match.
_IDLE_GUARD: contextvars.ContextVar[Mapping[str, object] | None] = contextvars.ContextVar(
    "dayz_idle_guard",
    default=None,
)
# Roles a caller may name on close_run_roles. close_run(only_roles=None)
# still closes every owned role.
_CLOSE_ROLE_NAMES = frozenset({"client", "server"})


@contextmanager
def idle_destruction_guard(guard: Mapping[str, object]):
    token = _IDLE_GUARD.set(guard)
    try:
        yield
    finally:
        _IDLE_GUARD.reset(token)


RUN_STATES = frozenset(
    {"STARTING", "RUNNING", "RUNNING_IDLE", "STOPPING", "EXITED", "UNRECONCILED"}
)
_ACTIVE_STATES = RUN_STATES - {"EXITED"}
# States a dead-run reaper may retire: they hold ProcessRecords but no in-flight
# lifecycle operation. STARTING/STOPPING (operation in progress under the lock) are
# left to recover_after_restart; EXITED is terminal.
_REAPABLE_STATES = frozenset({"RUNNING", "RUNNING_IDLE", "UNRECONCILED"})
_HEX = frozenset("0123456789abcdef")
_IDENTITY_SCHEMES = frozenset({"legacy-wmi-v1", "psutil-argv-v2"})
_BOX_MOD_CAP = 12
_BOX_OCCUPANCY_CACHE_S = 1.5
# fb-20260904-114520-6927: images whose bound UDP ports make the box occupied
# even without a run record. Mirrors orphan_guard so a probe fed from another
# source is classified the same way.
_DAYZ_IMAGE_NAMES = frozenset(
    {"dayzdiag_x64.exe", "dayzserver_x64.exe", "dayz_x64.exe", "dayz_be.exe"}
)
# The band this project launches in: 2302 by default, alternates at +100 steps
# (2402, 2502, ...) and the query/steam neighbours of each. A holder of one of
# these is reported in ports_in_use whatever its image, so a caller can pick
# another port; only DayZ images occupy the box.
_DAYZ_PORT_RANGE = range(2302, 3000)
# Visible window that does not take the foreground.
SW_SHOWNOACTIVATE = 4
# fb-20260822-025926-bad7: what DayZ writes on stdout/stderr is kept in files
# beside the role's RPT instead of being lost. Each stream keeps its first
# LAUNCH_OUTPUT_CAP_BYTES, then only the last LAUNCH_OUTPUT_TAIL_BYTES, which
# are appended when the stream ends: the exit reason a dying process prints is
# at the end, and a chatty one cannot fill the disk. A whole session RPT is
# 50-110 KB (profiles of 2026-09-30), so the cap holds any normal run whole.
LAUNCH_OUTPUT_CAP_BYTES = 4 * 1024 * 1024
LAUNCH_OUTPUT_TAIL_BYTES = 64 * 1024
_LAUNCH_OUTPUT_CHUNK_BYTES = 64 * 1024
_LAUNCH_OUTPUT_NAME_TOKEN = re.compile(r"[^A-Za-z0-9_.-]")
_PORT_SCAN_UNKNOWN_HINT = (
    "port_scan_unknown: the daemon could not read the host UDP socket table "
    "(psutil/netstat); waiting does not help, restore that first"
)
_ACTIVITY_STALE_S = 900.0
# 250f (D-80): an ownerless run with no input in its windows for this long is
# past the cut. The use_state published in box rows is measured against it.
RUN_IDLE_CUT_S = 600.0
# 250f (plan residue C1): with no good input sample for this long, the signal
# is unknown and a run with a live client reads use_state unknown.
INPUT_SIGNAL_STALE_S = 2.0
_ACTIVE_RUN_STOP_HINT = "stop it with dayz_test_stop(run_id={run_id})"
_ACTIVE_RUN_WAIT_HINT = "retry with wait_for_box_s=<n>"
_ACTIVE_RUN_TAKEOVER_HINT = (
    "pass takeover=true to evict occupied_by_run_id={run_id}; "
    "do not dayz_test_stop a run you do not own"
)


def _valid_uuid4(value: object) -> bool:
    if not isinstance(value, str) or value != value.casefold():
        return False
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


_COMPLETED_ROTATION_JOURNAL = re.compile(
    r"^storage_1\.modset\.rotation\.([0-9a-f]{32})\.completed\.json$"
)


_AMBIGUOUS_PENDING_ROTATION = object()


def _pending_completed_rotation(
    mission: str, seal: str
) -> dayz_test_storage.RotationResult | None | object:
    """Reset already committed for this seal, while replacement storage is absent.

    Reads a validated completed journal. Does not move or delete anything.
    An unreadable completed file is skipped, not a refusal. Several completed
    journals for the same seal (B->A, A->B, B->A) cannot be told apart, since
    transaction ids are not chronological: that is _AMBIGUOUS_PENDING_ROTATION,
    and the caller records the observation as unknown instead of guessing.
    """
    try:
        entries = os.listdir(mission)
    except OSError:
        return None
    matches: list[tuple[str, dict[str, object]]] = []
    for name in entries:
        matched = _COMPLETED_ROTATION_JOURNAL.fullmatch(name)
        if matched is None:
            continue
        document = dayz_test_storage._valid_journal(
            dayz_test_storage._read_json(os.path.join(mission, name)),
            matched.group(1),
        )
        if (
            document is None
            or document.get("phase") != dayz_test_storage.PHASE_MARKER_PUBLISHED
            or document.get("new_seal") != seal
        ):
            continue
        backup = document.get("storage_backup")
        if not _plain_storage_backup_name(backup) or not os.path.isdir(
            os.path.join(mission, str(backup))
        ):
            continue
        matches.append((name, document))
    if not matches:
        return None
    if len(matches) > 1:
        return _AMBIGUOUS_PENDING_ROTATION
    document = matches[0][1]
    marker_backup = document.get("marker_backup")
    return dayz_test_storage.RotationResult(
        launch_allowed=True,
        storage_rotated=True,
        storage_backup=str(document["storage_backup"]),
        storage_marker_backup=(
            str(marker_backup)
            if _plain_storage_backup_name(marker_backup)
            else None
        ),
        storage_seal=seal[:8],
        storage_recovery_required=False,
        storage_reset_notice=dayz_test_storage.RESET_NOTICE,
        decision=dayz_test_storage.DECISION_ROTATE,
        reason="pending_completed_rotation",
    )


def _plain_storage_backup_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value not in {".", ".."}
        and not any(separator in value for separator in ("/", "\\", ":"))
    )


def _storage_rotation_from_payload(
    value: dict[str, object],
) -> tuple[bool | None, str | None, str | None]:
    """Legacy rows omit the keys and stay unknown. A present key is validated."""
    if not any(
        key in value
        for key in ("storage_rotated", "storage_backup", "storage_reset_notice")
    ):
        return None, None, None
    rotated = value.get("storage_rotated")
    backup = value.get("storage_backup")
    notice = value.get("storage_reset_notice")
    _validate_storage_rotation(rotated, backup, notice)
    if rotated is True:
        return True, backup if isinstance(backup, str) else None, (
            notice if isinstance(notice, str) else None
        )
    if rotated is False:
        return False, None, None
    return None, None, None


def _storage_observations_from_payload(value: object) -> list[dict[str, object]]:
    """Advisory diagnostic list: never refuses the manifest, degrades per entry.

    An entry that cannot be validated is dropped, and a later version may add
    keys per entry: extra keys are ignored, never copied. A run id named twice
    anywhere in the original list -- even by an entry whose other fields do not
    validate -- is ambiguous, so every copy is dropped before the rest is
    validated. Unknown, never a guess.
    """
    if not isinstance(value, list):
        return []
    # Count before per-entry validation: a malformed twin still makes the id
    # ambiguous, and a measured value must not survive that ambiguity.
    seen: set[str] = set()
    repeated: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        run_id = item.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        if run_id in seen:
            repeated.add(run_id)
        else:
            seen.add(run_id)
    observations: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict) or not {
            "run_id",
            "storage_rotated",
            "storage_backup",
            "storage_reset_notice",
        }.issubset(item):
            continue
        run_id = item.get("run_id")
        rotated = item.get("storage_rotated")
        backup = item.get("storage_backup")
        notice = item.get("storage_reset_notice")
        if not isinstance(run_id, str) or not run_id or run_id in repeated:
            continue
        try:
            _validate_storage_rotation(rotated, backup, notice)
        except ValueError:
            continue
        if type(rotated) is not bool:
            continue
        observations.append(
            {
                "run_id": run_id,
                "storage_rotated": rotated,
                "storage_backup": backup,
                "storage_reset_notice": notice,
            }
        )
    if len(observations) > _STORAGE_OBSERVATION_BOUND:
        del observations[: len(observations) - _STORAGE_OBSERVATION_BOUND]
    return observations


def _validate_storage_rotation(
    rotated: object, backup: object, notice: object
) -> None:
    if rotated is None or rotated is False:
        if backup is not None or notice is not None:
            raise ValueError("invalid_run_record")
        return
    if rotated is not True:
        raise ValueError("invalid_run_record")
    if (
        not _plain_storage_backup_name(backup)
        or notice != dayz_test_storage.RESET_NOTICE
    ):
        raise ValueError("invalid_run_record")


def _valid_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and value == value.casefold()
        and all(character in _HEX for character in value)
    )


def _default_argv_of(pid: int) -> list[str] | None:
    try:
        from dayz_mcp.native_process_snapshot import command_argv_of
    except Exception:
        return None
    return command_argv_of(pid)


def _parse_port_token(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _public_profiles_label(value: object) -> str | None:
    """Publish a directory basename, never a host-absolute path."""

    if not isinstance(value, str) or not value.strip():
        return None
    path = value.replace("/", "\\").rstrip("\\")
    parts = [part for part in path.split("\\") if part]
    if not parts:
        return None
    if parts[-1].casefold() == "profiles" and len(parts) >= 2:
        return parts[-2]
    return parts[-1]


def _summarize_mod_tokens(raw: list[str]) -> list[str]:
    names: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            continue
        name = item.replace("/", "\\").rstrip("\\")
        base = name.rsplit("\\", 1)[-1]
        if not base or base in names:
            continue
        names.append(base)
        if len(names) >= _BOX_MOD_CAP:
            break
    return names


def parse_dayz_launch_argv(argv: object) -> dict[str, object]:
    """Extract public -port/-mod/-profiles tokens from a DayZDiag argv."""

    ports: list[int] = []
    mods: list[str] = []
    profiles: str | None = None
    if not isinstance(argv, list):
        return {"ports": ports, "mods": mods, "profiles": profiles}
    index = 0
    while index < len(argv):
        argument = argv[index]
        if not isinstance(argument, str) or not argument:
            index += 1
            continue
        if argument.startswith("-port="):
            port = _parse_port_token(argument[6:])
            if port is not None and port not in ports:
                ports.append(port)
        elif argument == "-port" and index + 1 < len(argv):
            port = _parse_port_token(argv[index + 1])
            if port is not None and port not in ports:
                ports.append(port)
            index += 1
        elif argument.startswith("-mod=") or argument.startswith("-serverMod="):
            payload = argument.split("=", 1)[1]
            mods.extend(part for part in payload.split(";") if part)
        elif argument.startswith("-profiles="):
            profiles = argument[10:] or None
        index += 1
    return {
        "ports": ports,
        "mods": _summarize_mod_tokens(mods),
        "profiles": profiles,
    }


def _is_dayz_image(name: object) -> bool:
    return isinstance(name, str) and name.casefold() in _DAYZ_IMAGE_NAMES


def _requested_port(argv: object) -> int | None:
    """The -port this launch asked for, or None when argv names none."""
    ports = parse_dayz_launch_argv(argv).get("ports")
    if isinstance(ports, list) and ports and isinstance(ports[0], int):
        return ports[0]
    return None


def _launch_output_dir(argv: object) -> str | None:
    """The -profiles folder of a launch, where its RPT goes, or None.

    Only an absolute folder named by the argv itself: the capture must land
    beside the RPT of that process, never in a directory guessed for it.
    """
    profiles = parse_dayz_launch_argv(argv).get("profiles")
    if not isinstance(profiles, str) or not profiles:
        return None
    try:
        absolute = Path(profiles).is_absolute()
    except (OSError, ValueError):
        return None
    return profiles if absolute else None


def _open_launch_output(target: Path) -> io.RawIOBase | None:
    """Create the capture file; never truncate one that already exists.

    Opened by the daemon after the spawn and not inheritable (PEP 446), so
    no process holds it but the drain thread, which closes it at EOF.
    """
    stem, _dot, suffix = target.name.partition(".")
    for attempt in range(1, 10):
        name = target.name if attempt == 1 else f"{stem}-{attempt}.{suffix}"
        try:
            return open(target.with_name(name), "xb", buffering=0)
        except FileExistsError:
            continue
        except (OSError, ValueError):
            return None
    return None


def _write_all(handle: io.RawIOBase, data: bytes | bytearray) -> None:
    """A raw write may take part of the buffer; loop until it took all."""
    view = memoryview(data)
    while view:
        count = handle.write(view)
        if not count:
            raise OSError("launch_output_short_write")
        view = view[count:]


def _pump_launch_output(
    stream: object, target: Path, cap: int, tail_cap: int
) -> None:
    """Drain one console stream of a launched DayZ into its capture file.

    The pipe is read to its end whatever happens to the file: a child that
    writes into a pipe nobody reads blocks, and a blocked game is worse than
    a lost log. The file is created on the first byte, so a process that
    writes nothing leaves nothing. Up to ``cap`` bytes are written as they
    arrive; past that only the last ``tail_cap`` bytes are kept, and they are
    appended after a marker when the stream ends. Whenever this returns, the
    read end is closed, so the child can never block on it afterwards.
    """
    read = getattr(stream, "read1", None) or getattr(stream, "read", None)
    handle: io.RawIOBase | None = None
    failed = False
    written = 0
    dropped = 0
    tail = bytearray()
    try:
        while callable(read):
            try:
                chunk = read(_LAUNCH_OUTPUT_CHUNK_BYTES)
            except (OSError, ValueError):
                break
            if not chunk or not isinstance(chunk, (bytes, bytearray)):
                break
            if failed:
                continue
            try:
                if handle is None:
                    handle = _open_launch_output(target)
                    if handle is None:
                        failed = True
                        continue
                head = chunk[: max(0, cap - written)]
                if head:
                    _write_all(handle, head)
                    written += len(head)
                rest = chunk[len(head):]
                if rest:
                    dropped += len(rest)
                    tail += rest
                    if len(tail) > tail_cap:
                        del tail[: len(tail) - tail_cap]
            except Exception:
                # Disk full, a vanished folder: stop writing, keep draining.
                failed = True
    finally:
        if handle is not None:
            try:
                if dropped and not failed:
                    marker = (
                        f"\n[dayz-mcp] capture cap of {cap} bytes reached: "
                        f"{dropped - len(tail)} bytes not kept, the last "
                        f"{len(tail)} follow\n"
                    )
                    _write_all(handle, marker.encode("ascii") + bytes(tail))
            except Exception:
                pass
            try:
                handle.close()
            except Exception:
                pass
        try:
            stream.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def _start_launch_output_capture(
    process: object, directory: str, argv: list[str]
) -> None:
    """One drain thread per console stream of a real Popen. Never raises.

    Named after the RPT DayZ writes in the same folder (exe stem, local
    launch time) plus the pid, with a .txt suffix so no RPT or script-log
    reader (log_tail, _script_log_for_server_rpt) ever picks it up.
    """
    pending = [
        (channel, stream)
        for channel, stream in (
            ("stdout", getattr(process, "stdout", None)),
            ("stderr", getattr(process, "stderr", None)),
        )
        if isinstance(stream, io.IOBase)
    ]
    try:
        pid = getattr(process, "pid", None)
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            pid = 0
        stem = _LAUNCH_OUTPUT_NAME_TOKEN.sub("_", Path(str(argv[0])).stem)[:64]
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        while pending:
            channel, stream = pending[0]
            target = Path(directory) / (
                f"{stem or 'dayz'}_{stamp}_{pid}.{channel}.txt"
            )
            threading.Thread(
                target=_pump_launch_output,
                args=(
                    stream,
                    target,
                    LAUNCH_OUTPUT_CAP_BYTES,
                    LAUNCH_OUTPUT_TAIL_BYTES,
                ),
                name=f"dayz-launch-{channel}-{pid}",
                daemon=True,
            ).start()
            pending.pop(0)
    except Exception:
        # A stream without a reader is closed, so the child's writes fail
        # instead of blocking once the pipe buffer fills.
        for _channel, stream in pending:
            try:
                stream.close()
            except Exception:
                pass


# fb-20260818-232129-1233: a server that hangs while loading keeps its process
# and stops writing its RPT; the UDP readiness probe (sealed, in the worker)
# only sees readiness_timeout. The daemon watches the RPT of each server launch
# while it starts, and when that start is stopped it records whether the RPT
# had gone silent for more than SERVER_START_HUNG_AFTER_S. The 75 s is the
# watchdog of the ficha, which told the two hangs of 2026-08-19 (over 6 min
# without a line) from the loads of 60-90 s. Measured 2026-09-30 over the 443
# server RPTs of DayZ_MCP_dev\_server\profiles that reach "Player connect
# enabled": the longest silence of a start is p50 6.4 s, p99 19.7 s, but three
# healthy starts went silent for 80.7, 108.3 and 113.7 s (CE map load, entity
# load) and then finished. The verdict only names a readiness timeout that
# already happened; it stops, kills or relaunches nothing.
SERVER_START_HUNG = "server_start_hung"
SERVER_START_HUNG_AFTER_S = 75.0
# The widest readiness window a request can ask for (dayz_test_request bounds
# server_wait_s to [1, 3600]); past it no readiness is left to explain.
_SERVER_START_WATCH_MAX_S = 3600.0
_SERVER_START_SAMPLE_INTERVAL_S = 1.0
# An RPT of this launch cannot be older than the launch; the slack absorbs
# file-time granularity between the daemon clock and the file system.
_LAUNCH_RPT_SLACK_S = 2.0


def _launch_rpt(profiles: str, launched_at: float) -> tuple[str, int, float] | None:
    """(path, size, mtime) of the newest .rpt written since a launch, or None.

    The directory listing only selects candidates: an RPT open for writing
    can show a stale size and time in it. Each candidate is stat'ed again,
    which reads the file itself.
    """
    floor = launched_at - _LAUNCH_RPT_SLACK_S
    candidates: list[str] = []
    try:
        with os.scandir(profiles) as entries:
            for entry in entries:
                if not entry.name.casefold().endswith(".rpt"):
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    listed = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if listed.st_mtime >= floor:
                    candidates.append(entry.path)
    except (OSError, ValueError):
        return None
    newest: tuple[str, int, float] | None = None
    for path in candidates:
        try:
            fresh = os.stat(path)
        except OSError:
            continue
        if newest is None or fresh.st_mtime > newest[2]:
            newest = (path, int(fresh.st_size), float(fresh.st_mtime))
    return newest


@dataclass
class _ServerStartWatch:
    """What the daemon saw of the RPT of one server launch while it started."""

    run_id: str
    profiles: str
    launched_at: float
    seen: tuple[str, int, float] | None = None
    grew_at: float | None = None
    sampled_at: float | None = None

    def sample(self, now: float) -> None:
        self.observe(_launch_rpt(self.profiles, self.launched_at), now)

    def observe(self, found: tuple[str, int, float] | None, now: float) -> None:
        """Fold one look at the RPT (path, size, mtime) taken at ``now``."""
        if self.sampled_at is not None and now < self.sampled_at:
            return
        previous = self.sampled_at if self.sampled_at is not None else self.launched_at
        self.sampled_at = now
        if found is None or found == self.seen:
            return
        self.seen = found
        # It changed after the previous look. Its mtime says when, unless the
        # mtime falls outside that window: then the latest moment it can have
        # been, so a doubtful clock never lengthens a silence.
        mtime = found[2]
        self.grew_at = mtime if previous < mtime <= now else now

    def verdict(self, now: float) -> dict[str, object] | None:
        """The hung verdict, or None: no RPT seen means no verdict at all."""
        if self.grew_at is None:
            return None
        stalled = now - self.grew_at
        if stalled <= SERVER_START_HUNG_AFTER_S:
            return None
        return {
            "run_id": self.run_id,
            "code": SERVER_START_HUNG,
            "rpt_stalled_s": round(stalled, 1),
        }


def _utc_epoch(value: object) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, rest = text.split(".", 1)
        digits = ""
        suffix = rest
        for offset, char in enumerate(rest):
            if char.isdigit():
                digits += char
                continue
            suffix = rest[offset:]
            break
        else:
            suffix = ""
        text = f"{head}.{(digits + '000000')[:6]}{suffix}"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _run_age_s(run: RunRecord, now: float) -> float:
    stamps = [
        stamp
        for stamp in (_utc_epoch(record.creation_time_utc) for record in run.processes)
        if stamp is not None
    ]
    if not stamps:
        return 0.0
    return max(0.0, now - min(stamps))


def empty_box(*, occupied: bool) -> dict[str, object]:
    return {
        "occupied": occupied,
        "runs": [],
        "foreign": [],
        "ports_in_use": [],
        "queue": [],
    }


def _copy_box(payload: dict[str, object]) -> dict[str, object]:
    runs = payload.get("runs")
    foreign = payload.get("foreign")
    ports = payload.get("ports_in_use")
    queue = payload.get("queue")
    return {
        "occupied": bool(payload.get("occupied")),
        "runs": [dict(item) for item in runs] if isinstance(runs, list) else [],
        "foreign": [dict(item) for item in foreign] if isinstance(foreign, list) else [],
        "ports_in_use": list(ports) if isinstance(ports, list) else [],
        "queue": list(queue) if isinstance(queue, list) else [],
        # Additive. Missing/unknown â†’ False (fail-closed). Distinguishes a
        # clean empty foreign list from a scan that never ran.
        "scan_known": payload.get("scan_known") is True,
    }


@dataclass(frozen=True)
class _BoxSnapshot:
    clock: float
    runs: tuple[RunRecord, ...]
    activity: Mapping[str, float]
    unknown: frozenset[str]
    revision: int
    daemon_generation: str = ""
    compensating: frozenset[str] = field(default_factory=frozenset)
    # 250f: who is using each run, from daemon memory of this generation.
    ownerless_since: Mapping[str, float] = field(default_factory=dict)
    human_input: Mapping[str, float] = field(default_factory=dict)
    uncertain_input: Mapping[str, float] = field(default_factory=dict)
    launched_by: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    input_good_at: float | None = None
    signal_recovered_at: float | None = None
    use_clock_origin: float | None = None
    bound_runs: frozenset[str] = field(default_factory=frozenset)
    # 250f PR 3: launcher request (§3.2), the warden's countdown, and a
    # warning that failed and must not be retried yet.
    launcher_request: Mapping[str, float] = field(default_factory=dict)
    closing_runs: frozenset[str] = field(default_factory=frozenset)
    warning_blocked: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class _BoxProbes:
    foreign: tuple[dict[str, object], ...]
    ports_in_use: tuple[int, ...]
    scan_known: bool
    port_scan_known: bool = True
    port_scan_reason: str | None = None
    foreign_ports: tuple[int, ...] = ()
    foreign_ports_dayz_related: tuple[int, ...] = ()
    # 250f: registered pids the diag scan saw alive; None when it did not run.
    live_pids: frozenset[int] | None = None


def _activity_from_snapshot(
    snapshot: _BoxSnapshot, run_id: str
) -> tuple[str, float | None]:
    if run_id in snapshot.unknown or run_id in snapshot.compensating:
        return "unknown", None
    stamp = snapshot.activity.get(run_id)
    if stamp is None:
        return "unknown", None
    if stamp > snapshot.clock:
        return "unknown", None
    age = snapshot.clock - stamp
    if age <= _ACTIVITY_STALE_S:
        return "recent", round(age, 3)
    return "stale", round(age, 3)


# 250f: the roles a person can play in. An offline run is a client with its
# own mission (dayz_test_worker treats it as one), never a server alone.
_PLAYER_ROLES = frozenset({"client", "offline"})
# 250f: past this many remembered exclusions, the ones no active run holds any
# more are pruned. Live ones are never dropped (review #125 R3).
_EXCLUSION_PRUNE_AT = 256
# Active-run client observations. Daemon memory only. A restart starts empty.
# Identities no active run still holds are the ones a full table drops.
_CLIENT_ROLE_OBSERVATION_BOUND = 32
# Observation strength for one client identity. A death is terminal and an
# alive probe beats an unknown one, so a weaker observation never erases a
# stronger stored one.
_CLIENT_STATE_STRENGTH = {"dead": 2, "alive": 1}


def _merge_client_observations(
    previous: dict[str, object] | None,
    observation: dict[str, object],
) -> dict[str, object]:
    """Fold one probe result into the latest stored observation.

    The probe ran outside the table lock, so by the time it is stored another
    reader may already have published a stronger row for the same identity.
    The merged row never regresses: a weaker or older state does not replace
    a stronger one. Recorded death evidence is write-once: an existing
    first-death stamp, exit code or exit time is kept, and an incoming
    observation only fills a field that is still missing. A conflicting
    exact exit value is ignored.
    """

    merged = dict(observation)
    if not isinstance(previous, dict):
        return merged
    new_state = merged.get("state")
    old_state = previous.get("state")
    if _CLIENT_STATE_STRENGTH.get(new_state, 0) < _CLIENT_STATE_STRENGTH.get(
        old_state, 0
    ):
        merged["state"] = old_state
    prev_dead = previous.get("first_observed_dead_at_utc")
    if isinstance(prev_dead, str) and prev_dead:
        merged["first_observed_dead_at_utc"] = prev_dead
    for field in ("exit_code", "exit_time_utc"):
        if previous.get(field) is not None:
            merged[field] = previous.get(field)
        elif merged.get(field) is None:
            merged[field] = previous.get(field)
    new_seen = merged.get("observed_at_utc")
    old_seen = previous.get("observed_at_utc")
    if isinstance(new_seen, str) and isinstance(old_seen, str) and new_seen < old_seen:
        # A reader that probed before the stored row was published still
        # observed: the stamp never moves backwards.
        merged["observed_at_utc"] = old_seen
    return merged


def _client_liveness(run: RunRecord, probes: _BoxProbes) -> str:
    """alive, dead or unknown for the run's player processes (250f).

    A run without a client or offline record is a server alone: nobody can be
    playing in it, so it reads dead. An unknown diag scan never confirms a
    death.
    """

    clients = [
        record.pid for record in run.processes if record.role in _PLAYER_ROLES
    ]
    if not clients:
        return "dead"
    if probes.live_pids is None:
        return "unknown"
    return "alive" if any(pid in probes.live_pids for pid in clients) else "dead"


def _use_projection(
    run: RunRecord,
    snapshot: _BoxSnapshot,
    probes: _BoxProbes,
    active_count: int,
) -> dict[str, object]:
    """250f (plan v2.1 §3.3): use_state and what it was measured from.

    adopt_run reads use_state: a RUNNING_IDLE run that is not abandoned is
    protected from every session except the one that launched it.
    Precedence: closing, agent, unknown, human, idle, then past
    RUN_IDLE_CUT_S idle_waiting (with a reason) or abandoned. STARTING,
    STOPPING and UNRECONCILED runs are not classified.
    """

    clock = snapshot.clock
    launcher = snapshot.launched_by.get(run.run_id)
    fields: dict[str, object] = {
        "use_state": None,
        "use_reason": None,
        "idle_s": None,
        "human_input_age_s": None,
        "launched_by": dict(launcher) if launcher is not None else None,
    }
    human_at = snapshot.human_input.get(run.run_id)
    if human_at is not None and human_at > clock:
        human_at = None
    if human_at is not None:
        fields["human_input_age_s"] = round(clock - human_at, 3)
    # closing beats agent: the warden owns the run and is counting down.
    if run.state == "RUNNING" and run.run_id in snapshot.closing_runs:
        fields["use_state"] = "closing"
        return fields
    if run.state == "RUNNING" and run.owner_session_id:
        fields["use_state"] = "agent"
        return fields
    if run.state != "RUNNING_IDLE":
        return fields
    client = _client_liveness(run, probes)
    uncertain_at = snapshot.uncertain_input.get(run.run_id)
    if uncertain_at is not None and uncertain_at > clock:
        uncertain_at = None
    # A stretch without a valid signal may have held use that nobody saw: with
    # a client that may be alive, the clock restarts where the signal came
    # back (review #125 F4).
    recovered_at = snapshot.signal_recovered_at if client != "dead" else None
    since = snapshot.ownerless_since.get(run.run_id, snapshot.use_clock_origin)
    launcher_at = snapshot.launcher_request.get(run.run_id)
    if launcher_at is not None and launcher_at > clock:
        launcher_at = None
    anchors = [
        value
        for value in (since, human_at, uncertain_at, recovered_at, launcher_at)
        if value is not None and value <= clock
    ]
    if not anchors:
        fields["use_state"] = "unknown"
        fields["use_reason"] = "clock_unavailable"
        return fields
    idle_s = max(0.0, clock - max(anchors))
    fields["idle_s"] = round(idle_s, 3)
    good_at = snapshot.input_good_at
    signal_ok = good_at is not None and 0.0 <= clock - good_at <= INPUT_SIGNAL_STALE_S
    if client != "dead" and not signal_ok:
        fields["use_state"] = "unknown"
        fields["use_reason"] = "input_signal_unavailable"
        return fields
    if human_at is not None and clock - human_at < RUN_IDLE_CUT_S:
        fields["use_state"] = "human"
        return fields
    if uncertain_at is not None and clock - uncertain_at < RUN_IDLE_CUT_S:
        # Input went to a window whose identity could not be read: a doubt,
        # published as such, never as idle (review #125 F2).
        fields["use_state"] = "unknown"
        fields["use_reason"] = "identity_unverified"
        return fields
    if idle_s < RUN_IDLE_CUT_S:
        fields["use_state"] = "idle"
        return fields
    reason: str | None = None
    if active_count > 1:
        reason = "multiple_active_runs"
    elif not probes.scan_known or not probes.port_scan_known:
        reason = "scan_unknown"
    elif client == "dead":
        fields["use_state"] = "abandoned"
        fields["use_reason"] = "client_gone"
        return fields
    elif run.daemon_generation_at_launch != snapshot.daemon_generation:
        reason = "previous_generation"
    elif run.run_id not in snapshot.bound_runs:
        reason = "bridge_not_ready"
    blocked_until = snapshot.warning_blocked.get(run.run_id)
    if (
        reason is None
        and client != "dead"
        and isinstance(blocked_until, (int, float))
        and not isinstance(blocked_until, bool)
        and clock < float(blocked_until)
    ):
        reason = "warning_failed"
    if reason is not None:
        fields["use_state"] = "idle_waiting"
        fields["use_reason"] = reason
        return fields
    fields["use_state"] = "abandoned"
    return fields


# Generation ids are minted as uuid4().hex and projections and stop envelopes publish
# them on the MCP wire; a persisted value outside this token is reported as unknown.
_GENERATION_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_STEAM_PREP_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_STEAM_PREPARATION_BOUND = 32


def _steam_prep_token(value: object) -> str | None:
    return value if type(value) is str and _STEAM_PREP_TOKEN.fullmatch(value) else None


def _steam_preparation_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _generation_projection(run: RunRecord, current: str) -> dict[str, object]:
    launch = getattr(run, "daemon_generation_at_launch", None)
    if not isinstance(launch, str) or not _GENERATION_TOKEN.fullmatch(launch):
        launch = None
    current_value = current if isinstance(current, str) else ""
    changed = None if launch is None else launch != current_value
    return {
        "daemon_generation_at_launch": launch,
        "daemon_generation_current": current_value,
        "generation_changed": changed,
    }


_DIAGNOSTIC_PUBLIC_KEYS = (
    "run_id",
    "daemon_generation_at_launch",
    "daemon_generation_current",
    "generation_changed",
    "event",
    "reason",
    "decision",
    "state",
)
_BINDING_REASON_BY_EVENT = {
    "lifecycle_stop_outcome": "stopped",
    "lifecycle_owner_released": "released",
    "lifecycle_recovery_repair": "recovery_repaired",
    "lifecycle_manifest_recovery": "manifest_repaired",
    "run_reaped": "reaped",
    "admin_reconcile": "admin_reconciled",
}
_RUN_PROCESSES_GONE_HINT = (
    "This run has no live owned process. Reap or recover the run "
    "(reap_dead_run / reap_dead_runs / admin reconcile) so the durable "
    "EXITED state converges; adopt_run does not restore a dead run."
)
_ADOPT_NOT_DISPATCHABLE_HINT = (
    "Instance bindings do not survive a daemon restart. This run admits "
    "stop_run and relaunch; mutations cannot dispatch until then."
)
# fb-20260904-025733-d60f: states an ownerless run may be adopted from. A stop
# that degraded leaves the run UNRECONCILED with no owner and, when the guard
# could not end it, with a live process of its own: stop_run demands RUNNING
# with an owner, reap_dead_run answers process_alive while the process lives,
# and admin_reconcile is a TTY-confirmed admin path, so the box stayed occupied
# with no verb a session could use. Adopting it re-enters the machine that
# already exists: UNRECONCILED -> adopt -> RUNNING -> stop -> EXITED. The
# safety discriminator does not move: still a lease, still no record the guard
# cannot vouch for, still at least one owned process.
_ADOPTABLE_STATES = frozenset({"RUNNING_IDLE", "UNRECONCILED"})
# fb-20260904-025027-8f76 (part c): a process that has already exited can
# outlive its own row in the host UDP table. start_run reads that table again
# right before the launcher, and a pid that is no longer a registered process
# reads there as a foreign holder, so retiring the record while the socket
# lives would leave the role dead AND unlaunched - worse than the state it
# started from. The wait for the release is bounded by contract, never by an
# open sleep: at most _ROLE_RELEASE_TRIES probes spaced
# _ROLE_RELEASE_INTERVAL_S apart, then port_still_held and nothing is retired.
_ROLE_RELEASE_TRIES = 10
_ROLE_RELEASE_INTERVAL_S = 0.25
_PORT_STILL_HELD_HINT = (
    "port_still_held: the superseded process ended but the host UDP socket "
    "table still lists its pid. Nothing was retired and nothing was launched; "
    "repeat the same call once the socket is released."
)
# fb-20260904-200816-79e2 / H-A2-2. The extension gate decides in the MCP server
# process and the kill happens here, after composing the sealed request, opening
# the launcher, starting app.pyz and two broker round trips. A4 measured that
# window on the durable audit: n=26, min 0,47 s, median 7,00 s, max 29,16 s --
# the same order as PEER_STALE_S. The bound below is twice the measured maximum,
# so a legitimate call never trips it while a request replayed minutes later does.
_REPLACE_WITNESS_MAX_AGE_S = 60.0
# fb-20260904-200821-dae1 part 1 (H-A2-3): the reaper can retire a run between
# the adopt and the stop of the worker. The owner a reap cleared is kept here,
# daemon memory only and bounded like _retired_diagnostics, so the stop of that
# owner can be answered as the exit it is instead of run_not_adopted.
_REAPED_OWNER_MEMORY = 32
# fb-20260904-200821-dae1 part 2 (A1-F7): the launch intent. A daemon that died
# between the Popen of start_run and the manifest write left the process it had
# just launched outside every row. start_run writes this file, fsynced, right
# before the Popen, and removes it once the durable row is no longer STARTING;
# the next daemon start reads it in recover_launch_intent. File: beside
# runs.json. Format, version 1, every field required and no other allowed:
#   version              1
#   run_id               the run the launch creates or extends
#   role                 the launch role, as its ProcessRecord will carry it
#   instance             the bridge instance minted for this launch (UUID4);
#                        the process reads it from <profiles>\dayz_mcp.json
#   profiles             the absolute profiles folder of that config; the argv
#                        names the same folder with -profiles=
#   command_line_sha256  the argv hash the process guard will read for it
#                        (native_process_guard.identity_hashes). Never the raw
#                        command line, as runs.json never keeps one.
#   owner_session_id,
#   owner_lease_id       the owner of the STARTING row the launch belongs to
#   not_before_utc       the wall clock just before the intent was written
# No file is the legacy state: there is nothing to recover.
_LAUNCH_INTENT_NAME = "launch-intent.json"
_LAUNCH_INTENT_VERSION = 1
_LAUNCH_INTENT_FIELDS = frozenset(
    {
        "version",
        "run_id",
        "role",
        "instance",
        "profiles",
        "command_line_sha256",
        "owner_session_id",
        "owner_lease_id",
        "not_before_utc",
    }
)
# The process of an intent is created after it: the Popen is the next step. The
# kernel may stamp the creation time from a clock coarser than the precise one
# the daemon reads (the timer tick is 15.6 ms by default), so it can read a few
# milliseconds earlier than the intent; the slack absorbs that and nothing
# larger. The window bounds how late after the intent the process may start.
_LAUNCH_INTENT_CLOCK_SLACK_S = 1.0
_LAUNCH_INTENT_WINDOW_S = 60.0


def _request_parser_sha256() -> str | None:
    path = Path(__file__).with_name("dayz_test_request.py")
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _tree_bundle_manifest_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "native-launchers"
        / "dayz-test-v1"
        / "closure-manifest.json"
    )


def _parse_bundle_manifest(manifest_path: Path) -> tuple[str | None, str | None]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    bundle_id = payload.get("bundle_id")
    request_sha = payload.get("dayz_test_request_sha256")
    if not isinstance(bundle_id, str) or not bundle_id:
        bundle_id = None
    if not isinstance(request_sha, str) or not request_sha:
        request_sha = None
    return bundle_id, request_sha


def _installed_bundle_identity() -> tuple[str | None, str | None, str]:
    """bundle_id, request-parser hash, and which manifest supplied them."""
    tree_path = _tree_bundle_manifest_path()
    try:
        from dayz_mcp.launcher_registry import open_approved_launcher

        with open_approved_launcher("dayz-test-v1") as opened:
            manifest_path = opened.root / "closure-manifest.json"
            bundle_id, request_sha = _parse_bundle_manifest(manifest_path)
            return (
                bundle_id,
                request_sha,
                f"approved launcher manifest {manifest_path}",
            )
    except Exception:
        bundle_id, request_sha = _parse_bundle_manifest(tree_path)
        return bundle_id, request_sha, f"module-tree manifest {tree_path}"


def _printable_sha256(value: str | None) -> str:
    """One hash, one spelling. The manifest stores it upper-case and hashlib
    yields lower-case: printing both made the reader see two different strings
    under a sentence saying they were the same, which is the very confusion
    ficha e8eb exists to remove. The comparison already casefolds."""
    if not value:
        return "unreadable"
    return value.casefold()


def _replace_witness_missing_hint() -> str:
    daemon_sha = _request_parser_sha256()
    bundle_id, bundle_sha, source = _installed_bundle_identity()
    daemon_label = _printable_sha256(daemon_sha)
    bundle_label = _printable_sha256(bundle_sha)
    bundle_name = bundle_id if bundle_id else "unreadable"
    same = (
        daemon_sha is not None
        and bundle_sha is not None
        and daemon_sha.casefold() == bundle_sha.casefold()
    )
    if same:
        rebuild = (
            "The launcher bundle and this daemon carry the same "
            "dayz_test_request.py hash, so an old bundle is not the cause; "
            "look for an orphan binding. Rebuild and reinstall app.pyz only "
            "if those hashes differ."
        )
    else:
        rebuild = (
            "The launcher bundle and this daemon carry different "
            "dayz_test_request.py hashes, so the bundle may be older than "
            "this daemon. Rebuild and reinstall app.pyz only if that "
            "mismatch is the cause, not an orphan binding."
        )
    return (
        "replace_witness_missing: superseding a live client needs the witness "
        "of the gate that authorised it, carried in the sealed request. Nothing "
        "was terminated and nothing was launched. "
        f"launcher bundle {bundle_name} ({source}) "
        f"dayz_test_request_sha256={bundle_label}; "
        f"running daemon dayz_test_request_sha256={daemon_label}. "
        + rebuild
    )


_REPLACE_WITNESS_HINTS = {
    "replace_witness_missing": _replace_witness_missing_hint,
    "replace_witness_stale": (
        "replace_witness_stale: the gate read the bridge too long ago for its "
        "verdict to still stand. Nothing was terminated and nothing was "
        "launched; repeat the same call."
    ),
    "client_polling_since_decision": (
        "client_polling_since_decision: the client polled the bridge again "
        "after the gate decided it had stopped. Nothing was terminated and "
        "nothing was launched: the client is alive and serving."
    ),
    "bridge_state_unreadable": (
        "bridge_state_unreadable: the bridge state carries no usable evidence "
        "about this client, and no evidence does not authorise ending a live "
        "process. Nothing was terminated and nothing was launched."
    ),
}
_STORAGE_OBSERVATION_BOUND = 32
_STORAGE_ROTATE_HINTS = {
    "storage_rotate_failed": (
        "storage_rotate_failed: the mission storage could not be sealed or set "
        "aside for this mod set. Nothing was launched and no process was "
        "created. The mission is NOT guaranteed untouched: a transaction that "
        "failed after its first rename leaves the old tree under its backup "
        "name and an active journal beside storage_1, which the next call "
        "reconciles. Nothing is ever deleted -- v1 renames only."
    ),
    "storage_recovery_required": (
        "storage_recovery_required: the mission carries a rotation that cannot "
        "be reconciled (an ambiguous or unreadable journal, or a physical state "
        "no sequence produces). Nothing was launched. Inspect the "
        "storage_1.modset.rotation.* files next to storage_1 before retrying; "
        "no data was deleted -- v1 renames and never removes."
    ),
}
_STATUS_SNAPSHOT_TRIES = 3
_RECOVERY_REPAIR_STATES = frozenset(
    {"STARTING", "RUNNING", "RUNNING_IDLE", "STOPPING", "UNRECONCILED"}
)


def _derive_box(snapshot: _BoxSnapshot, probes: _BoxProbes) -> dict[str, object]:
    runs: list[dict[str, object]] = []
    active_count = sum(1 for run in snapshot.runs if run.state in _ACTIVE_STATES)
    for run in snapshot.runs:
        if run.state not in _ACTIVE_STATES:
            continue
        owner = run.owner_session_id
        activity_state, last_activity_age_s = _activity_from_snapshot(
            snapshot, run.run_id
        )
        row: dict[str, object] = {
            "run_id": run.run_id,
            "mod": run.mod,
            "label": run.label,
            "age_s": round(_run_age_s(run, snapshot.clock), 3),
            "owner_session": owner[:12] if isinstance(owner, str) and owner else None,
            "state": run.state,
            "activity_state": activity_state,
            "last_activity_age_s": last_activity_age_s,
        }
        row.update(_generation_projection(run, snapshot.daemon_generation))
        row.update(_use_projection(run, snapshot, probes, active_count))
        runs.append(row)
    scans_known = probes.scan_known and probes.port_scan_known
    occupied = True if not scans_known else bool(runs or probes.foreign)
    return {
        "occupied": occupied,
        "runs": runs,
        "foreign": [dict(item) for item in probes.foreign],
        "ports_in_use": list(probes.ports_in_use),
        "queue": [],
        "scan_known": probes.scan_known,
        "port_scan_known": probes.port_scan_known,
        "port_scan_reason": probes.port_scan_reason,
        # Default surface: DayZ-related holders only (image or DayZ UDP range).
        # Full OS socket table (minus managed runs) lives under foreign_ports_all
        # so conflict diagnosis can still see any held port (fb-1432 option A).
        "foreign_ports": list(probes.foreign_ports_dayz_related),
        "foreign_ports_all": list(probes.foreign_ports),
        "foreign_ports_meta": {
            "count": len(probes.foreign_ports_dayz_related),
            "count_all": len(probes.foreign_ports),
            "dayz_related": len(probes.foreign_ports_dayz_related),
            "kind": "os_socket_table_ignored_for_occupancy",
        },
    }


def _wait_hint(box: object) -> str:
    if not isinstance(box, dict):
        return _ACTIVE_RUN_WAIT_HINT
    if box.get("port_scan_known") is False:
        return _PORT_SCAN_UNKNOWN_HINT
    claimed_s = box.get("claimed_s")
    if (
        isinstance(claimed_s, (int, float))
        and not isinstance(claimed_s, bool)
        and claimed_s >= 0
    ):
        return f"retry with wait_for_box_s=<n> (claimed for {float(claimed_s):.0f}s)"
    return _ACTIVE_RUN_WAIT_HINT


def _finite_use_age(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    age = float(value)
    if not math.isfinite(age):
        return None
    return age


def caller_launched_row(row: dict[str, object], caller_session: str | None) -> bool:
    """Message-layer match: the public 12-character session prefix.

    The daemon decides with the full session id. A prefix collision can
    describe the caller as the launcher here and still be refused there.
    """

    if not isinstance(caller_session, str) or not caller_session:
        return False
    launched = row.get("launched_by")
    if not isinstance(launched, dict):
        return False
    session = launched.get("session")
    if not isinstance(session, str) or not session:
        return False
    return caller_session[:12] == session[:12]


def caller_may_adopt_ownerless(
    row: dict[str, object], caller_session: str | None
) -> bool:
    """Launcher in any ownerless state, or anyone once the run is abandoned.

    A missing use_state is not abandoned: the caller may not adopt.
    """

    if caller_launched_row(row, caller_session):
        return True
    return row.get("use_state") == "abandoned"


def protection_retry_after_s(row: dict[str, object]) -> float | None:
    """Seconds until the 10-minute cut, for idle and human only."""

    use_state = row.get("use_state")
    if use_state == "idle":
        age = _finite_use_age(row.get("idle_s"))
    elif use_state == "human":
        age = _finite_use_age(row.get("human_input_age_s"))
    else:
        return None
    if age is None:
        return None
    remaining = RUN_IDLE_CUT_S - age
    if remaining <= 0:
        return None
    return round(remaining, 3)


ADOPTION_REVERT_PENDING = "adoption_revert_pending"


def adoption_revert_pending_fields() -> dict[str, object]:
    """Refusal while the stranger is still the durable owner.

    The revert write has not landed. The launcher is not refused for this
    reason; everyone else is, until that write succeeds or startup recovery
    reads the marker.
    """

    return {
        "use_state": "agent",
        "use_reason": ADOPTION_REVERT_PENDING,
        "hint": (
            "run_protected: adoption revert has not reached the manifest; "
            "this session does not have the launcher's right"
        ),
    }


def protection_fields(row: dict[str, object] | None) -> dict[str, object]:
    """Refusal body for a run that is not abandoned and not ours to adopt.

    Unclassifiable rows stay protected and say so. retry_after_s is present
    only for idle (from idle_s) and human (from human_input_age_s).
    """

    use_state = row.get("use_state") if isinstance(row, dict) else None
    use_reason = row.get("use_reason") if isinstance(row, dict) else None
    if not isinstance(use_state, str) or not use_state:
        use_state = None
    if not isinstance(use_reason, str) or not use_reason:
        use_reason = None if use_state is not None else "unclassified"
    state_text = use_state or "unclassified"
    hint = (
        f"run_protected: this ownerless run is in use ({state_text}); only the "
        "session that launched it may adopt it until it is abandoned"
    )
    if isinstance(use_reason, str) and use_reason:
        hint = f"{hint} ({use_reason})"
    fields: dict[str, object] = {
        "use_state": use_state,
        "use_reason": use_reason,
        "hint": hint,
    }
    if isinstance(row, dict):
        retry = protection_retry_after_s(row)
        if retry is not None:
            fields["retry_after_s"] = retry
            fields["hint"] = f"{hint}; retry in {retry}s"
    return fields


def _caller_owns_run(item: dict[str, object], caller_session: str | None) -> bool:
    owner = item.get("owner_session")
    if not isinstance(owner, str) or not owner:
        return False
    if not isinstance(caller_session, str) or not caller_session:
        return False
    return owner in {caller_session, caller_session[:12]}


def occupancy_error_fields(
    box: object, *, caller_session: str | None = None
) -> dict[str, object]:
    """Public active_run_exists extras derived from a box snapshot."""

    payload: dict[str, object] = {
        "occupied_by_run_id": None,
        "mod": "",
        "label": "",
        "age_s": 0.0,
        "foreign": False,
        "hint": _wait_hint(box),
    }
    if not isinstance(box, dict):
        payload["foreign"] = True
        return payload
    runs = box.get("runs")
    if isinstance(runs, list):
        for item in runs:
            if not isinstance(item, dict):
                continue
            run_id = item.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                continue
            payload["occupied_by_run_id"] = run_id
            payload["mod"] = item.get("mod") if isinstance(item.get("mod"), str) else ""
            payload["label"] = (
                item.get("label") if isinstance(item.get("label"), str) else ""
            )
            age = item.get("age_s")
            payload["age_s"] = (
                float(age)
                if isinstance(age, (int, float)) and not isinstance(age, bool)
                else 0.0
            )
            payload["activity_state"] = item.get("activity_state")
            payload["last_activity_age_s"] = item.get("last_activity_age_s")
            payload["foreign"] = False
            state = item.get("state")
            if state in {"STARTING", "STOPPING"}:
                payload["hint"] = _wait_hint(box)
            elif state == "UNRECONCILED":
                payload["hint"] = (
                    "this run is unreconciled; waiting will not free the box"
                )
            elif _caller_owns_run(item, caller_session) and state in {
                "RUNNING",
                "RUNNING_IDLE",
            }:
                payload["hint"] = _ACTIVE_RUN_STOP_HINT.format(run_id=run_id)
            elif state == "RUNNING_IDLE" and not _caller_owns_run(item, caller_session):
                if caller_may_adopt_ownerless(item, caller_session):
                    payload["hint"] = _ACTIVE_RUN_TAKEOVER_HINT.format(run_id=run_id)
                else:
                    notice = protection_fields(item)
                    payload["hint"] = notice["hint"]
                    payload["use_state"] = notice["use_state"]
                    payload["use_reason"] = notice["use_reason"]
                    if "retry_after_s" in notice:
                        payload["retry_after_s"] = notice["retry_after_s"]
            elif state == "RUNNING":
                payload["hint"] = _ACTIVE_RUN_TAKEOVER_HINT.format(run_id=run_id)
            else:
                payload["hint"] = _wait_hint(box)
            return payload
    foreign = box.get("foreign")
    if isinstance(foreign, list):
        for item in foreign:
            if not isinstance(item, dict):
                continue
            mods = item.get("mods")
            port = item.get("port")
            has_port = (
                isinstance(port, int)
                and not isinstance(port, bool)
                and 1 <= port <= 65535
            )
            has_mods = isinstance(mods, list) and any(
                isinstance(part, str) and part for part in mods
            )
            if not has_port and not has_mods:
                continue
            if has_mods:
                payload["mod"] = ";".join(
                    part for part in mods if isinstance(part, str) and part
                )
            payload["foreign"] = True
            payload["port"] = port if has_port else None
            payload["hint"] = _wait_hint(box)
            return payload
    if box.get("occupied") is True:
        payload["foreign"] = True
    return payload


def takeover_target_run_id(
    box: object, *, caller_session: str | None = None
) -> str | None:
    """Run id that dayz_test_run must not launch over without takeover=true."""

    if not isinstance(box, dict):
        return None
    runs = box.get("runs")
    if not isinstance(runs, list):
        return None
    for item in runs:
        if not isinstance(item, dict):
            continue
        run_id = item.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        state = item.get("state")
        if state not in {"RUNNING", "RUNNING_IDLE"}:
            continue
        if _caller_owns_run(item, caller_session):
            continue
        return run_id
    return None


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    creation_time_utc: str
    executable_sha256: str
    command_line_sha256: str
    role: str
    identity_scheme: str = "legacy-wmi-v1"

    @classmethod
    def from_payload(cls, value: object) -> "ProcessRecord":
        if not isinstance(value, dict):
            raise ValueError("invalid_process_record")
        record = cls(
            value.get("pid"),
            value.get("creation_time_utc"),
            value.get("executable_sha256"),
            value.get("command_line_sha256"),
            value.get("role"),
            value.get("identity_scheme", "legacy-wmi-v1"),
        )
        record.validate()
        return record

    def validate(self) -> None:
        if not isinstance(self.pid, int) or isinstance(self.pid, bool) or self.pid <= 0:
            raise ValueError("invalid_process_record")
        if not isinstance(self.creation_time_utc, str) or not self.creation_time_utc:
            raise ValueError("invalid_process_record")
        for value in (self.executable_sha256, self.command_line_sha256):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in _HEX for char in value.casefold())
            ):
                raise ValueError("invalid_process_record")
        if not isinstance(self.role, str) or not self.role:
            raise ValueError("invalid_process_record")
        if self.identity_scheme not in _IDENTITY_SCHEMES:
            raise ValueError("invalid_process_record")


@dataclass
class RunRecord:
    run_id: str
    owner_session_id: str | None
    owner_lease_id: str | None
    state: str
    label: str
    mod: str
    profiles: str
    mission: str
    processes: list[ProcessRecord]
    launch_operation_id: str | None = None
    launch_request_sha256: str | None = None
    launch_acknowledged: bool = True
    daemon_generation_at_launch: str | None = None
    # None: this record never observed a rotation (legacy row, or a launch
    # that does not create storage). False: this launch measured no rotation.
    # True: storage_1 was set aside. Not a claim that the economy rebuilt it.
    storage_rotated: bool | None = None
    storage_backup: str | None = None
    storage_reset_notice: str | None = None

    @classmethod
    def from_payload(cls, value: object) -> "RunRecord":
        if not isinstance(value, dict):
            raise ValueError("invalid_run_record")
        raw_processes = value.get("processes")
        if not isinstance(raw_processes, list):
            raise ValueError("invalid_run_record")
        rotated, backup, notice = _storage_rotation_from_payload(value)
        run = cls(
            value.get("run_id"),
            value.get("owner_session_id"),
            value.get("owner_lease_id"),
            value.get("state"),
            value.get("label"),
            value.get("mod"),
            value.get("profiles"),
            value.get("mission"),
            [ProcessRecord.from_payload(item) for item in raw_processes],
            value.get("launch_operation_id"),
            value.get("launch_request_sha256"),
            value.get("launch_acknowledged", True),
            value.get("daemon_generation_at_launch"),
            rotated,
            backup,
            notice,
        )
        run.validate()
        return run

    def validate(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ValueError("invalid_run_record")
        if self.state not in RUN_STATES:
            raise ValueError("invalid_run_record")
        if (self.owner_session_id is None) != (self.owner_lease_id is None):
            raise ValueError("invalid_run_record")
        if self.owner_session_id is not None and (
            not self.owner_session_id or not self.owner_lease_id
        ):
            raise ValueError("invalid_run_record")
        if self.state == "EXITED" and (
            self.owner_session_id is not None or self.processes
        ):
            raise ValueError("invalid_run_record")
        if self.state == "RUNNING" and (
            self.owner_session_id is None or not self.processes
        ):
            raise ValueError("invalid_run_record")
        if self.state == "RUNNING_IDLE" and (
            self.owner_session_id is not None or not self.processes
        ):
            raise ValueError("invalid_run_record")
        for value in (self.owner_session_id, self.owner_lease_id):
            if value is not None and not isinstance(value, str):
                raise ValueError("invalid_run_record")
        for value in (self.label, self.mod, self.profiles, self.mission):
            if not isinstance(value, str):
                raise ValueError("invalid_run_record")
        for record in self.processes:
            record.validate()
        if (self.launch_operation_id is None) != (
            self.launch_request_sha256 is None
        ):
            raise ValueError("invalid_run_record")
        if self.launch_operation_id is None:
            if self.launch_acknowledged is not True:
                raise ValueError("invalid_run_record")
        elif (
            not _valid_uuid4(self.launch_operation_id)
            or not _valid_sha256(self.launch_request_sha256)
            or not isinstance(self.launch_acknowledged, bool)
        ):
            raise ValueError("invalid_run_record")
        if self.daemon_generation_at_launch is not None and not isinstance(
            self.daemon_generation_at_launch, str
        ):
            raise ValueError("invalid_run_record")
        _validate_storage_rotation(
            self.storage_rotated, self.storage_backup, self.storage_reset_notice
        )


@dataclass(frozen=True)
class RetiredRunDiagnostic:
    run_id: str
    daemon_generation_at_launch: str | None
    daemon_generation_current: str
    generation_changed: bool | None
    event: str
    reason: str
    decision: str
    state: str
    launch_operation_id: str | None = None


class RunManifestStore:
    def __init__(
        self,
        paths: RuntimePaths,
        *,
        checkpoint: Callable[[bytes], object] | None = None,
        read_only: bool = False,
    ) -> None:
        self.paths = paths
        self._lock = threading.RLock()
        self._runs: dict[str, RunRecord] = {}
        self._storage_observations: list[dict[str, object]] = []
        self._legacy_gate_complete = False
        self._checkpoint = checkpoint
        self._read_only = bool(read_only)
        self._load()
        if self._read_only:
            return
        pruned = self._prune_exited_on_load()
        if self._checkpoint is not None and not pruned:
            raw = (
                self.paths.runs_path.read_bytes()
                if self.paths.runs_path.exists()
                else b'{"version":1,"runs":[]}\n'
            )
            self._checkpoint(raw)

    @classmethod
    def empty_for_recovery(
        cls,
        paths: RuntimePaths,
        *,
        checkpoint: Callable[[bytes], object] | None = None,
    ) -> "RunManifestStore":
        store = cls.__new__(cls)
        store.paths = paths
        store._lock = threading.RLock()
        store._runs = {}
        store._storage_observations = []
        store._legacy_gate_complete = True
        store._checkpoint = checkpoint
        store._read_only = False
        return store

    def _require_writable(self) -> None:
        if self._read_only:
            raise RuntimeError("run_manifest_store_read_only")

    def _create_preprune_backup(self, raw: bytes) -> bool:
        """Write a unique pre-prune backup; never overwrite an existing one.

        Slot names: runs.json.bak-preprune, then .bak-preprune.2 .. .10.
        Returns False (fail-closed, no prune) if every slot is occupied or I/O fails.
        """
        base_name = self.paths.runs_path.name + ".bak-preprune"
        slot_names = [base_name] + [f"{base_name}.{index}" for index in range(2, 11)]
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        for slot_name in slot_names:
            backup_path = self.paths.runs_path.with_name(slot_name)
            try:
                descriptor = os.open(backup_path, flags, 0o600)
            except FileExistsError:
                continue
            except OSError:
                return False
            try:
                with os.fdopen(descriptor, "wb") as backup:
                    if backup.write(raw) != len(raw):
                        raise OSError("preprune_backup_short_write")
                    backup.flush()
                    os.fsync(backup.fileno())
            except OSError:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                try:
                    backup_path.unlink()
                except OSError:
                    pass
                return False
            return True
        return False

    def _prune_exited_on_load(self) -> bool:
        retired = {
            run_id: run
            for run_id, run in self._runs.items()
            if run.state == "EXITED"
        }
        if not retired:
            return False
        try:
            original_raw = self.paths.runs_path.read_bytes()
        except OSError:
            return False
        if not self._create_preprune_backup(original_raw):
            return False
        previous = self._runs
        previous_observations = list(self._storage_observations)
        for run in retired.values():
            self._note_storage_observation_locked(run)
        self._runs = {
            run_id: run
            for run_id, run in self._runs.items()
            if run.state != "EXITED"
        }
        try:
            self._persist_locked()
        except Exception:
            self._runs = previous
            self._storage_observations = previous_observations
            raise
        return True

    def _load(self) -> None:
        if not self.paths.runs_path.exists():
            return
        try:
            payload = json.loads(self.paths.runs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_run_manifest") from exc
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("invalid_run_manifest")
        self._storage_observations = _storage_observations_from_payload(
            payload.get("storage_observations", [])
        )
        runs = payload.get("runs")
        if not isinstance(runs, list):
            raise ValueError("invalid_run_manifest")
        try:
            for item in runs:
                run = RunRecord.from_payload(item)
                if run.run_id in self._runs:
                    raise ValueError("invalid_run_manifest")
                self._runs[run.run_id] = run
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_run_manifest") from exc

    def _clone(self, run: RunRecord) -> RunRecord:
        return RunRecord.from_payload(dataclasses.asdict(run))

    def _persist_locked(self) -> None:
        self._require_writable()
        payload = {
            "version": 1,
            "runs": [dataclasses.asdict(self._runs[key]) for key in sorted(self._runs)],
        }
        if self._storage_observations:
            payload["storage_observations"] = list(self._storage_observations)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        ) + b"\n"
        previous = (
            self.paths.runs_path.read_bytes()
            if self.paths.runs_path.exists()
            else b'{"version":1,"runs":[]}\n'
        )
        atomic_write_bytes(self.paths.runs_path, raw)
        if self._checkpoint is not None:
            try:
                self._checkpoint(raw)
            except Exception:
                atomic_write_bytes(self.paths.runs_path, previous)
                try:
                    self._checkpoint(previous)
                except Exception:
                    pass
                raise

    def list_runs(self) -> list[RunRecord]:
        with self._lock:
            return [self._clone(self._runs[key]) for key in sorted(self._runs)]

    def _note_storage_observation_locked(self, run: RunRecord) -> None:
        """Keep a measured rotation after the EXITED row is pruned.

        Unknown stays off this list, and noting a run without a measurement
        removes any earlier entry for that run id. A stale or planted log
        must not survive the pruning of a row that measured nothing. The
        list is bounded and keyed by run id.
        """
        if type(run.storage_rotated) is not bool:
            self._storage_observations = [
                item
                for item in self._storage_observations
                if item.get("run_id") != run.run_id
            ]
            return
        entry = {
            "run_id": run.run_id,
            "storage_rotated": run.storage_rotated,
            "storage_backup": run.storage_backup,
            "storage_reset_notice": run.storage_reset_notice,
        }
        self._storage_observations = [
            item
            for item in self._storage_observations
            if item.get("run_id") != run.run_id
        ]
        self._storage_observations.append(entry)
        overflow = len(self._storage_observations) - _STORAGE_OBSERVATION_BOUND
        if overflow > 0:
            del self._storage_observations[:overflow]

    def storage_observations(self) -> list[dict[str, object]]:
        with self._lock:
            return [dict(item) for item in self._storage_observations]

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            run = self._runs.get(run_id)
            return self._clone(run) if run is not None else None

    def add(self, run: RunRecord) -> None:
        self._require_writable()
        run.validate()
        with self._lock:
            if run.run_id in self._runs:
                raise ValueError("run_exists")
            self._runs[run.run_id] = self._clone(run)
            previous_observations = list(self._storage_observations)
            self._note_storage_observation_locked(run)
            try:
                self._persist_locked()
            except Exception:
                self._runs.pop(run.run_id, None)
                self._storage_observations = previous_observations
                raise
            if self._active_legacy(run):
                self._legacy_gate_complete = False

    def replace(self, run: RunRecord) -> None:
        self._require_writable()
        run.validate()
        with self._lock:
            if run.run_id not in self._runs:
                raise ValueError("run_not_found")
            previous = self._runs[run.run_id]
            self._runs[run.run_id] = self._clone(run)
            previous_observations = list(self._storage_observations)
            self._note_storage_observation_locked(run)
            try:
                self._persist_locked()
            except Exception:
                self._runs[run.run_id] = previous
                self._storage_observations = previous_observations
                raise
            if self._active_legacy(run):
                self._legacy_gate_complete = False

    @staticmethod
    def _active_legacy(run: RunRecord) -> bool:
        return run.state in _ACTIVE_STATES and any(
            record.identity_scheme == "legacy-wmi-v1" for record in run.processes
        )

    def quarantine_legacy_active(
        self, audit: Callable[[dict[str, object]], object]
    ) -> list[str]:
        """Audit and durably quarantine active records that predate identity v2."""

        self._require_writable()
        if not callable(audit):
            raise TypeError("audit_callback_required")
        with self._lock:
            if self._legacy_gate_complete:
                return []
            targets = [
                self._runs[run_id]
                for run_id in sorted(self._runs)
                if self._active_legacy(self._runs[run_id])
            ]
            if not targets:
                self._legacy_gate_complete = True
                return []
            for run in targets:
                event = {
                    "event": "legacy_identity_quarantined",
                    "reason": "legacy_process_identity_scheme",
                    "duration_s": 0.0,
                    "decision": "manual_reconciliation_required",
                    "run_id": run.run_id,
                    "previous_state": run.state,
                    "process_count": len(run.processes),
                }
                try:
                    audited = audit(event)
                except Exception as error:
                    raise RuntimeError("legacy_identity_audit_failed") from error
                if audited is False:
                    raise RuntimeError("legacy_identity_audit_failed")

            previous = dict(self._runs)
            changed: list[str] = []
            for current in targets:
                if (
                    current.state == "UNRECONCILED"
                    and current.owner_session_id is None
                    and current.owner_lease_id is None
                ):
                    continue
                run = self._clone(current)
                run.state = "UNRECONCILED"
                run.owner_session_id = None
                run.owner_lease_id = None
                self._runs[run.run_id] = run
                changed.append(run.run_id)
            try:
                if changed:
                    self._persist_locked()
            except Exception:
                self._runs = previous
                raise
            self._legacy_gate_complete = True
            return changed

    def release_owner(self, session_id: str, lease_id: str) -> list[str]:
        self._require_writable()
        with self._lock:
            changed: list[str] = []
            previous = dict(self._runs)
            for run_id, current in list(self._runs.items()):
                if (
                    current.owner_session_id == session_id
                    and current.owner_lease_id == lease_id
                    and current.state == "RUNNING"
                ):
                    run = self._clone(current)
                    run.owner_session_id = None
                    run.owner_lease_id = None
                    run.state = "RUNNING_IDLE"
                    self._runs[run_id] = run
                    changed.append(run_id)
            if changed:
                try:
                    self._persist_locked()
                except Exception:
                    self._runs = previous
                    raise
            return changed

    def recover_after_restart(self) -> dict[str, list[str]]:
        """Atomically release stable owners and quarantine interrupted mutations."""

        self._require_writable()
        with self._lock:
            released: list[str] = []
            unreconciled: list[str] = []
            previous = dict(self._runs)
            for run_id, current in list(self._runs.items()):
                run = self._clone(current)
                if run.state == "RUNNING" and run.owner_session_id is not None:
                    run.owner_session_id = None
                    run.owner_lease_id = None
                    run.state = "RUNNING_IDLE"
                    released.append(run_id)
                elif run.state in {"STARTING", "STOPPING"}:
                    run.state = "UNRECONCILED"
                    run.owner_session_id = None
                    run.owner_lease_id = None
                    unreconciled.append(run_id)
                else:
                    continue
                self._runs[run_id] = run
            if released or unreconciled:
                try:
                    self._persist_locked()
                except Exception:
                    self._runs = previous
                    raise
            return {
                "released": sorted(released),
                "unreconciled": sorted(unreconciled),
            }


AuditCallback = Callable[[dict[str, object]], object]
RetailProbe = Callable[[], dict[str, object]]
Launcher = Callable[[list[str], str, str], object]
RecoveryFaultArm = Callable[[RunRecord, str], object]
_T = TypeVar("_T")


class ProcessLifecycle:
    """Every transition into RUNNING_IDLE goes through the fence; the poll re-validates after re-acquiring the lock."""

    def __init__(
        self,
        *,
        coordinator: SessionCoordinator,
        manifest: RunManifestStore,
        audit: AuditCallback,
        guard: object,
        retail_probe: RetailProbe | None,
        game_path: Path,
        diag_probe: RetailProbe | None = None,
        port_probe: RetailProbe | None = None,
        launcher: Launcher | None = None,
        id_fn: Callable[[], str] | None = None,
        recovery_fault_arm: RecoveryFaultArm | None = None,
        argv_of: Callable[[int], list[str] | None] | None = None,
        bindings: object | None = None,
        # 79e2: reads the bridge peer rows to revalidate a client replacement at
        # the instant of the kill. Read-only; None means "no answer", which is a
        # refusal, never a licence.
        bridge_probe: object | None = None,
        daemon_generation: str | None = None,
        steam_gate: object | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.manifest = manifest
        self.audit = audit
        self.guard = guard
        self.retail_probe = retail_probe
        self.diag_probe = diag_probe
        self.port_probe = port_probe
        self.game_path = Path(game_path).resolve()
        self.launcher = launcher or self._launch
        self.steam_gate = steam_gate if steam_gate is not None else SteamPreparationGate()
        self.id_fn = id_fn or (lambda: uuid.uuid4().hex)
        self.recovery_fault_arm = recovery_fault_arm
        self.argv_of = argv_of or _default_argv_of
        self.bindings = bindings
        self.bridge_probe = bridge_probe
        self.daemon_generation = (
            daemon_generation if isinstance(daemon_generation, str) else ""
        )
        self._audit_rows_dropped = 0
        self._box_cache: tuple[float, int, _BoxProbes] | None = None
        self._box_revision = 0
        self._operation_lock = threading.RLock()
        self._command_id = -1
        self._last_start_error: str | None = None
        # Wire-safe Steam preparation of the last successful client launch.
        # One row per session; never persisted and never includes identity or PIDs.
        self._steam_preparation_by_session: dict[str, dict[str, object]] = {}
        self._steam_preparation_lock = threading.Lock()
        self._activity_lock = threading.Lock()
        self._last_activity: dict[tuple[str, str], float] = {}
        self._activity_unknown: set[tuple[str, str]] = set()
        self._activity_tombstone: dict[tuple[str, str], float] = {}
        self._compensating_runs: set[tuple[str, str]] = set()
        # 250f: who is using each run. Daemon memory only, keyed like
        # _last_activity; runs.json is untouched, so a restart starts every
        # clock at _use_clock_origin and forgets every launcher.
        self._ownerless_since: dict[tuple[str, str], float] = {}
        self._human_input_at: dict[tuple[str, str], float] = {}
        self._uncertain_input_at: dict[tuple[str, str], float] = {}
        self._launched_by: dict[tuple[str, str], ClientIdentity] = {}
        # 250f PR 3. Launcher requests restart the idle clock (§3.2). Closing
        # is the warden's countdown. A failed warning stays idle_waiting until
        # its block lifts. Idle retirement labels the diagnostic when this
        # daemon, not a player, closes the run.
        self._launcher_request_at: dict[tuple[str, str], float] = {}
        self._closing_runs: set[tuple[str, str]] = set()
        self._warning_blocked_until: dict[tuple[str, str], float] = {}
        self._idle_retirement: dict[str, str] = {}
        self._input_attributor = InputAttributor(bridge_s=INPUT_SIGNAL_STALE_S)
        self._input_good_at: float | None = None
        self._signal_recovered_at: float | None = None
        # Runs already retired: a sample still in flight never writes to them
        # (review #125 F6). Bounded; retirement is rare.
        self._retired_use_keys: dict[tuple[str, str], None] = {}
        # Registered processes confirmed gone or reused, by the full record
        # (generation, run, pid, creation time): a later failed guard read can
        # never make them count again (review #125 R2-1). A new process that
        # reuses the pid is another record and is not excluded. Bounded by the
        # manifest: only exclusions of records no active run holds are pruned.
        self._excluded_use_records: dict[tuple[str, str, int, str], None] = {}
        self._use_clock_origin = time.time()
        # Open stranger adopt: token under _activity_lock, marker beside runs.json.
        # The marker is fsynced before the owner replace. A crash in between
        # reverts that owner on the next startup instead of keeping it.
        self._pending_adoption: dict[tuple[str, str], int] = {}
        self._invalidated_adoption: dict[tuple[str, str], tuple[int, str]] = {}
        self._adoption_serial = 0
        self._open_adoption: tuple[str, int] | None = None
        # Revert whose manifest write failed. The durable row still names the
        # stranger. Daemon memory only: startup recovery reads the marker.
        # The view is replaced wholesale so enqueue can read it without this
        # lock (the loopback lock is already held there).
        self._adoption_revert_pending: dict[tuple[str, str], tuple[int, str]] = {}
        self._revert_pending_view: tuple[frozenset[str], Mapping[str, str]] = (
            frozenset(),
            MappingProxyType({}),
        )
        # Retired-run diagnostics live in daemon memory and start empty after a
        # restart; the audit file is never read to reconstruct them.
        self._retired_diagnostics: deque[RetiredRunDiagnostic] = deque(maxlen=32)
        # fb-20260904-200821-dae1 part 1: the owner (session, lease) and the
        # records of each run the reaper retired while it had one, by activity
        # key. Daemon memory under _activity_lock, bounded, never persisted.
        self._reaped_owners: dict[
            tuple[str, str], tuple[str, str, tuple[ProcessRecord, ...]]
        ] = {}
        # Per client-process identity (generation, run, pid, creation time).
        # A reused pid is a different key. Status reads this; it never
        # terminates. Ordered so the bound drops the oldest identity.
        self._client_role_observations: dict[
            tuple[str, str, int, str], dict[str, object]
        ] = {}
        # Status readers merge, store and copy under this lock. Probes stay
        # outside it.
        self._client_observation_lock = threading.Lock()
        # fb-20260904-200821-dae1 part 2: the clock of not_before_utc in the
        # launch intent. Instance state so a test can drive it.
        self._launch_intent_clock: Callable[[], float] = time.time
        # Bound of the socket-release confirmation (P-L2.c). Instance state so a
        # test can shorten it without patching the module.
        self._role_release_tries = _ROLE_RELEASE_TRIES
        self._role_release_interval_s = _ROLE_RELEASE_INTERVAL_S
        # fb-20260818-232129-1233: the RPT watch of each server launch while
        # it starts, and the hung verdict of the last one that was stopped
        # hung. Daemon memory only; a new server launch clears the verdict,
        # so one that is present was reached after the latest server launch.
        # Leaf lock: nothing else is taken while it is held. The clock is
        # instance state so a test can drive it without patching the module.
        self._server_start_lock = threading.Lock()
        self._server_start_watches: dict[str, _ServerStartWatch] = {}
        self._server_start_verdict: dict[str, object] | None = None
        self._server_start_clock: Callable[[], float] = time.time
        # One diagnostic per lifecycle startup, before the first launch request.
        # This warning grants no authority; load_verified_bundle remains the gate.
        if os.name == "nt":
            from dayz_mcp.native_bundle import installed_source_pin_status

            seal = installed_source_pin_status()
            if seal["status"] != "fresh":
                logging.getLogger(__name__).warning("native_launcher_source_seal: %s", seal)
        self._recover_unconfirmed_adoption()

    def _prepare_instance(
        self,
        run_id: str,
        role: str,
        profiles_dir: str,
        replacing_role: bool,
    ) -> tuple[str | None, str | None]:
        bindings = self.bindings
        if bindings is None:
            return None, None
        try:
            if replacing_role:
                bindings.retire_role(run_id, role, "replace-role")
            minted = bindings.prepare(run_id, role, profiles_dir)
        except BindingPrepareError as exc:
            return None, exc.code
        except Exception:
            return None, "instance_config_missing"
        port = getattr(bindings, "config_port", None)
        key = getattr(bindings, "key", None)
        if isinstance(port, int) and not isinstance(port, bool) and isinstance(key, str) and key:
            try:
                written = json.loads(
                    (Path(profiles_dir) / "dayz_mcp.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                return None, "instance_endpoint_mismatch"
            expected_url = "http://127.0.0.1:" + str(port) + "/"
            if (
                not isinstance(written, dict)
                or written.get("url") != expected_url
                or written.get("key") != key
            ):
                return None, "instance_endpoint_mismatch"
        if not isinstance(minted, str) or not minted:
            return None, "instance_config_missing"
        return minted, None

    def _confirm_instance(self, minted: str | None, record: ProcessRecord) -> None:
        bindings = self.bindings
        if bindings is None or minted is None:
            return
        bindings.confirm(minted, record)

    def _retire_minted(
        self, run_id: str, role: str, minted: str | None, reason: str
    ) -> None:
        bindings = self.bindings
        if bindings is None or minted is None:
            return
        bindings.retire_role(run_id, role, reason)

    def _retire_run_bindings(self, run_id: str, reason: str) -> None:
        bindings = self.bindings
        if bindings is None:
            return
        bindings.retire_run(run_id, reason)

    def _adopt_dispatchable(self, run_id: str) -> bool:
        # Caller holds _operation_lock. ServerState.run_has_bound_binding
        # takes ServerState._lock; never the inverse.
        bindings = self.bindings
        if bindings is None:
            return False
        checker = getattr(bindings, "run_has_bound_binding", None)
        if not callable(checker):
            return False
        try:
            return bool(checker(run_id))
        except Exception:
            return False

    def _current_generation(self) -> str:
        return self.daemon_generation if isinstance(self.daemon_generation, str) else ""

    def _retire_run_diagnostic(
        self,
        run: RunRecord,
        event: str,
        reason: str,
        decision: str,
        state: str = "EXITED",
    ) -> None:
        key = (run.run_id, run.launch_operation_id)
        fields = _generation_projection(run, self._current_generation())
        entry = RetiredRunDiagnostic(
            run_id=run.run_id,
            daemon_generation_at_launch=fields["daemon_generation_at_launch"],
            daemon_generation_current=fields["daemon_generation_current"],
            generation_changed=fields["generation_changed"],
            event=event,
            reason=reason,
            decision=decision,
            state=state,
            launch_operation_id=run.launch_operation_id,
        )
        with self._activity_lock:
            for existing in self._retired_diagnostics:
                if (existing.run_id, existing.launch_operation_id) == key:
                    return
            self._retired_diagnostics.appendleft(entry)

    def _commit_retirement(
        self, run: RunRecord, event: str, reason: str, decision: str
    ) -> bool:
        # Binding retirement precedes the durable transition; the diagnostic
        # follows a successful persist. stop_run may already have retired.
        binding_reason = _BINDING_REASON_BY_EVENT.get(event, reason)
        self._retire_run_bindings(run.run_id, binding_reason)
        try:
            self.manifest.replace(run)
        except Exception:
            return False
        self._best_effort_post_persist_retirement(run, event, reason, decision)
        return True

    def _best_effort_post_persist_retirement(
        self, run: RunRecord, event: str, reason: str, decision: str
    ) -> None:
        try:
            self._invalidate_box_cache()
        except Exception:
            self._note_post_persist_fault(run.run_id, "invalidate_box_cache")
        try:
            self._retire_run_diagnostic(run, event, reason, decision, "EXITED")
        except Exception:
            self._note_post_persist_fault(run.run_id, "diagnostic")
        try:
            self._seal_terminal(run.run_id, time.time())
        except Exception:
            self._note_post_persist_fault(run.run_id, "seal_terminal")
            try:
                self._unfence_runs([run.run_id])
            except Exception:
                pass

    def _note_post_persist_fault(self, run_id: str, reason: str) -> None:
        try:
            if not self._audit(
                "lifecycle_terminal_post_persist",
                None,
                reason,
                "degraded",
                run_id=run_id,
            ):
                self._note_audit_row_dropped()
        except Exception:
            self._note_audit_row_dropped()

    def _projected_run(self, run: RunRecord) -> dict[str, object]:
        row = dataclasses.asdict(run)
        row.update(_generation_projection(run, self._current_generation()))
        row["client_diagnostics"] = self._client_role_diagnostics(run)
        return row

    @staticmethod
    def _observation_now() -> str:
        stamp = time.time()
        whole = time.gmtime(stamp)
        millis = int((stamp - math.floor(stamp)) * 1000)
        return time.strftime("%Y-%m-%dT%H:%M:%S", whole) + f".{millis:03d}Z"

    def _active_client_observation_keys(self) -> set[tuple[str, str, int, str]]:
        active: set[tuple[str, str, int, str]] = set()
        try:
            runs = self.manifest.list_runs()
        except Exception:
            return active
        generation = self._current_generation()
        for run in runs:
            if run.state not in _ACTIVE_STATES:
                continue
            for record in run.processes:
                if record.role != "client":
                    continue
                active.add(
                    (generation, run.run_id, record.pid, record.creation_time_utc)
                )
        return active

    def _remember_client_observation(
        self,
        key: tuple[str, str, int, str],
        observation: dict[str, object],
    ) -> dict[str, object]:
        """Merge one probe result into the table and return the merged row.

        The probe ran outside this lock. The latest stored row is re-read
        under it, so a reader that probed before a sibling published a
        stronger observation cannot erase that evidence with a weaker one.
        """

        active = self._active_client_observation_keys()
        with self._client_observation_lock:
            stored = self._client_role_observations.get(key)
            row = _merge_client_observations(
                stored if isinstance(stored, dict) else None,
                observation,
            )
            table = self._client_role_observations
            table[key] = row
            if len(table) <= _CLIENT_ROLE_OBSERVATION_BOUND:
                return dict(row)
            for old in list(table):
                if len(table) <= _CLIENT_ROLE_OBSERVATION_BOUND:
                    break
                if old in active:
                    continue
                table.pop(old, None)
            while len(table) > _CLIENT_ROLE_OBSERVATION_BOUND:
                table.pop(next(iter(table)))
            return dict(row)

    def _retained_client_observations(
        self,
        generation: str,
        run_id: str,
        current_keys: set[tuple[str, str, int, str]],
    ) -> list[dict[str, object]]:
        with self._client_observation_lock:
            stored = [
                (key, dict(row))
                for key, row in self._client_role_observations.items()
                if isinstance(row, dict)
            ]
        retained: list[dict[str, object]] = []
        for key, row in stored:
            if key in current_keys:
                continue
            if key[0] != generation or key[1] != run_id:
                continue
            row["current"] = False
            retained.append(row)
        return retained

    def _observe_client_record(self, run: RunRecord, record: ProcessRecord) -> dict[str, object]:
        """Identity-aware liveness of one registered client. Never terminates."""

        generation = self._current_generation()
        key = (generation, run.run_id, record.pid, record.creation_time_utc)
        state = "unknown"
        exit_code: int | None = None
        exit_time_utc: str | None = None
        if self._quarantined():
            # Quarantine blocks the guard. Status stays a read.
            state = "unknown"
            now = self._observation_now()
        else:
            try:
                actual = self.guard.snapshot(record.pid)
            except Exception:
                actual = None
            # After the probe returns, so a death stamp cannot precede the
            # moment this probe saw the process gone.
            now = self._observation_now()
            if not isinstance(actual, dict):
                state = "unknown"
            elif (
                actual.get("error") == "process_not_found"
                and actual.get("exit_code") == 4
            ):
                state = "dead"
            elif actual.get("exit_code") == 3 or actual.get("error"):
                state = "unknown"
            elif self._identity_matches(record, actual):
                state = "alive"
            elif actual.get("identity_complete") is True:
                # The pid belongs to someone else. The old client is gone.
                state = "dead"
            else:
                state = "unknown"
            if (
                state == "dead"
                and isinstance(actual, dict)
                and actual.get("error") != "process_identity_mismatch"
                and not (
                    actual.get("identity_complete") is True
                    and not self._identity_matches(record, actual)
                )
            ):
                # The guard's own exit_code (3 or 4) is not the process exit.
                # A reused pid's snapshot is a different process: do not copy it.
                observed = actual.get("process_exit_code")
                observed_at = actual.get("exit_time_utc")
                if (
                    isinstance(observed, int)
                    and not isinstance(observed, bool)
                ):
                    exit_code = observed
                if isinstance(observed_at, str) and observed_at:
                    exit_time_utc = observed_at
        first_dead = now if state == "dead" else None
        row: dict[str, object] = {
            "role": "client",
            "state": state,
            "pid": record.pid,
            "creation_time_utc": record.creation_time_utc,
            "executable_sha256": record.executable_sha256,
            "command_line_sha256": record.command_line_sha256,
            "identity_scheme": record.identity_scheme,
            "observed_at_utc": now,
            "first_observed_dead_at_utc": first_dead,
            "exit_code": exit_code,
            "exit_time_utc": exit_time_utc,
            "current": True,
        }
        # The probe ran unlocked. Merge, store and the returned copy share
        # one critical section, so a late weak probe cannot erase a death
        # another reader published while it was in flight.
        return self._remember_client_observation(key, row)

    def _client_role_diagnostics(self, run: RunRecord) -> list[dict[str, object]]:
        generation = self._current_generation()
        current: list[dict[str, object]] = []
        current_keys: set[tuple[str, str, int, str]] = set()
        for record in run.processes:
            if record.role != "client":
                continue
            current_keys.add(
                (generation, run.run_id, record.pid, record.creation_time_utc)
            )
            current.append(self._observe_client_record(run, record))
        retained = self._retained_client_observations(
            generation, run.run_id, current_keys
        )
        if not current and not retained:
            return [
                {
                    "role": "client",
                    "state": "not_started",
                    "pid": None,
                    "creation_time_utc": None,
                    "executable_sha256": None,
                    "command_line_sha256": None,
                    "identity_scheme": None,
                    "observed_at_utc": self._observation_now(),
                    "first_observed_dead_at_utc": None,
                    "exit_code": None,
                    "exit_time_utc": None,
                    "current": False,
                }
            ]
        return retained + current

    def _publish_retired_diagnostics(self) -> list[dict[str, object]]:
        with self._activity_lock:
            entries = list(self._retired_diagnostics)
        published: list[dict[str, object]] = []
        for item in entries:
            raw = dataclasses.asdict(item)
            published.append({key: raw[key] for key in _DIAGNOSTIC_PUBLIC_KEYS})
        return published

    def _status_snapshot(
        self,
    ) -> tuple[list[RunRecord], list[dict[str, object]]]:
        # Never takes _operation_lock: /status is the health discriminator and
        # must not wait out a stop. Revision stamp + bounded retry, then drop
        # a diagnostic whose run still has a non-terminal row in this payload.
        runs: list[RunRecord] = []
        diagnostics: list[dict[str, object]] = []
        for _ in range(_STATUS_SNAPSHOT_TRIES):
            with self._activity_lock:
                before = self._box_revision
            runs = list(self.manifest.list_runs())
            diagnostics = self._publish_retired_diagnostics()
            with self._activity_lock:
                after = self._box_revision
            if before == after:
                break
        live = {run.run_id for run in runs if run.state != "EXITED"}
        diagnostics = [
            item
            for item in diagnostics
            if item.get("run_id") not in live
        ]
        return runs, diagnostics

    def _launch(self, argv: list[str], cwd: str, window_style: str) -> object:
        kwargs: dict[str, object] = {
            "cwd": cwd,
            "close_fds": True,
            "env": whitelisted_child_environment(),
        }
        if os.name == "nt" and window_style == "hidden":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        elif os.name == "nt" and window_style == "normal":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = SW_SHOWNOACTIVATE
            kwargs["startupinfo"] = startupinfo
        # fb-20260822-025926-bad7: the console streams used to be dropped.
        # They go to pipes the daemon drains into files beside the RPT; the
        # child inherits only those pipe ends and NUL (close_fds limits the
        # inherited set to the three std handles), and the files are opened
        # by the daemon, so nothing holds them once the process is gone. An
        # argv without an absolute -profiles folder launches as before.
        capture_dir = _launch_output_dir(argv)
        if capture_dir is not None:
            kwargs["stdin"] = subprocess.DEVNULL
            kwargs["stdout"] = subprocess.PIPE
            kwargs["stderr"] = subprocess.PIPE
        process = subprocess.Popen(argv, **kwargs)
        if capture_dir is not None:
            _start_launch_output_capture(process, capture_dir, argv)
        return process

    @staticmethod
    def _error(error: str, status: int) -> dict[str, object]:
        return {"error": error, "_http_status": status}

    def _audit(self, event: str, client: ClientIdentity | None, reason: str, decision: str, **extra: object) -> bool:
        payload: dict[str, object] = {
            "event": event,
            "reason": reason,
            "duration_s": 0.0,
            "decision": decision,
        }
        if client is not None:
            payload["client"] = client.public_payload()
        payload.update(extra)
        try:
            return self.audit(payload) is not False
        except Exception:
            return False

    def _activity_key(self, run_id: str) -> tuple[str, str]:
        generation = self.daemon_generation if isinstance(self.daemon_generation, str) else ""
        return (generation, run_id)

    def _bump_box_revision_locked(self) -> None:
        # Caller holds _activity_lock. start/stop/reap take _operation_lock
        # then _activity_lock; reverse order would deadlock.
        self._box_revision += 1
        self._box_cache = None

    def _invalidate_box_cache(self) -> None:
        with self._activity_lock:
            self._bump_box_revision_locked()

    def _capture_start_activity(
        self, run: RunRecord, *, launched: ProcessRecord | None = None
    ) -> None:
        records = [launched] if launched is not None else list(run.processes)
        stamps = [
            stamp
            for stamp in (
                _utc_epoch(record.creation_time_utc) for record in records
            )
            if stamp is not None
        ]
        key = self._activity_key(run.run_id)
        with self._activity_lock:
            if stamps and key not in self._activity_unknown:
                if launched is not None or key not in self._last_activity:
                    # A process launched by this daemon is a stamp of its own:
                    # max with any earlier seal (extension), never import a
                    # pre-daemon basal when this daemon has not sealed yet.
                    self._seal_activity_locked(key, max(stamps))
            self._bump_box_revision_locked()

    def _clear_server_start_verdict(self) -> None:
        with self._server_start_lock:
            self._server_start_verdict = None

    def _begin_server_start_watch(
        self, run_id: str, argv: object, launched_at: float
    ) -> None:
        """Watch the RPT of a server launch that just reached RUNNING."""
        profiles = _launch_output_dir(argv)
        if profiles is None:
            return
        watch = _ServerStartWatch(run_id, profiles, launched_at)
        try:
            watch.sample(self._server_start_clock())
        except Exception:
            pass
        with self._server_start_lock:
            self._server_start_watches[run_id] = watch

    def _end_server_start_watch(self, run_id: str) -> None:
        with self._server_start_lock:
            self._server_start_watches.pop(run_id, None)

    def _sample_server_start_watches(self) -> None:
        """One look at each watched RPT, at most once per interval.

        Called from status(), which the readiness probe of the worker polls
        about every 2 s while it waits. The directory is read outside the
        lock and folded in only if the watch is still the same object.
        """
        now = self._server_start_clock()
        with self._server_start_lock:
            for run_id in [
                run_id
                for run_id, watch in self._server_start_watches.items()
                if now - watch.launched_at > _SERVER_START_WATCH_MAX_S
            ]:
                self._server_start_watches.pop(run_id, None)
            due = [
                watch
                for watch in self._server_start_watches.values()
                if watch.sampled_at is None
                or now - watch.sampled_at >= _SERVER_START_SAMPLE_INTERVAL_S
            ]
        for watch in due:
            try:
                found = _launch_rpt(watch.profiles, watch.launched_at)
            except Exception:
                continue
            with self._server_start_lock:
                if self._server_start_watches.get(watch.run_id) is watch:
                    watch.observe(found, now)

    def _settle_server_start_watch(self, run_id: str) -> None:
        """A watched start is being stopped: one last look, then the verdict.

        No verdict unless that last look still finds the RPT: a silence is
        only claimed up to a moment at which the file was seen unchanged.
        """
        with self._server_start_lock:
            watch = self._server_start_watches.pop(run_id, None)
        if watch is None:
            return
        now = self._server_start_clock()
        found = _launch_rpt(watch.profiles, watch.launched_at)
        if found is None:
            return
        watch.observe(found, now)
        verdict = watch.verdict(now)
        if verdict is not None:
            with self._server_start_lock:
                self._server_start_verdict = verdict

    def _server_start_verdict_payload(self) -> dict[str, object] | None:
        with self._server_start_lock:
            verdict = self._server_start_verdict
            return dict(verdict) if verdict is not None else None

    def record_input_sample(self, sample: InputSample) -> list[str]:
        """250f: attribute one input sample to the runs whose windows had the focus.

        A foreground pid counts as verified use only while the full registered
        identity of that process still matches. A complete identity that does
        not match, or a process that is gone, is not that run any more, so a
        reused pid never attributes input. An identity the guard cannot read
        is a doubt, kept apart as uncertain input (the run reads unknown).
        A sample that is not ok never refreshes the signal; the first good one
        after a stale stretch restarts the clocks from that instant. Returns
        the run ids the input went to.
        """

        runs = [
            run for run in self.manifest.list_runs() if run.state in _ACTIVE_STATES
        ]

        def owner_of(pid: int) -> tuple[str, bool] | None:
            for run in runs:
                for record in run.processes:
                    if record.pid != pid:
                        continue
                    record_key = (
                        *self._activity_key(run.run_id),
                        record.pid,
                        record.creation_time_utc,
                    )
                    with self._activity_lock:
                        if record_key in self._excluded_use_records:
                            return None
                    try:
                        actual = self.guard.snapshot(pid)
                    except Exception:
                        return run.run_id, False
                    if self._identity_matches(record, actual):
                        return run.run_id, True
                    if isinstance(actual, dict) and (
                        actual.get("identity_complete") is True
                        or (
                            actual.get("error") == "process_not_found"
                            and actual.get("exit_code") == 4
                        )
                    ):
                        with self._activity_lock:
                            # A read still in flight when the run was retired
                            # leaves nothing behind (review #125 R3-1).
                            if self._activity_key(run.run_id) not in self._retired_use_keys:
                                self._excluded_use_records[record_key] = None
                            if len(self._excluded_use_records) > _EXCLUSION_PRUNE_AT:
                                # Never forget a live exclusion: only records no
                                # active run holds any more are dropped, so the
                                # table stays bounded by the manifest itself
                                # (review #125 R3, R2-1 at the cap).
                                live = {
                                    (
                                        *self._activity_key(item.run_id),
                                        held.pid,
                                        held.creation_time_utc,
                                    )
                                    for item in runs
                                    for held in item.processes
                                }
                                for stale in [
                                    key
                                    for key in self._excluded_use_records
                                    if key not in live
                                ]:
                                    self._excluded_use_records.pop(stale, None)
                        return None
                    return run.run_id, False
            return None

        attributed = self._input_attributor.observe(sample, owner_of)
        credited: list[str] = []
        with self._activity_lock:
            if sample.ok:
                previous = self._input_good_at
                if previous is None or sample.at - previous > INPUT_SIGNAL_STALE_S:
                    self._signal_recovered_at = sample.at
                self._input_good_at = sample.at
            for run_id, epoch, verified in attributed:
                key = self._activity_key(run_id)
                if key in self._retired_use_keys:
                    continue
                table = self._human_input_at if verified else self._uncertain_input_at
                current = table.get(key)
                if current is None or epoch > current:
                    table[key] = epoch
                credited.append(run_id)
                self._invalidate_pending_adoption_locked(
                    run_id, "human" if verified else "uncertain"
                )
        return credited

    def record_command_activity(self, run_id: str, *, now: float | None = None) -> bool:
        if not isinstance(run_id, str) or not run_id:
            return False
        epoch = time.time() if now is None else float(now)
        return self._write_command_activity(run_id, epoch, reason="enqueue_accepted")

    def record_box_command_activity(
        self,
        *,
        now: float | None = None,
        owner_session: str | None = None,
        run_id: str | None = None,
    ) -> None:
        epoch = time.time() if now is None else float(now)
        if isinstance(run_id, str) and run_id:
            try:
                self.record_command_activity(run_id, now=epoch)
            except Exception:
                pass
            return
        # No accredited binding. Do not credit by owner or by cardinality:
        # either false-attributes freshness or leaves a false stale on a live
        # session. Forget stamps so occupancy publishes unknown.
        _ = owner_session
        try:
            runs = list(self.manifest.list_runs())
        except Exception:
            return
        active = [run for run in runs if run.state in _ACTIVE_STATES]
        with self._activity_lock:
            for run in active:
                key = self._activity_key(run.run_id)
                current = self._last_activity.get(key)
                if current is not None and current <= epoch:
                    self._last_activity.pop(key, None)
                self._raise_activity_tombstone_locked(key, epoch)
            self._bump_box_revision_locked()

    def tombstone_run_activity(self, run_id: str, *, now: float | None = None) -> None:
        if not isinstance(run_id, str) or not run_id:
            return
        epoch = time.time() if now is None else float(now)
        key = self._activity_key(run_id)
        with self._activity_lock:
            self._raise_activity_tombstone_locked(key, epoch)
            current = self._last_activity.get(key)
            if current is not None and current <= epoch:
                self._last_activity.pop(key, None)
            self._bump_box_revision_locked()

    def _forget_run_residues(self, run_id: str) -> None:
        self._seal_terminal(run_id, time.time())

    def _seal_terminal(self, run_id: str, at: float) -> None:
        # Drop data, unknown, and compensation; keep (or recreate) a frontier
        # later than any stamp that was acceptable at retirement so a writer
        # that accepted before this seal cannot reappear after it.
        epoch = float(at)
        with self._activity_lock:
            keys = [
                key
                for key in (
                    set(self._last_activity)
                    | set(self._activity_tombstone)
                    | set(self._activity_unknown)
                    | set(self._compensating_runs)
                )
                if key[1] == run_id
            ]
            current = self._activity_key(run_id)
            if current not in keys:
                keys.append(current)
            frontier = epoch
            for key in keys:
                stamp = self._last_activity.get(key)
                if stamp is not None and stamp > frontier:
                    frontier = stamp
                tomb = self._activity_tombstone.get(key)
                if tomb is not None and tomb > frontier:
                    frontier = tomb
            for key in keys:
                self._last_activity.pop(key, None)
                self._activity_unknown.discard(key)
                self._compensating_runs.discard(key)
                self._raise_activity_tombstone_locked(key, frontier)
            # 250f: a retired run keeps no clock, input or launcher, and a
            # sample still in flight cannot write to it afterwards.
            for table in (
                self._ownerless_since,
                self._human_input_at,
                self._uncertain_input_at,
                self._launched_by,
            ):
                for key in [key for key in table if key[1] == run_id]:
                    table.pop(key, None)
            for key in [key for key in self._excluded_use_records if key[1] == run_id]:
                self._excluded_use_records.pop(key, None)
            self._retired_use_keys[current] = None
            while len(self._retired_use_keys) > 256:
                self._retired_use_keys.pop(next(iter(self._retired_use_keys)))
            self._bump_box_revision_locked()
        # fb-20260818-232129-1233: a retired run has no start left to watch.
        self._end_server_start_watch(run_id)
        bindings = self.bindings
        unfence = getattr(bindings, "unfence_runs", None)
        if callable(unfence):
            try:
                unfence([run_id])
            except Exception:
                pass

    def _raise_activity_tombstone_locked(
        self, key: tuple[str, str], epoch: float
    ) -> None:
        current = self._activity_tombstone.get(key)
        if current is None or epoch > current:
            self._activity_tombstone[key] = epoch

    def _seal_activity_locked(self, key: tuple[str, str], epoch: float) -> None:
        # Unique stamp writer. Sticky unknown still wins over everything.
        # A compensating run discards credit and basal until the rollback
        # tombstone is the frontier.
        if key in self._compensating_runs:
            return
        tombstone = self._activity_tombstone.get(key)
        if tombstone is not None and epoch <= tombstone:
            return
        current = self._last_activity.get(key)
        if current is None or epoch >= current:
            self._last_activity[key] = epoch

    def _write_command_activity(self, run_id: str, epoch: float, *, reason: str) -> bool:
        key = self._activity_key(run_id)
        ok = self._audit(
            "run_command_activity",
            None,
            reason,
            "recorded",
            run_id=run_id,
            activity_epoch=epoch,
        )
        with self._activity_lock:
            if not ok:
                tombstone = self._activity_tombstone.get(key)
                if tombstone is None or epoch > tombstone:
                    self._activity_unknown.add(key)
            elif key not in self._activity_unknown:
                self._seal_activity_locked(key, epoch)
            self._bump_box_revision_locked()
        return ok

    def _activity_for_run_id(
        self, run_id: str, clock: float
    ) -> tuple[str, float | None]:
        key = self._activity_key(run_id)
        with self._activity_lock:
            if key in self._activity_unknown or key in self._compensating_runs:
                return "unknown", None
            stamp = self._last_activity.get(key)
            tombstone = self._activity_tombstone.get(key)
        if stamp is None:
            return "unknown", None
        if tombstone is not None and stamp <= tombstone:
            return "unknown", None
        if stamp > clock:
            return "unknown", None
        age = clock - stamp
        if age <= _ACTIVITY_STALE_S:
            return "recent", round(age, 3)
        return "stale", round(age, 3)

    def _run_activity(self, run: RunRecord, clock: float) -> tuple[str, float | None]:
        return self._activity_for_run_id(run.run_id, clock)

    def _forget_attempt_activity(
        self,
        run_id: str,
        attempt_started_at: float,
        *,
        rolled_back_at: float,
    ) -> None:
        key = self._activity_key(run_id)
        with self._activity_lock:
            current = self._last_activity.get(key)
            if (
                current is not None
                and attempt_started_at <= current <= rolled_back_at
            ):
                self._last_activity.pop(key, None)
            self._raise_activity_tombstone_locked(key, rolled_back_at)
            self._bump_box_revision_locked()

    @contextmanager
    def _compensating(self, run_id: str):
        key = self._activity_key(run_id)
        with self._activity_lock:
            self._compensating_runs.add(key)
        try:
            yield
        finally:
            with self._activity_lock:
                self._compensating_runs.discard(key)

    def _legacy_identity_error(self) -> dict[str, object] | None:
        try:
            self.manifest.quarantine_legacy_active(self.audit)
        except Exception:
            return self._error("legacy_identity_transition_failed", 503)
        return None

    def _require_legacy_identity_safe(self) -> None:
        if self._legacy_identity_error() is not None:
            raise RuntimeError("legacy_identity_transition_failed")

    def _quarantined(self) -> bool:
        if self.retail_probe is None:
            return True
        try:
            result = self.retail_probe()
        except Exception:
            return True
        return (
            not isinstance(result, dict)
            or result.get("known") is not True
            or not isinstance(result.get("processes"), list)
            or bool(result.get("processes"))
        )

    def _authorize(self, client: ClientIdentity, token: str | None, command: str):
        decision = self.coordinator.authorize(client, token, command, 15.0)
        if not decision.allowed:
            return decision, self._decision_error(decision)
        return decision, None

    @classmethod
    def _decision_error(cls, decision: AuthorizationDecision) -> dict[str, object]:
        """The refusal as a result; a coordinator audit_failed keeps its audit_stage."""
        result = cls._error(decision.error, decision.http_status)
        audit_stage = public_audit_stage(decision.audit_stage)
        if audit_stage is not None:
            result["audit_stage"] = audit_stage
        return result

    @staticmethod
    def _authority(
        decision: AuthorizationDecision,
    ) -> tuple[str, str, str] | None:
        if (
            decision.owner_session_id is None
            or decision.lease_id is None
            or decision.reservation_id is None
        ):
            return None
        return (
            decision.owner_session_id,
            decision.lease_id,
            decision.reservation_id,
        )

    def _reject_reserved(
        self,
        authority: tuple[str, str, str],
        command: str,
        reason: str,
        status: int = 409,
    ) -> dict[str, object]:
        decision = self.coordinator.reject_reservation(
            authority[0], authority[1], authority[2], reason, status
        )
        result = self._decision_error(decision)
        if decision.cleanup_degraded:
            result["cleanup_degraded"] = list(decision.cleanup_degraded)
        return result

    def _commit_reserved(
        self, authority: tuple[str, str, str], command: str
    ) -> int | None:
        command_id = self._command_id
        self._command_id -= 1
        if not self.coordinator.commit_authorization(
            authority[0],
            authority[1],
            authority[2],
            command_id,
            command,
            {},
        ):
            return None
        return command_id

    def _finish_committed(
        self, authority: tuple[str, str, str], command_id: int
    ) -> None:
        self.coordinator.finish_operation_exact(
            authority[0], authority[1], command_id
        )

    def _reservation_active(
        self, authority: tuple[str, str, str], command: str
    ) -> bool:
        return self.coordinator.reservation_active(
            authority[0], authority[1], authority[2], command
        )

    def _canonical_error(self, executable: Path) -> str | None:
        canonical = executable.resolve()
        diag = (self.game_path / "DayZDiag_x64.exe").resolve()
        retail = {
            os.path.normcase(str((self.game_path / "DayZ_BE.exe").resolve())),
            os.path.normcase(str((self.game_path / "DayZ_x64.exe").resolve())),
        }
        normalized = os.path.normcase(str(canonical))
        if normalized == os.path.normcase(str(diag)):
            return None
        if normalized in retail:
            return "retail_manual_lifecycle_required"
        return "executable_not_allowed"

    @staticmethod
    def _usable_peer_age(value: object) -> bool:
        """None, or a finite non-negative number. Nothing else is an age."""
        if value is None:
            return True
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return math.isfinite(value) and value >= 0.0

    def _client_peer_row(self) -> dict[str, object] | None:
        """The bridge row for the client peer, or None when there is no answer.

        The probe is the loopback ServerState's own status_snapshot: the daemon
        already owns that object (daemon.py passes it as ``bindings``), so the
        state the MCP server reads over HTTP as /status is readable here with no
        network hop. It travels as its OWN parameter and not through
        ``bindings`` because this is a read of the bridge, not a binding
        mutation, and an object that answers one must not have to answer both.

        The lock order is the established one: _prepare_instance already calls
        ``bindings.prepare`` under _operation_lock, and ServerState never takes
        _operation_lock while holding its own lock (loopback.py:1250).

        No probe, an unreadable one, or a snapshot without a client row are all
        the same answer: None. The caller turns that into a refusal, never into
        a licence to kill.
        """
        probe = self.bridge_probe
        if not callable(probe):
            return None
        try:
            snapshot = probe()
        except Exception:
            return None
        if not isinstance(snapshot, dict):
            return None
        peers = snapshot.get("peers")
        if not isinstance(peers, dict):
            return None
        row = peers.get("client")
        return row if isinstance(row, dict) else None

    @staticmethod
    def _rotation_applies(existing: object, launch_role: str) -> bool:
        """Only a launch that CREATES its run hands the engine a fresh storage.

        A client, or an offline that extends an existing run, attaches to a tree
        the engine already chose; rotating under it would pull the world from a
        live session. Mirrors dayz_test_worker._start_core, which puts the seal
        in the request for exactly these two roles and only when it creates the
        run -- so launch_request_sha256 covers it.
        """
        return existing is None and launch_role in {"server", "offline"}

    def _rotate_storage_for_launch(
        self,
        parsed: dict[str, object],
        run_id: str,
        provisional: RunRecord | None = None,
    ) -> str | None:
        """Seal the mod set and set aside an incompatible storage. Pre-spawn.

        Codex F-01 put this here. It used to run inside the worker, before the
        first lifecycle call, so a launch this method was going to refuse with
        active_run_exists had ALREADY renamed the storage of the run that was
        alive: the bytes survived (v1 renames, never deletes) but they left the
        canonical path under a live owner. Now every admission -- active run,
        quarantine, lease, audit, fence, and the last look at the socket table
        -- has already passed, the operation lock is held, and no process has
        been created yet. A refusal here has rotated nothing.

        Returns a declared error code, or None when the launch may proceed.
        """
        seal = parsed.get("storage_seal")
        mission = parsed.get("mission")
        project = str(parsed.get("mod") or "").lstrip("@")
        if (
            not isinstance(seal, str)
            or not isinstance(mission, str)
            or not mission
            or not project
        ):
            # Fail-closed: a launch that must rotate and does not carry its seal
            # comes from a launcher bundle older than this daemon. Refusing
            # costs a rebuild; guessing costs the mission.
            return "storage_rotate_failed"
        try:
            result = dayz_test_storage.prepare_storage(
                mission,
                seal=seal,
                project=project[:64],
                now=time.time(),
                # Derived, never minted: a retry of the same launch names the
                # same journal, and no clock or randomness enters the id.
                txid=hashlib.sha256(
                    (run_id + ":" + seal).encode("utf-8")
                ).hexdigest()[:32],
            )
        except (dayz_test_storage.StorageError, OSError):
            return "storage_rotate_failed"
        # prepare_storage skips completed journals. A retry before the engine
        # has created storage_1 would otherwise look like a launch that never
        # reset the mission. The marker match is not evidence the world was
        # rebuilt, and this does not define that accreditation.
        if (
            result.launch_allowed
            and not result.storage_rotated
            and isinstance(mission, str)
            and not os.path.isdir(
                os.path.join(mission, dayz_test_storage.STORAGE_NAME)
            )
        ):
            pending = _pending_completed_rotation(mission, seal)
            if pending is _AMBIGUOUS_PENDING_ROTATION:
                # A reset is pending but its saved world is ambiguous: leave the
                # run's storage observation unknown, never a measured false.
                return None
            if pending is not None:
                result = pending
        if not result.launch_allowed:
            # A refused classification measured nothing: the provisional keeps
            # null storage fields, exactly like a launch that raised before
            # prepare_storage returned. Writing false here would report a
            # measured reuse for a state the subsystem could not classify.
            return "storage_recovery_required"
        self._record_storage_rotation(provisional, result)
        # A replay of a completed journal is the same reset, not a second one.
        # The run record carries it; another audit row would claim a new move.
        if result.storage_rotated and result.reason != "pending_completed_rotation":
            self._audit_storage_rotation(run_id, result)
        return None

    @staticmethod
    def _record_storage_rotation(
        provisional: RunRecord | None, result: dayz_test_storage.RotationResult
    ) -> None:
        """Copy a measured rotation onto the run that is about to spawn.

        Unknown stays unknown: a launch that raised before prepare_storage
        returned has no measurement. False is only the measured non-rotation.
        """
        if provisional is None:
            return
        if result.storage_rotated:
            backup = result.storage_backup
            notice = result.storage_reset_notice
            if (
                not _plain_storage_backup_name(backup)
                or notice != dayz_test_storage.RESET_NOTICE
            ):
                return
            provisional.storage_rotated = True
            provisional.storage_backup = backup
            provisional.storage_reset_notice = notice
            return
        provisional.storage_rotated = False
        provisional.storage_backup = None
        provisional.storage_reset_notice = None

    def _audit_storage_rotation(self, run_id: str, result: object) -> None:
        """A rotation resets the world and the characters: it leaves a row.

        Routed through `_audit` so this event shares the payload path of the
        other lifecycle rows, and so a missing writer is counted instead of
        returning silently. That is a form improvement, not an incident
        explanation: the previous emitter already sent a non-empty reason
        and duration_s 0.0, and already counted writer exceptions, so this
        helper does not repair a validation that used to reject the row.
        Observability still cannot block a launch the admissions already
        allowed.
        """
        reason = getattr(result, "reason", None)
        if not isinstance(reason, str) or not reason.strip():
            reason = "storage_rotated"
        decision = getattr(result, "decision", None)
        if not isinstance(decision, str):
            decision = ""
        try:
            written = self._audit(
                "lifecycle_storage_rotated",
                None,
                reason,
                decision,
                run_id=run_id,
                storage_backup=getattr(result, "storage_backup", None),
                storage_marker_backup=getattr(
                    result, "storage_marker_backup", None
                ),
                storage_seal=getattr(result, "storage_seal", None),
                notice=getattr(result, "storage_reset_notice", None),
            )
        except Exception:
            self._note_audit_row_dropped()
            return
        if not written:
            self._note_audit_row_dropped()

    def _note_audit_row_dropped(self) -> None:
        self._audit_rows_dropped += 1

    def _replacement_witness_error(
        self, parsed: dict[str, object], *, now: float
    ) -> str | None:
        """Revalidate here the verdict the extension gate reached back there.

        H-A2-2 / ficha 79e2: the gate reads the bridge once, in another process,
        and this is where a live DayZ dies. Nothing revalidated in between, so a
        client that resumed polling inside the window was killed and the answer
        said client_not_polling -- a fact already false at the moment of the kill.

        The witness is the instant the gate READ the bridge, and it is compared
        by ELAPSED TIME, never by absolute clocks: the bridge stamps its polls
        with time.monotonic (loopback.py:939), so an epoch read here and a
        monotonic reading there do not live on the same axis. "The client polled
        after the decision" is therefore "its most recent poll is younger than
        the age of the decision".

        Deliberately stricter than server._peer_is_live: it takes the MOST
        RECENT of the two ages the row carries instead of choosing one by
        binding state. Any evidence of a poll after the decision blocks the
        kill, and refusing costs one repeated call while being wrong the other
        way costs a live session.
        """
        witness = parsed.get("replace_if_not_polling_since")
        if type(witness) is not int or witness <= 0:
            return "replace_witness_missing"
        decision_age_s = now - witness / 1000.0
        # Codex F-05. A witness in the future used to be tolerated up to a
        # couple of seconds and then clamped to zero, which turned a poll made
        # AFTER the real decision into one made before it and authorised the
        # kill. There is no benign reason for a decision stamped in the future
        # by a process on this host, so any negative age is a refusal and the
        # comparison below no longer needs a clamp.
        if decision_age_s < 0.0 or decision_age_s > _REPLACE_WITNESS_MAX_AGE_S:
            return "replace_witness_stale"
        peer = self._client_peer_row()
        if peer is None:
            return "bridge_state_unreadable"
        keys = ("last_poll_age_s", "bound_last_poll_age_s")
        if any(key not in peer for key in keys):
            # Not the shape status_snapshot publishes, so not an answer about
            # this client, and no answer must not authorise a kill.
            return "bridge_state_unreadable"
        values = [peer[key] for key in keys]
        if any(not self._usable_peer_age(value) for value in values):
            # Codex F-04: a field that is a string, a bool, NaN, an infinity or
            # a negative duration is an unknown dimension, and the kill may not
            # be decided on the subset that happened to parse. NaN matters on
            # its own: every comparison against it is False, so a filtered NaN
            # would have read as "did not poll".
            return "bridge_state_unreadable"
        ages = [value for value in values if value is not None]
        if not ages:
            # Both ages null is the row of a peer that has NEVER polled in this
            # generation (loopback.py: last_poll_at stays None until the first
            # poll). That is positive evidence that it did not poll after the
            # decision either -- and it is the hung client of ficha 8f76c, the
            # very case the replacement exists for. Reading it as silence would
            # make the feature refuse exactly the case it was built to fix.
            return None
        if min(ages) < decision_age_s:
            return "client_polling_since_decision"
        return None

    def _parse_start_request(self, request: object) -> tuple[dict[str, object] | None, str | None]:
        if not isinstance(request, dict):
            return None, "invalid_start_request"
        argv = request.get("argv")
        cwd = request.get("cwd")
        role = request.get("role")
        style = request.get("window_style")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) or not item for item in argv)
            or not isinstance(cwd, str)
            or not Path(cwd).is_absolute()
            or not isinstance(role, str)
            or not role
            or style not in {"normal", "hidden"}
            or type(request.get("auto_remediate_steam", False)) is not bool
        ):
            return None, "invalid_start_request"
        result = dict(request)
        for field in ("label", "mod", "profiles", "mission"):
            value = result.get(field, "")
            if not isinstance(value, str):
                return None, "invalid_start_request"
            result[field] = value
        launch_fields = (
            result.get("new_run_id"),
            result.get("launch_operation_id"),
            result.get("launch_request_sha256"),
        )
        if any(value is not None for value in launch_fields):
            new_run_id, operation_id, request_sha256 = launch_fields
            if (
                result.get("run_id") is not None
                or not _valid_uuid4(new_run_id)
                or not _valid_uuid4(operation_id)
                or new_run_id == operation_id
                or not _valid_sha256(request_sha256)
            ):
                return None, "invalid_launch_identity"
            canonical_request = {
                key: value
                for key, value in request.items()
                if key != "launch_request_sha256"
            }
            canonical = json.dumps(
                canonical_request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if hashlib.sha256(canonical).hexdigest() != request_sha256:
                return None, "launch_request_hash_mismatch"
        return result, None

    def _start_rejection(
        self,
        client: ClientIdentity,
        authority: tuple[str, str, str],
        reason: str,
        *,
        audit_reason: str | None = None,
    ) -> dict[str, object]:
        if not self._audit(
            "lifecycle_start_rejected",
            client,
            audit_reason or reason,
            "rejected",
        ):
            self.coordinator.reject_reservation(
                authority[0],
                authority[1],
                authority[2],
                reason,
            )
            return self._error("audit_failed", 503)
        return self._reject_reserved(authority, "lifecycle_start", reason)

    @staticmethod
    def _add_degradation(result: dict[str, object], value: str) -> None:
        current = result.get("cleanup_degraded")
        degraded = list(current) if isinstance(current, list) else []
        if value not in degraded:
            degraded.append(value)
        result["cleanup_degraded"] = degraded

    def _terminal_outcome(
        self,
        result: dict[str, object],
        event: str,
        client: ClientIdentity,
        *,
        reason: str,
        decision: str,
        run_id: str,
        state: str,
        terminated: int | None = None,
    ) -> dict[str, object]:
        fields: dict[str, object] = {"run_id": run_id, "state": state}
        if terminated is not None:
            fields["terminated"] = terminated
        if not self._audit(event, client, reason, decision, **fields):
            self._add_degradation(result, "audit_failed")
        return result

    def _persist_failed_launch_target(
        self,
        target: RunRecord,
        provisional: RunRecord,
        attempt_started_at: float | None,
    ) -> bool:
        persistence_failed = False
        try:
            self.manifest.replace(target)
        except Exception:
            persistence_failed = True
        else:
            self._invalidate_box_cache()
            if target.state == "EXITED":
                self._seal_terminal(provisional.run_id, time.time())
        if attempt_started_at is not None:
            self._forget_attempt_activity(
                provisional.run_id,
                attempt_started_at,
                rolled_back_at=time.time(),
            )
        return persistence_failed

    def _drain_pending_for_runs(self, run_ids: list[str]) -> None:
        bindings = self.bindings
        drain = getattr(bindings, "drain_pending_for_run", None)
        if not callable(drain):
            return
        for run_id in run_ids:
            try:
                drain(run_id)
            except Exception:
                pass

    def _fence_runs(self, run_ids: list[str]) -> None:
        if not run_ids:
            return
        bindings = self.bindings
        fence = getattr(bindings, "fence_runs", None)
        if callable(fence):
            fence(run_ids)
            return
        self._drain_pending_for_runs(run_ids)

    def _unfence_runs(self, run_ids: list[str]) -> None:
        bindings = self.bindings
        unfence = getattr(bindings, "unfence_runs", None)
        if not callable(unfence):
            return
        try:
            unfence(run_ids)
        except Exception:
            pass

    def _transition_to_idle(self, runs: list[str], persist: Callable[[], _T]) -> _T:
        """Fence + drain, persist, then confirm; unfence if persist fails.

        Every transition into RUNNING_IDLE goes through this method.
        """

        if runs:
            try:
                self._fence_runs(runs)
            except Exception:
                self._unfence_runs(runs)
                raise
        try:
            result = persist()
        except Exception:
            if runs:
                self._unfence_runs(runs)
            raise
        leftover: list[str] = []
        for run_id in runs:
            current = self.manifest.get(run_id)
            if current is None or current.state != "RUNNING_IDLE":
                leftover.append(run_id)
        if leftover:
            self._unfence_runs(leftover)
        confirmed = [run_id for run_id in runs if run_id not in leftover]
        if confirmed:
            # 250f: the ownerless clock of each run starts at its transition.
            stamp = time.time()
            with self._activity_lock:
                for run_id in confirmed:
                    self._ownerless_since[self._activity_key(run_id)] = stamp
        return result

    def _quiesce_then_release_owner(self, session_id: str, lease_id: str) -> list[str]:
        """Quiesce runs â†’ persist â†’ confirm or revert.

        Every transition into RUNNING_IDLE goes through the fence.
        """

        candidates = [
            run.run_id
            for run in self.manifest.list_runs()
            if run.owner_session_id == session_id
            and run.owner_lease_id == lease_id
            and run.state == "RUNNING"
        ]
        return self._transition_to_idle(
            candidates,
            lambda: self.manifest.release_owner(session_id, lease_id),
        )

    def _settle_failed_launch(
        self,
        *,
        client: ClientIdentity,
        previous: RunRecord | None,
        provisional: RunRecord,
        launched: object | None,
        record: ProcessRecord | None,
        confirmed_error: str,
        manifest_failure: bool = False,
        attempt_started_at: float | None = None,
    ) -> dict[str, object]:
        """Leave every failed Popen attempt in a durable, reconcilable state."""

        self._last_start_error = confirmed_error
        confirmed_closed = launched is None or self._terminate_open_handle(launched)
        persistence_failed = False
        with self._compensating(provisional.run_id):
            if confirmed_closed:
                if previous is not None:
                    target = RunRecord.from_payload(dataclasses.asdict(previous))
                else:
                    target = RunRecord.from_payload(dataclasses.asdict(provisional))
                    target.state = "EXITED"
                    target.owner_session_id = None
                    target.owner_lease_id = None
                    target.processes = []
                persistence_failed = self._persist_failed_launch_target(
                    target, provisional, attempt_started_at
                )
                durable = self.manifest.get(provisional.run_id)
                state = durable.state if durable is not None else "STARTING"
                if persistence_failed:
                    result = self._error("manual_cleanup_required", 409)
                else:
                    result = self._error(
                        confirmed_error,
                        503 if confirmed_error == "lifecycle_start_failed" else 409,
                    )
            else:
                target = RunRecord.from_payload(dataclasses.asdict(provisional))
                target.state = "UNRECONCILED"
                if record is not None and record not in target.processes:
                    target.processes.append(record)
                persistence_failed = self._persist_failed_launch_target(
                    target, provisional, attempt_started_at
                )
                durable = self.manifest.get(provisional.run_id)
                state = durable.state if durable is not None else "STARTING"
                result = self._error("manual_cleanup_required", 409)

        result["run_id"] = provisional.run_id
        result["state"] = state
        if manifest_failure or persistence_failed:
            self._add_degradation(result, "manifest_failed")
        return self._terminal_outcome(
            result,
            "lifecycle_start_outcome",
            client,
            reason=str(result["error"]),
            decision=str(result["error"]),
            run_id=provisional.run_id,
            state=state,
        )

    def start_run(self, client: ClientIdentity, token: str | None, request: object) -> dict[str, object]:
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        command = "lifecycle_start"
        decision, error = self._authorize(client, token, command)
        if error is not None:
            return error
        authority = self._authority(decision)
        if authority is None:
            return self._error("lease_required", 403)
        return self._start_run_reserved(client, authority, request)

    def _is_idempotent_launch_retry(
        self,
        client: ClientIdentity,
        authority: tuple[str, str, str],
        request: object,
    ) -> bool:
        parsed, request_error = self._parse_start_request(request)
        if request_error is not None or parsed is None:
            return False
        if self._canonical_error(Path(parsed["argv"][0])) is not None:
            return False
        if parsed.get("run_id") is not None:
            return False
        new_run_id = parsed.get("new_run_id")
        if not isinstance(new_run_id, str):
            return False
        existing = self.manifest.get(new_run_id)
        if existing is None:
            return False
        return (
            existing.owner_session_id == client.session_id
            and existing.owner_lease_id == authority[1]
            and existing.launch_operation_id == parsed.get("launch_operation_id")
            and existing.launch_request_sha256 == parsed.get("launch_request_sha256")
            and existing.state == "RUNNING"
        )

    def _store_steam_preparation(
        self, session_id: str, run_id: str, steam: Preparation
    ) -> None:
        key = _steam_preparation_key(session_id)
        with self._steam_preparation_lock:
            self._steam_preparation_by_session.pop(key, None)
            self._steam_preparation_by_session[key] = {
                "run_id": run_id,
                "startup": _steam_prep_token(steam.startup),
                "pid_repair": _steam_prep_token(steam.pid_repair),
                "restarted": steam.restarted if type(steam.restarted) is bool else None,
            }
            while len(self._steam_preparation_by_session) > _STEAM_PREPARATION_BOUND:
                oldest = next(iter(self._steam_preparation_by_session))
                del self._steam_preparation_by_session[oldest]

    def _steam_mutation_allowed(self) -> bool | str:
        """Probe outside the lifecycle lock, then compare the captured run state."""
        if not self._operation_lock.acquire(timeout=0.1):
            return "steam_prepare_busy"
        try:
            before = [dataclasses.asdict(run) for run in self.manifest.list_runs()]
            servers = {
                record.pid: record
                for run in self.manifest.list_runs() if run.state in _ACTIVE_STATES
                for record in run.processes if record.role == "server"
            }
        finally:
            self._operation_lock.release()
        # A hung OS identity/argv/retail probe must never retain the lifecycle
        # lock after the supervisor cancels and drains its helper.
        if self._quarantined():
            return False
        reason, processes = self._diag_snapshot()
        if reason is not None or processes is None:
            return False
        for row in processes:
            record = servers.get(row["pid"])
            if record is None:
                return False
            try:
                if not self._identity_matches(record, self.guard.snapshot(record.pid)):
                    return False
                argv = self.argv_of(record.pid)
                if not isinstance(argv, list) or "-server" not in [str(arg).casefold() for arg in argv[1:]]:
                    return False
            except Exception:
                return False
        if not self._operation_lock.acquire(timeout=0.1):
            return "steam_prepare_busy"
        try:
            return before == [dataclasses.asdict(run) for run in self.manifest.list_runs()]
        finally:
            self._operation_lock.release()

    def _start_run_reserved(
        self, client: ClientIdentity, authority: tuple[str, str, str], request: object,
        steam: Preparation | None = None,
    ) -> dict[str, object]:
        with box_admission() as admitted:
            if not admitted:
                return {
                    "ok": False,
                    "error": "box_admission_busy",
                    "status": 409,
                }
            return self._start_run_reserved_holding(
                client, authority, request, steam
            )

    def _start_run_reserved_holding(
        self, client: ClientIdentity, authority: tuple[str, str, str], request: object,
        steam: Preparation | None = None,
    ) -> dict[str, object]:
        command = "lifecycle_start"
        with self._operation_lock:
            # Drop this session's Steam projection before any rejection or
            # spawn failure can return. The idempotent retry of a launch
            # already recorded for the same operation keeps the row.
            keep_projection = False
            try:
                keep_projection = (
                    self._reservation_active(authority, command)
                    and not self._quarantined()
                    and self._is_idempotent_launch_retry(client, authority, request)
                )
            except Exception:
                keep_projection = False
            if not keep_projection:
                with self._steam_preparation_lock:
                    self._steam_preparation_by_session.pop(
                        _steam_preparation_key(client.session_id), None
                    )
            if not self._reservation_active(authority, command):
                return self._error("lease_invalid", 409)
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            parsed, request_error = self._parse_start_request(request)
            if request_error is not None or parsed is None:
                return self._start_rejection(
                    client, authority, request_error or "invalid_start_request"
                )
            executable = Path(parsed["argv"][0])
            canonical_error = self._canonical_error(executable)
            if canonical_error is not None:
                return self._start_rejection(client, authority, canonical_error)
            canonical_argv = list(parsed["argv"])
            canonical_argv[0] = str(executable.resolve())
            parsed["argv"] = canonical_argv

            supplied_run_id = parsed.get("run_id")
            new_run_id = parsed.get("new_run_id")
            launch_operation_id = parsed.get("launch_operation_id")
            launch_request_sha256 = parsed.get("launch_request_sha256")
            active = [run for run in self.manifest.list_runs() if run.state in _ACTIVE_STATES]
            existing: RunRecord | None = None
            if supplied_run_id is not None:
                if not isinstance(supplied_run_id, str) or not supplied_run_id:
                    return self._start_rejection(client, authority, "invalid_run_id")
                existing = self.manifest.get(supplied_run_id)
                if existing is None:
                    return self._start_rejection(client, authority, "run_not_found")
                if existing.state != "RUNNING":
                    return self._start_rejection(
                        client, authority, "run_not_extensible"
                    )
                if (
                    existing.owner_session_id != client.session_id
                    or existing.owner_lease_id != authority[1]
                ):
                    return self._start_rejection(client, authority, "run_not_adopted")
                if any(run.run_id != existing.run_id for run in active):
                    return self._start_rejection(
                        client, authority, "active_run_exists"
                    )
            elif isinstance(new_run_id, str):
                existing = self.manifest.get(new_run_id)
                if existing is not None:
                    if (
                        existing.owner_session_id != client.session_id
                        or existing.owner_lease_id != authority[1]
                        or existing.launch_operation_id != launch_operation_id
                        or existing.launch_request_sha256 != launch_request_sha256
                        or existing.state != "RUNNING"
                    ):
                        return self._start_rejection(
                            client, authority, "launch_identity_conflict"
                        )
                    if not self._audit(
                        "lifecycle_start",
                        client,
                        "idempotent_retry",
                        "reused",
                        run_id=existing.run_id,
                    ):
                        self.coordinator.reject_reservation(
                            authority[0], authority[1], authority[2], "audit_failed"
                        )
                        return self._error("audit_failed", 503)
                    command_id = self._commit_reserved(authority, command)
                    if command_id is None:
                        return self._error("lease_invalid", 409)
                    try:
                        return {
                            "ok": True,
                            "run_id": existing.run_id,
                            "state": existing.state,
                        }
                    finally:
                        self._finish_committed(authority, command_id)
                if active:
                    return self._start_rejection(
                        client, authority, "active_run_exists"
                    )
            elif active:
                return self._start_rejection(client, authority, "active_run_exists")
            blocker = getattr(self.coordinator, "box_blocks_start", None)
            if callable(blocker) and blocker(client):
                return self._start_rejection(
                    client,
                    authority,
                    "active_run_exists",
                    audit_reason="box_claimed",
                )
            registered_pids = {
                record.pid for run in active for record in run.processes
            }
            foreign_diag_reason = self._foreign_diag_reason(registered_pids)
            if foreign_diag_reason is not None:
                return self._start_rejection(
                    client,
                    authority,
                    "active_run_exists",
                    audit_reason=foreign_diag_reason,
                )
            requested_port = _requested_port(parsed["argv"])
            foreign_port_reason = self._foreign_port_reason(
                registered_pids, requested_port
            )
            if foreign_port_reason is not None:
                return self._start_rejection(
                    client,
                    authority,
                    "active_run_exists",
                    audit_reason=foreign_port_reason,
                )
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            # The process arguments decide whether Steam is needed. A forged
            # role label must not bypass the client gate on the direct route.
            if "-server" not in [arg.casefold() for arg in parsed["argv"][1:]]:
                if steam is None:
                    if not self.steam_gate.claim():
                        code = "steam_cleanup_degraded" if self.steam_gate.degraded else "steam_prepare_busy"
                        return self._start_rejection(client, authority, code)
                    try:
                        # This frame owns exactly one RLock acquisition. No run
                        # state or instance has changed. Retain the Steam claim
                        # until the recursive admission AND spawn have finished.
                        self._operation_lock.release()
                        try:
                            prepared = self.steam_gate.prepare(
                                consent=parsed.get("auto_remediate_steam", False),
                                authority_active=lambda: self.coordinator.reservation_active(
                                    *authority, command, expire=False
                                ),
                                mutation_allowed=self._steam_mutation_allowed,
                            )
                        finally:
                            self._operation_lock.acquire()
                        if prepared.error_code is not None:
                            rejected = self._start_rejection(client, authority, prepared.error_code)
                            rejected["steam_preparation"] = prepared.payload()
                            if prepared.cleanup_degraded:
                                self._add_degradation(rejected, "steam_helper_exit_unverified")
                            return rejected
                        # Reuse ALL admission checks with the SAME reservation:
                        # ownership, box, runs, ports, quarantine and identities.
                        return self._start_run_reserved(client, authority, request, prepared)
                    finally:
                        self.steam_gate.release()
                if not self.steam_gate.final_check(steam):
                    return self._start_rejection(client, authority, "steam_identity_changed")
            steam_fields = {"steam_preparation": steam.payload()} if steam is not None else {}
            if not self._audit("lifecycle_start", client, "lease_valid", "allowed", **steam_fields):
                self.coordinator.reject_reservation(
                    authority[0], authority[1], authority[2], "audit_failed"
                )
                return self._error("audit_failed", 503)
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")

            command_id = self._commit_reserved(authority, command)
            if command_id is None:
                return self._error("lease_invalid", 409)

            run_id = (
                existing.run_id
                if existing is not None
                else str(new_run_id) if isinstance(new_run_id, str) else self.id_fn()
            )
            previous = (
                RunRecord.from_payload(dataclasses.asdict(existing))
                if existing is not None
                else None
            )
            provisional = (
                RunRecord.from_payload(dataclasses.asdict(existing))
                if existing is not None
                else RunRecord(
                    run_id,
                    client.session_id,
                    authority[1],
                    "STARTING",
                    str(parsed["label"]),
                    str(parsed["mod"]),
                    str(parsed["profiles"]),
                    str(parsed["mission"]),
                    [],
                    str(launch_operation_id)
                    if isinstance(launch_operation_id, str)
                    else None,
                    str(launch_request_sha256)
                    if isinstance(launch_request_sha256, str)
                    else None,
                    False if isinstance(launch_operation_id, str) else True,
                    self._current_generation() or None,
                )
            )
            provisional.state = "STARTING"
            try:
                if existing is None:
                    self.manifest.add(provisional)
                else:
                    self.manifest.replace(provisional)
            except Exception:
                self._finish_committed(authority, command_id)
                result = self._error("manifest_failed", 503)
                return self._terminal_outcome(
                    result,
                    "lifecycle_start_outcome",
                    client,
                    reason="manifest_failed",
                    decision="manifest_failed",
                    run_id=run_id,
                    state=existing.state if existing is not None else "ABSENT",
                )
            self._invalidate_box_cache()

            launched: object | None = None
            record: ProcessRecord | None = None
            minted: str | None = None
            intent: dict[str, object] | None = None
            launch_role = str(parsed["role"])
            attempt_started_at = time.time()
            try:
                if self._quarantined():
                    return self._settle_failed_launch(
                        client=client,
                        previous=previous,
                        provisional=provisional,
                        launched=None,
                        record=None,
                        confirmed_error="retail_quarantine",
                        attempt_started_at=attempt_started_at,
                    )
                if (
                    existing is not None
                    and previous is not None
                    # A1-F1/A1-F2: only the role the extension gate of
                    # dayz_test_tool covers. That gate reads the client record
                    # and nothing else, so running the replacement for another
                    # role would end a live process no gate ever looked at, and
                    # publish no_client_to_replace while doing it. Measured on
                    # role=offline; restores the HEAD behaviour for it.
                    and launch_role == "client"
                    # fb-20260906-185909-df53: and only when there IS something
                    # to supersede. _replace_role_processes returns ([], None)
                    # for a run that carries no process of this role (:2718),
                    # so demanding the witness in front of that no-op refused a
                    # launch that would never have terminated anything -- which
                    # is what mode=all does: its client stage extends the run
                    # its own server stage created, and the worker stamps no
                    # witness because no gate decided to supersede a client
                    # (dayz_test_worker.py:317-323). Same set the callee
                    # filters, read from the same object it is handed.
                    and any(
                        record.role == launch_role
                        for record in provisional.processes
                    )
                ):
                    # P-L2 / P-L2.c: the role this launch claims is freed
                    # before the instance is prepared, so a refusal here never
                    # leaves the superseded process alive with its binding
                    # already retired.
                    # 79e2: revalidated HERE, against the bridge state this
                    # process owns, before a single process is touched.
                    replaced_pids: tuple[int, ...] = ()
                    replace_error = self._replacement_witness_error(
                        parsed, now=time.time()
                    )
                    if replace_error is None:
                        replaced_pids, replace_error = self._replace_role_processes(
                            provisional, launch_role, client=client
                        )
                    if replace_error is None and replaced_pids:
                        # Durable before the launch: a crash between here and
                        # the launcher leaves STARTING without the superseded
                        # record, which recover_after_restart turns into
                        # UNRECONCILED and the adopt + stop pair closes.
                        try:
                            self.manifest.replace(provisional)
                        except Exception:
                            replace_error = "manifest_failed"
                        else:
                            self._invalidate_box_cache()
                            previous.processes = list(provisional.processes)
                    if replace_error is not None:
                        settled = self._settle_failed_launch(
                            client=client,
                            previous=previous,
                            provisional=provisional,
                            launched=None,
                            record=None,
                            confirmed_error=replace_error,
                            manifest_failure=replace_error == "manifest_failed",
                            attempt_started_at=attempt_started_at,
                        )
                        if replace_error == "port_still_held":
                            settled["hint"] = _PORT_STILL_HELD_HINT
                        elif replace_error in _REPLACE_WITNESS_HINTS:
                            hint = _REPLACE_WITNESS_HINTS[replace_error]
                            settled["hint"] = hint() if callable(hint) else hint
                        return settled
                minted, prepare_error = self._prepare_instance(
                    run_id, launch_role, str(parsed["profiles"]), existing is not None
                )
                if prepare_error is not None:
                    return self._settle_failed_launch(
                        client=client,
                        previous=previous,
                        provisional=provisional,
                        launched=None,
                        record=None,
                        confirmed_error=prepare_error,
                        attempt_started_at=attempt_started_at,
                    )
                # Last look at the socket table before the bind. Audit, reserve
                # and persistence sat between the admission probe and here; a
                # server that appeared meanwhile must not be launched over. The
                # window that remains is between this read and DayZ's own bind.
                pre_launch_reason = self._foreign_port_reason(
                    registered_pids, requested_port
                )
                if pre_launch_reason is not None:
                    audited = self._audit(
                        "lifecycle_start_rejected",
                        client,
                        pre_launch_reason,
                        "rejected",
                        run_id=run_id,
                        stage="pre_launch",
                    )
                    self._retire_minted(run_id, launch_role, minted, "launch_failed")
                    settled = self._settle_failed_launch(
                        client=client,
                        previous=previous,
                        provisional=provisional,
                        launched=None,
                        record=None,
                        confirmed_error="active_run_exists",
                        attempt_started_at=attempt_started_at,
                    )
                    if not audited:
                        # The refusal stands; the missing audit row is published
                        # the way _start_rejection publishes it, not swallowed.
                        self._add_degradation(settled, "audit_failed")
                    return settled
                # M15 (fichas 4407 + 01ae) / Codex F-01. Last step before the
                # spawn and after EVERY admission: a refusal above this line has
                # rotated nothing, and this is the only point at which the run
                # is certain to be created.
                if self._rotation_applies(existing, launch_role):
                    storage_error = self._rotate_storage_for_launch(
                        parsed, run_id, provisional
                    )
                    if storage_error is None:
                        # Durable before the spawn, so a crash or a later
                        # failed settlement still has the reset on this run.
                        try:
                            self.manifest.replace(provisional)
                        except Exception:
                            storage_error = "manifest_failed"
                    if storage_error is not None:
                        self._retire_minted(
                            run_id, launch_role, minted, "launch_failed"
                        )
                        settled = self._settle_failed_launch(
                            client=client,
                            previous=previous,
                            provisional=provisional,
                            launched=None,
                            record=None,
                            confirmed_error=storage_error,
                            attempt_started_at=attempt_started_at,
                        )
                        hint = _STORAGE_ROTATE_HINTS.get(storage_error)
                        if hint is not None:
                            settled["hint"] = hint
                        return settled
                if steam is not None and not self.steam_gate.final_check(steam):
                    self._retire_minted(run_id, launch_role, minted, "launch_failed")
                    return self._settle_failed_launch(
                        client=client, previous=previous, provisional=provisional,
                        launched=None, record=None, confirmed_error="steam_identity_changed",
                        attempt_started_at=attempt_started_at,
                    )
                # fb-20260818-232129-1233: "-server" in the argv decides, as
                # for the Steam gate above. A hung verdict older than this
                # launch attempt can never be read as this launch's.
                server_launch = "-server" in [
                    arg.casefold() for arg in parsed["argv"][1:]
                ]
                if server_launch:
                    self._clear_server_start_verdict()
                # fb-20260904-200821-dae1 part 2 (A1-F7): the trace of this
                # launch is durable before its process exists. A daemon that
                # dies between the Popen and the manifest write below leaves
                # it for recover_launch_intent; the finally drops it once the
                # row is no longer STARTING. Refused rather than launched
                # without it: a launch that cannot leave its trace is the one
                # a crash would turn into an unknown DayZ occupying the box.
                intent = self._launch_intent_for(
                    run_id, launch_role, minted, parsed, provisional
                )
                if intent is not None:
                    try:
                        self._write_launch_intent(intent)
                    except Exception:
                        self._retire_minted(
                            run_id, launch_role, minted, "launch_failed"
                        )
                        return self._settle_failed_launch(
                            client=client,
                            previous=previous,
                            provisional=provisional,
                            launched=None,
                            record=None,
                            confirmed_error="launch_intent_failed",
                            attempt_started_at=attempt_started_at,
                        )
                launched_at = self._server_start_clock()
                try:
                    launched = self.launcher(
                        list(parsed["argv"]),
                        str(parsed["cwd"]),
                        str(parsed["window_style"]),
                    )
                    pid = getattr(launched, "pid", None)
                    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                        raise OSError("invalid_launcher_pid")
                except Exception:
                    self._retire_minted(run_id, launch_role, minted, "launch_failed")
                    return self._settle_failed_launch(
                        client=client,
                        previous=previous,
                        provisional=provisional,
                        launched=launched,
                        record=None,
                        confirmed_error="lifecycle_start_failed",
                        attempt_started_at=attempt_started_at,
                    )

                try:
                    actual = self.guard.snapshot(pid)
                except Exception:
                    actual = {"error": "guard_unavailable", "exit_code": 3}
                record = self._record_from_snapshot(actual, launch_role)
                if record is None:
                    self._retire_minted(run_id, launch_role, minted, "launch_failed")
                    return self._settle_failed_launch(
                        client=client,
                        previous=previous,
                        provisional=provisional,
                        launched=launched,
                        record=None,
                        confirmed_error="identity_unavailable",
                        attempt_started_at=attempt_started_at,
                    )
                self._confirm_instance(minted, record)

                completed = RunRecord.from_payload(dataclasses.asdict(provisional))
                completed.processes.append(record)
                completed.state = "RUNNING"
                try:
                    self.manifest.replace(completed)
                except Exception:
                    self._retire_minted(run_id, launch_role, minted, "launch_failed")
                    return self._settle_failed_launch(
                        client=client,
                        previous=previous,
                        provisional=provisional,
                        launched=launched,
                        record=record,
                        confirmed_error="lifecycle_start_failed",
                        manifest_failure=True,
                        attempt_started_at=attempt_started_at,
                    )
                if intent is not None:
                    # What recover_launch_intent would conclude about this very
                    # process after a crash. A mismatch changes nothing here;
                    # it leaves the row a live check can read.
                    unmatched = self._launch_intent_mismatch(intent, record)
                    if unmatched is not None and not self._audit(
                        "lifecycle_launch_intent_unmatchable",
                        client,
                        unmatched,
                        "recorded",
                        run_id=run_id,
                        role=launch_role,
                    ):
                        self._note_audit_row_dropped()
                self._capture_start_activity(completed, launched=record)
                if supplied_run_id is None:
                    # 250f: the session that created the run is its launcher
                    # for this generation; an extension never replaces it.
                    with self._activity_lock:
                        self._launched_by.setdefault(
                            self._activity_key(run_id), client
                        )
                if server_launch:
                    self._begin_server_start_watch(
                        run_id, parsed["argv"], launched_at
                    )
                elif existing is not None:
                    # The worker adds the client only after readiness saw the
                    # server's port bound: that start is over.
                    self._end_server_start_watch(run_id)

                result: dict[str, object] = {
                    "ok": True,
                    "run_id": run_id,
                    "state": "RUNNING",
                }
                self._last_start_error = None
                if steam is not None:
                    self._store_steam_preparation(client.session_id, run_id, steam)
                return self._terminal_outcome(
                    result,
                    "lifecycle_start_outcome",
                    client,
                    reason="started",
                    decision="started",
                    run_id=run_id,
                    state="RUNNING",
                )
            finally:
                self._finish_committed(authority, command_id)
                if intent is not None:
                    self._settle_launch_intent(run_id)

    def _launch_intent_path(self) -> Path | None:
        paths = getattr(self.manifest, "paths", None)
        runs_path = getattr(paths, "runs_path", None)
        if isinstance(runs_path, (str, Path)):
            return Path(runs_path).with_name(_LAUNCH_INTENT_NAME)
        return None

    def _launch_intent_for(
        self,
        run_id: str,
        role: str,
        minted: str | None,
        parsed: dict[str, object],
        provisional: RunRecord,
    ) -> dict[str, object] | None:
        """The launch intent of this launch, or None when nothing could bind it.

        A process is tied to an intent by its argv and by the bridge instance
        it reads, so there is an intent only when an instance was minted and
        the argv names the profiles folder that holds it. Without one the
        launch runs as it did before the intent existed.
        """
        if self._launch_intent_path() is None or not _valid_uuid4(minted):
            return None
        argv = [str(item) for item in parsed["argv"]]
        profiles = parsed.get("profiles")
        named = _launch_output_dir(argv)
        if (
            not isinstance(profiles, str)
            or named is None
            or os.path.normcase(os.path.normpath(named))
            != os.path.normcase(os.path.normpath(profiles))
            or provisional.owner_session_id is None
            or provisional.owner_lease_id is None
        ):
            return None
        try:
            command_line_sha256 = identity_hashes(argv[0], argv)["command_line_sha256"]
            not_before_utc = format_creation_time_utc(self._launch_intent_clock())
        except (OSError, OverflowError, TypeError, ValueError):
            return None
        return {
            "version": _LAUNCH_INTENT_VERSION,
            "run_id": run_id,
            "role": role,
            "instance": minted,
            "profiles": profiles,
            "command_line_sha256": command_line_sha256,
            "owner_session_id": provisional.owner_session_id,
            "owner_lease_id": provisional.owner_lease_id,
            "not_before_utc": not_before_utc,
        }

    def _write_launch_intent(self, document: dict[str, object]) -> None:
        path = self._launch_intent_path()
        if path is None:
            raise RuntimeError("launch_intent_unavailable")
        atomic_write_json(path, document)

    def _clear_launch_intent(self) -> bool:
        path = self._launch_intent_path()
        if path is None:
            return True
        try:
            path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    def _settle_launch_intent(self, run_id: str) -> None:
        """Drop the intent of this launch unless its row is still STARTING.

        STARTING is the one durable state a restart cannot tell from a launch
        that was cut short; every other state already says how it ended. A
        file that cannot be removed is retired by the next daemon start.
        """
        try:
            current = self.manifest.get(run_id)
            if current is not None and current.state == "STARTING":
                return
            self._clear_launch_intent()
        except Exception:
            return

    def _read_launch_intent(self) -> dict[str, object] | None:
        """The intent on disk, or None unless it is exactly format version 1."""
        path = self._launch_intent_path()
        if path is None:
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return None
        if (
            not isinstance(document, dict)
            or set(document) != _LAUNCH_INTENT_FIELDS
            or type(document["version"]) is not int
            or document["version"] != _LAUNCH_INTENT_VERSION
        ):
            return None
        for key in ("run_id", "role", "profiles", "owner_session_id", "owner_lease_id"):
            value = document[key]
            if not isinstance(value, str) or not value:
                return None
        if (
            not _valid_uuid4(document["instance"])
            or not _valid_sha256(document["command_line_sha256"])
            or not Path(str(document["profiles"])).is_absolute()
            or _utc_epoch(document["not_before_utc"]) is None
        ):
            return None
        return document

    @staticmethod
    def _launch_intent_mismatch(
        document: Mapping[str, object], record: ProcessRecord
    ) -> str | None:
        """Why this process is not the one the intent launched, or None.

        The argv hash and the creation time. The instance lives in the
        profiles, which _match_launch_intent reads.
        """
        if record.command_line_sha256 != document.get("command_line_sha256"):
            return "argv_mismatch"
        created = _utc_epoch(record.creation_time_utc)
        not_before = _utc_epoch(document.get("not_before_utc"))
        if (
            created is None
            or not_before is None
            or created < not_before - _LAUNCH_INTENT_CLOCK_SLACK_S
            or created > not_before + _LAUNCH_INTENT_WINDOW_S
        ):
            return "created_outside_intent"
        return None

    @staticmethod
    def _launch_intent_instance(document: Mapping[str, object]) -> str | None:
        """The instance the profiles of the intent hold now.

        loopback.ServerState.prepare wrote it into <profiles>\\dayz_mcp.json
        before the Popen; the game only reads that file.
        """
        config = Path(str(document["profiles"])) / "dayz_mcp.json"
        try:
            payload = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        instance = payload.get("instance")
        return instance if isinstance(instance, str) else None

    def _match_launch_intent(
        self, document: dict[str, object]
    ) -> tuple[ProcessRecord | None, str]:
        """The one live process the intent launched, or why there is none.

        Candidates are the DayZ images the process scan lists that no active
        run registers. A candidate the guard cannot read stops the match (it
        could be the process sought); two with the argv hash are ambiguous.
        The one match must then be created within the launch and read, from
        the profiles, the instance the intent carries.
        """
        reason, listed = self._diag_snapshot()
        if reason is not None or listed is None:
            return None, reason or "diag_snapshot_unknown"
        registered = {
            record.pid
            for run in self.manifest.list_runs()
            if run.state in _ACTIVE_STATES
            for record in run.processes
        }
        role = str(document["role"])
        matched: list[ProcessRecord] = []
        for row in listed:
            pid = int(row["pid"])
            if pid in registered:
                continue
            try:
                actual = self.guard.snapshot(pid)
            except Exception:
                return None, "guard_unavailable"
            if (
                isinstance(actual, dict)
                and actual.get("error") == "process_not_found"
                and actual.get("exit_code") == 4
            ):
                continue
            record = self._record_from_snapshot(actual, role)
            if record is None or record.pid != pid:
                return None, "identity_unavailable"
            if record.command_line_sha256 == document["command_line_sha256"]:
                matched.append(record)
        if not matched:
            return None, "process_not_found"
        if len(matched) > 1:
            return None, "process_ambiguous"
        record = matched[0]
        mismatch = self._launch_intent_mismatch(document, record)
        if mismatch is not None:
            return None, mismatch
        if self._launch_intent_instance(document) != document["instance"]:
            return None, "instance_mismatch"
        return record, ""

    def _retire_launch_intent(
        self, document: dict[str, object] | None, reason: str
    ) -> dict[str, object]:
        fields: dict[str, object] = {}
        if document is not None:
            fields["run_id"] = str(document["run_id"])
        if not self._audit(
            "lifecycle_launch_intent_retired", None, reason, "retired", **fields
        ):
            self._note_audit_row_dropped()
        outcome: dict[str, object] = {"launch_intent": "retired", "reason": reason}
        outcome.update(fields)
        if not self._clear_launch_intent():
            outcome["cleanup_degraded"] = ["launch_intent_not_removed"]
        return outcome

    def recover_launch_intent(self) -> dict[str, object]:
        """Daemon start: settle the launch intent a daemon that died left behind.

        fb-20260904-200821-dae1 part 2 (A1-F7). daemon._activate_server_coordination
        calls it before recover_unacknowledged_before_listen and
        recover_after_restart. A process that matches the intent exactly -- its
        argv hash, a creation time from the launch, the instance its profiles
        hold -- joins its STARTING row as the manifest write of start_run would
        have recorded it, and those two recoveries then give the run what they
        give any RUNNING run whose owner vanished. Anything short of that
        retires the intent and leaves the row to them unchanged. Kills nothing.
        No intent file (the legacy state) is a no-op.
        """
        path = self._launch_intent_path()
        if path is None or not path.exists():
            return {"launch_intent": "absent"}
        with self._operation_lock:
            try:
                return self._recover_launch_intent_locked()
            except Exception:
                # A fault here must not keep the daemon from starting: the
                # intent stays, the row goes to the existing recovery.
                return {"launch_intent": "kept", "reason": "recovery_failed"}

    def _recover_launch_intent_locked(self) -> dict[str, object]:
        document = self._read_launch_intent()
        if document is None:
            return self._retire_launch_intent(None, "intent_unreadable")
        run_id = str(document["run_id"])
        run = self.manifest.get(run_id)
        if run is None:
            return self._retire_launch_intent(document, "run_not_found")
        if run.state != "STARTING":
            return self._retire_launch_intent(document, "launch_settled")
        if (
            run.owner_session_id != document["owner_session_id"]
            or run.owner_lease_id != document["owner_lease_id"]
        ):
            return self._retire_launch_intent(document, "run_changed")
        if self._quarantined():
            return self._retire_launch_intent(document, "retail_quarantine")
        record, reason = self._match_launch_intent(document)
        if record is None:
            return self._retire_launch_intent(document, reason)
        if not self._audit(
            "lifecycle_launch_intent_recovered",
            None,
            "process_matched",
            "recorded",
            run_id=run_id,
            role=record.role,
            pid=record.pid,
        ):
            return {"launch_intent": "kept", "reason": "audit_failed", "run_id": run_id}
        completed = RunRecord.from_payload(dataclasses.asdict(run))
        completed.processes.append(record)
        completed.state = "RUNNING"
        try:
            self.manifest.replace(completed)
        except Exception:
            return {"launch_intent": "kept", "reason": "manifest_failed", "run_id": run_id}
        self._invalidate_box_cache()
        outcome: dict[str, object] = {
            "launch_intent": "recovered",
            "run_id": run_id,
            "role": record.role,
            "pid": record.pid,
        }
        if not self._clear_launch_intent():
            outcome["cleanup_degraded"] = ["launch_intent_not_removed"]
        return outcome

    def ack_run(
        self,
        client: ClientIdentity,
        token: str | None,
        run_id: object,
        launch_operation_id: object,
    ) -> dict[str, object]:
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        command = "lifecycle_ack"
        decision, error = self._authorize(client, token, command)
        if error is not None:
            return error
        authority = self._authority(decision)
        if authority is None:
            return self._error("lease_required", 403)
        with self._operation_lock:
            if not self._reservation_active(authority, command):
                return self._error("lease_invalid", 409)
            if not isinstance(run_id, str) or not isinstance(
                launch_operation_id, str
            ):
                return self._reject_reserved(
                    authority, command, "launch_identity_conflict"
                )
            run = self.manifest.get(run_id)
            if isinstance(run_id, str) and run is not None:
                refusal = self._refuse_unconfirmed_owner(client, run_id)
                if refusal is not None:
                    result = self._reject_reserved(
                        authority, command, "run_protected"
                    )
                    result.update(refusal)
                    return result
                run = self.manifest.get(run_id)
            if (
                run is None
                or run.state != "RUNNING"
                or run.owner_session_id != client.session_id
                or run.owner_lease_id != authority[1]
                or run.launch_operation_id != launch_operation_id
            ):
                return self._reject_reserved(
                    authority, command, "launch_identity_conflict"
                )
            if not self._audit(
                "lifecycle_ack",
                client,
                "launch_identity_match",
                "acknowledged",
                run_id=run_id,
            ):
                self.coordinator.reject_reservation(
                    authority[0], authority[1], authority[2], "audit_failed"
                )
                return self._error("audit_failed", 503)
            command_id = self._commit_reserved(authority, command)
            if command_id is None:
                return self._error("lease_invalid", 409)
            try:
                if not run.launch_acknowledged:
                    run.launch_acknowledged = True
                    try:
                        self.manifest.replace(run)
                    except Exception:
                        return self._error("manifest_failed", 503)
                return {"ok": True, "run_id": run_id, "state": run.state}
            finally:
                self._finish_committed(authority, command_id)

    @staticmethod
    def _terminate_open_handle(launched: object) -> bool:
        try:
            launched.terminate()
        except Exception:
            poll = getattr(launched, "poll", None)
            if callable(poll):
                try:
                    return poll() is not None
                except Exception:
                    return False
            return False
        try:
            launched.wait(timeout=5.0)
            return True
        except Exception:
            poll = getattr(launched, "poll", None)
            if callable(poll):
                try:
                    return poll() is not None
                except Exception:
                    return False
            return False

    @staticmethod
    def _record_from_snapshot(value: object, role: str) -> ProcessRecord | None:
        if (
            not isinstance(value, dict)
            or value.get("identity_complete") is not True
            or value.get("identity_scheme") != "psutil-argv-v2"
        ):
            return None
        try:
            record = ProcessRecord(
                value.get("pid"),
                value.get("creation_time_utc"),
                str(value.get("executable_sha256", "")).casefold(),
                str(value.get("command_line_sha256", "")).casefold(),
                role,
                identity_scheme="psutil-argv-v2",
            )
            record.validate()
            return record
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _identity_matches(record: ProcessRecord, actual: object) -> bool:
        if not isinstance(actual, dict) or actual.get("identity_complete") is not True:
            return False
        return (
            actual.get("identity_scheme") == record.identity_scheme
            and actual.get("pid") == record.pid
            and actual.get("creation_time_utc") == record.creation_time_utc
            and str(actual.get("executable_sha256", "")).casefold() == record.executable_sha256.casefold()
            and str(actual.get("command_line_sha256", "")).casefold() == record.command_line_sha256.casefold()
        )

    def _classify_registered_process(self, record: ProcessRecord) -> tuple[str, str]:
        """Classify a registered PID as owned, gone, foreign, or unknown.

        Absent (process_not_found) and a complete identity that does not match
        are both "no longer ours". Unknown snapshots stay fail-closed. A foreign
        PID must never be terminated.
        """
        try:
            actual = self.guard.snapshot(record.pid)
        except Exception:
            return "unknown", "guard_unavailable"
        if not isinstance(actual, dict):
            return "unknown", "process_identity_mismatch"
        if actual.get("error") == "process_not_found" and actual.get("exit_code") == 4:
            return "gone", "process_not_found"
        if actual.get("exit_code") == 3:
            return "unknown", "guard_unavailable"
        if self._identity_matches(record, actual):
            return "owned", ""
        if actual.get("identity_complete") is True:
            return "foreign", "process_identity_mismatch"
        return "unknown", "process_identity_mismatch"

    def _partition_registered_processes(
        self, processes: list[ProcessRecord]
    ) -> tuple[dict[str, list[int]], str | None]:
        buckets: dict[str, list[int]] = {
            "owned": [],
            "gone": [],
            "foreign": [],
            "unknown": [],
        }
        unknown_reason: str | None = None
        for record in processes:
            kind, reason = self._classify_registered_process(record)
            buckets[kind].append(record.pid)
            if kind == "unknown" and unknown_reason is None:
                unknown_reason = reason
        return buckets, unknown_reason

    def classify_registered_client_liveness(
        self, run_id: object, destination: tuple[int, str] | None = None
    ) -> str:
        """alive, dead, unknown or none for one exact run's player records.

        d17c-a: identity-sensitive admission classifies the registered
        client/offline records through _classify_registered_process, the full
        native identity snapshot, and never through the PID census
        _client_liveness uses for use_state. Read-only: it takes no
        _operation_lock and terminates, reaps and relaunches nothing. Doubt is
        never a death: an unreadable run, a missing run or a guard that cannot
        answer reads unknown, and a run without client/offline records has no
        client process (none) instead of a guessed one.

        destination, when given, is the (pid, creation_time_utc) identity the
        caller is about to publish to. A death is only evidence when the
        snapshot this verdict was computed from actually observed that
        identity: reattach confirms the station binding before its manifest
        record is published, so a clone taken inside that window still lists
        only the superseded client. A pinned destination absent from the
        snapshot reads unknown instead of inheriting the replaced client's
        death.
        """
        if not isinstance(run_id, str) or not run_id:
            return "unknown"
        try:
            run = self.manifest.get(run_id)
        except Exception:
            return "unknown"
        if run is None:
            return "unknown"
        processes = getattr(run, "processes", None)
        if not isinstance(processes, list):
            return "unknown"
        records: list[ProcessRecord] = []
        observed: list[tuple[int, str]] = []
        for item in processes:
            if getattr(item, "role", None) not in _PLAYER_ROLES:
                continue
            if not isinstance(item, ProcessRecord):
                return "unknown"
            records.append(item)
            observed.append((item.pid, item.creation_time_utc))
        if not records:
            return "none"
        verdicts = [
            self._classify_registered_process(record)[0] for record in records
        ]
        if "owned" in verdicts:
            return "alive"
        if "unknown" in verdicts:
            return "unknown"
        if destination is not None and destination not in observed:
            return "unknown"
        return "dead"

    def _ports_released(self, pids: set[int]) -> str | None:
        """None when no UDP holder carries one of these pids (P-L2.c).

        Fail-closed twice over: a table this daemon cannot read is never
        "released", and a pid still listed answers port_still_held instead of
        letting the caller retire a record that the pre-launch probe of
        start_run would then read as a foreign holder. A lifecycle wired
        without a port probe has no table to contradict, and _port_holders
        answers an empty known scan for it.

        A3-F4: a bare pid is not an identity, and this runs right after the
        guard confirmed the exit, which is the instant that pid stopped being
        one. The OS is free to hand it to anything, so only a holder that could
        plausibly be the game counts: a DayZ image on any port, or any image on
        a port of the band this project launches in. A recycled pid holding an
        unrelated socket no longer blocks the relaunch of a client it never was.
        """
        if not pids:
            return None
        tries = max(1, int(self._role_release_tries))
        reason = "port_scan_unknown"
        for attempt in range(tries):
            scan_reason, holders = self._port_holders()
            if scan_reason is None and holders is not None:
                held = any(
                    isinstance(holder.get("pid"), int)
                    and holder["pid"] in pids
                    and (
                        _is_dayz_image(holder.get("name"))
                        or holder["port"] in _DAYZ_PORT_RANGE
                    )
                    for holder in holders
                )
                if not held:
                    return None
                reason = "port_still_held"
            else:
                reason = scan_reason or "port_scan_unknown"
            if attempt + 1 < tries:
                time.sleep(self._role_release_interval_s)
        return reason

    def _replace_role_processes(
        self, run: RunRecord, role: str, *, client: ClientIdentity
    ) -> tuple[list[int], str | None]:
        """Free a role on a run being extended, before a new process takes it.

        fb-20260904-025027-8f76 (part c): start_run appended the relaunched
        client next to the hung one, and RunRecord.validate does not bound a
        run to one process per role, so the row carried two client records and
        the liveness projection reported the last of them. The role's instance
        binding is already retired on this same path (_prepare_instance with
        replacing_role), so the superseded process is cut off from the bridge
        either way: what was missing was ending it and dropping its record.

        Order: terminate -> confirm -> retire. The process is confirmed by the
        guard, which waits for the exit before answering terminated
        (native_process_guard.py: process.wait after kill); the socket is
        confirmed by _ports_released. The owned/foreign/unknown split is
        stop_run's and is not relaxed: only a process this lifecycle can vouch
        for is terminated, and a record it cannot classify is left exactly
        where it is - a duplicate record is preferable to ending a process that
        may not be ours.

        Answers (retired_pids, error). On an error the run keeps every record;
        the terminations already confirmed are reported so the caller can
        settle the launch on the truth.
        """
        role_records = [record for record in run.processes if record.role == role]
        if not role_records:
            return [], None
        owned: list[ProcessRecord] = []
        gone: list[ProcessRecord] = []
        for record in role_records:
            kind, _reason = self._classify_registered_process(record)
            if kind == "owned":
                owned.append(record)
            elif kind == "gone":
                gone.append(record)
        retiring = owned + gone
        if not retiring:
            return [], None
        if len(retiring) >= len(run.processes):
            # RunRecord.validate demands processes on a RUNNING run, so an
            # emptied row would make the rollback target of
            # _settle_failed_launch unpersistable. Refuse before terminating.
            return [], "run_would_be_empty"
        if not self._audit(
            "lifecycle_role_replaced",
            client,
            "role_superseded",
            "allowed",
            run_id=run.run_id,
            role=role,
            owned_pids=[record.pid for record in owned],
            gone_pids=[record.pid for record in gone],
        ):
            return [], "audit_failed"
        terminated: list[int] = []
        for record in owned:
            try:
                guard_result = self.guard.terminate(record)
            except Exception:
                guard_result = {
                    "error": "guard_unavailable",
                    "exit_code": 3,
                    "terminated": False,
                }
            if (
                not isinstance(guard_result, dict)
                or guard_result.get("terminated") is not True
            ):
                return terminated, (
                    str(guard_result.get("error", "termination_failed"))
                    if isinstance(guard_result, dict)
                    else "termination_failed"
                )
            terminated.append(record.pid)
        release_reason = self._ports_released(
            {record.pid for record in retiring}
        )
        if release_reason is not None:
            return terminated, release_reason
        retired = {(record.pid, record.role) for record in retiring}
        run.processes = [
            record
            for record in run.processes
            if (record.pid, record.role) not in retired
        ]
        return [record.pid for record in retiring], None

    def _stop_reaped_run(
        self,
        client: ClientIdentity,
        authority: tuple[str, str, str],
        command: str,
        run: RunRecord,
    ) -> dict[str, object] | None:
        """The stop of a run the reaper retired from this caller, or None.

        fb-20260904-200821-dae1 part 1 (H-A2-3). The reaper can run between the
        adopt and the stop of the worker. The stop then found the row EXITED
        and answered run_not_adopted about a run this caller owned and the
        daemon itself retired; dayz_test_stop only learned the box was free
        through the status fallback of the worker (744a), when that extra call
        got through. The answer is the reply the sealed worker maps to success
        (ok, run_id, state EXITED: dayz_test_worker._successful_run), and only
        when the reap cleared exactly this session and lease and every record
        it retired still reads gone or foreign. Anything else is None, and the
        caller answers run_not_adopted as before. Caller holds _operation_lock.
        """
        with self._activity_lock:
            reaped = self._reaped_owners.get(self._activity_key(run.run_id))
        if (
            reaped is None
            or reaped[0] != client.session_id
            or reaped[1] != authority[1]
            or run.state != "EXITED"
            or run.owner_session_id is not None
            or run.owner_lease_id is not None
            or run.processes
        ):
            return None
        buckets, _unknown_reason = self._partition_registered_processes(
            list(reaped[2])
        )
        if buckets["owned"] or buckets["unknown"]:
            return None
        if not self._audit(
            "lifecycle_stop",
            client,
            "already_reaped",
            "allowed",
            run_id=run.run_id,
            gone_pids=list(buckets["gone"]),
            foreign_pids=list(buckets["foreign"]),
        ):
            self.coordinator.reject_reservation(
                authority[0], authority[1], authority[2], "audit_failed"
            )
            return self._error("audit_failed", 503)
        command_id = self._commit_reserved(authority, command)
        if command_id is None:
            return self._error("lease_invalid", 409)
        try:
            result: dict[str, object] = {
                "ok": True,
                "run_id": run.run_id,
                "state": "EXITED",
                "terminated": 0,
                "stop_method": "already_exited",
                "exit_metrics_valid": False,
            }
            return self._terminal_outcome(
                result,
                "lifecycle_stop_outcome",
                client,
                reason="already_reaped",
                decision="already_exited",
                run_id=run.run_id,
                state="EXITED",
                terminated=0,
            )
        finally:
            self._finish_committed(authority, command_id)

    def stop_run(self, client: ClientIdentity, token: str | None, run_id: object) -> dict[str, object]:
        """Stop the run. Ordinary callers are unchanged.

        Warden path, residual contract:
        no effect after any doubt observed up to the final check of the first effect.
        the only residual window is between that check and the syscall.
        after the first effect, no foreign target is touched, and the outcome says so.
        """
        if isinstance(run_id, str) and run_id:
            self.note_launcher_request(client, run_id)
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        command = "lifecycle_stop"
        decision, error = self._authorize(client, token, command)
        if error is not None:
            return error
        authority = self._authority(decision)
        if authority is None:
            return self._error("lease_required", 403)
        with self._operation_lock:
            if not self._reservation_active(authority, command):
                return self._error("lease_invalid", 409)
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            if not isinstance(run_id, str) or not run_id:
                return self._reject_reserved(authority, command, "run_not_found", 404)
            run = self.manifest.get(run_id)
            if run is None:
                return self._reject_reserved(authority, command, "run_not_found", 404)
            refusal = self._refuse_unconfirmed_owner(client, run_id)
            if refusal is not None:
                result = self._reject_reserved(authority, command, "run_protected")
                result.update(refusal)
                return result
            run = self.manifest.get(run_id)
            if run is None:
                return self._reject_reserved(authority, command, "run_not_found", 404)
            never_started = (
                run.state == "EXITED"
                and run.launch_acknowledged is False
                and not run.processes
                and run.owner_session_id is None
                and run.owner_lease_id is None
            )
            if not never_started and run.state == "EXITED":
                reaped = self._stop_reaped_run(client, authority, command, run)
                if reaped is not None:
                    return reaped
            if not never_started and (
                run.owner_session_id != client.session_id
                or run.owner_lease_id != authority[1]
                or run.state != "RUNNING"
            ):
                return self._reject_reserved(authority, command, "run_not_adopted")
            # The warden authorizes the whole set. A foreign, gone, or unknown
            # process refuses the stop before any skip-and-continue.
            stop_guard = self._warden_idle_guard(client, "stop", run_id)
            if stop_guard is not None:
                refusal = self._idle_destruction_refusal(run, stop_guard)
                if refusal is not None:
                    return self._reject_reserved(authority, command, refusal)
            owned: list[ProcessRecord] = []
            gone_pids: list[int] = []
            foreign_pids: list[int] = []
            for record in run.processes:
                kind, reason = self._classify_registered_process(record)
                if kind == "owned":
                    owned.append(record)
                    continue
                if kind == "gone":
                    gone_pids.append(record.pid)
                    continue
                if kind == "foreign":
                    foreign_pids.append(record.pid)
                    continue
                run.owner_session_id = None
                run.owner_lease_id = None
                run.state = "UNRECONCILED"
                self._retire_run_bindings(run.run_id, "stopped")
                try:
                    self.manifest.replace(run)
                except Exception:
                    result = self._error("manifest_failed", 503)
                    degraded = ["manifest_failed"]
                    degraded.extend(
                        self.coordinator.abort_reservation(
                            authority[0],
                            authority[1],
                            authority[2],
                            "manifest_failed",
                        )
                    )
                    result["cleanup_degraded"] = list(
                        dict.fromkeys(degraded)
                    )
                    return self._terminal_outcome(
                        result,
                        "lifecycle_stop_outcome",
                        client,
                        reason="manifest_failed",
                        decision="manual_cleanup_required",
                        run_id=run.run_id,
                        state="RUNNING",
                        terminated=0,
                    )
                self._invalidate_box_cache()
                result = self._reject_reserved(
                    authority,
                    command,
                    reason,
                    503 if reason == "guard_unavailable" else 409,
                )
                result["run_id"] = run.run_id
                result["state"] = "UNRECONCILED"
                return self._terminal_outcome(
                    result,
                    "lifecycle_stop_outcome",
                    client,
                    reason=reason,
                    decision="manual_cleanup_required",
                    run_id=run.run_id,
                    state="UNRECONCILED",
                    terminated=0,
                )
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            if not self._audit(
                "lifecycle_stop",
                client,
                "identity_match",
                "allowed",
                run_id=run_id,
                owned_pids=[record.pid for record in owned],
                gone_pids=list(gone_pids),
                foreign_pids=list(foreign_pids),
            ):
                self.coordinator.reject_reservation(
                    authority[0], authority[1], authority[2], "audit_failed"
                )
                return self._error("audit_failed", 503)
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            command_id = self._commit_reserved(authority, command)
            if command_id is None:
                return self._error("lease_invalid", 409)
            try:
                # fb-20260818-232129-1233: the worker stops a run whose
                # readiness timed out; read its RPT before anything dies.
                try:
                    self._settle_server_start_watch(run_id)
                except Exception:
                    pass
                if stop_guard is not None:
                    refusal = self._idle_destruction_refusal(run, stop_guard)
                    if refusal is not None:
                        return {"error": refusal, "run_id": run_id}
                self._retire_run_bindings(run_id, "stopped")
                run.state = "STOPPING"
                try:
                    self.manifest.replace(run)
                except Exception:
                    result = self._error("manifest_failed", 503)
                    self._add_degradation(result, "manifest_failed")
                    return self._terminal_outcome(
                        result,
                        "lifecycle_stop_outcome",
                        client,
                        reason="manifest_failed",
                        decision="manual_cleanup_required",
                        run_id=run_id,
                        state="RUNNING",
                        terminated=0,
                    )
                self._invalidate_box_cache()
                if stop_guard is not None:
                    current_run = self.manifest.get(run_id)
                    watched = current_run if current_run is not None else run
                    refusal = self._idle_destruction_refusal(watched, stop_guard)
                    if refusal is not None:
                        self._restore_running_if_stopping(watched)
                        return {"error": refusal, "run_id": run_id}
                terminated = 0
                acted_pids: list[int] = []
                untouched_reasons: list[tuple[int, str]] = []
                survivors = list(owned)
                for record in list(owned):
                    if self._quarantined():
                        run.owner_session_id = None
                        run.owner_lease_id = None
                        run.state = "UNRECONCILED"
                        run.processes = list(survivors)
                        degraded: list[str] = []
                        try:
                            self.manifest.replace(run)
                        except Exception:
                            degraded.append("manifest_failed")
                        else:
                            self._invalidate_box_cache()
                        result: dict[str, object] = {
                            "error": "partial_cleanup",
                            "reason": "retail_quarantine",
                            "terminated": terminated,
                            "run_id": run_id,
                            "_http_status": 409,
                        }
                        if degraded:
                            result["cleanup_degraded"] = degraded
                        return self._terminal_outcome(
                            result,
                            "lifecycle_stop_outcome",
                            client,
                            reason="retail_quarantine",
                            decision="partial_cleanup",
                            run_id=run_id,
                            state="UNRECONCILED",
                            terminated=terminated,
                        )
                    if stop_guard is not None:
                        # Snapshot and the warden's switch, queue and anchor
                        # checks run inside the boundary. terminate is next.
                        boundary = self._idle_effect_boundary(
                            run,
                            run_id,
                            stop_guard,
                            record,
                            first=not acted_pids,
                        )
                        if boundary is not None:
                            scope, reason = boundary
                            if scope == "operation" and not acted_pids:
                                self._restore_running_if_stopping(run)
                                return {"error": reason, "run_id": run_id}
                            if scope == "operation":
                                already = {pid for pid, _reason in untouched_reasons}
                                for pending in survivors:
                                    if pending.pid not in already:
                                        untouched_reasons.append((pending.pid, reason))
                                break
                            untouched_reasons.append((record.pid, reason))
                            continue
                    try:
                        guard_result = self.guard.terminate(record)
                    except Exception:
                        guard_result = {
                            "error": "guard_unavailable",
                            "exit_code": 3,
                            "terminated": False,
                        }
                    if (
                        not isinstance(guard_result, dict)
                        or guard_result.get("terminated") is not True
                    ):
                        # The guard refused inside the residual window: the
                        # process was not killed. Do not record a cleanup of it.
                        if stop_guard is not None and isinstance(guard_result, dict):
                            missed = guard_result.get("error")
                            if missed in {
                                "process_identity_mismatch",
                                "process_not_found",
                                "invalid_expected_identity",
                                "identity_unavailable",
                                "guard_unavailable",
                            }:
                                if not acted_pids:
                                    self._restore_running_if_stopping(run)
                                    return {
                                        "error": str(missed),
                                        "run_id": run_id,
                                    }
                                untouched_reasons.append((record.pid, str(missed)))
                                continue
                        run.owner_session_id = None
                        run.owner_lease_id = None
                        run.state = "UNRECONCILED"
                        run.processes = list(survivors)
                        degraded = []
                        try:
                            self.manifest.replace(run)
                        except Exception:
                            degraded.append("manifest_failed")
                        else:
                            self._invalidate_box_cache()
                        response: dict[str, object] = {
                            "error": "partial_cleanup",
                            "terminated": terminated,
                            "run_id": run_id,
                            "_http_status": 409,
                        }
                        if degraded:
                            response["cleanup_degraded"] = degraded
                        return self._terminal_outcome(
                            response,
                            "lifecycle_stop_outcome",
                            client,
                            reason=(
                                str(guard_result.get("error", "termination_failed"))
                                if isinstance(guard_result, dict)
                                else "termination_failed"
                            ),
                            decision="partial_cleanup",
                            run_id=run_id,
                            state="UNRECONCILED",
                            terminated=terminated,
                        )
                    terminated += 1
                    acted_pids.append(record.pid)
                    survivors.remove(record)
                if stop_guard is not None and acted_pids and untouched_reasons:
                    run.owner_session_id = None
                    run.owner_lease_id = None
                    run.state = "UNRECONCILED"
                    run.processes = list(survivors)
                    degraded_partial: list[str] = []
                    try:
                        self.manifest.replace(run)
                    except Exception:
                        degraded_partial.append("manifest_failed")
                    else:
                        self._invalidate_box_cache()
                    self.clear_idle_retirement(run_id)
                    partial: dict[str, object] = {
                        "error": "partial_stop",
                        "outcome": "partial_stop",
                        "acted_pids": list(acted_pids),
                        "untouched_pids": [pid for pid, _reason in untouched_reasons],
                        "untouched_reasons": [
                            f"{pid}:{reason}" for pid, reason in untouched_reasons
                        ],
                        "terminated": len(acted_pids),
                        "run_id": run_id,
                        "state": "UNRECONCILED",
                        "_http_status": 409,
                    }
                    if degraded_partial:
                        partial["cleanup_degraded"] = degraded_partial
                    return self._terminal_outcome(
                        partial,
                        "lifecycle_stop_outcome",
                        client,
                        reason="partial_stop",
                        decision="partial_stop",
                        run_id=run_id,
                        state="UNRECONCILED",
                        terminated=len(acted_pids),
                    )
                run.state = "EXITED"
                run.owner_session_id = None
                run.owner_lease_id = None
                run.processes = []
                idle_decision = self._peek_idle_retirement(run_id)
                if idle_decision:
                    retire_event = "idle_timeout"
                    retire_reason = "idle_timeout"
                    retire_decision = idle_decision
                else:
                    retire_event = "lifecycle_stop_outcome"
                    retire_reason = "stopped"
                    retire_decision = "stopped"
                if not self._commit_retirement(
                    run,
                    retire_event,
                    retire_reason,
                    retire_decision,
                ):
                    result = {
                        "error": "partial_cleanup",
                        "reason": "manifest_failed",
                        "terminated": terminated,
                        "run_id": run_id,
                        "cleanup_degraded": ["manifest_failed"],
                        "_http_status": 503,
                    }
                    return self._terminal_outcome(
                        result,
                        "lifecycle_stop_outcome",
                        client,
                        reason="manifest_failed",
                        decision="partial_cleanup",
                        run_id=run_id,
                        state="STOPPING",
                        terminated=terminated,
                    )
                if idle_decision:
                    self._take_idle_retirement(run_id)
                # fb-20260908-202102-2edd (2edd-2): stop_run terminates via
                # guard.kill, not orderly mission teardown. Callers that read
                # RPT Leaked/Destroying-game lines after a "succeeded" stop
                # were reading an unexecuted check as zero. Say so on the wire.
                result = {
                    "ok": True,
                    "run_id": run_id,
                    "state": "EXITED",
                    "terminated": terminated,
                    "stop_method": (
                        "forced_kill" if terminated > 0 else "no_live_owned"
                    ),
                    "exit_metrics_valid": False,
                }
                return self._terminal_outcome(
                    result,
                    retire_event,
                    client,
                    reason=retire_reason,
                    decision=retire_decision,
                    run_id=run_id,
                    state="EXITED",
                    terminated=terminated,
                )
            finally:
                self._finish_committed(authority, command_id)

    def close_run(
        self,
        client: ClientIdentity,
        token: str | None,
        run_id: object,
        *,
        only_roles: frozenset[str] | None = None,
    ) -> dict[str, object]:
        """Close the run's windows. Ordinary callers are unchanged.

        Warden path, residual contract:
        no effect after any doubt observed up to the final check of the first effect.
        the only residual window is between that check and the syscall.
        after the first effect, no foreign target is touched, and the outcome says so.

        ``only_roles`` limits the non-warden post to those roles. None closes
        every owned role, which is the idle warden's call. A warden guard
        together with ``only_roles`` refuses before any post.
        """
        if only_roles is not None and (
            not isinstance(only_roles, frozenset)
            or not only_roles
            or any(
                not isinstance(role, str) or role not in _CLOSE_ROLE_NAMES
                for role in only_roles
            )
        ):
            return self._error("bad_args", 400)
        if isinstance(run_id, str) and run_id:
            self.note_launcher_request(client, run_id)
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        command = "lifecycle_close"
        decision, error = self._authorize(client, token, command)
        if error is not None:
            return error
        authority = self._authority(decision)
        if authority is None:
            return self._error("lease_required", 403)
        committed = False
        try:
            with self._operation_lock:
                if not self._reservation_active(authority, command):
                    return self._error("lease_invalid", 409)
                if self._quarantined():
                    return self._reject_reserved(authority, command, "retail_quarantine")
                if not isinstance(run_id, str) or not run_id:
                    return self._reject_reserved(authority, command, "run_not_found", 404)
                run = self.manifest.get(run_id)
                if run is None:
                    return self._reject_reserved(authority, command, "run_not_found", 404)
                refusal = self._refuse_unconfirmed_owner(client, run_id)
                if refusal is not None:
                    result = self._reject_reserved(
                        authority, command, "run_protected"
                    )
                    result.update(refusal)
                    return result
                run = self.manifest.get(run_id)
                if run is None:
                    return self._reject_reserved(authority, command, "run_not_found", 404)
                if (
                    run.owner_session_id != client.session_id
                    or run.owner_lease_id != authority[1]
                    or run.state != "RUNNING"
                ):
                    return self._reject_reserved(authority, command, "run_not_adopted")
                close_guard = self._warden_idle_guard(client, "close", run_id)
                if close_guard is not None and only_roles is not None:
                    return self._reject_reserved(authority, command, "close_failed")
                if close_guard is not None:
                    refusal = self._idle_destruction_refusal(run, close_guard)
                    if refusal is not None:
                        return self._reject_reserved(authority, command, refusal)
                owned: list[ProcessRecord] = []
                for record in run.processes:
                    kind, reason = self._classify_registered_process(record)
                    if kind == "owned":
                        owned.append(record)
                        continue
                    if kind in {"gone", "foreign"}:
                        continue
                    return self._reject_reserved(
                        authority,
                        command,
                        reason,
                        503 if reason == "guard_unavailable" else 409,
                    )
                if self._quarantined():
                    return self._reject_reserved(authority, command, "retail_quarantine")
                rank = {"client": 0, "server": 1}
                owned = [
                    record
                    for _index, record in sorted(
                        enumerate(owned),
                        key=lambda item: (rank.get(item[1].role, 2), item[0]),
                    )
                ]
                if close_guard is None and only_roles is not None:
                    owned = [
                        record for record in owned if record.role in only_roles
                    ]
                if not self._audit(
                    "lifecycle_close",
                    client,
                    "identity_match",
                    "allowed",
                    run_id=run_id,
                    owned_pids=[record.pid for record in owned],
                ):
                    self.coordinator.reject_reservation(
                        authority[0], authority[1], authority[2], "audit_failed"
                    )
                    return self._error("audit_failed", 503)
                command_id = self._commit_reserved(authority, command)
                if command_id is None:
                    return self._error("lease_invalid", 409)
                committed = True
                try:
                    if close_guard is not None:
                        return self._close_with_idle_guard(run, run_id, close_guard)
                    fns = getattr(self, "window_fns", None)
                    role_rows: dict[str, dict[str, object]] = {}
                    for record in owned:
                        windows = window_close.list_visible_top_level_windows(
                            (record.pid,), fns=fns
                        )
                        windows_found = len(windows)
                        windows_posted = 0
                        for hwnd, window_pid in windows:
                            kind, _reason = self._classify_registered_process(record)
                            if kind != "owned":
                                continue
                            if window_close.post_wm_close(
                                hwnd, fns=fns, expected_pid=record.pid
                            ):
                                windows_posted += 1
                            _ = window_pid
                        existing = role_rows.get(record.role)
                        if existing is None:
                            role_rows[record.role] = {
                                "pid": record.pid,
                                "windows_found": windows_found,
                                "windows_posted": windows_posted,
                            }
                        else:
                            existing["windows_found"] = (
                                int(existing["windows_found"]) + windows_found
                            )
                            existing["windows_posted"] = (
                                int(existing["windows_posted"]) + windows_posted
                            )
                    result: dict[str, object] = {"run_id": run_id}
                    result.update(role_rows)
                    return result
                finally:
                    self._finish_committed(authority, command_id)
        except Exception:
            # Unexpected exit after authorize opened a reservation: abort
            # before commit (same collection as adopt_run); after commit the
            # inner finally already finished the command.
            result = self._error("close_failed", 503)
            if not committed:
                degraded: list[str] = []
                try:
                    degraded.extend(
                        self.coordinator.abort_reservation(
                            authority[0],
                            authority[1],
                            authority[2],
                            "close_failed",
                        )
                    )
                except Exception:
                    try:
                        rejected = self.coordinator.reject_reservation(
                            authority[0],
                            authority[1],
                            authority[2],
                            "close_failed",
                        )
                        extra = getattr(rejected, "cleanup_degraded", ())
                        if extra:
                            degraded.extend(extra)
                    except Exception:
                        degraded.append("reservation_abort_failed")
                if degraded:
                    result["cleanup_degraded"] = list(dict.fromkeys(degraded))
            return result

    def close_run_roles(
        self,
        client: ClientIdentity,
        token: str | None,
        run_id: object,
        roles: object,
    ) -> dict[str, object]:
        """Post WM_CLOSE to the named roles only. ``close_run`` is unchanged.

        The idle warden calls ``close_run`` and never this method. Unknown,
        empty, or non-list roles post nothing.
        """
        if isinstance(roles, str) or not isinstance(roles, (list, tuple)):
            return self._error("bad_args", 400)
        if not roles or not all(isinstance(role, str) for role in roles):
            return self._error("bad_args", 400)
        chosen = frozenset(roles)
        if not chosen or not chosen <= _CLOSE_ROLE_NAMES:
            return self._error("bad_args", 400)
        return self.close_run(client, token, run_id, only_roles=chosen)

    def note_launcher_request(
        self, client: ClientIdentity, run_id: str, *, when: float | None = None
    ) -> bool:
        """Restart the idle clock when the launcher asks for this run (§3.2).

        A queued ticket is stamped from box_occupancy. A lifecycle call with
        the launcher's full session id is stamped here. Either one cancels a
        countdown the warden is already in.
        """

        if not isinstance(run_id, str) or not run_id:
            return False
        if not self._client_is_launcher(client, run_id):
            return False
        stamp = time.time() if when is None else float(when)
        with self._activity_lock:
            key = self._activity_key(run_id)
            current = self._launcher_request_at.get(key)
            if current is None or stamp >= current:
                self._launcher_request_at[key] = stamp
                self._bump_box_revision_locked()
        return True

    def remember_launcher(self, run_id: str, client: ClientIdentity) -> None:
        """Record the launcher for a run this process did not itself start.

        start_run already does this. Tests and a recovered row use it so the
        clock can recognise that session.
        """

        if not isinstance(run_id, str) or not run_id:
            return
        with self._activity_lock:
            self._launched_by.setdefault(self._activity_key(run_id), client)

    def _stamp_queued_launcher_requests(
        self, clock: float, *, strict: bool = False
    ) -> None:
        reader = getattr(self.coordinator, "queued_session_ids", None)
        if not callable(reader):
            if strict:
                raise RuntimeError("launcher_queue_unreadable")
            return
        try:
            sessions = reader()
        except Exception:
            if strict:
                raise
            return
        if not sessions:
            return
        if not isinstance(sessions, (list, tuple)):
            return
        wanted = {session for session in sessions if isinstance(session, str)}
        if not wanted:
            return
        with self._activity_lock:
            changed = False
            generation = self._current_generation()
            for (gen, run_id), identity in self._launched_by.items():
                if gen != generation or identity.session_id not in wanted:
                    continue
                key = (gen, run_id)
                current = self._launcher_request_at.get(key)
                if current is None or clock >= current:
                    self._launcher_request_at[key] = clock
                    changed = True
            if changed:
                self._bump_box_revision_locked()

    def note_queued_launcher_requests(self, *, when: float | None = None) -> None:
        """Stamp runs whose launcher, by full session id, is waiting in the FIFO.

        Does not scan the box and does not expire the queue. The warden calls
        this from every reaction check, including the one inside close_run.
        """

        clock = time.time() if when is None else float(when)
        self._stamp_queued_launcher_requests(clock, strict=True)

    def human_input_at(self, run_id: str) -> float | None:
        with self._activity_lock:
            return self._human_input_at.get(self._activity_key(run_id))

    def launcher_request_at(self, run_id: str) -> float | None:
        with self._activity_lock:
            return self._launcher_request_at.get(self._activity_key(run_id))

    def ownerless_since(self, run_id: str) -> float | None:
        with self._activity_lock:
            return self._ownerless_since.get(self._activity_key(run_id))

    def restore_ownerless_since(self, run_id: str, stamp: float) -> None:
        with self._activity_lock:
            self._ownerless_since[self._activity_key(run_id)] = float(stamp)
            self._bump_box_revision_locked()

    def mark_closing(self, run_id: str) -> None:
        with self._activity_lock:
            self._closing_runs.add(self._activity_key(run_id))
            self._bump_box_revision_locked()

    def clear_closing(self, run_id: str) -> None:
        with self._activity_lock:
            self._closing_runs.discard(self._activity_key(run_id))
            self._bump_box_revision_locked()

    def block_warning(self, run_id: str, until: float) -> None:
        with self._activity_lock:
            self._warning_blocked_until[self._activity_key(run_id)] = float(until)
            self._bump_box_revision_locked()

    def arm_idle_retirement(self, run_id: str, decision: str) -> None:
        if decision not in {"orderly", "fallback_stop"}:
            raise ValueError("invalid_idle_retirement")
        with self._activity_lock:
            self._idle_retirement[run_id] = decision

    def clear_idle_retirement(self, run_id: str) -> None:
        with self._activity_lock:
            self._idle_retirement.pop(run_id, None)

    def _peek_idle_retirement(self, run_id: str) -> str | None:
        with self._activity_lock:
            return self._idle_retirement.get(run_id)

    def _take_idle_retirement(self, run_id: str) -> str | None:
        with self._activity_lock:
            return self._idle_retirement.pop(run_id, None)

    def audit_idle_timeout(
        self,
        client: ClientIdentity | None,
        run_id: str,
        decision: str,
        detail: str = "",
    ) -> bool:
        extra: dict[str, object] = {"run_id": run_id}
        if detail:
            extra["detail"] = detail
        return self._audit("idle_timeout", client, "idle_timeout", decision, **extra)

    def registered_identities(
        self, run_id: str
    ) -> tuple[tuple[object, ...], ...] | None:
        run = self.manifest.get(run_id)
        if run is None:
            return None
        return tuple(
            sorted(
                (
                    record.pid,
                    record.creation_time_utc,
                    record.executable_sha256,
                    record.command_line_sha256,
                    record.role,
                )
                for record in run.processes
            )
        )

    def registered_process_kinds(self, run_id: str) -> dict[str, int] | None:
        """owned/gone/foreign/unknown counts. None when the run is gone."""

        run = self.manifest.get(run_id)
        if run is None:
            return None
        buckets, _reason = self._partition_registered_processes(run.processes)
        return {name: len(pids) for name, pids in buckets.items()}

    def launched_processes_still_alive(self, run_id: str) -> tuple[str, ...] | None:
        """Names that still block ``reap_dead_run``. None when the run is absent.

        Empty when nothing owned, unknown, or still listed by a known diag
        scan is left. An unknown diag scan does not add names, so empty is
        not by itself a known-clean read (``reap_rejection_witness``). A
        name is ``<pid>:<role>`` for a launched role that is still owned,
        still unclassified, or still listed by the diag scan, and
        ``<pid>:diag`` for any other diag process. Does not terminate.
        """

        run = self.manifest.get(run_id)
        if run is None:
            return None
        buckets, _unknown_reason = self._partition_registered_processes(run.processes)
        blocking = set(buckets["owned"]) | set(buckets["unknown"])
        labels: dict[int, str] = {}
        for record in run.processes:
            if record.pid in blocking:
                labels[record.pid] = f"{record.pid}:{record.role}"
        reason, processes = self._diag_snapshot()
        if reason is None and processes is not None:
            roles = {record.pid: record.role for record in run.processes}
            for process in processes:
                pid = process.get("pid")
                if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                    continue
                if pid in labels:
                    continue
                role = roles.get(pid)
                labels[pid] = f"{pid}:{role}" if role is not None else f"{pid}:diag"
        return tuple(labels[pid] for pid in sorted(labels))

    def reap_rejection_witness(
        self, run_id: str
    ) -> tuple[str, tuple[str, ...]] | None:
        """Classify a ``run_not_reapable`` read taken after ``reap_dead_run``.

        ``None`` when the run is absent. ``state`` when the state is not
        reapable. ``unknown`` when the diag scan cannot be vouched for and no
        owned or unclassified role is left. ``alive`` when a known witness
        still blocks; the names match ``launched_processes_still_alive``.
        ``clean`` when that read is known and empty. Ports are not consulted.
        Does not terminate.
        """

        run = self.manifest.get(run_id)
        if run is None:
            return None
        if run.state not in _REAPABLE_STATES:
            return ("state", ())
        buckets, _unknown_reason = self._partition_registered_processes(run.processes)
        named = self.launched_processes_still_alive(run_id)
        labels = named if named is not None else ()
        reason, processes = self._diag_snapshot()
        if reason is not None or processes is None:
            if buckets["owned"] or buckets["unknown"]:
                return ("alive", labels)
            return ("unknown", ())
        if labels or processes:
            return ("alive", labels)
        return ("clean", ())

    def retail_quarantined(self) -> bool:
        return self._quarantined()

    def scans_known(self) -> bool:
        reason, observed = self._diag_snapshot()
        if reason is not None or observed is None:
            return False
        port_reason, holders = self._port_holders()
        if port_reason is not None or holders is None:
            return False
        return True

    def idle_close_gate(
        self,
        run_id: str,
        *,
        allow_gone: bool,
        daemon_generation: str,
        launch_generation: object,
    ) -> str | None:
        """None when a close or a fallback stop is still allowed.

        Gone processes are a mismatch before WM_CLOSE and are ignored before
        the fallback stop: a process that died during the wait is not foreign.
        Anything the guard cannot vouch for, a foreign identity, an unknown
        scan or a generation change refuses the whole close. No partial close
        from this gate. partial_close is only the degraded result after the
        first WM_CLOSE has already been posted.
        """

        if self.daemon_generation != daemon_generation:
            return "generation_changed"
        if self._quarantined():
            return "retail_quarantine"
        if not self.scans_known():
            return "scan_unknown"
        run = self.manifest.get(run_id)
        if run is None:
            return "run_missing"
        if run.daemon_generation_at_launch != launch_generation:
            return "generation_changed"
        if run.state not in {"RUNNING", "STOPPING"}:
            return "run_not_owned"
        buckets, unknown_reason = self._partition_registered_processes(run.processes)
        if buckets["unknown"]:
            return unknown_reason or "process_identity_mismatch"
        if buckets["foreign"]:
            return "process_identity_mismatch"
        if buckets["gone"] and not allow_gone:
            return "process_changed"
        if not buckets["owned"] and not buckets["gone"]:
            return "process_changed"
        return None

    def _restore_running_if_stopping(self, run: RunRecord) -> None:
        if run.state != "STOPPING":
            return
        run.state = "RUNNING"
        try:
            self.manifest.replace(run)
        except Exception:
            return
        self._invalidate_box_cache()

    def _warden_idle_guard(
        self, client: ClientIdentity, operation: str, run_id: str
    ) -> Mapping[str, object] | None:
        guard = _IDLE_GUARD.get()
        if not isinstance(guard, dict):
            return None
        if guard.get("operation") != operation:
            return None
        if guard.get("session_id") != client.session_id:
            return None
        if guard.get("run_id") != run_id:
            return None
        return guard

    def _idle_switch_on(self, guard: Mapping[str, object]) -> bool:
        """True only when the warden's switch file still reads as enabled."""

        path = guard.get("settings_path")
        if isinstance(path, Path):
            settings = path
        elif isinstance(path, str) and path:
            settings = Path(path)
        else:
            return False
        try:
            # idle_warden imports this module, so the reader is loaded lazily.
            from dayz_mcp.idle_warden import read_idle_warden_enabled
        except Exception:
            return False
        try:
            return read_idle_warden_enabled(settings) is True
        except Exception:
            return False

    def _idle_effect_boundary(
        self,
        run: RunRecord,
        run_id: str,
        guard: Mapping[str, object],
        record: ProcessRecord,
        *,
        first: bool,
    ) -> tuple[str, str] | None:
        """None allows the syscall that the caller must invoke next.

        The switch, the FIFO stamp, the input anchors and the NativeProcessGuard
        snapshot are this check. ``operation`` refuses this target and every
        later one. ``target`` refuses only this target. Before the first effect,
        any doubt is ``operation``.
        """

        try:
            if not self._idle_switch_on(guard):
                return ("operation", "warden_disabled")
            if self._idle_anchors_changed(run_id, guard):
                return ("operation", "anchor_changed")
            if first:
                refusal = self._idle_destruction_refusal(run, guard)
                if refusal is not None:
                    return ("operation", refusal)
            kind, reason = self._classify_registered_process(record)
        except Exception:
            return ("operation", "anchor_changed")
        if kind == "owned":
            return None
        if kind == "gone" and guard.get("allow_gone") is True:
            return ("target", "process_not_found")
        if first:
            return ("operation", reason or "process_identity_mismatch")
        if kind == "gone":
            return ("target", "process_not_found")
        if kind == "unknown":
            return ("target", reason or "process_identity_mismatch")
        return ("target", "process_identity_mismatch")

    def _idle_destruction_refusal(
        self, run: RunRecord, guard: Mapping[str, object]
    ) -> str | None:
        """None when the whole registered set still matches the warden's authorization.

        Any foreign, unknown, or (when closing) gone process refuses the
        operation. Callers must not then act on the processes that still match.
        """

        allow_gone = guard.get("allow_gone") is True
        expected = guard.get("expected")
        if not isinstance(expected, tuple):
            return "process_changed"
        buckets, reason = self._partition_registered_processes(run.processes)
        if buckets["unknown"]:
            return reason or "process_identity_mismatch"
        if buckets["foreign"]:
            return "process_identity_mismatch"
        if buckets["gone"] and not allow_gone:
            return "process_changed"
        identity = lambda record: (
            record.pid,
            record.creation_time_utc,
            record.executable_sha256,
            record.command_line_sha256,
            record.role,
        )
        if not allow_gone:
            if not buckets["owned"]:
                return "process_changed"
            current = tuple(sorted(identity(record) for record in run.processes))
            if current != expected:
                return "process_changed"
            return None
        owned = tuple(
            sorted(
                identity(record)
                for record in run.processes
                if self._classify_registered_process(record)[0] == "owned"
            )
        )
        if not set(owned) <= set(expected):
            return "process_changed"
        return None

    def _idle_anchors_changed(self, run_id: str, guard: Mapping[str, object]) -> bool:
        """True when human input or a launcher ticket moved since the warden decided.

        The queue is stamped here, in memory, so a ticket does not depend on
        some other caller reading the box. Doubt is a change: do not close.
        """

        wall = guard.get("wall")
        when = (
            float(wall)
            if isinstance(wall, (int, float)) and not isinstance(wall, bool)
            else None
        )
        try:
            self.note_queued_launcher_requests(when=when)
        except Exception:
            return True
        if "human_at" in guard and self.human_input_at(run_id) != guard.get("human_at"):
            return True
        if (
            "launcher_at" in guard
            and self.launcher_request_at(run_id) != guard.get("launcher_at")
        ):
            return True
        return False

    def _close_with_idle_guard(
        self, run: RunRecord, run_id: str, guard: Mapping[str, object]
    ) -> dict[str, object]:
        """Post WM_CLOSE only when the boundary check still allows that window.

        Windows are listed first. The binding check runs inside
        ``post_wm_close``, after that window's PID read and immediately
        before PostMessageW.
        """

        fns = getattr(self, "window_fns", None)
        listed: list[tuple[ProcessRecord, list[tuple[int, int]]]] = []
        for record in run.processes:
            listed.append(
                (
                    record,
                    window_close.list_visible_top_level_windows(
                        (record.pid,), fns=fns
                    ),
                )
            )
        refusal = self._idle_destruction_refusal(run, guard)
        if refusal is not None:
            return {"error": refusal, "run_id": run_id}
        if self._idle_anchors_changed(run_id, guard):
            return {"error": "anchor_changed", "run_id": run_id}
        if not self._idle_switch_on(guard):
            return {"error": "warden_disabled", "run_id": run_id}
        rank = {"client": 0, "server": 1}
        ordered = [
            item
            for _index, item in sorted(
                enumerate(listed),
                key=lambda item: (rank.get(item[1][0].role, 2), item[0]),
            )
        ]
        role_rows: dict[str, dict[str, object]] = {}
        acted_hwnds: list[int] = []
        acted_pids: list[int] = []
        untouched_hwnds: list[int] = []
        untouched_pids: list[int] = []
        untouched_reasons: list[str] = []
        stop_rest = False
        stop_reason = "anchor_changed"

        def _remember_untouched(record: ProcessRecord, hwnd: int, reason: str) -> None:
            untouched_hwnds.append(int(hwnd))
            if record.pid not in untouched_pids:
                untouched_pids.append(record.pid)
            label = f"{record.pid}:{reason}"
            if label not in untouched_reasons:
                untouched_reasons.append(label)

        for record, windows in ordered:
            windows_found = len(windows)
            windows_posted = 0
            if stop_rest:
                for hwnd, _window_pid in windows:
                    _remember_untouched(record, hwnd, stop_reason)
                continue
            for hwnd, window_pid in windows:
                if stop_rest:
                    _remember_untouched(record, hwnd, stop_reason)
                    continue
                decision: dict[str, tuple[str, str] | None] = {"boundary": None}

                def before_post(
                    record: ProcessRecord = record,
                    decision: dict[str, tuple[str, str] | None] = decision,
                ) -> bool:
                    boundary = self._idle_effect_boundary(
                        run,
                        run_id,
                        guard,
                        record,
                        first=not acted_hwnds,
                    )
                    decision["boundary"] = boundary
                    return boundary is None

                posted = window_close.post_wm_close(
                    hwnd,
                    fns=fns,
                    expected_pid=record.pid,
                    before_post=before_post,
                )
                _ = window_pid
                if posted:
                    windows_posted += 1
                    acted_hwnds.append(int(hwnd))
                    if record.pid not in acted_pids:
                        acted_pids.append(record.pid)
                    continue
                boundary = decision["boundary"]
                if boundary is None:
                    reason = "window_not_posted"
                    scope = "operation" if not acted_hwnds else "target"
                else:
                    scope, reason = boundary
                if not acted_hwnds:
                    return {"error": reason, "run_id": run_id}
                _remember_untouched(record, hwnd, reason)
                if scope == "operation":
                    stop_rest = True
                    stop_reason = reason
            existing = role_rows.get(record.role)
            if existing is None:
                role_rows[record.role] = {
                    "pid": record.pid,
                    "windows_found": windows_found,
                    "windows_posted": windows_posted,
                }
            else:
                existing["windows_found"] = int(existing["windows_found"]) + windows_found
                existing["windows_posted"] = (
                    int(existing["windows_posted"]) + windows_posted
                )
        if acted_hwnds and untouched_hwnds:
            return {
                "error": "partial_close",
                "outcome": "partial_close",
                "run_id": run_id,
                "acted_pids": list(acted_pids),
                "untouched_pids": list(untouched_pids),
                "acted_hwnds": list(acted_hwnds),
                "untouched_hwnds": list(untouched_hwnds),
                "untouched_reasons": list(untouched_reasons),
            }
        result: dict[str, object] = {"run_id": run_id}
        result.update(role_rows)
        return result

    def _client_is_launcher(self, client: ClientIdentity, run_id: str) -> bool:
        # Full session id, not the public prefix and not the pid. Rights last
        # as long as that MCP client: server_reload keeps the session id, a
        # client reopen and a daemon restart do not.
        with self._activity_lock:
            identity = self._launched_by.get(self._activity_key(run_id))
        return (
            identity is not None
            and isinstance(client.session_id, str)
            and identity.session_id == client.session_id
        )

    def _adoption_protection(
        self, client: ClientIdentity, run: RunRecord
    ) -> dict[str, object] | None:
        """None allows the adopt. Only RUNNING_IDLE is passed in.

        The launcher skips the box read, so a classification failure cannot
        lock them out. Everyone else may adopt only use_state abandoned.
        Caller holds _operation_lock. box_occupancy takes _activity_lock and
        drops it before ServerState._lock; that order is the allowed one.
        The sampler can still write a sample after this read.
        _revalidate_abandoned repeats the classification before the owner
        is assigned.
        """

        if self._client_is_launcher(client, run.run_id):
            return None
        try:
            box = self.box_occupancy()
        except Exception:
            return protection_fields(None)
        runs = box.get("runs") if isinstance(box, dict) else None
        row: dict[str, object] | None = None
        if isinstance(runs, list):
            for item in runs:
                if isinstance(item, dict) and item.get("run_id") == run.run_id:
                    row = item
                    break
        if row is None or row.get("use_state") != "abandoned":
            return protection_fields(row)
        return None

    def _use_clocks_locked(
        self,
    ) -> tuple[
        str,
        Mapping[str, float],
        frozenset[str],
        frozenset[str],
        Mapping[str, float],
        Mapping[str, float],
        Mapping[str, float],
        float | None,
        Mapping[str, Mapping[str, object]],
        float | None,
        float | None,
        Mapping[str, float],
        frozenset[str],
        Mapping[str, float],
    ]:
        """Copy the use clocks. Caller holds _activity_lock."""

        generation = (
            self.daemon_generation
            if isinstance(self.daemon_generation, str)
            else ""
        )
        activity = {
            run_id: stamp
            for (gen, run_id), stamp in self._last_activity.items()
            if gen == generation
            and stamp
            > self._activity_tombstone.get((gen, run_id), stamp - 1.0)
        }
        unknown = frozenset(
            run_id
            for (gen, run_id) in self._activity_unknown
            if gen == generation
        )
        compensating = frozenset(
            run_id
            for (gen, run_id) in self._compensating_runs
            if gen == generation
        )
        ownerless = {
            run_id: stamp
            for (gen, run_id), stamp in self._ownerless_since.items()
            if gen == generation
        }
        human = {
            run_id: stamp
            for (gen, run_id), stamp in self._human_input_at.items()
            if gen == generation
        }
        uncertain = {
            run_id: stamp
            for (gen, run_id), stamp in self._uncertain_input_at.items()
            if gen == generation
        }
        launched = {
            run_id: MappingProxyType(identity.public_payload())
            for (gen, run_id), identity in self._launched_by.items()
            if gen == generation
        }
        launcher_request = {
            run_id: stamp
            for (gen, run_id), stamp in self._launcher_request_at.items()
            if gen == generation
        }
        closing = frozenset(
            run_id for (gen, run_id) in self._closing_runs if gen == generation
        )
        warning_blocked = {
            run_id: stamp
            for (gen, run_id), stamp in self._warning_blocked_until.items()
            if gen == generation
        }
        return (
            generation,
            MappingProxyType(activity),
            unknown,
            compensating,
            MappingProxyType(ownerless),
            MappingProxyType(human),
            MappingProxyType(uncertain),
            self._signal_recovered_at,
            MappingProxyType(launched),
            self._input_good_at,
            self._use_clock_origin,
            MappingProxyType(launcher_request),
            closing,
            MappingProxyType(warning_blocked),
        )

    def _snapshot_from_clocks(
        self,
        clock: float,
        runs: tuple[RunRecord, ...],
        revision: int,
        bound: frozenset[str],
        clocks: tuple[
            str,
            Mapping[str, float],
            frozenset[str],
            frozenset[str],
            Mapping[str, float],
            Mapping[str, float],
            Mapping[str, float],
            float | None,
            Mapping[str, Mapping[str, object]],
            float | None,
            float | None,
            Mapping[str, float],
            frozenset[str],
            Mapping[str, float],
        ],
    ) -> _BoxSnapshot:
        (
            generation,
            activity,
            unknown,
            compensating,
            ownerless,
            human,
            uncertain,
            recovered_at,
            launched,
            input_good_at,
            origin,
            launcher_request,
            closing,
            warning_blocked,
        ) = clocks
        return _BoxSnapshot(
            clock=clock,
            runs=runs,
            activity=activity,
            unknown=unknown,
            revision=revision,
            daemon_generation=generation,
            compensating=compensating,
            ownerless_since=ownerless,
            human_input=human,
            uncertain_input=uncertain,
            launched_by=launched,
            input_good_at=input_good_at,
            signal_recovered_at=recovered_at,
            use_clock_origin=origin,
            bound_runs=bound,
            launcher_request=launcher_request,
            closing_runs=closing,
            warning_blocked=warning_blocked,
        )

    def _adoption_marker_path(self) -> Path | None:
        paths = getattr(self.manifest, "paths", None)
        runs_path = getattr(paths, "runs_path", None)
        if isinstance(runs_path, (str, Path)):
            return Path(runs_path).with_name("adoption-unconfirmed.json")
        return None

    def _adoption_settled_path(self) -> Path | None:
        marker = self._adoption_marker_path()
        if marker is None:
            return None
        return marker.with_name("adoption-unconfirmed-settled.json")

    def _read_adoption_marker(self) -> dict[str, object] | None:
        path = self._adoption_marker_path()
        if path is None:
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return None
        if not isinstance(document, dict) or document.get("version") != 1:
            return None
        run_id = document.get("run_id")
        owner = document.get("owner_session_id")
        lease = document.get("owner_lease_id")
        token = document.get("token")
        invalidated = document.get("invalidated")
        kind = document.get("invalidated_by")
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(owner, str)
            or not owner
            or not isinstance(lease, str)
            or not lease
            or isinstance(token, bool)
            or not isinstance(token, int)
            or not isinstance(invalidated, bool)
            or (kind is not None and kind not in {"human", "uncertain"})
        ):
            return None
        return document

    def _write_adoption_marker(self, document: dict[str, object]) -> None:
        path = self._adoption_marker_path()
        if path is None:
            raise RuntimeError("adoption_marker_unavailable")
        atomic_write_json(path, document)

    def _delete_adoption_marker(self) -> None:
        path = self._adoption_marker_path()
        if path is None:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def _note_pending_adoption_locked(
        self, run_id: str, owner: str, lease_id: str
    ) -> int:
        """Caller holds _activity_lock. Fsync the marker, then remember the token.

        The marker names the owner this adopt is about to write. Startup
        reverts a run that still carries that owner. The fsync is the extra
        hold: no manifest lock and no ServerState._lock.
        """

        self._adoption_serial += 1
        token = self._adoption_serial
        self._write_adoption_marker(
            {
                "version": 1,
                "run_id": run_id,
                "owner_session_id": owner,
                "owner_lease_id": lease_id,
                "token": token,
                "invalidated": False,
                "invalidated_by": None,
            }
        )
        key = self._activity_key(run_id)
        self._pending_adoption[key] = token
        self._invalidated_adoption.pop(key, None)
        return token

    def _invalidate_pending_adoption_locked(self, run_id: str, kind: str) -> None:
        """Caller holds _activity_lock. Human or uncertain input closes the adopt."""

        if kind not in {"human", "uncertain"}:
            return
        key = self._activity_key(run_id)
        token = self._pending_adoption.get(key)
        if token is None:
            return
        current = self._invalidated_adoption.get(key)
        if current is not None and current[0] == token:
            return
        self._invalidated_adoption[key] = (token, kind)
        try:
            document = self._read_adoption_marker()
            if (
                document is None
                or document.get("run_id") != run_id
                or document.get("token") != token
                or document.get("invalidated") is True
            ):
                return
            document["invalidated"] = True
            document["invalidated_by"] = kind
            self._write_adoption_marker(document)
        except Exception:
            return

    def _pending_adoption_invalidated_locked(
        self, run_id: str, token: int
    ) -> str | None:
        """Caller holds _activity_lock. The input kind, or None if this token is clean."""

        found = self._invalidated_adoption.get(self._activity_key(run_id))
        if found is None or found[0] != token:
            return None
        return found[1]

    def _consume_pending_adoption_locked(self, run_id: str, token: int) -> None:
        key = self._activity_key(run_id)
        if self._pending_adoption.get(key) == token:
            self._pending_adoption.pop(key, None)
        found = self._invalidated_adoption.get(key)
        if found is not None and found[0] == token:
            self._invalidated_adoption.pop(key, None)

    def _drop_open_adoption(self) -> None:
        open_adoption = self._open_adoption
        self._open_adoption = None
        if open_adoption is None:
            return
        run_id, token = open_adoption
        with self._activity_lock:
            self._consume_pending_adoption_locked(run_id, token)
        try:
            self._delete_adoption_marker()
        except OSError:
            return

    def _settle_adoption_marker(self, *, reverted: bool, kind: str | None) -> None:
        document = self._read_adoption_marker()
        if document is None:
            return
        if kind in {"human", "uncertain"}:
            document["invalidated"] = True
            document["invalidated_by"] = kind
        settled_path = self._adoption_settled_path()
        if settled_path is None:
            return
        atomic_write_json(
            settled_path,
            {
                "version": 1,
                "settled": True,
                "run_id": document["run_id"],
                "owner_session_id": document["owner_session_id"],
                "owner_lease_id": document["owner_lease_id"],
                "token": document["token"],
                "invalidated": document["invalidated"] is True,
                "invalidated_by": document.get("invalidated_by"),
                "reverted": reverted,
            },
        )
        self._delete_adoption_marker()

    def _recover_unconfirmed_adoption(self) -> None:
        """A crash after the owner replace and before revert must not keep that owner.

        The marker is the trace: it names the owner and whether human or
        uncertain input invalidated the adopt. The run goes back to ownerless
        RUNNING_IDLE. The settled file keeps that fact after the marker is removed.
        An unreadable marker is left in place.
        """

        path = self._adoption_marker_path()
        if path is None or not path.exists():
            return
        document = self._read_adoption_marker()
        if document is None:
            return
        run = self.manifest.get(str(document["run_id"]))
        reverted = False
        if (
            run is not None
            and run.state == "RUNNING"
            and run.owner_session_id == document["owner_session_id"]
            and run.owner_lease_id == document["owner_lease_id"]
        ):
            run.owner_session_id = None
            run.owner_lease_id = None
            run.state = "RUNNING_IDLE"
            self.manifest.replace(run)
            reverted = True
        kind = document.get("invalidated_by")
        self._settle_adoption_marker(
            reverted=reverted,
            kind=kind if isinstance(kind, str) else None,
        )

    def _publish_revert_view_locked(self) -> None:
        """Caller holds _activity_lock. Publish a lock-free snapshot for enqueue."""

        generation = (
            self.daemon_generation if isinstance(self.daemon_generation, str) else ""
        )
        run_ids: set[str] = set()
        launchers: dict[str, str] = {}
        for (gen, run_id), _pending in self._adoption_revert_pending.items():
            if gen != generation:
                continue
            run_ids.add(run_id)
            identity = self._launched_by.get((gen, run_id))
            if identity is not None and isinstance(identity.session_id, str) and identity.session_id:
                launchers[run_id] = identity.session_id
        self._revert_pending_view = (frozenset(run_ids), MappingProxyType(launchers))

    def _adoption_revert_is_pending(self, run_id: str) -> bool:
        with self._activity_lock:
            return self._activity_key(run_id) in self._adoption_revert_pending

    def _clear_revert_pending(self, key: tuple[str, str], token: int) -> None:
        with self._activity_lock:
            current = self._adoption_revert_pending.get(key)
            if current is None or current[0] != token:
                return
            self._adoption_revert_pending.pop(key, None)
            self._publish_revert_view_locked()

    def _retry_adoption_reverts(self) -> None:
        """Caller holds _operation_lock. Leave a run pending when the write fails."""

        with self._activity_lock:
            pending = list(self._adoption_revert_pending.items())
        for key, (token, kind) in pending:
            try:
                run = self.manifest.get(key[1])
            except Exception:
                continue
            if run is None or run.state != "RUNNING" or not run.owner_session_id:
                if run is not None and run.owner_session_id is None:
                    try:
                        self._settle_adoption_marker(reverted=True, kind=kind)
                    except Exception:
                        pass
                self._clear_revert_pending(key, token)
                continue
            run.owner_session_id = None
            run.owner_lease_id = None
            run.state = "RUNNING_IDLE"
            try:
                RunManifestStore.replace(self.manifest, run)
            except Exception:
                continue
            try:
                self._settle_adoption_marker(reverted=True, kind=kind)
            except Exception:
                pass
            self._clear_revert_pending(key, token)
            self._invalidate_box_cache()

    def _refuse_unconfirmed_owner(
        self, client: ClientIdentity, run_id: str
    ) -> dict[str, object] | None:
        """None lets the caller continue. Caller holds _operation_lock.

        Retries the durable revert first. The launcher is not refused while
        the write is still pending; every other session is.
        """

        if not self._adoption_revert_is_pending(run_id):
            return None
        self._retry_adoption_reverts()
        if not self._adoption_revert_is_pending(run_id):
            return None
        if self._client_is_launcher(client, run_id):
            return None
        return adoption_revert_pending_fields()

    def _close_adoption_after_replace(
        self, client: ClientIdentity, run: RunRecord
    ) -> dict[str, object] | None:
        """None keeps the owner. A dict refuses with run_protected fields.

        Caller holds _operation_lock. The owner replace has already returned.
        """

        open_adoption = self._open_adoption
        if open_adoption is None:
            return None
        run_id, token = open_adoption
        with self._activity_lock:
            kind = self._pending_adoption_invalidated_locked(run_id, token)
            self._consume_pending_adoption_locked(run_id, token)
            if kind is None:
                try:
                    self._delete_adoption_marker()
                except OSError:
                    kind = "uncertain"
        self._open_adoption = None
        if kind is None:
            return None
        run.owner_session_id = None
        run.owner_lease_id = None
        run.state = "RUNNING_IDLE"
        # The owner commit is the instance replace. A probe wrapped around
        # that call runs before it returns; the revert must not re-enter it.
        try:
            RunManifestStore.replace(self.manifest, run)
        except Exception:
            # The store rolled back to the stranger. Remember the token and
            # refuse that ownership until a later write lands.
            with self._activity_lock:
                self._adoption_revert_pending[self._activity_key(run_id)] = (
                    token,
                    kind,
                )
                self._publish_revert_view_locked()
            return adoption_revert_pending_fields()
        try:
            self._settle_adoption_marker(reverted=True, kind=kind)
        except Exception:
            pass
        try:
            refusal = self._adoption_protection(client, run)
        except Exception:
            refusal = None
        if refusal is None:
            return protection_fields(None)
        return refusal

    def _revalidate_abandoned(
        self, client: ClientIdentity, run: RunRecord, lease_id: str
    ) -> dict[str, object] | None:
        """None allows the adopt. A dict is the run_protected body.

        Caller holds _operation_lock. The launcher returns immediately.
        Everyone else is classified again after probes return. Under
        _activity_lock the clocks are copied and, when the run is still
        abandoned, a pending adoption is registered and its marker is
        fsynced before the owner fields are set on this clone. The lock
        drops before manifest.replace. record_input_sample invalidates
        that token if it records human or uncertain input for the run.
        adopt_run then reverts the durable owner. Probes and
        _adopt_dispatchable stay outside the lock. Anything that cannot
        be classified is run_protected.
        """

        self._open_adoption = None
        if self._client_is_launcher(client, run.run_id):
            return None
        try:
            probe_snapshot = self._take_box_snapshot(time.time())
            probes = self._probes_for_snapshot(probe_snapshot, use_cache=False)
            runs = tuple(self.manifest.list_runs())
            bound = frozenset(
                item.run_id
                for item in runs
                if item.state in _ACTIVE_STATES
                and self._adopt_dispatchable(item.run_id)
            )
            with self._activity_lock:
                snapshot = self._snapshot_from_clocks(
                    time.time(),
                    runs,
                    self._box_revision,
                    bound,
                    self._use_clocks_locked(),
                )
                active_count = sum(
                    1 for item in runs if item.state in _ACTIVE_STATES
                )
                projected = _use_projection(run, snapshot, probes, active_count)
                if projected.get("use_state") != "abandoned":
                    return protection_fields(projected)
                token = self._note_pending_adoption_locked(
                    run.run_id, client.session_id, lease_id
                )
                self._open_adoption = (run.run_id, token)
                run.owner_session_id = client.session_id
                run.owner_lease_id = lease_id
                run.state = "RUNNING"
                return None
        except Exception:
            self._drop_open_adoption()
            return protection_fields(None)

    def adopt_run(self, client: ClientIdentity, token: str | None, run_id: object) -> dict[str, object]:
        if isinstance(run_id, str) and run_id:
            self.note_launcher_request(client, run_id)
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        command = "lifecycle_adopt"
        decision, error = self._authorize(client, token, command)
        if error is not None:
            return error
        authority = self._authority(decision)
        if authority is None:
            return self._error("lease_required", 403)
        committed = False
        try:
            with self._operation_lock:
                if not self._reservation_active(authority, command):
                    return self._error("lease_invalid", 409)
                if self._quarantined():
                    return self._reject_reserved(authority, command, "retail_quarantine")
                if not isinstance(run_id, str) or not run_id:
                    return self._reject_reserved(authority, command, "run_not_found", 404)
                run = self.manifest.get(run_id)
                if run is None:
                    return self._reject_reserved(authority, command, "run_not_found", 404)
                refusal = self._refuse_unconfirmed_owner(client, run_id)
                if refusal is not None:
                    result = self._reject_reserved(
                        authority, command, "run_protected"
                    )
                    result.update(refusal)
                    return result
                run = self.manifest.get(run_id)
                if run is None:
                    return self._reject_reserved(authority, command, "run_not_found", 404)
                if (
                    run.state == "RUNNING"
                    and run.owner_session_id == client.session_id
                    and run.owner_lease_id == authority[1]
                ):
                    # P-J2: same owner + same lease is a no-op success. dayz_test_worker
                    # adopts explicitly after the grant already adopted.
                    if not self._audit(
                        "lifecycle_adopt", client, "identity_match", "allowed", run_id=run_id
                    ):
                        self.coordinator.reject_reservation(
                            authority[0], authority[1], authority[2], "audit_failed"
                        )
                        return self._error("audit_failed", 503)
                    command_id = self._commit_reserved(authority, command)
                    if command_id is None:
                        return self._error("lease_invalid", 409)
                    committed = True
                    self._finish_committed(authority, command_id)
                    dispatchable = self._adopt_dispatchable(run_id)
                    payload: dict[str, object] = {
                        "ok": True,
                        "run_id": run_id,
                        "state": "RUNNING",
                        "dispatchable": dispatchable,
                    }
                    if not dispatchable:
                        payload["hint"] = _ADOPT_NOT_DISPATCHABLE_HINT
                    return payload
                if (
                    run.state not in _ADOPTABLE_STATES
                    or run.owner_session_id
                ):
                    return self._reject_reserved(authority, command, "run_not_adoptable")
                if any(
                    other.state in _ACTIVE_STATES and other.run_id != run_id
                    for other in self.manifest.list_runs()
                ):
                    return self._reject_reserved(authority, command, "active_run_exists")
                if run.state == "RUNNING_IDLE":
                    refusal = self._adoption_protection(client, run)
                    if refusal is not None:
                        result = self._reject_reserved(
                            authority, command, "run_protected"
                        )
                        result.update(refusal)
                        return result
                buckets, unknown_reason = self._partition_registered_processes(run.processes)
                if buckets["unknown"]:
                    reason = unknown_reason or "process_identity_mismatch"
                    return self._reject_reserved(
                        authority,
                        command,
                        reason,
                        503 if reason == "guard_unavailable" else 409,
                    )
                if not buckets["owned"]:
                    result = self._reject_reserved(
                        authority, command, "run_processes_gone"
                    )
                    result["hint"] = _RUN_PROCESSES_GONE_HINT
                    return result
                if self._quarantined():
                    return self._reject_reserved(authority, command, "retail_quarantine")
                adopt_reason = (
                    "unreconciled_adopt"
                    if run.state == "UNRECONCILED"
                    else "identity_match"
                )
                if not self._audit("lifecycle_adopt", client, adopt_reason, "allowed", run_id=run_id):
                    self.coordinator.reject_reservation(
                        authority[0], authority[1], authority[2], "audit_failed"
                    )
                    return self._error("audit_failed", 503)
                if self._quarantined():
                    return self._reject_reserved(authority, command, "retail_quarantine")
                if run.state == "RUNNING_IDLE":
                    refusal = self._revalidate_abandoned(
                        client, run, authority[1]
                    )
                    if refusal is not None:
                        result = self._reject_reserved(
                            authority, command, "run_protected"
                        )
                        result.update(refusal)
                        return result
                command_id = self._commit_reserved(authority, command)
                if command_id is None:
                    self._drop_open_adoption()
                    return self._error("lease_invalid", 409)
                committed = True
                run.owner_session_id = client.session_id
                run.owner_lease_id = authority[1]
                run.state = "RUNNING"
                try:
                    self.manifest.replace(run)
                except Exception:
                    self._drop_open_adoption()
                    self._finish_committed(authority, command_id)
                    return self._error("manifest_failed", 503)
                if self._open_adoption is not None:
                    refusal = self._close_adoption_after_replace(client, run)
                    if refusal is not None:
                        self._invalidate_box_cache()
                        self._finish_committed(authority, command_id)
                        result = self._error("run_protected", 409)
                        result.update(refusal)
                        return result
                self._invalidate_box_cache()
                self._unfence_runs([run_id])
                self._finish_committed(authority, command_id)
                dispatchable = self._adopt_dispatchable(run_id)
                payload = {
                    "ok": True,
                    "run_id": run_id,
                    "state": "RUNNING",
                    "dispatchable": dispatchable,
                }
                if not dispatchable:
                    payload["hint"] = _ADOPT_NOT_DISPATCHABLE_HINT
                return payload
        except Exception:
            # Unexpected exit after _authorize opened a reservation and before
            # commit: abort (not reject). abort_reservation is the coordinator
            # contract for an operation that could not complete; reject is the
            # fallback close. Compensator degradations are not swallowed (F-04);
            # same collection pattern as stop_run's manifest.replace failure.
            result = self._error("adopt_failed", 503)
            if not committed:
                degraded: list[str] = []
                try:
                    degraded.extend(
                        self.coordinator.abort_reservation(
                            authority[0],
                            authority[1],
                            authority[2],
                            "adopt_failed",
                        )
                    )
                except Exception:
                    try:
                        rejected = self.coordinator.reject_reservation(
                            authority[0],
                            authority[1],
                            authority[2],
                            "adopt_failed",
                        )
                        extra = getattr(rejected, "cleanup_degraded", ())
                        if extra:
                            degraded.extend(extra)
                    except Exception:
                        degraded.append("reservation_abort_failed")
                if degraded:
                    result["cleanup_degraded"] = list(dict.fromkeys(degraded))
            return result

    def release_owner(self, session_id: str, lease_id: str) -> list[str]:
        with self._operation_lock:
            self._require_legacy_identity_safe()
            changed = self._quiesce_then_release_owner(session_id, lease_id)
            if changed:
                self._invalidate_box_cache()
            return changed

    def begin_release_owner(
        self, session_id: str, lease_id: str
    ) -> CleanupDisposition:
        terminal = threading.Event()
        result: dict[str, object] = {}
        with self._operation_lock:
            self._require_legacy_identity_safe()
            unacknowledged = [
                run
                for run in self.manifest.list_runs()
                if run.owner_session_id == session_id
                and run.owner_lease_id == lease_id
                and run.launch_operation_id is not None
                and not run.launch_acknowledged
                and run.state in _ACTIVE_STATES
            ]
            if not unacknowledged:
                released = self._quiesce_then_release_owner(session_id, lease_id)
                if released:
                    self._invalidate_box_cache()
                result.update(
                    {
                        "terminal_safe": True,
                        "runs_released": released,
                    }
                )
                terminal.set()
                return CleanupDisposition(False, terminal, result)

        def cleanup() -> None:
            released: list[str] = []
            try:
                with self._operation_lock:
                    for original in unacknowledged:
                        current = self.manifest.get(original.run_id)
                        if (
                            current is None
                            or current.owner_session_id != session_id
                            or current.owner_lease_id != lease_id
                            or current.launch_operation_id
                            != original.launch_operation_id
                            or current.launch_request_sha256
                            != original.launch_request_sha256
                            or current.launch_acknowledged
                        ):
                            self._arm_cleanup_fault(
                                current or original, "identity_ambiguous"
                            )
                            result.update(
                                {
                                    "terminal_safe": False,
                                    "error": "identity_ambiguous",
                                }
                            )
                            return
                        for record in current.processes:
                            try:
                                actual = self.guard.snapshot(record.pid)
                            except Exception:
                                actual = {"error": "guard_unavailable"}
                            if not self._identity_matches(record, actual):
                                self._persist_unreconciled_and_arm(
                                    current, "identity_ambiguous"
                                )
                                result.update(
                                    {
                                        "terminal_safe": False,
                                        "error": "identity_ambiguous",
                                    }
                                )
                                return
                            try:
                                terminated = self.guard.terminate(record)
                            except Exception:
                                terminated = {"terminated": False}
                            if (
                                not isinstance(terminated, dict)
                                or terminated.get("terminated") is not True
                            ):
                                self._persist_unreconciled_and_arm(
                                    current, "cleanup_failed"
                                )
                                result.update(
                                    {
                                        "terminal_safe": False,
                                        "error": "cleanup_failed",
                                    }
                                )
                                return
                        current.state = "EXITED"
                        current.owner_session_id = None
                        current.owner_lease_id = None
                        current.processes = []
                        if not self._commit_retirement(
                            current,
                            "lifecycle_owner_released",
                            "released",
                            "released",
                        ):
                            result.update(
                                {
                                    "terminal_safe": False,
                                    "error": "cleanup_failed",
                                }
                            )
                            return
                        released.append(current.run_id)
                    extra = self._quiesce_then_release_owner(session_id, lease_id)
                    if extra:
                        self._invalidate_box_cache()
                    released.extend(extra)
                    result.update(
                        {
                            "terminal_safe": True,
                            "runs_released": sorted(set(released)),
                        }
                    )
            except Exception:
                result.update(
                    {"terminal_safe": False, "error": "cleanup_failed"}
                )
            finally:
                terminal.set()

        threading.Thread(
            target=cleanup,
            name=f"dayz-mcp-run-cleanup-{lease_id[:12]}",
            daemon=True,
        ).start()
        return CleanupDisposition(True, terminal, result)

    def _persist_unreconciled_and_arm(self, current: RunRecord, reason: str) -> None:
        durable = RunRecord.from_payload(dataclasses.asdict(current))
        current.state = "UNRECONCILED"
        current.owner_session_id = None
        current.owner_lease_id = None
        self._retire_run_bindings(current.run_id, "released")
        try:
            self.manifest.replace(current)
        except Exception:
            self._arm_cleanup_fault(durable, reason)
            return
        self._invalidate_box_cache()
        self._arm_cleanup_fault(current, reason)

    def _arm_cleanup_fault(self, run: RunRecord, reason: str) -> None:
        callback = self.recovery_fault_arm
        if callback is None:
            return
        try:
            callback(RunRecord.from_payload(dataclasses.asdict(run)), reason)
        except Exception:
            return

    def repair_recovery_fault(
        self, fault: dict[str, object]
    ) -> dict[str, object]:
        if not isinstance(fault, dict) or fault.get("scope") != "run":
            return {"terminal_safe": False, "error": "manifest_repair_required"}
        run_id = fault.get("run_id")
        operation_id = fault.get("launch_operation_id")
        expected_hash = fault.get("run_record_sha256")
        with self._operation_lock:
            if not isinstance(run_id, str):
                return {"terminal_safe": False, "error": "identity_ambiguous"}
            run = self.manifest.get(run_id)
            if (
                run is None
                or run.launch_operation_id != operation_id
                or run.launch_acknowledged
                or run.state not in _RECOVERY_REPAIR_STATES
            ):
                return {"terminal_safe": False, "error": "identity_ambiguous"}
            observed_hash = hashlib.sha256(
                json.dumps(
                    dataclasses.asdict(run),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if observed_hash != expected_hash:
                return {"terminal_safe": False, "error": "manifest_drift"}
            if not self._audit(
                "lifecycle_recovery_repair",
                None,
                "confirmed_repair",
                "allowed",
                run_id=run_id,
            ):
                return {"terminal_safe": False, "error": "audit_failed"}
            for record in run.processes:
                try:
                    actual = self.guard.snapshot(record.pid)
                except Exception:
                    actual = {"error": "guard_unavailable", "exit_code": 3}
                if (
                    isinstance(actual, dict)
                    and actual.get("error") == "process_not_found"
                    and actual.get("exit_code") == 4
                ):
                    continue
                if not self._identity_matches(record, actual):
                    return {
                        "terminal_safe": False,
                        "error": "identity_ambiguous",
                    }
                try:
                    terminated = self.guard.terminate(record)
                except Exception:
                    terminated = {"terminated": False}
                if (
                    not isinstance(terminated, dict)
                    or terminated.get("terminated") is not True
                ):
                    return {"terminal_safe": False, "error": "cleanup_failed"}
            run.state = "EXITED"
            run.owner_session_id = None
            run.owner_lease_id = None
            run.processes = []
            if not self._commit_retirement(
                run,
                "lifecycle_recovery_repair",
                "confirmed_repair",
                "repaired",
            ):
                return {"terminal_safe": False, "error": "manifest_drift"}
            return {
                "terminal_safe": True,
                "run_id": run_id,
                "state": "EXITED",
                "manifest_sha256": hashlib.sha256(
                    self.manifest.paths.runs_path.read_bytes()
                ).hexdigest(),
            }

    def repair_manifest_recovery(self, raw: bytes) -> dict[str, object]:
        if not isinstance(raw, bytes):
            return {"terminal_safe": False, "error": "manifest_drift"}
        try:
            payload = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("version") != 1
                or not isinstance(payload.get("runs"), list)
            ):
                raise ValueError("invalid_run_manifest")
            parsed = [RunRecord.from_payload(item) for item in payload["runs"]]
            if len({run.run_id for run in parsed}) != len(parsed):
                raise ValueError("invalid_run_manifest")
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return {"terminal_safe": False, "error": "manifest_drift"}

        with self._operation_lock:
            try:
                atomic_write_bytes(self.manifest.paths.runs_path, raw)
                restored = RunManifestStore(
                    self.manifest.paths,
                    checkpoint=self.manifest._checkpoint,
                )
            except Exception:
                return {"terminal_safe": False, "error": "manifest_drift"}
            self.manifest = restored
            self._invalidate_box_cache()
            for run in restored.list_runs():
                if (
                    run.launch_operation_id is None
                    or run.launch_acknowledged
                    or run.state == "EXITED"
                ):
                    continue
                if self._quarantined():
                    return self._manifest_repair_failure("identity_ambiguous")
                if not self._audit(
                    "lifecycle_manifest_recovery",
                    None,
                    "backup_restored",
                    "reconcile_unacknowledged",
                    run_id=run.run_id,
                ):
                    return self._manifest_repair_failure("cleanup_failed")
                for record in run.processes:
                    try:
                        actual = self.guard.snapshot(record.pid)
                    except Exception:
                        actual = {"error": "guard_unavailable", "exit_code": 3}
                    if (
                        isinstance(actual, dict)
                        and actual.get("error") == "process_not_found"
                        and actual.get("exit_code") == 4
                    ):
                        continue
                    if not self._identity_matches(record, actual):
                        return self._manifest_repair_failure(
                            "identity_ambiguous"
                        )
                    try:
                        terminated = self.guard.terminate(record)
                    except Exception:
                        terminated = {"terminated": False}
                    if (
                        not isinstance(terminated, dict)
                        or terminated.get("terminated") is not True
                    ):
                        return self._manifest_repair_failure("cleanup_failed")
                run.state = "EXITED"
                run.owner_session_id = None
                run.owner_lease_id = None
                run.processes = []
                if not self._commit_retirement(
                    run,
                    "lifecycle_manifest_recovery",
                    "backup_restored",
                    "repaired",
                ):
                    return self._manifest_repair_failure("manifest_drift")
            try:
                candidates = [
                    run.run_id
                    for run in restored.list_runs()
                    if run.state == "RUNNING" and run.owner_session_id is not None
                ]
                self._transition_to_idle(
                    candidates, restored.recover_after_restart
                )
            except Exception:
                return self._manifest_repair_failure("manifest_drift")
            return {
                "terminal_safe": True,
                "manifest_sha256": hashlib.sha256(
                    restored.paths.runs_path.read_bytes()
                ).hexdigest(),
            }

    def _manifest_repair_failure(self, error: str) -> dict[str, object]:
        try:
            manifest_sha256 = hashlib.sha256(
                self.manifest.paths.runs_path.read_bytes()
            ).hexdigest()
        except Exception:
            manifest_sha256 = None
        result: dict[str, object] = {
            "terminal_safe": False,
            "error": error,
        }
        if manifest_sha256 is not None:
            result["manifest_sha256"] = manifest_sha256
        return result

    def _run_all_dead(self, run: RunRecord) -> tuple[bool, str]:
        """True if every registered process is gone or foreign and the diag
        snapshot is known-clean. A still-owned survivor, an unknown snapshot,
        or an unexpected DayZ returns (False, reason). Identity mismatch is
        treated as foreign (not ours) and does not block reap. Never a basis
        to terminate."""
        buckets, unknown_reason = self._partition_registered_processes(run.processes)
        if buckets["owned"]:
            return False, "process_alive"
        if buckets["unknown"]:
            return False, unknown_reason or "process_identity_mismatch"
        diag_ok, diag_reason = self._diag_snapshot_registered(set())
        if not diag_ok:
            return False, diag_reason
        return True, ""

    def _reap_run_locked(self, run: RunRecord, *, client: ClientIdentity | None) -> str:
        """Audit-before-act retire of a confirmed-dead run to EXITED. Caller holds
        _operation_lock and has already proven _run_all_dead. Terminates nothing â€”
        there is no live process by construction. Returns "" on success, or the exact
        failure cause ("audit_failed" / "manifest_failed") so the agent path reports the
        real reason instead of conflating a disk failure with an audit failure. On a
        persist failure the manifest rolls back in-memory; the next pass converges."""
        buckets, _unknown_reason = self._partition_registered_processes(run.processes)
        idle_decision = self._peek_idle_retirement(run.run_id)
        if idle_decision:
            event = "idle_timeout"
            reason = "idle_timeout"
            decision = idle_decision
        else:
            event = "run_reaped"
            reason = "all_processes_gone_or_foreign"
            decision = "reaped"
        if not self._audit(
            event,
            client,
            reason,
            decision,
            run_id=run.run_id,
            dead_pids=[record.pid for record in run.processes],
            owned_pids=list(buckets["owned"]),
            gone_pids=list(buckets["gone"]),
            foreign_pids=list(buckets["foreign"]),
        ):
            return "audit_failed"
        owner_session_id = run.owner_session_id
        owner_lease_id = run.owner_lease_id
        reaped_records = tuple(run.processes)
        run.owner_session_id = None
        run.owner_lease_id = None
        run.processes = []
        run.state = "EXITED"
        if not self._commit_retirement(run, event, reason, decision):
            return "manifest_failed"
        self._take_idle_retirement(run.run_id)
        self._remember_reaped_owner(
            run.run_id, owner_session_id, owner_lease_id, reaped_records
        )
        self.coordinator.note_run_reaped(owner_session_id)
        return ""

    def _remember_reaped_owner(
        self,
        run_id: str,
        session_id: str | None,
        lease_id: str | None,
        records: tuple[ProcessRecord, ...],
    ) -> None:
        """fb-20260904-200821-dae1 part 1: the owner a committed reap cleared."""
        if not isinstance(session_id, str) or not isinstance(lease_id, str):
            return
        key = self._activity_key(run_id)
        with self._activity_lock:
            self._reaped_owners.pop(key, None)
            self._reaped_owners[key] = (session_id, lease_id, records)
            while len(self._reaped_owners) > _REAPED_OWNER_MEMORY:
                del self._reaped_owners[next(iter(self._reaped_owners))]

    def _reap_dead_runs_locked(self) -> list[str]:
        """Caller holds _operation_lock. Safe under retail quarantine: never
        terminates, only retires runs whose owned processes are already gone."""
        reaped: list[str] = []
        for run in self.manifest.list_runs():
            if run.state not in _REAPABLE_STATES:
                continue
            all_dead, _reason = self._run_all_dead(run)
            if not all_dead:
                continue
            if not self._reap_run_locked(run, client=None):
                reaped.append(run.run_id)
        return reaped

    def reap_dead_runs(self) -> list[str]:
        """Daemon housekeeping: retire every reapable run whose processes are all
        gone or foreign, so a crashed DayZ never permanently blocks the box (the run
        would otherwise linger active and fail every start/adopt/stop until a manual
        admin reconcile). Safe by construction â€” reaps only zero-owned-process runs
        and never calls terminate. Runs under retail quarantine; that pass is
        audited so a later ghost can be told apart from a skipped reaper."""
        with self._operation_lock:
            self._require_legacy_identity_safe()
            self._retry_adoption_reverts()
            if self._quarantined():
                if not self._audit(
                    "reap_under_quarantine",
                    None,
                    "retail_quarantine",
                    "continued",
                ):
                    self._note_audit_row_dropped()
            return self._reap_dead_runs_locked()

    def reap_dead_run(
        self, client: ClientIdentity, token: str | None, run_id: object
    ) -> dict[str, object]:
        """Agent-callable, non-TTY recovery restricted to the all-dead-or-foreign
        case. Lease-gated like other lifecycle ops. Rejects (run_not_reapable)
        any run with a still-owned process, an unavailable guard or an ambiguous
        diag â€” those keep the TTY-gated admin_reconcile path. Terminates nothing."""
        if isinstance(run_id, str) and run_id:
            self.note_launcher_request(client, run_id)
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        command = "lifecycle_reap"
        decision, error = self._authorize(client, token, command)
        if error is not None:
            return error
        authority = self._authority(decision)
        if authority is None:
            return self._error("lease_required", 403)
        with self._operation_lock:
            if not self._reservation_active(authority, command):
                return self._error("lease_invalid", 409)
            if self._quarantined():
                return self._reject_reserved(authority, command, "retail_quarantine")
            if not isinstance(run_id, str) or not run_id:
                return self._reject_reserved(authority, command, "run_not_found", 404)
            run = self.manifest.get(run_id)
            if run is None:
                return self._reject_reserved(authority, command, "run_not_found", 404)
            if run.state not in _REAPABLE_STATES:
                return self._reject_reserved(authority, command, "run_not_reapable")
            all_dead, _reason = self._run_all_dead(run)
            if not all_dead:
                return self._reject_reserved(authority, command, "run_not_reapable")
            command_id = self._commit_reserved(authority, command)
            if command_id is None:
                return self._error("lease_invalid", 409)
            try:
                outcome = self._reap_run_locked(run, client=client)
                if outcome:
                    return self._error(outcome, 503)
                return {"ok": True, "run_id": run_id, "state": "EXITED"}
            finally:
                self._finish_committed(authority, command_id)

    def retired_run_diagnostics(self) -> list[dict[str, object]] | None:
        # Same retired-run rows status() publishes; does not run the legacy
        # identity gate and does not write the manifest.
        _runs, diagnostics = self._status_snapshot()
        return diagnostics

    def status(self, client: ClientIdentity) -> dict[str, object]:
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        with self._steam_preparation_lock:
            stored = self._steam_preparation_by_session.get(
                _steam_preparation_key(client.session_id)
            )
            preparation = dict(stored) if stored is not None else None
        try:
            self._sample_server_start_watches()
        except Exception:
            # A diagnostic never fails the status read.
            pass
        runs, diagnostics = self._status_snapshot()
        payload: dict[str, object] = {
            "runs": [self._projected_run(run) for run in runs],
            "retail_quarantine": self._quarantined(),
            "client": client.public_payload(),
            "retired_run_diagnostics": diagnostics,
        }
        if self._last_start_error is not None:
            payload["last_start_error"] = self._last_start_error
        observations = self.manifest.storage_observations()
        if observations:
            payload["storage_observations"] = observations
        if preparation is not None:
            payload["steam_preparation"] = preparation
        verdict = self._server_start_verdict_payload()
        if verdict is not None:
            payload["server_start_verdict"] = verdict
        return payload

    def public_status(self) -> dict[str, object]:
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            legacy_error["audit_rows_dropped"] = self._audit_rows_dropped
            return legacy_error
        runs, diagnostics = self._status_snapshot()
        # Keep every non-terminal state: admin recovery needs STARTING and STOPPING.
        active_runs = [run for run in runs if run.state in _ACTIVE_STATES]
        payload: dict[str, object] = {
            "runs": [self._projected_run(run) for run in active_runs],
            "runs_retired": len(runs) - len(active_runs),
            "retail_quarantine": self._quarantined(),
            "retired_run_diagnostics": diagnostics,
            "audit_rows_dropped": self._audit_rows_dropped,
        }
        observations = self.manifest.storage_observations()
        if observations:
            payload["storage_observations"] = observations
        return payload

    def _diag_snapshot(
        self,
    ) -> tuple[str | None, list[dict[str, object]] | None]:
        if self.diag_probe is None:
            return "diag_snapshot_unknown", None
        try:
            result = self.diag_probe()
        except Exception:
            return "diag_snapshot_unknown", None
        if not isinstance(result, dict) or result.get("known") is not True:
            return "diag_snapshot_unknown", None
        processes = result.get("processes")
        if not isinstance(processes, list):
            return "diag_snapshot_unknown", None
        observed: list[dict[str, object]] = []
        for process in processes:
            if not isinstance(process, dict):
                return "diag_snapshot_unknown", None
            pid = process.get("pid")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                return "diag_snapshot_unknown", None
            observed.append(process)
        return None, observed

    def _port_holders(
        self,
    ) -> tuple[str | None, list[dict[str, object]] | None]:
        """Bound UDP ports with holder pid and image, or why the scan is unknown.

        No probe wired means the feature is absent (an empty, known scan), not
        an unknown one: daemons and fixtures built without it keep their
        behaviour. A wired probe that fails, or answers with a shape this code
        cannot vouch for, is unknown: launching on that would be a guess.
        """
        if self.port_probe is None:
            return None, []
        try:
            result = self.port_probe()
        except Exception:
            return "port_scan_unknown", None
        if not isinstance(result, dict) or result.get("known") is not True:
            return "port_scan_unknown", None
        holders = result.get("holders")
        if not isinstance(holders, list):
            return "port_scan_unknown", None
        observed: list[dict[str, object]] = []
        for holder in holders:
            if not isinstance(holder, dict):
                return "port_scan_unknown", None
            port = holder.get("port")
            if (
                not isinstance(port, int)
                or isinstance(port, bool)
                or not 1 <= port <= 65535
            ):
                return "port_scan_unknown", None
            pid = holder.get("pid")
            if pid is not None and (
                not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
            ):
                return "port_scan_unknown", None
            name = holder.get("name")
            observed.append(
                {
                    "port": port,
                    "pid": pid,
                    "name": name if isinstance(name, str) else None,
                }
            )
        return None, observed

    def _foreign_port_reason(
        self, registered_pids: set[int], requested_port: int | None
    ) -> str | None:
        """Refuse to launch on top of a socket this lifecycle does not own.

        A holder is foreign when its pid is not a process of an active run (an
        unattributable pid counts as foreign). It blocks the launch when it is
        a DayZ image on any port, or any image on the port this launch asked
        for. Always a fresh probe: the 1.5 s box cache serves readers, and a
        server that appeared a second ago is exactly the case this guards
        (fb-20260904-114520-6927: a 6-minute-old foreign server on 2302 was
        launched over because the name probe did not list it).
        """
        reason, holders = self._port_holders()
        if reason is not None or holders is None:
            return reason or "port_scan_unknown"
        unattributed = False
        for holder in holders:
            pid = holder.get("pid")
            if isinstance(pid, int) and pid in registered_pids:
                continue
            name = holder.get("name")
            if _is_dayz_image(name) or (
                requested_port is not None and holder["port"] == requested_port
            ):
                return "port_in_use_foreign"
            if pid is None or name is None:
                unattributed = True
        # A holder nobody can be named for might be a DayZ image on another
        # port: the launch is refused until the table can be attributed.
        return "port_attribution_unknown" if unattributed else None

    def box_occupancy(self, *, now: float | None = None) -> dict[str, object]:
        """The box is derived from one snapshot; live state is never re-read after it.

        Observe managed runs plus live DayZDiag not owned by this lifecycle.
        Reuses ``diag_probe`` (the same scan ``start_run`` uses to reject a
        foreign diag). Argv decode is per-PID enrichment, not a second scan.
        An unknown snapshot is occupied: launching would be a guess.
        ``scan_known`` is False on that path (empty ``foreign`` is not proof
        of an empty box). Probe results (``foreign``, ``ports_in_use``,
        ``scan_known``) may be reused for 1.5 s; rows are not.
        """

        clock = time.time() if now is None else float(now)
        self._stamp_queued_launcher_requests(clock)
        snapshot = self._take_box_snapshot(clock)
        probes = self._probes_for_snapshot(snapshot, use_cache=now is None)
        derived = _derive_box(snapshot, probes)
        with self._activity_lock:
            pending = {
                run_id
                for (gen, run_id) in self._adoption_revert_pending
                if gen == snapshot.daemon_generation
            }
        if pending:
            runs = derived.get("runs")
            if isinstance(runs, list):
                for row in runs:
                    if isinstance(row, dict) and row.get("run_id") in pending:
                        row["use_reason"] = ADOPTION_REVERT_PENDING
        return derived

    def _take_box_snapshot(self, clock: float) -> _BoxSnapshot:
        # The seal must be a lower bound of the data it certifies.
        with self._activity_lock:
            revision = self._box_revision
        runs = tuple(self.manifest.list_runs())
        with self._activity_lock:
            clocks = self._use_clocks_locked()
        # No lifecycle lock is held here, so the documented order
        # (_operation_lock before ServerState._lock) is kept.
        bound = frozenset(
            run.run_id
            for run in runs
            if run.state in _ACTIVE_STATES and self._adopt_dispatchable(run.run_id)
        )
        return self._snapshot_from_clocks(clock, runs, revision, bound, clocks)

    def _collect_probes(self, snapshot: _BoxSnapshot) -> _BoxProbes:
        active = [run for run in snapshot.runs if run.state in _ACTIVE_STATES]
        registered_pids = {
            record.pid for run in active for record in run.processes
        }
        ports: list[int] = []

        def _remember_port(port: int) -> None:
            if port not in ports:
                ports.append(port)

        def _absorb_argv(pid: int) -> dict[str, object]:
            try:
                argv = self.argv_of(pid)
            except Exception:
                argv = None
            parsed = parse_dayz_launch_argv(argv or [])
            for port in parsed["ports"]:
                if isinstance(port, int):
                    _remember_port(port)
            return parsed

        for run in active:
            for record in run.processes:
                _absorb_argv(record.pid)

        reason, observed = self._diag_snapshot()
        if reason is not None or observed is None:
            return _BoxProbes(
                foreign=(),
                ports_in_use=tuple(ports),
                scan_known=False,
            )
        live_pids = frozenset(
            int(process["pid"])
            for process in observed
            if int(process["pid"]) in registered_pids
        )
        foreign: list[dict[str, object]] = []
        diag_foreign_pids: set[int] = set()
        for process in observed:
            pid = int(process["pid"])
            parsed = _absorb_argv(pid)
            if pid in registered_pids:
                continue
            diag_foreign_pids.add(pid)
            parsed_ports = parsed.get("ports")
            port = (
                parsed_ports[0]
                if isinstance(parsed_ports, list) and parsed_ports
                else None
            )
            foreign.append(
                {
                    "port": port if isinstance(port, int) else None,
                    "mods": list(parsed.get("mods") or []),
                    "profiles": _public_profiles_label(parsed.get("profiles")),
                }
            )
        # fb-20260904-114520-6927: the socket table is the second witness. A
        # DayZ image holding a UDP port is foreign even when the name probe
        # never saw it (other image, argv without -port); its ports and the
        # ports of registered processes come from the OS, not from argv.
        # Unrelated services (DNS, IKE) never touch the box.
        port_reason, holders = self._port_holders()
        if port_reason is not None or holders is None:
            return _BoxProbes(
                foreign=tuple(foreign),
                ports_in_use=tuple(ports),
                scan_known=True,
                port_scan_known=False,
                port_scan_reason=port_reason or "port_scan_unknown",
                live_pids=live_pids,
            )
        rows_by_pid: dict[int, dict[str, object]] = {}
        unattributed = False
        foreign_ports: list[int] = []
        foreign_ports_dayz_related: list[int] = []
        for holder in holders:
            pid = holder.get("pid")
            port = holder["port"]
            name = holder.get("name")
            registered = isinstance(pid, int) and pid in registered_pids
            dayz = _is_dayz_image(name)
            if registered or dayz or int(port) in _DAYZ_PORT_RANGE:
                _remember_port(int(port))
            if registered:
                continue
            if int(port) not in foreign_ports:
                foreign_ports.append(int(port))
            # This count is evidence from the image name or the established
            # DayZ UDP range. Unattributed or unrelated ports are never guessed.
            if (dayz or int(port) in _DAYZ_PORT_RANGE) and int(port) not in foreign_ports_dayz_related:
                foreign_ports_dayz_related.append(int(port))
            if pid is None or name is None:
                # A socket nobody can be named for cannot be cleared as
                # not-DayZ: the scan is unknown, the box occupied.
                unattributed = True
                continue
            if not dayz or pid in diag_foreign_pids:
                continue
            row = rows_by_pid.get(int(pid))
            if row is None:
                row = {
                    "port": int(port),
                    "mods": [],
                    "profiles": None,
                    "image": name,
                    "source": "port",
                }
                rows_by_pid[int(pid)] = row
                foreign.append(row)
            elif int(port) < int(row["port"]):
                row["port"] = int(port)
        if unattributed:
            return _BoxProbes(
                foreign=tuple(foreign),
                ports_in_use=tuple(ports),
                scan_known=True,
                port_scan_known=False,
                port_scan_reason="port_attribution_unknown",
                foreign_ports=tuple(sorted(foreign_ports)),
                foreign_ports_dayz_related=tuple(
                    sorted(foreign_ports_dayz_related)
                ),
                live_pids=live_pids,
            )
        return _BoxProbes(
            foreign=tuple(foreign),
            ports_in_use=tuple(ports),
            scan_known=True,
            port_scan_known=True,
            foreign_ports=tuple(sorted(foreign_ports)),
            foreign_ports_dayz_related=tuple(
                sorted(foreign_ports_dayz_related)
            ),
            live_pids=live_pids,
        )

    def _probes_for_snapshot(
        self, snapshot: _BoxSnapshot, *, use_cache: bool
    ) -> _BoxProbes:
        if use_cache:
            with self._activity_lock:
                cached = self._box_cache
            if cached is not None:
                cached_at, cached_revision, cached_probes = cached
                if (
                    cached_revision == snapshot.revision
                    and time.monotonic() - cached_at < _BOX_OCCUPANCY_CACHE_S
                ):
                    return cached_probes
        probes = self._collect_probes(snapshot)
        if use_cache:
            with self._activity_lock:
                if self._box_revision == snapshot.revision:
                    self._box_cache = (time.monotonic(), snapshot.revision, probes)
        return probes

    def _reconcile_survivors(
        self, processes: list[ProcessRecord]
    ) -> tuple[list[ProcessRecord], str | None, int]:
        survivors: list[ProcessRecord] = []
        for record in processes:
            try:
                actual = self.guard.snapshot(record.pid)
            except Exception:
                actual = {"error": "guard_unavailable", "exit_code": 3}
            if (
                isinstance(actual, dict)
                and actual.get("error") == "process_not_found"
                and actual.get("exit_code") == 4
            ):
                continue
            if self._identity_matches(record, actual):
                survivors.append(record)
                continue
            if isinstance(actual, dict) and actual.get("exit_code") == 3:
                return [], "guard_unavailable", 503
            return [], "process_identity_mismatch", 409
        return survivors, None, 200

    def _diag_snapshot_registered(
        self, registered_pids: set[int]
    ) -> tuple[bool, str]:
        if self.diag_probe is None:
            return False, "diag_snapshot_unknown"
        try:
            result = self.diag_probe()
        except Exception:
            return False, "diag_snapshot_unknown"
        if not isinstance(result, dict) or result.get("known") is not True:
            return False, "diag_snapshot_unknown"
        processes = result.get("processes")
        if not isinstance(processes, list):
            return False, "diag_snapshot_unknown"
        observed: set[int] = set()
        for process in processes:
            if not isinstance(process, dict):
                return False, "diag_snapshot_unknown"
            pid = process.get("pid")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                return False, "diag_snapshot_unknown"
            observed.add(pid)
        if observed != registered_pids:
            return False, "manual_cleanup_required"
        return True, ""

    def _diag_snapshot_empty(self) -> tuple[bool, str]:
        if self.diag_probe is None:
            return False, "diag_snapshot_unknown"
        try:
            result = self.diag_probe()
        except Exception:
            return False, "diag_snapshot_unknown"
        if not isinstance(result, dict) or result.get("known") is not True:
            return False, "diag_snapshot_unknown"
        processes = result.get("processes")
        if not isinstance(processes, list):
            return False, "diag_snapshot_unknown"
        if processes:
            return False, "manual_cleanup_required"
        # fb-20260904-114520-6927: "empty" also means no DayZ image holds a UDP
        # port. An unreadable table cannot certify emptiness.
        port_reason, holders = self._port_holders()
        if port_reason is not None or holders is None:
            return False, port_reason or "port_scan_unknown"
        for holder in holders:
            if _is_dayz_image(holder.get("name")):
                return False, "manual_cleanup_required"
            if holder.get("pid") is None or holder.get("name") is None:
                return False, "port_attribution_unknown"
        return True, ""

    def _foreign_diag_reason(self, registered_pids: set[int]) -> str | None:
        reason, processes = self._diag_snapshot()
        if reason is not None or processes is None:
            return reason or "diag_snapshot_unknown"
        observed = {int(process["pid"]) for process in processes}
        # Registered PIDs absent from the snapshot are pending reap, not foreign.
        if observed - registered_pids:
            return "foreign_diag_process"
        return None

    def admin_reconcile(
        self,
        run_id: object,
        pid: object,
        reason: str,
        *,
        empty: bool = False,
    ) -> dict[str, object]:
        legacy_error = self._legacy_identity_error()
        if legacy_error is not None:
            return legacy_error
        if self._quarantined():
            return self._error("retail_quarantine", 409)
        if not isinstance(reason, str) or not reason.strip():
            return self._error("invalid_reason", 400)
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(empty, bool)
            or (
                empty
                and pid is not None
            )
            or (
                not empty
                and (
                    not isinstance(pid, int)
                    or isinstance(pid, bool)
                    or pid <= 0
                )
            )
        ):
            return self._error("invalid_reconcile_request", 400)
        with self._operation_lock:
            run = self.manifest.get(run_id)
            if run is None:
                return self._error("run_not_found", 404)
            idle_ownerless = (
                run.state == "RUNNING_IDLE"
                and run.owner_session_id is None
                and run.owner_lease_id is None
            )
            if run.state not in {"UNRECONCILED", "STARTING", "STOPPING"} and not idle_ownerless:
                return self._error("run_not_reconcilable", 409)
            if empty:
                if run.processes:
                    return self._error("invalid_reconcile_request", 400)
                empty_ok, empty_error = self._diag_snapshot_empty()
                if not empty_ok:
                    return self._error(empty_error, 409)
                survivors: list[ProcessRecord] = []
            else:
                selected = next(
                    (record for record in run.processes if record.pid == pid), None
                )
                if selected is None:
                    return self._error("process_not_registered", 404)
                survivors, survivor_error, survivor_status = self._reconcile_survivors(
                    run.processes
                )
                if survivor_error is not None:
                    return self._error(survivor_error, survivor_status)
                diag_ok, diag_error = self._diag_snapshot_registered(
                    {record.pid for record in survivors}
                )
                if not diag_ok:
                    return self._error(diag_error, 409)
            if self._quarantined():
                return self._error("retail_quarantine", 409)
            if not self._audit(
                "admin_reconcile",
                None,
                reason.strip(),
                "confirmed",
                run_id=run_id,
                pid=pid,
                empty=empty,
            ):
                return self._error("audit_failed", 503)
            if self._quarantined():
                return self._error("retail_quarantine", 409)
            if empty:
                empty_ok, empty_error = self._diag_snapshot_empty()
                if not empty_ok:
                    return self._error(empty_error, 409)
            else:
                survivors, survivor_error, survivor_status = self._reconcile_survivors(
                    survivors
                )
                if survivor_error is not None:
                    return self._error(survivor_error, survivor_status)
                diag_ok, diag_error = self._diag_snapshot_registered(
                    {record.pid for record in survivors}
                )
                if not diag_ok:
                    return self._error(diag_error, 409)
            run.owner_session_id = None
            run.owner_lease_id = None
            run.processes = survivors
            if (
                run.state in {"STARTING", "STOPPING"}
                and survivors
                and run.launch_operation_id is not None
            ):
                run.launch_acknowledged = True
            run.state = "RUNNING_IDLE" if survivors else "EXITED"
            if run.state == "EXITED":
                if not self._commit_retirement(
                    run,
                    "admin_reconcile",
                    "admin_reconciled",
                    "confirmed",
                ):
                    return self._error("manifest_failed", 503)
            else:
                try:
                    self._transition_to_idle(
                        [run.run_id],
                        lambda: self.manifest.replace(run),
                    )
                except Exception:
                    return self._error("manifest_failed", 503)
                self._invalidate_box_cache()
            result: dict[str, object] = {
                "reconciled": True,
                "run_id": run_id,
                "state": run.state,
            }
            if empty:
                result["empty"] = True
            else:
                result["pid"] = pid
            return result
