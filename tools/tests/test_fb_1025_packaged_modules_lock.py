from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import build_native_launcher
from write_packaged_modules_lock import (
    DRIFT_RECIPE,
    LOCK_PATH,
    REPO_ROOT,
    compute_lock,
    expected_paths,
    main,
    normalize_crlf_to_lf,
    problems,
    render,
)
from tests._tiers import slow_test


def _committed_files() -> dict[str, object]:
    payload = json.loads(LOCK_PATH.read_bytes().decode("utf-8"))
    if type(payload) is not dict:
        return {}
    files = payload.get("files")
    if type(files) is not dict:
        return {}
    return files


class PackagedModulesLockTest(unittest.TestCase):
    def test_fb_1025_lock_paths_equal_expected_paths(self) -> None:
        files = _committed_files()
        locked_paths = sorted(files)
        expected = expected_paths()
        missing = [path for path in expected if path not in files]
        unexpected = [path for path in locked_paths if path not in expected]
        self.assertEqual(
            locked_paths,
            expected,
            "missing paths: {missing}; unexpected paths: {unexpected}".format(
                missing=", ".join(missing),
                unexpected=", ".join(unexpected),
            ),
        )

    def test_fb_1025_lock_hashes_equal_current_file_bytes(self) -> None:
        files = _committed_files()
        drifted: list[str] = []
        for relative in expected_paths():
            location = REPO_ROOT / relative
            current = None
            if location.is_file():
                current = hashlib.sha256(normalize_crlf_to_lf(location.read_bytes())).hexdigest().upper()
            if files.get(relative) != current:
                drifted.append(relative)
        self.assertEqual(
            drifted,
            [],
            "drifted paths: {paths}\n{recipe}".format(
                paths=", ".join(drifted),
                recipe=DRIFT_RECIPE,
            ),
        )

    def test_fb_1025_main_check_returns_zero_on_this_tree(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--check"])
        output = buffer.getvalue()
        self.assertEqual(code, 0, output)
        self.assertEqual(output, "packaged modules lock ok\n")

    def test_fb_1025_crlf_working_copy_matches_the_lock(self) -> None:
        committed = json.loads(LOCK_PATH.read_bytes().decode("utf-8"))
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for relative in expected_paths():
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                lf_bytes = (REPO_ROOT / relative).read_bytes()
                crlf_bytes = lf_bytes.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                destination.write_bytes(crlf_bytes)
            self.assertEqual(compute_lock(root=root), committed)
            edited = expected_paths()[0]
            location = root / edited
            lines = location.read_bytes().split(b"\r\n")
            lines[0] = lines[0] + b"#"
            location.write_bytes(b"\r\n".join(lines))
            self.assertIn(
                "{path} differs from tools/packaged-modules.lock.json".format(path=edited),
                problems(committed, compute_lock(root=root)),
            )

    @slow_test
    def test_fb_1025_normalization_keeps_lone_cr(self) -> None:
        self.assertEqual(normalize_crlf_to_lf(b"a\rb\r\nc\n"), b"a\rb\nc\n")
        edited = expected_paths()[0]
        with tempfile.TemporaryDirectory() as raw_a, tempfile.TemporaryDirectory() as raw_b:
            root_a = Path(raw_a)
            root_b = Path(raw_b)
            for relative in expected_paths():
                data = (REPO_ROOT / relative).read_bytes()
                for root in (root_a, root_b):
                    destination = root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
            (root_b / edited).write_bytes((root_b / edited).read_bytes() + b"\r")
            lock_a = compute_lock(root=root_a)
            lock_b = compute_lock(root=root_b)
            self.assertNotEqual(lock_a["files"][edited], lock_b["files"][edited])

    @slow_test
    def test_fb_1025_check_fails_safe(self) -> None:
        lock_relative = "tools/packaged-modules.lock.json"
        one_source = "tools/native-launchers/dayz-test-v1/src/app_main.py"
        two_sources = [
            "tools/native-launchers/dayz-test-v1/src/app_main.py",
            "tools/native-launchers/dayz-test-v1/src/launcher.cpp",
        ]
        denied = "tools/dayz_mcp/win32_fileinfo.py"

        def run_check(root: Path) -> tuple[int, str]:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main(["--check"], root=root)
            return code, buffer.getvalue()

        def assert_safe_failure(code: int, output: str, *paths: str) -> None:
            self.assertEqual(code, 1, output)
            self.assertIn(DRIFT_RECIPE, output)
            self.assertNotIn("Traceback", output)
            for path in paths:
                self.assertIn(path, output)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_locked_tree(root)
            (root / lock_relative).unlink()
            code, output = run_check(root)
            assert_safe_failure(code, output, lock_relative)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_locked_tree(root)
            (root / lock_relative).write_bytes(b"{not json")
            code, output = run_check(root)
            assert_safe_failure(code, output, lock_relative)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_locked_tree(root)
            (root / lock_relative).write_bytes(b"[]")
            code, output = run_check(root)
            assert_safe_failure(code, output, lock_relative)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_locked_tree(root)
            payload = compute_lock(root=root)
            payload["format_version"] = 2
            (root / lock_relative).write_bytes(render(payload))
            code, output = run_check(root)
            assert_safe_failure(code, output, lock_relative)
            self.assertIn(f"{lock_relative} has format_version 2, expected 1", output)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_locked_tree(root)
            (root / one_source).unlink()
            code, output = run_check(root)
            assert_safe_failure(code, output, one_source)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_locked_tree(root)
            for relative in two_sources:
                (root / relative).unlink()
            code, output = run_check(root)
            assert_safe_failure(code, output, *two_sources)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_locked_tree(root)
            blocked = (root / denied).resolve()
            original_read = Path.read_bytes

            def patched_read(self: Path) -> bytes:
                try:
                    same = self.resolve() == blocked
                except OSError:
                    same = False
                if same:
                    raise PermissionError("denied")
                return original_read(self)

            with patch.object(Path, "read_bytes", patched_read):
                code, output = run_check(root)
            assert_safe_failure(code, output, denied)

    def test_fb_1025_write_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_locked_tree(root)
            lock_path = root / "tools" / "packaged-modules.lock.json"
            previous = lock_path.read_bytes()
            tools_dir = root / "tools"

            def leftovers() -> list[str]:
                return [
                    name
                    for name in os.listdir(tools_dir)
                    if name.startswith(".packaged-modules.lock.") and name.endswith(".tmp")
                ]

            buffer = io.StringIO()
            with patch("os.replace", side_effect=OSError("replace failed")):
                with redirect_stdout(buffer):
                    code = main([], root=root)
            self.assertEqual(code, 1, buffer.getvalue())
            self.assertEqual(lock_path.read_bytes(), previous)
            self.assertEqual(leftovers(), [])

            lock_path.write_bytes(b"{}\n")
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main([], root=root)
            self.assertEqual(code, 0, buffer.getvalue())
            self.assertEqual(lock_path.read_bytes(), render(compute_lock(root=root)))
            self.assertEqual(leftovers(), [])

    def test_fb_1025_lock_covers_what_the_builder_reads(self) -> None:
        recorded: set[Path] = set()
        original_io_open = io.open
        original_builtin_open = builtins.open

        def wrap(original):
            def wrapped(file, mode="r", *args, **kwargs):
                mode_text = "r" if mode is None else str(mode)
                if not any(flag in mode_text for flag in ("w", "x", "a", "+")):
                    try:
                        recorded.add(Path(os.fspath(file)).resolve())
                    except (TypeError, ValueError, OSError):
                        pass
                return original(file, mode, *args, **kwargs)

            return wrapped

        wrapped_io = wrap(original_io_open)
        repo = REPO_ROOT.resolve()
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw) / "app.pyz"
            io.open = wrapped_io
            if original_builtin_open is original_io_open:
                builtins.open = wrapped_io
            else:
                builtins.open = wrap(original_builtin_open)
            try:
                build_native_launcher._build_app_pyz(destination)
            finally:
                io.open = original_io_open
                builtins.open = original_builtin_open
        relatives: set[str] = set()
        for path in recorded:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved == repo or repo in resolved.parents:
                relatives.add(resolved.relative_to(repo).as_posix())
        launcher = "tools/native-launchers/dayz-test-v1/src/launcher.cpp"
        expected_reads = set(expected_paths()) - {launcher}
        missing = sorted(expected_reads - relatives)
        unexpected = sorted(relatives - expected_reads)
        self.assertEqual(
            relatives,
            expected_reads,
            "missing paths: {missing}; unexpected paths: {unexpected}".format(
                missing=", ".join(missing),
                unexpected=", ".join(unexpected),
            ),
        )
        self.assertIn(launcher, expected_paths())
        self.assertTrue((REPO_ROOT / launcher).is_file())


def _copy_locked_tree(root: Path) -> None:
    for relative in expected_paths():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO_ROOT / relative).read_bytes())
    lock_destination = root / "tools" / "packaged-modules.lock.json"
    lock_destination.parent.mkdir(parents=True, exist_ok=True)
    lock_destination.write_bytes(LOCK_PATH.read_bytes())


if __name__ == "__main__":
    unittest.main()
