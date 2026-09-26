# Prompt — fix DayZ-MCP phase-3 visual capture (sub-sesión)

Generado 2026-06-14 desde la sesión del pipeline de animación de player (A6_SR2M).
Pegar en una sub-sesión (Cowork/Claude o Codex CLI) abierta sobre el proyecto DayZ_MCP.
Al terminar, volver a la sesión de animación con el bloque D.

```
===== PROMPT INICIO =====
TAREA (una sola): arreglar la captura visual fase-3 del DayZ-MCP para que
`camera_set` + `capture_screenshot` devuelva un frame VIVO del cliente diag
renderizando la vista comandada — en concreto: un personaje spawneado, encuadrado
por `camera_set lookat`, VISIBLE en el PNG. Diagnosticar la causa real y arreglarla.

## Contexto del fallo (observado 2026-06-14, conduciendo el bridge por PowerShell)
- query_player_state → OK (pos). camera_set (cam_mode="lookat", cam_pos+look_at+fov) → ok=1.
  Luego window-grab del cliente.
- Los grabs salen BYTE-IDÉNTICOS entre launches DISTINTOS (mismo sha) → la captura coge un
  frame STALE/cacheado del escritorio, no el render vivo. `CopyFromScreen` del rect de la
  ventana 'DayZ' en un escritorio activo multi-monitor captura contenido DWM viejo o ventanas
  solapadas (en runs distintos capturó el cliente de Steam y la ventana de chat de Cowork).
- camera_set ok=1 pero la vista capturada NUNCA cambia → o el render no sigue a camera_set
  (SetCameraEx fija un estado / una freeCam que no es la cámara activa de render), o el grab
  es stale. No se puede distinguir hasta arreglar el grab.
- La validación de captura de fase3 (`phase3_capture_cases`) solo comprueba non-black + tamaño
  + síncrono — NUNCA que la captura muestre el contenido comandado. Por eso fase3 "pasó"
  capturando frames arbitrarios; el camino camera→render nunca se validó end-to-end.

## Carga inicial obligatoria (leer antes de tocar nada)
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md  (estado vivo; proyecto cerrado, fase 3 = visual)
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-mcp-architecture.md  (§6 pipeline visual; MakeScreenshot roto T165276; decisión window-grab)
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_capture.py  (la captura host-side / window-grab)
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\spike0\spike0-window-enum.ps1  (selector de ventana + CopyFromScreen — fuente del staleness, línea ~89)
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c  (camera_set: DispatchCameraSet:447, ValidateCameraArgs:717 modos orient/lookat/matrix/free, ApplyCameraSet/SetCameraEx/SetOrientation, Camera.IsInterpolationComplete)
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_client.py  (validación content-blind: phase3_capture_cases:709, phase3_camera_case:627)
7. C:\Users\guill\OneDrive\Documentos\DayZ Projects\A6_SR2M_dev\_gate\gate-mcp.ps1 + gate_mcp_init.c  (consumidor real: spawnea player + AKS74U en manos, drivea camera_set lookat + grab — usar como test de aceptación end-to-end)

## Criterios de aceptación (un solo deliverable, 4 facetas)
- A1 (frame vivo): la captura devuelve el contenido renderizado PROPIO de la ventana DayZ,
  robusto a que no tenga foco / esté ocluida / multi-monitor. Recomendado: sustituir
  CopyFromScreen-del-rect por `PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT=2)` (captura la
  superficie propia de la ventana aunque esté ocluida), o forzar foreground real
  (AttachThreadInput) antes del grab. VALIDAR: dos capturas de una escena que cambia difieren
  (no byte-idénticas).
- A2 (camera→render): camera_set re-apunta de verdad la vista RENDERIZADA. Validar con
  aserción de CONTENIDO: world_spawn un objeto visualmente distintivo en pos conocida,
  camera_set lookat esa pos, capturar, y asertar que el objeto aparece en la región esperada
  del frame (chequeo de color/región), NO solo que camera_get devuelva el estado fijado. Si la
  cámara renderizada no sigue a SetCameraEx, arreglar el bridge (la cámara activa de render
  debe ser la que camera_set conduce).
- A3 (validación content-aware): ampliar phase3_capture_cases con un chequeo de CONTENIDO (la
  captura muestra el sujeto comandado) para que "pasa" signifique que el visual funcionó de
  verdad. El check non-black es insuficiente.
- A4 (consumidor end-to-end): con A6_SR2M_dev\_gate\gate-mcp.ps1, un player spawneado
  sosteniendo AKS74U, camera_set lookat(player) + capture devuelve un PNG donde el personaje +
  arma son VISIBLES y encuadrados. Es el test que la sesión de animación necesita. Adjuntar el
  PNG como evidencia.

## NO (límites)
- NO toques el pipeline de animación (A6_SR2M\, A6_AnimRTTest\; de A6_SR2M_dev\ solo LEE el
  gate harness como consumidor). NO re-bakees nada del pipeline de anim.
- NO rompas los gates fase 0-2 (re-correrlos si tocas el bridge).
- NO uses MakeScreenshot (roto, T165276) — captura host-side.
- NO mutes ~/.codex/config.toml.

## Restricciones
- R2 cite-then-verify firmas Enforce/API. R7/R8/R9 + skill rigorous-data-audit si tocas el
  bridge Enforce (es código que ya pasó gates). Si re-empaquetas el PBO: AddonBuilder -packonly
  → P:\Mods\@DayZ_MCP, verificar por strings (no mtime).
- Python stdlib-only para shim/loopback; el server corre en .venv-mcp. Diag: -filePatching,
  -mod=@DayZ_MCP, -port=2402.
- Captura es host-side (PowerShell). El window-grab vive en spike0-window-enum.ps1 / mcp_capture.py.

## Output esperado (bloques A/B/C/D al cerrar)
- A: archivos creados/modificados (paths absolutos).
- B: output de los tests — el nuevo test content-aware (A2/A3) + el A4 end-to-end con el PNG
  de evidencia (player encuadrado). Output real, no parafraseado.
- C: el ROOT CAUSE real con evidencia (¿era grab stale, camera→render, o ambos?), y qué se cambió.
- D: handoff para volver a la sesión de animación: cómo invocar camera_set+capture arreglado
  para framear al player (comando/args exactos), y si gate-mcp.ps1 ya captura el player o qué
  ajustar en él.
===== PROMPT FIN =====
```

## Receptor (al volver con el output de la sub-sesión)
- Bloque A → Read de cada path (verificar que existen).
- Bloque B → el PNG A4 debe mostrar el player encuadrado (no un paisaje/menú); verificar con PIL.
- Bloque C → el root cause se incorpora a `pipeline-anim-player.md` §7 (estado del visual).
- Bloque D → retomar el gate visual del SR2M con el camera_set+capture arreglado.
