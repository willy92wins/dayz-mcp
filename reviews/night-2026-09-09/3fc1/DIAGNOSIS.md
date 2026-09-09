# 3fc1 ? cancelaci?n por input: mecanismo localizado, corrida no aislada

**Solo diagn?stico. Ninguna fuente modificada para este ticket.** El puente solicita una acci?n y devuelve antes de aceptaci?n/progreso; el camino vanilla puede cancelar inmediatamente una continua porque no hay input mantenido. El mecanismo est? en la fuente; atribuirle la corrida del ticket sigue siendo **[HIP?TESIS]**, porque no se han le?do trazas de estados de esa corrida ni se permite reproducirla.

## Hasta d?nde llega el bridge

`tools/dayz_mcp/server.py` (`action_use`) manda peer=client; `addon/scripts/5_Mission/MCPClientBridge.c:1903` encuentra clase, jugador y objetivo. Construye `ActionTarget(...,component=-1,...)` (`:1991`), comprueba Can (`:2023`) y llama `amc.PerformActionStart` (`:2030`). El resultado solo comprueba `GetRunningAction()!=null` (`:2034`) y devuelve `started=true` (`:2042`). No mantiene una operaci?n, no llama a SetIgnoreAutomaticInputEnd, no suministra hold y no espera `OnFinishProgressServer`.

El arranque multijugador real en `../scripts/4_world/classes/useractionscomponent/actionmanagerclient.c:598` puede quedarse `UA_AM_PENDING` (`:660`); el servidor reeval?a Can y juncture (`actionmanagerserver.c:142`) y puede rechazar (`:175`). Por eso el `started=1` medido no acredita siquiera aceptaci?n; no se debe equiparar a una barra avanzando en servidor. El cliente con ack aceptado ejecuta `action.Start` (`actionmanagerclient.c:80`). Ver el contrato completo en `../6157/ANSWER.md`.

## Qui?n debe avanzar la barra

Todas estas rutas son vanilla en `../scripts/4_world/classes/useractionscomponent/`:

1. `animatedactionbase.c:348`: `Start` crea/configura el callback de acci?n (`:355`).
2. `actions/actioncontinuousbase.c:39`: `InitActionComponent` crea el componente, lo inicializa, fija `UA_INITIALIZE` y registra eventos/CancelCondition (`:50`?`:54`).
3. `ActionContinuousBaseCB.CancelCondition` (`actioncontinuousbase.c:6`) entra cuando la animaci?n est? en LOOP o `m_inLoop`, fija `UA_PROCESSING` y llama `actionS.Do` (`:23`). `ActionExecStart` tambi?n activa `m_inLoop` mediante `OnAnimationEvent` (`:189`).
4. `AnimatedActionBase.Do` (`animatedactionbase.c:382`) solo progresa si `CanContinue` pasa (`:406`), con `ProgressActionComponent` (`:408`). Ese m?todo ejecuta el componente (`animatedactionbase.c:70`?`:74`).
5. Ejemplo temporal concreto, no todas las subclases: `actioncomponents/cacontinuoustime.c:29` acumula `player.GetDeltaT()` (`:43`); al alcanzar el tiempo llama `OnCompletePogress` (as? escrito en la API, `:53`) y devuelve `UA_FINISHED`.
6. `actioncomponents/cacontinuousbase.c:8` llama `action.OnFinishProgress`; `actions/actioncontinuousbase.c:243` deriva a `OnFinishProgressServer` solo en servidor (`:251`). Este es el punto que aplica los efectos de las acciones que lo sobrescriben.

No hace falta que la tool incremente manualmente una barra. La animaci?n/componente/m?quina de estados del motor lo hacen si el inicio se acepta y siguen siendo v?lidas las condiciones.

## Camino concreto de cancelaci?n compatible con lo medido

- `ActionInput` nace inactivo (`actioninput.c:45`); `OnActionStart` solo resetea la selecci?n (`:217`), no inventa un hold.
- En `AIT_CONTINUOUS`, `Update()` consulta `LocalHold`/`LocalHoldBegin` (`actioninput.c:125`?`:134`). `WasEnded()` es literalmente `!m_Active` (`:109`). `ContinuousDefaultActionInput` usa UADefaultAction (`:604`); `ContinuousInteractActionInput` usa UAAction (`:522`).
- `ActionManagerClient.InputsUpdate` (`actionmanagerclient.c:282`) llama `ai.Update` y, fuera del control quickbar, si `WasEnded()` y es input continuo y no se ignora el final autom?tico, llama `EndActionInput` (`:298`?`:300`). Bajo DEVELOPER, la c?mara libre que impide movimiento llama ResetInputsActions y evita InputsUpdate (`:105`?`:113`); el men? de inventario en `:117` solo cambia la b?squeda de targets cuando no hay acci?n actual.
- `EndActionInput` latchea `m_ActionInputWantEnd` (`:274`). `ProcessActionInputEnd` (`:359`) env?a `INPUT_UDT_STANDARD_ACTION_INPUT_END` (`:372`) y llama `EndInput` local (`:378`). El servidor recibe ese c?digo (`actionmanagerserver.c:88`) y acaba llamando tambi?n `EndInput` (`:291`).
- `ActionBase.EndInput` (`actionbase.c:791`) llama `OnEndInput`; la continua (`actions/actioncontinuousbase.c:135`) llama `callback.UserEndsAction` (`:142`), que ejecuta `m_ActionComponent.Cancel` (`:110`?`:114`). En CAContinuousTime, cancelar una acci?n finita devuelve `UA_CANCEL` (`cacontinuoustime.c:58`?`:70`), sin pasar por `OnCompletePogress`.

As? se puede arrancar, reportar started y cancelar antes del efecto. Aumentar wait_for a 45/60 segundos solo espera un efecto que esa acci?n ya no producir?.

## Por qu? no lo declaro causa ?nica

| Dimensi?n | Verificada / hip?tesis / no mirada | Discriminador m?nimo en la pr?xima corrida |
|---|---|---|
| Solicitud local | Verificada por c?digo; started aportado por el brief | Estado inicial y cambio de UA_AM_PENDING a ACCEPTED/REJECTED |
| Autocancelaci?n sin hold | Mecanismo verificado; [HIP?TESIS] para la corrida | Tipo input, IsActive/WasEnded, IsQBControl, EndInput cliente y servidor |
| Quickbar | Excepci?n verificada | `CanBePerformedFromQuickbar` puede activar IsQBControl (`actionmanagerclient.c:607`, `playerbase.c:4813`), que elude esa rama |
| C?mara libre DEVELOPER | Rama verificada, estado real no mirado | Si se ejecut? ResetInputsActions o InputsUpdate (`:105`?`:113`) |
| Target, CanContinue y animaci?n | Candidatos, sin medida | Can en servidor, component=-1, entrada en LOOP, deltaT y estado UA_PROCESSING |
| Clase exacta del mod | No suministrada/no localizada para esta corrida | Ver su callback y componente; un componente infinito no tiene final temporal |
| PBO cargado | No verificado por prohibici?n de consultar/alterar sesi?n | Comparar hash del PBO y c?digo instrumentado antes del repro |

`ActionBase.CanBePerformedFromQuickbar()` tiene su propia l?gica (`actionbase.c:309`), y `PlayerBase.SetActionEndInput` fija el control seg?n el input (`playerbase.c:4813`). Por ello ?ninguna continua puede terminar por MCP? es demasiado fuerte: las subclases/quickbar pueden cambiar el resultado. La evidencia del ticket confirma el caso medido seg?n quien lo abri?; falta aislar la clase y el primer estado divergente.

## Arreglo propuesto, NO implementado

[DESIGN] A?adir operaci?n cliente acotada para una continua, ligada a la identidad de acci?n/jugador/target y lease; observar aceptaci?n y fin/cancelaci?n, con TTL. La API existente `ActionManagerClient.SetIgnoreAutomaticInputEnd(bool state)` (`actionmanagerclient.c:1273`?`:1276`) desactiva precisamente el final autom?tico. Es candidata m?s directa que inyectar teclas, pero debe activarse exclusivamente mientras esa operaci?n es due?a de la acci?n y restaurarse en todos los finales: rechazo, timeout, disconnect, p?rdida del jugador, shutdown y final natural. No hay getter visible para restaurar el valor anterior: requiere dise?ar propiedad de ese flag, no ponerlo a false indiscriminadamente si otro consumidor lo usa.

Mantener `Can`, `CanContinue`, animaci?n, junctures y callbacks normales; NO invocar `OnFinishProgressServer` directamente ni simular ?xito adelantando el tiempo. Separar en contrato started/accepted/completed/cancelled y definir si se quiere una ?nica iteraci?n de acciones repetibles. Riesgos: input ignorado que se queda activo, acciones infinitas, aplicaci?n repetida del efecto, interferencia con input humano u otro mod, timeout de cliente antes de cancelaci?n en servidor.

Primero instrumentar los discriminadores anteriores y reproducir acci?n simple, continua temporal, rechazo de servidor y cancelaci?n por p?rdida de condici?n. Si la primera divergencia es rechazo/animaci?n, el flag no lo arreglar?. Todo ello necesita motor y est? prohibido en esta corrida. No se han ejecutado tests de fuente pretendiendo certificar el diagn?stico.
