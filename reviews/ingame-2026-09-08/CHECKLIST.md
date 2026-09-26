# Test in-game 2026-09-08 — una sola pasada, vale por todo lo del día

Tres arreglos entran a la vez: la fuga de callbacks (`0818ebd`), los cinco guards
(`da3b75f`) y el crash de cámara (`fdb6d73`). **DZ-R5**: no se pide otra corrida por cada
uno; esta los cubre.

## 0. Precondición — sin esto la corrida no mide nada

Los cinco días anteriores midieron el PBO del 3-sep, no el árbol
(ficha `fb-20260908-172018-738a`). Verificado antes de escribir esto:

- PBO instalado = **9 de 9 scripts idénticos** a `addon/scripts/`.
- Los tres arreglos dentro: `IsActivePollCallback` ×1, `MAX_CALLBACK_REFS` ×2, y de
  `Camera.GetCurrentCamera()` solo queda **un comentario**, ninguna llamada.
- `MCPClientBridge.c` desplegado: sha256 `9ce3ec0bb1430dea4f91e15e5efa4fffb0d77d95570da259b8463ec4c5017a19`.

Si se vuelve a empaquetar antes de correr, `pack-addon.ps1` ya lo prueba solo: sale 1 y
dice por qué si el PBO no llega al juego.

## 1. La fuga — la señal más nítida, y se lee al cerrar

Al terminar la misión, en `_server/profiles/script_*.log`:

    SCRIPT (E): Leaked 'MCPPollCallback' script instance (Nx)!

**Predicción:** N ≈ **1**, como el cliente. No un número proporcional a la duración.

Línea base medida hoy, tres corridas contra el PBO viejo:

| corrida | duración | fugados | por minuto |
|---|---|---|---|
| 15:06:34 → 15:18:20 | 11,8 min | 2214 | 188 |
| 17:27:51 → 17:51:46 | 23,9 min | 4633 | 194 |
| 17:59:39 → 18:15:57 | 16,3 min | 3120 | 191 |

Tasa plana. **Cualquier N que vuelva a escalar con la duración significa que el arreglo no
funciona en el motor**, y entonces el diagnóstico de la lane (asignación por poll frente a
reutilización) está incompleto: la causa nativa quedó INCONCLUSA a propósito.

## 2. Que el sondeo NO se pare — el riesgo del guard de admisión

`da3b75f` cuenta `m_CallbackRefs + m_Pending + m_Jobs` contra un techo. **Si algo no
drenara, el puente dejaría de admitir polls a los ~13 s a 5 Hz.**

- Dejar correr **varios minutos** de polls y comprobar que siguen. No basta con que arranque.
- Una ráfaga de comandos y luego reposo: el sondeo debe reanudarse tras ACK/error/timeout.

## 3. Los otros cuatro guards

- `pollHz` absurdo en la config → se limita a 60, no se rechaza la configuración.
- Un resultado sin transporte → **un solo** aviso por instancia, no uno por tick.
- Cambiar de misión: `Shutdown` una vez, sin repetir el cleanup global.
- Salir con la simulación tocada: sin referencia nula en `RestoreGameplay`.

## 4. La cámara — reproducir exactamente lo que mataba al cliente

El repro de `c580`, 2 de 2 veces mortal contra el PBO viejo:

1. `dayz_test_run` server + client, adoptar el run.
2. `camera_set` (lookat) varias veces **sobre un vehículo cercano**.
3. `restore_gameplay`.
4. `camera_get`.

**Predicción:** el cliente **sobrevive**. Sin `ACCESS_VIOLATION`, sin minidump nuevo en
`_client/profiles/`.

Y con el jugador **sentado** o parentado, o entrando en vehículo durante SETTLE:
`camera.ok=false` con `camera_unavailable_vehicle` / `camera_unavailable_parented_player`,
`viewport_moved=false` y arrays vacíos. **Nunca `ok=true` solo porque exista `m_ActiveCam`.**

Con cámara MCP activa **en pie**: `camera_set`/`camera_get` deben dar una lectura utilizable
que case con el viewport real.

## 5. Lo que va a parecer una regresión y no lo es

**`restore_gameplay` devolverá `restore_unverified` también en pie.** Es el peaje deliberado
del arreglo de cámara: `ReleaseCamera()` limpia `m_ActiveCam` y esa vía ya no toma una
lectura positiva. El cleanup —simulación, controles, HUD, cámara— **sí se ejecuta**; lo que
se pierde es la confirmación. Ficha `fb-20260908-170306-0d65`.

Comprobar a mano vista, controles, HUD y simulación, y anotar si el motivo es
`no_scripted_camera` o el de vehículo/parentado.

## 6. Qué guardar

- `_server/profiles/script_*.log` y `_client/profiles/script_*.log` de la corrida (las líneas
  `Leaked` están al final).
- Cualquier `.mdmp` nuevo en `_client/profiles/` — que no debería haberlo.
- La duración real de la corrida: sin ella el número de fugados no se puede comparar.

## Qué NO cierra esta corrida

- **Por qué un `RestCallback` sobrevive al teardown** sigue inconcluso. Un residuo constante
  confirma acotamiento, no eliminación de la fuga nativa (`fb-20260908-135221-6570`).
- **Por qué `-filePatching` no alcanza `P:\DayZ_MCP\`.** Hoy se prueba con el PBO
  reconstruido; mientras siga así, cada cambio cuesta un empaquetado.
