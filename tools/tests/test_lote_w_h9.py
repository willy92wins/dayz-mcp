"""Lote W regression tests for h9_native_probe."""
from __future__ import annotations

import ast
import inspect
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import contextlib

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

# h9_native_probe.py is a host-local probe (never published, untracked in the repo): a
# checkout without it skips this module instead of failing at import.
if not (_TOOLS_DIR / "h9_native_probe.py").is_file():
    raise unittest.SkipTest("h9_native_probe.py is not present in this tree")

from dayz_mcp import native_launcher_backend as nlb
import h9_native_probe as h9


class H9NativeProbeTests(unittest.TestCase):
    def test_launch_registered_native_passes_all_required_keyword_args(self) -> None:
        sig = inspect.signature(nlb.launch_registered_native)
        required_kw = {
            name
            for name, param in sig.parameters.items()
            if param.kind is inspect.Parameter.KEYWORD_ONLY
            and param.default is inspect.Parameter.empty
        }
        tree = ast.parse((_TOOLS_DIR / "h9_native_probe.py").read_text(encoding="utf-8"))
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name == "launch_registered_native":
                    calls.append(node)
        passed: set[str] = set()
        for call in calls:
            passed |= {keyword.arg for keyword in call.keywords if keyword.arg}
        self.assertTrue(calls)
        self.assertLessEqual(required_kw, passed)

    def test_main_does_not_fold_typeerror_into_bundle_code(self) -> None:
        async def boom(*_args, **_kwargs):
            raise TypeError(
                "missing 1 required keyword-only argument: 'daemon_policy_json'"
            )

        buf = io.StringIO()
        with patch.object(h9, "_run", boom), patch.object(
            sys, "argv", ["h9_native_probe.py", "preflight"]
        ):
            with contextlib.redirect_stdout(buf):
                try:
                    rc: object = h9.main()
                except TypeError:
                    rc = "raised"
        out = buf.getvalue()
        self.assertTrue(
            rc == "raised"
            or (
                "probe_internal_error" in out
                and "invalid_native_launcher_bundle" not in out
            ),
            f"rc={rc!r} out={out!r}",
        )


if __name__ == "__main__":
    unittest.main()
