# -*- coding: utf-8 -*-
"""Integrate a delivered workspace into the repo: exact paths, LF-normalised, hash-verified, then
the FULL suite in the repo.

Usage: integrar.py <ws-tools-dir> <repo-tools-dir> --files <rel>... [--run-suite] [--dry-run]

Rules encoded here (all measured in this project):
  - The worker's editor may deliver CRLF whole files: every copied file is normalised to LF
    before writing (the repo is LF; a CRLF file would show as a whole-file diff).
  - OneDrive: write once per file and verify by sha256 of the bytes just written vs the bytes
    intended (read-after-write). No Edit tool, no partial writes.
  - Only the listed relative paths are touched; nothing else in the repo changes.
  - The full suite runs IN THE REPO (not in a copy): `python -m unittest discover -s tests -t .`
    from the repo tools dir with PYTHONPATH=. — the earlier copy-based measurement was invalid.
Exit 0 only if every file verified and (if requested) the suite ran; the suite verdict is
printed, not judged: compare its failures against the recorded baseline by NAME.
"""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path


def lf_bytes(p: Path) -> bytes:
    return p.read_bytes().replace(b"\r\n", b"\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ws_tools")
    ap.add_argument("repo_tools")
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--run-suite", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ws = Path(a.ws_tools).resolve()
    repo = Path(a.repo_tools).resolve()
    ok = True
    print(f"ws={ws}\nrepo={repo}")
    for rel in a.files:
        src, dst = ws / rel, repo / rel
        if not src.is_file():
            print(f"  FALTA en ws: {rel}"); ok = False; continue
        data = lf_bytes(src)
        before = hashlib.sha256(dst.read_bytes()).hexdigest()[:12] if dst.is_file() else "nuevo"
        target = hashlib.sha256(data).hexdigest()
        crlf_src = src.read_bytes().count(b"\r\n")
        if a.dry_run:
            print(f"  [dry] {rel}: repo={before} -> {target[:12]} (CRLF en origen: {crlf_src}, {len(data)} B)")
            continue
        dst.write_bytes(data)
        got = hashlib.sha256(dst.read_bytes()).hexdigest()
        verified = got == target
        ok &= verified
        print(f"  {rel}: repo={before} -> {got[:12]} {'VERIFICADO' if verified else 'MISMATCH'} (CRLF normalizados: {crlf_src})")
    if a.run_suite and not a.dry_run:
        print("\nsuite COMPLETA en el repo (unittest discover):")
        py = repo / ".venv-mcp" / "Scripts" / "python.exe"
        r = subprocess.run(
            [str(py), "-m", "unittest", "discover", "-s", "tests", "-t", "."],
            cwd=str(repo), capture_output=True, text=True,
            env={**__import__("os").environ, "PYTHONPATH": ".", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        lines = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()]
        fails = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
        print("  " + " | ".join(lines[-3:]))
        print(f"  rc={r.returncode}  rojos por nombre ({len(fails)}):")
        for l in fails:
            print("    " + l[:160])
    print("INTEGRACION:", "OK" if ok else "NO OK")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
