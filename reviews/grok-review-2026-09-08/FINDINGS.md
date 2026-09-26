Voy a leer el brief y ejecutar el encargo completo, respondiendo solo con el texto que pida.
El brief pide revisi├│n adversarial sin escribir ficheros ni usar shell. Voy a abrir los entregables, el triaje y el c├│digo citado.
Ya tengo el plan, el QA y el veredicto. Ahora cruzo el triaje, el c├│digo citado y los tests de compartici├│n.
Voy a abrir las citas de c├│digo que el plan y el QA usan como veredicto, y a contrastarlas con el ├írbol real.
Abr├¡ el brief, el triaje, el plan, el veredicto, `host_config.py`, `control_client.py`, los tests T1/T2/T3 y `test_shareconflict_windows.py`, y las citas `path:line` de Enforce/Python que usan esos documentos.

**Grave en B (cliente no acreditado que pasa, T2 debilitado): no.** El diff de producci├│n no relaja la aceptaci├│n. T2 sigue rechazando. El problema serio est├í en A: el recorte 2 HACER / 16 NO HACER no ordena por riesgo.

```
[A][1] SEVERIDAD: alto
QU├ë: ┬½16 NO HACER de 60┬╗ no es 16 descarte; es 55 de 60 VIVO empaquetados, y N3 entierra guards baratos en un refactor caro.
D├ôNDE: reviews/audit-plan-2026-09-08/PLAN.md:250-275; addon/scripts/5_Mission/MCPBridge.c:3436; addon/scripts/5_Mission/MCPClientBridge.c:371 y :3911 y :4058; addon/scripts/5_Mission/MCPDialogController.c:944.
POR QU├ë: H2 cubre 1 VIVO (P-OPT-05); D1ÔÇôD3 cubren 4; N1ÔÇôN16 cubren 55. N8 descarta bien trabajo vac├¡o (config.cpp:21 es coherente; E-GUI-01/02 son VIVO positivos). N1 se sostiene: MCPCallbacks.c:10 libera y despacha, el cliente en MCPClientBridge.c:19-22 hace lo mismo y adem├ís descarta callbacks no activos, y :287 tiene watchdog. N5 ┬½no compensa┬╗ es opini├│n: Capture ya retorna en fr├¡o en MCP_CarScript.c:207 y no hay m├®trica. N16 no inventar expr<4096 se sostiene (loopback.py:1868-1870 ya exige allowlist de expr), pero N3 no: PostResult retorna en silencio si !m_Configured||!m_Ctx (:3436), pollHz>0 entra sin techo en servidor (MCPBridge.c:195) y cliente (:371), RestoreGameplay llama GetGame().GetPlayer() sin nulo (:3911), y Shutdown no tiene flag de reentrada (:237 destructor, :252 ShutdownInstance, cuerpo :4058). Esos cortes se ven en fuente. El plan los rechaza porque el paquete entero (TickHub, Insert nativo, vuelo de lifecycle) no cabe en la lane offline; el motivo no ataca el guard concreto.
C├ôMO SE COMPRUEBA: contar filas de la tabla ┬½Trazabilidad completa de los 60 VIVO┬╗; abrir las cinco ramas citadas; contrastar con PLAN.md:132-140, que admite el riesgo y aun as├¡ cierra N3.
```

```
[A][2] SEVERIDAD: medio
QU├ë: el 12/12 puede fallar en etiquetas de triaje; no puede fallar el recorte 2/4/16 porque esa decisi├│n no estaba en la muestra.
D├ôNDE: reviews/audit-plan-2026-09-08/QA-TRIAJE.md:11-26; reviews/audit-plan-2026-09-08/PLAN.md:3.
POR QU├ë: repet├¡ tres (en realidad seis) verificaciones y coincido en el veredicto de esas entradas: E-ERR-06, throttle se rechaza en MCPClientBridge.c:1065 aunque IsFiniteFloat del cliente (:3759) solo caza NaN; E-BUF-02, DrainPending quita pending en MCPBridge.c:115 y :317 antes de Dispatch, sin ACK; P-ARC-03a, daemon_policy.py:319 recarga procedencia al revalidar; E-DUP-02 como arriba; T-DUP-04, test_task7_final_authority_regressions.py:12 y :18 y test_task7_final_lifecycle_regressions.py:9 importan tests.test_*; P-TICK-01, lease_supervisor.py:103 espera el pr├│ximo heartbeat, daemon.py:870 instala el reaper y :1591 excluye parent-death, server.py:3044 es el heartbeat del claim sin tool_lock. La muestra son 4 VIVO de duplicaci├│n, 4 FALSO ┬½si me equivoco entierro un defecto┬╗ y 4 NO VERIFICABLE. No incluye SCORE-10, ni E-ERR-04, ni ning├║n VIVO que el plan iba a mandar a NO HACER. El 100% habilita PLAN.md (QA-TRIAJE.md:3) sin haber interrogado la prioridad.
C├ôMO SE COMPRUEBA: listar los 12 IDs de la tabla QA y cruzarlos con las unidades H/D/N; no hay solape con E-ERR-04/E-BUF-01/E-ERR-01.
```

```
[A][4] SEVERIDAD: alto
QU├ë: los 2 HACER no son lo m├ís importante de los 60; H1 ya est├í hecho en este ├írbol y H2 documenta constantes que README ya distingue.
D├ôNDE: PROJECT-MAP.md:21-27 frente a reviews/projectmap-2026-09-08/PROJECT-MAP.BEFORE.md:21-23; tools/README-mcp.md:58; addon/scripts/5_Mission/MCPClientBridge.c:4058 y :3911; addon/scripts/5_Mission/MCPBridge.c:3451; tools/dayz_mcp/lease_supervisor.py:12; tools/dayz_mcp/session_coordination.py:15.
POR QU├ë: el PLAN cita PROJECT-MAP.md:21 y :23 como l├¡mite fijo de 125 l├¡neas. El BEFORE s├¡ lo ten├¡a (`HANDOFF.md` tiene **125 l├¡neas**; Read limit: 125). El mapa actual, generado 2026-09-08 09:38:15 UTC, ya nombra LIVE-STATE:END, proh├¡be fijar l├¡neas, y HANDOFF.md:125 tiene el marcador; los entry points listados existen bajo tools/. H1 era el ├║nico rojo reproducible de la lane y otra lane ya lo cerr├│. H2 es una tabla: 45 s est├í en lease_supervisor.py:12, 120 s en session_coordination.py:15, 10/3 s en orphan_guard.py:380 y :473; los n├║meros cuadran. README-mcp.md:58 ya dice que el TTL del lease/ticket es 120 s y que el heartbeat es solo para trabajo exclusivo. El VIVO que deber├¡a estar arriba es E-ERR-04/E-BUF-01: Shutdown sin reentrada y m_CallbackRefs.Insert sin techo (MCPBridge.c:3451), no una tabla de cadencias.
C├ôMO SE COMPRUEBA: diff PROJECT-MAP.md contra PROJECT-MAP.BEFORE.md; leer README-mcp.md:52-58; no hace falta juego para ver que H1 ya no describe el artefacto actual.
```

Sobre **A3**: reabr├¡ E-ERR-06, E-BUF-02 y E-P01. Siguen FALSO. Throttle infinito no pasa el rango. Pending no espera ACK de result. `MultilineTextWidget extends TextWidget` est├í en `scripts/1_core/proto/enwidgets.c:219` y el layout usa `MultilineTextWidgetClass Title` (`addon/gui/layouts/mcp_dialog.layout:26`). T-CON-02 tambi├®n se sostiene: `vehicle-trace-v1.json:3` es schema, el fixture de curso declara `dayz-mcp-vehicle-trace-course-v1`, `test_vehicle_trace.py:23-24` y :261 consumen fuentes distintas. SCORE-10 es FALSO en la premisa del ├¡ndice (`GATES.md:3-7` explica 195 con los ocho ROOT); el mapa obsoleto era otra afirmaci├│n y no se enterr├│ (sali├│ H1). **No encontr├® el error caro de un FALSO que sea VIVO.**

```
[B][2] SEVERIDAD: medio
QU├ë: las 24 combinaciones prueban las 6 celdas elegidas ├ù 2 ficheros ├ù 2 ├│rdenes, no la ret├¡cula Win32 de share/acceso.
D├ôNDE: tools/tests/test_shareconflict_windows.py:53-59 y :204-251; reviews/shareconflict-2026-09-08/gate.log:10-34; tools/dayz_mcp/host_config.py:710-723.
POR QU├ë: _CASES tiene READ+share 1, READ+share 7, READ+share 0, WRITE+share 7, READ|WRITE+share 7, DELETE+share 7. gate.log muestra exactamente esas 12+12 l├¡neas con winerror 0 o 32. Faltan FILE_SHARE_WRITE solo, FILE_SHARE_DELETE solo, WRITE+share READ, FILE_WRITE_ATTRIBUTES, rename en curso, filtros/cloud. Eso no cambia la conclusi├│n que s├¡ prueban: no hace falta exclusividad; un WRITE o DELETE con share=7 ya da 32. El pin de producci├│n sigue GENERIC_READ + FILE_SHARE_READ (:710-713). El orden pin-primero no llama al ControlClient: solo mide si el otro proceso abre; la tabla del VERDICT lo etiqueta bien.
C├ôMO SE COMPRUEBA: contar _CASES (6) ├ù paths (2) ├ù tests de orden (2); listar share modes 0x2 y 0x4, que no aparecen.
```

```
[B][3] SEVERIDAD: medio
QU├ë: ┬½mecanismo demostrado / atribuci├│n no demostrada┬╗ es honesta en el fondo y vende de m├ís en el titular.
D├ôNDE: reviews/shareconflict-2026-09-08/VERDICT.md:3-7 y :75-82; tools/tests/test_shareconflict_windows.py:278-293.
POR QU├ë: TEMP_ONLY y RENAME_COMPLETED aceptan; no ejecutaron ReplaceFileW ni el CLI. Eso acota bien temp+rename como causa necesaria. El salto ┬½la hip├│tesis deja de ser una posibilidad sin reproducir┬╗ trata un handle WRITE/DELETE sostenido (suficiente para el mismo token) como si fuera la hip├│tesis del incidente (escritura del host). El cambio de producci├│n es publicitar policy_cause en el hint (control_client.py:177-189), no atribuir 49b2/c261/050e. Esa distinci├│n del STATE.md:160-166 es la que vale; el primer p├írrafo del VERDICT la diluye.
C├ôMO SE COMPRUEBA: leer test_unrelated_temp_handle_and_completed_rename; buscar en VERDICT.md una traza contempor├ínea con PID/imagen ÔÇö no hay.
```

**B1 ÔÇö v├¡a de no acreditado que pase.** No la encontr├®. `implementation.diff` a├▒ade `policy_cause=` al hint y conserva el mismo `client_policy_untrusted_open_new_session`, `pre_request`, 0 bytes. `_request_once` sigue capturando cualquier `Exception` de `revalidate()` y rechazando (`control_client.py:174-190`). `server.py:1287-1291` reintenta solo `daemon_unavailable`. Ese c├│digo est├í en `_CONTROL_CLIENT_ERROR_CODES` (`server.py:386`), as├¡ que se publica `str(error)` (c├│digo+hint), no un ├®xito. `_policy_revalidation_cause` (`control_client.py:43-63`) publica tipo+errno/winerror o un token `[A-Za-z][A-Za-z0-9_]{2,63}`; `HostConfigError` no lleva errno, as├¡ que el 32 sale como `HostConfigError:32` y no la ruta. El test `test_raw_message_never_reaches_public_response` (`test_shareconflict_windows.py:295-310`) exige que no salgan `PRIVATE_MESSAGE`, `private` ni `secret=fixture`. La comparaci├│n sem├íntica de `_registration_from_entry` (`host_config.py:215-295`) y el consenso dual (`:359-360`) no las toc├│ esta lane. Un lector compatible (READ+share READ/7) acepta porque la registraci├│n sigue siendo la acreditada, no porque se relaje el parser.

**B4 ÔÇö T2.** Sigue vivo y no est├í debilitado. `test_t2_changed_registration_is_rejected_without_http` (`tools/tests/test_provenance_gate.py:151-198`) muta command, keyfile, timeout, idle_timeout, missing, unknown_field, malformed y bool_timeout, en between_requests/reopen/reread, y exige `client_policy_untrusted_open_new_session`, `pre_request`, `http_bytes_sent=0` y cero HTTP. STATE.md dice que ese fichero no se modific├│. El test nuevo `test_changed_registration_has_distinct_public_cause` (`test_shareconflict_windows.py:261-276`) es aditivo y m├ís estrecho (solo idle-timeout 12.5ÔåÆ13.0) y adem├ís exige causa distinta de `:32`. Los dobles `_race` de T2 siguen siendo handles de snapshot, no pines Win32; eso ya estaba documentado en el propio test L6 y M2 no lo us├│ para T2.

**QU├ë PUEDE ESTAR MAL EN LA PREMISA DE ESTE ENCARGO.** El encargo trata ┬½16 de 60┬╗ como la tasa sospechosa y el FALSO mal etiquetado como el error caro. En estos entregables el recorte real es 55/60 VIVO a NO HACER, y VIVO incluye observaciones positivas (E-GUI-01/02) y costes no medidos; la pregunta que mueve trabajo es si N3 empaquet├│ guards fail-closed visibles en fuente con un refactor que esta lane no pod├¡a validar. Pedirme cazar un FALSOÔåÆVIVO desv├¡a hacia donde el triaje ya se hab├¡a defendido (throttle, pending, Title) y no hacia Shutdown/callbacks. En B, mandar a leer `host_config.py` como si M2 lo hubiera relajado apunta al arreglo sem├íntico anterior (`4e34bda`), no al diff de esta lane; la pregunta correcta de B1 es el catch de `revalidate` y el retry de `server.py:1287`, y esos siguen cerrados. Las 24 celdas no son el sitio donde se esconder├¡a un pase de no acreditado. La prohibici├│n de shell impide reejecutar los 82 tests: si `gate.log` mintiera, este encargo no lo cazar├¡a m├ís que leyendo el log.

**LO QUE NO PUDE VERIFICAR**
- Reejecutar `unittest` de `test_shareconflict_windows`, `test_provenance_gate`, `test_docs_truth` ni los 82 tests: el brief proh├¡be shell.
- `git ls-files` de los 26 `.bak` (N15): misma prohibici├│n; el `git status` inicial de esta sesi├│n los muestra untracked, no versionados, pero no es el comando que cit├│ el plan.
- Que la muestra de 12 se escribiera antes de abrir fuentes: solo lo afirma QA-TRIAJE.md:5.
- SHA-256 de `host_config.py` contra `baseline.json`: no hashe├®; le├¡ el pin y el parser.
- Causa de 49b2/c261/050e, actor y duraci├│n del conflicto real, rename en progreso, flags Win32 del CLI, y si un proceso vivo carga este `control_client.py`.
- Sem├íntica nativa de `map.Insert` con duplicados y crash de Shutdown/GetGame nulo: hace falta motor; el brief de revisi├│n tambi├®n es solo lectura.
- Creaci├│n real de symlink (WinError 1314) y suite completa.
- Escritura de Codex sobre `config.toml` (no estaba en PATH seg├║n el STATE de M2).
- Equivalencia de suites si se aceptaran D1/D2: no se implementaron.
- CPU, spawns de netstat, latencia de captura: no hay medici├│n en ning├║n entregable, y este encargo no deja ejecutar daemon/juego.
- Que `PROJECT-MAP.md` actual pase `test_docs_truth` en esta m├íquina: el texto actual satisface a ojo los asserts de `test_docs_truth.py:545-580` y :641-655, pero no corr├¡ el m├│dulo.