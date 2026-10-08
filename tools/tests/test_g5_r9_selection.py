"""g5-r9 selection consistency and exec-enforce audit naming.

Each test is a regression for one specification bullet and fails when that
bullet's entry point still has the old behavior.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from dayz_mcp.server_cli import (
    bind_instance_context,
    current_game_path,
    current_instance_token,
    reset_instance_context_for_tests,
)


class SelectionConsistencyTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_instance_context_for_tests()

    def test_m2_entry_selectors_use_shared_validation_before_effect(self) -> None:
        from dayz_mcp import knowledge_pack, stdio_bridge

        with mock.patch("dayz_mcp.knowledge_pack.install_knowledge_pack") as install:
            for argv in (
                ["install", "--instance=130"],
                ["install", "--inst", "130"],
                ["install", "--instance", "130", "--instance", "131"],
                ["install", "--instance", "Bad"],
            ):
                code = knowledge_pack.main(argv)
                self.assertEqual(code, 2, argv)
                install.assert_not_called()

        with mock.patch("dayz_mcp.stdio_bridge.official_client_argv") as build:
            for argv in (
                ["--instance=130"],
                ["--instance", "130", "--instance", "131"],
                ["--game-path", "relative\\DayZ"],
            ):
                code = stdio_bridge.main(argv)
                self.assertEqual(code, 2, argv)
                build.assert_not_called()
            with self.assertRaises(SystemExit):
                stdio_bridge.main(["--inst", "130"])
            build.assert_not_called()

    def test_m3_server_client_and_embedded_reject_environment_conflict(self) -> None:
        from dayz_mcp.server import parse_args

        cases = (
            ([], {"DAYZ_MCP_INSTANCE": "other"}, ["--instance", "130"]),
            (["--client"], {"DAYZ_MCP_PORT": "9999"}, ["--port", "8765"]),
            (
                ["--embedded"],
                {"DAYZ_MCP_GAME_PATH": r"C:\Other\DayZ"},
                ["--game-path", r"C:\Games\DayZ"],
            ),
        )
        for mode, conflict, selector in cases:
            reset_instance_context_for_tests()
            with mock.patch.dict(os.environ, conflict, clear=False):
                for name in ("DAYZ_MCP_INSTANCE", "DAYZ_MCP_PORT", "DAYZ_MCP_GAME_PATH"):
                    if name not in conflict:
                        os.environ.pop(name, None)
                with self.assertRaises(SystemExit) as caught:
                    parse_args(["--keyfile", "K", *mode, *selector])
            self.assertEqual(caught.exception.code, 2, mode or ["embedded-default"])

    def test_m4_doctor_diagnoses_the_selected_instance(self) -> None:
        from dayz_mcp import doctor

        seen: dict[str, object] = {}

        def execute(**_kwargs: object) -> tuple[dict[str, object], int]:
            seen["token"] = current_instance_token()
            seen["game"] = current_game_path()
            return {"ok": True, "findings": [], "summary": {"fail": 0, "warn": 0}}, 0

        with (
            mock.patch(
                "dayz_mcp.doctor.daemon_policy.load_daemon_policy",
                return_value=object(),
            ),
            mock.patch("dayz_mcp.doctor.execute", side_effect=execute),
        ):
            code = doctor.main(
                [
                    "--daemon-policy",
                    "normal",
                    "--json",
                    "--instance",
                    "130",
                    "--game-path",
                    r"C:\Games\DayZ",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(seen["token"], "130")
        self.assertEqual(seen["game"], os.path.normpath(r"C:\Games\DayZ"))

        with mock.patch("dayz_mcp.doctor.execute") as execute_mock:
            code = doctor.main(
                ["--daemon-policy", "normal", "--json", "--instance=130"]
            )
        self.assertEqual(code, 2)
        execute_mock.assert_not_called()

    def test_m5_selection_error_does_not_write_the_default_sidecar(self) -> None:
        import mcp_capture

        reset_instance_context_for_tests()
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": temporary}, clear=False):
                os.environ.pop("DAYZ_MCP_FRAME_STATE_PATH", None)
                with mock.patch.object(sys, "argv", ["mcp", "--instance", "NOTVALID"]):
                    report = mcp_capture._frame_stale_report("window-key", "window", "ab", {})
            default_sidecar = Path(temporary) / "DayZ_MCP" / "capture-frame-state.json"
            self.assertFalse(default_sidecar.exists())
            self.assertFalse((Path(temporary) / "DayZ_MCP").exists())
            self.assertEqual(report["detail"]["selection_error"], "invalid_instance_token")

    def test_f1_override_sidecar_still_requires_a_valid_selector(self) -> None:
        import mcp_capture

        reset_instance_context_for_tests()
        with tempfile.TemporaryDirectory() as temporary:
            override = Path(temporary) / "DayZ_MCP" / "capture-frame-state.json"
            with mock.patch.dict(
                os.environ,
                {"DAYZ_MCP_FRAME_STATE_PATH": str(override), "LOCALAPPDATA": temporary},
                clear=False,
            ):
                with mock.patch.object(sys, "argv", ["mcp", "--instance", "NOTVALID"]):
                    report = mcp_capture._frame_stale_report("window-key", "window", "ab", {})
            self.assertFalse(override.exists())
            self.assertEqual(report["detail"]["selection_error"], "invalid_instance_token")
            self.assertEqual(report["detail"]["state_backend"], mcp_capture.STATE_BACKEND_UNAVAILABLE)
            self.assertNotEqual(report["detail"]["state_backend"], mcp_capture.STATE_BACKEND_SIDECAR)

    def test_f2_public_captures_report_selection_and_do_not_persist(self) -> None:
        import mcp_capture
        from PIL import Image

        reset_instance_context_for_tests()
        frame = Image.new("RGB", (8, 8), (20, 20, 20))
        frame.info["frame_stale_report"] = {
            "stale": None,
            "detail": {"selection_error": "invalid_instance_token"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "fullres"
            with mock.patch("mcp_capture.grab_stable_frame", return_value=frame):
                screenshot = mcp_capture.capture_screenshot(frames=1, scale=20)
                dual = mcp_capture.capture_dual(
                    frames=1,
                    scale=20,
                    save_fullres=True,
                    save_dir=str(destination),
                )
            self.assertEqual(screenshot.get("selection_error"), "invalid_instance_token")
            self.assertTrue(screenshot.get("isError"))
            self.assertNotIn("data", screenshot)
            self.assertEqual(dual.get("selection_error"), "invalid_instance_token")
            self.assertFalse(destination.exists())

            bare = Image.new("RGB", (8, 8), (20, 20, 20))
            reset_instance_context_for_tests()
            with mock.patch.object(sys, "argv", ["mcp", "--instance", "NOTVALID"]):
                with mock.patch("mcp_capture.grab_stable_frame", return_value=bare):
                    raised = mcp_capture.capture_dual(
                        frames=1,
                        scale=20,
                        save_fullres=True,
                        crop_space="window",
                    )
            self.assertEqual(raised.get("selection_error"), "invalid_instance_token")
            self.assertNotIsInstance(raised, BaseException)

    def test_f3_missing_package_is_omission_not_a_crash(self) -> None:
        import builtins

        import mcp_capture

        reset_instance_context_for_tests()
        real_import = builtins.__import__

        def blocked(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "dayz_mcp.server_cli" or (
                name == "dayz_mcp" and fromlist and "server_cli" in fromlist
            ):
                raise ModuleNotFoundError("dayz_mcp")
            return real_import(name, globals, locals, fromlist, level)

        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": temporary}, clear=False):
                os.environ.pop("DAYZ_MCP_FRAME_STATE_PATH", None)
                with mock.patch.object(sys, "argv", ["mcp"]):
                    with mock.patch("builtins.__import__", side_effect=blocked):
                        token = mcp_capture._bound_capture_token()
                        report = mcp_capture._frame_stale_report("key", "window", "ab", {})
            self.assertIsNone(token)
            self.assertNotIn("selection_error", report["detail"])
            self.assertEqual(report["detail"]["state_backend"], mcp_capture.STATE_BACKEND_SIDECAR)

    def _probe_backend(self, window: dict, client_stats: object):
        from PIL import Image

        def fake(output_path: str, **_: object) -> dict:
            Image.new("RGB", (8, 8), (20, 20, 20)).save(output_path, format="PNG")
            return {
                "ok": True,
                "window": window,
                "client": {"left": 0, "top": 0, "width": 8, "height": 8},
                "clientStats": client_stats,
            }

        return fake

    def _assert_public_selection_failure(self, window: dict, client_stats: object) -> None:
        import mcp_capture

        reset_instance_context_for_tests()
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": temporary}, clear=False):
                os.environ.pop("DAYZ_MCP_FRAME_STATE_PATH", None)
                os.environ.pop("DAYZ_MCP_CAPTURE_DIR", None)
                with mock.patch.object(sys, "argv", ["mcp", "--instance", "NOTVALID"]):
                    with mock.patch(
                        "mcp_capture._run_window_capture",
                        side_effect=self._probe_backend(window, client_stats),
                    ) as backend:
                        screenshot = mcp_capture.capture_screenshot(frames=1, scale=20)
                        dual = mcp_capture.capture_dual(
                            frames=1,
                            scale=20,
                            save_fullres=True,
                            save_dir=str(Path(temporary) / "fullres"),
                        )
            self.assertEqual(screenshot.get("selection_error"), "invalid_instance_token")
            self.assertEqual(screenshot.get("error"), "invalid_instance_token")
            self.assertNotIn("data", screenshot)
            self.assertEqual(dual.get("selection_error"), "invalid_instance_token")
            self.assertEqual(dual.get("error"), "invalid_instance_token")
            self.assertNotIn("inline", dual)
            self.assertNotEqual(screenshot.get("error"), "frame_client_area_unverified")
            self.assertNotEqual(dual.get("error"), "frame_client_area_unverified")
            self.assertEqual(list(Path(temporary).rglob("*")), [])
            backend.assert_not_called()

    def test_f1_unaccredited_window_still_reports_the_selector(self) -> None:
        self._assert_public_selection_failure({}, {"meanBrightness": 20, "nonBlackRatio": 1.0})

    def test_f1_missing_client_stats_do_not_replace_the_selector(self) -> None:
        self._assert_public_selection_failure({"pid": 123}, None)

    def test_a4_fallback_audit_writer_records_the_bound_instance(self) -> None:
        from dayz_mcp import daemon

        reset_instance_context_for_tests()
        bind_instance_context("r9inst", replace=True)
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": temporary}, clear=False):
                landed = daemon.record_daemon_event(
                    {
                        "event": "daemon_started",
                        "reason": "daemon_start",
                        "duration_s": 0.0,
                    },
                    daemon_generation="g5r9selection",
                )
            named = Path(temporary) / "DayZ_MCP_r9inst" / "audit" / "events.jsonl"
            default = Path(temporary) / "DayZ_MCP" / "audit" / "events.jsonl"
            self.assertTrue(landed)
            self.assertTrue(named.is_file())
            self.assertIn("daemon_started", named.read_text(encoding="utf-8"))
            self.assertFalse(default.exists())

    def test_a5_exec_enforce_audit_name_matches_across_writers(self) -> None:
        from dayz_mcp.daemon import exec_enforce_audit_path
        from dayz_mcp.server import Runtime, ServerConfig

        named = ServerConfig(instance_token="130")
        runtime = Runtime(named)
        daemon_path = exec_enforce_audit_path(
            SimpleNamespace(instance_token="130", exec_audit_path=None)
        )
        self.assertEqual(runtime.exec_audit_path(), daemon_path)
        self.assertEqual(runtime.exec_audit_path().name, "exec_enforce-130.jsonl")

        omitted = ServerConfig()
        self.assertEqual(
            Runtime(omitted).exec_audit_path().name,
            exec_enforce_audit_path(
                SimpleNamespace(instance_token=None, exec_audit_path=None)
            ).name,
        )
        self.assertEqual(omitted and Runtime(omitted).exec_audit_path().name, "exec_enforce.jsonl")


if __name__ == "__main__":
    unittest.main()
