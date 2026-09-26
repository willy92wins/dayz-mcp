# -*- coding: utf-8 -*-
"""Oracle for the UI ingress schema. Expectations are literal, never derived."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from dayz_mcp.loopback import validate_command_args  # noqa: E402

# (label, cmd, args) that the approved contract says the ingress MUST accept.
ACCEPT = [
    ("tree bare", "ui_tree", {}),
    ("tree path", "ui_tree", {"path": "a/b"}),
    ("tree limit", "ui_tree", {"path": "a/b", "limit": 8}),
    ("tree root", "ui_tree", {"root": "MyMenu"}),
    ("tree root+path", "ui_tree", {"root": "MyMenu", "path": "a/b"}),
    ("set_text plain", "ui_set_text", {"path": "a/b", "text": "x"}),
    ("set_text empty text", "ui_set_text", {"path": "a/b", "text": ""}),
    ("set_text root", "ui_set_text", {"path": "a/b", "text": "x", "root": "MyMenu"}),
    ("click plain", "ui_click", {"path": "a/b"}),
    ("click button", "ui_click", {"path": "a/b", "button": 1}),
    ("click mode direct", "ui_click", {"path": "a/b", "mode": "direct"}),
    ("click mode complete", "ui_click", {"path": "a/b", "mode": "complete"}),
    ("click bubble true", "ui_click", {"path": "a/b", "bubble": True}),
    ("click bubble false", "ui_click", {"path": "a/b", "bubble": False}),
    ("click root", "ui_click", {"path": "a/b", "root": "MyMenu"}),
    ("click full", "ui_click",
     {"path": "a/b", "root": "MyMenu", "mode": "direct", "bubble": True, "button": 0}),
    ("focus plain", "ui_focus", {"path": "a/b"}),
    ("focus root", "ui_focus", {"root": "MyMenu", "path": "a/b"}),
]

# What must STAY rejected. These are the fail-closed controls: widening the table
# far enough to accept the list above must not drag any of these in with it.
REJECT = [
    ("click unknown key", "ui_click", {"path": "a/b", "wiggle": 1}),
    ("click mode outside enum", "ui_click", {"path": "a/b", "mode": "sideways"}),
    # B-01, found by the cross-family review: a JSON array or object is
    # unhashable, so the enum check raised TypeError instead of saying no.
    ("click mode is an array", "ui_click", {"path": "a/b", "mode": []}),
    ("click mode is an object", "ui_click", {"path": "a/b", "mode": {}}),
    ("click mode empty", "ui_click", {"path": "a/b", "mode": ""}),
    ("click bubble is a string", "ui_click", {"path": "a/b", "bubble": "true"}),
    ("click bubble is an int", "ui_click", {"path": "a/b", "bubble": 1}),
    ("click without path", "ui_click", {}),
    ("click root empty", "ui_click", {"path": "a/b", "root": ""}),
    ("click root not a string", "ui_click", {"path": "a/b", "root": 123}),
    ("mode leaks into set_text", "ui_set_text", {"path": "a/b", "text": "x", "mode": "direct"}),
    ("bubble leaks into set_text", "ui_set_text", {"path": "a/b", "text": "x", "bubble": True}),
    ("mode leaks into tree", "ui_tree", {"mode": "direct"}),
    ("bubble leaks into focus", "ui_focus", {"path": "a/b", "bubble": True}),
    ("button leaks into focus", "ui_focus", {"path": "a/b", "button": 1}),
    ("tree root not a string", "ui_tree", {"root": 5}),
    ("set_text without text", "ui_set_text", {"path": "a/b"}),
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
print(f"casos: {len(ACCEPT)} aceptar / {len(REJECT)} rechazar / {len(bad)} incumplidos")
if bad:
    sys.exit(1)
print("UI_INGRESS_SCHEMA_OK")
