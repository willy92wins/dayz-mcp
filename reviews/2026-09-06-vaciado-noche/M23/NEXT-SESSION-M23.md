# Carga inicial de la sesión dedicada M23 (decisión de Guillermo, picker 2026-09-06 ~18:40: «Sí, mañana tras el reset de Codex»)

Objetivo del lote: el instrumento de esquema efectivo como AUTORIDAD del contrato de las tools (gate del repo, promotor,
CAS/journal) y la señal de registro desactualizado para sesiones cliente (9b7b). Codex vuelve a tener cuota el
2026-09-07 04:27. Implementa Grok/Cursor (`cursor-grok-4.6-xhigh`; `grok.exe` daba 402), revisa Codex; el revisor cruzado
es de otra familia siempre (G7).

## Léelo entero antes de planificar

1. `PLAN-M23.md` (Codex, effort max, 586 líneas, completo; escrito sobre `8ff937e`, HEAD hoy `0873d53`: las citas de
   `server.py` ≥ 298 van +17/+19 y `result_prune.py:69→75`).
2. `REVIEW-ANTHROPIC-plan.md` (APROBAR CON CAMBIOS MENORES; R-001..R-015; tabla de 45 citas verificadas).
3. `DIGEST-M23-composer25.md` (mapa rápido; se equivoca en un punto: el TypeError con enum no escalar YA está corregido
   en HEAD, `tests/test_effective_schema.py::test_non_scalar_enum_values_do_not_crash_the_audit`).
4. `BRIEF-PLAN-M23.txt` (el contrato del entregable, sección 3) y las fichas d366/9b7b/141e (`FICHAS-M23.txt` en el
   scratchpad de la sesión Vaciado o vía `pipeline_inbox include_resolved=true`).
5. Estado del buzón: d366 y 141e RESUELTAS por texto el 2026-09-06 (el instrumento `tools/dayz_mcp/effective_schema.py`
   ya está en HEAD: `8732ee3`..`7fe4cb1` + `ae64fdd`); 9b7b ABIERTA; ficha nueva `fb-20260906-160935-9e0d`
   (`tool_registry_source_stale` siempre `unknown`: `server.py:606-624` alimenta `read_authority_marker` con todos los
   bytes a None).

## Primeras tareas, en este orden

1. Cerrar el P1 R-001 del plan antes de L4: el cliente MCP corre desde fuente (`install-mcp.ps1:479` registra
   `-m dayz_mcp --client`; `server.py` no está en PACKAGED_MODULES), así que no existe «despliegue Python» al que atar el
   promotor y la regla «hash local ≠ autoridad ⇒ stale + reopen_mcp_client» invertiría la señal. Fix acotado propuesto por
   el revisor: comparar `source_files` del artefacto con el disco (`authority_behind_sources`) + dimensión local «cierre de
   fuentes cargado vs disco» por SHA-256; decidir quién corre el promotor (pregunta Q6).
2. Responder Q7 (coste de contexto: +10-12 KB por sesión si se duplica el `inputSchema` en prosa) y R-005
   (`dayz_test_modes.py` SÍ está sellado en `app.pyz`: la autoridad no puede publicar un modo que el worker rechace).
3. Re-revisión del plan corregido por Codex (R22, otra familia respecto a Anthropic) antes de escribir GATES.md.
4. Lotes L0 → L1 → L2 (lote mínimo útil: entrega la pieza (a) y el gate real); (b) y (c) después, con el ledger de puertas
   escrito ANTES de implementar (`gates-ledger`).

## Fronteras

- Módulos sellados en `app.pyz` exigen rebuild (ventana exclusiva, coordinada con la sesión que lleve el daemon).
- El daemon vivo no carga `server.py`; una sesión MCP solo ve el nuevo overlay si se abre después del commit.
- Vault y HANDOFF: patch solo bajo anchor propio; `decisions/decision-log.md` está staged por una tercera sesión.
