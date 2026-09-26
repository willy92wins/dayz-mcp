# -*- coding: utf-8 -*-
r"""External oracle for lote W-tools: five products outside server.py.

Run:  cd <ws>/tools && PYTHONPATH=. <venv-python> ../gate/oracle.py

Design notes:
- Self-contained: imports nothing from tools/tests/ except the modules under test, by name.
- Expectations are literal. Seams (function names) are contracts the brief names; the method
  behind them is the implementer's.
- A check that cannot reach its subject reports UNMET, never PASS.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib
import inspect
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def unmet(name: str, detail: str) -> None:
    RESULTS.append((name, "UNMET", detail))


TOOLS = Path.cwd()
REMEDY_TOKENS = ("rollback-last", "install-dayz-test-v1")


def main() -> int:
    # ------------------------------------------------ W7 test_effective_schema collected + green
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_effective_schema")
        n = suite.countTestCases()
        res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        check("W7-collected", n >= 14, f"collected={n} (14 functions in the pytest-style file)")
        check("W7-green", n >= 14 and res.wasSuccessful(),
              f"failures={len(res.failures)} errors={len(res.errors)}")
    except Exception as exc:  # noqa: BLE001
        unmet("W7", repr(exc))

    # ------------------------------------------------ W6 h9_native_probe passes every required kw-only arg
    try:
        from dayz_mcp import native_launcher_backend as nlb
        sig = inspect.signature(nlb.launch_registered_native)
        required_kw = {n for n, p in sig.parameters.items()
                       if p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty}
        tree = ast.parse((TOOLS / "h9_native_probe.py").read_text(encoding="utf-8"))
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = getattr(f, "attr", None) or getattr(f, "id", None)
                if name == "launch_registered_native":
                    calls.append(node)
        passed = set()
        for c in calls:
            passed |= {k.arg for k in c.keywords if k.arg}
        check("W6-kwargs-complete", bool(calls) and required_kw <= passed,
              f"required_kw={sorted(required_kw)} passed={sorted(passed)} calls={len(calls)}")
        # main() must not fold an internal TypeError into a bundle code
        import h9_native_probe as h9

        async def boom(*_a, **_k):
            raise TypeError("missing 1 required keyword-only argument: 'daemon_policy_json'")

        buf = io.StringIO()
        with patch.object(h9, "_run", boom), patch.object(sys, "argv", ["h9_native_probe.py", "preflight"]):
            with contextlib.redirect_stdout(buf):
                try:
                    rc: object = h9.main()
                except SystemExit as e:
                    rc = e.code
                except TypeError:
                    rc = "raised"
        out = buf.getvalue()
        check("W6-main-does-not-fold-TypeError",
              rc == "raised" or ("probe_internal_error" in out and "invalid_native_launcher_bundle" not in out),
              f"rc={rc!r} out={out[:160]!r}")
    except Exception as exc:  # noqa: BLE001
        unmet("W6", repr(exc))

    # ------------------------------------------------ W8 doctor: closure-manifest externals vs disk
    try:
        from dayz_mcp import doctor
        fn = getattr(doctor, "check_native_bundle_externals", None)
        if fn is None:
            unmet("W8-seam", "doctor.check_native_bundle_externals(entries, stat=...) does not exist")
        else:
            entries = [
                {"kind": "external", "path": r"C:\Program Files (x86)\Steam\steamclient.dll", "size": 10, "sha256": "aa" * 32},
                {"kind": "external", "path": r"C:\Program Files (x86)\Steam\tier0_s.dll", "size": 20, "sha256": "bb" * 32},
                {"kind": "bundle", "path": r"runtime\python.exe", "size": 5, "sha256": "cc" * 32},
            ]
            table_ok = {entries[0]["path"]: (10, "aa" * 32), entries[1]["path"]: (20, "bb" * 32)}

            def stat_ok(path):
                return table_ok.get(str(path))

            f_ok = fn(entries, stat=stat_ok)
            codes = [f.get("code") for f in f_ok]
            check("W8-all-match", codes == ["NATIVE_BUNDLE_EXTERNALS_OK"] and f_ok[0].get("severity") == "INFO"
                  and f_ok[0].get("checked") == 2, f"findings={f_ok!r}")

            def stat_drift(path):
                if str(path) == entries[0]["path"]:
                    return (11, "aa" * 32)      # size changed: Steam updated the client
                return table_ok.get(str(path))

            f_d = fn(entries, stat=stat_drift)
            d = next((f for f in f_d if f.get("code") == "NATIVE_BUNDLE_EXTERNAL_DRIFT"), None)
            check("W8-size-drift", d is not None and d.get("severity") == "FAIL"
                  and entries[0]["path"] in (d.get("drifted") or [])
                  and all(t in str(d.get("remediation", "")) for t in REMEDY_TOKENS),
                  f"findings={f_d!r}")

            def stat_missing(path):
                if str(path) == entries[1]["path"]:
                    return None
                return table_ok.get(str(path))

            f_m = fn(entries, stat=stat_missing)
            m = next((f for f in f_m if f.get("code") == "NATIVE_BUNDLE_EXTERNAL_DRIFT"), None)
            check("W8-missing-file", m is not None and entries[1]["path"] in (m.get("drifted") or []),
                  f"findings={f_m!r}")
            # bundle entries are never checked by this seam (they have their own verifier)
            check("W8-bundle-entries-untouched", all(entries[2]["path"] not in (f.get("drifted") or []) for f in f_d + f_m),
                  "bundle-kind entries must not be reported as external drift")
            # wired: the seam is CALLED somewhere in doctor.py besides its definition
            src = (TOOLS / "dayz_mcp" / "doctor.py").read_text(encoding="utf-8")
            check("W8-wired", src.count("check_native_bundle_externals(") >= 2,
                  f"occurrences={src.count('check_native_bundle_externals(')} (def + at least one call)")
    except Exception as exc:  # noqa: BLE001
        unmet("W8", repr(exc))

    # ------------------------------------------------ W9 interpreter guard
    try:
        import dayz_mcp
        mod = importlib.import_module("tests.test_interpreter_guard")
        approved = mod.approved_interpreter(Path(dayz_mcp.__file__))
        check("W9-approved-path", str(approved).replace("/", "\\").lower().endswith(r"tools\.venv-mcp\scripts\python.exe"),
              f"approved={approved}")
        cur = Path(r"C:\Python314\python.exe")
        appr = Path(r"C:\x\tools\.venv-mcp\Scripts\python.exe")
        v_mis = mod.verdict(cur, appr, approved_exists=True)
        v_ok = mod.verdict(appr, appr, approved_exists=True)
        v_abs = mod.verdict(cur, appr, approved_exists=False)
        check("W9-verdict-mismatch", v_mis[0] == "mismatch" and str(cur) in v_mis[1] and str(appr) in v_mis[1], f"{v_mis!r}")
        check("W9-verdict-ok", v_ok[0] == "ok", f"{v_ok!r}")
        check("W9-verdict-absent", v_abs[0] == "absent" and ".venv-mcp" in v_abs[1], f"{v_abs!r}")
        suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_interpreter_guard")
        res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        # In this workspace the approved venv is absent: the guard must SKIP with a message, never fail.
        check("W9-skips-when-absent", suite.countTestCases() >= 1 and res.wasSuccessful() and len(res.skipped) >= 1,
              f"tests={suite.countTestCases()} failures={len(res.failures)} errors={len(res.errors)} skipped={len(res.skipped)}")
    except Exception as exc:  # noqa: BLE001
        unmet("W9", repr(exc))

    # ------------------------------------------------ W12 docs test advice is actionable
    try:
        src = (TOOLS / "tests" / "test_docs_truth.py").read_text(encoding="utf-8")
        check("W12-no-regenerate-advice", "Regenerate the map" not in src and "regenerate the map" not in src,
              "the advice named a generator that never existed")
        check("W12-by-hand-advice", "by hand" in src, "advice must say the map is maintained by hand")
    except Exception as exc:  # noqa: BLE001
        unmet("W12", repr(exc))

    counts = {"PASS": 0, "FAIL": 0, "UNMET": 0}
    for name, status, detail in RESULTS:
        counts[status] += 1
        print(f"{status:5} {name}" + (f"  -- {detail}" if detail and status != "PASS" else ""))
    print(f"PASS={counts['PASS']}  FAIL={counts['FAIL']}  UNMET={counts['UNMET']}  de {len(RESULTS)}")
    if counts["FAIL"] == 0 and counts["UNMET"] == 0:
        print("ORACULO-VERDE")
        return 0
    print("ORACULO-ROJO")
    return 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.exit(main())
