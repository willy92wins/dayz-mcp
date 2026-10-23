"""action_use answers started for an action that ran inside its start (204e).

fb-20261009-122149-204e: action_use(ActionTogglePlaceObject) answered
setup_failed every time. The action is local and instant: ActionStart runs its
Start and then OnActionEnd at once (actionmanagerclient.c:719-721 in 1.30,
:677-679 in 1.29), so GetRunningAction() is null when PerformActionStart
returns, as after a start that SetupAction refused. DispatchActionUse watches
the action it starts and the ActionTarget it made for that start
(MCPActionUseWatch), and the addon's ActionBase.Start, after super.Start,
counts a Start of that action whose m_Target is that target. Ends are not
counted: an end can follow no Start, as another mod's cleanup after a refused
start, and the ActionBase is one instance another mod can run for another
target.

The end of DispatchActionUse, after the action_hold branch, and IsWatched run
as Python (enforce_subset_helpers) against a manager that does what vanilla's
ActionStart does. Begin, End, NoteStart and the Start override are read as
text, with comments blanked and the whitespace outside string literals
removed, and Watch below does what they say, so a layout change keeps these
tests green and a change of what the code does turns them red.
"""
from __future__ import annotations

import re
import unittest

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import clean, method_body, translate

BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
HOLD_PATH = addon_root() / "scripts" / "4_World" / "MCP_ActionHold.c"
DISPATCH = "protected bool DispatchActionUse(MCPCommand command, MCPResult result)"
HOLD_BRANCH = re.compile(r'\bif\s*\(\s*command\s*\.\s*cmd\s*==\s*"action_hold"\s*\)')
# The end of DispatchActionUse runs as the body of this method.
TAIL = (
    "bool DispatchTail(ActionBase action, ActionTarget actionTarget, "
    "ItemBase heldItem, ActionManagerClient amc, MCPResult result)"
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
    """
    modded class ActionBase
    {
        override void Start(ActionData action_data)
        {
            super.Start(action_data);
            MCPActionUseWatch.NoteStart(this, action_data);
        }
    };
    """,
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


class Action:
    """An ActionBase. Its Start reaches the addon's override unless it does
    not call super.Start, as vanilla's ActionDebug (actiondebug.c:98)."""

    def __init__(self, watch: Watch, *, instant: bool = True, calls_super: bool = True) -> None:
        self.watch = watch
        self.instant = instant
        self.calls_super = calls_super

    def IsInstant(self) -> bool:
        return self.instant

    def Start(self, data: Data) -> None:
        if self.calls_super:
            self.watch.NoteStart(self, data)


class Manager:
    """ActionManagerClient in multiplayer, as vanilla's ActionStart runs
    (1.30 actionmanagerclient.c:628-721, 1.29 :600-679): no data when
    SetupAction refuses; SetupAction keeps the target in m_Target, unless the
    action puts another one there (retarget); a non-local action that uses an
    acknowledgment stays pending without Start; otherwise Start runs, and
    OnActionEnd right after it for an instant action. perform is what happens
    inside PerformActionStart."""

    def __init__(self) -> None:
        self.current: Data | None = None
        self.perform = self.ActionStart

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

    def PerformActionStart(self, action: Action, target: Target, item: object, extra: object) -> None:
        self.perform(action, target)

    def GetRunningAction(self) -> object:
        return None if self.current is None else self.current.m_Action


class Result:
    def __init__(self) -> None:
        self.started = False
        self.ok = True
        self.error = None


def dispatch_tail(source: str, watch: Watch):
    """The end of DispatchActionUse, after the action_hold branch, as Python."""
    body = method_body(source, DISPATCH)
    branches = list(HOLD_BRANCH.finditer(body))
    if len(branches) != 1:
        raise AssertionError(f"{len(branches)} action_hold branches in DispatchActionUse")
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
    namespace = {"MCPActionUseWatch": watch, "NULL": None}
    return translate(f"{TAIL}\n{{{rest}}}\n", TAIL, namespace)


def answer(tail, manager: Manager, action: Action, target: Target) -> str:
    result = Result()
    if tail(action, target, None, manager, result) is not True:
        raise AssertionError("the tail does not return true")
    if result.started and result.ok:
        return "started"
    if not result.started and not result.ok and result.error is not None:
        return result.error.text
    raise AssertionError(f"started {result.started!r}, ok {result.ok!r}, error {result.error!r}")


def verify_flows(bridge: str, hold: str) -> None:
    watch = Watch(hold)
    tail = dispatch_tail(bridge, watch)
    manager = Manager()
    other = Action(watch)

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
        # Not seen, as the CHANGELOG says.
        "an action that puts another target in m_Target before Start": (
            {}, lambda a, t: manager.ActionStart(a, t, retarget=True), "setup_failed"
        ),
        "an instant action whose Start does not call super.Start": (
            {"calls_super": False}, manager.ActionStart, "setup_failed"
        ),
    }
    wrong = []
    for name, (kind, perform, expected) in flows.items():
        # The bridge refuses a call while an action is running.
        manager.current = None
        manager.perform = perform
        got = answer(tail, manager, Action(watch, **kind), Target())
        if got != expected:
            wrong.append(f"{name}: {got}, expected {expected}")
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
        watch = Watch(self.hold)
        tail = dispatch_tail(self.bridge, watch)
        manager = Manager()
        toggle = Action(watch)
        for attempt in (1, 2):
            with self.subTest(attempt=attempt):
                self.assertEqual(answer(tail, manager, toggle, Target()), "started")
                self.assertIsNone(watch.statics["s_WatchedTarget"])

    def test_a_start_after_the_call_counts_for_no_command(self) -> None:
        watch = Watch(self.hold)
        tail = dispatch_tail(self.bridge, watch)
        manager = Manager()
        action, first = Action(watch), Target()
        manager.perform = lambda a, t: manager.ActionStart(a, t, local=False, acknowledged=True)
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
        noted = "MCPActionUseWatch.NoteStart(this, action_data);"
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
            # Codex review of #257, A2: a rejection of instant actions before
            # the start.
            "instants_rejected_before_the_start": (
                verify_flows, BRIDGE_PATH, begin,
                'if (action.IsInstant()) { result.ok = false; result.error = "setup_failed";'
                f" return true; }} {begin}",
            ),
            "start_not_counted": (verify_watch, HOLD_PATH, "s_Starts++;", ""),
            "every_start_counted": (
                verify_watch, HOLD_PATH, "if (IsWatched(action, action_data))", "if (true)"
            ),
            "count_before_super": (
                verify_watch, HOLD_PATH,
                f"super.Start(action_data); {noted}", f"{noted} super.Start(action_data);",
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
                    elif verify is verify_flows:
                        verify_flows(mutated, self.hold)
                    else:
                        verify(mutated)

    def test_a_layout_change_stays_green(self) -> None:
        def spread(text: str) -> str:
            return text.replace(";", " ;\n\t ")

        bridge = self.bridge.replace(
            "MCPActionUseWatch.Begin(action, actionTarget);",
            "MCPActionUseWatch.Begin(action, actionTarget); // before the start",
        ).replace("if (starts > 0)", "if (starts > 0) // it ran")
        hold = self.hold.replace("static int End()", "static int End ()").replace(
            "super.Start(action_data);", "super.Start(action_data); // vanilla first"
        )
        self.assertNotEqual(bridge, self.bridge)
        self.assertNotEqual(hold, self.hold)
        verify_watch(spread(hold))
        verify_is_watched(spread(hold))
        verify_flows(spread(bridge), spread(hold))
        verify_names({**self.sources, BRIDGE_PATH: spread(bridge), HOLD_PATH: spread(hold)})


if __name__ == "__main__":
    unittest.main()
