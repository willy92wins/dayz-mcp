# Consola del MCP y Steam — diagnóstico y propuesta

Fecha: 2026-09-08. Estado: **propuesta, sin integrar ni desplegar**. Encargo: handoff completo de LFSecure y, por separado, investigar el fallo tras cerrar la shell del MCP y abrirla desde el siguiente agente. No modifica el contrato ni consume una ronda de revisión de LFSecure.

## Resultado

Hay un defecto reproducible en la creación de la consola del daemon: los flags de desacoplamiento se aplican al lanzador del entorno virtual de Python, pero su intérprete hijo obtiene una consola. La variante con `CREATE_NO_WINDOW` evita esa ventana en el mismo entorno real. **Este fix corrige la ventana inesperada; no está demostrado que resuelva el fallo de Steam.**

El fallo de Steam se ha reproducido antes de cargar scripts, con registro válido y también sin LFSecure V2. No hay evidencia suficiente para atribuirlo a PATH, al producto V2 o a cerrar la consola. No se ha cerrado el host Windows Terminal compartido para provocar una prueba destructiva sobre pestañas ajenas.

Evidencia durable: [dossier](../evidence/20260908-steam-console-lifecycle/). Fuente Python congelada bajo `source/dayz_mcp`; las sondas, sus salidas y el manifiesto están en el mismo dossier. Investigación inicial en [memoria](C:/Users/guill/ObsidianVault/AI/10_Projects/DayZ_MCP/research/2026-09-08-steam-console-lifecycle-codex.md).

## Descubrimiento antes de implementación

Se leyó el camino de lanzamiento, cierre, recuperación, comprobación de Steam y las diferencias de entorno antes de generar el candidato. Base Git `91eeccad11b597d73a92efb1b7f02957782ae8b1`, rama `work/inbox-20260830-modules`. El daemon vivo tenía versiones anteriores de tres módulos; se distingue de la fuente actual y no se usa esa diferencia como causa automática.

No se ha editado la fuente productiva. El archivo staged `decisions/decision-log.md` pertenece a otra sesión y queda intacto. No existe `P:/DayZ_MCP_dev/bugs.md`; se registra el hallazgo en este informe y en memoria, sin crear otro índice de bugs ni cambiar el histórico por iniciativa.

## Mecanismo de la consola

- `tools/dayz_mcp/daemon.py:1636–1660`: el lanzamiento usa `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`, primero con breakaway y después sin él si falla. `:978–983` copia el entorno del cliente.
- El ejecutable del venv es el redirector Python 3.14.3. El código oficial [v3.14.3, PC/venvlauncher.c:408–460](https://github.com/python/cpython/blob/v3.14.3/PC/venvlauncher.c#L408) crea su job, lanza el intérprete con flags cero y espera su salida. No afirmar que un PID wrapper vivo equivale a daemon operativo, ni que el wrapper sobrevive indefinidamente al hijo.
- Prueba con el venv real: originales → wrapper e intérprete separados, consola del intérprete y ventana visible; `CREATE_NO_WINDOW` → HWND nulo, ambos procesos vivos y heartbeat avanzando. Control adicional con Python base y flags originales → sin consola. Evidencia: `console-spawn-results.json` y `console-redirector-v3143-findings.md`.
- El intento de cierre aislado quedó **INCONCLUSO**: el HWND del intérprete era una pseudoconsola; la ventana visible pertenecía a Windows Terminal PID46580, anterior al ensayo. Los guardas rechazaron el cierre. Todos los hijos de prueba salieron por su señal propia, código0.

## Qué se ha comprobado de Steam

| Ensayo | Resultado observado | Límite |
|---|---|---|
| Steam vivo PID47816, registro PID0/usuario0 | La API del juego inicializa; la comprobación antigua de Steam en ejecución devuelve falso | Inicialización de una sonda no demuestra aceptación del cliente DayZ |
| Mismo ensayo con entornos completos del daemon, Explorer y Steam | Los tres dan el mismo resultado | Compara el entorno de un hijo de prueba; no equivale a lanzar el juego desde cada proceso padre |
| Arranque gestionado sin remediación | `steam_session_stale`, run_id nulo, sin lanzar DayZ | El rechazo corresponde al contrato actual del preflight |
| Un reinicio gestionado con remediación explícita | Nuevo Steam PID53412; registro pasa 0 → PID válido → usuario válido | No se reinició Steam entre los cuatro clientes siguientes |
| Cliente V2, sin filePatching, a las01:53:32 | Diálogo Steam, RPT733B, minidump con el literal exacto | No carga de scripts |
| Repetir mismo cliente a las01:54:17 | Mismo fallo | La espera adicional no resuelve este intento |
| Activar filePatching, mismo V2,01:54:54 | Mismo fallo, RPT747B | No resuelve cambiar ese parámetro |
| Retirar V2 del cliente, manteniendo filePatching,01:55:14 | Mismo fallo, RPT723B | El fallo ocurre también sin V2; el servidor mantuvo su stack |
| Sonda tras reinicio, registro válido | Init y comprobación de Steam verdaderos; también antes/después de Init en sonda posterior | No contradice que otro chequeo interno o contexto del juego falle |
| Cierre gestionado del run | Steam PID53412 y registro permanecen válidos | No reproduce un cierre por X de la pestaña |

Las tres primeras pruebas de entorno quedaron en la salida de la herramienta; el script sobrescribió después sus archivos. Se conserva una transcripción explícitamente identificada en `api-pre-remediation-observation.json`, además de `steam-api-before-close.json` original. No presentar esa transcripción como un archivo de medición original intacto.

Fuentes de API: [SteamAPI_Init/Shutdown](https://partner.steamgames.com/doc/api/steam_api?l=english) y [SteamAPI_IsSteamRunning en el header de Valve](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/public/steam/steam_api.h), consultadas el08-septiembre. La sonda usa la DLL instalada junto a DayZ, no una implementación simulada.

Los minidumps anteriores y los cuatro nuevos contienen el mismo mensaje. Las DLL de Steam tienen iguales rutas, versiones y tamaños en los tres dumps iniciales; no se ha demostrado que difieran por PATH. Esto tampoco acredita igualdad histórica de bytes de todas las DLL. El proceso cliente que fallaba tenía cwd correcto, `SteamAppId=221100` y `SteamGameId=221100`; no un AppID0 observado en esos campos. PID del registro, ID de usuario y AppID del juego son conceptos distintos.

El estado `succeeded` de la herramienta acreditó creación de procesos, con `bridge_ready=false`; **no acreditó un cliente jugable**. El error mantuvo el cliente en el diálogo: no se etiqueta como crash inmediato solo por encontrar un minidump.

## Código de cierre y lanzamiento leído

- `process_lifecycle.py:1376–1380,2546`: DayZ se lanza con cwd explícito y entorno heredado del daemon. No hay cambio de entorno en ese camino.
- `native_process_guard.py:194–217`: verifica la identidad y termina el proceso concreto. El cierre/reemplazo en `process_lifecycle.py:2880–2882,3050–3084` no mata indiscriminadamente el árbol ni invoca Steam. El reaper `:3706–3782` retira registros de procesos muertos.
- `steam_preflight.py:211–247`: exige dos lecturas coherentes, PID positivo, usuario no cero y proceso Steam correspondiente. `:276–281` arranca Steam con entorno/cwd heredados. La remediación solo se activa por opt-in en `dayz_test_tool.py:1389–1403`.
- `native_process_guard.py:79–89`: el campo `executable_sha256` del lifecycle hashea **la ruta normalizada con prefijo**, no el contenido del PE. No usarlo como huella de build. SHA256 de los bytes actuales de DayZDiag: `34f6377be4fd065d104e67263e0c96ac2cb2e348119a4838eba08d2e61b7a69a`, tamaño20245560, mtime15-agosto04:37:25. La referencia inicial a da6814… como hash del binario se corrigió durante la investigación.

## Fix propuesto: solo ventana del daemon

**[EXACT — candidato contrastado con la preimagen, no desplegado]** El [diff mínimo](../evidence/20260908-steam-console-lifecycle/patch-daemon-no-window.diff) sustituye `DETACHED_PROCESS` por `CREATE_NO_WINDOW` en `daemon.py:62–65,1636`, manteniendo grupo nuevo, stdio cerrado, entorno, cwd y las dos ramas de breakaway. Actualiza solo las expectativas y comentarios afectados de `test_daemon_spawn_branch.py:146–157`.

Suite focal: **35/35 PASS**, sin skips, Python3.14.3; paquete y tests importados desde la copia. Se compararon64 archivos del paquete y solo cambió `daemon.py`, además del test. Originales productivos intactos. Evidencia: `patch-test-results.json`, `patch-import-proof.json` y `patch-verification.json`. SHA256 diff `27f32319e742aa9121f6eb565f149cd6f8bac35e6415fa729298474a77831288`. La prueba focal de fallback usa mocks; la evidencia de ventana procede del ensayo con venv real, y no prueba la supervivencia frente a todos los jobs.

No combinar ambos flags: [Windows ignora CREATE_NO_WINDOW cuando se combina con DETACHED_PROCESS](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags). No cambiar autenticación, leases, reaper, protocolos, formatos persistentes ni mecanismos de reinicio de Steam en este parche.

No se propone quitar el preflight porque Init pueda funcionar con PID0: DayZ puede exigir más comprobaciones que Init. Tampoco se propone escribir a mano el registro ni “limpiar PATH” sin aislar el efecto en el cliente real.

### Criterios verificables

1. Control negativo con venv real3.14.3: flags anteriores producen ventana; candidato no produce HWND y el heartbeat progresa en ambos PID. No sustituir por un mock que solo compruebe bits.
2. Test focal existente: spawn correcto, fallback ante breakaway denegado y fallo de ambos lanzamientos conservan sus resultados; los flags nuevos no contienen DETACHED_PROCESS. Mantener comportamiento POSIX.
3. Cierre por pestaña: con host exclusivo e identidad acreditada, cerrar la ventana del control termina su daemon simulado; el candidato no ofrece esa ventana. Si el host es compartido o falta identidad, resultado INCONCLUSO. No exigir cerrar la ventana global de Windows Terminal.
4. Supervivencia al fin de un agente y rama real de job que deniega breakaway: comprobar aparte; el test de HWND no demuestra supervivencia frente a todos los jobs.
5. Steam: un resultado válido exige un cliente fresco que llegue a misión/menú según modo, sin diálogo ni minidump nuevo. PID vivo, registro válido y sonda API no bastan. Este criterio sigue sin cumplirse.

### Integración y rollback

El candidato está aislado y no se despliega durante esta investigación. Antes de integrarlo, verificar que el source hash sigue siendo el de su preimagen y que la caja no tiene consumidores activos; un cambio de fuente MCP puede invalidar clientes existentes. Revertir este parche restaura flags y expectativas anteriores; no hay migración ni datos nuevos en disco que interpretar. Los artefactos de diagnóstico son independientes.

## Experimento pendiente para la causa de Steam

Se leyó también el camino nativo en el PE del juego: [análisis con bytes e imports](../evidence/20260908-steam-console-lifecycle/dayz-native-steam-condition.md). En el hash de bytes identificado, la rutina RVA0x46cf70 devuelve false por fallo de Init/reintento o por `IsSteamRunning=false` después de Init; el wrapper RVA0x46e170 puede devolver false antes si una bandera global es cero. Hay dos variantes y lugares del mensaje. Las llamadas y ramas están en `dayz-steam-disasm-callees.txt:74–102,138–176,178–227`.

Esto explica por qué Init=true no basta, pero **no identifica cuál de esas ramas tomó el cliente real** cuando la sonda externa daba true/true. El siguiente discriminador técnico es observar esos retornos y la bandera dentro del mismo cliente en el instante del fallo. No hay parche ni instrumentación aplicada al juego.

El siguiente experimento debe partir de **un cliente que funciona**, no de un entorno que ya falla. Capturar antes/después de cerrar la pestaña exacta señalada por Guillermo, conservando identidad de Steam/daemon, registro y contexto de lanzamiento. A continuación abrir un cliente MCP nuevo y repetir exactamente el arranque. Un solo ciclo A/B y una confirmación como máximo; si no discrimina, conservar INCONCLUSO y llevar una traza de escrituras de registro/procesos al punto de transición, sin otra cadena de reinicios especulativos.

Falta confirmar si la pestaña que cierra es la del Python del MCP o la consola de servidor DayZ. La pregunta quedó pendiente; no se ha supuesto que ambas sean la misma ventana. No se ha probado causalmente la cadena cierre → PID0 → fallo al siguiente agente.

## Cierre de la sesión

Run propio `fb85f1cf-80f2-4d7a-80f6-5fa0e058d72a` detenido por `dayz_test_stop`: succeeded, cleanup_degraded=false. Steam quedó vivo con registro válido en la lectura posterior. El estado final de coordinación se conserva en el dossier al cerrar. No commit ni deploy de código productivo.

LFSecure tiene [handoff completo](P:/LFSecure_dev/HANDOFF-V2.md) y backup durable `P:/LFSecure_dev/_backups/v2-r1-20260908`; V1 y candidato V2 conservados. Su aceptación en juego sigue pendiente y no se mezcla con este diagnóstico.
