# Plan — POC fase 0: round-trip async no-bloqueante + readiness  ·  v2 (post-R22)

> Proyecto: **DayZ-MCP**. Fase 0 del plan por fases de [`../dayz-mcp-architecture.md`](../dayz-mcp-architecture.md) §8.
> Traza a **product-spec grupo A** (A1-A5). Research/diseño: el architecture doc (~2.5M tokens) +
> spot-check directo de APIs load-bearing (esta sesión, ver §11). Decisiones de arranque (Grill):
> server-side (modded MissionServer) · transporte HTTP crudo · no-bloqueo probado por tick-counter + RTT.
> **Estado: v2 — R22 de Codex aplicada (approve with minor changes). Listo para implementar por Step 0.**
> Cambios v1→v2 en §12 (changelog post-R22). Implementa Codex.

## 1. Objetivo y trazabilidad (gate DPF)

De-riscar lo crítico de todo el end-goal: que un agente pueda **mandar un comando y recibir
datos estructurados de DayZ por un transporte async que NO estanca el sim de 60Hz**, con
readiness (correlation-id) y seguridad fail-closed desde el día 1.

| Criterio product-spec | Qué prueba el POC |
|---|---|
| **A1** | `query_player_state` devuelve la posición autoritativa del player (server-side), == fixture conocido |
| **A2** | El round-trip no bloquea el tick (el tick avanza mientras un GET async está en vuelo) |
| **A3** | Correlation-id casa pedido↔resultado (2 comandos concurrentes no se cruzan) |
| **A4** | Fail-closed: sin key/key mala → 401; comando fuera de whitelist → rechazo; bind 127.0.0.1 o abortar |
| **A5** | Resiliencia: servidor caído → mod no crashea/spamea (backoff); al volver, se reanuda |

Fuera del POC: las **tools** spawn, conducir, raycast, cámara, captura, MCP stdio (fases 1-4).
**Nota A1 (R22-004)**: la "coordenada conocida" la fija el **spawn determinista de la mission de
test** (un único spawn point), NO la tool `world_spawn` (que sigue fuera de scope). La mission
*setea* la coord; el POC solo la *lee* y compara.

## 2. Arquitectura del POC

Dos procesos + un transporte HTTP pull sobre `127.0.0.1`:

```
[mcp_client.py (CLI test)]                 [DayZ_MCP mod = modded MissionServer]
   POST /enqueue {cmd}  ─┐                   OnUpdate→OnTick (cada ~200ms, 5Hz):
   POST /set_poll_delay ─┤                     GET /poll?key=…        (async, pull comandos)
   GET  /await?id=N   ───┤                      → dispatch query_player_state
                         ▼                      POST /result?key=…    (async, push resultado)
              [mcp_server.py  127.0.0.1:PORT]   midiendo tick_poll_sent→tick_poll_callback
              cola + results{id→…} en memoria
              caras: cliente (/enqueue,/await,/set_poll_delay) · mod (/poll,/result)
```

- El **mod es cliente HTTP** (`RestContext.GET/POST` async). El **servidor Python es pasivo**
  (responde a polls). El mod **pull**ea comandos y **push**ea resultados → desacopla el tick del RTT.
- **Modelo de latencia (importante, no es un bug)**: el RTT está dominado por la **cadencia de poll**
  del modelo pull, NO por el RTT de REST. Con mod-poll 5Hz (200ms) + await-poll del cliente ~50ms,
  el RTT esperado es ~100-350ms. Subir `pollHz` baja la latencia; para el POC 5Hz va bien y se
  **reporta la latencia honestamente** (que sea ~poll_interval NO significa que REST sea lento).

## 3. Componentes a implementar

### 3a. Mod Enforce `@DayZ_MCP` (server-side)

Estructura (en `..\..\DayZ_MCP\`, lo crea Codex):
```
DayZ_MCP/
├── $PBOPREFIX$                     → "DayZ_MCP"
├── config.cpp                      → CfgPatches + CfgMods (missionScriptModule)
└── scripts/5_Mission/
    ├── MCPBridge.c                 → la clase puente (config, poll loop, dispatch, backoff, in-flight ticks)
    ├── MCPMessages.c               → clases JSON (MCPConfig/MCPCommand/MCPCommandBatch/MCPResult/MCPPlayerState)
    ├── MCPCallbacks.c              → MCPPollCallback / MCPResultCallback : RestCallback
    └── MissionServer.c             → modded class MissionServer (hook OnUpdate)
```

`config.cpp` **[DESIGN]** (modelado sobre `..\..\MCPTest\config.cpp`, verificado):
```cpp
class CfgPatches { class DayZ_MCP {
    units[]={}; weapons[]={}; requiredVersion=0.1; requiredAddons[]={"DZ_Data"};
}; };
class CfgMods { class DayZ_MCP {
    dir="DayZ_MCP"; name="DayZ_MCP"; type="mod"; hideName=1; hidePicture=1;
    dependencies[]={"Mission"};
    class defs { class missionScriptModule {
        value=""; files[]={"DayZ_MCP/scripts/5_Mission"};
    }; };
}; };
```

`MissionServer.c` **[DESIGN]** (hook [EXACT] de §11):
```c
modded class MissionServer
{
    ref MCPBridge m_MCP;
    override void OnMissionStart() { super.OnMissionStart(); m_MCP = new MCPBridge(); }
    override void OnUpdate(float timeslice)
    {
        super.OnUpdate(timeslice);          // R: nunca saltarse el super
        if (m_MCP) m_MCP.OnTick(timeslice);
    }
}
```

`MCPBridge.c` **[DESIGN]** (lógica del lazo; APIs [EXACT] en §11):
```c
class MCPBridge
{
    protected RestContext m_Ctx;
    protected string m_Url, m_Key;
    protected float m_PollHz = 5.0, m_Accum, m_Backoff;
    protected int m_Tick;                 // contador monótono (base de la prueba de no-bloqueo)
    protected int m_TickPollSent, m_TickPollCallback;   // ventana in-flight del poll actual (A2)
    protected bool m_PollInFlight, m_Configured, m_InitTried;

    void OnTick(float dt)
    {
        m_Tick++;                          // avanza SIEMPRE, aunque haya REST en vuelo
        if (!m_Configured) { TryInit(); return; }
        m_Accum += dt;
        if (m_Accum < (1.0/m_PollHz) + m_Backoff) return;
        if (m_PollInFlight) return;        // single in-flight: no apilar polls
        m_Accum = 0; m_PollInFlight = true;
        m_TickPollSent = m_Tick;           // (A2) tick al emitir el GET
        m_Ctx.GET(new MCPPollCallback(this), "poll?key=" + m_Key);   // async, NUNCA GET_now
    }

    protected void TryInit()               // R22-003: get-or-create
    {
        RestApi api = GetRestApi();
        if (!api) api = CreateRestApi();   // si el engine no lo creó aún, crearlo (prior-art LFPG_BTCPriceFetcher.c:132-138, #ifdef SERVER)
        if (!api) { m_InitTried = true; return; }   // solo entonces se considera no disponible
        // En diag server `$profile:` NO resuelve para leer (FileExist=0, confirmado Step 0) → usar `$mission:`.
        // El mod prueba ambos por robustez pero carga de `$mission:dayz_mcp.json`.
        MCPConfig cfg; JsonFileLoader<MCPConfig>.JsonLoadFile("$mission:dayz_mcp.json", cfg);
        if (!cfg || cfg.url == "" || cfg.key == "") { m_InitTried = true; return; }  // sin config: silencio
        m_Url = cfg.url; m_Key = cfg.key; if (cfg.pollHz > 0) m_PollHz = cfg.pollHz;
        // ERESTOPTION_* / SetOption NO son script-accesibles en runtime 1.29 ("Can't find variable"),
        // aunque existan en el proto restapi.c. Timeout default = 10s (ok para localhost). NO usar SetOption.
        m_Ctx = api.GetRestContext(m_Url);
        m_Ctx.SetHeader("application/json");               // SOLO Content-Type (ver §11 hallazgo)
        m_Configured = true; m_Backoff = 0;
    }

    void OnPollSuccess(string data)        // llamado por MCPPollCallback.OnSuccess
    {
        m_TickPollCallback = m_Tick;       // (A2) tick al volver el callback → delta = ticks in-flight
        m_PollInFlight = false; m_Backoff = 0;
        MCPCommandBatch batch; string err; JsonSerializer js = new JsonSerializer();
        if (!js.ReadFromString(batch, data, err)) return;  // log err (sin volcar la key)
        if (!batch || !batch.commands) return;
        foreach (MCPCommand c : batch.commands) Dispatch(c);
    }

    void OnPollFail()                      // OnError u OnTimeout
    {
        m_PollInFlight = false;
        m_Backoff = Math.Clamp(m_Backoff > 0 ? m_Backoff*2 : 1.0, 1.0, 30.0);  // expo, cap 30s
    }

    protected void Dispatch(MCPCommand c)
    {
        MCPResult r = new MCPResult(); r.id = c.id;
        r.tick_poll_sent = m_TickPollSent; r.tick_poll_callback = m_TickPollCallback;  // (A2)
        r.tick_dispatch = m_Tick;
        if (c.cmd == "query_player_state") { r.ok = true; r.state = BuildPlayerState(); }
        else { r.ok = false; r.error = "unknown_command"; }   // whitelist defensiva en el mod
        string body; new JsonSerializer().WriteToString(r, false, body);
        m_Ctx.POST(new MCPResultCallback(), "result?key=" + m_Key, body);   // async
    }

    protected ref MCPPlayerState BuildPlayerState()
    {
        array<Man> players = new array<Man>(); GetGame().GetPlayers(players);   // server-side, autoritativo
        MCPPlayerState s = new MCPPlayerState();
        if (players.Count() > 0) {
            Man p = players[0]; vector pos = p.GetPosition();
            s.pos = {pos[0], pos[1], pos[2]};
            PlayerIdentity id = p.GetIdentity(); if (id) s.name = id.GetName();   // GetName: gameplay.c:362
        }
        return s;
    }
}
```

`MCPCallbacks.c` **[DESIGN]**:
```c
class MCPPollCallback : RestCallback {
    protected ref MCPBridge m_B;
    void MCPPollCallback(MCPBridge b) { m_B = b; }
    override void OnSuccess(string data, int dataSize) { if (m_B) m_B.OnPollSuccess(data); }
    override void OnError(int errorCode) { if (m_B) m_B.OnPollFail(); }
    override void OnTimeout() { if (m_B) m_B.OnPollFail(); }
}
class MCPResultCallback : RestCallback {
    override void OnError(int errorCode) {}   // POST de resultado: best-effort en el POC
    override void OnTimeout() {}
}
```
> ⚠️ **Lifetime de RestCallback** (Step 0 lo valida): `RestCallback : Managed`. Si el engine no
> retiene el callback durante la request, hay que guardar una `ref` viva (p.ej. en una lista del
> bridge) hasta que dispare. Asumir que puede ser GC-eado y protegerlo. **[DESIGN/verify]**

`MCPMessages.c` **[DESIGN]** (deserialización de array de ref a validar en Step 0):
```c
class MCPConfig { string url; string key; float pollHz; }
class MCPCommand { int id; string cmd; }
class MCPCommandBatch { ref array<ref MCPCommand> commands; }
class MCPPlayerState { string name; ref array<float> pos; }   // pos = [x,y,z]
class MCPResult { int id; bool ok; string error; ref MCPPlayerState state;
                  int tick_poll_sent; int tick_poll_callback; int tick_dispatch; }
```

### 3b. Servidor Python `tools/mcp_server.py` **[DESIGN]**

`http.server.ThreadingHTTPServer`, sin dependencias externas. Estado en memoria (cola FIFO +
`results{id}` + `poll_delay_ms` one-shot), `threading.Lock`. Endpoints:

| Cara | Método+Ruta | Cuerpo / query | Acción |
|---|---|---|---|
| cliente | `POST /enqueue` | `{cmd, args?}` | valida whitelist; asigna `id`; encola; guarda `t_enqueue`; → `{id}` |
| cliente | `GET /await?id=N` | — | → `{status:"done",result}` si está, si no `{status:"pending"}` |
| cliente | `POST /set_poll_delay` | `{ms}` | (A2) fija un delay one-shot que se aplica al **siguiente /poll no-vacío** → `{ok:true}` |
| mod | `GET /poll` | — | saca y **vacía** la cola (at-most-once); si hay comandos Y hay delay pendiente: `sleep(delay)` y lo limpia, luego responde → `{commands:[…]}` |
| mod | `POST /result` | `{id,ok,state,tick_poll_sent,tick_poll_callback,tick_dispatch}` | guarda `results[id]` + `t_result` → `{ok:true}` |

`/poll` (lógica del delay, para que la ventana in-flight caiga sobre el poll que trae el comando):
```python
with lock:
    cmds = queue[:]; queue.clear()
    d = 0
    if cmds and poll_delay_ms: d = poll_delay_ms; poll_delay_ms = 0   # one-shot, solo si no-vacío
if d: time.sleep(d/1000.0)      # fuera del lock: bloquea SOLO este hilo de request (ThreadingHTTPServer)
return {"commands": cmds}
```

Seguridad (R6, fail-closed):
- **Bind solo `127.0.0.1`** (nunca `""`/`0.0.0.0`). Tras bind, **assert** `server_address[0]=="127.0.0.1"`
  o `sys.exit(1)` (no arrancar inseguro).
- **API-key** obligatoria en **query string** (`?key=…`) en TODAS las rutas → si falta o no casa: `401`.
- **Whitelist** `{"query_player_state"}` en `/enqueue`. Comando fuera → `400 not_whitelisted`.
- `log_message` **override a no-op** (o log a archivo SIN la URL) → la key del query string **no se
  vuelca** a stdout/logs. (Mitiga el caveat de key-en-query-string.)
- Key NO hardcodeada: se lee de `--keyfile` (la genera el orquestador con `secrets.token_urlsafe`).

### 3c. Cliente de test `tools/mcp_client.py` **[DESIGN]**

Habla HTTP al servidor (mismo `127.0.0.1:PORT` + key). Funciones:
- `enqueue(cmd) -> id`; `await_result(id, timeout) -> (result, rtt)` (poll a `/await` cada ~50ms).
- **Test A1** (R22-004): `query_player_state` → compara `state.pos` con la **coord del spawn
  determinista de la mission de test** (constante conocida, ver §3d), error < 0.5 m.
- **Test A2 (no-bloqueo, rediseñado R22-001)** — mide que el tick avanza con un GET async en vuelo:
  1. `POST /set_poll_delay {ms: 600}` (one-shot; 600 ms < 10 s = read timeout default del mod).
  2. `enqueue("query_player_state")` → id.
  3. El siguiente `/poll` que trae el comando duerme ~600ms en el server → ventana in-flight.
  4. Del resultado, leer `ticks_in_flight = tick_poll_callback − tick_poll_sent`.
  5. **PASS** si `ticks_in_flight >= 5` (umbral conservador: a 600ms, incluso a 8 fps son ~5 ticks; si
     bloqueara con `*_now` sería ~0). Reportar `ticks_in_flight` y el fps implícito = `ticks_in_flight/0.6`.
  6. Sanity: baseline sin delay (2 queries espaciadas 1.0s → `(tick_dispatch2−tick_dispatch1)/Δwall`)
     y confirmar que el fps implícito de (5) está en el mismo orden (no cae). Reportar ambos + RTT min/med/max.
- **Test A3**: encolar 2 queries casi a la vez; ambas resuelven con su propio `id` y resultado coherente.
- **Test A4**: requests sin key / key mala → 401; `POST /enqueue {cmd:"evil"}` → 400.
- **Test A5**: matar el server mid-test → el mod no crashea (RPT limpio); relanzar server → el round-trip vuelve.
- Salida: JSON de verdict + PASS/FAIL por criterio + números (a `tools/poc-verdict.json`).

### 3d. Orquestador `tools/run-poc.ps1` **[DESIGN]**

> **Host-path test (workflow)**: ninguna ruta de sandbox; descubrir/parametrizar rutas reales del host.
> Reutiliza la infra del skill `dayz-test-ingame` (DAYZ_INFRA.md): AddonBuilder/filepatching, junction
> `P:\Mods`, `serverDZ.cfg allowFilePatching=1`, flags de diag, server+client en una caja.

1. Generar key (`token_urlsafe`) → escribir keyfile (para Python) + `dayz_mcp.json` en el **dir de la
   mission** (`-mission=`; ahí resuelve `$mission:` del mod). **Step 0 confirmó: en el server diag
   `$profile:` NO resuelve para leer (FileExist=0); `$mission:` SÍ.** (El mod prueba ambos, carga de `$mission:`.)
2. **Mission de test con spawn determinista (R22-004)**: usar/configurar una mission cuyo único spawn
   point sea una coord fija conocida `POC_SPAWN`; pasar esa constante al `mcp_client.py` para A1.
3. Arrancar `mcp_server.py` (background) con `--port` `--keyfile`.
4. **Build + deploy del PBO (OBLIGATORIO — verificado Step 0, 2026-06-06)**: empaquetar con AddonBuilder
   (`AddonBuilder.exe P:\DayZ_MCP P:\Mods\@DayZ_MCP\Addons -prefix=DayZ_MCP -temp=P:\temp\DayZ_MCP -clear`,
   DAYZ_INFRA.md:68) y lanzar **diag server** con `-mod=P:\Mods\@DayZ_MCP` + **diag client** (`-connect`,
   ~50s a in-game). `-filePatching` (+ `allowFilePatching=1`) solo itera `.c` raw SOBRE el PBO ya
   desplegado; el PBO debe existir primero (registra el addon y el `missionScriptModule`). **Carpeta
   loose con `-mod=` SIN PBO NO registra los scripts server-side** (config carga = "define", pero corre
   el `MissionServer` vanilla — root cause del FAIL del 2026-06-06).
5. Esperar al player in-game (sondear A1 hasta que devuelva pos válida, con timeout).
6. Correr `mcp_client.py` (A1-A5); recoger verdict.
7. Imprimir PASS/FAIL + números; teardown (parar diag + server).

## 4. Esquemas de mensaje (JSON)

```
config (mod lee):     { "url":"http://127.0.0.1:8765/", "key":"<rnd>", "pollHz":5 }
set_poll_delay (cli): { "ms":600 }
command (server→mod): { "commands":[ {"id":1,"cmd":"query_player_state"} ] }
result  (mod→server): { "id":1, "ok":true, "state":{"name":"…","pos":[x,y,z]},
                        "tick_poll_sent":12300, "tick_poll_callback":12318, "tick_dispatch":12318 }
```

## 5. Readiness protocol (resuelve "¿ya está?")

`query_player_state` no es instantáneo (pull + tick). El cliente: `enqueue → loop GET /await hasta
status=="done" o timeout`. "Listo" = `results[id]` existe **AND** `ok==true` **AND** `state.pos`
presente. RTT = `t_result(servidor) − t_enqueue(servidor)` (y/o wall-clock del cliente). Sin esto,
blind-poll/sleep fijo → frágil. (Generaliza a los predicados de readiness de fases 1-3, arch §5.)

## 6. Decisiones de diseño (con razón)

| Decisión | Valor POC | Razón |
|---|---|---|
| Cadencia de poll del mod | 5 Hz (200 ms) | equilibra latencia vs carga; configurable por `pollHz` |
| Concurrencia de poll | single in-flight | evita apilar requests; además garantiza que `m_TickPollSent/Callback` no se solapen |
| Entrega de comandos | at-most-once (`/poll` vacía la cola) | para una query read-only basta; si el mod cae entre poll y result, el cliente hace timeout y re-encola |
| Delay de `/poll` (A2) | one-shot, server-side, sobre poll no-vacío, < read-timeout (3 s) | crea una ventana in-flight observable para medir el no-bloqueo del tick (R22-001) |
| Puerto | fijo configurable (def. 8765) | escrito en el config que lee el mod |
| Timeouts REST | default 10 s (NO `SetOption`) | `ERESTOPTION_*`/`SetOption` no son script-accesibles en runtime 1.29; el default 10s > delay A2 (600 ms) y el backoff cubre un server muerto |
| Backoff ante error | expo 1→30 s | A5: no spamear si el server está caído |
| Init RestApi | get-or-create (`GetRestApi`→`CreateRestApi`) | R22-003: no falsear el gate si el engine aún no creó el singleton (prior-art LFPG) |
| Key transport | query string | el mod no puede setear `Authorization` (§11 hallazgo); mitigado con no-logging + bind local |

## 7. Prueba de no-bloqueo (A2) — mecánica (rediseñada, R22-001)

**Por qué cambió desde v1**: la v1 medía el ritmo de tick sobre una **ráfaga**, pero a 5Hz la ráfaga
entra entera en un `/poll` y se despacha en el mismo callback → `tick` casi igual para todas →
`load_rate≈0` y **false-FAIL**, sin aislar el no-bloqueo. Además `tick_post` se ponía antes de que el
POST async terminara → no medía nada en vuelo.

**v2 — medida directa de la ventana in-flight**: el mod marca `tick_poll_sent` justo antes del `GET`
y `tick_poll_callback` al entrar en `OnSuccess`. Con un **delay controlado** del server sobre ese
`/poll` (600 ms), si el tick NO se bloquea, `tick_poll_callback − tick_poll_sent ≈ 600ms·fps` (decenas
de ticks); si bloqueara (`*_now`), sería ~0. Eso **aísla** la propiedad "el tick corre mientras un GET
async está en vuelo". Sólo se usan callbacks async (nunca `*_now`); RPT sin caída de FPS como sanity
adicional. (No se instrumenta el contra-experimento con `*_now` — basta la evidencia positiva + el
contraste teórico ~0.)

## 8. Riesgos y gate de descubrimiento (R15 / pre-output Pattern 1)

> **Step 0 es un GATE de descubrimiento, no el primer paso de construcción.** El "server-side"
> entero depende de supuestos que el architecture doc §10 marca "a confirmar in-game". Validar
> ANTES de construir 3a-3d completos, o arriesgamos rehacerlo.

| Riesgo | Probar en Step 0 | Si falla |
|---|---|---|
| `GetRestApi()` y `CreateRestApi()` null en `MissionServer` | log de get-or-create != null tras unos ticks | RestApi no disponible server-side → fallback client-side |
| El mod no carga/filepatchea en el **server** diag | Python ve el hit del `/poll` | **empaquetar PBO + deploy a `P:\Mods\@DayZ_MCP\Addons` (loose `-mod=` SIN PBO NO registra el missionScriptModule — FAIL 2026-06-06)**; luego `allowFilePatching` + dependencias |
| `GET` async no alcanza `127.0.0.1` desde el server | `OnSuccess` dispara con 200 | revisar firewall local / formato base URL (slash final) |
| Deserialización `array<ref>` por JsonSerializer | parsear un batch de **2 comandos** | hand-parse mínimo o ajustar las clases |
| Lifetime de `RestCallback` (GC) | lanzar **varias** requests async y ver callbacks fiables | retener `ref` viva hasta el callback |
| ~~`$profile:` del server diag~~ **RESUELTO Step 0** | (n/a) | en el server diag `$profile:` NO resuelve para leer (FileExist=0); usar **`$mission:`** (config en el dir de la mission) |
| `POST /result` server-side | un `POST /result` mínimo que el Python confirme | revisar Content-Type / cuerpo |

**Fallback explícito si Step 0 (server-side) no pasa**: portar el bridge a `modded MissionGameplay`
(client-side, infra @MCPTest ya validada) — es la opción "client-first" que el usuario no eligió, pero
queda como plan B sin rehacer el transporte (mismo MCPBridge, otro hook). La posición del cliente no es
autoritativa, pero de-risca el transporte; luego se vuelve a server-side. **Solo tras agotar get-or-create.**

Otros (heredados del arch §9): BattlEye/filePatching 0x00020005 (cubierto por infra diag);
`SetTimeMultiplier(0)` no aplica al POC (no congelamos); concurrencia 1-dueño (un solo cliente en el POC).

## 9. Pasos de implementación (orden para Codex)

0. **Step 0 — GATE de descubrimiento (R22-002, ampliado)**. Mínimos para validar TODOS los supuestos
   server-side del gate, sin ser impl completa:
   - `mcp_server.py` mínimo: `/poll` (devuelve un batch fijo de **2 comandos**) + `/result` (loguea lo recibido).
   - `modded MissionServer` que, en el primer tick: hace **get-or-create** de RestApi y loguea no-null;
     lee `dayz_mcp.json` desde `$profile:` y loguea los campos; hace un `GET /poll`, **parsea el batch
     de 2** y loguea su contenido; lanza **varias** requests async (observa que todas disparan callback);
     y hace un `POST /result` mínimo que Python confirme.
   **No avanzar a 1-4 hasta que**: Python vea los hits, el mod loguee get-or-create OK, el parse del batch
   de 2 OK, callbacks fiables, y el `/result` llegue. (gate R15) Si falla algo irreparable → fallback §8.
1. `mcp_server.py` completo (5 endpoints + delay one-shot + seguridad: bind-assert, key 401, whitelist, no-log de URL).
2. Mod bridge completo (`MCPBridge`/`MCPMessages`/`MCPCallbacks`/`MissionServer`): config, cadencia,
   dispatch `query_player_state`, result POST con `tick_poll_sent/callback`, backoff, lifetime de callbacks.
3. `mcp_client.py`: enqueue/await + RTT + tests A1-A5 (A2 con `set_poll_delay`) + verdict JSON.
4. `run-poc.ps1`: keygen, config write (host paths), mission con spawn determinista, launch server+diag,
   wait in-game, run client, report, teardown.
5. **Correr y recoger**: ejecutar; verificar A1-A5; reportar pos vs fixture, `ticks_in_flight`, RTT
   (min/med/max), fps baseline vs in-flight, RPT limpio.

## 10. Criterios de aceptación del POC (= product-spec A1-A5)

- **A1** `state.pos` == coord del spawn determinista de la mission (<0.5 m). **A2** `ticks_in_flight ≥ 5`
  con `/poll` retrasado 600 ms (y fps implícito ≈ baseline, no ~0) + RPT sin caída de FPS. **A3** 2 ids
  concurrentes resuelven sin cruce. **A4** 401 sin/mala key, 400 fuera de whitelist, abort si no bind
  127.0.0.1. **A5** server caído → RPT limpio (sin crash/spam) → relanzar → round-trip vuelve. Todos con
  números en `poc-verdict.json` (R22: declarar qué y cómo se verificó).

## 11. Verificación de APIs (R2 — leídas en source esta sesión)

Todas **[EXACT]** salvo nota. Paths relativos a `..\..\scripts\` (raíz vanilla).

| API | Firma | path:line |
|---|---|---|
| Hook tick server | `modded MissionServer` · `override void OnUpdate(float timeslice)` (llama `TickScheduler` :115) | 5_mission/mission/missionserver.c:102 |
| `TickScheduler` | `void TickScheduler(float timeslice)` (existe en MissionServer **y** MissionGameplay :216) | missionserver.c:753 |
| Players (server) | `proto native void GetPlayers(out array<Man> players)` | 3_game/global/game.c:947 (uso: missionserver.c:243) |
| Posición | `proto native vector GetPosition()` | 3_game/entities/object.c:293 |
| RestApi (global) | `proto native RestApi GetRestApi()` | 3_game/http/restapi.c:183 |
| RestApi (crear) | `proto native RestApi CreateRestApi()` — usar como fallback get-or-create (R22-003) | restapi.c:181 |
| Contexto | `proto native RestContext GetRestContext(string serverURL)` | restapi.c:155 |
| GET async | `proto native int GET(RestCallback cb, string request)` | restapi.c:103 |
| POST async | `proto native int POST(RestCallback cb, string request, string data)` | restapi.c:123 |
| Bloqueantes (NO usar) | `GET_now`/`POST_now` | restapi.c:108/128 |
| Header | `proto native void SetHeader(string value)` — **solo Content-Type** | restapi.c:141 |
| Timeouts | ⚠️ `SetOption`/`ERESTOPTION_*` existen en el proto (restapi.c:175/:32-33) pero **NO son script-accesibles en runtime 1.29** ("Can't find variable" in-game; cero uso en vanilla/prior-art). **No usar** → default 10s | runtime-broken |
| Callback | `class RestCallback : Managed` · `OnSuccess(string,int)` `OnError(int)` `OnTimeout()` | restapi.c:50/73/55/64 |
| JSON | `proto bool ReadFromString(void,string,out string)` · `WriteToString(void,bool,out string)` | 3_game/gameplay.c:100/68 |
| Config file | `static void JsonLoadFile(string, out T)` (`JsonFileLoader<T>`) | 3_game/tools/jsonfileloader.c:105 |
| Identity (player) | `proto native PlayerIdentity GetIdentity()` (en `Man`) | 3_game/entities/man.c:21 |
| Nombre (player) | `proto string GetName()` (en `PlayerIdentityBase`; `PlayerIdentity` lo hereda, :388) | 3_game/gameplay.c:362 |

**Hallazgo que corrige `dayz-mcp-architecture.md` §7**: el mod **no puede** poner header `Authorization`
(`SetHeader` es solo Content-Type, restapi.c:135-141) → la API-key va en **query string**. Caveat fase 4:
puede filtrarse a logs HTTP; con bind 127.0.0.1 la exposición es local (mitigado con no-logging de URL).

**Prior-art verificado (R22-003)**: get-or-create de RestApi server-side en
`LFPowerGrid/scripts/4_World/LFPG_BTCPriceFetcher.c:132-138` (`GetRestApi()` → si null `CreateRestApi()`
→ si null FATAL, bajo `#ifdef SERVER`). El bridge puede envolver su init server-side igual.

**[DESIGN/verify] que Step 0 debe smoke-testear**: (a) `JsonSerializer` deserializando `array<ref MCPCommand>`;
(b) lifetime de `RestCallback` bajo varias requests; (c) `$profile:` del mod resuelve al profile dir del
**server** diag; (d) get-or-create de `GetRestApi()` no-null en `MissionServer`.

## 12. Changelog post-R22 (v1 → v2)

Review: [`../reviews/2026-06-06-plan-review-codex.md`](../reviews/2026-06-06-plan-review-codex.md)
(approve with minor changes; 1 FAIL, 4 WARN). Las dos citas de source aportadas por Codex fueron
re-verificadas por Claude contra el vanilla antes de aplicar (gameplay.c:362, LFPG_BTCPriceFetcher.c:132-138).

- **R22-001 (FAIL)** — A2 rediseñado: ventana in-flight (`tick_poll_sent`/`tick_poll_callback`) con
  `/poll` retrasado en el server; se descarta el `load_rate` sobre ráfaga (false-FAIL por batching) y el
  `tick_post` (no medía la request en vuelo). §3a, §3b (`/set_poll_delay`, delay one-shot), §3c, §4, §6, §7, §10.
- **R22-002 (WARN)** — Step 0 ampliado a TODO el gate: config `$profile:`, parse de batch de 2, varias
  callbacks (lifetime), `POST /result` mínimo. §8, §9.
- **R22-003 (WARN)** — get-or-create `GetRestApi()`→`CreateRestApi()` en `TryInit`; `CreateRestApi()`
  añadida a §11; fallback client-first solo tras agotarlo. §3a, §8, §11.
- **R22-004 (WARN)** — A1 fixture = **spawn determinista de la mission de test** (elección del usuario);
  compara <0.5 m contra esa coord; la mission *setea*, no la tool `world_spawn`. §1, §3c, §3d, §10 + product-spec A1.
- **R22-005 (WARN)** — §11 fila Identity partida: `Man.GetIdentity()` man.c:21 · `PlayerIdentityBase.GetName()` gameplay.c:362.
