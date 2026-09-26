# Lote D (6927) — registro del orquestador, ronda 2 (2026-09-04, 18:30-18:55)

Ronda 1: revisión de corrección (REVIEW-CODEX.md) + tres auditorías R9 en paralelo (AUDIT-RACE.md, AUDIT-ADMIN.md,
AUDIT-LOSS.md), Codex gpt-5.6-sol, sesiones frescas. El auditor RACE original murió por el filtro de ciberseguridad de OpenAI
tras 150 k tokens (`out-codex-RACE-flagged.log`); se relanzó con brief neutro (`BRIEF-AUDIT-RACE2.txt`) y entregó.

| Hallazgo | Severidad | Decisión | Dónde |
|---|---|---|---|
| REVIEW B-01 / RACE H-01 / LOSS M-1: ventana entre el sondeo de admisión y `launcher()`; el texto prometía «never» | CRITICAL (repro) | APLICADO: re-lectura de la tabla justo antes de `self.launcher(...)`; rechazo por `_settle_failed_launch(active_run_exists)` con audit `stage=pre_launch`; README y descripción dicen que la ventana restante es el bind de DayZ y que no hay reserva de puerto. Un holder que aparece DENTRO del bind sigue fuera del alcance (documentado). | process_lifecycle.py `pre_launch_reason`; server.py/README |
| ADMIN H-04: holder sin nombre (ToolHelp no lo lista) → caja libre y lanzamiento en otro puerto | MAJOR | APLICADO: `port_attribution_unknown` (rechazo y `port_scan_known:false` + `port_scan_reason`); reintento `_toolhelp_lookup` por PID antes de publicar `name None` | process_lifecycle.py `_foreign_port_reason`/`_collect_probes`; orphan_guard.py |
| LOSS M-3: fila netstat truncada → `known=True` vacío | MAJOR | APLICADO: fila `UDP` ilegible → volcado no confiable → `None` → `known=False` | orphan_guard.py `_udp_holders_from_netstat_output` |
| LOSS B-1: `laddr` tupla | BACKLOG | APLICADO (barato) | orphan_guard.py `_udp_holders_via_psutil` |
| RACE H-02: netstat 10 s bajo `_operation_lock` | BACKLOG | APLICADO: timeout 3 s, vencimiento = desconocido (cerrado) | orphan_guard.py `_udp_holders_via_netstat` |
| ADMIN H-02: `admin_reconcile --empty` ignora el testigo UDP | MAJOR | APLICADO: `_diag_snapshot_empty` exige tabla legible sin imagen DayZ ni holder sin nombre | process_lifecycle.py |
| ADMIN H-03 / REVIEW backlog-1 / LOSS M-2: `port_scan_unknown` oculto; hint «espera» cuando esperar no sirve; caja libre con rechazo por puerto | MAJOR | APLICADO: `_wait_hint`; `execute_wait_for_box` falla en el acto con `port_scan_unknown`; `_port_conflict_fields(box, port)` publica `reason`/`port`/hint «pass another port=»; `ports_in_use` lista holders de 2302-2999 sea cual sea la imagen | server.py; process_lifecycle.py `_DAYZ_PORT_RANGE` |
| LOSS M-1 (parte pre-bind): `DayZServer_x64.exe` invisible al `diag_probe` | MAJOR | APLICADO: el sondeo por nombre del daemon enumera también `DayZServer_x64.exe` | daemon.py |
| REVIEW backlog-2: fallbacks de `_box_payload` sin flags | BACKLOG | APLICADO (+6 líneas en loopback.py, avisado a par y Reserva) | loopback.py |
| ADMIN H-01: heredero huérfano del socket sin salida gestionada (antes invisible, ahora bloquea con visibilidad) | MAJOR | NO APLICADO: exige un verbo TTY de reconciliación por puerto (diseño nuevo). Backlog con propuesta del auditor; el operador ve `foreign` con imagen y puerto | ficha nueva pendiente |
| LOSS M-4 / cabd: lanzador ajeno que arranca DESPUÉS del run propio | MAJOR | FUERA DE ALCANCE del MCP: `session_status.box.foreign` lo lista mientras vive; la comprobación previa del lote ajeno es de LFHeli (Reserva) | resolución de 6927 |
| LOSS B-2: `port_probe=None` = feature ausente (fail-open por defecto en constructores futuros) | BACKLOG | NO APLICADO: compatibilidad transitoria; el único constructor productivo lo pasa | backlog |
| REVIEW backlog-3: test de wire de `port_scan_known` por sesión MCP | BACKLOG | NO APLICADO (sonda manual verde) | backlog |

Delta 2b (18:5x, dos minutos después de lanzar la ronda 2; `DELTA-R2b.txt`): `_DAYZ_PORT_RANGE` 2302-2320 → 2302-2999 (el
proyecto lanza en 2402/2502) y corrección de un test de wait (el ticket vuelve al llamante, no se llama `done`).

Verificación de la ronda 2: focal 56 OK (`focal-r2b.log`); suite completa 2628 con solo los 2 centinelas y los dos rojos
míos corregidos en 2b (`SUITE-D2.txt`); se repite la suite completa antes del commit. Mutantes de la ronda 1 (a-d) siguen
rojos por construcción (los tests no cambiaron); los de la ronda 2 los mide Codex (`REVIEW-CODEX-R2.md` V-3).

## Veredictos de la ronda 2 y deltas 2c/2d

| Sesión R2 | Veredicto | Hallazgo | Decisión |
|---|---|---|---|
| AUDIT-RACE-R2 | CRITICAL=0 · MAJOR=0 · BACKLOG=1 | H-R2-01: ventana residual dentro de `launcher`/bind, sin código intermedio (:2037-2063) | BACKLOG documentado (no hay reserva de puerto; mitigación futura = verificación post-launch de propiedad del puerto) |
| AUDIT-ADMIN-R2 | CRITICAL=0 · MAJOR=1 · BACKLOG=1 | R2-H-01: `session_status.blocked_on` manda a la FIFO con `port_scan_known=false` | APLICADO en 2c: rama nueva en `_session_status_blocked_on` que nombra `port_scan_reason` y la reparación; tests `BlockedOnTest` |
| AUDIT-LOSS-R2 | CRITICAL=0 · MAJOR=1 · BACKLOG=1 | R2-M-1: run gestionado + holder ajeno del puerto pedido → el run gana y receta esperar; R2-B-1: banda 2302-2999 ≠ dominio de la API (hasta 65530) | APLICADO en 2d: la caja publica `foreign_ports` (tabla menos PIDs registrados, cualquier imagen, solo enteros); `_port_conflict_fields` compone «caja ocupada Y puerto ajeno» y diagnostica cualquier puerto pedido |
| REVIEW-CODEX-R2 | BLOQUEANTES=1 | B-02: un puerto válido fuera de la banda 2302-2999 pierde `reason`/`port` (el mismo hueco que LOSS R2-B-1); BACKLOG-NUEVO-1: la rama pre-launch ignoraba el retorno de `_audit`; BACKLOG-3: test de wire de `port_scan_known` | B-02 APLICADO en 2d (`foreign_ports` para cualquier puerto); BACKLOG-NUEVO-1 APLICADO en 2e (`cleanup_degraded: audit_failed`); BACKLOG-3 sigue en backlog (sonda manual verde) |

Estado tras 2c/2d/2e (19:10): batería focal 62 OK, regresión 602 OK, suite completa 2642 con solo los 2 centinelas
(`SUITE-D3.txt`). Ronda 3 = `BRIEF-R3.txt` + `DELTA-R3.txt` + `DIFF-D3.patch`, una sesión Codex.

Orden de aplicación: 2c y 2d se aplican DESPUÉS de que salga REVIEW-R2 (esa sesión muta y restaura `server.py` por sha;
una escritura concurrente se pisaría). Ronda 3 = una sola sesión Codex de verificación (`BRIEF-R3.txt`, `DELTA-R3.txt`,
`DIFF-D3.patch`).
