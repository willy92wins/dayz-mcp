"""The log files of the current launch, and reading them from a marker.

Which RPT and script logs belong to the live run, the logs_since marker form,
the tail markers wait_for rewinds, and the report of what a wait read. Moved
out of server.py unchanged (backlog 71fc); server.py imports every name back,
so dayz_mcp.server.<name> is the same object.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import log_tail


def _sibling_profile_dirs(profiles: list[str]) -> list[str]:
    """Add the _client/_server sibling when the run only recorded one side."""
    extra: list[str] = []
    for item in profiles:
        path = Path(item)
        parent = path.parent.name.casefold()
        if parent == "_server":
            sibling = str(path.parent.parent / "_client" / "profiles")
        elif parent == "_client":
            sibling = str(path.parent.parent / "_server" / "profiles")
        else:
            continue
        if log_tail.is_allowed_profiles_dir(sibling) and Path(sibling).is_dir():
            extra.append(sibling)
    return sorted(set(profiles + extra))


def _run_start_epoch(runs: list[dict[str, Any]]) -> float | None:
    """Start of the launch in progress: the newest run's earliest process.

    `min` inside a run is when that run started -- process_lifecycle._run_age_s
    aggregates the same way. `max` across runs keeps the floor on the current
    launch, so a second live run cannot pull it back and readmit the first
    one's logs as if they belonged to this one.

    Only a live run reaches here with a stamp at all: RunRecord.validate makes
    EXITED carry an empty `processes` and RUNNING/RUNNING_IDLE a non-empty one.
    With a single live run -- the only shape observed on this host across the
    store and its six pre-prune backups -- both aggregations return the same
    float, so this is a guard rather than a repair.
    """

    starts: list[float] = []
    for run in runs:
        times: list[float] = []
        for proc in run.get("processes") or []:
            if not isinstance(proc, dict):
                continue
            raw = proc.get("creation_time_utc")
            if not isinstance(raw, str) or not raw:
                continue
            try:
                times.append(
                    datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
                )
            except ValueError:
                continue
        if times:
            starts.append(min(times))
    return max(starts) if starts else None


def _newest_rpt_and_script(dated: list[tuple[float, str]]) -> list[str]:
    """Newest .rpt and newest .log by suffix, independent of mtime gap."""
    newest_rpt: str | None = None
    newest_script: str | None = None
    rpt_mtime = script_mtime = None
    for mtime, path in dated:
        suffix = Path(path).suffix.casefold()
        if suffix == ".rpt":
            if rpt_mtime is None or mtime >= rpt_mtime:
                newest_rpt, rpt_mtime = path, mtime
        elif suffix == ".log":
            if script_mtime is None or mtime >= script_mtime:
                newest_script, script_mtime = path, mtime
    return [path for path in (newest_rpt, newest_script) if path]


def _current_launch_logs(profiles_dir: str, start_epoch: float | None) -> list[str]:
    """Return RPT/script files from the current launch, never historic dumps.

    Without a launch timestamp, keep the newest .rpt and the newest .log by
    name suffix (not a time cluster), so a quiet current file is not dropped.
    """
    paths = log_tail.resolve_log_files(profiles_dir)
    dated: list[tuple[float, str]] = []
    for path in paths:
        # crash_*.log dumps are excluded even when freshly touched: a crash dump
        # carries CE world-create noise by the hundred thousand lines and starves
        # the wait_for scan budget (fb-20260823-130809-413a).
        if Path(path).name.casefold().startswith("crash"):
            continue
        try:
            dated.append((Path(path).stat().st_mtime, path))
        except OSError:
            continue
    if not dated:
        return []
    if start_epoch is None:
        return _newest_rpt_and_script(dated)
    floor = start_epoch - 2.0
    return [path for mtime, path in dated if mtime >= floor]


def _coerce_logs_since_marker(marker: object) -> str:
    """Normalize a logs_since marker to the encoded JSON string.

    The tool returns an encoded JSON string. FastMCP pre-parses JSON-looking
    strings into dicts before the handler runs because the parameter type is a
    union, not bare ``str`` (``FuncMetadata.pre_parse_json``). Clients that
    JSON-decode the returned marker also pass a dict. Accept both; reject
    anything else as ``bad_marker``.
    """
    if isinstance(marker, str):
        return marker
    if isinstance(marker, dict):
        try:
            return json.dumps(marker, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as error:
            raise ToolError("bad_marker") from error
    raise ToolError("bad_marker")


def _profile_dirs_from_runs(runs: list[dict[str, Any]]) -> list[str]:
    candidates = sorted(
        {str(item.get("profiles")) for item in runs if item.get("profiles")}
    )
    allowed = [item for item in candidates if log_tail.is_allowed_profiles_dir(item)]
    return _sibling_profile_dirs(allowed)


def _offset_before_last_lines(data: bytes, lookback_lines: int) -> int:
    """Byte offset of the start of the last ``lookback_lines`` lines.

    The result is always ``0`` or the byte just after a ``\\n``: a reader
    resuming there sees whole lines, never a half-line.
    """
    if lookback_lines <= 0 or not data:
        return len(data)
    parts = data.split(b"\n")
    line_count = len(parts) - 1 if parts and parts[-1] == b"" else len(parts)
    skip = max(0, line_count - lookback_lines)
    if skip == 0:
        return 0
    offset = 0
    seen = 0
    for part in parts:
        if seen >= skip:
            break
        offset += len(part) + 1
        seen += 1
    # `offset` is measured from byte 0 of `data` (each skipped line contributes
    # its length plus its terminating newline), so it is already an absolute
    # file offset; no window base is added.
    return min(offset, len(data))


def _offset_before_last_lines_in_window(
    window: bytes, window_start: int, lookback_lines: int
) -> int:
    """Absolute offset of the start of the last ``lookback_lines`` lines.

    ``window`` is the tail of the file starting at ``window_start``, not the whole
    file, and that is what makes this fiddly in two places:

    * unless the window starts at byte 0 its first line is a fragment cut by the
      window boundary. It is not a line, so it is neither counted nor returned --
      but its bytes still have to be added to the offset, or the result lands
      mid-line and the reader gets half a line as though it were whole;
    * when the window holds fewer complete lines than were asked for, the honest
      answer is the first complete line IN THE WINDOW. Returning 0 would point at
      the start of a file that may be hundreds of MB, which is the read this
      function exists to avoid.
    """
    if lookback_lines <= 0 or not window:
        return window_start + len(window)
    parts = window.split(b"\n")
    base = window_start
    if window_start > 0:
        base += len(parts[0]) + 1      # skip the boundary fragment, bytes included
        parts = parts[1:]
    if not parts:
        return base
    line_count = len(parts) - 1 if parts[-1] == b"" else len(parts)
    skip = max(0, line_count - lookback_lines)
    offset = base
    for part in parts[:skip]:
        offset += len(part) + 1
    return min(offset, window_start + len(window))


def _marker_rewound_handle(
    handle, path: str, lookback_lines: int, *, stop: object = None
) -> log_tail.TailMarker:
    """``_marker_rewound`` on a handle the caller already holds open.

    ``stop`` makes the window read cooperative; see ``log_tail.read_window``.
    """

    size = os.fstat(handle.fileno()).st_size
    identity = log_tail._file_identity(
        handle, min(log_tail.IDENTITY_PREFIX_BYTES, size)
    )
    read_size = min(size, log_tail.MAX_TAIL_BYTES)
    window_start = size - read_size
    handle.seek(window_start)
    window = log_tail.read_window(handle, read_size, stop)
    offset = _offset_before_last_lines_in_window(window, window_start, lookback_lines)
    return log_tail.TailMarker(
        path=path, offset=offset, size=size, identity=identity
    )


def _marker_rewound(path: str, lookback_lines: int) -> log_tail.TailMarker:
    """Marker rewound by ``lookback_lines``, reading only the file's tail.

    D40: this used to read the file whole. DayZ RPTs reach hundreds of MB in a
    long session, and every ``wait_for(log_matches, lookback_lines>0)`` paid for
    it. ``log_tail`` already caps its own reads at ``MAX_TAIL_BYTES``; this now
    respects the same ceiling. Size comes from ``os.fstat`` on the open handle,
    not from ``stat(path)``: the game is appending to this file while we read it,
    so the size has to describe the bytes we actually took.
    """
    file_path = Path(path)
    with file_path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        identity = log_tail._file_identity(
            handle, min(log_tail.IDENTITY_PREFIX_BYTES, size)
        )
        read_size = min(size, log_tail.MAX_TAIL_BYTES)
        window_start = size - read_size
        handle.seek(window_start)
        window = handle.read(read_size)
    offset = _offset_before_last_lines_in_window(window, window_start, lookback_lines)
    return log_tail.TailMarker(
        path=str(file_path), offset=offset, size=size, identity=identity
    )


def _log_markers_at_end(paths: list[str]) -> dict[str, log_tail.TailMarker]:
    markers: dict[str, log_tail.TailMarker] = {}
    for path in paths:
        try:
            result = log_tail.read_since(path, None)
        except log_tail.LogTailError:
            continue
        markers[path] = result["marker"]
    return markers


def _log_markers_with_lookback(
    paths: list[str], lookback_lines: int
) -> dict[str, log_tail.TailMarker]:
    if lookback_lines <= 0:
        return _log_markers_at_end(paths)
    markers: dict[str, log_tail.TailMarker] = {}
    for path in paths:
        try:
            markers[path] = _marker_rewound(path, lookback_lines)
        except (OSError, log_tail.LogTailError):
            continue
    return markers


def _new_log_lines(
    paths: list[str], markers: dict[str, log_tail.TailMarker]
) -> tuple[list[str], dict[str, log_tail.TailMarker], dict[str, int]]:
    """New lines since ``markers``, plus how many each file contributed.

    A path missing from the returned counts could not be read at all. That is
    the difference between "the file had nothing new" and "the file was never
    opened", and wait_for used to collapse both into silence.
    """
    lines: list[str] = []
    updated = dict(markers)
    counts: dict[str, int] = {}
    for path in paths:
        try:
            result = log_tail.read_since(path, markers.get(path))
        except log_tail.LogTailError:
            continue
        updated[path] = result["marker"]
        lines.extend(result["lines"])
        counts[path] = len(result["lines"])
    return lines, updated, counts


def _log_label(path: str) -> str:
    """Side-qualified file name for the wire; no host path leaves the daemon."""
    item = Path(path)
    side = item.parent.parent.name
    return f"{side}/{item.name}" if side.startswith("_") else item.name


def _record_scan(
    paths: list[str],
    counts: dict[str, int],
    seen: list[str],
    totals: dict[str, int],
    unreadable: set[str],
) -> None:
    """Fold one probe's per-file counts into the cumulative scan report."""
    for path in paths:
        if path not in totals:
            seen.append(path)
            totals[path] = 0
        if path in counts:
            totals[path] += counts[path]
            unreadable.discard(path)
        else:
            unreadable.add(path)


def _scanned_report(
    paths: list[str],
    totals: dict[str, int],
    unreadable: set[str],
    lookback_from: str,
    scan_truncated: bool,
) -> dict[str, Any]:
    """What wait_for actually read, so a no-match is visible as a no-match.

    Reported by two sessions on 2026-08-21: ``observed`` carries only the last
    line of the newest file, so when the RPT sorts newest it looks like the
    script log was never opened. It was; nothing in it matched. Names are
    side-qualified file names, never host paths -- this crosses the MCP wire.
    """
    files = [
        {
            "name": _log_label(path),
            "lines": totals.get(path, 0),
            "readable": path not in unreadable,
        }
        for path in paths
    ]
    report: dict[str, Any] = {
        "pattern_kind": "substring",
        "lookback_from": lookback_from,
        "files": files,
        "lines_total": sum(int(item["lines"]) for item in files),
    }
    if lookback_from == "launch":
        report["scan_truncated"] = scan_truncated
    return report
