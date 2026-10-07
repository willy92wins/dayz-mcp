"""DZ-R9 step 6: host-config journal crash states and named contract errors."""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import dayz_mcp.host_config as host_config
from dayz_mcp.bridge_errors import _opaque_dayz_test_failure
from dayz_mcp.dayz_test_request import RequestProjectPolicy
from dayz_mcp.dayz_test_tool import build_run_request
from dayz_mcp.host_config import HostConfigCrash, HostConfigError, apply_host_timeouts
from dayz_mcp.server_cli import CliContractError, build_lock_wait_s, shared_root
from install_mcp import (
    InstallerContractError,
    RegistrationSpec,
    _public_installer_error_code,
    register_transaction,
)


def _claude_original() -> bytes:
    payload = {
        "mcpServers": {
            "dayz-mcp": {
                "args": ["-m", "dayz_mcp"],
                "command": "p" * 33,
            }
        }
    }
    return json.dumps(payload, indent=2).encode("utf-8")


_CODEX_ORIGINAL = (
    '[mcp_servers.dayz-mcp]\ncommand = "python.exe"\nargs = ["-m", "dayz_mcp"]\n'
).encode()


class _ProbeProvider:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, role: str) -> None:
        self.calls += 1
        raise AssertionError(role)

    def remove(self, role: str) -> None:
        self.calls += 1
        raise AssertionError(role)

    def add(self, role: str, spec: RegistrationSpec) -> None:
        self.calls += 1
        raise AssertionError(role)


def _desired() -> dict[str, RegistrationSpec]:
    spec = RegistrationSpec(Path(r"C:\new\python.exe"), ("-m", "dayz_mcp"))
    return {"CLAUDE": spec, "CODEX": spec}


class HostJournalFragmentTests(unittest.TestCase):
    def _paths(self, root: Path) -> tuple[Path, Path, Path]:
        claude_path = root / ".claude.json"
        codex_path = root / "config.toml"
        journal = root / "host-config-transaction"
        claude_path.write_bytes(_claude_original())
        codex_path.write_bytes(_CODEX_ORIGINAL)
        return claude_path, codex_path, journal

    def _assert_two_runs_commit(self, claude_path: Path, codex_path: Path, journal: Path) -> None:
        apply_host_timeouts(claude_path, codex_path, journal_root=journal)
        apply_host_timeouts(claude_path, codex_path, journal_root=journal)
        self.assertFalse(journal.exists())
        self.assertEqual(
            claude_path.read_bytes(),
            host_config.build_claude_target(_claude_original()),
        )
        self.assertEqual(
            codex_path.read_bytes(),
            host_config.build_codex_target(_CODEX_ORIGINAL),
        )

    def test_zero_byte_manifest_next_is_discarded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            claude_path, codex_path, journal = self._paths(Path(directory))
            journal.mkdir()
            (journal / "manifest.next").write_bytes(b"")
            self._assert_two_runs_commit(claude_path, codex_path, journal)

    def test_partial_manifest_next_is_discarded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            claude_path, codex_path, journal = self._paths(Path(directory))
            journal.mkdir()
            (journal / "manifest.next").write_bytes(b'{"schema":')
            self._assert_two_runs_commit(claude_path, codex_path, journal)

    def test_staging_fragment_is_discarded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            claude_path, codex_path, journal = self._paths(Path(directory))
            journal.mkdir()
            (journal / "manifest.staging").write_bytes(b'{"schema":')
            self._assert_two_runs_commit(claude_path, codex_path, journal)

    def test_empty_journal_after_mkdir_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            claude_path, codex_path, journal = self._paths(Path(directory))
            journal.mkdir()
            self._assert_two_runs_commit(claude_path, codex_path, journal)

    def test_empty_journal_after_cleanup_unlink_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            claude_path, codex_path, journal = self._paths(Path(directory))

            def unlink_then_die(path: Path) -> None:
                for child in list(Path(path).iterdir()):
                    if child.is_file():
                        child.unlink()
                raise HostConfigCrash()

            with mock.patch.object(host_config.shutil, "rmtree", unlink_then_die):
                with self.assertRaises(HostConfigCrash):
                    apply_host_timeouts(claude_path, codex_path, journal_root=journal)
            self.assertTrue(journal.is_dir())
            self.assertEqual(list(journal.iterdir()), [])
            self._assert_two_runs_commit(claude_path, codex_path, journal)

    def test_unexpected_journal_file_still_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            claude_path, codex_path, journal = self._paths(Path(directory))
            journal.mkdir()
            (journal / "notes.txt").write_text("x", encoding="utf-8")
            before_claude = claude_path.read_bytes()
            before_codex = codex_path.read_bytes()
            with self.assertRaises(HostConfigError) as raised:
                apply_host_timeouts(claude_path, codex_path, journal_root=journal)
            self.assertEqual(str(raised.exception), "registration_journal_invalid")
            self.assertEqual(claude_path.read_bytes(), before_claude)
            self.assertEqual(codex_path.read_bytes(), before_codex)
            self.assertTrue((journal / "notes.txt").is_file())


class NamedContractErrorTests(unittest.TestCase):
    def test_relative_shared_root_reaches_the_dayz_test_failure_text(self) -> None:
        class _Sealed:
            def __init__(self, policy: RequestProjectPolicy) -> None:
                self.policy = policy

        policy = RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF",),
            mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
            mod_roots=(r"P:\Mods",),
        )
        relative = "g5r9-step6-relative-shared"
        with mock.patch.dict(os.environ, {"DAYZ_MCP_SHARED_ROOT": relative}):
            with self.assertRaises(CliContractError) as raised:
                build_run_request((_Sealed(policy),), project="ExampleMod", mode="server")
        self.assertEqual(raised.exception.code, "relative_shared_root")
        reported = _opaque_dayz_test_failure(raised.exception)
        self.assertEqual(reported, "dayz_test_failed:CliContractError:relative_shared_root")
        self.assertNotIn(relative, reported)
        self.assertFalse((Path.cwd() / relative).exists())

    def test_invalid_build_lock_wait_names_its_code(self) -> None:
        with mock.patch.dict(os.environ, {"DAYZ_MCP_BUILD_LOCK_WAIT_S": "nan"}):
            with self.assertRaises(CliContractError) as raised:
                build_lock_wait_s()
        self.assertEqual(raised.exception.code, "invalid_build_lock_wait")
        self.assertIn(
            "invalid_build_lock_wait",
            _opaque_dayz_test_failure(raised.exception),
        )

    def test_registration_lock_open_failed_is_not_collapsed(self) -> None:
        error = OSError(5, "registration_lock_open_failed")
        self.assertEqual(_public_installer_error_code(error), "registration_lock_open_failed")
        self.assertEqual(
            _public_installer_error_code(OSError(2, r"C:\Users\secret\missing")),
            "installer_failed",
        )
        self.assertEqual(
            _public_installer_error_code(InstallerContractError("relative_journal_root")),
            "relative_journal_root",
        )

    def test_nan_and_infinity_lock_waits_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "registration-transaction"
            for timeout in (math.nan, math.inf, -math.inf):
                provider = _ProbeProvider()
                with self.assertRaises(InstallerContractError) as raised:
                    register_transaction(
                        provider,
                        _desired(),
                        journal_root=journal,
                        lock_timeout_s=timeout,
                    )
                self.assertEqual(str(raised.exception), "invalid_registration_lock_timeout")
                self.assertEqual(provider.calls, 0)
            self.assertFalse((journal.parent / f"{journal.name}.lock").exists())

    def test_relative_journal_root_is_refused_before_provider(self) -> None:
        provider = _ProbeProvider()
        relative = Path("g5r9-step6-relative-journal")
        with self.assertRaises(InstallerContractError) as raised:
            register_transaction(provider, _desired(), journal_root=relative)
        self.assertEqual(str(raised.exception), "relative_journal_root")
        self.assertEqual(provider.calls, 0)
        self.assertFalse((Path.cwd() / f"{relative.name}.lock").exists())
        self.assertFalse(relative.exists())

    def test_relative_host_journal_root_is_refused_before_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "registration-transaction"
            provider = _ProbeProvider()
            with self.assertRaises(InstallerContractError) as raised:
                register_transaction(
                    provider,
                    _desired(),
                    journal_root=journal,
                    host_journal_root=Path("g5r9-step6-relative-host"),
                )
            self.assertEqual(str(raised.exception), "relative_host_journal_root")
            self.assertEqual(provider.calls, 0)
            self.assertFalse((journal.parent / f"{journal.name}.lock").exists())


if __name__ == "__main__":
    unittest.main()
