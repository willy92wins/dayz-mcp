"""Source contracts for manual-gearbox settle and nearest-vehicle get-in."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests._addon_paths import addon_root


MOD_SCRIPTS = addon_root() / "scripts"
CAR_SCRIPT = MOD_SCRIPTS / "4_World" / "MCP_CarScript.c"
CLIENT_BRIDGE = MOD_SCRIPTS / "5_Mission" / "MCPClientBridge.c"
SERVER_PY = Path(__file__).resolve().parents[1] / "dayz_mcp" / "server.py"
KNOWN_CLEAR_STATICS = {
    "s_Active",
    "s_Car",
    # b95f: Clear also drops the motorbike drive.
    "s_Bike",
    "s_TickEngineReady",
    "s_TickThrottleSet",
}


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


def _body_re(source: str, pattern: str) -> str:
    match = re.search(pattern, source)
    if not match:
        raise AssertionError(f"missing: {pattern}")
    brace = source.index("{", match.end())
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated block: {pattern}")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _if_condition(text: str, start: int) -> str:
    match = re.compile(r"\bif\s*\(").search(text, start)
    if not match:
        raise AssertionError("missing if condition")
    depth = 0
    for index in range(match.end() - 1, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[match.end() : index]
    raise AssertionError("unterminated if condition")


class FbDeb907a1SourceContractTest(unittest.TestCase):
    def test_fb_deb9_07a1_shift_up_sits_behind_settle_interval_guard(self) -> None:
        source = CAR_SCRIPT.read_text(encoding="utf-8")
        drive = _method_body(source, "class MCPCarDrive")
        const_match = re.search(
            r"static\s+const\s+float\s+(\w+)\s*=\s*[0-9.]+\s*;", drive
        )
        self.assertIsNotNone(const_match)
        const_name = const_match.group(1)

        clear = _method_body(drive, "static void Clear()")
        extra = [
            name
            for name in re.findall(r"\b(s_\w+)\s*=", clear)
            if name not in KNOWN_CLEAR_STATICS
        ]
        self.assertEqual(len(extra), 1)
        stamp_name = extra[0]

        on_input = _method_body(source, "override void OnInput(float dt)")
        throttle_body = _body_re(on_input, r"if\s*\(\s*throttle\s*>\s*0\.1\s*\)")
        self.assertIn("ShiftUp", throttle_body)
        self.assertRegex(throttle_body, r"\bif\s*\(")
        self.assertLess(throttle_body.index("if"), throttle_body.index("ShiftUp"))
        self.assertIn("GetTickTime", throttle_body)
        self.assertIn(const_name, throttle_body)
        self.assertIn(stamp_name, throttle_body)
        self.assertRegex(_compact(throttle_body), r">=")
        inner = _body_re(throttle_body, r"if\s*\(")
        self.assertIn("ShiftUp", inner)
        self.assertIn(stamp_name, inner)
        self.assertNotIn("ShiftTo", throttle_body)
        self.assertIn("ShiftTo(CarGear.FIRST)", on_input)

    def test_fb_deb9_07a1_find_transport_near_client_picks_nearest_to_pos(self) -> None:
        source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        finder = _method_body(
            source, "protected Transport FindTransportNearClient(vector pos)"
        )
        loop = _body_re(finder, r"while\s*\(\s*i\s*<\s*m_ReadyObjects\.Count\s*\(\s*\)\s*\)")
        self.assertIsNone(re.search(r"\breturn\b", loop))
        self.assertRegex(loop, r"=\s*vehicle\b")
        self.assertRegex(loop, r"Distance(?:Sq)?\s*\(")
        self.assertRegex(loop, r"<(?!=)")
        self.assertNotRegex(loop, r"<=")
        self.assertIn("GetPosition", finder)
        self.assertGreater(finder.rindex("return"), finder.rindex("while"))

    def test_fb_deb9_p2_nearest_distance_feeds_comparison_and_accumulator(self) -> None:
        source = CLIENT_BRIDGE.read_text(encoding="utf-8")
        finder = _method_body(
            source, "protected Transport FindTransportNearClient(vector pos)"
        )
        loop = _body_re(
            finder, r"while\s*\(\s*i\s*<\s*m_ReadyObjects\.Count\s*\(\s*\)\s*\)"
        )
        cast = re.search(r"\bTransport\s+(\w+)\s*=\s*Transport\.Cast\s*\(", loop)
        self.assertIsNotNone(cast)
        vehicle = cast.group(1)
        vehicle_body = _body_re(loop, rf"if\s*\(\s*{re.escape(vehicle)}\s*\)")
        dist = re.search(
            rf"\bfloat\s+(\w+)\s*=\s*vector\.DistanceSq\s*\(\s*pos\s*,\s*{re.escape(vehicle)}\.GetPosition\s*\(\s*\)\s*\)",
            vehicle_body,
        )
        self.assertIsNotNone(dist)
        dist_name = dist.group(1)
        seed_if = re.search(r"\bif\s*\(\s*!\s*best\s*\)", vehicle_body)
        self.assertIsNotNone(seed_if)
        self.assertEqual(
            _compact(_if_condition(vehicle_body, seed_if.start())), "!best"
        )
        seed_body = _body_re(vehicle_body, r"if\s*\(\s*!\s*best\s*\)")
        self.assertIn(f"best={vehicle}", _compact(seed_body))
        acc = re.search(rf"\b(\w+)\s*=\s*{re.escape(dist_name)}\s*;", seed_body)
        self.assertIsNotNone(acc)
        acc_name = acc.group(1)
        self.assertNotEqual(acc_name, "best")
        self.assertNotEqual(acc_name, vehicle)
        open_rel = re.search(r"\{", vehicle_body[seed_if.end() :])
        self.assertIsNotNone(open_rel)
        open_at = seed_if.end() + open_rel.start()
        depth = 0
        close_at = None
        for index in range(open_at, len(vehicle_body)):
            if vehicle_body[index] == "{":
                depth += 1
            elif vehicle_body[index] == "}":
                depth -= 1
                if depth == 0:
                    close_at = index
                    break
        self.assertIsNotNone(close_at)
        trailing = vehicle_body[close_at + 1 :]
        else_if = re.search(r"^\s*else\s+if\s*\(", trailing)
        self.assertIsNotNone(else_if)
        self.assertEqual(
            _compact(_if_condition(trailing, else_if.start())),
            f"{dist_name}<{acc_name}",
        )
        else_body = _body_re(trailing, r"else\s+if\s*\(")
        else_compact = _compact(else_body)
        self.assertIn(f"best={vehicle}", else_compact)
        self.assertIn(f"{acc_name}={dist_name}", else_compact)
        while_at = re.search(r"\bwhile\s*\(", finder)
        self.assertIsNotNone(while_at)
        self.assertRegex(
            finder[: while_at.start()], rf"\bfloat\s+{re.escape(acc_name)}\b"
        )
        self.assertRegex(finder, r"\breturn\s+best\s*;")
        self.assertIn("return", finder)
        self.assertGreater(finder.rfind("return"), while_at.start())

    def test_fb_deb9_07a1_vehicle_get_in_client_description_names_nearest_to_pos(
        self,
    ) -> None:
        source = SERVER_PY.read_text(encoding="utf-8")
        func = re.search(r"async def vehicle_get_in_client\s*\(", source)
        self.assertIsNotNone(func)
        deco = source.rfind("@app.tool", 0, func.start())
        self.assertGreaterEqual(deco, 0)
        description = source[deco : func.start()]
        self.assertIn("Seat the connected", description)
        self.assertIn("nearby vehicle", description)
        self.assertIn("nearest to pos", description)
        self.assertIn("search radius", description)
        self.assertIn("does not place the player in the server crew", description)

    def test_fb_deb9_p2_settle_stamp_resets_per_drive_and_ignores_a_backward_clock(
        self,
    ) -> None:
        source = CAR_SCRIPT.read_text(encoding="utf-8")
        drive = _method_body(source, "class MCPCarDrive")
        const_match = re.search(
            r"static\s+const\s+float\s+(\w+)\s*=\s*([0-9.]+)\s*;", drive
        )
        self.assertIsNotNone(const_match)
        const_name = const_match.group(1)
        self.assertGreater(float(const_match.group(2)), 0.0)

        clear = _method_body(drive, "static void Clear()")
        extra = [
            name
            for name in re.findall(r"\b(s_\w+)\s*=", clear)
            if name not in KNOWN_CLEAR_STATICS
        ]
        self.assertEqual(len(extra), 1)
        stamp_name = extra[0]
        negative = r"(-[0-9]+(?:\.[0-9]+)?)\s*;"

        declared = re.search(
            rf"\bstatic\s+float\s+{stamp_name}\s*=\s*{negative}", drive
        )
        self.assertIsNotNone(declared)
        self.assertLess(float(declared.group(1)), 0.0)
        self.assertRegex(clear, rf"\b{stamp_name}\s*=\s*{negative}")

        set_body = _method_body(drive, "static void Set(")
        guard = re.search(r"\bif\s*\(", set_body)
        self.assertIsNotNone(guard)
        reset_clauses = _compact(_if_condition(set_body, guard.start())).split("||")
        self.assertCountEqual(
            reset_clauses,
            ["!s_Active", "s_Car!=car", "GetGame().GetTickTime()>s_DeadlineS"],
        )
        self.assertRegex(
            _body_re(set_body, r"\bif\s*\("), rf"\b{stamp_name}\s*=\s*{negative}"
        )
        deadline_write = re.search(r"\bs_DeadlineS\s*=(?!=)", set_body)
        self.assertIsNotNone(deadline_write)
        self.assertLess(guard.start(), deadline_write.start())

        on_input = _method_body(source, "override void OnInput(float dt)")
        throttle_body = _body_re(on_input, r"if\s*\(\s*throttle\s*>\s*0\.1\s*\)")
        clock = re.search(
            r"\bfloat\s+(\w+)\s*=\s*GetGame\(\)\.GetTickTime\(\)\s*;", throttle_body
        )
        self.assertIsNotNone(clock)
        now = clock.group(1)
        stamp = f"MCPCarDrive.{stamp_name}"
        shift_clauses = _compact(_if_condition(throttle_body, clock.end())).split("||")
        self.assertCountEqual(
            shift_clauses,
            [
                f"{stamp}<0.0",
                f"{now}<{stamp}",
                f"{now}-{stamp}>=MCPCarDrive.{const_name}",
            ],
        )


if __name__ == "__main__":
    unittest.main()
