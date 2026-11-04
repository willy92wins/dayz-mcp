"""The pause rule after a /session/wait 202 that carries session_wait_quota (#317).

ControlClient.session_acquire_wait (tools/dayz_mcp/control_client.py) and the
G0 gate's copy (tools/g0_abba_gate.py) must both follow it: only a string
quota in {"duplicate", "full"} pauses; the pause is min(1s, retry_after_s)
when retry_after_s is a finite positive int or float (not bool), and 0.05s
otherwise. test_client_acquire_wait and test_g0_abba_verdict run each case
on a virtual clock with a 64s budget, so the deadline never cuts the pause.
"""

from __future__ import annotations

import math

# (label, session_wait_quota, retry_after_s, pauses asked for)
QUOTA_PAUSE_CASES = (
    ("duplicate, 30s: the 1s cap", "duplicate", 30, [1.0]),
    ("full, 30s: the 1s cap", "full", 30, [1.0]),
    ("a fraction under the cap", "duplicate", 0.125, [0.125]),
    ("full with 0, as the daemon sends it", "full", 0.0, [0.05]),
    ("an int too large for a float", "duplicate", 10**400, [0.05]),
    ("inf", "duplicate", math.inf, [0.05]),
    ("nan", "duplicate", math.nan, [0.05]),
    ("negative", "duplicate", -1, [0.05]),
    ("bool", "duplicate", True, [0.05]),
    ("numeric string", "duplicate", "0.25", [0.05]),
    ("missing", "duplicate", None, [0.05]),
    ("a list is not a quota", ["duplicate"], 30, []),
    ("an unknown quota", "other", 30, []),
)
