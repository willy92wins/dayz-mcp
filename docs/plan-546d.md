# Plan R26 — `546d` `vehicle_trace mode=dump`

Ficha `fb-20260909-190200-546d`. Spec: **G3** (stream pull atómico; esta ficha lo enmienda con persistencia a disco). Group G sigue **❓**.

Lane **única**: dump vs drop ya lo cerró el dueño (dump). Mecanismo citado a `path:line`. Contrato **[EXACT]** abajo.

**Estado:** criterios del host + source-contract Enforce son verificables offline. Implementar en `DayZ_MCP_dev` `main`. Survive-kill in-game, PACKONLY y G3 GREEN **no** se fingen (I3 leftover).

---

## Contrato sellado

El dueño eligió **dump**, no drop. No se espera otra grill de formato: los huecos de la ficha (“JSONL/CSV”, “autodump en stop/start”) se sellan aquí con APIs ya verificadas.

| Param | [EXACT] |
|---|---|
| Modo | `mode=dump` añadido a `TRACE_MODES` y al ingress `_one_of` |
| Escritor | **Enforce** (`MCPVehicleTrace.Dump`). Un `call_bridge` por dump. **No** pager Python de `read` (eso es drop automatizado) |
| Formato | **JSONL** (ficha: JSONL primero). Línea 0 = header `MCPVehicleTraceRead` con `samples=[]`; líneas 1..N = `MCPVehicleTraceSample`. `WriteToString(..., false)` (compacto; `true` rompe JSONL). CSV **fuera** |
| Path | Solo canónico `$profile:dayz_mcp_trace_<trace_id>.jsonl`. **Sin** parámetro `path` en la tool. `args.path` extra → `bad_args` |
| Autodump | **sí** en `Stop` (mismo fichero). **no** en `start` (`trace_exists` sigue). **no** en `Abort`/`clear`/`release` |
| Wire | View `mode=dump`, `samples=[]` (no 8192 por HTTP), `{path, rows, schema}` |
| Schema fichero | `dayz-mcp-vehicle-trace-v1` (`TRACE_SCHEMA`); `mode=dump` en el header. `validate_trace` sigue exigiendo `mode=read` (G3 artifact). `load_dump_jsonl` reconstruye un trace con `mode=read` para el validador |
| Bridge version | **sin bump** (`MCP_BRIDGE_VERSION = "10"`). Python nuevo + PBO viejo → `bad_mode` |
| Group G | sigue **❓**. Dump no cierra el curso live |

Nombre canónico (trace_id = 32 hex minúsculas):

```
dump_relpath(id)    = "dayz_mcp_trace_" + id + ".jsonl"
dump_profile_path(id) = "$profile:" + dump_relpath(id)
```

---

## Qué arregla

Incidente de la ficha: 2884 muestras en RAM del cliente, 0 leídas, mueren al matar el proceso. `read` pagina ≤64. `build_artifact` es G3 host, no dump in-process.

| | Pedido | Este cambio |
|---|---|---|
| **dump** | persistir el buffer bajo `profiles\` | Enforce escribe JSONL en `$profile:` |
| **drop** | paginar antes de cerrar | **fuera** |
| **G3 GREEN** | curso live + PACKONLY | **fuera** (❓) |

---

## Hechos verificados (2026-09-12, `main` @ `edc7bb3`)

- `TRACE_MODES = {start, status, stop, read, clear}` — `tools/dayz_mcp/vehicle_trace.py:18`
- `normalize_request`: `mode not in TRACE_MODES` → `bad_mode` — `:174-175`
- Ingress: `_one_of("start", "status", "stop", "read", "clear")` — `loopback.py:514`
- Dispatch: la misma lista; otro modo → `bad_mode` — `MCPClientBridge.c:1300-1304`
- `View` solo copia muestras si `mode == "read"` y `limit` 1..64 — `MCP_CarScript.c:400-413`
- `$profile:` + `OpenFile` WRITE + `FPrintln` — `MCPClientBridge.c:355`; vanilla `ensystem.c:406-414`
- JSONL compacto: `JsonSerializer.WriteToString(result, false, body)` — `MCPClientBridge.c:4401-4403`
- Lector JSONL ya existe (telemetría) — `MCPBridge.c:2500-2516`
- `JsonFileLoader.JsonSaveFile` pretty-print (`nice=true`) — `jsonfileloader.c:140` — **no** sirve para JSONL
- G3 spec histórica: exactamente esos 5 modos y “no persistencia del trace” — `plans/2026-07-25-vehicle-trace-feature-spec.md:52,76`
- Tests del contrato leen `DayZ_MCP_dev/addon/`, no el sibling `DayZ_MCP` — `tools/tests/_addon_paths.py:5-10`
- `is_allowed_profiles_dir` es para logs de run, no un argumento de dump — `log_tail.py:98-118`

---

## Fuera de alcance (R25 / R20)

- Lote `2edd-1`/`dae1-1`, PARO `3fc1`/`1025`, reabrir `0ab2`
- Drop / pager host de `read`
- CSV; path de caller; autodump en `start`/`Abort`
- Bump `MCP_BRIDGE_VERSION`; G3 GREEN; catálogo Group G
- `build_artifact` / `validate_trace` aceptando `mode=dump` en crudo
- Resellado PBO / in-game / R9 fingido
- Reescribir HANDOFF LIVE-STATE
- Sibling `P:\DayZ_MCP` (el suite no lo busca)

---

## Schema [EXACT]

### Ingress / tool

`vehicle_trace` no añade `path`. Payload de dump = el de los otros modos (`mode, trace_id, cursor, limit, sample_hz, max_samples`). `cursor`/`limit` se validan igual y Dump los ignora.

```
TRACE_MODES = frozenset({"start", "status", "stop", "read", "clear", "dump"})
```

loopback `_one_of` la misma sexta.

### Wire (MCPResult.trace)

Campos nuevos en `MCPVehicleTraceRead`:

```
string path;   # "" hasta un Dump ok
int rows;      # 0 hasta un Dump ok; tras Dump == s_Count
```

Dump:

```
trace.schema = "dayz-mcp-vehicle-trace-v1"
trace.mode = "dump"
trace.trace_id = <32 hex>
trace.path = "$profile:dayz_mcp_trace_<trace_id>.jsonl"
trace.rows = s_Count          # 0..8192
trace.samples = []          # nunca el buffer completo por HTTP
```

Resto de View (active/complete/count/…) igual que `status`. `normalize_bridge_result`: si `mode=="dump"`, exige `path == dump_profile_path(trace_id)` y `rows` int 0..8192; si falta → `bad_bridge_trace_dump`. Otros modos no exigen `path`.

### JSONL on-disk

```
line 0: header object (View dump, samples vacío, path y rows puestos)
line 1..N: un MCPVehicleTraceSample por línea
N = header.rows = header.count
```

Bools 0|1 como el resto del puente. `load_dump_jsonl(path)` → `{path, rows, schema, trace}` con `trace.mode="read"` para `validate_trace`.

Enmienda G3 [EXACT], una frase en `product-spec.md` (estado sigue ❓):

> `mode=dump` persiste el buffer en `$profile:dayz_mcp_trace_<trace_id>.jsonl` (JSONL; autodump en `stop`; el wire no pagina las N muestras); no sustituye el curso live ni el artefacto G3.

---

## Exit codes [EXACT]

| Código | Dónde | Cuándo |
|---|---|---|
| `bad_mode` | `normalize_request` / Dispatch / ToolError | modo fuera de `TRACE_MODES` (hoy: `dump`; mañana: `DUMP`) |
| `bad_trace_id` | `normalize_request` | dump (y no-start) con id vacío o no 32 hex |
| `bad_args` | loopback | `path` u otra clave extra; límites cursor/limit/hz/max |
| `trace_not_found` | Enforce Dump/Stop | no hay trace con ese id |
| `dump_failed` | Dump / Stop autodump | `OpenFile` 0 o `WriteToString` false. Stop **sí** deja `s_Active=false`; se puede reintentar `dump` |
| `bad_bridge_trace_dump` | `normalize_bridge_result` | dump sin path canónico o `rows` inválido |
| `dump_count_mismatch` | `load_dump_jsonl` | `rows`/`count` ≠ líneas de muestra |
| `dump_invalid` | `load_dump_jsonl` | JSON roto, schema/mode de header mal |
| unittest | process exit **0** | comando abajo |

```
.\.venv-mcp\Scripts\python.exe -B -m unittest tests.test_546d_dump tests.test_vehicle_trace tests.test_vehicle_trace_contract tests.test_validate_command_args_table tests.test_boundary_values_are_pinned tests.test_precondition_docs -v
```

cwd `DayZ_MCP_dev/tools`. Exit **0**.

---

## Fixtures [EXACT]

Un test = un veredicto. Sin DayZ.

### Positivos

| ID | Given | When | Then |
|---|---|---|---|
| P1 | `TRACE_MODES` con dump | `normalize_request("dump", "a"*32, 0, 64, 20, 4096)` | dict `mode=dump`, mismo `trace_id`; no `bad_mode` |
| P2 | ingress | `validate_command_args("vehicle_trace", dump_args)` | `(True, None)` |
| P3 | JSONL 1 header + 2 samples, `rows=2`, `count=2`, schema v1, mode dump | `load_dump_jsonl` | `rows=2`; `trace.mode=="read"`; `len(samples)==2`; `validate_trace` no STOP por schema/mode |
| P4 | FastMCP mock bridge dump (`path` canónico, `rows=3`, `samples=[]`, bools 0\|1) | `vehicle_trace(mode="dump", trace_id=…)` | un `call_bridge`; result `path`/`rows`/bools nativos |
| P5 | source `addon/` | grep | Dispatch acepta `"dump"`; `MCPVehicleTrace.Dump(`; `Stop` llama `Dump(`; path `$profile:dayz_mcp_trace_`; `FileMode.WRITE`; `WriteToString(..., false` |

### Negativos

| ID | Given | When | Then |
|---|---|---|---|
| N1 | hoy, pre-fix | `normalize_request("dump", …)` | `ValueError("bad_mode")` — el test post-fix es el inverso de P1; N1 queda como “`DUMP`/`Dump` siguen `bad_mode`” |
| N2 | dump args + `path="C:\\Windows\\x.jsonl"` | `validate_command_args` | `(False, "bad_args")` |
| N3 | dump sin `trace_id` / `""` | `normalize_request` | `bad_trace_id` |
| N4 | header `count=2`, una línea de muestra | `load_dump_jsonl` | `dump_count_mismatch` |
| N5 | `vehicle_trace` tool source | contar `call_bridge` en el cuerpo | **1** (no pager) |
| N6 | dump result `path="$profile:../secret.jsonl"` o rows=-1 | `normalize_bridge_result` | `bad_bridge_trace_dump` |
| N7 | PBO viejo (source-contract: Dispatch **sin** dump sería RED; con dump, Python viejo no lo envía) | — | leftover I3: Python nuevo + PBO live actual → `bad_mode` hasta resellar |

### INCONCLUSO (LL-017)

| ID | Precondición | Si falla |
|---|---|---|
| I1 | `.venv-mcp\Scripts\python.exe` | No correr; no PASS |
| I2 | `addon/scripts/4_World/MCP_CarScript.c` y `5_Mission/MCPClientBridge.c` | Skip source-contract; no PASS |
| I3 / I-game | Cliente vivo, traza con N>0, kill tras dump, fichero aún en `_client\profiles` | **No** este turno. PACKONLY + H8 leftover |

### No-regresión

`test_vehicle_trace.py` request contract (START/id mal/limit 65). `test_precondition_docs` cláusulas seated/clear. `test_boundary_values_are_pinned` 19/20 y 60/61 Hz.

CHK017 (falsas que los Then rechazan): pager Python (N5); dump que solo escribe el chunk de 64 (`Dump` recorre `s_Count`, no `limit`); aceptar `path` de caller (N2); JSON pretty-print (source `false`).

---

## Sesión de día + DZ-R9 (leftover; no este turno)

1. Unittest exit 0 **antes** de R9.
2. PACKONLY del addon; resellar PBO live. Hasta entonces dump contra el PBO actual es `bad_mode`.
3. In-game: start traza ≥2 s; `dump`; matar cliente; el JSONL sigue en `_client\profiles` con `rows==count`; `stop` autodump deja el mismo nombre; sin traza → `trace_not_found`.
4. R9 si el dueño trata el JSONL como dato de progreso (este worker no lo finge). Ángulos si se abre: data-loss (kill), persistencia de fichero, fail-closed de path.
5. Owner/integrador: HANDOFF. Este worker no reescribe LIVE-STATE.

---

## Call-sites (R7)

Invariante: el buffer vive en el cliente; dump lo copia a `$profile:dayz_mcp_trace_<id>.jsonl` en un comando; el wire no sustituye `read`.

Grep: `TRACE_MODES`, `_one_of("start"`, `bad_mode`, `DispatchVehicleTrace`, `MCPVehicleTrace.View`, `MCPVehicleTrace.Stop`, `MCPVehicleTrace.Clear`, `Abort(`, `build_artifact`, `validate_trace`. Opuesto: `read` (paginar) vs dump (fichero); `clear`/`Abort` no borran el JSONL; `JsonSaveFile` pretty no se usa.

`build_artifact` no lee el dump. `validate_trace` sigue en `mode=read`.

---

## Criterios verificables (R26.1)

1. P1 ejecutable: dump deja de ser `bad_mode`.
2. N2 Then estable: `path` extra = `bad_args`.
3. P3/N4: JSONL pos+neg con `rows` único.
4. N5: un solo `call_bridge` (no pager).
5. I3 no se corre en el turno de code.
6. G3 sigue ❓; una frase de enmienda en `product-spec.md`.

**Listo para implementar:** sí, rebanada host + Enforce source. I3/PACKONLY/R9 leftover.

---

## Mapa cliente/servidor

| Dato | CLIENT? | SERVER? | Puente |
|---|---|---|---|
| Buffer `s_Samples` | sí (owner) | no | RAM del cliente |
| JSONL dump | `$profile` del cliente | no | `OpenFile` local |
| `mode=dump` | Dispatch cliente | no | cmd `vehicle_trace` ya cliente |
