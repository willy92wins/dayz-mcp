# gpt-6.1-sol review, b2_c8c7, round 3 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — Public failure normalization discards proven successful-run correlation.**

Locations: `tools/dayz_mcp/dayz_test_tool.py:1835`, `:1850`, `:1856`.

Executable scenario, reproduced against the actual `_execute_request`:

- Worker returns a valid successful terminal naming `11111111-1111-4111-8111-111111111111`.
- Lifecycle status contains that exact run with `storage_rotated=true`, backup `storage_1.modset-own`, and the reset notice.
- Mode is `all`; server PID is alive and client PID is dead.

Normalization changes `terminal.ok` to false with `client_dead_after_ack`. Successful terminals have no `attempt_run_id`, so the subsequent correlation branch discards the known run.

Actual output:

```json
{
  "status": "failed",
  "error_code": "client_dead_after_ack",
  "run_id": "11111111-1111-4111-8111-111111111111",
  "storage_rotated": null,
  "storage_backup": null,
  "storage_reset_notice": null
}
```

Expected storage fields: `true`, `storage_1.modset-own`, `mission_world_and_character_reset`.

**Suggested fix:** capture correlation from the validated original terminal before modifying its success status. Preserve the null fallback for genuine older-worker failure terminals.

**F2 — P2 — The outer worker handler loses known attempt context.**

Locations: `tools/dayz_mcp/dayz_test_worker.py:793`, `:798`, `:852`.

The outer handler forwards only `created_run_id`. It omits a supplied target ID when adoption fails and cannot retain an allocated operation ID after `_start` returns.

Two executable scenarios were reproduced using the actual worker and terminal writer:

1. **Known target disappears.** Request `mode="client"` with run ID `11111111-1111-4111-8111-111111111111`. That running target has a persisted true rotation. Broker adoption returns:

   ```json
   {"ok": false, "error": "run_not_adoptable"}
   ```

   Actual terminal:

   ```json
   {
     "cleanup_degraded": false,
     "error_code": "worker_failed",
     "exit_code": 2,
     "ok": false,
     "run_id": null
   }
   ```

   `attempt_run_id` is absent despite the worker having targeted that run. Passing this terminal through `_execute_request` returns all three storage fields as null. The worker must transmit the known target independently of cleanup `run_id`.

2. **Allocated operation disappears after acknowledgement.** Request `mode="all"`. Server start allocates operation `87654321-4321-4321-8321-ba0987654321`; start and acknowledgement succeed; readiness returns `ReadinessResult(False, "readiness_timeout")`; cleanup succeeds.

   Actual failure terminal contains `attempt_run_id` but omits `launch_operation_id`. The directive requires the allocated operation ID, and its absence bypasses operation comparison at `tools/dayz_mcp/dayz_test_tool.py:1911`.

**Suggested fix:** retain complete attempt context throughout execution: supplied/created target ID and any allocated launch operation ID. Keep existing cleanup `run_id` semantics unchanged.

## SPEC COVERAGE

| Specification requirement | Assessment |
|---|---|
| Confirmed reset visibility defect | **Done** — rotation facts now reach public results on covered paths. |
| Durable transport independent of audit/transient dictionaries | **Done** — run metadata and persisted observations. |
| Carry rotation into provisional run before spawning | **Done** — `process_lifecycle.py:3897`, `:3904`. |
| Optional validated metadata; cloning, settlement, reload; legacy unknown | **Done** — source inspection and supplied regression evidence. |
| Both status projections; exact run extraction | **Done**, with missing correlation paths in F1/F2. |
| Always include three fields; null unknown, false measured nonrotation | **Wrong in known cases** — keys always exist, but F1/F2 turn available observations into null. |
| Preserve reset facts on later failures; fetch before normalization loses identity | **Wrong** — F1/F2. |
| Retain history after acknowledgement; recover completed journal on retry | **Done** on lifecycle side. |
| No acknowledgement/marker claim proving CE restoration | **Done**. |
| Legacy readability; downgrade preserves storage/backups/journals | **Done by inspection**; downgrade execution remains unverified. |
| Required regression scenarios | **Done** for named scenarios; additional failure paths remain uncovered. |
| H11 accreditation left explicitly unresolved | **Done**. |

Round 3 directive coverage:

- Snapshot set-difference inference removed: **done**.
- Optional worker correlation fields on applicable failures: **wrong/incomplete**, F2.
- Terminal validation and regenerated source lock: **done**.
- Three specified schedules plus old-worker fallback: **done**, independently exercised in memory.
- Report reload correction: **done**; its universal correlation claims still exceed implementation behavior.

Previous F2/F3 fixes remain present; I found no regression in those fixes.

## GATE GAP

The new public tests fabricate worker terminals at `tools/tests/test_storage_reset_visibility.py:217` and always use `public_mode="server"` at `:244`. They cannot detect either lost context in real worker failure paths or the client-death normalization in F1.

Local verification passed: **42 worker/app tests**, packaged-source lock check, seven terminal-validation cases, and the correlation schedules. The supplied orchestrator gate remains separate evidence.

## PREMISE

The round 3 boundary correction is valid. Updating sealed sources and their lock does not update the existing launcher binary; older-worker failure results remaining unknown until resealing is explicitly allowed.

The fast-tier gate reports two baseline failures, not a completely green suite. H11 accreditation remains outside this implementation.

## NOT VERIFIED

- In-game behavior, native launcher rebuild/reseal, or deployed artifacts.
- Independent full fast-tier execution.
- Filesystem-based storage tests: temporary-directory creation is blocked by the read-only sandbox.
- `test_dayz_test_tool`: local interpreter lacks `anyio`.
- Executed downgrade/reload compatibility.

No files were modified.

