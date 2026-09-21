### Resumen ejecutivo
- Veredicto: approve with minor changes.
- El diseño base respeta el DPF A1-A5 y la arquitectura: 3 actores, POC server-side, transporte pull async y corrección intencional de API-key en query string.
- No empezaría implementación todavía: R22-001 invalida la medición A2 tal como está escrita, porque puede medir batching del `/poll` en vez de no-bloqueo del tick.
- El resto son ajustes acotados: endurecer Step 0, añadir `CreateRestApi()` al camino verificado, fijar el fixture de posición A1 y corregir una cita `path:line`.

### Matriz de hallazgos
| ID | Sección plan | Severidad | Resumen | Resolución sugerida |
|---|---|---|---|---|
| R22-001 | §3c Test A2 / §7 / §10 | FAIL | La prueba A2 puede fallar en falso y no demuestra que el tick avance mientras REST está en vuelo. | Redefinir A2 con ticks alrededor de una request async pendiente (`tick_poll_sent`/`tick_poll_callback`) y un delay controlado del servidor; no calcular `load_rate` sobre una ráfaga que puede caer en un solo batch. |
| R22-002 | §8 / §9 Step 0 | WARN | Step 0 no cubre todos los riesgos que el propio plan declara como gate antes de construir 3a-3d. | Expandir Step 0 para probar config `$profile:`, parse de batch de 2 comandos, lifetime de callbacks y `POST /result` mínimo. |
| R22-003 | §3a `TryInit` / §11 RestApi | WARN | El plan omite `CreateRestApi()` aunque existe en vanilla y el prior art local lo usa cuando `GetRestApi()` devuelve null. | Verificar y usar patrón get-or-create antes de declarar fallido el camino server-side o saltar al fallback client-first. |
| R22-004 | §3c Test A1 / §10 | WARN | A1 exige comparar contra una coordenada conocida, pero el POC excluye spawn/teleport y el orquestador no fija una posición. | Declarar un fixture de posición determinista: misión con spawn fijo, teleport/setup previo fuera del POC, o actualizar el criterio si solo se va a validar “posición presente/plausible”. |
| R22-005 | §11 fila Identity | WARN | La API existe, pero la cita `path:line` mezcla `Man.GetIdentity()` y `PlayerIdentity.GetName()` en un solo path incorrecto. | Separar la fila: `Man.GetIdentity()` en `3_game/entities/man.c:21`; `PlayerIdentityBase.GetName()` en `3_game/gameplay.c:362`. |

### Hallazgos detallados

#### R22-001 — §3c Test A2 / §7 / §10 — FAIL

Cita del plan:

```text
§3c líneas 217-219:
Test A2: baseline ... ráfaga de 10 queries seguidas ...
PASS si load_rate >= 0.8*base_rate

§3b línea 200:
/poll ... devuelve y vacía la cola (at-most-once)

§3a líneas 142-147:
r.tick_dispatch = m_Tick ... r.tick_post = m_Tick ... POST(...)
```

Problema: con el diseño actual, una ráfaga de 10 queries puede entrar completa en la cola antes del siguiente poll de 5 Hz. Entonces `/poll` devuelve y vacía las 10 en un único batch, `Dispatch()` las procesa en el mismo callback, y `tick_dispatch`/`tick_post` quedan iguales o casi iguales para todas. Eso hace que `load_rate` sea 0 o no representativo aunque REST sea perfectamente async. Además, `tick_post` se asigna antes de lanzar el `POST` async, no cuando el POST termina; por tanto no mide progreso del tick durante la request de resultado.

Referencia: el DPF A2 exige que el tick-counter avance durante el round-trip (`product-spec.md:42`). El source confirma que `RestContext.GET/POST` async existen (`scripts/3_game/http/restapi.c:103` y `:123`) y que los bloqueantes son `GET_now/POST_now` (`:108`/`:128`), pero la prueba del plan no aísla esa propiedad.

Resolución sugerida: cambiar A2 a un fixture de no-bloqueo con delay controlado en el servidor. El bridge debe registrar `tick_poll_sent` al emitir `GET`, `tick_poll_callback` al entrar en `OnSuccess`, y el test debe verificar que `tick_poll_callback - tick_poll_sent` avanza durante un `/poll` retrasado. Para carga, medir heartbeat/status o espaciar queries para evitar que todas se despachen en el mismo batch.

#### R22-002 — §8 / §9 Step 0 — WARN

Cita del plan:

```text
§8 líneas 283-290:
Riesgos Step 0: GetRestApi null, server filepatching, GET async, deserialización array<ref>, lifetime RestCallback.

§9 líneas 301-303:
Step 0: mcp_server.py mínimo solo /poll + modded MissionServer hace UN GET /poll y loguea OnSuccess/OnError.
```

Problema: §8 define Step 0 como gate de descubrimiento antes de construir 3a-3d, pero §9 solo valida el primer GET. Quedan fuera del gate dos riesgos explícitos (`array<ref>` por `JsonSerializer` y lifetime de `RestCallback`) y dos dependencias prácticas que §11 deja como `[DESIGN/verify]`: `$profile:` del server diag y el camino `POST /result`.

Resolución sugerida: ampliar Step 0 sin convertirlo en implementación completa: leer `dayz_mcp.json` desde `$profile:`, hacer `/poll` con un batch de 2 comandos y parsearlo, lanzar varias requests async para observar callbacks fiables, y hacer un `POST /result` mínimo. Mantener el gate antes de los pasos 1-4.

#### R22-003 — §3a `TryInit` / §11 RestApi — WARN

Cita del plan:

```text
§3a líneas 114-120:
RestApi api = GetRestApi(); if (!api) return;
...
m_Ctx = api.GetRestContext(m_Url);

§8 línea 285:
GetRestApi() null en MissionServer -> RestApi quizá necesita otra init, o ir client-side
```

Problema: el source vanilla expone `CreateRestApi()` junto a `GetRestApi()` (`scripts/3_game/http/restapi.c:181` y `:183`). Además, hay prior art local que usa patrón get-or-create: `LFPowerGrid/scripts/4_World/LFPG_BTCPriceFetcher.c:134-137` y `LBmaster_Core/scripts/5_Mission/LBmaster_Core/RestAPI/ServerUpdateRequestPacket.c:35-37`. Si `GetRestApi()` es null server-side, el plan actual puede falsear el gate y saltar a client-first sin probar el init correcto.

Resolución sugerida: añadir `CreateRestApi()` a §11 y al Step 0 como opción verificada antes de clasificar server-side como fallido. El fallback client-first debe venir después de probar get-or-create, no antes.

#### R22-004 — §3c Test A1 / §10 — WARN

Cita del plan:

```text
§1 línea 23:
Fuera del POC: spawn, conducir, raycast, cámara, captura, MCP stdio.

§3c línea 216:
Test A1: query_player_state -> compara state.pos con la coord conocida (spawn/teleport), error < 0.5 m.

§3d líneas 231-238:
run-poc.ps1 genera config, lanza server+diag, espera player in-game y corre mcp_client.py.
```

Problema: A1 requiere una coordenada conocida, pero el plan no define quién fija esa coordenada. Como spawn/teleport están fuera del POC y `run-poc.ps1` solo espera a que el player esté in-game, el test puede terminar validando “hay una posición” en vez de “la posición server-authoritative coincide con un fixture”.

Resolución sugerida: declarar el fixture A1. Opciones acotadas: misión/test profile con spawn determinista, setup manual/externo documentado antes del POC, o un paso de test que lea una coordenada de referencia independiente. Si se decide que fase 0 solo prueba presencia/plausibilidad, hay que ajustar A1 en DPF o plan, no mezclarlo con `<0.5 m`.

#### R22-005 — §11 fila Identity — WARN

Cita del plan:

```text
§11 línea 338:
Identity | proto native PlayerIdentity GetIdentity() · GetName() | 3_game/entities/man.c:21
```

Problema: `3_game/entities/man.c:21` verifica `Man.GetIdentity()`, pero no `PlayerIdentity.GetName()`. La firma real de nombre está en `PlayerIdentityBase`: `proto string GetName()` en `scripts/3_game/gameplay.c:362`. No es una API inexistente, pero sí rompe el `path:line` exacto de §11.

Resolución sugerida: dividir la fila o corregirla a dos citas. `Man.GetIdentity()` queda OK en `scripts/3_game/entities/man.c:21`; `PlayerIdentityBase.GetName()` queda OK en `scripts/3_game/gameplay.c:362`.

### Cobertura

- Criterios A1-A5 cubiertos por el plan:
  - A1: cubierto por `query_player_state` y `BuildPlayerState`, pero débil por R22-004.
  - A2: cubierto nominalmente por tick-counter, pero bloqueado por R22-001.
  - A3: cubierto por correlation-id en mensajes/resultados y test de 2 ids.
  - A4: cubierto por bind `127.0.0.1`, key obligatoria, whitelist y no-log de URL.
  - A5: cubierto por single-in-flight, timeouts, backoff y test de server caído/reanudación.

- Criterios A1-A5 NO cubiertos o débiles:
  - A1: WARN R22-004, falta fixture de coordenada conocida.
  - A2: FAIL R22-001, prueba no demuestra no-bloqueo y puede fallar por batching.

- APIs de §11 verificadas OK:
  - `MissionServer.OnUpdate(float timeslice)`: `scripts/5_mission/mission/missionserver.c:102`; llama `TickScheduler(timeslice)` en `:115`.
  - `MissionServer.TickScheduler(float timeslice)`: `scripts/5_mission/mission/missionserver.c:753`; `MissionGameplay.TickScheduler(float timeslice)` existe en `scripts/5_mission/mission/missiongameplay.c:216`.
  - `CGame.GetPlayers(out array<Man>)`: `scripts/3_game/global/game.c:947`; uso server en `scripts/5_mission/mission/missionserver.c:243`.
  - `Object.GetPosition()`: `scripts/3_game/entities/object.c:293`.
  - `GetRestApi()`: `scripts/3_game/http/restapi.c:183`.
  - `RestApi.GetRestContext(string)`: `scripts/3_game/http/restapi.c:155`.
  - `RestContext.GET(RestCallback,string)`: `scripts/3_game/http/restapi.c:103`.
  - `RestContext.POST(RestCallback,string,string)`: `scripts/3_game/http/restapi.c:123`.
  - `GET_now` / `POST_now`: `scripts/3_game/http/restapi.c:108` / `:128`.
  - `RestContext.SetHeader(string)`: `scripts/3_game/http/restapi.c:141`; el comentario lo limita a Content-Type en `:135-140`.
  - `RestApi.SetOption(int,int)` y `ERESTOPTION_*`: `scripts/3_game/http/restapi.c:175` y `:32-34`.
  - `RestCallback : Managed`, `OnError`, `OnTimeout`, `OnSuccess`: `scripts/3_game/http/restapi.c:50`, `:55`, `:64`, `:73`.
  - `JsonSerializer.WriteToString` / `ReadFromString`: `scripts/3_game/gameplay.c:68` / `:100`.
  - `JsonFileLoader<T>.JsonLoadFile(string,out T)`: `scripts/3_game/tools/jsonfileloader.c:105`.
  - `Man.GetIdentity()`: `scripts/3_game/entities/man.c:21`.

- APIs con firma incorrecta/inexistente:
  - Ninguna API inexistente encontrada en §11.
  - Cita incorrecta: `PlayerIdentity.GetName()` no está en `man.c:21`; firma real `proto string GetName()` en `scripts/3_game/gameplay.c:362` (R22-005).
  - API faltante para el init RestApi: `CreateRestApi()` existe en `scripts/3_game/http/restapi.c:181` y debería añadirse al plan (R22-003).

### Próximo paso

- `approve with minor changes` -> Claude aplica R22-001..R22-005 y re-envía el plan.
- No empezar implementación hasta corregir al menos R22-001; si se deja A2 como está, el POC puede fallar o pasar sin probar lo que A2 pretende probar.
