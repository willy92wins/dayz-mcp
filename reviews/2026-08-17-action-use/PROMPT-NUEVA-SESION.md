# Prompt para sesión nueva — construir `action_use` en dayz-mcp

> **AVISO 2026-08-17 tarde — este prompt contiene TRES anclajes REFUTADOS.**
> Los escribió el orquestador, el implementador los refutó, y se re-verificaron uno a uno
> contra el árbol: el implementador tenía razón en los tres. **No los reutilices.**
>
> - `PerformAction(int, …)` en `actionmanagerclient.c:756` **no existe en esta build**: está
>   dentro de `#ifdef BOT` (`:754-760`) y `BOT` no se define. El bueno es `PerformActionStart`
>   en `:762`.
> - `ForceTarget` **no sirve**: pone `cursorHitPos = vector.Zero` (`:466`) y `CCTCursor.Can` mide
>   desde ahí (`cctcursor.c:30-31`), así que la distancia siempre excede el límite.
> - `CCINone` **no significa manos vacías**: `CCINone.Can()` devuelve `true` incondicionalmente
>   (`ccinone.c:3-6`). Ese anclaje salió de creerse el comentario de cabecera de
>   `LFPG_ActionOpenBTCAtm.c` en vez de abrir la clase vanilla.
>
> El estado bueno vive en el LIVE-STATE de `LFPowerGrid_dev\HANDOFF.md`. Ledger: SP-281.


Escrito 2026-08-17 tras aterrizar los tres verbos de UI. Copiar el bloque entero.

---

```
Construye `action_use` en dayz-mcp: una tool que haga al jugador ejecutar una acción
in-game sin manos humanas. El caso que la motiva es abrir el panel del ATM de
LFPowerGrid, que hoy es lo único que impide verificar el gate WithdrawOnly de forma
autónoma.

## Estado del que partes (no lo rehagas)

Los verbos `ui_tree`, `ui_set_text` y `ui_click` YA están construidos, revisados y
aterrizados en el árbol real, y compilan: PBO de @DayZ_MCP reconstruido y servidor
arrancado limpio el 17-ago 11:21 (Module Mission 216 files / 497 classes, puente
inicializado, cero errores). Sirven para leer y pulsar cualquier UI YA ABIERTA.

Lo que falta es abrirla. `ui_click` sobre la vista pre-creada oculta del ATM sale por
`if (!m_IsOpen) return`, así que sin una acción de jugador no hay escenario.

Comprueba antes de nada si el daemon ya reinició: si `ui_tree` responde
`not_whitelisted`, la whitelist aún no cargó y hay que reiniciarlo.

## Anclajes VERIFICADOS por lectura directa del árbol (fíate de estos)

- `ActionManagerClient.PerformAction(int user_action_id, ActionTarget target,
  ItemBase item, Param extraData = NULL)`
  → `DayZ Projects\scripts\4_world\classes\useractionscomponent\actionmanagerclient.c:756`
- `ForceTarget(Object targetObject)` → mismo fichero `:460`
- `ClearForceTarget()` → mismo fichero `:469`
  Estas dos son la clave del diseño: la acción del ATM usa `CCTCursor`, y ForceTarget
  evita tener que simular hacia dónde mira el jugador.
- `PerformActionStart(ActionBase, ActionTarget, ItemBase, Param)` → `:762`
- `UseAcknowledgment()` devuelve `true` en la base
  → `scripts\4_world\classes\useractionscomponent\actionbase.c:1146-1148`
  Consecuencia: el arranque de la acción NO es local. El cliente pide permiso, el
  servidor re-evalúa la condición y responde; `OnExecuteClient` solo corre tras el ack.
- La acción a disparar, `LFPG_ActionOpenBTCAtm`
  → `LFPowerGrid\scripts\4_World\LFPG_ActionOpenBTCAtm.c:26-67`
  · extiende `ActionInteractBase`
  · `m_ConditionItem = new CCINone` en `:37` → **las manos deben estar VACÍAS**
  · `m_ConditionTarget = new CCTCursor(LFPG_INTERACT_DIST_M)` en `:38`
  · condición: el objetivo es `LFPG_BTCAtmBase`, está alimentado y no está ruined
  · `OnExecuteClient` manda `BTC_OPEN_REQUEST` y guarda netLow/netHigh en
    `LFPG_BTCAtmClientData` bajo `#ifndef SERVER`
- El ATM sin cableado es `LFPG_BTCAtmAdmin` (`LFPG_BTCAtm.c:470-496`): sin puertos y
  siempre alimentado. Su kit es `LFPG_BTCAtmAdmin_Kit`. Es el que quieres spawnear.

## Anclaje NO verificado (verifícalo tú antes de usarlo)

- `ActionManagerServer.StartDeliveredAction` re-comprobaría `pickedAction.Can(...)`
  en `actionmanagerserver.c:142-146`. Viene de un informe de otra lane y NO lo abrí.
  Si tu diseño depende de ello, ábrelo primero.

## Cómo está montado el puente (patrón a imitar, no a inventar)

Un comando viaja: `@app.tool` en `DayZ_MCP_dev\tools\dayz_mcp\server.py` → encola por
nombre en `loopback.py` (`CLIENT_COMMANDS`) → el juego lo saca en su poll →
if/else en `DayZ_MCP\scripts\5_Mission\MCPClientBridge.c` → responde por `PostResult`.

El nombre del comando debe ser IDÉNTICO en tres sitios: whitelist de Python, la
`@app.tool`, y el if/else del puente. Un desajuste da `not_whitelisted` o
`unknown_command`. Copia la forma de `ui_click`, que es el ejemplo más cercano.

Todo campo de args nuevo hay que DECLARARLO en `MCPArgs` (`MCPMessages.c`): las claves
JSON no declaradas se descartan en silencio al deserializar, y el síntoma despista.

## Las trampas que ya conocemos

1. **La acción es asíncrona.** `PerformAction` vuelve enseguida; el RPC sale después,
   en el evento de animación, y solo si el servidor dio el ack. Así que `action_use`
   NO puede reportar éxito de forma síncrona. O devuelve "enviada" y el llamante
   espera, o el propio verbo espera una señal. Decide y dilo, no lo dejes ambiguo.
2. **Manos vacías.** `CCINone` en la acción del ATM. Si el jugador lleva algo, la
   condición falla y no vas a entender por qué.
3. **Enforce no compila offline.** El único gate real es arrancar. Reglas que muerden:
   nada de ternario, ninguna condición booleana partida en varias líneas, tipo
   explícito en cada local, nada de `new` en rutas por tick, y `Print(string.Format(
   TAG + ...))` es trampa de parseo. Ningún fichero con BOM.
4. **`DayZ_MCP_dev\.git` es una carpeta ROTA.** `git status` falla y devuelve stdout
   vacío, que es lo mismo que un árbol limpio. Cualquier gate escrito así da FALSO
   VERDE. Mira el `returncode`, no el stdout.
5. **Puede haber otra sesión editando este árbol a la vez.** Ya pasó el 17-ago y se
   pisó trabajo ajeno. Re-toma hashes de los ficheros destino JUSTO ANTES de escribir,
   nunca al empezar la tarea, y aborta si difieren. Trabaja en `%TEMP%`, promociona al
   final.
6. **Reiniciar el daemon mata las tools MCP de la sesión viva.** Planifícalo como lo
   último que hagas.

## Reparto

Delega la implementación en Grok (postura A: `--tools "read_file,grep,list_dir,
search_replace"`, `--deny "MCPTool"`, `--always-approve`, NUNCA `--permission-mode
acceptEdits`, `--no-memory`, `-m grok-4.6`). Revisor ciego en sesión NUEVA, jamás un
`-r` de quien implementó. Tú arbitras y verificas contra el árbol las citas que
decidan algo.

## Hecho cuando

- `action_use` abre el panel del ATM sin que nadie toque el teclado, y se ve en el log
  del cliente el `BTC_OPEN_RESPONSE`.
- Y entonces, encadenado: `ui_set_text` en `EditBtcAmount`, `ui_click` en `BtnBuyBtc`,
  y `wait_for` sobre `[BTCTxResult]` leyendo su `err=`. Ese es el oráculo del gate
  WithdrawOnly de B3, y cierra el escenario completo sin humano.

No toques código de LFPowerGrid. Este trabajo es del MCP.
```
