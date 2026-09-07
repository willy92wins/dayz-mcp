from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import identity_migration
from dayz_mcp.identity_migration import (
    RunsBackupGateError,
    daemon_startup_election,
    ensure_runs_v1_backup,
    scan_dayz_mcp_processes,
)
from dayz_mcp.daemon import DAEMON_STARTUP_BUDGET_S, build_daemon_argv
from dayz_mcp.host_config import CLAUDE_TIMEOUT_MS, CODEX_TIMEOUT_SECONDS
from dayz_mcp.runtime_state import RuntimePaths
from tests._tree_identity import (
    TreeIdentityError,
    assert_same_checkout,
    checkout_root,
    editable_mapped_tools_dir,
)


DAEMON_FIXTURE_SITE = Path(__file__).resolve().parent / "fixtures" / "dayz_mcp"


class SimulatedCrash(BaseException):
    pass


def runtime_paths(root: Path) -> RuntimePaths:
    runtime = root / "runtime"
    return RuntimePaths(
        runtime,
        runtime / "audit",
        runtime / "coordination.json",
        runtime / "runs.json",
    )


def unused_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


def communicate_owned_fixture(
    child: subprocess.Popen[str], timeout: float
) -> tuple[str, str]:
    try:
        return child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate(timeout=5.0)
        raise


def fixture_daemon_argv(port: int, keyfile: Path) -> list[str]:
    return build_daemon_argv(
        SimpleNamespace(
            port=port,
            keyfile=str(keyfile),
            idle_timeout_s=0.5,
            expected_game_version=None,
            require_version=False,
            enable_exec_enforce=False,
        ),
        python=sys.executable,
    )


def fixture_daemon_cwd() -> str:
    """Return the cwd a fixture-spawned daemon will accredit.

    Sitecustomize rebinds ``dayz_mcp`` onto this checkout before the daemon
    starts, and run_daemon accredits ``daemon_runtime_cwd()`` of that import.
    Spawning with a different cwd lets unauthenticated /status answer while
    accredited startup never completes, so the idle watchdog is never armed
    and communicate() times out. The tools directory next to this test is
    that accredited path; the editable-install mapping is a different tree.
    """
    return str(Path(__file__).resolve().parents[1])


def write_client_host_fixture(home: Path, keyfile: Path) -> None:
    launcher = str(Path(sys.executable).resolve(strict=True))

    def client_args(platform: str) -> list[str]:
        return [
            "-m",
            "dayz_mcp",
            "--client",
            "--keyfile",
            str(keyfile.resolve()),
            "--port",
            "8765",
            "--idle-timeout",
            "1800",
            "--client-platform",
            platform,
        ]

    home.mkdir(parents=True, exist_ok=True)
    (home / ".codex").mkdir()
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "dayz-mcp": {
                        "type": "stdio",
                        "command": launcher,
                        "args": client_args("claude"),
                        "timeout": CLAUDE_TIMEOUT_MS,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (home / ".codex" / "config.toml").write_text(
        "\n".join(
            (
                "[mcp_servers.dayz-mcp]",
                f"command = {json.dumps(launcher)}",
                f"args = {json.dumps(client_args('codex'))}",
                f"tool_timeout_sec = {CODEX_TIMEOUT_SECONDS}",
                "",
            )
        ),
        encoding="utf-8",
    )


def fixture_environment(
    base: dict[str, str],
    *,
    mode: str,
    signal: Path,
    migration: Path,
    start: Path | None = None,
    fixture_pids_path: Path | None = None,
) -> dict[str, str]:
    environment = dict(base)
    inherited_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(DAEMON_FIXTURE_SITE)
    if inherited_pythonpath:
        environment["PYTHONPATH"] += os.pathsep + inherited_pythonpath
    environment["DAYZ_MCP_FIXTURE_MODE"] = mode
    environment["DAYZ_MCP_FIXTURE_SIGNAL"] = str(signal)
    environment["DAYZ_MCP_FIXTURE_MIGRATION"] = str(migration)
    mapped = editable_mapped_tools_dir()
    if mapped is not None:
        environment["DAYZ_MCP_HOST_TOOLS"] = str(mapped)
    if start is not None:
        environment["DAYZ_MCP_FIXTURE_START"] = str(start)
    if fixture_pids_path is not None:
        environment["DAYZ_MCP_FIXTURE_PIDS"] = str(fixture_pids_path)
    return environment


class BackupTransactionAdversarialTest(unittest.TestCase):
    def run_gate(
        self,
        paths: RuntimePaths,
        migration: Path,
        **kwargs: object,
    ) -> dict[str, object]:
        return ensure_runs_v1_backup(
            paths,
            8765,
            migration_dir=migration,
            scan_fn=lambda _allowed: (),
            listener_fn=lambda _port: False,
            **kwargs,
        )

    def test_marker_drift_between_temp_preparation_and_publish_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = runtime_paths(root)
            paths.root.mkdir(parents=True)
            paths.runs_path.write_bytes(b"source")
            migration = root / "migration"
            external = b"external-marker-bytes"

            def inject(phase: str) -> None:
                if phase == "marker_before_rename":
                    (migration / "runs-backup-transaction.json").write_bytes(external)

            with self.assertRaisesRegex(
                RunsBackupGateError, "runs_backup_transaction_conflict"
            ):
                self.run_gate(paths, migration, fault_injector=inject)

            self.assertEqual(
                (migration / "runs-backup-transaction.json").read_bytes(), external
            )
            self.assertTrue((migration / "runs-backup-transaction.next").is_file())
            self.assertFalse((migration / "runs.pre-v2.json").exists())
            self.assertFalse((migration / "runs-backup-receipt.json").exists())

    def test_marker_publish_cannot_clobber_writer_inside_os_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = runtime_paths(root)
            paths.root.mkdir(parents=True)
            paths.runs_path.write_bytes(b"source")
            migration = root / "migration"
            marker = migration / "runs-backup-transaction.json"
            external = b"external-marker-inside-publish"
            original_replace = os.replace
            original_rename = os.rename
            raced = False

            def boundary(operation: object):
                def race(source: object, destination: object) -> None:
                    nonlocal raced
                    if Path(destination) == marker and not raced:
                        raced = True
                        marker.write_bytes(external)
                    operation(source, destination)  # type: ignore[operator]

                return race

            with (
                patch.object(
                    identity_migration.os,
                    "replace",
                    side_effect=boundary(original_replace),
                ),
                patch.object(
                    identity_migration.os,
                    "rename",
                    side_effect=boundary(original_rename),
                ),
                self.assertRaisesRegex(
                    RunsBackupGateError, "runs_backup_transaction_conflict"
                ),
            ):
                self.run_gate(paths, migration)

            self.assertTrue(raced)
            self.assertEqual(marker.read_bytes(), external)
            self.assertFalse((migration / "runs.pre-v2.json").exists())
            self.assertFalse((migration / "runs-backup-receipt.json").exists())

    def test_valid_receipt_never_authorizes_cleanup_of_external_auxiliary(self) -> None:
        names = (
            "runs-backup-transaction.json",
            "runs-backup-transaction.next",
            "runs-backup-receipt.pending",
        )
        for name in names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                paths = runtime_paths(root)
                paths.root.mkdir(parents=True)
                paths.runs_path.write_bytes(b"source")
                migration = root / "migration"
                self.run_gate(paths, migration)
                external = migration / name
                external.write_bytes(b"external-auxiliary")

                with self.assertRaises(RunsBackupGateError):
                    self.run_gate(paths, migration)

                self.assertEqual(external.read_bytes(), b"external-auxiliary")

    def test_partial_owned_backup_is_rolled_back_but_external_drift_is_not(self) -> None:
        for payload, recoverable in ((b"sou", True), (b"foreign", False)):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                paths = runtime_paths(root)
                paths.root.mkdir(parents=True)
                paths.runs_path.write_bytes(b"source")
                migration = root / "migration"

                def inject(phase: str) -> None:
                    if phase == "after_marker_prepared":
                        (migration / "runs.pre-v2.json").write_bytes(payload)
                        raise SimulatedCrash()

                with self.assertRaises(SimulatedCrash):
                    self.run_gate(paths, migration, fault_injector=inject)

                if recoverable:
                    receipt = self.run_gate(paths, migration)
                    self.assertEqual(receipt["source"], receipt["backup"])
                    self.assertEqual(
                        (migration / "runs.pre-v2.json").read_bytes(), b"source"
                    )
                else:
                    with self.assertRaisesRegex(
                        RunsBackupGateError, "runs_backup_recovery_conflict"
                    ):
                        self.run_gate(paths, migration)
                    self.assertEqual(
                        (migration / "runs.pre-v2.json").read_bytes(), b"foreign"
                    )
                    self.assertTrue(
                        (migration / "runs-backup-transaction.json").is_file()
                    )

    def test_legacy_mutable_marker_revisions_remain_recoverable(self) -> None:
        cases = (
            ("prepared", "backup_written"),
            ("backup_written", "receipt_pending"),
            ("receipt_pending", None),
        )
        for phase, next_phase in cases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                paths = runtime_paths(root)
                paths.root.mkdir(parents=True)
                paths.runs_path.write_bytes(b"source")
                migration = root / "migration"
                marker_path = migration / "runs-backup-transaction.json"
                next_path = migration / "runs-backup-transaction.next"

                def crash_after_initial_marker(actual: str) -> None:
                    if actual == "after_marker_prepared":
                        raise SimulatedCrash()

                with self.assertRaises(SimulatedCrash):
                    self.run_gate(
                        paths,
                        migration,
                        fault_injector=crash_after_initial_marker,
                    )

                initial = json.loads(marker_path.read_bytes())
                initial_encoded = marker_path.read_bytes()
                backup_written = dict(initial)
                backup_written["revision"] = 2
                backup_written["previous_sha256"] = identity_migration._sha256(
                    initial_encoded
                )
                backup_written["phase"] = "backup_written"
                backup_encoded = identity_migration._encoded_json(backup_written)
                receipt_pending = dict(backup_written)
                receipt_pending["revision"] = 3
                receipt_pending["previous_sha256"] = identity_migration._sha256(
                    backup_encoded
                )
                receipt_pending["phase"] = "receipt_pending"
                receipt_encoded = identity_migration._encoded_json(receipt_pending)
                encodings = {
                    "prepared": initial_encoded,
                    "backup_written": backup_encoded,
                    "receipt_pending": receipt_encoded,
                }
                marker_path.write_bytes(encodings[phase])
                if next_phase is not None:
                    candidate = encodings[next_phase]
                    next_path.write_bytes(candidate[: max(1, len(candidate) // 2)])

                receipt = self.run_gate(paths, migration)

                self.assertEqual(receipt["source"], receipt["backup"])
                self.assertFalse(marker_path.exists())
                self.assertFalse(next_path.exists())

    def test_rev1_marker_does_not_authorize_current_revision_prefix_as_next(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = runtime_paths(root)
            paths.root.mkdir(parents=True)
            paths.runs_path.write_bytes(b"source")
            migration = root / "migration"
            marker_path = migration / "runs-backup-transaction.json"
            next_path = migration / "runs-backup-transaction.next"

            def crash_after_initial_marker(actual: str) -> None:
                if actual == "after_marker_prepared":
                    raise SimulatedCrash()

            with self.assertRaises(SimulatedCrash):
                self.run_gate(
                    paths,
                    migration,
                    fault_injector=crash_after_initial_marker,
                )

            marker_encoded = marker_path.read_bytes()
            marker = json.loads(marker_encoded)
            revision_two = dict(marker)
            revision_two["revision"] = 2
            revision_two["previous_sha256"] = identity_migration._sha256(
                marker_encoded
            )
            revision_two["phase"] = "backup_written"
            revision_two_encoded = identity_migration._encoded_json(revision_two)
            difference = next(
                index
                for index, (left, right) in enumerate(
                    zip(marker_encoded, revision_two_encoded, strict=False)
                )
                if left != right
            )
            external = marker_encoded[: difference + 1]
            self.assertTrue(marker_encoded.startswith(external))
            self.assertFalse(revision_two_encoded.startswith(external))
            next_path.write_bytes(external)

            with self.assertRaisesRegex(
                RunsBackupGateError, "runs_backup_recovery_conflict"
            ):
                self.run_gate(paths, migration)

            self.assertEqual(marker_path.read_bytes(), marker_encoded)
            self.assertEqual(next_path.read_bytes(), external)
            self.assertFalse((migration / "runs.pre-v2.json").exists())
            self.assertFalse((migration / "runs-backup-receipt.json").exists())

    def test_receipt_rejects_duplicate_keys_at_top_and_nested_levels(self) -> None:
        transforms = (
            lambda document: document.replace(
                "{\n", '{\n  "kind": "external-wrong",\n', 1
            ),
            lambda document: document.replace(
                '  "source": {\n', '  "source": {\n    "bytes": -1,\n', 1
            ),
        )
        for transform in transforms:
            with self.subTest(transform=transform), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                paths = runtime_paths(root)
                paths.root.mkdir(parents=True)
                paths.runs_path.write_bytes(b"source")
                migration = root / "migration"
                receipt_path = migration / "runs-backup-receipt.json"
                self.run_gate(paths, migration)
                duplicate = transform(receipt_path.read_text(encoding="utf-8"))
                receipt_path.write_text(duplicate, encoding="utf-8")

                with self.assertRaisesRegex(
                    RunsBackupGateError, "invalid_runs_backup_receipt"
                ):
                    self.run_gate(paths, migration)

                self.assertEqual(receipt_path.read_text(encoding="utf-8"), duplicate)

    def test_marker_rejects_duplicate_keys_at_top_and_nested_levels(self) -> None:
        transforms = (
            lambda document: document.replace(
                "{\n", '{\n  "revision": 99,\n', 1
            ),
            lambda document: document.replace(
                '  "source": {\n', '  "source": {\n    "bytes": -1,\n', 1
            ),
        )
        for transform in transforms:
            with self.subTest(transform=transform), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                paths = runtime_paths(root)
                paths.root.mkdir(parents=True)
                paths.runs_path.write_bytes(b"source")
                migration = root / "migration"
                marker_path = migration / "runs-backup-transaction.json"

                def crash_after_initial_marker(actual: str) -> None:
                    if actual == "after_marker_prepared":
                        raise SimulatedCrash()

                with self.assertRaises(SimulatedCrash):
                    self.run_gate(
                        paths,
                        migration,
                        fault_injector=crash_after_initial_marker,
                    )
                duplicate = transform(marker_path.read_text(encoding="utf-8"))
                marker_path.write_text(duplicate, encoding="utf-8")

                with self.assertRaisesRegex(
                    RunsBackupGateError, "invalid_runs_backup_transaction"
                ):
                    self.run_gate(paths, migration)

                self.assertEqual(marker_path.read_text(encoding="utf-8"), duplicate)
                self.assertFalse((migration / "runs.pre-v2.json").exists())

    def test_partial_initial_marker_temp_is_safe_to_discard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = runtime_paths(root)
            paths.root.mkdir(parents=True)
            paths.runs_path.write_bytes(b"source")
            migration = root / "migration"

            def inject(phase: str) -> None:
                if phase == "marker_before_rename":
                    next_path = migration / "runs-backup-transaction.next"
                    next_path.write_bytes(next_path.read_bytes()[:17])
                    raise SimulatedCrash()

            with self.assertRaises(SimulatedCrash):
                self.run_gate(paths, migration, fault_injector=inject)

            receipt = self.run_gate(paths, migration)
            self.assertEqual(receipt["source"], receipt["backup"])
            self.assertFalse((migration / "runs-backup-transaction.next").exists())

    def test_zero_progress_fsync_and_rename_failures_are_reentrant(self) -> None:
        failures = ("write", "fsync", "rename")
        for failure in failures:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                paths = runtime_paths(root)
                paths.root.mkdir(parents=True)
                paths.runs_path.write_bytes(b"source")
                migration = root / "migration"
                migration.mkdir()
                (migration / ".runs-v1.lock").write_bytes(b"\0")

                if failure == "write":
                    context = patch.object(identity_migration.os, "write", return_value=0)
                elif failure == "fsync":
                    context = patch.object(
                        identity_migration.os,
                        "fsync",
                        side_effect=OSError("fixture-fsync"),
                    )
                else:
                    context = patch.object(
                        identity_migration.os,
                        "rename",
                        side_effect=OSError("fixture-rename"),
                    )
                with context, self.assertRaises(RunsBackupGateError):
                    self.run_gate(paths, migration)

                receipt = self.run_gate(paths, migration)
                self.assertEqual(receipt["source"], receipt["backup"])
                self.assertFalse(
                    (migration / "runs-backup-transaction.next").exists()
                )

    def test_recovery_detects_marker_swap_before_pinned_delete_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = runtime_paths(root)
            paths.root.mkdir(parents=True)
            paths.runs_path.write_bytes(b"source")
            migration = root / "migration"
            marker = migration / "runs-backup-transaction.json"
            displaced = migration / "owned-marker.displaced"
            external = b"external-marker-before-delete"

            def crash_after_receipt(phase: str) -> None:
                if phase == "after_receipt_publish":
                    raise SimulatedCrash()

            with self.assertRaises(SimulatedCrash):
                self.run_gate(
                    paths,
                    migration,
                    fault_injector=crash_after_receipt,
                )

            swapped = False

            def swap_before_pin(phase: str) -> None:
                nonlocal swapped
                if phase == "marker_before_unlink_open" and not swapped:
                    swapped = True
                    os.rename(marker, displaced)
                    marker.write_bytes(external)

            with self.assertRaisesRegex(
                RunsBackupGateError, "runs_backup_recovery_conflict"
            ):
                self.run_gate(
                    paths,
                    migration,
                    fault_injector=swap_before_pin,
                )

            self.assertTrue(swapped)
            self.assertEqual(marker.read_bytes(), external)
            self.assertTrue(displaced.is_file())

    def test_recovery_pins_marker_against_swap_until_delete_on_close(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = runtime_paths(root)
            paths.root.mkdir(parents=True)
            paths.runs_path.write_bytes(b"source")
            migration = root / "migration"
            marker = migration / "runs-backup-transaction.json"
            displaced = migration / "owned-marker.displaced"

            def crash_after_receipt(phase: str) -> None:
                if phase == "after_receipt_publish":
                    raise SimulatedCrash()

            with self.assertRaises(SimulatedCrash):
                self.run_gate(
                    paths,
                    migration,
                    fault_injector=crash_after_receipt,
                )

            attempted = False
            blocked = False

            def swap_while_pinned(phase: str) -> None:
                nonlocal attempted, blocked
                if phase == "marker_unlink_pinned":
                    attempted = True
                    try:
                        os.rename(marker, displaced)
                    except OSError:
                        blocked = True

            receipt = self.run_gate(
                paths,
                migration,
                fault_injector=swap_while_pinned,
            )

            self.assertEqual(receipt["source"], receipt["backup"])
            self.assertTrue(attempted)
            self.assertTrue(blocked)
            self.assertFalse(marker.exists())
            self.assertFalse(displaced.exists())

    def test_lock_initialization_zero_short_and_fsync_fail_closed(self) -> None:
        failures = (
            ("zero", lambda: patch.object(identity_migration.os, "write", return_value=0)),
            ("short", lambda: patch.object(identity_migration.os, "write", return_value=2)),
            (
                "fsync",
                lambda: patch.object(
                    identity_migration.os, "fsync", side_effect=OSError("fixture-lock-fsync")
                ),
            ),
        )
        for name, context_factory in failures:
            with self.subTest(lock="startup", failure=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "runtime"
                paths = RuntimePaths(
                    root,
                    root / "audit",
                    root / "coordination.json",
                    root / "runs.json",
                )
                with context_factory(), self.assertRaisesRegex(
                    RunsBackupGateError, "daemon_startup_lock_unavailable"
                ):
                    with daemon_startup_election(paths):
                        pass

        for name, context_factory in failures:
            with self.subTest(lock="migration", failure=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                paths = runtime_paths(root)
                paths.root.mkdir(parents=True)
                paths.runs_path.write_bytes(b"source")
                with context_factory(), self.assertRaisesRegex(
                    RunsBackupGateError, "runs_backup_lock_unavailable"
                ):
                    self.run_gate(paths, root / "migration")

    def test_name_surrogate_runtime_root_is_rejected_when_platform_can_create_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / "target"
            target.mkdir()
            surrogate = base / "surrogate"
            try:
                surrogate.symlink_to(target, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symlink unavailable: {error}")
            paths = RuntimePaths(
                surrogate,
                surrogate / "audit",
                surrogate / "coordination.json",
                surrogate / "runs.json",
            )

            with self.assertRaisesRegex(
                RunsBackupGateError, "daemon_startup_lock_unavailable"
            ):
                with daemon_startup_election(paths):
                    pass

    def test_name_surrogate_tag_fails_closed_without_platform_symlink_privilege(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            paths = RuntimePaths(
                root,
                root / "audit",
                root / "coordination.json",
                root / "runs.json",
            )
            with (
                patch.object(
                    identity_migration.os,
                    "stat",
                    return_value=SimpleNamespace(st_reparse_tag=0xA000000C),
                ),
                self.assertRaisesRegex(
                    RunsBackupGateError, "daemon_startup_lock_unavailable"
                ),
            ):
                with daemon_startup_election(paths):
                    pass


class DaemonStartupElectionProcessTest(unittest.TestCase):
    def _assert_real_client_is_not_a_blocker(self, arguments: list[str]) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            keyfile = base / "fixture.key"
            keyfile.write_text("fixture-key", encoding="ascii")
            fixture_home = base / "home"
            write_client_host_fixture(fixture_home, keyfile)
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = str(base / "local")
            environment["HOME"] = str(fixture_home)
            environment["USERPROFILE"] = str(fixture_home)
            child = subprocess.Popen(
                [
                    sys.executable,
                    *arguments,
                    f"--keyfile={keyfile}",
                ],
                cwd=str(Path(__file__).resolve().parents[1]),
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and child.poll() is None:
                    self.assertNotIn(child.pid, scan_dayz_mcp_processes())
                    time.sleep(0.02)
                self.assertIsNone(child.poll())
            finally:
                if child.stdin is not None:
                    child.stdin.close()
                    child.stdin = None
                _stdout, stderr = communicate_owned_fixture(child, 10.0)
            self.assertEqual(child.returncode, 0, stderr)

    def test_isolated_dash_I_client_argv_is_not_a_blocker(self) -> None:
        """``-Imdayz_mcp`` is an argv classifier, not copy accreditation.

        Isolated mode ignores cwd and PYTHONPATH. The child therefore loads
        whatever the approved venv's editable finder maps — the live tree,
        not this checkout. A green here only means that argv is not a
        blocker. Identity of that isolation is
        ``test_isolated_dash_I_import_does_not_accredit_this_checkout``.
        """
        self._assert_real_client_is_not_a_blocker(
            ["-Imdayz_mcp", "--client", "--idle-timeout", "-1"]
        )

    def test_compact_module_client_with_equals_value_is_not_a_blocker(self) -> None:
        """``-mdayz_mcp`` without ``-I`` is scanned as a client, not a writer.

        This spawn keeps cwd on ``sys.path``, so PathFinder can see this
        checkout. The import probe below is the accreditation for that
        resolution; the ``-I`` sibling test is not.
        """
        self._assert_real_client_is_not_a_blocker(
            ["-mdayz_mcp", "--client", "--task-label=-nightly"]
        )
        probe = "import dayz_mcp; print(dayz_mcp.__file__, flush=True)"
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            timeout=15.0,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        assert_same_checkout(Path(__file__), Path(completed.stdout.strip()))

    def test_module_main_entrypoint_is_observed_as_real_writer_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            keyfile = base / "fixture.key"
            keyfile.write_text("fixture-key", encoding="ascii")
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = str(base / "local")
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "dayz_mcp.__main__",
                    "--embedded",
                    "--keyfile",
                    str(keyfile),
                    "--port",
                    str(unused_port()),
                    "--idle-timeout",
                    "0.5",
                ],
                cwd=str(Path(__file__).resolve().parents[1]),
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            observed: tuple[int, ...] = ()
            try:
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and child.poll() is None:
                    observed = scan_dayz_mcp_processes()
                    if child.pid in observed:
                        break
                    time.sleep(0.02)
                self.assertIn(child.pid, observed)
            finally:
                if child.stdin is not None:
                    child.stdin.close()
                    child.stdin = None
                _stdout, stderr = communicate_owned_fixture(child, 10.0)
            self.assertEqual(child.returncode, 0, stderr)

    def test_real_run_daemon_crash_boundaries_recover_in_second_wave(self) -> None:
        stages = ("migration", "bind", "activation", "status")
        for stage in stages:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                localappdata = base / "local"
                runtime = localappdata / "DayZ_MCP"
                runtime.mkdir(parents=True)
                source = b'{"runs":[],"version":1}\n'
                (runtime / "runs.json").write_bytes(source)
                migration = base / "migration"
                signal = base / "crashed"
                port = unused_port()
                keyfile = base / "fixture.key"
                keyfile.write_text("fixture-key", encoding="ascii")
                cwd = fixture_daemon_cwd()
                base_environment = os.environ.copy()
                base_environment["LOCALAPPDATA"] = str(localappdata)
                first_environment = fixture_environment(
                    base_environment,
                    mode=stage,
                    signal=signal,
                    migration=migration,
                )

                first = subprocess.run(
                    fixture_daemon_argv(port, keyfile),
                    cwd=cwd,
                    env=first_environment,
                    capture_output=True,
                    text=True,
                    timeout=12.0,
                )
                self.assertEqual(first.returncode, 91, first.stderr)
                self.assertEqual(signal.read_text(encoding="ascii"), stage)

                second_environment = fixture_environment(
                    base_environment,
                    mode="none",
                    signal=base / "unused-signal",
                    migration=migration,
                )
                second = subprocess.Popen(
                    fixture_daemon_argv(port, keyfile),
                    cwd=cwd,
                    env=second_environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                deadline = time.monotonic() + DAEMON_STARTUP_BUDGET_S
                payload: dict[str, object] | None = None
                while (
                    payload is None
                    and second.poll() is None
                    and time.monotonic() < deadline
                ):
                    url = (
                        f"http://127.0.0.1:{port}/status?key="
                        + urllib.parse.quote("fixture-key", safe="")
                    )
                    try:
                        with urllib.request.urlopen(url, timeout=0.2) as response:
                            payload = json.loads(response.read().decode("utf-8"))
                    except Exception:
                        time.sleep(0.02)
                _stdout, stderr = communicate_owned_fixture(second, 10.0)
                self.assertIsNotNone(payload, stderr)
                self.assertEqual(second.returncode, 0, stderr)
                self.assertIsInstance(payload.get("daemon_generation"), str)
                self.assertEqual((migration / "runs.pre-v2.json").read_bytes(), source)
                receipt = json.loads(
                    (migration / "runs-backup-receipt.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(receipt["source"], receipt["backup"])
                self.assertFalse(
                    (migration / "runs-backup-transaction.json").exists()
                )

    def test_run_daemon_candidate_wave_and_cross_port_publish_one_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            localappdata = base / "local"
            control = base / "control"
            control.mkdir()
            start = control / "start"
            fixture_pids_path = control / "fixture-pids.json"
            ports = (unused_port(), unused_port())
            keyfile = base / "fixture.key"
            keyfile.write_text("fixture-key", encoding="ascii")
            cwd = fixture_daemon_cwd()
            base_environment = os.environ.copy()
            base_environment["LOCALAPPDATA"] = str(localappdata)
            environment = fixture_environment(
                base_environment,
                mode="wave",
                signal=base / "unused-signal",
                migration=base / "migration",
                start=start,
                fixture_pids_path=fixture_pids_path,
            )
            children = []
            for index in range(6):
                port = ports[index % 2]
                children.append(
                    subprocess.Popen(
                        fixture_daemon_argv(port, keyfile),
                        cwd=cwd,
                        env=environment,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                )
            fixture_pids_path.write_text(
                json.dumps([child.pid for child in children]), encoding="utf-8"
            )
            errors: list[str] = []
            try:
                start.write_text("start", encoding="ascii")
                waiters = [
                    threading.Thread(target=child.wait, daemon=True)
                    for child in children
                ]
                for waiter in waiters:
                    waiter.start()
                deadline = time.monotonic() + DAEMON_STARTUP_BUDGET_S
                payloads: dict[int, dict[str, object]] = {}
                while (
                    not payloads
                    and any(child.poll() is None for child in children)
                    and time.monotonic() < deadline
                ):
                    for port in ports:
                        url = (
                            f"http://127.0.0.1:{port}/status?key="
                            + urllib.parse.quote("fixture-key", safe="")
                        )
                        try:
                            with urllib.request.urlopen(url, timeout=0.2) as response:
                                payloads[port] = json.loads(
                                    response.read().decode("utf-8")
                                )
                        except Exception:
                            pass
                    if not payloads:
                        time.sleep(0.02)
            finally:
                for child in children:
                    _stdout, stderr = communicate_owned_fixture(child, 12.0)
                    errors.append(
                        f"pid={child.pid};rc={child.returncode};stderr={stderr}"
                    )
                for waiter in waiters:
                    waiter.join(timeout=1.0)

            self.assertEqual(len(payloads), 1, errors)
            generation = next(iter(payloads.values())).get("daemon_generation")
            self.assertIsInstance(generation, str)
            self.assertTrue(generation)
            self.assertTrue(
                all(child.returncode in {0, 75} for child in children),
                ([child.returncode for child in children], errors),
            )
            coordination = json.loads(
                (localappdata / "DayZ_MCP" / "coordination.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(coordination["daemon_generation"], generation)

    def test_owner_process_death_releases_election_for_next_wave(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            signal = Path(temporary) / "owned"
            child_code = """
import os, sys
from pathlib import Path
from dayz_mcp.identity_migration import daemon_startup_election
from dayz_mcp.runtime_state import RuntimePaths
root, signal = map(Path, sys.argv[1:])
paths = RuntimePaths(root, root / 'audit', root / 'coordination.json', root / 'runs.json')
with daemon_startup_election(paths) as elected:
    if elected:
        signal.write_text('owned', encoding='ascii')
        os._exit(91)
raise SystemExit(92)
"""
            child = subprocess.Popen(
                [sys.executable, "-c", child_code, str(root), str(signal)],
                cwd=str(Path(__file__).resolve().parents[1]),
            )
            child.wait(timeout=5.0)
            self.assertEqual(child.returncode, 91)
            self.assertEqual(signal.read_text(encoding="ascii"), "owned")
            paths = RuntimePaths(
                root,
                root / "audit",
                root / "coordination.json",
                root / "runs.json",
            )
            with daemon_startup_election(paths) as elected:
                self.assertTrue(elected)

    def test_candidate_wave_elects_one_owner_and_second_wave_progresses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            control = Path(temporary) / "control"
            control.mkdir()
            start = control / "start"
            release = control / "release"
            child_code = """
import sys, time
from pathlib import Path
from dayz_mcp.identity_migration import daemon_startup_election
from dayz_mcp.runtime_state import RuntimePaths
root, start, release, result = map(Path, sys.argv[1:])
while not start.exists():
    time.sleep(0.005)
paths = RuntimePaths(root, root / 'audit', root / 'coordination.json', root / 'runs.json')
with daemon_startup_election(paths) as elected:
    result.write_text('1' if elected else '0', encoding='ascii')
    if elected:
        while not release.exists():
            time.sleep(0.005)
"""
            results = [control / f"result-{index}" for index in range(6)]
            children = [
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        child_code,
                        str(root),
                        str(start),
                        str(release),
                        str(result),
                    ],
                    cwd=str(Path(__file__).resolve().parents[1]),
                )
                for result in results
            ]
            try:
                start.write_text("start", encoding="ascii")
                deadline = time.monotonic() + 8.0
                while (
                    any(not result.exists() for result in results)
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                self.assertTrue(all(result.exists() for result in results))
                self.assertEqual(
                    [result.read_text(encoding="ascii") for result in results].count("1"),
                    1,
                )
            finally:
                release.write_text("release", encoding="ascii")
                for child in children:
                    child.wait(timeout=5.0)

            paths = RuntimePaths(
                root,
                root / "audit",
                root / "coordination.json",
                root / "runs.json",
            )
            with daemon_startup_election(paths) as elected:
                self.assertTrue(elected)

    def test_runtime_root_lock_has_cross_process_ownership_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            signal = root / "locked"
            release = root / "release"
            child_code = """
import sys, time
from pathlib import Path
from dayz_mcp.identity_migration import daemon_startup_election
from dayz_mcp.runtime_state import RuntimePaths
root, signal, release = map(Path, sys.argv[1:])
paths = RuntimePaths(root, root / 'audit', root / 'coordination.json', root / 'runs.json')
with daemon_startup_election(paths) as elected:
    # Publish atomically: the parent must never observe an existing-but-empty signal.
    staged = signal.with_suffix('.tmp')
    staged.write_text('1' if elected else '0', encoding='ascii')
    staged.replace(signal)
    while not release.exists():
        time.sleep(0.01)
"""
            child = subprocess.Popen(
                [sys.executable, "-c", child_code, str(root), str(signal), str(release)],
                cwd=str(Path(__file__).resolve().parents[1]),
            )
            try:
                # Wait for CONTENT, not existence: a create-then-write child races an
                # exists()-gated read into '' (measured 1/3 under a full-suite load).
                deadline = time.monotonic() + 15.0
                observed = ""
                while time.monotonic() < deadline:
                    try:
                        observed = signal.read_text(encoding="ascii")
                    except FileNotFoundError:
                        observed = ""
                    if observed:
                        break
                    time.sleep(0.01)
                self.assertEqual(observed, "1")
                paths = RuntimePaths(
                    root,
                    root / "audit",
                    root / "coordination.json",
                    root / "runs.json",
                )
                with daemon_startup_election(paths) as elected:
                    self.assertFalse(elected)
            finally:
                release.write_text("release", encoding="ascii")
                child.wait(timeout=5.0)
            with daemon_startup_election(paths) as elected:
                self.assertTrue(elected)


class TreeIdentityTest(unittest.TestCase):
    def test_same_checkout_is_silent(self) -> None:
        import dayz_mcp

        assert_same_checkout(Path(__file__), Path(dayz_mcp.__file__))

    def test_foreign_checkout_names_both_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            foreign = Path(temporary)
            package = foreign / "tools" / "dayz_mcp"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            with self.assertRaises(TreeIdentityError) as raised:
                assert_same_checkout(Path(__file__), package / "__init__.py")
            message = str(raised.exception)
            self.assertIn("another tree", message)
            self.assertIn(str(checkout_root(Path(__file__))), message)
            self.assertIn(str(foreign.resolve()), message)

    def test_fixture_daemon_cwd_is_this_checkout_not_the_editable_mapping(self) -> None:
        cwd = Path(fixture_daemon_cwd()).resolve()
        self.assertEqual(cwd, Path(__file__).resolve().parents[1])
        mapped = editable_mapped_tools_dir()
        self.assertIsNotNone(mapped)
        test_root = checkout_root(Path(__file__))
        mapped_root = checkout_root(mapped)
        if os.path.normcase(str(test_root)) == os.path.normcase(str(mapped_root)):
            self.skipTest(
                "this run is the editable checkout; fixture cwd and mapping coincide"
            )
        self.assertNotEqual(
            os.path.normcase(str(cwd)),
            os.path.normcase(str(mapped)),
            "fixture cwd still follows the editable mapping",
        )

    def test_fixture_child_loads_dayz_mcp_from_this_checkout(self) -> None:
        """A child with only the fixture on PYTHONPATH must still import this copy.

        That is the historical spawn: cwd has no package, PYTHONPATH is the
        crash-fixture site, and the editable finder used to win. Sitecustomize
        has to beat the finder; cwd/PYTHONPATH alone did not.
        """
        probe = "import dayz_mcp; print(dayz_mcp.__file__, flush=True)"
        with tempfile.TemporaryDirectory() as temporary:
            environment = os.environ.copy()
            inherited = environment.get("PYTHONPATH")
            environment["PYTHONPATH"] = str(DAEMON_FIXTURE_SITE)
            if inherited:
                environment["PYTHONPATH"] += os.pathsep + inherited
            completed = subprocess.run(
                [sys.executable, "-c", probe],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15.0,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        loaded = Path(completed.stdout.strip()).resolve()
        assert_same_checkout(Path(__file__), loaded)
        test_root = checkout_root(Path(__file__))
        loaded_s = os.path.normcase(str(loaded))
        self.assertTrue(
            loaded_s.startswith(os.path.normcase(str(test_root)) + os.sep),
            f"child did not import this checkout: {loaded}",
        )
        mapped = editable_mapped_tools_dir()
        if mapped is None:
            return
        mapped_root = checkout_root(mapped)
        if os.path.normcase(str(mapped_root)) == os.path.normcase(str(test_root)):
            return
        self.assertFalse(
            loaded_s.startswith(os.path.normcase(str(mapped_root)) + os.sep),
            f"child imported the editable mapping: {loaded}",
        )

    def test_isolated_dash_I_import_does_not_accredit_this_checkout(self) -> None:
        """The ``-I`` client spawn cannot accredit this copy's code.

        Isolated mode drops cwd and PYTHONPATH. The editable finder then
        serves the live tree. This is the identity measurement for that
        isolation; it is not a copy-green.
        """
        mapped = editable_mapped_tools_dir()
        self.assertIsNotNone(mapped)
        test_root = checkout_root(Path(__file__))
        mapped_root = checkout_root(mapped)
        if os.path.normcase(str(test_root)) == os.path.normcase(str(mapped_root)):
            self.skipTest(
                "this run is the editable checkout; -I isolation and the copy coincide"
            )
        probe = "import dayz_mcp; print(dayz_mcp.__file__, flush=True)"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(DAEMON_FIXTURE_SITE)
        completed = subprocess.run(
            [sys.executable, "-I", "-c", probe],
            cwd=str(Path(__file__).resolve().parents[1]),
            env=environment,
            capture_output=True,
            text=True,
            timeout=15.0,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        loaded = Path(completed.stdout.strip()).resolve()
        with self.assertRaises(TreeIdentityError):
            assert_same_checkout(Path(__file__), loaded)
        loaded_s = os.path.normcase(str(loaded))
        self.assertTrue(
            loaded_s.startswith(os.path.normcase(str(mapped_root)) + os.sep),
            f"-I child did not import the editable mapping: {loaded}",
        )

    def test_fixture_child_with_live_cwd_loads_this_checkout(self) -> None:
        """Bind must win when cwd is the live tools directory, without fixture mode.

        Sitecustomize runs before Python prepends ``''``. A path insert made
        there loses to a cwd that already contains ``dayz_mcp``. The helper
        has to pin the copy anyway.
        """
        mapped = editable_mapped_tools_dir()
        self.assertIsNotNone(mapped)
        test_root = checkout_root(Path(__file__))
        mapped_root = checkout_root(mapped)
        if os.path.normcase(str(test_root)) == os.path.normcase(str(mapped_root)):
            self.skipTest(
                "this run is the editable checkout; live cwd and the copy coincide"
            )
        probe = "import dayz_mcp; print(dayz_mcp.__file__, flush=True)"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(DAEMON_FIXTURE_SITE)
        environment.pop("DAYZ_MCP_FIXTURE_MODE", None)
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(mapped),
            env=environment,
            capture_output=True,
            text=True,
            timeout=15.0,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        loaded = Path(completed.stdout.strip()).resolve()
        assert_same_checkout(Path(__file__), loaded)
        loaded_s = os.path.normcase(str(loaded))
        self.assertFalse(
            loaded_s.startswith(os.path.normcase(str(mapped_root)) + os.sep),
            f"live cwd still imported the editable mapping: {loaded}",
        )

    def test_fixture_child_drops_editable_finder(self) -> None:
        """Sitecustomize must call the helper; a path insert does not drop the finder."""
        self.assertIsNotNone(editable_mapped_tools_dir())
        probe = (
            "import sys\n"
            "from tests._tree_identity import PinnedDayzMcpFinder, editable_mapped_tools_dir\n"
            "pinned = isinstance(sys.meta_path[0], PinnedDayzMcpFinder)\n"
            "mapped = editable_mapped_tools_dir()\n"
            "print('pinned' if pinned else 'unpinned', flush=True)\n"
            "print('none' if mapped is None else 'present', flush=True)\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(DAEMON_FIXTURE_SITE)
            environment.pop("DAYZ_MCP_FIXTURE_MODE", None)
            completed = subprocess.run(
                [sys.executable, "-c", probe],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15.0,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.splitlines(), ["pinned", "none"], completed.stdout)

    def test_bind_purges_preloaded_foreign_dayz_mcp(self) -> None:
        """A live ``dayz_mcp`` already in ``sys.modules`` must not survive bind."""
        mapped = editable_mapped_tools_dir()
        self.assertIsNotNone(mapped)
        test_root = checkout_root(Path(__file__))
        mapped_root = checkout_root(mapped)
        if os.path.normcase(str(test_root)) == os.path.normcase(str(mapped_root)):
            self.skipTest(
                "this run is the editable checkout; no foreign package to purge"
            )
        copy_tools = Path(__file__).resolve().parents[1]
        probe = """
import sys
import dayz_mcp
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tests._tree_identity import bind_child_import_tree, checkout_root
before = checkout_root(dayz_mcp.__file__)
bind_child_import_tree(Path(sys.argv[1]))
import dayz_mcp as rebound
after = checkout_root(rebound.__file__)
print(before, after, sep='\\n', flush=True)
"""
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("DAYZ_MCP_FIXTURE_MODE", None)
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, "-c", probe, str(copy_tools)],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15.0,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2, completed.stdout)
        before_root = Path(lines[0])
        after_root = Path(lines[1])
        self.assertEqual(os.path.normcase(str(before_root)), os.path.normcase(str(mapped_root)))
        self.assertEqual(os.path.normcase(str(after_root)), os.path.normcase(str(test_root)))


class FixturePythonGuardTest(unittest.TestCase):
    def test_same_shape_venv_is_rejected_by_independent_host_reference(self) -> None:
        """A second ``<root>/.venv-mcp/Scripts/python.exe`` must not be approved.

        The fixture may relocate the lookup when this copy has no sibling
        venv, but the approved interpreter has to come from a host reference
        independent of the executable under test. Matching the path shape is
        not approval.
        """
        mapped = editable_mapped_tools_dir()
        self.assertIsNotNone(mapped)
        approved_site = (
            Path(sys.executable).resolve().parents[1] / "Lib" / "site-packages"
        )
        probe = (
            "from types import SimpleNamespace\n"
            "from dayz_mcp import daemon\n"
            "try:\n"
            "    daemon._ensure_identity_migration(SimpleNamespace(port=49999))\n"
            "except RuntimeError as exc:\n"
            "    print(str(exc), flush=True)\n"
            "else:\n"
            "    print('ACCEPTED', flush=True)\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            venv_home = root / "shape" / ".venv-mcp"
            created = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_home), "--without-pip"],
                capture_output=True,
                text=True,
                timeout=60.0,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            other_python = venv_home / "Scripts" / "python.exe"
            self.assertTrue(other_python.is_file(), other_python)
            signal = root / "signal"
            migration = root / "migration"
            local = root / "local"
            local.mkdir()
            base_env = os.environ.copy()
            base_env["PYTHONPATH"] = os.pathsep.join(
                (str(DAEMON_FIXTURE_SITE), str(approved_site))
            )
            base_env["DAYZ_MCP_FIXTURE_MODE"] = "none"
            base_env["DAYZ_MCP_FIXTURE_SIGNAL"] = str(signal)
            base_env["DAYZ_MCP_FIXTURE_MIGRATION"] = str(migration)
            base_env["LOCALAPPDATA"] = str(local)
            variants = {
                "host_tools_known": str(mapped),
                "host_tools_absent": None,
            }
            for name, host_tools in variants.items():
                with self.subTest(name=name):
                    environment = dict(base_env)
                    if host_tools is None:
                        environment.pop("DAYZ_MCP_HOST_TOOLS", None)
                    else:
                        environment["DAYZ_MCP_HOST_TOOLS"] = host_tools
                    completed = subprocess.run(
                        [str(other_python), "-c", probe],
                        cwd=str(root),
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=30.0,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(
                        completed.stdout.strip(),
                        "daemon_python_not_approved",
                        completed.stdout + completed.stderr,
                    )


if __name__ == "__main__":
    unittest.main()
