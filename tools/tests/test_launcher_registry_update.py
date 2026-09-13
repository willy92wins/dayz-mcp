from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import build_native_launcher
from dayz_mcp import launcher_registry
from dayz_mcp import launcher_registry_update as updater
from tests._bundle_paths import requires_built_bundle


BASELINE = b'{\n  "format_version": 1,\n  "launchers": []\n}\n'


def _entry() -> dict[str, object]:
    return {
        "id": "dayz-test-v1",
        "relative_path": "dayz-test-launcher.exe",
        "root": r"C:\fixture\dayz-test-v1",
        "root_file_id": {
            "file_id": "0" * 32,
            "volume_serial_number": 1,
        },
        "sha256": "A" * 64,
    }


@unittest.skipUnless(os.name == "nt", "ReplaceFileW and LockFileEx are Windows-only")
class LauncherRegistryUpdateTest(unittest.TestCase):
    def _paths(self, root: Path) -> tuple[Path, Path, Path]:
        registry = root / "approved-launchers.json"
        registry.write_bytes(BASELINE)
        lock = root / "approved-launchers.lock"
        lock.write_bytes(b"lock\n")
        return registry, lock, root / "receipts"

    def test_install_is_cas_verified_and_one_link_rollback_restores_exact_bytes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                installed_sha = updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                )
            installed = json.loads(registry.read_text(encoding="utf-8"))
            self.assertEqual(installed["launchers"][0]["id"], "dayz-test-v1")
            self.assertEqual(updater._sha256(registry.read_bytes()), installed_sha)
            self.assertEqual(len(list(receipts.glob("*/committed.json"))), 1)

            restored_sha = updater._rollback_transition(
                registry_path=registry,
                lock_path=lock,
                receipts_path=receipts,
            )
            self.assertEqual(registry.read_bytes(), BASELINE)
            self.assertEqual(restored_sha, updater._sha256(BASELINE))
            self.assertEqual(len(list(receipts.glob("*/rolled-back.json"))), 1)

    def test_bootstrap_creates_the_registry_from_baseline_exactly_once(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "approved-launchers.baseline.json"
            baseline.write_bytes(BASELINE)
            registry = root / "approved-launchers.json"

            created_sha = updater._bootstrap_registry(
                registry_path=registry, baseline_path=baseline
            )
            self.assertEqual(registry.read_bytes(), BASELINE)
            self.assertEqual(created_sha, updater._sha256(BASELINE))
            # Ties this fixture to the shipped pin: if the baseline constant
            # drifts from the real file, the mismatch surfaces here.
            self.assertEqual(created_sha, updater._BASELINE_SHA256)

            with self.assertRaisesRegex(
                RuntimeError, "launcher_registry_already_bootstrapped"
            ):
                updater._bootstrap_registry(
                    registry_path=registry, baseline_path=baseline
                )
            self.assertEqual(registry.read_bytes(), BASELINE)

            drifted = root / "drifted-baseline.json"
            drifted.write_bytes(BASELINE + b" ")
            fresh = root / "fresh-registry.json"
            with self.assertRaisesRegex(
                RuntimeError, "launcher_registry_baseline_drift"
            ):
                updater._bootstrap_registry(
                    registry_path=fresh, baseline_path=drifted
                )
            self.assertFalse(fresh.exists())

    def test_provenance_tracks_the_recorded_chain_and_catches_in_place_rewrites(self) -> None:
        # The four states are asserted on ONE registry as it moves, because
        # what matters is the transition between them, not each label in isolation.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)

            def provenance() -> dict[str, object]:
                return updater.describe_registry_provenance(
                    registry_path=registry, lock_path=lock, receipts_path=receipts
                )

            # A fresh clone has no receipts at all: absence of a chain is not a break.
            self.assertEqual(provenance()["status"], "pristine")

            with patch.object(updater, "_validated_entry", return_value=_entry()):
                updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                )
            installed = provenance()
            self.assertEqual(installed["status"], "anchored")
            self.assertEqual(installed["anchors"], 1)
            self.assertEqual(installed["sha256"], updater._sha256(registry.read_bytes()))

            # The bug: same file id, different bytes, no receipt emitted. This is what
            # an editor or an ad-hoc script does, and the drift check alone misses it.
            rewritten = registry.read_bytes().replace(b'"sha256": "A', b'"sha256": "B')
            self.assertNotEqual(rewritten, registry.read_bytes())
            registry.write_bytes(rewritten)
            self.assertEqual(provenance()["status"], "unanchored")

            # Causal link: the state this reports is exactly the one that makes
            # rollback-last die, so the check predicts the failure instead of
            # describing it afterwards.
            with self.assertRaisesRegex(
                RuntimeError, "launcher_registry_rollback_predecessor_unknown"
            ):
                updater._rollback_transition(
                    registry_path=registry, lock_path=lock, receipts_path=receipts
                )

            # Putting the recorded bytes back by hand does NOT forge an anchor: the
            # transition was never rolled back, so nothing legitimises this content.
            registry.write_bytes(BASELINE)
            self.assertEqual(provenance()["status"], "unanchored")

    def test_provenance_after_a_real_rollback_is_not_reported_as_broken(self) -> None:
        # Guards the obvious false positive: after rollback-last the live content is
        # a receipt's `from` side, never a `to` side. A naive "must be some receipt's
        # target" predicate would flag every correctly rolled-back tree as corrupt.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                )
            updater._rollback_transition(
                registry_path=registry, lock_path=lock, receipts_path=receipts
            )
            self.assertEqual(registry.read_bytes(), BASELINE)
            state = updater.describe_registry_provenance(
                registry_path=registry, lock_path=lock, receipts_path=receipts
            )
            self.assertEqual(state["status"], "rolled_back")

    def test_cas_mismatch_and_pre_replace_failure_never_change_registry(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                with self.assertRaisesRegex(RuntimeError, "cas_mismatch"):
                    updater._install_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256="F" * 64,
                    )
                with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                    updater._install_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(BASELINE),
                        fail_at="before_replace",
                    )
            self.assertEqual(registry.read_bytes(), BASELINE)
            self.assertFalse(list(receipts.glob("*/committed.json")))

    def test_post_replace_failure_is_recovered_into_a_rollback_receipt(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                    updater._install_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(BASELINE),
                        fail_at="after_replace",
                    )
            self.assertNotEqual(registry.read_bytes(), BASELINE)
            self.assertFalse(list(receipts.glob("*/committed.json")))

            updater._rollback_transition(
                registry_path=registry,
                lock_path=lock,
                receipts_path=receipts,
            )
            self.assertEqual(registry.read_bytes(), BASELINE)
            self.assertEqual(len(list(receipts.glob("*/committed.json"))), 1)

    def test_receipt_drift_blocks_rollback_without_changing_installed_bytes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                )
            installed = registry.read_bytes()
            prepared = next(receipts.glob("*/prepared.json"))
            prepared.write_bytes(prepared.read_bytes() + b" ")
            with self.assertRaisesRegex(RuntimeError, "invalid_launcher_registry_receipt"):
                updater._rollback_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                )
            self.assertEqual(registry.read_bytes(), installed)

    def test_rollback_revalidates_registry_immediately_before_replace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                )
            external = b'{"format_version":1,"launchers":[]}\n '
            original_read = updater._read_pinned
            registry_reads = 0

            def racing_read(path: Path):
                nonlocal registry_reads
                if path == registry:
                    registry_reads += 1
                    if registry_reads == 2:
                        registry.write_bytes(external)
                return original_read(path)

            with patch.object(updater, "_read_pinned", side_effect=racing_read):
                with self.assertRaisesRegex(RuntimeError, "cas_mismatch"):
                    updater._rollback_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                    )

            self.assertEqual(registry.read_bytes(), external)
            self.assertFalse(list(receipts.glob("*/rolled-back.json")))


def _has_dayz_test_v1(registry: Path) -> bool:
    payload = json.loads(registry.read_text(encoding="utf-8"))
    return any(item.get("id") == "dayz-test-v1" for item in payload.get("launchers", []))


def _entry_with_sha(digest: str) -> dict[str, object]:
    entry = _entry()
    entry["sha256"] = digest
    return entry


@unittest.skipUnless(os.name == "nt", "ReplaceFileW and LockFileEx are Windows-only")
class ReplaceTransitionTest(unittest.TestCase):
    def _paths(self, root: Path) -> tuple[Path, Path, Path]:
        registry = root / "approved-launchers.json"
        registry.write_bytes(BASELINE)
        lock = root / "approved-launchers.lock"
        lock.write_bytes(b"lock\n")
        return registry, lock, root / "receipts"

    def test_replacing_an_installed_launcher_never_leaves_the_registry_without_one(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                )
            self.assertTrue(_has_dayz_test_v1(registry))
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                with self.assertRaisesRegex(
                    RuntimeError, "launcher_registry_version_already_installed"
                ):
                    updater._install_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                    )
            self.assertTrue(_has_dayz_test_v1(registry))

            replacement = _entry_with_sha("B" * 64)
            with patch.object(updater, "_validated_entry", return_value=replacement):
                updater._replace_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(registry.read_bytes()),
                )
            self.assertTrue(_has_dayz_test_v1(registry))
            installed = json.loads(registry.read_text(encoding="utf-8"))
            self.assertEqual(installed["launchers"][0]["sha256"], "B" * 64)
            self.assertEqual(len(installed["launchers"]), 1)

            with patch.object(
                updater, "_validated_entry", return_value=_entry_with_sha("C" * 64)
            ):
                with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                    updater._replace_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                        fail_at="before_apply",
                    )
            self.assertTrue(_has_dayz_test_v1(registry))
            self.assertEqual(
                json.loads(registry.read_text(encoding="utf-8"))["launchers"][0]["sha256"],
                "B" * 64,
            )

            with patch.object(
                updater, "_validated_entry", return_value=_entry_with_sha("C" * 64)
            ):
                with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                    updater._replace_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                        fail_at="after_apply",
                    )
            self.assertTrue(_has_dayz_test_v1(registry))
            self.assertEqual(
                json.loads(registry.read_text(encoding="utf-8"))["launchers"][0]["sha256"],
                "C" * 64,
            )

    def test_a_failure_after_the_swap_is_recovered(self) -> None:
        """Recover the after_apply half of a replace. The before_apply half is
        covered by fb-20260907-083722-1f0a, not by this test.
        """
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                )
            committed_after_install = list(receipts.glob("*/committed.json"))
            self.assertEqual(len(committed_after_install), 1)

            replacement = _entry_with_sha("B" * 64)
            with patch.object(updater, "_validated_entry", return_value=replacement):
                with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                    updater._replace_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                        fail_at="after_apply",
                    )
            self.assertTrue(_has_dayz_test_v1(registry))
            self.assertEqual(len(list(receipts.glob("*/committed.json"))), 1)
            self.assertTrue(list(receipts.glob("*/prepared.json")))

            with patch.object(updater, "_validated_entry", return_value=replacement):
                with self.assertRaisesRegex(RuntimeError, "cas_mismatch"):
                    updater._replace_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256="F" * 64,
                    )
            self.assertTrue(_has_dayz_test_v1(registry))
            self.assertEqual(len(list(receipts.glob("*/committed.json"))), 2)
            self.assertEqual(
                json.loads(registry.read_text(encoding="utf-8"))["launchers"][0]["sha256"],
                "B" * 64,
            )

    def test_an_aborted_transition_leaves_no_temporary_file(self) -> None:
        def _temps(registry: Path) -> list[Path]:
            return list(registry.parent.glob(f".{registry.name}.tmp.*"))

        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                    updater._install_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(BASELINE),
                        fail_at="before_replace",
                    )
            self.assertEqual(_temps(registry), [])

        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                )
            self.assertEqual(_temps(registry), [])
            replacement = _entry_with_sha("B" * 64)
            with patch.object(updater, "_validated_entry", return_value=replacement):
                with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                    updater._replace_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                        fail_at="before_apply",
                    )
            self.assertEqual(_temps(registry), [])


@unittest.skipUnless(os.name == "nt", "ReplaceFileW and LockFileEx are Windows-only")
class AbortedPreparedRecoveryTest(unittest.TestCase):
    def _paths(self, root: Path) -> tuple[Path, Path, Path]:
        registry = root / "approved-launchers.json"
        registry.write_bytes(BASELINE)
        lock = root / "approved-launchers.lock"
        lock.write_bytes(b"lock\n")
        return registry, lock, root / "receipts"

    def _abort_then_retry_same_replace(
        self, registry: Path, lock: Path, receipts: Path, root: Path
    ) -> str:
        # STEP 1: install A. STEP 2: abort the A->B swap before ReplaceFileW,
        # leaving an orphaned prepared.json whose to_identity never landed.
        # STEP 3: retry the same replace; the live file becomes B through a
        # NEW identity. The orphan still names B's sha with the temp identity.
        with patch.object(updater, "_validated_entry", return_value=_entry()):
            updater._install_transition(
                registry_path=registry,
                lock_path=lock,
                receipts_path=receipts,
                bundle=root,
                expected_sha256=updater._sha256(BASELINE),
            )
        self.assertEqual(len(list(receipts.glob("*/prepared.json"))), 1)
        self.assertEqual(len(list(receipts.glob("*/committed.json"))), 1)

        replacement = _entry_with_sha("B" * 64)
        with patch.object(updater, "_validated_entry", return_value=replacement):
            with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                updater._replace_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(registry.read_bytes()),
                    fail_at="before_apply",
                )
            self.assertEqual(len(list(receipts.glob("*/prepared.json"))), 2)
            self.assertEqual(len(list(receipts.glob("*/committed.json"))), 1)
            sha_b = updater._replace_transition(
                registry_path=registry,
                lock_path=lock,
                receipts_path=receipts,
                bundle=root,
                expected_sha256=updater._sha256(registry.read_bytes()),
            )
        self.assertEqual(updater._sha256(registry.read_bytes()), sha_b)
        self.assertEqual(len(list(receipts.glob("*/prepared.json"))), 3)
        self.assertEqual(len(list(receipts.glob("*/committed.json"))), 2)
        return sha_b

    def test_aborted_before_swap_retry_does_not_block_later_transitions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            self._abort_then_retry_same_replace(registry, lock, receipts, root)

            state = updater.describe_registry_provenance(
                registry_path=registry, lock_path=lock, receipts_path=receipts
            )
            self.assertNotEqual(state["status"], "anchored")
            self.assertEqual(state["status"], "stalled")
            self.assertIs(state["recoverable"], True)
            self.assertIn("prepared.json", str(state["blocking_receipt"]))
            self.assertTrue(state["repair"])

            with patch.object(
                updater, "_validated_entry", return_value=_entry_with_sha("C" * 64)
            ):
                updater._replace_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(registry.read_bytes()),
                )
            self.assertEqual(
                json.loads(registry.read_text(encoding="utf-8"))["launchers"][0]["sha256"],
                "C" * 64,
            )
            updater._rollback_transition(
                registry_path=registry, lock_path=lock, receipts_path=receipts
            )

            with patch.object(updater, "_validated_entry", return_value=_entry()):
                with self.assertRaisesRegex(
                    RuntimeError, "launcher_registry_version_already_installed"
                ):
                    updater._install_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                    )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            sha_b = self._abort_then_retry_same_replace(registry, lock, receipts, root)
            restored = updater._rollback_transition(
                registry_path=registry, lock_path=lock, receipts_path=receipts
            )
            self.assertNotEqual(restored, sha_b)
            self.assertTrue(_has_dayz_test_v1(registry))
            with patch.object(
                updater, "_validated_entry", return_value=_entry_with_sha("C" * 64)
            ):
                updater._replace_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(registry.read_bytes()),
                )
            self.assertEqual(
                json.loads(registry.read_text(encoding="utf-8"))["launchers"][0]["sha256"],
                "C" * 64,
            )

    def test_aborted_first_install_retry_does_not_block_install(self) -> None:
        # Same recover branch, but only install: the pre-existing path that
        # does not need replace. After the retry, rollback restores empty and
        # a fresh install must be allowed to land.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                    updater._install_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(BASELINE),
                        fail_at="before_replace",
                    )
                installed = updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                )
            self.assertEqual(updater._sha256(registry.read_bytes()), installed)
            state = updater.describe_registry_provenance(
                registry_path=registry, lock_path=lock, receipts_path=receipts
            )
            self.assertNotEqual(state["status"], "anchored")
            self.assertEqual(state["status"], "stalled")
            self.assertIs(state["recoverable"], True)
            updater._rollback_transition(
                registry_path=registry, lock_path=lock, receipts_path=receipts
            )
            self.assertEqual(registry.read_bytes(), BASELINE)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                again = updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                )
            self.assertEqual(updater._sha256(registry.read_bytes()), again)

    def test_unexplained_to_sha_rewrite_is_still_identity_drift(self) -> None:
        # Closest neighbour of the retry: same to_sha256 as the orphaned
        # prepared, different identity, but NO committed receipt that accounts
        # for the live file. That is manipulation, not an aborted swap.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                )
            replacement = _entry_with_sha("B" * 64)
            with patch.object(updater, "_validated_entry", return_value=replacement):
                with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                    updater._replace_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                        fail_at="before_apply",
                    )
            target_raw = updater._canonical(
                {"format_version": 1, "launchers": [replacement]}
            )
            registry.write_bytes(target_raw)
            self.assertEqual(updater._sha256(registry.read_bytes()), updater._sha256(target_raw))

            with self.assertRaisesRegex(
                RuntimeError, "launcher_registry_receipt_identity_drift"
            ):
                updater._rollback_transition(
                    registry_path=registry, lock_path=lock, receipts_path=receipts
                )
            with patch.object(updater, "_validated_entry", return_value=replacement):
                with self.assertRaisesRegex(
                    RuntimeError, "launcher_registry_receipt_identity_drift"
                ):
                    updater._replace_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                    )
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                with self.assertRaisesRegex(
                    RuntimeError, "launcher_registry_receipt_identity_drift"
                ):
                    updater._install_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                    )
            drifted = updater.describe_registry_provenance(
                registry_path=registry, lock_path=lock, receipts_path=receipts
            )
            self.assertNotEqual(drifted["status"], "anchored")
            self.assertEqual(drifted["status"], "blocked")
            self.assertIs(drifted["recoverable"], False)

    def test_committed_that_contradicts_its_prepared_cannot_explain_live(self) -> None:
        # F1: a committed whose to_sha256/to_identity name the live file but
        # disagree with its sibling prepared is not authority. rollback already
        # rejected this; install/replace must not skip the orphan on that
        # evidence.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                )
            replacement = _entry_with_sha("B" * 64)
            with patch.object(updater, "_validated_entry", return_value=replacement):
                with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                    updater._replace_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                        fail_at="before_apply",
                    )
            target_raw = updater._canonical(
                {"format_version": 1, "launchers": [replacement]}
            )
            registry.write_bytes(target_raw)
            live_raw, live_identity = updater._read_pinned(registry)
            live_sha = updater._sha256(live_raw)
            committed_path = next(receipts.glob("*/committed.json"))
            prepared_path = committed_path.parent / "prepared.json"
            prepared_before = prepared_path.read_bytes()
            committed = json.loads(committed_path.read_text(encoding="utf-8"))
            original_prepared_sha = committed["prepared_sha256"]
            committed["to_sha256"] = live_sha
            committed["to_identity"] = live_identity
            committed_path.write_bytes(updater._canonical(committed))
            tampered = json.loads(committed_path.read_text(encoding="utf-8"))
            self.assertEqual(tampered["prepared_sha256"], original_prepared_sha)
            self.assertEqual(prepared_path.read_bytes(), prepared_before)
            self.assertNotEqual(tampered["to_sha256"], json.loads(prepared_before)["to_sha256"])

            blocked = (
                r"invalid_launcher_registry_receipt|"
                r"launcher_registry_receipt_identity_drift"
            )
            with self.assertRaisesRegex(RuntimeError, blocked):
                updater._rollback_transition(
                    registry_path=registry, lock_path=lock, receipts_path=receipts
                )
            with patch.object(
                updater, "_validated_entry", return_value=_entry_with_sha("C" * 64)
            ):
                with self.assertRaisesRegex(RuntimeError, blocked):
                    updater._replace_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                    )
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                with self.assertRaisesRegex(RuntimeError, blocked):
                    updater._install_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                    )

    def test_rolled_back_that_contradicts_its_committed_cannot_explain_live(self) -> None:
        # Same hole on the new rolled-back reader: matching the live file is
        # not enough if the receipt is not bound to its committed.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            self._abort_then_retry_same_replace(registry, lock, receipts, root)
            updater._rollback_transition(
                registry_path=registry, lock_path=lock, receipts_path=receipts
            )
            rolled_back_path = next(receipts.glob("*/rolled-back.json"))
            committed_path = rolled_back_path.parent / "committed.json"
            rolled_back = json.loads(rolled_back_path.read_text(encoding="utf-8"))
            committed = json.loads(committed_path.read_text(encoding="utf-8"))
            self.assertEqual(rolled_back["to_sha256"], committed["from_sha256"])
            rolled_back["from_sha256"] = "F" * 64
            rolled_back_path.write_bytes(updater._canonical(rolled_back))

            blocked = (
                r"invalid_launcher_registry_receipt|"
                r"launcher_registry_receipt_identity_drift"
            )
            with patch.object(
                updater, "_validated_entry", return_value=_entry_with_sha("C" * 64)
            ):
                with self.assertRaisesRegex(RuntimeError, blocked):
                    updater._replace_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                    )
            with self.assertRaisesRegex(RuntimeError, blocked):
                updater._rollback_transition(
                    registry_path=registry, lock_path=lock, receipts_path=receipts
                )

    def _two_orphan_install_tree(
        self, registry: Path, lock: Path, receipts: Path, root: Path
    ) -> tuple[Path, Path]:
        # Abort the first install before the swap, then the retry after it.
        # Two prepared, zero committed: the first names a dead temp; the second
        # matches the live bytes and identity and is promotable.
        with patch.object(updater, "_validated_entry", return_value=_entry()):
            with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                    fail_at="before_replace",
                )
            with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                    fail_at="after_replace",
                )
        self.assertEqual(len(list(receipts.glob("*/committed.json"))), 0)
        orphans = [
            path.parent
            for path in receipts.glob("*/prepared.json")
            if not (path.parent / "committed.json").exists()
        ]
        self.assertEqual(len(orphans), 2)
        _live_raw, live_identity = updater._read_pinned(registry)
        live_sha = updater._sha256(registry.read_bytes())
        dead = live_tx = None
        for transaction in orphans:
            prepared, _raw = updater._load_prepared(transaction / "prepared.json")
            if (
                prepared["to_sha256"] == live_sha
                and prepared["to_identity"] == live_identity
            ):
                live_tx = transaction
            else:
                dead = transaction
        self.assertIsNotNone(dead)
        self.assertIsNotNone(live_tx)
        return dead, live_tx

    def test_recovery_is_independent_of_orphan_visit_order(self) -> None:
        # F2: visiting the dead-temp orphan before the promotable one must not
        # raise identity_drift. Both directory orders restore the baseline.
        orders = (
            (
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000003",
            ),
            (
                "ffffffff-ffff-ffff-ffff-ffffffffffff",
                "00000000-0000-0000-0000-000000000003",
            ),
        )
        for dead_name, live_name in orders:
            with self.subTest(dead=dead_name, live=live_name):
                with TemporaryDirectory() as directory:
                    root = Path(directory)
                    registry, lock, receipts = self._paths(root)
                    dead, live_tx = self._two_orphan_install_tree(
                        registry, lock, receipts, root
                    )
                    _place_orphan_names(receipts, dead, live_tx, dead_name, live_name)
                    state = updater.describe_registry_provenance(
                        registry_path=registry, lock_path=lock, receipts_path=receipts
                    )
                    restored = updater._rollback_transition(
                        registry_path=registry, lock_path=lock, receipts_path=receipts
                    )
                    self.assertEqual(registry.read_bytes(), BASELINE)
                    self.assertEqual(restored, updater._sha256(BASELINE))
                    self.assertEqual(len(list(receipts.glob("*/committed.json"))), 1)
                    self.assertEqual(state["status"], "stalled")
                    self.assertIs(state["recoverable"], True)

    def test_checker_rejects_blocked_provenance_from_real_receipts(self) -> None:
        # F3: producer (describe_registry_provenance) and consumer
        # (check_native_launcher_registry.main) on the same unexplained
        # rewrite. Bundle/contract checks are stubbed positive so only the
        # provenance seam can turn the checker red.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            with patch.object(updater, "_validated_entry", return_value=_entry()):
                updater._install_transition(
                    registry_path=registry,
                    lock_path=lock,
                    receipts_path=receipts,
                    bundle=root,
                    expected_sha256=updater._sha256(BASELINE),
                )
            replacement = _entry_with_sha("B" * 64)
            with patch.object(updater, "_validated_entry", return_value=replacement):
                with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                    updater._replace_transition(
                        registry_path=registry,
                        lock_path=lock,
                        receipts_path=receipts,
                        bundle=root,
                        expected_sha256=updater._sha256(registry.read_bytes()),
                        fail_at="before_apply",
                    )
            registry.write_bytes(
                updater._canonical({"format_version": 1, "launchers": [replacement]})
            )
            producer = updater.describe_registry_provenance(
                registry_path=registry, lock_path=lock, receipts_path=receipts
            )
            self.assertEqual(producer["status"], "blocked")
            self.assertIs(producer["recoverable"], False)

            code, output = _run_checker_against_fixture(
                registry, lock, receipts, replacement
            )
            self.assertEqual(code, 1)
            self.assertIn("NATIVE LAUNCHER REGISTRY DRIFT", output)
            self.assertIn("blocked", output)
            self.assertNotIn("NATIVE LAUNCHER REGISTRY OK", output)

            with self.assertRaisesRegex(
                RuntimeError, "launcher_registry_receipt_identity_drift"
            ):
                updater._rollback_transition(
                    registry_path=registry, lock_path=lock, receipts_path=receipts
                )

    @requires_built_bundle
    def test_checker_accepts_recoverable_stalled_residue(self) -> None:
        # Structured split: leftover aborted prepared after a successful retry
        # is stalled/recoverable. The checker must not treat that as the lost
        # unanchored rejection; the next transition still proceeds.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry, lock, receipts = self._paths(root)
            sha_b = self._abort_then_retry_same_replace(registry, lock, receipts, root)
            replacement = json.loads(registry.read_text(encoding="utf-8"))["launchers"][0]
            producer = updater.describe_registry_provenance(
                registry_path=registry, lock_path=lock, receipts_path=receipts
            )
            self.assertEqual(producer["status"], "stalled")
            self.assertIs(producer["recoverable"], True)
            code, output = _run_checker_against_fixture(
                registry, lock, receipts, replacement
            )
            self.assertEqual(code, 0)
            self.assertIn("NATIVE LAUNCHER REGISTRY OK", output)
            self.assertIn("stalled", output)
            self.assertEqual(updater._sha256(registry.read_bytes()), sha_b)


def _place_orphan_names(
    receipts: Path, dead: Path, live_tx: Path, dead_name: str, live_name: str
) -> None:
    parked = receipts / "_parked"
    parked.mkdir()
    dead = dead.rename(parked / "dead")
    live_tx = live_tx.rename(parked / "live")
    dead.rename(receipts / dead_name)
    live_tx.rename(receipts / live_name)
    parked.rmdir()


def _load_checker_module():
    path = Path(__file__).resolve().parents[1] / "checks" / "check_native_launcher_registry.py"
    spec = importlib.util.spec_from_file_location("check_native_launcher_registry", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_checker_against_fixture(
    registry: Path,
    lock: Path,
    receipts: Path,
    expected_entry: dict[str, object],
) -> tuple[int, str]:
    checker = _load_checker_module()
    tools_dir = Path(__file__).resolve().parents[1]
    lock_digest = hashlib.sha256((tools_dir / "dependency-lock.json").read_bytes()).hexdigest().upper()
    builder_digest = hashlib.sha256(
        (tools_dir / "build_native_launcher.py").read_bytes()
    ).hexdigest().upper()
    stdout = io.StringIO()
    with (
        patch.object(updater, "_CANONICAL_REGISTRY", registry),
        patch.object(updater, "_CANONICAL_LOCK", lock),
        patch.object(updater, "_CANONICAL_RECEIPTS", receipts),
        patch.object(launcher_registry, "_CANONICAL_REGISTRY", registry),
        patch.object(
            launcher_registry, "_create_registry_entry_for_test", return_value=expected_entry
        ),
        patch.object(build_native_launcher, "verify_bundle"),
        patch.object(
            build_native_launcher,
            "_closed_load",
            return_value={
                "dependency_lock_sha256": lock_digest,
                "builder_sha256": builder_digest,
            },
        ),
        redirect_stdout(stdout),
    ):
        code = checker.main()
    return code, stdout.getvalue()


if __name__ == "__main__":
    unittest.main()
