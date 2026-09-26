# Prompt de arranque — sesión siguiente (2026-07-29)

> Copiar el bloque entre marcadores como primer mensaje de la sesión nueva.

===== PROMPT INICIO =====

Retomas DayZ_MCP como orquestador. Declara al empezar:

"Retomo DayZ_MCP desde: 7 fixes de Enforce verificados EN EL JUEGO y suite en 1265/1F/0E (ese rojo
es ambiental), lote Python REVERTIDO por una regresión de lease-first, y BUG-067 (P1) recién abierto
· próxima acción: BUG-067, que es el único que está haciendo daño ahora."

CARGA INICIAL — sólo estos cuatro, en este orden:
1. C:\Users\guill\ObsidianVault\AI\00_System\workflow.md
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md (bloque LIVE-STATE)
3. C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-07-28-DayZ_MCP-rebuild-bundle-rollout-cas.md
4. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md (sólo las fichas BUG-066 y BUG-067)

TU PAPEL: orquestas y verificas. La implementación se delega (G7). Verificar = correr tests y
comprobar ficheros host-direct. Escribir el deliverable delegado = jamás tuyo; si el ejecutor muere a
mitad, se relanza, no se remata.

SECUENCIA, un deliverable por sesión, en este orden:

1. **BUG-067 (P1) — el arranque pisa runs ajenos no registrados.**
   Verificado en código: `active` sale sólo del manifest (`process_lifecycle.py:813`) y
   `dayz_test_worker.py` NO scanea procesos. Un servidor lanzado fuera del lifecycle es invisible y
   se pisa en silencio; uno lanzado vía `dayz_test_run` sí se rechaza correctamente.
   **NO aceptes el diagnóstico "basta con que el run pase por la cola"**: la cola de la Fase 2 se
   alimenta del mismo manifest y no cerraría esto.
   Empieza por la **mitigación mínima fail-closed** (detectar DayZ vivo no registrado y RECHAZAR en
   vez de pisar), que es separable del fix de fondo. Toca lifecycle -> pregunta al usuario si exige
   `rigorous-data-audit` (DZ-R9) antes de ir más allá de la mitigación.

2. **Rehacer el lote Python** (BUG-024/025/026/037/039).
   Todo el material está ÍNTEGRO en `%TEMP%\dayz-mcp-bundle-rebuild\_reverted-python-tanda`
   (5 módulos + su suite). **No se re-aplica tal cual**: BUG-037 introdujo una regresión de
   seguridad — propagó `operation_timeout_s` al comando y el campo pasa a validarse ANTES del lease,
   rompiendo *lease first* (`test_session_http.py:292` esperaba `423 lease_required` y recibía
   `400 bad_operation_timeout`). El encargo nuevo debe llevar **lease first como criterio explícito y
   verificado**. Los otros cuatro fixes estaban bien.

3. **BUG-066 (c) + los gates in-game pendientes**, todos en una sesión con la caja levantada (DZ-R5).
   - BUG-066 (c): `declared_slots` devuelve `[""]` in-game; el objetivo ("este vehículo no debería
     tener slot de capó") sigue sin ser medible. Toca Enforce -> rebuild PBO + gate.
   - BUG-061 cadence (`19.96890272042031 Hz < 20`; no rebajar, redondear ni sintetizar).
   - `OnContact` owner-client: el source-contract no acredita el lado de dispatch.
   - Gate de captura con `MAX_MCP_OUTPUT_TOKENS=75000` (imagen ~1010 px, vigilar el 400-brick de webp).

4. **Fase 2 + BUG-067 de fondo, DISEÑADOS JUNTOS.**
   Son el mismo problema por dos caras: *quién tiene derecho a arrancar un run*. La Fase 2 ordena a
   quien pasa por el lifecycle; BUG-067 es que hay quien no pasa. El plan v2 está en
   `plans\2026-07-28-fase2-run-queue-orden-estable.md` y ya declara que NO cierra BUG-067.
   **Nudo sin adjudicar, es del usuario**: el TTL de la cabeza de cola es *indecidible* con lo que
   lifecycle sabe — `touched_at` sólo se actualiza cuando el cliente consigue el lease, pero un
   cliente vivo esperando el lease no puede actualizarlo, así que "120 s sin tocar" significa a la vez
   *muerto* y *esperando turno*. Las dos ordenaciones posibles fallan (verificado con dos R21: expirar
   antes = la cabeza viva se autoexpira; renovar antes = la cabeza muerta resucita). Dirección
   propuesta sin implementar: medir la vida del waiter contra algo que sí pueda renovar sin lease
   (p. ej. una espera activa suya en el coordinador; `session_coordination.py` se puede LEER, lo que
   BUG-046 prohíbe es modificarlo). **Segundo bloqueante**: `mode=all` hace DOS `lifecycle_start`, así
   que el ticket se consume en el primero y la segunda fase queda detrás de otro cliente.

NO ABRAS sin decisión explícita del usuario:
- Fases 3-6: tocan `runs.json` -> exigen DZ-R9 (`rigorous-data-audit`).
- El frente del Knowledge Pack: la regresión vive en la rama `r21/phase01-foundation`, no en el
  despliegue; `ac21d13` sigue sin mergear e invirtió 4 tests de fail-closed a permitido.
- `vehicle_trace` (P7): su premisa de "caja idle" hay que reverificarla.

REGLAS OPERATIVAS VERIFICADAS — respétalas o repetirás errores ya pagados:
- **BOM**: escribe con `python open(path,"w",encoding="utf-8").write()`. `Set-Content -Encoding utf8`
  (PS 5.1) añade `EF BB BF` SIEMPRE; en Enforce eso impide compilar y el síntoma engaña
  (`CParser: quoted string not closed on line 1`, sin ninguna comilla suelta). La skill
  `codex-handoff-template` ya está corregida.
- **Enforce no tiene compilador offline**: si la tanda toca `.c`, el gate in-game ES la verificación.
- **Las fichas del ledger anteriores al refactor de julio citan anclas MUERTAS.** Tres casos en un
  día: BUG-064 (`_cmdline_runs_module`), BUG-044 (`should_reclaim_listener`), BUG-039
  (`build_daemon_argv` ya no está en `daemon.py`). Verifica el ancla ANTES de delegar.
- **`P:` puede caerse sola** (es un `subst`). Síntoma engañoso: el build del bundle falla con
  `identity_open_failed:P:\Utopia_PC`, que parece de código. Remontar:
  `subst P: "C:\Users\guill\OneDrive\Documentos\DayZ Projects"`.
- **NUNCA encadenes el rollout detrás del build sin comprobar su exit code.** Si el build falla y el
  `rollback-last` corre igual, el registro queda VACÍO y el sistema sin launcher aprobado.
  Recuperación: `install-dayz-test-v1 --expected-sha256 330B04E8…` (sha del registro vacío).
- **Rollout = `rollback-last` + `install`**; `--expected-sha256` es el sha del REGISTRO, no del PE.
  Cualquier cambio en uno de los 13 `PACKAGED_MODULES` o en `src/app_main.py` exige repetir el ciclo;
  `checks\check_native_launcher_registry.py` lo detecta solo.
- **El rebuild del bundle NO se delega**: bundle y registro clavados a rutas absolutas de OneDrive.
- **Workspace de Codex SIEMPRE en `%TEMP%`**, replicando `DayZ Projects\{DayZ_MCP, DayZ_MCP_dev}`
  (los tests aseveran por `parents[2]` y `parents[3]`), copiando `reports\security\`, y parcheando
  el `MAPPING` del finder meta-path (`__editable___dayz_mcp_tools_0_0_0_finder.py`, 2 ocurrencias
  escapadas). Comprueba `<pkg>.__file__` dentro del workspace ANTES de delegar.
- **Un workspace delegado envejece**: diff fichero a fichero antes de aterrizar; aterriza sólo el
  alcance declarado. (El de la Fase 1 divergía en 16 ficheros de los que sólo 2 eran suyos.)
- **Para vigilar a Codex usa el PID o el lock de escritura, NUNCA marcadores de texto**: su log
  incluye los ficheros que lee, y el HANDOFF documenta esos marcadores -> falsos positivos.
- **El guard de PowerShell bloquea `Remove-Item`/`Clear-Content`/`Set-Content`** si en el mismo
  comando hay un token que parezca ruta (`/E`, `/c`, `~`, `/`, `P:`). Sepáralo o hazlo en Python.
- **Cuatro módulos NO deterministas** bajo carga, exclúyelos de cualquier gate:
  `test_bug046_startup_deadlock`, `test_task7_review_regressions`, `test_bug046_audit_fault_recovery`,
  `test_client_mode`.
- **`baseline_manifest.json` de la Fase 0 NO sirve como gate de regresión** (52 de sus 93 rojos son
  perfil de sandbox).
- Antes de tocar `dayz_mcp\*.py`: avisa (desarma las tools MCP de las sesiones vivas). Reiniciar el
  daemon es seguro desde BUG-062(b): validado en vivo el 2026-07-28.
- No re-registres el MCP con `install-mcp.ps1 -Register` para cambiar un flag: hace remove+add sin
  setear los timeouts de 7 días que sostienen las FIFO.
- No arranques DayZ, no adquieras lease y no inicies live sin autorización explícita.

ESTADO QUE NO DEBES REHACER (verificado host-direct el 2026-07-28):
- **7 fixes de Enforce** aterrizados, PBO desplegado (`131.843 B`) y verificados EN EL JUEGO:
  BUG-029 (versión completa `6~1.29.163451`), BUG-030 (`ver=` con reservados), BUG-011
  (`radius=999999` -> `bad_args`), BUG-028 (marcador `float.MAX`), BUG-066 a/b (`attachment_items`,
  `cargo_items`, `items_total`, `items_truncated`) con `items` conservado.
- **BUG-044 CERRADO sin código** (ya no reproducía; el discriminador actual es más fuerte que el fix
  propuesto). **BUG-064 cerrado.** **Q1/Q2/Q3 RATIFICADAS** -> `decisions/decision-log.md` D-32, con
  dos condiciones vinculantes (atomicidad en Q2, staleness por hash en Q3).
- **Hashes del bridge re-congelados** en `test_task9_spawn_phase_markers`, tras verificar que los
  cuatro marcadores de fase seguían intactos.
- **Bundle**: `app.pyz D622C0FB…`, PE `5C633683…`, registro `498B8DF3…`, check OK.
- **Suite**: 1265 / 1F / 0E / 4S. El único rojo, `test_capture_window_not_found_returns_is_error`, es
  ambiental (espera `window_not_found`, recibe `capture_timeout` sin ventana de juego abierta).

===== PROMPT FIN =====
