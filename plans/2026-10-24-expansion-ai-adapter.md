# Plan: adaptador opcional de Expansion AI (173a, 3ca5, 90f9 y el emote de NPC de 36fa)

**Fichas:** fb-20261023-235342-173a (ai_group_spawn), -3ca5 (ai_group_order), -90f9 (ai_group_state) y la mitad de NPC de -36fa (actor_emote sobre un NPC de Expansion). Proyecto DayZSandbox; las cuatro piden la tool al MCP.

**Decisión de Guillermo (2026-10-24 10:08Z, «Adaptador ya»):** un addon opcional aparte que solo llama a la API pública de Expansion AI, leída en su PC, y que se prueba en la ronda final.

**Revisión 2 (2026-10-25):** cierra la revisión ROJA de Codex Sol sobre la revisión 1 (`BY-267-codex-sol-r1.md`, 28 908 B, sha256 ab8cffc7…): los hallazgos H1 a H14, las respuestas a las nueve preguntas, las tablas por criterio y por paso y las citas corregidas, más las notas del orquestador. §10 dice dónde cambió cada cosa.

**Revisión 3 (2026-10-31):** sustituye el contrato de cierre de la revisión 2 por #268 @78f7919e y actualiza la base a main @de12fd6b. §11 separa los dos P1 conocidos de los dos P2 reconstruidos sin el texto original de Sol. Este PR entrega solo el plan; las pruebas y los cambios de código descritos son requisitos de la futura implementación.

**Fuente de la API:** el informe de Codex Sol `reviews\expansion-ai-api.md` del PC (59 076 B, sha256 2d0433bf…). Lo escribió sobre Expansion AI 2792982069 (ai_scripts.pbo 63da52cb) y Core 2291785308 (core_scripts.pbo b881f2b1), extraídos solo en `%TEMP%\expansion-ai-read`. Las citas `2792982069/…` y `2291785308/…` son de ese árbol; las de vanilla, de la 1.30.

**Licencia:** Expansion es CC BY-NC-ND 4.0 (`2792982069/ai_scripts/5_Mission/DayZExpansion_AI/Mission/MissionServer.c:8`). Ni el repo ni el PBO llevan código ni ficheros de Expansion: el adaptador solo llama a métodos públicos y nombra sus clases. No hay `modded class` de ninguna clase de Expansion. Si DayZSandbox puede usarlo en un producto comercial no lo decide este plan; la ficha 173a ya dice que el producto final usará una IA propia.

**Base y convención de citas:** main **de12fd6b7fb3b3adbbdcba4b61a276752c2ac3fd**, leído en el árbol de trabajo, ya integrado en esta rama por `558171a`. Los nombres cortos de Python remiten a `tools/dayz_mcp/`; `MCPBridge.c`, `MCPClientBridge.c` y `MCPMessages.c`, a `addon/scripts/5_Mission/`. Las citas MCP usan ruta y símbolo; no se trasladan números de línea de versiones antiguas.
- **#262, integrado:** escenas en main @de12fd6b (merge `1f9eba7`): `MCPBridge.c`, `m_NextBatchObjectId`, `BATCH_OBJECT_ID_BASE`, `AddSceneEntry`, `SceneSpawnRefusal` y `DispatchWorldSpawnBatch`.
- **#266, pendiente de merge:** cabeza local fetched `c4502d1` (`origin/claude/buzon-36fa-c801-actors`), leída con `git show`: `server.py::actor_emote`, `MCPClientBridge.c::DispatchActorEmote` y sus observadores. Las citas de actores apuntan a esa cabeza, no se atribuyen a main. Revalidar al integrarla; no se afirma una nueva verificación remota de #266.
- **#268, pendiente de merge:** cabeza verificada **78f7919e24e9b98789884e4e6a99daf66907454c**, leída con `git show` y `git log`. Mandan `loopback.py::_close_destination_locked`, `_close_outcome_from_result_locked`, `session_coordination.py::note_close_round`, `note_close_unreachable`, `_apply_close_outcome_locked` y `tools/tests/test_lease_close_broker.py` (K32 negativo/positivo), incluido 268F1 `92ea1da`. Su plan `plans/2026-10-24-transport-lease-end-barrier.md`, §4, especifica la integración futura del adaptador; no acredita código Enforce todavía inexistente.
- **B1 #289, integrado en main @de12fd6b:** godmode y refill por defecto solo con `profile: "test"`; §7 fija ese supuesto de la ronda.

El orden de merge de este plan sigue siendo después de #268. La implementación requiere también #266 y la activación real del cierre en el PBO (§5.1); mergear #268 @78f7919e por sí solo no satisface esa compuerta.

## 1. Qué entrega y qué no

Entrega:
- Tres tools nuevas: `ai_group_spawn`, `ai_group_order` y `ai_group_state`.
- Un parámetro nuevo, `object_id`, en `actor_emote`.
- Un PBO nuevo, `@DayZ_MCP_ExpansionAI`, que solo se carga junto a Expansion.
- Tres ganchos generales y dos de cierre, sin tipos de Expansion, en el PBO base (§2.1). Son trabajo futuro, no código entregado por este PR.

No entrega, con su porqué:
- **`hold` de `ai_group_order`.** La ficha lo pone en la firma, pero no dice qué hace ni le da criterio. `behaviour="HOLD"` ya cubre «pararlo en su marca». Se dice en el cuerpo del PR, y DayZSandbox puede pedirlo con su significado.
- **Modo de combate.** No hay un `SetCombatMode` genérico. `eAI_SetPassive` (`2792982069/…/Entities/DayZPlayerImplement.c:372`) no vuelve activo a un miembro de una facción pasiva: el getter también mira la facción (:377; informe §3).
- **Seguir a un objeto.** No hay una orden pública de seguir un objeto cualquiera (informe §3).
- **Los NPC estáticos de Core** (`ExpansionNPCBase`, que no pasa por `eAIBase`; informe §6). El emote de NPC solo vale para los miembros que creó el adaptador.
- **Grupos persistentes o de patrulla.** Los grupos del adaptador acaban con su lease (§5).

## 2. Arquitectura

### 2.1 Dos PBO

- **El base, `DayZ_MCP`, recibe cinco ganchos virtuales: tres generales y los dos de cierre previstos por #268 @78f7919e, plan §4.** No lleva ningún tipo de Expansion ni un `#ifdef`. Un símbolo que falta no se arregla con una comprobación en tiempo de ejecución (informe §7). Y el orden de carga entre el base y Expansion no está fijado, así que un `#ifdef EXPANSIONMODAI` en el base sería frágil.
  1. `protected string ServerCapabilities()` devuelve `SERVER_CAPABILITIES`. La petición del poll (MCPBridge.c, main @de12fd6b) pasa a llamarlo.
  2. `protected string ExtensionPollParams()` devuelve `""`. Se añade al final de la petición del poll, después de `&ach=` (main @de12fd6b, `MCPBridge.c`, petición del poll).
  3. `protected int DispatchExtension(MCPCommand command, MCPResult result)` devuelve 0 si no conoce el comando, 1 si contestó y hay que publicar ya, y 2 si lo publicará un job. `Dispatch` (main @de12fd6b, `MCPBridge.c`) lo llama justo antes de `unknown_command`. Con 0 sigue dando `unknown_command`, como hoy.
  4. `protected void LeaseCloseExtensionStop()` vacío en el base: el dispatch de `lease_close` lo llama para iniciar el borrado de todos los grupos del adaptador, incluidos rollbacks y residuos de leases anteriores.
  5. `protected void LeaseCloseExtensionCensus(MCPLeaseCloseCensus census)` vacío en el base: cada evaluación del cierre lo llama para añadir los residuos reales (§5.2). El tipo de censo y estos dos ganchos son requisitos de la futura activación, ausentes en el PBO de #268 @78f7919e.
- **El adaptador, `DayZ_MCP_ExpansionAI`, es una carpeta nueva del repo: `addon_expansion_ai/`.** Lleva `$PBOPREFIX$`, `config.cpp` y `scripts/5_Mission/`.
  - Su `CfgPatches` tiene `requiredAddons[] = {"DayZ_MCP", "DayZExpansion_AI_Scripts"}` (`2792982069/ai_scripts/config.cpp:3`, :8). Así compila después de los dos.
  - Sin Expansion, el juego no arranca con él cargado: falla cerrado en la carga.
  - Su `CfgMods` sigue la forma del base, que declara World y Mission (main @de12fd6b, `addon/config.cpp::CfgMods`). El adaptador declara solo `dependencies[] = {"Mission"}` y `missionScriptModule`: es una elección nueva, no una copia, porque vive entero en 5_Mission.
  - No usa defines. El `CfgMods` de Expansion no trae `defines[]` (BW3a): `EXPANSIONMODAI` es un `#define` de su preload (`2792982069/0_ai_preload/Common/DayZExpansion_AI_Defines.c:3`, :4). El diseño no cambia.
  - Hace `modded class` de `MCPBridge`, `MCPArgs`, `MCPResult` y `MCPJob`, que son clases del propio MCP.
  - El emote usa `MCPJob` de `m_Jobs`, con `kind` y `deadline_s`, y sobrescribe `IsJobReady`, `PostJobSuccess` y `PostJobTimeout` solo para su kind (main @de12fd6b, `MCPBridge.c`). El cierre no espera a vaciar `m_Jobs`: su único observador relee el censo real. `ai_group_release` no añade un job de confirmación (§3.6).
  - El adaptador vive en 5_Mission. Desde ahí llama a las clases de 4_World y 3_Game de Expansion.
- **Los campos van en los `modded class`, no en el base**, para que el base no lleve campos de Expansion. Leyendo no se puede afirmar que `JsonSerializer` los vea: `ReadFromString` y `WriteToString` son `proto` (vanilla `3_game/gameplay.c:49`, :68, :100; respuesta 1). Lo mide el paso 0 de la ronda, antes de crear nada (§7). Si falla, los campos van al base, sin tipos de Expansion; eso cambia el PBO base y vuelve a revisión.
- **Nombres de los campos.** Se reusan los del base con su tipo: `pos`, `object_id`, `speed` con `speed_set`, `emote` y `wait_s` (MCPMessages.c::MCPArgs, #266 @c4502d1, pendiente de merge), y `scene_id` (main @de12fd6b, `MCPMessages.c::MCPArgs`). Ningún campo nuevo repite un nombre del base. Un valor que no case con el tipo de un campo del base podría tumbar el parseo del lote entero (MCPBridge.c, main @de12fd6b), y por eso `speed` viaja como número (§3.3). Lo que lee el adaptador va dentro de `ai_group` (`ai_groups` con `scene_id`), porque `MCPResult` ya tiene `state` (main @de12fd6b, `MCPMessages.c::MCPResult`). El emote reusa `emote_state`, `started_after_s` y `finished_after_s` (MCPMessages.c::MCPResult, #266 @c4502d1, pendiente de merge) y añade `emote_pending`.

### 2.2 Cuándo responde `expansion_unavailable`

Sin el adaptador, ningún verbo de Expansion llega al juego:
- El adaptador añade sus nombres al censo: `ServerCapabilities()` devuelve el del base más `,ai_emote,ai_group_order,ai_group_release,ai_group_spawn,ai_group_state`. `parse_poll_caps` no pide orden y rechaza duplicados (main @de12fd6b, `loopback.py`). El tope es de 64 nombres (`POLL_CAPS_MAX_NAMES`); se comprueba el conjunto final tras integrar #266 y la activación del cierre, sin usar el recuento aproximado de la revisión 2.
- Además anuncia `&xai=<16 hex>`, el hash de su propio contrato de args. Se calcula como `server_arg_contract_hash` (main @de12fd6b, `bridge_readiness.py`), sobre un `EXPANSION_AI_ARG_CONTRACT` aparte. La extensión no altera `SERVER_ARG_CONTRACT` ni `ach=`: usa los de la base integrada y no fija el hash anterior a la activación.
- En el broker, los cinco verbos entran en `_CAPABILITY_ADMISSION_COMMANDS` (main @de12fd6b, `loopback.py`). `_capability_refusal_locked` se aplica al encolar y al entregar. Mantiene primero las guardas comunes de censo ausente/viejo o de otra instancia/generación (`bridge_capability_missing`) y después el `ach` base (`arg_contract_mismatch`, incluso si también falta el verbo). Con esas guardas satisfechas, para estos cinco comandos la rama específica sustituye el rechazo genérico por verbo ausente; no se coloca después de un retorno que la haga inalcanzable:
  - sin el verbo en el censo → `expansion_unavailable`, con `detail: "adapter_not_loaded"`;
  - con un `xai` ausente, mal formado o que no sea `EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH` → `expansion_unavailable`, con `detail: "adapter_contract_mismatch"`. Es un PBO del adaptador viejo junto a un base nuevo, o al revés.
  - para admitir nuevas mutaciones del adaptador, sin el soporte real de cierre de §5.1 → `expansion_unavailable`, con `detail: "lease_close_not_supported"`. Las lecturas de residuos siguen disponibles sin lease y no dependen de esa compuerta; los releases internos siguen su autoridad y validación de destino, sin abrir nuevas mutaciones públicas.
  - Si algo cambia entre encolar y entregar, el comando se descarta al entregar y no llega al juego.
- En `_BRIDGE_COMMAND_TOOLS` (main @de12fd6b, `bridge_readiness.py`) entran los cinco del servidor, con `ai_emote` → `actor_emote` y `ai_group_release` → None, como el `survivor_drive_release` interno de #266 @c4502d1 (pendiente de merge). En `_compare_bridge_capabilities` son opcionales: si no se anuncian, el estado sigue en `match`. Una partida sin Expansion no se pone en rojo. La política live de main @de12fd6b (`loopback.py::LIVE_ALLOWED_COMMANDS`, B4 #288) sigue teniendo precedencia: las nuevas mutaciones no se añaden a su lista permitida ni sortean `live_profile_refused`; la ronda usa el entorno de test autorizado.

### 2.3 Ids y registro

- **`group_id` y el `object_id` de cada miembro salen de `m_NextBatchObjectId`** (MCPBridge.c, main @de12fd6b), como los de `world_spawn_batch` (`MCPBridge.c::SpawnBatchItem`, main @de12fd6b). Se toman al registrar (§3.1, punto 9): primero el del grupo y después los de los miembros, en el orden en que se crearon. Un rechazo o un rollback no toman ninguno. Los ids del broker vuelven a empezar con cada daemon (lo vio la ronda 6 de #266); este contador es del bridge y no se repite en una misión.
- **Solo los miembros entran en `m_RuntimeObjects` y en `AddSceneEntry`** (MCPBridge.c, main @de12fd6b), que piden un `Object`. El `eAIGroup` no lo es (`2792982069/…/Classes/eAIGroup.c:3`) y vive solo en el registro del adaptador. Así `object_inspect`, `object_delete` y `scene_entities` funcionan con los miembros sin cambios, y `object_inspect` del `group_id` da `object_id_unknown` (MCPBridge.c, main @de12fd6b).
- **`spawn_token`:** la tool genera 32 hex aleatorios por cada `ai_group_spawn` y los manda en los args. El release los nombra (§5). Un release de un daemon nuevo nunca coincide con un grupo de otro, y el broker no necesita el `group_id` para soltar un spawn que se abandonó tras entregarlo.
- **El registro del adaptador** nunca borra un grupo, como las escenas de #262 (MCPBridge.c, main @de12fd6b). Guarda:
  - por grupo: el token, el `group_id`, el `eAIGroup` y su id de Expansion (`GetID()`, `eAIGroup.c:695`), el `scene_id`, el comportamiento pedido y un estado, `active` o `releasing`;
  - por miembro: su `object_id`, la referencia, sus dos preferencias de velocidad (§3.2) y la última observación (pos, salud, vivo);
  - los muertos salen de la lista activa del grupo a una privada sin getter (`eAIGroup.c:23`, :999), así que hace falta este registro propio (informe §4).
- Un grupo `releasing` del que ya no queda nada (§3.6) contesta `not_found` a order/state; el release interno conserva su respuesta idempotente confirmada.

## 3. Verbos del adaptador

Cada verbo del adaptador cambia el mundo solo en su dispatch, incluido el stop invocado por el cierre. Sus jobs solo leen y publican, como exige este diseño: no cambian el mundo ni el registro, tampoco para consumir una petición de emote, y solo apuntan en sus propios campos, como el de #266 (MCPClientBridge.c, #266 @c4502d1, pendiente de merge). Antes de crear o cambiar nada se valida todo; un fallo durante la creación deja el rollback explícito de §3.1. Esto no acredita los jobs heredados del base: el seguimiento H7 de #268 @78f7919e, plan §7, sigue separado.

### 3.1 `ai_group_spawn`

**Args:** `faction`, `count`, `pos`, `loadout`, `formation`, `behaviour`, `waypoints` (tríos de números seguidos, como `pos`), `scene_id` y `spawn_token`.

**En el dispatch, en este orden, sin crear nada hasta el punto 8:**
1. **Repetición.** Si el `spawn_token` ya está en el registro, es una entrega repetida del mismo comando: no crea nada y contesta la lectura de ese grupo, como `ai_group_state` (§3.4), o `spawn_failed` con `created: true` si ese grupo está `releasing`. Va antes del tope, así que una repetición nunca da `ai_limit_reached`.
2. **Formas.** `spawn_token` de 32 hex, `count` de 1 a 8, `behaviour` de los cuatro de §3.2, `pos` de tres números y `waypoints` en tríos, 32 como mucho. Si no, `bad_args` con su `field`.
3. **Facción.** `eAIFaction.GetType(faction)` (`2792982069/…/Factions/eAIFaction.c:124`). Si no existe, `bad_args` con `field: "faction"` y `allowed`: los nombres que salen de los tipos registrados en `eAIRegisterFaction.s_FactionTypes` (`2792982069/ai_scripts/1_Core/DayZExpansion_AI/eAIRegisterFaction.c:3`). Nunca se cae a una facción por defecto. La instancia sale de `eAIFaction.Create(faction)` (`eAIFaction.c:129`); si es null, `spawn_failed` con `created: false`. Esa instancia es la que recibe `CreateGroup`.
4. **Formación.** Si `eAIFormation.GetType(formation)` (`…/Formations/eAIFormation.c:123`) no la conoce, `bad_args` con las 11 de esta versión (:5). `Create` caería a Vee (:130) tras dejar un error en el log (:139).
5. **Loadout.**
   - El nombre se normaliza una vez: sin un `.json` final. Vacío, es el `GetDefaultLoadout()` de la facción (`eAIFaction.c:47`), normalizado igual. Ese mismo nombre va a las dos comprobaciones y a `Apply`.
   - Existe: `FileExist` vanilla, que el bridge ya usa (MCPBridge.c, main @de12fd6b), de `<nombre>.json` en `$profile:ExpansionMod/Loadouts/` (informe §7). No depende de la caché de prefabs.
   - Se parsea: `ExpansionPrefab.Load(nombre, true)` (`2291785308/…/Prefab/ExpansionPrefab.c:12`), con la recarga forzada, para que no conteste un prefab que la caché guardó de un intento fallido.
   - Si falla una de las dos, `bad_args` con `field: "loadout"`.
6. **Escena.** `SceneSpawnRefusal(command.args, count)` (MCPBridge.c, main @de12fd6b), como el lote (`MCPBridge.c::DispatchWorldSpawnBatch`, main @de12fd6b): `bad_args` con un `scene_id` malo y `scene_full` si no caben `count` entradas más (2048, límite de escena en `MCPBridge.c`, main @de12fd6b). Cuenta solo los miembros y corre en este dispatch, antes de crear.
7. **Tope.** Los miembros vivos de todos los grupos del registro, también los `releasing`, más `count` no pueden pasar de 24, el `AI_TOTAL_MAX`. Si pasan, `ai_limit_reached` con los dos números. El tope por grupo también lo mira la tool (§4). Expansion pide más de 35 FPS de servidor de media y se degrada por encima de 30 IA (ficha 173a).
8. **Crea:**
   - el grupo con `eAIGroup.CreateGroup(<la instancia del punto 3>)` (`eAIGroup.c:123`), con `m_Persist = false` (:32);
   - `count` miembros con `GetGame().CreateObject(eAISurvivor.GetQuasiRandom(), p_i)` (vanilla `3_game/global/game.c:691`; `…/Entities/AI/eAISurvivor.c:27`), con los flags por defecto, como `SpawnAIEx` (`eAICommandManager.c:376`, llamada en :384): `init_ai` es false (respuesta 2). No es el `ECE_INITAI` que #262 pone a los infectados (MCPBridge.c, main @de12fd6b). `p_i` es `pos` más un anillo de 1,5 m, pasado por `ExpansionAISpawnBase.GetPlacementPosition` (`…/Settings/Patrols/ExpansionAIPatrolBase.c:126`), que no garantiza los 5 m;
   - por miembro: `ExpansionHumanLoadout.Apply(member, nombre, true)`, con la recarga forzada que pide el helper (`2291785308/…/Loadout/ExpansionLoadout.c:3199`; llamada en `eAICommandManager.c:385`; respuesta 6), guardando su bool; `member.SetGroup(group)` (`eAIBase.c:3926`; `AddMember` solo no basta, informe §2); `eAI_SetLootingBehavior(eAILootingBehavior.NONE)` (`eAIBase.c:1890`; `AIenums.c:15`), para que el saqueo no saque al actor de su marca; y sus dos preferencias de velocidad (§3.2);
   - la formación y el comportamiento (§3.2).
   - No hace falta registrar al miembro en ningún gestor: la inicialización del actor prepara lo del servidor (`eAIBase.c:503`; respuesta 6).
   - Si un `CreateObject` da null: `eAI_Despawn` de lo creado (`eAIBase.c:2156`) y `group.Delete()` (`eAIGroup.c:204`); el grupo entra en el registro como `releasing`, sin ids, y contesta `spawn_failed` con `created: true`. El censo del cierre confirma después que no queda nada; el release interno puede responder aún pendiente (§3.6).
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

**Args:** `group_id` y los campos presentes de `behaviour`, `waypoints`, `formation` y `speed`. `speed` viaja en el `speed` del base, con `speed_set`, como 1, 2 o 3 (la forma de `player_move`, MCPMessages.c::MCPArgs, main @de12fd6b), que pasan a `eAIMovementSpeed.WALK`, `JOG` o `SPRINT` (`AIenums.c:55`).

- Si `group_id` no está en el registro, o el grupo está `releasing`, `not_found` sin cambiar nada.
- Valida todo antes de aplicar ningún campo: la formación como en el spawn, `behaviour`, `waypoints` y `speed`.
- Aplica solo los campos presentes; la formación, con `SetFormation` (`eAIGroup.c:933`). `speed` cambia la preferencia `normal` de cada miembro vivo: fuera de un HOLD la aplica con `SetMovementSpeedLimits(normal, amenaza)`, y en un HOLD solo la guarda.
- La respuesta trae todo leído después del cambio: `behaviour` (`GetWaypointBehaviour`, `eAIGroup.c:304`), `formation` (`GetFormation`, :903), `formation_state` (`GetFormationState`, :624), el número de waypoints, `waypoint_index` (`m_CurrentWaypointIndex`, :37) y los dos límites de cada miembro (`eAIBase.c:5926`, :5931).
- Trae además `in_combat`, de `group.IsInCombat()` (`eAIGroup.c:1123`). Con `true`, añade `combat_may_override: true`: la FSM de combate puede ignorar la orden (ficha 3ca5; `eAIState_Fighting.c:18`).

### 3.4 `ai_group_state`

Es de solo lectura: entra en `READ_ONLY_COMMANDS` (§4), no lleva lease, no cambia el mundo y contesta en su dispatch, sin job. No participa como prueba del cierre de #268 @78f7919e (§5). Recibe `group_id` o `scene_id`; con `scene_id` lee, en `ai_groups`, todos los grupos de esa escena.

**Por miembro:**
- `object_id`;
- si su entidad existe: `alive` (`IsAlive`, vanilla object.c:517), `pos` (`GetPosition`, :287), `health` (`GetHealth()`, :1062), `fsm_state` y `fsm_substate`. El estado sale de `GetFSM()`, `GetState()` y `GetName()`, y el subestado de `GetSubFSM()`; cada referencia puede ser nula (`eAIBase.c:1480`, `2291785308/…/FSM/ExpansionFSM.c:106`, `ExpansionState.c:49`, :54). `IsAlive` y `GetHealth` valen sobre un eAI muerto mientras exista su entidad (respuesta 7; `eAIBase.c:20`);
- también, para los fixtures y el emote: `threat` (`GetThreatToSelf(true)`, `eAIBase.c:2028`), `fighting` (`m_eAI_IsFightingFSM`, :71), los dos límites (:5926, :5931), `emote_playing` (`m_bEmoteIsPlaying`, vanilla emotemanager.c:127) y `emote_pending`: una petición en cualquiera de las dos capas, la de Expansion (`m_Expansion_EmoteID`, `eAIBase.c:347`) o la vanilla (`m_bEmoteIsRequestPending`, emotemanager.c:126);
- si ya no existe: `gone: true`, con la última observación marcada como tal. Un miembro que se vio muerto sigue con `alive: false`.
- No usa `eAITarget.GetThreat` ni `GetPosition(false)`, que actualizan estado al leer (informe §4).

**Por grupo:** `group_id`, la facción, la formación, `formation_state`, `behaviour` (en las dos formas), `waypoint_index`, el número de waypoints, `current_waypoint` (`GetCurrentWaypoint`, `eAIGroup.c:236`), `in_combat`, `alive`, `members_total` y, si está `releasing` y queda algo, `release: "pending"`, los ids de miembros pendientes y si el grupo original sigue presente. No llamar getters de un grupo ya desaparecido: esos campos se marcan no disponibles, mientras se siguen censando sus miembros registrados.

**Errores:** un `group_id` que no existe, o el de un grupo ya soltado del todo, da `not_found`; un grupo sin vivos da ok con `alive: 0`.

### 3.5 `ai_emote`, el comando que manda `actor_emote(object_id=…)`

**Args:** `object_id`, `emote` y `wait_s`.

**Las capas.** La petición de Expansion (`m_Expansion_EmoteID`, `eAIBase.c:347`), la petición vanilla (`m_bEmoteIsRequestPending`, emotemanager.c:126) y la reproducción (`m_bEmoteIsPlaying`, :127) son tres cosas distintas (respuesta 4). Con `autoCancel=false`, el id de Expansion se queda tras el intento y puede volver a reproducir el gesto después de verse el cese (respuesta 8; `eAIBase.c:10869`). Con `autoCancel=true`, Expansion consume la petición en su propio intento, aunque no llegue a reproducir, y programa la cancelación de las poses estáticas a `autoCancelDelay` ms (informe §6; :10869). El adaptador usa la segunda: una sola petición se consume una sola vez, sin que nadie escriba después.

**En el dispatch, en este orden:**
1. Si `object_id` no es un miembro vivo de un grupo `active`, `bad_target`.
2. Un solo job de emote por miembro: con otro abierto sobre él, también para `end`, `emote_rejected`, como #266 (MCPClientBridge.c, #266 @c4502d1, pendiente de merge).
3. Con una petición de Expansion pendiente, o con `IsEmotePlaying()` cierto (emotemanager.c:1328, que cuenta la petición vanilla y la reproducción), `emote_rejected`, como #266 (MCPClientBridge.c, #266 @c4502d1, pendiente de merge). Así nada suena al pedir, y lo que vea el job después es suyo.
4. Con `m_eAI_IsFightingFSM` (`eAIBase.c:71`), `group.IsInCombat()` o `GetThreatToSelf(true) > 0.2` (`eAIBase.c:2028`; la guarda de `eAIState_PlayEmote.c:13`, con el umbral en :27), `emote_rejected` y no pide nada.
5. La única escritura: `Expansion_SetEmote(id, true, wait_s × 1000)` (`eAIBase.c:10862`). No se llama a `Expansion_PlayEmote`, que se saltaría la guarda (informe §6). El retraso solo cancela poses estáticas (§8, pregunta 1).

**El job solo lee el `EmoteManager` del miembro** (`GetEmoteManager()`, vanilla `playerbase.c:1892`) y su petición de Expansion:
- `started`: el primer tick con `m_bEmoteIsPlaying` cierto y `Expansion_GetCurrentGesture()` (`2291785308/…/ExpansionEmoteManager.c:49`) igual al id pedido. Es una transición nueva, porque al pedir no sonaba nada;
- `finished`: tras un `started`, el primer tick en que deja de reproducir;
- cada respuesta trae `started_after_s` y `finished_after_s`, contados desde la petición, y `emote_pending` (cualquiera de las dos capas) leído en ese tick, en la forma de #266 (MCPClientBridge.c, #266 @c4502d1, pendiente de merge). `finished` con `emote_pending: false` dice que sonó una vez y que nada lo va a repetir;
- al llegar a `wait_s`: con `started` sin acabar, `started`, como #266; sin `started`, `emote_timed_out`, con `emote_pending`;
- si el miembro muere o su entidad desaparece durante la espera, `emote_interrupted`.
- Una petición que se queda pendiente la borra `emote="end"`, o el stop del cierre del lease al borrar al miembro.

**Con `emote="end"`**, tras los puntos 1 y 2:
- si reproduce: borra la petición de Expansion si la hay, con `Expansion_SetEmote(0)`; pide `ServerRequestEmoteCancel()` (vanilla `4_world/classes/emotemanager.c:1275`), que es una solicitud y no un cese; y un job lee hasta que deja de reproducir: `finished` con `finished_after_s`, o `emote_timed_out` a `wait_s` (MCPClientBridge.c, #266 @c4502d1, pendiente de merge);
- si no reproduce pero hay una petición de Expansion: `Expansion_SetEmote(0)` la deja a cero (respuesta 4) y contesta en el dispatch `emote_state: "cancelled"`, con `emote_pending` leído después de escribir;
- si solo queda la petición vanilla, que está en vuelo: `emote_rejected` con `emote_pending: true`, sin escribir nada; el `end` se puede repetir;
- si no hay nada: `no_emote_playing` (MCPClientBridge.c, #266 @c4502d1, pendiente de merge).

**Los ids de emote** son los de `EmoteConstants` (vanilla `3_game/constants.c:366`; `ID_EMOTE_NOD` en :404), con los mismos nombres que `ActorEmoteId` de #266 (MCPClientBridge.c, #266 @c4502d1, pendiente de merge).

### 3.6 `ai_group_release` (interno)

**Args:** `spawn_token`.

**En el dispatch:**
- Con un grupo `active`: `eAI_Despawn()` de cada miembro cuya entidad exista, vivo o muerto, y `group.Delete()` (`eAIGroup.c:204`). Los dos son diferidos: `eAI_Despawn` acaba en el `Delete` diferido de `Object` (respuesta 5; `eAIBase.c:2156`; vanilla `3_game/entities/object.c:82`), y la limpieza de Expansion usa esa misma vía para sus muertos (`eAIGroup.c:1220`). Quita cada miembro de `m_RuntimeObjects` mientras su id siga nombrándolo, con `MCPBotControl.OnObjectGone`, como `scene_clear` (MCPBridge.c, main @de12fd6b), y pasa el grupo a `releasing`. Las entradas de su escena se quedan, y `scene_entities` las da borradas (main @de12fd6b, `MCPBridge.c::DispatchSceneEntities`).
- Con un grupo ya `releasing` (un rollback o un release anterior): no vuelve a pedir nada.
- No usa `ClearAI` (salta los `eAINPCBase` vivos y no usa `autodelete`, `eAIGroup.c:1220`) ni `Admin_ClearAllAI` (:1208).
- Si ya no queda nada, contesta en el dispatch `released` con `confirmed: true`. Si queda algo, contesta pendiente como se define abajo.

**Respuesta inmediata, sin job propio.** Si quedan referencias, publica `ok: true`, `ai_group: {release: "pending", confirmed: false}` y los ids restantes (el token identifica un rollback sin ids públicos). Esto confirma la solicitud, no el borrado. Un token repetido no vuelve a pedirlo. El mismo helper de dispatch se usa desde `LeaseCloseExtensionStop()`; ese hook no encola comandos ni publica resultados de `ai_group_release`.

**La prueba de desaparición** solo lee las referencias de todos los miembros, vivos y muertos, y consulta `eAIGroup.GetGroupByID(id, false)` (`eAIGroup.c:105`), que busca sin crear. El grupo cuenta como presente solo si esa consulta devuelve el grupo original registrado. Conservar su referencia para identificarlo no demuestra que siga registrado en Expansion; un id reutilizado por otro grupo tampoco cuenta como el original (§8, pregunta 4). El predicado limpio exige que no quede ninguna entidad de miembro y que la consulta ya no devuelva el grupo original: su ausencia sola no basta. Esta prueba alimenta `ai_group_state` y `LeaseCloseExtensionCensus`; no depende de `m_RuntimeObjects`, de la lista de vivos ni de que exista un job.

**Timeout y deuda:** se elimina `AI_RELEASE_CONFIRM_S`. Durante un cierre, el único observador común tiene el tope de 25 s previsto por #268 @78f7919e, plan §4. Si aún queda algo, publica un censo con residuo `ai_group_release_unconfirmed`; el grupo sigue `releasing`, con sus referencias. El coordinator conserva OWED y permite un grant degradado al terminar la espera acotada; §5.3 fija las únicas salidas de esa deuda, incluido el caso en que nunca se observa un delete. Acabar un job o recibir el resultado inmediato del release no salda el cierre.

**Además:**
- Con un token desconocido, ok con `released: 0`: el spawn abandonado pudo no llegar a crear nada.
- Tras un `scene_clear` de #262, el helper sigue pidiendo el borrado del grupo si estaba `active`; la prueba conserva tanto los miembros cuyo borrado aún esté pendiente como el grupo. Solo cuando los miembros han desaparecido queda por censar el grupo.
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
- Uno de los dos y no los dos. Sin lease: `ai_group_state` entra en `READ_ONLY_COMMANDS` (main @de12fd6b, `session_coordination.py`). Todo comando que no esté ahí pide lease (`session_coordination.py::command_requires_lease` y `authorize`, main @de12fd6b), y sin token da `lease_required`. #262 ya integró `scene_entities` en esa lista de main @de12fd6b.

**`actor_emote(emote, wait_s=10.0, object_id=None, timeout_s)`**
- Sin `object_id`, igual que #266: el comando `actor_emote` va al cliente.
- Con `object_id`, el comando `ai_emote` va al servidor, con lease.
- La lista de emotes y los topes de `wait_s` son los de #266 (loopback.py, esquema `actor_emote`, #266 @c4502d1, pendiente de merge). Con `object_id`, los estados son los de #266 más `cancelled`, y la respuesta trae además `emote_pending` (§3.5).

**En el broker:**
- un `_command_schema` por cada uno de los cinco comandos;
- `EXPANSION_AI_ARG_CONTRACT`;
- la admisión de §2.2;
- `peer_for_command`: `ai_emote` es de servidor y `actor_emote` sigue siendo de cliente.

## 5. Fin del lease y abandono

### 5.1 Contrato leído y compuerta de activación

Todas las referencias de transporte de esta sección son **#268 @78f7919e**. Su implementación tiene FIFO (`RemoveOrdered` y no adelantar pendientes), reservas y el coordinator de cierres por `(run_id, peer)`. **Los PBO de esa cabeza siguen en v12, sin `lease_close` / `lease_close_client` en sus censos y sin los hooks de cierre.** El plan de transporte describe v13 y su observador de 25 s como integración futura; no se atribuye esa implementación al head actual.

268F1 hace `_close_destination_locked` dependiente del censo del peer ligado a su instancia y generación. Sin el nombre de cierre devuelve `close_not_supported`; `note_close_round` y `note_close_unreachable` saldan esa obligación sin comando y sin OWED. K32 negativo comprueba que el siguiente grant no hereda `lease_end_unconfirmed`. Esto significa «cierre no soportado», no «NPC borrados». Otros fallos (cuarentena, destino no ligado o versión bloqueada) conservan deuda según el contrato.

**Compuerta para la futura implementación del adaptador:** integrar la activación completa del cierre, sus tipos, hooks, censos y observador antes de admitir spawns. Exigir en admisión y entrega el soporte de cierre de todos los peers requeridos del run (servidor y cliente, o ambos roles del offline), con instancia/generación vigentes, además de `xai` y el contrato base. Si falta, `expansion_unavailable`, `detail: "lease_close_not_supported"`, sin creación. No basta cambiar un número de versión ni anunciar un nombre sin implementarlo. La ausencia de soporte en v12 debe seguir siendo inerte; no se introduce un fallback que genere deuda sintética. Este requisito y sus pruebas forman parte de la implementación posterior, con revisión y ronda propias.

### 5.2 Registro, stop y censo del adaptador

- Conservar en `RELEASE_REGISTRY` el par `ai_group_spawn` → `ai_group_release` por `spawn_token` **solo para abandono tras entrega**, siguiendo `loopback.py::_abandon_row_locked` y `_publish_release_locked`. FIFO lo ejecuta después de su origen; el release conserva el pin de destino y la autoridad interna de cleanup. Un spawn no entregado no necesita release; uno con resultado ya guardado lo limpia el cierre. `_OWNER_CLEANUP_COMMANDS` admite el release interno, no una tool pública sin lease.
- **Ningún libro de tokens por lease en Python y ningún `ai_group_release` desde `cleanup_owner`.** Tampoco se añade `cleanup.ai_group_release_enqueued`. El registro de §2.3 vive en el servidor durante la misión y conserva tokens para idempotencia y referencias para la prueba, incluidos los rollbacks sin ids y los grupos `releasing` de leases previos.
- `LeaseCloseExtensionStop()` inicia en el dispatch el borrado de todos los grupos registrados, usando el helper de §3.6; un grupo `releasing` no repite la solicitud. El emote pendiente queda cubierto por la desaparición de su miembro; su job puede acabar en `emote_interrupted` y nunca constituye la prueba del cierre.
- `LeaseCloseExtensionCensus(census)` añade una entrada por cada grupo original con miembros o grupo aún presentes: `name: "ai_group:<spawn_token>"`, `state: "residue"`, `reason: "ai_group_release_unconfirmed"`. Mantiene las entradas ajenas y el estado global sucio; no sustituye el censo base. Si ya no queda nada, omite esa entrada. No usa un booleano de timeout permanente: cada evaluación relee las referencias. Los ids pendientes se exponen en `ai_group_state` y en la respuesta de §3.6, sin cambiar el esquema base de entradas `{name,state,reason}`.
- El único observador del cierre vuelve a evaluar ese predicado, hasta 25 s desde su dispatch, sin setters ni un job adicional de release. Un `ok: true` exterior con `lease_close.state: "residue"` sigue siendo residuo. El broker solo acepta un censo global `clean` cuyas entradas sean `clean` o `stopped`, tras validar identidad, ronda, id y floors (`_close_outcome_from_result_locked`). Un registro vacío de jobs no demuestra nada.

### 5.3 Espera acotada, OWED y recuperación

El coordinator registra la ronda antes del cleanup. El cierre cliente va primero; el del servidor espera su resultado o 10 s desde la apertura de la ronda y solo transporta floors de un resultado cliente limpio. La espera de grant está acotada por `LEASE_END_BOUND_S = 40.0` desde la ronda, no por 40 s desde cada release. Un residuo puede terminar la espera antes; un cierre perdido o sin respuesta acaba como `close_timeout`. Las demás compuertas de lifecycle/cuarentena siguen aplicándose.

| Suceso | Resultado y deuda |
|---|---|
| Todos los deletes observados dentro del cierre | Censo limpio; el coordinator valida floors y salda la fila. El grant incluye esa fila en `lease_end.settled`, con `close_id`. |
| A los 25 s aún existen miembros o grupo | Censo con `ai_group_release_unconfirmed`; fila OWED y próximos grants con `lease_end_unconfirmed` y su motivo. No queda un job del adaptador esperando para siempre. |
| No llega resultado válido dentro de los 40 s de la ronda | OWED por `close_timeout`; no bloquea permanentemente la cola. Un resultado inmediato de `ai_group_release` no lo liquida. |
| Los deletes se observan más tarde | Un cierre posterior vuelve a censar los residuos y, si todo queda limpio, salda la deuda. También vale un resultado limpio tardío del cierre todavía correlacionado a la ronda/id vigentes; un resultado obsoleto, de otra instancia o con floors insuficientes no la borra. |
| Los deletes **nunca** se observan | Conservar OWED mientras viva el servidor. La salida terminal es el stop del run exacto por el lifecycle guard y la confirmación de salida del proceso **servidor**, que llega a `note_processes_gone(run_id, role)`. Un replace de ese rol solo sirve si confirma que murió el proceso anterior. Entonces se elimina su deuda, sin depender del job, token o lease original. |

No se borra deuda por TTL, por quitar referencias del registro, por reiniciar el daemon ni por reemplazar solo el cliente. `seed_restart_debt` conserva la incertidumbre de procesos vivos; `note_processes_gone` elimina únicamente los peers cuya salida se ha acreditado. Si el guard no puede confirmar la salida, se documenta cierre degradado y el run queda `UNRECONCILED`; no se promete limpieza ni se mata por PID. «Sin fuga OWED» significa que la desaparición real seguida de un censo válido, o la salida confirmada del servidor, elimina la deuda del adaptador. No es posible prometer ausencia de `lease_end_unconfirmed` mientras sobrevivan NPC cuyo borrado nunca se prueba. O10–O13 verifican las dos salidas y que no se arrastre deuda a un run nuevo.

**Forma pública real:** `session_status` muestra `lease_end.outstanding`, `deadline_in_s` y `unconfirmed` mientras haya filas; puede omitir `lease_end` cuando ya no quedan. El grant captura `lease_end: {round, settled, unconfirmed}`; su `cleanup_degraded` combina `lease_end_unconfirmed` con otros motivos, sin sobrescribirlos. No se espera `lease_end.state: confirming`. Una foto de un grant ya emitido no se reescribe tras limpiar deuda: se comprueban el estado actual y el siguiente grant (`_status_lease_end_locked`, `_lease_end_photo_locked`, `_apply_owed_clean_locked`, `note_processes_gone`).

## 6. Criterios offline (R26), que corren en el CI sin el juego

**Python, con el broker real en memoria y el bridge traducido donde haga falta:** salvo el fallo bajo prueba, las fixtures tienen censo vigente, instancia/generación correctas, `ach`/`xai` válidos, peers con cierre soportado y política de test que admite el comando. O13 prueba expresamente la excepción v12.

| # | Criterio | Tipo |
|---|---|---|
| O1 | Sin el verbo en el censo, cada uno de los cinco da `expansion_unavailable` (`adapter_not_loaded`) al encolar y no se encola nada. Si el censo lo pierde entre encolar y entregar, se descarta al entregar (main @de12fd6b, `loopback.py::_capability_refusal_locked`) y no llega al juego. Un censo ausente o viejo sigue dando `bridge_capability_missing`. | NEG |
| O2 | Con el censo y un `xai` ausente, mal formado o distinto, `expansion_unavailable` (`adapter_contract_mismatch`); con el bueno, se encola. Un `xai` que cambia entre encolar y entregar descarta al entregar. Las comprobaciones de instancia y de frescura siguen igual. | NEG/POS |
| O3 | Por la entrada real de cada tool: `count` 0, 9, 1.5 o true dan `bad_args` y nada se encola; 1 y 8, sí. Behaviour `"hold"` en minúsculas o `"PATROL"` dan `bad_args` con los cuatro valores. 33 waypoints, uno con NaN y una `pos` de dos números dan `bad_args`. | NEG/POS |
| O4 | `ai_group_order` sin campos da `bad_args`; `ai_group_state` con los dos ids o con ninguno da `bad_args`. `ai_group_state` pasa el `authorize` real (main @de12fd6b, `session_coordination.py::authorize`) sin token, y otra vez después de `session_release`; en el mismo caso, un comando con lease da `lease_required`. | NEG/POS |
| O5 | Un `ai_group_spawn` entregado y abandonado (timeout o cancelación) encola `ai_group_release` con su `spawn_token`, detrás de su spawn en la cola del servidor. Uno sin entregar no encola nada, y uno cuyo resultado ya se guardó tampoco: lo suelta el fin del lease (O6). | POS/NEG |
| O6 | Al soltar el lease con peers que anuncian cierre, `cleanup_owner` no encola ningún `ai_group_release` ni mantiene un libro de tokens. Se publican `lease_close_client` y después `lease_close` conforme a #268 @78f7919e; el stop cubre todos los grupos, también rollbacks y residuos anteriores. El release de abandono de O5 conserva la autoridad interna real y no basta para saldar la fila del cierre. Cuarentena produce deuda, sin borrar referencias ni entregar mutaciones. | POS/NEG |
| O7 | Con el comparador real (main @de12fd6b, `bridge_readiness.py::_compare_bridge_capabilities`) y las tres tools nuevas registradas: sin los cinco verbos en el censo, `match`; con ellos, también. | POS |
| O8 | Sin `object_id`, `actor_emote` manda `actor_emote` al cliente, como #266. Con él, manda `ai_emote` al servidor, con lease. El job del servidor lo prueba E8. | POS |
| O9 | El hash del contrato del adaptador del `.c` es `EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH`. Cada clave del contrato es un campo del `modded class MCPArgs` o uno heredado con su tipo (`pos`, `object_id`, `speed`, `speed_set`, `emote`, `wait_s` y `scene_id`). Ningún campo nuevo de `MCPArgs` o de `MCPResult` repite un nombre del base, ni se llama `state`. La lectura y la escritura reales las prueba el paso 0 (§7). | POS |
| O10 | Broker y coordinator reales, reloj controlado, borrado que no se observa: el censo a 25 s lleva residuo y OWED; con respuesta perdida, a los 40 s desde la ronda, `close_timeout`. B obtiene grant degradado cuando las demás compuertas lo permiten; C sigue viendo la deuda si nada cambió. No hay espera infinita, resultado limpio por timeout ni job de release huérfano. | NEG |
| O11 | Después de O10, hacer desaparecer las referencias y producir un cierre limpio válido: se elimina la deuda del adaptador, `outstanding` llega a cero (o se omite `lease_end`) y el siguiente grant no arrastra su motivo. Un clean tardío del id/ronda vigente también funciona; otro id, instancia, generación, ronda o floors insuficientes no. La foto del grant anterior permanece intacta. | POS/NEG |
| O12 | Deletes nunca observados: simular la notificación acreditada del lifecycle a `note_processes_gone` para el servidor del run exacto; su deuda desaparece y no reaparece al ligar un run nuevo. Reinicio del daemon (`seed_restart_debt`), retirada de un binding sin salida acreditada, salida solo del cliente y notificación de otro run no liquidan esa deuda. Preservar las deudas de otros peers/runs. | POS/NEG |
| O13 | Reproducir K32 de #268 @78f7919e: censos v12 reales sin `lease_close*` → cero closes y cero OWED por falta de soporte, sin `lease_end_unconfirmed` espurio. Con los nombres anunciados se publica el cierre. Aunque anuncie `xai`, un adaptador sin soporte de cierre se rechaza antes del spawn y al perderlo antes de entrega; `ai_group_state` sigue leyendo residuos sin lease. | POS/NEG |
| O14 | Rama de admisión real: distinguir censo ausente/viejo, verbo ausente, `ach` incorrecto, `xai` incorrecto y soporte de cierre ausente, con los errores y precedencia de §2.2 al encolar y entregar. El comparador conserva la opcionalidad, el censo final no excede 64 nombres y la política live sigue rechazando lo que no permite. | POS/NEG |

**Enforce traducido, con dobles de las firmas de la tabla del informe y la forma de los tests de #266.** Cada criterio conduce el tick real del bridge (`ProcessJobs`, MCPBridge.c, main @de12fd6b) hasta el resultado o el plazo:

| # | Criterio |
|---|---|
| E1 | Facción desconocida: `bad_args` con la lista registrada, `created: false` y 0 llamadas a `CreateObject` ni a `CreateGroup`. Ídem con una formación desconocida (las 11). Loadout, con un doble cuyo `Load` cachea: dos peticiones seguidas del mismo nombre inexistente dan `bad_args` las dos; `X.json` y `X` llegan como el mismo nombre a `FileExist`, a `Load` y a `Apply`; un fichero que existe y no se parsea da `bad_args`. |
| E2 | Con 20 vivos en el registro, `count` 5 da `ai_limit_reached` y 0 creados. Con 24 vivos, repetir el token de un grupo ya creado contesta ese grupo, no `ai_limit_reached`. |
| E3 | Un spawn bueno: `count` llamadas a `CreateObject`, un `SetGroup` por miembro con el grupo creado, `m_Persist` false, saqueo NONE, cada miembro en `m_RuntimeObjects` y en la escena, y el grupo en ninguno de los dos. Con 2047 entradas en la escena, `count` 2 da `scene_full` y 0 creados; un `scene_id` malo, `bad_args`. La respuesta sale de lo que leen los getters del doble, no de los args: un doble que lee otra facción da esa otra. |
| E4 | Un `CreateObject` null a la mitad: los ya creados reciben `eAI_Despawn`, el grupo `Delete`, la respuesta es `spawn_failed` con `created: true` y el grupo queda `releasing`, sin ids en `m_RuntimeObjects`. El release responde pendiente; el censo del cierre no limpia hasta que el doble los da por desaparecidos. |
| E5 | Con límites distintos en el doble (JOG y SPRINT): HOLD pone NONE, HALT y (STATIC, SPRINT) en cada miembro. Con el seguimiento activo (el doble sube el normal), un LOOP posterior pone (JOG, SPRINT). Dos HOLD seguidos y un LOOP, igual. `speed` en un HOLD no se aplica hasta salir. Una formación mala en el order no cambia nada, ni los otros campos de la misma llamada. |
| E6 | Release con borrado diferido: despawn de vivos y muertos y `Delete` del grupo solo en dispatch; responde pendiente y no crea job. El stop del cierre usa el mismo helper para todos los grupos; su observador único mantiene el residuo aunque no haya jobs y a 25 s lo publica si aún queda algo. Probar miembros sin grupo, grupo sin miembros, cadáveres, rollback sin ids y ambos desaparecidos; solo este último limpia. Repetir release/stop no repite deletes. Un token desconocido da `released: 0`; uno ya desaparecido, `confirmed: true`. Un cierre posterior elimina el residuo al observar desaparición. El censo no llama a setters ni modifica el registro; un id reutilizado no confunde dos grupos. |
| E7 | Estado: un miembro sin entidad sale `gone`, con la última observación. Uno muerto, `alive: false`. Un grupo sin vivos da ok con `alive: 0`; uno `releasing` con algo pendiente, `release: "pending"`; uno ya soltado del todo, `not_found`. `threat`, `fighting`, los límites y las capas del emote salen de los getters del doble. Ninguna lectura llama a un setter. La consulta sin lease la prueba O4. |
| E8 | Emote, con dobles de las dos capas pendientes: en combate o con amenaza mayor que 0,2, `emote_rejected` y 0 llamadas a `Expansion_SetEmote`. Con otro job sobre el miembro, con un gesto ya en curso (también del mismo id) o con una petición en cualquiera de las dos capas, `emote_rejected`. En reposo, una sola llamada, `Expansion_SetEmote(id, true, …)`, en el dispatch. `started` pide el gesto, `m_bEmoteIsPlaying` y una transición posterior al dispatch; `finished`, un inicio visto y un cese, con sus dos tiempos. Si el doble no consume la petición y repite el gesto, `finished` sale con `emote_pending: true`. `end`: con solo la petición de Expansion, `cancelled`; con el gesto en curso, la cancelación y `finished` al ver el cese; sin nada, `no_emote_playing`. El job no llama a nada que cambie el mundo. |
| E9 | Sin el adaptador, `Dispatch` de `ai_group_spawn` da `unknown_command`, el poll del base no cambia byte a byte y su censo sigue igual. Compilar y cargar con y sin el adaptador lo cubre la ronda (corridas A y B). |

**Mutantes:** al menos uno por criterio, aplicado en su propia copia. Por ejemplo: quitar la validación de la facción, caer a Vee, no restaurar el límite tras un HOLD, que el release se salte los muertos y que el job del emote llame a `Expansion_SetEmote`. Y los cuatro que conservan las llamadas y falsean el resultado: un release que confirma con el borrado aún pendiente (E6), una validación que acepta el prefab que `Load` cacheó de un intento fallido (E1), un `started` que acepta el gesto que ya sonaba con el mismo id (E8) y un HOLD que pisa el límite bajo amenaza (E5). Para O10–O14: limpiar por timeout, conservar un residuo después de desaparecer, borrar deuda del servidor al salir solo el cliente, crear OWED para un v12 sin cierre y dejar inalcanzable el rechazo específico del adaptador.

## 7. Ronda final (en el juego)

**Esta ronda pertenece a la implementación futura.** La revisión 3 solo modifica el plan: no construye PBO, inicia DayZ, levanta daemons ni consulta el puerto 8765. Los criterios offline de §6 son trabajo pendiente de esa implementación, no resultados de esta revisión.

**Preparación.** Los pasos 1, 2 y 4 tocan carpetas o la configuración del MCP de Guillermo, así que necesitan su palabra para la ronda; ninguno resella. Antes de la corrida B se debe cumplir §5.1: registrar las versiones y los censos de ambos peers, los nombres de cierre implementados y el `xai` esperado. Un v12 inerte no acredita cleanup del adaptador.
1. En `!Workshop` (`P:\Mods`), solo las carpetas vacías `@DayZ-Expansion-Core` y `@DayZ-Expansion-AI` pasan a ser junctions, a `content\221100\2291785308` y `\2792982069`. Core lleva también ModStorage (`modstorage_scripts.pbo`, con `requiredAddons` `JM_CF_Scripts`). `DF_Scripts` es Dabs Framework (2545327648; pide `DZ_Scripts` y `DF_GUI`, del mismo mod), y `@Dabs Framework` ya es junction, como `@CF` (1559212036); los dos van en los `base_mods` por defecto de LIVE. Se deshace volviendo a dejar vacías las dos carpetas.
2. El PBO del adaptador se construye con el AddonBuilder de P en una carpeta nueva, `P:\Mods\@DayZ_MCP_ExpansionAI\Addons`. El worker gestionado solo construye y despliega el mod del proyecto (main @de12fd6b, `dayz_test_worker.py`, fase de build).
3. `dayz_test_run` lleva `extra_mods=["@DayZ-Expansion-Core", "@DayZ-Expansion-AI"]`, que servidor y cliente cargan con el mismo `-mod` (main @de12fd6b, `dayz_test_worker.py`, composición de argumentos de lanzamiento); el cliente necesita las clases de los eAI. `@Dabs Framework`, con su espacio, y `@CF` ya vienen en los `base_mods`. El adaptador va en `server_mods` (`-serverMod`, main @de12fd6b, `dayz_test_worker.py`), porque solo corre en el servidor. Cambiar el conjunto rota storage_1 (mundo y personajes nuevos).
4. `exec_enforce`, que usa el paso 10, está apagado por defecto (main @de12fd6b, `server.py::exec_enforce`). Para la ronda, el MCP se registra con `--enable-exec-enforce --exec-allowlist <fichero>` (main @de12fd6b, `server_cli.py` y `daemon_contract.py`). El fichero, fuera del repo, es la lista JSON de `load_exec_allowlist` (main @de12fd6b, `core.py::load_exec_allowlist`) con solo las dos expresiones del paso 10. Al acabar, el MCP vuelve a registrarse sin ellas.
5. **B1 #289:** el harness escribe `profile: "test"` en su configuración (`loopback.py`, preparación del run, main @de12fd6b). `MCPBridge.ConfigIsTestProfile`, `MissionServer.OnMissionStart` y `4_World/MCP_PlayerCare.c::MCPGodmode` activan por defecto godmode y refill de agua/energía solo con ese valor. Sin fichero, con JSON ilegible, sin campo o con otro valor, ambos quedan apagados por defecto; una elección explícita de `player_godmode` prevalece para esa identidad. Registrar el perfil efectivo y el readback de godmode del jugador; no inferirlo de tener el PBO cargado ni del nombre LIVE. El perfil del harness y la política live del daemon (B4) son controles distintos.

La ronda usa los ajustes y los loadouts por defecto de Expansion (informe §7) y no los edita.

**Fixtures.** Las muestras van a una por segundo, cada una con su hora.
- **P:** un punto llano, sin edificios a 30 m ni zonas de peligro (respuesta 3). Antes del paso 2, `entities_query(pos=P, radius=30)` no da ningún vehículo ni ningún `eAI_*`. W1 está a 25 m de P, W2 a 25 m de W1 y W3 a 25 m de W2, en llano.
- **Reposo:** los grupos de los pasos 2 a 11 son de la facción `Fr`, que no ataca al jugador (§8, pregunta 2). En un paso en reposo, cada muestra da `in_combat: false` y, en cada miembro, `fighting: false` y `threat` de 0,2 o menos. Si una falla, el paso es INCONCLUSO.
- **Combate** (respuesta 9), después del paso 11 y en otro punto: un grupo Raiders y otro West, de 2 cada uno, a 30 m. Que sean hostiles no basta (`eAIFactionRaiders.c:9`, `eAIFactionWest.c:9`; `eAIBase.c:969`): se espera como mucho 60 s a que una muestra dé `in_combat` en uno de los dos grupos o `fighting` en un miembro. Si no, INCONCLUSO. Antes, identificar al jugador por `uid` (main @de12fd6b, B2/B3: `player_find` o `query_all_players`), usar `player_godmode(on=true, uid=...)` si hace falta y comprobar el readback; no presuponer inmunidad fuera de `profile: "test"` ni aplicar esa política a los NPC.

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

    Usan `MCPBridge.Get()` y `RuntimeObjectById` (MCPBridge.c, main @de12fd6b) y el `SetHealth(0)` de `player_kill` (`MCPBridge.c::DispatchPlayerKill`, main @de12fd6b); el eAI es un `PlayerBase` (`eAIBase.c:20`). `player_kill` no sirve: pide la identidad de un jugador (MCPBridge.c, main @de12fd6b). Tras la primera, M da `alive: false` y los otros tres, vivos. Tras la segunda, el grupo da ok con `alive: 0`. Un `exec_failed` (MCPBridge.c, main @de12fd6b) es INCONCLUSO.
11. (36fa NPC) Un grupo nuevo, `count=1`, en HOLD, en reposo y con el jugador a menos de 20 m; N es su miembro. `actor_emote(object_id=N, emote="nod", wait_s=10)` da `finished`, con `started_after_s` < `finished_after_s` ≤ 10 y `emote_pending: false`. En las cinco muestras siguientes, N da `emote_playing: false` y `emote_pending: false`: el gesto no se repite. Si N está en `Dormant` y no arranca, ese es el hallazgo (informe §7).
12. (36fa NPC NEG) Con el combate del fixture ya observado, `actor_emote(object_id=<un miembro de Raiders con fighting: true>, emote="nod")` da `emote_rejected` o `emote_timed_out`, nunca `finished`.
13. El fin del lease va el último: `session_release`. Correlacionar los ids de `lease_close_client` y `lease_close` con sus censos; no esperar `cleanup.ai_group_release_enqueued` ni `confirming`. Para aprobar el caso limpio, `session_status` debe llegar a `lease_end.outstanding: 0` y `unconfirmed: []`, o a omitir `lease_end` cuando no queden filas (#268 @78f7919e, §5.3). En un acquire de comprobación, el grant debe mostrar los cierres soportados en `lease_end.settled` con sus `close_id`, sin motivo del adaptador en `unconfirmed` ni `lease_end_unconfirmed`; liberar inmediatamente ese lease sin mutar. Ya sin lease, `scene_entities("xai")` da borrados todos los miembros, el `ai_group_state` de cada grupo da `not_found` y `entities_query`, en P y en el punto del combate, no da ningún `eAI_*`. Los logs deben probar deletes de vivos y cadáveres y la desaparición del grupo, no solo la retirada de ids públicos.

**Si el paso 13 encuentra residuo:** no aprueba la ronda limpia. Guardar censo, run/peer, ids y tiempos; seguir §5.3: un cierre posterior puede acreditar desaparición tardía. Si no se observa nunca, cerrar el run exacto por lifecycle guard y comprobar que la salida del servidor elimina su deuda, conservando las ajenas. Si no se confirma salida, documentar cierre degradado `UNRECONCILED`. Los fallos deterministas (25 s, 40 s, respuesta perdida, reinicio y proceso que no termina) se fuerzan offline en O10–O13/E6; no se simulan matando procesos. En todos los casos, el handoff de la futura ronda incluye `session_status` y el estado del run.

**Aprobado:** del 0 al 13 como se dicen, sin ningún INCONCLUSO. Se resuelven 173a, 3ca5 y 90f9, y 36fa si #266 también salió bien.

## 8. Preguntas abiertas para la revisión de Codex

Las preguntas 1–4 se conservan para la implementación leyendo el árbol de `%TEMP%\expansion-ai-read` y la vanilla. La 5 queda respondida con el contrato de #268 @78f7919e leído desde git.
1. ¿`Expansion_SetEmote(id, true, delay)` consume la petición en el intento y cancela solo las poses estáticas? ¿El retraso cuenta desde el intento o desde la petición (`eAIBase.c:10869`)? Si no es así, ¿qué vía pública consume una sola petición sin que el job escriba?
2. ¿Qué facción no ataca al jugador sin configuración extra, para `Fr`: `Passive`, `Civilian` u otra?
3. ¿El nombre normalizado de §3.1 es el que `Apply` carga tras su propia normalización (`ExpansionLoadout.c:3199`), y `ExpansionPrefab.Load(nombre, true)` da null con un fichero que no se parsea (`ExpansionPrefab.c:12`)?
4. ¿`eAIGroup.GetGroupByID(id, false)` da null tras el `Delete()` diferido, sin que otro grupo reuse ese id antes (`eAIGroup.c:105`, :204)?
5. **Resuelta (#268 @78f7919e):** el cierre observa efectos reales, no espera jobs. El emote puede observar hasta 30 s, pero el stop inicia el borrado de su miembro y el observador común censará ese residuo hasta 25 s; no hay otro job de release ni un plazo diseñado para exceder el cierre. El coordinator limita la espera de la ronda a 40 s y conserva OWED hasta una de las pruebas de §5.3. La activación Enforce sigue siendo prerrequisito explícito, no una capacidad del PBO v12 actual.

## 9. Ficheros de la implementación futura

El único fichero modificado por este PR de plan es `plans/2026-10-24-expansion-ai-adapter.md`. La lista siguiente delimita trabajo posterior y no autoriza ejecutarlo en esta revisión.

- `addon/scripts/5_Mission/MCPBridge.c`: los tres ganchos generales y sus llamadas, más los dos de stop/censo de §2.1, sin tipos de Expansion. La activación completa de cierre y sus tipos base se integra como dependencia de §5.1; no se limita a añadir cinco métodos vacíos.
- `addon_expansion_ai/` (nuevo): `$PBOPREFIX$`, `config.cpp` y `scripts/5_Mission/MCPExpansionAI.c`.
- `tools/dayz_mcp/`:
  - `server.py`: las tres tools y `object_id` en `actor_emote`;
  - `loopback.py`: esquemas, admisión (incluida la compuerta de cierre), `RELEASE_REGISTRY` y `_OWNER_CLEANUP_COMMANDS` para abandono; sin libro por lease ni releases del adaptador desde `cleanup_owner`;
  - `bridge_readiness.py`: el contrato del adaptador, los cinco en `_BRIDGE_COMMAND_TOOLS` y su opcionalidad en el censo;
  - `session_coordination.py`: `ai_group_state` en `READ_ONLY_COMMANDS`.
- `tools/tests/`: los criterios de §6.
- Docs:
  - `README.md`: el recuento de tools, que sube en 3;
  - `CHANGELOG.md`: resumir el cambio y verificar el tamaño real y las reglas vigentes al implementarlo;
  - `PROJECT-MAP.md`;
  - `docs/expansion-ai-adapter.md`: cómo se construye y se carga, con la licencia.
- La lista propuesta no modifica los 21 ficheros de `tools/packaged-modules.lock.json` de main @de12fd6b (los cuatro módulos Python de arriba no están), `approved-launchers.json` ni el lanzador nativo. Revalidar esa lista al integrar las dependencias. La futura implementación cambia el PBO base y añade el opcional: requiere build, revisión y ronda en juego propios. Este PR no toca PBO; el merge sigue siendo decisión de Guillermo.

## 10. Cambios de la revisión 2

Mapa histórico de la revisión 2; las filas H3/H12 quedan sustituidas por el cierre basado en censo de la revisión 3 (§11).

| Qué | Dónde |
|---|---|
| H1, registro tipado | §2.3, §3.1 (punto 9), E3 |
| H2, lectura sin lease | §3.4, §4, §9, O4, paso 13 |
| H3, cleanup confirmado | Históricamente ligado a jobs; ahora §2.1, §2.3, §3.1 (punto 8), §3.6, §5, E4, E6 y paso 13 usan prueba real y censo. |
| H4, loadout | §3.1 (punto 5), E1, paso 5, §8 (3) |
| H5, velocidades | §3.2, §3.3, E5, paso 6 |
| H6, consumo del emote | §3.5 (las capas y el punto 5), E8, paso 11, §8 (1) |
| H7, `end` | §3.5 (`end`), E8 |
| H8, exclusión y correlación | §3.5 (puntos 2 y 3, `started`), E8 |
| H9, oráculos de estado | pasos 8 y 9 |
| H10, reposo y combate | §3.4, fixtures de §7, pasos 2, 3, 6, 7, 11 y 12 |
| H11, positivo del emote | §3.4, §3.5, paso 11 |
| H12, contador del cleanup | El libro por lease y su contador se retiran en r3: §5.2, O5/O6 y paso 13 verifican abandono y cierre por peer. |
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

## 11. Revision 3

**Procedencia y límite de la revisión.** El BRIEF sellado resume la revisión de Sol sobre `1dde8b966547dc34cdf60e5d74be7fd7dcacdc8f` como 2 P1 y 2 P2. El texto original de esos hallazgos no está disponible: la máquina que lo contiene está offline. Los dos P1 se toman de ese resumen. **El mapeo de los dos P2 se ha reconstruido sin el texto original**: P2-R1 y P2-R2 son riesgos concretos identificados al contrastar este plan con las fuentes fijadas, no citas ni identificadores originales de Sol. No se afirma que coincidan con sus dos P2; esa correspondencia y un veredicto Sol OK quedan pendientes de revisión externa.

| Hallazgo / procedencia | Respuesta de la revisión 3 | Secciones y verificación prevista |
|---|---|---|
| P1-1, resumen del BRIEF: contrato ligado al diseño antiguo de #268 (`e1fdd66`) | Reanclar a #268 @78f7919e: FIFO/coordinator implementados, cierre inerte para v12 sin censo, `close_not_supported` saldado sin OWED, y activación futura del PBO separada. Usar stop + censo, sin libro por lease ni `lease_barrier`. | Base; §2.1–2.2; §3.6; §5.1–5.2; §8 (5); §9; O6/O13/O14, E6 y paso 13. |
| P1-2, resumen del BRIEF: timeout/deuda de `ai_group_release` sin resolver | Release interno de abandono con respuesta inmediata, sin job propio; observador común de 25 s, espera de ronda de 40 s, residuo OWED persistente. Censo limpio válido o salida acreditada del servidor elimina deuda; un timeout o un cliente muerto no fingen limpieza. | §2.3; §3.1/3.6; §5.2–5.3; O10–O12, E4/E6 y contingencia del paso 13. |
| P2-R1, reconstruido: admisión opcional con rama de error inalcanzable y soporte de cierre implícito | Orden explícito de censo/instancia/generación → `ach` → verbo → `xai` → soporte de cierre para nuevas mutaciones. Mantener errores comunes, lectura sin lease, opcionalidad del comparador y política live. | §2.2; §4–5.1; O1/O2/O4/O7/O9/O13/O14. |
| P2-R2, reconstruido: citas y oráculos de ronda anclados a versiones anteriores | Main @de12fd6b, #262 integrado, #266 @c4502d1 marcado pendiente de merge; B1 `profile: "test"` y readback explícitos. Paso 13 usa `outstanding`/`unconfirmed` y foto `settled`, sin contador de releases ni estado `confirming`. Distinguir plan, implementación futura y evidencia. | Base; citas MCP de §2–4/6–7; §7 (preparación 5, combate, paso 13); §9–11. |

**CAUSA RAÍZ:** la revisión anterior vinculaba la vida de los grupos a una barrera de jobs de #268 que fue sustituida por cierres con censo y deuda por peer; además, el PBO del head actual aún no activa esos cierres. Un timeout del job no podía acreditar la desaparición de sus efectos.

**Estado de entrega:** revisión documental sobre el merge ya presente de main @de12fd6b, con contrato #268 leído desde git @78f7919e. Solo cambia este plan. No se ejecutan los criterios futuros O/E ni la ronda en juego, ni se afirma CI 4/4, revisión Sol OK o sello Auditor para este delta sin commit. El cuerpo de la PR se entrega como borrador para Orq/Guillermo; el ejecutor no lo publica. No hay merge, rebase, commit ni push en esta revisión.
