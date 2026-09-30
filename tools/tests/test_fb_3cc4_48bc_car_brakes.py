"""vehicle_control brakes whatever the engine state and gives the driverless brake back.

fb-20260822-191204-3cc4: OnInput applied SetBrake/SetHandbrake only once the
engine reached idle rpm, and below idle it started the engine by itself, so a
brake-only vehicle_control could leave the car unbraked and start the engine.

fb-20260822-191204-48bc: OnInput turns SetBrakesActivateWithoutDriver off while
it drives and MCPCarDrive.Clear never turned it back on. Vanilla
ActionPushCar.OnEndServer restores it (actionpushcar.c:111).

48bc review P3: MCPCarDrive.Set reassigned s_Car when a different car took
over without a Clear, so the previous car kept its driverless brake off. Set
now restores it on the previous car before the reassignment.

File-only: reads the Enforce sources; does not import the server stack.
"""

from __future__ import annotations

import re
import unittest

from tests._addon_paths import addon_root


MOD_SCRIPTS = addon_root() / "scripts"
CAR_SCRIPT = MOD_SCRIPTS / "4_World" / "MCP_CarScript.c"
CLIENT_BRIDGE = MOD_SCRIPTS / "5_Mission" / "MCPClientBridge.c"

ON_INPUT = re.escape("override void OnInput(float dt)")
DRIVE_CLASS = r"\bclass\s+MCPCarDrive\b"
CLEAR = re.escape("static void Clear()")
APPLY_CONTROL = r"\bif\s*\(\s*applyControl\s*\)"
TTL_EXPIRY = (
    r"\bif\s*\(\s*applyControl\s*&&\s*GetGame\(\)\.GetTickTime\(\)\s*>\s*"
    r"MCPCarDrive\.s_DeadlineS\s*\)"
)
BELOW_IDLE = r"\bif\s*\(\s*rpm\s*<\s*EngineGetRPMIdle\(\)\s*\)"
THROTTLE_ASKED = r"\bif\s*\(\s*MCPCarDrive\.s_Throttle\s*>\s*0(?:\.0+)?\s*\)"
ENGINE_READY = r"\bif\s*\(\s*engineReady\s*\)"
CAR_GUARD = r"\bif\s*\(\s*s_Car\s*\)"
SET = re.escape("static void Set(")
TAKEOVER_GUARD = r"\bif\s*\(\s*s_Car\s*&&\s*s_Car\s*!=\s*car\s*\)"

BRAKE = "SetBrake(MCPCarDrive.s_Brake);"
HANDBRAKE = "SetHandbrake(MCPCarDrive.s_Handbrake);"
RESTORE = "s_Car.SetBrakesActivateWithoutDriver(true);"
TURN_OFF = "SetBrakesActivateWithoutDriver(false);"
DROP = "s_Car = null;"
REASSIGN = "s_Car = car;"


def _without_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _block(source: str, pattern: str) -> str:
    """Body of the first brace block after pattern, without the braces."""
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


def _depth_at(text: str, index: int) -> int:
    return text.count("{", 0, index) - text.count("}", 0, index)


def _car_code() -> str:
    # No string literal in this file holds "//" or "/*", so stripping is safe.
    return _without_comments(CAR_SCRIPT.read_text(encoding="utf-8"))


def _on_input() -> str:
    return _block(_car_code(), ON_INPUT)


def _drive_clear() -> str:
    return _block(_block(_car_code(), DRIVE_CLASS), CLEAR)


def _drive_set() -> str:
    return _block(_block(_car_code(), DRIVE_CLASS), SET)


# Pre-fix shape (origin/main 847c563), trimmed to the lines the contracts read.
PRE_FIX_ON_INPUT = """
override void OnInput(float dt)
{
	bool applyControl = MCPCarDrive.s_Active && MCPCarDrive.s_Car == this;
	if (applyControl)
	{
		float rpm = EngineGetRPM();
		bool engineReady = true;
		if (rpm < EngineGetRPMIdle())
		{
			if (rpm < 1.0 && EngineIsOn())
			{
				EngineStop();
			}
			else if (!EngineIsOn())
			{
				EngineStart();
			}
			engineReady = false;
		}
		MCPCarDrive.s_TickEngineReady = engineReady;
		if (engineReady)
		{
			float throttle = MCPCarDrive.s_Throttle;
			SetThrottle(throttle);
			MCPCarDrive.s_TickThrottleSet = true;
			SetSteering(MCPCarDrive.s_Steer);
			SetBrake(MCPCarDrive.s_Brake);
			SetHandbrake(MCPCarDrive.s_Handbrake);
			SetBrakesActivateWithoutDriver(false);
		}
	}
}
"""

PRE_FIX_CLEAR = """
static void Clear()
{
	s_Active = false;
	s_Car = null;
	s_TickEngineReady = false;
	s_TickThrottleSet = false;
	s_LastAutoShiftS = -1.0;
}
"""

# The guard makes a restore after the drop a silent no-op.
RESTORE_AFTER_DROP_CLEAR = """
static void Clear()
{
	s_Active = false;
	s_Car = null;
	if (s_Car)
	{
		s_Car.SetBrakesActivateWithoutDriver(true);
	}
}
"""

# Pre-fix Set (origin/main 65bff29): a takeover drops the previous car as is.
PRE_FIX_SET = """
static void Set(CarScript car, float throttle, float steer, float brake, float handbrake, float deadlineS)
{
	if (!s_Active || s_Car != car || GetGame().GetTickTime() > s_DeadlineS)
	{
		s_LastAutoShiftS = -1.0;
	}

	s_Car = car;
	s_Throttle = throttle;
	s_Active = true;
}
"""

# After the reassignment s_Car is car, so the guard never holds.
RESTORE_AFTER_REASSIGN_SET = """
static void Set(CarScript car, float throttle, float steer, float brake, float handbrake, float deadlineS)
{
	s_Car = car;
	if (s_Car && s_Car != car)
	{
		s_Car.SetBrakesActivateWithoutDriver(true);
	}
	s_Active = true;
}
"""

# Without the car comparison every command re-brakes the car it keeps driving.
RESTORE_SAME_CAR_SET = """
static void Set(CarScript car, float throttle, float steer, float brake, float handbrake, float deadlineS)
{
	if (s_Car)
	{
		s_Car.SetBrakesActivateWithoutDriver(true);
	}
	s_Car = car;
	s_Active = true;
}
"""


class CarBrakesSourceContractTest(unittest.TestCase):
    def _assert_brakes_ignore_engine_state(self, on_input: str) -> None:
        control = _block(on_input, APPLY_CONTROL)
        for call in (BRAKE, HANDBRAKE):
            self.assertEqual(on_input.count(call), 1, call)
            self.assertIn(call, control)
            # Top level of the applyControl block: no engine condition around it.
            self.assertEqual(_depth_at(control, control.index(call)), 0, call)
        self.assertEqual(on_input.count("SetBrake("), 1)
        self.assertEqual(on_input.count("SetHandbrake("), 1)
        ready = _block(control, ENGINE_READY)
        self.assertNotIn("SetBrake(", ready)
        self.assertNotIn("SetHandbrake(", ready)

    def _assert_engine_start_needs_throttle(self, on_input: str) -> None:
        below_idle = _block(on_input, BELOW_IDLE)
        asked = _block(below_idle, THROTTLE_ASKED)
        for call in ("EngineStart();", "EngineStop();"):
            self.assertEqual(on_input.count(call), 1, call)
            self.assertIn(call, asked)
        # Below idle the engine is not ready whatever the throttle.
        self.assertIn("engineReady = false;", below_idle)
        self.assertNotIn("engineReady = false;", asked)

    def _assert_clear_restores_driverless_brake(self, clear: str) -> None:
        self.assertIn(RESTORE, _block(clear, CAR_GUARD))
        self.assertEqual(clear.count("SetBrakesActivateWithoutDriver("), 1)
        self.assertEqual(clear.count(DROP), 1)
        self.assertLess(clear.index(RESTORE), clear.index(DROP))

    def _assert_takeover_restores_previous_car(self, set_body: str) -> None:
        # The restore is the whole body of the takeover guard, the guard is not
        # nested in another block, and it runs before s_Car is reassigned.
        takeover = _block(set_body, TAKEOVER_GUARD)
        self.assertEqual(takeover.strip(), RESTORE)
        guard_at = re.search(TAKEOVER_GUARD, set_body).start()
        self.assertEqual(_depth_at(set_body, guard_at), 0)
        self.assertEqual(set_body.count("SetBrakesActivateWithoutDriver("), 1)
        self.assertEqual(set_body.count(REASSIGN), 1)
        self.assertLess(set_body.index(RESTORE), set_body.index(REASSIGN))
        self.assertNotIn(TURN_OFF, set_body)

    def test_fb_3cc4_brake_and_handbrake_apply_whatever_the_engine_state(self) -> None:
        self._assert_brakes_ignore_engine_state(_on_input())

    def test_fb_3cc4_engine_starts_by_itself_only_for_a_throttle_request(self) -> None:
        self._assert_engine_start_needs_throttle(_on_input())

    def test_fb_3cc4_throttle_steering_and_gear_still_wait_for_the_engine(self) -> None:
        on_input = _on_input()
        ready = _block(_block(on_input, APPLY_CONTROL), ENGINE_READY)
        for call in (
            "SetThrottle(throttle);",
            "SetSteering(MCPCarDrive.s_Steer);",
            "ShiftUp();",
            "ShiftTo(CarGear.FIRST);",
        ):
            self.assertEqual(on_input.count(call), 1, call)
            self.assertIn(call, ready)

    def test_fb_48bc_clear_restores_driverless_brake_before_dropping_the_car(self) -> None:
        self._assert_clear_restores_driverless_brake(_drive_clear())

    def test_fb_48bc_only_the_engine_ready_drive_turns_the_driverless_brake_off(
        self,
    ) -> None:
        code = _car_code()
        self.assertEqual(code.count(TURN_OFF), 1)
        # Two restores since the 48bc review P3: Clear, and Set on a takeover.
        self.assertEqual(code.count(RESTORE), 2)
        self.assertEqual(code.count("SetBrakesActivateWithoutDriver("), 3)
        self.assertEqual(_drive_clear().count(RESTORE), 1)
        self.assertEqual(_drive_set().count(RESTORE), 1)
        ready = _block(_block(_on_input(), APPLY_CONTROL), ENGINE_READY)
        self.assertIn(TURN_OFF, ready)

    def test_fb_48bc_p3_a_takeover_restores_the_previous_cars_driverless_brake(
        self,
    ) -> None:
        self._assert_takeover_restores_previous_car(_drive_set())

    def test_fb_48bc_p3_pre_fix_and_misplaced_restores_fail_the_contract(self) -> None:
        for label, source in (
            ("pre_fix", PRE_FIX_SET),
            ("restore_after_reassign", RESTORE_AFTER_REASSIGN_SET),
            ("restore_same_car", RESTORE_SAME_CAR_SET),
        ):
            with self.subTest(label):
                with self.assertRaises(AssertionError):
                    self._assert_takeover_restores_previous_car(_block(source, SET))

    def test_fb_48bc_ttl_expiry_release_and_shutdown_all_go_through_clear(self) -> None:
        self.assertIn("MCPCarDrive.Clear();", _block(_on_input(), TTL_EXPIRY))
        bridge = CLIENT_BRIDGE.read_text(encoding="utf-8")
        release = _block(bridge, re.escape("protected bool DispatchVehicleRelease("))
        shutdown = _block(bridge, re.escape("void Shutdown()"))
        self.assertIn("MCPCarDrive.Clear();", release)
        self.assertIn("MCPCarDrive.Clear();", shutdown)

    def test_fb_3cc4_48bc_pre_fix_shape_fails_the_contracts(self) -> None:
        on_input = _block(PRE_FIX_ON_INPUT, ON_INPUT)
        with self.assertRaises(AssertionError):
            self._assert_brakes_ignore_engine_state(on_input)
        with self.assertRaises(AssertionError):
            self._assert_engine_start_needs_throttle(on_input)
        with self.assertRaises(AssertionError):
            self._assert_clear_restores_driverless_brake(_block(PRE_FIX_CLEAR, CLEAR))
        with self.assertRaises(AssertionError):
            self._assert_clear_restores_driverless_brake(
                _block(RESTORE_AFTER_DROP_CLEAR, CLEAR)
            )


if __name__ == "__main__":
    unittest.main()
