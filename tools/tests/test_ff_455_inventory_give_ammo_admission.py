"""Fix-forward of #455 (cd7e7328): an inventory_give that carries ammo is
admitted like a new verb.

An older PBO reads inventory_give without ammo/ammo_set: it would create the
item and never apply the count. Only the arg-contract hash tells the two
bridges apart, so ammo_set must make the daemon demand the current ach both
when the command is queued and when it is delivered. A plain inventory_give
(no ammo) keeps working against an older PBO.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, server
from dayz_mcp.bridge_readiness import EXPECTED_SERVER_ARG_CONTRACT_HASH
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from tests.fence_helpers import INST_SERVER, PID_SERVER, bound_queue

# The arg-contract hash main announced right before #455 (790e2184): a PBO
# whose inventory_give has no ammo.
OLDER_PBO_ARG_CONTRACT_HASH = "3b7a1b143153de71"
PLAIN = {"classname": "Mag_AKM_30Rnd", "dest": "inventory", "uid": "u1"}
WITH_AMMO = {**PLAIN, "ammo": 17, "ammo_set": True}


def _bound_state() -> loopback.ServerState:
    state = loopback.ServerState(key="k")  # type: ignore[arg-type]
    state.daemon_generation = "gen-ff455"
    state.install_bound_peer(
        instance=INST_SERVER, role="server", pid=PID_SERVER, run_id="ff455-run"
    )
    return state


def _commands() -> list[str]:
    return sorted(server._BRIDGE_COMMAND_TOOLS["server"])


def _announce(state: loopback.ServerState, ach: str | None) -> None:
    state._peer_caps["server"] = {
        "generation": state.daemon_generation,
        "commands": _commands(),
        "reason": "ok",
        "arg_contract_hash": ach,
        "announced_at": state._now(),
        "instance": INST_SERVER,
    }


def _poll(state: loopback.ServerState, ach: str):
    return state.record_poll(
        "server",
        f"{EXPECTED_BRIDGE_VERSION}~1.30.0",
        instance=INST_SERVER,
        source_pid=PID_SERVER,
        caps=",".join(_commands()),
        ach=ach,
    )


class InventoryGiveAmmoAdmissionTest(unittest.TestCase):
    def test_the_predicate_names_ammo_and_not_a_plain_give(self) -> None:
        self.assertTrue(loopback._requires_capability_admission("inventory_give", WITH_AMMO))
        self.assertTrue(
            loopback._requires_capability_admission("inventory_give", {**PLAIN, "ammo_set": True})
        )
        self.assertTrue(loopback._requires_capability_admission("inventory_give", {**PLAIN, "ammo": 3}))
        self.assertFalse(loopback._requires_capability_admission("inventory_give", PLAIN))
        self.assertFalse(loopback._requires_capability_admission("inventory_give", None))

    def test_enqueue_refuses_ammo_against_an_older_or_missing_hash(self) -> None:
        for ach in (OLDER_PBO_ARG_CONTRACT_HASH, None):
            with self.subTest(ach=ach):
                state = _bound_state()
                _announce(state, ach)
                status, body = state.enqueue_command("inventory_give", dict(WITH_AMMO), peer="server")
                self.assertEqual(status, 409, body)
                self.assertEqual(body["error"], "arg_contract_mismatch")
                self.assertEqual(bound_queue(state, "server"), [])
                self.assertEqual(state._next_id, 1)

    def test_enqueue_admits_ammo_with_the_current_hash(self) -> None:
        state = _bound_state()
        _announce(state, EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, body = state.enqueue_command("inventory_give", dict(WITH_AMMO), peer="server")
        self.assertEqual(status, 200, body)
        self.assertEqual([c["cmd"] for c in bound_queue(state, "server")], ["inventory_give"])

    def test_a_plain_give_stays_compatible_with_an_older_pbo(self) -> None:
        state = _bound_state()
        _announce(state, OLDER_PBO_ARG_CONTRACT_HASH)
        status, body = state.enqueue_command("inventory_give", dict(PLAIN), peer="server")
        self.assertEqual(status, 200, body)
        status, polled = _poll(state, OLDER_PBO_ARG_CONTRACT_HASH)
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in polled["commands"]], [body["id"]])

    def test_delivery_to_an_older_pbo_drops_ammo_but_not_a_plain_give(self) -> None:
        state = _bound_state()
        _announce(state, EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, ammo = state.enqueue_command("inventory_give", dict(WITH_AMMO), peer="server")
        self.assertEqual(status, 200, ammo)
        status, plain = state.enqueue_command("inventory_give", dict(PLAIN), peer="server")
        self.assertEqual(status, 200, plain)
        status, polled = _poll(state, OLDER_PBO_ARG_CONTRACT_HASH)
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in polled["commands"]], [plain["id"]])
        self.assertEqual(state.take_result(ammo["id"])["error"], "arg_contract_mismatch")

    def test_delivery_with_the_current_hash_hands_ammo_over(self) -> None:
        state = _bound_state()
        _announce(state, EXPECTED_SERVER_ARG_CONTRACT_HASH)
        status, ammo = state.enqueue_command("inventory_give", dict(WITH_AMMO), peer="server")
        self.assertEqual(status, 200, ammo)
        status, polled = _poll(state, EXPECTED_SERVER_ARG_CONTRACT_HASH)
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in polled["commands"]], [ammo["id"]])


class InventoryGiveAmmoRangeTest(unittest.IsolatedAsyncioTestCase):
    """P2 of #455: ammo above the Enforce int range never reaches the bridge."""

    def test_the_daemon_refuses_a_count_above_int32(self) -> None:
        wire = {"classname": "Mag_AKM_30Rnd", "dest": "inventory", "uid": "u1"}
        self.assertEqual(
            (False, "bad_args"),
            loopback.validate_command_args(
                "inventory_give", {**wire, "ammo": 2147483648, "ammo_set": True}
            ),
        )
        ok, error = loopback.validate_command_args(
            "inventory_give", {**wire, "ammo": 2147483647, "ammo_set": True}
        )
        self.assertTrue(ok, error)

    async def test_the_tool_refuses_a_count_above_int32_before_the_bridge(self) -> None:
        from unittest.mock import AsyncMock, patch

        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        with patch.object(runtime, "call_bridge", new=AsyncMock(return_value={"ok": 1})) as call:
            with self.assertRaises(Exception) as caught:
                await app.call_tool(
                    "inventory_give",
                    {"classname": "Mag_AKM_30Rnd", "dest": "inventory", "uid": "u1", "ammo": 2147483648},
                )
        call.assert_not_awaited()
        self.assertIn("ammo", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
