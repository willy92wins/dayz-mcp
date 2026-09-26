# Reparto del buzón — 2026-09-04 16:50 (sesión «lote J» → «Reserva MCP bugfixing (en espera)»)

Escrito por la sesión de los lotes G/H/I/J (`938664ff…`, autora de `bdeb87f`, `155e5b0`, `cdae732`) a petición de
Guillermo: «si queda algún ticket que no haya sido cerrado ni enviado a la otra sesión de remediación, mándalo a
`local_e84a1b8e-8577-4f17-917a-b7dd6fbf8054`; asegúrate de que no se pise con la otra sesión».

## Estado medido al repartir

- Buzón: **24 fichas sin resolución** (`%LOCALAPPDATA%\DayZ_MCP\inbox\feedback.jsonl`, 421 entradas, 397 con al menos
  una resolución; criterio last-wins por `resolves`). Lista y reparto abajo; suman 24.
- Repo `DayZ_MCP_dev`, rama `work/inbox-20260830-modules`, HEAD **`cdae732`** (F-06 del lote J: `_adopt_on_grant` exige
  `state ∈ RUN_STATES` y `owner_session_id: str|None`; 2 tests calibrados en rojo antes del parche; `tests.test_daemon` +
  `tests.test_loopback` 133 OK). Debajo: `7670703` (lote C de Vaciado, b2c4), `155e5b0` (lote J), `adc1c22`, `2d069d2`,
  `0eff49e`, `bdeb87f`.
- Daemon vivo: generación `4b615aba68304fec9c9d608bd0b0366c` (corre el lote J). Caja OCUPADA por la sesión WRX
  (`f932c01e-44d`, run `8e3d4d22`, `state=RUNNING` con `owner_session` — evidencia viva de que la concesión del lease
  adopta el run; antes del lote J todo run nacía sin dueño); LFQuad2 (`5487322c-48d`) en la FIFO para correr el gate
  in-game formal del lote J. No hay lease ni run de esta sesión.

## Quién tiene qué en el árbol (la frontera que NO se cruza)

| Sesión | Ficheros | Estado |
|---|---|---|
| **Vaciado de buzón MCP** (`local_32a70c44-caca-42fa-b72f-15138674efc1`), lote D = **6927** (+ `cabd` como anexo: «mismo remedio que 6927.4») | `tools/dayz_mcp/orphan_guard.py`, `process_lifecycle.py`, `daemon.py`, `server.py`, `tools/README-mcp.md` **modificados sin commitear EN el árbol vivo** (16:34-16:36); tests nuevos `tests/test_box_port_occupancy.py`, `tests/test_orphan_guard_udp.py` | EN VUELO. Hasta que anuncie su commit: nadie toca esos cinco ficheros ni `dayz_test_tool.py` |
| Tercera sesión | `decisions/decision-log.md` staged (`A`) | No tocar; commitear siempre por pathspec para no arrastrarlo |
| Lote J (esta sesión) | `tools/dayz_mcp/loopback.py`, `tools/tests/test_daemon.py` | Commiteados (`155e5b0`, `cdae732`); libres |
| Reserva | lo que abajo se le asigna, por grupos, respetando las dos filas de arriba | — |

Protocolo anti-pisado: antes de editar, `git status --short` + `git log -3 -- <fichero>`; una ruta ` M` de otra sesión se
espera, no se sobrescribe. Al abrir un lote, avisar por `send_message` a Vaciado (id arriba) con el write-set exacto. Vaciado
avisará a Reserva del commit del lote D (se lo pido en el mismo momento en que se envía este reparto).

## Reparto de las 24 fichas

### Vaciado (2) — no tocar
- `fb-20260904-114520-6927` — lote D en vuelo (ocupación de la caja por PUERTO UDP del sistema + imágenes de servidor;
  `wait_for_box_s` espera en vez de limpiar). Diagnóstico: `reviews/2026-09-04-lote-V/DIAGNOSTICO-6927.md`. Clase R9.
- `fb-20260904-022558-cabd` — LFHeli `run_batch_f1.ps1` y el servidor MCP se pisan el 2302: cierra con 6927.4 (Vaciado lo
  resuelve o lo devuelve explícitamente).

### R1 — Lifecycle, DESPUÉS del commit del lote D (5)
Ficheros: `process_lifecycle.py`, `daemon.py`, `dayz_test_tool.py`, `dayz_test_worker.py`/`dayz_test_request.py` (bundle
`app.pyz`: rebuild + G-PYZ). Los cuatro primeros son persistencia/recovery → **R9** (`rigorous-data-audit`, ≥2 ángulos).
- `fb-20260901-225325-76dd` (SUB_BRZ) — el reinicio del daemon por idle-timeout mata/olvida runs gestionados vivos. Hecho
  ya cerrado por los lotes G/H: los bindings NO sobreviven al reinicio (`unbound_after_restart`); el backlog escrito es
  «re-acreditación por `instance`». Opciones de la ficha: (a) el daemon re-adopta runs vivos al arrancar (ya identifica
  procesos por sha de CommandLine), (b) `keep_alive` con caducidad explícita. Es la fricción nº 1 del ciclo in-game.
- `fb-20260904-025733-d60f` (LFQuad3) — `dayz_test_stop` con cliente colgado al arrancar → `run_stop_failed`
  `cleanup_degraded=true` → `run_not_active` → run `UNRECONCILED` con `ports_in_use=[2302]` y caja bloqueada para todos.
  Falta una vía soportada para reconciliar/liberar sin matar procesos a mano (lifecycle guard). Anexo: `@Survivor
  Animations` sin `SurvivorAnimsExtras` cuelga el cliente (aviso a los demás proyectos).
- `fb-20260904-025027-8f76` (ForzaDayZ) — parte **(c)**: `dayz_test_run(mode=client, run_id=…)` debe relanzar cuando el
  bridge ve `client_not_polling`, en vez de reportar `client_alive` y no hacer nada. (a) `frame_sha256` entregado en el lote
  M; (b) foco de ventana = documental + in-game (va en R4).
- `fb-20260829-115147-4407` — rotar `storage_1` sólo cuando cambia el juego de mods (sello `.modset` = sha256 de la lista
  `-mod=` normalizada; 12 crashes `Failed to read modstorage` en 40 días). Módulo previsto M15
  `tools/dayz_mcp/dayz_test_storage.py` (0/10, sin un byte). Validación final in-game.
  Anexo archivado a las 16:48 (`fb-20260904-144835-01ae`, evidencia de LFQuad3/LFQuad2: tres modsets en un día sobre el
  mismo `storage_1`; el fallo se disfraza de `game_not_ready:reason=server_poll_stale`): hereda el estado de 4407 y se
  resuelven juntos (LL-324). Con él, el buzón queda en 25 entradas sin resolución; el reparto no cambia.
- `fb-20260830-010517-9d46` — los modos de `dayz_test_run` viven espejados en 6 sitios sin derivar del dueño
  (`dayz_test_tool.py:17-18`). Parche ya redactado por Vaciado y entregado al par:
  `%LOCALAPPDATA%\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\parche_modos_dayz_test_tool.py`
  (5470 B, 05:07). Exige rebuild del worker empaquetado; junto con el **punto 3 de `05 8f8c`** (`bad_dayz_test_request`
  sin motivo: `dayz_test_request.py` está en el contrato del bundle) forman UN lote «bundle».

### R2 — M23, lote DEDICADO y XL (3) — decisión de Guillermo (picker 14:55): «lote dedicado en sesión nueva, no mezclar»
- `fb-20260829-194752-d366` — instrumento de esquema efectivo construido y NO aterrizado (worktree sin commit; artefactos
  en ruta durable del vault, ver cuerpo de la ficha).
- `fb-20260829-024827-9b7b` — una sesión cliente anterior a un despliegue no ve los verbos nuevos y no puede saberlo
  (`tool_registry_fingerprint`, hoy 0 ocurrencias en `server.py`).
- `fb-20260829-104630-141e` — censo de 54 tools: el contrato no es auditable leyendo el cuerpo → promotor
  `tools/promote_effective_schema.py` (0 bytes hoy) + autoridad + CAS/journal.
  Hojas: `plans/inbox-20260830/{07,18,26}-*.md`, `S13/S14-authority-options.md`, `non-s15-successor-authority-v1.md`.
  Alternativa barata (comparar generación del daemon vs arranque de sesión) está PROHIBIDA por la hoja.

### R3 — Decisiones de Guillermo (5): preparar la propuesta con evidencia; NO ejecutar sin su palabra
- `fb-20260902-235135-077d` (AI_Pipeline) — receta de aislamiento `claude -p` con `CLAUDE_CONFIG_DIR` vacío responde
  `Not logged in`; el sustituto crea `~/.claude/.claude.json`. Guillermo la dejó abierta a propósito (no seleccionada).
- `fb-20260904-021510-1f85` (SUB_WRXSTI) — el workspace de runtime de `dayz-test-ingame` vive dentro de la skill y se borra
  al reinstalarla: edición de skill GOBERNADA (circuito Pack: editar `skills/…`, resellar `source-map.json`, validate,
  commit, promote; nunca el árbol vivo `~/.claude/skills`).
- `fb-20260830-011217-668f` (LFQuad2) — `dayz-test.ps1` imprime `[ok] deployed` con el PBO viejo (comprueba existencia, no
  novedad). Propuesta de Vaciado: sha256 antes/después en `Invoke-Build`, parsear «Build failed», preflight DayZDiag vivo.
  Plantilla del Pack `skills/dayz-test-ingame/templates/dayz-test.ps1:537-542` + **17 copias** en `<Mod>_dev/tools/`:
  el alcance (plantilla sola o plantilla + copias) es de Guillermo.
- `fb-20260902-202543-2bd3` — al registro de launchers le falta la transición `replace` in situ
  (`launcher_registry_update.py`): persistencia encadenada → R9.
- `fb-20260903-143856-df93` — la ruta `secure_launcher` no hace el preflight de VPP (mods, permisos,
  `vppDisablePassword`) que sí hace `dayz-test.ps1`: worker empaquetado + in-game.

### R4 — In-game / Enforce (9): exigen juego + Guillermo; Reserva PREPARA los gates, no los cierra sin juego
Hojas de las fichas numeradas: `plans/inbox-20260830/NN-fb-….md` (línea `Disposición:` y `OWNS:` son el oráculo).
- `01 fb-20260828-211445-3bb4` — ESC headless. En HEAD existe `key_press(dik)` (`server.py:4113`: entrega un DIK a
  `Mission.OnKeyPress`, ESC = 1; «no es input de SO»). Reconciliar la hoja 01 contra esa tool: si cubre el gate «ESC cierra
  el panel», el cierre es un gate in-game; si el panel no pasa por `OnKeyPress`, falta el camino y sigue abierta.
- `15 fb-20260829-104543-47c9` — petición de prioridad sobre f6ac (`ui_click` en ScriptViews). El arreglo real llegó
  por f4f2/M05 (`117730b`) y la capa Python por el lote C (`7670703`): sólo falta el gate in-game.
- `29 fb-20260829-230535-f4f2` y `33 fb-20260830-112422-2762` — YA EN FUENTE (M05 `117730b`: eco `ui_request`, cerco por
  `root`, `ambiguous_path`/`widget_not_found`). Gate: PBO desplegado + `ui_click` sobre `BtnCloseX` con y sin `root`.
- `25 fb-20260829-184952-20be` — tres huecos de diseño de `ui_click` (ámbito global, sin down/up, walk que no burbujea): Enforce.
- `30 fb-20260830-002237-0de3` — `vehicle_telemetry` `found:0/seated:0` con el jugador sentado (M04). Aviso del LIVE-STATE:
  `test_vehicle_telemetry_contract.py:280-287` exige el cast temprano que la ficha manda quitar: ese gate no puede ponerse rojo.
- `34 fb-20260830-112438-40e4` — `entities_query` no expone si la entidad tiene cargo (`MCPMessages.c:353-364`): Enforce.
- `fb-20260903-125244-4f83` — `restore_gameplay` devuelve `ok:1` incondicional (Enforce + in-game).
- `fb-20260902-194845-bd90` — centinela de fichero entero sobre `MCPBridge.c` (`test_task9_spawn_phase_markers.py`): se
  recongela DESPUÉS del canario in-game; hoy son los 2 rojos «por diseño» de la suite completa.
  También esperan juego (fuera de las 24 por tener resolución parcial): `05 8f8c` puntos 1 y 4, `28 b2c4` (gate de un
  `not_handled` real), `32 668f` (una vez decidido), y el gate in-game de `16 4d66` y del lote J (LFQuad2 en cola).

## Mandato vigente de Guillermo (verbatim) y restricciones medidas hoy

> «continua con los tickets, hay que acabar de resolver todo intentando construir una infraestructura optima de cara a
> futuro, no solo cerrarlos a corto plazo. Usa a grok cursor como worker para todo lo que necesites y descargarte al
> maximo contexto y uso de tokens, chatgpt codex como revisor, opus luego.»

- Grok CLI: **402 saldo agotado** desde las 04:57. Cursor `cursor-agent` (Composer 2.5 o Grok 4.6 High) entrega completo pero
  **A CIEGAS** (los pre-hooks PowerShell del host bloquean su shell): presupuestar una ronda del orquestador. Nunca Cursor
  para modelos Anthropic ni OpenAI. Harness listo: `tools/lote_harness/` (montar_ws.sh, preparar_ronda.py, runner_cursor.sh,
  watch.sh, recibir.py, review_codex.sh, integrar.py; ledger `GATES.md` de ejemplo en `reviews/2026-09-04-lotes-G-H/lote-J/`).
- Codex (`codex exec`, gpt-5.6-sol): revisor ciego en sesión NUEVA; ~84 % de la ventana semanal gastado (reset 2026-09-07).
  Tope de rondas y criterio de parada: `gates-ledger` §Cuándo para un bucle (ronda 3 última; MEDIA/BAJA → backlog).
- Todo lo delegado se revisa por OTRA familia. Un gate se calibra en ROJO antes de cada ronda; controles negativos con
  control positivo (LL-378); el recorrido de extremo a extremo empieza en la superficie que conduce el usuario (LL-452).
- Suite completa MEDIDA nombrando intérprete: `cd tools && ./.venv-mcp/Scripts/python.exe -m unittest discover -s tests -t .`
  (HEAD `155e5b0`: `Ran 2570`, sólo los 2 centinelas de bd90; intermitentes bajo carga: `test_wait_for_marker…lookback`,
  `test_bug046…cross_port_publish`, verdes en solitario). G-PYZ si se toca un módulo empaquetado.
- Cierre de ficha: `pipeline_resolve(feedback_id, resolution ≤2000 chars, evidence_ref ≤240)` citando commit + suite +
  pyz + dictamen del revisor; append-only, nunca «EN PARTE» sin decir qué queda.
- Git: stage por rutas exactas, `git diff --cached` entero, commit por pathspec; nunca `git add -A`/`.`. OneDrive: una
  escritura + read-after-write (el verificador es el Read tool); JSON/YAML por Python. Lease `dayz-mcp` antes de mutar o
  gestionar procesos, nunca matar DayZ a mano (lifecycle guard); `session_status` antes de cada handoff.

## Fichas nuevas tras el reparto (16:50-17:00) — todas a Reserva

- `fb-20260904-144835-01ae` — anexo de 4407 (evidencia de LFQuad3/LFQuad2; ver R1).
- `fb-20260904-145230-6d22` (LFQuad2, bug) — cliente sin render; `capture_screenshot` detecta el negro pero no el CONGELADO
  (fotograma viejo servido como bueno). Va con 8f76: parte (a) = declarar el congelado en la capa Python (offline); el meta
  `[Image, meta]` con `frame_sha256` existe desde el lote M (`e603c16`) y LFQuad2 no lo ve por 9b7b (cliente registrado a
  las 01:39, antes del despliegue).
- `fb-20260904-145528-3f5a` (request, archivada por esta sesión con el dato de LFQuad2) — `session_acquire_wait` con lease
  propio vivo devuelve `session_transition_conflict` y el agente no sabe si el lease caducó: (a) idempotencia por sesión o
  (b) `expires_in_s` en `session_status.self` + descripción. Decisión de contrato de Guillermo (R3), junto a la renovación
  automática del lease. Ficheros cuando se decida: `loopback.py`, `session_coordination.py`; oráculo HTTP como el del lote J.

**Gate in-game del lote J: VERDE** (LFQuad2, ~17:00, daemon `4b615aba`, run `94953d2b`): `adopted_run` ok, `entities_query`
verde, `run_not_owned` desaparecido de `fence.mutation_rejects_by_code`, siete mutaciones sin rechazo, sin reabrir el cliente
MCP, readopción sola tras dos caducidades del lease. Registrado en las resoluciones de f70b y 5ca1. Buzón: 27 sin
resolución (Reserva 25, Vaciado 2). Caja libre desde ~17:00.
