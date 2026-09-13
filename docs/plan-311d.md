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
- `applied_echo`: copia cruda del `applied` del puente, antes de normalizar
- `clock_normalized`: bool (true cuando `applied` ≠ `applied_echo` en year/month/day/hour/minute)
- `warnings`: ausente, o lista de tokens:
  - `date_not_applied`
  - `multiplier_unconfirmed` (pedido, echo ausente; **no** implica `ok:0`)
  - `multiplier_mismatch` (echo numérico distinto)

`applied` se reescribe al reloj normalizado con `divmod` (`minute>=60`, incl. `120` / `120.0` → hour+2; `60` / `60.0` → hour+1; `hour` `24` / `24.0` → día siguiente). Hour queda en 0–23. `applied_echo` conserva el eco crudo (`8:60` recuperable). El pedido no se consulta al normalizar: un eco same-day `day=12 hour=23 minute=60` publica `applied.day=13 hour=0` y, si el pedido era 00:00 del día 12, `ok:0`. Un eco parcial `{day:31, hour:23, minute:60}` no inventa `day=32` ni se publica como `ok:1`. No se publica `hour=24` ni `24.0`.

`ok`:

- `0` si el echo `applied` trae year/month/day/hour/minute **y** no coinciden (tras normalizar), si el eco de calendario está incompleto, o si `multiplier_applied` es false.
- Sin reescribir `ok` si el echo de fecha falta (stubs de test / FakePeer).
- Sin cambio de `ok` solo por `multiplier_unconfirmed`. `World` no tiene `GetTimeMultiplier`; `MCPApplied` no echoa el multiplicador. N3 (echo `time_multiplier:3` vs pedido 4) es Python-closable; el round-trip live es dump-bound. No se empaqueta el PBO de noche.

## Fixtures

| ID | Bridge `applied` | Pedido | EXPECT | Exit |
|---|---|---|---|---|
| P1 | hour=9 minute=0, sin multiplier | 2026-09-12 9:00 | `date_applied true`, sin warnings de date | 0 |
| P2 | hour=8 minute=60 | 9:00 | `date_applied true`; `applied` hour=9 minute=0; `applied_echo` hour=8 minute=60; `clock_normalized true` | 0 |
| P2-midnight-prev | day=11 hour=23 minute=60 | 2026-09-12 00:00 | `date_applied true`; echo hour=0 minute=0 day=12; `ok:1`; no `hour=24` | 0 |
| P2-midnight-same | day=12 hour=23 minute=60 | 00:00 | echo hour=0 minute=0 **day=13**; `date_applied false`, `ok:0`, `date_not_applied`; no `hour=24` | 0 |
| P2-120 | hour=8 minute=120 / 120.0 | — | `applied` hour=10 minute=0 | 0 |
| N1 | hour=10 minute=0 | 9:00 | `date_applied false`, `ok:0`, warning `date_not_applied` | 0 |
| N2 | sin `time_multiplier` en applied; pedido multiplier=1 | — | `multiplier_applied null`, warning `multiplier_unconfirmed`, no se afirma ok por el multiplier | 0 |
| N3 | `time_multiplier: 3` vs pedido 4 | — | `multiplier_applied false`, `ok:0`, `multiplier_mismatch` (inyectado; productor live dump-bound) | 0 |
| F3 | `{day:31, hour:23, minute:60}` | 00:00 | no `day=32`; `ok:0`; `date_not_applied` | 0 |
| I1 | GetTimeMultiplier real in-game | — | INCONCLUSO (World no tiene getter) | — |

## Hecho cuando

P1–N3 verdes. I1 nombrado. No se toca el PBO.
