# -*- coding: utf-8 -*-
"""Is the prepared PBO still valid, or did the tree move under it?

This is the check TANDA-INGAME tells the next session to run. A packaged artefact
built from a live tree expires silently; the only way to know is to compare what
it carries against what the tree holds now.
"""
import hashlib
import os
import struct

import glob
PBO = sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "DayZ_MCP_fence_*.pbo")))[-1]
TREE = r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP"
DEPLOYED = r"P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo"

data = open(PBO, "rb").read()


def read_asciiz(buf, pos):
    end = buf.index(b"\x00", pos)
    return buf[pos:end].decode("utf-8", "replace"), end + 1


pos, entries = 0, []
while True:
    name, pos = read_asciiz(data, pos)
    mime, orig, res, ts, size = struct.unpack_from("<5I", data, pos)
    pos += 20
    if name == "":
        if mime == 0x56657273:
            while True:
                key, pos = read_asciiz(data, pos)
                if key == "":
                    break
                _v, pos = read_asciiz(data, pos)
            continue
        break
    entries.append({"name": name, "size": size})

offset = pos
for e in entries:
    e["sha"] = hashlib.sha256(data[offset:offset + e["size"]]).hexdigest().upper()
    offset += e["size"]

ENFORCE = {"scripts\\5_Mission\\MCPBridge.c", "scripts\\5_Mission\\MCPClientBridge.c",
           "scripts\\5_Mission\\MCPMessages.c"}

# Base del merge: sha256 del arbol cuando se derivaron las Enforce del fencing.
# Sin esto el chequeo de esas tres es un pase automatico (ver enforce-base.json).
BASE_SHA = {}
_base_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "enforce-base.json")
if os.path.exists(_base_path):
    import json as _json
    with open(_base_path, "r", encoding="utf-8") as _fh:
        BASE_SHA = _json.load(_fh).get("tree_sha256", {})
else:
    print("AVISO: falta enforce-base.json; las 3 fuentes Enforce no se pueden comprobar")

stale, ok = [], 0
for e in entries:
    disk = os.path.join(TREE, e["name"])
    if not os.path.exists(disk):
        stale.append((e["name"], "no existe en el arbol"))
        continue
    disk_sha = hashlib.sha256(open(disk, "rb").read()).hexdigest().upper()
    if disk_sha == e["sha"]:
        ok += 1
    elif e["name"] in ENFORCE:
        # El fencing no esta promocionado, asi que estas TIENEN que diferir. Lo que
        # no puede pasar es que el arbol se haya movido por debajo del merge: el
        # overlay sustituye el fichero entero, asi que cualquier linea que otra
        # sesion haya añadido aqui desapareceria del PBO sin avisar.
        want = BASE_SHA.get(os.path.basename(e["name"]))
        if want is not None and want != disk_sha:
            stale.append((e["name"],
                          "Enforce: el arbol se movio BAJO el merge -> rehacer el "
                          "merge, no sustituir"))
        else:
            ok += 1
    else:
        stale.append((e["name"], "el arbol tiene otra version (%d B)" % os.path.getsize(disk)))

print("PBO preparado : %s" % os.path.basename(PBO))
print("entradas      : %d  (%d coinciden con el arbol o son Enforce del fencing)" % (len(entries), ok))
print()
if stale:
    print("** CADUCADO ** el arbol se movio en:")
    for name, why in stale:
        print("   - %-42s %s" % (name, why))
    print("\n   -> reconstruir antes de desplegar, o se revierte trabajo ajeno")
else:
    print("VIGENTE: todo lo no-Enforce coincide byte a byte con el arbol de ahora.")

if os.path.exists(DEPLOYED):
    dep = open(DEPLOYED, "rb").read()
    print("\ndesplegado ahora: %d B  sha=%s  (mtime %s)" % (
        len(dep), hashlib.sha256(dep).hexdigest()[:16].upper(),
        __import__("datetime").datetime.fromtimestamp(os.path.getmtime(DEPLOYED)).strftime("%H:%M:%S")))
