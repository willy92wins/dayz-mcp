# Pleno DayZ-MCP, Ola A: evidencia (2026-09-15)

Puntero local sin versionar. No contiene claves ni texto del buzón.

## PRs integrados en main
- #59 T10 (`f298`, `75e7`): lanzamiento sin activar la ventana. Merge `3f4b976`.
- #60 T6 (`deb9`, P2 3-5): tests. Merge `29ff5ed`.
- #61 T4a (`1025`): lock de los módulos embebidos en el launcher. Merge `2ceb9fe`.
- #62 T1 (`63c9`, `855a`): pack desde el manifest de git con stage verificado. Merge `fb02f0e`.
- #63 T3 (`88ef`, `305a`): reintento del replace y cuarentena de copias incompletas. Merge `fbed209`.
- #64 T15 (`1f21`): preparación de Steam en el envelope. Merge `88bf698`.
- #65 T14, parte Python (`b0d9`, `f47b`): `restore_gameplay` honesto y aviso `render_frozen_signal`. Merge `8ae4159`.
- #66 T2 (`d85b`, `8d68`, parte de `7ef2`, `dff2`): contrato de renovación del lease y rastro de runs retirados dentro
  de una generación. Merge `e20a269`.

## Promoción 4 (2026-09-15 21:53, generación `ce6ee0d624e9483497470fd419d22500`)
- Sin daemon previo. Persistencia archivada a las 19:19 (`runtime-lifecycle-20260915-191928.tar.xz`, sha256 `39D68A63…`)
  y sin cambios hasta el arranque (`runs.json` de las 15:41).
- Arranque con el argv canónico sobre `e20a269`: `DAEMON LISTEN` en 6 s, `.err` sin traceback.
- Tras el arranque: `runs.json` reescrito vacío (24 B), 200 backups idénticos por nombre a los del archivo, 0 cuarentenas.
- Doctor `--daemon-policy normal --json`: `ok=true`; INFO `DAEMON_STATUS_OK` y `NATIVE_BUNDLE_EXTERNALS_OK`; WARN
  `KNOWLEDGE_PACK_MISSING` (esperado); sin `MANIFEST_BACKUP_RETENTION_STALLED` ni `MANIFEST_BACKUP_QUARANTINED`.
- `session_status`: caja vacía, sin fallos de auditoría ni de recuperación. `bridge_status`: `daemon_modules.stale=[]`.
- Lease de verificación tomado tras el arranque y liberado con `terminal_safe=true`.

## Detalle
- Vault: `AI/10_Projects/DayZ_MCP/reviews/2026-09-15-pleno-tickets/` (ledgers, reverify, reviews y backlogs por ticket).
