# Resultado del test in-game - 2026-09-09

Corrida `25dc3c98-1560-49a0-9b8b-694178fec1b1`, ~11 min, cierre ordenado de **los dos**
lados. Es la primera pasada que mide el cliente: la de ayer lo dejo sin log de cierre.

Logs: `_server/profiles/script_2026-09-09_00-37-18.log` y
`_client/profiles/script_2026-09-09_00-37-27.log`.

## La cadena de build, verificada antes de medir

El PBO de ayer (21:13:25) era anterior al commit del pool (`39db0d8`, 23:02:52), y el
arbol de deploy tampoco lo tenia: `AcquireResultCallback` daba 0. Desplegado y
empaquetado a las 00:36:05, con el gate de `b4e124f` diciendo
`destination written by this build`. Comprobado ADEMAS dentro del binario:

| simbolo | apariciones en el PBO |
|---|---|
| `m_ResultCallbackPool` | 10 |
| `AcquireResultCallback` | 2 |
| `RecycleResultCallback` | 2 |
| `Camera.GetCurrentCamera()` | 0 (la unica mencion del arbol es el comentario de `MCPClientBridge.c:3671`) |

## El pool: 675 resultados, 1 fuga

Trafico generado con dos `wait_for(players_at_least=99)` de 300 s a 0,5 s de intervalo.
Cada sondeo valido es un comando que el servidor contesta con un `PostResult`, asi que
una llamada rinde cientos de resultados. La relacion 1:1 se comprobo con la linea base:
7 sondeos validos -> 7 `result posted`.

    servidor: 675 result posted, 675 result ack, 675 con ok=1
              Leaked 'MCPResultCallback' script instance (1x)!
              Leaked 'MCPPollCallback'   script instance (1x)!

Ayer, contra el PBO sin pool: **6 resultados -> 6 fugas**, 1:1. Hoy, **675 -> 1**. Con
112 veces el trafico el contador BAJA. El numero de identidades no escala con el
trafico: queda acotado por el pico de concurrencia.

**Limite de esta medida**: el pico de concurrencia fue **1**. El sondeo es secuencial,
asi que el pool nunca retuvo mas de una identidad y su techo (`MAX_CALLBACK_REFS = 128`)
no se acerco. Lo que queda probado es que no se acuna una por resultado; el
comportamiento con POST realmente simultaneos sigue sin medir.

## El cliente, medido por primera vez

    cliente: Leaked 'MCPClientPollCallback'   script instance (1x)!
             Leaked 'MCPClientResultCallback' script instance (1x)!

- **`MCPClientPollCallback` = 1** es el arreglo de poll del cliente
  (`MCPClientBridge.c:447`) verificado en el motor. Nunca se habia medido. Base del
  sondeo: `bridge_status` daba `last_poll_age_s` 0,234 s sobre ~11 min de corrida. El
  log **no** sirve para contar polls: el bridge solo registra los que traen comandos
  (hay 1 linea), asi que el numero de sondeos es inferido de la edad del poll, no leido.
- **`MCPClientResultCallback` = 1 NO es una buena noticia.** El cliente posteo
  exactamente **un** resultado (`id=677`, el `camera_set`). Es 1 fuga por 1 resultado:
  el mismo 1:1 de siempre, que sigue sin tocar a proposito (`MCPClientBridge.c:4050`).
  Este numero no dice nada sobre el cliente bajo trafico.

## Los guards: tres de los cuatro son inobservables

Este es el hallazgo que decide el trabajo siguiente. No es que la pasada no provocara
sus estados: es que **no hay con que decidirlo**.

| guard | por que no se puede verificar in-game |
|---|---|
| clamp de `pollHz` (`MCPBridge.c:203`, `MCPClientBridge.c:376`) | inalcanzable: el unico productor del config escribe `"pollHz": 5` literal (`tools/dayz_mcp/loopback.py:967`) |
| `Shutdown` una sola vez (`MCPClientBridge.c:4073`) | no escribe ninguna linea; grep de `shutdown\|restore\|terminal\|disconnect` en el log del cliente: **0** |
| `GetGame()` en `RestoreGameplay` (`MCPClientBridge.c:3917`) | idem, y su camino feliz es indistinguible del guard: si `GetGame()` no era null, el guard no disparo y no se aprende nada |
| aviso unico sin transporte (`MCPBridge.c:3454`) | SI escribe (`result dropped transport unavailable`), pero provocarlo pide tirar el daemon, que es infraestructura compartida |

El cierre se hizo con la camara puesta y **sin** `restore_gameplay`, justo para forzar el
camino del destructor. El cliente salio limpio (`~DayZGame()`, `Total Leaks (2x)`), que
descarta un crash ahi, pero no distingue «el guard salvo la situacion» de «no hacia
falta».

**Consecuencia accionable**: un guard sin linea de log no es verificable en el motor.
Los dos de cliente necesitan un `Log` latcheado antes de que tenga sentido gastar otra
pasada en ellos.

## Higiene

Ningun proceso ajeno vivo al cerrar (solo los PID 376 y 28240 de esta corrida). Cierre
por ventana con `CloseMainWindow()` en los dos, cliente primero. `cleanup_degraded: []`,
caja vacia y sin runs al terminar.
