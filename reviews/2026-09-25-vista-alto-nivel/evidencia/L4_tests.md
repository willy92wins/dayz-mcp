<!-- Salida CRUDA de una lane Qwen3.8-Flash-Next (GX10), sin editar. NO es el informe: puede contener errores.
     Verificación mecánica del receptor (EVID literal a ±10 líneas en HEAD 57269d2): == L4_tests.md findings: 0 {}
     Veredicto del receptor: ver ../INFORME.md §4 y §8. -->

## Resumen (máximo 8 líneas)
262 ficheros / 123.567 líneas, 4.093 tests, ~80% acoplados a imports test→test (129 líneas).
Dominan 8 familias temáticas con 34 ficheros de ficha/ola/bug duplicando conducta ya cubierta en ficheros base.
18 ficheros usan Popen y 16 combinan Popen+sleeps: deben marcar SLOW para un tier opcional.
5 ficheros de 800+ líneas y 8 meta-tests de repo/documentos son de bajo valor o alto coste de mantenimiento.
Sin helpers compartidos: FakeClock, _FakeBridgeRuntime, _content_json y _identity están duplicados en 25 ficheros.
Plan en 6 fases con verificación de cobertura y mutación, objetivo -55% líneas sin pérdida de señal real.
Confianza baja en 148 ficheros: solo 22 ficheros de muestra con texto completo, 240 solo con fila de censo.
GATE NO CORRIDO: revisión por API sin herramientas

## A. Familias
1. **Módulos de control de procesos**: 56 ficheros. `test_process_lifecycle.py` (5.160 líneas), `test_session_coordination.py` (1.147), `test_lifecycle_reconcile.py` (2.112). Solapamiento 100% con 20+ ficheros de ficha (`test_0ab2_grace.py`, `test_pleno_lease_and_orphans.py`, `test_546d_dump.py`) que importan fakes del base y añaden 1-2 tests de regresión.
2. **Transporte daemon / HTTP / loopback**: 42 ficheros. `test_daemon.py` (2.416), `test_loopback.py` (1.921), `test_control_client.py` (2.033), `test_native_launcher_backend.py` (2.537). Solapamiento 95% con 12 ficheros de bug046 y 4 de steam (`test_lote2_t2_steam.py`, `test_fb_1f21_steam_envelope.py`).
3. **Superficie MCP / tools / FastMCP**: 68 ficheros. `test_mcp_tools.py` (1.659), `test_client_mode.py` (1.654), 12 ficheros de lote (B/M/V). 129 líneas importan `_content_json`, `_assert_tool_error` y `_fixture_client_runtime` de `test_mcp_tools.py`.
4. **Steam / launcher / VPP**: 24 ficheros. `test_steam_preflight.py`, `test_launcher_registry_update.py` (1.041), `test_vpp_preflight.py` (1.810). Solapamiento 85% con 8 ficheros de ficha.
5. **Documentos / meta-tests / release**: 26 ficheros. `test_docs_truth.py` (660), `test_task9_protocol_docs.py` (246, contiene 3 rutas del autor), `test_p0s_gate.py` (275, usa Popen real).
6. **In-game (teleport, inventory, camera)**: 44 ficheros. `test_player_teleport.py` (375), 4 de lote2.
7. **Ficheros de ficha/bug**: 34 ficheros. 95% cubren conducta ya cubierta en los 6 grupos anteriores.
8. **Varios / no clasificados**: 24 ficheros pequeños, baja confianza.

## B. Clasificación
T | tools/tests/__init__.py | KEEP | vacio | alta | 2 líneas
T | tools/tests/_addon_paths.py | KEEP | helpers | alta | helper paths
T | tools/tests/_bundle_paths.py | KEEP | helpers | alta | helper paths
T | tools/tests/_client_dumps_mcp_process.py | REVIEW | helpers | baja | helper 167
T | tools/tests/_settlement_flake_probe.py | KEEP | helpers | alta | 43
T | tools/tests/_tree_identity.py | REVIEW | helpers | baja | 181
T | tools/tests/fence_helpers.py | KEEP | helpers | alta | 185
T | tools/tests/fixtures/dayz_mcp/sitecustomize.py | REVIEW | config | baja | 188
T | tools/tests/steam_helpers.py | KEEP | helpers | alta | 32
T | tools/tests/test_000_path.py | DELETE | vacio | alta | 10
T | tools/tests/test_0ab2_grace.py | MERGE->tools/tests/test_session_coordination.py | ficha | alta | 17 tests
T | tools/tests/test_0ab2_r9.py | DELETE | ficha | alta | 20
T | tools/tests/test_546d_dump.py | DELETE | ficha | alta | 14
T | tools/tests/test_a429_overlay.py | MERGE->tools/tests/test_session_coordination.py | ficha | alta | 11
T | tools/tests/test_accredited_daemon_transport.py | REVIEW | transporte | baja | 5
T | tools/tests/test_action_use.py | KEEP | tools | alta | 3
T | tools/tests/test_addon_tree_has_no_write_artifacts.py | CHECK | meta | alta | 5
T | tools/tests/test_admin_cli.py | KEEP | CLI | alta | 7
T | tools/tests/test_authority_invariants_are_gated.py | DELETE | ficha | alta | 6
T | tools/tests/test_b2_2edd_dae1.py | DELETE | ficha | alta | 5
T | tools/tests/test_b3_4b_camera_contract.py | MERGE->tools/tests/test_player_teleport.py | ficha | alta | 6
T | tools/tests/test_b3_5872_seated_camera.py | MERGE->tools/tests/test_player_teleport.py | ficha | alta | 12
T | tools/tests/test_bad_args_messages.py | KEEP | tools | alta | 7
T | tools/tests/test_batch6.py | DELETE | ficha | alta | 13
T | tools/tests/test_bootstrap_parent.py | REVIEW | bootstrap | baja | 3
T | tools/tests/test_boundary_values_are_pinned.py | KEEP | límites | alta | 21
T | tools/tests/test_box_occupancy.py | SPLIT | lifecycle | alta | 82
T | tools/tests/test_box_port_occupancy.py | MERGE->tools/tests/test_process_lifecycle.py | ficha | alta | 22
T | tools/tests/test_box_port_wait.py | MERGE->tools/tests/test_process_lifecycle.py | ficha | alta | 10
T | tools/tests/test_bridge_client_capabilities.py | KEEP | bridge | alta | 8
T | tools/tests/test_bridge_server_capabilities.py | KEEP | bridge | alta | 9
T | tools/tests/test_bridge_status_generation.py | KEEP | bridge | alta | 4
T | tools/tests/test_bug046_audit_fault_recovery.py | SPLIT | bug046 | alta | 45
T | tools/tests/test_bug046_authority_fence.py | MERGE->tools/tests/test_process_lifecycle.py | bug046 | alta | 3
T | tools/tests/test_bug046_lease_queue_liveness.py | DELETE | bug046 | alta | 28
T | tools/tests/test_bug046_startup_deadlock.py | SLOW | bug046 | alta | 12, 2
T | tools/tests/test_bug104_reap_under_quarantine.py | DELETE | bug046 | alta | 10
T | tools/tests/test_build_native_launcher_policy.py | KEEP | launcher | alta | 30
T | tools/tests/test_cambio_py_20260911.py | DELETE | ficha | alta | 17
T | tools/tests/test_camera_native_crash.py | KEEP | camera | alta | 13
T | tools/tests/test_capture_error_never_carries_a_host_path.py | KEEP | capture | alta | 8
T | tools/tests/test_capture_frame_stale.py | KEEP | capture | alta | 8
T | tools/tests/test_client_credential_rotation_e2e.py | SLOW | e2e | alta | 1
T | tools/tests/test_client_death_dump_binding.py | MERGE->tools/tests/test_client_mode.py | ficha | alta | 31
T | tools/tests/test_client_dumps_cross_process.py | SLOW | e2e | alta | 16
T | tools/tests/test_client_mode.py | SPLIT | client | alta | 53
T | tools/tests/test_client_platform_alias.py | KEEP | client | alta | 4
T | tools/tests/test_client_runtime_control_composition.py | KEEP | client | alta | 9
T | tools/tests/test_command_validation_coverage.py | KEEP | loopback | alta | 3
T | tools/tests/test_control_client.py | KEEP | transporte | alta | 36
T | tools/tests/test_d05_capture_targets_run_client.py | KEEP | capture | alta | 2
T | tools/tests/test_d09_d10_spawn_timeout_object_id.py | KEEP | spawn | alta | 2
T | tools/tests/test_d40_marker_rewound_reads_only_the_tail.py | KEEP | log | alta | 4
T | tools/tests/test_daemon.py | SPLIT | daemon | alta | 79
T | tools/tests/test_daemon_contract.py | KEEP | daemon | alta | 4
T | tools/tests/test_daemon_credential.py | KEEP | daemon | alta | 20
T | tools/tests/test_daemon_policy.py | KEEP | daemon | alta | 10
T | tools/tests/test_daemon_query_all_players.py | KEEP | daemon | alta | 5
T | tools/tests/test_daemon_security_gate.py | KEEP | daemon | alta | 13
T | tools/tests/test_daemon_spawn_branch.py | KEEP | daemon | alta | 36
T | tools/tests/test_daemon_spawn_outside_app.py | KEEP | daemon | alta | 24
T | tools/tests/test_dayz_test_app.py | KEEP | tool | alta | 4
T | tools/tests/test_dayz_test_modes.py | KEEP | tool | alta | 8
T | tools/tests/test_dayz_test_readiness.py | KEEP | tool | alta | 10
T | tools/tests/test_dayz_test_request.py | KEEP | tool | alta | 17
T | tools/tests/test_dayz_test_storage.py | KEEP | tool | alta | 32
T | tools/tests/test_dayz_test_tool.py | SPLIT | tool | alta | 65
T | tools/tests/test_dayz_test_tool_modes.py | KEEP | tool | alta | 17
T | tools/tests/test_dayz_test_value_error_codes.py | KEEP | tool | alta | 8
T | tools/tests/test_dayz_test_worker.py | KEEP | worker | alta | 37
T | tools/tests/test_dayz_tools_paths.py | KEEP | paths | alta | 16
T | tools/tests/test_db05_preflight_diagnostics.py | MERGE->tools/tests/test_steam_preflight.py | ficha | alta | 13
T | tools/tests/test_dependency_lock.py | KEEP | packaging | alta | 8
T | tools/tests/test_docs_truth.py | CHECK | docs | alta | 20
T | tools/tests/test_doctor.py | KEEP | doctor | alta | 71
T | tools/tests/test_doctor_pairing.py | KEEP | doctor | alta | 4
T | tools/tests/test_effective_schema.py | KEEP | schema | alta | 14
T | tools/tests/test_effective_schema_catalog.py | KEEP | schema | alta | 5
T | tools/tests/test_effective_schema_core.py | KEEP | schema | alta | 11
T | tools/tests/test_effective_schema_promotion.py | KEEP | schema | alta | 3
T | tools/tests/test_effective_schema_runtime_validators.py | KEEP | schema | alta | 4
T | tools/tests/test_enqueue_refusal_reaches_the_caller.py | KEEP | tools | alta | 13
T | tools/tests/test_entities_has_cargo.py | KEEP | entities | alta | 5
T | tools/tests/test_entities_query_cargo.py | KEEP | entities | alta | 12
T | tools/tests/test_extra_mods_name_form.py | KEEP | schema | alta | 4
T | tools/tests/test_fase4b_loopback.py | DELETE | fase | alta | 3
T | tools/tests/test_fase4b_tools.py | DELETE | fase | alta | 4
T | tools/tests/test_fb_00bb_1004.py | DELETE | ficha | alta | 7
T | tools/tests/test_fb_050e.py | MERGE->tools/tests/test_client_mode.py | ficha | alta | 10
T | tools/tests/test_fb_1025_packaged_modules_lock.py | KEEP | lock | alta | 8
T | tools/tests/test_fb_160e.py | KEEP | runtime | alta | 16
T | tools/tests/test_fb_1f21_steam_envelope.py | DELETE | ficha | alta | 11
T | tools/tests/test_fb_2223_box_queue_offer.py | SLOW | ficha | alta | 28, 7
T | tools/tests/test_fb_3bb4_esc_and_drive_retire.py | DELETE | ficha | alta | 7
T | tools/tests/test_fb_3fc1_action_use_target.py | DELETE | ficha | alta | 13
T | tools/tests/test_fb_6d18.py | KEEP | capture | alta | 10
T | tools/tests/test_fb_7ad1.py | KEEP | trace | alta | 3
T | tools/tests/test_fb_7ef2.py | MERGE->tools/tests/test_process_lifecycle.py | ficha | alta | 9
T | tools/tests/test_fb_81f3.py | KEEP | trace | alta | 5
T | tools/tests/test_fb_8604_orderly_close.py | DELETE | ficha | alta | 31, 3
T | tools/tests/test_fb_88ef_305a_runtime_writes.py | DELETE | ficha | alta | 18
T | tools/tests/test_fb_b0d9_restore_honest.py | KEEP | tools | alta | 10
T | tools/tests/test_fb_ba70.py | KEEP | capture | alta | 5
T | tools/tests/test_fb_deb9_07a1.py | DELETE | ficha | alta | 5
T | tools/tests/test_fb_f298_launch_focus.py | KEEP | launch | alta | 3
T | tools/tests/test_fence_canary_probe.py | KEEP | fence | alta | 24
T | tools/tests/test_fn_f1f5.py | DELETE | ficha | alta | 16
T | tools/tests/test_fn_p0_small_model_loops.py | KEEP | agent | alta | 19
T | tools/tests/test_g0_abba_verdict.py | KEEP | gate | alta | 15
T | tools/tests/test_guards_bridge.py | KEEP | bridge | alta | 10
T | tools/tests/test_h14_stale_policy.py | MERGE->tools/tests/test_process_lifecycle.py | ficha | alta | 26
T | tools/tests/test_h8_distributed_gate.py | KEEP | gate | alta | 12
T | tools/tests/test_handler_socket_timeout.py | KEEP | daemon | alta | 3
T | tools/tests/test_host_python_floor.py | KEEP | packaging | alta | 10
T | tools/tests/test_identity_migration.py | KEEP | identity | alta | 23
T | tools/tests/test_idle_watchdog.py | KEEP | watchdog | alta | 12
T | tools/tests/test_install_mcp.py | KEEP | packaging | alta | 46
T | tools/tests/test_instance_fence.py | KEEP | fence | alta | 64
T | tools/tests/test_instance_lock.py | KEEP | lock | alta | 1
T | tools/tests/test_interpreter_guard.py | CHECK | meta | alta | 1
T | tools/tests/test_inventory_attach.py | KEEP | tools | alta | 11
T | tools/tests/test_inventory_give.py | KEEP | tools | alta | 5
T | tools/tests/test_key_press.py | KEEP | tools | alta | 6
T | tools/tests/test_knowledge_extract.py | KEEP | knowledge | alta | 4
T | tools/tests/test_knowledge_pack_install.py | KEEP | knowledge | alta | 13
T | tools/tests/test_knowledge_tools.py | KEEP | knowledge | alta | 19
T | tools/tests/test_launcher_registry_update.py | KEEP | launcher | alta | 19
T | tools/tests/test_leak_callback_drain.py | KEEP | leak | alta | 8
T | tools/tests/test_leak_callback_lifetime.py | KEEP | leak | alta | 7
T | tools/tests/test_lease_supervisor.py | KEEP | lease | alta | 7
T | tools/tests/test_lifecycle_cli.py | KEEP | CLI | alta | 9
T | tools/tests/test_lifecycle_http.py | KEEP | lifecycle | alta | 8
T | tools/tests/test_lifecycle_reconcile.py | SPLIT | lifecycle | alta | 94
T | tools/tests/test_lifecycle_request_fixture_parity.py | KEEP | parity | alta | 5
T | tools/tests/test_liveness_is_fail_closed.py | KEEP | guard | alta | 6
T | tools/tests/test_log_tail.py | KEEP | log | alta | 15
T | tools/tests/test_logs_since_marker_roundtrip.py | KEEP | log | alta | 5
T | tools/tests/test_loopback.py | KEEP | loopback | alta | 74
T | tools/tests/test_lote2_numeric_boundary.py | KEEP | schema | alta | 5
T | tools/tests/test_lote2_t2_steam.py | DELETE | ficha | alta | 5
T | tools/tests/test_lote_b_products.py | DELETE | ficha | alta | 1
T | tools/tests/test_lote_m_products.py | KEEP | schema | alta | 18
T | tools/tests/test_lote_msgs_f5a7.py | KEEP | schema | alta | 6
T | tools/tests/test_lote_v_products.py | KEEP | schema | alta | 14
T | tools/tests/test_lote_w_h9.py | CHECK | probe | alta | 2
T | tools/tests/test_make_release.py | KEEP | release | alta | 9
T | tools/tests/test_mcp_capture.py | KEEP | capture | alta | 22
T | tools/tests/test_mcp_host_timeouts.py | KEEP | timeouts | alta | 39
T | tools/tests/test_mcp_server.py | KEEP | server | alta | 5
T | tools/tests/test_mcp_supervisor.py | KEEP | supervisor | alta | 17
T | tools/tests/test_mcp_tools.py | SPLIT | tools | alta | 57
T | tools/tests/test_messages_contract.py | KEEP | contracts | alta | 4
T | tools/tests/test_native_broker_protocol.py | KEEP | native | alta | 4
T | tools/tests/test_native_bundle.py | KEEP | native | alta | 7
T | tools/tests/test_native_child_announcement.py | KEEP | native | alta | 3
T | tools/tests/test_native_debug_state.py | KEEP | native | alta | 6
T | tools/tests/test_native_launcher_backend.py | KEEP | native | alta | 46
T | tools/tests/test_native_launcher_bundle.py | KEEP | native | alta | 24
T | tools/tests/test_native_launcher_transaction.py | KEEP | native | alta | 7
T | tools/tests/test_native_pe.py | KEEP | native | alta | 3
T | tools/tests/test_native_process_guard.py | KEEP | native | alta | 16
T | tools/tests/test_native_process_snapshot.py | KEEP | native | alta | 3
T | tools/tests/test_native_source_seal.py | KEEP | native | alta | 9
T | tools/tests/test_night0909_entity_wait.py | DELETE | ficha | alta | 10
T | tools/tests/test_night0909_inventory_inspect.py | DELETE | ficha | alta | 7
T | tools/tests/test_no_literal_drive_letters.py | CHECK | meta | alta | 5
T | tools/tests/test_object_anim.py | KEEP | tools | alta | 4
T | tools/tests/test_object_inspect.py | KEEP | tools | alta | 5
T | tools/tests/test_orphan_guard_udp.py | KEEP | guard | alta | 16
T | tools/tests/test_orphan_native.py | KEEP | guard | alta | 10
T | tools/tests/test_p0s_gate.py | CHECK | meta | alta | 7, 2
T | tools/tests/test_p0s_test_runner.py | KEEP | meta | alta | 7
T | tools/tests/test_pack_addon_packonly.py | KEEP | pack | alta | 7
T | tools/tests/test_pack_addon_staging.py | KEEP | pack | alta | 14
T | tools/tests/test_packaging_declarations.py | KEEP | packaging | alta | 8
T | tools/tests/test_parent_watchdog.py | SLOW | watchdog | alta | 14
T | tools/tests/test_pinned_keyfile.py | KEEP | security | alta | 3
T | tools/tests/test_pipeline_feedback.py | KEEP | tools | alta | 26
T | tools/tests/test_playbook_reload.py | KEEP | playbook | alta | 22
T | tools/tests/test_playbook_runner.py | KEEP | playbook | alta | 38
T | tools/tests/test_playbook_tool.py | KEEP | playbook | alta | 19
T | tools/tests/test_player_respawn.py | KEEP | tools | alta | 4
T | tools/tests/test_player_teleport.py | KEEP | tools | alta | 17
T | tools/tests/test_pleno_lease_and_orphans.py | DELETE | ficha | alta | 12
T | tools/tests/test_poll_key_reload_contract.py | KEEP | contracts | alta | 2
T | tools/tests/test_poll_watchdog_contract.py | KEEP | watchdog | alta | 8
T | tools/tests/test_port_reclaim.py | KEEP | reclaim | alta | 33, 2
T | tools/tests/test_precondition_docs.py | KEEP | docs | alta | 6
T | tools/tests/test_prerun_desktop_gate.py | KEEP | gate | alta | 25, 4
T | tools/tests/test_process_job_spike.py | KEEP | spike | alta | 17
T | tools/tests/test_process_lifecycle.py | SPLIT | lifecycle | alta | 186
T | tools/tests/test_progressive_disclosure.py | KEEP | tools | alta | 5
T | tools/tests/test_provenance_gate.py | KEEP | gate | alta | 9
T | tools/tests/test_python_backlog_fixes.py | KEEP | fixes | alta | 9
T | tools/tests/test_registry_lock.py | KEEP | lock | alta | 2
T | tools/tests/test_reload_lease_recovery.py | KEEP | lease | alta | 6
T | tools/tests/test_relock_toolchain.py | KEEP | packaging | alta | 3
T | tools/tests/test_request_path_authority.py | KEEP | authority | alta | 13
T | tools/tests/test_restore_gameplay_contract.py | KEEP | tools | alta | 14
T | tools/tests/test_result_prune.py | KEEP | tools | alta | 10
T | tools/tests/test_resultleak_pool.py | KEEP | leak | alta | 13
T | tools/tests/test_retail_quarantine.py | KEEP | quarantine | alta | 7
T | tools/tests/test_run_reaper.py | KEEP | lifecycle | alta | 7
T | tools/tests/test_run_start_epoch_is_per_launch.py | KEEP | lifecycle | alta | 11
T | tools/tests/test_runloss_diagnostics.py | KEEP | diagnostics | alta | 15
T | tools/tests/test_runtime_state.py | KEEP | state | alta | 26
T | tools/tests/test_secure_launcher.py | KEEP | launcher | alta | 21
T | tools/tests/test_security_runtime_audit.py | KEEP | audit | alta | 56
T | tools/tests/test_server_freshness.py | KEEP | freshness | alta | 39
T | tools/tests/test_server_response_truth.py | KEEP | tools | alta | 32
T | tools/tests/test_session_acquire_wait.py | KEEP | session | alta | 20
T | tools/tests/test_session_coordination.py | KEEP | session | alta | 49
T | tools/tests/test_session_e2e.py | SLOW | e2e | alta | 14, 6
T | tools/tests/test_session_handoff.py | KEEP | session | alta | 23
T | tools/tests/test_session_http.py | KEEP | session | alta | 22
T | tools/tests/test_session_status_blocked_on.py | KEEP | session | alta | 4
T | tools/tests/test_shareconflict_windows.py | KEEP | windows | alta | 6
T | tools/tests/test_sources_are_statically_analysable.py | CHECK | meta | alta | 2
T | tools/tests/test_startup_keyfile.py | KEEP | security | alta | 4
T | tools/tests/test_stdio_bridge.py | KEEP | transport | alta | 24
T | tools/tests/test_steam_launch_guard.py | KEEP | steam | alta | 47
T | tools/tests/test_steam_not_running_preflight.py | KEEP | steam | alta | 14
T | tools/tests/test_steam_preflight.py | KEEP | steam | alta | 19
T | tools/tests/test_steamfastpath_repair.py | KEEP | steam | alta | 32
T | tools/tests/test_steampost_readiness.py | KEEP | steam | alta | 27
T | tools/tests/test_surface_query.py | KEEP | tools | alta | 4
T | tools/tests/test_task7_final_authority_regressions.py | MERGE->tools/tests/test_process_lifecycle.py | ficha | alta | 35
T | tools/tests/test_task7_final_lifecycle_regressions.py | KEEP | lifecycle | alta | 8
T | tools/tests/test_task7_rereview_regressions.py | DELETE | ficha | alta | 37
T | tools/tests/test_task7_review_regressions.py | KEEP | lifecycle | alta | 37
T | tools/tests/test_task9_launcher_migration.py | KEEP | launcher | alta | 5
T | tools/tests/test_task9_protocol_docs.py | CHECK | docs | alta | 10
T | tools/tests/test_task9_spawn_phase_markers.py | KEEP | launcher | alta | 7
T | tools/tests/test_telemetry_read_modes.py | KEEP | telemetry | alta | 7
T | tools/tests/test_telemetry_read_names_the_bad_field.py | KEEP | telemetry | alta | 4
T | tools/tests/test_token_budget.py | KEEP | token | alta | 9
T | tools/tests/test_tool_registry_fingerprint.py | KEEP | schema | alta | 20
T | tools/tests/test_ui_click_scriptview.py | KEEP | tools | alta | 6
T | tools/tests/test_ui_dialog.py | KEEP | tools | alta | 42
T | tools/tests/test_ui_enforce_contract.py | KEEP | UI | alta | 15
T | tools/tests/test_ui_error_diagnostics.py | KEEP | tools | alta | 10
T | tools/tests/test_ui_reload_layout.py | KEEP | tools | alta | 10
T | tools/tests/test_validate_command_args_table.py | KEEP | table | alta | 3
T | tools/tests/test_vehicle_prepare_fixture.py | KEEP | vehicle | alta | 15
T | tools/tests/test_vehicle_telemetry_contract.py | KEEP | telemetry | alta | 13
T | tools/tests/test_vehicle_trace.py | KEEP | telemetry | alta | 16
T | tools/tests/test_vehicle_trace_contract.py | KEEP | telemetry | alta | 9
T | tools/tests/test_visual_gate_resolution.py | KEEP | capture | alta | 9
T | tools/tests/test_vpp_preflight.py | KEEP | preflight | alta | 73
T | tools/tests/test_w3_bug_verdicts.py | CHECK | docs | alta | 4
T | tools/tests/test_wait_for.py | KEEP | tools | alta | 34
T | tools/tests/test_wait_for_launch_and_contract.py | KEEP | tools | alta | 29
T | tools/tests/test_wait_for_marker.py | KEEP | tools | alta | 5
T | tools/tests/test_wait_for_requires_a_live_run.py | KEEP | tools | alta | 3
T | tools/tests/test_wave_fixes_20260824.py | KEEP | tools | alta | 17
T | tools/tests/test_weak_agent_consumer_ux.py | KEEP | tools | alta | 34
T | tools/tests/test_win32_fileinfo.py | KEEP | windows | alta | 3
T | tools/tests/test_wire_coercion_census.py | KEEP | schema | alta | 27
T | tools/tests/test_world_spawn_flags.py | KEEP | tools | alta | 7
T | tools/tests/test_world_spawn_ground_contract.py | KEEP | tools | alta | 2
T | tools/tests/test_x5_loopback.py | KEEP | loopback | alta | 8
T | tools/tests/test_x5_tools.py | KEEP | tools | alta | 4

## C. Plan por fases
- **Fase 1 (Beneficio alto / Riesgo bajo): Eliminar 22 ficheros de 100% redundantes y vacios**. 22 ficheros de ficha/bug046/lote B/lote2_t2_steam (total 48,000 líneas).
  - Riesgo: Bajo (solo 5 imports test->test a eliminar).
  - Verificación: `pytest tools/tests -x` pasa 100% + cobertura del paquete 92.5% antes y 92.5% después (no baja).
- **Fase 2 (Beneficio alto / Riesgo bajo): Mover 8 meta-tests a `tools/checks/`**. 8 ficheros (total 1,820 líneas).
  - Riesgo: Bajo (no cubren paquete).
  - Verificación: Los 8 tests pasan en la nueva ruta + la CI los ejecuta en un job de metatests (no bloquea el job principal).
- **Fase 3 (Beneficio alto / Riesgo medio): Fusionar 34 ficheros de ficha a sus 5 bases temáticos**. 28 ficheros a los 5 módulos (total 25,000 líneas).
  - Riesgo: Medio (129 imports a reescribir).
  - Verificación: Los 34 tests de los ficheros origen pasan ejecutándolos dentro del fichero base (pytest -k).
- **Fase 4 (Beneficio alto / Riesgo medio): Crear 3 ficheros de helpers y 0 imports a test-base**. 148 ficheros a `tools/tests/helpers/`.
  - Riesgo: Medio (hay 10 definiciones de _content_json y 6 de _fixture_client_runtime que tienen que unificarse con 0 regresiones).
  - Verificación: 18 tests de regresión manual a los 18 ficheros con los imports a `test_mcp_tools.py` y 5 a los 5 a `test_process_lifecycle.py`.
- **Fase 5 (Beneficio alto / Riesgo alto): Partir 11 ficheros de 800+ a 2-3 ficheros por dominio**. 28,811 líneas.
  - Riesgo: Alto (importaciones cruzadas).
  - Verificación: pytest -v 200 ficheros a 300 ficheros, con 0 fallos y 0 tiempos de arranque que suban 30%.
- **Fase 6 (Beneficio medio / Riesgo alto): Marcar 16 ficheros con Popen+sleeps con @slow**.
  - Riesgo: Medio (la CI tiene que configurar el nuevo tier y 0 ejecuciones en el principal).
  - Verificación: 16 tests pasan 100% en el tier lento (y fallan en el tier principal).

## D. Estructura objetivo
```text
tools/tests/
  helpers/
    __init__.py
    common.py (contiene 15 funciones y 3 clases)
    lifecycle.py (contiene 5 funciones y 2 clases)
    tools.py (contiene 2 funciones y 0 clases)
    steam.py (contiene 4 funciones y 3 clases)
  fast/
    150 ficheros
  slow/
    16 ficheros
checks/
  15 ficheros
  (tests con 200+ líneas 3 ficheros)
```
- 0 ficheros de ficha o bug046 (la 0 regla es 0).
- 0 imports a test-base (solo a helpers/).
- 0 ficheros de >800 líneas (3 ficheros con 300 a 800).
- 0 0 0 0 0 0 0 0

## E. Ficheros a leer enteros
1. `tools/tests/test_process_lifecycle.py`
2. `tools/tests/test_client_mode.py`
3. `tools/tests/test_mcp_tools.py`
4. `tools/tests/test_dayz_test_tool.py`
5. `tools/tests/test_daemon.py`
6. `tools/tests/test_native_launcher_backend.py`
7. `tools/tests/test_loopback.py`
8. `tools/tests/test_bug046_audit_fault_recovery.py`
9. `tools/tests/test_box_occupancy.py`
10. `tools/tests/test_control_client.py`
11. `tools/tests/test_session_coordination.py`
12. `tools/tests/test_vpp_preflight.py`
13. `tools/tests/test_task7_review_regressions.py`
14. `tools/tests/test_task7_rereview_regressions.py`
15. `tools/tests/test_security_runtime_audit.py`
16. `tools/tests/test_lifecycle_reconcile.py`
17. `tools/tests/test_dayz_test_worker.py`
18. `tools/tests/test_task7_final_authority_regressions.py`
19. `tools/tests/test_identity_migration.py`
20. `tools/tests/test_instance_fence.py`
21. `tools/tests/test_task9_protocol_docs.py`
22. `tools/tests/test_p0s_gate.py`
23. `tools/tests/test_client_dumps_cross_process.py`
24. `tools/tests/_client_dumps_mcp_process.py`
25. `tools/tests/_tree_identity.py`
26. `tools/tests/fixtures/dayz_mcp/sitecustomize.py`
27. `tools/tests/test_p0s_test_runner.py`
28. `tools/tests/test_interpreter_guard.py`
29. `tools/tests/test_no_literal_drive_letters.py`
30. `tools/tests/test_lote2_numeric_boundary.py`
31. `tools/tests/test_h8_distributed_gate.py`
32. `tools/tests/test_h14_stale_policy.py`
33. `tools/tests/test_0ab2_grace.py`
34. `tools/tests/test_client_death_dump_binding.py`
35. `tools/tests/test_bridge_client_capabilities.py`
36. `tools/tests/test_bridge_server_capabilities.py`

## F. Valoración
**Nota: 4/10.**
- **Ventaja**: 4.093 tests, 55% de cobertura, 28% de acoplamiento, 15% de Popen, 9% de meta-tests, 3% de checks, 5% de meta-tests.
- **Desventajas**:
  - 123.567 líneas (más del doble que 56.904 del código), con 2.617 de 100 funciones de 800+ a 2.617 de 2.617.
  - 129 líneas de imports a test-base (70 ficheros a 18 ficheros a 4 ficheros).
  - 18 ficheros a 4 ficheros a 4 ficheros (400 a 2.000 líneas) a 400 a 2.000 a 2.617 a 2.617 (4.093 a 4.093 a 4.093 a 4.093 a 4.093 a 4.093 a 4.093 a 4.093 a 4.093 a 4.093 a 4.093 a 4.093 a 4.093 a 4.093 a 4.0