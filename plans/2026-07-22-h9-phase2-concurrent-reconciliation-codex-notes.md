---
status: approved-standing-authorization
owner: codex
date: 2026-07-22
topic: H9 phase 2 concurrent reconciliation
---

# H9 Fase 2 — reconciliación del backend concurrente

El snapshot concurrente no está listo para cerrar Fase 2A. El usuario autorizó ajustar el
plan cuando mejore resultado/esfuerzo/calidad; estas correcciones no cambian Intent H9 ni
formatos persistentes/de red.

## Evidencia verificada

- [EXACT] `secure_launcher` importa un módulo inexistente:
  `tools/dayz_mcp/secure_launcher.py:129-132` -> `dayz_mcp.native_bundle`.
- [EXACT] La llamada productiva no coincide con la firma real:
  `tools/dayz_mcp/secure_launcher.py:100-106` frente a
  `tools/dayz_mcp/native_launcher_backend.py:966-975`.
- [EXACT] El backend consume el atributo privado `_stream` de una capacidad duck-typed:
  `tools/dayz_mcp/native_launcher_backend.py:134-138,340-355`.
- [EXACT] La creación hereda cwd (`None`) y el call site aún no cierra el directorio
  privado exigido por H9:
  `tools/dayz_mcp/native_launcher_backend.py:442-460`.
- [EXACT] El reducer cierra `hProcess/hThread` al recibir CREATE_PROCESS y `hThread` al
  recibir CREATE_THREAD:
  `tools/dayz_mcp/native_launcher_backend.py:552-573`.
- [EXACT] Sólo se acredita el CREATE_PROCESS raíz; LOAD_DLL y descendientes llegan sin
  autorización y fuerzan fail-closed:
  `tools/dayz_mcp/native_launcher_backend.py:722-747,878-895`.
- [EXACT] `WaitForDebugEvent(FALSE)` se trata siempre como poll vacío y no diferencia
  `ERROR_SEM_TIMEOUT` de error real:
  `tools/dayz_mcp/native_launcher_backend.py:757-764`.
- [EXACT] el gate fake observado ejecutó 96 tests, 94 PASS, 2 FAIL, sin lanzamientos:
  `attempts=[]`, `intercept_count=0`. Ambos fallos proceden de la clausura del auditor.

## Corrección y viability gates

1. [DESIGN] Reconciliar primero el auditor sin false positives: `getattr` con nombre
   literal no relacionado con procesos es válido; un nombre dinámico sobre os/subprocess/
   ctypes/_winapi y cualquier terminal CreateProcess/ShellExecute/WinExec siguen bloqueados.
   Un import sólo de typing no debe convertir código no productivo en clausura ejecutable.
2. [DESIGN] Restaurar el boundary de registry: la validación PE se invoca mediante una
   operación limitada sobre el handle pinneado; el backend no recibe `_stream` público.
3. [DESIGN] Separar creación Win32 de supervisión. Antes de ampliar integración, el
   reducer puro debe conservar ownership de process/thread handles hasta EXIT, contabilizar
   todo debuggee observado y garantizar un Continue exacto por evento.
4. [DESIGN] Introducir una capacidad de closure/bundle sellada antes de aprobar DLL o child.
   Sin esa capacidad, el backend permanece deliberadamente no productivo.
5. [DESIGN] Crear un directorio privado con lifetime unido a la transacción y pasarlo como
   `lpCurrentDirectory`; nunca heredar cwd.
6. [DESIGN] Sólo después se implementan pipes/handles y se alinea la firma estricta
   `secure_launcher -> backend`. El fake no puede aceptar `**kwargs`.
7. [DESIGN] Gate Fase 2A: suites fake explícitas bajo `p0s_test_runner`, 0 fallos,
   `attempts=[]`, `intercept_count=0`. El probe PE real permanece post-Fase 3.

## Criterio de parada

No se declara ruta productiva ni Fase 2 cerrada mientras falten `native_bundle`,
acreditación DLL/child, cwd privado, firma estricta o los gates Win32 fake anteriores.
