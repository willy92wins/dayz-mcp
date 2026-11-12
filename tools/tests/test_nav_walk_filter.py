"""Offline contracts and Enforce-subset checks for nav_path_query (d5d6).

Runtime PGPolyFlags bit values, partial-path behaviour of AIWorld.FindPath and
the PGFilter instance leak were measured in game on DayZDiag 1.30.164289
(REPORT-st1.md, inbox d5d6 / fb-20261111-203344-d5d6).
"""

from __future__ import annotations

import math
import re
from types import SimpleNamespace
import unittest

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceString,
    EnforceVector,
    translate,
)


BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
DISPATCH_SIGNATURE = "protected bool DispatchNavPathQuery(MCPCommand command, MCPResult result)"

# Runtime PGPolyFlags bit values measured on DayZDiag 1.30.164289
# (REPORT-st1.md:7, :55, :62). PGFilter keeps 16-bit masks.
POLY_BIT_VALUES = {
    "WALK": 1,
    "DISABLED": 2,
    "DOOR": 4,
    "INSIDE": 8,
    "SWIM": 16,
    "SWIM_SEA": 32,
}
PG_FILTER_MASK_BITS = 0xFFFF
_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_MASK_TOKEN = re.compile(r"\s+|PGPolyFlags\.(\w+)|\d+|[()|&^+\-]|<<|>>")


def _without_comments(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return text if text.startswith('"') else re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


def _method_body(source: str, header_pattern: str) -> str:
    match = re.search(rf"{header_pattern}\s*\{{", source)
    if match is None:
        raise AssertionError(f"missing method matching {header_pattern}")
    depth = 1
    for end in range(match.end(), len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
        if depth == 0:
            return source[match.end():end]
    raise AssertionError(f"unclosed method matching {header_pattern}")


def _distance_3d(first: EnforceVector, second: EnforceVector) -> float:
    return math.sqrt(
        (first[0] - second[0]) ** 2
        + (first[1] - second[1]) ** 2
        + (first[2] - second[2]) ** 2
    )


def _vec_arg(coords: tuple[float, float, float]) -> EnforceArray:
    arr = EnforceArray()
    for value in coords:
        arr.Insert(float(value))
    return arr


class _FakePGFilter:
    def __init__(self) -> None:
        self.flags: tuple[int, int, int] | None = None

    def SetFlags(self, include_flags: int, exclude_flags: int, exclusive_flags: int) -> None:
        self.flags = (include_flags, exclude_flags, exclusive_flags)


class _FakeAIWorld:
    def __init__(
        self,
        *,
        from_snapped: tuple[float, float, float] | None = (0.0, 0.0, 0.0),
        to_snapped: tuple[float, float, float] | None = (10.0, 0.0, 0.0),
        found: bool = True,
        waypoints: list[tuple[float, float, float]] | None = None,
    ) -> None:
        self.from_snapped = from_snapped
        self.to_snapped = to_snapped
        self.found = found
        self.waypoints = waypoints if waypoints is not None else []
        self.filters_seen: list[object] = []
        self._sample_calls = 0

    def SampleNavmeshPosition(
        self,
        _pos: EnforceVector,
        _radius: float,
        pg_filter: object,
        sampled: EnforceVector,
    ) -> bool:
        self.filters_seen.append(pg_filter)
        self._sample_calls += 1
        target = self.from_snapped if self._sample_calls % 2 == 1 else self.to_snapped
        if target is None:
            return False
        sampled[0] = float(target[0])
        sampled[1] = float(target[1])
        sampled[2] = float(target[2])
        return True

    def FindPath(
        self,
        _from_pos: EnforceVector,
        _to_pos: EnforceVector,
        pg_filter: object,
        out_waypoints: EnforceArray,
    ) -> bool:
        self.filters_seen.append(pg_filter)
        for point in self.waypoints:
            out_waypoints.Insert(EnforceVector(point))
        return self.found


class NavWalkFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = BRIDGE_PATH.read_bytes()
        cls.source = cls.raw.decode("utf-8")
        # Comments cannot satisfy the assertions on executable code.
        cls.code = _without_comments(cls.source)
        cls.builder = _method_body(cls.code, r"\bprotected\s+PGFilter\s+BuildNavWalkFilter\(\)")
        cls.dispatch = _method_body(cls.code, r"\bprotected\s+bool\s+DispatchNavPathQuery\([^)]*\)")

    def _assignment_rhs(self, variable: str) -> str:
        assignments = re.findall(rf"\bint\s+{variable}\s*=\s*([^;]+);", self.builder)
        self.assertEqual(len(assignments), 1, f"missing or duplicate {variable}")
        return assignments[0].strip()

    def _eval_mask(self, variable: str) -> int:
        expr = self._assignment_rhs(variable)
        pos = 0
        while pos < len(expr):
            match = _MASK_TOKEN.match(expr, pos)
            self.assertIsNotNone(match, f"unsupported mask token at {expr[pos:]!r}")
            member = match.group(1)
            if member is not None:
                self.assertIn(member, POLY_BIT_VALUES, f"unknown PGPolyFlags member {member}")
            pos = match.end()
        env = {"__builtins__": {}, "PGPolyFlags": SimpleNamespace(**POLY_BIT_VALUES)}
        value = eval(expr, env, {})
        self.assertIsInstance(value, int)
        return value & PG_FILTER_MASK_BITS

    def _unshifted_members(self, variable: str) -> list[str]:
        expr = self._assignment_rhs(variable)
        members = []
        for term in expr.split("|"):
            match = re.fullmatch(r"\s*PGPolyFlags\.(\w+)\s*", term)
            self.assertIsNotNone(match, f"flag term must be unshifted PGPolyFlags.<NAME>: {term!r}")
            members.append(match.group(1))
        return members

    def _build_dispatch_harness(self):
        created_filters: list[_FakePGFilter] = []
        world_holder = SimpleNamespace(ai_world=None)
        world = SimpleNamespace(GetAIWorld=lambda: world_holder.ai_world)
        game = SimpleNamespace(GetWorld=lambda: world)
        float_consts = {
            name: float(value)
            for name, value in re.findall(
                r"\bprotected\s+const\s+float\s+(\w+)\s*=\s*(-?[0-9.]+)\s*;",
                self.code,
            )
        }

        def build_filter() -> _FakePGFilter:
            flt = _FakePGFilter()
            flt.SetFlags(
                self._eval_mask("includeFlags"),
                self._eval_mask("excludeFlags"),
                0,
            )
            created_filters.append(flt)
            return flt

        def is_finite_float(value: object) -> bool:
            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

        def is_finite_vec3(arr: object) -> bool:
            return (
                isinstance(arr, EnforceArray)
                and arr.Count() == 3
                and all(is_finite_float(arr.Get(idx)) for idx in range(3))
            )

        def vector_to_array(vec: EnforceVector, out_arr: EnforceArray) -> None:
            out_arr.Insert(vec[0])
            out_arr.Insert(vec[1])
            out_arr.Insert(vec[2])

        namespace: dict[str, object] = {
            "GetGame": lambda: game,
            "IsFiniteFloat": is_finite_float,
            "IsFiniteVec3": is_finite_vec3,
            "Vector": lambda x, y, z: EnforceVector((x, y, z)),
            "VectorToArray": vector_to_array,
            "vector": SimpleNamespace(Distance=_distance_3d),
            "TVectorArray": EnforceArray,
            "array": EnforceArray,
            "BuildNavWalkFilter": build_filter,
            "m_NavWalkFilter": None,
            **float_consts,
        }
        # MCPArgs names the wire field `from`, which is a Python keyword that
        # enforce_subset_helpers refuses directly; renaming only that attribute
        # lets translate execute the real DispatchNavPathQuery body.
        rewritten = re.sub(r"\bcommand\.args\.from\b", "command.args.from_arg", self.source)
        dispatch = translate(
            rewritten,
            DISPATCH_SIGNATURE,
            namespace,
            class_types={"World", "AIWorld", "PGFilter", "TVectorArray", "MCPCommand", "MCPResult", "array"},
            members=("m_NavWalkFilter",),
        )
        return dispatch, namespace, world_holder, created_filters

    @staticmethod
    def _new_result() -> SimpleNamespace:
        return SimpleNamespace(
            ok=False,
            error=EnforceString(b""),
            points=None,
            length_m=0.0,
            from_snapped=None,
            to_snapped=None,
        )

    @staticmethod
    def _new_command(
        from_pos: tuple[float, float, float] = (6380.0, 9.32, 2630.0),
        to_pos: tuple[float, float, float] = (6429.52, 9.32, 2630.0),
        snap_radius: float = 2.0,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            args=SimpleNamespace(
                from_arg=_vec_arg(from_pos),
                to=_vec_arg(to_pos),
                snap_radius=snap_radius,
            )
        )

    def test_no_bit_offset_constant_remains(self) -> None:
        self.assertNotIn("NAV_POLY_BIT_OFFSET", self.source)
        self.assertIsNone(re.search(r"<<\s*\(?\s*PGPolyFlags\.", self.code))

    def test_masks_use_exact_unshifted_members(self) -> None:
        self.assertCountEqual(
            self._unshifted_members("includeFlags"),
            ["WALK", "DOOR", "INSIDE"],
        )
        self.assertCountEqual(
            self._unshifted_members("excludeFlags"),
            ["DISABLED", "SWIM", "SWIM_SEA"],
        )

    def test_masks_are_13_and_50(self) -> None:
        masks = (
            self._eval_mask("includeFlags"),
            self._eval_mask("excludeFlags"),
        )
        self.assertEqual(masks, (13, 50))
        self.assertEqual(masks[0] & masks[1], 0)

    def test_set_flags_configures_the_returned_filter(self) -> None:
        statements = [line.strip() for line in self.builder.splitlines() if line.strip()]
        self.assertEqual(len(statements), 5)
        self.assertEqual(statements[0], "PGFilter filter = new PGFilter();")
        self.assertTrue(statements[1].startswith("int includeFlags = "))
        self.assertTrue(statements[2].startswith("int excludeFlags = "))
        self.assertEqual(statements[3], "filter.SetFlags(includeFlags, excludeFlags, 0);")
        self.assertEqual(statements[4], "return filter;")

    def test_both_samples_and_find_path_use_the_shared_filter(self) -> None:
        self.assertEqual(
            re.findall(r"\bm_NavWalkFilter\s*=\s*([^;]+);", self.dispatch),
            ["BuildNavWalkFilter()"],
        )
        self.assertEqual(
            re.findall(r"(?<!m_NavWalk)\bfilter\s*=\s*([^;]+);", self.dispatch),
            ["m_NavWalkFilter"],
        )
        calls = re.findall(r"aiWorld\.(SampleNavmeshPosition|FindPath)\(([^;\n]+?)\)", self.dispatch)
        self.assertEqual(calls, [
            ("SampleNavmeshPosition", "fromPos, command.args.snap_radius, filter, fromSnapped"),
            ("SampleNavmeshPosition", "toPos, command.args.snap_radius, filter, toSnapped"),
            ("FindPath", "fromSnapped, toSnapped, filter, waypoints"),
        ])
        self.assertLess(
            self.dispatch.index("filter = m_NavWalkFilter;"),
            self.dispatch.index("aiWorld.SampleNavmeshPosition("),
        )
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

    def test_partial_paths_beyond_half_metre_return_no_path(self) -> None:
        const_match = re.findall(
            r"\bprotected\s+const\s+float\s+NAV_END_MAX_DISTANCE_M\s*=\s*([0-9.]+)\s*;",
            self.code,
        )
        self.assertEqual(const_match, ["0.5"])
        with self.assertRaisesRegex(AssertionError, "identifier 'from' cannot be translated"):
            translate(
                self.source,
                DISPATCH_SIGNATURE,
                {},
                class_types={"World", "AIWorld", "PGFilter", "TVectorArray", "MCPCommand", "MCPResult", "array"},
                members=("m_NavWalkFilter",),
            )

        dispatch, _namespace, world_holder, _filters = self._build_dispatch_harness()
        start = (6380.0, 9.32, 2630.0)
        partial_cases = (
            # REPORT-st1.md:125 — Skalisty Island, 6845.5 m short.
            ("skalisty", (13560.0, 40.0, 3170.0), [start, (6730.7, 5.74, 2700.0)]),
            # REPORT-st1.md:126 — Elektrozavodsk, 3646.6 m short.
            ("elektrozavodsk", (10300.0, 13.35, 2330.0), [start, (6661.5, 5.97, 2573.3)]),
            # REPORT-st1.md:132 — Chernogorsk stairs, ~7.6 m short.
            ("chernogorsk_7_6m", (6431.3, 28.2, 2770.95), [start, (6431.3, 28.2, 2763.35)]),
            # Just beyond the 0.5 m tolerance (horizontal and vertical).
            ("just_over_horizontal", (6400.0, 9.32, 2630.0), [start, (6399.49, 9.32, 2630.0)]),
            ("just_over_vertical", (6400.0, 9.32, 2630.0), [start, (6400.0, 9.83, 2630.0)]),
        )
        for label, goal, points in partial_cases:
            with self.subTest(case=label):
                world_holder.ai_world = _FakeAIWorld(
                    from_snapped=start,
                    to_snapped=goal,
                    found=True,
                    waypoints=points,
                )
                result = self._new_result()
                self.assertTrue(dispatch(self._new_command(start, goal), result))
                self.assertEqual((result.ok, result.error), (False, EnforceString(b"no_path")))
                self.assertIsNone(result.points)

        accepted_cases = (
            ("exact_end", (6429.52, 9.32, 2630.0), [start, (6429.52, 9.32, 2630.0)]),
            ("at_half_metre_edge", (6400.0, 9.32, 2630.0), [start, (6399.5, 9.32, 2630.0)]),
            ("within_half_metre", (6400.0, 9.32, 2630.0), [start, (6399.75, 9.32, 2630.0)]),
        )
        for label, goal, points in accepted_cases:
            with self.subTest(case=label):
                world_holder.ai_world = _FakeAIWorld(
                    from_snapped=start,
                    to_snapped=goal,
                    found=True,
                    waypoints=points,
                )
                result = self._new_result()
                self.assertTrue(dispatch(self._new_command(start, goal), result))
                self.assertTrue(result.ok)
                self.assertEqual(result.error, EnforceString(b""))
                self.assertEqual(len(result.points), 6)

    def test_one_bridge_filter_is_reused_and_cleared_without_delete(self) -> None:
        # Pack rules 12 & 14: strong ref on the class member only, never delete.
        self.assertEqual(
            re.findall(r"\bprotected\s+ref\s+PGFilter\s+m_NavWalkFilter\s*;", self.code),
            ["protected ref PGFilter m_NavWalkFilter;"],
        )
        self.assertEqual(re.findall(r"\bnew\s+PGFilter\s*\(\s*\)", self.code), ["new PGFilter()"])
        self.assertIsNone(re.search(r"\bref\s+PGFilter\b", self.dispatch))
        self.assertIsNone(re.search(r"\bref\s+PGFilter\b", self.builder))

        destructor = _method_body(self.code, r"\bvoid\s+~MCPBridge\(\)")
        shutdown = _method_body(self.code, r"\bvoid\s+Shutdown\(\)")
        shutdown_instance = _method_body(self.code, r"\bstatic\s+void\s+ShutdownInstance\(\)")
        for body in (self.dispatch, self.builder, destructor, shutdown, shutdown_instance):
            self.assertIsNone(re.search(r"\bdelete\b", body))
        self.assertIn("m_NavWalkFilter = null;", destructor)
        self.assertIn("m_NavWalkFilter = null;", shutdown)
        self.assertIn("m_Instance.Shutdown();", shutdown_instance)
        self.assertIn("m_Instance = null;", shutdown_instance)

        dispatch, namespace, world_holder, created_filters = self._build_dispatch_harness()
        # Early refusals before AIWorld allocate no PGFilter at all.
        bad = self._new_result()
        self.assertTrue(dispatch(SimpleNamespace(args=None), bad))
        self.assertEqual((bad.ok, bad.error), (False, EnforceString(b"bad_args")))
        no_nav = self._new_result()
        self.assertTrue(dispatch(self._new_command(), no_nav))
        self.assertEqual((no_nav.ok, no_nav.error), (False, EnforceString(b"no_navmesh")))
        self.assertEqual(created_filters, [])

        # 13 queries across all four post-filter exits (matching ST-A1's 13 calls)
        # allocate only one PGFilter and pass that instance every time.
        start = (6380.0, 9.32, 2630.0)
        goal = (6429.52, 9.32, 2630.0)
        scenarios = [
            _FakeAIWorld(from_snapped=None, to_snapped=goal),
            _FakeAIWorld(from_snapped=start, to_snapped=None),
            _FakeAIWorld(from_snapped=start, to_snapped=goal, found=False, waypoints=[]),
            _FakeAIWorld(from_snapped=start, to_snapped=goal, found=True, waypoints=[start, (6400.0, 9.32, 2630.0)]),
        ] + [
            _FakeAIWorld(from_snapped=start, to_snapped=goal, found=True, waypoints=[start, goal])
            for _ in range(9)
        ]
        self.assertEqual(len(scenarios), 13)
        for world_fake in scenarios:
            world_holder.ai_world = world_fake
            dispatch(self._new_command(start, goal), self._new_result())

        self.assertEqual(len(created_filters), 1)
        shared = created_filters[0]
        self.assertIs(namespace["m_NavWalkFilter"], shared)
        self.assertEqual(shared.flags, (13, 50, 0))
        for world_fake in scenarios:
            self.assertTrue(all(flt is shared for flt in world_fake.filters_seen))

    def test_bridge_is_utf8_without_bom(self) -> None:
        self.assertFalse(self.raw.startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
