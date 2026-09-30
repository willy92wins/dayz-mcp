from __future__ import annotations

import errno
import json
import os
import re
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

try:
    import msvcrt
except ImportError:  # not Windows: the append lock is fcntl.flock
    msvcrt = None  # type: ignore[assignment]
    import fcntl
else:
    fcntl = None  # type: ignore[assignment]


INBOX_DIR = Path(os.environ["LOCALAPPDATA"]) / "DayZ_MCP" / "inbox"
FEEDBACK_PATH = INBOX_DIR / "feedback.jsonl"
KINDS = frozenset({"bug", "request", "tool_contribution", "finding"})
# Published on pipeline_feedback.inputSchema (fb-20260910-032514-2c43) and
# enforced again in append_feedback. Keep the two in lockstep via these names.
TITLE_MIN_CHARS = 1
TITLE_MAX_CHARS = 120
BODY_MIN_CHARS = 1
BODY_MAX_CHARS = 8000
PROJECT_MAX_CHARS = 64
RESOLUTION_MAX_CHARS = 2000
EVIDENCE_REF_MAX_CHARS = 240
# re.ASCII: a bare \d also matches other scripts' digits (fullwidth,
# Arabic-Indic, ...), which no id from append_feedback holds.
_FEEDBACK_ID_RE = re.compile(r"^fb-\d{8}-\d{6}-[0-9a-f]{4}$", re.ASCII)
_EVIDENCE_ROOTS = frozenset({"reviews", "gates", "reports", "research"})
_EVIDENCE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# Every append holds _append_lock (ficha fb-20260822-193753-6271). A writer
# polls another's lock this long, then fails with inbox_busy, writing nothing.
APPEND_LOCK_TIMEOUT_S = 10.0
_APPEND_LOCK_POLL_S = 0.01
# token_hex(2) draws for a new id before inbox_id_collision.
ID_SUFFIX_ATTEMPTS = 16
# What a non-blocking lock attempt raises while another handle holds it:
# EACCES from msvcrt.locking(LK_NBLCK), EWOULDBLOCK (EAGAIN) from flock.
_LOCK_BUSY_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK})


def _require_str(value: object, field: str = "value") -> str:
    if type(value) is not str:
        raise ValueError(f"bad_args: {field} must be a string")
    return value


def _check_length(value: str, field: str, limit: int) -> None:
    """Reject an over-long value naming how long it actually is.

    "resolution > 2000 chars" says the caller overshot but not by how much, so
    trimming is guesswork and every guess costs another rejected call (ficha
    fb-20260906-145656-d45f: six such rejections in one session). The count is
    in characters, the same unit the limit is in.
    """
    if not value:
        raise ValueError(f"bad_args: {field} empty")
    if len(value) > limit:
        raise ValueError(f"bad_args: {field} {len(value)} > {limit} chars")


def _utc_now() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d-%H%M%S"), now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_evidence_ref(value: str | None) -> str | None:
    if value is None:
        return None
    value = _require_str(value, "evidence_ref")
    _check_length(value, "evidence_ref", EVIDENCE_REF_MAX_CHARS)
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("bad_args: evidence_ref must be ASCII") from exc
    segments = value.split("/")
    if len(segments) < 2 or segments[0] not in _EVIDENCE_ROOTS:
        raise ValueError("bad_args: evidence_ref must be under reviews|gates|reports|research")
    for segment in segments[1:]:
        if segment in {".", ".."} or _EVIDENCE_SEGMENT_RE.fullmatch(segment) is None:
            raise ValueError("bad_args: evidence_ref contains an invalid path segment")
    return value


def _age_fields(ts: object, now: datetime) -> dict[str, object]:
    if type(ts) is not str:
        return {"age_s": None, "age_label": None, "age_reason": "invalid_timestamp"}
    try:
        original = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return {"age_s": None, "age_label": None, "age_reason": "invalid_timestamp"}
    if original > now:
        return {"age_s": None, "age_label": None, "age_reason": "future_timestamp"}
    age_s = int((now - original).total_seconds())
    if age_s < 60:
        age_label = f"{age_s}s"
    elif age_s < 3600:
        age_label = f"{age_s // 60}m"
    elif age_s < 86400:
        age_label = f"{age_s // 3600}h"
    else:
        age_label = f"{age_s // 86400}d"
    return {"age_s": age_s, "age_label": age_label}


def _lock_path() -> Path:
    # Derived at call time, like every use of FEEDBACK_PATH: a test that
    # points the store at a temporary directory moves the lock with it.
    return FEEDBACK_PATH.with_name(FEEDBACK_PATH.name + ".lock")


def _try_lock(fd: int) -> bool:
    """Take the append lock through fd without waiting; False while it is held."""
    try:
        if msvcrt is not None:
            # msvcrt.locking starts at the file position: byte 0, one byte.
            # A range past the end locks too, so the lock file stays empty.
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in _LOCK_BUSY_ERRNOS:
            return False
        raise
    return True


def _unlock(fd: int) -> None:
    if msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _append_lock() -> Iterator[None]:
    """Hold the store's exclusive cross-process append lock.

    An OS byte-range lock (msvcrt.locking on Windows, flock elsewhere) on
    byte 0 of feedback.jsonl.lock, beside FEEDBACK_PATH. It belongs to the
    open handle: closing the handle, or the death of the process, drops it,
    so a writer that crashed leaves nothing stale and whether the lock file
    exists means nothing. The file stays empty and is never removed. A lock
    another handle holds is polled for up to APPEND_LOCK_TIMEOUT_S, then the
    append fails with inbox_busy before anything is written. Only appends
    take this lock; readers never do.
    """
    fd = os.open(_lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + APPEND_LOCK_TIMEOUT_S
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise ValueError(
                    "inbox_busy: another writer held the inbox append lock for "
                    f"{APPEND_LOCK_TIMEOUT_S:g} s; nothing was written, retry"
                )
            time.sleep(_APPEND_LOCK_POLL_S)
        try:
            yield
        finally:
            try:
                _unlock(fd)
            except OSError:
                # Closing the handle below drops the lock too, and the record
                # may already be on disk: this must not fail the append.
                pass
    finally:
        os.close(fd)


def _filed_ids() -> set[str]:
    """Every "id" string that a line of the store which parses carries.

    Framed as _feedback_state frames the store: split on b"\\n", each line
    decoded and parsed on its own, a line that does not parse skipped. Any
    dict counts, a resolution that carries an "id" included, so this is never
    narrower than the ids _read_inbox lists.
    """
    filed: set[str] = set()
    try:
        handle = FEEDBACK_PATH.open("rb")
    except FileNotFoundError:
        return filed
    with handle:
        for raw in handle:
            try:
                obj = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            if type(obj) is dict and type(obj.get("id")) is str:
                filed.add(obj["id"])
    return filed


def _unused_id(stamp: str) -> str:
    """fb-<stamp>-<token_hex(2)> that no line of the store carries yet.

    Called under _append_lock, so no other writer can file the same id between
    this check and the write. The id keeps its shape: _FEEDBACK_ID_RE pins
    the four hex digits, which the pipeline uses as the ticket's short name.
    A suffix already filed for this stamp is drawn again instead, at most
    ID_SUFFIX_ATTEMPTS times in all.
    """
    filed = _filed_ids()
    for _attempt in range(ID_SUFFIX_ATTEMPTS):
        candidate = f"fb-{stamp}-{secrets.token_hex(2)}"
        if candidate not in filed:
            return candidate
    raise ValueError(
        f"inbox_id_collision: {ID_SUFFIX_ATTEMPTS} draws for fb-{stamp}-xxxx "
        "all hit a filed id; nothing was written, retry"
    )


def _ends_mid_line() -> bool:
    """True when the store's last byte is not the b"\\n" that ends a record."""
    try:
        handle = FEEDBACK_PATH.open("rb")
    except FileNotFoundError:
        return False
    with handle:
        if handle.seek(0, os.SEEK_END) == 0:
            return False
        handle.seek(-1, os.SEEK_END)
        return handle.read(1) != b"\n"


def _append_jsonl(record: dict, id_stamp: str | None = None) -> None:
    # Every append, entries and resolutions alike, holds _append_lock while it
    # reads the store and writes its record with one os.write, so no other
    # writer on this machine can land in between: records go in whole, one
    # after another, whatever their size. The file is still never rewritten,
    # only appended to. The old premise here, "a unique append <4KB on a local
    # volume = concurrent processes' lines do not interleave", did not hold
    # on Windows at any size (ficha fb-20260822-193753-6271). Measured on
    # 2026-09-30 without the lock, six processes appending at once kept 255
    # of 300 records of about 1 KB, and 35 of 150 records of about 24 KB with
    # 39 torn lines beside them.
    #
    # With id_stamp, record["id"] is chosen here, under the lock (_unused_id),
    # so the uniqueness check and the write are one step to every other writer.
    os.makedirs(INBOX_DIR, exist_ok=True)
    with _append_lock():
        if id_stamp is not None:
            record["id"] = _unused_id(id_stamp)
        payload = (
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if _ends_mid_line():
            # A writer died mid-record, its lock dying with it, or appended
            # without the lock. End its torn line first, so it costs one
            # malformed line and does not swallow this record as well.
            payload = b"\n" + payload
        fd = os.open(FEEDBACK_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)


def append_feedback(
    kind: str,
    title: str,
    body: str,
    project: str = "",
    platform: str = "",
) -> dict:
    kind = _require_str(kind, "kind")
    title = _require_str(title, "title").strip()
    body = _require_str(body, "body")
    project = _require_str(project, "project").strip()
    platform = _require_str(platform, "platform")
    if kind not in KINDS:
        raise ValueError("bad_args: kind not in bug|request|tool_contribution|finding")
    _check_length(title, "title", TITLE_MAX_CHARS)
    _check_length(body, "body", BODY_MAX_CHARS)
    if len(project) > PROJECT_MAX_CHARS:
        raise ValueError(
            f"bad_args: project {len(project)} > {PROJECT_MAX_CHARS} chars"
        )
    stamp, ts = _utc_now()
    entry = {
        # Chosen by _append_jsonl under the append lock; the key stays first.
        "id": None,
        "ts": ts,
        "kind": kind,
        "title": title,
        "body": body,
        "project": project,
        "platform": platform,
    }
    _append_jsonl(entry, id_stamp=stamp)
    result = dict(entry)
    result["path"] = str(FEEDBACK_PATH)
    return result


def _feedback_state(feedback_id: str) -> tuple[bool, bool]:
    """(entry exists, a resolution already follows it) for one id.

    Read from disk on every call, never cached: other processes append to the
    same file. Takes no lock: a record another process is still writing is at
    most an unterminated last line, which either does not parse or is that
    whole record. Records are split on the b"\\n" _append_jsonl ends each one
    with (a JSON string never holds a raw newline); a line that does not decode
    or parse is skipped, so it can never vouch for an id. Classification is the
    one _read_inbox uses: a dict carrying "resolves" is a resolution whatever
    else it holds, so an orphan resolution or an id quoted in some body text
    proves nothing. A resolution counts only after its entry, the only order
    in which _read_inbox attaches it.
    """
    exists = False
    already_resolved = False
    try:
        handle = FEEDBACK_PATH.open("rb")
    except FileNotFoundError:
        return exists, already_resolved
    with handle:
        for raw in handle:
            try:
                obj = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            if type(obj) is not dict:
                continue
            if "resolves" in obj:
                if exists and obj.get("resolves") == feedback_id:
                    already_resolved = True
            elif obj.get("id") == feedback_id:
                exists = True
    return exists, already_resolved


def append_resolution(
    feedback_id: str,
    resolution: str,
    platform: str = "",
    evidence_ref: str | None = None,
) -> dict:
    feedback_id = _require_str(feedback_id, "feedback_id")
    resolution = _require_str(resolution, "resolution")
    platform = _require_str(platform, "platform")
    evidence_ref = _validate_evidence_ref(evidence_ref)
    if _FEEDBACK_ID_RE.fullmatch(feedback_id) is None:
        raise ValueError("bad_args: feedback_id must match fb-YYYYMMDD-HHMMSS-xxxx")
    _check_length(resolution, "resolution", RESOLUTION_MAX_CHARS)
    # Looked up only once every argument is valid, so a malformed call keeps
    # its bad_args error. Read without the append lock, like every reader: the
    # file is never rewritten and entries are never removed, so an entry found
    # here still exists when _append_jsonl writes the record below under the
    # lock, and this read changes nothing on disk. An entry filed after the
    # read is refused now and resolvable on retry. already_resolved is the
    # store as read here, so two resolutions racing can both report false.
    exists, already_resolved = _feedback_state(feedback_id)
    if not exists:
        # feedback_id passed _FEEDBACK_ID_RE above, so the echo is that shape.
        raise ValueError(f"feedback_not_found: {feedback_id}")
    _stamp, ts = _utc_now()
    record = {
        "resolves": feedback_id,
        "ts": ts,
        "resolution": resolution,
        "platform": platform,
    }
    if evidence_ref is not None:
        record["evidence_ref"] = evidence_ref
    _append_jsonl(record)
    # Reply-only, like append_feedback's path: derived from the store as read
    # above, so it is never written into the record.
    result = dict(record)
    result["already_resolved"] = already_resolved
    return result


def _read_inbox(
    limit: int = 20,
    kind: str = "",
    include_resolved: bool = False,
    now: datetime | None = None,
) -> dict:
    if type(kind) is not str or (kind != "" and kind not in KINDS):
        raise ValueError("bad_args: kind not in bug|request|tool_contribution|finding")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("bad_args: limit not in 1..100")
    if type(include_resolved) is not bool:
        raise ValueError("bad_args: include_resolved must be a bool")

    entries: list[dict] = []
    by_id: dict[str, dict] = {}
    malformed = 0
    if FEEDBACK_PATH.is_file():
        # Read as _feedback_state reads, without the append lock, and split
        # the same way: on the b"\n" ending each record
        # (a CRLF's \r is JSON whitespace), each line decoded on its own.
        # str.splitlines() also broke at U+0085, U+2028 and U+2029, which
        # ensure_ascii=False writes raw, and one undecodable byte failed the
        # whole read (fb-20260930-172226-89c9); now either costs one line.
        with FEEDBACK_PATH.open("rb") as handle:
            lines = handle.readlines()
        for raw in lines:
            try:
                obj = json.loads(raw.decode("utf-8"))
            except ValueError:
                malformed += 1
                continue
            if type(obj) is not dict:
                malformed += 1
                continue
            if "resolves" in obj:
                target = obj.get("resolves")
                if type(target) is not str:
                    malformed += 1
                    continue
                if target in by_id:
                    by_id[target]["resolution"] = obj.get("resolution")
                    by_id[target]["resolved_ts"] = obj.get("ts")
                    by_id[target].pop("evidence_ref", None)
                    if obj.get("evidence_ref") is not None:
                        by_id[target]["evidence_ref"] = obj["evidence_ref"]
                continue
            entry_id = obj.get("id")
            if type(entry_id) is not str:
                malformed += 1
                continue
            item = dict(obj)
            entries.append(item)
            by_id[entry_id] = item

    count_total = len(entries)
    unresolved_total = sum(1 for item in entries if "resolution" not in item)
    ranked: list[tuple[str, int, dict]] = []
    if now is None:
        now = datetime.now(timezone.utc)
    for index, item in enumerate(entries):
        item.update(_age_fields(item.get("ts"), now))
        if kind != "" and item.get("kind") != kind:
            continue
        if not include_resolved and "resolution" in item:
            continue
        ranked.append((str(item.get("ts", "")), index, item))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return {
        "entries": [row[2] for row in ranked[:limit]],
        "count_total": count_total,
        "unresolved_total": unresolved_total,
        "malformed": malformed,
    }


def read_inbox(limit: int = 20, kind: str = "", include_resolved: bool = False) -> dict:
    return _read_inbox(limit=limit, kind=kind, include_resolved=include_resolved)
