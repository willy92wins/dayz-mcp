"""DayZ launch must not steal the foreground; grab must not open a console."""

from __future__ import annotations

import io
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
from dayz_mcp import dayz_test_worker
from dayz_mcp import process_lifecycle
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.session_coordination import SessionCoordinator
from tests.steam_helpers import FakeSteamGate
from tests.process_lifecycle_helpers import (
    AuditSink,
    FakeGuard,
    IDENTITY_A,
    process,
    snapshot,
)

# fb-20260930-171421-fa6f: requests built by dayz_test_worker itself, so the
# argv the launcher reads is the worker's and not a copy written for the test.
_WORKER_PAYLOAD: dict[str, object] = {
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


def _worker_request(
    root: Path, role: str, run_id: str | None = None
) -> dict[str, object]:
    runtime = dayz_test_worker.WorkerRuntimePolicy(
        dev_root=str(root / "ExampleMod_Suite"),
        mod="ExampleMod",
        diag_executable=str(root / "DayZ" / "DayZDiag_x64.exe"),
        game_directory=str(root / "DayZ"),
        mission_aliases=(
            ("chernarus", str(root / "mpmissions" / "dayzOffline.chernarusplus")),
        ),
        mods_root=str(root / "Mods"),
        build_temp_root=str(root / "temp"),
        build_source_basename=None,
    )
    return dayz_test_worker._start_core(
        dict(_WORKER_PAYLOAD), runtime, role=role, run_id=run_id
    )


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

    def _popen_call(
        self, argv: list[str], window_style: str
    ) -> tuple[list[str], dict]:
        seen: list[tuple[list[str], dict]] = []

        def fake_popen(popen_argv, **kwargs):
            seen.append((list(popen_argv), kwargs))
            return mock.Mock(pid=4242)

        with mock.patch.object(
            process_lifecycle.subprocess, "Popen", side_effect=fake_popen
        ):
            self.lifecycle._launch(list(argv), str(self.game), window_style)
        self.assertEqual(len(seen), 1)
        return seen[0]

    def _popen_kwargs(self, window_style: str) -> dict:
        return self._popen_call(["DayZDiag_x64.exe"], window_style)[1]

    def _role_argv(self, role: str) -> list[str]:
        return list(_worker_request(self.root, role)["argv"])

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

    def test_fb_f298_server_launch_starts_minimized_without_activation(self) -> None:
        argv = self._role_argv("server")
        popen_argv, kwargs = self._popen_call(argv, "normal")
        startupinfo = kwargs["startupinfo"]
        self.assertTrue(startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(startupinfo.wShowWindow, 7)
        self.assertEqual(
            startupinfo.wShowWindow, process_lifecycle.SW_SHOWMINNOACTIVE
        )
        self.assertNotIn("creationflags", kwargs)
        self.assertEqual(popen_argv, argv)
        self.assertEqual(kwargs["cwd"], str(self.game))
        self.assertTrue(kwargs["close_fds"])

    def test_fb_f298_client_and_offline_launches_keep_shownoactivate(self) -> None:
        _server_argv, server = self._popen_call(self._role_argv("server"), "normal")
        for role in ("client", "offline"):
            with self.subTest(role=role):
                argv = self._role_argv(role)
                popen_argv, kwargs = self._popen_call(argv, "normal")
                startupinfo = kwargs["startupinfo"]
                self.assertEqual(startupinfo.wShowWindow, 4)
                self.assertEqual(
                    startupinfo.wShowWindow, process_lifecycle.SW_SHOWNOACTIVATE
                )
                self.assertEqual(popen_argv, argv)
                # The show state is the only difference from the server spawn:
                # same Popen keywords, flags, directory and environment.
                self.assertNotEqual(
                    startupinfo.wShowWindow, server["startupinfo"].wShowWindow
                )
                self.assertEqual(sorted(kwargs), sorted(server))
                self.assertEqual(startupinfo.dwFlags, server["startupinfo"].dwFlags)
                for key in ("cwd", "close_fds", "env"):
                    self.assertEqual(kwargs[key], server[key])

    def test_fb_f298_hidden_launches_keep_create_no_window(self) -> None:
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        for role in ("server", "client", "offline"):
            with self.subTest(role=role):
                argv = self._role_argv(role)
                popen_argv, kwargs = self._popen_call(argv, "hidden")
                self.assertTrue(
                    int(kwargs.get("creationflags", 0)) & create_no_window
                )
                self.assertNotIn("startupinfo", kwargs)
                self.assertEqual(popen_argv, argv)

    def test_fb_f298_server_is_the_exact_server_argument_in_any_case(self) -> None:
        # The Steam gate of _start_run_reserved reads the argv the same way.
        cases = (
            (
                ["DayZDiag_x64.exe", "-SERVER", "-port=2302"],
                process_lifecycle.SW_SHOWMINNOACTIVE,
            ),
            (
                ["DayZDiag_x64.exe", r"-serverMod=P:\Mods\@X", "-port=2302"],
                process_lifecycle.SW_SHOWNOACTIVATE,
            ),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv):
                _popen_argv, kwargs = self._popen_call(argv, "normal")
                self.assertEqual(kwargs["startupinfo"].wShowWindow, expected)


class FbF298StartRunShowStateTest(unittest.TestCase):
    """start_run with the launcher the daemon uses and the worker's requests."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        (self.game / "DayZDiag_x64.exe").write_bytes(b"")
        # An empty mission: the server launch seals it and rotates nothing.
        (self.root / "mpmissions" / "dayzOffline.chernarusplus").mkdir(parents=True)
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
        status, acquired = coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token = acquired["lease_token"]
        self.guard = FakeGuard()
        # No launcher argument, as in daemon.py: start_run reaches _launch.
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=coordinator,
            manifest=RunManifestStore(paths),
            audit=audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            id_fn=lambda: "run-1",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_fb_f298_mode_all_spawns_the_server_minimized_and_the_client_visible(
        self,
    ) -> None:
        spawned: list[tuple[list[str], dict]] = []
        pids = iter((9001, 9002))

        def fake_popen(argv, **kwargs):
            spawned.append((list(argv), kwargs))
            return mock.Mock(pid=next(pids))

        self.guard.snapshots[9001] = snapshot(process(9001, "server"))
        self.guard.snapshots[9002] = snapshot(process(9002, "client"))
        with mock.patch.object(
            process_lifecycle.subprocess, "Popen", side_effect=fake_popen
        ):
            server = self.lifecycle.start_run(
                IDENTITY_A, self.token, _worker_request(self.root, "server")
            )
            client = self.lifecycle.start_run(
                IDENTITY_A,
                self.token,
                _worker_request(self.root, "client", "run-1"),
            )

        self.assertEqual(server, {"ok": True, "run_id": "run-1", "state": "RUNNING"})
        self.assertEqual(client, {"ok": True, "run_id": "run-1", "state": "RUNNING"})
        self.assertEqual(len(spawned), 2)
        (server_argv, server_kwargs), (client_argv, client_kwargs) = spawned
        self.assertIn("-server", server_argv)
        self.assertNotIn("-server", client_argv)
        self.assertEqual(
            server_kwargs["startupinfo"].wShowWindow,
            process_lifecycle.SW_SHOWMINNOACTIVE,
        )
        self.assertEqual(
            client_kwargs["startupinfo"].wShowWindow,
            process_lifecycle.SW_SHOWNOACTIVATE,
        )
        for kwargs in (server_kwargs, client_kwargs):
            self.assertTrue(
                kwargs["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW
            )
            self.assertNotIn("creationflags", kwargs)


class FbF298CaptureCreateNoWindowTest(unittest.TestCase):
    def test_fb_f298_window_capture_passes_create_no_window_and_grab_env(self) -> None:
        grab_env = {"KEEP": "1"}

        class _ExitedGrab:
            def __init__(self) -> None:
                self.stdout = io.StringIO('{"ok":false,"error":"no_window"}\n')
                self.stderr = io.StringIO("")
                self.returncode = 0

            def poll(self) -> int:
                return self.returncode

            def wait(self, timeout: float | None = None) -> int:
                return self.returncode

            def kill(self) -> None:
                self.returncode = -9

        popen = mock.Mock(return_value=_ExitedGrab())
        with mock.patch.object(
            mcp_capture, "probe_input_desktop", return_value="unlocked"
        ):
            with mock.patch.object(mcp_capture.os.path, "exists", return_value=True):
                with mock.patch.object(
                    mcp_capture, "_grab_subprocess_env", return_value=grab_env
                ):
                    with mock.patch.object(mcp_capture.subprocess, "Popen", popen):
                        mcp_capture._run_window_capture(
                            "frame.png", "DayZDiag_x64", 8.0
                        )
        popen.assert_called_once()
        kwargs = popen.call_args.kwargs
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        self.assertTrue(int(kwargs.get("creationflags", 0)) & create_no_window)
        self.assertIs(kwargs["env"], grab_env)


if __name__ == "__main__":
    unittest.main()
