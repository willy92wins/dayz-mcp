# Prompt — Step 0 config-path (write a mission dir + Test-Path) · Codex sesión 4

```
===== PROMPT INICIO =====

Tarea: resolver `config_url_empty` del Step 0. Verificado (Claude): el mod NO encuentra el config en
`$profile:` (JsonLoadFile hace FileExist y es no-op silencioso si falta; no hubo parse-error en RPT).
**Claude YA actualizó el mod** `DayZ_MCP\scripts\5_Mission\MCPBridge.c`: ahora intenta `$profile:` y
si no, `$mission:`, logueando `FileExist` de cada uno y de cuál cargó. **NO toques el mod.** Tú solo
cambias el orchestrator (escribir el config también en el dir de la mission + verificar), re-empaquetas
y re-corres.

## Carga inicial

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-step0.ps1 (el que editas).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
   (solo para LEER el TryInit nuevo y entender qué loguea; NO lo edites).

## Cambios en run-step0.ps1 (mínimos)

1. Justo después del `Set-Content` que escribe `$ConfigFile` (= `$Profiles\dayz_mcp.json`), escribe el
   **mismo** JSON también en el dir de la mission que pasas a `-mission=` (la variable `$MissionWs`):
   `$MissionConfig = Join-Path $MissionWs "dayz_mcp.json"` + `Set-Content -LiteralPath $MissionConfig -Encoding ASCII -Value $jsonConfig`.
   (Asegúrate de que `$MissionWs` existe antes de escribir; si no, créalo.)
2. Tras escribir ambos, **verifica y loguea** cada uno (path absoluto + existe + tamaño):
   `Info ("config profiles -> {0} exists={1} bytes={2}" -f $ConfigFile,(Test-Path $ConfigFile),((Get-Item $ConfigFile -EA SilentlyContinue).Length))`
   y lo mismo para `$MissionConfig`. (Esto nos dice si el write aterriza y dónde.)
3. NO cambies nada más (el `-packonly`, el check de PBO y el launch ya están bien).

## Correr

4. Re-corre `run-step0.ps1` (re-empaqueta solo, no toca el mod). Precheck que sí sigue válido:
   `grep -nE '^\s*api\.SetOption\(' MCPBridge.c` = 0 (el comentario que menciona ERESTOPTION SE QUEDA;
   el PBO contendrá esa cadena en el comentario y es ESPERADO).

## Recoger evidencia

Del orchestrator: las 2 líneas `config profiles -> ...` / `config mission -> ...` (Test-Path + bytes).
Del RPT, las líneas `[MCP-STEP0]` — en orden esperado:
`restapi=OK` · `config $profile: FileExist=0/1` · `config $mission: FileExist=0/1` ·
`config loaded from <path>` · (si llega) `poll.OnSuccess` · `parsed cmds=2` · `callbacks=...` ·
`result.posted/OnSuccess` · `GATE=PASS/FAIL`.
Del Python: hits `GET /poll` y `POST /result`.

## Regla de decisión

- `GATE=PASS` (+ hits Python) → **Step 0 superado**: confirma RestApi async server-side. Handoff a Step 1.
- Sigue `config_url_empty` con **ambos** `FileExist=0` → el config no aterriza donde el server lee:
  mira las 2 líneas Test-Path del orchestrator (¿se escribió? ¿dónde?) y pega RPT+esas líneas. Para.
- Un `FileExist=1` pero `config_url_empty` → entonces SÍ es parse: pega el contenido del json que se
  escribió. Para.
- Carga el config pero falla en `poll`/`result` → ese ya es el test real del transporte; repórtalo entero.

## Restricciones

1. NO toques el mod (`.c`). NO añadas features (servidor completo, A1-A5, Step 1). Solo el orchestrator.
2. NO re-copies desde el snapshot de staging `_tmp_dayz_mcp_step0`. NO `git`. NO te auto-revises (R21 aparte).

## Output (A/B/C/D)

A — run-step0.ps1 modificado + PBO (path/tamaño/mtime).
B — LITERAL: las 2 líneas Test-Path del orchestrator; Python stdout (hits); RPT excerpt con TODAS las
    líneas `[MCP-STEP0]` (sobre todo las `config ... FileExist=` y `config loaded from`); veredicto GATE.
C — Hallazgos (qué prefijo resolvió, si alguno; si no, "Sin hallazgos").
D — Handoff: estado; si PASS → Step 1; si FAIL → qué dicen las líneas FileExist/Test-Path y la hipótesis.

===== PROMPT FIN =====
```

## Notas para Claude (receptor)

- El objetivo real de esta corrida es doble: pasar el gate Y aprender qué prefijo (`$profile:` vs
  `$mission:`) resuelve server-side, para LUEGO fijarlo en el plan §3a/§3d (no fijar el spec hasta saberlo).
- Verificar que Codex no tocó el mod (grep MCPBridge.c host-direct: debe tener el dual-path que metió Claude).
