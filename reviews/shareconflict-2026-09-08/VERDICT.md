# M2 - Veredicto sobre el conflicto de comparticion

**Explicacion parcial: el mecanismo esta demostrado; la atribucion al incidente historico no.** Un handle incompatible sobre cualquiera de las dos configuraciones provoca exactamente daemon_provenance_conflict con winerror=32, que termina en client_policy_untrusted_open_new_session, sin cambiar un solo byte ni enviar HTTP. Ahora esa causa tambien llega al texto de la respuesta MCP. No se ha probado que ese handle existiera durante 49b2/c261/050e ni que perteneciera a Claude/Codex.

La hipotesis deja de ser una posibilidad sin reproducir: hay una matriz nativa determinista, controles negativos y una prueba del consumidor. Queda descartada la **necesidad** de una apertura exclusiva: basta otro handle con acceso WRITE o DELETE aunque conceda compartir lectura, escritura y borrado. La mera existencia de temporales o un rename ya terminado no demuestra la causa del incidente.

Base: 4e34bda128fee203f0056fe5e2fa56ce0b941763. Fecha: 2026-09-08. Hashes iniciales en baseline.json; salida completa en gate.log; preimagen en control_client.py.before.

## 1. Que pide el lector y que permite

Fuente leida: tools/dayz_mcp/host_config.py:705, llamada en :710, constantes en :488 y :649.

| Parametro | Valor efectivo |
| --- | --- |
| dwDesiredAccess | GENERIC_READ = 0x80000000 |
| dwShareMode | FILE_SHARE_READ = 0x00000001 |
| lpSecurityAttributes | NULL |
| dwCreationDisposition | OPEN_EXISTING = 3 |
| dwFlagsAndAttributes | FILE_ATTRIBUTE_NORMAL OR FILE_FLAG_OPEN_REPARSE_POINT = 0x00200080 |
| hTemplateFile | NULL |

Microsoft establece compatibilidad en ambos sentidos: el acceso solicitado debe caber en la comparticion de los handles existentes, y la comparticion solicitada debe permitir los accesos ya abiertos. Los derechos sobre atributos tienen reglas distintas de los accesos a datos. El lector permite otros lectores que compartan READ; no permite accesos concurrentes WRITE o DELETE. DELETE incluye renombrado. Las restricciones duran hasta cerrar el handle, con independencia de que se trate del mismo proceso u otro. [CreateFileW, Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew), consultado el 2026-09-08.

OPEN_EXISTING no crea ni trunca. Se abre el propio reparse point y despues se rechaza si corresponde (:733-740). Los errores 2/3 de CreateFileW se traducen a ausencia; cualquier otro mantiene el rechazo y conserva el numero (:719-723). Microsoft identifica 32 como ERROR_SHARING_VIOLATION y distingue 33, ERROR_LOCK_VIOLATION, de los bloqueos sobre una region del fichero. [Codigos Windows](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--0-499-).

La siguiente tabla procede del experimento, no solamente de nuestras constantes. Se probo para .claude.json y config.toml sinteticos, con el otro proceso primero y con el pin primero: **24 combinaciones**.

| Otro handle | Share del otro | Otro abre primero: gate | Pin abre primero: otro proceso |
| --- | --- | --- | --- |
| READ | READ (1) | acepta | abre |
| READ | READ+WRITE+DELETE (7) | acepta | abre |
| READ | ninguno (0) | rechaza, 32 | no abre, 32 |
| WRITE | READ+WRITE+DELETE (7) | rechaza, 32 | no abre, 32 |
| READ+WRITE | READ+WRITE+DELETE (7) | rechaza, 32 | no abre, 32 |
| DELETE | READ+WRITE+DELETE (7) | rechaza, 32 | no abre, 32 |

## 2. Experimento y discriminadores

tools/tests/test_shareconflict_windows.py:28 contiene el llamador Win32 independiente del hijo; :64 sincroniza apertura/liberacion por pipes, sin depender de un sleep. No importa dayz_mcp en el hijo ni escribe el archivo que mantiene abierto. Sus constantes y firmas se contrastaron con Microsoft.

- :204 prueba loader y politica reales con el segundo proceso primero. Cada rechazo exige HostConfigError.winerror == 32, token estable, pre_request, cero bytes HTTP y proveedor de transporte sin llamadas.
- Tras cerrar el handle, una NUEVA peticion acepta. Contenido y tamano son identicos antes/despues. Cerrar el handle es la unica intervencion. No hay retry dentro del gate.
- :238 prueba el orden inverso: si el gate llega primero, es el otro proceso quien recibe 32. Un fallo del escritor tampoco demuestra un fallo del gate.
- :253 lleva el conflicto real hasta CallToolResult.isError=true y el texto MCP; :261 cambia la registracion real y exige una causa distinta, manteniendo el rechazo.
- :278 mantiene un temporal independiente abierto con WRITE exclusivo: no bloquea el destino. Despues se cierra y se renombra entre resoluciones: sigue aceptado.
- :295 verifica que mensajes con rutas/contenido sensible sintetico no aparecen en la respuesta.

Aislamiento declarado: el resolver se redirige SOLO a rutas temporales y delega en la funcion original; no se simulan sus resultados ni CreateFileW/GetLastError. El proveedor HTTP es un doble. Para publicar se sustituye SOLO el constructor del runtime, se heredan sus metodos reales y se ejecuta el handler MCP registrado en memoria (:164). No se inicia servidor, lifespan, listener ni cliente MCP externo; cualquier intento de autospawn falla en el test. El transporte real y el daemon vivo no se prueban.

RED anterior al cambio: Ran 6 tests in 3.928s; FAILED (failures=7); exit 1 (red.log). Son siete subcasos de publicacion: faltaba la causa en el texto; el conflicto nativo, las negativas y el rechazo ya se comportaban como se esperaba.

Final: 6 + 39 + 3 + 34 = **82 tests, 81 sin fallo y 1 omitido**, cuatro procesos unittest con exit 0. Se omitio crear un symlink por WinError 1314, falta de privilegio; no se presenta como ejecutado. T1/T2/T3 permanecen intactos y verdes, incluida registracion CAMBIADA -> RECHAZO.

## 3. Donde se perdia el dato y cambio aplicado

Correccion al brief: en esta base HostConfigError.winerror YA llega a _policy_revalidation_cause, tools/dayz_mcp/control_client.py:43 (:50 prioriza errno; :52 usa winerror si falta un errno valido). daemon_policy.py:319 instala un hook que deja propagar la excepcion del loader; daemon_policy_contract.py:132 no la aplana.

El punto perdido es posterior: server.py:1267 publica codigo/hint mediante str(error), no policy_cause. El handler de la dependencia instalada transforma la excepcion a respuesta de error en tools/.venv-mcp/Lib/site-packages/mcp/server/lowlevel/server.py:583.

Cambio unico en produccion: control_client.py:177 calcula una vez la misma causa segura, conserva el atributo separado (:182) y la coloca en el hint (:185). Diff completo: implementation.diff, 4 lineas anadidas / 1 retirada.

Fragmentos literales observados en gate.log:

~~~text
policy_cause=HostConfigError:32.
policy_cause=HostConfigError:daemon_provenance_conflict.
~~~

Ambos aparecen despues del token intacto client_policy_untrusted_open_new_session; ambos devuelven isError=true. El dato llega como **texto dentro de content**, no como nuevo campo hermano JSON. El atributo Python sigue disponible por separado. Se usa el contrato de hint existente porque es la superficie autorizada y ya publicada; no se edita server.py ni se cambia el token.

Ninguna regla de aceptacion se modifica: misma revalidacion, mismo catch, mismo codigo, mismo pre_request/0 bytes, sin nuevos retries ni tolerancia. host_config.py, parser, identidad, chequeos de reparse, consenso dual y tests T1/T2/T3 conservan sus hashes. La rama de retry del adaptador solo admite daemon_unavailable (server.py:1287), no este codigo; el test vigila que no se llama al autospawn.

**Un conflicto generico no demuestra una registracion cambiada.** Tambien engloba parseo, reparse, identidad y algunos errores nativos de metadata que no conservan winerror (:733, :696, :804 en host_config.py). Un PermissionError con errno y winerror simultaneos sigue priorizando errno, por compatibilidad con el comportamiento existente. El marcador 32 discrimina el fallo nativo conservado; para atribuir API, fichero y propietario hace falta la traza de abajo.

## 4. Temp+rename: que cambia en la premisa

1. Un temporal con otro nombre no basta: TEMP_ONLY lo mantiene abierto en exclusiva sin rechazo. Un rename completado entre resoluciones tampoco basta: RENAME_COMPLETED acepta. No se ha medido la ventana de una llamada rename en progreso; la matriz demuestra el conflicto de handles que puede hacerla incompatible.
2. Una API estandar puede pedir DELETE sobre el destino sin abrirlo en exclusiva. ReplaceFileW documenta READ+DELETE+SYNCHRONIZE y share READ+WRITE+DELETE para el archivo reemplazado, y una apertura distinta, sin compartir, para el reemplazante. La prueba DELETE/share=7 aisla la incompatibilidad pertinente; **no es una ejecucion de ReplaceFileW ni prueba que el CLI use esa API**. [ReplaceFileW, Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew), consultado el 2026-09-08.
3. Hallazgo en lectura estatica de C:\Users\guill\.local\bin\claude.exe, sin ejecutarlo: hay helpers sincronico y asincronico que cierran el temporal antes del rename y tienen una alternativa que abre el destino con modo "w" si falla la escritura atomica. Tambien se encontro el camino etiquetado saveConfigWithLock que llama a ese escritor, y un escritor de configuracion global sincronico. claude-writer-evidence.txt incluye hash, tamano, offsets y extractos del binario instalado (217771680 bytes).
4. Eso impide asumir, solo por observar .tmp, que toda escritura del host usa exclusivamente temp+rename. **No prueba** que esa alternativa se ejecutara en el incidente, ni sus flags Win32 subyacentes, ni que el proceso afectado cargara ese ejecutable. El getter minificado de ruta no se trazo hasta su definicion. Codex tampoco se ha atribuido por codigo: no estaba disponible como comando en el PATH de esta shell.

No se necesitan mas cambios al gate para observar el siguiente rechazo. No propongo implantar reintentos sobre esta evidencia: aun falta medir actor, duracion y API de la colision real.

## 5. Receta para decidir el siguiente caso en el sistema vivo

Receta para el receptor; NO ejecutada en esta lane por las restricciones del brief.

1. Usar un cliente que haya cargado este control_client.py mediante el flujo habitual del host. Registrar hora UTC y version/hash de ese cliente. No asumir que un proceso ya cargado use los bytes actuales del disco, ni reiniciar el daemon para comprobar un error local del cliente.
2. Preparar Process Monitor antes del episodio. Incluir Path begins with C:\Users\guill\.claude.json y Path begins with C:\Users\guill\.codex\; asi se ven tambien temporales. Mantener todos los procesos y resultados, incluidos SUCCESS; filtrar solamente al daemon o por SHARING VIOLATION ocultaria al escritor. No registrar el contenido de las configuraciones.
3. Ante un rechazo en el uso normal, guardar texto exacto y ventana de eventos. Buscar el CreateFile del proceso cliente sobre una de las dos rutas: Desired Access de lectura, ShareMode Read, resultado SHARING VIOLATION. Guardar PID, imagen, TID, hora, ruta, operacion, resultado, Desired Access, ShareMode y stack si esta disponible. El lector que acredita es el cliente; no se presupone el PID del daemon 8765.
4. Correlacionar con la apertura SUCCESS previa sobre el mismo archivo y el intervalo hasta Cleanup/CloseFile. Si el otro handle pide WRITE/DELETE o no comparte READ, la matriz explica el rechazo. Para atribuirlo al CLI, debe identificarse su PID/imagen; antivirus, editor u otro actor producirian el mismo 32. Un evento de rename del escritor o un temporal aislado no bastan.
5. Decision: marcador HostConfigError:32 mas ese CreateFile fallido confirma comparticion en ese rechazo; una captura que cubra el intervalo completo y muestre aperturas del lector compatibles/exitosas lo descarta como causa de apertura de esa peticion. Un marcador generico, sin version cargada o sin cobertura temporal, es INCONCLUSO. Para sostener un cambio real de autoridad hacen falta los campos acreditados relevantes en el instante, bajo manejo privado; no volcar la configuracion ni secretos.
6. Tras el cierre natural del handle, observar la siguiente peticion que ocurra en el uso normal. Una aceptacion sin cambios de autoridad apoya la colision transitoria. Rechazos que persisten sin handles incompatibles exigen otra causa; esta prueba no explica por si sola un bloqueo permanente.

Process Monitor documenta eventos de ficheros, detalle por proceso, filtros no destructivos y registro de parametros/stack: [Microsoft Sysinternals](https://learn.microsoft.com/en-us/sysinternals/downloads/procmon). La receta no cambia configuraciones, no mata procesos y no introduce sondeo automatico ni retries en el gate.

## 6. Entrega y limites de revision

Sin commit, git add, stash, build nativo, MCP vivo ni acceso a las dos configuraciones reales. Se usaron fixtures sinteticas, sin copiar secretos de los archivos del usuario. Cada escritura propia se releyo y comparo byte a byte y por tamano.

**Resellado:** host_config.py esta en PACKAGED_MODULES (tools/build_native_launcher.py:53, entrada :62), pero esta lane NO lo modifica; control_client.py NO esta en esa lista. M2 no introduce un cambio del bundle empaquetado. El estado de sellado del commit previo 4e34bda y de los procesos vivos queda sin verificar; no se construyo ni desplego nada.

Fuera de alcance: campo estructurado nuevo en el serializador, memoria vault/HANDOFF global/buzon, atribucion historica sin traza y revision independiente Claude/Grok. El receptor dispone de comandos, preimagen, diff y negativos para repetirlos y revisar la ausencia de vias de aceptacion.
