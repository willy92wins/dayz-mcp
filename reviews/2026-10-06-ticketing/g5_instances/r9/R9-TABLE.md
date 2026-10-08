# DZ-R9 audit of g5 (independent instances): working table

Steps so far (21:10): 1 mechanical census (gpt-6.1-sol, `MECHANICAL.md`, M1-M11); 2 angle auditor A (GLM,
persistence/migration/recovery, `A/run/ws/AUDIT.md`), auditor B pending; 3 independent verifier on A
(gpt-6.1-sol, fresh, `VERIFY_A.md`); 3c orchestrator self-sample 2/7 of A (A4, A7): both confirmed by
reading the code, 0 confabulation.

| ID | Sev | Title | File:Lines | Found by | Verified by | Kind | Status |
|---|---|---|---|---|---|---|---|
| A1 | P1 | Recovery of an interrupted host-config restore rejects its own completed restore; the shared journal wedges every instance's installer until deleted by hand | host_config.py:1102-1119, :1183-1189 | A (executed) | V1 CONFIRMED/HOLDS | defect | fix |
| A2 | P1 | A journal killed during its first manifest publication stays invalid; `manifest.next` is never replayed; installers blocked | host_config.py:963-968, :993-1004 | M10, A (executed) | V2 CONFIRMED/HOLDS | defect | fix |
| A3 | P2 | Registration mutations have no durable journal; a kill or the PowerShell throw leaves hosts half registered (re-run repairs) | install_mcp.py:1185-1210; install-mcp.ps1:892-897 | M6, A | V3 CONFIRMED/HOLDS | defect | fix |
| A4 | P3 | Daemon fallback audit writer uses an undefined `config`: NameError swallowed, row dropped | daemon.py:1430-1436 | M7, A | V4 CONFIRMED/HOLDS; self-sample | defect | fix |
| A5 | P3 | Named exec-enforce audit stream split across two file names | daemon.py:343-352; server.py:858-859 | M8, A | V5 CONFIRMED/HOLDS | defect | fix |
| A6 | P2 | Lifetime lock opened with FILE_SHARE_DELETE: deleting the held lock file lets a second process lock a fresh file of the same name (two writers); owner identity is `id()` | instance_context.py:155-159, :188, :209 | A | V6 CONFIRMED / UNDETERMINED; orchestrator experiment on this host (Windows 11, temp dir): delete succeeded while held, second process locked byte 1 -> TWO HOLDERS | defect | fix (no FILE_SHARE_DELETE on lock files; check every lock open) |
| A7 | P3 | A relative `DAYZ_MCP_SHARED_ROOT` splits box admission per cwd (build locks fail closed: the worker requires an absolute root) | server_cli.py:83-88; box_admission.py:23-28 | M1, A | V7 CONFIRMED / DOES NOT HOLD as stated (build part); self-sample confirms the box-admission split | defect (narrowed) | fix: require an absolute override |
| B2 | P2 | The shared build lock waits at most ~10 s (`msvcrt.locking` LK_LOCK: 10 retries of 1 s, documented) and then raises; a second build of the same target fails (`internal_failure`) instead of waiting | server_cli.py:107-125, :165-169; dayz_test_worker.py:806-807 | B (measured 9.1 s) | W1 CONFIRMED / UNDETERMINED (verifier did not measure); documented semantics + B's measurement | defect | fix (bounded wait loop with an explicit `build_busy` code) |
| B3 | P2 | After a settled migration receipt, no daemon start scans for live writers again; a legacy tree hardcoded to the root (no lease) started later writes the same runs.json | identity_migration.py:1576-1601 | B | W2 CONFIRMED/HOLDS (verifier: deliberate for availability) | defect for this deployment (dayz-mcp-130 points at the legacy tree) | fix (scan for same-root writers on every start; the new classifier no longer over-blocks) + promotion step: retire the legacy tree and re-register |
| B4 | - | PowerShell duplicate `-Instance` | install-mcp.ps1:54-80 | B | W3 DOES NOT HOLD (ParameterAlreadyBound) | dropped | - |
| B1 | - | build-lock split by a relative root | server_cli.py:87-112 | B | W4 DOES NOT HOLD (request parser and worker require an absolute root); box-admission part = A7 | merged into A7 | - |
| M2 | P2 | knowledge_pack and stdio_bridge accept looser selector forms than the shared validator | knowledge_pack.py:324-339; stdio_bridge.py:418-442 | M2, A confirmed | pending verifier | defect | fix |
| M3 | P2 | server/client/embedded bind the instance without the environment-conflict rejection the daemon applies | server.py:7328 vs daemon.py:1545 | M3, A (probe) | pending verifier | defect | fix |
| M4 | P3 | doctor cannot select a named instance | doctor.py:1437 | M4, A | pending verifier | defect | fix |
| M5 | P3 | capture falls back to the default sidecar on any selection error | mcp_capture.py:556-572 | M5, A | pending verifier | defect | fix |
| M9 | P3 | killed native build leaves `.partial.<pid>` garbage | build_native_launcher.py:274-293 | M9, A | pending verifier | improvement | backlog |
| M11 | P3 | programmatic activation keeps the lease descriptor until explicit release or exit | daemon.py:401-413 | M11, A (designed) | n/a | improvement | backlog |
