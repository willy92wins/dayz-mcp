# Plan: adaptador opcional de Expansion AI (173a, 3ca5, 90f9 y el emote de NPC de 36fa)

**Fichas:** fb-20261023-235342-173a (ai_group_spawn), -3ca5 (ai_group_order), -90f9 (ai_group_state) y la mitad de NPC de -36fa (actor_emote sobre un NPC de Expansion). Proyecto DayZSandbox; las cuatro piden la tool al MCP.

**Decisión de Guillermo (2026-10-24 10:08Z, «Adaptador ya»):** un addon opcional aparte que solo llama a la API pública de Expansion AI, leída en su PC, y que se prueba en la ronda final.

**Fuente de la API:** el informe de Codex Sol `reviews\expansion-ai-api.md` del PC (59 076 B, sha256 2d0433bf…). Lo escribió sobre Expansion AI 2792982069 (ai_scripts.pbo 63da52cb) y Core 2291785308 (core_scripts.pbo b881f2b1), extraídos solo en `%TEMP%\expansion-ai-read`. Las citas `2792982069/…` y `2291785308/…` son de ese árbol; las de vanilla, de la 1.30.

**Licencia:** Expansion es CC BY-NC-ND 4.0 (`2792982069/ai_scripts/5_Mission/DayZExpansion_AI/Mission/MissionServer.c:8`). Ni el repo ni el PBO llevan código ni ficheros de Expansion: el adaptador solo llama a métodos públicos y nombra sus clases. No hay `modded class` de ninguna clase de Expansion. Si DayZSandbox puede usarlo en un producto comercial no lo decide este plan; la ficha 173a ya dice que el producto final usará una IA propia.

**Base:** main f8cf3b4, más tres PR que van antes en el tren:
- #262 (escenas): aporta el contador de ids `m_NextBatchObjectId` desde `BATCH_OBJECT_ID_BASE` (MCPBridge.c:46 de 796f25d) y `AddSceneEntry` (:861).
- #266 (actores): aporta la tool `actor_emote`.
- El PR del transporte: el fin del lease espera, con tope, a que el juego confirme o descarte lo entregado.

La rama sale de main cuando esos tres estén mergeados. Si tardan, sale de una integración local de sus cabezas y se trae main al mergear.

## 1. Qué entrega y qué no

Entrega:
- Tres tools nuevas: `ai_group_spawn`, `ai_group_order` y `ai_group_state`.
- Un parámetro nuevo, `object_id`, en `actor_emote`.
- Un PBO nuevo, `@DayZ_MCP_ExpansionAI`, que solo se carga junto a Expansion.
- Tres ganchos sin nada de Expansion en el PBO base.

No entrega, con su porqué:
- **`hold` de `ai_group_order`.** La ficha lo pone en la firma, pero no dice qué hace ni le da criterio. `behaviour="HOLD"` ya cubre «pararlo en su marca». Se dice en el cuerpo del PR, y DayZSandbox puede pedirlo con su significado.
- **Modo de combate.** No hay un `SetCombatMode` genérico. `eAI_SetPassive` no vuelve activo a un miembro de una facción pasiva (`2792982069/…/Entities/DayZPlayerImplement.c:372`, informe §3).
- **Seguir a un objeto.** No hay una orden pública de seguir un objeto cualquiera (informe §3).
- **Los NPC estáticos de Core** (`ExpansionNPCBase`, que no pasa por `eAIBase`; informe §6). El emote de NPC solo vale para los miembros que creó el adaptador.
- **Grupos persistentes o de patrulla.** Los grupos del adaptador acaban con su lease (§5).

## 2. Arquitectura

### 2.1 Dos PBO

- **El base, `DayZ_MCP`, recibe solo tres ganchos virtuales.** No lleva ningún tipo de Expansion ni un `#ifdef`. Un símbolo que falta no se arregla con una comprobación en tiempo de ejecución (informe §7). Y el orden de carga entre el base y Expansion no está fijado, así que un `#ifdef EXPANSIONMODAI` en el base sería frágil.
  1. `protected string ServerCapabilities()` devuelve `SERVER_CAPABILITIES`. La petición del poll (MCPBridge.c:268) pasa a llamarlo.
  2. `protected string ExtensionPollParams()` devuelve `""`. Se añade al final de la petición del poll, después de `&ach=` (:269).
  3. `protected int DispatchExtension(MCPCommand command, MCPResult result)` devuelve 0 si no conoce el comando, 1 si contestó y hay que publicar ya, y 2 si lo publicará un job. `Dispatch` (:469) lo llama justo antes de `unknown_command` (:637). Con 0 sigue dando `unknown_command`, como hoy.
- **El adaptador, `DayZ_MCP_ExpansionAI`, es una carpeta nueva del repo: `addon_expansion_ai/`.** Lleva `$PBOPREFIX$`, `config.cpp` y `scripts/5_Mission/`.
  - Su `CfgPatches` tiene `requiredAddons[] = {"DayZ_MCP", "DayZExpansion_AI_Scripts"}` (`2792982069/ai_scripts/config.cpp:3`, :8). Así compila después de los dos.
  - Sin Expansion, el juego no arranca con él cargado: falla cerrado en la carga.
  - Su `CfgMods` copia la forma del base (`addon/config.cpp`), con `dependencies[] = {"Mission"}` y solo `missionScriptModule`.
  - Hace `modded class MCPBridge`, `modded class MCPArgs` y `modded class MCPResult`, que son clases del propio MCP.
  - El adaptador vive en 5_Mission. Desde ahí llama a las clases de 4_World y 3_Game de Expansion.
- **Por qué los campos van en `modded class MCPArgs` y no en el base:** sin el adaptador, el base no lleva campos de Expansion. Hay que medir que `JsonSerializer.ReadFromString` (MCPBridge.c:290) rellena los campos de una clase moddeada. Si no, los campos van al base, que siguen sin tipos de Expansion (§8, pregunta 1).

### 2.2 Cuándo responde `expansion_unavailable`

Sin el adaptador, ningún verbo de Expansion llega al juego:
- El adaptador añade sus nombres al censo: `ServerCapabilities()` devuelve el del base más `,ai_emote,ai_group_order,ai_group_release,ai_group_spawn,ai_group_state`. `parse_poll_caps` no pide orden (loopback.py:263-287 de fe16c68, la cabeza de #266). El tope es de 64 nombres: el base tiene hoy 34 y con el lote llega a unos 44.
- Además anuncia `&xai=<16 hex>`, el hash de su propio contrato de args. Se calcula como `server_arg_contract_hash` (bridge_readiness.py), sobre un `EXPANSION_AI_ARG_CONTRACT` aparte. `SERVER_ARG_CONTRACT` y el `ach=` del base no cambian.
- En el broker, los cinco verbos entran en la admisión por capacidad (`_capability_refusal_locked`, loopback.py:3416 de fe16c68). Se piden al encolar y al entregar:
  - sin el verbo en el censo → `expansion_unavailable`, con `detail: "adapter_not_loaded"`;
  - con un `xai` que no sea `EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH` → `expansion_unavailable`, con `detail: "adapter_contract_mismatch"`. Es un PBO del adaptador viejo junto a un base nuevo, o al revés.
- En `_compare_bridge_capabilities` (bridge_readiness.py:356 de fe16c68), los cinco son opcionales: si no se anuncian, el estado sigue en `match`. Una partida sin Expansion no se pone en rojo.

### 2.3 Ids

- **`group_id` y el `object_id` de cada miembro salen de `m_NextBatchObjectId`, el contador del bridge de #262.** Cada miembro se registra en `m_RuntimeObjects`, así que `object_inspect`, `object_delete` y `scene_entities` funcionan con ellos sin cambios. Los ids del broker vuelven a empezar con cada daemon (lo vio la ronda 6 de #266); este contador es del bridge y no se repite en una misión.
- **`spawn_token`:** la tool genera 32 hex aleatorios por cada `ai_group_spawn` y los manda en los args. El release los nombra (§5). Un release de un daemon nuevo nunca coincide con un grupo de otro, y el broker no necesita el `group_id` para soltar un spawn que se abandonó tras entregarlo.
- **El registro del adaptador** guarda por grupo:
  - el token, el `group_id`, el `eAIGroup`, el `scene_id` y el comportamiento pedido;
  - el límite de velocidad que tenía antes de un HOLD;
  - y por miembro, su `object_id`, la referencia y la última observación (pos, salud, vivo).
  - Los muertos salen de la lista activa del grupo a una privada sin getter (`2792982069/…/Classes/eAIGroup.c:23`, :999), así que hace falta este registro propio (informe §4).

## 3. Verbos del adaptador

Cada uno cambia el mundo solo en su dispatch. Los jobs solo esperan y publican, que es la regla de los jobs del bridge. Antes de crear o cambiar nada se valida todo; un error no deja nada a medias.

### 3.1 `ai_group_spawn`

**Args:** `faction`, `count`, `pos`, `loadout`, `formation`, `behaviour`, `waypoints`, `scene_id` y `spawn_token`.

**En el dispatch, en este orden, sin crear nada hasta el punto 6:**
1. `eAIFaction.GetType(faction)` (`2792982069/…/Factions/eAIFaction.c:124`). Si no existe, `bad_args` con `field: "faction"` y `allowed`: los nombres registrados en `eAIRegisterFaction.s_FactionTypes` (`2792982069/ai_scripts/1_Core/DayZExpansion_AI/eAIRegisterFaction.c:3`). Nunca se cae a una facción por defecto; `Create` puede dar null (:129), y entonces da `spawn_failed`.
2. `formation`: si `eAIFormation.GetType(formation)` (`…/Formations/eAIFormation.c:123`) no la conoce, `bad_args` con las 11 de esta versión. `Create` caería a Vee sin avisar (:130).
3. `loadout`: si llega vacío, el de `GetDefaultLoadout()` de la facción (`eAIFaction.c:47`). Si `ExpansionPrefab.Load(loadout)` (`2291785308/…/Prefab/ExpansionPrefab.c:12`) da null, `bad_args` con `field: "loadout"`.
4. El tope total: los miembros vivos del registro más `count` no pueden pasar de 24, el `AI_TOTAL_MAX`. Si pasan, `ai_limit_reached` con los dos números. El tope por grupo, de 1 a 8, lo mira la tool (§4). Expansion pide más de 35 FPS de servidor de media y se degrada por encima de 30 IA (ficha 173a).
5. Si el `spawn_token` ya está en el registro, es una entrega repetida del mismo comando: contesta lo del grupo registrado y no crea nada.
6. **Crea:**
   - el grupo con `eAIGroup.CreateGroup(eAIFaction.Create(faction))` (`eAIGroup.c:123`), con `m_Persist = false` (:32);
   - `count` miembros con `GetGame().CreateObject(eAISurvivor.GetQuasiRandom(), p_i, …)` (vanilla `3_game/global/game.c:691`; `…/Entities/AI/eAISurvivor.c:27`). `p_i` es `pos` más un anillo de 1,5 m, pasado por `ExpansionAISpawnBase.GetPlacementPosition` (`…/Settings/Patrols/ExpansionAIPatrolBase.c:126`). Los flags de `CreateObject` son los que usa el helper de Expansion, una línea que Codex confirma (§8, pregunta 2);
   - por miembro: `ExpansionHumanLoadout.Apply(member, loadout)`, guardando su bool (`2291785308/…/Loadout/ExpansionLoadout.c:3199`); `member.SetGroup(group)` (`eAIBase.c:3926`; `AddMember` solo no basta, informe §2); y `eAI_SetLootingBehavior(eAILootingBehavior.NONE)` (`eAIBase.c:1890`), para que el saqueo no saque al actor de su marca;
   - la formación y el comportamiento (§3.2).
   - Si un `CreateObject` da null, se borra lo creado (`eAI_Despawn`, `eAIBase.c:2156`, y `group.Delete()`, `eAIGroup.c:204`) y contesta `spawn_failed`.
7. Registra el grupo y cada miembro en `m_RuntimeObjects` y, con `scene_id`, en `AddSceneEntry`.

**Respuesta, leída del servidor después de crear y nunca un eco:**
- `group_id`;
- la facción, de `group.GetFaction().GetName()` (`eAIGroup.c:671`, `eAIFaction.c:34`);
- la formación, de `GetFormation().GetName()`;
- `behaviour`: el valor en el vocabulario de la tool y su nombre en Expansion;
- y `members`. Cada miembro trae su `object_id`, su `type`, su `pos`, `loadout_applied` (el bool de `Apply`) e `inventory`: los tipos de sus attachments, 16 como mucho. Así lo leído sustituye al nombre del loadout, que no tiene getter (informe §2).

### 3.2 Correspondencia de `behaviour` (informe §3)

| Tool | Expansion | Y además |
|---|---|---|
| HOLD | `SetFormationState(NONE)` y `SetWaypointBehaviour(HALT)` (`eAIGroup.c:615`, :251) | `SetMovementSpeedLimits(STATIC)` por miembro, solo el límite normal (`eAIBase.c:5871`). HALT solo no basta: el líder aún va al waypoint actual (informe §3). Se guarda el límite anterior. |
| ONCE | `IN` y `ONCE` | Se restaura el límite que tenía antes de un HOLD. |
| LOOP | `IN` y `LOOP` | Ídem. |
| ROAM | `IN` y `ROAMING` | Ídem. ROAMING elige destinos de las localizaciones de Expansion, no solo los waypoints recibidos. La respuesta lo dice. |

Los waypoints: `ClearWaypoints()` (:241), `m_BackTracking = false` (:38, que `ClearWaypoints` no reinicia) y un `AddWaypoint(p)` por punto (:213).

### 3.3 `ai_group_order`

**Args:** `group_id` y los campos presentes de `behaviour`, `waypoints`, `formation` y `speed` (`walk`, `jog` o `sprint`, que pasan a `eAIMovementSpeed.WALK`, `JOG` o `SPRINT`; `AIenums.c:55`).

- Si `group_id` no está en el registro, o el grupo ya se soltó, `not_found` sin cambiar nada.
- Valida la formación como en el spawn, antes de aplicar ningún campo.
- Aplica solo los campos presentes. `speed` cambia el límite normal de cada miembro vivo; en un HOLD se guarda como el límite que se restaura después.
- La respuesta trae todo leído después del cambio: `behaviour`, `formation`, `formation_state`, el número de waypoints, `waypoint_index` (`m_CurrentWaypointIndex`, :37) y el límite de velocidad de cada miembro (`GetMovementSpeedLimit`, `eAIBase.c:5926`).
- Trae además `in_combat`, de `group.IsInCombat()` (`eAIGroup.c:1123`). Con `true`, añade `combat_may_override: true`: la FSM de combate puede ignorar la orden (ficha 3ca5; `eAIState_Fighting.c:18`).

### 3.4 `ai_group_state`

Es de solo lectura: no lleva lease ni cambia nada. Recibe `group_id` o `scene_id`; con `scene_id` lee todos los grupos de esa escena.

**Por miembro:**
- `object_id`;
- si su entidad existe: `alive` (`IsAlive`, vanilla object.c:517), `pos`, `health` (`GetHealth()`, :1062), `fsm_state` y `fsm_substate`. El estado sale de `GetFSM()`, `GetState()` y `GetName()`, y el subestado de `GetSubFSM()`; cada referencia puede ser nula (`eAIBase.c:1480`, `2291785308/…/FSM/ExpansionFSM.c:106`, `ExpansionState.c:49`, :54);
- si ya no existe: `gone: true`, con la última observación marcada como tal. Un miembro que se vio muerto sigue con `alive: false`.
- No usa `eAITarget.GetThreat` ni `GetPosition(false)`, que actualizan estado al leer (informe §4).

**Por grupo:** `group_id`, la facción, la formación, `formation_state`, `behaviour` (en las dos formas), `waypoint_index`, el número de waypoints, `current_waypoint` (`GetCurrentWaypoint`, :236), `in_combat`, `alive` y `members_total`.

**Errores:** un `group_id` que no existe da `not_found`; un grupo sin vivos da ok con `alive: 0`.

### 3.5 `ai_emote`, el comando que manda `actor_emote(object_id=…)`

**Args:** `object_id`, `emote` y `wait_s`.

- Si `object_id` no es un miembro vivo del registro, `bad_target`.
- Con `emote="end"`:
  - si el miembro está reproduciendo, `ServerRequestEmoteCancel()` (vanilla `4_world/classes/emotemanager.c:1275`);
  - si hay una petición pendiente, `Expansion_SetEmote(0)` la deja a cero (§8, pregunta 4);
  - contesta `finished` si había algo, y `no_emote_playing` si no.
- **Antes de pedir nada:** con `m_eAI_IsFightingFSM` (`eAIBase.c:71`), `group.IsInCombat()` o `GetThreatToSelf(true) > 0.2` (`eAIBase.c:2028`; es el umbral de la guarda de `eAIState_PlayEmote.c:13`), contesta `emote_rejected` y no pide el emote.
- **En el dispatch:** `Expansion_SetEmote(id, false, 0)` (`eAIBase.c:10862`). No se llama a `Expansion_PlayEmote`, que se saltaría la guarda (informe §6).
- **El job solo lee el `EmoteManager` del miembro** (`GetEmoteManager()`, vanilla `playerbase.c:1892`):
  - `started` cuando `m_bEmoteIsPlaying` (`emotemanager.c:127`) es cierto y `Expansion_GetCurrentGesture()` (`2291785308/…/ExpansionEmoteManager.c:49`) es el id pedido. `IsEmotePlaying()` no vale: cuenta también la petición pendiente (informe §6);
  - `finished`, con un `started` visto antes, cuando el miembro deja de reproducir;
  - al llegar a `wait_s` sin `started`, `emote_timed_out`, con `pending: true` si la petición sigue en pie. Con `started` sin acabar, contesta `started`, como #266. El job no cancela nada: lo hace `emote="end"`;
  - si el miembro muere o su entidad desaparece durante la espera, `emote_interrupted`.
- **Los ids de emote** son los de `EmoteConstants` (vanilla `3_game/constants.c:366`), con los mismos nombres que `ActorEmoteId` de #266.

### 3.6 `ai_group_release` (interno)

**Args:** `spawn_token`.

- Busca el grupo por token. Hace `eAI_Despawn()` de cada miembro registrado cuya entidad exista, vivo o muerto, y después `group.Delete()`. Los dos son diferidos (informe §5). Lo quita del registro, de `m_RuntimeObjects` y de su escena.
- No usa `ClearAI` (salta los `eAINPCBase` y no usa `autodelete`, `eAIGroup.c:1220`) ni `Admin_ClearAllAI` (:1208).
- Con un token desconocido, ok con `released: 0`: el spawn abandonado pudo no llegar a crear nada.
- Un `scene_clear` de #262 borra los miembros como cualquier objeto de su escena; el grupo queda vacío en el registro hasta su release.

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
- Con lease. Sin ningún campo, da `bad_args`.

**`ai_group_state(group_id=None, scene_id=None, timeout_s)`**
- Uno de los dos y no los dos. Sin lease, como `object_inspect`.

**`actor_emote(emote, wait_s=10.0, object_id=None, timeout_s)`**
- Sin `object_id`, igual que #266: el comando `actor_emote` va al cliente.
- Con `object_id`, el comando `ai_emote` va al servidor, con lease.
- La lista de emotes y los topes de `wait_s` son los de #266.

**En el broker:**
- un `_command_schema` por cada uno de los cinco comandos;
- `EXPANSION_AI_ARG_CONTRACT`;
- la admisión de §2.2;
- `peer_for_command`: `ai_emote` es de servidor y `actor_emote` sigue siendo de cliente.

## 5. Fin del lease y abandono

Los grupos son un efecto que dura: andan, patrullan y pelean.
- En `RELEASE_REGISTRY` (loopback.py:151 de #266) entra `("ai_group_spawn", "ai_group_release", lambda command_id, args: {"spawn_token": args["spawn_token"]})`. Un spawn que vence o se cancela tras entregarse se suelta.
- `ai_group_release` entra en `_OWNER_CLEANUP_COMMANDS` (:1115).
- `cleanup_owner` encola un release por cada spawn entregado en el lease, como `enqueue_survivor_drive_releases_for_lease` de #266 (:3549), y los cuenta en `cleanup.ai_group_release_enqueued`.
- **El PR del transporte va antes:** con él, el fin del lease espera, con tope, el resultado de esos releases. Si no llega, lo da como pendiente, y la recuperación lo distingue. Este PR usa ese mecanismo tal cual.

## 6. Criterios offline (R26), que corren en el CI sin el juego

**Python, con el broker real en memoria y el bridge traducido donde haga falta:**

| # | Criterio | Tipo |
|---|---|---|
| O1 | Sin `ai_group_spawn` en el censo, `ai_group_spawn` da `expansion_unavailable` (`adapter_not_loaded`) y no se encola ni se entrega nada. Ídem con los otros cuatro. | NEG |
| O2 | Con el censo pero un `xai` distinto, `expansion_unavailable` (`adapter_contract_mismatch`); con el `xai` bueno, se encola. | NEG/POS |
| O3 | `count` 0, 9, 1.5 o true dan `bad_args` y nada se encola; 1 y 8, sí. Behaviour `"hold"` en minúsculas o `"PATROL"` dan `bad_args` con los cuatro valores. 33 waypoints, uno con NaN y una `pos` de dos números dan `bad_args`. | NEG/POS |
| O4 | `ai_group_order` sin campos da `bad_args`; `ai_group_state` con los dos ids o con ninguno da `bad_args`. | NEG |
| O5 | Un `ai_group_spawn` entregado y abandonado (timeout o cancelación) encola `ai_group_release` con su `spawn_token`. Uno sin entregar no encola nada. | POS/NEG |
| O6 | Al soltar el lease, un release por cada spawn entregado de ese lease y ninguno de otro lease. `ai_group_release` pasa la autoridad del cleanup sin lease. | POS/NEG |
| O7 | Sin los cinco verbos en el censo, el estado de capacidades sigue en `match`. | POS |
| O8 | Sin `object_id`, `actor_emote` manda `actor_emote` al cliente, como #266. Con él, manda `ai_emote` al servidor, con lease. | POS |
| O9 | El hash del contrato del adaptador del `.c` es igual a `EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH`, y cada clave del contrato es un campo del `modded class MCPArgs`. | POS |

**Enforce traducido, con dobles de las firmas de la tabla del informe y la forma de los tests de #266:**

| # | Criterio |
|---|---|
| E1 | Facción desconocida: `bad_args` con la lista registrada y 0 llamadas a `CreateObject` ni a `CreateGroup`. Ídem con una formación desconocida (las 11) y con un loadout que no carga. |
| E2 | Con 20 vivos en el registro, `count` 5 da `ai_limit_reached` y 0 creados. |
| E3 | Un spawn bueno: `count` llamadas a `CreateObject`, un `SetGroup` por miembro con el grupo creado, `m_Persist` false, saqueo NONE y cada id en `m_RuntimeObjects`. La respuesta sale de lo que leen los getters del doble, no de los args: un doble que lee otra facción da esa otra. |
| E4 | Un `CreateObject` null a la mitad: los ya creados reciben `eAI_Despawn`, el grupo `Delete`, la respuesta es `spawn_failed` y el registro queda vacío. |
| E5 | HOLD: NONE, HALT y STATIC en cada miembro. Un LOOP posterior restaura el límite anterior. Una formación mala en el order no cambia nada, ni los otros campos de la misma llamada. |
| E6 | Release: despawn de los vivos y de los muertos que existen, `Delete` del grupo, fuera del registro y de la escena. Un token desconocido da `released: 0`. |
| E7 | Estado: un miembro sin entidad sale `gone`, con la última observación. Uno muerto, `alive: false`. Un grupo sin vivos da ok con `alive: 0`. Ninguna lectura llama a un setter (el doble falla si se llama a uno). |
| E8 | Emote: en combate o con amenaza mayor que 0,2, `emote_rejected` y 0 llamadas a `Expansion_SetEmote`. En reposo, una sola llamada, en el dispatch. El job no llama a nada que cambie el mundo. `started` pide el gesto y `m_bEmoteIsPlaying`; con solo la petición pendiente, `emote_timed_out` con `pending: true`. |
| E9 | Sin el adaptador, `Dispatch` de `ai_group_spawn` da `unknown_command`, el poll del base no cambia byte a byte y su censo sigue igual. |

**Mutantes:** al menos uno por criterio, aplicado en su propia copia. Por ejemplo: quitar la validación de la facción, caer a Vee, no restaurar el límite tras un HOLD, que el release se salte los muertos, y que el job del emote llame a `Expansion_SetEmote`.

## 7. Ronda final (en el juego)

**Preparación.** Los pasos 1 y 2 tocan carpetas de Guillermo, así que necesitan su palabra para la ronda; ninguno resella.
1. En `!Workshop` (`P:\Mods`), las carpetas vacías `@DayZ-Expansion-Core` y `@DayZ-Expansion-AI` pasan a ser junctions a `content\221100\2291785308` y `\2792982069`, más las dependencias que diga BW3 (ModStorage y Dabs, `DF_Scripts`). Se deshace volviendo a dejar las carpetas vacías.
2. El PBO del adaptador se construye con el AddonBuilder de P: en `P:\Mods\@DayZ_MCP_ExpansionAI\Addons`. El worker gestionado solo construye y despliega el mod del proyecto (`dayz_test_worker.py:1731`).
3. `dayz_test_run` lleva `extra_mods` con las dependencias, `@DayZ-Expansion-Core` y `@DayZ-Expansion-AI`, que servidor y cliente cargan con el mismo `-mod` (`dayz_test_worker.py:509-514`); el cliente necesita las clases de los eAI. El adaptador va en `server_mods` (`-serverMod`), porque solo corre en el servidor. Cambiar el conjunto rota storage_1 (mundo y personajes nuevos).

**Pasos.** P es un punto llano a unos 20 m del jugador. Cada respuesta se ata a su id de comando.
1. Sin el adaptador cargado, en una partida normal, `ai_group_spawn(…)` da `expansion_unavailable`, y `entities_query` en P no da ningún eAI.
2. (173a) `ai_group_spawn(faction="Raiders", count=4, pos=P, formation="Column", behaviour="LOOP", waypoints=[W1, W2, W3])`, con W1 a 25 m. En menos de 1 s, `ai_group_state` da 4 vivos a menos de 5 m de P. Antes de 60 s, `waypoint_index` 1 o más y un miembro a 3 m o menos de W1.
3. (173a) `count=1`, ONCE y un waypoint a 15 m. Llega a 1,5 m o menos, y dos `object_inspect` separados 10 s distan menos de 0,5 m.
4. (173a) La facción de la respuesta es `Raiders`, leída del grupo; `loadout_applied` es true e `inventory` no está vacío.
5. (173a NEG) `faction="Nope"` da `bad_args` con la lista; `formation="Nope"`, con las 11; `count=9`, `bad_args` en la tool. Tras los tres, `entities_query` en P da los mismos eAI que antes.
6. (3ca5) `ai_group_order(behaviour="HOLD")`, sin combate: cada miembro se mueve menos de 1 m en 10 s.
7. (3ca5) `ai_group_order(behaviour="ONCE", waypoints=[un punto a 50 m del centroide])`: el centroide se acerca 20 m o más en 30 s.
8. (3ca5 NEG) Con un `group_id` que no existe, `not_found`. Con `formation="Nope"`, `bad_args`, y `ai_group_state` da lo mismo antes y después.
9. (90f9) La `pos` de cada miembro de `ai_group_state` está a menos de 0,5 m de su `object_inspect`.
10. (90f9) Matar a un miembro con `exec_enforce`, con `SetHealth(0)` sobre el eAI más cercano a su posición. La lectura siguiente lo da `alive: false`. Matar a los demás: ok con `alive: 0`.
11. (36fa NPC) Con un grupo en HOLD, sin amenazas y con el jugador cerca, `actor_emote(object_id=M, emote="nod", wait_s=10)` da `started` y `finished`. Si el NPC está en `Dormant` y no arranca, ese es el hallazgo (informe §7).
12. (36fa NPC NEG) En combate, con un grupo de una facción que lo ataque a 30 m (el par lo da Codex, §8 pregunta 9), `actor_emote(object_id=M, emote="nod")` da `emote_rejected` o `emote_timed_out`, nunca `finished`.
13. El fin del lease va el último: `session_release`. La respuesta trae `cleanup.ai_group_release_enqueued` igual al número de grupos y nada del adaptador en `cleanup_degraded`. Antes de 5 s, `object_inspect` de cada miembro da borrado y `ai_group_state` da `not_found`.

**Aprobado:** del 1 al 13 como se dicen. Se resuelven 173a, 3ca5 y 90f9, y 36fa si #266 también salió bien.

## 8. Preguntas para la revisión de Codex

Codex las contesta leyendo el árbol de `%TEMP%\expansion-ai-read` y la vanilla.
1. ¿`JsonSerializer.ReadFromString` y `WriteToString` ven los campos de un `modded class MCPArgs` o `MCPResult` de otro addon? Si no se puede afirmar leyendo, ¿basta una sonda en la ronda final, o mejor los campos en el base?
2. ¿Con qué flags crea `SpawnAIEx` (`eAICommandManager.c:376`) el superviviente con `CreateObject`, en concreto `init_ai`? Basta una línea.
3. ¿HALT con NONE y el límite STATIC dejan quieto al grupo fuera de combate, o hay otro estado de la FSM que lo mueva (el seguimiento de la formación sube el límite, informe §3)? ¿Y qué hace el `pSpeedUnderThreat = -1` por defecto de `SetMovementSpeedLimits`: deja el de amenaza como estaba o lo iguala al normal?
4. ¿`Expansion_SetEmote(0)` deja la petición pendiente a cero, o hace falta otra vía?
5. ¿`eAI_Despawn` sobre un cadáver de eAI es seguro, o el cadáver se borra con `Object.Delete`?
6. ¿Hay algo en la creación por piezas (sin `SpawnAIEx`) que el helper haga y aquí falte, como el registro en algún gestor? Solo el nombre del paso.
7. ¿Un `IsAlive` y un `GetHealth` vanilla sobre un eAI muerto antes de su borrado dan lo esperado?
8. ¿Algo del plan contradice el informe o la regla de los jobs?
9. ¿Qué par de facciones se ataca sin configuración extra, para provocar el combate del paso 12?

## 9. Ficheros

- `addon/scripts/5_Mission/MCPBridge.c`: los tres ganchos y sus dos llamadas, sin nada de Expansion.
- `addon_expansion_ai/` (nuevo): `$PBOPREFIX$`, `config.cpp` y `scripts/5_Mission/MCPExpansionAI.c`.
- `tools/dayz_mcp/`:
  - `server.py`: las tres tools y `object_id` en `actor_emote`;
  - `loopback.py`: esquemas, admisión, `RELEASE_REGISTRY`, `_OWNER_CLEANUP_COMMANDS` y `cleanup_owner`;
  - `bridge_readiness.py`: el contrato del adaptador y los verbos opcionales en el censo.
- `tools/tests/`: los criterios de §6.
- Docs:
  - `README.md`: el recuento de tools, que sube en 3;
  - `CHANGELOG.md`: tras el lote estará cerca del tope de 76 800 B;
  - `PROJECT-MAP.md`;
  - `docs/expansion-ai-adapter.md`: cómo se construye y se carga, con la licencia.
- No toca ningún fichero sellado: ni los 21 de `packaged-modules.lock.json`, ni `approved-launchers.json`, ni el lanzador nativo. El contrato del PBO base solo crece en los tres ganchos. Lo mergea Guillermo, porque toca el PBO.
