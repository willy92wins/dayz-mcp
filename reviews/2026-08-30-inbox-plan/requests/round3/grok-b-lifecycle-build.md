# Ronda 3 — grupo B lifecycle/build

Lee primero `reviews/2026-08-30-inbox-plan/requests/round3/common.md` y aplica su formato.

| ID | Plan | SHA-256 |
|---|---|---|
| 8f8c | `plans/inbox-20260830/05-fb-20260829-023649-8f8c.md` | `412b627bd4254cc5a82f3cba15cec1ef72770d93ed8627ae6e741232b398e1c3` |
| 4d66 | `plans/inbox-20260830/16-fb-20260829-104608-4d66.md` | `d5a0602e20b501f9d8142108a95f10ee1c11771fbb42308c81c61cfa9654eda7` |
| 7c88 | `plans/inbox-20260830/17-fb-20260829-104625-7c88.md` | `22c4e413e752f23e8dd4de99d52bd0659c477e24218d9d0e21742cbd1fa6daad` |
| 344d | `plans/inbox-20260830/19-fb-20260829-111016-344d.md` | `101eeafbaf4d4ab44d776e514606fd6293f1bb6e5f27d5f3afa7384680bb3245` |
| 4407 | `plans/inbox-20260830/20-fb-20260829-115147-4407.md` | `6644ed6696b7f2285853fe49d0ec40ffe9c67810bce11c010ce785f3c9c62f6d` |
| a396 | `plans/inbox-20260830/21-fb-20260829-133459-a396.md` | `68602dc76c9a29358d995a19e4489d0ad3d8e560731ec97cafc3637ee1740390` |
| cc2d | `plans/inbox-20260830/22-fb-20260829-135408-cc2d.md` | `68da15699cbbe6766dfebd8e2932b2f901416ce3dd911ffdca7ca82e6a37246f` |
| 668f | `plans/inbox-20260830/32-fb-20260830-011217-668f.md` | `1b6e89fdc3880a9561076dc474f5a97b7c7455e34a632fbbfd9f1d03164742ca` |

Tensión obligatoria: `execute_wait_for` no recibe run_id y es M22; la misión absoluta ya la acepta
el parser y el filtro real es M19. Para 4d66 prueba que `Binding.run_id` + evento auditado produce
recent/stale/unknown medibles sin preemptar. Para 4407 recorre pre-pass, digest del árbol, los seis estados y cada fallo parcial.
Para 344d/668f desafía E6 contra su Intent; E3 ya no es su traza.
