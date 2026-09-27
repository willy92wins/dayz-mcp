"""The suite must not grow new imports between test modules (review 2026-09-25, T0).

A test module that imports a fake from another test module ties the two
together: regrouping or renaming one breaks the other, and the fake has no
obvious home. Shared fakes live in tests/*_helpers.py (fence_helpers,
lease_helpers, lifecycle_helpers, ...).

KNOWN_CROSS_TEST_IMPORTS lists the (importer, test module, name) imports that
existed when this rule landed. It may only shrink: remove an entry when its
fake moves into a helper module. Adding an entry is a review decision, never
a way to turn this test green; the test cannot tell the two apart.

Static imports only, in every file under tests/: `from tests.test_x import y`,
`from tests import test_x`, `import tests.test_x` and their relative forms
(name "*" means the module itself). importlib calls are not checked.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent

# Imports that pre-date the rule, by importer.
KNOWN_CROSS_TEST_IMPORTS: frozenset[tuple[str, str, str]] = frozenset({
    ("test_546d_dump.py", "tests.test_vehicle_trace", "_positive_trace"),
    ("test_546d_dump.py", "tests.test_vehicle_trace_contract", "_content_json"),
    ("test_546d_dump.py", "tests.test_vehicle_trace_contract", "_method_body"),
    ("test_a429_overlay.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_a429_overlay.py", "tests.test_mcp_tools", "FakePeer"),
    ("test_a429_overlay.py", "tests.test_mcp_tools", "_content_json"),
    ("test_a429_overlay.py", "tests.test_session_status_blocked_on", "BOX_BLOCKED_ON"),
    ("test_b3_5872_seated_camera.py", "tests.test_camera_native_crash", "BUILD"),
    ("test_b3_5872_seated_camera.py", "tests.test_camera_native_crash", "LIVE_TRANSPORT"),
    ("test_b3_5872_seated_camera.py", "tests.test_camera_native_crash", "READ_ERROR"),
    ("test_b3_5872_seated_camera.py", "tests.test_camera_native_crash", "_body"),
    ("test_b3_5872_seated_camera.py", "tests.test_camera_native_crash", "_source"),
    ("test_b3_5872_seated_camera.py", "tests.test_camera_native_crash", "assert_live_vehicle_guard"),
    ("test_box_occupancy.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_box_occupancy.py", "tests.test_mcp_tools", "_content_json"),
    ("test_box_occupancy.py", "tests.test_process_lifecycle", "AuditSink"),
    ("test_box_occupancy.py", "tests.test_process_lifecycle", "FakeGuard"),
    ("test_box_occupancy.py", "tests.test_process_lifecycle", "FakeLauncher"),
    ("test_box_occupancy.py", "tests.test_process_lifecycle", "HASH_A"),
    ("test_box_occupancy.py", "tests.test_process_lifecycle", "HASH_B"),
    ("test_box_occupancy.py", "tests.test_process_lifecycle", "IDENTITY_A"),
    ("test_box_occupancy.py", "tests.test_process_lifecycle", "process"),
    ("test_box_occupancy.py", "tests.test_process_lifecycle", "snapshot"),
    ("test_box_port_occupancy.py", "tests.test_box_occupancy", "_argv_lookup"),
    ("test_box_port_occupancy.py", "tests.test_process_lifecycle", "AuditSink"),
    ("test_box_port_occupancy.py", "tests.test_process_lifecycle", "FakeGuard"),
    ("test_box_port_occupancy.py", "tests.test_process_lifecycle", "FakeLauncher"),
    ("test_box_port_occupancy.py", "tests.test_process_lifecycle", "IDENTITY_A"),
    ("test_box_port_occupancy.py", "tests.test_process_lifecycle", "process"),
    ("test_box_port_occupancy.py", "tests.test_process_lifecycle", "snapshot"),
    ("test_box_port_wait.py", "tests.test_box_occupancy", "FakeBoxClient"),
    ("test_bug104_reap_under_quarantine.py", "tests.test_process_lifecycle", "AuditSink"),
    ("test_bug104_reap_under_quarantine.py", "tests.test_process_lifecycle", "FakeGuard"),
    ("test_bug104_reap_under_quarantine.py", "tests.test_process_lifecycle", "FakeLauncher"),
    ("test_bug104_reap_under_quarantine.py", "tests.test_process_lifecycle", "IDENTITY_A"),
    ("test_bug104_reap_under_quarantine.py", "tests.test_process_lifecycle", "process"),
    ("test_bug104_reap_under_quarantine.py", "tests.test_process_lifecycle", "snapshot"),
    ("test_client_acquire_wait.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_client_death_dump_binding.py", "tests.test_dayz_test_tool", "_Bundle"),
    ("test_client_death_dump_binding.py", "tests.test_dayz_test_tool", "_Opened"),
    ("test_client_death_dump_binding.py", "tests.test_dayz_test_tool", "_Runtime"),
    ("test_client_death_dump_binding.py", "tests.test_dayz_test_tool", "_policy"),
    ("test_client_death_dump_binding.py", "tests.test_dayz_test_tool", "_sealed"),
    ("test_client_death_dump_binding.py", "tests.test_dayz_test_tool", "_terminal"),
    ("test_client_dumps_cross_process.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_client_mode.py", "tests.test_daemon", "DaemonHttpServer"),
    ("test_client_mode.py", "tests.test_daemon", "_config"),
    ("test_client_mode.py", "tests.test_daemon", "_free_port"),
    ("test_client_mode.py", "tests.test_daemon", "_http"),
    ("test_client_mode.py", "tests.test_mcp_tools", "_content_json"),
    ("test_client_platform_alias.py", "tests.test_daemon", "DaemonHttpServer"),
    ("test_client_platform_alias.py", "tests.test_daemon", "_config"),
    ("test_client_platform_alias.py", "tests.test_daemon", "_http"),
    ("test_client_runtime_control_composition.py", "tests.test_control_client", "_policy"),
    ("test_coordination_audit_faults.py", "tests.test_process_lifecycle", "FakeGuard"),
    ("test_coordination_audit_faults.py", "tests.test_process_lifecycle", "FakeLauncher"),
    ("test_coordination_audit_faults.py", "tests.test_process_lifecycle", "process"),
    ("test_d05_capture_targets_run_client.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_dayz_test_tool_modes.py", "tests.test_dayz_test_tool", "RUN_ID"),
    ("test_dayz_test_tool_modes.py", "tests.test_dayz_test_tool", "_Bundle"),
    ("test_dayz_test_tool_modes.py", "tests.test_dayz_test_tool", "_Opened"),
    ("test_dayz_test_tool_modes.py", "tests.test_dayz_test_tool", "_Runtime"),
    ("test_dayz_test_tool_modes.py", "tests.test_dayz_test_tool", "_policy"),
    ("test_dayz_test_tool_modes.py", "tests.test_dayz_test_tool", "_sealed"),
    ("test_dayz_test_tool_modes.py", "tests.test_dayz_test_tool", "_terminal"),
    ("test_db05_preflight_diagnostics.py", "tests.test_dayz_test_tool", "*"),
    ("test_db05_preflight_diagnostics.py", "tests.test_steam_preflight", "_MutableSteamProvider"),
    ("test_enqueue_refusal_reaches_the_caller.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_fase4b_tools.py", "tests.test_mcp_tools", "FakePeer"),
    ("test_fase4b_tools.py", "tests.test_mcp_tools", "_assert_tool_error"),
    ("test_fase4b_tools.py", "tests.test_mcp_tools", "_content_json"),
    ("test_fb_050e.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_fb_050e.py", "tests.test_control_client", "_policy"),
    ("test_fb_050e.py", "tests.test_mcp_tools", "_content_json"),
    ("test_fb_1f21_steam_envelope.py", "tests.test_dayz_test_tool", "RUN_ID"),
    ("test_fb_1f21_steam_envelope.py", "tests.test_dayz_test_tool", "_Bundle"),
    ("test_fb_1f21_steam_envelope.py", "tests.test_dayz_test_tool", "_Opened"),
    ("test_fb_1f21_steam_envelope.py", "tests.test_dayz_test_tool", "_Runtime"),
    ("test_fb_1f21_steam_envelope.py", "tests.test_dayz_test_tool", "_policy"),
    ("test_fb_1f21_steam_envelope.py", "tests.test_dayz_test_tool", "_sealed"),
    ("test_fb_1f21_steam_envelope.py", "tests.test_dayz_test_tool", "_terminal"),
    ("test_fb_1f21_steam_envelope.py", "tests.test_process_lifecycle", "*"),
    ("test_fb_2223_box_queue_offer.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_fb_2223_box_queue_offer.py", "tests.test_mcp_tools", "_content_json"),
    ("test_fb_3bb4_esc_and_drive_retire.py", "tests.test_bridge_client_capabilities", "announced_caps"),
    ("test_fb_3bb4_esc_and_drive_retire.py", "tests.test_bridge_client_capabilities", "dispatch_census"),
    ("test_fb_3bb4_esc_and_drive_retire.py", "tests.test_bridge_server_capabilities", "DISPATCH_SIGNATURE"),
    ("test_fb_3bb4_esc_and_drive_retire.py", "tests.test_bridge_server_capabilities", "_method_body"),
    ("test_fb_3bb4_esc_and_drive_retire.py", "tests.test_bridge_server_capabilities", "_walk_dispatch_chain"),
    ("test_fb_3bb4_esc_and_drive_retire.py", "tests.test_bridge_server_capabilities", "_without_comments"),
    ("test_fb_7ef2.py", "tests.test_dayz_test_tool", "RUN_ID"),
    ("test_fb_7ef2.py", "tests.test_dayz_test_tool", "_Bundle"),
    ("test_fb_7ef2.py", "tests.test_dayz_test_tool", "_Opened"),
    ("test_fb_7ef2.py", "tests.test_dayz_test_tool", "_Runtime"),
    ("test_fb_7ef2.py", "tests.test_dayz_test_tool", "_policy"),
    ("test_fb_7ef2.py", "tests.test_dayz_test_tool", "_sealed"),
    ("test_fb_7ef2.py", "tests.test_process_lifecycle", "AuditSink"),
    ("test_fb_7ef2.py", "tests.test_process_lifecycle", "FakeGuard"),
    ("test_fb_7ef2.py", "tests.test_process_lifecycle", "FakeLauncher"),
    ("test_fb_7ef2.py", "tests.test_process_lifecycle", "IDENTITY_A"),
    ("test_fb_7ef2.py", "tests.test_process_lifecycle", "process"),
    ("test_fb_7ef2.py", "tests.test_process_lifecycle", "snapshot"),
    ("test_fb_8604_orderly_close.py", "tests.test_control_client", "_policy"),
    ("test_fb_88ef_305a_runtime_writes.py", "tests.test_doctor", "CLAUDE_GOOD"),
    ("test_fb_88ef_305a_runtime_writes.py", "tests.test_doctor", "CODEX_GOOD"),
    ("test_fb_88ef_305a_runtime_writes.py", "tests.test_doctor", "DAEMON_GOOD"),
    ("test_fb_88ef_305a_runtime_writes.py", "tests.test_doctor", "clean_status"),
    ("test_fb_88ef_305a_runtime_writes.py", "tests.test_doctor", "daemon_argv"),
    ("test_fb_88ef_305a_runtime_writes.py", "tests.test_doctor", "doctor"),
    ("test_fb_88ef_305a_runtime_writes.py", "tests.test_fb_160e", "_backup_dir"),
    ("test_fb_88ef_305a_runtime_writes.py", "tests.test_fb_160e", "_hex_dirs"),
    ("test_fb_88ef_305a_runtime_writes.py", "tests.test_fb_160e", "_limits"),
    ("test_fb_88ef_305a_runtime_writes.py", "tests.test_fb_160e", "_seed"),
    ("test_fb_b0d9_restore_honest.py", "tests.test_capture_frame_stale", "CMDLINE"),
    ("test_fb_b0d9_restore_honest.py", "tests.test_capture_frame_stale", "_backend"),
    ("test_fb_b0d9_restore_honest.py", "tests.test_restore_gameplay_contract", "_camera_probe"),
    ("test_fb_b0d9_restore_honest.py", "tests.test_restore_gameplay_contract", "_content_json"),
    ("test_fb_f298_launch_focus.py", "tests.test_process_lifecycle", "AuditSink"),
    ("test_fb_f298_launch_focus.py", "tests.test_process_lifecycle", "FakeGuard"),
    ("test_fn_f1f5.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_fn_f1f5.py", "tests.test_mcp_tools", "_content_json"),
    ("test_h14_stale_policy.py", "tests.test_control_client", "_policy"),
    ("test_h14_stale_policy.py", "tests.test_dayz_test_tool", "RUN_ID"),
    ("test_h14_stale_policy.py", "tests.test_dayz_test_tool", "_Bundle"),
    ("test_h14_stale_policy.py", "tests.test_dayz_test_tool", "_Opened"),
    ("test_h14_stale_policy.py", "tests.test_dayz_test_tool", "_Runtime"),
    ("test_h14_stale_policy.py", "tests.test_dayz_test_tool", "_policy"),
    ("test_h14_stale_policy.py", "tests.test_dayz_test_tool", "_sealed"),
    ("test_h14_stale_policy.py", "tests.test_dayz_test_tool", "_terminal"),
    ("test_leak_callback_drain.py", "tests.test_leak_callback_lifetime", "body"),
    ("test_leak_callback_drain.py", "tests.test_leak_callback_lifetime", "clean"),
    ("test_leak_callback_drain.py", "tests.test_leak_callback_lifetime", "source"),
    ("test_lifecycle_reconcile.py", "tests.test_daemon", "_config"),
    ("test_lifecycle_reconcile.py", "tests.test_dayz_test_tool", "RUN_ID"),
    ("test_lifecycle_reconcile.py", "tests.test_dayz_test_tool", "_Bundle"),
    ("test_lifecycle_reconcile.py", "tests.test_dayz_test_tool", "_Opened"),
    ("test_lifecycle_reconcile.py", "tests.test_dayz_test_tool", "_Runtime"),
    ("test_lifecycle_reconcile.py", "tests.test_dayz_test_tool", "_policy"),
    ("test_lifecycle_reconcile.py", "tests.test_dayz_test_tool", "_sealed"),
    ("test_lifecycle_reconcile.py", "tests.test_dayz_test_tool", "_terminal"),
    ("test_lifecycle_reconcile.py", "tests.test_process_lifecycle", "AuditSink"),
    ("test_lifecycle_reconcile.py", "tests.test_process_lifecycle", "FakeGuard"),
    ("test_lifecycle_reconcile.py", "tests.test_process_lifecycle", "FakeLauncher"),
    ("test_lifecycle_reconcile.py", "tests.test_process_lifecycle", "HASH_A"),
    ("test_lifecycle_reconcile.py", "tests.test_process_lifecycle", "HASH_B"),
    ("test_lifecycle_reconcile.py", "tests.test_process_lifecycle", "IDENTITY_A"),
    ("test_lifecycle_reconcile.py", "tests.test_process_lifecycle", "process"),
    ("test_lifecycle_reconcile.py", "tests.test_process_lifecycle", "snapshot"),
    ("test_lifecycle_request_fixture_parity.py", "tests.test_lifecycle_reconcile", "*"),
    ("test_logs_since_marker_roundtrip.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_logs_since_marker_roundtrip.py", "tests.test_mcp_tools", "_content_json"),
    ("test_lote2_t2_steam.py", "tests.test_dayz_test_tool", "*"),
    ("test_lote2_t2_steam.py", "tests.test_steam_preflight", "_FakeRemediationHost"),
    ("test_lote2_t2_steam.py", "tests.test_steam_preflight", "_MutableSteamProvider"),
    ("test_lote_v_products.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_mcp_tools.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_mcp_tools.py", "tests.test_dayz_test_tool", "_Bundle"),
    ("test_mcp_tools.py", "tests.test_dayz_test_tool", "_Opened"),
    ("test_mcp_tools.py", "tests.test_dayz_test_tool", "_policy"),
    ("test_mcp_tools.py", "tests.test_dayz_test_tool", "_sealed"),
    ("test_playbook_reload.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_pleno_lease_and_orphans.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_pleno_lease_and_orphans.py", "tests.test_daemon", "IDENTITY"),
    ("test_pleno_lease_and_orphans.py", "tests.test_daemon", "_http"),
    ("test_pleno_lease_and_orphans.py", "tests.test_mcp_tools", "_content_json"),
    ("test_pleno_lease_and_orphans.py", "tests.test_process_lifecycle", "FakeGuard"),
    ("test_pleno_lease_and_orphans.py", "tests.test_process_lifecycle", "legacy_process"),
    ("test_pleno_lease_and_orphans.py", "tests.test_session_status_blocked_on", "_status_payload"),
    ("test_precondition_docs.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_precondition_docs.py", "tests.test_vehicle_trace_contract", "_method_body"),
    ("test_prerun_desktop_gate.py", "tests.test_dayz_test_tool", "RUN_ID"),
    ("test_prerun_desktop_gate.py", "tests.test_dayz_test_tool", "_Bundle"),
    ("test_prerun_desktop_gate.py", "tests.test_dayz_test_tool", "_Opened"),
    ("test_prerun_desktop_gate.py", "tests.test_dayz_test_tool", "_Runtime"),
    ("test_prerun_desktop_gate.py", "tests.test_dayz_test_tool", "_policy"),
    ("test_prerun_desktop_gate.py", "tests.test_dayz_test_tool", "_sealed"),
    ("test_prerun_desktop_gate.py", "tests.test_dayz_test_tool", "_terminal"),
    ("test_relock_toolchain.py", "tests.test_dependency_lock", "*"),
    ("test_run_reaper.py", "tests.test_process_lifecycle", "AuditSink"),
    ("test_run_reaper.py", "tests.test_process_lifecycle", "FakeGuard"),
    ("test_run_reaper.py", "tests.test_process_lifecycle", "FakeLauncher"),
    ("test_run_reaper.py", "tests.test_process_lifecycle", "IDENTITY_A"),
    ("test_run_reaper.py", "tests.test_process_lifecycle", "IDENTITY_B"),
    ("test_run_reaper.py", "tests.test_process_lifecycle", "process"),
    ("test_runloss_diagnostics.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_runloss_diagnostics.py", "tests.test_instance_fence", "*"),
    ("test_server_freshness.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_server_response_truth.py", "tests.test_mcp_tools", "_content_json"),
    ("test_session_e2e.py", "tests.test_client_mode", "_VALID_PEER_VERSION"),
    ("test_session_e2e.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_session_e2e.py", "tests.test_daemon", "_free_port"),
    ("test_session_e2e.py", "tests.test_daemon", "_http"),
    ("test_session_handoff_reload.py", "tests.test_control_client", "_clean_session_status"),
    ("test_session_handoff_reload.py", "tests.test_control_client", "_policy"),
    ("test_session_status_blocked_on.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_session_status_blocked_on.py", "tests.test_mcp_tools", "_content_json"),
    ("test_steam_launch_guard.py", "tests.test_dayz_test_app", "RUN_ID"),
    ("test_steam_launch_guard.py", "tests.test_dayz_test_app", "_load_app"),
    ("test_steam_launch_guard.py", "tests.test_dayz_test_worker", "*"),
    ("test_steam_launch_guard.py", "tests.test_process_lifecycle", "*"),
    ("test_steam_launch_guard.py", "tests.test_steamfastpath_repair", "MemoryHost"),
    ("test_steam_launch_guard.py", "tests.test_steamfastpath_repair", "MemoryProvider"),
    ("test_steam_not_running_preflight.py", "tests.test_dayz_test_tool", "*"),
    ("test_steam_not_running_preflight.py", "tests.test_steam_preflight", "FakeSteamProvider"),
    ("test_steam_not_running_preflight.py", "tests.test_steam_preflight", "_MutableSteamProvider"),
    ("test_steam_not_running_preflight.py", "tests.test_steam_preflight", "_STEAM_EXE"),
    ("test_steam_not_running_preflight.py", "tests.test_steam_preflight", "active_process"),
    ("test_steampost_readiness.py", "tests.test_dayz_test_tool", "*"),
    ("test_steampost_readiness.py", "tests.test_steamfastpath_repair", "*"),
    ("test_takeover_contract.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_takeover_contract.py", "tests.test_mcp_tools", "_content_json"),
    ("test_task7_final_lifecycle_regressions.py", "tests.test_lifecycle_cli", "TtyInput"),
    ("test_telemetry_read_modes.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_telemetry_read_names_the_bad_field.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_ui_dialog.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_ui_enforce_contract.py", "tests.test_vehicle_telemetry_contract", "TELEMETRY_REGION_SHA256"),
    ("test_ui_enforce_contract.py", "tests.test_vehicle_telemetry_contract", "_telemetry_sha256"),
    ("test_vehicle_prepare_fixture.py", "tests.test_ui_error_diagnostics", "_wire_error_text"),
    ("test_wait_for.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_wait_for.py", "tests.test_mcp_tools", "_content_json"),
    ("test_wait_for_launch_and_contract.py", "tests.test_wait_for", "_HttpClientNotPollingThenPlayers"),
    ("test_wait_for_launch_and_contract.py", "tests.test_wait_for", "_MAPPED_CLIENT_NOT_POLLING"),
    ("test_wait_for_launch_and_contract.py", "tests.test_wait_for", "_http_always_client_not_polling"),
    ("test_wait_for_launch_and_contract.py", "tests.test_wait_for", "_real_client_runtime_http_only"),
    ("test_wait_for_marker.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_wait_for_marker.py", "tests.test_mcp_tools", "_content_json"),
    ("test_wait_for_requires_a_live_run.py", "tests.test_wait_for_launch_and_contract", "_FakeRuntime"),
    ("test_wait_for_requires_a_live_run.py", "tests.test_wait_for_launch_and_contract", "_live_process"),
    ("test_wait_for_requires_a_live_run.py", "tests.test_wait_for_launch_and_contract", "_profiles"),
    ("test_weak_agent_consumer_ux.py", "tests.test_client_mode", "_fixture_client_runtime"),
    ("test_weak_agent_consumer_ux.py", "tests.test_mcp_tools", "_content_json"),
    ("test_world_spawn_flags.py", "tests.test_precondition_docs", "_assert_world_spawn_copy"),
    ("test_world_spawn_flags.py", "tests.test_vehicle_trace_contract", "_method_body"),
    ("test_x5_tools.py", "tests.test_mcp_tools", "FakePeer"),
    ("test_x5_tools.py", "tests.test_mcp_tools", "_assert_tool_error"),
    ("test_x5_tools.py", "tests.test_mcp_tools", "_content_json"),
})


def _cross_test_imports() -> set[tuple[str, str, str]]:
    """(importer path under tests/, imported test module, name) for every static import.

    Covers `from tests.test_x import y`, `from tests import test_x`,
    `import tests.test_x`, and relative imports of the same targets, in every
    file under tests/ (unittest discovery also runs subpackages). A module
    importing itself is not coupling and is skipped.
    """
    found: set[tuple[str, str, str]] = set()
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        relative = path.relative_to(_TESTS_DIR)
        importer = relative.as_posix()
        package = ("tests",) + relative.parent.parts
        # A package's own name is the directory, not "<package>.__init__".
        own = ".".join(package if path.name == "__init__.py" else package + (path.stem,))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    if node.level > len(package):
                        continue  # beyond the top-level package: ImportError, not an import
                    base = package[: len(package) - node.level + 1]
                    module = ".".join(base + ((node.module,) if node.module else ()))
                else:
                    module = node.module or ""
                for alias in node.names:
                    if _is_test_module(module):
                        target, name = module, alias.name
                    elif _is_test_module(f"{module}.{alias.name}"):
                        target, name = f"{module}.{alias.name}", "*"
                    else:
                        continue
                    if target != own:
                        found.add((importer, target, name))
            elif isinstance(node, ast.Import):
                found.update(
                    (importer, alias.name, "*")
                    for alias in node.names
                    if _is_test_module(alias.name) and alias.name != own
                )
    return found


def _is_test_module(module: str) -> bool:
    parts = module.split(".")
    return len(parts) > 1 and parts[0] == "tests" and parts[-1].startswith("test_")


class CrossTestImportRatchetTest(unittest.TestCase):
    def test_no_new_import_between_test_modules(self) -> None:
        new = sorted(_cross_test_imports() - KNOWN_CROSS_TEST_IMPORTS)
        self.assertEqual(
            new,
            [],
            "A test module imports from another test module. Move the shared "
            "fake into a tests/*_helpers.py module and import it from there.",
        )

    def test_known_list_only_shrinks(self) -> None:
        gone = sorted(KNOWN_CROSS_TEST_IMPORTS - _cross_test_imports())
        self.assertEqual(
            gone,
            [],
            "These imports no longer exist: delete them from KNOWN_CROSS_TEST_IMPORTS.",
        )


if __name__ == "__main__":
    unittest.main()
