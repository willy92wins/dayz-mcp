"""A3 (964e): registry lock retry with bounded backoff and deadline."""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from dayz_mcp import dayz_test_request, dayz_test_tool, launcher_registry, registry_lock
from dayz_mcp import launcher_registry_update as updater
from dayz_mcp.server_cli import reset_instance_context_for_tests


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
        rejected_busy = threading.Event()
        acquired = threading.Event()
        result: dict[str, object] = {}
        calls: list = []

        def observed_acquire(**kwargs):
            calls.append(kwargs)
            try:
                return _REAL_ACQUIRE(**kwargs)
            except RuntimeError as error:
                if str(error) == "launcher_registry_busy":
                    rejected_busy.set()
                raise

        def _retry() -> None:
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
        with mock.patch.object(
            registry_lock, "acquire_registry_lock", side_effect=observed_acquire
        ):
            worker_started = False
            try:
                worker.start()
                worker_started = True
                self.assertTrue(rejected_busy.wait(5.0), result)
                self.assertFalse(acquired.is_set())
            finally:
                holder.close()
                if worker_started:
                    worker.join(5.0)
        self.assertFalse(worker.is_alive())
        self.assertTrue(acquired.is_set(), result)
        self.assertEqual(result.get("ok"), True)
        self.assertNotIn("error", result)
        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(all(call["exclusive"] is True for call in calls))

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


class _FakeOpenedLauncher:
    def __init__(self) -> None:
        self.closed = False

    def validate_native_pe(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "_FakeOpenedLauncher":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _busy_acquire(calls: list):
    def _acquire(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("launcher_registry_busy")

    return _acquire


class NonBlockingRegistryOpenTest(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_cache = launcher_registry._cached_approved_registry()
        launcher_registry._clear_approved_registry_cache_for_test()
        self.addCleanup(self._restore_cache)

    def _restore_cache(self) -> None:
        launcher_registry._clear_approved_registry_cache_for_test()
        if self._saved_cache is not None:
            launcher_registry._cache_approved_registry(self._saved_cache)

    def test_busy_without_cache_raises_busy_after_one_fail_fast_attempt(self) -> None:
        calls: list = []
        started = time.monotonic()
        with mock.patch.object(
            registry_lock, "acquire_registry_lock", _busy_acquire(calls)
        ):
            with self.assertRaisesRegex(RuntimeError, "^launcher_registry_busy$"):
                launcher_registry.try_open_approved_launcher("dayz-test-v1")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(call["exclusive"] is False for call in calls))

    def test_busy_with_cache_opens_from_cache_without_lock(self) -> None:
        launcher_registry._cache_approved_registry([_entry()])
        opened = _FakeOpenedLauncher()
        calls: list = []
        with (
            mock.patch.object(
                registry_lock, "acquire_registry_lock", _busy_acquire(calls)
            ),
            mock.patch.object(
                launcher_registry,
                "_read_canonical_registry",
                return_value=[_entry()],
            ),
            mock.patch.object(
                launcher_registry, "_open_validated_entry", return_value=opened
            ) as open_entry,
        ):
            result = launcher_registry.try_open_approved_launcher("dayz-test-v1")
        self.assertIs(result, opened)
        self.assertFalse(hasattr(opened, "_registry_lock"))
        self.assertEqual(open_entry.call_count, 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(call["exclusive"] is False for call in calls))

    def test_free_lock_reads_caches_and_releases_lock_on_error(self) -> None:
        fake_lock = _FakeLock()
        calls: list = []

        def free_acquire(**kwargs):
            calls.append(kwargs)
            return fake_lock

        with (
            mock.patch.object(
                registry_lock, "acquire_registry_lock", side_effect=free_acquire
            ),
            mock.patch.object(
                launcher_registry,
                "_read_canonical_registry",
                return_value=[_entry()],
            ),
            mock.patch.object(
                launcher_registry,
                "_open_validated_entry",
                side_effect=ValueError("launcher_not_approved"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "launcher_not_approved"):
                launcher_registry.try_open_approved_launcher("dayz-test-v1")
        self.assertTrue(fake_lock.closed)
        self.assertTrue(all(call["exclusive"] is False for call in calls))
        self.assertEqual(launcher_registry._cached_approved_registry(), [_entry()])

    def test_pending_open_completes_after_lock_is_released(self) -> None:
        # Busy with a cache: the close-path open is served from the cache.
        launcher_registry._cache_approved_registry([_entry()])
        cached_opened = _FakeOpenedLauncher()
        with (
            mock.patch.object(
                registry_lock, "acquire_registry_lock", _busy_acquire([])
            ),
            mock.patch.object(
                launcher_registry,
                "_read_canonical_registry",
                return_value=[_entry()],
            ),
            mock.patch.object(
                launcher_registry, "_open_validated_entry", return_value=cached_opened
            ),
        ):
            self.assertIs(
                launcher_registry.try_open_approved_launcher("dayz-test-v1"),
                cached_opened,
            )
        # Once the holder releases, the next call takes the real lock and
        # re-reads the canonical registry instead of serving the cache.
        fake_lock = _FakeLock()
        fresh_opened = _FakeOpenedLauncher()
        with (
            mock.patch.object(
                registry_lock,
                "acquire_registry_lock",
                return_value=fake_lock,
            ),
            mock.patch.object(
                launcher_registry,
                "_read_canonical_registry",
                return_value=[_entry()],
            ) as read_registry,
            mock.patch.object(
                launcher_registry, "_open_validated_entry", return_value=fresh_opened
            ),
        ):
            result = launcher_registry.try_open_approved_launcher("dayz-test-v1")
        self.assertIs(result, fresh_opened)
        self.assertIs(fresh_opened._registry_lock, fake_lock)  # type: ignore[attr-defined]
        self.assertEqual(read_registry.call_count, 1)


_CLOSE_RUN_ID = "run-a3-fix5"
_CLOSE_TERMINATION = "--- Termination successfully completed ---\n"
_CLOSE_CONNECT = 'Player "Ada" (id=1) is connected\n'
_CLOSE_LINK = "[StateMachine]: Player Ada (uid 76561198000000001)\n"


class CloseNonBlockingLogoutTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        reset_instance_context_for_tests()
        self.addCleanup(reset_instance_context_for_tests)
        self._saved_cache = launcher_registry._cached_approved_registry()
        launcher_registry._clear_approved_registry_cache_for_test()
        self.addCleanup(self._restore_cache)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "_server").mkdir()
        (self.root / "_client").mkdir()
        self.server_rpt = self.root / "_server" / "profiles" / "server.rpt"
        self.client_rpt = self.root / "_client" / "profiles" / "client.rpt"
        self.policy = dayz_test_request.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=str(self.root),
            default_source=str(self.root),
            default_base_mods=(),
            mission_roots=(str(self.root / "_server" / "mpmissions"),),
            mod_roots=(str(self.root),),
        )
        self.acquire_calls: list = []
        self._busy_patcher = mock.patch.object(
            registry_lock, "acquire_registry_lock", _busy_acquire(self.acquire_calls)
        )
        self._busy_patcher.start()
        self.addCleanup(self._busy_patcher.stop)

    def _restore_cache(self) -> None:
        launcher_registry._clear_approved_registry_cache_for_test()
        if self._saved_cache is not None:
            launcher_registry._cache_approved_registry(self._saved_cache)

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _runtime(self, *, append_logout: bool) -> SimpleNamespace:
        runtime = SimpleNamespace(
            active_lease_token="held",
            active_ticket=None,
            active_operation_id=None,
            retire_event=None,
            close_at=[],
        )

        async def lifecycle_status():
            if runtime.retire_event is not None:
                return {
                    "runs": [],
                    "retired_run_diagnostics": [
                        {
                            "run_id": _CLOSE_RUN_ID,
                            "daemon_generation_at_launch": "",
                            "daemon_generation_current": "",
                            "generation_changed": False,
                            "event": runtime.retire_event,
                            "reason": "all_processes_gone_or_foreign",
                            "decision": "reaped",
                            "state": "EXITED",
                        }
                    ],
                }
            return {
                "runs": [
                    {
                        "run_id": _CLOSE_RUN_ID,
                        "state": "RUNNING",
                        "mod": "@ExampleMod",
                        "profiles": str(self.server_rpt.parent),
                        "processes": [
                            {"role": "client", "pid": 2},
                            {"role": "server", "pid": 1},
                        ],
                    }
                ],
                "retired_run_diagnostics": [],
            }

        async def lifecycle_close_roles(run_id: str, roles: list[str]):
            runtime.close_at.append((tuple(roles), time.monotonic()))
            for role in roles:
                path = self.client_rpt if role == "client" else self.server_rpt
                if append_logout and role == "client" and self.server_rpt.is_file():
                    self.server_rpt.write_text(
                        self.server_rpt.read_text(encoding="utf-8")
                        + "[Logout]: Player 76561198000000001 finished\n",
                        encoding="utf-8",
                    )
                if path.is_file():
                    path.write_text(
                        path.read_text(encoding="utf-8") + _CLOSE_TERMINATION,
                        encoding="utf-8",
                    )
            result: dict[str, object] = {"run_id": run_id}
            for role in roles:
                result[role] = {"pid": 1, "windows_found": 1, "windows_posted": 1}
            return result

        async def lifecycle_close(run_id: str):
            return await lifecycle_close_roles(run_id, ["client", "server"])

        async def lifecycle_reap(run_id: str):
            runtime.retire_event = "run_reaped"
            return {"ok": True, "run_id": run_id, "state": "EXITED"}

        runtime.lifecycle_status = lifecycle_status
        runtime.lifecycle_close_roles = lifecycle_close_roles
        runtime.lifecycle_close = lifecycle_close
        runtime.lifecycle_reap = lifecycle_reap
        return runtime

    _LOOP_STALL_LIMIT_S = 0.2

    def _assert_loop_responsive(
        self, timer_latency: float, max_tick_gap: float
    ) -> None:
        self.assertLess(
            timer_latency,
            self._LOOP_STALL_LIMIT_S,
            f"first 10 ms timer latency {timer_latency:.3f}s: event loop stalled",
        )
        self.assertLess(
            max_tick_gap,
            self._LOOP_STALL_LIMIT_S,
            f"max tick gap {max_tick_gap:.3f}s: event loop stalled",
        )

    async def _close_with_ticker(
        self, runtime
    ) -> tuple[dict[str, object], float, float, float]:
        ticks: list[float] = []

        async def ticker() -> None:
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        loop = asyncio.get_running_loop()
        timer_at: list[float] = []
        started = time.monotonic()
        loop.call_later(0.01, lambda: timer_at.append(time.monotonic()))
        task = asyncio.create_task(ticker())
        try:
            result = await dayz_test_tool.execute_dayz_test_close(
                runtime, _CLOSE_RUN_ID, graceful_timeout_s=0.6
            )
        finally:
            task.cancel()
        # Include the final instant: a stall after the last tick (e.g.
        # during the server close) is only visible in last-tick -> end.
        ended = time.monotonic()
        elapsed = ended - started
        timer_latency = (
            timer_at[0] - started if timer_at else float("inf")
        )
        points = [started, *ticks, ended]
        max_tick_gap = (
            max(b - a for a, b in zip(points, points[1:]))
            if len(points) > 1
            else float("inf")
        )
        return result, timer_latency, max_tick_gap, elapsed

    async def test_close_busy_no_cache_returns_promptly_and_waits_logout(self) -> None:
        self._write(self.server_rpt, "boot\n")
        self._write(self.client_rpt, "boot client\n")
        runtime = self._runtime(append_logout=False)
        result, timer_latency, max_tick_gap, elapsed = (
            await self._close_with_ticker(runtime)
        )
        # The busy lock never stalls the loop: the 10 ms timer fires
        # promptly and the 10 ms ticker never gaps during close.
        self._assert_loop_responsive(timer_latency, max_tick_gap)
        self.assertLess(elapsed, 3.0)
        self.assertTrue(all(c["exclusive"] is False for c in self.acquire_calls))
        # Conservative default: with no policy the server close still
        # waits the logout budget after the client close.
        self.assertEqual(
            [roles for roles, _at in runtime.close_at], [("client",), ("server",)]
        )
        delta = runtime.close_at[1][1] - runtime.close_at[0][1]
        self.assertGreaterEqual(delta, 0.4)
        self.assertGreaterEqual(float(result["logout_wait_s"]), 0.4)
        # P2: the logout timeout warns even without a policy, where
        # graceful is False (no RPT watches / missing roles).
        self.assertEqual(
            result.get("warnings"), [dayz_test_tool._PLAYER_STATE_NOT_SAVED]
        )

    @unittest.skipUnless(
        os.name == "nt", "registry lock (LockFileEx) is Windows-only"
    )
    async def test_close_busy_with_cache_waits_for_connected_logout(self) -> None:
        self._write(self.server_rpt, "boot\n" + _CLOSE_CONNECT + _CLOSE_LINK)
        self._write(self.client_rpt, "boot client\n")
        launcher_registry._cache_approved_registry([_entry()])
        opened = _FakeOpenedLauncher()

        @contextmanager
        def fake_bundle(_opened):
            yield SimpleNamespace(sealed_policies=object())

        runtime = self._runtime(append_logout=False)
        with (
            mock.patch.object(
                launcher_registry,
                "_read_canonical_registry",
                return_value=[_entry()],
            ),
            mock.patch.object(
                launcher_registry, "_open_validated_entry", return_value=opened
            ),
            mock.patch.object(
                dayz_test_tool.secure_launcher,
                "load_verified_bundle",
                side_effect=fake_bundle,
            ),
            mock.patch.object(
                dayz_test_tool, "_semantic_policies", return_value=[self.policy]
            ),
        ):
            result, timer_latency, max_tick_gap, elapsed = (
                await self._close_with_ticker(runtime)
            )
        self._assert_loop_responsive(timer_latency, max_tick_gap)
        self.assertLess(elapsed, 3.0)
        self.assertTrue(all(c["exclusive"] is False for c in self.acquire_calls))
        self.assertEqual(
            [roles for roles, _at in runtime.close_at], [("client",), ("server",)]
        )
        delta = runtime.close_at[1][1] - runtime.close_at[0][1]
        self.assertGreaterEqual(delta, 0.4)
        self.assertEqual(
            result["logout_players"],
            [{"player": "Ada", "logout_finished": False}],
        )
        self.assertEqual(
            result.get("warnings"), [dayz_test_tool._PLAYER_STATE_NOT_SAVED]
        )

    async def test_close_with_policy_ungraceful_retired_elsewhere_no_warn(
        self,
    ) -> None:
        # FIX 8, other side of the busy-no-cache warning above: with a
        # sealed policy the logout timeout alone must not warn when the
        # close ends ungraceful because the run retired elsewhere.
        self._write(self.server_rpt, "boot\n" + _CLOSE_CONNECT + _CLOSE_LINK)
        self._write(self.client_rpt, "boot client\n")
        launcher_registry._cache_approved_registry([_entry()])
        opened = _FakeOpenedLauncher()

        @contextmanager
        def fake_bundle(_opened):
            yield SimpleNamespace(sealed_policies=object())

        runtime = self._runtime(append_logout=False)

        async def reap_elsewhere(run_id: str):
            runtime.retire_event = "lifecycle_stop_outcome"
            return {"ok": True, "run_id": run_id, "state": "EXITED"}

        runtime.lifecycle_reap = reap_elsewhere
        with (
            mock.patch.object(
                launcher_registry,
                "_read_canonical_registry",
                return_value=[_entry()],
            ),
            mock.patch.object(
                launcher_registry, "_open_validated_entry", return_value=opened
            ),
            mock.patch.object(
                dayz_test_tool.secure_launcher,
                "load_verified_bundle",
                side_effect=fake_bundle,
            ),
            mock.patch.object(
                dayz_test_tool, "_semantic_policies", return_value=[self.policy]
            ),
        ):
            result = await dayz_test_tool.execute_dayz_test_close(
                runtime, _CLOSE_RUN_ID, graceful_timeout_s=0.6
            )
        self.assertFalse(result["graceful"])
        self.assertEqual(result["reason"], "run_retired_elsewhere")
        self.assertGreaterEqual(float(result["logout_wait_s"]), 0.4)
        self.assertNotIn("warnings", result)

    async def test_close_loop_gate_rejects_late_sync_pause(self) -> None:
        # Sol r6 P2: a 1 s synchronous pause during the server close
        # happens after the last ticker tick and after the first timer
        # fired; only the interval to the final instant can catch it,
        # so the gate must fail on the max tick gap alone.
        self._write(self.server_rpt, "boot\n")
        self._write(self.client_rpt, "boot client\n")
        runtime = self._runtime(append_logout=False)
        original_close_roles = runtime.lifecycle_close_roles

        async def late_pause_close(run_id: str, roles: list[str]):
            if list(roles) == ["server"]:
                time.sleep(1.0)
            return await original_close_roles(run_id, roles)

        runtime.lifecycle_close_roles = late_pause_close
        _result, timer_latency, max_tick_gap, _elapsed = (
            await self._close_with_ticker(runtime)
        )
        self.assertLess(timer_latency, self._LOOP_STALL_LIMIT_S)
        self.assertGreaterEqual(max_tick_gap, 0.9)
        with self.assertRaises(AssertionError):
            self._assert_loop_responsive(timer_latency, max_tick_gap)

    async def test_close_graceful_without_timeout_does_not_warn(self) -> None:
        # P3a (:4644): a graceful close whose logout wait did not time
        # out must not publish player_state_not_saved.
        self._write(self.server_rpt, "boot\n" + _CLOSE_CONNECT + _CLOSE_LINK)
        self._write(self.client_rpt, "boot client\n")
        launcher_registry._cache_approved_registry([_entry()])
        opened = _FakeOpenedLauncher()

        @contextmanager
        def fake_bundle(_opened):
            yield SimpleNamespace(sealed_policies=object())

        runtime = self._runtime(append_logout=True)
        with (
            mock.patch.object(
                launcher_registry,
                "_read_canonical_registry",
                return_value=[_entry()],
            ),
            mock.patch.object(
                launcher_registry, "_open_validated_entry", return_value=opened
            ),
            mock.patch.object(
                dayz_test_tool.secure_launcher,
                "load_verified_bundle",
                side_effect=fake_bundle,
            ),
            mock.patch.object(
                dayz_test_tool, "_semantic_policies", return_value=[self.policy]
            ),
        ):
            result = await dayz_test_tool.execute_dayz_test_close(
                runtime, _CLOSE_RUN_ID, graceful_timeout_s=2.0
            )
        self.assertTrue(result["graceful"])
        self.assertIsNone(result["reason"])
        self.assertEqual(
            result["logout_players"],
            [{"player": "Ada", "logout_finished": True}],
        )
        self.assertNotIn("warnings", result)

    async def test_close_role_without_rpt_with_policy_warns_on_timeout(
        self,
    ) -> None:
        # P3c: with a sealed policy, a role_without_rpt close whose
        # logout wait timed out warns; the missing RPT is why no
        # logout could be verified.
        self._write(self.client_rpt, "boot client\n")
        launcher_registry._cache_approved_registry([_entry()])
        opened = _FakeOpenedLauncher()

        @contextmanager
        def fake_bundle(_opened):
            yield SimpleNamespace(sealed_policies=object())

        runtime = self._runtime(append_logout=False)
        with (
            mock.patch.object(
                launcher_registry,
                "_read_canonical_registry",
                return_value=[_entry()],
            ),
            mock.patch.object(
                launcher_registry, "_open_validated_entry", return_value=opened
            ),
            mock.patch.object(
                dayz_test_tool.secure_launcher,
                "load_verified_bundle",
                side_effect=fake_bundle,
            ),
            mock.patch.object(
                dayz_test_tool, "_semantic_policies", return_value=[self.policy]
            ),
        ):
            result = await dayz_test_tool.execute_dayz_test_close(
                runtime, _CLOSE_RUN_ID, graceful_timeout_s=0.6
            )
        self.assertFalse(result["graceful"])
        self.assertEqual(result["reason"], "role_without_rpt")
        self.assertEqual(
            result.get("warnings"), [dayz_test_tool._PLAYER_STATE_NOT_SAVED]
        )

    async def test_close_loop_gate_rejects_injected_sync_pause(self) -> None:
        # P3 negative control: a 1 s synchronous pause on the loop
        # (as a blocking registry call would cause) must fail the
        # responsiveness gate used by the two tests above.
        self._write(self.server_rpt, "boot\n")
        self._write(self.client_rpt, "boot client\n")
        runtime = self._runtime(append_logout=False)

        def blocking_policy(_run: object) -> None:
            time.sleep(1.0)
            return None

        with mock.patch.object(
            dayz_test_tool, "_close_project_policy", side_effect=blocking_policy
        ):
            _result, timer_latency, max_tick_gap, _elapsed = (
                await self._close_with_ticker(runtime)
            )
        self.assertGreaterEqual(timer_latency, 0.9)
        self.assertGreaterEqual(max_tick_gap, 0.9)
        with self.assertRaises(AssertionError):
            self._assert_loop_responsive(timer_latency, max_tick_gap)


class ApprovedRegistryCacheAuditTest(unittest.TestCase):
    """P3a/P3d: cache and try_open contracts of launcher_registry."""

    def setUp(self) -> None:
        self._saved_cache = launcher_registry._cached_approved_registry()
        launcher_registry._clear_approved_registry_cache_for_test()
        self.addCleanup(self._restore_cache)

    def _restore_cache(self) -> None:
        launcher_registry._clear_approved_registry_cache_for_test()
        if self._saved_cache is not None:
            launcher_registry._cache_approved_registry(self._saved_cache)

    def test_try_open_non_busy_error_propagates_with_cache_present(self) -> None:
        # :357: only launcher_registry_busy may fall back to the cache.
        launcher_registry._cache_approved_registry([_entry()])
        calls: list = []

        def invalid_acquire(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("invalid_launcher_registry_lock")

        with (
            mock.patch.object(
                registry_lock, "acquire_registry_lock", invalid_acquire
            ),
            mock.patch.object(
                launcher_registry, "_open_validated_entry"
            ) as open_entry,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "^invalid_launcher_registry_lock$"
            ):
                launcher_registry.try_open_approved_launcher("dayz-test-v1")
        self.assertEqual(len(calls), 1)
        open_entry.assert_not_called()

    def test_try_open_revoked_launcher_is_not_opened_from_cache(self) -> None:
        # P3d: cached, then revoked from the canonical registry; with
        # the lock busy the stale cache must not open it.
        launcher_registry._cache_approved_registry([_entry()])
        with (
            mock.patch.object(
                registry_lock, "acquire_registry_lock", _busy_acquire([])
            ),
            mock.patch.object(
                launcher_registry, "_read_canonical_registry", return_value=[]
            ),
            mock.patch.object(
                launcher_registry, "_open_validated_entry"
            ) as open_entry,
        ):
            with self.assertRaisesRegex(ValueError, "^launcher_not_approved$"):
                launcher_registry.try_open_approved_launcher("dayz-test-v1")
        open_entry.assert_not_called()

    def test_try_open_changed_entry_opens_current_not_stale_cache(self) -> None:
        # P3d: the verified current entry wins over the cached one.
        launcher_registry._cache_approved_registry([_entry("A" * 64)])
        opened = _FakeOpenedLauncher()
        seen: list = []

        def capture(entry):
            seen.append(entry)
            return opened

        with (
            mock.patch.object(
                registry_lock, "acquire_registry_lock", _busy_acquire([])
            ),
            mock.patch.object(
                launcher_registry,
                "_read_canonical_registry",
                return_value=[_entry("B" * 64)],
            ),
            mock.patch.object(
                launcher_registry, "_open_validated_entry", side_effect=capture
            ),
        ):
            result = launcher_registry.try_open_approved_launcher("dayz-test-v1")
        self.assertIs(result, opened)
        self.assertEqual(seen, [_entry("B" * 64)])
        self.assertEqual(
            launcher_registry._cached_approved_registry(), [_entry("B" * 64)]
        )

    def test_try_open_unverifiable_cache_propagates_busy(self) -> None:
        # P3d fail-closed: cache present but the current registry
        # cannot be read, so the busy error propagates unverified.
        launcher_registry._cache_approved_registry([_entry()])
        with (
            mock.patch.object(
                registry_lock, "acquire_registry_lock", _busy_acquire([])
            ),
            mock.patch.object(
                launcher_registry,
                "_read_canonical_registry",
                side_effect=ValueError("invalid_launcher_registry"),
            ),
            mock.patch.object(
                launcher_registry, "_open_validated_entry"
            ) as open_entry,
        ):
            with self.assertRaisesRegex(RuntimeError, "^launcher_registry_busy$"):
                launcher_registry.try_open_approved_launcher("dayz-test-v1")
        open_entry.assert_not_called()

    def test_blocking_open_fills_the_cache(self) -> None:
        # :330: open_approved_launcher caches the registry it read.
        fake_lock = _FakeLock()
        opened = _FakeOpenedLauncher()
        with (
            mock.patch.object(
                registry_lock,
                "acquire_registry_lock_with_retry",
                return_value=fake_lock,
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
        self.assertEqual(launcher_registry._cached_approved_registry(), [_entry()])

    def test_cache_is_deep_copied_on_store_and_read(self) -> None:
        # :300/:306: neither the stored list nor a read copy can
        # mutate the cached registry.
        stored = [_entry()]
        launcher_registry._cache_approved_registry(stored)
        stored[0]["sha256"] = "F" * 64
        stored.append(_entry())
        first = launcher_registry._cached_approved_registry()
        self.assertEqual(first, [_entry()])
        first[0]["root_file_id"]["file_id"] = "F" * 32  # type: ignore[index]
        first.append(_entry())
        self.assertEqual(launcher_registry._cached_approved_registry(), [_entry()])

    def test_tool_module_uses_the_async_variant(self) -> None:
        self.assertIs(
            dayz_test_tool.open_approved_launcher_async,
            launcher_registry.open_approved_launcher_async,
        )

    def test_duplicate_id_is_not_approved(self) -> None:
        # :321: exactly one match is required.
        with mock.patch.object(
            launcher_registry, "_open_validated_entry"
        ) as open_entry:
            with self.assertRaisesRegex(ValueError, "^launcher_not_approved$"):
                launcher_registry._open_from_approved(
                    [_entry(), _entry()], "dayz-test-v1"
                )
        open_entry.assert_not_called()


class _FakeAsyncClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class AsyncApprovedLauncherTest(unittest.IsolatedAsyncioTestCase):
    """P2/FIX 9: the async registry open never sleeps on the loop."""

    def setUp(self) -> None:
        self._saved_cache = launcher_registry._cached_approved_registry()
        launcher_registry._clear_approved_registry_cache_for_test()
        self.addCleanup(self._restore_cache)

    def _restore_cache(self) -> None:
        launcher_registry._clear_approved_registry_cache_for_test()
        if self._saved_cache is not None:
            launcher_registry._cache_approved_registry(self._saved_cache)

    async def test_async_open_retries_busy_with_awaited_backoff(self) -> None:
        clock = _FakeAsyncClock()
        fake_lock = _FakeLock()
        opened = _FakeOpenedLauncher()
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
            async with launcher_registry.open_approved_launcher_async(
                "dayz-test-v1",
                deadline_s=5.0,
                initial_backoff_s=0.02,
                max_backoff_s=0.1,
                sleep_fn=clock.sleep,
                monotonic_fn=clock.monotonic,
            ) as result:
                self.assertIs(result, opened)
                self.assertIs(opened._registry_lock, fake_lock)  # type: ignore[attr-defined]
        self.assertTrue(opened.closed)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(c["exclusive"] is False for c in calls))
        self.assertEqual(clock.sleeps, [0.02, 0.04])
        self.assertEqual(launcher_registry._cached_approved_registry(), [_entry()])

    async def test_async_open_busy_past_deadline_times_out(self) -> None:
        clock = _FakeAsyncClock()
        calls: list = []
        with mock.patch.object(
            registry_lock, "acquire_registry_lock", _always_busy(calls)
        ):
            with self.assertRaisesRegex(
                RuntimeError, "launcher_registry_busy_timeout"
            ):
                async with launcher_registry.open_approved_launcher_async(
                    "dayz-test-v1",
                    deadline_s=1.0,
                    initial_backoff_s=0.4,
                    max_backoff_s=0.5,
                    sleep_fn=clock.sleep,
                    monotonic_fn=clock.monotonic,
                ):
                    pass
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(clock.sleeps), 3)
        for got, want in zip(clock.sleeps, [0.4, 0.5, 0.1]):
            self.assertAlmostEqual(got, want)

    async def test_async_open_non_busy_error_is_not_retried(self) -> None:
        clock = _FakeAsyncClock()
        calls: list = []

        def invalid_acquire(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("invalid_launcher_registry_lock")

        with mock.patch.object(
            registry_lock, "acquire_registry_lock", invalid_acquire
        ):
            with self.assertRaisesRegex(
                RuntimeError, "^invalid_launcher_registry_lock$"
            ):
                async with launcher_registry.open_approved_launcher_async(
                    "dayz-test-v1",
                    sleep_fn=clock.sleep,
                    monotonic_fn=clock.monotonic,
                ):
                    pass
        self.assertEqual(len(calls), 1)
        self.assertEqual(clock.sleeps, [])

    async def test_async_open_releases_lock_when_open_fails(self) -> None:
        fake_lock = _FakeLock()
        with (
            mock.patch.object(
                registry_lock, "acquire_registry_lock", return_value=fake_lock
            ),
            mock.patch.object(
                launcher_registry,
                "_read_canonical_registry",
                return_value=[_entry()],
            ),
            mock.patch.object(
                launcher_registry,
                "_open_validated_entry",
                side_effect=ValueError("launcher_not_approved"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "launcher_not_approved"):
                async with launcher_registry.open_approved_launcher_async(
                    "dayz-test-v1"
                ):
                    pass
        self.assertTrue(fake_lock.closed)

    async def test_async_open_rejects_invalid_retry_parameters(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_registry_lock_retry"):
            async with launcher_registry.open_approved_launcher_async(
                "dayz-test-v1", deadline_s=-1.0
            ):
                pass

    async def _open_with_ticker(self, open_coro_factory):
        ticks: list[float] = []

        async def ticker() -> None:
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        started = time.monotonic()
        task = asyncio.create_task(ticker())
        try:
            await open_coro_factory()
        finally:
            task.cancel()
        ended = time.monotonic()
        points = [started, *ticks, ended]
        max_gap = max(b - a for a, b in zip(points, points[1:]))
        return max_gap, ended - started

    async def test_async_open_keeps_loop_responsive_while_busy(self) -> None:
        # P2: with the real asyncio.sleep, a registry busy for ~0.35 s
        # is retried without freezing the loop, final instant included.
        fake_lock = _FakeLock()
        opened = _FakeOpenedLauncher()
        release_at = time.monotonic() + 0.35
        calls: list = []

        def busy_until_release(**kwargs):
            calls.append(kwargs)
            if time.monotonic() < release_at:
                raise RuntimeError("launcher_registry_busy")
            return fake_lock

        async def open_it() -> None:
            with (
                mock.patch.object(
                    registry_lock,
                    "acquire_registry_lock",
                    busy_until_release,
                ),
                mock.patch.object(
                    launcher_registry,
                    "_read_canonical_registry",
                    return_value=[_entry()],
                ),
                mock.patch.object(
                    launcher_registry,
                    "_open_validated_entry",
                    return_value=opened,
                ),
            ):
                async with launcher_registry.open_approved_launcher_async(
                    "dayz-test-v1"
                ) as result:
                    self.assertIs(result, opened)

        max_gap, elapsed = await self._open_with_ticker(open_it)
        self.assertGreaterEqual(len(calls), 2)
        self.assertGreaterEqual(elapsed, 0.3)
        self.assertLess(
            max_gap, 0.2, f"max tick gap {max_gap:.3f}s: event loop stalled"
        )

    async def test_blocking_open_would_stall_the_loop(self) -> None:
        # P2 negative control: the old blocking retry (time.sleep)
        # freezes the loop for its whole wait and fails the same gate.
        def blocking_retry(**_kwargs):
            time.sleep(1.0)
            raise RuntimeError("launcher_registry_busy_timeout")

        async def open_it() -> None:
            with mock.patch.object(
                registry_lock,
                "acquire_registry_lock_with_retry",
                blocking_retry,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "launcher_registry_busy_timeout"
                ):
                    launcher_registry.open_approved_launcher("dayz-test-v1")

        max_gap, _elapsed = await self._open_with_ticker(open_it)
        self.assertGreaterEqual(max_gap, 0.9)


if __name__ == "__main__":
    unittest.main()
