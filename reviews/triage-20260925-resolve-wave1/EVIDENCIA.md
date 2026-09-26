# EVIDENCIA - resolve wave1 PR #92 (2026-09-25)

## Base
- PR #92, squash tip `72f63549f2337764a73b34c6e6a935eed0697c3b`.
- Resolutions are limited to the three authorized mailbox rows in this wave.

## Product evidence
PR #92 changes:
- `tools/dayz_mcp/control_client.py`
- `tools/dayz_mcp/doctor.py`
- `tools/dayz_mcp/native_bundle.py`
- `tools/dayz_mcp/process_lifecycle.py`
- `tools/tests/test_control_client.py`
- `tools/tests/test_native_source_seal.py`
- `tools/tests/test_reload_lease_recovery.py`

## Resolution scope
| id | scope |
|---|---|
| `fb-20260924-003722-0c27` | reload lease recovery |
| `fb-20260924-010108-b1a7` | hot-reload ghost lease, **lease slice only**; no catalogue/e7ef claim |
| `fb-20260920-230648-99db` | stale launcher source-pin gate |

## Method
`tools/dayz_mcp/inbox.append_resolution` with
`evidence_ref=reviews/triage-20260925-resolve-wave1/EVIDENCIA.md`.
No product code change was made by this resolve-only hop.