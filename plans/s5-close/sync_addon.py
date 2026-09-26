"""Sincroniza los 12 ficheros de addon/ desde el arbol hermano DayZ_MCP\\.

addon/ esta trackeado pero con skip-worktree (sparse-checkout lo excluye del disco),
asi que `git add` no sirve. Se prepara por indice: hash-object -w --path (para que
aplique los mismos filtros de fin de linea que un add normal) + update-index.
"""
import os
import subprocess

DEV = r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev"
SIB = r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP"


def git(*args, **kw):
    r = subprocess.run(["git"] + list(args), cwd=DEV, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise SystemExit("git %s -> %s\n%s" % (" ".join(args), r.returncode, r.stderr))
    return r.stdout.strip()


paths = [p for p in git("ls-files", "--", "addon/").split("\n") if p]
assert len(paths) == 12, "esperaba 12 ficheros de addon, hay %d" % len(paths)

cambiados = []
for rel in paths:
    src = os.path.join(SIB, rel[len("addon/"):].replace("/", os.sep))
    assert os.path.isfile(src), "falta en el hermano: " + rel
    antes = git("rev-parse", ":" + rel)
    nuevo = git("hash-object", "-w", "--path", rel, src)
    if nuevo != antes:
        git("update-index", "--cacheinfo", "100644,%s,%s" % (nuevo, rel))
        cambiados.append((rel, antes[:8], nuevo[:8]))

print("addon/: %d de %d ficheros actualizados en el indice" % (len(cambiados), len(paths)))
for rel, a, b in cambiados:
    print("   %s  %s -> %s" % (rel, a, b))

# verificacion: el blob preparado debe coincidir con el hermano, byte a byte tras filtros
for rel in paths:
    src = os.path.join(SIB, rel[len("addon/"):].replace("/", os.sep))
    esperado = git("hash-object", "--path", rel, src)
    real = git("rev-parse", ":" + rel)
    assert esperado == real, "el indice no coincide con el hermano en " + rel
print("\nverificado: los 12 blobs del indice == el arbol hermano")

# y el bridge preparado debe traer infected_drive
blob = subprocess.run(["git", "cat-file", "-p", ":addon/scripts/5_Mission/MCPBridge.c"],
                      cwd=DEV, capture_output=True).stdout
n = blob.count(b"DispatchInfectedDrive")
print("DispatchInfectedDrive en el bridge preparado: %d ocurrencias" % n)
assert n == 2, "el bridge preparado no trae infected_drive"
