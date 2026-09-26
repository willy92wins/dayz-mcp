# Sonnet 5 — grupo B lifecycle/build

El contrato `common.md` viene inyectado en este mismo mensaje. Úsalo desde contexto: no abras,
busques ni enumeres `reviews/**`. Revisa:

| ID | Plan | SHA-256 |
|---|---|---|
| 8f8c | `plans/inbox-20260830/05-fb-20260829-023649-8f8c.md` | `9ac1db530bedb6617fae6cb1304ddd457582d8a3aa6d703dd1b1a618b57624b0` |
| 4d66 | `plans/inbox-20260830/16-fb-20260829-104608-4d66.md` | `2e566fd69c3741e40c5c31c4c2181389dce575b20dc23fc1ba0d506d94fc9f70` |
| 7c88 | `plans/inbox-20260830/17-fb-20260829-104625-7c88.md` | `e2462406d04782c497a75de79e8a849effee07cb9dd23316d34dd128a5a9a52c` |
| 344d | `plans/inbox-20260830/19-fb-20260829-111016-344d.md` | `408b28aa1b3903185cf029e1d71928b65de0fb79143a11d3722d8e3322f82fb4` |
| 4407 | `plans/inbox-20260830/20-fb-20260829-115147-4407.md` | `5de4dc79c8624e20b4dc23b2e56ab1cda2788317c03a9267185aca00608b73b1` |
| a396 | `plans/inbox-20260830/21-fb-20260829-133459-a396.md` | `17783fabbf60d6dfbefbe67dcd1e53f0dfc5da5303caf44709aed686f96e3fea` |
| cc2d | `plans/inbox-20260830/22-fb-20260829-135408-cc2d.md` | `c86ee15c5e89c8fd7c49dc0917896a9f68d70cbec4ffcc1cdb390075c9b403f3` |
| 668f | `plans/inbox-20260830/32-fb-20260830-011217-668f.md` | `d9a7552ca605d558bd584545d6235cef9902bc90508cc33f2e4313538b45538c` |

Ataque obligatorio: reintento sólo por la causa realmente alcanzable; 4d66 writer-fault→restart
debe quedar unknown por generation; 344d posee un receipt exacto y dos fuentes; 4407 debe cerrar
cada fallo filesystem, bundle/allowlists y rollback→install→anchored por APIs soportadas.
