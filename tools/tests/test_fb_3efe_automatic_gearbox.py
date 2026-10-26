"""vehicle_control shifts an automatic gearbox up to D before it gives it throttle.

fb-20261026-142708-3efe: OnInput shifted only when GearboxGetType() was MANUAL,
so an automatic (vanilla Offroad_02, LFQuad) stayed in N and SetThrottle revved
it in place. CarAutomaticGearboxMode is P, R, N, D (car.c:56-63 in 1.30.164200),
GearboxGetMode only reads it and no proto sets it; ShiftUp shifts the future
gear (car.c:256-269). The automatic branch shifts up behind the same settle
interval as the manual upshift, and the car gets no throttle until the mode
reads D, so going through R on the way from P never drives it backwards.
That ShiftUp walks the modes in the enum's order is native: the in-game round
checks it.

File-only: reads the Enforce sources; does not import the server stack.
"""

from __future__ import annotations

import re
import unittest

from tests._addon_paths import addon_root


CAR_SCRIPT = addon_root() / "scripts" / "4_World" / "MCP_CarScript.c"

ON_INPUT = re.escape("override void OnInput(float dt)")
APPLY_CONTROL = r"\bif\s*\(\s*applyControl\s*\)"
ENGINE_READY = r"\bif\s*\(\s*engineReady\s*\)"
MANUAL = r"\bif\s*\(\s*isManual\s*\)"
AUTOMATIC = (
    r"\belse\s+if\s*\(\s*throttle\s*>\s*0(?:\.0+)?\s*&&\s*"
    r"GearboxGetMode\(\)\s*!=\s*CarAutomaticGearboxMode\.D\s*\)"
)
GUARD = r"\bif\s*\("
STAMP = "MCPCarDrive.s_LastAutoShiftS"
SETTLE = "MCPCarDrive.AUTO_SHIFT_SETTLE_S"
NO_THROTTLE = "throttle = 0.0;"
SET_THROTTLE = "SetThrottle(throttle);"


def _without_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _span(source: str, pattern: str) -> tuple[int, int, int]:
    """Start of the match, and the body bounds of the brace block after it."""
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
                return match.start(), brace + 1, index
    raise AssertionError(f"unterminated block: {pattern}")


def _block(source: str, pattern: str) -> str:
    _, start, end = _span(source, pattern)
    return source[start:end]


def _depth_at(text: str, index: int) -> int:
    return text.count("{", 0, index) - text.count("}", 0, index)


def _ready(on_input: str) -> str:
    return _block(_block(on_input, APPLY_CONTROL), ENGINE_READY)


def _on_input() -> str:
    # No string literal in MCP_CarScript.c holds "//" or "/*".
    return _block(_without_comments(CAR_SCRIPT.read_text(encoding="utf-8")), ON_INPUT)


# Pre-fix shape (origin/main 2e844c5), trimmed to the lines the contract reads.
PRE_FIX_ON_INPUT = """
override void OnInput(float dt)
{
	bool applyControl = MCPCarDrive.s_Active && MCPCarDrive.s_Car == this;
	if (applyControl)
	{
		bool engineReady = true;
		if (engineReady)
		{
			float throttle = MCPCarDrive.s_Throttle;
			bool isManual = GearboxGetType() == CarGearboxType.MANUAL;
			if (isManual)
			{
				if (rpm > EngineGetRPMRedline() * 0.8)
				{
					if (throttle > 0.1)
					{
						float nowS = GetGame().GetTickTime();
						if (MCPCarDrive.s_LastAutoShiftS < 0.0 || nowS < MCPCarDrive.s_LastAutoShiftS || nowS - MCPCarDrive.s_LastAutoShiftS >= MCPCarDrive.AUTO_SHIFT_SETTLE_S)
						{
							ShiftUp();
							MCPCarDrive.s_LastAutoShiftS = nowS;
						}
					}
				}
				else if (GetGear() < CarGear.FIRST || (throttle > 0.0 && spd < 5.0))
				{
					ShiftTo(CarGear.FIRST);
				}
			}

			SetThrottle(throttle);
		}
	}
}
"""

# The automatic branch with the throttle left on while it shifts.
THROTTLE_KEPT_ON_INPUT = PRE_FIX_ON_INPUT.replace(
    "\t\t\t\t}\n\t\t\t}\n\n\t\t\tSetThrottle(throttle);",
    "\t\t\t\t}\n\t\t\t}\n"
    "\t\t\telse if (throttle > 0.0 && GearboxGetMode() != CarAutomaticGearboxMode.D)\n"
    "\t\t\t{\n"
    "\t\t\t\tfloat modeNowS = GetGame().GetTickTime();\n"
    "\t\t\t\tif (MCPCarDrive.s_LastAutoShiftS < 0.0 || modeNowS < MCPCarDrive.s_LastAutoShiftS || modeNowS - MCPCarDrive.s_LastAutoShiftS >= MCPCarDrive.AUTO_SHIFT_SETTLE_S)\n"
    "\t\t\t\t{\n"
    "\t\t\t\t\tShiftUp();\n"
    "\t\t\t\t\tMCPCarDrive.s_LastAutoShiftS = modeNowS;\n"
    "\t\t\t\t}\n"
    "\t\t\t}\n\n\t\t\tSetThrottle(throttle);",
)


class Fb3efeAutomaticGearboxTest(unittest.TestCase):
    def _assert_automatic_shifts_to_drive(self, on_input: str) -> None:
        ready = _ready(on_input)

        # The branch is the else of the manual block, inside the engine-ready block.
        _, _, manual_end = _span(ready, MANUAL)
        automatic_at, body_start, body_end = _span(ready, AUTOMATIC)
        self.assertEqual(ready[manual_end + 1 : automatic_at].strip(), "")
        self.assertEqual(_depth_at(ready, automatic_at), 0)
        body = ready[body_start:body_end]

        # ShiftUp sits behind the settle interval the manual upshift uses.
        guard_at, guard_start, guard_end = _span(body, GUARD)
        condition = body[guard_at:guard_start]
        self.assertIn(STAMP, condition)
        self.assertIn(SETTLE, condition)
        self.assertIn(">=", condition)
        self.assertIn("GetGame().GetTickTime()", body[:guard_at])
        guarded = body[guard_start:guard_end]
        self.assertEqual(guarded.count("ShiftUp();"), 1)
        self.assertRegex(guarded, re.escape(STAMP) + r"\s*=\s*\w+\s*;")
        self.assertLess(guarded.index("ShiftUp();"), guarded.index(STAMP))
        self.assertNotIn("ShiftTo", body)
        self.assertNotIn("ShiftDown", body)

        # No throttle until D, on every tick of the branch, not only when it shifts.
        self.assertEqual(body.count(NO_THROTTLE), 1)
        no_throttle_at = body.index(NO_THROTTLE)
        self.assertEqual(_depth_at(body, no_throttle_at), 0)
        self.assertGreater(no_throttle_at, guard_end)
        self.assertGreater(ready.index(SET_THROTTLE), body_end)

    def test_fb_3efe_an_automatic_shifts_up_to_d_before_it_gets_throttle(self) -> None:
        self._assert_automatic_shifts_to_drive(_on_input())

    def test_fb_3efe_the_manual_branch_is_unchanged(self) -> None:
        manual = _block(_ready(_on_input()), MANUAL)
        self.assertNotIn("GearboxGetMode", manual)
        self.assertEqual(manual.count("ShiftUp();"), 1)
        self.assertEqual(manual.count("ShiftTo(CarGear.FIRST);"), 1)

    def test_fb_3efe_pre_fix_and_throttle_kept_shapes_fail_the_contract(self) -> None:
        self.assertNotEqual(THROTTLE_KEPT_ON_INPUT, PRE_FIX_ON_INPUT)
        for label, source in (
            ("pre_fix", PRE_FIX_ON_INPUT),
            ("throttle_kept", THROTTLE_KEPT_ON_INPUT),
        ):
            with self.subTest(label):
                with self.assertRaises(AssertionError):
                    self._assert_automatic_shifts_to_drive(_block(source, ON_INPUT))


if __name__ == "__main__":
    unittest.main()
