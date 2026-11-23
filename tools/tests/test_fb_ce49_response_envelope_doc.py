"""ce49: the reply envelope of the bridge's tools is documented, and dayz_test_close says how a close that leaves the
game open answers.

Inbox fb-20261122-020925-ce49 (LFSkateboard): 264 of 264 surface_query replies and 1489 of 1489 scene_raycast replies
carried "error": "" and "ok": 1 with valid data, so a reader that took the mere presence of an error key as a failure
would have dropped a whole survey. Every bridge reply is an MCPResult with `bool ok` and `string error`
(addon/scripts/5_Mission/MCPMessages.c), and error stays empty when nothing failed. dayz_test_close has no ok: a close
that leaves the game open answers stop_required=true and a reason with no error field (dayz_test_tool.py), except a
failed server close, which keeps its error; dayz_test_stop then ends the run with a forced kill.

The README assertions read tools/README-mcp.md, as test_docs_truth does; the description assertions read what a client
gets (resolve_effective_schemas, the list_tools path), split into sentences at the whitespace after a full stop.
"""

from __future__ import annotations

import functools
import re
import unittest
from pathlib import Path

from dayz_mcp.effective_schema import resolve_effective_schemas

README = Path(__file__).resolve().parents[1] / "README-mcp.md"
_HEADING = re.compile(r"^(#{2,4})\s+(.*\S)\s*$")


def _envelope_sections() -> list[str]:
    """Every README section whose heading names both ok and error, from its heading to the next heading of the same
    or a higher level."""
    lines = README.read_text(encoding="utf-8").splitlines()
    sections = []
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if not m or not (re.search(r"\bok\b", m.group(2)) and re.search(r"\berror\b", m.group(2))):
            continue
        level = len(m.group(1))
        body = []
        for nxt in lines[i + 1:]:
            n = _HEADING.match(nxt)
            if n and len(n.group(1)) <= level:
                break
            body.append(nxt)
        sections.append("\n".join(body))
    return sections


@functools.lru_cache(maxsize=1)
def _close_sentences() -> tuple[str, ...]:
    text = resolve_effective_schemas()["dayz_test_close"]["description"]
    return tuple(s for s in re.split(r"(?<=\.)\s+", text) if s.strip())


class ReadmeEnvelopeTest(unittest.TestCase):
    def test_readme_has_one_section_on_ok_and_error(self) -> None:
        self.assertEqual(len(_envelope_sections()), 1, "README-mcp needs one section whose heading names ok and error")

    def test_the_section_says_how_to_read_ok_and_an_empty_error(self) -> None:
        sections = _envelope_sections()
        self.assertTrue(sections)
        text = " ".join(sections[0].split())
        for phrase in ("0 or 1", "empty", "not a failure", "satisfied"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


class CloseDescriptionTest(unittest.TestCase):
    def test_a_close_that_leaves_the_game_open_is_described(self) -> None:
        about = [s for s in _close_sentences() if "stays open" in s]
        self.assertTrue(about, "dayz_test_close's description has no sentence about a game that stays open")
        for phrase in ("stop_required=true", "reason", "no error field", "dayz_test_stop"):
            with self.subTest(phrase=phrase):
                self.assertTrue(any(phrase in s for s in about), about)

    def test_the_failed_server_close_keeps_its_error(self) -> None:
        text = " ".join(_close_sentences())
        self.assertIn("If that server close fails, the result is not graceful, stop_required is true, and the original "
                      "error is kept.", text)


if __name__ == "__main__":
    unittest.main()
