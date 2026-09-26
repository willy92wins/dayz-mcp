"""Executed [EXACT] reproduction for R4-F-01. No host reads, processes or engine."""
import ntpath
from dayz_mcp import native_launcher_transaction as t
from tests.test_vpp_preflight import _policy, _healthy

p = _policy()
candidate = r"P:\Mods\1828439124"
payload = {"mode": "server", "base_mods": [], "extra_mods": [candidate], "mod": p.mod, "dev_root": p.dev_root}
meta_path = ntpath.normcase(ntpath.join(candidate, "meta.cpp"))
cfg_path = ntpath.normcase(ntpath.join(p.dev_root, "_server", "serverDZ.cfg"))
for label, meta, cfg in (
    ("meta_duplicate_control", "publishedid=1828439124; publishedid=1559212036;", "vppDisablePassword=1;"),
    ("meta_duplicate_block_comment", "publishedid=1828439124; publishedid/*x*/=1559212036;", "vppDisablePassword=1;"),
    ("meta_duplicate_line_comment", "publishedid=1828439124; publishedid//x\n=1559212036;", "vppDisablePassword=1;"),
    ("meta_duplicate_identical", "publishedid=1828439124; publishedid/*x*/=1828439124;", "vppDisablePassword=1;"),
    ("meta_value_comment_control", "publishedid=1828439124 /*x*/;", "vppDisablePassword=1;"),
    ("meta_value_comment_adjacent", "publishedid=1828439124/*x*/;", "vppDisablePassword=1;"),
    ("cfg_conflict_control", "publishedid=1828439124;", "vppDisablePassword=1; vppDisablePassword=0;"),
    ("cfg_conflict_block_comment", "publishedid=1828439124;", "vppDisablePassword=1; vppDisablePassword/*x*/=0;"),
):
    f = _healthy(p)
    f.files[meta_path] = meta
    f.files[cfg_path] = cfg
    out = t.evaluate_vpp_preflight(payload, p, files=f)
    print(f"{label}: meta_values={t._live_assignments(meta, 'publishedid')!r} cfg_values={t._live_assignments(cfg, t._VPP_KEY)!r} error={out.error_code!r} missing={out.missing!r} warnings={out.warnings!r}")
