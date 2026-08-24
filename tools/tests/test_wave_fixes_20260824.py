"""Coverage for the 2026-08-24 wave fixes: entities_query reliability annotation
(fb-...-638e), object_id targeting contract (fb-...-ecf5), crash-log exclusion
from the launch scan (fb-...-413a) and subst-aware same_path (fb-...-9287)."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dayz_mcp import server
from dayz_mcp.native_process_snapshot import same_path
from dayz_mcp.server import (
    ENTITIES_QUERY_BUBBLE_M,
    ToolError,
    _annotate_entities_reliability,
    _current_launch_logs,
    _object_target_args,
)


class AnnotateEntitiesReliabilityTest(unittest.TestCase):
    def test_player_inside_bubble_marks_trustworthy(self) -> None:
        result = {"ok": 1, "count_total": 3, "entities": []}
        players = {"ok": 1, "players": [{"uid": "1", "pos": [100.0, 0.0, 0.0]}]}
        out = _annotate_entities_reliability(result, players, [0.0, 0.0, 0.0])
        self.assertEqual(out["reliability"], "player_in_bubble")
        self.assertEqual(out["nearest_player_m"], 100.0)

    def test_player_beyond_bubble_marks_remote(self) -> None:
        far = ENTITIES_QUERY_BUBBLE_M + 50.0
        result = {"ok": 1, "count_total": 0, "entities": []}
        players = {"ok": 1, "players": [{"uid": "1", "pos": [far, 0.0, 0.0]}]}
        out = _annotate_entities_reliability(result, players, [0.0, 0.0, 0.0])
        self.assertEqual(out["reliability"], "remote_unverified")
        self.assertEqual(out["nearest_player_m"], far)

    def test_nearest_of_many_players_wins(self) -> None:
        result = {"ok": 1}
        players = {
            "ok": 1,
            "players": [
                {"uid": "far", "pos": [1000.0, 0.0, 0.0]},
                {"uid": "near", "pos": [0.0, 30.0, 40.0]},
            ],
        }
        out = _annotate_entities_reliability(result, players, [0.0, 0.0, 0.0])
        self.assertEqual(out["nearest_player_m"], 50.0)
        self.assertEqual(out["reliability"], "player_in_bubble")

    def test_no_players_is_remote_with_null_distance(self) -> None:
        result = {"ok": 1}
        out = _annotate_entities_reliability(result, {"ok": 1, "players": []}, [0.0, 0.0, 0.0])
        self.assertIsNone(out["nearest_player_m"])
        self.assertEqual(out["reliability"], "remote_unverified")

    def test_failed_players_probe_is_remote_not_a_crash(self) -> None:
        result = {"ok": 1}
        out = _annotate_entities_reliability(result, {"ok": 0, "error": "x"}, [0.0, 0.0, 0.0])
        self.assertIsNone(out["nearest_player_m"])
        self.assertEqual(out["reliability"], "remote_unverified")

    def test_failed_query_passes_through_untouched(self) -> None:
        result = {"ok": 0, "error": "bad_args"}
        out = _annotate_entities_reliability(result, {"ok": 1, "players": []}, [0.0, 0.0, 0.0])
        self.assertNotIn("reliability", out)
        self.assertNotIn("nearest_player_m", out)


class ObjectTargetArgsTest(unittest.TestCase):
    def test_object_id_alone_targets_the_registry(self) -> None:
        self.assertEqual(_object_target_args("", None, 7), {"object_id": 7})

    def test_object_id_wins_even_with_type_present(self) -> None:
        self.assertEqual(
            _object_target_args("CivilianSedan", [1.0, 2.0, 3.0], 7),
            {"object_id": 7},
        )

    def test_classname_path_requires_type_and_pos(self) -> None:
        out = _object_target_args("CivilianSedan", [1.0, 2.0, 3.0], 0)
        self.assertEqual(out, {"type": "CivilianSedan", "pos": [1.0, 2.0, 3.0]})

    def test_rejections(self) -> None:
        with self.assertRaises(ToolError):
            _object_target_args("", None, 0)
        with self.assertRaises(ToolError):
            _object_target_args("CivilianSedan", [1.0, 2.0, 3.0], -1)
        with self.assertRaises(ToolError):
            _object_target_args("CivilianSedan", [1.0, 2.0, 3.0], True)
        with self.assertRaises(ToolError):
            _object_target_args("X", [1.0, 2.0], 0)


class CurrentLaunchLogsCrashExclusionTest(unittest.TestCase):
    def _touch(self, directory: str, name: str) -> str:
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("line\n")
        return path

    def test_crash_dumps_stay_out_of_the_launch_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rpt = self._touch(tmp, "DayZDiag_x64_2026-08-24_18-00-00.RPT")
            script = self._touch(tmp, "script_2026-08-24_18-00-00.log")
            self._touch(tmp, "crash_2026-08-24_18-00-00.log")
            selected = _current_launch_logs(tmp, time.time() - 30.0)
            names = sorted(Path(item).name for item in selected)
            self.assertEqual(
                names,
                sorted(Path(item).name for item in (rpt, script)),
            )

    def test_fallback_without_epoch_also_excludes_crash_dumps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rpt = self._touch(tmp, "DayZDiag_x64_2026-08-24_18-00-00.RPT")
            self._touch(tmp, "crash_2026-08-24_19-00-00.log")
            script = self._touch(tmp, "script_2026-08-24_18-00-00.log")
            selected = _current_launch_logs(tmp, None)
            names = sorted(Path(item).name for item in selected)
            self.assertEqual(
                names,
                sorted(Path(item).name for item in (rpt, script)),
            )


class SamePathSubstTest(unittest.TestCase):
    def test_two_spellings_of_one_file_compare_equal(self) -> None:
        # A hardlink gives one file two normpath-distinct names, the same
        # situation a subst P: view creates for the venv python.
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "python.exe")
            with open(first, "w", encoding="utf-8") as handle:
                handle.write("x")
            second = os.path.join(tmp, "alias.exe")
            try:
                os.link(first, second)
            except OSError as error:
                self.skipTest(f"hardlinks unavailable: {error}")
            self.assertTrue(same_path(first, second))

    def test_distinct_files_still_compare_different(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "a.exe")
            second = os.path.join(tmp, "b.exe")
            for path in (first, second):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x")
            self.assertFalse(same_path(first, second))

    def test_nonexistent_paths_fall_back_to_string_compare(self) -> None:
        self.assertTrue(same_path(r"C:\missing\x.exe", r"c:\missing\X.EXE"))
        self.assertFalse(same_path(r"C:\missing\x.exe", r"C:\missing\y.exe"))


class EntitiesQueryCompositeTest(unittest.IsolatedAsyncioTestCase):
    async def test_query_is_followed_by_a_players_probe(self) -> None:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        responses = [
            {"ok": 1, "count_total": 2, "entities": []},
            {"ok": 1, "players": [{"uid": "1", "pos": [4200.0, 339.0, 10650.0]}]},
        ]
        with patch.object(
            runtime, "call_bridge", new=AsyncMock(side_effect=responses)
        ) as call:
            await app.call_tool(
                "entities_query",
                {"pos": [4200.0, 339.0, 10650.0], "radius": 65.0, "timeout_s": 1.0},
            )
        awaited = [item.args[0] for item in call.await_args_list]
        self.assertEqual(awaited, ["entities_query", "query_all_players"])


if __name__ == "__main__":
    unittest.main()
