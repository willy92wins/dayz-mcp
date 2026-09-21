# Enmienda A al plan de implementación — lifecycle retail manual-only

**Fecha:** 2026-07-15  
**Estado:** listo para implementar  
**Plan base:** `plans/2026-07-14-agent-session-coordination-implementation.md`  
**Diseño vinculante:** `plans/2026-07-15-agent-session-coordination-retail-lifecycle-delta.md`

Esta enmienda sustituye únicamente las partes indicadas de Tasks 6-10. Tasks 1-5 y el resto del plan base permanecen vigentes.

## Task 6 — gate corregido

Sustituir el stop gate universal de `plans/2026-07-14-agent-session-coordination-implementation.md:570-576,905` por una matriz de capacidades:

- `DayZDiag_x64.exe`: identidad completa, `managed_lifecycle=true`.
- `DayZServer_x64.exe`: `managed_lifecycle=false` hasta superar y persistir el mismo probe real; tests desechables solo cubren el guard offline.
- `DayZ_BE.exe`: wrapper completo observado, pero shape retail `managed_lifecycle=false`.
- `DayZ_x64.exe`: path + FILETIME nativos observados, command line no disponible, `managed_lifecycle=false`.

**Gate:** Task 7 puede continuar si toda identidad incompleta o todo shape retail queda fuera de la allowlist antes del launch. Cualquier fallback a nombre/mod/PID bloquea la fase.

## Task 7 — cambios vinculantes

### Superficie

Se mantienen los archivos e interfaces HTTP/CLI del plan base. `ProcessRecord` conserva los cuatro campos fuertes obligatorios; no se añaden registros parciales ni estados ambiguos. Añadir a la superficie de Task 7:

- Modify: `tools/dayz_mcp/orphan_guard.py` — snapshot read-only reutilizable por nombre; nunca termina ni atribuye.
- Modify: `tools/dayz_mcp/session_coordination.py` — rechazo auditado de autorización reservada.
- Create: `tools/tests/test_retail_quarantine.py`.

### TDD RED adicional

Antes de producción, añadir:

```python
# [DESIGN] Criterio de test; cerrar nombres tras crear las firmas reales.
def test_retail_start_is_rejected_before_launch(self):
    result = lifecycle.start_run(owner, token, retail_request)
    self.assertEqual(result["error"], "retail_manual_lifecycle_required")
    self.assertEqual(fake_launcher.calls, [])
    self.assertEqual(manifest.list_runs(), [])
```

Cubrir también `DayZ_x64.exe`, basename con case distinto, ruta no allowlisted, same-mod foreign process, PID reuse, fingerprint mismatch, release/expiry sin terminate, restart del manifiesto, cuarentena retail y fallo del snapshot.

### Implementación

1. `[EXACT]` Allowlist inicial: ruta absoluta canónica `DAYZ_GAME_PATH\DayZDiag_x64.exe`. Server permanece deshabilitado hasta artefacto real verde.
2. `[EXACT]` Autorización de launch compara la ruta resuelta completa case-insensitive. Solo las rutas canónicas instaladas `DAYZ_GAME_PATH\DayZ_BE.exe` y `DAYZ_GAME_PATH\DayZ_x64.exe` reciben `retail_manual_lifecycle_required`; una copia con el mismo basename devuelve `executable_not_allowed`.
3. `[EXACT]` El rechazo ocurre después de auth/lease y de un evento `lifecycle_start_rejected` (`reason`, identidad pública, duración; sin argv/token), pero antes de `subprocess.Popen`, creación de run o guard. Si el append falla, devuelve `audit_failed`, siempre con cero launch/manifiesto.
4. `[EXACT]` `process-guard.ps1 terminate` exige y revalida los cuatro campos fuertes sobre el mismo proceso; ningún campo es opcional.
5. `[EXACT]` El stop de dos fases, adopción `RUNNING_IDLE`, one-run invariant, persistencia atómica, audit fail-closed y release-owner del plan base permanecen.
6. `[EXACT]` No implementar Job Objects, WM_CLOSE ni discovery BattlEye en producción dentro de esta tarea.
7. `[EXACT]` Extender el ToolHelp32 prior art de `orphan_guard.py:71-98,162-189` con un snapshot read-only de coincidencias exactas case-insensitive para `DayZ_BE.exe`/`DayZ_x64.exe`. Un fallo del snapshot devuelve estado desconocido y activa cuarentena; nunca llama a `kill_pid`.
8. `[EXACT]` Añadir `SessionCoordinator.reject_authorization(owner_session_id, command, reason, http_status=409) -> AuthorizationDecision`. Siempre elimina la reserva. Si `session_rejected` se escribe, devuelve decisión denegada 409/`reason`; si el append falla, devuelve 503/`audit_failed`. `abort_authorization()` conserva su contrato actual para excepciones de enqueue.
9. `[EXACT]` Inyectar el probe en `LoopbackState`. Tras `coordination.authorize` y antes de `_enqueue_command` (`loopback.py:184-269`), toda mutación con retail presente/probe desconocido llama `reject_authorization(..., "retail_quarantine")`; devuelve exactamente la decisión 409 o 503 y hace cero enqueue. Lecturas puras no consultan/bloquean.
10. `[EXACT]` Inyectar el mismo probe en el servicio/rutas lifecycle. `start`, `stop`, `adopt` y `admin/reconcile` rechazan `retail_quarantine` después de auth y antes de manifiesto/guard; `lifecycle/status`, `admin/release` y operaciones de coordinación siguen disponibles.
11. `[EXACT]` `cleanup_owner` (`loopback.py:600-620`) cancela pendientes pero no encola `vehicle_release` bajo cuarentena; devuelve `cleanup_degraded=["retail_quarantine"]` para que release/expiry quede auditado.

### Validación

- Suite focal del plan base.
- Dos procesos desechables con label idéntico; solo el registrado termina.
- Identidad forjada; ninguno termina.
- Negativos retail prueban cero launch y cero manifest mutation.
- Retail presente/probe fallido: lecturas pasan; mutaciones y lifecycle hacen cero enqueue/guard. Audit failure devuelve `audit_failed`.
- Revisión adversarial independiente obligatoria antes de Task 8.

## Task 8 — doctor

Añadir diagnóstico `RETAIL_MANUAL_CLOSE_REQUIRED` cuando exista `DayZ_BE.exe` o `DayZ_x64.exe` fuera del lifecycle gestionado. El diagnóstico:

- es `WARN` en modo normal y `FAIL` con modo explícito `doctor --require-clean`;
- puede listar PID/nombre para observación;
- no concede ownership, no ofrece botón/acción de stop y no llama al guard;
- distingue retail conocido de `PROCESS_UNREGISTERED` desconocido;
- prueba JSON estable y códigos de salida para normal/`--require-clean`.

## Task 9 — protocolo y launchers

- Eliminar kills directos sin sustituirlos por otro scan/kill.
- Solo las rutas Diag usan lifecycle gestionado en esta fase; Server queda gated por probe.
- Ningún launcher oficial inicia retail. Si aparece por una ruta externa, el protocolo declara cuarentena: lecturas sí, mutaciones/lifecycle no.
- La sesión/usuario que abrió retail externamente debe cerrarlo por UI, ejecutar doctor/rescan y registrar el resultado.
- Si el agente pierde acceso a la UI, declara `manual_cleanup_required`; otro agente no mata el proceso para “desbloquear”.
- El checklist de handoff revisa sesión/lease/comandos y, por separado, presencia retail.

Antes de editar skills o instrucciones globales, aplicar `superpowers:writing-skills` y `anthropic-skills:skill-conventions`.

## Task 10 — proof corregido

1. Suite offline completa.
2. Gate desechable de ownership del guard.
3. Gate real H8 con `DayZDiag_x64.exe`: lecturas paralelas, FIFO mutante, release, expiry, adopción/reemplazo y cierre limpio.
4. Negativos retail: request rechazada antes de launch; presencia externa simulada bloquea mutaciones/lifecycle y deja lecturas disponibles; scan final cero.
5. `session_status` final: `own_lease=none`, `own_ticket=none`, `pending_commands=0`.
6. H8 solo se marca in-game si el gate de cuatro agentes se ejecuta realmente; offline verde no lo sustituye.

## Stop gates actualizados

1. Task 6 requiere clasificación fail-closed, no identidad universal.
2. Task 7 no puede lanzar retail ni registrar identidad parcial; presencia retail/scan desconocido impone cuarentena de mutaciones.
3. Tests adversariales Task 7 antes de modificar launchers.
4. Installer/doctor verde antes de instrucciones globales.
5. Cualquier intento de ampliar a Job Objects, cambiar D-14 o automatizar cierre retail vuelve a diseño y a un gate real separado.
