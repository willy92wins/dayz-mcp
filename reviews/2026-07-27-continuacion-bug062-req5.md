# Continuación acotada — BUG-062(b) requisito 5

Continuación de un trabajo **ya casi terminado**. Raíz real del proyecto:
`C:\Users\guill\OneDrive\Documentos\DayZ Projects\`. La unidad de trabajo con letra **no está
montada en esta máquina**: cualquier ruta con esa letra que aparezca en documentos antiguos
equivale a esa raíz real. Usa siempre la raíz real.

## Ya hecho y verificado — NO lo toques, NO lo rehagas, NO lo refactorices

Los **requisitos 1 a 4** de la sección `PROMPT A` de
`reviews\2026-07-26-prompt-bug062-reacreditacion-codex.md` están implementados en
`tools\dayz_mcp\daemon_credential.py` y `tools\dayz_mcp\control_client.py`, con **58 tests en
verde** en `tools\tests\test_daemon_credential.py`, `test_control_client.py` y
`test_client_runtime_control_composition.py`. Verificado por el orquestador, incluyendo los
negativos (`test_policy_drift_during_reaccreditation_fails_before_retry`,
`test_illegitimate_daemon_fails_after_one_reaccreditation_attempt`,
`test_reaccreditation_does_not_chain_into_a_third_auth_attempt`).

## Lo único que falta: requisito 5

Cuando aun así **no** se pueda recuperar, el error debe ser un **código estable y distinguible**
cuyo mensaje indique **explícitamente que hay que abrir una sesión nueva**.

Hoy `credential_source_untrusted` y `daemon_identity_unverified` no lo dicen, y el resultado
medido es que se diagnostican mal de forma sistemática: dos sesiones distintas (una Claude y una
Codex) los interpretaron como registro roto y estuvieron a punto de re-registrar el MCP y
reiniciar daemons, operaciones que tumban trabajo de otros agentes.

Condiciones:

- Sin secretos ni material de clave en el mensaje.
- Fail-closed se mantiene: esto cambia **qué se comunica**, no qué se permite.
- No degradar ningún fallo actual a warning ni hacerlo desaparecer del resumen.

## Método

TDD estricto. Primero un test que exija el código o mensaje nuevo y **falle por esa razón**;
después el cambio. Añade el negativo: los caminos que ya fallaban cerrado siguen fallando cerrado
y no adquieren el mensaje nuevo por accidente.

## Alcance permitido

- `tools\dayz_mcp\daemon_credential.py`
- `tools\dayz_mcp\control_client.py`
- `tools\dayz_mcp\server.py` — **sólo** si el código nuevo debe declararse ahí
- los tests de esos módulos

**Nada más.** No toques `doctor.py` (BUG-063 ya cerrado y verificado), ni lifecycle, ni cola, ni
runs, ni el bridge Enforce, ni ningún `.c`. No arranques DayZ, no adquieras lease, no reinicies
daemons.

## Salida

Ejecuta las tres suites focales y entrega los bloques **A / B / C / D** con la salida **real** de
unittest, no parafraseada.
