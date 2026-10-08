"""has_binarizable_assets must not follow junctions or symbolic links (dbe0).

A source that junctions to itself and holds no .p3d/.paa/.rvmat used to be
walked with Path.rglob, which follows directory junctions. The walk is now
bounded: name-surrogate reparse points are not descended into. Cloud
placeholder tags are not name surrogates and stay in the walk.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dayz_mcp import pack_only


# IO_REPARSE_TAG_MOUNT_POINT, IO_REPARSE_TAG_SYMLINK, IO_REPARSE_TAG_CLOUD.
_TAG_MOUNT_POINT = 0xA0000003
_TAG_SYMLINK = 0xA000000C
_TAG_CLOUD = 0x9000001A

_TOOLS_DIR = Path(__file__).resolve().parents[1]
_WALK_TIMEOUT_S = 5


def _make_junction(target: Path, link: Path) -> None:
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def _remove_junction(link: Path) -> None:
    if os.path.lexists(link):
        os.rmdir(link)


class PackOnlyJunctionTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "directory junctions are a Windows feature")
    def test_self_junction_without_assets_returns_false_within_bound(self) -> None:
        """Pre-fix rglob follows the junction. A bounded child must still finish."""
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "scripts").mkdir()
            (source / "scripts" / "bridge.c").write_text("// no assets\n", encoding="utf-8")
            link = source / "loop"
            _make_junction(source, link)
            self.addCleanup(_remove_junction, link)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    "import sys\n"
                    "from dayz_mcp.pack_only import has_binarizable_assets\n"
                    "print(has_binarizable_assets(sys.argv[1]))\n",
                    str(source),
                ],
                cwd=_TOOLS_DIR,
                capture_output=True,
                text=True,
                timeout=_WALK_TIMEOUT_S,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr,
            )
            self.assertEqual(completed.stdout.strip(), "False")
            self.assertFalse(pack_only.has_binarizable_assets(source))

    def test_regular_p3d_returns_true(self) -> None:
        with TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "nested").mkdir()
            (source / "nested" / "body.P3D").write_bytes(b"odol")
            self.assertTrue(pack_only.has_binarizable_assets(source))

    @unittest.skipUnless(os.name == "nt", "directory junctions are a Windows feature")
    def test_outside_junction_is_not_followed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            source = root / "source"
            outside.mkdir()
            source.mkdir()
            (outside / "secret.p3d").write_bytes(b"odol")
            (source / "notes.txt").write_text("scripts only\n", encoding="utf-8")
            link = source / "linked"
            _make_junction(outside, link)
            self.addCleanup(_remove_junction, link)
            self.assertFalse(pack_only.has_binarizable_assets(source))
            (source / "local.paa").write_bytes(b"paa")
            self.assertTrue(pack_only.has_binarizable_assets(source))

    def test_placeholder_tag_is_not_a_name_surrogate(self) -> None:
        self.assertFalse(pack_only._is_name_surrogate_tag(_TAG_CLOUD))
        self.assertFalse(pack_only._is_name_surrogate_tag(0))
        self.assertTrue(pack_only._is_name_surrogate_tag(_TAG_MOUNT_POINT))
        self.assertTrue(pack_only._is_name_surrogate_tag(_TAG_SYMLINK))

        with TemporaryDirectory() as tmp:
            source = Path(tmp)
            cloud = source / "cloud"
            mount = source / "mount"
            cloud.mkdir()
            mount.mkdir()
            (cloud / "model.p3d").write_bytes(b"odol")
            (mount / "hidden.rvmat").write_bytes(b"rvmat")
            real_tag = pack_only._reparse_tag

            def tagged(path: Path) -> int:
                name = Path(path).name
                if name == "cloud":
                    return _TAG_CLOUD
                if name == "mount":
                    return _TAG_MOUNT_POINT
                return real_tag(path)

            with patch.object(pack_only, "_reparse_tag", tagged):
                self.assertTrue(pack_only.has_binarizable_assets(source))
                (cloud / "model.p3d").unlink()
                self.assertFalse(pack_only.has_binarizable_assets(source))


if __name__ == "__main__":
    unittest.main()
