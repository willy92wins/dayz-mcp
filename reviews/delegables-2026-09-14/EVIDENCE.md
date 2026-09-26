# Evidencia de las delegables y la tercera promoción (2026-09-14)

Puntero local para el `evidence_ref` de `pipeline_resolve`. **No se versiona:** el repo es público y la evidencia vive en el vault privado.

- **Mergeado en `main`:** #51 `e179b6e` (6553), #52 `f98414e` (0e4c) y #53 `346e3ad` (160e).
- **Promovido al árbol vivo** el 2026-09-14: `main` `346e3ad`, daemon generación `00b35adf4b184d8880c2ec27c994e692`.
- **Suite completa sobre el árbol de `346e3ad`:** full suite: ran 3715 tests in 310.212s; reds now 0, baseline 0. Es la del regate de la ronda 4 de la 160e; `git diff --quiet 11e1ef8 346e3ad` confirma que `main` no se movió después.
- **Doctor tras la promoción:** `C:\Users\guill\AppData\Local\DayZ_MCP\daemon-logs\doctor-20260914-221630.json`.
- **Copias de manifest:** 8812 antes del reinicio y 8684 después.
- **Evidencia por ticket** (ledgers, reverify, reviews de Codex, cuerpos de PR): `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\reviews\2026-09-14-delegables\`, carpetas `6553`, `0e4c`, `160e` y `160e-operational` (archivo de slots y de copias).
- **Decisiones:** D-72 en `AI/10_Projects/DayZ_MCP/decisions/decision-log.md`.
- **5dbe y c261** (sin juego, tras el reinicio): lease, `server_reload`, heartbeat y status, y release con el mismo `lease_id` y dueño; el cambio de generación no desarmó al cliente sin recargar. Detalle en la nota de sesión `AI/30_Sessions/2026-09-14-dayzmcp-tercera-promocion.md` y en el runbook `AI/20_Runbooks/dayz-mcp-agent-session-protocol.md` (recuperación).
