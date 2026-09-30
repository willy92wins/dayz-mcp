"""fb-20260822-193753-6271: inbox appends serialize on a cross-process lock.

_append_jsonl justified append-only writes with "a unique append <4KB on a
local volume = concurrent processes' lines do not interleave". The module
accepts bodies of 8000 characters, and on Windows the premise failed at any
size: several processes appending at once lost whole records and tore big
ones. The id (fb-<second>-<token_hex(2)>) was never checked either, while
pipeline_feedback promised "ids cannot collide".

Now every append (entries and resolutions) holds an OS byte-range lock on
byte 0 of feedback.jsonl.lock beside the store, and a new id is drawn again
under that lock until no line of the store carries it. The id format, the
size limits and the record fields are unchanged, and readers take no lock.

Every test points inbox.INBOX_DIR / FEEDBACK_PATH at a temporary directory;
the lock path is derived from FEEDBACK_PATH, so it moves with it. The live
inbox is never read or written here.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import call, patch

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import inbox, server
from tests._tiers import slow_test


_STAMP = "20260930-120000"
_FIXED_NOW = (_STAMP, "2026-09-30T12:00:00Z")
_A_ID = "fb-20260930-110000-aaaa"
_B_ID = "fb-20260930-110001-bbbb"
_C_ID = "fb-20260930-110002-cccc"
# The lock file name is a protocol between writers of different processes
# (and versions), so it is spelled out here rather than read off inbox.
_LOCK_NAME = "feedback.jsonl.lock"
# A test that waits on the real lock bound allows this much scheduling slack.
_SLACK_S = 5.0


def _entry(entry_id: str, title: str = "t", body: str = "b") -> dict:
    return {
        "id": entry_id,
        "ts": "2026-09-30T11:00:00Z",
        "kind": "bug",
        "title": title,
        "body": body,
        "project": "",
        "platform": "",
    }


def _jsonl(*records: object, newline: bytes = b"\n") -> bytes:
    """Records framed the way inbox._append_jsonl writes them."""
    return b"".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + newline
        for record in records
    )


def _seed_store(payload: bytes) -> bytes:
    """Write the temporary store (never the live one) and return its bytes."""
    inbox.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    inbox.FEEDBACK_PATH.write_bytes(payload)
    return payload


def _communicate_owned(
    child: subprocess.Popen[str], timeout: float
) -> tuple[str, str]:
    """communicate_owned_fixture (test_bug046): never leave a child behind."""
    try:
        return child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate(timeout=5.0)
        raise


def _python_child(code: str, *args: str, stdin: int | None = None) -> subprocess.Popen[str]:
    """A Python child that imports dayz_mcp from this checkout (cwd tools/)."""
    return subprocess.Popen(
        [sys.executable, "-B", "-c", code, *args],
        cwd=str(_TOOLS_DIR),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _busy(bound_s: float) -> str:
    return (
        "inbox_busy: another writer held the inbox append lock for "
        f"{bound_s:g} s; nothing was written, retry"
    )


class _TemporaryInboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_dir = inbox.INBOX_DIR
        self._orig_path = inbox.FEEDBACK_PATH
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        inbox.INBOX_DIR = self.root / "inbox"
        inbox.FEEDBACK_PATH = inbox.INBOX_DIR / "feedback.jsonl"

    def tearDown(self) -> None:
        inbox.INBOX_DIR = self._orig_dir
        inbox.FEEDBACK_PATH = self._orig_path
        self._tmp.cleanup()


class AppendLockTest(_TemporaryInboxTest):
    def test_the_lock_file_sits_beside_the_store_it_guards(self) -> None:
        # Derived from FEEDBACK_PATH at call time: patching the store moves
        # the lock, so no test can lock (or create a file) in the live inbox.
        lock = inbox.INBOX_DIR / _LOCK_NAME
        self.assertEqual(inbox._lock_path(), lock)
        entry = inbox.append_feedback("bug", "t", "b")
        inbox.append_resolution(entry["id"], "fix")
        self.assertEqual(
            sorted(path.name for path in inbox.INBOX_DIR.iterdir()),
            ["feedback.jsonl", _LOCK_NAME],
        )
        # Ownership is the held byte range, never the file's content.
        self.assertEqual(lock.stat().st_size, 0)

    def test_a_held_lock_refuses_both_appends_and_writes_nothing(self) -> None:
        before = _seed_store(_jsonl(_entry(_A_ID)))
        # The holder is a second handle on the same lock file: a byte-range
        # lock (msvcrt.locking, flock) is per handle, not per process.
        with inbox._append_lock(), patch.object(inbox, "APPEND_LOCK_TIMEOUT_S", 0.0):
            with self.assertRaises(ValueError) as filed:
                inbox.append_feedback("bug", "t", "b")
            with self.assertRaises(ValueError) as resolved:
                inbox.append_resolution(_A_ID, "fix")
        self.assertEqual(str(filed.exception), _busy(0.0))
        self.assertEqual(str(resolved.exception), _busy(0.0))
        self.assertEqual(inbox.FEEDBACK_PATH.read_bytes(), before)
        # Released with its handle: both go through now.
        entry = inbox.append_feedback("bug", "t", "b")
        self.assertIs(inbox.append_resolution(_A_ID, "fix")["already_resolved"], False)
        listed = inbox.read_inbox(limit=100)
        self.assertEqual({item["id"] for item in listed["entries"]}, {entry["id"]})

    def test_a_refused_first_append_creates_no_store(self) -> None:
        inbox.INBOX_DIR.mkdir(parents=True)
        with inbox._append_lock(), patch.object(inbox, "APPEND_LOCK_TIMEOUT_S", 0.0):
            with self.assertRaises(ValueError) as ctx:
                inbox.append_feedback("bug", "t", "b")
        self.assertEqual(str(ctx.exception), _busy(0.0))
        self.assertFalse(inbox.FEEDBACK_PATH.exists())


class UniqueIdUnderTheLockTest(_TemporaryInboxTest):
    def test_a_repeated_draw_in_one_second_gets_another_suffix(self) -> None:
        # The live burst of 2026-08-22 filed 18 tickets in one second; a
        # 16-bit suffix drawn twice there used to be written twice.
        with patch.object(inbox, "_utc_now", return_value=_FIXED_NOW), patch.object(
            inbox.secrets, "token_hex", side_effect=["abcd", "abcd", "ef01"]
        ) as draw:
            first = inbox.append_feedback("bug", "one", "b1")
            second = inbox.append_feedback("bug", "two", "b2")
        self.assertEqual(first["id"], f"fb-{_STAMP}-abcd")
        self.assertEqual(second["id"], f"fb-{_STAMP}-ef01")
        self.assertEqual(draw.call_args_list, [call(2)] * 3)
        lines = inbox.FEEDBACK_PATH.read_bytes().splitlines()
        self.assertEqual(
            [json.loads(line)["id"] for line in lines], [first["id"], second["id"]]
        )

    def test_a_suffix_another_writer_filed_is_skipped(self) -> None:
        # The check reads the store, so an id filed by another process (a
        # line this process never wrote) is avoided as well.
        before = _seed_store(_jsonl(_entry(f"fb-{_STAMP}-abcd")))
        with patch.object(inbox, "_utc_now", return_value=_FIXED_NOW), patch.object(
            inbox.secrets, "token_hex", side_effect=["abcd", "0123"]
        ):
            entry = inbox.append_feedback("bug", "t", "b")
        self.assertEqual(entry["id"], f"fb-{_STAMP}-0123")
        self.assertTrue(inbox.FEEDBACK_PATH.read_bytes().startswith(before))

    def test_the_same_suffix_under_another_second_is_not_a_collision(self) -> None:
        # Unique per stamp, as the id is: refusing a suffix used in any other
        # second would leave 65536 ids for the life of the inbox.
        _seed_store(_jsonl(_entry("fb-20260930-115959-abcd")))
        with patch.object(inbox, "_utc_now", return_value=_FIXED_NOW), patch.object(
            inbox.secrets, "token_hex", return_value="abcd"
        ) as draw:
            entry = inbox.append_feedback("bug", "t", "b")
        self.assertEqual(entry["id"], f"fb-{_STAMP}-abcd")
        self.assertEqual(draw.call_count, 1)

    def test_every_draw_taken_writes_nothing_and_says_so(self) -> None:
        before = _seed_store(_jsonl(_entry(f"fb-{_STAMP}-abcd")))
        with patch.object(inbox, "_utc_now", return_value=_FIXED_NOW), patch.object(
            inbox.secrets, "token_hex", return_value="abcd"
        ) as draw:
            with self.assertRaises(ValueError) as ctx:
                inbox.append_feedback("bug", "t", "b")
        self.assertEqual(
            str(ctx.exception),
            f"inbox_id_collision: {inbox.ID_SUFFIX_ATTEMPTS} draws for "
            f"fb-{_STAMP}-xxxx all hit a filed id; nothing was written, retry",
        )
        self.assertEqual(draw.call_count, inbox.ID_SUFFIX_ATTEMPTS)
        self.assertEqual(inbox.FEEDBACK_PATH.read_bytes(), before)

    def test_the_reply_and_the_record_keep_their_shape(self) -> None:
        # The id is now set inside the append path; the reply is unchanged:
        # the same keys in the same order, "path" reply-only, and the line on
        # disk still starts with the id.
        entry = inbox.append_feedback("bug", "t", "b", project="p", platform="claude")
        self.assertEqual(
            list(entry),
            ["id", "ts", "kind", "title", "body", "project", "platform", "path"],
        )
        self.assertRegex(entry["id"], r"^fb-\d{8}-\d{6}-[0-9a-f]{4}$")
        self.assertEqual(entry["path"], str(inbox.FEEDBACK_PATH))
        raw = inbox.FEEDBACK_PATH.read_bytes()
        self.assertTrue(raw.startswith(b'{"id":"fb-'), raw[:40])
        persisted = json.loads(raw)
        self.assertEqual(list(persisted), list(entry)[:-1])
        self.assertEqual(persisted, {k: v for k, v in entry.items() if k != "path"})


class LockFreeReadersTest(_TemporaryInboxTest):
    def test_a_torn_last_line_is_at_most_one_malformed_line_for_both_readers(self) -> None:
        # A record another process is still writing is a prefix of its line.
        # Cut before every byte of a multi-byte character (a raw U+2028
        # included), where the tail fails to decode rather than to parse, and
        # around both ends of the line, neither reader hides A or B or lists C
        # before its JSON is whole, and both agree. They run while the append
        # lock is held with a zero bound, so a reader that tried to take it
        # would fail inbox_busy.
        head = _jsonl(_entry(_A_ID), _entry(_B_ID))
        title = "torn " + chr(0xF1) + chr(0x20AC) + chr(0x2028) + " end"
        line = _jsonl(_entry(_C_ID, title=title, body="x" + chr(0xE9)))
        whole = len(line) - 1
        cuts = {0, 1, 2, whole - 2, whole - 1, whole, whole + 1}
        cuts |= {index for index, byte in enumerate(line) if byte >= 0x80}
        # U+00F1, U+20AC, U+2028 and U+00E9 are 2 + 3 + 3 + 2 bytes.
        self.assertEqual(sum(byte >= 0x80 for byte in line), 10)
        inbox.INBOX_DIR.mkdir(parents=True)
        with inbox._append_lock(), patch.object(inbox, "APPEND_LOCK_TIMEOUT_S", 0.0):
            for cut in sorted(cuts):
                with self.subTest(cut=cut):
                    _seed_store(head + line[:cut])
                    result = inbox.read_inbox(limit=100)
                    listed = {item["id"] for item in result["entries"]}
                    expected = {_A_ID, _B_ID} | ({_C_ID} if cut >= whole else set())
                    self.assertEqual(listed, expected)
                    self.assertEqual(result["count_total"], len(expected))
                    self.assertEqual(result["malformed"], 1 if 0 < cut < whole else 0)
                    for feedback_id in (_A_ID, _B_ID, _C_ID):
                        exists, _already = inbox._feedback_state(feedback_id)
                        self.assertIs(exists, feedback_id in expected)

    def test_an_append_after_a_torn_line_starts_its_own_line(self) -> None:
        # A writer that dies mid-record leaves its line unterminated. The next
        # append ends that line first, so the tear costs one malformed line
        # and never the record written after it.
        line = _jsonl(_entry(_C_ID, title="torn " + chr(0xF1) + chr(0x20AC)))
        torn = _seed_store(_jsonl(_entry(_A_ID)) + line[: len(line) // 2])
        after = inbox.append_feedback("bug", "after the tear", "body")
        self.assertTrue(inbox.FEEDBACK_PATH.read_bytes().startswith(torn))
        listed = inbox.read_inbox(limit=100)
        self.assertEqual({item["id"] for item in listed["entries"]}, {_A_ID, after["id"]})
        self.assertEqual(listed["malformed"], 1)
        self.assertEqual(inbox._feedback_state(after["id"]), (True, False))

        _seed_store(torn)
        inbox.append_resolution(_A_ID, "fixed")
        shown = inbox.read_inbox(include_resolved=True, limit=100)
        self.assertEqual(shown["unresolved_total"], 0)
        self.assertEqual(shown["entries"][0]["resolution"], "fixed")
        self.assertEqual(shown["malformed"], 1)


class LegacyStoreTest(_TemporaryInboxTest):
    def test_a_legacy_store_with_records_over_4_kib_reads_and_resolves_unchanged(self) -> None:
        # Shaped like the live file: CRLF endings (os.open text mode on
        # Windows), a few LF-only lines, records over 4096 B (the biggest live
        # one is about 7.3 KB) and a resolution appended after its entry.
        text = "Diseño con acentos áéíóú — medido. "
        first = _entry(_A_ID, title="primera", body=(text * 200)[:4000])
        second = _entry(_B_ID, title="segunda", body=(text * 300)[:5900])
        third = _entry(_C_ID, title="tercera", body="corta")
        resolution = {
            "resolves": _A_ID,
            "ts": "2026-09-30T11:05:00Z",
            "resolution": "hecho",
            "platform": "claude",
        }
        legacy = _seed_store(
            _jsonl(first, second, resolution, newline=b"\r\n")
            + _jsonl(third, newline=b"\n")
        )
        sizes = [len(line) for line in legacy.split(b"\n")[:2]]
        self.assertTrue(all(size > 4096 for size in sizes), sizes)
        self.assertGreater(max(sizes), 7000)

        shown = inbox.read_inbox(include_resolved=True, limit=100)
        self.assertEqual(shown["malformed"], 0)
        self.assertEqual(shown["count_total"], 3)
        self.assertEqual(shown["unresolved_total"], 2)
        by_id = {item["id"]: item for item in shown["entries"]}
        for record in (first, second, third):
            self.assertEqual(by_id[record["id"]]["body"], record["body"])
        self.assertEqual(by_id[_A_ID]["resolution"], "hecho")
        self.assertEqual(inbox._feedback_state(_A_ID), (True, True))
        self.assertEqual(inbox._feedback_state(_B_ID), (True, False))

        record = inbox.append_resolution(_B_ID, "arreglada")
        self.assertIs(record["already_resolved"], False)
        entry = inbox.append_feedback("bug", "nueva", "cuerpo")
        self.assertNotIn(entry["id"], {_A_ID, _B_ID, _C_ID})
        self.assertTrue(inbox.FEEDBACK_PATH.read_bytes().startswith(legacy))
        shown = inbox.read_inbox(include_resolved=True, limit=100)
        self.assertEqual(shown["malformed"], 0)
        self.assertEqual(shown["count_total"], 4)
        self.assertEqual(shown["unresolved_total"], 2)
        by_id = {item["id"]: item for item in shown["entries"]}
        self.assertEqual(by_id[_B_ID]["resolution"], "arreglada")
        self.assertEqual(by_id[_B_ID]["body"], second["body"])


class InboxBusyToolTest(unittest.IsolatedAsyncioTestCase):
    """The new refusals reach the caller as a ToolError, like bad_args."""

    def setUp(self) -> None:
        self._orig_dir = inbox.INBOX_DIR
        self._orig_path = inbox.FEEDBACK_PATH
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        inbox.INBOX_DIR = root / "inbox"
        inbox.FEEDBACK_PATH = inbox.INBOX_DIR / "feedback.jsonl"

    def tearDown(self) -> None:
        inbox.INBOX_DIR = self._orig_dir
        inbox.FEEDBACK_PATH = self._orig_path
        self._tmp.cleanup()

    async def test_inbox_busy_is_a_tool_error_that_writes_nothing(self) -> None:
        before = _seed_store(_jsonl(_entry(_A_ID)))
        app, _runtime = server.build_app(server.ServerConfig())
        feedback = app._tool_manager.get_tool("pipeline_feedback")
        resolve = app._tool_manager.get_tool("pipeline_resolve")
        with inbox._append_lock(), patch.object(inbox, "APPEND_LOCK_TIMEOUT_S", 0.0):
            with self.assertRaises(server.ToolError) as filed:
                await feedback.fn(kind="bug", title="t", body="b")
            with self.assertRaises(server.ToolError) as resolved:
                await resolve.fn(feedback_id=_A_ID, resolution="fix")
        self.assertEqual(str(filed.exception), _busy(0.0))
        self.assertEqual(str(resolved.exception), _busy(0.0))
        self.assertEqual(inbox.FEEDBACK_PATH.read_bytes(), before)

    async def test_inbox_id_collision_is_a_tool_error_that_writes_nothing(self) -> None:
        before = _seed_store(_jsonl(_entry(f"fb-{_STAMP}-abcd")))
        app, _runtime = server.build_app(server.ServerConfig())
        feedback = app._tool_manager.get_tool("pipeline_feedback")
        with patch.object(inbox, "_utc_now", return_value=_FIXED_NOW), patch.object(
            inbox.secrets, "token_hex", return_value="abcd"
        ):
            with self.assertRaises(server.ToolError) as ctx:
                await feedback.fn(kind="bug", title="t", body="b")
        self.assertTrue(str(ctx.exception).startswith("inbox_id_collision: "), ctx.exception)
        self.assertEqual(inbox.FEEDBACK_PATH.read_bytes(), before)


class PipelineDescriptionsTest(unittest.IsolatedAsyncioTestCase):
    """The catalog promises what the lock gives, and no more."""

    async def _texts(self, name: str) -> dict[str, str]:
        app, _runtime = server.build_app(server.ServerConfig())
        tool = app._tool_manager.get_tool(name)
        listed = {item.name: item for item in await app.list_tools()}[name]
        return {
            "description": tool.description or "",
            "listed": listed.description or "",
            "docstring": tool.fn.__doc__ or "",
        }

    async def test_feedback_promises_the_append_lock_not_collision_freedom(self) -> None:
        texts = await self._texts("pipeline_feedback")
        for where, text in texts.items():
            with self.subTest(where=where):
                self.assertNotIn("cannot collide", text)
                self.assertIn(
                    "Appends to a local shared inbox under a cross-process lock; "
                    "ids are checked unique under the append lock.",
                    text,
                )
        # The number is the bound _append_lock waits, not a copy of it.
        bound = f"{inbox.APPEND_LOCK_TIMEOUT_S:g} s"
        for where, text in texts.items():
            with self.subTest(where=where):
                self.assertIn(
                    f"If another writer holds that lock for {bound}, the call "
                    "fails with inbox_busy and writes nothing.",
                    text,
                )

    async def test_resolve_names_inbox_busy(self) -> None:
        texts = await self._texts("pipeline_resolve")
        for where, text in texts.items():
            with self.subTest(where=where):
                self.assertIn("inbox_busy", text)
        bound = f"{inbox.APPEND_LOCK_TIMEOUT_S:g} s"
        for where, text in texts.items():
            with self.subTest(where=where):
                self.assertIn(
                    "The append takes the inbox's cross-process lock; if another "
                    f"writer holds it for {bound}, the call fails with inbox_busy "
                    "and writes nothing.",
                    text,
                )


# Each child appends `count` entries whose body is BODY_MAX_CHARS characters,
# nearly all the three-byte euro sign (U+20AC): about 24 KB a record, far
# over 8 KiB.
_WRITER = """
import sys, time
from pathlib import Path
from dayz_mcp import inbox
store, start = Path(sys.argv[1]), Path(sys.argv[2])
worker, count = int(sys.argv[3]), int(sys.argv[4])
inbox.INBOX_DIR = store.parent
inbox.FEEDBACK_PATH = store
print(ascii(str(Path(inbox.__file__).resolve())), flush=True)
while not start.exists():
    time.sleep(0.002)
for index in range(count):
    tag = f"w{worker}-r{index}:"
    body = tag + chr(0x20AC) * (inbox.BODY_MAX_CHARS - len(tag))
    inbox.append_feedback("finding", tag, body)
"""

# Takes byte 0 of the lock file the way _append_lock does, with no dayz_mcp
# import, and keeps it until its stdin closes.
_HOLDER = """
import os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
if os.name == "nt":
    import msvcrt
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
else:
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
print("locked", flush=True)
sys.stdin.readline()
"""

# Dies halfway through the one os.write of its record, holding the lock.
_DYING_WRITER = """
import os, sys
from pathlib import Path
from dayz_mcp import inbox
store = Path(sys.argv[1])
inbox.INBOX_DIR = store.parent
inbox.FEEDBACK_PATH = store
real_write = os.write
def write_half_then_die(fd, data):
    real_write(fd, data[: len(data) // 2])
    os._exit(91)
os.write = write_half_then_die
inbox.append_feedback("bug", "torn", "x" * 3000)
raise SystemExit(92)
"""


class CrossProcessAppendTest(_TemporaryInboxTest):
    @slow_test
    def test_concurrent_writers_of_records_over_8_kib_land_whole(self) -> None:
        writers, per_writer = 6, 15
        start = self.root / "start"
        this_inbox = ascii(str(Path(inbox.__file__).resolve()))
        children = [
            _python_child(
                _WRITER, str(inbox.FEEDBACK_PATH), str(start), str(worker), str(per_writer)
            )
            for worker in range(writers)
        ]
        try:
            for child in children:
                # Each child tests this checkout's inbox, not another copy.
                self.assertEqual(child.stdout.readline().strip(), this_inbox)
        finally:
            # All start together, and none is left waiting if the check failed.
            start.write_text("go", encoding="ascii")
            outcomes = [_communicate_owned(child, 120.0) for child in children]
        for child, (_stdout, stderr) in zip(children, outcomes):
            self.assertEqual(child.returncode, 0, stderr)

        *lines, tail = inbox.FEEDBACK_PATH.read_bytes().split(b"\n")
        records = []
        torn = 0
        for line in lines:
            try:
                records.append(json.loads(line.decode("utf-8")))
            except ValueError:
                torn += 1
        # (records that parse, lines that do not, bytes after the last b"\n")
        self.assertEqual((len(records), torn, tail), (writers * per_writer, 0, b""))
        self.assertGreater(min(len(line) for line in lines), 8 * 1024)
        self.assertEqual(len({record["id"] for record in records}), len(records))
        expected = {
            f"w{worker}-r{index}:" for worker in range(writers) for index in range(per_writer)
        }
        self.assertEqual({record["title"] for record in records}, expected)
        for record in records:
            tag = record["title"]
            self.assertEqual(
                record["body"], tag + chr(0x20AC) * (inbox.BODY_MAX_CHARS - len(tag))
            )
        listed = inbox.read_inbox(limit=100)
        self.assertEqual(listed["malformed"], 0)
        self.assertEqual(listed["count_total"], writers * per_writer)

    @slow_test
    def test_a_lock_held_by_another_process_fails_the_append_within_the_bound(self) -> None:
        before = _seed_store(_jsonl(_entry(_A_ID)))
        bound = 0.5
        holder = _python_child(
            _HOLDER, str(inbox.INBOX_DIR / _LOCK_NAME), stdin=subprocess.PIPE
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            with patch.object(inbox, "APPEND_LOCK_TIMEOUT_S", bound):
                for name, append in (
                    ("feedback", lambda: inbox.append_feedback("bug", "t", "b")),
                    ("resolution", lambda: inbox.append_resolution(_A_ID, "fix")),
                ):
                    with self.subTest(name):
                        started = time.monotonic()
                        with self.assertRaises(ValueError) as ctx:
                            append()
                        elapsed = time.monotonic() - started
                        self.assertEqual(str(ctx.exception), _busy(bound))
                        self.assertGreaterEqual(elapsed, bound)
                        self.assertLess(elapsed, bound + _SLACK_S)
            self.assertEqual(inbox.FEEDBACK_PATH.read_bytes(), before)
        finally:
            if holder.stdin is not None:
                holder.stdin.close()
                holder.stdin = None
            _stdout, stderr = _communicate_owned(holder, 10.0)
        self.assertEqual(holder.returncode, 0, stderr)
        # The holder is gone and so is its lock.
        entry = inbox.append_feedback("bug", "t", "b")
        self.assertEqual(inbox._feedback_state(entry["id"]), (True, False))

    @slow_test
    def test_a_writer_that_dies_mid_append_leaves_no_stale_lock(self) -> None:
        before = _seed_store(_jsonl(_entry(_A_ID)))
        child = _python_child(_DYING_WRITER, str(inbox.FEEDBACK_PATH))
        _stdout, stderr = _communicate_owned(child, 60.0)
        self.assertEqual(child.returncode, 91, stderr)
        torn = inbox.FEEDBACK_PATH.read_bytes()
        self.assertTrue(torn.startswith(before))
        self.assertGreater(len(torn), len(before))
        self.assertFalse(torn.endswith(b"\n"))
        # The lock died with the process: no wait for the bound, and the new
        # record starts its own line instead of joining the torn one.
        started = time.monotonic()
        entry = inbox.append_feedback("bug", "after", "body")
        elapsed = time.monotonic() - started
        listed = inbox.read_inbox(limit=100)
        self.assertEqual({item["id"] for item in listed["entries"]}, {_A_ID, entry["id"]})
        self.assertEqual(listed["malformed"], 1)
        self.assertEqual(inbox._feedback_state(entry["id"]), (True, False))
        self.assertLess(elapsed, inbox.APPEND_LOCK_TIMEOUT_S)


if __name__ == "__main__":
    unittest.main()
