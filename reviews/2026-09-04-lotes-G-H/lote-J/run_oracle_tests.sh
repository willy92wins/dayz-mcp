#!/usr/bin/env bash
# Oracle of lote J (P-J1 fence exemption, P-J2 adopt-on-grant): production wiring over HTTP; see oracle_tests.py.
set -u
cd "$(dirname "$0")/../tools" || exit 2
PY="C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe"
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 "$PY" ../gate/oracle_tests.py
