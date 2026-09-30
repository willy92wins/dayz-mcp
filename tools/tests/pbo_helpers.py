"""Synthetic PBOs for the provenance, pack-addon and release tests.

The layout is the one tools/dev/pbo_provenance.py documents, measured on 24
DayZ_MCP.pbo builds: a 'Vers' properties record, one header record
per file, the data in header order, then a zero byte and the SHA-1 of every byte
before it. AddonBuilder -packonly packs every file of the stage (fb-63c9; the live
PBO of 2026-09-30 carries all 17 staged files, include.lst and $PBOPREFIX$
included), with backslash names; stage_entries mirrors that.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

_RECORD = struct.Struct("<5I")
_MIME_PROPERTIES = 0x56657273  # 'Vers'
DEFAULT_PROPERTIES = (("product", "dayz ugc"), ("prefix", "DayZ_MCP"))


def build_pbo(
    entries: list[tuple[str, bytes]],
    *,
    properties: tuple[tuple[str, str], ...] = DEFAULT_PROPERTIES,
) -> bytes:
    """A plain (uncompressed) PBO holding ``entries`` in order."""
    header = bytearray(b"\x00" + _RECORD.pack(_MIME_PROPERTIES, 0, 0, 0, 0))
    for key, value in properties:
        header += key.encode("utf-8") + b"\x00" + value.encode("utf-8") + b"\x00"
    header += b"\x00"
    for name, data in entries:
        header += name.encode("utf-8") + b"\x00" + _RECORD.pack(0, 0, 0, 0, len(data))
    header += b"\x00" + _RECORD.pack(0, 0, 0, 0, 0)
    body = bytes(header) + b"".join(data for _, data in entries)
    return body + b"\x00" + hashlib.sha1(body).digest()


def stage_entries(stage: Path) -> list[tuple[str, bytes]]:
    """Every file under ``stage`` as -packonly packs it: backslash names, sorted."""
    files = sorted(path for path in Path(stage).rglob("*") if path.is_file())
    return [
        (path.relative_to(stage).as_posix().replace("/", "\\"), path.read_bytes())
        for path in files
    ]


def marker_bytes(
    commit: str | None,
    tree: str | None,
    *,
    built_utc: str = "2026-09-30T12:00:00Z",
    source: str = "git",
) -> bytes:
    """mcp_build.json in the shape tools/pack-addon.ps1 writes it."""
    payload = {"commit": commit, "tree": tree, "built_utc": built_utc, "source": source}
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
