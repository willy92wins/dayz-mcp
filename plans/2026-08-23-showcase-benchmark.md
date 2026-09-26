# Showcase + benchmark — DayZ-MCP (5 beats, 45–60 s filmables)

Fecha: 2026-08-23
Estado: prompt congelado ANTES de la primera corrida. No se edita a mitad.
Corrida de ensayo: Grok 4.6 (experto, skills cargadas).
Corrida fría: mismo texto de «Tarea» contra un modelo que solo ve el catálogo MCP.
Grabación (ShadowPlay): después, cuando el ensayo esté verde.

SHA del bloque «Tarea» se calcula al congelar (ver pie).

## Tarea (esto es lo que recibe la corrida fría)

El juego DayZDiag ya está en marcha con el puente MCP. Tienes las tools `dayz-mcp`.
No hay teclado ni ratón de jugador. No inventes nombres de tools.

Haz, en este orden, cinco beats visuales en el mismo sitio. Cada beat tiene que
verse en el cliente renderizado. Tras cada beat, `capture_screenshot`.

1. **Clima y hora.** Pon mediodía despejado (luz clara, sombras duras).
2. **Coche y órbita.** Elige un sitio seguro, spawnea un `CivilianSedan` en el
   suelo (no en el cielo), y encuádralo desde cuatro ángulos con la cámara.
3. **Conducción.** Sienta al jugador local en el coche, arranca el motor y
   muévelo de verdad (no un probe inerte). Tiene que desplazarse >1 m.
4. **Infectados.** Spawnea al menos tres infectados VIVOS (con IA) junto al
   coche, visibles en cámara.
5. **Aviso.** Muestra una notificación vanilla en pantalla (título + detalle
   que identifique DayZ-MCP).

Cuando termines: `session_release` si tienes lease. Si una tool falla, no
inventes otra: o recuperas con el error que te ha dado, o archivas en
`pipeline_feedback` (kind=bug, plantilla tool/args/error/repro) y sigues al
siguiente beat. No lances otro `dayz_test_run`. No mates procesos.

## Criterios pass/fail (idénticos en experto y en frío)

| Beat | PASS | FAIL típico |
|---|---|---|
| 1 Clima+hora | `world_time_set` + `world_weather_set` ok; captura con cielo de día | args incompletos (faltan year/month/day); captura nocturna o negra |
| 2 Coche+órbita | `place_safely` (o equivalente) PASS; `world_spawn` CivilianSedan; `object_inspect`/`entities_query` a <2 m del pedido; 4 capturas con el coche en cuadro | y invertida / objeto a kilómetros / spawn tautológico (`ok=1` pero pos distinta) |
| 3 Conducción | `vehicle_get_in_client` seated; `engine_set start`; `vehicle_control` con hold; telemetría `pos_delta > 1 m` | sentado server-side sin ownership; throttle sin movimiento; ruedas=0 juzgadas como fallo de malla |
| 4 Infectados | ≥3 `world_spawn` con flags 3108; visibles de pie, no ragdoll bajo el mapa | flags=0 (sin IA); classname mal |
| 5 Notify | `notify_players` ok; captura con el popup | sin `show_time`; popup fuera de frame |

## Métricas de fricción (se anotan en ambas corridas)

- Verbo de lease usado: `session_acquire_wait` | `lease_acquire` | `session_acquire` | otro | ninguno
- ¿Usó `playbook_run(name="place_safely")`? sí/no
- Tools inventadas (nombre)
- `lease_required` / `bad_args` / `game_not_ready`: cuántos y si el siguiente paso fue el que nombra el error
- Archivos al buzón: ids
- Reintentos por beat
- Wall-clock de beats 1–5 (sin el boot)
- Cada beat: PASS / FAIL / SKIP + evidencia (tool result path o PNG)

## Receta del ensayo experto (NO va en el prompt frío)

Sitio por defecto: `x=7512, z=7502` (fila de calibración de `place_safely`, in-game 2026-08-16).
Fallback cookbook: `pos=[7086, 0, 7726]` CivilianSedan verificado.

1. `session_status` → caja libre. `bridge_status.ready`.
2. `dayz_test_run(project="DayZ_MCP", mode="all", mission="chernarus", server_wait_s=240)`. Sin `extra_mods` (reemplaza la lista y tira `@DayZ_MCP`).
3. `wait_for(log_matches, pattern="OnStoreLoad SUCCESS", lookback_from="launch")` luego `wait_for(players_at_least, value=1)`. Gate en `satisfied`, no en `ok`.
4. `session_acquire_wait(purpose="showcase-rehearsal")`.
5. `playbook_run(name="place_safely", params={x:7512, z:7502})`. Spawn solo si S2 es PASS limpio, no `PASS_WITH_WARNINGS` de canopy. `pos=[x, S1.y, z]`.
6. Beat 1: `world_time_set(year=2021, month=7, day=15, hour=12, minute=0)` + `world_weather_set(overcast=0.2, rain=0, fog=0)`.
7. Beat 2: `world_spawn(type="CivilianSedan", pos=[x,0,z], flags=0)` → confirmar con `object_inspect`/`entities_query` distancia al pedido. `camera_set(cam_mode="lookat")` ×4 + `capture_screenshot(save_fullres=true)`.
8. Beat 3: `player_teleport` junto al coche → `vehicle_get_in_client(pos=coche)` → `engine_set(mode="start")` → `vehicle_control(throttle=1, steer=0, hold_ttl_s=8)` → `vehicle_telemetry` antes/después.
9. Beat 4: 3× `world_spawn(type="ZmbM_CitizenASkinny_Blue", pos=alrededor, flags=3108)`.
10. Beat 5: `notify_players(show_time=8, title="DayZ-MCP", detail="agent-driven, no keyboard")` + captura.
11. `session_release`. Dejar el run vivo para la corrida fría. No `dayz_test_stop` hasta cerrar el bench.

## Ensayo experto 2026-08-23 (Grok 4.6) — run `e8454507`

SHA fichero `2aa279ba…7248`. SHA Tarea `23ec751b…eb9a`.
Lease: `session_acquire_wait` (no `session_acquire`). Playbook `place_safely` overall=PASS en `x=13000,z=8000` (y=15.42, S2 limpio).

| Beat | Resultado | Evidencia |
|---|---|---|
| 1 Clima+hora | PASS | `world_time_set` 12:00 2021-07-15 ok; `world_weather_set` overcast=0.20 |
| 2 Coche+órbita | PASS | `world_spawn` CivilianSedan `pos_real≈[12999.88, 15.18, 8000.00]` (Δxz 0.12 m vs pedido); `entities_query` distance=0.056 m; PNG `capture_20260823_145708_259.jpg` |
| 3 Conducción | PASS | `vehicle_prepare_fixture` wheel_count=4 fuel=1; `vehicle_get_in_client` seated=driver is_owner=1; `engine_set start`; throttle 10 s; pos `[13000.26,15.33,8000.04]` → `[12999.36,13.73,8054.75]` Δxz≈54.7 m; gear 1→2 (1ª) **sin `gear_shift`** |
| 4 Infectados | PASS con matiz | 3 spawns flags=3108 ok; primera tanda no salió en `entities_query` r=20 (IA se alejó o no indexa DayZInfected). Segunda tanda en cuadro: PNG `capture_20260823_150124_238.jpg` (uno andando en primer plano, otro en el coche) |
| 5 Notify | PASS | `notify_players` sent=1; popup «DayZ-MCP / agent-driven, no keyboard» en `capture_20260823_150003_786.jpg` |

Sitio filmable: vía férrea sur de Berezino ≈ `[13000, 14, 8055]`. Capturas fullres: `C:\Users\guill\AppData\Local\Temp\dayz-mcp-showcase-20260823\`.

### Fricción medida (experto)

- `wait_for(OnStoreLoad SUCCESS, lookback_from=launch)` timeout 301 s / 453k líneas. El jugador YA estaba (`query_all_players`). Buzón `fb-20260823-130230-37f7`.
- `bridge_status.ready` mintió `server_poll_stale` con `last_poll_age_s` del run anterior durante el CE. Tras el primer `query_all_players`, ready=true.
- `wait_for` timeout → `ok:true` (description pública; el test weak-agent espera `ok:false`; daemon `server.py` stale).
- `place_safely` + `lease_acquire`/`session_acquire_wait` + `Requires a lease` en descriptions: el experto no tropezó. No es prueba de junior.
- `vehicle_prepare_fixture` SÍ funciona con `CivilianSedan` (el ítem del buzón era `SUB_BRZ`).
- `gear_shift` no existe; el sedán vanilla subió N→1ª con throttle. El hueco del HANDOFF no tumba este beat.

Run dejado vivo (`RUNNING_IDLE`, sin lease) para el bench frío y para ShadowPlay.

## Fuera de alcance del short

UI de mods (`action_use`/`ui_*`), `vehicle_trace`, `exec_enforce`, doors, disparo.
`gear_shift` no existe (HANDOFF 2026-08-23).
