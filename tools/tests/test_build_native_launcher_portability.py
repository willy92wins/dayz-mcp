"""The launcher build works when the SDK, %TEMP% and the clone are on different drives (#93)."""

from __future__ import annotations

import errno
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import build_native_launcher

VERSION = "10.0.99999.0"


def _lock(include: str, lib: str) -> dict[str, object]:
    msvc_bin = r"F:\VS\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64"
    return {
        "toolchains": {
            "msvc": {
                "files": {
                    "cl": {"path": msvc_bin + r"\cl.exe"},
                    "link": {"path": msvc_bin + r"\link.exe"},
                }
            },
            "windows_sdk": {
                "version": VERSION,
                "files": {},
                "trees": {"include": {"path": include}, "lib": {"path": lib}},
            },
        }
    }


def _run_compile(lock: dict[str, object]) -> list[list[str]]:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(list(command))
        return SimpleNamespace(returncode=0, stdout="")

    with tempfile.TemporaryDirectory() as staging, patch.object(
        build_native_launcher.subprocess, "run", fake_run
    ):
        build_native_launcher._compile(Path(staging), lock)
    return commands


class CompileUsesTheLockedSdkTreesTest(unittest.TestCase):
    def test_an_sdk_on_another_drive_reaches_compile_and_link(self) -> None:
        include = rf"F:\Kits\10\Include\{VERSION}"
        lib = rf"F:\Kits\10\Lib\{VERSION}"

        compile_command, link_command = _run_compile(_lock(include, lib))

        for folder in ("um", "shared", "ucrt"):
            self.assertIn(rf"/I{include}\{folder}", compile_command)
        self.assertIn(rf"/LIBPATH:{lib}\um\x64", link_command)
        self.assertFalse(
            any("Windows Kits" in part for part in compile_command + link_command)
        )

    def test_trees_of_another_sdk_version_are_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "windows_sdk_lock_version_mismatch"):
            _run_compile(
                _lock(r"F:\Kits\10\Include\10.0.11111.0", rf"F:\Kits\10\Lib\{VERSION}")
            )


class PublishStaysOnTheOutputVolumeTest(unittest.TestCase):
    """Staging under %TEMP% (one volume), output beside the clone (another)."""

    def setUp(self) -> None:
        temp_volume = tempfile.TemporaryDirectory()
        clone_volume = tempfile.TemporaryDirectory()
        self.addCleanup(temp_volume.cleanup)
        self.addCleanup(clone_volume.cleanup)
        self.temp_root = Path(temp_volume.name)
        self.clone_root = Path(clone_volume.name)
        self.staging = self.temp_root / "dayz-test-v1"
        self.staging.mkdir()
        for name in ("app.pyz", "closure-manifest.json", "dayz-test-launcher.exe", "request-policy.json"):
            (self.staging / name).write_bytes(name.encode() + b" new")
        (self.staging / "runtime").mkdir()
        (self.staging / "runtime" / "python.dll").write_bytes(b"dll")
        self.output = self.clone_root / "native-launchers" / "dayz-test-v1"
        self.fingerprint = build_native_launcher._artifact_fingerprint(self.staging)
        real_replace = os.replace

        def replace_on_one_volume(source: object, destination: object) -> None:
            roots = {
                root
                for root in (self.temp_root, self.clone_root)
                for path in (Path(source), Path(destination))
                if path.is_relative_to(root)
            }
            if len(roots) != 1:
                raise OSError(errno.EXDEV, "The system cannot move the file to a different disk drive")
            real_replace(source, destination)

        for patcher in (
            patch.object(build_native_launcher.os, "replace", replace_on_one_volume),
            patch.object(build_native_launcher, "verify_bundle", lambda *_a, **_k: None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_bundle_staged_on_another_volume_replaces_the_output(self) -> None:
        self.output.mkdir(parents=True)
        (self.output / "app.pyz").write_bytes(b"old")

        build_native_launcher._publish_bundle(self.staging, self.output, self.fingerprint)

        self.assertEqual((self.output / "app.pyz").read_bytes(), b"app.pyz new")
        self.assertEqual((self.output / "runtime" / "python.dll").read_bytes(), b"dll")
        self.assertEqual(
            sorted(path.name for path in self.output.parent.iterdir()), ["dayz-test-v1"]
        )

    def test_a_publish_stopped_between_its_renames_is_restored_first(self) -> None:
        # Review of #114, F2: output gone and .previous holding the last bundle.
        # A retry whose copy fails must not lose it.
        previous = self.output.with_name(self.output.name + ".previous")
        previous.mkdir(parents=True)
        (previous / "app.pyz").write_bytes(b"old")

        with patch.object(
            build_native_launcher.shutil, "copytree", side_effect=OSError("copy failed")
        ):
            with self.assertRaisesRegex(OSError, "copy failed"):
                build_native_launcher._publish_bundle(
                    self.staging, self.output, self.fingerprint
                )

        self.assertEqual((self.output / "app.pyz").read_bytes(), b"old")
        self.assertEqual(
            sorted(path.name for path in self.output.parent.iterdir()), ["dayz-test-v1"]
        )

    def test_a_copy_that_fails_halfway_leaves_no_incoming_behind(self) -> None:
        self.output.mkdir(parents=True)
        (self.output / "app.pyz").write_bytes(b"old")

        def half_copy(_source: object, destination: object, **_kwargs: object) -> None:
            Path(destination).mkdir()
            (Path(destination) / "app.pyz").write_bytes(b"partial")
            raise OSError("disk full")

        with patch.object(build_native_launcher.shutil, "copytree", half_copy):
            with self.assertRaisesRegex(OSError, "disk full"):
                build_native_launcher._publish_bundle(
                    self.staging, self.output, self.fingerprint
                )

        self.assertEqual((self.output / "app.pyz").read_bytes(), b"old")
        self.assertEqual(
            sorted(path.name for path in self.output.parent.iterdir()), ["dayz-test-v1"]
        )

    def test_a_copy_that_is_not_the_staged_artifact_leaves_the_output_alone(self) -> None:
        self.output.mkdir(parents=True)
        (self.output / "app.pyz").write_bytes(b"old")
        wrong = dict(self.fingerprint, app_pyz_sha256="0" * 64)

        with self.assertRaisesRegex(ValueError, "final_copy_mismatch"):
            build_native_launcher._publish_bundle(self.staging, self.output, wrong)

        self.assertEqual((self.output / "app.pyz").read_bytes(), b"old")
        self.assertEqual(
            sorted(path.name for path in self.output.parent.iterdir()), ["dayz-test-v1"]
        )


if __name__ == "__main__":
    unittest.main()
