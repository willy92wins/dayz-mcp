# Plan: adaptador opcional de Expansion AI (173a, 3ca5, 90f9 y el emote de NPC de 36fa)

**Fichas:** fb-20261023-235342-173a (ai_group_spawn), -3ca5 (ai_group_order), -90f9 (ai_group_state) y la mitad de NPC de -36fa (actor_emote sobre un NPC de Expansion). Proyecto DayZSandbox; las cuatro piden la tool al MCP.

**Decisión de Guillermo (2026-10-24 10:08Z, «Adaptador ya»):** un addon opcional aparte que solo llama a la API pública de Expansion AI, leída en su PC, y que se prueba en la ronda final.

**Revisión 2 (2026-10-25):** cierra la revisión ROJA de Codex Sol sobre la revisión 1 (`BY-267-codex-sol-r1.md`, 28 908 B, sha256 ab8cffc7…): los hallazgos H1 a H14, las respuestas a las nueve preguntas, las tablas por criterio y por paso y las citas corregidas, más las notas del orquestador. §10 dice dónde cambió cada cosa.

**Fuente de la API:** el informe de Codex Sol `reviews\expansion-ai-api.md` del PC (59 076 B, sha256 2d0433bf…). Lo escribió sobre Expansion AI 2792982069 (ai_scripts.pbo 63da52cb) y Core 2291785308 (core_scripts.pbo b881f2b1), extraídos solo en `%TEMP%\expansion-ai-read`. Las citas `2792982069/…` y `2291785308/…` son de ese árbol; las de vanilla, de la 1.30.

**Licencia:** Expansion es CC BY-NC-ND 4.0 (`2792982069/ai_scripts/5_Mission/DayZExpansion_AI/Mission/MissionServer.c:8`). Ni el repo ni el PBO llevan código ni ficheros de Expansion: el adaptador solo llama a métodos públicos y nombra sus clases. No hay `modded class` de ninguna clase de Expansion. Si DayZSandbox puede usarlo en un producto comercial no lo decide este plan; la ficha 173a ya dice que el producto final usará una IA propia.

**Base:** main f8cf3b4, más tres PR que van antes en el tren. Cada cita MCP lleva la cabeza en la que se leyó. Desde entonces main va por 2e844c5 (#269) y #266 por b4f0611; las citas siguen en las cabezas que se nombran.
- #262 (escenas), 796f25d: el contador de ids `m_NextBatchObjectId` (MCPBridge.c:92, que :120 inicia en `BATCH_OBJECT_ID_BASE`, :46), `AddSceneEntry` (:861) y `SceneSpawnRefusal` (:814).
- #266 (actores), fe16c68: la tool `actor_emote` y la forma de su job.
- #268 (transporte), con su diseño en `plans/2026-10-24-transport-lease-end-barrier.md` de e1fdd66: FIFO en los dos bridges y una barrera al fin del lease (§5). Lo está revisando Codex; si su revisión cambia el contrato, el adaptador sigue el contrato final. Su bridge v13 obliga a traerlo a cada PR del lote que toque el PBO (línea 13), y este lo toca.

La rama sale de main cuando esos tres estén mergeados. Si tardan, sale de una integración local de sus cabezas y se trae main al mergear.

## 1. Qué entrega y qué no

Entrega:
- Tres tools nuevas: `ai_group_spawn`, `ai_group_order` y `ai_group_state`.
- Un parámetro nuevo, `object_id`, en `actor_emote`.
- Un PBO nuevo, `@DayZ_MCP_ExpansionAI`, que solo se carga junto a Expansion.
- Tres ganchos sin nada de Expansion en el PBO base.

No entrega, con su porqué:
- **`hold` de `ai_group_order`.** La ficha lo pone en la firma, pero no dice qué hace ni le da criterio. `behaviour="HOLD"` ya cubre «pararlo en su marca». Se dice en el cuerpo del PR, y DayZSandbox puede pedirlo con su significado.
- **Modo de combate.** No hay un `SetCombatMode` genérico. `eAI_SetPassive` (`2792982069/…/Entities/DayZPlayerImplement.c:372`) no vuelve activo a un miembro de una facción pasiva: el getter también mira la facción (:377; informe §3).
- **Seguir a un objeto.** No hay una orden pública de seguir un objeto cualquiera (informe §3).
- **Los NPC estáticos de Core** (`ExpansionNPCBase`, que no pasa por `eAIBase`; informe §6). El emote de NPC solo vale para los miembros que creó el adaptador.
- **Grupos persistentes o de patrulla.** Los grupos del adaptador acaban con su lease (§5).

## 2. Arquitectura

### 2.1 Dos PBO

- **El base, `DayZ_MCP`, recibe solo tres ganchos virtuales.** No lleva ningún tipo de Expansion ni un `#ifdef`. Un símbolo que falta no se arregla con una comprobación en tiempo de ejecución (informe §7). Y el orden de carga entre el base y Expansion no está fijado, así que un `#ifdef EXPANSIONMODAI` en el base sería frágil.
  1. `protected string ServerCapabilities()` devuelve `SERVER_CAPABILITIES`. La petición del poll (MCPBridge.c:268 de f8cf3b4) pasa a llamarlo.
  2. `protected string ExtensionPollParams()` devuelve `""`. Se añade al final de la petición del poll, después de `&ach=` (:269).
  3. `protected int DispatchExtension(MCPCommand command, MCPResult result)` devuelve 0 si no conoce el comando, 1 si contestó y hay que publicar ya, y 2 si lo publicará un job. `Dispatch` (:469) lo llama justo antes de `unknown_command` (:637). Con 0 sigue dando `unknown_command`, como hoy.
- **El adaptador, `DayZ_MCP_ExpansionAI`, es una carpeta nueva del repo: `addon_expansion_ai/`.** Lleva `$PBOPREFIX$`, `config.cpp` y `scripts/5_Mission/`.
  - Su `CfgPatches` tiene `requiredAddons[] = {"DayZ_MCP", "DayZExpansion_AI_Scripts"}` (`2792982069/ai_scripts/config.cpp:3`, :8). Así compila después de los dos.
  - Sin Expansion, el juego no arranca con él cargado: falla cerrado en la carga.
  - Su `CfgMods` sigue la forma del base, que declara World y Mission (`addon/config.cpp:21`, :29 de f8cf3b4). El adaptador declara solo `dependencies[] = {"Mission"}` y `missionScriptModule`: es una elección nueva, no una copia, porque vive entero en 5_Mission.
  - No usa defines. El `CfgMods` de Expansion no trae `defines[]` (BW3a): `EXPANSIONMODAI` es un `#define` de su preload (`2792982069/0_ai_preload/Common/DayZExpansion_AI_Defines.c:3`, :4). El diseño no cambia.
  - Hace `modded class` de `MCPBridge`, `MCPArgs`, `MCPResult` y `MCPJob`, que son clases del propio MCP.
  - Sus jobs son `MCPJob` de `m_Jobs` (MCPBridge.c:86 de 796f25d), con su `kind` y su `deadline_s` (:4963). Sobrescribe `IsJobReady`, `PostJobSuccess` y `PostJobTimeout` (:4976, :5097, :5167) para sus kinds y deja los demás al base. Son los jobs que espera la barrera de #268 (§5).
  - El adaptador vive en 5_Mission. Desde ahí llama a las clases de 4_World y 3_Game de Expansion.
- **Los campos van en los `modded class`, no en el base**, para que el base no lleve campos de Expansion. Leyendo no se puede afirmar que `JsonSerializer` los vea: `ReadFromString` y `WriteToString` son `proto` (vanilla `3_game/gameplay.c:49`, :68, :100; respuesta 1). Lo mide el paso 0 de la ronda, antes de crear nada (§7). Si falla, los campos van al base, sin tipos de Expansion; eso cambia el PBO base y vuelve a revisión.
- **Nombres de los campos.** Se reusan los del base con su tipo: `pos`, `object_id`, `speed` con `speed_set`, `emote` y `wait_s` (MCPMessages.c:51, :109, :164, :166, :215 y :216 de fe16c68), y `scene_id` (:233 de 796f25d). Ningún campo nuevo repite un nombre del base. Un valor que no case con el tipo de un campo del base podría tumbar el parseo del lote entero (MCPBridge.c:290-295 de f8cf3b4), y por eso `speed` viaja como número (§3.3). Lo que lee el adaptador va dentro de `ai_group` (`ai_groups` con `scene_id`), porque `MCPResult` ya tiene `state` (MCPMessages.c:1039 de fe16c68). El emote reusa `emote_state`, `started_after_s` y `finished_after_s` (:1179-1181) y añade `emote_pending`.

### 2.2 Cuándo responde `expansion_unavailable`

Sin el adaptador, ningún verbo de Expansion llega al juego:
- El adaptador añade sus nombres al censo: `ServerCapabilities()` devuelve el del base más `,ai_emote,ai_group_order,ai_group_release,ai_group_spawn,ai_group_state`. `parse_poll_caps` no pide orden y rechaza duplicados (loopback.py:263-287 de fe16c68). El tope es de 64 nombres (`POLL_CAPS_MAX_NAMES`, :252): el base tiene 34 en f8cf3b4 y con el lote llega a unos 44.
- Además anuncia `&xai=<16 hex>`, el hash de su propio contrato de args. Se calcula como `server_arg_contract_hash` (bridge_readiness.py:254 de fe16c68), sobre un `EXPANSION_AI_ARG_CONTRACT` aparte. `SERVER_ARG_CONTRACT` y el `ach=` del base no cambian.
- En el broker, los cinco verbos entran en `_CAPABILITY_ADMISSION_COMMANDS` (loopback.py:1100 de fe16c68). `_capability_refusal_locked` (:3416) corre al encolar (:3753) y al entregar (:4790). Hoy mira el censo, su frescura, la instancia y `ach`, y esos fallos siguen dando `bridge_capability_missing` o `arg_contract_mismatch`. Para los cinco añade dos comprobaciones detrás:
  - sin el verbo en el censo → `expansion_unavailable`, con `detail: "adapter_not_loaded"`;
  - con un `xai` ausente, mal formado o que no sea `EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH` → `expansion_unavailable`, con `detail: "adapter_contract_mismatch"`. Es un PBO del adaptador viejo junto a un base nuevo, o al revés.
  - Si algo cambia entre encolar y entregar, el comando se descarta al entregar y no llega al juego.
- En `_BRIDGE_COMMAND_TOOLS` (bridge_readiness.py:276 de fe16c68) entran los cinco del servidor, con `ai_emote` → `actor_emote` y `ai_group_release` → None, como el `survivor_drive_release` interno (:303). En `_compare_bridge_capabilities` (:356) son opcionales: si no se anuncian, el estado sigue en `match`. Una partida sin Expansion no se pone en rojo.

### 2.3 Ids y registro

- **`group_id` y el `object_id` de cada miembro salen de `m_NextBatchObjectId`** (MCPBridge.c:92 de 796f25d), como los de `world_spawn_batch` (:1034-1036). Se toman al registrar (§3.1, punto 9): primero el del grupo y después los de los miembros, en el orden en que se crearon. Un rechazo o un rollback no toman ninguno. Los ids del broker vuelven a empezar con cada daemon (lo vio la ronda 6 de #266); este contador es del bridge y no se repite en una misión.
- **Solo los miembros entran en `m_RuntimeObjects` y en `AddSceneEntry`** (MCPBridge.c:87 y :861 de 796f25d), que piden un `Object`. El `eAIGroup` no lo es (`2792982069/…/Classes/eAIGroup.c:3`) y vive solo en el registro del adaptador. Así `object_inspect`, `object_delete` y `scene_entities` funcionan con los miembros sin cambios, y `object_inspect` del `group_id` da `object_id_unknown` (MCPBridge.c:3430).
- **`spawn_token`:** la tool genera 32 hex aleatorios por cada `ai_group_spawn` y los manda en los args. El release los nombra (§5). Un release de un daemon nuevo nunca coincide con un grupo de otro, y el broker no necesita el `group_id` para soltar un spawn que se abandonó tras entregarlo.
- **El registro del adaptador** nunca borra un grupo, como las escenas de #262 (MCPBridge.c:89 de 796f25d). Guarda:
  - por grupo: el token, el `group_id`, el `eAIGroup` y su id de Expansion (`GetID()`, `eAIGroup.c:695`), el `scene_id`, el comportamiento pedido y un estado, `active` o `releasing`;
  - por miembro: su `object_id`, la referencia, sus dos preferencias de velocidad (§3.2) y la última observación (pos, salud, vivo);
  - los muertos salen de la lista activa del grupo a una privada sin getter (`eAIGroup.c:23`, :999), así que hace falta este registro propio (informe §4).
- Un grupo `releasing` del que ya no queda nada (§3.6) contesta `not_found` a todo.

## 3. Verbos del adaptador

Cada uno cambia el mundo solo en su dispatch. Los jobs solo leen y publican, que es la regla de los jobs del bridge: no cambian el mundo ni el registro, tampoco para consumir una petición de emote, y solo apuntan en sus propios campos, como el de #266 (MCPClientBridge.c:7085-7088 de fe16c68). Antes de crear o cambiar nada se valida todo; un error no deja nada a medias.

### 3.1 `ai_group_spawn`

**Args:** `faction`, `count`, `pos`, `loadout`, `formation`, `behaviour`, `waypoints` (tríos de números seguidos, como `pos`), `scene_id` y `spawn_token`.

**En el dispatch, en este orden, sin crear nada hasta el punto 8:**
1. **Repetición.** Si el `spawn_token` ya está en el registro, es una entrega repetida del mismo comando: no crea nada y contesta la lectura de ese grupo, como `ai_group_state` (§3.4), o `spawn_failed` con `created: true` si ese grupo está `releasing`. Va antes del tope, así que una repetición nunca da `ai_limit_reached`.
2. **Formas.** `spawn_token` de 32 hex, `count` de 1 a 8, `behaviour` de los cuatro de §3.2, `pos` de tres números y `waypoints` en tríos, 32 como mucho. Si no, `bad_args` con su `field`.
3. **Facción.** `eAIFaction.GetType(faction)` (`2792982069/…/Factions/eAIFaction.c:124`). Si no existe, `bad_args` con `field: "faction"` y `allowed`: los nombres que salen de los tipos registrados en `eAIRegisterFaction.s_FactionTypes` (`2792982069/ai_scripts/1_Core/DayZExpansion_AI/eAIRegisterFaction.c:3`). Nunca se cae a una facción por defecto. La instancia sale de `eAIFaction.Create(faction)` (`eAIFaction.c:129`); si es null, `spawn_failed` con `created: false`. Esa instancia es la que recibe `CreateGroup`.
4. **Formación.** Si `eAIFormation.GetType(formation)` (`…/Formations/eAIFormation.c:123`) no la conoce, `bad_args` con las 11 de esta versión (:5). `Create` caería a Vee (:130) tras dejar un error en el log (:139).
5. **Loadout.**
   - El nombre se normaliza una vez: sin un `.json` final. Vacío, es el `GetDefaultLoadout()` de la facción (`eAIFaction.c:47`), normalizado igual. Ese mismo nombre va a las dos comprobaciones y a `Apply`.
   - Existe: `FileExist` vanilla, que el bridge ya usa (MCPBridge.c:173 de f8cf3b4), de `<nombre>.json` en `$profile:ExpansionMod/Loadouts/` (informe §7). No depende de la caché de prefabs.
   - Se parsea: `ExpansionPrefab.Load(nombre, true)` (`2291785308/…/Prefab/ExpansionPrefab.c:12`), con la recarga forzada, para que no conteste un prefab que la caché guardó de un intento fallido.
   - Si falla una de las dos, `bad_args` con `field: "loadout"`.
6. **Escena.** `SceneSpawnRefusal(command.args, count)` (MCPBridge.c:814 de 796f25d), como el lote (:941): `bad_args` con un `scene_id` malo y `scene_full` si no caben `count` entradas más (2048, :38). Cuenta solo los miembros y corre en este dispatch, antes de crear (:810-813).
7. **Tope.** Los miembros vivos de todos los grupos del registro, también los `releasing`, más `count` no pueden pasar de 24, el `AI_TOTAL_MAX`. Si pasan, `ai_limit_reached` con los dos números. El tope por grupo también lo mira la tool (§4). Expansion pide más de 35 FPS de servidor de media y se degrada por encima de 30 IA (ficha 173a).
8. **Crea:**
   - el grupo con `eAIGroup.CreateGroup(<la instancia del punto 3>)` (`eAIGroup.c:123`), con `m_Persist = false` (:32);
   - `count` miembros con `GetGame().CreateObject(eAISurvivor.GetQuasiRandom(), p_i)` (vanilla `3_game/global/game.c:691`; `…/Entities/AI/eAISurvivor.c:27`), con los flags por defecto, como `SpawnAIEx` (`eAICommandManager.c:376`, llamada en :384): `init_ai` es false (respuesta 2). No es el `ECE_INITAI` que #262 pone a los infectados (MCPBridge.c:1012-1019 de 796f25d). `p_i` es `pos` más un anillo de 1,5 m, pasado por `ExpansionAISpawnBase.GetPlacementPosition` (`…/Settings/Patrols/ExpansionAIPatrolBase.c:126`), que no garantiza los 5 m;
   - por miembro: `ExpansionHumanLoadout.Apply(member, nombre, true)`, con la recarga forzada que pide el helper (`2291785308/…/Loadout/ExpansionLoadout.c:3199`; llamada en `eAICommandManager.c:385`; respuesta 6), guardando su bool; `member.SetGroup(group)` (`eAIBase.c:3926`; `AddMember` solo no basta, informe §2); `eAI_SetLootingBehavior(eAILootingBehavior.NONE)` (`eAIBase.c:1890`; `AIenums.c:15`), para que el saqueo no saque al actor de su marca; y sus dos preferencias de velocidad (§3.2);
   - la formación y el comportamiento (§3.2).
   - No hace falta registrar al miembro en ningún gestor: la inicialización del actor prepara lo del servidor (`eAIBase.c:503`; respuesta 6).
   - Si un `CreateObject` da null: `eAI_Despawn` de lo creado (`eAIBase.c:2156`) y `group.Delete()` (`eAIGroup.c:204`); el grupo entra en el registro como `releasing`, sin ids, y contesta `spawn_failed` con `created: true`. El release de su token confirma después que no queda nada (§3.6).
9. **Registra.** Toma los ids (§2.3) y mete cada miembro en `m_RuntimeObjects` y, con `scene_id`, en `AddSceneEntry`. El grupo va solo al registro del adaptador.

**Respuesta, dentro de `ai_group`, leída del servidor después de crear y nunca un eco:**
- `group_id` y `created: true`;
- la facción, de `group.GetFaction().GetName()` (`eAIGroup.c:671`, `eAIFaction.c:34`);
- la formación, de `GetFormation().GetName()` (`eAIGroup.c:903`, `eAIFormation.c:28`);
- `behaviour`: el valor en el vocabulario de la tool y su nombre en Expansion;
- y `members`. Cada miembro trae su `object_id`, su `type`, su `pos`, `loadout_applied` (el bool de `Apply`) e `inventory`: los tipos de sus attachments, 16 como mucho. El bool no prueba que todo el inventario se materializara, y el nombre del loadout no tiene getter (informe §2); por eso va también lo leído.

Cada rechazo antes de crear trae `created: false` y `args_read`: la facción, `count` y `waypoints` tal como los leyó el bridge. Lo usa el paso 0 de la ronda.

### 3.2 Correspondencia de `behaviour` (informe §3)

| Tool | Expansion | Límites de cada miembro |
|---|---|---|
| HOLD | `SetFormationState(NONE)` (`eAIGroup.c:615`; `AIenums.c:48`) y `SetWaypointBehaviour(HALT)` (`eAIGroup.c:251`). HALT solo no basta: el líder aún va al waypoint actual (informe §3). | `SetMovementSpeedLimits(STATIC, amenaza)` (`eAIBase.c:5871`) |
| ONCE | `IN` y `ONCE` | `SetMovementSpeedLimits(normal, amenaza)` |
| LOOP | `IN` y `LOOP` | Ídem. |
| ROAM | `IN` y `ROAMING`. ROAMING elige destinos de las localizaciones de Expansion, no solo los waypoints recibidos; la respuesta lo dice. | Ídem. |

**Las preferencias.** `normal` y `amenaza` son las dos preferencias de velocidad del miembro en el registro.
- Se pasan siempre los dos argumentos: con uno solo, el límite bajo amenaza se iguala al normal (respuesta 3).
- Se leen una vez, con `GetMovementSpeedLimit()` y `GetMovementSpeedLimitUnderThreat()` (`eAIBase.c:5926`, :5931), en el dispatch que crea al miembro. Después no se vuelven a leer de los getters, porque el seguimiento de la formación puede subir el normal durante un rato (`eAIBase.c:231`; `eAIState_FollowFormation.c:7`).
- `speed` (§3.3) cambia solo la preferencia `normal`. Un HOLD repetido no toca ninguna.
- HOLD no garantiza la inmovilidad. NONE impide entrar al seguimiento y al recorrido, pero el controlador aún puede imponer velocidad para esquivar un vehículo o por una zona de peligro (respuesta 3; `eAIBase.c:7091`). El punto P de la ronda no tiene ni lo uno ni lo otro (§7).

**Los waypoints:** `ClearWaypoints()` (`eAIGroup.c:241`), `m_BackTracking = false` (:38), un `AddWaypoint(p)` por punto (:213) y `SetCurrentWaypoint` (:231) al primero nuevo. `ClearWaypoints` no reinicia ni el sentido ni el destino actual (revisión §2), así que se fijan los dos.

### 3.3 `ai_group_order`

**Args:** `group_id` y los campos presentes de `behaviour`, `waypoints`, `formation` y `speed`. `speed` viaja en el `speed` del base, con `speed_set`, como 1, 2 o 3 (la forma de `player_move`, MCPMessages.c:186-187 de fe16c68), que pasan a `eAIMovementSpeed.WALK`, `JOG` o `SPRINT` (`AIenums.c:55`).

- Si `group_id` no está en el registro, o el grupo está `releasing`, `not_found` sin cambiar nada.
- Valida todo antes de aplicar ningún campo: la formación como en el spawn, `behaviour`, `waypoints` y `speed`.
- Aplica solo los campos presentes; la formación, con `SetFormation` (`eAIGroup.c:933`). `speed` cambia la preferencia `normal` de cada miembro vivo: fuera de un HOLD la aplica con `SetMovementSpeedLimits(normal, amenaza)`, y en un HOLD solo la guarda.
- La respuesta trae todo leído después del cambio: `behaviour` (`GetWaypointBehaviour`, `eAIGroup.c:304`), `formation` (`GetFormation`, :903), `formation_state` (`GetFormationState`, :624), el número de waypoints, `waypoint_index` (`m_CurrentWaypointIndex`, :37) y los dos límites de cada miembro (`eAIBase.c:5926`, :5931).
- Trae además `in_combat`, de `group.IsInCombat()` (`eAIGroup.c:1123`). Con `true`, añade `combat_may_override: true`: la FSM de combate puede ignorar la orden (ficha 3ca5; `eAIState_Fighting.c:18`).

### 3.4 `ai_group_state`

Es de solo lectura: entra en `READ_ONLY_COMMANDS` (§4), no lleva lease, no cambia el mundo y contesta en su dispatch, sin job, como #268 pide a una lectura (líneas 334-335). Recibe `group_id` o `scene_id`; con `scene_id` lee, en `ai_groups`, todos los grupos de esa escena.

**Por miembro:**
- `object_id`;
- si su entidad existe: `alive` (`IsAlive`, vanilla object.c:517), `pos` (`GetPosition`, :287), `health` (`GetHealth()`, :1062), `fsm_state` y `fsm_substate`. El estado sale de `GetFSM()`, `GetState()` y `GetName()`, y el subestado de `GetSubFSM()`; cada referencia puede ser nula (`eAIBase.c:1480`, `2291785308/…/FSM/ExpansionFSM.c:106`, `ExpansionState.c:49`, :54). `IsAlive` y `GetHealth` valen sobre un eAI muerto mientras exista su entidad (respuesta 7; `eAIBase.c:20`);
- también, para los fixtures y el emote: `threat` (`GetThreatToSelf(true)`, `eAIBase.c:2028`), `fighting` (`m_eAI_IsFightingFSM`, :71), los dos límites (:5926, :5931), `emote_playing` (`m_bEmoteIsPlaying`, vanilla emotemanager.c:127) y `emote_pending`: una petición en cualquiera de las dos capas, la de Expansion (`m_Expansion_EmoteID`, `eAIBase.c:347`) o la vanilla (`m_bEmoteIsRequestPending`, emotemanager.c:126);
- si ya no existe: `gone: true`, con la última observación marcada como tal. Un miembro que se vio muerto sigue con `alive: false`.
- No usa `eAITarget.GetThreat` ni `GetPosition(false)`, que actualizan estado al leer (informe §4).

**Por grupo:** `group_id`, la facción, la formación, `formation_state`, `behaviour` (en las dos formas), `waypoint_index`, el número de waypoints, `current_waypoint` (`GetCurrentWaypoint`, `eAIGroup.c:236`), `in_combat`, `alive`, `members_total` y, si está `releasing` y queda algo, `release: "pending"`.

**Errores:** un `group_id` que no existe, o el de un grupo ya soltado del todo, da `not_found`; un grupo sin vivos da ok con `alive: 0`.

### 3.5 `ai_emote`, el comando que manda `actor_emote(object_id=…)`

**Args:** `object_id`, `emote` y `wait_s`.

**Las capas.** La petición de Expansion (`m_Expansion_EmoteID`, `eAIBase.c:347`), la petición vanilla (`m_bEmoteIsRequestPending`, emotemanager.c:126) y la reproducción (`m_bEmoteIsPlaying`, :127) son tres cosas distintas (respuesta 4). Con `autoCancel=false`, el id de Expansion se queda tras el intento y puede volver a reproducir el gesto después de verse el cese (respuesta 8; `eAIBase.c:10869`). Con `autoCancel=true`, Expansion consume la petición en su propio intento, aunque no llegue a reproducir, y programa la cancelación de las poses estáticas a `autoCancelDelay` ms (informe §6; :10869). El adaptador usa la segunda: una sola petición se consume una sola vez, sin que nadie escriba después.

**En el dispatch, en este orden:**
1. Si `object_id` no es un miembro vivo de un grupo `active`, `bad_target`.
2. Un solo job de emote por miembro: con otro abierto sobre él, también para `end`, `emote_rejected`, como #266 (MCPClientBridge.c:6772-6774 y :6804 de fe16c68).
3. Con una petición de Expansion pendiente, o con `IsEmotePlaying()` cierto (emotemanager.c:1328, que cuenta la petición vanilla y la reproducción), `emote_rejected`, como #266 (MCPClientBridge.c:6844 de fe16c68). Así nada suena al pedir, y lo que vea el job después es suyo.
4. Con `m_eAI_IsFightingFSM` (`eAIBase.c:71`), `group.IsInCombat()` o `GetThreatToSelf(true) > 0.2` (`eAIBase.c:2028`; la guarda de `eAIState_PlayEmote.c:13`, con el umbral en :27), `emote_rejected` y no pide nada.
5. La única escritura: `Expansion_SetEmote(id, true, wait_s × 1000)` (`eAIBase.c:10862`). No se llama a `Expansion_PlayEmote`, que se saltaría la guarda (informe §6). El retraso solo cancela poses estáticas (§8, pregunta 1).

**El job solo lee el `EmoteManager` del miembro** (`GetEmoteManager()`, vanilla `playerbase.c:1892`) y su petición de Expansion:
- `started`: el primer tick con `m_bEmoteIsPlaying` cierto y `Expansion_GetCurrentGesture()` (`2291785308/…/ExpansionEmoteManager.c:49`) igual al id pedido. Es una transición nueva, porque al pedir no sonaba nada;
- `finished`: tras un `started`, el primer tick en que deja de reproducir;
- cada respuesta trae `started_after_s` y `finished_after_s`, contados desde la petición, y `emote_pending` (cualquiera de las dos capas) leído en ese tick, en la forma de #266 (MCPClientBridge.c:7121-7129 de fe16c68). `finished` con `emote_pending: false` dice que sonó una vez y que nada lo va a repetir;
- al llegar a `wait_s`: con `started` sin acabar, `started`, como #266; sin `started`, `emote_timed_out`, con `emote_pending`;
- si el miembro muere o su entidad desaparece durante la espera, `emote_interrupted`.
- Una petición que se queda pendiente la borra `emote="end"`, o el release del fin del lease al borrar al miembro.

**Con `emote="end"`**, tras los puntos 1 y 2:
- si reproduce: borra la petición de Expansion si la hay, con `Expansion_SetEmote(0)`; pide `ServerRequestEmoteCancel()` (vanilla `4_world/classes/emotemanager.c:1275`), que es una solicitud y no un cese; y un job lee hasta que deja de reproducir: `finished` con `finished_after_s`, o `emote_timed_out` a `wait_s` (#266, MCPClientBridge.c:7045-7046 de fe16c68);
- si no reproduce pero hay una petición de Expansion: `Expansion_SetEmote(0)` la deja a cero (respuesta 4) y contesta en el dispatch `emote_state: "cancelled"`, con `emote_pending` leído después de escribir;
- si solo queda la petición vanilla, que está en vuelo: `emote_rejected` con `emote_pending: true`, sin escribir nada; el `end` se puede repetir;
- si no hay nada: `no_emote_playing` (#266, MCPClientBridge.c:6827 de fe16c68).

**Los ids de emote** son los de `EmoteConstants` (vanilla `3_game/constants.c:366`; `ID_EMOTE_NOD` en :404), con los mismos nombres que `ActorEmoteId` de #266 (MCPClientBridge.c:6883 de fe16c68).

### 3.6 `ai_group_release` (interno)

**Args:** `spawn_token`.

**En el dispatch:**
- Con un grupo `active`: `eAI_Despawn()` de cada miembro cuya entidad exista, vivo o muerto, y `group.Delete()` (`eAIGroup.c:204`). Los dos son diferidos: `eAI_Despawn` acaba en el `Delete` diferido de `Object` (respuesta 5; `eAIBase.c:2156`; vanilla `3_game/entities/object.c:82`), y la limpieza de Expansion usa esa misma vía para sus muertos (`eAIGroup.c:1220`). Quita cada miembro de `m_RuntimeObjects` mientras su id siga nombrándolo, con `MCPBotControl.OnObjectGone`, como `scene_clear` (MCPBridge.c:1220-1223 de 796f25d), y pasa el grupo a `releasing`. Las entradas de su escena se quedan, y `scene_entities` las da borradas (:35-36, :1109-1110).
- Con un grupo ya `releasing` (un rollback o un release anterior): no vuelve a pedir nada.
- No usa `ClearAI` (salta los `eAINPCBase` vivos y no usa `autodelete`, `eAIGroup.c:1220`) ni `Admin_ClearAllAI` (:1208).
- Si ya no queda nada, contesta en el dispatch `released` con `confirmed: true`. Si queda algo, abre un job.

**El job de confirmación solo lee**, como `IsSceneClearDone` (MCPBridge.c:1358 de 796f25d): cada tick mira las referencias de los miembros y `eAIGroup.GetGroupByID(id, false)` (`eAIGroup.c:105`), que busca sin crear.
- Cuando no queda ninguna entidad y el grupo no aparece, contesta `released` con `confirmed: true`. La ausencia del grupo sola no basta (informe §5).
- Su plazo, `AI_RELEASE_CONFIRM_S`, es de 40 s: más que el tope de 36 s de la barrera de #268 (línea 355), para que la barrera nunca vea cerrado un job cuyo borrado sigue pendiente. Al vencer: `ok: false`, `error: "release_unconfirmed"` y los ids que quedan; el grupo sigue `releasing`.
- Mientras está abierto, sigue en `m_Jobs`, y la barrera lo espera (§5).

**Además:**
- Con un token desconocido, ok con `released: 0`: el spawn abandonado pudo no llegar a crear nada.
- Tras un `scene_clear` de #262, que borra los miembros como cualquier objeto de su escena, el release solo espera a que desaparezca el grupo.
- La respuesta va en `ai_group`.

## 4. Tools (Python)

**`ai_group_spawn(faction, count, pos, loadout, formation, behaviour, waypoints=[], scene_id="", timeout_s)`**, la firma de la ficha. Un `loadout` vacío es el de la facción.
- La tool solo valida las formas y su propio vocabulario:
  - `count`, entero de 1 a 8 (`AI_GROUP_COUNT_MAX`);
  - `pos`, tres números finitos;
  - `waypoints`, 32 como mucho, cada uno de tres números finitos;
  - `behaviour`, de HOLD, ONCE, LOOP y ROAM;
  - `faction` y `formation`, cadenas no vacías de 64 como mucho;
  - `loadout`, `[A-Za-z0-9_.-]`, 64 como mucho;
  - `scene_id`, con la regla de #262.
- La facción, la formación y el loadout los valida el servidor, que conoce los de verdad. Una lista fija en Python rechazaría una formación nueva de Expansion.
- Va al peer servidor con lease y genera el `spawn_token`.

**`ai_group_order(group_id, behaviour=None, waypoints=None, formation=None, speed=None, timeout_s)`**
- Con lease. Sin ningún campo, da `bad_args`. `speed` es `walk`, `jog` o `sprint`, y viaja como 1, 2 o 3 con `speed_set` (§3.3).

**`ai_group_state(group_id=None, scene_id=None, timeout_s)`**
- Uno de los dos y no los dos. Sin lease: `ai_group_state` entra en `READ_ONLY_COMMANDS` (session_coordination.py:21-51 de f8cf3b4). Todo comando que no esté ahí pide lease (`command_requires_lease`, :99, que `authorize` mira en :1343), y sin token da `lease_required` (:1388). #262 añade `scene_entities` igual (:51 de 796f25d).

**`actor_emote(emote, wait_s=10.0, object_id=None, timeout_s)`**
- Sin `object_id`, igual que #266: el comando `actor_emote` va al cliente.
- Con `object_id`, el comando `ai_emote` va al servidor, con lease.
- La lista de emotes y los topes de `wait_s` son los de #266 (loopback.py:1047-1085 y :1089-1090 de fe16c68). Con `object_id`, los estados son los de #266 más `cancelled`, y la respuesta trae además `emote_pending` (§3.5).

**En el broker:**
- un `_command_schema` por cada uno de los cinco comandos;
- `EXPANSION_AI_ARG_CONTRACT`;
- la admisión de §2.2;
- `peer_for_command`: `ai_emote` es de servidor y `actor_emote` sigue siendo de cliente.

## 5. Fin del lease y abandono

Los grupos son un efecto que dura: andan, patrullan y pelean.
- En `RELEASE_REGISTRY` (loopback.py:151-170 de fe16c68) entra `("ai_group_spawn", "ai_group_release", lambda command_id, args: {"spawn_token": args["spawn_token"]})`. Un spawn que vence o se cancela tras entregarse se suelta. Si su resultado ya estaba guardado, el abandono no encola nada (`_abandon_row_locked` solo publica el release de una fila entregada, :4315), y lo suelta el fin del lease.
- `ai_group_release` entra en `_OWNER_CLEANUP_COMMANDS` (loopback.py:1115 de fe16c68).

**El libro de tokens.**
- Al entregar un `ai_group_spawn`, el broker apunta su `spawn_token` bajo su lease, como `_record_delivered_drive_locked` (loopback.py:3527 de fe16c68, que se llama en :4868). Al encolar no: un spawn que la cola descarta no corre.
- Solo lo borra un resultado guardado con `ok: false` y `created: false`, en `store_result` (:5072), junto a `_note_hold_result_locked` (:5173). Un resultado descartado no lo borra: el release de un token desconocido no hace nada (§3.6).
- `cleanup_owner` (:5435) encola un `ai_group_release` por cada token que quede, como `enqueue_survivor_drive_releases_for_lease` (:3549), y los cuenta en `cleanup.ai_group_release_enqueued`. La salida temprana (:5500) también mira si quedan tokens.
- Un release que no se pudo encolar, y la cuarentena (:5503-5511), dan `ai_group_release_unconfirmed` en `cleanup_degraded`, como el drive (:5545-5551).
- Un token cuyo grupo ya soltó un abandono sigue en el libro. Al fin del lease, su release encuentra el grupo `releasing`: no pide nada y solo vuelve a mirar.

**Lo que usa de #268** (diseño de e1fdd66; si su revisión lo cambia, se sigue el contrato final):
- **FIFO** (líneas 324-329): un spawn corre antes que su release, y el release antes que la barrera. Los releases del abandono también corren después de su origen (452-454); sin FIFO, uno podría correr antes que su spawn y no encontrar nada, y el grupo duraría hasta el fin del lease.
- **La barrera, encolada la última** (370-376): `lease_barrier` contesta `ok` solo cuando el servidor no tiene ningún job abierto en `m_Jobs` (330-333, 353-354). El job de confirmación (§3.6) sigue en `m_Jobs` mientras falte algo, así que la barrera no puede contestar `ok` antes de que acabe.
- **El tope de 36 s y `jobs_open`** (355-356): un borrado atascado mantiene el job abierto más allá de ese tope, y la barrera contesta `jobs_open`. Eso es `lease_end_unconfirmed` en el `cleanup_degraded` del grant siguiente (436-445), y es lo cierto: los NPC siguen ahí.
- **`_lease_end_pending` sin bloqueo**, con su tope de 40 s (384-399): mientras tanto, `session_status` da `lease_end.state: confirming` (391-392) y `session_release` contesta `lease_end: confirming` (393).
- `ai_group_release_enqueued` y `ai_group_release_unconfirmed` tienen el sentido que #268 da a los `*_enqueued` y `*_unconfirmed` (448-449).
- #268 cuenta con jobs del servidor de 5 s como mucho (407-409). El adaptador trae dos más largos: el de `ai_emote`, de hasta `wait_s` (30 s), que cabe en el tope; y el de confirmación, que solo dura más de unos ticks si un borrado se atasca, y entonces acaba en `jobs_open` a propósito (§8, pregunta 5).

## 6. Criterios offline (R26), que corren en el CI sin el juego

**Python, con el broker real en memoria y el bridge traducido donde haga falta:**

| # | Criterio | Tipo |
|---|---|---|
| O1 | Sin el verbo en el censo, cada uno de los cinco da `expansion_unavailable` (`adapter_not_loaded`) al encolar y no se encola nada. Si el censo lo pierde entre encolar y entregar, se descarta al entregar (loopback.py:3753 y :4790 de fe16c68) y no llega al juego. Un censo ausente o viejo sigue dando `bridge_capability_missing`. | NEG |
| O2 | Con el censo y un `xai` ausente, mal formado o distinto, `expansion_unavailable` (`adapter_contract_mismatch`); con el bueno, se encola. Un `xai` que cambia entre encolar y entregar descarta al entregar. Las comprobaciones de instancia y de frescura siguen igual. | NEG/POS |
| O3 | Por la entrada real de cada tool: `count` 0, 9, 1.5 o true dan `bad_args` y nada se encola; 1 y 8, sí. Behaviour `"hold"` en minúsculas o `"PATROL"` dan `bad_args` con los cuatro valores. 33 waypoints, uno con NaN y una `pos` de dos números dan `bad_args`. | NEG/POS |
| O4 | `ai_group_order` sin campos da `bad_args`; `ai_group_state` con los dos ids o con ninguno da `bad_args`. `ai_group_state` pasa el `authorize` real (session_coordination.py:1343 de f8cf3b4) sin token, y otra vez después de `session_release`; en el mismo caso, un comando con lease da `lease_required` (:1388). | NEG/POS |
| O5 | Un `ai_group_spawn` entregado y abandonado (timeout o cancelación) encola `ai_group_release` con su `spawn_token`, detrás de su spawn en la cola del servidor. Uno sin entregar no encola nada, y uno cuyo resultado ya se guardó tampoco: lo suelta el fin del lease (O6). | POS/NEG |
| O6 | Al soltar el lease, un release por cada token que quede de ese lease, ninguno de otro lease y ninguno de un spawn que contestó `created: false`, con su `ai_group_release_enqueued`. El release pasa la autoridad real del cleanup sin lease (`_cleanup_dispatch_locked`, loopback.py:4416 de fe16c68). Con #268, la cola del servidor acaba en `["ai_group_release", "lease_barrier"]` y el resultado del release no basta para dar el lease siguiente (su T3, líneas 507-509 de e1fdd66). En cuarentena, `ai_group_release_unconfirmed`. | POS/NEG |
| O7 | Con el comparador real (bridge_readiness.py:356 de fe16c68) y las tres tools nuevas registradas: sin los cinco verbos en el censo, `match`; con ellos, también. | POS |
| O8 | Sin `object_id`, `actor_emote` manda `actor_emote` al cliente, como #266. Con él, manda `ai_emote` al servidor, con lease. El job del servidor lo prueba E8. | POS |
| O9 | El hash del contrato del adaptador del `.c` es `EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH`. Cada clave del contrato es un campo del `modded class MCPArgs` o uno heredado con su tipo (`pos`, `object_id`, `speed`, `speed_set`, `emote`, `wait_s` y `scene_id`). Ningún campo nuevo de `MCPArgs` o de `MCPResult` repite un nombre del base, ni se llama `state`. La lectura y la escritura reales las prueba el paso 0 (§7). | POS |

**Enforce traducido, con dobles de las firmas de la tabla del informe y la forma de los tests de #266.** Cada criterio conduce el tick real del bridge (`ProcessJobs`, MCPBridge.c:4938 de 796f25d) hasta el resultado o el plazo:

| # | Criterio |
|---|---|
| E1 | Facción desconocida: `bad_args` con la lista registrada, `created: false` y 0 llamadas a `CreateObject` ni a `CreateGroup`. Ídem con una formación desconocida (las 11). Loadout, con un doble cuyo `Load` cachea: dos peticiones seguidas del mismo nombre inexistente dan `bad_args` las dos; `X.json` y `X` llegan como el mismo nombre a `FileExist`, a `Load` y a `Apply`; un fichero que existe y no se parsea da `bad_args`. |
| E2 | Con 20 vivos en el registro, `count` 5 da `ai_limit_reached` y 0 creados. Con 24 vivos, repetir el token de un grupo ya creado contesta ese grupo, no `ai_limit_reached`. |
| E3 | Un spawn bueno: `count` llamadas a `CreateObject`, un `SetGroup` por miembro con el grupo creado, `m_Persist` false, saqueo NONE, cada miembro en `m_RuntimeObjects` y en la escena, y el grupo en ninguno de los dos. Con 2047 entradas en la escena, `count` 2 da `scene_full` y 0 creados; un `scene_id` malo, `bad_args`. La respuesta sale de lo que leen los getters del doble, no de los args: un doble que lee otra facción da esa otra. |
| E4 | Un `CreateObject` null a la mitad: los ya creados reciben `eAI_Despawn`, el grupo `Delete`, la respuesta es `spawn_failed` con `created: true` y el grupo queda `releasing`, sin ids en `m_RuntimeObjects`. El release de su token no confirma hasta que el doble los da por desaparecidos. |
| E5 | Con límites distintos en el doble (JOG y SPRINT): HOLD pone NONE, HALT y (STATIC, SPRINT) en cada miembro. Con el seguimiento activo (el doble sube el normal), un LOOP posterior pone (JOG, SPRINT). Dos HOLD seguidos y un LOOP, igual. `speed` en un HOLD no se aplica hasta salir. Una formación mala en el order no cambia nada, ni los otros campos de la misma llamada. |
| E6 | Release, con un doble de borrado diferido: despawn de los vivos y de los muertos que existen y `Delete` del grupo, en el dispatch. Mientras el doble sigue dando las entidades o el grupo, no hay respuesta y el job sigue en `m_Jobs`. Cuando desaparecen, `released` con `confirmed: true`. Si no desaparecen, a los 40 s `release_unconfirmed` con los ids que quedan, y el grupo sigue `releasing`. Un segundo release no pide nada. Un token desconocido da `released: 0`. El job no llama a ningún setter (el doble falla si se llama a uno). |
| E7 | Estado: un miembro sin entidad sale `gone`, con la última observación. Uno muerto, `alive: false`. Un grupo sin vivos da ok con `alive: 0`; uno `releasing` con algo pendiente, `release: "pending"`; uno ya soltado del todo, `not_found`. `threat`, `fighting`, los límites y las capas del emote salen de los getters del doble. Ninguna lectura llama a un setter. La consulta sin lease la prueba O4. |
| E8 | Emote, con dobles de las dos capas pendientes: en combate o con amenaza mayor que 0,2, `emote_rejected` y 0 llamadas a `Expansion_SetEmote`. Con otro job sobre el miembro, con un gesto ya en curso (también del mismo id) o con una petición en cualquiera de las dos capas, `emote_rejected`. En reposo, una sola llamada, `Expansion_SetEmote(id, true, …)`, en el dispatch. `started` pide el gesto, `m_bEmoteIsPlaying` y una transición posterior al dispatch; `finished`, un inicio visto y un cese, con sus dos tiempos. Si el doble no consume la petición y repite el gesto, `finished` sale con `emote_pending: true`. `end`: con solo la petición de Expansion, `cancelled`; con el gesto en curso, la cancelación y `finished` al ver el cese; sin nada, `no_emote_playing`. El job no llama a nada que cambie el mundo. |
| E9 | Sin el adaptador, `Dispatch` de `ai_group_spawn` da `unknown_command`, el poll del base no cambia byte a byte y su censo sigue igual. Compilar y cargar con y sin el adaptador lo cubre la ronda (corridas A y B). |

**Mutantes:** al menos uno por criterio, aplicado en su propia copia. Por ejemplo: quitar la validación de la facción, caer a Vee, no restaurar el límite tras un HOLD, que el release se salte los muertos y que el job del emote llame a `Expansion_SetEmote`. Y los cuatro que conservan las llamadas y falsean el resultado: un release que confirma con el borrado aún pendiente (E6), una validación que acepta el prefab que `Load` cacheó de un intento fallido (E1), un `started` que acepta el gesto que ya sonaba con el mismo id (E8) y un HOLD que pisa el límite bajo amenaza (E5).

## 7. Ronda final (en el juego)

**Preparación.** Los pasos 1, 2 y 4 tocan carpetas o la configuración del MCP de Guillermo, así que necesitan su palabra para la ronda; ninguno resella.
1. En `!Workshop` (`P:\Mods`), solo las carpetas vacías `@DayZ-Expansion-Core` y `@DayZ-Expansion-AI` pasan a ser junctions, a `content\221100\2291785308` y `\2792982069`. Core lleva también ModStorage (`modstorage_scripts.pbo`, con `requiredAddons` `JM_CF_Scripts`). `DF_Scripts` es Dabs Framework (2545327648; pide `DZ_Scripts` y `DF_GUI`, del mismo mod), y `@Dabs Framework` ya es junction, como `@CF` (1559212036); los dos van en los `base_mods` por defecto de LIVE. Se deshace volviendo a dejar vacías las dos carpetas.
2. El PBO del adaptador se construye con el AddonBuilder de P en una carpeta nueva, `P:\Mods\@DayZ_MCP_ExpansionAI\Addons`. El worker gestionado solo construye y despliega el mod del proyecto (`dayz_test_worker.py:1731` de f8cf3b4).
3. `dayz_test_run` lleva `extra_mods=["@DayZ-Expansion-Core", "@DayZ-Expansion-AI"]`, que servidor y cliente cargan con el mismo `-mod` (`dayz_test_worker.py:509-514`, :613 y :631 de f8cf3b4); el cliente necesita las clases de los eAI. `@Dabs Framework`, con su espacio, y `@CF` ya vienen en los `base_mods`. El adaptador va en `server_mods` (`-serverMod`, :620-624), porque solo corre en el servidor. Cambiar el conjunto rota storage_1 (mundo y personajes nuevos).
4. `exec_enforce`, que usa el paso 10, está apagado por defecto (server.py:774 y :8351 de f8cf3b4). Para la ronda, el MCP se registra con `--enable-exec-enforce --exec-allowlist <fichero>` (server_cli.py:559-560; el daemon los recibe por daemon_contract.py:37-41). El fichero, fuera del repo, es la lista JSON de `load_exec_allowlist` (core.py:297-306) con solo las dos expresiones del paso 10. Al acabar, el MCP vuelve a registrarse sin ellas.

La ronda usa los ajustes y los loadouts por defecto de Expansion (informe §7) y no los edita.

**Fixtures.** Las muestras van a una por segundo, cada una con su hora.
- **P:** un punto llano, sin edificios a 30 m ni zonas de peligro (respuesta 3). Antes del paso 2, `entities_query(pos=P, radius=30)` no da ningún vehículo ni ningún `eAI_*`. W1 está a 25 m de P, W2 a 25 m de W1 y W3 a 25 m de W2, en llano.
- **Reposo:** los grupos de los pasos 2 a 11 son de la facción `Fr`, que no ataca al jugador (§8, pregunta 2). En un paso en reposo, cada muestra da `in_combat: false` y, en cada miembro, `fighting: false` y `threat` de 0,2 o menos. Si una falla, el paso es INCONCLUSO.
- **Combate** (respuesta 9), después del paso 11 y en otro punto: un grupo Raiders y otro West, de 2 cada uno, a 30 m. Que sean hostiles no basta (`eAIFactionRaiders.c:9`, `eAIFactionWest.c:9`; `eAIBase.c:969`): se espera como mucho 60 s a que una muestra dé `in_combat` en uno de los dos grupos o `fighting` en un miembro. Si no, INCONCLUSO. El jugador va con `player_godmode`.

**Corridas.** Primero la A: una partida normal, sin Expansion ni el adaptador, que solo hace el paso 1. Después la B, con Expansion y el adaptador, que empieza por el paso 0 y no crea nada antes. En la B, los spawns usan `scene_id="xai"` y los negativos, `"xai_neg"`. Cada respuesta se ata a su id de comando.

**Pasos.**
0. (Sonda; respuesta 1) `ai_group_spawn(faction="Nope", count=2, pos=P, formation="Column", behaviour="HOLD", waypoints=[W1, W2], scene_id="xai_neg")` da `bad_args` con `field: "faction"`, un `allowed` no vacío y `args_read` igual a lo enviado. Prueba que el bridge lee los campos del `modded class MCPArgs` y escribe los del `MCPResult`, los anidados incluidos. `entities_query` en P no da ningún `eAI_*`. Si algo difiere, o no llega respuesta, la ronda para y se aplica la alternativa de §2.1.
1. (Corrida A) Antes de la llamada, `bridge_status` da el censo del servidor en `match` y sin ninguno de los cinco verbos. `ai_group_spawn(…)` da `expansion_unavailable`, y `entities_query` en P no da ningún `eAI_*`.
2. (173a) La primera creación de la corrida: `ai_group_spawn(faction=Fr, count=4, pos=P, formation="Column", behaviour="LOOP", waypoints=[W1, W2, W3])`. La primera muestra de `ai_group_state`, a menos de 1 s de que vuelva la tool, da 4 vivos a menos de 5 m de P. Antes de 60 s, una muestra da `waypoint_index` 1 o más y un miembro a 3 m o menos de W1.
3. (173a) Otro grupo, `count=1`, ONCE y un waypoint W a 15 m de P. Antes de 30 s su miembro llega a 1,5 m o menos de W. Después, dos `object_inspect(object_id=…, want=["inventory"])` separados 10 s, con sus horas, dan `telemetry.pos` a menos de 0,5 m entre sí.
4. (173a) En la respuesta del paso 2, la facción leída es `Fr` y cada miembro trae `loadout_applied: true`. El `object_inspect(want=["inventory"])` de cada miembro da attachments, con los tipos de su `inventory`.
5. (173a NEG) `faction="Nope"` da `bad_args` con la lista; `formation="Nope"`, con las 11; `loadout="NoSuchLoadout"`, dos veces seguidas, `bad_args` las dos; `count=9`, `bad_args` en la tool. Las respuestas del juego traen `created: false`, y `scene_entities("xai_neg")` da `[]`: no se creó nada.
6. (3ca5) Con el grupo del paso 2 en reposo, `ai_group_order(behaviour="HOLD")`. Diez muestras en 10 s: cada miembro está a menos de 1 m de su posición en la primera, y sus límites son STATIC el normal y, el de amenaza, el de antes del HOLD.
7. (3ca5) `ai_group_order(behaviour="ONCE", waypoints=[D])`, con D a 50 m del centroide. Se apuntan el centroide inicial, D, los miembros y las horas. En reposo, el centroide se acerca 20 m o más a D antes de 30 s.
8. (3ca5 NEG) Un `group_id` que no existe da `not_found`. `ai_group_order(formation="Nope", behaviour="HOLD", speed="sprint")` da `bad_args`. La configuración leída antes y después es la misma: `behaviour`, `formation`, `formation_state`, el número de waypoints y los dos límites de cada miembro. Las posiciones y la FSM no se comparan.
9. (90f9) Tras otro `ai_group_order(behaviour="HOLD")` y con cada miembro asentado (`telemetry.velocity` por debajo de 0,2 m/s): `ai_group_state` y el `object_inspect(want=["inventory"])` de cada miembro, a menos de 1 s, dan posiciones a menos de 0,5 m.
10. (90f9) M es 1000000002, el segundo miembro del grupo del paso 2 (grupo 1000000000, miembros 1000000001 a 1000000004); si el paso 2 dio otros ids, INCONCLUSO. Dos llamadas a `exec_enforce`, con `main_fn="scConsMain"` y estas dos expresiones exactas:
    - `void scConsMain() { PlayerBase p = PlayerBase.Cast(MCPBridge.Get().RuntimeObjectById(1000000002)); if (p) p.SetHealth(0); }`
    - `void scConsMain() { PlayerBase p; int i = 1000000001; while (i <= 1000000004) { p = PlayerBase.Cast(MCPBridge.Get().RuntimeObjectById(i)); if (p) p.SetHealth(0); i = i + 1; } }`

    Usan `MCPBridge.Get()` y `RuntimeObjectById` (MCPBridge.c:131 y :2665 de 796f25d) y el `SetHealth(0)` de `player_kill` (:2507); el eAI es un `PlayerBase` (`eAIBase.c:20`). `player_kill` no sirve: pide la identidad de un jugador (MCPBridge.c:2480-2492 de 796f25d). Tras la primera, M da `alive: false` y los otros tres, vivos. Tras la segunda, el grupo da ok con `alive: 0`. Un `exec_failed` (MCPBridge.c:2996-3000 de f8cf3b4) es INCONCLUSO.
11. (36fa NPC) Un grupo nuevo, `count=1`, en HOLD, en reposo y con el jugador a menos de 20 m; N es su miembro. `actor_emote(object_id=N, emote="nod", wait_s=10)` da `finished`, con `started_after_s` < `finished_after_s` ≤ 10 y `emote_pending: false`. En las cinco muestras siguientes, N da `emote_playing: false` y `emote_pending: false`: el gesto no se repite. Si N está en `Dormant` y no arranca, ese es el hallazgo (informe §7).
12. (36fa NPC NEG) Con el combate del fixture ya observado, `actor_emote(object_id=<un miembro de Raiders con fighting: true>, emote="nod")` da `emote_rejected` o `emote_timed_out`, nunca `finished`.
13. El fin del lease va el último: `session_release`. La respuesta trae `cleanup.ai_group_release_enqueued` igual al número de spawns que contestaron ok (los de los pasos 0 y 5 dieron `created: false`), nada del adaptador en `cleanup_degraded` y `lease_end: confirming`. Aprueba si después `session_status` deja `confirming` sin pasar a `unconfirmed` (#268, líneas 391-393 y 436-445). Entonces, ya sin lease: `scene_entities("xai")` da borrados todos los miembros, el `ai_group_state` de cada grupo da `not_found` y `entities_query`, en P y en el punto del combate, no da ningún `eAI_*`.

**Aprobado:** del 0 al 13 como se dicen, sin ningún INCONCLUSO. Se resuelven 173a, 3ca5 y 90f9, y 36fa si #266 también salió bien.

## 8. Preguntas abiertas para la revisión de Codex

Codex las contesta leyendo el árbol de `%TEMP%\expansion-ai-read` y la vanilla, salvo la 5, que es de #268.
1. ¿`Expansion_SetEmote(id, true, delay)` consume la petición en el intento y cancela solo las poses estáticas? ¿El retraso cuenta desde el intento o desde la petición (`eAIBase.c:10869`)? Si no es así, ¿qué vía pública consume una sola petición sin que el job escriba?
2. ¿Qué facción no ataca al jugador sin configuración extra, para `Fr`: `Passive`, `Civilian` u otra?
3. ¿El nombre normalizado de §3.1 es el que `Apply` carga tras su propia normalización (`ExpansionLoadout.c:3199`), y `ExpansionPrefab.Load(nombre, true)` da null con un fichero que no se parsea (`ExpansionPrefab.c:12`)?
4. ¿`eAIGroup.GetGroupByID(id, false)` da null tras el `Delete()` diferido, sin que otro grupo reuse ese id antes (`eAIGroup.c:105`, :204)?
5. (#268) ¿Cabe en su contrato un job del servidor de hasta 30 s (`ai_emote`) y otro que pasa del tope de la barrera solo cuando un borrado se atasca, y que entonces acaba en `jobs_open` (líneas 355-356 y 407-409)?

## 9. Ficheros

- `addon/scripts/5_Mission/MCPBridge.c`: los tres ganchos y sus dos llamadas, sin nada de Expansion.
- `addon_expansion_ai/` (nuevo): `$PBOPREFIX$`, `config.cpp` y `scripts/5_Mission/MCPExpansionAI.c`.
- `tools/dayz_mcp/`:
  - `server.py`: las tres tools y `object_id` en `actor_emote`;
  - `loopback.py`: esquemas, admisión, `RELEASE_REGISTRY`, `_OWNER_CLEANUP_COMMANDS`, el libro de tokens y `cleanup_owner`;
  - `bridge_readiness.py`: el contrato del adaptador, los cinco en `_BRIDGE_COMMAND_TOOLS` y su opcionalidad en el censo;
  - `session_coordination.py`: `ai_group_state` en `READ_ONLY_COMMANDS`.
- `tools/tests/`: los criterios de §6.
- Docs:
  - `README.md`: el recuento de tools, que sube en 3;
  - `CHANGELOG.md`: tras el lote estará cerca del tope de 76 800 B;
  - `PROJECT-MAP.md`;
  - `docs/expansion-ai-adapter.md`: cómo se construye y se carga, con la licencia.
- No toca ningún fichero sellado: ni los 21 de `packaged-modules.lock.json` (:3-23 de f8cf3b4; los cuatro módulos de arriba no están), ni `approved-launchers.json`, ni el lanzador nativo. El contrato del PBO base solo crece en los tres ganchos. Lo mergea Guillermo, porque toca el PBO.

## 10. Cambios de la revisión 2

| Qué | Dónde |
|---|---|
| H1, registro tipado | §2.3, §3.1 (punto 9), E3 |
| H2, lectura sin lease | §3.4, §4, §9, O4, paso 13 |
| H3, cleanup confirmado | §2.1 (jobs en `m_Jobs`), §2.3, §3.1 (punto 8), §3.6, §5, E4, E6, paso 13 |
| H4, loadout | §3.1 (punto 5), E1, paso 5, §8 (3) |
| H5, velocidades | §3.2, §3.3, E5, paso 6 |
| H6, consumo del emote | §3.5 (las capas y el punto 5), E8, paso 11, §8 (1) |
| H7, `end` | §3.5 (`end`), E8 |
| H8, exclusión y correlación | §3.5 (puntos 2 y 3, `started`), E8 |
| H9, oráculos de estado | pasos 8 y 9 |
| H10, reposo y combate | §3.4, fixtures de §7, pasos 2, 3, 6, 7, 11 y 12 |
| H11, positivo del emote | §3.4, §3.5, paso 11 |
| H12, contador del cleanup | §3.1, §5 (el libro de tokens), O5, O6, paso 13 |
| H13, repetición en el tope | §3.1 (punto 1), E2 |
| H14, tope de escena | §3.1 (punto 6), §3.6, E3 |
| Respuesta 1, serialización | §2.1, paso 0, O9 |
| Respuesta 2, `init_ai` | §3.1 (punto 8). La revisión 1 no dependía de `init_ai`: solo dejaba los flags a esta respuesta. |
| Respuesta 3, HOLD y amenaza | §3.2, fixture P, paso 6 |
| Respuesta 4, `Expansion_SetEmote(0)` | §3.5 |
| Respuesta 5, despawn de cadáveres | §3.6 |
| Respuesta 6, recarga forzada | §3.1 (puntos 5 y 8) |
| Respuesta 7, `IsAlive` y `GetHealth` | §3.4 |
| Respuesta 8, regla de los jobs | §3, §3.5, §3.6 |
| Respuesta 9, par hostil | fixture de combate, paso 12 |
| Citas corregidas (revisión §2) | cabecera, §1, §2.1, §2.2, §2.3, §3.1, §3.2, §3.3, §3.5, §7 |
| PR del transporte sin identificar | cabecera, §5, §8 (5) |
| Tabla por criterio | §2.2 (el mapa de comandos, por O7), §6 |
| Tabla por paso | §7, pasos 1 a 13 |
| Mutantes que faltaban | §6 |
| Paso 10, `exec_enforce` | §7 (preparación 4 y paso 10) |
| Campos heredados (O9) | §2.1, §3.1 (punto 2), §3.3, §4 |
| Notas del orquestador | cabecera (las cabezas nuevas), §2.1 (defines), §7 (preparación 1 a 3) |
