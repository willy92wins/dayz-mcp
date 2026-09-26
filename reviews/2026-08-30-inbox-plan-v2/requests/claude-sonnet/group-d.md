# Sonnet 5 — grupo D DayZ/effect

El contrato `common.md` viene inyectado en este mismo mensaje. Úsalo desde contexto: no abras,
busques ni enumeres `reviews/**`. Revisa:

| ID | Plan | SHA-256 |
|---|---|---|
| 55dd | `plans/inbox-20260830/06-fb-20260829-024747-55dd.md` | `2dbfe3dac2886391e262a2660fe99f3034923ae8a4c6f1fe23161575b270156c` |
| 251d | `plans/inbox-20260830/10-fb-20260829-025502-251d.md` | `3eef856a73097671512f0c08a99616fa2e493e4c1db4a535614199ef65ba73a0` |
| d73b | `plans/inbox-20260830/12-fb-20260829-030056-d73b.md` | `0b51c2b75fd44b78527b553839ba471e42885626fa06c61fb2ec48d5b8564fe2` |
| fc6e | `plans/inbox-20260830/13-fb-20260829-032121-fc6e.md` | `0271c035ae0d75a05fecfca50df68780f849082075b0cbed6433b98d0f824648` |
| 243b | `plans/inbox-20260830/14-fb-20260829-103347-243b.md` | `57a9d751b56be87afe1a7765de20668678038081f65fea489a79f4239ef35ca2` |
| 0de3 | `plans/inbox-20260830/30-fb-20260830-002237-0de3.md` | `d4d1c6551b247a7d818953986a529471bd79507539880953e909e08fa1a1c2ca` |
| 40e4 | `plans/inbox-20260830/34-fb-20260830-112438-40e4.md` | `ec16a704814b788211ce8e5b57007f648b026a83a5b4ff81db802ff68fcd9393` |

Ataque obligatorio: diferencia wire-success/effect; `y==0` resuelve SurfaceY antes de flags;
transporte cubre entidad no-CarScript y `CrewMemberIndex=-1`; `has_cargo` vive por entity; los
gates de efecto no heredan PASS de otra ficha ni confunden divergencia con causalidad.
