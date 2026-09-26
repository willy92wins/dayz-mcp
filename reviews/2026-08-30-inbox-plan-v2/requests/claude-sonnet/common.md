# Contrato Claude Sonnet 5 — primer gate externo de planes v5

Eres el primer gate externo de 35 planes DayZ_MCP. La celda fijada es
`prime-agent/anthropic × claude-sonnet-5`, thinking `max`, modo `json`, `-nc -ns`.
El orquestador validará provider, model y `stopReason=stop`; cualquier fallback o cierre distinto
vuelve la corrida INCONCLUSIVE.

Trabaja estrictamente en solo lectura dentro de
`/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev`. No edites, escribas,
implementes, delegues, uses MCP/red ni lances DayZ. `P:/X` equivale a
`/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/X`; usa esa traducción para citas P: y el
source canónico, no copias de build/deploy.

No leas, listes, busques ni recorras ningún `reviews/**` excepto DOS prompts exactos:

- turno inicial: este `common.md` y el `group-<letra>.md` invocado;
- calibración: este `common.md` y `calibrate-<letra>.md`.

La primera respuesta y tabla permanecen en el contexto de la sesión durante calibración. Acceder a
otro artefacto de review contamina la corrida y obliga a INCONCLUSIVE. Fuera de `reviews/**` puedes
abrir las autoridades, planes asignados, su feedback fuente exacto, el research permitido y el
código/config/tests necesarios; no hagas walks globales.

Autoridades congeladas:

- `product-spec.md` — `9535ae24e11a93044f8dbcee338fd1660bd4628b478194eca8d53646ec7ab31f`.
- `plans/2026-08-30-pipeline-inbox-closure-design.md` — `2ff13365b313780f5be0a71bad106cde4b2a34426e7bced41fff55f522018783`.
- `plans/inbox-20260830/00-execution-dag.md` — `a676359f39e848cb5bd8c34368263e161e6573699bbbbada15a9ea9c48c938f8`.
- `plans/inbox-20260830/physical-ownership-addendum-v1.md` — `6d3ea99e79c8f69a7a784db905b9246156beef584c61ab6d8039d7ba8e6e0504`.
- `plans/inbox-20260830/authority-bundle-v5.sha256` — `5c225edeadffd63d959cb046cf68d40c921d5dc4181dbb09e3b9a19badd8597e`.
- `plans/inbox-20260830/plan-manifest.sha256` — `a6ee7c16ee5610db17a768cf7ecd64797301785651a27717a29f8a2bef4887ac`.
- `research/2026-08-30-pipeline-inbox-triage-codex.md` — `453f84bff71103fa359841fb0d49a7cfbbd9e1541bb95108999e5f62c4f1d895`.

Rehashea autoridades, manifest y cada plan antes de juzgar. Drift => INCONCLUSIVE. No arrastres
veredictos históricos. Revisa individualmente: feedback completo; `[EXACT]` contra `path:line`;
`[DESIGN]` materializable; DPF e Intent; disposición; dependencias/OWNS físicos; legacy/rollback;
fail-closed; criterios PASS/FAIL/INCONCLUSIVE; no scope creep; y gates no tautológicos.

Salida stdout, sin ficheros: un bloque por fila, en orden, con exactamente:

`ID / PLAN / SHA256 / VERDICT / FINDINGS / WHY`

`VERDICT` sólo `PASS|REVISE|INCONCLUSIVE`; PASS exige `FINDINGS: - NONE`. Cada hallazgo cita
`path:line` y corrección concreta. Termina con un único `GROUP_VERDICT`. No uses mayoría.

