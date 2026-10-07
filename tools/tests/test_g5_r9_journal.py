from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from pathlib import Path
from unittest import mock


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import dayz_mcp.host_config as host_config
import install_mcp as installer
from dayz_mcp.host_config import (
    HostConfigCrash,
    HostConfigError,
    apply_host_timeouts,
)
from install_mcp import (
    RegistrationCrash,
    RegistrationSpec,
    RegistrationTransactionError,
    register_transaction,
)


def _claude_pair() -> tuple[bytes, bytes]:
    # Auditor sizes: original 160 B, timeout target 189 B. The canonical dump
    # without the trailing byte is 160; build_claude_target adds the timeout.
    payload = {
        "mcpServers": {
            "dayz-mcp": {
                "args": ["-m", "dayz_mcp"],
                "command": "p" * 33,
            }
        }
    }
    original = json.dumps(payload, indent=2).encode("utf-8")
    target = host_config.build_claude_target(original)
    if len(original) != 160 or len(target) != 189:
        raise AssertionError(f"claude fixture {len(original)}/{len(target)}")
    return original, target


_CODEX_ORIGINAL = (
    '[mcp_servers.dayz-mcp]\ncommand = "python.exe"\nargs = ["-m", "dayz_mcp"]\n'
).encode()


class _FakeProvider:
    def __init__(self, states: dict[str, RegistrationSpec | None]) -> None:
        self.states = dict(states)
        self.calls = 0

    def get(self, role: str) -> RegistrationSpec | None:
        self.calls += 1
        return self.states[role]

    def remove(self, role: str) -> None:
        self.calls += 1
        self.states[role] = None

    def add(self, role: str, spec: RegistrationSpec) -> None:
        self.calls += 1
        self.states[role] = spec


def _keyed_journal(root: Path, server_name: str = "dayz-mcp") -> Path:
    return root / server_name


class RestoreFinishedOriginalTest(unittest.TestCase):
    def test_finished_shorter_original_is_restore_progress(self) -> None:
        original, target = _claude_pair()
        self.assertEqual(len(original), 160)
        self.assertEqual(len(target), 189)
        self.assertTrue(host_config._is_restore_progress(original, original, target))
        self.assertTrue(
            host_config._is_restore_progress(
                original[:40] + target[40:], original, target
            )
        )
        self.assertFalse(
            host_config._is_restore_progress(b"not-our-bytes", original, target)
        )

    def test_recovery_after_rewritten_original_clears_on_the_next_two_runs(self) -> None:
        original, target = _claude_pair()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude_path = root / ".claude.json"
            codex_path = root / "config.toml"
            journal = root / "journal"
            claude_path.write_bytes(original)
            codex_path.write_bytes(_CODEX_ORIGINAL)

            def crash_write(phase: str) -> None:
                if phase == "after_write_claude":
                    raise HostConfigCrash()

            with self.assertRaises(HostConfigCrash):
                apply_host_timeouts(
                    claude_path,
                    codex_path,
                    journal_root=journal,
                    fault_injector=crash_write,
                )
            self.assertEqual(len(claude_path.read_bytes()), len(target))

            def crash_restore(phase: str) -> None:
                if phase == "after_recovery_write_claude":
                    raise HostConfigCrash()

            with self.assertRaises(HostConfigCrash):
                apply_host_timeouts(
                    claude_path,
                    codex_path,
                    journal_root=journal,
                    fault_injector=crash_restore,
                )
            self.assertEqual(claude_path.read_bytes(), original)
            self.assertEqual(codex_path.read_bytes(), _CODEX_ORIGINAL)
            self.assertTrue((journal / "manifest.json").is_file())

            host_config._recover_if_needed(claude_path, codex_path, journal)
            host_config._recover_if_needed(claude_path, codex_path, journal)

            self.assertFalse(journal.exists())
            self.assertEqual(claude_path.read_bytes(), original)
            self.assertEqual(codex_path.read_bytes(), _CODEX_ORIGINAL)


class ManifestNextOnlyTest(unittest.TestCase):
    def test_crash_during_first_publish_is_discarded_and_two_runs_commit(self) -> None:
        original, _target = _claude_pair()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude_path = root / ".claude.json"
            codex_path = root / "config.toml"
            journal = root / "journal"
            claude_path.write_bytes(original)
            codex_path.write_bytes(_CODEX_ORIGINAL)
            real_replace = os.replace
            armed = True

            def crash_first_replace(src: str, dst: str) -> None:
                nonlocal armed
                if armed and Path(src).name == "manifest.next" and not Path(dst).exists():
                    armed = False
                    raise HostConfigCrash()
                real_replace(src, dst)

            with mock.patch.object(host_config.os, "replace", crash_first_replace):
                with self.assertRaises(HostConfigCrash):
                    apply_host_timeouts(
                        claude_path,
                        codex_path,
                        journal_root=journal,
                    )
            self.assertTrue((journal / "manifest.next").is_file())
            self.assertFalse((journal / "manifest.json").exists())
            self.assertEqual(claude_path.read_bytes(), original)
            self.assertEqual(codex_path.read_bytes(), _CODEX_ORIGINAL)

            apply_host_timeouts(claude_path, codex_path, journal_root=journal)
            apply_host_timeouts(claude_path, codex_path, journal_root=journal)
            self.assertFalse(journal.exists())

    def test_next_only_journal_is_not_discarded_when_a_host_file_changed(self) -> None:
        original, _target = _claude_pair()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude_path = root / ".claude.json"
            codex_path = root / "config.toml"
            journal = root / "journal"
            claude_path.write_bytes(original)
            codex_path.write_bytes(_CODEX_ORIGINAL)
            real_replace = os.replace
            armed = True

            def crash_first_replace(src: str, dst: str) -> None:
                nonlocal armed
                if armed and Path(src).name == "manifest.next" and not Path(dst).exists():
                    armed = False
                    raise HostConfigCrash()
                real_replace(src, dst)

            with mock.patch.object(host_config.os, "replace", crash_first_replace):
                with self.assertRaises(HostConfigCrash):
                    apply_host_timeouts(
                        claude_path,
                        codex_path,
                        journal_root=journal,
                    )
            external = _CODEX_ORIGINAL + b"# external\n"
            codex_path.write_bytes(external)
            with self.assertRaisesRegex(HostConfigError, "^registration_recovery_conflict$"):
                apply_host_timeouts(claude_path, codex_path, journal_root=journal)
            self.assertEqual(claude_path.read_bytes(), original)
            self.assertEqual(codex_path.read_bytes(), external)
            self.assertTrue(journal.exists())


class RegistrationJournalTest(unittest.TestCase):
    def test_kill_between_adds_is_repaired_by_the_next_run(self) -> None:
        previous = {
            "CLAUDE": RegistrationSpec(Path(r"C:\old\python.exe"), ("-m", "old")),
            "CODEX": RegistrationSpec(Path(r"C:\old\python.exe"), ("-m", "old")),
        }
        desired = {
            "CLAUDE": RegistrationSpec(
                Path(r"C:\new\python.exe"),
                ("-m", "dayz_mcp", "--client-platform", "claude"),
            ),
            "CODEX": RegistrationSpec(
                Path(r"C:\new\python.exe"),
                ("-m", "dayz_mcp", "--client-platform", "codex"),
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "registration-transaction"
            provider = _FakeProvider(previous)

            def kill(phase: str) -> None:
                if phase == "after_add_CLAUDE":
                    raise RegistrationCrash()

            with self.assertRaises(RegistrationCrash):
                register_transaction(
                    provider,
                    desired,
                    journal_root=journal,
                    fault_injector=kill,
                )
            self.assertEqual(provider.states["CLAUDE"], desired["CLAUDE"])
            self.assertIsNone(provider.states["CODEX"])
            self.assertTrue((_keyed_journal(journal) / "manifest.json").is_file())

            register_transaction(provider, desired, journal_root=journal)
            self.assertEqual(provider.states, desired)
            self.assertFalse(_keyed_journal(journal).exists())

    def test_other_instance_does_not_replay_or_delete_the_journal(self) -> None:
        def specs(old: str, new: str, supervised: bool) -> tuple[dict, dict]:
            flags = ("-m", "dayz_mcp", "--client")
            if supervised:
                flags = flags + ("--supervised",)
            previous = {
                role: RegistrationSpec(Path(old), flags)
                for role in ("CLAUDE", "CODEX")
            }
            desired = {
                "CLAUDE": RegistrationSpec(Path(new), flags + ("--client-platform", "claude")),
                "CODEX": RegistrationSpec(Path(new), flags + ("--client-platform", "codex")),
            }
            return previous, desired

        alpha_previous, alpha_desired = specs(
            r"C:\A-old\python.exe", r"C:\A-new\python.exe", True
        )
        beta_previous, beta_desired = specs(
            r"C:\B-old\python.exe", r"C:\B-new\python.exe", False
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "registration-transaction"
            alpha = _FakeProvider(alpha_previous)
            beta = _FakeProvider(beta_previous)

            def kill(phase: str) -> None:
                if phase == "after_add_CLAUDE":
                    raise RegistrationCrash()

            with self.assertRaises(RegistrationCrash):
                register_transaction(
                    alpha,
                    alpha_desired,
                    server_name="dayz-mcp-alpha",
                    journal_root=journal,
                    fault_injector=kill,
                )
            alpha_journal = _keyed_journal(journal, "dayz-mcp-alpha")
            alpha_bytes = (alpha_journal / "manifest.json").read_bytes()
            register_transaction(
                beta,
                beta_desired,
                server_name="dayz-mcp-beta",
                journal_root=journal,
            )
            register_transaction(
                beta,
                beta_desired,
                server_name="dayz-mcp-beta",
                journal_root=journal,
            )
            self.assertEqual(beta.states, beta_desired)
            self.assertEqual((alpha_journal / "manifest.json").read_bytes(), alpha_bytes)
            self.assertIsNone(alpha.states["CODEX"])

            register_transaction(
                alpha,
                alpha_desired,
                server_name="dayz-mcp-alpha",
                journal_root=journal,
            )
            self.assertEqual(alpha.states, alpha_desired)
            self.assertFalse(alpha_journal.exists())

    def test_truncated_first_manifest_does_not_wedge_the_next_runs(self) -> None:
        previous = {
            "CLAUDE": None,
            "CODEX": None,
        }
        desired = {
            "CLAUDE": RegistrationSpec(Path(r"C:\new\python.exe"), ("-m", "claude")),
            "CODEX": RegistrationSpec(Path(r"C:\new\python.exe"), ("-m", "codex")),
        }
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "registration-transaction"
            fragment = _keyed_journal(journal)
            fragment.mkdir(parents=True)
            (fragment / "manifest.next").write_bytes(b'{"previous":')
            provider = _FakeProvider(previous)
            register_transaction(provider, desired, journal_root=journal)
            register_transaction(provider, desired, journal_root=journal)
            self.assertEqual(provider.states, desired)
            self.assertFalse(fragment.exists())

    def test_crash_during_first_manifest_write_is_not_an_active_journal(self) -> None:
        previous = {"CLAUDE": None, "CODEX": None}
        desired = {
            "CLAUDE": RegistrationSpec(Path(r"C:\new\python.exe"), ("-m", "claude")),
            "CODEX": RegistrationSpec(Path(r"C:\new\python.exe"), ("-m", "codex")),
        }
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "registration-transaction"
            provider = _FakeProvider(previous)

            def partial_write(path: Path, _value: bytes) -> None:
                path.write_bytes(b'{"previous":')
                raise RegistrationCrash()

            with mock.patch("dayz_mcp.host_config._write_private", partial_write):
                with self.assertRaises(RegistrationCrash):
                    register_transaction(provider, desired, journal_root=journal)
            self.assertEqual(provider.states, previous)
            self.assertFalse((_keyed_journal(journal) / "manifest.json").exists())

            register_transaction(provider, desired, journal_root=journal)
            register_transaction(provider, desired, journal_root=journal)
            self.assertEqual(provider.states, desired)
            self.assertFalse(_keyed_journal(journal).exists())


def _command_change(old: str, new: str, arguments: tuple[str, ...]) -> tuple[dict, dict]:
    previous = {
        role: RegistrationSpec(Path(old), arguments) for role in ("CLAUDE", "CODEX")
    }
    desired = {
        role: RegistrationSpec(Path(new), arguments) for role in ("CLAUDE", "CODEX")
    }
    return previous, desired


class _SuspendOnFirstGet(_FakeProvider):
    def __init__(
        self,
        states: dict[str, RegistrationSpec | None],
        entered: threading.Event,
        release: threading.Event,
    ) -> None:
        super().__init__(states)
        self._entered = entered
        self._release = release
        self._parked = False

    def get(self, role: str) -> RegistrationSpec | None:
        if role == "CLAUDE" and not self._parked:
            self._parked = True
            self._entered.set()
            if not self._release.wait(10):
                raise TimeoutError("registration lock holder was not released")
        return super().get(role)


class RegistrationLockIsolationTest(unittest.TestCase):
    def test_f4_contender_cannot_overwrite_the_held_journal(self) -> None:
        alpha_previous, alpha_desired = _command_change(
            r"C:\A-old\python.exe",
            r"C:\A-new\python.exe",
            ("-m", "dayz_mcp", "--client", "--supervised"),
        )
        beta_previous, beta_desired = _command_change(
            r"C:\B-old\python.exe",
            r"C:\B-new\python.exe",
            ("-m", "dayz_mcp", "--client"),
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "registration-transaction"
            alpha = _FakeProvider(alpha_previous)

            def kill(phase: str) -> None:
                if phase == "after_add_CLAUDE":
                    raise RegistrationCrash()

            with self.assertRaises(RegistrationCrash):
                register_transaction(
                    alpha,
                    alpha_desired,
                    server_name="dayz-mcp-alpha",
                    journal_root=journal,
                    fault_injector=kill,
                )
            alpha_journal = _keyed_journal(journal, "dayz-mcp-alpha")
            alpha_bytes = (alpha_journal / "manifest.json").read_bytes()
            entered = threading.Event()
            release = threading.Event()
            beta = _SuspendOnFirstGet(beta_previous, entered, release)
            errors: list[BaseException] = []

            def run_beta() -> None:
                try:
                    register_transaction(
                        beta,
                        beta_desired,
                        server_name="dayz-mcp-beta",
                        journal_root=journal,
                    )
                except BaseException as error:
                    errors.append(error)

            worker = threading.Thread(target=run_beta)
            worker.start()
            self.assertTrue(entered.wait(10))
            calls_before = alpha.calls
            with self.assertRaisesRegex(
                RegistrationTransactionError, "^registration_busy$"
            ):
                register_transaction(
                    alpha,
                    alpha_desired,
                    server_name="dayz-mcp-alpha",
                    journal_root=journal,
                    lock_timeout_s=0.2,
                    fault_injector=kill,
                )
            self.assertEqual(alpha.calls, calls_before)
            self.assertEqual((alpha_journal / "manifest.json").read_bytes(), alpha_bytes)
            self.assertIsNone(alpha.states["CODEX"])
            release.set()
            worker.join(10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(beta.states, beta_desired)
            self.assertEqual((alpha_journal / "manifest.json").read_bytes(), alpha_bytes)

            register_transaction(
                alpha,
                alpha_desired,
                server_name="dayz-mcp-alpha",
                journal_root=journal,
            )
            self.assertEqual(alpha.states, alpha_desired)
            self.assertFalse(alpha_journal.exists())

    def test_f1_other_instance_leaves_the_keyed_journal_byte_identical(self) -> None:
        alpha_previous, alpha_desired = _command_change(
            r"C:\A-old\python.exe",
            r"C:\A-new\python.exe",
            ("-m", "dayz_mcp", "--client", "--supervised"),
        )
        beta_previous, beta_desired = _command_change(
            r"C:\B-old\python.exe",
            r"C:\B-new\python.exe",
            ("-m", "dayz_mcp", "--client"),
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "registration-transaction"
            alpha = _FakeProvider(alpha_previous)
            beta = _FakeProvider(beta_previous)

            def kill(phase: str) -> None:
                if phase == "after_add_CLAUDE":
                    raise RegistrationCrash()

            with self.assertRaises(RegistrationCrash):
                register_transaction(
                    alpha,
                    alpha_desired,
                    server_name="dayz-mcp-alpha",
                    journal_root=journal,
                    fault_injector=kill,
                )
            alpha_journal = _keyed_journal(journal, "dayz-mcp-alpha")
            alpha_bytes = (alpha_journal / "manifest.json").read_bytes()
            register_transaction(
                beta,
                beta_desired,
                server_name="dayz-mcp-beta",
                journal_root=journal,
            )
            register_transaction(
                beta,
                beta_desired,
                server_name="dayz-mcp-beta",
                journal_root=journal,
            )
            self.assertEqual(beta.states, beta_desired)
            self.assertEqual((alpha_journal / "manifest.json").read_bytes(), alpha_bytes)
            self.assertFalse(_keyed_journal(journal, "dayz-mcp-beta").exists())
            self.assertIsNone(alpha.states["CODEX"])

            register_transaction(
                alpha,
                alpha_desired,
                server_name="dayz-mcp-alpha",
                journal_root=journal,
            )
            self.assertEqual(alpha.states, alpha_desired)
            self.assertFalse(alpha_journal.exists())

    def test_short_lock_limit_is_registration_busy_with_no_provider_calls(self) -> None:
        previous, desired = _command_change(
            r"C:\A-old\python.exe",
            r"C:\A-new\python.exe",
            ("-m", "dayz_mcp", "--client", "--supervised"),
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "registration-transaction"
            entered = threading.Event()
            release = threading.Event()
            holder = _SuspendOnFirstGet(dict(previous), entered, release)
            contender = _FakeProvider(dict(previous))
            errors: list[BaseException] = []

            def run_holder() -> None:
                try:
                    register_transaction(
                        holder,
                        desired,
                        server_name="dayz-mcp-alpha",
                        journal_root=journal,
                    )
                except BaseException as error:
                    errors.append(error)

            worker = threading.Thread(target=run_holder)
            worker.start()
            self.assertTrue(entered.wait(10))
            with self.assertRaisesRegex(
                RegistrationTransactionError, "^registration_busy$"
            ):
                register_transaction(
                    contender,
                    desired,
                    server_name="dayz-mcp-beta",
                    journal_root=journal,
                    lock_timeout_s=0.2,
                )
            self.assertEqual(contender.calls, 0)
            self.assertFalse(_keyed_journal(journal, "dayz-mcp-beta").exists())
            release.set()
            worker.join(10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])

    def test_held_lock_file_cannot_be_deleted(self) -> None:
        previous, desired = _command_change(
            r"C:\B-old\python.exe",
            r"C:\B-new\python.exe",
            ("-m", "dayz_mcp", "--client"),
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "registration-transaction"
            entered = threading.Event()
            release = threading.Event()
            holder = _SuspendOnFirstGet(previous, entered, release)
            errors: list[BaseException] = []

            def run_holder() -> None:
                try:
                    register_transaction(
                        holder,
                        desired,
                        server_name="dayz-mcp-beta",
                        journal_root=journal,
                    )
                except BaseException as error:
                    errors.append(error)

            worker = threading.Thread(target=run_holder)
            worker.start()
            self.assertTrue(entered.wait(10))
            lock_path = journal.parent / f"{journal.name}.lock"
            self.assertTrue(lock_path.is_file())
            with self.assertRaises(OSError):
                lock_path.unlink()
            self.assertTrue(lock_path.is_file())
            release.set()
            worker.join(10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])

    def test_manifest_server_name_must_match_its_directory(self) -> None:
        previous, desired = _command_change(
            r"C:\B-old\python.exe",
            r"C:\B-new\python.exe",
            ("-m", "dayz_mcp", "--client"),
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "registration-transaction"
            provider = _FakeProvider(previous)

            def kill(phase: str) -> None:
                if phase == "after_add_CLAUDE":
                    raise RegistrationCrash()

            with self.assertRaises(RegistrationCrash):
                register_transaction(
                    provider,
                    desired,
                    server_name="dayz-mcp-beta",
                    journal_root=journal,
                    fault_injector=kill,
                )
            manifest = _keyed_journal(journal, "dayz-mcp-beta") / "manifest.json"
            payload = json.loads(manifest.read_bytes())
            payload["server_name"] = "dayz-mcp-alpha"
            encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
            manifest.write_bytes(encoded)
            frozen = dict(provider.states)
            with self.assertRaisesRegex(
                RegistrationTransactionError, "^registration_journal_owner_mismatch$"
            ):
                register_transaction(
                    provider,
                    desired,
                    server_name="dayz-mcp-beta",
                    journal_root=journal,
                )
            self.assertEqual(provider.states, frozen)
            self.assertEqual(manifest.read_bytes(), encoded)


class _ParsingProvider:
    def __init__(
        self,
        states: dict[str, RegistrationSpec | None],
        claude_path: Path,
        codex_path: Path,
    ) -> None:
        self.states = dict(states)
        self.claude_path = claude_path
        self.codex_path = codex_path

    def get(self, role: str) -> RegistrationSpec | None:
        if role == "CLAUDE":
            json.loads(self.claude_path.read_bytes())
        else:
            tomllib.loads(self.codex_path.read_text(encoding="utf-8"))
        return self.states[role]

    def remove(self, role: str) -> None:
        self.states[role] = None

    def add(self, role: str, spec: RegistrationSpec) -> None:
        self.states[role] = spec


class HostTimeoutRecoveryOrderTest(unittest.TestCase):
    def test_torn_timeout_write_is_recovered_before_provider_probes(self) -> None:
        payload = {
            "mcpServers": {
                "dayz-mcp": {
                    "args": ["-m", "dayz_mcp"],
                    "command": "p" * 39,
                }
            }
        }
        original = json.dumps(payload, indent=2).encode("utf-8")
        target = host_config.build_claude_target(original)
        self.assertEqual(len(original), 166)
        self.assertEqual(len(target), 195)
        codex = _CODEX_ORIGINAL
        desired = {
            "CLAUDE": RegistrationSpec(Path(r"C:\new\python.exe"), ("-m", "claude")),
            "CODEX": RegistrationSpec(Path(r"C:\new\python.exe"), ("-m", "codex")),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude_path = root / ".claude.json"
            codex_path = root / "config.toml"
            claude_path.write_bytes(original)
            codex_path.write_bytes(codex)
            journal = root / "registration-transaction"
            host_journal = root / "host-journal"
            provider = _ParsingProvider(
                {"CLAUDE": None, "CODEX": None}, claude_path, codex_path
            )

            def tear(phase: str) -> None:
                if phase == "after_write_claude":
                    raise HostConfigCrash()

            with self.assertRaises(HostConfigCrash):
                register_transaction(
                    provider,
                    desired,
                    host_configs=(claude_path, codex_path),
                    journal_root=journal,
                    host_journal_root=host_journal,
                    fault_injector=tear,
                )
            written = claude_path.read_bytes()
            self.assertEqual(written, target)
            torn = target[:161] + original[161:]
            claude_path.write_bytes(torn)
            self.assertEqual(claude_path.read_bytes(), torn)
            with self.assertRaises(json.JSONDecodeError):
                json.loads(torn)

            register_transaction(
                provider,
                desired,
                host_configs=(claude_path, codex_path),
                journal_root=journal,
                host_journal_root=host_journal,
            )
            register_transaction(
                provider,
                desired,
                host_configs=(claude_path, codex_path),
                journal_root=journal,
                host_journal_root=host_journal,
            )
            self.assertEqual(provider.states, desired)
            json.loads(claude_path.read_bytes())
            self.assertFalse(_keyed_journal(journal).exists())
            self.assertFalse(host_journal.exists())


class PowershellRegistrationRollbackTest(unittest.TestCase):
    def test_validate_seam_rolls_back_claude_before_the_codex_error(self) -> None:
        # The PowerShell rollback seam is gone: -Register delegates to
        # install_mcp.py, whose register_transaction owns recovery. The old
        # switch refuses by name and is not executed here.
        script = (TOOLS_DIR / "install-mcp.ps1").read_text(encoding="utf-8")
        self.assertNotIn("function Undo-DayZMcpClaudeRegistration", script)
        self.assertNotIn("registration_rollback_ok", script)
        self.assertIn("registration_rollback_seam_removed", script)
        self.assertIn("install_mcp.py", script)


if __name__ == "__main__":
    unittest.main()
