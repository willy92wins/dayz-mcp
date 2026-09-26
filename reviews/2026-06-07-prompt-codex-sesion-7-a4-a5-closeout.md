# Prompt — Cierre POC: A4 (seguridad) + A5 (resiliencia) · Codex sesión 7

```
===== PROMPT INICIO =====

Tarea: cerrar el POC fase 0 probando **A4 (seguridad fail-closed)** y **A5 (resiliencia)**. A1+A2+A3
ya PASAN in-game (no los rehagas; correrán igualmente porque A5 usa el harness completo). La seguridad
y el backoff YA están implementados (server: bind 127.0.0.1 + key + whitelist; bridge: backoff expo en
OnPollFail) — esta sesión solo los **ejercita y verifica**, con UN fix de contrato puntual en A4
(código HTTP 403→401, ver abajo). NO cambies lógica del mod ni del transporte más allá de ese fix.
NO toques scope fuera de A4/A5 (nada de fase 1, MCP stdio, otras tools).

## Carga inicial

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-06-poc-fase-0-roundtrip.md
   (§3b server seguridad, §3c cliente A4/A5, §3a bridge backoff, §10 criterios).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_client.py (añadir A4/A5).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-poc.ps1 (añadir fase A5).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_server.py (aplicar SOLO el fix
   403→401 de A4; el resto del contrato §3b —whitelist 400, bind-assert 127.0.0.1— ya está correcto, NO tocar).

## A4 — seguridad (se prueba contra el server Python; NO requiere DayZ)

**Fix de contrato previo (gap §3b real, verificado host-direct por Claude 2026-06-07)**: `mcp_server.py`
`_authorized()` (línea 77) devuelve hoy **403** `{"error":"forbidden"}` para key ausente/incorrecta, pero
el contrato §3b del plan y product-spec A4 especifican **401**. Es drift de implementación (el path de auth
nunca se ejercitó en A1-A3, que usaron key válida). Cámbialo a **401**
(`self._json(401, {"error": "unauthorized"})`). Razón: 401 Unauthorized es el código correcto para
credencial ausente/mala; 403 es "autenticado pero sin permiso". El bind-assert 127.0.0.1 (línea ~229) y la
whitelist 400 not_whitelisted (línea ~116) ya están correctos — NO los toques.

Luego añade a `mcp_client.py` un bloque A4 que hace requests crudas al server y valida el status:
- `GET /poll` SIN `?key=` → **401**.
- `GET /poll?key=<wrong>` → **401**.
- `POST /enqueue` SIN key → **401**.
- `POST /enqueue?key=<valid>` con `{cmd:"evil"}` (fuera de whitelist) → **400**.
PASS si los 4 dan el código esperado. (Si alguno NO, es un agujero fail-closed real → reportar.)
Opcional/best-effort: confirmar que el server bindó 127.0.0.1 (no 0.0.0.0) — leer el log de bind (`LISTEN host=127.0.0.1`).

## A5 — resiliencia (in-game, con player spawneado)

En `run-poc.ps1`, tras A1-A3 (player in-game), añade una fase A5:
1. **Matar** el proceso del server Python (Stop-Process del `$py`). Mantenerlo caído ~6 s.
2. Verificar en el RPT del server-diag que el bridge NO crashea y entra en backoff: deben aparecer
   `[MCP-POC] poll error=... backoff_s=` (o `[MCP-POC] poll timeout backoff_s=`) y el server sigue
   tickeando (sin "Can't compile" ni crash). NO debe spamear (las llamadas se espacian por el backoff).
3. **Relanzar** el server Python (mismo `--port` `--keyfile`).
4. Encolar `query_player_state` y esperar hasta ~20 s a que vuelva `ok=1` (el bridge reanuda tras el
   backoff). PASS si reanuda (ok=1 post-relaunch).
(Mantén la caída corta ~6 s: con connection-refused el backoff sube 1→2→4 en esa ventana, sin llegar al
cap de 30 s, y la reanudación cae dentro de los 20 s de await.)

## Output esperado — verdict A1-A5 completo

Actualiza `poc-verdict.json` (o el que uses) para incluir A4 y A5.
- A — archivos modificados (mcp_client.py, run-poc.ps1, mcp_server.py [SOLO el 403→401]) — sin tocar el mod.
- B — LITERAL: A4 (4 status codes), A5 (RPT backoff lines + post-relaunch ok=1), y A1-A3 (siguen PASS).
  Veredicto final: A1-A5 todos PASS → POC fase 0 COMPLETO.
- C — Hallazgos (si A4 destapó algún agujero, o A5 algún crash; si no, "Sin hallazgos").
- D — Handoff: POC fase 0 cerrado → siguiente = R21 (doble revisión del código del POC) y luego
  fase 1 de la arquitectura (control). Deuda: P3 MissionBase/PluginManagerInit log-noise (NO tocar aquí).

## Restricciones

1. Solo A4+A5. El ÚNICO cambio de transporte permitido es el 403→401 de A4 en mcp_server.py; nada más del
   mod/transporte (A1-A3 probados). El backoff y el resto de la seguridad YA existen.
2. A4 modifica mcp_client.py + el 403→401 de mcp_server.py; A5 modifica run-poc.ps1. Ningún otro cambio a
   mcp_server.py salvo que falte algo más del contrato §3b.
3. NO toques el P3 MissionBase/PluginManagerInit (flagged aparte). NO añadas scope (fase 1, MCP stdio).
4. R2 para cualquier API. NO auto-review (R21 es la sesión siguiente). OneDrive: escritura atómica + verificar.

===== PROMPT FIN =====
```

## Notas para Claude (receptor)
- A4: confirmar los 4 status host-direct (no fiarse del resumen): los 3 de auth = **401** (no 403) y el
  de whitelist = **400**. Verificar también que el 403→401 quedó aplicado en `mcp_server.py:77`.
- A5: confirmar las líneas `backoff_s=` en el RPT (server-diag) + el `ok=1` post-relaunch.
- Si A1-A5 PASS → POC fase 0 cerrado. Siguiente: R21 del código del POC, luego fase 1 (control).
