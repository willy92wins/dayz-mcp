"""Contrato focal de M16 para el preflight Steam sin tocar el host."""

from __future__ import annotations

from dataclasses import fields
import unittest

from dayz_mcp.steam_preflight import (
    REMEDIATION,
    STEAM_SESSION_STALE,
    SteamActiveProcessSnapshot,
    evaluate_steam_session,
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


if __name__ == "__main__":
    unittest.main()
