# Cola aislada de retrabajo de planes

Esta cola permite continuar los lotes independientes sin promover un plan con hallazgos.
Cada entrada conserva su identidad y debe volver a pasar Sonnet y después Opus con el nuevo hash.

## fb-20260829-221423-b2c4

- Estado: corregido y `PASS` de Claude Sonnet 5 sobre hash nuevo; espera `PLAN-OPUS`.
- Plan revisado: `plans/inbox-20260830/28-fb-20260829-221423-b2c4.md`
- Hash rechazado: `75cfbdb7038193da216485ea2c2b66443f271dba77659efb198b2706f2185f23`
- Evidencia: `reviews/2026-08-30-inbox-plan/sonnet/group-a.md` (`sha256:388eaec770619a19eea034416c62ca845c906300b012c33323d4d34e7e84600c`).
- Correcciones exigidas:
  1. retirar la autoasignación de `M05-UI-ENFORCE` y expresar que la ficha consume el resultado estructurado producido por los propietarios de M05 registrados en el DAG;
  2. separar el hecho actual `[EXACT]` (`widget_not_found` y restricciones por verbo) del contrato futuro `[DESIGN]` (`ambiguous_path`), y cubrir ambos errores de resolución en los criterios TDD de la allowlist M22.
- Reentrada Sonnet cerrada: `reviews/2026-08-30-inbox-plan/sonnet/rework-1.md` (`sha256:f63b85dbf6ddf48bc4cdab19d335a46834e83b5d957ec68f2b366d853ffccd06`), plan nuevo `sha256:1301e22f8432f9d0f099c663fb4886a9f849f601b512d0afb50a3a86b515c197`; siguiente gate: Opus fresco y ciego a Sonnet.

## fb-20260830-011217-668f

- Estado: corregido y `PASS` de Claude Sonnet 5 calibrado sobre hash nuevo; espera `PLAN-OPUS`.
- Plan revisado: `plans/inbox-20260830/32-fb-20260830-011217-668f.md`
- Hash rechazado: `1b6e89fdc3880a9561076dc474f5a97b7c7455e34a632fbbfd9f1d03164742ca`
- Evidencia: `reviews/2026-08-30-inbox-plan/sonnet/group-b.md` (`sha256:80dd31889563629c1c8a4f61f0c7b0e97acd97bef128ad34add6e71467f831c6`).
- Corrección exigida: sustituir el hecho `[EXACT]` que dice que la plantilla falla por ausencia/tamaño del PBO; el código falla por `ExitCode != 0` o PBO ausente, mientras un PBO menor de 4096 bytes solo provoca `Warn` no bloqueante.
- Reentrada Sonnet cerrada: `reviews/2026-08-30-inbox-plan/sonnet/rework-1.md` (`sha256:f63b85dbf6ddf48bc4cdab19d335a46834e83b5d957ec68f2b366d853ffccd06`), plan nuevo `sha256:71157e66b4699613163c130d48f223855c8a99b2d2c12e18e504feb33b62bbaf`; siguiente gate: Opus fresco y ciego a Sonnet.

## fb-20260829-194752-d366

- Estado: corregido y `PASS` de Claude Sonnet 5 calibrado sobre hash nuevo; espera `PLAN-OPUS`.
- Plan revisado: `plans/inbox-20260830/26-fb-20260829-194752-d366.md`
- Hash rechazado: `f10ce2996578f542fa36e9382714b26e769dc5b3ad80fb949364c4ce66dee8f0`
- Evidencia: `reviews/2026-08-30-inbox-plan/sonnet/group-c.md` (`sha256:947d1d71585b7d6fe55386ce153c695d7d9dcfe832fcb4bb417750558eab6022`).
- Corrección exigida: sustituir la cita `[EXACT]` de la línea 37 a un artefacto histórico `reviews/**` por el `path:line` del banco/tests v5 vigente si ya existe; si todavía no está materializado, etiquetar esa afirmación como `[DESIGN]` y no usar la revisión histórica como autoridad ejecutable.
- Reentrada Sonnet cerrada: `reviews/2026-08-30-inbox-plan/sonnet/rework-1.md` (`sha256:f63b85dbf6ddf48bc4cdab19d335a46834e83b5d957ec68f2b366d853ffccd06`), plan nuevo `sha256:46b52af14529e2ce66d1ce13dad9f88cf4a11682931b45a19f9fba0076bdd508`; siguiente gate: Opus fresco y ciego a Sonnet.
