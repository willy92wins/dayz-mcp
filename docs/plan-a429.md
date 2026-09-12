# Plan R26 — `a429` overlay de diagnóstico

Ficha `fb-20260910-043740-a429`. Spec: E5 (superficie interpretable; `reopen_mcp_client` **sin** reiniciar el daemon) y H13 (ocupación nombrada, no FIFO ciego). Encargo: plan con fixtures pos+neg, exit codes y schema **antes** de codear.

Lane **única** (no dual): el mecanismo está citado a `path:line` en este árbol; un segundo research no cambia el contrato.

**Estado (2026-09-12):** implementado en `DayZ_MCP_dev` `main`. Overlay MCP only. I3 in-game no se corre.

---

## Qué arregla

Tres lecturas falsas sobre tools de **status** (no mutan procesos):

1. `tool_registry_remediation=reopen_mcp_client` siempre, incluso con `server_modules.status=fresh` → el modelo imputa crash/daemon. Hoy: `server.py:728-734` + `server_freshness.py:24`.
2. `fence.mutation_rejects_by_code` (ints de por vida, `loopback.py:1864-1866` / `:2878-2884`) se lee como bloqueo actual. `compute_bridge_ready` (`server.py:523-566`) **no** usa esos ints; `ready=true` y contadores >0 ya coexisten.
3. `session_status.blocked_on` con `box.occupied=true` manda al FIFO de `dayz_test_run` (`server.py:3406-3410`), aunque un `RUNNING_IDLE` único sin dueño se adopta al conceder el lease (`loopback.py:3180-3271`).

`9b7b` ya publica fingerprint + `tool_registry_schema_signal`. Este plan añade scope/aplicabilidad y no inventa reload de daemon.

---

## Fuera de alcance (R25 / R20)

- Enforce, PBO, `_APP_PACKAGED_MODULES`, resellado, `1025`, `3fc1`.
- `0ab2` (TTL/gracia), `546d` (dump de traza), lote `2edd-1`/`dae1-1`.
- Vista compacta opcional (la ficha decía “valorar”; no.
- Cambiar códigos de `dayz_test_run` / `active_run_exists` / `occupancy_error_fields`.
- Cambiar el mapa int de `fence.mutation_rejects_by_code` en HTTP `/status` (el juego y `test_instance_fence.py:1411-1432` leen ints).
- Reiniciar, reciclar o matar daemon/cliente/run desde `bridge_status` o `session_status`.

---

## Superficie que cambia

| Sitio | Cambio |
|---|---|
| `tools/dayz_mcp/server.py` | Overlay de registry; anotar fence **solo** en el payload MCP de `bridge_status`; `box.available_for`; `blocked_on` cuando el box es adoptable; descripción de `bridge_status` / `session_status`. |
| `tools/dayz_mcp/server_freshness.py` | Marker (cuando existe) declara `scope=tools`. Texto fresco: sin marker (ya es así). |
| Tests | Módulo nuevo + aserciones que hoy exigen el string siempre. |

HTTP `/status` **sigue** omitiendo el overlay (`test_mcp_tools.py:783-799`).

---

## Schema (MCP)

Tipos en inglés. `[EXACT]` = forma que el implementador debe publicar. No hay JSON Schema file; el contrato es este más los asserts.

### 1. `bridge_status.tool_registry_remediation`

Hoy: `string` siempre `"reopen_mcp_client"`.

**Después:** `null | object`.

```
null
```

cuando `server_modules.status == "fresh"`.

```
{
  "code": "reopen_mcp_client",
  "scope": "tools",
  "applies_when": "tool_registry_schema_signal=stale_client"
}
```

cuando `tool_registry_schema_signal == "stale_client"`.

```
{
  "code": "reopen_mcp_client",
  "scope": "tools",
  "applies_when": "tool_registry_schema_signal=unknown; sources unverifiable, not a crash"
}
```

cuando `tool_registry_schema_signal == "unknown"`.

`scope` es el enum `tools | daemon`. Este código **nunca** es `daemon`. E5: el token `reopen_mcp_client` aparece **dentro** del objeto cuando el registro/cliente está obsoleto o inverificable, y no aparece (clave `null`) cuando está fresco.

`tool_registry_schema_signal` y `tool_registry_source_stale` no cambian de semántica (`fresh` / `stale_client` / `unknown`; bool conservador).

### 2. `bridge_status.daemon_source_remediation`

Nuevo, `null | object`. Independiente del overlay de tools.

```
null
```

cuando `daemon_modules` falta, o `stale` y `unreadable` están vacíos.

```
{
  "code": "none",
  "scope": "daemon",
  "applies_when": "daemon_modules.stale or unreadable is non-empty; not a crash; do not kill; reopen_mcp_client does not refresh the daemon"
}
```

en caso contrario. `code=none` = no hay verbo. No se añade tool de reload.

### 3. `bridge_status.fence` (MCP only)

HTTP `/status` y `status_snapshot()` conservan:

```
fence.mutation_rejects_by_code: { <FENCE_MUTATION_REJECT_CODES>: int }
```

En el dict que devuelve la tool `bridge_status`, **añadir** (no sustituir los ints):

```
fence.mutation_rejects_meta: {
  "kind": "historical",
  "window": "since_daemon_start",
  "origin": "loopback_enqueue_fence",
  "blocks_now": false
}
```

`blocks_now` es **siempre** `false` para este mapa: no alimenta `ready`. Si `ready.ready === true` y algún int > 0, el test POS exige `blocks_now === false` y `ready.ready is True`.

Si el payload no trae `fence` (fixture de client sin loopback rico), no inventar el bloque.

### 4. `session_status.box.available_for`

Nuevo:

```
box.available_for: {
  "new_launch": bool,
  "adopt": bool
}
```

Función nueva junto a `_session_status_blocked_on`, p.ej. `box_available_for(box) -> dict`. Predicado de adopt **alineado** con `_adopt_on_grant` (`loopback.py:3246-3268`):

| Condición | `new_launch` | `adopt` |
|---|---|---|
| `occupied is not True` y `port_scan_known is not False` | `true` | `false` |
| exactamente un run `RUNNING_IDLE` con `owner_session` vacío/None, `foreign` vacío, `port_scan_known is not False` | `false` | `true` |
| más de un idle ownerless (`multiple_idle_runs`) | `false` | `false` |
| `port_scan_known is False`, foreign, RUNNING con dueño, UNRECONCILED, etc. | `false` | `false` |

### 5. `session_status.blocked_on`

El string FIFO actual (`test_session_status_blocked_on.py:25-28`) **solo** si `available_for.adopt is false` y `occupied is true` (y el lease no es el bloqueo).

Nuevo string cuando `adopt is true` (lease libre):

```
DayZ test box has an ownerless RUNNING_IDLE run; next: call session_acquire_wait(purpose=...) to adopt it. dayz_test_run wait_for_box_s is for a new launch, not this box.
```

No mencionar FIFO de launch en ese caso. `blocked_on` de lease extranjero no cambia.

### 6. Marker `server_code_freshness` (cuando no es fresh)

`_call_marker` (`server_freshness.py:187-205`) ya tiene `"scope": "loaded_server_modules"`. Añadir al **texto** `scope=tools` (el marker es del proceso de tools, no del daemon). `remediation=reopen_mcp_client` en el texto **solo** si el marker se emite (stale/unknown). Fresh: sin marker, como hoy (`test_server_freshness.py:128-133`).

---

## Fixtures

Un test = un veredicto. Interpreter: `tools/.venv-mcp/Scripts/python.exe`. Cwd `DayZ_MCP_dev/tools`. Módulo nuevo `tests/test_a429_overlay.py` (más actualizar asserts que rompa el schema). Harness: `build_app` + `_fixture_client_runtime` como `test_session_status_blocked_on.py` / `test_mcp_tools.py`. Sin DayZ, sin Steam, sin `:8765` real.

### Positivos (PASS)

| ID | Given | When | Then |
|---|---|---|---|
| P1 | `server_modules` fresh (watch real o snapshot parcheado `status=fresh`, `stale=[]`, `unreadable=[]`) | `bridge_status` | `tool_registry_remediation is None`; `tool_registry_schema_signal=="fresh"`; `tool_registry_source_stale is False`; `ready` puede ser true o false **sin** correlacionar con remediación. |
| P2 | Snapshot stale (`stale=["fixture"]`) | `bridge_status` | objeto remediación `code=reopen_mcp_client`, `scope=tools`, `applies_when` contiene `stale_client`; `daemon_source_remediation is None` si `daemon_modules.stale=[]`. |
| P3 | `ready.ready is True` (peers versionados como `test_mcp_tools.py:707-724`) y `fence.mutation_rejects_by_code.legacy_unbound >= 1` (inyectar el mapa en el payload **antes** del annotate, o enqueue 409 en embedded) | `bridge_status` | `ready.ready is True`; `fence.mutation_rejects_meta.blocks_now is False`; `kind=="historical"`; ints siguen siendo `int`. |
| P4 | `session_status` con `occupied=true`, un run `RUNNING_IDLE`, `owner_session=None`, `foreign=[]`, `port_scan_known=true`, `owner` lease ausente | `session_status` | `box.available_for == {new_launch: false, adopt: true}`; `blocked_on` es el string exacto de adopt (§5), **no** el de FIFO (`join the box FIFO`). El string de adopt menciona `wait_for_box_s` solo para decir que no es la cola de este box. |
| P5 | caja libre (`occupied=false`, scan known) | `session_status` | `{new_launch: true, adopt: false}`; `blocked_on is None`. |
| P6 | `bridge_status` y `session_status` con spies en `dayz_test_run`, `dayz_test_stop`, `daemon.spawn_detached`, `process.kill` | cada tool `{}` | ninguna de esas llamadas; HTTP status de las tools = éxito de `call_tool` (`isError is False`). |

### Negativos (FAIL del producto si pasaran; el test debe quedar rojo ante el código **actual** y verde tras el fix)

| ID | Given | When | Then (el fix hace que esto **no** ocurra) |
|---|---|---|---|
| N1 | same as P1 | `bridge_status` | **No** `tool_registry_remediation == "reopen_mcp_client"` (ni string ni objeto) en fresh. El test pre-fix documenta el bug: hoy el string está. Post-fix: `None`. |
| N2 | `server_modules.status=fresh` y `daemon_modules.stale=["x"]` | `bridge_status` | remediación de tools `None`; `daemon_source_remediation.scope=="daemon"`; `code=="none"`; ningún campo dice que hay que matar el daemon; `reopen_mcp_client` no es la remediación del daemon. |
| N3 | `ready.ready is True` y contadores >0 | `bridge_status` | **No** se infiere bloqueo: `blocks_now is False`. Un assert que exigiera `ready.ready is False` por contadores >0 es el anti-fixture. |
| P4-neg | occupied idle adoptable | `session_status` | `blocked_on` **no** es `BOX_BLOCKED_ON` (`test_session_status_blocked_on.py:25-28`). |
| N4 | dos runs `RUNNING_IDLE` ownerless | `session_status` | `{new_launch: false, adopt: false}` (espejo `multiple_idle_runs`); `blocked_on` no promete adopt. |
| N5 | `port_scan_known=false` | `session_status` | ambos flags false; `blocked_on` sigue el texto de port-scan (`server.py:3396-3405`), no FIFO ni adopt. |
| N6 | loopback HTTP `/status` | GET | **sin** `tool_registry_remediation`, **sin** `available_for`, `mutation_rejects_by_code.*.__class__ is int` (no objetos). |

### INCONCLUSO / setup-failed (LL-017)

| ID | Precondición | Si falla |
|---|---|---|
| I1 | `tools/.venv-mcp\Scripts\python.exe` existe | No correr la suite; no declarar PASS. |
| I2 | Fixture `_fixture_client_runtime` construye `build_app(mode=client)` | Skip del módulo, no PASS. |
| I3 | Peers reales / juego | **No** es gate de `a429`. No lanzar DayZ. |

---

## Exit codes

| Comando | Exit | Significado |
|---|---|---|
| `.\.venv-mcp\Scripts\python.exe -B -m unittest tests.test_a429_overlay tests.test_session_status_blocked_on tests.test_mcp_tools.MCPToolsTest.test_bridge_status_publishes_frozen_tool_registry_overlay tests.test_mcp_tools.MCPToolsTest.test_loopback_status_omits_tool_registry_overlay tests.test_server_freshness.ServerFreshnessTest.test_fresh_response_has_no_marker tests.test_server_freshness.ServerFreshnessTest.test_bridge_status_is_live_but_registry_fingerprint_is_frozen -v` | **0** | PASS del overlay. |
| el mismo | **1** | FAIL unittest (schema o fixture). |
| `call_tool("bridge_status"\|"session_status", {})` | `isError is False` | Las tools de status no lanzan `ToolError` en el camino feliz. |
| HTTP `/status` | **200** | Sin cambio de contrato HTTP. 401 sin key (ya existe) intacto. |

Clase verificada: `MCPToolsTest` en `tools/tests/test_mcp_tools.py:128` (métodos ~L733 y ~L783).

Focal mínimo si el argv largo estorba:

```
.\.venv-mcp\Scripts\python.exe -B -m unittest tests.test_a429_overlay tests.test_session_status_blocked_on -v
```

→ exit **0**, más los dos tests de overlay en `test_mcp_tools` actualizados.

Status tools: **no** hay código de proceso de DayZ. El “exit” del producto es el del unittest y `isError`.

---

## Pasos de implementación (cuando se autorice)

1. Helper `box_available_for` + ajuste `_session_status_blocked_on` (P4, N4, N5, P5).
2. `_frozen_tool_registry_overlay` deja de meter el string; `_with_tool_registry` pone `tool_registry_remediation` según schema_signal y `daemon_source_remediation` según `daemon_modules`.
3. Anotar `fence.mutation_rejects_meta` en `_with_tool_registry` (copia del dict; no mutar el snapshot HTTP).
4. Marker text `scope=tools`.
5. Descripciones de tools: una frase cada una (fresh ⇒ remediation null; counters historical; occupied idle ⇒ adopt).
6. Tests: módulo nuevo + actualizar `assertEqual(..., "reopen_mcp_client")` en `test_mcp_tools.py:744` y el `in "remediation=reopen_mcp_client"` del marker **solo** en caminos stale/unknown (el fresh ya no tiene marker).

No tocar `product-spec.md` E5 salvo una nota de forma (`reopen_mcp_client` como `code` scoped) si el implementador lo necesita para no dejar E5 mintiendo; no reabrir Group G.

---

## Criterios verificables (R26.1)

Más de tres entradas concretas:

1. **P1** produce `tool_registry_remediation is None` con `server_modules.status=fresh` (PASS).
2. **N2** produce `scope=daemon` + `code=none` y tools remediation null (FAIL del bug Ornith9 si el string tools reaparece).
3. **P3** `ready.ready is True` + counters >0 + `blocks_now is False` (PASS).
4. **P4** `available_for.adopt is True` y `blocked_on` no es FIFO (PASS).
5. **N6** HTTP `/status` sin overlay y con ints (PASS de no-regresión).
6. **P6** spies: status no spawnea/mata (PASS).
7. **I3** in-game no se corre → no se declara verificado in-game.

Heurística R26.1: experimento PASS = P1+P4+P6; FAIL = N1/N2/P4-neg; INCONCLUSO = I1–I3.

---

## Implementado

Sí. Fixtures P1–P6 / N1–N6 + tests de no-regresión del plan: unittest exit 0. Siguiente PARK: grill `0ab2` (no en este turno). R22 cruzado no se ha pedido. Grill Modo B no aplica a `a429`.
