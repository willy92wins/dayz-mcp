# R1 - Callback de resultados (fb-20260908-202130-2b16)

Implementado en el **servidor**: los resultados exitosos reutilizan un pool de callbacks con identidad exclusiva durante cada POST. El cliente queda sin modificar porque carece del limite de admision necesario para garantizar un pool total finito sin cambiar su flujo. No se declara resuelta la retencion nativa; el cambio elimina la asignacion por resultado exitoso despues de alcanzar el pico de concurrencia.

Validacion: **428 tests / 36 modulos, exit 0**, ejecutados en serie y por nombre. Contra los tres `.BEFORE`: **13 tests, 12 fallos, exit 1**. Linter general: **FAIL, 2 errores y 5 avisos**, identicos antes/despues salvo desplazamientos de linea. No hay compilacion ni prueba in-game en esta entrega.

## Cuantos POST pueden coexistir

Aqui ?en vuelo? significa entregados a `RestContext.POST` sin que el bridge haya liberado su identidad. No significa necesariamente conexiones TCP simultaneas: la implementacion C++ y su cola no estan disponibles. Incluso si el motor serializara los sockets, varios POST ya entregados necesitan conservar callbacks distintos hasta sus eventos.

**Servidor: puede acumular varios, hasta 128 identidades de resultados contabilizadas bajo el productor y la admision actuales.** No hay un gate de uno en vuelo para resultados.

- El productor limita la cola de cada peer a 64: `tools/dayz_mcp/loopback.py:156`, rechazo de enqueue en `:1829`. El poll toma un snapshot en `:2253`, devuelve los comandos en `:2349` y los retira de la cola en `:2350`; no espera a los ACK de esos resultados para admitir comandos posteriores.
- `addon/scripts/5_Mission/MCPBridge.c:241` admite un poll solo si `callbacks + pending + jobs <= MAX_CALLBACK_REFS - MAX_POLL_RESULTS`, es decir, <=64. Las constantes son 128 y 64 (`:6`, `:8`). No solo cuenta POST activos: reserva tambien los resultados que aun deben los jobs y la cola.
- El callback GET libera su referencia antes de despachar la respuesta (`addon/scripts/5_Mission/MCPCallbacks.c:20`, `:25`). El servidor despacha hasta cuatro comandos directamente por lote (`MCPBridge.c:319`) y drena hasta cuatro por tick (`:339`). Los demas entran en cola; el exceso devuelve un resultado de error (`:362`). Por eso cuatro NO es el limite de POST pendientes.
- El tick procesa jobs y drena pendientes antes de decidir si puede sondear otra vez (`MCPBridge.c:122`, `:123`). Un comando inmediato publica su resultado (`:577`); un job listo o vencido publica antes de quitarse del map (`:2874`, `:2877`, `:2895`, `:2898`). Cada transicion convierte trabajo ya aceptado en un POST, no introduce otro comando.
- La cota es: trabajo anterior <=64 + lote nuevo <=64 = <=128 resultados. El GET puede ocupar transitoriamente una referencia, pero sale antes de generar los POST de su lote. Mientras se solapan GET y trabajo previo, este ultimo mantiene su deuda al pasar de job/pendiente a POST. Un ACK libera solo su identidad (`MCPBridge.c:3544`). No existe espera por ACK en `PostResult` (`:3467`, `:3495`).

Esta es una cota de las identidades que el script considera activas, no de objetos C++ o reintentos nativos de peticiones ya retiradas. Presupone el productor actual, una respuesta terminal por comando normal y que los eventos nativos exitosos son terminales. Los errores pueden retirar identidades aun retenidas por el motor.

**Cliente: N POST pendientes, sin cota total impuesta por el bridge.** `MCPClientBridge.c:419` inicia polls sin consultar el numero de resultados; `:296` solo espera al GET y `:313` solo limita la cola de comandos. `:511` despacha cuatro comandos de la respuesta; `:651` drena otros pendientes y `:4050` asigna un callback nuevo por POST. Si cuatro comandos inmediatos producen cuatro POST cuyos eventos no llegan, y el siguiente GET si termina, cada nuevo poll puede sumar otros cuatro: despues de k polls hay 4k identidades. MAX_PENDING=16 (`:143`) no limita los resultados ya posteados. Tampoco MAX_QUEUE=64 del daemon limita ese acumulado: la cola se libera al entregar el poll, antes del POST.

El ACK HTTP actual contiene `ok` y opcionalmente `discarded`, sin identificador de resultado (`loopback.py:3601`); los callbacks tampoco reciben id en error/timeout (`../scripts/3_game/http/restapi.c:55`, `:64`). El payload de cada POST ya lleva su propio id; el callback no lo transporta de vuelta al script.

## Por que el patron del poll no se copia literalmente

`RestContext.POST(RestCallback cb, string request, string data)` es asincrono (`../scripts/3_game/http/restapi.c:123`). La clase `RestCallback : Managed` y sus eventos estan en `:50`, `:55`, `:64`, `:73`. La advertencia de `:53` permite varias llamadas a OnError por reintento. Un callback compartido con contador o un pool que reciclara en el primer error podria descontar la deuda de un POST posterior cuando llegue un error antiguo.

El poll actual cambia de identidad en error/timeout (`MCPBridge.c:374`, `:381`; cliente `:557`, `:564`) y comprueba la identidad en cada evento. Se conserva entero. Para resultados se reutilizan solo identidades que ya terminaron con exito, como en el caso exitoso del poll; una identidad fallida nunca se vincula a un nuevo POST.

## Cambio aplicado [EXACT]

- `MCPBridge.c:60`, `:86`: array fuerte de callbacks libres, creado una vez. Los activos permanecen en el array existente `m_CallbackRefs`, que sigue alimentando la admision.
- `MCPBridge.c:3504`: Acquire extrae el ultimo elemento libre antes de re-vincularlo y devolverlo; si no hay libres, crea uno. Insertarlo en el array activo y llamar a POST sigue ocurriendo inmediatamente en `:3488` y `:3495`.
- `MCPBridge.c:3521`: Recycle conserva hasta MAX_CALLBACK_REFS identidades libres. Bajo la admision demostrada, activos + libres quedan limitados por el pico de concurrencia, <=128. No se agrega un limite que descarte resultados aceptados ni una cola de POST. La cota del total reutilizable depende de esa admision; la cota explicita de la free-list es 128.
- `MCPCallbacks.c:66`, `:71`: AttachBridge y DetachBridge estan declarados en MCPResultCallback. `:76` libera la identidad que completo, registra el ACK existente, recicla y desvincula. `:87` y `:98` liberan y desvinculan sin reciclar. Errores repetidos o eventos posteriores sobre esa identidad retirada no actuan sobre otro POST.
- `MCPBridge.c:3558`: Shutdown desvincula los resultados activos y limpia el pool antes de resetear el contexto. No cambia el arreglo del poll, los guards ni el cierre del cliente.

**Orden y latencia:** no se agregan esperas, reintentos, cola ni serializacion. Se conserva el orden de invocacion de POST y el body/request de cada llamada; el orden de llegada/ACK sigue dependiendo del motor y de HTTP. En el camino caliente hay operaciones O(1) sobre el ultimo elemento del pool y una asignacion de backlink; no se ha medido su tiempo real. No se anaden logs por resultado. El coste de Shutdown agrega un recorrido de identidades activas.

**Errores:** tras error/timeout una nueva solicitud puede crear otro callback, igual que el poll. Por tanto no hay una cota del numero historico de objetos nativos retenidos si fallan solicitudes repetidamente. En trafico exitoso y concurrencia estable las asignaciones dejan de crecer. Esto es mitigacion de la asignacion 1:1 por exito, no prueba de liberacion nativa o de memoria total acotada.

**Cliente pendiente:** el alcance positivo 2 permite corregir solo el lado sustentado por el diagnostico. Un pool de identidades exclusivas con limite total cliente exige backpressure/admision o espera; cortar al llegar al limite perderia resultados. FUERA DE MI ALCANCE: introducir ese cambio de despacho. Un receptor de ACK completamente sin estado compartido entre todos los POST cliente es otra posibilidad, pero implicaria retirar su bookkeeping actual y verificar reutilizacion simultanea en el nativo; no se ha demostrado aqui. No se afirma imposibilidad universal de arreglar el cliente.

## APIs y declaraciones verificadas

| Uso | Definicion leida | Aplicacion |
| --- | --- | --- |
| POST / eventos / reset | `../scripts/3_game/http/restapi.c:123`, `:55`, `:64`, `:73`, `:133` | La misma API; reset cancela pendientes. OnError puede repetirse. |
| Count / Clear / Find / Get / Insert / Remove | `../scripts/1_core/proto/enscript.c:380`, `:385`, `:394`, `:399`, `:407`, `:463` | Remove intercambia con el ultimo; extraer precisamente el ultimo evita dependencia de orden. |
| Cast | `../scripts/1_core/proto/enscript.c:94` | Cast puede devolver null; se comprueba antes de DetachBridge. Patron ya usado en cliente `MCPClientBridge.c:4200`. |
| Backlink de servidor | `MCPCallbacks.c:59`, `MCPBridge.c:1` | Se conserva el enlace debil al bridge Managed; no se cambia su tipo. |
| Backlink y padre del cliente | `MCPClientBridge.c:64`, `:140`; `MCPJobRunner.c:1` | Se conservan ref y DetachBridge existentes; no se asume Managed en ese padre. |
| Campo/metodos nuevos | `MCPBridge.c:60`, `:86`, `:3504`, `:3521`; `MCPCallbacks.c:66`, `:71` | Declarados y usados en las clases correctas; gate nuevo tambien comprueba sus miembros. |

No hay datos nuevos sincronizados: el id/body ya circulan por HTTP; cache y backlinks son estado local del servidor. No se modifican MCPMessages, MCP_BRIDGE_VERSION, persistencia, RPC ni protocolo.

## Evidencia de pruebas

`test_resultleak_pool.py` traduce un subconjunto limitado de los metodos reales de las fuentes a Python, incluidos PostResult, Acquire/Recycle, eventos y Shutdown; el .BEFORE se ejecuta por la misma via. REST, arrays y serializacion son dobles. La traduccion omite el texto de Log y no implementa ARC, C++, scheduler ni compilacion Enforce. El oraculo independiente exige payload/id y orden intactos, identidades activas distintas, asignaciones iguales al pico en exitos, y aislamiento de identidades retiradas. Tres mutaciones negativas quitan pop, rebind o Release y comprueban el fallo relevante.

Casos: 2.000 exitos consecutivos (BEFORE asigna 2.000; parche 1), ocho POST en vuelo con ACK fuera de orden y reutilizacion del unico completado, dos tandas completas de 128 (BEFORE 256 asignaciones; parche 128), timeout/error repetidos, duplicados antes de reutilizar, fallo de serializacion y Shutdown con cancelaciones sincr?nicas. Una duplicacion nativa de OnSuccess DESPUES de re-vincular la misma identidad no es distinguible con esta API; se asume terminalidad de OnSuccess, como en el poll.

- `red.log`: `Ran 13 tests in 0.129s`, `FAILED (failures=12)`, `EXIT CODE: 1`.
- `green.log`: 36 invocaciones por modulo, 428 tests, todas `OK`, `EXIT CODE: 0`. Incluye los 13 nuevos y los 415 existentes. Nombres y comandos completos en STATE.md / test-results.json. No se ejecuto discovery ni la suite completa.
- `preservation.log`: tres copias con hash verificado; cliente y MCPPollCallback identicos por bytes; 14 metodos servidor conservados; PostResult solo cambia la adquisicion del callback.
- `lint.log` y `lint-before.log`: ambos `FAIL - 2 errors, 5 warnings`, exit 1. Se sustituyo solo la lectura de los tres .c por las copias en memoria, sin revertir el arbol, y se compararon todos los findings normalizando desplazamientos de linea. Los JSON completos se conservan.

Errores del linter: ES-LAYOUT-PATH-PBOPREFIX-MISMATCH y ES-LAYOUT-FILE-MISSING, ambos en MCPDialogController.c:39/40. Son rutas de probes que se comprueban con FileExist y se saltan si faltan (`:169`, `:171`), con fallback a la ruta empaquetada (`:41`). No se declara que crasheen: esa afirmacion generica del detector no esta demostrada aqui. Cinco avisos ES-GETTYPE-EXACT-MATCH estan en comparaciones ya existentes (servidor 1051/1617/2279, cliente 2026/2746). Se documentan y dejan intactos: cambiarlos seria ajeno a R1. El gate general NO se presenta verde.

## Revision y limites

Las citas del brief a new result callbacks y ReleaseCallback correspondian al arbol inicial; las citas anteriores son finales. Las copias .BEFORE y before-manifest.json permiten auditar las originales. El estado vivo de la manana estaba atrasado respecto a los backlinks: el servidor ya usaba enlace debil y el cliente ya desvinculaba resultados al completarse.

La lista de comandos del brief enumera diez operaciones (incluidas repeticiones) y mezcla peers; no demuestra seis POST de servidor exactos. La medida de seis fugas es evidencia aportada por el usuario, no repetida en esta sesion. El new por llamada si queda demostrado en ambos sources. Tampoco una unica tanda con seis POST solapados tiene por que reducirse a un objeto con este arreglo: el objetivo comprobable es que con concurrencia estable el contador deje de crecer en tandas sucesivas. Un contador de instancias estable no acredita ausencia de retencion por solicitud de buffers nativos.

No se uso lease, session_status, tools MCP, daemon vivo, DayZ o DayZDiag por prohibicion expresa del brief. Los tests de HTTP existentes usan sus fixtures locales con puerto efimero; no se levanto servicio en 8765. No se ejecuto build ni despliegue. Ningun fichero de PACKAGED_MODULES (`tools/build_native_launcher.py:53`) cambio: este parche no requiere resellar el bundle nativo. La incorporacion del addon a un despliegue corresponde al receptor tras revisar; no se realizo.

No hubo git add, commit, stash ni modificaciones intencionales fuera de la lista autorizada. La memoria durable de esta lane queda aqui; actualizar el vault/registro compartido o pipeline_feedback es FUERA DE MI ALCANCE segun el brief. La revision independiente queda para Claude, receptor indicado; no se abrieron subagentes.
