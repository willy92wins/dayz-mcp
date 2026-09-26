"""Cierra S-5 DESPUES del gate in-game: re-congela el centinela y deja todo preparado.

NO lo ejecutes antes de la corrida in-game. El centinela existe porque el fuente del
bridge no esta bajo control de version en el arbol hermano, y su contrato dice
re-congelar SOLO sobre estado gateado in-game. Un cambio de tag de log parece
cosmetico pero es lo que los seis .ps1 parsean: es comportamiento.

Uso:
    python close_s5.py --gated "<una linea describiendo la corrida in-game>"
"""
import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys

DEV = r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev"
ADDON = r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP"
TOOLS = os.path.join(DEV, "tools")
PY = os.path.join(TOOLS, ".venv-mcp", "Scripts", "python.exe")
TEST = os.path.join(TOOLS, "tests", "test_task9_spawn_phase_markers.py")
BRIDGE = os.path.join(ADDON, "scripts", "5_Mission", "MCPBridge.c")

ap = argparse.ArgumentParser()
ap.add_argument("--gated", required=True,
                help="Una linea con la evidencia de la corrida in-game (run id, que se vio).")
args = ap.parse_args()

# --- los hashes nuevos se OBTIENEN del propio test, que es la unica implementacion
#     autoritativa del stripping de marcadores. Se corre, se leen del fallo.
r = subprocess.run([PY, "-m", "unittest", "tests.test_task9_spawn_phase_markers"],
                   cwd=TOOLS, capture_output=True, text=True,
                   env={**os.environ, "PYTHONIOENCODING": "utf-8"})
if r.returncode == 0:
    print("El centinela ya esta verde: nada que re-congelar.")
    sys.exit(0)

import re
reales = re.findall(r"AssertionError: '([0-9A-F]{64})' != '([0-9A-F]{64})'", r.stderr)
assert len(reales) == 2, "esperaba los dos fallos del centinela, vi %d" % len(reales)
(new_full, old_full), (new_base, old_base) = reales

medido = hashlib.sha256(io.open(BRIDGE, "rb").read()).hexdigest().upper()
assert medido == new_full, "el bridge cambio entre la corrida del test y ahora"
print("hash completo   %s -> %s" % (old_full[:16], new_full[:16]))
print("hash sin marcas %s -> %s" % (old_base[:16], new_base[:16]))

NOTA = ('# Re-congelados sobre el bridge con el tag renombrado [MCP-POC] -> [DayZ-MCP] (S-5, :3490)\n'
        '# mas tres comentarios sin codigo de tracker privado. El renombrado NO es cosmetico: los seis\n'
        '# .ps1 de fase parsean ese prefijo (run-poc, run-fase1/2/3, run-s0-gate, spike0/mcp-grab-diag),\n'
        '# 21 sitios cambiados a la vez y verificados por conteo en los dos lados.\n'
        '# Gate in-game: %s\n' % args.gated.replace("\n", " "))

src = io.open(TEST, encoding="utf-8", newline="").read()
nl = "\r\n" if "\r\n" in src else "\n"
shutil.copy2(TEST, TEST + ".pre_s5")
out = (src.replace('BRIDGE_SHA256 = "%s"' % old_full,
                   NOTA.replace("\n", nl) + 'BRIDGE_SHA256 = "%s"' % new_full, 1)
          .replace('BASE_BRIDGE_SHA256 = "%s"' % old_base,
                   'BASE_BRIDGE_SHA256 = "%s"' % new_base, 1))
assert out != src, "no se pudo sustituir ningun hash"
io.open(TEST, "w", encoding="utf-8", newline="").write(out)
assert io.open(TEST, encoding="utf-8", newline="").read() == out

r2 = subprocess.run([PY, "-m", "unittest", "tests.test_task9_spawn_phase_markers"],
                    cwd=TOOLS, capture_output=True, text=True,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"})
print("\ncentinela tras re-congelar:", "VERDE" if r2.returncode == 0 else "SIGUE ROJO")
assert r2.returncode == 0
os.remove(TEST + ".pre_s5")

print("""
Hecho. Queda por hacer, en este orden:
  1. cd tools && .venv-mcp\\Scripts\\python.exe -m unittest discover -s tests -t .
     (deben quedar SOLO los 2 rojos del bundle del launcher, que se saltan en un clon)
  2. cd tools\\publish && ..\\.venv-mcp\\Scripts\\python.exe boundary.py
     (import check: clean, 0 private hits)
  3. sincronizar addon/ y commitear:  python sync_addon.py  (esta al lado de este script)
     git add -u && git commit && git push origin master:main
""")
