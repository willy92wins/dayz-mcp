"""pack-addon.ps1 must pass -packonly when the source has no binarizable assets.

fb-20260915-005408-bcd8: AddonBuilder binarize uses -addon=P: and dies if any
config.cpp under P:\\ fails to parse. The worker already auto-sets pack_only
when the tree has no .p3d/.paa/.rvmat; the pack script must mirror that.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dayz_mcp import pack_only
from dayz_mcp.dayz_test_worker import _default_has_assets
from tests._addon_paths import addon_root


TOOLS_DIR = Path(__file__).resolve().parents[1]
PACK_PS1 = TOOLS_DIR / "pack-addon.ps1"
WORKER = TOOLS_DIR / "dayz_mcp" / "dayz_test_worker.py"


class PackOnlyPredicateTest(unittest.TestCase):
    def test_suffix_set_matches_worker_and_launcher(self) -> None:
        self.assertEqual(pack_only.BINARIZABLE_SUFFIXES, {".p3d", ".paa", ".rvmat"})

    def test_empty_tree_passes_packonly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "scripts").mkdir()
            Path(tmp, "scripts", "bridge.c").write_text("// scripts only\n", encoding="utf-8")
            self.assertFalse(pack_only.has_binarizable_assets(tmp))
            self.assertTrue(pack_only.should_pack_only(tmp))
            self.assertEqual(pack_only.addon_builder_packonly_args(tmp), ("-packonly",))

    def test_explicit_pack_only_wins_even_with_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "model.p3d").write_bytes(b"odol")
            self.assertTrue(pack_only.has_binarizable_assets(tmp))
            self.assertEqual(
                pack_only.addon_builder_packonly_args(tmp, pack_only=True),
                ("-packonly",),
            )
            self.assertEqual(pack_only.addon_builder_packonly_args(tmp), ())

    def test_each_binarizable_suffix_disables_packonly(self) -> None:
        for suffix in pack_only.BINARIZABLE_SUFFIXES:
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as tmp:
                Path(tmp, f"asset{suffix}").write_bytes(b"x")
                self.assertTrue(pack_only.has_binarizable_assets(tmp))
                self.assertEqual(pack_only.addon_builder_packonly_args(tmp), ())

    def test_repo_addon_tree_is_scripts_only(self) -> None:
        source = addon_root()
        self.assertTrue(source.is_dir(), source)
        self.assertFalse(pack_only.has_binarizable_assets(source))
        self.assertFalse(_default_has_assets(str(source)))
        self.assertEqual(pack_only.addon_builder_packonly_args(source), ("-packonly",))

    def test_worker_default_scan_matches_shared_predicate(self) -> None:
        worker = WORKER.read_text(encoding="utf-8")
        self.assertIn("pack_only.has_binarizable_assets", worker)
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "notes.txt").write_text("no assets\n", encoding="utf-8")
            self.assertEqual(
                pack_only.has_binarizable_assets(tmp),
                _default_has_assets(tmp),
            )
            Path(tmp, "skin.paa").write_bytes(b"paa")
            self.assertEqual(
                pack_only.has_binarizable_assets(tmp),
                _default_has_assets(tmp),
            )


class PackAddonScriptContractTest(unittest.TestCase):
    def test_ps1_mirrors_worker_predicate_and_passes_packonly(self) -> None:
        text = PACK_PS1.read_text(encoding="utf-8")
        self.assertIn("fb-20260915-005408-bcd8", text)
        self.assertIn("Test-HasBinarizableAssets", text)
        for suffix in (".p3d", ".paa", ".rvmat"):
            self.assertIn(f"'{suffix}'", text)
        self.assertIn('$args += "-packonly"', text)
        self.assertLess(
            text.index('$args += "-packonly"'),
            text.index("& $builder @args"),
            "-packonly must be appended before AddonBuilder is invoked",
        )
        self.assertIn("PackOnly", text)
        self.assertIn("dayz_mcp.pack_only", text)


if __name__ == "__main__":
    unittest.main()
