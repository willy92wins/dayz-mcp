"""DayZ launch must not steal the foreground; grab must not open a console."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import mcp_capture
from dayz_mcp import process_lifecycle
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.steam_helpers import FakeSteamGate
from tests.process_lifecycle_helpers import AuditSink, FakeGuard


class FbF298LaunchStartupinfoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        audit = AuditSink()
        coordinator = SessionCoordinator(
            token_fn=lambda: "token-A",
            id_fn=lambda: "lease-A",
            audit=audit,
        )
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=coordinator,
            manifest=RunManifestStore(paths),
            audit=audit,
            guard=FakeGuard(),
            retail_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _popen_kwargs(self, window_style: str) -> dict:
        seen: list[dict] = []

        def fake_popen(_argv, **kwargs):
            seen.append(kwargs)
            return mock.Mock(pid=4242)

        with mock.patch.object(
            process_lifecycle.subprocess, "Popen", side_effect=fake_popen
        ):
            self.lifecycle._launch(
                ["DayZDiag_x64.exe"], str(self.game), window_style
            )
        self.assertEqual(len(seen), 1)
        return seen[0]

    def test_fb_f298_normal_launch_passes_shownoactivate_startupinfo(self) -> None:
        kwargs = self._popen_kwargs("normal")
        startupinfo = kwargs["startupinfo"]
        self.assertTrue(startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(startupinfo.wShowWindow, 4)
        self.assertEqual(
            startupinfo.wShowWindow, process_lifecycle.SW_SHOWNOACTIVATE
        )
        flags = int(kwargs.get("creationflags", 0))
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        self.assertFalse(flags & create_no_window)
        self.assertEqual(kwargs["cwd"], str(self.game))
        self.assertTrue(kwargs["close_fds"])

    def test_fb_f298_hidden_launch_still_passes_create_no_window(self) -> None:
        kwargs = self._popen_kwargs("hidden")
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        self.assertTrue(int(kwargs.get("creationflags", 0)) & create_no_window)
        self.assertNotIn("startupinfo", kwargs)


class FbF298CaptureCreateNoWindowTest(unittest.TestCase):
    def test_fb_f298_window_capture_passes_create_no_window_and_grab_env(self) -> None:
        grab_env = {"KEEP": "1"}
        run = mock.Mock(
            return_value=mock.Mock(
                stdout='{"ok":false,"error":"no_window"}\n', stderr=""
            )
        )
        with mock.patch.object(
            mcp_capture, "probe_input_desktop", return_value="unlocked"
        ):
            with mock.patch.object(mcp_capture.os.path, "exists", return_value=True):
                with mock.patch.object(
                    mcp_capture, "_grab_subprocess_env", return_value=grab_env
                ):
                    with mock.patch.object(mcp_capture.subprocess, "run", run):
                        mcp_capture._run_window_capture(
                            "frame.png", "DayZDiag_x64", 8.0
                        )
        run.assert_called_once()
        kwargs = run.call_args.kwargs
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        self.assertTrue(int(kwargs.get("creationflags", 0)) & create_no_window)
        self.assertIs(kwargs["env"], grab_env)


if __name__ == "__main__":
    unittest.main()
