# Triaje del buzon: las 64 fichas abiertas (2026-09-21)

Sesion `741dc585`, que otras sesiones direccionan como «MCP project review y estado». El buzon
acumulaba **64 fichas sin resolver de 652 historicas**. Este documento es la evidencia de por que
se cierran 40 de ellas y por que las demas siguen abiertas.

La pregunta de partida no era «que falta por arreglar» sino **«que esta arreglado y nadie cerro»**.

## Metodo: tres canales de evidencia

Una ficha solo se cierra si al menos uno de estos canales la respalda, y la resolucion dice cual:

- **A — el historial de git nombra el id.** La rama que cierra la ficha suele llevar su short id
  (`fix/fb-1025-packaged-modules-lock`). Mecanico y barato.
- **B — el arbol versionado cita el id.** El que aplico el arreglo dejo el id en un comentario, un
  docstring, un test o el CHANGELOG. **Es el canal mas fiable de los tres** y el que menos se mira:
  17 fichas estan nombradas dentro del arbol, y seis de ellas no aparecian por el canal A.
- **C — comportamiento observado en vivo.** La respuesta real de la herramienta hoy, no el diff.
  Cinco fichas se cerraron asi, y es la unica evidencia que no depende de que el commit hiciera lo
  que su asunto promete.

### La trampa del canal A, medida

Un short id de 4 hex **puede aparecer dentro del sha de 7 hex** que abre la linea de un commit:
`d6ac586` contiene `c586`, `7785edf` contiene `5edf`, `e951956` contiene `e951`. Una busqueda por
subcadena da tres falsos positivos en este mismo corpus. El match solo vale si el id aparece en un
nombre de rama o como token del asunto: `(?<![0-9a-f])<id>(?![0-9a-f])`.

## El triaje delegado, y donde llega su techo

La primera pasada la hizo `Qwen/Qwen3.8-Flash-Next` en el **GX10** por `prime-agent`, aislado
(`-nc -ns --no-session`, workspace de dos ficheros, sin acceso al arbol). Coste 0, ~10 minutos,
identidad comprobada en el stream (`provider:"gx10"` y el modelo correcto en las 189 apariciones),
cierre `stopReason:"stop"`. Su artefacto integro esta en `TRIAGE-GX10.md`.

Lo que aporto y lo que no, porque condiciona como usar esta lane la proxima vez:

- **Acerto en lo semantico**: las familias de duplicados y las diez fichas que no son del MCP sino
  del arnes del agente. Eso es lectura y juicio, y salio limpio.
- **Fallo en lo mecanico**: de las 16 fichas cuya rama nombra el id, solo marco 7. Se dejo `1025`,
  cuya rama era el ejemplo literal que llevaba el brief.
- **No invento evidencia.** El receptor comprueba que cada sha citado exista de verdad en el
  corpus: ese control no salto ni una vez. Si saltaron 5 etiquetas mal puestas (coincidencia de
  sintoma declarada como coincidencia de id), que es un error de grado, no de honestidad.

Conclusion operativa: **a esta lane se le delega el agrupamiento, no el emparejamiento exacto**;
lo mecanico lo hace un grep bien escrito, que ademas no se equivoca.

## Resultado


| estado | n |
|---|---|
| Se cierran: arregladas con evidencia | **33** |
| Se cierran: mismo sintoma que una ya arreglada | **7** |
| Siguen abiertas, del MCP | **11** |
| No son del MCP: arnes del agente o host | **10** |
| De otros proyectos | **3** |

Suma: 64 de 64.

## Se cierran: arregladas con evidencia (33)

| ficha | proyecto | titulo | canal | evidencia | nota |
|---|---|---|---|---|---|
| `4f66` | DayZ_MCP | dayz-test-v1 bundle: only dayz_test_worker_sha256 is stale after commit d72a73 | hoy | idem 2669; los 4 pines casan |  |
| `8620` | DayZ_MCP | dayz_test_run blocked: native launcher closure-manifest older than edited work | hoy | idem 2669 |  |
| `2669` | SUB_BRZ | dayz_test_run: invalid_native_launcher_bundle while server code is stale | hoy | resellado 17:46; audit lifecycle_start_outcome 19:15:52Z |  |
| `db93` | SUB_BRZ | dayz-mcp dropped mid-session and came back with 18 tools instead of 46 | C+B | server.py:627; esta sesion ve 18 tools sin lease | no es regresion: es el PR #80 |
| `744a` | DayZ_MCP | dayz_test_stop run_stop_failed/cleanup_degraded tras C1 PASS (smoke E98B7FBD) | A+B | PR #86 fix/744a-stop-cleanup-gone; dayz_test_worker.py:415 |  |
| `632e` | LFPowerGrid | dayz_test_stop devuelve run_not_adoptable en una run recién arrancada; acquire | A+B | PR #87 fix/632e-stop-adoptable; loopback.py:3297 |  |
| `c0e5` | DayZ_MCP | Gate pre-run: desktop desbloqueado + sonda de captura antes de la tanda (evita | B | CHANGELOG.md:17; dayz_test_tool.py:37; mcp_capture.py:933 |  |
| `e007` | DayZ_MCP | mcp.list_tools McpStartupError (vsock) desde el host del agente: obliga a puen | A | PR #89 cursor/fix-e007-vsock-stdio-docs | usado hoy por esta sesion |
| `05a0` | DayZ_MCP | capture_screenshot frame_client_all_black con desktop del host bloqueado/suspe | B | CHANGELOG.md:17; mcp_capture.py:933 |  |
| `d0e0` | DayZ_MCP | session_release returns bare "lease_invalid" after silent lease expiry: wrong  | B | CHANGELOG.md:34; control_client.py:674; test_client_mode.py:443 |  |
| `f18d` | DayZ_MCP | Knowledge pack missing with no install path exposed; no download tool; two dif | C | dayz_knowledge_status publica install_command y dos reasons |  |
| `e5cb` | DayZ_MCP | notify_players reports sent:1 with zero connected players (query_all_players f | git | d6ac586 + test_mcp_tools.py (+39) | no verificado en vivo: pide juego |
| `b286` | DayZ_MCP | object_inspect: omitting `type` fails with "type '' must be a non-empty string | codigo | server.py:5933 error nuevo missing target parameters |  |
| `5edf` | DayZ_MCP | ready.reason=server_poll_stale is wrong after a fresh launch: reports the stal | B | CHANGELOG.md:31; loopback.py:1088; server.py:579 |  |
| `26ac` | DayZ_MCP | session_status foreign_ports has no DayZ-relevance flag, so occupied=false is  | C+git | session_status: dayz_relevant por puerto; ec55b3b |  |
| `f8e0` | DayZ_MCP | ready.reason=server_poll_stale omits the age threshold, so stale-vs-down canno | C | bridge_status.ready trae stale_threshold_s=15 |  |
| `8011` | DayZ_MCP | knowledge_find error text prescribes a prepare step that cannot succeed when p | B | CHANGELOG.md:35; knowledge.py:640 cita la ficha |  |
| `1bcb` | DayZ_MCP | Tool responses omit next_step hints, leaving small models with no follow-up ac | A+C | PR #81; session_status termina en next_step |  |
| `baf9` | DayZ_MCP | World tools: fail-fast not_ready envelope when bridge ready=false | B | server.py:1038 cita 1765/baf9 |  |
| `2ad1` | DayZ_MCP | Progressive tool disclosure for free/local models (~56KB catalog) | A+B | PR #80; server.py:627; test_progressive_disclosure.py |  |
| `0505` | DayZ_MCP | knowledge: can_prepare=false but prepare still callable | codigo+C | knowledge.py:695-701 rechaza con next_step; can_prepare=false |  |
| `1765` | DayZ_MCP | Unify not_ready language: bridge vs world tools | A+B | PR #82; server.py:1038 |  |
| `2492` | DayZ_MCP | bridge_status: put ready/reason/next_step first in response | C | bridge_status abre por ready (primera clave) |  |
| `b139` | DayZ_MCP | Error text tells you to call lifecycle_status, but that tool is not in the exp | codigo | agent_loop.py:1-14 PUBLIC_NEXT_TOOLS |  |
| `00bb` | DayZ_MCP | player_teleport con el cliente cerrado: timeout de vehicle_telemetry a los 15  | A+B | PR #76; peer_liveness.py:5 |  |
| `1004` | DayZ_MCP | object_delete con el cliente sentado deja el render en negro: NULL en la cámar | A+B | PR #76; test_fb_00bb_1004.py |  |
| `63c9` | DayZ_MCP | pack-addon.ps1 con -packonly ignora include.lst: empaqueta los .bak_* y CLAUDE | A+B | PR #62; test_pack_addon_staging.py:3 |  |
| `b0d9` | DayZ_MCP | f47b reproducido (PBO 7DE421C5): restore_gameplay devuelve ok y camera_release | A | PR #65 restore-honest-frozen-signal | decision del dueno 21-09: entrego la senal honesta |
| `1025` | DayZ_MCP | Rollout del bundle nativo: dos ventanas en las que dayz_test_run falla para to | A | PR #61 fix/fb-1025-packaged-modules-lock |  |
| `1f21` | DayZ_MCP | La remediacion de Steam da OK mirando la CLAVE, no si Steam sirve: el cliente  | A | PR #64 fix/fb-1f21-steam-guard-calibration |  |
| `3fc1` | LFPowerGrid | action_use no completa acciones continuas (barra de progreso): bloquea probar  | A | PR #70 fix/fb-3fc1-action-use-target |  |
| `f298` | DayZ_MCP | Al unirse a la partida, el cliente lanzado por MCP captura el raton del usuari | A | PR #59 launch-no-activate | decision del dueno 21-09: el sintoma vive en fade/75e7 |
| `dae1` | DayZ_MCP | Backlog lifecycle preexistente (R9 d60f/8f76c): reaper entre adopt y stop, cli | A+B | PR #20; dayz_test_tool.py:1220 |  |

## Se cierran: mismo sintoma que una ya arreglada (7)

| ficha | proyecto | titulo | canal | evidencia | nota |
|---|---|---|---|---|---|
| `e951` | DayZ_MCP | Flash-Next real-MCP gauntlet: 5 ok / 3 err; no game client | - | informe paraguas del gauntlet; sus hijos ya estan fichados |  |
| `5bde` | DayZ_MCP | Errors cite lifecycle_status when tool not in exposed registry | - | canonica b139 (ya arreglada) |  |
| `69d9` | DayZ_MCP | When ready=false, world tools should return a short structured not_ready envel | - | canonica baf9 (ya arreglada) |  |
| `51c9` | DayZ_MCP | Full tool catalog is ~56KB JSON; needs progressive disclosure and curated task | - | canonica 2ad1 (ya arreglada) |  |
| `4ef3` | DayZ_MCP | can_prepare=false but dayz_knowledge_prepare still callable; fails late with k | - | canonica 0505 (ya arreglada) |  |
| `3485` | DayZ_MCP | Same outage, two vocabularies: bridge_status says server_poll_stale while worl | - | canonica 1765 (ya arreglada) |  |
| `ec73` | DayZ_MCP | bridge_status buries ready/reason under ~200 lines; healthy-looking version_st | - | canonica 2492 (ya arreglada) |  |

## Siguen abiertas, del MCP (11)

| ficha | proyecto | titulo | canal | evidencia | nota |
|---|---|---|---|---|---|
| `2e3f` | DayZ_MCP | session_status: hide/collapse foreign_ports OS noise by default | C | session_status sigue enumerando 49 puertos | el flag si, el colapso no |
| `6a72` | DayZ_MCP | No dayz_knowledge_search in the registry although dayz_knowledge_status implie | - | no existe dayz_knowledge_search en el arbol | menor, de nomenclatura |
| `1432` | DayZ_MCP | session_status.foreign_ports returns ~58 OS-noise ports; box reads as busy eve | C | misma familia que 2e3f | el flag si, el colapso no |
| `6ed1` | DayZ_MCP | Árbol vivo de DayZ_MCP_dev: 9 ficheros versionados con copia CRLF; dos van emb | propia | win32_fileinfo.py y launcher.cpp van CRLF y entran al sello | condiciona el gate |
| `2143` | SUB_BRZ | vehicle_get_in_client: tras minutos sin conducir, el jugador vuelve al punto d | - | - | pide juego |
| `75e7` | DayZ_MCP | f298 confirmado por el dueño: DayZ roba el foco en cada arranque, también en s | - | - | ciclo in-game |
| `fade` | DayZ_MCP | f298 medido con sonda pasiva: el join no confina el cursor; los DayZ toman el  | - | - | ciclo in-game |
| `ba11` | DayZ_MCP | Canario BUG-096 (2026-09-15): intruso con la misma cuenta Steam se queda en el | - | - | canario INCONCLUSO |
| `e4be` | DayZ_MCP | La poda de backups (160e) borra por ruta: una junction cambiada entre check y  | - | - | poda por ruta con junctions |
| `8bc6` | DayZ_MCP | Un coche de world_spawn que sobrevive a su run vuelve por persistencia y objec | - | - | coche persistente fuera de su run |
| `f47b` | Arma2Quad | camera_set deja el render del cliente congelado y restore_gameplay no lo recup | A+B | e3acb6a; test_lote_msgs_f5a7.py | la SENAL se arreglo en b0d9; la cura del render sigue sin probar |

## No son del MCP: arnes del agente o host (10)

| ficha | proyecto | titulo | canal | evidencia | nota |
|---|---|---|---|---|---|
| `9556` | SUB_BRZ | PowerShell hook blocks here-strings whose Spanish text contains the word 'del' | - | - | hook PowerShell del host |
| `facc` | SUB_BRZ | Bash tool returned an `export -p` dump (env with API keys) instead of running  | - | - | Bash del host volco el entorno |
| `d70d` | LFPowerGrid | Heredoc a Python por la herramienta Bash: las dobles barras llegan simples y \ | - | - | heredoc del host |
| `51f6` | LFPowerGrid | Un stop hook del host se cuela en el worker delegado (grok-cli) y le pide edit | - | - | stop hook en worker delegado |
| `5ccc` | LFPowerGrid | codex exec se cuelga para siempre con stdin heredado abierto; el vigia por fic | - | - | codex exec y stdin heredado |
| `e0f0` | LFPowerGrid | codex exec -s workspace-write deniega escribir en CUALQUIER dir llamado .git ( | - | - | sandbox de codex y .git |
| `c586` | AI_Pipeline | Claude Code: el .output de un subagente en segundo plano queda en 0 B; el info | - | - | subagente en background con .output vacio |
| `7b41` | AI_Pipeline | agy: 429 RESOURCE_EXHAUSTED en gemini-3.8-flash-high, y status=ERROR con el in | - | - | cuota de agy |
| `dfca` | AI_Pipeline | Cursor Ultra: Kimi K3 Max y GLM 5.2 Max bloqueados por límite de uso hasta el  | - | - | cuota de Cursor |
| `160c` | DayZ_MCP | 75e7, hipótesis 2 no reproducida: capture_screenshot, Bash y PowerShell de la  | - | - | sonda: las tools del host no roban foco |

## De otros proyectos (3)

| ficha | proyecto | titulo | canal | evidencia | nota |
|---|---|---|---|---|---|
| `98f3` | LFPowerGrid | Dos bucles nocturnos en el mismo proyecto sin verse: un «restaurar PBO por sha | - | - | LFPowerGrid |
| `2084` | LFPowerGrid | Follow-up: a restored storage_1 loaded after a 1.5 h gap | - | - | LFPowerGrid |
| `52c3` | LFPowerGrid | DayZDiag rejects an orderly-saved storage_1 when the next boot comes after a g | - | - | LFPowerGrid |


## Decisiones del dueno (2026-09-21)

Dos fichas quedaron esperando su criterio porque en ambas **el mecanismo se arreglo y el sintoma
sigue vivo**, y cerrarlas por la rama habria afirmado una cura que nadie ha visto:

- **`f298`** (DayZ roba el foco al arrancar): **cerrada**. El PR #59 entrego lo que esa ficha pedia,
  arrancar sin activar la ventana. El sintoma vivo se sigue en `fade` y `75e7`, con mediciones mejores
  que el reporte original, y lo cerrara el ciclo in-game. Un sintoma, un hilo.
- **`b0d9`** (restore_gameplay dice ok con el render congelado): **cerrada**. El PR #65 hace que la
  congelacion se confiese en vez de ocultarse, que es exactamente lo que la ficha reclamaba.
- **`f47b`** (camera_set congela el render): **sigue abierta**. Lo que se arreglo es la SENAL, no la
  cura. Se cierra cuando un run in-game vea el render volver.

## Correcciones a lo que esta misma sesion habia dicho antes

1. **`2e3f` y `1432` no son duplicadas de `26ac`.** El flag `dayz_relevant` si esta (PR #83), pero
   lo que esas dos pedian —colapsar la lista por defecto— no se ha hecho: `session_status`
   devolvio **49 puertos a las 23:5x y 102 tras dos arranques**, todos con `dayz_relevant:false`.
   La lista crece sin tope en cada respuesta. Siguen abiertas.
2. **La primera cuenta de coincidencias de id fue 19 y la correcta es 16.** Las otras tres eran la
   trampa del sha descrita arriba. El error propio es el origen de la regla que se le dio al worker.

## Nota para la ficha del gate (`fb-20260920-230648-99db`)

El bundle del launcher se reconstruye desde el **arbol de trabajo**, y `win32_fileinfo.py` (uno de
los 16 modulos de `_APP_PACKAGED_MODULES`, `native_bundle.py:65-84`) y `src/launcher.cpp` estan en
disco con **CRLF** mientras git guarda LF (`git ls-files --eol`, ficha `6ed1`). Un clon limpio saca
LF, produce otro `app.pyz` y otro PE: **el sello es reproducible contra este arbol, no contra el
repositorio**. Cualquier gate que compare una compilacion limpia con el artefacto instalado dara
rojo siempre hasta que eso se normalice.
