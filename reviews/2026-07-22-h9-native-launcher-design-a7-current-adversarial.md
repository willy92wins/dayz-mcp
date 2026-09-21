# BUG-046 / H9 — Reauditoría adversarial de A7 actual

Fecha: 2026-07-22  
Revisor: subagente adversarial `bug046_plan_adversarial`  
Modo: read-only, independiente  
Veredicto de implementación: **BLOCKED**

## Objeto exacto revisado

- Plan: `plans/2026-07-22-bug046-h9-native-launcher-plan.md`
- SHA-256 de los bytes actuales: `A9F54A7D8B155ED22C35BC0DA4DC9049C661D101A21D5B9FAA505DE2A37294A6`
- Revisión técnica: 0 Critical / 0 High nuevos.

## Bloqueos de trazabilidad y autorización

1. El encabezado del plan acredita `D65FCE3F...B96B7`, pero ese no es el SHA de los bytes actuales. La atestación R4 acredita exclusivamente A4/`9A3E1290...8083`; ninguna de esas referencias acredita A7/`A9F54A7D...94A6`.
2. `apruebo A4` ratifica las tres decisiones humanas conservadas en A7: CPython embebible fijado, PE reproducible sin Authenticode y las dos diferencias seguras respecto al `.ps1`. No autoriza automáticamente todo el alcance añadido por A7.
3. La solución al self-hash es una atestación detached: el plan no debe contener el SHA de todos sus propios bytes. Tras aprobación se elimina ese digest del encabezado, se congela el plan y un sidecar create-only acredita su SHA final.

## Gate

No implementar ni registrar A7 hasta una aprobación humana explícita de esa revisión. El registro debe permanecer vacío y el launcher fail-closed.
