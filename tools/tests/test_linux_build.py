"""armake2 in place of AddonBuilder, and the POSIX parts of the worker's build path."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

POSIX = os.name != "nt"
if POSIX:
    from dayz_mcp import linux_compat

    linux_compat.install()
    from dayz_mcp import dayz_test_worker, linux_build, native_broker_protocol, server_cli

_FAKE_ARMAKE2 = """#!/bin/sh
for last; do :; done
echo "$@" >> "$FAKE_ARMAKE2_LOG"
[ -z "$FAKE_ARMAKE2_FAIL" ] || { echo boom; exit 1; }
sleep 0.3
printf pbo > "$last"
"""

# A binarizing armake2 runs Proton as its child; this one leaves a sleep behind.
_FAKE_ARMAKE2_WITH_CHILD = """#!/bin/sh
sleep 300 &
echo $! > child.pid
wait
"""


def _running(pid: int) -> bool:
    """Alive and not a zombie: a killed orphan stays a zombie until init reaps it."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stream:
            return stream.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False
    except OSError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True


def _gone_soon(pid: int, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while _running(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


@unittest.skipUnless(POSIX, "armake2 relay")
class RunAddonBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "armake2"
        fake.write_text(_FAKE_ARMAKE2)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.log = self.root / "calls"
        patcher = mock.patch.dict(
            os.environ,
            {"PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_ARMAKE2_LOG": str(self.log)},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source = self.root / "src"
        self.source.mkdir()
        self.target = self.root / "mods" / "@M" / "Addons"
        self.payload = {
            "clear": False,
            "pack_only": True,
            "prefix": "M",
            "source": str(self.source),
            "target": str(self.target),
            "temp": str(self.root / "temp"),
        }

    def _run(self, **overrides) -> dict:
        raw = linux_build.run_addon_builder(
            {**self.payload, **overrides}, cancelled=lambda: False
        )
        return json.loads(raw)

    def test_pack_only_answers_like_the_native_launcher(self) -> None:
        self.assertEqual(self._run(), {"exit_code": 0, "ok": True, "pbo_size": 3})
        self.assertEqual((self.target / "M.pbo").read_bytes(), b"pbo")
        self.assertEqual([p.name for p in self.target.iterdir()], ["M.pbo"])
        self.assertTrue(self.log.read_text().startswith("pack -f "))

    def test_a_binarizing_build_uses_proton(self) -> None:
        self._run(pack_only=False)
        self.assertTrue(self.log.read_text().startswith("build -f --proton-binarize "))

    def test_the_prefix_is_only_passed_without_a_marker(self) -> None:
        self._run()
        self.assertIn("-e prefix=M", self.log.read_text())
        self.log.unlink()
        (self.source / linux_build.MARKER).write_text("M")
        self._run()
        self.assertNotIn("prefix=", self.log.read_text())

    def test_a_failed_build_keeps_the_old_pbo_and_reports_its_output(self) -> None:
        self.assertTrue(self._run()["ok"])
        sink: list[bytes] = []
        with mock.patch.dict(os.environ, {"FAKE_ARMAKE2_FAIL": "1"}):
            raw = linux_build.run_addon_builder(
                self.payload, cancelled=lambda: False, output_sink=lambda _c, b: sink.append(b)
            )
        self.assertEqual(json.loads(raw), {"exit_code": 1, "ok": False, "pbo_size": 0})
        self.assertEqual((self.target / "M.pbo").read_bytes(), b"pbo")
        self.assertIn(b"boom", b"".join(sink))

    def test_a_missing_armake2_fails_the_build(self) -> None:
        with mock.patch.object(linux_build.shutil, "which", return_value=None):
            self.assertFalse(self._run()["ok"])

    def test_cancel_stops_the_build(self) -> None:
        raw = linux_build.run_addon_builder(self.payload, cancelled=lambda: True)
        self.assertFalse(json.loads(raw)["ok"])
        self.assertFalse((self.target / "M.pbo").exists())
        self.assertFalse(self.log.exists(), "armake2 started after the cancel")

    def test_cancel_also_kills_what_armake2_started(self) -> None:
        (self.root / "bin" / "armake2").write_text(_FAKE_ARMAKE2_WITH_CHILD)
        child_pid = self.root / "temp" / "child.pid"

        def cancelled() -> bool:
            return child_pid.is_file() and child_pid.read_text().strip() != ""

        raw = linux_build.run_addon_builder(self.payload, cancelled=cancelled)
        self.assertFalse(json.loads(raw)["ok"])
        pid = int(child_pid.read_text())
        try:
            self.assertTrue(_gone_soon(pid), "armake2's child outlived the cancel")
        finally:
            if _running(pid):
                os.kill(pid, signal.SIGKILL)

    def test_cancel_kills_a_running_build(self) -> None:
        answers = iter([False])
        raw = linux_build.run_addon_builder(
            self.payload, cancelled=lambda: next(answers, True)
        )
        self.assertFalse(json.loads(raw)["ok"])


@unittest.skipUnless(POSIX, "the worker's POSIX build path")
class WorkerBuildPathTest(unittest.TestCase):
    def test_an_addon_builder_frame_with_posix_paths_round_trips(self) -> None:
        payload = {
            "clear": False,
            "pack_only": True,
            "prefix": "M",
            "source": "/work/src",
            "target": "/work/@M/Addons",
            "temp": "/work/temp",
        }
        frame = native_broker_protocol.encode_request(
            native_broker_protocol.BrokerKind.ADDON_BUILDER, payload
        )
        self.assertEqual(native_broker_protocol.decode_request(frame).payload, payload)

    def test_namespace_from_marker_or_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as source:
            self.assertEqual(dayz_test_worker._read_pbo_namespace(source, "M", None), "M")
            Path(source, linux_build.MARKER).write_text("Custom\n")
            self.assertEqual(dayz_test_worker._read_pbo_namespace(source, "M", None), "Custom")

    def test_a_missing_source_is_refused(self) -> None:
        with self.assertRaises(Exception) as caught:
            dayz_test_worker._read_pbo_namespace("/nonexistent/source", "M", None)
        self.assertIn("build_source_unavailable", str(vars(caught.exception)) + str(caught.exception))

    def test_the_binarize_gate_sees_the_posix_leaf(self) -> None:
        self.assertTrue(dayz_test_worker._binarize_namespace_matches("MyMod", "/a/b/mymod"))
        self.assertFalse(dayz_test_worker._binarize_namespace_matches("MyMod", "/a/b/other"))

    def test_the_build_lock_conflicts_and_releases(self) -> None:
        async def scenario(root: Path) -> None:
            async with server_cli.async_shared_build_lock("t", root=root, wait_s=0):
                with self.assertRaises(server_cli.BuildLockBusy):
                    async with server_cli.async_shared_build_lock("t", root=root, wait_s=0):
                        self.fail("the second holder entered")
            async with server_cli.async_shared_build_lock("t", root=root, wait_s=0):
                pass

        with tempfile.TemporaryDirectory() as root:
            asyncio.run(scenario(Path(root)))

    def test_the_lock_can_be_cancelled(self) -> None:
        async def scenario(root: Path) -> None:
            with self.assertRaises(server_cli.BuildLockCancelled):
                async with server_cli.async_shared_build_lock(
                    "t", root=root, wait_s=0, cancel=lambda: True
                ):
                    self.fail("entered after cancel")

        with tempfile.TemporaryDirectory() as root:
            asyncio.run(scenario(Path(root)))


if __name__ == "__main__":
    unittest.main()
