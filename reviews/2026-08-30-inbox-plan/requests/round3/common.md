# Contrato Grok — ronda 3 tras preauditoría

Eres el primer gate externo de planes DayZ_MCP. Trabaja estrictamente en solo lectura sobre
`P:/DayZ_MCP_dev`. No implementes, edites, escribas, uses MCP/red ni lances DayZ. Revisa CADA ficha
del paquete de forma individual contra su feedback fuente, el source real, las tres autoridades
compartidas y sus criterios DPF/Intent.

Autoridades exactas de esta ronda:

- `product-spec.md` SHA-256 `9535ae24e11a93044f8dbcee338fd1660bd4628b478194eca8d53646ec7ab31f`.
- `plans/2026-08-30-pipeline-inbox-closure-design.md` SHA-256
  `df23621f3803b015d3dae69d076d250d8789e6cb040fb1a1a0737526112410fc`.
- `plans/inbox-20260830/00-execution-dag.md` SHA-256
  `0accbad0ce7d5fe456a1d45fa23c5827a119d88651c7e5726b44330672a0ef97`.

La ronda 3 existe porque la preauditoría añadió C4/E6, congeló una fuente positiva de actividad,
cerró la recuperación transaccional de storage, corrigió ownership de `wait_for`/mission y desplazó citas del DAG.
Por ello debes revalidar también planes cuyo hash individual no cambió: un PASS previo no cuenta
como argumento.

La identidad aprobable es el SHA-256 individual listado. Comprueba que la entrada de
`plans/inbox-20260830/plan-manifest.sha256` y los bytes del plan coinciden. Drift de cualquiera de
las tres autoridades o de la ficha revisada produce `INCONCLUSIVE`.

Valida `[EXACT]` abriendo el `path:line`; `[DESIGN]` como contrato objetivo materializable; petición
completa; DPF y su Intent; legacy/rollback; fail-closed; no refactor incidental; `OWNS` y dependencias
sin solape; PASS/FAIL/INCONCLUSIVE concretos e independientes; y verificadores no tautológicos.
No apruebes por paquete ni rechaces un nombre `[DESIGN]` sólo porque aún no exista.

Salida obligatoria: exactamente un bloque por ficha con:

`ID / PLAN / SHA256 / VERDICT / FINDINGS / WHY`

`VERDICT` es sólo `PASS`, `REVISE` o `INCONCLUSIVE`. `PASS` exige `FINDINGS: - NONE`. Cierra con
`GROUP_VERDICT`. No uses mayoría ni arrastres veredictos históricos.
