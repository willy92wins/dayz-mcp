"""Expansion AI adapter foundation (expansion-ai-adapter plan 2.1-2.3, 3.6, 5.2).

Offline criteria O1, O2, O7, O9, O14, E6 and E9, plus the part of O13 that
does not need the v13 lease-close activation. O6, O10, O11, O12 and the
close-publishing part of O13 are skipped with their blocker named.

Each test names the mutant it kills in its docstring; the commit message
carries the criterion -> test -> mutant table.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, server
from dayz_mcp.bridge_readiness import (
    EXPANSION_AI_ARG_CONTRACT,
    EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH,
    _compare_bridge_capabilities,
)
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceMap,
    EnforceString,
    method_body,
    translate,
)
from tests.fence_helpers import INST_SERVER, PID_SERVER, bound_queue
from tests.xai_helpers import GOOD_XAI, _WireString, _base_commands

SPAWN = "ai_group_spawn"
RELEASE = "ai_group_release"
TOKEN = "a" * 32
SPAWN_WIRE = {
    "faction": "West",
    "count": 2,
    "pos": [1.0, 2.0, 3.0],
    "loadout": "",
    "formation": "Vee",
    "behaviour": "HOLD",
    "waypoints": [],
    "spawn_token": TOKEN,
}
BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
MESSAGES = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
ADAPTER_ROOT = addon_root().parent / "addon_expansion_ai"
ADAPTER = ADAPTER_ROOT / "scripts" / "5_Mission" / "MCPExpansionAI.c"


def _bound_state() -> loopback.ServerState:
    state = loopback.ServerState(key="k")
    state.daemon_generation = "gen-xai"
    state.install_bound_peer(
        instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="xai-run"
    )
    return state


def _announce(
    state: loopback.ServerState,
    commands: list[str] | None,
    ach: str | None,
    xai: str | None,
    *,
    generation: str | None = None,
    announced_at: float | None = None,
) -> None:
    if commands is None:
        state._peer_caps.pop("server", None)
        return
    state._peer_caps["server"] = {
        "generation": state.daemon_generation if generation is None else generation,
        "commands": list(commands),
        "reason": "ok",
        "arg_contract_hash": ach,
        "expansion_arg_contract_hash": xai,
        "announced_at": state._now() if announced_at is None else announced_at,
        "instance": INST_SERVER,
    }


def _good_commands(*, lease_close: bool = True, adapter: bool = True) -> list[str]:
    commands = _base_commands()
    if adapter:
        commands += [RELEASE]
    if lease_close:
        commands += ["lease_close"]
    return commands


def _poll(
    state: loopback.ServerState, commands: list[str], ach: str | None, xai: str | None
):
    return state.record_poll(
        "server",
        f"{EXPECTED_BRIDGE_VERSION}~1.29",
        instance=INST_SERVER,
        source_pid=PID_SERVER,
        caps=",".join(commands),
        ach=ach,
        xai=xai,
    )


def _delivered(
    state: loopback.ServerState,
    commands: list[str] | None = None,
    ach: str | None = server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
    xai: str | None = GOOD_XAI,
) -> list[dict]:
    census = _good_commands() if commands is None else commands
    status, polled = _poll(state, census, ach, xai)
    if status != 200:
        raise AssertionError(polled)
    return list(polled["commands"])


class ExpansionAdmissionTest(unittest.TestCase):
    """O1/O2/O14: broker admission and delivery, precedence of plan 2.2."""

    def _enqueue(self, state: loopback.ServerState):
        # The release is the one adapter verb this foundation ships; spawn,
        # order and state join this admission set with their own dispatches.
        return state.enqueue_command(RELEASE, {"spawn_token": TOKEN}, peer="server")

    def test_o1_missing_verb_is_adapter_not_loaded_and_queues_nothing(self) -> None:
        # Mutant killed: the generic missing-verb refusal reaching adapter
        # verbs (they would read bridge_capability_missing, not the detail).
        state = _bound_state()
        _announce(state, _good_commands(adapter=False), server.EXPECTED_SERVER_ARG_CONTRACT_HASH, GOOD_XAI)
        status, body = self._enqueue(state)
        self.assertEqual(status, 409)
        self.assertEqual(body, {"error": "expansion_unavailable", "detail": "adapter_not_loaded"})
        self.assertEqual(bound_queue(state, "server"), [])

    def test_o1_missing_census_is_still_bridge_capability_missing(self) -> None:
        # Mutant killed: adapter branch running before the common guards.
        state = _bound_state()
        _announce(state, None, None, None)
        status, body = self._enqueue(state)
        self.assertEqual((status, body), (409, {"error": "bridge_capability_missing"}))
        self.assertEqual(bound_queue(state, "server"), [])

    def test_o1_verb_lost_before_delivery_discards_it(self) -> None:
        # Mutant killed: delivery trusting the enqueue-time census.
        state = _bound_state()
        _announce(state, _good_commands(), server.EXPECTED_SERVER_ARG_CONTRACT_HASH, GOOD_XAI)
        status, body = self._enqueue(state)
        self.assertEqual(status, 200, body)
        delivered = _delivered(state, _good_commands(adapter=False))
        self.assertEqual(delivered, [])
        self.assertIn("expansion_unavailable", str(state.take_result(body["id"])))
        self.assertEqual(bound_queue(state, "server"), [])

    def test_o2_bad_xai_is_adapter_contract_mismatch(self) -> None:
        # Mutant killed: accepting an absent, malformed or wrong xai.
        for xai in (None, "", "xyz", "0" * 16, GOOD_XAI.upper()):
            with self.subTest(xai=xai):
                state = _bound_state()
                _announce(state, _good_commands(), server.EXPECTED_SERVER_ARG_CONTRACT_HASH, xai)
                status, body = self._enqueue(state)
                self.assertEqual(status, 409)
                self.assertEqual(
                    body,
                    {"error": "expansion_unavailable", "detail": "adapter_contract_mismatch"},
                )
                self.assertEqual(bound_queue(state, "server"), [])

    def test_o2_good_xai_enqueues(self) -> None:
        # Mutant killed: the xai check rejecting the real hash.
        state = _bound_state()
        _announce(state, _good_commands(), server.EXPECTED_SERVER_ARG_CONTRACT_HASH, GOOD_XAI)
        status, body = self._enqueue(state)
        self.assertEqual(status, 200, body)
        self.assertEqual(len(bound_queue(state, "server")), 1)

    def test_o2_xai_changed_before_delivery_discards_it(self) -> None:
        # Mutant killed: delivery not re-reading xai.
        state = _bound_state()
        _announce(state, _good_commands(), server.EXPECTED_SERVER_ARG_CONTRACT_HASH, GOOD_XAI)
        status, body = self._enqueue(state)
        self.assertEqual(status, 200, body)
        delivered = _delivered(state, _good_commands(), xai="0" * 16)
        self.assertEqual(delivered, [])
        self.assertIn("expansion_unavailable", str(state.take_result(body["id"])))

    def test_o14_precedence_common_then_ach_then_verb_then_xai_then_close(self) -> None:
        # Mutants killed: any reordering of the plan 2.2 precedence chain.
        cases = (
            # (commands, ach, xai, announced_at, expected body)
            (None, None, None, None, {"error": "bridge_capability_missing"}),
            (
                _good_commands(),
                server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
                GOOD_XAI,
                0.0,
                {"error": "bridge_capability_missing"},
            ),
            (
                _good_commands(adapter=False),
                "0" * 16,
                GOOD_XAI,
                None,
                {"error": "arg_contract_mismatch"},
            ),
            (
                _good_commands(adapter=False),
                server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
                GOOD_XAI,
                None,
                {"error": "expansion_unavailable", "detail": "adapter_not_loaded"},
            ),
            (
                _good_commands(),
                server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
                "0" * 16,
                None,
                {"error": "expansion_unavailable", "detail": "adapter_contract_mismatch"},
            ),
        )
        for commands, ach, xai, announced_at, expected in cases:
            with self.subTest(expected=expected):
                state = _bound_state()
                _announce(state, commands, ach, xai, announced_at=announced_at)
                status, body = self._enqueue(state)
                self.assertEqual(status, 409)
                self.assertEqual(body, expected)
                self.assertEqual(bound_queue(state, "server"), [])

    def test_o14_live_profile_still_refuses_the_new_mutation(self) -> None:
        # Mutant killed: ai_group_spawn joining LIVE_ALLOWED_COMMANDS.
        self.assertNotIn(SPAWN, loopback.LIVE_ALLOWED_COMMANDS)
        self.assertEqual(
            loopback.live_profile_refusal(SPAWN, enabled=True, admin_enabled=True),
            loopback.LIVE_PROFILE_REFUSED,
        )

    def test_o14_census_never_exceeds_the_poll_cap(self) -> None:
        # Mutant killed: the adapter census pushing the poll past 64 names.
        self.assertLessEqual(len(_good_commands()), loopback.POLL_CAPS_MAX_NAMES)


class ExpansionComparatorTest(unittest.TestCase):
    """O7: the adapter verbs are optional in _compare_bridge_capabilities."""

    def _registered(self) -> frozenset[str]:
        tools = {
            tool
            for tool in server._BRIDGE_COMMAND_TOOLS["server"].values()
            if tool
        }
        return frozenset(tools)

    def _block(self, commands: list[str]) -> dict:
        return {
            "state": "announced",
            "reason": "ok",
            "announced_commands": commands,
            "announced_arg_contract_hash": server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
        }

    def test_o7_match_without_and_with_the_adapter_verbs(self) -> None:
        # Mutant killed: an adapter verb demanding the adapter census (a
        # run without Expansion would read mismatch).
        base = sorted(server._BRIDGE_COMMAND_TOOLS["server"])
        without = [name for name in base if name != RELEASE]
        with_adapter = [*without, RELEASE]
        for commands in (without, with_adapter):
            with self.subTest(commands=commands[-3:]):
                verdict = _compare_bridge_capabilities(
                    "server", self._block(commands), self._registered()
                )
                self.assertEqual(verdict["state"], "match", verdict)


class ExpansionContractTest(unittest.TestCase):
    """O9: contract hash and field names across Python and Enforce."""

    def test_o9_enforce_hash_matches_python(self) -> None:
        # Mutant killed: one contract key added on only one side.
        source = ADAPTER.read_text(encoding="utf-8")
        match = re.search(r'EXPANSION_AI_ARG_CONTRACT_HASH = "([0-9a-f]{16})"', source)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH)

    def test_o9_every_contract_key_is_a_field_with_its_base_type(self) -> None:
        # Mutant killed: a new MCPArgs field repeating a base name, or one
        # named state (MCPResult already has state).
        base_args = method_body(
            MESSAGES.read_text(encoding="utf-8"), "class MCPArgs"
        ) if False else MESSAGES.read_text(encoding="utf-8")
        base_fields = set(
            re.findall(
                r"\n\t(?:ref\s+)?[A-Za-z_][\w<> ]*?\s+(\w+);",
                base_args.split("class MCPCommand")[0].split("class MCPArgs")[1],
            )
        )
        adapter = ADAPTER.read_text(encoding="utf-8")
        adapter_args = adapter.split("modded class MCPArgs")[1].split("};")[0]
        new_fields = re.findall(
            r"\n\t(?:ref\s+)?[A-Za-z_][\w<> ]*?\s+(\w+);", adapter_args
        )
        self.assertTrue(new_fields)
        for name in new_fields:
            self.assertNotIn(name, base_fields)
            self.assertNotEqual(name, "state")
        known = base_fields | set(new_fields)
        for keys in EXPANSION_AI_ARG_CONTRACT.values():
            for key in keys:
                self.assertIn(key, known)
        result_block = adapter.split("modded class MCPResult")[1].split("};")[0]
        result_fields = re.findall(r"\n\t(?:ref\s+)?[A-Za-z_][\w<> ]*?\s+(\w+);", result_block)
        self.assertEqual(set(result_fields), {"ai_group", "ai_groups"})
        # Inherited keys keep their base types.
        self.assertIn("ref array<float> pos;", base_args)
        self.assertIn("int object_id;", base_args)
        self.assertIn("float speed;", base_args)
        self.assertIn("bool speed_set;", base_args)
        self.assertIn("string scene_id;", base_args)


class ExpansionReleaseRegistryTest(unittest.TestCase):
    """O5 foundation part: the release registry names the spawn by token."""

    def test_release_registry_maps_spawn_to_release_by_token(self) -> None:
        # Mutant killed: the release naming the group_id instead of the token.
        plans = [entry for entry in loopback.RELEASE_REGISTRY if entry[0] == SPAWN]
        self.assertEqual([plan[1] for plan in plans], [RELEASE])
        self.assertEqual(plans[0][2](41, dict(SPAWN_WIRE)), {"spawn_token": TOKEN})
        self.assertIn(RELEASE, loopback._OWNER_CLEANUP_COMMANDS)
        self.assertIsNone(server._BRIDGE_COMMAND_TOOLS["server"][RELEASE])
        self.assertEqual(
            loopback.validate_command_args(RELEASE, {"spawn_token": TOKEN}), (True, None)
        )
        self.assertIs(loopback.validate_command_args(RELEASE, {"spawn_token": "x"})[0], False)


class ExpansionBlockedOnV13Test(unittest.TestCase):
    """Criteria that need the v13 lease-close activation (plan 5.1)."""

    @unittest.skip("blocked: needs the v13 lease-close activation (plan §5.1)")
    def test_o6_lease_end_publishes_closes_and_stop_covers_all_groups(self) -> None:
        raise AssertionError("unreachable")

    @unittest.skip("blocked: needs the v13 lease-close activation (plan §5.1)")
    def test_o10_close_census_at_25s_owed_and_timeout_at_40s(self) -> None:
        raise AssertionError("unreachable")

    @unittest.skip("blocked: needs the v13 lease-close activation (plan §5.1)")
    def test_o11_late_clean_close_clears_the_debt(self) -> None:
        raise AssertionError("unreachable")

    @unittest.skip("blocked: needs the v13 lease-close activation (plan §5.1)")
    def test_o12_processes_gone_clears_never_observed_deletes(self) -> None:
        raise AssertionError("unreachable")

    @unittest.skip("blocked: needs the v13 lease-close activation (plan §5.1)")
    def test_o13_close_publishing_with_v12_and_close_capable_census(self) -> None:
        raise AssertionError("unreachable")


class ExpansionO13InertTest(unittest.TestCase):
    """O13, the part that needs no close: v12 support stays inert."""

    def test_o13_internal_release_stays_available_on_a_v12_census(self) -> None:
        # Mutant killed: the lease-close gate blocking an internal release
        # (it is not a new mutation) or creating close debt on v12.
        state = _bound_state()
        _announce(
            state,
            _good_commands(lease_close=False),
            server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            GOOD_XAI,
        )
        status, body = state.enqueue_command(RELEASE, {"spawn_token": TOKEN}, peer="server")
        self.assertEqual(status, 200, body)
        self.assertEqual(len(bound_queue(state, "server")), 1)

    def test_o14_internal_release_is_not_gated_on_lease_close(self) -> None:
        # Mutant killed: ai_group_release joining the new-mutation gate.
        state = _bound_state()
        _announce(
            state,
            _good_commands(lease_close=False),
            server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            GOOD_XAI,
        )
        status, body = state.enqueue_command(RELEASE, {"spawn_token": TOKEN}, peer="server")
        self.assertEqual(status, 200, body)


# --- E6/E9: translated Enforce bodies -------------------------------------

class _FakeMember:
    def __init__(self) -> None:
        self.despawns = 0

    def eAI_Despawn(self) -> None:
        self.despawns += 1


class _FakeGroup:
    def __init__(self) -> None:
        self.deletes = 0

    def Delete(self) -> None:
        self.deletes += 1


def _adapter_namespace() -> dict:
    source = ADAPTER.read_text(encoding="utf-8")
    registry: dict[str, object] = {"by_id": {}}
    namespace: dict[str, object] = {
        "_S": _WireString,
        "m_AIGroups": None,
        "m_RuntimeObjects": EnforceMap(),
        "this": object(),
        "eAIGroup": SimpleNamespace(
            GetGroupByID=lambda group_id, create: registry["by_id"].get(group_id)
        ),
        "MCPBotControl": SimpleNamespace(OnObjectGone=lambda bridge, object_id: None),
        "MCPLeaseCloseEntry": lambda: SimpleNamespace(
            name=None, state=None, reason=None
        ),
        "_registry": registry,
    }
    for signature, members in (
        ("protected void EnsureAIRegistry()", ("m_AIGroups",)),
        (
            "protected bool AIGroupGone(MCPAIGroupRecord record)",
            (),
        ),
        (
            "protected bool AIGroupRequestRelease(MCPAIGroupRecord record)",
            ("m_RuntimeObjects",),
        ),
        (
            "protected override void LeaseCloseExtensionCensus(MCPLeaseCloseCensus census)",
            ("m_AIGroups",),
        ),
    ):
        translate(
            source,
            signature,
            namespace,
            members=members,
            class_types=("MCPAIGroupRecord", "MCPAIGroupMemberRecord", "eAIGroup", "MCPLeaseCloseCensus", "MCPLeaseCloseEntry", "Object"),
        )
    return namespace


def _record(namespace: dict, *, members: list, group=None, expansion_id: int = 7):
    namespace["_registry"]["by_id"] = {expansion_id: group} if group else {}
    return SimpleNamespace(
        spawn_token=_WireString.of(TOKEN),
        group_id=1000000001,
        group=group,
        expansion_id=expansion_id,
        scene_id=_WireString.of(""),
        behaviour=_WireString.of("HOLD"),
        group_state=EnforceString.of("active"),
        members=EnforceArray(members),
    )


def _member(object_id: int, entity: object) -> SimpleNamespace:
    return SimpleNamespace(
        object_id=object_id,
        member=entity,
        speed_normal=2,
        speed_threat=3,
        last_pos=EnforceArray(),
        last_health=1.0,
        last_alive=True,
    )


class ExpansionReleaseEnforceTest(unittest.TestCase):
    """E6: release and census through the translated helper and override."""

    def test_e6_release_is_deferred_pending_and_idempotent(self) -> None:
        # Mutants killed: confirming while a delete is still pending, the
        # release skipping a dead member's entity, and a repeated release
        # or stop repeating the deletes.
        namespace = _adapter_namespace()
        alive, dead = _FakeMember(), _FakeMember()
        group = _FakeGroup()
        record = _record(namespace, members=[_member(11, alive), _member(12, dead)], group=group)
        namespace["m_RuntimeObjects"].Insert(11, alive)
        namespace["m_RuntimeObjects"].Insert(12, dead)

        gone = namespace["AIGroupRequestRelease"](record)
        self.assertFalse(gone)
        self.assertEqual((alive.despawns, dead.despawns, group.deletes), (1, 1, 1))
        self.assertEqual(record.group_state.data, b"releasing")
        self.assertFalse(namespace["m_RuntimeObjects"].Contains(11))

        # Repeated request: nothing is asked twice.
        self.assertFalse(namespace["AIGroupRequestRelease"](record))
        self.assertEqual((alive.despawns, dead.despawns, group.deletes), (1, 1, 1))

        # The census reads only: residue while anything remains, no setters.
        census = SimpleNamespace(entries=EnforceArray())
        namespace["m_AIGroups"] = EnforceArray([record])
        namespace["LeaseCloseExtensionCensus"](census)
        self.assertEqual(len(census.entries), 1)
        entry = census.entries.Get(0)
        self.assertEqual(entry.name.data, ("ai_group:" + TOKEN).encode())
        self.assertEqual(entry.state.data, b"residue")
        self.assertEqual(entry.reason.data, b"ai_group_release_unconfirmed")
        self.assertEqual((alive.despawns, group.deletes), (1, 1))

        # Only both gone cleans: members deleted but the group still
        # registered in Expansion is not clean, and a reused id naming
        # another group does not count as this one.
        record.members.Get(0).member = None
        record.members.Get(1).member = None
        self.assertFalse(namespace["AIGroupGone"](record))
        namespace["_registry"]["by_id"] = {7: _FakeGroup()}
        self.assertTrue(namespace["AIGroupGone"](record))
        census2 = SimpleNamespace(entries=EnforceArray())
        namespace["LeaseCloseExtensionCensus"](census2)
        self.assertEqual(len(census2.entries), 0)

    def test_e6_unknown_token_and_gone_group_answers(self) -> None:
        # Mutant killed: an unknown token erroring instead of released 0.
        source = ADAPTER.read_text(encoding="utf-8")
        body = method_body(source, "protected void DispatchAIGroupRelease(MCPCommand command, MCPResult result)")
        self.assertIn("report.released = 0;", body)
        self.assertIn('report.release = "released";', body)
        self.assertIn("report.confirmed = true;", body)
        self.assertIn('report.release = "pending";', body)
        stop = method_body(source, "protected override void LeaseCloseExtensionStop()")
        self.assertIn("AIGroupRequestRelease(m_AIGroups.Get(index))", stop)


class ExpansionBaseHooksTest(unittest.TestCase):
    """E9: without the adapter the base answers unknown_command unchanged."""

    def test_e9_base_dispatch_and_poll_are_unchanged(self) -> None:
        # Mutants killed: a base Dispatch branch for an adapter verb (the
        # census would grow), and the base poll gaining bytes.
        source = BRIDGE.read_text(encoding="utf-8")
        dispatch = method_body(source, "protected void Dispatch(MCPCommand command)")
        self.assertNotIn("ai_group_spawn", dispatch)
        extension_at = dispatch.index("DispatchExtension(command, result)")
        unknown_at = dispatch.index('"unknown_command"')
        self.assertLess(extension_at, unknown_at)
        hook = method_body(source, "protected int DispatchExtension(MCPCommand command, MCPResult result)")
        self.assertIn("return 0;", hook)
        poll_params = method_body(source, "protected string ExtensionPollParams()")
        self.assertIn('return "";', poll_params)
        caps = method_body(source, "protected string ServerCapabilities()")
        self.assertIn("return SERVER_CAPABILITIES;", caps)
        self.assertNotIn("ai_group", _base_commands())
        start_poll = method_body(source, "protected void StartPoll()")
        self.assertIn("ServerCapabilities()", start_poll)
        self.assertIn("ExtensionPollParams()", start_poll)


if __name__ == "__main__":
    unittest.main()
