"""Run only the two offline PS harnesses and seven isolated mutations, serially.

Usage (repository venv): python run-r3.py [--mutations]
All writes stay under this file's directory and are immediately read back.
Never invokes real AddonBuilder, DayZ, the daemon, or the Python suite.
"""
from pathlib import Path
import argparse
import hashlib
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SUBJECT = ROOT / "dayz-test.ps1"
HARNESS = ROOT / "test-af59.ps1"


def write(path, data):
    assert path.resolve().is_relative_to(ROOT), path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    assert path.stat().st_size == len(data) and path.read_bytes() == data, path


def run(harness, subject):
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(harness), "-Script", str(subject)]
    print("COMMAND: " + subprocess.list2cmdline(command), flush=True)
    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=300)
    log = ("COMMAND: " + subprocess.list2cmdline(command) + "\r\n").encode()
    log += result.stdout + ("\r\nEXIT_CODE=%d\r\n" % result.returncode).encode()
    rows = re.findall(rb"^(S\d+|H\d+) (PASS|FAIL)\b", result.stdout, re.M)
    print(result.stdout.decode("cp1252", errors="replace"), flush=True)
    print("EXIT_CODE=%d" % result.returncode, flush=True)
    return result.returncode, rows, log


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutations", action="store_true")
    args = parser.parse_args()
    frozen = {p: p.read_bytes() for p in (SUBJECT, HARNESS, ROOT / "test-H1.ps1")}
    expected = [f"S{i}".encode() for i in range(1, 17)]
    code, rows, log = run(HARNESS, SUBJECT)
    write(ROOT / "gate-r3.log", log)
    assert code == 0 and rows == [(i, b"PASS") for i in expected], "gate failed"
    code, rows, log = run(ROOT / "test-H1.ps1", SUBJECT)
    write(ROOT / "H1-r3.log", log)
    assert code == 0 and rows == [(f"H{i}".encode(), b"PASS") for i in range(11, 17)], "H1 failed"
    if args.mutations:
        src = frozen[SUBJECT].decode("utf-8")
        count_line = "    $srcC = @(Get-ChildItem -LiteralPath $src -Recurse -File | Where-Object { $_.Extension -eq '.c' }).Count"
        unsupported = 'catch [NotSupportedException] { Warn "PBO script check INCONCLUSIVE (unsupported format): $($_.Exception.Message)" }'
        invalid = 'catch [IO.InvalidDataException] { Die "Invalid PBO: $($_.Exception.Message)" }'
        guard = "if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -and $env:DAYZ_ALLOW_PLAIN_MODS -ne '1') {"
        mutations = [
            ("M3", "S10", "unknown reader format becomes fatal", unsupported,
             unsupported.replace(" Warn ", " Die "), "verdict mismatch"),
            ("M4", "S11", "proven corruption is downgraded to warning", invalid,
             invalid.replace(" Die ", " Warn "), "verdict mismatch"),
            ("M5", "S12", "partial R2 fix: scripts subtree preferred whenever present", count_line,
             "    $countRoot = if (Test-Path -LiteralPath (Join-Path $src 'scripts') -PathType Container) { Join-Path $src 'scripts' } else { $src }\n"
             + count_line.replace("-LiteralPath $src ", "-LiteralPath $countRoot "), "R2a:"),
            ("M6", "S13", "partial R2 fix: keep the old scripts-directory prerequisite",
             "    if ($srcC -gt 0) {",
             "    if ($srcC -gt 0 -and (Test-Path -LiteralPath (Join-Path $src 'scripts') -PathType Container)) {", "R2b:"),
            ("M7", "S14", "standard Vers parsing is unavailable",
             "                    $knownLayout = $true\n                    while ($true) {",
             "                    throw [NotSupportedException]::new('M7: Vers parsing unavailable.')\n                    $knownLayout = $true\n                    while ($true) {", "R3:"),
            ("M8", "S15", "existing plain destinations bypass type guard", guard,
             "if ($false) {", "R4: builder ran"),
            ("M9", "S16", "require attestation even for prepared junctions", guard,
             "if ($env:DAYZ_ALLOW_PLAIN_MODS -ne '1') {", "verdict mismatch"),
        ]
        combined = b"# R3 isolated mutations: original S1-S9 remain active\r\n"
        write(ROOT / "mutants-r3.log", combined)
        for name, target, description, old, new, reason in mutations:
            assert src.count(old) == 1, (name, "mutation anchor count", src.count(old))
            mutant = ROOT / "mutants-r3" / (name + ".ps1")
            write(mutant, src.replace(old, new, 1).encode())
            code, rows, log = run(HARNESS, mutant)
            failures = [i for i, verdict in rows if verdict == b"FAIL"]
            isolated = (code == 1 and [i for i, _ in rows] == expected
                        and failures == [target.encode()] and reason.encode() in log)
            section = (f"\r\n## {name} -> {target}: {description}\r\n"
                       f"MUTANT_SHA256={hashlib.sha256(mutant.read_bytes()).hexdigest()}\r\n").encode()
            section += log + (f"ISOLATED={str(isolated).lower()} expected_failure={target}\r\n").encode()
            write(ROOT / (name + "-r3.log"), section)
            combined += section
            write(ROOT / "mutants-r3.log", combined)
            assert isolated, (name, failures, "mutation not isolated")
        combined += b"\r\nMUTATIONS_ISOLATED\r\n"
        write(ROOT / "mutants-r3.log", combined)
        print("MUTATIONS_ISOLATED", flush=True)
    assert all(p.read_bytes() == data for p, data in frozen.items()), "subject/harness changed during run"
    print("R3_GATES_VERIFIED", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
