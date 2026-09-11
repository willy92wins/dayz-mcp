"""Where does the spike's 15.63 s for 32 MB actually go?

The spike's pump did json.loads AND json.dumps on every message before writing it
(reviews/.../spike/supervisor.py, pump_worker -> send_host). It blamed readline. Split
the three costs so the production forwarder pays only what it must.
"""
import io
import json
import time


def build_line(mb: int) -> bytes:
    # Shaped like a real capture_screenshot response: one huge base64-ish string.
    blob = "A" * (mb * 1024 * 1024)
    return (json.dumps({
        "jsonrpc": "2.0",
        "id": 42,
        "result": {"content": [{"type": "image", "data": blob, "mimeType": "image/png"}]},
    }) + "\n").encode("utf-8")


def timed(label, fn):
    start = time.perf_counter()
    out = fn()
    return time.perf_counter() - start, label, out


print(f"{'carga':>8} {'readline':>10} {'loads':>10} {'dumps':>10} {'raw wr':>10}   {'total spike':>12} {'total raw':>10}")
for mb in (1, 8, 32):
    line = build_line(mb)

    t_read, _, _ = timed("readline", lambda: io.BufferedReader(io.BytesIO(line), 8192).readline())
    t_loads, _, obj = timed("loads", lambda: json.loads(line))
    t_dumps, _, _ = timed("dumps", lambda: (json.dumps(obj) + "\n").encode("utf-8"))
    sink = io.BytesIO()
    t_write, _, _ = timed("write", lambda: (sink.write(line), sink.flush()))

    spike_total = t_read + t_loads + t_dumps + t_write
    raw_total = t_read + t_write
    print(f"{mb:>6} MB {t_read:>9.3f}s {t_loads:>9.3f}s {t_dumps:>9.3f}s {t_write:>9.3f}s"
          f"   {spike_total:>11.3f}s {raw_total:>9.3f}s")
