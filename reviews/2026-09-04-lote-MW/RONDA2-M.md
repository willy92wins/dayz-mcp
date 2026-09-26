# Ronda 2 del lote M — aplicada por el orquestador (2026-09-04)

Ronda 1: Composer 2.5 por `cursor-agent` entregó los cinco productos a CIEGAS: sus llamadas de
shell fueron bloqueadas por los pre-hooks del host (`launch-ledger.ps1`, `prime-agent-skills-gate.ps1`,
`gpu-lease-gate.ps1`, error de sintaxis de bash sobre PowerShell `| & { ... }`), así que ningún gate
corrió en su sesión. Recepción: sello 4/4, write-set limpio, diff LF +58 -14, G1 48/50, G2 17/18.

Dos huecos, ambos límite del ENCUADRE (mío), no del delegado, cerrados por mí en el workspace en
vez de una segunda ronda ciega (cambios de <10 líneas, reversibles):

1. `tools/dayz_mcp/server.py` `_patch_closed_tool_schema`: `tool.parameters.setdefault("required", [])`
   — la ficha 23 exige `required=[]` explícito en el schema 0-arg y el oráculo lo mide (P2-A);
   el brief decía "required == []" en S0 pero el helper solo ponía `additionalProperties`.
2. `tools/tests/test_weak_agent_consumer_ux.py::test_capture_webp_uses_webp_mime`: el test
   llamaba `tool.fn()` y esperaba UNA `Image`; P3 devuelve `[Image, meta JSON]` en las dos ramas
   por contrato (ficha 03, plan `:41`). El fichero estaba FUERA del write-set del brief (mi
   allowlist se quedó corta; el implementador lo anunció en LO QUE NO PUDE VERIFICAR en vez de
   tocarlo, que es lo correcto). Se toma `result[0]` y se afirma la forma de dos bloques.

Todo lo delegado y lo parcheado se revisa por otra familia (Codex) sobre el diff integrado.
