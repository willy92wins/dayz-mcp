# Claude Sonnet 5 — reentrada 1 de planes corregidos

Lee primero `reviews/2026-08-30-inbox-plan/requests/claude-sonnet/common-rework-1.md` y aplica su formato literal.

| ID | Plan | SHA-256 |
|---|---|---|
| b2c4 | `plans/inbox-20260830/28-fb-20260829-221423-b2c4.md` | `1301e22f8432f9d0f099c663fb4886a9f849f601b512d0afb50a3a86b515c197` |
| 668f | `plans/inbox-20260830/32-fb-20260830-011217-668f.md` | `71157e66b4699613163c130d48f223855c8a99b2d2c12e18e504feb33b62bbaf` |
| d366 | `plans/inbox-20260830/26-fb-20260829-194752-d366.md` | `46b52af14529e2ce66d1ce13dad9f88cf4a11682931b45a19f9fba0076bdd508` |

Tensiones obligatorias: b2c4 no reclama M05, separa hechos actuales de `ambiguous_path`
futuro y demuestra que ambos errores de resolución sobreviven la allowlist M22 en los cuatro
verbos y transportes; 668f distingue `Die` por ExitCode/PBO ausente de `Warn` por tamaño
menor de 4096 sin debilitar los mutantes E6; d366 no usa revisiones históricas como autoridad ni
acredita como existente un banco v5 todavía `[DESIGN]`. Revalida además DPF, OWNS, DAG,
criterios independientes y hashes. Emite exactamente tres bloques completos y `GROUP_VERDICT`.

