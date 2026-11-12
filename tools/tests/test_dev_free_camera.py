"""Inbox fb-20261112-142535-beb3: dev_free_camera_set turns DayZDiag's free camera on and off.

The client bridge calls vanilla PluginDeveloper.ToggleFreeCameraBackPos, the
method DayZDiag binds to KC_DIVIDE (1.30 pluginkeybinding.c:63, under
DIAG_DEVELOPER), only when FreeDebugCamera.GetInstance() is not already in the
requested state, after its refusals in order: bad_args without args,
client_not_in_game without a local player, dev_free_camera_unavailable without
PluginDeveloper (or outside DIAG_DEVELOPER), dev_free_camera_bridge_owned while
the bridge holds the free camera (camera_set free or a camera release
handoff), busy while an exclusive client job runs. It answers
dev_free_camera {active_before, active_after}, read right after the toggle or,
when that read does not show the requested state yet, on the next OnTick by a
one-tick read job that HasExclusiveJob does not count. The bridge never owns
this camera: the dispatcher and its job leave the camera path, m_ActiveCam,
m_ActiveCamOwned and the player simulation alone, so a playing
camera_path_play ends aborted through TickCameraPath's own check.

The Enforce checks read MCPClientBridge.c as text; each one has a negative
control on an in-memory copy of the body.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dayz_mcp import loopback, server
from dayz_mcp.result_prune import PRUNABLE_FIELDS, prune_unfilled_fields
from dayz_mcp.server import (
    DEFAULT_TOOL_TIMEOUT_S,
    LEASE_TOOL_LINE,
    ServerConfig,
    ToolError,
    _BRIDGE_COMMAND_TOOLS,
    build_app,
)
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, SessionCoordinator, command_requires_lease
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import announced_caps, dispatch_census
from tests.fence_helpers import (
    INST_CLIENT,
    PID_CLIENT,
    bind_both_peers,
    bound_queue,
    poll_census_query,
)


COMMAND = "dev_free_camera_set"
FIELD = "dev_free_camera"
IDENTITY = {
    "platform": "codex",
    "pid": 11,
    "ppid": 1,
    "started_at_utc": "2026-07-15T00:00:00Z",
    "session_id": "A",
    "task_label": "free-camera",
}
SCRIPTS = addon_root() / "scripts"
CLIENT_PATH = SCRIPTS / "5_Mission" / "MCPClientBridge.c"
SERVER_BRIDGE_PATH = SCRIPTS / "5_Mission" / "MCPBridge.c"
MESSAGES_PATH = SCRIPTS / "5_Mission" / "MCPMessages.c"
CENSUS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"

DISPATCH_CHAIN = "protected void Dispatch(MCPCommand command)"
DISPATCH = "protected bool DispatchDevFreeCameraSet(MCPCommand command, MCPResult result)"
PROCESS = "protected bool ProcessDevFreeCameraJob(MCPJob job)"
POST = "protected void PostDevFreeCameraJob(MCPJob job)"
EXCLUSIVE = "protected bool HasExclusiveJob()"
PROCESS_HOOK = "override bool MCP_ProcessJob(MCPJob job)"
SUCCESS_HOOK = "override void MCP_PostJobSuccess(MCPJob job)"
FAILURE_HOOK = "override void MCP_PostJobFailure(MCPJob job)"
TIMEOUT_HOOK = "override void MCP_PostJobTimeout(MCPJob job)"

# The checks in the brief's order (a to e), then the toggle and the reads.
DISPATCH_STEPS = (
    ("a", "if (!command.args)"),
    ("a", 'result.error = "bad_args";'),
    ("b", "if (!GetGame() || !GetGame().GetPlayer() || GetGame().IsDedicatedServer())"),
    ("b", 'result.error = "client_not_in_game";'),
    ("c", 'unavailable = "no_diag_developer";'),
    ("c", "#ifdef DIAG_DEVELOPER"),
    ("c", "if (!PluginDeveloper.GetInstance())"),
    ("c", 'unavailable = "plugin_developer_missing";'),
    ("c", "freeCam = FreeDebugCamera.GetInstance();"),
    ("c", 'result.error = "dev_free_camera_unavailable";'),
    ("d", "if (m_CameraHandoffPending || m_ActiveCam == freeCam)"),
    ("d", 'result.error = "dev_free_camera_bridge_owned";'),
    ("e", "if (HasExclusiveJob())"),
    ("e", 'result.error = "busy";'),
    ("read", "before = freeCam.IsActive();"),
    ("toggle", "if (before != requested)"),
    ("toggle", "PluginDeveloper.GetInstance().ToggleFreeCameraBackPos();"),
    ("read", "after = freeCam.IsActive();"),
    ("job", "if (after != requested)"),
    ("job", 'job.kind = "dev_free_camera_set";'),
    ("job", "m_JobRunner.AddJob(job);"),
    ("job", "return false;"),
    ("reply", "result.dev_free_camera = reply;"),
)
TOGGLE_CALL = "PluginDeveloper.GetInstance().ToggleFreeCameraBackPos();"
# The bridge does not own this camera (brief point 4): none of these may run.
FORBIDDEN_CALLS = (
    "StopCameraPath",
    "ReleaseCamera",
    "RestoreGameplay",
    "SuppressGameplay",
    "DeleteOwnedCamera",
    "BeginCameraHandoff",
    "FinishCameraHandoff",
    "ClearCameraDOF",
    "OverrideDOF",
    "DisableSimulation",
    "PlayerControlEnable",
    "PlayerControlDisable",
    "SetActive",
)
FORBIDDEN_ASSIGNMENTS = ("m_ActiveCam", "m_ActiveCamOwned", "m_PlayerSimulationDisabled")

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_DIAG_BLOCK = re.compile(r"^#ifdef DIAG_DEVELOPER\n(?:(?!^#(?:ifdef|ifndef|else|endif)\b).*\n)*?#endif\n", re.M)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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
    raise AssertionError(f"unterminated block at offset {start}")


def _method_body(source: str, signature: str) -> str:
    count = source.count(signature)
    if count != 1:
        raise AssertionError(f"{signature!r} occurs {count} times; the check expects exactly one method with that signature")
    return _block_after(source, source.index(signature))


def _if_body(source: str, condition: str) -> str:
    needle = f"if ({condition})"
    count = source.count(needle)
    if count != 1:
        raise AssertionError(f"{needle!r} occurs {count} times")
    return _block_after(source, source.index(needle))


def _replace_in_if_block(body: str, condition: str, old: str, new: str) -> str:
    needle = f"if ({condition})"
    if body.count(needle) != 1:
        raise AssertionError(f"{needle!r} occurs {body.count(needle)} times")
    start = body.index(needle)
    brace = body.index("{", start)
    inner = _block_after(body, start)
    if inner.count(old) != 1:
        raise AssertionError(f"{old!r} occurs {inner.count(old)} times in {needle!r}")
    return body[: brace + 1] + inner.replace(old, new, 1) + body[brace + 1 + len(inner) :]


def _class_body(source: str, name: str) -> str:
    match = re.search(rf"^class {re.escape(name)}\b", source, re.M)
    if match is None:
        raise AssertionError(f"class {name} is absent")
    return _block_after(source, match.end())


def _without_comments(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return text if text.startswith('"') else re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


def _without_comments_or_strings(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return '""' if text.startswith('"') else re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


# Amendment B1 pinned zones of DispatchDevFreeCameraSet, normalized with
# comments removed and every whitespace character outside string literals
# removed (string literals kept as they are). A legitimate change to either
# zone updates its pinned literal in the same commit.
_ZONE_ONE_PINNED = (
    'requested=command.args.active;'
    'requestedBit=DevFreeCameraBit(requested);'
    'if(!GetGame()||!GetGame().GetPlayer()||GetGame().IsDedicatedServer()){'
    'result.ok=false;'
    'result.error="client_not_in_game";'
    'Log("dev_free_camera_set requested="+requestedBit+" before=-1 after=-1 ok=0 error=client_not_in_game");'
    'returntrue;'
    '}'
    'unavailable="no_diag_developer";'
    '#ifdefDIAG_DEVELOPER'
    'unavailable="";'
    'if(!PluginDeveloper.GetInstance()){'
    'unavailable="plugin_developer_missing";'
    '}'
    '#endif'
    'freeCam=FreeDebugCamera.GetInstance();'
    'if(unavailable==""&&!freeCam){'
    'unavailable="free_debug_camera_missing";'
    '}'
    'if(unavailable!=""){'
    'result.ok=false;'
    'result.error="dev_free_camera_unavailable";'
    'Log("dev_free_camera_set requested="+requestedBit+" before=-1 after=-1 ok=0 error=dev_free_camera_unavailable reason="+unavailable);'
    'returntrue;'
)
_ZONE_TWO_PINNED = 'if(before!=requested){PluginDeveloper.GetInstance().ToggleFreeCameraBackPos();}'


def _normalize_pinned(text: str) -> str:
    code = _without_comments(text)
    kept: list[str] = []
    in_string = False
    escaped = False
    for char in code:
        if in_string:
            kept.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
            kept.append(char)
        elif not char.isspace():
            kept.append(char)
    return "".join(kept)


def _zone_one(dispatch: str) -> str:
    start_marker = "requested = command.args.active;"
    if dispatch.count(start_marker) != 1:
        raise AssertionError(
            f"zone 1 start marker {start_marker!r} occurs {dispatch.count(start_marker)} times; "
            "the pinned zone 1 cannot be found"
        )
    refusal = 'if (unavailable != "")'
    if dispatch.count(refusal) != 1:
        raise AssertionError(
            f"zone 1 end marker {refusal!r} occurs {dispatch.count(refusal)} times; "
            "the pinned zone 1 cannot be found"
        )
    start = dispatch.index(start_marker)
    refusal_at = dispatch.index(refusal)
    if refusal_at < start:
        raise AssertionError("zone 1 end marker comes before its start marker; the pinned zone 1 cannot be found")
    end_marker = "return true;"
    if dispatch.count(end_marker, refusal_at) < 1:
        raise AssertionError("zone 1 end marker 'return true;' after check c is missing; the pinned zone 1 cannot be found")
    end = dispatch.index(end_marker, refusal_at) + len(end_marker)
    return dispatch[start:end]


def _zone_two(dispatch: str) -> str:
    marker = "if (before != requested)"
    if dispatch.count(marker) != 1:
        raise AssertionError(
            f"zone 2 start marker {marker!r} occurs {dispatch.count(marker)} times; "
            "the pinned zone 2 cannot be found"
        )
    start = dispatch.index(marker)
    brace = dispatch.index("{", start)
    inner = _block_after(dispatch, start)
    return dispatch[start : brace + 1 + len(inner) + 1]


def assert_amended_zones(dispatch: str) -> None:
    zone_one = _normalize_pinned(_zone_one(dispatch))
    if zone_one != _ZONE_ONE_PINNED:
        raise AssertionError(
            f"zone 1 of DispatchDevFreeCameraSet does not match its pinned literal: {zone_one!r} != {_ZONE_ONE_PINNED!r}"
        )
    zone_two = _normalize_pinned(_zone_two(dispatch))
    if zone_two != _ZONE_TWO_PINNED:
        raise AssertionError(
            f"zone 2 of DispatchDevFreeCameraSet does not match its pinned literal: {zone_two!r} != {_ZONE_TWO_PINNED!r}"
        )


def _retail_view(source: str) -> str:
    """`source` as a build without DIAG_DEVELOPER compiles it: every DIAG_DEVELOPER block removed."""
    code = _without_comments(source)
    if re.search(r"^\s*#(?:if|elif|else)\b", code, re.M):
        raise AssertionError("an #if, #elif or #else was found; the retail view handles #ifdef/#endif blocks only")
    return _DIAG_BLOCK.sub("", code)


def _ownership_violations(body: str) -> list[str]:
    """Calls and assignments of FORBIDDEN_* in `body`, comments and string literals ignored."""
    code = _without_comments_or_strings(body)
    found = [name + "()" for name in FORBIDDEN_CALLS if re.search(rf"\b{name}\s*\(", code)]
    found += [name + " =" for name in FORBIDDEN_ASSIGNMENTS if re.search(rf"\b{name}\s*=(?!=)", code)]
    return found


def assert_dispatch_contract(dispatch_body: str) -> None:
    """The dispatcher's checks run in order (a to e) before the one toggle, which reads the state first."""
    code = _without_comments(dispatch_body)
    calls = re.findall(r"\bToggleFreeCameraBackPos\s*\(", _without_comments_or_strings(dispatch_body))
    if len(calls) != 1 or code.count(TOGGLE_CALL) != 1:
        raise AssertionError(
            f"expected exactly one {TOGGLE_CALL!r} statement, found {len(calls)} call(s) and "
            f"{code.count(TOGGLE_CALL)} statement(s)"
        )
    positions: list[int] = []
    cursor = 0
    for step, token in DISPATCH_STEPS:
        at = code.find(token, cursor)
        if at < 0:
            where = "anywhere" if token not in code else "after the previous step"
            raise AssertionError(f"DispatchDevFreeCameraSet step {step} {token!r} is missing {where}")
        positions.append(at)
        cursor = at + len(token)
    toggle_at = code.index(TOGGLE_CALL)
    for step, token in DISPATCH_STEPS:
        if step in ("c", "d", "e") and code.index(token) > toggle_at:
            raise AssertionError(f"check {step} ({token!r}) must come before the toggle")
    # The toggle is DIAG_DEVELOPER-only and runs only when the state differs.
    opened = code.rfind("#ifdef DIAG_DEVELOPER", 0, toggle_at)
    closed = code.rfind("#endif", 0, toggle_at)
    if opened < 0 or closed > opened or code.find("#endif", toggle_at) < 0:
        raise AssertionError("the toggle must sit inside an #ifdef DIAG_DEVELOPER ... #endif block")
    guard = code.rfind("if (before != requested)", 0, toggle_at)
    if guard < 0 or _block_after(code, guard).strip() != TOGGLE_CALL:
        raise AssertionError("the toggle must be the only statement of if (before != requested)")
    # Check c's missing-plugin branch must itself establish unavailability:
    # an empty reason there would reach the toggle on a null PluginDeveloper.
    try:
        missing_plugin = _if_body(code, "!PluginDeveloper.GetInstance()")
    except AssertionError:
        raise AssertionError(
            "DispatchDevFreeCameraSet step c missing-plugin branch if (!PluginDeveloper.GetInstance()) is missing or not unique"
        ) from None
    if missing_plugin.strip() != 'unavailable = "plugin_developer_missing";':
        raise AssertionError(
            'DispatchDevFreeCameraSet step c missing-plugin branch must set unavailable = "plugin_developer_missing";'
        )
    # Each refusal must exit: it sets the error, logs it and returns true as
    # its last statement, so a refused call never reaches the toggle.
    refusals = (
        ("a", "!command.args", "bad_args"),
        ("b", "!GetGame() || !GetGame().GetPlayer() || GetGame().IsDedicatedServer()", "client_not_in_game"),
        ("c", 'unavailable != ""', "dev_free_camera_unavailable"),
        ("d", "m_CameraHandoffPending || m_ActiveCam == freeCam", "dev_free_camera_bridge_owned"),
        ("e", "HasExclusiveJob()", "busy"),
    )
    for step, condition, error in refusals:
        try:
            block = _if_body(code, condition)
        except AssertionError:
            raise AssertionError(
                f"DispatchDevFreeCameraSet step {step} refusal if ({condition}) is missing or not unique"
            ) from None
        cursor = 0
        for token in ("result.ok = false;", f'result.error = "{error}";', 'Log("dev_free_camera_set ', "return true;"):
            at = block.find(token, cursor)
            if at < 0:
                raise AssertionError(f"DispatchDevFreeCameraSet step {step} refusal is missing {token!r} or it is out of order")
            cursor = at + len(token)
        if f"error={error}" not in block:
            raise AssertionError(f"DispatchDevFreeCameraSet step {step} refusal does not log error={error}")
        if not block.rstrip().endswith("return true;"):
            raise AssertionError(
                f"DispatchDevFreeCameraSet step {step} refusal must end with 'return true;' so it exits without reaching the toggle"
            )


def assert_bad_args_precedes_in_game_gate(dispatch_chain: str) -> None:
    """Dispatch answers a null-args dev_free_camera_set with bad_args before the client_not_in_game gate."""
    code = _without_comments(dispatch_chain)
    gate = "if (!IsClientInGame())"
    if code.count(gate) != 1:
        raise AssertionError(f"{gate!r} occurs {code.count(gate)} times in Dispatch")
    condition = 'cmd == "dev_free_camera_set" && !command.args'
    try:
        block = _if_body(code, condition)
    except AssertionError:
        raise AssertionError(
            "Dispatch must answer dev_free_camera_set bad_args before the client_not_in_game gate: "
            f"if ({condition}) is missing or not unique"
        ) from None
    if code.index(f"if ({condition})") > code.index(gate):
        raise AssertionError("Dispatch answers dev_free_camera_set bad_args after the client_not_in_game gate")
    for token in (
        "result.ok = false;",
        'result.error = "bad_args";',
        'Log("dev_free_camera_set ',
        "error=bad_args",
        "PostResult(result);",
        "return;",
    ):
        if token not in block:
            raise AssertionError(f"the Dispatch bad_args answer before the gate is missing {token!r}")
    if not block.rstrip().endswith("return;"):
        raise AssertionError("the Dispatch bad_args answer before the gate must end with 'return;' so it does not reach the gate")


def assert_retail_build_answers_unavailable(source: str) -> None:
    """Without DIAG_DEVELOPER nothing names PluginDeveloper and the dispatcher answers dev_free_camera_unavailable."""
    retail = _retail_view(source)
    named = re.findall(r"\bPluginDeveloper\b", _without_comments_or_strings(retail))
    if named:
        raise AssertionError(f"PluginDeveloper is named outside #ifdef DIAG_DEVELOPER ({len(named)} time(s))")
    body = _without_comments(_method_body(retail, DISPATCH))
    for token in (
        'unavailable = "no_diag_developer";',
        'if (unavailable != "")',
        'result.error = "dev_free_camera_unavailable";',
    ):
        if token not in body:
            raise AssertionError(f"without DIAG_DEVELOPER the dispatcher lost {token!r}")
    if 'unavailable = "";' in body:
        raise AssertionError("without DIAG_DEVELOPER the dispatcher still clears the unavailable reason")


def assert_bridge_does_not_own_the_camera(*bodies: str) -> None:
    for body in bodies:
        found = _ownership_violations(body)
        if found:
            raise AssertionError(f"the bridge does not own this camera, but the body runs {found}")


def assert_read_job_is_not_exclusive(exclusive_body: str) -> None:
    code = _without_comments(exclusive_body)
    count = re.search(r"\b(\w+) = m_JobRunner\.CountOfKind\(\"dev_free_camera_set\"\);", code)
    if count is None:
        raise AssertionError('HasExclusiveJob does not count the "dev_free_camera_set" read jobs')
    name = count.group(1)
    subtract = f"blocking = blocking - {name};"
    ret = "return blocking > 0;"
    if subtract not in code or ret not in code:
        raise AssertionError(f"HasExclusiveJob must take {name} off blocking before {ret!r}")
    if not code.index(count.group(0)) < code.index(subtract) < code.index(ret):
        raise AssertionError("HasExclusiveJob subtracts the read jobs after it answers")


def assert_read_job_contract(process_body: str, post_body: str) -> None:
    proc = _without_comments(process_body)
    if "return m_JobRunner.GetElapsedS() > job.sample_start_s;" not in proc:
        raise AssertionError("ProcessDevFreeCameraJob must be done on the next tick: 'return m_JobRunner.GetElapsedS() > job.sample_start_s;'")
    post = _without_comments(post_body)
    for body, name in ((proc, "ProcessDevFreeCameraJob"), (post, "PostDevFreeCameraJob")):
        if re.search(r"\bToggleFreeCameraBackPos\s*\(", _without_comments_or_strings(body)):
            raise AssertionError(f"{name} must only read: it calls ToggleFreeCameraBackPos")
    steps = (
        "freeCam = FreeDebugCamera.GetInstance();",
        "after = freeCam.IsActive();",
        "reply.active_after = after;",
        "result.dev_free_camera = reply;",
        'result.error = "dev_free_camera_not_switched";',
        "PostResult(result);",
    )
    cursor = 0
    for token in steps:
        at = post.find(token, cursor)
        if at < 0:
            raise AssertionError(f"PostDevFreeCameraJob step {token!r} is missing or out of order")
        cursor = at + len(token)
    if "requested = job.args.active;" not in post:
        raise AssertionError("PostDevFreeCameraJob must compare the read with the requested job.args.active")


class DevFreeCameraCensusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(CLIENT_PATH)

    def test_client_caps_and_dispatch_name_the_verb_next_to_ui_pause(self) -> None:
        self.assertIn(COMMAND, announced_caps(self.source))
        names = dispatch_census(self.source)
        self.assertEqual(names[names.index("ui_pause") + 1], COMMAND)
        self.assertEqual(names[-1], "ui_dialog")
        dispatch = _method_body(self.source, DISPATCH_CHAIN)
        branch = _if_body(dispatch, f'command.cmd == "{COMMAND}"')
        self.assertEqual(branch.strip(), "postNow = DispatchDevFreeCameraSet(command, result);")
        gate = _if_body(dispatch, f'cmd == "{COMMAND}"')
        self.assertEqual(gate.count("Log("), 1)
        self.assertIn("error=client_not_in_game", gate)

    def test_server_bridge_does_not_announce_or_implement_the_verb(self) -> None:
        self.assertNotIn(COMMAND, _source(SERVER_BRIDGE_PATH))
        fixture = json.loads(CENSUS_FIXTURE.read_text(encoding="utf-8"))
        self.assertNotIn(COMMAND, fixture["peers"]["server"])
        self.assertEqual(fixture["peers"]["client"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, _BRIDGE_COMMAND_TOOLS["server"])
        self.assertEqual(_BRIDGE_COMMAND_TOOLS["client"].get(COMMAND), COMMAND)
        self.assertNotIn(COMMAND, server.SERVER_ARG_CONTRACT)

    def test_stale_client_census_flags_the_verb_missing(self) -> None:
        registered = frozenset(tool for tool in _BRIDGE_COMMAND_TOOLS["client"].values() if tool)
        announced = sorted(_BRIDGE_COMMAND_TOOLS["client"])
        announced.remove(COMMAND)
        result = server._compare_bridge_capabilities(
            "client",
            {"state": "announced", "reason": "ok", "announced_commands": announced},
            registered,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["registered_without_announced_command"], [COMMAND])


class DevFreeCameraEnforceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(CLIENT_PATH)
        self.dispatch = _method_body(self.source, DISPATCH)
        self.process = _method_body(self.source, PROCESS)
        self.post = _method_body(self.source, POST)
        self.exclusive = _method_body(self.source, EXCLUSIVE)

    def test_checks_run_in_order_before_the_one_toggle(self) -> None:
        assert_dispatch_contract(self.dispatch)

    # Amendment B1: a legitimate change to either zone updates its pinned
    # literal in the same commit as the change to the zone.
    def test_the_two_amended_zones_match_their_pinned_literals(self) -> None:
        assert_amended_zones(self.dispatch)

    def test_negative_controls_on_the_amended_zones(self) -> None:
        reset = '\t\tunavailable = "";\n'
        self.assertEqual(self.dispatch.count(reset), 1, "the check c reset does not have the expected shape")
        without_reset = self.dispatch.replace(reset, "", 1)
        moved_anchor = "\t\t}\n#endif\n\t\tfreeCam = FreeDebugCamera.GetInstance();"
        self.assertEqual(without_reset.count(moved_anchor), 1, "the missing-plugin block does not have the expected shape")
        moved_reset = without_reset.replace(
            moved_anchor,
            '\t\t}\n\t\tunavailable = "";\n#endif\n\t\tfreeCam = FreeDebugCamera.GetInstance();',
            1,
        )
        toggle_line = "\t\t\t" + TOGGLE_CALL + "\n"
        self.assertEqual(self.dispatch.count(toggle_line), 1, "the toggle line does not have the expected shape")
        without_toggle = self.dispatch.replace(toggle_line, "", 1)
        guard = "\t\tif (before != requested)"
        self.assertEqual(without_toggle.count(guard), 1, "the toggle guard does not have the expected shape")
        moved_toggle = without_toggle.replace(guard, "\t\t" + TOGGLE_CALL + "\n" + guard, 1)
        mutations = (
            ("zone 1 delete the reset", without_reset),
            ("zone 1 move the reset after the missing-plugin block", moved_reset),
            (
                "zone 1 invert the requested binding",
                self.dispatch.replace("requested = command.args.active;", "requested = !command.args.active;", 1),
            ),
            (
                "zone 1 empty the missing-plugin literal",
                self.dispatch.replace('unavailable = "plugin_developer_missing";', 'unavailable = "";', 1),
            ),
            ("zone 2 delete the toggle", without_toggle),
            ("zone 2 move the toggle before the guard", moved_toggle),
            (
                "zone 2 invert the guard",
                self.dispatch.replace("if (before != requested)", "if (before == requested)", 1),
            ),
            ("zone 2 replace the toggle with a bare semicolon", self.dispatch.replace(TOGGLE_CALL, ";", 1)),
        )
        for name, mutated in mutations:
            with self.subTest(name=name):
                self.assertNotEqual(mutated, self.dispatch, f"{name} did not change the dispatcher")
                with self.assertRaises(AssertionError):
                    assert_amended_zones(mutated)

    def test_dispatch_answers_bad_args_before_the_in_game_gate(self) -> None:
        chain = _method_body(self.source, DISPATCH_CHAIN)
        assert_bad_args_precedes_in_game_gate(chain)
        without_exit = _replace_in_if_block(chain, 'cmd == "dev_free_camera_set" && !command.args', "return;", "")
        with self.assertRaisesRegex(AssertionError, "return;"):
            assert_bad_args_precedes_in_game_gate(without_exit)
        removed = chain.replace('if (cmd == "dev_free_camera_set" && !command.args)', "if (false)", 1)
        with self.assertRaisesRegex(AssertionError, "bad_args before the client_not_in_game gate"):
            assert_bad_args_precedes_in_game_gate(removed)

    def test_negative_controls_on_the_refusal_exits(self) -> None:
        for condition in (
            "!command.args",
            "!GetGame() || !GetGame().GetPlayer() || GetGame().IsDedicatedServer()",
            'unavailable != ""',
            "m_CameraHandoffPending || m_ActiveCam == freeCam",
            "HasExclusiveJob()",
        ):
            with self.subTest(condition=condition):
                without_exit = _replace_in_if_block(self.dispatch, condition, "return true;", "")
                with self.assertRaisesRegex(AssertionError, "return true"):
                    assert_dispatch_contract(without_exit)
        # The reviewer's case: check c without its log and return would fall
        # through to the toggle when PluginDeveloper is missing.
        log_line = 'Log("dev_free_camera_set requested=" + requestedBit + " before=-1 after=-1 ok=0 error=dev_free_camera_unavailable reason=" + unavailable);'
        self.assertEqual(self.dispatch.count(log_line), 1, "the c refusal log does not have the expected shape")
        without_log = _replace_in_if_block(self.dispatch, 'unavailable != ""', log_line, "")
        with self.assertRaisesRegex(AssertionError, "Log"):
            assert_dispatch_contract(without_log)
        without_both = _replace_in_if_block(without_log, 'unavailable != ""', "return true;", "")
        with self.assertRaisesRegex(AssertionError, "Log|return true"):
            assert_dispatch_contract(without_both)

    def test_negative_controls_on_the_check_order(self) -> None:
        d_check = (
            "\t\tif (m_CameraHandoffPending || m_ActiveCam == freeCam)\n"
            "\t\t{\n"
            "\t\t\tresult.ok = false;\n"
            '\t\t\tresult.error = "dev_free_camera_bridge_owned";\n'
        )
        self.assertEqual(self.dispatch.count(d_check), 1, "the d check does not have the expected shape")
        without_d = self.dispatch.replace(d_check, "\t\tif (false)\n\t\t{\n\t\t\tresult.ok = false;\n", 1)
        with self.assertRaisesRegex(AssertionError, "step d"):
            assert_dispatch_contract(without_d)
        only_handoff = self.dispatch.replace(
            "if (m_CameraHandoffPending || m_ActiveCam == freeCam)", "if (m_CameraHandoffPending)", 1
        )
        with self.assertRaisesRegex(AssertionError, "step d"):
            assert_dispatch_contract(only_handoff)
        without_busy = self.dispatch.replace("if (HasExclusiveJob())", "if (false)", 1)
        with self.assertRaisesRegex(AssertionError, "step e"):
            assert_dispatch_contract(without_busy)
        without_c = self.dispatch.replace('result.error = "dev_free_camera_unavailable";', 'result.error = "x";', 1)
        with self.assertRaisesRegex(AssertionError, "step c"):
            assert_dispatch_contract(without_c)
        # The reviewer's case: an empty missing-plugin reason would leave
        # unavailable clear and reach the toggle on a null PluginDeveloper.
        self.assertEqual(
            self.dispatch.count('unavailable = "plugin_developer_missing";'),
            1,
            "the missing-plugin reason does not have the expected shape",
        )
        missing_plugin_cleared = self.dispatch.replace('unavailable = "plugin_developer_missing";', 'unavailable = "";', 1)
        with self.assertRaisesRegex(AssertionError, "plugin_developer_missing"):
            assert_dispatch_contract(missing_plugin_cleared)
        unconditional = self.dispatch.replace("if (before != requested)", "if (true)", 1)
        with self.assertRaisesRegex(AssertionError, "before != requested"):
            assert_dispatch_contract(unconditional)
        # The toggle moved ahead of the d check: the order fails.
        hoisted = self.dispatch.replace(TOGGLE_CALL, "", 1).replace(
            "\t\tif (m_CameraHandoffPending || m_ActiveCam == freeCam)",
            "#ifdef DIAG_DEVELOPER\n\t\t" + TOGGLE_CALL + "\n#endif\n\t\tif (m_CameraHandoffPending || m_ActiveCam == freeCam)",
            1,
        )
        with self.assertRaises(AssertionError):
            assert_dispatch_contract(hoisted)
        twice = self.dispatch.replace(TOGGLE_CALL, TOGGLE_CALL + "\n\t\t\t" + TOGGLE_CALL, 1)
        with self.assertRaisesRegex(AssertionError, "exactly one"):
            assert_dispatch_contract(twice)
        commented = self.dispatch.replace(TOGGLE_CALL, "// " + TOGGLE_CALL, 1)
        with self.assertRaisesRegex(AssertionError, "exactly one"):
            assert_dispatch_contract(commented)

    def test_a_build_without_diag_developer_answers_unavailable(self) -> None:
        assert_retail_build_answers_unavailable(self.source)

    def test_negative_controls_on_the_diag_developer_guard(self) -> None:
        guarded = "#ifdef DIAG_DEVELOPER\n\t\tif (before != requested)"
        self.assertEqual(self.source.count(guarded), 1, "the toggle guard does not have the expected shape")
        unguarded = self.source.replace(guarded, "\t\tif (before != requested)", 1)
        unguarded = unguarded.replace(TOGGLE_CALL + "\n\t\t}\n#endif\n", TOGGLE_CALL + "\n\t\t}\n", 1)
        with self.assertRaisesRegex(AssertionError, "PluginDeveloper is named outside"):
            assert_retail_build_answers_unavailable(unguarded)
        with self.assertRaisesRegex(AssertionError, "#ifdef DIAG_DEVELOPER"):
            assert_dispatch_contract(_method_body(unguarded, DISPATCH))
        cleared = self.source.replace('unavailable = "no_diag_developer";', 'unavailable = "";', 1)
        with self.assertRaisesRegex(AssertionError, "no_diag_developer"):
            assert_retail_build_answers_unavailable(cleared)
        with self.assertRaisesRegex(AssertionError, "#else"):
            _retail_view("#ifdef DIAG_DEVELOPER\nint a;\n#else\nint b;\n#endif\n")

    def test_the_bridge_does_not_own_the_camera(self) -> None:
        assert_bridge_does_not_own_the_camera(self.dispatch, self.process, self.post)

    def test_negative_controls_on_camera_ownership(self) -> None:
        anchor = "\t\tbefore = freeCam.IsActive();\n"
        self.assertEqual(self.dispatch.count(anchor), 1, "the read before the toggle does not have the expected shape")
        for statement in (
            "StopCameraPath();",
            "ReleaseCamera();",
            "RestoreGameplay();",
            "m_ActiveCam = freeCam;",
            "m_ActiveCamOwned = false;",
            "m_PlayerSimulationDisabled = true;",
            "freeCam.SetActive(true);",
            "player.DisableSimulation(true);",
        ):
            with self.subTest(statement=statement):
                mutated = self.dispatch.replace(anchor, anchor + "\t\t" + statement + "\n", 1)
                with self.assertRaisesRegex(AssertionError, "does not own this camera"):
                    assert_bridge_does_not_own_the_camera(mutated)
                post = self.post.replace("PostResult(result);", statement + "\n\t\tPostResult(result);", 1)
                with self.assertRaisesRegex(AssertionError, "does not own this camera"):
                    assert_bridge_does_not_own_the_camera(self.dispatch, self.process, post)
        # A comparison, a comment and a log string are not calls or assignments.
        harmless = self.dispatch.replace(
            anchor,
            anchor + '\t\t// StopCameraPath(); m_ActiveCam = null;\n\t\tLog("ReleaseCamera() m_ActiveCamOwned = 0");\n',
            1,
        )
        assert_bridge_does_not_own_the_camera(harmless)
        self.assertIn("m_ActiveCam == freeCam", _without_comments(self.dispatch))

    def test_the_read_job_does_not_count_as_exclusive(self) -> None:
        assert_read_job_is_not_exclusive(self.exclusive)
        code = _without_comments(self.exclusive)
        name = re.search(r"\b(\w+) = m_JobRunner\.CountOfKind\(\"dev_free_camera_set\"\);", code).group(1)
        without = self.exclusive.replace(f"blocking = blocking - {name};", "", 1)
        with self.assertRaisesRegex(AssertionError, "take"):
            assert_read_job_is_not_exclusive(without)
        uncounted = self.exclusive.replace('CountOfKind("dev_free_camera_set")', 'CountOfKind("weapon_action")', 1)
        with self.assertRaisesRegex(AssertionError, "does not count"):
            assert_read_job_is_not_exclusive(uncounted)

    def test_the_read_job_reads_on_the_next_tick_and_reports(self) -> None:
        assert_read_job_contract(self.process, self.post)
        waits = self.process.replace("> job.sample_start_s;", ">= job.deadline_s;", 1)
        with self.assertRaisesRegex(AssertionError, "next tick"):
            assert_read_job_contract(waits, self.post)
        toggles = self.post.replace("PostResult(result);", TOGGLE_CALL + "\n\t\tPostResult(result);", 1)
        with self.assertRaisesRegex(AssertionError, "only read"):
            assert_read_job_contract(self.process, toggles)
        silent = self.post.replace('result.error = "dev_free_camera_not_switched";', 'result.error = "";', 1)
        with self.assertRaisesRegex(AssertionError, "dev_free_camera_not_switched"):
            assert_read_job_contract(self.process, silent)

    def test_the_job_hooks_route_the_read_job(self) -> None:
        process_hook = _without_comments(_method_body(self.source, PROCESS_HOOK))
        branch = _if_body(process_hook, f'job.kind == "{COMMAND}"')
        self.assertEqual(branch.strip(), "return ProcessDevFreeCameraJob(job);")
        for hook in (SUCCESS_HOOK, FAILURE_HOOK, TIMEOUT_HOOK):
            with self.subTest(hook=hook):
                body = _without_comments(_method_body(self.source, hook))
                self.assertRegex(body, rf'job\.kind == "{COMMAND}"\)\s*\{{\s*PostDevFreeCameraJob\(job\);')

    def test_one_log_line_on_every_answer(self) -> None:
        dispatch = _without_comments(self.dispatch)
        post = _without_comments(self.post)
        self.assertEqual(dispatch.count("return true;"), dispatch.count('Log("dev_free_camera_set '))
        self.assertEqual(dispatch.count("return false;"), 1)
        self.assertEqual(post.count('Log("dev_free_camera_set '), 3)

    def test_a_body_without_the_expected_shape_fails_clearly(self) -> None:
        with self.assertRaisesRegex(AssertionError, "occurs 0 times"):
            _method_body(self.source.replace(DISPATCH, DISPATCH.replace("Dispatch", "Handle"), 1), DISPATCH)
        with self.assertRaisesRegex(AssertionError, "step a"):
            assert_dispatch_contract(TOGGLE_CALL)

    def test_message_classes_and_result_prune_wiring(self) -> None:
        messages = _source(MESSAGES_PATH)
        reply = _class_body(messages, "MCPDevFreeCamera")
        self.assertRegex(reply, r"(?m)^\tbool active_before;$")
        self.assertRegex(reply, r"(?m)^\tbool active_after;$")
        self.assertRegex(_class_body(messages, "MCPArgs"), r"(?m)^\tbool active;$")
        self.assertRegex(_class_body(messages, "MCPResult"), r"(?m)^\tref MCPDevFreeCamera dev_free_camera;$")
        self.assertRegex(_class_body(messages, "MCPJob"), r"(?m)^\tref MCPDevFreeCamera dev_free_camera;$")
        self.assertEqual(PRUNABLE_FIELDS[-1], FIELD)
        for block in ({"active_before": 0, "active_after": 1}, {"active_before": 0, "active_after": 0}):
            with self.subTest(block=block):
                filled = {"ok": 1, FIELD: dict(block)}
                self.assertEqual(prune_unfilled_fields(COMMAND, filled)[FIELD], block)
        self.assertNotIn(FIELD, prune_unfilled_fields(COMMAND, {"ok": 0, "error": "busy", FIELD: None}))
        self.assertNotIn(FIELD, prune_unfilled_fields("ui_pause", {"ok": 1, FIELD: {}}))


class DevFreeCameraIngressAndAdmissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)
        self.caps = poll_census_query("client")["caps"].split(",")
        self.older = [name for name in self.caps if name != COMMAND]

    def _poll(self, caps: list[str]) -> dict[str, Any]:
        status, body = self.state.record_poll(
            "client",
            "12~1.30",
            instance=INST_CLIENT,
            source_pid=PID_CLIENT,
            caps=",".join(caps),
        )
        self.assertEqual(status, 200, body)
        return body

    def test_client_command_requires_a_lease_like_ui_pause(self) -> None:
        self.assertIn(COMMAND, loopback.CLIENT_COMMANDS)
        self.assertNotIn(COMMAND, loopback.SERVER_COMMANDS)
        self.assertNotIn(COMMAND, loopback.EXEC_COMMANDS)
        self.assertEqual(loopback.peer_for_command(COMMAND), "client")
        self.assertNotIn(COMMAND, READ_ONLY_COMMANDS)
        self.assertEqual(command_requires_lease(COMMAND), command_requires_lease("ui_pause"))
        self.assertTrue(command_requires_lease(COMMAND))

    def test_a_valid_request_without_a_lease_is_refused_and_queues_nothing(self) -> None:
        self._poll(self.caps)
        self.state.coordination = SessionCoordinator(
            token_fn=lambda: "token",
            id_fn=lambda: "lease",
            audit=lambda event: True,
            cleanup=lambda session, lease, reason, active: self.state.cleanup_owner(
                session, lease, reason, active
            ),
        )
        self.assertEqual(
            self.state.enqueue_command(COMMAND, {"active": True}, "client", identity_payload=IDENTITY),
            (423, {"error": "lease_required"}),
        )
        self.assertEqual(bound_queue(self.state, "client"), [])
        self.assertEqual(bound_queue(self.state, "server"), [])

    def test_ingress_schema_takes_a_bool_active_and_nothing_else(self) -> None:
        for active in (True, False):
            with self.subTest(active=active):
                self.assertEqual(loopback.validate_command_args(COMMAND, {"active": active}), (True, None))
        for bad in (
            {},
            {"active": True, "extra": 1},
            {"active": False, "timeout_s": 5.0},
            {"active": 1},
            {"active": 0},
            {"active": "true"},
            {"active": None},
            {"on": True},
        ):
            with self.subTest(args=bad):
                self.assertEqual(loopback.validate_command_args(COMMAND, bad), (False, "bad_args"))
                self.assertEqual(self.state.enqueue_command(COMMAND, bad), (400, {"error": "bad_args"}))
        self.assertEqual(bound_queue(self.state, "client"), [])

    def test_older_client_pbo_is_refused_and_current_client_receives_command(self) -> None:
        self.assertEqual(
            self.state.enqueue_command(COMMAND, {"active": True}),
            (409, {"error": "bridge_capability_missing"}),
        )
        self._poll(self.older)
        self.assertEqual(
            self.state.enqueue_command(COMMAND, {"active": True}),
            (409, {"error": "bridge_capability_missing"}),
        )
        self.assertEqual(bound_queue(self.state, "client"), [])

        self._poll(self.caps)
        status, body = self.state.enqueue_command(COMMAND, {"active": True})
        self.assertEqual((status, body["peer"], body["cmd"]), (200, "client", COMMAND))
        delivered = self._poll(self.caps)
        self.assertEqual(delivered["commands"], [{"id": body["id"], "cmd": COMMAND, "args": {"active": True}}])


def _tool_error_text(exc: BaseException) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {COMMAND}: "
    return message[len(wrapper) :] if message.startswith(wrapper) else message


def _tool_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    blocks = raw[0] if isinstance(raw, tuple) else raw
    return json.loads(blocks[0].text)


class DevFreeCameraToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(ServerConfig(key="test-key", port=0, log_sink=lambda _message: None))

    async def _tool(self):
        tools = {tool.name: tool for tool in await self.app.list_tools()}
        self.assertIn(COMMAND, tools)
        return tools[COMMAND]

    async def _refused(self, arguments: dict[str, Any]) -> str:
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            with self.assertRaises(ToolError) as raised:
                await self.app.call_tool(COMMAND, arguments)
        call.assert_not_awaited()
        return _tool_error_text(raised.exception)

    async def test_registered_with_the_lease_line_and_a_required_bool(self) -> None:
        tool = await self._tool()
        description = tool.description or ""
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description)
        schema = tool.inputSchema
        self.assertEqual(set(schema["properties"]), {"active", "timeout_s"})
        self.assertEqual(schema.get("required"), ["active"])
        self.assertEqual(schema["properties"]["active"].get("type"), "boolean")

    async def test_the_description_states_the_contract(self) -> None:
        description = (await self._tool()).description or ""
        for phrase in (
            "PluginDeveloper.ToggleFreeCameraBackPos",
            "KC_DIVIDE",
            "not camera_set's free mode",
            "the bridge does not own",
            "camera_path_play",
            "aborted",
            "turning it on ends a playing camera_path_play aborted",
            "restore_gameplay turns it off",
            "stays on when the lease ends",
            "dev_free_camera {active_before, active_after}",
            "bad_args",
            "client_not_in_game",
            "dev_free_camera_unavailable",
            "dev_free_camera_bridge_owned",
            "busy",
            "dev_free_camera_not_switched",
            "bridge_capability_missing",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)

    async def test_active_true_and_false_reach_the_client_as_sent(self) -> None:
        for active in (True, False):
            with self.subTest(active=active):
                call = AsyncMock(return_value={"ok": 1, FIELD: {"active_before": 0, "active_after": int(active)}})
                with patch.object(self.runtime, "call_bridge", new=call):
                    raw = await self.app.call_tool(COMMAND, {"active": active})
                call.assert_awaited_once_with(COMMAND, {"active": active}, "client", DEFAULT_TOOL_TIMEOUT_S)
                self.assertEqual(_tool_json(raw)[FIELD], {"active_before": 0, "active_after": int(active)})
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            await self.app.call_tool(COMMAND, {"active": True, "timeout_s": 3.5})
        call.assert_awaited_once_with(COMMAND, {"active": True}, "client", 3.5)

    async def test_an_active_that_is_not_a_bool_is_bad_args_and_never_reaches_the_bridge(self) -> None:
        for value in (1, 0, "true", None):
            with self.subTest(value=value):
                text = await self._refused({"active": value})
                self.assertTrue(text.startswith("bad_args: active "), text)
                self.assertIn("must be a bool", text)
        self.assertIn("Field required", await self._refused({}))
        self.assertIn("bad_timeout", await self._refused({"active": True, "timeout_s": 0.0}))

    async def test_full_runtime_round_trip_keeps_the_reply_and_propagates_the_errors(self) -> None:
        state = loopback.ServerState("test-key")
        bind_both_peers(state)
        caps = poll_census_query("client")["caps"]
        state.record_poll("client", "12~1.30", instance=INST_CLIENT, source_pid=PID_CLIENT, caps=caps)
        self.runtime.loopback = SimpleNamespace(state=state)
        original_enqueue = state.enqueue_command
        wire: dict[str, Any] = {}

        def enqueue_and_answer(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
            status, body = original_enqueue(*args, **kwargs)
            if status == 200:
                poll_status, poll_body = state.record_poll(
                    "client", "12~1.30", instance=INST_CLIENT, source_pid=PID_CLIENT, caps=caps
                )
                self.assertEqual(poll_status, 200, poll_body)
                self.assertEqual(poll_body["commands"], [{"id": body["id"], "cmd": COMMAND, "args": {"active": True}}])
                store_status, store_body = state.store_result({"id": body["id"], **wire}, instance=INST_CLIENT)
                self.assertEqual(store_status, 200, store_body)
            return status, body

        with patch.object(state, "enqueue_command", side_effect=enqueue_and_answer):
            wire = {"ok": 1, FIELD: {"active_before": 0, "active_after": 1}, "ui_pause": None}
            payload = _tool_json(await self.app.call_tool(COMMAND, {"active": True}))
            self.assertEqual(payload["ok"], 1)
            self.assertEqual(payload[FIELD], {"active_before": 0, "active_after": 1})
            self.assertNotIn("ui_pause", payload)
            for code in (
                "dev_free_camera_not_switched",
                "dev_free_camera_unavailable",
                "dev_free_camera_bridge_owned",
                "busy",
                "client_not_in_game",
            ):
                with self.subTest(error=code):
                    wire = {"ok": 0, "error": code, FIELD: {"active_before": 0, "active_after": 0}}
                    with self.assertRaises(ToolError) as raised:
                        await self.app.call_tool(COMMAND, {"active": True})
                    self.assertEqual(_tool_error_text(raised.exception), code)


if __name__ == "__main__":
    unittest.main()
