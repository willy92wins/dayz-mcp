# EVIDENCIA - resolve YA_RESUELTO 296b + 678b (2026-09-25)

## Preconditions
- G-CAL durable GREEN: `gates/triage-20260925-gcal/GATES.md` (JobDir mcp-gcal-remeasure-20260925; descensos=0)
- Sol decide: `mcp-triaje-sol-decide-20260925/decide.jsonl` both `X/X-YA_RESUELTO`
- Checkout tip: `78cc50a` (includes #91; #90 ancestor `90ac6f5`)

## Resolutions
| id | PR | merge commit |
|---|---|---|
| fb-20260924-012921-296b | #91 | 78cc50a74d44700df85bfde32e5b36f5f1dbe220 |
| fb-20260924-011620-678b | #90 | 90ac6f555e33b2b79e6643ca4c7658c43c13dad7 |

## Method
`tools/dayz_mcp/inbox.append_resolution` with `evidence_ref=reviews/triage-20260925-resolve-ya/EVIDENCIA.md` (this file). No product code change this hop.
