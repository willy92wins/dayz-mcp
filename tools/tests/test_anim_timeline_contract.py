"""anim_timeline (ficha 5535): the Enforce sampler, its dispatch and its messages.

Source-level contract, like tests/test_vehicle_trace_contract.py: nothing here
compiles Enforce, so each assertion pins the text the behaviour depends on.
"""
from __future__ import annotations

import re
import unittest

from dayz_mcp import anim_timeline
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import (
    _method_body,
    announced_caps,
    dispatch_census,
)


MOD_SCRIPTS = addon_root() / "scripts"
TIMELINE = MOD_SCRIPTS / "4_World" / "MCP_AnimTimeline.c"
MESSAGES = MOD_SCRIPTS / "5_Mission" / "MCPMessages.c"
CLIENT_BRIDGE = MOD_SCRIPTS / "5_Mission" / "MCPClientBridge.c"
MISSION_GAMEPLAY = MOD_SCRIPTS / "5_Mission" / "MissionGameplay.c"

TICK_CALL = "MCPAnimTimeline.Tick(timeslice);"
EDGE_TEST = (
    "if (s_FrameAction != s_PrevAction || s_FrameCallback != s_PrevCallback "
    "|| s_FrameCallbackState != s_PrevCallbackState)"
)
DECLARATION_RE = re.compile(
    r"^\s*(?:ref\s+)?(?:int|float|bool|string|vector|[A-Z]\w*(?:<[^>]*>)?)\s+([A-Za-z_]\w*)\s*[;=]",
    re.M,
)


def _source(path) -> str:  # type: ignore[no-untyped-def]
    return path.read_text(encoding="utf-8")


def _static_methods(source: str) -> dict[str, str]:
    """Every `static <type> Name(` body of MCPAnimTimeline, by name."""
    body = _method_body(source, "class MCPAnimTimeline\n")
    methods: dict[str, str] = {}
    for match in re.finditer(r"static\s+[\w<> ]+?\s+(\w+)\(([^)]*)\)\s*\n\s*\{", body):
        methods[match.group(1)] = _method_body(body[match.start():], match.group(0).split("\n")[0])
    return methods


class AnimTimelineAnnouncementTest(unittest.TestCase):
    def test_command_is_announced_and_dispatched(self) -> None:
        bridge = _source(CLIENT_BRIDGE)
        self.assertIn("anim_timeline", announced_caps(bridge))
        self.assertIn("anim_timeline", dispatch_census(bridge))
        branch = _method_body(bridge, 'else if (command.cmd == "anim_timeline")')
        self.assertEqual(branch.strip(), "postNow = DispatchAnimTimeline(command, result);")

    def test_dispatch_validates_before_it_touches_the_sampler(self) -> None:
        dispatch = _method_body(_source(CLIENT_BRIDGE), "protected bool DispatchAnimTimeline(")
        for needle in (
            'args.mode != "start" && args.mode != "status" && args.mode != "stop" '
            '&& args.mode != "read" && args.mode != "clear"',
            'result.error = "bad_mode";',
            "!IsValidTraceId(args.trace_id)",
            "args.cursor < 0",
            "args.limit > MCPAnimTimeline.LIMIT_MAX",
            "args.sample_hz < MCPAnimTimeline.SAMPLE_HZ_MIN",
            "args.max_samples > MCPAnimTimeline.MAX_SAMPLES_MAX",
            "!MCPAnimTimeline.SourcesOk(args.sources)",
            'result.error = "trace_not_found";',
            "MCPAnimTimeline.Start(args.trace_id, args.sample_hz, args.max_samples, args.sources)",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, dispatch)
        self.assertNotIn('"dump"', dispatch)
        self.assertLess(dispatch.index("SourcesOk("), dispatch.index("MCPAnimTimeline.Start("))
        self.assertLess(dispatch.index("MCPAnimTimeline.Start("), dispatch.index("MCPAnimTimeline.Matches("))
        clear = dispatch.index('MCPAnimTimeline.View("clear"')
        self.assertLess(clear, dispatch.index("MCPAnimTimeline.Clear(args.trace_id)"))

    def test_shutdown_aborts_the_timeline(self) -> None:
        shutdown = _method_body(_source(CLIENT_BRIDGE), "void Shutdown()")
        self.assertIn('MCPAnimTimeline.Abort("shutdown");', shutdown)


class AnimTimelineFramePathTest(unittest.TestCase):
    def test_sampler_runs_from_the_frame_tick_before_any_early_return(self) -> None:
        mission = _method_body(_source(MISSION_GAMEPLAY), "override void OnUpdate(float timeslice)")
        self.assertIn("bridge.OnTick(timeslice);", mission)
        on_tick = _method_body(_source(CLIENT_BRIDGE), "void OnTick(float timeslice)")
        self.assertEqual(on_tick.count(TICK_CALL), 1)
        tick_at = on_tick.index(TICK_CALL)
        self.assertLess(tick_at, on_tick.index("m_JobRunner.Tick(timeslice, this);"))
        self.assertLess(tick_at, on_tick.index("if (!m_Configured)"))
        self.assertLess(tick_at, on_tick.index("return;"))

    def test_sampler_is_never_driven_from_a_job(self) -> None:
        # One caller in the whole addon: the frame tick above. A job would
        # sample at job cadence and miss the frame an edge happens on.
        callers = []
        for path in MOD_SCRIPTS.rglob("*.c"):
            text = path.read_text(encoding="utf-8", errors="replace")
            callers.extend(path.name for _ in re.finditer(r"MCPAnimTimeline\.Tick\(", text))
        self.assertEqual(callers, ["MCPClientBridge.c"])

    def test_idle_tick_returns_first_and_the_hot_path_does_not_allocate(self) -> None:
        methods = _static_methods(_source(TIMELINE))
        tick = methods["Tick"]
        self.assertTrue(tick.split("if (!s_Active)")[0].strip().endswith("bool due;"))
        for name in ("Tick", "ReadFrame", "CaptureNow"):
            with self.subTest(method=name):
                self.assertNotRegex(methods[name], r"\bnew\b")
        self.assertRegex(methods["Start"], r"s_Samples\.Resize\(maxSamples\);")
        self.assertIn("sample.phases.Resize(sourceCount);", methods["Start"])


class AnimTimelineSamplingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(TIMELINE)
        self.methods = _static_methods(self.source)

    def test_edge_sample_on_action_or_callback_state_change(self) -> None:
        tick = self.methods["Tick"]
        self.assertIn("ReadFrame();", tick)
        self.assertIn(EDGE_TEST, tick)
        self.assertLess(tick.index("ReadFrame();"), tick.index(EDGE_TEST))
        self.assertIn("if (!due && !edge)", tick)
        self.assertIn("CaptureNow(nowS, edge);", tick)
        # The periodic accumulator is only consumed by a due sample.
        due_body = _method_body(tick, "if (due)\n")
        self.assertIn("s_AccumS = s_AccumS - intervalS;", due_body)
        capture = self.methods["CaptureNow"]
        self.assertIn("sample.edge = edge;", capture)
        for previous in (
            "s_PrevAction = s_FrameAction;",
            "s_PrevCallback = s_FrameCallback;",
            "s_PrevCallbackState = s_FrameCallbackState;",
        ):
            with self.subTest(previous=previous):
                self.assertIn(previous, capture)

    def test_capacity_stops_with_overflow_instead_of_overwriting(self) -> None:
        capture = self.methods["CaptureNow"]
        full = "if (s_Count >= s_Capacity)"
        self.assertIn(full, capture)
        full_body = _method_body(capture, full)
        self.assertIn("s_Overflow = true;", full_body)
        self.assertIn('Fail("overflow");', full_body)
        self.assertIn("return;", full_body)
        self.assertLess(capture.index(full), capture.index("s_Samples.Get(s_Count)"))
        fail = self.methods["Fail"]
        self.assertIn("s_Active = false;", fail)
        self.assertIn("s_StopReason = reason;", fail)

    def test_samples_read_the_vanilla_getters(self) -> None:
        read_frame = self.methods["ReadFrame"]
        for getter in (
            "PlayerBase.Cast(GetGame().GetPlayer())",
            "player.GetActionManager()",
            "actionManager.GetRunningAction()",
            "runningAction.Type().ToString()",
            "actionManager.GetActionState(runningAction)",
            "player.GetCommand_Action()",
            "player.GetCommandModifier_Action()",
            "chosenCallback.GetState()",
            "HumanCommandActionCallback.GetStateString(s_FrameCallbackState)",
            "player.GetItemInHands()",
            "heldItem.GetType()",
            "heldItem.GetAnimationPhase(s_Sources.Get(index))",
        ):
            with self.subTest(getter=getter):
                self.assertIn(getter, read_frame)
        # The command callback wins when both exist, and the sample says so.
        command_branch = _method_body(read_frame, "if (commandCallback)\n")
        self.assertIn("chosenCallback = commandCallback;", command_branch)
        self.assertIn('chosenName = "command";', command_branch)
        self.assertEqual(
            _method_body(command_branch, "if (modifierCallback)").strip(),
            "s_FrameCallbackBoth = true;",
        )
        modifier_branch = _method_body(read_frame, "else if (modifierCallback)\n")
        self.assertIn("chosenCallback = modifierCallback;", modifier_branch)
        self.assertIn('chosenName = "modifier";', modifier_branch)
        self.assertLess(
            read_frame.index("if (commandCallback)\n"),
            read_frame.index("else if (modifierCallback)\n"),
        )
        # No player and no item are reported, not skipped.
        self.assertLess(read_frame.index("if (!player)"), read_frame.index("s_FramePlayerPresent = true;"))
        self.assertLess(read_frame.index("if (!heldItem)"), read_frame.index("s_FrameHandsPresent = true;"))
        self.assertIn("GetGame().GetTickTime()", self.methods["Tick"])
        self.assertIn("s_StartTickS = GetGame().GetTickTime();", self.methods["Start"])
        self.assertIn("sample.t_s = nowS - s_StartTickS;", self.methods["CaptureNow"])

    def test_sample_and_read_carry_the_fields_the_tool_documents(self) -> None:
        sample = _method_body(self.source, "class MCPAnimTimelineSample")
        for field in (
            "float t_s;", "bool edge;", "bool player_present;", "string action;",
            "int action_state;", "string callback;", "bool callback_both;", "int state;",
            "string state_name;", "string hands;", "bool hands_present;", "ref array<float> phases;",
        ):
            with self.subTest(field=field):
                self.assertIn(field, sample)
        read = _method_body(self.source, "class MCPAnimTimelineRead")
        for field in (
            "bool active;", "bool complete;", "bool overflow;", "string stop_reason;",
            "int sample_hz;", "int capacity;", "int count;", "float elapsed_s;",
            "int next_cursor;", "bool eof;", "ref array<string> sources;",
        ):
            with self.subTest(field=field):
                self.assertIn(field, read)

    def test_clear_discards_an_active_timeline_and_errors_match_vehicle_trace(self) -> None:
        self.assertNotIn("trace_active", self.methods["Clear"])
        self.assertIn("ClearState();", self.methods["Clear"])
        self.assertIn('s_LastError = "trace_exists";', self.methods["Start"])
        for name in ("Stop", "Clear", "View"):
            with self.subTest(method=name):
                self.assertIn('s_LastError = "trace_not_found";', self.methods[name])

    def test_enforce_limits_equal_the_python_contract(self) -> None:
        expected = {
            "SOURCE_NAME_MAX": anim_timeline.SOURCE_NAME_MAX_CHARS,
            "SOURCE_COUNT_MAX": anim_timeline.SOURCE_COUNT_MAX,
            "SAMPLE_HZ_MIN": anim_timeline.SAMPLE_HZ_MIN,
            "SAMPLE_HZ_MAX": anim_timeline.SAMPLE_HZ_MAX,
            "MAX_SAMPLES_MIN": anim_timeline.MAX_SAMPLES_MIN,
            "MAX_SAMPLES_MAX": anim_timeline.MAX_SAMPLES_MAX,
            "LIMIT_MAX": anim_timeline.LIMIT_MAX,
        }
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertIn(f"static const int {name} = {value};", self.source)
        self.assertIn(f'static const string SCHEMA = "{anim_timeline.TIMELINE_SCHEMA}";', self.source)
        source_name = _method_body(self.source, "protected static bool IsSourceName(string value)")
        self.assertIn("if (code < 32 || code > 126)", source_name)


class AnimTimelineMessagesTest(unittest.TestCase):
    def test_args_carry_sources_and_the_result_carries_the_timeline(self) -> None:
        messages = _source(MESSAGES)
        args = _method_body(messages, "class MCPArgs")
        self.assertIn("ref array<string> sources;", args)
        self.assertIn("sources = new array<string>();", _method_body(args, "void MCPArgs()"))
        result = _method_body(messages, "class MCPResult")
        self.assertIn("ref MCPAnimTimelineRead timeline;", result)


class AnimTimelineCompileHazardTest(unittest.TestCase):
    """The hazards the offline linter misses (DayZ 1.29, see the brief)."""

    def test_no_duplicate_declaration_in_any_function(self) -> None:
        for path in (TIMELINE,):
            methods = _static_methods(_source(path))
            self.assertGreaterEqual(len(methods), 10)
            for name, body in methods.items():
                with self.subTest(method=name):
                    declared = DECLARATION_RE.findall(body)
                    self.assertEqual(len(declared), len(set(declared)), declared)
        dispatch = _method_body(_source(CLIENT_BRIDGE), "protected bool DispatchAnimTimeline(")
        declared = DECLARATION_RE.findall(dispatch)
        self.assertEqual(sorted(declared), ["args", "clearView"])

    def test_no_reserved_or_type_named_identifier_and_no_object_equality(self) -> None:
        source = _source(TIMELINE)
        self.assertNotRegex(source, r"\blocal\b")
        for type_name in ("Action", "Human", "State", "Callback", "PlayerBase", "ItemBase"):
            with self.subTest(type_name=type_name):
                self.assertNotRegex(source, rf"(?m)^\s*(?:ref\s+)?[\w<>]+\s+{type_name}\s*[;=]")
        for local_object in (
            "player", "actionManager", "runningAction", "commandCallback",
            "modifierCallback", "chosenCallback", "heldItem",
        ):
            with self.subTest(local_object=local_object):
                self.assertNotRegex(source, rf"\b{local_object}\s*[!=]=|[!=]=\s*{local_object}\b")

    def test_timeline_strings_stay_short(self) -> None:
        for literal in re.findall(r'"([^"\\]*)"', _source(TIMELINE)):
            with self.subTest(literal=literal):
                self.assertLessEqual(len(literal.encode("utf-8")), 200)


if __name__ == "__main__":
    unittest.main()
