"""The committed admin catalog (fb-20261028-131118-19f6).

catalog_admin.json must equal what `--tool-pack admin` lists today, written
by fc_bench/export_admin_catalog.py, and its names must equal the pack's
closed list.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

os.environ.setdefault(
    "LOCALAPPDATA", str(Path(tempfile.gettempdir()) / "dayz-mcp-localapp")
)
if sys.platform != "win32":
    import ctypes

    from unittest.mock import MagicMock

    class _FakeWinDLL:
        def __init__(self, name: str, *args: object, **kwargs: object) -> None:
            self._name = name

        def __getattr__(self, name: str) -> MagicMock:
            return MagicMock(name=f"{self._name}.{name}")

    ctypes.WinDLL = _FakeWinDLL  # type: ignore[misc, assignment]
    sys.modules.setdefault(
        "msvcrt",
        types.SimpleNamespace(
            LK_NBLCK=1,
            LK_UNLCK=2,
            get_osfhandle=lambda fd: fd,
            locking=lambda *_a, **_k: None,
        ),
    )

from dayz_mcp import tool_pack

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "fc_bench" / "catalog_admin.json"
EXPORTER = ROOT / "fc_bench" / "export_admin_catalog.py"

_spec = importlib.util.spec_from_file_location("fc_bench_export_admin_catalog", EXPORTER)
export_admin_catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_admin_catalog)


class RealAdminCatalogPinTest(unittest.TestCase):
    def test_committed_catalog_matches_the_code(self) -> None:
        tools = asyncio.run(export_admin_catalog.admin_catalog_tools())
        rendered = export_admin_catalog.render(tools)
        committed = CATALOG.read_text(encoding="utf-8")
        self.assertEqual(
            committed,
            rendered,
            "catalog_admin.json is stale: from tools/, run "
            "python -B -m fc_bench.export_admin_catalog and commit it; "
            "results from the old catalog do not compare",
        )

    def test_committed_catalog_names_equal_the_admin_pack(self) -> None:
        tools = json.loads(CATALOG.read_text(encoding="utf-8"))["tools"]
        self.assertEqual({t["name"] for t in tools}, set(tool_pack.ADMIN_TOOL_NAMES))


if __name__ == "__main__":
    unittest.main()
