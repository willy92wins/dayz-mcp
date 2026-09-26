# BUG-062(b) — re-acreditación de cliente vivo

Fecha: 2026-07-27
Estado: **GREEN offline; pendiente recepción R21 independiente y carga en una sesión cliente nueva**
Alcance: exclusivamente credencial/transporte cliente de PROMPT A. PROMPT B no se tocó ni se repitió.

## Bloque A - Archivos

Código productivo editado:

- `tools/dayz_mcp/daemon_credential.py`: separa drift de policy de reemplazo de daemon; añade época single-flight, revalidación de la autoridad inmutable y exactamente un replay con el request/deadline originales. Segundo fallo, 401 o deadline agotado cierran con código accionable.
- `tools/dayz_mcp/control_client.py`: expone el drift del cliente como `client_policy_untrusted_open_new_session`.
- `tools/dayz_mcp/server.py`: publica los dos códigos nuevos sin degradarlos a `remote_error`; siguen fuera del camino lazy-spawn.

Tests editados:

- `tools/tests/test_daemon_credential.py`: A→B legítimo, drift, daemon ilegítimo, segundo fallo/401, deadline agotado real y single-flight concurrente.
- `tools/tests/test_control_client.py`: recuperación de `session_status` sin alterar lease/ticket/operation/state locales.
- `tools/tests/test_client_runtime_control_composition.py`: errores accionables públicos y cero spawn.

Verificación host-direct: `Test-Path=True` para los seis archivos. SHA-256 finales:

- `daemon_credential.py`: `73AEBCD50F809CFE0FF1F839329CF96F9CCC3E6116E5E9E921B1436485FD91A9`
- `control_client.py`: `D9A21734E0D111D6DB6DC8EA94B4717147E63C95FF1768B52504E797E9160F8A`
- `server.py`: `0BCC612C9477C17D5239A19606FEA83687C32A8049E8237EC8769DDF618C5FE1`
- `test_daemon_credential.py`: `F7C80C0E0F440C773B4EC108279B05D31F5452F53246892C152A9C063C322E20`
- `test_control_client.py`: `8070AD22B112D4B370F56EE7CDFA238DA6791F0243BC196F76BBD44E07AF6059`
- `test_client_runtime_control_composition.py`: `DCBFC926AC8641449C73D8F0826E9AE5F64561F2B1DDC62D359A5E9922EF78E3`

Fuera de alcance: `accredited_daemon_transport.py` sigue en `FEEC5F9657EBFDAB668923CFBE897F3AA2E14FD9CFC2AAB843BAF7F0D07B6556`; `doctor.py` (PROMPT B) sigue en `B8158E016A06550471B84994DB112B6D3C460DF2F0631ACF0A2552C9D0AA182A`, mtime 2026-07-26. Tampoco se tocaron lifecycle, coordinación, loopback, Enforce ni formatos persistentes.

## Bloque B - Evidencia

TDD: el negativo de deadline produjo primero exit 1 al escapar un `TimeoutError("daemon_request_deadline_exceeded")`; tras corregir ese borde, la salida final exacta fue:

GREEN focal final, incluidos todos los negativos exigidos:

```text
test_replaced_legitimate_daemon_reaccredits_and_retries_once (tests.test_daemon_credential.RefreshingDaemonCredentialTests.test_replaced_legitimate_daemon_reaccredits_and_retries_once) ... ok
test_policy_drift_during_reaccreditation_fails_before_retry (tests.test_daemon_credential.RefreshingDaemonCredentialTests.test_policy_drift_during_reaccreditation_fails_before_retry) ... ok
test_illegitimate_daemon_fails_after_one_reaccreditation_attempt (tests.test_daemon_credential.RefreshingDaemonCredentialTests.test_illegitimate_daemon_fails_after_one_reaccreditation_attempt) ... ok
test_reaccreditation_does_not_chain_into_a_third_auth_attempt (tests.test_daemon_credential.RefreshingDaemonCredentialTests.test_reaccreditation_does_not_chain_into_a_third_auth_attempt) ... ok
test_reaccreditation_retry_uses_original_exhausted_deadline (tests.test_daemon_credential.RefreshingDaemonCredentialTests.test_reaccreditation_retry_uses_original_exhausted_deadline) ... ok
test_identity_failure_never_creates_a_third_attempt (tests.test_daemon_credential.RefreshingDaemonCredentialTests.test_identity_failure_never_creates_a_third_attempt) ... ok
test_concurrent_identity_failures_reaccredit_single_flight (tests.test_daemon_credential.RefreshingDaemonCredentialTests.test_concurrent_identity_failures_reaccredit_single_flight) ... ok
test_status_reaccredits_replaced_daemon_without_local_state_change (tests.test_control_client.ControlClientTests.test_status_reaccredits_replaced_daemon_without_local_state_change) ... ok
test_credential_and_identity_errors_are_public_without_spawn (tests.test_client_runtime_control_composition.ClientRuntimeControlCompositionTests.test_credential_and_identity_errors_are_public_without_spawn) ... Executing <Task finished name='Task-7' coro=<ClientRuntimeControlCompositionTests.test_credential_and_identity_errors_are_public_without_spawn() done, defined at C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_client_runtime_control_composition.py:254> result=None created at C:\Python314\Lib\asyncio\runners.py:109> took 0.582 seconds
ok

----------------------------------------------------------------------
Ran 9 tests in 0.634s

OK
```

Regresión credencial/transporte/control/composición:

```text
......................................................Executing <Task finished name='Task-168' coro=<ClientRuntimeControlCompositionTests.test_bridge_and_control_share_one_refreshing_credential() done, defined at C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_client_runtime_control_composition.py:495> result=None created at C:\Python314\Lib\asyncio\runners.py:109> took 0.572 seconds
.........
----------------------------------------------------------------------
Ran 63 tests in 1.211s

OK
```

Shake del caso single-flight: `25/25 OK`. `py_compile` de los tres módulos productivos y los tres tests: exit 0.

## Bloque C - Ambigüedades resueltas

1. **Dónde ocurre la acreditación completa.** No existe identidad adoptable/cacheada en el provider: cada `_send` delega en `verified_daemon_http_request`. El single-flight coordina la revalidación de la policy inmutable y el replay vuelve a ejecutar el transporte canónico completo: propietario único del socket, PID estable, executable/argv/cwd y dos snapshots nativos antes del HTTP (`accredited_daemon_transport.py:115-201,204-298`). No hay atajo por status.
2. **Qué significa “un reintento”.** Máximo total de dos `_send`. Si el replay devuelve 401 no se encadena el refresh H12: cierra con `daemon_reaccreditation_failed_open_new_session`.
3. **Drift propio frente a daemon nuevo.** La autoridad original no se recarga ni adopta. Un cambio de keyfile/argv/cwd/build-id/hash o fallo de `policy.revalidate()` cierra como `client_policy_untrusted_open_new_session` antes del replay.
4. **Deadline original.** Se reenvía el mismo valor absoluto. La revisión descubrió que el transporte puede lanzar `TimeoutError` directamente antes de conectar; ese segundo fallo ahora también se sanitiza al código accionable, sin ampliar presupuesto.
5. **Mensaje accionable.** El texto público del protocolo es el código estable; ambos códigos nuevos contienen `open_new_session` y no incluyen secretos ni identidad.

## Bloque D - Handoff

- Veredicto: **GREEN offline**. Una sesión cliente que cargue este código sobrevive a futuros reemplazos legítimos; los clientes arrancados antes del cambio necesitan una única sesión nueva para cargarlo.
- No se arrancó/reinició/mató ningún daemon, no se llamó lifecycle, no se adquirió lease y no se alteraron cola, tickets, runs u ownership. No se lanzó DayZ.
- No se ejecutó `session_status`: sin listener 8765 podía entrar en lazy-spawn, prohibido por PROMPT A. Postflight MCP degradado/offline, no verificación live.
- No hubo E2E con daemon real por esa prohibición; el gate es unitario/aislado y reutiliza la suite del transporte canónico. Pendientes: recepción R21 independiente de Claude y cualquier gate live sólo si el orquestador lo autoriza después.
- PROMPT B/`doctor.py` quedó intacto y no se repitió.
- No hay worktree Git operativo; trazabilidad por paths, líneas, mtimes y SHA-256.
- El conector rechazo crear el handoff canonico en `AI\30_Sessions`; quedan como cierre durable este recibo y `HANDOFF.md`.
- La próxima acción global `vehicle_trace` no cambia: Fase A0→A4, cierre `DIAGNOSTIC READY / STOP`, sin live ni Mercedes.
