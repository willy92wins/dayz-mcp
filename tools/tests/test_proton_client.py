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
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stable_and_experimental_resolve_their_app_and_prefix(self) -> None:
        for game, app in (("DayZ", "221100"), ("DayZ Exp", "1024020")):
            with self.subTest(game=game), tempfile.TemporaryDirectory() as other:
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
        self.temporary = tempfile.TemporaryDirectory()
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

    def test_the_server_flag_is_read_from_a_real_command_line(self) -> None:
        import subprocess
        import sys

        child = subprocess.Popen([sys.executable, "-c", "input()", "-SERVER"], stdin=subprocess.PIPE)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        # exec closes the pipe Popen waits on before the new argv is readable.
        for _ in range(500):
            if b"-SERVER" in Path(f"/proc/{child.pid}/cmdline").read_bytes():
                break
            proton_client.time.sleep(0.01)
        self.assertTrue(proton_client._is_server_process(child.pid))
        self.assertFalse(proton_client._is_server_process(os.getpid()))

    def test_the_two_diag_roles_coexist_but_not_twice_and_never_with_retail(self) -> None:
        roles = {1: True, 2: False}
        with mock.patch.object(proton_client, "_is_server_process", side_effect=lambda pid: roles[pid]):
            server = {"pid": 1, "name": "DayZDiag_x64.exe"}
            client = {"pid": 2, "name": "DayZDiag_x64.exe"}
            retail = {"pid": 3, "name": "DayZ_x64.exe"}
            self.assertEqual(proton_client.conflicting_games([server], server=False), [])
            self.assertEqual(proton_client.conflicting_games([client], server=True), [])
            self.assertEqual(proton_client.conflicting_games([server], server=True), [server])
            self.assertEqual(proton_client.conflicting_games([client], server=False), [client])
            self.assertEqual(proton_client.conflicting_games([retail], server=True), [retail])

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
    def test_steam_pid_file_is_the_registered_pid(self) -> None:
        # registry.vdf's SteamPID can name a dead start-up process: measured
        # SteamPID 20624 (gone) with steam.pid 20622 (the live ubuntu12_32/steam).
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".steam").mkdir()
            (Path(home) / ".steam" / "steam.pid").write_text("4321\n", encoding="ascii")
            (Path(home) / ".steam" / "registry.vdf").write_text(
                '"Registry"\n{\n "SteamPID"\t\t"9999"\n}\n', encoding="utf-8"
            )
            with mock.patch.object(Path, "home", return_value=Path(home)):
                snapshot = steam_linux.LinuxSteamPreflightProvider().read_active_process()
        self.assertEqual(snapshot.pid, 4321)
        self.assertEqual(snapshot.active_user, 1)

    def test_empty_steam_pid_file_is_no_registered_pid(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".steam").mkdir()
            (Path(home) / ".steam" / "steam.pid").write_text("", encoding="ascii")
            with mock.patch.object(Path, "home", return_value=Path(home)):
                snapshot = steam_linux.LinuxSteamPreflightProvider().read_active_process()
        self.assertEqual(snapshot.pid, 0)
        self.assertEqual(snapshot.active_user, 0)

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
        return types.SimpleNamespace(game_path=Path(game_path), guard=object())

    def test_a_dayzserver_policy_goes_to_podman(self) -> None:
        from dayz_mcp import podman_server

        stub = self._lifecycle("/s/steamapps/common/DayZServer")
        argv = ["/s/steamapps/common/DayZServer/DayZServer", "-server"]
        with mock.patch.object(podman_server, "launch_lifecycle_process", return_value="srv") as launch:
            self.assertEqual(ProcessLifecycle._launch(stub, argv, "/c", "normal"), "srv")
        self.assertEqual(launch.call_args.args[0], argv)

    def test_a_dayzserver_policy_refuses_a_client(self) -> None:
        # The engine would stop it at 0x00020017, "Client is using Diag exe".
        stub = self._lifecycle("/s/steamapps/common/DayZServer")
        with self.assertRaisesRegex(OSError, "client_needs_diag_executable"):
            ProcessLifecycle._launch(stub, ["/x/DayZServer", "-connect=127.0.0.1"], "/c", "normal")

    def test_a_diag_policy_runs_both_roles_under_proton(self) -> None:
        stub = self._lifecycle("/s/steamapps/common/DayZ")
        for extra in ("-connect=127.0.0.1", "-server"):
            with mock.patch.object(proton_client, "launch_client", return_value="game") as launch:
                result = ProcessLifecycle._launch(stub, ["/x/DayZDiag_x64.exe", extra], "/c", "normal")
            self.assertEqual(result, "game")
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
