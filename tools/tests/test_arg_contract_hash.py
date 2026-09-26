"""Arg-contract hash gate (fb-20260924-235528-0878).

Name-only ``caps=`` census green-washed a stale @DayZ_MCP PBO that still
listed ``vehicle_prepare_fixture`` but rejected the tool's ``mode``/``radius``
shape (``bad_args``). The server peer now announces ``ach=`` (16-hex sha256
prefix of the canonical arg contract) and ``_compare_bridge_capabilities``
plus ``compute_bridge_ready`` fail closed on absent/wrong values.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, server as server_module
from tests._addon_paths import addon_root


BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
_HASH_DECL = re.compile(
    r'\bconst\s+string\s+SERVER_ARG_CONTRACT_HASH\s*=\s*"([0-9a-f]{16})"\s*;'
)
_ACH_WIRE = '"&ach=" + EncodeQueryValue(SERVER_ARG_CONTRACT_HASH)'


class ArgContractHashTest(unittest.TestCase):
    def test_python_hash_matches_canonical_fixture(self) -> None:
        self.assertEqual(
            server_module.server_arg_contract_canonical(),
            "vehicle_prepare_fixture=mode,pos,radius,type",
        )
        self.assertEqual(
            server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH, "3c77a99c95fd05a4"
        )
        self.assertEqual(
            server_module.server_arg_contract_hash(),
            server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )

    def test_enforce_declares_and_wires_the_same_hash(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        match = _HASH_DECL.search(source)
        self.assertIsNotNone(match, "SERVER_ARG_CONTRACT_HASH declaration missing")
        self.assertEqual(match.group(1), server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH)
        self.assertIn(_ACH_WIRE, source)

    def test_matching_hash_with_full_census_is_match(self) -> None:
        census = sorted(server_module._BRIDGE_COMMAND_TOOLS["server"])
        registered = frozenset(
            tool for tool in server_module._BRIDGE_COMMAND_TOOLS["server"].values() if tool
        )
        result = server_module._compare_bridge_capabilities(
            "server",
            {
                "state": "announced",
                "reason": "ok",
                "announced_commands": census,
                "announced_arg_contract_hash": server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            },
            registered,
        )
        self.assertEqual(result["state"], "match")
        self.assertEqual(result["reason"], "ok")
        self.assertEqual(
            result["announced_arg_contract_hash"],
            server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )

    def test_absent_hash_on_announced_server_census_is_mismatch(self) -> None:
        census = sorted(server_module._BRIDGE_COMMAND_TOOLS["server"])
        registered = frozenset(
            tool for tool in server_module._BRIDGE_COMMAND_TOOLS["server"].values() if tool
        )
        result = server_module._compare_bridge_capabilities(
            "server",
            {
                "state": "announced",
                "reason": "ok",
                "announced_commands": census,
            },
            registered,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["reason"], "arg_contract_mismatch")
        self.assertIsNone(result["announced_arg_contract_hash"])
        self.assertEqual(
            result["expected_arg_contract_hash"],
            server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )

    def test_wrong_hash_is_mismatch(self) -> None:
        census = sorted(server_module._BRIDGE_COMMAND_TOOLS["server"])
        registered = frozenset(
            tool for tool in server_module._BRIDGE_COMMAND_TOOLS["server"].values() if tool
        )
        result = server_module._compare_bridge_capabilities(
            "server",
            {
                "state": "announced",
                "reason": "ok",
                "announced_commands": census,
                "announced_arg_contract_hash": "deadbeefdeadbeef",
            },
            registered,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["reason"], "arg_contract_mismatch")
        self.assertEqual(result["announced_arg_contract_hash"], "deadbeefdeadbeef")

    def test_client_peer_does_not_require_arg_contract_hash(self) -> None:
        census = sorted(server_module._BRIDGE_COMMAND_TOOLS["client"])
        registered = frozenset(
            tool for tool in server_module._BRIDGE_COMMAND_TOOLS["client"].values() if tool
        )
        result = server_module._compare_bridge_capabilities(
            "client",
            {
                "state": "announced",
                "reason": "ok",
                "announced_commands": census,
            },
            registered,
        )
        self.assertEqual(result["state"], "match")
        self.assertIsNone(result["expected_arg_contract_hash"])

    def test_ready_fails_closed_on_arg_contract_mismatch(self) -> None:
        status = {
            "server_peer": {
                "last_poll_age_s": 0.1,
                "bound_last_poll_age_s": 0.1,
                "binding_state": "BOUND",
                "version_state": "ok",
                "capabilities": {
                    "state": "mismatch",
                    "reason": "arg_contract_mismatch",
                    "announced_commands": [],
                },
            },
            "client_peer": {
                "last_poll_age_s": 0.1,
                "bound_last_poll_age_s": 0.1,
                "binding_state": "BOUND",
                "version_state": "ok",
                "capabilities": {"state": "match", "reason": "ok", "announced_commands": []},
            },
        }
        # Force live peers: _peer_is_live checks binding + ages. BOUND + ages set.
        ready = server_module.compute_bridge_ready(status)
        self.assertEqual(ready, {"ready": False, "reason": "arg_contract_mismatch"})
        self.assertIn("arg_contract_mismatch", server_module.READY_REASONS)

    def test_loopback_stores_ach_on_accredited_poll_view(self) -> None:
        # Minimal accredited path is heavy; unit-test the recorder + view.
        state = loopback.ServerState(key="k")
        state.daemon_generation = 1
        state._record_poll_caps_locked(
            "server",
            "entities_query,vehicle_prepare_fixture",
            True,
            ach=server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )
        view = state._capabilities_view_locked("server")
        self.assertEqual(view["state"], "announced")
        self.assertEqual(
            view["announced_arg_contract_hash"],
            server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )
        state._record_poll_caps_locked("server", "entities_query", False, ach="nope")
        view2 = state._capabilities_view_locked("server")
        self.assertEqual(view2["state"], "unknown")
        self.assertIsNone(view2["announced_arg_contract_hash"])

    def _live_peers(self, server_caps: dict) -> dict:
        return {
            "server_peer": {
                "last_poll_age_s": 0.1,
                "bound_last_poll_age_s": 0.1,
                "binding_state": "BOUND",
                "version_state": "ok",
                "capabilities": server_caps,
            },
            "client_peer": {
                "last_poll_age_s": 0.1,
                "bound_last_poll_age_s": 0.1,
                "binding_state": "BOUND",
                "version_state": "ok",
                "capabilities": {
                    "state": "match",
                    "reason": "ok",
                    "announced_commands": [],
                },
            },
        }

    def _server_registered(self) -> frozenset[str]:
        return frozenset(
            tool
            for tool in server_module._BRIDGE_COMMAND_TOOLS["server"].values()
            if tool
        )

    def _full_announced(self, ach: str | None) -> dict:
        block: dict = {
            "state": "announced",
            "reason": "ok",
            "announced_commands": sorted(
                server_module._BRIDGE_COMMAND_TOOLS["server"]
            ),
        }
        if ach is not None:
            block["announced_arg_contract_hash"] = ach
        return block

    def _announced_missing_command(self, ach: str | None, missing: str = "world_spawn") -> dict:
        """Census lacking one registered command (B2 residual repro)."""
        cmds = sorted(
            c
            for c in server_module._BRIDGE_COMMAND_TOOLS["server"]
            if c != missing
        )
        self.assertTrue(cmds, "census should still list other commands")
        self.assertNotIn(missing, cmds)
        block: dict = {
            "state": "announced",
            "reason": "ok",
            "announced_commands": cmds,
        }
        if ach is not None:
            block["announced_arg_contract_hash"] = ach
        return block

    def test_ready_false_when_capabilities_unknown(self) -> None:
        # B2: missing/malformed caps must not green-wash ready.
        status = self._live_peers(
            {
                "state": "unknown",
                "reason": "absent",
                "announced_commands": [],
                "announced_arg_contract_hash": None,
            }
        )
        ready = server_module.compute_bridge_ready(status)
        self.assertEqual(ready, {"ready": False, "reason": "capabilities_unknown"})
        self.assertIn("capabilities_unknown", server_module.READY_REASONS)

    def test_ready_false_when_capabilities_block_missing(self) -> None:
        status = self._live_peers({})
        ready = server_module.compute_bridge_ready(status)
        self.assertEqual(ready, {"ready": False, "reason": "capabilities_unknown"})

    def test_bridge_status_path_compare_before_ready_correct_hash(self) -> None:
        # B1: compare then ready ? correct ach => ready true + caps match.
        raw = self._live_peers(
            self._full_announced(server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH)
        )
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        self.assertEqual(compared["server_peer"]["capabilities"]["state"], "match")
        payload = server_module._with_ready(compared)
        self.assertTrue(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "ready")
        self.assertEqual(payload["server_peer"]["capabilities"]["state"], "match")

    def test_bridge_status_path_compare_before_ready_absent_hash(self) -> None:
        raw = self._live_peers(self._full_announced(None))
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        caps = compared["server_peer"]["capabilities"]
        self.assertEqual(caps["state"], "mismatch")
        self.assertEqual(caps["reason"], "arg_contract_mismatch")
        payload = server_module._with_ready(compared)
        self.assertFalse(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "arg_contract_mismatch")

    def test_bridge_status_path_compare_before_ready_wrong_hash(self) -> None:
        raw = self._live_peers(self._full_announced("deadbeefdeadbeef"))
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        caps = compared["server_peer"]["capabilities"]
        self.assertEqual(caps["state"], "mismatch")
        self.assertEqual(caps["reason"], "arg_contract_mismatch")
        payload = server_module._with_ready(compared)
        self.assertFalse(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "arg_contract_mismatch")

    def test_bridge_status_path_unknown_caps_ready_false(self) -> None:
        # B1+B2: unknown census after comparison => ready false.
        raw = self._live_peers(
            {
                "state": "unknown",
                "reason": "absent",
                "announced_commands": [],
            }
        )
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        self.assertEqual(compared["server_peer"]["capabilities"]["state"], "unknown")
        payload = server_module._with_ready(compared)
        self.assertFalse(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "capabilities_unknown")

    def test_ready_on_raw_announced_wrong_hash_without_comparison(self) -> None:
        # Fail-closed even if a caller skips comparison (raw announced + bad ach).
        status = self._live_peers(self._full_announced("deadbeefdeadbeef"))
        ready = server_module.compute_bridge_ready(status)
        self.assertEqual(ready, {"ready": False, "reason": "arg_contract_mismatch"})

    def test_bridge_status_missing_census_cmd_absent_ach_ready_false(self) -> None:
        # B2 residual: missing census cmd must not hide absent ach => ready=false.
        raw = self._live_peers(self._announced_missing_command(None))
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        caps = compared["server_peer"]["capabilities"]
        self.assertEqual(caps["state"], "mismatch")
        self.assertEqual(caps["reason"], "arg_contract_mismatch")
        self.assertIn("world_spawn", caps.get("registered_without_announced_command", []))
        payload = server_module._with_ready(compared)
        self.assertFalse(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "arg_contract_mismatch")

    def test_bridge_status_missing_census_cmd_wrong_ach_ready_false(self) -> None:
        # B2 residual: missing census cmd must not hide wrong ach => ready=false.
        raw = self._live_peers(self._announced_missing_command("deadbeefdeadbeef"))
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        caps = compared["server_peer"]["capabilities"]
        self.assertEqual(caps["state"], "mismatch")
        self.assertEqual(caps["reason"], "arg_contract_mismatch")
        self.assertIn("world_spawn", caps.get("registered_without_announced_command", []))
        payload = server_module._with_ready(compared)
        self.assertFalse(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "arg_contract_mismatch")

    def test_bridge_status_missing_census_cmd_correct_ach_keeps_census_reason(self) -> None:
        # Census-only disagreement with matching ach: reason stays census;
        # historical ready=true among mismatches is preserved.
        raw = self._live_peers(
            self._announced_missing_command(
                server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH
            )
        )
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        caps = compared["server_peer"]["capabilities"]
        self.assertEqual(caps["state"], "mismatch")
        self.assertEqual(caps["reason"], "census_disagrees_with_registered_tools")
        payload = server_module._with_ready(compared)
        self.assertTrue(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "ready")


if __name__ == "__main__":
    unittest.main()
