"""Pure parsers, PBO header reader, and composition for ``dayz_test_run.server_boot``.

Stdlib-only so unit tests can exercise log parsing, PBO header inspection,
vanilla/mod script counting, and report composition without starting the daemon.
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Mapping, Sequence

MODULE_NAMES: tuple[str, ...] = ("GameLib", "Game", "World", "Mission")
MAX_LOG_READ_BYTES = 64 * 1024 * 1024

_VALID_MODULES = frozenset(MODULE_NAMES)
_MODULE_LINE_RE = re.compile(r"SCRIPT\s+: Module: (\w+); loaded (\d+)x files;")
_SCRIPT_ERROR_MARKER = "SCRIPT    (E)"
_TERMINATION_COUNTDOWN_MARKER = "[Server] :: termination in:"

_MAX_HEADER_BYTES = 16 * 1024 * 1024
_MAX_HEADER_ENTRIES = 500_000
_COMPACT_THRESHOLD = 65_536
_RECORD = struct.Struct("<5I")
_MIME_VERS = 0x56657273
_NAME_SURROGATE_BIT = 0x20000000

_SEGMENT_TO_MODULE: dict[str, str] = {
    "2_gamelib": "GameLib",
    "3_game": "Game",
    "4_world": "World",
    "5_mission": "Mission",
}


@dataclass(frozen=True, slots=True)
class ScriptLogReport:
    """Parsed summary of a server ``script_*.log`` file."""

    modules: dict[str, int]
    complete: bool
    script_errors: int


def parse_script_log(text: str) -> ScriptLogReport:
    """Parse per-module loaded file counts and ``SCRIPT    (E)`` count."""
    modules: dict[str, int] = {}
    script_errors = 0
    for line in text.splitlines():
        if _SCRIPT_ERROR_MARKER in line:
            script_errors += 1
        match = _MODULE_LINE_RE.search(line)
        if match is None:
            continue
        name, count_raw = match.group(1), match.group(2)
        if name in _VALID_MODULES and name not in modules:
            modules[name] = int(count_raw)
    return ScriptLogReport(
        modules=modules,
        complete="Mission" in modules,
        script_errors=script_errors,
    )


def has_termination_countdown(rpt_text: str) -> bool:
    """True when the server RPT contains a ``[Server] :: termination in:`` line."""
    return any(
        _TERMINATION_COUNTDOWN_MARKER in line for line in rpt_text.splitlines()
    )


def read_log_text(
    path: str | os.PathLike[str],
    *,
    max_bytes: int = MAX_LOG_READ_BYTES,
) -> str:
    """Read up to ``max_bytes`` in binary mode and decode UTF-8 with replacement."""
    with open(path, "rb") as handle:
        raw = handle.read(max_bytes)
    return raw.decode("utf-8", errors="replace")


def _classify_pbo_entry(name: str) -> str | None:
    if not name.casefold().endswith(".c"):
        return None
    parts = [part for part in name.replace("/", "\\").split("\\") if part]
    if len(parts) < 2:
        return None
    for segment in parts[:-1]:
        module = _SEGMENT_TO_MODULE.get(segment.casefold())
        if module is not None:
            return module
    return None


def _ensure_bytes(
    stream: BinaryIO,
    buf: bytearray,
    pos: int,
    needed: int,
    base_offset: int,
) -> bool:
    if base_offset + pos + needed > _MAX_HEADER_BYTES:
        return False
    while len(buf) - pos < needed:
        to_read = needed - (len(buf) - pos)
        chunk = stream.read(to_read)
        if not chunk:
            return False
        buf.extend(chunk)
        if base_offset + len(buf) > _MAX_HEADER_BYTES:
            return False
    return True


def _read_cstring(
    stream: BinaryIO,
    buf: bytearray,
    pos: int,
    base_offset: int,
    min_if_empty: int,
    min_if_nonempty: int,
) -> tuple[bytes, int] | None:
    """Read one NUL-terminated string without reading past the header end.

    ``min_if_empty`` and ``min_if_nonempty`` are exact lower bounds on how many
    header bytes must still follow the terminating NUL in any valid PBO header,
    guaranteeing that lookahead reads never cross into entry payload data.
    """
    if not _ensure_bytes(stream, buf, pos, 1 + min_if_empty, base_offset):
        return None
    if buf[pos] == 0:
        return b"", pos + 1
    while True:
        nul_idx = buf.find(0, pos)
        if nul_idx >= 0:
            raw = bytes(buf[pos:nul_idx])
            next_pos = nul_idx + 1
            if not _ensure_bytes(stream, buf, next_pos, min_if_nonempty, base_offset):
                return None
            return raw, next_pos
        step = 1 + min_if_nonempty
        if base_offset + len(buf) + step > _MAX_HEADER_BYTES:
            return None
        chunk = stream.read(step)
        if not chunk:
            return None
        buf.extend(chunk)
        if base_offset + len(buf) > _MAX_HEADER_BYTES:
            return None


def _read_pbo_stream_counts(stream: BinaryIO) -> dict[str, int] | None:
    counts: dict[str, int] = {name: 0 for name in MODULE_NAMES}
    buf = bytearray()
    pos = 0
    base_offset = 0
    entry_count = 0
    first = True

    while True:
        if pos >= _COMPACT_THRESHOLD:
            del buf[:pos]
            base_offset += pos
            pos = 0

        parsed = _read_cstring(stream, buf, pos, base_offset, 20, 41)
        if parsed is None:
            return None
        raw_name, pos = parsed

        if first and raw_name == b"":
            mime, _orig, _res, _stamp, _size = _RECORD.unpack_from(buf, pos)
            pos += _RECORD.size
            first = False
            if mime == _MIME_VERS:
                while True:
                    key_parsed = _read_cstring(stream, buf, pos, base_offset, 21, 23)
                    if key_parsed is None:
                        return None
                    raw_key, pos = key_parsed
                    if raw_key == b"":
                        break
                    val_parsed = _read_cstring(stream, buf, pos, base_offset, 22, 22)
                    if val_parsed is None:
                        return None
                    _raw_val, pos = val_parsed
                continue
            break

        first = False
        if raw_name == b"":
            pos += _RECORD.size
            break

        pos += _RECORD.size
        entry_count += 1
        if entry_count > _MAX_HEADER_ENTRIES:
            return None

        if len(raw_name) >= 2 and raw_name[-2:] in (b".c", b".C"):
            module = _classify_pbo_entry(raw_name.decode("utf-8", errors="replace"))
            if module is not None:
                counts[module] += 1

    return counts


def read_pbo_header_counts(
    source: str | os.PathLike[str] | BinaryIO,
) -> dict[str, int] | None:
    """Count module ``.c`` entries in a PBO header; ``None`` if malformed or capped.

    A path is opened unbuffered: every read asks the OS for no more bytes than
    the header still holds, so the entry data after the header is never read.
    """
    if isinstance(source, (str, os.PathLike)):
        try:
            with open(source, "rb", buffering=0) as handle:
                return _read_pbo_stream_counts(handle)
        except OSError:
            return None
    try:
        return _read_pbo_stream_counts(source)
    except OSError:
        return None


def _add_counts(target: dict[str, int], delta: Mapping[str, int]) -> None:
    for name in MODULE_NAMES:
        target[name] += int(delta[name])


def _is_link_or_junction(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    info = entry.stat(follow_symlinks=False)
    tag = int(getattr(info, "st_reparse_tag", 0) or 0)
    return bool(tag & _NAME_SURROGATE_BIT)


def _accumulate_pbo_directory_no_links(
    directory: str | Path, totals: dict[str, int]
) -> bool:
    with os.scandir(directory) as entries:
        for entry in entries:
            if _is_link_or_junction(entry):
                continue
            if (
                entry.is_file(follow_symlinks=False)
                and entry.name.casefold().endswith(".pbo")
            ):
                counts = read_pbo_header_counts(entry.path)
                if counts is None:
                    return False
                _add_counts(totals, counts)
    return True


def count_vanilla_files(
    game_directory: str | os.PathLike[str],
) -> dict[str, int] | None:
    """Sum module ``.c`` entries across vanilla ``dta``, ``addons`` and DLC ``addons``."""
    totals: dict[str, int] = {name: 0 for name in MODULE_NAMES}
    game_path = Path(game_directory)
    try:
        info = os.stat(game_path, follow_symlinks=False)
        if os.path.islink(game_path) or bool(
            int(getattr(info, "st_reparse_tag", 0) or 0) & _NAME_SURROGATE_BIT
        ):
            return None
        with os.scandir(game_path) as top_entries:
            for entry in top_entries:
                if _is_link_or_junction(entry):
                    continue
                if not entry.is_dir(follow_symlinks=False):
                    continue
                folded = entry.name.casefold()
                if folded in {"dta", "addons"}:
                    if not _accumulate_pbo_directory_no_links(entry.path, totals):
                        return None
                    continue
                if entry.name.startswith(("!", "@")):
                    continue
                with os.scandir(entry.path) as sub_entries:
                    for sub in sub_entries:
                        if _is_link_or_junction(sub):
                            continue
                        if (
                            sub.is_dir(follow_symlinks=False)
                            and sub.name.casefold() == "addons"
                        ):
                            if not _accumulate_pbo_directory_no_links(sub.path, totals):
                                return None
    except OSError:
        return None
    return totals


def count_mod_files(
    mod_directories: Sequence[str | os.PathLike[str]],
) -> dict[str, int] | None:
    """Sum module ``.c`` entries across each mod directory's ``addons`` folder."""
    totals: dict[str, int] = {name: 0 for name in MODULE_NAMES}
    try:
        for mod_dir in mod_directories:
            with os.scandir(mod_dir) as entries:
                for entry in entries:
                    if (
                        entry.is_dir(follow_symlinks=True)
                        and entry.name.casefold() == "addons"
                    ):
                        with os.scandir(entry.path) as pbo_entries:
                            for pbo_entry in pbo_entries:
                                if (
                                    pbo_entry.is_file(follow_symlinks=True)
                                    and pbo_entry.name.casefold().endswith(".pbo")
                                ):
                                    counts = read_pbo_header_counts(pbo_entry.path)
                                    if counts is None:
                                        return None
                                    _add_counts(totals, counts)
    except OSError:
        return None
    return totals


def compute_missing_files(
    loaded_modules: Mapping[str, int],
    vanilla_files: Mapping[str, int] | None,
    mod_files: Mapping[str, int] | None,
) -> dict[str, int] | None:
    """Per-loaded-module ``max(0, vanilla + mods - loaded)``, or ``None``."""
    if vanilla_files is None or mod_files is None:
        return None
    return {
        name: max(0, int(vanilla_files[name]) + int(mod_files[name]) - int(loaded))
        for name, loaded in loaded_modules.items()
    }


def compose_server_boot(
    script_log: ScriptLogReport,
    *,
    termination_countdown: bool,
    vanilla_files: Mapping[str, int] | None,
    mod_files: Mapping[str, int] | None,
) -> dict[str, object]:
    """Build the ``server_boot`` report dictionary for ``dayz_test_run``."""
    normalized_vanilla = (
        {name: int(vanilla_files[name]) for name in MODULE_NAMES}
        if vanilla_files is not None
        else None
    )
    normalized_mods = (
        {name: int(mod_files[name]) for name in MODULE_NAMES}
        if mod_files is not None
        else None
    )
    modules = dict(script_log.modules)
    return {
        "modules": modules,
        "complete": bool(script_log.complete),
        "script_errors": int(script_log.script_errors),
        "termination_countdown": bool(termination_countdown),
        "vanilla_files": normalized_vanilla,
        "mod_files": normalized_mods,
        "missing_files": compute_missing_files(
            modules, normalized_vanilla, normalized_mods
        ),
    }
