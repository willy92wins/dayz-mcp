#!/usr/bin/env bash
# Oracle of lote I round 2: the tests are the product. Mutants run on temp copies, never here.
set -u
cd "$(dirname "$0")/../tools" || exit 2
PY="C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe"
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 "$PY" ../gate/oracle_tests.py
