#!/usr/bin/env bash
# Create a git worktree of this repository with its own tools/.venv-mcp, installed the
# way tools/install-mcp.ps1 and CI install it, then prove that the venv runs THIS
# worktree's code.
#
# Usage:
#   tools/dev/setup_worktree.sh <worktree-dir> <new-branch> [base-ref]
#       git worktree add -b <new-branch> <worktree-dir> <base-ref> (default origin/main),
#       then the venv, then the check below. Refuses with exit 3 when <worktree-dir>
#       already exists, and before touching git when the base ref's installer has no
#       exact pip pin. A failure after the worktree exists leaves it in place: fix the
#       cause, then `git worktree remove <worktree-dir>` and run again.
#   tools/dev/setup_worktree.sh --check <worktree-dir>
#       Only the check, for a worktree (or copy) that already has its venv.
#
# The venv is <worktree-dir>/tools/.venv-mcp: the daemon only starts under that
# interpreter, and the suite runs from it. PYTHON names the interpreter that creates
# the venv (default: python). pip is the exact $PipRequirement of the base ref's own
# tools/install-mcp.ps1 (dc90: never --upgrade), then requirements-mcp.txt, then the
# package in editable mode, as the CI workflow does.
#
# The check (fb-20260818-220336-2eb5). setuptools' editable finder sits at the END of
# sys.meta_path, behind PathFinder, and maps each package to an absolute path. From
# inside tools/, `python -c "import dayz_mcp"` finds the local folder first and says
# nothing about that map, so it stays green while the map points at another tree; a
# test that starts a subprocess from another directory falls through to the map and
# runs that other tree. So the check runs the venv's python isolated (-I: no
# PYTHONPATH, no current directory on sys.path) from an empty temporary directory,
# hands it <worktree-dir>/tools as named, and requires:
#   - that neither <worktree-dir>, its tools/ nor tools/.venv-mcp is a junction or a
#     symlink, and that the interpreter's venv is that tools/.venv-mcp. Review R1 F4:
#     a .venv-mcp junctioned to another checkout's venv sealed the other checkout;
#   - that every module tools/pyproject.toml declares or the editable finder maps,
#     and dayz_mcp itself, resolves inside that same tools/.
# It prints SEALED <tools> and exits 0, or one LEAK line per problem and exits 1.
#
# The functions can be sourced without running anything (tools/tests use that).

set -euo pipefail

USAGE="usage: setup_worktree.sh <worktree-dir> <new-branch> [base-ref]
       setup_worktree.sh --check <worktree-dir>"

# The venv interpreter under <tools-dir>/.venv-mcp (Windows or POSIX layout).
venv_python() {
  local venv="$1/.venv-mcp"
  if [ -x "$venv/Scripts/python.exe" ]; then
    printf '%s\n' "$venv/Scripts/python.exe"
  elif [ -x "$venv/bin/python" ]; then
    printf '%s\n' "$venv/bin/python"
  else
    return 1
  fi
}

# stdin: an install-mcp.ps1. stdout: its one exact pip pin, e.g. pip==26.2.1.
pip_requirement() {
  local pins
  pins="$(tr -d '\r' | sed -n 's/^\$PipRequirement[[:space:]]*=[[:space:]]*"\(pip==[0-9][0-9A-Za-z.+!-]*\)"[[:space:]]*$/\1/p')"
  if [ -z "$pins" ] || [ "$(printf '%s\n' "$pins" | wc -l)" -ne 1 ]; then
    echo 'setup_worktree: install-mcp.ps1 must assign exactly one $PipRequirement = "pip==X.Y.Z"' >&2
    return 1
  fi
  printf '%s\n' "$pins"
}

check_worktree() {
  local tools tools_arg python neutral status=0
  if ! tools="$(cd "$1/tools" 2>/dev/null && pwd)"; then
    echo "setup_worktree: no tools/ directory in $1" >&2
    return 1
  fi
  # The path as named. `pwd -P` and Git Bash's `pwd -W` resolve a junction, which
  # would hide the very link the check looks for; cygpath only rewrites the name
  # into the form the Windows interpreter reads.
  if command -v cygpath > /dev/null 2>&1; then
    tools_arg="$(cygpath -m "$tools")"
  else
    tools_arg="$tools"
  fi
  if ! python="$(venv_python "$tools")"; then
    echo "setup_worktree: no venv at $tools/.venv-mcp" >&2
    return 1
  fi
  neutral="$(mktemp -d)"
  (cd "$neutral" && "$python" -I - "$tools_arg" <<'PY') || status=$?
import importlib.util
import os
import stat
import sys
from pathlib import Path

REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: a junction or a symlink on Windows


def real(path):
    return os.path.normcase(os.path.realpath(path))


def inside(path, root):
    path, root = real(path), real(root).rstrip("\\/")
    return path == root or path.startswith(root + os.sep)


def is_link(path):
    try:
        status = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(status.st_mode) or bool(getattr(status, "st_file_attributes", 0) & REPARSE_POINT)


tools = Path(sys.argv[1])
venv = tools / ".venv-mcp"
leaks = []
for part in (tools.parent, tools, venv):
    if is_link(part):
        leaks.append("%s is a junction or a symlink, so its code may be another tree's" % part)
if sys.prefix == sys.base_prefix:
    leaks.append("%s is not a venv interpreter" % sys.executable)
elif real(sys.prefix) != real(venv):
    leaks.append("the interpreter's venv is %s, not %s" % (sys.prefix, venv))
own = {"dayz_mcp"}
pyproject = tools / "pyproject.toml"
if pyproject.is_file():
    try:
        import tomllib

        declared = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("tool", {}).get("setuptools", {})
        own |= set(declared.get("packages", [])) | set(declared.get("py-modules", []))
    except Exception as error:  # an unreadable declaration is a failed check, not a crash
        leaks.append("cannot read the modules %s declares: %r" % (pyproject, error))
mapped = {}
for name, module in sorted(sys.modules.items()):
    if name.startswith("__editable__") and name.endswith("_finder"):
        mapping = getattr(module, "MAPPING", None)
        if isinstance(mapping, dict):
            mapped.update(mapping)
for name, target in sorted(mapped.items()):
    if not inside(target, tools):
        leaks.append("the editable finder maps %s to %s, outside %s" % (name, target, tools))
for name in sorted(own | set(mapped)):
    try:
        spec = importlib.util.find_spec(name)
    except Exception as error:  # a broken finder is a failed check, not a crash
        spec, origin = None, "an error: %r" % (error,)
    else:
        origin = (spec.origin if spec is not None else None) or "nothing"
    print("%s from %s" % (name, origin))
    if spec is None or not spec.origin or not inside(spec.origin, tools):
        leaks.append("%s resolves to %s, outside %s" % (name, origin, tools))
for leak in leaks:
    print("LEAK: " + leak)
if leaks:
    sys.exit(1)
print("SEALED %s" % tools)
PY
  rmdir "$neutral" 2>/dev/null || true
  return "$status"
}

create_worktree() {
  local wt="$1" branch="$2" base="${3:-origin/main}" repo pip_req tools python
  # git -C resolves a relative path against the repository; the caller means their own cwd.
  case "$wt" in
    /*|[A-Za-z]:[\\/]*) ;;
    *) wt="$PWD/$wt" ;;
  esac
  if [ -e "$wt" ]; then
    echo "EXISTS $wt" >&2
    exit 3
  fi
  repo="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  git -C "$repo" rev-parse --verify --quiet "$base^{commit}" >/dev/null ||
    { echo "setup_worktree: base ref $base is not a commit in $repo" >&2; exit 2; }
  # The pin comes from the tree being checked out, before anything is created.
  pip_req="$(git -C "$repo" show "$base:tools/install-mcp.ps1" | pip_requirement)"
  git -C "$repo" worktree add -b "$branch" "$wt" "$base"
  tools="$(cd "$wt/tools" && pwd)"
  (cd "$tools" && "${PYTHON:-python}" -m venv .venv-mcp)
  python="$(venv_python "$tools")"
  "$python" -m pip install -q "$pip_req"
  "$python" -m pip install -q -r "$tools/requirements-mcp.txt"
  (cd "$tools" && "$python" -m pip install -q -e .)
  check_worktree "$wt"
  echo "READY $wt $(git -C "$wt" rev-parse --short HEAD) $branch"
}

main() {
  case "${1:-}" in
    -h|--help)
      printf '%s\n' "$USAGE"
      ;;
    --check)
      if [ "$#" -ne 2 ]; then printf '%s\n' "$USAGE" >&2; exit 2; fi
      check_worktree "$2"
      ;;
    ""|-*)
      printf '%s\n' "$USAGE" >&2
      exit 2
      ;;
    *)
      if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then printf '%s\n' "$USAGE" >&2; exit 2; fi
      create_worktree "$@"
      ;;
  esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
