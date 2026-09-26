# -*- coding: utf-8 -*-
"""Held-out oracle. Lives OUTSIDE the delegate's workspace and it never saw these
cases. Same contract as gate/oracle_ui_schema.py, deliberately different shapes:
a candidate that generalised passes both, one that fitted the visible 33 fails here.
"""
import sys
from pathlib import Path

WS = Path(__file__).resolve().parent / "ws"
sys.path.insert(0, str(WS / "tools"))
from dayz_mcp.loopback import validate_command_args  # noqa: E402

ACCEPT = [
    # root under names and combinations the visible oracle never used
    ("tree root+limit", "ui_tree", {"root": "InventoryMenu", "limit": 1}),
    ("tree root+path+limit", "ui_tree", {"root": "X", "path": "p", "limit": 512}),
    ("set_text root+empty text", "ui_set_text", {"path": "p", "text": "", "root": "Q"}),
    ("click complete+bubble+root", "ui_click",
     {"path": "p", "root": "Deep/Name", "mode": "complete", "bubble": False}),
    ("click bubble+button", "ui_click", {"path": "p", "bubble": True, "button": 2}),
    ("click mode+button", "ui_click", {"path": "p", "mode": "complete", "button": 0}),
    ("focus root long", "ui_focus", {"path": "p", "root": "A_B-C.D"}),
    ("tree root only, no path", "ui_tree", {"root": "SoloRoot"}),
]

REJECT = [
    # every verb x every field the contract does NOT give it
    ("bubble into tree", "ui_tree", {"bubble": True}),
    ("mode into focus", "ui_focus", {"path": "p", "mode": "direct"}),
    ("button into set_text", "ui_set_text", {"path": "p", "text": "x", "button": 0}),
    ("limit into click", "ui_click", {"path": "p", "limit": 4}),
    ("limit into focus", "ui_focus", {"path": "p", "limit": 4}),
    ("text into click", "ui_click", {"path": "p", "text": "x"}),
    # value-shape controls the visible oracle did not spell out
    ("mode not a string", "ui_click", {"path": "p", "mode": 123}),
    ("mode is None", "ui_click", {"path": "p", "mode": None}),
    ("bubble is None", "ui_click", {"path": "p", "bubble": None}),
    ("bubble is a list", "ui_click", {"path": "p", "bubble": []}),
    ("root is a list", "ui_click", {"path": "p", "root": ["a"]}),
    ("root is None", "ui_tree", {"root": None}),
    ("root is whitespace-only is still a string, but path must exist", "ui_focus", {"root": "Q"}),
    ("button out of range still rejected", "ui_click", {"path": "p", "button": 9}),
    ("limit out of range still rejected", "ui_tree", {"path": "p", "limit": 513}),
    ("unknown key alongside legal ones", "ui_click",
     {"path": "p", "root": "Q", "mode": "direct", "bubble": True, "sneak": 1}),
]

bad = []
for label, cmd, args in ACCEPT:
    ok, err = validate_command_args(cmd, dict(args))
    if not ok:
        bad.append(f"RECHAZA lo que debe aceptar: {label} ({cmd} {args}) -> {err}")
for label, cmd, args in REJECT:
    ok, err = validate_command_args(cmd, dict(args))
    if ok:
        bad.append(f"ACEPTA lo que debe rechazar: {label} ({cmd} {args})")

for line in bad:
    print(line)
print(f"retenidos: {len(ACCEPT)} aceptar / {len(REJECT)} rechazar / {len(bad)} incumplidos")
sys.exit(1 if bad else 0)
