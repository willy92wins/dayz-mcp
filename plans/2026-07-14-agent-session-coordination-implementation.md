# DayZ MCP Agent Session Coordination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Añadir al broker DayZ MCP una coordinación multiagente FIFO con leases de 120 s, cleanup owner-scoped, lifecycle de procesos fail-closed y un protocolo durable compartido por Claude y Codex.

**Architecture:** El daemon D-14 sigue siendo la única autoridad de `127.0.0.1:8765`. Un módulo de coordinación independiente administra identidad, tickets, lease y auditoría; `loopback.py` aplica autorización antes de encolar, y `ClientRuntime` transporta identidad/token automáticamente. El lifecycle se ejecuta dentro del daemon y usa un guard PowerShell que revalida el proceso inmediatamente antes de terminarlo; los launchers solo llaman al helper central.

**Tech Stack:** Python 3.14/stdlib, `mcp==1.27.2` FastMCP, `http.server.ThreadingHTTPServer`, PowerShell 5.1+, `unittest`, JSON/JSONL y APIs Windows `Get-Process`/`Get-CimInstance`.

## Global Constraints

- Fuente de diseño aprobada: `plans/2026-07-14-agent-session-coordination-design.md`.
- DPF: cada tarea traza a `product-spec.md` H1-H8; no se cambia la intención de D-14/D-15.
- Baseline fresco 2026-07-14: `154/154` tests PASS con `.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t . -v`.
- Python-only para coordinación; no tocar Enforce/PBO ni `MCP_BRIDGE_VERSION`.
- Un único daemon/listener; sesiones interactivas Claude/Codex siempre `--client`; embedded queda explícito para CI/offline/backcompat y nunca es fallback automático.
- Lecturas puras no requieren lease; cualquier comando no clasificado se considera mutante.
- FIFO estricta; TTL de ticket y lease exactamente `120.0` s; `session_wait` máximo `30.0` s.
- Una operación mutante aceptada puede fijar el lease hasta su hard deadline, con techo absoluto `MAX_OPERATION_PIN_S = 300.0`; ningún timeout del caller lo fija más tiempo.
- Ningún cliente puede evictar otro. Solo owner release, TTL o helper admin interactivo auditado.
- Release/expiry no matan DayZ ni daemon. Cancelan solo comandos del owner aún no entregados e intentan `vehicle_release` de forma acotada.
- Tokens/keyfiles nunca aparecen en logs, manifiestos, status, argv ni errores. El helper de lifecycle los lee solo de entorno.
- No añadir dependencias Python. Si el probe Windows no puede recuperar creation time, executable y command line, la fase B se detiene fail-closed.
- No inferir ownership por `@Mod`, profiles, misión o nombre de exe.
- El workspace no es Git (`git rev-parse` devuelve 128). No inicializar repositorio. Cada tarea termina con tests + lista de archivos/hash en el handoff; si durante la ejecución aparece un repo real, usar commits pequeños sin cambiar este alcance.

## File Map

**Nuevos módulos de responsabilidad única**

- `tools/dayz_mcp/session_coordination.py` — identidad, clasificación, FIFO, leases y autorización.
- `tools/dayz_mcp/runtime_state.py` — paths `%LOCALAPPDATA%`, auditoría JSONL rotada y snapshots atómicos sin secretos.
- `tools/dayz_mcp/process_lifecycle.py` — run manifest, start/adopt/stop y revalidación mediante el guard.
- `tools/dayz_mcp/lifecycle_cli.py` — CLI no-MCP para launchers; lee credenciales efímeras del entorno.
- `tools/dayz_mcp/admin_cli.py` — force-release/reconcile solo desde TTY, con confirmación y motivo.
- `tools/dayz_mcp/doctor.py` — diagnóstico de topología, configs, sesiones, runs y launchers legacy.
- `tools/process-guard.ps1` — snapshot/discover/terminate atómico y fail-closed sobre PIDs registrados.
- `tools/_session_coordination/process-identity-probe.ps1` — viability gate previo al lifecycle.
- `tools/_session_coordination/e2e_agent_sessions.py` — gate offline binario multi-cliente.
- `tools/tests/test_session_coordination.py`, `test_runtime_state.py`, `test_session_http.py`, `test_session_e2e.py`, `test_process_lifecycle.py`, `test_lifecycle_cli.py`, `test_doctor.py`.
- `C:\Users\guill\ObsidianVault\AI\20_Runbooks\dayz-mcp-agent-session-protocol.md` — protocolo canónico.
- `AGENTS.md` — reglas cortas específicas de DayZ_MCP para Codex.

**Archivos existentes que cambian**

- `tools/dayz_mcp/loopback.py` — autorización en `/enqueue`, endpoints session/lifecycle/admin y ownership de comandos.
- `tools/dayz_mcp/daemon.py` — composición del coordinator/store/lifecycle y generación del daemon.
- `tools/dayz_mcp/server.py` — identidad del proxy, tools `session_*`, transporte automático del token y flags cliente.
- `tools/install-mcp.ps1` y `tools/README-mcp.md` — registro/verificación Claude+Codex y doctor.
- `tools/_broker/e2e_daemon.py` — no-regresión del broker y topología.
- `product-spec.md`, `decisions/decision-log.md`, `CLAUDE.md`, `HANDOFF.md` — estado/contrato/cierre.
- Instrucciones globales y skills listadas en Task 9.
- Los 14 launchers activos `*/tools/dayz-test.ps1` listados en Task 9; backups no se modifican.

## DPF Trace

| Fase | Tareas | Criterios |
|---|---|---|
| A — Coordinator | 1-5 | H1, H2, H3, H4, H5, H7 |
| B — Process lifecycle | 6-7 | H2, H5, H6, H7 |
| C — Adoption and proof | 8-10 | H1, H7, H8 |

---

## Phase A — Coordinator core

### Task 1: Pure session state machine

**Files:**
- Create: `tools/dayz_mcp/session_coordination.py`
- Create: `tools/tests/test_session_coordination.py`

**Interfaces:**
- Produces: `ClientIdentity`, `SessionCoordinator`, `AuthorizationDecision`, `READ_ONLY_COMMANDS`, `command_requires_lease()`.
- Consumes: monotonic `time_fn`, injectable `token_fn`/`id_fn`, `audit(event)` and `cleanup(session_id, reason, vehicle_active)` callbacks.

- [ ] **Step 1: Write failing identity/classification tests**

```python
READ_ONLY = {
    "query_player_state", "scene_raycast", "telemetry_read",
    "query_get_in_condition", "camera_get", "vehicle_telemetry",
}

def test_unknown_command_is_mutating_by_default(self):
    self.assertFalse(command_requires_lease("camera_get"))
    self.assertTrue(command_requires_lease("brand_new_tool"))

def test_identity_rejects_missing_or_invalid_fields(self):
    with self.assertRaises(ValueError):
        ClientIdentity.from_payload({"platform": "codex"})
    with self.assertRaises(ValueError):
        ClientIdentity.from_payload(self.identity_payload | {"pid": True})
```

- [ ] **Step 2: Run the focused tests and prove RED**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest tests.test_session_coordination -v`

Expected: import failure for `dayz_mcp.session_coordination`.

- [ ] **Step 3: Add exact constants and immutable identity model**

```python
SESSION_TTL_S = 120.0
WAIT_MAX_S = 30.0
MAX_SESSION_QUEUE = 64
READ_ONLY_COMMANDS = frozenset({
    "query_player_state", "scene_raycast", "telemetry_read",
    "query_get_in_condition", "camera_get", "vehicle_telemetry",
})
MAX_OPERATION_PIN_S = 300.0

def command_requires_lease(command: str) -> bool:
    return command not in READ_ONLY_COMMANDS

@dataclass(frozen=True)
class ClientIdentity:
    platform: str
    pid: int
    ppid: int
    started_at_utc: str
    session_id: str
    task_label: str = ""

    @classmethod
    def from_payload(cls, value: object) -> "ClientIdentity":
        if not isinstance(value, dict):
            raise ValueError("invalid_identity")
        platform = value.get("platform")
        pid, ppid = value.get("pid"), value.get("ppid")
        started = value.get("started_at_utc")
        session_id = value.get("session_id")
        label = value.get("task_label", "")
        if platform not in {"claude", "codex", "unknown"}:
            raise ValueError("invalid_identity")
        if any(not isinstance(number, int) or isinstance(number, bool) or number < 0 for number in (pid, ppid)):
            raise ValueError("invalid_identity")
        if not isinstance(started, str) or not started or not isinstance(session_id, str) or not session_id:
            raise ValueError("invalid_identity")
        if not isinstance(label, str) or len(label) > 120:
            raise ValueError("invalid_identity")
        return cls(platform, pid, ppid, started, session_id, label)

    def to_payload(self) -> dict[str, object]:
        return dataclasses.asdict(self)

    def public_payload(self) -> dict[str, object]:
        return {"platform": self.platform, "session": self.session_id[:12],
                "started_at_utc": self.started_at_utc, "task_label": self.task_label}
```

Validation is exact: payload must be an object; `platform in {"claude","codex","unknown"}`; PID/PPID are non-negative integers and not bool; timestamps/session IDs are non-empty strings; `task_label` is a string capped at 120 chars. `public_payload()` omits PID/PPID and exposes platform, session-id prefix, start and label only.

- [ ] **Step 4: Write RED tests for FIFO, idempotency, ownership and 119/120/121**

```python
def test_fifo_idempotent_and_abandoned_middle_ticket(self):
    active = self.coordinator.acquire(self.a, "drive")
    b1 = self.coordinator.acquire(self.b, "camera")
    b2 = self.coordinator.acquire(self.b, "camera")
    c = self.coordinator.acquire(self.c, "weather")
    self.assertEqual(b1[1]["ticket"], b2[1]["ticket"])
    self.assertEqual(c[1]["position"], 2)
    self.clock.advance(119.0)
    token = active[1]["lease_token"]
    self.assertTrue(self.coordinator.authorize(self.a, token, "world_spawn").allowed)
    self.clock.advance(1.0)
    self.coordinator.wait(self.c, c[1]["ticket"], 0.0)
    self.assertEqual(self.coordinator.status(self.c)["self"]["position"], 1)

def test_lease_boundary_is_exact(self):
    token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
    self.clock.advance(119.0)
    self.assertTrue(self.coordinator.authorize(self.a, token, "world_spawn").allowed)
    self.clock.advance(120.0)
    self.assertEqual(self.coordinator.authorize(self.a, token, "world_spawn").error, "lease_expired")

def test_ticket_and_token_are_bound_to_identity(self):
    token = self.coordinator.acquire(self.a, "drive")[1]["lease_token"]
    decision = self.coordinator.authorize(self.b, token, "world_spawn")
    self.assertEqual((decision.allowed, decision.error), (False, "lease_invalid"))
```

Use a fresh coordinator for each boundary case so 119, 120 and 121 s are independently asserted.

- [ ] **Step 5: Implement `SessionCoordinator` with one condition lock**

The final module defines `AuthorizationDecision(allowed, http_status, error, owner_session_id, cleanup_degraded=())` as a frozen dataclass, with `cleanup_degraded: tuple[str, ...]`, and exposes these exact methods: `acquire(client, purpose)`, `wait(client, ticket_id, timeout_s)`, `heartbeat(client, lease_token)`, `release(client, lease_token, reason="owner_release")`, `authorize(client, lease_token, command, operation_timeout_s=0.0)`, `abort_authorization(owner_session_id, command, reason)`, `finish_operation(owner_session_id, command_id)`, `note_command(owner_session_id, command_id, command, args)` and `status(client)`. `abort_authorization()` returns `tuple[str, ...]`; it removes exactly one matching pending authorization and pending pin from the active or releasing owner, never changes `vehicle_active`, bumps revision only when state changed, emits `session_rejected` with the supplied non-empty reason, and still cleans state while returning `("audit_failed",)` if that audit append fails. Other return types remain `tuple[int, dict]`, `AuthorizationDecision`, `None` and `dict`; no alternate names are allowed in later tasks.

Rules implemented inside the condition lock: one live ticket/lease per session ID; token generated with `secrets.token_urlsafe(32)`; distinct public `lease_id`; wait blocks at most `min(timeout_s, 30.0)` and wakes at owner/ticket deadline; `_expire_due()` runs before every decision; mutating authorization renews and sets `pinned_until = now + min(operation_timeout_s, 300.0)`; `finish_operation` removes that command's pin; a read is always authorized without requiring a lease and renews only when it carries the valid token, while an absent, invalid or expired optional token never denies the read; status never includes tokens or raw PIDs. Mark `vehicle_active=True` only after accepted `vehicle_control` with any non-zero control, and false on accepted `vehicle_release`.

Audit append is the only bounded local I/O allowed while holding the condition lock. Acquire/grant/heartbeat/mutating authorization return `503 audit_failed` and leave state unchanged if their required event cannot be written. Owner release and TTL expiry still invalidate/clean up for safety and return `cleanup_degraded=["audit_failed"]`; `authorize()` transports the same condition as `AuthorizationDecision.cleanup_degraded == ("audit_failed",)`. No lock is held across HTTP, bridge wait, process guard or lifecycle cleanup.

- [ ] **Step 6: Run state-machine tests GREEN**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest tests.test_session_coordination -v`

Expected: all tests PASS, including independent 119/120/121, stolen ticket/token and duplicate acquire cases.

- [ ] **Step 7: No-git checkpoint**

Run: `git -C .. rev-parse --is-inside-work-tree`

Expected in this workspace: exit 128. Record the two file paths, SHA256 and test command in the session handoff; do not initialize Git.

### Task 2: Runtime storage, redaction and restart evidence

**Files:**
- Create: `tools/dayz_mcp/runtime_state.py`
- Create: `tools/tests/test_runtime_state.py`
- Modify: `tools/dayz_mcp/session_coordination.py`

**Interfaces:**
- Produces: `RuntimePaths`, `JsonlAuditWriter`, `CoordinationSnapshotStore`.
- Consumes: coordinator audit callback and redacted `snapshot_payload()`.

- [ ] **Step 1: Write RED tests for path precedence, atomic snapshot and secret redaction**

```python
def test_runtime_path_uses_localappdata_not_onedrive(self):
    paths = RuntimePaths.from_env({"LOCALAPPDATA": r"C:\Local"})
    self.assertEqual(paths.root, Path(r"C:\Local\DayZ_MCP"))

def test_audit_redacts_recursive_secret_fields(self):
    writer.write({"event":"grant", "lease_token":"secret", "nested":{"key":"secret"}})
    text = writer.current_path.read_text(encoding="utf-8")
    self.assertNotIn("secret", text)
    self.assertIn('"lease_token":"[REDACTED]"', text)

def test_restart_invalidates_without_persisting_token(self):
    store.write_coordination({"daemon_generation":"old", "active":{"lease_id":"L", "lease_token":"secret"}})
    loaded = store.consume_previous_generation("new")
    self.assertEqual(loaded["previous_generation"], "old")
    self.assertNotIn("secret", store.coordination_path.read_text(encoding="utf-8"))
```

- [ ] **Step 2: Run focused tests and prove RED**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest tests.test_runtime_state -v`

Expected: import failure for `dayz_mcp.runtime_state`.

- [ ] **Step 3: Implement fixed storage contract**

```python
SECRET_KEYS = frozenset({"key", "api_key", "keyfile", "lease_token", "password", "token"})
AUDIT_MAX_BYTES = 5 * 1024 * 1024
AUDIT_BACKUPS = 5

@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    audit_dir: Path
    coordination_path: Path
    runs_path: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RuntimePaths":
        values = os.environ if env is None else env
        base = values.get("LOCALAPPDATA", "")
        if not base:
            raise RuntimeError("localappdata_unavailable")
        root = Path(base) / "DayZ_MCP"
        return cls(root, root / "audit", root / "coordination.json", root / "runs.json")
```

`JsonlAuditWriter(paths, daemon_generation, utc_now_fn=None).write(event)` and `CoordinationSnapshotStore(paths, daemon_generation).write_coordination(payload) -> bool`/`consume_previous_generation(generation)` are the only public persistence methods. Both stores inject their constructor's non-empty `daemon_generation`; the audit writer also injects the UTC timestamp, so `SessionCoordinator` remains generation-agnostic. They use the fixed paths/constants above and the atomic/redaction algorithm in the following paragraph; later tasks must not introduce a second writer.

`from_env` raises `RuntimeError("localappdata_unavailable")` if the base is absent. Atomic writes use a sibling `.tmp`, `flush()`, `os.fsync()` and `os.replace()`. Rotation occurs before append when the current JSONL would exceed 5 MiB, retaining `.1` through `.5`. `JsonlAuditWriter` owns a `threading.Lock` covering rotation+append. Redaction walks dict/list recursively before serialization. Coordination snapshots contain generation, monotonic integer revision, lease ID/session public ID/timestamps and queued ticket IDs only; never token/key/cmdline. `SessionCoordinator` starts revision at zero and increments it for every observable state mutation, never for a pure snapshot read or a rejected operation that leaves state unchanged. `CoordinationSnapshotStore` owns a lock covering revision comparison plus atomic write; it writes only a revision greater than the last accepted revision and returns `True`, while an equal/older revision is a benign no-op returning `False`.

- [ ] **Step 4: Wire audit and expose the redacted snapshot contract**

Emit these exact event names: `session_acquire`, `session_queued`, `session_granted`, `session_wait`, `session_heartbeat`, `session_authorized`, `session_rejected`, `session_release_started`, `session_release_finished`, `session_expired`, `ticket_cancelled`, `daemon_restart_invalidated`. Every event includes UTC timestamp, daemon generation, public client, lease/ticket/run IDs where applicable, decision/reason and duration. Never include request args.

`JsonlAuditWriter.write()` rejects an absent/empty `reason` or a non-finite/negative/missing `duration_s` with `ValueError("invalid_audit_event")`; it never invents evidence defaults. Exact reasons are: `request`, `lease_busy`, `fifo_head`, the `session_wait` outcome (`granted`, `queued` or `ticket_expired`), `owner_heartbeat`, authorization decision (`read_only` or `lease_valid`), the exact rejection error, the supplied release/cleanup reason, `lease_ttl`, `ticket_ttl` and `daemon_restart`. `session_wait.duration_s` is actual monotonic elapsed time from method entry through return; `session_release_finished.duration_s` is actual cleanup elapsed time. Instantaneous decisions explicitly emit `0.0`.

Add the public, additive `SessionCoordinator.snapshot_payload() -> dict[str, object]`. It captures a fully redacted copy under the condition lock and performs no I/O. Task 2 tests the payload and stores independently. The daemon composition in Task 3 calls `write_coordination(coordinator.snapshot_payload())` after each public transition and after releasing the coordinator lock; no snapshot callback or storage I/O is added to the state-machine constructor.

`consume_previous_generation()` collects attribution from both `active` and `releasing`, plus queued tickets, before clearing them. Its `daemon_restart_invalidated` event contains every affected public client and applicable lease/ticket ID; a releasing-only fixture and a mixed active/releasing/queue fixture are mandatory.

- [ ] **Step 5: Run focused tests GREEN and scan raw files**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest tests.test_runtime_state tests.test_session_coordination -v`

Expected: PASS. `test_runtime_state` reads its own `TemporaryDirectory` before teardown and asserts that `secret` and a token-shaped value are absent from every JSON/JSONL file.

- [ ] **Step 6: No-git checkpoint**

Record hashes and passing commands; do not create a repo.

### Task 3: Daemon HTTP enforcement and owner-scoped command cleanup

**Files:**
- Modify: `tools/dayz_mcp/loopback.py:116-708`
- Modify: `tools/dayz_mcp/daemon.py:58-210`
- Create: `tools/tests/test_session_http.py`
- Modify: `tools/tests/test_daemon.py`
- Modify: `tools/tests/test_loopback.py`

**Interfaces:**
- Consumes: `SessionCoordinator`, `ClientIdentity`, `command_requires_lease()` and runtime store.
- Produces HTTP: `POST /session/acquire|wait|heartbeat|release|status`; extends `POST /enqueue` with `identity` and optional `lease_token`.
- Produces queue APIs: `cancel_owner_pending(session_id, reason)` and `pending_for_owner(session_id)`.

- [ ] **Step 1: Write RED endpoint tests with exact wire schemas**

```python
IDENTITY_A = {"platform":"codex","pid":11,"ppid":1,"started_at_utc":"2026-07-14T10:00:00Z","session_id":"A","task_label":"test"}

def test_mutation_requires_lease_but_read_is_admitted(self):
    status, body = _http(self.base, "POST", "/enqueue", self.key,
        {"identity": IDENTITY_A, "cmd":"world_spawn", "args":{"type":"X","pos":[1,2,3]}, "peer":"server"})
    self.assertEqual((status, body["error"]), (423, "lease_required"))
    status, body = _http(self.base, "POST", "/enqueue", self.key,
        {"identity": IDENTITY_A, "cmd":"query_player_state", "args":{}, "peer":"server"})
    self.assertEqual(status, 200)

def test_owner_release_cancels_only_its_undelivered_commands(self):
    # Acquire A, queue A mutation and B read, release A.
    # A resolves owner_released; B remains deliverable.
```

Also assert: missing identity `400 invalid_identity`; stolen token `403 lease_invalid`; unknown tool classification is mutating before whitelist response; `/session/status` has no token/PID; bad wait range returns `400 bad_wait_timeout`; audit write failure rejects acquire/mutation with 503 but cannot prevent owner release from invalidating; `operation_timeout_s=9999` pins at most 300 s.

Total-schema negatives are mandatory: non-string `peer` returns `423 lease_required` without a lease and `400 bad_peer` plus reservation abort with a valid lease; non-string `lease_token` returns `403 lease_invalid` without side effects; non-numeric, non-finite or overflowed `operation_timeout_s` still performs the lease gate first and, with a valid lease, returns `400 bad_operation_timeout` plus abort. No malformed raw JSON value is audited.

- [ ] **Step 2: Run focused HTTP tests RED**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest tests.test_session_http -v`

Expected: `/session/*` returns 404 and mutation without lease still enqueues.

- [ ] **Step 3: Extend `ServerState` without changing embedded defaults**

Add `coordination: SessionCoordinator | None = None` as the final `ServerState.__init__` parameter after the existing `time_fn`; assign `self.coordination` and initialize `self._command_owner: dict[int, str] = {}`. Extend `enqueue_command` with keyword-only `identity_payload: object = None`, `lease_token: str | None = None`, `operation_timeout_s: float = 0.0`, `internal: bool = False`. Add exact methods `cancel_owner_pending(session_id, reason) -> dict[str, int]`, `pending_for_owner(session_id) -> int`, and `cleanup_owner(session_id, reason, vehicle_active) -> dict[str, object]`.

Change the existing `ServerState` mutex to `threading.RLock`. With coordination enabled, hold it across the atomic sequence `authorize -> validate/append queue -> note_command`; this prevents release cleanup from completing between authorization and insertion. Reentrancy is required because an expiry discovered by `authorize()` can synchronously invoke `cleanup_owner()` on the same thread. Capture owner/command IDs while locked but call `finish_operation()` only after releasing the state lock. Add a barrier-controlled regression that forces authorize to pause while another thread releases, then proves the command is either rejected before insertion or inserted and cancelled—never left pending after release.

When `coordination is None` (embedded/harness), preserve current byte-level enqueue behavior. When enabled, parse identity and normalize a non-string/unhashable `cmd` only for authorization/audit to the safe sentinel `""`; never serialize the raw malformed value. The sentinel is mutating-default: without lease return `423 lease_required`; with a valid lease continue to validation, return `400 not_whitelisted`, and abort the reservation. Validate untrusted `peer`, `lease_token` and `operation_timeout_s` without hashing or floating-point overflow: malformed peer follows the same lease-first pattern and ends as `400 bad_peer`; malformed token is `403 lease_invalid`; malformed/non-finite/overflow timeout ends as `400 bad_operation_timeout` after a valid lease. Call `authorize(client, safe_lease_token, safe_cmd, safe_operation_timeout_s)` before adding to `_queues`. After allocating the command ID, call `note_command(owner_session_id, command_id, cmd, args)`. A `finally`-guarded reservation tracks whether a lease-required authorization reached an accepted command ID; every other exit or exception calls `abort_authorization(owner_session_id, safe_cmd, exact_error)` and merges its degradation, so no rejected request retains a pending authorization or pin. Call `finish_operation(owner_session_id, command_id)` from `store_result`, `_mark_discarded` and result removal. Store `owner_session_id` only in Python queue metadata; strip it from the command returned by `/poll` so the Enforce wire schema is unchanged. `cancel_owner_pending` filters both peer queues, calls `_mark_discarded(command, reason, discarded_exec)` and leaves other owners untouched. `cleanup_owner` cancels, then internally enqueues `vehicle_release` only when `vehicle_active` is true; it never calls process termination.

Internal cleanup `vehicle_release` commands are fire-and-forget: enqueue with no owner mapping, track their IDs in a private set, and purge the ID/result automatically on `store_result` or any discard/reconnect/TTL path. They may exist while queued/in flight but must leave `pending_commands=0` and `results_pending=0` after result or discard; no external await is required.

- [ ] **Step 4: Add authenticated session routes**

```python
SESSION_ROUTES = {
    "/session/acquire": "acquire", "/session/wait": "wait",
    "/session/heartbeat": "heartbeat", "/session/release": "release",
    "/session/status": "status",
}
```

After each `/session/*` transition and each `/enqueue` authorization decision, the daemon-side route wrapper calls `write_coordination(coordination.snapshot_payload())` only after the coordinator method has returned. A `False` return is a benign superseded revision. If the write raises after a transition or accepted enqueue, preserve the original HTTP status/body—including any granted token or command ID—and append `cleanup_degraded=["snapshot_failed"]`; never convert an already-applied success into 503, retry it or roll it back. Snapshot persistence never executes under the coordinator condition lock.

All are POST because identity is a JSON body. Handler touch counts as client activity. `/session/release` enters RELEASING, invokes `cleanup_owner` outside the coordinator condition lock, invalidates the token and wakes the next ticket. HTTP logs contain event/lease IDs only.

- [ ] **Step 5: Compose daemon generation/store/coordinator in `build_server_state`**

Create one UUID generation per `run_daemon`, but perform no coordination snapshot/audit mutation before the process wins the exclusive bind. Build an inert base `ServerState` and dynamic status provider, call `_bind_with_reclaim`, and return immediately if another daemon wins. Only after a successful bind create runtime paths/writer/store/coordinator, attach them to the already-bound state, consume the old coordination snapshot and emit `daemon_restart_invalidated`; then start `serve_forever`. Direct `build_server_state` test callers may request immediate activation explicitly, but `run_daemon` must use bind-first activation. A losing candidate leaves coordination JSON/JSONL byte-identical and does not create them when absent. Do not arm parent-death watchdog.

- [ ] **Step 6: Run focused and no-regression tests GREEN**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest tests.test_session_http tests.test_daemon tests.test_loopback tests.test_x5_loopback -v`

Expected: PASS; existing embedded tests remain unchanged and green.

- [ ] **Step 7: No-git checkpoint**

Record hashes/tests and verify `rg -n 'Stop-Process|TerminateProcess' tools/dayz_mcp/session_coordination.py tools/dayz_mcp/loopback.py tools/dayz_mcp/daemon.py` returns no match.

### Task 4: Proxy identity, session tools and automatic lease transport

**Files:**
- Modify: `tools/dayz_mcp/server.py:34-903`
- Modify: `tools/tests/test_client_mode.py`
- Modify: `tools/tests/test_mcp_tools.py`

**Interfaces:**
- Adds `ServerConfig.client_platform`, `task_label`, `session_ttl_s`, `runtime_dir`.
- Adds `ClientRuntime.session_acquire/wait/heartbeat/release/status`.
- Adds FastMCP tools with the same names; existing mutation signatures do not gain a token parameter.

- [ ] **Step 1: Write RED proxy/tool tests**

```python
async def test_client_acquire_stores_token_and_mutation_transports_it(self):
    runtime = self._client(srv, client_platform="codex")
    acquired = await runtime.session_acquire("spawn fixture")
    self.assertEqual(acquired["status"], "active")
    self.assertEqual(runtime.active_lease_token, acquired["lease_token"])
    self.assertEqual(json.loads(acquired["client_identity_json"])["session_id"], runtime.identity.session_id)
    result = await runtime.call_bridge("world_spawn", {"type":"X","pos":[1,2,3]}, "server", 2.0)
    self.assertTrue(result["ok"])

async def test_session_tools_require_client_mode(self):
    app, _runtime = self.build_started()
    with self.assertRaises(Exception) as err:
        await app.call_tool("session_acquire", {"purpose":"x"})
    self.assertIn("session_tools_require_client_mode", str(err.exception))
```

Also test two client runtimes get different session IDs, task labels cap at 120, release clears local token/ticket, and daemon-unreachable never switches to embedded.

- [ ] **Step 2: Run focused tests RED**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest tests.test_client_mode tests.test_mcp_tools -v`

Expected: missing config fields/session methods/tools.

- [ ] **Step 3: Build one identity per stdio proxy**

```python
self.identity = ClientIdentity(
    platform=config.client_platform,
    pid=os.getpid(), ppid=os.getppid(),
    started_at_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    session_id=str(uuid.uuid4()),
    task_label=(config.task_label or os.environ.get("DAYZ_MCP_TASK_LABEL", ""))[:120],
)
self.active_lease_token: str | None = None
self.active_ticket: str | None = None
```

`ClientRuntime.session_acquire/session_wait` append `client_identity_json=json.dumps(self.identity.to_payload(), separators=(",", ":"))` to their own successful response; the daemon never returns raw PID/PPID for another client. `call_bridge` always sends `identity` and `operation_timeout_s=timeout_s`; it sends `lease_token` only when locally active. Existing tool signatures remain stable.

- [ ] **Step 4: Add exact MCP session tools**

```python
@app.tool(description="Acquire or join the FIFO lease for exclusive DayZ mutations.")
async def session_acquire(purpose: str) -> dict[str, Any]:
    if not isinstance(purpose, str) or not purpose.strip():
        raise ToolError("bad_purpose")
    return await runtime.session_acquire(purpose.strip())

@app.tool(description="Wait up to 30 seconds for this client's FIFO ticket.")
async def session_wait(ticket: str, timeout_s: float = 30.0) -> dict[str, Any]:
    if not isinstance(ticket, str) or not ticket:
        raise ToolError("bad_ticket")
    return await runtime.session_wait(ticket, _require_range(timeout_s, 0.0, 30.0, "bad_wait_timeout"))

@app.tool(description="Renew an active lease while exclusive work is in progress.")
async def session_heartbeat(lease_token: str) -> dict[str, Any]:
    if not isinstance(lease_token, str) or not lease_token:
        raise ToolError("bad_lease_token")
    return await runtime.session_heartbeat(lease_token)

@app.tool(description="Release this client's active lease and run bounded cleanup.")
async def session_release(lease_token: str) -> dict[str, Any]:
    if not isinstance(lease_token, str) or not lease_token:
        raise ToolError("bad_lease_token")
    return await runtime.session_release(lease_token)

@app.tool(description="Read redacted daemon/queue/self coordination state.")
async def session_status() -> dict[str, Any]:
    return await runtime.session_status()
```

Validate non-empty purpose/token/ticket before HTTP. Session responses may return the caller's own token; bridge/status/audit never do. Heartbeat is documented as valid only during active exclusive work.

- [ ] **Step 5: Add CLI identity flags without changing embedded default**

Add `--client-platform {claude,codex,unknown}` default `unknown`; `--task-label` default empty. `--client` remains explicit; parser default remains embedded. `build_daemon_argv` does not forward client identity flags to the daemon.

- [ ] **Step 6: Run client/tool suites GREEN**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest tests.test_client_mode tests.test_mcp_tools tests.test_session_http -v`

Expected: PASS; mutating MCP calls in client mode require acquire; embedded historical tool tests still pass through compatibility mode.

- [ ] **Step 7: No-git checkpoint**

Record hashes and exact tool list from a FastMCP test client. Confirm `session_*` are present and `force_release` is absent.

### Task 5: Coordinator integration, zombie prevention and binary E2E

**Files:**
- Create: `tools/tests/test_session_e2e.py`
- Create: `tools/_session_coordination/e2e_agent_sessions.py`
- Modify: `tools/_broker/e2e_daemon.py`

**Interfaces:**
- Consumes public ClientRuntime/session APIs only.
- Produces `_session_coordination/e2e_result.json` with per-gate booleans and evidence; no tokens.

- [ ] **Step 1: Add integration tests for concurrent reads and non-interleaved mutation sequences**

Use two `ClientRuntime` objects, one fake server peer and one fake client peer. A acquires; B reads `query_player_state`; B mutation gets `lease_required`; A sends two mutations; B cannot interleave until A releases. Assert poll order from `GamePeer.commands_seen`.

- [ ] **Step 2: Add cleanup/reconnect/restart tests**

Cover: owner A queued commands cancelled on release; B queue untouched; delivered command is reported pending rather than replayed; `vehicle_release` cleanup appears once when vehicle control was active; a mutation with timeout 400 s pins at most 300 s and its result clears the pin; restarting a daemon invalidates old token/ticket and emits `daemon_restart_invalidated`; result/audit contains no token.

- [ ] **Step 3: Run integration tests RED, then apply the cleanup/restart wiring fixed in Tasks 1-4**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest tests.test_session_e2e -v`

Expected before wiring: failures in cleanup/restart ordering. Modify only the files enumerated in Tasks 1-4 and preserve their published interfaces; do not add lifecycle here.

- [ ] **Step 4: Implement binary E2E gates P1-P7**

```python
result = {
  "P1_one_daemon": False,
  "P2_parallel_reads": False,
  "P3_mutation_rejected_before_poll": False,
  "P4_fifo_a_b_c": False,
  "P5_owner_exit_expiry": False,
  "P6_restart_invalidates": False,
  "P7_no_secrets_and_clean_status": False,
  "overall_pass": False,
}
```

P5 uses the real 120 s contract: kill only the disposable stdio proxy subprocess, never daemon/DayZ, then call `session_wait` in ≤30 s slices until the next ticket becomes active. Hard timeout 150 s. The JSON stores lease IDs, queue positions and durations, never tokens/key.

- [ ] **Step 5: Run Phase A full gate**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t . -v`

Expected: exit 0 and at least the fresh baseline 154 plus all new tests. Then run `.\.venv-mcp\Scripts\python.exe .\_session_coordination\e2e_agent_sessions.py`; expected `overall_pass=true`.

- [ ] **Step 6: Phase A review gate**

Review diff independently for: token leakage, lock held across cleanup I/O, FIFO race, cross-owner cancellation, embedded regression, auto-spawn fallback and daemon parent-death regression. Stop Phase B on any P1/P2 finding.

- [ ] **Step 7: No-git checkpoint**

Record suite count, E2E JSON SHA256 and files changed.

---

## Phase B — Process lifecycle

### Task 6: Windows process-identity viability gate

**Files:**
- Create: `tools/_session_coordination/process-identity-probe.ps1`
- Create runtime artifact only: `tools/_session_coordination/process-identity-probe.json`

**Interfaces:**
- Produces JSON fields `pid`, `creation_time_utc`, `executable_path`, `command_line`, `identity_complete`, `error`.
- Does not terminate any process.

- [ ] **Step 1: Write the read-only probe**

```powershell
param([Parameter(Mandatory=$true)][int]$Pid)
$proc = Get-Process -Id $Pid -ErrorAction Stop
$cim = Get-CimInstance Win32_Process -Filter "ProcessId=$Pid" -ErrorAction Stop
$out = [ordered]@{
  pid = $Pid
  creation_time_utc = $proc.StartTime.ToUniversalTime().ToString('o')
  executable_path = [string]$proc.Path
  command_line = [string]$cim.CommandLine
  identity_complete = $false
  error = $null
}
$out.identity_complete = ($out.creation_time_utc -and $out.executable_path -and $out.command_line)
$out | ConvertTo-Json -Compress
```

Catch all failures and return JSON with `identity_complete=false`; exit 3. Do not weaken `ErrorAction`.

- [ ] **Step 2: Probe the current venv Python process**

Run a disposable Python that sleeps 10 s, probe its PID, then let that exact disposable process exit normally. Expected: `identity_complete=true`, non-empty three identity fields, and command line contains `.venv-mcp`.

- [ ] **Step 3: Probe direct DayZDiag and retail BE wrapper shapes without terminating them**

During an approved maintenance window, launch via the existing safe/manual path, record wrapper/child PIDs, probe each, and close them through their owning session. Store only SHA256 of command line in the result artifact. Expected: every PID that a production launcher will register has complete identity.

- [ ] **Step 4: Enforce the stop condition**

If any required process returns incomplete identity, mark Phase B `BLOCKED_IDENTITY_PROBE`, leave every process untouched, and return to design. Do not substitute `@Mod`, process name or PID-only ownership.

- [ ] **Step 5: No-git checkpoint**

Record probe JSON/hash. The probe script may be committed later if a repo appears; the runtime probe output remains uncommitted evidence.

### Task 7: Persistent run manifest and daemon-owned lifecycle

**Files:**
- Create: `tools/process-guard.ps1`
- Create: `tools/dayz_mcp/process_lifecycle.py`
- Create: `tools/dayz_mcp/lifecycle_cli.py`
- Create: `tools/dayz_mcp/admin_cli.py`
- Create: `tools/tests/test_process_lifecycle.py`
- Create: `tools/tests/test_lifecycle_cli.py`
- Modify: `tools/dayz_mcp/loopback.py`
- Modify: `tools/dayz_mcp/daemon.py`
- Modify: `tools/dayz_mcp/runtime_state.py`

**Interfaces:**
- HTTP: `POST /lifecycle/start|stop|adopt|status`; admin-only `POST /admin/release|reconcile`.
- CLI: `python -m dayz_mcp.lifecycle_cli --keyfile C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.dayz_mcp.key --port 8765 start --request-file request.json`, then the same prefix with `stop --run-id RUN_ID`, `adopt --run-id RUN_ID`, or `status`.
- Env: `DAYZ_MCP_CLIENT_ID_JSON`, `DAYZ_MCP_LEASE_TOKEN`; key remains read from keyfile.

- [ ] **Step 1: Write RED manifest/identity tests**

```python
def test_same_mod_does_not_grant_ownership(self):
    manifest.add(run_a_same_mod)
    result = lifecycle.stop_run(client_b, token_b, run_a_same_mod.run_id)
    self.assertEqual(result["error"], "run_not_adopted")
    self.assertEqual(fake_guard.terminated, [])

def test_pid_reuse_or_fingerprint_mismatch_preserves_process(self):
    fake_guard.snapshot = snapshot_with_new_creation_time
    result = lifecycle.stop_run(owner, token, run_id)
    self.assertEqual(result["error"], "process_identity_mismatch")
    self.assertEqual(fake_guard.terminated, [])
```

Cover unregistered PID, missing cmdline, BE wrapper+child discovery, preflight mismatch killing zero processes, phase-2 race producing explicit partial cleanup, daemon restart manifest reload and release transition to `RUNNING_IDLE` without terminate.

- [ ] **Step 2: Run lifecycle tests RED**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest tests.test_process_lifecycle tests.test_lifecycle_cli -v`

Expected: missing modules.

- [ ] **Step 3: Implement `process-guard.ps1` operations `snapshot`, `discover` and `terminate`**

Input is JSON on stdin, never argv. `snapshot` returns actual identity. `discover` returns the exact root plus allowlisted descendants by `ParentProcessId`, each with complete identity; it never selects by mod. `terminate` opens `Get-Process`, obtains CIM command line, computes SHA256 of normalized executable path and command line, compares PID + creation UTC + both expected hashes, and only then calls `$proc.Kill()` followed by bounded `WaitForExit(5000)`. Any missing field/mismatch returns exit 4 and `terminated=false`. It never kills by name/mod and never invokes tree-kill.

- [ ] **Step 4: Implement exact manifest types and atomic persistence**

```python
RUN_STATES = frozenset({"STARTING","RUNNING","RUNNING_IDLE","STOPPING","EXITED","UNRECONCILED"})

@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    creation_time_utc: str
    executable_sha256: str
    command_line_sha256: str
    role: str

@dataclass
class RunRecord:
    run_id: str
    owner_session_id: str | None
    owner_lease_id: str | None
    state: str
    label: str
    mod: str
    profiles: str
    mission: str
    processes: list[ProcessRecord]
```

`RunManifestStore` uses `RuntimePaths.runs_path` and the same atomic writer. Raw command lines and tokens are never persisted. `release_owner(session_id)` changes matching RUNNING records to RUNNING_IDLE only.

- [ ] **Step 5: Implement lifecycle authorization and one-run invariant**

`start` requires active lease, successful audit append, executable basename in `{DayZDiag_x64.exe, DayZServer_x64.exe, DayZ_x64.exe, DayZ_BE.exe}`, explicit argv list/cwd/role/window style, and either no active run or the same supplied run ID owned by the lease. Daemon launches with `subprocess.Popen` list argv and obtains complete guard identity before recording. For `DayZ_BE.exe`, poll `discover` for at most 15 s and require a complete allowlisted `DayZ_x64.exe` descendant; record wrapper if still alive plus the child. If identity/tree discovery fails, return `identity_unavailable`, mark the run `UNRECONCILED`, audit the incident and use only already-open exact process handles; never scan/kill by name or claim a safe launch. `adopt` only accepts registered RUNNING_IDLE and audit success.

`stop` is two-phase. Phase 1 snapshots and compares every ProcessRecord without terminating; any mismatch aborts the whole stop before the first kill. Phase 2 calls guard `terminate` for each prevalidated record; a new race/mismatch stops further termination, marks `UNRECONCILED` and reports partial cleanup without touching the mismatched PID. `lifecycle/status` is a read that requires identity but no lease. When coordinator release/expiry fires, the daemon cleanup closure calls `RunManifestStore.release_owner(session_id)` after queue cleanup and never terminates the run.

- [ ] **Step 6: Implement lifecycle CLI with env-only credentials**

```python
identity = json.loads(require_env("DAYZ_MCP_CLIENT_ID_JSON"))
lease_token = require_env("DAYZ_MCP_LEASE_TOKEN")
```

Reject if either env var missing; do not echo values. `start --request-file` reads a UTF-8 JSON object, calls daemon and prints only result/run ID. All requests include identity/token in JSON body over loopback. No token appears in `sys.argv`.

- [ ] **Step 7: Implement interactive admin helper; no MCP force tool**

`admin_cli` accepts the same `--keyfile`/`--port` prefix, then requires `sys.stdin.isatty()` and non-empty `--reason`, fetches redacted status, and prompts `Type FORCE {lease_id} to continue:` with the real redacted lease ID substituted. Only exact input sends `/admin/release`. Reconcile similarly prompts the real PID/run ID. Daemon audits `admin_release`/`admin_reconcile` with reason and never exposes these as FastMCP tools.

- [ ] **Step 8: Run lifecycle tests and real disposable-process gate**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest tests.test_process_lifecycle tests.test_lifecycle_cli tests.test_session_e2e -v`

Then start two disposable Python processes with identical labels, register only one, stop its run and assert the other remains alive. Repeat with a forged creation time and assert neither is killed. Expected: PASS, no unregistered termination.

- [ ] **Step 9: Phase B review gate**

Review for PID reuse TOCTOU, partial-stop semantics, raw cmdline/token persistence, run adoption authorization, BE wrapper outcome and release accidentally stopping a run. Any uncertainty is fail-closed and blocks launcher migration.

- [ ] **Step 10: No-git checkpoint**

Record manifest/audit samples after redaction plus test hashes; remove disposable runtime artifacts only after verifying their absolute paths are under the test temp directory.

---

## Phase C — Adoption, instructions and proof

### Task 8: Installer and doctor for Claude + Codex

**Files:**
- Modify: `tools/install-mcp.ps1:1-158`
- Create: `tools/dayz_mcp/doctor.py`
- Create: `tools/tests/test_doctor.py`
- Modify: `tools/README-mcp.md`

**Interfaces:**
- Installer registers Claude with `--client-platform claude`, Codex with `--client-platform codex`.
- Doctor: `python -m dayz_mcp.doctor --json`; exit 0 clean, 1 findings, 2 diagnostic failure.

- [ ] **Step 1: Write RED doctor tests**

Fixtures cover: Codex missing `--client`; Claude correct; two listener PIDs; long-held lease; orphan ticket; unregistered DayZ process; active launcher containing direct `Stop-Process`; clean state. Assert stable finding codes: `CONFIG_EMBEDDED`, `MULTIPLE_LISTENERS`, `LEASE_STALE`, `TICKET_STALE`, `PROCESS_UNREGISTERED`, `LEGACY_BLIND_KILL`.

- [ ] **Step 2: Implement dual registration using verified CLIs**

```powershell
$claudeArgs = $serverArgs + @('--client-platform','claude')
$codexArgs  = $serverArgs + @('--client-platform','codex')
& claude mcp remove dayz-mcp -s user
& claude mcp add dayz-mcp -s user -- $VenvPython @claudeArgs
& (Get-Command codex.cmd).Source mcp remove dayz-mcp
& (Get-Command codex.cmd).Source mcp add dayz-mcp -- $VenvPython @codexArgs
```

PowerShell invocation must assign the command path first: `$CodexCmd=(Get-Command codex.cmd).Source; & $CodexCmd mcp add dayz-mcp -- $VenvPython @codexArgs`. Verify Claude via `claude mcp get dayz-mcp` text and Codex via `codex.cmd mcp get dayz-mcp --json`, checking `transport.args` contains both `--client` and the correct platform. Remove-then-add remains idempotent. `-Register` mutates both; print-only mode prints both commands.

- [ ] **Step 3: Implement doctor checks without extra dependencies**

Use subprocess output from the verified CLIs, daemon `/status`, `orphan_guard.listener_pid_for_port`, manifest revalidation and `Path.rglob("dayz-test.ps1")`. Scan active paths only; ignore `_backups` and the explicitly documented historical embedded harnesses under `DayZ_MCP_dev/tools/run-*.ps1`/`spike0`. Any unreadable config/process becomes a finding, not a pass.

- [ ] **Step 4: Update README with exact startup/health commands**

Document install/register, `session_acquire -> session_wait -> mutate -> session_release -> session_status`, lifecycle env names without sample secrets, doctor exit codes, admin TTY rule and the no-embedded-fallback invariant. Replace the obsolete bare startup example with explicit `--embedded` labeled CI/backcompat.

- [ ] **Step 5: Run installer/doctor tests without mutating live configs**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest tests.test_doctor -v`; then `powershell -NoProfile -ExecutionPolicy Bypass -File .\install-mcp.ps1` without `-Register`. Expected: both registration commands printed; no config mutation.

- [ ] **Step 6: Register in an approved maintenance step and verify effective state**

Run installer with `-Register`; then `claude mcp get dayz-mcp` and `codex.cmd mcp get dayz-mcp --json`. Expected: both `--client`, distinct platform flags, same port/keyfile, no embedded registration. New sessions are required before judging loaded MCP tools.

- [ ] **Step 7: No-git checkpoint**

Record redacted effective args and doctor JSON; never copy the key value.

### Task 9: Canonical protocol, skills and launcher migration

**Files:**
- Create: `C:\Users\guill\ObsidianVault\AI\20_Runbooks\dayz-mcp-agent-session-protocol.md`
- Modify: `C:\Users\guill\ObsidianVault\AI\00_System\workflow.md`
- Modify: `C:\Users\guill\.claude\CLAUDE.md`
- Modify: `C:\Users\guill\.codex\AGENTS.md`
- Modify: `CLAUDE.md`
- Create: `AGENTS.md`
- Modify: `C:\Users\guill\.claude\skills\dayz-mcp-verify\SKILL.md`
- Modify: `C:\Users\guill\.claude\skills\dayz-test-ingame\SKILL.md`
- Modify: `C:\Users\guill\.claude\skills\dayz-test-ingame\templates\dayz-test.ps1`
- Modify active launchers only:
  - `A6_MK47_dev/tools/dayz-test.ps1`
  - `A6_SR2M_dev/tools/dayz-test.ps1`
  - `ExpandedBuilding_dev/tools/dayz-test.ps1`
  - `kt_roadkill_armed_dev/tools/dayz-test.ps1`
  - `LF_VStorage_dev/tools/dayz-test.ps1`
  - `LFGungame_dev/tools/dayz-test.ps1`
  - `LFHeli_dev/tools/dayz-test.ps1`
  - `LFInfectedBig_dev/tools/dayz-test.ps1`
  - `LFPowerGrid_dev/tools/dayz-test.ps1`
  - `LFQuad_dev/tools/dayz-test.ps1`
  - `LFSlidingFloor_dev/tools/dayz-test.ps1`
  - `MERCEDES_AMGLF_dev/tools/dayz-test.ps1`
  - `MilitaryBunker_dev/tools/dayz-test.ps1`
  - `SUB_BRZ_dev/tools/dayz-test.ps1`

**Interfaces:**
- Four-rule L1 block points to one L2 runbook.
- Launchers use lifecycle CLI, require active lease env and explicit run ID for stop/adopt.

- [ ] **Step 1: Read skill authoring constraints before editing skills**

Required at execution time: read `anthropic-skills:skill-conventions` and `superpowers:writing-skills` completely, including directly referenced conventions. Do not copy the full runbook into each skill.

- [ ] **Step 2: Write the canonical runbook with exact protocol**

Include: command classification; acquire/wait examples; FIFO/120 s semantics; heartbeat allowed only during exclusive work; env-only lifecycle handoff using the caller's own `client_identity_json` and `lease_token`; adopt/replace by run ID; release cleanup; admin TTY path; status/doctor; degraded close; residual same-user shell risk. The mandatory closure block is:

```markdown
1. `session_release` si existe lease propio.
2. `session_status`: `own_lease=none`, `own_ticket=none`, `pending_commands=0`.
3. Confirmar `vehicle_control=inactive` o deadman observado.
4. Declarar el run como `RUNNING_IDLE`, `EXITED` o `UNRECONCILED`.
5. Solo escribir HANDOFF si hubo cambio durable, incidente o cleanup degradado.
```

- [ ] **Step 3: Add only the four L1 rules to global/project instructions**

```markdown
## DayZ MCP — sesión compartida
1. Adquirir lease antes de mutar o gestionar procesos.
2. Liberarlo en cuanto termine la secuencia exclusiva.
3. No matar procesos DayZ directamente; usar el lifecycle guard.
4. Ejecutar `session_status` antes del handoff y documentar cierres degradados.
Runbook: `C:\Users\guill\ObsidianVault\AI\20_Runbooks\dayz-mcp-agent-session-protocol.md`.
```

Add the same semantic block, adapted only for heading level, to workflow, Claude global, Codex global, project CLAUDE and new project AGENTS.

- [ ] **Step 4: Remove contradictory skill instructions**

In `dayz-mcp-verify`, replace the 2026-06-24 exclusive-box/kill-residual rule with acquire/release/status and the runbook link. In `dayz-test-ingame`, remove ownership-by-`@Mod`, direct `-Kill` recommendations and `Get-Process | Stop-Process` troubleshooting. State that same-mod sessions are indistinguishable without run ID and direct kills are forbidden.

- [ ] **Step 5: Replace canonical template lifecycle functions**

Add `-RunId` parameter. `Stop-DayZ` requires non-empty RunId and calls `python -m dayz_mcp.lifecycle_cli stop --run-id $RunId`. `Start-Server` creates a UTF-8 request JSON, calls lifecycle `start`, stores returned run ID in `$script:RunId`; `Start-Client/Offline` add to that exact run. Always delete request temp files in `finally`. Remove every direct DayZ/BattlEye `Stop-Process` and every ownership inference by mod.

- [ ] **Step 6: Apply the same bounded replacement to 14 active copies**

Modify only parameter block and lifecycle helper/start/stop functions. Preserve each project's build, PBO, mission, VPP, retail/diag and storage behavior. Do not touch `_backups`. After each file, parse with PowerShell AST:

Run for each file in the enumerated list: `powershell -NoProfile -Command '$p=$args[0];$e=$null;[System.Management.Automation.Language.Parser]::ParseFile($p,[ref]$null,[ref]$e)>$null;if($e){$e;exit 1}' -- 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\SUB_BRZ_dev\tools\dayz-test.ps1'`; substitute each other exact enumerated path in turn.

Expected: exit 0.

- [ ] **Step 7: Prove no active launcher has a bypass**

Run doctor JSON, then build `$files` from the 14 exact paths enumerated in this task plus the canonical template and execute `& rg -n 'Stop-Process|Get-Process.+Stop-Process|Get-CimInstance Win32_Process' @files`.

Expected: no match in active launcher/template lifecycle code. Historical embedded harnesses may be reported only under the documented exception list, never as clean production paths.

- [ ] **Step 8: Skill/runbook scenario tests**

Run three scripted walkthroughs against a fake daemon: A acquires-launches-releases leaving RUNNING_IDLE; B adopts-stops exact run; C tries same mod with foreign run ID and is rejected. Inspect outputs for forbidden advice (`kill`, `Stop-Process`, ownership by mod) and token disclosure.

- [ ] **Step 9: No-git checkpoint**

Record every instruction/skill/launcher path and parser/doctor result. Do not edit unrelated project code.

### Task 10: Full verification, real four-agent gate and durable closeout

**Files:**
- Modify: `tools/_session_coordination/e2e_agent_sessions.py`
- Modify: `product-spec.md`
- Modify: `HANDOFF.md`
- Modify/create project memory as evidence requires under `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\`
- Create: `C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-07-14-DayZ_MCP-agent-session-coordination.md`

**Interfaces:**
- Produces offline verdict JSON and real-gate evidence; updates H1-H8 only to the level actually proved.

- [ ] **Step 1: Run complete offline suite**

Run: `.\.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t . -v`

Expected: exit 0, count ≥154, no skip for new coordination/lifecycle/doctor tests. A `0 file(s)` or zero-test result is failure.

- [ ] **Step 2: Run binary broker/session E2E**

Run: `.\.venv-mcp\Scripts\python.exe .\_session_coordination\e2e_agent_sessions.py`

Expected: P1-P7 true, `overall_pass=true`, exactly one listener PID, final `own_lease=none`, `own_ticket=none`, `pending_commands=0`, and no secret match in JSONL/result.

- [ ] **Step 3: Run doctor on effective live setup**

Run: `.\.venv-mcp\Scripts\python.exe -m dayz_mcp.doctor --json`

Expected: exit 0 after configs and launcher migration. Any unregistered DayZ process or blind-kill script blocks the real gate.

- [ ] **Step 4: Execute H8 real combined gate**

Open two fresh Claude sessions and two fresh Codex tasks, all showing the new `session_*` tools. Against one game: parallel pure reads; A lease; B/C FIFO mutations; normal release; kill only A's disposable stdio proxy and wait 120 s; B/C advance; registered run remains; next owner adopts or replaces exact run; collect audit. Never kill daemon/game to simulate owner loss.

- [ ] **Step 5: Verify process safety adversarially**

Create two registered disposable runs with the same mod metadata where only one is adopted; attempt stop with the other's run ID, a reused/fake PID, altered creation time and altered fingerprint. Expected: all mismatches preserved and audited; only fully matching adopted record terminates.

- [ ] **Step 6: Update DPF conservatively**

Mark H1-H7 `✓ offline` only for properties proved offline. Mark H8 `✓ in-game` only if the real four-agent gate completed with artifacts; otherwise `[verify] offline ✓; falta gate real`. Do not convert suite success into in-game confirmation.

- [ ] **Step 7: Independent double review**

Codex and Claude review independently for concurrency races, auth fail-open, cleanup, persistence corruption, PID reuse, config drift, launcher bypass, secret leakage and scope creep. Resolve P1/P2 findings and rerun affected gates; overlapping findings are not deduplicated away.

- [ ] **Step 8: Durable memory and handoff**

Update project brief/verified APIs/assumptions/validation matrix/bug ledger/decision mirror as warranted. HANDOFF records approved plan, implementation state, exact validation level and residual same-user shell risk. Session handoff includes changed files, commands/results, audit/verdict paths, review findings, no-git reason and final coordination status.

- [ ] **Step 9: Final closure check**

Call `session_status` from the implementing agent and record `own_lease=none`, `own_ticket=none`, `pending_commands=0`. If cleanup is degraded, do not claim complete; document the incident and leave H8 unconfirmed.

## Approved-Spec Coverage Checklist

| Approved design surface | Implementation tasks | Proof gate |
|---|---|---|
| Daemon authority, client-only production topology and embedded exceptions | Tasks 3, 4, 8 | Binary E2E plus doctor effective-config checks |
| Stable caller identity and fail-closed authentication | Tasks 1, 3, 4 | Unit auth negatives plus forged-identity E2E |
| Parallel reads versus lease-required mutations | Tasks 1, 3, 4 | Command classification tests and concurrent E2E |
| Strict FIFO, 120 s inactivity expiry and bounded waits | Tasks 1, 3, 5 | Fake-clock unit tests and zombie-owner E2E |
| Owner-scoped cleanup, `vehicle_release` and restart recovery | Tasks 2, 3, 5 | Cleanup isolation and daemon-restart tests |
| PID-safe lifecycle, adoption and no ownership by mod | Tasks 6, 7, 9 | Identity viability gate and adversarial process tests |
| Atomic persistence, redacted JSONL audit and rotation | Tasks 2, 7, 8 | Restart/corruption tests, secret scan and doctor |
| Durable protocol in global/project instructions, skills and launchers | Tasks 8, 9 | Parser, launcher sweep and three walkthroughs |
| Handoff discipline and four-agent acceptance | Task 10 | Offline verdict, real H8 artifacts and final `session_status` |
| Residual same-Windows-user shell risk | Tasks 9, 10 | Explicit runbook warning and conservative handoff |

## Execution Order and Stop Gates

1. Complete Phase A and its review before touching process lifecycle.
2. Task 6 `identity_complete=true` is mandatory before Task 7.
3. Task 7 adversarial process tests are mandatory before modifying any launcher.
4. Installer/doctor must be green before migrating global instructions.
5. Offline green is not H8; the four-agent one-game gate is separate.
6. Any mismatch between this plan and a verified current signature stops execution and returns to plan review; do not improvise a replacement API.
