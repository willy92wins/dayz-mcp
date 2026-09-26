# Contrato Grok — paquetes restantes de ronda 2

Primer gate externo de planes DayZ_MCP, estrictamente de solo lectura en `P:/DayZ_MCP_dev`.
No implementes, edites, escribas, uses MCP/red ni lances DayZ. Revisa CADA ficha del paquete de
forma individual contra source, feedback/research, `product-spec.md` y
`plans/inbox-20260830/00-execution-dag.md`.

La identidad aprobable es el SHA-256 individual indicado. Comprueba que la entrada correspondiente
de `plans/inbox-20260830/plan-manifest.sha256` y los bytes del plan coinciden. Un cambio posterior
del manifiesto causado únicamente por otra ficha no invalida ésta; cualquier drift de la ficha
revisada sí produce `INCONCLUSIVE`. El product-spec congelado es
`36ac7d864e953b002af9135aab688ad754e34c752f0dc429e3abb2d0169501cd` y el DAG
`989b45271d2b607b5360fba55a2808695b0706168eee94ff8518624e6668fb1c`.

Valida `[EXACT]` con `path:line`; `[DESIGN]` como contrato objetivo materializable; DPF e `Intent`;
petición completa; legacy/rollback; fail-closed; no refactor incidental; `OWNS` y dependencias sin
solape; PASS/FAIL/INCONCLUSIVE concretos e independientes; verificadores no tautológicos. No
apruebes por paquete ni rechaces un nombre `[DESIGN]` sólo porque aún no exista.

Primera pasada: exactamente un bloque por ficha con `ID/PLAN/SHA256/VERDICT/FINDINGS/WHY`, más
`GROUP_VERDICT`. `PASS` exige `FINDINGS: - NONE`. Después recibirás calibración en la misma sesión.

