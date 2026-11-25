# -*- coding: utf-8 -*-
"""Run a mechanical line-deletion mutation sweep over lines added since a base commit.

Usage:
  mutation_sweep.py --base <rev> --tree <dir> --module tests.test_x [--module ...]
                    [--python <exe>] [--context N] [--timeout SECONDS]
                    [--include GLOB]... [--exclude GLOB]...
                    [--json PATH] [--list] [--no-isolate] [--keep-env]

Contract, in order:
  1. resolve ``--base`` and ``HEAD`` to commits via git;
  2. select diff files (``<base>...HEAD``), verify none differ from ``HEAD``, record bytes/sha256;
  3. build unique deletion candidates; ``--list`` prints them, writes JSON (status ``"listed"``),
     and exits 0 without running tests or touching the tree;
  4. run every unittest module unmutated as the base run; if any is red, abort untouched;
  5. for each mutant (path/line order), check sha256, write mutated bytes, run modules with ``-f``
     stopping at first red/timeout (killing the process tree), restore + verify sha256 in ``finally``;
  6. verify every selected file matches its original sha256 after the sweep;
  7. on Ctrl+C, restore the active file, print completed progress, and exit 130.

Notes:
  - Context rule: a non-unique line is widened with up to N lines on each side (``--context N``,
    default 5); ``--context 0`` skips non-unique lines.
  - No-test base: a base module running no test (no ``Ran`` line, ``Ran 0 tests``, or all skipped)
    counts as red and aborts with exit 2.
  - CRLF caveat: ``mutation_gate.py --old @f`` reads ``f`` with universal newlines, so a multi-line
    ``old``/``new`` from a CRLF file cannot be replayed directly through it.

Exit codes: 0 completed, 2 invalid input/git/dirty/red base, 3 integrity/restore error, 130 Ctrl+C.
"""
from __future__ import annotations

import argparse
from collections import namedtuple
import contextlib
import fnmatch
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tokenize

_CLIKE_SUFFIXES = frozenset({".c", ".cpp", ".h", ".hpp", ".cs", ".js", ".ts"})
_PS_SUFFIXES = frozenset({".ps1", ".psm1"})
_DEFAULT_SUFFIXES = _CLIKE_SUFFIXES | _PS_SUFFIXES | {".py", ".sh"}
_BRACKET_ONLY = frozenset("{}()[];,")
_NON_CODE_TOKENS = frozenset({
    tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER,
})
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_RAN_RE = re.compile(r"\bRan (\d+) tests?\b")
_SKIPPED_RE = re.compile(r"\bskipped=(\d+)\b")

_Original = namedtuple("_Original", "rel_path old_path full_path raw sha256 text")
_Candidate = namedtuple("_Candidate", "path line text context old new")


def _err(msg: str, code: int = 2, *extra: str) -> int:
    print(f"!! {msg}", file=sys.stderr)
    for line in extra:
        print(f"   {line}", file=sys.stderr)
    return code


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run_git(tree: Path, args: list[str]) -> tuple[int, bytes, str]:
    try:
        proc = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(tree), capture_output=True)
    except OSError as exc:
        return -1, b"", str(exc)
    return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", errors="replace").strip()


def _resolve_commit(tree: Path, rev: str) -> tuple[str | None, str]:
    rc, out, err = _run_git(tree, ["rev-parse", "--verify", f"{rev}^{{commit}}"])
    sha = out.decode("utf-8", errors="replace").strip() if rc == 0 else ""
    return (sha, "") if sha else (None, err or f"git rev-parse devolvio {rc}")


def _list_diff_entries(tree: Path, base_sha: str) -> tuple[list[tuple[str | None, str]] | None, str]:
    rc, out, err = _run_git(tree, ["diff", "--no-color", "--no-ext-diff", "--name-status", "-z", "-M", f"{base_sha}...HEAD"])
    if rc != 0:
        return None, err or f"git diff --name-status devolvio {rc}"
    parts = [p for p in out.decode("utf-8", errors="replace").split("\0") if p]
    entries, i = [], 0
    while i < len(parts):
        code = parts[i][0]
        if code in ("R", "C") and i + 2 < len(parts):
            entries.append((parts[i + 1], parts[i + 2]))
            i += 3
        elif i + 1 < len(parts):
            if code in ("A", "M"):
                entries.append((None, parts[i + 1]))
            i += 2
        else:
            break
    return entries, ""


def _select_entries(entries: list[tuple[str | None, str]], includes: list[str], excludes: list[str]) -> list[tuple[str | None, str]]:
    def _keep(rel: str) -> bool:
        if any(fnmatch.fnmatchcase(rel, pat) for pat in excludes):
            return False
        if includes:
            return any(fnmatch.fnmatchcase(rel, pat) for pat in includes)
        p = PurePosixPath(rel)
        is_test = (
            any(s in ("tests", "test") for s in p.parts) or p.name == "conftest.py"
            or fnmatch.fnmatchcase(p.name, "test_*") or fnmatch.fnmatchcase(p.name, "*_test.*")
        )
        return p.suffix.lower() in _DEFAULT_SUFFIXES and not is_test

    return sorted((item for item in entries if _keep(item[1])), key=lambda item: item[1])


def _check_clean_working_tree(tree: Path, paths: list[str]) -> tuple[list[str] | None, str]:
    if not paths:
        return [], ""
    rc, out, err = _run_git(tree, ["status", "--porcelain", "-z", "--", *paths])
    if rc != 0:
        return None, err or f"git status devolvio {rc}"
    tokens = [t for t in out.decode("utf-8", errors="replace").split("\0") if t]
    dirty, i = [], 0
    while i < len(tokens):
        dirty.append(tokens[i][3:])
        i += 2 if tokens[i][0] in ("R", "C") else 1
    return dirty, ""


def _added_lines_for_file(tree: Path, base_sha: str, old_path: str | None, new_path: str) -> tuple[list[int] | None, str]:
    diff_paths = [old_path, new_path] if old_path is not None else [new_path]
    cmd = ["diff", "--no-color", "--no-ext-diff", "--unified=0", "-M", "--src-prefix=a/", "--dst-prefix=b/", f"{base_sha}...HEAD", "--", *diff_paths]
    rc, out, err = _run_git(tree, cmd)
    if rc != 0:
        return None, err or f"git diff devolvio {rc} para {new_path}"
    expected_dst, in_target, added = f"b/{new_path}", False, set()
    for raw_line in out.decode("utf-8", errors="replace").splitlines():
        if raw_line.startswith("diff --git "):
            in_target = False
        elif raw_line.startswith("+++ "):
            hdr = raw_line[4:]
            in_target = hdr != "/dev/null" and (old_path is None or hdr in (expected_dst, f"{expected_dst}\t"))
        elif in_target and (m := _HUNK_RE.match(raw_line)) is not None:
            start, count = int(m.group(1)), (1 if m.group(2) is None else int(m.group(2)))
            added.update(range(start, start + count))
    return sorted(added), ""


def _index_lines(text: str) -> tuple[list[int], list[str]]:
    """Return 1-based (index 0 unused) start offsets and line contents without trailing CR."""
    starts, contents, offset = [0], [""], 0
    for idx, seg in enumerate(text.split("\n")):
        line_start, content = offset, (seg[:-1] if seg.endswith("\r") else seg)
        if idx == 0 and content.startswith("\ufeff"):
            line_start, content = line_start + 1, content[1:]
        starts.append(line_start)
        contents.append(content)
        offset += len(seg) + 1
    return starts, contents


def _python_code_lines(text: str, contents: list[str]) -> set[int]:
    clean = text[1:] if text.startswith("\ufeff") else text
    code_lines, stmt = set(), []

    def _flush() -> None:
        if stmt and not all(tok.type == tokenize.STRING for tok in stmt):
            for tok in stmt:
                code_lines.update(range(tok.start[0], tok.end[0] + 1))
        stmt.clear()

    try:
        for tok in tokenize.generate_tokens(io.StringIO(clean).readline):
            if tok.type in (tokenize.NEWLINE, tokenize.ENDMARKER):
                _flush()
            elif tok.type not in _NON_CODE_TOKENS:
                stmt.append(tok)
        _flush()
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return {i for i in range(1, len(contents)) if not contents[i].lstrip().startswith("#")}
    return code_lines


def _scan_code_lines(
    contents: list[str], line_cmt: str, block_open: str, block_close: str, *, ps_quotes: bool = False,
) -> set[int]:
    code_lines: set[int] = set()
    in_block, in_quote = False, None
    for line_no, line in enumerate(contents[1:], start=1):
        n, i, has_code, escaped_eol = len(line), 0, False, False
        while i < n:
            ch = line[i]
            if in_block:
                in_block, i = (False, i + len(block_close)) if line.startswith(block_close, i) else (True, i + 1)
            elif in_quote is not None:
                has_code = has_code or not ch.isspace()
                if not ps_quotes and ch == "\\":
                    escaped_eol, i = (i + 1 >= n), min(i + 2, n)
                elif ps_quotes and ((in_quote == '"' and ch == "`" and i + 1 < n) or (in_quote == "'" and line.startswith("''", i))):
                    i += 2
                else:
                    in_quote, i = (None if ch == in_quote else in_quote), i + 1
            elif line.startswith(block_open, i):
                in_block, i = True, i + len(block_open)
            elif line.startswith(line_cmt, i):
                break
            elif ch in ('"', "'"):
                has_code, in_quote, i = True, ch, i + 1
            else:
                has_code, i = (has_code or not ch.isspace()), i + 1
        if in_quote is not None and (ps_quotes or not escaped_eol):
            in_quote = None
        if has_code:
            code_lines.add(line_no)
    return code_lines


def _code_lines_for_file(rel_path: str, text: str, contents: list[str]) -> set[int]:
    suffix = PurePosixPath(rel_path).suffix.lower()
    if suffix == ".py":
        return _python_code_lines(text, contents)
    if suffix in _CLIKE_SUFFIXES:
        return _scan_code_lines(contents, "//", "/*", "*/")
    if suffix in _PS_SUFFIXES:
        return _scan_code_lines(contents, "#", "<#", "#>", ps_quotes=True)
    if suffix == ".sh":
        return {i for i in range(1, len(contents)) if not contents[i].lstrip().startswith("#")}
    return set(range(1, len(contents)))


def _count_overlapping(text: str, old: str) -> int:
    if not old:
        return 0
    count, pos = 0, 0
    while count < 2:
        idx = text.find(old, pos)
        if idx < 0:
            break
        count += 1
        pos = idx + 1
    return count


def _build_mutant(text: str, starts: list[int], contents: list[str], line_no: int, max_context: int) -> tuple[int, str, str] | None:
    line_count = len(contents) - 1
    for k in range(max_context + 1):
        if k == 0:
            old, new = contents[line_no], ""
        else:
            lo, hi = max(1, line_no - k), min(line_count, line_no + k)
            end_hi = starts[hi] + len(contents[hi])
            old = text[starts[lo] : end_hi]
            new = text[starts[lo] : starts[line_no]] + text[starts[line_no] + len(contents[line_no]) : end_hi]
        if _count_overlapping(text, old) == 1:
            return k, old, new
    return None


def _collect_candidates(tree: Path, base_sha: str, originals: list[_Original], max_context: int) -> tuple[list[_Candidate], list[dict[str, object]], str | None]:
    candidates, skipped = [], []
    for orig in originals:
        added, err = _added_lines_for_file(tree, base_sha, orig.old_path, orig.rel_path)
        if added is None:
            return [], [], f"fallo al obtener lineas anadidas de {orig.rel_path}: {err}"
        starts, contents = _index_lines(orig.text)
        line_count, code_lines = len(contents) - 1, _code_lines_for_file(orig.rel_path, orig.text, contents)
        for line_no in added:
            if not (1 <= line_no <= line_count) or line_no not in code_lines:
                continue
            stripped = contents[line_no].strip()
            if not stripped or all(ch in _BRACKET_ONLY for ch in stripped):
                continue
            built = _build_mutant(orig.text, starts, contents, line_no, max_context)
            if built is None:
                skipped.append({"path": orig.rel_path, "line": line_no, "text": stripped})
            else:
                candidates.append(_Candidate(orig.rel_path, line_no, stripped, *built))
    return candidates, skipped, None


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15, check=False)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
        return
    except (OSError, subprocess.TimeoutExpired):
        with contextlib.suppress(OSError):
            proc.kill()


def _ran_no_tests(summary: str) -> bool:
    ran_m = _RAN_RE.search(summary)
    ran = int(ran_m.group(1)) if ran_m else 0
    skip_m = _SKIPPED_RE.search(summary)
    return ran == 0 or (skip_m is not None and int(skip_m.group(1)) >= ran)


def _run_unittest_module(
    python: str, cwd: Path, module: str, *, failfast: bool, env: dict[str, str] | None, timeout: float | None,
) -> tuple[int, bool, float, str]:
    cmd = [python, "-B", "-m", "unittest", *(["-f"] if failfast else []), module]
    kw: dict[str, object] = {"cwd": str(cwd), "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True, "encoding": "utf-8", "errors": "replace"}
    if env is not None:
        kw["env"] = env
    if os.name != "nt":
        kw["start_new_session"] = True
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, **kw)  # type: ignore[call-overload]
    except OSError as exc:
        return -1, False, time.monotonic() - t0, str(exc)
    timed_out, stdout, stderr = False, "", ""
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_tree(proc)
        for attempt in range(2):
            try:
                stdout, stderr = proc.communicate(timeout=5)
                break
            except subprocess.TimeoutExpired:
                if attempt == 0:
                    with contextlib.suppress(OSError):
                        proc.kill()
    except KeyboardInterrupt:
        _kill_process_tree(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        raise
    finally:
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
    elapsed = time.monotonic() - t0
    rc = -1 if timed_out else (proc.returncode if proc.returncode is not None else -1)
    lines = [ln for ln in ((stdout or "") + "\n" + (stderr or "")).splitlines() if ln.startswith(("Ran ", "OK", "FAILED", "NO TESTS RAN"))]
    summary = " ".join(lines[-2:])
    if timed_out:
        tag = f"TIMEOUT ({timeout:.1f}s)" if timeout is not None else "TIMEOUT"
        summary = f"{summary} {tag}".strip()
    return rc, timed_out, elapsed, summary


def _run_mutant(python: str, cwd: Path, modules: list[str], env: dict[str, str] | None, timeouts: dict[str, float]) -> tuple[str, float, str | None]:
    total = 0.0
    for module in modules:
        rc, timed_out, elapsed, _ = _run_unittest_module(python, cwd, module, failfast=True, env=env, timeout=timeouts[module])
        total += elapsed
        if timed_out or rc != 0:
            return ("timeout" if timed_out else "killed"), total, module
    return "survived", total, None


def _create_isolation_env() -> tuple[Path, dict[str, str]]:
    iso_root = Path(tempfile.mkdtemp(prefix="mutation-sweep-"))
    local_appdata, temp_dir = iso_root / "LocalAppData", iso_root / "Temp"
    local_appdata.mkdir()
    temp_dir.mkdir()
    env = {**os.environ, "LOCALAPPDATA": str(local_appdata), "TEMP": str(temp_dir), "TMP": str(temp_dir), "TMPDIR": str(temp_dir)}
    return iso_root, env


def _rmtree_best_effort(path: Path) -> None:
    def _retry(func: object, target: str, _exc: object) -> None:
        with contextlib.suppress(OSError):
            os.chmod(target, stat.S_IWRITE)
            if callable(func):
                func(target)

    with contextlib.suppress(OSError):
        shutil.rmtree(path, **({"onexc": _retry} if sys.version_info >= (3, 12) else {"onerror": _retry}))  # type: ignore[arg-type]


def _verify_sha(orig: _Original, phase: str) -> str | None:
    try:
        got = _sha256(orig.full_path.read_bytes())
    except OSError as exc:
        return f"no se pudo leer {orig.rel_path} {phase}: {exc}"
    return None if got == orig.sha256 else f"{orig.rel_path} cambio {phase} ({orig.sha256[:12]} -> {got[:12]})"


def _mutant_record(c: _Candidate, status: str, seconds: float = 0.0, red_module: str | None = None) -> dict[str, object]:
    return {"path": c.path, "line": c.line, "text": c.text, "status": status, "context": c.context, "old": c.old, "new": c.new, "seconds": round(seconds, 3), "red_module": red_module}


def _print_header(base: str, head: str, modules: list[str], python: str, env_label: str, n_cand: int, n_skip: int) -> None:
    print(f"base: {base}\nhead: {head}\nmodulos: {', '.join(modules)}\npython: {python}\nentorno: {env_label}\ncandidatos: {n_cand} (saltadas por no ser únicas: {n_skip})", file=sys.stderr)


def _write_json_report(
    args: argparse.Namespace, base_sha: str, head_sha: str, tree: Path, python: str,
    base_rows: list[dict[str, object]], mutants: list[dict[str, object]], skipped: list[dict[str, object]],
) -> str | None:
    if args.json_path is None:
        return None
    counts = {
        "mutants": len(mutants),
        "killed": sum(1 for m in mutants if m["status"] in ("killed", "timeout")),
        "survived": sum(1 for m in mutants if m["status"] == "survived"),
        "timeout": sum(1 for m in mutants if m["status"] == "timeout"),
        "not_unique": len(skipped),
    }
    payload = {
        "base": base_sha, "head": head_sha, "tree": str(tree), "modules": list(args.modules),
        "python": python, "context": args.context, "isolated": not args.no_isolate,
        "base_run": base_rows, "mutants": mutants, "skipped_not_unique": skipped, "counts": counts,
    }
    try:
        out_path = Path(args.json_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        return str(exc)
    return None


def _parse_args(argv: list[str] | None) -> tuple[argparse.Namespace | None, int]:
    p = argparse.ArgumentParser(description="Mechanical line-deletion mutation sweep over lines added since --base.")
    p.add_argument("--base", required=True, help="git revision for diff base")
    p.add_argument("--tree", required=True, help="repo root holding tools/")
    p.add_argument("--module", action="append", required=True, dest="modules", help="unittest module (repeatable)")
    p.add_argument("--python", default=None, help="defaults to <tree>/tools/.venv-mcp/Scripts/python.exe")
    p.add_argument("--context", type=int, default=5, help="max context lines on each side (default: 5)")
    p.add_argument("--timeout", type=float, default=None, help="timeout in seconds per module run")
    p.add_argument("--include", action="append", default=[], dest="includes", help="POSIX glob to include")
    p.add_argument("--exclude", action="append", default=[], dest="excludes", help="POSIX glob to exclude")
    p.add_argument("--json", default=None, dest="json_path", help="write JSON report to PATH (UTF-8)")
    p.add_argument("--list", action="store_true", dest="list_only", help="list candidates without running tests")
    p.add_argument("--no-isolate", action="store_true", dest="no_isolate", help="inherit sweep environment")
    p.add_argument("--keep-env", action="store_true", dest="keep_env", help="keep private isolation dir")
    try:
        return p.parse_args(argv), 0
    except SystemExit as exc:
        return None, 0 if exc.code is None else (exc.code if isinstance(exc.code, int) else 2)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and (stream.encoding or "").lower().replace("-", "") != "utf8":
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(encoding="utf-8", errors="replace")

    args, parse_rc = _parse_args(argv)
    if args is None:
        return parse_rc
    if args.context < 0:
        return _err(f"--context debe ser >= 0, recibido: {args.context}")
    if args.timeout is not None and (not math.isfinite(args.timeout) or args.timeout <= 0):
        return _err(f"--timeout debe ser > 0, recibido: {args.timeout}")

    tree = Path(args.tree).resolve()
    cwd = tree / "tools"
    if not cwd.is_dir():
        return _err(f"--tree es la raiz que CONTIENE tools/, y no existe {cwd}", 2, f"recibido: {tree}")
    python = args.python or str(tree / "tools" / ".venv-mcp" / "Scripts" / "python.exe")
    if not Path(python).is_file():
        return _err(f"interprete no encontrado: {python}", 2, "una copia fiel no lleva .venv-mcp: pasa --python con el del arbol vivo")

    # Step 1: resolve --base and HEAD to commits.
    base_sha, err = _resolve_commit(tree, args.base)
    if base_sha is None:
        return _err(f"no se pudo resolver --base '{args.base}': {err}")
    head_sha, err = _resolve_commit(tree, "HEAD")
    if head_sha is None:
        return _err(f"no se pudo resolver HEAD: {err}")

    # Step 2: select files, verify clean against HEAD, record originals.
    diff_entries, err = _list_diff_entries(tree, base_sha)
    if diff_entries is None:
        return _err(f"fallo al listar el diff {base_sha[:12]}...HEAD: {err}")
    selected = _select_entries(diff_entries, args.includes, args.excludes)
    dirty_paths, err = _check_clean_working_tree(tree, [rel for _, rel in selected])
    if dirty_paths is None:
        return _err(f"fallo al comprobar el estado del arbol: {err}")
    if dirty_paths:
        return _err("hay ficheros seleccionados modificados respecto a HEAD; nada tocado:", 2, *(f"{rel}  (usa `git diff -- {rel}` para ver que difiere)" for rel in dirty_paths))

    originals: list[_Original] = []
    for old_path, rel in selected:
        full_path = tree / rel
        try:
            raw = full_path.read_bytes()
        except OSError as exc:
            return _err(f"no se pudo leer {rel}: {exc}")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            _err(f"aviso: {rel} no es UTF-8 valido; se omite")
            continue
        originals.append(_Original(rel, old_path, full_path, raw, _sha256(raw), text))

    # Step 3: compute candidates; handle --list.
    candidates, skipped, cerr = _collect_candidates(tree, base_sha, originals, args.context)
    if cerr is not None:
        return _err(cerr)
    isolated = not args.no_isolate

    if args.list_only:
        _print_header(base_sha, head_sha, args.modules, python, "aislado (--list)" if isolated else "no-isolate", len(candidates), len(skipped))
        if (jerr := _write_json_report(args, base_sha, head_sha, tree, python, [], [_mutant_record(c, "listed") for c in candidates], skipped)) is not None:
            return _err(f"no se pudo escribir --json {args.json_path}: {jerr}")
        for c in candidates:
            print(f"{c.path}:{c.line} | {c.text}")
        print(f"mutantes: {len(candidates)}\nsaltadas por no ser únicas: {len(skipped)}")
        return 0

    iso_root, run_env = _create_isolation_env() if isolated else (None, None)
    try:
        _print_header(base_sha, head_sha, args.modules, python, str(iso_root) if iso_root else "no-isolate", len(candidates), len(skipped))

        # Step 4: base run (every module in order, unmutated).
        base_rows, mutant_timeouts, base_ok = [], {}, True
        print("ejecucion base:", file=sys.stderr)
        try:
            for module in args.modules:
                rc, timed_out, elapsed, summary = _run_unittest_module(python, cwd, module, failfast=False, env=run_env, timeout=args.timeout)
                no_tests = _ran_no_tests(summary)
                base_rows.append({"module": module, "rc": rc, "seconds": round(elapsed, 3), "summary": summary})
                mutant_timeouts[module] = args.timeout if args.timeout is not None else max(60.0, 4.0 * elapsed)
                note = " [sin tests]" if (rc == 0 and not timed_out and no_tests) else ""
                print(f"  {module}: rc={rc} {elapsed:.2f}s {summary}{note}".rstrip(), file=sys.stderr, flush=True)
                if timed_out or rc != 0 or no_tests:
                    base_ok = False
        except KeyboardInterrupt:
            return _err("interrumpido durante la ejecucion base", 130)
        if not base_ok:
            return _err("la base YA esta roja (o algun modulo no ejecuto ningun test): no se muta nada")

        # Step 5: sweep each mutant in path and line order.
        orig_by_path = {o.rel_path: o for o in originals}
        results, total, interrupted, restore_err = [], len(candidates), False, None
        try:
            for idx, cand in enumerate(candidates, start=1):
                orig = orig_by_path[cand.path]
                if (verr := _verify_sha(orig, "antes de mutar")) is not None:
                    return _err(verr, 3)
                pos = orig.text.find(cand.old)
                if pos < 0:
                    return _err(f"no se encontro el patron del mutante en {orig.rel_path}", 3)
                mutated = (orig.text[:pos] + cand.new + orig.text[pos + len(cand.old) :]).encode("utf-8")
                mutant_sha = _sha256(mutated)
                wrote_mutant = False
                io_err: OSError | None = None
                try:
                    orig.full_path.write_bytes(mutated)
                    wrote_mutant = True
                    status, elapsed, red_module = _run_mutant(python, cwd, args.modules, run_env, mutant_timeouts)
                except OSError as exc:
                    io_err, status, elapsed, red_module = exc, "", 0.0, None
                finally:
                    # No return here: returning in finally swallows KeyboardInterrupt/exceptions.
                    should_restore = True
                    if wrote_mutant:
                        try:
                            current_sha = _sha256(orig.full_path.read_bytes())
                        except OSError as exc:
                            should_restore = False
                            restore_err = (
                                f"{cand.path}:{cand.line} | {cand.text}: "
                                f"no se pudo leer el fichero antes de restaurarlo ({exc}); "
                                f"no se toca: el original esta en HEAD"
                            )
                        else:
                            if current_sha != mutant_sha:
                                should_restore = False
                                restore_err = (
                                    f"{cand.path}:{cand.line} | {cand.text}: "
                                    f"el fichero cambio mientras el mutante estaba escrito; no se toca "
                                    f"({mutant_sha[:12]} -> {current_sha[:12]})"
                                )
                    if should_restore:
                        try:
                            orig.full_path.write_bytes(orig.raw)
                            restored_sha = _sha256(orig.full_path.read_bytes())
                        except OSError:
                            restored_sha = "<error-io>"
                        if restored_sha != orig.sha256:
                            restore_err = f"NO SE RESTAURO {orig.rel_path}: {orig.sha256[:12]} -> {restored_sha[:12]}"
                if restore_err is not None:
                    return _err(restore_err, 3)
                if io_err is not None:
                    return _err(f"error de E/S al mutar {orig.rel_path}: {io_err}", 3)
                results.append(_mutant_record(cand, status, elapsed, red_module))
                print(f"[{idx}/{total}] {cand.path}:{cand.line} {status} {elapsed:.1f}s", file=sys.stderr, flush=True)
        except KeyboardInterrupt:
            interrupted = True
            if restore_err is not None:
                return _err(restore_err, 3)

        # Step 6: verify every selected file matches its original sha256.
        for orig in originals:
            if (verr := _verify_sha(orig, "tras el barrido")) is not None:
                return _err(verr, 3)
        if (jerr := _write_json_report(args, base_sha, head_sha, tree, python, base_rows, results, skipped)) is not None:
            return _err(f"no se pudo escribir --json {args.json_path}: {jerr}")

        survivors = [m for m in results if m["status"] == "survived"]
        timeouts = sum(1 for m in results if m["status"] == "timeout")
        killed = len(results) - len(survivors)
        print(f"supervivientes: {len(survivors)}")
        for m in survivors:
            print(f"{m['path']}:{m['line']} | {m['text']}")
        print(f"mueren: {killed} de {len(results)} ({timeouts} por timeout)\nsaltadas por no ser únicas: {len(skipped)}")
        return _err(f"interrumpido tras {len(results)} de {total} mutantes", 130) if interrupted else 0
    finally:
        if iso_root is not None and args.keep_env:
            print(f"entorno conservado: {iso_root}", file=sys.stderr)
        elif iso_root is not None:
            _rmtree_best_effort(iso_root)


if __name__ == "__main__":
    raise SystemExit(main())
