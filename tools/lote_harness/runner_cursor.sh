#!/usr/bin/env bash
# Implementer lane via Cursor CLI (flat-rate relief when grok.exe is out of balance: 402).
# Usage: runner_cursor.sh <lote-dir> <runs-dir-name> [model]   (default cursor-grok-4.6-high)
#
# The three measured traps of this route (delegar/references/routes/cursor-cli.md §Trampas):
#   7. a multi-line -p dies in the .cmd wrapper with exit 0 -> the brief is copied INTO the
#      workspace as BRIEF.txt and the prompt is ONE line telling the agent to read it;
#   8. the trust gate looks at the process cwd, not --workspace -> cd into the workspace;
#   9. without --force every shell call is rejected -> the agent could not run its gate.
# `agent` on PATH is grok.exe: always the absolute cursor-agent.cmd. Identity is verified at
# receipt from the stream-json `init` event (`"model"`), never assumed.
set -u
LOTE="${1:?lote dir}"; RUNS="${2:?runs dir name}"; MODEL="${3:-cursor-grok-4.6-high}"
LOTE="$(cd "$LOTE" && pwd)"
RUN="$LOTE/$RUNS"; WS="$LOTE/ws"
CA="$LOCALAPPDATA/cursor-agent/cursor-agent.cmd"
[ -f "$RUN/brief.txt" ] || { echo "no brief at $RUN/brief.txt" >&2; exit 2; }
[ -d "$WS" ] || { echo "no workspace at $WS" >&2; exit 2; }
[ -f "$CA" ] || { echo "no cursor-agent at $CA" >&2; exit 2; }
cp "$RUN/brief.txt" "$WS/BRIEF.txt"
rm -f "$RUN/EXIT"
date +%s > "$RUN/STARTED"
echo "$MODEL" > "$RUN/MODEL"
cd "$WS" || exit 2
# cursor-agent 2026.09.02 imports Claude Code hooks from <homedir>/.claude/settings.json
# (hardcoded: claudeUserConfigPath in its bundle; CLAUDE_CONFIG_DIR is ignored) and runs the
# PowerShell wrapper through bash, so EVERY shell call of the worker dies with
# "Hook blocked: eval: syntax error near `&'". Measured 2026-09-04: the round-7 worker could
# not run a single test. The only dial is homedir(): a sandbox HOME with no .claude/ and a
# junction .cursor -> the real one (login, config and models stay shared). 0 blocks measured.
SANDBOX_HOME="$LOTE/../cursor-home"
mkdir -p "$SANDBOX_HOME"
if [ ! -d "$SANDBOX_HOME/.cursor" ]; then
  MSYS_NO_PATHCONV=1 cmd /c "mklink /J $(cygpath -w "$SANDBOX_HOME")\\.cursor $(cygpath -w "$USERPROFILE")\\.cursor" >/dev/null \
    || { echo "could not create the .cursor junction under $SANDBOX_HOME" >&2; exit 2; }
fi
[ -f "$SANDBOX_HOME/.cursor/cli-config.json" ] || { echo "sandbox .cursor junction not usable" >&2; exit 2; }
[ -e "$SANDBOX_HOME/.claude" ] && { echo "sandbox HOME must not contain .claude" >&2; exit 2; }
PROMPT="Lee el fichero BRIEF.txt que esta en la raiz de este workspace y ejecutalo entero, hasta el final, sin preguntar nada; al terminar escribe STATE.md en la raiz del workspace exactamente como indica el brief."
# With HOME redirected, powershell.exe drops its ModuleAnalysisCache under the cwd (the
# workspace) and pollutes the write-set: pin it to the sandbox instead. Measured round 8.
mkdir -p "$SANDBOX_HOME/ps-cache"
PSModuleAnalysisCachePath="$(cygpath -w "$SANDBOX_HOME/ps-cache")\\ModuleAnalysisCache" \
USERPROFILE="$(cygpath -w "$SANDBOX_HOME")" HOME="$SANDBOX_HOME" MSYS_NO_PATHCONV=1 "$CA" \
  -p "$PROMPT" \
  --trust --force \
  --workspace "$(cygpath -w "$WS")" \
  --output-format stream-json \
  --model "$MODEL" \
  > "$RUN/out.json" 2> "$RUN/err.txt"
RC=$?
echo "RC=$RC" > "$RUN/EXIT"
exit $RC
