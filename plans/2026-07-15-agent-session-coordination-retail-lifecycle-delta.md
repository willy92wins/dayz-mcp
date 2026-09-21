# Delta de diseño D-16 — lifecycle retail fail-closed

**Fecha:** 2026-07-15  
**Estado:** aprobado para ejecución bajo la autorización autónoma del usuario de 2026-07-15  
**Aplica a:** `plans/2026-07-14-agent-session-coordination-design.md` §8 y §13.5/§13.7  
**No cambia:** D-14, H1-H4, H7 ni la intención de H5/H6/H8; H3 recibe una precondición fail-closed y H5 una excepción de cleanup segura

## 1. Falsación que obliga al delta

- El plan exigía identidad completa para wrapper e hijo retail y bloqueaba Task 7 ante cualquier campo ausente: `plans/2026-07-14-agent-session-coordination-implementation.md:570-576,658`.
- En dos ejecuciones reales, `DayZ_x64.exe` expuso creation time pero no command line mediante CIM/.NET: `.superpowers/sdd/task-6-report.md:84-98`.
- El probe Win32 posterior sí obtuvo path + FILETIME mediante `PROCESS_QUERY_LIMITED_INFORMATION`, pero no recuperó command line: `tools/_session_coordination/process-native-query-probe.ps1:17-18,42-60,91-143`; evidencia saneada: `tools/_session_coordination/process-native-query-probe.json`.
- El daemon real que escucha `127.0.0.1:8765` está dentro de un Job Object del host. El spike seguro se negó con `REFUSED_PARENT_ALREADY_IN_JOB`; evidencia saneada: `tools/_session_coordination/process-job-spike-preflight.json`. Un Job Object exclusivo retail no está disponible en la topología de producción actual sin rediseñar también el spawn de D-14.

## 2. Alternativas evaluadas

### A — retail manual-only

El lifecycle automático admite únicamente procesos cuya identidad completa puede registrarse y revalidarse. Ningún launcher oficial abre retail. Si aparece `DayZ_BE.exe` o `DayZ_x64.exe` por una ruta externa, se reconoce solo como cuarentena `RETAIL_MANUAL_CLOSE_REQUIRED`: lecturas puras y operaciones de coordinación pueden continuar, pero mutaciones de juego y lifecycle quedan bloqueados hasta un rescan cero. La observación nunca concede ownership.

### B — PID + FILETIME + path sin contenedor

Rechazada. Identifica con fuerza el objeto reabierto, pero no conserva una prueba durable suficiente de provenance del árbol BattlEye tras desaparecer el wrapper intermedio.

### C — Job Object exclusivo por run

Es la dirección completa futura: asignación atómica con `PROC_THREAD_ATTRIBUTE_JOB_LIST`, descendientes contenidos y cierre normal member-scoped. No entra en esta fase porque el daemon real está job-bound y el gate BattlEye todavía no está verde en esa topología.

## 3. Decisión

Se adopta **A** para la fase actual.

1. `[EXACT]` La allowlist inicial de `lifecycle/start` contiene solo la ruta canónica resuelta de `DayZDiag_x64.exe` bajo `DAYZ_GAME_PATH`; `DayZServer_x64.exe` queda deshabilitado hasta superar su probe real.
2. `[EXACT]` Solo las rutas retail canónicas instaladas bajo `DAYZ_GAME_PATH` devuelven `retail_manual_lifecycle_required`; cualquier copia/otra ruta no allowlisted devuelve `executable_not_allowed`. Ambos rechazos se auditan fail-closed antes de crear proceso o run.
3. `[EXACT]` El guard solo termina registros con PID, creation time, executable fingerprint y command-line fingerprint completos y coincidentes.
4. `[EXACT]` Un snapshot read-only por nombre puede imponer cuarentena conservadora, pero nunca concede ownership ni habilita una acción. Error del snapshot equivale a cuarentena activa.
5. `[EXACT]` Con cuarentena activa, lecturas puras, heartbeat, status y release siguen disponibles; mutaciones de juego, cleanup `vehicle_release` y lifecycle se rechazan antes de encolar o actuar.
6. `[EXACT]` Ningún launcher oficial abre retail. Si una ruta externa lo abrió, el usuario/sesión propietaria debe cerrarlo por UI y confirmar rescan cero. Si no puede, declara `manual_cleanup_required`; el lease no convierte el residuo en propiedad del siguiente agente.
7. `[EXACT]` Ninguna ruta oficial usa nombre, mod, PID solo, `Stop-Process`, tree-kill ni force-release para compensar la limitación.
8. `[DESIGN]` El Job Object atómico queda como gate futuro separado. No es una dependencia oculta de Task 7 ni autoriza una implementación parcial.

## 4. Efecto sobre H6 y H8

H6 conserva su intención: el lifecycle opera solo sobre runs registrados con identidad completa; identidad incompleta queda intacta. El delta reduce la allowlist antes del launch para impedir crear deliberadamente un run que luego no pueda detenerse con el contrato aprobado.

H3 conserva que toda mutación requiere lease, pero el lease deja de ser condición suficiente mientras exista retail no registrado. La cuarentena es una precondición adicional fail-closed que evita transferir de hecho una sesión externa cuando avanza FIFO.

H5 conserva cleanup owner-scoped, pero `vehicle_release` solo se intenta sin cuarentena. Con retail presente o snapshot desconocido, release/expiry cancela pendientes, omite esa mutación y audita `cleanup_degraded=retail_quarantine`.

H8 prueba adopción/reemplazo automático sobre un run `DayZDiag_x64.exe` registrado. Añade un negativo retail real: la solicitud se rechaza antes del launch y el scan final permanece limpio. No se presenta ese negativo como soporte automático retail.

## 5. Criterios verificables

1. `start(DayZ_BE.exe)` y `start(DayZ_x64.exe)` devuelven `retail_manual_lifecycle_required`; ruta renombrada/no canónica devuelve `executable_not_allowed`; launcher/guard no reciben llamadas y no aparece run.
2. Dos procesos desechables con el mismo label: detener el registrado deja vivo el no registrado.
3. PID reutilizado, creation time o fingerprint alterado: cero terminaciones.
4. Cada rechazo pre-launch emite `lifecycle_start_rejected` sin argv/token; si el audit append falla, devuelve `audit_failed` y mantiene cero launch/manifiesto.
5. Presencia retail o fallo del snapshot: una lectura pura pasa; una mutación/lifecycle devuelve `retail_quarantine`; no se encola comando ni se llama al guard.
6. Release/expiry pasa un run compatible a `RUNNING_IDLE`; con cuarentena no encola `vehicle_release` y audita cleanup degradado.
7. Doctor con retail presente devuelve `RETAIL_MANUAL_CLOSE_REQUIRED`, incluye PIDs solo como diagnóstico y no ofrece acción automática; `--require-clean` lo convierte en FAIL/código no cero.
8. Protocolo/handoff exige cierre UI + rescan; cualquier residuo mantiene el cierre degradado.
9. Gate H8: adopción/reemplazo positivo con Diag y rechazo retail negativo, ambos con JSONL y estado final limpio.

## 6. Riesgo residual aceptado

Retail puede seguir existiendo por una ruta externa, pero queda fuera de la automatización MCP: su presencia pone la caja en cuarentena de mutaciones hasta cierre y rescan. No se compensa ampliando privilegios, creando un servicio Windows ni relajando la prueba de ownership.
