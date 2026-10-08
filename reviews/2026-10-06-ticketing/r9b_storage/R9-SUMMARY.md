# r9b (mission storage rotation recovery, inbox c5ac): design, review loop, DZ-R9 step 6 and the owner's decision

## Origin
The c8c7 DZ-R9 audit found D1-D5 in the rotation recovery (`dayz_test_storage.py`). D1 was P1: a crash between the storage rename and the marker publish let a later launch with another modset reuse a world written by the first one. Three implementation rounds did not converge. gpt-6.1-sol then designed the state machine of the rotation transaction (`DESIGN.md`; binding spec `SPEC_R9B_IMPL.md`, which adds the orchestrator's two decisions on the design's open questions).

## Review loop (`loop/`)
- Grok 4.7 implemented and gpt-6.1-sol reviewed every round. Round 3 closed the production findings.
- Round 4 (GLM-5.3-Flash) fixed the model oracle. gpt-6.1-sol **approved**; it left two P3 in the test oracle.
- The gate is `tests/test_storage_rotation_model.py`: an in-memory filesystem with a fault at every I/O step, the producer and two recoveries driven to a fixed point, and the transaction's invariants asserted after every cut.

## DZ-R9 step 6 (`r9/`)
- **Who looked:**
  - gpt-6.1-sol ran the mechanical re-check (`MECHANICAL.md`).
  - Two GLM angle auditors: D on persistence on a real disk (`AUDIT_D.md`), E on recovery paths and the operator (`AUDIT_E.md`).
  - A fresh gpt-6.1-sol session verified the seven claims the orchestrator had not sampled, from the bare claims alone (`VERIFY.md`). It partly refuted three of them.
  - The orchestrator self-sampled M1 and M2. Both were real.
- **Table** (`R9-TABLE.md`): M1 P1; M2, M3, D1 P2; D2, D3, E2 and E1+E3 P3; M4 is an improvement.
- **Already on main before this change:**
  - M1: the case-sensitive journal scan, `startswith(JOURNAL_PREFIX)` in `_active_journals`.
  - M2: `storage_1` checked with `os.path.isdir`.
  - D3: `_parse_json` without `RecursionError`.
  - D1: the same `derived_txid` re-rotation.
  - E2, E1 and E3: the generic refusal code and missing remedy text.
- **New with this change:** M3. After a crash recovery that reseals the journal's seal to the launch's seal, a retried launch records `storage_rotated=false`. The world is correct. This is a reporting gap.

## Fix rounds for the step-6 findings (`fix_rounds/`)
- gpt-6.1-sol wrote the binding fix specification (`r9/FIX_SPEC.md`, SPEC: READY).
- Round 5 (GLM) stopped mid-way: a syntax error, no tests and no lock.
- Round 6 (a fresh Grok session) delivered all eight production changes, and gpt-6.1-sol confirmed each works in independent checks. It still lacked the required regression tests, the model-oracle extensions and the migration of the existing envelope tests for the new E2 field.
- That used the round budget.

## Owner decision (2026-10-07)
Merge the round-4 tree, the approved and model-checked c5ac fix. It is strictly better than main: it removes the P1 world-poisoning path, and the step-6 findings other than M3 already exist on main.

The step-6 findings go to a separate batch. It starts from round 6's production changes and is scoped to their tests and oracle extensions.

## Local checks
Composed on main `33077dd`, which includes g5 and g5fix:
- Batch modules (`test_storage_rotation_model`, `test_dayz_test_storage`, `test_storage_reset_visibility`): 76 tests, OK.
- Fast tier: 6022 tests, OK (513 skipped).
- `write_packaged_modules_lock.py --check`: ok.

## Not done here (deployment)
- **Launcher reseal:** `dayz_test_storage.py` is sealed. One reseal will cover g5fix and this change.
- **In game:** a rotation on a modset change, and the rotation report.
