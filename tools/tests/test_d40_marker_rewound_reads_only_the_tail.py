"""D40: rewinding a marker must read the file's tail, not the whole file.

A DayZ RPT reaches hundreds of MB in a long session, and `_marker_rewound` ran
on every `wait_for(log_matches=..., lookback_lines>0)`. `log_tail` already caps
its own reads at MAX_TAIL_BYTES; this helper lived outside that module and
ignored it.

Reading only a window makes the offset arithmetic the part that can go wrong, so
the boundary cases are asserted alongside the byte count: an offset that is
"inside the window" but lands mid-line hands the caller half a line as though it
were whole.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import log_tail, server  # noqa: E402

MAX = log_tail.MAX_TAIL_BYTES


class MarkerRewoundReadsOnlyTheTailTest(unittest.TestCase):
    def _write(self, body: bytes) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "test.log"
        path.write_bytes(body)
        return path

    def _assert_lands_on_a_line_start(self, body: bytes, offset: int) -> None:
        self.assertTrue(
            offset == 0 or body[offset - 1 : offset] == b"\n",
            f"offset {offset} is mid-line: a reader resuming there gets half a line",
        )

    def test_reads_at_most_the_tail_window(self) -> None:
        # 4 MB of short lines, far past the 256 KB ceiling.
        body = b"".join(b"line-%06d\n" % i for i in range(400_000))
        path = self._write(body)

        read_sizes: list[int] = []
        real_open = Path.open

        def counting_open(self_path, *args, **kwargs):  # noqa: ANN001
            handle = real_open(self_path, *args, **kwargs)
            real_read = handle.read

            def read(size=-1):
                data = real_read(size)
                read_sizes.append(len(data))
                return data

            handle.read = read
            return handle

        with unittest.mock.patch.object(Path, "open", counting_open):
            marker = server._marker_rewound(str(path), lookback_lines=50)

        self.assertLessEqual(
            max(read_sizes),
            MAX,
            f"a single read took {max(read_sizes):,} bytes; the ceiling is {MAX:,}",
        )
        self.assertEqual(len(body), marker.size)
        self._assert_lands_on_a_line_start(body, marker.offset)
        self.assertEqual(50, body[marker.offset :].count(b"\n"))

    def test_window_holding_fewer_lines_than_asked_stays_in_the_window(self) -> None:
        # 40 lines of 32 KB: the 256 KB window holds only ~8 line breaks, so a
        # lookback of 20 cannot be satisfied. Answering 0 here would re-read the
        # entire file, which is the defect this fix exists to remove.
        body = (b"x" * (32 * 1024 - 1) + b"\n") * 40
        path = self._write(body)
        marker = server._marker_rewound(str(path), lookback_lines=20)

        self.assertGreaterEqual(
            marker.offset,
            len(body) - MAX,
            "offset fell outside the tail window: the whole file would be read",
        )
        self._assert_lands_on_a_line_start(body, marker.offset)

    def test_offset_is_exact_when_the_window_can_satisfy_the_lookback(self) -> None:
        # Just over the window, so a boundary fragment is dropped -- if its bytes
        # are not counted the offset drifts by the fragment's length.
        body = b"".join(b"line-%05d\n" % i for i in range(40_000))
        path = self._write(body)
        marker = server._marker_rewound(str(path), lookback_lines=100)

        self._assert_lands_on_a_line_start(body, marker.offset)
        self.assertEqual(100, body[marker.offset :].count(b"\n"))

    def test_short_file_still_rewinds_to_the_start(self) -> None:
        body = b"a\nb\nc\n"
        path = self._write(body)
        marker = server._marker_rewound(str(path), lookback_lines=10)
        self.assertEqual(0, marker.offset)
        self.assertEqual(3, body[marker.offset :].count(b"\n"))


if __name__ == "__main__":
    unittest.main()
