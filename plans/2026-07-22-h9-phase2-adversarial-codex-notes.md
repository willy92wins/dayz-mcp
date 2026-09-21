---
date: 2026-07-22
project: DayZ_MCP
topic: H9 phase 2 pre-implementation correction
status: approved-by-prior-user-authorization
source_plan: plans/2026-07-22-bug046-h9-native-launcher-plan.md
---

# H9 Fase 2 — corrección pre-implementación

El review adversarial independiente declara **NO-GO** sobre la secuencia original:
1 Critical y 6 High. No cambia el Intent ni la arquitectura H9; endurece los gates y
reordena el probe real para hacerlo ejecutable.

## Evidencia

- [EXACT] El auditor sólo comprueba módulo/función/primer call site y no los argumentos
  de `CreateProcessW`: `tools/dayz_mcp/security_runtime_audit.py:1468-1480`.
- [EXACT] Su fixture positiva acepta receptor nominal con argumentos `None` y flags `0`:
  `tools/tests/test_security_runtime_audit.py:1214-1227`.
- [EXACT] El prior art local ya usa `PROC_THREAD_ATTRIBUTE_JOB_LIST`,
  `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` y `EXTENDED_STARTUPINFO_PRESENT`:
  `tools/process-job-spike.py:30-42,576-625`.
- [EXACT] `_OpenedLauncher` mantiene el stream pinneado, pero no ofrece validación PE:
  `tools/dayz_mcp/launcher_registry.py:61-84,239-266`.
- [EXACT] El validador existente reabre por path y vive en un módulo con `subprocess`:
  `tools/install_mcp.py:12,132-152,446`.
- [EXACT] Cancelar un `asyncio.Task` no demuestra que haya terminado un futuro thread
  Win32; hoy la transacción puede llegar a release tras `CancelledError`:
  `tools/dayz_mcp/native_launcher_transaction.py:47-84,143-188`.
- [EXACT] La Fase 2 pedía un probe PE antes de producir el PE en Fase 3:
  `plans/2026-07-22-bug046-h9-native-launcher-plan.md:172-185`.

## Addendum vinculante

1. [DESIGN] Fase 2A empieza endureciendo auditor y runner. Debe rechazar call ausente,
   segundo call, receptor distinto de `_kernel32`, argumentos/flags de forma no canónica,
   lookup dinámico y parámetros libres. Una API Win32 falsa verifica además los diez
   valores efectivos; AST no se considera prueba suficiente.
2. [DESIGN] Extraer `dayz_mcp/native_pe.py`, puro/no-launching, que valide
   `MZ`, `PE\0\0`, AMD64 y PE32+ sobre el stream ya pinneado. No reabre el path ni importa
   `install_mcp`.
3. [DESIGN] El Job, su completion port y un `STARTUPINFOEXW` con dos atributos existen
   antes del único call site: `JOB_LIST` hace atómica la pertenencia al Job y
   `HANDLE_LIST` limita la herencia.
4. [DESIGN] El contrato exige `EXTENDED_STARTUPINFO_PRESENT`,
   `CREATE_UNICODE_ENVIRONMENT`, `CREATE_SUSPENDED`, `DEBUG_PROCESS`,
   `STARTF_USESTDHANDLES`, `bInheritHandles=TRUE`, cuatro handles child-side únicos e
   inheritable-duplicates y cero extremos parent/sentinel.
5. [DESIGN] Un reducer puro gobierna ownership de debug events y handles: exactamente un
   continue por evento; cierres exactos; breakpoint inicial una vez por PID; first chance
   no manejada; second chance/RIP/malformed fail-closed; fallo cierra Job antes del
   continue terminal y drena hasta EXIT/deadline.
6. [DESIGN] Un único thread dedicado ejecuta CreateProcess/Wait/Continue. Cancelar el
   awaitable sólo arma una señal thread-safe. El consumer no termina hasta cerrar Job,
   observar active-zero, drenar eventos y hacer `join`; release/status ocurren después.
7. [DESIGN] Fase 2A usa sólo fakes bajo runner deny-launch. El probe real se mueve a
   Fase 2B post-Fase 3, cuando exista PE/bundle sellado, fuera del runner.

## Viability gates

- [DESIGN] Auditor: receiver/args/flags/call-count positivos y negativos.
- [DESIGN] PE: fixtures MZ/offset/signature/machine/magic y stream pinneado único.
- [DESIGN] FFI fake: diez argumentos exactos, Job+HANDLE_LIST presentes antes del call,
  cleanup total en cada punto de error.
- [DESIGN] Reducer: matriz completa de eventos, un continue exacto y cero handles vivos.
- [DESIGN] Cancelación: `release` sólo después de signal→job close→active-zero→drain→join.
- [DESIGN] Gate Fase 2A: `attempts=[]`, `intercept_count=0`; ningún proceso real.

