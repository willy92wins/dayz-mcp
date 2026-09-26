# -*- coding: utf-8 -*-
"""HELD-OUT oracle for lote D. Never enters the delegate's workspace.

Its job is not to re-run the visible oracle. It is to separate "generalised" from "fitted
to the cases it could see", so every fixture here differs from the visible one, and it
carries one discriminator the visible oracle does not have at all:

  H-PREFIX -- a sibling directory whose name has the allowed root as a PREFIX
  (`...\\mpmissions_evil\\`). A containment test written with a naive startswith accepts it.
  The visible oracle only ever offers `C:\\Windows\\Temp\\...`, which anything rejects, so a
  fix fitted to the visible cases passes there and dies here.

Honest limit, stated because a held-out oracle that overclaims is worse than none: this
file does NOT re-check the P1 overlay values, only its isolation. If the four fields are
published with wrong values, the visible oracle is what catches it.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def unmet(name: str, detail: str) -> None:
    RESULTS.append((name, "UNMET", detail))


from dayz_mcp import dayz_test_request, dayz_test_tool  # noqa: E402

# Deliberately a different project, different roots, different mission name than the
# visible oracle's. Nothing here is a string the delegate has seen.
DEV = r"Q:\OtherMod_Suite"
ROOT = DEV + r"\_server\mpmissions"
POLICY = dayz_test_request.RequestProjectPolicy(
    mod="OtherMod", dev_root=DEV, default_source=r"Q:\OtherMod",
    default_base_mods=("@CF",), mission_roots=(ROOT,), mod_roots=(r"Q:\Mods",),
)
SEALED = (types.SimpleNamespace(policy=POLICY),)
RUN_ID = "11112222-3333-4444-8555-666677778888"

INSIDE = ROOT + r"\deep\nested\other.Enoch"
PREFIX_SIBLING = DEV + r"\_server\mpmissions_evil\x.Enoch"
TRAVERSAL = ROOT + r"\..\..\..\Windows\Temp\y.Enoch"


def build(mission: str):
    return dayz_test_tool.build_run_request(
        SEALED, project="OtherMod", mode="client", mission=mission,
        run_id=RUN_ID, extra_mods=["@DayZ_MCP"],
    )


def accepts(mission: str) -> tuple[bool, str]:
    try:
        build(mission)
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


ok, why = accepts(INSIDE)
check("H-A ruta absoluta anidada DENTRO de roots aceptada", ok, why or INSIDE)

ok, why = accepts(PREFIX_SIBLING)
check("H-PREFIX hermano con el root como PREFIJO rechazado", not ok,
      "ACEPTADO: la contencion usa startswith y no frontera de segmento" if ok else why)

ok, why = accepts(TRAVERSAL)
check("H-TRAVERSAL ruta con .. que sale de roots rechazada", not ok,
      "ACEPTADA: la contencion no normaliza la ruta" if ok else why)

ok, why = accepts("livonia")
check("H-ALIAS un alias distinto del de la muestra visible sigue vivo", ok, why or "livonia")

# P1: isolation only. The overlay must not have leaked into the shared daemon surface.
FOUR = ("tool_registry_fingerprint", "tool_registry_captured_at",
        "tool_registry_source_stale", "tool_registry_remediation")
try:
    from dayz_mcp import daemon as _daemon

    with open(_daemon.__file__, encoding="utf-8") as fh:
        leaked = [k for k in FOUR if k in fh.read()]
    check("H-AISLAMIENTO daemon.py sin tool_registry_*", not leaked, f"filtrados={leaked}")
except Exception as exc:
    unmet("H-AISLAMIENTO daemon.py sin tool_registry_*", f"{type(exc).__name__}: {exc}")

# P1: the four fields exist at all, from a second call path (fresh app instance).
try:
    from dayz_mcp.server import ServerConfig, build_app

    app, rt = build_app(ServerConfig(key="z", port=0, log_sink=lambda m: None))
    rt.start_loopback()
    try:
        res = asyncio.run(app.call_tool("bridge_status", {}))
        p = (res[1] if isinstance(res, tuple) and len(res) > 1 and isinstance(res[1], dict)
             else json.loads(res[0][0].text))
    finally:
        rt.stop_loopback()
    missing = [k for k in FOUR if k not in p]
    check("H-CAMPOS los cuatro campos en una instancia nueva", not missing, f"faltan={missing}")
except Exception as exc:
    unmet("H-CAMPOS los cuatro campos en una instancia nueva", f"{type(exc).__name__}: {exc}")

print("=" * 96)
worst = 0
for nm, v, d in RESULTS:
    print(f"[{v:<5}] {nm}")
    if d:
        print(f"          {d[:260]}")
    if v != "PASS":
        worst = 1
print("=" * 96)
t = {x: sum(1 for _, y, _ in RESULTS if y == x) for x in ("PASS", "FAIL", "UNMET")}
print(f"RETENIDO: PASS={t['PASS']} FAIL={t['FAIL']} UNMET={t['UNMET']} de {len(RESULTS)}")
sys.exit(worst)
