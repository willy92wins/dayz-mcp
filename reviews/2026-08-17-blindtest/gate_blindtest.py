# Gate mecanico identico para las 3 lanes. Mide el CRITERIO DE HECHO del brief sobre
# el codigo ANADIDO (diff contra base), no sobre el fichero entero.
import difflib, os, re, sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"C:\Users\guill\AppData\Local\Temp\blindtest"
BASE = os.path.join(ROOT, "base")
FILES = ["MCPMessages.c", "MCPClientBridge.c"]

ERRORS = ["no_player", "no_action_manager", "action_not_found", "target_not_found",
          "action_in_progress", "not_possible", "input_busy", "condition_failed"]


def added_lines(base_dir, lane_dir):
    out = {}
    for f in FILES:
        a = open(os.path.join(base_dir, f), encoding="utf-8", errors="replace").read().splitlines()
        b = open(os.path.join(lane_dir, f), encoding="utf-8", errors="replace").read().splitlines()
        out[f] = [l[1:] for l in difflib.unified_diff(a, b, n=0) if l.startswith("+") and not l.startswith("+++")]
    return out


def gate(name, lane_dir):
    add = added_lines(BASE, lane_dir)
    allnew = "\n".join(add[FILES[0]] + add[FILES[1]])
    bridge = "\n".join(add["MCPClientBridge.c"])
    msgs = "\n".join(add["MCPMessages.c"])
    boms = [f for f in FILES if open(os.path.join(lane_dir, f), "rb").read(3) == b"\xef\xbb\xbf"]

    # condiciones booleanas partidas: linea que ACABA en && o ||
    multiline = [l for l in allnew.splitlines() if re.search(r"(&&|\|\|)\s*$", l)]
    # ternarios: '?' fuera de string/comentario, con ':' despues
    tern = [l for l in allnew.splitlines()
            if re.search(r"\?[^\"']*:", re.sub(r"//.*|\"[^\"]*\"", "", l))]

    # CALIBRACION: los checks de codigo corren sobre el codigo SIN comentarios.
    # Un comentario que documenta "no uso ForceTarget porque..." daba rojo al control
    # verificado (mismo falso positivo que la puerta de ErrorEx de la corrida 38).
    code = re.sub(r"//.*", "", bridge)

    # cursorHitPos: vale inline dentro del ctor o via local asignada desde GetPosition().
    ctor = re.search(r"new\s+ActionTarget\s*\(([^;]*)\)", code, re.S)
    ctor_args = [a.strip() for a in ctor.group(1).split(",")] if ctor else []
    hitpos_arg = ctor_args[3] if len(ctor_args) >= 4 else ""
    hitpos_ok = "GetPosition()" in hitpos_arg or bool(
        hitpos_arg and re.search(r"\b" + re.escape(hitpos_arg) + r"\s*=\s*[\w\.]*GetPosition\(\)", code))
    hitpos_zero = "vector.Zero" in hitpos_arg or bool(
        hitpos_arg and re.search(r"\b" + re.escape(hitpos_arg) + r"\s*=\s*vector\.Zero", code))

    checks = [
        ("sin BOM", not boms, ",".join(boms) or "ok"),
        ("`action` declarado en MCPArgs", bool(re.search(r"string\s+action\s*;", msgs)), ""),
        ("rama `action_use` en el if/else", '"action_use"' in bridge, ""),
        ("[A2] NO usa PerformAction(int", "PerformAction(" not in code.replace("PerformActionStart(", ""), ""),
        ("[A3] NO usa ForceTarget", "ForceTarget" not in code, ""),
        ("llama PerformActionStart", "PerformActionStart(" in code, ""),
        ("cursorHitPos <- GetPosition()", hitpos_ok, hitpos_arg or "sin ctor"),
        ("NO usa vector.Zero de cursorHitPos", not hitpos_zero, ""),
        ("resuelve por m_ActionsArray", "m_ActionsArray" in bridge, ""),
        ("usa Type().ToString()", "Type().ToString()" in bridge, ""),
        ("GetObjectsAtPosition3D", "GetObjectsAtPosition3D" in bridge, ""),
        ("ActionPossibilityCheck", "ActionPossibilityCheck" in bridge, ""),
        ("CanStoreInputUserData", "CanStoreInputUserData" in bridge, ""),
        ("GetRunningAction", "GetRunningAction" in bridge, ""),
        ("action.Can(player,", bool(re.search(r"\.Can\s*\(\s*player", bridge)), ""),
        ("campo result.started", "bool started" in msgs or "started" in msgs, ""),
        ("campo result.distance", "distance" in msgs, ""),
        ("sin ternarios", not tern, (tern[0][:50] if tern else "ok")),
        ("sin booleanas multilinea", not multiline, (multiline[0][:50] if multiline else "ok")),
    ]
    miss = [e for e in ERRORS if f'"{e}"' not in bridge]
    checks.append((f"los {len(ERRORS)} codigos de error", not miss, ",".join(miss) or "ok"))

    ok = sum(1 for _, p, _ in checks if p)
    print(f"\n### {name}  —  +{len(add[FILES[0]])} / +{len(add[FILES[1]])} lineas  —  {ok}/{len(checks)}")
    for label, passed, note in checks:
        if not passed:
            print(f"   FALLA  {label}" + (f"   [{note}]" if note and note != "ok" else ""))
    return ok, len(checks)


lanes = [("composer-2.5", os.path.join(ROOT, "ws_composer")),
         ("qwen3.8:27b", os.path.join(ROOT, "ws_qwen")),
         ("grok-4.6 (control)", os.path.join(ROOT, "ws_grok"))]
res = {}
for n, d in lanes:
    if os.path.isdir(d) and all(os.path.exists(os.path.join(d, f)) for f in FILES):
        res[n] = gate(n, d)
    else:
        print(f"\n### {n}  —  (workspace ausente, saltado)")
print("\n=== RESUMEN ===")
for n, (o, t) in res.items():
    print(f"  {n:22} {o}/{t}")
