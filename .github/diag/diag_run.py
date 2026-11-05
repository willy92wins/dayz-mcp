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
    import collections
    import ctypes
    from ctypes import wintypes

    class _PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    k32 = ctypes.WinDLL("kernel32")
    psapi = ctypes.WinDLL("psapi")
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
    psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    with open(os.path.join(out_dir, "mem_end.json"), "w") as fh:
        json.dump({"peak_working_set_mb": round(pmc.PeakWorkingSetSize / 2**20, 1),
                   "peak_private_mb": round(pmc.PeakPagefileUsage / 2**20, 1),
                   "private_mb": round(pmc.PagefileUsage / 2**20, 1)}, fh)

    before = len(gc.get_objects())
    top_before = collections.Counter(type(o).__name__ for o in gc.get_objects()).most_common(15)
    frozen = gc.get_freeze_count()
    gc.unfreeze()
    t_collect = _perf()
    collected = gc.collect()
    t_collect = _perf() - t_collect
    after = len(gc.get_objects())
    top_after = collections.Counter(type(o).__name__ for o in gc.get_objects()).most_common(15)
    with open(os.path.join(out_dir, "gc_end.json"), "w") as fh:
        json.dump({"objects": before, "frozen": frozen, "collected_at_end": collected,
                   "objects_after_collect": after, "final_collect_s": round(t_collect, 3),
                   "top_before": top_before, "top_after": top_after, "counts": gc.get_count(),
                   "threshold": gc.get_threshold(), "stats": gc.get_stats()}, fh)
    names = sorted(t.name for t in threading.enumerate())
    with open(os.path.join(out_dir, "threads_at_end.json"), "w") as fh:
        json.dump(names, fh)
sys.exit(code)
