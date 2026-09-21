# Fase 3 D1 — gate in-game + receptor de la implementación Codex (2026-06-10)

> Cierre del ciclo D1 (`camera_set`/`camera_get`). Codex implementó offline; Claude verificó (receptor) + condujo el gate in-game, que surfaceó 2 fixes del harness. Resultado: **D1 PASS in-game**.

## Receptor — implementación Codex (verificado host-direct, no por la palabra de Codex)
- Archivos+sizes OK. `MCPBridge.c` (mtime 06-09) / `MissionServer.c` (06-07) **intactos** (mtime viejo) → 0 regresión al server bridge.
- `python -m unittest discover tests` → **5/5 verde** (re-corrido por Claude). Routing 2-colas correcto (`VALID_PEERS`, default `server` back-compat, check `peer==command_peer`).
- DTOs exactas (MCPArgs cámara + `capture_scale`/`max_tokens` solo DECLARADOS; MCPCamera/MCPCameraValidation). 
- Invariantes load-bearing: **SUP-1** (sin indexado `SetCameraEx`/`GetCamera(0)` — 0 hits), **SUP-2** (RestContext propio, sin RPC/OnRPC — 0 hits), **sin D2** (capture/window-grab — 0 hits). Fixes de la review presentes: `create_local=true` (`MCPClientBridge.c:568`), `SetOrientation` primario (`:596`), `Camera.GetCurrentFOV()` estático (`:823/833`), `PlayerControlDisable`+`GetHud().Show` (`:950-978`), borra cámara previa + teardown. Sin ternario (Enforce 1.29). Suite phase3 anti-tautológica (`roll_signal` + `exact_match_suspect`).
- **Veredicto receptor: ACEPTADO, 0 scope creep.**

## Gate in-game — 3 corridas (la 1ª y 2ª surfacearon fixes del harness; la 3ª PASS)
**Resultado final**: `GATE=PASS`, `overall_pass: true`. `D1_camera_set_orient_roll` + `D1_camera_get_orient_roll` + `D1_fail_closed_client_peer` = **PASS**. `matrix_max_error 3.4e-05`, `pos_error 3.9e-05 m`, `fov_error 0`, `roll_signal true`, `exact_match_suspect false` (NO tautológico — `pos_error ≠ 0` y `matrix_error` es residual float real). El Enforce nuevo **compiló y cargó bien** al 1er intento (no hubo error de compilación).

## 2 fixes del harness aplicados por Claude durante el gate (hallazgos del in-game; Codex no podía correrlo)
1. **`run-fase3.ps1` — config no llegaba al peer cliente** (run 1: `[MCP-CLIENT] client init pending: config not found`; cero polls `peer=client`; `camera_set` id=6 → `TimeoutError`). Causa: el harness (clon de run-fase2, server-only) escribía `dayz_mcp.json` a `server_profiles` + mission, pero **no a `client_profiles`**; el cliente lee `$profile:dayz_mcp.json` de SU profile dir y **no resuelve `$mission:`** a la mission del server. Fix: escribir la config también en `$ClientProfiles\dayz_mcp.json` (simétrico al server) — `run-fase3.ps1` tras la escritura del server config.
2. **`mcp_client.py` `phase3_expected_orient_matrix` — convención de signo del roll** (run 2: `camera_set`/`get` ok=1 pero `matrix_max_error 0.5176 = 2·sin(15°)`; pos/fov/roll-magnitud exactos). Causa: el harness construía el roll como `right=(c,+s,0) up=(-s,c,0)`, pero DayZ (`Math3D.YawPitchRollMatrix`, vía `SetOrientation→GetTransform`) usa `right=(c,-s,0) up=(s,c,0)`. **El mod NO transforma** — pasa la orientación tal cual a `SetOrientation`, así que la salida del engine es la aplicación fiel del comando; el bug estaba en el expected del harness. Fix: signo de `s` corregido en el expected. Verificado: la fórmula nueva produce exactamente la matriz medida in-game (no tautológico — comparado contra el dato real).

Ambos son **harness**, no código del mod. `MCPClientBridge.c`/DTOs intactos y correctos.

## Fact verificado (reusable, in-game)
- **DayZ `SetOrientation(Vector(0,0,roll))` → `GetTransform` (convención `Math3D.YawPitchRollMatrix`)**: para +roll sobre forward, `right=(cos,-sin,0)`, `up=(sin,cos,0)`, `forward=(0,0,1)` con yaw=pitch=0. `GetTransform` devuelve la base 4×4 completa (roll preservado, no roll-lossy). `Camera.GetCurrentFOV()` es estático y devuelve radianes. `create_local=true` obligatorio en `CreateObject("staticcamera",...)` desde el cliente.

## Estado / siguiente
- **D1 ✓ in-game** (product-spec D1 actualizado). Peer cliente (T-A) + cámara operativos.
- Siguiente: **Spike 0.2** (selector de ventana) + **0.3** (target de tokens sobre grab real) → **D2 `capture_screenshot`** (host-side, plan §Paso 3, handoff a Codex).
- **Deuda**: migrar `MCPBridge.c` a la base compartida `MCPJobRunner` (D-13; sesión aparte; el server bridge quedó intacto a propósito).
