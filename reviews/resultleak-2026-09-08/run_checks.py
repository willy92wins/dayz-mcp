"""Serial, explicit-module evidence runner; no discovery or live DayZ services."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
TOOLS = ROOT / "tools"
PYTHON = TOOLS / ".venv-mcp/Scripts/python.exe"
MODULES = [
    "tests.test_resultleak_pool",
    "tests.test_action_use",
    "tests.test_addon_tree_has_no_write_artifacts",
    "tests.test_batch6",
    "tests.test_bridge_client_capabilities",
    "tests.test_bridge_server_capabilities",
    "tests.test_camera_native_crash",
    "tests.test_d09_d10_spawn_timeout_object_id",
    "tests.test_effective_schema_catalog",
    "tests.test_entities_has_cargo",
    "tests.test_entities_query_cargo",
    "tests.test_guards_bridge",
    "tests.test_instance_fence",
    "tests.test_inventory_give",
    "tests.test_key_press",
    "tests.test_leak_callback_drain",
    "tests.test_leak_callback_lifetime",
    "tests.test_loopback",
    "tests.test_mcp_tools",
    "tests.test_object_anim",
    "tests.test_object_inspect",
    "tests.test_player_respawn",
    "tests.test_player_teleport",
    "tests.test_poll_key_reload_contract",
    "tests.test_poll_watchdog_contract",
    "tests.test_restore_gameplay_contract",
    "tests.test_surface_query",
    "tests.test_task9_spawn_phase_markers",
    "tests.test_ui_click_scriptview",
    "tests.test_ui_enforce_contract",
    "tests.test_ui_error_diagnostics",
    "tests.test_ui_reload_layout",
    "tests.test_vehicle_prepare_fixture",
    "tests.test_vehicle_telemetry_contract",
    "tests.test_vehicle_trace_contract",
    "tests.test_world_spawn_ground_contract",
]
ENV = os.environ.copy()
for key in ("DAYZ_RESULTLEAK_BEFORE_DIR", "DAYZ_LEAK_BEFORE_DIR", "GUARDS_SOURCE_DIR"):
    ENV.pop(key, None)
ENV["PYTHONPATH"] = str(ROOT)
ENV["PYTHONDONTWRITEBYTECODE"] = "1"
TEMP = OUT / "test-temp"
TEMP.mkdir(exist_ok=True)
for key in ("TMP", "TEMP", "TMPDIR"):
    ENV[key] = str(TEMP)


def save(name, text):
    data = text.encode("utf-8")
    p = OUT / name
    p.write_bytes(data)
    assert p.stat().st_size == len(data) and p.read_bytes() == data, p


def run(args, *, before=False):
    env = ENV.copy()
    if before:
        env["DAYZ_RESULTLEAK_BEFORE_DIR"] = str(OUT)
    command = [str(PYTHON), "-X", "utf8", *args]
    settings = [f"cwd = {TOOLS}", f"PYTHONPATH = {ROOT}", "PYTHONDONTWRITEBYTECODE = 1", f"TMP/TEMP/TMPDIR = {TEMP}"]
    if before:
        settings.append(f"DAYZ_RESULTLEAK_BEFORE_DIR = {OUT}")
    else:
        settings.append("DAYZ_RESULTLEAK_BEFORE_DIR, DAYZ_LEAK_BEFORE_DIR, GUARDS_SOURCE_DIR unset")
    literal = "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in command)
    result = subprocess.run(command, cwd=TOOLS, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=180)
    output = result.stdout
    summary = [line for line in output.splitlines() if re.match(r"^(Ran \d+ tests?|OK\b|FAILED\b|FAIL\b|WARN\b|PASS\b)", line)]
    row = dict(command=literal, settings=settings, exit_code=result.returncode, summary=summary)
    log = "\n".join(settings) + "\n" + literal + "\n\n" + output + f"\nEXIT CODE: {result.returncode}\n"
    return row, log


rows = []
row, log = run(["-m", "unittest", "tests.test_resultleak_pool", "-v"], before=True)
rows.append(dict(phase="red", **row))
save("red.log", log)
save("test-results.json", json.dumps(rows, indent=2) + "\n")
print("RED", " | ".join(row["summary"]), "EXIT", row["exit_code"], flush=True)
assert row["exit_code"] != 0, "negative control unexpectedly green"
green = ""
for module in MODULES:
    row, log = run(["-m", "unittest", module, "-v"])
    green += log + "\n"
    rows.append(dict(phase="green", module=module, **row))
    save("green.log", green)
    save("test-results.json", json.dumps(rows, indent=2) + "\n")
    print(module, " | ".join(row["summary"]), "EXIT", row["exit_code"], flush=True)
validator = "C:/Users/guill/DayZ-Modding-Knowledge-Pack/tools/dayz-script-validator/scripts/script_validator.py"
row, log = run([validator, str(ROOT / "addon"), "--terse"])
rows.append(dict(phase="lint", **row))
save("lint.log", log)
save("test-results.json", json.dumps(rows, indent=2) + "\n")
print("LINT", " | ".join(row["summary"]), "EXIT", row["exit_code"], flush=True)
sys.exit(0 if all(r["exit_code"] == 0 for r in rows if r["phase"] == "green") else 1)
