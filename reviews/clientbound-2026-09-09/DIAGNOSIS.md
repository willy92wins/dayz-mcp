# El cliente y sus resultados en vuelo - diagnostico

Offline, sobre las fuentes. Todo lleva `path:line`.

## Las tres cosas que el handoff mezclaba en una

El LIVE-STATE dice "el cliente sigue sin cota de resultados en vuelo: 4 por poll sin
esperar -> 4k identidades tras k polls". Eso junta tres propiedades distintas, y solo
una es un problema de verdad.

### 1. Retencion: ACOTADA. No es el problema.

`ReleaseCallback` (`MCPClientBridge.c:4093`) saca el callback de `m_CallbackRefs`, y la
clase lo llama en **los tres** caminos terminales:

| camino | linea |
|---|---|
| `OnSuccess` | `MCPClientBridge.c:78` |
| `OnError` | `MCPClientBridge.c:89` |
| `OnTimeout` | `MCPClientBridge.c:99` |

Asi que lo retenido en memoria es el numero de POST **realmente en vuelo**, no el
trafico acumulado. Las "4k identidades tras k polls" son objetos ACUNADOS, no objetos
vivos a la vez. La distincion importa porque cambia cual es el arreglo.

### 2. Churn: real, pero por COMANDO, no por tick.

`MCPClientBridge.c:4062` hace `new MCPClientResultCallback(this)` por cada resultado
posteado. Es el mismo defecto que tenia el servidor y que cerro el pool de `39db0d8`.

**Pero la magnitud no se parece.** El del servidor acunaba ~190/min **en reposo**,
porque lo movia el sondeo. El del cliente solo acuna cuando alguien manda un comando de
cliente (`camera_*`, `ui_*`, `vehicle_*`). Medido anoche en 11 minutos de corrida:
**servidor 675, cliente 1**. El churn del cliente lo marca el ritmo del agente, no el
reloj.

### 3. Concurrencia en vuelo: el cliente NO esta desguarnecido, esta guardado por el termino equivocado.

Correccion a como lo cuenta el LIVE-STATE. El cliente **si** frena la admision, en
`OnTick` (`MCPClientBridge.c:317-320`):

    if (m_Pending && m_Pending.Count() > PENDING_POLL_THRESHOLD)   // 8
    {
        return;
    }

Lo que NO cuenta es lo demas. La asimetria exacta:

| termino | servidor | cliente |
|---|---|---|
| POST en vuelo (`m_CallbackRefs`) | contado | **no contado** |
| jobs en curso | contado | **no contado** |
| cola de comandos (`m_Pending`) | contado | contado (umbral 8) |
| techo global | `MAX_CALLBACK_REFS = 128` | **no existe** |

`m_CallbackRefs.Count()` en el cliente aparece unicamente dentro de bucles (`:4099`,
`:4218`); **nunca** comparado contra un techo.

El hueco es preciso, no general: una rafaga cuyos comandos se despachen en el acto
(hasta `MAX_DISPATCH_PER_TICK = 4` por poll, `:515-517`) y cuyos resultados tarden deja
crecer `m_CallbackRefs` **sin que la cola pase nunca de 8**. Los jobs, igual.

**El guard no puede provocar deadlock, y esto se comprobo antes de proponerlo.** El
`OnTick` drena antes de decidir: `m_JobRunner.Tick()` (`:281-283`) y `DrainPending()`
(`:298`) corren por encima de la decision de sondear (`:322-330`). Pausar la admision
deja el drenaje vivo, que es justo lo que hace falta para que la cuenta vuelva a bajar.

## Lo que esto refuta

La lane R1 se nego a acotar el cliente con este motivo: "no tiene cota de resultados en
vuelo, y acotarlo pedia backpressure o cambiar el despacho".

**La primera mitad es cierta; la segunda no.** El servidor resolvio exactamente ese
problema sin backpressure y sin tocar el despacho, con cinco lineas en `StartPoll`
(`MCPBridge.c:236-240`):

    if (m_CallbackRefs.Count() + m_Pending.Count() + m_Jobs.Count() > MAX_CALLBACK_REFS - MAX_POLL_RESULTS)
    {
        return;
    }

Pausa la ADMISION y deja drenar; los POST terminales siguen saliendo. Y el cliente tiene
las tres piezas para contarlo igual: `m_CallbackRefs`, `m_Pending` y
`m_JobRunner.Count()` (`MCPJobRunner.c:46`).

La reserva correcta para el cliente no es la del servidor. El servidor reserva 64 porque
un poll puede devolver hasta el tope de ingreso del daemon. Un poll del cliente solo
puede aceptar **20** obligaciones nuevas: 4 despachadas (`MAX_DISPATCH_PER_TICK`) mas 16
encoladas (`MAX_PENDING`); todo lo que pase de ahi ya lo rechaza `QueuePendingOrFail`.

## Lo que NO se debe portar, y por que

El pool del servidor **no se porta tal cual**, y la razon esta en una linea de cada
clase:

    MCPCallbacks.c:58        protected MCPBridge m_Bridge;        // enlace debil
    MCPClientBridge.c:64     protected ref MCPClientBridge m_Bridge;   // ref FUERTE

El del servidor es debil a proposito ("the mission singleton owns the Managed bridge").
El del cliente es `ref` a proposito tambien, con su motivo escrito al lado:
"MCPJobRunnerOwner is not Managed: detach explicitly; a raw link can dangle".

Consecuencia: un callback ocioso metido en un pool seguiria sujetando el bridge con una
referencia fuerte -> ciclo bridge -> pool -> callback -> bridge, y justo en el camino de
teardown donde vivia el crash de camara. Se puede evitar (soltar el bridge ANTES de
reciclar, con un handle local), pero es un cambio delicado en el peor sitio posible **a
cambio de un churn que anoche fue de 1 objeto en 11 minutos**.

Coste/beneficio: no compensa. El pool del cliente queda fichado, no hecho.

## Propuesta

1. **Techo de callbacks y guard de admision en el cliente**, simetricos con el servidor,
   con reserva 20 en vez de 64. Mecanico, con precedente medido en el motor anoche (el
   sondeo del servidor aguanto 16 min sin que el guard cortara de mas).
2. **El pool del cliente NO**, por lo de arriba.
3. Riesgo que hay que nombrar: pausar el sondeo del cliente pausa su entrada de
   comandos. Con umbral 108 y una concurrencia real de 1, no se alcanza nunca en uso
   normal; un `ui_dialog` abierto esperando a una persona aporta 1. Solo se llega bajo
   inundacion, que es cuando se quiere.
4. **Sin validar en el motor**: cambia el comportamiento bajo carga y eso pide corrida.
   El gate offline son los contratos + la suite.
