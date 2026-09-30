# -*- coding: utf-8 -*-
"""Canario del fencing por `instance` (D-55.9) — parte mecanica.

Que mide
--------
`wrong_target_canary_count`: numero de `camera_set` cuyo efecto visible aparece en
una ventana que NO es la del cliente registrado. Valor que refuta el diseno:
distinto de 0.

Con el fencing puesto, ademas del PNG hay una segunda medida que antes no existia:
los contadores de `/status`. El desenlace esperado es que el intruso presente el
MISMO `inst=`, el binding pase a AMBIGUOUS y **nadie** reciba mutaciones — o sea
`wrong_target_canary_count == 0` con `binding_state == AMBIGUOUS`.

Que hace este script (lo mecanico y propenso a error):
  1. Fotografia el estado y los contadores ANTES.
  2. Copia el `dayz_mcp.json` del cliente a un perfil temporal (sin pasar por
     `start_run`, que remintaria el UUID) y lanza un segundo DayZDiag.
  3. Captura las dos ventanas por `cmdline_match` (NO por PID: DayZDiag tiene
     launcher-pid != window-pid, `mcp_capture.py:330`).
  4. Espera a que el orquestador mande los `camera_set` (fase interactiva).
  5. Captura otra vez, lee los contadores DESPUES y mata SOLO el proceso extra.

Que NO hace, a proposito: no toca el run de la sesion, no llama a `dayz_test_stop`,
no adquiere ni suelta el lease y no manda un solo verbo mutante. Los `camera_set`
los emite el orquestador con su lease, que es quien tiene la autoridad.

Uso:
    python canary_fence.py --phase before  --client-profiles <dir> --evidence <dir>
    python canary_fence.py --phase spawn   ...        (lanza el intruso)
    python canary_fence.py --phase after   ...        (captura + contadores + mata el extra)

No volver a programar este canario sin una segunda cuenta de Steam o un intruso
que no dependa de Steam: dos clientes comparten Steam ID y el intruso se queda
en el menu con 0x000400B3 (ficha e4cf).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

TOOLS = r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools"
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)


def daemon_status(port: int, key: str) -> dict:
    url = "http://127.0.0.1:%d/status?key=%s" % (port, key)
    with urllib.request.urlopen(url, timeout=5.0) as response:
        return json.loads(response.read().decode("utf-8"))


def fence_view(status: dict) -> dict:
    """The three things that decide the canary, pulled out of /status."""
    fence = status.get("fence") or {}
    # /status returns FLAT client_peer/server_peer. The earlier
    # status["peers"]["client"] never resolved, so client_binding_state was
    # None on every run and AMBIGUOUS -- the expected verdict -- could not be
    # observed. Measured against the live daemon 2026-08-19.
    client = status.get("client_peer") or {}
    return {
        "client_binding_state": client.get("binding_state"),
        "client_instance_prefix": client.get("instance_prefix"),
        "unaccredited_mutation_enqueues": fence.get("unaccredited_mutation_enqueues"),
        "unaccredited_polls_by_class": fence.get("unaccredited_polls_by_class"),
        "mutation_rejects_by_code": fence.get("mutation_rejects_by_code"),
    }


def capture(evidence: str, label: str, cmdline_match: str) -> dict:
    from mcp_capture import grab_window_to_file

    path = os.path.join(evidence, "%s.png" % label)
    result = grab_window_to_file(path, cmdline_match=cmdline_match)
    result["path"] = path
    result["exists"] = os.path.exists(path)
    result["bytes"] = os.path.getsize(path) if result["exists"] else 0
    return result


def _intruder_still_running(evidence: str) -> bool:
    """True while any DayZDiag is still running against the intruder profile."""
    marker = os.path.join(evidence, "intruder_profiles").lower()
    try:
        listing = subprocess.run(
            ["wmic", "process", "where", "name='DayZDiag_x64.exe'", "get", "CommandLine"],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except Exception:
        return True  # unknown means NOT verified; never report a clean kill on doubt
    return marker in listing.lower()


# One wmic snapshot races the exit. Poll for this long before calling it a miss.
KILL_VERIFY_WINDOW_S = 15
KILL_VERIFY_INTERVAL_S = 1


def _poll_intruder_exit(evidence: str) -> dict:
    """Re-query until the intruder profile is gone or the window ends.

    The first look is immediate; the next ones are KILL_VERIFY_INTERVAL_S
    apart. Stop at the first query that says it is gone. A query that cannot
    classify counts as still running (_intruder_still_running), so a wmic
    failure on every attempt stays unverified. waited_s is the sum of the
    sleeps, not wall time around the queries themselves.
    """
    queries = 0
    waited_s = 0
    while True:
        queries += 1
        if not _intruder_still_running(evidence):
            verified = True
            break
        if waited_s >= KILL_VERIFY_WINDOW_S:
            verified = False
            break
        step = min(KILL_VERIFY_INTERVAL_S, KILL_VERIFY_WINDOW_S - waited_s)
        time.sleep(step)
        waited_s += step
    return {
        "killed_verified": verified,
        "kill_verify_queries": queries,
        "kill_verify_waited_s": waited_s,
    }


def spawn_intruder(client_profiles: str, evidence: str, diag_exe: str, extra_args: list[str]) -> dict:
    """Copy the profile (config included) and launch a second client against it.

    The copy is deliberate: the intruder must present the SAME `instance` as the
    registered client, which is what §7.5 describes and what the fence has to catch.
    """
    intruder_profiles = os.path.join(evidence, "intruder_profiles")
    os.makedirs(intruder_profiles, exist_ok=True)
    src_cfg = os.path.join(client_profiles, "dayz_mcp.json")
    if not os.path.exists(src_cfg):
        raise SystemExit("no encuentro %s — ¿es el perfil del cliente del run?" % src_cfg)
    shutil.copy2(src_cfg, os.path.join(intruder_profiles, "dayz_mcp.json"))
    with open(src_cfg, encoding="utf-8") as fh:
        cfg = json.load(fh)

    # Without the mod the intruder never loads the bridge, never polls, and can
    # never present the duplicated inst=. The camera_set then lands cleanly on
    # the registered client and the whole run reads as a pass -- while nothing
    # was ever tested. Refuse instead of producing that.
    if not any(str(arg).startswith("-mod=") for arg in extra_args):
        raise SystemExit(
            "el intruso necesita -mod=<ruta a @DayZ_MCP>: sin el no carga el "
            "puente, no sondea y el canario sale VACUO (parece PASS sin medir). "
            "Pasalo como --extra-arg=-mod=P:\\Mods\\@DayZ_MCP"
        )
    cmd = [diag_exe, "-profiles=%s" % intruder_profiles] + extra_args
    proc = subprocess.Popen(cmd, cwd=os.path.dirname(diag_exe))
    return {
        "pid": proc.pid,
        "profiles": intruder_profiles,
        "instance_in_copied_config": (cfg.get("instance") or "")[:8] or None,
        "cmd": cmd,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=["before", "spawn", "after"])
    ap.add_argument("--client-profiles", required=True)
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--diag-exe", default="")
    ap.add_argument("--intruder-pid", type=int, default=0)
    ap.add_argument("--extra-arg", action="append", default=[])
    args = ap.parse_args()

    os.makedirs(args.evidence, exist_ok=True)
    bound_match = "-profiles=%s" % args.client_profiles
    intruder_match = "-profiles=%s" % os.path.join(args.evidence, "intruder_profiles")
    out: dict = {"phase": args.phase, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}

    if args.phase == "before":
        out["fence"] = fence_view(daemon_status(args.port, args.key))
        out["capture_bound"] = capture(args.evidence, "A_bound", bound_match)

    elif args.phase == "spawn":
        if not args.diag_exe:
            raise SystemExit("--diag-exe es obligatorio en la fase spawn")
        out["intruder"] = spawn_intruder(args.client_profiles, args.evidence,
                                         args.diag_exe, args.extra_arg)
        out["nota"] = ("esperar a que el intruso pinte (~60 s). Si no pinta, el canario "
                       "es INCONCLUSO, no un PASS.")

    else:  # after
        out["capture_bound"] = capture(args.evidence, "B_bound", bound_match)
        out["capture_intruder"] = capture(args.evidence, "B_intruder", intruder_match)
        out["fence"] = fence_view(daemon_status(args.port, args.key))
        if args.intruder_pid:
            # Solo el extra. Nunca el run de la sesion (D-49).
            subprocess.run(["taskkill", "/PID", str(args.intruder_pid), "/F", "/T"],
                           capture_output=True)
            out["killed_pid"] = args.intruder_pid
            # DayZDiag launcher pid != window pid, so the spawned pid may outlive
            # the call. Confirm by profile instead of trusting the return code.
            poll = _poll_intruder_exit(args.evidence)
            out["killed_verified"] = poll["killed_verified"]
            out["kill_verify_queries"] = poll["kill_verify_queries"]
            out["kill_verify_waited_s"] = poll["kill_verify_waited_s"]

    path = os.path.join(args.evidence, "canary_%s.json" % args.phase)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    print("\nescrito: %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
