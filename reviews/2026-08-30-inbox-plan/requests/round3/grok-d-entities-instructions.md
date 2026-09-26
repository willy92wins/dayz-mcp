# Ronda 3 — grupo D entidades/instrucciones

Lee primero `reviews/2026-08-30-inbox-plan/requests/round3/common.md` y aplica su formato.

| ID | Plan | SHA-256 |
|---|---|---|
| 55dd | `plans/inbox-20260830/06-fb-20260829-024747-55dd.md` | `e0e0114e045c36f9070dc0593476e7ea1186dab20a76f9aee7cb608b99f55b42` |
| 251d | `plans/inbox-20260830/10-fb-20260829-025502-251d.md` | `16572ca7bf5bd6686987654479f9359a00f9627682e5352b9359d434bb250187` |
| d73b | `plans/inbox-20260830/12-fb-20260829-030056-d73b.md` | `6937eb2e3170f9ee9f0c499da420993de398ebf41dc5b2a15b145c9786e2af29` |
| fc6e | `plans/inbox-20260830/13-fb-20260829-032121-fc6e.md` | `7ce83ba16ba5cc93116b58098308935b3907d0a2e8011bb3c218c03d2a16d9b6` |
| 243b | `plans/inbox-20260830/14-fb-20260829-103347-243b.md` | `2ab4d89de9f4107c0789b073edf02aab62a90b2b79238adbde1b97fdb6fd2c5c` |
| 0de3 | `plans/inbox-20260830/30-fb-20260830-002237-0de3.md` | `136bacf233b3572e632bfae54782ee5d885234b658c6e9e053072da76413a490` |
| 40e4 | `plans/inbox-20260830/34-fb-20260830-112438-40e4.md` | `c78057d6d97270b2ff6d2f4a2a79f512024dc17c4b61a64d847bb2a69d1a071a` |

Tensión obligatoria: en fc6e distingue posición validada antes de `CreateObjectEx`, `pos_real`
inicial y simulación posterior; el control y!=0 debe fijar instante y no asumir flags. En 0de3 no
confundas found/seated/seat con métricas CarScript. En 55dd/243b/251d/d73b exige gates semánticos,
no mera presencia textual; 251d/d73b deben trazar al C4 runtime 200/201 y causal 200/0.
