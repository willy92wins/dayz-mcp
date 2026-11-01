"""A3 (964e): registry lock retry with bounded backoff and deadline."""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from dayz_mcp import dayz_test_tool, launcher_registry, registry_lock
from dayz_mcp import launcher_registry_update as updater


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class RegistryLockRetryInjectedClockTest(unittest.TestCase):
    def test_retry_acquires_after_busy_with_injected_clock(self) -> None:
        clock = _FakeClock()
        sentinel = object()
        with mock.patch.object(
            registry_lock,
            "acquire_registry_lock",
            side_effect=[
                RuntimeError("launcher_registry_busy"),
                RuntimeError("launcher_registry_busy"),
                sentinel,
            ],
        ) as acquire:
            result = registry_lock.acquire_registry_lock_with_retry(
                exclusive=True,
                path=Path("approved-launchers.lock"),
                deadline_s=5.0,
                initial_backoff_s=0.02,
                max_backoff_s=0.1,
                sleep_fn=clock.sleep,
                monotonic_fn=clock.monotonic,
            )
        self.assertIs(result, sentinel)
        self.assertEqual(acquire.call_count, 3)
        self.assertEqual(clock.sleeps, [0.02, 0.04])

    def test_no_acquire_after_deadline_when_sleep_overshoots(self) -> None:
        # Sol reproduction: deadline 1 s, first attempt busy, wake at 2.05 s,
        # second attempt would be free but must not be tried.
        clock = _FakeClock()
        sentinel = object()
        calls: list[int] = []

        def fake_acquire(**_kwargs: object) -> object:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("launcher_registry_busy")
            return sentinel

        def overshoot_sleep(_seconds: float) -> None:
            clock.now = 2.05

        with mock.patch.object(
            registry_lock, "acquire_registry_lock", side_effect=fake_acquire
        ):
            with self.assertRaisesRegex(
                RuntimeError, "launcher_registry_busy_timeout"
            ):
                registry_lock.acquire_registry_lock_with_retry(
                    exclusive=True,
                    path=Path("approved-launchers.lock"),
                    deadline_s=1.0,
                    initial_backoff_s=0.05,
                    max_backoff_s=0.5,
                    sleep_fn=overshoot_sleep,
                    monotonic_fn=clock.monotonic,
                )
        self.assertEqual(len(calls), 1)
        self.assertEqual(clock.now, 2.05)

    def test_busy_past_deadline_with_injected_clock(self) -> None:
        clock = _FakeClock()
        with mock.patch.object(
            registry_lock,
            "acquire_registry_lock",
            side_effect=RuntimeError("launcher_registry_busy"),
        ) as acquire:
            with self.assertRaisesRegex(
                RuntimeError, "launcher_registry_busy_timeout"
            ):
                registry_lock.acquire_registry_lock_with_retry(
                    exclusive=True,
                    path=Path("approved-launchers.lock"),
                    deadline_s=1.0,
                    initial_backoff_s=0.4,
                    max_backoff_s=0.5,
                    sleep_fn=clock.sleep,
                    monotonic_fn=clock.monotonic,
                )
        self.assertEqual(acquire.call_count, 3)
        self.assertEqual(len(clock.sleeps), 3)
        for got, want in zip(clock.sleeps, [0.4, 0.5, 0.1]):
            self.assertAlmostEqual(got, want)


@unittest.skipUnless(os.name == "nt", "LockFileEx is Windows-only")
class RegistryLockRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.lock_path = Path(directory.name) / "approved-launchers.lock"
        self.lock_path.touch()

    def test_busy_lock_is_acquired_after_release(self) -> None:
        holder = registry_lock.acquire_registry_lock(
            exclusive=True, path=self.lock_path
        )
        started = threading.Event()
        acquired = threading.Event()
        result: dict[str, object] = {}

        def _retry() -> None:
            started.set()
            try:
                with registry_lock.acquire_registry_lock_with_retry(
                    exclusive=True,
                    path=self.lock_path,
                    deadline_s=5.0,
                    initial_backoff_s=0.02,
                    max_backoff_s=0.1,
                ):
                    acquired.set()
                    result["ok"] = True
            except BaseException as error:  # noqa: BLE001 - reported below
                result["error"] = error

        worker = threading.Thread(target=_retry)
        try:
            worker.start()
            self.assertTrue(started.wait(5.0))
            # While holder holds the lock, acquisition cannot succeed,
            # whatever the scheduling of the worker thread.
            self.assertFalse(acquired.wait(0.3))
        finally:
            holder.close()
        worker.join(5.0)
        self.assertFalse(worker.is_alive())
        self.assertTrue(acquired.is_set(), result)
        self.assertEqual(result.get("ok"), True)
        self.assertNotIn("error", result)

    def test_busy_past_deadline_fails_clearly_without_hanging(self) -> None:
        with registry_lock.acquire_registry_lock(
            exclusive=True, path=self.lock_path
        ):
            started = time.monotonic()
            with self.assertRaisesRegex(
                RuntimeError, "launcher_registry_busy_timeout"
            ):
                registry_lock.acquire_registry_lock_with_retry(
                    exclusive=True,
                    path=self.lock_path,
                    deadline_s=0.3,
                    initial_backoff_s=0.02,
                    max_backoff_s=0.05,
                )
            self.assertLess(time.monotonic() - started, 2.0)

    def test_non_busy_error_is_not_retried(self) -> None:
        missing = (
            self.lock_path.with_name("no-such-dir") / "approved-launchers.lock"
        )
        started = time.monotonic()
        with self.assertRaisesRegex(
            RuntimeError, "invalid_launcher_registry_lock"
        ):
            registry_lock.acquire_registry_lock_with_retry(
                exclusive=True,
                path=missing,
                deadline_s=5.0,
                initial_backoff_s=0.02,
                max_backoff_s=0.05,
            )
        self.assertLess(time.monotonic() - started, 1.0)

    def test_fail_fast_acquire_is_unchanged(self) -> None:
        with registry_lock.acquire_registry_lock(
            exclusive=True, path=self.lock_path
        ):
            with self.assertRaisesRegex(
                RuntimeError, "^launcher_registry_busy$"
            ):
                registry_lock.acquire_registry_lock(
                    exclusive=True, path=self.lock_path
                )


_BASELINE = b'{\n  "format_version": 1,\n  "launchers": []\n}\n'


def _entry(sha256: str = "A" * 64) -> dict[str, object]:
    return {
        "id": "dayz-test-v1",
        "relative_path": "dayz-test-launcher.exe",
        "root": r"C:\fixture\dayz-test-v1",
        "root_file_id": {"file_id": "0" * 32, "volume_serial_number": 1},
        "sha256": sha256,
    }


class _FakeLock:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "_FakeLock":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _flaky(original, failures: int, calls: list):
    def _acquire(**kwargs):
        calls.append(kwargs)
        if len(calls) <= failures:
            raise RuntimeError("launcher_registry_busy")
        return original(**kwargs)

    return _acquire


def _always_busy(calls: list):
    def _acquire(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("launcher_registry_busy")

    return _acquire


def _fast_retry(**kwargs):
    kwargs.update(deadline_s=0.3, initial_backoff_s=0.02, max_backoff_s=0.05)
    return _REAL_RETRY(**kwargs)


_REAL_RETRY = registry_lock.acquire_registry_lock_with_retry
_REAL_ACQUIRE = registry_lock.acquire_registry_lock


@unittest.skipUnless(os.name == "nt", "LockFileEx is Windows-only")
class CallerRetryTest(unittest.TestCase):
    def _paths(self, root: Path, raw: bytes = _BASELINE):
        registry = root / "approved-launchers.json"
        registry.write_bytes(raw)
        lock = root / "approved-launchers.lock"
        lock.write_bytes(b"lock\n")
        return registry, lock, root / "receipts"

    # -- open_approved_launcher (shared) --------------------------------
    def test_open_retries_transient_busy(self) -> None:
        fake_lock = _FakeLock()
        opened = mock.Mock()
        calls: list = []
        with (
            mock.patch.object(
                registry_lock,
                "acquire_registry_lock",
                _flaky(lambda **_kw: fake_lock, 2, calls),
            ),
            mock.patch.object(
                launcher_registry,
                "_read_canonical_registry",
                return_value=[_entry()],
            ),
            mock.patch.object(
                launcher_registry, "_open_validated_entry", return_value=opened
            ),
        ):
            result = launcher_registry.open_approved_launcher("dayz-test-v1")
        self.assertIs(result, opened)
        self.assertIs(opened._registry_lock, fake_lock)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(c["exclusive"] is False for c in calls))

    def test_open_exhausted_fails_busy_timeout(self) -> None:
        calls: list = []
        with (
            mock.patch.object(
                registry_lock, "acquire_registry_lock", _always_busy(calls)
            ),
            mock.patch.object(
                registry_lock, "acquire_registry_lock_with_retry", _fast_retry
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "launcher_registry_busy_timeout"
            ):
                launcher_registry.open_approved_launcher("dayz-test-v1")
        self.assertGreaterEqual(len(calls), 2)

    # -- _install_transition (exclusive) --------------------------------
    def test_install_retries_transient_busy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            calls: list = []
            with (
                mock.patch.object(
                    registry_lock,
                    "acquire_registry_lock",
                    _flaky(_REAL_ACQUIRE, 2, calls),
                ),
                mock.patch.object(
                    updater, "_validated_entry", return_value=_entry()
                ),
            ):
                sha = updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(_BASELINE),
                )
            self.assertEqual(sha, updater._sha256(registry.read_bytes()))
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(c["exclusive"] is True for c in calls))

    def test_install_exhausted_fails_and_leaves_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            calls: list = []
            with (
                mock.patch.object(
                    registry_lock, "acquire_registry_lock", _always_busy(calls)
                ),
                mock.patch.object(
                    updater, "acquire_registry_lock_with_retry", _fast_retry
                ),
                mock.patch.object(
                    updater, "_validated_entry", return_value=_entry()
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "launcher_registry_busy_timeout"
                ):
                    updater._install_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(_BASELINE),
                    )
            self.assertGreaterEqual(len(calls), 2)
            self.assertTrue(all(c["exclusive"] is True for c in calls))
            self.assertEqual(registry.read_bytes(), _BASELINE)

    # -- _replace_transition (exclusive) --------------------------------
    def _installed_registry(self, root: Path, sha256: str = "A" * 64) -> bytes:
        raw = updater._canonical(
            {"format_version": 1, "launchers": [_entry(sha256)]}
        )
        self._paths(root, raw)
        return raw

    def test_replace_retries_transient_busy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = self._installed_registry(root)
            registry = root / "approved-launchers.json"
            lock = root / "approved-launchers.lock"
            receipts = root / "receipts"
            calls: list = []
            with (
                mock.patch.object(
                    registry_lock,
                    "acquire_registry_lock",
                    _flaky(_REAL_ACQUIRE, 2, calls),
                ),
                mock.patch.object(
                    updater, "_validated_entry", return_value=_entry("B" * 64)
                ),
            ):
                updater._replace_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(raw),
                )
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(c["exclusive"] is True for c in calls))
            self.assertIn("B" * 64, registry.read_text(encoding="utf-8"))

    def test_replace_exhausted_fails_and_leaves_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = self._installed_registry(root)
            registry = root / "approved-launchers.json"
            lock = root / "approved-launchers.lock"
            receipts = root / "receipts"
            calls: list = []
            with (
                mock.patch.object(
                    registry_lock, "acquire_registry_lock", _always_busy(calls)
                ),
                mock.patch.object(
                    updater, "acquire_registry_lock_with_retry", _fast_retry
                ),
                mock.patch.object(
                    updater, "_validated_entry", return_value=_entry("B" * 64)
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "launcher_registry_busy_timeout"
                ):
                    updater._replace_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(raw),
                    )
            self.assertGreaterEqual(len(calls), 2)
            self.assertTrue(all(c["exclusive"] is True for c in calls))
            self.assertEqual(registry.read_bytes(), raw)

    # -- describe_registry_provenance (shared) ---------------------------
    def test_describe_retries_transient_busy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            calls: list = []
            with mock.patch.object(
                registry_lock,
                "acquire_registry_lock",
                _flaky(_REAL_ACQUIRE, 2, calls),
            ):
                state = updater.describe_registry_provenance(
                    registry_path=registry, lock_path=lock, receipts_path=receipts
                )
            self.assertEqual(state["status"], "pristine")
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(c["exclusive"] is False for c in calls))

    def test_describe_exhausted_fails_busy_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            calls: list = []
            with (
                mock.patch.object(
                    registry_lock, "acquire_registry_lock", _always_busy(calls)
                ),
                mock.patch.object(
                    updater, "acquire_registry_lock_with_retry", _fast_retry
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "launcher_registry_busy_timeout"
                ):
                    updater.describe_registry_provenance(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                    )
            self.assertGreaterEqual(len(calls), 2)

    # -- _rollback_transition (exclusive) --------------------------------
    def _install(self, root: Path):
        registry, lock, receipts = self._paths(root)
        with mock.patch.object(updater, "_validated_entry", return_value=_entry()):
            updater._install_transition(
                registry_path=registry,
                lock_path=lock,
                receipts_path=receipts,
                bundle=root,
                expected_sha256=updater._sha256(_BASELINE),
            )
        return registry, lock, receipts

    def test_rollback_retries_transient_busy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._install(root)
            calls: list = []
            with mock.patch.object(
                registry_lock,
                "acquire_registry_lock",
                _flaky(_REAL_ACQUIRE, 2, calls),
            ):
                restored = updater._rollback_transition(
                    registry_path=registry, lock_path=lock, receipts_path=receipts
                )
            self.assertEqual(restored, updater._sha256(_BASELINE))
            self.assertEqual(registry.read_bytes(), _BASELINE)
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(c["exclusive"] is True for c in calls))

    def test_rollback_exhausted_fails_and_leaves_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._install(root)
            installed = registry.read_bytes()
            calls: list = []
            with (
                mock.patch.object(
                    registry_lock, "acquire_registry_lock", _always_busy(calls)
                ),
                mock.patch.object(
                    updater, "acquire_registry_lock_with_retry", _fast_retry
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "launcher_registry_busy_timeout"
                ):
                    updater._rollback_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                    )
            self.assertGreaterEqual(len(calls), 2)
            self.assertTrue(all(c["exclusive"] is True for c in calls))
            self.assertEqual(registry.read_bytes(), installed)


class _FakeOpened:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "_FakeOpened":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class AsyncLauncherOpenTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_progress_while_launcher_open_waits(self) -> None:
        # The synchronous open (with its busy-lock retry sleep) would stall
        # a 10 ms timer for its whole wait if it ran on the event loop.
        opened = _FakeOpened()

        def slow_open(_launcher_id: str) -> _FakeOpened:
            time.sleep(0.4)
            return opened

        ticks: list[float] = []

        async def ticker() -> None:
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        with mock.patch.object(
            dayz_test_tool, "open_approved_launcher", side_effect=slow_open
        ):
            tick_task = asyncio.create_task(ticker())
            try:
                result = await dayz_test_tool._open_approved_launcher_async()
            finally:
                tick_task.cancel()
        self.assertIs(result, opened)
        self.assertGreaterEqual(len(ticks), 5)

    async def test_cancelled_open_releases_launcher_when_worker_finishes(
        self,
    ) -> None:
        opened = _FakeOpened()

        def slow_open(_launcher_id: str) -> _FakeOpened:
            time.sleep(0.3)
            return opened

        with mock.patch.object(
            dayz_test_tool, "open_approved_launcher", side_effect=slow_open
        ):
            task = asyncio.create_task(
                dayz_test_tool._open_approved_launcher_async()
            )
            await asyncio.sleep(0.05)
            started = time.monotonic()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            # Cancellation is prompt, not held until the worker finishes.
            self.assertLess(time.monotonic() - started, 0.25)
            self.assertFalse(opened.closed)
            # Once the worker returns the launcher, it is closed (lock freed).
            deadline = time.monotonic() + 2.0
            while not opened.closed and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
        self.assertTrue(opened.closed)


if __name__ == "__main__":
    unittest.main()
