from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from ctypes import wintypes
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import install_mcp as installer
from tests.pe_helpers import write_fake_x64_pe
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
from tests._tiers import slow_test


def _short_path_name(path: Path) -> str | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetShortPathNameW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    kernel32.GetShortPathNameW.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(kernel32.GetShortPathNameW(str(path), buffer, len(buffer)))
    if length <= 0 or length >= len(buffer):
        return None
    return buffer.value


def _short_path_or_skip(test: unittest.TestCase, path: Path) -> str:
    short = _short_path_name(path)
    long_form = str(path.resolve())
    if short is None or os.path.normcase(short) == os.path.normcase(long_form):
        test.skipTest(
            "volume did not return an 8.3 short name distinct from the long path"
        )
    return short


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
        return InstallerCliManifestTest._entry_spelled(path, str(path.absolute()))

    @staticmethod
    def _entry_spelled(path: Path, spelling: str) -> dict[str, object]:
        payload = path.read_bytes()
        return {
            "path": spelling,
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
        self.assertEqual(manifest.entries["CLAUDE"].path, self.claude.absolute())
        self.assertEqual(manifest.entries["CODEX"].path, self.codex.absolute())

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

    def test_short_path_of_the_cli_is_canonical_and_another_file_is_not(self) -> None:
        nested = self.root / "ALongDirectoryNameForShortPaths"
        nested.mkdir()
        cli = nested / "claude.exe"
        write_fake_x64_pe(cli)
        short = _short_path_or_skip(self, cli)
        payload = self.payload()
        payload["entries"]["CLAUDE"] = self._entry_spelled(cli, short)
        self.write_manifest(payload)
        manifest = load_installer_cli_manifest(self.manifest_path)
        self.assertEqual(manifest.entries["CLAUDE"].path, Path(short))

        real = self.root / "RealCliDirectoryForJunction"
        real.mkdir()
        target = real / "claude.exe"
        write_fake_x64_pe(target)
        junction = self.root / "JunctionDirectoryName"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(real)],
            capture_output=True,
        )
        if created.returncode != 0:
            self.skipTest("junction unavailable")
        self.addCleanup(lambda: os.rmdir(junction) if junction.exists() else None)
        through = junction / "claude.exe"
        short_other = _short_path_or_skip(self, through)
        if Path(short_other).name.casefold() != "claude.exe":
            short_dir = _short_path_name(junction)
            if short_dir is None:
                self.skipTest(
                    "volume did not return an 8.3 short name distinct from the long path"
                )
            short_other = str(Path(short_dir) / "claude.exe")
        refused = self.payload()
        refused["entries"]["CLAUDE"] = self._entry_spelled(through, short_other)
        self.write_manifest(refused)
        with self.assertRaises(InstallerContractError) as caught:
            load_installer_cli_manifest(self.manifest_path)
        self.assertEqual(caught.exception.code, "installer_cli_path_not_canonical")

    def test_trailing_dot_cli_component_stays_noncanonical(self) -> None:
        nested = self.root / "foo"
        nested.mkdir()
        cli = nested / "claude.exe"
        write_fake_x64_pe(cli)
        resolved = str(cli.resolve())
        parent, name = resolved.rsplit("\\", 1)
        head, leaf = parent.rsplit("\\", 1)
        spelling = head + "\\" + leaf + ".\\" + name
        payload = self.payload()
        payload["entries"]["CLAUDE"] = self._entry_spelled(cli, spelling)
        self.write_manifest(payload)
        with self.assertRaises(InstallerContractError) as caught:
            load_installer_cli_manifest(self.manifest_path)
        self.assertEqual(caught.exception.code, "installer_cli_path_not_canonical")

    def test_dotdot_cli_path_stays_noncanonical(self) -> None:
        extra = self.root / "extra"
        extra.mkdir()
        dotted = self.root / "extra" / ".." / "claude.exe"
        payload = self.payload()
        payload["entries"]["CLAUDE"] = self._entry_spelled(self.claude, str(dotted))
        self.write_manifest(payload)
        with self.assertRaises(InstallerContractError) as caught:
            load_installer_cli_manifest(self.manifest_path)
        self.assertEqual(caught.exception.code, "installer_cli_path_not_canonical")

    def test_matching_long_cli_path_is_accepted_when_expansion_fails(self) -> None:
        pinned = importlib.import_module("dayz_mcp.pinned_keyfile")
        self.write_manifest()
        with patch.object(
            pinned._kernel32, "GetLongPathNameW", return_value=0
        ) as expanded:
            manifest = load_installer_cli_manifest(self.manifest_path)
        self.assertEqual(expanded.call_count, 0)
        self.assertEqual(manifest.entries["CLAUDE"].path, self.claude.absolute())

    def test_short_cli_path_is_refused_when_expansion_fails(self) -> None:
        pinned = importlib.import_module("dayz_mcp.pinned_keyfile")
        nested = self.root / "ALongDirectoryNameForShortPaths"
        nested.mkdir()
        cli = nested / "claude.exe"
        write_fake_x64_pe(cli)
        short = _short_path_or_skip(self, cli)
        payload = self.payload()
        payload["entries"]["CLAUDE"] = self._entry_spelled(cli, short)
        self.write_manifest(payload)
        with patch.object(
            pinned._kernel32, "GetLongPathNameW", return_value=0
        ) as expanded, self.assertRaises(InstallerContractError) as caught:
            load_installer_cli_manifest(self.manifest_path)
        self.assertEqual(caught.exception.code, "installer_cli_path_not_canonical")
        self.assertGreater(expanded.call_count, 0)

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
            "--supervised",
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

    def test_claude_opt_out_of_progressive_disclosure_reaches_claude_only(self) -> None:
        default = parse_args([], tools_root=self.tools_root)
        opted = parse_args(
            ["--claude-no-progressive-disclosure"], tools_root=self.tools_root
        )

        self.assertNotIn(
            "--no-progressive-disclosure", build_client_args(default, "claude")
        )
        self.assertEqual(
            build_client_args(opted, "claude"),
            build_client_args(default, "claude") + ["--no-progressive-disclosure"],
        )
        self.assertEqual(
            build_client_args(opted, "codex"), build_client_args(default, "codex")
        )

    def test_supervisor_is_registered_by_default_and_can_be_opted_out(self) -> None:
        default = parse_args([], tools_root=self.tools_root)
        opted_out = parse_args(["--no-supervised"], tools_root=self.tools_root)

        for platform in ("claude", "codex"):
            with self.subTest(platform=platform):
                arguments = build_client_args(default, platform)
                self.assertEqual(
                    arguments[:4], ["-m", "dayz_mcp", "--client", "--supervised"]
                )
                self.assertEqual(
                    build_client_args(opted_out, platform),
                    [argument for argument in arguments if argument != "--supervised"],
                )

    def test_option_removal_is_refused_unless_asked(self) -> None:
        self.assertFalse(parse_args([], tools_root=self.tools_root).allow_option_removal)
        self.assertTrue(
            parse_args(
                ["--allow-option-removal"], tools_root=self.tools_root
            ).allow_option_removal
        )

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

    @staticmethod
    def _client(command: str, *extra: str) -> dict[str, RegistrationSpec]:
        return {
            role: RegistrationSpec(
                command=Path(command),
                arguments=("-m", "dayz_mcp", "--client", *extra, "--client-platform", platform),
            )
            for role, platform in (("CLAUDE", "claude"), ("CODEX", "codex"))
        }

    def test_refuses_to_drop_an_option_the_current_registration_has(self) -> None:
        # The box's registrations carried --supervised that no installer emitted
        # (fb-20260927-210146-0f68); Claude's may also carry the disclosure opt-out.
        live = self._client(r"C:\old\python.exe", "--supervised")
        live["CLAUDE"] = RegistrationSpec(
            command=live["CLAUDE"].command,
            arguments=live["CLAUDE"].arguments + ("--no-progressive-disclosure",),
        )
        provider = FakeRegistrationProvider(live)

        with self.assertRaises(InstallerContractError) as raised:
            register_transaction(provider, self._client(r"C:\new\python.exe"))

        self.assertEqual(raised.exception.code, "registration_would_drop_options")
        self.assertIn(
            "CLAUDE:--no-progressive-disclosure,--supervised;CODEX:--supervised",
            str(raised.exception),
        )
        self.assertEqual(provider.states, live)
        self.assertEqual(provider.events, [("get", "CLAUDE"), ("get", "CODEX")])

    def test_an_unknown_codex_option_counts_as_dropped_too(self) -> None:
        live = self._client(r"C:\old\python.exe")
        live["CODEX"] = RegistrationSpec(
            command=live["CODEX"].command,
            arguments=live["CODEX"].arguments + ("--exec-audit-path", r"C:\audit"),
        )
        provider = FakeRegistrationProvider(live)

        with self.assertRaises(InstallerContractError) as raised:
            register_transaction(provider, self._client(r"C:\new\python.exe"))

        self.assertIn("CODEX:--exec-audit-path ", str(raised.exception))
        self.assertNotIn("CLAUDE:", str(raised.exception))
        self.assertEqual(provider.states, live)

    def test_exec_audit_path_on_claude_is_dropped_not_a_probe_failure(self) -> None:
        # Known value flag (server_cli.py:59). The Claude parser must accept it,
        # and the guard still refuses to strip it because the new argv does not
        # emit it. Remove is not reached: the provider only saw the two gets.
        text = """dayz-mcp:
  Scope: User config
  Type: stdio
  Command: C:\\Python\\python.exe
  Args: -m dayz_mcp --client --exec-audit-path C:\\audit --client-platform claude
  Environment:
"""
        live = self._client(r"C:\old\python.exe")
        live["CLAUDE"] = parse_claude_registration(text)
        provider = FakeRegistrationProvider(live)

        with self.assertRaises(InstallerContractError) as raised:
            register_transaction(provider, self._client(r"C:\new\python.exe"))

        self.assertEqual(raised.exception.code, "registration_would_drop_options")
        self.assertIn("CLAUDE:--exec-audit-path", str(raised.exception))
        self.assertEqual(provider.events, [("get", "CLAUDE"), ("get", "CODEX")])
        self.assertEqual(provider.states, live)

    def test_allow_option_removal_lets_the_registration_drop_options(self) -> None:
        provider = FakeRegistrationProvider(
            self._client(r"C:\old\python.exe", "--supervised")
        )
        desired = self._client(r"C:\new\python.exe")

        register_transaction(provider, desired, allow_option_removal=True)

        self.assertEqual(provider.states, desired)

    def test_adding_the_supervisor_to_an_older_registration_is_not_a_drop(self) -> None:
        provider = FakeRegistrationProvider(self._client(r"C:\old\python.exe"))
        desired = self._client(r"C:\new\python.exe", "--supervised")

        register_transaction(provider, desired)

        self.assertEqual(provider.states, desired)


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

    def test_claude_text_reads_back_the_progressive_disclosure_opt_out(self) -> None:
        text = """dayz-mcp:
  Scope: User config
  Type: stdio
  Command: C:\\Python\\python.exe
  Args: -m dayz_mcp --client --client-platform claude --no-progressive-disclosure
  Environment:
"""

        spec = parse_claude_registration(text)

        self.assertEqual(spec.arguments[-1], "--no-progressive-disclosure")

    def test_claude_text_reads_back_a_supervised_registration(self) -> None:
        text = """dayz-mcp:
  Scope: User config
  Type: stdio
  Command: C:\\Python\\python.exe
  Args: -m dayz_mcp --client --supervised --keyfile C:\\k\\.dayz_mcp.key --port 8765 --require-version --idle-timeout 3600 --client-platform claude
  Environment:
"""

        spec = parse_claude_registration(text)

        self.assertEqual(spec.arguments[:4], ("-m", "dayz_mcp", "--client", "--supervised"))
        self.assertEqual(spec.arguments[-2:], ("--client-platform", "claude"))

    def test_claude_text_accepts_exec_audit_path_as_a_value(self) -> None:
        text = """dayz-mcp:
  Scope: User config
  Type: stdio
  Command: C:\\Python\\python.exe
  Args: -m dayz_mcp --client --exec-audit-path C:\\audit\\exec log --client-platform claude
  Environment:
"""

        spec = parse_claude_registration(text)

        self.assertEqual(
            spec.arguments,
            (
                "-m",
                "dayz_mcp",
                "--client",
                "--exec-audit-path",
                r"C:\audit\exec log",
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

        removal_seen: list[bool] = []

        def register(
            _provider: object,
            desired: dict[str, RegistrationSpec],
            *,
            host_configs: tuple[Path, Path] | None = None,
            allow_option_removal: bool = False,
        ) -> None:
            order.append("register")
            desired_seen.update(desired)
            host_configs_seen.append(host_configs)
            removal_seen.append(allow_option_removal)

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
        self.assertIn("--supervised", desired_seen["CLAUDE"].arguments)
        self.assertIn("--supervised", desired_seen["CODEX"].arguments)
        self.assertEqual(removal_seen, [False])
        self.assertEqual(result["status"], "installed_and_registered")

    def test_allow_option_removal_reaches_the_registration_transaction(self) -> None:
        options = parse_args(["--register", "--allow-option-removal"], tools_root=self.root)
        seen: list[object] = []
        with (
            patch.object(
                installer,
                "install_runtime",
                return_value={"status": "installed", "venv_python": str(self.venv_python)},
            ),
            patch.object(installer, "load_installer_cli_manifest", return_value=object()),
            patch.object(installer, "load_installer_not_found_fixtures", return_value=object()),
            patch.object(installer, "CliRegistrationProvider", return_value=object()),
            patch.object(
                installer,
                "run_runs_backup_gate",
                return_value={"status": "verified", "source_absent": False},
            ),
            patch.object(
                installer,
                "register_transaction",
                side_effect=lambda *_args, **kwargs: seen.append(
                    kwargs.get("allow_option_removal")
                ),
            ),
        ):
            installer.run_installer(options, base_python=Path(sys.executable))

        self.assertEqual(seen, [True])

    def test_main_reports_a_refused_drop_with_its_code_and_the_options(self) -> None:
        provider = FakeRegistrationProvider(
            {
                role: RegistrationSpec(
                    command=self.venv_python,
                    arguments=(
                        "-m", "dayz_mcp", "--client", "--supervised",
                        "--client-platform", platform,
                    ),
                )
                for role, platform in (("CLAUDE", "claude"), ("CODEX", "codex"))
            }
        )
        stderr = io.StringIO()
        with (
            patch.object(
                installer,
                "install_runtime",
                return_value={"venv_python": str(self.venv_python)},
            ),
            patch.object(installer, "load_installer_cli_manifest", return_value=object()),
            patch.object(installer, "load_installer_not_found_fixtures", return_value=object()),
            patch.object(installer, "CliRegistrationProvider", return_value=provider),
            patch.object(
                installer,
                "run_runs_backup_gate",
                return_value={"status": "verified", "source_absent": False},
            ),
            redirect_stderr(stderr),
        ):
            code = installer.main(["--register", "--no-supervised", "--skip-knowledge-pack"])

        self.assertEqual(code, 2)
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["error"], "registration_would_drop_options")
        self.assertIn("CLAUDE:--supervised;CODEX:--supervised", payload["detail"])
        self.assertIn("--allow-option-removal", payload["detail"])
        self.assertEqual(provider.events, [("get", "CLAUDE"), ("get", "CODEX")])

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
    @slow_test
    def test_readme_tool_count_matches_instantiated_app(self) -> None:
        from dayz_mcp.server import ServerConfig, build_app

        app, _runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _message: None)
        )
        without = {tool.name for tool in app._tool_manager.list_tools()}
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

        self.assertNotIn("exec_enforce", without)
        self.assertEqual(with_exec, without | {"exec_enforce"})
        readme = (TOOLS_DIR.parent / "README.md").read_text(encoding="utf-8")
        formula = (
            f"{len(without)} tools (+ `exec_enforce` when an allowlist is configured)"
        )
        self.assertIn(formula, readme)
        # The formula alone is not enough: the opening paragraph states the count
        # too. On 2026-08-21 that let the README ship 52 in the headline and 53 in
        # the section at once, green. Pin EVERY count to the instantiated app
        # instead of pinning one spelling of it -- which is why this pattern is
        # deliberately looser than `formula`: it matches any "N tools" in the
        # README, with or without the exec_enforce caveat trailing it. Requiring
        # the caveat in every count would dictate WHERE the caveat lives, and the
        # headline is a claim about what the server is, not the place to qualify a
        # tool that does not execute on a headless diag server at all.
        counts = re.findall(
            r"(\d+)\s+(?:typed\s+)?tools\b",
            readme,
        )
        self.assertGreaterEqual(len(counts), 2, "README lost one of its tool counts")
        self.assertEqual(
            set(counts), {str(len(without))}, f"README disagrees with itself: {counts}"
        )
        self.assertNotIn("39 tools", readme)
        self.assertIn("--pin-clis", readme)
        self.assertIn("python install_mcp.py --register", readme)
        self.assertIn("does not read that pin", readme)
        for name in sorted(without):
            self.assertIn(f"`{name}`", readme)
        tools_readme = (TOOLS_DIR / "README-mcp.md").read_text(encoding="utf-8")
        self.assertIn("python install_mcp.py --pin-clis", tools_readme)
        self.assertIn("python install_mcp.py --register", tools_readme)
        self.assertNotIn("when run with `-Register`", tools_readme)

    def test_architecture_tool_count_matches_instantiated_app(self) -> None:
        from dayz_mcp.server import ServerConfig, build_app

        app, _runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _message: None)
        )
        without = {tool.name for tool in app._tool_manager.list_tools()}
        architecture = (TOOLS_DIR.parent / "dayz-mcp-architecture.md").read_text(
            encoding="utf-8"
        )
        formula = (
            f"{len(without)} tools (+ `exec_enforce` when an allowlist is configured)"
        )
        self.assertIn(formula, architecture)
        # Same trap as the README: the intro states the count in prose, where the
        # formula cannot match. Pin it to the app as well.
        self.assertIn(f"expone hoy {len(without)} tools", architecture)
        self.assertNotIn("Tool surface (11 tools, 6 dominios)", architecture)
        for name in (
            "ui_tree",
            "ui_set_text",
            "ui_click",
            "ui_dialog",
            "ui_reload_layout",
        ):
            self.assertIn(f"`{name}`", architecture)


# Evaluates only the two pure argument validators and the argv block of
# install-mcp.ps1 (review of #113, F1): no installer, CLI or registry action runs.
_PS_ARGV_PROBE = r'''
param([string]$SourcePath)
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($SourcePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'PowerShell source did not parse' }
foreach ($name in @('Test-CanonicalTextArguments', 'Test-CanonicalArrayArguments')) {
    $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if ($null -eq $functionAst) { throw "Missing function $name" }
    . ([scriptblock]::Create($functionAst.Extent.Text))
}
$source = [IO.File]::ReadAllText($SourcePath)
$begin = $source.IndexOf('$serverArgs = @(')
$end = $source.IndexOf('$quotedClaudeArgs =', $begin)
if ($begin -lt 0 -or $end -lt 0) { throw 'Missing argv block' }
$argvBlock = [scriptblock]::Create($source.Substring($begin, $end - $begin))
$KeyFile = Join-Path $PSScriptRoot 'key with spaces.key'
$Port = 18765
$ExpectedGameVersion = ''
$AllowLegacy = $false
$IdleTimeoutSeconds = 1800
$NoSupervised = $false
foreach ($enabled in @($false, $true)) {
    $ClaudeNoProgressiveDisclosure = $enabled
    . $argvBlock
    if (($claudeArgs -contains '--no-progressive-disclosure') -ne $enabled) { throw 'Claude flag not preserved' }
    if ($codexArgs -contains '--no-progressive-disclosure') { throw 'Flag leaked to Codex' }
    $textArgs = ($claudeArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    if (-not (Test-CanonicalTextArguments $textArgs $claudeArgs)) { throw 'Claude text rejected' }
    if (-not (Test-CanonicalArrayArguments $claudeArgs $claudeArgs)) { throw 'Array rejected' }
    if ($enabled) {
        if (Test-CanonicalTextArguments ($textArgs + ' --no-progressive-disclosure') $claudeArgs) { throw 'Duplicate accepted' }
        if (Test-CanonicalArrayArguments ($claudeArgs + '--no-progressive-disclosure') ($claudeArgs + '--no-progressive-disclosure')) { throw 'Array duplicate accepted' }
    }
}
$ClaudeNoProgressiveDisclosure = $false
foreach ($optOut in @($false, $true)) {
    $NoSupervised = $optOut
    . $argvBlock
    foreach ($arguments in @(, $claudeArgs) + @(, $codexArgs)) {
        if (($arguments -ccontains '--supervised') -eq $optOut) { throw 'Supervisor flag wrong' }
        if (($arguments[0..2] -join ' ') -cne '-m dayz_mcp --client') { throw 'Mode prefix moved' }
        if (-not (Test-CanonicalArrayArguments $arguments $arguments)) { throw 'Supervised array rejected' }
    }
    $textArgs = ($claudeArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    if (-not (Test-CanonicalTextArguments $textArgs $claudeArgs)) { throw 'Supervised text rejected' }
}
'PASS'
'''

# Prints the argv block's two argument lists for the parameters it is given, one
# bracketed element per line, so the PowerShell argv can be compared exactly with
# the Python installer's (0f68, review of #121: comparing it with itself let a
# dropped --require-version through).
_PS_ARGV_DUMP = r'''
param(
    [string]$SourcePath,
    [string]$KeyFile,
    [int]$Port,
    [string]$ExpectedGameVersion = '',
    [double]$IdleTimeoutSeconds = 1800,
    [switch]$AllowLegacy,
    [switch]$ClaudeNoProgressiveDisclosure,
    [switch]$NoSupervised
)
$ErrorActionPreference = 'Stop'
$source = [IO.File]::ReadAllText($SourcePath)
$begin = $source.IndexOf('$serverArgs = @(')
$end = $source.IndexOf('$quotedClaudeArgs =', $begin)
if ($begin -lt 0 -or $end -lt 0) { throw 'Missing argv block' }
. ([scriptblock]::Create($source.Substring($begin, $end - $begin)))
$claudeArgs | ForEach-Object { '[' + $_ + ']' }
'---'
$codexArgs | ForEach-Object { '[' + $_ + ']' }
'''


@unittest.skipUnless(os.name == "nt", "install-mcp.ps1 runs on Windows PowerShell")
class PowerShellInstallerArgvTest(unittest.TestCase):
    @slow_test
    def test_claude_switch_adds_the_disclosure_opt_out_to_claude_only(self) -> None:
        with TemporaryDirectory() as tmp:
            probe = Path(tmp) / "argv_probe.ps1"
            probe.write_text(_PS_ARGV_PROBE, encoding="utf-8")
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(probe),
                    "-SourcePath",
                    str(TOOLS_DIR / "install-mcp.ps1"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(
            (completed.returncode, completed.stdout.strip()),
            (0, "PASS"),
            completed.stderr,
        )

    @slow_test
    def test_both_installers_register_the_same_argv(self) -> None:
        cases = (
            ([], []),
            (["--no-supervised"], ["-NoSupervised"]),
            (
                ["--allow-legacy", "--claude-no-progressive-disclosure"],
                ["-AllowLegacy", "-ClaudeNoProgressiveDisclosure"],
            ),
            (
                ["--expected-game-version", "1.28.159000", "--idle-timeout-seconds", "2.5"],
                ["-ExpectedGameVersion", "1.28.159000", "-IdleTimeoutSeconds", "2.5"],
            ),
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            keyfile = root / "key with spaces.key"
            probe = root / "argv_dump.ps1"
            probe.write_text(_PS_ARGV_DUMP, encoding="utf-8")
            for python_flags, powershell_flags in cases:
                with self.subTest(python_flags=python_flags):
                    options = parse_args(
                        ["--port", "18765", "--keyfile", str(keyfile), *python_flags],
                        tools_root=root,
                    )
                    completed = subprocess.run(
                        [
                            "powershell.exe",
                            "-NoProfile",
                            "-NonInteractive",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(probe),
                            "-SourcePath",
                            str(TOOLS_DIR / "install-mcp.ps1"),
                            "-KeyFile",
                            str(keyfile),
                            "-Port",
                            "18765",
                            *powershell_flags,
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    lines = completed.stdout.splitlines()
                    self.assertEqual(lines.count("---"), 1, completed.stdout)
                    split = lines.index("---")
                    for role, platform, dumped in (
                        ("CLAUDE", "claude", lines[:split]),
                        ("CODEX", "codex", lines[split + 1 :]),
                    ):
                        self.assertTrue(
                            all(line[:1] == "[" and line[-1:] == "]" for line in dumped),
                            (role, dumped),
                        )
                        self.assertEqual(
                            [line[1:-1] for line in dumped],
                            build_client_args(options, platform),
                            role,
                        )


def _parser_option_sets(parser: argparse.ArgumentParser) -> tuple[set[str], set[str]]:
    """Value vs boolean flags from the parser the server actually builds.

    Help is argparse's terminal action, never a registration flag. ``nargs == 0``
    is store_true / store_false / store_const; everything else consumes a value.
    """
    value: set[str] = set()
    boolean: set[str] = set()
    for action in parser._actions:
        if not action.option_strings or isinstance(action, argparse._HelpAction):
            continue
        flags = set(action.option_strings)
        if action.nargs == 0:
            boolean.update(flags)
        else:
            value.update(flags)
    return value, boolean


def _ps_function_body(source: str, name: str) -> str:
    marker = f"function {name} {{"
    start = source.index(marker)
    next_at = source.find("\nfunction ", start + len(marker))
    if next_at < 0:
        raise AssertionError(f"{name} has no following function")
    return source[start:next_at]


def _ps_flag_array(body: str, variable: str) -> set[str]:
    marker = f"${variable} = @("
    count = body.count(marker)
    if count != 1:
        raise AssertionError(f"${variable} appears {count} times")
    start = body.index(marker) + len(marker)
    end = body.index(")", start)
    flags = re.findall(r"'(-{1,2}[^']+)'", body[start:end])
    if not flags or len(flags) != len(set(flags)):
        raise AssertionError(f"flags for ${variable} did not parse: {flags}")
    return set(flags)


def _assert_flag_copy(
    parser_flags: set[str],
    copy_flags: set[str],
    *,
    copy_name: str,
    allowed_extras: frozenset[str],
    omissions: frozenset[str],
) -> None:
    missing = sorted(parser_flags - copy_flags - omissions)
    if missing:
        raise AssertionError(
            f"{copy_name} is missing parser option(s): {', '.join(missing)}"
        )
    extra = sorted(copy_flags - parser_flags - allowed_extras)
    if extra:
        raise AssertionError(
            f"{copy_name} has flag(s) the parser does not define: {', '.join(extra)}"
        )
    stale = sorted(omissions & copy_flags)
    if stale:
        raise AssertionError(
            f"{copy_name} exception list includes option(s) the copy already has: "
            f"{', '.join(stale)}"
        )
    unknown = sorted(omissions - parser_flags)
    if unknown:
        raise AssertionError(
            f"{copy_name} exception list names option(s) the parser does not define: "
            f"{', '.join(unknown)}"
        )


class RegistryFlagGrammarTest(unittest.TestCase):
    def test_copies_match_the_server_parser(self) -> None:
        from dayz_mcp import doctor as doctor_module
        from dayz_mcp import host_config
        from dayz_mcp.server_cli import build_server_parser

        # `python -m dayz_mcp`: the interpreter's module switch, not a server_cli
        # option. Registration copies and the daemon argv include it. host_config
        # checks args[:2] itself (host_config.py:254) and does not list it.
        module_switch = frozenset({"-m"})
        # doctor._DAEMON_* parses the listener argv (doctor.py:893-897).
        # daemon_contract.build_daemon_argv (daemon_contract.py:15-45) forwards
        # bridge policy and `--daemon` only, so client-only parser options are
        # omitted from that copy on purpose.
        daemon_value_omissions = frozenset(
            {
                # registration identity / catalog; build_daemon_argv never emits them
                "--client-platform",
                "--task-label",
                "--tool-pack",
            }
        )
        daemon_boolean_omissions = frozenset(
            {
                # the listener's mode is --daemon (daemon_contract.py:21);
                # these two select the other modes (server_cli.py:96-117)
                "--client",
                "--embedded",
                # supervisor wraps the client, not the daemon (server_cli.py:87-95)
                "--supervised",
                # client-side "do not spawn a daemon" (server_cli.py:71-76)
                "--no-daemon-autospawn",
                # client tool-list shape (server_cli.py:77-86)
                "--no-progressive-disclosure",
            }
        )
        # host_config accepts a client registration only (host_config.py:263,
        # namespace.mode != "client"), so the other mode flags are not in that copy.
        host_boolean_omissions = frozenset({"--daemon", "--embedded"})

        value, boolean = _parser_option_sets(build_server_parser())
        self.assertIn("--exec-audit-path", value)
        self.assertNotIn("--help", value | boolean)
        self.assertNotIn("-h", value | boolean)
        self.assertTrue(value.isdisjoint(boolean))

        script = (TOOLS_DIR / "install-mcp.ps1").read_text(encoding="utf-8")
        text_body = _ps_function_body(script, "Test-CanonicalTextArguments")
        array_body = _ps_function_body(script, "Test-CanonicalArrayArguments")
        copies = (
            ("install_mcp._VALUE_FLAGS", set(installer._VALUE_FLAGS), value, module_switch, frozenset()),
            ("install_mcp._BOOLEAN_FLAGS", set(installer._BOOLEAN_FLAGS), boolean, frozenset(), frozenset()),
            (
                "install-mcp.ps1 Test-CanonicalTextArguments $valueFlags",
                _ps_flag_array(text_body, "valueFlags"),
                value,
                module_switch,
                frozenset(),
            ),
            (
                "install-mcp.ps1 Test-CanonicalTextArguments $booleanFlags",
                _ps_flag_array(text_body, "booleanFlags"),
                boolean,
                frozenset(),
                frozenset(),
            ),
            (
                "install-mcp.ps1 Test-CanonicalArrayArguments $valueFlags",
                _ps_flag_array(array_body, "valueFlags"),
                value,
                module_switch,
                frozenset(),
            ),
            (
                "install-mcp.ps1 Test-CanonicalArrayArguments $booleanFlags",
                _ps_flag_array(array_body, "booleanFlags"),
                boolean,
                frozenset(),
                frozenset(),
            ),
            ("doctor._VALUE_OPTIONS", set(doctor_module._VALUE_OPTIONS), value, module_switch, frozenset()),
            ("doctor._BOOLEAN_OPTIONS", set(doctor_module._BOOLEAN_OPTIONS), boolean, frozenset(), frozenset()),
            (
                "doctor._DAEMON_VALUE_OPTIONS",
                set(doctor_module._DAEMON_VALUE_OPTIONS),
                value,
                module_switch,
                daemon_value_omissions,
            ),
            (
                "doctor._DAEMON_BOOLEAN_OPTIONS",
                set(doctor_module._DAEMON_BOOLEAN_OPTIONS),
                boolean,
                frozenset(),
                daemon_boolean_omissions,
            ),
            (
                "host_config._VALUE_OPTIONS",
                set(host_config._VALUE_OPTIONS),
                value,
                frozenset(),
                frozenset(),
            ),
            (
                "host_config._BOOLEAN_OPTIONS",
                set(host_config._BOOLEAN_OPTIONS),
                boolean,
                frozenset(),
                host_boolean_omissions,
            ),
        )
        for copy_name, copy_flags, parser_flags, extras, omissions in copies:
            with self.subTest(copy=copy_name):
                _assert_flag_copy(
                    parser_flags,
                    copy_flags,
                    copy_name=copy_name,
                    allowed_extras=extras,
                    omissions=omissions,
                )

        registration_value = (
            ("install_mcp._VALUE_FLAGS", set(installer._VALUE_FLAGS)),
            ("install-mcp.ps1 Test-CanonicalTextArguments $valueFlags", _ps_flag_array(text_body, "valueFlags")),
            ("install-mcp.ps1 Test-CanonicalArrayArguments $valueFlags", _ps_flag_array(array_body, "valueFlags")),
            ("doctor._VALUE_OPTIONS", set(doctor_module._VALUE_OPTIONS)),
        )
        registration_boolean = (
            ("install_mcp._BOOLEAN_FLAGS", set(installer._BOOLEAN_FLAGS)),
            ("install-mcp.ps1 Test-CanonicalTextArguments $booleanFlags", _ps_flag_array(text_body, "booleanFlags")),
            ("install-mcp.ps1 Test-CanonicalArrayArguments $booleanFlags", _ps_flag_array(array_body, "booleanFlags")),
            ("doctor._BOOLEAN_OPTIONS", set(doctor_module._BOOLEAN_OPTIONS)),
        )
        for group in (registration_value, registration_boolean):
            baseline_name, baseline = group[0]
            for copy_name, copy_flags in group[1:]:
                missing = sorted(baseline - copy_flags)
                extra = sorted(copy_flags - baseline)
                if missing or extra:
                    self.fail(
                        f"{copy_name} drifted from {baseline_name}: "
                        f"missing {', '.join(missing) or '-'}; "
                        f"extra {', '.join(extra) or '-'}"
                    )


def _decode_powershell(blob: bytes) -> str:
    if blob.startswith(b"\xff\xfe") or blob.startswith(b"\xfe\xff") or b"\x00" in blob[:80]:
        try:
            return blob.decode("utf-16")
        except UnicodeError:
            pass
    try:
        return blob.decode("utf-8")
    except UnicodeError:
        return blob.decode("cp1252", errors="replace")


def _run_powershell(script: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
        ],
        capture_output=True,
        check=False,
    )
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        _decode_powershell(completed.stdout),
        _decode_powershell(completed.stderr),
    )


_PS_DECISION_PROBE = r'''
param([string]$SourcePath)
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($SourcePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ('PowerShell source did not parse: ' + $parseErrors[0].ToString()) }
$functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-RegistrationReplaceDecision' }, $true)
if ($null -eq $functionAst) { throw 'Missing function Get-RegistrationReplaceDecision' }
. ([scriptblock]::Create($functionAst.Extent.Text))

function New-Probe($CommandMissing, $ExitCode, [string]$Stdout, [string]$Stderr) {
  return @{ CommandMissing = [bool]$CommandMissing; ExitCode = $ExitCode; Stdout = $Stdout; Stderr = $Stderr }
}
function Assert-Decision($Name, $Claude, $Codex, [bool]$Replace, [string]$ExpectAction, [string[]]$Fragments) {
  $decision = Get-RegistrationReplaceDecision -Claude $Claude -Codex $Codex -ReplaceExistingRegistration:$Replace
  if ($decision.Action -cne $ExpectAction) {
    throw "$Name action=$($decision.Action) reason=$($decision.Reason)"
  }
  if ($ExpectAction -eq 'proceed' -and $decision.Reason) {
    throw "$Name proceed carried a reason: $($decision.Reason)"
  }
  foreach ($fragment in @($Fragments)) {
    if (-not $fragment) { continue }
    if ($decision.Reason -notlike ('*' + $fragment + '*')) {
      throw "$Name reason missing [$fragment]: $($decision.Reason)"
    }
  }
}

$absentClaude = New-Probe $false 1 '' "No MCP server named `"dayz-mcp`". Configured servers: other, dayz-mcp`n"
$absentCodex = New-Probe $false 1 '' "Error: No MCP server named 'dayz-mcp' found.`n"
$presentClaude = New-Probe $false 0 "dayz-mcp:`r`n  Type: stdio`r`n  Command: C:\Python\python.exe`r`n  Args: -m dayz_mcp --client --exec-audit-path C:\audit`r`n" ''
$presentCodex = New-Probe $false 0 '{"transport":{"type":"stdio","command":"C:\\Python\\python.exe","args":["-m","dayz_mcp","--client"]}}' ''
$missing = @{ CommandMissing = $true; ExitCode = $null; Stdout = ''; Stderr = '' }

Assert-Decision 'both absent' $absentClaude $absentCodex $false 'proceed' @()
Assert-Decision 'both absent replace' $absentClaude $absentCodex $true 'proceed' @()
Assert-Decision 'claude present' $presentClaude $absentCodex $false 'refuse' @('already registered', 'Claude', 'python tools/install_mcp.py --register', '-ReplaceExistingRegistration')
Assert-Decision 'codex present' $absentClaude $presentCodex $false 'refuse' @('already registered', 'Codex', 'python tools/install_mcp.py --register')
Assert-Decision 'both present' $presentClaude $presentCodex $false 'refuse' @('already registered', 'Claude and Codex')
Assert-Decision 'claude present replace' $presentClaude $absentCodex $true 'proceed' @()
Assert-Decision 'both present replace' $presentClaude $presentCodex $true 'proceed' @()
Assert-Decision 'claude missing' $missing $absentCodex $false 'refuse' @('command missing', 'Stopped before removing')
Assert-Decision 'claude missing replace' $missing $absentCodex $true 'refuse' @('command missing', 'Stopped before removing')
Assert-Decision 'codex missing' $absentClaude $missing $false 'refuse' @('Codex: command missing')
Assert-Decision 'claude exit 2' (New-Probe $false 2 'boom' '') $absentCodex $false 'refuse' @('Claude: exit code 2')
Assert-Decision 'claude garbage' (New-Probe $false 0 'hello' '') $absentCodex $false 'refuse' @('Claude: unparseable output')
Assert-Decision 'claude bad not-found' (New-Probe $false 1 '' 'not today') $absentCodex $false 'refuse' @('without the not-found message')
Assert-Decision 'stdout phrase is not absent' (New-Probe $false 1 $absentClaude.Stderr '') $absentCodex $false 'refuse' @('without the not-found message')
Assert-Decision 'claude contradictory' (New-Probe $false 0 $presentClaude.Stdout "No MCP server named `"dayz-mcp`".") $absentCodex $false 'refuse' @('unparseable output')
Assert-Decision 'stderr warning on success' (New-Probe $false 0 $presentClaude.Stdout 'warning') $absentCodex $false 'refuse' @('unparseable output')
$shapedAbsent = "dayz-mcp:`r`n  Type: stdio`r`n  Command: C:\Python\python.exe`r`n  Args: -m dayz_mcp`r`n"
Assert-Decision 'claude shaped absent' (New-Probe $false 1 $shapedAbsent $absentClaude.Stderr) $absentCodex $false 'refuse' @('unparseable output')
Assert-Decision 'codex garbage' $absentClaude (New-Probe $false 0 'hello' '') $false 'refuse' @('Codex: unparseable output')
Assert-Decision 'codex empty object' $absentClaude (New-Probe $false 0 '{}' '') $false 'refuse' @('Codex: unparseable output')
Assert-Decision 'codex no args' $absentClaude (New-Probe $false 0 '{"transport":{"type":"stdio","command":"C:\\Python\\python.exe"}}' '') $false 'refuse' @('Codex: unparseable output')
Assert-Decision 'swapped claude phrase' (New-Probe $false 1 '' "No MCP server named 'dayz-mcp' found") $absentCodex $false 'refuse' @('without the not-found message')
Assert-Decision 'swapped codex phrase' $absentClaude (New-Probe $false 1 '' 'No MCP server named "dayz-mcp".') $false 'refuse' @('without the not-found message')
Assert-Decision 'null exit' (@{ CommandMissing = $false; ExitCode = $null; Stdout = 'x'; Stderr = '' }) $absentCodex $false 'refuse' @('exit code is missing')
Assert-Decision 'null probe' $null $absentCodex $true 'refuse' @('probe is missing', 'Stopped before removing')
Assert-Decision 'present plus unreadable' $presentClaude (New-Probe $false 2 'boom' '') $true 'refuse' @('registration check failed', 'Codex: exit code 2')
'PASS'
'''


_PS_REMOVE_ORDER_PROBE = r'''
param(
  [string]$SourcePath,
  [string]$BothDir,
  [string]$CodexOnlyDir,
  [string]$LogPath,
  [string]$ClaudeStdout,
  [string]$ClaudeStderr,
  [string]$CodexStdout,
  [string]$CodexStderr
)
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($SourcePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ('PowerShell source did not parse: ' + $parseErrors[0].ToString()) }
foreach ($name in @('Invoke-NativeRegistrationCommand', 'Get-ClientRegistrationProbe', 'Get-RegistrationReplaceDecision')) {
  $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
  if ($null -eq $functionAst) { throw "Missing function $name" }
  . ([scriptblock]::Create($functionAst.Extent.Text))
}
$source = [IO.File]::ReadAllText($SourcePath)
$start = $source.IndexOf('$registrationDecision = Get-RegistrationReplaceDecision')
$throwAt = $source.IndexOf('throw $registrationDecision.Reason', $start)
$end = $source.IndexOf("`n  }", $throwAt)
if ($start -lt 0 -or $throwAt -lt 0 -or $end -lt 0) { throw 'decision block missing' }
$removeAt = $source.IndexOf('& claude mcp remove dayz-mcp')
if ($removeAt -lt $end) { throw 'decision block is not before mcp remove' }
$blockText = $source.Substring($start, ($end + 4) - $start)
if ($blockText -match 'mcp remove') { throw 'decision block contains mcp remove' }
$decisionBlock = [scriptblock]::Create($blockText)

$presentClaude = "dayz-mcp:`r`n  Type: stdio`r`n  Command: C:\Python\python.exe`r`n  Args: -m dayz_mcp --client --exec-audit-path C:\audit`r`n"
$presentCodex = '{"transport":{"type":"stdio","command":"C:\\Python\\python.exe","args":["-m","dayz_mcp","--client"]}}'
$absentClaude = "No MCP server named `"dayz-mcp`". Configured servers: other, dayz-mcp"
$absentCodex = "Error: No MCP server named 'dayz-mcp' found."
$cases = @(
  @{ Name = 'present'; Dir = $BothDir; Replace = $false; Expect = 'refuse'; Fragment = 'already registered'; ClaudeExit = 0; CodexExit = 0; ClaudeOut = $presentClaude; ClaudeErr = ''; CodexOut = $presentCodex; CodexErr = ''; WantClaude = $true; WantCodex = $true },
  @{ Name = 'absent'; Dir = $BothDir; Replace = $false; Expect = 'proceed'; Fragment = ''; ClaudeExit = 1; CodexExit = 1; ClaudeOut = ''; ClaudeErr = $absentClaude; CodexOut = ''; CodexErr = $absentCodex; WantClaude = $true; WantCodex = $true },
  @{ Name = 'replace'; Dir = $BothDir; Replace = $true; Expect = 'proceed'; Fragment = ''; ClaudeExit = 0; CodexExit = 0; ClaudeOut = $presentClaude; ClaudeErr = ''; CodexOut = $presentCodex; CodexErr = ''; WantClaude = $true; WantCodex = $true },
  @{ Name = 'garbage'; Dir = $BothDir; Replace = $false; Expect = 'refuse'; Fragment = 'unparseable output'; ClaudeExit = 0; CodexExit = 1; ClaudeOut = 'hello'; ClaudeErr = ''; CodexOut = ''; CodexErr = $absentCodex; WantClaude = $true; WantCodex = $true },
  @{ Name = 'exit-2'; Dir = $BothDir; Replace = $true; Expect = 'refuse'; Fragment = 'exit code 2'; ClaudeExit = 2; CodexExit = 1; ClaudeOut = 'boom'; ClaudeErr = ''; CodexOut = ''; CodexErr = $absentCodex; WantClaude = $true; WantCodex = $true },
  @{ Name = 'claude-missing'; Dir = $CodexOnlyDir; Replace = $false; Expect = 'refuse'; Fragment = 'command missing'; ClaudeExit = 1; CodexExit = 1; ClaudeOut = ''; ClaudeErr = $absentClaude; CodexOut = ''; CodexErr = $absentCodex; WantClaude = $false; WantCodex = $true }
)
$env:DAYZ_MCP_FAKE_LOG = $LogPath
$env:DAYZ_MCP_CLAUDE_STDOUT = $ClaudeStdout
$env:DAYZ_MCP_CLAUDE_STDERR = $ClaudeStderr
$env:DAYZ_MCP_CODEX_STDOUT = $CodexStdout
$env:DAYZ_MCP_CODEX_STDERR = $CodexStderr
$env:PATHEXT = '.CMD;.EXE;.BAT'
foreach ($case in $cases) {
  $env:PATH = $case.Dir
  $resolved = Get-Command codex.cmd -ErrorAction SilentlyContinue
  if ($null -eq $resolved -or -not $resolved.Source.StartsWith($case.Dir)) {
    throw "$($case.Name) codex resolved outside the fake dir"
  }
  if ($case.WantClaude) {
    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if ($null -eq $claude -or -not $claude.Source.StartsWith($case.Dir)) {
      throw "$($case.Name) claude resolved outside the fake dir"
    }
  }
  Set-Content -LiteralPath $ClaudeStdout -Encoding Ascii -Value $case.ClaudeOut
  Set-Content -LiteralPath $ClaudeStderr -Encoding Ascii -Value $case.ClaudeErr
  Set-Content -LiteralPath $CodexStdout -Encoding Ascii -Value $case.CodexOut
  Set-Content -LiteralPath $CodexStderr -Encoding Ascii -Value $case.CodexErr
  Set-Content -LiteralPath $LogPath -Encoding Ascii -Value ''
  $env:DAYZ_MCP_CLAUDE_EXIT = [string]$case.ClaudeExit
  $env:DAYZ_MCP_CODEX_EXIT = [string]$case.CodexExit
  $ReplaceExistingRegistration = [bool]$case.Replace
  $threw = $false
  $message = ''
  try {
    . $decisionBlock
  } catch {
    $threw = $true
    $message = [string]$_.Exception.Message
  }
  if ($case.Expect -eq 'proceed' -and $threw) { throw "$($case.Name) refused: $message" }
  if ($case.Expect -eq 'refuse' -and -not $threw) { throw "$($case.Name) proceeded" }
  if ($case.Fragment -and $message -notlike ('*' + $case.Fragment + '*')) {
    throw "$($case.Name) reason missing [$($case.Fragment)]: $message"
  }
  $logged = [IO.File]::ReadAllText($LogPath)
  if ($logged -match 'remove' -or $logged -match 'NOT-GET') {
    throw "$($case.Name) invoked something other than mcp get: $logged"
  }
  if ($case.WantClaude -and $logged -notmatch 'CLAUDE "mcp" "get"') {
    throw "$($case.Name) did not probe claude: $logged"
  }
  if ($case.WantCodex -and $logged -notmatch 'CODEX "mcp" "get"') {
    throw "$($case.Name) did not probe codex: $logged"
  }
  if (-not $case.WantClaude -and $logged -match 'CLAUDE') {
    throw "$($case.Name) invoked claude: $logged"
  }
}
'PASS'
'''


def _fake_mcp_cmd(path: Path, role: str, stdout_var: str, stderr_var: str, exit_var: str) -> None:
    # Not-found text belongs on stderr, matching installer-not-found-fixtures-v1.json.
    # `exit /b` inside a parenthesized block does not become PowerShell's
    # $LASTEXITCODE, so the get path leaves that block before exiting.
    path.write_text(
        "@echo off\n"
        f'>>"%DAYZ_MCP_FAKE_LOG%" echo {role} %*\n'
        'if /I not "%~1"=="mcp" goto :notget\n'
        'if /I not "%~2"=="get" goto :notget\n'
        f'type "%{stdout_var}%"\n'
        f'type "%{stderr_var}%" 1>&2\n'
        f"exit /b %{exit_var}%\n"
        ":notget\n"
        f'>>"%DAYZ_MCP_FAKE_LOG%" echo {role}-NOT-GET %*\n'
        "exit /b 0\n",
        encoding="ascii",
        newline="\r\n",
    )


class PowerShellRegisterGuardTest(unittest.TestCase):
    def test_decision_precedes_remove_and_docs_name_the_switch(self) -> None:
        source = (TOOLS_DIR / "install-mcp.ps1").read_text(encoding="utf-8")
        register_at = source.rindex("if ($Register)")
        decision_at = source.index("$registrationDecision = Get-RegistrationReplaceDecision")
        remove_at = source.index("& claude mcp remove dayz-mcp")
        codex_remove_at = source.index("& $CodexCmd mcp remove dayz-mcp")
        self.assertLess(register_at, decision_at)
        self.assertLess(decision_at, remove_at)
        self.assertLess(decision_at, codex_remove_at)
        window = source[register_at:remove_at]
        self.assertIn("throw $registrationDecision.Reason", window)
        self.assertNotIn("mcp remove", window)
        self.assertIn(
            "if ($ReplaceExistingRegistration) {\n    & claude mcp remove dayz-mcp -s user\n  }",
            source,
        )
        self.assertIn(
            "if ($ReplaceExistingRegistration) {\n    & $CodexCmd mcp remove dayz-mcp\n  }",
            source,
        )
        probe_body = _ps_function_body(source, "Get-ClientRegistrationProbe")
        self.assertNotIn("mcp remove", probe_body)
        self.assertNotIn("mcp add", probe_body)
        self.assertIn("'mcp', 'get', 'dayz-mcp'", probe_body)
        self.assertIn("[switch]$ReplaceExistingRegistration", source)
        readme = (TOOLS_DIR.parent / "README.md").read_text(encoding="utf-8")
        quickstart = (TOOLS_DIR.parent / "QUICKSTART.md").read_text(encoding="utf-8")
        self.assertIn("-ReplaceExistingRegistration", readme)
        self.assertIn("python tools/install_mcp.py --register", readme)
        self.assertNotIn("replaces\nboth registrations", readme)
        self.assertIn("-ReplaceExistingRegistration", quickstart)

    @unittest.skipUnless(os.name == "nt", "install-mcp.ps1 runs on Windows PowerShell")
    @slow_test
    def test_replace_decision_matrix(self) -> None:
        with TemporaryDirectory() as tmp:
            probe = Path(tmp) / "decision_probe.ps1"
            probe.write_text(_PS_DECISION_PROBE, encoding="utf-8")
            completed = _run_powershell(
                probe, ["-SourcePath", str(TOOLS_DIR / "install-mcp.ps1")]
            )
        self.assertEqual(
            (completed.returncode, completed.stdout.strip()),
            (0, "PASS"),
            completed.stderr,
        )

    @unittest.skipUnless(os.name == "nt", "install-mcp.ps1 runs on Windows PowerShell")
    @slow_test
    def test_refusal_probes_get_and_does_not_call_remove(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            both = root / "both"
            codex_only = root / "codex-only"
            both.mkdir()
            codex_only.mkdir()
            _fake_mcp_cmd(both / "claude.cmd", "CLAUDE", "DAYZ_MCP_CLAUDE_STDOUT", "DAYZ_MCP_CLAUDE_STDERR", "DAYZ_MCP_CLAUDE_EXIT")
            _fake_mcp_cmd(both / "codex.cmd", "CODEX", "DAYZ_MCP_CODEX_STDOUT", "DAYZ_MCP_CODEX_STDERR", "DAYZ_MCP_CODEX_EXIT")
            _fake_mcp_cmd(codex_only / "codex.cmd", "CODEX", "DAYZ_MCP_CODEX_STDOUT", "DAYZ_MCP_CODEX_STDERR", "DAYZ_MCP_CODEX_EXIT")
            probe = root / "remove_order_probe.ps1"
            probe.write_text(_PS_REMOVE_ORDER_PROBE, encoding="utf-8")
            completed = _run_powershell(
                probe,
                [
                    "-SourcePath",
                    str(TOOLS_DIR / "install-mcp.ps1"),
                    "-BothDir",
                    str(both),
                    "-CodexOnlyDir",
                    str(codex_only),
                    "-LogPath",
                    str(root / "calls.log"),
                    "-ClaudeStdout",
                    str(root / "claude-out.txt"),
                    "-ClaudeStderr",
                    str(root / "claude-err.txt"),
                    "-CodexStdout",
                    str(root / "codex-out.txt"),
                    "-CodexStderr",
                    str(root / "codex-err.txt"),
                ],
            )
        self.assertEqual(
            (completed.returncode, completed.stdout.strip()),
            (0, "PASS"),
            completed.stderr,
        )

    def _write_channel_fakes(self, directory: Path) -> None:
        # `exit /b` inside a parenthesized block does not become PowerShell's
        # $LASTEXITCODE, so every exit is at the top level after a goto.
        claude = r"""@echo off
if not "%~2"=="get" goto :notget
if "%FAKE_PRESENT%"=="1" goto :present
echo No MCP server named "dayz-mcp". Configured servers: other, dayz-mcp 1>&2
exit /b 1
:present
echo dayz-mcp:
echo   Type: stdio
echo   Command: C:\Python\python.exe
echo   Args: -m dayz_mcp --client
exit /b 0
:notget
if not "%~2"=="remove" goto :notremove
if not exist "%FAKE_STATE%" goto :removeabsent
echo CLAUDE_REMOVE_DELETED>>"%FAKE_LOG%"
del "%FAKE_STATE%"
exit /b 0
:removeabsent
echo CLAUDE_REMOVE_ABSENT>>"%FAKE_LOG%"
exit /b 0
:notremove
if not "%~2"=="add" goto :other
echo CLAUDE_ADD>>"%FAKE_LOG%"
if "%FAKE_ADD_FAIL%"=="1" exit /b 1
exit /b 0
:other
echo CLAUDE_OTHER>>"%FAKE_LOG%"
exit /b 0
"""
        codex = r"""@echo off
if not "%~2"=="get" goto :notget
if "%FAKE_PRESENT%"=="1" goto :present
echo old-registration>"%FAKE_STATE%"
echo Error: No MCP server named 'dayz-mcp' found. 1>&2
exit /b 1
:present
echo {"transport":{"type":"stdio","command":"C:\\Python\\python.exe","args":["-m","dayz_mcp","--client"]}}
exit /b 0
:notget
if not "%~2"=="remove" goto :notremove
echo CODEX_REMOVE>>"%FAKE_LOG%"
exit /b 0
:notremove
if not "%~2"=="add" goto :other
echo CODEX_ADD>>"%FAKE_LOG%"
exit /b 0
:other
echo CODEX_OTHER>>"%FAKE_LOG%"
exit /b 0
"""
        (directory / "claude.cmd").write_text(claude, encoding="ascii", newline="\r\n")
        (directory / "codex.cmd").write_text(codex, encoding="ascii", newline="\r\n")

    def _run_register_mode(self, mode: str) -> subprocess.CompletedProcess[str]:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bindir = root / "bin"
            bindir.mkdir()
            self._write_channel_fakes(bindir)
            probe = root / "register_mutation.ps1"
            probe.write_text(_PS_REGISTER_MUTATION_PROBE, encoding="utf-8")
            return _run_powershell(
                probe,
                [
                    "-SourcePath",
                    str(TOOLS_DIR / "install-mcp.ps1"),
                    "-BinDir",
                    str(bindir),
                    "-StatePath",
                    str(root / "state.txt"),
                    "-LogPath",
                    str(root / "calls.txt"),
                    "-Mode",
                    mode,
                ],
            )

    @unittest.skipUnless(os.name == "nt", "install-mcp.ps1 runs on Windows PowerShell")
    @slow_test
    def test_fixture_stderr_absent_proceeds(self) -> None:
        completed = self._run_register_mode("stderr")
        self.assertEqual(
            (completed.returncode, completed.stdout.strip()),
            (0, "PASS"),
            completed.stderr,
        )

    @unittest.skipUnless(os.name == "nt", "install-mcp.ps1 runs on Windows PowerShell")
    @slow_test
    def test_new_install_does_not_remove_a_registration_that_appears(self) -> None:
        completed = self._run_register_mode("race")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.strip().endswith("PASS"), completed.stdout)

    @unittest.skipUnless(os.name == "nt", "install-mcp.ps1 runs on Windows PowerShell")
    @slow_test
    def test_failed_add_stops_without_remove(self) -> None:
        completed = self._run_register_mode("addfail")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.strip().endswith("PASS"), completed.stdout)

    @unittest.skipUnless(os.name == "nt", "install-mcp.ps1 runs on Windows PowerShell")
    @slow_test
    def test_replace_switch_still_removes_before_add(self) -> None:
        completed = self._run_register_mode("replace")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.strip().endswith("PASS"), completed.stdout)


_PS_REGISTER_MUTATION_PROBE = r'''
param(
  [string]$SourcePath,
  [string]$BinDir,
  [string]$StatePath,
  [string]$LogPath,
  [string]$Mode
)
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($SourcePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ('PowerShell source did not parse: ' + $parseErrors[0].ToString()) }
foreach ($name in @('Invoke-NativeRegistrationCommand', 'Get-ClientRegistrationProbe', 'Get-RegistrationReplaceDecision')) {
  $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
  if ($null -eq $functionAst) { throw "Missing function $name" }
  . ([scriptblock]::Create($functionAst.Extent.Text))
}
$source = [IO.File]::ReadAllText($SourcePath)
$start = $source.IndexOf('$registrationDecision = Get-RegistrationReplaceDecision')
$end = $source.IndexOf("  # Self-verify", $start)
if ($start -lt 0 -or $end -lt 0) { throw 'register block missing' }
$blockText = $source.Substring($start, $end - $start)
$env:PATH = $BinDir
$env:PATHEXT = '.CMD;.EXE;.BAT'
$env:FAKE_STATE = $StatePath
$env:FAKE_LOG = $LogPath
$env:FAKE_PRESENT = '0'
$env:FAKE_ADD_FAIL = '0'
$ReplaceExistingRegistration = $false
if ($Mode -eq 'replace') {
  $env:FAKE_PRESENT = '1'
  $ReplaceExistingRegistration = $true
  Set-Content -LiteralPath $StatePath -Encoding Ascii -Value 'old-registration'
} elseif (Test-Path -LiteralPath $StatePath) {
  Remove-Item -LiteralPath $StatePath -Force
}
if ($Mode -eq 'addfail') { $env:FAKE_ADD_FAIL = '1' }
Set-Content -LiteralPath $LogPath -Encoding Ascii -Value ''
$resolved = Get-Command claude -ErrorAction SilentlyContinue
if ($null -eq $resolved -or -not $resolved.Source.StartsWith($BinDir)) {
  throw "claude resolved outside the fake dir: $($resolved.Source)"
}
$VenvPython = 'C:\fake\python.exe'
$claudeArgs = @('--client')
$codexArgs = @('--client')
if ($Mode -eq 'stderr') {
  $decision = Get-RegistrationReplaceDecision -Claude (Get-ClientRegistrationProbe -Client claude) -Codex (Get-ClientRegistrationProbe -Client codex) -ReplaceExistingRegistration:$false
  if ($decision.Action -ne 'proceed') { throw $decision.Reason }
  'PASS'
  return
}
$threw = $false
$message = ''
try {
  . ([scriptblock]::Create($blockText))
} catch {
  $threw = $true
  $message = [string]$_.Exception.Message
}
$logged = [IO.File]::ReadAllText($LogPath)
$state = Test-Path -LiteralPath $StatePath
if ($Mode -eq 'race') {
  if ($threw) { throw "race refused: $message" }
  if ($logged -match 'REMOVE') { throw "race removed: $logged" }
  if ($logged -notmatch 'CLAUDE_ADD' -or $logged -notmatch 'CODEX_ADD') { throw "race did not add: $logged" }
  if (-not $state) { throw 'race deleted the registration that appeared after the first get' }
}
if ($Mode -eq 'addfail') {
  if (-not $threw) { throw "add failure proceeded: $logged" }
  if ($message -notlike '*claude mcp add*') { throw "add failure reason: $message" }
  if ($logged -match 'REMOVE') { throw "add failure removed: $logged" }
  if ($logged -match 'CODEX_ADD') { throw "add failure continued to codex: $logged" }
  if ($logged -notmatch 'CLAUDE_ADD') { throw "add was not attempted: $logged" }
}
if ($Mode -eq 'replace') {
  if ($threw) { throw "replace refused: $message" }
  if ($logged -notmatch 'CLAUDE_REMOVE_DELETED') { throw "replace did not remove: $logged" }
  if ($logged -notmatch 'CODEX_REMOVE' -or $logged -notmatch 'CLAUDE_ADD' -or $logged -notmatch 'CODEX_ADD') {
    throw "replace log: $logged"
  }
}
'PASS'
'''


if __name__ == "__main__":
    unittest.main()
