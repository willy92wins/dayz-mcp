# Feature spec — `restore_gameplay` + alineado de `vehicle_get_in_client`

**Fecha:** 2026-08-05  
**Estado:** `APPROVED_FOR_SOURCE_ONLY_BY_USER`  
**Runtime/deploy autorizado:** no  
**Persistencia/network schema:** sin cambios  
**Bridge version:** permanece en v6; verbo aditivo compatible

## Resultado

Exponer una tool pública cliente que invoque el `RestoreGameplay()` existente y
hacer que `vehicle_get_in_client` aplique el mismo restore idempotente antes de
consultar/iniciar el comando de vehículo. El cambio restablece simulación local,
input y HUD; no desactiva la cámara activa.

## Trazabilidad DPF e Intent

- Sirve al Intent D al cerrar de forma controlada la supresión introducida por
  `camera_set`: `product-spec.md:66-73`.
- Sirve al Intent G al permitir el handoff a control manual/owner antes de la
  conducción: `product-spec.md:102-118`.
- El criterio explícito G4 se añade antes del código porque el product-spec vivo
  no contiene hoy este contrato.

## Contrato funcional

| ID | Contrato | Evidencia/aceptación |
|---|---|---|
| RG-01 | `restore_gameplay` es verbo cliente, mutante y sin argumentos | whitelist cliente; no figura en `READ_ONLY_COMMANDS`; `{}` acepta y cualquier clave rechaza `bad_args` |
| RG-02 | La tool pública serializa con `runtime.tool_lock` | llamada exacta `restore_gameplay`, `{}`, peer `client`, timeout validado |
| RG-03 | El dispatch cliente invoca `RestoreGameplay()` y responde `ok=true` | source-contract sobre la rama exacta |
| RG-04 | `vehicle_get_in_client` restaura una sola vez antes de `GetCommand_Vehicle()` | espejo del guard `job.sim_restored` de `ProcessDriveProbeClientPrep()` |
| RG-05 | No se amplía la semántica de cámara | la implementación no llama `SetActive(false)` ni `DeleteOwnedCamera()` |
| RG-06 | Activación real exige rollout coherente | PBO rebuild/PACKONLY, daemon restart por whitelist, gate in-game posterior |

## Alcance exacto

- `[EXACT]` `DayZ_MCP_dev/product-spec.md`: añadir G4 y dejarlo `❓` hasta gate
  in-game.
- `[EXACT]` `DayZ_MCP_dev/tools/tests/test_restore_gameplay_contract.py`: crear
  cuatro contratos RED→GREEN para RG-01..RG-04.
- `[EXACT]` `DayZ_MCP_dev/tools/dayz_mcp/loopback.py`: añadir el verbo cliente y
  validación fail-closed de argumentos vacíos.
- `[EXACT]` `DayZ_MCP_dev/tools/dayz_mcp/server.py`: añadir wrapper público con
  mutex y peer cliente.
- `[EXACT]` `DayZ_MCP/scripts/5_Mission/MCPClientBridge.c`: añadir rama síncrona
  y el guard idempotente de BUG-040.

Fuera de alcance: desactivar/borrar cámara, subir `MCP_BRIDGE_VERSION`, desplegar
PBO, reiniciar daemon, gestionar el run ajeno, modificar lifecycle, BUG-066(c) o
BUG-061.

## Viability tests R26

| Test | Pre-fix esperado | Post-fix esperado |
|---|---:|---:|
| RG-T1 ingress/args/peer/lease | FAIL | PASS |
| RG-T2 registro FastMCP + forwarding exacto | FAIL | PASS |
| RG-T3 dispatch Enforce llama restore y marca éxito | FAIL | PASS |
| RG-T4 BUG-040 guard antes de `GetCommand_Vehicle()` | FAIL | PASS |
| Suite relacionada Python + `py_compile` | sin regresión | PASS |
| PACKONLY + gate freecam→restore/get-in | no ejecutado en esta sesión | PENDIENTE |

## Rollout y rollback

Rollout futuro, bajo lease y sin runs ajenos: construir PBO desde staging,
verificar hash/inventario, reiniciar el daemon para recargar whitelist, comprobar
bridge v6 y ejecutar negativos + freecam→restore + freecam→get-in. Si falla,
restaurar por hash los tres fuentes de producción y el PBO previo; los tests y
docs pueden conservarse como caracterización RED.

## Riesgos residuales

- El nombre `restore_gameplay` no implica `camera_release`: el viewport puede
  seguir siendo la cámara MCP aunque vuelvan input/HUD/simulación.
- Sin despliegue coordinado puede haber `not_whitelisted` o `unknown_command`.
- El source-contract no sustituye compilación Enforce ni prueba in-game.

## Entrega

El árbol no es un repositorio Git válido; la entrega usa lista de archivos,
SHA-256 y resultados de prueba. No se simulará un commit.
