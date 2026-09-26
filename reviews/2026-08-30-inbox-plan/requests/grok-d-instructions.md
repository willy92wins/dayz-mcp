Eres el primer gate externo de planes para DayZ_MCP. Revisa de forma adversarial CADA ficha listada, de manera individual, contra el repo real en P:/DayZ_MCP_dev. No implementes ni edites. Verifica las afirmaciones [EXACT] leyendo source, DPF product-spec.md, research/2026-08-30-pipeline-inbox-triage-codex.md, plans/2026-08-30-pipeline-inbox-closure-design.md y plans/inbox-20260830/00-execution-dag.md cuando sean relevantes. Aplica: APIs y causalidad con path:line; compatibilidad/legacy/rollback; fail-closed; ausencia de refactor incidental; criterios PASS/FAIL/INCONCLUSIVE independientes y no tautológicos; propiedad OWNS sin solape; dependencias realizables. No apruebes una ficha porque el paquete global parezca bueno.

El hash dado es la identidad congelada. Comprueba que coincide con plans/inbox-20260830/plan-manifest.sha256. Si no coincide: INCONCLUSIVE. Para cada ficha devuelve EXACTAMENTE un bloque:
ID: <feedback-id>
PLAN: <path>
SHA256: <hash exacto>
VERDICT: PASS | REVISE | INCONCLUSIVE
FINDINGS:
- NONE, o hallazgos F-01... con severidad, path:line, mecanismo y cambio mínimo exigido
WHY: <razón concreta>
Al final: GROUP_VERDICT: PASS solo si todas las fichas son PASS; si no, REVISE o INCONCLUSIVE. No mezcles IDs ni des un único veredicto de paquete.

Fichas de este paquete:
- fb-20260829-024747-55dd | plans/inbox-20260830/06-fb-20260829-024747-55dd.md | 6395d6fb56e43c0be278019ea452b0a2493ed1ab43e49484c97b8a93b02a987e
- fb-20260829-025502-251d | plans/inbox-20260830/10-fb-20260829-025502-251d.md | 31390b58a92ebd7ea46970ec0044d65772cc3061e11a1f14a84146cc16ce99a0
- fb-20260829-030056-d73b | plans/inbox-20260830/12-fb-20260829-030056-d73b.md | 9b511f2e85e16364fa7640f2da364463da85b71c95071f84d01086a74dfa4db7
- fb-20260829-032121-fc6e | plans/inbox-20260830/13-fb-20260829-032121-fc6e.md | 5bdf239fb738283c643e2c9e652aa615fcb0c0369a3a94f6fe0971740a3c855b

