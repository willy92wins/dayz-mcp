"""A1: bridge_status load counters (fb-20261028-131233-a07f).

Pins the poll query the PBO sends, the daemon's reading of it, and the
four-state record shape. The four states themselves are not measured here.
Review round 1: the reading is per instance, a junk /poll still answers,
and the bridge counters saturate.
"""

from __future__ import annotations

import unittest

from dayz_mcp import core, loopback
from dayz_mcp.instance_fence import BINDING_BOUND
from tests._addon_paths import addon_root


_MISSION = addon_root() / "scripts" / "5_Mission"
_QUERY = '&ia=" + m_InitAttempts + "&pc=" + m_Polls + "&lpims=" + ms'
_CAP = 2000000000
_INST_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_INST_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_PID_A = 41001
_PID_B = 41002
_CTIME = "2026-08-18T00:00:00.000000Z"


def _null_load(load: dict) -> None:
    if not isinstance(load, dict):
        raise AssertionError(load)
    for key in ("init_attempts", "polls", "last_poll_interval_s"):
        if load[key] is not None:
            raise AssertionError(load)


class LoadCountersTest(unittest.TestCase):
    def test_both_bridges_announce_the_same_query(self) -> None:
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            text = (_MISSION / name).read_text(encoding="utf-8")
            self.assertIn(_QUERY, text, name)
            self.assertIn("m_InitAttempts = NextLoadCounter(m_InitAttempts);", text, name)
            self.assertIn("m_Polls = NextLoadCounter(m_Polls);", text, name)

    def test_bridge_counters_saturate_at_a_fixed_cap(self) -> None:
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            text = (_MISSION / name).read_text(encoding="utf-8")
            self.assertIn(f"LOAD_COUNTER_CAP = {_CAP};", text, name)
            self.assertIn("if (value < 0 || value >= LOAD_COUNTER_CAP)", text, name)
            self.assertIn("return LOAD_COUNTER_CAP;", text, name)
            self.assertNotIn("m_InitAttempts = m_InitAttempts + 1;", text, name)
            self.assertNotIn("m_Polls = m_Polls + 1;", text, name)

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

    def test_retired_instance_does_not_lend_counters_to_an_old_pbo(self) -> None:
        state = loopback.ServerState("k")
        state.install_bound_peer(
            instance=_INST_A, role="server", pid=_PID_A, run_id="run-a"
        )
        code, payload = state.record_poll(
            "server",
            "12~1.30",
            instance=_INST_A,
            source_pid=_PID_A,
            source_creation_time=_CTIME,
            load={"init_attempts": 4, "polls": 9, "last_poll_interval_s": 0.2},
        )
        self.assertEqual(code, 200)
        self.assertEqual(payload["bind"], BINDING_BOUND)
        owned = state.status_snapshot()["peers"]["server"]["load"]
        self.assertEqual(owned["init_attempts"], 4)
        self.assertEqual(owned["polls"], 9)

        state.retire_role("run-a", "server", "test")
        state.install_bound_peer(
            instance=_INST_B, role="server", pid=_PID_B, run_id="run-b"
        )
        code, payload = state.record_poll(
            "server",
            "12~1.30",
            instance=_INST_B,
            source_pid=_PID_B,
            source_creation_time=_CTIME,
            load=None,
        )
        self.assertEqual(code, 200)
        self.assertEqual(payload["bind"], BINDING_BOUND)
        peer = state.status_snapshot()["peers"]["server"]
        self.assertEqual(peer["binding_state"], BINDING_BOUND)
        _null_load(peer["load"])

    def test_non_ascii_and_huge_poll_query_still_answers(self) -> None:
        self.assertIsNone(
            loopback._load_from_poll_query(
                {"ia": ["\u00b2"], "pc": ["1"], "lpims": ["1"]}
            )
        )
        self.assertIsNone(
            loopback._load_from_poll_query(
                {"ia": ["1"], "pc": ["1"], "lpims": ["9" * 400]}
            )
        )
        self.assertIsNone(
            loopback._load_from_poll_query(
                {"ia": ["1"], "pc": ["1"], "lpims": ["9" * (loopback._POLL_COUNTER_MAX_DIGITS + 1)]}
            )
        )
        kept = loopback._load_from_poll_query(
            {"ia": [str(_CAP)], "pc": ["1"], "lpims": ["250"]}
        )
        self.assertIsNotNone(kept)
        assert kept is not None
        self.assertEqual(kept["init_attempts"], _CAP)

        state = loopback.ServerState("k")
        state.record_poll(
            "server",
            load={
                "init_attempts": 3,
                "polls": 3,
                "last_poll_interval_s": 0.2,
            },
        )
        handler = loopback.Handler.__new__(loopback.Handler)
        handler.server = type("Srv", (), {"state": state, "log_sink": lambda _m: None})()
        handler.connection = None
        answered: list[tuple[int, dict]] = []

        def _json(code: int, body: dict) -> None:
            answered.append((code, body))

        handler._json = _json  # type: ignore[method-assign]
        handler._log = lambda _message: None  # type: ignore[method-assign]
        for query in (
            {"ia": ["\u00b2"], "pc": ["1"], "lpims": ["1"]},
            {"ia": ["1"], "pc": ["1"], "lpims": ["9" * 400]},
        ):
            answered.clear()
            handler._handle_poll(query)
            self.assertEqual(len(answered), 1)
            self.assertEqual(answered[0][0], 200)
            self.assertIn("commands", answered[0][1])
        load = state.status_snapshot()["peers"]["server"]["load"]
        self.assertEqual(load["init_attempts"], 3)
        self.assertEqual(load["polls"], 3)

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
