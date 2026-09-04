#!/usr/bin/env bash
# Coupled watcher, one-shot: exits on the first state transition so the harness re-invokes the
# orchestrator. Usage: watch.sh <lote-dir> <runs-dir-name> [stall-seconds]
#
# Transitions: EXIT written (worker returned) · DISPUTAS with real content in ws/STATE.md
# (orchestrator decision) · STALLED (no change in out.json/STATE.md for <stall-seconds>,
# default 2400): look at the tree before relaunching.
#
# "Ninguna." under DISPUTAS is a negation, not a dispute: explicit negations are discarded,
# after a measured false positive that woke the orchestrator for nothing.
set -u
LOTE="${1:?lote dir}"; RUNS="${2:?runs dir name}"; STALL="${3:-2400}"
LOTE="$(cd "$LOTE" && pwd)"
STATE="$LOTE/ws/STATE.md"; OUT="$LOTE/$RUNS/out.json"; EXIT_F="$LOTE/$RUNS/EXIT"

disputas_reales() {
  [ -f "$STATE" ] || { echo 0; return; }
  awk '
    /^## DISPUTAS/ {f=1; next}
    /^## / {f=0}
    f && NF {
      linea = tolower($0)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", linea)
      # A line that STARTS with a negation is a negation whatever follows it: "Ninguna sobre
      # el criterio. N17 es alcanzable..." woke the orchestrator on the second real run.
      if (linea ~ /^[-*[:space:]]*(ninguna|ninguno|none|n\/a|nada|sin disputas)([^a-z]|$)/) next
      n++
    }
    END {print n+0}
  ' "$STATE" 2>/dev/null
}

huella() {
  echo "$(stat -c %s "$OUT" 2>/dev/null || echo 0):$(stat -c %Y "$STATE" 2>/dev/null || echo 0)"
}

ULTIMA="$(huella)"; QUIETO=0
while true; do
  if [ -f "$EXIT_F" ]; then
    echo "WORKER_EXIT $(cat "$EXIT_F")"
    echo "out.json: $(wc -c < "$OUT" 2>/dev/null || echo 0) B"
    exit 0
  fi
  N=$(disputas_reales)
  if [ "${N:-0}" -gt 0 ]; then
    echo "DISPUTAS con contenido real: $N lineas -- decision del orquestador"
    awk '/^## DISPUTAS/{f=1;next} /^## /{f=0} f && NF' "$STATE" | head -25
    exit 0
  fi
  AHORA="$(huella)"
  if [ "$AHORA" = "$ULTIMA" ]; then QUIETO=$((QUIETO+45)); else QUIETO=0; ULTIMA="$AHORA"; fi
  if [ "$QUIETO" -ge "$STALL" ]; then
    echo "STALLED: ${STALL}s sin cambios en out.json ni STATE.md -- mirar el arbol antes de relanzar"
    exit 0
  fi
  sleep 45
done
