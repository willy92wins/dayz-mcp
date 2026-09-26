# Resultado del test in-game — 2026-09-08

Dos corridas. La primera no midió nada y explicar por qué es medio hallazgo del día.

## Veredicto por ítem de la checklist

| # | qué | resultado |
|---|---|---|
| 1 | La fuga de `MCPPollCallback` baja | **PASA — 1** en 15,9 min (base: ~190/min) |
| 2 | El sondeo NO se detiene | **PASA** — 16 min, incluidos 3,5 sin atender |
| 4 | El crash de cámara no vuelve | **PASA** — cliente vivo tras el repro completo |
| 5 | `restore_unverified` esperado | **PASA** — `camera_unavailable_no_scripted_camera` |
| 3 | Los otros cuatro guards | **NO EJERCITADOS** — ver abajo |

## 1. La fuga: uno

Corrida `2f50be6c`, servidor de **22:02:18 a 22:18:13 = 15,9 min**, cierre ordenado:

```
22:18:13.348 ENGINE : Destroying game
22:18:13.348  SCRIPT : Cleaning up script module globals 'Mission'
22:18:13.348   SCRIPT (E): Leaked 'MCPResultCallback' script instance (6x)!
22:18:13.348   SCRIPT (E): Leaked 'MCPPollCallback' script instance (1x)!
22:18:16.516  --- Termination successfully completed ---
```

La línea base medida hoy contra el PBO viejo era **188 / 194 / 191 fugados por minuto**
en tres corridas. Para 15,9 min eso predecía **~3.040**. Salió **1**.

Ese 1 residual es lo que la lane anunció: acotamiento del crecimiento por poll, **no**
certificado de cero fugas. Por qué un `RestCallback` sobrevive al teardown sigue sin
explicarse, y ese 1 es su huella.

## 2. El sondeo aguantó

El guard de admisión de `da3b75f` cuenta `m_CallbackRefs + m_Pending + m_Jobs`; si algo
no drenara, habría cortado el sondeo **a los ~13 s** a 5 Hz. Sondeó 16 minutos, con un
tramo de **3,5 min sin nadie atendiendo** (se me caducó el lease), y al volver
`query_all_players` respondió en **0,28 s**. `bridge_status` durante la corrida:
`last_poll_age_s` 0,106 en servidor y 0,232 en cliente, `ready: true`, y los **nueve**
contadores de rechazo del fence a 0.

## 4-5. La cámara

Repro de `c580` completo —`camera_set` ×3 (lookat sobre un `CivilianSedan` spawneado),
`camera_get` ×2, `restore_gameplay` ×2— con el jugador a pie y sentado como conductor.
**El cliente sobrevivió a todo.** Sin `ACCESS_VIOLATION`, sin minidump nuevo.

Con cámara MCP activa: `camera.ok=1`, matriz y dirección reales — o sea que el nuevo
`BuildCameraResult` lee de `m_ActiveCam` y funciona. Tras `ReleaseCamera`:

```
camera.ok = 0   pos = []   matrix = []   dir = []   viewport_moved = 0
error = "camera_unavailable_no_scripted_camera"
```

Fail-closed con estado nombrado y arrays vacíos, exactamente el contrato. Y
`restore_gameplay` devolvió `restore_unverified` las dos veces, que es el peaje
deliberado fichado en `0d65`.

**Límite honesto**: no alcancé el estado exacto que mataba. `vehicle_enter` sienta
server-side, y el cliente no recibe `HumanCommandVehicle`, así que sus guards de
vehículo/parentado no llegaron a dispararse (`seated=0` en la respuesta del cliente con
`in_vehicle=1` en el servidor). Lo que sí es estructural: el binario desplegado **no
contiene ninguna llamada** a `Camera.GetCurrentCamera()` —solo un comentario—, y ahí es
donde moría.

## 3. Los guards que no se probaron

`pollHz` absurdo, el aviso único de resultado sin transporte, `Shutdown` una sola vez y
el `GetGame()` de `RestoreGameplay` **no se ejercitaron**: necesitan estados que esta
pasada no provocó (config inválida, transporte caído, cambio de misión). Quedan
pendientes; no cuentan como verdes.

## Lo que costó la primera corrida

`8eef9811`, 21:49-21:58, cerrada con `dayz_test_stop`: **cero líneas `Leaked`** — y eso
no significaba cero fugas, significaba que el hook no llegó a correr. Los RPT se cortan
en seco (servidor 21:52:13, cliente 21:50:48) sin una sola línea de teardown, mientras
los procesos vivieron hasta las 21:58.

El control es la corrida ajena de las 17:59, que sí tiene `Destroying game` →
`Cleaning up script module globals` → `Termination successfully completed` y por eso dio
contadores. Fichado como `fb-20260908-202102-2edd`.

## Hallazgo nuevo

`MCPResultCallback` fugó **6x**, y en las tres líneas base aparece **0 veces** — no
porque no fugara, sino porque aquellas corridas no posteaban resultados.
`MCPBridge.c:3486` sigue haciendo `new MCPResultCallback(this)` por cada resultado: el
mismo defecto que tenía el de poll, a mucho menor ritmo. Ficha
`fb-20260908-202130-2b16`.

## Higiene

Ninguna sesión ajena tocada. El run de `@LFPowerGrid` que ocupaba la caja lo cerró su
propia sesión (`afe45ee5-428`, `lifecycle_adopt` → `lifecycle_stop`, `owned_pids
29132 26860`), no yo. Cierre propio sin degradar: `cleanup_degraded: []`, sin lease ni
runs míos al terminar.
