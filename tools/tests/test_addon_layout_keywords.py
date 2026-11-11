"""Guard addon .layout files against the unsupported 'name' keyword.

In DayZ GUI .layout files, each widget's name comes from its class header
(for example ``FrameWidgetClass MCPDialogRoot {``); ``name`` is not a keyword
of the format. DayZDiag 1.30.164289 writes
``GUI (E): Unknown keyword/data 'name'`` to the client error log once for
every ``name "..."`` line whenever it loads the layout
(``addon/gui/layouts/mcp_dialog.layout`` had 28 such lines,
fb-20261110-170143-6a2c).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from tests._addon_paths import addon_root  # noqa: E402

_NAME_LINE_RE = re.compile(r'^\s*name\s+"[^"]*"\s*$')

_SAMPLE_LAYOUT_WITH_NAME = (
    "FrameWidgetClass SampleRoot {\n"
    "\tvisible 0\n"
    "\tposition 0 0\n"
    "\tsize 1 1\n"
    "\t{\n"
    "\t\tButtonWidgetClass X {\n"
    '\t\t\tname "X"\n'
    '\t\t\ttext "OK"\n'
    '\t\t\tfont "gui/fonts/sdf_MetronBook24"\n'
    "\t\t}\n"
    "\t}\n"
    "}\n"
)

_SAMPLE_LAYOUT_WITHOUT_NAME = (
    "FrameWidgetClass SampleRoot {\n"
    "\tvisible 0\n"
    "\tposition 0 0\n"
    "\tsize 1 1\n"
    "\t{\n"
    "\t\tButtonWidgetClass X {\n"
    '\t\t\ttext "OK"\n'
    '\t\t\tfont "gui/fonts/sdf_MetronBook24"\n'
    "\t\t}\n"
    "\t}\n"
    "}\n"
)


def name_lines(text: str) -> list[int]:
    """Return 1-based line numbers matching ``^\\s*name\\s+"[^"]*"\\s*$``."""
    return [
        lineno
        for lineno, line in enumerate(text.splitlines(), start=1)
        if _NAME_LINE_RE.match(line) is not None
    ]


class AddonLayoutKeywordsTest(unittest.TestCase):
    def test_sample_layout_with_name_line_returns_its_line_number(self) -> None:
        self.assertEqual(name_lines(_SAMPLE_LAYOUT_WITH_NAME), [7])

    def test_same_sample_layout_without_name_line_returns_empty_list(self) -> None:
        self.assertEqual(
            _SAMPLE_LAYOUT_WITH_NAME.replace('\t\t\tname "X"\n', "", 1),
            _SAMPLE_LAYOUT_WITHOUT_NAME,
        )
        self.assertEqual(name_lines(_SAMPLE_LAYOUT_WITHOUT_NAME), [])

    def test_addon_layouts_contain_no_name_lines(self) -> None:
        root = addon_root()
        layouts = sorted(p for p in root.rglob("*.layout") if p.is_file())
        self.assertTrue(
            layouts,
            f"no .layout files found under addon root {root}",
        )
        offenders: list[str] = []
        for path in layouts:
            lines = name_lines(path.read_text(encoding="utf-8"))
            if lines:
                rel = f"{root.name}/{path.relative_to(root).as_posix()}"
                joined = ", ".join(str(line) for line in lines)
                offenders.append(f"{rel}: lines {joined}")
        self.assertEqual(
            offenders,
            [],
            "addon .layout files must not use the unsupported 'name' keyword: "
            + "; ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
