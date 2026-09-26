# Lote formal G3 — lifecycle, readiness y no-preemption

Aplica exactamente el contrato que precede a esta ficha en el mismo mensaje; no abras prompts desde disco.

| feedback_id | plan_path | plan_sha256 |
|---|---|---|
| `fb-20260829-023649-8f8c` | `plans/inbox-20260830/05-fb-20260829-023649-8f8c.md` | `c4e33c478a783e27ac72209f05805b7fb61b585a79d86c6e4a05d0f660fa19b6` |
| `fb-20260829-104608-4d66` | `plans/inbox-20260830/16-fb-20260829-104608-4d66.md` | `5f949c030f65888a85135c1bfd36c145c098f42f8dc06f209198c37f0c263601` |
| `fb-20260829-104625-7c88` | `plans/inbox-20260830/17-fb-20260829-104625-7c88.md` | `be40516d936feee072a3f40be9a60f97cf78e874e5c555875d7754401d47be73` |
| `fb-20260829-133459-a396` | `plans/inbox-20260830/21-fb-20260829-133459-a396.md` | `ff6c16b5f498a633a81abe409f672555cadea46c1815be16b13248d500e65079` |
| `fb-20260829-135408-cc2d` | `plans/inbox-20260830/22-fb-20260829-135408-cc2d.md` | `e969d75a05e0028224031fc9eac587d38bd95559e0f3eb8770249f691d4b2e16` |

Tensión obligatoria: actividad reciente, generación, edad, terminalidad e identidad no son
sinónimos. Comprueba que restart/writer-fault no promociona a fresh, que foreign/live nunca se
preempta y que readiness no suplanta autoridad lifecycle.
