# Prompt — Step 0 fix de deploy (empaquetar PBO + @-deploy) · Codex sesión 2

Copiar de marcador a marcador en el CLI de Codex.

```
===== PROMPT INICIO =====

Tarea: arreglar el deploy del Step 0 de DayZ-MCP y re-correr el MISMO gate. El gate falló NO por
RestApi ni por el código del mod, sino porque el mod se lanzó como carpeta loose (-mod=P:\DayZ_MCP)
sin PBO: el config.cpp se parsea (aparece "define DayZ_MCP") pero el missionScriptModule no tiene
addon registrado del que compilar scripts/5_Mission, así que corre el MissionServer vanilla. El
referente que SÍ funcionó (@MCPTest) corre desde un PBO en !Workshop\@MCPTest\Addons\MCPTest.pbo.
Esta sesión SOLO arregla el deploy y re-corre. NO cambies la lógica del mod ni añadas features.

## Carga inicial obligatoria

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DAYZ_INFRA.md
   (build con AddonBuilder :68-73; allowFilePatching :52-64; naming de mods :34-36; diag :40).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-step0.ps1
   (el orquestador a parchear).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\MCPTest\mcp-shot-test.ps1
   (referencia del lanzamiento que funcionó: resuelve mods contra !Workshop, no contra carpeta loose).

NO toques los .c del mod (MCPBridge/MCPCallbacks/MCPMessages/MissionServer): Claude los revisó y
compilan limpio. El singleton de MCPBridge se queda (el "campo en modded class no compila" era un
mito — los modded class admiten miembros; no lo reviertas, pero tampoco hace falta).

## Cambios (mínimos)

1. **Empaquetar el PBO** con AddonBuilder (DayZ Tools; típico en
   `...\steamapps\common\DayZ Tools\Bin\AddonBuilder\AddonBuilder.exe`), per DAYZ_INFRA.md:68-73:
   ```
   AddonBuilder.exe P:\DayZ_MCP  P:\Mods\@DayZ_MCP\Addons  -prefix=DayZ_MCP  -temp=P:\temp\DayZ_MCP  -clear
   ```
   (P:\ es el symlink a "...\DayZ Projects". P:\Mods → !Workshop, junction ya existe.)
   **Verifica** que `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo` existe y pesa > 0 tras el build; si
   AddonBuilder falla, captura su salida y PARA (no relances el diag con un PBO ausente/stale).

2. **Editar `run-step0.ps1`**:
   - Antes de lanzar el diag, hacer el build del PBO de arriba (parametriza la ruta de AddonBuilder;
     descúbrela en runtime, NO hardcodees un mount de sandbox).
   - Cambiar el launch del server de `-mod=$ModSource` (carpeta loose) a **`-mod=P:\Mods\@DayZ_MCP`**.
   - Para el gate, el PBO basta: puedes **quitar `-filePatching`** del server (elimina una variable;
     los scripts van dentro del PBO). `allowFilePatching=1` en serverDZ.cfg puede quedarse (inocuo).
   - Mantén el resto igual (mission mínima, config $profile:, recolección de RPT + Python stdout).

3. **Re-correr** `run-step0.ps1` y recoger evidencia.

## Recolección de evidencia (más que la vez anterior)

La vez pasada solo se capturó "[MCP-STEP0]: ninguno", que no basta para distinguir causas. Esta vez,
del RPT del server, captura TAMBIÉN:
- Las líneas de **carga del addon/mod** (`DayZ_MCP`, "Addons", PBO, mod loaded).
- Cualquier **error de compilación** de scripts (`Compile`, `error`, `Cannot`, `undefined`, `5_Mission`).
- Las líneas `[MCP-STEP0]` (si aparecen) y el `GATE=PASS/FAIL`.
Y del Python: los hits `GET /poll` y `POST /result`.

## Regla de decisión

- Si aparecen `[MCP-STEP0]` y `GATE=PASS` (+ hits Python) → Step 0 superado. Handoff a Step 1.
- Si el PBO se desplegó OK pero SIGUE sin `[MCP-STEP0]` → ya NO es deploy. Pega las líneas de
  carga del addon + errores de compilación del RPT y PARA. Entonces toca revisar hook/dependencias
  del modded MissionServer (no es esta sesión; repórtalo en el handoff). NO vayas al fallback
  client-first sin antes haber probado el PBO.

## Restricciones

1. NO cambies la lógica de los .c del mod ni añadas features (servidor completo, A1-A5, A2 in-flight).
   Esto es solo deploy + re-run del MISMO smoke.
2. Rutas del HOST (no mounts de sandbox); descubre/parametriza AddonBuilder y P:\.
3. Naming: el mod es `DayZ_MCP` (con guion bajo, válido; sin guiones — DAYZ_INFRA.md:34-36).
4. NO te auto-revises (R21 es sesión aparte). Arregla el deploy, corre, reporta, para.

## Output esperado (A/B/C/D)

### Bloque A — Archivos modificados (run-step0.ps1) + artefacto PBO (path + tamaño).
### Bloque B — LITERAL: salida de AddonBuilder (éxito), Python stdout (hits), y el RPT excerpt
(carga de addon + errores de compilación si hay + líneas [MCP-STEP0]) + veredicto GATE=PASS/FAIL.
### Bloque C — Hallazgos (qué cambió en el deploy, si AddonBuilder dio guerra, etc.). Si nada: "Sin hallazgos."
### Bloque D — Handoff: estado; si PASS → Step 1 (servidor completo + seguridad); si FAIL pese al PBO →
qué dicen las líneas de addon/compilación del RPT y la hipótesis siguiente.

===== PROMPT FIN =====
```

## Notas para Claude (receptor)

- Verificar que `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo` existe (Read/Glob host-direct) si Codex dice que lo construyó.
- Bloque B: confirmar que el RPT excerpt parece real (no parafraseado) — pre-output P2.
- Si PASS → prompt Step 1. Si FAIL con PBO → analizar las líneas de addon-load/compile antes de tocar el hook.
