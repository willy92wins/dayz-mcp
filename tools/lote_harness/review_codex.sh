#!/usr/bin/env bash
# Reviewer lane (Codex CLI, fresh session). Usage: review_codex.sh <lote-dir> <review-dir-name>
#
# Reads <review-dir>/BRIEF.txt, gives Codex the workspace read-only via --add-dir and lets it
# write ONLY inside <review-dir> (REVIEW-CODEX.md). EXIT with RC= on return (LL-406).
# --skip-git-repo-check is mandatory: the scratchpad is not a git repo and codex dies in 1 s
# with RC=1 without it.
set -u
LOTE="${1:?lote dir}"; REV="${2:?review dir name}"
LOTE="$(cd "$LOTE" && pwd)"
R="$LOTE/$REV"
[ -f "$R/BRIEF.txt" ] || { echo "no brief at $R/BRIEF.txt" >&2; exit 2; }
rm -f "$R/EXIT"; date +%s > "$R/STARTED"
MSYS_NO_PATHCONV=1 codex exec \
  -C "$(cygpath -w "$R")" \
  -s workspace-write \
  --add-dir "$(cygpath -w "$LOTE/ws")" \
  --skip-git-repo-check \
  -c model_reasoning_effort=high \
  "$(cat "$R/BRIEF.txt")" \
  > "$R/out-codex.log" 2>&1 < /dev/null
RC=$?
echo "RC=$RC" > "$R/EXIT"
exit $RC
