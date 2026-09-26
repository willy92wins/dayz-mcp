<!-- Salida CRUDA de la lane W4d (familia de tests de lease), sin editar. Verificación de pares del receptor: pairs: 16 {'EXACT/EXACT': 14, 'NOT_FOUND/EXACT': 1, 'EXACT/FAR': 1}. Veredicto: ../INFORME.md §4.9 -->



## Resumen
- La familia cubre conducta muy amplia de lease/FIFO, autoridad, auditoría durable, handoff, HTTP y lifecycle acoplado.
- Hay duplicados reales pero muchos son CASI por casos límite: gracia exacta, auditoría en prepared vs commit, colisión de identidad, release audit bloqueada, tombstone por HTTP vs coordinator.
- Los helpers están dispersos: relojes, ids secuenciales, audit sinks, cleanup sinks, WAL fakes, `_coord`, `_identity`, helpers HTTP y fixtures de lifecycle.
- Hay import cross-test visible: por ejemplo `test_bug046_lease_queue_liveness.py` importa desde `test_session_coordination.py` y `test_task7_rereview_regressions.py` importa desde `test_task7_review_regressions.py`.
- HECHO verificado por el receptor: no existe `tests/helpers_*.py`, y en esta familia hay imports entre ficheros de test.
- [INFERENCIA] Conviene separar dominio: lease/FIFO, autoridad/fence, auditoría/fallos, loopback/dispatch, handoff/reload, HTTP y soporte de lifecycle/procesos.
- La fusión debe conservar casos límite de gracia, auditoría, WAL, HTTP y reload porque varios nombres por bug/task protegen regresiones específicas.
- GATE NO CORRIDO: revisión por API sin herramientas

## A. Inventario
| Fichero | Clase | Conducta protegida | Rango |
|---|---|---|---|
| tools/tests/test_session_coordination.py | IdentityAndClassificationTest | Constantes, clasificación de comandos, identidad válida/inmutable/redactada | 49-117 |
| tools/tests/test_session_coordination.py | SessionCoordinatorTest | TTL/FIFO, release/expiry, pin, vehicle cleanup, auditoría degradada, fence FIFO | 177-1069 |
| tools/tests/test_session_coordination.py | BoxNoneReleaseTest | Cola FIFO de box/tickets con release None y claim exacto | 1072-1142 |
| tools/tests/test_bug046_lease_queue_liveness.py | Bug046DpfContractTests | Contrato estructural de product-spec H4/H9/H8 | 93-163 |
| tools/tests/test_bug046_lease_queue_liveness.py | Bug046H4GraceRuntimeTests | Gracia H4 runtime ligada a constantes, no solo a markdown | 177-213 |
| tools/tests/test_bug046_lease_queue_liveness.py | Bug046QueueLivenessRedTests | Liveness FIFO: release/expiry, live wait, audit prepared/commit, release terminal | 216-746 |
| tools/tests/test_bug046_lease_queue_liveness.py | Bug046GrantAuthorityFenceTests | Autoridad provisional vs WAL/admin/cancel/queue drift/duplicate wait | 799-1227 |
| tools/tests/test_bug046_audit_fault_recovery.py | CoordinationFaultStoreTests | Store CAS de markers de fault, transiciones, idempotencia, temp/serialización | 62-548 |
| tools/tests/test_bug046_audit_fault_recovery.py | CoordinationFaultStartupRecoveryTests | Recuperación startup de markers, snapshot faults, desaparición/quarantine cutpoints | 550-885 |
| tools/tests/test_bug046_audit_fault_recovery.py | DurableStartupRecoveryIntegrationTests | Integración daemon/restart: faults durables, quarantine, repair CLI paths | 888-1214 |
| tools/tests/test_bug046_audit_fault_recovery.py | AuditWriteOnceTests | Auditoría JSONL write-once por event id y scan de backups | 1217-1272 |
| tools/tests/test_bug046_audit_fault_recovery.py | AuditFaultSnapshotTests | Fault público en snapshot y bloqueo de nueva autoridad | 1275-1311 |
| tools/tests/test_bug046_audit_fault_recovery.py | CoordinatorWalIntegrationTests | Protocolo WAL/snapshot de coordinator en grant/release/live wait | 1314-1729 |
| tools/tests/test_bug046_audit_fault_recovery.py | AuditRepairCliTests | CLI de reparación exige fault id y confirmación exacta | 1732-1783 |
| tools/tests/test_bug046_authority_fence.py | GrantAuthorityFenceTests | Inmediatez/FIFO no exponen autoridad bajo WAL blocking y clear pendiente | 70-223 |
| tools/tests/test_authority_invariants_are_gated.py | AuthorityInvariantsAreGatedTest | `claim_dispatch`/`commit_authorization` solo aceptan comando y lease comprometidos | 43-141 |
| tools/tests/test_task7_review_regressions.py | ProcessGuardGateContractTest | Contrato de evidencia del gate de proceso y ausencia de kill genérico | 46-144 |
| tools/tests/test_task7_review_regressions.py | ExactLeaseBarrierTest | Lease exacto L1/L2: stale action no lanza/adopta/para/entrega mutación | 383-512 |
| tools/tests/test_task7_review_regressions.py | DispatchAuthorityAndQuarantineTest | Dispatch y cuarentena retail: poll, read/mutation, release vs claim | 515-681 |
| tools/tests/test_task7_review_regressions.py | LifecyclePostAuditQuarantineTest | Retail durante auditoría bloquea manifest/guard/start/stop/adopt/admin | 683-751 |
| tools/tests/test_task7_review_regressions.py | RunStateRecoveryTest | Run manifests: unknown/exited, handle exit, stop parcial, reconcile exacto | 754-929 |
| tools/tests/test_task7_review_regressions.py | RestartAndManifestTest | Activación/restart libera owner previo, preserva manifiesto si auditoría falla | 932-1043 |
| tools/tests/test_task7_review_regressions.py | ReleaseAuditTest | Release finished deduplica degradación y reporta fallo de auditoría | 1046-1077 |
| tools/tests/test_task7_review_regressions.py | RealLifecycleHttpQuarantineTest | HTTP lifecycle con cuarentena bloquea mutaciones pero no status/admin/release | 1098-1260 |
| tools/tests/test_task7_review_regressions.py | DaemonReleaseWiringTest | Owner/admin/expiry llevan a RUNNING_IDLE sin guard, limpio o cuarentena | 1263-1341 |
| tools/tests/test_task7_review_regressions.py | DayzMcpWorkerWaitTest | Wait de workers dayz-mcp falla explícito si workers sobreviven | 1386-1417 |
| tools/tests/test_task7_rereview_regressions.py | AuthorityIoBoundaryTest | Auditorías/audits bloqueados no bloquean polls/reads; stale admin/heartbeat/FIFO | 52-351 |
| tools/tests/test_task7_rereview_regressions.py | CleanupBudgetAndFencingTest | Presupuesto de cleanup, fencing de lease y encolado tardío | 353-530 |
| tools/tests/test_task7_rereview_regressions.py | DispatchAuditDegradationTest | Rechazo de dispatch adjunta fallo de auditoría al resultado | 532-560 |
| tools/tests/test_task7_rereview_regressions.py | FailedLaunchSettlementTest | Settlement de lanzamiento fallido/restauración/jitter/guard error | 563-791 |
| tools/tests/test_task7_rereview_regressions.py | SettlementFlakeProbeExitTest | Harness de medición va a rojo si setup/import falla | 793-826 |
| tools/tests/test_task7_rereview_regressions.py | LifecycleRecoveryAndOutcomeTest | Stop/recover/manifest bytes/auditoría terminal redactada | 829-982 |
| tools/tests/test_task7_rereview_regressions.py | GuardFailureNormalizationTest | Errores de guard nativo normalizados fail-closed | 985-999 |
| tools/tests/test_task7_rereview_regressions.py | GateEvidenceContractTest | Validador de evidencia gate: PIDs, scheme, children, contract drift | 1003-1180 |
| tools/tests/test_task7_rereview_regressions.py | ToolHelpCloseFailureTest | Toolhelp/close handle fallido produce unknown fail-closed | 1183-1214 |
| tools/tests/test_task7_final_authority_regressions.py | ReleasingBudgetTest | Release audit/cleanup timeout no pinna FIFO ni siguiente grant | 32-319 |
| tools/tests/test_task7_final_authority_regressions.py | CleanupWorkerCapacityTest | Workers de cleanup limitados y saturación avanza FIFO | 320-381 |
| tools/tests/test_task7_final_authority_regressions.py | GrantLedgerLinearizationTest | Grant FIFO/initial: auditoría, revisiones, colisión, cancel, queue FIFO, TTL audit | 384-1453 |
| tools/tests/test_task7_final_authority_regressions.py | StateLockIoBoundaryTest | Lock de state no bloquea auditorías/polls/probes fuera de I/O | 1456-1651 |
| tools/tests/test_task7_final_authority_regressions.py | ExecEnforceCapacityReservationTest | `exec_enforce` cancel/publish/último slot sin audit fantasma | 1654-1755 |
| tools/tests/test_task7_final_authority_regressions.py | OwnerReadRevalidationTest | Read stale del owner se publica sin owner tras L2 y no renueva L2 | 1758-1847 |
| tools/tests/test_0ab2_grace.py | GraceS1Tests | Gracia S1, reacquire preferente, extraños en cola, probe attached, status grace | 53-198 |
| tools/tests/test_0ab2_grace.py | TakeoverBTests | Ownerless idle/targets takeover y exclusión de stop sugerido | 201-232 |
| tools/tests/test_0ab2_grace.py | DayzTestRunTakeoverTests | Herramienta run: takeover_required, stop previo, publicar esquema takeover | 234-370 |
| tools/tests/test_0ab2_r9.py | StateMachineR9Tests | Gracia nula/viva, arms, consumo, release voluntaria, tercero, expira exacto | 111-173 |
| tools/tests/test_0ab2_r9.py | RaceR9Tests | Race probe/cleanup, concurrentes former/stranger, wait claim FIFO | 176-320 |
| tools/tests/test_0ab2_r9.py | IdentityR9Tests | Identidad completa en gracia, colisión identity, takeover owner prefix | 322-397 |
| tools/tests/test_0ab2_r9.py | DataLossR9Tests | Snapshot no persiste grace/pref_used; restart/replacement; cleanup lifecycle | 400-522 |
| tools/tests/test_0ab2_r9.py | DayzTestRunIdentityR9Tests | Takeover default false y owned running no stop; identity takeover | 524-642 |
| tools/tests/test_session_acquire_wait.py | OperationQueueCoordinatorTests | Enqueue always ticket, cancel/tombstones, box claimer, lease holder, cancel exacto | 20-267 |
| tools/tests/test_session_acquire_wait.py | ClientAcquireWaitTests | Cliente acquire_wait: progreso, cancel, ToolError, schema público, timeouts | 270-613 |
| tools/tests/test_session_handoff.py | HandoffRoundTripTest | Carrier round-trip, consume replayable=False, malformed, clear | 41-94 |
| tools/tests/test_session_handoff.py | HandoffRefusalTest | Refusiones de carrier inválido/devuelven None, nunca raise | 97-165 |
| tools/tests/test_session_handoff.py | HandoffWriterContractTest | Escritor carrier rechaza errores, reemplaza, token no envuelve | 168-215 |
| tools/tests/test_session_handoff.py | HandoffAgainstTheRealCoordinatorTest | Carrier válido funciona contra coordinator; fresh no usa lease; overrun no recupera | 218-285 |
| tools/tests/test_reload_lease_recovery.py | ReloadLeaseRecoveryTests | Reload/client: carrier, lease heredado, reconcile, foreign owner, transporte | 16-199 |
| tools/tests/test_session_http.py | SessionHttpTest | Contrato HTTP: enqueue identity/schema, lease, cancel, release, status, box_wait | 102-716 |
| tools/tests/test_session_http.py | ProductionCoordinationStorePersistSkipTest | Store de producción escribe revision y salta writes sin revision change | 719-788 |

## B. Duplicados
[INFERENCIA] Basada solo en material visible; los límites a conservar están indicados.

D01 | DUPLICADO | tools/tests/test_0ab2_grace.py:157 | tools/tests/test_bug046_lease_queue_liveness.py:195 | Tras TTL+gracia un extraño puede adquirir el lease | EVID-A: clock.advance(AFTER) | EVID-B: clock.advance(_H4_SPEC_TTL_S + _H4_SPEC_GRACE_S + 0.001) | CONSERVAR: nada
D02 | CASI | tools/tests/test_0ab2_r9.py:163 | tools/tests/test_0ab2_grace.py:157 | Expiración de gracia: justo en TTL+G vs algo después | EVID-A: clock.advance(TTL + G) | EVID-B: clock.advance(AFTER) | CONSERVAR: 0ab2_r9 cubre el límite exacto TTL+G
D03 | CASI | tools/tests/test_session_coordination.py:426 | tools/tests/test_0ab2_grace.py:147 | Autorizar a TTL/caducidad devuelve lease_expired | EVID-A: decision = coordinator.authorize(self.a, token, "world_spawn") | EVID-B: decision = coordinator.authorize(self.a, token, "world_spawn") | CONSERVAR: 0ab2_grace añade heartbeat !=200 a TTL; base cubre 119/120/121
D04 | CASI | tools/tests/test_task7_rereview_regressions.py:300 | tools/tests/test_task7_final_authority_regressions.py:1282 | Grant inicial concurrente deja un solo lease/auditoría permitido | EVID-A: self.assertEqual(second[0][0], 202) | EVID-B: self.assertEqual(len(granted), 1, granted) | CONSERVAR: rereview deja 202 en cola; final valida un evento permitido y coherencia con active
D05 | CASI | tools/tests/test_task7_rereview_regressions.py:347 | tools/tests/test_task7_final_authority_regressions.py:1324 | Grant FIFO concurrente tiene un solo evento permitido/coherente | EVID-A: self.assertEqual((claimed[0], claimed[0][1]["status"]), (200, "active")) | EVID-B: self.assertEqual(len(fifo_grants), 1, fifo_grants) | CONSERVAR: rereview deja cola vacía; final deja un 202 y valida granted events
D06 | CASI | tools/tests/test_bug046_lease_queue_liveness.py:695 | tools/tests/test_task7_final_authority_regressions.py:1421 | Auditoría de release bloqueada mantiene FIFO y no otorga antes del terminal | EVID-A: self.assertEqual((during[0], during[1].get("status")), (202, "queued")) | EVID-B: self.assertLess(finished, prepared) | CONSERVAR: bug046 valida fence de release; final valida orden finished/prepared/fifo
D07 | CASI | tools/tests/test_bug046_lease_queue_liveness.py:439 | tools/tests/test_task7_final_authority_regressions.py:528 | Fallo de auditoría en grant deja 503 y no publica autoridad | EVID-A: self.assertEqual((result[0], result[1].get("error")), (503, "audit_failed")) | EVID-B: (owner_result[0][0], owner_result[0][1]["error"]), | CONSERVAR: bug046 cubre fallo en session_grant_prepared; final cubre session_granted/revocación
D08 | CASI | tools/tests/test_session_coordination.py:1018 | tools/tests/test_bug046_lease_queue_liveness.py:520 | Grant fallido deja ticket en cola sin owner y 503 audit_failed | EVID-A: self.assertEqual((status_code, body["error"]), (503, "audit_failed")) | EVID-B: self.assertEqual((result[0], result[1].get("error")), (503, "audit_failed")) | CONSERVAR: base valida posición y cleanup_degraded vacío; bug046 valida granting vacío y cola exacta
D09 | CASI | tools/tests/test_task7_final_authority_regressions.py:64 | tools/tests/test_task7_final_authority_regressions.py:227 | Release audit bloqueado mantiene FIFO y no deja active/granting | EVID-A: self.assertEqual((progress[0], progress[1]["status"]), (202, "queued")) | EVID-B: self.assertEqual((progress[0], progress[1]["status"]), (202, "queued")) | CONSERVAR: 193 cubre status sin degraded; 33 cubre no releasing/granting
D10 | CASI | tools/tests/test_0ab2_r9.py:367 | tools/tests/test_session_coordination.py:469 | Colisión de identity con mismo session_id da identity_mismatch | EVID-A: self.assertEqual(payload["error"], "identity_mismatch") | EVID-B: self.assertEqual((collision[0], collision[1]["error"]), (403, "identity_mismatch")) | CONSERVAR: 0ab2_r9 usa spoof a mitad de gracia; base usa colisión con identity y cola
D11 | CASI | tools/tests/test_session_acquire_wait.py:48 | tools/tests/test_session_http.py:411 | Cancelación antes de enqueue tardío instala tombstone y bloquea late request | EVID-A: self.assertEqual((late[0], late[1]["error"]), (409, "operation_cancelled")) | EVID-B: self.assertEqual((status, body), (409, {"error": "operation_cancelled"})) | CONSERVAR: HTTP cubre ruta, status y cuenta de tombstones visibles
D12 | CASI | tools/tests/test_session_handoff.py:259 | tools/tests/test_reload_lease_recovery.py:128 | Token portado por carrier sigue autorizando lease heredado | EVID-A: self.assertTrue(decision.allowed) | EVID-B: self.coordinator.authorize(self.identity, runtime.active_lease_token, "world_spawn").allowed | CONSERVAR: reload cubra ClientRuntime/env carrier/reconcile
D13 | CASI | tools/tests/test_session_coordination.py:990 | tools/tests/test_bug046_lease_queue_liveness.py:736 | Release con auditoría terminal fallida degrada y mantiene coherencia FIFO | EVID-A: self.assertIn("audit_failed", body["cleanup_degraded"]) | EVID-B: self.assertEqual((blocked[0], blocked[1].get("error")), (503, "audit_failed")) | CONSERVAR: bug046 añade handoff_pending y bloqueo de live wait
D14 | CASI | tools/tests/test_0ab2_r9.py:155 | tools/tests/test_0ab2_grace.py:169 | Extraños se encolan durante gracia | EVID-A: self.assertEqual(coordinator.acquire(self.b, "drive")[0], 202) | EVID-B: self.assertEqual(status, 202) | CONSERVAR: 0ab2_r9 valida b y c; 0ab2_grace 163 cubre a/b
D15 | CASI | tools/tests/test_0ab2_grace.py:92 | tools/tests/test_0ab2_grace.py:166 | Ventana de gracia con former/back vs shape de grace | EVID-A: clock.advance(119.0) | EVID-B: clock.advance(MID) | CONSERVAR: 92 cubre enqueue/wait former; 162 cubre grace status
D16 | CASI | tools/tests/test_0ab2_r9.py:202 | tools/tests/test_0ab2_grace.py:226 | Lease activo no es takeover target si owner coincide | EVID-A: self.assertFalse(box.probe_saw_released) | EVID-B: self.assertIsNone(takeover_target_run_id(box, caller_session="me")) | CONSERVAR: 0ab2_r9 cubre coordinator/box cleanup; 201 cubre takeover helper

## C. Helpers duplicados
| Copies | Dónde | Helper común propuesto |
|---|---|---|
| Identity builder `_identity` | tools/tests/test_session_coordination.py:164; tools/tests/test_session_handoff.py:30; inline tools/tests/test_task7_review_regressions.py:38 | `def make_identity(name_or_pid: str | int, *, session_id: str | None = None, platform: str = "codex", task_label: str | None = None) -> ClientIdentity` |
| Reloj controlable | tools/tests/test_session_coordination.py:120 (`FakeClock`); tools/tests/test_task7_review_regressions.py:157 (`Clock`) | `class FakeClock: __call__(self) -> float; advance(self, seconds: float) -> None` |
| IDs secuenciales | tools/tests/test_session_coordination.py:131 (`SequentialIds`); tools/tests/test_task7_review_regressions.py:147 (`Sequence`) | `class SequentialStrings: def __init__(self, prefix: str) -> None; def __call__(self) -> str` |
| Audit sink | tools/tests/test_session_coordination.py:142 (`AuditSink`); tools/tests/test_task7_review_regressions.py:168 (`Audit`) | `class AuditRecorder: fail_events: set[str]; on_event: Callable[[dict[str, object]], None] | None; def __call__(self, event: dict[str, object]) -> bool` |
| Cleanup sink | tools/tests/test_session_coordination.py:153 (`CleanupSink`); lambdas inline en lease/task7 | `class CleanupRecorder: def __call__(self, session_id: str, lease_id: str, reason: str, vehicle_active: bool) -> dict[str, object]` |
| Coordinator de gracia | tools/tests/test_0ab2_grace.py:42 (`_coord`); tools/tests/test_0ab2_r9.py:61 (`_coord`); tools/tests/test_bug046_lease_queue_liveness.py:166 (`_h4_grace_coordinator`) | `def grace_coordinator(clock: FakeClock, *, attached_run_probe: Callable[[str, str], bool] | bool = True, cleanup: Callable[..., dict[str, object]] | None = None, **overrides) -> SessionCoordinator` |
| Coordinator helper | tools/tests/test_session_coordination.py:195 (`SessionCoordinatorTest._fresh`); tools/tests/test_bug046_authority_fence.py:71; tools/tests/test_bug046_lease_queue_liveness.py:808; tools/tests/test_task7_review_regressions.py:516 | `def build_coordinator(*, token_fn: object | None = None, id_fn: object | None = None, audit: object | None = None, cleanup: object | None = None, **overrides) -> SessionCoordinator` |
| WAL blocking | tools/tests/test_bug046_authority_fence.py:11 (`_BlockingWal`); tools/tests/test_bug046_lease_queue_liveness.py:749 (`_BlockingWalBoundary`) | `class BlockingWal: def __init__(self, boundary: str | None = None, *, clear_succeeds: bool = True, fail_clear_after: int | None = None) -> None; arm(...); transition(...); persist(...); clear(...)` |
| Snapshot sink | tools/tests/test_session_http.py:65 (`SnapshotStore`); tools/tests/test_bug046_audit_fault_recovery.py:1323 (`persist` callback) | `class SnapshotRecorder: def write_coordination(self, payload: dict[str, object]) -> bool; payloads: list[dict[str, object]]; raise_on_write: bool; lock_probe: SessionCoordinator | None` |
| HTTP POST helper | tools/tests/test_session_http.py:44 (`_http`); tools/tests/test_task7_review_regressions.py:1080 (`http_post`) | `def http_json(method: str, base: str, path: str, payload: dict[str, object] | None = None, *, key: str | None = None, query: dict[str, object] | None = None) -> tuple[int, dict[str, object]]` |
| Lifecycle fixture cross-file | tools/tests/test_task7_review_regressions.py:249 (`LifecycleFixture`); tools/tests/test_task7_rereview_regressions.py:35 (import) | `class LifecycleFixture(...) -> LifecycleFixture` en helper de dominio lifecycle, sin import desde tests |

## D. Estructura objetivo
Regla general: si una fuente no aparece en excepción, todas sus pruebas se mueven intactas al destino indicado.

| Fuente | Destino | Acción |
|---|---|---|
| test_session_coordination.py: IdentityAndClassificationTest, BoxNoneReleaseTest | tools/tests/test_lease_queue_fifo.py | mover intactas |
| test_session_coordination.py: SessionCoordinatorTest | tools/tests/test_lease_queue_fifo.py | mover TTL/FIFO/ticket/pin/cleanup/vehicle/identity |
| test_session_coordination.py: SessionCoordinatorTest | tools/tests/test_coordination_audit_faults.py | mover auditoría degradada, expiry audit, release audit, grant audit fail |
| test_bug046_lease_queue_liveness.py: Bug046DpfContractTests, Bug046H4GraceRuntimeTests, Bug046QueueLivenessRedTests | tools/tests/test_lease_queue_fifo.py | mover; D01-D16 conservan límites donde toca |
| test_bug046_lease_queue_liveness.py: Bug046GrantAuthorityFenceTests | tools/tests/test_coordination_authority_fence.py | mover |
| test_bug046_audit_fault_recovery.py: todas | tools/tests/test_coordination_audit_faults.py | mover intactas |
| test_bug046_authority_fence.py: todas | tools/tests/test_coordination_authority_fence.py | mover |
| test_authority_invariants_are_gated.py: todas | tools/tests/test_coordination_authority_fence.py | mover |
| test_task7_review_regressions.py: ExactLeaseBarrierTest | tools/tests/test_coordination_authority_fence.py | mover |
| test_task7_review_regressions.py: ReleaseAuditTest | tools/tests/test_coordination_audit_faults.py | mover |
| test_task7_review_regressions.py: DispatchAuthorityAndQuarantineTest | tools/tests/test_loopback_authority_quarantine.py | mover |
| test_task7_review_regressions.py: LifecyclePostAuditQuarantineTest, RunStateRecoveryTest, RestartAndManifestTest, DaemonReleaseWiringTest | tools/tests/test_process_lifecycle_authority.py | mover |
| test_task7_review_regressions.py: RealLifecycleHttpQuarantineTest | tools/tests/test_session_http_contract.py | mover |
| test_task7_review_regressions.py: DayzMcpWorkerWaitTest | tools/tests/test_test_support.py | mover como test de utilidad |
| test_task7_rereview_regressions.py: AuthorityIoBoundaryTest, DispatchAuditDegradationTest | tools/tests/test_loopback_authority_quarantine.py o tools/tests/test_coordination_audit_faults.py | mover a auditoría si la aserción principal es degraded; si es poll/I/O, loopback |
| test_task7_rereview_regressions.py: CleanupBudgetAndFencingTest | tools/tests/test_coordination_authority_fence.py | mover |
| test_task7_rereview_regressions.py: FailedLaunchSettlementTest, LifecycleRecoveryAndOutcomeTest, GuardFailureNormalizationTest, GateEvidenceContractTest, ToolHelpCloseFailureTest | tools/tests/test_process_lifecycle_authority.py | mover |
| test_task7_rereview_regressions.py: SettlementFlakeProbeExitTest | tools/tests/test_test_support.py | mover como test de harness |
| test_task7_final_authority_regressions.py: ReleasingBudgetTest | tools/tests/test_lease_queue_fifo.py | mover |
| test_task7_final_authority_regressions.py: CleanupWorkerCapacityTest | tools/tests/test_coordination_audit_faults.py | mover |
| test_task7_final_authority_regressions.py: GrantLedgerLinearizationTest | tools/tests/test_coordination_authority_fence.py | mover |
| test_task7_final_authority_regressions.py: StateLockIoBoundaryTest, ExecEnforceCapacityReservationTest, OwnerReadRevalidationTest | tools/tests/test_loopback_authority_quarantine.py | mover |
| test_0ab2_grace.py: GraceS1Tests | tools/tests/test_lease_queue_fifo.py | mover; `test_n3_stranger_wins_after_grace` se fusiona/borra como D01 |
| test_0ab2_grace.py: TakeoverBTests, DayzTestRunTakeoverTests | tools/tests/test_takeover_contract.py | mover |
| test_0ab2_r9.py: StateMachineR9Tests, RaceR9Tests, IdentityR9Tests | tools/tests/test_lease_queue_fifo.py o tools/tests/test_coordination_authority_fence.py | mover identidad a autoridad si la aserción es identity_mismatch |
| test_0ab2_r9.py: DataLossR9Tests | tools/tests/test_coordination_audit_faults.py | mover |
| test_0ab2_r9.py: DayzTestRunIdentityR9Tests | tools/tests/test_takeover_contract.py | mover |
| test_session_acquire_wait.py: OperationQueueCoordinatorTests | tools/tests/test_lease_queue_fifo.py | mover |
| test_session_acquire_wait.py: ClientAcquireWaitTests | tools/tests/test_client_acquire_wait.py | mover |
| test_session_handoff.py | tools/tests/test_session_handoff_reload.py | mover |
| test_reload_lease_recovery.py | tools/tests/test_session_handoff_reload.py | mover |
| test_session_http.py | tools/tests/test_session_http_contract.py | mover |

## E. Plan de fusión
1. **Crear helper único de dominio**:
   - Crear `tools/tests/helpers_lease_coordination.py` con relojes, ids, audit/cleanup sinks, builders de identity/coordinator, WAL/recorder/HTTP helpers.
   - No cambiar asserts todavía.
   - Verificación: ejecutar la familia y comprobar que el número de tests recolectados es idéntico al de HEAD actual; si se añade helper-test, ese aumento debe ser 1 y visible.

2. **Sustituir imports cross-test por helpers**:
   - Reemplazar imports desde `tests.test_*` por imports a helpers en las 14 fuentes.
   - Conservar imports de helpers de terceros ya usados por el repo si son legítimos (`fence_helpers`, `steam_helpers`, etc.) salvo mover a helper común cuando sea dominio de test.
   - Verificación: grep de `from tests.test_` debe dejar 0 resultados dentro de esta familia, salvo ficheros de tests de helpers que se autoimporten; suite verde y mismo número de tests recolectados.

3. **Dividir destinos por dominio**:
   - Crear los destinos de D:
     - `tools/tests/test_lease_queue_fifo.py`
     - `tools/tests/test_coordination_authority_fence.py`
     - `tools/tests/test_coordination_audit_faults.py`
     - `tools/tests/test_loopback_authority_quarantine.py`
     - `tools/tests/test_session_handoff_reload.py`
     - `tools/tests/test_session_http_contract.py`
     - `tools/tests/test_process_lifecycle_authority.py`
     - `tools/tests/test_takeover_contract.py`
     - `tools/tests/test_client_acquire_wait.py`
     - `tools/tests/test_test_support.py`
   - Mover clases completas a destino; no borrar todavía.
   - Verificación: ejecutar destino a destino y suite completa; mismo número de tests; cobertura por línea igual o mayor por import de helpers sin pérdida de líneas ejecutadas.

4. **Fusionar CASI con union conservadora**:
   - Para D02-D16, fusionar en el destino elegido creando un test con un prefijo `subTest` por variante o conservando tests separados si el caso límite es crítico.
   - No eliminar todavía ninguno de D02-D16 hasta que el receptor confirme que las aserciones únicas se mantienen.
   - Verificación:
     - Comparar asserts únicos de cada test fuente contra destino.
     - Mantener `subTest` para 0.001, TTL exacto, TTL+G exacto, prepared vs commit, identity mismatch, HTTP, reload, probe/grace shape.
     - Suite verde y cobertura igual o mayor.

5. **Eliminar D01**:
   - Solo tras verificación mecánica:
     - `test_n3_stranger_wins_after_grace`
     - vs `test_stranger_grants_after_spec_grace_window`
   - Borrar la fuente redundante y añadir comentario de referencia si se desea trazabilidad a la ficha 0ab2/H4.
   - Verificación:
     - Número de tests disminuye exactamente 1 respecto al pre-D01.
     - El test restante cubre 202/200 tras TTL+G y mantiene `attached_run_probe=True`.
     - Suite verde y cobertura no baja.

6. **Separar contratos de documentación/product-spec**:
   - Mover `Bug046DpfContractTests` a `test_product_spec_coordination_contract.py` si se considera contrato de docs, no conducta runtime.
   - Verificación: si el equipo decide que es contract test, cambiar destino; no cambiar asserts.
   - Suite verde; cobertura runtime igual o mayor porque no se toca runtime.

7. **Cierre**:
   - Ejecutar `unittest` de la familia en origen, destino y repo completo.
   - Confirmar:
     - 0 imports `tests.test_*` a test siblings en la familia objetivo, salvo helper tests.
     - Todos los destinos del mapa D existen y ejecutan.
     - Los 15 CASI conservan sus casos límite.
     - D01 es el único borrado si se acepta como DUPLICADO.

## F. Valoración
| Eje | Nota | Motivo |
|---|---:|---|
| Cobertura de conducta | 8 | Alta variedad de estados y fallos: TTL, FIFO, grace, auditoría, WAL, lifecycle, HTTP, reload, tombstones, concurrency. |
| Redundancia | 4 | Hay 15 pares CASI y un DUPLICADO claro en material visible; muchos ficheros por bug/task repiten la misma frontera con variantes pequeñas. |
| Acoplamiento | 2 | Tests importan otros tests, productos docs/`product-spec.md`, internals como `coordinator._condition`, `_active`, `_releasing`, locks de loopback y fixtures lifecycle. |
| Legibilidad | 4 | Ficheros largos, clases enormes, helpers inline, pero docstrings/comentarios en casos críticos son útiles y a veces explican mutaciones y fences. |
| Total | 5 | Conducta rica y valiosa, pero lista para consolidar por dominio y helper para reducir costo de revisión y riesgo de regresiones por duplicado parcial. |

## LO QUE NO PUDE VERIFICAR
- Número exacto de tests únicos, tests duplicados a nivel de cobertura, cobertura de línea, suite verde/roja o tiempos de ejecución.
- Si los 15 pares CASI cubren realmente la misma línea de producto durante ejecución, no lo ejecuté.
- Qué fakes inline son 100% equivalentes a los helpers propuestos por AST o ejecución; solo identifiqué copias visibles por lectura.
- Si `product-spec.md` es el destino natural de `Bug046DpfContractTests` o si debe seguir junto a runtime por regla del equipo.
- Límites exactos de rendimiento o timeouts en `DayzMcpWorkerWaitTest`, `SettlementFlakeProbeExitTest` o helpers de workers, porque no los ejecuté.

## ¿Qué puede estar mal en la premisa de este encargo?
- Un test puede parecer duplicado textual pero no ejecutar el mismo camino de producto si cambia el hilo, lock, WAL boundary, order de auditoría o HTTP transport.
- Los nombres por bug/tarea/ficha pueden ser trazabilidad requerida; borrar o renombrar puede perder evidencia de regresión histórica.
- Esta familia incluye lifecycle, process guard y toolhelp, que no son estrictamente lease/FIFO; consolidar todo junto puede volver a mezclar dominios.
- Los imports cross-test pueden ser deliberados para reutilizar fixtures mientras no exista helper común; eliminarlos sin crear helper puede romper builds.
- Duplicar asserts pequeños a veces protege mutaciones distintas (prepared vs commit, release terminal vs expiry), por lo que fusionar sin `subTest` podría debilitar la red de seguridad.

GATE NO CORRIDO: revisión por API sin herramientas