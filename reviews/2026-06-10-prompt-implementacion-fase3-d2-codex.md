===== PROMPT INICIO =====

Tarea: implementar **D2 (`capture_screenshot`)** de Fase 3 (Visual) de DayZ-MCP — captura **host-side** por window-grab del cliente renderizado. Esta sesión cubre **únicamente D2** (la captura + su verdict en el harness). **NO toques D1** (`camera_set`/`camera_get`, ya PASS in-game), **ni el Enforce** (peer cliente/DTOs/bridge), **ni el scene-freeze server-side** (diferido). No los implementes aquí ni siquiera parcialmente.

Contexto: D1 (cámara) está **PASS in-game** — `camera_set` posiciona la cámara client-side. D2 solo añade la captura de la ventana ya renderizada. La imagen **NO pasa por el engine** (window-grab externo). Los 2 spikes que gateaban D2 ya están resueltos (números abajo).

## Carga inicial obligatoria
Lee estos archivos (rutas absolutas) antes de tocar nada:
1. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-09-fase3-visual.md`
   (plan v2 — §"Paso 3 — D2" es la spec; §"Revisión adversarial aplicada" P2-a/P2-b/P2-g vinculante: ventana correcta, presupuesto de tokens, gate best-of-N).
2. `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\verified-apis.md`
   (§Visual: D1 in-game + window-grab `CopyFromScreen` validado).
3. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\spike0\spike0-window-enum.ps1`
   (selector de ventana PROBADO in-game: `EnumWindows` + predicado clase+pid + `CopyFromScreen`. PÓRTALO/INVÓCALO).
4. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\spike0\spike0-token-calib.py`
   (downscale + base64 + estimación de tokens PROBADO. La lógica de downscale-a-presupuesto sale de aquí).
5. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_client.py`
   (el harness; aquí va `capture_screenshot` host-side + la sub-suite D2 del `--mode phase3`).
6. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-10-fase3-d1-ingame.md`
   (cierre D1 + los 2 fixes del harness; contexto del estado).
7. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-fase3.ps1`
   (launcher in-game; el verdict D2 se integra aquí / en la sub-suite que llama).

NO releas el research consolidado. La captura es **Python host-side** (NO Enforce, NO `/enqueue` del engine).

## Resultados de los spikes (YA resueltos — bakea estos números, NO los re-investigues)
- **Selector de ventana (0.2)**: la ventana de render del cliente tiene **`class == 'DayZ'`**; el pid es el DayZDiag **cliente** (el server es una consola `class == '#32770'`, título "DayZ Console version..."). Predicado determinista = `pid ∈ DayZDiag procs` AND `class == 'DayZ'` AND `visible` AND `class != ConsoleWindowClass`. **PIN DPI-awareness** (`SetProcessDpiAwareness`/`SetProcessDPIAware`) antes de `GetWindowRect`/`CopyFromScreen`: sin ello el rect sale virtualizado (868×517 en vez de 1280×720) — no rompe la captura pero pierde resolución real.
- **Presupuesto de tokens (0.3, calibrado sobre un grab DayZ REAL)**: downscale hasta **≤ ~25.000 tokens** de base64. Medido: **240×143 cabe holgado** (~15.7k tok @2.5 chars/tok), **320×191 al límite** (~25.5k @2.5 / ~18.2k @3.5). → `capture_scale` default `small` ≈ **240-280px de ancho**; `tiny` ≈ 160px; **máx ≈ 320px**. Un frame DayZ real comprime parecido al sintético `frame`.

## Alcance acotado — D2 (plan v2 §Paso 3)
Salida principal en Python host-side. Donde toque image-processing **puedes usar PIL/Pillow** (ya instalado, lo usa `spike0-token-calib.py`) — es tooling host-side, NO el `mcp_server.py` (ese sigue stdlib-only). La parte .NET de captura (`CopyFromScreen`) se invoca vía PowerShell (el `spike0-window-enum.ps1` ya la encapsula) o se porta; reusa lo probado.

### Paso A — módulo de captura host-side (nuevo, p.ej. `tools/mcp_capture.py` o dentro de `mcp_client.py`)
`capture_screenshot(scale="small", max_tokens=25000) -> dict` **síncrono** (CONFLICT-2: sin job-id):
1. **Localiza+captura** la ventana cliente con el **predicado 0.2** (clase `DayZ` + pid cliente + DPI-aware). Reusa `spike0-window-enum.ps1 -CapturePng <tmp.png>` (invócalo como subproceso) o porta su interop. Fuerza topmost+foreground antes del grab.
2. **Gate de estabilidad best-of-N (P2-g)**: captura **N=3-5** frames; mide diff medio de píxeles entre consecutivos; **acepta el más estable (best-of-N), NO devuelvas error si no converge** (la cámara es estática vía D1 → debería ser estable; threshold calibrable, esperar 1-3%, NO 0.1%).
3. **Downscale a presupuesto (0.3)**: a `scale` (`small`≈240-280px / `tiny`≈160 / máx 320) hasta `len(base64) ≤ max_tokens*2.5` (proxy conservador). Lógica de `spike0-token-calib.py`.
4. **Devuelve** `{"type":"image","data":<base64>,"mimeType":"image/png"}` (ImageContent). Errores de negocio (ventana no encontrada, frame all-black) → **`{"isError": true, "error": "<motivo>"}`**, NO excepción.

### Paso B — sub-suite D2 en `mcp_client.py --mode phase3`
Tras el `camera_set` de D1 (reúsalo; la cámara queda posicionada), llamar `capture_screenshot` **en proceso** (NO por `/enqueue`) y emitir un test D2 en `fase3-verdict.json`:
- `D2_capture_nonblack`: `meanBrightness > 16` AND `nonBlackRatio > 0.5` (frame real, no negro).
- `D2_capture_budget`: `len(base64) ≤ ~25k*2.5` chars (cabe en el presupuesto).
- `D2_capture_synchronous`: una sola llamada devolvió imagen (sin job-id/poll).
- **Anti-tautología (LL-115)**: la imagen se valida por propiedad independiente (brillo/contenido), NUNCA comparándola con un fixture generado por el propio capturador.

### Paso C — integración en `run-fase3.ps1`
El cliente ya se lanza renderizado (D1). La captura corre host-side en la sub-suite; añade el test D2 al verdict + GATE. NO cambies el launch ni el deploy.

## Suite de tests automatizados (gate OFFLINE de esta sesión)
Amplía `tools/tests/test_mcp_server.py` (o añade `test_mcp_capture.py`, unittest stdlib) con lo testeable sin DayZ:
- el downscale-a-presupuesto produce `len(base64) ≤ budget` para una imagen de prueba (sintética, como hace `spike0-token-calib.py`).
- la construcción de `ImageContent` y el camino `isError` (ventana no encontrada → `isError:true`).
Comando: `python -m unittest discover tests` desde `tools\` → verde. (El gate in-game D2 lo corre el usuario con `run-fase3.ps1`.)

## Restricciones críticas (vinculantes toda la sesión)
1. **Captura host-side** (Python/.NET): la imagen **NO pasa por el engine** ni por `/enqueue`. `mcp_server.py` sigue **stdlib-only**; el módulo de captura host-side **sí puede usar PIL** (ya instalado).
2. **Selector 0.2 + DPI-aware** EXACTO (clase `DayZ`, pid cliente, no `#32770`). NO `MainWindowHandle` (capturaba mal).
3. **CONFLICT-2**: `capture_screenshot` **síncrono, sin job-id**. Errores de negocio → `isError:true`, no excepción.
4. **CONFLICT-1**: ≤ ~25k tokens vía downscale (target 0.3). `capture_scale` knob.
5. **NO toques D1** (`camera_set`/`camera_get`, PASS in-game) **ni el Enforce** (`MCPClientBridge.c`/`MCPBridge.c`/`MissionServer.c`/`MCPMessages.c`). **NO toques el deploy/launch** salvo añadir el test D2 al verdict.
6. **NO implementes el scene-freeze server-side** (`SetTimeMultiplier`): el plan §Paso 3 step 1 lo menciona pero está **DIFERIDO** (la cámara ya se asienta vía D1; el gate host-side basta). Te tentará — resiste.
7. **NO MCP stdio** (fase 4). El "tool" lo ejerce el harness por ahora.
8. **R21 (doble revisión) NO en esta sesión**: implementa D2 y PARA. La review del plan ya está hecha.
9. **NO improvises fuera del plan**: si algo no encaja, anótalo en Bloque C con `path:line`, interpretación conservadora, marca para revisión.

## Output esperado al cerrar la sesión
### Bloque A — Archivos creados/modificados (rutas absolutas + tamaño aprox).
### Bloque B — `python -m unittest discover tests` COMPLETO y literal (no parafrasees). Compile/run host-side declarado con CÓMO se verificó.
### Bloque C — Hallazgos (sección/archivo del plan, qué no encajaba, acción, sugerencia). Si no hay: "Sin hallazgos."
### Bloque D — Handoff: estado al cierre (D2 implementado, gate in-game pendiente); deuda (scene-freeze server-side diferido; migración `MCPBridge.c`→`MCPJobRunner`); próximo paso (gate in-game D2 + fase 4 MCP stdio).

===== PROMPT FIN =====
