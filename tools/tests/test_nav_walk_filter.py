"""Offline source contract for the walking navmesh filter, not an engine test.

Enum ordinals verified in vanilla scripts/3_Game/ai/aiworld.c:1-26;
PGFilter.SetFlags takes bitmasks at :61-65. The native bit convention and
open-sea rejection still need an in-game check with the rebuilt PBO.
"""

from __future__ import annotations

import re
import unittest

from tests._addon_paths import addon_root


BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
# Independent snapshot of vanilla's sequential enum, not values from the addon.
POLY_ORDINALS = {
    "NONE": 0,
    "WALK": 1,
    "DISABLED": 2,
    "DOOR": 3,
    "INSIDE": 4,
    "SWIM": 5,
    "SWIM_SEA": 6,
}


def _method_body(source: str, name: str) -> str:
    match = re.search(rf"\bprotected\s+\w+\s+{name}\([^)]*\)\s*\{{", source)
    if match is None:
        raise AssertionError(f"missing method {name}")
    depth = 1
    for end in range(match.end(), len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
        if depth == 0:
            return source[match.end():end]
    raise AssertionError(f"unclosed method {name}")


class NavWalkFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = BRIDGE_PATH.read_bytes()
        cls.source = cls.raw.decode("utf-8")
        # Comments cannot satisfy the assertions on executable code.
        cls.code = re.sub(r"/\*.*?\*/|//[^\n]*", "", cls.source, flags=re.S)
        cls.builder = _method_body(cls.code, "BuildNavWalkFilter")
        cls.dispatch = _method_body(cls.code, "DispatchNavPathQuery")

    def _offset(self, code: str) -> int:
        declarations = re.findall(
            r"\bconst\s+int\s+NAV_POLY_BIT_OFFSET\s*=\s*(\d+)\s*;", code
        )
        self.assertEqual(len(declarations), 1, "declare the bit offset exactly once")
        return int(declarations[0])

    def _members(self, variable: str) -> list[str]:
        assignments = re.findall(rf"\bint\s+{variable}\s*=\s*([^;]+);", self.builder)
        self.assertEqual(len(assignments), 1, f"missing or duplicate {variable}")
        members = []
        for term in assignments[0].split("|"):
            match = re.fullmatch(
                r"\s*\(\s*1\s*<<\s*\(\s*PGPolyFlags\.(\w+)"
                r"\s*-\s*NAV_POLY_BIT_OFFSET\s*\)\s*\)\s*", term
            )
            self.assertIsNotNone(match, f"flag must use the shared offset: {term}")
            members.append(match.group(1))
        return members

    def _mask(self, variable: str, offset: int) -> int:
        mask = 0
        for member in self._members(variable):
            mask |= 1 << (POLY_ORDINALS[member] - offset)
        return mask

    def test_one_offset_constant_is_one(self) -> None:
        self.assertEqual(self._offset(self.code), 1)
        self.assertEqual(self.code.count("NAV_POLY_BIT_OFFSET"), 7)

    def test_masks_use_exact_named_members_and_shared_offset(self) -> None:
        self.assertCountEqual(self._members("includeFlags"), ["WALK", "DOOR", "INSIDE"])
        self.assertCountEqual(self._members("excludeFlags"), ["DISABLED", "SWIM", "SWIM_SEA"])

    def test_masks_are_13_and_50(self) -> None:
        offset = self._offset(self.code)
        self.assertEqual(self._mask("includeFlags", offset), 13)
        self.assertEqual(self._mask("excludeFlags", offset), 50)
        self.assertEqual(self._mask("includeFlags", offset) & self._mask("excludeFlags", offset), 0)

    def test_offset_zero_alone_selects_the_alternative_convention(self) -> None:
        changed, count = re.subn(
            r"(\bconst\s+int\s+NAV_POLY_BIT_OFFSET\s*=\s*)1(\s*;)",
            r"\g<1>0\2", self.code,
        )
        self.assertEqual(count, 1)
        offset = self._offset(changed)
        self.assertEqual(offset, 0)
        self.assertEqual(self._mask("includeFlags", offset), 26)
        self.assertEqual(self._mask("excludeFlags", offset), 100)

    def test_set_flags_configures_the_returned_filter(self) -> None:
        statements = [line.strip() for line in self.builder.splitlines() if line.strip()]
        self.assertEqual(len(statements), 5)
        self.assertEqual(statements[0], "PGFilter filter = new PGFilter();")
        self.assertTrue(statements[1].startswith("int includeFlags = "))
        self.assertTrue(statements[2].startswith("int excludeFlags = "))
        self.assertEqual(statements[3], "filter.SetFlags(includeFlags, excludeFlags, 0);")
        self.assertEqual(statements[4], "return filter;")

    def test_both_samples_and_find_path_use_the_same_filter(self) -> None:
        self.assertEqual(re.findall(r"\bfilter\s*=\s*([^;]+);", self.dispatch), ["BuildNavWalkFilter()"])
        calls = re.findall(r"aiWorld\.(SampleNavmeshPosition|FindPath)\(([^;\n]+?)\)", self.dispatch)
        self.assertEqual(calls, [
            ("SampleNavmeshPosition", "fromPos, command.args.snap_radius, filter, fromSnapped"),
            ("SampleNavmeshPosition", "toPos, command.args.snap_radius, filter, toSnapped"),
            ("FindPath", "fromSnapped, toSnapped, filter, waypoints"),
        ])
        self.assertLess(self.dispatch.index("filter = BuildNavWalkFilter();"), self.dispatch.index("aiWorld.SampleNavmeshPosition("))
        self.assertNotIn("filter.SetFlags", self.dispatch)

    def test_failed_samples_return_the_endpoint_error(self) -> None:
        for endpoint in ("from", "to"):
            with self.subTest(endpoint=endpoint):
                condition = re.escape(
                    f"if (!aiWorld.SampleNavmeshPosition({endpoint}Pos, "
                    f"command.args.snap_radius, filter, {endpoint}Snapped))"
                )
                self.assertRegex(
                    self.dispatch,
                    condition + r'\s*\{\s*result.ok = false;\s*result.error = "'
                    + f"no_navmesh_at_{endpoint}" + r'";\s*return true;\s*\}',
                )

    def test_bridge_is_utf8_without_bom(self) -> None:
        self.assertFalse(self.raw.startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
