# Atestación detached — BUG-046 / H9 plan A7 aprobado

Fecha: 2026-07-22  
Estado: **APPROVED / FROZEN**

## Objeto aprobado

- Plan: `plans/2026-07-22-bug046-h9-native-launcher-plan.md`
- SHA-256 A7 presentado al usuario: `A9F54A7D8B155ED22C35BC0DA4DC9049C661D101A21D5B9FAA505DE2A37294A6`
- Respuesta humana exacta: `Apruebo A7`
- Revisión técnica previa: 0 Critical / 0 High nuevos, documentada en
  `reviews/2026-07-22-h9-native-launcher-design-a7-current-adversarial.md`.

## Normalización detached

Después de la aprobación se eliminó exclusivamente el self-hash obsoleto del header y
se sustituyó por una referencia a esta atestación. No cambió arquitectura, alcance,
criterios, fases ni decisiones A7.

- SHA-256 final congelado del plan: `92EB7C20AC3CCDBB4A5F30D52AA404DDBD8437589AA08CC3919CD121C482D391`
- Tamaño final: `53588` bytes

El digest vive fuera de los bytes que acredita. Cualquier cambio posterior del plan
invalida esta atestación y exige una nueva revisión/aprobación.

## Gate

Queda autorizado comenzar las fases TDD de A7. El registro del launcher debe seguir
vacío hasta que pasen todas las fases, los gates combinados y la revisión independiente
final exigidos por el plan.
