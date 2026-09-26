"""Run the same linter on BEFORE text without reverting shared working files."""
import json
from pathlib import Path
import re
import runpy
import sys
from unittest.mock import patch

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
LINTER = Path("C:/Users/guill/DayZ-Modding-Knowledge-Pack/tools/dayz-script-validator/scripts/script_validator.py")
sys.path.insert(0, str(LINTER.parent))
namespace = runpy.run_path(str(LINTER))
original = namespace["read_text_utf8_or_error"]
seen = set()


def before(path, rel_path):
    backup = OUT / (path.name + ".BEFORE")
    if path.parent == ROOT / "addon/scripts/5_Mission" and backup.exists():
        seen.add(path.name)
        return backup.read_text(encoding="utf-8-sig"), None
    return original(path, rel_path)


# Functions from runpy retain their own globals mapping.
with patch.dict(namespace["validate_addon"].__globals__, read_text_utf8_or_error=before):
    baseline = namespace["validate_addon"](ROOT / "addon")
assert seen == {"MCPBridge.c", "MCPCallbacks.c", "MCPClientBridge.c"}, seen
current = namespace["validate_addon"](ROOT / "addon")


def normalized(result):
    # Two inserted member/constructor lines move existing warnings, not their content.
    return [(category, row["rule_id"], re.sub(r"line \d+", "line N", row["message"]))
            for category in ("errors", "warnings") for row in result[category]]


assert normalized(baseline) == normalized(current), "lint findings differ beyond line movement"
print(namespace["format_terse"](baseline))
print("BEFORE substitutions verified: " + ", ".join(sorted(seen)))
print("BEFORE == current: same 2 errors and 5 warnings (line offsets normalized)")
for name, result in (("lint-before.json", baseline), ("lint-current.json", current)):
    p = OUT / name
    data = (json.dumps(result, indent=2) + "\n").encode("utf-8")
    p.write_bytes(data)
    assert p.read_bytes() == data and p.stat().st_size == len(data)
sys.exit(namespace["exit_code_for_status"](baseline["status"]))
