"""Incremental tailing of DayZ RPT / script logs (F2.1).

Pure host-side file reading: nothing here talks to the bridge, the daemon or the
game, so the whole contract is exercisable with fixtures.

The marker is (path, offset, size) per file. Size is carried alongside offset so
truncation is detectable: DayZ rotates logs by starting a new file per launch,
but a profile directory can also be wiped between runs, and silently resuming at
a stale offset would skip lines with no signal that anything was lost.
"""
from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from pathlib import Path


MAX_TAIL_BYTES = 256 * 1024
LOG_SUFFIXES = (".rpt", ".log")
# Bytes hashed to identify the file. A rewrite in place keeps the path and can
# keep or exceed the old size, so size alone cannot detect it.
IDENTITY_PREFIX_BYTES = 512


class LogTailError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TailMarker:
    path: str
    offset: int
    size: int
    identity: int = 0


def encode_marker(markers: dict[str, TailMarker]) -> str:
    return json.dumps(
        {
            path: [marker.offset, marker.size, marker.identity]
            for path, marker in sorted(markers.items())
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_marker(value: str | None) -> dict[str, TailMarker]:
    if value is None or value == "":
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as error:
        raise LogTailError("bad_marker") from error
    if not isinstance(parsed, dict):
        raise LogTailError("bad_marker")
    markers: dict[str, TailMarker] = {}
    for path, fields in parsed.items():
        # Two-element markers are the pre-identity format; accept them so an
        # in-flight caller is not hard-failed, and treat identity as unknown.
        if (
            not isinstance(path, str)
            or not isinstance(fields, list)
            or len(fields) not in (2, 3)
            or any(type(item) is not int or item < 0 for item in fields)
        ):
            raise LogTailError("bad_marker")
        markers[path] = TailMarker(
            path=path,
            offset=fields[0],
            size=fields[1],
            identity=fields[2] if len(fields) == 3 else 0,
        )
    return markers


def _file_identity(handle, length: int) -> int:
    """crc32 of the first `length` bytes; 0 means "unknown", never "mismatch".

    The prefix length is bounded by the size ALREADY seen, never by the current
    size: hashing "the first 512 bytes" of a file shorter than that would change
    on every append and report each append as a rotation.
    """
    if length <= 0:
        return 0
    position = handle.tell()
    try:
        handle.seek(0)
        prefix = handle.read(length)
    finally:
        handle.seek(position)
    if len(prefix) < length:
        return 0  # shorter than expected: the size checks own that case
    return zlib.crc32(prefix) or 1  # never 0: 0 is reserved for "unknown"


def is_allowed_profiles_dir(value: object) -> bool:
    """Fail-closed check on a profiles path taken from lifecycle state.

    `profiles` reaches this tool from the run manifest, i.e. it is data, not a
    caller argument -- but it still decides which host directory gets read, so it
    is validated rather than trusted. The worker always builds it as
    `<dev_root>\\_server|_client\\profiles` (`dayz_test_worker.py:208-211`), so
    anything else is not a run profile and is refused.
    """
    if not isinstance(value, str) or not value:
        return False
    try:
        path = Path(value)
    except (OSError, ValueError):
        return False
    if not path.is_absolute() or ".." in path.parts:
        return False
    return (
        path.name.casefold() == "profiles"
        and path.parent.name.casefold() in {"_server", "_client"}
    )


def resolve_log_files(profiles_dir: str) -> list[str]:
    """Newest-last list of RPT/script logs in a profile directory."""
    try:
        entries = [
            item
            for item in Path(profiles_dir).iterdir()
            if item.is_file() and item.suffix.casefold() in LOG_SUFFIXES
        ]
    except OSError:
        return []
    return [str(item) for item in sorted(entries, key=lambda item: item.stat().st_mtime)]


def read_since(
    path: str,
    marker: TailMarker | None,
    *,
    max_bytes: int = MAX_TAIL_BYTES,
    max_lines: int | None = None,
) -> dict[str, object]:
    """Read what was appended since `marker`.

    `rotated` is True when the file cannot be a pure append of what the marker
    described: it shrank, it is shorter than where we stopped, or its first bytes
    changed (a wipe-and-rewrite keeps the path and can keep the size, so size
    alone misses it). The read then restarts at zero instead of resuming at an
    offset that now points into unrelated content.

    `max_lines` caps what is RETURNED, and the returned marker advances only to
    the end of the lines actually handed over -- capping output while reporting a
    marker past the withheld lines would drop them silently on the next call.

    Line terminator is LF; CRLF works because it ends in LF. A file using lone CR
    would not be split, which DayZ on Windows does not produce.
    """
    file_path = Path(path)
    try:
        with file_path.open("rb") as handle:
            size = file_path.stat().st_size
            # Compare over the SAME prefix length the marker was built with, so
            # an append (which only adds bytes after it) cannot look like a change.
            previous = 0
            if marker is not None:
                previous = _file_identity(
                    handle, min(IDENTITY_PREFIX_BYTES, marker.size)
                )
            rotated = marker is not None and (
                size < marker.size
                or size < marker.offset
                or (marker.identity != 0 and previous != marker.identity)
            )
            start = 0 if (marker is None or rotated) else marker.offset
            truncated = False
            if size - start > max_bytes:
                # Keep the NEWEST bytes: on a burst the tail diagnoses the run.
                start = size - max_bytes
                truncated = True
            handle.seek(start)
            payload = handle.read(max_bytes)
            new_identity = _file_identity(handle, min(IDENTITY_PREFIX_BYTES, size))
    except OSError as error:
        raise LogTailError("log_unavailable") from error

    # Split on BYTES, never on decoded text: with errors="replace" an invalid
    # byte becomes U+FFFD, which re-encodes to 3 bytes, so measuring the withheld
    # tail in characters would desynchronise the offset. A trailing partial line
    # is left for next time -- half a line reported as whole is a lie the
    # consumer cannot detect.
    last_newline = payload.rfind(b"\n")
    complete = b"" if last_newline == -1 else payload[: last_newline + 1]
    if max_lines is not None:
        cut = 0
        for _ in range(max_lines):
            nxt = complete.find(b"\n", cut)
            if nxt == -1:
                break
            cut = nxt + 1
        if cut < len(complete):
            truncated = True
        complete = complete[:cut]
    consumed = start + len(complete)
    lines = complete.decode("utf-8", errors="replace").splitlines()
    return {
        "path": str(file_path),
        "lines": lines,
        "rotated": rotated,
        "truncated": truncated,
        "marker": TailMarker(
            path=str(file_path),
            offset=consumed,
            size=size,
            identity=new_identity,
        ),
    }
