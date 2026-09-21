# Transición de revisor formal — Grok mediante Cursor

Fecha: 2026-08-31  
Estado: vigente para el cierre de las 35 entradas del inbox DayZ MCP

## Decisión del usuario

La última instrucción sustituye a Claude Opus 5 por Grok para toda revisión que no hubiera
empezado y extiende el mismo cambio al paso 5, la revisión posterior a la implementación.
Sonnet continúa retirado.

El protocolo vivo queda:

1. Codex produce y congela el plan individual.
2. Grok 4.6 Medium mediante Cursor revisa el hash congelado.
3. Codex materializa únicamente tras PASS del plan.
4. Grok 4.6 Medium mediante Cursor revisa los bytes/delivery finales en otra sesión fresca.
5. Sólo entonces se resuelve la entrada del inbox.

## Celda y evidencia exigida

- Modelo solicitado: `cursor-grok-4.6-medium`.
- Ruta: `cursor-agent`, cuenta Cursor Ultra.
- Modo: `--mode ask`, sólo lectura.
- Identidad: el evento `system/init` debe declarar `Cursor Grok 4.6 Medium`.
- Término: un único evento `result` con `subtype=success`, `is_error=false` y el mismo
  `session_id` que `init`.
- Forma: un veredicto y hash independiente por feedback; PASS exige cero hallazgos abiertos.
- Frontera: sin `reviews/**`, `gates/**`, informes de otros revisores ni historia git en contexto.

Para ahorrar el peaje fijo de Cursor se agrupan hasta cinco fichas por sesión y no se añade una
segunda llamada de calibración: la identidad ya se valida en `init`. La revisión de implementación
usa una sesión nueva y no reanuda la sesión del plan.

## Tratamiento de corridas anteriores

Las corridas Opus que ya estaban en marcha al llegar la orden pudieron terminar para no desperdiciar
su trabajo y sirvieron como descubrimiento de defectos. Sus PASS/REVISE no satisfacen `PLAN-GROK`
ni `REVIEW-GROK`, y quedan fuera de los manifiestos formales vigentes. Los artefactos Sonnet y el
manifiesto de retirada también son históricos y no condicionan el gate Grok/Cursor.
