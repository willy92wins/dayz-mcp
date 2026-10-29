"""B2 min (fb-20261028-131048-091a): scripts under tools/ send a uid on player mutations.

The daemon refuses player_teleport and the other player mutations without a
non-empty uid (loopback.validate_command_args). A gate script that omitted it
would get bad_args and fail its own setup (tools/g0_site_gate.py did, with
teleport_failed). This pins two things: precheck sends the connected player's
uid on player_teleport, and no call in a tools/ script names one of these
commands with a literal argument dict that lacks "uid".
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import g0_site_gate as gate
from dayz_mcp import loopback

UID = "76561198000000001"

# Commands whose daemon schema or uid rule requires a non-empty uid.
UID_REQUIRED = frozenset({
    "player_teleport", "player_heal", "player_godmode", "player_kill",
    "inventory_give", "hands_take", "hands_attach",
})


class _Daemon:
    def __init__(self, players: dict) -> None:
        self.players = players
        self.calls: list[tuple[str, dict]] = []

    def run(self, cmd, args, peer=None, timeout_s=0.0):
        self.calls.append((cmd, args))
        if cmd == "query_all_players":
            return self.players
        return {"ok": False}


class SiteGateUidTest(unittest.TestCase):
    def _precheck(self, players: dict) -> tuple[dict, _Daemon]:
        daemon = _Daemon(players)
        with patch.object(gate, "canopy_gate", return_value={"status": "PASS", "surface_y": 10.0}):
            record = gate.precheck(daemon, "t", 100.0, 200.0)
        return record, daemon

    def test_teleport_carries_the_player_uid_and_passes_the_daemon_schema(self) -> None:
        record, daemon = self._precheck(
            {"ok": 1, "players": [{"uid": UID, "pos": [1, 2, 3]}]}
        )
        teleports = [args for cmd, args in daemon.calls if cmd == "player_teleport"]
        self.assertEqual(len(teleports), 1)
        self.assertEqual(teleports[0]["uid"], UID)
        ok, error = loopback.validate_command_args("player_teleport", teleports[0])
        self.assertTrue(ok, error)
        self.assertEqual(record["reason"], "teleport_failed")

    def test_no_readable_uid_stops_before_any_teleport(self) -> None:
        for players in (
            {"ok": 1, "players": []},
            {"ok": 1, "players": [{"uid": "  "}]},
            {"ok": 1, "players": [{"pos": [1, 2, 3]}]},
            {"ok": 0},
            {"_timeout": True},
        ):
            with self.subTest(players=players):
                record, daemon = self._precheck(players)
                self.assertEqual(record["reason"], "player_uid_unavailable")
                self.assertFalse(any(cmd == "player_teleport" for cmd, _ in daemon.calls))


def _literal_command_calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        strings = [
            arg.value for arg in node.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        ]
        names = UID_REQUIRED.intersection(strings)
        if not names:
            continue
        dicts = [arg for arg in node.args if isinstance(arg, ast.Dict)]
        yield node.lineno, sorted(names)[0], dicts


class ScriptCallersSendUidTest(unittest.TestCase):
    def test_no_tools_script_calls_a_uid_command_without_uid(self) -> None:
        offenders: list[str] = []
        checked = 0
        for path in sorted(_TOOLS_DIR.rglob("*.py")):
            rel = path.relative_to(_TOOLS_DIR)
            if rel.parts[0] in {"tests", "dayz_mcp"}:
                continue
            for lineno, name, dicts in _literal_command_calls(path):
                for arg_dict in dicts:
                    checked += 1
                    keys = {k.value for k in arg_dict.keys if isinstance(k, ast.Constant)}
                    if "uid" not in keys:
                        offenders.append(f"{rel}:{lineno} {name}")
        self.assertGreaterEqual(checked, 1, "the scan found no call to check")
        self.assertEqual(offenders, [], "calls without uid: " + ", ".join(offenders))


if __name__ == "__main__":
    unittest.main()