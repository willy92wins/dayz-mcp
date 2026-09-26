# Buzón del pipeline — lo que queda y por qué (sesión «Vaciado de buzón MCP», 2026-09-04)

## DECISIONES DE GUILLERMO (picker, 14:55) — el plan de «continuar»
1. **6927** → lote corto MÍO con auditoría R9 (≥2 ángulos: race del sondeo con caché de 1,5 s; admin/reboot), Codex,
   sin juego. Arranca cuando el par avise la integración del lote J (sus ficheros). Diseño: DIAGNOSTICO-6927.md.
2. **Lecciones LL-445..448** → escribir las CUATRO (borradores en scratchpad/LL-borrador.md). Procedimiento: releer
   lessons-learned.md host-direct y tomar el siguiente número libre AL ESCRIBIR (LL-011; el par propone 445-447 también),
   test de familia en lessons-index.md antes de cada una, formato exacto del fichero, y regenerar el índice el ÚLTIMO
   (gen-lessons-index.ps1) comprobando el pin sha256. Cierra 8fac, 21bc y 5ca4 con pipeline_resolve citando el LL-NNN.
3. **M23 (26 d366, 07 9b7b, 18 141e)** → lote DEDICADO en la siguiente sesión: promotor de esquema efectivo + autoridad +
   CAS/journal, delegado con oráculo sellado y revisión Codex (tamaño XL: no mezclar con otros lotes).
4. **Cierres menores con la propuesta por defecto**: 19 344d (LFPG_Defines.c → 1.2.4, recongelar el gate); 0fde (texto de
   promotion-gate.ps1 sigue a CONTRIBUTING.md; LL-390); 251d (test del brazo lookback_lines=0 sobre los mismos bytes).
   **077d NO seleccionada**: sigue abierta (decisión pendiente).
Orden sugerido tras compactar: 2 (LL, no depende de nadie) → 4 (tres lotes cortos, sin juego) → 1 (cuando llegue el aviso
del par) → 3 (sesión nueva). Las 12 in-game siguen esperando sesión con Guillermo.

Estado tras los lotes M, W y V (esta sesión) y G/H/I (sesión par, cerrada 13:5x): 36 fichas abiertas
al medir, de las que esta sesión cierra ef5e y la parte offline de 05 al llegar el dictamen R2.
Lo que sigue son las que NO puedo cerrar sin Guillermo, agrupadas, con recomendación primero.

## A. URGENTE — régimen P6 estricto: ningún run recién lanzado es despachable (f70b, 5ca1, 6927, f7af)
**Reparto a las 14:35**: la sesión par (lotes G/H/I) se queda con f7af, f70b y 5ca1 en un lote J sobre loopback.py y
process_lifecycle.py (la concesión del lease adoptará el run RUNNING_IDLE y lo declarará en `adopted_run`; adopt_run
idempotente; la limpieza vehicle_release exenta del cerco), ETA 2-4 h; es la opción 1 de abajo, implementada por
quien firmó bdeb87f. **Queda 6927 para ti o para mí**: diagnóstico en solo lectura en
`reviews/2026-09-04-lote-V/DIAGNOSTICO-6927.md` — el sondeo de la caja enumera SOLO por nombre `DayZDiag_x64.exe` y
saca `ports_in_use` del argv, no de la tabla de puertos del sistema (`daemon.py:526`, `process_lifecycle.py:3170-3222`),
así que un servidor ajeno con otra imagen es invisible; el MCP no tiene ningún sitio que mate un PID no registrado
(quién tiró el 22740 sigue abierto). Arreglo propuesto: ocupación por PUERTO (sondeo del sistema) + imágenes de servidor,
y `wait_for_box_s` espera en vez de limpiar. Los dos ficheros son del lote J del par → esperar su integración; clase R9.
Hechos medidos (fichas de tres sesiones distintas hoy entre 09:30 y 11:48, generación del daemon
ad875657, es decir el código de lotes G/H ANTES de bdeb87f):
- `dayz_test_run` exige soltar el lease y el run nace `RUNNING_IDLE` con `owner_session=null`; bajo
  P6 estricto un run sin dueño no despacha NADA, ni lecturas (`loopback.py:1272-1286` → `run_not_owned`).
- La única vía de adopción es la acción `adopt` del lifecycle (`loopback.py:3220` →
  `process_lifecycle.py:2487 adopt_run`), que hoy envían SOLO el worker empaquetado
  (`dayz_test_worker.py:530,:593`, dentro del app.pyz) y el CLI de admin (`lifecycle_cli.py:63`). No
  hay tool pública; `session_acquire_wait` no adopta; el `mode=client` adopta con el lease INTERNO de la
  tool y la propiedad se pierde al soltarlo (`process_lifecycle.py:853-858`).
- Consecuencia: livelock para toda verificación in-game de cualquier proyecto (f70b), y en 6927 el
  lanzador limpió el puerto 2302 matando un servidor ajeno vivo de 6 min porque `foreign=[]` no lo veía
  (el sondeo existe: `process_lifecycle.py:3118-3124`, `diag_probe`; el run ajeno no tenía ficha).
- Cuarta ficha (92e5, 12:02Z, otra sesión): confirma en fuente que `/lifecycle/adopt` ya existe y está
  enrutado (`loopback.py:146`) y que «lo único que falta es el cable desde la superficie MCP» — es decir,
  la opción 1 de abajo, ya medida por un tercero.
Decisión que es tuya (contrato de seguridad: fail-closed deliberado de lotes G/H):
1. RECOMENDADO: tool pública `run_adopt(run_id)` (o que `session_acquire_wait` adopte el run
   `RUNNING_IDLE` de la caja cuando el llamador tiene lease) sobre la ruta `adopt` que ya existe;
   `dayz_test_run` documenta «lanza → adquiere lease → adopta». Cambio en server.py + control_client
   (no toca el bundle). Requiere reiniciar el daemon para que llegue a la generación viva.
2. Alternativa: que `start_run` deje al run con dueño = sesión lanzadora (rompe «la tool gestiona su
   propio lease»; exige rediseñar el lease interno del worker → rebuild del bundle).
3. f7af (limpieza `vehicle_release` cercada): recomendación del par, eximir del cerco las órdenes
   internas de limpieza del daemon. Misma decisión de contrato.
4. 6927: tratar un DayZDiag vivo en el puerto pedido como caja OCUPADA aunque no tenga ficha, y que
   `wait_for_box_s` espere en vez de limpiar. Es pérdida de datos ajena → R9 (rigorous-data-audit).

## B000. LOTE D CERRADO (19:2x): 6927 RESUELTA salvo gate in-game — commit `4e227a3`
- Producción: ocupación de la caja por PUERTO (tabla UDP del sistema, imagen por ToolHelp, `foreign` por imagen DayZ,
  `ports_in_use`/`foreign_ports` del SO, `port_scan_known`/`port_scan_reason`), rechazo con sondeo fresco en admisión Y justo
  antes del launcher (`port_in_use_foreign` / `port_attribution_unknown` / `port_scan_unknown`), `admin --empty` con testigo UDP,
  `wait_for_box_s` falla en el acto si la tabla es ilegible, diagnóstico al cliente con `reason`/`port`/hint, `blocked_on` honesto,
  `diag_probe` con `DayZServer_x64.exe`. R9: 3 rondas Codex (1 CRITICAL + 8 MAJOR en R1 → todo aplicado o documentado; R3 = 0).
- **Backlogs documentados que son decisión tuya**: (1) la ventana entre la última lectura y el bind de DayZ no tiene reserva de
  puerto (cerrarla exige verificación post-launch de propiedad del puerto + liquidar el propio intento por el guard);
  (2) verbo TTY de reconciliación por puerto para un heredero huérfano del socket (antes invisible, ahora bloquea con
  visibilidad); (3) `port_probe=None` = feature ausente (fail-open en constructores futuros); (4) test de wire de
  `port_scan_known`. Detalle: `reviews/2026-09-04-cierres-menores/lote-D/RECIBO-D.md` y los AUDIT-*.md.
- **Gate in-game (tuyo)**: servidor ajeno en 2302 → `dayz_test_run` devuelve `active_run_exists` con `reason: port_in_use_foreign`
  y `session_status.box.foreign` con `source: port`; `wait_for_box_s` espera. El daemon vivo corre el lote tras su re-spawn.
- cabd: mitad MCP cubierta por el lote D; mitad LFHeli en manos de Reserva (guard `Get-NetUDPEndpoint` + `port=` en
  BATCH-SUMMARY; `pbo_sha256` pendiente de que digas QUÉ PBO).

## B00. Reparto 16:55 (aviso del par, «por encargo de Guillermo») y estado del lote D
- El resto del buzón (22 fichas) pasa a la sesión **Reserva MCP bugfixing** (`local_e84a1b8e-8577-4f17-917a-b7dd6fbf8054`), con
  brief en `DayZ_MCP_dev/reviews/2026-09-04-reparto-buzon-reserva.md`. Conmigo quedan **6927** (lote D) y **cabd**. Reserva
  espera mi commit del lote D antes de tocar process_lifecycle.py / daemon.py / dayz_test_tool.py (sus fichas: 76dd, d60f,
  8f76(c), 4407, 9d46+05.3). Protocolo: al commitear, send_message a Reserva con sha + write-set.
- **Lote D (6927)** implementado y en revisión: Codex de corrección + 3 auditores R9 en paralelo desde 16:45 (review-D/).
  Producción sin commitear en 5 ficheros + 2 tests. Control positivo vivo: el run 8e3d4d22 (sesión WRX, pid 45428) aparece en
  la tabla UDP 2302/2304 como registrado → `ports_in_use`, no `foreign`.
- **cabd** (LFHeli `run_batch_f1.ps1` vs MCP en el 2302): la mitad MCP la cubre el lote D (no se lanza encima de un puerto
  sostenido; `foreign` lista un DayZ ajeno por imagen). La mitad LFHeli se DEVUELVE a Reserva con propuesta: antes de
  `& $serverWrap -Port $Port …` (línea ~229) un guard `Get-NetUDPEndpoint -LocalPort $Port` que lance con PID/imagen si está
  sostenido; `port=$Port` en BATCH-SUMMARY (línea ~392); `pbo_sha256` en `batch_manifest.json` (línea ~217) **cuando se decida
  QUÉ PBO** (el script carga @LFHeliCore/@LFHeli_OH1/@LFHeliTestVariants). LFHeli_dev no es repo git: escritura OneDrive con
  read-after-write. No lo toco yo: herramienta de otro proyecto, sin arnés de prueba, con lotes nocturnos que dependen de ella.
- 2762 y f4f2 (in-game), 668f (Pack + 17 copias) y 077d: siguen como estaban (ahora en la lista de Reserva salvo que Guillermo
  diga otra cosa).

## B0. Cambios de estado de la tarde (15:45, tras las decisiones del picker)
- CERRADAS: 8fac, 21bc, 5ca4 (LL-448..450 escritas; LL-451 = Cursor a ciegas), 344d (ya corregida en LFPowerGrid por
  2e8ef7d el 08-29; gate verde ejecutado; `build_guarded.py` +43 y `test_version_gate.py` siguen sin commitear en
  LFPowerGrid_dev como trabajo ajeno), 0fde (CONTRIBUTING.md fue reescrito HOY por el Pack 94a6da3 en la dirección del hook;
  al hook le faltaba el paso «resellar source-map.json» → ~/.claude b5add82; memoria skill-edits-delivered-as-package
  corregida). EN VUELO (lote B, Codex revisando): 251d (test «mismos bytes», ambos brazos) y 5930 puntos 1/2/4 (descripciones
  de entities_query, logs_since, capture_screenshot). 5930 punto 3 (el lease caduca por inactividad y devuelve el run a
  RUNNING_IDLE) → enviado al par como anexo de f70b/92e5 (lote J).
- **2762 y f4f2 (29 y 33) YA ESTÁN HECHAS EN FUENTE** por el lote M05 (commit 117730b): eco `ui_request`
  (`requested_path/root/text`, `matched_path`; MCPClientBridge.c:2199-2218, MCPMessages.c:415-419) y cerco por `root` con
  `ambiguous_path`/`widget_not_found` sin elegir nunca el primer homónimo (:2042-2129). Solo les falta el gate in-game (PBO
  desplegado + un `ui_click` sobre `BtnCloseX` con y sin `root`): siguen en el cubo D.
- **b2c4 (28)**: la mitad Python sí es offline: `_bridge_error` (server.py:647) tiraba `handler`/`user_id`/`clicked` y el eco.
  Parche preparado (scratchpad/aplicar_b2c4.py: el código sigue siendo la cabeza del mensaje, los campos van detrás de «; »
  solo cuando el resultado los trae; test nuevo tests/test_ui_error_diagnostics.py con caso de wire). Se aplica tras el
  dictamen de Codex B (no tocar server.py mientras audita el diff) y va a una revisión Codex propia. Queda su gate in-game.
- **668f (32)** — dayz-test.ps1 «[ok] deployed» con el PBO viejo: la plantilla del Pack
  (`skills/dayz-test-ingame/templates/dayz-test.ps1:537-542`) sigue comprobando solo existencia, y hay **17 copias** en
  `<Mod>_dev/tools/` (versiones distintas: LFQuad2 :480, plantilla :537). Propuesta: (1) en `Invoke-Build`, sha256 del PBO
  antes/después y morir si no cambió; (2) parsear «Build failed»/«[ERROR]» del log de AddonBuilder además del exit code;
  (3) preflight: avisar si hay DayZDiag_x64 vivo con `-Build`. Es edición de skill gobernada (circuito Pack: editar
  `skills/…`, resellar source-map, validate, commit, promote) MÁS 17 copias por mod → decisión tuya (alcance: ¿plantilla
  sola, o plantilla + regenerar/parchear copias?). No tocado.

## B. Decisiones de producto/infra ya conocidas (sin cambio desde el handoff de las 06:15)
- 19 `344d` LFPowerGrid 1.2.3↔1.2.4: qué dirección.
- 0fde `promotion-gate.ps1` vs CONTRIBUTING.md: cuál de los dos textos manda.
- 077d receta `claude -p` con `CLAUDE_CONFIG_DIR` vacío (Not logged in): el sustituto crea `~/.claude/.claude.json`.
- 26 `d366` + 07 `9b7b`: M23 entero (promotor de esquema efectivo + autoridad + CAS/journal), XL.
- 2bd3: transición `replace` del registro de launchers (persistencia encadenada → R9).
- df93: preflight VPP en la ruta secure_launcher (worker empaquetado + in-game).
- 1f85: el workspace de runtime vive dentro de la skill dayz-test-ingame (skill edit).
- cabd: `run_batch_f1.ps1` de LFHeli y el servidor MCP se pisan el 2302 (mismo remedio que 6927.4).
- 76dd: daemon idle-restart mata runs entre sesiones (daemon.py; el par lo dejó en «lote H» sin cerrar).
- d60f: `dayz_test_stop` deja UNRECONCILED + caja bloqueada si el cliente cuelga al arrancar (in-game).
- 8f76: capture_screenshot exige foco; forzarlo tumba al cliente (in-game; (a) frame_sha256 ya entregado en M).
- 4f83 `restore_gameplay` ok:1 incondicional (Enforce + in-game) · bd90 centinela MCPBridge.c (se recongela tras el canario).
- 251d (10): T1-T5 `lookback_lines` 200→0: tests existen; falta el brazo 0 sobre los mismos bytes (pequeño, siguiente lote).
- 141e (18) censo de 54 tools: contrato no auditable leyendo el cuerpo → es M23/instrumento (con 26/07).
- 9d46 (31): copias de modos en `dayz_test_tool.py` (parche entregado al par; el worker empaquetado exige rebuild).
- 05 8f8c punto 3: `bad_dayz_test_request` sin motivo → `dayz_test_request.py` está en el contrato del bundle (rebuild).

## C. Lecciones (LL-445..448 redactadas en scratchpad/LL-borrador.md; sin tu OK no se escriben)
- 8fac, 21bc, 5ca4 quedan abiertas hasta que apruebes las LL o las descartes.

## D. In-game con Guillermo (doce): 01, 05(1 y 4 de este lote), 15, 18, 20, 25, 28, 29, 30, 32, 33, 34.
