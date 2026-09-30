"""Compare a built PBO file by file with addon/ at a git ref, and check its build marker.

Usage: python tools/dev/pbo_provenance.py <pbo> <repo> <ref>

tools/pack-addon.ps1 packs the committed addon/ tree of a ref and writes one file
that is not in git into the staged addon root, mcp_build.json, so that a PBO says
which commit built it (fb-20260819-024951-e307):

    {"commit":"<sha>","tree":"<addon/ tree sha>","built_utc":"YYYY-MM-DDTHH:MM:SSZ","source":"git"}

This tool prints one line per PBO entry (exact, DIFF, DIFF-EOL, MISSING_IN_GIT,
DUPLICATE, marker), one line per file of addon/ that the PBO lacks (NOT_IN_PBO),
the prefix verdict, the marker verdict, and a last line PROVENANCE OK or
PROVENANCE FAIL.

Exit status: 0 only when every entry equals its git blob byte for byte, no file of
addon/ is missing, the header's prefix property equals addon/$PBOPREFIX$ at <ref>
(the prefix is the path every file of the addon is served under), and the one extra
entry is a marker that names <ref>'s commit and addon/ tree. 1 on any mismatch,
line-ending-only differences included. 2 when the check cannot run: bad arguments, a
file that is not a plain PBO (a header that names a property twice included), or a
git error.

PBO layout, measured on 24 DayZ_MCP.pbo builds (2026-06..09, all carrying the
property product=dayz ugc that AddonBuilder writes): a header of records, each a
NUL-terminated name and five little-endian uint32 (mime, original size, reserved,
timestamp, data size). The first record has an empty name and mime 'Vers' and is
followed by NUL-terminated key/value properties closed by an empty key; the next
record with an empty name ends the header. The data of every entry follows in
header order, then one zero byte and the SHA-1 of every byte before that zero byte.

Standard library only; git must be on PATH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
from datetime import datetime
from pathlib import Path

MARKER_NAME = "mcp_build.json"
MARKER_KEYS = frozenset({"commit", "tree", "built_utc", "source"})
MARKER_SOURCES = frozenset({"git", "folder"})
PREFIX_FILE = "$PBOPREFIX$"

_MIME_PROPERTIES = 0x56657273  # 'Vers'
_RECORD = struct.Struct("<5I")
_TRAILER_SIZE = 1 + 20  # a zero byte, then the SHA-1 of every byte before it
_OBJECT_ID = re.compile(r"[0-9a-f]{40}")
_UTC_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class PboFormatError(ValueError):
    """The bytes are not a PBO this tool can compare."""


class MarkerError(ValueError):
    """mcp_build.json is not the marker tools/pack-addon.ps1 writes."""


class GitError(RuntimeError):
    """git could not answer for the repository or the ref."""


def _read_cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.find(b"\x00", pos)
    if end < 0:
        raise PboFormatError(f"the header string at byte {pos} is not NUL-terminated")
    try:
        return data[pos:end].decode("utf-8"), end + 1
    except UnicodeDecodeError as error:
        raise PboFormatError(f"the header string at byte {pos} is not UTF-8") from error


def read_pbo(data: bytes) -> tuple[dict[str, str], list[tuple[str, bytes]]]:
    """Return (properties, [(entry name, entry bytes)]) or raise PboFormatError."""
    pos = 0
    properties: dict[str, str] = {}
    records: list[tuple[str, int]] = []
    first = True
    while True:
        name, pos = _read_cstr(data, pos)
        if pos + _RECORD.size > len(data):
            raise PboFormatError("the header ends inside a record")
        mime, original, _reserved, _stamp, size = _RECORD.unpack_from(data, pos)
        pos += _RECORD.size
        if name == "" and first and mime == _MIME_PROPERTIES:
            while True:
                key, pos = _read_cstr(data, pos)
                if key == "":
                    break
                value, pos = _read_cstr(data, pos)
                if key in properties:
                    # Readers disagree on which value wins; the header says two things.
                    raise PboFormatError(f"the header names the property {key!r} twice")
                properties[key] = value
            first = False
            continue
        first = False
        if name == "":
            break
        if mime != 0 or original not in (0, size):
            raise PboFormatError(
                f"entry {name!r} is compressed or encoded (mime {mime:#010x}); "
                "only plain entries can be compared"
            )
        records.append((name, size))
    data_end = pos + sum(size for _, size in records)
    if len(data) - data_end != _TRAILER_SIZE:
        raise PboFormatError(
            f"the header declares {data_end - pos} bytes of data; the file does not hold "
            f"exactly that plus the {_TRAILER_SIZE}-byte trailer"
        )
    if data[data_end] != 0 or hashlib.sha1(data[:data_end]).digest() != data[data_end + 1:]:
        raise PboFormatError(
            "the trailing SHA-1 does not match the file: truncated or modified after packing"
        )
    entries: list[tuple[str, bytes]] = []
    for name, size in records:
        entries.append((name, data[pos:pos + size]))
        pos += size
    return properties, entries


def parse_marker(data: bytes) -> dict[str, str | None]:
    """Return the fields of mcp_build.json, or raise MarkerError naming what is wrong.

    A git build names a 40-hex commit and addon/ tree; a -Source folder build has
    null for both. Either way built_utc is YYYY-MM-DDTHH:MM:SSZ.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        raise MarkerError("it starts with a byte-order mark")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MarkerError("it is not UTF-8") from error
    try:
        marker = json.loads(text)
    except json.JSONDecodeError as error:
        raise MarkerError(f"it is not JSON: {error}") from error
    if not isinstance(marker, dict) or set(marker) != MARKER_KEYS:
        raise MarkerError(f"it must hold exactly the keys {sorted(MARKER_KEYS)}")
    source = marker["source"]
    if not isinstance(source, str) or source not in MARKER_SOURCES:
        raise MarkerError(f"source must be one of {sorted(MARKER_SOURCES)}, not {source!r}")
    if source == "git":
        for key in ("commit", "tree"):
            value = marker[key]
            if not isinstance(value, str) or _OBJECT_ID.fullmatch(value) is None:
                raise MarkerError(f"{key} is not a 40-hex git object id: {value!r}")
    elif marker["commit"] is not None or marker["tree"] is not None:
        raise MarkerError("a folder build has no commit and no tree; both must be null")
    stamp = marker["built_utc"]
    if not isinstance(stamp, str) or _UTC_STAMP.fullmatch(stamp) is None:
        raise MarkerError(f"built_utc is not YYYY-MM-DDTHH:MM:SSZ: {stamp!r}")
    try:
        datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise MarkerError(f"built_utc is not a real time: {stamp!r}") from error
    return marker


def _check_prefix(properties: dict[str, str], blobs: dict[str, bytes]) -> tuple[list[str], bool]:
    """The header's prefix must be the one addon/$PBOPREFIX$ declares at the ref."""
    declared = blobs.get(PREFIX_FILE)
    if declared is None:
        return [f"prefix FAIL: addon/{PREFIX_FILE} is missing at the ref, so nothing says what the prefix must be"], False
    try:
        expected = declared.decode("utf-8").strip()
    except UnicodeDecodeError:
        return [f"prefix FAIL: addon/{PREFIX_FILE} at the ref is not UTF-8"], False
    actual = properties.get("prefix")
    if actual is None:
        return [f"prefix FAIL: the PBO header has no prefix property; addon/{PREFIX_FILE} says {expected!r}"], False
    if actual != expected:
        return [f"prefix FAIL: the PBO header says {actual!r}; addon/{PREFIX_FILE} at the ref says {expected!r}"], False
    return [f"prefix OK {actual}"], True


def _check_marker(markers: list[bytes], commit: str, tree: str) -> tuple[list[str], bool]:
    if not markers:
        return [
            f"marker MISSING: the PBO root has no {MARKER_NAME}, so the PBO does not say "
            "which commit built it (built before the marker existed, or not by "
            "tools/pack-addon.ps1)"
        ], False
    try:
        marker = parse_marker(markers[0])
    except MarkerError as error:
        return [f"marker INVALID: {error}"], False
    lines = ["marker " + " ".join(f"{key}={marker[key]}" for key in ("commit", "tree", "built_utc", "source"))]
    if marker["source"] != "git":
        lines.append("marker FAIL: built from a folder (-Source), not from a commit")
        return lines, False
    if marker["commit"] != commit:
        lines.append(f"marker FAIL: commit {marker['commit']} is not the ref's commit {commit}")
        if marker["tree"] == tree:
            lines.append(
                "marker note: its addon/ tree equals the ref's, so the files can match; "
                "the PBO was still built from another commit"
            )
        return lines, False
    if marker["tree"] != tree:
        lines.append(f"marker FAIL: tree {marker['tree']} is not the ref's addon/ tree {tree}")
        return lines, False
    lines.append("marker OK")
    return lines, True


def compare(
    entries: list[tuple[str, bytes]],
    blobs: dict[str, bytes],
    commit: str,
    tree: str,
    properties: dict[str, str],
) -> tuple[list[str], bool]:
    """Report lines and the verdict for a PBO against addon/ at one commit.

    ``entries`` and ``properties`` come from read_pbo; ``blobs`` maps every file
    under addon/ (path below addon/, forward slashes) to its bytes; ``commit`` and
    ``tree`` are the ref's commit and addon/ tree ids. Names match exactly: the
    stage keeps git's spelling, so a case difference is a different file.
    """
    lines: list[str] = []
    ok = True
    matched: set[str] = set()
    folded: set[str] = set()
    markers: list[bytes] = []
    for name, data in entries:
        rel = name.replace("\\", "/")
        if rel.casefold() in folded:
            lines.append(f"{rel:60s} {len(data):8d} DUPLICATE")
            ok = False
            continue
        folded.add(rel.casefold())
        if rel == MARKER_NAME:
            markers.append(data)
            lines.append(f"{rel:60s} {len(data):8d} marker")
            continue
        blob = blobs.get(rel)
        if blob is None:
            state = "MISSING_IN_GIT"
        else:
            matched.add(rel)
            if blob == data:
                state = "exact"
            elif blob.replace(b"\r\n", b"\n") == data.replace(b"\r\n", b"\n"):
                state = "DIFF-EOL"
            else:
                state = "DIFF"
        if state != "exact":
            ok = False
        lines.append(f"{rel:60s} {len(data):8d} {state}")
    for rel in sorted(blobs):
        if rel.casefold() == MARKER_NAME:
            lines.append(
                f"{rel:60s} {'':8s} RESERVED (git tracks the marker's name; "
                "tools/pack-addon.ps1 refuses to pack that tree)"
            )
            ok = False
        elif rel not in matched:
            lines.append(f"{rel:60s} {'':8s} NOT_IN_PBO")
            ok = False
    prefix_lines, prefix_ok = _check_prefix(properties, blobs)
    lines.extend(prefix_lines)
    marker_lines, marker_ok = _check_marker(markers, commit, tree)
    lines.extend(marker_lines)
    return lines, ok and prefix_ok and marker_ok


def _git(repo: str, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", repo, *args],
            input=stdin,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise GitError(f"cannot run git: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args)} failed with exit {completed.returncode}: {detail}")
    return completed.stdout


def resolve_ref(repo: str, ref: str) -> tuple[str, str]:
    """(commit id, addon/ tree id) of ``ref`` in ``repo``."""
    commit = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode("ascii").strip()
    tree = _git(repo, "rev-parse", "--verify", f"{commit}:addon").decode("ascii").strip()
    kind = _git(repo, "cat-file", "-t", tree).decode("ascii").strip()
    if kind != "tree":
        raise GitError(f"addon is a {kind}, not a directory, at {commit}")
    return commit, tree


def addon_blobs(repo: str, commit: str, tree: str) -> dict[str, bytes]:
    """Every file of the addon/ tree ``tree`` (of ``commit``): path below addon/ -> bytes."""
    listed: list[tuple[str, str]] = []
    for record in _git(repo, "ls-tree", "-r", "-z", tree).split(b"\x00"):
        if not record:
            continue
        meta, _, path_bytes = record.partition(b"\t")
        mode, kind, oid = meta.decode("ascii").split(" ")
        path = path_bytes.decode("utf-8")
        if mode in ("120000", "160000") or kind != "blob":
            raise GitError(
                f"addon/{path} at {commit} is a symlink or a submodule (mode {mode}); "
                "tools/pack-addon.ps1 refuses to pack such a tree"
            )
        listed.append((path, oid))
    output = _git(repo, "cat-file", "--batch", stdin=b"".join(f"{oid}\n".encode("ascii") for _, oid in listed))
    blobs: dict[str, bytes] = {}
    pos = 0
    for path, oid in listed:
        newline = output.find(b"\n", pos)
        header = output[pos:newline].decode("ascii", "replace").split(" ") if newline >= 0 else []
        if len(header) != 3 or header[0] != oid or header[1] != "blob" or not header[2].isdigit():
            raise GitError(f"git cat-file answered {' '.join(header)!r} for addon/{path}")
        start = newline + 1
        size = int(header[2])
        if start + size > len(output):
            raise GitError(f"git cat-file cut addon/{path} short")
        blobs[path] = output[start:start + size]
        pos = start + size + 1
    return blobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pbo_provenance.py",
        description=(
            "Compare a built PBO file by file with addon/ at a git ref and check that "
            f"its {MARKER_NAME} names that ref's commit. Exit 0 only when everything "
            "matches, 1 on any mismatch, 2 when the check cannot run."
        ),
    )
    parser.add_argument("pbo", help="the built .pbo file")
    parser.add_argument("repo", help="a checkout of this repository")
    parser.add_argument("ref", help="the git ref the PBO should have been built from")
    options = parser.parse_args(argv)
    if options.ref.startswith("-"):
        parser.error(f"ref must not start with '-': {options.ref!r}")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    try:
        data = Path(options.pbo).read_bytes()
    except OSError as error:
        print(f"cannot read {options.pbo}: {error}", file=sys.stderr)
        return 2
    try:
        properties, entries = read_pbo(data)
    except PboFormatError as error:
        print(f"{options.pbo} is not a PBO this tool can compare: {error}", file=sys.stderr)
        return 2
    try:
        commit, tree = resolve_ref(options.repo, options.ref)
        blobs = addon_blobs(options.repo, commit, tree)
    except GitError as error:
        print(error, file=sys.stderr)
        return 2
    print(f"pbo {options.pbo} {len(data)} B sha256 {hashlib.sha256(data).hexdigest().upper()}")
    print(f"props {properties}")
    print(f"ref {options.ref} commit {commit} addon/ tree {tree}")
    lines, ok = compare(entries, blobs, commit, tree, properties)
    for line in lines:
        print(line)
    print("PROVENANCE", "OK" if ok else "FAIL", f"entries={len(entries)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
