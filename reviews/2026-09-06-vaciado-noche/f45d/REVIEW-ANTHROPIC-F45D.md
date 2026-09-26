# REVIEW-ANTHROPIC-F45D — dictamen adversarial (familia Anthropic, Fable) sobre el lote f45d

**Veredicto: SEGURO INTEGRAR.** Ningún hallazgo BLOQUEA ni DEBE; los cuatro PUEDE (H1–H4) son mejoras o decisiones
fuera del contrato del brief, con diff mínimo listo por si Guillermo las quiere en este lote o en el siguiente.

Convención de citas: `server.py:N` es el fichero de `lote-f45d/ws` (diff aplicado) salvo que diga `HEAD`.
`loopback.py`, `instance_fence.py`, `session_coordination.py`, `control_client.py`,
`accredited_daemon_transport.py` y `tests/test_client_mode.py` son idénticos en ws, wt y HEAD `4e264bb`
(sha256 comprobado: `loopback.py` 9c3c270e…, `instance_fence.py` c089636a…, `test_client_mode.py` 6eb705d3…).

Estado de los árboles al revisar (importa para la integración, no para el juicio del diff):
- ws `server.py` sha256 `5641ec14…` = el que STATE declara; mi `diff -u <(git show HEAD:…server.py) ws/server.py`
  reproduce `DIFF-F45D-product.patch` byte a byte salvo las dos líneas de cabecera. `test_wait_for.py` ws
  `571dba41…` = STATE. Módulo nuevo `6143cefa…` = STATE.
- `wt-f45d` ya NO está prístino: `git status` marca ` M tools/dayz_mcp/server.py`, ` M tools/tests/test_wait_for.py`,
  `?? tools/tests/test_enqueue_refusal_reaches_the_caller.py`. Su `server.py` (sha `b832f741…`) es el de ws con
  las tres líneas largas re-envueltas (`:208`, `:759-762`, `:4886-4891`); nada más difiere. Es edición del
  integrador, no de Grok; ver H4.
- Árbol vivo `DayZ_MCP_dev`: `server.py` `47fded30…` y `test_wait_for.py` `3aba7e1b…` = HEAD; el módulo nuevo no
  existe. El parche aplica limpio sobre el vivo.

## Hallazgos

### H1 — PUEDE — `session_granting` sale por `/enqueue` y sigue llegando como `remote_error` desnudo
- Dónde: `session_coordination.py:3412-3437` `_validate_token_locked` devuelve `"session_granting"` (`:3423`)
  cuando el token pertenece a la concesión en vuelo (`_grant_inflight`, asignada en `:422-423` mientras se
  escriben los audits iniciales del grant, `:426-431`); `authorize` lo propaga (`:1210-1226`) y
  `loopback.py:1591-1605` lo pone en `{"error": decision.error}` → 409 (`_token_error_status`, `:3704-3709`).
  `server.py:150-201` no lo contiene (grep `"session_granting"` en `server.py`: 0 hits); tampoco
  `_CONTROL_CLIENT_ERROR_CODES` (`:363-379`).
- Qué está mal: es la misma clase de bug que cierra f45d (código del daemon aplastado a `remote_error`), para un
  código que el brief no listó y que el censo T2 no puede ver por construcción (es `decision.error`, una variable,
  y vive fuera de las seis funciones; ver respuesta 4). Pre-existente, no introducido por el diff; ventana
  estrecha (solo un cliente que reenvíe el token durante el grant en vuelo). No hay test que fije el texto
  cliente de ese código (grep en `tools/tests`: solo `test_admin_cli.py:140-150` y
  `test_bug046_lease_queue_liveness.py:771/951`, ambos del lado daemon).
- Cómo lo verifiqué: lectura de los `path:line` citados; ejecución del censo con lista de códigos alcanzables
  fuera de las seis funciones (`session_granting whitelisted=False in_census=False`; los demás
  —`lease_required`, `lease_expired`, `identity_mismatch`— están en la lista aunque el censo no los vea).
- Corrección propuesta (decisión de Guillermo: amplía la lista blanca más allá del brief):
  ```
  --- tools/dayz_mcp/server.py
  @@ -199,3 +199,5 @@
       "run_state_unavailable",
       "enqueue_cancelled",
  +    # lease-grant race surfaced by session_coordination._validate_token_locked on /enqueue.
  +    "session_granting",
   })
  --- tools/tests/test_enqueue_refusal_reaches_the_caller.py
  @@ -68,2 +68,4 @@
       codes |= set(instance_fence.FENCE_ENQUEUE_CODES)
       codes.add(loopback._DURABLE_UNREADABLE)
  +    # Emitted by session_coordination.authorize, outside the walked functions.
  +    codes.add("session_granting")
  ```

### H2 — PUEDE — `_carriable_hint` deja pasar caracteres de control distintos de `\n`/`\r`
- Dónde: `server.py:207-220`. Filtra tipo, longitud 1..240, `strip()` y `\n`/`\r` (`:212-219`), exactamente lo
  que el brief §3 dictó como [EXACT]. Grok cumplió la spec; el hueco es de la spec.
- Qué está mal: ejecutado contra ws, `{"error": "run_not_owned", "hint": "a\tb"}` → `'run_not_owned: a\tb'`,
  `"a\x00b"` → `'run_not_owned: a\x00b'`, `"\x1b[31mred\x1b[0m"` → viaja con las secuencias ANSI. Hoy es
  inerte: todos los hints de la ruta `/enqueue` son constantes (`loopback.py:99-102` y `:1327-1328`;
  `instance_fence.py:22-70` y `:157-162`; ninguno formateado con datos) y el emisor es el daemon acreditado
  (respuesta 2). Los hints dinámicos del daemon (`process_lifecycle.py:475`, `:504-515`, `:540`,
  `_ACTIVE_RUN_STOP_HINT.format(run_id=…)`) van por el canal de sesión/lifecycle, no por `/enqueue`.
- Corrección propuesta (una línea, subsume la comprobación de `\n`/`\r`; añadir el caso `"a\x1bb"` a
  `test_an_oversized_or_malformed_hint_is_dropped`):
  ```
  --- tools/dayz_mcp/server.py
  @@ -216,3 +216,3 @@
  -    if "\n" in hint or "\r" in hint:
  +    if not hint.isprintable():
           return None
  ```

### H3 — PUEDE — La receta de la descripción de `wait_for` es cierta solo con UN run ocioso sin dueño
- Dónde: `server.py:4886` («adopt the run with session_acquire_wait first»).
- Qué pasa de verdad: `session_acquire_wait` = `/session/enqueue` (acción `enqueue`, `loopback.py:134`, sin
  adopción) + bucle de `/session/wait` (`control_client.py:726-738`); el daemon llama a `_adopt_on_grant` solo
  para `action in {"acquire", "wait"}` con status 200 (`loopback.py:3333-3334`) y solo si el payload es
  `status == "active"` (`:3126-3127`). `_adopt_on_grant` (`:3119-3245`, P-J2) adopta «the unique ownerless
  RUNNING_IDLE run» (`:3120`): con 0 runs ociosos devuelve `adopted_run: None` (`:3199-3201`), con >1 devuelve
  `{"ok": false, "error": "multiple_idle_runs"}` y no adopta (`:3202-3208`); solo con exactamente uno llama a
  `lifecycle.adopt_run` (`:3209`). El resultado va en `adopted_run` del grant, que el cliente devuelve entero
  (`control_client.py:738` `_with_own_identity` copia el dict; `server.py:3172-3197` lo retorna tal cual), así
  que el llamante SÍ puede comprobarlo. Para el incidente (un run) la receta es correcta; en general es incompleta.
- Cómo lo verifiqué: lectura de los `path:line`; grep `adopted_run` en `server.py` (0 hits: no se filtra ni se
  documenta).
- Frase exacta propuesta (T4 usa `assertIn` de `run_not_owned` y `session_acquire_wait`, sigue verde):
  ```
  "A probe refused with run_not_owned (the run has no owner) aborts on the first probe with the daemon's hint: "
  "adopt the run first with session_acquire_wait, whose grant adopts the single ownerless RUNNING_IDLE run and "
  "reports it in adopted_run (with several idle runs it reports multiple_idle_runs and adopts none). "
  ```

### H4 — PUEDE (cosmético, ya resuelto en wt por el integrador) — tres líneas de 108/182/169 caracteres
- Dónde: `server.py:208` (docstring de `_carriable_hint`), `:759` (docstring de `_public_enqueue_error`),
  `:4886` (descripción). `tools/pyproject.toml` no declara `[tool.ruff]`/flake8 ni `line-length`; HEAD ya tiene
  48 líneas >100 (máx. 532), ws 51: ningún gate lo detecta. La copia de trabajo de wt ya las envuelve.
- Riesgo real: que se integre la versión envuelta sin volver a correr los dos módulos; el texto de la descripción
  se concatena por literales adyacentes y T4 usa `assertIn`, así que el envoltorio es seguro, pero el sha256 que
  STATE registra (`5641ec14…`) ya no describirá el fichero integrado. Correr
  `tests.test_enqueue_refusal_reaches_the_caller` y `tests.test_wait_for` sobre lo que se comitee, no sobre ws.

## Respuestas a las ocho preguntas

### 1. Invariante de redacción
Con el cambio, el único texto remoto que puede llegar al llamante además de un código de la lista blanca es
`payload["hint"]`, y solo si (a) `_remote_error_code` (`server.py:384-389`) devolvió un código de
`_REMOTE_ERROR_CODES` distinto de `remote_error`, (b) ese código no cayó en una rama con receta (`:762-785`, que
retornan antes de la cola), y (c) el hint pasó `_carriable_hint` (`:207-220`). Cola en `:786-789`. Ejecutado:
- código desconocido + hint → `'remote_error'` (también por `call_bridge` con `_call` fijado);
- `run_not_owned` + hint de 10 000 chars → `'run_not_owned'` desnudo (el motivo sobrevive, el hint no);
- `run_not_owned` + hint con texto con forma de token → `'run_not_owned: token=active-token-shaped-test-value'`:
  viaja, por diseño (DECISION: el hint es prosa del daemon acreditado pegada a un código conocido). Hoy ningún hint
  de `/enqueue` se formatea con datos (H2); el canal de sesión ya llevaba hints sin cota antes de este lote
  (`control_client.py:196-204` + `server.py:1214-1228` `public_error_code` → `str(error)`), así que el cambio no
  abre un canal nuevo, lo acota (240, sin `\n`/`\r`) en el que faltaba.
`tests/test_client_mode.py:331-370` es byte a byte HEAD (sha `6eb705d3…` en ws y en `git show HEAD:`) y verde
(ejecutado; ver abajo). Nota: sus payloads no llevan `hint`, por eso no detecta el mutante (c); lo detecta
`test_an_unknown_code_drops_its_hint` (`NEW:114-124`). Está bien que el control viejo no cambie (fuera del write-set).

### 2. Procedencia del hint
Canal: `ClientRuntime.call_bridge`/`enqueue_bridge` (`server.py:1545-1553`, `:1636-1644`) → `_call` (`:1471-1532`)
→ `_request_once` (`:1439-1469`) → `daemon_credential.request_with_refresh` → `accredited_daemon_transport`.
La garantía está en el transporte, no en el cambio: antes de enviar un solo byte HTTP, sobre el socket ya
conectado, `_connected_daemon_identity_verified` (`accredited_daemon_transport.py:143-200`) resuelve el PID del
extremo servidor por la tabla TCP (`_connected_server_pid`, `:78`), exige ejecutable/argv/cwd iguales a la
procedencia esperada del daemon, contrasta el snapshot del guard (`pid`, `creation_time_utc`, `executable_sha256`,
`command_line_sha256`, `identity_scheme == "psutil-argv-v2"`) y repite la resolución del PID para descartar un
relevo; host fijado a `127.0.0.1` (`:230`); se aplica en cada petición en `:262-273` y un fallo es
`daemon_identity_unverified`, que `_call` convierte en `ToolError` sin reintento (`server.py:1496-1498`,
`:1512-1513`); el cuerpo de respuesta está acotado (`max_response_bytes`, `:286-288`) y `_decode_body` rechaza
no-JSON/no-dict (`daemon_bad_body`). Los tests T3 sustituyen todo esto fijando `runtime._call`, así que no prueban
esta garantía (y no tienen que hacerlo). En modo embebido (`Runtime.call_bridge`/`enqueue_bridge`/
`call_exec_enforce`, `server.py:932-999`) el payload viene de `self.state.enqueue_command` en proceso: no hay
puerto que suplantar.

### 3. Comparaciones exactas rotas
Ninguna deja de funcionar. Grep de `(==|!=|in {|.startswith()` sobre texto de error en `tools/dayz_mcp/*.py`:
- `server.py:1556` y `:1647`: ahora `_remote_error_code(payload) in _STALE_LEASE_ERRORS`. Para `lease_expired`/
  `lease_invalid` desnudos la semántica es idéntica: ambos están en la lista (`:166-167`), ninguna rama con receta
  los intercepta (`:762-785`), luego antes `_public_enqueue_error` devolvía el mismo token que hoy devuelve
  `_remote_error_code`. Con hint (`lease_invalid` + `"x"` → `'lease_invalid: x'`) la versión vieja habría dejado
  de limpiar el lease; la nueva limpia (T3 `NEW:218-228`, ejecutado). `test_client_mode.py:443-456` verde.
  El `Runtime` embebido no compara con `_STALE_LEASE_ERRORS` (grep: solo esas dos líneas).
- `execute_wait_for` `server.py:2647-2671`: `== "game_not_ready:reason=server_poll_stale"` (`:2648`) y los
  `startswith("version_blocked"/"game_not_ready")` (`:2659-2661`) comparan textos que producen las ramas con receta,
  que retornan antes de la cola del hint; `"daemon_unavailable"` es código de transporte sin payload;
  `"query_all_players" in message` (`:2666-2669`) reescribe el nombre del verbo: ninguno de los 12 hints de la ruta
  (11 `FENCE_HINTS` + `_RUN_NOT_OWNED_HINT`) contiene esa subcadena (comprobado por ejecución); `else: raise`
  (`:2670-2671`) entrega el texto intacto, fijado por T5 (`test_wait_for.py:170-178`).
- `playbook_tool.py:116-125` (`map_schema_error`, errores de esquema, no de enqueue) y `:224`
  (`ToolCallError(str(exc))`, envuelve sin comparar): intactos.
- `ui_dialog.py:203-209`: docstring sobre que `bad_args` queda como token fijo; `bad_args` no lleva hint. Intacto.
- `dayz_test_tool.py:255`: compara el token de un `ValueError` del parser, no texto de enqueue. Intacto.
- Otras igualdades: `server.py:1830`, `:1860`, `:4968`, `:4992`, `:5028` comparan `bad_args` (sin hint).
- Tests: ningún test del árbol aserta `str(exception) == "<código con hint>"` para los códigos que ahora pueden
  llevarlo (grep en `tools/tests`: 0). Los módulos que tocan códigos de valla y que Grok no corrió los corrí yo:
  `test_instance_fence` 64 OK, `test_fence_canary_probe` 24 OK, `test_d09_d10_spawn_timeout_object_id` 2 OK,
  `test_dayz_test_tool` 57 OK (ninguno pasa por `_public_enqueue_error`; `test_process_lifecycle` no se corrió,
  levanta procesos).

### 4. El censo por AST
- Recorre lo que dice: las seis funciones existen como `def` síncronos en el cuerpo de `class ServerState`
  (`loopback.py:859`): `_enqueue_run_rejection :1293`, `_fence_reject_response :1323`, `_enqueue_fence_target
  :1331`, `enqueue_command :1552`, `_enqueue_command :1747`, `_enqueue_exec_enforce :1853`. Re-recorrido por mí:
  6 `FunctionDef`, 0 `AsyncFunctionDef` (un `async def` habría sido saltado en silencio por `NEW:65`).
- Recoge los `return "<str>"` de `_enqueue_run_rejection` (`:1306`, `:1310-1311`, `:1313`, `:1318`, `:1320-1321`),
  el dict `{"error": "run_state_unavailable"}` de `_fence_reject_response` (`:1325`) y las tuplas
  `("<code>", None, None)` de `_enqueue_fence_target` (`:1352`, `:1361-1372`, `:1379-1387`). Censo reproducido:
  25 códigos, lista idéntica a la de STATE.
- No puede ver por construcción: (i) variables: `decision.error` (`:1595`), `rejected.error` (`:1630`),
  `args_error` (`:1765`), `rejection` (`:1359`, `:1377`; sus literales están en `_enqueue_run_rejection`, así que
  no se pierde nada), `code` en `_fence_reject_response` (`:1326`); (ii) lo emitido fuera de las seis funciones:
  `session_coordination.authorize` (`:1159-1265`: `lease_required :1213`, `audit_failed :1239`,
  `lease_invalid :1243`; vía `_validate_token_locked :3412-3437`: `lease_required`, `session_granting :3423`,
  `lease_invalid`, y los tokens invalidados `lease_expired`/`lease_invalid` de `:2149`), `reject_reservation`
  (`retail_quarantine`), `validate_command_args` (`:781-796`: solo `bad_args`; el delegado
  `ui_dialog.validate_command_args` `ui_dialog.py:203-215` también solo `bad_args`), y los códigos de la capa
  HTTP en la misma ruta (`bad_content_length :3046/:3049`, `bad_json :3065/:3071`, `bad_json_type :3075`). De
  todos ellos solo `session_granting` falta en la lista blanca (H1). Un recorrido extendido (todo literal str con
  forma de identificador en cualquier posición de las seis funciones) no aporta ningún código de error más: dentro
  de su perímetro el censo es completo.
- No está vacío ni es tautológico: sobre HEAD falla nombrando `enqueue_cancelled, run_not_owned,
  run_state_unavailable` (ejecutado); con el mutante (a) falla; el control de vacuidad (`NEW:168`) y el de
  requeridos (`NEW:169-175`) están.

### 5. La descripción de `wait_for`
Verdadera para el caso medido y para cualquier caso con un único run ocioso sin dueño; incompleta en general
(0 o >1 runs ociosos): H3, con cita `loopback.py:3119-3245` y frase propuesta.

### 6. Alcance
- `diff -rq` wt(HEAD)↔ws excluyendo `__pycache__`/`.git`: solo `tools/dayz_mcp/server.py`, más `STATE.md` y
  `BRIEF.txt` en la raíz del ws (artefactos del encargo, no se integran); `test_wait_for.py` y el módulo nuevo ya
  estaban copiados en wt por el integrador y son idénticos a ws.
- El parche de `server.py` tiene 7 hunks y ninguno toca `retail_quarantine`/`lease_required`/`version_blocked`
  (`:762-785`), `execute_wait_for` (`:2511-2700`), `_remote_error_code` (`:384-389`), `_game_not_ready_reason`
  (`:656-670`), `_target_peer_down` (`:672-684`) ni `_bridge_error` (`:729-742`). El diff contra HEAD que generé
  yo coincide con el entregado.
- Efecto no nombrado por el brief pero dentro del contrato («cualquier verbo de puente»): el `Runtime` embebido
  (`server.py:932-999`) también pasa por `_public_enqueue_error`, así que el hint viaja igualmente en modo
  embebido. Sin cambio de código allí.

### 7. Tests: ¿miden lo que dicen?
Mutante que mata a cada test (verificado con mutantes en memoria, tabla abajo):
- `test_the_idle_run_refusal_keeps_its_code_and_hint`: (a) y (b).
- `test_every_run_fence_code_is_whitelisted`: (a).
- `test_a_whitelisted_code_without_hint_stays_bare`: verde en HEAD y ws (control declarado en el brief); lo mata
  un mutante «siempre pegar» (`return f"{code}: {payload.get('hint')}"`). No tautológico.
- `test_a_fence_hint_travels_with_its_code`: (b); control de vacuidad presente (`NEW:103-106`). Nota: fija de
  paso que los 11 hints caben en 240 (el más largo, `binding_retired`, mide 204): si un hint crece, cae aquí.
- `test_an_unknown_code_drops_its_hint`: (c); verde en HEAD (control negativo).
- `test_an_oversized_or_malformed_hint_is_dropped`: (a), (b) (caso 240) y cualquier relajación de cota/forma.
- `test_the_recipes_keep_their_own_text`: verde en HEAD y ws; lo mata mover la cola del hint por delante de las
  ramas con receta. Pin, no rojo-primero (STATE lo anota así).
- T2 censo: (a); rojo en HEAD.
- T3 `call_bridge`/`enqueue_bridge`: (a) y (b).
- T3 `test_a_hinted_stale_lease_still_clears_the_local_lease`: verde en HEAD y ws (STATE lo anota); lo mata
  revertir solo `:1556` a `error in _STALE_LEASE_ERRORS` conservando la cola del hint. Es exactamente su papel.
- T4 descripción: rojo en HEAD.
- T5 (`test_wait_for.py:170-178`): verde en HEAD y ws, pin del `else: raise`; lo mata un mutante que trate un
  `ToolError` desconocido como not-ready.
Los tres mutantes de STATE son plausibles y los reproduje exactamente: (a) 6 rojos, (b) 5 rojos, (c) 2 rojos con
`test_session_http_error_never_echoes_unredacted_payload` verde (sus payloads no llevan hint).

### 8. Lo que el implementador dice no haber podido verificar
- Suite completa: prohibida por el brief; DECISION la programa tras el merge (baseline `Ran 2911, failures=2`).
  No bloquea.
- Repro contra proceso vivo: no hace falta para integrar; el mecanismo está fijado en las tres capas del cliente
  (`_public_enqueue_error`, `call_bridge`/`enqueue_bridge`, `execute_wait_for`) y el lado daemon lo fijan
  `test_box_occupancy.py:1384-1392` y `test_loopback.py:1137-1147` según DECISION (Grok corrió ambos módulos
  verdes: 81 y 74; yo no los releí ni los repetí). No bloquea.
- Contraste con git: hecho aquí (parche = HEAD→ws). No bloquea.
- «Acceso denegado» Win32: tampoco apareció en mis ejecuciones. No bloquea.
Ninguno de los huecos bloquea la integración.

## Lo que verifiqué ejecutando
Intérprete `…\tools\.venv-mcp\Scripts\python.exe`, cwd `<ws>\tools`, `PYTHONPATH=.`,
`PYTHONDONTWRITEBYTECODE=1` (ws y wt son de solo lectura; sin `__pycache__` nuevos).

```
git -C wt-f45d rev-parse --short HEAD              -> 4e264bb
git -C wt-f45d status --short                      ->  M tools/dayz_mcp/server.py /  M tools/tests/test_wait_for.py / ?? tools/tests/test_enqueue_refusal_reaches_the_caller.py
sha256 ws server.py / wt server.py / HEAD server.py -> 5641ec1485f7f013 / b832f741183a35bd / 47fded3015df6ba6
diff -u <(git show HEAD:…server.py) ws/…server.py | diff - DIFF-F45D-product.patch -> solo cabeceras (1,2c1,2)
diff <(tr -d '\r' < wt/server.py) ws/server.py     -> 3 bloques (208,209c208 / 760,762c759 / 4889,4891c4886): re-envoltura
grep -c $'\r' ws/server.py                         -> 0
sha256 live DayZ_MCP_dev server.py / test_wait_for.py -> 47fded3015df6ba6 / 3aba7e1b18e27746 (= HEAD)

python -m unittest -v tests.test_enqueue_refusal_reaches_the_caller -> Ran 12 tests in 0.149s / OK (12 ok listados)
python -m unittest tests.test_wait_for                                -> Ran 23 tests in 9.485s / OK
python -m unittest -v tests.test_client_mode.ClientModeTest.test_session_http_error_never_echoes_unredacted_payload
                      tests.test_client_mode.ClientModeTest.test_lease_errors_clear_only_matching_token_for_session_and_enqueue
                                                                      -> Ran 2 tests in 0.157s / OK
python -m unittest tests.test_fence_canary_probe                      -> Ran 24 tests / OK
python -m unittest tests.test_d09_d10_spawn_timeout_object_id         -> Ran 2 tests / OK
python -m unittest tests.test_dayz_test_tool                          -> Ran 57 tests / OK
python -m unittest tests.test_instance_fence                          -> Ran 64 tests in 1.889s / OK

Rojo-primero (git show HEAD:…server.py > review/server_head_4e264bb.py, sha 47fded3015df6ba6, cargado como
dayz_mcp.server antes de importar los tests; módulo nuevo + tests.test_wait_for):
  HEAD module loaded: run_not_owned whitelisted = False | has _carriable_hint = False
  FAIL x8: test_call_bridge_surfaces_the_idle_run_refusal_with_its_hint, test_enqueue_bridge_surfaces_the_same_text,
           test_the_wait_for_description_names_the_ownership_refusal, test_every_code_the_enqueue_route_can_emit_is_whitelisted
           (nombra enqueue_cancelled, run_not_owned, run_state_unavailable), test_a_fence_hint_travels_with_its_code,
           test_an_oversized_or_malformed_hint_is_dropped, test_every_run_fence_code_is_whitelisted,
           test_the_idle_run_refusal_keeps_its_code_and_hint
  Ran 35 tests in 9.679s / FAILED (failures=8)      (T5 verde en HEAD, como dice STATE)

Mutantes en memoria sobre ws (módulo nuevo + control negativo de test_client_mode, 13 tests):
  (a) _REMOTE_ERROR_CODES - {"run_not_owned"}         -> FAILED (failures=6): call_bridge…, enqueue_bridge…, censo,
                                                         oversized…, every_run_fence…, idle_run_refusal…
  (b) cola de _public_enqueue_error = `return code`   -> FAILED (failures=5): call_bridge…, enqueue_bridge…,
                                                         fence_hint_travels…, oversized… (caso 240), idle_run_refusal…
  (c) sin `code != "remote_error"`                    -> FAILED (failures=2): las dos subpruebas con hint de
                                                         test_an_unknown_code_drops_its_hint; control negativo OK

Payloads hostiles por _public_enqueue_error (y los tres primeros también por call_bridge con _call fijado):
  unknown+hint -> 'remote_error' | run_not_owned+hint 10000 -> 'run_not_owned' |
  run_not_owned+"token=active-token-shaped-test-value" -> 'run_not_owned: token=active-token-shaped-test-value' (lease local NO limpiado) |
  hint 240 -> viaja (len 255) | "a\tb", "a\x00b", "\x1b[31mred\x1b[0m" -> viajan (H2) | hint dict/bool -> desnudo |
  lease_invalid+"z" -> 'lease_invalid: z' | payload no-dict -> 'remote_error'
Longitudes de hints: _RUN_NOT_OWNED_HINT 80; FENCE_HINTS máx 204 (binding_retired); todos carriable; ninguno
contiene "query_all_players"; FENCE_ENQUEUE_CODES ⊆ _REMOTE_ERROR_CODES.
Censo reproducido: 25 códigos, lista = STATE; 6 FunctionDef visitados; recorrido extendido sin códigos extra;
session_granting whitelisted=False in_census=False.
```

## Lo que NO verifiqué
- Suite completa de `tools/tests` (prohibida); `tests.test_process_lifecycle` (levanta procesos); los diez módulos
  de regresión que STATE tabula no los repetí salvo los cuatro listados arriba (`test_client_mode` solo los dos
  tests citados).
- Ningún proceso vivo: ni daemon, ni juego, ni el caso medido de las 21:07. La garantía de procedencia (respuesta
  2) está verificada por lectura del transporte, no ejecutando la acreditación.
- `test_box_occupancy.py:1384-1392` y `test_loopback.py:1137-1147` (pins del lado daemon citados por DECISION): no
  releídos.
- Alcance práctico de la ventana `session_granting` desde un cliente bien comportado (H1): no medido.
- La copia de trabajo de wt con las líneas envueltas: no ejecuté tests sobre ella (H4 pide hacerlo antes de
  comitear).
