"""fb-20260822-025926-bad7: DayZ's stdout/stderr are kept beside its RPT.

_launch used to start the game with no stdout/stderr, so whatever it printed
was lost. It now hands the child two pipes the daemon drains into files in the
role's -profiles folder: capped per stream, created on the first byte, opened
by the daemon (never inherited), and closed when the pipe ends.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import dayz_test_worker
from dayz_mcp import process_lifecycle
from dayz_mcp.process_lifecycle import ProcessLifecycle
from tests._tiers import slow_test


class _Stream:
    """A pipe read end: chunks, then EOF; records what was consumed."""

    def __init__(self, chunks: list[bytes], *, fail_at: int | None = None) -> None:
        self.chunks = list(chunks)
        self.fail_at = fail_at
        self.reads = 0
        self.closed = False

    def read1(self, _size: int) -> bytes:
        if self.closed:
            raise ValueError("read of closed file")
        if self.fail_at is not None and self.reads == self.fail_at:
            raise OSError("broken pipe")
        self.reads += 1
        return self.chunks.pop(0) if self.chunks else b""

    def close(self) -> None:
        self.closed = True


def _captures(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.txt"))


class LaunchOutputPumpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.target = self.root / "DayZDiag_x64_2026-09-30_20-31-07_4242.stdout.txt"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def pump(self, stream: _Stream, *, cap: int = 1024, tail: int = 64) -> None:
        process_lifecycle._pump_launch_output(stream, self.target, cap, tail)

    def test_a_silent_stream_leaves_no_file(self) -> None:
        stream = _Stream([])
        self.pump(stream)
        self.assertEqual(_captures(self.root), [])
        self.assertTrue(stream.closed)

    def test_output_under_the_cap_is_kept_whole(self) -> None:
        stream = _Stream([b"Exiting: ", b"out of memory\r\n"])
        self.pump(stream)
        self.assertEqual(self.target.read_bytes(), b"Exiting: out of memory\r\n")
        self.assertTrue(stream.closed)

    def test_past_the_cap_the_head_stays_and_only_the_tail_follows(self) -> None:
        data = bytes(range(256)) * 4  # 1024 bytes, every position distinct mod 256
        stream = _Stream([data[:300], data[300:700], data[700:]])
        self.pump(stream, cap=100, tail=50)
        kept = self.target.read_bytes()
        marker = (
            b"\n[dayz-mcp] capture cap of 100 bytes reached: 874 bytes not kept, "
            b"the last 50 follow\n"
        )
        self.assertEqual(kept, data[:100] + marker + data[-50:])
        self.assertEqual(stream.chunks, [], "the whole pipe was read")
        self.assertTrue(stream.closed)

    def test_the_cap_bounds_the_file_whatever_the_stream_says(self) -> None:
        chunk = b"x" * 4096
        stream = _Stream([chunk] * 256)  # 1 MiB into a 2 KiB capture
        self.pump(stream, cap=2048, tail=256)
        size = self.target.stat().st_size
        self.assertLess(size, 2048 + 256 + 200)
        self.assertGreaterEqual(size, 2048 + 256)
        self.assertEqual(stream.chunks, [])

    def test_a_folder_that_does_not_exist_still_drains_the_pipe(self) -> None:
        self.target = self.root / "missing" / "x_1.stdout.txt"
        stream = _Stream([b"a" * 10, b"b" * 10, b"c"])
        self.pump(stream)
        self.assertEqual(stream.chunks, [], "a child is never left writing into a full pipe")
        self.assertTrue(stream.closed)
        self.assertFalse(self.target.parent.exists())

    def test_a_write_failure_stops_writing_but_keeps_draining(self) -> None:
        stream = _Stream([b"first", b"second", b"third", b"fourth"])
        real = process_lifecycle._write_all
        calls: list[bytes] = []

        def failing(handle, data):
            calls.append(bytes(data))
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            return real(handle, data)

        with mock.patch.object(process_lifecycle, "_write_all", side_effect=failing):
            self.pump(stream)
        self.assertEqual(stream.chunks, [])
        self.assertTrue(stream.closed)
        self.assertEqual(calls, [b"first", b"second"], "no write after the failure")
        self.assertEqual(self.target.read_bytes(), b"first")

    def test_a_read_error_ends_the_pump_and_closes_the_pipe(self) -> None:
        stream = _Stream([b"one", b"two"], fail_at=1)
        self.pump(stream)
        self.assertEqual(self.target.read_bytes(), b"one")
        self.assertTrue(stream.closed)

    def test_an_existing_file_is_never_truncated(self) -> None:
        self.target.write_bytes(b"older capture")
        stream = _Stream([b"new"])
        self.pump(stream)
        self.assertEqual(self.target.read_bytes(), b"older capture")
        sibling = self.root / "DayZDiag_x64_2026-09-30_20-31-07_4242-2.stdout.txt"
        self.assertEqual(sibling.read_bytes(), b"new")


class LaunchOutputKwargsTest(unittest.TestCase):
    """What _launch hands Popen, with the argv the worker really builds."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _worker_argv(self, role: str) -> list[str]:
        runtime = dayz_test_worker.WorkerRuntimePolicy(
            dev_root=str(self.root / "ExampleMod_Suite"),
            mod="ExampleMod",
            diag_executable=str(self.root / "DayZ" / "DayZDiag_x64.exe"),
            game_directory=str(self.root / "DayZ"),
            mission_aliases=(
                ("chernarus", str(self.root / "mpmissions" / "dayzOffline.chernarusplus")),
            ),
            mods_root=str(self.root / "Mods"),
            build_temp_root=str(self.root / "temp"),
            build_source_basename=None,
        )
        payload: dict[str, object] = {
            "auto_remediate_steam": False,
            "base_mods": [],
            "extra_mods": [],
            "height": 1080,
            "mission": "chernarus",
            "no_file_patching": False,
            "player_name": "Dev",
            "port": 2302,
            "server_mods": [],
            "width": 1920,
        }
        return list(
            dayz_test_worker._start_core(payload, runtime, role=role, run_id=None)["argv"]
        )

    def _popen_kwargs(self, argv: list[str], returned: object = None) -> dict:
        seen: list[dict] = []

        def fake_popen(_argv, **kwargs):
            seen.append(kwargs)
            return returned if returned is not None else mock.Mock(pid=4242)

        lifecycle = object.__new__(ProcessLifecycle)
        with mock.patch.object(process_lifecycle.subprocess, "Popen", side_effect=fake_popen):
            lifecycle._launch(argv, str(self.root), "normal")
        self.assertEqual(len(seen), 1)
        return seen[0]

    def test_every_role_launch_pipes_both_streams_and_reads_nothing_from_stdin(self) -> None:
        for role in ("server", "client", "offline"):
            with self.subTest(role=role):
                kwargs = self._popen_kwargs(self._worker_argv(role))
                self.assertIs(kwargs["stdout"], subprocess.PIPE)
                self.assertIs(kwargs["stderr"], subprocess.PIPE)
                self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
                # close_fds with std handles restricts inheritance to those three.
                self.assertIs(kwargs["close_fds"], True)

    def test_an_argv_without_a_profiles_folder_launches_as_before(self) -> None:
        for argv in (
            ["DayZDiag_x64.exe"],
            ["DayZDiag_x64.exe", "-server", "-port=2302"],
            ["DayZDiag_x64.exe", r"-profiles=relative\profiles"],
            ["DayZDiag_x64.exe", "-profiles="],
        ):
            with self.subTest(argv=argv):
                kwargs = self._popen_kwargs(argv)
                for key in ("stdin", "stdout", "stderr"):
                    self.assertNotIn(key, kwargs)

    def test_a_popen_stand_in_without_streams_starts_no_drain(self) -> None:
        before = {thread.name for thread in threading.enumerate()}
        self._popen_kwargs(self._worker_argv("server"), returned=object())
        started = {
            thread.name
            for thread in threading.enumerate()
            if thread.name.startswith("dayz-launch-")
        } - before
        self.assertEqual(started, set())


_CHILD = r"""
import sys
sys.stdout.buffer.write(b"out:" + sys.argv[1].encode("ascii") * int(sys.argv[2]))
sys.stdout.buffer.flush()
if sys.argv[3] != "0":
    sys.stderr.buffer.write(b"err:reason\n")
    sys.stderr.buffer.flush()
"""


class LaunchOutputRealProcessTest(unittest.TestCase):
    """A real child through the real _launch: files, cap, no lingering handle."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.profiles = Path(self.temporary.name) / "_server" / "profiles"
        self.profiles.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, *args: str) -> int:
        argv = [
            sys.executable,
            "-c",
            _CHILD,
            *args,
            f"-profiles={self.profiles}",
        ]
        lifecycle = object.__new__(ProcessLifecycle)
        child = lifecycle._launch(argv, str(self.profiles), "hidden")
        self.assertEqual(child.wait(timeout=60), 0)
        for thread in threading.enumerate():
            if thread.name.startswith("dayz-launch-") and thread.name.endswith(
                f"-{child.pid}"
            ):
                thread.join(timeout=30)
                self.assertFalse(thread.is_alive(), thread.name)
        self.assertTrue(child.stdout.closed)
        self.assertTrue(child.stderr.closed)
        return child.pid

    @slow_test
    def test_a_real_child_leaves_both_streams_beside_its_rpt(self) -> None:
        pid = self._run("A", "5", "1")
        stem = Path(sys.executable).stem
        out = list(self.profiles.glob(f"{stem}_*_{pid}.stdout.txt"))
        err = list(self.profiles.glob(f"{stem}_*_{pid}.stderr.txt"))
        self.assertEqual(len(out), 1, sorted(self.profiles.iterdir()))
        self.assertEqual(len(err), 1)
        self.assertEqual(out[0].read_bytes(), b"out:AAAAA")
        self.assertEqual(err[0].read_bytes(), b"err:reason\n")
        # Nothing keeps them open once the child is gone: Python opens files
        # without FILE_SHARE_DELETE, so a handle still held would refuse this.
        for path in (out[0], err[0]):
            os.remove(path)
            self.assertFalse(path.exists())

    @slow_test
    def test_a_chatty_child_is_capped_and_never_blocks(self) -> None:
        # 200 000 bytes is far past the pipe buffer: a child whose pipe is
        # not drained would block, and wait() above would time out.
        with mock.patch.object(process_lifecycle, "LAUNCH_OUTPUT_CAP_BYTES", 1000), \
                mock.patch.object(process_lifecycle, "LAUNCH_OUTPUT_TAIL_BYTES", 100):
            started = time.monotonic()
            pid = self._run("B", "200000", "0")
        self.assertLess(time.monotonic() - started, 60.0)
        out = list(self.profiles.glob(f"*_{pid}.stdout.txt"))
        self.assertEqual(len(out), 1)
        kept = out[0].read_bytes()
        self.assertTrue(kept.startswith(b"out:" + b"B" * 996))
        self.assertTrue(kept.endswith(b"B" * 100))
        self.assertIn(b"[dayz-mcp] capture cap of 1000 bytes reached", kept)
        self.assertLess(len(kept), 1000 + 100 + 200)
        self.assertEqual(
            list(self.profiles.glob(f"*_{pid}.stderr.txt")),
            [],
            "a stream that wrote nothing leaves no file",
        )


if __name__ == "__main__":
    unittest.main()
