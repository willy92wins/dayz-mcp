# Plan R26 — `b1ff` `fixture_not_ready` con telemetría

Ficha `fb-20260911-230927-b1ff` (no la `b1ff` de agosto, ya resuelta). Lane W9 Python S.

---

## Qué arregla

`vehicle_prepare_fixture` falla con `ToolError("fixture_not_ready")`. El bridge **ya** rellena `telemetry` (`PopulateTelemetryObject`) y `vehicle_fixture_ready` **antes** de poner el error (`MCPBridge.c`). Python tira el payload en `_bridge_error` porque el detalle solo se adjunta a verbos UI.

La ficha pide `{reason, expected, observed}` **o al menos** la telemetría consultada. `expected` (p.ej. `WheelCount()`) no viaja en el wire actual (`MCPApplied`/telemetry solo tiene `wheel_count` = present). Python S no toca Enforce/PBO → se publica la telemetría observada, no se inventa `expected: 4`.

## Fuera

- Enforce, PBO, `MCPBridge.c`, `IsVehicleFixtureReady`.
- Ocupación, `control_client.py`, dump/#26–#29, JSONL, in-game.
- Enriquecer `fixture_not_found` / `ambiguous_fixture` / `fixture_not_vehicle`.
- Listas `attachment_items` (no acotadas) en el mensaje.

## Superficie

| Sitio | Cambio |
|---|---|
| `tools/dayz_mcp/server.py` `_bridge_error_detail` | Si `cmd==vehicle_prepare_fixture` **y** `error==fixture_not_ready`, allowlist de escalares de `telemetry` + `vehicle_fixture_ready`. Decisión por verbo+código, no por presencia de keys (MCPResult plano). |
| Tests | Extender el patrón de `test_ui_error_diagnostics.py`. |

## Schema del mensaje [EXACT]

Cabeza = código fijo. Detalle after `"; "`:

```
fixture_not_ready; wheel_count=2 fuel_fraction=1.0 attachment_count=8 vehicle_fixture_ready=False
```

Allowlist: `wheel_count`, `fuel_fraction`, `attachment_count` (dentro de `telemetry` si es dict) y `vehicle_fixture_ready` en el result. Ausente → se omite esa pareja. Sin telemetría y sin el flag → código desnudo `fixture_not_ready`.

`world_spawn` timeout con payload cargado **sigue** `timeout` (N1 en este PR + control UI preexistente).

JSON puede entregar `wheel_count` como string `"2"`. El formateo publicado es el [EXACT] sin comillas (`wheel_count=2`), no `wheel_count='2'`. Un string no numérico se omite.

## Fixtures

| ID | Entrada | EXPECT | Exit |
|---|---|---|---|
| P1 | `_bridge_error` fixture_not_ready + telemetry wheel_count=2 | mensaje contiene `fixture_not_ready` y `wheel_count=2` | 0 |
| P2 | wire MCP `call_tool` (mismo arnés que UI) | el cliente ve esas parejas | 0 |
| N1 | mismo payload, `cmd=world_spawn` / `error=timeout` | `timeout` desnudo | 0 |
| N1b | mismo payload, `cmd=world_spawn` / `error=fixture_not_ready` | `fixture_not_ready` desnudo (gate de verbo) | 0 |
| N4 | telemetry `wheel_count="2"` (string JSON) | `wheel_count=2` sin `!r` | 0 |
| N2 | `vehicle_prepare_fixture` + `fixture_not_found` + telemetry | código desnudo `fixture_not_found` | 0 |
| N3 | `fixture_not_ready` sin telemetry | `fixture_not_ready` desnudo | 0 |
| I1 | in-game OnDebugSpawn / Axles de 1 eje | INCONCLUSO (no in-game) | — |

## Hecho cuando

P1–N3 verdes. I1 declarado. No se afirma `expected=4`.

## Leftovers (SOUND-CON-FIXES)

| ID | Estado | Nota |
|---|---|---|
| P34-P2-1 | leftover | Observados sin etiqueta `observed=`. El [EXACT] es `key=value`. No se inventa `expected`. |
| P34-P2-2 | cerrado | N1 (`world_spawn` + `timeout` + payload de fixture) además del gate de verbo. |
| P34-P2-3 | cerrado | String JSON `"2"` → `wheel_count=2`. |
