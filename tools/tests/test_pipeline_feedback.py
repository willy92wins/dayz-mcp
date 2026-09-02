from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import inbox, server


_ID_RE = r"^fb-\d{8}-\d{6}-[0-9a-f]{4}$"


class InboxTest(unittest.TestCase):
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

    def test_append_feedback_writes_parseable_jsonl(self) -> None:
        self.assertFalse(inbox.INBOX_DIR.exists())
        entry = inbox.append_feedback(
            "bug", " title ", "the body", project=" proj ", platform="claude"
        )
        self.assertTrue(inbox.INBOX_DIR.is_dir())
        self.assertTrue(inbox.FEEDBACK_PATH.is_file())
        self.assertRegex(entry["id"], _ID_RE)
        self.assertEqual(entry["kind"], "bug")
        self.assertEqual(entry["title"], "title")
        self.assertEqual(entry["body"], "the body")
        self.assertEqual(entry["project"], "proj")
        self.assertEqual(entry["platform"], "claude")
        self.assertTrue(entry["ts"].endswith("Z"))
        self.assertEqual(entry["path"], str(inbox.FEEDBACK_PATH))
        raw = inbox.FEEDBACK_PATH.read_text(encoding="utf-8")
        self.assertTrue(raw.endswith("\n"))
        parsed = json.loads(raw.splitlines()[0])
        for field in ("id", "ts", "kind", "title", "body", "project", "platform"):
            self.assertEqual(parsed[field], entry[field])
        self.assertNotIn("path", parsed)

    def test_append_rejects_unknown_kind(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            inbox.append_feedback("nope", "title", "body")
        self.assertTrue(str(ctx.exception).startswith("bad_args"), str(ctx.exception))

    def test_append_rejects_empty_title(self) -> None:
        for title in ("", "   "):
            with self.subTest(title=repr(title)):
                with self.assertRaises(ValueError) as ctx:
                    inbox.append_feedback("bug", title, "body")
                self.assertTrue(str(ctx.exception).startswith("bad_args"), str(ctx.exception))

    def test_append_rejects_title_over_120(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            inbox.append_feedback("bug", "x" * 121, "body")
        message = str(ctx.exception)
        self.assertTrue(message.startswith("bad_args"), message)
        self.assertIn("title", message)
        self.assertIn("120", message)

    def test_append_rejects_empty_body(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            inbox.append_feedback("bug", "title", "")
        self.assertTrue(str(ctx.exception).startswith("bad_args"), str(ctx.exception))

    def test_append_rejects_non_str_types(self) -> None:
        cases = (
            ("kind", {"kind": 1, "title": "t", "body": "b"}),
            ("title", {"kind": "bug", "title": 1, "body": "b"}),
            ("body", {"kind": "bug", "title": "t", "body": None}),
            ("project", {"kind": "bug", "title": "t", "body": "b", "project": 1}),
            ("platform", {"kind": "bug", "title": "t", "body": "b", "platform": 1}),
        )
        for name, kwargs in cases:
            with self.subTest(name):
                with self.assertRaises(ValueError) as ctx:
                    inbox.append_feedback(**kwargs)
                self.assertTrue(str(ctx.exception).startswith("bad_args"), str(ctx.exception))

    def test_read_inbox_newest_first_and_count(self) -> None:
        first = inbox.append_feedback("bug", "one", "body-one")
        second = inbox.append_feedback("request", "two", "body-two")
        third = inbox.append_feedback("finding", "three", "body-three")
        result = inbox.read_inbox()
        self.assertEqual(result["count_total"], 3)
        self.assertEqual(result["unresolved_total"], 3)
        self.assertEqual(result["malformed"], 0)
        self.assertEqual(
            [item["id"] for item in result["entries"]],
            [third["id"], second["id"], first["id"]],
        )

    def test_resolve_hides_unless_included(self) -> None:
        first = inbox.append_feedback("bug", "one", "b1")
        second = inbox.append_feedback("bug", "two", "b2")
        third = inbox.append_feedback("bug", "three", "b3")
        before = inbox.FEEDBACK_PATH.read_bytes()
        inbox.append_resolution(second["id"], "first-fix")
        after = inbox.FEEDBACK_PATH.read_bytes()
        self.assertTrue(after.startswith(before))
        hidden = inbox.read_inbox(include_resolved=False)
        self.assertEqual(hidden["count_total"], 3)
        self.assertEqual(hidden["unresolved_total"], 2)
        self.assertEqual(
            {item["id"] for item in hidden["entries"]},
            {first["id"], third["id"]},
        )
        shown = inbox.read_inbox(include_resolved=True)
        self.assertEqual(len(shown["entries"]), 3)
        resolved = next(item for item in shown["entries"] if item["id"] == second["id"])
        self.assertEqual(resolved["resolution"], "first-fix")
        self.assertIn("resolved_ts", resolved)

    def test_resolve_last_wins(self) -> None:
        item = inbox.append_feedback("bug", "two", "b2")
        inbox.append_resolution(item["id"], "first-fix")
        inbox.append_resolution(item["id"], "second-fix")
        shown = inbox.read_inbox(include_resolved=True)
        self.assertEqual(shown["entries"][0]["resolution"], "second-fix")

    def test_resolution_evidence_ref_accepts_only_the_four_durable_roots(self) -> None:
        valid = (
            "reviews/r.json",
            "gates/g.md",
            "reports/a/b.json",
            "research/x-1/y_2.md",
        )
        item = inbox.append_feedback("bug", "evidence", "body")
        for evidence_ref in valid:
            with self.subTest(evidence_ref=evidence_ref):
                record = inbox.append_resolution(item["id"], "fix", evidence_ref=evidence_ref)
                self.assertEqual(record["evidence_ref"], evidence_ref)
        raw = inbox.FEEDBACK_PATH.read_text(encoding="utf-8")
        self.assertIn('"evidence_ref":"research/x-1/y_2.md"', raw)

        invalid = (
            "",
            ".",
            "..",
            "/reviews/r.json",
            "C:/reviews/r.json",
            "reviews\\r.json",
            "reviews:r.json",
            "https://example.test/r.json",
            "other/r.json",
            "reviews/r/../x.json",
            "reviews/\x01.json",
            "reviews/é.json",
            "reviews/r file.json",
            "reviews",
        )
        for evidence_ref in invalid:
            with self.subTest(evidence_ref=repr(evidence_ref)):
                with self.assertRaises(ValueError):
                    inbox.append_resolution(item["id"], "fix", evidence_ref=evidence_ref)

    def test_latest_resolution_without_evidence_ref_clears_the_previous_ref(self) -> None:
        item = inbox.append_feedback("bug", "two", "b2")
        before = inbox.FEEDBACK_PATH.read_bytes()
        inbox.append_resolution(item["id"], "first-fix", evidence_ref="reviews/first.md")
        inbox.append_resolution(item["id"], "second-fix", evidence_ref="gates/second.md")
        shown = inbox.read_inbox(include_resolved=True)
        resolved = shown["entries"][0]
        self.assertEqual(resolved["resolution"], "second-fix")
        self.assertEqual(resolved["evidence_ref"], "gates/second.md")
        inbox.append_resolution(item["id"], "third-fix")
        cleared = inbox.read_inbox(include_resolved=True)["entries"][0]
        self.assertEqual(cleared["resolution"], "third-fix")
        self.assertNotIn("evidence_ref", cleared)
        after = inbox.FEEDBACK_PATH.read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertEqual(len(after.splitlines()), 4)
        self.assertNotIn(b"evidence_ref", after.splitlines()[-1])
        self.assertNotIn(b"age_s", after)
        self.assertNotIn(b"age_label", after)

    def test_age_boundaries_are_derived_from_original_ts_without_persistence(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        cases = ((59, "59s"), (60, "1m"), (3599, "59m"), (3600, "1h"), (86399, "23h"), (86400, "1d"))
        entries = []
        for index, (seconds, label) in enumerate(cases):
            entry_id = f"fb-20260101-0000{index:02d}-abcd"
            stamp = (now - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
            entries.append({"id": entry_id, "ts": stamp, "kind": "bug", "title": "t", "body": "b"})
        entries.append({
            "resolves": "fb-20260101-000000-abcd",
            "ts": "2026-01-02T00:00:00Z",
            "resolution": "fix",
            "evidence_ref": "reviews/r.json",
        })
        invalid_id = "fb-20260101-000006-abcd"
        future_id = "fb-20260101-000007-abcd"
        entries.append({"id": invalid_id, "ts": "not-a-timestamp", "kind": "bug", "title": "t", "body": "b"})
        entries.append({"id": future_id, "ts": "2026-01-02T00:00:01Z", "kind": "bug", "title": "t", "body": "b"})
        inbox.INBOX_DIR.mkdir(parents=True)
        inbox.FEEDBACK_PATH.write_text(
            "".join(json.dumps(entry, separators=(",", ":")) + "\n" for entry in entries),
            encoding="utf-8",
        )
        result = inbox._read_inbox(now=now, limit=100, include_resolved=True)
        by_id = {entry["id"]: entry for entry in result["entries"]}
        for index, (seconds, label) in enumerate(cases):
            item = by_id[f"fb-20260101-0000{index:02d}-abcd"]
            self.assertEqual(item["age_s"], seconds)
            self.assertEqual(item["age_label"], label)
        self.assertEqual(by_id[invalid_id]["age_s"], None)
        self.assertEqual(by_id[invalid_id]["age_label"], None)
        self.assertEqual(by_id[invalid_id]["age_reason"], "invalid_timestamp")
        self.assertEqual(by_id[future_id]["age_s"], None)
        self.assertEqual(by_id[future_id]["age_label"], None)
        self.assertEqual(by_id[future_id]["age_reason"], "future_timestamp")
        raw = inbox.FEEDBACK_PATH.read_bytes()
        self.assertNotIn(b"age_s", raw)
        self.assertNotIn(b"age_label", raw)

    def test_malformed_line_is_counted(self) -> None:
        inbox.append_feedback("bug", "ok", "body")
        with inbox.FEEDBACK_PATH.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        inbox.append_feedback("request", "ok2", "body2")
        result = inbox.read_inbox()
        self.assertEqual(result["malformed"], 1)
        self.assertEqual(result["count_total"], 2)
        self.assertEqual(len(result["entries"]), 2)


class PipelineToolsTest(unittest.IsolatedAsyncioTestCase):
    def test_tools_registered_signatures(self) -> None:
        app, _runtime = server.build_app(server.ServerConfig())
        feedback = app._tool_manager.get_tool("pipeline_feedback")
        inbox_tool = app._tool_manager.get_tool("pipeline_inbox")
        resolve = app._tool_manager.get_tool("pipeline_resolve")
        self.assertIsNotNone(feedback)
        self.assertIsNotNone(inbox_tool)
        self.assertIsNotNone(resolve)

        fb_params = inspect.signature(feedback.fn).parameters
        self.assertIn("kind", fb_params)
        self.assertIn("title", fb_params)
        self.assertIn("body", fb_params)
        self.assertIn("project", fb_params)
        self.assertEqual(fb_params["project"].default, "")
        fb_required = feedback.parameters.get("required") or []
        self.assertEqual(set(fb_required), {"kind", "title", "body"})
        self.assertNotIn("project", fb_required)
        self.assertIn("project", feedback.parameters.get("properties", {}))

        in_params = inspect.signature(inbox_tool.fn).parameters
        self.assertEqual(in_params["limit"].default, 20)
        self.assertEqual(in_params["kind"].default, "")
        self.assertEqual(in_params["include_resolved"].default, False)
        in_required = inbox_tool.parameters.get("required") or []
        self.assertEqual(set(in_required), set())
        in_props = inbox_tool.parameters.get("properties", {})
        self.assertIn("limit", in_props)
        self.assertIn("kind", in_props)
        self.assertIn("include_resolved", in_props)

        rs_params = inspect.signature(resolve.fn).parameters
        self.assertIn("feedback_id", rs_params)
        self.assertIn("resolution", rs_params)
        rs_required = resolve.parameters.get("required") or []
        self.assertEqual(set(rs_required), {"feedback_id", "resolution"})

    async def test_feedback_tool_rejects_bad_kind(self) -> None:
        app, _runtime = server.build_app(server.ServerConfig())
        with self.assertRaises(Exception) as ctx:
            await app.call_tool(
                "pipeline_feedback",
                {"kind": "nope", "title": "t", "body": "b"},
            )
        self.assertEqual(type(ctx.exception).__name__, "ToolError")
        message = str(ctx.exception)
        self.assertTrue(
            "bad_args" in message or "tool_contribution" in message,
            message,
        )
