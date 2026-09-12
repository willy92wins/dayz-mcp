# Plan R26 — `2c43` límites de `pipeline_feedback`

Ficha `fb-20260910-032514-2c43`. Lane W9 Python S. Worktree `origin/main` `edc7bb3`.

Lane única: el `tools/list` de `edc7bb3` ya se midió — `title`/`body`/`project` son `string` sin `minLength`/`maxLength`; `pipeline_resolve.evidence_ref` ya publica `maxLength: 240` vía `Field`.

---

## Qué arregla

`inbox.append_feedback` rechaza title 1..120, body 1..8000, project 0..64 (`inbox.py`). El `inputSchema` de `pipeline_feedback` no lo dice. Un cliente local planifica contra schema vacío y solo descubre el tope al `bad_args`.

## Fuera (R25 / R20)

- JSONL `feedback.jsonl` (no append, no resolve).
- Enforce, PBO, ocupación, `control_client.py`, `daemon_credential.py`, `product-spec.md`, dump/#26–#29.
- In-game. PARO.

## Superficie

| Sitio | Cambio |
|---|---|
| `tools/dayz_mcp/inbox.py` | Constantes nombradas; `_check_length` las usa. |
| `tools/dayz_mcp/server.py` | `Annotated[..., Field(min_length, max_length)]` en title/body/project. Validación servidor intacta. `evidence_ref` Field usa la misma constante. |
| `tools/tests/test_pipeline_feedback.py` | Schema pos/neg. |

## Schema [EXACT]

`tools/list` → `pipeline_feedback.inputSchema.properties`:

- `title`: `{type:string, minLength:1, maxLength:120}`
- `body`: `{type:string, minLength:1, maxLength:8000}`
- `project`: `{type:string, maxLength:64, default:""}` (vacío legal; no `minLength`)

Números leídos de las constantes, no del texto de la ficha al assert.

## Fixtures

| ID | Entrada | EXPECT | Exit |
|---|---|---|---|
| P1 | `list_tools` | los tres campos con los max/min de arriba | 0 |
| P2 | `append_feedback` title 120 / body 8000 / project 64 | inserta | 0 |
| N1 | `append_feedback` title 121 | `bad_args: title 121 > 120 chars`; no inserta | ToolError/ValueError |
| N2 | schema **sin** `maxLength` en title | el test de P1 **rojo** | 1 |
| I1 | interpreter `.venv-mcp` ausente | INCONCLUSO | — |

`call_tool` con 121 puede morir en Pydantic (schema) **antes** de inbox. Eso es el contrato publicado. El mensaje con recuento queda en `append_feedback` (mismo que hoy).

## Hecho cuando

P1+P2+N1 verdes con `.venv-mcp`. N2 es el control negativo del propio test. Sin in-game.
