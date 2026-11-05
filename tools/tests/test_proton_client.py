"""The Linux client role: Proton launcher, Steam gate, role dispatch. Nothing starts a game."""

from __future__ import annotations

import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

POSIX = os.name != "nt"

if POSIX:
    from dayz_mcp import linux_compat

    linux_compat.install()
    from dayz_mcp import proton_client, steam_launch_guard, steam_linux
    from dayz_mcp import steam_preflight as steam
    from dayz_mcp.process_lifecycle import ProcessLifecycle


def _library(root: Path, *, game: str = "DayZ", prefix: bool = True) -> Path:
    common = root / "steamapps" / "common"
    game_dir = common / game
    game_dir.mkdir(parents=True)
    (game_dir / "DayZDiag_x64.exe").write_bytes(b"MZ")
    runtime = common / "SteamLinuxRuntime_4"
    runtime.mkdir()
    (runtime / "_v2-entry-point").write_text("#!/bin/sh\n")
    tool = root / "compatibilitytools.d" / "GE-Proton11-1"
    tool.mkdir(parents=True)
    (tool / "proton").write_text("#!/usr/bin/env python3\n")
    if prefix:
        app = "221100" if game == "DayZ" else "1024020"
        (root / "steamapps" / "compatdata" / app / "pfx").mkdir(parents=True)
    return game_dir


@unittest.skipUnless(POSIX, "the Proton client launcher")
class ArgumentTranslationTest(unittest.TestCase):
    def test_posix_paths_become_z_drive_paths(self) -> None:
        self.assertEqual(proton_client.windows_path("/tmp/a/b"), "Z:\\tmp\\a\\b")
        self.assertEqual(proton_client.windows_path("@CF"), "@CF")
        self.assertEqual(proton_client.windows_path("C:\\x"), "C:\\x")

    def test_worker_client_argv_is_translated_and_completed(self) -> None:
        args = proton_client.translate_args(
            [
                "-mod=/m/@CF;/m/@DayZ_MCP",
                "-connect=127.0.0.1",
                "-port=2302",
                "-profiles=/dev/_client/test",
                "-name=Dev",
                "-window",
                "-x=1600",
                "-y=900",
                "-noPause",
                "-filePatching",
            ]
        )
        self.assertEqual(args[:3], ["-nolauncher", "-skipintro", "-nosplash"])
        self.assertIn("-mod=Z:\\m\\@CF;Z:\\m\\@DayZ_MCP", args)
        self.assertIn("-profiles=Z:\\dev\\_client\\test", args)
        self.assertIn("-connect=127.0.0.1", args)
        self.assertIn("-filePatching", args)
        self.assertEqual(proton_client._resolution(args), "1600x900")

    def test_offline_mission_path_is_translated_and_flags_not_doubled(self) -> None:
        args = proton_client.translate_args(["-skipintro", "-mission=/dev/mpmissions/x.chernarusplus"])
        self.assertEqual(args.count("-skipintro"), 1)
        self.assertIn("-mission=Z:\\dev\\mpmissions\\x.chernarusplus", args)


@unittest.skipUnless(POSIX, "the Proton client launcher")
class ChainResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(os.path.realpath(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stable_and_experimental_resolve_their_app_and_prefix(self) -> None:
        for game, app in (("DayZ", "221100"), ("DayZ Exp", "1024020")):
            with self.subTest(game=game), tempfile.TemporaryDirectory(dir="/tmp") as other:
                root = Path(os.path.realpath(other))
                game_dir = _library(root, game=game)
                chain = proton_client.resolve_chain(game_dir, {"DAYZ_MCP_STEAM_ROOT": str(root)})
                self.assertEqual(chain.app_id, app)
                self.assertEqual(chain.client, game_dir / "DayZDiag_x64.exe")
                self.assertEqual(chain.compat_data, root / "steamapps" / "compatdata" / app)
                self.assertTrue(str(chain.proton).endswith("GE-Proton11-1/proton"))

    def test_missing_pieces_are_named(self) -> None:
        game_dir = _library(self.root, prefix=False)
        env = {"DAYZ_MCP_STEAM_ROOT": str(self.root)}
        with self.assertRaisesRegex(proton_client.ProtonClientError, "proton_prefix_missing"):
            proton_client.resolve_chain(game_dir, env)
        (self.root / "steamapps" / "compatdata" / "221100" / "pfx").mkdir(parents=True)
        with self.assertRaisesRegex(proton_client.ProtonClientError, "proton_tool_missing"):
            proton_client.resolve_chain(game_dir, {**env, "DAYZ_MCP_PROTON": "Nope"})
        (game_dir / "DayZDiag_x64.exe").unlink()
        with self.assertRaisesRegex(proton_client.ProtonClientError, "no_DayZDiag"):
            proton_client.resolve_chain(game_dir, env)

    def test_session_script_runs_the_chain_and_ends_sway(self) -> None:
        game_dir = _library(self.root)
        chain = proton_client.resolve_chain(game_dir, {"DAYZ_MCP_STEAM_ROOT": str(self.root)})
        state = self.root / "state"
        state.mkdir()
        proton_client._session_files(state, chain, ["-mod=Z:\\m\\@A;Z:\\m\\@B", "-name=x y"], "1280x720")
        script = (state / "game.sh").read_text()
        self.assertIn("STEAM_COMPAT_APP_ID=221100", script)
        self.assertIn("--verb=run --", script)
        self.assertIn("run " + str(chain.client).replace(" ", "\\ ") if " " in str(chain.client) else str(chain.client), script)
        self.assertTrue(script.rstrip().endswith("swaymsg exit"))
        # Arguments go through a file, one per line: ';' and spaces never meet a parser.
        self.assertEqual((state / "args").read_text().splitlines(), ["-mod=Z:\\m\\@A;Z:\\m\\@B", "-name=x y"])
        self.assertIn('app_id="dayzdiag_x64.exe"', (state / "sway.conf").read_text())


@unittest.skipUnless(POSIX, "the Proton client launcher")
class LaunchRefusalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(os.path.realpath(self.temporary.name))
        self.game_dir = _library(self.root)
        patcher = mock.patch.dict(
            os.environ, {"DAYZ_MCP_STEAM_ROOT": str(self.root), "LOCALAPPDATA": str(self.root / "state")}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_a_running_dayz_client_refuses_the_launch(self) -> None:
        running = [{"pid": 4242, "ppid": 1, "name": "DayZ_x64.exe"}]
        with mock.patch.object(proton_client, "_running_games", return_value=running), mock.patch.object(
            proton_client.subprocess, "Popen"
        ) as popen:
            with self.assertRaisesRegex(proton_client.ProtonClientError, "dayz_client_already_running"):
                proton_client.launch_client(["/x/DayZDiag_x64.exe", "-connect=127.0.0.1"], self.game_dir)
            popen.assert_not_called()

    def test_a_server_argv_is_not_launched_as_a_client(self) -> None:
        with self.assertRaisesRegex(proton_client.ProtonClientError, "server_argv"):
            proton_client.launch_client(["/x/DayZDiag_x64.exe", "-server"], self.game_dir)

    def test_identity_must_read_the_same_twice_under_this_session(self) -> None:
        sway = types.SimpleNamespace(pid=100, poll=lambda: None)
        reads = iter(
            [
                {"identity_complete": True, "executable_sha256": "a", "command_line_sha256": "wine"},
                {"identity_complete": True, "executable_sha256": "a", "command_line_sha256": "dayz"},
                {"identity_complete": True, "executable_sha256": "a", "command_line_sha256": "dayz"},
            ]
        )
        guard = types.SimpleNamespace(snapshot=lambda pid: next(reads))
        rows = {"known": True, "processes": [{"pid": 300, "ppid": 200, "name": "DayZDiag_x64.exe"},
                                              {"pid": 900, "ppid": 1, "name": "DayZDiag_x64.exe"}]}
        parents = {300: 200, 200: 100, 900: 1}
        from dayz_mcp import orphan_guard

        with mock.patch.object(orphan_guard, "snapshot_processes_with_parent", return_value=rows), mock.patch.object(
            proton_client, "_ppid", side_effect=lambda pid: parents.get(pid)
        ), mock.patch.object(proton_client.time, "sleep"):
            pid = proton_client._wait_for_game(sway, self.root, proton_client.time.monotonic() + 5, guard)
        # 900 is a DayZDiag outside this session: never chosen.
        self.assertEqual(pid, 300)

    def test_a_session_that_ends_first_is_an_error_with_its_log(self) -> None:
        state = self.root / "s"
        state.mkdir()
        (state / "game.log").write_text("proton: could not start\n")
        sway = types.SimpleNamespace(pid=100, poll=lambda: 0)
        with self.assertRaisesRegex(proton_client.ProtonClientError, "client_session_ended:.*could not start"):
            proton_client._wait_for_game(sway, state, proton_client.time.monotonic() + 5, object())

    def test_stale_sessions_without_a_game_are_removed(self) -> None:
        root = self.root / "sessions"
        dead = root / (proton_client.SESSION_PREFIX + "dead")
        dead.mkdir(parents=True)
        (dead / "sway.pid").write_text("999999999\n")
        foreign = root / "unrelated"
        foreign.mkdir()
        proton_client.remove_stale_sessions(root, [])
        self.assertFalse(dead.exists())
        self.assertTrue(foreign.exists())


@unittest.skipUnless(POSIX, "the guard's view of a Wine process")
class WineArgvTrimTest(unittest.TestCase):
    def _trimmed(self, argv: list[str]) -> list[str]:
        process = types.SimpleNamespace(cmdline=lambda: list(argv), pid=7)
        wrapped = linux_compat._WineArgvTrimmedProcess(process)
        self.assertEqual(wrapped.pid, 7)
        return wrapped.cmdline()

    def test_wine_padding_is_dropped(self) -> None:
        argv = ["Z:\\g\\DayZDiag_x64.exe", "-connect=127.0.0.1", *[""] * 222]
        self.assertEqual(self._trimmed(argv), argv[:2])

    def test_native_argv_is_untouched(self) -> None:
        self.assertEqual(self._trimmed(["/usr/bin/prog", "a", ""]), ["/usr/bin/prog", "a", ""])

    def test_the_real_guard_is_wrapped_once(self) -> None:
        from dayz_mcp import native_process_guard

        linux_compat.install_wine_argv_trim(native_process_guard)
        snapshot = native_process_guard.NativeProcessGuard().snapshot(os.getpid())
        self.assertTrue(snapshot["identity_complete"])
        self.assertTrue(native_process_guard.NativeProcessGuard._process._wine_argv_trim)


@unittest.skipUnless(POSIX, "the Linux Steam gate")
class LinuxSteamGateTest(unittest.TestCase):
    def test_registry_vdf_steam_pid_is_the_registered_pid(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as home:
            (Path(home) / ".steam").mkdir()
            (Path(home) / ".steam" / "registry.vdf").write_text(
                '"Registry"\n{\n "HKLM"\n {\n  "Software"\n  {\n   "Valve"\n   {\n    "Steam"\n'
                '    {\n     "SteamPID"\t\t"4321"\n    }\n   }\n  }\n }\n}\n'
            )
            with mock.patch.object(steam_linux.Path, "home", return_value=Path(home)):
                snapshot = steam_linux.LinuxSteamPreflightProvider().read_active_process()
        self.assertEqual((snapshot.pid, snapshot.active_user), (4321, 1))

    def test_the_steam_image_is_steam_off_windows(self) -> None:
        self.assertTrue(steam.is_steam_image("/s/ubuntu12_32/steam"))
        self.assertFalse(steam.is_steam_image("/s/steam.sh"))
        self.assertFalse(steam.is_steam_image("steam"))
        self.assertTrue(steam.is_steam_image("C:\\x\\steam.exe"))

    def _provider(self, *, registered: int, live: tuple[int, ...]) -> object:
        ticks = steam_linux._FILETIME_UNIX_EPOCH + 1_700_000_000 * 10_000_000
        return types.SimpleNamespace(
            read_active_process=lambda: steam.SteamActiveProcessSnapshot(registered, 1),
            process_exists=lambda pid: pid in live,
            process_image_path=lambda pid: "/s/ubuntu12_32/steam",
            process_creation_ticks=lambda pid: ticks,
            steam_process_pids=lambda: live,
            steam_startup_complete=lambda pid: True,
        )

    def test_gate_pins_a_running_steam_and_never_mutates(self) -> None:
        gate = steam_linux.LinuxSteamPreparationGate(self._provider(registered=77, live=(77,)))
        mutated = []
        prepared = gate.prepare(
            consent=True, authority_active=lambda: True, mutation_allowed=lambda: mutated.append(1)
        )
        self.assertIsNone(prepared.error_code)
        self.assertEqual(prepared.identity.pid, 77)
        self.assertEqual(prepared.startup, "observed")
        self.assertTrue(gate.final_check(prepared))
        self.assertEqual(mutated, [])

    def test_gate_refuses_a_stopped_or_unregistered_steam(self) -> None:
        stopped = steam_linux.LinuxSteamPreparationGate(self._provider(registered=77, live=()))
        self.assertEqual(
            stopped.prepare(consent=True, authority_active=lambda: True, mutation_allowed=lambda: True).error_code,
            steam.STEAM_NOT_RUNNING,
        )
        other = steam_linux.LinuxSteamPreparationGate(self._provider(registered=5, live=(77,)))
        self.assertEqual(
            other.prepare(consent=False, authority_active=lambda: True, mutation_allowed=lambda: True).error_code,
            steam.STEAM_SESSION_STALE,
        )
        cancelled = steam_linux.LinuxSteamPreparationGate(self._provider(registered=77, live=(77,)))
        self.assertEqual(
            cancelled.prepare(consent=False, authority_active=lambda: False, mutation_allowed=lambda: True).error_code,
            "steam_prepare_cancelled",
        )
        self.assertIsInstance(
            cancelled.prepare(consent=False, authority_active=lambda: True, mutation_allowed=lambda: True),
            steam_launch_guard.Preparation,
        )


@unittest.skipUnless(POSIX, "the Linux role dispatch")
class LaunchDispatchTest(unittest.TestCase):
    def _lifecycle(self, game_path: str) -> object:
        stub = types.SimpleNamespace(
            game_path=Path(game_path), guard=object(), _LINUX_SERVER_APPS=ProcessLifecycle._LINUX_SERVER_APPS
        )
        stub._linux_server_executable = lambda: ProcessLifecycle._linux_server_executable(stub)
        return stub

    def test_server_argv_goes_to_podman_with_the_paired_server(self) -> None:
        from dayz_mcp import podman_server

        stub = self._lifecycle("/s/steamapps/common/DayZ")
        with mock.patch.object(podman_server, "launch_lifecycle_process", return_value="srv") as launch:
            result = ProcessLifecycle._launch(stub, ["/s/steamapps/common/DayZ/DayZDiag_x64.exe", "-server"], "/c", "normal")
        self.assertEqual(result, "srv")
        self.assertEqual(
            launch.call_args.kwargs["server_binary"],
            Path("/s/steamapps/common/DayZServer/DayZServer").resolve(),
        )

    def test_client_argv_goes_to_proton(self) -> None:
        stub = self._lifecycle("/s/steamapps/common/DayZ")
        with mock.patch.object(proton_client, "launch_client", return_value="cli") as launch:
            result = ProcessLifecycle._launch(stub, ["/x/DayZDiag_x64.exe", "-connect=127.0.0.1"], "/c", "normal")
        self.assertEqual(result, "cli")
        self.assertEqual(launch.call_args.args[1], Path("/s/steamapps/common/DayZ"))

    def test_launcher_errors_surface_as_oserror(self) -> None:
        stub = self._lifecycle("/s/steamapps/common/DayZ")
        with mock.patch.object(
            proton_client, "launch_client", side_effect=proton_client.ProtonClientError("dayz_client_already_running:x")
        ):
            with self.assertRaisesRegex(OSError, "dayz_client_already_running"):
                ProcessLifecycle._launch(stub, ["/x/DayZDiag_x64.exe"], "/c", "normal")


if __name__ == "__main__":
    unittest.main()
