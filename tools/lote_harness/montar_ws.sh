#!/usr/bin/env bash
# Build a FAITHFUL workspace copy of DayZ_MCP_dev for a lote whose gate runs the full suite.
# Usage: montar_ws.sh <repo-dir> <ws-dir> [max-MiB (default 600)]
#
# Faithful means: everything `unittest discover` and the doc/skill/launcher tests read — the
# repo root files, tools/ (native-launchers/ and spike0/ INCLUDED: the launcher tests import
# them), addon/, docs — and NOT the run artefacts. Measured 2026-09-04 while mounting lote I:
#   tracked files only         -> 38 reds (untracked helper modules and data files missing)
#   tools/ alone               -> 43 reds (doc tests resolve README/docs from the repo root)
#   whole repo minus 2 dirs    -> 43 reds and 16 GB copied (_s0, _fase*, _poc, _server, reviews)
# The exclusion list below is the declared boundary; a new heavy artefact dir shows up as a
# size overflow, which is the failure mode you want (loud), not a silent 16 GB copy.
set -u
REPO="${1:?repo dir}"; WS="${2:?ws dir}"; MAX_MIB="${3:-600}"
EXCLUDES=(.git .venv-mcp __pycache__ _s0 _fase1 _fase2 _fase3 _poc _server _step0 reviews)
args=(); for e in "${EXCLUDES[@]}"; do args+=(--exclude="$e"); done
est=$(cd "$REPO" && du -sm "${args[@]}" . | cut -f1)
if [ "$est" -gt "$MAX_MIB" ]; then
  echo "montar_ws: el arbol fiel mide ${est} MiB > tope ${MAX_MIB} MiB; anade el artefacto nuevo a EXCLUDES o sube el tope a sabiendas" >&2
  (cd "$REPO" && du -sm "${args[@]}" ./*/ 2>/dev/null | sort -rn | head -5 >&2)
  exit 2
fi
mkdir -p "$WS"
(cd "$REPO" && tar "${args[@]}" -cf - .) | (cd "$WS" && tar -xf -)
echo "montar_ws: $WS <- $REPO  ($(du -sm "$WS" | cut -f1) MiB, $(find "$WS" -type f | wc -l) ficheros, tests=$(ls "$WS"/tools/tests/test_*.py 2>/dev/null | wc -l))"
