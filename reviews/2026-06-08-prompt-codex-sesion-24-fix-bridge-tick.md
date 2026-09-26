# Prompt Codex — sesión 24 — fix: el modded MissionServer no aplica → bridge no tickea (fase 2)

Patrón: fix handoff (defecto compile/load). Diagnóstico de Claude: el spike in-game falla porque el `modded class MissionServer` no se aplica → el bridge nunca tickea. Generado por Claude 2026-06-08. Copiar entre marcadores.

```
===== PROMPT INICIO =====

Tarea: encontrar y arreglar el defecto de compilación/carga introducido en la Sesión A de fase 2 que impide que el `modded class MissionServer` se aplique in-game (resultado: el bridge nunca tickea, `OnTick` no corre, todos los `query_player_state` dan timeout). Esta sesión cubre **únicamente ese fix**. NO cambies el comportamiento/contrato de las tools fase 2, NO toques el harness, NO rediseñes.

## Evidencia (smoking gun — ya verificada host-direct por Claude)

Comparación fase-1 (PASÓ) vs fase-2 (FALLÓ), mismo `MissionServer.c` + mismo `init.c` (MCPPOCMission), mismo config:

- **fase-1 server script log** (`_fase1\run_20260608_024848\server_profiles\script_2026-06-08_02-49-12.log`): el bridge logueó
  `[MCP-POC] config loaded path=$profile:dayz_mcp.json url=http://127.0.0.1:8765/ keylen=43 poll_hz=5`
  `[MCP-POC] poll commands=11 ...`
  `[MCP-POC] result posted id=1 ok=0 ...`
  → el bridge configuró, polleó y posteó. El `OnUpdate`→`MCPBridge.Get().OnTick()` del modded MissionServer SÍ corría.
- **fase-2 server script log** (`_fase2\run_20260608_171929\server_profiles\script_2026-06-08_17-19-44.log`): solo
  `[MCP-POC] spawn_actual=6063.02 7.99834 1931.91` (lo escribe `MCPPOCMission.CreateCharacter`)
  y **NUNCA** `config loaded`/`poll`/`result posted`. El player spawnea pero el bridge **no tickea**.
- El error vanilla `'Module PluginConfigDebugProfile is not Registred'` (`MissionBase()` missionbase.c:21) aparece en **AMBAS** corridas → es benigno, NO es la causa.

Interpretación: `MCPPOCMission : MissionServer` (en `init.c`) corre su `CreateCharacter` (spawn_actual), pero el `OnUpdate` que tickea el bridge **es el vanilla**, no el del modded class → **el `modded class MissionServer` NO se aplicó en fase 2**. Como `MissionServer.c` referencia `MCPBridge.Get()`/`MCPBridge.ShutdownInstance()`, un error de compilación en `MCPBridge.c` (adiciones fase-2) tumbaría también `MissionServer.c` → el modded class se cae → la misión usa `OnUpdate` vanilla. El launch por filepatching es el gate de compilación real (AddonBuilder está bloqueado por Steam-init en el entorno del usuario) y cazó lo que la revisión estática no vio.

## Carga inicial obligatoria

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\_fase2\run_20260608_171929\server_profiles\script_2026-06-08_17-19-44.log
   (fase-2 FAIL: solo spawn_actual, sin config loaded).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\_fase1\run_20260608_024848\server_profiles\script_2026-06-08_02-49-12.log
   (fase-1 PASS: baseline con config loaded + poll + result posted).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
   (adiciones fase-2: VectorToArray ~:1023, handlers DispatchSceneRaycast/DispatchTelemetryRead/Populate*).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c
   (DTOs fase-2).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MissionServer.c
   (modded class que referencia MCPBridge y que se está cayendo).
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-08-fase2-observacion.md
   (qué debe hacer fase 2 — para no romper el contrato al arreglar).

## Pasos (en orden)

1. **Confirmar la caída de clases**: compara la línea `Module: Mission; loaded Nx files; Mx classes` entre el log fase-1 y el fase-2. Si fase-2 cargó MENOS clases, confirma que se dropearon clases (compile error). Además grepea el RPT y script log de fase-2 (`...\_fase2\run_20260608_171929\server_profiles\`) a fondo por errores de compilación de script (`Can't compile`, `Compile`, `MCPBridge`, `MCPMessages`, `error` cerca de la carga de módulos). Cita lo que encuentres (path:line).
2. **Localizar el error de compile en las adiciones fase-2** (R2 cite-then-verify, NO asumir). Candidatos prioritarios:
   - **`protected void VectorToArray(vector v, out array<float> a)` (`MCPBridge.c:~1023`)**: el patrón vanilla in-place (`BuildPlayerState` `MCPBridge.c:1198-1230`: `state.pos.Insert(pos[0]); ...`) NO usa `out` sobre el array. `out array<ref/float>` es sospechoso de no compilar en Enforce. Verifícalo.
   - **`JsonSerializer.ReadFromString(parsed, line, parseError)`** (en `DispatchTelemetryFixtureJsonl`): verifica la firma real contra vanilla (uso en `scripts\3_game\tools\jsonfileloader.c`). Era `[A]` en el plan.
   - Cualquier otro constructo nuevo en los handlers/DTOs.
3. **Arreglar el defecto** manteniendo intacta la funcionalidad fase-2 (handlers, contrato, semántica ok/error). Ej. si el problema es `out`: `void VectorToArray(vector v, array<float> a)` (array por referencia, modificado in-place como BuildPlayerState). Fix mínimo y fiel al patrón vanilla.
4. **Re-verificar compile**: si tu entorno tiene Steam corriendo, reconstruye con AddonBuilder y confirma 0 errores (en el entorno del usuario AddonBuilder fallaba por Steam-init; si en el tuyo va, úsalo). Si no, revisión Enforce cuidadosa. El gate definitivo es el spike in-game, que **corre el usuario** después.

## Restricciones críticas
1. **Solo el fix del defecto compile/load**. NO cambies comportamiento/contrato/semántica de scene_raycast/telemetry_read más allá de lo necesario para compilar.
2. **NO toques el harness** (`run-fase2.ps1`, `mcp_server.py`, `mcp_client.py`) — funcionan (infra + cliente + player verificados OK por Claude).
3. **R2 cite-then-verify** cada API/constructo que cambies contra el source vanilla.
4. **Enforce 1.29**: sin ternario `?:`; arrays<float> por componentes; `Print(string.Format(...))`.
5. **NO rediseñes, NO te autorrevises (R21 es sesión aparte).** Fix → para.

## Output esperado (A/B/C/D)
- **Bloque A** — archivos cambiados (MCPBridge.c / MCPMessages.c) + qué tocaste (diff conceptual).
- **Bloque B** — la evidencia del defecto: diff de class-count fase1-vs-fase2 y/o el error de compile exacto (path:line), + resultado del re-compile (AddonBuilder o revisión).
- **Bloque C** — root cause exacto (qué constructo no compilaba y por qué tumbaba el modded MissionServer).
- **Bloque D** — handoff para el usuario: re-correr el spike con
  `& "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-fase2.ps1" -WaitInGameSeconds 300`
  y confirmar que reaparece `[MCP-POC] config loaded` en el server script log y que la suite C1/C2 corre (fase2-verdict.json).

===== PROMPT FIN =====
```
