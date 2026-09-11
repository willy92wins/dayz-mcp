# -*- coding: utf-8 -*-
"""Receive a worker delivery: seal, write-set, hashes, LF-normalised diff, gates run by ME.

Usage: recibir.py <lote-dir> <N> --allow <relpath>... [--baseline <dir>] [--freeze]

Nothing the worker pasted is trusted: the gates are re-run here and their last lines printed.
  1. Seal: every sealed gate file still matches runs<N>/GATE-SEAL.txt.
  2. Write-set: files under ws/ modified after runs<N>/STARTED must all be in --allow.
  3. Hashes of the allowed product files (to pin the review briefs to bytes).
  4. Diff vs --baseline (a frozen copy of the previous delivery), LF-normalised: the worker's
     editor rewrites whole files CRLF, and without normalising the diff overstates massively.
  5. Run gate/run.sh and gate/suite.sh; print their verdict lines.
  6. --freeze: copy ws/ to ws-frozen-r<N>/ as the baseline for the next round.
Exit 0 only if seal OK, write-set OK and both gates green; the report prints regardless.
"""
import argparse
import difflib
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _git_bash() -> str:
    for candidate in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "bin" / "bash.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return "bash"


def lf(p: Path) -> list[str]:
    return p.read_bytes().replace(b"\r\n", b"\n").decode("utf-8", "replace").splitlines()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("lote")
    ap.add_argument("n", type=int)
    ap.add_argument("--allow", nargs="+", required=True)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--freeze", action="store_true")
    a = ap.parse_args()

    lote = Path(a.lote).resolve()
    ws = lote / "ws"
    runs = lote / f"runs{a.n}"
    ok = True

    # 1. seal
    sello = (runs / "GATE-SEAL.txt").read_text(encoding="utf-8").splitlines()
    rotos = []
    for linea in sello:
        h, nombre = linea.split(" *")
        if sha(ws / "gate" / nombre) != h:
            rotos.append(nombre)
    print(f"[1] sello: {len(sello) - len(rotos)}/{len(sello)} intactos" + (f" ROTOS={rotos}" if rotos else ""))
    ok &= not rotos

    # 2. write-set
    started = int((runs / "STARTED").read_text().strip())
    permitidos = {Path(p).as_posix() for p in a.allow}
    tocados, fuera = [], []
    for p in ws.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and p.stat().st_mtime >= started:
            rel = p.relative_to(ws).as_posix()
            # gate/ is covered by the seal (content hash) in step 1; STARTED has whole-second
            # precision, so a ledger appended by preparar_ronda.py 160 ms before the runner
            # started showed up here as "touched". Measured on cierre 3 of lote H.
            if rel.startswith("gate/"):
                continue
            tocados.append(rel)
            # STATE.md is the worker's report; BRIEF.txt is the brief the Cursor runner copies
            # into the workspace (its .cmd wrapper cannot take a multi-line prompt).
            # suite_full-last.txt is written by gate/suite_full.sh itself (2026-09-07, lote G2:
            # it showed up as "outside the allowlist" on a delivery whose three gates were all
            # green -- the instrument accusing the worker of its own artefact).
            if rel not in permitidos and rel not in ("STATE.md", "BRIEF.txt", "suite_full-last.txt"):
                fuera.append(rel)
    print(f"[2] write-set: {len(tocados)} ficheros tocados desde STARTED; fuera del allowlist: {fuera or 'ninguno'}")
    for rel in sorted(tocados):
        print(f"      {rel}")
    ok &= not fuera

    # 3. hashes
    print("[3] hashes del producto:")
    for rel in a.allow:
        p = ws / rel
        if p.is_file():
            print(f"      {sha(p)}  {rel}")

    # 4. LF-normalised diff vs baseline
    if a.baseline:
        base = Path(a.baseline).resolve()
        print(f"[4] diff LF-normalizado contra {base}:")
        total_add = total_del = 0
        for rel in a.allow:
            p, q = ws / rel, base / rel
            if not p.is_file():
                continue
            if not q.is_file():
                print(f"      {rel}: NUEVO ({len(lf(p))} lineas)")
                continue
            d = list(difflib.unified_diff(lf(q), lf(p), lineterm="", n=0))
            add = sum(1 for x in d if x.startswith("+") and not x.startswith("+++"))
            dele = sum(1 for x in d if x.startswith("-") and not x.startswith("---"))
            crlf = p.read_bytes().count(b"\r\n")
            total_add += add; total_del += dele
            print(f"      {rel}: +{add} -{dele}   (CRLF en el fichero entregado: {crlf})")
        print(f"      TOTAL +{total_add} -{total_del}")
    else:
        print("[4] sin baseline: diff no medido")

    # 5. gates, run here
    print("[5] gates (corridos aqui, no pegados):")
    verdes = 0
    # gate/EXPECT.txt (one "script|expected verdict line" per line) names the gates to run; the
    # pair run.sh/suite.sh is the default for the lotes that predate it (lote I added it).
    expect = ws / "gate" / "EXPECT.txt"
    if expect.is_file():
        gates = [tuple(l.split("|", 1)) for l in expect.read_text(encoding="utf-8").splitlines() if "|" in l]
    else:
        gates = [("run.sh", "ORACULO-VERDE"), ("suite.sh", "SUITE-ACOTADA OK")]
    for script, esperado in gates:
        # Relative path with cwd=ws: bash mangles a Windows absolute path ("C:Usersguill...").
        # And the bash must be Git's: a bare "bash" resolves to WSL's System32\bash.exe from a
        # Python subprocess, where the gate's "C:\...\python.exe" is "command not found".
        # Both measured on the first real receipt; the dry run had no gate to execute.
        r = subprocess.run([_git_bash(), f"gate/{script}"], cwd=str(ws), capture_output=True, text=True)
        cola = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()][-2:]
        verde = esperado in r.stdout and r.returncode == 0
        verdes += verde
        print(f"      {script}: rc={r.returncode} {'VERDE' if verde else 'ROJO'} | " + " | ".join(cola))
        if not verde:
            # A red gate is only actionable by NAME: the two-line tail of suite_full.sh hid which
            # tests were red (lote I, round 1: one flaky e2e test; the rerun was green).
            for l in [l for l in (r.stdout + r.stderr).splitlines() if l.strip()][-40:-2]:
                print(f"          {l[:200]}")
    ok &= verdes == len(gates)

    # 6. freeze
    if a.freeze:
        destino = lote / f"ws-frozen-r{a.n}"
        if destino.exists():
            shutil.rmtree(destino)
        shutil.copytree(ws, destino, ignore=shutil.ignore_patterns("__pycache__"))
        print(f"[6] congelado en {destino.name} ({sum(1 for _ in destino.rglob('*') if _.is_file())} ficheros)")

    print("RECEPCION:", "OK" if ok else "NO OK")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
