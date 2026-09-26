# BUG-046 — Revisión adversarial final R10

Fecha: 2026-07-22  
Alcance: núcleo de cola/lease, wait/cancel, arranque aislado, cleanup de workers y contrato de entorno de `dayz-test.ps1`.  
H9: excluido del veredicto del núcleo; el launcher productivo permanece deshabilitado.

## Primera pasada

Veredicto: `CORE BLOCKED`.

Hallazgo bloqueante: `cancel_operation()` rechazaba con
`operation_tombstones_saturated` antes de retirar una operación ya admitida cuando
los 128 tombstones estaban ocupados. La operación podía conservar ticket o lease
hasta su TTL. Evidencia de entrada:

- `tools/dayz_mcp/session_coordination.py:759-822`;
- `tools/dayz_mcp/server.py:502-529`;
- `tools/tests/test_session_acquire_wait.py:93-107` sólo cubría el fence de admisión.

## Corrección y regresión

- `tools/dayz_mcp/session_coordination.py:759-828` distingue la operación ya
  admitida del ID no visto: el cap sigue rechazando admisión nueva, pero no impide
  instalar el marker y limpiar el ticket/lease exacto.
- `tools/tests/test_session_acquire_wait.py:109-136` reproduce active + cap lleno,
  exige liberación y comprueba que una operación nueva sigue recibiendo 503.
- RED observado antes del cambio: `KeyError: 'cancelled'` porque la respuesta era el
  error de saturación. GREEN posterior: test exacto 1/1 y módulo 11/11.

## Revisión independiente posterior

El subagente revisor confirmó los hashes del delta, verificó la excepción acotada a
operaciones ya admitidas y no encontró otro bloqueo. Veredicto literal:

`CORE GREEN`

## Evidencia de cierre

- Focal BUG-046: 395 tests, `OK`, 1 skip previsto.
- Auditor de runtime sobre los bytes finales concurrentes: 33/33 `OK`.
- Gate multiproceso local: `status=PASS`, `fifo_grants=2`,
  `fifo_grants_without_live_wait=0`, `abandoned_grants=0`, `final_clean=true` y
  cero procesos fixture residuales.
- Suite global: 891 tests, 2 skips y exactamente 26 errores preexistentes de
  `test_port_reclaim.py`; cero fallos/errores nuevos.
- `session_status` real: `daemon_unavailable`; no se arrancó ni reinició el daemon.

## Freeze revisado

- `tools/dayz_mcp/session_coordination.py` —
  `19A409A3F77A7A274C05FA7C5843B744DCD37BCCE7E58AC4ECCA9D90AFDAF716`.
- `tools/tests/test_session_acquire_wait.py` —
  `3AC5D155E635986EDD7E4672F03C6C890B2EB2CA59DA9AC2F6932A390FD5B6C7`.
- `tools/_session_coordination/bug046_local_liveness_gate.py` —
  `E79423785F81BEFA0FAFE997A35B9DC3C21A58685E1F6C8C8EAB7D148A2C478F`.
- `C:\Users\guill\.claude\skills\dayz-test-ingame\templates\dayz-test.ps1` —
  `3AE0911F682F051C48FCA398D7E6CF2896D2ECDCBD6E068FB3D72125216D8912`.

## Residual separado

H9 sigue `❌`: `tools/dayz_mcp/secure_launcher.py:313-320` falla cerrado con
`native_launcher_not_configured` y `tools/approved-launchers.json` no registra
consumidores. Hace falta decidir entre PE nativo/neutral o restaurar el wrapper
PowerShell registrado; esta revisión no adjudica esa arquitectura.
