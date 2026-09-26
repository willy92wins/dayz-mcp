# Prompt — Step 0 re-run tras compile fix · Codex sesión 3

Copiar de marcador a marcador en el CLI de Codex.

```
===== PROMPT INICIO =====

Tarea: re-correr el MISMO gate del Step 0 de DayZ-MCP. El compile error ya está corregido (Claude
quitó las 2 líneas `RestApi.SetOption(ERESTOPTION_*)` de MCPBridge.c, porque ESas constantes no son
script-accesibles en runtime 1.29 → "Can't find variable" → tumbaban el módulo Mission; timeout
default 10s basta). NO cambies código. Esta sesión solo re-empaqueta, re-corre y reporta.

## Antes de correr (checks)

1. Confirma que el fix está en la copia que empaquetas (el repo):
   `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c`
   NO debe contener una **LLAMADA ejecutable** `api.SetOption(` — `grep -nE '^\s*api\.SetOption\(' ` = 0.
   El comentario que menciona `ERESTOPTION`/`RestApi.SetOption` es la nota anti-regresión y **SE QUEDA**
   (no compila, no matchea ese grep). (Si ves un `api.SetOption(` ejecutable, estás mirando el snapshot
   viejo de staging `_tmp_dayz_mcp_step0` — NO lo uses; la copia viva es la del repo.)

## Correr

2. Re-corre `run-step0.ps1` (ya empaqueta con AddonBuilder `-packonly` y hace el check de PBO que
   añadiste en la sesión 2). Si por lo que sea no re-empaqueta, hazlo antes a mano:
   `AddonBuilder.exe P:\DayZ_MCP P:\Mods\@DayZ_MCP\Addons -prefix=DayZ_MCP -temp=P:\temp\DayZ_MCP -clear -packonly`
3. El PBO **contendrá** la cadena `ERESTOPTION` dentro de ese comentario — es ESPERADO, no un fallo.
   La prueba de que se empaquetó el código corregido es el RPT: el módulo Mission compila **SIN**
   `Can't find variable` / `Can't compile Mission script module` (los comentarios no compilan).

## Recoger evidencia

Del run: Python stdout (hits `GET /poll` y `POST /result`) + del RPT del server:
- que YA NO aparezca el error de compilación del módulo Mission (ni `Can't find variable`).
- las líneas `[MCP-STEP0]` (`restapi=OK`, `config ...`, `poll.OnSuccess`, `parsed cmds=2`,
  `callbacks=...`, `result.posted/OnSuccess`) y el `GATE=PASS/FAIL`.

## Restricciones (vinculantes)

1. NO re-añadas `SetOption`/timeouts ni los sustituyas por enteros mágicos ni por otra API de
   timeout (ninguna está verificada en runtime; el default 10s va bien). 
2. NO toques ninguna otra lógica del mod ni empieces el servidor completo / A1-A5 / Step 1.
3. NO re-copies archivos desde el snapshot de staging `_tmp_dayz_mcp_step0` (está atrás respecto al repo).
4. NO te auto-revises (R21 aparte).

## Regla de decisión

- Si el módulo compila y aparece `[MCP-STEP0] ... GATE=PASS` (+ hits Python) → **Step 0 superado**;
  handoff a Step 1 (servidor completo + seguridad). Esto confirma RestApi async server-side.
- Si aparece OTRO compile error de Mission → para, cítalo con `path:line`, y aplica R2 (grepea el
  USO del símbolo en el source vanilla antes de tocar; cero uso = red flag). NO improvises el fix.
- Si compila pero NO hay `[MCP-STEP0]` ni hits Python → ya no es compile; pega del RPT las líneas de
  carga del addon + cualquier error y la traza de OnMissionStart, y para. (Aún NO fallback client-first.)

## Output (A/B/C/D)

A — Archivos (debería ser ninguno modificado salvo, si acaso, run-step0.ps1) + PBO (path, tamaño, mtime).
B — LITERAL: Python stdout (hits); RPT excerpt que muestre que el módulo Mission compila **sin**
    `Can't find variable` (eso prueba que el fix está empaquetado); líneas `[MCP-STEP0]`; veredicto `GATE=PASS/FAIL`.
C — Hallazgos (si los hay; si no, "Sin hallazgos").
D — Handoff: estado; si PASS → arrancar Step 1; si FAIL → la causa acotada + hipótesis siguiente.

===== PROMPT FIN =====
```

## Notas para Claude (receptor)

- Si PASS: preparar prompt Step 1 (servidor completo + seguridad + el bridge real con cadencia/
  dispatch/backoff/medida in-flight A2). Si FAIL: analizar el RPT antes de tocar (no fallback aún).
- Verificar que Codex no re-introdujo SetOption (grep MCPBridge.c host-direct).
