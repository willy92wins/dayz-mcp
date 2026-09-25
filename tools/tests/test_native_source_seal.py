from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import dayz_test_tool, doctor, launcher_registry, native_bundle, process_lifecycle, server


# Independent contract: all four pins requested by the launcher source seal.
PINS = {
    "dayz_test_readiness_sha256": "dayz_test_readiness.py",
    "dayz_test_request_sha256": "dayz_test_request.py",
    "dayz_test_worker_sha256": "dayz_test_worker.py",
    "native_broker_protocol_sha256": "native_broker_protocol.py",
}


class NativeSourceSealTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = {}
        for key, module in PINS.items():
            raw = (f"# fixture {module}\r\n").encode()
            (self.root / module).write_bytes(raw)
            self.manifest[key] = hashlib.sha256(raw).hexdigest().upper()

    def test_exact_bytes_and_digest_case_are_accepted_without_rewriting_sources(self):
        before = {name: (self.root / name).read_bytes() for name in PINS.values()}
        for lower in (False, True):
            declared = {key: value.lower() if lower else value for key, value in self.manifest.items()}
            self.assertEqual(native_bundle.source_pin_status(declared, source_root=self.root),
                             {"status": "fresh", "failures": []})
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in PINS.values()})

    def test_each_stale_pin_names_key_file_and_reason(self):
        for key, module in PINS.items():
            with self.subTest(key=key):
                stale = {**self.manifest, key: "0" * 64}
                self.assertEqual(native_bundle.source_pin_status(stale, source_root=self.root), {
                    "status": "stale", "failures": [{"key": key, "file": module, "reason": "sha256_mismatch"}],
                })

    def test_missing_malformed_pins_missing_source_and_eol_drift_are_not_fresh(self):
        key, module = next(iter(PINS.items()))
        for value in (None, "", "z" * 64, 12):
            with self.subTest(value=value):
                result = native_bundle.source_pin_status({**self.manifest, key: value}, source_root=self.root)
                self.assertEqual(result["failures"], [{"key": key, "file": module, "reason": "invalid_pin"}])
        missing = dict(self.manifest)
        del missing[key]
        self.assertEqual(native_bundle.source_pin_status(missing, source_root=self.root)["status"], "stale")
        target = self.root / module
        target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n"))
        self.assertEqual(native_bundle.source_pin_status(self.manifest, source_root=self.root)["failures"][0]["reason"], "sha256_mismatch")
        target.unlink()
        self.assertEqual(native_bundle.source_pin_status(self.manifest, source_root=self.root)["failures"],
                         [{"key": key, "file": module, "reason": "source_unreadable"}])

    def test_loader_reports_pin_before_missing_bundle_artifacts(self):
        # Keep the real source-pin checker and loader; isolate only registry/PE and
        # external-entry policy, which are unrelated to the pin rejection.
        key = "dayz_test_worker_sha256"
        source_root = Path(native_bundle.__file__).parent
        pins = {name: hashlib.sha256((source_root / module).read_bytes()).hexdigest().upper()
                for name, module in PINS.items()}
        pins[key] = "0" * 64
        manifest_path = self.root / "closure-manifest.json"
        manifest_path.write_bytes((json.dumps(pins, sort_keys=True, separators=(",", ":")) + "\n").encode())
        opened = object.__new__(launcher_registry._OpenedLauncher)
        opened.root = self.root
        with (
            patch.object(launcher_registry._OpenedLauncher, "revalidate"),
            patch.object(native_bundle, "_parse_manifest", return_value=([], pins)),
            patch.object(native_bundle, "_open_verified_file") as closure,
        ):
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                with server._typed_dayz_test_value_errors():
                    native_bundle.load_verified_bundle(opened)
        self.assertEqual(caught.exception.code,
                         f"invalid_native_launcher_bundle__{key}")
        closure.assert_not_called()
        # A pinned manifest handle must be released on rejection (Windows delete).
        manifest_path.unlink()

    def test_each_pin_failure_survives_mcp_error_typing(self):
        source_root = Path(native_bundle.__file__).parent
        pins = {key: hashlib.sha256((source_root / module).read_bytes()).hexdigest().upper()
                for key, module in PINS.items()}
        for key in PINS:
            with self.subTest(key=key):
                with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                    with server._typed_dayz_test_value_errors():
                        native_bundle._require_source_pins({**pins, key: "0" * 64})
                self.assertEqual(caught.exception.code, f"invalid_native_launcher_bundle__{key}")

    def write_installed_manifest(self, *, stale=False):
        source_root = Path(native_bundle.__file__).parent
        pins = {name: hashlib.sha256((source_root / module).read_bytes()).hexdigest().upper()
                for name, module in PINS.items()}
        if stale:
            pins["dayz_test_worker_sha256"] = "0" * 64
        pins["entries"] = []
        (self.root / "closure-manifest.json").write_bytes(
            (json.dumps(pins, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )

    def test_installed_diagnostic_and_doctor_use_approved_manifest_and_name_drift(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                self.write_installed_manifest(stale=stale)
                with patch.object(launcher_registry, "open_approved_launcher",
                                  side_effect=lambda _: nullcontext(SimpleNamespace(root=self.root))) as approved:
                    result = native_bundle.installed_source_pin_status()
                    findings = []
                    doctor._check_native_bundle_closure(SimpleNamespace(native_launcher_id="dayz-test-v1"), findings)
                self.assertEqual(result["status"], "stale" if stale else "fresh")
                self.assertTrue(all(call.args == ("dayz-test-v1",) for call in approved.call_args_list))
                self.assertEqual(findings[0]["code"], "NATIVE_BUNDLE_SOURCE_PIN_DRIFT" if stale else "NATIVE_BUNDLE_SOURCE_PINS_OK")
                self.assertEqual(findings[0]["severity"], "FAIL" if stale else "INFO")
                if stale:
                    self.assertEqual(findings[0]["failures"], [{
                        "key": "dayz_test_worker_sha256", "file": "dayz_test_worker.py", "reason": "sha256_mismatch",
                    }])

    def test_missing_or_invalid_installed_manifest_is_unavailable(self):
        for raw in (None, b"{}", b"not-json"):
            manifest = self.root / "closure-manifest.json"
            if raw is not None:
                manifest.write_bytes(raw)
            with patch.object(launcher_registry, "open_approved_launcher",
                              return_value=nullcontext(SimpleNamespace(root=self.root))):
                result = native_bundle.installed_source_pin_status()
            self.assertEqual(result["status"], "unavailable")
        with patch.object(launcher_registry, "open_approved_launcher",
                          side_effect=RuntimeError("invalid_launcher_registry_lock")):
            self.assertEqual(native_bundle.installed_source_pin_status()["status"], "unavailable")

    def test_lifecycle_start_warns_once_with_key_and_file_and_never_reseals(self):
        self.write_installed_manifest(stale=True)
        before = (self.root / "closure-manifest.json").read_bytes()
        with (
            patch.object(launcher_registry, "open_approved_launcher",
                         return_value=nullcontext(SimpleNamespace(root=self.root))),
            self.assertLogs(process_lifecycle.__name__, level="WARNING") as captured,
        ):
            process_lifecycle.ProcessLifecycle(
                coordinator=SimpleNamespace(), manifest=SimpleNamespace(), audit=lambda _: None,
                guard=SimpleNamespace(), retail_probe=None, game_path=self.root,
                steam_gate=SimpleNamespace(),
            )
        self.assertEqual(len(captured.output), 1)
        self.assertIn("dayz_test_worker_sha256", captured.output[0])
        self.assertIn("dayz_test_worker.py", captured.output[0])
        self.assertEqual(before, (self.root / "closure-manifest.json").read_bytes())

    def test_lifecycle_start_with_matching_pins_does_not_warn(self):
        self.write_installed_manifest()
        with (
            patch.object(launcher_registry, "open_approved_launcher",
                         return_value=nullcontext(SimpleNamespace(root=self.root))),
            self.assertNoLogs(process_lifecycle.__name__, level="WARNING"),
        ):
            process_lifecycle.ProcessLifecycle(
                coordinator=SimpleNamespace(), manifest=SimpleNamespace(), audit=lambda _: None,
                guard=SimpleNamespace(), retail_probe=None, game_path=self.root,
                steam_gate=SimpleNamespace(),
            )


if __name__ == "__main__":
    unittest.main()
