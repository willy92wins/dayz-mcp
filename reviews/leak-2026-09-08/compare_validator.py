from pathlib import Path
import importlib.util
import json
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
VALIDATOR = Path("C:/Users/guill/DayZ-Modding-Knowledge-Pack/tools/dayz-script-validator/scripts/script_validator.py")
sys.path.insert(0, str(VALIDATOR.parent))
spec = importlib.util.spec_from_file_location("leak_validator", VALIDATOR)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
read = validator.read_text_utf8_or_error
before = {str((ROOT / "addon/scripts/5_Mission" / name).resolve()): (OUT / (name + ".BEFORE"))
          for name in ("MCPCallbacks.c", "MCPBridge.c", "MCPClientBridge.c")}

def original(path, rel_path):
    backup = before.get(str(path.resolve()))
    return (backup.read_text(encoding="utf-8"), None) if backup else read(path, rel_path)

validator.read_text_utf8_or_error = original
baseline = validator.validate_addon(ROOT / "addon")
current_log = (OUT / "validator.log").read_text(encoding="utf-8")
current = json.loads(current_log[current_log.index("{"):current_log.rindex("}") + 1])

def findings(result, previous):
    keys = []
    for finding in result["errors"] + result["warnings"]:
        path = ROOT / "addon" / finding["file"]
        if previous:
            text, _ = original(path, finding["file"])
        else:
            text = path.read_text(encoding="utf-8")
        keys.append((finding["rule_id"], finding["severity"], finding["file"], text.splitlines()[finding["line"] - 1].strip()))
    return sorted(keys)

same = findings(baseline, True) == findings(current, False)
lines = ["BEFORE validator: " + baseline["status"],
         f"errors={len(baseline['errors'])} warnings={len(baseline['warnings'])}",
         "BASELINE VALIDATOR EXIT CODE: " + str(validator.exit_code_for_status(baseline["status"])),
         "CURRENT validator: " + current["status"],
         f"errors={len(current['errors'])} warnings={len(current['warnings'])}",
         "SOURCE/SEVERITY/RULE FINDINGS IDENTICAL: " + str(same),
         "COMPARISON EXIT CODE: " + str(0 if same else 1),
         json.dumps(baseline, indent=2)]
data = ("\n".join(lines) + "\n").encode("utf-8")
p = OUT / "validator-before.log"
p.write_bytes(data)
assert p.read_bytes() == data
print("\n".join(lines[:-1]))
sys.exit(0 if same else 1)
