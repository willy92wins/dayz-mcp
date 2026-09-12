# Plan R26 — `311d` `world_time_set` echo vs ok

Ficha `fb-20260911-230929-311d`. Lane W9 Python S.

---

## Qué arregla

Python compara `applied.hour/minute` campo a campo. El engine a veces echoa `hour=8, minute=60` para un pedido `9:00` → `date_applied:false` con `ok:1`. `MCPApplied` **no tiene** `time_multiplier` (`MCPMessages.c`) → `multiplier_applied` es siempre `null` aunque se pidió. Un `ok:1` se lee como “luz fija”.

## Fuera

- Enforce `DispatchWorldTimeSet` / `SetTimeMultiplier` / campo nuevo en `MCPApplied` (PBO).
- In-game, ocupación, JSONL, dump/#26–#29, `product-spec.md`.
- Inventar que el multiplicador se aplicó.

## Superficie

| Sitio | Cambio |
|---|---|
| `server.py` `world_time_set` | Normalizar reloj aplicado (`minute>=60` → carry a hour) **antes** de comparar y en el echo `applied`. `ok:0` si la fecha sigue sin coincidir o el multiplicador echoa otro valor. `warnings` si el multiplicador se pidió y el echo no lo trae. |
| Descripción del tool | Nombrar `warnings` y que `multiplier_applied=null` no es confirmación. |
| `test_server_response_truth.py` | Fixtures abajo. No edita `control_client.py`. |

## Schema [EXACT]

Campos extra (además de los actuales):

- `date_applied`: bool (tras normalizar reloj; year/month/day + hour/minute equivalentes)
- `multiplier_applied`: `true` \| `false` \| `null` (null = no hubo echo numérico)
- `warnings`: ausente, o lista de tokens:
  - `date_not_applied`
  - `multiplier_unconfirmed` (pedido, echo ausente; **no** implica `ok:0`)
  - `multiplier_mismatch` (echo numérico distinto)

`applied.hour`/`applied.minute` en la respuesta MCP se reescriben al reloj normalizado cuando `minute` era int ≥60. year/month/day no se inventan (no hay carry de día).

`ok`:

- `0` si el echo `applied` trae year/month/day/hour/minute **y** no coinciden (tras normalizar), o si `multiplier_applied` es false.
- Sin reescribir `ok` si el echo de fecha falta (stubs de test / FakePeer).
- Sin cambio de `ok` solo por `multiplier_unconfirmed` (el PBO actual nunca echoa el multiplicador).

## Fixtures

| ID | Bridge `applied` | Pedido | EXPECT | Exit |
|---|---|---|---|---|
| P1 | hour=9 minute=0, sin multiplier | 2026-09-12 9:00 | `date_applied true`, sin warnings de date | 0 |
| P2 | hour=8 minute=60 | 9:00 | `date_applied true`; echo hour=9 minute=0 | 0 |
| N1 | hour=10 minute=0 | 9:00 | `date_applied false`, `ok:0`, warning `date_not_applied` | 0 |
| N2 | sin `time_multiplier` en applied; pedido multiplier=1 | — | `multiplier_applied null`, warning `multiplier_unconfirmed`, no se afirma ok por el multiplier | 0 |
| N3 | `time_multiplier: 3` vs pedido 4 | — | `multiplier_applied false`, `ok:0`, `multiplier_mismatch` | 0 |
| I1 | GetTimeMultiplier real in-game | — | INCONCLUSO | — |

## Hecho cuando

P1–N3 verdes. I1 nombrado. No se toca el PBO.
