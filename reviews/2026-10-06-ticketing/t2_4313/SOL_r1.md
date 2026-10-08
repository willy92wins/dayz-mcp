# gpt-6.1-sol review, t2_4313, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — Regression guard swallows its own failures.**  
`tools/tests/test_capture_frame_state_isolation.py:131-148`

The spy asserts isolation inside `_write_frame_state`, but `tools/mcp_capture.py:908-911` catches that `AssertionError` and returns `state_backend="unavailable"`. The guard subsequently checks only that some destinations were recorded and that sentinel files do not exist. Because the assertion prevented the write, those checks can pass despite escaping destinations.

**Executable failure scenario:** from `tools/`, run this through the project Python with `-B -`:

```python
import os
os.environ["DAYZ_MCP_FAST_TESTS"] = "1"

import unittest
from unittest import mock
from tests import test_mcp_capture as capture
from tests import test_tool_description_caveats as caveats
from tests import test_capture_frame_state_isolation as guard

with mock.patch.object(capture, "isolate_capture_frame_state", lambda case: None), \
     mock.patch.object(caveats, "isolate_capture_frame_state", lambda case: None):
    suite = unittest.TestLoader().loadTestsFromTestCase(
        guard.CaptureFrameStateIsolationTest
    )
    unittest.TextTestRunner().run(suite)
```

This removes fixture protection in memory. The guard should fail for inherited and fallback destinations; its swallowed assertions permit false success.

**Measured evidence:** a filesystem-free probe using the actual guard and production exception handler returned `OK` for both override modes, despite **four escaping writer attempts**. All four failures appeared only as `state_error="AssertionError: …"`.

**Suggested fix:** record violations in the spy, prevent unsafe writes, and assert that the violation list is empty **after** capture calls return. Add a negative fixture-removal test that must fail.

## SPEC COVERAGE

| Specification bullet | Status |
|---|---|
| Reusable per-test fixture overrides the variable, restores the environment, and cleans through `addCleanup` | **Done** — `_frame_state_isolation.py:36-44`. |
| Apply to capturing classes and `CaptureFullresCropBehaviorTest`; preserve existing isolation | **Done** — all four classes covered; existing overrides retained. |
| Sentinel guard records all writer destinations and requires isolation | **Wrong** — destinations are recorded, but isolation failures can be swallowed (F1). |
| Verify module and discovery loading, with dangerous inherited override and without override | **Wrong overall** — both loading modes and environment cases are present; their isolation verdict is unreliable. |
| Preserve production fallback and real-profile records | **Done by inspection** — `mcp_capture.py` is SHA256-identical to `base_v11`; no profile cleanup added. |

## GATE GAP

The positive gate exercises correctly installed fixtures. It does not establish that the guard rejects missing isolation. F1 lets an escaping destination produce a successful test result while production converts the assertion into degraded capture metadata.

## PREMISE

The core premise is correct: production resolves the environment override before the `LOCALAPPDATA` fallback and writes/prunes the sidecar. The existing `b0d9` isolation remains unchanged.

The report’s claimed negative behavior was not demonstrated; the guard’s exception placement undermines that claim.

## NOT VERIFIED

- Full targeted tests could not complete in this read-only sandbox: Python 3.11 reported **41 tests, 20 errors, 9 skipped**, with errors caused by unavailable writable temporary directories. These are environment limitations.
- Module/discovery identity comparison passed.
- The full fixture-removal mutation above was not run against real temporary files; the narrower in-memory probe confirmed the swallowing mechanism.
- No in-game execution, PBO build, or reseal. No files modified.