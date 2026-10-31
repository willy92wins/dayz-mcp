"""A3 (964e): registry lock retry with bounded backoff and deadline."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from dayz_mcp import registry_lock


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


if __name__ == "__main__":
    unittest.main()
