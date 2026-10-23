"""action_use answers started for an action that ran inside its start (204e).

fb-20261009-122149-204e: action_use(ActionTogglePlaceObject) answered
setup_failed every time. The action is local and instant: ActionStart runs its
Start and then OnActionEnd at once (actionmanagerclient.c:719-721 in 1.30,
:677-679 in 1.29), so GetRunningAction() is null when PerformActionStart
returns, as after a start that SetupAction refused. DispatchActionUse watches
the action it starts and the ActionTarget it made for that start
(MCPActionUseWatch), and the addon's ActionBase.Start, on entry and before
super.Start, counts a Start of that action whose m_Target is that target.
Ends are not counted: an end can follow no Start, as another mod's cleanup
after a refused start, and the ActionBase is one instance another mod can run
for another target. An action left running or pending answers started, as
before 204e, whoever started it.

DispatchActionUse from its action_in_progress check to its end (without the
action_hold branch, as built for 1.29 and for 1.30), the addon's
PerformActionStart and Start, and IsWatched run as Python
(enforce_subset_helpers) against a manager that does what vanilla's
ActionStart does, with other mods' Starts loaded before or after the addon's.
Begin, End and NoteStart are read as text, with comments blanked and the
whitespace outside string literals removed, and Watch below does what they
say, so a layout change keeps these tests green and a change of what the code
does turns them red. The addon declares no other method of the start path.
"""
from __future__ import annotations

import re
import unittest
from types import SimpleNamespace

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import clean, method_body, translate

BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
HOLD_PATH = addon_root() / "scripts" / "4_World" / "MCP_ActionHold.c"
DISPATCH = "protected bool DispatchActionUse(MCPCommand command, MCPResult result)"
RUNNING_CHECK = re.compile(r"\bif\s*\(\s*amc\s*\.\s*GetRunningAction\s*\(\s*\)\s*!=\s*null\s*\)")
HOLD_BRANCH = re.compile(r'\bif\s*\(\s*command\s*\.\s*cmd\s*==\s*"action_hold"\s*\)')
# 1.29 has no CanBeStarted(), so the bridge asks it only where DAYZ_1_29 is
# not defined.
NOT_1_29 = re.compile(r"^[ \t]*#ifndef DAYZ_1_29[ \t]*\n(.*?)^[ \t]*#endif[ \t]*\n", re.M | re.S)
VERSIONS = ("1.29", "1.30")
# DispatchActionUse from its action_in_progress check runs as the body of
# this method.
TAIL = (
    "bool DispatchTail(ActionBase action, ActionTarget actionTarget, "
    "ActionManagerClient amc, PlayerBase player, MCPResult result)"
)
START = "override void Start(ActionData action_data)"
WRAPPER = (
    "override void PerformActionStart(ActionBase action, ActionTarget target, "
    "ItemBase item, Param extra_data = NULL)"
)
# The wrapper's body runs as the body of this method, which has no default.
WRAPPER_RUN = (
    "void PerformActionStart(ActionBase action, ActionTarget target, "
    "ItemBase item, Param extra_data)"
)
IS_WATCHED = "protected static bool IsWatched(ActionBase action, ActionData action_data)"
WATCH_BODIES = {
    "static void Begin(ActionBase action, ActionTarget target)": """
        s_Watched = action;
        s_WatchedTarget = target;
        s_Starts = 0;
    """,
    "static int End()": """
        int starts = s_Starts;
        s_Watched = null;
        s_WatchedTarget = null;
        s_Starts = 0;
        return starts;
    """,
    "static void NoteStart(ActionBase action, ActionData action_data)": """
        if (IsWatched(action, action_data))
        {
            s_Starts++;
        }
    """,
}
WATCH_DECLARATIONS = (
    "protected static ActionBase s_Watched;",
    "protected static ref ActionTarget s_WatchedTarget;",
    "protected static int s_Starts;",
    f"modded class ActionBase {{ {START} {{",
)
# How often each name occurs in each addon file, declarations included, so
# any other read or write of the watch, by &= or an out parameter too, is red.
NAMES = {
    "MCPActionUseWatch": {HOLD_PATH: 2, BRIDGE_PATH: 2},
    "s_Watched": {HOLD_PATH: 4},
    "s_WatchedTarget": {HOLD_PATH: 4},
    "s_Starts": {HOLD_PATH: 5},
    "NoteStart": {HOLD_PATH: 2},
    "IsWatched": {HOLD_PATH: 2},
}
# How often each addon file declares a method of the start path, which the
# flows run only as the addon's Start and PerformActionStart, so that another
# override there, such as a Start of the toggle's own class, is red (Codex
# review of #257, R3).
DECLARED = {
    r"\bPerformActionStart\s*\(\s*ActionBase\b": {HOLD_PATH: 1},
    r"\bActionStart\s*\(\s*ActionBase\b": {},
    r"\bSetupAction\s*\(\s*PlayerBase\b": {},
    r"\bStart\s*\(\s*ActionData\b": {HOLD_PATH: 1},
}

_STRING = re.compile(r'("(?:\\.|[^"\\])*")')


def _bare(text: str) -> str:
    """`text` without the whitespace outside its string literals."""
    parts = _STRING.split(text)
    return "".join(
        part if index % 2 else re.sub(r"\s+", "", part) for index, part in enumerate(parts)
    )


def _body(source: str, signature: str) -> str:
    """Body of `signature` in `source`, both without comments and whitespace."""
    return method_body(_bare(clean(source)), _bare(signature))


def _mutant(source: str, old: str, new: str) -> str:
    """`source` with comments blanked and its one `old` replaced by `new`,
    whatever the whitespace between the tokens of `old`."""
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\w+|\S', old)
    pattern = re.compile(r"\s*".join(map(re.escape, tokens)))
    text = clean(source)
    found = pattern.findall(text)
    if len(found) != 1:
        raise RuntimeError(f"{len(found)} matches of {old!r}")
    return pattern.sub(lambda _match: new, text)


def verify_watch(source: str) -> None:
    for signature, body in WATCH_BODIES.items():
        if _body(source, signature) != _bare(body):
            raise AssertionError(f"{signature} does not do what 204e needs")
    code = _bare(clean(source))
    for declaration in WATCH_DECLARATIONS:
        if code.count(_bare(declaration)) != 1:
            raise AssertionError(f"not declared once: {declaration.strip()}")


def verify_names(sources: dict) -> None:
    for name, expected in NAMES.items():
        for path, source in sources.items():
            count = len(re.findall(rf"\b{name}\b", clean(source)))
            if count != expected.get(path, 0):
                raise AssertionError(f"{name} occurs {count} times in {path.name}")
    for pattern, expected in DECLARED.items():
        for path, source in sources.items():
            count = len(re.findall(pattern, clean(source)))
            if count != expected.get(path, 0):
                raise AssertionError(f"{pattern} is declared {count} times in {path.name}")


class Target:
    """An ActionTarget; Enforce compares objects by identity, as Python does here."""


class Data:
    def __init__(self, action: "Action", target: Target) -> None:
        self.m_Action = action
        self.m_Target = target


class Watch:
    """MCPActionUseWatch: Begin, End and NoteStart do what verify_watch reads;
    IsWatched is the addon's, translated, reading the watch's statics."""

    def __init__(self, source: str) -> None:
        self.statics: dict = {"s_Watched": None, "s_WatchedTarget": None}
        self.is_watched = translate(source, IS_WATCHED, self.statics)
        self.starts = 0

    def Begin(self, action: object, target: object) -> None:
        self.statics["s_Watched"] = action
        self.statics["s_WatchedTarget"] = target
        self.starts = 0

    def End(self) -> int:
        starts = self.starts
        self.statics["s_Watched"] = None
        self.statics["s_WatchedTarget"] = None
        self.starts = 0
        return starts

    def NoteStart(self, action: object, data: Data) -> None:
        if self.is_watched(action, data):
            self.starts += 1


class Link:
    """One Start of the ActionBase.Start chain."""

    def __init__(self, start) -> None:
        self.Start = start


def retarget(*, before: bool):
    """Another mod's ActionBase.Start, which puts another ActionTarget in
    m_Target before or after its super.Start."""

    def wrap(inner: Link) -> Link:
        def start(data: Data) -> None:
            if before:
                data.m_Target = Target()
            inner.Start(data)
            if not before:
                data.m_Target = Target()

        return Link(start)

    return wrap


# The addon's Start among the `mods` of an Action.
ADDON = "addon"


class Action:
    """An ActionBase. Its Start reaches the ActionBase.Start chain unless it
    does not call super.Start, as vanilla's ActionDebug (actiondebug.c:98).
    The chain is `mods`, the last loaded first (ADDON is the addon's Start,
    translated), then vanilla's ActionBase.Start."""

    def __init__(
        self,
        watch: Watch,
        hold: str,
        *,
        instant: bool = True,
        calls_super: bool = True,
        mods: tuple = (ADDON,),
    ) -> None:
        self.instant = instant
        self.calls_super = calls_super
        self.chain = Link(lambda data: None)
        for mod in reversed(mods):
            if mod == ADDON:
                namespace = {"MCPActionUseWatch": watch, "this": self, "super": self.chain}
                self.chain = Link(translate(hold, START, namespace))
            else:
                self.chain = mod(self.chain)

    def IsInstant(self) -> bool:
        return self.instant

    def Can(self, player: object, target: Target, item: object) -> bool:
        return True

    def CanBeStarted(self) -> bool:
        return True

    def Start(self, data: Data) -> None:
        if self.calls_super:
            self.chain.Start(data)


class Manager:
    """ActionManagerClient in multiplayer. PerformActionStart is the addon's,
    translated; its super.PerformActionStart runs `perform`, by default
    ActionStart as vanilla's runs (1.30 actionmanagerclient.c:628-721, 1.29
    :600-679): no data when SetupAction refuses; SetupAction keeps the target
    in m_Target, unless the action puts another one there (retarget); a
    non-local action that uses an acknowledgment stays pending without Start;
    otherwise Start runs, and OnActionEnd right after it for an instant
    action. No hold is active, so MCPHoldControl.AfterStart does nothing."""

    def __init__(self, hold: str) -> None:
        self.current: Data | None = None
        self.perform = self.ActionStart
        self.performed = 0
        namespace = {
            "super": SimpleNamespace(PerformActionStart=self._vanilla),
            "this": self,
            "MCPHoldControl": SimpleNamespace(AfterStart=lambda manager: None),
        }
        self.PerformActionStart = translate(
            f"{WRAPPER_RUN}\n{{{method_body(hold, WRAPPER)}}}\n", WRAPPER_RUN, namespace
        )

    def _vanilla(self, action: Action, target: Target, item: object, extra: object) -> None:
        self.performed += 1
        self.perform(action, target)

    def ActionStart(
        self,
        action: Action,
        target: Target,
        *,
        setup: bool = True,
        local: bool = True,
        acknowledged: bool = False,
        retarget: bool = False,
    ) -> None:
        if not setup:
            return
        self.current = Data(action, Target() if retarget else target)
        if not local and acknowledged:
            return
        action.Start(self.current)
        if action.IsInstant():
            self.OnActionEnd()

    def OnActionEnd(self) -> None:
        self.current = None

    def ActionPossibilityCheck(self, command_id: int) -> bool:
        return True

    def GetRunningAction(self) -> object:
        return None if self.current is None else self.current.m_Action


class Player:
    def GetCurrentCommandID(self) -> int:
        return 0

    def GetItemInHands(self) -> object:
        return None


class Result:
    def __init__(self) -> None:
        self.started = False
        self.ok = True
        self.error = None


def dispatch(source: str, watch: Watch, version: str):
    """DispatchActionUse from its action_in_progress check to its end, without
    the action_hold branch, as Python, as `version` builds it."""
    body = method_body(source, DISPATCH)
    checks = list(RUNNING_CHECK.finditer(body))
    branches = list(HOLD_BRANCH.finditer(body))
    if len(checks) != 1 or len(branches) != 1 or checks[0].start() > branches[0].start():
        raise AssertionError(
            f"{len(checks)} action_in_progress checks and {len(branches)} action_hold "
            "branches in DispatchActionUse, or the branch first"
        )
    depth = 0
    for match in re.finditer(r'"(?:\\.|[^"\\])*"|[{}]', body[branches[0].end() :]):
        if match.group() == "{":
            depth += 1
        elif match.group() == "}":
            depth -= 1
            if depth == 0:
                rest = body[branches[0].end() + match.end() :]
                break
    else:
        raise AssertionError("the action_hold branch does not close")
    region = body[checks[0].start() : branches[0].start()] + rest
    region = NOT_1_29.sub("" if version == "1.29" else r"\1", region)
    if re.search(r"^[ \t]*#", region, re.M):
        raise AssertionError("a directive this test does not build")
    namespace = {
        "MCPActionUseWatch": watch,
        "NULL": None,
        "ScriptInputUserData": SimpleNamespace(CanStoreInputUserData=lambda: True),
    }
    return translate(f"{TAIL}\n{{{region}}}\n", TAIL, namespace, class_types=("ItemBase",))


def answer(tail, manager: Manager, action: Action, target: Target) -> str:
    result = Result()
    performed = manager.performed
    if tail(action, target, manager, Player(), result) is not True:
        raise AssertionError("the tail does not return true")
    if manager.performed != performed + 1:
        raise AssertionError(f"vanilla's PerformActionStart ran {manager.performed - performed} times")
    if result.started and result.ok:
        return "started"
    if not result.started and not result.ok and result.error is not None:
        return result.error.text
    raise AssertionError(f"started {result.started!r}, ok {result.ok!r}, error {result.error!r}")


def verify_flows(bridge: str, hold: str) -> None:
    watch = Watch(hold)
    ran = []
    action = Action(watch, hold, mods=(ADDON, lambda inner: Link(ran.append)))
    data = Data(action, Target())
    action.Start(data)
    if ran != [data]:
        raise AssertionError(f"the addon's Start ran super.Start {len(ran)} times or with other data")
    wrong = []
    for version in VERSIONS:
        tail = dispatch(bridge, watch, version)
        manager = Manager(hold)
        other = Action(watch, hold)
        continuous = Action(watch, hold, instant=False)

        def refused_then(after):
            def perform(action: Action, target: Target) -> None:
                manager.ActionStart(action, target, setup=False)
                after(action, target)

            return perform

        def pending_then_ended(action: Action, target: Target) -> None:
            manager.ActionStart(action, target, local=False, acknowledged=True)
            manager.OnActionEnd()

        flows = {
            "a local instant action": ({}, manager.ActionStart, "started"),
            "a non-local instant action without acknowledgment": (
                {}, lambda a, t: manager.ActionStart(a, t, local=False), "started"
            ),
            "a continuous action": ({"instant": False}, manager.ActionStart, "started"),
            "an action waiting for the server": (
                {}, lambda a, t: manager.ActionStart(a, t, local=False, acknowledged=True), "started"
            ),
            "a start SetupAction refused": (
                {}, lambda a, t: manager.ActionStart(a, t, setup=False), "setup_failed"
            ),
            "another mod's cleanup after a refused start": (
                {}, refused_then(lambda a, t: manager.OnActionEnd()), "setup_failed"
            ),
            "another mod starting this action for another target": (
                {}, refused_then(lambda a, t: manager.ActionStart(a, Target())), "setup_failed"
            ),
            "another mod starting another action for this target": (
                {}, refused_then(lambda a, t: manager.ActionStart(other, t)), "setup_failed"
            ),
            "a pending action another mod ends without its Start": (
                {}, pending_then_ended, "setup_failed"
            ),
            # Codex review of #257, R2: m_Target is read on entry.
            "a Start of a mod loaded later that retargets after super.Start": (
                {"mods": (retarget(before=False), ADDON)}, manager.ActionStart, "started"
            ),
            "a Start of a mod loaded earlier that retargets after super.Start": (
                {"mods": (ADDON, retarget(before=False))}, manager.ActionStart, "started"
            ),
            "a Start of a mod loaded earlier that retargets before super.Start": (
                {"mods": (ADDON, retarget(before=True))}, manager.ActionStart, "started"
            ),
            # Not seen, as the CHANGELOG says.
            "an action that puts another target in m_Target before Start": (
                {}, lambda a, t: manager.ActionStart(a, t, retarget=True), "setup_failed"
            ),
            "a Start of a mod loaded later that retargets before super.Start": (
                {"mods": (retarget(before=True), ADDON)}, manager.ActionStart, "setup_failed"
            ),
            "an instant action whose Start does not call super.Start": (
                {"calls_super": False}, manager.ActionStart, "setup_failed"
            ),
            # As before 204e, whoever started it (Codex review of #257, R1).
            "another mod leaving this action pending for another target": (
                {},
                refused_then(lambda a, t: manager.ActionStart(a, Target(), local=False, acknowledged=True)),
                "started",
            ),
            "another mod leaving another action running": (
                {}, refused_then(lambda a, t: manager.ActionStart(continuous, t)), "started"
            ),
        }
        for name, (kind, perform, expected) in flows.items():
            # The bridge refuses a call while an action is running.
            manager.current = None
            manager.perform = perform
            got = answer(tail, manager, Action(watch, hold, **kind), Target())
            if got != expected:
                wrong.append(f"{version}, {name}: {got}, expected {expected}")
    if wrong:
        raise AssertionError("; ".join(wrong))


def verify_is_watched(source: str) -> None:
    watch = Watch(source)
    action, other, target = object(), object(), Target()
    cases = (
        ("nothing watched", None, None, Data(action, target), False),
        ("the watched action and target", action, target, Data(action, target), True),
        ("another action", action, target, Data(other, target), False),
        ("another target", action, target, Data(action, Target()), False),
    )
    for name, watched, watched_target, data, expected in cases:
        watch.statics["s_Watched"] = watched
        watch.statics["s_WatchedTarget"] = watched_target
        got = watch.is_watched(data.m_Action, data)
        if got is not expected:
            raise AssertionError(f"{name}: {got!r}, expected {expected!r}")


class InstantActionStartTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = BRIDGE_PATH.read_text(encoding="utf-8")
        self.hold = HOLD_PATH.read_text(encoding="utf-8")
        self.sources = {
            path: path.read_text(encoding="utf-8")
            for path in sorted(addon_root().glob("scripts/**/*.c"))
        }

    def test_the_watch_does_what_204e_needs(self) -> None:
        verify_watch(self.hold)

    def test_a_start_counts_only_for_the_watched_action_and_target(self) -> None:
        verify_is_watched(self.hold)

    def test_the_names_occur_only_where_they_are_read(self) -> None:
        self.assertIn(HOLD_PATH, self.sources)
        self.assertIn(BRIDGE_PATH, self.sources)
        verify_names(self.sources)

    def test_only_a_start_of_this_command_answers_started(self) -> None:
        verify_flows(self.bridge, self.hold)

    def test_a_second_toggle_answers_started_again(self) -> None:
        for version in VERSIONS:
            watch = Watch(self.hold)
            tail = dispatch(self.bridge, watch, version)
            manager = Manager(self.hold)
            toggle = Action(watch, self.hold)
            for attempt in (1, 2):
                with self.subTest(version=version, attempt=attempt):
                    self.assertEqual(answer(tail, manager, toggle, Target()), "started")
                    self.assertIsNone(watch.statics["s_WatchedTarget"])

    def test_a_start_after_the_call_counts_for_no_command(self) -> None:
        for version in VERSIONS:
            with self.subTest(version=version):
                watch = Watch(self.hold)
                tail = dispatch(self.bridge, watch, version)
                manager = Manager(self.hold)
                action, first = Action(watch, self.hold), Target()
                manager.perform = lambda a, t: manager.ActionStart(
                    a, t, local=False, acknowledged=True
                )
                self.assertEqual(answer(tail, manager, action, first), "started")
                # The server accepts later, in Update: Start with the first target.
                action.Start(manager.current)
                manager.OnActionEnd()
                manager.perform = lambda a, t: manager.ActionStart(a, t, setup=False)
                self.assertEqual(answer(tail, manager, action, Target()), "setup_failed")

    def test_mutants_are_red(self) -> None:
        begin = "MCPActionUseWatch.Begin(action, actionTarget);"
        start = "amc.PerformActionStart(action, actionTarget, heldItem, NULL);"
        end = "int starts = MCPActionUseWatch.End();"
        ran = "if (starts > 0) { result.started = true; result.ok = true;"
        reject = 'if (action.IsInstant()) { result.ok = false; result.error = "setup_failed"; return true; }'
        hold_branch = '#endif if (command.cmd == "action_hold")'
        noted = "MCPActionUseWatch.NoteStart(this, action_data);"
        vanilla = "super.Start(action_data);"
        hold_start = "super.PerformActionStart(action, target, item, extra_data);"
        mutants = {
            "begin_after_the_start": (
                verify_flows, BRIDGE_PATH, f"{begin} {start}", f"{start} {begin}"
            ),
            "end_before_the_start": (
                verify_flows, BRIDGE_PATH, f"{start} {end}", f"{end} {start}"
            ),
            "watch_no_target": (
                verify_flows, BRIDGE_PATH, begin, "MCPActionUseWatch.Begin(action, null);"
            ),
            "no_start_reads_as_ran": (verify_flows, BRIDGE_PATH, "starts > 0", "starts >= 0"),
            "ran_answers_not_started": (
                verify_flows, BRIDGE_PATH, ran, ran.replace("started = true", "started = false")
            ),
            "ran_answers_not_ok": (
                verify_flows, BRIDGE_PATH, ran, ran.replace("ok = true", "ok = false")
            ),
            # Codex reviews of #257, A2: a rejection of instant actions before
            # the start, also before the action_hold branch.
            "instants_rejected_before_the_start": (
                verify_flows, BRIDGE_PATH, begin, f"{reject} {begin}"
            ),
            "instants_rejected_before_the_action_hold_branch": (
                verify_flows, BRIDGE_PATH, hold_branch, hold_branch.replace("#endif ", f"#endif\n{reject}\n")
            ),
            "start_not_counted": (verify_watch, HOLD_PATH, "s_Starts++;", ""),
            "every_start_counted": (
                verify_watch, HOLD_PATH, "if (IsWatched(action, action_data))", "if (true)"
            ),
            "begin_keeps_the_count": (
                verify_watch, HOLD_PATH,
                "s_WatchedTarget = target; s_Starts = 0;", "s_WatchedTarget = target;",
            ),
            "end_returns_zero": (verify_watch, HOLD_PATH, "return starts;", "return 0;"),
            "any_action_counts": (verify_is_watched, HOLD_PATH, "action == s_Watched &&", ""),
            "any_target_counts": (
                verify_is_watched, HOLD_PATH, "&& action_data.m_Target == s_WatchedTarget", ""
            ),
            "only_another_target_counts": (
                verify_is_watched, HOLD_PATH,
                "action_data.m_Target == s_WatchedTarget", "action_data.m_Target != s_WatchedTarget",
            ),
            # Codex review of #257, R2: m_Target read after super.Start.
            "count_after_super": (
                verify_flows, HOLD_PATH, f"{noted} {vanilla}", f"{vanilla} {noted}"
            ),
            "start_not_noted": (verify_flows, HOLD_PATH, noted, ""),
            "start_skips_super": (verify_flows, HOLD_PATH, vanilla, ""),
            "start_runs_super_twice": (verify_flows, HOLD_PATH, vanilla, f"{vanilla} {vanilla}"),
            # Codex review of #257, R3: the start call or another Start of the
            # toggle's chain stops it.
            "the_start_call_skips_instants": (
                verify_flows, HOLD_PATH, hold_start, f"if (action.IsInstant()) {{ return; }} {hold_start}"
            ),
            "the_start_call_runs_twice": (
                verify_flows, HOLD_PATH, hold_start, f"{hold_start} {hold_start}"
            ),
            "a_start_of_the_toggle_without_super": (
                verify_names, HOLD_PATH, "modded class ActionBase",
                f"modded class ActionTogglePlaceObject {{ {START} {{ }} }}; modded class ActionBase",
            ),
            "an_action_start_of_the_addon": (
                verify_names, HOLD_PATH, "bool MCP_HoldBusy()",
                "override protected void ActionStart(ActionBase action, ActionTarget target, "
                "ItemBase item, Param extra_data = NULL) { } bool MCP_HoldBusy()",
            ),
            "a_setup_action_of_the_addon": (
                verify_names, HOLD_PATH, "modded class ActionBase {",
                "modded class ActionBase { override bool SetupAction(PlayerBase player, "
                "ActionTarget target, ItemBase item, out ActionData action_data, "
                "Param extra_data = NULL) { return false; }",
            ),
            "a_second_start_call_of_the_addon": (
                verify_names, HOLD_PATH, "bool MCP_HoldBusy()",
                f"{WRAPPER.replace('override ', '')} {{ }} bool MCP_HoldBusy()",
            ),
            "watch_cleared_in_the_hold_start": (
                verify_names, HOLD_PATH, hold_start, f"{hold_start} MCPActionUseWatch.End();"
            ),
        }
        for name, (verify, path, old, new) in mutants.items():
            with self.subTest(mutant=name):
                mutated = _mutant(self.sources[path], old, new)
                with self.assertRaises(AssertionError):
                    if verify is verify_names:
                        verify_names({**self.sources, path: mutated})
                    elif verify is verify_flows and path == BRIDGE_PATH:
                        verify_flows(mutated, self.hold)
                    elif verify is verify_flows:
                        verify_flows(self.bridge, mutated)
                    else:
                        verify(mutated)

    def test_a_layout_change_stays_green(self) -> None:
        def spread(text: str) -> str:
            # A line break after each statement; comments and strings stay.
            return re.sub(
                r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|;',
                lambda match: " ;\n\t " if match.group() == ";" else match.group(),
                text,
            )

        bridge = self.bridge.replace(
            "MCPActionUseWatch.Begin(action, actionTarget);",
            "MCPActionUseWatch.Begin(action, actionTarget); // before the start",
        ).replace("if (starts > 0)", "if (starts > 0) // it ran")
        hold = self.hold.replace("static int End()", "static int End ()").replace(
            "super.Start(action_data);", "super.Start(action_data); // then vanilla"
        )
        self.assertNotEqual(bridge, self.bridge)
        self.assertNotEqual(hold, self.hold)
        verify_watch(spread(hold))
        verify_is_watched(spread(hold))
        verify_flows(spread(bridge), spread(hold))
        verify_names({**self.sources, BRIDGE_PATH: spread(bridge), HOLD_PATH: spread(hold)})


if __name__ == "__main__":
    unittest.main()
