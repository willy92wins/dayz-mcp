"""Expansion AI adapter ai_group_state (plan 3.4/4, ficha 90f9).

Offline criteria O4 and E7, plus the O13 part that needs no v13
lease-close activation: the read stays available when the census
announces no close support, while a real bridge v12 poll is stopped
earlier by the version gate.

Each test names the mutant it kills in its docstring; the commit message
carries the criterion -> test -> mutant table.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import daemon, loopback, server
from dayz_mcp.bridge_readiness import (
    EXPANSION_AI_VERB_BITS,
    EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH,
    _compare_bridge_capabilities,
    parse_xai,
)
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
    _ADOPTABLE_STATES,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import (
    READ_ONLY_COMMANDS,
    SessionCoordinator,
    command_requires_lease,
)
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceMap,
    EnforceString,
    EnforceVector,
    method_body,
    translate,
)
from tests.fence_helpers import INST_SERVER, PID_SERVER, bound_queue
from tests.lease_helpers import (
    AuditSink,
    FakeClock,
    SequentialIds,
    _coord,
    _identity,
)
from tests.lifecycle_helpers import Guard, Launcher, Sequence, stamp_launcher
from tests.mcp_helpers import _content_json
from tests.steam_helpers import FakeSteamGate
from tests.xai_helpers import (
    adapter_census,
    announce as _shared_announce,
    bound_state as _shared_bound_state,
    wire_caps,
    xai_wire,
)

STATE = "ai_group_state"
TOKEN = "a" * 32
GOOD_XAI = EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH
BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
ADAPTER_ROOT = addon_root().parent / "addon_expansion_ai"
ADAPTER = ADAPTER_ROOT / "scripts" / "5_Mission" / "MCPExpansionAI.c"


def _bound_state() -> loopback.ServerState:
    return _shared_bound_state()


def _announce(
    state: loopback.ServerState,
    commands: list[str] | None,
    ach: str | None,
    xai: str | None,
) -> None:
    _shared_announce(state, commands, ach, xai)


def _good_commands(*, lease_close: bool = True, state_verb: bool = True) -> list[str]:
    commands = adapter_census()
    if not state_verb:
        commands = [name for name in commands if name != STATE]
    # v13: the base census already announces lease_close; never duplicate it.
    commands = [name for name in commands if name not in ("lease_close", "lease_close_client")]
    if lease_close:
        commands += ["lease_close"]
    return commands


def _run(coro):
    # H10 deviation, kept on purpose: IsolatedAsyncioTestCase creates a
    # Windows proactor loop whose self-pipe socket the offline sandbox
    # refuses (WinError 10013), so these socket-free calls (the bridge
    # call is an AsyncMock) are driven directly. If the coroutine yields
    # to a loop this raises instead of passing vacuously.
    while True:
        try:
            yielded = coro.send(None)
        except StopIteration as stop:
            return stop.value
        if yielded is not None:
            raise AssertionError(f"tool call yielded {yielded!r} to a loop")


class AiGroupStateToolTest(unittest.TestCase):
    """O4 (tool part): exactly one id, no lease, server peer."""

    def setUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _m: None)
        )

    def test_o4_both_or_neither_id_is_bad_args_and_sends_nothing(self) -> None:
        # Mutant killed: the tool forwarding both/neither to the bridge
        # (the bridge would read group_id 0 / scene "" as a real query).
        call = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=call):
            for arguments in (
                {},
                {"group_id": 7, "scene_id": "scene1"},
                {"group_id": 0},
                {"group_id": -3},
                {"scene_id": "bad scene"},
            ):
                with self.subTest(arguments=arguments):
                    with self.assertRaises(ToolError) as raised:
                        _run(
                            self.app.call_tool(
                                STATE, {**arguments, "timeout_s": 2.0}
                            )
                        )
                    self.assertIn("bad_args", str(raised.exception))
        call.assert_not_awaited()

    def test_o4_one_id_goes_to_the_server_peer(self) -> None:
        # Mutant killed: ai_group_state routed to the client peer, or with
        # the other id's key left in the args.
        reply = {"ok": 1, "ai_group": {"group_id": 7, "alive": 1}}
        call = AsyncMock(return_value=reply)
        with patch.object(self.runtime, "call_bridge", new=call):
            result = _content_json(
                _run(self.app.call_tool(STATE, {"group_id": 7, "timeout_s": 2.0}))
            )
            scene_result = _content_json(
                _run(
                    self.app.call_tool(
                        STATE, {"scene_id": "scene1", "timeout_s": 2.0}
                    )
                )
            )
        self.assertEqual(result, reply)
        self.assertEqual(scene_result, reply)
        self.assertEqual(
            [item.args for item in call.await_args_list],
            [
                (STATE, {"group_id": 7}, "server", 2.0),
                (STATE, {"scene_id": "scene1"}, "server", 2.0),
            ],
        )
        self.assertEqual(loopback.peer_for_command(STATE), "server")
        self.assertIn(STATE, loopback.SERVER_COMMANDS)
        self.assertNotIn(STATE, loopback.CLIENT_COMMANDS)


class AiGroupStateAuthorizeTest(unittest.TestCase):
    """O4 (authorize + managed-run fence): coordinator authorize vs idle run."""

    def test_o4_authorize_without_token_and_after_release(self) -> None:
        """Coordinator authorize alone: ai_group_state passes without a token
        and after session_release (a managed run's idle fence is covered below).

        Mutants killed: ai_group_state absent from READ_ONLY_COMMANDS
        (authorize would answer lease_required without a token), and a
        coordinator read check that only works while its caller holds a lease.
        """
        self.assertIn(STATE, READ_ONLY_COMMANDS)
        self.assertFalse(command_requires_lease(STATE))
        coordinator = _coord(FakeClock(), attached=True)
        client = _identity("a")

        decision = coordinator.authorize(client, None, STATE)
        self.assertTrue(decision.allowed, decision)
        denied = coordinator.authorize(client, None, "ai_group_spawn")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.error, "lease_required")

        status, grant = coordinator.acquire(client, "owner")
        self.assertEqual(status, 200, grant)
        status, released = coordinator.release(client, grant["lease_token"])
        self.assertEqual(status, 200, released)

        after = coordinator.authorize(client, None, STATE)
        self.assertTrue(after.allowed, after)
        denied_after = coordinator.authorize(client, None, "ai_group_spawn")
        self.assertFalse(denied_after.allowed)
        self.assertEqual(denied_after.error, "lease_required")

    def test_o4_managed_run_enqueues_before_release_and_fences_after_release(
        self,
    ) -> None:
        """Managed-run idle fence: before session_release ai_group_state
        enqueues without a lease token; after daemon cleanup_begin ->
        begin_release_owner leaves the run RUNNING_IDLE, enqueue answers
        run_not_owned with the standard hint, matching players_visibility.

        Mutant killed: promising or admitting lease-free reads on an idle
        managed run without updating the documented idle-run fence contract.
        """
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        env = patch.dict(
            os.environ, {"DAYZ_MCP_SHARED_ROOT": str(root / "shared-root")}
        )
        env.start()
        self.addCleanup(env.stop)

        game = root / "DayZ"
        game.mkdir()
        for name in ("DayZDiag_x64.exe", "DayZ_BE.exe", "DayZ_x64.exe"):
            (game / name).write_bytes(b"")
        paths = RuntimePaths(
            root / "runtime",
            root / "runtime" / "audit",
            root / "runtime" / "coordination.json",
            root / "runtime" / "runs.json",
        )
        audit = AuditSink()
        state = loopback.ServerState("test-key")
        state.daemon_generation = "gen-xai"
        state.retail_probe = lambda: {"known": True, "processes": []}
        lifecycle_holder: dict[str, ProcessLifecycle] = {}
        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("lease"),
            audit=audit,
            cleanup=lambda *args: daemon.cleanup_begin(
                state, lifecycle_holder.get("service"), *args
            ),
        )
        manifest = RunManifestStore(paths)
        lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=coordinator,
            manifest=manifest,
            audit=audit,
            guard=Guard(),
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=game,
            launcher=Launcher(),
            id_fn=Sequence("run"),
            bindings=state,
            daemon_generation="gen-xai",
        )
        lifecycle_holder["service"] = lifecycle

        owner = _identity("owner")
        reader = _identity("reader")
        status, grant = coordinator.acquire(owner, "owner")
        self.assertEqual(status, 200, grant)

        run_id = "xai-managed-run"
        proc = ProcessRecord(
            PID_SERVER,
            "2026-08-18T00:00:00.000000Z",
            "a" * 64,
            "b" * 64,
            "server",
            identity_scheme="psutil-argv-v2",
        )
        manifest.add(
            RunRecord(
                run_id,
                owner.session_id,
                grant["lease_id"],
                "RUNNING",
                "red",
                "@DayZ_MCP_ExpansionAI",
                "profiles",
                "mission",
                [proc],
            )
        )
        stamp_launcher(lifecycle, run_id, owner)
        state.lifecycle = lifecycle
        state.install_bound_peer(
            instance=INST_SERVER,
            role="server",
            pid=PID_SERVER,
            run_id=run_id,
            creation_time_utc=proc.creation_time_utc,
        )
        state.coordination = coordinator
        _announce(
            state,
            _good_commands(),
            server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            GOOD_XAI,
        )

        # Positive: before session_release, a lease-free read on the owned
        # RUNNING run is admitted and enqueued without a token.
        pre_status, pre_body = state.enqueue_command(
            STATE,
            {"group_id": 7},
            peer="server",
            identity_payload=reader.to_payload(),
        )
        self.assertEqual(pre_status, 200, pre_body)
        self.assertEqual(
            [cmd["cmd"] for cmd in bound_queue(state, "server")], [STATE]
        )

        # Production release: SessionCoordinator.release -> daemon.cleanup_begin
        # -> ProcessLifecycle.begin_release_owner -> RUNNING_IDLE + fence.
        rel_status, released = coordinator.release(owner, grant["lease_token"])
        self.assertEqual(rel_status, 200, released)
        with coordinator._condition:
            self.assertTrue(
                coordinator._condition.wait_for(
                    lambda: not coordinator._handoff_pending, timeout=30.0
                )
            )
        after_run = manifest.get(run_id)
        self.assertIsNotNone(after_run)
        assert after_run is not None
        self.assertEqual(after_run.state, "RUNNING_IDLE")
        self.assertIn(after_run.state, _ADOPTABLE_STATES)
        self.assertIsNone(after_run.owner_session_id)
        self.assertNotIn(
            STATE, [cmd.get("cmd") for cmd in bound_queue(state, "server")]
        )

        # Negative: coordinator authorize still passes the read, yet the idle
        # managed run refuses enqueue with run_not_owned, like players_visibility.
        self.assertTrue(coordinator.authorize(reader, None, STATE).allowed)
        post_queue = list(bound_queue(state, "server"))
        expected = {"error": "run_not_owned", "hint": loopback._RUN_NOT_OWNED_HINT}
        state_status, state_body = state.enqueue_command(
            STATE,
            {"group_id": 7},
            peer="server",
            identity_payload=reader.to_payload(),
        )
        vis_status, vis_body = state.enqueue_command(
            "players_visibility",
            {"pos": [1.0, 2.0, 3.0], "radius": 10.0},
            peer="server",
            identity_payload=reader.to_payload(),
        )
        self.assertEqual((state_status, state_body), (409, expected))
        self.assertEqual((vis_status, vis_body), (409, expected))
        self.assertEqual(bound_queue(state, "server"), post_queue)


class AiGroupStateAdmissionTest(unittest.TestCase):
    """O13 residue part + O1/O2 for the state verb at the broker."""

    def _enqueue(self, state: loopback.ServerState, args=None):
        # v13: bound_state attaches a real coordinator, so enqueue needs a
        # parseable identity; the read itself takes no lease token.
        coord = state.coordination
        if coord is None:
            return state.enqueue_command(STATE, args or {"group_id": 7}, peer="server")
        client = getattr(state, "_state_client", None)
        if client is None:
            client = _identity("state")
            state._state_client = client  # type: ignore[attr-defined]
        payload = {
            "platform": client.platform, "pid": client.pid, "ppid": client.ppid,
            "started_at_utc": client.started_at_utc,
            "session_id": client.session_id, "task_label": client.task_label,
        }
        return state.enqueue_command(
            STATE, args or {"group_id": 7}, peer="server",
            identity_payload=payload,
        )

    def _enqueue_order(self, state: loopback.ServerState):
        # The mutation half of O13 reaches the lease-close gate only under
        # a real lease, as the tool layer presents it (B's pattern).
        coord = state.coordination
        if coord is None:
            return state.enqueue_command(
                "ai_group_order", {"group_id": 7, "behaviour": "HOLD"}, peer="server"
            )
        lease = getattr(state, "_state_order_lease", None)
        client = getattr(state, "_state_order_client", None)
        if lease is None or client is None:
            client = _identity("stateord")
            status, lease = coord.acquire(client, "trace")
            assert status == 200, lease
            state._state_order_client = client  # type: ignore[attr-defined]
            state._state_order_lease = lease  # type: ignore[attr-defined]
        payload = {
            "platform": client.platform, "pid": client.pid, "ppid": client.ppid,
            "started_at_utc": client.started_at_utc,
            "session_id": client.session_id, "task_label": client.task_label,
        }
        return state.enqueue_command(
            "ai_group_order", {"group_id": 7, "behaviour": "HOLD"}, peer="server",
            identity_payload=payload, lease_token=lease["lease_token"],
            operation_timeout_s=30.0,
        )

    def test_o13_state_reads_residues_without_lease_close_in_census(self) -> None:
        """Census without lease_close: ai_group_state still enqueues without a
        lease, while a new adapter mutation is refused by the lease-close gate.

        Mutant killed: ai_group_state joining the new-mutation lease-close gate
        (a census announcing no close support would refuse the residue read),
        or the read requiring a lease at enqueue.
        """
        state = _bound_state()
        _announce(
            state,
            _good_commands(lease_close=False),
            server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            GOOD_XAI,
        )
        status, body = self._enqueue(state)
        self.assertEqual(status, 200, body)
        self.assertEqual(len(bound_queue(state, "server")), 1)
        self.assertNotIn(STATE, loopback._EXPANSION_NEW_MUTATION_COMMANDS)
        # Same census without lease_close: a new adapter mutation is refused,
        # while the read is admitted.
        order_status, order_body = self._enqueue_order(state)
        self.assertEqual(order_status, 409)
        self.assertEqual(
            order_body,
            {"error": "expansion_unavailable", "detail": "lease_close_not_supported"},
        )
        self.assertEqual(len(bound_queue(state, "server")), 1)

    def test_o13_real_bridge_v12_poll_is_version_blocked_under_production_validator(
        self,
    ) -> None:
        """Real bridge v12 poll under daemon.build_server_state's production
        version validator is refused with version_blocked before enqueue, while
        a bridge v13 poll announcing no lease_close admits ai_group_state.

        Mutant killed: wiring version_validator=None on the daemon broker path,
        or claiming a real bridge v12 peer reaches residue reads past the
        version gate.
        """
        state = daemon.build_server_state(
            SimpleNamespace(live_profile=False, live_admin_enabled=False),
            "test-key",
            daemon_generation="gen-xai",
        )
        self.assertIsNotNone(state.version_validator)
        state.install_bound_peer(
            instance=INST_SERVER,
            role="server",
            pid=PID_SERVER,
            run_id="xai-version-gate-run",
        )
        state.coordination = SessionCoordinator()
        state.retail_probe = lambda: {"known": True, "processes": []}

        no_close = _good_commands(lease_close=False)
        caps = ",".join(wire_caps(no_close))
        xai = xai_wire(no_close, GOOD_XAI)

        # Negative: a real bridge v12 poll makes the production version gate
        # refuse ai_group_state with version_blocked and queue nothing.
        poll12_status, polled12 = state.record_poll(
            "server",
            "12~1.29",
            instance=INST_SERVER,
            source_pid=PID_SERVER,
            caps=caps,
            ach=server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            xai=xai,
        )
        self.assertEqual(poll12_status, 200, polled12)
        self.assertEqual(polled12["commands"], [])
        blocked_status, blocked_body = self._enqueue(state)
        self.assertEqual(
            (blocked_status, blocked_body),
            (
                409,
                {
                    "error": "version_blocked",
                    "state": "version_mismatch",
                    "expected": EXPECTED_BRIDGE_VERSION,
                    "got": "12",
                    "detail": f"bridge_version '12' != {EXPECTED_BRIDGE_VERSION!r}",
                },
            ),
        )
        self.assertEqual(bound_queue(state, "server"), [])

        # Positive: once a bridge v13 peer polls with the same census omitting
        # lease_close, the production-validator broker admits ai_group_state
        # without a lease while still refusing ai_group_order.
        poll13_status, polled13 = state.record_poll(
            "server",
            f"{EXPECTED_BRIDGE_VERSION}~1.29",
            instance=INST_SERVER,
            source_pid=PID_SERVER,
            caps=caps,
            ach=server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            xai=xai,
        )
        self.assertEqual(poll13_status, 200, polled13)
        ok_status, ok_body = self._enqueue(state)
        self.assertEqual(ok_status, 200, ok_body)
        self.assertEqual(
            [cmd["cmd"] for cmd in bound_queue(state, "server")], [STATE]
        )
        order_status, order_body = self._enqueue_order(state)
        self.assertEqual(
            (order_status, order_body),
            (
                409,
                {
                    "error": "expansion_unavailable",
                    "detail": "lease_close_not_supported",
                },
            ),
        )
        self.assertEqual(
            [cmd["cmd"] for cmd in bound_queue(state, "server")], [STATE]
        )

    def test_state_is_admitted_under_the_live_profile(self) -> None:
        # As a server read the verb is in LIVE_ALLOWED_COMMANDS; the
        # adapter mutations stay outside it (C-r2-H3).
        self.assertIn(STATE, loopback.LIVE_ALLOWED_COMMANDS)
        self.assertNotIn("ai_group_spawn", loopback.LIVE_ALLOWED_COMMANDS)
        self.assertNotIn("ai_group_order", loopback.LIVE_ALLOWED_COMMANDS)

    def test_o1_state_missing_verb_is_adapter_not_loaded(self) -> None:
        # Mutant killed: ai_group_state missing from the expansion
        # admission set (it would read bridge_capability_missing).
        state = _bound_state()
        _announce(
            state,
            _good_commands(state_verb=False),
            server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            GOOD_XAI,
        )
        status, body = self._enqueue(state)
        self.assertEqual(status, 409)
        self.assertEqual(
            body, {"error": "expansion_unavailable", "detail": "adapter_not_loaded"}
        )
        self.assertEqual(bound_queue(state, "server"), [])

    def test_o2_state_bad_xai_is_adapter_contract_mismatch(self) -> None:
        # Mutant killed: the state read skipping the xai check.
        state = _bound_state()
        _announce(
            state,
            _good_commands(),
            server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            "0" * 16,
        )
        status, body = self._enqueue(state)
        self.assertEqual(status, 409)
        self.assertEqual(
            body,
            {"error": "expansion_unavailable", "detail": "adapter_contract_mismatch"},
        )
        self.assertEqual(bound_queue(state, "server"), [])

    def test_state_schema_accepts_exactly_one_id(self) -> None:
        # Mutant killed: a schema variant accepting both keys (the bridge
        # would have to guess which query was meant).
        self.assertEqual(loopback.validate_command_args(STATE, {"group_id": 7}), (True, None))
        self.assertEqual(
            loopback.validate_command_args(STATE, {"scene_id": "scene1"}), (True, None)
        )
        for args in (
            {},
            {"group_id": 7, "scene_id": "scene1"},
            {"group_id": 0},
            {"group_id": True},
            {"group_id": 1.5},
            {"scene_id": "bad scene"},
            {"scene_id": ""},
        ):
            with self.subTest(args=args):
                self.assertIs(loopback.validate_command_args(STATE, args)[0], False)

    def test_o7_comparator_matches_with_and_without_the_state_verb(self) -> None:
        # Mutant killed: ai_group_state demanding its census in the
        # comparator (a run without Expansion would read mismatch).
        registered = frozenset(
            tool
            for tool in server._BRIDGE_COMMAND_TOOLS["server"].values()
            if tool
        )
        base = sorted(server._BRIDGE_COMMAND_TOOLS["server"])
        for commands in ([n for n in base if n != STATE], base):
            with self.subTest(last=commands[-3:]):
                verdict = _compare_bridge_capabilities(
                    "server",
                    {
                        "state": "announced",
                        "reason": "ok",
                        "announced_commands": commands,
                        "announced_arg_contract_hash": (
                            server.EXPECTED_SERVER_ARG_CONTRACT_HASH
                        ),
                    },
                    registered,
                )
                self.assertEqual(verdict["state"], "match", verdict)
        self.assertEqual(
            server._BRIDGE_COMMAND_TOOLS["server"][STATE], STATE
        )


# --- E7: translated Enforce bodies ---------------------------------------

_S = EnforceString.of


def _vector_to_array(value, target) -> None:
    target.Clear()
    target.Insert(value[0])
    target.Insert(value[1])
    target.Insert(value[2])


def _is_valid_scene_id(value) -> bool:
    return isinstance(value, EnforceString) and loopback.is_scene_id(value.text)


def _new_group() -> SimpleNamespace:
    return SimpleNamespace(
        created=False, group_id=0, faction=_S(""), formation=_S(""),
        behaviour=_S(""), behaviour_expansion=_S(""), field=_S(""),
        allowed=EnforceArray(), args_read=None, release=_S(""),
        confirmed=False, released=0, remaining_ids=EnforceArray(),
        members=EnforceArray(), scene_id=_S(""), formation_state=_S(""),
        waypoint_index=0, waypoint_count=0, current_waypoint=EnforceArray(),
        in_combat=False, alive=0, members_total=0, group_present=False,
    )


def _new_member_row() -> SimpleNamespace:
    return SimpleNamespace(
        object_id=0, type=_S(""), pos=EnforceArray(), loadout_applied=False,
        inventory=EnforceArray(), alive=False, gone=False,
        last_observation=False, health=0.0, fsm_state=_S(""),
        fsm_substate=_S(""), threat=0.0, fighting=False, speed_limit=0,
        speed_limit_threat=0, emote_playing=False, emote_pending=False,
    )


class _FakeEmoteManager:
    def __init__(self, *, playing: bool, pending: bool) -> None:
        self.m_bEmoteIsPlaying = playing
        self.m_bEmoteIsRequestPending = pending


class _FakeMemberEntity:
    """An eAIBase double: getters read, every setter is counted."""

    def __init__(
        self,
        *,
        alive: bool = True,
        health: float = 80.0,
        pos=(10.0, 20.0, 30.0),
        fsm=None,
        threat: float = 0.0,
        fighting: bool = False,
        speed_limit: int = 2,
        speed_limit_threat: int = 3,
        emote_manager=None,
        expansion_emote_id: int = 0,
    ) -> None:
        self._alive = alive
        self._health = health
        self._pos = EnforceVector(pos)
        self._fsm = fsm
        self._threat = threat
        self.m_eAI_IsFightingFSM = fighting
        self._speed_limit = speed_limit
        self._speed_limit_threat = speed_limit_threat
        self._emote_manager = emote_manager
        self.m_Expansion_EmoteID = expansion_emote_id
        self.threat_args: list[bool] = []
        self.setter_calls = 0

    def IsAlive(self) -> bool:
        return self._alive

    def GetHealth(self) -> float:
        return self._health

    def GetPosition(self) -> EnforceVector:
        return EnforceVector(self._pos)

    def GetFSM(self):
        return self._fsm

    def GetThreatToSelf(self, ignore_los: bool = False) -> float:
        self.threat_args.append(ignore_los)
        return self._threat

    def GetMovementSpeedLimit(self) -> int:
        return self._speed_limit

    def GetMovementSpeedLimitUnderThreat(self) -> int:
        return self._speed_limit_threat

    def GetEmoteManager(self):
        return self._emote_manager

    # Setter traps: a read that calls any of these fails its test.
    def SetMovementSpeedLimits(self, *_a) -> None:
        self.setter_calls += 1

    def Expansion_SetEmote(self, *_a) -> None:
        self.setter_calls += 1

    def eAI_Despawn(self) -> None:
        self.setter_calls += 1

    def SetGroup(self, *_a) -> None:
        self.setter_calls += 1


class _FakeGroupEntity:
    def __init__(
        self,
        *,
        faction: str = "West",
        formation: str = "Vee",
        formation_state: int = 1,
        waypoint_behaviour: int = 1,
        waypoint_index: int = 2,
        waypoints: int = 3,
        current=(1.0, 2.0, 3.0),
        in_combat: bool = False,
        roaming_local: bool = False,
    ) -> None:
        self._faction = (
            SimpleNamespace(GetName=lambda: _S(faction)) if faction else None
        )
        self._formation = (
            SimpleNamespace(GetName=lambda: _S(formation)) if formation else None
        )
        self._formation_state = formation_state
        self._waypoint_behaviour = waypoint_behaviour
        self.m_CurrentWaypointIndex = waypoint_index
        self._waypoints = EnforceArray(
            [EnforceVector((float(i), 0.0, 0.0)) for i in range(waypoints)]
        )
        self._current = EnforceVector(current)
        self._in_combat = in_combat
        self.m_RoamingLocal = roaming_local
        self.getter_calls = 0
        self.setter_calls = 0

    def GetFaction(self):
        self.getter_calls += 1
        return self._faction

    def GetFormation(self):
        self.getter_calls += 1
        return self._formation

    def GetFormationState(self) -> int:
        self.getter_calls += 1
        return self._formation_state

    def GetWaypointBehaviour(self) -> int:
        self.getter_calls += 1
        return self._waypoint_behaviour

    def GetWaypoints(self) -> EnforceArray:
        self.getter_calls += 1
        return self._waypoints

    def GetCurrentWaypoint(self) -> EnforceVector:
        self.getter_calls += 1
        return EnforceVector(self._current)

    def IsInCombat(self) -> bool:
        self.getter_calls += 1
        return self._in_combat

    def Delete(self) -> None:
        self.setter_calls += 1

    def SetWaypointBehaviour(self, *_a) -> None:
        self.setter_calls += 1


def _fsm(state_name: str | None, sub_name: str | None = None):
    if state_name is None:
        return None
    sub_fsm = None
    if sub_name is not None:
        sub_state = SimpleNamespace(
            GetName=lambda: _S(sub_name), GetSubFSM=lambda: None
        )
        sub_fsm = SimpleNamespace(GetState=lambda: sub_state)
    state = SimpleNamespace(
        GetName=lambda: _S(state_name), GetSubFSM=lambda: sub_fsm
    )
    return SimpleNamespace(GetState=lambda: state)


def _member_record(object_id: int, entity, *, last_pos=(), last_health=0.0, last_alive=False):
    return SimpleNamespace(
        object_id=object_id,
        member=entity,
        speed_normal=2,
        speed_threat=3,
        last_pos=EnforceArray(list(last_pos)),
        last_health=last_health,
        last_alive=last_alive,
    )


def _group_record(
    *,
    group_id: int = 1000000001,
    members: list,
    group=None,
    expansion_id: int = 7,
    scene_id: str = "scene1",
    behaviour: str = "LOOP",
    group_state: str = "active",
):
    return SimpleNamespace(
        spawn_token=_S(TOKEN),
        group_id=group_id,
        group=group,
        expansion_id=expansion_id,
        scene_id=_S(scene_id),
        behaviour=_S(behaviour),
        group_state=_S(group_state),
        members=EnforceArray(members),
    )


def _state_namespace() -> dict:
    source = ADAPTER.read_text(encoding="utf-8")
    registry: dict[str, object] = {"by_id": {}}

    def _lookup(group_id, create):
        # Plan 3.6: the disappearance proof looks up without creating;
        # GetGroupByID(id, true) would register a new eAIGroup.
        if create is not False:
            raise AssertionError(f"GetGroupByID called with create={create!r}")
        return registry["by_id"].get(group_id)

    namespace: dict[str, object] = {
        "_S": EnforceString,
        "m_AIGroups": None,
        "m_RuntimeObjects": EnforceMap(),
        "this": object(),
        "eAIGroup": SimpleNamespace(GetGroupByID=_lookup),
        "eAIGroupFormationState": SimpleNamespace(NONE=0, IN=1, FLANK=2),
        "eAIWaypointBehavior": SimpleNamespace(
            HALT=0, LOOP=1, ALTERNATE=2, ONCE=3, ROAMING=7
        ),
        "typename": SimpleNamespace(
            EnumToString=lambda enum_type, value: _S("ALTERNATE")
        ),
        "VectorToArray": _vector_to_array,
        "IsValidSceneId": _is_valid_scene_id,
        "MCPAIGroup": _new_group,
        "MCPAIGroupMember": _new_member_row,
        "array": EnforceArray,
        "_registry": registry,
    }
    class_types = (
        "MCPAIGroupRecord", "MCPAIGroupMemberRecord", "MCPAIGroup",
        "MCPAIGroupMember", "eAIGroup", "eAIFSM", "ExpansionState",
        "ExpansionFSM", "EmoteManager", "MCPLeaseCloseCensus",
        "MCPLeaseCloseEntry", "Object", "eAIWaypointBehavior",
        "eAIGroupFormationState",
    )
    for signature, members in (
        ("protected void EnsureAIRegistry()", ("m_AIGroups",)),
        ("protected bool AIGroupGone(MCPAIGroupRecord record)", ()),
        ("protected bool AIGroupOriginalPresent(MCPAIGroupRecord record)", ()),
        (
            "protected void FillAIMemberState(MCPAIGroupMemberRecord memberRecord, MCPAIGroupMember row)",
            (),
        ),
        (
            "protected void FillRemainingIds(MCPAIGroupRecord record, MCPAIGroup report)",
            (),
        ),
        (
            "protected void AIFillBehaviour(eAIGroup group, MCPAIGroup report)",
            (),
        ),
        ("protected MCPAIGroup BuildAIGroupState(MCPAIGroupRecord record)", ()),
        (
            "protected MCPAIGroupRecord FindAIGroupById(int groupId)",
            ("m_AIGroups",),
        ),
        (
            "protected void DispatchAIGroupState(MCPCommand command, MCPResult result)",
            ("m_AIGroups",),
        ),
    ):
        translate(source, signature, namespace, members=members, class_types=class_types)
    return namespace


def _dispatch(namespace: dict, *, group_id: int = 0, scene_id: str = ""):
    command = SimpleNamespace(
        args=SimpleNamespace(group_id=group_id, scene_id=_S(scene_id))
    )
    result = SimpleNamespace(ok=False, error=_S(""), ai_group=None, ai_groups=None)
    namespace["DispatchAIGroupState"](command, result)
    return result


def _register(namespace: dict, record, *, present_group=None) -> None:
    namespace["m_AIGroups"] = EnforceArray([record])
    if present_group is not None:
        namespace["_registry"]["by_id"] = {record.expansion_id: present_group}
    else:
        namespace["_registry"]["by_id"] = {}


class AiGroupStateEnforceTest(unittest.TestCase):
    """E7: state through the translated adapter dispatch."""

    def test_e7_member_and_group_reads_come_from_the_getters(self) -> None:
        # Mutants killed: threat read with GetThreatToSelf(false) or via
        # eAITarget, a read calling a setter, and group fields echoed from
        # the record instead of the group's getters.
        namespace = _state_namespace()
        entity = _FakeMemberEntity(
            health=55.0,
            pos=(11.0, 22.0, 33.0),
            fsm=_fsm("Fighting", "FireWeapon"),
            threat=0.7,
            fighting=True,
            speed_limit=1,
            speed_limit_threat=3,
            emote_manager=_FakeEmoteManager(playing=True, pending=False),
        )
        member = _member_record(11, entity)
        group = _FakeGroupEntity(in_combat=True, waypoints=4)
        record = _group_record(members=[member], group=group)
        _register(namespace, record, present_group=group)

        result = _dispatch(namespace, group_id=record.group_id)
        self.assertTrue(result.ok)
        report = result.ai_group
        self.assertEqual(report.group_id, record.group_id)
        self.assertEqual(report.faction.text, "West")
        self.assertEqual(report.formation.text, "Vee")
        self.assertEqual(report.formation_state.text, "IN")
        self.assertEqual(report.behaviour.text, "LOOP")
        self.assertEqual(report.behaviour_expansion.text, "LOOP")
        self.assertEqual(report.waypoint_index, 2)
        self.assertEqual(report.waypoint_count, 4)
        self.assertEqual(list(report.current_waypoint), [1.0, 2.0, 3.0])
        self.assertTrue(report.in_combat)
        self.assertTrue(report.group_present)
        self.assertEqual(report.alive, 1)
        self.assertEqual(report.members_total, 1)
        self.assertEqual(report.release.text, "")

        row = report.members.Get(0)
        self.assertEqual(row.object_id, 11)
        self.assertFalse(row.gone)
        self.assertTrue(row.alive)
        self.assertEqual(list(row.pos), [11.0, 22.0, 33.0])
        self.assertEqual(row.health, 55.0)
        self.assertEqual(row.fsm_state.text, "Fighting")
        self.assertEqual(row.fsm_substate.text, "FireWeapon")
        self.assertEqual(row.threat, 0.7)
        self.assertEqual(entity.threat_args, [True])
        self.assertTrue(row.fighting)
        self.assertEqual((row.speed_limit, row.speed_limit_threat), (1, 3))
        self.assertTrue(row.emote_playing)
        self.assertFalse(row.emote_pending)
        self.assertEqual(entity.setter_calls, 0)
        self.assertEqual(group.setter_calls, 0)
        # The live read refreshed the last observation.
        self.assertEqual(list(member.last_pos), [11.0, 22.0, 33.0])
        self.assertEqual(member.last_health, 55.0)
        self.assertTrue(member.last_alive)

    def test_e7_group_enums_map_every_stored_value(self) -> None:
        # Mutants killed: behaviour echoed from the record or constant
        # ("LOOP"), a swapped behaviour/formation name (HALT read as HOLD,
        # FLANK as IN, ONCE as LOOP), and a constant waypoint_index. The
        # record behaviour is stale ("LOOP") in every case, so only the
        # Expansion read-back through AIFillBehaviour can pass; ALTERNATE
        # has no tool name and reads "" like the spawn/order replies.
        cases = (
            (0, 0, "HOLD", "HALT", "NONE"),
            (1, 1, "LOOP", "LOOP", "IN"),
            (2, 2, "", "ALTERNATE", "FLANK"),
            (3, 1, "ONCE", "ONCE", "IN"),
            (7, 1, "ROAM", "ROAMING", "IN"),
        )
        for value, formation_state, behaviour, expansion, state_name in cases:
            with self.subTest(waypoint_behaviour=value):
                namespace = _state_namespace()
                group = _FakeGroupEntity(
                    waypoint_behaviour=value,
                    formation_state=formation_state,
                    waypoint_index=5,
                )
                record = _group_record(
                    members=[_member_record(11, _FakeMemberEntity())],
                    group=group,
                    behaviour="LOOP",
                )
                _register(namespace, record, present_group=group)
                report = _dispatch(namespace, group_id=record.group_id).ai_group
                self.assertEqual(report.behaviour.text, behaviour)
                self.assertEqual(report.behaviour_expansion.text, expansion)
                self.assertEqual(report.formation_state.text, state_name)
                self.assertEqual(report.waypoint_index, 5)

    def test_e7_gone_keeps_last_observation_and_dead_stays_dead(self) -> None:
        # Mutants killed: a gone member losing its last observation (empty
        # pos / zero health), and a group with no living members erroring
        # instead of answering ok with alive 0.
        namespace = _state_namespace()
        gone = _member_record(
            11, None, last_pos=(1.0, 2.0, 3.0), last_health=42.0, last_alive=True
        )
        dead = _FakeMemberEntity(alive=False, health=0.0, pos=(4.0, 5.0, 6.0))
        dead_record = _member_record(12, dead)
        gone_dead = _member_record(
            13, None, last_pos=(7.0, 8.0, 9.0), last_health=0.0, last_alive=False
        )
        group = _FakeGroupEntity()
        record = _group_record(members=[gone, dead_record, gone_dead], group=group)
        _register(namespace, record, present_group=group)

        result = _dispatch(namespace, group_id=record.group_id)
        self.assertTrue(result.ok)
        self.assertEqual(result.ai_group.alive, 0)
        gone_row, dead_row = result.ai_group.members.Get(0), result.ai_group.members.Get(1)
        self.assertTrue(gone_row.gone)
        self.assertTrue(gone_row.last_observation)
        self.assertEqual(list(gone_row.pos), [1.0, 2.0, 3.0])
        self.assertEqual(gone_row.health, 42.0)
        self.assertTrue(gone_row.alive)
        self.assertFalse(dead_row.gone)
        self.assertFalse(dead_row.alive)
        self.assertEqual(dead.setter_calls, 0)
        gone_dead_row = result.ai_group.members.Get(2)
        self.assertTrue(gone_dead_row.gone)
        self.assertFalse(gone_dead_row.alive)  # seen dead stays dead
        self.assertEqual(list(gone_dead_row.pos), [7.0, 8.0, 9.0])

    def test_e7_null_fsm_references_and_both_emote_layers(self) -> None:
        # Mutants killed: dereferencing a null FSM/state/sub-FSM, and
        # emote_pending reading only one of the two pending layers.
        namespace = _state_namespace()
        no_fsm = _FakeMemberEntity(
            fsm=None,
            emote_manager=_FakeEmoteManager(playing=False, pending=False),
            expansion_emote_id=9,
        )
        no_sub = _FakeMemberEntity(
            fsm=_fsm("Idle"),
            emote_manager=_FakeEmoteManager(playing=False, pending=True),
        )
        group = _FakeGroupEntity()
        record = _group_record(
            members=[_member_record(11, no_fsm), _member_record(12, no_sub)],
            group=group,
        )
        _register(namespace, record, present_group=group)

        result = _dispatch(namespace, group_id=record.group_id)
        self.assertTrue(result.ok)
        first, second = result.ai_group.members.Get(0), result.ai_group.members.Get(1)
        self.assertEqual(first.fsm_state.text, "")
        self.assertEqual(first.fsm_substate.text, "")
        self.assertTrue(first.emote_pending)  # Expansion layer only.
        self.assertFalse(first.emote_playing)
        self.assertEqual(second.fsm_state.text, "Idle")
        self.assertEqual(second.fsm_substate.text, "")
        self.assertTrue(second.emote_pending)  # Vanilla layer only.

    def test_e7_releasing_pending_and_fully_released_is_not_found(self) -> None:
        # Mutants killed: a clean releasing group answering state instead
        # of not_found, and a pending release not naming its pending ids.
        namespace = _state_namespace()
        entity = _FakeMemberEntity()
        group = _FakeGroupEntity()
        record = _group_record(
            members=[_member_record(11, entity)], group=group, group_state="releasing"
        )
        _register(namespace, record, present_group=group)

        result = _dispatch(namespace, group_id=record.group_id)
        self.assertTrue(result.ok)
        self.assertEqual(result.ai_group.release.text, "pending")
        self.assertFalse(result.ai_group.confirmed)
        self.assertEqual(list(result.ai_group.remaining_ids), [11])
        self.assertTrue(result.ai_group.group_present)

        unknown = _dispatch(namespace, group_id=999999)
        self.assertFalse(unknown.ok)
        self.assertEqual(unknown.error.text, "not_found")

        record.members.Get(0).member = None
        namespace["_registry"]["by_id"] = {}
        gone = _dispatch(namespace, group_id=record.group_id)
        self.assertFalse(gone.ok)
        self.assertEqual(gone.error.text, "not_found")

    def test_e7_disappeared_group_getters_are_never_called(self) -> None:
        # Mutant killed: BuildAIGroupState calling the getters of a group
        # Expansion no longer returns (fields must read unavailable, and
        # a reused id must not be read as this group).
        namespace = _state_namespace()
        entity = _FakeMemberEntity()
        group = _FakeGroupEntity()
        record = _group_record(
            members=[_member_record(11, entity)], group=group, group_state="releasing"
        )
        # The registry now returns another group for this id.
        _register(namespace, record, present_group=_FakeGroupEntity())

        result = _dispatch(namespace, group_id=record.group_id)
        self.assertTrue(result.ok)
        self.assertFalse(result.ai_group.group_present)
        self.assertEqual(result.ai_group.faction.text, "")
        self.assertEqual(result.ai_group.formation.text, "")
        self.assertEqual(result.ai_group.formation_state.text, "")
        self.assertEqual(result.ai_group.waypoint_index, -1)
        self.assertEqual(list(result.ai_group.current_waypoint), [])
        self.assertEqual(result.ai_group.release.text, "pending")
        self.assertEqual(group.getter_calls, 0)
        self.assertEqual(result.ai_group.members.Get(0).object_id, 11)

    def test_e7_scene_lists_its_groups_and_dispatch_rejects_two_ids(self) -> None:
        # Mutants killed: the scene read returning only the first group,
        # including a fully released one, or accepting two ids at once.
        namespace = _state_namespace()
        group_a, group_b = _FakeGroupEntity(), _FakeGroupEntity()
        first = _group_record(
            group_id=1000000001,
            members=[_member_record(11, _FakeMemberEntity())],
            group=group_a,
            expansion_id=7,
        )
        second = _group_record(
            group_id=1000000002,
            members=[_member_record(12, _FakeMemberEntity())],
            group=group_b,
            expansion_id=8,
            group_state="releasing",
        )
        other = _group_record(
            group_id=1000000003,
            members=[_member_record(13, _FakeMemberEntity())],
            group=_FakeGroupEntity(),
            expansion_id=9,
            scene_id="other",
        )
        released = _group_record(
            group_id=1000000004,
            members=[_member_record(14, None)],
            group=None,
            expansion_id=10,
            group_state="releasing",
        )
        namespace["m_AIGroups"] = EnforceArray([first, second, other, released])
        namespace["_registry"]["by_id"] = {7: group_a, 8: group_b}

        result = _dispatch(namespace, scene_id="scene1")
        self.assertTrue(result.ok)
        self.assertEqual(
            [row.group_id for row in result.ai_groups],
            [1000000001, 1000000002],
        )
        self.assertEqual(result.ai_groups.Get(1).release.text, "pending")

        empty = _dispatch(namespace, scene_id="no_such_scene")
        self.assertTrue(empty.ok)
        self.assertEqual(list(empty.ai_groups), [])

        both = _dispatch(namespace, group_id=1000000001, scene_id="scene1")
        self.assertFalse(both.ok)
        self.assertEqual(both.error.text, "bad_args")
        neither = _dispatch(namespace)
        self.assertFalse(neither.ok)
        self.assertEqual(neither.error.text, "bad_args")

    def test_e7_state_bodies_call_no_setter_and_no_updating_read(self) -> None:
        # Mutant killed: a state read that mutates (any Set*/Delete/
        # despawn/emote call in its bodies) or reads through eAITarget.
        source = ADAPTER.read_text(encoding="utf-8")
        for signature in (
            "protected void FillAIMemberState(MCPAIGroupMemberRecord memberRecord, MCPAIGroupMember row)",
            "protected MCPAIGroup BuildAIGroupState(MCPAIGroupRecord record)",
            "protected void DispatchAIGroupState(MCPCommand command, MCPResult result)",
        ):
            body = method_body(source, signature)
            for forbidden in (
                ".Set", ".Delete(", "eAI_Despawn", "Expansion_SetEmote",
                "GetPosition(false", ".GetThreat(", "eAITarget",
                "m_Jobs", "new MCPJob",
            ):
                with self.subTest(signature=signature, forbidden=forbidden):
                    self.assertNotIn(forbidden, body)
        # The verb travels as bit3 of the xai mask, not as a caps name.
        verbs = re.search(r'EXPANSION_AI_VERBS = "([0-9]+)"', source)
        self.assertIsNotNone(verbs)
        _hash, names = parse_xai(f"{GOOD_XAI}.{verbs.group(1)}")
        self.assertIn(STATE, names)
        self.assertEqual(EXPANSION_AI_VERB_BITS[3], STATE)
        dispatch = method_body(
            source,
            "protected override int DispatchExtension(MCPCommand command, MCPResult result)",
        )
        self.assertIn('"ai_group_state"', dispatch)
        self.assertIn("DispatchAIGroupState(command, result);", dispatch)
        self.assertIn("return 1;", dispatch)

    def test_adapter_classes_declare_each_field_once(self) -> None:
        # Two adapter PRs adding the same response field merge cleanly in
        # git and then fail Enforce's multiple-declaration check.
        source = ADAPTER.read_text(encoding="utf-8")
        for match in re.finditer(
            r"^(?:modded )?class (\w+)[^\n]*\n\{\n(.*?)^\};", source, re.M | re.S
        ):
            names = re.findall(
                r"^\t(?:ref |protected |private )*[\w<> ,]+? (\w+);$",
                match.group(2),
                re.M,
            )
            with self.subTest(cls=match.group(1)):
                self.assertEqual(
                    sorted({n for n in names if names.count(n) > 1}), []
                )

    def test_e7_extension_dispatch_posts_state_now_with_no_job(self) -> None:
        # Mutant killed: DispatchExtension answering 2 (a job will post) or 0
        # for ai_group_state; no job exists, so the read would hang.
        namespace = _state_namespace()
        calls: list[str] = []
        namespace["DispatchAIGroupRelease"] = lambda c, r: calls.append("release")
        namespace["DispatchAIGroupOrder"] = lambda c, r: calls.append("order")
        inner = namespace["DispatchAIGroupState"]
        namespace["DispatchAIGroupState"] = lambda c, r: (calls.append("state"), inner(c, r))
        translate(
            ADAPTER.read_text(encoding="utf-8"),
            "protected override int DispatchExtension(MCPCommand command, MCPResult result)",
            namespace,
            class_types=("MCPCommand", "MCPResult"),
        )
        command = SimpleNamespace(
            cmd=_S(STATE), args=SimpleNamespace(group_id=0, scene_id=_S("scene1"))
        )
        result = SimpleNamespace(ok=False, error=_S(""), ai_group=None, ai_groups=None)
        namespace["m_AIGroups"] = EnforceArray([])
        self.assertEqual(namespace["DispatchExtension"](command, result), 1)
        self.assertEqual(calls, ["state"])
        self.assertTrue(result.ok)

    def test_e7_roaming_local_comes_from_the_roaming_flag(self) -> None:
        # Mutant killed: a local-roaming group reported as plain ROAMING
        # (SetWaypointBehaviour never stores ROAMING_LOCAL itself).
        namespace = _state_namespace()
        group = _FakeGroupEntity(waypoint_behaviour=7, roaming_local=True)
        record = _group_record(
            members=[_member_record(11, _FakeMemberEntity())], group=group
        )
        _register(namespace, record, present_group=group)
        result = _dispatch(namespace, group_id=record.group_id)
        self.assertTrue(result.ok)
        self.assertEqual(result.ai_group.behaviour_expansion.text, "ROAMING_LOCAL")


if __name__ == "__main__":
    unittest.main()
