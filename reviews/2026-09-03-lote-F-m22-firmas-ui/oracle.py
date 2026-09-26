# -*- coding: utf-8 -*-
"""External oracle for lote F: fichas 25 (20be) and 29 (f4f2), the M22 half.

Run:  cd <ws>/tools && PYTHONPATH=. <venv-python> ../gate/oracle.py

Load-bearing design notes:
- Two DIFFERENT observables, on purpose: the REGISTERED schema (what a client is told it
  may send) and the args dict that actually reaches the bridge (what the client's value
  turns into). A change that does one without the other is the defect this ficha exists
  to close -- the capability was already accepted one layer down and unreachable above.
- The ficha's asymmetry is the discriminator and is checked in both directions: `root`
  preserves PRESENCE (absent stays absent, never defaulted to ""), while `mode` and
  `bubble` travel ALWAYS, even at their defaults.
- Every acceptance check is paired with a fail-closed check that must go RED if validation
  is dropped: a bad value must never reach the bridge.
- A check that cannot reach its subject reports UNMET, never PASS.
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def unmet(name: str, detail: str) -> None:
    RESULTS.append((name, "UNMET", detail))


from dayz_mcp.server import ServerConfig, build_app  # noqa: E402

UI_TOOLS = ("ui_tree", "ui_set_text", "ui_click", "ui_focus")


def _app():
    return build_app(ServerConfig(key="k", port=0, log_sink=lambda m: None))


# ---------------------------------------------------------------- schema registrado
try:
    app, _rt = _app()
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    for name in UI_TOOLS:
        props = sorted((tools[name].inputSchema or {}).get("properties", {}))
        check(f"F1-{name} el schema registrado declara root", "root" in props,
              f"properties={props}")
    props_click = sorted((tools["ui_click"].inputSchema or {}).get("properties", {}))
    for field in ("mode", "bubble"):
        check(f"F1-ui_click el schema registrado declara {field}", field in props_click,
              f"properties={props_click}")
except Exception as exc:  # noqa: BLE001
    for name in UI_TOOLS:
        unmet(f"F1-{name} el schema registrado declara root", f"{type(exc).__name__}: {exc}")
    for field in ("mode", "bubble"):
        unmet(f"F1-ui_click el schema registrado declara {field}", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- lo que llega al puente
def call(tool: str, args: dict):
    """Return (sent_args | None, error). sent_args is what reached call_bridge."""
    app, rt = _app()
    seen: list[dict] = []

    async def recorder(cmd, bridge_args, role, timeout):
        seen.append(dict(bridge_args))
        return {"ok": 1}

    with patch.object(rt, "call_bridge", side_effect=recorder):
        try:
            asyncio.run(app.call_tool(tool, args))
            return (seen[0] if seen else None), ""
        except Exception as exc:  # noqa: BLE001
            return (seen[0] if seen else None), f"{type(exc).__name__}: {exc}"


BASE = {"path": "Btn"}

# F2: root travels with its value when the caller sends one.
for tool, extra in (("ui_tree", {}), ("ui_set_text", {"text": "x"}),
                    ("ui_click", {}), ("ui_focus", {})):
    sent, err = call(tool, {**BASE, **extra, "root": "MiRoot"})
    check(f"F2-{tool} root llega al puente con su valor",
          bool(sent) and sent.get("root") == "MiRoot",
          f"args={sent} err={err[:80]}")

# F3: root PRESERVES ABSENCE. Defaulting it to "" would be a silent scope change.
for tool, extra in (("ui_tree", {}), ("ui_set_text", {"text": "x"}),
                    ("ui_click", {}), ("ui_focus", {})):
    sent, err = call(tool, {**BASE, **extra})
    check(f"F3-{tool} sin root, root NO viaja", bool(sent) and "root" not in sent,
          f"args={sent} err={err[:80]}")

# F4: mode and bubble travel ALWAYS on ui_click, even at their defaults.
sent, err = call("ui_click", dict(BASE))
check("F4 ui_click transmite mode y bubble incluso por defecto",
      bool(sent) and sent.get("mode") == "direct" and sent.get("bubble") is False,
      f"args={sent} err={err[:80]}")

sent, err = call("ui_click", {**BASE, "mode": "complete", "bubble": True})
check("F4b ui_click transmite los valores que le pasan",
      bool(sent) and sent.get("mode") == "complete" and sent.get("bubble") is True,
      f"args={sent} err={err[:80]}")

# ---------------------------------------------------------------- fail-closed
# Every one of these must be rejected BEFORE the command is enqueued: sent is None.
REJECT = [
    ("F5-mode-desconocido", "ui_click", {**BASE, "mode": "otro"}),
    ("F5-mode-no-str", "ui_click", {**BASE, "mode": 3}),
    ("F5-bubble-no-bool", "ui_click", {**BASE, "bubble": "true"}),
    ("F5-bubble-entero", "ui_click", {**BASE, "bubble": 1}),
    ("F5-root-vacio", "ui_click", {**BASE, "root": ""}),
    ("F5-root-no-str", "ui_focus", {**BASE, "root": 7}),
]
for label, tool, args in REJECT:
    sent, err = call(tool, args)
    check(f"{label} se rechaza ANTES de encolar", sent is None and bool(err),
          f"llego_al_puente={sent is not None} err={err[:90]}")

# F5b (B-01 de la revision cruzada): un `root` presente y NULO es un valor invalido, no una
# omision. Con `root: str | None = None` el schema publica anyOf:[string,null] y FastMCP
# colapsa omision y null explicito en el mismo None, asi que la guarda no puede
# distinguirlos y la peticion invalida se encola con alcance global. Con `root: str = None`
# el schema publica type:string y Pydantic rechaza el null antes de entrar en la funcion.
# Medido por el orquestador con una app minima sobre esta version de FastMCP.
for tool, extra in (("ui_tree", {}), ("ui_set_text", {"text": "x"}),
                    ("ui_click", {}), ("ui_focus", {})):
    sent, err = call(tool, {**BASE, **extra, "root": None})
    check(f"F5b-{tool} root=null explicito se rechaza ANTES de encolar",
          sent is None and bool(err),
          f"llego_al_puente={sent is not None} args={sent} err={err[:80]}")

# Fail-closed pairs that already shipped and must not be lost while widening.
for label, tool, args in (
    ("F6-path-vacio", "ui_click", {"path": ""}),
    ("F6-button-fuera-de-rango", "ui_click", {**BASE, "button": 9}),
):
    sent, err = call(tool, args)
    check(f"{label} sigue rechazandose", sent is None and bool(err),
          f"llego_al_puente={sent is not None} err={err[:90]}")

print("=" * 104)
worst = 0
for nm, v, d in RESULTS:
    print(f"[{v:<5}] {nm}")
    if d:
        print(f"          {d[:240]}")
    if v != "PASS":
        worst = 1
print("=" * 104)
t = {x: sum(1 for _, y, _ in RESULTS if y == x) for x in ("PASS", "FAIL", "UNMET")}
print(f"ORACULO: PASS={t['PASS']} FAIL={t['FAIL']} UNMET={t['UNMET']} de {len(RESULTS)}")
if worst == 0:
    print("ORACULO-VERDE")
sys.exit(worst)
