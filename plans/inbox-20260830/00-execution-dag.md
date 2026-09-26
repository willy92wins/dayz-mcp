# Grafo de ejecución modular — cierre de 35 feedbacks

Fecha: 2026-08-31  
Estado: PLAN CODEX v6; pendiente de revisión única Grok 4.6 Medium mediante Cursor  
Graph version: `inbox-20260831-v6`  
Research: `research/2026-08-30-pipeline-inbox-triage-codex.md`  
Diseño aprobado: `plans/2026-08-30-pipeline-inbox-closure-design.md`

## Regla de autoridad

Cada feedback conserva plan, hash y veredicto individual. Los módulos solo agrupan escritura. Un
PASS de módulo no cierra automáticamente ninguna ficha y una revisión agrupada sin un bloque por ID
es inválida.

## Routing `delegar`

| Campo | Decisión |
|---|---|
| Decisión | delegar juicio, mantener autoría e integración en Codex |
| Rol | Grok 4.6 Medium, único revisor formal de plan y de implementación |
| Modelo × ruta | `cursor-grok-4.6-medium × cursor-agent` |
| Estado | WORKS medido 2026-08-31 en Cursor Ultra; Sonnet eliminado y Opus supersedido por la última decisión expresa del usuario |
| Boundaries | sesiones nuevas, solo lectura, máximo cinco fichas, sin MCP ni procesos DayZ; prohibidos `reviews/**`, `gates/**`, evidencia histórica y walks recursivos amplios |
| Artefacto | veredicto individual contra hash exacto; evento `init` acredita modelo/sesión y `result` acredita `success,is_error=false` |
| Independencia | la revisión de implementación usa una sesión Grok/Cursor nueva y no recibe el razonamiento de la revisión de plan; sólo autoridades, plan aprobado y bytes/evidencia finales |

## Propiedad y dependencias

`OWNS` es exclusividad física durante una ventana, no propiedad permanente. Dos módulos que nombren
el mismo fichero se ejecutan en serie por una arista explícita. Los nuevos nombres quedan reservados
aquí para que dos subagentes no creen soluciones paralelas incompatibles.

| Módulo | Feedbacks principales | OWNS durante su ventana | Depende de |
|---|---|---|---|
| M00-DIRTY-AUDIT | 3bb4, 7743, 1082 | crea sólo `reports/2026-08-30-inbox-implementation/M00/{m00-git-status-v2.raw,m00-git-inventory-v2.jsonl,m00-owns-baseline.sha256,m00-dirty-audit.json}`; lee los tests/targets cerrados y registra dentro de esos cuatro informes el recibo por ficha; **sin source** | M01 |
| M01-CONTRACT-FREEZE | los 35 | `product-spec.md`, diseño, este DAG, `physical-ownership-addendum-v1.md`, `authority-bundle-v7.sha256`, los 35 planes, manifiesto, manifiesto histórico Sonnet, transición de revisor y revisión/manifiesto Grok/Cursor de plan, `GATES.md`, hojas/títulos y attestations deterministas de planificación | — |
| M02-MESSAGES-CONTRACT | 40e4, 0de3, UI | `addon/scripts/5_Mission/MCPMessages.c`, test contractual nuevo | M00 |
| M03-ENTITIES | 40e4, 8f8c capabilities server | `addon/scripts/5_Mission/MCPBridge.c`, `test_entities_has_cargo.py`, `test_bridge_server_capabilities.py` | M02 |
| M04-VEHICLE-TELEMETRY | 0de3 | región telemetría de `MCPClientBridge.c`, `test_vehicle_telemetry_contract.py` | M00 |
| M05-UI-ENFORCE | f6ac, 47c9, 21f5, 20be, f4f2, 2762, mitad 7743, 8f8c capabilities client | regiones UI/capability poll de `MCPClientBridge.c`, tests Enforce UI y `test_bridge_client_capabilities.py` | M02,M04 |
| M06-UI-INGRESS | b2c4, schema UI y 8f8c capability poll | `tools/dayz_mcp/loopback.py`, tests de ingress/errores UI/capabilities | M00 |
| M07-CAPTURE | 268a | `tools/mcp_capture.py`, `tools/tests/test_mcp_capture.py` | M00 |
| M08-INBOX | c7ca | `tools/dayz_mcp/inbox.py`, `tools/tests/test_pipeline_feedback.py` | M00 |
| M09-KNOWLEDGE | 782b | `tools/dayz_mcp/knowledge.py`, tests Knowledge | M00 |
| M10-PBO-FRESHNESS | 668f | `tools/pbo_freshness.py`, sus tests, `C:\Users\guill\.agents\skills\dayz-test-ingame\templates\dayz-test.ps1`, `C:\Users\guill\.claude\skills\dayz-test-ingame\templates\dayz-test.ps1`, la copia generada `P:\LFQuad2_dev\tools\dayz-test.ps1`, verificador source→staged-PBO y harness RED→GREEN; ningún `SKILL.md`. Si la implementación demuestra que el contrato documental debe cambiar: INCONCLUSIVE y replan con revisión Grok/Cursor nueva | M00 + TDD de skill |
| M11-LFPG-VERSION | 344d y preflight LFPG de 3bb4/f6ac/7743/21f5/1082 | `P:\LFPowerGrid_dev\tools\build_guarded.py`, `P:\LFPowerGrid_dev\tests\test_version_gate.py` y evidencia M11; `P:\LFPowerGrid\config.cpp`, `P:\LFPowerGrid\scripts\3_Game\LFPG_Defines.c`, `P:\LFPowerGrid_dev\tools\verify_corrective.py` y `P:\Mods\@LFPowerGrid\Addons\LFPowerGrid.pbo` son read-only. Cualquier drift: INCONCLUSIVE y replan; nunca se edita source/PBO desde este módulo | M00 |
| M12-MODE-AUTHORITY | 9d46, ffc7 | nuevo `tools/dayz_mcp/dayz_test_modes.py`, tests propios | M00 |
| M13-EFFECTIVE-SCHEMA-CORE | f201, 141e, d366 | nuevos `effective_schema.py`, `effective_schema_catalog.py`, `effective_schema_runtime_validators.py`, tests y fixtures v1/v5 independientes (incluidas identidad registry e instructions); no `server.py` | M12 |
| M14-REGISTRY-FINGERPRINT | 9b7b, 103f | nuevo `tools/dayz_mcp/tool_registry_fingerprint.py`, tests propios | M13 |
| M15-STORAGE-CORE | 4407 | nuevo `tools/dayz_mcp/dayz_test_storage.py`, tests filesystem | M00 |
| M16-STEAM-PREFLIGHT | a396 | nuevo `tools/dayz_mcp/steam_preflight.py`, tests registro/proceso | M00 |
| M17-LIFECYCLE-STATUS | 4d66, cc2d | `tools/dayz_mcp/process_lifecycle.py`, inyección acotada de generation en `tools/dayz_mcp/daemon.py`, hook estrecho `run_command_activity` fuera del lock en `tools/dayz_mcp/loopback.py` **después de M06**, lookup del audit JSONL existente, tests lifecycle/ocupación | M00,M06 |
| M18-REQUEST-CONTRACT | 8f8c, 7c88, ffc7/9d46 | `tools/dayz_mcp/dayz_test_request.py`, tests request; consume `request_path_authority.py` sólo read-only. Un hallazgo que exija editarlo produce INCONCLUSIVE y replan con revisión Grok/Cursor nueva | M12 |
| M19-TOOL-READINESS | 8f8c, 7c88, 4d66, a396, cc2d, ffc7/9d46 | `dayz_test_tool.py` excepto terminal, `dayz_test_readiness.py`, tests tool/readiness; consume Steam y diagnósticos lifecycle | M16,M17,M18 |
| M20-WORKER-STORAGE | 7c88, 4407, ffc7/9d46 | `tools/dayz_mcp/dayz_test_worker.py`, `tools/native-launchers/dayz-test-v1/src/app_main.py`, ambas allowlists en `tools/build_native_launcher.py` y `tools/dayz_mcp/native_bundle.py`, hash/manifest de `dayz_test_modes.py` y `dayz_test_readiness.py`, rebuild/reaprobación y tests worker/bundle/registry; lee la entrega M19, no la modifica | M15,M18,M19 |
| M21-TERMINAL-INTEGRATION | 7c88, 4407 | terminal/resultados en `dayz_test_tool.py`, tests terminal | M19,M20 |
| M22-SERVER-INTEGRATION | toda superficie pública, instructions, BUG-086 y comparación capabilities 8f8c | **único dueño** de `tools/dayz_mcp/server.py`; tests públicos y fixture `bridge_capabilities_v1.json` | M03,M05,M06,M07,M08,M09,M12,M13,M14,M17,M21 |
| M23-FINAL-SCHEMA-PROMOTION | f201, 141e, d366, ffc7/9d46, 9b7b/103f | `tools/promote_effective_schema.py`, su test y los canónicos cerrados bajo `reports/2026-08-30-inbox-implementation/M23/`; sin source compartido | M20,M22 |
| M24-PBO-BUILD-DEPLOY | cambios Enforce | source `P:\DayZ_MCP_dev\addon`, temp `%TEMP%\dayz-mcp-m24\<txid>`, candidate/backup create-only y único publish final `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo`; sin source | M05,M10,M22 |
| M25-EVIDENCE-CLOSE | los 35 | gates in-game/offline, delivery-manifest por ID, revisión/manifiesto Grok/Cursor de bytes finales, `GATES.md`/hojas/attestations de cierre y resoluciones inbox; sin source | M00,M11,M23,M24 |

## Olas ejecutables

```text
Ola 0   M01→M00
Ola 1   M02 ∥ M04 ∥ M06 ∥ M07 ∥ M08 ∥ M09 ∥ M10 ∥ M11 ∥ M12 ∥ M15 ∥ M16
Ola 2a  M02→M03 ; M06→M17 ; M12→(M13 ∥ M18)
Ola 2b  (M02+M04)→M05 ; M13→M14 ; (M16+M17+M18)→M19
Ola 2c  (M15+M18+M19)→M20
Ola 2d  (M19+M20)→M21
Ola 3   (M03+M05+M06+M07+M08+M09+M12+M13+M14+M17+M21)→M22
Ola 4   M22→M23 ; (M05+M10+M22)→M24
Ola 5   (M00+M11+M23+M24)→M25
```

La paralelización se hace entre módulos sin intersección física. M04 y M05 comparten
`MCPClientBridge.c`, por eso telemetría termina antes de UI. M19 y M21 comparten
`dayz_test_tool.py`, por eso el terminal se integra al final. El fingerprint final se genera en
M23 contra la app de M22 y el bundle de M20; M13 solo construye el extractor, el catálogo y sus
mutantes. M20 espera M19 porque empaqueta y hashea `dayz_test_readiness.py`; nunca reconstruye una
versión paralela de ese módulo.

## Interfaces fijadas

### Enforce y observabilidad

- `entities_query.entities[*].has_cargo` significa capacidad (`EntityAI.GetInventory()!=null` y
  `GetCargo()!=null`), no ocupación ni lista de classnames.
- `vehicle_telemetry` no cambia `ResolveOwnedCar`. Su resolvedor local usa el transporte real y
  rellena los campos existentes `found`, `seated`, `seat`, `type`, `classname` antes de intentar
  métricas `CarScript`. Sin transporte devuelve `ok=true,found=false,seated=false` sin error;
  con transporte, `found=true`, `seated=(CrewMemberIndex>=0)` y un tipo de asiento
  no reconocido produce `seat="unknown"` sin convertir `seated` a false. `seat` conserva strings
  nombrados, nunca `"0"`. Los gates incluyen un `Transport` no-`CarScript` y el estado con
  transporte presente pero `CrewMemberIndex=-1`; un coche no discrimina el orden del cast.
- M02 declara de una vez los campos DTO que consumen M03/M04/M05. Ningún productor paralelo edita
  `MCPMessages.c`.

### UI

- Los verbos públicos son exactamente `ui_tree`, `ui_set_text`, `ui_click` y `ui_focus`.
- `root` es opcional. Con `root`, su nombre debe tener un único match en el workspace y `path` se
  resuelve dentro de ese scope. Sin `root` y con `path`, la búsqueda se hace sobre el workspace
  completo, incluidos `ScriptView`; 0=`widget_not_found`, >1=`ambiguous_path`. Solo `ui_tree` con
  ambos vacíos conserva el active-menu legacy. Nunca se elige el primer homónimo.
- El eco público vive bajo el miembro podable `ref ui_request`; dentro usa los campos dedicados
  `requested_path`, `requested_root`, `requested_text` solo en set-text y `matched_path` solo tras
  match único. `handler` y `user_id` siguen siendo campos de primer nivel aplicables solo a click
  tras match único. Ningún verbo UI reutiliza `source`/`type` con otro sentido. `requested_path`
  conserva literalmente la entrada. La poda es consciente del verbo: conserva
  `requested_text=""` en set-text, lo elimina en los otros verbos y trata un sobre vacío en uno de
  los cuatro verbos como defecto observable, no como ruido. `matched_path` se deriva exclusivamente del
  `Widget` real tras un match único: recorrido root→leaf con `GetParent()`, un segmento
  `/<pct-name>@<ordinal>` por nodo, nombre codificado como bytes UTF-8 percent-encoded con hex
  uppercase fuera de RFC3986 unreserved (y siempre `%`, `/`, `@`), y ordinal decimal sin ceros a
  la izquierda, 0-based entre siblings con `GetName()` byte-exacto. La cadena siempre empieza en
  `WorkspaceWidget`, aunque el caller haya dado `root`; ese nodo superior usa ordinal `@0`.
  Nunca se rellena copiando el request ni existe dentro del sobre en ausencia/ambigüedad; en esas
  ramas tampoco se publican `handler`/`user_id`. Los fixtures contrastan scope root vs workspace y
  siblings homónimos.
- Errores de negocio se permiten por verbo: resolución ambigua/ausente en los cuatro;
  `text_not_writable` solo set-text; `no_handler`/`not_handled` solo click;
  `focus_not_taken` solo focus. Conservan payload `ok=0`; auth, bad args, cola, versión, timeout y
  readiness siguen como `ToolError` fail-closed en los cuatro transportes.
- `ui_click(mode="direct")` conserva el lookup legacy hasta el primer handler y su retorno. El modo
  `complete` se implementa solo después de un viability RED que discrimine coordenadas
  screen/local/zero; entrega `down→up→click` al centro medido. `bubble=true` continúa a handlers
  ancestro adicionales solo después de que el handler localizado decline.

### Schema, registry y modos

- Autoridad única `[DESIGN]`: tres modos públicos (`all`, `server`, `client`) y el modo interno
  `offline`; schema, texto, request, tool y worker derivan de esa autoridad. Un mutante por capa debe
  poner el gate rojo.
- El schema efectivo se extrae post-`build_app`, aliases y perfiles. El inventario esperado se
  deriva de una fuente independiente del extractor real; contar tools o comparar el output consigo
  mismo no acredita PASS.
- M13 separa constructor, catálogo, fixtures y enumerador runtime. Ninguno importa ni genera los
  otros. El enumerador observa la app post-build y adapters M22 que llaman boundaries reales; se
  exige igualdad triple catálogo=fixture=runtime. Un validator runtime nuevo no catalogado, una
  entrada sin adapter o un mutante que añade una rama real sin tocar catálogo/fixtures ponen rojo.
- El gate anti-snapshot genera en cada corrida, fuera del workspace candidato, un nombre,
  descripción, propiedad y validator-case aleatorios; los inyecta en la app/registry real y un
  oracle separado compara el registro canónico completo, no un subconjunto. La calibración incluye
  un candidato snapshot/deliberadamente inútil que debe quedar rojo. Se ejecuta con
  `PYTHONDONTWRITEBYTECODE=1` y cero `.pyc`, conforme LL-378
  (`C:/Users/guill/.agents/skills/gates-ledger/SKILL.md:89-115`).
- La identidad registry deriva sólo de `ServerConfig`: `profile=standard|exec_enforce` según
  `enable_exec_enforce`; `role=claude|codex` según `client_platform`. `profile_inventory.json`
  congela las cuatro parejas y su config positiva; valores desconocidos no se infieren de tools.
- M22 registra al final una tool pública 0-arg `dayz_effective_schema`. Su payload version 1 es un
  envelope con `schema_version` y cuatro `payloads[]` ordenados por `(profile,role)`; cada payload
  congela `profile`, `role`, `instructions`, `tools[]` y `tool_registry_fingerprint`; cada tool
  contiene `name`, `description`, `input_schema`, `public_constraints[]` y
  `effect_verification` (`wire` o `in_game_required`). Sólo se materializan constraints de
  validadores públicos; delegación al bridge nunca se certifica offline.
- Campos públicos congelados: `tool_registry_fingerprint`, `tool_registry_captured_at`,
  `tool_registry_source_stale` y `tool_registry_remediation="reopen_mcp_client"`. Son por proceso
  FastMCP/`ControlIdentity.session_id`, no por PID ni daemon. El snapshot incluye la pareja positiva
  `profile/role` de config y sólo se compara con el payload M23 único de la misma pareja; identidad,
  autoridad o cardinalidad desconocida produce null/unknown. Comparar dos llamadas al mismo
  helper/app no acredita frescura.
- La autoridad de modos es un módulo leaf incluido en ambas allowlists del launcher, en el manifest
  con hash propio y dentro de `app.pyz`. `await app.list_tools()` y un `tools/list` stdio real deben
  observar el enum público exacto; la tool cero-argumentos rechaza propiedades extra de verdad en la
  superficie wire. Bundle/verifier/manifest/registry se reconstruyen y reaprueban como una unidad
  fail-closed.

### Lifecycle y persistencia

- `mode=client` exige `run_id`, reusa ese run y conserva el mismo ID incluso en terminal de error.
  `server/all` no aceptan `run_id`. Los códigos públicos son
  `bad_dayz_test_request:client_requires_run_id`,
  `bad_dayz_test_request:server_all_forbid_run_id`, `bad_run_id`, `run_not_found`,
  `run_not_extensible`, `run_project_mismatch` y `run_not_adoptable` según el boundary.
- Steam client-mode pasa solo si `ActiveUser != 0`, el PID registrado existe y su imagen es
  `steam.exe`; lectura, identidad o permisos ambiguos devuelven `steam_session_stale` antes de
  lanzar. El error incluye `steam_registered_pid`, `steam_live_pids` acotado y
  `remediation="restart_steam_and_wait_for_active_process_match"`, nunca usuario/path completo.
  PID vivo no equivale a readiness.
- `wait_for` solo reintenta el error público estructurado
  `game_not_ready:reason=server_poll_stale` dentro de M22 y del deadline original; no exige un
  `run_id` que su firma no recibe. `no_run` por fallo de snapshot, `client_not_polling` en la vía
  server actual, incompatibilidad, versión, auth, argumentos y transporte ambiguo fallan pronto.
  El deadline del transporte debe ser ≥ `timeout_s` aceptado o el valor se rechaza antes de enviar.
- La ficha 8f8c se cierra completa: `action_use.action` se documenta como classname Enforce, no
  texto visible; `entities_query` devuelve `reason="no_player_connected"` sólo cuando la lista de
  jugadores acreditada está vacía; y la señal registry stale procede de M14/M23.
- Su BONUS es independiente de P11: ambos peers Enforce anuncian en cada poll una lista canónica y
  acotada de comandos desde sus dispatchers; M06 conserva sólo el último anuncio acreditado de la
  generación. M22 lo cruza con `await app.list_tools()` mediante la fixture externa
  `bridge_capabilities_v1.json` y publica por peer `match|mismatch|unknown` más diferencias. Anuncio
  legacy/ausente/malformado es unknown, nunca match; expected no deriva del anuncio, de las listas de
  loopback ni de la app observada.
- Ocupación publica edad del run mediante `_run_age_s`. M17 usa `lifecycle_start_outcome` con
  `decision=started,state=RUNNING` como actividad basal y registra `run_command_activity` sólo
  después de aceptar un comando dirigido a un binding acreditado. Captura run_id/comando bajo lock,
  escribe fuera con `reason="accepted",decision="accepted",duration_s=0.0`, cubre normal+exec y
  degrada la fuente a unknown si el writer falla. `last_activity_age_s<=900`=`recent`, `>900`=
  `stale`; timestamp futuro/ilegible/ambiguo=`unknown`. `active_run_exists` transporta ambos sin preemptar. Generation de lanzamiento/retiro se deriva del
  audit JSONL ya existente y sus backups acotados; run ausente sin evidencia queda unknown. Nunca
  preempta por edad/inactividad ni conserva leases/tickets que H2/H5/H12 invalidan.
- Modset canónico conserva orden dentro de `base_mods`, mod principal, `extra_mods` y
  `server_mods`. El árbol es `<mission>/storage_1` y el sidecar sibling; mission ya pasó roots
  sellados y un señuelo `<profiles>/storage_1` nunca se toca. La matriz distingue árbol ausente,
  marker huérfano, legacy, match y mismatch; sólo apartar un árbol real pone `storage_rotated=true`.
  El digest del árbol usa manifest canónico recursivo (rutas POSIX/tipo; tamaño+SHA para ficheros),
  sin seguir symlinks/reparse points. Un journal durable por fases precede renames; antes de A–F,
  un pre-pass compensa `prepared|storage_moved|marker_moved`, finaliza `marker_published` o bloquea
  si hay estado múltiple/malformado/no convergente. Sólo journal activo y sidecar actual admiten
  atomic-replace con preimagen acreditada; storage/backups nunca overwrite/delete. El sidecar
  versionado conserva `rotation_pending` hasta acreditar storage nuevo, por lo que una caída
  no pierde el aviso. El hook sólo corre al crear server/all/offline nuevo, nunca client/kill/
  preflight/build. Colisión/rename/sidecar fallan cerrado y conservan todos los bytes. El terminal
  añade `storage_rotated`, `storage_backup` y el backup de
  marker cuando exista; la respuesta pública deriva
  además `storage_reset_notice="mission_world_and_character_reset"` cuando rota. Launcher y parser
  cambian en la misma cadena M20→M21. Rollback nunca borra: aparta el storage nuevo y restaura un
  backup exacto.

### Datos locales, captura y build

- `crop_space=window` conserva legacy; `client` es el nuevo default. Se persiste el client rect del
  frame elegido antes del downscale; crop vacío devuelve el client area completo. Rect ausente o
  inválido y crop adicional inválido fallan cerrado y nunca devuelven chrome. La evidencia distingue
  `window_surface`, `client_surface` y `effective_surface`: hashes/estadísticas nombran la superficie
  explícita, `fullres` sigue la efectiva y la ruta pública default expone metadatos suficientes para
  probar con un viewport coloreado que no se incluyó chrome.
- Inbox sigue JSONL append-only. `evidence_ref` es ASCII 1..240, relativo bajo
  `reviews/|gates/|reports/|research/`, sin segmentos vacíos, `.`/`..`, raíz, drive ni URI. `age_s`
  y `age_label` (`s/m/h/d`, floor) se derivan; timestamp futuro/inválido produce `null` más razón.
  La última resolución gana y una resolución sin ref limpia la ref anterior. El gate cruza la vía
  pública completa `pipeline_resolve → append JSONL → pipeline_inbox`, prueba last-wins y calcula edad
  desde el timestamp original, no desde el evento de resolución.
- Knowledge prepare/status no acepta path de usuario ni red/git. Solo extrae del pack instalado a
  temporal, valida schema y entries no vacíos con `load_index`, y reemplaza atómicamente; fallo
  conserva el índice previo. Estado de índice y estado del pack instalado son ejes independientes;
  los seis estados atómicos (`missing|invalid|valid` por eje) forman una matriz cartesiana de nueve
  celdas que no colapsa «índice utilizable» con «pack disponible». La firma pública cero-argumentos
  rechaza inputs extra en el wire real, no sólo en una llamada Python directa.
- PBO se construye fuera del destino, se valida por fatal-log y procedencia source→staging→entrada.
  PackOnly exige set+SHA exacto del source desempaquetado; build binarizado compara scripts/config
  contra source y payload binario contra el temp de esa corrida. Tokens/prefix solos no acreditan.
  Se publica por reemplazo sibling. Un rebuild determinista puede tener el mismo SHA: no se exige
  hash/mtime distintos. Fallo deja el PBO publicado byte-idéntico.
- M23 publica `reports/2026-08-30-inbox-implementation/M23/dayz-effective-schema-v1.json` sólo
  después de M20+M22: target y productores se capturan antes, banco/app/wire se ejecutan,
  productores y preimagen se rehashean, backup es create-only y un único `os.replace` promueve el
  candidate validado. El schema final es el commit marker; candidate, backup, journal y receipt nunca
  son autoridad pública. Drift, colisión o recuperación no reacreditada bloquean.

## Gate de ausencia de solape

Antes de lanzar dos implementadores se materializa el inventario físico vinculante de
`physical-ownership-addendum-v1.md`: ruta absoluta/real, alias y enlaces resueltos, intención
`read|create|modify|publish`, y solape fichero-fichero o padre-hijo. Cualquier intersección de
escritura = serializar. M00 toma dos capturas quiescentes consecutivas; sólo A=B congela el baseline.
Cada subagente recibe un solo módulo, no el paquete completo. Al entregar, devuelve
`git diff -- <OWNS>`, tests RED/GREEN y hashes; el integrador reabre cada cita y rehashea los
productores. Drift concurrente, ruta no resoluble o write-set nuevo produce INCONCLUSIVE, no merge
a ciegas.

## Viability global

- PASS: las 35 fichas tienen hash actual, PASS Grok/Cursor de plan, materialización/evidencia, PASS Grok/Cursor
  de implementación y resolución durable; el inbox queda a cero y todas las puertas vivas pasan.
- FAIL: cambia un hash aprobado, un gate acepta su mutante/predecesor, dos módulos escriben el mismo
  fichero a la vez, se confunde proceso con readiness o una ficha se cierra por el PASS de otra.
- INCONCLUSIVE: provider/model/stopReason incorrecto, drift de bytes, PBO/build no acreditado,
  fixture que no alcanza el mecanismo o entorno sin el discriminador necesario.
