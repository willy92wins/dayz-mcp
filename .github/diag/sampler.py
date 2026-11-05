"""Runner-load sampler for the diagnostic job (temporary, not part of the suite).

Every 1 s: per-process CPU times (user+system), pid, ppid, name, create
time. Run with a third argument `probe`, it instead measures OS scheduling
delay of its own process: it sleeps
2 ms and records every wake that came more than 50 ms late.

usage: python sampler.py <out_dir> <stop_file>
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

import psutil

out_dir, stop_file = sys.argv[1], sys.argv[2]
os.makedirs(out_dir, exist_ok=True)


def probe() -> None:
    with open(os.path.join(out_dir, "probe_os.jsonl"), "w") as fh:
        window_start = time.time()
        window_max = 0.0
        n = 0
        while not os.path.exists(stop_file):
            t0 = time.perf_counter()
            time.sleep(0.002)
            d = time.perf_counter() - t0
            n += 1
            window_max = max(window_max, d)
            if d > 0.05:
                fh.write(json.dumps({"t": time.time(), "d": round(d, 4)}) + "\n")
            if time.time() - window_start >= 10.0:
                fh.write(json.dumps({"t": time.time(), "window_max": round(window_max, 4), "n": n}) + "\n")
                fh.flush()
                window_start, window_max, n = time.time(), 0.0, 0


if len(sys.argv) > 3 and sys.argv[3] == "probe":
    # Own process: the probe must not share a GIL with the psutil walk.
    probe()
    sys.exit(0)
with open(os.path.join(out_dir, "procs.jsonl"), "w") as fh:
    while not os.path.exists(stop_file):
        t = time.time()
        rows = []
        for p in psutil.process_iter(["pid", "ppid", "name", "create_time", "cpu_times"]):
            info = p.info
            ct = info.get("cpu_times")
            if ct is None:
                continue
            rows.append([info["pid"], info["ppid"], info["name"], round(info["create_time"] or 0, 2),
                         round(ct.user + ct.system, 3)])
        fh.write(json.dumps({"t": t, "cpu_total": psutil.cpu_percent(None), "p": rows}) + "\n")
        fh.flush()
        time.sleep(max(0.0, 1.0 - (time.time() - t)))
