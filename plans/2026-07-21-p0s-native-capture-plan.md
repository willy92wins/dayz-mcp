# P0.S — Captura nativa de ventana sin PowerShell

> **Estado:** incluido en la opción A ratificada; endurecido, requiere revisión
> independiente final antes de implementar.  
> **Owner:** `P:\DayZ_MCP_dev`.  
> **Dependencia:** el guard/security plan de la misma fecha entrega psutil 7.2.2,
> manifests P0.S, instalador Python y runner deny-launch previo a toda suite.  
> **Research:** `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-07-21-powershell-free-capture-codex.md`.

## 1. Objetivo y traza DPF

Sustituir el child `powershell ... mcp-grab.ps1` por un worker Python/Win32/Pillow
directo, conservando selección multi-cliente, timeout, fallback de frame negro,
estadísticas, SHA-256, PNG y payload público.

Traza a:

- **D2** — `capture_screenshot` síncrono, readiness-gated, window-grab real y dentro
  del presupuesto: `P:\DayZ_MCP_dev\product-spec.md:73`.
- **Intent visual** — emitir evidencia visual cuando el headless no basta:
  `P:\DayZ_MCP_dev\product-spec.md:59-73`.
- **E2/F3** — seguridad endurecida/fail-closed:
  `P:\DayZ_MCP_dev\product-spec.md:82,98`.
- La captura externa debe seguir siendo pasiva, sin inyectar input:
  `P:\DayZ_MCP_dev\product-spec.md:154`.

## 2. Autoridad verificada

- El único child productivo actual se construye en
  `P:\DayZ_MCP_dev\tools\mcp_capture.py:313-344` y usa
  `-ExecutionPolicy Bypass`.
- El consumidor interpreta la última línea JSON, exige PNG cuando `ok=true` y mapea
  timeout/backend/no-json: `P:\DayZ_MCP_dev\tools\mcp_capture.py:345-363`.
- La API pública es `grab_window_to_file(output_path, process_name,
  method, timeout_s, client_pid, cmdline_match)`:
  `P:\DayZ_MCP_dev\tools\mcp_capture.py:366-372`.
- El payload observable contiene `ok,error,method,window,stats,sha256,client,clientStats`:
  `P:\DayZ_MCP_dev\tools\spike0\mcp-grab.ps1:161-166,295-304`.
- El selector legacy prioriza cmdline, PID y sólo después any cuando no se pidió
  desambiguación: `P:\DayZ_MCP_dev\tools\spike0\mcp-grab.ps1:231-270`.
- Pillow instalado es 12.2.0 y `ImageGrab.grab` acepta `window: HWND`:
  `P:\DayZ_MCP_dev\tools\.venv-mcp\Lib\site-packages\PIL\_version.py:4` y
  `P:\DayZ_MCP_dev\tools\.venv-mcp\Lib\site-packages\PIL\ImageGrab.py:33-39`.
- En Windows esa ruta llama directamente a
  `Image.core.grabscreen_win32(..., int(window))`:
  `P:\DayZ_MCP_dev\tools\.venv-mcp\Lib\site-packages\PIL\ImageGrab.py:90-106`.

## 3. Scope de archivos

### Producción

- Add `P:\DayZ_MCP_dev\tools\native_window_capture.py`.
- Modify `P:\DayZ_MCP_dev\tools\mcp_capture.py`.
- No modificar `mcp_client.py`, tool schema, ImageContent, crop/downscale ni presupuesto
  salvo que un test de paridad demuestre una incompatibilidad y se enmiende este plan.

### Tests/evidencia

- Add `P:\DayZ_MCP_dev\tools\tests\test_native_window_capture.py`.
- Modify `P:\DayZ_MCP_dev\tools\tests\test_mcp_capture.py`.
- Modify `P:\DayZ_MCP_dev\tools\tests\test_mcp_client_capture.py` sólo si la inyección
  del worker cambia el seam del fake, sin alterar expectativas públicas.
- Extend `tools\tests\test_security_runtime_audit.py` y el manifest P0.S.

`tools\spike0\mcp-grab.ps1` queda reference-only con hash congelado. No se ejecuta para
paridad ni self-test.

## 4. Arquitectura

### 4.1 Worker aislado, sin shell

Se conserva el boundary de proceso porque `PrintWindow`/captura Win32 es síncrona y puede
bloquear. Un thread abandonado no permite garantizar timeout ni ausencia de una escritura
tardía. El parent lanza exclusivamente:

**[DESIGN; argv directo]**

```json
["P:\\DayZ_MCP_dev\\tools\\.venv-mcp\\Scripts\\python.exe", "-I", "-B",
 "P:\\DayZ_MCP_dev\\tools\\native_window_capture.py", "--process-name", "DayZDiag_x64",
 "--capture-png", "<ABS_CREATE_ONLY_PNG>", "--staging-png",
 "<ABS_CREATE_ONLY_SAME_DIR_PART>", "--method", "auto"]
```

Los selectores opcionales se añaden como `--cmdline-match VALUE` o
`--client-pid INT`. `subprocess.run` usa `shell=False`, pipes text UTF-8, timeout heredado
y `check=False`. No hay `-c`, shell, `.ps1`, reflection ni C# dinámico.

El worker escribe un único JSON en stdout; diagnóstico va a stderr sin cmdline ni paths
sensibles. El parent mantiene los mismos códigos públicos `capture_timeout`,
`capture_backend_failed` y `missing_png`.

### 4.2 API interna exacta

**[DESIGN]** `native_window_capture.py` expone funciones testeables:

```text
enable_dpi_awareness() -> None
enumerate_windows() -> list[WindowCandidate]
select_window(process_name: str, client_pid: int, cmdline_match: str) -> WindowCandidate
window_geometry(hwnd: int) -> CaptureGeometry
image_stats(image: Image, region: Region | None = None) -> CaptureStats
capture_selected(candidate: WindowCandidate, method: str) -> tuple[Image, str, CaptureGeometry]
publish_png_create_only(image: Image, staging: Path, destination: Path) -> str
capture_window(process_name: str, method: str, client_pid: int, cmdline_match: str,
               staging_png: Path, capture_png: Path) -> dict[str, object]
main(argv: list[str] | None = None) -> int
```

`WindowCandidate` contiene sólo hwnd, pid, class, title y rectángulo. `CaptureGeometry`
contiene `window` y región `client` relativa. Ninguna dataclass persiste handles HDC ni
objetos psutil.

## 5. Enumeración y selección

1. Activar DPI awareness antes de enumerar; preferir Per-Monitor v2 y fallback a la API
   process-aware ya usada por el referente. Fallo total → `dpi_awareness_failed`.
2. `EnumWindows`; exigir `IsWindowVisible`, ancho/alto positivos y clase exacta `DayZ`.
3. Para cada PID construir `psutil.Process` y exigir `name()` igual a
   `<process_name>.exe`, case-insensitive. `AccessDenied` no concede candidatura.
4. Si `cmdline_match` no está vacío, leer `cmdline()` y buscar substring case-sensitive
   en `subprocess.list2cmdline(argv)`. Elegir sólo candidatos coincidentes.
5. Si no hubo match y `client_pid > 0`, elegir sólo ese PID.
6. Sólo si ambos selectores están ausentes se permiten todos los PIDs del process name.
7. Elegir mayor área; empate por PID y HWND ascendentes para determinismo.

Si se pidió desambiguación, nunca caer a `any`. Si cmdline es `AccessDenied` y no hay PID
fallback explícito, devolver `window_identity_unavailable`; no capturar otro cliente.

Errores conservan códigos legacy sanitizados:
`window_not_found`, `window_not_found_for_pid_<PID>` y
`window_not_found_for_cmdline_<SANITIZED>`.

## 6. Geometría y estadísticas

- `GetWindowRect` obtiene outer; `GetClientRect` + `ClientToScreen` obtiene client
  relativo. Coordenadas multi-monitor negativas son válidas para bbox de screen.
- Ancho/alto <=0, overflow, client fuera de outer o API parcial → fail-closed.
- La imagen de ventana debe tener exactamente outer width×height. Una discrepancia no se
  corrige por resize: `window_geometry_mismatch`.
- Estadística replica el referente: grid 64×36 en centros de celda, luminancia
  `0.299R + 0.587G + 0.114B`, pixel non-black si luminancia `>9.0`:
  `P:\DayZ_MCP_dev\tools\spike0\mcp-grab.ps1:100-120`.
- Región inválida devuelve 0.0/0.0 y nunca pasa liveness.
- Frame live exige estrictamente `meanBrightness > 2.0` y
  `nonBlackRatio > 0.02` sobre client:
  `P:\DayZ_MCP_dev\tools\spike0\mcp-grab.ps1:179-186`.

Los floats JSON conservan nombres `meanBrightness` y `nonBlackRatio`; tests fijan
tolerancia absoluta `1e-9`, no representación textual de Python.

## 7. Métodos de captura

### `printwindow`

Invocar `PIL.ImageGrab.grab(window=hwnd)`; convertir a RGB. El campo público sigue siendo
`method="printwindow"` para compatibilidad semántica. Excepción, imagen vacía o tamaño
incorrecto falla con `printwindow_failed`. En modo explícito se publica también un frame
negro válido, igual que el referente; el liveness sólo decide fallback en `auto`.

### `foreground`

Guardar HWND foreground previo. Usar `ShowWindow(SW_RESTORE)`, `BringWindowToTop` y
`SetForegroundWindow`; si hace falta, `AttachThreadInput` sólo durante la transición y
siempre detach en `finally`. Esperar exactamente 250 ms mediante `time.sleep(0.250)` en el
worker y capturar `ImageGrab.grab(bbox=outer, all_screens=True)`.

En modo explícito se publica cualquier imagen geométricamente válida, aunque client no
supere liveness. En `finally`, intentar restaurar el HWND previo si sigue válido. Un fallo de restauración
produce warning `foreground_restore_failed` dentro del payload, rate-limited a una vez por
captura, pero no invalida pixels ya verificados.

### `screen`

Capturar el mismo bbox sin cambiar foreground; es baseline/último recurso.

### `auto`

Orden exacto: `printwindow`; si client no-live, `foreground`; si sigue no-live,
`screen`. En `auto`, sólo un frame client-live puede producir `ok=true` antes del último
screen; el último screen conserva la compatibilidad legacy y puede publicarse aunque sea
negro, pero `grab_stable_frame` aplica después el mismo predicado estricto §6 y lo marca
no-verificado. `method=printwindow` o
`foreground` explícito no cae a otro método.

## 8. Publicación create-only

1. Destination debe ser absoluto, parent existente y fichero ausente.
2. El parent predeclara `--staging-png` en el mismo directorio y `capture_window` transporta
   ambos paths, sin recalcularlos, a
   `publish_png_create_only(image, staging_png, capture_png)`. El worker crea staging
   exclusivo con `os.open(O_CREAT|O_EXCL)`; guardar PNG al file object, flush+fsync.
3. Reabrir con Pillow, exigir `RGB`, tamaño/estadísticas esperadas y calcular SHA-256.
4. Publicar mediante `os.rename(temp, destination)`; en Windows destination existente
   falla, nunca se usa `os.replace`.
5. En excepción normal, borrar sólo el temp exacto propio. Si el parent mata al worker por
   timeout, el parent elimina únicamente el temp path predeclarado después de confirmar
   que el child salió; destination nunca se toca.

Payload `ok=true` sólo después de publicación y re-hash del destination. SHA se emite
uppercase para mantener el referente.

## 9. Integración en `mcp_capture.py`

- Sustituir `GRAB_SCRIPT` por `NATIVE_CAPTURE_SCRIPT` absoluto.
- `_run_window_capture` conserva firma y parsing; cambia sólo argv/flags.
- Exigir `sys.executable` resuelto dentro de `.venv-mcp` en runtime productivo; test puede
  inyectar `python_executable` explícito. Un intérprete externo →
  `capture_backend_failed: untrusted_python`.
- Rechazar stdout con más de una línea JSON, payload no-dict, `ok` no-bool, método fuera
  de allowlist, path de salida distinto o campos geometry/stats mal tipados.
- `grab_stable_frame` cambia únicamente su predicado de liveness: acepta si y sólo si
  `meanBrightness > 2.0 && nonBlackRatio > 0.02`, igual al worker. El caller actual sólo
  rechaza cuando ambos valores son `<=1.0`/`<=0.01` en
  `P:\DayZ_MCP_dev\tools\mcp_capture.py:404-420`; esa discrepancia se elimina.
  `grab_window_to_file`, `capture_screenshot`, ImageContent, encoding, crop, budget y
  save-fullres no cambian.

No se incorpora un segundo backend ctypes GDI mientras Pillow 12.2.0 pase el gate. Si
`ImageGrab.grab(window=hwnd)` falla en DayZ real, el resultado es INCOMPLETE y se redacta
una enmienda; no se improvisa PrintWindow durante implementación.

## 10. Viability tests

### Unitarios worker

- Boundary liveness: `2.0/0.03`, `2.1/0.02`, `1.5/0.015` y cada combinación cruzada
  son no-live; sólo ambos estrictamente sobre umbral son live. Worker y
  `grab_stable_frame` deben coincidir bit por bit en toda la tabla.
- EnumWindows: invisible, clase ajena, zero-area, coordenadas negativas y empate.
- Selección: cmdline match, cmdline miss→PID explícito, PID miss, any sólo sin selector,
  dos clientes y AccessDenied.
- Geometría: client válido, outside, vacío, API parcial y mismatch de tamaño.
- Stats: fixture sintético 64×36, client negro/titlebar vivo, boundaries 2.0/0.02,
  above-boundary y región inválida.
- Métodos: printwindow live; negro→foreground; foreground negro→screen; explícito sin
  fallback; restauración foreground en finally incluso con excepción.
- Publicación: happy, destination existente, temp collision, save parcial, reopen corrupt,
  rename race y SHA exacto.
- CLI: flags exactos, selector exclusivo, JSON único, stderr sanitizado, exit 0 para
  resultado de negocio y exit 2 para uso/config inválidos.

### Integración parent

- argv exacto contiene `sys.executable -I -B` y script absoluto; `shell=False`.
- timeout mata/recolecta sólo el worker y limpia su temp; devuelve `capture_timeout`.
- worker exit nozero, no JSON, dos JSON, payload mal tipado y `ok` sin PNG fallan.
- `test_mcp_capture.py`, `test_mcp_client_capture.py`, token budget y visual resolution
  siguen verdes sin cambiar expectativas públicas.
- Runner security intercepta launches y acepta sólo este worker exacto; PowerShell,
  cmd, `.ps1`, `-c`, interpreter/path no congelado o `shell=True` fallan antes de launch.

### Paridad reference-only

Tests portan a fixtures Python los casos del self-test legacy y comparan payload/errores,
grid/thresholds y selección. No ejecutan el `.ps1`.

## 11. Gate real único, compartido con S4

No se abre un segundo boot. El S4 del plan lifecycle arranca un único DayZDiag gestionado
y, dentro de ese run:

1. Seleccionar por `client_pid` exacto y capturar `method=printwindow`.
2. Capturar `method=auto`; exigir `ok=true`, client live, PNG reabrible, SHA exacto y
   geometría consistente.
3. Capturar `method=screen` como control sólo si la ventana está visible; no se cambia
   método ni foreground de forma oculta.
4. Dos frames del mismo view separados 250 ms deben ser reabribles; el gate no exige
   delta mínimo si el menú es estático. Para demostrar contenido real se exige región
   client live y diferencia suficiente frente a un fixture negro, no frente a sí mismo.
5. Stop exacto del run, cierre limpio y delta Defender 1116/1117 vacío tras 120 s.

PASS requiere que `printwindow` o `auto` entregue contenido vivo. Si sólo screen funciona,
se registra degradación e INCOMPLETE porque la captura ocluida no quedó demostrada. No se
repite automáticamente el boot.

## 12. Orden de implementación

1. Escribir tests worker/parent en rojo sin ejecutar legacy.
2. Implementar bindings Win32 + selección + stats.
3. Implementar métodos y publicación create-only.
4. Implementar CLI y migrar `_run_window_capture`.
5. Ejecutar suites focales y completas bajo deny-launch dentro de S2A.
6. Revisión Codex y revisión independiente; 0 CRITICAL/HIGH offline.
7. Antes de publicar S2A, congelar sources/tests/hashes de capture en la clausura del
   `p0s-build-manifest.json`; el manifest se publica una sola vez y nunca se amplía.
8. Ejecutar S2B del owner lifecycle contra ese build manifest ya inmutable.
9. Ejecutar el gate compartido S4 sólo tras aprobación host.
10. Incorporar únicamente el receipt del gate real S4 al `p0s-test-manifest.json` final.

## 13. Aceptación

- Cero child PowerShell/cmd/.ps1/reflection; único child es Python venv exacto.
- Payload, errores, selección y stats compatibles con el contrato actual.
- Timeout termina el worker sin escritura tardía.
- Destination y evidencia nunca se sobrescriben.
- Suite completa verde bajo deny-launch.
- Gate S4 entrega window-grab client-live y cero evento Defender nuevo.
- Ambos manifests P0.S enlazan worker, tests, venv/Pillow/psutil y receipt.
- Cero proceso/lease/ticket propio al cierre.

## 14. Rollback y handoff

No cambia formato persistente ni wire MCP. Rollback restaura sólo `mcp_capture.py` y
retira el worker, pero reintroducir el backend PowerShell queda prohibido en este host; si
el nuevo worker falla, la feature queda INCOMPLETE hasta una enmienda nativa. Como el
proyecto no tiene `.git`, el handoff usa hashes/manifest/reviews y no inventa commit.
