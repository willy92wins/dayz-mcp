# -*- coding: utf-8 -*-
"""Prepare round N of a lote: ledger append, STATE archive, gate seal.

Usage: preparar_ronda.py <lote-dir> <N> [--oracle-source <path>]

Steps, each verified by read-after-write:
  1. If <lote-dir>/gates_ronda<N>.md exists and GATES.md lacks "## Ronda <N>", append it.
  2. If --oracle-source is given, assert it is byte-identical to ws/gate/oracle.py: sealing a
     diverged copy is what broke the round-4 seal.
  3. Archive ws/STATE.md (previous round's worker state) to runs<N-1>/STATE-ronda<N-1>.md so
     the new worker never sees the previous one's report.
  4. Create runs<N>/ and write GATE-SEAL.txt with LF only (write_text would emit CRLF on
     Windows and `sha256sum -c` chokes on the \r).
  5. Re-verify the seal against the files on disk and print it.
Exit 0 only if every step verified.
"""
import argparse
import hashlib
import shutil
import sys
from pathlib import Path

# Every regular file under gate/ is sealed (lote I brought gates that are not oracle.py/run.sh/
# suite.sh); GATE-SEAL.txt itself is never part of the seal.
def sealed_names(gate: Path) -> tuple[str, ...]:
    return tuple(sorted(p.name for p in gate.iterdir() if p.is_file() and p.name != "GATE-SEAL.txt"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("lote")
    ap.add_argument("n", type=int)
    ap.add_argument("--oracle-source", default=None)
    a = ap.parse_args()

    lote = Path(a.lote).resolve()
    gate = lote / "ws" / "gate"
    if not gate.is_dir():
        print(f"no gate dir at {gate}", file=sys.stderr)
        return 2

    # 1. ledger
    gates = gate / "GATES.md"
    seccion = lote / f"gates_ronda{a.n}.md"
    texto = gates.read_text(encoding="utf-8")
    if seccion.is_file() and f"## Ronda {a.n}" not in texto:
        gates.write_text(texto.rstrip("\n") + "\n" + seccion.read_text(encoding="utf-8"),
                         encoding="utf-8", newline="\n")
        vuelto = gates.read_text(encoding="utf-8")
        assert f"## Ronda {a.n}" in vuelto, "the ledger section did not land"
        print(f"GATES.md: ronda {a.n} anexada ({len(vuelto.splitlines())} lineas)")
    else:
        print(f"GATES.md: sin cambios ({len(texto.splitlines())} lineas)")

    # 2. oracle source == sealed copy
    if a.oracle_source:
        src = Path(a.oracle_source)
        if sha(src) != sha(gate / "oracle.py"):
            print("oracle source and sealed copy DIVERGE: not sealing", file=sys.stderr)
            return 1
        print("oracle fuente == sellada:", sha(src)[:16])

    # 3. archive previous STATE
    state = lote / "ws" / "STATE.md"
    if state.is_file():
        prev = lote / f"runs{a.n - 1}"
        prev.mkdir(exist_ok=True)
        destino = prev / f"STATE-ronda{a.n - 1}.md"
        shutil.move(str(state), str(destino))
        assert destino.is_file() and not state.exists(), "STATE archive failed"
        print(f"STATE.md archivado en {destino.relative_to(lote)}")

    # 4. seal
    runs = lote / f"runs{a.n}"
    runs.mkdir(exist_ok=True)
    SEALED = sealed_names(gate)
    lineas = [f"{sha(gate / n)} *{n}" for n in SEALED]
    sello = runs / "GATE-SEAL.txt"
    sello.write_bytes(("\n".join(lineas) + "\n").encode("utf-8"))
    crudo = sello.read_bytes()
    assert crudo.count(b"\r") == 0, "CR in seal"

    # 5. re-verify from disk
    for linea in crudo.decode("utf-8").splitlines():
        h, nombre = linea.split(" *")
        assert sha(gate / nombre) == h, f"seal mismatch on {nombre}"
    print(f"\nsello {sello.relative_to(lote)} verificado ({len(SEALED)}/{len(SEALED)}):")
    print(crudo.decode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
