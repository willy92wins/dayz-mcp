# P1 — callbacks de poll: diagnóstico y corrección acotada

**La asimetría está en cuántos objetos se crean: un callback por petición en servidor y uno reutilizado por generación en cliente. Ambos drenan sus listas.** Se ha eliminado la asignación por poll exitoso del servidor y la retención del bridge por callbacks retirados. La causa exacta de que un RestCallback sobreviva al cierre del motor y la desaparición de los 2.214 avisos quedan **INCONCLUSAS**: no hay implementación C++ ni ejecución in-game permitida. El cambio es una mitigación del crecimiento por poll y una corrección de ownership; no certifica cero fugas nativas.

## Evidencia y procedencia

HEAD inicial y final de esta lane: `da3b75fc8e70f8720aaef56331932d624d3bc461`. Fuentes originales conservadas byte a byte, antes de modificarlas, en este directorio. `baseline.json` identifica hashes/tamaños originales y `after-sources.json` los entregados. En las citas siguientes:

- `S0` = `reviews/leak-2026-09-08/MCPBridge.c.BEFORE`.
- `K0` = `reviews/leak-2026-09-08/MCPCallbacks.c.BEFORE`.
- `C0` = `reviews/leak-2026-09-08/MCPClientBridge.c.BEFORE`.
- `S`, `K`, `C` = los mismos nombres `.c` bajo `addon/scripts/5_Mission/`, después del cambio.

Las referencias del brief se abrieron. La numeración del servidor y los callbacks coincidía con los originales; el `if (m_Bridge)` está en K0:12, no :11. El cliente tiene otra diferencia relevante omitida en el brief: hereda de `MCPJobRunnerOwner` (C0:130; definición real `addon/scripts/5_Mission/MCPJobRunner.c:1`), que no hereda de Managed.

La corrida acredita los avisos: `_server/profiles/script_2026-09-08_15-06-36.log:61-64` registra 2.214 MCPPollCallback, un MCPBridge y un map<int,Object>; `_client/profiles/script_2026-09-08_15-08-21.log:191-192` registra un MCPClientBridge y un MCPClientPollCallback. El RPT del servidor :1172-1185 muestra destrucción del juego y terminación completada. Son avisos de instancias retenidas al limpiar el módulo, no una medida de crecimiento del RSS ni evidencia de muerte inesperada del proceso.

La línea histórica de arranque (`_server/profiles/DayZDiag_x64_2026-09-08_15-06-34.RPT:3`) usa `-filePatching` y `-mod=P:/Mods/@DayZ_MCP`. P: no está disponible aquí. El sibling `../DayZ_MCP` existe; MCPCallbacks.c coincide con K0, pero los dos bridges no coinciden con el árbol de trabajo. El diff del sibling carece de los guards de da3b75f. No se ha acreditado que ese sibling fuese exactamente lo cargado a las 15:06. La comparación de lógica es válida contra las fuentes inspeccionadas; atribuirles todos los eventos de aquella corrida requiere verificar el despliegue. No se modificó el sibling ni los perfiles.

## Camino completo del poll original

| Paso | Servidor | Cliente | Consecuencia |
|---|---|---|---|
| Crear | S0:247 crea `new MCPPollCallback(this)` en CADA StartPoll | C0:433-438 crea solo bajo `if (!m_PollCallback)`; C0:430 usa el guardado | N peticiones sanas producen N objetos frente a uno |
| Mantener vivo | S0:248 inserta en m_CallbackRefs | C0:421-422 llama Ensure/Hold; C0:441-459 deduplica e inserta en m_PollCallbackRefs, además del ref del campo C0:199 | El cache cliente sigue vivo entre polls, aunque salga del array |
| In-flight | S0:122-125 retorna antes de acumular tiempo | C0:286-300 también retorna, pero tiene watchdog | Una petición activa impide emitir la siguiente |
| éxito / error / timeout | K0:10-34 llama ReleaseCallback ANTES del handler en los tres casos | C0:15-49 hace lo mismo y luego verifica identidad | No hay una rama de terminación que omita Release solo en servidor |
| Borrar hold | S0:3499-3510 usa Find y Remove | C0:4034-4067 recorre las dos listas, compara identidad y Remove | Diferente implementación de búsqueda, misma operación de liberación |
| Reabrir polling | S0:267 o S0:377 ponen in-flight false | C0:470 o C0:564 lo ponen false | Los parse errors también ocurren después de Release; no retienen el hold |
| Callback que no llega | Servidor no tiene watchdog; conserva in-flight y el hold | C0:529-545 abandona el cache y conserva el hold antiguo hasta respuesta/cierre | El servidor puede quedarse esperando; el cliente puede crecer por abandonos, no por cada poll sano |
| Respuesta tarde | El servidor original no comprueba identidad; una notificación repetida tras error puede tocar el siguiente poll | C0:19-24 libera el hold antiguo y descarta si ya no es activo | El descarte debe suceder DESPUÉS del drenaje |
| Cierre | S0:3513-3562 resetea contexto, limpia arrays y anula campos; S0:3565-3570 anula singleton | C0:4127-4155 desconecta callbacks que siguen en arrays; C0:4189 suelta el cache | El cliente original NO desconecta el callback cacheado que ya salió del array |

Los endpoints nativos se verificaron en `../scripts/3_game/http/restapi.c`: GET :103, POST :123, reset :133, GetRestContext :155; son `proto`, no cuerpos de implementación. Find :394, Insert :407 y Remove :463 se verificaron en `../scripts/1_core/proto/enscript.c`. No hay evidencia que permita culpar a Find frente al bucle del cliente. Se conserva Find y su drenaje.

## Por qué el ciclo propuesto no basta

Durante una petición existe una relación bridge→array→callback→bridge. Cuando Release elimina el elemento, **esa vuelta del ciclo ya no existe**. El ref callback→bridge mantiene al bridge si otra raíz conserva el callback, pero no explica qué conserva al propio callback completado. El cierre del servidor también limpia y anula el array; el log conserva los callbacks y el bridge, sin listar ese array. Esto es compatible con retención ajena al array / contabilidad nativa de RestCallback, no con la explicación suficiente «se olvidó Remove». No es un experimento que aísle la raíz nativa.

[HIPÓTESIS] Cada instancia de RestCallback puede quedar retenida/contabilizada por el motor independientemente del array. El consumidor cliente reutilizado explica entonces 1 frente a N sin requerir distinta gestión de `ref` en los miembros. Hay un reporte primario histórico, T188795, donde un autor informa fugas incluso al crear una subclase vacía de RestCallback; leído en el [feed de Bohemia, comentario de Vliek del 21-03-2025](https://feedback.bistudio.com/feed/?after=7488005327892464247&projectPHIDs=PHID-PROJ-n3vkkfov4otq7e6kzd6o). El acceso directo al ticket no estuvo disponible. Es evidencia de plausibilidad, no prueba de su vigencia ni del mecanismo en DayZ 1.29 de esta corrida.

Por eso **quitar ref solamente no constituye una solución demostrada a los 2.214 callbacks**, y tampoco se añaden `delete this`, resets por tick ni supuestas APIs de refcount. Reutilizar elimina el multiplicador observable. Romper la referencia de retorno evita que los callbacks conservados sigan siendo propietarios del bridge.

## 2.214 frente a ~3.420: el denominador no está medido

S0:132-138 acumula el intervalo solo después del guard in-flight. El período real incorpora espera HTTP, oportunidad de tick, backoff y pausas. `poll_hz=5` es un mínimo intervalo entre una terminación y el siguiente envío, no cinco GET garantizados por segundo de reloj.

- 684 × 5 = 3.420 es la estimación del brief, no un contador de peticiones.
- 684 / 2.214 = 0,308943 s por ciclo; con 0,2 s de intervalo quedan 0,108943 s por ciclo para esperas/planificación. Por tanto, los 2.214 podrían ser TODOS los objetos creados. No se ha medido esa latencia: es una compatibilidad aritmética.
- El RPT del servidor registra idle durante 85,052 s, entre :910 (15:07:10.233) y :1078 (15:08:35.285). No se ha medido cuánto siguió haciendo tick/poll en idle; no se descuentan esos segundos como un hecho.
- El cliente ni siquiera tuvo los mismos 684 s de misión: su inicialización aparece aproximadamente a 15:08:54.946 (`_client/profiles/DayZDiag_x64_2026-09-08_15-08-18.RPT:526`), y limpia globals a 15:18:09.963 (:700).

**Fracción liberada: INCONCLUSA.** No existe en estos logs un censo de GET enviados ni de callbacks construidos/destruidos. No hay soporte para decir «se liberaron 1.206» o inventar una rama que libere el 35,26%. El modelo de tests usa 3.420 eventos artificiales para comprobar el contrato, no para reinterpretarlos como tráfico real.

## Por qué no aparece MCPResultCallback

En las 69 líneas del script log del servidor hay cero líneas `result posted`, cero `result ack` y cero errores/timeouts de poll. El emisor original crea MCPResultCallback en S0:3471 y registra el POST en :3481, y los handlers de resultados registran en :3484-3496. No hay evidencia de que se crease un solo callback de resultado en esta corrida. La ausencia de ese tipo en fugas no discrimina éxito frente a error ni GET frente a POST; haría falta una corrida con resultados efectivamente emitidos y acreditados. Tampoco demuestra que POST está libre del posible problema nativo.

## Cambio aplicado y defecto latente del cliente

1. **Servidor: cache por generación sana.** S:59 guarda m_PollCallback; S:250-263 solo crea si falta, pero inserta el hold por petición y hace el mismo GET. El guard exacto de da3b75f se conserva en S:239. Release continúa haciendo Find/Remove en S:3514-3526. Cachear fuera del array no acumula entradas en él.
2. **Servidor: retorno débil seguro.** K:4 y K:59 dejan de ser ref. S:1 pasa a Managed, cuya definición se leyó en `../scripts/1_core/proto/enscript.c:117`. Así el singleton sigue poseyendo el bridge durante la misión y la referencia débil se anula cuando se destruye. La distinción de soft links y punteros plain se verificó en la [documentación primaria de DayZ](https://community.bistudio.com/wiki/DayZ%3AEnforce_Script_Syntax#Managed_class_&_pointer_safety), consultada el 08-09-2026. `if (m_Bridge)` deja de ser una comprobación incapaz de detectar al dueño desaparecido por culpa del propio ref.
3. **Identidad retirada en error/timeout.** S:372-383 y C:557-568 anulan el cache antes de reabrir el polling. K:21,34,47 y C:21,35,50 drenan y descartan lo que ya no sea activo. La API avisa que OnError puede repetirse (`restapi.c:53`); reutilizar inmediatamente esa identidad confundiría una notificación antigua con el siguiente GET. Los parse errors recibidos dentro de un OnSuccess no requieren cambiar identidad. Se conserva el abandono por watchdog del cliente y su hold hasta respuesta/cierre.
4. **Cliente: desconexión explícita, sin cambiar su jerarquía.** C:140 conserva `extends MCPJobRunnerOwner`. Hacer Managed esa base está **FUERA DE MI ALCANCE**. Mantener ref y desconectar es la alternativa conservadora: C:23,37,52 desconecta respuestas descartadas; C:41,56 desconecta polls retirados; C:82,92,102 desconecta cada resultado completado. El callback sano se reutiliza; C:4138 lo desconecta al cerrar aun estando fuera de m_PollCallbackRefs. Los callbacks aún pendientes se siguen desconectando en los bucles existentes C:4154,4169. El caso latente era el callback cacheado y retenido nativamente, ya liberado del array: el Shutdown anterior no encontraba su m_Bridge para anularlo.
5. **Cierre servidor.** S:3528-3538 desconecta y suelta el cache antes de resetear el contexto. Los callbacks de servidor retirados llevan soft links; no se fuerza destrucción de un objeto nativo.

No se cambian versión/protocolo, URL, frecuencia, backoff, jobs, despacho ni el guard de admisión. No se han añadido logs. El cache genera como máximo un objeto para una secuencia de éxitos; **errores/timeouts crean una generación nueva**, el cliente conserva además su posible residuo por watchdog y cada misión crea su cache. Si el motor retiene esos objetos, ese crecimiento residual sigue pendiente. Los resultados siguen asignando por POST; no se ha hecho un pool general ni N3.

## Validación y alcance del resultado

`red.log` ejecuta los tests finales con `DAYZ_LEAK_BEFORE_DIR` apuntando a las copias originales verificadas por SHA. No restaura fuentes encima de trabajo compartido. `red-initial.log` conserva además la primera corrida directamente sobre el árbol aún sin modificar. El receptor puede revertir únicamente las tres fuentes y ejecutar ambos módulos sin esa variable: volverán a comprobar los originales.

El rojo final tiene 7 tests de lifetime con 22 fallos (subtests) y 8 de drenaje con 2 fallos. El fallo principal de asignación es literal: `AssertionError: 3420 != 1`. El verde final tiene **403 tests en 34 módulos, 34 exit codes 0**: 32 módulos existentes encontrados por contenido y dos nuevos test_leak_. El brief decía 34 existentes; el censo inicial dio 32 ejecutables más _addon_paths.py, un helper.

Los tests verifican: 3.420 polls exitosos con un objeto y lista vacía al completar; éxito/error/timeout; resultados mezclados; ACKs fuera de orden/duplicados; reserva de pending/jobs; cierre a 65 holds y reapertura al liberar; una generación antigua no borra ni despacha el poll actual. Las mutaciones en memoria que omiten Release o Remove dejan 65 callbacks y cierran el guard. El guard in-flight preserva el hold de una petición que no termina. Son contratos estructurales y un modelo de flujo conectado a las fuentes; **no ejecutan Enforce, no prueban refcounts nativos y no demuestran cero fugas**.

Se encontró un validador estructural fuera del repo en el Knowledge Pack (el path local citado por la skill no existía). `validator.log` y `validator-before.log`: ambos FAIL, 2 errores y 5 avisos, con los mismos rule IDs, severidades, archivos y líneas de código. Los dos errores están en las rutas de sondeo opcional de MCPDialogController.c:39-40; los cinco avisos son comparaciones GetType intactas. Comparación exit 0; validador bruto exit 1. No se anuncia ese gate como verde. Son **FUERA DE MI ALCANCE**, y no se han modificado.

Para la corrida conjunta del receptor: acreditar hashes desplegados; observar éxito sostenido durante varios minutos y **comprobar que el polling NO se detiene**; medir peticiones realmente emitidas; producir al menos un resultado en cada lado; observar recuperación de error/timeout y descarte de respuestas tardías; cerrar y comparar callbacks y bridges retenidos, incluyendo un cierre entre polls y otro con una petición pendiente. Un único callback residual tras muchos éxitos confirmaría el acotamiento, no cero fugas. La desaparición de MCPBridge/MCPClientBridge comprobaría la separación de lifetimes. No se ha solicitado ni ejecutado esa corrida en esta lane.
