from __future__ import annotations

import ast
import importlib
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class NativeProcessSnapshotTests(unittest.TestCase):
    def test_orphan_guard_reexports_the_extracted_read_only_helpers(self) -> None:
        spec = importlib.util.find_spec("dayz_mcp.native_process_snapshot")
        self.assertIsNotNone(
            spec, "dayz_mcp.native_process_snapshot is not implemented"
        )
        snapshot = importlib.import_module("dayz_mcp.native_process_snapshot")
        orphan_guard = importlib.import_module("dayz_mcp.orphan_guard")

        self.assertIs(orphan_guard.full_image_path_of, snapshot.full_image_path_of)
        self.assertIs(orphan_guard.command_argv_of, snapshot.command_argv_of)
        self.assertIs(orphan_guard.working_directory_of, snapshot.working_directory_of)
        self.assertIs(orphan_guard._same_path, snapshot.same_path)

    def test_snapshot_module_has_no_process_mutation_surface(self) -> None:
        spec = importlib.util.find_spec("dayz_mcp.native_process_snapshot")
        self.assertIsNotNone(
            spec, "dayz_mcp.native_process_snapshot is not implemented"
        )
        source_path = Path(spec.origin or "")
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        called_names = {
            node.func.attr.casefold()
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertNotIn("subprocess", imported_roots)
        self.assertTrue(
            called_names.isdisjoint(
                {
                    "createprocessw",
                    "kill",
                    "spawn",
                    "terminate",
                    "terminateprocess",
                }
            ),
            called_names,
        )

    def test_psutil_readers_preserve_exact_argv_and_fail_closed(self) -> None:
        snapshot = importlib.import_module("dayz_mcp.native_process_snapshot")

        class FakeProcess:
            pid = 42

            def oneshot(self) -> object:
                class Context:
                    def __enter__(self) -> None:
                        return None

                    def __exit__(self, *_args: object) -> None:
                        return None

                return Context()

            def cmdline(self) -> list[str]:
                return [r"P:\Runtime\python.exe", "-m", "dayz_mcp", "--daemon"]

            def cwd(self) -> str:
                return r"P:\DayZ_MCP_dev\tools"

        fake_psutil = SimpleNamespace(Process=lambda pid: FakeProcess())
        with patch.object(snapshot, "psutil", fake_psutil):
            self.assertEqual(
                snapshot.command_argv_of(42),
                [r"P:\Runtime\python.exe", "-m", "dayz_mcp", "--daemon"],
            )
            self.assertEqual(
                snapshot.working_directory_of(42), r"P:\DayZ_MCP_dev\tools"
            )
            self.assertIsNone(snapshot.command_argv_of(True))
            self.assertIsNone(snapshot.working_directory_of(0))


if __name__ == "__main__":
    unittest.main()
