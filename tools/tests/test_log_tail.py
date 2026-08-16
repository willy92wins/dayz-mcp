from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import log_tail
from dayz_mcp.log_tail import LogTailError, TailMarker


class LogTailTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.log = self.root / "script_2026-08-07_03-50-13.log"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, text: str) -> None:
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(text)

    def test_second_call_returns_only_the_appended_lines(self) -> None:
        self._write("first\nsecond\n")
        first = log_tail.read_since(str(self.log), None)
        self.assertEqual(first["lines"], ["first", "second"])
        self.assertFalse(first["rotated"])

        self._write("third\n")
        second = log_tail.read_since(str(self.log), first["marker"])

        self.assertEqual(second["lines"], ["third"])
        self.assertGreater(second["marker"].offset, first["marker"].offset)

    def test_no_append_yields_no_lines_and_an_unchanged_offset(self) -> None:
        # Negative control demanded by the plan: without the offset assertion an
        # empty stub reader would satisfy `lines == []` and look correct.
        self._write("only\n")
        first = log_tail.read_since(str(self.log), None)
        self.assertEqual(first["lines"], ["only"])

        second = log_tail.read_since(str(self.log), first["marker"])

        self.assertEqual(second["lines"], [])
        self.assertEqual(second["marker"].offset, first["marker"].offset)
        self.assertEqual(second["marker"].size, first["marker"].size)
        self.assertFalse(second["rotated"])

    def test_truncated_file_is_reported_rotated_and_reread_without_loss(self) -> None:
        self._write("alpha\nbravo\ncharlie\n")
        first = log_tail.read_since(str(self.log), None)
        self.assertEqual(len(first["lines"]), 3)

        # A new launch recreates the profile log: shorter, different content.
        self.log.write_text("delta\n", encoding="utf-8")
        second = log_tail.read_since(str(self.log), first["marker"])

        self.assertTrue(second["rotated"])
        self.assertEqual(second["lines"], ["delta"])  # nothing skipped

    def test_shorter_than_offset_but_not_than_size_still_rotates(self) -> None:
        self._write("aaaa\nbbbb\n")
        first = log_tail.read_since(str(self.log), None)
        stale = TailMarker(path=str(self.log), offset=first["marker"].offset + 50, size=0)

        result = log_tail.read_since(str(self.log), stale)

        self.assertTrue(result["rotated"])
        self.assertEqual(result["lines"], ["aaaa", "bbbb"])

    def test_partial_trailing_line_is_withheld_until_complete(self) -> None:
        self._write("done\nhalf-")
        first = log_tail.read_since(str(self.log), None)
        self.assertEqual(first["lines"], ["done"])

        self._write("written\n")
        second = log_tail.read_since(str(self.log), first["marker"])

        self.assertEqual(second["lines"], ["half-written"])

    def test_invalid_utf8_in_a_partial_line_keeps_the_offset_byte_exact(self) -> None:
        # Regression: measuring the withheld tail in DECODED characters loses the
        # offset, because an invalid byte becomes U+FFFD and re-encodes to 3 bytes.
        # DayZ logs are not guaranteed clean UTF-8, so this is reachable.
        self.log.write_bytes(b"good line\n" + b"partial \xff")

        first = log_tail.read_since(str(self.log), None)

        self.assertEqual(first["lines"], ["good line"])
        self.assertEqual(first["marker"].offset, len(b"good line\n"))

        with self.log.open("ab") as handle:
            handle.write(b" rest\n")
        second = log_tail.read_since(str(self.log), first["marker"])

        self.assertEqual(len(second["lines"]), 1)
        self.assertTrue(second["lines"][0].startswith("partial "))
        self.assertTrue(second["lines"][0].endswith(" rest"))

    def test_multibyte_lines_advance_the_offset_by_bytes_not_characters(self) -> None:
        self.log.write_bytes("café ñandú\n".encode("utf-8"))
        result = log_tail.read_since(str(self.log), None)
        self.assertEqual(result["lines"], ["café ñandú"])
        self.assertEqual(result["marker"].offset, self.log.stat().st_size)

    def test_oversized_tail_keeps_the_newest_bytes_and_flags_truncation(self) -> None:
        self._write("".join(f"line-{index}\n" for index in range(5000)))
        result = log_tail.read_since(str(self.log), None, max_bytes=512)

        self.assertTrue(result["truncated"])
        self.assertIn("line-4999", result["lines"][-1])
        self.assertEqual(result["marker"].offset, result["marker"].size)

    def test_capping_lines_withholds_the_rest_instead_of_skipping_them(self) -> None:
        # H1 (Grok R21): returning N lines while advancing the marker past ALL
        # read lines drops the middle ones for good. Cap and marker must agree.
        self._write("".join(f"line-{index}\n" for index in range(10)))

        first = log_tail.read_since(str(self.log), None, max_lines=4)
        self.assertEqual(first["lines"], [f"line-{i}" for i in range(4)])
        self.assertTrue(first["truncated"])

        second = log_tail.read_since(str(self.log), first["marker"], max_lines=4)
        self.assertEqual(second["lines"], [f"line-{i}" for i in range(4, 8)])

        third = log_tail.read_since(str(self.log), second["marker"], max_lines=4)
        self.assertEqual(third["lines"], ["line-8", "line-9"])
        self.assertFalse(third["truncated"])
        # Nothing lost across the three capped reads.
        self.assertEqual(third["marker"].offset, self.log.stat().st_size)

    def test_rewrite_in_place_of_equal_size_is_detected_as_rotation(self) -> None:
        # H3 (Grok R21): same path, same-or-larger size -> the shrink check misses
        # it and the read resumes into unrelated bytes with rotated:false.
        self._write("aaaaa\nbbbbb\n")
        first = log_tail.read_since(str(self.log), None)
        self.assertEqual(first["lines"], ["aaaaa", "bbbbb"])

        self.log.write_text("ccccc\nddddd\neeeee\n", encoding="utf-8")  # bigger
        second = log_tail.read_since(str(self.log), first["marker"])

        self.assertTrue(second["rotated"])
        self.assertEqual(second["lines"], ["ccccc", "ddddd", "eeeee"])

    def test_appending_to_the_same_file_is_not_flagged_as_rotation(self) -> None:
        # Negative control for H3: identity must not report false rotations, or
        # every poll would re-read the whole log.
        self._write("one\n")
        first = log_tail.read_since(str(self.log), None)
        self._write("two\n")
        second = log_tail.read_since(str(self.log), first["marker"])

        self.assertFalse(second["rotated"])
        self.assertEqual(second["lines"], ["two"])

    def test_profiles_allowlist_accepts_only_run_profile_directories(self) -> None:
        # H4 (Grok R21): `profiles` arrives from the run manifest and decides
        # which host directory is read, so it is validated, not trusted.
        for allowed in (
            r"P:\DayZ_MCP_dev\_server\profiles",
            r"C:\Users\example\Documentos\DayZ Projects\DayZ_MCP_dev\_client\profiles",
            r"P:\DayZ_MCP_dev\_SERVER\Profiles",
        ):
            with self.subTest(allowed=allowed):
                self.assertTrue(log_tail.is_allowed_profiles_dir(allowed), allowed)

        for refused in (
            r"C:\Users\example\Documents",
            r"P:\DayZ_MCP_dev\_server",
            r"P:\DayZ_MCP_dev\_server\profiles\..\..\secrets",
            r"_server\profiles",
            "",
            None,
            123,
        ):
            with self.subTest(refused=refused):
                self.assertFalse(log_tail.is_allowed_profiles_dir(refused), refused)

    def test_marker_roundtrips_and_malformed_markers_are_rejected(self) -> None:
        markers = {str(self.log): TailMarker(path=str(self.log), offset=12, size=34)}
        encoded = log_tail.encode_marker(markers)
        decoded = log_tail.decode_marker(encoded)
        self.assertEqual(decoded[str(self.log)].offset, 12)
        self.assertEqual(decoded[str(self.log)].size, 34)
        self.assertEqual(log_tail.decode_marker(None), {})
        self.assertEqual(log_tail.decode_marker(""), {})

        for malformed in ("[]", "{", '{"p":[1]}', '{"p":[-1,2]}', '{"p":"x"}'):
            with self.subTest(malformed=malformed):
                with self.assertRaises(LogTailError):
                    log_tail.decode_marker(malformed)

    def test_missing_file_is_a_typed_error(self) -> None:
        with self.assertRaises(LogTailError):
            log_tail.read_since(str(self.root / "absent.rpt"), None)

    def test_resolve_log_files_picks_logs_oldest_first_and_ignores_others(self) -> None:
        import os

        (self.root / "a.rpt").write_text("x", encoding="utf-8")
        (self.root / "b.log").write_text("y", encoding="utf-8")
        (self.root / "notes.txt").write_text("z", encoding="utf-8")
        (self.root / "subdir").mkdir()
        os.utime(self.root / "a.rpt", (1_000_000, 1_000_000))
        os.utime(self.root / "b.log", (2_000_000, 2_000_000))

        found = log_tail.resolve_log_files(str(self.root))

        self.assertEqual([Path(item).name for item in found], ["a.rpt", "b.log"])
        self.assertEqual(log_tail.resolve_log_files(str(self.root / "absent")), [])


if __name__ == "__main__":
    unittest.main()
