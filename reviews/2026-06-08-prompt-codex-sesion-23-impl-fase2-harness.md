# Prompt Codex — sesión 23 — fase 2 Sesión B (harness Paso 4 + spike)

Patrón: implementation handoff. Scope: harness Python + run-fase2.ps1 + fixture (Paso 4). Codex entrega un harness ejecutable; el spike in-game lo corre el usuario después. Generado por Claude 2026-06-08. Copiar entre marcadores.

```
===== PROMPT INICIO =====

Tarea: implementar el harness de fase 2 (Paso 4 del plan v2) que hace testeable in-game el bridge ya implementado en Sesión A. Esta sesión cubre **únicamente el Paso 4**: whitelist Python, `--mode phase2` + suite de verdict en `mcp_client.py`, y `run-fase2.ps1` (clon de run-fase1.ps1) que escribe el fixture y prepara la escena. **NO toques los .c del bridge** (ya están, Sesión A) ni el harness de fase 0/1. Tú entregas el harness ejecutable; el **spike in-game lo corre el usuario** (no lo lances tú).

## Carga inicial obligatoria (lee antes de tocar nada)

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-08-fase2-observacion.md
   (plan v2 SPEC; Paso 4 + "Spike + matriz de validación" + "Semántica ok/error" + contrato fixture).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_server.py
   (WHITELISTED_COMMANDS :14 — ampliar).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_client.py
   (--mode :629, output :638, y la suite phase1 existente — clónala para phase2).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-fase1.ps1
   (orquestador a clonar: setup de misión, cliente auto-conectado, invocación de mcp_client, launch por filepatching).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c
   (nombres EXACTOS de campos del result que la suite debe asertar: `raycast.hit/pos/normal/distance/object_type/parent_type/hier_level/...`, `telemetry.found/type/health01/last_valid.fixture_id/line_count_read/parse_error`).

NO releas el research consolidado. NO re-revises los handlers .c de Sesión A (solo usa MCPMessages.c para los nombres de campo).

## Alcance acotado — Paso 4 del plan v2

Archivos de salida (SOLO estos tres):
- `...\DayZ_MCP_dev\tools\mcp_server.py` — `WHITELISTED_COMMANDS += {"scene_raycast","telemetry_read"}`.
- `...\DayZ_MCP_dev\tools\mcp_client.py` — `--mode` choices += `"phase2"`; output `fase2-verdict.json` si `mode=="phase2"`; **suite phase2** (clona la estructura de la suite phase1).
- `...\DayZ_MCP_dev\tools\run-fase2.ps1` — clon de run-fase1.ps1 + escritura del fixture + target estático.

### Suite phase2 (mcp_client.py) — asertar contra los campos reales de MCPResult
Matriz del plan (cada caso = comando + aserción independiente; anti-tautología LL-115: el esperado se computa de la pos de spawn conocida, NO de la lectura del bridge):
- **C1-smoke (confound control, LL-116, PRIMERO)**: raycast recto hacia abajo desde un punto sobre el player hacia el suelo → `raycast.hit==true`. Si falla → reportar `raycast_setup_fail` (cliente no conectado / geo no cargada) y ABORTAR C1, NO marcar C1 FAIL (distingue setup de API rota, LL-093).
- **C1 dinámico** (× `method=rvproxy` y `method=bullet`): raycast desde punto conocido hacia el vehículo spawneado → `hit==true`, `object_type` esperado, `distance` dentro de tolerancia vs la distancia computada, `normal` no-cero. Registrar la normal de ambos métodos para **elegir la primaria de C1**.
- **C1 estático** (× ambos métodos): raycast hacia un target estático a pos conocida → confirma que el server golpea geo estática.
- **C1 negativo**: raycast a cielo/vacío → `ok==true`, `hit==false`.
- **C1 proxy**: si `hier_level>0`, asertar `parent_type`, no solo `object_type`.
- **C2 object_at**: snapshot del vehículo → `found==true`, `type`/`health01`/pos esperados.
- **C2 fixture_jsonl**: → `found==true`, `line_count_read==2`, `last_valid.fixture_id=="fx2"`, `value==7.5`, `seq==1`.
- **C2 negativos**: path no-allowlist → `ok==false error=="bad_args"`; archivo ausente → `fixture_not_found`; JSON malo → `parse_error`; tipo inexistente → `found==false ok==true`; ambiguo → `ambiguous_fixture`.
- Verdict `fase2-verdict.json`: gate PASS si todos los positivos+negativos pasan; incluir la comparación de normal rvproxy-vs-bullet como DATO para la decisión de primaria.

### run-fase2.ps1 (clon de run-fase1.ps1)
- Reutiliza el setup de misión + cliente auto-conectado + launch por **filepatching** de run-fase1.ps1 (la fase 2 NO necesita AddonBuilder; el block de Steam solo afecta al empaquetado del PBO, no al spike por filepatching).
- Spawnea el vehículo (como fase1) y **un target estático a pos conocida** (vía el world_spawn ya existente o geometría de mapa conocida); registra ambas posiciones para que la suite compute distancias esperadas.
- **Escribe el fixture** `$mission:dayz_mcp/telemetry_fixture.jsonl` con EXACTAMENTE estas 2 líneas y **SIN línea en blanco final** (el parser lee mientras `FGets>=0`; un `\n` final podría inflar `line_count_read`/dar parse_error):
  `{"fixture_id":"fx1","value":42.0,"seq":0}`
  `{"fixture_id":"fx2","value":7.5,"seq":1}`
- Invoca `mcp_client.py --mode phase2` y deja `fase2-verdict.json`.

## Gate de esta sesión (offline; el spike in-game lo corre el usuario)
1. `python -m py_compile mcp_server.py mcp_client.py` → exit 0 (pega literal en Bloque B).
2. PowerShell parsea `run-fase2.ps1` sin error (p.ej. `[System.Management.Automation.Language.Parser]::ParseFile`).
3. Dry-run de la lógica de aserción de la suite contra una copia LOCAL del fixture (parseo JSONL) → confirma que las aserciones leen los campos correctos. NO lances DayZ.

## Restricciones críticas (vinculantes)
1. **Solo stdlib** en Python (argparse, json, http.server, hmac, etc. — como mcp_server/mcp_client actuales). Cualquier dep nueva es regresión.
2. **NO toques los .c del bridge** (MCPMessages.c/MCPBridge.c) ni los handlers de Sesión A. Si el spike (cuando lo corra el usuario) revela un bug del bridge, va a una corrección aparte, NO aquí.
3. **NO cambies el harness fase 0/1** (`--mode poc/phase1`, run-poc.ps1, run-fase1.ps1) salvo lo estrictamente compartido e inevitable; si tocas algo compartido, decláralo en Bloque C.
4. **NO amplíes la matriz** más allá de los casos C1/C2 del plan. Te tentará añadir más escenarios "ya que estoy" — cíñete a la matriz.
5. **Filepatching, no AddonBuilder**: run-fase2.ps1 lanza por filepatching como run-fase1.ps1; no dependas de AddonBuilder (está bloqueado por Steam init, irrelevante para el spike).
6. **NO te autorrevises (R21)** en esta sesión. Entrega el harness y para.

## Output esperado al cerrar (bloques A/B/C/D)
- **Bloque A** — archivos modificados/creados (mcp_server.py, mcp_client.py, run-fase2.ps1) con tamaño aprox y resumen.
- **Bloque B** — gate offline: output literal de `py_compile`, del parse de PowerShell, y del dry-run de aserciones.
- **Bloque C** — hallazgos: algo compartido que tuviste que tocar, supuestos sobre el target estático, cualquier R22-corto. Si ninguno: "Sin hallazgos."
- **Bloque D** — handoff: instrucciones EXACTAS para que el usuario corra el spike (comando run-fase2.ps1, qué recolectar: fase2-verdict.json + RPT + script_*.log), y qué decide el spike (primaria C1 rvproxy-vs-bullet, geo estática server-side).

===== PROMPT FIN =====
```
