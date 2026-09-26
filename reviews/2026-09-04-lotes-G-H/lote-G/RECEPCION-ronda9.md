# Recepción de la ronda 9 — 2026-09-04 ~06:50

Medido por el orquestador; nada se toma del STATE del implementador (Cursor Grok 4.6 High,
identidad verificada en el `init`; 0 hooks bloqueados con el HOME de sandbox; 1.121 s).

- Sello `runs9/GATE-SEAL.txt` **4/4**; write-set **0 fuera del allowlist**.
- `process_lifecycle.py` SHA-256 `f0b876634851dd50e765d90c05b87cbb3cd1b0288ea18fd571bbad31e3605d3b`
  (+104/−35 vs r8; CRLF entero: normalizar al integrar); `loopback.py`
  `f5db0ec67d4b7cda14207e25fa8add9d5c443a3b58e9cdf5c321d4f6b59d714d` (+97/−29); tests +299/−9.
- Gates corridos por el orquestador: `ORACULO: PASS=35 FAIL=0 UNMET=0 de 35` · `SUITE-ACOTADA OK`
  (76+109+8+65+45+46 = 349 tests).
- Rojo-antes contra `ws-frozen-r8` demostrado por el worker con shell: 6 FAIL + 5 preservación.
- **Las cinco sondas de los revisores sobre este árbol**: Opus H1 (rama asíncrona del release)
  NO reproduce (control de la rama síncrona OK); Opus H2 (`repair_manifest_recovery`) NO
  reproduce; P7 aguanta (crédito y basal); P8 aguanta; Codex-H1 (`release` → `world_spawn`)
  NO reproduce: 409 `run_not_owned`. La sonda de vías de despacho de Opus da `ROTO en V4`
  porque V4 afirma el contrato VIEJO («lectura sobre idle → 200 por contrato»): en la 9 es 409,
  que es P6 estricto. No es defecto.

## Nota de instrumento

El worker lo dijo y es cierto: **N25 se pone verde por la defensa del loopback sola** (el poll
fail-closed sobre `RUNNING_IDLE`), porque `build()` no cablea `lifecycle.bindings` y la
retirada/quiesce del lifecycle es invisible para ese check. Medido: el oráculo de G da 35/35
también sobre el workspace del lote H con `loopback.py` de la 9 y `process_lifecycle.py` de la
r8. El quiesce del lifecycle lo acreditan los tests del worker (rojo-antes) y las sondas de
Opus (que sí cablean bindings) — no mi oráculo. Consta.

## Siguiente

Codex ciego sobre la 9 (`review9/`), R9 `r9_r6.workflow.js` (P1-P8 + P6 estricto + P7')
sobre `ws/tools`, suite completa en el repo, integración por rutas exactas con LF.
