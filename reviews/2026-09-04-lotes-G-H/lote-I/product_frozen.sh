#!/usr/bin/env bash
# The product is FROZEN in this lote: every dayz_mcp/*.py must hash exactly as sealed.
# Sealed by the orchestrator from the repo working tree (HEAD + lotes G/H) before delegating.
set -u
cd "$(dirname "$0")/../tools/dayz_mcp" || exit 2
if sha256sum -c --quiet "../../gate/product_frozen.sha256"; then
  n=$(find . -name "*.py" -type f | wc -l); s=$(wc -l < "../../gate/product_frozen.sha256")
  if [ "$n" -eq "$s" ]; then echo "PRODUCTO-CONGELADO OK ($n ficheros)"; exit 0; fi
  echo "PRODUCTO-CONGELADO ROTO: $n ficheros .py, $s sellados (fichero nuevo o borrado)"; exit 1
fi
echo "PRODUCTO-CONGELADO ROTO"; exit 1
