# gpt-6.1-sol review, g5r9_selection, round 3 (unedited)

VERDICT: APPROVED

## FINDINGS

None. Previous F1 is closed; no new P1/P2 defect was found within the bounded round 3 scope.

Selection validation now runs first at `tools/mcp_capture.py:1509` and `tools/mcp_capture.py:1609`, before backend calls, window accreditation, crop checks, or later errors.

For both specified backend scenarios, both public entry points return:

```json
{"isError": true, "error": "invalid_instance_token", "selection_error": "invalid_instance_token"}
```

The backend is never called. The two new regressions pass on this tree and fail on reconstructed round 2 code, with filesystem I/O virtualized in memory. Independent probes reproduce round 2’s image-success and `frame_client_area_unverified` failures.

## SPEC COVERAGE

| Bullet | Status | Evidence in this tree |
|---|---|---|
| M2 — shared entry validation | done | `knowledge_pack.py:335`, `stdio_bridge.py:441`; preserved |
| M3 — conflicting environment rejection | done | `server.py:7333`; preserved |
| M4 — doctor selects instance/game path | done | `doctor.py:1447`; preserved |
| M5 — selection failure reported without writes | done | `mcp_capture.py:562`, `:1509`, `:1609` |
| A4 — fallback audit uses bound daemon instance | done | `daemon.py:1435`; preserved |
| A5 — shared exec-enforce audit filename | done | `daemon.py:343`, `server.py:859`; preserved |
| Regressions, including round 3 scenarios | done | `tests/test_g5_r9_selection.py:259` and `:262`; both detect previous behavior |

Reconstructing the round 2 production files from its patch confirmed that round 3 changed only the capture validation ordering and its documentation; the other batch fixes remain intact.

## GATE GAP

The in-memory regressions cannot establish real Windows backend or filesystem integration correctness. Backend launch failures, actual sidecar/full-resolution permissions, and concurrent instance-context changes remain outside these probes.

Additional read-only probes passed: eight selection-error precedence cases and four valid-bound-selector delegation cases.

## PREMISE

No material premise error found. The supplied gate results are external orchestrator evidence; they are not presented as locally reproduced results.

## NOT VERIFIED

- Full local unittest execution: the sandbox prevents temporary-directory creation; the default Python also lacks Pillow and AnyIO.
- The two regression reruns used the project venv with temporary-file/image I/O virtualized in memory.
- No live DayZ capture, daemon operation, real filesystem persistence, or local Python 3.11 rerun.
- No files were modified.

