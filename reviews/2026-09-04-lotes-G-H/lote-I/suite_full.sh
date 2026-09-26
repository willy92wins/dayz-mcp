#!/usr/bin/env bash
# Full unittest discovery over tools/tests. Green iff the set of red test ids (FAIL/ERROR lines,
# subtests collapsed) is EXACTLY gate/allowed_red.txt: a vanished known failure is as suspicious
# as a new one. The count of tests run is printed so a collapse in coverage is visible.
set -u
cd "$(dirname "$0")/../tools" || exit 2
PY="C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe"
LOG="$(mktemp)"
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t . > "$LOG" 2>&1
grep -E "^Ran [0-9]+ tests" "$LOG" || { echo "SUITE-COMPLETA ROJA (sin linea Ran: discover no termino)"; tail -5 "$LOG"; rm -f "$LOG"; exit 1; }
grep -E "^(FAIL|ERROR): " "$LOG" | sed -E 's/ \(.*//' | sort -u > "$LOG.red"
sort -u "../gate/allowed_red.txt" > "$LOG.allowed"
if diff -q "$LOG.red" "$LOG.allowed" > /dev/null; then
  echo "rojos = permitidos ($(wc -l < "$LOG.red"))"; echo "SUITE-COMPLETA OK"; rc=0
else
  echo "rojos NO permitidos:"; comm -23 "$LOG.red" "$LOG.allowed" | sed 's/^/  + /'
  echo "permitidos que ya no fallan:"; comm -13 "$LOG.red" "$LOG.allowed" | sed 's/^/  - /'
  echo "SUITE-COMPLETA ROJA"; rc=1
fi
rm -f "$LOG" "$LOG.red" "$LOG.allowed"; exit $rc
