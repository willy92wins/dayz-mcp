"""Contrato focal de M16 para el preflight Steam sin tocar el host."""

from __future__ import annotations

from dataclasses import fields
import unittest

from collections.abc import Callable

from dayz_mcp.steam_preflight import (
    REMEDIATION,
    STEAM_SESSION_STALE,
    SteamActiveProcessSnapshot,
    evaluate_steam_session,
    remediate_stale_steam_session,
)


class FakeSteamProvider:
    def __init__(
        self,
        snapshots: list[object],
        *,
        existing: set[int] | None = None,
        images: dict[int, object] | None = None,
        steam_pids: object = (),
    ) -> None:
        self._snapshots = iter(snapshots)
        self._existing = set() if existing is None else existing
        self._images = {} if images is None else images
        self._steam_pids = steam_pids

    def read_active_process(self) -> object:
        snapshot = next(self._snapshots)
        if isinstance(snapshot, BaseException):
            raise snapshot
        return snapshot

    def process_exists(self, pid: int) -> bool:
        return pid in self._existing

    def process_image_path(self, pid: int) -> object:
        return self._images[pid]

    def steam_process_pids(self) -> object:
        if isinstance(self._steam_pids, BaseException):
            raise self._steam_pids
        return self._steam_pids


def active_process(pid: object = 41, active_user: object = 7) -> SteamActiveProcessSnapshot:
    return SteamActiveProcessSnapshot(pid=pid, active_user=active_user)


class SteamPreflightTests(unittest.TestCase):
    def assert_stale(self, result: object, registered_pid: object = None) -> None:
        self.assertEqual(result.error_code, STEAM_SESSION_STALE)
        self.assertEqual(result.steam_registered_pid, registered_pid)
        self.assertEqual(result.remediation, REMEDIATION)

    def test_accepts_only_registered_live_steam_process_with_active_user(self) -> None:
        provider = FakeSteamProvider(
            [active_process(), active_process()],
            existing={41},
            images={41: r"C:\\Steam\\steam.EXE"},
            steam_pids=(41, 9, 9),
        )

        result = evaluate_steam_session(provider)

        self.assertIsNone(result.error_code)
        self.assertEqual(result.steam_registered_pid, 41)
        self.assertEqual(result.steam_live_pids, (9, 41))
        self.assertEqual(result.remediation, REMEDIATION)

    def test_rejects_gone_registered_pid_even_when_another_steam_is_live(self) -> None:
        provider = FakeSteamProvider(
            [active_process(41), active_process(41)],
            existing={9},
            images={9: r"C:\\Steam\\steam.exe"},
            steam_pids=(9,),
        )

        result = evaluate_steam_session(provider)

        self.assert_stale(result, 41)
        self.assertEqual(result.steam_live_pids, (9,))

    def test_rejects_registered_process_with_non_steam_basename(self) -> None:
        provider = FakeSteamProvider(
            [active_process(41), active_process(41)],
            existing={41},
            images={41: r"C:\\Steam\\steamwebhelper.exe"},
            steam_pids=(9,),
        )

        result = evaluate_steam_session(provider)

        self.assert_stale(result, 41)

    def test_rejects_absent_or_zero_active_user(self) -> None:
        for snapshot in (active_process(41, None), active_process(41, 0)):
            with self.subTest(snapshot=snapshot):
                result = evaluate_steam_session(
                    FakeSteamProvider(
                        [snapshot, snapshot],
                        existing={41},
                        images={41: r"C:\\Steam\\steam.exe"},
                    )
                )
                self.assert_stale(result, 41)

    def test_rejects_bool_and_string_registry_values(self) -> None:
        for snapshots, registered_pid in (
            ((active_process(True, 7), active_process(True, 7)), None),
            ((active_process("41", 7), active_process("41", 7)), None),
            ((active_process(41, True), active_process(41, True)), 41),
            ((active_process(41, "7"), active_process(41, "7")), 41),
        ):
            with self.subTest(snapshots=snapshots):
                result = evaluate_steam_session(
                    FakeSteamProvider(
                        list(snapshots),
                        existing={41},
                        images={41: r"C:\\Steam\\steam.exe"},
                        steam_pids=(41,),
                    )
                )
                self.assert_stale(result, registered_pid)

    def test_rejects_registry_access_denied_and_partial_or_changing_snapshots(self) -> None:
        denied = evaluate_steam_session(FakeSteamProvider([PermissionError("denied")]))
        partial = evaluate_steam_session(
            FakeSteamProvider([active_process(41, None), active_process(41, None)])
        )
        changing = evaluate_steam_session(
            FakeSteamProvider([active_process(41), active_process(42)])
        )

        self.assert_stale(denied)
        self.assert_stale(partial, 41)
        self.assert_stale(changing)

    def test_bounds_and_deduplicates_live_steam_pids(self) -> None:
        provider = FakeSteamProvider(
            [active_process(41), active_process(41)],
            existing={41},
            images={41: r"C:\\Steam\\steam.exe"},
            steam_pids=(18, 5, 12, 3, 10, 1, 7, 6, 15, 2, 5, 18),
        )

        result = evaluate_steam_session(provider)

        self.assertEqual(result.steam_live_pids, (1, 2, 3, 5, 6, 7, 10, 12))

    def test_public_result_has_only_safe_contract_fields(self) -> None:
        result = evaluate_steam_session(
            FakeSteamProvider(
                [active_process(41, 123456), active_process(41, 123456)],
                existing={41},
                images={41: r"C:\\Users\\secret\\Steam\\steam.exe"},
                steam_pids=(41,),
            )
        )

        self.assertEqual(
            [field.name for field in fields(result)],
            ["error_code", "steam_registered_pid", "steam_live_pids", "remediation"],
        )
        self.assertNotIn("secret", repr(result))
        self.assertNotIn("123456", repr(result))



    def test_rejects_failed_or_malformed_process_enumeration(self) -> None:
        cases = [
            RuntimeError("snapshot failed"),
            (0, 41),
            (-5, 41),
            ("41", 41),
            (None, 41),
        ]
        for steam_pids in cases:
            with self.subTest(steam_pids=steam_pids):
                result = evaluate_steam_session(
                    FakeSteamProvider(
                        [active_process(), active_process()],
                        existing={41},
                        images={41: r"C:\\Steam\\steam.exe"},
                        steam_pids=steam_pids,
                    )
                )
                self.assert_stale(result, 41)
                self.assertEqual(result.steam_live_pids, ())

    def test_rejects_when_liveness_or_image_probe_raises(self) -> None:
        class ExplodingProvider(FakeSteamProvider):
            def __init__(self, explode: str, **kwargs) -> None:
                super().__init__(**kwargs)
                self._explode = explode

            def process_exists(self, pid: int) -> bool:
                if self._explode == "exists":
                    raise OSError("probe failed")
                return super().process_exists(pid)

            def process_image_path(self, pid: int) -> object:
                if self._explode == "image":
                    raise OSError("image failed")
                return super().process_image_path(pid)

        for explode in ("exists", "image"):
            with self.subTest(explode=explode):
                result = evaluate_steam_session(
                    ExplodingProvider(
                        explode,
                        snapshots=[active_process(), active_process()],
                        existing={41},
                        images={41: r"C:\\Steam\\steam.exe"},
                        steam_pids=(41,),
                    )
                )
                self.assert_stale(result, 41)

    def test_rejects_non_string_image_path(self) -> None:
        for image in (None, 41, b"C:\\Steam\\steam.exe"):
            with self.subTest(image=image):
                result = evaluate_steam_session(
                    FakeSteamProvider(
                        [active_process(), active_process()],
                        existing={41},
                        images={41: image},
                        steam_pids=(41,),
                    )
                )
                self.assert_stale(result, 41)

    def test_unaccreditable_registered_pid_is_null_and_stale(self) -> None:
        for pid in (0, -3, None):
            with self.subTest(pid=pid):
                result = evaluate_steam_session(
                    FakeSteamProvider(
                        [active_process(pid), active_process(pid)],
                        existing={41},
                        images={41: r"C:\\Steam\\steam.exe"},
                        steam_pids=(41,),
                    )
                )
                self.assert_stale(result, None)
                self.assertEqual(result.steam_live_pids, (41,))

    def test_pass_keeps_empty_or_foreign_enumeration_out_of_the_verdict(self) -> None:
        empty = evaluate_steam_session(
            FakeSteamProvider(
                [active_process(7, 3), active_process(7, 3)],
                existing={7},
                images={7: r"C:\\Steam\\steam.exe"},
                steam_pids=(),
            )
        )
        self.assertIsNone(empty.error_code)
        self.assertEqual(empty.steam_registered_pid, 7)
        self.assertEqual(empty.steam_live_pids, ())
        self.assertEqual(empty.remediation, REMEDIATION)

        foreign = evaluate_steam_session(
            FakeSteamProvider(
                [active_process(7, 3), active_process(7, 3)],
                existing={7},
                images={7: r"C:\\Steam\\steam.exe"},
                steam_pids=(99, 100),
            )
        )
        self.assertIsNone(foreign.error_code)
        self.assertEqual(foreign.steam_live_pids, (99, 100))


_STEAM_EXE = r"C:\Steam\steam.exe"
_SHUTDOWN_BUDGET_S = 15.0


class _MutableSteamProvider:
    """In-memory Steam session whose live-pid list the host can mutate."""

    def __init__(self, *, steam_pids: object = (41,)) -> None:
        self.pid = 41
        self.active_user = 7
        self.existing: set[int] = {41}
        self.images: dict[int, object] = {41: _STEAM_EXE}
        self.steam_pids = steam_pids

    def read_active_process(self) -> SteamActiveProcessSnapshot:
        return SteamActiveProcessSnapshot(pid=self.pid, active_user=self.active_user)

    def process_exists(self, pid: int) -> bool:
        return pid in self.existing

    def process_image_path(self, pid: int) -> object:
        return self.images[pid]

    def steam_process_pids(self) -> object:
        value = self.steam_pids
        if isinstance(value, BaseException):
            raise value
        return value

    def steam_startup_complete(self, pid: int) -> bool:
        # These existing fixtures model a fully initialized Steam client.
        return True

    def shut_down(self) -> None:
        self.steam_pids = ()
        self.existing.clear()

    def come_back(self) -> None:
        self.steam_pids = (self.pid,)
        self.existing = {self.pid}


class _FakeRemediationHost:
    """Records steam.exe invocations; never touches a real process."""

    def __init__(
        self,
        executable: str = _STEAM_EXE,
        *,
        silent_errors: int = 0,
    ) -> None:
        self._executable = executable
        self.invocations: list[tuple[str, tuple[str, ...]]] = []
        self._now = 0.0
        self.monotonic_calls = 0
        self._silent_errors = silent_errors
        self.on_invoke: Callable[[tuple[str, ...]], None] | None = None

    def steam_executable(self) -> str | None:
        return self._executable

    def invoke_steam(self, executable: str, extra_args: tuple[str, ...]) -> None:
        self.invocations.append((executable, extra_args))
        if extra_args == ("-silent",) and self._silent_errors:
            self._silent_errors -= 1
            raise OSError("silent launch failed")
        if self.on_invoke is not None:
            self.on_invoke(extra_args)

    def monotonic(self) -> float:
        self.monotonic_calls += 1
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += float(seconds)

    def extra_args(self) -> list[tuple[str, ...]]:
        return [args for _executable, args in self.invocations]


class SteamRemediationHostTest(unittest.TestCase):
    def test_a_clean_shutdown_and_relaunch_reports_success(self) -> None:
        provider = _MutableSteamProvider()
        host = _FakeRemediationHost()

        def _on_invoke(extra_args: tuple[str, ...]) -> None:
            if extra_args == ("-shutdown",):
                provider.shut_down()
            elif extra_args == ("-silent",):
                provider.come_back()

        host.on_invoke = _on_invoke

        result = remediate_stale_steam_session(provider, host)

        self.assertIsNone(result.error_code)
        self.assertEqual(result.steam_registered_pid, 41)
        self.assertIs(result.steam_left_down, False)
        self.assertEqual(host.extra_args(), [("-shutdown",), ("-silent",)])
        self.assertGreater(host.monotonic_calls, 0)

    def test_a_shutdown_that_never_finishes_does_not_relaunch(self) -> None:
        provider = _MutableSteamProvider()
        provider.existing = set()
        provider.steam_pids = (99,)
        provider.images[99] = _STEAM_EXE
        host = _FakeRemediationHost()

        result = remediate_stale_steam_session(provider, host)

        self.assertEqual(result.error_code, STEAM_SESSION_STALE)
        self.assertEqual(host.extra_args(), [("-shutdown",)])
        self.assertGreaterEqual(host._now, _SHUTDOWN_BUDGET_S)
        self.assertIs(getattr(result, "steam_left_down", False), False)

    def test_a_relaunch_that_fails_reports_steam_left_down(self) -> None:
        provider = _MutableSteamProvider()
        host = _FakeRemediationHost(silent_errors=8)

        def _on_invoke(extra_args: tuple[str, ...]) -> None:
            if extra_args == ("-shutdown",):
                provider.shut_down()

        host.on_invoke = _on_invoke

        result = remediate_stale_steam_session(provider, host)

        self.assertEqual(result.error_code, STEAM_SESSION_STALE)
        self.assertIs(result.steam_left_down, True)
        self.assertEqual(host.extra_args()[0], ("-shutdown",))
        self.assertIn(("-silent",), host.extra_args())
        self.assertEqual(provider.steam_pids, ())

    def test_an_unreadable_process_list_is_not_read_as_shut_down(self) -> None:
        provider = _MutableSteamProvider(steam_pids=RuntimeError("snapshot failed"))
        host = _FakeRemediationHost()

        result = remediate_stale_steam_session(provider, host)

        self.assertEqual(result.error_code, STEAM_SESSION_STALE)
        self.assertNotIn(("-silent",), host.extra_args())
        self.assertLess(host._now, _SHUTDOWN_BUDGET_S)
        self.assertIs(getattr(result, "steam_left_down", False), False)


if __name__ == "__main__":
    unittest.main()
