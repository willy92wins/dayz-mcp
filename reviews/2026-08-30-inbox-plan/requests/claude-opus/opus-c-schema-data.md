# Claude Opus 5 — grupo C schema/datos locales

Lee primero `reviews/2026-08-30-inbox-plan/requests/claude-opus/common.md` y aplica su formato.
Esta ficha reproduce el censo fijado; root sólo la invoca para grupos/IDs con PASS Sonnet. No
busques ni recibas ese PASS ni ninguna salida Sonnet.

| ID | Plan | SHA-256 |
|---|---|---|
| 268a | `plans/inbox-20260830/03-fb-20260828-224835-268a.md` | `23c2755ba3ec1a790bd49cf7a5c38fab8b3b5142549a73e11eda1ba5428b9606` |
| 9b7b | `plans/inbox-20260830/07-fb-20260829-024827-9b7b.md` | `ab183269aead3a0b634377707705b63a41b09e6b4df6bdede3bdce58289e9616` |
| c7ca | `plans/inbox-20260830/08-fb-20260829-024848-c7ca.md` | `a0696b848fb9c90694c5dcd857805d94f1f65c903bac2d355a87da3d9fcc35fa` |
| 103f | `plans/inbox-20260830/09-fb-20260829-025012-103f.md` | `b0993a0896c87af197b74b58be8560f6c598d2e0547015dee26addf2d28bd881` |
| f201 | `plans/inbox-20260830/11-fb-20260829-025754-f201.md` | `a4a762a80d944f1f588b5d2a6e4ccb3c8c33f15a2964d903fbcb0eb29559f0c3` |
| 141e | `plans/inbox-20260830/18-fb-20260829-104630-141e.md` | `e74f6bec177bab0b417b31f157706459b209118171693b5a174e51042e388d54` |
| 782b | `plans/inbox-20260830/23-fb-20260829-135727-782b.md` | `40cc5434cd2882fb655fec60810914cf9538ab8617f37ba5b11acd6852059f53` |
| d366 | `plans/inbox-20260830/26-fb-20260829-194752-d366.md` | `46b52af14529e2ce66d1ce13dad9f88cf4a11682931b45a19f9fba0076bdd508` |
| ffc7 | `plans/inbox-20260830/27-fb-20260829-194823-ffc7.md` | `eee764ee969a2f3784505e3ab9e40dccaae51a610105fb19ef0220c999becaab` |
| 9d46 | `plans/inbox-20260830/31-fb-20260830-010517-9d46.md` | `e70d6a99310c946fd43c3b1624c526698f1bf45af4e29a06794ec30ba841c266` |

Tensión obligatoria: autoridad esperada independiente del extractor, no fingerprint/comparación
contra sí mismo; unknown no se promociona a fresh; crop client usa rect real independiente; inbox
preserva JSONL legacy; Knowledge no introduce path/red. Revalida todo contra el DAG nuevo.
