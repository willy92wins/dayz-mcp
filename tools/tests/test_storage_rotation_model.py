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
        self._fs.nodes[self._path] = ("file", bytes(current))
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
        if size is None or size < 0:
            chunk = blob[self._pos :]
        else:
            chunk = blob[self._pos : self._pos + size]
        self._pos += len(chunk)
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
        mode = stat.S_IFDIR if kind == "dir" else stat.S_IFREG
        if kind == "link":
            mode = stat.S_IFLNK
        self._after(decision)
        return os.stat_result((mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))

    def listdir(self, path: str) -> list[str]:
        decision = self.touch("listdir")
        parent = _norm(path)
        if self.kind(parent) != "dir":
            raise FileNotFoundError(path)
        prefix = parent + "\\"
        names: set[str] = set()
        for key in self.nodes:
            if key.startswith(prefix):
                names.add(key[len(prefix) :].split("\\", 1)[0])
        listed = sorted(names)
        self._after(decision)
        return listed

    def open(self, path: str, mode: str = "r") -> _Handle:
        decision = self.touch("open")
        normal = _norm(path)
        binary = "b" in mode
        if not binary:
            raise OSError("binary only")
        if "x" in mode:
            if self.kind(normal) != "absent":
                raise FileExistsError(path)
            self.nodes[normal] = ("file", b"")
        elif "w" in mode:
            self.nodes[normal] = ("file", b"")
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
        for key in keys:
            suffix = key[len(source) :]
            self.nodes[dest + suffix] = self.nodes.pop(key)


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


def _closure_key(nodes: dict) -> tuple:
    """Fixed-point equivalence. Snapshot dedup stays on `_auth_key`.

    `_auth_key` is exact: every authoritative relative path and byte hash,
    temporaries omitted. The enumerator keeps one snapshot per `_auth_key`.

    The depth-2 check uses this coarser key. Completed journals are history:
    a repeated call finishes an aborted prepare under a new txid and adds a
    file without moving the world or the marker. An active journal counts as
    its phase and payload with `txid` replaced by `*`, so the same
    continuation under txid `4`*32 matches the one already recorded for `3`*32.
    Every other path keeps its own hash.
    """
    mission = _norm(MISSION)
    prefix = mission + "\\"
    rows = []
    for key in sorted(nodes):
        if not key.startswith(prefix):
            continue
        name = key[len(prefix) :]
        if ".modset.tmp-" in name or name.endswith(storage.JOURNAL_COMPLETED_SUFFIX):
            continue
        node = nodes[key]
        if node[0] == "dir":
            rows.append((name, "dir"))
            continue
        if _txid_from_journal_name(name) is None:
            rows.append((name, _digest(node[1])))
            continue
        document = _loaded_json(node[1])
        if isinstance(document, dict):
            document = dict(document)
            document["txid"] = "*"
            rows.append(("active", json.dumps(document, sort_keys=True)))
        else:
            rows.append(("active", hashlib.sha256(node[1]).hexdigest()))
    return tuple(rows)


def _auth_key(nodes: dict) -> tuple:
    mission = _norm(MISSION)
    prefix = mission + "\\"
    rows = []
    for key in sorted(nodes):
        if not key.startswith(prefix):
            continue
        name = key[len(prefix) :]
        if ".modset.tmp-" in name:
            continue
        node = nodes[key]
        if node[0] == "dir":
            rows.append((name, "dir"))
        else:
            rows.append((name, _digest(node[1])))
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
        name = key[len(prefix) :]
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
        name = key[len(prefix) :]
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
            # Depth 3 is the same Y call again. Inputs dedup on `_closure_key`
            # (defined above). Every cut of each distinct continuation is run.
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
                self.assertIn(_closure_key(recovered), shapes)
                for cut in _cuts(probe.trace):
                    snap, crashed = _run(probe, SEAL_Y, "4" * 32, state, cut, None)
                    self.assertIsNone(crashed)
                    self._check(snap, original_world, marker, None)
                    self.assertIn(_closure_key(snap), shapes)
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
        self.assertGreaterEqual(partial_temps, 1)
        self.assertIn("S0", rows)
        self.assertTrue(edges)
        self.assertTrue(rows.keys() <= _ROWS)

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
