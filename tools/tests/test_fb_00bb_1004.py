"""fb-20260915-143332-00bb + fb-20260915-104637-1004.

Host-safe: peer-gated teleport precheck and object_id teardown copy.
Does not import the Windows-only server stack. No DayZ, Diag,
AddonBuilder, or Steam.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from dayz_mcp.peer_liveness import client_peer_probeable, peer_is_live


TOOLS_DIR = Path(__file__).resolve().parents[1]
SERVER_PY = TOOLS_DIR / "dayz_mcp" / "server.py"
BRIDGE_READINESS_PY = TOOLS_DIR / "dayz_mcp" / "bridge_readiness.py"


def _python_def(source: str, signature: str, stop: str) -> str:
    start = source.index(signature)
    end = source.index(stop, start)
    return source[start:end]


class ClientPeerProbeablePredicateTest(unittest.TestCase):
    def test_missing_and_unread_are_not_probeable(self) -> None:
        self.assertFalse(client_peer_probeable(None))
        self.assertFalse(client_peer_probeable({}))
        self.assertFalse(client_peer_probeable({"client_peer": {}}))
        self.assertFalse(
            client_peer_probeable({"client_peer": {"last_poll_age_s": None}})
        )
        self.assertFalse(peer_is_live(None))
        self.assertFalse(peer_is_live({"last_poll_age_s": None}))

    def test_stale_or_unbound_client_is_not_probeable(self) -> None:
        self.assertFalse(
            client_peer_probeable({"client_peer": {"last_poll_age_s": 20.0}})
        )
        self.assertFalse(
            client_peer_probeable(
                {"client_peer": {"binding_state": "LEGACY_UNBOUND", "last_poll_age_s": 0.1}}
            )
        )
        self.assertFalse(
            client_peer_probeable(
                {
                    "client_peer": {
                        "binding_state": "BOUND",
                        "bound_last_poll_age_s": None,
                        "last_poll_age_s": 0.1,
                    }
                }
            )
        )

    def test_probing_client_is_probeable(self) -> None:
        self.assertTrue(
            client_peer_probeable({"client_peer": {"last_poll_age_s": 0.1}})
        )
        self.assertTrue(
            client_peer_probeable(
                {
                    "client_peer": {
                        "binding_state": "BOUND",
                        "bound_last_poll_age_s": 0.2,
                    }
                }
            )
        )
        self.assertTrue(peer_is_live({"last_poll_age_s": 0.1}))


class Fb00bbSourceContractTest(unittest.TestCase):
    def test_teleport_gates_telemetry_on_probeable_client(self) -> None:
        source = SERVER_PY.read_text(encoding="utf-8")
        body = _python_def(
            source, "async def player_teleport(", "async def object_anim("
        )
        self.assertIn("if await _runtime_client_peer_probeable(runtime):", body)
        self.assertLess(
            body.index("if await _runtime_client_peer_probeable(runtime):"),
            body.index('"vehicle_telemetry"'),
        )
        self.assertLess(
            body.index('"vehicle_telemetry"'),
            body.index("occupant_client_seated(telemetry)"),
        )
        self.assertIn("from dayz_mcp.peer_liveness import", source)
        self.assertIn("client_peer_probeable as _client_peer_probeable", source)

    def test_runtime_helper_fails_open_on_status_error(self) -> None:
        source = BRIDGE_READINESS_PY.read_text(encoding="utf-8")
        body = _python_def(
            source,
            "async def _runtime_client_peer_probeable(",
            "def compute_bridge_ready(",
        )
        self.assertIn("except Exception:", body)
        self.assertIn("return False", body)
        self.assertIn("bridge_status_payload", body)


class Fb1004TeardownCopyTest(unittest.TestCase):
    def test_1004_three_tools_warn_object_id_does_not_survive(self) -> None:
        source = SERVER_PY.read_text(encoding="utf-8")
        delete_desc = source[
            source.index("Delete an object ")
            : source.index("async def object_delete(")
        ]
        teleport_desc = source[
            source.rindex("Teleport a ", 0, source.index("async def player_teleport("))
            : source.index("async def player_teleport(")
        ]
        get_in_desc = source[
            source.index("Seat the connected ")
            : source.index("async def vehicle_get_in_client(")
        ]
        for name, description in (
            ("object_delete", delete_desc),
            ("player_teleport", teleport_desc),
            ("vehicle_get_in_client", get_in_desc),
        ):
            with self.subTest(tool=name):
                self.assertIn("does not survive the", description)
                self.assertIn("needs care", description)
        self.assertIn("no pos+type delete", delete_desc)
        self.assertIn("object_delete", teleport_desc)
        self.assertIn("object_delete", get_in_desc)

    def test_1004_object_delete_does_not_invent_pos_type_api_or_refuse_seat(self) -> None:
        source = SERVER_PY.read_text(encoding="utf-8")
        body = _python_def(
            source, "async def object_delete(", "async def notify_players("
        )
        self.assertIn("object_id", body)
        self.assertNotIn("expected_type", body)
        self.assertNotIn("pos:", body)
        self.assertNotIn("vehicle_telemetry", body)
        self.assertNotIn("occupant_client_seated", body)


if __name__ == "__main__":
    unittest.main()
