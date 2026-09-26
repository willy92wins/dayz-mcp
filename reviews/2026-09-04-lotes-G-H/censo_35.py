# -*- coding: utf-8 -*-
"""Censo de las 35 fichas del paquete contra el buzon vivo (feedback.jsonl).

Imprime UNA linea por ficha: numero, id, resuelta o no, y el titulo recortado. La fuente de
verdad del estado es el buzon (append-only: una ficha esta resuelta si tiene al menos un evento
de resolucion), no la existencia de paths ni la prosa de los planes.
"""
import json
import os
import re
import sys
from pathlib import Path

PLANES = Path(r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\inbox-20260830")
BUZON = Path(os.environ["LOCALAPPDATA"]) / "DayZ_MCP" / "inbox" / "feedback.jsonl"

pack = {}
for p in sorted(PLANES.glob("[0-9][0-9]-fb-*.md")):
    m = re.match(r"(\d\d)-(fb-\d{8}-\d{6}-[0-9a-f]{4})\.md$", p.name)
    if m:
        pack[m.group(2)] = m.group(1)

titulos, kinds, resueltas, ultima_res = {}, {}, {}, {}
claves_vistas = set()
with BUZON.open(encoding="utf-8") as fh:
    for linea in fh:
        linea = linea.strip()
        if not linea:
            continue
        try:
            ev = json.loads(linea)
        except json.JSONDecodeError:
            continue
        claves_vistas.update(ev.keys())
        fid = ev.get("resolves") or ev.get("id")
        if fid not in pack:
            continue
        if ev.get("title"):
            titulos[fid] = ev["title"]
            kinds[fid] = ev.get("kind", "?")
        # Un evento de resolucion: lleva `resolution` (o `resolved`/`status=resolved`).
        if ev.get("resolution") or ev.get("resolved") or ev.get("status") == "resolved":
            resueltas[fid] = resueltas.get(fid, 0) + 1
            ultima_res[fid] = str(ev.get("resolution") or ev.get("status"))[:90]

print("claves vistas en el buzon:", sorted(claves_vistas))
print()
abiertas = 0
for fid, num in sorted(pack.items(), key=lambda kv: kv[1]):
    n = resueltas.get(fid, 0)
    estado = f"RESUELTA x{n}" if n else "ABIERTA"
    if not n:
        abiertas += 1
    print(f"{num} {fid} [{kinds.get(fid,'?'):>7}] {estado:<12} {titulos.get(fid,'(sin titulo)')[:70]}")
    if n:
        print(f"      ultima: {ultima_res[fid]}")
print()
print(f"abiertas={abiertas} resueltas={len(pack)-abiertas} de {len(pack)}")
