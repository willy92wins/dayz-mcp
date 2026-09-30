"""fb-20260930-065425-8779: an optional value travels with a presence flag.

A key absent from the command JSON reaches Enforce as 0 or false, not as the
MCPArgs constructor value. Comparing a value with the float.MAX sentinel
therefore let an object_anim read write phase 0 and a partial
world_weather_set zero the levels it did not name. Each optional value now
travels with a <field>_set bool, and these text contracts pin the three layers:
the Enforce consumers test the flag, the Python producers send it beside the
value and send neither when the value is not given, and the loopback ingress
refuses a value without its flag, a flag without its value and a flag that is
not true. Every refusal case is checked against an accepted pair that differs
from it in one key, so the refusal comes from the pairing rule and not from an
unknown key.
"""
from __future__ import annotations

import re
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from tests._addon_paths import addon_root


MISSION = addon_root() / "scripts" / "5_Mission"
BRIDGE_PATH = MISSION / "MCPBridge.c"
MESSAGES_PATH = MISSION / "MCPMessages.c"
FB_ID = "fb-20260930-065425-8779"
# value -> its presence flag, as the bridge and the tools spell them.
FLAG_OF = {
    "phase": "phase_set",
    "time_multiplier": "time_multiplier_set",
    "overcast": "overcast_set",
    "rain": "rain_set",
    "fog": "fog_set",
    "heading": "heading_set",
    "speed": "speed_set",
}
_POS = [10.0, 0.0, 20.0]
_DATE = {"year": 2026, "month": 9, "day": 30, "hour": 12, "minute": 0}
_INFECTED = {"type": "ZmbM_CitizenASkinny_Base", "pos": [1.0, 2.0, 3.0]}
_TIMES = {"time": 0.0, "min_duration": 0.0}

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')


def _without_comments(source: str) -> str:
    return _COMMENT_OR_STRING.sub(
        lambda match: match.group(0) if match.group(0).startswith('"') else " ",
        source,
    )


def _block_after(source: str, start: int) -> str:
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError("unterminated block")


def _method_body(source: str, signature: str) -> str:
    if source.count(signature) != 1:
        raise AssertionError(f"expected one {signature!r}, found {source.count(signature)}")
    return _block_after(source, source.index(signature))


def _if_block(body: str, condition: str) -> str:
    """The braced body of the one `if (<condition>)` in body."""
    marker = f"if ({condition})"
    if body.count(marker) != 1:
        raise AssertionError(f"expected one {marker!r}, found {body.count(marker)}")
    start = body.index(marker) + len(marker)
    if body[start:].lstrip()[:1] != "{":
        raise AssertionError(f"{marker!r} has no braced body")
    return _block_after(body, start)


def _pair_mutations(accepted: dict[str, Any], value: str) -> dict[str, dict[str, Any]]:
    """The one-key departures from an accepted pair that must be refused."""
    flag = FLAG_OF[value]
    without_flag = {key: item for key, item in accepted.items() if key != flag}
    without_value = {key: item for key, item in accepted.items() if key != value}
    mutations = {
        "value_without_flag": without_flag,
        "flag_without_value": without_value,
    }
    for label, bad in (
        ("flag_false", False),
        ("flag_int_1", 1),
        ("flag_float_1", 1.0),
        ("flag_string", "true"),
        ("flag_null", None),
    ):
        mutations[label] = {**accepted, flag: bad}
    return mutations


# (command, accepted pair, value whose flag is exercised)
_PAIRS: tuple[tuple[str, dict[str, Any], str], ...] = (
    ("object_anim", {"object_id": 3, "source": "DoorsDriver", "phase": 1.0, "phase_set": True}, "phase"),
    ("object_anim", {"object_id": 3, "source": "DoorsDriver", "phase": 0.0, "phase_set": True}, "phase"),
    (
        "object_anim",
        {"type": "CivilianSedan", "pos": _POS, "source": "DoorsDriver", "phase": 1.0, "phase_set": True},
        "phase",
    ),
    ("world_time_set", {**_DATE, "time_multiplier": 4.0, "time_multiplier_set": True}, "time_multiplier"),
    ("world_time_set", {**_DATE, "time_multiplier": -1.0, "time_multiplier_set": True}, "time_multiplier"),
    ("world_weather_set", {"rain": 0.2, "rain_set": True, **_TIMES}, "rain"),
    ("world_weather_set", {"overcast": 0.3, "overcast_set": True, **_TIMES}, "overcast"),
    ("world_weather_set", {"fog": 0.0, "fog_set": True, **_TIMES}, "fog"),
    (
        "world_weather_set",
        {"overcast": 0.3, "overcast_set": True, "fog": 0.6, "fog_set": True, **_TIMES},
        "fog",
    ),
    (
        "infected_drive",
        {**_INFECTED, "heading": 90.0, "heading_set": True, "speed": 1.0, "speed_set": True},
        "heading",
    ),
    (
        "infected_drive",
        {**_INFECTED, "heading": 90.0, "heading_set": True, "speed": 0.0, "speed_set": True},
        "speed",
    ),
)


class PresenceFlagEnforceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.messages_raw = MESSAGES_PATH.read_text(encoding="utf-8")
        cls.messages = _without_comments(cls.messages_raw)
        cls.bridge = _without_comments(BRIDGE_PATH.read_text(encoding="utf-8"))

    def test_mcpargs_declares_each_flag_as_a_bool_and_says_why(self) -> None:
        args = _method_body(self.messages, "class MCPArgs")
        for flag in FLAG_OF.values():
            with self.subTest(flag=flag):
                self.assertEqual(len(re.findall(rf"(?m)^\s*bool\s+{flag}\s*;", args)), 1)
        constructor = _method_body(args, "void MCPArgs()")
        for value, flag in FLAG_OF.items():
            with self.subTest(value=value):
                # An absent flag must read false: nothing may default it to true.
                self.assertIsNone(re.search(rf"\b{flag}\s*=", constructor))
                # The sentinel stays as a second guard only (see the class comment).
                self.assertIn(f"{value} = MCP_ARG_FLOAT_UNSET;", constructor)
        start = self.messages_raw.index("class MCPArgs")
        above = self.messages_raw[self.messages_raw.rindex("};", 0, start) : start]
        self.assertIn(FB_ID, above)
        self.assertIn("_set", above)

    def test_no_consumer_compares_a_value_with_the_sentinel(self) -> None:
        self.assertNotIn("MCP_ARG_FLOAT_UNSET", self.bridge)
        # The second guard: a flag that ever arrived without its value would
        # carry float.MAX, and the server's finiteness check refuses it.
        finite = _method_body(self.bridge, "protected bool IsFiniteFloat(float value)")
        self.assertIn("value >= float.MAX", finite)

    def test_object_anim_writes_only_when_phase_set(self) -> None:
        body = _method_body(self.bridge, "protected bool DispatchObjectAnim(")
        self.assertIn("bool writePhase = command.args.phase_set;", body)
        self.assertIn("if (writePhase && !IsFiniteFloat(command.args.phase))", body)
        write = _if_block(body, "writePhase")
        self.assertIn("entity.SetAnimationPhaseNow(command.args.source, command.args.phase);", write)
        self.assertEqual(body.count("SetAnimationPhase"), 1, "a write outside the phase_set block")
        self.assertIn("result.phase = entity.GetAnimationPhase(command.args.source);", body)
        self.assertNotIn("GetAnimationPhase", write)

    def test_infected_drive_needs_both_flags_unless_release(self) -> None:
        body = _method_body(self.bridge, "protected bool DispatchInfectedDrive(")
        self.assertIn("bool headingUnset = !command.args.heading_set;", body)
        self.assertIn("bool speedUnset = !command.args.speed_set;", body)
        refuse = _if_block(body, "!release && (headingUnset || speedUnset)")
        self.assertIn('result.error = "bad_args";', refuse)
        finite = _if_block(
            body,
            "!release && (!IsFiniteFloat(command.args.heading) || !IsFiniteFloat(command.args.speed))",
        )
        self.assertIn('result.error = "bad_args";', finite)
        self.assertLess(body.index("headingUnset ||"), body.index("OverrideHeading(true"))

    def test_world_time_set_applies_and_checks_the_multiplier_only_when_flagged(self) -> None:
        dispatch = _method_body(self.bridge, "protected bool DispatchWorldTimeSet(")
        apply = _if_block(dispatch, "command.args.time_multiplier_set")
        self.assertIn("world.SetTimeMultiplier(command.args.time_multiplier);", apply)
        self.assertEqual(dispatch.count("SetTimeMultiplier"), 1)

        validate = _method_body(self.bridge, "protected bool ValidateWorldTimeArgs(")
        check = _if_block(validate, "args.time_multiplier_set")
        self.assertIn("!IsFiniteFloat(args.time_multiplier)", check)
        self.assertIn("args.time_multiplier > 64.0", check)
        self.assertIn('result.error = "bad_time_multiplier";', check)
        self.assertEqual(
            validate.count("args.time_multiplier"),
            check.count("args.time_multiplier") + 1,  # + the flag in the condition
            "time_multiplier read outside its flag block",
        )

    def test_world_weather_set_sets_and_checks_only_flagged_levels(self) -> None:
        dispatch = _method_body(self.bridge, "protected bool DispatchWorldWeatherSet(")
        validate = _method_body(self.bridge, "protected bool ValidateWorldWeatherArgs(")
        for level in ("overcast", "rain", "fog"):
            with self.subTest(level=level):
                applied = _if_block(dispatch, f"command.args.{level}_set")
                self.assertIn(
                    f"{level}.Set(command.args.{level}, changeTime, minDuration);", applied
                )
                self.assertEqual(dispatch.count(f".Set(command.args.{level},"), 1)
                checked = _if_block(validate, f"args.{level}_set")
                self.assertIn("hasPhenomenon = true;", checked)
                self.assertIn(f"ValidateWeatherValue(args.{level})", checked)
                self.assertIn(f'result.error = "bad_{level}";', checked)
        self.assertIn('result.error = "no_weather_fields";', validate)


class PresenceFlagProducerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _sent(self, tool: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            await self.app.call_tool(tool, {**arguments, "timeout_s": 1.0})
        call.assert_awaited_once()
        command, args = call.await_args.args[0], dict(call.await_args.args[1])
        self.assertEqual(
            loopback.validate_command_args(command, args),
            (True, None),
            f"{tool} sent {args!r}, which the ingress refuses",
        )
        return command, args

    async def test_object_anim_sends_phase_set_only_beside_phase(self) -> None:
        for arguments in (
            {"object_id": 7, "source": "DoorsDriver"},
            {"type": "CivilianSedan", "pos": _POS, "source": "DoorsDriver"},
        ):
            with self.subTest(read=arguments):
                _command, args = await self._sent("object_anim", arguments)
                self.assertNotIn("phase", args)
                self.assertNotIn("phase_set", args)
        for phase in (1.0, 0.0, 0):
            with self.subTest(write=phase):
                command, args = await self._sent(
                    "object_anim", {"object_id": 7, "source": "DoorsDriver", "phase": phase}
                )
                self.assertEqual(command, "object_anim")
                self.assertEqual(args.get("phase"), float(phase))
                self.assertIs(args.get("phase_set"), True)

    async def test_infected_drive_sends_both_flags_beside_the_values(self) -> None:
        _command, args = await self._sent(
            "infected_drive", {**_INFECTED, "heading": 270.0, "speed": 0.0}
        )
        self.assertEqual((args.get("heading"), args.get("speed")), (270.0, 0.0))
        self.assertIs(args.get("heading_set"), True)
        self.assertIs(args.get("speed_set"), True)
        _command, released = await self._sent("infected_drive", {**_INFECTED, "mode": "release"})
        for key in ("heading", "heading_set", "speed", "speed_set"):
            with self.subTest(release_key=key):
                self.assertNotIn(key, released)

    async def test_world_time_set_sends_the_flag_only_with_the_multiplier(self) -> None:
        _command, bare = await self._sent("world_time_set", dict(_DATE))
        self.assertNotIn("time_multiplier", bare)
        self.assertNotIn("time_multiplier_set", bare)
        for multiplier in (-1.0, 0.0, 4.0):
            with self.subTest(multiplier=multiplier):
                _command, args = await self._sent(
                    "world_time_set", {**_DATE, "time_multiplier": multiplier}
                )
                self.assertEqual(args.get("time_multiplier"), multiplier)
                self.assertIs(args.get("time_multiplier_set"), True)

    async def test_world_weather_set_sends_only_the_given_levels_with_their_flags(self) -> None:
        cases = (
            ({"rain": 0.2}, {"rain"}),
            ({"rain": 0.0}, {"rain"}),
            ({"overcast": 0.3, "fog": 0.6}, {"overcast", "fog"}),
            ({"overcast": 1.0, "rain": 0.0, "fog": 0.2}, {"overcast", "rain", "fog"}),
        )
        for arguments, levels in cases:
            with self.subTest(arguments=arguments):
                _command, args = await self._sent("world_weather_set", arguments)
                expected = {"time", "min_duration"} | levels | {f"{level}_set" for level in levels}
                self.assertEqual(set(args), expected)
                for level in levels:
                    self.assertEqual(args.get(level), float(arguments[level]))
                    self.assertIs(args.get(f"{level}_set"), True)


class PresenceFlagIngressTest(unittest.TestCase):
    def test_each_pair_is_accepted_and_each_one_key_departure_refused(self) -> None:
        for command, accepted, value in _PAIRS:
            with self.subTest(command=command, pair=accepted):
                self.assertEqual(loopback.validate_command_args(command, dict(accepted)), (True, None))
            for label, refused in _pair_mutations(accepted, value).items():
                with self.subTest(command=command, pair=accepted, case=label):
                    self.assertEqual(
                        loopback.validate_command_args(command, refused), (False, "bad_args")
                    )

    def test_a_flag_names_only_its_own_value(self) -> None:
        # The flag of one weather level cannot stand in for another's.
        base = {"rain": 0.2, "rain_set": True, **_TIMES}
        self.assertEqual(loopback.validate_command_args("world_weather_set", base), (True, None))
        for extra in ("overcast_set", "fog_set"):
            with self.subTest(extra=extra):
                self.assertEqual(
                    loopback.validate_command_args("world_weather_set", {**base, extra: True}),
                    (False, "bad_args"),
                )
        swapped = {"rain": 0.2, "fog_set": True, **_TIMES}
        self.assertEqual(
            loopback.validate_command_args("world_weather_set", swapped), (False, "bad_args")
        )
        # A release carries no drive values and no flags.
        release = {**_INFECTED, "mode": "release"}
        self.assertEqual(loopback.validate_command_args("infected_drive", release), (True, None))
        for flag in ("heading_set", "speed_set"):
            with self.subTest(release_flag=flag):
                self.assertEqual(
                    loopback.validate_command_args("infected_drive", {**release, flag: True}),
                    (False, "bad_args"),
                )
        drive = {**_INFECTED, "heading": 0.0, "heading_set": True, "speed": 1.0, "speed_set": True}
        self.assertEqual(loopback.validate_command_args("infected_drive", drive), (True, None))
        self.assertEqual(
            loopback.validate_command_args(
                "infected_drive", {key: item for key, item in drive.items() if key not in ("speed", "speed_set")}
            ),
            (False, "bad_args"),
        )

    def test_reads_without_the_value_stay_accepted(self) -> None:
        self.assertEqual(
            loopback.validate_command_args("object_anim", {"object_id": 3, "source": "DoorsDriver"}),
            (True, None),
        )
        self.assertEqual(
            loopback.validate_command_args(
                "object_anim", {"type": "CivilianSedan", "pos": _POS, "source": "DoorsDriver"}
            ),
            (True, None),
        )
        self.assertEqual(loopback.validate_command_args("world_time_set", dict(_DATE)), (True, None))
        # Positive control: the write shape is known, so the reads above are
        # accepted as reads and not by a schema that ignores the flag.
        self.assertEqual(
            loopback.validate_command_args(
                "object_anim", {"object_id": 3, "source": "DoorsDriver", "phase": 0.5, "phase_set": True}
            ),
            (True, None),
        )

    def test_enqueue_refuses_an_unflagged_value_before_the_queue(self) -> None:
        from tests.fence_helpers import bind_both_peers

        state = loopback.ServerState("test-key")
        bind_both_peers(state)
        for command, accepted, value in _PAIRS[:1] + _PAIRS[3:4] + _PAIRS[5:6] + _PAIRS[9:10]:
            with self.subTest(command=command):
                status, body = state.enqueue_command(command, dict(accepted))
                self.assertEqual(status, 200, body)
                unflagged = _pair_mutations(accepted, value)["value_without_flag"]
                status, body = state.enqueue_command(command, unflagged)
                self.assertEqual((status, body), (400, {"error": "bad_args"}))


if __name__ == "__main__":
    unittest.main()
