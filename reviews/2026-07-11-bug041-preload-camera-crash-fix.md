# BUG-041 — Preload camera crash (stale `camera_get` delivered during client preload)

**Fecha**: 2026-07-11 · **Estado**: fix implementado + verificado offline (151/151) · **Gate in-game PENDIENTE**

> Numeración: el prompt inicial lo llamó "BUG-024", pero BUG-024..031 ya están
> ocupados (P3 de R21 F4). El siguiente ID libre del ledger (max usado = BUG-040)
> es **BUG-041**. No hay `bug-ledger.md` dedicado; los BUG-0xx viven en `HANDOFF.md`
> + `reviews/`.

## Síntoma (repro 2026-07-11, smoke SUB_BRZ s32b, intento 2 de launch)

Un `camera_get` encolado en el broker durante una sesión anterior quedó stale. Al
reconectar un client DayZDiag nuevo, el bridge le entregó el comando **durante el
preload** (player/cámara aún no existen) y el client crasheó con minidump nativo en
`MCPClientBridge::BuildCameraResult`
(`DayZ_MCP\scripts\5_Mission\MCPClientBridge.c`; minidump
`DayZDiag_x64_2026-07-11_21-35-53.mdmp`). Mitigación usada en el smoke: reiniciar el
server para vaciar la cola + evitar `camera_get` toda la sesión.

## Causa raíz (dos capas)

**Capa 1 — bridge Enforce (el crash).** `MCPClientBridge` arranca el poll en cuanto
`TryInit` encuentra RestApi + config (`:213-278`); no exige player ni estar in-game.
`Dispatch` (`:435`) no tenía gate de readiness: despachaba los 8 comandos-cliente sin
verificar el player. `camera_get` → `DispatchCameraGet` (`:526`) → `BuildCameraResult`
**síncrono** (`:1657`), el único camino síncrono del cliente que tocaba APIs de engine
sin guard de player: `Camera.GetCurrentCamera()` / `GetGame().GetCurrentCameraPosition()`
/ `Camera.GetCurrentFOV()`. En preload (world/cámara sin construir) → deref nativo →
minidump. El `if (!current)` que ya existía es Enforce y no atrapa un crash del engine.
Los otros 6 comandos ya estaban protegidos por `ResolveOwnedCar()` (`:889`, null-check de
player) o por los guards `if (!player)` de las fases de job (`:929`, `:1033`).

**Capa 2 — daemon Python (comando stale entregado a peer nuevo).** `record_poll`
(`loopback.py`) entregaba toda la cola del peer y la limpiaba, sin TTL ni noción de
sesión de peer. El broker daemon sobrevive a la sesión del client (LL-156), así que
`_queues["client"]` persiste: un `camera_get` de la sesión N que nunca se pulleó lo
recibe el client de la sesión N+1 en su primer poll (preload).

## Fix

**Capa 1 — `DayZ_MCP\scripts\5_Mission\MCPClientBridge.c`** (Enforce):
- Helper `IsClientInGame()` (`:430`): `GetGame() && GetGame().GetPlayer()` (idiom ya
  usado en `:498`; API `GetGame().GetPlayer()` verificada, game.c:946).
- Gate fail-closed en `Dispatch` (`:449-459`): rechaza cualquier comando-cliente con
  `error="client_not_in_game"` si el player local no existe. Cierra la clase entera
  "comando durante preload" en un solo punto (R7).
- Guard defensivo en `BuildCameraResult` (`:1662-1672`): mismo check antes de tocar los
  getters nativos. Necesario porque el report del job de `camera_set`
  (`MCP_PostJobSuccess:1438`) llama `BuildCameraResult` sin re-pasar por `Dispatch`.
  Devuelve `ok=false` con pos/matrix/dir vacíos (ctor-init de MCPCamera, MCPMessages.c:186).

**Capa 2 — `DayZ_MCP_dev\tools\dayz_mcp\loopback.py`** (Python):
- Reloj inyectable `time_fn` en `ServerState` (default `time.monotonic`) para hacer la
  higiene testeable de forma determinista.
- `record_poll`: **reconnect-flush** (gap desde el último poll del peer >
  `PEER_RECONNECT_GAP_S=10s` → purga la cola del peer, `error="peer_reconnect_flush"`) +
  **TTL** (comandos > `COMMAND_TTL_S=30s` → `error="stale_discarded"`). Cada comando
  descartado recibe un result de error para que el `/await` del enqueuer resuelva.
- Los descartes de `exec_enforce` escriben un audit `"discarded"` fuera del lock, para
  mantener la invariante audit-ledger (allowed == comando que llegó al juego) (R7).

Ambas capas son fail-closed. La Capa 1 evita el crash en **todo** timing (incluido el
relanzamiento rápido dentro del TTL); la Capa 2 evita además que comandos stale se
entreguen/ejecuten tarde (relevante sobre todo para un `vehicle_control` stale).

## Verificación offline

- Suite Python: **151/151 OK** (`.venv-mcp`, unittest discover) — 7 tests nuevos en
  `tests/test_loopback.py::StaleCommandHygieneTest` (TTL drop/keep, flush drop/keep,
  first-poll edge, aislamiento per-peer, exec-audit consistency). `py_compile` OK.
- Walk R8 de `MCPClientBridge.c`: happy-path in-game idéntico (sin regresión), preload
  rechazado limpio, player-desaparece-mid-job cubierto por el guard de `BuildCameraResult`.
- **NO verificado offline** (gate de comportamiento, solo el engine): que el guard
  `GetPlayer()==null` es suficiente para evitar el crash nativo. Es la hipótesis a
  confirmar in-game — bien fundada (los otros handlers usan el mismo discriminador).

## Gate in-game pendiente

Reconstruir la PBO `@DayZ_MCP` (cambio Enforce; config.cpp intacto → filepatching también
sirve para iterar) + recoger el cambio Python del daemon (sesión nueva por el editable
install, o reiniciar el daemon broker si está vivo). Repro:

1. Con el juego arriba, encolar un `camera_get` (peer client) — que quede sin pullear.
2. Matar el client DayZDiag.
3. Relanzar el client.
4. **PASS** = el client arranca sin minidump; el `camera_get` stale no se ejecuta (el
   daemon lo flushea por reconnect-gap y/o el bridge lo rechaza con `client_not_in_game`).

`MCP_BRIDGE_VERSION` se queda en `"5"` (core.py:17 = MCPMessages.c:1): el fix no cambia el
protocolo de serialización, solo añade rechazos.

## Gate in-game — PASS (2026-07-12)

Ejecutado sobre el juego real (DayZDiag server+client contra el daemon `:8765`; PBO reconstruida
con `HAS_FIX=True`; daemon con la Capa 2 activa, arrancado tras la edición de `loopback.py`).
Orquestador `scratchpad/bug041_repro.ps1` (raw `/enqueue` + `/await` al daemon para controlar el
timing del preload sin la latencia de las tools).

- **Baseline (no-regresión)**: con el player in-game, `camera_get` devolvió la cámara del player
  (`pos=[6063.07, 9.66, 1932.0]`, `error="player_camera_active"` = el branch `!current` normal). El
  guard no rompe el happy path.
- **Capa 2 (flush)**: `camera_get` (id=3) encolado con el client MUERTO → al relanzar, el primer
  poll del client nuevo lo descartó con `{"ok":false,"error":"peer_reconnect_flush"}`. No llegó al
  bridge.
- **Capa 1 (guard)**: durante el preload del client relanzado (player local null), `camera_get`
  (id=4) llegó al bridge (gap pequeño, sin flush) y devolvió `client_not_in_game` — rechazo limpio,
  sin crash. 6 s después, con el player ya spawneado, `camera_get` (id=5) devolvió la cámara
  (`camok=1`): recovery automático.
- **Veredicto de crash**: client vivo tras el ciclo, `dump_delta=0` (350→350 minidumps, ninguno
  nuevo). El escenario que produjo `DayZDiag_x64_2026-07-11_21-35-53.mdmp` ya no crashea.

Ambas capas verificadas AISLADAS (no solo el "no crashea" agregado). El gate de comportamiento —lo
único no fabricable offline— queda PASADO: la hipótesis "`GetPlayer()==null` gate previene el crash
nativo de preload" está confirmada in-game. Matiz observado: el preload tiene dos sub-fases — una
temprana con `GetPlayer()==null` (donde el guard actúa → `client_not_in_game`, id=4) y una pantalla
de carga tardía donde el player local ya existe y `camera_get` devuelve la cámara de intro sin crash
(id=6, no-regresión adicional). Caveat de tooling: `Start-Process DayZDiag` desde un job en
background captura el PID del launcher, que el fork de DayZDiag reemplaza por el proceso real del
juego; al terminar el job muere el launcher pero el proceso real persiste (PID distinto, polleando,
`dump_delta=0`). Un screenshot inmediato puede dar `window_not_found` mientras re-renderiza.
