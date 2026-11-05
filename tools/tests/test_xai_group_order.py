"""ai_group_order (ficha 3ca5, expansion-ai-adapter plan 3.2, 3.3 and 4).

Offline criteria O3 (the order part), O4 (the order part) and E5, plus the
O1/O2/O7/O14 branches this verb adds on top of tests.test_xai_foundation.
Each test names the mutant it kills; ExpansionOrderMutantTest runs two of
those mutants on a mutated copy of the adapter source and shows the E5
expectations reject them (R26).
"""

from __future__ import annotations

import math
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, server
from dayz_mcp.bridge_readiness import _compare_bridge_capabilities
from dayz_mcp.session_coordination import command_requires_lease
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceString,
    EnforceVector,
    method_body,
    translate,
)
from tests.fence_helpers import INST_CLIENT, PID_CLIENT, bound_queue
from tests.xai_helpers import GOOD_XAI, announce as _announce, bound_state as _bound_state, good_commands as _good_commands

ORDER = "ai_group_order"
GROUP_ID = 1000000001
ADAPTER = addon_root().parent / "addon_expansion_ai" / "scripts" / "5_Mission" / "MCPExpansionAI.c"

FORMATIONS = [
    "Column", "InvColumn", "File", "InvFile", "Vee", "InvVee",
    "Wall", "Circle", "CircleDot", "Star", "StarDot",
]


def _tool_error_text(exc: BaseException) -> str:
    text = str(exc)
    nested = getattr(exc, "exceptions", None)
    if nested:
        text = text + " " + str(nested)
    return text


class _WireString(EnforceString):
    __slots__ = ()

    def __add__(self, other: object) -> "_WireString":
        if isinstance(other, EnforceString):
            return _WireString(self.data + other.data)
        return NotImplemented


def _s(value: str) -> _WireString:
    return _WireString.of(value)


class AiGroupOrderToolTest(unittest.IsolatedAsyncioTestCase):
    """O3/O4 through the real tool entry, and the wire of plan 4."""

    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.tool = self.app._tool_manager.get_tool(ORDER)

    async def _call(self, arguments: dict):
        answer = {"ok": 1, "ai_group": {"group_id": GROUP_ID}}
        with patch.object(
            self.runtime, "call_bridge", new=AsyncMock(return_value=answer)
        ) as call:
            result = await self.app.call_tool(ORDER, arguments)
        call.assert_awaited_once()
        return call.await_args.args, result

    async def _refused(self, arguments: dict) -> str:
        with patch.object(
            self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})
        ) as call:
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool(ORDER, arguments)
        call.assert_not_awaited()
        return _tool_error_text(caught.exception)

    def test_tool_is_registered_closed_and_lease_gated(self) -> None:
        # Mutant killed: the tool registered read-only (no lease) or with an
        # open schema that drops a mistyped key on the floor.
        self.assertIsNotNone(self.tool)
        self.assertEqual(
            set(self.tool.parameters["properties"]),
            {"group_id", "behaviour", "waypoints", "formation", "speed", "timeout_s"},
        )
        self.assertIs(self.tool.parameters.get("additionalProperties"), False)
        self.assertIn(ORDER, server._CLOSED_SCHEMA_TOOLS)
        self.assertTrue(command_requires_lease(ORDER))
        self.assertTrue(self.tool.description.startswith(server.LEASE_TOOL_LINE))
        self.assertIn("combat_may_override", self.tool.description)

    async def test_o4_order_without_fields_is_bad_args_and_sends_nothing(self) -> None:
        # Mutant killed: an empty order travelling to the bridge, where the
        # absent keys would read as 0 and change nothing silently.
        text = await self._refused({"group_id": GROUP_ID})
        self.assertIn("bad_args", text)

    async def test_o3_behaviour_lowercase_or_unknown_is_bad_args(self) -> None:
        # Mutant killed: accepting "hold" or "PATROL" and letting the bridge
        # (or Expansion) guess what was meant.
        for behaviour in ("hold", "PATROL"):
            with self.subTest(behaviour=behaviour):
                text = await self._refused(
                    {"group_id": GROUP_ID, "behaviour": behaviour}
                )
                self.assertIn("bad_args", text)
                for allowed in ("HOLD", "LOOP", "ONCE", "ROAM"):
                    self.assertIn(allowed, text)

    async def test_o3_bad_waypoints_are_bad_args(self) -> None:
        # Mutant killed: a 33rd waypoint, a NaN or a two-number point
        # reaching the bridge as a truncated flat triple.
        bad = (
            [],
            [[float(index), 0.0, 0.0] for index in range(33)],
            [[float("nan"), 0.0, 0.0]],
            [[1.0, 2.0]],
            [[1.0, 2.0, float("inf")]],
        )
        for waypoints in bad:
            with self.subTest(waypoints=str(waypoints)[:40]):
                text = await self._refused(
                    {"group_id": GROUP_ID, "waypoints": waypoints}
                )
                self.assertIn("bad_args", text)

    async def test_o3_bad_speed_is_bad_args(self) -> None:
        # Mutant killed: a speed outside walk/jog/sprint mapping to a
        # movement-speed value the caller never asked for.
        text = await self._refused({"group_id": GROUP_ID, "speed": "run"})
        self.assertIn("bad_args", text)
        self.assertIn("walk", text)

    async def test_wire_maps_speed_and_flattens_waypoints_to_the_server(self) -> None:
        # Mutant killed: speed travelling as its word (the bridge reads the
        # base float field), or waypoints travelling nested.
        args, _result = await self._call(
            {
                "group_id": GROUP_ID,
                "behaviour": "LOOP",
                "waypoints": [[1, 2, 3], [4, 5, 6]],
                "formation": "Vee",
                "speed": "sprint",
            }
        )
        self.assertEqual(
            args,
            (
                ORDER,
                {
                    "group_id": GROUP_ID,
                    "behaviour": "LOOP",
                    "waypoints": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                    "formation": "Vee",
                    "speed": 3,
                    "speed_set": True,
                },
                "server",
                15.0,
            ),
        )
        for word, value in (("walk", 1), ("jog", 2)):
            args, _result = await self._call({"group_id": GROUP_ID, "speed": word})
            self.assertEqual(args[1], {"group_id": GROUP_ID, "speed": value, "speed_set": True})

    async def test_wire_sends_only_the_fields_present(self) -> None:
        # Mutant killed: an absent field travelling as an empty value the
        # bridge would apply (an empty formation, a zero speed).
        args, _result = await self._call({"group_id": GROUP_ID, "behaviour": "HOLD"})
        self.assertEqual(args[1], {"group_id": GROUP_ID, "behaviour": "HOLD"})


class AiGroupOrderBrokerTest(unittest.TestCase):
    """The loopback schema and the plan 2.2 admission for this verb."""

    def test_schema_accepts_each_field_and_rejects_bad_shapes(self) -> None:
        # Mutant killed: the broker handing the bridge a lowercase behaviour
        # or a non-triple waypoint list the Enforce side would misread.
        good = (
            {"group_id": GROUP_ID, "behaviour": "HOLD"},
            {"group_id": GROUP_ID, "waypoints": [1.0, 2.0, 3.0]},
            {"group_id": GROUP_ID, "formation": "Vee"},
            {"group_id": GROUP_ID, "speed": 2, "speed_set": True},
        )
        for args in good:
            with self.subTest(args=args):
                self.assertEqual(loopback.validate_command_args(ORDER, args), (True, None))
        bad = (
            {"group_id": GROUP_ID, "behaviour": "hold"},
            {"group_id": GROUP_ID, "behaviour": "PATROL"},
            {"group_id": GROUP_ID, "waypoints": [1.0, 2.0]},
            {"group_id": GROUP_ID, "waypoints": []},
            {"group_id": GROUP_ID, "waypoints": [float("nan")] * 3},
            {"group_id": 0, "behaviour": "LOOP"},
            {"group_id": GROUP_ID, "speed": 4, "speed_set": True},
            {"group_id": GROUP_ID, "speed": 2},
            {"group_id": GROUP_ID, "speed_set": True},
        )
        for args in bad:
            with self.subTest(args=args):
                self.assertIs(loopback.validate_command_args(ORDER, args)[0], False)

    def _commands(self, *, verb: bool = True, lease_close: bool = True) -> list[str]:
        commands = _good_commands(lease_close=lease_close)
        if not verb:
            commands = [name for name in commands if name != ORDER]
        return commands

    def _enqueue(self, state: loopback.ServerState):
        return state.enqueue_command(
            ORDER, {"group_id": GROUP_ID, "behaviour": "LOOP"}, peer="server"
        )

    def test_o1_missing_verb_is_adapter_not_loaded(self) -> None:
        # Mutant killed: the generic missing-verb refusal reaching this verb.
        state = _bound_state()
        _announce(state, self._commands(verb=False), server.EXPECTED_SERVER_ARG_CONTRACT_HASH, GOOD_XAI)
        status, body = self._enqueue(state)
        self.assertEqual(status, 409)
        self.assertEqual(body, {"error": "expansion_unavailable", "detail": "adapter_not_loaded"})
        self.assertEqual(bound_queue(state, "server"), [])

    def test_o2_bad_xai_is_adapter_contract_mismatch(self) -> None:
        # Mutant killed: this verb skipping the xai check the release has.
        state = _bound_state()
        _announce(state, self._commands(), server.EXPECTED_SERVER_ARG_CONTRACT_HASH, "0" * 16)
        status, body = self._enqueue(state)
        self.assertEqual(status, 409)
        self.assertEqual(body, {"error": "expansion_unavailable", "detail": "adapter_contract_mismatch"})
        self.assertEqual(bound_queue(state, "server"), [])

    def test_order_is_gated_on_lease_close_support(self) -> None:
        # Mutant killed: a new adapter mutation admitted while no peer
        # announces lease_close (plan 2.2, the close gate of 5.1).
        state = _bound_state()
        _announce(
            state,
            self._commands(lease_close=False),
            server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            GOOD_XAI,
        )
        status, body = self._enqueue(state)
        self.assertEqual(status, 409)
        self.assertEqual(
            body, {"error": "expansion_unavailable", "detail": "lease_close_not_supported"}
        )
        self.assertEqual(bound_queue(state, "server"), [])

    def test_order_is_blocked_when_a_bound_client_lacks_close_support(self) -> None:
        # Mutant killed: the close gate reading only the server peer
        # (plan 5.1 requires every peer the run binds).
        state = _bound_state()
        state.install_bound_peer(
            instance=INST_CLIENT, role="client", pid=PID_CLIENT, run_id="xai-run"
        )
        _announce(state, self._commands(), server.EXPECTED_SERVER_ARG_CONTRACT_HASH, GOOD_XAI)
        state._peer_caps["client"] = {
            "generation": state.daemon_generation,
            "commands": ["entities_query"],
            "reason": "ok",
            "arg_contract_hash": None,
            "expansion_arg_contract_hash": None,
            "announced_at": state._now(),
            "instance": INST_CLIENT,
        }
        status, body = self._enqueue(state)
        self.assertEqual(status, 409)
        self.assertEqual(
            body, {"error": "expansion_unavailable", "detail": "lease_close_not_supported"}
        )
        self.assertEqual(bound_queue(state, "server"), [])

    def test_order_enqueues_with_census_xai_and_close_support(self) -> None:
        # Mutant killed: the close gate refusing a fully announced adapter.
        state = _bound_state()
        _announce(state, self._commands(), server.EXPECTED_SERVER_ARG_CONTRACT_HASH, GOOD_XAI)
        status, body = self._enqueue(state)
        self.assertEqual(status, 200, body)
        self.assertEqual(len(bound_queue(state, "server")), 1)

    def test_o7_comparator_matches_with_and_without_the_verb(self) -> None:
        # Mutant killed: ai_group_order demanding the adapter census, so a
        # run without Expansion reads mismatch instead of match.
        registered = frozenset(
            tool
            for tool in server._BRIDGE_COMMAND_TOOLS["server"].values()
            if tool
        )
        base = sorted(server._BRIDGE_COMMAND_TOOLS["server"])
        for commands in ([name for name in base if name != ORDER], base):
            with self.subTest(last=commands[-1]):
                verdict = _compare_bridge_capabilities(
                    "server",
                    {
                        "state": "announced",
                        "reason": "ok",
                        "announced_commands": commands,
                        "announced_arg_contract_hash": server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
                    },
                    registered,
                )
                self.assertEqual(verdict["state"], "match", verdict)

    def test_adapter_census_and_live_profile(self) -> None:
        # Mutants killed: the adapter announcing the verb in caps instead
        # of the xai bitmask (the saturated poll would pass 1024, #321),
        # and the verb joining LIVE_ALLOWED_COMMANDS.
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertNotIn("ServerCapabilities()", source)
        verbs = re.search(r'EXPANSION_AI_VERBS = "([0-9]+)"', source)
        self.assertIsNotNone(verbs)
        from dayz_mcp.bridge_readiness import parse_xai

        _hash, names = parse_xai(f"{GOOD_XAI}.{verbs.group(1)}")
        self.assertIn(ORDER, names)
        poll = method_body(source, "protected override string ExtensionPollParams()")
        self.assertIn("&xai=", poll)
        dispatch = method_body(
            source, "protected override int DispatchExtension(MCPCommand command, MCPResult result)"
        )
        self.assertIn(ORDER, dispatch)
        self.assertIn("DispatchAIGroupOrder(command, result)", dispatch)
        self.assertLessEqual(len(self._commands()), loopback.POLL_CAPS_MAX_NAMES)
        self.assertNotIn(ORDER, loopback.LIVE_ALLOWED_COMMANDS)
        self.assertEqual(
            loopback.live_profile_refusal(ORDER, enabled=True, admin_enabled=True),
            loopback.LIVE_PROFILE_REFUSED,
        )
        self.assertEqual(loopback.peer_for_command(ORDER), "server")


# --- E5: translated Enforce bodies ---------------------------------------

HALT, LOOP_B, ONCE_B, ROAMING_B = 0, 1, 3, 7
STATIC, WALK, JOG, SPRINT = 0, 1, 2, 3


class _FakeTypename:
    def __init__(self, name: str) -> None:
        self._cls = "eAIFormation" + name

    def ToString(self) -> _WireString:
        return _s(self._cls)


class _FakeFormation:
    def __init__(self, name: object) -> None:
        self._name = name.data.decode() if isinstance(name, EnforceString) else str(name)
        self.scale = 1.0
        self.looseness = 0.0

    def GetName(self) -> _WireString:
        return _s(self._name)

    def GetScale(self) -> float:
        return self.scale

    def SetScale(self, value: float) -> None:
        self.scale = value

    def GetLooseness(self) -> float:
        return self.looseness

    def SetLooseness(self, value: float) -> None:
        self.looseness = value


class _FakeMember:
    def __init__(self, normal: int = JOG, threat: int = SPRINT) -> None:
        self.limits = (normal, threat)
        self.calls: list[tuple[int, int]] = []
        self.alive = True

    def IsAlive(self) -> bool:
        return self.alive

    def SetMovementSpeedLimits(self, normal: int, threat: int) -> None:
        self.calls.append((normal, threat))
        self.limits = (normal, threat)

    def GetMovementSpeedLimit(self) -> int:
        return self.limits[0]

    def GetMovementSpeedLimitUnderThreat(self) -> int:
        return self.limits[1]


class _FakeGroup:
    def __init__(self) -> None:
        self.formation_state = 1
        self.waypoint_behaviour = LOOP_B
        self.formation = _FakeFormation("Vee")
        self.waypoints = EnforceArray()
        self.current_waypoint = None
        self.in_combat = False
        self.m_CurrentWaypointIndex = 0
        self.m_BackTracking = True
        self.set_formation_calls: list[object] = []

    def SetFormationState(self, state: int) -> None:
        self.formation_state = state

    def GetFormationState(self) -> int:
        return self.formation_state

    def SetWaypointBehaviour(self, behaviour: int) -> None:
        self.waypoint_behaviour = behaviour

    def GetWaypointBehaviour(self) -> int:
        return self.waypoint_behaviour

    def SetFormation(self, formation: object) -> None:
        self.set_formation_calls.append(formation)
        self.formation = formation

    def GetFormation(self) -> object:
        return self.formation

    def ClearWaypoints(self) -> None:
        self.waypoints.Clear()
        self.m_CurrentWaypointIndex = 0

    def AddWaypoint(self, point: object) -> None:
        self.waypoints.Insert(point)

    def GetWaypoints(self) -> EnforceArray:
        return self.waypoints

    def SetCurrentWaypoint(self, point: object) -> None:
        self.current_waypoint = point

    def IsInCombat(self) -> bool:
        return self.in_combat


def _report() -> SimpleNamespace:
    return SimpleNamespace(
        created=False,
        group_id=0,
        faction=_s(""),
        formation=_s(""),
        behaviour=_s(""),
        behaviour_expansion=_s(""),
        field=_s(""),
        allowed=EnforceArray(),
        args_read=None,
        formation_state=_s(""),
        waypoint_count=0,
        waypoint_index=0,
        in_combat=False,
        combat_may_override=False,
        roams_expansion_locations=False,
        release=_s(""),
        confirmed=False,
        released=0,
        remaining_ids=EnforceArray(),
        members=EnforceArray(),
    )


def _member_report() -> SimpleNamespace:
    return SimpleNamespace(
        object_id=0,
        type=_s(""),
        pos=EnforceArray(),
        loadout_applied=False,
        inventory=EnforceArray(),
        alive=False,
        speed_limit=0,
        speed_limit_threat=0,
    )


def _order_namespace(source: str, formations: list[str] | None = None) -> dict:
    registered = FORMATIONS if formations is None else formations
    namespace: dict[str, object] = {
        "_S": _WireString,
        "m_AIGroups": None,
        "AI_WAYPOINTS_MAX": 32,
        "Vector": lambda x, y, z: EnforceVector((x, y, z)),
        "eAIFormation": SimpleNamespace(
            Create=lambda name: _FakeFormation(name),
            s_FormationTypes=EnforceArray([_FakeTypename(n) for n in registered]),
        ),
        "eAIGroupFormationState": SimpleNamespace(NONE=0, IN=1, FLANK=2),
        "eAIWaypointBehavior": SimpleNamespace(
            HALT=HALT, LOOP=LOOP_B, ALTERNATE=2, ONCE=ONCE_B, ROAMING=ROAMING_B
        ),
        "eAIMovementSpeed": SimpleNamespace(
            STATIC=STATIC, WALK=WALK, JOG=JOG, SPRINT=SPRINT
        ),
        "IsFiniteFloat": lambda value: isinstance(value, (int, float)) and math.isfinite(float(value)),
        "typename": SimpleNamespace(
            EnumToString=lambda enum_type, value: _s("ALTERNATE")
        ),
        "MCPAIGroup": _report,
        "MCPAIGroupMember": _member_report,
    }
    class_types = (
        "MCPAIGroupRecord", "MCPAIGroupMemberRecord", "MCPAIGroup", "MCPAIGroupMember",
        "MCPArgs", "eAIFormation", "eAIWaypointBehavior", "eAIGroupFormationState", "eAIGroup",
    )
    for signature, members in (
        ("protected void EnsureAIRegistry()", ("m_AIGroups",)),
        ("protected MCPAIGroupRecord FindAIGroupById(int groupId)", ()),
        ("protected string AIFormationName(int index)", ()),
        ("protected bool AIFormationKnown(string formation)", ()),
        ("protected void AIFillFormationAllowed(MCPAIGroup report)", ()),
        ("protected void AIFillBehaviourAllowed(MCPAIGroup report)", ()),
        ("protected bool AIIsBehaviour(string value)", ()),
        ("protected bool AIWaypointsOk(array<float> waypoints, int minPoints)", ()),
        ("protected void AIOrderSaveSpeed(MCPAIGroupRecord record, int speedValue)", ()),
        ("protected void AIGroupApplyRoute(MCPAIGroupRecord record)", ()),
        ("protected void AIGroupApplyMode(MCPAIGroupRecord record)", ()),
        ("protected void AIGroupApplyLimits(MCPAIGroupRecord record)", ()),
        ("protected void AIFillBehaviour(eAIGroup group, MCPAIGroup report)", ()),
        ("protected void AIFillGroupOrderFields(MCPAIGroupRecord record, MCPAIGroup report)", ()),
        ("protected void DispatchAIGroupOrder(MCPCommand command, MCPResult result)", ()),
    ):
        translate(source, signature, namespace, members=members, class_types=class_types)
    return namespace


def _member_record(object_id: int, member: _FakeMember) -> SimpleNamespace:
    return SimpleNamespace(
        object_id=object_id,
        member=member,
        speed_normal=JOG,
        speed_threat=SPRINT,
        last_pos=EnforceArray(),
        last_health=1.0,
        last_alive=True,
    )


def _record(namespace: dict, *, behaviour: str = "LOOP", group_state: str = "active"):
    group = _FakeGroup()
    members = [_FakeMember(), _FakeMember()]
    record = SimpleNamespace(
        spawn_token=_s("a" * 32),
        group_id=GROUP_ID,
        group=group,
        expansion_id=7,
        scene_id=_s(""),
        behaviour=_s(behaviour),
        group_state=_s(group_state),
        waypoints=EnforceArray(),
        members=EnforceArray(
            [_member_record(11, members[0]), _member_record(12, members[1])]
        ),
    )
    namespace["m_AIGroups"] = EnforceArray([record])
    return record, group, members


def _command(**overrides: object) -> SimpleNamespace:
    args = SimpleNamespace(
        group_id=GROUP_ID,
        behaviour=_s(""),
        formation=_s(""),
        waypoints=None,
        speed=0.0,
        speed_set=False,
    )
    for name, value in overrides.items():
        setattr(args, name, value)
    return SimpleNamespace(cmd=_s(ORDER), args=args)


def _dispatch(namespace: dict, command: SimpleNamespace) -> SimpleNamespace:
    result = SimpleNamespace(ai_group=None, ok=False, error=_s(""))
    namespace["DispatchAIGroupOrder"](command, result)
    return result


class ExpansionOrderEnforceTest(unittest.TestCase):
    """E5: HOLD, speed preferences and all-or-nothing validation."""

    def setUp(self) -> None:
        self.source = ADAPTER.read_text(encoding="utf-8")
        self.namespace = _order_namespace(self.source)

    def test_e5_hold_then_loop_restores_the_saved_limits(self) -> None:
        # Mutants killed: HOLD leaving the formation IN (the leader walks
        # on), HOLD writing only one limit, and LOOP re-reading the limit
        # the formation following raised instead of the saved preference.
        record, group, members = _record(self.namespace)
        result = _dispatch(self.namespace, _command(behaviour=_s("HOLD")))
        self.assertTrue(result.ok, result.error)
        self.assertEqual((group.formation_state, group.waypoint_behaviour), (0, HALT))
        for member in members:
            self.assertEqual(member.calls, [(STATIC, SPRINT)])
        report = result.ai_group
        self.assertEqual(report.behaviour.text, "HOLD")
        self.assertEqual(report.behaviour_expansion.text, "HALT")
        self.assertEqual(report.formation_state.text, "NONE")
        self.assertEqual(report.members.Get(0).speed_limit, STATIC)
        self.assertEqual(report.members.Get(0).speed_limit_threat, SPRINT)

        # The formation following raises the normal limit for a while.
        for member in members:
            member.limits = (SPRINT, SPRINT)
            member.calls.clear()
        result = _dispatch(self.namespace, _command(behaviour=_s("LOOP")))
        self.assertTrue(result.ok, result.error)
        for member in members:
            self.assertEqual(member.calls, [(JOG, SPRINT)])
        self.assertEqual(result.ai_group.behaviour.text, "LOOP")
        self.assertEqual(result.ai_group.formation_state.text, "IN")

    def test_e5_two_holds_then_loop_and_speed_saved_during_hold(self) -> None:
        # Mutants killed: a repeated HOLD re-applying (STATIC, threat), and
        # a speed ordered in a HOLD applying before the group leaves it.
        record, group, members = _record(self.namespace)
        _dispatch(self.namespace, _command(behaviour=_s("HOLD")))
        _dispatch(self.namespace, _command(behaviour=_s("HOLD")))
        for member in members:
            self.assertEqual(member.calls, [(STATIC, SPRINT)])

        result = _dispatch(
            self.namespace, _command(speed=3.0, speed_set=True)
        )
        self.assertTrue(result.ok, result.error)
        for member in members:
            self.assertEqual(member.calls, [(STATIC, SPRINT)])
            self.assertEqual(member.limits, (STATIC, SPRINT))
        self.assertEqual(record.members.Get(0).speed_normal, SPRINT)

        _dispatch(self.namespace, _command(behaviour=_s("LOOP")))
        for member in members:
            self.assertEqual(member.calls, [(STATIC, SPRINT), (SPRINT, SPRINT)])

    def test_e5_speed_outside_a_hold_applies_at_once(self) -> None:
        # Mutant killed: speed only ever saved, never applied outside HOLD.
        record, group, members = _record(self.namespace)
        result = _dispatch(self.namespace, _command(speed=1.0, speed_set=True))
        self.assertTrue(result.ok, result.error)
        for member in members:
            self.assertEqual(member.calls, [(WALK, SPRINT)])

    def test_e5_bad_formation_changes_nothing_not_even_other_fields(self) -> None:
        # Mutant killed: applying the fields before validating them all, so
        # a bad formation still moves the group, its speed or its route.
        record, group, members = _record(self.namespace)
        group.waypoints.Insert(EnforceVector((9.0, 9.0, 9.0)))
        result = _dispatch(
            self.namespace,
            _command(
                behaviour=_s("HOLD"),
                formation=_s("Nope"),
                waypoints=EnforceArray([1.0, 2.0, 3.0]),
                speed=3.0,
                speed_set=True,
            ),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error.text, "bad_args")
        self.assertEqual(result.ai_group.field.text, "formation")
        self.assertEqual(
            [item.text for item in result.ai_group.allowed], FORMATIONS
        )
        self.assertEqual((group.formation_state, group.waypoint_behaviour), (1, LOOP_B))
        self.assertEqual(group.waypoints.Count(), 1)
        self.assertEqual(record.behaviour.text, "LOOP")
        self.assertEqual(record.members.Get(0).speed_normal, JOG)
        for member in members:
            self.assertEqual(member.calls, [])
        self.assertEqual(group.set_formation_calls, [])

    def test_e5_bad_behaviour_and_bad_waypoints_change_nothing(self) -> None:
        # Mutants killed: the bridge accepting "hold" or a flat list that is
        # not a whole number of triples.
        record, group, members = _record(self.namespace)
        result = _dispatch(self.namespace, _command(behaviour=_s("hold")))
        self.assertEqual(result.error.text, "bad_args")
        self.assertEqual(result.ai_group.field.text, "behaviour")
        self.assertEqual(
            [item.text for item in result.ai_group.allowed],
            ["HOLD", "ONCE", "LOOP", "ROAM"],
        )
        result = _dispatch(
            self.namespace, _command(waypoints=EnforceArray([1.0, 2.0, 3.0, 4.0]))
        )
        self.assertEqual(result.error.text, "bad_args")
        self.assertEqual(result.ai_group.field.text, "waypoints")
        self.assertEqual((group.formation_state, group.waypoint_behaviour), (1, LOOP_B))
        for member in members:
            self.assertEqual(member.calls, [])

    def test_e5_not_found_for_unknown_and_releasing_groups(self) -> None:
        # Mutant killed: ordering a releasing group (its members are being
        # deleted) or an unknown id changing something.
        record, group, members = _record(self.namespace)
        result = _dispatch(
            self.namespace, _command(group_id=GROUP_ID + 9, behaviour=_s("HOLD"))
        )
        self.assertEqual(result.error.text, "not_found")
        record.group_state = _s("releasing")
        result = _dispatch(self.namespace, _command(behaviour=_s("HOLD")))
        self.assertEqual(result.error.text, "not_found")
        self.assertEqual((group.formation_state, group.waypoint_behaviour), (1, LOOP_B))
        for member in members:
            self.assertEqual(member.calls, [])

    def test_e5_waypoints_replace_route_and_combat_is_reported(self) -> None:
        # Mutants killed: ClearWaypoints leaving the backtracking flag or
        # the old destination, and a reply read before the change.
        record, group, members = _record(self.namespace)
        group.in_combat = True
        result = _dispatch(
            self.namespace,
            _command(
                waypoints=EnforceArray([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
                formation=_s("Wall"),
            ),
        )
        self.assertTrue(result.ok, result.error)
        self.assertFalse(group.m_BackTracking)
        self.assertEqual(group.waypoints.Count(), 2)
        self.assertEqual(list(group.current_waypoint), [1.0, 2.0, 3.0])
        self.assertEqual(group.formation.GetName().text, "Wall")
        report = result.ai_group
        self.assertEqual(report.waypoint_count, 2)
        self.assertEqual(report.waypoint_index, 0)
        self.assertTrue(report.in_combat)
        self.assertTrue(report.combat_may_override)
        self.assertEqual(report.group_id, GROUP_ID)
        self.assertEqual(list(record.waypoints), [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])

    def test_e5_bad_speed_changes_nothing(self) -> None:
        # Mutant killed (M11): the bridge not validating speed, so an
        # out-of-range value would be saved as STATIC and stop the group.
        record, group, members = _record(self.namespace)
        for speed in (0.0, 4.0, -1.0):
            result = _dispatch(self.namespace, _command(speed=speed, speed_set=True))
            self.assertEqual(result.error.text, "bad_args")
            self.assertEqual(result.ai_group.field.text, "speed")
        self.assertEqual(record.members.Get(0).speed_normal, JOG)
        for member in members:
            self.assertEqual(member.calls, [])

    def test_e5_empty_waypoints_and_random_formation_change_nothing(self) -> None:
        # Mutants killed: an empty route clearing the waypoints and
        # keeping the old destination, and RANDOM resolving to a sane
        # formation instead of being refused.
        record, group, members = _record(self.namespace)
        result = _dispatch(self.namespace, _command(waypoints=EnforceArray()))
        self.assertEqual(result.error.text, "bad_args")
        self.assertEqual(result.ai_group.field.text, "waypoints")
        result = _dispatch(self.namespace, _command(formation=_s("RANDOM")))
        self.assertEqual(result.error.text, "bad_args")
        self.assertEqual(result.ai_group.field.text, "formation")
        self.assertEqual(group.set_formation_calls, [])
        for member in members:
            self.assertEqual(member.calls, [])

    def test_e5_registered_extra_formation_is_accepted(self) -> None:
        # Mutant killed: a fixed list of 11 names rejecting a formation
        # a newer Expansion build registers (Diamond).
        namespace = _order_namespace(self.source, formations=FORMATIONS + ["Diamond"])
        record, group, members = _record(namespace)
        result = _dispatch(namespace, _command(formation=_s("Diamond")))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(group.formation.GetName().text, "Diamond")
        result = _dispatch(namespace, _command(formation=_s("Nope")))
        self.assertEqual(result.error.text, "bad_args")
        self.assertIn("Diamond", [item.text for item in result.ai_group.allowed])

    def test_e5_not_found_carries_no_default_report(self) -> None:
        # Mutant killed: a not_found reply carrying a default ai_group
        # whose zeroes read as a real state.
        record, group, members = _record(self.namespace)
        result = _dispatch(
            self.namespace, _command(group_id=GROUP_ID + 9, behaviour=_s("HOLD"))
        )
        self.assertEqual(result.error.text, "not_found")
        self.assertIsNone(result.ai_group)

    def test_e5_roam_marks_expansion_locations_and_formation_keeps_scale(self) -> None:
        # Mutants killed: ROAM not saying it roams Expansion locations,
        # and a formation change resetting scale and looseness.
        record, group, members = _record(self.namespace)
        group.formation.scale = 2.5
        group.formation.looseness = 0.75
        result = _dispatch(
            self.namespace, _command(behaviour=_s("ROAM"), formation=_s("Wall"))
        )
        self.assertTrue(result.ok, result.error)
        self.assertTrue(result.ai_group.roams_expansion_locations)
        self.assertEqual(group.formation.scale, 2.5)
        self.assertEqual(group.formation.looseness, 0.75)


    def test_e5_each_behaviour_maps_to_its_expansion_value(self) -> None:
        # Mutant killed (E-i): ONCE applied or reported as another value.
        for behaviour, value, name in (
            ("HOLD", HALT, "HALT"), ("ONCE", ONCE_B, "ONCE"),
            ("LOOP", LOOP_B, "LOOP"), ("ROAM", ROAMING_B, "ROAMING"),
        ):
            with self.subTest(behaviour=behaviour):
                namespace = _order_namespace(self.source)
                record, group, members = _record(namespace, behaviour="ONCE" if behaviour != "ONCE" else "LOOP")
                result = _dispatch(namespace, _command(behaviour=_s(behaviour)))
                self.assertTrue(result.ok, result.error)
                self.assertEqual(group.waypoint_behaviour, value)
                self.assertEqual(result.ai_group.behaviour.text, behaviour)
                self.assertEqual(result.ai_group.behaviour_expansion.text, name)

    def test_e5_more_than_32_waypoints_change_nothing(self) -> None:
        # Mutant killed (E-j): the bridge accepting a 33rd point.
        record, group, members = _record(self.namespace)
        result = _dispatch(self.namespace, _command(waypoints=EnforceArray([0.0] * 99)))
        self.assertEqual(result.error.text, "bad_args")
        self.assertEqual(result.ai_group.field.text, "waypoints")
        self.assertEqual(group.waypoints.Count(), 0)
        self.assertEqual(list(record.waypoints), [])

    def test_e5_non_finite_waypoints_are_bad_args(self) -> None:
        # Mutant killed: the order bridge skipping the IsFiniteFloat check
        # the spawn bridge has (B-r2-H1 divergence).
        record, group, members = _record(self.namespace)
        result = _dispatch(self.namespace, _command(waypoints=EnforceArray([float("nan"), 0.0, 0.0])))
        self.assertEqual(result.error.text, "bad_args")
        self.assertEqual(result.ai_group.field.text, "waypoints")
        self.assertEqual(group.waypoints.Count(), 0)

    def test_e5_flank_is_read_back(self) -> None:
        # Mutant killed (E-g): a FLANK the player ordered read back as "".
        record, group, members = _record(self.namespace)
        group.formation_state = 2
        result = _dispatch(self.namespace, _command(speed=2.0, speed_set=True))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.ai_group.formation_state.text, "FLANK")

    def test_e5_member_without_entity_still_gets_a_row(self) -> None:
        # Mutant killed: a deleted member vanishing from the reply, so a
        # row count reads as a member count (B-r2-H3).
        record, group, members = _record(self.namespace)
        record.members.Get(1).member = None
        result = _dispatch(self.namespace, _command(speed=2.0, speed_set=True))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.ai_group.members.Count(), 2)
        gone = result.ai_group.members.Get(1)
        self.assertEqual(gone.object_id, 12)
        self.assertFalse(gone.alive)
        self.assertEqual((gone.speed_limit, gone.speed_limit_threat), (0, 0))
        self.assertTrue(result.ai_group.members.Get(0).alive)

class ExpansionOrderMutantTest(unittest.TestCase):
    """R26: the two named E5 mutants, on a mutated copy of the source."""

    def setUp(self) -> None:
        self.source = ADAPTER.read_text(encoding="utf-8")

    def _hold_then_loop_calls(self, source: str) -> list[tuple[int, int]]:
        namespace = _order_namespace(source)
        _record(namespace)
        _dispatch(namespace, _command(behaviour=_s("HOLD")))
        record = namespace["m_AIGroups"].Get(0)
        member = record.members.Get(0).member
        member.limits = (SPRINT, SPRINT)
        member.calls.clear()
        _dispatch(namespace, _command(behaviour=_s("LOOP")))
        return member.calls

    def test_mutant_not_restoring_the_limit_after_a_hold_is_killed(self) -> None:
        # Mutant: LOOP re-reads the (raised) current limit instead of the
        # saved preference. The real source restores (JOG, SPRINT).
        mutant = self.source.replace(
            "memberRecord.member.SetMovementSpeedLimits(memberRecord.speed_normal, memberRecord.speed_threat);",
            "memberRecord.member.SetMovementSpeedLimits(memberRecord.member.GetMovementSpeedLimit(), memberRecord.speed_threat);",
        )
        self.assertNotEqual(mutant, self.source)
        self.assertEqual(self._hold_then_loop_calls(self.source), [(JOG, SPRINT)])
        self.assertEqual(self._hold_then_loop_calls(mutant), [(SPRINT, SPRINT)])

    def test_mutant_a_hold_stomping_the_threat_limit_is_killed(self) -> None:
        # Mutant: HOLD writes the normal preference as the threat limit.
        # The real source keeps (STATIC, SPRINT).
        mutant = self.source.replace(
            "memberRecord.member.SetMovementSpeedLimits(eAIMovementSpeed.STATIC, memberRecord.speed_threat);",
            "memberRecord.member.SetMovementSpeedLimits(eAIMovementSpeed.STATIC, memberRecord.speed_normal);",
        )
        self.assertNotEqual(mutant, self.source)
        namespace = _order_namespace(mutant)
        _record(namespace)
        _dispatch(namespace, _command(behaviour=_s("HOLD")))
        record = namespace["m_AIGroups"].Get(0)
        self.assertEqual(record.members.Get(0).member.calls, [(STATIC, JOG)])


if __name__ == "__main__":
    unittest.main()

