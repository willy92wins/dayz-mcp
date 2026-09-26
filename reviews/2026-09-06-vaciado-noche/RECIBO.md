# RECIBO — noche del 2026-09-06 (sesión «Vaciado de buzón MCP», continuación)

Decisiones de Guillermo (picker ~16:55): df93 + plan M23 por Codex en la ventana final de cuota
(98 %, reset 07-sep 04:27); 59d9 opción (a); ae65 la llevo yo (paso 1 + repro con la caja libre).
Reserva (`local_e84a1b8e`) no está viva desde el 05-sep 02:02; su índice de cierre es
`reviews/2026-09-04-reserva/INDEX.md`.

## b8b0 — fixture dependiente del host (RESUELTA)

Commit `8ff937e`. Detalle en `reviews/2026-09-04-cierres-menores/lote-D/RECIBO-D.md` (anexo 2026-09-06).

## 59d9 — entities_query poda `entities: []` (RESUELTA, opción a)

Commit `8c4007d`: `("entities_query", "entities")` entra en `SEMANTIC_EMPTY_FIELDS` (`result_prune.py`),
docstring ajustado y línea en CHANGELOG; test `test_entities_query_keeps_its_empty_list` y fixture de wire
con `entities`. `tests.test_result_prune` + `test_ui_dialog` + `test_ui_error_diagnostics`: 62 OK. La
descripción de la tool (server.py, «Absent entities travel as []») no cambia: ahora es cierta. La corrección
de cita heredada que pedía la ficha (`bool has_cargo` está en MCPMessages.c:360, no :359) queda anotada aquí
para 40e4 y `R4-gates-engine.md:35`; no toco el documento de Reserva.

## ae65 — build=true muere en NativeLauncherBackendError (ABIERTA; paso 1 hecho)

Commit `0873d53`: `_opaque_dayz_test_failure()` en server.py: el código del backend (token identificador
según `_is_safe_error_token`) viaja tras el nombre de la clase,
`dayz_test_failed:NativeLauncherBackendError:<code>`; el `detail` (rutas de host) sigue solo en el log local.
Test `test_dayz_test_launcher_backend_code_travels_without_its_detail` (token viaja; mensaje con ruta no
viaja). 11 OK. Paso 2, la repro con `build=true`: ver «Repro ae65» abajo cuando esté.

## df93 — ronda 4 en la rama `work/df93-vpp-preflight` (EN REVISIÓN)

Commit `180815e` en el worktree de Reserva (`…\e89adcdf-…\scratchpad\wt-df93`): R3-F-01 (meta.cpp por el
mismo scanner del cfg; una única asignación viva con el decimal canónico; fichero sobre el tope = nada probado)
y R3-F-02 (comilla simple = terreno muerto; sin cerrar = rechazo). Rojo-primero: 11 subcasos rojos sobre
`fd660d2`; después 53 OK (`test_vpp_preflight`) + 129 OK (4 módulos tocados), intérprete del worktree.
Delta-3 de Codex a effort max desde `review-df93/` en el scratchpad de esta sesión (`DELTA-R4.patch`,
`BRIEF-DELTA3.txt` con los sha256 del producto, `REVIEW-CODEX.md` al terminar; se copia aquí al recibirlo).
Destino del merge: decisión de Guillermo (Reserva prohibió mergear a `work/inbox-20260830-modules` mientras
el dictamen fuera NO SEGURO); pendiente también la decisión A/B/C del alcance (la puerta rechaza a
`LF_VStorage`, `LFPowerGrid` y `SimpleGroup`) de `lote-df93/CIERRE.md`.

## M23 — plan por Codex (EN CURSO)

Sesión `codex exec` a effort max lanzada a las 17:13 desde `review-M23/` (brief `BRIEF-PLAN-M23.txt`,
fichas `FICHAS-M23.txt`); entregable `PLAN-M23.md` + `EXIT-PLAN-M23.txt`. Se copia aquí al recibirlo.

## Qué NO se verificó

- Nada in-game salvo la repro de ae65 (abajo, si llega a lanzar algo se para con `dayz_test_stop`).
- La suite completa del árbol vivo con los tres commits de esta noche: se lanza al final del recibo; hasta
  entonces la evidencia es por módulo.
- El daemon vivo (generación `8f41560e…`) no carga server.py (no está sellado): la sesión MCP que lo vea
  tiene que abrirse después del commit `0873d53`.

## Repro ae65 (2026-09-06, 17:23-17:26)

Caja libre (session_status: occupied false, sin lease). `dayz_test_run(project=LFPowerGrid, mode=server,
extra_mods=[@DayZ_MCP], build=true, port=2302, wait_for_box_s=0, server_wait_s=30)` -> **status succeeded**, run
`3796344c-427b-4178-ab77-1a079bb8f86f`, `server_alive: true`, `elapsed_s: 121.08` (build incluido, sin error_code).
`dayz_test_stop` -> succeeded en 3.1 s; caja libre despues. Daemon vivo: generacion `8f41560e...` (la ficha midio
`20c6ee15...` el 04-sep). **No reproducido con mode=server.** El brazo exacto de la ficha (mode=all, que abre el
cliente de DayZ en el escritorio) no se ha corrido: pendiente del OK de Guillermo. Con el paso 1 integrado
(`0873d53`), la proxima vez que ocurra el error llegara con el codigo del backend (uno de los 12 puntos de fallo de
native_launcher_backend.py), desde un cliente MCP abierto despues de ese commit.

## Suite completa del arbol vivo (HEAD 0873d53, 17:24:53 -> 17:29:42)

`SUITE-noche.txt`: Ran 2763, failures=2, skipped=6; los dos rojos son los centinelas de bd90
(`test_full_source_hash_is_frozen`, `test_removing_only_marker_lines_restores_frozen_source_hash`), iguales a la
linea base. Reserva anoto `Ran 2794` en 24919cb; la diferencia de recuento (31) no se investigo.

## Dictamenes recibidos (17:43-17:55) y lo que sigue

- **df93 delta-3 (Codex, effort max, RC=0)**: NO ES SEGURO INTEGRAR. R3-F-02 CIERRA; R3-F-01 NO CIERRA por un hallazgo
  nuevo **R4-F-01 (ALTA)**: un comentario entre la clave y el `=`, o entre el `=` y el valor, hace que esa asignacion no se
  cuente (`publishedid=1828439124; publishedid/*x*/=1559212036;` acredita el mod; `vppDisablePassword=1;
  vppDisablePassword/*x*/=0;` pasa) y un comentario pegado al valor produce un falso rechazo. Mutantes: 3/3 cazados; sin
  cambio de tokens ni de modulos sellados. Copia en `df93-delta3/`. **Ronda 5 delegada a Grok** (Cursor,
  `cursor-grok-4.6-xhigh`, workspace fiel `lote-df93-r5/ws` del scratchpad, brief `run1/brief.txt`): tests rojo-primero,
  semantica fijada en el brief, 3 mutantes, STATE.md. Revision cruzada al recibir: Anthropic (Codex sin cuota hasta 04:27).
- **M23 plan (Codex, effort max)**: `PLAN-M23.md` de 586 lineas con las 8 secciones del contrato; la sesion murio al
  llegar la cuota al 100 % (`You've hit your usage limit`, RC=1, 197k tokens) sin escribir su marcador de cierre. Digest
  por Cursor `composer-2.5` (el intento con `cursor-grok-4.6-high` fue bloqueado por el filtro del proveedor: `Request
  blocked ... usage guidelines`): 6 lotes L0-L5 con oraculo mecanico, lote minimo util L0+L1+L2, 5 preguntas con
  recomendacion, 3 afirmaciones sin cita y 3 huecos frente a las fichas (TypeError con enum no escalar ausente; A-01 solo
  advisory; 9b7b opcion c descartada a proposito). Copias en `M23/`. Pendiente: revision del plan por otra familia antes
  de implementar (Codex tras el reset, o subagente Anthropic).
- **Doctrina corregida**: la sesion Codex ya arrancada TAMBIEN muere al 100 %. Memoria
  `codex-window-cut-kills-running-session`; hook `~/.claude/hooks/codex-quota-alert.ps1` reescrito (commit `0a7b6c0` en
  `~/.claude`); `delegar/references/providers/openai.md` seccion Exprimir el tramo final queda por corregir via Pack.
- **Coordinacion**: Reserva (`local_e84a1b8e`) volvio a estar viva a las 17:47 y abre la ventana de rebuild de app.pyz;
  mensaje enviado con fronteras (df93 y su worktree, ae65, M23, este recibo son mios; etapa B, 76dd, 40e4, d60f/8f76,
  668f, R4 y la caja son suyos; aviso previo al merge de etapa B y al re-spawn del daemon). Sin respuesta aun.

## df93 ronda 5 (Grok por Cursor, cursor-grok-4.6-xhigh; 17:46-17:53)

Entrega en `lote-df93-r5/ws` (scratchpad): write-set exacto (2 ficheros + STATE.md), `_VPP_VALUE` sustituido por
`_skip_non_string_dead_ground()` + lectura del valor hasta `;`, blanco, `/*`, `//` o EOF; clase `VppPreflightRound5Test`
(4 tests, 14 subcasos). Grok declara: rojo-primero 10 subcasos; verde 57; focused_repro cerrado; regresion 129; 3 mutantes
cazados (8, 2 y 4 rojos) con sha256 antes/despues; producto final
`079c6737502255b01aaf89ef73e782e451abd099d404df0f550c02ca5035d3b5`, tests
`c1e6a65f5b5513d75adc8b93b0d6c9f351c8b373ae019c275b4688ec57b5a391`.
**Verificado por mi** con el interprete del arbol vivo en ese workspace: `tests.test_vpp_preflight` Ran 57 OK; los 4 modulos
Ran 129 OK; `SPEC-focused_repro.py` con las ocho filas como exige el contrato (duplicados y conflicto rechazan;
`meta_value_comment_adjacent` pasa); hashes iguales a los declarados. Diffs: `DIFF-R5-product.patch` (94 lineas) y
`DIFF-R5-tests.patch` (148). Revision cruzada Anthropic (subagente, contexto fresco) en curso: `review-r5-anthropic/`
del scratchpad; se integra en la rama solo con dictamen SEGURO.

## d366 y 141e RESUELTAS por texto; 9b7b sigue abierta (18:00, tras el triaje de Reserva)

Reserva (`TRIAJE-BUZON.md` filas 21/24/26) senalo que el instrumento de esquema efectivo ya esta en HEAD. Verificado:
`8732ee3`, `139c9f4`, `7f58aa8`, `7fe4cb1` y `ae64fdd` son ancestros de HEAD (`git merge-base --is-ancestor` rc 0 en los
cinco); `tools/dayz_mcp/effective_schema.py` con `resolve_effective_schemas()` (:29) y `audit_contracts()` (:165);
tests `test_effective_schema.py` (14, incluido `test_non_scalar_enum_values_do_not_crash_the_audit`),
`test_effective_schema_catalog.py`, `test_effective_schema_core.py`, `test_effective_schema_runtime_validators.py`,
`test_tool_registry_fingerprint.py`. El PLAN-M23 ya lo sabe (linea 6: "completar el instrumento ya aterrizado").
- **d366** (contribucion: instrumento construido y no aterrizado) -> RESUELTA: aterrizado; lo que falta (autoridad
  bloqueante, promotor, CAS/journal) es el alcance del plan M23.
- **141e** (hallazgo: el contrato no es auditable leyendo el cuerpo) -> RESUELTA: existe el instrumento que pedia; la
  frontera con el puente Enforce queda declarada en el plan.
- **9b7b** sigue ABIERTA como objetivo de la pieza (b) de M23: `bridge_status` publica `tool_registry_fingerprint`,
  `captured_at`, `source_stale` y `remediation`, pero medido en vivo (daemon `8f41560e`, 17:59)
  `tool_registry_source_stale` vale "unknown": `_frozen_tool_registry_overlay` (server.py:606-624) alimenta
  `read_authority_marker` con `AuthorityBundleBytes` todo a None y `compare_snapshot_to_authority`
  (tool_registry_fingerprint.py:769-783) no puede decidir. Ficha nueva de tipo bug archivada con esa evidencia.

## Revision Anthropic del PLAN-M23 (subagente, contexto fresco; 18:00-18:17)

`M23/REVIEW-ANTHROPIC-plan.md`: **APROBAR CON CAMBIOS MENORES**. 1 P1 + 5 P2 + 9 P3 (R-001..R-015). Citas: 45
verificadas, 44 correctas sobre 8ff937e (9 desplazadas por los tres commits posteriores; server.py >= 298 va +17/+19), 0
falsas; el plan NO esta truncado (8 secciones; copia byte-identica al cwd de Codex). P1 R-001: el cliente MCP corre desde
fuente (`install-mcp.ps1:479` registra `-m dayz_mcp --client`; server.py no esta sellado), asi que no hay despliegue
Python al que atar el promotor y la regla "hash local != autoridad => stale + reopen_mcp_client" invertiria la senal de
9b7b (la sesion vieja saldria fresh y toda sesion nueva stale): hay que cerrarlo antes de L4 (comparar `source_files` del
artefacto con el disco; nueva pregunta Q6: quien corre el promotor). Otros: coste de contexto no cuantificado (+10-12 KB
por sesion si se duplica el inputSchema en prosa, R-004/Q7); `dayz_test_modes.py` SI esta sellado en app.pyz y la
autoridad podria publicar un modo que el worker rechaza (R-005). El digest de composer-2.5 se equivoca en un punto: el
TypeError con enum no escalar ya esta corregido en HEAD (`test_non_scalar_enum_values_do_not_crash_the_audit`).
Ficha nueva: `fb-20260906-160935-9e0d` (veredicto muerto de `tool_registry_source_stale`, entra en la pieza b).
Estado para la sesion dedicada de M23: plan + digest + revision listos; d366 y 141e cerradas; 9b7b y 9e0d abiertas.

## df93 ronda 5 INTEGRADA en la rama (18:2x)

Revision cruzada Anthropic (`df93-delta3/REVIEW-ANTHROPIC-R5.md`): **SEGURO INTEGRAR**, 0 ALTA, 0 MEDIA, 2 BAJA (una de
evidencia: el patch de tests mostraba como borrada la fila extrana del worktree, que no era de Grok; otra de cobertura:
cuatro mutantes que violan reglas del contrato sobrevivian a los 57 tests). 70 casos limite ejecutados: 0 desviaciones del
contrato sobre la ronda 5 (17 sobre HEAD 180815e); STATE.md de Grok cuadra numero a numero. Respuesta a la cobertura: un
metodo mas en `VppPreflightRound5Test` fija las cuatro reglas y la repro literal de delta-2 (`;` dentro de la cadena
simple): `tests.test_vpp_preflight` 58 OK; en el worktree 58 + 129 = 187 OK. Commit **`a7ddbac`** en
`work/df93-vpp-preflight` (worktree de Reserva, limpio; hashes producto `079c6737...`, tests `46ec4ecb...`).
La rama queda: f4fb41d -> fd660d2 -> 180815e -> a7ddbac sobre 24919cb. NO se mergea: destino y alcance (A/B/C de
`lote-df93/CIERRE.md`) son decision de Guillermo; el gate in-game G-VPP sigue abierto. Ficha df93: abierta hasta el merge.

## Picker de las 18:4x y lo que sigue

Guillermo: df93 -> "comentamos esto" (sin decidir destino ni alcance); M23 -> si, manana tras el reset de Codex
(carga inicial lista en `M23/NEXT-SESSION-M23.md`); ae65 -> repro exacta (mode=all) ahora, coordinada con Reserva;
leccion -> escrita **LL-462** (corte de cuota por peticion, no por sesion; corpus 626c2067..., indice regenerado el
ultimo, `-Check` rc 0, pin fresco) con destino `openai.md` seccion Exprimir via Pack: texto propuesto en
`PACK-openai-exprimir.patch.md`, pendiente de aplicar en el Pack cuando Reserva termine su reseal (o lo aplique ella).
Arbol vivo mientras tanto: Reserva mergeo etapa B (`1cb90b5`) y d45f (`57c690d`); daemon re-spawneado (generacion
`484794f6`).

## Repro ae65, brazo exacto (mode=all + build=true; 19:1x)

`dayz_test_run(project=LFPowerGrid, mode=all, extra_mods=[@DayZ_MCP], build=true, port=2302, wait_for_box_s=0,
server_wait_s=120)` -> **failed en `validating` a 1.3 s con `error_code=steam_session_stale`** (`steam_live_pids=[22296]`,
`steam_registered_pid=null`, remediacion `restart_steam_and_wait_for_active_process_match`). No se lanzo nada; caja
libre. Es la puerta de Steam, ANTES de la ruta de build y del backend nativo: hoy no se puede ejercer el brazo de la
ficha sin reiniciar Steam (accion de Guillermo). Lo que si esta medido: mode=server + build=true funciona (run
3796344c), asi que el fallo del 04-sep es especifico del lanzamiento del cliente o fue transitorio; con `0873d53` la
proxima ocurrencia llegara con el codigo del backend desde un cliente MCP abierto tras ese commit. Ficha ae65: ABIERTA.

## LL-462 aplicada en su destino (19:3x)

`delegar` NO esta gobernada por el Pack (0 ficheros trackeados en `DayZ-Modding-Knowledge-Pack`, sin adjudicacion) y
`~/.claude/.gitignore` ignora `/skills/`: el destino se edita en su sitio. `openai.md` seccion Exprimir reescrita (titulo con
rev. 2026-09-06, premisa medida, reglas de presupuesto vigentes, hook), backup junto al fichero; LL-462 corregida en su
Destino (applied), indice regenerado el ultimo, pin fresco. Sin reseal ni promocion.

## Continuación 20:0x-21:2x — df93 agnóstico, merge y LL-465

Guillermo preguntó por qué df93 obligaba a usar VPP («debemos ser agnósticos a cuál use la gente») y eligió «opción 1,
un warn». Reconstrucción de cómo se coló el «obligatorio» (ficha «al menos avisar» → R3 «rechazar» dentro de la opción
recomendada de un picker de lotes → lote «todo modo que arranca servidor» → picker «Ronda 4 + rechazar a todos»):
`df93-r6/DECISION-R6.md`. Ronda 6 por Grok/Cursor xhigh en copia fiel, revisión Anthropic SEGURO INTEGRAR (H1 test
host-dependiente y H2 docstrings aplicados por mí; H3 anotado), commit `1bc7c52` en la rama; luego, por orden suya,
merge a la viva `4e264bb` por plumbing (la viva tenía `decision-log.md` staged de otra sesión); suite completa 2911
con solo los centinelas de bd90 tras aplicar el probe host-local del lote (`df93-r6/INTEGRACION-R6.md`). LL-465
(decisión de diseño colada como cláusula de picker) con destino aplicado en `CLAUDE-reference.md` §G1 (`64fdc6b` en
`~/.claude`). Ficha df93 abierta solo por G-VPP in-game. Reserva informada en tres mensajes (20:05, 20:5x, 21:05).

## f45d 21:4x-22:5x — el remote_error desnudo de wait_for

Orden de Guillermo tras la compactación: hablar con la sesión LFPowerGrid, hacer el plan y delegar en Grok. La ficha
`f45d` atribuía el fallo al cliente sin sondear; el audit del daemon (`session_authorized read_only query_all_players`
a las 19:07:25Z sin `enqueue_accepted`) y un control positivo demostraron que era la valla del run sin dueño
(`run_not_owned` + hint) aplastada a `remote_error` por la lista blanca del cliente. Brief con `path:line`, Grok/Cursor
xhigh en copia fiel (12 min), revisión Anthropic SEGURO INTEGRAR (H1 `session_granting`, H2 `isprintable`, H3 receta
de adopción, H4 líneas largas; los cuatro aplicados), commit `44de5ff`, merge por plumbing `8f5727f` tras el
`d3f49dd` de Reserva, suite completa: `Ran 2926 tests in 244.368s` → `FAILED (failures=2, skipped=6)` (RC=1); rojos: `test_full_source_hash_is_frozen`, `test_removing_only_marker_lines_restores_frozen_source_hash`. Ficha cerrada con la causa corregida. Recibos en `f45d/`.
Cierre 22:5x: ficha f45d resuelta (20:33Z); sin `session_status` final a propósito (ventana de auto-apagado del daemon de Reserva, ~22:46); nada mío en vuelo; viva `8f5727f`.
Promoción 00:0x del 07: secuencia de arranque del puente a `dayz-mcp-verify` y puntero en `dayz-test-ingame` (Pack `e9c14ba`, validate PASS, copias vivas iguales al Pack, sin commit en los repos de skills); sin daemon.
