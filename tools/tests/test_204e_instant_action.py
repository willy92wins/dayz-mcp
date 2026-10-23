"""action_use answers started for an action that ran inside its start (204e).

fb-20261009-122149-204e: action_use(ActionTogglePlaceObject) answered
setup_failed every time. The action is local and instant: ActionStart runs its
Start and then OnActionEnd at once (actionmanagerclient.c:719-721 in 1.30,
:677-679 in 1.29), so GetRunningAction() is null when PerformActionStart
returns, as after a start that SetupAction refused. DispatchActionUse watches
the action it starts, and the modded ActionManagerClient counts an OnActionEnd
only with that action current and not waiting for the server (UA_AM_PENDING):
vanilla's OnActionEnd does nothing without a current action
(actionmanagerclient.c:830 in 1.30, :775 in 1.29), so another mod can call it
to clean up after a refused start.

MCP_EndingWatched runs as Python against fakes (enforce_subset_helpers). The
other bodies are read as text, with comments blanked and the whitespace outside
string literals removed, and Manager and dispatch() below do what they say, so
a layout change keeps these tests green and a change of what the code does
turns them red.
"""
from __future__ import annotations

import re
import unittest

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import clean, method_body, translate

BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
HOLD_PATH = addon_root() / "scripts" / "4_World" / "MCP_ActionHold.c"
DISPATCH = "protected bool DispatchActionUse(MCPCommand command, MCPResult result)"
ENDING_WATCHED = re.compile(r"\bprotected\s+bool\s+MCP_EndingWatched\s*\(\s*\)")

# The end of DispatchActionUse, after the action_hold branch has returned.
DISPATCH_TAIL = """
amc.MCP_Watch(action);
amc.PerformActionStart(action, actionTarget, heldItem, NULL);
if (amc.GetRunningAction() == null)
{
    if (amc.MCP_WatchedEnds() > 0)
    {
        result.started = true;
        result.ok = true;
        return true;
    }

    result.started = false;
    result.ok = false;
    result.error = "setup_failed";
    return true;
}

result.started = true;
result.ok = true;
return true;
"""
HOLD_BODIES = {
    "override void OnActionEnd()": """
        if (MCP_EndingWatched())
        {
            m_MCP_WatchedEnds++;
        }

        MCPHoldControl.BeforeActionEnd(this);
        super.OnActionEnd();
        MCPHoldControl.AfterActionEnd(this);
    """,
    "void MCP_Watch(ActionBase action)": """
        m_MCP_Watched = action;
        m_MCP_WatchedEnds = 0;
    """,
    "int MCP_WatchedEnds()": "return m_MCP_WatchedEnds;",
}
HOLD_FIELDS = ("protected ActionBase m_MCP_Watched;", "protected int m_MCP_WatchedEnds;")
# How often each name occurs in each addon file, declarations included, so
# any other read or write of the count, by &= or an out parameter too, is red.
NAMES = {
    "m_MCP_Watched": {HOLD_PATH: 3},
    "m_MCP_WatchedEnds": {HOLD_PATH: 4},
    "MCP_Watch": {HOLD_PATH: 1, BRIDGE_PATH: 1},
    "MCP_WatchedEnds": {HOLD_PATH: 1, BRIDGE_PATH: 1},
    "MCP_EndingWatched": {HOLD_PATH: 2},
}
# m_State values: only UA_AM_PENDING is compared with.
PENDING = 7
STARTED = 1

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


def verify_dispatch(source: str) -> None:
    if not _body(source, DISPATCH).endswith(_bare(DISPATCH_TAIL)):
        raise AssertionError("DispatchActionUse does not end with the 204e start check")


def verify_counter(source: str) -> None:
    for signature, body in HOLD_BODIES.items():
        if _body(source, signature) != _bare(body):
            raise AssertionError(f"{signature} does not do what 204e needs")
    code = _bare(clean(source))
    for field in HOLD_FIELDS:
        if code.count(_bare(field)) != 1:
            raise AssertionError(f"not declared once: {field}")


class Data:
    def __init__(self, action: object, state: int) -> None:
        self.m_Action = action
        self.m_State = state


def ending_watched(source: str) -> tuple:
    """(MCP_EndingWatched as Python, the namespace its members are read from)."""
    signatures = ENDING_WATCHED.findall(clean(source))
    if len(signatures) != 1:
        raise AssertionError("MCP_EndingWatched not declared exactly once")
    namespace: dict = {"UA_AM_PENDING": PENDING}
    return translate(source, signatures[0], namespace), namespace


def verify_ending_watched(source: str) -> None:
    function, namespace = ending_watched(source)
    watched, other = object(), object()
    cases = (
        ("no current action", None, watched, False),
        ("another action", Data(other, STARTED), watched, False),
        ("the watched action waiting for the server", Data(watched, PENDING), watched, False),
        ("the watched action after its Start", Data(watched, STARTED), watched, True),
        ("nothing watched", Data(watched, STARTED), None, False),
    )
    for name, current, watching, expected in cases:
        namespace["m_CurrentActionData"] = current
        namespace["m_MCP_Watched"] = watching
        try:
            got = function()
        except Exception as error:
            raise AssertionError(f"{name}: {error!r}") from error
        if got is not expected:
            raise AssertionError(f"{name}: {got!r}, expected {expected!r}")


def verify_names(sources: dict) -> None:
    for name, expected in NAMES.items():
        for path, source in sources.items():
            count = len(re.findall(rf"\b{name}\b", clean(source)))
            if count != expected.get(path, 0):
                raise AssertionError(f"{name} occurs {count} times in {path.name}")


class Manager:
    """ActionManagerClient in multiplayer, with the addon's override.

    ActionStart does what vanilla does (1.30 actionmanagerclient.c:628-721,
    1.29 :600-679): no data when SetupAction refuses, a non-local action that
    uses an acknowledgment stays pending without Start, and an instant action
    ends right after its Start. MCP_Watch, MCP_WatchedEnds and OnActionEnd do
    what the bodies verify_counter reads do; MCP_EndingWatched is that body.
    """

    def __init__(self, source: str) -> None:
        self._ending_watched, self._namespace = ending_watched(source)
        self.current: Data | None = None
        self.watched: object = None
        self.ends = 0

    def MCP_Watch(self, action: object) -> None:
        self.watched = action
        self.ends = 0

    def MCP_WatchedEnds(self) -> int:
        return self.ends

    def OnActionEnd(self) -> None:
        self._namespace["m_CurrentActionData"] = self.current
        self._namespace["m_MCP_Watched"] = self.watched
        if self._ending_watched():
            self.ends += 1
        self.current = None

    def ActionStart(
        self,
        action: object,
        *,
        setup: bool = True,
        local: bool = True,
        acknowledged: bool = False,
        instant: bool = True,
    ) -> None:
        if not setup:
            return
        self.current = Data(action, 0)
        if not local and acknowledged:
            self.current.m_State = PENDING
            return
        self.current.m_State = STARTED
        if instant:
            self.OnActionEnd()

    def GetRunningAction(self) -> object:
        return None if self.current is None else self.current.m_Action


def dispatch(manager: Manager, action: object, perform) -> str:
    """DISPATCH_TAIL, with perform() as the PerformActionStart call."""
    manager.MCP_Watch(action)
    perform()
    if manager.GetRunningAction() is None:
        if manager.MCP_WatchedEnds() > 0:
            return "started"
        return "setup_failed"
    return "started"


class InstantActionStartTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = BRIDGE_PATH.read_text(encoding="utf-8")
        self.hold = HOLD_PATH.read_text(encoding="utf-8")
        self.sources = {
            path: path.read_text(encoding="utf-8")
            for path in sorted(addon_root().glob("scripts/**/*.c"))
        }

    def test_dispatch_watches_the_action_it_starts(self) -> None:
        verify_dispatch(self.bridge)

    def test_on_action_end_counts_the_ends_of_the_watched_action(self) -> None:
        verify_counter(self.hold)

    def test_an_end_counts_only_after_the_start_of_the_watched_action(self) -> None:
        verify_ending_watched(self.hold)

    def test_the_names_occur_only_where_they_are_read(self) -> None:
        self.assertIn(HOLD_PATH, self.sources)
        self.assertIn(BRIDGE_PATH, self.sources)
        verify_names(self.sources)

    def test_only_a_start_that_ran_answers_started(self) -> None:
        manager = Manager(self.hold)
        toggle, other = object(), object()

        def refused_then(after) -> None:
            manager.ActionStart(toggle, setup=False)
            after()

        def pending_then_ended() -> None:
            manager.ActionStart(toggle, local=False, acknowledged=True)
            manager.OnActionEnd()

        flows = {
            "a local instant action": (lambda: manager.ActionStart(toggle), "started"),
            "a non-local instant action without acknowledgment": (
                lambda: manager.ActionStart(toggle, local=False), "started"
            ),
            "a continuous action": (lambda: manager.ActionStart(toggle, instant=False), "started"),
            "an action waiting for the server": (
                lambda: manager.ActionStart(toggle, local=False, acknowledged=True), "started"
            ),
            "a start SetupAction refused": (
                lambda: manager.ActionStart(toggle, setup=False), "setup_failed"
            ),
            "another mod's cleanup after a refused start": (
                lambda: refused_then(manager.OnActionEnd), "setup_failed"
            ),
            "another mod's instant action after a refused start": (
                lambda: refused_then(lambda: manager.ActionStart(other)), "setup_failed"
            ),
            "another mod ending an action waiting for the server": (
                pending_then_ended, "setup_failed"
            ),
        }
        for name, (perform, expected) in flows.items():
            with self.subTest(flow=name):
                # The bridge refuses a call while an action is running.
                manager.current = None
                self.assertEqual(dispatch(manager, toggle, perform), expected)

    def test_a_second_toggle_answers_started_again(self) -> None:
        manager = Manager(self.hold)
        toggle = object()
        for attempt in (1, 2):
            with self.subTest(attempt=attempt):
                answer = dispatch(manager, toggle, lambda: manager.ActionStart(toggle))
                self.assertEqual(answer, "started")
                self.assertEqual(manager.ends, 1)

    def test_mutants_are_red(self) -> None:
        start = "amc.PerformActionStart(action, actionTarget, heldItem, NULL);"
        ran = "if (amc.MCP_WatchedEnds() > 0) { result.started = true; result.ok = true;"
        hold_start = "super.PerformActionStart(action, target, item, extra_data);"
        counted = "if (MCP_EndingWatched()) { m_MCP_WatchedEnds++; }"
        mutants = {
            "watch_after_the_start": (
                verify_dispatch, BRIDGE_PATH,
                f"amc.MCP_Watch(action); {start}", f"{start} amc.MCP_Watch(action);",
            ),
            "watch_nothing": (
                verify_dispatch, BRIDGE_PATH, "amc.MCP_Watch(action);", "amc.MCP_Watch(null);"
            ),
            "no_end_reads_as_ran": (
                verify_dispatch, BRIDGE_PATH,
                "amc.MCP_WatchedEnds() > 0", "amc.MCP_WatchedEnds() >= 0",
            ),
            "ran_answers_not_started": (
                verify_dispatch, BRIDGE_PATH, ran, ran.replace("started = true", "started = false")
            ),
            "ran_answers_not_ok": (
                verify_dispatch, BRIDGE_PATH, ran, ran.replace("ok = true", "ok = false")
            ),
            "end_not_counted": (
                verify_counter, HOLD_PATH, "m_MCP_WatchedEnds++;", ""
            ),
            "every_end_counted": (
                verify_counter, HOLD_PATH, "if (MCP_EndingWatched())", "if (true)"
            ),
            "count_after_super": (
                verify_counter, HOLD_PATH,
                f"{counted} MCPHoldControl.BeforeActionEnd(this); super.OnActionEnd();",
                f"MCPHoldControl.BeforeActionEnd(this); super.OnActionEnd(); {counted}",
            ),
            "watch_keeps_the_count": (
                verify_counter, HOLD_PATH, "m_MCP_WatchedEnds = 0;", ""
            ),
            "accessor_returns_zero": (
                verify_counter, HOLD_PATH, "return m_MCP_WatchedEnds;", "return 0;"
            ),
            "an_end_without_action_counts": (
                verify_ending_watched, HOLD_PATH,
                "if (!m_CurrentActionData) { return false; }", "",
            ),
            "the_end_of_another_action_counts": (
                verify_ending_watched, HOLD_PATH,
                "if (m_CurrentActionData.m_Action != m_MCP_Watched) { return false; }", "",
            ),
            "a_pending_action_counts": (
                verify_ending_watched, HOLD_PATH,
                "return m_CurrentActionData.m_State != UA_AM_PENDING;", "return true;",
            ),
            "only_another_action_counts": (
                verify_ending_watched, HOLD_PATH,
                "m_CurrentActionData.m_Action != m_MCP_Watched",
                "m_CurrentActionData.m_Action == m_MCP_Watched",
            ),
            "count_reset_by_and_before_the_start": (
                verify_names, HOLD_PATH, hold_start, f"m_MCP_WatchedEnds &= 0; {hold_start}"
            ),
            "watch_cleared_in_the_start": (
                verify_names, HOLD_PATH, hold_start, f"{hold_start} MCP_Watch(null);"
            ),
        }
        for name, (verify, path, old, new) in mutants.items():
            with self.subTest(mutant=name):
                mutated = _mutant(self.sources[path], old, new)
                with self.assertRaises(AssertionError):
                    if verify is verify_names:
                        verify_names({**self.sources, path: mutated})
                    else:
                        verify(mutated)

    def test_a_layout_change_stays_green(self) -> None:
        def spread(text: str) -> str:
            return text.replace(";", " ;\n\t ")

        bridge = self.bridge.replace(
            "amc.MCP_Watch(action);", "amc.MCP_Watch(action); // before the start"
        ).replace("if (amc.MCP_WatchedEnds() > 0)", "if (amc.MCP_WatchedEnds() > 0) // it ran")
        hold = self.hold.replace("int MCP_WatchedEnds()", "int MCP_WatchedEnds ()").replace(
            "protected bool MCP_EndingWatched()", "protected bool MCP_EndingWatched ( )"
        )
        self.assertNotEqual(bridge, self.bridge)
        self.assertNotEqual(hold, self.hold)
        verify_dispatch(spread(bridge))
        verify_counter(spread(hold))
        verify_ending_watched(spread(hold))
        verify_names({**self.sources, BRIDGE_PATH: spread(bridge), HOLD_PATH: spread(hold)})


if __name__ == "__main__":
    unittest.main()
