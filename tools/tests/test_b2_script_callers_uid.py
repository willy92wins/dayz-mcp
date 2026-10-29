"""B2 min (fb-20261028-131048-091a): scripts under tools/ send a uid on player mutations.

The daemon refuses player_teleport and the other player mutations without a
non-empty uid (loopback.validate_command_args). A gate script that omitted it
would get bad_args and fail its own setup (tools/g0_site_gate.py did, with
teleport_failed). This pins two things: precheck sends the uid of the single
connected player it observes on player_teleport, and no call in a tools/ script names one of these
commands with an argument dict (literal, or a variable assigned a literal in
the same file) that lacks "uid" or sets it to a blank literal. Arguments built
any other way (dict(), ** merges, a variable assigned elsewhere) are not
checked; that is a known limit of the scan.
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
OTHER_UID = "76561198000000002"

# Commands whose daemon schema or uid rule requires a non-empty uid.
UID_REQUIRED = frozenset({
    "player_teleport", "player_heal", "player_godmode", "player_kill",
    "inventory_give", "hands_take", "hands_attach",
})


class _Daemon:
    def __init__(self, players: dict, state: dict | None = None) -> None:
        self.players = players
        self.state = {"ok": 1, "name": "A", "pos": [1.0, 2.0, 3.0]} if state is None else state
        self.calls: list[tuple[str, dict]] = []

    def run(self, cmd, args, peer=None, timeout_s=0.0):
        self.calls.append((cmd, args))
        if cmd == "query_all_players":
            return self.players
        if cmd == "query_player_state":
            return self.state
        return {"ok": False}


def _row(uid=UID, pos=(1.0, 2.0, 3.0)) -> dict:
    row: dict = {"pos": list(pos)}
    if uid is not None:
        row["uid"] = uid
    return row


class SiteGateUidTest(unittest.TestCase):
    def _precheck(self, players: dict, state: dict | None = None) -> tuple[dict, _Daemon]:
        daemon = _Daemon(players, state)
        with patch.object(gate, "canopy_gate", return_value={"status": "PASS", "surface_y": 10.0}):
            record = gate.precheck(daemon, "t", 100.0, 200.0)
        return record, daemon

    def _teleports(self, daemon: _Daemon) -> list[dict]:
        return [args for cmd, args in daemon.calls if cmd == "player_teleport"]

    def test_teleport_carries_the_player_uid_and_passes_the_daemon_schema(self) -> None:
        record, daemon = self._precheck({"ok": 1, "players": [_row()]})
        teleports = self._teleports(daemon)
        self.assertEqual(len(teleports), 1)
        self.assertEqual(teleports[0]["uid"], UID)
        ok, error = loopback.validate_command_args("player_teleport", teleports[0])
        self.assertTrue(ok, error)
        self.assertEqual(record["reason"], "teleport_failed")

    def test_several_connected_players_are_refused(self) -> None:
        # The uid cannot be tied to the player query_player_state observes
        # (the bridge skips players without an identity), so the gate refuses.
        record, daemon = self._precheck({"ok": 1, "players": [
            _row(OTHER_UID, (900.0, 5.0, 900.0)),
            _row(UID, (1.0, 2.0, 3.0)),
        ]})
        self.assertEqual(record["reason"], "player_uid_unavailable")
        self.assertEqual(self._teleports(daemon), [])

    def test_no_readable_uid_stops_before_any_teleport(self) -> None:
        far = _row(OTHER_UID, (900.0, 5.0, 900.0))
        for label, players, state in (
            ("empty list", {"ok": 1, "players": []}, None),
            ("blank uid", {"ok": 1, "players": [_row("  ")]}, None),
            ("missing uid", {"ok": 1, "players": [_row(None)]}, None),
            ("not ok", {"ok": 0}, None),
            ("timeout", {"_timeout": True}, None),
            ("no row near the observed player", {"ok": 1, "players": [far]}, None),
            ("two rows near the observed player",
             {"ok": 1, "players": [_row(UID), _row(OTHER_UID, (2.0, 2.0, 4.0))]}, None),
            ("observed player moved away from the only row",
             {"ok": 1, "players": [_row(UID, (7.0, 2.0, 3.0))]}, None),
            ("row without pos", {"ok": 1, "players": [{"uid": UID}]}, None),
            ("observed player has no identity (name empty)",
             {"ok": 1, "players": [_row(OTHER_UID, (2.0, 2.0, 3.0))]},
             {"ok": 1, "name": "", "pos": [1.0, 2.0, 3.0]}),
            ("observed player has no name field",
             {"ok": 1, "players": [_row()]}, {"ok": 1, "pos": [1.0, 2.0, 3.0]}),
            ("state unreadable", {"ok": 1, "players": [_row()]}, {"ok": 0}),
            ("state timeout", {"ok": 1, "players": [_row()]}, {"_timeout": True}),
        ):
            with self.subTest(case=label):
                record, daemon = self._precheck(players, state)
                self.assertEqual(record["reason"], "player_uid_unavailable")
                self.assertEqual(self._teleports(daemon), [])


def _uid_less_calls(source: str) -> list[tuple[int, str]]:
    """(line, command) of calls naming a uid-required command whose args lack a uid."""
    tree = ast.parse(source)
    assigned: dict[str, list[ast.Dict]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, []).append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.value, ast.Dict)
            and isinstance(node.target, ast.Name)
        ):
            assigned.setdefault(node.target.id, []).append(node.value)

    def lacks_uid(arg_dict: ast.Dict) -> bool:
        # A duplicate key keeps the last value, so the last literal "uid" decides.
        last = None
        for key, value in zip(arg_dict.keys, arg_dict.values):
            if isinstance(key, ast.Constant) and key.value == "uid":
                last = value
        if last is not None:
            return (
                isinstance(last, ast.Constant)
                and (not isinstance(last.value, str) or not last.value.strip())
            )
        return not any(key is None for key in arg_dict.keys)

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        operands = list(node.args) + [kw.value for kw in node.keywords]
        names = UID_REQUIRED.intersection(
            op.value for op in operands
            if isinstance(op, ast.Constant) and isinstance(op.value, str)
        )
        if not names:
            continue
        dicts: list[ast.Dict] = []
        for op in operands:
            if isinstance(op, ast.Dict):
                dicts.append(op)
            elif isinstance(op, ast.Name):
                dicts.extend(assigned.get(op.id, []))
        if any(lacks_uid(d) for d in dicts):
            found.append((node.lineno, sorted(names)[0]))
    return found


class ScanTest(unittest.TestCase):
    def test_the_scan_catches_the_ways_a_caller_can_drop_the_uid(self) -> None:
        for label, source in (
            ("positional literal", 'd.run("player_teleport", {"pos": p}, "server", 5)'),
            ("keyword args", 'run(cmd="player_teleport", args={"pos": p})'),
            ("keyword cmd and positional", 'run("player_heal", args={})'),
            ("variable dict", 'a = {"pos": p}\nd.run("player_teleport", a)'),
            ("annotated variable dict", 'a: dict = {"pos": p}\nd.run("player_teleport", a)'),
            ("blank uid", 'd.run("inventory_give", {"uid": "", "item": 1})'),
            ("whitespace uid", 'd.run("hands_take", {"uid": "  "})'),
            ("duplicate uid key, last one blank", 'd.run("hands_take", {"uid": "ok", "uid": ""})'),
            ("none uid", 'd.run("player_godmode", {"uid": None})'),
        ):
            with self.subTest(case=label):
                self.assertEqual(len(_uid_less_calls(source)), 1)

    def test_the_scan_accepts_calls_that_carry_a_uid_or_use_exempt_tools(self) -> None:
        for label, source in (
            ("duplicate uid key, last one set", 'd.run("hands_take", {"uid": "", "uid": u})'),
            ("literal uid key", 'd.run("player_teleport", {"pos": p, "uid": u})'),
            ("variable with uid", 'a = {"pos": p, "uid": u}\nd.run("player_teleport", a)'),
            ("keyword with uid", 'run(cmd="player_heal", args={"uid": u})'),
            ("unpacked dict", 'd.run("player_heal", {**base})'),
            ("exempt tool", 'd.run("notify_players", {"text": "x"})'),
            ("read", 'd.run("query_player_state", {})'),
        ):
            with self.subTest(case=label):
                self.assertEqual(_uid_less_calls(source), [])


class ScriptCallersSendUidTest(unittest.TestCase):
    def test_no_tools_script_calls_a_uid_command_without_uid(self) -> None:
        offenders: list[str] = []
        checked = 0
        for path in sorted(_TOOLS_DIR.rglob("*.py")):
            rel = path.relative_to(_TOOLS_DIR)
            if rel.parts[0] in {"tests", "dayz_mcp"}:
                continue
            source = path.read_text(encoding="utf-8-sig")
            if any(f'"{name}"' in source for name in UID_REQUIRED):
                checked += 1
            for lineno, name in _uid_less_calls(source):
                offenders.append(f"{rel}:{lineno} {name}")
        self.assertGreaterEqual(checked, 1, "the scan found no call to check")
        self.assertEqual(offenders, [], "calls without uid: " + ", ".join(offenders))


if __name__ == "__main__":
    unittest.main()
