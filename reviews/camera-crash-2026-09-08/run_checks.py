"""Run only explicitly named modules, serially; write and reread evidence per module."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys


OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
TOOLS = ROOT / "tools"
PYTHON = TOOLS / ".venv-mcp/Scripts/python.exe"
MODULES = [
    "tests.test_camera_native_crash",
    "tests.test_action_use",
    "tests.test_bridge_client_capabilities",
    "tests.test_addon_tree_has_no_write_artifacts",
    "tests.test_effective_schema_catalog",
    "tests.test_guards_bridge",
    "tests.test_instance_fence",
    "tests.test_leak_callback_lifetime",
    "tests.test_key_press",
    "tests.test_loopback",
    "tests.test_mcp_tools",
    "tests.test_player_respawn",
    "tests.test_poll_key_reload_contract",
    "tests.test_poll_watchdog_contract",
    "tests.test_restore_gameplay_contract",
    "tests.test_ui_click_scriptview",
    "tests.test_ui_error_diagnostics",
    "tests.test_ui_enforce_contract",
    "tests.test_vehicle_telemetry_contract",
    "tests.test_ui_reload_layout",
    "tests.test_vehicle_trace_contract",
]


def save(path, data):
    path.write_bytes(data)
    assert path.read_bytes() == data and path.stat().st_size == len(data), path


def main():
    phase = sys.argv[1]
    assert phase in ("red", "green")
    env = os.environ.copy()
    temporary = OUT / "test-tmp"
    temporary.mkdir(exist_ok=True)
    env.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
               TEMP=str(temporary), TMP=str(temporary))
    env.pop("MCP_CAMERA_SOURCE", None)
    if phase == "red":
        env["MCP_CAMERA_SOURCE"] = str(OUT / "MCPClientBridge.c.BEFORE")
    modules = MODULES[:1] if phase == "red" else MODULES
    log = (f"cwd={TOOLS}\nPYTHONPATH={env['PYTHONPATH']}\n"
           f"PYTHONDONTWRITEBYTECODE=1\nPYTHONUTF8=1\nTEMP={temporary}\nTMP={temporary}\n"
           f"MCP_CAMERA_SOURCE={env.get('MCP_CAMERA_SOURCE', '<unset>')}\n").encode()
    results = []
    for module in modules:
        argv = [str(PYTHON), "-B", "-m", "unittest", module, "-v"]
        command = subprocess.list2cmdline(argv)
        print(f"RUN {module}", flush=True)
        run = subprocess.run(argv, cwd=TOOLS, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=180)
        log += b"\nCOMMAND: " + command.encode() + b"\n" + run.stdout
        log += f"\nEXIT CODE: {run.returncode}\n".encode()
        save(OUT / f"{phase}.log", log)
        summary = [line for line in run.stdout.decode("utf-8", errors="replace").splitlines()
                   if line.startswith(("Ran ", "OK", "FAILED", "ERROR:", "FAIL:"))]
        results.append(dict(module=module, command=command, exit_code=run.returncode, summary=summary))
        save(OUT / f"{phase}-results.json", (json.dumps(results, indent=2) + "\n").encode())
        print("\n".join(summary) + f"\nEXIT CODE: {run.returncode}", flush=True)
    print(f"VERIFIED {phase}.log: {len(log)} bytes, sha256={hashlib.sha256(log).hexdigest()}")
    if phase == "red":
        return 0 if results[0]["exit_code"] != 0 else 1
    return int(any(result["exit_code"] != 0 for result in results))


if __name__ == "__main__":
    sys.exit(main())
