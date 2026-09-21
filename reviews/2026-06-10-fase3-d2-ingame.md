# Fase 3 D2 — gate in-game + receptor de la implementación Codex (2026-06-10)

> Cierre del ciclo D2 (`capture_screenshot` host-side). Codex implementó offline; Claude verificó (receptor) + condujo el gate in-game. Resultado: **D2 PASS in-game → Fase 3 (Visual) COMPLETA**.

## Receptor — implementación Codex (verificado host-direct)
- Archivos: `mcp_capture.py` (nuevo, ~11.2 KB), `mcp_client.py` (sub-suite D2), `tests/test_mcp_capture.py` (nuevo, 2 tests). 
- **Scope limpio** (mtime): D2 tocó SOLO esos 3 Python; `mcp_server.py` (06-10 04:17), `run-fase3.ps1` (04:39) y **todo el Enforce** (MCPBridge/MCPClientBridge/MCPJobRunner/MCPMessages/MissionGameplay/MissionServer) **intactos**. 0 regresión.
- `python -m unittest discover tests` → **7/7 verde** (re-corrido por Claude; +2 vs D1: downscale-a-presupuesto + isError window-not-found).
- Invariantes load-bearing (`mcp_capture.py`): selector **`class=='DayZ'`+pid DayZDiag** (:90), **DPI-aware** `SetProcessDpiAwareness(2)`+fallback (:81-83) antes de GetWindowRect/CopyFromScreen, topmost+foreground+restore (:101-116), **best-of-N** `choose_stable_frame` (acepta el más estable, NO error si no converge, :207-223), downscale iterativo a presupuesto (`small=260`, máx `full=320`, :150-163), **síncrono sin job-id**, `isError` en fallos + `frame_all_black` (:300), **sin `SetTimeMultiplier`** (scene-freeze diferido, respetado), PIL solo en captura (`mcp_server.py` stdlib intacto).
- Sub-suite phase3 (mcp_client.py): valida por **propiedad independiente** (`image_content_stats` brillo/nonBlack, NO fixture propio → **anti-tautología** LL-115). `D2_capture_nonblack`/`budget`/`synchronous`.
- **Veredicto receptor: ACEPTADO, 0 scope creep.** Bloque C "sin hallazgos" se sostiene. (A diferencia de D1, D2 no necesitó fixes del harness — fue limpio al 1er gate.)

## Gate in-game (1 corrida, PASS directo)
`GATE=PASS`, `overall_pass: true`. D1_camera_set/get + **D2_capture_nonblack/budget/synchronous = PASS**. La captura host-side agarró la ventana correcta del cliente: `meanBrightness 155.2`, `nonBlackRatio 0.99996` (frame real, brillante), `capture_is_error: false`, ≤ presupuesto de tokens, una sola llamada (síncrona). NO hizo falta rebuild de `@DayZ_MCP` (D2 es Python-only).

## Estado / siguiente
- **Fase 3 (Visual) COMPLETA in-game**: D1 (cámara) ✓ + D2 (captura) ✓. Product-spec D1/D2 → ✓.
- Siguiente fase: **Fase 4 (MCP stdio / FastMCP)** — tool surface completa vía MCP + seguridad endurecida (E1-E4).
- **Deuda heredada** (documentada, no bloquea): (a) scene-freeze server-side `SetTimeMultiplier` (diferido; el best-of-N + cámara estática bastaron para D2); (b) migrar `MCPBridge.c` a la base compartida `MCPJobRunner` (D-13; el server bridge quedó intacto a propósito en D1).
