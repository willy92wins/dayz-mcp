from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dayz_mcp import launcher_registry_update as updater


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


if __name__ == "__main__":
    unittest.main()
