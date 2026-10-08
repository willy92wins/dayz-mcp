# Triage 3 — DayZ-MCP `ba07cca`

**Ronda propuesta: corregir la sincronización de T7, documentar T1, reparar el procedimiento de T4 y actualizar #195 con una puerta sobre el PBO.** Ningún ticket de comportamiento en juego queda cerrado por esta lectura.

Árbol: `.TREE_SHA:1`:

```text
ba07cca2aa680b5d23366f842504f721dc880319
```

Solo lectura: no ejecuté tests, builds ni DayZ; no escribí archivos ni accedí a `%LOCALAPPDATA%\DayZ_MCP*`. Las pruebas siguientes son propuestas, no resultados.

## T1 — c440

**VERDICT: OPEN_CONFIRMED** para las solicitudes pendientes; **[HIPÓTESIS]** que la falta de foco cause el streaming lento.

**EVIDENCE.** `tools/dayz_mcp/server.py:7503-7504` distingue repetición de píxeles de errores:

```python
        "stored state and are therefore there on the very first capture. A repeated frame is a fact about pixels, not an error: a paused sim, an open menu "
        "and a still scene all produce it legitimately. "
```

Por tanto, `frame_stale=false` tampoco certifica texturas cargadas. El contrato reconoce otra limitación, `server.py:7498`:

```python
        "An unfocused DayZDiag client renders at about 20 fps, so client-side timing depends on which window owns the foreground. "
```

El reconnect existente es restringido, `server.py:4717-4722` — dos extractos:

```python
            "takeover=true, except an explicit mode=client with that run_id, "
            "a matching project and RUNNING_IDLE: that call replaces only the "
            "client and does not stop the server. A public session prefix "
```

```python
            "with the full session id. A client that is still polling is "
            "client_already_polling. "
```

**PROPOSED FIX.**

1. **S**, `server.py`: documentar el reporte en captura y teleport; ninguna respuesta certifica streaming.
2. **M**, `mcp_capture.py`: evaluar un aviso opcional de teselas oscuras. El umbral 0,2 es una contribución sin calibración aquí; no convertirlo en `textures_ready`.
3. **M**, lifecycle/contrato de lanzamiento: diseñar reconexión explícita del cliente propio mediante guard, conservando servidor y autorización.

**FLAGS:** documentación/diagnóstico SEALED no, ENFORCE no; reconexión SEALED sí si modifica el worker sellado, ENFORCE no. **TESTS:** metadata publicada; borrar la advertencia debe dar rojo. Para el detector: escenas texturadas oscuras y escenas sin texturas; devolver siempre “listo” debe fallar.

**RISK / NOT VERIFIED:** falsos positivos visuales; no inspeccioné la evidencia Baltic ni comparé foco/no foco.

**INBOX ACTION:** mantener abierto. Decisión recomendada: **documentación + experimento**; alternativas: diseñar reconexión ahora o aplazar diagnóstico visual.

## T2 — 75e7 + fade / f298

**VERDICT: PARTLY_FIXED.**

**EVIDENCE.** `tools/mcp_capture.py:1373-1376`:

```python
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NO_WINDOW", 0x08000000
        )
```

`tools/dayz_mcp/process_lifecycle.py:2656-2660`:

```python
        elif os.name == "nt" and window_style == "normal":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = SW_SHOWNOACTIVATE
            kwargs["startupinfo"] = startupinfo
```

La hipótesis original sobre `launcher.cpp` mezcla procesos: `src/launcher.cpp:1236-1237` selecciona Python:

```cpp
    } else if (!BundlePath(L"runtime\\python.exe", application, ARRAYSIZE(application)) ||
        !BundlePath(L"app.pyz", app_archive, ARRAYSIZE(app_archive)) ||
```

Su creación todavía carece de `CREATE_NO_WINDOW`, `launcher.cpp:1337-1340`:

```cpp
        created = CreateProcessW(
            application, command, nullptr, nullptr, TRUE,
            CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED,
            environment, cwd, &startup.StartupInfo, &process);
```

**PROPOSED FIX:** primero sonda pasiva sobre el build resellado, identificando DayZ, Python y hosts de consola. **[HIPÓTESIS]** residual: consola del worker nativo. Si se confirma, **S**, `launcher.cpp`, ocultar esa consola preservando pipes/job.

**FLAGS:** candidato SEALED sí, ENFORCE no. **TESTS:** `test_fb_f298_launch_focus` más gate nativo; retirar el flag debe dar rojo. El cierre exige medir foco real.

**RISK / NOT VERIFIED:** Enfusion puede activar ventanas independientemente de los flags; ninguna medición actual.

**INBOX ACTION:** mantener 75e7; vincular fade como evidencia del mismo problema. No cerrar por tests de kwargs.

## T3 — 86a3

**VERDICT: OPEN_CONFIRMED.**

**EVIDENCE.** `server.py:6458-6460`:

```python
            f"{LEASE_TOOL_LINE} Apply one ONE_FRAME aim change on the local "
            "player through OverrideAimChangeX and OverrideAimChangeY. No OS "
            "input and no focus. The override protos name no unit. The result "
```

La observación de visibilidad ya existe, `addon/scripts/5_Mission/MCPClientBridge.c:4354-4355`:

```c
        node.visible = w.IsVisible();
        node.visible_hierarchy = w.IsVisibleHierarchy();
```

Referencia vanilla auxiliar: `scripts/5_mission/gui/actiontargetscursor.c:752`:

```c
        m_Target = m_AM.FindActionTarget();
```

**PROPOSED FIX [DESIGN]:** **M**, lectura `action_cursor` desde el gestor real, con identidad del objetivo/componente y visibilidad efectiva; conservar `ui_tree`. Orientar hacia coordenadas requiere investigación independiente: no asumir unidades de `OverrideAimChangeX/Y`.

**FLAGS:** SEALED no, ENFORCE sí. Archivos: puente, mensajes y `server.py`. **TESTS:** objetivo protegido/no protegido/fuera de alcance y cursor vacío; devolver siempre el objetivo solicitado debe dar rojo.

**RISK / NOT VERIFIED:** leer el cursor no orienta al jugador ni satisface por sí solo S7.

**INBOX ACTION:** mantener. Opciones: **investigar orientación y usar `ui_tree`**; añadir primero lectura del cursor; aplazar.

## T4 — 9941 + d1a8 + 9ab8 + 01d3

**VERDICTS:** 9941 **OPEN_CONFIRMED**; d1a8 **DUPLICATE_OF 9941**, conservando el caso repetitivo; 9ab8 **PARTLY_FIXED**; 01d3 **OPEN_CONFIRMED**.

**EVIDENCE.** Acción genérica publicada, `server.py:8208-8209`:

```python
    async def action_use(
        action: str,
```

Su límite está explícito, `server.py:8204-8206`:

```python
        "tool does not sustain continuous-action input or wait for progress "
        "completion. Verify the intended effect separately; client callback "
        "reachability by code is not an in-engine test of your mod."
```

Persiste el falso negativo, `MCPClientBridge.c:3959-3963`:

```c
        if (amc.GetRunningAction() == null)
        {
            result.started = false;
            result.ok = false;
            result.error = "setup_failed";
```

`reviews/2026-10-06-ticketing/ingame-v11/RESULTS.md:48-51`:

```text
2. 9941 PROCEDURE.md names FenceKit with ActionPlaceObject; kits use ActionDeployObject (kitbase.c:150-151, 5 s),
   ActionPlaceObject belongs to Container_Base and others (container_base.c:52-53). With a WoodenCrate no placement
   hologram was observed after three toggles (with and without camera_set aimed at the ground); ActionPlaceObject stayed
   condition_failed. H1 untested.
```

**PROPOSED FIX:** **S**, corregir el procedimiento externo `probe_9941/ws/PROCEDURE.md`: FenceKit + `ActionDeployObject`, toggle `target=hands`; control/suppress/cancel, nueva identidad de Fence, inicio atribuible, restauración y terminal. Trials nunca ejecutados: **orchestrator fact**.

**M [DESIGN]**, falso negativo: observar el intento instantáneo en el gestor; no sustituir simplemente el error por `started=true`, porque SetupAction también puede fallar. Archivos: puente/mensajes y observador cliente.

**FLAGS:** SEALED no; ENFORCE sí para observador y experimento con PBO de prueba. **TESTS:** `test_action_use`, caso instantáneo exitoso y setup rechazado; invertirlos debe dar rojo. Hold: control negativo, cancelación sin Fence nuevo y prueba repetitiva de bandera.

**RISK / NOT VERIFIED:** suppress es hipótesis, no hold demostrado; las pruebas genéricas LFPowerGrid tampoco quedan acreditadas.

**INBOX ACTION:** mantener 9941, 9ab8 y 01d3; consolidar d1a8 conservando sus criterios. Respetar la investigación aprobada.

## T5 — 6ed1

**VERDICT: PARTLY_FIXED.** La ventana pendiente es apropiada, con quiescencia y cierre guardado: **orchestrator fact**. Solo quedan tres copias CRLF; launcher y otros cuatro archivos ya son LF. LICENSE `-text` es intencional.

**EVIDENCE.** `.gitattributes:4,8`:

```gitattributes
* text=auto eol=lf
```

```gitattributes
tools/vendor/** binary
```

El lock normaliza, `tools/write_packaged_modules_lock.py:41`:

```python
    return data.replace(b"\r\n", b"\n")
```

Pero el empaquetado toma bytes crudos, `tools/build_native_launcher.py:377-378`:

```python
    for name in PACKAGED_MODULES:
        files[f"dayz_mcp/{name}"] = (TOOLS_DIR / "dayz_mcp" / name).read_bytes()
```

**PROPOSED FIX: S; SEALED sí; ENFORCE no.** Normalización selectiva, sin renormalizar todo el índice. Comandos propuestos para el dueño, **no ejecutados**, desde el árbol vivo ya actualizado y sin escritores concurrentes:

```powershell
$py = (Resolve-Path 'tools/.venv-mcp/Scripts/python.exe').Path
$eolFiles = @(
 'tools/checks/check_readme_cites.py',
 'tools/dayz_mcp/win32_fileinfo.py',
 'tools/tests/test_d40_marker_rewound_reads_only_the_tail.py'
)
@'
import pathlib, subprocess, sys
for p in sys.argv[1:]:
    work = pathlib.Path(p).read_bytes()
    index = subprocess.check_output(["git", "show", ":" + p])
    head = subprocess.check_output(["git", "show", "HEAD:" + p])
    if work.replace(b"\r\n", b"\n") != index or index != head or b"\r" in index:
        raise SystemExit("STOP: contenido o indice distinto: " + p)
'@ | & $py -B - @eolFiles
if ($LASTEXITCODE) { throw 'Precheck EOL fallido' }
git -c core.autocrlf=false checkout-index --force -- @eolFiles
if ($LASTEXITCODE) { throw 'Checkout EOL fallido' }
git ls-files --eol -- @eolFiles
```

Exigir `i/lf w/lf`; conservar contenido/índice ajenos. `git add --renormalize` no reescribe las copias y aquí no hace falta.

Desde `tools`, cada comando debe salir 0 antes del siguiente:

```powershell
Set-Location -LiteralPath tools
function CheckedPython {
 & $py -B @args
 if ($LASTEXITCODE) { throw 'Comando fallido; detener promocion' }
}
$registrySha = (Get-FileHash -LiteralPath approved-launchers.json -Algorithm SHA256).Hash
CheckedPython write_packaged_modules_lock.py --check
CheckedPython build_native_launcher.py --offline --verify-reproducible
CheckedPython -m dayz_mcp.launcher_registry_update replace-dayz-test-v1 --expected-sha256 $registrySha
CheckedPython checks/check_native_launcher_registry.py
CheckedPython write_packaged_modules_lock.py --check
```

Firmas: `build_native_launcher.py:1767-1769`:

```python
    parser.add_argument("--output", type=Path, default=CANONICAL_BUNDLE)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--verify-reproducible", action="store_true")
```

`launcher_registry_update.py:796-797`:

```python
    replace = commands.add_parser("replace-dayz-test-v1")
    replace.add_argument("--expected-sha256", required=True)
```

**TESTS / RISK / NOT VERIFIED:** seal/bundle/lock y comparación cruda pyz↔fuente; introducir CRLF debe fallar el gate crudo aunque pase el lock normalizado. No probé comandos ni caché offline. Backup verificable del bundle/registro y rollback antes del build.

**INBOX ACTION:** mantener hasta resellado, comprobación y arranque; después resolver citando receipt y evidencia EOL.

## T6 — ce72

**VERDICT: NOT_IN_MCP** para el residual de HKCU; protección de hijos MCP presente.

**EVIDENCE.** `child_environment.py:51-55`:

```python
    return {
        key: value
        for key, value in os.environ.items()
        if key.casefold() in _WHITELIST_CASEFOLD
    }
```

`process_lifecycle.py:2652` y `steam_preflight.py:535`:

```python
            "env": whitelisted_child_environment(),
```

```python
            env=whitelisted_child_environment(),
```

**PROPOSED FIX:** ninguno MCP; SEALED no, ENFORCE no. **TESTS:** mantener pruebas de entorno con secretos sintéticos; heredar `os.environ` debe dar rojo.

**RISK / NOT VERIFIED:** procesos existentes/manuales y CrashReporter; no inspeccioné registro ni entornos.

**INBOX ACTION:** resolver la parte MCP con esas rutas; residual al dueño: **carga por proceso**, aceptar riesgo o investigar reporters. No cerrar el riesgo del host como corregido.

## T7 — 33ed

**VERDICT: PARTLY_FIXED.**

**EVIDENCE.** Bug046 ya publica atómicamente, `test_bug046_startup_deadlock.py:1271-1273`:

```python
    staged = signal.with_suffix('.tmp')
    staged.write_text('1' if elected else '0', encoding='ascii')
    staged.replace(signal)
```

Coordinación espera solo memoria, `test_coordination_audit_faults.py:1684-1686`:

```python
                coordinator._condition.wait_for(
                    lambda: not coordinator._handoff_pending, timeout=1.0
                )
```

La persistencia posterior libera el lock, `session_coordination.py:2792-2794` y `3619-3623`:

```python
                            self._clear_handoff_pending_locked()
                            if not self._persist_snapshot_locked():
                                self._handoff_audit_failed = True
```

```python
        snapshot = self._snapshot_payload_locked(persist_provisional_grant=True)
        self._condition.release()
        try:
            try:
                return self._persist_snapshot(snapshot) is not False
```

Esto permite leer el snapshot anterior: mecanismo confirmado por código.

**PROPOSED FIX: S**, solo test de coordinación: evento tras escritura real del snapshot terminal y espera acotada; forzar intercalado bloqueando esa escritura.

**FLAGS:** SEALED no, ENFORCE no. **TESTS:** ambos casos + fast tier completo; quitar persistencia terminal o restaurar la espera prematura debe dar rojo.

**RISK / NOT VERIFIED:** no atribuir todo rojo futuro a ruido; no demostré pérdida de datos ni estabilidad de suite.

**INBOX ACTION:** mantener coordinación; registrar bug046 como corregido en main, sin reclamar repro bajo carga.

## T8 — bf5c + 8cf9 + a97e; 713a / 66cc

**VERDICTS:** bf5c/8cf9 **OPEN_CONFIRMED**; a97e **NEEDS_REPRO**.

**EVIDENCE.** Worker transmite source directo, `dayz_test_worker.py:1204-1206`:

```python
                        "source": source,
                        "target": target,
                        "temp": temp,
```

El cierre de `BuildAddonCommand`, `launcher.cpp:1046-1050`, no incorpora `-project`:

```cpp
           AppendText(command, command_capacity, L" \"-temp=") &&
           AppendText(command, command_capacity, request.temp) &&
           AppendText(command, command_capacity, L"\"") &&
           (!request.clear || AppendText(command, command_capacity, L" -clear")) &&
           (!request.pack_only || AppendText(command, command_capacity, L" -packonly"));
```

El gate actual solo comprueba salida/PBO cambiado, `launcher.cpp:1117-1119`:

```cpp
    PboSnapshot after{};
    bool valid = exit_code == 0 && CapturePboSnapshot(pbo_path, false, &after) &&
                 PboSnapshotChanged(before, after);
```

**713a afecta la ruta de invocación del worker. [HIPÓTESIS]** que su PBO concreto tenga materiales vacíos/tangentes perdidas: requiere inspección ODOL.

Conflictos de #195 y build de P:\SimpleGroup en 251 s: **orchestrator fact**. Diff auxiliar `../pr195.diff:405-406`:

```diff
+        staging = (
+            contextlib.nullcontext(source) if pack_only else stage_build_source(source)
```

**PROPOSED FIX: M**, actualizar #195 sobre main: portar staging dentro del lock compartido actual; preservar cancelación, errores y attestation; reconciliar docs y regenerar lock desde el resultado, sin recuperar hashes antiguos.

**FLAGS:** SEALED sí, ENFORCE no. **TESTS:** staging/junctions, cancelación, escapes, pack-only y locks actuales. Source directo debe fallar el control con config hermano venenoso. Build real aislado debe producir modelos con materiales/tangentes válidos; controles negativos: padre contaminado y PBO roto de 713a. Si falla resolución, ampliar diseño a raíz de unidad `-project`, incluyendo broker/protocolo.

**RISK / NOT VERIFIED:** #195 no demuestra por sí solo seguridad de materiales. Repro a97e con contenido/flags iguales y salida scratch; no adoptar workaround prematuro. 66cc aporta una precaución, no determinismo demostrado aquí: conservar y desplegar el PBO exacto probado.

**INBOX ACTION:** mantener; cerrar bf5c/8cf9 tras merge y gate real. a97e permanece abierto.

## T9 — 7695

**VERDICT: OWNER_DECISION.**

**EVIDENCE.** Timeline actual va al cliente, `server.py:7854-7857`:

```python
            raw_result = await runtime.call_bridge(
                "anim_timeline",
                args,
                "client",
```

No sustituye FK offline. La contribución auxiliar tiene rutas locales, `CocaLab_dev/tools/pour_fk.py:29-31`:

```python
DZ = r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DZ"
DAYZATOOL = os.environ.get("DAYZATOOL", r"C:\Users\guill\Downloads\DayZATool_v1.3\DayZATool.exe")
SEANIM_READER = r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\A6_SR2M_dev\tools\anim-pipeline"
```

**PROPOSED FIX:** **M**, adaptar contribución como CLI portable con consumidor ejecutable y contrato de resultados; decidir después el verbo. SEALED no, ENFORCE no.

**TESTS:** cadena FK analítica, unidades, jerarquía inválida y extracción fallida; alterar multiplicación/unidades debe dar rojo.

**RISK / NOT VERIFIED:** mezclas/IK y orientación absoluta; no ejecuté extractor.

**INBOX ACTION:** **conservar CLI y concretar consumidor** recomendado; alternativas: financiar verbo con aceptación concreta o aplazar. CocaLab demuestra utilidad, no necesidad adicional del envoltorio MCP.

## T10 — 1d31 + d490

**VERDICTS:** 1d31 **PARTLY_FIXED**; d490 **NEEDS_REPRO**.

**EVIDENCE.** `server.py:7254-7258`:

```python
        "the mission handler only; for game-level key handlers "
        'use input_trigger(kind="key", dik=1, entry="game", phase="click") '
        "instead. A delivered callback is not confirmation that the intended "
        "UI effect happened (an open menu, for example); verify the result "
```

`server.py:7497`:

```python
        "The preflight is a point-in-time check of current desktop accessibility and brightness: it neither keeps the display awake nor guarantees later client captures (a report links a display that entered power-save after the probe to frame_client_all_black; that cause is not reproduced). "
```

**PROPOSED CHECKS: S**, ciclo de deploy; SEALED no, ENFORCE no adicional.

- **1d31:** menú presente en `ui_tree`; `key_press(1)` y lectura; después `input_trigger` exacto anterior y lectura. PASS si desaparece el menú y vuelve gameplay; `ui_click continuebtn` es recuperación/control. FAIL si solo hay delivery. Ausencia inicial del menú: INCONCLUSO. Cierra como contrato aclarado y ruta funcional comprobada.
- **d490:** misma escena saludable, awake→sleep confirmado independientemente→awake; capturas en cada estado, heartbeat y pose constantes. Idle/timeout no prueban sleep. Negro solo dormido y recuperación despierto apoyan limitación documentada; negro despierto mantiene bug abierto. Sin confirmación de power state: INCONCLUSO.

**TESTS:** caveats publicados; eliminar límites debe dar rojo. **RISK / NOT VERIFIED:** no atribuir negro a sleep por exclusión ni cerrar el fallo separado de cámara/proceso de 1d31.

**INBOX ACTION:** mantener ambos hasta esas comprobaciones; d490 solo se resuelve como limitación documentada si el ciclo la confirma.

## Una ronda

Orden por valor/coste; **un PR por lote**, máximo **3 revisiones por lote**. Al tercer rechazo, consolidar bloqueadores y devolver al orquestador; no añadir otra ronda automáticamente.

| Lote | Items y archivos | SEALED / ENFORCE | Puerta mecánica |
|---|---|---|---|
| A — S | T7: `test_coordination_audit_faults.py` | no / no | Intercalado forzado, snapshot terminal real, mutantes rojos; fast tier completo y CI 4/4 + aprobación |
| B — S | T1 documentación: `server.py`, tests de metadata | no / no | Descripciones publicadas advierten reporte y ausencia de certificación; eliminación roja; CI 4/4 + aprobación |
| C — S | T4 procedimiento: `SPEC_9941_PROBE.md` y procedimiento canónico externo | no / sí, experimento | Tres trials atribuibles, JSONL nuevo, terminal/restauración y controles negativos; aceptación tras ciclo in-game |
| D — M | T8: worker, lock, tests staging/locks, docs de #195 | sí / no | CI 4/4 + aprobación **y** build/ODOL discriminante; ampliar diseño antes de implementar si 713a falla |

Tras D, los cambios sellados viajan al próximo reseal junto con T5. El ciclo de deploy incorpora T2 y T10; lease antes de mutaciones, lifecycle guard para cierres, liberación y `session_status` al handoff.

**Fuera:** detector/reconexión de T1, orientación T3, hold productivo y observador instantáneo T4, cambios host T6 y verbo T9. Requieren calibración, diseño o consumidor. a97e queda como repro; 9376/250f siguen con el orquestador.