# G0 — diagnóstico de drivability owner-client

Convención de citas: `vanilla/...` se resuelve desde la raíz read-only de scripts vanilla indicada en el briefing. Las demás rutas son relativas a este clon. El veredicto offline es concreto: `is_authority_owner=0` es el valor esperado cuando el coche tiene dueño cliente; no es la anomalía. El rojo antiguo no conserva suficiente telemetría ni la identidad del build para decidir entre actuador y entorno, pero el control positivo cercano permite resolverlo en una sola sesión ABBA con cuatro spawns equivalentes y trace raw.

## 1. Hechos observados

### 1.1 Artefacto negativo que bloqueó G0

Sólo se usan los campos enumerados aquí; no se atribuye semántica a los campos DTO que el `spawn` dejó a cero por defecto.

| Campo JSON | Celda `client mode=''` | Celda `client mode='suppress'` | Fuente |
|---|---:|---:|---|
| `vehicle` | `"CivilianSedan"` | `"CivilianSedan"` | `evidence/_tramoA-gate-clientonly.json:2` |
| `server_condition` | `false` | `false` | `evidence/_tramoA-gate-clientonly.json:3` |
| `cell` | `"client mode=''"` | `"client mode='suppress'"` | `evidence/_tramoA-gate-clientonly.json:6`, `evidence/_tramoA-gate-clientonly.json:24` |
| `PASS` | `false` | `false` | `evidence/_tramoA-gate-clientonly.json:7`, `evidence/_tramoA-gate-clientonly.json:25` |
| `pos_delta` | `0.1258208304643631` m | `0.11511760205030441` m | `evidence/_tramoA-gate-clientonly.json:8`, `evidence/_tramoA-gate-clientonly.json:26` |
| `speedo_max` | `0.0` | `0.0` | `evidence/_tramoA-gate-clientonly.json:9`, `evidence/_tramoA-gate-clientonly.json:27` |
| `engine_on_server` | `true` | `true` | `evidence/_tramoA-gate-clientonly.json:10`, `evidence/_tramoA-gate-clientonly.json:28` |
| `vehicle_fixture_ready` | `true` | `true` | `evidence/_tramoA-gate-clientonly.json:11`, `evidence/_tramoA-gate-clientonly.json:29` |
| `is_owner` | `1` | `1` | `evidence/_tramoA-gate-clientonly.json:12`, `evidence/_tramoA-gate-clientonly.json:30` |
| `is_authority_owner` | `0` | `0` | `evidence/_tramoA-gate-clientonly.json:13`, `evidence/_tramoA-gate-clientonly.json:31` |
| `owner_identity` | `"76561198141021937"` | `"76561198141021937"` | `evidence/_tramoA-gate-clientonly.json:14`, `evidence/_tramoA-gate-clientonly.json:32` |
| `net_strategy` | `2` | `2` | `evidence/_tramoA-gate-clientonly.json:15`, `evidence/_tramoA-gate-clientonly.json:33` |
| `net_id` | `[23, 0]` | `[23, 0]` | `evidence/_tramoA-gate-clientonly.json:16`, `evidence/_tramoA-gate-clientonly.json:34` |
| `error` | `""` | `""` | `evidence/_tramoA-gate-clientonly.json:20`, `evidence/_tramoA-gate-clientonly.json:38` |
| `interpretation` | `"inconclusive -> inspect raw"` | `"inconclusive -> inspect raw"` | `evidence/_tramoA-gate-clientonly.json:21`, `evidence/_tramoA-gate-clientonly.json:39` |

El `spawn` fue `id=13`, `ok=1`, `type="CivilianSedan"`, `found=1` y devolvió `pos_real=[6063.095703125, 8.35513687133789, 1931.7137451171875]`; `car_pos` contiene exactamente el mismo vector. El resultado global fue `overall_PASS=false`. `evidence/_tramoA-gate-clientonly.json:42`, `evidence/_tramoA-gate-clientonly.json:48`, `evidence/_tramoA-gate-clientonly.json:49`, `evidence/_tramoA-gate-clientonly.json:56`, `evidence/_tramoA-gate-clientonly.json:57`, `evidence/_tramoA-gate-clientonly.json:62`, `evidence/_tramoA-gate-clientonly.json:83`.

El criterio contractual G0 era `pos_delta>1.0 m` tras dos segundos a throttle `1.0`, con ownership y net id reportados; por eso ambos resultados son rojos reales respecto al criterio, no excepciones ni crashes. `product-spec.md:113`.

### 1.2 Control positivo disponible en `evidence/`

El artefacto de verbos contiene un control positivo que no debe omitirse del diagnóstico:

| Campo | Valor exacto | Fuente |
|---|---:|---|
| `get_in.seated` / `seat` / `vehicle_fixture_ready` | `1` / `"driver"` / `1` | `evidence/_tramoA_verbs_verdict.json:15`, `evidence/_tramoA_verbs_verdict.json:16`, `evidence/_tramoA_verbs_verdict.json:18` |
| ownership al sentarse | `net_strategy=2`, `is_owner=1`, `owner_identity="76561198141021937"`, `net_id=[23,0]`, `is_authority_owner=0` | `evidence/_tramoA_verbs_verdict.json:23`, `evidence/_tramoA_verbs_verdict.json:24`, `evidence/_tramoA_verbs_verdict.json:25`, `evidence/_tramoA_verbs_verdict.json:26`, `evidence/_tramoA_verbs_verdict.json:27`, `evidence/_tramoA_verbs_verdict.json:28` |
| telemetría inicial | `pos=[6063.013671875,5.880380630493164,1972.0299072265625]`, `engine_on_server=1`, `speedo_max=1.3316757678985596`, `gear=1` | `evidence/_tramoA_verbs_verdict.json:79`, `evidence/_tramoA_verbs_verdict.json:89`, `evidence/_tramoA_verbs_verdict.json:90`, `evidence/_tramoA_verbs_verdict.json:92` |
| telemetría final | `pos=[6062.75634765625,4.244087219238281,2007.4820556640625]`, `engine_on_server=1`, `speedo_max=39.821712493896484`, `gear=5` | `evidence/_tramoA_verbs_verdict.json:149`, `evidence/_tramoA_verbs_verdict.json:159`, `evidence/_tramoA_verbs_verdict.json:160`, `evidence/_tramoA_verbs_verdict.json:162` |
| ownership final | `net_strategy=2`, `is_owner=1`, `owner_identity="76561198141021937"`, `net_id=[23,0]`, `is_authority_owner=0` | `evidence/_tramoA_verbs_verdict.json:163`, `evidence/_tramoA_verbs_verdict.json:164`, `evidence/_tramoA_verbs_verdict.json:165`, `evidence/_tramoA_verbs_verdict.json:166`, `evidence/_tramoA_verbs_verdict.json:167`, `evidence/_tramoA_verbs_verdict.json:168` |
| veredicto del control | `pos_delta=35.49082276752407`, `telemetry_speedo=39.821712493896484`, `seated=true`, `is_owner=1`, `PASS=true` | `evidence/_tramoA_verbs_verdict.json:225`, `evidence/_tramoA_verbs_verdict.json:226`, `evidence/_tramoA_verbs_verdict.json:227`, `evidence/_tramoA_verbs_verdict.json:228`, `evidence/_tramoA_verbs_verdict.json:229` |

### 1.3 Comparación diferencial, sin presuponer que sólo cambió la posición

| Dimensión | Estado |
|---|---|
| Classname | `[VERIFIED]` `CivilianSedan` en el rojo. `[UNCHECKED]` en el control: su JSON no guarda classname; `car_pos` no lo demuestra. `evidence/_tramoA-gate-clientonly.json:2`, `evidence/_tramoA_verbs_verdict.json:210` |
| Ownership observable | `[VERIFIED]` mismos valores publicados: `is_owner=1`, `is_authority_owner=0`, `owner_identity="76561198141021937"`, `net_strategy=2`, net id numérico `[23,0]`. Las citas están en las dos tablas anteriores. |
| Resultado físico | `[VERIFIED]` rojo `0.115–0.126 m` y `0.0 km/h`; control `35.49082276752407 m` y `39.821712493896484 km/h`. Las citas están en las dos tablas anteriores. |
| Posición | `[VERIFIED]` el rojo parte de z=`1931.7137451171875`; el control parte de z=`1972.0299072265625`, unos 40 m más al norte. `evidence/_tramoA-gate-clientonly.json:42`, `evidence/_tramoA_verbs_verdict.json:79` |
| Camino de mando | `[VERIFIED]` el rojo sólo publica etiquetas `client mode=...`; no guarda el nombre del command. El control publica un resultado `control` entre `telemetry0` y `telemetry1`; el JSON tampoco guarda el nombre del command. `evidence/_tramoA-gate-clientonly.json:6`, `evidence/_tramoA_verbs_verdict.json:69`, `evidence/_tramoA_verbs_verdict.json:106`, `evidence/_tramoA_verbs_verdict.json:139` |
| Build/PBO, commit y versión de bridge | `[UNCHECKED]` ninguno de los dos JSON los guarda. |
| Throttle solicitado/aplicado por frame | `[UNCHECKED]` no está en ninguno de los dos JSON antiguos. |
| RPM, marcha del rojo, ruedas, contactos, orientación y geometría delante | `[UNCHECKED]` no están en el artefacto rojo. |
| Estado inicial completo del coche y daño posterior | `[UNCHECKED]`; `vehicle_fixture_ready=true` no cubre todas esas dimensiones. |

Conclusión de la comparación: el control positivo refuta que `is_authority_owner=0`, `net_strategy=2` o ese patrón de ownership impidan por sí solos conducir. No prueba todavía si el rojo fue un build/actuador anterior, un obstáculo en la primera posición o ambos.

## 2. Qué mide `is_authority_owner`

### 2.1 Camino exacto del campo en el addon

El DTO de resultado declara `is_authority_owner` junto a `is_owner`, `owner_identity` y el net id. `addon/scripts/5_Mission/MCPMessages.c:434`, `addon/scripts/5_Mission/MCPMessages.c:435`, `addon/scripts/5_Mission/MCPMessages.c:436`, `addon/scripts/5_Mission/MCPMessages.c:437`, `addon/scripts/5_Mission/MCPMessages.c:438`, `addon/scripts/5_Mission/MCPMessages.c:439`.

En el probe cliente, `CaptureDriveProbeClientOwnership` lee directamente estas APIs del `CarScript`:

- `GetNetworkMoveStrategy()` → `job.net_strategy`;
- `IsOwner()` → `job.is_owner`;
- `IsAuthorityOwner()` → `job.is_authority_owner`;
- `GetOwnerIdentity()` → `job.owner_identity`;
- `GetNetworkID()` → los dos enteros del net id.

No hay inferencia conductual en esa captura: son lecturas directas del objeto coche. `addon/scripts/5_Mission/MCPClientBridge.c:2482`, `addon/scripts/5_Mission/MCPClientBridge.c:2489`, `addon/scripts/5_Mission/MCPClientBridge.c:2490`, `addon/scripts/5_Mission/MCPClientBridge.c:2491`, `addon/scripts/5_Mission/MCPClientBridge.c:2493`, `addon/scripts/5_Mission/MCPClientBridge.c:2496`, `addon/scripts/5_Mission/MCPClientBridge.c:2503`, `addon/scripts/5_Mission/MCPClientBridge.c:2505`, `addon/scripts/5_Mission/MCPClientBridge.c:2506`, `addon/scripts/5_Mission/MCPClientBridge.c:2507`. La rama de éxito copia después esos campos al resultado publicado. `addon/scripts/5_Mission/MCPClientBridge.c:2701`, `addon/scripts/5_Mission/MCPClientBridge.c:2710`, `addon/scripts/5_Mission/MCPClientBridge.c:2711`, `addon/scripts/5_Mission/MCPClientBridge.c:2712`, `addon/scripts/5_Mission/MCPClientBridge.c:2713`, `addon/scripts/5_Mission/MCPClientBridge.c:2714`, `addon/scripts/5_Mission/MCPClientBridge.c:2715`.

El trace nuevo vuelve a leer `car.IsOwner()` y `car.IsAuthorityOwner()` en cada muestra; no reutiliza el resultado del probe. `addon/scripts/4_World/MCP_CarScript.c:538`, `addon/scripts/4_World/MCP_CarScript.c:539`, `addon/scripts/4_World/MCP_CarScript.c:540`, `addon/scripts/4_World/MCP_CarScript.c:541`, `addon/scripts/4_World/MCP_CarScript.c:542`, `addon/scripts/4_World/MCP_CarScript.c:543`.

### 2.2 Semántica de engine y veredicto

Vanilla define:

- `IsOwner()`: verdadero si el `Pawn` se simula por el owner;
- `IsAuthority()`: verdadero si se simula en una autoridad;
- `IsAuthorityOwner()`: verdadero si se simula en una autoridad **y no tiene owner**;
- `GetOwnerIdentity()`: la identidad que posee el `Pawn`.

Son los comentarios y firmas de la propia API nativa, no una interpretación del addon. `vanilla/3_game/entities/pawn.c:191`, `vanilla/3_game/entities/pawn.c:193`, `vanilla/3_game/entities/pawn.c:194`, `vanilla/3_game/entities/pawn.c:196`, `vanilla/3_game/entities/pawn.c:197`, `vanilla/3_game/entities/pawn.c:199`, `vanilla/3_game/entities/pawn.c:200`, `vanilla/3_game/entities/pawn.c:208`, `vanilla/3_game/entities/pawn.c:209`.

El addon no deja el `2` a una suposición ordinal: su encoder devuelve explícitamente `2` para `NetworkMoveStrategy.PHYSICS`. `addon/scripts/5_Mission/MCPClientBridge.c:2547`, `addon/scripts/5_Mission/MCPClientBridge.c:2559`, `addon/scripts/5_Mission/MCPClientBridge.c:2561`. En vanilla, PHYSICS es además el tercer miembro del enum y describe reconciliación del owner mediante un buffer fijo de movimientos con re-simulación física. `vanilla/3_game/entities/pawn.c:134`, `vanilla/3_game/entities/pawn.c:138`, `vanilla/3_game/entities/pawn.c:140`, `vanilla/3_game/entities/pawn.c:141`, `vanilla/3_game/entities/pawn.c:143`, `vanilla/3_game/entities/pawn.c:144`, `vanilla/3_game/entities/pawn.c:146`, `vanilla/3_game/entities/pawn.c:147`. El propio `CarScript` vanilla hace que, para PHYSICS, el camino no-servidor dependa de `IsOwner()`. `vanilla/4_world/entities/vehicles/carscript.c:3220`, `vanilla/4_world/entities/vehicles/carscript.c:3222`, `vanilla/4_world/entities/vehicles/carscript.c:3224`, `vanilla/4_world/entities/vehicles/carscript.c:3225`, `vanilla/4_world/entities/vehicles/carscript.c:3230`.

**Veredicto:** en el peer cliente que posee el coche, `is_owner=1` e `is_authority_owner=0` es la combinación esperable. `is_authority_owner=1` describiría la rama de autoridad sin owner, no “cliente con autoridad física”. Además, el control positivo condujo con `is_authority_owner=0`, así que el valor queda refutado como causa del rojo. `evidence/_tramoA_verbs_verdict.json:159`, `evidence/_tramoA_verbs_verdict.json:160`, `evidence/_tramoA_verbs_verdict.json:164`, `evidence/_tramoA_verbs_verdict.json:168`, `evidence/_tramoA_verbs_verdict.json:225`.

## 3. Hipótesis rankeadas

El ranking se refiere a la explicación del artefacto rojo antiguo. Para una corrida con el árbol actual, H1 se convierte primero en una comprobación de procedencia del PBO, porque el source ya contiene el arreglo de orden.

### H1 — El throttle del artefacto no sobrevivió al paso de input; build/actuador distinto del árbol actual

**Por qué está primera.** Vanilla documenta que `SetThrottle` fija un valor **futuro**, no una entrada durable. `vanilla/3_game/vehicles/car.c:201`, `vanilla/3_game/vehicles/car.c:202`. También documenta que otros sistemas pueden cambiar el estado y recomienda guardar el input custom fuera y llamar a los setters desde `OnInput`. `vanilla/3_game/vehicles/transport.c:254`, `vanilla/3_game/vehicles/transport.c:257`, `vanilla/3_game/vehicles/transport.c:258`, `vanilla/3_game/vehicles/transport.c:262`.

El árbol actual implementa precisamente esa forma: el handler guarda el holder con `MCPCarDrive.Set`; `CarScript.OnInput` llama primero a `super.OnInput(dt)` y después aplica throttle, steering, brake y handbrake; la captura ocurre después. `addon/scripts/5_Mission/MCPClientBridge.c:882`, `addon/scripts/5_Mission/MCPClientBridge.c:888`, `addon/scripts/4_World/MCP_CarScript.c:603`, `addon/scripts/4_World/MCP_CarScript.c:605`, `addon/scripts/4_World/MCP_CarScript.c:607`, `addon/scripts/4_World/MCP_CarScript.c:655`, `addon/scripts/4_World/MCP_CarScript.c:656`, `addon/scripts/4_World/MCP_CarScript.c:657`, `addon/scripts/4_World/MCP_CarScript.c:658`, `addon/scripts/4_World/MCP_CarScript.c:663`. El `worldScriptModule` que debe incluir ese archivo está declarado en el addon. `addon/config.cpp:21`, `addon/config.cpp:24`, `addon/config.cpp:27`.

El probe actual también refresca el holder tanto al entrar en DRIVE como durante SAMPLE. `addon/scripts/5_Mission/MCPClientBridge.c:2414`, `addon/scripts/5_Mission/MCPClientBridge.c:2425`, `addon/scripts/5_Mission/MCPClientBridge.c:2436`, `addon/scripts/5_Mission/MCPClientBridge.c:2446`. Por tanto, si el próximo juego carga estos bytes y `OnInput` corre, una muestra tomada al final de ese método debe mostrar `control_active=true`, `throttle_requested=1.0` y `throttle_applied≈1.0`; el trace lee requested y applied por separado. `addon/scripts/4_World/MCP_CarScript.c:473`, `addon/scripts/4_World/MCP_CarScript.c:476`, `addon/scripts/4_World/MCP_CarScript.c:488`.

- **Observación discriminante:** ambas posiciones fallan y el trace da `control_active=true`, `throttle_requested=1.0`, pero `throttle_applied≈0.0` durante todo el tramo; o `control_active=false` pese al resultado `ok` de `vehicle_control`.
- **La refuta:** al menos una celda control muestra requested≈applied≈`1.0` y mueve más de un metro dentro de los dos segundos contractuales.
- **Límite:** requested=`1`/applied=`0` también puede ocurrir mientras la rama `engineReady` no llega a aplicar los setters; H4 explica cómo separarlo parcialmente.

### H2 — La coordenada original bloquea físicamente el coche

**Por qué está segunda.** El único control positivo disponible parte unos 40 m al norte y alcanza `35.49082276752407 m`/`39.821712493896484 km/h` con los mismos valores publicados de ownership; la posición es una diferencia verificada, pero el JSON rojo no contiene geometría ni contactos. `evidence/_tramoA-gate-clientonly.json:42`, `evidence/_tramoA-gate-clientonly.json:45`, `evidence/_tramoA_verbs_verdict.json:79`, `evidence/_tramoA_verbs_verdict.json:82`, `evidence/_tramoA_verbs_verdict.json:225`, `evidence/_tramoA_verbs_verdict.json:226`.

El trace actual captura cuatro contactos de rueda, cuatro velocidades angulares y, por separado, contactos corporales con zona, impulso, normal y penetración. `addon/scripts/4_World/MCP_CarScript.c:493`, `addon/scripts/4_World/MCP_CarScript.c:495`, `addon/scripts/4_World/MCP_CarScript.c:506`, `addon/scripts/4_World/MCP_CarScript.c:507`, `addon/scripts/4_World/MCP_CarScript.c:511`, `addon/scripts/4_World/MCP_CarScript.c:512`, `addon/scripts/4_World/MCP_CarScript.c:516`, `addon/scripts/4_World/MCP_CarScript.c:517`, `addon/scripts/4_World/MCP_CarScript.c:521`, `addon/scripts/4_World/MCP_CarScript.c:522`, `addon/scripts/4_World/MCP_CarScript.c:544`, `addon/scripts/4_World/MCP_CarScript.c:545`, `addon/scripts/4_World/MCP_CarScript.c:546`, `addon/scripts/4_World/MCP_CarScript.c:547`, `addon/scripts/4_World/MCP_CarScript.c:551`, `addon/scripts/4_World/MCP_CarScript.c:554`. Vanilla define `WheelHasContact` y `WheelGetAngularVelocity` como observaciones físicas nativas. `vanilla/3_game/vehicles/car.c:285`, `vanilla/3_game/vehicles/car.c:290`, `vanilla/3_game/vehicles/car.c:292`, `vanilla/3_game/vehicles/car.c:297`.

- **Observación discriminante:** dos spawns nuevos de `CivilianSedan`, preparados y conducidos con la misma secuencia en la misma sesión, superan `1 m` a los dos segundos en la posición control y no lo superan en la posición original con owner, input aplicado, motor, marcha, orientación y ruedas equivalentes; en la celda original aparecen contactos corporales/impulso o ruedas girando sin desplazamiento.
- **La refuta:** ambos spawns fallan con el mismo patrón, o ambos pasan sin contacto significativo.

### H3 — `vehicle_fixture_ready=true` ocultó un estado de transmisión/ruedas/suelo no conducible

El predicado de fixture cliente sólo exige que `WheelCountPresent()==WheelCount()`, combustible, y las piezas vitales batería/bujía que correspondan. No comprueba contacto con suelo, rueda bloqueada, marcha, RPM, daño de motor, orientación ni colisión corporal. `addon/scripts/5_Mission/MCPClientBridge.c:2510`, `addon/scripts/5_Mission/MCPClientBridge.c:2517`, `addon/scripts/5_Mission/MCPClientBridge.c:2522`, `addon/scripts/5_Mission/MCPClientBridge.c:2527`, `addon/scripts/5_Mission/MCPClientBridge.c:2535`, `addon/scripts/5_Mission/MCPClientBridge.c:2544`. Vanilla distingue el número de hubs del número de ruedas presentes. `vanilla/3_game/vehicles/car.c:348`, `vanilla/3_game/vehicles/car.c:349`, `vanilla/3_game/vehicles/car.c:351`, `vanilla/3_game/vehicles/car.c:352`.

La marcha futura se lee con `GetGear`; en el enum nativo `REVERSE=0`, `NEUTRAL=1`, `FIRST=2`. `vanilla/3_game/vehicles/car.c:42`, `vanilla/3_game/vehicles/car.c:43`, `vanilla/3_game/vehicles/car.c:45`, `vanilla/3_game/vehicles/car.c:46`, `vanilla/3_game/vehicles/car.c:47`, `vanilla/3_game/vehicles/car.c:258`, `vanilla/3_game/vehicles/car.c:259`. El trace actual captura `engine_on`, `gear`, recuentos de ruedas, contactos y velocidades angulares. `addon/scripts/4_World/MCP_CarScript.c:493`, `addon/scripts/4_World/MCP_CarScript.c:495`, `addon/scripts/4_World/MCP_CarScript.c:538`, `addon/scripts/4_World/MCP_CarScript.c:539`.

- **Observación discriminante:** input applied≈`1`, engine_on=`true`, pero `gear=1` todo el tramo; o `wheel_count != wheels_present`; o ninguna rueda tiene contacto; o la marcha es forward y las ruedas no adquieren velocidad angular en la posición control.
- **La refuta:** en la posición control hay `gear>=2`, todas las ruedas presentes, contactos plausibles y velocidad angular creciente, con desplazamiento >1 m a los dos segundos.

### H4 — El muestreo de dos segundos quedó dentro del arranque/stall y nunca alcanzó `engineReady`

El actuador actual no aplica los cuatro setters cuando RPM está por debajo de idle: puede parar/rearrancar el motor y deja `engineReady=false`; sólo en la rama ready aplica throttle. `addon/scripts/4_World/MCP_CarScript.c:616`, `addon/scripts/4_World/MCP_CarScript.c:618`, `addon/scripts/4_World/MCP_CarScript.c:621`, `addon/scripts/4_World/MCP_CarScript.c:623`, `addon/scripts/4_World/MCP_CarScript.c:627`, `addon/scripts/4_World/MCP_CarScript.c:631`, `addon/scripts/4_World/MCP_CarScript.c:634`, `addon/scripts/4_World/MCP_CarScript.c:655`. Vanilla confirma que `EngineGetRPM`, `EngineIsOn`, `EngineStart` y `EngineStop` son estados distintos; engine-on no prueba por sí solo RPM por encima de idle. `vanilla/3_game/vehicles/car.c:237`, `vanilla/3_game/vehicles/car.c:238`, `vanilla/3_game/vehicles/car.c:240`, `vanilla/3_game/vehicles/car.c:241`, `vanilla/3_game/vehicles/car.c:243`, `vanilla/3_game/vehicles/car.c:244`, `vanilla/3_game/vehicles/car.c:246`, `vanilla/3_game/vehicles/car.c:247`.

- **Observación discriminante disponible:** requested=`1`, engine_on flapea o comienza false, applied=`0` al principio y pasa a `1` antes de terminar un trace de cinco segundos.
- **La refuta:** applied=`1` desde el inicio y el coche sigue inmóvil.
- **No separable hoy:** requested=`1`, engine_on=`true`, applied=`0` durante todo el trace no distingue un RPM eternamente bajo de un build sin el actuador, porque ni `vehicle_telemetry` ni `vehicle_trace` exponen RPM. Las muestras declaradas no incluyen RPM. `addon/scripts/4_World/MCP_CarScript.c:29`, `addon/scripts/4_World/MCP_CarScript.c:64`, `addon/scripts/4_World/MCP_CarScript.c:66`, `addon/scripts/4_World/MCP_CarScript.c:67`.

### H5 — Los ~0.12 m son settle/drift, no propulsión, y el gate antiguo carecía de independencia

`GetSpeedometer()` es km/h y el helper absoluto sólo aplica valor absoluto. `vanilla/3_game/vehicles/car.c:112`, `vanilla/3_game/vehicles/car.c:113`, `vanilla/3_game/vehicles/car.c:115`, `vanilla/3_game/vehicles/car.c:116`, `vanilla/3_game/vehicles/car.c:118`. El probe calcula `pos_delta` como longitud 3D entre posición actual y inicial, de modo que un pequeño asentamiento vertical cuenta como desplazamiento aunque `speedo_max` quede a cero. `addon/scripts/5_Mission/MCPClientBridge.c:2453`, `addon/scripts/5_Mission/MCPClientBridge.c:2459`, `addon/scripts/5_Mission/MCPClientBridge.c:2460`.

Las dos celdas antiguas comparten coche, posición y medida. En el árbol actual, el path correspondiente hace que `mode="suppress"` sólo añada `SuppressGameplay` en PREP y que ambas ramas terminen en el mismo `MCPCarDrive.Set`. `addon/scripts/5_Mission/MCPClientBridge.c:2365`, `addon/scripts/5_Mission/MCPClientBridge.c:2367`, `addon/scripts/5_Mission/MCPClientBridge.c:2368`, `addon/scripts/5_Mission/MCPClientBridge.c:2370`, `addon/scripts/5_Mission/MCPClientBridge.c:2425`, `addon/scripts/5_Mission/MCPClientBridge.c:2446`. Por eso repetir `""`/`"suppress"` con el árbol actual no discriminaría entorno ni readback del actuator; la identidad del build antiguo sigue no verificada.

- **Observación discriminante:** el trace muestra desplazamiento casi exclusivamente vertical, velocidad horizontal próxima a cero y throttle aplicado cero; las dos posiciones A/B no reproducen el mismo delta.
- **La refuta:** desplazamiento horizontal sostenido, velocidad angular de ruedas y speedometer positivo.

## 4. Plan de una sesión in-game

### 4.1 Objetivo y precondición

La sesión ejecuta cuatro celdas **CONTROL→RED→RED→CONTROL**, cada una con un spawn nuevo de `CivilianSedan`, la misma preparación y el mismo mando. El orden ABBA replica cada posición y reduce dos confusores: variación entre entidades y deriva temporal del juego. No se usa `player_teleport`: su implementación server sólo traslada el `Transport` si la copia server del jugador expone `GetCommand_Vehicle`; si no, llama `player.SetPosition`, y los JSON antiguos no publican ese estado server-authoritative. `addon/scripts/5_Mission/MCPBridge.c:1117`, `addon/scripts/5_Mission/MCPBridge.c:1119`, `addon/scripts/5_Mission/MCPBridge.c:1145`, `addon/scripts/5_Mission/MCPBridge.c:1146`, `addon/scripts/5_Mission/MCPBridge.c:1153`, `addon/scripts/5_Mission/MCPBridge.c:1155`, `addon/scripts/5_Mission/MCPBridge.c:1158`, `addon/scripts/5_Mission/MCPBridge.c:1163`, `addon/scripts/5_Mission/MCPBridge.c:1165`, `addon/scripts/5_Mission/MCPBridge.c:1166`.

El contraste no presupone identidad entre entidades. Cambiarán `object_id` y posiblemente net id; dentro de cada celda se exige que el net id del get-in coincida con el trace. Entre celdas se exige el mismo classname, owner identity, fixture, orientación inicial equivalente y readback del mando. Si las dos réplicas por posición no concuerdan, el resultado es `INCONCLUSIVE_ENTITY_VARIANCE`, no una conclusión ambiental.

Precondición no sustituible por el gate: arrancar el juego con un PBO construido desde este clon y registrar fuera del MCP el SHA-256 del PBO desplegado. `bridge_status` prueba liveness/version, no identidad byte-exacta del PBO; si no hay hash del artefacto, el resultado se etiqueta `BUILD_UNVERIFIED`.

Las firmas de lease/status están verificadas en `tools/dayz_mcp/server.py:2402`, `tools/dayz_mcp/server.py:2409`, `tools/dayz_mcp/server.py:2410`, `tools/dayz_mcp/server.py:2411`, `tools/dayz_mcp/server.py:2453`, `tools/dayz_mcp/server.py:2456`, `tools/dayz_mcp/server.py:2463`, `tools/dayz_mcp/server.py:2464`, `tools/dayz_mcp/server.py:2471`, `tools/dayz_mcp/server.py:2478`; `bridge_status` en `tools/dayz_mcp/server.py:3388`, `tools/dayz_mcp/server.py:3396`. El payload de status contiene `version_state.server/client`, y `_with_ready` añade el objeto `ready`. `tools/dayz_mcp/core.py:103`, `tools/dayz_mcp/core.py:107`, `tools/dayz_mcp/core.py:108`, `tools/dayz_mcp/core.py:109`, `tools/dayz_mcp/server.py:389`, `tools/dayz_mcp/server.py:391`.

[EXACT]

```text
SESSION0 = session_status()
B0 = bridge_status()
L = session_acquire_wait(
  purpose="G0 ABBA drivability differential",
  max_wait_s=300.0
).lease_token
B = bridge_status()

Gate de setup:
- continuar sólo si B.ready.ready=true,
  B.version_state.server="ok" y B.version_state.client="ok";
- cualquier otro valor => SETUP_FAILED, no veredicto de drivability.
```

### 4.2 Preparación repetible de una celda

`surface_query`, `world_spawn` y `vehicle_prepare_fixture` usan las firmas verificadas en `tools/dayz_mcp/server.py:2788`, `tools/dayz_mcp/server.py:2794`, `tools/dayz_mcp/server.py:2795`, `tools/dayz_mcp/server.py:2796`, `tools/dayz_mcp/server.py:2797`, `tools/dayz_mcp/server.py:2798`, `tools/dayz_mcp/server.py:2799`, `tools/dayz_mcp/server.py:2934`, `tools/dayz_mcp/server.py:2942`, `tools/dayz_mcp/server.py:2943`, `tools/dayz_mcp/server.py:2944`, `tools/dayz_mcp/server.py:2945`, `tools/dayz_mcp/server.py:2946`, `tools/dayz_mcp/server.py:2964`, `tools/dayz_mcp/server.py:2966`, `tools/dayz_mcp/server.py:2967`, `tools/dayz_mcp/server.py:2968`, `tools/dayz_mcp/server.py:2969`. Get-in, engine, control, telemetría, trace y release están en `tools/dayz_mcp/server.py:3400`, `tools/dayz_mcp/server.py:3406`, `tools/dayz_mcp/server.py:3411`, `tools/dayz_mcp/server.py:3417`, `tools/dayz_mcp/server.py:3423`, `tools/dayz_mcp/server.py:3429`, `tools/dayz_mcp/server.py:3430`, `tools/dayz_mcp/server.py:3431`, `tools/dayz_mcp/server.py:3432`, `tools/dayz_mcp/server.py:3433`, `tools/dayz_mcp/server.py:3434`, `tools/dayz_mcp/server.py:3435`, `tools/dayz_mcp/server.py:3456`, `tools/dayz_mcp/server.py:3457`, `tools/dayz_mcp/server.py:3461`, `tools/dayz_mcp/server.py:3464`, `tools/dayz_mcp/server.py:3465`, `tools/dayz_mcp/server.py:3466`, `tools/dayz_mcp/server.py:3467`, `tools/dayz_mcp/server.py:3468`, `tools/dayz_mcp/server.py:3469`, `tools/dayz_mcp/server.py:3470`, `tools/dayz_mcp/server.py:3471`, `tools/dayz_mcp/server.py:3496`, `tools/dayz_mcp/server.py:3502`. `restore_gameplay` está en `tools/dayz_mcp/server.py:3270`, `tools/dayz_mcp/server.py:3274`, `tools/dayz_mcp/server.py:3276`.

[EXACT]

```text
CONTROL = [6063.01416015625, 0.0, 1971.696044921875]
RED     = [6063.095703125, 0.0, 1931.7137451171875]

SURFACE_CONTROL_START = surface_query(
  x=6063.01416015625, z=1971.696044921875, timeout_s=30.0
)
SURFACE_CONTROL_END = surface_query(
  x=6062.75634765625, z=2007.4820556640625, timeout_s=30.0
)
SURFACE_RED = surface_query(
  x=6063.095703125, z=1931.7137451171875, timeout_s=30.0
)
exigir SURFACE_CONTROL_START.ok=1, SURFACE_CONTROL_END.ok=1 y SURFACE_RED.ok=1

PREPARE_SITE(SITE):
  S = world_spawn(
    type="CivilianSedan", pos=SITE, flags=0, rotation=0, timeout_s=30.0
  )
  exigir S.ok=1, S.type="CivilianSedan", S.found=1,
          S.object_id>0 y tres componentes en S.pos_real

  F = vehicle_prepare_fixture(
    type="CivilianSedan", pos=S.pos_real, radius=8.0, timeout_s=30.0
  )
  restore_gameplay(timeout_s=30.0)
  vehicle_release(timeout_s=30.0)
  G = vehicle_get_in_client(pos=S.pos_real, timeout_s=30.0)

  exigir F.ok=1, G.seated=1, G.seat="driver",
          G.vehicle_fixture_ready=1, G.is_owner=1,
          G.is_authority_owner=0, G.net_strategy=2,
          G.owner_identity!="";
  guardar S.object_id, S.pos_real, G.owner_identity y
          [G.net_id_low,G.net_id_high].

  engine_set(mode="start", timeout_s=30.0)
  BASE = vehicle_telemetry(timeout_s=30.0)
  exigir BASE.engine_on_server=1, BASE.is_owner=1,
          BASE.is_authority_owner=0, BASE.net_strategy=2 y
          [BASE.net_id_low,BASE.net_id_high]=[G.net_id_low,G.net_id_high].

  devolver S, F, G y BASE.
```

`flags=0` conserva `ECE_PLACE_ON_SURFACE`, `rotation=0` conserva `RF_DEFAULT` y una Y igual a cero se resuelve mediante `SurfaceY`. `addon/scripts/5_Mission/MCPBridge.c:2439`, `addon/scripts/5_Mission/MCPBridge.c:2443`, `addon/scripts/5_Mission/MCPBridge.c:2444`, `addon/scripts/5_Mission/MCPBridge.c:2482`, `addon/scripts/5_Mission/MCPBridge.c:2485`, `addon/scripts/5_Mission/MCPBridge.c:2487`, `addon/scripts/5_Mission/MCPBridge.c:2490`, `addon/scripts/5_Mission/MCPBridge.c:2501`, `addon/scripts/5_Mission/MCPBridge.c:2506`. El `spawn` real usa `CreateObjectEx`, registra el objeto bajo el id del job y publica ese id como `object_id`, junto con type/found y los tres componentes de `pos_real`. `addon/scripts/5_Mission/MCPBridge.c:540`, `addon/scripts/5_Mission/MCPBridge.c:543`, `addon/scripts/5_Mission/MCPBridge.c:553`, `addon/scripts/5_Mission/MCPBridge.c:571`, `addon/scripts/5_Mission/MCPBridge.c:572`, `addon/scripts/5_Mission/MCPBridge.c:3168`, `addon/scripts/5_Mission/MCPBridge.c:3171`, `addon/scripts/5_Mission/MCPBridge.c:3172`, `addon/scripts/5_Mission/MCPBridge.c:3173`, `addon/scripts/5_Mission/MCPBridge.c:3177`, `addon/scripts/5_Mission/MCPBridge.c:3179`, `addon/scripts/5_Mission/MCPBridge.c:3180`, `addon/scripts/5_Mission/MCPBridge.c:3181`, `addon/scripts/5_Mission/MCPBridge.c:3182`. Si cualquier gate de `PREPARE_SITE` falla después de crear `S`, se intenta `FINISH_SITE` con ese `object_id`; si `world_spawn` no produjo un id, no hay objeto que borrar. La sesión termina `SETUP_FAILED` y no pasa a otra celda.

### 4.3 Macro exacta: hasta 7.5 segundos de captura, cinco de mando observable

El trace acepta `20–60 Hz` y hasta `8192` muestras en el addon. `addon/scripts/4_World/MCP_CarScript.c:147`, `addon/scripts/4_World/MCP_CarScript.c:150`, `addon/scripts/4_World/MCP_CarScript.c:160`. Cada muestra incluye posición, velocidad, direction, requested/applied, ruedas, engine, gear y ownership. `addon/scripts/4_World/MCP_CarScript.c:440`, `addon/scripts/4_World/MCP_CarScript.c:460`, `addon/scripts/4_World/MCP_CarScript.c:463`, `addon/scripts/4_World/MCP_CarScript.c:466`, `addon/scripts/4_World/MCP_CarScript.c:473`, `addon/scripts/4_World/MCP_CarScript.c:488`, `addon/scripts/4_World/MCP_CarScript.c:493`, `addon/scripts/4_World/MCP_CarScript.c:538`, `addon/scripts/4_World/MCP_CarScript.c:540`. Al arrancar, fija owner identity y net id desde el coche y los publica en la vista del trace. `addon/scripts/4_World/MCP_CarScript.c:191`, `addon/scripts/4_World/MCP_CarScript.c:194`, `addon/scripts/4_World/MCP_CarScript.c:200`, `addon/scripts/4_World/MCP_CarScript.c:394`, `addon/scripts/4_World/MCP_CarScript.c:396`, `addon/scripts/4_World/MCP_CarScript.c:397`. En `mode="start"`, la tool exige `trace_id=""` y genera un UUID; los demás modos exigen ese id de 32 hex devuelto. `tools/dayz_mcp/vehicle_trace.py:185`, `tools/dayz_mcp/vehicle_trace.py:186`, `tools/dayz_mcp/vehicle_trace.py:188`, `tools/dayz_mcp/vehicle_trace.py:189`, `tools/dayz_mcp/vehicle_trace.py:190`, `tools/dayz_mcp/vehicle_trace.py:194`, `tools/dayz_mcp/vehicle_trace.py:196`. El TTL elegido, `8.0 s`, está por debajo del máximo `30.0 s` que replica el bridge. `tools/dayz_mcp/server.py:1432`, `tools/dayz_mcp/server.py:1435`, `addon/scripts/5_Mission/MCPClientBridge.c:114`.

[EXACT]

```text
RUN_CELL(G):
  START = vehicle_trace(
    mode="start", trace_id="", cursor=0, limit=1,
    sample_hz=20, max_samples=256, timeout_s=30.0
  )
  exigir START.ok=1 y START.trace.active=true
  TRACE_ID = START.trace.trace_id
  CONTROL_CALL = vehicle_control(
    throttle=1.0, steer=0.0, brake=0.0, handbrake=0.0,
    hold_ttl_s=8.0, timeout_s=30.0
  )
  exigir CONTROL_CALL.ok=1; si no, COMMAND_FAILED y no interpretar drivability.

  scan_cursor=0
  S0=null
  Repetir, sin superar 7.5 s de pared:
    STATUS = vehicle_trace(
      mode="status", trace_id=TRACE_ID, cursor=0, limit=1,
      sample_hz=20, max_samples=256, timeout_s=30.0
    )
    mientras scan_cursor<STATUS.trace.count:
      n=min(64, STATUS.trace.count-scan_cursor)
      LIVE_PAGE = vehicle_trace(
        mode="read", trace_id=TRACE_ID, cursor=scan_cursor, limit=n,
        sample_hz=20, max_samples=256, timeout_s=30.0
      )
      añadir LIVE_PAGE.trace.samples a live_samples
      scan_cursor=LIVE_PAGE.trace.next_cursor
    si S0=null: S0=primera live_sample con control_active=true y
                        abs(throttle_requested-1.0)<=0.001
  hasta S0!=null y la última live_sample.monotonic_s>=S0.monotonic_s+5.0.

  STOP = vehicle_trace(
    mode="stop", trace_id=TRACE_ID, cursor=0, limit=1,
    sample_hz=20, max_samples=256, timeout_s=30.0
  )
  vehicle_control(
    throttle=0.0, steer=0.0, brake=1.0, handbrake=1.0,
    hold_ttl_s=8.0, timeout_s=30.0
  )
  END = vehicle_telemetry(timeout_s=30.0)

  cursor=0
  Repetir:
    PAGE = vehicle_trace(
      mode="read", trace_id=TRACE_ID, cursor=cursor, limit=64,
      sample_hz=20, max_samples=256, timeout_s=30.0
    )
    guardar PAGE.trace.samples sin transformar
    cursor=PAGE.trace.next_cursor
  hasta PAGE.trace.eof=true.

  vehicle_trace(
    mode="clear", trace_id=TRACE_ID, cursor=0, limit=1,
    sample_hz=20, max_samples=256, timeout_s=30.0
  )

  Gate del trace:
  - exigir complete=true, overflow=false, stop_reason="requested",
    ids de trace consistentes y eof=true;
  - exigir que owner_identity y net id del trace coincidan con G;
  - exigir max sample gap<=0.075 s y frecuencia efectiva>=20 Hz entre S0 y S5;
  - si no existe S0 pero el trace es íntegro => H1_CONTROL_NOT_OBSERVABLE;
  - si falta S2/S5 o falla otra condición => TRACE_SETUP_FAILED;
  - devolver END, TRACE_ID y las muestras raw; mantener el freno hasta FINISH_SITE.
```

`View` permite paginar las muestras ya capturadas mientras `active=true`: valida cursor/límite y sólo copia samples cuando `mode="read"`; no contiene un gate que exija trace detenido. `addon/scripts/4_World/MCP_CarScript.c:368`, `addon/scripts/4_World/MCP_CarScript.c:371`, `addon/scripts/4_World/MCP_CarScript.c:376`, `addon/scripts/4_World/MCP_CarScript.c:382`, `addon/scripts/4_World/MCP_CarScript.c:386`, `addon/scripts/4_World/MCP_CarScript.c:401`, `addon/scripts/4_World/MCP_CarScript.c:403`, `addon/scripts/4_World/MCP_CarScript.c:408`, `addon/scripts/4_World/MCP_CarScript.c:409`. Aunque no aparezca S0/S5, se ejecutan `stop`, lectura raw y `clear`; así el fallo conserva evidencia y no deja un trace activo. No se llama `vehicle_release` antes de leer: esa tool aborta y limpia el trace además del holder. `addon/scripts/5_Mission/MCPClientBridge.c:1070`, `addon/scripts/5_Mission/MCPClientBridge.c:1072`, `addon/scripts/5_Mission/MCPClientBridge.c:1073`. Se usa `stop` antes de la paginación final porque fija `complete=true`/`stop_reason="requested"`, conserva muestras y `clear` exige que el trace ya no esté activo. `addon/scripts/4_World/MCP_CarScript.c:277`, `addon/scripts/4_World/MCP_CarScript.c:303`, `addon/scripts/4_World/MCP_CarScript.c:304`, `addon/scripts/4_World/MCP_CarScript.c:305`, `addon/scripts/4_World/MCP_CarScript.c:309`, `addon/scripts/4_World/MCP_CarScript.c:317`, `addon/scripts/4_World/MCP_CarScript.c:322`.

### 4.4 Secuencia ABBA y normalización entre celdas

[EXACT]

```text
CELL_1 = PREPARE_SITE(CONTROL)
CELL_1.RESULT = RUN_CELL(CELL_1.G)
FINISH_SITE(CELL_1)
exigir FINISH_SITE=OK
session_heartbeat(lease_token=L)

CELL_2 = PREPARE_SITE(RED)
CELL_2.RESULT = RUN_CELL(CELL_2.G)
FINISH_SITE(CELL_2)
exigir FINISH_SITE=OK
session_heartbeat(lease_token=L)

CELL_3 = PREPARE_SITE(RED)
CELL_3.RESULT = RUN_CELL(CELL_3.G)
FINISH_SITE(CELL_3)
exigir FINISH_SITE=OK
session_heartbeat(lease_token=L)

CELL_4 = PREPARE_SITE(CONTROL)
CELL_4.RESULT = RUN_CELL(CELL_4.G)
FINISH_SITE(CELL_4)
exigir FINISH_SITE=OK

Gate de comparabilidad antes del árbol de veredicto:
- las cuatro celdas tienen owner_identity idéntico;
- cada trace conserva su propio net id e is_owner=true;
- wheel_count y wheels_present iniciales coinciden entre celdas;
- el producto escalar XZ de las direcciones iniciales de cualquier par es >=0.99;
- si todas tienen S0/S2, las dos réplicas CONTROL tienen el mismo veredicto
  `delta_2s_3d>1 m` / `<=1 m`, y también las dos RED;
- si no aparece S0 en ninguna, saltar sólo la comparación de delta y tomar
  la rama H1_CONTROL_NOT_OBSERVABLE;
- si S0 aparece de forma intermitente entre celdas, emitir INCONCLUSIVE_RUNTIME_VARIANCE.
Si no, emitir INCONCLUSIVE_ENTITY_VARIANCE o SETUP_FAILED según el campo.
```

### 4.5 Árbol de veredicto

Para cada celda, `S0` es la primera muestra con `control_active=true` y `throttle_requested` dentro de `0.001` de `1.0`; así el reloj contractual empieza cuando el holder ya es observable, no cuando se envió la tool. `S2` y `S5` son las primeras muestras que alcanzan `S0.monotonic_s+2.0` y `+5.0`, respectivamente; cada una debe quedar como máximo `0.075 s` después del objetivo. `0.075 s` es el max-gap que el validador deriva como `1.5/sample_hz`, y el mínimo efectivo es `20 Hz`. `tools/dayz_mcp/vehicle_trace.py:627`, `tools/dayz_mcp/vehicle_trace.py:628`. Si el trace es íntegro pero falta S0, eso es la observación H1 `CONTROL_NOT_OBSERVABLE`; si existe S0 pero falta S2/S5, es `TRACE_SETUP_FAILED`. Se calculan `delta_2s_3d`/`delta_2s_xz` entre S0 y S2, y `delta_5s_3d`/`delta_5s_xz` entre S0 y S5. El trace publica secuencia, reloj monotónico, posiciones, `sample_dt_s`, `control_active` y throttle solicitado. `addon/scripts/4_World/MCP_CarScript.c:449`, `addon/scripts/4_World/MCP_CarScript.c:450`, `addon/scripts/4_World/MCP_CarScript.c:451`, `addon/scripts/4_World/MCP_CarScript.c:453`, `addon/scripts/4_World/MCP_CarScript.c:457`, `addon/scripts/4_World/MCP_CarScript.c:460`, `addon/scripts/4_World/MCP_CarScript.c:461`, `addon/scripts/4_World/MCP_CarScript.c:462`, `addon/scripts/4_World/MCP_CarScript.c:473`, `addon/scripts/4_World/MCP_CarScript.c:476`.

G0 se decide **sólo** con `delta_2s_3d>1.0 m`, que procede del criterio externo, no del output transformado. Los cinco segundos restantes discriminan warm-up/stall y no pueden convertir un rojo contractual en verde. La tolerancia requested/applied `<=0.001` procede del criterio G3. Cada posición tiene dos entidades independientes en orden ABBA, y el gate sólo compara posiciones si pasa primero la comparabilidad anterior. `product-spec.md:113`, `product-spec.md:116`.

| Observación | Veredicto |
|---|---|
| Las dos CONTROL dan `delta_2s_3d>1 m`, `abs(applied-requested)<=0.001` y owner estable; las dos RED dan `delta_2s_3d<=1 m` y siguen `delta_5s_3d<=1 m` con las mismas señales y contacto corporal o wheel-spin | **H2 aislada a nivel de posición**. La mecánica concreta sigue abierta entre obstáculo, suelo y contacto; no tocar drivetrain. |
| Las cuatro dan `delta_2s_3d>1 m` | El rojo antiguo **no reproduce** con el build actual; H1 histórica/build o command path anterior queda primera. G0 actual PASS, sin afirmar qué bytes produjeron el JSON antiguo. |
| Las cuatro dan `delta_2s_3d<=1 m` pero `delta_5s_3d>1 m`; requested pasa a applied durante el tramo | **G0 sigue rojo; H4/timing sube**. El movimiento tardío refuta bloqueo físico permanente y no satisface los dos segundos contractuales. |
| En las cuatro falta S0: `control_active=false` o requested no llega a `1` | **H1-holder/target/TTL** en el runtime actual. El command fue aceptado pero el holder no estuvo activo sobre esas entidades. |
| S0 aparece sólo en parte de las celdas | **INCONCLUSIVE_RUNTIME_VARIANCE**; el actuador/holder es intermitente y la comparación de posición no es válida. |
| Las cuatro fallan; requested=`1`, applied≈`0` todo el tramo | **H1/H4 aún juntas**. Si engine_on flapea, H4 sube; si engine_on permanece true, hace falta RPM o prueba de contenido del PBO. No declarar causa raíz. |
| Las cuatro fallan; applied≈`1`, engine_on=true, `gear=1` todo el tramo | **H3-marcha neutral**. |
| Las cuatro fallan; applied≈`1`, gear>=2, ruedas presentes, pero sin velocidad angular | **H3-transmisión/física**. |
| CONTROL pasa dos veces a 2 s; RED falla dos veces a 2 s y 5 s sin contacto corporal ni wheel-spin distinguible | **H2 aislada a nivel de posición, mecanismo no observado**; inspeccionar geometría/suelo antes de tocar código. |
| `delta_2s_3d` es pequeño pero claramente mayor que `delta_2s_xz`, speedometer/velocidad XZ quedan cerca de cero y applied queda en cero | **H5-settle medido**; el delta no es propulsión. Buscar H1/H4 para explicar por qué no hubo input aplicado. |
| Una réplica de la misma posición cambia de lado respecto a `delta_2s_3d>1 m` | **INCONCLUSIVE_ENTITY_VARIANCE**; el efecto no está aislado por posición. |
| Owner/net id cambia dentro de una celda, orientación no comparable, trace overflow, `stop_reason` inesperado, falta S2/S5 o falla el sample-rate gate | **INCONCLUSO/SETUP_FAILED**, no rojo de drivability. |

### 4.6 Cleanup exacto

`ActionGetOutTransport` está registrado en el constructor vanilla y en `PlayerBase`. `vanilla/4_world/classes/useractionscomponent/actionconstructor.c:293`, `vanilla/4_world/entities/manbase/playerbase.c:1681`. Su condición exige estar dentro, que el asiento permita salida y que el área de puerta esté libre; la rama que no supera el umbral de jump-out llama `GetOutVehicle`. `vanilla/4_world/classes/useractionscomponent/actions/interact/actiongetouttransport.c:68`, `vanilla/4_world/classes/useractionscomponent/actions/interact/actiongetouttransport.c:70`, `vanilla/4_world/classes/useractionscomponent/actions/interact/actiongetouttransport.c:76`, `vanilla/4_world/classes/useractionscomponent/actions/interact/actiongetouttransport.c:77`, `vanilla/4_world/classes/useractionscomponent/actions/interact/actiongetouttransport.c:156`, `vanilla/4_world/classes/useractionscomponent/actions/interact/actiongetouttransport.c:161`, `vanilla/4_world/classes/useractionscomponent/actions/interact/actiongetouttransport.c:174`, `vanilla/4_world/classes/useractionscomponent/actions/interact/actiongetouttransport.c:175`. La tool `action_use` y sus args están en `tools/dayz_mcp/server.py:3636`, `tools/dayz_mcp/server.py:3640`, `tools/dayz_mcp/server.py:3641`, `tools/dayz_mcp/server.py:3642`, `tools/dayz_mcp/server.py:3643`, `tools/dayz_mcp/server.py:3644`, `tools/dayz_mcp/server.py:3645`.

[EXACT]

```text
FINISH_SITE(CELL):
  CHECK = vehicle_telemetry(timeout_s=30.0)
  si CHECK.ok=0 y CHECK.error="not_seated":
    vehicle_release(timeout_s=30.0)
    D = object_delete(object_id=CELL.S.object_id, timeout_s=30.0)
    exigir D.ok=1 y D.deleted=1
    restore_gameplay(timeout_s=30.0)
    devolver OK_UNSEATED.
  si CHECK.ok!=1:
    restore_gameplay(timeout_s=30.0)
    devolver CLEANUP_DEGRADED.

  vehicle_control(throttle=0.0, steer=0.0, brake=1.0, handbrake=1.0,
                  hold_ttl_s=8.0, timeout_s=30.0)
  repetir STOPPED = vehicle_telemetry(timeout_s=30.0)
  hasta abs(STOPPED.speedo_max)<0.1 km/h,
  con máximo de 10 s de pared; refrescar el mismo freno a los 4 s.

  engine_set(mode="stop", timeout_s=30.0)
  vehicle_release(timeout_s=30.0)

  si abs(STOPPED.speedo_max)>=0.1:
    NO intentar salir ni borrar el coche en movimiento;
    restore_gameplay(timeout_s=30.0)
    devolver CLEANUP_DEGRADED.

  OUT = action_use(
    action="ActionGetOutTransport",
    classname="CivilianSedan",
    pos=STOPPED.pos_real,
    radius=8.0,
    timeout_s=30.0
  )
  repetir SEAT_CHECK = vehicle_telemetry(timeout_s=30.0)
  hasta SEAT_CHECK.ok=0 y SEAT_CHECK.error="not_seated",
  con máximo de 5 s de pared.

  sólo si SEAT_CHECK.ok=0 y SEAT_CHECK.error="not_seated":
    D = object_delete(object_id=CELL.S.object_id, timeout_s=30.0)
    exigir D.ok=1 y D.deleted=1
    restore_gameplay(timeout_s=30.0)
    devolver OK

  si no:
    NO borrar el coche ocupado;
    restore_gameplay(timeout_s=30.0)
    devolver CLEANUP_DEGRADED.

AL TERMINAR LAS CUATRO CELDAS, o ante cualquier salida prematura:
  intentar FINISH_SITE para la celda activa si existe y aún no terminó
  restore_gameplay(timeout_s=30.0)
  RELEASE = session_release(lease_token=L)
  FINAL_SESSION = session_status()
  registrar RELEASE y FINAL_SESSION incluso si el cleanup fue degradado.
```

`object_delete` acepta sólo el `object_id` positivo devuelto por `world_spawn`; la comprobación cliente de salida evita borrar un transporte ocupado. `tools/dayz_mcp/server.py:2805`, `tools/dayz_mcp/server.py:2808`, `tools/dayz_mcp/server.py:2814`, `tools/dayz_mcp/server.py:2815`, `tools/dayz_mcp/server.py:2818`, `tools/dayz_mcp/server.py:2822`. `ResolveOwnedCar` lee `GetGame().GetPlayer().GetCommand_Vehicle().GetTransport()`; `vehicle_telemetry` publica exactamente `ok=false,error="not_seated"` si ya no lo resuelve. Por eso es un discriminador cliente, no la vista server potencialmente distinta. `addon/scripts/5_Mission/MCPClientBridge.c:2133`, `addon/scripts/5_Mission/MCPClientBridge.c:2135`, `addon/scripts/5_Mission/MCPClientBridge.c:2141`, `addon/scripts/5_Mission/MCPClientBridge.c:2142`, `addon/scripts/5_Mission/MCPClientBridge.c:2144`, `addon/scripts/5_Mission/MCPClientBridge.c:2147`, `addon/scripts/5_Mission/MCPClientBridge.c:894`, `addon/scripts/5_Mission/MCPClientBridge.c:896`, `addon/scripts/5_Mission/MCPClientBridge.c:897`, `addon/scripts/5_Mission/MCPClientBridge.c:899`, `addon/scripts/5_Mission/MCPClientBridge.c:900`. En el bridge server, un id registrado se elimina del mapa y sólo entonces se llama `ObjectDelete`; el resultado `deleted=1` prueba que había un objeto bajo ese id. `addon/scripts/5_Mission/MCPBridge.c:591`, `addon/scripts/5_Mission/MCPBridge.c:597`, `addon/scripts/5_Mission/MCPBridge.c:598`, `addon/scripts/5_Mission/MCPBridge.c:599`, `addon/scripts/5_Mission/MCPBridge.c:601`, `addon/scripts/5_Mission/MCPBridge.c:602`.

## 5. Esbozos de arreglo por hipótesis

No se aplica ninguno hasta ejecutar la sesión anterior. Todos los hunks quedarían fuera del único fichero poseído por este lote.

### H1 — Actuador/build

[DESIGN]

```text
if deployed PBO does not contain the current worldScriptModule and MCP_CarScript:
    rebuild from the pinned commit
    verify the PBO contents and SHA-256 before launch
    rerun the two-site gate without changing source

if trace.control_active is false after vehicle_control returned ok:
    fail vehicle_control with a typed holder_not_active/not_owner result
    include current car net id and holder car net id in diagnostic telemetry

if requested=1 but applied=0 and RPM is healthy:
    keep control application after super.OnInput
    add a source-contract assertion that Capture runs after all four setters
```

El orden correcto ya existe en `addon/scripts/4_World/MCP_CarScript.c:603`, `addon/scripts/4_World/MCP_CarScript.c:605`, `addon/scripts/4_World/MCP_CarScript.c:655`, `addon/scripts/4_World/MCP_CarScript.c:663`; el primer arreglo esperado es procedencia/despliegue, no duplicar otro actuador.

### H2 — Entorno bloqueado

[DESIGN]

```text
gate setup:
    use a known-clear control coordinate first
    run fresh spawns in CONTROL->RED->RED->CONTROL order
    require same classname, fixture, owner, initial orientation and input readback
    require an independent surface/corridor check
    classify body contact + wheel spin + no translation as blocked_environment
    never report a drivetrain failure from the suspect coordinate alone
```

El hunk pertenecería al runner/playbook del gate, no a `CarScript`. No hay runner histórico en este clon que pueda modificarse dentro del scope actual.

### H3 — Fixture/transmisión/ruedas

[DESIGN]

```text
extend diagnostic readiness, not generic fixture readiness, with:
    gear, gearbox type, current/future gear
    wheel_count, wheels_present, per-wheel contact/angular velocity/locked
    engine health and relevant fluid fractions

branch on the observed failing field;
do not widen IsDriveClientVehicleFixtureReady until a field is proven causal.
```

Los destinos condicionales serían `addon/scripts/4_World/MCP_CarScript.c`, `addon/scripts/5_Mission/MCPMessages.c`, el esquema `tools/schemas/vehicle-trace-v1.json` y el normalizador `tools/dayz_mcp/vehicle_trace.py`. El trace ya declara los campos de ruedas y gear en `addon/scripts/4_World/MCP_CarScript.c:56`, `addon/scripts/4_World/MCP_CarScript.c:64`, `addon/scripts/4_World/MCP_CarScript.c:65`, `addon/scripts/4_World/MCP_CarScript.c:67`; ampliar sin el resultado in-game sería instrumentación especulativa.

### H4 — Arranque/RPM

[DESIGN]

```text
add rpm, rpm_idle, rpm_redline, gearbox_type and engine_ready to each trace sample;
rerun the same five-second cell;
only then decide whether to lengthen warm-up or change the engine-ready state machine.
```

No se propone “esperar más” como fix sin medir RPM: `EngineIsOn()` y `EngineGetRPM()` son APIs distintas. `vanilla/3_game/vehicles/car.c:237`, `vanilla/3_game/vehicles/car.c:238`, `vanilla/3_game/vehicles/car.c:240`, `vanilla/3_game/vehicles/car.c:241`.

### H5 — Medición/gate

[DESIGN]

```text
derive verdict from raw first/last trace samples plus horizontal distance;
require positive speed or horizontal velocity and requested/applied agreement;
retain vertical settle as a separate metric;
store PBO SHA-256, bridge version, trace id and raw samples with the verdict.
```

Esto evita que `pos_delta` de settle certifique propulsión y que dos celdas con el mismo actuador/entorno parezcan un control independiente.

## 6. No verificable offline

- Qué PBO/commit produjo exactamente `evidence/_tramoA-gate-clientonly.json`; el artefacto no guarda SHA-256 ni versión.
- Si el PBO que cargará la próxima sesión coincide byte a byte con este clon. `bridge_status` no lo demuestra.
- Throttle applied/requested, RPM, gear, ruedas y contactos durante el rojo antiguo: no viajaron en ese JSON.
- La geometría/colisión real delante de `[6063.095703125,8.35513687133789,1931.7137451171875]`.
- Si requested=`1`/applied=`0` con engine_on estable se debe a RPM bajo o a ausencia del actuador desplegado: la surface actual no expone RPM.
- La causa raíz final. Offline quedan H1/H2 rankeadas y un experimento que las separa; afirmar una de ellas como hecho antes de la corrida violaría la evidencia disponible.

## 7. Decisiones del hallazgo

| Decisión | Resultado | Motivo |
|---|---|---|
| Tratar `is_authority_owner=0` como anomalía | **Rechazada** | La API significa autoridad sin owner; el control positivo condujo con valor `0`. |
| Repetir sólo `mode=""`/`"suppress"` | **Rechazada** | Comparte actuador, posición y medida; no separa H1/H2/H3. |
| Tocar código ahora | **Rechazada** | Falta el discriminador in-game y el árbol actual ya contiene el orden correcto de `OnInput`. |
| Próximo gate | **Aceptado** | ABBA con cuatro spawns equivalentes, posición control versus roja, trace requested/applied + física. |
| Clasificación del síntoma | **Degradation** | El comando termina sin error pero no cumple drivability; no hay proceso muerto, excepción ni evidencia de corrupción. |

## 8. Diff y gates del lote

### Diff

[EXACT]

```text
git diff --no-index --summary -- NUL reviews/2026-08-24-g0-diagnosis.md
 create mode 100644 reviews/2026-08-24-g0-diagnosis.md

git status --short --untracked-files=normal
?? evidence/
?? reviews/

git diff --name-only
<empty>
```

### Gate rojo de existencia

[EXACT]

```text
RED target_exists=False
BASELINE git_status_short:
?? evidence/
BASELINE tracked_diff_names:
```

`evidence/` ya era un untracked de entrada y se mantuvo read-only.

### Gates verdes

[EXACT]

```text
fact_values_negative=PASS checks=38
fact_values_positive=PASS checks=18
citation_mentions=448
citation_unique=409
citation_targets_missing=0
citation_lines_out_of_range=0
citation_lines_blank=0
citation_prefix_forbidden=0
heading_order=PASS count=9
code_fences_balanced=PASS markers=26
code_block_labels=PASS bad=0
absolute_machine_paths=0
status_entries=6
owned_target_entries=1
baseline_evidence_entries=5
unexpected_entries=0
tracked_diff_names=<empty>
utf8_bom=False
nul_bytes=0
diff_check_output=<empty>
final_json=PASS status=ok lote=GPREP paths=1 hallazgos=1
placeholder_count=0
```

### Módulos de test

No se ejecutó ningún módulo de test: el lote es documental, no cambia `.py`/`.c`, no tiene venv y el briefing prohíbe gastar la suite. Los gates ejecutados son de estructura, citas, JSON final, whitespace y boundary de ficheros.

## 9. Handoff operativo

El valor que decide G0 no es `is_authority_owner`; es la combinación independiente de procedencia del build, requested/applied, desplazamiento en el control físico y contraste con la coordenada roja. Si el control despejado vuelve a mover, el grupo G puede cerrar G0 sobre el actuator actual y registrar la coordenada roja como setup inválido. Si no mueve, el trace deja una rama concreta antes de autorizar cualquier hunk.

{"status":"ok","lote":"GPREP","paths":["reviews/2026-08-24-g0-diagnosis.md"],"hallazgos":[{"id":"G0-DIAG","estado":"arreglado","gate":"RED target_exists=False; GREEN fact_values=PASS, citations=PASS, structure=PASS, boundary=PASS, final_json=PASS"}],"fuera_de_mis_ficheros":["Esbozos [DESIGN] H1-H5; no se modificó código"],"no_verificado":["Causa raíz del artefacto histórico","SHA-256 del PBO histórico o de la próxima sesión","Resultados de la sesión in-game ABBA"]}
