from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
    installer_cli_manifest_path,
    installer_not_found_fixtures_path,
    invoke_manifest_cli,
    load_installer_cli_manifest,
    load_installer_not_found_fixtures,
    parse_claude_registration,
    parse_codex_registration,
    parse_args,
    pin_installer_clis,
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


def cli_entry_payload(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest().upper(),
    }


def write_synthetic_cli_manifest(path: Path, claude: Path, codex: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "dayz-mcp-installer-clis-v1",
                "entries": {
                    "CLAUDE": cli_entry_payload(claude),
                    "CODEX": cli_entry_payload(codex),
                },
            }
        ),
        encoding="utf-8",
    )


def write_synthetic_not_found_fixture(fixture_path: Path, manifest_path: Path) -> None:
    manifest_bytes = manifest_path.read_bytes()
    manifest = load_installer_cli_manifest(manifest_path)
    payload = {
        "schema_version": 1,
        "kind": "dayz-mcp-installer-not-found-fixtures-v1",
        "probe_name": "p0s-absent-fixture-do-not-create",
        "cli_manifest": {
            "bytes": len(manifest_bytes),
            "sha256": hashlib.sha256(manifest_bytes).hexdigest().upper(),
        },
        "entries": {
            "CLAUDE": {
                "cli_sha256": manifest.entries["CLAUDE"].sha256,
                "returncode": 7,
                "stdout": "CLAUDE-absent\n",
                "stderr": "",
            },
            "CODEX": {
                "cli_sha256": manifest.entries["CODEX"].sha256,
                "returncode": 8,
                "stdout": "CODEX-absent\n",
                "stderr": "",
            },
        },
    }
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")


def absent_probe_runner(argv: list[str], **_kwargs: object) -> object:
    role = Path(argv[0]).stem.upper()
    return type(
        "Completed",
        (),
        {
            "returncode": 7 if role == "CLAUDE" else 8,
            "stdout": f"{role}-absent\n",
            "stderr": "",
        },
    )()


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
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        claude = root / "claude.exe"
        codex = root / "codex.exe"
        write_fake_x64_pe(claude)
        write_fake_x64_pe(codex)
        manifest_path = root / "installer-cli-manifest-v1.json"
        fixture_path = root / "installer-not-found-fixtures-v1.json"
        write_synthetic_cli_manifest(manifest_path, claude, codex)
        write_synthetic_not_found_fixture(fixture_path, manifest_path)

        fixture = load_installer_not_found_fixtures(fixture_path, manifest_path)

        self.assertEqual(set(fixture.entries), {"CLAUDE", "CODEX"})
        self.assertNotEqual(fixture.entries["CLAUDE"].returncode, 0)
        self.assertNotEqual(fixture.entries["CODEX"].returncode, 0)
        drifted = json.loads(fixture_path.read_text(encoding="utf-8"))
        drifted["cli_manifest"]["sha256"] = "0" * 64
        fixture_path.write_text(json.dumps(drifted), encoding="utf-8")
        with self.assertRaises(InstallerContractError) as raised:
            load_installer_not_found_fixtures(fixture_path, manifest_path)
        self.assertEqual(raised.exception.code, "installer_not_found_manifest_binding_drift")


class InstallerCliRegistrationProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.claude = root / "claude.exe"
        self.codex = root / "codex.exe"
        write_fake_x64_pe(self.claude)
        write_fake_x64_pe(self.codex)
        self.manifest_path = root / "installer-cli-manifest-v1.json"
        self.fixture_path = root / "installer-not-found-fixtures-v1.json"
        write_synthetic_cli_manifest(self.manifest_path, self.claude, self.codex)
        write_synthetic_not_found_fixture(self.fixture_path, self.manifest_path)
        self.manifest = load_installer_cli_manifest(self.manifest_path)
        self.fixture = load_installer_not_found_fixtures(
            self.fixture_path, self.manifest_path
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


class InstallerCliPinLocalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.security = self.root / "security"
        self.security.mkdir()
        self.claude = self.root / "claude.exe"
        self.codex = self.root / "codex.exe"
        write_fake_x64_pe(self.claude)
        write_fake_x64_pe(self.codex)
        self.env = patch.dict(
            os.environ, {"DAYZ_MCP_SECURITY_DIR": str(self.security)}
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_pin_clis_happy_path_writes_two_roles(self) -> None:
        entries = pin_installer_clis(
            claude_exe=self.claude,
            codex_exe=self.codex,
            runner=absent_probe_runner,
        )

        self.assertEqual([entry.role for entry in entries], ["CLAUDE", "CODEX"])
        manifest = load_installer_cli_manifest(installer_cli_manifest_path())
        self.assertEqual(manifest.entries["CLAUDE"].path, self.claude)
        self.assertEqual(manifest.entries["CODEX"].path, self.codex)
        fixture = load_installer_not_found_fixtures(
            installer_not_found_fixtures_path(),
            installer_cli_manifest_path(),
        )
        self.assertEqual(fixture.entries["CLAUDE"].returncode, 7)
        self.assertEqual(fixture.entries["CODEX"].returncode, 8)

    def test_pin_clis_main_prints_one_line_per_role(self) -> None:
        buffer = io.StringIO()

        def fake_invoke(entry: object, arguments: object, _runner: object) -> object:
            path = getattr(entry, "path", Path("unknown.exe"))
            return absent_probe_runner([str(path), *list(arguments)])

        with (
            patch.object(installer, "invoke_manifest_cli", side_effect=fake_invoke),
            redirect_stdout(buffer),
        ):
            code = installer.main(
                [
                    "--pin-clis",
                    "--claude-exe",
                    str(self.claude),
                    "--codex-exe",
                    str(self.codex),
                ]
            )

        self.assertEqual(code, 0)
        lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("CLAUDE "))
        self.assertTrue(lines[1].startswith("CODEX "))
        self.assertIn(str(self.claude), lines[0])
        self.assertIn(str(self.codex), lines[1])

    def test_which_shim_is_typed_error_asking_for_explicit_path(self) -> None:
        shim = self.root / "claude.cmd"
        shim.write_text("@echo off\n", encoding="utf-8")

        def fake_which(name: str) -> str | None:
            if name == "claude":
                return str(shim)
            if name == "codex":
                return str(self.codex)
            return None

        with patch.object(installer.shutil, "which", side_effect=fake_which):
            with self.assertRaises(InstallerContractError) as raised:
                pin_installer_clis(runner=absent_probe_runner)

        self.assertEqual(raised.exception.code, "installer_cli_not_native_exe")
        self.assertIn("--claude-exe", str(raised.exception))
        self.assertFalse(installer_cli_manifest_path().exists())

    def test_missing_manifest_is_typed_with_pin_recipe(self) -> None:
        with self.assertRaises(InstallerContractError) as raised:
            load_installer_cli_manifest(installer_cli_manifest_path())

        self.assertEqual(raised.exception.code, "installer_cli_manifest_missing")
        self.assertIn("run: python install_mcp.py --pin-clis", str(raised.exception))

        options = parse_args(["--register"], tools_root=self.root)
        with (
            patch.object(
                installer,
                "install_runtime",
                return_value={"venv_python": str(self.root / "python.exe")},
            ),
            self.assertRaises(InstallerContractError) as register_raised,
        ):
            installer.run_installer(options, base_python=Path(sys.executable))
        self.assertEqual(
            register_raised.exception.code, "installer_cli_manifest_missing"
        )
        self.assertIn(
            "run: python install_mcp.py --pin-clis",
            str(register_raised.exception),
        )

    def test_byte_and_hash_drift_after_pin_name_pin_clis(self) -> None:
        pin_installer_clis(
            claude_exe=self.claude,
            codex_exe=self.codex,
            runner=absent_probe_runner,
        )
        original = self.claude.read_bytes()
        mutated = bytearray(original)
        mutated[-1] = (mutated[-1] + 1) % 256
        self.claude.write_bytes(bytes(mutated))
        with self.assertRaises(InstallerContractError) as hash_raised:
            load_installer_cli_manifest(installer_cli_manifest_path())
        self.assertEqual(hash_raised.exception.code, "installer_cli_hash_drift")
        self.assertIn("--pin-clis", str(hash_raised.exception))

        write_fake_x64_pe(self.claude)
        pin_installer_clis(
            claude_exe=self.claude,
            codex_exe=self.codex,
            runner=absent_probe_runner,
        )
        self.claude.write_bytes(self.claude.read_bytes() + b"\x00")
        with self.assertRaises(InstallerContractError) as byte_raised:
            load_installer_cli_manifest(installer_cli_manifest_path())
        self.assertEqual(byte_raised.exception.code, "installer_cli_byte_drift")
        self.assertIn("--pin-clis", str(byte_raised.exception))

    def test_security_dir_env_is_used_by_installer(self) -> None:
        other = self.root / "other-security"
        other.mkdir()
        with patch.dict(os.environ, {"DAYZ_MCP_SECURITY_DIR": str(other)}):
            self.assertEqual(
                installer_cli_manifest_path(),
                other / "installer-cli-manifest-v1.json",
            )
            pin_installer_clis(
                claude_exe=self.claude,
                codex_exe=self.codex,
                runner=absent_probe_runner,
            )
            self.assertTrue((other / "installer-cli-manifest-v1.json").is_file())
            self.assertTrue(
                (other / "installer-not-found-fixtures-v1.json").is_file()
            )
        self.assertFalse(
            (self.security / "installer-cli-manifest-v1.json").exists()
        )

    def test_pin_clis_and_register_are_exclusive(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            parse_args(
                ["--pin-clis", "--register"],
                tools_root=self.root,
            )
        self.assertEqual(raised.exception.code, 2)

    def test_register_main_error_json_includes_pin_recipe(self) -> None:
        stderr = io.StringIO()
        with (
            patch.object(
                installer,
                "install_runtime",
                return_value={"venv_python": str(self.root / "python.exe")},
            ),
            redirect_stderr(stderr),
        ):
            code = installer.main(["--register"])

        self.assertEqual(code, 2)
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["error"], "installer_cli_manifest_missing")
        self.assertIn(
            "run: python install_mcp.py --pin-clis", payload["detail"]
        )

    def test_failed_repin_does_not_leave_orphan_fixture(self) -> None:
        pin_installer_clis(
            claude_exe=self.claude,
            codex_exe=self.codex,
            runner=absent_probe_runner,
        )
        fixture = installer_not_found_fixtures_path()
        self.assertTrue(fixture.is_file())

        def fail_runner(_argv: list[str], **_kwargs: object) -> object:
            return type(
                "Completed",
                (),
                {"returncode": 0, "stdout": "", "stderr": ""},
            )()

        with self.assertRaises(InstallerContractError) as raised:
            pin_installer_clis(
                claude_exe=self.claude,
                codex_exe=self.codex,
                runner=fail_runner,
            )

        self.assertFalse(fixture.exists())
        self.assertIn("--pin-clis", str(raised.exception))

    def test_exe_flags_without_pin_clis_are_argument_errors(self) -> None:
        for argv in (
            ["--claude-exe", str(self.claude)],
            ["--codex-exe", str(self.codex)],
            ["--register", "--claude-exe", str(self.claude)],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as raised:
                    parse_args(argv, tools_root=self.root)
                self.assertEqual(raised.exception.code, 2)

    def test_pin_probe_timeout_is_typed_contract_error(self) -> None:
        def timeout_runner(_argv: list[str], **_kwargs: object) -> object:
            raise subprocess.TimeoutExpired(cmd="claude.exe", timeout=30.0)

        with self.assertRaises(InstallerContractError) as raised:
            pin_installer_clis(
                claude_exe=self.claude,
                codex_exe=self.codex,
                runner=timeout_runner,
            )
        self.assertEqual(raised.exception.code, "installer_cli_probe_timeout")
        self.assertIn("--pin-clis", str(raised.exception))


class PublicBoundaryPinLocalTest(unittest.TestCase):
    def test_publish_boundary_excludes_installer_cli_pins(self) -> None:
        publish = TOOLS_DIR / "publish"
        # The publish tooling stays in the private tree; the exported clone
        # has no tools/publish, so the boundary check only runs at the source.
        if not (publish / "included.json").is_file():
            self.skipTest("publish tooling not shipped in this checkout")
        included = json.loads(
            (publish / "included.json").read_text(encoding="utf-8")
        )
        files = included["files"]
        self.assertNotIn(
            "reports/security/installer-cli-manifest-v1.json", files
        )
        self.assertNotIn(
            "reports/security/installer-not-found-fixtures-v1.json", files
        )
        source = (publish / "boundary.py").read_text(encoding="utf-8")
        runtime_generated = source.split("RUNTIME_GENERATED", 1)[1]
        self.assertIn("installer-cli-manifest-v1.json", runtime_generated)
        self.assertIn("installer-not-found-fixtures-v1.json", runtime_generated)
        self.assertNotIn(
            '"reports/security/installer-cli-manifest-v1.json"', source
        )
        self.assertNotIn(
            '"reports/security/installer-not-found-fixtures-v1.json"', source
        )
        self.assertIn("--pin-clis", source)
        installer_source = (TOOLS_DIR / "install_mcp.py").read_text(encoding="utf-8")
        gate_source = (TOOLS_DIR / "p0s_gate.py").read_text(encoding="utf-8")
        self.assertNotIn("reports/security", installer_source)
        self.assertNotIn("reports\\security", installer_source)
        self.assertNotIn("reports/security", gate_source)
        self.assertNotIn("reports\\security", gate_source)


class PublicToolCountDocsTest(unittest.TestCase):
    def test_readme_tool_count_matches_instantiated_app(self) -> None:
        from dayz_mcp.server import ServerConfig, build_app

        app, _runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _message: None)
        )
        without = {tool.name for tool in app._tool_manager.list_tools()}
        without.discard("ui_dialog")
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        allow_path = Path(tmp.name) / "allowlist.json"
        allow_path.write_text("[]", encoding="utf-8")
        app_with, _runtime_with = build_app(
            ServerConfig(
                key="k",
                port=0,
                log_sink=lambda _message: None,
                enable_exec_enforce=True,
                exec_allowlist=str(allow_path),
            )
        )
        with_exec = {tool.name for tool in app_with._tool_manager.list_tools()}
        with_exec.discard("ui_dialog")

        self.assertNotIn("exec_enforce", without)
        self.assertEqual(with_exec, without | {"exec_enforce"})
        readme = (TOOLS_DIR.parent / "README.md").read_text(encoding="utf-8")
        formula = (
            f"{len(without)} tools (+ `exec_enforce` when an allowlist is configured)"
        )
        self.assertIn(formula, readme)
        self.assertNotIn("39 tools", readme)
        self.assertIn("--pin-clis", readme)
        self.assertIn("python install_mcp.py --register", readme)
        self.assertIn("does not read that pin", readme)
        self.assertNotIn("ui_dialog", readme)
        for name in sorted(without):
            self.assertIn(f"`{name}`", readme)
        tools_readme = (TOOLS_DIR / "README-mcp.md").read_text(encoding="utf-8")
        self.assertIn("python install_mcp.py --pin-clis", tools_readme)
        self.assertIn("python install_mcp.py --register", tools_readme)
        self.assertNotIn("when run with `-Register`", tools_readme)


if __name__ == "__main__":
    unittest.main()
