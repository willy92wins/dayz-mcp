"""The slow tier of the suite, and the switch that leaves it out.

The whole suite runs by default. With DAYZ_MCP_FAST_TESTS=1 every test marked
@slow_test skips with its reason named, which leaves a fast tier for the
edit-run loop: about 80 s of the 420 s the whole suite took on 2026-09-28. Any
other value, or none, runs everything, so a typo costs time and never coverage.

A slow test is as required as any other; the split only decides when it runs.
CI runs the fast tier and the whole suite in separate jobs on every pull
request, and the whole suite on main.

Membership came from that run's per-test durations (unittest --durations 0):
every test that took 0.2 s or more, most of them because they start real
processes or wait on real time, plus a few that do either and have failed
under load in CI however long they took. A new test that starts real processes
or waits on real time belongs here too.
"""

import os
import unittest

FAST_TIER_ONLY = os.environ.get("DAYZ_MCP_FAST_TESTS") == "1"

slow_test = unittest.skipIf(
    FAST_TIER_ONLY,
    "DAYZ_MCP_FAST_TESTS=1 leaves out the slow tier; the whole suite runs it",
)
