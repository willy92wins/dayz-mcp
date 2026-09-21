<!-- ARBITRAJE DEL ORQUESTADOR — leer antes que el cuerpo -->

# Arbitraje — spec de fencing por `instance`

**Qué es esto.** El cuerpo lo escribió una lane de Grok en copia aislada (sesión
`01a0153e-f58e-72e3-b18f-d5f9405f5b6b`, 28 turnos, $0,45, 21 ficheros abiertos) sobre el marco de
D-55 y la evidencia del consejo del 18-08. Esta cabecera es del orquestador: dice qué está
verificado contra el árbol real, qué corrige y qué queda por medir. **Donde la cabecera y el cuerpo
discrepen, manda la cabecera.**

## Verificado por el orquestador contra el árbol real (no la copia)

- `EXPECTED_BRIDGE_VERSION = "8"` (`tools\dayz_mcp\core.py:17`), `BLOCKED_VERSION_STATES` (`:20`) y
  `version_state_for` con comparación estricta (`:23`). La opción A de la §6 se sostiene: el
  discriminador de identidad es `inst=`, no `ver=`, y no hace falta pagar el bump global de D-54.
- `_connected_server_pid(sock, *, connections_fn)` existe y hace exactamente lo que la §4.3 quiere
  reutilizar: *"Return the unique owner of this exact established loopback connection"*
  (`tools\dayz_mcp\accredited_daemon_transport.py:78-83`). El patrón inverso ya está en producción.
- `grab_window_to_file(..., client_pid=0, cmdline_match="")` existe con esa firma
  (`tools\mcp_capture.py:366`).
- `_pre_admission_rejection` exige el dict exacto `{"error": ...}` (`tools\dayz_mcp\dayz_test_worker.py:341`)
  y ese módulo viaja **sellado** dentro de `app.pyz`. La §2.3 acierta al declarar el worker
  intocable: cualquier diseño que ensanche el cuerpo de error del daemon rompe el consumidor y
  obliga a reconstruir un bundle firmado.

## P1 — el modo de fallo que puede tumbar la v1 y que el cuerpo no contempla

La §4.3 atribuye el poll comparando el PID dueño del socket con `ProcessRecord.pid`. El propio
proyecto documenta que esa igualdad **no es fiable con DayZDiag**:

- *"Prefer cmdline_match (robust to the launcher-pid != window-pid mismatch); fall back to client_pid"*
  (`tools\mcp_capture.py:330`).
- *"unique profiles path) is preferred and robust to DayZDiag's launcher-pid != window-pid mismatch"*
  (`tools\mcp_client.py:1756`).
- Mismo aviso en `tools\run-fase3.ps1:526`.

Si el proceso que abre la conexión al daemon no es el PID que `start_run` registró, **todos** los
polls caen en `instance_unattributed` y el fencing pasa a fail-closed permanente: cero comandos
entregados, nunca. Es el fallo más caro posible (el sistema deja de funcionar entero) y el cuerpo
solo tiene en NO VERIFICADO el riesgo vecino (reutilización de la conexión TCP).

**Gate resuelto — sonda ejecutada 2026-08-18, la §4.3 queda acreditada.** Run `94cf75b8` (`mode=all`,
proyecto `DayZ_MCP`), 80 muestras de la tabla TCP en 100 s contra el puerto del daemon, comparadas
con el `ProcessRecord` de cada rol:

| Dueño del socket | cmdline | `ProcessRecord` |
|---|---|---|
| PID 53664 `DayZDiag_x64.exe` | `-profiles=P:\DayZ_MCP_dev\_server\profiles` | **casa** con el `server` registrado |
| PID 51288 `DayZDiag_x64.exe` | `-profiles=P:\DayZ_MCP_dev\_client\profiles` | **casa** con el `client` registrado |

No apareció ningún tercer dueño (el PID 0 de las muestras es `System Idle Process`, que es lo que
Windows atribuye a los sockets en `TIME_WAIT`, no un proceso). El mismatch launcher-pid ≠ window-pid
documentado en `mcp_capture.py:330` afecta a **la captura de ventana**, no a quién abre la conexión:
la atribución por PID de la §4.3 vale.

**Dos precisiones de implementación que salen de la misma medida**, y que el cuerpo no recoge:

1. **El puente abre una conexión nueva por request y la cierra.** En 100 s se contaron 4 muestras con
   la conexión `Established` frente a decenas de miles de observaciones en `TIME_WAIT`. La ventana
   para atribuir el socket es la propia request: la resolución tiene que hacerse **dentro del
   handler, con el socket vivo** (que es justo lo que hace `_connected_server_pid` sobre
   `sock.getsockname()`/`getpeername()`), nunca consultando la tabla a posteriori.
2. **No escanear la tabla entera en cada poll.** Con esa rotación, la tabla TCP local acumula cientos
   de entradas en `TIME_WAIT`; hacer un barrido completo dos veces por segundo (5 Hz × 2 peers) es
   coste inútil. La atribución se resuelve una vez por binding y se cachea: el par
   `(instance, PID + creation_time)` se reverifica con baja frecuencia o cuando algo cambia, no en
   cada `/poll`.

Queda como `[DESIGN]` sin acreditar el caso del **canario**: dos procesos con el mismo `instance`
sondeando a la vez. La sonda de hoy midió un run sano, no la colisión.

## P2 — el canario mide con la herramienta frágil

La §8.2 pasa `client_pid=P_bound` a `grab_window_to_file`. Por lo de arriba, el canario debe usar
**`cmdline_match`** con el `-profiles=` de cada cliente: los dos clientes del canario tienen
directorios de perfil distintos por construcción (el extra corre sobre una copia temporal), así que
el discriminador es exacto y no depende del PID. Un canario que falle por captar la ventana
equivocada produciría un `wrong_target_canary_count` falso — en cualquiera de los dos sentidos.

## Corrección menor de cita

`mcp_capture.py` vive en `tools\`, no en `tools\dayz_mcp\`. El cuerpo lo cita sin prefijo.

## Lo que el orquestador adopta sin reservas

- **Opción A de despliegue** (§6.3): no se bumpea `MCP_BRIDGE_VERSION`; el campo se versiona aparte
  y un PBO viejo queda `LEGACY_UNBOUND` en vez de `version_blocked`. Resuelve el conflicto con D-54
  sin esperar a que nadie esté jugando. **Coste que hay que decir en voz alta**: al desplegar el
  daemon nuevo, cualquier sesión con el juego ya arriba deja de poder mutar hasta relanzar. Es
  fail-closed y no corta la partida, pero se nota.
- El recorte de v1 (§9), incluida la exclusión de la cola de caja y de la persistencia de bindings.
- Legacy = lectura desde el día uno, sin excepción de "único run".
- `m_PeerInstance` en vez de `m_Instance` (colisión real con el singleton).
- Que `adopt_run` no restaure mutaciones y que no se reconstruya un binding leyendo el JSON de un
  proceso vivo.

## Lo que sigue sin verificar (además de lo que el cuerpo declara)

- Si `JsonFileLoader<MCPConfig>` tolera un campo de más al leer un JSON nuevo con un PBO viejo. Si
  no lo tolera, un PBO viejo deja de configurarse tras el primer `start_run` nuevo — fail-closed,
  pero con síntoma indistinguible de "el mod no cargó".
- Si el proceso `offline` levanta de verdad ambos puentes contra un solo perfil.

## Sobre la objeción del cuerpo (D-55.8)

Correcta y ya resuelta fuera de la lane: el movimiento 1 (perfil por run + key por proceso) queda
**cancelado** por medición — el worker viaja sellado, el aislamiento de logs ya lo da la rotación
por lanzamiento, y el perfil del servidor es estado compartido de otros mods. D-55.8 se actualiza en
el decision-log con ese motivo; el resto de D-55 sigue vigente.

---

# Spec: fencing por `instance` (D-55, v1)

**Estado**: contrato de implementación. No implementado.
**Marco**: D-55 (no se relitiga). Complementa D-54 (bump de versión global).
**Incidente**: BUG-096 — colas indexadas por rol; un segundo DayZDiag con la misma key recibió y ejecutó `player_teleport` en el mundo ajeno.
**Fuera de esta spec**: cola de caja, movimiento 1 (perfil por run + key por proceso), lock de escritorio, persistencia de bindings.

Las citas `path:line` se reabrieron en el árbol de esta copia. Las líneas que el encargo daba para `loopback.py` (`VALID_PEERS` 72, `record_poll` 936, `COMMAND_TTL_S` 102, `PEER_RECONNECT_GAP_S` 103 y flush 962-966, `hmac.compare_digest` 1509-1511) y para `ReloadKeyAfterFailure` en `MCPBridge.c` (378-405) coinciden con el fichero abierto; no hubo que desplazarlas. El resto de citas se tomó del mismo pase.

Los bloques marcados `[EXACT]` son literales del árbol. Los marcados `[DESIGN]` son contrato de la implementación, no código existente.

---

## 1. Invariante

Un comando mutante solo se entrega al proceso DayZ cuyo `instance` está registrado para ese `run_id`, rol y PID; un peer sin binding, con `instance` vacío o que no casa, no recibe mutaciones.

---

## 2. Contrato del identificador `instance`

### 2.1 Qué es y qué no es

`instance` identifica un proceso. `key` autentica el request. No se reutiliza la API-key como identidad (D-55.2).

Motivo verificado, no estético: el puente recarga la key en caliente y un proceso zombi que reciba 401 releería el perfil y adoptaría la identidad del run nuevo.

```378:405:..\DayZ_MCP\scripts\5_Mission\MCPBridge.c
	protected void ReloadKeyAfterFailure()
	{
		string path = "$profile:dayz_mcp.json";
		if (!FileExist(path))
		{
			path = "$mission:dayz_mcp.json";
		}
		// ...
		if (cfg.key == m_Key)
		{
			return;
		}

		m_Key = cfg.key;
		m_Backoff = 0.0;
		Log("poll key reloaded path=" + path + " keylen=" + m_Key.Length());
	}
```

El cliente tiene la misma función en `MCPClientBridge.c:448-475` (`[EXACT]`, cuerpo simétrico: solo asigna `m_Key`).

La frontera en código: `ReloadKeyAfterFailure` sigue leyendo `cfg.key` y **no lee ni escribe** el miembro de identidad. `TryInit` es el único sitio que copia `cfg.instance` a memoria. Un proceso vivo no puede adoptar el `instance` de un run posterior aunque el JSON del perfil cambie.

### 2.2 Formato

| Pieza | Valor |
|---|---|
| JSON (`MCPConfig.instance`) | UUID4 minúsculo con guiones |
| Query (`inst=`) | el mismo string |
| Validador Python | `_valid_uuid4` en `process_lifecycle.py:35-42` (casefold + `uuid.UUID` versión 4 + `str(parsed) == value`) |
| Regex ya usada por el worker | `dayz_test_worker.py:19-21` (`_UUID4`) |
| Generación | `str(uuid.uuid4())` validado con `_valid_uuid4` antes de persistir |
| Longitud | 36 caracteres |

No usar `uuid.uuid4().hex` (ese es el estilo de `run_id` en `ProcessLifecycle.id_fn`, `process_lifecycle.py:558`). `instance` y `run_id` no comparten formato a propósito: uno es identidad de proceso, el otro de run.

`EncodeQueryValue` (`MCPBridge.c:1903-1933`) deja sin percent-encode ASCII 45 (`-`), 46 (`.`), 95 (`_`) y 126 (`~`). Un UUID4 con guiones viaja literal. Aun así el puente pasa `m_PeerInstance` por `EncodeQueryValue` por simetría con `ver=`.

`instance` **no es secreto**. No se añade a `SECRET_KEYS` (`runtime_state.py:23-25`, hoy `key`, `api_key`, `keyfile`, `lease_token`, `password`, `token`). El daemon no registra la URL completa ni el UUID entero: en `/status` y en el audit, solo un prefijo de 8 hex (los 8 primeros de `uuid.hex`) o un sha256 truncado. Conocer `instance` sin `key` no autentica (`Handler._authorized`, `loopback.py:1509-1513`).

### 2.3 Quién lo genera y quién lo escribe

El daemon, en `ProcessLifecycle.start_run`, **antes** de `self.launcher(...)` (`process_lifecycle.py:1046-1050`).

Hoy `start_run` no toca `dayz_mcp.json`. El JSON lo siembran el instalador y los scripts de gate, sin identidad:

```977:985:tools\install_mcp.py
    config_payload = json.dumps(
        {
            "url": f"http://127.0.0.1:{options.port}/",
            "key": key,
            "pollHz": 5,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
```

`_start_core` ya entrega un directorio de perfil **por proceso** (`dayz_test_worker.py:223-284`): servidor → `{dev_root}\_server\profiles`, cliente y offline → `{dev_root}\_client\profiles`. Ese string llega a `start_run` como `parsed["profiles"]` (`process_lifecycle.py:1002`).

Contrato de escritura `[DESIGN]`:

1. Resolver `config_path = Path(parsed["profiles"]) / "dayz_mcp.json"`. Si `profiles` está vacío o no es ruta absoluta → rechazar el start con `instance_config_missing` (fail-closed; no inventar URL ni caer a `$mission:`).
2. Si el fichero existe: leer JSON, conservar `url`, `key`, `pollHz`; sustituir o insertar `instance`.
3. Si no existe: no crear uno desde cero (el puente exige url+key loopback, `MCPBridge.c:160-176`). Rechazar `instance_config_missing`.
4. Persistencia con `atomic_write_json` (`runtime_state.py:1947-1950`), no con `Path.write_text` del instalador ni con `ConvertTo-Json`.
5. Releer y exigir `payload["instance"] == minted`. Si no casa → abortar el launch (`instance_config_mismatch`), no llamar a `launcher`.
6. **No** escribir `$mission:dayz_mcp.json`. Un solo fichero de misión no puede llevar tokens distintos para servidor y cliente (el fallback `MCPBridge.c:147-149` / `MCPClientBridge.c:264-266` queda como camino legacy).

El worker sellado (`dayz_test_worker.py` / bundle nativo) no se modifica: no minta, no escribe, no rota la key.

### 2.4 Quién lo lee (puente)

Ambos puentes, una sola vez, en `TryInit`:

- Servidor: `MCPBridge.c:144-197` (hoy copia `url`, `key`, `pollHz`).
- Cliente: `MCPClientBridge.c:262-313` (igual).

Campo Enforce: `MCPConfig.instance` (`MCPMessages.c:5-10`). Miembro en memoria: **`m_PeerInstance`**, no `m_Instance`. `m_Instance` ya es el singleton (`MCPBridge.c:27,84-91` y `MCPClientBridge.c:125,187-192`). Colisión de nombre = defecto de implementación.

Si `cfg.instance` falta o es `""`, `m_PeerInstance` queda `""` y el puente sigue configurado. Ese proceso es legacy: sondea, no se le entregan mutaciones.

### 2.5 Cómo viaja

Hoy el servidor no envía `peer=` (el handler asume `"server"`):

```211:221:..\DayZ_MCP\scripts\5_Mission\MCPBridge.c
	protected void StartPoll()
	{
		// ...
		string request = "poll?key=" + m_Key;
		request = request + "&ver=" + GetPollVersion();
		m_Ctx.GET(cb, request);
	}
```

```1855:1858:tools\dayz_mcp\loopback.py
    def _handle_poll(self, qs: dict[str, list[str]]) -> None:
        peer = qs.get("peer", ["server"])[0] or "server"
        version = qs["ver"][0] if "ver" in qs else None
        status, payload = self.state.record_poll(peer, version)
```

El cliente sí envía `peer=client` (`MCPClientBridge.c:340-342`). `/result` solo lleva `key` (`MCPBridge.c:3283`, `MCPClientBridge.c:3105`). `store_result` no mira query ni identidad (`loopback.py:1171-1233`).

Contrato de cable `[DESIGN]`:

```
GET  /poll?key=…&ver=…&peer=…&inst=<uuid>
POST /result?key=…&inst=<uuid>
```

- Si `m_PeerInstance == ""`, el puente **omite** `inst=` (no envía `inst=` vacío).
- `Handler.do_GET` / `do_POST` ya hacen `parse_qs(..., keep_blank_values=True)` (`loopback.py:1449,1471`). `inst` ausente o `inst=` vacío se tratan igual: sin identidad.
- `record_poll` gana un argumento `instance: str | None` y, en el camino HTTP, un `source_pid` atribuido (sección 4).
- `_handle_result` lee `inst` de la query y lo pasa a `store_result`.

`SetHeader` sigue siendo solo Content-Type (gotcha del proyecto). `inst` no puede ir en header.

### 2.6 Ciclo de vida

| Evento | `instance` |
|---|---|
| `start_run` de un rol | se minta uno nuevo para ese proceso; se escribe el perfil de **ese** rol; se registra el binding en memoria |
| Microcorte / hueco de poll del mismo proceso | no cambia; el valor vive en `m_PeerInstance` |
| Relanzamiento del cliente, servidor vivo | se retira el binding del cliente anterior; se minta otro; el servidor conserva el suyo. El "mismo mundo" lo da `run_id` |
| `stop_run` / `reap_dead_runs` | se retiran los bindings del run |
| Reinicio del daemon | el registro en memoria desaparece; el juego sigue enviando el UUID viejo → `instance_unknown` / `unbound_after_restart`. No se reminta el JSON de un proceso ya vivo (no lo recargaría) |
| `adopt_run` | no toca `instance`; transfiere dueño del `RunRecord` (`process_lifecycle.py:1568-1577`) |
| `ReloadKeyAfterFailure` | no toca `m_PeerInstance` |

### 2.7 Si falta, está vacío o no casa

Regla única en `/poll`: HTTP 200 y `commands: []` para todo lo que no sea entrega acreditada. Un 4xx aquí dispara el backoff del puente (`OnPollFail` → `ReloadKeyAfterFailure` a los 4 s, `MCPBridge.c:361-364`). El rechazo accionable vive en `/enqueue` (409) y en `/status`.

| `inst=` | Clasificación | Mutaciones | Lecturas (`READ_ONLY_COMMANDS`, `session_coordination.py:18-38`) |
|---|---|---|---|
| ausente o `""` | `LEGACY_UNBOUND` | no | sí, cola legacy por rol |
| no pasa `_valid_uuid4` | `instance_malformed` (ocupación; no binding) | no | no |
| UUID desconocido | `instance_unknown` | no | no |
| UUID de otro rol, y el `ProcessRecord.role` no es `offline` | `instance_role_mismatch` | no | no |
| UUID retirado | `binding_retired` | no | no |
| UUID vivo, PID atribuido ≠ PID registrado, o dos PID distintos lo presentan | `instance_ambiguous` | no | no |
| UUID vivo, atribución TCP no unívoca | `instance_unattributed` | no | no |
| UUID vivo, PID casa, rol casa, estado `BOUND` | acreditado | sí, si el lease lo permite | sí |

`/result` con `inst` que no casa con el `target_instance` del `command_id`: HTTP 200, `discarded: true`, evento de audit `late_result_fenced`. El puente no entra en backoff (`MCPBridge.c:3289-3291` solo loguea el ack).

---

## 3. Cambios en Enforce

El PBO es uno y lo cargan todas las sesiones. Esta spec **no** sube `MCP_BRIDGE_VERSION` (sección 6). Los cambios son aditivos en el DTO y en las URLs.

### 3.1 `..\DayZ_MCP\scripts\5_Mission\MCPMessages.c`

Hoy `[EXACT]`:

```1:10:..\DayZ_MCP\scripts\5_Mission\MCPMessages.c
const string MCP_BRIDGE_VERSION = "8";
const float MCP_ARG_FLOAT_UNSET = float.MAX;
const int MCP_FIXTURE_SEQ_UNSET = -2147483647;

class MCPConfig
{
	string url;
	string key;
	float pollHz;
};
```

Añadir `string instance;` a `MCPConfig`. **No** tocar `MCP_BRIDGE_VERSION`. `test_batch6.py:32-40` y `test_vehicle_trace_contract.py` fijan el `"8"`; se quedan verdes.

Este fichero no está congelado por SHA.

### 3.2 `..\DayZ_MCP\scripts\5_Mission\MCPBridge.c`

| Sitio | Qué se añade | Qué no se toca |
|---|---|---|
| miembros, junto a `m_Key` (`:31`) | `protected string m_PeerInstance;` inicializado a `""` en el ctor (`:65`) | el singleton `m_Instance` (`:27`) |
| `TryInit` (`:180-184`) | copiar `cfg.instance` a `m_PeerInstance` si no es vacío; si falta, dejar `""` | la validación de url/key/loopback; no exigir `instance` para configurar |
| `StartPoll` (`:219-221`) | si `m_PeerInstance != ""`, anexar `"&inst=" + EncodeQueryValue(m_PeerInstance)` | `key`, `ver`, ausencia de `peer=` (el default `server` se queda) |
| `PostResult` (`:3283`) | misma cláusula `inst=` en `"result?key=" + m_Key` | el body JSON de `MCPResult` |
| `ReloadKeyAfterFailure` (`:378-405`) | **nada**. Sigue asignando solo `m_Key` | la salida temprana `cfg.key == m_Key`, el reset de backoff, las rutas `$profile:` / `$mission:` |
| log de `TryInit` (`:197`) | `instlen=` (longitud), nunca el valor | `keylen` |

No se valida el formato UUID en Enforce. El daemon valida. El puente declara.

### 3.3 `..\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c`

Espejo del servidor:

| Sitio | Cambio |
|---|---|
| `m_Instance` singleton (`:125`) | no tocar |
| nuevo `m_PeerInstance` | igual que el servidor |
| `TryInit` (`:295-296`) | copiar `cfg.instance` una vez |
| `StartPoll` (`:340-342`) | anexar `inst=` al `poll?peer=client&key=` |
| `PostResult` (`:3105`) | anexar `inst=` |
| `ReloadKeyAfterFailure` (`:448-475`) | no tocar `m_PeerInstance` |

`InvokeUiClick` (`:1747-1783`) no se modifica. `ui_click` no es input físico: llama `ScriptedWidgetEventHandler.OnClick` / `GetUserData` dentro del cliente que recibió el comando. El fencing por `instance` ya elige el cliente. No se añade `GetForegroundWindow`.

`DispatchCameraSet` (`:626-656`) no se modifica. El canario usa el verbo tal cual.

### 3.4 Qué no se toca en Enforce

`MCPCallbacks.c`, `MCPJobRunner.c`, `MissionServer.c`, `MissionGameplay.c`, `config.cpp`, handlers de verbos, `GetPollVersion` / caché de `ver=` (`MCPBridge.c:1875-1900`). El version gate del cable sigue anunciando `"8"`.

### 3.5 Centinela SHA

`tools\tests\test_task9_spawn_phase_markers.py` congela `MCPBridge.c` entero.

```95:96:tools\tests\test_task9_spawn_phase_markers.py
BRIDGE_SHA256 = "F1B49714E2CC9660362BF5ACD57844BAD16F284BC5F5D523908F44E56A38DFE3"
BASE_BRIDGE_SHA256 = "136A6056C6439B2D41A24FCE390567421489134ABFBD1DED9891C1264B50F158"
```

SHA-256 medido del fichero en esta copia: `F1B49714E2CC9660362BF5ACD57844BAD16F284BC5F5D523908F44E56A38DFE3` (75346 bytes). El centinela está vivo. Cualquier edición de `MCPBridge.c` lo pone en rojo.

Contrato: re-congelar **las dos** mitades (`BRIDGE_SHA256` y `BASE_BRIDGE_SHA256`) **después** del canario in-game de la sección 8, no sobre un edit source-only. El comentario del propio test (`:19-21`) lo exige. Hasta el gate, el centinela rojo es la señal de que el PBO aún no está aceptado. `MCPClientBridge.c` no tiene SHA freeze; el contrato de recarga de key (`test_poll_key_reload_contract.py`) cubre ambos puentes y se extiende (sección 8).

---

## 4. Cambios en Python

### 4.1 Dónde vive el registro de bindings, y por qué

En `ServerState` (`loopback.py:432-478`), bajo el mismo `self._lock` (`:456`) que `_queues`, `_last_poll_at` y `_command_owner`.

No en `RunRecord` / `runs.json`. El agujero está en el despacho (`record_poll` entrega `_queues[peer]`, `:960`), no en el manifiesto. `_foreign_diag_reason` solo corre en `start_run` (`process_lifecycle.py:960-967`); un DayZDiag que aparece después no se consulta nunca en el poll. El registro tiene que decidir **en cada** `/poll` y **en cada** `/enqueue`.

No se persiste. Tras un reinicio del daemon el mapa está vacío (sección 7). Añadir campos a `RunRecord` sería un cambio de formato persistente; v1 no lo hace.

`ProcessLifecycle` no conoce `ServerState` hoy. Se le inyecta un puerto estrecho `[DESIGN]`:

```
# [DESIGN]
class BindingRegistry(Protocol):
    def prepare(self, run_id: str, role: str, profiles_dir: str) -> str: ...
    def confirm(self, instance: str, record: ProcessRecord) -> None: ...
    def retire_role(self, run_id: str, role: str, reason: str) -> None: ...
    def retire_run(self, run_id: str, reason: str) -> None: ...
```

`prepare` minta, escribe el JSON, deja el binding en `STARTING` y devuelve el UUID. `confirm` se llama tras `guard.snapshot` + `_record_from_snapshot` (`process_lifecycle.py:1064-1080`). Si `confirm` no llega, el binding caduca a `STARTING` y no recibe mutaciones.

### 4.2 `tools\dayz_mcp\loopback.py`

**Colas.** Hoy:

```72:72:tools\dayz_mcp\loopback.py
VALID_PEERS = {"server", "client"}
```

```458:470:tools\dayz_mcp\loopback.py
        self._queues: dict[str, list[dict]] = {"server": [], "client": []}
        # ...
        self._last_poll_at: dict[str, float | None] = {"server": None, "client": None}
        self._poll_versions: dict[str, str | None] = {"server": None, "client": None}
```

`[DESIGN]` se parte en dos mapas:

- `_legacy_queues: {"server": [], "client": []}` — solo lecturas, peers sin `inst=`.
- `_bound_queues: {instance: []}` — lecturas y mutaciones del proceso acreditado.

`_queues` como clave por rol **deja de recibir mutaciones**. Los tests que hoy hacen `state._queues["client"]` (p. ej. `test_loopback.py:865`) se actualizan.

**`record_poll` (`:936`).** Firma actual: `(peer, version=None)`. Se amplía a `(peer, version=None, instance=None, source_pid=None)`. Sin `instance` válido → camino legacy: no flush de colas bound; `PEER_RECONNECT_GAP_S` **sí** aplica a `_legacy_queues[peer]` (`:962-966`); `COMMAND_TTL_S` aplica en ambos caminos (`:974-978`, `:1095-1109`).

Con `instance` bound:

- no se usa `PEER_RECONNECT_GAP_S` como inferencia de identidad (D-55.5). Un hueco durante carga/filepatching no vacía la cola del mismo proceso.
- `COMMAND_TTL_S` sigue caducando comandos no recogidos.
- solo se entregan comandos de `_bound_queues[instance]` cuyo `target_epoch` sigue siendo el epoch actual.
- `_last_poll_at[peer]` (ocupación) se actualiza siempre que el poll autentique; un campo nuevo `_bound_last_poll_at[peer]` solo se actualiza si el poll está acreditado. `compute_bridge_ready` (`server.py:187-218`) usa el bound cuando existe un binding para ese rol. Si no, un peer ajeno que sondea el mismo rol seguiría manteniendo `ready=true` — es el mismo defecto de identidad, aplicado a la readiness.

**`_handle_poll` (`:1855-1869`).** Extrae `inst`. Atribuye PID (4.3). No loguea el UUID; el log actual (`HIT GET /poll peer=…`) gana `inst8=` (prefijo) y `bind=`.

**`_enqueue_command` (`:690-762`).** Tras el version gate (`:720-728`) y antes de `queue.append`:

1. Resolver el peer del comando (`peer_for_command`, `:115-118`).
2. Si `command_requires_lease(cmd)` (`session_coordination.py:47-48`): exigir exactamente un binding `BOUND` para ese peer (o, si el proceso es `offline`, el binding offline que cubre ambos roles). Si no → 409 con el código de la tabla 5.2. **No encolar.**
3. Si es lectura: destino = binding `BOUND` de ese rol si existe; si no, `_legacy_queues[peer]`.
4. Sellar el comando en un sidecar `_command_fence[id] = (instance, epoch, pid)` — no meter esos campos en el dict que viaja al juego. El strip actual (`:1050-1052`) solo quita `owner_session_id` / `owner_lease_id`.

**`store_result` (`:1171`).** Recibe `instance`. Si el `command_id` tiene fence y no casa → 200 + `discarded` + audit `late_result_fenced` (mismos campos que pide el consejo: `command_id`, epochs, prefijos de instancia esperada/origen, lease, `lateness_s`). Si casa y el epoch es viejo con el mismo instance → finalización tardía aceptada para `/await`, audit `late_result_same_instance`.

**`status_snapshot` (`:1382-1408`).** Campos aditivos por peer: `binding_state`, `instance_prefix` (o `null`), `bound_last_poll_age_s`. `queue_depth` pasa a ser la cola que ese rol puede vaciar (bound si hay binding, legacy si no). No se serializa el UUID entero.

**`cleanup_owner` / `vehicle_release` interno (`:1349-1351`).** El `enqueue_command(..., peer="client", internal=True)` debe apuntar al `instance` bound del cliente, no a la cola por rol.

**`VALID_PEERS`, `COMMAND_TTL_S`, whitelist, lease `claim_dispatch` (`:1035-1041`)** se quedan.

### 4.3 Atribución del PID en el poll

`instance` es una declaración. Dos procesos pueden leer el mismo JSON (caso 7.5 y canario). El daemon atribuye el socket entrante a un PID.

Ya existe el patrón inverso (el cliente MCP acredita al daemon) en `accredited_daemon_transport.py:78-113` (`_connected_server_pid`: tabla TCP, `CONN_ESTABLISHED`, un único owner).

`[DESIGN]` en el handler: `self.connection` + `self.client_address` → buscar en `psutil.net_connections` la conexión establecida `127.0.0.1:<ephemeral> → 127.0.0.1:<daemon_port>` y exigir **exactamente un** PID. Ese PID se compara con `ProcessRecord.pid` + `creation_time_utc` vía `native_process_guard.snapshot` (`native_process_guard.py:150-159`, campos `:140-148`).

- 0 u >1 owners → `instance_unattributed`, ocupación, cero comandos.
- PID distinto del registrado, o `creation_time_utc` distinta → si es la primera vez que otro PID presenta este `instance`, el binding pasa a `AMBIGUOUS`.
- DayZDiag podría reutilizar la conexión HTTP más rápido de lo que la tabla TCP distingue. Eso está en NO VERIFICADO. El contrato si la atribución no es unívoca es fail-closed (cero mutaciones), no “entregar igual”.

Los unitarios inyectan `source_pid=` y no tocan la tabla TCP.

No se usa “si hay cualquier DayZDiag ajeno, no despacho”. D-55 lo descartó: convierte el bug en bloqueo permanente cuando otra sesión tiene su juego. El censo de `_foreign_diag_reason` (`process_lifecycle.py:2104-2127`) **no** se llama desde `record_poll`. El discriminador es `(instance, PID)`, no “hay otro proceso en la máquina”.

### 4.4 `tools\dayz_mcp\process_lifecycle.py`

| Función | Cambio |
|---|---|
| `start_run` (`:862`) | Antes de `launcher` (`:1046`): `retire_role` del rol que se lanza (cerca el proceso anterior del mismo rol), `prepare` (mint + write), luego Popen, `confirm`. Si `prepare` falla, no se lanza. Si Popen/`snapshot` falla, `retire` del UUID mintado |
| `stop_run` (`:1272`) | Tras éxito, `retire_run` |
| `reap_dead_runs` (`:1961`) / `reap_dead_run` (`:1973`) | Tras retirar el `RunRecord`, `retire_run` |
| `adopt_run` (`:1519`) | **No** registra binding. Sigue siendo transferencia de `owner_session_id` / `state` (`:1568-1577`) |
| `recover_after_restart` del manifiesto (`:494-526`) | Sin cambio de formato. Los runs pasan a `RUNNING_IDLE`; el mapa de bindings nace vacío |
| `RunRecord` / `ProcessRecord` | Sin campos nuevos |

Mapeo de rol del launch al peer del puente: `server` → `server`; `client` → `client`; `offline` → un solo `instance` que puede sondear **ambos** `peer=` (un proceso, un perfil, `dayz_test_worker.py:262-274`). Eso no relitiga D-55.3: la granularidad sigue siendo el proceso. Un proceso `server` que envíe `peer=client` con el `instance` del servidor es `instance_role_mismatch`.

### 4.5 `tools\dayz_mcp\core.py` y `server.py`

`EXPECTED_BRIDGE_VERSION = "8"` (`core.py:17`) y `version_state_for` (`:23-47`) **no se tocan**. Un `ver=8~…` sin `inst=` no es `version_mismatch`; es legacy de identidad.

`BLOCKED_VERSION_STATES` (`core.py:20`) se queda en `legacy_blocked` / `version_mismatch`. El fencing no se disfraza de version gate.

`compute_bridge_ready` (`server.py:187-218`): si el peer tiene binding, la liveness sale de `bound_last_poll_age_s`. Razones nuevas, cerradas, que hay que añadir a `READY_REASONS` (`server.py:78-84`): `binding_ambiguous`, `unbound_after_restart`. Un agente débil tiene que ver *qué hacer*, no solo `ready:false`.

`_REMOTE_ERROR_CODES` (`server.py:87-119`) incorpora los códigos de la tabla 5.2 para que no colapsen a `remote_error`.

`capture_screenshot` (`server.py:2465-2510`) y `mcp_capture.grab_window_to_file(..., client_pid=)` (`mcp_capture.py:366-371`) no cambian de contrato; el canario los usa con el PID registrado.

`camera_set` (`server.py:2401-2442`) no cambia. Sigue exigiendo lease y yendo al peer `client`.

### 4.6 `tools\dayz_mcp\daemon.py`

`build_server_state` (`:314-347`) y `_activate_server_coordination` (`:350`, asignación `state.lifecycle = lifecycle` en `:577`): cablear el `BindingRegistry` (el propio `state`) en `ProcessLifecycle`. El reaper (`install_run_reaper`, `:763-794`) no se toca más que por el `retire_run` que ya dispara `reap_dead_runs`.

### 4.7 Qué no se toca en Python

`session_coordination.py` (lease, tombstones, FIFO). `install_mcp.py` (los `dayz_mcp.json` de muestra siguen sin `instance` → legacy). `dayz_test_worker.py` (bundle sellado). `runs.json` / `RunRecord`. Herramientas de ratón/teclado sintético: no hay ninguna en `tools\dayz_mcp` (búsqueda de `SendInput` / `mouse_event` / `pyautogui` = 0). No se añaden.

---

## 5. Máquina de estados de un binding

Un binding es la tupla en memoria `(instance, run_id, role, epoch, pid, creation_time_utc)`.

`station_epoch` es un entero en `ServerState`, no persistido. Incrementa al `prepare` de un rol, al `retire` de un rol o run, y al pasar a `AMBIGUOUS`. No incrementa: heartbeat, cambio de lease sobre el mismo run, hueco de poll, foco de ventana, `adopt_run`. El `lease_id` cerca al agente; el epoch cerca al juego.

### 5.1 Estados y transiciones

```
                 prepare()
ABSENT ─────────────────────► STARTING
                                 │
                    confirm()    │ timeout / launch fail / retire
                                 ▼                    │
                               BOUND ◄────────────────┤
                                 │                    │
          segundo PID / mismatch │                    │
                                 ▼                    │
                            AMBIGUOUS                 │
                                 │                    │
              extra PID gone +    │                    │
              un único PID casa   │                    │
                                 ▼                    │
                               BOUND                  │
                                                      │
     stop / reap / replace-role / daemon process end  │
                                 ▼                    ▼
                              RETIRED
```

`UNBOUND_AFTER_RESTART` no es un estado del binding: es la lectura de un `inst=` bien formado contra un registro vacío tras nacer el daemon.

### 5.2 Permisos por estado

| Estado | Lecturas | Mutaciones | Enqueue (mutación) | Poll entrega |
|---|---|---|---|---|
| ABSENT / sin `inst` (`LEGACY_UNBOUND`) | sí (cola legacy) | no | 409 `legacy_unbound` | solo lecturas legacy |
| STARTING | no | no | 409 `binding_not_ready` | vacío |
| BOUND | sí (cola bound) | sí, si lease activo | 200 | lecturas + mutaciones de ese `instance`+epoch |
| AMBIGUOUS | no | no | 409 `instance_ambiguous` | vacío |
| RETIRED | no | no | 409 `binding_retired` | vacío |
| UUID desconocido tras restart | no | no | 409 `unbound_after_restart` | vacío |
| `instance_role_mismatch` | no | no | 409 `instance_role_mismatch` | vacío |
| `instance_malformed` / `instance_unattributed` | no | no | 409 con ese código | vacío |

El lease se revalida igual que hoy (`claim_dispatch`, `loopback.py:1035-1041`). Un binding `BOUND` sin lease sigue sin entregar mutaciones (`lease_inactive`). Las lecturas no exigen lease (`READ_ONLY_COMMANDS`).

### 5.3 Códigos de error (accionables)

Cada 409 lleva `error` + `hint`. El consumidor es a veces un modelo pequeño.

| `error` | HTTP | `hint` (texto fijo) |
|---|---|---|
| `legacy_unbound` | 409 | `Peer poll lacks a valid inst=. Relaunch via dayz_test_run so start_run writes instance into $profile:dayz_mcp.json. Do not mutate a hand-launched game.` |
| `instance_malformed` | 409 | `inst= is not a lowercase UUID4. Check the profile JSON; do not invent the field. Relaunch via dayz_test_run.` |
| `instance_unknown` | 409 | `This inst= is not in the daemon registry. If the daemon restarted, relaunch the run. adopt_run does not restore mutations.` |
| `unbound_after_restart` | 409 | `Daemon came up with a live game and no in-memory bindings. Relaunch the run; do not remint JSON under a live process (the bridge will not reload instance).` |
| `instance_role_mismatch` | 409 | `This instance is registered for a different peer role. Stop that process and relaunch the correct role.` |
| `instance_ambiguous` | 409 | `Two processes presented the same instance. Close the extra DayZDiag that copied this profile. Mutations stay blocked until one PID remains.` |
| `instance_unattributed` | 409 | `TCP table did not map this poll to exactly one PID. Retry; if it persists, relaunch. Commands were not delivered.` |
| `binding_not_ready` | 409 | `Instance is minted but the first accredited poll has not landed. Wait for bridge_status.ready; do not enqueue yet.` |
| `binding_retired` | 409 | `Target instance was replaced (client relaunch or stop). Resend the command; it will target the new instance.` |
| `version_blocked` | 409 | sin cambio (`loopback.py:726`) |

`/poll` no devuelve estos códigos (evita backoff). Aparecen en `/enqueue`, `/status.binding_state` y en `bridge_status.ready.reason` cuando aplica.

---

## 6. Despliegue y compatibilidad

### 6.1 El constraint

```1:1:..\DayZ_MCP\scripts\5_Mission\MCPMessages.c
const string MCP_BRIDGE_VERSION = "8";
```

```17:17:tools\dayz_mcp\core.py
EXPECTED_BRIDGE_VERSION = "8"
```

`version_state_for` compara igualdad estricta (`core.py:43-44`). Si `bridge_version != expected` → `version_mismatch` ∈ `BLOCKED_VERSION_STATES` (`core.py:20`). Eso bloquea enqueue (`loopback.py:720-728`) y vacía el poll (`:957-958`). El PBO es uno por máquina. Subir a 9 con otra sesión en v8 deja esa sesión `version_blocked` sin aviso. D-54: el bump solo con la caja libre y el PBO nuevo desplegable en la misma ventana. Ya hubo que revertir un bump 7→8 en caliente.

`require_version` por defecto es `False` (`server.py:309`, `daemon.py:328`). Eso solo distingue `ver=` ausente (`legacy` vs `legacy_blocked`). No suaviza un desajuste 8 vs 9.

### 6.2 Opciones

| Opción | Qué hace | Coste |
|---|---|---|
| A. Campo aparte, versión intacta (recomendada) | El PBO nuevo sigue anunciando `"8"` y añade `inst=`. El daemon nuevo trata `inst` ausente como `LEGACY_UNBOUND`. `EXPECTED_BRIDGE_VERSION` se queda en `"8"` | Una sesión ajena con PBO viejo sigue sondeando (`version_state=ok`) y **deja de recibir mutaciones**. No se cae la partida. Hay que desplegar un PBO (Enforce tiene que enviar `inst=`), pero no hay que coordinar un bump global |
| B. Dual-accept 8+9 | Subir Enforce a `"9"`, y cambiar `version_state_for` para aceptar `"8"` como legacy de identidad | Toca el version gate (superficie caliente, tests de `test_batch6` / `test_daemon` / `test_mcp_tools`). Sigue haciendo falta un PBO. Mezcla versión de protocolo con presencia de un campo |
| C. Caja libre + bump a 9 | D-54 al pie de la letra: nadie jugando, PBO 9, Python 9, rama legacy con fecha de muerte | El fencing (corrección) espera a que no haya sesión ajena. Esa espera es exactamente lo que impidió el bump anterior. El incidente ya ocurrió |

### 6.3 Recomendación

**A.** El discriminador de identidad es `inst=`, no `ver=`. Meter `instance` dentro de `ver` (descartado por el consejo) o bumpear para señalar un query param opcional paga el coste de D-54 sin ganar aislamiento.

Despliegue:

1. Python primero (daemon nuevo: sin `inst=` → solo lectura). Los juegos v8 ya arriba siguen en `version_state=ok` y pierden mutaciones hasta relanzar con el PBO que envía `inst=`. Eso es fail-closed, no un corte de partida.
2. PBO siguiente **sin** cambiar `MCP_BRIDGE_VERSION`. Los `start_run` posteriores escriben `instance` y los puentes nuevos lo reenvían.
3. Sunset del camino legacy: cuando D-54 lo permita (caja libre), bump 8→9 y entonces `inst=` pasa a ser obligatorio para *cualquier* entrega, lecturas incluidas. Ese bump no es v1.

`install_mcp.py` no minta `instance` en los JSON de muestra. Un juego arrancado a mano con ese perfil es `LEGACY_UNBOUND` desde el día uno. No hay excepción de “único run gestionado”: ese era el estado de BUG-096.

---

## 7. Casos duros

### 7.1 Run adoptado que ya estaba vivo cuando el daemon arrancó

`adopt_run` (`process_lifecycle.py:1519-1577`) exige `RUNNING_IDLE`, sin dueño, identidad de procesos clasificable, y pasa el run a `RUNNING` con el nuevo `owner_session_id`. El manifiesto sobrevive al daemon (`recover_after_restart`, `:494-526`, suelta dueños a `RUNNING_IDLE`). El mapa de bindings **no**.

Respuesta: `adopt_run` restaura el lease/dueño, no las mutaciones. El juego sigue sondeando el UUID que tiene en `m_PeerInstance`. El daemon no lo conoce → `unbound_after_restart`. Remintar el JSON no sirve: el puente no recarga `instance`. La salida es relanzar el run (nuevo proceso, `TryInit` lee el UUID nuevo).

### 7.2 Reinicio del daemon con el juego arriba

Igual que 7.1 más las colas vacías. Los procesos son observables y detenibles por `ProcessRecord` (PID + `creation_time_utc`). Cero comandos hasta un `start_run` nuevo. No se reconstruye el binding leyendo el JSON de un proceso vivo.

### 7.3 Cliente relanzado con el servidor vivo

`start_run` con `run_id` existente (`process_lifecycle.py:897-911`, estado `RUNNING`, mismo dueño) lanza el cliente y hace `processes.append` (`:1080`).

Respuesta:

1. `retire_role(run_id, "client")` — cola del cliente anterior a `binding_retired`, epoch++.
2. Mint C2, escribir solo `{client_profiles}\dayz_mcp.json`.
3. Popen, `confirm` C2. El binding del servidor no se toca.
4. El cliente viejo, si aún sondea, declara C1 (memoria) → sin mutaciones.
5. Re-ejecutar readiness sobre polls **acreditados** de S y C2 antes de tratar el run como `ready`.

No se comparte un nonce entre servidor y cliente. Si se compartiera, el servidor podría presentarse como `peer=client`.

### 7.4 Resultado tarde de una instancia retirada

`store_result` no es fail-open. HTTP 200 + `discarded` para que el puente no haga backoff (`MCPBridge.c:3289-3291`). Audit `late_result_fenced`. `/await` del encolador ya debería haber visto `binding_retired` / `peer_reconnect_flush` / `stale_discarded` según el camino. Un resultado con el **mismo** `instance` y epoch viejo se acepta como finalización tardía del comando original y no se reutiliza para un id nuevo.

### 7.5 Dos procesos arrancados con el mismo fichero de config

Los dos hacen `TryInit`, copian el mismo `instance`, sondean el mismo `inst=`. El primero que case con el PID registrado queda `BOUND`. El segundo PID, o una atribución que vea dos owners, pasa el binding a `AMBIGUOUS`. Cero mutaciones a ambos hasta que quede un solo PID. Este es el canario (sección 8.2). No se “elige al que sondea más rápido”: eso es BUG-096.

---

## 8. Plan de tests

### 8.1 Unitarios

Cada test tiene que poder ponerse rojo. Nombre → frase de fallo.

| Test | Este test falla si… |
|---|---|
| `test_record_poll_without_instance_does_not_deliver_mutation` | un `player_teleport` encolado se entrega a un poll sin `inst=` |
| `test_record_poll_without_instance_still_delivers_read` | un `query_player_state` / `camera_get` deja de llegar por el camino legacy |
| `test_enqueue_mutation_without_binding_is_legacy_unbound` | `/enqueue` de mutación sin binding no devuelve 409 `legacy_unbound` y encola igual |
| `test_bound_poll_receives_only_its_instance_queue` | un poll `inst=C1` recibe un comando sellado a `C2` |
| `test_role_mismatch_does_not_drain_server_queue` | un poll `peer=client&inst=<server-uuid>` vacía o entrega la cola del servidor |
| `test_offline_instance_may_poll_both_peers` | un binding `role=offline` deja de recibir `query_player_state` (server) o `camera_get` (client) |
| `test_reload_key_after_failure_does_not_assign_peer_instance` | `ReloadKeyAfterFailure` en cualquiera de los dos `.c` contiene `m_PeerInstance` |
| `test_try_init_copies_instance_once_in_both_bridges` | `TryInit` no asigna `m_PeerInstance` o `StartPoll` no concatena `inst=` |
| `test_start_poll_omits_inst_when_peer_instance_empty` | un puente sin `instance` en el JSON envía `inst=` vacío (rompe el parseo y el clasificador) |
| `test_reconnect_gap_does_not_flush_bound_queue` | un hueco > `PEER_RECONNECT_GAP_S` (`loopback.py:103`) en un peer `BOUND` descarta su cola |
| `test_reconnect_gap_still_flushes_legacy_queue` | el flush de BUG-041 deja de vaciar `_legacy_queues` (regresión de `test_reconnect_flush_drops_previous_session_command`, `test_loopback.py:908`) |
| `test_command_ttl_still_expires_bound_command` | un comando bound más viejo que `COMMAND_TTL_S` (`loopback.py:102`) se entrega |
| `test_second_pid_same_instance_marks_ambiguous` | dos `source_pid` distintos con el mismo `inst=` siguen recibiendo mutaciones |
| `test_unattributed_poll_gets_zero_commands` | `source_pid is None` entrega la cola bound |
| `test_store_result_wrong_instance_is_discarded_200` | un POST `/result` con `inst` ajeno escribe el resultado en `_results` o responde ≠ 200 |
| `test_adopt_run_does_not_confirm_binding` | `adopt_run` deja el run en `BOUND` sin `prepare`/`confirm` |
| `test_start_run_writes_instance_before_popen` | `launcher` se invoca sin que `dayz_mcp.json` del perfil lleve el UUID mintado |
| `test_start_run_does_not_write_mission_config` | `start_run` crea o muta `$mission:dayz_mcp.json` |
| `test_client_relaunch_retires_old_instance_keeps_server` | tras un segundo `start_run` client, un poll `inst=C1` recibe mutaciones o el server pierde el binding |
| `test_status_hides_full_instance` | `/status` o el audit contienen el UUID de 36 caracteres |
| `test_ready_uses_bound_last_poll_not_legacy` | un poll legacy del mismo rol mantiene `ready=true` cuando el binding bound está stale |
| `test_version_gate_unchanged_for_v8_without_inst` | un `ver=8~x` sin `inst=` pasa a `version_mismatch` o `version_blocked` |
| `test_expected_bridge_version_stays_8` | alguien sube `EXPECTED_BRIDGE_VERSION` / `MCP_BRIDGE_VERSION` en esta v1 |

Los tests existentes de TTL/flush (`test_loopback.py:876-953`) se duplican sobre el camino **legacy** (siguen siendo la higiene de BUG-041) y se añaden las variantes bound de la tabla. No se borra `StaleCommandHygieneTest`.

`test_five_endpoints_round_trip` (`test_loopback.py:94-105`) encola `query_player_state` (lectura, `READ_ONLY_COMMANDS`) y poll sin `inst=`. Debe seguir verde.

Extender `test_poll_key_reload_contract.py:42-74`: además de `m_Key = cfg.key` y `if (cfg.key == m_Key)`, afirmar que el cuerpo de `ReloadKeyAfterFailure` **no** contiene `m_PeerInstance`. Este test falla si la recarga de key se convierte en recarga de identidad.

### 8.2 Canario in-game

Cabe en la **misma** sesión que las regresiones del PBO. No se abre un run nuevo. No se para el juego del usuario.

El recurso caro es el DayZDiag ya sentado. El segundo cliente es un proceso extra de vida corta; se mata al terminar el canario, no el run de la sesión.

**Qué mide.** `wrong_target_canary_count`: número de `camera_set` cuyo efecto visible aparece en una ventana cuyo PID no es `command.target_pid` (el `ProcessRecord.pid` del cliente bound).

**Valor que refuta.** `wrong_target_canary_count != 0`. Un solo frame del cliente extra con la pose mandada tumba el diseño, aunque `/status` diga `BOUND`.

**Procedimiento.**

1. Precondiciones de la sesión ya viva: lease tomado, `bridge_status.ready.reason == ready`, binding del cliente `BOUND`, se anota `C = instance` (del JSON del perfil, no del log), `P_bound = pid` del cliente registrado, `P_server` del servidor.
2. Grab de control, **por PID**, del cliente bound y —cuando exista ventana— del extra (aún no): `mcp_capture.grab_window_to_file(..., client_pid=P_bound)` (`mcp_capture.py:366-371`). Guardar PNG A_bound.
3. Copiar `{client_profiles}\dayz_mcp.json` a un directorio temporal **sin** pasar por `start_run` (un `start_run` remintaría). Lanzar un segundo `DayZDiag_x64` con `-profiles=<temp>` y el mismo `-connect`/`-port` que el run. Anotar `P_extra`.
4. Esperar a que el extra esté in-game lo justo para renderizar (no se exige `ready` global; el binding debe haber pasado a `AMBIGUOUS` o, si la atribución falla, a cero entregas). Si el extra no llega a pintar en ~60 s, se aborta el canario como `inconclusive`, no como pass.
5. Con el lease de la sesión, enviar 5 veces `camera_set` en modo `lookat` (`server.py:2421-2422`) a una pose inequívoca (p. ej. `cam_pos` sobre el jugador y `look_at` al cenit). No es input físico.
6. Grab B_bound (`client_pid=P_bound`) y B_extra (`client_pid=P_extra`).
7. Contar: si B_extra muestra la pose mandada (cámara al cenit / cielo) y A_extra no la tenía → `wrong_target_canary_count += 1`. Si B_bound no cambia, el canario no refuta el aislamiento (puede ser `AMBIGUOUS` fail-closed: nadie recibió el comando). Eso es **aceptable** para la invariante; se anota `delivered_to_target=0` como dato, no como fallo.
8. Terminar **solo** `P_extra`. No llamar `dayz_test_stop` sobre el run de la sesión. Confirmar que el binding vuelve a `BOUND` o que un `camera_set` posterior aterriza en `P_bound`.
9. Seguir con las regresiones de la misma sesión (`camera_get`, un `surface_query`, `ui_click` si el PBO lo trae).

`capture_screenshot` por defecto no filtra por PID (`server.py:2469`, `process_name="DayZDiag_x64"`). El canario **no** usa esa herramienta a secas: usaría la ventana que Windows considere principal. El harness del canario llama a `grab_window_to_file` con `client_pid`.

---

## 9. Qué NO entra en v1

Recorte agresivo. Si no tapa BUG-096, no entra.

- **Cola de caja / reservas horarias.** D-55.7: la última, y solo si el dato la pide. Antes hay que tapar tombstones globales, persistencia de `/session/*` y el reaper que no despierta `_condition`.
- **Movimiento 1** (perfil por run + key por proceso). Cancelado tras medirlo: el worker viaja sellado en un bundle firmado, la rotación de logs ya es por lanzamiento, el perfil del servidor es estado compartido de otros mods. Esta spec no lo propone. El daemon escribe `instance` en el perfil **existente** del rol.
- **Bump `MCP_BRIDGE_VERSION` 8→9.** Sección 6. El campo se versiona aparte.
- **Persistir bindings / epoch en `runs.json`.** Cambio de formato persistente. v1 es `UNBOUND_AFTER_RESTART`.
- **Rebind de un proceso vivo** leyendo su JSON tras `adopt_run`. El puente no recargaría un UUID nuevo; reusar el viejo sin atribución de PID reabre el canario.
- **Multi-run mutante, scheduler de puertos, resource manager genérico.**
- **Lock `desktop.input` / `GetForegroundWindow` para `ui_click`.** `ui_click` no sintetiza input (`InvokeUiClick`, `MCPClientBridge.c:1747-1783`).
- **Herramientas nuevas de click físico.** No existen hoy en `tools\dayz_mcp`. No se añaden. La fase 3 de `ui_dialog` (clicks físicos) queda fuera.
- **Compatibilidad mutante con puentes sin `instance`.** Legacy = lectura, desde el día uno. Sin flag `ALLOW_LEGACY_DISPATCH`.
- **Meter `instance` dentro de `ver=`.** Mezcla versión e identidad y complica el sunset.
- **Rotar la API-key para rotar identidad.** Identidad ≠ secreto.
- **Exigir `$profile:dayz_mcp.json` y romper el fallback a `$mission:`.** El fallback se queda para no dejar tirado un juego que solo tiene misión; ese camino es legacy no cercado. Los runs gestionados escriben `$profile:`.
- **Tocar el worker nativo / el bundle firmado.**
- **Validar UUID en Enforce.**
- **Matar o reclamar un DayZDiag ajeno.**

---

## 10. Riesgos y NO VERIFICADO

### Riesgos

- **Atribución TCP no unívoca.** Si DayZ reutiliza `RestContext` de un modo que la tabla TCP no distingue dos clientes, el canario es la única medida que vale. El diseño, si la atribución falla, bloquea (no entrega). Un falso “mismo PID” para dos procesos es el modo de fallo que el canario está diseñado para ver.
- **`JsonFileLoader` y un campo de más.** Si el loader de un PBO viejo rechaza `instance` en el JSON, `ReloadKeyAfterFailure` de un proceso ya vivo podría dejar de parsear el perfil. Mitigación: `start_run` solo escribe el perfil del proceso que va a lanzar; un proceso ya configurado no relee el JSON salvo fallo de poll. Un PBO viejo que arranque *después* de esa escritura, si el loader es estricto, no configurará (fail-closed). Comportamiento del loader: NO VERIFICADO.
- **`ready` y ocupación.** Si la implementación actualiza `last_poll_at` con polls legacy y `compute_bridge_ready` no se cambia, un juego ajeno mantiene `ready=true`. El campo `bound_last_poll_age_s` es parte del contrato, no un extra.
- **`start_run` que añade un segundo cliente** (`processes.append`, `process_lifecycle.py:1080`) sin retirar el `ProcessRecord` muerto. El fencing retira el **binding**; el manifiesto puede seguir listando el PID viejo hasta el reaper. No se limpia el manifiesto en v1.
- **Centinela rojo** hasta el gate in-game. No re-congelar sobre source-only.
- **Logs de script del cliente** no se vuelcan en caliente. El canario no usa esos logs como oráculo; usa PNG por PID.

### NO VERIFICADO

Afirmaciones que esta spec **no** pudo cerrar abriendo un fichero o midiendo:

- Comportamiento de `JsonFileLoader<MCPConfig>` ante un campo JSON ausente (`instance` → `""`) y ante un campo de más (PBO viejo leyendo JSON nuevo).
- Si el `RestApi` / `RestContext` de DayZDiag reutiliza la conexión loopback lo bastante rápido como para que `psutil.net_connections` no separe dos clientes.
- Si el proceso offline carga de verdad **ambos** puentes (`MCPBridge` y `MCPClientBridge`) contra el mismo `$profile:`. El launch (`dayz_test_worker.py:262-274`) usa un solo `-profiles=`; no se abrió una traza in-game de offline en esta copia.
- Si `capture_screenshot` sin `client_pid` elige de forma estable la ventana del extra cuando hay dos clientes (el canario no se fía y pasa `client_pid`).
- Contenido de `tools\dayz_mcp\__pycache__` y de cualquier daemon vivo fuera de esta copia.
- El árbol OneDrive real; esta spec se escribió sobre la copia aislada.

Lo que sí se abrió y se cita: `loopback.py`, `core.py`, `process_lifecycle.py`, `session_coordination.py`, `daemon.py`, `server.py`, `runtime_state.py`, `accredited_daemon_transport.py`, `native_process_guard.py`, `orphan_guard.py`, `dayz_test_worker.py`, `install_mcp.py`, `mcp_capture.py`, `test_task9_spawn_phase_markers.py`, `test_poll_key_reload_contract.py`, `test_batch6.py`, `test_loopback.py`, `MCPMessages.c`, `MCPBridge.c`, `MCPClientBridge.c`, `decisions\decision-log.md` (D-52..D-55), y el consejo en `reviews\2026-08-18-council-lease-cola\`.

---

## Objeciones al marco

1. **D-55.8 vs. el encargo de esta spec.** El decision-log, punto 8, todavía lista el movimiento 1 (perfil por run + key por proceso) como pieza del marco y el HANDOFF lo da como próxima acción. El encargo de esta spec lo declara **cancelado** tras medirlo y prohíbe proponerlo. Esta spec obedece el encargo y deja el movimiento 1 fuera de v1. No se ha encontrado imposibilidad en el código; hay que arbitrar el decision-log (actualizar D-55.8 o retractar la cancelación) fuera de esta lane.

2. **Nada de D-55.1–7 ni D-55.9 es imposible** contra el árbol abierto. El relanzamiento parcial (D-55.3) cabe en `start_run` extensible (`process_lifecycle.py:897-911`) más `retire_role`. El consejo Codex recortaba ese relanzamiento en su “v1 de ~300 líneas”; D-55 manda y esta spec lo implementa. El consejo Grok pedía un nonce por run; D-55.3 lo sustituye por proceso y esta spec no lo reabre.
