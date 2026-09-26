#!/usr/bin/env bash
set -uo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 g1 attempt-1" >&2
  exit 64
fi

GROUP="${1,,}"
ATTEMPT="$2"
case "$GROUP" in
  g1|g2|g3|g4|g5|g6|g7) ;;
  *) echo "invalid group: $GROUP" >&2; exit 64 ;;
esac
if [[ ! "$ATTEMPT" =~ ^attempt-[1-9][0-9]*$ ]]; then
  echo "invalid attempt: $ATTEMPT" >&2
  exit 64
fi

export PATH="$HOME/.local/node/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1
WS="/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev"
BASE="$WS/reviews/2026-08-31-inbox-plan-v6"
COMMON="$BASE/requests/common.md"
GROUP_PROMPT="$BASE/requests/group-$GROUP.md"
CALIBRATION="$BASE/requests/calibration.md"
PARSER="$BASE/parse_prime_plan_run.py"
DEST="$BASE/runs/$GROUP/$ATTEMPT"

if [[ -e "$DEST" ]]; then
  echo "destination already exists: $DEST" >&2
  exit 65
fi

TMP_ROOT="$(mktemp -d "/tmp/dayz-mcp-opus-plan-v6-${GROUP}-${ATTEMPT}.XXXXXX")"
SESSION_DIR="$TMP_ROOT/session"
RAW_REVIEW="$TMP_ROOT/review-turn.raw"
RAW_CALIBRATION="$TMP_ROOT/calibration-turn.raw"
mkdir -p "$SESSION_DIR"

FIRST_PROMPT="$(printf '%s\n\n%s\n' "$(<"$COMMON")" "$(<"$GROUP_PROMPT")")"
first_cmd=(
  prime-agent -p "$FIRST_PROMPT"
  --cwd "$WS"
  --provider anthropic
  --model claude-opus-5
  --thinking max
  --mode json
  -nc -ns -ne -np --no-themes -t ipython
  --session-dir "$SESSION_DIR"
)
first_quoted="$(printf '%q ' "${first_cmd[@]}")"
script -qefc "$first_quoted" "$RAW_REVIEW" >/dev/null
FIRST_RC=$?

mapfile -t session_files < <(find "$SESSION_DIR" -maxdepth 1 -type f -name '*.jsonl')
if (( ${#session_files[@]} != 1 )); then
  echo "expected one session file, found ${#session_files[@]}" >&2
  exit 96
fi
SESSION_FILE="${session_files[0]}"

SECOND_PROMPT="$(<"$CALIBRATION")"
second_cmd=(
  prime-agent -r "$SESSION_FILE" -p "$SECOND_PROMPT"
  --cwd "$WS"
  --provider anthropic
  --model claude-opus-5
  --thinking max
  --mode json
  -nc -ns -ne -np --no-themes -t ipython
  --session-dir "$SESSION_DIR"
)
second_quoted="$(printf '%q ' "${second_cmd[@]}")"
script -qefc "$second_quoted" "$RAW_CALIBRATION" >/dev/null
SECOND_RC=$?

python3 "$PARSER" \
  --group "$GROUP" \
  --attempt "$ATTEMPT" \
  --group-prompt "$GROUP_PROMPT" \
  --raw-review "$RAW_REVIEW" \
  --raw-calibration "$RAW_CALIBRATION" \
  --session-file "$SESSION_FILE" \
  --dest "$DEST"
PARSE_RC=$?

printf 'group=%s attempt=%s first_rc=%s second_rc=%s parse_rc=%s dest=%s\n' \
  "$GROUP" "$ATTEMPT" "$FIRST_RC" "$SECOND_RC" "$PARSE_RC" "$DEST"
if (( FIRST_RC != 0 || SECOND_RC != 0 || PARSE_RC != 0 )); then
  exit 2
fi
