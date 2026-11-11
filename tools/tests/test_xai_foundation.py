"""Expansion AI adapter foundation (expansion-ai-adapter plan 2.1-2.3, 3.6, 5.2).

Offline criteria O1, O2, O6, O7, O9, O10-O14, E6 and E9 on bridge v13.
The lease-close gate admits only when every bound peer announces the
v13 close; O6/O10-O13 drive the real broker and coordinator.

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
    EXPANSION_AI_VERB_BITS,
    EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH,
    _compare_bridge_capabilities,
    parse_xai,
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
from tests.fence_helpers import INST_CLIENT, INST_SERVER, PID_CLIENT, PID_SERVER, bound_queue
from tests.xai_helpers import GOOD_XAI, _WireString, _base_commands, wire_caps, xai_wire

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


def _adapter_census() -> list[str]:
    """The stored census with the adapter loaded: base names plus the verbs
    the broker expands out of the adapter's xai bitmask (H3/H12)."""

    source = ADAPTER.read_text(encoding="utf-8")
    verbs = re.search(r'EXPANSION_AI_VERBS = "([0-9]+)"', source)
    assert verbs is not None
    _hash, names = parse_xai(f"{GOOD_XAI}.{verbs.group(1)}")
    return _base_commands() + list(names)


def _good_commands(*, lease_close: bool = True, adapter: bool = True) -> list[str]:
    commands = _adapter_census() if adapter else _base_commands()
    # v13: the base census already announces lease_close; never duplicate it.
    commands = [name for name in commands if name not in ("lease_close", "lease_close_client")]
    if lease_close:
        commands += ["lease_close"]
    return commands


def _poll(
    state: loopback.ServerState, commands: list[str], ach: str | None, xai: str | None
):
    # commands is the stored (post-expansion) census; on the wire the
    # adapter verbs travel in the xai mask, not in caps (H3).
    return state.record_poll(
        "server",
        f"{EXPECTED_BRIDGE_VERSION}~1.29",
        instance=INST_SERVER,
        source_pid=PID_SERVER,
        caps=",".join(wire_caps(commands)),
        ach=ach,
        xai=None if xai is None else xai_wire(commands, xai),
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
        stored = state.take_result(body["id"])
        self.assertEqual(
            (stored["error"], stored["detail"]),
            ("expansion_unavailable", "adapter_not_loaded"),
            stored,
        )
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
        stored = state.take_result(body["id"])
        self.assertEqual(
            (stored["error"], stored["detail"]),
            ("expansion_unavailable", "adapter_contract_mismatch"),
            stored,
        )

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
            (
                # verb before xai: both wrong reads the missing verb (H7)
                _good_commands(adapter=False),
                server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
                "0" * 16,
                None,
                {"error": "expansion_unavailable", "detail": "adapter_not_loaded"},
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
        names = list(dict.fromkeys(_adapter_census() + ["lease_close"]))
        self.assertEqual(len(names), len(set(names)))
        self.assertLessEqual(len(names), loopback.POLL_CAPS_MAX_NAMES)


class ExpansionCloseGateAllPeersTest(unittest.TestCase):
    """H10 / plan 5.1: the close gate checks every peer the run binds."""

    def _spawn_attempt(self, state: loopback.ServerState):
        coord = state.coordination
        if coord is None:
            return state.enqueue_command(SPAWN, dict(SPAWN_WIRE), peer="server")
        lease = getattr(state, "_gate_lease", None)
        client = getattr(state, "_gate_client", None)
        if lease is None or client is None:
            from tests.lease_helpers import _identity

            client = _identity("gate")
            status, lease = coord.acquire(client, "trace")
            self.assertEqual(status, 200, lease)
            state._gate_client = client  # type: ignore[attr-defined]
            state._gate_lease = lease  # type: ignore[attr-defined]
        payload = {
            "platform": client.platform, "pid": client.pid, "ppid": client.ppid,
            "started_at_utc": client.started_at_utc,
            "session_id": client.session_id, "task_label": client.task_label,
        }
        return state.enqueue_command(
            SPAWN, dict(SPAWN_WIRE), peer="server",
            identity_payload=payload, lease_token=lease["lease_token"],
            operation_timeout_s=30.0,
        )

    def _attach_registered(self, state: loopback.ServerState) -> None:
        # Real §2.12 path: the coordination setter replays BOUND bindings
        # (install_bound_peer/confirm already noted them if attached).
        from dayz_mcp.session_coordination import SessionCoordinator

        state.coordination = SessionCoordinator()
        state.retail_probe = lambda: {"known": True, "processes": []}

    def _state_with_client(
        self, client_commands: list[str], *, registered: bool = True
    ) -> loopback.ServerState:
        state = _bound_state()
        state.install_bound_peer(
            instance=INST_CLIENT, role="client", pid=PID_CLIENT, run_id="xai-run"
        )
        if registered:
            self._attach_registered(state)
        _announce(state, _good_commands(), server.EXPECTED_SERVER_ARG_CONTRACT_HASH, GOOD_XAI)
        state._peer_caps["client"] = {
            "generation": state.daemon_generation,
            "commands": list(client_commands),
            "reason": "ok",
            "arg_contract_hash": None,
            "expansion_arg_contract_hash": None,
            "announced_at": state._now(),
            "instance": INST_CLIENT,
        }
        return state

    def test_h10_client_without_close_support_blocks_the_spawn(self) -> None:
        # Mutant killed: the gate reading only the server's lease_close.
        state = self._state_with_client(["entities_query"])
        status, body = self._spawn_attempt(state)
        self.assertEqual(status, 409)
        self.assertEqual(
            body, {"error": "expansion_unavailable", "detail": "lease_close_not_supported"}
        )
        self.assertEqual(bound_queue(state, "server"), [])

    def test_h10_every_bound_peer_with_close_support_admits_the_spawn(self) -> None:
        state = self._state_with_client(["lease_close_client"])
        status, body = self._spawn_attempt(state)
        self.assertEqual(status, 200, body)
        self.assertEqual(len(bound_queue(state, "server")), 1)

    def test_h1_unregistered_peer_blocks_the_spawn(self) -> None:
        # A-r4-H1. Mutant killed: gate without the registration check.
        # Full v13 census on both peers, but the coordinator has no peer
        # registered for the run: no lease-end round could ever open.
        state = self._state_with_client(["lease_close_client"], registered=False)
        status, body = self._spawn_attempt(state)
        self.assertEqual(
            (status, body),
            (409, {"error": "expansion_unavailable", "detail": "lease_close_not_supported"}),
        )
        self.assertEqual(bound_queue(state, "server"), [])
        # Registered through the real path it admits; clearing the
        # registry (processes gone) makes it refuse again.
        self._attach_registered(state)
        assert state.coordination is not None
        self.assertTrue(
            state.coordination.peer_registered("xai-run", "server", INST_SERVER)
        )
        self.assertEqual(self._spawn_attempt(state)[0], 200)
        state._bound_queues[INST_SERVER].clear()
        state.coordination.note_processes_gone("xai-run", None)
        status, body = self._spawn_attempt(state)
        self.assertEqual(
            (status, body),
            (409, {"error": "expansion_unavailable", "detail": "lease_close_not_supported"}),
        )
        self.assertEqual(bound_queue(state, "server"), [])

    def _gate_state(self, role: str) -> loopback.ServerState:
        state = loopback.ServerState(key="k")
        state.daemon_generation = "gen-xai"
        state.install_bound_peer(
            instance=INST_SERVER, role=role, pid=PID_SERVER, run_id="xai-run"
        )
        self._attach_registered(state)
        _announce(state, _good_commands(), server.EXPECTED_SERVER_ARG_CONTRACT_HASH, GOOD_XAI)
        return state

    def _client_census(self, state, instance, commands, generation=None) -> None:
        state._peer_caps["client"] = {
            "generation": state.daemon_generation if generation is None else generation,
            "commands": list(commands),
            "reason": "ok",
            "arg_contract_hash": None,
            "expansion_arg_contract_hash": None,
            "announced_at": state._now(),
            "instance": instance,
        }

    def test_h1_peer_registered_distinguishes_the_instance(self) -> None:
        # A-r5-H4. Mutant killed: peer_registered ignoring the instance
        # (N3: key-only lookup in the peer registry).
        from dayz_mcp.session_coordination import SessionCoordinator

        coordinator = SessionCoordinator()
        coordinator.note_peer_bound("xai-run", "client", INST_CLIENT)
        self.assertTrue(
            coordinator.peer_registered("xai-run", "client", INST_CLIENT)
        )
        self.assertFalse(
            coordinator.peer_registered("xai-run", "client", INST_SERVER)
        )

    def test_h10_offline_binding_needs_both_closes(self) -> None:
        # Mutant killed: an offline binding treated as server-only.
        state = self._gate_state("offline")
        status, body = self._spawn_attempt(state)
        self.assertEqual(
            (status, body),
            (409, {"error": "expansion_unavailable", "detail": "lease_close_not_supported"}),
        )
        self._client_census(state, INST_SERVER, ["lease_close_client"])
        status, body = self._spawn_attempt(state)
        self.assertEqual(status, 200, body)

    def test_h10_starting_or_stale_client_blocks_and_server_only_run_admits(self) -> None:
        # Mutants killed: required peers read only from BOUND bindings, and
        # a client census of another generation accepted.
        state = self._gate_state("server")
        state._bindings[INST_CLIENT] = loopback.Binding(
            instance=INST_CLIENT, run_id="xai-run", role="client", epoch=99,
            pid=None, creation_time_utc=None, state=loopback.BINDING_STARTING,
        )
        status, body = self._spawn_attempt(state)
        self.assertEqual(body.get("detail"), "lease_close_not_supported", body)
        state = self._gate_state("server")
        state.install_bound_peer(
            instance=INST_CLIENT, role="client", pid=PID_CLIENT, run_id="xai-run"
        )
        self._client_census(state, INST_CLIENT, ["lease_close_client"], generation="old")
        status, body = self._spawn_attempt(state)
        self.assertEqual(body.get("detail"), "lease_close_not_supported", body)
        state = self._gate_state("server")
        status, body = self._spawn_attempt(state)
        self.assertEqual(status, 200, body)


class ExpansionXaiWireTest(unittest.TestCase):
    """H3 mask on the wire: record_poll -> stored census -> admission (O2/O14)."""

    def _after_poll(self, xai: str, caps: list[str] | None = None):
        state = _bound_state()
        names = list(dict.fromkeys(_base_commands() + ["lease_close"])) if caps is None else caps
        status, polled = state.record_poll(
            "server", f"{EXPECTED_BRIDGE_VERSION}~1.29", instance=INST_SERVER,
            source_pid=PID_SERVER, caps=",".join(names),
            ach=server.EXPECTED_SERVER_ARG_CONTRACT_HASH, xai=xai,
        )
        self.assertEqual(status, 200, polled)
        return state, state.enqueue_command(RELEASE, {"spawn_token": TOKEN}, peer="server")

    def test_good_mask_expands_to_names_and_enqueues(self) -> None:
        state, (status, body) = self._after_poll(f"{GOOD_XAI}.3")
        self.assertEqual(status, 200, body)
        stored = state._peer_caps["server"]["commands"]
        self.assertEqual([c for c in stored if c.startswith("ai_")], [RELEASE, SPAWN])

    def test_unknown_bit_is_contract_mismatch(self) -> None:
        # Mutant killed: a bit this broker does not know accepted (or read
        # as adapter_not_loaded, hiding the version skew of plan 2.2).
        unknown = 1 << len(EXPANSION_AI_VERB_BITS)
        _state, reply = self._after_poll(f"{GOOD_XAI}.{unknown | 1}")
        self.assertEqual(
            reply,
            (409, {"error": "expansion_unavailable", "detail": "adapter_contract_mismatch"}),
        )

    def test_malformed_mask_announces_no_verb(self) -> None:
        # Mutant killed: a bare hash or a non-canonical mask read as verbs.
        for xai in (GOOD_XAI, f"{GOOD_XAI}.", f"{GOOD_XAI}.03", f"{GOOD_XAI}.x", f"{GOOD_XAI}.-1"):
            with self.subTest(xai=xai):
                _state, reply = self._after_poll(xai)
                self.assertEqual(
                    reply,
                    (409, {"error": "expansion_unavailable", "detail": "adapter_not_loaded"}),
                )

    def test_name_in_caps_and_mask_is_duplicate(self) -> None:
        # Mutant killed: the expansion skipping the duplicate check.
        state, _reply = self._after_poll(
            f"{GOOD_XAI}.3", list(dict.fromkeys(_base_commands() + ["lease_close", RELEASE]))
        )
        self.assertEqual(state._peer_caps["server"]["reason"], "duplicate")

    def test_expanded_census_over_the_cap_is_too_many(self) -> None:
        # Mutant killed: the 64-name cap checked before the expansion only.
        caps = [f"x{i}" for i in range(loopback.POLL_CAPS_MAX_NAMES - 1)]
        state, _reply = self._after_poll(f"{GOOD_XAI}.3", caps)
        self.assertEqual(state._peer_caps["server"]["reason"], "too_many")

    def test_bit_order_is_append_only_and_matches_the_dispatch(self) -> None:
        # Mutants killed: a reordered bit table, and a mask announcing a
        # verb DispatchExtension does not answer (it would read unknown_command).
        self.assertEqual(
            EXPANSION_AI_VERB_BITS[:4],
            ("ai_group_release", "ai_group_spawn", "ai_group_order", "ai_group_state"),
        )
        source = ADAPTER.read_text(encoding="utf-8")
        hook = method_body(
            source, "protected override int DispatchExtension(MCPCommand command, MCPResult result)"
        )
        dispatched = set(re.findall(r'command\.cmd == "([a-z_]+)"', hook))
        self.assertEqual(set(_adapter_census()) - set(_base_commands()), dispatched)


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
        base = _base_commands()
        for commands in (base, _adapter_census()):
            with self.subTest(n=len(commands)):
                verdict = _compare_bridge_capabilities(
                    "server", self._block(commands), self._registered()
                )
                self.assertEqual(verdict["state"], "match", verdict)

    def _public_registry(self) -> frozenset[str]:
        app, runtime = server.build_app(
            server.ServerConfig(
                key="test-key", port=0, log_sink=lambda _message: None
            )
        )
        registered = frozenset(tool.name for tool in app._tool_manager.list_tools())
        self.assertEqual(registered, runtime._registered_tool_names)
        return registered

    def test_o7_announced_adapter_verb_missing_from_public_registry_is_mismatch(
        self,
    ) -> None:
        # Mutant killed: missing_tool excluding EXPANSION_AI_COMMANDS and
        # hiding an announced adapter verb absent from the real public registry.
        registered = self._public_registry()
        self.assertIn(SPAWN, registered)

        state = _bound_state()
        status, polled = _poll(
            state,
            _adapter_census(),
            server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            GOOD_XAI,
        )
        self.assertEqual(status, 200, polled)
        announced = state.status_snapshot()["peers"]["server"]["capabilities"]

        missing = _compare_bridge_capabilities(
            "server", announced, registered - {SPAWN}
        )
        self.assertEqual(missing["state"], "mismatch", missing)
        self.assertEqual(
            missing["reason"], "census_disagrees_with_registered_tools", missing
        )
        self.assertEqual(
            missing["announced_without_registered_tool"], [SPAWN], missing
        )
        self.assertEqual(
            missing["registered_without_announced_command"], [], missing
        )

        with_adapter = _compare_bridge_capabilities(
            "server", announced, registered
        )
        self.assertEqual(with_adapter["state"], "match", with_adapter)
        self.assertEqual(
            with_adapter["announced_without_registered_tool"], [], with_adapter
        )
        self.assertEqual(
            with_adapter["registered_without_announced_command"], [], with_adapter
        )

        state_no_xai = _bound_state()
        status, polled = _poll(
            state_no_xai,
            _base_commands(),
            server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            None,
        )
        self.assertEqual(status, 200, polled)
        no_xai = state_no_xai.status_snapshot()["peers"]["server"]["capabilities"]
        without_adapter = _compare_bridge_capabilities(
            "server", no_xai, registered
        )
        self.assertEqual(without_adapter["state"], "match", without_adapter)
        self.assertEqual(
            without_adapter["announced_without_registered_tool"], [], without_adapter
        )
        self.assertEqual(
            without_adapter["registered_without_announced_command"],
            [],
            without_adapter,
        )


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
        base_args = MESSAGES.read_text(encoding="utf-8")
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


class ExpansionLeaseCloseV13Test(unittest.TestCase):
    """O6/O10-O13 on v13: real ServerState + SessionCoordinator, fake clocks.

    Mutants (plan §6): cleaning by timeout, keeping residue after it
    disappears, clearing server debt on client-only exit, creating OWED
    for a census without the close, and an unreachable adapter rejection.
    """

    def setUp(self) -> None:
        from dayz_mcp.session_coordination import SessionCoordinator
        from tests.lease_helpers import AuditSink, FakeClock, SequentialIds, _identity

        self._coord_cls = SessionCoordinator
        self.clock = FakeClock()
        self.broker_clock = FakeClock()
        self.audit = AuditSink()
        self.a = _identity("a")
        self.b = _identity("b")
        self.close_supported = True
        self.state = loopback.ServerState("test-key", time_fn=self.broker_clock)
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(self.state, run_id="xai-close-run")
        self.coordinator = SessionCoordinator(
            time_fn=self.clock,
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("lease"),
            audit=self.audit,
            cleanup=lambda *args: self.state.cleanup_owner(*args),
        )
        # Peers were bound by bind_both_peers (install_bound_peer) before
        # the coordinator attached, so the setter replay registered them:
        # no manual note_peer_bound anywhere in this class (A-r4-H1).
        self.state.coordination = self.coordinator
        self.state.retail_probe = lambda: {"known": True, "processes": []}
        self.c = _identity("c")
        for peer in ("server", "client"):
            inst = INST_SERVER if peer == "server" else INST_CLIENT
            self.assertTrue(
                self.coordinator.peer_registered("xai-close-run", peer, inst)
            )
            self.assertEqual(self._poll(peer), [])

    # -- harness (mirrors test_lease_close_broker stage 2b) ----------------
    def _poll(self, peer: str) -> list[dict]:
        from tests.fence_helpers import poll_census_query

        query = poll_census_query(peer)
        close_name = "lease_close" if peer == "server" else "lease_close_client"
        if self.close_supported:
            # Adapter verbs travel in the xai mask, not in caps (H3).
            names = [n for n in query["caps"].split(",") if n and n not in EXPANSION_AI_ARG_CONTRACT]
            if close_name not in names:
                names.append(close_name)
            version = f"{EXPECTED_BRIDGE_VERSION}~1.29"
        else:
            # A-r4-H5: a census without the close, from the authored fixture,
            # with no lease_close* name announced.
            import json

            fixture = json.loads(
                (Path(__file__).parent / "fixtures" / "bridge_capabilities_v1.json")
                .read_text(encoding="utf-8")
            )
            names = [
                n for n in fixture["peers"][peer]
                if not n.startswith("lease_close") and n not in EXPANSION_AI_ARG_CONTRACT
            ]
            version = f"{EXPECTED_BRIDGE_VERSION}~1.29"
        status, body = self.state.record_poll(
            peer,
            version,
            instance=INST_SERVER if peer == "server" else INST_CLIENT,
            source_pid=PID_SERVER if peer == "server" else PID_CLIENT,
            caps=",".join(names),
            ach=query.get("ach"),
            xai=f"{GOOD_XAI}.3" if peer == "server" else None,
        )
        self.assertEqual(status, 200, body)
        return list(body.get("commands") or [])

    def _queued(self, peer: str) -> list[str]:
        inst = INST_SERVER if peer == "server" else INST_CLIENT
        return [str(c.get("cmd")) for c in self.state._bound_queues.get(inst, [])]

    def _payload(self, client) -> dict:
        return {
            "platform": client.platform, "pid": client.pid, "ppid": client.ppid,
            "started_at_utc": client.started_at_utc,
            "session_id": client.session_id, "task_label": client.task_label,
        }

    def _spawn_delivered(self):
        status, lease = self.coordinator.acquire(self.a, "trace")
        self.assertEqual(status, 200, lease)
        status, body = self.state.enqueue_command(
            SPAWN, dict(SPAWN_WIRE), peer="server",
            identity_payload=self._payload(self.a),
            lease_token=lease["lease_token"], operation_timeout_s=30.0,
        )
        self.assertEqual(status, 200, body)
        delivered = self._poll("server")
        self.assertEqual([c["cmd"] for c in delivered], [SPAWN], delivered)
        return lease, int(body["id"])

    def _release(self, client, lease) -> None:
        status, body = self.coordinator.release(client, lease["lease_token"])
        self.assertEqual(status, 200, body)
        with self.coordinator._condition:
            self.assertTrue(self.coordinator._condition.wait_for(
                lambda: not self.coordinator._handoff_pending, timeout=30.0))

    def _census(self, peer: str, close: dict, **fields) -> dict:
        census = {
            "generation": close["args"]["close_generation"],
            "lease_id": close["args"]["close_lease_id"],
            "peer": peer, "state": "clean",
        }
        if peer == "client":
            census.update(entries=[{"name": "action", "state": "clean"}],
                          move_floor=7, weapon_floor=4, look_floor=3, net_low=10, net_high=20,
                          conn_gen=1)
        else:
            census.update(entries=[{"name": "bots", "state": "clean"}],
                          floors=[{"net_low": 10, "net_high": 20, "conn_gen": 1,
                                   "move_id": 7, "weapon_id": 4, "look_id": 3}])
        census.update(fields)
        return census

    def _settle(self, peer: str, close: dict, **fields):
        return self.state.store_result(
            {"id": close["id"], "ok": True, "lease_close": self._census(peer, close, **fields)},
            instance=INST_SERVER if peer == "server" else INST_CLIENT,
        )

    def _deliver_close(self, peer: str) -> dict:
        commands = self._poll(peer)
        want = "lease_close" if peer == "server" else "lease_close_client"
        self.assertEqual([c["cmd"] for c in commands], [want], commands)
        return commands[0]

    def _photo(self) -> dict:
        return self.coordinator.status(self.a).get("lease_end", {})

    def _lost_server_close(self) -> dict:
        # Delivered spawn, lease released, client close settled clean;
        # the server close is delivered but its answer is lost.
        lease, command_id = self._spawn_delivered()
        self.state.store_result({"id": command_id, "ok": True}, instance=INST_SERVER)
        self._release(self.a, lease)
        self.assertEqual(self._settle("client", self._deliver_close("client"))[0], 200)
        return self._deliver_close("server")

    def _residue_owed(self) -> dict:
        # A server close answered with the adapter residue: OWED at once.
        lease, command_id = self._spawn_delivered()
        self.state.store_result({"id": command_id, "ok": True}, instance=INST_SERVER)
        self._release(self.a, lease)
        self.assertEqual(self._settle("client", self._deliver_close("client"))[0], 200)
        server_close = self._deliver_close("server")
        self.assertEqual(self._settle(
            "server", server_close,
            entries=[{"name": f"ai_group:{TOKEN}", "state": "residue",
                      "reason": "ai_group_release_unconfirmed"}],
            state="residue")[0], 200)
        self.assertTrue(self._photo().get("unconfirmed"))
        return server_close

    # -- O6 -----------------------------------------------------------------
    def test_o6_quarantine_produces_debt_and_publishes_no_close(self) -> None:
        # A-r4-H6: quarantine at lease end owes both peers, queues nothing.
        lease, command_id = self._spawn_delivered()
        self.state.store_result({"id": command_id, "ok": True}, instance=INST_SERVER)
        self.state.retail_probe = lambda: {"known": True, "processes": [{"pid": 1}]}
        self._release(self.a, lease)
        self.assertEqual(self._queued("server"), [])
        self.assertEqual(self._queued("client"), [])
        reasons = {
            r["peer"]: list(r["reasons"]) for r in self._photo().get("unconfirmed", [])
        }
        self.assertEqual(
            reasons, {"server": ["retail_quarantine"], "client": ["retail_quarantine"]}
        )

    def test_o6_abandon_release_does_not_settle_the_close_row(self) -> None:
        # A-r4-H6: an answered ai_group_release from the abandon path (O5)
        # does not settle the lease-close row; the close still has to run.
        lease, command_id = self._spawn_delivered()
        abandoned = self.state.abandon_command(command_id, "tool_timeout")
        self.assertEqual(abandoned.get("status"), "release_queued", abandoned)
        delivered = self._poll("server")
        self.assertEqual([c["cmd"] for c in delivered], [RELEASE], delivered)
        self.state.store_result(
            {"id": delivered[0]["id"], "ok": True}, instance=INST_SERVER
        )
        self._release(self.a, lease)
        self.assertEqual(self._settle("client", self._deliver_close("client"))[0], 200)
        # Still outstanding: the server close, not the abandon release,
        # is what settles this row.
        self.assertEqual(self._queued("server"), ["lease_close"])
        self.assertEqual(self._photo().get("outstanding", 0), 1)
        self.assertEqual(self._settle(
            "server", self._deliver_close("server"),
            entries=[{"name": f"ai_group:{TOKEN}", "state": "residue",
                      "reason": "ai_group_release_unconfirmed"}],
            state="residue")[0], 200)
        self.assertTrue(self._photo().get("unconfirmed"))

    def test_o6_lease_end_publishes_closes_and_stop_covers_all_groups(self) -> None:
        # Mutant killed: cleanup_owner queueing ai_group_release at lease end.
        lease, command_id = self._spawn_delivered()
        self.state.store_result({"id": command_id, "ok": True}, instance=INST_SERVER)
        self._release(self.a, lease)
        self.assertEqual(self._queued("client"), ["lease_close_client"])
        self.assertEqual(self._queued("server"), [])
        self.assertNotIn(RELEASE, self._queued("server") + self._queued("client"))
        client_close = self._deliver_close("client")
        status, body = self._settle("client", client_close)
        self.assertEqual(status, 200, body)
        self.assertEqual(self._queued("server"), ["lease_close"])
        server_close = self._deliver_close("server")
        # The server census carries the adapter residue by spawn_token name.
        status, body = self._settle(
            "server", server_close,
            entries=[{"name": f"ai_group:{TOKEN}", "state": "residue",
                      "reason": "ai_group_release_unconfirmed"}],
            state="residue",
        )
        self.assertEqual(status, 200, body)
        self.assertIn("server", {r["peer"] for r in self._photo().get("unconfirmed", [])})

    # -- O10 ----------------------------------------------------------------
    def test_o10_residue_census_is_owed(self) -> None:
        # Residue still present when the bridge observer gives up (25 s):
        # the row is OWED, never silently cleaned.
        self._residue_owed()
        rows = self._photo()["unconfirmed"]
        self.assertEqual(
            [(r["peer"], r["reasons"]) for r in rows],
            [("server", ["ai_group_release_unconfirmed"])],
        )

    def test_o10_lost_answer_is_close_timeout_at_40s(self) -> None:
        # A-r4-H2. Mutants killed in this module: cleaning by timeout
        # (_expire_rows_locked settling instead of OWED) and waiting
        # forever (no expiry). The server close is delivered and its
        # answer is lost, so only the 40 s bound can resolve the row.
        from dayz_mcp.session_coordination import LEASE_END_BOUND_S

        self._lost_server_close()
        status, ticket = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status, 202, ticket)  # bounded wait, not a grant
        self.clock.advance(LEASE_END_BOUND_S - 0.01)
        self.assertEqual(self._photo().get("unconfirmed", []), [])
        self.clock.advance(0.01)
        rows = self._photo()["unconfirmed"]
        self.assertEqual(
            [(r["peer"], r["reasons"]) for r in rows],
            [("server", ["close_timeout"])],
        )
        status, grant = self.coordinator.wait(self.b, ticket["ticket"], 0.0)
        self.assertEqual(status, 200, grant)
        self.assertIn("lease_end_unconfirmed", grant["cleanup_degraded"])
        # A third session still sees the debt, and no orphan
        # ai_group_release was queued to clear it behind the close.
        third = self.coordinator.status(self.c).get("lease_end", {})
        self.assertEqual(
            [(r["peer"], r["reasons"]) for r in third.get("unconfirmed", [])],
            [("server", ["close_timeout"])],
        )
        self.assertNotIn(RELEASE, self._queued("server"))
        self.assertNotIn(RELEASE, self._queued("client"))

    # -- O11 ----------------------------------------------------------------
    def test_o11_late_clean_of_current_id_clears(self) -> None:
        # A-r4-H3. Mutant killed in this module: keeping the residue
        # after a late clean of the current close id/round
        # (_apply_owed_clean_locked not settling).
        from dayz_mcp.session_coordination import LEASE_END_BOUND_S

        close = self._lost_server_close()
        self.clock.advance(LEASE_END_BOUND_S)
        before = self._photo()
        self.assertTrue(before.get("unconfirmed"))
        status, body = self._settle("server", close)
        self.assertEqual(status, 200, body)
        self.assertNotIn("discarded", body)
        self.assertEqual(self._photo().get("unconfirmed", []), [])
        # The next grant does not carry the reason.
        status, grant = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status, 200, grant)
        self.assertNotIn(
            "lease_end_unconfirmed", grant.get("cleanup_degraded", [])
        )

    def test_o11_residue_then_clean_close_clears_adapter_debt(self) -> None:
        # Plan O11 POS: after O10's residue, the references disappear and a
        # valid clean close clears the adapter debt; outstanding reaches
        # zero, the next grant does not carry the reason, and the earlier
        # degraded grant keeps its photo.
        import copy

        self._residue_owed()
        status, grant_b = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status, 200, grant_b)
        self.assertIn("lease_end_unconfirmed", grant_b.get("cleanup_degraded", []))
        frozen = copy.deepcopy(grant_b)
        self._release(self.b, grant_b)
        self.assertEqual(self._settle("client", self._deliver_close("client"))[0], 200)
        status, body = self._settle("server", self._deliver_close("server"))
        self.assertEqual(status, 200, body)
        self.assertNotIn("discarded", body)
        photo = self.coordinator.status(self.c).get("lease_end", {})
        self.assertEqual(photo.get("unconfirmed", []), [])
        self.assertEqual(photo.get("outstanding", 0), 0)
        status, grant_c = self.coordinator.acquire(self.c, "trace")
        self.assertEqual(status, 200, grant_c)
        self.assertNotIn("lease_end_unconfirmed", grant_c.get("cleanup_degraded", []))
        self.assertEqual(grant_b, frozen)

    def _late_negative(self, **field) -> None:
        # Each negative gets its own lost close, because the first
        # result consumes the close id.
        from dayz_mcp.session_coordination import LEASE_END_BOUND_S

        close = self._lost_server_close()
        self.clock.advance(LEASE_END_BOUND_S)
        self.assertTrue(self._photo().get("unconfirmed"))
        if "instance" in field:
            instance = field.pop("instance")
            status, _body = self.state.store_result(
                {"id": close["id"], "ok": True,
                 "lease_close": self._census("server", close, **field)},
                instance=instance,
            )
            self.assertEqual(status, 200)
        else:
            self._settle("server", close, **field)
        self.assertTrue(self._photo().get("unconfirmed"), field)

    def test_o11_neg_other_lease(self) -> None:
        self._late_negative(lease_id="another-lease")

    def test_o11_neg_other_generation(self) -> None:
        self._late_negative(generation="999")

    def test_o11_neg_other_instance(self) -> None:
        self._late_negative(instance=INST_CLIENT)

    def test_o11_neg_low_floors(self) -> None:
        self._late_negative(floors=[{"net_low": 10, "net_high": 20, "conn_gen": 1,
                                     "move_id": 0, "weapon_id": 0, "look_id": 0}])

    def test_o11_neg_other_round(self) -> None:
        # A clean census carried by the previous round's close id does
        # not settle the current round's row.
        from dayz_mcp.session_coordination import LEASE_END_BOUND_S

        old_close = self._lost_server_close()
        self.clock.advance(LEASE_END_BOUND_S)
        self.assertTrue(self._photo().get("unconfirmed"))
        status, grant_b = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status, 200, grant_b)
        self.assertIn("lease_end_unconfirmed", grant_b.get("cleanup_degraded", []))
        self._release(self.b, grant_b)
        self.assertEqual(self._settle("client", self._deliver_close("client"))[0], 200)
        self._deliver_close("server")
        self._settle("server", old_close)
        self.clock.advance(LEASE_END_BOUND_S)
        rows = self._photo().get("unconfirmed", [])
        self.assertEqual(
            [(r["peer"], r["reasons"]) for r in rows],
            [("server", ["close_timeout"])],
        )

    # -- O12 ----------------------------------------------------------------
    def test_o12_processes_gone_clears_never_observed_deletes(self) -> None:
        # A-r4-H4. Mutants killed: clearing server debt on client-only
        # exit, note_processes_gone of another run settling this run,
        # retire_role settling the debt, and seed_restart_debt settling
        # or replacing an existing row. The debt is a real adapter
        # residue (O10), not a seeded one.
        self._residue_owed()
        status, grant = self.coordinator.acquire(self.b, "trace")
        self.assertEqual(status, 200, grant)
        self.assertIn("lease_end_unconfirmed", grant.get("cleanup_degraded", []))
        # Another run's exit settles nothing here (checked directly on
        # the coordinator status, not on the earlier grant).
        self.coordinator.note_processes_gone("other-run", None)
        self.assertTrue(self.coordinator.status(self.b)["lease_end"]["unconfirmed"])
        # Retiring the server binding (the peer that owes) without an
        # accredited exit does not settle its row either.
        self.state.retire_role("xai-close-run", "server", "test")
        self.assertEqual(
            [(r["peer"], r["reasons"]) for r in self._photo()["unconfirmed"]],
            [("server", ["ai_group_release_unconfirmed"])],
        )
        # Seeding restart debt neither settles nor replaces the row.
        self.coordinator.seed_restart_debt([("xai-close-run", "server")])
        rows = self._photo()["unconfirmed"]
        self.assertEqual(
            [(r["peer"], r["reasons"]) for r in rows],
            [("server", ["ai_group_release_unconfirmed"])],
        )
        self.coordinator.note_processes_gone("xai-close-run", "client")
        still = self.coordinator.status(self.b).get("lease_end", {})
        self.assertTrue(still.get("unconfirmed"), still)
        self.coordinator.note_processes_gone("xai-close-run", "server")
        self.assertEqual(self._photo().get("unconfirmed", []), [])
        self._release(self.b, grant)
        status_b, grant_b = self.coordinator.acquire(self.c, "trace")
        self.assertEqual(status_b, 200, grant_b)
        self.assertNotIn("lease_end_unconfirmed", grant_b.get("cleanup_degraded", []))
        self.assertNotIn("lease_end", grant_b)

    # -- O13 ----------------------------------------------------------------
    def test_o13_close_publishing_with_a_census_without_the_close(self) -> None:
        # Mutant killed: creating OWED/close work for a census without the close.
        self.close_supported = False
        self._poll("server")
        self._poll("client")
        lease, command_id = self._delivered_on_a_census_without_the_close()
        self.state.store_result({"id": command_id, "ok": True}, instance=INST_SERVER)
        self._release(self.a, lease)
        self.assertEqual(self._queued("server"), [])
        self.assertEqual(self._queued("client"), [])
        self.assertEqual(self._photo().get("unconfirmed", []), [])

    def _delivered_on_a_census_without_the_close(self):
        # With a census without the close the spawn gate itself refuses (see H10 tests);
        # deliver plain work instead so the lease has delivered work.
        status, lease = self.coordinator.acquire(self.a, "trace")
        self.assertEqual(status, 200, lease)
        status, body = self.state.enqueue_command(
            "object_delete", {"object_id": 1}, peer="server",
            identity_payload=self._payload(self.a),
            lease_token=lease["lease_token"], operation_timeout_s=30.0,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual([c["cmd"] for c in self._poll("server")], ["object_delete"])
        return lease, int(body["id"])

    def test_o13_adapter_without_close_support_rejected_before_spawn(self) -> None:
        # Mutant killed: the lease_close_not_supported rejection unreachable.
        # The delivery half lives in test_xai_spawn
        # (test_close_support_lost_before_delivery_discards_the_spawn).
        self.close_supported = False
        self._poll("server")
        self._poll("client")
        status, lease = self.coordinator.acquire(self.a, "trace")
        self.assertEqual(status, 200, lease)
        status, body = self.state.enqueue_command(
            SPAWN, dict(SPAWN_WIRE), peer="server",
            identity_payload=self._payload(self.a),
            lease_token=lease["lease_token"], operation_timeout_s=30.0,
        )
        self.assertEqual(status, 409, body)
        self.assertEqual(body, {"error": "expansion_unavailable",
                                "detail": "lease_close_not_supported"})
        self.assertEqual(bound_queue(self.state, "server"), [])


class ExpansionO13InertTest(unittest.TestCase):
    """O13, the part that needs no close: a census without the close stays inert."""

    def test_o13_internal_release_stays_available_on_a_census_without_the_close(self) -> None:
        # Mutants killed: the lease-close gate blocking an internal release
        # (it is not a new mutation), ai_group_release joining the
        # new-mutation gate, or creating close debt on a census without the close.
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

        # A member entity left while Expansion already dropped the group
        # is not clean either (H8).
        namespace["_registry"]["by_id"] = {}
        self.assertFalse(namespace["AIGroupGone"](record))
        namespace["_registry"]["by_id"] = {7: group}
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

class ExpansionReleaseDispatchTest(unittest.TestCase):
    """E6: the real DispatchAIGroupRelease and LeaseCloseExtensionStop."""

    def _namespace(self) -> dict:
        namespace = _adapter_namespace()
        source = ADAPTER.read_text(encoding="utf-8")
        namespace["MCPAIGroup"] = lambda: SimpleNamespace(
            release=_WireString.of(""), confirmed=False, released=-1,
            remaining_ids=EnforceArray(),
        )
        for signature in (
            "protected MCPAIGroupRecord FindAIGroupByToken(string spawnToken)",
            "protected void FillRemainingIds(MCPAIGroupRecord record, MCPAIGroup report)",
            "protected void DispatchAIGroupRelease(MCPCommand command, MCPResult result)",
            "protected override void LeaseCloseExtensionStop()",
        ):
            translate(
                source, signature, namespace,
                members=("m_AIGroups", "m_RuntimeObjects"),
                class_types=("MCPAIGroupRecord", "MCPAIGroupMemberRecord", "MCPAIGroup",
                             "MCPCommand", "MCPResult", "Object"),
            )
        return namespace

    def _release(self, namespace: dict, token: str) -> SimpleNamespace:
        result = SimpleNamespace(ai_group=None, ok=False, error=_WireString.of(""))
        command = SimpleNamespace(args=SimpleNamespace(spawn_token=_WireString.of(token)))
        namespace["DispatchAIGroupRelease"](command, result)
        return result

    def test_e6_dispatch_pending_then_confirmed_and_unknown_token(self) -> None:
        namespace = self._namespace()
        alive, group = _FakeMember(), _FakeGroup()
        record = _record(namespace, members=[_member(11, alive)], group=group)
        namespace["m_AIGroups"] = EnforceArray([record])
        result = self._release(namespace, TOKEN)
        self.assertTrue(result.ok)
        self.assertEqual((result.ai_group.release.data, result.ai_group.confirmed), (b"pending", False))
        self.assertEqual(list(result.ai_group.remaining_ids), [11])
        record.members.Get(0).member = None
        namespace["_registry"]["by_id"] = {}
        result = self._release(namespace, TOKEN)
        self.assertEqual((result.ai_group.release.data, result.ai_group.confirmed), (b"released", True))
        self.assertEqual((alive.despawns, group.deletes), (1, 1))
        result = self._release(namespace, "c" * 32)
        self.assertTrue(result.ok)
        self.assertEqual(result.ai_group.released, 0)

    def test_e6_stop_requests_every_group_once(self) -> None:
        namespace = self._namespace()
        first, second = _FakeMember(), _FakeMember()
        groups = (_FakeGroup(), _FakeGroup())
        records = [
            _record(namespace, members=[_member(11, first)], group=groups[0]),
            _record(namespace, members=[_member(12, second)], group=groups[1]),
        ]
        namespace["m_AIGroups"] = EnforceArray(records)
        namespace["LeaseCloseExtensionStop"]()
        namespace["LeaseCloseExtensionStop"]()
        self.assertEqual((first.despawns, second.despawns), (1, 1))
        self.assertEqual([g.deletes for g in groups], [1, 1])


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
