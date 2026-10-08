## ITEM 86a3
**Decisión vinculante [DESIGN]:** añadir `action_cursor` como lectura del cursor real y diseñar `player_look_at(pos)` mediante aim con realimentación. La implementación del segundo queda condicionada a calibrar los overrides en juego. Esta ronda contiene únicamente diseño.

Fuentes y abreviaturas de rutas:

- `R` = `C:/Users/guill/dzmcp_gauntlet/triage3/tree`
- `V` = `C:/Users/guill/OneDrive/Documentos/DayZ Projects/scripts`
- `G` = `C:/Users/guill/OneDrive/Documentos/DayZ Projects/gui`
- `E` = `E:/DayZ-Exp-Extract/1.30.164014/exp/scripts/scripts`

Cada cita relativa se expande sobre estas raíces. `ba07cca` es la procedencia declarada por el encargo; no verificable mediante Git en esta copia, que carece de repositorio accesible.

### 1. `action_cursor`: lectura

### Anclajes verificados

**[EXACT]** El cursor consume el gestor del jugador seleccionado:

`V/5_mission/gui/actiontargetscursor.c:694-696`:

```c
if (m_Player && m_Player.IsPlayerSelected())
{
	Class.CastTo(m_AM, m_Player.GetActionManager());
```

`:752`:

```c
m_Target = m_AM.FindActionTarget();
```

**[EXACT]** El gestor puede devolver un objetivo forzado o un `ActionTarget` sin objeto; comprobar únicamente que existe `ActionTarget` produce falsos positivos.

`V/4_world/classes/useractionscomponent/actionmanagerclient.c:478-479,505`:

```c
if (m_ForceTarget)
	return m_ForceTarget;
action_target = new ActionTarget(null, null, -1, vector.Zero, -1);
```

**[EXACT]** La identidad incluye objeto, padre, componente y punto de impacto.

`V/4_world/classes/useractionscomponent/actiontargets.c:122-142`, extractos:

```c
Object GetObject()
	{ return m_Object; }
Object GetParent()
	{ return m_Parent; }
int GetComponentIndex()
	{ return m_ComponentIndex; }
vector GetCursorHitPos()
	{ return m_CursorHitPos; }
```

**[EXACT]** Existen cuatro slots, con restricciones propias del cursor:

`V/5_mission/gui/actiontargetscursor.c:713-720`:

```c
if (!m_Target) return;
if (m_Player.IsSprinting()) return;
if (m_Player.IsInVehicle()) return;
m_Interact = m_AM.GetPossibleAction(InteractActionInput);
m_ContinuousInteract = m_AM.GetPossibleAction(ContinuousInteractActionInput);
m_Single = m_AM.GetPossibleAction(DefaultActionInput);
m_Continuous = m_AM.GetPossibleAction(ContinuousDefaultActionInput);
```

`:216-223`, extractos:

```c
SetItemDesc(GetItemDesc(m_Interact), cargoCount, "item", "item_desc");
SetActionWidget(m_Interact, GetActionDesc(m_Interact), "interact", "interact_action_name");
SetActionWidget(m_ContinuousInteract, GetActionDesc(m_ContinuousInteract), "continuous_interact", "continuous_interact_action_name");
SetActionWidget(m_Single, GetActionDesc(m_Single), "single", "single_action_name");
SetActionWidget(m_Continuous, GetActionDesc(m_Continuous), "continuous", "continuous_action_name");
```

**[EXACT]** Una acción seleccionada puede tener su widget oculto.

`V/5_mission/gui/actiontargetscursor.c:1133,1148,1157`:

```c
if (action.HasTarget() && m_AM.GetActionState() < 1)
widget.Show(true);
widget.Show(false);
```

**[EXACT]** El HUD contiene la instancia real y la actualiza. Ocultar el HUD puede impedir que el cursor refresque objetivo y acciones.

`V/5_mission/gui/ingamehud.c:196,198,1142`:

```c
m_ActionTarget = m_HudPanelWidget.FindAnyWidget("ActionTargetsCursorWidget");
m_ActionTarget.GetScript(m_ActionTargetsCursor);
m_ActionTargetsCursor.Update();
```

`V/5_mission/gui/actiontargetscursor.c:337-344`:

```c
if (isVisionObstructionActive || m_Hud.GetHudVisibility().IsContextFlagActive(IngameHudVisibility.HUD_HIDE_FLAGS))
{
	HideWidget();
	return;
}
GetTarget();
GetActions();
```

**[EXACT]** El icono usa una condición vanilla sobre objeto o padre:

`V/5_mission/gui/actiontargetscursor.c:1296-1305`, extractos:

```c
EntityAI entity = EntityAI.Cast(target.GetObject());
if (!entity)
	entity = EntityAI.Cast(target.GetParent());
Widget w = m_Root.FindAnyWidget("item_flag_icon");
if (w)
	w.Show(entity.IsRefresherSignalingViable() && m_Player.IsTargetInActiveRefresherRange(entity));
```

**[EXACT]** `ui_tree` ya distingue visibilidad propia y jerárquica, y conserva geometría.

`R/addon/scripts/5_Mission/MCPClientBridge.c:4354-4355,4372-4373`:

```c
node.visible = w.IsVisible();
node.visible_hierarchy = w.IsVisibleHierarchy();
w.GetScreenPos(sx, sy);
w.GetScreenSize(sw, sh);
```

### Contrato obligatorio

**[DESIGN]** Firma pública: `action_cursor(timeout_s=DEFAULT_TOOL_TIMEOUT_S)`. Sin selector de objetivo: observa lo seleccionado realmente.

El puente cliente obtendrá la instancia del HUD mediante un accessor nuevo y tomará una instantánea después de su actualización natural. No llamará a `Update`, `Can`, setters, raycasts alternativos ni ejecución de acciones para producir la respuesta.

**[DESIGN]** Resultado normalizado:

| Campo | Contenido |
|---|---|
| `ok`, `schema_version`, `request_id` | Éxito de lectura, versión `1`, correlación |
| `run_id`, `client_instance`, `generation` | Procedencia acreditada |
| `sample` | Tick de lectura, tick de actualización del cursor, `content_fresh`, `coherent` |
| `manager.target` | Objetivo actual de `FindActionTarget` |
| `manager.selected_slots` | Cuatro acciones seleccionadas y sus cantidades |
| `cursor.target`, `cursor.display_object` | Objetivo y objeto de presentación usados por el cursor |
| `cursor.slots` | Cuatro acciones publicadas: clase, descriptor de entrada y widget asociado |
| `cursor.item_description_input` | Argumento observado de `SetItemDesc`; no texto renderizado |
| `widgets` | Raíz, `item`, `item_desc`, `item_flag_icon` y cuatro widgets de acción |

**[DESIGN]** Los nombres públicos de slots serán `primary`, `secondary`, `continuous_primary`, `continuous_secondary`, correspondientes respectivamente a `InteractActionInput`, `DefaultActionInput` y sus variantes continuas. Son nombres del contrato nuevo.

Cada widget devuelve `exists`, ruta dentro de la instancia, tipo, `visible`, `visible_hierarchy` y rectángulo de pantalla. Ausencia: `exists=false`, visibilidades y rectángulo `null`. La visibilidad efectiva para aceptación será exclusivamente `visible_hierarchy`.

Cada objetivo contiene `object`, `parent`, `component_index`, `cursor_pos`. Objeto y padre incluyen `classname`, posición, pareja de red y `object_id` nullable. Objetivo sin objeto/padre: `target=null`; se conserva separadamente la existencia del `ActionTarget` para acciones ambientales. No convertir componente `-1` en `0`.

**[EXACT]** La pareja de red es compartida; el identificador reutilizable de los verbos de objetos pertenece al registro del servidor.

`V/3_game/entities/object.c:813-815`:

```c
//! Returns low and high bits of networkID.
//! This id is shared between client and server for whole server-client session.
proto void GetNetworkID( out int lowBits, out int highBits );
```

`V/3_game/global/game.c:355`:

```c
proto native Object GetObjectByNetworkId( int networkIdLowBits, int networkIdHighBits );
```

`R/addon/scripts/5_Mission/MCPBridge.c:834-838,2538,2546,2553`:

```c
foreach (int spawnedId, Object registered : m_RuntimeObjects)
{
	if (registered == subject)
	{
		return spawnedId;
if (args.object_id > 0)
Object registered = m_RuntimeObjects.Get(args.object_id);
return registered;
```

**[DESIGN]** Una lectura interna nueva del servidor resolverá las parejas capturadas y consultará el registro por igualdad exacta. No registrará objetos. `object_id=null` significa sin correspondencia acreditada; jamás inferirlo por clase, distancia o índice. Ambas lecturas comparten presupuesto y fence de run/generación. Cambio de sesión durante la correlación: `snapshot_invalidated`.

**[DESIGN]** `action_cursor` será cliente-only en su semántica; el servidor únicamente correlacionará identidades. Será lectura sin lease obligatorio, incorporada explícitamente al conjunto de lecturas. Requiere run acreditado, bridges compatibles y cliente en misión con jugador seleccionado. No requiere ser dueño del lanzamiento.

**[EXACT]** Clasificación y readiness existentes:

`R/tools/dayz_mcp/session_coordination.py:95-96`:

```python
def command_requires_lease(command: str) -> bool:
    return command not in READ_ONLY_COMMANDS
```

`R/tools/dayz_mcp/bridge_readiness.py:201-208`, extractos:

```python
verdict = compute_bridge_ready(status)
if verdict["ready"]:
    return None
"error": f"game_not_ready:reason={reason}",
"code": "not_ready",
```

**[DESIGN]** Conservar errores de autenticación/readiness/transporte. Errores nuevos o específicos: `no_player`, `no_action_manager`, `cursor_unavailable`, `snapshot_invalidated`, `bad_cursor_result`. Cursor oculto o sin objetivo es una lectura válida; `ok=true` nunca significa que S7 pasó. Contenido retenido se identifica como antiguo, sin hacerlo coincidir artificialmente con el gestor.

No certifica protección autoritativa, persistencia, ejecución de acciones, aceptación del servidor, textura dibujada ni píxeles.

### 2. Orientación del jugador local

### Investigación

**[EXACT]** Lectores de heading y aim documentados en radianes; los overrides no especifican unidad. `ONE_FRAME` dura hasta el siguiente `CommandHandler`.

`V/3_game/human.c:11,27-34,240,243`:

```c
ONE_FRAME, //! Will apply value and then DISABLED on subsequent CommandHandler call
//! returns main heading angle (in radians) -PI .. PI
proto native float GetHeadingAngle();
//! returns per tick aim change (in radians)
proto native vector GetAimChange();
//! returns aim change (in radians)
proto native vector GetAimDelta(float dt);
proto native void OverrideAimChangeX(HumanInputControllerOverrideType overrideType, float value);
proto native void OverrideAimChangeY(HumanInputControllerOverrideType overrideType, float value);
```

**[EXACT]** El verbo existente pasa los valores sin conversión:

`R/addon/scripts/4_World/MCP_Weapon.c:309-310`:

```c
hic.OverrideAimChangeX(HumanInputControllerOverrideType.ONE_FRAME, dx);
hic.OverrideAimChangeY(HumanInputControllerOverrideType.ONE_FRAME, dy);
```

**[EXACT]** La cámara transforma cambios de aim a grados y separa freelook:

`V/4_world/entities/manbase/dayzplayer/dayzplayercamera_base.c:247,249,266`:

```c
if( m_pInput.CameraIsFreeLook() )
pAngleAdd += m_pInput.GetAimChange()[1] * Math.RAD2DEG;
pAngle += m_pInput.GetAimChange()[1] * Math.RAD2DEG;
```

**[EXACT]** La selección toma la cámara actual, no solamente la orientación corporal:

`V/4_world/classes/useractionscomponent/actiontargets.c:211-212`:

```c
m_RayStart = g_Game.GetCurrentCameraPosition();
m_RayEnd = m_RayStart + g_Game.GetCurrentCameraDirection() * c_RayDistance;
```

**[EXACT]** `DayZPlayer` distingue orientación corporal y heading de aim. El `SetHeading` encontrado pertenece a `HumanCommandScript`, con restricción de fase.

`V/3_game/dayzplayer.c:1088-1089`:

```c
float m_fOrientationAngle; //[in/out] horizontal model orientation ... in rad
float m_fHeadingAngle; //[in/out] horizontal aim angle ... in rad
```

`V/3_game/human.c:1209,1233-1234`:

```c
class HumanCommandScript
//! sets character rotation (heading) (PreAnim/PrePhys only!)
proto native void SetHeading(float yawAngle, float filterDt = -1, float maxYawSpeed = FLT_MAX);
```

**[EXACT]** El bloque completo `HumanCommandMove` (`V/3_game/human.c:433-504`) no declara setter de heading; sí lectores de movimiento y modificadores del filtro:

```c
proto native float GetCurrentMovementAngle();
proto native void SetTurnSpanModifier(float value);
```

**[EXACT]** Existe orientación de servidor mediante la herramienta de desarrollo, pero no demuestra convergencia de vista local ni pitch:

`V/4_world/plugins/pluginbase/plugindeveloper/developerteleport.c:157-164,170`:

```c
if (g_Game.IsServer())
{
	playerRoot.SetDirection(direction);
}
else
{
	Param3<float, float, float> params = new Param3<float, float, float>(direction[0], direction[1], direction[2]);
	player.RPCSingleParam(ERPCs.DEV_RPC_SET_PLAYER_DIRECTION, params, true);
#ifdef DIAG_DEVELOPER
```

**[EXACT]** Los setters generales de orientación usan grados; no son un contrato de aim:

`V/3_game/entities/object.c:314,317`:

```c
\brief Set orientation (yaw, pitch, roll) in <b>degrees</b>
proto native void SetOrientation(vector vOrientation);
```

**[EXACT]** `camera_set` modifica el objeto cámara. `camera_get` permite observar vista y dirección del jugador:

`R/addon/scripts/5_Mission/MCPClientBridge.c:5457,5460,5960,5972,5975`:

```c
cam.SetPosition(validation.pos);
cam.LookAt(validation.look_at);
cameraPlayer.GetCurrentCameraTransform(playerPos, playerDir, playerRot);
VectorToArray(playerDir, camera.dir);
camera.view = "player";
```

### Diseño recomendado y experimento mínimo

**[DESIGN]** `player_look_at(pos, timeout_s)` será mutación con lease. Primera versión: jugador local vivo, inmóvil, de pie/agachado, primera persona, arma bajada, sin freelook/tracking, acción o controlador concurrente. Estados restantes: rechazo explícito.

**[EXACT]** Hay discriminadores reales:

`V/3_game/dayzplayer.c:1267`; `V/3_game/human.c:43,49`:

```c
bool IsInThirdPerson();
proto native bool CameraIsFreeLook();
proto native bool CameraIsTracking();
```

**[DESIGN]** Calcular bearing/pitch desde la posición real de cámara hacia `pos`, comparar dirección observada y aplicar pulsos `ONE_FRAME` acotados por tick. Hipótesis inicial: radianes; signos, ganancia y dependencia de `dt` requieren medición. Prohibido publicar esa hipótesis como unidad verificada.

Convergencia: error angular ≤1° durante tres ticks consecutivos y tres ticks adicionales sin overrides. Presupuesto máximo de control: 3 s, limitado además por timeout. Respuesta: `converged`, error inicial/final en grados, ticks, duración, pose observada y causa de terminación. Éxito exige convergencia; no exige que exista objetivo.

Cancelación, pérdida de lease, respawn o deadline liberan únicamente overrides propios mediante ownership/generación. Sin efectos persistentes ni cambios de cámara para obtener PASS.

**[DESIGN]** Experimento previo mínimo: baseline cero y pulsos aislados `±0.01`, `+0.02` en cada eje. Registrar `dt`, cambio de aim en el tick aplicado, heading y dirección de cámara antes/después/hasta estabilizar. Comparar proporcionalidad y respuesta frente a grados, radianes o tasa dependiente de `dt`.

Observadores existentes:

- **[EXACT]** Aim: `R/addon/scripts/5_Mission/MCPClientBridge.c:4774-4776`:
  ```c
  job.weapon_action.aim_lr_after = hcw.GetBaseAimingAngleLR();
  job.weapon_action.aim_ud_after = hcw.GetBaseAimingAngleUD();
  change = hic.GetAimChange();
  ```
- **[EXACT]** `player_trace`: `R/addon/scripts/4_World/MCP_PlayerTrace.c:548,550-551`:
  ```c
  sample.heading_deg = CompassDeg(hic.GetHeadingAngle());
  orientation = s_Player.GetOrientation();
  sample.yaw_deg = orientation[0];
  ```
- **[DESIGN]** Usar además `camera_get` y `action_cursor`. Si el readback tardío pierde el pulso, instrumentar temporalmente el tick aplicado; cero tardío no demuestra ausencia de efecto.

### 3. Pruebas y aceptación

**[DESIGN]** Tests Python con fixtures independientes:

| Test | Mutación que debe volverlo rojo |
|---|---|
| Routing, autorización y readiness | Enviar cursor al servidor; exigir lease a lectura; permitir look sin lease/run |
| Ingreso estricto | Aceptar NaN, vector incorrecto, timeout inválido o selector de objetivo en cursor |
| Cuatro slots y componentes | Intercambiar slots; sustituir `-1` por `0`; listar acción registrada como visible |
| Identidad y fences | Devolver objetivo solicitado; correlacionar por classname; aceptar ID de otro run |
| Visibilidad y nulos | Sustituir jerárquica por propia; interpretar widget ausente como oculto |
| Frescura y coherencia | Mezclar gestor actual con cursor anterior y marcar coherente |
| Normalización y errores | Eliminar campos durante prune; convertir error o resultado malformado en éxito |
| Control y terminación | Converger por valor comandado; omitir estabilidad; continuar tras cancelación/deadline |

Los tests Python prueban transporte, contrato y decisiones; las referencias textuales a Enforce no acreditan comportamiento nativo.

**[DESIGN]** Aceptación in-game, automática:

1. Acreditar hash/build desplegado y stack SimpleGroup; adquirir lease para preparación/control.
2. Fixture independiente: vehículo protegido `P`, vehículo comparable no protegido `U`, y escena sin objeto alcanzable. Registrar previamente identidades exactas mediante preparación del servidor.
3. Secuencia `P → U → vacío → P`, usando `player_look_at`; exigir convergencia y muestras frescas coherentes.
4. En `P`: identidad correcta, nombre y raíz efectivos, `item_flag_icon.visible_hierarchy=true`.
5. En `U`: identidad correcta y nombre/raíz efectivos, icono existente pero jerárquicamente oculto.
6. En vacío: objetivos sin objeto/padre, cursor e icono efectivamente ocultos.
7. Confirmar que icono y descripción pertenecen a la misma fila real y comparar sus rectángulos. Nunca aceptar otro widget homónimo.
8. Repetir positivos/negativos de orientación horizontal, vertical y cruce ±180°; verificar estabilidad tras liberación y rechazo de cámara script/freelook.
9. Guardar JSONL de fixture, comandos y muestras. Liberar lease; consultar `session_status`. Cierres mediante lifecycle guard.

**[EXACT]** La fila vanilla contiene ambos widgets:

`G/layouts/day_z_hud.layout:2292,2310,2450`:

```text
ImageWidgetClass item {
ImageWidgetClass item_flag_icon {
TextWidgetClass item_desc {
```

**[DESIGN]** Widget faltante, identidad ilegible, cámara ilegible o muestra antigua: **INCONCLUSO**, nunca negativo satisfecho. Identidad incorrecta o visibilidad contraria con muestra válida: **FAIL**.

### 4. Riesgos y no verificado

**[DESIGN]** Riesgos: objetivos forzados, proxies/padres, cursor retenido, mods que sustituyan HUD, reutilización de IDs, límites de pitch, suavizado, reconciliación y competencia de controles. No emitir certificación de píxeles, textura, alpha u oclusión mediante visibilidad jerárquica.

**[EXACT]** En 1.30 persisten estos dos mecanismos:

`E/5_mission/gui/actiontargetscursor.c:709,1314`:

```c
m_Target = m_AM.FindActionTarget();
w.Show(entity.IsRefresherSignalingViable() && m_Player.IsTargetInActiveRefresherRange(entity));
```

**[DESIGN]** Esa comparación no acredita paridad completa: 1.30 requiere compilación y ciclo propio.

**No verificado:** unidades/signos/ganancia de overrides, convergencia, replicación de orientación hacia la vista local y comportamiento SimpleGroup S7 desplegado. No se implementó, ejecutó juego ni escribió archivo/commit; este contenido es el entregable para `SPEC_86A3.md`.

### Orchestrator addendum (binding; measured in game on 2026-10-08, DayZ 1.29, main 46833d7)
- `OverrideAimChangeX/Y(HumanInputControllerOverrideType.ONE_FRAME, v)` (as `weapon_aim` applies them,
  `addon/scripts/4_World/MCP_Weapon.c:309-310`) moves the player view by exactly `v` RADIANS, once per pulse,
  proportionally: dx +0.01 -> yaw +0.5730 deg, dx -0.01 -> -0.5730 deg, dx +0.02 -> +1.1459 deg, dx +0.2 ->
  +11.4592 deg (positive = to the right, compass heading increasing); dy +0.01 -> pitch +0.5730 deg (positive = up);
  0,0 -> no change. Readback: `camera_get` (camera.dir) and the bridge's `aim_lr`/`aim_ud` (degrees). The pitch set
  this way persisted after the weapon left the hands.
- `weapon_aim` refuses `no_weapon_in_hands` without a weapon; every measurement above had an SKS in hands. Whether
  the overrides act with empty hands or another item in hands is NOT verified: `player_look_at` must not depend on
  `weapon_aim`'s weapon gate; it applies the overrides itself, measures the result (camera dir), and fails closed
  with a named error when the view does not converge (do not report success from the commanded value).
- A full-body action (a deploy that was cancelled) changed the player's yaw and pitch: never assume the view keeps
  a previous orientation across actions; always measure the current camera direction first.
- Bridge version: bump `MCP_BRIDGE_VERSION` to "12" (`addon/scripts/5_Mission/MCPMessages.c:1`); another batch of
  this round makes the same one-line change.
- Scope of this batch: `action_cursor` (read) and `player_look_at` (mutation) as specified above; nothing else.
