# Lote formal G4 — build, storage, screenshot y evidencia

Aplica exactamente el contrato que precede a esta ficha en el mismo mensaje; no abras prompts desde disco.

| feedback_id | plan_path | plan_sha256 |
|---|---|---|
| `fb-20260829-111016-344d` | `plans/inbox-20260830/19-fb-20260829-111016-344d.md` | `b6fb11e963c04dc45d8580a4565a0de3ef1c57a12cc3d31f6ed2babde068990f` |
| `fb-20260829-115147-4407` | `plans/inbox-20260830/20-fb-20260829-115147-4407.md` | `ac9f3da267779d5d35e4e99f3ab86a1622bf0414bef1f22a549e682d3c0323ed` |
| `fb-20260830-011217-668f` | `plans/inbox-20260830/32-fb-20260830-011217-668f.md` | `c5c01f67a28460c0bf7da8b9bd2d78d11e76e6d0dcce0142b75fd7bb7e1574bb` |
| `fb-20260828-224835-268a` | `plans/inbox-20260830/03-fb-20260828-224835-268a.md` | `409506aa17631ffcd99e0a8c3c1cf422b9219c1a82bdf3d9964e7f2617e8214f` |
| `fb-20260829-024848-c7ca` | `plans/inbox-20260830/08-fb-20260829-024848-c7ca.md` | `87ed8cb4ed5d91c32f19dec6a130344a0a6b2427e610d3f7f784e73734df1392` |

Tensión obligatoria: source→staged→PBO desplegado requiere procedencia byte-level independiente;
hash/mtime distintos no son requisito de rebuild determinista. En storage, trata offline como run
real, pending como state machine CAS y cada rename/journal/recovery como fallo inyectable sin pérdida.
