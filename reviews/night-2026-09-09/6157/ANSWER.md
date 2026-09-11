# 6157 ? action_use ejecuta el ciclo cliente

La ruta es CLIENTE. Puede ejecutar c?digo de acci?n compilado bajo `#ifndef SERVER` porque llama al gestor del jugador local. No llama directamente a `OnExecuteClient`, y devolver `started=true` no prueba que ese callback haya ocurrido.

Descripci?n ampliada en `tools/dayz_mcp/server.py:5000`. No se ha modificado comportamiento.

## Camino completo le?do

1. Tool y peer: `tools/dayz_mcp/server.py:5020` valida los argumentos y llama `runtime.call_bridge("action_use", args, "client", ...)` en `:5045`. El mapa cliente lo anuncia en `tools/dayz_mcp/server.py:549`; whitelist/esquema del ingreso en `tools/dayz_mcp/loopback.py:92` y `:740`.
2. Capability y dispatch cliente: `addon/scripts/5_Mission/MCPClientBridge.c:182`, `:825`, `:1903`. Selecciona al jugador local (`:1914`), su `ActionManagerClient` (`:1922`), la clase Enforce (`:1938`), target sint?tico con component=-1 (`:1991`) y item en manos (`:2022`). Aplica `Can` (`:2023`).
3. Entrada real: `MCPClientBridge.c:2030` llama `PerformActionStart(action, actionTarget, heldItem, NULL)`. La definici?n existe fuera del bloque BOT en `../scripts/4_world/classes/useractionscomponent/actionmanagerclient.c:762`; en multijugador llama `ActionStart` en `:772`.
4. `ActionStart` (`actionmanagerclient.c:598`) ejecuta `SetupAction` (`:638`), serializa acciones multijugador no locales (`:650`) con `INPUT_UDT_STANDARD_ACTION_START` y `Send` (`:666`). Si usa acknowledgement queda `UA_AM_PENDING` (`:660`); sin ack ejecuta `action.Start` (`:670`); acciones locales ejecutan `Start` (`:677`).
5. El servidor valida nuevamente `pickedAction.Can` y `AddActionJuncture` en `../scripts/4_world/classes/useractionscomponent/actionmanagerserver.c:142`; env?a aceptaci?n/rechazo (`:159`, `:175`). En el cliente, el caso `UA_AM_ACCEPTED` del update (`actionmanagerclient.c:70`) vuelve a comprobar condiciones y arranca la acci?n. Por eso `started` puede observar el estado pendiente, a?n sin efecto.
6. Base del ciclo: `../scripts/4_world/classes/useractionscomponent/actionbase.c:737` llama `OnStart`, luego `OnStartServer` o `OnStartClient` seg?n `IsServer` (`:748`). Para acciones animadas, `ActionBaseCB.OnAnimationEvent` guarda el evento (`../scripts/4_world/classes/useractionscomponent/animatedactionbase.c:18`); `OnUpdate` (`:229`) llama `CheckAnimationEvent` (`:213`), que lo despacha; `AnimatedActionBase.OnAnimationEvent` (`:184`) llama `OnExecuteClient` en la rama no servidor (`:199`).
7. Resultado del puente: `MCPClientBridge.c:2034` comprueba solo `GetRunningAction()!=null`; `:2042` devuelve `started=true`. No espera ack ni anima la barra por s? mismo.

## Correcciones y l?mites

El s?mbolo que el brief llama `ActionBase.OnExecuteClient` se declara realmente en `AnimatedActionBase` (`animatedactionbase.c:179`), no en `ActionBase`. No est? rodeado de `#ifndef SERVER` en vanilla; la selecci?n de lado aqu? es en runtime. Un override del mod puede estar protegido por ese preprocesador.

La checklist de conocimiento contradice la fuente: `C:/Users/guill/ObsidianVault/AI/20_Knowledge/dayz-mod-implementation-checklists.md:28` afirma que el servidor no reeval?a `Can`; `actionmanagerserver.c:142` demuestra que s?. Prevalece la fuente. Corregir la nota es FUERA DE MI ALCANCE.

La alcanzabilidad de los callbacks queda decidida por c?digo. No se puede asegurar que TODA acci?n o un mod concreto alcance su evento, mantenga condiciones, reciba ack o termine. El defecto de continuas se diagnostica en 3fc1 sin corregirlo. Gate de motor propuesto: acci?n m?nima con contadores observables distintos en cliente/servidor, confirmar ambos y el efecto real; requiere lanzamiento, prohibido por este brief.
