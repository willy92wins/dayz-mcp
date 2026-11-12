"""ai_group_spawn (173a): tool shapes (O3), abandoned-spawn release (O5) and
the translated Enforce dispatch (E1-E5, spawn/HOLD part).

Each test names the mutant it kills in its docstring; the commit message
carries the criterion -> test -> mutant table.
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
from dayz_mcp.bridge_readiness import EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceMap,
    EnforceString,
    EnforceVector,
    method_body,
    translate,
)
from tests.client_helpers import _fixture_client_runtime
from tests.fence_helpers import INST_SERVER, PID_SERVER, bound_queue
from tests.xai_helpers import GOOD_XAI, _WireString, _base_commands, wire_caps, xai_wire

SPAWN = "ai_group_spawn"
RELEASE = "ai_group_release"
TOKEN = "b" * 32
ADAPTER = addon_root().parent / "addon_expansion_ai" / "scripts" / "5_Mission" / "MCPExpansionAI.c"
TOOL_ARGS = {
    "faction": "West",
    "count": 2,
    "pos": [1.0, 2.0, 3.0],
    "loadout": "",
    "formation": "Vee",
    "behaviour": "HOLD",
    "waypoints": [],
}


def _tool_error_text(exc: BaseException) -> str:
    text = str(exc)
    nested = getattr(exc, "exceptions", None)
    if nested:
        text = text + " " + str(nested)
    return text


class AIGroupSpawnToolTest(unittest.IsolatedAsyncioTestCase):
    """O3: the real tool entry refuses bad shapes and enqueues nothing."""

    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _refused(self, arguments: dict) -> str:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool(SPAWN, arguments)
        call.assert_not_awaited()
        return _tool_error_text(caught.exception)

    async def _forwarded(self, arguments: dict) -> tuple:
        with patch.object(self.runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            await self.app.call_tool(SPAWN, arguments)
        call.assert_awaited_once()
        return call.await_args.args

    async def test_o3_bad_counts_behaviours_and_shapes_are_bad_args(self) -> None:
        # Mutants killed: count bounds dropped in the tool, behaviour
        # accepted case-insensitively or beyond the four, waypoint/pos
        # shapes reaching the bridge.
        for count in (0, 9, 1.5, True):
            with self.subTest(count=count):
                text = await self._refused({**TOOL_ARGS, "count": count})
                self.assertIn("bad_args", text)
                self.assertIn("count", text)
        for behaviour in ("hold", "PATROL"):
            with self.subTest(behaviour=behaviour):
                text = await self._refused({**TOOL_ARGS, "behaviour": behaviour})
                self.assertIn("bad_args", text)
                self.assertIn("HOLD, ONCE, LOOP, ROAM", text)
        text = await self._refused({**TOOL_ARGS, "waypoints": [[0.0, 0.0, 0.0]] * 33})
        self.assertIn("bad_args", text)
        text = await self._refused({**TOOL_ARGS, "waypoints": [[0.0, float("nan"), 0.0]]})
        self.assertIn("bad_args", text)
        text = await self._refused({**TOOL_ARGS, "pos": [1.0, 2.0]})
        self.assertIn("bad_args", text)
        self.assertIn("pos", text)
        # Integers past the double range raise OverflowError in float();
        # the tool must still answer bad_args and enqueue nothing.
        for huge in (10**400, -(10**400)):
            with self.subTest(pos=huge):
                text = await self._refused({**TOOL_ARGS, "pos": [huge, 0, 0]})
                self.assertIn("bad_args", text)
            with self.subTest(waypoints=huge):
                text = await self._refused({**TOOL_ARGS, "waypoints": [[huge, 0, 0]]})
                self.assertIn("bad_args", text)

    async def test_o3_counts_1_and_8_forward_with_a_fresh_token(self) -> None:
        # Mutant killed: the tool echoing a caller token or sending the
        # spawn to the client peer.
        for count in (1, 8):
            with self.subTest(count=count):
                cmd, args, peer, _timeout = await self._forwarded({**TOOL_ARGS, "count": count})
                self.assertEqual((cmd, peer), (SPAWN, "server"))
                self.assertEqual(args["count"], count)
                self.assertIsNotNone(re.fullmatch(r"[0-9a-f]{32}", args["spawn_token"]))
                self.assertEqual(args["waypoints"], [])

    async def test_o3_waypoint_coordinates_reject_non_wire_numbers_and_forward_finite_floats(
        self,
    ) -> None:
        # Mutants killed: waypoint coordinates coerced through float() without
        # _is_wire_number (bool or numeric string accepted, None raising
        # TypeError instead of bad_args), or finite ints/floats not flattened
        # to wire floats.
        bad_values = (
            True,
            False,
            "2",
            "-1.5",
            None,
            float("nan"),
            float("inf"),
            float("-inf"),
        )
        for bad in bad_values:
            for axis in range(3):
                point: list[object] = [1.0, 2.0, 3.0]
                point[axis] = bad
                with self.subTest(bad=bad, axis=axis):
                    text = await self._refused({**TOOL_ARGS, "waypoints": [point]})
                    self.assertIn("bad_args", text)
                    self.assertIn("waypoints[0]", text)
                    self.assertIn("be [x, y, z] finite numbers", text)
        text = await self._refused(
            {**TOOL_ARGS, "waypoints": [[0.0, 0.0, 0.0], [1.0, True, 3.0]]}
        )
        self.assertIn("bad_args", text)
        self.assertIn("waypoints[1]", text)
        self.assertIn("be [x, y, z] finite numbers", text)
        cmd, args, peer, _timeout = await self._forwarded(
            {
                **TOOL_ARGS,
                "behaviour": "ONCE",
                "waypoints": [[1, 2.5, -3], [0, -0.25, 100.0]],
            }
        )
        self.assertEqual((cmd, peer), (SPAWN, "server"))
        self.assertEqual(args["waypoints"], [1.0, 2.5, -3.0, 0.0, -0.25, 100.0])
        self.assertTrue(all(type(value) is float for value in args["waypoints"]))


def _real_broker_refusal(detail: str) -> dict:
    """The broker's own 409 for ai_group_spawn, from a real ServerState.

    No hand-written payload: the state is bound and announced, the spawn is
    enqueued under a real lease, and the body below is what enqueue_command
    returns (and what its audit records) for each plan 2.2 detail.
    """
    state = _bound_state()
    if detail == "adapter_not_loaded":
        state._peer_caps["server"] = {
            "generation": state.daemon_generation,
            "commands": list(_base_commands()),
            "reason": "ok",
            "arg_contract_hash": server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            "expansion_arg_contract_hash": GOOD_XAI,
            "announced_at": state._now(),
            "instance": INST_SERVER,
        }
    elif detail == "adapter_contract_mismatch":
        _announce(state)
        state._peer_caps["server"]["expansion_arg_contract_hash"] = "0" * 16
    elif detail == "lease_close_not_supported":
        _announce(state)
        state._peer_caps["server"]["commands"] = [
            name for name in _commands() if name != "lease_close"
        ]
    else:  # pragma: no cover - guard against a typo'd detail in this file
        raise AssertionError(detail)
    status, body = _enqueue_spawn(state)
    if status != 409 or body != {"error": "expansion_unavailable", "detail": detail}:
        raise AssertionError((status, body))
    if bound_queue(state, "server"):
        raise AssertionError("a refused spawn was queued")
    return body


class AIGroupSpawnExpansionUnavailableToolTest(unittest.IsolatedAsyncioTestCase):
    """In-game round 7: without the adapter the agent saw remote_error.

    Mutant killed: expansion_unavailable removed from _REMOTE_ERROR_CODES
    (the enqueue mapper collapses the broker's code to remote_error and
    drops its detail, so every assertion on the code/detail below fails).
    """

    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def test_broker_expansion_unavailable_reaches_the_agent_with_its_detail(self) -> None:
        for detail in (
            "adapter_not_loaded",
            "adapter_contract_mismatch",
            "lease_close_not_supported",
        ):
            with self.subTest(detail=detail):
                payload = _real_broker_refusal(detail)
                client = _fixture_client_runtime(
                    server.ServerConfig(
                        mode="client", key="k", port=12345, log_sink=lambda _m: None
                    )
                )
                client._call = lambda *_a, **_k: (409, payload)
                with patch.object(self.runtime, "call_bridge", client.call_bridge):
                    with self.assertRaises(Exception) as caught:
                        await self.app.call_tool(SPAWN, dict(TOOL_ARGS))
                text = _tool_error_text(caught.exception)
                self.assertIn("expansion_unavailable", text)
                self.assertIn(f"detail={detail}", text)
                self.assertNotIn("remote_error", text)

    async def test_unknown_code_and_unknown_detail_at_the_tool(self) -> None:
        cases = (
            ({"error": "arbitrary_remote_text", "detail": "adapter_not_loaded"}, "remote_error"),
            ({"error": "expansion_unavailable", "detail": "C:\\host\\secret"}, "expansion_unavailable"),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload):
                client = _fixture_client_runtime(
                    server.ServerConfig(mode="client", key="k", port=12345, log_sink=lambda _m: None)
                )
                client._call = lambda *_a, _p=payload, **_k: (409, _p)
                with patch.object(self.runtime, "call_bridge", client.call_bridge):
                    with self.assertRaises(Exception) as caught:
                        await self.app.call_tool(SPAWN, dict(TOOL_ARGS))
                text = _tool_error_text(caught.exception)
                self.assertTrue(text.endswith(": " + expected), text)
                self.assertNotIn("detail=", text)
                self.assertNotIn("secret", text)


class AIGroupAdapterErrorSurfaceTest(unittest.TestCase):
    """expansion_unavailable keeps its detail and drops unknown details."""

    def test_an_unknown_expansion_detail_is_dropped_not_echoed(self) -> None:
        payload = {"error": "expansion_unavailable", "detail": "C:\\host\\secret"}
        self.assertEqual(server._public_enqueue_error(payload), "expansion_unavailable")
        error = server._bridge_error({**payload, "ok": 0}, SPAWN)
        self.assertEqual(str(error), "expansion_unavailable")

    def test_a_delivery_discard_keeps_the_expansion_detail(self) -> None:
        # loopback stores a delivery discard as this result; _bridge_error
        # is what the waiting tool raises from it.
        error = server._bridge_error(
            {
                "id": 7,
                "ok": 0,
                "error": "expansion_unavailable",
                "detail": "lease_close_not_supported",
            },
            SPAWN,
        )
        self.assertEqual(
            str(error), "expansion_unavailable; detail=lease_close_not_supported"
        )


def _adapter_spawn_refusal(field: str, allowed: list, error: str = "bad_args") -> dict:
    """The ok=0 result DispatchAIGroupSpawn posts for a refusal.

    Built from MCPExpansionAI.c: report.args_read is assigned before any
    check (:727), faction/count at :735-736 and waypoints at :737-745;
    AISpawnRefuse (:688-695) sets created=false (:690) and field (:691).
    faction fills allowed via AIFillFactionAllowed (:457-471) called at
    :827 before refusing at :828; formation via AIFillFormationAllowed
    (:473-487) called at :845 before :846; loadout refuses at :861/:867
    with no allowed fill, so allowed stays the empty array from the
    MCPAIGroup constructor (:56). living/limit are only set for
    ai_limit_reached at :889-890 before :891.
    """
    return {
        "ok": 0,
        "error": error,
        "ai_group": {
            "created": False,
            "field": field,
            "allowed": list(allowed),
            "args_read": {
                "faction": "Nope",
                "count": 2,
                "waypoints": [10.0, 2.0, 20.0, 30.0, 2.0, 40.0],
            },
            "living": 20 if error == "ai_limit_reached" else 0,
            "limit": 24 if error == "ai_limit_reached" else 0,
        },
    }


class AIGroupSpawnBadArgsDetailToolTest(unittest.IsolatedAsyncioTestCase):
    """Round B: a spawn bad_args reaches the agent with its report.

    Mutant killed: the ai_group_spawn branch of _bridge_error_detail
    removed (as in b8fb379) -- every POS assertion below collapses to the
    bare code. The bridge result arrives as wait_for_result raises it
    (server.py wait_for_result: not ok -> _bridge_error(result, cmd)).
    """

    async def asyncSetUp(self) -> None:
        self.app, self.runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )

    async def _refusal_text(self, result: dict, arguments: dict | None = None) -> str:
        async def fake_call_bridge(cmd, _args, _peer, _timeout, _result=result):
            if not _result.get("ok"):
                raise server._bridge_error(_result, cmd)
            return _result

        with patch.object(
            self.runtime, "call_bridge", new=AsyncMock(side_effect=fake_call_bridge)
        ):
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool(SPAWN, arguments or dict(TOOL_ARGS))
        return _tool_error_text(caught.exception)

    async def test_pos_faction_formation_and_loadout_carry_the_report(self) -> None:
        args = {**TOOL_ARGS, "faction": "Nope", "waypoints": [[10.0, 2.0, 20.0], [30.0, 2.0, 40.0]]}
        text = await self._refusal_text(_adapter_spawn_refusal("faction", FACTIONS), args)
        self.assertIn("bad_args", text)
        self.assertIn("field=faction", text)
        self.assertIn("allowed=West,East", text)
        self.assertIn(
            "args_read=faction=Nope count=2 waypoints=10.0,2.0,20.0,30.0,2.0,40.0", text
        )
        self.assertIn("created=false", text)

        text = await self._refusal_text(_adapter_spawn_refusal("formation", FORMATIONS), args)
        self.assertIn("field=formation", text)
        self.assertIn("allowed=" + ",".join(FORMATIONS), text)
        self.assertIn("args_read=faction=Nope count=2", text)
        self.assertIn("created=false", text)

        text = await self._refusal_text(_adapter_spawn_refusal("loadout", []), args)
        self.assertIn("field=loadout", text)
        self.assertNotIn("allowed=", text)
        self.assertIn("args_read=faction=Nope count=2", text)
        self.assertIn("created=false", text)

    async def test_pos_ai_limit_reached_carries_living_limit_and_created(self) -> None:
        result = _adapter_spawn_refusal("", [], error="ai_limit_reached")
        text = await self._refusal_text(result)
        self.assertIn("ai_limit_reached", text)
        self.assertIn("living=20", text)
        self.assertIn("limit=24", text)
        self.assertIn("created=false", text)

    async def test_neg_unlisted_field_and_values_do_not_cross(self) -> None:
        result = _adapter_spawn_refusal("secret_field", ["West", "C:\\host\\secret"])
        result["ai_group"]["args_read"]["faction"] = "Nope; rm -rf"
        text = await self._refusal_text(result)
        self.assertNotIn("secret_field", text)
        self.assertNotIn("field=", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("rm -rf", text)
        self.assertIn("allowed=West", text)
        self.assertIn("created=false", text)
        # Scope: the same report under a verb without a branch stays
        # bare (ai_group_order has its own field/allowed branch, #340).
        other = server._bridge_error(result, "ai_group_state")
        self.assertEqual(str(other), "bad_args")
        timeout = _adapter_spawn_refusal("", [], error="timeout")
        self.assertEqual(str(server._bridge_error(timeout, SPAWN)), "timeout")

    async def test_neg_bad_args_does_not_carry_living_or_limit(self) -> None:
        # The adapter serializes living=0 and limit=0 on every refusal
        # (MCPAIGroup ints, MCPExpansionAI.c:44-45); only ai_limit_reached
        # sets them (:889-890).
        result = _adapter_spawn_refusal("faction", FACTIONS)
        result["ai_group"]["living"] = 7
        result["ai_group"]["limit"] = 9
        text = await self._refusal_text(result)
        self.assertIn("field=faction", text)
        self.assertNotIn("living=", text)
        self.assertNotIn("limit=", text)

    async def test_neg_oversized_values_are_capped(self) -> None:
        result = _adapter_spawn_refusal("faction", ["Z" * 65] + [f"F{i}" for i in range(200)])
        result["ai_group"]["args_read"]["faction"] = "Q" * 65
        result["ai_group"]["args_read"]["waypoints"] = [1.0] * 97
        text = await self._refusal_text(result)
        allowed = re.search(r"allowed=([^;]*)", text).group(1).split(",")
        self.assertEqual(allowed, [f"F{i}" for i in range(63)])
        self.assertNotIn("Z" * 65, text)
        self.assertNotIn("Q" * 65, text)
        self.assertNotIn("waypoints=", text)
        self.assertIn("args_read=count=2", text)

    async def test_neg_bad_args_without_ai_group_stays_bare(self) -> None:
        text = await self._refusal_text({"ok": 0, "error": "bad_args"})
        self.assertTrue(text.rstrip().endswith("bad_args"), text)
        self.assertNotIn("field=", text)
        self.assertNotIn("created=", text)


class AIGroupSpawnDetailDocsTest(unittest.TestCase):
    """PX8 c: the guide and the 173a entry say what a refusal's message carries."""

    def test_guide_and_changelog_name_the_refusal_detail(self) -> None:
        root = _TOOLS_DIR.parent
        guide = (root / "docs" / "expansion-ai-adapter.md").read_text(encoding="utf-8")
        start = guide.index("- `ai_group_spawn` (tool, lease required)")
        bullet = guide[start : guide.index("\n- ", start)]
        entries = [
            line
            for line in (root / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
            if line.startswith("- Expansion AI adapter foundation and `ai_group_spawn`")
        ]
        self.assertEqual(len(entries), 1)
        for text in (bullet, entries[0]):
            for name in ("`field`", "`allowed`", "`args_read`", "`living`", "`limit`", "`created`"):
                self.assertIn(name, text)


def _bound_state() -> loopback.ServerState:
    state = loopback.ServerState(key="k")
    state.daemon_generation = "gen-xai-spawn"
    state.install_bound_peer(instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="xai-run")
    # Real §2.12 path: attaching the coordinator replays the BOUND peer
    # into its registry, so the §5.1 gate sees it registered.
    from dayz_mcp.session_coordination import SessionCoordinator

    state.coordination = SessionCoordinator()
    state.retail_probe = lambda: {"known": True, "processes": []}
    return state


def _enqueue_spawn(state: loopback.ServerState):
    # Enqueue under a real lease from the attached coordinator, as the
    # tool layer does; the coordinator was attached in _bound_state.
    from tests.lease_helpers import _identity

    lease = getattr(state, "_spawn_lease", None)
    client = getattr(state, "_spawn_client", None)
    if lease is None or client is None:
        client = _identity("spawn")
        coord = state.coordination
        assert coord is not None
        status, lease = coord.acquire(client, "trace")
        assert status == 200, lease
        state._spawn_client = client  # type: ignore[attr-defined]
        state._spawn_lease = lease  # type: ignore[attr-defined]
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


def _commands() -> list[str]:
    # v13: the base census already announces lease_close; never duplicate it.
    return list(dict.fromkeys([*_base_commands(), RELEASE, SPAWN, "lease_close"]))


def _announce(state: loopback.ServerState) -> None:
    state._peer_caps["server"] = {
        "generation": state.daemon_generation,
        "commands": _commands(),
        "reason": "ok",
        "arg_contract_hash": server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
        "expansion_arg_contract_hash": GOOD_XAI,
        "announced_at": state._now(),
        "instance": INST_SERVER,
    }


def _delivered(state: loopback.ServerState) -> list[dict]:
    status, polled = state.record_poll(
        "server",
        f"{EXPECTED_BRIDGE_VERSION}~1.29",
        instance=INST_SERVER,
        source_pid=PID_SERVER,
        caps=",".join(wire_caps(_commands())),
        ach=server.EXPECTED_SERVER_ARG_CONTRACT_HASH,
        xai=xai_wire(_commands()),
    )
    if status != 200:
        raise AssertionError(polled)
    return list(polled["commands"])


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


class AIGroupSpawnAbandonTest(unittest.TestCase):
    """O5: an abandoned delivered spawn queues its release by token."""

    def test_o5_delivered_abandoned_spawn_queues_release_behind_it(self) -> None:
        # Mutants killed: the release naming group_id, the release jumping
        # ahead of its spawn, and a release for a spawn never delivered or
        # already answered.
        state = _bound_state()
        _announce(state)
        status, spawn = _enqueue_spawn(state)
        self.assertEqual(status, 200, spawn)
        self.assertEqual([c["id"] for c in _delivered(state)], [spawn["id"]])
        abandoned = state.abandon_command(spawn["id"], "tool_timeout")
        self.assertEqual(abandoned.get("status"), "release_queued", abandoned)
        self.assertEqual(
            [(c["cmd"], c["args"]) for c in _delivered(state)],
            [(RELEASE, {"spawn_token": TOKEN})],
        )
        # Not delivered: dropped, no release.
        state2 = _bound_state()
        _announce(state2)
        status, queued = _enqueue_spawn(state2)
        self.assertEqual(status, 200, queued)
        self.assertEqual(state2.abandon_command(queued["id"], "cancelled"), {"status": "dropped"})
        self.assertEqual(_delivered(state2), [])
        # Result already stored: nothing queued; the lease end owns it.
        state3 = _bound_state()
        _announce(state3)
        status, done = _enqueue_spawn(state3)
        self.assertEqual(status, 200, done)
        self.assertEqual([c["id"] for c in _delivered(state3)], [done["id"]])
        state3.store_result({"id": done["id"], "ok": 1}, instance=INST_SERVER)
        self.assertEqual(state3.abandon_command(done["id"], "tool_timeout"), {"status": "noop"})
        self.assertEqual(_delivered(state3), [])

    def test_close_support_lost_before_delivery_discards_the_spawn(self) -> None:
        # H7/O13 delivery half: the close gate is re-read at delivery.
        state = _bound_state()
        _announce(state)
        status, spawn = _enqueue_spawn(state)
        self.assertEqual(status, 200, spawn)
        commands = [c for c in _commands() if c != "lease_close"]
        status, polled = state.record_poll(
            "server", f"{EXPECTED_BRIDGE_VERSION}~1.29", instance=INST_SERVER,
            source_pid=PID_SERVER, caps=",".join(wire_caps(commands)),
            ach=server.EXPECTED_SERVER_ARG_CONTRACT_HASH, xai=xai_wire(commands),
        )
        self.assertEqual((status, polled["commands"]), (200, []))
        stored = state.take_result(spawn["id"])
        self.assertEqual((stored["error"], stored["detail"]),
                         ("expansion_unavailable", "lease_close_not_supported"))
        self.assertEqual(
            str(server._bridge_error(stored, SPAWN)),
            "expansion_unavailable; detail=lease_close_not_supported",
        )

    def test_spawn_without_close_support_is_inert(self) -> None:
        # Mutant killed: ai_group_spawn admitted while no peer announces
        # lease_close (the v13 gate of plan 5.1).
        state = _bound_state()
        _announce(state)
        state._peer_caps["server"]["commands"] = [c for c in _commands() if c != "lease_close"]
        status, body = _enqueue_spawn(state)
        self.assertEqual(status, 409)
        self.assertEqual(body, {"error": "expansion_unavailable", "detail": "lease_close_not_supported"})
        self.assertEqual(bound_queue(state, "server"), [])


# --- E1-E5: translated Enforce dispatch -----------------------------------

FACTIONS = ["West", "East"]
FORMATIONS = [
    "Column", "InvColumn", "File", "InvFile", "Vee", "InvVee",
    "Wall", "Circle", "CircleDot", "Star", "StarDot",
]


class _FakeTypenames:
    def __init__(self, names: list[str], prefix: str) -> None:
        self._items = EnforceArray([_Typename(prefix + name) for name in names])

    def Count(self) -> int:
        return self._items.Count()

    def GetElement(self, index: int):
        return self._items.Get(index)

    def Get(self, index: int):
        return self._items.Get(index)


class _FakeFaction:
    def __init__(self, name: str, read_name: str | None = None) -> None:
        self._name = name
        self._read = read_name if read_name is not None else name

    def GetName(self):
        return EnforceString.of(self._read)

    def GetDefaultLoadout(self):
        return EnforceString.of("DefaultLoadout")


class _FakeFormation:
    def __init__(self, name: str) -> None:
        self._name = name

    def GetName(self):
        return EnforceString.of(self._name)


class _FakeInventory:
    def __init__(self, types: list[str]) -> None:
        self._types = types

    def AttachmentCount(self) -> int:
        return len(self._types)

    def GetAttachmentFromIndex(self, index: int):
        return SimpleNamespace(GetType=lambda: EnforceString.of(self._types[index]))


class _FakeMember:
    def __init__(self, normal: int = 2, threat: int = 3) -> None:
        self.normal = normal
        self.threat = threat
        self.limits: list[tuple] = []
        self.looting: list = []
        self.group = None
        self.despawns = 0
        self.alive = True

    def GetType(self):
        return EnforceString.of("eAI_SurvivorM_Test")

    def GetPosition(self):
        return EnforceVector((10.0, 20.0, 30.0))

    def GetHealth01(self) -> float:
        return 1.0

    def GetHealth(self) -> float:
        return 100.0

    def IsAlive(self) -> bool:
        return self.alive

    def GetInventory(self):
        return _FakeInventory(["AKM", "Bandage"])

    def SetGroup(self, group) -> None:
        self.group = group

    def eAI_SetLootingBehavior(self, value) -> None:
        self.looting.append(value)

    def GetMovementSpeedLimit(self) -> int:
        return self.normal

    def GetMovementSpeedLimitUnderThreat(self) -> int:
        return self.threat

    def SetMovementSpeedLimits(self, normal, threat) -> None:
        self.limits.append((normal, threat))
        self.normal = normal

    def eAI_Despawn(self) -> None:
        self.despawns += 1


class _FakeGroup:
    NEXT_ID = 7

    def __init__(self, faction: _FakeFaction) -> None:
        self._faction = faction
        self._formation = _FakeFormation("Vee")
        self.m_Persist = True
        self.m_BackTracking = True
        self.deletes = 0
        self.waypoints: list = []
        self.current = None
        self.formation_state = None
        self.waypoint_behaviour = 0
        self._id = _FakeGroup.NEXT_ID

    def GetID(self) -> int:
        return self._id

    def GetFaction(self):
        return self._faction

    def GetFormation(self):
        return self._formation

    def SetFormation(self, formation) -> None:
        self._formation = formation

    def ClearWaypoints(self) -> None:
        self.waypoints = []

    def AddWaypoint(self, pos) -> None:
        self.waypoints.append(pos)

    def SetCurrentWaypoint(self, pos) -> None:
        self.current = pos

    def SetFormationState(self, value) -> None:
        self.formation_state = value

    def SetWaypointBehaviour(self, value) -> None:
        self.waypoint_behaviour = value

    def GetWaypointBehaviour(self):
        return self.waypoint_behaviour

    def Delete(self) -> None:
        self.deletes += 1


class _Typename:
    def __init__(self, name: str) -> None:
        self._name = name

    def ToString(self):
        return EnforceString.of(self._name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Typename) and other._name == self._name

    def __hash__(self) -> int:
        return hash(self._name)


def _report() -> SimpleNamespace:
    return SimpleNamespace(
        created=False, group_id=0, faction=EnforceString.of(""), formation=EnforceString.of(""),
        behaviour=EnforceString.of(""), behaviour_expansion=EnforceString.of(""),
        field=EnforceString.of(""), living=0, limit=0, allowed=EnforceArray(), args_read=None,
        release=EnforceString.of(""), confirmed=False, released=0,
        remaining_ids=EnforceArray(), members=EnforceArray(),
    )


def _record(**overrides) -> SimpleNamespace:
    record = SimpleNamespace(
        spawn_token=EnforceString.of(TOKEN), group_id=0, group=None, expansion_id=7,
        scene_id=EnforceString.of(""), behaviour=EnforceString.of("HOLD"),
        group_state=EnforceString.of("active"), waypoints=EnforceArray(), members=EnforceArray(),
    )
    for key, value in overrides.items():
        setattr(record, key, value)
    return record


def _member_record(entity, object_id: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        object_id=object_id, member=entity, type=EnforceString.of("eAI_SurvivorM_Test"),
        loadout_applied=True, speed_normal=2, speed_threat=3,
        last_pos=EnforceArray([10.0, 20.0, 30.0]), last_health=1.0, last_alive=True,
    )


def _spawn_namespace(*, files=(), parse_ok=None, fail_at=None, read_name=None):
    source = ADAPTER.read_text(encoding="utf-8")
    state: dict = {
        "created_objects": 0, "groups": [], "apply": [], "file_exist": [], "load": [],
        "scene_entries": [], "deleted_objects": 0, "scene_size": 0, "members": [],
    }
    parseable = set(files) if parse_ok is None else set(parse_ok)

    def create_object(type_name, pos):
        index = state["created_objects"]
        state["created_objects"] += 1
        if fail_at is not None and index == fail_at:
            return None
        member = _FakeMember()
        state["members"].append(member)
        return member

    def create_group(faction):
        group = _FakeGroup(faction)
        state["groups"].append(group)
        return group

    game = SimpleNamespace(
        CreateObject=create_object,
        ObjectDelete=lambda obj: state.__setitem__("deleted_objects", state["deleted_objects"] + 1),
    )
    namespace: dict = {
        "_S": _WireString,
        "array": EnforceArray,
        "m_AIGroups": None,
        "m_RuntimeObjects": EnforceMap(),
        "m_NextBatchObjectId": 1000000000,
        "AI_GROUP_COUNT_MAX": 8,
        "AI_TOTAL_MAX": 24,
        "AI_WAYPOINTS_MAX": 32,
        "AI_MEMBER_RING_M": 1.5,
        "Math": SimpleNamespace(PI2=math.tau, Cos=math.cos, Sin=math.sin),
        "Vector": lambda x, y, z: EnforceVector((x, y, z)),
        "GetGame": lambda: game,
        "FileExist": lambda path: (state["file_exist"].append(path.text if isinstance(path, EnforceString) else str(path)), (path.text if isinstance(path, EnforceString) else str(path)) in {f"$profile:ExpansionMod/Loadouts/{name}.json" for name in files})[1],
        "IsFiniteFloat": lambda value: isinstance(value, (int, float)) and math.isfinite(float(value)),
        "SceneSpawnRefusal": lambda args, count: EnforceString.of("" if not args.scene_id or args.scene_id == EnforceString.of("") else ("bad_args" if args.scene_id == EnforceString.of("bad scene!") else ("scene_full" if state["scene_size"] + count > 2048 else ""))),
        "AddSceneEntry": lambda scene_id, object_id, obj: state["scene_entries"].append((object_id, obj)),
        "MCPAIGroup": _report,
        "MCPAIArgsRead": lambda: SimpleNamespace(faction=EnforceString.of(""), count=0, waypoints=EnforceArray()),
        "MCPAIGroupRecord": lambda: _record(group=None),
        "MCPAIGroupMemberRecord": lambda: _member_record(None),
        "MCPAIGroupMember": lambda: SimpleNamespace(object_id=0, type=EnforceString.of(""), pos=EnforceArray(), loadout_applied=False, inventory=EnforceArray(), alive=False),
        "MCPLeaseCloseEntry": lambda: SimpleNamespace(name=None, state=None, reason=None),
        "eAIRegisterFaction": SimpleNamespace(s_FactionTypes=_FakeTypenames(FACTIONS, "eAIFaction")),
        "eAIFaction": SimpleNamespace(
            GetType=lambda name: _Typename("eAIFaction" + (name.text if isinstance(name, EnforceString) else str(name))),
            Create=lambda name: _FakeFaction(name.text if isinstance(name, EnforceString) else str(name), read_name),
        ),
        "eAIFormation": SimpleNamespace(
            s_FormationTypes=_FakeTypenames(FORMATIONS, "eAIFormation"),
            # eAIFormation.c:123: "" and RANDOM give a random sane formation.
            GetType=lambda name: (lambda n: _Typename("eAIFormationColumn") if n in ("", "RANDOM") else (_Typename("eAIFormation" + n) if n in FORMATIONS else None))(name.text if isinstance(name, EnforceString) else str(name)),
            Create=lambda name: _FakeFormation(name.text if isinstance(name, EnforceString) else str(name)),
        ),
        "eAIGroup": SimpleNamespace(
            CreateGroup=create_group,
            GetGroupByID=lambda group_id, create: next((g for g in state["groups"] if g.GetID() == group_id), None),
        ),
        "eAIBase": SimpleNamespace(Cast=lambda obj: obj if isinstance(obj, _FakeMember) else None),
        "eAISurvivor": SimpleNamespace(GetQuasiRandom=lambda: EnforceString.of("eAI_SurvivorM_Test")),
        "ExpansionAISpawnBase": SimpleNamespace(GetPlacementPosition=lambda pos: pos),
        # Real Load returns its prefab even for a file that does not parse.
        "EXPANSION_LOADOUT_FOLDER": EnforceString.of("$profile:ExpansionMod/Loadouts/"),
        "ExpansionPrefab": SimpleNamespace(
            Load=lambda name, force: object(),
            GetPath=lambda name, folder=None: EnforceString.of((folder.text if isinstance(folder, EnforceString) else "$profile:ExpansionMod/Loadouts/") + (name.text if isinstance(name, EnforceString) else str(name)) + ".json"),
        ),
        "AILoadoutParses": lambda path: (state["load"].append((path.text, True)), path.text in {f"$profile:ExpansionMod/Loadouts/{name}.json" for name in parseable})[1],
        "ExpansionHumanLoadout": SimpleNamespace(Apply=lambda member, name, force: (state["apply"].append((name.text if isinstance(name, EnforceString) else str(name), force)), True)[1]),
        "eAIWaypointBehavior": SimpleNamespace(HALT=0, LOOP=1, ONCE=3, ROAMING=7),
        "typename": SimpleNamespace(EnumToString=lambda enum_type, value: EnforceString.of("ALTERNATE")),
        "eAIGroupFormationState": SimpleNamespace(NONE=0, IN=1),
        "eAIMovementSpeed": SimpleNamespace(STATIC=0, WALK=1, JOG=2, SPRINT=3),
        "eAILootingBehavior": SimpleNamespace(NONE=0),
        "MCPBotControl": SimpleNamespace(OnObjectGone=lambda bridge, object_id: None),
        "this": object(),
        "_state": state,
    }
    class_types = ("MCPAIGroupRecord", "MCPAIGroupMemberRecord", "MCPAIGroup", "MCPAIArgsRead", "MCPAIGroupMember", "eAIGroup", "eAIBase", "eAIFaction", "Object", "MCPLeaseCloseCensus", "MCPLeaseCloseEntry", "typename", "GameInventory", "EntityAI", "eAIWaypointBehavior", "MCPArgs", "MCPCommand", "MCPResult")
    members = ("m_AIGroups", "m_RuntimeObjects", "m_NextBatchObjectId", "AI_GROUP_COUNT_MAX", "AI_TOTAL_MAX", "AI_WAYPOINTS_MAX", "AI_MEMBER_RING_M")
    for signature in (
        "protected void EnsureAIRegistry()",
        "protected MCPAIGroupRecord FindAIGroupByToken(string spawnToken)",
        "protected bool AIGroupGone(MCPAIGroupRecord record)",
        "protected bool AIGroupRequestRelease(MCPAIGroupRecord record)",
        "protected bool AIIsSpawnToken(string value)",
        "protected string AINormalizeLoadout(string name)",
        "protected bool AIIsBehaviour(string value)",
        "protected bool AIFactionRegistered(typename factionType)",
        "protected void AIFillFactionAllowed(MCPAIGroup report)",
        "protected string AIFormationName(int index)",
        "protected bool AIFormationKnown(string formation)",
        "protected void AIFillFormationAllowed(MCPAIGroup report)",
        "protected bool AIWaypointsOk(array<float> waypoints, int minPoints)",
        "protected int AICountLivingMembers()",
        "protected void AIGroupApplyRoute(MCPAIGroupRecord record)",
        "protected void AIGroupApplyMode(MCPAIGroupRecord record)",
        "protected void AIGroupApplyLimits(MCPAIGroupRecord record)",
        "protected void AIFillBehaviour(eAIGroup group, MCPAIGroup report)",
        "protected void AIFillGroupReport(MCPAIGroupRecord record, MCPAIGroup report)",
        "protected void AISpawnRefuse(MCPResult result, MCPAIGroup report, string errorName, string fieldName)",
        "protected void DispatchAIGroupSpawn(MCPCommand command, MCPResult result)",
    ):
        translate(source, signature, namespace, members=members, class_types=class_types)
    return namespace, state


def _args(**overrides) -> SimpleNamespace:
    args = SimpleNamespace(
        faction=EnforceString.of("West"), count=2,
        pos=EnforceArray([1.0, 2.0, 3.0]), loadout=EnforceString.of(""),
        formation=EnforceString.of("Vee"), behaviour=EnforceString.of("HOLD"),
        waypoints=EnforceArray(), spawn_token=EnforceString.of(TOKEN),
        scene_id=EnforceString.of(""),
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _dispatch(namespace, args) -> SimpleNamespace:
    result = SimpleNamespace(ai_group=None, ok=False, error=EnforceString.of(""))
    namespace["DispatchAIGroupSpawn"](SimpleNamespace(args=args), result)
    return result


def _apply_spawn_behaviour(namespace, record) -> None:
    namespace["AIGroupApplyRoute"](record)
    namespace["AIGroupApplyMode"](record)
    namespace["AIGroupApplyLimits"](record)


class AIGroupSpawnEnforceTest(unittest.TestCase):
    """E1-E5 (spawn part): the real dispatch against Expansion doubles."""

    def test_e1_unknown_faction_and_formation_create_nothing(self) -> None:
        # Mutants killed: falling back to a default faction or to Vee.
        namespace, state = _spawn_namespace(files={"DefaultLoadout"})
        result = _dispatch(namespace, _args(faction=EnforceString.of("Nope")))
        self.assertEqual(result.error.text, "bad_args")
        self.assertEqual(result.ai_group.field.text, "faction")
        self.assertFalse(result.ai_group.created)
        self.assertEqual([a.text for a in result.ai_group.allowed], FACTIONS)
        self.assertEqual(result.ai_group.args_read.faction.text, "Nope")
        self.assertEqual((state["created_objects"], len(state["groups"])), (0, 0))
        result = _dispatch(namespace, _args(formation=EnforceString.of("Nope")))
        self.assertEqual(result.ai_group.field.text, "formation")
        self.assertEqual([a.text for a in result.ai_group.allowed], FORMATIONS)
        self.assertEqual((state["created_objects"], len(state["groups"])), (0, 0))
        for name in ("", "RANDOM"):
            result = _dispatch(namespace, _args(formation=EnforceString.of(name)))
            self.assertEqual(result.ai_group.field.text, "formation", name)
        self.assertEqual((state["created_objects"], len(state["groups"])), (0, 0))

    def test_e1_loadout_normalization_and_cached_failure(self) -> None:
        # Mutants killed: accepting the prefab Load cached from a failed
        # attempt, and normalizing .json on only one of the three names.
        namespace, state = _spawn_namespace(files={"X"})
        for _ in range(2):
            result = _dispatch(namespace, _args(loadout=EnforceString.of("Ghost")))
            self.assertEqual(result.ai_group.field.text, "loadout")
        self.assertTrue(all(force for _name, force in state["load"]))
        result = _dispatch(namespace, _args(loadout=EnforceString.of("X.json")))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(state["file_exist"][-1], "$profile:ExpansionMod/Loadouts/X.json")
        self.assertEqual(state["load"][-1], ("$profile:ExpansionMod/Loadouts/X.json", True))
        self.assertEqual(state["apply"][-1], ("X", True))
        namespace2, _state2 = _spawn_namespace(files={"Y"}, parse_ok=set())
        result = _dispatch(namespace2, _args(loadout=EnforceString.of("Y")))
        self.assertEqual(result.ai_group.field.text, "loadout")

    def test_e2_total_cap_and_repeat_token(self) -> None:
        # Mutants killed: the cap counting only this group, and the repeat
        # check sitting after the cap.
        namespace, state = _spawn_namespace(files={"DefaultLoadout"})
        members = EnforceArray([_member_record(_FakeMember()) for _ in range(20)])
        namespace["m_AIGroups"] = EnforceArray([_record(members=members, group=_FakeGroup(_FakeFaction("West")))])
        namespace["_state"] = state
        result = _dispatch(namespace, _args(count=5, spawn_token=EnforceString.of("c" * 32)))
        self.assertEqual(result.error.text, "ai_limit_reached")
        self.assertEqual((result.ai_group.living, result.ai_group.limit), (20, 24))
        self.assertEqual(state["created_objects"], 0)
        # Repeat of an existing token answers that group, never the cap.
        members2 = EnforceArray([_member_record(_FakeMember()) for _ in range(24)])
        group = _FakeGroup(_FakeFaction("West"))
        namespace["m_AIGroups"] = EnforceArray([_record(members=members2, group=group)])
        result = _dispatch(namespace, _args(count=1))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(state["created_objects"], 0)

    def test_e3_good_spawn_registers_members_and_reads_back(self) -> None:
        # Mutants killed: the group entering m_RuntimeObjects or the scene,
        # and the reply echoing args instead of the group's getters.
        namespace, state = _spawn_namespace(files={"DefaultLoadout"}, read_name="WestRead")
        result = _dispatch(namespace, _args(scene_id=EnforceString.of("scene1")))
        self.assertTrue(result.ok, result.error)
        group = state["groups"][0]
        self.assertFalse(group.m_Persist)
        self.assertEqual(state["created_objects"], 2)
        members = state["members"]
        self.assertTrue(all(m.group is group for m in members))
        self.assertTrue(all(m.looting == [0] for m in members))
        runtime = namespace["m_RuntimeObjects"]
        self.assertEqual(runtime.Count(), 2)
        self.assertFalse(runtime.Contains(result.ai_group.group_id))
        self.assertEqual(len(state["scene_entries"]), 2)
        self.assertEqual(result.ai_group.faction.text, "WestRead")
        self.assertEqual(result.ai_group.formation.text, "Vee")
        self.assertEqual([m.object_id for m in result.ai_group.members], [1000000001, 1000000002])
        self.assertEqual(result.ai_group.group_id, 1000000000)
        self.assertTrue(all(m.loadout_applied for m in result.ai_group.members))
        self.assertEqual(result.ai_group.members[0].inventory[0].text, "AKM")
        # H14: args_read is published only on refusals, not on success.
        self.assertIsNone(result.ai_group.args_read)
        # H16: the last observation uses GetHealth(), the scale state reads.
        record = namespace["m_AIGroups"].Get(0)
        self.assertEqual(record.members.Get(0).last_health, 100.0)

    def test_e3_scene_full_and_bad_scene_create_nothing(self) -> None:
        # Mutant killed: the scene check running after creation.
        namespace, state = _spawn_namespace(files={"DefaultLoadout"})
        state["scene_size"] = 2047
        result = _dispatch(namespace, _args(scene_id=EnforceString.of("scene1")))
        self.assertEqual(result.error.text, "scene_full")
        self.assertEqual(state["created_objects"], 0)
        result = _dispatch(namespace, _args(scene_id=EnforceString.of("bad scene!")))
        self.assertEqual(result.ai_group.field.text, "scene_id")
        self.assertEqual(state["created_objects"], 0)

    def test_e4_null_member_rolls_back_to_releasing(self) -> None:
        # Mutants killed: a partial spawn answering success, taking public
        # ids, or the release confirming while deletes are pending.
        namespace, state = _spawn_namespace(files={"DefaultLoadout"}, fail_at=2)
        result = _dispatch(namespace, _args(count=3))
        self.assertEqual(result.error.text, "spawn_failed")
        self.assertTrue(result.ai_group.created)
        members = state["members"]
        self.assertEqual([m.despawns for m in members], [1, 1])
        self.assertEqual(state["groups"][0].deletes, 1)
        self.assertEqual(namespace["m_RuntimeObjects"].Count(), 0)
        record = namespace["m_AIGroups"].Get(0)
        self.assertEqual(record.group_state.text, "releasing")
        self.assertFalse(namespace["AIGroupRequestRelease"](record))
        record.members.Get(0).member = None
        record.members.Get(1).member = None
        state["groups"] = []
        self.assertTrue(namespace["AIGroupGone"](record))

    def test_h13_unknown_waypoint_behaviour_is_not_read_as_roam(self) -> None:
        # Mutant killed: the report inventing ROAM for an unknown enum.
        namespace, _state = _spawn_namespace(files={"DefaultLoadout"})
        group = _FakeGroup(_FakeFaction("West"))
        group.waypoint_behaviour = 99
        record = _record(group=group, members=EnforceArray())
        report = _report()
        namespace["AIFillGroupReport"](record, report)
        self.assertEqual(report.behaviour.text, "")
        self.assertNotEqual(report.behaviour_expansion.text, "ROAMING")

    def test_e5_hold_and_loop_speed_limits_from_stored_preferences(self) -> None:
        # Mutants killed: HOLD leaving the threat limit at the normal one,
        # and a later LOOP reading the formation-raised normal back.
        namespace, _state = _spawn_namespace(files={"DefaultLoadout"})
        member = _FakeMember(normal=2, threat=3)
        group = _FakeGroup(_FakeFaction("West"))
        record = _record(
            group=group, members=EnforceArray([_member_record(member)]),
            waypoints=EnforceArray([1.0, 2.0, 3.0]),
        )
        _apply_spawn_behaviour(namespace, record)
        self.assertEqual((group.formation_state, group.waypoint_behaviour), (0, 0))
        self.assertEqual(member.limits, [(0, 3)])
        self.assertEqual(len(group.waypoints), 1)
        self.assertFalse(group.m_BackTracking)
        record.behaviour = EnforceString.of("LOOP")
        _apply_spawn_behaviour(namespace, record)
        self.assertEqual((group.formation_state, group.waypoint_behaviour), (1, 1))
        self.assertEqual(member.limits[-1], (2, 3))
        record.behaviour = EnforceString.of("HOLD")
        _apply_spawn_behaviour(namespace, record)
        _apply_spawn_behaviour(namespace, record)
        self.assertEqual(member.limits[-1], (0, 3))



class SpawnThenOrderEnforceTest(unittest.TestCase):
    """Spawn -> order on one record (the r1-H10 contract, B-r2-H2)."""

    def test_spawn_then_order_uses_the_seeded_preferences(self) -> None:
        # Mutant killed: spawn and order disagreeing on the record they
        # share: a HOLD the spawn applied, repeated by the order, touches
        # nothing, and LOOP restores the limits the spawn read at creation.
        namespace, state = _spawn_namespace(files={"DefaultLoadout"})
        source = ADAPTER.read_text(encoding="utf-8")
        namespace["eAIGroupFormationState"] = SimpleNamespace(NONE=0, IN=1, FLANK=2)
        class_types = ("MCPAIGroupRecord", "MCPAIGroupMemberRecord", "MCPAIGroup", "MCPAIArgsRead", "MCPAIGroupMember", "eAIGroup", "eAIBase", "eAIFaction", "Object", "MCPLeaseCloseCensus", "MCPLeaseCloseEntry", "typename", "GameInventory", "EntityAI", "eAIWaypointBehavior", "MCPArgs", "MCPCommand", "MCPResult", "eAIFormation", "eAIGroupFormationState")
        members = ("m_AIGroups", "m_RuntimeObjects", "m_NextBatchObjectId", "AI_GROUP_COUNT_MAX", "AI_TOTAL_MAX", "AI_WAYPOINTS_MAX", "AI_MEMBER_RING_M")
        for signature in (
            "protected MCPAIGroupRecord FindAIGroupById(int groupId)",
            "protected void AIFillBehaviourAllowed(MCPAIGroup report)",
            "protected void AIOrderSaveSpeed(MCPAIGroupRecord record, int speedValue)",
            "protected void AIFillGroupOrderFields(MCPAIGroupRecord record, MCPAIGroup report)",
            "protected void DispatchAIGroupOrder(MCPCommand command, MCPResult result)",
        ):
            translate(source, signature, namespace, members=members, class_types=class_types)
        spawn_result = _dispatch(namespace, _args(behaviour=EnforceString.of("HOLD"), waypoints=EnforceArray([1.0, 2.0, 3.0])))
        self.assertTrue(spawn_result.ok, spawn_result.error)
        record = namespace["m_AIGroups"].Get(0)
        group = state["groups"][0]
        member = state["members"][0]
        self.assertEqual(member.limits, [(0, 3)])
        group.m_CurrentWaypointIndex = 0
        group.GetFormationState = lambda: group.formation_state
        group.GetWaypoints = lambda: EnforceArray(list(group.waypoints))
        group.IsInCombat = lambda: False

        def order(behaviour: str):
            args = SimpleNamespace(
                group_id=record.group_id, behaviour=EnforceString.of(behaviour),
                formation=EnforceString.of(""), waypoints=None, speed=0.0, speed_set=False,
            )
            result = SimpleNamespace(ai_group=None, ok=False, error=EnforceString.of(""))
            namespace["DispatchAIGroupOrder"](SimpleNamespace(args=args), result)
            return result

        result = order("HOLD")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(member.limits, [(0, 3)])
        result = order("LOOP")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(member.limits[-1], (2, 3))
        self.assertEqual(result.ai_group.behaviour.text, "LOOP")


if __name__ == "__main__":
    unittest.main()


