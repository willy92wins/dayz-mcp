from __future__ import annotations

import importlib
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class BootstrapParentAccreditationTests(unittest.TestCase):
    class _Opened:
        path = Path(r"P:\Bundle\dayz-test-launcher.exe")
        sha256 = "A" * 64

        def revalidate(self) -> None:
            return None

        def __enter__(self) -> "BootstrapParentAccreditationTests._Opened":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def test_exact_registered_parent_requires_two_stable_native_snapshots(self) -> None:
        module = importlib.import_module("dayz_mcp.bootstrap_parent")
        self.assertFalse(hasattr(module, "secure_launcher"))
        self.assertTrue(hasattr(module, "launcher_registry"))
        opened = self._Opened()
        snapshots = [
            {
                "identity_complete": True,
                "identity_scheme": "psutil-argv-v2",
                "pid": 4242,
                "creation_time_utc": "2026-07-22T00:00:00Z",
                "executable_sha256": "A" * 64,
                "command_line_sha256": "B" * 64,
            }
        ] * 2
        guard = SimpleNamespace(snapshot=lambda _pid: snapshots.pop(0))

        with (
            patch.object(module.os, "getppid", return_value=4242),
            patch.object(
                module.launcher_registry,
                "open_approved_launcher",
                return_value=opened,
            ) as open_launcher,
            patch.object(
                module.native_process_snapshot,
                "full_image_path_of",
                side_effect=(str(opened.path), str(opened.path)),
            ),
        ):
            module.accredit_registered_bootstrap_parent(guard=guard)

        open_launcher.assert_called_once_with("dayz-test-v1")
        self.assertEqual(snapshots, [])

    def test_parent_path_hash_or_snapshot_drift_fails_closed(self) -> None:
        module = importlib.import_module("dayz_mcp.bootstrap_parent")
        opened = self._Opened()
        base = {
            "identity_complete": True,
            "identity_scheme": "psutil-argv-v2",
            "pid": 4242,
            "creation_time_utc": "2026-07-22T00:00:00Z",
            "executable_sha256": "A" * 64,
            "command_line_sha256": "B" * 64,
        }
        cases = (
            (r"P:\Foreign\python.exe", base, base),
            (str(opened.path), {**base, "executable_sha256": "C" * 64}, base),
            (
                str(opened.path),
                base,
                {**base, "creation_time_utc": "2026-07-22T00:00:01Z"},
            ),
        )
        for parent_path, first, second in cases:
            snapshots = [first, second]
            guard = SimpleNamespace(snapshot=lambda _pid: snapshots.pop(0))
            with (
                self.subTest(parent_path=parent_path, first=first, second=second),
                patch.object(module.os, "getppid", return_value=4242),
                patch.object(
                    module.launcher_registry,
                    "open_approved_launcher",
                    return_value=opened,
                ),
                patch.object(
                    module.native_process_snapshot,
                    "full_image_path_of",
                    side_effect=(parent_path, str(opened.path)),
                ),
                self.assertRaisesRegex(
                    ValueError, "unaccredited_bootstrap_parent"
                ),
            ):
                module.accredit_registered_bootstrap_parent(guard=guard)

    def test_bootstrap_read_closure_does_not_import_launch_capable_module(self) -> None:
        module = importlib.import_module("dayz_mcp.bootstrap_parent")
        registry = importlib.import_module("dayz_mcp.launcher_registry")

        bootstrap_source = inspect.getsource(module)
        registry_source = inspect.getsource(registry)
        self.assertNotIn("secure_launcher", bootstrap_source)
        self.assertNotIn("native_launcher_backend", bootstrap_source)
        self.assertNotIn("secure_launcher", registry_source)
        self.assertNotIn("native_launcher_backend", registry_source)


if __name__ == "__main__":
    unittest.main()
