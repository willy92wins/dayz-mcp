# R9 de cierre del lote G — resultado VERIFICADO (2026-09-04)

Workflow `r9_r6.workflow.js` sobre `lote-G/ws/tools` (= ronda 9). Primera corrida: 7 ángulos +
pasada cruzada OK, **las 7 verificaciones caídas** por el filtro de Fable 5.1
(`reasoning_extraction`). Reanudada desde caché con `model:'opus'` en la verificación:
**18 afirmaciones de 7 ángulos; 14 confirmadas (cita e inferencia), 4 refutadas, 0 sin
verificar. Tasa de refutación 0,22.** Cruzado: 6. Fuente completa:
`tasks/wkzeaw2bj.output` (118 KB) y `subagents/workflows/wf_eece123e-743/journal.jsonl`.

## Confirmadas (cita + inferencia, verificador ciego, casadas por índice)

| # | sev | ángulo | qué | dónde |
|---|---|---|---|---|
| 1 | **P2 defecto** | race | El crédito de actividad se muestrea FUERA del lock del loopback y DESPUÉS de publicar (`time.time()` en `_note_run_command_activity`); `fence_runs`/`retire` no dejan tumba → un run `RUNNING_IDLE` publica `recent` por un comando descartado (`run_not_owned`). El verificador lo siguió eslabón a eslabón; nota: la ruta `exec_enforce` SÍ se protege (`fence_lost` corta antes del crédito): la genérica se lo saltó. | loopback 1712-1725, 1856-1859; process_lifecycle `_seal_activity_locked` |
| 2 | **P2 defecto** | race | `_retire_instance_locked` descarta la cola pero NUNCA ejecuta `_flush_queue_discards`: `discard_committed` (pins del coordinador, evento `session_rejected`) y `exec_audit(..., 'discarded')` no corren tras stop/reap/repair/launch fallido; `fence_runs` sí los ejecuta. El pin del lease vive hasta `operation_timeout`. | loopback 1054-1062 vs 1131-1142; session_coordination 164 |
| 3 | P2 mejora | perf | Cada enqueue acreditado invalida el caché de sondas aunque las sondas no dependen de la actividad (1 Hz por sesión en espera + `/session/status`). | process_lifecycle 1066-1081, 2877-2891 |
| 4 | P3 defecto | state-machine | `begin_release_owner` arma el fallo de recovery con el hash del registro NO persistido cuando `replace` falla: el run queda `RUNNING` con dueño muerto e irreparable por `repair_recovery_fault`. | 2320-2333, 2348-2359; daemon 495-510 |
| 5 | P3 defecto | data-loss | Rollback durable FALLIDO deja vivo el crédito de la ventana del intento: sin olvido ni tumba (el `return True` precede a la compensación). | 1341-1358 |
| 6 | P3 mejora | data-loss | El basal del proceso lanzado se descarta si el run ya tenía sello (extensión): la caja publica `stale` segundos después de lanzar un cliente. (cruzado #4 lo confirma) | 995-1004 |
| 7 | P3 defecto | persistence | `repair_manifest_recovery` libera dueños con `recover_after_restart()` sin cerco ni vaciado. **Ya en la ronda 10 (P6'').** | 2566-2569 |
| 8 | P3 mejora | persistence | = #3 por otra vía. | 1074-1080, 974-978 |
| 9 | P3 defecto | admin | El hint «stop it with dayz_test_stop(run_id=X)» se emite para un run propio en STARTING/STOPPING, que `dayz_test_stop` rechaza con `run_not_active`. | 291-299, 2047-2050, 2145-2159 |
| 10 | P3 defecto | admin | Un run UNRECONCILED con proceso vivo publica «retry with wait_for_box_s» aunque esperar no libera la caja. | 278-288, 336-344 |
| 11 | P3 mejora | admin | `occupancy_error_fields` etiqueta `foreign=True` una caja «ocupada sin filas» (sonda desconocida), indistinguible de un DayZDiag ajeno. | 371-373 |
| 12 | P3 defecto | security | Retirar un binding borra la valla de resultado de los comandos YA despachados: un `store_result` sin `inst` (o de otra instancia) se acepta como genuino. | loopback 1076-1078, 2389-2404, 2426-2431 |
| 13 | P3 mejora | security | `_enqueue_run_rejection` ABRE PASO (y acredita) cuando el estado durable no se puede leer (`manifest.get` lanza): **fail-open**. | loopback 1196-1203 |
| 14 | P3 mejora | security | Residuos por `run_id` (`_last_activity`, tumbas, `_fenced_runs`) no se limpian en la salida durable a EXITED: reutilizar un `run_id` heredaría actividad. | 2145-2148, 2629-2634 |

## Refutadas (4)
- repair sin cerco (state-machine, CITA_OK_INFERENCIA_NO) y su gemela de security — el verificador no sostuvo la inferencia tal como estaba escrita (la persistence sí la confirmó, #7): cuenta como confirmada por #7.
- perf: reescritura completa del audit con fsync por enqueue (inferencia no sostenida).
- perf: pre-prune de 10 slots (cita no cuadra).

## Cruzado (implementer-grade, 6)
1. P2 — = #2 (retire descarta sin cerrar operaciones ni auditar exec descartados).
2. **P2 — el hint público de `run_not_owned` ordena una acción con efectos**: `dayz_test_run mode=client run_id=...` retira el binding vivo y lanza un segundo cliente. Reescribir el hint (adopción sin efectos) o apuntar al camino que sólo adopta.
3. P3 — = #1 (crédito fuera del lock tras el cerco).
4. P3 — = #6 (extensión no refresca el basal).
5. P3 — = #7 (repair sin cerco).
6. P3 — `admin_reconcile` deja vivo un run no acreditado (STARTING→RUNNING_IDLE) que el siguiente arranque del daemon convierte en fault global (`daemon.py:114-131`).

## Qué va a la ronda de cierre (sobre el árbol fusionado, tras la r3)
Defectos: #1 (tumba/cerco también para el crédito en vuelo: el epoch se muestrea BAJO el lock al aceptar, o el cerco/retirada eleva tumba), #2 (retire hace flush como fence_runs), #4, #5, #9, #10, #12, #13 (fail-closed), cruzado #2 (hint) y #6 (reconcile no deja runs no acreditados). Mejoras que entran por baratas y coherentes: #6 (basal por extensión = max), #14 (limpiar residuos en EXITED). Fuera: #3/#8 (perf del caché de sondas: se mide antes de tocar), #11 (etiqueta foreign: decisión de contrato público, backlog).
