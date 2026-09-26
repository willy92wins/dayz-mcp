# Prompt X.5 Fase 4 — implementación de los fixes R21 (Codex CLI)

> Generado 2026-06-11 tras adjudicación del usuario (X.5 completa). Patrón:
> implementation-handoff scope-bounded. Receptor: Claude (re-run suite + verificación
> host-direct + smoke MCP). Pegar tal cual en Codex CLI.

===== PROMPT INICIO =====

Tarea: implementar la sesión correctiva X.5 de Fase 4 de DayZ-MCP — los 5 fixes adjudicados
de la R21 consolidada (F4-X5-1..5). Esta sesión cubre **únicamente esos 5 fixes y sus
tests**. Es Python + PowerShell de install + tests: **CERO cambios en Enforce (.c), cero
rebuild del PBO**. Los 8 hallazgos P3 de la R21 quedan en backlog: no los implementes ni
siquiera parcialmente.

## Carga inicial obligatoria

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-11-r21-fase4-consolidated.md
   (la spec de esta X.5: matriz CONS-* + §Propuesta X.5 — vinculante).
2. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\reviews\2026-06-11-r21-fase4-codex.md
   (tu propia R21: detalle + resoluciones sugeridas de R21-F4-001/002/003).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-11-r21-fase4-claude.md
   (mitad Claude: detalle de CONS-P2-1 /enqueue bypass, CONS-P2-2 year, CONS-P2-3 install cwd,
   y el matiz main_fn de CONS-P1-2).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-10-fase4-mcp.md
   (contrato original: §5.2 handshake, §5.3 exec gating, §6 install — los fixes deben dejar el
   código cumpliendo ESTE contrato).

NO releas el research F4 ni los gates in-game. Código objetivo (lo abrirás al editar):
`DayZ_MCP_dev\tools\dayz_mcp\loopback.py`, `…\dayz_mcp\server.py`, `…\tools\install-mcp.ps1`,
`…\tools\tests\`.

## Alcance acotado — los 5 fixes

### F4-X5-1 (CONS-P1-1) — gate de versión en la ENTREGA + versión no pegajosa
Archivos: `loopback.py` + `server.py`.
- `ServerState.record_poll` registra la versión SIEMPRE: `self._poll_versions[peer] = version`
  incondicional (None sobreescribe — un poll sin `ver=` ES información: el peer es legacy ahora).
- El loopback acepta un **validador opcional inyectado** (callable `version -> state_str`,
  construido por `Runtime` como closure sobre `_version_state_for` — stdlib-only se mantiene:
  es solo un callable). Sin validador → comportamiento EXACTAMENTE igual al actual (shim,
  harness, tests existentes intactos).
- Con validador: `record_poll` evalúa el estado con la versión RECIÉN registrada de ESE poll;
  si el estado ∈ {`legacy_blocked`, `version_mismatch`} → **NO drena la cola**, devuelve
  `commands=[]` (delay_ms=0). Estados `ok`/`legacy` → comportamiento actual. El validador se
  llama bajo el `_lock` (debe ser puro y breve, sin I/O).
- Criterios: (a) `ok` → enqueue → poll con `ver=999~x` ⇒ `commands=[]` y el comando SIGUE en
  cola; (b) `ok` → enqueue → poll sin `ver=` con require_version ⇒ `commands=[]` y
  `bridge_status` pasa a `legacy_blocked` (la versión registrada es None, ya no `ok`); (c) sin
  validador, ambos escenarios drenan como hoy.

### F4-X5-2 (CONS-P1-2 + CONS-P2-1) — audit de exec fail-closed en el chokepoint
Archivos: `loopback.py` + `server.py`.
- Mover allowlist + audit de exec al **chokepoint `ServerState.enqueue_command`** para
  `cmd == "exec_enforce"`, vía hooks inyectados por `Runtime` (`exec_allowlist: set[str]` +
  `exec_audit: callable(expr, verdict, main_fn, command_id|None)` — de nuevo callables,
  stdlib-only intacto). Así el gate cubre los DOS ingress (tools MCP in-process y POST
  /enqueue del harness).
- Orden vinculante dentro de enqueue_command para exec: validar expr (no-vacía + ∈ allowlist)
  → escribir audit → solo si el audit NO lanzó → append a la cola. Audit **fuera del `_lock`**
  (no I/O bajo lock); el append después, bajo el lock como hoy.
- Resultados HTTP/status: expr vacía o fuera de allowlist → `403 {"error":"exec_not_allowed"}`
  + audit `denied`; fallo de escritura del audit → `503 {"error":"audit_failed"}` SIN encolar
  (fail-closed). `queue_full` (429) ya no puede dejar un allowed sin rastro: el audit va antes.
- La entry de audit incluye SIEMPRE `main_fn` (string, "" si no viene).
- La tool `exec_enforce` de server.py deja de duplicar el match: traduce status≠200 del
  chokepoint a `ToolError` (exec_not_allowed / audit_failed / queue_full). Una sola fuente de
  verdad para el match.
- Sin hooks configurados (shim standalone): exec_enforce NO está en la whitelist (el shim no
  expone el flag) → el chokepoint es inerte; si alguien construye ServerState con
  enable_exec_enforce=True y SIN hooks → deny-all (fail-closed), no fail-open.
- Criterios: (a) monkeypatch del audit que lanza ⇒ la tool da ToolError Y
  `fake.commands_seen == []` (nada encolado); (b) POST /enqueue exec con expr fuera de
  allowlist ⇒ 403 + línea `denied` en el JSONL; (c) expr vacía vía tool ⇒ ToolError + línea
  `denied`; (d) entries contienen `main_fn`.

### F4-X5-3 (CONS-P1-3 + CONS-P2-3) — install: `--require-version` por defecto + cwd-safe
Archivo: `install-mcp.ps1` (+ `tools/pyproject.toml` NUEVO si eliges editable-install).
- Switch `-AllowLegacy` (default OFF). SIN él, `$serverArgs` incluye `--require-version`; con
  él, no. (PowerShell 5.1: switch default-off es el patrón limpio para un default-on del flag.)
- Fix del cwd: el comando emitido/registrado debe arrancar desde CUALQUIER cwd. Vía
  recomendada: `tools/pyproject.toml` mínimo (package `dayz_mcp` + py-module `mcp_capture`) +
  `pip install -e .` en el install. Alternativa si editable falla en Python 3.14: launcher por
  path absoluto. Justifica la elección en el handoff con la verificación.
- Criterios: (a) output print-only default contiene `--require-version`; con `-AllowLegacy` no;
  (b) `& $VenvPython -c "import dayz_mcp, mcp_capture"` ejecutado con cwd=$env:TEMP → exit 0.
  Pega ambos outputs literales en el Bloque B.

### F4-X5-4 (CONS-P2-2) — rango de `year` en la capa Python
Archivo: `server.py`, tool `world_time_set`.
- `year` fuera de [1970, 2100] → `ToolError("bad_year")`, antes de encolar. SOLO Python: la
  capa Enforce queda como está (cero rebuild — el hueco Enforce-side queda documentado como P3).

### F4-X5-5 — tests
- Nuevos stdlib-only (patrón `test_fase4b_loopback.py`, SIN import de mcp):
  `tests/test_x5_loopback.py` — gate de entrega (criterios a/b/c de F4-X5-1), versión no
  pegajosa (None sobreescribe → version refleja el último poll), chokepoint exec vía HTTP
  (403/503/audit denied), `ver=` malformado (sin tilde, vacío) → version_mismatch.
- Nuevos con mcp (patrón `test_fase4b_tools.py`): `tests/test_x5_tools.py` — audit-fail ⇒ no
  encola; main_fn en entries; year 1969/2101 ⇒ bad_year, 1970/2100 ⇒ pasan al fake;
  **regression BOM**: allowlist escrita con BOM real (`encoding="utf-8-sig"` al escribir el
  fixture) carga OK.
- Wire-type (LL-139 residual): en `test_loopback.py`, los payloads POST /result que SIMULAN AL
  BRIDGE pasan a `"ok": 1` / `"ok": 0` (int). Las respuestas HTTP propias del loopback
  (`{"ok": true}` que genera Python) NO cambian — solo los fixtures que imitan el wire del bridge.
- Comando esperado (pega output literal):
  `& "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -m unittest discover -s tests -v`
  desde `…\DayZ_MCP_dev\tools\` → TODO verde (27 existentes + los nuevos). Y el subset
  stdlib-only por nombre con el python del sistema (sin mcp), como hace la suite actual.

## Restricciones críticas (vinculantes toda la sesión)

1. **`loopback.py` sigue stdlib-only** (los hooks son callables inyectados, sin imports nuevos).
2. **PROHIBIDO tocar**: cualquier `.c` (Enforce), `mcp_capture.py`, `mcp_client.py`,
   `run-*.ps1`, `gate4a_mcp_client.py`, los tests existentes salvo los fixtures wire-type de
   `test_loopback.py` indicados en F4-X5-5.
3. **Back-compat dura**: shim standalone (`tools/mcp_server.py` + `loopback.main()`) =
   comportamiento OBSERVABLE idéntico (sin validador, sin hooks). Los 5 endpoints y
   `/set_poll_delay` intactos. La suite existente debe pasar SIN editar (salvo el punto
   wire-type autorizado).
4. **Te tentará arreglar P3s al pasar por las mismas funciones** — el leak de `_results` en
   timeout, el audit I/O síncrono en el event loop (P3-007: NO lo muevas a to_thread; solo
   reordenas), el exit-code del lifespan, el alias `from_pos`, el sentinel -999.0, validar
   year en Enforce. RESISTE: todos son backlog explícito del bug-ledger. Cambiarlos invalida
   el scope-clean del receptor.
5. **NO improvises fuera del consolidado**: si algo no encaja (p.ej. el validador bajo lock
   crea un problema real), no rediseñes — anota en el handoff con path:line, aplica la
   interpretación conservadora y márcalo.
6. **R21 de esta X.5 NO se hace en esta sesión**: implementa, tests verdes, y para. Sin
   pasadas de cleanup.

## Output esperado al cerrar

- **Bloque A**: archivos creados/modificados con path absoluto + líneas aprox por archivo.
- **Bloque B**: output LITERAL de la suite completa (venv) + subset stdlib-only + los dos
  outputs del install (default y -AllowLegacy) + el import-check desde $env:TEMP.
- **Bloque C**: hallazgos/desviaciones del consolidado detectados al implementar (si los hay).
- **Bloque D**: handoff a `C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-06-11-DayZ_MCP-x5-fase4-codex.md`
  (qué se hizo, decisiones tomadas donde el consolidado daba opción, qué queda para el receptor).

===== PROMPT FIN =====
