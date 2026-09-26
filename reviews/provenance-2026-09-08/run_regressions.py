from run_tests import run
import sys

# Explicit importer modules; daemon-starting integrations remain for the receiver.
MODULES = (
    "test_mcp_host_timeouts", "test_control_client", "test_daemon_policy",
    "test_admin_cli", "test_bad_args_messages", "test_bug046_audit_fault_recovery",
    "test_client_mode", "test_client_runtime_control_composition", "test_daemon_credential",
    "test_daemon_contract", "test_dayz_test_tool", "test_dayz_test_value_error_codes",
    "test_doctor", "test_lifecycle_cli", "test_native_launcher_transaction",
    "test_python_backlog_fixes", "test_secure_launcher", "test_server_response_truth",
    "test_task7_final_lifecycle_regressions", "test_vpp_preflight",
)
failed = []
for name in MODULES:
    print(f"RUNNING tests.{name}", flush=True)
    if run(f"regression-{name}.log", [f"tests.{name}"]):
        failed.append(name)
print(f"MODULES={len(MODULES)} FAILED={failed}", flush=True)
sys.exit(bool(failed))
