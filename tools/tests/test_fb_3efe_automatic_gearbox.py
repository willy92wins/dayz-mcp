"""vehicle_control shifts an automatic gearbox up to D before it gives it throttle.

fb-20261026-142708-3efe: OnInput shifted only when GearboxGetType() was MANUAL,
so an automatic (vanilla Offroad_02, LFQuad) stayed in N and SetThrottle revved
it in place. CarAutomaticGearboxMode is P, R, N, D (car.c:56-63 in 1.30.164200),
GearboxGetMode only reads it and no proto sets it; ShiftUp shifts the future
gear (car.c:256-269). The automatic branch shifts up behind the same settle
interval as the manual upshift, and the car is asked for no throttle until
the mode reads D, R included. That ShiftUp walks the modes in the enum's
order is native: the in-game round checks it.

The 9/11 round then found an automatic in D at 616.5 rpm against an idle of
625 that never moved: below idle OnInput never gave throttle, a rule copied
from vanilla's DIAG_DEVELOPER autopilot, which drives a manual gearbox.
Vanilla counts a stall as engine on with rpm < 1. Below idle an automatic is
now ready once its engine runs; a manual keeps the old rule.
BelowIdleOnInputTest runs the real OnInput body on a fake car
(tests.enforce_subset_helpers).

File-only: reads the Enforce sources; does not import the server stack.
"""

from __future__ import annotations

import re
import unittest
from types import SimpleNamespace

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import translate


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


def _on_input(source: str | None = None) -> str:
    # No string literal in MCP_CarScript.c holds "//" or "/*".
    if source is None:
        source = CAR_SCRIPT.read_text(encoding="utf-8")
    return _block(_without_comments(source), ON_INPUT)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# The manual block of OnInput at origin/main 2e844c5, comments out and blanks
# collapsed. A manual gearbox does what it did there.
MANUAL_AT_2E844C5 = (
    "if (rpm > EngineGetRPMRedline() * 0.8) { if (throttle > 0.1) { "
    "float nowS = GetGame().GetTickTime(); "
    "if (MCPCarDrive.s_LastAutoShiftS < 0.0 || nowS < MCPCarDrive.s_LastAutoShiftS || "
    "nowS - MCPCarDrive.s_LastAutoShiftS >= MCPCarDrive.AUTO_SHIFT_SETTLE_S) { "
    "ShiftUp(); MCPCarDrive.s_LastAutoShiftS = nowS; } } } "
    "else if (GetGear() < CarGear.FIRST || (throttle > 0.0 && spd < 5.0)) { "
    "ShiftTo(CarGear.FIRST); }"
)
# The settle guard of the manual upshift, read with the automatic branch's clock.
AUTOMATIC_GUARD = (
    "if (MCPCarDrive.s_LastAutoShiftS < 0.0 || modeNowS < MCPCarDrive.s_LastAutoShiftS || "
    "modeNowS - MCPCarDrive.s_LastAutoShiftS >= MCPCarDrive.AUTO_SHIFT_SETTLE_S)"
)
ASSIGNS_THROTTLE = r"\bthrottle\s*=(?!=)"
# Any other write: a compound assignment, ++ or --.
REWRITES_THROTTLE = r"\bthrottle\s*(?:[-+*/%&|^]=|\+\+|--)|(?:\+\+|--)\s*throttle\b"


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

        # ShiftUp sits behind the settle interval the manual upshift uses, with
        # the stamp subtracted from this tick's clock.
        guard_at, guard_start, guard_end = _span(body, GUARD)
        self.assertEqual(_flat(body[guard_at : guard_start - 1]), AUTOMATIC_GUARD)
        self.assertEqual(_flat(body[:guard_at]), "float modeNowS = GetGame().GetTickTime();")
        guarded = body[guard_start:guard_end]
        self.assertEqual(guarded.count("ShiftUp();"), 1)
        self.assertEqual(len(re.findall(re.escape(STAMP) + r"\s*=(?!=)", guarded)), 1)
        self.assertRegex(guarded, re.escape(STAMP) + r"\s*=\s*modeNowS\s*;")
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

    def _assert_manual_unchanged(self, on_input: str) -> None:
        ready = _ready(on_input)
        self.assertEqual(_flat(_block(ready, MANUAL)), MANUAL_AT_2E844C5)

        # throttle is written once after its declaration, in the automatic
        # branch, and never with a compound operator, ++ or --, so a manual
        # gets the throttle it was asked for.
        self.assertEqual(re.findall(REWRITES_THROTTLE, ready), [])
        writes = [m.start() for m in re.finditer(ASSIGNS_THROTTLE, ready)]
        self.assertEqual(len(writes), 2)
        self.assertEqual(_flat(ready[writes[0] : ready.index(";", writes[0]) + 1]),
                         "throttle = MCPCarDrive.s_Throttle;")
        _, body_start, body_end = _span(ready, AUTOMATIC)
        self.assertTrue(body_start < writes[1] < body_end)

    def test_fb_3efe_the_manual_branch_is_unchanged(self) -> None:
        self._assert_manual_unchanged(_on_input())

    def test_fb_3efe_pre_fix_and_throttle_kept_shapes_fail_the_contract(self) -> None:
        self.assertNotEqual(THROTTLE_KEPT_ON_INPUT, PRE_FIX_ON_INPUT)
        for label, source in (
            ("pre_fix", PRE_FIX_ON_INPUT),
            ("throttle_kept", THROTTLE_KEPT_ON_INPUT),
        ):
            with self.subTest(label):
                with self.assertRaises(AssertionError):
                    self._assert_automatic_shifts_to_drive(_block(source, ON_INPUT))

    def test_fb_3efe_mutants_of_the_shipped_source_fail(self) -> None:
        source = CAR_SCRIPT.read_text(encoding="utf-8")

        def mutant(old: str, new: str) -> str:
            # A missing anchor is an error of this test, not a rejection by the
            # contract: LookupError makes the test error where a rejection fails.
            if source.count(old) != 1:
                raise LookupError(f"mutant anchor not found exactly once: {old!r}")
            return source.replace(old, new)

        for label, old, new in (
            ("manual_throttle_cut", "\t\t\t\t\t\tShiftTo(CarGear.FIRST);\n",
             "\t\t\t\t\t\tShiftTo(CarGear.FIRST);\n\t\t\t\t\t\tthrottle = 0.0;\n"),
            ("compound_throttle_cut", "\t\t\t\tSetThrottle(throttle);\n",
             "\t\t\t\tthrottle -= throttle;\n\t\t\t\tSetThrottle(throttle);\n"),
        ):
            shipped = mutant(old, new)
            with self.subTest(label), self.assertRaises(AssertionError):
                self._assert_manual_unchanged(_on_input(shipped))
        for label, old, new in (
            ("interval_inverted", "modeNowS - MCPCarDrive.s_LastAutoShiftS",
             "MCPCarDrive.s_LastAutoShiftS - modeNowS"),
            ("settle_comparison_flipped",
             "modeNowS - MCPCarDrive.s_LastAutoShiftS >= MCPCarDrive.AUTO_SHIFT_SETTLE_S",
             "modeNowS - MCPCarDrive.s_LastAutoShiftS < MCPCarDrive.AUTO_SHIFT_SETTLE_S"),
            ("stamp_not_the_clock", "MCPCarDrive.s_LastAutoShiftS = modeNowS;",
             "MCPCarDrive.s_LastAutoShiftS = 0;"),
        ):
            shipped = mutant(old, new)
            with self.subTest(label), self.assertRaises(AssertionError):
                self._assert_automatic_shifts_to_drive(_on_input(shipped))


AUTOMATIC_GEARBOX = 1
MANUAL_GEARBOX = 2
MODE_N = 2
MODE_D = 3


class _Car:
    """A CarScript as OnInput reads and drives it, recording what it is told."""

    def __init__(self, *, gearbox: int, rpm: float, engine_on: bool, mode: int = MODE_D) -> None:
        self.gearbox = gearbox
        self.rpm = rpm
        self.engine_on = engine_on
        self.mode = mode
        self.calls: list[str] = []
        self.throttle: float | None = None

    def bind(self, namespace: dict) -> None:
        for name in (
            "EngineGetRPM", "GetSpeedometerAbsolute", "EngineGetRPMIdle", "EngineIsOn",
            "EngineStop", "EngineStart", "SetBrake", "SetHandbrake", "GearboxGetType",
            "EngineGetRPMRedline", "GetGear", "ShiftUp", "ShiftTo", "GearboxGetMode",
            "SetThrottle", "SetSteering", "SetBrakesActivateWithoutDriver",
        ):
            namespace[name] = getattr(self, name)

    def EngineGetRPM(self) -> float:
        return self.rpm

    def GetSpeedometerAbsolute(self) -> float:
        return 0.0

    def EngineGetRPMIdle(self) -> float:
        return 625.0

    def EngineIsOn(self) -> bool:
        return self.engine_on

    def EngineStop(self) -> None:
        self.calls.append("EngineStop")
        self.engine_on = False

    def EngineStart(self) -> None:
        self.calls.append("EngineStart")

    def SetBrake(self, _value: float) -> None:
        self.calls.append("SetBrake")

    def SetHandbrake(self, _value: float) -> None:
        self.calls.append("SetHandbrake")

    def GearboxGetType(self) -> int:
        return self.gearbox

    def EngineGetRPMRedline(self) -> float:
        return 6000.0

    def GetGear(self) -> int:
        return 2

    def ShiftUp(self) -> None:
        self.calls.append("ShiftUp")

    def ShiftTo(self, gear: int) -> None:
        self.calls.append(f"ShiftTo({gear})")

    def GearboxGetMode(self) -> int:
        return self.mode

    def SetThrottle(self, value: float) -> None:
        self.calls.append("SetThrottle")
        self.throttle = value

    def SetSteering(self, _value: float) -> None:
        self.calls.append("SetSteering")

    def SetBrakesActivateWithoutDriver(self, _value: bool) -> None:
        self.calls.append("SetBrakesActivateWithoutDriver")


class BelowIdleOnInputTest(unittest.TestCase):
    """3efe in the 9/11 round: the real OnInput body, one tick, below idle."""

    def _tick(self, car: _Car, throttle: float = 0.6, source: str | None = None) -> SimpleNamespace:
        if source is None:
            source = CAR_SCRIPT.read_text(encoding="utf-8")
        drive = SimpleNamespace(
            s_Active=True, s_Car=car, s_DeadlineS=100.0, s_Throttle=throttle, s_Steer=0.0,
            s_Brake=0.0, s_Handbrake=0.0, s_LastAutoShiftS=-1.0, AUTO_SHIFT_SETTLE_S=0.3,
            s_TickEngineReady=None, s_TickThrottleSet=None, Clear=lambda: None,
        )
        namespace: dict = {
            "super": SimpleNamespace(OnInput=lambda _dt: None),
            "this": car,
            "MCPCarDrive": drive,
            "MCPVehicleTrace": SimpleNamespace(Capture=lambda _car, _dt: None),
            "GetGame": lambda: SimpleNamespace(GetTickTime=lambda: 10.0),
            "CarGearboxType": SimpleNamespace(AUTOMATIC=AUTOMATIC_GEARBOX, MANUAL=MANUAL_GEARBOX),
            "CarAutomaticGearboxMode": SimpleNamespace(P=0, R=1, N=MODE_N, D=MODE_D),
            "CarGear": SimpleNamespace(FIRST=2),
        }
        car.bind(namespace)
        on_input = translate(source, "override void OnInput(float dt)", namespace)
        on_input(0.05)
        return drive

    def test_an_automatic_in_d_below_idle_with_its_engine_on_gets_throttle(self) -> None:
        car = _Car(gearbox=AUTOMATIC_GEARBOX, rpm=616.5, engine_on=True)
        drive = self._tick(car)
        self.assertIs(drive.s_TickEngineReady, True)
        self.assertIs(drive.s_TickThrottleSet, True)
        self.assertEqual(car.throttle, 0.6)
        self.assertNotIn("EngineStop", car.calls)
        self.assertNotIn("EngineStart", car.calls)

    def test_a_manual_below_idle_keeps_the_old_rule(self) -> None:
        car = _Car(gearbox=MANUAL_GEARBOX, rpm=616.5, engine_on=True)
        drive = self._tick(car)
        self.assertIs(drive.s_TickEngineReady, False)
        self.assertIs(drive.s_TickThrottleSet, False)
        self.assertIsNone(car.throttle)

    def test_a_stalled_automatic_is_stopped_and_not_ready(self) -> None:
        car = _Car(gearbox=AUTOMATIC_GEARBOX, rpm=0.5, engine_on=True)
        drive = self._tick(car)
        self.assertEqual([call for call in car.calls if call.startswith("Engine")], ["EngineStop"])
        self.assertIs(drive.s_TickEngineReady, False)
        self.assertIsNone(car.throttle)

    def test_an_automatic_with_its_engine_off_is_started_and_not_ready(self) -> None:
        car = _Car(gearbox=AUTOMATIC_GEARBOX, rpm=0.0, engine_on=False)
        drive = self._tick(car)
        self.assertEqual([call for call in car.calls if call.startswith("Engine")], ["EngineStart"])
        self.assertIs(drive.s_TickEngineReady, False)
        self.assertIsNone(car.throttle)

    def test_brakes_apply_and_a_brake_only_command_starts_nothing(self) -> None:
        car = _Car(gearbox=AUTOMATIC_GEARBOX, rpm=0.0, engine_on=False)
        drive = self._tick(car, throttle=0.0)
        self.assertIn("SetBrake", car.calls)
        self.assertIn("SetHandbrake", car.calls)
        self.assertNotIn("EngineStart", car.calls)
        self.assertIs(drive.s_TickEngineReady, False)

    def test_the_never_ready_below_idle_shape_fails_the_automatic_case(self) -> None:
        source = CAR_SCRIPT.read_text(encoding="utf-8")
        guard = "if (GearboxGetType() == CarGearboxType.MANUAL || !EngineIsOn() || rpm < 1.0)"
        self.assertEqual(source.count(guard), 1)
        before = source.replace(guard, "if (true)")
        car = _Car(gearbox=AUTOMATIC_GEARBOX, rpm=616.5, engine_on=True)
        drive = self._tick(car, source=before)
        self.assertIs(drive.s_TickEngineReady, False)
        self.assertIsNone(car.throttle)


if __name__ == "__main__":
    unittest.main()
