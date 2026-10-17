# DayZ Enforce API — referencia para el harness de autotest

> **Fuente:** source vanilla bajo `<vanilla scripts root>`, DayZ 1.30.164078. El 2026-10-17 se
> reanclaron las citas desde la 1.29: 90 se movieron con el texto igual, y `CreateObject` y
> `CreateStaticObjectUsingP3D` ganaron `bool objSpawner = false`.
> **Nivel de verificación:** 122 APIs cosechadas por un Workflow de 27 agentes con pasada de
> verificación adversarial (cite-then-verify por subagente), **menos 4 descartadas por
> `#ifdef GAME_TEMPLATE`**, **más 12** de una 2ª ronda, **más** spot-check directo de Claude
> sobre las 5 afirmaciones load-bearing (leídas en el source con mis propios ojos — ver §Trampas).
> Catálogo: **128 símbolos únicos** (122 cosechadas − 4 `GAME_TEMPLATE` + 12 de 2ª ronda
= 130 brutas; 2 colisiones colapsan al deduplicar). El fichero además cita posiciones
en la prosa y algunas entradas comparten posición bajo otro nombre, por lo que lleva
**más citas `path:line` únicas (135) que entradas de catálogo**; no falta nada.
No sustituye a la skill `enforce-script-reference`; es un
> índice operativo para construir el harness. Generado 2026-06-06.

## Trampas verificadas (lo que casi contamina la referencia)

- **`#ifdef GAME_TEMPLATE` = no existe en DayZ.** `SpawnEntity` / `SpawnEntityTemplate` /
  `SpawnComponentTemplate` / `GetInputManager` viven dentro de `#ifdef GAME_TEMPLATE` en
  `scripts\2_gamelib\gamelib.c` (guard en la **línea 1**) -> **no compilan en un mod DayZ**. Son
  APIs estilo Enfusion/Reforger que se colaron por coincidencia de firma. *Verificado por mí
  (grep del guard).* Para spawnear usa `CreateObject`/`CreateObjectEx`/`CreateStaticObjectUsingP3D`
  (game.c). Para input, `GetUApi()` (uainput.c:279), **no** `GetInputManager`. `ScriptCamera`
  (scriptcamera.c) también es GAME_TEMPLATE -> la cámara scriptada va por `SetCameraEx`/`GetCamera`
  (enworld.c), no por ScriptCamera.
- **`GetGame()` es engine-injected:** no tiene línea de source (grep no lo encuentra). Lo
  script-visible es `g_Game` (dayzgame.c:4074) y `GetDayZGame()` (dayzgame.c:4076). Documentar
  como conocido, no esperar hit de grep.
- **Provenance de paths:** bajo el árbol hay mods de terceros con su propio `scripts\3_game`
  (CodeLock, ParagonStorage, tofu_vstorage_2, EFT_Barters...). Una cita relativa es ambigua; las
  firmas de la sección siguiente las re-verifiqué contra la raíz vanilla.

## Lo que el harness necesita (curado y verificado por Claude)

**Spawnear** (game.c):
- `CreateObjectEx(string type, vector pos, int iFlags, int iRotation = RF_DEFAULT)` -> game.c:703 (el que usa tu mission)
- `CreateObject(string type, vector pos, bool create_local=false, bool init_ai=false, bool create_physics=true, bool objSpawner=false)` -> game.c:691
- `CreateStaticObjectUsingP3D(...)` -> game.c:680 · `ObjectDelete(Object)` -> game.c:705
- `const int ECE_PLACE_ON_SURFACE = 1060` -> centraleconomy.c:38

**Conducir — script-driven, sin teclas SO** (car.c, verificado por mí):
- `void SetThrottle(float value)` -> car.c:191 · `void SetSteering(float value, bool unused0=false)` -> car.c:185
- `void SetBrake(float value, float unused0=0, bool unused1=false)` -> car.c:203 · `void SetHandbrake(float value)` -> car.c:209
- `void ShiftTo(int gear)` -> car.c:260 · `enum CarGear { REVERSE, NEUTRAL, FIRST, ... }` -> car.c:32
- `float GetSpeedometer()` -> car.c:102 (señal real de movimiento; el RPM sube aunque las ruedas estén bloqueadas)
- `void EngineStart()` -> car.c:233 · `void Fill(CarFluid fluid, float amount)` -> car.c:372
> Un coche recién spawneado suele estar con handbrake puesto y en NEUTRAL: soltar handbrake + `ShiftTo(FIRST)` antes de que `SetThrottle` tenga efecto.

**Cámara scriptada + medida** (enworld.c, verificado por mí):
- `void SetCameraEx(int cam, const vector mat[4])` -> enworld.c:56 — pone pose por matriz (posición+orientación de una)
- `void GetCamera(int cam, out vector mat[4])` -> enworld.c:59 — **lee la matriz 4x4 = pose medida y reproducible** (conserva roll, que `GetCurrentCameraDirection` pierde)
- `void SetCamera(int cam, vector origin, vector angle)` -> enworld.c:53 · `void SetCameraVerticalFOV(int cam, float fovy)` -> enworld.c:61 · `void SetCameraType(int cam, CameraType)` -> enworld.c:65
- Alternativa de alto nivel: `Camera` class con `LookAt(vector)` / `SetActive(bool)` -> camera.c:80 / camera.c:45

**Input por script — alternativa a teclas SO** (uainput.c, verificado por mí):
- `UAInputAPI GetUApi()` -> uainput.c:279 · `class UAInputAPI` -> uainput.c:205
- `UAInput` (uainput.c:33): `ForceEnable(bool)` :83 · `ForceDisable(bool)` :84 · `Supress()` :74 · `LocalPress()` :50 · `LocalHold()` :52 · `LocalValue()` :48
> Permite **forzar/suprimir una acción mapeada por script en el cliente** -> puede esquivar el
> "riesgo nuclear" del spike (raw input) para acciones bindeadas. Para conducir, `Car.SetThrottle/SetSteering` es aún más directo.

**Entrar al vehículo + saber si se sentó** (del crítico — clase confirmada por mí en human.c:711; métodos a confirmar en el spike):
- `class HumanCommandVehicle` -> human.c:711. Métodos citados: `IsGettingIn()` (false = montado), `GetVehicleSeat()` (==0 = conductor), `GetTransport()`
- `Human.GetCommand_Vehicle()` -> no-null solo mientras está sentado (predicado de completitud)

**Desactivar daño durante el test** (del crítico, no spot-checked):
- `Object.SetAllowDamage(bool)` -> object.c:1196

## Runbook: cosecha fiable de APIs DayZ (lecciones de esta ronda)

Refinamiento del proceso que produjo el crítico de completitud. Aplicar en la próxima cosecha:

1. **Comprobar `#ifdef` siempre.** Un match de firma por grep NO prueba disponibilidad en el build. Leer las líneas de alrededor para detectar un `#ifdef GAME_TEMPLATE` / `FEATURE_*` que lo envuelva; si está, descartar o marcar `CONDITIONAL` con el guard. *(Este proceso marcó 4 APIs GAME_TEMPLATE como VERIFIED hasta el spot-check.)*
2. **Derivar los símbolos de los VERBOS de la tarea.** "Conducir" exige mutadores (`SetThrottle`/`SetSteering`), no solo observadores (`EngineGetRPM`). Un dominio de acción que solo da getters es red flag -> re-escanear el mismo archivo. *(Faltaban todos los setters de conducción en la 1ª pasada.)*
3. **Capturar el predicado de completitud junto al comando.** Al coger `StartCommand_X`, coger también la clase `Get/Is/State` del mismo archivo (`HumanCommandVehicle.IsGettingIn/GetVehicleSeat`), o el harness no puede secuenciar pasos.
4. **Capturar el enum/typename de cada parámetro.** `GetFluidCapacity(CarFluid)` es incallable sin el enum `CarFluid`; `ShiftTo` sin `CarGear`. Auto-capturar la dependencia en la misma pasada.
5. **Grepear también la capa proto baja (`1_core/proto/*.c`).** Los primitivos del engine (`enworld.c` `SetCameraEx`/`GetCamera`) suelen ser la herramienta correcta y se pierden si solo buscas la clase de alto nivel.
6. **Allowlist de globals engine-injected.** `GetGame()`, `GetGame().GetMission()`... no tienen línea de source; inyectarlos de conocimiento curado, marcados "engine-injected".
7. **Pasada de merge por `file:line`.** Colapsar duplicados cross-dominio y forzar un único status; un delta de solo-whitespace NUNCA es MISMATCH. *(RPC game.c:1011 salió VERIFIED y MISMATCH a la vez.)*
8. **Paths absolutos a la raíz vanilla.** El árbol tiene mods de terceros con su propio `scripts\3_game`; un path relativo puede apuntar a un fork. Rechazar hits bajo subcarpetas de mod salvo que el mod sea el objetivo.
9. **Cruzar APIs runtime-sensibles con el tracker/foros.** Un proto en `scripts\` prueba que la firma existe, NO que funcione en el build. Para captura/render/I-O de pantalla/hardware, verificar contra el feedback tracker de Bohemia + foros antes de darlas por buenas; y verificar el artefacto resultante (no fiarse de una métrica indirecta). Caso: `MakeScreenshot` (proto.c:142) no-op en retail y diag (T165276). *(added 2026-06-06)*
10. **Grepear el USO, no solo la definición — cero uso en vanilla = red flag.** Un símbolo (enum, const, método) declarado en un proto puede NO estar bound al VM de script aunque el `.c` lo declare. Antes de citarlo `[EXACT]`, grepear el árbol vanilla (y prior-art real que funcione) por su **uso**; si NADIE lo usa, sospechar que no es script-accesible y verificarlo in-game antes de construir encima. Hay dos modos de fallo distintos: **no-op silencioso** (compila, no hace nada — `MakeScreenshot`) y **compile-blocking** (no existe en el VM → `Can't find variable` → tumba el módulo entero). Segundo caso del proyecto: `ERESTOPTION_READOPERATION/_CONNECTION` + `RestApi.SetOption` (restapi.c:32-33/:175, en ese orden: L32 es READOPERATION) dieron `Can't find variable` en runtime 1.29 y tumbaron el módulo Mission; tenían **cero uso en vanilla** y el prior-art RestApi que funciona (un mod de terceros con RestApi en produccion) tampoco los usa — la señal estaba ahí antes del test. *(added 2026-06-07)*

## Catálogo completo (machine-generated, exacto)

> 128 símbolos únicos verificados, agrupados por dominio. Firmas literales del source; paths relativos a la raíz vanilla. Excluidas 4 GAME_TEMPLATE (arriba).

### Spawn / entidades

- `CreateStaticObjectUsingP3D` — `proto native Object CreateStaticObjectUsingP3D(string p3dFilename, vector position, vector orientation, float scale = 1.0, bool createLocal = false, bool objSpawner = false)`  (scripts\3_game\global\game.c:680)
- `CreateObject` — `proto native Object CreateObject( string type, vector pos, bool create_local = false, bool init_ai = false, bool create_physics = true, bool objSpawner = false)`  (scripts\3_game\global\game.c:691)
- `CreateObjectEx` — `proto native Object CreateObjectEx( string type, vector pos, int iFlags, int iRotation = RF_DEFAULT )`  (scripts\3_game\global\game.c:703)
- `ObjectDelete` — `proto native void ObjectDelete( Object obj )`  (scripts\3_game\global\game.c:705)
- `ObjectDeleteOnClient` — `proto native void ObjectDeleteOnClient( Object obj )`  (scripts\3_game\global\game.c:706)
- `RemoteObjectDelete` — `proto native void RemoteObjectDelete( Object obj )`  (scripts\3_game\global\game.c:707)
- `RemoteObjectTreeDelete` — `proto native void RemoteObjectTreeDelete( Object obj )`  (scripts\3_game\global\game.c:708)
- `RemoteObjectCreate` — `proto native void RemoteObjectCreate( Object obj )`  (scripts\3_game\global\game.c:709)
- `RemoteObjectTreeCreate` — `proto native void RemoteObjectTreeCreate( Object obj )`  (scripts\3_game\global\game.c:710)
- `ObjectRelease` — `proto native int ObjectRelease( Object obj )`  (scripts\3_game\global\game.c:711)
- `CreatePlayer` — `proto native Entity CreatePlayer(PlayerIdentity identity, string name, vector pos, float radius, string spec)`  (scripts\3_game\global\game.c:332)
- `SelectPlayer` — `proto native void SelectPlayer(PlayerIdentity identity, Object player)`  (scripts\3_game\global\game.c:340)
- `ECE_PLACE_ON_SURFACE` — `const int ECE_PLACE_ON_SURFACE = 1060`  (scripts\3_game\ce\centraleconomy.c:38)
- `ECE_IN_INVENTORY` — `const int ECE_IN_INVENTORY = 787456`  (scripts\3_game\ce\centraleconomy.c:37)
- `ECE_FULL` — `const int ECE_FULL = 25126`  (scripts\3_game\ce\centraleconomy.c:41)

### Vehiculos (CarScript)

- `EngineGetRPM` — `proto native float EngineGetRPM();`  (scripts\3_game\vehicles\car.c:227)
- `EngineIsOn` — `proto native bool EngineIsOn();`  (scripts\3_game\vehicles\car.c:230)
- `EngineStart` — `proto native void EngineStart();`  (scripts\3_game\vehicles\car.c:233)
- `EngineStop` — `proto native void EngineStop();`  (scripts\3_game\vehicles\car.c:236)
- `WheelHasContact` — `proto native bool WheelHasContact( int wheelIdx );`  (scripts\3_game\vehicles\car.c:286)
- `WheelCount` — `proto native int WheelCount();`  (scripts\3_game\vehicles\car.c:345)
- `WheelCountPresent` — `proto native int WheelCountPresent();`  (scripts\3_game\vehicles\car.c:348)
- `GetFluidCapacity` — `proto native float GetFluidCapacity(CarFluid fluid);`  (scripts\3_game\vehicles\car.c:355)
- `GetFluidFraction` — `proto native float GetFluidFraction(CarFluid fluid);`  (scripts\3_game\vehicles\car.c:363)
- `Fill` — `proto native void Fill(CarFluid fluid, float amount);`  (scripts\3_game\vehicles\car.c:372)
- `CrewSize` — `proto native int CrewSize();`  (scripts\3_game\vehicles\transport.c:175)
- `CrewMember` — `proto native Human CrewMember( int posIdx );`  (scripts\3_game\vehicles\transport.c:187)
- `CrewDriver` — `proto native Human CrewDriver();`  (scripts\3_game\vehicles\transport.c:191)
- `CrewGetIn` — `proto native void CrewGetIn( Human player, int posIdx );`  (scripts\3_game\vehicles\transport.c:206)
- `StartCommand_Vehicle` — `proto native HumanCommandVehicle StartCommand_Vehicle(Transport pTransport, int pTransportPositionIndex, int pVehicleSeat, bool fromUnconscious = false);`  (scripts\3_game\human.c:1526)

### Camara

- `Camera.GetCurrentCamera` — `static proto native Camera GetCurrentCamera()`  (scripts\3_game\entities\camera.c:7)
- `Camera.GetCurrentFOV` — `static proto native float GetCurrentFOV()`  (scripts\3_game\entities\camera.c:13)
- `Camera.InterpolateTo` — `static proto native void InterpolateTo(Camera targetCamera, float time, int type)`  (scripts\3_game\entities\camera.c:24)
- `Camera.IsInterpolationComplete` — `static proto native bool IsInterpolationComplete()`  (scripts\3_game\entities\camera.c:29)
- `Camera.SetNearPlane` — `proto native void SetNearPlane(float nearPlane)`  (scripts\3_game\entities\camera.c:35)
- `Camera.GetNearPlane` — `proto native float GetNearPlane()`  (scripts\3_game\entities\camera.c:40)
- `Camera.SetActive` — `proto native void SetActive(bool active)`  (scripts\3_game\entities\camera.c:45)
- `Camera.EnableSmooth` — `proto native void EnableSmooth(bool enable)`  (scripts\3_game\entities\camera.c:50)
- `Camera.StopInterpolation` — `proto native void StopInterpolation()`  (scripts\3_game\entities\camera.c:55)
- `Camera.IsActive` — `proto native bool IsActive()`  (scripts\3_game\entities\camera.c:61)
- `Camera.SetFOV` — `proto native void SetFOV(float fov)`  (scripts\3_game\entities\camera.c:67)
- `Camera.SetFocus` — `proto native void SetFocus(float distance, float blur)`  (scripts\3_game\entities\camera.c:74)
- `Camera.LookAt` — `proto native void LookAt(vector targetPos)`  (scripts\3_game\entities\camera.c:80)
- `FreeDebugCamera.GetInstance` — `static proto native FreeDebugCamera GetInstance()`  (scripts\3_game\entities\camera.c:90)
- `FreeDebugCamera.IsPlayerMove` — `proto native bool IsPlayerMove()`  (scripts\3_game\entities\camera.c:97)
- `FreeDebugCamera.SetFreezed` — `proto native void SetFreezed(bool freezed)`  (scripts\3_game\entities\camera.c:103)
- `FreeDebugCamera.IsFreezed` — `proto native bool IsFreezed()`  (scripts\3_game\entities\camera.c:109)
- `FreeDebugCamera.GetCrosshairObject` — `proto native Object GetCrosshairObject()`  (scripts\3_game\entities\camera.c:115)
- `DeveloperFreeCamera.FreeCameraToggle` — `static void FreeCameraToggle(PlayerBase player, bool teleport_player = false)`  (scripts\4_world\plugins\pluginbase\plugindeveloper\developerfreecamera.c:6)
- `DayZPlayer.GetCurrentCamera` — `proto native DayZPlayerCamera GetCurrentCamera()`  (scripts\3_game\dayzplayer.c:1234)
- `DayZPlayer.GetCurrentCameraTransform` — `proto native void GetCurrentCameraTransform(out vector position, out vector direction, out vector rotation)`  (scripts\3_game\dayzplayer.c:1237)
- `Game.GetCurrentCameraPosition` — `proto native vector GetCurrentCameraPosition()`  (scripts\3_game\global\game.c:731)
- `Game.GetCurrentCameraDirection` — `proto native vector GetCurrentCameraDirection()`  (scripts\3_game\global\game.c:732)

### Input

- `GetInputController` — `proto native HumanInputController GetInputController()`  (scripts\3_game\human.c:1458)
- `HumanInputController` — `class HumanInputController`  (scripts\3_game\human.c:17)
- `GetMovement` — `proto void GetMovement(out float pSpeed, out vector pLocalDirection)`  (scripts\3_game\human.c:25)
- `GetHeadingAngle` — `proto native float GetHeadingAngle()`  (scripts\3_game\human.c:28)
- `GetAimChange` — `proto native vector GetAimChange()`  (scripts\3_game\human.c:31)
- `OverrideMovementSpeed` — `proto native void OverrideMovementSpeed(HumanInputControllerOverrideType overrideType, float value)`  (scripts\3_game\human.c:253)
- `OverrideMovementAngle` — `proto native void OverrideMovementAngle(HumanInputControllerOverrideType overrideType, float value)`  (scripts\3_game\human.c:256)
- `OverrideAimChangeX` — `proto native void OverrideAimChangeX(HumanInputControllerOverrideType overrideType, float value)`  (scripts\3_game\human.c:259)
- `OverrideAimChangeY` — `proto native void OverrideAimChangeY(HumanInputControllerOverrideType overrideType, float value)`  (scripts\3_game\human.c:262)
- `GetInputInterface` — `proto native UAInterface GetInputInterface()`  (scripts\3_game\entities\man.c:19)
- `GetInput` — `proto native Input GetInput()`  (scripts\3_game\global\game.c:728)
- `Input` — `class Input`  (scripts\3_game\tools\input.c:10)
- `LocalValue` — `proto native float LocalValue(string action, bool check_focus = true)`  (scripts\3_game\tools\input.c:51)
- `LocalPress` — `proto native bool LocalPress(string action, bool check_focus = true)`  (scripts\3_game\tools\input.c:62)
- `StoreInputForRemotes` — `proto native void StoreInputForRemotes(ParamsWriteContext ctx)`  (scripts\3_game\dayzplayer.c:1304)

### Player / Mission

- `MissionServer::OnUpdate` — `override void OnUpdate(float timeslice)`  (scripts\5_mission\mission\missionserver.c:108)
- `MissionGameplay::OnUpdate` — `override void OnUpdate(float timeslice)`  (scripts\5_mission\mission\missiongameplay.c:287)
- `MissionServer::OnInit` — `override void OnInit()`  (scripts\5_mission\mission\missionserver.c:85)
- `MissionGameplay::OnInit` — `override void OnInit()`  (scripts\5_mission\mission\missiongameplay.c:102)
- `MissionServer::OnMissionStart` — `override void OnMissionStart()`  (scripts\5_mission\mission\missionserver.c:99)
- `MissionGameplay::OnMissionStart` — `override void OnMissionStart()`  (scripts\5_mission\mission\missiongameplay.c:193)
- `MissionServer::OnEvent` — `override void OnEvent(EventType eventTypeId, Param params)`  (scripts\5_mission\mission\missionserver.c:309)
- `MissionGameplay::OnEvent` — `override void OnEvent(EventType eventTypeId, Param params)`  (scripts\5_mission\mission\missiongameplay.c:757)
- `CGame::GetPlayers` — `proto native void GetPlayers(out array<Man> players)`  (scripts\3_game\global\game.c:945)
- `CGame::GetPlayer` — `proto native DayZPlayer GetPlayer()`  (scripts\3_game\global\game.c:944)
- `CGame::CreatePlayer` — `proto native Entity CreatePlayer(PlayerIdentity identity, string name, vector pos, float radius, string spec)`  (scripts\3_game\global\game.c:332)
- `CGame::SelectPlayer` — `proto native void SelectPlayer(PlayerIdentity identity, Object player)`  (scripts\3_game\global\game.c:340)
- `CGame::DisconnectPlayer` — `proto native void DisconnectPlayer(PlayerIdentity identity, string uid = "")`  (scripts\3_game\global\game.c:399)
- `PlayerBase::OnConnect` — `void OnConnect()`  (scripts\4_world\entities\manbase\playerbase.c:7623)
- `PlayerBase::OnDisconnect` — `void OnDisconnect()`  (scripts\4_world\entities\manbase\playerbase.c:7647)
- `Man::GetIdentity` — `proto native PlayerIdentity GetIdentity()`  (scripts\3_game\entities\man.c:21)
- `CGame::GetWorld` — `proto native World GetWorld()`  (scripts\3_game\global\game.c:931)
- `World::GetPlayerList` — `proto native void GetPlayerList(out array<Man> players)`  (scripts\3_game\global\world.c:8)

### RPC / Sync

- `RPC` — `proto native void RPC(Object target, int rpcType, notnull array<ref Param> params, bool guaranteed,PlayerIdentity recipient = null);`  (scripts\3_game\global\game.c:1011)
- `RPCSingleParam` — `proto native void RPCSingleParam(Object target, int rpc_type, Param param, bool guaranteed, PlayerIdentity recipient = null);`  (scripts\3_game\global\game.c:1013)
- `RPCSelf` — `proto native void RPCSelf(Object target, int rpcType, notnull array<ref Param> params);`  (scripts\3_game\global\game.c:1015)
- `RPCSelfSingleParam` — `proto native void RPCSelfSingleParam(Object target, int rpcType, Param param);`  (scripts\3_game\global\game.c:1016)
- `ScriptRPC` — `class ScriptRPC: ParamsWriteContext`  (scripts\3_game\gameplay.c:104)
- `ScriptRPC.Send` — `proto native void Send(Object target, int rpc_type, bool guaranteed,PlayerIdentity recipient = NULL);`  (scripts\3_game\gameplay.c:117)
- `ScriptRPC.Reset` — `proto native void Reset();`  (scripts\3_game\gameplay.c:109)
- `OnRPC` — `void OnRPC(PlayerIdentity sender, int rpc_type, ParamsReadContext ctx);`  (scripts\3_game\entities\object.c:860)
- `EntityAI.OnRPC` — `override void OnRPC(PlayerIdentity sender, int rpc_type, ParamsReadContext ctx)`  (scripts\3_game\entities\entityai.c:3507)
- `RegisterNetSyncVariableBool` — `proto native void RegisterNetSyncVariableBool(string variableName);`  (scripts\3_game\entities\entityai.c:2832)
- `RegisterNetSyncVariableBoolSignal` — `proto native void RegisterNetSyncVariableBoolSignal(string variableName);`  (scripts\3_game\entities\entityai.c:2840)
- `RegisterNetSyncVariableInt` — `proto native void RegisterNetSyncVariableInt(string variableName, int minValue = 0, int maxValue = 0);`  (scripts\3_game\entities\entityai.c:2850)
- `RegisterNetSyncVariableFloat` — `proto native void RegisterNetSyncVariableFloat(string variableName, float minValue = 0, float maxValue = 0, int precision = 1);`  (scripts\3_game\entities\entityai.c:2861)
- `RegisterNetSyncVariableObject` — `proto native void RegisterNetSyncVariableObject(string variableName);`  (scripts\3_game\entities\entityai.c:2869)
- `SetSynchDirty` — `proto native void SetSynchDirty();`  (scripts\3_game\entities\entityai.c:3064)
- `OnVariablesSynchronized` — `void OnVariablesSynchronized()`  (scripts\3_game\entities\entityai.c:3069)

### Admin / Dev

- `PluginDeveloper.GetInstance` — `static PluginDeveloper GetInstance()`  (scripts\4_world\plugins\pluginbase\plugindeveloper.c:26)
- `PluginDeveloper.TeleportAtCursor` — `void TeleportAtCursor()`  (scripts\4_world\plugins\pluginbase\plugindeveloper.c:32)
- `PluginDeveloper.Teleport` — `void Teleport(PlayerBase player, vector position)`  (scripts\4_world\plugins\pluginbase\plugindeveloper.c:38)
- `PluginDeveloper.SetDirection` — `void SetDirection(PlayerBase player, vector direction)`  (scripts\4_world\plugins\pluginbase\plugindeveloper.c:44)
- `PluginDeveloper.ToggleFreeCamera` — `void ToggleFreeCamera()`  (scripts\4_world\plugins\pluginbase\plugindeveloper.c:56)
- `PluginDeveloper.SpawnEntityOnGroundPos` — `EntityAI SpawnEntityOnGroundPos(PlayerBase player, string item_name, float health, float quantity, vector pos, bool special = false, bool withPhysics = false)`  (scripts\4_world\plugins\pluginbase\plugindeveloper.c:541)
- `PluginDeveloper.SpawnEntityOnCursorDir` — `EntityAI SpawnEntityOnCursorDir(PlayerBase player, string item_name, float quantity, float distance, float health = -1, bool special = false, string presetName = "", bool withPhysics = false)`  (scripts\4_world\plugins\pluginbase\plugindeveloper.c:572)
- `PluginDeveloper.SpawnEntityInInventory` — `EntityAI SpawnEntityInInventory(notnull EntityAI target, string className, float health, float quantity, bool special = false, string presetName = "", FindInventoryLocationType locationType = FindInventoryLocationType.ANY)`  (scripts\4_world\plugins\pluginbase\plugindeveloper.c:615)
- `PluginDeveloper.ClearInventory` — `void ClearInventory(EntityAI entity)`  (scripts\4_world\plugins\pluginbase\plugindeveloper.c:841)
- `DeveloperTeleport.TeleportAtCursorEx` — `static void TeleportAtCursorEx()`  (scripts\4_world\plugins\pluginbase\plugindeveloper\developerteleport.c:45)
- `DeveloperTeleport.SetPlayerPosition` — `static void SetPlayerPosition(PlayerBase player, vector position, bool breakSync = false)`  (scripts\4_world\plugins\pluginbase\plugindeveloper\developerteleport.c:115)
- `DiagMenu.RegisterBool` — `static proto void RegisterBool(int id, string shortcut, string name, int parent, bool reverse = false, func callback = null)`  (scripts\1_core\proto\endebug.c:280)
- `DiagMenu.GetBool` — `static proto bool GetBool(int id, bool reverse = false)`  (scripts\1_core\proto\endebug.c:322)
- `g_Game.CreateObjectEx` — `proto native Object CreateObjectEx(string type, vector pos, int iFlags, int iRotation = RF_DEFAULT)`  (scripts\3_game\global\game.c:703)

### Conduccion + camara-matriz + input (2a ronda)

- `Car.SetThrottle` — `proto native void SetThrottle(float value)`  (scripts\3_game\vehicles\car.c:191)
- `Car.SetSteering` — `proto native void SetSteering(float value, bool unused0 = false)`  (scripts\3_game\vehicles\car.c:185)
- `Car.SetBrake` — `proto native void SetBrake(float value, float unused0 = 0, bool unused1 = false)`  (scripts\3_game\vehicles\car.c:203)
- `Car.GetSpeedometer` — `proto native float GetSpeedometer()`  (3_game\vehicles\car.c:102)
- `Car::ShiftTo` — `proto native void ShiftTo(int gear)`  (scripts\3_game\vehicles\car.c:260)
- `CarGear` — `enum CarGear { REVERSE, NEUTRAL, FIRST, SECOND, THIRD, FOURTH, FIFTH, SIXTH, SEVENTH, EIGTH, NINTH, TENTH, ELEVENTH, TWELFTH, THIRTEENTH, FOURTEENTH, FIFTEENTH, SIXTEENTH }`  (scripts\3_game\vehicles\car.c:32)
- `SetCameraEx` — `void SetCameraEx(int cam, const vector mat[4])`  (scripts\1_core\proto\enworld.c:56)
- `GetCamera` — `proto native void GetCamera(int cam, out vector mat[4])`  (scripts\1_core\proto\enworld.c:59)
- `SetCamera` — `proto native void SetCamera(int cam, vector origin, vector angle)`  (scripts\1_core\proto\enworld.c:53)
- `SetListenerCamera` — `proto native void SetListenerCamera(int camera)`  (scripts\1_core\proto\enworld.c:45)
- `GetUApi` — `proto native UAInputAPI GetUApi()`  (scripts\3_game\inputapi\uainput.c:279)
- `UAInput` — `class UAInput { proto native float LocalValue(); proto native bool LocalPress(); proto native bool LocalHold(); proto native void ForceEnable(bool bEnable); proto native void ForceDisable(bool bEnable); proto native void Lock(); proto native void Unlock(); proto native void Supress(); }`  (scripts\3_game\inputapi\uainput.c:33)


