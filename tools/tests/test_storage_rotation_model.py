"""In-memory crash model of the storage rotation transaction.

A cut freezes the filesystem and drops every later mutation, including
cleanup. Returnable errors are not this enumerator.
"""

from __future__ import annotations

import builtins
import copy
import hashlib
import json
import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests._tiers import slow_test

from dayz_mcp import dayz_test_storage as storage
from dayz_mcp.process_lifecycle import (
    RunManifestStore,
    RunRecord,
    _AMBIGUOUS_PENDING_ROTATION,
    _pending_completed_rotation,
    _storage_observations_from_payload,
)
from dayz_mcp.runtime_state import RuntimePaths


SEAL_A = "a" * 64
SEAL_X = "b" * 64
SEAL_Y = "d" * 64
SEAL_OTHER = "c" * 64
TXID = "1" * 32
PROJECT = "DayZ_MCP"
MISSION = "M:\\mission"
WORLD = b"world"


def _canonical_marker(seal: str, project: str = PROJECT) -> bytes:
    return json.dumps(
        storage._marker_document(seal, project),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))

# The enumeration replays the same few byte strings hundreds of thousands of
# times; these caches only memoize pure functions of those bytes.
_SHA_CACHE: dict[bytes, str] = {}
_JSON_CACHE: dict[bytes, object] = {}
_VALID_CACHE: dict[tuple[bytes, str], dict[str, object] | None] = {}


def _digest(raw: bytes) -> str:
    digest = _SHA_CACHE.get(raw)
    if digest is None:
        digest = hashlib.sha256(raw).hexdigest()
        _SHA_CACHE[raw] = digest
    return digest


def _loaded_json(raw: bytes) -> object | None:
    try:
        return _JSON_CACHE[raw]
    except KeyError:
        pass
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        document = None
    _JSON_CACHE[raw] = document
    return document


def _cached_valid_journal(raw: bytes, txid: str) -> dict[str, object] | None:
    key = (raw, txid)
    document = _VALID_CACHE.get(key, False)
    if document is False:
        parsed = _loaded_json(raw)
        document = storage._valid_journal(parsed, txid) if parsed is not None else None
        _VALID_CACHE[key] = document
    return document


class _Handle:
    def __init__(self, fs: "MemFS", path: str, mode: str) -> None:
        self._fs = fs
        self._path = path
        self._mode = mode
        self._pos = 0
        self._closed = False

    def write(self, data: object) -> int:
        if self._closed or "r" in self._mode:
            raise OSError("write")
        payload = bytes(data)  # type: ignore[arg-type]
        accepted = self._fs.accept_write(self._path, payload)
        node = self._fs.nodes.get(self._path)
        current = bytearray(node[1]) if node and node[0] == "file" else bytearray()
        current[self._pos : self._pos + len(accepted)] = accepted
        spelling = node[2] if node is not None and len(node) >= 3 else os.path.basename(self._path)
        self._fs.nodes[self._path] = ("file", bytes(current), spelling)
        self._pos += len(accepted)
        if self._fs.prefix_pending:
            self._fs.prefix_pending = False
            self._fs._freeze()
            raise _Abandoned()
        self._fs._after("after" if self._fs.cut and self._fs.cut[0] == self._fs.index - 1 and self._fs.cut[1] == "after" else "")
        return len(accepted)

    def read(self, size: int = -1) -> bytes:
        if self._closed:
            raise OSError("closed")
        decision = self._fs.touch("read")
        node = self._fs.nodes.get(self._path)
        blob = node[1] if node and node[0] == "file" else b""
        requested = -1 if size is None else int(size)
        self._fs.read_sizes.append(requested)
        bounded = self._fs.one_byte_reads or self._fs.digest_short > 0
        if bounded and requested < 0:
            raise OSError("unbounded read")
        if size is None or size < 0:
            chunk = blob[self._pos :]
        else:
            take = size
            if self._fs.one_byte_reads:
                take = 1
            elif self._fs.digest_short > 0:
                take = min(size, self._fs.digest_short)
            chunk = blob[self._pos : self._pos + take]
        self._pos += len(chunk)
        self._fs.bytes_consumed += len(chunk)
        self._fs.consumed_by_path[self._path] = (
            self._fs.consumed_by_path.get(self._path, 0) + len(chunk)
        )
        self._fs._after(decision)
        return chunk

    def flush(self) -> None:
        self._fs._after(self._fs.touch("flush"))

    def close(self) -> None:
        if not self._closed:
            decision = self._fs.touch("close")
            self._closed = True
            self._fs._after(decision)

    def fileno(self) -> int:
        return 3

    def __enter__(self) -> "_Handle":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class _Abandoned(BaseException):
    """Abrupt death. Not an Exception, so production cleanup does not run."""


class MemFS:
    def __init__(self) -> None:
        self.nodes: dict[str, tuple] = {_norm(MISSION): ("dir", b"")}
        self.trace: list[tuple[str, int]] = []
        self.index = 0
        self.cut: tuple | None = None
        self.frozen = False
        self.snapshot: dict | None = None
        self.short_at: int | None = None
        self.prefix_pending = False
        self.one_byte_reads = False
        self.digest_short = 0
        self.read_sizes: list[int] = []
        self.bytes_consumed = 0
        self.consumed_by_path: dict[str, int] = {}

    def clone_nodes(self) -> dict:
        # Values are immutable tuples of bytes. A shallow copy is a snapshot.
        return dict(self.nodes)

    def restore(self, nodes: dict) -> None:
        self.nodes = dict(nodes)
        self.index = 0
        self.frozen = False
        self.snapshot = None
        self.cut = None
        self.short_at = None
        self.prefix_pending = False
        self.trace = []
        self.read_sizes = []
        self.bytes_consumed = 0
        self.consumed_by_path = {}

    def _freeze(self) -> None:
        if not self.frozen:
            self.snapshot = self.clone_nodes()
            self.frozen = True

    def touch(self, kind: str, size: int = 0) -> str:
        current = self.index
        self.index += 1
        if self.cut is None and self.short_at is None:
            self.trace.append((kind, size))
        if self.frozen:
            raise _Abandoned()
        if self.short_at == current and kind == "write":
            return "short"
        if self.cut is not None and self.cut[0] == current:
            phase = self.cut[1]
            if phase == "before":
                self._freeze()
                raise _Abandoned()
            if phase == "prefix":
                return "prefix"
            if phase == "after":
                return "after"
        return "ok"

    def _after(self, decision: str) -> None:
        if decision == "after":
            self._freeze()
            raise _Abandoned()

    def accept_write(self, path: str, payload: bytes) -> bytes:
        decision = self.touch("write", len(payload))
        if decision == "short":
            return payload[:1] if payload else payload
        if decision == "prefix":
            keep = int(self.cut[2]) if self.cut is not None else 0
            self.prefix_pending = True
            return payload[:keep]
        if decision == "after":
            return payload
        return payload

    def kind(self, path: str) -> str:
        node = self.nodes.get(_norm(path))
        if node is None:
            return "absent"
        return str(node[0])

    def stat(self, path: str) -> os.stat_result:
        decision = self.touch("stat")
        kind = self.kind(path)
        if kind == "absent":
            self._after(decision)
            raise FileNotFoundError(path)
        if kind == "dir":
            mode = stat.S_IFDIR
        elif kind == "link":
            mode = stat.S_IFLNK
        elif kind == "other":
            mode = stat.S_IFCHR
        else:
            mode = stat.S_IFREG
        self._after(decision)
        return os.stat_result((mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))

    def listdir(self, path: str) -> list[str]:
        decision = self.touch("listdir")
        parent = _norm(path)
        if self.kind(parent) != "dir":
            raise FileNotFoundError(path)
        prefix = parent + "\\"
        names: set[str] = set()
        for key, node in self.nodes.items():
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix) :]
            if "\\" in rest:
                continue
            # Directory-entry spelling is not the Win32 lookup key. normcase
            # folds `STORAGE_1...` onto `storage_1...`; the entry the scanner
            # sees has to stay the bytes open() was given.
            if len(node) >= 3 and isinstance(node[2], str):
                names.add(node[2])
            else:
                names.add(rest)
        listed = sorted(names)
        self._after(decision)
        return listed

    def open(self, path: str, mode: str = "r") -> _Handle:
        decision = self.touch("open")
        normal = _norm(path)
        binary = "b" in mode
        if not binary:
            raise OSError("binary only")
        leaf = os.path.basename(os.path.normpath(path))
        if "x" in mode:
            if self.kind(normal) != "absent":
                raise FileExistsError(path)
            self.nodes[normal] = ("file", b"", leaf)
        elif "w" in mode:
            self.nodes[normal] = ("file", b"", leaf)
        elif "r" in mode:
            if self.kind(normal) != "file":
                self._after(decision)
                raise FileNotFoundError(path)
        self._after(decision)
        return _Handle(self, normal, mode)

    def rename(self, src: str, dst: str) -> None:
        decision = self.touch("rename")
        self._move(src, dst, overwrite=False)
        self._after(decision)

    def replace(self, src: str, dst: str) -> None:
        decision = self.touch("replace")
        self._move(src, dst, overwrite=True)
        self._after(decision)

    def unlink(self, path: str) -> None:
        decision = self.touch("unlink")
        normal = _norm(path)
        node = self.nodes.get(normal)
        if node is None or node[0] != "file":
            self._after(decision)
            raise FileNotFoundError(path)
        del self.nodes[normal]
        self._after(decision)

    def fsync(self, fd: int) -> None:
        del fd
        decision = self.touch("fsync")
        self._after(decision)

    def _move(self, src: str, dst: str, *, overwrite: bool) -> None:
        source = _norm(src)
        dest = _norm(dst)
        if self.kind(source) == "absent":
            raise FileNotFoundError(src)
        if self.kind(dest) != "absent":
            if not overwrite or self.kind(dest) != "file" or self.kind(source) != "file":
                raise FileExistsError(dst)
            del self.nodes[dest]
        keys = [key for key in list(self.nodes) if key == source or key.startswith(source + "\\")]
        dest_leaf = os.path.basename(os.path.normpath(dst))
        for key in keys:
            suffix = key[len(source) :]
            node = self.nodes.pop(key)
            if key == source:
                node = (node[0], node[1], dest_leaf)
            self.nodes[dest + suffix] = node


def _install(fs: MemFS) -> dict[str, object]:
    saved = {
        "stat": os.stat,
        "lstat": os.lstat,
        "listdir": os.listdir,
        "rename": os.rename,
        "replace": os.replace,
        "unlink": os.unlink,
        "fsync": os.fsync,
        "open": builtins.open,
        "isdir": os.path.isdir,
    }
    os.stat = fs.stat  # type: ignore[assignment]
    os.lstat = fs.stat  # type: ignore[assignment]
    os.listdir = fs.listdir  # type: ignore[assignment]
    os.rename = fs.rename  # type: ignore[assignment]
    os.replace = fs.replace  # type: ignore[assignment]
    os.unlink = fs.unlink  # type: ignore[assignment]
    os.fsync = fs.fsync  # type: ignore[assignment]

    def _open(path, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(path, str) and _norm(path).startswith(_norm("M:\\")):
            return fs.open(path, mode)
        return saved["open"](path, mode, *args, **kwargs)

    builtins.open = _open  # type: ignore[assignment]

    def _isdir(path: object) -> bool:
        # Python 3.14's ntpath.isdir is a Win32 builtin and ignores os.stat.
        # Replay classifies the reserved backup with isdir, so the model has
        # to answer from the same nodes the patched stat sees.
        return isinstance(path, str) and fs.kind(path) == "dir"

    os.path.isdir = _isdir  # type: ignore[assignment]
    return saved


def _restore(saved: dict[str, object]) -> None:
    os.stat = saved["stat"]  # type: ignore[assignment]
    os.lstat = saved["lstat"]  # type: ignore[assignment]
    os.listdir = saved["listdir"]  # type: ignore[assignment]
    os.rename = saved["rename"]  # type: ignore[assignment]
    os.replace = saved["replace"]  # type: ignore[assignment]
    os.unlink = saved["unlink"]  # type: ignore[assignment]
    os.fsync = saved["fsync"]  # type: ignore[assignment]
    builtins.open = saved["open"]  # type: ignore[assignment]
    os.path.isdir = saved["isdir"]  # type: ignore[assignment]


def _seed(fs: MemFS, marker: bytes | None) -> None:
    root = _norm(MISSION)
    storage_root = _norm(os.path.join(MISSION, storage.STORAGE_NAME))
    players = _norm(os.path.join(storage_root, "players"))
    fs.nodes = {
        root: ("dir", b""),
        storage_root: ("dir", b""),
        players: ("dir", b""),
        _norm(os.path.join(players, "p.bin")): ("file", WORLD),
    }
    if marker is not None:
        fs.nodes[_norm(os.path.join(MISSION, storage.MARKER_NAME))] = ("file", marker)


def _tree(nodes: dict, root: str) -> dict[str, bytes]:
    base = _norm(root)
    if nodes.get(base, ("absent",))[0] != "dir":
        return {}
    found: dict[str, bytes] = {}
    prefix = base + "\\"
    for key, node in nodes.items():
        if key.startswith(prefix) and node[0] == "file":
            found[key[len(prefix) :]] = node[1]
    return found


def _world_ok(nodes: dict, original: dict[str, bytes]) -> bool:
    # Same home set as one `_tree` call per candidate root, accumulated in a
    # single walk: W, plus every `storage_1.modset-*` directory that is not a
    # `*.marker.json` name. The world or the reserved backup must hold exactly
    # the original tree.
    mission = _norm(MISSION)
    home = _norm(os.path.join(MISSION, storage.STORAGE_NAME))
    prefix = mission + "\\"
    w: dict[str, bytes] = {}
    backups: dict[str, dict[str, bytes]] = {}
    for key, node in nodes.items():
        if not key.startswith(prefix) or node[0] != "file":
            continue
        rel = key[len(prefix) :]
        if key.startswith(home + "\\"):
            w[rel[len(home) - len(prefix) + 1 :]] = node[1]
            continue
        top, slash, rest = rel.partition("\\")
        if (
            not slash
            or ".marker.json" in top
            or not top.startswith(storage.STORAGE_NAME + ".modset-")
        ):
            continue
        backups.setdefault(top, {})[rest] = node[1]
    homes = [tree for tree in ([w] if w else []) + [tree for tree in backups.values() if tree]]
    return any(tree == original for tree in homes) and original in homes


def _marker_kept(nodes: dict, marker: bytes | None) -> bool:
    if marker is None:
        return True
    return any(node[0] == "file" and node[1] == marker for node in nodes.values())


def _node_name(key: str, node: tuple) -> str:
    """Directory-entry spelling of one mission-relative path.

    The node key is the Win32 lookup identity (case folded). A third tuple
    slot, when present, is the spelling `open` or `rename` actually used.
    """
    prefix = _norm(MISSION) + "\\"
    relative = key[len(prefix) :] if key.startswith(prefix) else key
    spelling = node[2] if len(node) >= 3 and isinstance(node[2], str) else None
    if spelling is None:
        return relative
    if "\\" not in relative:
        return spelling
    parent, _leaf = relative.rsplit("\\", 1)
    return parent + "\\" + spelling


def _txid_from_journal_name(name: str) -> str | None:
    if not name.startswith(storage.JOURNAL_PREFIX) or "\\" in name:
        return None
    rest = name[len(storage.JOURNAL_PREFIX) :]
    if rest.endswith(storage.JOURNAL_COMPLETED_SUFFIX):
        txid = rest[: -len(storage.JOURNAL_COMPLETED_SUFFIX)]
    elif rest.endswith(storage.JOURNAL_SUFFIX):
        txid = rest[: -len(storage.JOURNAL_SUFFIX)]
    else:
        return None
    if storage._TXID.fullmatch(txid) is None:
        return None
    return txid


def _published_replay_row(nodes: dict, name: str, document: dict) -> tuple | None:
    """What lifecycle replay consumes from one completed journal, or None."""
    if document.get("phase") != storage.PHASE_MARKER_PUBLISHED:
        return None
    backup = document.get("storage_backup")
    if not isinstance(backup, str):
        return None
    home = nodes.get(_norm(os.path.join(MISSION, backup)))
    if home is None or home[0] != "dir":
        return None
    return (
        "published",
        document.get("phase"),
        document.get("new_seal"),
        document.get("project"),
        backup,
        document.get("marker_backup"),
    )


def _eligible_abort_record(nodes: dict, document: dict) -> bool:
    """A validated prepared completion whose reserved backups are gone."""
    if document.get("phase") != storage.PHASE_PREPARED:
        return False
    for field in ("storage_backup", "marker_backup"):
        reserved = document.get(field)
        if not isinstance(reserved, str):
            return False
        if nodes.get(_norm(os.path.join(MISSION, reserved))) is not None:
            return False
    return True


def _oracle_family_aliases(nodes: dict) -> list[str]:
    """Journal-family spellings that are not their Win32 identity.

    Independent of `_active_journals`: it reads directory-entry spelling off
    the snapshot and applies the same identity rule the scanner documents.
    """
    prefix = _norm(MISSION) + "\\"
    found = []
    for key, node in nodes.items():
        if not key.startswith(prefix) or node[0] != "file":
            continue
        name = _node_name(key, node)
        if "\\" in name or ".modset.tmp-" in name:
            continue
        identity = storage._win32_file_identity(name)
        if identity.startswith(storage.JOURNAL_PREFIX) and name != identity:
            found.append(name)
    return sorted(found)


def _canonical_completed_name(txid: str) -> str:
    return storage.JOURNAL_PREFIX + txid + storage.JOURNAL_COMPLETED_SUFFIX


def _closure_key(nodes: dict) -> tuple:
    """Fixed-point equivalence. Exact dedup stays on `_auth_key`.

    Published completions keep their canonical name, txid and bytes: two
    records that replay the same backup still collide differently when the
    caller id matches one filename and not the other. A directory-entry
    alias keeps its spelling for the same reason. The only abstraction is a
    validated consumed retry prefix (canonical prepared completion, D and K
    absent, chained by `derived_txid`), and that row keeps the next usable
    id. Replay still records phase, seal A, project, backup names and
    whether the candidate set is unique.
    """
    mission = _norm(MISSION)
    prefix = mission + "\\"
    rows = []
    published: list[tuple] = []
    eligible: list[str] = []
    for key in sorted(nodes):
        if not key.startswith(prefix):
            continue
        node = nodes[key]
        name = _node_name(key, node)
        if ".modset.tmp-" in name:
            continue
        if node[0] == "dir":
            rows.append((name, "dir"))
            continue
        # A link or other node can carry journal bytes and still collide.
        # Only a real file may enter the eligible-prefix abstraction.
        if node[0] != "file":
            rows.append((name, node[0], _digest(node[1])))
            continue
        completed = name.endswith(storage.JOURNAL_COMPLETED_SUFFIX)
        if completed:
            document = _loaded_json(node[1])
            txid = document.get("txid") if isinstance(document, dict) else None
            canonical = (
                isinstance(txid, str)
                and name == _canonical_completed_name(txid)
                and storage._win32_file_identity(name) == name
            )
            validated = (
                _cached_valid_journal(node[1], txid)
                if canonical and isinstance(txid, str)
                else None
            )
            if (
                validated is not None
                and validated.get("phase") == storage.PHASE_MARKER_PUBLISHED
            ):
                replay = _published_replay_row(nodes, name, validated)
                if replay is not None:
                    published.append(replay + (name, txid, _digest(node[1])))
                else:
                    rows.append((name, txid, _digest(node[1])))
                continue
            if (
                validated is not None
                and _eligible_abort_record(nodes, validated)
            ):
                eligible.append(txid)
                continue
            rows.append((name, _digest(node[1])))
            continue
        if _txid_from_journal_name(name) is None or name != storage._win32_file_identity(name):
            rows.append((name, _digest(node[1])))
            continue
        document = _loaded_json(node[1])
        if isinstance(document, dict):
            document = dict(document)
            document["txid"] = "*"
            rows.append(("active", json.dumps(document, sort_keys=True)))
        else:
            rows.append(("active", hashlib.sha256(node[1]).hexdigest()))
    rows.append(("eligible-prefixes", tuple(_eligible_prefixes(eligible))))
    rows.append(("published-records", tuple(sorted(published))))
    rows.append((
        "replay",
        "ambiguous" if len(published) > 1 else "unique" if published else "none",
    ))
    return tuple(rows)


def _eligible_prefixes(txids: list[str]) -> tuple:
    """Collapse only a derived chain of validated eligible aborts.

    The head, the length and the next id stay. A txid that is not on such a
    chain cannot be formed here: every eligible id is placed on the chain
    that starts at its earliest ancestor in the set.
    """
    remaining = set(txids)
    derived_from = {storage.derived_txid(txid): txid for txid in remaining}
    heads = sorted(txid for txid in remaining if txid not in derived_from)
    prefixes = []
    for head in heads:
        length = 0
        cursor = head
        while cursor in remaining:
            remaining.remove(cursor)
            length += 1
            cursor = storage.derived_txid(cursor)
        prefixes.append((head, length, cursor))
    return tuple(prefixes)


def _auth_key(nodes: dict) -> tuple:
    mission = _norm(MISSION)
    prefix = mission + "\\"
    rows = []
    for key in sorted(nodes):
        if not key.startswith(prefix):
            continue
        node = nodes[key]
        name = _node_name(key, node)
        if ".modset.tmp-" in name:
            continue
        if node[0] == "dir":
            rows.append((name, "dir"))
        else:
            rows.append((name, node[0], _digest(node[1])))
    return tuple(rows)


def _run(
    fs: MemFS, seal: str, txid: str, nodes: dict | None, cut, short_at
) -> tuple[dict, storage.RotationResult | None]:
    if nodes is not None:
        fs.restore(nodes)
    else:
        fs.index = 0
        fs.frozen = False
        fs.snapshot = None
        fs.trace = []
    fs.cut = cut
    fs.short_at = short_at
    saved = _install(fs)
    result: storage.RotationResult | None = None
    try:
        try:
            result = storage.prepare_storage(
                MISSION, seal=seal, project=PROJECT, now=1_756_000_000.0, txid=txid
            )
        except _Abandoned:
            result = None
    finally:
        _restore(saved)
    if fs.snapshot is not None:
        return fs.snapshot, None
    return fs.clone_nodes(), result


_ROWS = frozenset({"S0", "S1", "S2", "S3", "S6", "S7", "S8", "SA", "SJ"})


def _mission_file(nodes: dict, name: str) -> bytes | None:
    node = nodes.get(_norm(os.path.join(MISSION, name)))
    if node is not None and node[0] == "file":
        return node[1]
    return None


def _active_journal(nodes: dict) -> str | None:
    prefix = _norm(MISSION) + "\\"
    for key, node in nodes.items():
        if not key.startswith(prefix) or node[0] != "file":
            continue
        name = _node_name(key, node)
        if "\\" in name or ".modset.tmp-" in name:
            continue
        if (
            name.startswith(storage.JOURNAL_PREFIX)
            and name.endswith(storage.JOURNAL_SUFFIX)
            and not name.endswith(storage.JOURNAL_COMPLETED_SUFFIX)
        ):
            return name
    return None


def _published_journals(nodes: dict) -> list[tuple[str, dict | None]]:
    """Active and completed journals. A name in the grammar that is not a valid document is None."""
    prefix = _norm(MISSION) + "\\"
    found = []
    for key, node in sorted(nodes.items()):
        if not key.startswith(prefix) or node[0] != "file":
            continue
        name = _node_name(key, node)
        if "\\" in name or ".modset.tmp-" in name:
            continue
        txid = _txid_from_journal_name(name)
        if txid is None:
            continue
        found.append((name, _cached_valid_journal(node[1], txid)))
    return found


def _original_at_reserved_backup(nodes: dict, marker: bytes, journals: list) -> bool:
    digest = _digest(marker)
    for _name, document in journals:
        if document is None or document.get("old_marker_sha256") != digest:
            continue
        if document.get("phase") != storage.PHASE_MARKER_PUBLISHED:
            continue
        if _mission_file(nodes, str(document["marker_backup"])) == marker:
            return True
    return False


def _marker_document_ok(raw: bytes) -> bool:
    document = storage._parse_json(raw)
    if not isinstance(document, dict):
        return False
    return (
        set(document) == {"schema_version", "algorithm", "seal", "project"}
        and document.get("schema_version") == storage.MARKER_SCHEMA_VERSION
        and document.get("algorithm") == storage.MARKER_ALGORITHM
        and isinstance(document.get("seal"), str)
        and storage._SEAL.fullmatch(str(document.get("seal"))) is not None
        and isinstance(document.get("project"), str)
        and bool(document["project"])
    )


def _partial_temps(nodes: dict) -> int:
    prefix = _norm(MISSION) + "\\"
    found = 0
    for key, node in nodes.items():
        if not key.startswith(prefix) or node[0] != "file" or ".modset.tmp-" not in key:
            continue
        if not node[1].endswith(b"}"):
            found += 1
    return found


def _phase_of(raw: bytes | None) -> str | None:
    if raw is None:
        return None
    document = _loaded_json(raw)
    phase = document.get("phase") if isinstance(document, dict) else None
    return phase if isinstance(phase, str) else None


def _row_of(nodes: dict) -> str:
    active = _active_journal(nodes)
    if active is not None:
        phase = _phase_of(_mission_file(nodes, active))
        world = nodes.get(_norm(os.path.join(MISSION, storage.STORAGE_NAME)))
        if phase == storage.PHASE_PREPARED and world is not None and world[0] == "dir":
            return "S1"
        if phase == storage.PHASE_PREPARED:
            return "S2"
        if phase == storage.PHASE_STORAGE_MOVED:
            return "S3"
        if phase == storage.PHASE_MARKER_PUBLISHED:
            return "S6"
        return "SJ"
    prefix = _norm(MISSION) + "\\"
    completed = None
    for key, node in nodes.items():
        if key.startswith(prefix) and node[0] == "file" and key.endswith(storage.JOURNAL_COMPLETED_SUFFIX):
            completed = node[1]
    phase = _phase_of(completed)
    if phase == storage.PHASE_PREPARED:
        return "SA"
    if phase == storage.PHASE_MARKER_PUBLISHED:
        marker = _mission_file(nodes, storage.MARKER_NAME)
        if marker == _canonical_marker(SEAL_A):
            return "S7"
        return "S8"
    world = nodes.get(_norm(os.path.join(MISSION, storage.STORAGE_NAME)))
    if world is not None and world[0] == "dir":
        return "S0"
    return "S8"


def _cuts(trace: list[tuple[str, int]]) -> list:
    cuts = []
    for index, (kind, size) in enumerate(trace):
        cuts.append((index, "before", None))
        cuts.append((index, "after", None))
        if kind == "write":
            for keep in range(size):
                cuts.append((index, "prefix", keep))
    return cuts


class RotationModelTest(unittest.TestCase):
    # The full producer/cut/recovery enumeration takes ~40 s, far over the 0.2 s
    # slow-tier line in tests/_tiers.py. The gate's required-module passes and the
    # whole suite run it; only the fast-tier edit loop leaves it out.
    @slow_test
    def test_depth_two_fixed_point_preserves_world_and_marker(self) -> None:
        originals: list[bytes | None] = [
            None,
            b"",
            _canonical_marker(SEAL_OTHER),
            json.dumps(json.loads(_canonical_marker(SEAL_A).decode("utf-8"))).encode("utf-8"),
            _canonical_marker(SEAL_A),
        ]
        original_world = _tree(
            _seed_nodes(None), os.path.join(MISSION, storage.STORAGE_NAME)
        )
        rows: dict[str, tuple] = {}
        edges: set[tuple[str, str]] = set()
        partial_temps = 0
        for marker in originals:
            seed = _seed_nodes(marker)
            fs = MemFS()
            clean, produced = _run(fs, SEAL_A, TXID, seed, None, None)
            self.assertIsNotNone(produced)
            assert produced is not None
            self.assertTrue(produced.launch_allowed, produced)
            self._check(clean, original_world, marker, SEAL_A)
            trace = list(fs.trace)
            layer: dict[tuple, dict] = {_auth_key(clean): clean}
            self._note(clean, rows, edges, None)
            for cut in _cuts(trace):
                snap, crashed = _run(fs, SEAL_A, TXID, seed, cut, None)
                self.assertIsNone(crashed)
                self._check(snap, original_world, marker, None)
                partial_temps += _partial_temps(snap)
                layer.setdefault(_auth_key(snap), snap)
                self._note(snap, rows, edges, _row_of(seed))
            for index, (kind, _size) in enumerate(trace):
                if kind != "write":
                    continue
                snap, finished = _run(fs, SEAL_A, TXID, seed, None, index)
                self.assertIsNotNone(finished)
                assert finished is not None
                self.assertTrue(finished.launch_allowed, finished)
                self._check(snap, original_world, marker, SEAL_A)
                layer.setdefault(_auth_key(snap), snap)
            reachable: dict[tuple, dict] = dict(layer)
            for seal, txid in ((SEAL_X, "2" * 32), (SEAL_Y, "3" * 32)):
                nxt: dict[tuple, dict] = {}
                for state in list(reachable.values()):
                    probe = MemFS()
                    recovered, result = _run(probe, seal, txid, state, None, None)
                    self.assertIsNotNone(result)
                    assert result is not None
                    self.assertTrue(result.launch_allowed, result)
                    self.assertNotEqual(result.reason, "journal_state_impossible")
                    self.assertIsNone(_active_journal(recovered))
                    self._check(recovered, original_world, marker, seal)
                    parent = _row_of(state)
                    self._note(recovered, rows, edges, parent)
                    nxt[_auth_key(recovered)] = recovered
                    for cut in _cuts(probe.trace):
                        snap, crashed = _run(probe, seal, txid, state, cut, None)
                        self.assertIsNone(crashed)
                        self._check(snap, original_world, marker, None)
                        partial_temps += _partial_temps(snap)
                        nxt[_auth_key(snap)] = snap
                        self._note(snap, rows, edges, parent)
                reachable.update(nxt)
            shapes = {_closure_key(nodes) for nodes in reachable.values()}
            # Depth 3 repeats Y under a fresh caller id. Dedup is `_closure_key`.
            # An occupied prepared abort keeps its own name, so this pass can
            # reveal an abort identity that the previous pass had collapsed
            # while its backup was still absent. That reveal is checked here;
            # it is not required to equal an earlier shape. A second repeat of
            # the same quiescent launch is the block below, and it must not
            # move bytes.
            seen_closure: set[tuple] = set()
            for state in list(reachable.values()):
                state_key = _closure_key(state)
                if state_key in seen_closure:
                    continue
                seen_closure.add(state_key)
                probe = MemFS()
                recovered, result = _run(probe, SEAL_Y, "4" * 32, state, None, None)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertTrue(result.launch_allowed, result)
                self.assertNotEqual(result.reason, "journal_state_impossible")
                self.assertIsNone(_active_journal(recovered))
                self._check(recovered, original_world, marker, SEAL_Y)
                shapes.add(_closure_key(recovered))
                for cut in _cuts(probe.trace):
                    snap, crashed = _run(probe, SEAL_Y, "4" * 32, state, cut, None)
                    self.assertIsNone(crashed)
                    self._check(snap, original_world, marker, None)
                    partial_temps += _partial_temps(snap)
                    shapes.add(_closure_key(snap))
            for state in reachable.values():
                if _active_journal(state) is not None:
                    continue
                if _tree(state, os.path.join(MISSION, storage.STORAGE_NAME)):
                    continue
                if _mission_file(state, storage.MARKER_NAME) != _canonical_marker(SEAL_Y):
                    continue
                again, result = _run(MemFS(), SEAL_Y, "5" * 32, state, None, None)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertTrue(result.launch_allowed, result)
                self.assertEqual(_auth_key(again), _auth_key(state))
                self.assertEqual(_closure_key(again), _closure_key(state))
        self.assertGreaterEqual(partial_temps, 1)
        self.assertIn("S0", rows)
        self.assertTrue(edges)
        self.assertTrue(rows.keys() <= _ROWS)

    def _assert_measured_replay(self, nodes: dict) -> None:
        """Lifecycle replay, read from the completed record, not from the marker alone.

        A mutant that again ignores a reset whose `new_seal` is not the
        requested seal fails here: the marker carries X, the record still
        carries A, and the measured observation must stay a reset.
        """
        world = nodes.get(_norm(os.path.join(MISSION, storage.STORAGE_NAME)))
        if world is not None:
            return
        raw = _mission_file(nodes, storage.MARKER_NAME)
        if raw is None or not _marker_document_ok(raw):
            return
        marker_seal = str(_loaded_json(raw)["seal"])  # type: ignore[index]
        candidates = []
        for name, document in _published_journals(nodes):
            if (
                document is None
                or not name.endswith(storage.JOURNAL_COMPLETED_SUFFIX)
                or document.get("phase") != storage.PHASE_MARKER_PUBLISHED
            ):
                continue
            backup = document.get("storage_backup")
            home = nodes.get(_norm(os.path.join(MISSION, str(backup))))
            if home is not None and home[0] == "dir":
                candidates.append(document)
        fs = MemFS()
        fs.restore(nodes)
        saved = _install(fs)
        try:
            measured = _pending_completed_rotation(MISSION, marker_seal)
        finally:
            _restore(saved)
        if len(candidates) > 1:
            self.assertIs(measured, _AMBIGUOUS_PENDING_ROTATION)
            return
        if len(candidates) == 1:
            self.assertIsInstance(measured, storage.RotationResult)
            assert isinstance(measured, storage.RotationResult)
            self.assertTrue(measured.storage_rotated)
            self.assertEqual(measured.storage_backup, candidates[0]["storage_backup"])
            self.assertEqual(measured.storage_reset_notice, storage.RESET_NOTICE)
            self.assertEqual(measured.reason, "pending_completed_rotation")
            return
        self.assertIsNone(measured)

    def _check(self, nodes, world, marker, seal: str | None) -> None:
        self.assertTrue(_world_ok(nodes, world))
        journals = _published_journals(nodes)
        for name, document in journals:
            self.assertIsNotNone(document, name)
        # A completed journal is history the producer wrote once: `prepared`
        # is a recorded abort, `marker_published` a finished rotation. The
        # production validator also accepts `storage_moved` on it, so the
        # reachable set is enforced here, before any closure equivalence.
        for name, document in journals:
            if document is None or not name.endswith(storage.JOURNAL_COMPLETED_SUFFIX):
                continue
            phase = document.get("phase")
            self.assertIn(
                phase,
                (storage.PHASE_PREPARED, storage.PHASE_MARKER_PUBLISHED),
                f"completed journal {name} carries unreachable phase {phase!r}",
            )
        published = _mission_file(nodes, storage.MARKER_NAME)
        if published is not None and published != marker:
            self.assertTrue(_marker_document_ok(published))
        self._authenticate_original(nodes, marker, journals)
        self._assert_measured_replay(nodes)
        if seal is None or _active_journal(nodes) is not None:
            return
        home = _tree(nodes, os.path.join(MISSION, storage.STORAGE_NAME))
        if home:
            return
        self.assertEqual(published, _canonical_marker(seal))
        if marker is not None:
            self.assertTrue(
                _original_at_reserved_backup(nodes, marker, journals),
                "completed rotation left the original off its reserved backup",
            )

    def _authenticate_original(self, nodes, marker, journals) -> None:
        """E lives at M or at the journal's own marker_backup, according to phase.

        Any other `*.marker.json` does not count. A published phase keeps E at
        the reserved backup even when those bytes equal the canonical marker.
        """
        if marker is None:
            for _name, document in journals:
                if document is None:
                    continue
                if document.get("old_marker_state") == storage.MARKER_ABSENT:
                    self.assertIsNone(
                        _mission_file(nodes, str(document["marker_backup"]))
                    )
            return
        digest = hashlib.sha256(marker).hexdigest()
        # A completed `prepared` journal is an abort. It never reserved K, and a
        # later rotation may have moved E. Only the active journal and a
        # published completion authenticate the original.
        owners = [
            (name, document)
            for name, document in journals
            if document is not None
            and document.get("old_marker_sha256") == digest
            and (
                not name.endswith(storage.JOURNAL_COMPLETED_SUFFIX)
                or document.get("phase") == storage.PHASE_MARKER_PUBLISHED
            )
        ]
        if not owners:
            self.assertEqual(_mission_file(nodes, storage.MARKER_NAME), marker)
            return
        for name, document in owners:
            backup = str(document["marker_backup"])
            at_m = _mission_file(nodes, storage.MARKER_NAME) == marker
            at_k = _mission_file(nodes, backup) == marker
            phase = document.get("phase")
            published = name.endswith(storage.JOURNAL_COMPLETED_SUFFIX) or (
                phase == storage.PHASE_MARKER_PUBLISHED
            )
            if published and phase == storage.PHASE_MARKER_PUBLISHED:
                self.assertTrue(at_k, backup)
            elif phase == storage.PHASE_PREPARED:
                self.assertTrue(at_m)
                self.assertIsNone(_mission_file(nodes, backup))
            elif phase == storage.PHASE_STORAGE_MOVED:
                if _mission_file(nodes, backup) is None:
                    self.assertTrue(at_m)
                else:
                    self.assertTrue(at_k)
            else:
                self.assertTrue(at_k, backup)

    def _note(self, nodes, rows, edges, parent: str | None) -> None:
        row = _row_of(nodes)
        rows.setdefault(row, _auth_key(nodes))
        if parent is not None:
            edges.add((parent, row))

    def test_each_refusal_keeps_the_snapshot_and_spawns_nothing(self) -> None:
        cases = _refusal_cases()
        self.assertGreaterEqual(len(cases), 12)
        for name, plant in cases:
            with self.subTest(name=name):
                with TemporaryDirectory() as room:
                    mission = Path(room) / "mission"
                    mission.mkdir()
                    plant(mission)
                    before = _disk(mission)
                    renames = {"n": 0}
                    real_rename, real_replace = os.rename, os.replace

                    def counting_rename(*args, **kwargs):  # type: ignore[no-untyped-def]
                        renames["n"] += 1
                        return real_rename(*args, **kwargs)

                    def counting_replace(*args, **kwargs):  # type: ignore[no-untyped-def]
                        renames["n"] += 1
                        return real_replace(*args, **kwargs)

                    os.rename, os.replace = counting_rename, counting_replace
                    try:
                        result = storage.prepare_storage(
                            str(mission),
                            seal=SEAL_A,
                            project=PROJECT,
                            now=1.0,
                            txid=TXID,
                        )
                    finally:
                        os.rename, os.replace = real_rename, real_replace
                    self.assertFalse(result.launch_allowed, name)
                    self.assertIn(
                        result.reason,
                        {"journal_state_impossible", "journal_unreadable"},
                        name,
                    )
                    self.assertEqual(_disk(mission), before, name)
                    self.assertEqual(renames["n"], 0, name)

    def test_d3_reseal_then_a_new_world_is_what_rotates(self) -> None:
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            _plant_moved(mission, marker=b"not-json", seal=SEAL_A)
            original = (mission / "storage_1.modset-d3-legacy" / "p.bin").read_bytes()
            recovered = storage.prepare_storage(
                str(mission), seal=SEAL_X, project=PROJECT, now=2.0, txid="2" * 32
            )
            self.assertTrue(recovered.launch_allowed, recovered)
            self.assertEqual(storage.read_marker(str(mission)).seal, SEAL_X)
            self.assertEqual(
                (mission / "storage_1.modset-d3-legacy" / "p.bin").read_bytes(),
                original,
            )
            created = mission / storage.STORAGE_NAME
            created.mkdir()
            (created / "p.bin").write_bytes(b"world-x")
            again = storage.prepare_storage(
                str(mission), seal=SEAL_A, project=PROJECT, now=3.0, txid="3" * 32
            )
            self.assertTrue(again.storage_rotated, again)
            self.assertEqual((mission / str(again.storage_backup) / "p.bin").read_bytes(), b"world-x")
            self.assertEqual(
                (mission / "storage_1.modset-d3-legacy" / "p.bin").read_bytes(),
                original,
            )

    def test_schema1_directory_marker_is_preserved(self) -> None:
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            _plant_moved(mission, marker=None, seal=SEAL_A, old_seal=None)
            marker = mission / storage.MARKER_NAME
            marker.mkdir()
            (marker / "opaque.txt").write_bytes(b"dir-marker")
            result = storage.prepare_storage(
                str(mission), seal=SEAL_A, project=PROJECT, now=4.0, txid="4" * 32
            )
            self.assertTrue(result.launch_allowed, result)
            kept = mission / "storage_1.modset-d3-legacy.marker.json" / "opaque.txt"
            self.assertEqual(kept.read_bytes(), b"dir-marker")

    def test_known_launch_operation_survives_the_observation_log(self) -> None:
        operation = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        loaded = _storage_observations_from_payload(
            [
                {
                    "run_id": "run-1",
                    "storage_rotated": True,
                    "storage_backup": "storage_1.modset-aaaaaaaa",
                    "storage_reset_notice": storage.RESET_NOTICE,
                    "launch_operation_id": operation,
                }
            ]
        )
        self.assertEqual(loaded[0]["launch_operation_id"], operation)
        with TemporaryDirectory() as room:
            root = Path(room)
            paths = RuntimePaths(root, root / "audit", root / "coordination.json", root / "runs.json")
            (root / "runs.json").write_text(
                json.dumps({"version": 1, "runs": []}), encoding="utf-8"
            )
            store = RunManifestStore(paths)
            record = RunRecord(
                "run-2",
                None,
                None,
                "EXITED",
                "",
                "@DayZ_MCP",
                "profiles",
                "mission",
                [],
                operation,
                "ab" * 32,
                True,
                None,
                True,
                "storage_1.modset-bbbbbbbb",
                storage.RESET_NOTICE,
            )
            store.add(record)
            store._runs.clear()
            store._persist_locked()
            reloaded = RunManifestStore(paths)
            self.assertEqual(reloaded.storage_observations()[0]["launch_operation_id"], operation)

    def test_oracle_rejects_original_parked_under_an_unreserved_name(self) -> None:
        original = b"old-invalid"
        nodes, world = _completed_rotation(original)
        reserved = _reserved_backup_name(nodes, original)
        blob = nodes.pop(_norm(os.path.join(MISSION, reserved)))
        nodes[_norm(os.path.join(MISSION, "unrelated.marker.json"))] = blob
        with self.assertRaises(AssertionError):
            self._check(nodes, world, original, SEAL_A)

    def test_oracle_rejects_completed_e_equals_n_left_only_at_m(self) -> None:
        # E equals the published document, so a producer call would reuse the
        # world and never reserve K. The completed image is planted: K is the
        # only copy that satisfies a finished rotation.
        original = _canonical_marker(SEAL_A)
        nodes, world = _planted_completed(original)
        reserved = _reserved_backup_name(nodes, original)
        del nodes[_norm(os.path.join(MISSION, reserved))]
        self.assertEqual(_mission_file(nodes, storage.MARKER_NAME), original)
        with self.assertRaises(AssertionError):
            self._check(nodes, world, original, SEAL_A)

    def test_oracle_rejects_a_malformed_completed_journal(self) -> None:
        original_complete = storage._complete_journal

        def corrupt(mission: str, txid: str) -> None:
            original_complete(mission, txid)
            path = os.path.join(
                mission,
                storage.JOURNAL_PREFIX + txid + storage.JOURNAL_COMPLETED_SUFFIX,
            )
            with open(path, "wb") as handle:
                handle.write(b"{")
                handle.flush()
                os.fsync(handle.fileno())

        storage._complete_journal = corrupt
        try:
            with self.assertRaises(AssertionError):
                self.test_depth_two_fixed_point_preserves_world_and_marker()
        finally:
            storage._complete_journal = original_complete

    def test_completed_journal_storage_moved_phase_is_refused(self) -> None:
        # F1 regression. No row of the matrix keeps a completed journal in
        # `storage_moved`: completion is the recorded abort (`prepared`) or
        # the finished rotation (`marker_published`).
        world = _tree(_seed_nodes(None), os.path.join(MISSION, storage.STORAGE_NAME))
        nodes = _seed_nodes(None)
        del nodes[_norm(os.path.join(MISSION, storage.STORAGE_NAME))]
        del nodes[_norm(os.path.join(MISSION, storage.STORAGE_NAME, "players"))]
        backup = _norm(os.path.join(MISSION, "storage_1.modset-d3-legacy"))
        nodes[backup] = ("dir", b"")
        nodes[_norm(os.path.join(backup, "players"))] = ("dir", b"")
        nodes[_norm(os.path.join(backup, "players", "p.bin"))] = ("file", WORLD)
        nodes[_norm(os.path.join(MISSION, storage.MARKER_NAME))] = (
            "file",
            _canonical_marker(SEAL_A),
        )
        completed = storage.JOURNAL_PREFIX + TXID + storage.JOURNAL_COMPLETED_SUFFIX
        body = _journal(TXID, storage.PHASE_STORAGE_MOVED)
        nodes[_norm(os.path.join(MISSION, completed))] = (
            "file",
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
        with self.assertRaises(AssertionError) as caught:
            self._check(nodes, world, None, SEAL_A)
        self.assertIn(storage.PHASE_STORAGE_MOVED, str(caught.exception))

    def test_wrong_completed_phase_rewrite_is_reported_by_the_gate(self) -> None:
        # F1 negative control: an atomic wrong-phase rewrite of every finished
        # rotation that recorded an absent original must fail the gate.
        original_complete = storage._complete_journal

        def rewrite(mission: str, txid: str) -> None:
            original_complete(mission, txid)
            completed_path = os.path.join(
                mission,
                storage.JOURNAL_PREFIX + txid + storage.JOURNAL_COMPLETED_SUFFIX,
            )
            with open(completed_path, "rb") as handle:
                document = json.loads(handle.read().decode("utf-8"))
            if (
                document.get("old_marker_state") == storage.MARKER_ABSENT
                and document.get("phase") == storage.PHASE_MARKER_PUBLISHED
            ):
                document["phase"] = storage.PHASE_STORAGE_MOVED
                storage._write_json_atomic(completed_path, document, replace=True)

        storage._complete_journal = rewrite
        try:
            with self.assertRaises(AssertionError) as caught:
                self.test_depth_two_fixed_point_preserves_world_and_marker()
        finally:
            storage._complete_journal = original_complete
        self.assertIn(storage.PHASE_STORAGE_MOVED, str(caught.exception))

    def test_trailing_period_alias_is_refused_before_any_rename(self) -> None:
        original = b"old-invalid"
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            mission.mkdir()
            backup = mission / "storage_1.modset-alias"
            backup.mkdir()
            (backup / "p.bin").write_bytes(b"original-world")
            (mission / storage.MARKER_NAME).write_bytes(original)
            _write_journal(
                mission,
                _journal(
                    "1" * 32,
                    storage.PHASE_STORAGE_MOVED,
                    new_seal=SEAL_A,
                    old_seal=None,
                    storage_backup="storage_1.modset-alias",
                    marker_backup="storage_1.",
                    old_marker_state=storage.MARKER_PRESENT_INVALID,
                    old_marker_sha256=hashlib.sha256(original).hexdigest(),
                ),
            )
            before = _disk(mission)
            renames = {"n": 0}
            real_rename, real_replace = os.rename, os.replace

            def counting_rename(*args, **kwargs):  # type: ignore[no-untyped-def]
                renames["n"] += 1
                return real_rename(*args, **kwargs)

            def counting_replace(*args, **kwargs):  # type: ignore[no-untyped-def]
                renames["n"] += 1
                return real_replace(*args, **kwargs)

            os.rename, os.replace = counting_rename, counting_replace
            try:
                result = storage.prepare_storage(
                    str(mission),
                    seal=SEAL_A,
                    project=PROJECT,
                    now=1.0,
                    txid="1" * 32,
                )
            finally:
                os.rename, os.replace = real_rename, real_replace
            self.assertFalse(result.launch_allowed)
            self.assertEqual(result.reason, "journal_unreadable")
            self.assertEqual(_disk(mission), before)
            self.assertEqual(renames["n"], 0)
            self.assertFalse((mission / storage.STORAGE_NAME).exists())

    def test_intact_schema1_new_marker_with_preserved_old_k_is_refused(self) -> None:
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            world = mission / storage.STORAGE_NAME
            (world / "players").mkdir(parents=True)
            (world / "p.bin").write_bytes(b"original-world")
            (mission / storage.MARKER_NAME).write_bytes(_canonical_marker(SEAL_A))
            (mission / "storage_1.modset-d3-legacy.marker.json").write_bytes(
                _canonical_marker(SEAL_OTHER)
            )
            _write_journal(
                mission,
                _journal(
                    "1" * 32,
                    storage.PHASE_PREPARED,
                    schema_version=storage.JOURNAL_SCHEMA_LEGACY,
                    new_seal=SEAL_A,
                    old_seal=SEAL_OTHER,
                ),
            )
            before = _disk(mission)
            journal_name = storage.JOURNAL_PREFIX + ("1" * 32) + storage.JOURNAL_SUFFIX
            result = storage.prepare_storage(
                str(mission),
                seal=SEAL_A,
                project=PROJECT,
                now=1.0,
                txid="2" * 32,
            )
            self.assertFalse(result.launch_allowed)
            self.assertEqual(result.reason, "journal_state_impossible")
            self.assertFalse(result.storage_rotated)
            self.assertEqual(_disk(mission), before)
            self.assertTrue((mission / journal_name).is_file())
            self.assertEqual((world / "p.bin").read_bytes(), b"original-world")

    def test_same_seal_recovery_keeps_the_journal_project_and_reports_rotation(self) -> None:
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            backup = mission / "storage_1.modset-d3-legacy"
            (backup / "players").mkdir(parents=True)
            (backup / "p.bin").write_bytes(b"original-world")
            _write_journal(
                mission,
                _journal(
                    "9" * 32,
                    storage.PHASE_STORAGE_MOVED,
                    new_seal=SEAL_A,
                    old_seal=None,
                    old_marker_state=storage.MARKER_ABSENT,
                    old_marker_sha256=None,
                ),
            )
            result = storage.prepare_storage(
                str(mission),
                seal=SEAL_A,
                project="OtherProject",
                now=2.0,
                txid="2" * 32,
            )
            self.assertTrue(result.launch_allowed, result)
            self.assertTrue(result.storage_rotated, result)
            self.assertEqual(result.decision, storage.DECISION_ROTATE)
            self.assertEqual(
                (mission / storage.MARKER_NAME).read_bytes(),
                _canonical_marker(SEAL_A, PROJECT),
            )
            self.assertIsNone(_active_journal_on(mission))
            again = storage.prepare_storage(
                str(mission),
                seal=SEAL_A,
                project="OtherProject",
                now=3.0,
                txid="3" * 32,
            )
            self.assertTrue(again.launch_allowed, again)
            self.assertFalse(again.storage_rotated)

    def test_f1_occupied_abort_keeps_its_identity_in_the_fixed_point(self) -> None:
        """Two prepared completions that reserve the same live backup are not one state.

        Collapsing every occupied abort to a boolean made caller txid `1`*32
        and an unrelated `9`*32 equivalent, then gave them different results.
        """
        original = _canonical_marker(SEAL_OTHER)
        world = _tree(_seed_nodes(original), os.path.join(MISSION, storage.STORAGE_NAME))
        backup = "storage_1.modset-d3-legacy"
        base = _seed_nodes(original)
        base[_norm(os.path.join(MISSION, backup))] = ("dir", b"")
        base[_norm(os.path.join(MISSION, backup, "p.bin"))] = ("file", b"occupied")

        def plant(txid: str) -> dict:
            snap = dict(base)
            body = _journal(
                txid,
                storage.PHASE_PREPARED,
                new_seal=SEAL_A,
                old_seal=SEAL_OTHER,
                storage_backup=backup,
                marker_backup=backup + ".marker.json",
                old_marker_state=storage.MARKER_PRESENT_VALID,
                old_marker_sha256=hashlib.sha256(original).hexdigest(),
            )
            raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            name = storage.JOURNAL_PREFIX + txid + storage.JOURNAL_COMPLETED_SUFFIX
            snap[_norm(os.path.join(MISSION, name))] = ("file", raw)
            return snap

        occupied = plant("1" * 32)
        unrelated = plant("9" * 32)
        self._check(occupied, world, original, None)
        self._check(unrelated, world, original, None)
        self.assertNotEqual(_closure_key(occupied), _closure_key(unrelated))
        caller, caller_result = _run(MemFS(), SEAL_A, "1" * 32, occupied, None, None)
        other, other_result = _run(MemFS(), SEAL_A, "1" * 32, unrelated, None, None)
        self.assertIsNotNone(caller_result)
        self.assertIsNotNone(other_result)
        assert caller_result is not None and other_result is not None
        self.assertFalse(caller_result.launch_allowed)
        self.assertEqual(caller_result.reason, "backup_name_collision")
        self.assertTrue(other_result.launch_allowed, other_result)
        self.assertEqual(other_result.reason, "seal_changed")
        self.assertTrue(_world_ok(caller, world))
        self.assertTrue(_world_ok(other, world))

    def test_f2_upper_case_journal_spelling_is_not_a_canonical_recovery(self) -> None:
        """MemFS must enumerate the directory entry, not its case-folded key."""
        original = b"original-marker"
        fs = MemFS()
        mission = _norm(MISSION)
        backup = "storage_1.modset-alias"
        backup_key = _norm(os.path.join(MISSION, backup))
        fs.nodes = {
            mission: ("dir", b""),
            backup_key: ("dir", b""),
            _norm(os.path.join(backup_key, "p.bin")): ("file", b"original-world"),
            _norm(os.path.join(MISSION, storage.MARKER_NAME)): ("file", original),
        }
        txid = "1" * 32
        alias = "STORAGE_1.modset.rotation." + txid + ".json"
        body = _journal(
            txid,
            storage.PHASE_PREPARED,
            new_seal=SEAL_A,
            old_seal=None,
            storage_backup=backup,
            marker_backup=backup + ".marker.json",
            old_marker_state=storage.MARKER_PRESENT_INVALID,
            old_marker_sha256=hashlib.sha256(original).hexdigest(),
        )
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        handle = fs.open(os.path.join(MISSION, alias), "xb")
        handle.write(raw)
        handle.close()
        self.assertIn(alias, fs.listdir(MISSION))
        self.assertIsNone(_active_journal(fs.nodes))
        before = fs.clone_nodes()
        after, result = _run(fs, SEAL_X, TXID, before, None, None)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.launch_allowed)
        self.assertEqual(result.reason, "journal_name_invalid")
        self.assertEqual(_auth_key(after), _auth_key(before))
        self.assertEqual(
            _mission_file(after, storage.MARKER_NAME),
            original,
        )

    def test_f3_two_published_records_are_not_the_same_replay(self) -> None:
        unique, _world = _completed_rotation(None)
        twin = dict(unique)
        mission = _norm(MISSION)
        published = [
            (name, document)
            for name, document in _published_journals(unique)
            if name.endswith(storage.JOURNAL_COMPLETED_SUFFIX)
        ]
        self.assertEqual(len(published), 1)
        _name, document = published[0]
        assert document is not None
        copy = dict(document)
        copy["txid"] = "2" * 32
        raw = json.dumps(copy, sort_keys=True, separators=(",", ":")).encode("utf-8")
        twin[_norm(os.path.join(
            MISSION,
            storage.JOURNAL_PREFIX + ("2" * 32) + storage.JOURNAL_COMPLETED_SUFFIX,
        ))] = ("file", raw)
        self.assertNotEqual(_closure_key(unique), _closure_key(twin))
        self.assertIn(("replay", "unique"), _closure_key(unique))
        self.assertIn(("replay", "ambiguous"), _closure_key(twin))

    def test_f4_positive_short_reads_verify_a_published_journal(self) -> None:
        fs = MemFS()
        fs.one_byte_reads = True
        _nodes, result = _run(
            fs, SEAL_A, TXID, _seed_nodes(_canonical_marker(SEAL_OTHER)), None, None
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.launch_allowed, result)
        self.assertNotEqual(result.reason, "write_not_durable")

    def test_f6_oversized_marker_stays_invalid_and_is_preserved_whole(self) -> None:
        raw = _canonical_marker(SEAL_OTHER)
        original = raw + b" " * (65536 - len(raw)) + b"garbage-tail"
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            world = mission / storage.STORAGE_NAME
            world.mkdir(parents=True)
            (world / "p.bin").write_bytes(b"world")
            (mission / storage.MARKER_NAME).write_bytes(original)
            marker = storage.read_marker(str(mission))
            self.assertEqual(marker.state, storage.MARKER_PRESENT_INVALID)
            state, _seal, digest = storage._capture_original_marker(
                str(mission / storage.MARKER_NAME)
            )
            self.assertEqual(state, storage.MARKER_PRESENT_INVALID)
            self.assertEqual(digest, hashlib.sha256(original).hexdigest())
            result = storage.prepare_storage(
                str(mission), seal=SEAL_A, project=PROJECT, now=1.0, txid=TXID
            )
            self.assertTrue(result.launch_allowed, result)
            preserved = (mission / str(result.storage_marker_backup)).read_bytes()
            self.assertEqual(preserved, original)

    def test_f7_mixed_case_completed_name_is_an_alias(self) -> None:
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            mission.mkdir()
            alias = mission / (
                "storage_1.modset.rotation.Garbage.completed.json"
            )
            alias.write_bytes(b"{}")
            before = _disk(mission)
            result = storage.prepare_storage(
                str(mission), seal=SEAL_X, project=PROJECT, now=1.0, txid=TXID
            )
            self.assertFalse(result.launch_allowed)
            self.assertEqual(result.reason, "journal_name_invalid")
            self.assertEqual(_disk(mission), before)
            self.assertFalse((mission / storage.MARKER_NAME).exists())

    def test_m1_upper_case_active_journal_is_not_recovered(self) -> None:
        original = b"original-marker"
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            backup = mission / "storage_1.modset-alias"
            (backup).mkdir(parents=True)
            (backup / "p.bin").write_bytes(b"original-world")
            (mission / storage.MARKER_NAME).write_bytes(original)
            # The prefix case is the alias. The txid stays lowercase.
            name = "STORAGE_1.modset.rotation." + ("a" * 32) + ".json"
            (mission / name).write_bytes(b"{}")
            before = _disk(mission)
            result = storage.prepare_storage(
                str(mission), seal=SEAL_X, project=PROJECT, now=1.0, txid=TXID
            )
            self.assertFalse(result.launch_allowed)
            self.assertEqual(result.reason, "journal_name_invalid")
            self.assertEqual(_disk(mission), before)
            self.assertEqual((mission / storage.MARKER_NAME).read_bytes(), original)

    def test_d3_deeply_nested_journal_is_unreadable(self) -> None:
        payload = b"[" * 30000 + b"0" + b"]" * 30000
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            world = mission / storage.STORAGE_NAME
            world.mkdir(parents=True)
            (world / "p.bin").write_bytes(b"world")
            journal = mission / (
                storage.JOURNAL_PREFIX + ("b" * 32) + storage.JOURNAL_SUFFIX
            )
            journal.write_bytes(payload)
            before = _disk(mission)
            result = storage.prepare_storage(
                str(mission), seal=SEAL_A, project=PROJECT, now=1.0, txid=TXID
            )
            self.assertFalse(result.launch_allowed)
            self.assertEqual(result.reason, "journal_unreadable")
            self.assertEqual(_disk(mission), before)

    def test_e2_public_result_keeps_the_storage_reason(self) -> None:
        from dayz_mcp import dayz_test_tool

        reason = "journal_state_impossible"
        hint = storage.storage_recovery_hint(reason)
        terminal = dayz_test_tool.WorkerTerminal(
            cleanup_degraded=False,
            error_code="storage_recovery_required",
            exit_code=1,
            ok=False,
            run_id=None,
            storage_recovery_reason=reason,
            storage_recovery_hint=hint,
        )
        payload = dayz_test_tool._compact_result(
            terminal=terminal,
            project=PROJECT,
            mode="server",
            started_at=0.0,
            artifacts_paths=[],
        )
        self.assertEqual(payload["error_code"], "storage_recovery_required")
        self.assertEqual(payload["storage_recovery_reason"], reason)
        self.assertEqual(payload["remediation"], hint)

    def test_r3_f1_published_txid_is_not_erased_from_the_fixed_point(self) -> None:
        """Caller `1`*32 collides with its own published completion; `9`*32 does not."""
        original = _canonical_marker(SEAL_OTHER)
        published, world = _completed_rotation(original)
        name, record = next(
            (item, document)
            for item, document in _published_journals(published)
            if document and document.get("phase") == storage.PHASE_MARKER_PUBLISHED
        )
        other = dict(published)
        del other[_norm(os.path.join(MISSION, name))]
        moved = dict(record)
        moved["txid"] = "9" * 32
        other_name = _canonical_completed_name(moved["txid"])
        other[_norm(os.path.join(MISSION, other_name))] = (
            "file",
            json.dumps(moved, sort_keys=True).encode("utf-8"),
            other_name,
        )
        for nodes in (published, other):
            nodes[_norm(os.path.join(MISSION, storage.STORAGE_NAME))] = ("dir", b"")
            nodes[_norm(os.path.join(MISSION, storage.STORAGE_NAME, "new.bin"))] = (
                "file",
                b"new-world",
            )
            self._check(nodes, world, original, None)
        self.assertNotEqual(_closure_key(published), _closure_key(other))
        blocked, blocked_result = _run(MemFS(), SEAL_X, TXID, published, None, None)
        allowed, allowed_result = _run(MemFS(), SEAL_X, TXID, other, None, None)
        self.assertIsNotNone(blocked_result)
        self.assertIsNotNone(allowed_result)
        assert blocked_result is not None and allowed_result is not None
        self.assertFalse(blocked_result.launch_allowed)
        self.assertEqual(blocked_result.reason, "backup_name_collision")
        self.assertTrue(allowed_result.launch_allowed, allowed_result)
        self.assertEqual(allowed_result.reason, "seal_changed")
        self.assertTrue(_world_ok(blocked, world) or _mission_file(blocked, "new.bin") == b"new-world")
        alias = dict(_seed_nodes(_canonical_marker(SEAL_OTHER)))
        alias_name = "STORAGE_1.modset.rotation." + TXID + storage.JOURNAL_COMPLETED_SUFFIX
        alias_body = _journal(
            TXID,
            storage.PHASE_PREPARED,
            old_seal=SEAL_OTHER,
            old_marker_state=storage.MARKER_PRESENT_VALID,
            old_marker_sha256=hashlib.sha256(_canonical_marker(SEAL_OTHER)).hexdigest(),
            storage_backup="storage_1.modset-alias-absent",
            marker_backup="storage_1.modset-alias-absent.marker.json",
        )
        alias[_norm(os.path.join(MISSION, alias_name))] = (
            "file",
            json.dumps(alias_body, sort_keys=True).encode("utf-8"),
            alias_name,
        )
        self.assertNotEqual(_closure_key(alias), _closure_key(_seed_nodes(_canonical_marker(SEAL_OTHER))))
        refused, refused_result = _run(MemFS(), SEAL_A, "2" * 32, alias, None, None)
        self.assertIsNotNone(refused_result)
        assert refused_result is not None
        self.assertFalse(refused_result.launch_allowed)
        self.assertEqual(refused_result.reason, "journal_name_invalid")
        self.assertEqual(_auth_key(refused), _auth_key(alias))

    def test_r3_f1_eligible_prefix_keeps_the_next_usable_id(self) -> None:
        original = _canonical_marker(SEAL_OTHER)
        base = _seed_nodes(original)

        def plant(txid: str) -> dict:
            snap = dict(base)
            body = _journal(
                txid,
                storage.PHASE_PREPARED,
                old_seal=SEAL_OTHER,
                old_marker_state=storage.MARKER_PRESENT_VALID,
                old_marker_sha256=hashlib.sha256(original).hexdigest(),
                storage_backup="storage_1.modset-spent-" + txid[:8],
                marker_backup="storage_1.modset-spent-" + txid[:8] + ".marker.json",
            )
            raw = json.dumps(body, sort_keys=True).encode("utf-8")
            name = _canonical_completed_name(txid)
            snap[_norm(os.path.join(MISSION, name))] = ("file", raw, name)
            return snap

        caller = plant(TXID)
        unrelated = plant("9" * 32)
        self.assertNotEqual(_closure_key(caller), _closure_key(unrelated))
        prefixes = [
            row for row in _closure_key(caller) if row and row[0] == "eligible-prefixes"
        ]
        self.assertEqual(
            prefixes,
            [("eligible-prefixes", ((TXID, 1, storage.derived_txid(TXID)),))],
        )
        self.assertIn(
            ("eligible-prefixes", (("9" * 32, 1, storage.derived_txid("9" * 32)),)),
            _closure_key(unrelated),
        )

    def test_r4_f1_link_abort_is_not_the_same_state_as_a_file(self) -> None:
        """A completed link is not an eligible file abort.

        The file continues into a rotation. The link collides. Both closure
        and the exact snapshot key have to keep that kind.
        """
        original = _canonical_marker(SEAL_OTHER)
        base = _seed_nodes(original)
        body = _journal(
            TXID,
            storage.PHASE_PREPARED,
            old_seal=SEAL_OTHER,
            old_marker_state=storage.MARKER_PRESENT_VALID,
            old_marker_sha256=hashlib.sha256(original).hexdigest(),
            storage_backup="storage_1.modset-spent",
            marker_backup="storage_1.modset-spent.marker.json",
        )
        name = _canonical_completed_name(TXID)
        key = _norm(os.path.join(MISSION, name))
        raw = json.dumps(body, sort_keys=True).encode("utf-8")
        file_nodes = dict(base)
        link_nodes = dict(base)
        file_nodes[key] = ("file", raw, name)
        link_nodes[key] = ("link", raw, name)
        world = _tree(base, os.path.join(MISSION, storage.STORAGE_NAME))
        for nodes in (file_nodes, link_nodes):
            self._check(nodes, world, original, None)
        self.assertNotEqual(_closure_key(file_nodes), _closure_key(link_nodes))
        self.assertNotEqual(_auth_key(file_nodes), _auth_key(link_nodes))
        file_prefixes = [
            row for row in _closure_key(file_nodes) if row and row[0] == "eligible-prefixes"
        ]
        link_prefixes = [
            row for row in _closure_key(link_nodes) if row and row[0] == "eligible-prefixes"
        ]
        self.assertEqual(file_prefixes, [("eligible-prefixes", ((TXID, 1, _retry_txid(TXID)),))])
        self.assertEqual(link_prefixes, [("eligible-prefixes", ())])
        _kept, file_result = _run(MemFS(), SEAL_A, TXID, file_nodes, None, None)
        _kept, link_result = _run(MemFS(), SEAL_A, TXID, link_nodes, None, None)
        self.assertIsNotNone(file_result)
        self.assertIsNotNone(link_result)
        assert file_result is not None and link_result is not None
        self.assertTrue(file_result.launch_allowed, file_result)
        self.assertEqual(file_result.reason, "seal_changed")
        self.assertFalse(link_result.launch_allowed)
        self.assertEqual(link_result.reason, "backup_name_collision")

    def test_r3_f2_reseal_retry_records_the_same_reset_once(self) -> None:
        from dayz_mcp.process_lifecycle import ProcessLifecycle

        original = _canonical_marker(SEAL_OTHER)
        nodes, world = _completed_rotation(original)
        resealed, result = _run(MemFS(), SEAL_X, TXID, nodes, None, None)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.launch_allowed, result)
        self._check(resealed, world, original, SEAL_X)
        published = [
            document
            for _name, document in _published_journals(resealed)
            if document and document.get("phase") == storage.PHASE_MARKER_PUBLISHED
        ]
        self.assertEqual(len(published), 1)
        record = published[0]
        self.assertNotEqual(record["new_seal"], SEAL_X)
        self.assertIsNone(resealed.get(_norm(os.path.join(MISSION, storage.STORAGE_NAME))))
        audits: list[str] = []

        class _Stub:
            _last_storage_recovery_reason = None

            def _audit(self, event: str, *_args: object, **_kwargs: object) -> bool:
                audits.append(event)
                return True

            def _note_audit_row_dropped(self) -> None:
                raise AssertionError("audit dropped")

        stub = _Stub()
        stub._record_storage_rotation = ProcessLifecycle._record_storage_rotation  # type: ignore[attr-defined]
        stub._audit_storage_rotation = ProcessLifecycle._audit_storage_rotation.__get__(  # type: ignore[attr-defined]
            stub, _Stub
        )
        provisional = RunRecord(
            run_id="11111111-1111-4111-8111-111111111111",
            owner_session_id=None,
            owner_lease_id=None,
            state="STARTING",
            label="retry",
            mod=PROJECT,
            profiles="profiles",
            mission=MISSION,
            processes=[],
        )
        fs = MemFS()
        fs.restore(resealed)
        saved = _install(fs)
        try:
            code = ProcessLifecycle._rotate_storage_for_launch(
                stub,  # type: ignore[arg-type]
                {"storage_seal": SEAL_X, "mission": MISSION, "mod": PROJECT},
                provisional.run_id,
                provisional,
            )
        finally:
            _restore(saved)
        self.assertIsNone(code)
        self.assertTrue(provisional.storage_rotated)
        self.assertEqual(provisional.storage_backup, record["storage_backup"])
        self.assertEqual(provisional.storage_reset_notice, storage.RESET_NOTICE)
        self.assertNotIn("lifecycle_storage_rotated", audits)
        self.assertEqual(
            _mission_file(fs.clone_nodes(), storage.MARKER_NAME),
            _canonical_marker(SEAL_X),
        )

    def test_r3_m1_alias_spellings_are_discovered_without_the_scanner(self) -> None:
        original = b"original-marker"
        txid = "a" * 32
        canonical = storage.JOURNAL_PREFIX + txid + storage.JOURNAL_SUFFIX
        aliases = (
            "STORAGE_1.modset.rotation." + txid + ".json",
            canonical + " ",
            canonical + ".",
        )
        for alias in aliases:
            fs = MemFS()
            backup = "storage_1.modset-alias"
            mission = _norm(MISSION)
            backup_key = _norm(os.path.join(MISSION, backup))
            fs.nodes = {
                mission: ("dir", b""),
                backup_key: ("dir", b""),
                _norm(os.path.join(backup_key, "p.bin")): ("file", b"original-world"),
                _norm(os.path.join(MISSION, storage.MARKER_NAME)): ("file", original),
            }
            body = _journal(
                txid,
                storage.PHASE_PREPARED,
                new_seal=SEAL_A,
                old_seal=None,
                storage_backup=backup,
                marker_backup=backup + ".marker.json",
                old_marker_state=storage.MARKER_PRESENT_INVALID,
                old_marker_sha256=hashlib.sha256(original).hexdigest(),
            )
            raw = json.dumps(body, sort_keys=True).encode("utf-8")
            # Assign the spelling directly. normpath on Windows drops a trailing
            # space or period, which would hide the alias this fixture plants.
            fs.nodes[_norm(os.path.join(MISSION, canonical))] = ("file", raw, alias)
            discovered = _oracle_family_aliases(fs.nodes)
            self.assertIn(alias, discovered)
            before = fs.clone_nodes()
            after, result = _run(fs, SEAL_X, TXID, before, None, None)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertFalse(result.launch_allowed)
            self.assertEqual(result.reason, "journal_name_invalid")
            self.assertEqual(_auth_key(after), _auth_key(before))
        positive = _seed_nodes(original)
        backup = "storage_1.modset-canon"
        body = _journal(
            txid,
            storage.PHASE_PREPARED,
            new_seal=SEAL_A,
            old_seal=SEAL_OTHER,
            storage_backup=backup,
            marker_backup=backup + ".marker.json",
            old_marker_state=storage.MARKER_PRESENT_VALID,
            old_marker_sha256=hashlib.sha256(original).hexdigest(),
        )
        raw = json.dumps(body, sort_keys=True).encode("utf-8")
        positive[_norm(os.path.join(MISSION, canonical))] = ("file", raw, canonical)
        self.assertEqual(_oracle_family_aliases(positive), [])
        _after, result = _run(MemFS(), SEAL_X, TXID, positive, None, None)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.launch_allowed, result)
        self.assertNotEqual(result.reason, "journal_name_invalid")

    def test_r3_m2_ordinary_file_link_or_other_world_is_not_absence(self) -> None:
        for kind in ("file", "link", "other"):
            for marker in (None, _canonical_marker(SEAL_X), _canonical_marker(SEAL_OTHER), b"nope"):
                nodes = {_norm(MISSION): ("dir", b"")}
                nodes[_norm(os.path.join(MISSION, storage.STORAGE_NAME))] = (kind, b"known-bytes")
                if marker is not None:
                    nodes[_norm(os.path.join(MISSION, storage.MARKER_NAME))] = ("file", marker)
                before = dict(nodes)
                after, result = _run(MemFS(), SEAL_X, TXID, nodes, None, None)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertFalse(result.launch_allowed)
                self.assertEqual(result.reason, "storage_not_a_directory")
                self.assertEqual(_auth_key(after), _auth_key(before))

    def test_r3_d1_same_caller_walks_t1_t2_and_t3(self) -> None:
        """Retained aborts come from deaths of this same caller, not from planted files.

        The ids are SHA-256("<txid>:retry")[:32] computed here. A mutant that
        refuses to reconcile the active t1 left by the first death returns
        backup_name_collision on the retry.
        """
        caller = TXID
        t1 = _retry_txid(caller)
        t2 = _retry_txid(t1)
        t3 = _retry_txid(t2)
        original = _canonical_marker(SEAL_OTHER)
        seed = _seed_nodes(original)
        _plant_prepared_active(seed, caller, original, "storage_1.modset-call-t0")
        world = _tree(seed, os.path.join(MISSION, storage.STORAGE_NAME))
        first_death = _die_before_world_move(seed, caller)
        self.assertEqual(_active_txid(first_death), t1)
        self.assertIsNotNone(
            _mission_file(first_death, _canonical_completed_name(caller))
        )
        retained_t0 = _mission_file(first_death, _canonical_completed_name(caller))
        probe = MemFS()
        finished, result = _run(probe, SEAL_A, caller, first_death, None, None)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.launch_allowed, result)
        self.assertTrue(result.storage_rotated)
        self.assertIsNone(_active_journal(finished))
        self.assertIsNotNone(_mission_file(finished, _canonical_completed_name(t2)))
        self.assertEqual(
            _mission_file(finished, _canonical_completed_name(caller)),
            retained_t0,
        )
        self.assertEqual(
            _mission_file(finished, _canonical_completed_name(t1)),
            _mission_file(first_death, storage.JOURNAL_PREFIX + t1 + storage.JOURNAL_SUFFIX),
        )
        self._check(finished, world, original, SEAL_A)
        for cut in _cuts(probe.trace):
            snap, crashed = _run(probe, SEAL_A, caller, first_death, cut, None)
            self.assertIsNone(crashed)
            self._check(snap, world, original, None)
        quiet, again = _run(MemFS(), SEAL_A, caller, finished, None, None)
        self.assertIsNotNone(again)
        assert again is not None
        self.assertTrue(again.launch_allowed, again)
        self.assertFalse(again.storage_rotated)
        self.assertIsNone(_mission_file(quiet, _canonical_completed_name(t3)))
        second_death = _die_before_world_move(first_death, caller)
        self.assertEqual(_active_txid(second_death), t2)
        self.assertEqual(
            _mission_file(second_death, _canonical_completed_name(caller)),
            retained_t0,
        )
        self.assertEqual(
            _mission_file(second_death, _canonical_completed_name(t1)),
            _mission_file(first_death, storage.JOURNAL_PREFIX + t1 + storage.JOURNAL_SUFFIX),
        )
        third, result = _run(MemFS(), SEAL_A, caller, second_death, None, None)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.launch_allowed, result)
        self.assertIsNotNone(_mission_file(third, _canonical_completed_name(t3)))
        self.assertIsNone(_active_journal(third))
        self.assertEqual(
            _mission_file(third, _canonical_completed_name(caller)),
            retained_t0,
        )
        rested, rest = _run(MemFS(), SEAL_A, caller, third, None, None)
        self.assertIsNotNone(rest)
        assert rest is not None
        self.assertFalse(rest.storage_rotated)
        self.assertIsNone(_mission_file(rested, _canonical_completed_name(_retry_txid(t3))))

    def test_r3_d2_ordinary_unsupported_marker_kinds(self) -> None:
        from dayz_mcp.process_lifecycle import ProcessLifecycle

        for world_present in (False, True):
            for kind in ("dir", "link", "other"):
                nodes = {_norm(MISSION): ("dir", b"")}
                if world_present:
                    home = _norm(os.path.join(MISSION, storage.STORAGE_NAME))
                    nodes[home] = ("dir", b"")
                    nodes[_norm(os.path.join(home, "p.bin"))] = ("file", b"O")
                marker = _norm(os.path.join(MISSION, storage.MARKER_NAME))
                nodes[marker] = (kind, b"")
                if kind == "dir":
                    nodes[_norm(os.path.join(marker, "child.bin"))] = ("file", b"child")
                before = dict(nodes)
                after, result = _run(MemFS(), SEAL_X, TXID, nodes, None, None)
                self.assertIsNotNone(result, kind)
                assert result is not None
                self.assertFalse(result.launch_allowed)
                self.assertEqual(result.reason, "marker_type_unsupported")
                self.assertEqual(_auth_key(after), _auth_key(before))
        audits: list[str] = []

        class _Stub:
            _last_storage_recovery_reason: str | None = None

            def _audit(self, event: str, reason: object, *_rest: object, **_kwargs: object) -> bool:
                audits.append(str(reason))
                return True

            def _note_audit_row_dropped(self) -> None:
                raise AssertionError("dropped")

        stub = _Stub()
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            (mission / storage.MARKER_NAME).mkdir(parents=True)
            (mission / storage.MARKER_NAME / "child.bin").write_bytes(b"child")
            code = ProcessLifecycle._rotate_storage_for_launch(
                stub,  # type: ignore[arg-type]
                {
                    "storage_seal": SEAL_X,
                    "mission": str(mission),
                    "mod": PROJECT,
                },
                "11111111-1111-4111-8111-111111111111",
                None,
            )
        self.assertEqual(code, "storage_recovery_required")
        self.assertEqual(stub._last_storage_recovery_reason, "marker_type_unsupported")

    def test_r3_d3_cap_spies_nested_marker_and_tail(self) -> None:
        nested = b"[" * 30000 + b"0" + b"]" * 30000
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            world = mission / storage.STORAGE_NAME
            world.mkdir(parents=True)
            (world / "p.bin").write_bytes(b"world")
            (mission / storage.MARKER_NAME).write_bytes(nested)
            result = storage.prepare_storage(
                str(mission), seal=SEAL_A, project=PROJECT, now=1.0, txid=TXID
            )
            self.assertTrue(result.launch_allowed, result)
            preserved = (mission / str(result.storage_marker_backup)).read_bytes()
            self.assertEqual(preserved, nested)
            journal_name = _canonical_completed_name(TXID)
            document = json.loads((mission / journal_name).read_text(encoding="utf-8"))
            self.assertEqual(document["old_marker_sha256"], hashlib.sha256(nested).hexdigest())
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            world = mission / storage.STORAGE_NAME
            world.mkdir(parents=True)
            (world / "p.bin").write_bytes(b"world")
            raw = _canonical_marker(SEAL_OTHER)
            original_marker = raw + b" " * (storage._MAX_JSON_BYTES - len(raw)) + b"TAIL"
            (mission / storage.MARKER_NAME).write_bytes(original_marker)
            result = storage.prepare_storage(
                str(mission), seal=SEAL_A, project=PROJECT, now=1.0, txid="c" * 32
            )
            self.assertTrue(result.launch_allowed, result)
            preserved = (mission / str(result.storage_marker_backup)).read_bytes()
            self.assertEqual(preserved, original_marker)
            document = json.loads(
                (mission / _canonical_completed_name("c" * 32)).read_text(encoding="utf-8")
            )
            self.assertEqual(
                document["old_marker_sha256"], hashlib.sha256(original_marker).hexdigest()
            )
            full_hash = document["old_marker_sha256"]
            tail = bytearray(original_marker)
            tail[-1] ^= 0x01
            corrupted = bytes(tail)
            self.assertEqual(corrupted[: storage._MAX_JSON_BYTES], original_marker[: storage._MAX_JSON_BYTES])
            self.assertNotEqual(hashlib.sha256(corrupted).hexdigest(), full_hash)
            for where in ("k", "m"):
                planted = _oversized_moved(original_marker, str(full_hash), where, corrupted)
                before = dict(planted)
                fs = MemFS()
                fs.digest_short = 64
                after, refused = _run(fs, SEAL_X, "2" * 32, planted, None, None)
                self.assertIsNotNone(refused)
                assert refused is not None
                self.assertFalse(refused.launch_allowed)
                self.assertEqual(refused.reason, "journal_state_impossible")
                self.assertEqual(_auth_key(after), _auth_key(before))
                self.assertTrue(fs.read_sizes)
                self.assertTrue(all(size > 0 for size in fs.read_sizes))
                self.assertGreater(fs.bytes_consumed, 0)
                for cut in _cuts(fs.trace):
                    snap, crashed = _run(fs, SEAL_X, "2" * 32, planted, cut, None)
                    self.assertIsNone(crashed)
                    self.assertEqual(_auth_key(snap), _auth_key(before))
        oversized = b"{" * (storage._MAX_JSON_BYTES + 50)
        nodes = _seed_nodes(_canonical_marker(SEAL_OTHER))
        name = storage.JOURNAL_PREFIX + ("b" * 32) + storage.JOURNAL_SUFFIX
        journal_key = _norm(os.path.join(MISSION, name))
        nodes[journal_key] = ("file", oversized, name)
        fs = MemFS()
        fs.digest_short = 5
        before = dict(nodes)
        after, result = _run(fs, SEAL_A, TXID, nodes, None, None)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.reason, "journal_unreadable")
        self.assertEqual(_auth_key(after), _auth_key(before))
        self.assertTrue(fs.read_sizes)
        self.assertTrue(all(size > 0 for size in fs.read_sizes))
        self.assertEqual(fs.consumed_by_path[journal_key], storage._MAX_JSON_BYTES + 1)
        self.assertLess(fs.consumed_by_path[journal_key], len(oversized))

    def test_r3_e2_refusal_survives_worker_terminal_and_public_result(self) -> None:
        import asyncio
        import importlib.util
        import io
        import sys

        from dayz_mcp import dayz_test_tool, dayz_test_worker

        source = (
            Path(__file__).resolve().parents[1]
            / "native-launchers"
            / "dayz-test-v1"
            / "src"
            / "app_main.py"
        )
        spec = importlib.util.spec_from_file_location("r4_app_main", source)
        assert spec is not None and spec.loader is not None
        app_main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app_main)
        reasons = (
            "journal_state_impossible",
            "journal_unreadable",
            "journal_name_invalid",
            "journal_ambiguous",
        )
        runtime = dayz_test_worker.WorkerRuntimePolicy(
            dev_root=r"P:\ExampleMod_Suite",
            mod="ExampleMod",
            diag_executable=r"C:\DayZDiag_x64.exe",
            game_directory=r"C:\DayZ",
            mission_aliases=(("chernarus", r"C:\missions\dayzOffline.chernarusplus"),),
            mods_root=r"P:\Mods",
            build_temp_root=r"P:\temp",
            build_source_basename=None,
        )
        payload = {
            "mission": "chernarus",
            "port": 2302,
            "no_file_patching": True,
            "base_mods": ["@CF"],
        }

        class _Broker:
            def __init__(self, response: dict[str, object], *, fail_stop: bool) -> None:
                self.response = response
                self.fail_stop = fail_stop
                self.spawns = 0
                self.commands: list[str] = []

            async def invoke(self, frame: bytes) -> dict[str, object]:
                request = __import__(
                    "dayz_mcp.native_broker_protocol", fromlist=["decode_request"]
                ).decode_request(frame)
                command = str(request.payload.get("command"))
                self.commands.append(command)
                if command == "start":
                    return self.response
                if command == "stop" and self.fail_stop:
                    return {"ok": False, "error": "run_stop_failed"}
                if command == "stop":
                    return {"ok": True, "state": "EXITED", "run_id": request.payload.get("run_id")}
                self.spawns += 1
                return {"ok": True}

        class _Stdout:
            def __init__(self) -> None:
                self.buffer = io.BytesIO()

            def flush(self) -> None:
                return None

        issued = {"n": 0}

        def _id() -> str:
            issued["n"] += 1
            return f"{issued['n']:08x}-1111-4111-8111-111111111111"

        for reason in reasons:
            hint = storage.storage_recovery_hint(reason)
            refused_nodes = _refusal_snapshot(reason)
            before = dict(refused_nodes)
            after, refused = _run(MemFS(), SEAL_A, TXID, refused_nodes, None, None)
            self.assertIsNotNone(refused)
            assert refused is not None
            self.assertFalse(refused.launch_allowed)
            self.assertEqual(refused.reason, reason)
            self.assertEqual(_auth_key(after), _auth_key(before))
            for degraded in (False, True):
                broker = _Broker(
                    {
                        "ok": False,
                        "error": "storage_recovery_required",
                        "storage_recovery_reason": reason,
                        "storage_recovery_hint": hint,
                    },
                    fail_stop=degraded,
                )

                async def _worker_main() -> int:
                    await dayz_test_worker._start(
                        broker,
                        payload,
                        runtime,
                        role="server",
                        existing_run_id=None,
                        id_fn=_id,
                    )
                    return 0

                previous_argv = sys.argv
                previous_out = sys.stdout
                holder = _Stdout()
                sys.argv = ["app_main"]
                sys.stdout = holder  # type: ignore[assignment]
                app_main._worker_main = _worker_main
                try:
                    code = app_main.main()
                finally:
                    sys.argv = previous_argv
                    sys.stdout = previous_out
                self.assertEqual(code, 2)
                self.assertEqual(broker.spawns, 0)
                # Server creation replays one failed start before it settles the refusal.
                self.assertEqual(broker.commands, ["start", "start", "stop"])
                framed = holder.buffer.getvalue()
                size = int.from_bytes(framed[:4], "little")
                body = framed[4 : 4 + size]
                self.assertTrue(body.startswith(b"DZW1"))
                terminal = dayz_test_tool.parse_worker_terminal(body[4:], b"", 2)
                self.assertEqual(terminal.error_code, "storage_recovery_required")
                self.assertEqual(terminal.storage_recovery_reason, reason)
                self.assertEqual(terminal.storage_recovery_hint, hint)
                self.assertEqual(terminal.cleanup_degraded, degraded)
                public = dayz_test_tool._compact_result(
                    terminal=terminal,
                    project=PROJECT,
                    mode="server",
                    started_at=0.0,
                    artifacts_paths=[],
                )
                self.assertEqual(public["error_code"], "storage_recovery_required")
                self.assertEqual(public["storage_recovery_reason"], reason)
                self.assertEqual(public["remediation"], hint)
        del asyncio
        legacy = dayz_test_tool.parse_worker_terminal(
            json.dumps(
                {
                    "cleanup_degraded": False,
                    "error_code": "worker_failed",
                    "exit_code": 1,
                    "ok": False,
                    "run_id": None,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            b"",
            1,
        )
        self.assertIsNone(legacy.storage_recovery_reason)
        with self.assertRaises(dayz_test_tool.DayzTestToolError):
            dayz_test_tool.parse_worker_terminal(
                json.dumps(
                    {
                        "cleanup_degraded": False,
                        "error_code": "storage_recovery_required",
                        "exit_code": 1,
                        "ok": False,
                        "run_id": None,
                        "storage_recovery_reason": "journal_unreadable",
                        "storage_recovery_hint": "not-the-canonical-hint",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                b"",
                1,
            )

    def test_r3_e1_restore_and_quarantine_are_external(self) -> None:
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            _plant_moved(mission, marker=b"original-marker", seal=SEAL_A, old_seal=None)
            backup = mission / "storage_1.modset-d3-legacy"
            held = mission / "held-backup"
            os.rename(backup, held)
            before = _disk(mission)
            refused = storage.prepare_storage(
                str(mission), seal=SEAL_X, project=PROJECT, now=1.0, txid=TXID
            )
            self.assertFalse(refused.launch_allowed)
            self.assertEqual(_disk(mission), before)
            os.rename(held, backup)
            restored = storage.prepare_storage(
                str(mission), seal=SEAL_X, project=PROJECT, now=1.0, txid=TXID
            )
            self.assertTrue(restored.launch_allowed, restored)
            self.assertEqual((backup / "p.bin").read_bytes(), b"original-world")
            stray = mission / (storage.JOURNAL_PREFIX + "notes.json.bak")
            stray.write_bytes(b"keep-these-bytes")
            blocked = storage.prepare_storage(
                str(mission), seal=SEAL_X, project=PROJECT, now=2.0, txid="2" * 32
            )
            self.assertFalse(blocked.launch_allowed)
            self.assertEqual(blocked.reason, "journal_name_invalid")
            self.assertEqual(stray.read_bytes(), b"keep-these-bytes")
            quarantine = mission / "quarantine-notes.json.bak"
            os.rename(stray, quarantine)
            cleared = storage.prepare_storage(
                str(mission), seal=SEAL_X, project=PROJECT, now=3.0, txid="3" * 32
            )
            self.assertTrue(cleared.launch_allowed, cleared)
            self.assertEqual(quarantine.read_bytes(), b"keep-these-bytes")
            garbage = mission / (
                storage.JOURNAL_PREFIX + "garbage" + storage.JOURNAL_COMPLETED_SUFFIX
            )
            garbage.write_bytes(b"not-a-journal")
            exempt = storage.prepare_storage(
                str(mission), seal=SEAL_X, project=PROJECT, now=4.0, txid="4" * 32
            )
            self.assertTrue(exempt.launch_allowed, exempt)
            self.assertEqual(garbage.read_bytes(), b"not-a-journal")
        with TemporaryDirectory() as room:
            mission = Path(room) / "mission"
            original_marker = b"original-marker"
            mission.mkdir(parents=True)
            backup = mission / "storage_1.modset-d3-legacy"
            (backup / "players").mkdir(parents=True)
            (backup / "p.bin").write_bytes(b"original-world")
            published = _canonical_marker(SEAL_A)
            (mission / storage.MARKER_NAME).write_bytes(published)
            (mission / "storage_1.modset-d3-legacy.marker.json").write_bytes(original_marker)
            _write_journal(
                mission,
                _journal(
                    "8" * 32,
                    storage.PHASE_MARKER_PUBLISHED,
                    new_seal=SEAL_A,
                    old_seal=None,
                    old_marker_state=storage.MARKER_PRESENT_INVALID,
                    old_marker_sha256=hashlib.sha256(original_marker).hexdigest(),
                ),
            )
            held = mission / "held-published-backup"
            os.rename(backup, held)
            before = _disk(mission)
            refused = storage.prepare_storage(
                str(mission), seal=SEAL_X, project=PROJECT, now=5.0, txid=TXID
            )
            self.assertFalse(refused.launch_allowed)
            self.assertEqual(_disk(mission), before)
            os.rename(held, backup)
            restored = storage.prepare_storage(
                str(mission), seal=SEAL_X, project=PROJECT, now=6.0, txid=TXID
            )
            self.assertTrue(restored.launch_allowed, restored)
            self.assertEqual((backup / "p.bin").read_bytes(), b"original-world")
            self.assertEqual(
                (mission / "storage_1.modset-d3-legacy.marker.json").read_bytes(),
                original_marker,
            )

    def test_shipped_recovery_document_is_linked(self) -> None:
        root = Path(__file__).resolve().parents[2]
        document = (root / "docs" / "STORAGE_RECOVERY.md").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/STORAGE_RECOVERY.md", readme)
        folded = document.lower()
        for phrase in (
            "storage_moved",
            "marker_published",
            "exclusive",
            ".json.bak",
            "garbage.completed.json",
            "do not fabricate",
            "original complete tree",
            "outside",
            "storage_rotated",
            "do not delete",
            "exclusive",
        ):
            self.assertIn(phrase, folded)


def _active_journal_on(mission: Path) -> str | None:
    for path in mission.iterdir():
        name = path.name
        if (
            path.is_file()
            and name.startswith(storage.JOURNAL_PREFIX)
            and name.endswith(storage.JOURNAL_SUFFIX)
            and not name.endswith(storage.JOURNAL_COMPLETED_SUFFIX)
        ):
            return name
    return None


def _completed_rotation(marker: bytes) -> tuple[dict, dict]:
    world = _tree(_seed_nodes(marker), os.path.join(MISSION, storage.STORAGE_NAME))
    clean, produced = _run(MemFS(), SEAL_A, TXID, _seed_nodes(marker), None, None)
    if produced is None or not produced.launch_allowed:
        raise AssertionError(produced)
    return clean, world


def _planted_completed(marker: bytes) -> tuple[dict, dict]:
    """A finished schema-2 rotation whose original bytes are `marker`, even if they equal N."""
    world = {"players\\p.bin": b"world"}
    backup = "storage_1.modset-19700101-000000-" + SEAL_A[:8]
    body = _journal(
        TXID,
        storage.PHASE_MARKER_PUBLISHED,
        new_seal=SEAL_A,
        old_seal=SEAL_A,
        storage_backup=backup,
        marker_backup=backup + ".marker.json",
        old_marker_state=storage.MARKER_PRESENT_VALID,
        old_marker_sha256=hashlib.sha256(marker).hexdigest(),
    )
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    mission = _norm(MISSION)
    nodes = {
        mission: ("dir", b""),
        _norm(os.path.join(MISSION, backup)): ("dir", b""),
        _norm(os.path.join(MISSION, backup, "players")): ("dir", b""),
        _norm(os.path.join(MISSION, backup, "players", "p.bin")): ("file", b"world"),
        _norm(os.path.join(MISSION, storage.MARKER_NAME)): ("file", _canonical_marker(SEAL_A)),
        _norm(os.path.join(MISSION, backup + ".marker.json")): ("file", marker),
        _norm(os.path.join(MISSION, storage.JOURNAL_PREFIX + TXID + storage.JOURNAL_COMPLETED_SUFFIX)): (
            "file",
            raw,
        ),
    }
    return nodes, world


def _reserved_backup_name(nodes: dict, marker: bytes) -> str:
    digest = hashlib.sha256(marker).hexdigest()
    for name, document in _published_journals(nodes):
        if document is not None and document.get("old_marker_sha256") == digest:
            return str(document["marker_backup"])
    raise AssertionError(name if False else "no journal recorded the original")


def _seed_nodes(marker: bytes | None) -> dict:
    fs = MemFS()
    _seed(fs, marker)
    return fs.clone_nodes()


def _call(nodes: dict, seal: str, txid: str) -> storage.RotationResult | None:
    fs = MemFS()
    fs.nodes = copy.deepcopy(nodes)
    saved = _install(fs)
    try:
        return storage.prepare_storage(
            MISSION, seal=seal, project=PROJECT, now=9.0, txid=txid
        )
    except (OSError, storage.StorageError):
        return None
    finally:
        _restore(saved)


def _disk(mission: Path) -> dict[str, bytes]:
    found = {}
    for path in mission.rglob("*"):
        if path.is_file():
            found[str(path.relative_to(mission))] = path.read_bytes()
    return found


def _retry_txid(txid: str) -> str:
    """Next id of one caller, independent of `storage.derived_txid`."""
    return hashlib.sha256(f"{txid}:retry".encode("ascii")).hexdigest()[:32]


def _active_txid(nodes: dict) -> str | None:
    name = _active_journal(nodes)
    if name is None:
        return None
    return _txid_from_journal_name(name)


def _plant_prepared_active(nodes: dict, txid: str, marker: bytes, backup: str) -> None:
    body = _journal(
        txid,
        storage.PHASE_PREPARED,
        new_seal=SEAL_A,
        old_seal=SEAL_OTHER,
        old_marker_state=storage.MARKER_PRESENT_VALID,
        old_marker_sha256=hashlib.sha256(marker).hexdigest(),
        storage_backup=backup,
        marker_backup=backup + ".marker.json",
    )
    raw = json.dumps(body, sort_keys=True).encode("utf-8")
    name = storage.JOURNAL_PREFIX + txid + storage.JOURNAL_SUFFIX
    nodes[_norm(os.path.join(MISSION, name))] = ("file", raw, name)


def _die_before_world_move(nodes: dict, caller: str) -> dict:
    """Last abrupt cut that still has the world directory and an active journal."""
    fs = MemFS()
    _done, result = _run(fs, SEAL_A, caller, nodes, None, None)
    if result is None or not result.launch_allowed:
        raise AssertionError(result)
    chosen = None
    home = _norm(os.path.join(MISSION, storage.STORAGE_NAME))
    for index, _item in enumerate(fs.trace):
        snap, crashed = _run(fs, SEAL_A, caller, nodes, (index, "before", None), None)
        if crashed is not None:
            continue
        world = snap.get(home)
        if world is None or world[0] != "dir" or _active_journal(snap) is None:
            continue
        chosen = snap
    if chosen is None:
        raise AssertionError("no pre-move death")
    return chosen


def _oversized_moved(
    original: bytes, recorded_hash: str, where: str, corrupted: bytes
) -> dict:
    """Schema-2 `storage_moved`: W absent, D holds the world, tail lives at M or K."""
    backup = "storage_1.modset-d3-legacy"
    body = _journal(
        TXID,
        storage.PHASE_STORAGE_MOVED,
        new_seal=SEAL_A,
        old_seal=SEAL_OTHER,
        old_marker_state=storage.MARKER_PRESENT_VALID,
        old_marker_sha256=recorded_hash,
        storage_backup=backup,
        marker_backup=backup + ".marker.json",
    )
    raw = json.dumps(body, sort_keys=True).encode("utf-8")
    name = storage.JOURNAL_PREFIX + TXID + storage.JOURNAL_SUFFIX
    mission = _norm(MISSION)
    nodes = {
        mission: ("dir", b""),
        _norm(os.path.join(MISSION, backup)): ("dir", b""),
        _norm(os.path.join(MISSION, backup, "p.bin")): ("file", b"original-world"),
        _norm(os.path.join(MISSION, name)): ("file", raw, name),
    }
    target = storage.MARKER_NAME if where == "m" else backup + ".marker.json"
    nodes[_norm(os.path.join(MISSION, target))] = ("file", corrupted)
    return nodes


def _refusal_snapshot(reason: str) -> dict:
    """A mission prepare_storage refuses with `reason`, built without the scanner."""
    original = _canonical_marker(SEAL_OTHER)
    nodes = _seed_nodes(original)
    if reason == "journal_unreadable":
        name = storage.JOURNAL_PREFIX + TXID + storage.JOURNAL_SUFFIX
        nodes[_norm(os.path.join(MISSION, name))] = ("file", b"{}", name)
        return nodes
    if reason == "journal_name_invalid":
        name = storage.JOURNAL_PREFIX + TXID + storage.JOURNAL_SUFFIX
        nodes[_norm(os.path.join(MISSION, name))] = (
            "file",
            b"{}",
            "STORAGE_1.modset.rotation." + TXID + ".json",
        )
        return nodes
    if reason == "journal_ambiguous":
        for txid in (TXID, "2" * 32):
            name = storage.JOURNAL_PREFIX + txid + storage.JOURNAL_SUFFIX
            nodes[_norm(os.path.join(MISSION, name))] = ("file", b"{}", name)
        return nodes
    backup = "storage_1.modset-impossible"
    _plant_prepared_active(nodes, TXID, original, backup)
    nodes[_norm(os.path.join(MISSION, backup))] = ("dir", b"")
    return nodes


def _journal(txid: str, phase: str, **extra: object) -> dict[str, object]:
    body = {
        "schema_version": extra.pop("schema_version", storage.JOURNAL_SCHEMA_VERSION),
        "txid": txid,
        "phase": phase,
        "new_seal": extra.pop("new_seal", SEAL_A),
        "old_seal": extra.pop("old_seal", None),
        "project": PROJECT,
        "storage_backup": extra.pop("storage_backup", "storage_1.modset-d3-legacy"),
        "marker_backup": extra.pop("marker_backup", "storage_1.modset-d3-legacy.marker.json"),
    }
    if body["schema_version"] == storage.JOURNAL_SCHEMA_VERSION:
        body["old_marker_state"] = extra.pop("old_marker_state", storage.MARKER_ABSENT)
        body["old_marker_sha256"] = extra.pop("old_marker_sha256", None)
    body.update(extra)
    return body


def _write_journal(mission: Path, body: dict[str, object]) -> None:
    name = storage.JOURNAL_PREFIX + str(body["txid"]) + storage.JOURNAL_SUFFIX
    (mission / name).write_text(json.dumps(body), encoding="utf-8")


def _plant_moved(mission: Path, *, marker: bytes | None, seal: str, old_seal: str | None = None) -> None:
    mission.mkdir(parents=True, exist_ok=True)
    backup = mission / "storage_1.modset-d3-legacy"
    (backup / "players").mkdir(parents=True)
    (backup / "p.bin").write_bytes(b"original-world")
    if marker is not None:
        (mission / storage.MARKER_NAME).write_bytes(marker)
    legacy = old_seal is None and marker is None
    state = storage.MARKER_ABSENT if marker is None else storage.MARKER_PRESENT_INVALID
    digest = None if marker is None else hashlib.sha256(marker).hexdigest()
    if old_seal is not None:
        state = storage.MARKER_PRESENT_VALID
    fields: dict[str, object] = {
        "new_seal": seal,
        "old_seal": old_seal,
        "schema_version": storage.JOURNAL_SCHEMA_LEGACY if legacy else storage.JOURNAL_SCHEMA_VERSION,
    }
    if not legacy:
        fields["old_marker_state"] = state
        fields["old_marker_sha256"] = digest
    _write_journal(mission, _journal("9" * 32, storage.PHASE_STORAGE_MOVED, **fields))


def _refusal_cases() -> list[tuple[str, object]]:
    def base(mission: Path) -> None:
        tree = mission / storage.STORAGE_NAME
        (tree / "players").mkdir(parents=True)
        (tree / "p.bin").write_bytes(b"world")

    def plant_w_file(mission: Path) -> None:
        base(mission)
        (mission / storage.STORAGE_NAME / "p.bin").unlink()
        # W itself must be a file. Remove the directory by writing the journal over a file world.
        import shutil
        shutil.rmtree(mission / storage.STORAGE_NAME)
        (mission / storage.STORAGE_NAME).write_bytes(b"not-a-dir")
        _write_journal(mission, _journal(TXID, storage.PHASE_PREPARED))

    def plant_both_journals(mission: Path) -> None:
        base(mission)
        body = _journal(TXID, storage.PHASE_PREPARED)
        _write_journal(mission, body)
        completed = storage.JOURNAL_PREFIX + TXID + storage.JOURNAL_COMPLETED_SUFFIX
        (mission / completed).write_text("{}", encoding="utf-8")

    def plant_neither_tree(mission: Path) -> None:
        mission.mkdir(exist_ok=True)
        _write_journal(mission, _journal(TXID, storage.PHASE_PREPARED))

    def plant_s_without_d(mission: Path) -> None:
        base(mission)
        _write_journal(mission, _journal(TXID, storage.PHASE_STORAGE_MOVED))

    def plant_both_present(mission: Path) -> None:
        base(mission)
        backup = mission / "storage_1.modset-d3-legacy"
        backup.mkdir()
        (backup / "p.bin").write_bytes(b"world")
        _write_journal(mission, _journal(TXID, storage.PHASE_MARKER_PUBLISHED, old_marker_state=storage.MARKER_ABSENT))

    def plant_p_k(mission: Path) -> None:
        base(mission)
        (mission / "storage_1.modset-d3-legacy.marker.json").write_bytes(b"k")
        _write_journal(
            mission,
            _journal(TXID, storage.PHASE_PREPARED, old_marker_state=storage.MARKER_ABSENT),
        )

    def plant_absent_k(mission: Path) -> None:
        mission.mkdir(exist_ok=True)
        (mission / "storage_1.modset-d3-legacy").mkdir()
        (mission / "storage_1.modset-d3-legacy.marker.json").write_bytes(b"k")
        _write_journal(
            mission,
            _journal(TXID, storage.PHASE_STORAGE_MOVED, old_marker_state=storage.MARKER_ABSENT),
        )

    def plant_hash_mismatch(mission: Path) -> None:
        mission.mkdir(exist_ok=True)
        (mission / "storage_1.modset-d3-legacy").mkdir()
        (mission / storage.MARKER_NAME).write_bytes(b"other")
        _write_journal(
            mission,
            _journal(
                TXID,
                storage.PHASE_STORAGE_MOVED,
                old_seal=SEAL_OTHER,
                old_marker_state=storage.MARKER_PRESENT_VALID,
                old_marker_sha256="d" * 64,
            ),
        )

    def plant_q_bad_m(mission: Path) -> None:
        mission.mkdir(exist_ok=True)
        (mission / "storage_1.modset-d3-legacy").mkdir()
        (mission / storage.MARKER_NAME).write_bytes(b"nope")
        _write_journal(
            mission,
            _journal(TXID, storage.PHASE_MARKER_PUBLISHED, old_marker_state=storage.MARKER_ABSENT),
        )

    def plant_legacy_q(mission: Path) -> None:
        mission.mkdir(exist_ok=True)
        (mission / "storage_1.modset-d3-legacy").mkdir()
        (mission / storage.MARKER_NAME).write_bytes(_canonical_marker(SEAL_A))
        _write_journal(
            mission,
            _journal(
                TXID,
                storage.PHASE_MARKER_PUBLISHED,
                schema_version=storage.JOURNAL_SCHEMA_LEGACY,
                old_seal=SEAL_OTHER,
            ),
        )

    def plant_legacy_k_m(mission: Path) -> None:
        base(mission)
        (mission / "storage_1.modset-d3-legacy.marker.json").write_bytes(b"kept")
        (mission / storage.MARKER_NAME).write_bytes(b"garbage")
        _write_journal(
            mission,
            _journal(
                TXID,
                storage.PHASE_PREPARED,
                schema_version=storage.JOURNAL_SCHEMA_LEGACY,
                old_seal=None,
            ),
        )

    def plant_alias(mission: Path) -> None:
        base(mission)
        _write_journal(
            mission,
            _journal(TXID, storage.PHASE_PREPARED, storage_backup=storage.MARKER_NAME),
        )

    return [
        ("w-not-dir", plant_w_file),
        ("j-and-c", plant_both_journals),
        ("w-and-d-absent", plant_neither_tree),
        ("phase-s-without-d", plant_s_without_d),
        ("w-and-d-present", plant_both_present),
        ("schema2-p-k", plant_p_k),
        ("schema2-absent-k", plant_absent_k),
        ("schema2-hash", plant_hash_mismatch),
        ("schema2-q", plant_q_bad_m),
        ("schema1-known-q", plant_legacy_q),
        ("schema1-unknown-k-m", plant_legacy_k_m),
        ("alias", plant_alias),
    ]


if __name__ == "__main__":
    unittest.main()
