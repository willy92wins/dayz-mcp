from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

_TOOLS_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _TOOLS_DIR.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import build_wheel
from tests._tiers import slow_test


def _git_status() -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(
            [git, "status", "--porcelain"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


class BuildWheelTest(unittest.TestCase):
    def test_stage_rejects_a_non_empty_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary) / "stage"
            stage.mkdir()
            (stage / "occupied.txt").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "^wheel_stage_not_empty$"):
                build_wheel.stage_wheel_tree(
                    _TOOLS_DIR,
                    _REPO_ROOT / "playbooks",
                    stage,
                    b"{}",
                )

    def test_build_failure_from_the_runner(self) -> None:
        def fake_run(command, capture_output, text):
            del command, capture_output, text
            return SimpleNamespace(returncode=1, stdout="", stderr="boom\nlast line\n")

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "^wheel_build_failed:"):
                build_wheel.build_wheel(
                    Path(temporary) / "stage",
                    Path(temporary) / "out",
                    run=fake_run,
                )

    def test_build_without_a_wheel_is_a_failure(self) -> None:
        def fake_run(command, capture_output, text):
            del command, capture_output, text
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "out"
            out_dir.mkdir()
            with self.assertRaisesRegex(
                RuntimeError,
                "^wheel_build_failed: expected one wheel, found 0$",
            ):
                build_wheel.build_wheel(
                    Path(temporary) / "stage",
                    out_dir,
                    run=fake_run,
                )

    def test_stage_excludes_pycache_and_loose_pyc_from_playbooks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            playbooks = base / "playbooks-src"
            cache = playbooks / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "x.cpython-314.pyc").write_bytes(b"cached")
            (playbooks / "y.pyc").write_bytes(b"loose")
            (playbooks / "a.toml").write_text("x = 1\n", encoding="utf-8")
            stage = base / "stage"
            build_wheel.stage_wheel_tree(_TOOLS_DIR, playbooks, stage, b"{}")
            staged = stage / "dayz_mcp" / "playbooks"
            self.assertTrue((staged / "a.toml").is_file())
            self.assertFalse((staged / "y.pyc").exists())
            self.assertFalse((staged / "__pycache__").exists())
            self.assertFalse(
                (staged / "__pycache__" / "x.cpython-314.pyc").exists()
            )

    def test_build_failure_message_keeps_the_stderr_tail(self) -> None:
        def fake_run(command, capture_output, text):
            del command, capture_output, text
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="primera línea\nlínea intermedia\núltima línea\n",
            )

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(RuntimeError) as raised:
                build_wheel.build_wheel(
                    Path(temporary) / "stage",
                    Path(temporary) / "out",
                    run=fake_run,
                )
            message = str(raised.exception)
            self.assertTrue(message.startswith("wheel_build_failed: "))
            self.assertIn("última línea", message)
            self.assertEqual(
                message,
                "wheel_build_failed: primera línea\n"
                "línea intermedia\núltima línea",
            )

    def test_build_with_two_wheels_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "out"

            def fake_run(command, capture_output, text):
                del command, capture_output, text
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "uno-1.0-py3-none-any.whl").write_bytes(b"wheel")
                (out_dir / "dos-1.0-py3-none-any.whl").write_bytes(b"wheel")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with self.assertRaises(RuntimeError) as raised:
                build_wheel.build_wheel(
                    Path(temporary) / "stage",
                    out_dir,
                    run=fake_run,
                )
            self.assertEqual(
                str(raised.exception),
                "wheel_build_failed: expected one wheel, found 2",
            )

    @slow_test
    def test_real_wheel_contents(self) -> None:
        version_json = b'{"version": "wheel-test"}\n'
        before = _git_status()
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            stage = base / "stage"
            out_dir = base / "wheelhouse"
            build_wheel.stage_wheel_tree(
                _TOOLS_DIR, _REPO_ROOT / "playbooks", stage, version_json
            )
            wheel = build_wheel.build_wheel(stage, out_dir)
            with zipfile.ZipFile(wheel) as archive:
                names = archive.namelist()
                contents = {name: archive.read(name) for name in names}
            playbooks_stage = stage / "dayz_mcp" / "playbooks"
            staged_bytes = {
                path.relative_to(playbooks_stage).as_posix(): path.read_bytes()
                for path in playbooks_stage.rglob("*")
                if path.is_file()
            }
        after = _git_status()
        if before is not None and after is not None:
            self.assertEqual(after, before)

        for source in sorted((_TOOLS_DIR / "dayz_mcp").glob("*.py")):
            arcname = f"dayz_mcp/{source.name}"
            self.assertIn(arcname, contents)
            self.assertEqual(contents[arcname], source.read_bytes())
        self.assertIn("mcp_capture.py", contents)
        self.assertEqual(
            contents["mcp_capture.py"], (_TOOLS_DIR / "mcp_capture.py").read_bytes()
        )

        in_wheel = {
            name.removeprefix("dayz_mcp/playbooks/"): contents[name]
            for name in contents
            if name.startswith("dayz_mcp/playbooks/")
        }
        self.assertEqual(set(in_wheel), set(staged_bytes))
        for relative, data in staged_bytes.items():
            self.assertEqual(in_wheel[relative], data)

        self.assertEqual(contents["dayz_mcp/VERSION.json"], version_json)

        grab_entries = [
            name
            for name in names
            if name.endswith(".data/data/Lib/site-packages/mcp-grab.ps1")
        ]
        self.assertEqual(len(grab_entries), 1)
        self.assertEqual(
            contents[grab_entries[0]],
            (_TOOLS_DIR / "mcp-grab.ps1").read_bytes(),
        )

        for name in names:
            self.assertNotIn("__pycache__", name)
            self.assertFalse(name.endswith(".pyc"), name)
            self.assertNotIn(".biprivatekey", name)


if __name__ == "__main__":
    unittest.main()
