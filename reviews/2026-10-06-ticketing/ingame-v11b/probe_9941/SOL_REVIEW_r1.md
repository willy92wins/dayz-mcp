**CHANGES_REQUIRED.** La combinación corregida es válida, pero faltan controles de atribución, limpieza y clasificación. Además, la secuencia control→suppress→cancel tiene un problema en single-player. Revisión estática únicamente: no ejecuté DayZ ni escribí archivos.

Rutas abreviadas en las citas:

- `W` = `C:\Users\guill\dzmcp_gauntlet\probe_9941\ws`
- `T` = `C:\Users\guill\dzmcp_gauntlet\triage3\tree`
- `V` = `C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts`
- `H` = `W\MCPHoldProbe\scripts\4_World\MCPHoldProbe.c`
- `P` = `W\PROCEDURE.md`

**1. Sí, puede armar un trial válido; todavía puede rechazarse antes del probe.**

`V/4_world/entities/itembase/kitbase.c:150-151`:
```c
AddAction(ActionTogglePlaceObject);
AddAction(ActionDeployObject);
```

`T/tools/dayz_mcp/server.py:8161-8164`:
```python
"object, hands is the item in the player's hands, self is a null target; "
"the result echoes that same mode. hands and self need an addon that "
"announces action_use_target"
```

`T/addon/scripts/5_Mission/MCPClientBridge.c:3745`:
```c
actionTarget = new ActionTarget(targetItem, targetParent, -1, vector.Zero, -1);
```

El toggle utiliza ese target no nulo: `V/4_world/classes/useractionscomponent/actions/singleuse/actiontoggleplaceobject.c:57-58`:
```c
action_data.m_Player.SetLocalProjectionPosition(action_data.m_Target.GetCursorHitPos());
action_data.m_Player.TogglePlacingLocal();
```

Después, el holograma sigue el ray de cámara (`V/4_world/classes/hologram.c:1157-1162`). Deploy exige holograma local y posición válida:

`V/4_world/classes/useractionscomponent/actions/continuous/deployactions/actiondeployobject.c:40-46`:
```c
if (player.IsPlacingLocal())
{
    Hologram hologram = player.GetHologramLocal();
    if (!hologram.IsColliding())
    {
        if (item.CanBePlaced(player, hologram.GetProjectionEntity().GetPosition()))
            return true;
```

La máscara permite crouch/erect (`:12`); es full-body y comprueba posibilidad de transición (`V/4_world/classes/useractionscomponent/actionbase.c:901-906`). Piernas rotas, inventario ocupado, otra acción o input ocupado también bloquean el inicio. El bridge devuelve `condition_failed` **antes** de `PerformActionStart` (`MCPClientBridge.c:3948-3955`); la reserva puede fallar después (`actionbase.c:193-198`).

**P2 — Preparación insuficiente.** Escenario: holograma visible pero colisionando; queda el marker intacto y nunca se arma.

Reemplazar pasos 1–2 (`P:35-36`) por:
> 1. With all markers absent and no action or inventory operation pending, give a fresh `FenceKit` using `inventory_give(classname="FenceKit", dest="hands")`. Record its receipt and object_id; confirm it is held by the local client player.  
> 2. Stand erect, with unbroken legs, on open flat ground. With no existing hologram, call `action_use(action="ActionTogglePlaceObject", target="hands", classname="FenceKit")` once. Confirm the resulting hologram and valid placement indication with `capture_screenshot`. `setup_failed` alone proves neither success nor failure; an instant local toggle can finish before the bridge checks for a running action. Keep all markers absent during preparation.

**2. Los casts y el bookkeeping sirven para Deploy; cancel necesita una restricción de modo.**

`V/4_world/classes/useractionscomponent/actions/continuous/deployactions/actiondeploybase.c:56-58`:
```c
override ActionData CreateActionData()
{
    PlaceObjectActionData action_data = new PlaceObjectActionData();
```

`actiondeployobject.c:152,162`:
```c
override void OnFinishProgressClient(ActionData action_data)
poActionData.m_AlreadyPlaced = true;
```

Por tanto, `already_placed=true` sí evidencia progreso terminado en cliente. El probe conserva identidad de `ActionData`, captura estado antes de limpiar (`H:227-242`) y restaura una vez (`H:271-277`). Sin embargo, timeout restaura el flag; **no termina la acción**:
```c
// H:194-195
McpHoldWrite("timeout");
McpHoldRestore();
```

**P1 — Cancel puede quedar neutralizado en offline.** Tras control, `m_ActionInputWantEnd_Send=true` (`actionmanagerclient.c:385`). El siguiente inicio SP pasa directamente por `SetupAction` (`:764-769`), evitando el reset de `ActionStart` (`:602-605`). Entonces:
```c
// actionmanagerclient.c:276-278
if (!m_ActionInputWantEnd_Send)
{
    m_ActionInputWantEnd = true;
```

El `EndActionInput()` del probe (`H:129`) puede no hacer nada.

Reemplazar «Run **control**, then **suppress**, then **cancel**» (`P:3`) por:
> Run **control**, then **suppress**, then **cancel** in multiplayer with a dedicated server and client. Single-player/offline is excluded from this unchanged-PBO cycle because repeated `PerformActionStart` calls bypass the input-end bookkeeping reset.

Reemplazar `P:26` por:
> - `role` = `"client"`. This cycle requires the multiplayer topology specified above.

**3. Los cinco segundos permiten distinguir brazos, pero las reglas actuales pueden dar falsos veredictos.**

`actiondeploybase.c:12`: `new CAContinuousTime(m_ActionData.m_MainItem.GetDeployTime())`; `itembase.c:4388`: `return UATimeSpent.DEFAULT_DEPLOY;`; `actionconstants.c:38`: `DEFAULT_DEPLOY = 5`.

Cancel devuelve `UA_CANCEL` (`V/4_world/classes/useractionscomponent/actioncomponents/cacontinuoustime.c:70`). `HasAlternativeInterrupt()` devuelve true (`actiondeployobject.c:20-22`), pero solo selecciona la animación:
```c
// actions/actioncontinuousbase.c:89-97
if(action.HasAlternativeInterrupt())
{
    SetCommand(DayZPlayerConstants.CMD_ACTIONINT_FINISH);
}
...
m_Canceled = true;
```
No convierte cancel en progreso terminado.

**P1 — Refutación y apoyo demasiado amplios.** Escenarios: suppress recibe rechazo servidor después de `accepted=true`; o termina progreso cliente pero falla placement servidor. Ninguno refuta H1. Control rechazado tampoco constituye un control negativo válido.

Reemplazar `P:79-85` por:
> Classify H1 only from uncontaminated, attributable trials. Require all three starts accepted, matching mode/action, initial ignore flag false for control and true for suppress/cancel, complete lifecycle evidence, and restoration to false.  
> **Supported:** suppress completes normal progress (`already_placed=true`, finished lifecycle) and produces an attributable baseline-new Fence; valid control and cancel terminate without completing progress or producing a Fence.  
> **Refuted:** suppress reaches execution, then demonstrably cancels before completing progress with `already_placed=false`, despite suppression remaining enabled and no independent interruption or placement failure. Confirm no attributable Fence after bounded server observation.  
> **Inconclusive:** every other result, including server rejection, vanished pending data without execution evidence, timeout, missing observations, client completion without server placement, or a Fence in control/cancel.

**4. Faltan IDs obtenibles, limpieza y recuperación segura.**

**P1 — El baseline descrito no puede obtener sus IDs.** `entities_query` no los devuelve (`T/addon/scripts/5_Mission/MCPMessages.c:434-438`):
```c
string type;
string classname;
bool has_cargo;
ref array<float> pos;
float distance;
```
`object_inspect` solo devuelve ID si ya se le pasó (`MCPBridge.c:2523-2525`). `object_resolve` registra objetos existentes (`server.py:5554-5559`).

Reemplazar `P:37` por:
> 3. Preserve the preceding attempt's evidence before clearing JSONL. Query the fixed placement area with `limit=128`; require reliable, untruncated results. Resolve each existing Fence using `object_resolve(type="Fence", pos=<row position>, radius=<isolating radius>)`. Record IDs and positions; ambiguity blocks the trial.

Reemplazar `P:42` por:
> 8. Repeat the same query and resolution after closure and bounded server observation. Attribute only a new Fence in the recorded placement area, with no competing actor or action. Preserve JSONL, dispatch receipt, screenshots, kit ID and before/after results. Then, with markers absent, delete only trial-created Fences and any surviving trial kit using existing `object_delete`; verify removal. Successful deploy consumes the kit. Confirm no hologram remains; toggle it off only if present. Restore the baseline before preparing another trial.

Mecanismo: Fence se crea en `fencekit.c:27`; kit se elimina en `actiondeployobject.c:230-232`; cancel y completion eliminan holograma (`playerbase.c:2538,2550`).

**P2 — Recovery confunde restauración con cierre.** También atribuye marker intacto exclusivamente a `DeleteFile`, ignorando rechazo previo del bridge.

Reemplazar `P:46` por:
> Never retry blindly. A remaining marker can mean pre-dispatch rejection or failed deletion; remove it before any other action and inspect the dispatch receipt. Read the full JSONL on the host if waits miss evidence. `restored` restores only the flag. After timeout or unresolved lifecycle, stop this cycle. Retry only after demonstrated closure, evidence preservation, cleanup and complete preparation.

Reemplazar `P:29` por:
> - Gate on `satisfied`, not `ok`. The 200-line lookback can miss an earlier start after many tick lines; inspect the complete fresh JSONL before treating a wait timeout as missing lifecycle evidence.