"""Serial, explicit-module runner. Never discovers the full test suite."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
PYTHON = ROOT / "tools/.venv-mcp/Scripts/python.exe"
EXISTING = (
    "test_action_use", "test_addon_tree_has_no_write_artifacts", "test_batch6",
    "test_bridge_client_capabilities", "test_bridge_server_capabilities",
    "test_d09_d10_spawn_timeout_object_id", "test_effective_schema_catalog",
    "test_entities_has_cargo", "test_entities_query_cargo", "test_guards_bridge",
    "test_instance_fence", "test_inventory_give", "test_key_press", "test_loopback",
    "test_mcp_tools", "test_object_anim", "test_object_inspect", "test_player_respawn",
    "test_player_teleport", "test_poll_key_reload_contract", "test_poll_watchdog_contract",
    "test_restore_gameplay_contract", "test_surface_query", "test_task9_spawn_phase_markers",
    "test_ui_click_scriptview", "test_ui_enforce_contract", "test_ui_error_diagnostics",
    "test_ui_reload_layout", "test_vehicle_prepare_fixture", "test_vehicle_telemetry_contract",
    "test_vehicle_trace_contract", "test_world_spawn_ground_contract",
)
NEW = ("test_leak_callback_lifetime", "test_leak_callback_drain")

def save(path, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.write_bytes(data)
    assert path.read_bytes() == data, str(path)


def run(mode):
    modules = NEW if mode == "red" else NEW + EXISTING
    if mode == "red":
        for name in ("MCPCallbacks.c", "MCPBridge.c", "MCPClientBridge.c"):
            original = (OUT / (name + ".BEFORE")).read_bytes()
            baseline = json.loads((OUT / "baseline.json").read_text())
            assert hashlib.sha256(original).hexdigest() == baseline[str(ROOT / "addon/scripts/5_Mission" / name)]["sha256"]
    env = os.environ.copy()
    env.update(PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    if mode == "red":
        env["DAYZ_LEAK_BEFORE_DIR"] = str(OUT)
    else:
        env.pop("DAYZ_LEAK_BEFORE_DIR", None)
    evidence = ""
    records = []
    for module in modules:
        args = [str(PYTHON), "-B", "-m", "unittest", "tests." + module, "-v"]
        command = (f"$env:PYTHONPATH='{ROOT}'; $env:PYTHONDONTWRITEBYTECODE='1'; "
                   f"$env:PYTHONIOENCODING='utf-8'; & '{PYTHON}' -B -m unittest tests.{module} -v")
        if mode == "red":
            command = f"$env:DAYZ_LEAK_BEFORE_DIR='{OUT}'; " + command
        else:
            command = "Remove-Item Env:DAYZ_LEAK_BEFORE_DIR -ErrorAction SilentlyContinue; " + command
        result = subprocess.run(args, cwd=ROOT / "tools", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = result.stdout.decode("utf-8", errors="replace")
        evidence += f"cwd = {ROOT / 'tools'}\n{command}\n{output}\nEXIT CODE: {result.returncode}\n\n"
        save(OUT / (mode + ".log"), evidence)
        summary = [line for line in output.splitlines() if line.startswith(("Ran ", "OK", "FAILED", "ERROR:", "FAIL:"))]
        records.append(dict(module=module, command=command, cwd=str(ROOT / 'tools'), summary=summary, exit_code=result.returncode))
        save(OUT / (mode + "-results.json"), json.dumps(records, indent=2) + "\n")
        print(module, " | ".join(summary), "EXIT", result.returncode, flush=True)
    return 1 if any(record["exit_code"] for record in records) else 0


if __name__ == "__main__":
    assert sys.argv[1] in ("red", "green")
    sys.exit(run(sys.argv[1]))
