"""The pause rule after a /session/wait 202 that carries session_wait_quota (#317).

ControlClient.session_acquire_wait (tools/dayz_mcp/control_client.py) and the
G0 gate's copy (tools/g0_abba_gate.py) must both follow it: only a string
quota in {"duplicate", "full"} pauses; the pause is min(1s, retry_after_s)
when retry_after_s is a finite positive int or float (not bool), and 0.05s
otherwise; and the pause is cut to what is left of the caller's deadline when
the answer arrives, never below zero. test_client_acquire_wait and
test_g0_abba_verdict run every case on a virtual clock.
"""

from __future__ import annotations

import math

# A field the answer leaves out, as the daemon's ordinary queued answer does.
ABSENT = object()

# Every case needs one or two /session/wait calls. On a virtual clock a loop
# that neither pauses nor sees time pass would call it forever, so the fakes
# fail past this many calls.
MAX_VIRTUAL_WAITS = 8


def queued(quota: object, retry_after: object) -> dict[str, object]:
    """A queued /session/wait answer, without the fields set to ABSENT."""
    answer: dict[str, object] = {"status": "queued", "position": 1}
    if quota is not ABSENT:
        answer["session_wait_quota"] = quota
    if retry_after is not ABSENT:
        answer["retry_after_s"] = retry_after
    return answer


# (label, session_wait_quota, retry_after_s, pauses asked for). Run with a
# budget the pause never reaches, so the deadline does not cut it.
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
    ("null retry_after_s", "duplicate", None, [0.05]),
    ("no retry_after_s field", "duplicate", ABSENT, [0.05]),
    ("the ordinary queued answer, with neither field", ABSENT, ABSENT, []),
    ("a null quota", None, 30, []),
    ("a list is not a quota", ["duplicate"], 30, []),
    ("a JSON object is not a quota", {"duplicate": True}, 30, []),
    ("an unknown quota", "other", 30, []),
)

# (label, max_wait_s, transport_s, quota, retry_after_s, pauses asked for,
# virtual time when the wait times out). The virtual clock starts at 1024.0
# and every value is dyadic, so the arithmetic is exact. Each case ends in
# session_wait_timeout after one /session/wait.
DEADLINE_CASES = (
    (
        "the 1s cap is cut to what is left after the answer",
        0.375, 0.25, "duplicate", 30, [0.125], 1024.375,
    ),
    (
        "the 0.05s fallback is cut too",
        0.03125, 0.0, "full", 0.0, [0.03125], 1024.03125,
    ),
    (
        "an answer after the deadline asks for no pause",
        0.375, 0.5, "duplicate", 30, [0.0], 1024.5,
    ),
)
