# Lecciones LL-448..451 — recibo de escritura (sesión local_32a70c44, 2026-09-04 ~15:20)

Decisión de Guillermo (picker 14:55): escribir las CUATRO lecciones redactadas durante el vaciado del buzón.
Escritas con un solo append host-direct en `ObsidianVault/AI/20_Knowledge/lessons-learned.md` (LF, sin CRLF);
el borrador las numeraba 445..448 y el par (lotes G/H/I) ocupó 445..447 antes: renumeradas al escribir (LL-011).

| LL | Título | Ficha que cierra | Destino propuesto (no aplicado; las skills se editan en el Pack, LL-390) |
|---|---|---|---|
| LL-448 | Una lane «ciega» deja de serlo en cuanto el brief le cuenta que es la pasada N | fb-20260901-185627-8fac | `council-orchestration` + `delegar/references/patterns/review.md` |
| LL-449 | Sustituir lanes de un council pierde diversidad de PUERTA sin que nadie recuente | fb-20260901-185612-21bc | `council-orchestration` junto a C13 |
| LL-450 | Un gate «no-writes» que excluye terceros por un argumento en un COMENTARIO caduca en la primera fase nueva | fb-20260901-135420-5ca4 | `gates-ledger` §EXPECT |
| LL-451 | Cursor headless entrega A CIEGAS en este host: sus llamadas de shell mueren en los pre-hooks PowerShell | (ninguna; lotes M/W/V) | `delegar/references/routes/cursor-cli.md`; memoria host `cursor-headless-shell-blocked-by-hooks` |

Verificación:
- corpus antes `c7b2e95e4e76c5e031bee82f91d18919ecf03328b5ceba92907c0677339aa63e` (1 001 088 B), después
  `c375d531821e224bfa408d68c3ec6f8f279ce395fbe92db8113fd3a3e9894e0c` (1 009 081 B); copia previa `lessons-learned.md.bak_20260904_pre_ll448`.
- índice regenerado el último (`gen-lessons-index.ps1`): 451 lecciones, huérfanas 22 → 26 (las cuatro nuevas, ○);
  pin `> <!-- source-sha256: C375D531821E224BFA408D68C3EC6F8F279CE395FBE92DB8113FD3A3E9894E0C -->`; `-Check` → FRESH rc=0.
- citas comprobadas antes de escribir: handoffs 2026-09-01 (adr) y 2026-09-04 (buzón), C13 en council-orchestration,
  EXPECT en gates-ledger, cursor-cli.md y patterns/review.md en delegar, LL-406/408/421/432 en el corpus.
