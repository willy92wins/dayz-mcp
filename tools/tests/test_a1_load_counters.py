"""A1: bridge_status load counters (fb-20261028-131233-a07f).

Pins the poll query the PBO sends, the daemon's reading of it, and the
four-state record shape. The four states themselves are not measured here.
"""

from __future__ import annotations

import unittest

from dayz_mcp import core, loopback
from tests._addon_paths import addon_root


_MISSION = addon_root() / "scripts" / "5_Mission"
_QUERY = '&ia=" + m_InitAttempts + "&pc=" + m_Polls + "&lpims=" + ms'


class LoadCountersTest(unittest.TestCase):
    def test_both_bridges_announce_the_same_query(self) -> None:
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            text = (_MISSION / name).read_text(encoding="utf-8")
            self.assertIn(_QUERY, text, name)
            self.assertIn("m_InitAttempts = m_InitAttempts + 1;", text, name)

    def test_status_load_is_null_until_a_complete_poll(self) -> None:
        state = loopback.ServerState("k")
        code, _payload = state.record_poll("server", "12~1.30")
        self.assertEqual(code, 200)
        load = state.status_snapshot()["peers"]["server"]["load"]
        self.assertEqual(load["init_attempts"], None)
        self.assertEqual(load["polls"], None)
        self.assertEqual(load["last_poll_interval_s"], None)

    def test_poll_query_lands_on_bridge_status(self) -> None:
        state = loopback.ServerState("k")
        code, _payload = state.record_poll(
            "server",
            "12~1.30",
            load=loopback._load_from_poll_query(
                {"ia": ["7"], "pc": ["2"], "lpims": ["250"]}
            ),
        )
        self.assertEqual(code, 200)
        status = core.build_status(
            state.status_snapshot(),
            require_version=False,
            expected_game_version=None,
        )
        load = status["server_peer"]["load"]
        self.assertEqual(load["init_attempts"], 7)
        self.assertEqual(load["polls"], 2)
        self.assertEqual(load["last_poll_interval_s"], 0.25)
        client = status["client_peer"]["load"]
        self.assertEqual(client["init_attempts"], None)
        self.assertEqual(client["polls"], None)
        self.assertIsNone(client["last_poll_interval_s"])

    def test_partial_or_junk_query_does_not_replace_a_reading(self) -> None:
        self.assertIsNone(loopback._load_from_poll_query({"ia": ["1"], "pc": ["1"]}))
        self.assertIsNone(
            loopback._load_from_poll_query(
                {"ia": ["-1"], "pc": ["1"], "lpims": ["1"]}
            )
        )
        self.assertIsNone(
            loopback._load_from_poll_query(
                {"ia": ["1"], "pc": ["nope"], "lpims": ["1"]}
            )
        )
        state = loopback.ServerState("k")
        state.record_poll(
            "client",
            load={
                "init_attempts": 3,
                "polls": 3,
                "last_poll_interval_s": 0.2,
            },
        )
        state.record_poll("client", load=None)
        load = state.status_snapshot()["peers"]["client"]["load"]
        self.assertEqual(load["init_attempts"], 3)
        self.assertEqual(load["polls"], 3)
        self.assertEqual(load["last_poll_interval_s"], 0.2)

    def test_baseline_record_names_four_unmeasured_states(self) -> None:
        record = core.empty_load_baseline_record()
        self.assertEqual(record["observer"], "server_process_cpu")
        self.assertFalse(record["measured"])
        self.assertEqual(
            tuple(record["states"]),
            (
                "no_mod",
                "loaded_unconfigured",
                "configured_idle",
                "admin_load",
            ),
        )
        self.assertTrue(all(value is None for value in record["states"].values()))


if __name__ == "__main__":
    unittest.main()
