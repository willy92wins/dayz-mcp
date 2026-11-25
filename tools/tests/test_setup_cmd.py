from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import install_mcp
from dayz_mcp import entry, setup_cmd
from dayz_mcp.server_cli import InstanceSelectionError, reset_instance_context_for_tests
from tests.pe_helpers import write_fake_x64_pe


def _completed(returncode: int, stdout: str, stderr: str) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail_gate = False

    def __call__(self, argv: object, **_kwargs: object) -> SimpleNamespace:
        command = [str(part) for part in argv]  # type: ignore[arg-type]
        self.calls.append(command)
        if any(part.endswith("p0s_gate.py") for part in command):
            if self.fail_gate:
                return _completed(1, "", "gate failed\n")
            return _completed(0, '{"source_absent":true,"status":"verified"}\n', "")
        name = Path(command[0]).name.casefold()
        if name in {"claude.exe", "codex.exe"}:
            role = name.removesuffix(".exe")
            probe = "p0s-absent-fixture-do-not-create"
            if role == "claude":
                return _completed(
                    1, "", f'No MCP server named "{probe}". Run `claude mcp add` to add one.\n'
                )
            return _completed(1, "", f"Error: No MCP server named '{probe}' found.\n")
        raise AssertionError(f"unexpected runner command: {command!r}")


class EntryPlatformTest(unittest.TestCase):
    def test_platform_unsupported_is_exact_and_writes_nothing(self) -> None:
        with TemporaryDirectory() as temporary:
            local = Path(temporary) / "LocalAppData"
            local.mkdir()
            buffer = io.StringIO()
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                with redirect_stdout(buffer):
                    code = entry.main(["setup", "--json"], platform="linux")
            expected = {
                "error": {
                    "code": "platform_unsupported",
                    "remedy": "dayz-mcp setup and doctor run only on Windows; on Linux, run python -m dayz_mcp from a clone as before",
                },
                "missing": [],
                "ok": False,
            }
            self.assertEqual(code, 2)
            self.assertEqual(buffer.getvalue(), json.dumps(expected, sort_keys=True) + "\n")
            self.assertEqual(list(local.rglob("*")), [])

    def test_entry_without_a_subcommand_exits_2(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            entry.main([], platform="win32")
        self.assertEqual(raised.exception.code, 2)

    def test_setup_subcommand_forwards_the_arguments_without_setup(self) -> None:
        with mock.patch.object(setup_cmd, "main") as fake:
            fake.return_value = 7
            code = entry.main(["setup", "--json", "--port", "1"], platform="win32")
        self.assertEqual(code, 7)
        fake.assert_called_once_with(["--json", "--port", "1"])

    def test_doctor_subcommand_forwards_the_arguments_without_doctor(self) -> None:
        from dayz_mcp import doctor

        with mock.patch.object(doctor, "main") as fake:
            fake.return_value = 9
            code = entry.main(["doctor", "--json"], platform="win32")
        self.assertEqual(code, 9)
        fake.assert_called_once_with(["--json"])


@unittest.skipUnless(sys.platform == "win32", "requires Windows")
class SetupCmdTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.local_appdata = self.base / "LocalAppData"
        self.local_appdata.mkdir()
        self.state = self.local_appdata / "DayZ_MCP"
        self.home = self.base / "home"
        self.home.mkdir()
        self.venv_root = self.base / "venv"
        (self.venv_root / "Scripts").mkdir(parents=True)
        (self.venv_root / "pyvenv.cfg").write_text("home = C:\\Python\n", encoding="utf-8")
        self.venv_python = self.venv_root / "Scripts" / "python.exe"
        write_fake_x64_pe(self.venv_python)
        self.cli_dir = self.base / "clis"
        self.cli_dir.mkdir()
        self.claude_exe = self.cli_dir / "claude.exe"
        self.codex_exe = self.cli_dir / "codex.exe"
        write_fake_x64_pe(self.claude_exe)
        write_fake_x64_pe(self.codex_exe)
        self.steam = self.base / "Steam"
        self.lib_one = self.base / "LibraryOne"
        self.lib_two = self.base / "LibraryTwo"
        self._write_libraryfolders()
        self.dayz = self.lib_two / "steamapps" / "common" / "DayZ"
        self.dayz.mkdir(parents=True)
        (self.dayz / "DayZ_x64.exe").write_bytes(b"dayz")
        self.item = (
            self.lib_two / "steamapps" / "workshop" / "content" / "221100" / "123"
        )
        (self.item / "Addons").mkdir(parents=True)
        self.pbo_bytes = b"fake-pbo-bytes"
        (self.item / "Addons" / "DayZ_MCP.pbo").write_bytes(self.pbo_bytes)
        self.pbo_sha = hashlib.sha256(self.pbo_bytes).hexdigest()
        self.write_item_version("1.6", self.pbo_sha)
        self.wheel_version_file = self.base / "wheel_VERSION.json"
        self.write_wheel_version("1.6", self.pbo_sha)
        self.runner = FakeRunner()
        self.registry_calls: list[tuple[str, str, str]] = []
        self.register_calls: list[dict[str, object]] = []
        self.env_patch = mock.patch.dict(
            os.environ,
            {
                "LOCALAPPDATA": str(self.local_appdata),
                "USERPROFILE": str(self.home),
                "PATH": str(self.cli_dir),
            },
            clear=True,
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.addCleanup(reset_instance_context_for_tests)
        self._patches = [
            mock.patch.object(setup_cmd, "installed_venv_root", return_value=self.venv_root),
            mock.patch.object(setup_cmd, "_wheel_version_path", return_value=self.wheel_version_file),
            mock.patch.object(setup_cmd, "WORKSHOP_ITEM_ID", "123"),
            mock.patch.object(
                install_mcp, "register_transaction", side_effect=self._fake_register
            ),
        ]
        for patcher in self._patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _fake_register(self, provider: object, desired: object, **kwargs: object) -> None:
        self.register_calls.append(
            {"provider": provider, "desired": desired, "kwargs": kwargs}
        )

    def _write_libraryfolders(self) -> None:
        steamapps = self.steam / "steamapps"
        steamapps.mkdir(parents=True)

        def escaped(path: Path) -> str:
            return str(path).replace("\\", "\\\\")

        text = (
            '"libraryfolders"\n{\n'
            f'    "0"\n    {{\n        "path"    "{escaped(self.lib_one)}"\n'
            '        "apps"\n        {\n            "228980"    "100"\n        }\n    }\n'
            f'    "1"\n    {{\n        "path"    "{escaped(self.lib_two)}"\n'
            '        "apps"\n        {\n            "221100"    "200"\n        }\n    }\n'
            "}\n"
        )
        (steamapps / "libraryfolders.vdf").write_text(text, encoding="utf-8")

    def write_wheel_version(self, version: object, sha: object) -> None:
        self.wheel_version_file.write_text(
            json.dumps({"version": version, "pbo_sha256": sha}), encoding="utf-8"
        )

    def write_item_version(self, version: object, sha: object) -> None:
        (self.item / "VERSION.json").write_text(
            json.dumps({"version": version, "pbo_sha256": sha}), encoding="utf-8"
        )

    def fake_registry(self, hive: str, subkey: str, value: str) -> str | None:
        self.registry_calls.append((hive, subkey, value))
        if (hive, subkey, value) == setup_cmd.dayz_tools_paths.REGISTRY_STEAM_KEYS[0]:
            return str(self.steam)
        return None

    def exploding_registry(self, hive: str, subkey: str, value: str) -> str | None:
        raise AssertionError("registry must not be read")

    def run_setup(
        self,
        args: list[str] | None = None,
        *,
        registry: object = None,
        runner: FakeRunner | None = None,
    ) -> tuple[int, dict[str, object], str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = setup_cmd.main(
                ["--json"] if args is None else args,
                runner=self.runner if runner is None else runner,  # type: ignore[arg-type]
                registry=self.fake_registry if registry is None else registry,  # type: ignore[arg-type]
                token_factory=lambda: "test-key-123",
            )
        text = buffer.getvalue()
        return code, json.loads(text), text

    def good_args(self) -> list[str]:
        return [
            "--json",
            "--claude-exe",
            str(self.claude_exe),
            "--codex-exe",
            str(self.codex_exe),
        ]

    def expected_success(self) -> dict[str, object]:
        server = self.state / "profiles" / "server"
        client = self.state / "profiles" / "client"
        return {
            "client_profiles_arg": f"-profiles={client}",
            "item": str(self.item),
            "keyfile": str(self.state / "dayz_mcp.key"),
            "missing": [],
            "mod_arg": f"-mod={self.item}",
            "ok": True,
            "port": 8765,
            "python": str(self.venv_python),
            "registered": ["claude", "codex"],
            "server_name": "dayz-mcp",
            "server_profiles_arg": f"-profiles={server}",
            "version": "1.6",
        }

    def assert_no_state_writes(self) -> None:
        self.assertEqual(list(self.local_appdata.rglob("*")), [])

    def venv_snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.venv_root)): path.read_bytes()
            for path in self.venv_root.rglob("*")
            if path.is_file()
        }

    def test_good_case_registers_and_writes_state(self) -> None:
        before = self.venv_snapshot()
        code, payload, text = self.run_setup(self.good_args())
        expected = self.expected_success()
        self.assertEqual(code, 0)
        self.assertEqual(payload, expected)
        self.assertEqual(text, json.dumps(expected, sort_keys=True) + "\n")
        keyfile = self.state / "dayz_mcp.key"
        self.assertEqual(keyfile.read_text(encoding="ascii"), "test-key-123\n")
        payload_text = install_mcp.config_payload(8765, "test-key-123")
        for leaf in ("server", "client"):
            config = self.state / "profiles" / leaf / "dayz_mcp.json"
            self.assertEqual(config.read_bytes(), (payload_text + "\n").encode("ascii"))
        manifest = install_mcp.load_installer_cli_manifest(
            install_mcp.installer_cli_manifest_path()
        )
        self.assertEqual(manifest.entries["CLAUDE"].path, self.claude_exe.resolve())
        self.assertEqual(manifest.entries["CODEX"].path, self.codex_exe.resolve())
        self.assertEqual(len(self.register_calls), 1)
        self.assertEqual(
            self.register_calls[0]["kwargs"],
            {
                "allow_option_removal": False,
                "host_configs": (
                    self.home / ".claude.json",
                    self.home / ".codex" / "config.toml",
                ),
            },
        )
        desired = self.register_calls[0]["desired"]
        options = install_mcp.InstallerOptions(
            port=8765,
            keyfile=keyfile,
            server_profiles=self.state / "profiles" / "server",
            client_profiles=self.state / "profiles" / "client",
            mission_path=None,
            expected_game_version="",
            idle_timeout_seconds=1800.0,
            allow_legacy=False,
            register=True,
            pin_clis=False,
            claude_exe=None,
            codex_exe=None,
            tools_root=Path(install_mcp.__file__).resolve().parent,
            skip_knowledge_pack=True,
            instance_token=None,
        )
        self.assertEqual(
            desired,
            {
                "CLAUDE": install_mcp.RegistrationSpec(
                    self.venv_python, tuple(install_mcp.build_client_args(options, "claude"))
                ),
                "CODEX": install_mcp.RegistrationSpec(
                    self.venv_python, tuple(install_mcp.build_client_args(options, "codex"))
                ),
            },
        )
        self.assertEqual(self.venv_snapshot(), before)

    def test_phase_two_passes_the_exact_options_to_register_clients(self) -> None:
        seen: dict[str, object] = {}

        def fake_register_clients(options: object, venv_python: object, *, runner: object) -> None:
            seen["options"] = options
            seen["venv_python"] = venv_python
            seen["runner"] = runner

        with mock.patch.object(
            install_mcp, "register_clients", side_effect=fake_register_clients
        ):
            code, _payload, _text = self.run_setup(self.good_args())
        self.assertEqual(code, 0)
        self.assertEqual(seen["venv_python"], self.venv_python)
        self.assertIs(seen["runner"], self.runner)
        self.assertEqual(
            seen["options"],
            install_mcp.InstallerOptions(
                port=8765,
                keyfile=self.state / "dayz_mcp.key",
                server_profiles=self.state / "profiles" / "server",
                client_profiles=self.state / "profiles" / "client",
                mission_path=None,
                expected_game_version="",
                idle_timeout_seconds=1800.0,
                allow_legacy=False,
                register=True,
                pin_clis=False,
                claude_exe=None,
                codex_exe=None,
                tools_root=Path(install_mcp.__file__).resolve().parent,
                skip_knowledge_pack=True,
                instance_token=None,
            ),
        )

    def test_good_case_finds_the_clis_on_the_path(self) -> None:
        code, payload, text = self.run_setup(["--json"])
        expected = self.expected_success()
        self.assertEqual(code, 0)
        self.assertEqual(payload, expected)
        self.assertEqual(text, json.dumps(expected, sort_keys=True) + "\n")
        manifest = install_mcp.load_installer_cli_manifest(
            install_mcp.installer_cli_manifest_path()
        )
        self.assertEqual(manifest.entries["CLAUDE"].path, self.claude_exe.resolve())
        self.assertEqual(manifest.entries["CODEX"].path, self.codex_exe.resolve())

    def test_good_case_writes_the_literal_config_bytes(self) -> None:
        code, _payload, _text = self.run_setup(self.good_args())
        self.assertEqual(code, 0)
        expected = b'{"url":"http://127.0.0.1:8765/","key":"test-key-123","pollHz":5}\n'
        for leaf in ("server", "client"):
            config = self.state / "profiles" / leaf / "dayz_mcp.json"
            self.assertEqual(config.read_bytes(), expected)

    def test_phase_two_failure_keeps_the_error_remedy(self) -> None:
        failure = install_mcp.InstallerExecutionError("pin_step_failed")
        failure.remedy = "close the client CLIs, then run dayz-mcp setup --json again"
        with mock.patch.object(
            install_mcp, "pin_installer_clis", side_effect=failure
        ):
            code, payload, text = self.run_setup(self.good_args())
        expected = {
            "error": {
                "code": "pin_step_failed",
                "remedy": "close the client CLIs, then run dayz-mcp setup --json again",
            },
            "missing": [],
            "ok": False,
        }
        self.assertEqual(code, 2)
        self.assertEqual(payload, expected)
        self.assertEqual(text, json.dumps(expected, sort_keys=True) + "\n")

    def test_register_clients_pins_only_when_both_exes_are_set(self) -> None:
        def options(
            claude_exe: Path | None, codex_exe: Path | None
        ) -> install_mcp.InstallerOptions:
            return install_mcp.InstallerOptions(
                port=8765,
                keyfile=self.state / "dayz_mcp.key",
                server_profiles=self.state / "profiles" / "server",
                client_profiles=self.state / "profiles" / "client",
                mission_path=None,
                expected_game_version="",
                idle_timeout_seconds=1800.0,
                allow_legacy=False,
                register=True,
                pin_clis=False,
                claude_exe=claude_exe,
                codex_exe=codex_exe,
                tools_root=Path(install_mcp.__file__).resolve().parent,
                skip_knowledge_pack=True,
                instance_token=None,
            )

        for patcher in (
            mock.patch.object(
                install_mcp, "load_installer_cli_manifest", return_value=object()
            ),
            mock.patch.object(
                install_mcp, "load_installer_not_found_fixtures", return_value=object()
            ),
            mock.patch.object(
                install_mcp, "CliRegistrationProvider", return_value=object()
            ),
            mock.patch.object(install_mcp, "run_runs_backup_gate", return_value=None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        with mock.patch.object(install_mcp, "pin_installer_clis") as pin:
            install_mcp.register_clients(
                options(self.claude_exe, None), self.venv_python, runner=self.runner
            )
        pin.assert_not_called()
        with mock.patch.object(install_mcp, "pin_installer_clis") as pin:
            install_mcp.register_clients(
                options(self.claude_exe, self.codex_exe),
                self.venv_python,
                runner=self.runner,
            )
        pin.assert_called_once_with(
            claude_exe=self.claude_exe, codex_exe=self.codex_exe, runner=self.runner
        )

    def test_second_pass_is_idempotent(self) -> None:
        first_code, _first, _text = self.run_setup(self.good_args())
        self.assertEqual(first_code, 0)
        key_before = (self.state / "dayz_mcp.key").read_bytes()
        configs_before = {
            leaf: (self.state / "profiles" / leaf / "dayz_mcp.json").read_bytes()
            for leaf in ("server", "client")
        }
        desired_before = self.register_calls[-1]["desired"]
        code, payload, _text = self.run_setup(self.good_args())
        self.assertEqual(code, 0)
        self.assertEqual(payload, self.expected_success())
        self.assertEqual((self.state / "dayz_mcp.key").read_bytes(), key_before)
        self.assertEqual(
            {
                leaf: (self.state / "profiles" / leaf / "dayz_mcp.json").read_bytes()
                for leaf in ("server", "client")
            },
            configs_before,
        )
        self.assertEqual(self.register_calls[-1]["desired"], desired_before)
        self.assertEqual(len(self.register_calls), 2)

    def assert_rejected(
        self,
        args: list[str],
        expected_missing: list[dict[str, object]],
        *,
        registry: object = None,
    ) -> None:
        code, payload, text = self.run_setup(args, registry=registry)
        expected = {"error": expected_missing[0], "missing": expected_missing, "ok": False}
        self.assertEqual(code, 2)
        self.assertEqual(payload, expected)
        self.assertEqual(text, json.dumps(expected, sort_keys=True) + "\n")
        self.assert_no_state_writes()
        self.assertEqual(self.register_calls, [])

    def test_reject_not_installed_from_wheel(self) -> None:
        with mock.patch.object(setup_cmd, "installed_venv_root", return_value=None):
            self.assert_rejected(
                self.good_args(),
                [
                    {
                        "code": "not_installed_from_wheel",
                        "remedy": "run setup from the venv that has the dayz-mcp wheel installed; from a clone, follow QUICKSTART.md",
                    }
                ],
            )

    def test_reject_instance_selection_error_alone(self) -> None:
        self.assert_rejected(
            ["--json", "--instance", "Invalid_Token"],
            [{"code": "invalid_instance_token"}],
        )

    def test_reject_instance_error_from_the_environment_check(self) -> None:
        with mock.patch.object(
            setup_cmd,
            "reject_conflicting_environment",
            side_effect=InstanceSelectionError("instance_environment_conflict"),
        ) as check:
            self.assert_rejected(
                self.good_args(),
                [{"code": "instance_environment_conflict"}],
            )
        check.assert_called_once_with(None, 8765)

    def test_reject_steam_missing(self) -> None:
        self.assert_rejected(
            self.good_args(),
            [{"code": "steam_missing", "remedy": "install Steam, or pass --steam PATH"}],
            registry=lambda *_args: None,
        )

    def test_reject_dayz_missing(self) -> None:
        (self.dayz / "DayZ_x64.exe").unlink()
        self.assert_rejected(
            self.good_args(),
            [
                {
                    "code": "dayz_missing",
                    "remedy": "install DayZ from Steam and start it once, or pass --dayz PATH",
                }
            ],
        )

    def test_reject_dayz_missing_when_no_library_holds_the_game(self) -> None:
        escaped = str(self.lib_one).replace("\\", "\\\\")
        text = (
            '"libraryfolders"\n{\n'
            f'    "0"\n    {{\n        "path"    "{escaped}"\n'
            '        "apps"\n        {\n            "228980"    "100"\n        }\n    }\n'
            "}\n"
        )
        (self.steam / "steamapps" / "libraryfolders.vdf").write_text(
            text, encoding="utf-8"
        )
        self.assert_rejected(
            self.good_args(),
            [
                {
                    "code": "dayz_missing",
                    "remedy": "install DayZ from Steam and start it once, or pass --dayz PATH",
                },
                {
                    "code": "workshop_item_missing",
                    "remedy": "subscribe to the DayZ-MCP item in the Steam Workshop, let Steam download it, then run dayz-mcp setup --json again; or pass --item PATH",
                    "url": "https://steamcommunity.com/sharedfiles/filedetails/?id=123",
                },
            ],
        )

    def test_reject_workshop_item_missing_with_url(self) -> None:
        (self.item / "Addons" / "DayZ_MCP.pbo").unlink()
        self.assert_rejected(
            self.good_args(),
            [
                {
                    "code": "workshop_item_missing",
                    "path": str(self.item),
                    "remedy": "subscribe to the DayZ-MCP item in the Steam Workshop, let Steam download it, then run dayz-mcp setup --json again; or pass --item PATH",
                    "url": "https://steamcommunity.com/sharedfiles/filedetails/?id=123",
                }
            ],
        )

    def test_reject_profiles_missing_server(self) -> None:
        missing_dir = self.base / "no-server-profiles"
        self.assert_rejected(
            self.good_args() + ["--server-profiles", str(missing_dir)],
            [
                {
                    "code": "profiles_missing",
                    "path": str(missing_dir),
                    "remedy": "create that folder or leave the option out",
                    "which": "server",
                }
            ],
        )

    def test_reject_profiles_missing_client(self) -> None:
        missing_dir = self.base / "no-client-profiles"
        self.assert_rejected(
            self.good_args() + ["--client-profiles", str(missing_dir)],
            [
                {
                    "code": "profiles_missing",
                    "path": str(missing_dir),
                    "remedy": "create that folder or leave the option out",
                    "which": "client",
                }
            ],
        )

    def test_reject_version_json_missing_wheel(self) -> None:
        self.wheel_version_file.unlink()
        self.assert_rejected(
            self.good_args(),
            [
                {
                    "code": "version_json_missing",
                    "path": str(self.wheel_version_file),
                    "remedy": "reinstall the dayz-mcp wheel of this release, then run dayz-mcp setup --json again",
                    "which": "wheel",
                }
            ],
        )

    def test_reject_version_json_missing_item(self) -> None:
        (self.item / "VERSION.json").unlink()
        self.assert_rejected(
            self.good_args(),
            [
                {
                    "code": "version_json_missing",
                    "path": str(self.item / "VERSION.json"),
                    "remedy": "let Steam download the DayZ-MCP Workshop item of this release, then run dayz-mcp setup --json again",
                    "which": "item",
                }
            ],
        )

    def test_reject_version_json_invalid(self) -> None:
        self.wheel_version_file.write_text("[]", encoding="utf-8")
        self.assert_rejected(
            self.good_args(),
            [
                {
                    "code": "version_json_invalid",
                    "path": str(self.wheel_version_file),
                    "remedy": "reinstall the dayz-mcp wheel of this release, then run dayz-mcp setup --json again",
                    "which": "wheel",
                }
            ],
        )

    def test_reject_version_json_invalid_when_the_sha_is_not_a_string(self) -> None:
        self.write_wheel_version("1.6", 5)
        self.assert_rejected(
            self.good_args(),
            [
                {
                    "code": "version_json_invalid",
                    "path": str(self.wheel_version_file),
                    "remedy": "reinstall the dayz-mcp wheel of this release, then run dayz-mcp setup --json again",
                    "which": "wheel",
                }
            ],
        )

    def _halves_problem(self, direction: str, item_version: str, wheel_version: str) -> dict[str, object]:
        if direction == "item_ahead":
            remedy = f"install the dayz-mcp wheel of release {item_version}, then run dayz-mcp setup --json again"
        elif direction == "item_behind":
            remedy = "let Steam update the DayZ-MCP Workshop item, then run dayz-mcp setup --json again"
        else:
            remedy = "install the dayz-mcp wheel and the Workshop item of the same release, then run dayz-mcp setup --json again"
        return {
            "code": "halves_mismatch",
            "direction": direction,
            "item_version": item_version,
            "remedy": remedy,
            "wheel_version": wheel_version,
        }

    def test_reject_halves_item_ahead(self) -> None:
        self.write_item_version("1.7", self.pbo_sha)
        self.write_wheel_version("1.6", "0" * 64)
        self.assert_rejected(self.good_args(), [self._halves_problem("item_ahead", "1.7", "1.6")])

    def test_reject_halves_item_behind(self) -> None:
        self.write_item_version("1.5", self.pbo_sha)
        self.write_wheel_version("1.6", "0" * 64)
        self.assert_rejected(self.good_args(), [self._halves_problem("item_behind", "1.5", "1.6")])

    def test_reject_halves_differs(self) -> None:
        self.write_wheel_version("1.6", "0" * 64)
        self.assert_rejected(self.good_args(), [self._halves_problem("differs", "1.6", "1.6")])

    def test_reject_workshop_item_unreadable(self) -> None:
        pbo_path = self.item / "Addons" / "DayZ_MCP.pbo"
        real_read_bytes = Path.read_bytes

        def fake_read_bytes(
            path: Path, *args: object, **kwargs: object
        ) -> bytes:
            if path == pbo_path:
                raise PermissionError("the PBO is locked")
            return real_read_bytes(path, *args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(Path, "read_bytes", fake_read_bytes):
            self.assert_rejected(
                self.good_args(),
                [
                    {
                        "code": "workshop_item_unreadable",
                        "path": str(pbo_path),
                        "remedy": "close whatever holds DayZ_MCP.pbo open (Steam may still be updating the item), then run dayz-mcp setup --json again",
                    }
                ],
            )

    def test_reject_port_zero(self) -> None:
        self.assert_rejected(
            self.good_args() + ["--port", "0"],
            [
                {
                    "code": "port_out_of_range",
                    "remedy": "pass --port between 1 and 65535",
                }
            ],
        )

    def test_reject_port_above_65535(self) -> None:
        self.assert_rejected(
            self.good_args() + ["--port", "65536"],
            [
                {
                    "code": "port_out_of_range",
                    "remedy": "pass --port between 1 and 65535",
                }
            ],
        )

    def test_port_65535_passes(self) -> None:
        code, payload, text = self.run_setup(
            self.good_args() + ["--port", "65535"]
        )
        expected = self.expected_success()
        expected["port"] = 65535
        self.assertEqual(code, 0)
        self.assertEqual(payload, expected)
        self.assertEqual(text, json.dumps(expected, sort_keys=True) + "\n")

    def test_reject_halves_with_a_non_decimal_digit_version(self) -> None:
        self.write_item_version("1.\u00b2", self.pbo_sha)
        self.write_wheel_version("1.6", "0" * 64)
        self.assert_rejected(
            self.good_args(),
            [self._halves_problem("differs", "1.\u00b2", "1.6")],
        )

    def test_reject_codex_home_unsupported(self) -> None:
        elsewhere = self.base / "elsewhere" / ".codex"
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(elsewhere)}):
            self.assert_rejected(
                self.good_args(),
                [
                    {
                        "code": "codex_home_unsupported",
                        "path": str(elsewhere),
                        "remedy": "unset CODEX_HOME or set it to %USERPROFILE%\\.codex: the daemon reads only that Codex config",
                    }
                ],
            )

    def test_codex_home_at_userprofile_with_other_case_passes(self) -> None:
        value = str(self.home / ".codex").upper()
        with mock.patch.dict(os.environ, {"CODEX_HOME": value}):
            code, payload, _text = self.run_setup(self.good_args())
        self.assertEqual(code, 0)
        self.assertEqual(payload, self.expected_success())

    def test_codex_home_defined_but_empty_passes(self) -> None:
        with mock.patch.dict(os.environ, {"CODEX_HOME": ""}):
            code, payload, _text = self.run_setup(self.good_args())
        self.assertEqual(code, 0)
        self.assertEqual(payload, self.expected_success())

    def test_reject_installer_cli_missing_claude(self) -> None:
        empty = self.base / "empty-path"
        empty.mkdir()
        args = ["--json", "--codex-exe", str(self.codex_exe)]
        with mock.patch.dict(os.environ, {"PATH": str(empty)}):
            self.assert_rejected(
                args,
                [
                    {
                        "code": "installer_cli_missing",
                        "remedy": "install the native claude CLI, or pass --claude-exe PATH",
                        "role": "claude",
                    }
                ],
            )

    def test_reject_installer_cli_missing_codex(self) -> None:
        empty = self.base / "empty-path"
        empty.mkdir()
        args = ["--json", "--claude-exe", str(self.claude_exe)]
        with mock.patch.dict(os.environ, {"PATH": str(empty)}):
            self.assert_rejected(
                args,
                [
                    {
                        "code": "installer_cli_missing",
                        "remedy": "install the native codex CLI, or pass --codex-exe PATH",
                        "role": "codex",
                    }
                ],
            )

    def test_multiple_problems_keep_the_fixed_order(self) -> None:
        (self.dayz / "DayZ_x64.exe").unlink()
        missing_client = self.base / "no-client-profiles"
        elsewhere = self.base / "elsewhere" / ".codex"
        empty = self.base / "empty-path"
        empty.mkdir()
        args = [
            "--json",
            "--client-profiles",
            str(missing_client),
            "--codex-exe",
            str(self.codex_exe),
        ]
        expected = [
            {
                "code": "dayz_missing",
                "remedy": "install DayZ from Steam and start it once, or pass --dayz PATH",
            },
            {
                "code": "profiles_missing",
                "path": str(missing_client),
                "remedy": "create that folder or leave the option out",
                "which": "client",
            },
            {
                "code": "codex_home_unsupported",
                "path": str(elsewhere),
                "remedy": "unset CODEX_HOME or set it to %USERPROFILE%\\.codex: the daemon reads only that Codex config",
            },
            {
                "code": "installer_cli_missing",
                "remedy": "install the native claude CLI, or pass --claude-exe PATH",
                "role": "claude",
            },
        ]
        with mock.patch.dict(
            os.environ, {"PATH": str(empty), "CODEX_HOME": str(elsewhere)}
        ):
            self.assert_rejected(args, expected)

    def test_explicit_dayz_and_item_never_read_the_registry(self) -> None:
        args = self.good_args() + [
            "--dayz",
            str(self.dayz),
            "--item",
            str(self.item),
        ]
        code, payload, _text = self.run_setup(args, registry=self.exploding_registry)
        self.assertEqual(code, 0)
        self.assertEqual(payload, self.expected_success())

    def test_phase_two_gate_failure_reports_and_registers_nothing(self) -> None:
        self.runner.fail_gate = True
        code, payload, text = self.run_setup(self.good_args())
        expected = {
            "error": {"code": "runs_backup_gate_failed"},
            "missing": [],
            "ok": False,
        }
        self.assertEqual(code, 2)
        self.assertEqual(payload, expected)
        self.assertEqual(text, json.dumps(expected, sort_keys=True) + "\n")
        self.assertEqual(self.register_calls, [])

    def test_reject_workshop_item_missing_without_url(self) -> None:
        with mock.patch.object(setup_cmd, "WORKSHOP_ITEM_ID", ""):
            self.assert_rejected(
                self.good_args(),
                [
                    {
                        "code": "workshop_item_missing",
                        "remedy": "subscribe to the DayZ-MCP item in the Steam Workshop, let Steam download it, then run dayz-mcp setup --json again; or pass --item PATH",
                    }
                ],
            )


@unittest.skipUnless(sys.platform == "win32", "requires Windows")
class FindNativeCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()

    def _find(self, path_dirs: list[Path], role: str = "codex") -> Path | None:
        path_value = os.pathsep.join(str(path_dir) for path_dir in path_dirs)
        with mock.patch.dict(os.environ, {"PATH": path_value}):
            return setup_cmd.find_native_cli(role)

    def test_exe_beside_the_cmd_shim(self) -> None:
        shim_dir = self.base / "shim"
        shim_dir.mkdir()
        exe = shim_dir / "codex.exe"
        write_fake_x64_pe(exe)
        shim = shim_dir / "codex.cmd"
        shim.write_text("@echo off\n", encoding="utf-8")
        found = self._find([shim_dir])
        self.assertEqual(found, exe.resolve())

    def test_exe_named_inside_the_shim_text(self) -> None:
        shim_dir = self.base / "shim"
        shim_dir.mkdir()
        other = self.base / "other"
        other.mkdir()
        exe = other / "codex.exe"
        write_fake_x64_pe(exe)
        shim = shim_dir / "codex.cmd"
        shim.write_text(f'@echo off\n"{exe}" %*\n', encoding="utf-8")
        found = self._find([shim_dir])
        self.assertEqual(found, exe.resolve())

    def test_non_pe_candidate_is_skipped_for_the_next_one(self) -> None:
        bad = self.base / "bad" / "claude.exe"
        bad.parent.mkdir()
        bad.write_text("not a pe", encoding="utf-8")
        good_dir = self.base / "good"
        good_dir.mkdir()
        good = good_dir / "claude.exe"
        write_fake_x64_pe(good)
        shim = good_dir / "claude.cmd"
        shim.write_text("@echo off\n", encoding="utf-8")
        found = self._find([bad.parent, good_dir], role="claude")
        self.assertEqual(found, good.resolve())

    def test_exe_on_the_path_wins_over_the_shim(self) -> None:
        first_dir = self.base / "first"
        first_dir.mkdir()
        first = first_dir / "codex.exe"
        write_fake_x64_pe(first)
        shim_dir = self.base / "shim"
        shim_dir.mkdir()
        second = shim_dir / "codex.exe"
        write_fake_x64_pe(second)
        shim = shim_dir / "codex.cmd"
        shim.write_text("@echo off\n", encoding="utf-8")
        found = self._find([first_dir, shim_dir])
        self.assertEqual(found, first.resolve())

    def test_exe_in_the_current_folder_is_not_found(self) -> None:
        current = self.base / "current"
        current.mkdir()
        write_fake_x64_pe(current / "codex.exe")
        empty = self.base / "empty"
        empty.mkdir()
        with mock.patch.dict(os.environ):
            os.environ.pop("NoDefaultCurrentDirectoryInExePath", None)
            self.addCleanup(os.chdir, os.getcwd())
            os.chdir(current)
            self.assertIsNotNone(shutil.which("codex.exe"))
            found = self._find([empty])
        self.assertIsNone(found)

    def test_cmd_in_the_current_folder_is_not_found(self) -> None:
        current = self.base / "current"
        current.mkdir()
        other = self.base / "other"
        other.mkdir()
        exe = other / "codex.exe"
        write_fake_x64_pe(exe)
        shim = current / "codex.cmd"
        shim.write_text(f'@echo off\n"{exe}" %*\n', encoding="utf-8")
        empty = self.base / "empty"
        empty.mkdir()
        with mock.patch.dict(os.environ):
            os.environ.pop("NoDefaultCurrentDirectoryInExePath", None)
            self.addCleanup(os.chdir, os.getcwd())
            os.chdir(current)
            self.assertIsNotNone(shutil.which("codex.cmd"))
            found = self._find([empty])
        self.assertIsNone(found)

    def test_relative_path_entry_is_not_searched(self) -> None:
        current = self.base / "current"
        current.mkdir()
        write_fake_x64_pe(current / "codex.exe")
        empty = self.base / "empty"
        empty.mkdir()
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(current)
        found = self._find([Path("."), empty])
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()

