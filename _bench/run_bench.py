#!/usr/bin/env python3
"""Run the complete v8 adversarial acceptance matrix fail-closed."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "_gate.py"
REAL_CANDIDATE = ROOT / "tools" / "dayz_mcp" / "effective_schema.py"
BASE_SEED = 730031
CASE_TIMEOUT_SECONDS = 120
EXPECTED_REJECTS = 30
EXPECTED_ACCEPTS = 5
EXPECTED_LIMIT_ACCEPTS = 1
ATTACK_ARTIFACT_NAMES = ("_grok_probe_hook.py", "_grok_probe_hook.pth")


@dataclass(frozen=True)
class Case:
    path: Path
    group: str
    expected: str
    use_default_candidate: bool = False


@dataclass(frozen=True)
class Result:
    case: Case
    obtained: str
    returncode: int | str
    ok: bool
    stdout: str
    stderr: str


def _discover_cases() -> list[Case]:
    bank = ROOT / "_bench"
    rejects = sorted((bank / "must_reject").glob("*.py"))
    accepts = sorted((bank / "must_accept").glob("*.py"))
    limit_accepts = sorted((bank / "accepted_by_declared_limit").glob("*.py"))
    actual = (len(rejects), len(accepts), len(limit_accepts))
    expected = (EXPECTED_REJECTS, EXPECTED_ACCEPTS, EXPECTED_LIMIT_ACCEPTS)
    if actual != expected:
        raise RuntimeError(
            "invalid bank shape: expected "
            f"{EXPECTED_REJECTS} reject + {EXPECTED_ACCEPTS} accept + "
            f"{EXPECTED_LIMIT_ACCEPTS} accepted-by-limit, got "
            f"{len(rejects)} reject + {len(accepts)} accept + "
            f"{len(limit_accepts)} accepted-by-limit"
        )
    return (
        [Case(path, "must_reject", "S7-GATE-FAIL") for path in rejects]
        + [Case(path, "must_accept", "S7-GATE-OK") for path in accepts]
        + [
            Case(path, "accepted_by_declared_limit", "S7-GATE-OK")
            for path in limit_accepts
        ]
        + [
            Case(
                REAL_CANDIDATE,
                "real_module",
                "S7-GATE-OK",
                use_default_candidate=True,
            )
        ]
    )


def _run_case(case: Case, index: int) -> Result:
    command = [
        sys.executable,
        "-I",
        "-S",
        "-B",
        str(GATE),
        "--seed",
        str(BASE_SEED + index),
    ]
    if not case.use_default_candidate:
        command.extend(("--candidate", str(case.path)))
    try:
        process = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=CASE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        return Result(
            case=case,
            obtained="<TIMEOUT>",
            returncode="-",
            ok=False,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
        )
    lines = process.stdout.splitlines()
    obtained = lines[-1] if lines else "<NO-VERDICT>"
    expected_rc = 0 if case.expected == "S7-GATE-OK" else 1
    environment_failure = "S7-GATE-ENVIRONMENT: FAIL" in process.stdout
    ok = (
        not environment_failure
        and obtained == case.expected
        and process.returncode == expected_rc
    )
    return Result(
        case=case,
        obtained=obtained,
        returncode=process.returncode,
        ok=ok,
        stdout=process.stdout,
        stderr=process.stderr,
    )


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _cleanup_attack_artifacts() -> list[str]:
    site_packages = (
        Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages"
    ).resolve()
    problems: list[str] = []
    for name in ATTACK_ARTIFACT_NAMES:
        path = (site_packages / name).resolve()
        if path.parent != site_packages:
            problems.append(f"refusing unsafe cleanup target: {path}")
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            problems.append(f"could not remove {path}: {exc}")
    return problems


def main() -> int:
    try:
        cases = _discover_cases()
    except RuntimeError as exc:
        print(f"BENCH-CONFIG-ERROR: {exc}")
        print("BENCH_RC=1")
        return 1
    results = [_run_case(case, index) for index, case in enumerate(cases)]
    cleanup_problems = _cleanup_attack_artifacts()
    paths = [_display_path(result.case.path) for result in results]
    path_width = max(len("candidato"), *(len(path) for path in paths))
    group_width = max(len("grupo"), *(len(result.case.group) for result in results))
    print(
        f"{'grupo':<{group_width}} | {'candidato':<{path_width}} | "
        "esperado     | obtenido     | rc | OK/MAL"
    )
    print(
        "-" * group_width
        + "-+-"
        + "-" * path_width
        + "-+--------------+--------------+----+-------"
    )
    for path, result in zip(paths, results, strict=True):
        print(
            f"{result.case.group:<{group_width}} | {path:<{path_width}} | "
            f"{result.case.expected:<12} | {result.obtained:<12} | "
            f"{str(result.returncode):<2} | {'OK' if result.ok else 'MAL'}"
        )
    failures = [result for result in results if not result.ok]
    for result in failures:
        print(
            f"\n--- FAILURE [{result.case.group}]: "
            f"{_display_path(result.case.path)} ---"
        )
        if result.stdout:
            print(result.stdout.rstrip())
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
    for problem in cleanup_problems:
        print(f"BENCH-CLEANUP-ERROR: {problem}")
    returncode = 1 if failures or cleanup_problems else 0
    print(f"BENCH_RC={returncode}")
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
