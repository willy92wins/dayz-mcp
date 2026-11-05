"""Run one shard like tests._shard, recording each test's start/stop and an
in-process scheduling probe (temporary diagnostic, not part of the suite).

usage (cwd = tools): python -B ../.github/diag/diag_run.py <i>/<n> <out_dir>
"""
from __future__ import annotations

import gc
import json
import os
import sys
import threading
import time
import unittest

# Bound before any test patches time.sleep / time.perf_counter.
_sleep = time.sleep
_perf = time.perf_counter
_now = time.time

sys.path[0] = os.getcwd()
from tests import _shard  # noqa: E402

spec, out_dir = sys.argv[1], sys.argv[2]
os.makedirs(out_dir, exist_ok=True)
tests_fh = open(os.path.join(out_dir, "tests.jsonl"), "w")
stop = threading.Event()


def probe() -> None:
    # Same probe as sampler.py, but inside the test process: a late wake here
    # includes GIL contention from threads the tests left running.
    with open(os.path.join(out_dir, "probe_inproc.jsonl"), "w") as fh:
        while not stop.is_set():
            t0 = _perf()
            _sleep(0.002)
            d = _perf() - t0
            if d > 0.05:
                fh.write(json.dumps({"t": _now(), "d": round(d, 4)}) + "\n")
                fh.flush()


gc_fh = open(os.path.join(out_dir, "gc.jsonl"), "w")
_gc_start = {}


def gc_cb(phase, info):
    # Every collection of generation 2, and any collection over 20 ms.
    if phase == "start":
        _gc_start["t"] = _perf()
        _gc_start["w"] = _now()
        return
    d = _perf() - _gc_start.get("t", _perf())
    if info.get("generation") == 2 or d > 0.02:
        gc_fh.write(json.dumps({"t": _gc_start.get("w"), "d": round(d, 4),
                                "gen": info.get("generation"),
                                "collected": info.get("collected")}) + "\n")
        gc_fh.flush()


gc.callbacks.append(gc_cb)


class Result(unittest.TextTestResult):
    def startTest(self, test):  # noqa: N802
        self._diag = {"id": test.id(), "t0": _now(), "threads0": threading.active_count(),
                      "outcome": "ok"}
        super().startTest(test)

    def _mark(self, outcome, test, err=None):
        d = getattr(self, "_diag", None)
        if d is not None and d["id"] == test.id():
            d["outcome"] = outcome
            if err is not None:
                d["err"] = str(err[1])[:300]

    def addFailure(self, test, err):  # noqa: N802
        self._mark("FAIL", test, err)
        super().addFailure(test, err)

    def addError(self, test, err):  # noqa: N802
        self._mark("ERROR", test, err)
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):  # noqa: N802
        if err is not None:
            self._mark("FAIL", test, err)
        super().addSubTest(test, subtest, err)

    def addSkip(self, test, reason):  # noqa: N802
        self._mark("skip", test)
        super().addSkip(test, reason)

    def stopTest(self, test):  # noqa: N802
        super().stopTest(test)
        d = self._diag
        d["t1"] = _now()
        d["threads1"] = threading.active_count()
        tests_fh.write(json.dumps(d) + "\n")
        tests_fh.flush()


class Runner(unittest.TextTestRunner):
    resultclass = Result


unittest.TextTestRunner = Runner
threading.Thread(target=probe, daemon=True, name="diag-probe").start()
try:
    code = _shard.main([spec])
finally:
    stop.set()
    gc.callbacks.remove(gc_cb)
    with open(os.path.join(out_dir, "gc_end.json"), "w") as fh:
        json.dump({"objects": len(gc.get_objects()), "counts": gc.get_count(),
                   "threshold": gc.get_threshold(), "stats": gc.get_stats()}, fh)
    names = sorted(t.name for t in threading.enumerate())
    with open(os.path.join(out_dir, "threads_at_end.json"), "w") as fh:
        json.dump(names, fh)
sys.exit(code)
