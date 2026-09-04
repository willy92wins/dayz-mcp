#!/usr/bin/env bash
# Implementer lane (Grok CLI). Usage: runner.sh <lote-dir> <runs-dir-name> [max-turns]
#
# Layout expected under <lote-dir>:
#   ws/                 workspace the worker edits (its gate/ is sealed, read-only by contract)
#   <runs-dir>/brief.txt  the brief; STARTED, EXIT (RC=), out.json, err.txt are written here
#
# EXIT is written when grok.exe returns, whatever the RC (LL-406): the coupled watcher keys on
# it, so a crash still wakes the orchestrator instead of leaving a silent run.
set -u
LOTE="${1:?lote dir}"; RUNS="${2:?runs dir name}"; TURNS="${3:-100}"
LOTE="$(cd "$LOTE" && pwd)"
RUN="$LOTE/$RUNS"; WS="$LOTE/ws"
[ -f "$RUN/brief.txt" ] || { echo "no brief at $RUN/brief.txt" >&2; exit 2; }
[ -d "$WS" ] || { echo "no workspace at $WS" >&2; exit 2; }
rm -f "$RUN/EXIT"
date +%s > "$RUN/STARTED"
MSYS_NO_PATHCONV=1 "$USERPROFILE/.grok/bin/grok.exe" \
  --prompt-file "$(cygpath -w "$RUN/brief.txt")" \
  --cwd "$(cygpath -w "$WS")" \
  --tools "read_file,grep,list_dir,search_replace,run_terminal_cmd" \
  --no-subagents --no-alt-screen --no-leader --always-approve \
  --max-turns "$TURNS" --effort high --output-format json \
  > "$RUN/out.json" 2> "$RUN/err.txt"
RC=$?
echo "RC=$RC" > "$RUN/EXIT"
exit $RC
