from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import install_mcp as installer
from install_mcp import (
    InstallerContractError,
    InstallerExecutionError,
    CliRegistrationProvider,
    RegistrationRollbackError,
    RegistrationSpec,
    RegistrationTransactionError,
    build_client_args,
    install_runtime,
    invoke_manifest_cli,
    load_installer_cli_manifest,
    load_installer_not_found_fixtures,
    parse_claude_registration,
    parse_codex_registration,
    parse_args,
    register_transaction,
)


def write_fake_x64_pe(path: Path) -> None:
    payload = bytearray(512)
    payload[0:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", payload, 0x84, 0x8664)
    struct.pack_into("<H", payload, 0x94, 0xF0)
    struct.pack_into("<H", payload, 0x98, 0x20B)
    path.write_bytes(payload)


class InstallerCliManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.claude = self.root / "claude.exe"
        self.codex = self.root / "codex.exe"
        write_fake_x64_pe(self.claude)
        write_fake_x64_pe(self.codex)
        self.manifest_path = self.root / "installer-cli-manifest-v1.json"

    @staticmethod
    def _entry(path: Path) -> dict[str, object]:
        payload = path.read_bytes()
        return {
            "path": str(path.absolute()),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest().upper(),
        }

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": "dayz-mcp-installer-clis-v1",
            "entries": {
                "CLAUDE": self._entry(self.claude),
                "CODEX": self._entry(self.codex),
            },
        }

    def write_manifest(self, payload: dict[str, object] | None = None) -> None:
        self.manifest_path.write_text(
            json.dumps(self.payload() if payload is None else payload),
            encoding="utf-8",
        )

    def test_exact_manifest_loads_two_native_absolute_entries(self) -> None:
        self.write_manifest()

        manifest = load_installer_cli_manifest(self.manifest_path)

        self.assertEqual(set(manifest.entries), {"CLAUDE", "CODEX"})
        self.assertEqual(manifest.entries["CLAUDE"].path, self.claude.resolve())
        self.assertEqual(manifest.entries["CODEX"].path, self.codex.resolve())

    def test_manifest_rejects_missing_extra_or_ambiguous_schema_keys(self) -> None:
        variants = []
        missing = self.payload()
        del missing["entries"]["CODEX"]  # type: ignore[index]
        variants.append(missing)
        extra_role = self.payload()
        extra_role["entries"]["SHELL"] = self._entry(self.codex)  # type: ignore[index]
        variants.append(extra_role)
        extra_key = self.payload()
        extra_key["unexpected"] = True
        variants.append(extra_key)

        for payload in variants:
            with self.subTest(payload=payload):
                self.write_manifest(payload)
                with self.assertRaises(InstallerContractError):
                    load_installer_cli_manifest(self.manifest_path)

    def test_manifest_rejects_path_hash_bytes_and_extension_drift(self) -> None:
        variants = []
        hash_drift = self.payload()
        hash_drift["entries"]["CLAUDE"]["sha256"] = "0" * 64  # type: ignore[index]
        variants.append(hash_drift)
        byte_drift = self.payload()
        byte_drift["entries"]["CLAUDE"]["bytes"] = 1  # type: ignore[index]
        variants.append(byte_drift)
        relative = self.payload()
        relative["entries"]["CLAUDE"]["path"] = "claude.exe"  # type: ignore[index]
        variants.append(relative)
        wrapper = self.payload()
        wrapper["entries"]["CLAUDE"]["path"] = str(self.root / "claude.cmd")  # type: ignore[index]
        variants.append(wrapper)

        for payload in variants:
            with self.subTest(payload=payload):
                self.write_manifest(payload)
                with self.assertRaises(InstallerContractError):
                    load_installer_cli_manifest(self.manifest_path)

    def test_manifest_rejects_non_pe_renamed_as_exe(self) -> None:
        self.claude.write_text("powershell wrapper", encoding="utf-8")
        payload = self.payload()
        self.write_manifest(payload)

        with self.assertRaises(InstallerContractError):
            load_installer_cli_manifest(self.manifest_path)

    def test_entry_reported_as_symlink_is_rejected_even_when_bytes_are_valid(self) -> None:
        self.write_manifest()
        original = Path.is_symlink

        def fake_is_symlink(path: Path) -> bool:
            return path == self.claude or original(path)

        with patch.object(Path, "is_symlink", fake_is_symlink):
            with self.assertRaises(InstallerContractError):
                load_installer_cli_manifest(self.manifest_path)

    def test_hash_is_revalidated_immediately_before_fake_child(self) -> None:
        self.write_manifest()
        entry = load_installer_cli_manifest(self.manifest_path).entries["CLAUDE"]
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_runner(argv: list[str], **kwargs: object) -> object:
            calls.append((argv, kwargs))
            return object()

        self.claude.write_bytes(self.claude.read_bytes() + b"drift")

        with self.assertRaises(InstallerContractError):
            invoke_manifest_cli(entry, ["mcp", "get", "dayz-mcp"], fake_runner)
        self.assertEqual(calls, [])


class InstallerArgumentsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.tools_root = Path(self.temporary.name).resolve()

    def test_unknown_python_override_is_rejected_by_parser(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            parse_args(["--python", r"C:\evil.exe"], tools_root=self.tools_root)

        self.assertEqual(raised.exception.code, 2)

    def test_client_args_match_legacy_policy_without_shell_text(self) -> None:
        options = parse_args(
            [
                "--port",
                "9876",
                "--keyfile",
                str(self.tools_root / "shared.key"),
                "--expected-game-version",
                "1.28.159000",
                "--idle-timeout-seconds",
                "1800",
            ],
            tools_root=self.tools_root,
        )

        claude = build_client_args(options, "claude")
        codex = build_client_args(options, "codex")

        expected_common = [
            "-m",
            "dayz_mcp",
            "--client",
            "--keyfile",
            str((self.tools_root / "shared.key").resolve()),
            "--port",
            "9876",
            "--expected-game-version",
            "1.28.159000",
            "--require-version",
            "--idle-timeout",
            "1800",
        ]
        self.assertEqual(claude, expected_common + ["--client-platform", "claude"])
        self.assertEqual(codex, expected_common + ["--client-platform", "codex"])
        self.assertTrue(all(isinstance(argument, str) for argument in claude + codex))

    def test_allow_legacy_omits_require_version_only(self) -> None:
        options = parse_args(
            ["--allow-legacy", "--idle-timeout-seconds", "2.5"],
            tools_root=self.tools_root,
        )

        arguments = build_client_args(options, "claude")

        self.assertNotIn("--require-version", arguments)
        self.assertEqual(arguments[arguments.index("--idle-timeout") + 1], "2.5")

    def test_invalid_platform_is_rejected(self) -> None:
        options = parse_args([], tools_root=self.tools_root)

        with self.assertRaises(InstallerContractError):
            build_client_args(options, "powershell")


class InstallerRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.tools_root = self.root / "tools"
        self.tools_root.mkdir()
        self.base_python = self.root / "base" / "python.exe"
        self.base_python.parent.mkdir()
        write_fake_x64_pe(self.base_python)
        (self.tools_root / "requirements-mcp.txt").write_text(
            "mcp==1.27.2\nPillow==12.2.0\npsutil==7.2.2\n",
            encoding="utf-8",
        )
        (self.tools_root / "pyproject.toml").write_text(
            "[build-system]\nrequires=[]\n",
            encoding="utf-8",
        )
        vendor = self.tools_root / "vendor" / "psutil"
        vendor.mkdir(parents=True)
        self.wheel = vendor / "psutil-7.2.2-cp37-abi3-win_amd64.whl"
        source_vendor = TOOLS_DIR / "vendor" / "psutil"
        shutil.copyfile(source_vendor / self.wheel.name, self.wheel)
        shutil.copyfile(
            source_vendor / "SHA256SUMS.json",
            vendor / "SHA256SUMS.json",
        )
        self.options = parse_args([], tools_root=self.tools_root)
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(self, argv: list[str], **kwargs: object) -> object:
        self.calls.append((list(argv), dict(kwargs)))
        if argv[1:3] == ["-m", "venv"]:
            venv_python = Path(argv[3]) / "Scripts" / "python.exe"
            venv_python.parent.mkdir(parents=True)
            write_fake_x64_pe(venv_python)
        return type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "", "stderr": ""},
        )()

    def test_fresh_install_uses_exact_python_and_offline_psutil_without_upgrade(self) -> None:
        summary = install_runtime(
            self.options,
            base_python=self.base_python,
            runner=self.runner,
            token_factory=lambda: "fixture-secret-token",
        )

        venv_python = self.tools_root / ".venv-mcp" / "Scripts" / "python.exe"
        expected_commands = [
            [str(self.base_python), "-m", "venv", str(self.tools_root / ".venv-mcp")],
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                str(self.wheel),
            ],
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "-r",
                str(self.tools_root / "requirements-mcp.txt"),
            ],
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "-e",
                str(self.tools_root),
            ],
        ]
        self.assertEqual([call[0] for call in self.calls], expected_commands)
        self.assertTrue(all(call[1]["shell"] is False for call in self.calls))
        self.assertFalse(any("--upgrade" in command for command in expected_commands))
        self.assertNotIn("fixture-secret-token", json.dumps(summary))
        self.assertEqual(summary["venv_python"], str(venv_python))

        configs = sorted((self.tools_root / "_mcp_config").rglob("dayz_mcp.json"))
        self.assertEqual(len(configs), 3)
        for config in configs:
            payload = json.loads(config.read_text(encoding="ascii"))
            self.assertEqual(payload["key"], "fixture-secret-token")
            self.assertEqual(payload["url"], "http://127.0.0.1:8765/")
            self.assertEqual(payload["pollHz"], 5)

    def test_existing_venv_and_key_are_reused_without_secret_in_summary(self) -> None:
        venv_python = self.tools_root / ".venv-mcp" / "Scripts" / "python.exe"
        venv_python.parent.mkdir(parents=True)
        write_fake_x64_pe(venv_python)
        self.options.keyfile.write_text("existing-secret", encoding="ascii")

        summary = install_runtime(
            self.options,
            base_python=self.base_python,
            runner=self.runner,
            token_factory=lambda: self.fail("existing key must be reused"),
        )

        self.assertEqual(len(self.calls), 3)
        self.assertNotIn("existing-secret", json.dumps(summary))

    def test_nonzero_required_command_stops_the_install(self) -> None:
        calls = 0

        def failing_runner(argv: list[str], **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            return type(
                "Completed",
                (),
                {"returncode": 9, "stdout": "secret", "stderr": "secret"},
            )()

        with self.assertRaisesRegex(InstallerExecutionError, "required_command_failed"):
            install_runtime(
                self.options,
                base_python=self.base_python,
                runner=failing_runner,
                token_factory=lambda: "never-used",
            )

        self.assertEqual(calls, 1)
        self.assertFalse(self.options.keyfile.exists())

    def test_wheel_and_manifest_drifting_together_are_still_rejected(self) -> None:
        self.wheel.write_bytes(self.wheel.read_bytes() + b"coordinated-drift")
        manifest_path = self.wheel.parent / "SHA256SUMS.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        wheel_entry = next(
            entry
            for entry in manifest["files"]
            if entry["filename"] == self.wheel.name
        )
        payload = self.wheel.read_bytes()
        wheel_entry["bytes"] = len(payload)
        wheel_entry["sha256"] = hashlib.sha256(payload).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaises(InstallerContractError):
            install_runtime(
                self.options,
                base_python=self.base_python,
                runner=self.runner,
                token_factory=lambda: "never-used",
            )

        self.assertEqual(self.calls, [])


class FakeRegistrationProvider:
    def __init__(
        self,
        states: dict[str, RegistrationSpec | None],
    ) -> None:
        self.states = dict(states)
        self.events: list[tuple[str, str]] = []
        self.failures: dict[tuple[str, str], int] = {}
        self.get_overrides: dict[str, list[RegistrationSpec | None]] = {}

    def fail(self, operation: str, role: str, *, count: int = 1) -> None:
        self.failures[(operation, role)] = count

    def _maybe_fail(self, operation: str, role: str) -> None:
        key = (operation, role)
        remaining = self.failures.get(key, 0)
        if remaining:
            self.failures[key] = remaining - 1
            raise InstallerExecutionError(f"fake_{operation}_failure")

    def get(self, role: str) -> RegistrationSpec | None:
        self.events.append(("get", role))
        self._maybe_fail("get", role)
        overrides = self.get_overrides.get(role)
        if overrides:
            return overrides.pop(0)
        return self.states[role]

    def remove(self, role: str) -> None:
        self.events.append(("remove", role))
        self._maybe_fail("remove", role)
        self.states[role] = None

    def add(self, role: str, spec: RegistrationSpec) -> None:
        self.events.append(("add", role))
        self._maybe_fail("add", role)
        self.states[role] = spec


class InstallerRegistrationTransactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old = {
            "CLAUDE": RegistrationSpec(
                command=Path(r"C:\old\python.exe"),
                arguments=("-m", "old_claude"),
            ),
            "CODEX": RegistrationSpec(
                command=Path(r"C:\old\python.exe"),
                arguments=("-m", "old_codex"),
            ),
        }
        self.desired = {
            "CLAUDE": RegistrationSpec(
                command=Path(r"C:\new\python.exe"),
                arguments=("-m", "dayz_mcp", "--client-platform", "claude"),
            ),
            "CODEX": RegistrationSpec(
                command=Path(r"C:\new\python.exe"),
                arguments=("-m", "dayz_mcp", "--client-platform", "codex"),
            ),
        }

    def test_fresh_absent_install_adds_and_verifies_without_remove(self) -> None:
        provider = FakeRegistrationProvider({"CLAUDE": None, "CODEX": None})

        register_transaction(provider, self.desired)

        self.assertEqual(provider.states, self.desired)
        self.assertNotIn(("remove", "CLAUDE"), provider.events)
        self.assertNotIn(("remove", "CODEX"), provider.events)
        self.assertEqual(provider.events.count(("get", "CLAUDE")), 2)
        self.assertEqual(provider.events.count(("get", "CODEX")), 2)

    def test_existing_entries_are_snapshotted_removed_replaced_and_verified(self) -> None:
        provider = FakeRegistrationProvider(self.old)

        register_transaction(provider, self.desired)

        self.assertEqual(provider.states, self.desired)
        self.assertIn(("remove", "CLAUDE"), provider.events)
        self.assertIn(("remove", "CODEX"), provider.events)

    def test_remove_failure_preserves_and_verifies_both_original_snapshots(self) -> None:
        provider = FakeRegistrationProvider(self.old)
        provider.fail("remove", "CLAUDE")

        with self.assertRaises(RegistrationTransactionError):
            register_transaction(provider, self.desired)

        self.assertEqual(provider.states, self.old)
        self.assertNotIn(("add", "CLAUDE"), provider.events)
        self.assertNotIn(("add", "CODEX"), provider.events)

    def test_add_failure_for_each_role_rolls_back_both_originals(self) -> None:
        for failing_role in ("CLAUDE", "CODEX"):
            with self.subTest(failing_role=failing_role):
                provider = FakeRegistrationProvider(self.old)
                provider.fail("add", failing_role)

                with self.assertRaises(RegistrationTransactionError):
                    register_transaction(provider, self.desired)

                self.assertEqual(provider.states, self.old)

    def test_verify_mismatch_rolls_back_both_originals(self) -> None:
        provider = FakeRegistrationProvider(self.old)
        provider.get_overrides["CLAUDE"] = [
            self.old["CLAUDE"],
            RegistrationSpec(Path(r"C:\drift\python.exe"), ("-m", "drift")),
        ]

        with self.assertRaises(RegistrationTransactionError):
            register_transaction(provider, self.desired)

        self.assertEqual(provider.states, self.old)

    def test_rollback_failure_is_distinct_and_never_reports_success(self) -> None:
        provider = FakeRegistrationProvider(self.old)
        provider.fail("add", "CODEX", count=2)

        with self.assertRaises(RegistrationRollbackError):
            register_transaction(provider, self.desired)

        self.assertNotEqual(provider.states, self.desired)

    def test_timeout_transaction_failure_rolls_back_both_registrations(self) -> None:
        provider = FakeRegistrationProvider(self.old)
        paths = (Path(r"C:\config\.claude.json"), Path(r"C:\config\config.toml"))

        with (
            patch.object(
                installer,
                "apply_host_timeouts",
                side_effect=RuntimeError("private-config-content"),
            ) as apply_timeouts,
            self.assertRaises(RegistrationTransactionError) as raised,
        ):
            register_transaction(provider, self.desired, host_configs=paths)

        apply_timeouts.assert_called_once_with(*paths)
        self.assertEqual(provider.states, self.old)
        self.assertEqual(str(raised.exception), "registration_transaction_failed")
        self.assertNotIn("private-config-content", repr(raised.exception))


class InstallerRegistrationParserTest(unittest.TestCase):
    def test_claude_text_accepts_current_user_scope_label(self) -> None:
        text = """dayz-mcp:
  Scope: User config (available in all your projects)
  Status: connected
  Type: stdio
  Command: C:\\Python\\python.exe
  Args: -m dayz_mcp --client --client-platform claude
  Environment:
  Timeout: 604800000ms
"""

        spec = parse_claude_registration(text)

        self.assertEqual(spec.command, Path(r"C:\Python\python.exe"))
        self.assertEqual(spec.arguments[-2:], ("--client-platform", "claude"))

    def test_claude_text_rejects_wrong_timeout(self) -> None:
        text = """dayz-mcp:
  Scope: User config
  Type: stdio
  Command: C:\\Python\\python.exe
  Args: -m dayz_mcp --client --client-platform claude
  Environment:
  Timeout: 1000ms
"""
        with self.assertRaises(InstallerContractError):
            parse_claude_registration(text)

    def test_claude_text_reconstructs_space_containing_value_by_flag_grammar(self) -> None:
        text = """dayz-mcp:
  Scope: User config
  Status: connected
  Type: stdio
  Command: C:\\DayZ MCP\\.venv-mcp\\Scripts\\python.exe
  Args: -m dayz_mcp --client --keyfile C:\\DayZ MCP\\shared.key --port 8765 --require-version --idle-timeout 1800 --client-platform claude
  Environment:
"""

        spec = parse_claude_registration(text)

        self.assertEqual(spec.command, Path(r"C:\DayZ MCP\.venv-mcp\Scripts\python.exe"))
        self.assertEqual(
            spec.arguments,
            (
                "-m",
                "dayz_mcp",
                "--client",
                "--keyfile",
                r"C:\DayZ MCP\shared.key",
                "--port",
                "8765",
                "--require-version",
                "--idle-timeout",
                "1800",
                "--client-platform",
                "claude",
            ),
        )

    def test_claude_text_rejects_duplicate_unknown_or_nonempty_environment(self) -> None:
        base = """dayz-mcp:
  Scope: User config
  Type: stdio
  Command: C:\\Python\\python.exe
  Args: {args}
  Environment:{environment}
"""
        variants = (
            ("-m dayz_mcp -m attacker", ""),
            ("-m dayz_mcp --unknown value", ""),
            ("-m dayz_mcp", " SECRET=value"),
        )
        for arguments, environment in variants:
            with self.subTest(arguments=arguments, environment=environment):
                with self.assertRaises(InstallerContractError):
                    parse_claude_registration(
                        base.format(args=arguments, environment=environment)
                    )

    def test_codex_json_requires_exact_stdio_transport_shape(self) -> None:
        payload = json.dumps(
            {
                "name": "dayz-mcp",
                "enabled": True,
                "disabled_reason": None,
                "startup_timeout_sec": None,
                "tool_timeout_sec": 604800.0,
                "enabled_tools": None,
                "disabled_tools": None,
                "transport": {
                    "type": "stdio",
                    "command": r"C:\Python\python.exe",
                    "args": ["-m", "dayz_mcp", "--client-platform", "codex"],
                    "cwd": None,
                    "env": {},
                    "env_vars": [],
                }
            }
        )

        spec = parse_codex_registration(payload)

        self.assertEqual(spec.command, Path(r"C:\Python\python.exe"))
        self.assertEqual(spec.arguments[-2:], ("--client-platform", "codex"))
        wrong_timeout = json.loads(payload)
        wrong_timeout["tool_timeout_sec"] = 30.0
        with self.assertRaises(InstallerContractError):
            parse_codex_registration(json.dumps(wrong_timeout))
        with self.assertRaises(InstallerContractError):
            parse_codex_registration(
                json.dumps(
                    {
                        "name": "dayz-mcp",
                        "enabled": True,
                        "disabled_reason": None,
                        "startup_timeout_sec": None,
                        "tool_timeout_sec": None,
                        "enabled_tools": None,
                        "disabled_tools": None,
                        "transport": {
                            "type": "stdio",
                            "command": r"C:\Python\python.exe",
                            "args": [],
                            "cwd": None,
                            "env": {"SECRET": "value"},
                            "env_vars": [],
                        }
                    }
                )
            )

    def test_real_not_found_fixture_is_byte_bound_to_current_cli_manifest(self) -> None:
        reports = TOOLS_DIR.parent / "reports" / "security"

        fixture = load_installer_not_found_fixtures(
            reports / "installer-not-found-fixtures-v1.json",
            reports / "installer-cli-manifest-v1.json",
        )

        self.assertEqual(set(fixture.entries), {"CLAUDE", "CODEX"})
        self.assertNotEqual(fixture.entries["CLAUDE"].returncode, 0)
        self.assertNotEqual(fixture.entries["CODEX"].returncode, 0)


class InstallerCliRegistrationProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        reports = TOOLS_DIR.parent / "reports" / "security"
        self.manifest = load_installer_cli_manifest(
            reports / "installer-cli-manifest-v1.json"
        )
        self.fixture = load_installer_not_found_fixtures(
            reports / "installer-not-found-fixtures-v1.json",
            reports / "installer-cli-manifest-v1.json",
        )

    def test_exact_frozen_nonzero_maps_to_absent_and_drift_is_error(self) -> None:
        calls: list[list[str]] = []

        def runner(argv: list[str], **_kwargs: object) -> object:
            calls.append(list(argv))
            role = "CLAUDE" if Path(argv[0]).name.casefold() == "claude.exe" else "CODEX"
            expected = self.fixture.entries[role]
            return type(
                "Completed",
                (),
                {
                    "returncode": expected.returncode,
                    "stdout": expected.stdout,
                    "stderr": expected.stderr,
                },
            )()

        provider = CliRegistrationProvider(
            self.manifest,
            self.fixture,
            runner=runner,
        )

        self.assertIsNone(provider.get("CLAUDE"))
        self.assertIsNone(provider.get("CODEX"))
        self.assertEqual(calls[0][1:], ["mcp", "get", "dayz-mcp"])
        self.assertEqual(calls[1][1:], ["mcp", "get", "dayz-mcp", "--json"])

        def drift_runner(_argv: list[str], **_kwargs: object) -> object:
            return type(
                "Completed",
                (),
                {"returncode": 1, "stdout": "", "stderr": "different"},
            )()

        drift_provider = CliRegistrationProvider(
            self.manifest,
            self.fixture,
            runner=drift_runner,
        )
        with self.assertRaises(InstallerExecutionError):
            drift_provider.get("CLAUDE")

    def test_add_and_remove_use_absolute_manifest_cli_and_argv_lists(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(argv: list[str], **kwargs: object) -> object:
            calls.append((list(argv), dict(kwargs)))
            return type(
                "Completed",
                (),
                {"returncode": 0, "stdout": "", "stderr": ""},
            )()

        provider = CliRegistrationProvider(
            self.manifest,
            self.fixture,
            runner=runner,
        )
        spec = RegistrationSpec(
            Path(r"C:\venv\python.exe"),
            ("-m", "dayz_mcp", "--client-platform", "claude"),
        )

        provider.remove("CLAUDE")
        provider.add("CLAUDE", spec)
        provider.remove("CODEX")
        provider.add("CODEX", replace(spec, arguments=spec.arguments[:-1] + ("codex",)))

        self.assertEqual(
            calls[0][0][1:],
            ["mcp", "remove", "dayz-mcp", "-s", "user"],
        )
        self.assertEqual(calls[1][0][1:6], ["mcp", "add", "dayz-mcp", "-s", "user"])
        self.assertEqual(calls[2][0][1:], ["mcp", "remove", "dayz-mcp"])
        self.assertEqual(calls[3][0][1:4], ["mcp", "add", "dayz-mcp"])
        self.assertTrue(all(kwargs["shell"] is False for _, kwargs in calls))


class InstallerOrchestrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.options = parse_args(["--register"], tools_root=self.root)
        self.venv_python = self.root / ".venv-mcp" / "Scripts" / "python.exe"

    def test_backup_gate_child_uses_isolated_venv_python_and_exact_json(self) -> None:
        self.venv_python.parent.mkdir(parents=True)
        write_fake_x64_pe(self.venv_python)
        (self.root / "p0s_gate.py").write_text("# fixture\n", encoding="utf-8")
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(argv: list[str], **kwargs: object) -> object:
            calls.append((list(argv), dict(kwargs)))
            return type(
                "Completed",
                (),
                {
                    "returncode": 0,
                    "stdout": '{"source_absent":false,"status":"verified"}\n',
                    "stderr": "",
                },
            )()

        result = installer.run_runs_backup_gate(
            self.venv_python,
            self.root,
            8765,
            runner=runner,
        )

        self.assertEqual(result, {"source_absent": False, "status": "verified"})
        self.assertEqual(
            calls[0][0],
            [
                str(self.venv_python),
                "-I",
                "-B",
                str(self.root / "p0s_gate.py"),
                "backup-runs-v1",
                "--port",
                "8765",
            ],
        )
        self.assertFalse(calls[0][1]["shell"])

    def test_registration_runs_only_after_backup_gate(self) -> None:
        order: list[str] = []
        desired_seen: dict[str, RegistrationSpec] = {}
        provider = object()

        host_configs_seen: list[tuple[Path, Path] | None] = []

        def register(
            _provider: object,
            desired: dict[str, RegistrationSpec],
            *,
            host_configs: tuple[Path, Path] | None = None,
        ) -> None:
            order.append("register")
            desired_seen.update(desired)
            host_configs_seen.append(host_configs)

        with (
            patch.object(
                installer,
                "install_runtime",
                side_effect=lambda *_args, **_kwargs: {
                    "status": "installed",
                    "venv_python": str(self.venv_python),
                    "keyfile": str(self.options.keyfile),
                    "configs": [],
                },
            ),
            patch.object(installer, "load_installer_cli_manifest", return_value=object()),
            patch.object(installer, "load_installer_not_found_fixtures", return_value=object()),
            patch.object(installer, "CliRegistrationProvider", return_value=provider),
            patch.object(
                installer,
                "run_runs_backup_gate",
                side_effect=lambda *_args, **_kwargs: order.append("backup")
                or {"status": "verified", "source_absent": False},
            ),
            patch.object(installer, "register_transaction", side_effect=register),
        ):
            result = installer.run_installer(
                self.options,
                base_python=Path(sys.executable),
            )

        self.assertEqual(order, ["backup", "register"])
        self.assertEqual(
            host_configs_seen,
            [(Path.home() / ".claude.json", Path.home() / ".codex" / "config.toml")],
        )
        self.assertEqual(set(desired_seen), {"CLAUDE", "CODEX"})
        self.assertEqual(desired_seen["CLAUDE"].command, self.venv_python)
        self.assertEqual(desired_seen["CLAUDE"].arguments[-1], "claude")
        self.assertEqual(desired_seen["CODEX"].arguments[-1], "codex")
        self.assertEqual(result["status"], "installed_and_registered")

    def test_backup_failure_prevents_registration_transaction(self) -> None:
        with (
            patch.object(
                installer,
                "install_runtime",
                return_value={"venv_python": str(self.venv_python)},
            ),
            patch.object(installer, "load_installer_cli_manifest", return_value=object()),
            patch.object(installer, "load_installer_not_found_fixtures", return_value=object()),
            patch.object(installer, "CliRegistrationProvider", return_value=object()),
            patch.object(
                installer,
                "run_runs_backup_gate",
                side_effect=InstallerExecutionError("backup_failed"),
            ),
            patch.object(installer, "register_transaction") as register,
            self.assertRaises(InstallerExecutionError),
        ):
            installer.run_installer(self.options, base_python=Path(sys.executable))

        register.assert_not_called()


if __name__ == "__main__":
    unittest.main()
