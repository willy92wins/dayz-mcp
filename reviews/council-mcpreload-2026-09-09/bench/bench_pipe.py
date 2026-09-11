"""In memory the JSON round-trip is linear and cheap. So where did the spike's 15.63 s go?

Next suspect: the OS pipe. Measure a real child writing N MB to stdout and a parent
reading it back, three ways -- readline, chunked reads, and chunked reads with a bigger
pipe buffer -- so the supervisor's forwarding design rests on a number, not on the
spike's attribution.
"""
import subprocess
import sys
import time
from pathlib import Path

CHILD = r'''
import sys
mb = int(sys.argv[1])
line = b'{"jsonrpc":"2.0","id":42,"result":"' + b"A" * (mb * 1024 * 1024) + b'"}\n'
sys.stdout.buffer.write(line)
sys.stdout.buffer.flush()
'''

HERE = Path(__file__).resolve().parent
child_path = HERE / "_pipe_child.py"
child_path.write_text(CHILD, encoding="utf-8")


def run(mb: int, how: str, bufsize: int) -> tuple[float, int]:
    proc = subprocess.Popen(
        [sys.executable, "-u", str(child_path), str(mb)],
        stdout=subprocess.PIPE, bufsize=bufsize,
    )
    start = time.perf_counter()
    if how == "readline":
        data = proc.stdout.readline()
        total = len(data)
    else:
        total = 0
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            total += len(chunk)
    elapsed = time.perf_counter() - start
    proc.wait()
    return elapsed, total


print(f"{'carga':>8} {'readline':>12} {'MB/s':>8} {'chunked 64K':>13} {'MB/s':>8}")
for mb in (1, 8, 32):
    t_line, n_line = run(mb, "readline", -1)
    t_chunk, n_chunk = run(mb, "chunked", -1)
    assert n_line == n_chunk, (n_line, n_chunk)
    print(f"{mb:>6} MB {t_line:>11.3f}s {mb / t_line:>7.1f} "
          f"{t_chunk:>12.3f}s {mb / t_chunk:>7.1f}")

child_path.unlink()
