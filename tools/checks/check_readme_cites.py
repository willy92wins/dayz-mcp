"""Check that every path:line citation in README.md resolves in this tree.

Vanilla DayZ scripts (`file.c:N` with no directory) are not in the repo; they
are listed separately and do not fail the check. In-tree paths must exist and
must contain the cited line (or the end of a `start-end` range).
"""
from __future__ import annotations

import re
import sys
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
README = REPO / "README.md"

# `dir/file.ext:12`, `file.ext:12-15`, or a dotfile (`.gitignore:15-16`).
# A suffix or a leading-dot name is required so `127.0.0.1` does not match.
CITE_RE = re.compile(
    r"(?P<path>(?:[\w.-]+/)*(?:[\w.-]+\.[A-Za-z0-9]+|\.[\w-]+)):(?P<a>\d+)(?:-(?P<b>\d+))?"
)

# Bare `something.c:N` — Enforce vanilla, not shipped in this repo.
VANILLA_C = re.compile(r"^[A-Za-z0-9_]+\.c$")

# Unpublished trees the README must not point a clone at, with or without :N.
UNPUBLISHED = (
    "HANDOFF.md",
    "poc-verdict.json",
    "reviews/",
    "decisions/",
)


def classify(path: str) -> str:
    if VANILLA_C.match(path):
        return "vanilla"
    return "in-tree"


def _tracked_lines(repo: Path, rel: str) -> int | None:
    """Line count from the index, or None when the path is not tracked.

    Used only when the file is absent from disk: a sparse checkout excludes
    tracked paths from the worktree, and a clone still receives them.
    """
    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{rel}"],
            cwd=repo,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if blob.returncode != 0:
        return None
    return len(blob.stdout.decode("utf-8", errors="replace").splitlines())


def check(readme: Path = README, repo: Path = REPO) -> dict:
    text = readme.read_text(encoding="utf-8")
    vanilla: list[str] = []
    ok: list[str] = []
    missing: list[str] = []
    short: list[str] = []
    seen: set[str] = set()

    for match in CITE_RE.finditer(text):
        rel = match.group("path")
        start = int(match.group("a"))
        end = int(match.group("b") or match.group("a"))
        token = match.group(0)
        if token in seen:
            continue
        seen.add(token)
        if start <= 0 or end < start:
            short.append(f"{token}  invalid line range")
            continue
        if classify(rel) == "vanilla":
            vanilla.append(token)
            continue
        target = repo / rel
        if target.is_file():
            n = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
        else:
            # Absent here is not absent for a clone: sparse checkout keeps
            # tracked paths (addon/) out of this worktree only.
            n = _tracked_lines(repo, rel)
            if n is None:
                missing.append(f"{token}  file not in tree")
                continue
        if end > n:
            short.append(f"{token}  file has {n} lines, cited {end}")
            continue
        ok.append(token)

    unpublished = []
    for needle in UNPUBLISHED:
        if needle in text:
            unpublished.append(needle)

    return {
        "ok": ok,
        "vanilla": vanilla,
        "missing": missing,
        "short": short,
        "unpublished": unpublished,
    }


def format_report(result: dict) -> str:
    lines = []
    broken = result["missing"] + result["short"]
    lines.append(f"in-tree ok:          {len(result['ok'])}")
    lines.append(f"vanilla (exception): {len(result['vanilla'])}")
    lines.append(f"missing file:        {len(result['missing'])}")
    lines.append(f"line out of range:   {len(result['short'])}")
    lines.append(f"unpublished mention: {len(result['unpublished'])}")
    if result["vanilla"]:
        lines.append("")
        lines.append("VANILLA (engine scripts, not in this repo):")
        for token in result["vanilla"]:
            lines.append(f"  {token}")
    if result["ok"]:
        lines.append("")
        lines.append("IN-TREE OK:")
        for token in result["ok"]:
            lines.append(f"  {token}")
    if broken:
        lines.append("")
        lines.append("FAIL:")
        for token in broken:
            lines.append(f"  {token}")
    if result["unpublished"]:
        lines.append("")
        lines.append("UNPUBLISHED PATH MENTIONED:")
        for token in result["unpublished"]:
            lines.append(f"  {token}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    result = check()
    sys.stdout.write(format_report(result))
    if result["missing"] or result["short"] or result["unpublished"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
