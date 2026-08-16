from __future__ import annotations

import importlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests import test_dependency_lock as lock_contract


TOOLS_DIR = Path(__file__).resolve().parents[1]


def _fake_toolchain_roots(root: Path) -> tuple[Path, Path, str]:
    relock = importlib.import_module("relock_toolchain")
    msvc = root / "MSVC" / "14.99.11111"
    for parts in relock._MSVC_FILES.values():
        target = msvc.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake-" + target.name.encode("utf-8"))
    (msvc / "include").mkdir(parents=True, exist_ok=True)
    (msvc / "include" / "example.h").write_bytes(b"// header\n")
    sdk = root / "Kits10"
    version = "10.0.99999.0"
    um = sdk / "Lib" / version / "um" / "x64"
    um.mkdir(parents=True)
    for filename in relock._SDK_FILES.values():
        (um / filename).write_bytes(b"fake-" + filename.encode("utf-8"))
    include = sdk / "Include" / version
    include.mkdir(parents=True)
    (include / "windows.h").write_bytes(b"// sdk header\n")
    return msvc, sdk, version


class RelockToolchainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.relock = importlib.import_module("relock_toolchain")

    def test_tree_digest_matches_the_contract_the_suite_verifies(self) -> None:
        # The lock's tree digests are checked by test_dependency_lock with its
        # own implementation; a relock that hashed differently would write
        # pins the suite immediately rejects.
        with TemporaryDirectory() as directory:
            tree = Path(directory) / "tree"
            (tree / "sub").mkdir(parents=True)
            (tree / "B.txt").write_bytes(b"upper")
            (tree / "a.txt").write_bytes(b"lower")
            (tree / "sub" / "c.bin").write_bytes(b"\x00\x01")
            self.assertEqual(
                self.relock._tree_digest(tree), lock_contract._tree_digest(tree)
            )

    def test_collect_produces_schema_valid_toolchains_and_rewrite_keeps_pins(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            msvc, sdk, version = _fake_toolchain_roots(root)
            toolchains = self.relock.collect_toolchains(
                msvc, "19.99.11111", sdk, version
            )
            self.assertEqual(set(toolchains), {"msvc", "windows_sdk"})
            self.assertEqual(set(toolchains["msvc"]["files"]), set(self.relock._MSVC_FILES))
            self.assertEqual(toolchains["msvc"]["root_version"], "14.99.11111")
            self.assertEqual(toolchains["windows_sdk"]["version"], version)

            lock_copy = root / "dependency-lock.json"
            lock_copy.write_bytes((TOOLS_DIR / "dependency-lock.json").read_bytes())
            before = json.loads(lock_copy.read_text(encoding="utf-8"))
            old_sha, new_sha = self.relock.rewrite_lock(lock_copy, toolchains)
            self.assertNotEqual(old_sha, new_sha)

            raw = lock_copy.read_bytes()
            after = json.loads(raw.decode("utf-8"))
            self.assertEqual(raw, self.relock._canonical_bytes(after))
            self.assertEqual(after["artifacts"], before["artifacts"])
            self.assertEqual(after["remote_artifacts"], before["remote_artifacts"])
            self.assertEqual(after["toolchains"], toolchains)
            lock_contract._validate_lock(after)

    def test_rewrite_refuses_a_lock_with_the_wrong_shape(self) -> None:
        with TemporaryDirectory() as directory:
            bad = Path(directory) / "dependency-lock.json"
            bad.write_text('{"format_version": 2}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "dependency_lock_invalid"):
                self.relock.rewrite_lock(bad, {})


if __name__ == "__main__":
    unittest.main()
