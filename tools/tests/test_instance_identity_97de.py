"""Instance identity regressions for tickets 97de and 0cb3.

The four reviewer failures F01-F04 are executable here. They describe the
unmodified scanner, startup lock and provenance chain.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from dayz_mcp.daemon_contract import build_daemon_argv
from dayz_mcp.host_config import HostConfigError, _registration_from_entry
from dayz_mcp.identity_migration import scan_dayz_mcp_processes
from dayz_mcp.instance_context import (
    RootWriterLease,
    reset_instance_context_for_tests,
    state_root_name,
    validate_instance_token,
)
from dayz_mcp.server_cli import parse_server_tail_silent


def _process(pid: int, argv: list[str], executable: str, cwd: str | None = None):
    class _Process:
        info = {"pid": pid, "name": "python.exe"}

        def oneshot(self):
            return mock.MagicMock(__enter__=lambda s: None, __exit__=lambda *a: None)

        def exe(self):
            return executable

        def cmdline(self):
            return argv

        def cwd(self):
            if cwd is None:
                raise OSError("cwd_unavailable")
            return cwd

    return _Process()


class _Psutil:
    def __init__(self, processes):
        self._processes = processes

    def process_iter(self, _fields, ad_value=None):
        del ad_value
        return self._processes


class InstanceIdentityTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_instance_context_for_tests()

    def test_f01_valid_client_does_not_block_same_root_migration(self) -> None:
        """F01: a live --client of this root is not a writer, so it cannot
        deadlock the daemon's first migration."""
        client = [
            "python",
            "-m",
            "dayz_mcp",
            "--client",
            "--keyfile",
            r"C:\keys\shared.key",
            "--port",
            "8775",
            "--instance",
            "130",
        ]
        found = scan_dayz_mcp_processes(
            psutil_module=_Psutil([_process(41, client, r"C:\Python\python.exe")]),
            state_token="130",
        )
        self.assertEqual(found, ())

    def test_f02_second_writer_same_token_different_port_does_not_activate(self) -> None:
        """F02: exclusion is the state root, not the port."""
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / state_root_name("130")
            first = RootWriterLease(root)
            self.assertTrue(first.try_acquire())
            try:
                probe = (
                    "import sys\n"
                    "from dayz_mcp.instance_context import RootWriterLease\n"
                    "lease = RootWriterLease(sys.argv[1])\n"
                    "raise SystemExit(0 if lease.try_acquire() else 3)\n"
                )
                completed = subprocess.run(
                    [sys.executable, "-B", "-c", probe, str(root)],
                    cwd=str(Path(__file__).resolve().parents[1]),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 3, completed.stderr)
                self.assertFalse((root / "runs.json").exists())
            finally:
                first.release()

    def test_f03_unaccredited_flagged_writer_still_blocks(self) -> None:
        """F03: appending --instance does not accredit a foreign tree."""
        foreign = [
            "python",
            "-m",
            "dayz_mcp",
            "--daemon",
            "--keyfile",
            r"C:\keys\shared.key",
            "--port",
            "8785",
            "--instance",
            "130",
        ]
        found = scan_dayz_mcp_processes(
            psutil_module=_Psutil(
                [_process(77, foreign, r"C:\Python\python.exe", r"C:\fixture\exp-copy")]
            ),
            state_token="130",
        )
        self.assertEqual(found, (77,))

    def test_f03_accredited_other_root_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "dayz_mcp"
            package.mkdir()
            (package / "instance_context.py").write_text(
                'ROOT_SELECTION_CONTRACT_ID = "97de-v1"\n',
                encoding="utf-8",
            )
            venv_python = Path(tmp) / ".venv-mcp" / "Scripts" / "python.exe"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_bytes(b"")
            other = [
                venv_python.name,
                "-m",
                "dayz_mcp",
                "--daemon",
                "--keyfile",
                r"C:\keys\shared.key",
                "--instance",
                "130",
            ]
            found = scan_dayz_mcp_processes(
                psutil_module=_Psutil(
                    [_process(88, other, str(venv_python), tmp)]
                ),
                state_token=None,
            )
            self.assertEqual(found, ())

    def test_f04_registration_name_must_match_argv_token(self) -> None:
        entry = {
            "type": "stdio",
            "command": os.path.abspath(__file__),
            "args": [
                "-m",
                "dayz_mcp",
                "--client",
                "--port",
                "8775",
                "--keyfile",
                os.path.abspath(__file__),
                "--idle-timeout",
                "1800",
                "--client-platform",
                "claude",
                "--instance",
                "130",
            ],
            "timeout": 604_800_000,
        }
        with mock.patch(
            "dayz_mcp.host_config._local_launch_executable",
            return_value=os.path.abspath(__file__),
        ):
            with self.assertRaises(HostConfigError):
                _registration_from_entry(entry, platform="claude", server_name="dayz-mcp")
            registration = _registration_from_entry(
                entry, platform="claude", server_name="dayz-mcp-130"
            )
        self.assertEqual(registration.instance_token, "130")

    def test_default_daemon_argv_omits_instance_flags(self) -> None:
        config = SimpleNamespace(
            port=8765,
            keyfile=r"C:\keys\shared.key",
            expected_game_version=None,
            require_version=False,
            idle_timeout_s=1800.0,
            enable_exec_enforce=False,
            exec_allowlist=None,
            exec_audit_path=None,
        )
        argv = build_daemon_argv(config, python=r"C:\Python\python.exe")
        self.assertEqual(
            argv,
            [
                r"C:\Python\python.exe",
                "-m",
                "dayz_mcp",
                "--daemon",
                "--port",
                "8765",
                "--keyfile",
                r"C:\keys\shared.key",
                "--idle-timeout",
                "1800.0",
            ],
        )
        named = SimpleNamespace(**config.__dict__, instance_token="130", game_path=r"C:\Games\DayZ")
        named_argv = build_daemon_argv(named, python=r"C:\Python\python.exe")
        self.assertEqual(named_argv[: len(argv)], argv)
        self.assertEqual(
            named_argv[len(argv) :],
            ["--instance", "130", "--game-path", r"C:\Games\DayZ"],
        )

    def test_glued_and_reserved_tokens_are_rejected(self) -> None:
        self.assertEqual(
            parse_server_tail_silent(["--instance=130", "--keyfile", "K"]).status,
            "invalid",
        )
        self.assertEqual(
            parse_server_tail_silent(
                ["--instance", "130", "--instance", "130", "--keyfile", "K"]
            ).status,
            "invalid",
        )
        with self.assertRaises(ValueError):
            validate_instance_token("default")
        parsed = parse_server_tail_silent(["--keyfile", "K", "--daemon"])
        self.assertEqual(parsed.status, "parsed")
        assert parsed.namespace is not None
        self.assertIsNone(parsed.namespace.instance)

    def test_malformed_tail_blocks(self) -> None:
        bad = ["python", "-m", "dayz_mcp", "--client", "--evil"]
        found = scan_dayz_mcp_processes(
            psutil_module=_Psutil([_process(9, bad, r"C:\Python\python.exe")]),
            state_token="130",
        )
        self.assertEqual(found, (9,))

    def test_omission_namespace_has_no_token(self) -> None:
        parsed = parse_server_tail_silent(["--keyfile", "K"])
        self.assertIsInstance(parsed.namespace, Namespace)
        self.assertIsNone(parsed.namespace.instance)
        self.assertIsNone(parsed.namespace.game_path)


class Round2Findings(unittest.TestCase):
    def tearDown(self) -> None:
        reset_instance_context_for_tests()

    def test_f1_cwd_package_does_not_accredit_another_script(self) -> None:
        with tempfile.TemporaryDirectory() as tools, tempfile.TemporaryDirectory() as legacy:
            package = Path(tools) / "dayz_mcp"
            package.mkdir()
            (package / "instance_context.py").write_text(
                'ROOT_SELECTION_CONTRACT_ID = "97de-v1"\n',
                encoding="utf-8",
            )
            script = Path(legacy) / "dayz_mcp" / "__main__.py"
            script.parent.mkdir()
            script.write_text("print('legacy')\n", encoding="utf-8")
            argv = [
                r"C:\Python314\python.exe",
                str(script),
                "--daemon",
                "--keyfile",
                r"C:\keys\K",
                "--instance",
                "130",
            ]
            found = scan_dayz_mcp_processes(
                psutil_module=_Psutil(
                    [_process(91, argv, argv[0], tools)]
                ),
                state_token="130",
            )
            self.assertEqual(found, (91,))

    def test_f2_programmatic_activation_requires_the_writer_lease(self) -> None:
        from dayz_mcp import daemon
        from dayz_mcp.instance_context import RootWriterLease as Lease

        with tempfile.TemporaryDirectory() as temporary:
            config = SimpleNamespace(
                port=8785,
                instance_token="130",
                key=None,
                enable_exec_enforce=False,
                require_version=False,
                expected_game_version=None,
            )
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": temporary}), mock.patch.object(
                Lease, "try_acquire", return_value=False
            ), mock.patch.object(
                daemon, "_activate_server_coordination"
            ) as activate, mock.patch.object(
                daemon, "_ensure_identity_migration"
            ) as migrate:
                with self.assertRaises(RuntimeError):
                    daemon.build_server_state(
                        config, "k", activate_coordination=True
                    )
            activate.assert_not_called()
            migrate.assert_not_called()

    def test_f3_game_path_configures_lifecycle_authority(self) -> None:
        from dayz_mcp.daemon import _lifecycle_game_path
        import dayz_mcp.daemon as daemon

        experimental = r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Experimental"
        self.assertEqual(
            _lifecycle_game_path(SimpleNamespace(game_path=experimental)),
            Path(experimental),
        )
        source = Path(daemon.__file__).read_text(encoding="utf-8")
        self.assertIn("game_path=_lifecycle_game_path(config)", source)

    def test_f4_named_prepare_does_not_rewrite_a_foreign_profile(self) -> None:
        import json
        from dayz_mcp.loopback import BindingPrepareError, ServerState

        with tempfile.TemporaryDirectory() as temporary:
            profiles = Path(temporary) / "profiles"
            profiles.mkdir()
            config = profiles / "dayz_mcp.json"
            original = {
                "url": "http://127.0.0.1:8765/",
                "key": "a-key",
                "instance": "11111111-1111-4111-8111-111111111111",
            }
            config.write_text(json.dumps(original), encoding="utf-8")
            state = ServerState("b-key", config_port=8775)
            state.instance_token = "130"
            with self.assertRaises(BindingPrepareError) as raised:
                state.prepare("run", "server", str(profiles))
            self.assertEqual(raised.exception.code, "instance_profile_owner_mismatch")
            self.assertEqual(json.loads(config.read_text(encoding="utf-8")), original)

    def test_f4_worker_profile_comes_from_launcher_environment(self) -> None:
        from dayz_mcp.dayz_test_worker import WorkerRuntimePolicy, _start_core

        with tempfile.TemporaryDirectory() as temporary:
            mission = Path(temporary) / "mission.chernarusplus"
            mission.mkdir()
            runtime = WorkerRuntimePolicy(
                dev_root=temporary,
                mod="Example",
                diag_executable=str(Path(temporary) / "DayZDiag_x64.exe"),
                game_directory=temporary,
                mission_aliases=(),
                mods_root=str(Path(temporary) / "mods"),
                build_temp_root=str(Path(temporary) / "temp"),
                build_source_basename=None,
            )
            payload = {
                "mission": str(mission),
                "base_mods": [],
                "extra_mods": [],
                "port": 2302,
                "no_file_patching": True,
                "player_name": "A",
                "width": 640,
                "height": 480,
                "navmesh_data_server": False,
                "server_mods": [],
            }
            payload["instance_token"] = "130"
            core = _start_core(payload, runtime, role="server", run_id=None)
            self.assertTrue(str(core["profiles"]).endswith("profiles-130"))

    def test_f5_recovery_second_check_keeps_the_token(self) -> None:
        from dayz_mcp import identity_migration as migration

        seen: list[object] = []

        def _record(*args, **kwargs):
            seen.append(kwargs.get("state_token", args[-1] if args else None))

        from dayz_mcp.runtime_state import RuntimePaths

        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"LOCALAPPDATA": temporary}
        ), mock.patch.object(
            migration, "_assert_quiescent", side_effect=_record
        ), mock.patch.object(
            migration, "_settled_receipt", return_value=None
        ), mock.patch.object(
            migration, "_recover_backup_transaction", return_value={"ok": True}
        ):
            migration.ensure_runs_v1_backup(
                RuntimePaths.for_token("130"),
                8775,
                state_token="130",
                migration_dir=Path(temporary) / "migration",
            )
        self.assertEqual(seen, ["130", "130"])

    def test_f6_installers_and_launchers_accept_the_selector(self) -> None:
        import install_mcp
        from dayz_mcp.secure_launcher import _parser as secure_parser

        options = install_mcp.parse_args(
            ["--instance", "130", "--port", "8775", "--register"]
        )
        self.assertEqual(options.instance_token, "130")
        self.assertEqual(options.port, 8775)
        parsed = secure_parser().parse_args(["DayZ130", "--instance", "130"])
        self.assertEqual(parsed.instance, "130")
        script = Path(__file__).resolve().parents[1].joinpath("install-mcp.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("mcp add $ServerName", script)
        self.assertIn("'--instance', $Instance", script)
        self.assertIn("install_mcp.py", script)

    def test_f7_gate_rejects_a_conflicting_instance_environment(self) -> None:
        import p0s_gate

        with mock.patch.dict(os.environ, {"DAYZ_MCP_INSTANCE": "130"}, clear=False):
            code = p0s_gate.main(["backup-runs-v1", "--port", "8775"])
        self.assertEqual(code, 2)

    def test_f8_shared_build_lock_admits_one_writer(self) -> None:
        import threading
        from dayz_mcp.server_cli import shared_build_lock

        with tempfile.TemporaryDirectory() as temporary:
            entered = []
            release = threading.Event()
            started = threading.Event()

            def hold() -> None:
                with shared_build_lock(r"P:\Mods\@ExampleMod\Addons", r"P:\temp\ExampleMod"):
                    entered.append("in")
                    started.set()
                    release.wait(2)

            with mock.patch.dict(os.environ, {"DAYZ_MCP_SHARED_ROOT": temporary}):
                worker = threading.Thread(target=hold)
                worker.start()
                self.assertTrue(started.wait(2))
                blocked = shared_build_lock(
                    r"P:\Mods\@ExampleMod\Addons", r"P:\temp\ExampleMod"
                )
                acquired = []

                def try_second() -> None:
                    with blocked:
                        acquired.append("second")

                rival = threading.Thread(target=try_second)
                rival.start()
                rival.join(0.2)
                self.assertFalse(acquired)
                release.set()
                rival.join(2)
                worker.join(2)
            self.assertEqual(acquired, ["second"])

    def test_f9_named_capture_paths_differ(self) -> None:
        import mcp_capture
        from dayz_mcp.server_cli import bind_instance_context

        bind_instance_context(None, replace=True)
        default_dir = mcp_capture.resolve_capture_dir()
        default_frame = mcp_capture.frame_state_path()
        bind_instance_context("130", replace=True)
        named_dir = mcp_capture.resolve_capture_dir()
        named_frame = mcp_capture.frame_state_path()
        self.assertNotEqual(default_dir, named_dir)
        self.assertNotEqual(default_frame, named_frame)
        self.assertTrue(default_dir.endswith("dayz_mcp_captures"))
        self.assertTrue(named_dir.endswith("dayz_mcp_captures_130"))
        self.assertIn("DayZ_MCP_130", named_frame)

    def test_f10_named_install_does_not_copy_global_skills(self) -> None:
        from dayz_mcp.knowledge_pack import KnowledgePackError, install_knowledge_pack
        from dayz_mcp.server_cli import bind_instance_context

        bind_instance_context("130", replace=True)
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary) / "pack"
            (pack / "skills" / "new-skill").mkdir(parents=True)
            (pack / "skills" / "new-skill" / "SKILL.md").write_text("x\n", encoding="utf-8")
            skills = Path(temporary) / "skills"
            with mock.patch(
                "dayz_mcp.knowledge_pack.ensure_pack", return_value=pack
            ), self.assertRaises(KnowledgePackError):
                install_knowledge_pack(
                    sync=True,
                    pack_dir=pack,
                    skills_dir=skills,
                    manifest_path=pack / "manifest.json",
                )
            self.assertFalse(skills.exists())

    def test_fast_tests_do_not_use_the_real_shared_profile(self) -> None:
        from dayz_mcp.server_cli import shared_root

        real = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "DayZ_MCP_shared"
        with mock.patch.dict(
            os.environ, {"DAYZ_MCP_FAST_TESTS": "1"}, clear=False
        ):
            os.environ.pop("DAYZ_MCP_SHARED_ROOT", None)
            chosen = shared_root()
            self.assertNotEqual(
                os.path.normcase(str(chosen)), os.path.normcase(str(real))
            )
            with mock.patch.dict(os.environ, {"DAYZ_MCP_SHARED_ROOT": str(real)}):
                with self.assertRaises(OSError):
                    shared_root()


class Round3Findings(unittest.TestCase):
    def tearDown(self) -> None:
        reset_instance_context_for_tests()

    def test_f1_keyfile_argument_does_not_accredit_the_package(self) -> None:
        from dayz_mcp.identity_migration import _migration_disposition, _script_target

        workspace = Path(__file__).resolve().parents[1] / "dayz_mcp" / "__main__.py"
        argv = [
            r"C:\Python314\python.exe",
            "-m",
            "dayz_mcp",
            "--daemon",
            "--keyfile",
            str(workspace),
        ]
        self.assertIsNone(_script_target(argv))
        disposition = _migration_disposition(
            argv, argv[0], cwd=r"C:\fixture\legacy", state_token="130"
        )
        self.assertNotEqual(disposition, "not_writer")
        found = scan_dayz_mcp_processes(
            psutil_module=_Psutil([_process(130, argv, argv[0], r"C:\fixture\legacy")]),
            state_token="130",
        )
        self.assertEqual(found, (130,))

    def test_f2_independent_state_in_the_same_process_is_refused(self) -> None:
        from dayz_mcp import daemon
        from dayz_mcp.instance_context import RootWriterLease

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / state_root_name("130")
            held = RootWriterLease(root)
            self.assertTrue(held.try_acquire())
            try:
                with mock.patch.dict(os.environ, {"LOCALAPPDATA": temporary}), mock.patch.object(
                    daemon, "_activate_server_coordination"
                ) as activate, mock.patch.object(
                    daemon, "_ensure_identity_migration"
                ) as migrate:
                    with self.assertRaises(RuntimeError) as raised:
                        daemon.build_server_state(
                            SimpleNamespace(
                                instance_token="130",
                                port=8785,
                                game_path=None,
                                enable_exec_enforce=False,
                            ),
                            "another-key",
                            activate_coordination=True,
                        )
                self.assertEqual(str(raised.exception), "state_root_writer_busy")
                activate.assert_not_called()
                migrate.assert_not_called()
                from dayz_mcp import instance_context as context

                self.assertEqual(context._lease_holders[os.path.normcase(str(root))], 1)
            finally:
                held.release()

    def test_f2_same_state_may_reenter(self) -> None:
        from dayz_mcp.instance_context import RootWriterLease

        with tempfile.TemporaryDirectory() as temporary:
            owner = object()
            first = RootWriterLease(temporary, owner=owner)
            second = RootWriterLease(temporary, owner=owner)
            self.assertTrue(first.try_acquire())
            self.assertTrue(second.try_acquire())
            second.release()
            first.release()

    def test_f6_overlapping_resources_share_a_lock(self) -> None:
        from dayz_mcp.server_cli import shared_build_lock_paths

        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {"DAYZ_MCP_SHARED_ROOT": temporary}):
                target = shared_build_lock_paths(
                    r"P:\Mods\@ExampleMod\Addons", r"P:\temp\A\ExampleMod"
                )
                other_temp = shared_build_lock_paths(
                    r"P:\Mods\@ExampleMod\Addons", r"P:\temp\B\ExampleMod"
                )
                shared_temp = shared_build_lock_paths(
                    r"P:\Mods\@Other\Addons", r"P:\temp\A\ExampleMod"
                )
            self.assertTrue(set(target) & set(other_temp))
            self.assertNotEqual(set(target), set(other_temp))
            self.assertTrue(set(target) & set(shared_temp))
            self.assertNotEqual(set(target) & set(other_temp), set(target) & set(shared_temp))

    def test_f3_named_codex_registration_parses(self) -> None:
        import json
        from install_mcp import parse_codex_registration

        payload = {
            "name": "dayz-mcp-130",
            "enabled": True,
            "disabled_reason": None,
            "startup_timeout_sec": None,
            "tool_timeout_sec": None,
            "enabled_tools": None,
            "disabled_tools": None,
            "transport": {
                "type": "stdio",
                "command": r"C:\Python\python.exe",
                "args": ["-m", "dayz_mcp", "--instance", "130"],
                "cwd": None,
                "env": None,
                "env_vars": None,
            },
        }
        spec = parse_codex_registration(json.dumps(payload), server_name="dayz-mcp-130")
        self.assertIn("--instance", spec.arguments)

    def test_f3_named_timeout_builders_select_the_named_entry(self) -> None:
        from dayz_mcp.host_config import build_claude_target, build_codex_target

        claude = json.dumps({"mcpServers": {"dayz-mcp-130": {}}}).encode()
        built = json.loads(build_claude_target(claude, "dayz-mcp-130"))
        self.assertIn("timeout", built["mcpServers"]["dayz-mcp-130"])
        codex = b"[mcp_servers.dayz-mcp-130]\ncommand = \"python\"\n"
        text = build_codex_target(codex, "dayz-mcp-130").decode()
        self.assertIn("tool_timeout_sec", text)
        self.assertNotIn("[mcp_servers.dayz-mcp]", text)

    def test_f4_backup_gate_argv_forwards_the_selector(self) -> None:
        import install_mcp

        captured: list[list[str]] = []

        def runner(command, **kwargs):
            captured.append(list(command))
            return SimpleNamespace(
                returncode=0,
                stdout='{"status": "verified", "source_absent": true}',
                stderr="",
            )

        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "p0s_gate.py"
            script.write_text("# gate\n", encoding="utf-8")
            install_mcp.run_runs_backup_gate(
                sys.executable,
                Path(temporary),
                8775,
                runner=runner,
                instance_token="130",
                game_path=r"C:\DayZ",
            )
        self.assertTrue(captured)
        self.assertIn("--instance", captured[0])
        self.assertIn("130", captured[0])
        self.assertIn("--game-path", captured[0])

    def test_f5_worker_lock_root_comes_from_the_request(self) -> None:
        from dayz_mcp.dayz_test_worker import _accredited_shared_root
        from dayz_mcp.server_cli import shared_build_lock

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "shared"
            payload = {"shared_lock_root": os.path.normpath(str(root))}
            minimal = {
                "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
                "TEMP": temporary,
                "TMP": temporary,
                "DAYZ_MCP_CANCEL_HANDLE": "1",
            }
            with mock.patch.dict(os.environ, minimal, clear=True):
                accredited = _accredited_shared_root(payload)
                with shared_build_lock(
                    r"P:\Mods\@ExampleMod\Addons",
                    r"P:\temp\ExampleMod",
                    root=accredited,
                ):
                    locks = list((root / "build-locks").glob("*.lock"))
                self.assertTrue(locks)

    def test_f7_named_powershell_child_does_not_sync_global_skills(self) -> None:
        script = Path(__file__).resolve().parents[1].joinpath("install-mcp.ps1").read_text(
            encoding="utf-8"
        )
        knowledge = script.split("$knowledgePackArgs = @", 1)[1].split("& $VenvPython @knowledgePackArgs", 1)[0]
        self.assertIn('"--instance", $Instance', knowledge)
        self.assertIn("elseif ($Register)", knowledge)
        named_branch = knowledge.split("if ($Instance -ne \"\")", 1)[1].split("elseif ($Register)")[0]
        self.assertNotIn("--sync", named_branch)

    def test_f8_glued_and_duplicate_selectors_are_rejected(self) -> None:
        import install_mcp

        with self.assertRaises(SystemExit):
            install_mcp.parse_args(["--instance=130", "--port", "8775"])
        with self.assertRaises(SystemExit):
            install_mcp.parse_args(
                ["--instance", "130", "--instance", "other", "--port", "8775"]
            )
        script = Path(__file__).resolve().parents[1].joinpath("install-mcp.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("Contains('--')", script)


class Round4Findings(unittest.TestCase):
    def tearDown(self) -> None:
        reset_instance_context_for_tests()

    def test_f1_isolated_module_flag_does_not_accredit_cwd(self) -> None:
        from dayz_mcp.identity_migration import _migration_disposition

        tools = Path(__file__).resolve().parents[1]
        argv = [
            r"C:\fixture\legacy\.venv\Scripts\python.exe",
            "-I",
            "-m",
            "dayz_mcp",
            "--daemon",
            "--keyfile",
            r"C:\keys\legacy.key",
        ]
        disposition = _migration_disposition(
            argv, argv[0], cwd=str(tools), state_token="130"
        )
        self.assertEqual(disposition, "blocker")
        found = scan_dayz_mcp_processes(
            psutil_module=_Psutil([_process(8785, argv, argv[0], str(tools))]),
            state_token="130",
        )
        self.assertEqual(found, (8785,))

    def test_f1_unknown_interpreter_flag_keeps_daemon_evidence(self) -> None:
        """F1: `-z` makes import mode unestablished. The daemon argv still blocks."""
        from dayz_mcp.identity_migration import _migration_disposition

        argv = [
            r"C:\fixture\legacy\.venv\Scripts\python.exe",
            "-z",
            "-m",
            "dayz_mcp",
            "--daemon",
            "--keyfile",
            r"C:\keys\legacy.key",
        ]
        disposition = _migration_disposition(
            argv, argv[0], cwd=r"C:\fixture\legacy", state_token="130"
        )
        self.assertEqual(disposition, "blocker")
        found = scan_dayz_mcp_processes(
            psutil_module=_Psutil([_process(8786, argv, argv[0], r"C:\fixture\legacy")]),
            state_token="130",
        )
        self.assertEqual(found, (8786,))

    def test_f7_named_python_install_does_not_sync_global_skills(self) -> None:
        import install_mcp

        captured: dict[str, object] = {}

        def fake_run(options, **kwargs):
            del options, kwargs
            return {
                "status": "installed_and_registered",
                "registered": True,
                "venv_python": sys.executable,
            }

        def fake_pack(**kwargs):
            captured.update(kwargs)
            return {"status": "ready"}

        with mock.patch.object(install_mcp, "run_installer", fake_run), mock.patch.object(
            install_mcp, "install_knowledge_pack", fake_pack
        ):
            rc = install_mcp.main(
                ["--instance", "130", "--port", "8775", "--register"]
            )
        self.assertEqual(rc, 0)
        self.assertIs(captured["sync"], False)
        self.assertEqual(captured["owner"], "130")

    def _powershell_validate(self, *arguments: str, env: dict[str, str] | None = None):
        script = Path(__file__).resolve().parents[1] / "install-mcp.ps1"
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-File",
                str(script),
                "-ValidateOnly",
                *arguments,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        return completed

    def test_f8_powershell_rejects_uppercase_token(self) -> None:
        completed = self._powershell_validate("-Instance", "UPPER", "-Port", "8775")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("invalid_instance_token", completed.stderr)

    def test_f8_powershell_rejects_glued_instance_parameter(self) -> None:
        completed = self._powershell_validate("-Instance=130", "-Port", "8775")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("unconsumed_selector_argument", completed.stderr)

    def test_f8_powershell_rejects_glued_gnu_instance(self) -> None:
        completed = self._powershell_validate("--instance=130", "-Port", "8775")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("unconsumed_selector_argument", completed.stderr)

    def test_f8_powershell_rejects_relative_game_path(self) -> None:
        completed = self._powershell_validate(
            "-Instance", "130", "-Port", "8775", "-GamePath", "relative"
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("invalid_game_path", completed.stderr)

    def test_f8_powershell_rejects_inherited_instance_conflict(self) -> None:
        env = dict(os.environ)
        env["DAYZ_MCP_INSTANCE"] = "other"
        completed = self._powershell_validate(
            "-Instance", "130", "-Port", "8775", env=env
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("instance_environment_conflict", completed.stderr)

    def test_f8_powershell_validate_only_accepts_named_selector(self) -> None:
        env = dict(os.environ)
        env.pop("DAYZ_MCP_INSTANCE", None)
        env.pop("DAYZ_MCP_PORT", None)
        env.pop("DAYZ_MCP_GAME_PATH", None)
        completed = self._powershell_validate(
            "-Instance", "130", "-Port", "8775", env=env
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_f8_gate_rejects_duplicate_instance(self) -> None:
        import p0s_gate

        with mock.patch(
            "dayz_mcp.identity_migration.ensure_runs_v1_backup"
        ) as backup:
            rc = p0s_gate.main(
                [
                    "backup-runs-v1",
                    "--instance",
                    "130",
                    "--instance",
                    "other",
                    "--port",
                    "8775",
                ]
            )
        self.assertEqual(rc, 2)
        backup.assert_not_called()

    def test_f8_secure_launcher_rejects_duplicate_instance(self) -> None:
        from dayz_mcp import secure_launcher

        with mock.patch.object(secure_launcher, "run_secure_launcher") as launch:
            rc = secure_launcher.main(
                ["launcher", "--instance", "130", "--instance", "other"]
            )
        self.assertEqual(rc, 2)
        launch.assert_not_called()

    def test_f8_python_parsers_reject_glued_instance(self) -> None:
        import install_mcp
        import p0s_gate
        from dayz_mcp import secure_launcher

        with self.assertRaises(SystemExit):
            install_mcp.parse_args(["--instance=130", "--port", "8775"])
        with mock.patch(
            "dayz_mcp.identity_migration.ensure_runs_v1_backup"
        ) as backup:
            rc = p0s_gate.main(
                ["backup-runs-v1", "--instance=130", "--port", "8775"]
            )
        self.assertEqual(rc, 2)
        backup.assert_not_called()
        with mock.patch.object(secure_launcher, "run_secure_launcher") as launch:
            rc = secure_launcher.main(["launcher", "--instance=130"])
        self.assertEqual(rc, 2)
        launch.assert_not_called()

    def test_f8_entry_points_reject_relative_game_path_and_env_conflict(self) -> None:
        import install_mcp
        import p0s_gate
        from dayz_mcp import secure_launcher

        with self.assertRaises(SystemExit):
            install_mcp.parse_args(
                ["--instance", "130", "--port", "8775", "--game-path", "relative"]
            )
        with mock.patch.dict(os.environ, {"DAYZ_MCP_INSTANCE": "other"}):
            with self.assertRaises(SystemExit):
                install_mcp.parse_args(["--instance", "130", "--port", "8775"])
            with mock.patch(
                "dayz_mcp.identity_migration.ensure_runs_v1_backup"
            ) as backup:
                rc = p0s_gate.main(
                    ["backup-runs-v1", "--instance", "130", "--port", "8775"]
                )
            self.assertEqual(rc, 2)
            backup.assert_not_called()
            with mock.patch.object(secure_launcher, "run_secure_launcher") as launch:
                rc = secure_launcher.main(["launcher", "--instance", "130"])
            self.assertEqual(rc, 2)
            launch.assert_not_called()

    def test_f8_install_rejects_abbreviated_instance(self) -> None:
        import install_mcp

        with self.assertRaises(SystemExit):
            options = install_mcp.parse_args(["--inst", "UPPER", "--port", "8775"])
            self.assertIsNotNone(options.instance_token)

    def test_f8_backup_rejects_abbreviated_instance(self) -> None:
        import p0s_gate

        with mock.patch("dayz_mcp.identity_migration.ensure_runs_v1_backup") as backup:
            with self.assertRaises(SystemExit) as caught:
                p0s_gate.main(["backup-runs-v1", "--inst", "UPPER", "--port", "8775"])
        backup.assert_not_called()
        self.assertNotEqual(caught.exception.code, 0)

    def test_f8_secure_launcher_rejects_abbreviated_instance(self) -> None:
        from dayz_mcp import secure_launcher

        with mock.patch.object(secure_launcher, "run_secure_launcher") as launch:
            with self.assertRaises(SystemExit) as caught:
                secure_launcher.main(["launcher", "--inst", "UPPER"])
        launch.assert_not_called()
        self.assertNotEqual(caught.exception.code, 0)

    def test_f8_abbreviated_duplicate_does_not_select_the_long_flag(self) -> None:
        import install_mcp

        with self.assertRaises(SystemExit):
            install_mcp.parse_args(
                ["--instance", "130", "--inst", "other", "--port", "8775"]
            )

    def test_f8_powershell_rejects_drive_relative_game_path(self) -> None:
        completed = self._powershell_validate(
            "-Instance", "130", "-Port", "8775", "-GamePath", "C:relative"
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("invalid_game_path", completed.stderr)

    def test_f8_powershell_rejects_root_relative_game_path(self) -> None:
        completed = self._powershell_validate(
            "-Instance", "130", "-Port", "8775", "-GamePath", r"\relative"
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("invalid_game_path", completed.stderr)

    def test_f8_powershell_rejects_token_with_trailing_newline(self) -> None:
        completed = self._powershell_validate(
            "-Instance", "130\n", "-Port", "8775"
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("invalid_instance_token", completed.stderr)

    def test_f10_fixtures_keep_separate_shared_roots(self) -> None:
        from tests.lifecycle_helpers import LifecycleFixture

        previous = os.environ.get("DAYZ_MCP_SHARED_ROOT")
        os.environ.pop("DAYZ_MCP_SHARED_ROOT", None)
        first = LifecycleFixture()
        try:
            first_shared = os.environ["DAYZ_MCP_SHARED_ROOT"]
            self.assertTrue(
                os.path.normcase(first_shared).startswith(
                    os.path.normcase(str(first.root))
                )
            )
        finally:
            first.close()
        self.assertIsNone(os.environ.get("DAYZ_MCP_SHARED_ROOT"))
        second = LifecycleFixture()
        try:
            second_shared = os.environ["DAYZ_MCP_SHARED_ROOT"]
            self.assertTrue(
                os.path.normcase(second_shared).startswith(
                    os.path.normcase(str(second.root))
                )
            )
            self.assertNotEqual(
                os.path.normcase(first_shared), os.path.normcase(second_shared)
            )
        finally:
            second.close()
        if previous is None:
            os.environ.pop("DAYZ_MCP_SHARED_ROOT", None)
        else:
            os.environ["DAYZ_MCP_SHARED_ROOT"] = previous

    def test_f10_inherited_shared_root_is_replaced_and_restored(self) -> None:
        from tests.lifecycle_helpers import LifecycleFixture

        previous = os.environ.get("DAYZ_MCP_SHARED_ROOT")
        inherited = r"C:\fixture\inherited-shared-root"
        os.environ["DAYZ_MCP_SHARED_ROOT"] = inherited
        try:
            fixture = LifecycleFixture()
            try:
                shared = os.environ["DAYZ_MCP_SHARED_ROOT"]
                self.assertTrue(
                    os.path.normcase(shared).startswith(
                        os.path.normcase(str(fixture.root))
                    )
                )
                self.assertNotEqual(os.path.normcase(shared), os.path.normcase(inherited))
            finally:
                fixture.close()
            self.assertEqual(os.environ.get("DAYZ_MCP_SHARED_ROOT"), inherited)
            with mock.patch(
                "tests.lifecycle_helpers.SessionCoordinator.acquire",
                side_effect=RuntimeError("boom"),
            ):
                with self.assertRaises(RuntimeError):
                    LifecycleFixture()
            self.assertEqual(os.environ.get("DAYZ_MCP_SHARED_ROOT"), inherited)
        finally:
            if previous is None:
                os.environ.pop("DAYZ_MCP_SHARED_ROOT", None)
            else:
                os.environ["DAYZ_MCP_SHARED_ROOT"] = previous


if __name__ == "__main__":
    unittest.main()
