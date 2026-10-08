## ITEM 9941

**Decisión vinculante [DESIGN]: añadir `action_hold`, genérico por clase de acción, con ejecución acotada y resultado de ciclo de vida.** `action_use` conserva su comportamiento. No se implementan operaciones específicas para colocar kits o subir banderas, ni se invocan directamente callbacks de efectos.

Alcance: especificación en solo lectura para la base solicitada `46833d7`, incluyendo el caso repetitivo d1a8 y la intención de acciones genéricas de 9ab8. No hay cambios, pruebas ejecutadas ni commit.

Las rutas relativas pertenecen a `C:/Users/guill/dzmcp_gauntlet/base_v11f`. Para las citas siguientes:

- `V` = `C:/Users/guill/OneDrive/Documentos/DayZ Projects/scripts`.
- `U` = `V/4_world/classes/useractionscomponent`.
- `P` = `C:/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev/.claude/worktrees/mcp-ticketing-session-7fe51f/reviews/2026-10-06-ticketing/ingame-v11b/probe_9941`.
- `S` = `C:/Users/guill/OneDrive/Documentos/DayZ Projects/SimpleGroup`.

**1. Contrato público [DESIGN]**

Nueva herramienta:

```python
# [DESIGN] Ejemplo de llamada; no es implementación.
action_hold(
    action="ActionDeployObject",
    target="hands",
    classname="FenceKit",
    hold_timeout_s=15.0,
    timeout_s=30.0,
)
```

Acepta los selectores de `action_use`: `action`, `classname`, `pos`, `radius`, `target`, `door_index`, `component_index`, `cursor_pos`, con las mismas restricciones. Solo admite acciones derivadas de `ActionContinuousBase`; las demás devuelven `not_continuous` antes de ejecutarse.

`hold_timeout_s`: número finito, excluyendo booleanos, `0 < valor <= 120`, predeterminado 30 segundos. Comienza inmediatamente antes del inicio local e incluye la espera de aceptación. `timeout_s`: presupuesto total Python, predeterminado 45 segundos; debe ser finito, excluir booleanos y cumplir `hold_timeout_s + 15 <= timeout_s <= 300`. Los 15 segundos reservan entrega, cierre local —máximo 5 segundos— y publicación. El plazo del puente es independiente del proceso Python.

Se elige un verbo separado para mantener intactos los valores predeterminados y resultados de `action_use`, y utilizar un comando desconocido por PBO antiguos. La necesidad está anclada en `tools/dayz_mcp/server.py:8204-8206`:

```text
[EXACT]
"tool does not sustain continuous-action input or wait for progress "
"completion. Verify the intended effect separately; client callback "
"reachability by code is not an in-engine test of your mod."
```

Una solicitud admitida devuelve una observación estructurada, incluso cuando la acción termina sin éxito:

```json
{
  "ok": true,
  "hold_protocol": 1,
  "hold_id": "<identificador único>",
  "started": true,
  "end_state": "finished",
  "reason": "natural",
  "action_state": 4,
  "duration_s": 6.863,
  "completed_cycles": 1,
  "cycles_scope": "client_progress",
  "already_placed": true,
  "flag_restored": true,
  "cleanup_complete": true
}
```

[DESIGN] El ejemplo describe campos propuestos, no una respuesta existente.

- `finished`: estado terminal observado `UA_FINISHED`.
- `cancel`: cancelación/interrupción observada, incluyendo muerte, cambio de jugador o cierre del puente; `reason` identifica la causa.
- `timeout`: venció el plazo y se solicitó cierre. No implica ausencia de efectos.
- `rejected`: no se creó esta ejecución o se observó rechazo; razones `setup_failed` o `server_rejected`.
- `unknown`: desapareció la ejecución sin evidencia terminal suficiente. Nunca convertirla en cancelación o éxito.

`action_state` es el último estado observado de **esta** ejecución, o `null`; `duration_s` mide desde el inicio local hasta el cierre de la observación e incluye drenaje. `completed_cycles` cuenta eventos completos de progreso observados en cliente, nunca segundos transcurridos ni efectos confirmados por servidor. `already_placed` aparece únicamente cuando el dato permite `PlaceObjectActionData.Cast`; conserva tanto `false` como `true`. `cleanup_complete` significa que la ejecución ya no está pendiente/actual **localmente**, y que el flag fue restaurado.

`ok=true` acredita una observación válida; solo `end_state=finished` acredita ese terminal local. Colocación, subida de bandera y otros efectos requieren comprobación independiente.

Errores anteriores al armado mantienen el canal de errores: argumentos inválidos, `hold_not_supported`, jugador/gestor ausente, acción inexistente, gestor ocupado, input ocupado, condición falsa y restricciones de lease/run. Un fallo de transporte no se traduce en `end_state=timeout`: el terminal queda desconocido para quien llama; no reintentar ciegamente.

**2. Transporte, capacidades y autoridad [DESIGN]**

Nuevo comando cliente `action_hold`, con `selector` obligatorio y cerrado: `world`, `hands`, `self`, `door`, `component`. Los dos últimos llevan sus índices obligatorios; la ausencia no se infiere del valor cero. Reutilizar la resolución y validación de targets mediante una extracción mínima, conservando resultados del camino antiguo.

Antes de encolar, exigir anuncio de `action_hold` en el peer cliente. El helper existente exige anuncio explícito, en `tools/dayz_mcp/bridge_readiness.py:466-471`:

```text
[EXACT]
if capabilities.get("state") != "announced":
    return False
announced = capabilities.get("announced_commands")
if not isinstance(announced, list):
    return False
return command in announced
```

Ausencia, estado malformado o excepción de lectura: `hold_not_supported`, cero dispatch. Sin fallback a `action_use`. Validar también `hold_protocol=1` y el `hold_id` devuelto; una respuesta antigua/incompatible no prueba ejecución sostenida.

El rechazo de verbos desconocidos ya existe en `addon/scripts/5_Mission/MCPClientBridge.c:1231-1232`:

```text
[EXACT]
result.ok = false;
result.error = "unknown_command";
```

Actualizar anuncio ordenado `CLIENT_POLL_CAPS`, whitelist, schemas de ingreso, asociación comando/herramienta, comparación de capacidades, serialización y poda de resultados. **Versión de puente y versión esperada Python: `"12"`**, sin relajar el gate global. Anclas actuales:

```text
[EXACT] addon/scripts/5_Mission/MCPMessages.c:1
const string MCP_BRIDGE_VERSION = "11";

[EXACT] tools/dayz_mcp/core.py:41
EXPECTED_BRIDGE_VERSION = "11"

[EXACT] tools/dayz_mcp/core.py:67-68
if bridge_version != expected_bridge_version:
    return "version_mismatch", f"bridge_version {bridge_version!r} != {expected_bridge_version!r}"
```

Lease obligatorio y binding/run autorizado mediante el camino normal. No incorporar `action_hold` a comandos de lectura. La clasificación existente es `tools/dayz_mcp/session_coordination.py:95-96`:

```text
[EXACT]
def command_requires_lease(command: str) -> bool:
    return command not in READ_ONLY_COMMANDS
```

Conservar gates de ownership, readiness y cuarentena. En `tools/dayz_mcp/loopback.py:2306-2312`:

```text
[EXACT]
if self._run_is_fenced(run_id):
    return "run_not_owned"
state = self._durable_run_state(run_id)
if state in {"RUNNING", "STARTING"}:
    return None
if state == "RUNNING_IDLE":
    return "run_not_owned"
```

El daemon asigna un `hold_id` no reutilizable y lo asocia a comando, lease y run. Añadir cancelación interna `action_hold_cancel`, dirigida exclusivamente a ese identificador, para abandono, liberación/expiración y retirada del run. Un cancel tardío nunca termina un hold posterior.

Integrar esa cancelación en cleanup; no asumir que cancelar la cola termina una acción entregada. El cleanup actual comienza con `tools/dayz_mcp/loopback.py:4048-4050`:

```text
[EXACT]
cleanup: dict[str, object] = self.cancel_owner_pending(
    session_id, reason, lease_id
)
```

Sin confirmación de cierre, registrar limpieza degradada y mantener cercado el run frente a nuevos holds hasta reconciliarlo. Respetar cuarentena; el límite autónomo del puente sigue siendo obligatorio.

**3. Ejecución y observación del puente [DESIGN]**

Implementar el controlador y snapshot en `4_World`; el adaptador de comandos/jobs reside en `5_Mission`. Los hooks inferiores no dependen de `MCPCommand`/`MCPResult`.

1. **Armar únicamente esta llamada.** Tras validaciones y reserva del job, guardar jugador, gestor, acción, identificador, referencias previa pendiente/actual y valor anterior del flag. Rechazar cualquier pendiente/actual preexistente. Guardar el valor verdadero anterior, no asumir `false`.

   El probe guarda `P/MCPHoldProbe.c.txt:58` y restaura en `:274-275`:

   ```text
   [EXACT]
   m_McpHoldPrevIgnore = m_IgnoreAutoInputEnd;
   m_McpHoldRestored = true;
   SetIgnoreAutomaticInputEnd(m_McpHoldPrevIgnore);
   ```

2. **Suprimir justo antes del único `PerformActionStart`.** Llamar `SetIgnoreAutomaticInputEnd(true)` y después la ruta vanilla. No llamar callbacks de efectos manualmente.

   En `U/actionmanagerclient.c:298-300`:

   ```text
   [EXACT]
   if (ai.WasEnded() && (ai.GetInputType() == ActionInputType.AIT_CONTINUOUS || ai.GetInputType() == ActionInputType.AIT_CLICKCONTINUOUS) && !m_IgnoreAutoInputEnd)
   {
       EndActionInput();
   ```

3. **Identidad por `ActionData`.** Adoptar solo un dato nuevo respecto de ambas referencias previas, cuyo `m_Action` corresponde a la acción solicitada. Durante el inicio armado, los hooks también deben poder identificar ese dato si un callback ocurre dentro de `super.PerformActionStart`; no esperar necesariamente a su retorno. La identidad de `ActionBase` sola no basta.

   El probe comprueba `P/MCPHoldProbe.c.txt:90,92,94,96`:

   ```text
   [EXACT]
   if (m_PendingActionData != prevPending)
   if (m_PendingActionData != prevCurrent)
   if (m_PendingActionData.m_Action == action)
   m_McpHoldData = m_PendingActionData;
   ```

4. **Capturar terminal por callback, publicar mediante job.** En `ActionManagerClient.OnActionEnd`, comprobar identidad y copiar estado y placement **antes** de `super`; restaurar después. Snapshot persistente e idempotente: el job puede consumirlo posteriormente, una sola vez. Polling solo vigila plazos, jugador y desapariciones; no determina éxito.

   `P/MCPHoldProbe.c.txt:237-242`:

   ```text
   [EXACT]
   m_McpHoldEndState = m_CurrentActionData.m_State;
   PlaceObjectActionData placeData = PlaceObjectActionData.Cast(m_CurrentActionData);
   if (placeData)
   {
       m_McpHoldEndPlacedKnown = true;
       m_McpHoldEndPlaced = placeData.m_AlreadyPlaced;
   ```

   La necesidad de capturar antes del borrado está en `U/actionmanagerbase.c:319-321`:

   ```text
   [EXACT]
   if (m_CurrentActionData)
       m_CurrentActionData.m_Action.ActionCleanup(m_CurrentActionData);
   m_CurrentActionData = NULL;
   ```

5. **Contar progreso completo, no frames.** Hook cliente en `CAContinuousBase.OnCompletePogress`, con filtro exacto de dato y componente. Incrementar al entrar y preservar la llamada `super` exactamente una vez; incluir el último ciclo aunque su callback cierre la acción.

   `U/actioncomponents/cacontinuousbase.c:8-12`:

   ```text
   [EXACT]
   void OnCompletePogress(ActionData action_data)
   {
       ActionContinuousBase action = ActionContinuousBase.Cast(action_data.m_Action);
       if(action)
           action.OnFinishProgress(action_data);
   ```

   El repeat completa progreso y continúa, en `U/actioncomponents/cacontinuousrepeat.c:46-47`:

   ```text
   [EXACT]
   OnCompletePogress(action_data);
   return UA_PROCESSING;
   ```

6. **Timeout y cierre.** Al vencer: fijar causa `timeout`, restaurar inmediatamente el flag y llamar `EndActionInput()` solo si el dato sigue siendo propio. Dar hasta un segundo al cierre normal; si sigue presente, solicitar interrupción de red y cierre/interrupción local, reintentando envío cuando el canal esté disponible, hasta completar cinco segundos de drenaje.

   Las APIs existentes, en `U/actionmanagerclient.c:274-278,732-739,1282-1287`, incluyen:

   ```text
   [EXACT]
   override void EndActionInput()
   if (!m_ActionInputWantEnd_Send)
       m_ActionInputWantEnd = true;

   if (m_CurrentActionData.m_State == UA_AM_PENDING || m_CurrentActionData.m_State == UA_AM_REJECTED || m_CurrentActionData.m_State == UA_AM_ACCEPTED)
       OnActionEnd();
   m_CurrentActionData.m_Action.Interrupt(m_CurrentActionData);

   if (ScriptInputUserData.CanStoreInputUserData())
   ctx.Write(INPUT_UDT_STANDARD_ACTION_END_REQUEST);
   ctx.Write(DayZPlayerConstants.CMD_ACTIONINT_INTERRUPT);
   ctx.Send();
   ```

   Estas líneas son extractos, no un bloque ejecutable. No borrar referencias vanilla para simular cierre. Si no se demuestra cierre: `cleanup_complete=false`; conservar evidencia y cercado. Una finalización capturada antes del plazo gana; una finalización posterior al timeout no borra la causa timeout.

7. **Toda salida restaura.** Rechazo de setup, fin normal, cancelación, timeout, muerte, cambio/pérdida del jugador, shutdown y destructor. Mantener referencia al gestor original; no restaurar sobre el jugador nuevo. El shutdown cancela/restaura antes de vaciar jobs/REST. El puente actual vacía jobs en `addon/scripts/5_Mission/MCPClientBridge.c:7012-7014`:

   ```text
   [EXACT]
   if (m_JobRunner)
   {
       m_JobRunner.Clear();
   ```

**4. Falso negativo instantáneo: fuera de este cambio**

El defecto está explicado por fuentes. `ActionTogglePlaceObject` es local e instantánea, en `U/actions/singleuse/actiontoggleplaceobject.c:19-26`:

```text
[EXACT]
override bool IsLocal()
{
    return true;
}
override bool IsInstant()
{
    return true;
}
```

Vanilla ejecuta y termina dentro del inicio, `U/actionmanagerclient.c:677-679`:

```text
[EXACT]
action.Start(m_CurrentActionData);
if (action.IsInstant())
    OnActionEnd();
```

Después el puente interpreta ausencia de acción como fallo, `addon/scripts/5_Mission/MCPClientBridge.c:3959,3963`:

```text
[EXACT]
if (amc.GetRunningAction() == null)
result.error = "setup_failed";
```

**[DESIGN] Excluirlo:** corregirlo requiere acreditar ejecución instantánea, también frente a setup rechazado. Cambiar ese resultado modificaría precisamente el camino que debe conservarse. Preparar el holograma comprobando su estado real y sin repetir el toggle por el error.

**5. Pruebas exigidas [DESIGN]**

| Prueba independiente | Mutación que debe volverla roja |
|---|---|
| Schema público e ingreso: tipos, finitud, límites y combinación de selectores | Aceptar booleanos/NaN, omitir selector o admitir índice ausente como cero |
| Dispatch positivo para los cinco selectores, item real en manos | Enviar `action_use`, cambiar selector, perder cursor/índice o pasar item nulo |
| PBO antiguo, anuncio malformado/ausente y respuesta incompatible: cero fallback | Quitar comprobación, aceptar solo versión o ignorar protocolo/identificador |
| Resultado público preserva cero ciclos, `already_placed=false`, terminal y duración | Poda por truthiness o pérdida de campos nuevos |
| Lease/run: sin lease, ajeno, idle, cercado y cuarentena | Clasificar como lectura o eludir gates |
| Cleanup dirigido: cancel viejo contra hold nuevo; abandono antes/después de entrega | Cancelar sin identidad o limpiar únicamente la cola |
| Regresión `action_use`: mismo dispatch, resultado y ausencia de supresión | Activar hold implícitamente o modificar falsos negativos actuales |

Ejercitar herramientas registradas, schemas de ingreso y runtime/broker, no solo helpers. Los asserts de texto sirven para cableado; no acreditan comportamiento Enforce.

Aceptación in-game, **multiplayer DayZ 1.29**, PBO v12 con hash/procedencia comprobados, lease y run propio:

- FenceKit: preparación idéntica, holograma válido, baseline completo de Fences e IDs. Sin hold: inicio seguido de cancelación y ninguna Fence nueva atribuible. Con hold: terminal finished, placement cliente verdadero y exactamente una Fence nueva atribuible en el área, sin actores competidores.
- Timeout de un segundo: flag restaurado, cierre local observado, ningún progreso completo ni Fence nueva. Repetir con valor anterior del flag `true`; debe restaurar `true`.
- Cancelación explícita del gestor, muerte/cambio de jugador y shutdown: una restauración y un terminal, sin contaminar ejecución posterior.
- Repeat: SimpleGroup T3 preparada sin autoizado eléctrico ni actores competidores; contar varios ciclos y terminar por timeout/cancelación.

El caso repeat está anclado en `S/scripts/4_World/actions/LFPG_ActionRaiseFlag.c:11,117-118`:

```text
[EXACT]
m_ActionData.m_ActionComponent = new CAContinuousRepeat(1.0);
float delta = config.m_FlagRaiseRatePerSecond;
flag.IncrementRaiseProgress(delta);
```

Comparar contador cliente con callbacks instrumentados del servidor y cambio de progreso/animación; no exigir igualdad cliente/servidor sin evidencia. Si SimpleGroup no puede cargarse mediante la policy del ciclo, usar una fixture repeat genérica con contador servidor independiente. Eso valida el mecanismo; **SimpleGroup queda INCONCLUSO**.

**6. Riesgos y límites**

La medición aportada valida el mecanismo para FenceKit; no prueba todos los mods. Overrides que omitan la ruta instrumentada pueden dejar ciclos sin observar. El flag afecta al gestor completo: escrituras concurrentes de otros mods y acciones humanas durante el hold requieren exclusión operativa.

Restaurar/interrumpir no deshace efectos ni demuestra cancelación remota con red perdida. `already_placed` y ciclos son evidencia cliente. Timeout puede coexistir con efectos tardíos.

No se ha ejecutado esta implementación, compilación ni aceptación. DayZ 1.30 necesita aceptación separada. El directorio consultado no expuso metadatos Git utilizables: se verificaron las anclas indicadas, no la atribución independiente del snapshot al commit declarado.