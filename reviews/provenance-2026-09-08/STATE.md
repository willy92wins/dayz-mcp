## A - Ficheros creados/modificados

**host_config.py ha cambiado y esta en PACKAGED_MODULES (tools/build_native_launcher.py:62). Hace falta resellar el bundle nativo antes de desplegar. NO se ha resellado ni desplegado.**

- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\host_config.py` ? 45806 -> 46293 bytes. Relectura/reopen comparan el registro validado; fallos Win32 de apertura/seek/read conservan winerror. REQUIERE RESELLADO NATIVO antes de desplegar.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\control_client.py` ? 30452 -> 31868 bytes. Anade policy_cause separado y saneado; hint accionable sin cambiar el token ni autorizar HTTP tras rechazo.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_provenance_gate.py` ? 0 -> 12339 bytes. Tres tests T1/T2/T3 con subcasos positivos, negativos, fallos Win32 y control de cero HTTP.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\artifact-hashes.json` ? 0 -> 7580 bytes. Tamanos y SHA-256 finales de fuentes y evidencia; no es resellado del launcher.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\baseline-host.log` ? 0 -> 8225 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\baseline-unrelated.log` ? 0 -> 32085 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\baseline.json` ? 0 -> 437 bytes. Tamanos y SHA-256 previos de los tres modulos autorizados.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\check_baseline.py` ? 0 -> 845 bytes. Carga preimagenes solo en el proceso de tests; no revierte el arbol compartido.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\control_client.py.before` ? 0 -> 30452 bytes. Preimagen exacta del modulo, preservada para invertir el gate sin tocar el arbol.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\daemon_policy.py.before` ? 0 -> 13400 bytes. Preimagen exacta del modulo, preservada para invertir el gate sin tocar el arbol.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\diff-check.log` ? 0 -> 127 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\final-control-policy.log` ? 0 -> 9001 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\final-dayz-test-tool.log` ? 0 -> 11289 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\final-host.log` ? 0 -> 9398 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\GATES.md` ? 0 -> 829 bytes. Criterios, contraejemplos y presupuesto escritos antes de implementar; gate ejecutable por unittest.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\green.log` ? 0 -> 958 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\host_config.py.before` ? 0 -> 45806 bytes. Preimagen exacta del modulo, preservada para invertir el gate sin tocar el arbol.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\implementation.diff` ? 0 -> 7271 bytes. Diff de los dos modulos modificados; no incluye test nuevo no trazado.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\pipeline-feedback.md` ? 0 -> 1402 bytes. Hallazgos para el receptor; no enviados por MCP por restriccion del brief.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\red-final.log` ? 0 -> 13654 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\red.log` ? 0 -> 17863 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_admin_cli.log` ? 0 -> 1739 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_bad_args_messages.log` ? 0 -> 1917 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_bug046_audit_fault_recovery.log` ? 0 -> 9784 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_client_mode.log` ? 0 -> 17122 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_client_runtime_control_composition.log` ? 0 -> 2712 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_control_client.log` ? 0 -> 6499 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_daemon_contract.log` ? 0 -> 1129 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_daemon_credential.log` ? 0 -> 4264 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_daemon_policy.log` ? 0 -> 2222 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_dayz_test_tool.log` ? 0 -> 15632 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_dayz_test_value_error_codes.log` ? 0 -> 2196 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_doctor.log` ? 0 -> 9518 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_lifecycle_cli.log` ? 0 -> 2272 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_mcp_host_timeouts.log` ? 0 -> 9398 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_native_launcher_transaction.log` ? 0 -> 20803 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_python_backlog_fixes.log` ? 0 -> 1343 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_secure_launcher.log` ? 0 -> 4841 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_server_response_truth.log` ? 0 -> 4728 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_task7_final_lifecycle_regressions.log` ? 0 -> 2210 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\regression-test_vpp_preflight.log` ? 0 -> 12838 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\requested-host-config.log` ? 0 -> 1032 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\run_regressions.py` ? 0 -> 955 bytes. Lista explicita de 20 modulos offline ejecutados en serie.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\run_tests.py` ? 0 -> 1793 bytes. Runner de nombres explicitos, PYTHONPATH/cwd correctos y logs re-leidos byte a byte.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\startup-offline.log` ? 0 -> 3851 bytes. Comando y salida literal de la prueba; incluye exit code y verificable en disco.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\STATE.md` ? 0 -> 40526 bytes. Informe de entrega A-E; bytes finales incluyen esta misma fila.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\DONE` ? 0 -> 0 bytes. Marcador vacio de fin de la lane; no significa desplegado ni suite completa verde.

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\daemon_policy.py` permanece intacto: 13400 bytes; SHA-256 coincide con baseline.json. Su hook ya compara la politica semantica entre peticiones.
No git add, commit, stash ni restauracion de ficheros compartidos. El indice conserva decisions/decision-log.md staged; no se escribio en el indice.

## B - Resultado de las pruebas

Gate de esta lane: T1/T2/T3 VERDE (ControlClient, transporte simulado). No se declara regresion global verde. T1 ROJO real antes de editar en red.log; T2 ya era verde. red-final.log repite la inversion con las preimagenes exactas y el test final, sin revertir el arbol. T1 usa handles reales entre peticiones y doubles explicitos durante la resolucion porque Windows impide esa carrera por comparticion.

Regresion: 20 modulos enumerados en serie; luego comprobaciones finales dirigidas tras preservar winerror. Persisten dos expectativas antiguas incompatibles en host_config y ocho errores de autoridad de rutas independientes de L6. Los cuatro fallos iniciales de dayz_test_tool ya no aparecen en su pasada final. Todas las salidas completas estan en los logs; abajo se transcriben comandos y lineas de resumen sin reinterpretar el exit code.

`red.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_provenance_gate -v

test_t1_same_registration_survives_whole_file_replacement (tests.test_provenance_gate.ProvenanceGateTests.test_t1_same_registration_survives_whole_file_replacement) ... 
  test_t1_same_registration_survives_whole_file_replacement (tests.test_provenance_gate.ProvenanceGateTests.test_t1_same_registration_survives_whole_file_replacement) (platform='claude', phase='reopen') ... ERROR
  test_t1_same_registration_survives_whole_file_replacement (tests.test_provenance_gate.ProvenanceGateTests.test_t1_same_registration_survives_whole_file_replacement) (platform='claude', phase='reread') ... ERROR
  test_t1_same_registration_survives_whole_file_replacement (tests.test_provenance_gate.ProvenanceGateTests.test_t1_same_registration_survives_whole_file_replacement) (platform='codex', phase='reopen') ... ERROR
  test_t1_same_registration_survives_whole_file_replacement (tests.test_provenance_gate.ProvenanceGateTests.test_t1_same_registration_survives_whole_file_replacement) (platform='codex', phase='reread') ... ERROR
test_t2_changed_registration_is_rejected_without_http (tests.test_provenance_gate.ProvenanceGateTests.test_t2_changed_registration_is_rejected_without_http) ... ok
test_t3_rejection_cause_is_separate_safe_and_actionable (tests.test_provenance_gate.ProvenanceGateTests.test_t3_rejection_cause_is_separate_safe_and_actionable) ... 
  test_t3_rejection_cause_is_separate_safe_and_actionable (tests.test_provenance_gate.ProvenanceGateTests.test_t3_rejection_cause_is_separate_safe_and_actionable) (cause='HostConfigError:daemon_provenance_conflict') ... ERROR
  test_t3_rejection_cause_is_separate_safe_and_actionable (tests.test_provenance_gate.ProvenanceGateTests.test_t3_rejection_cause_is_separate_safe_and_actionable) (cause='HostConfigError:daemon_provenance_incomplete') ... ERROR
  test_t3_rejection_cause_is_separate_safe_and_actionable (tests.test_provenance_gate.ProvenanceGateTests.test_t3_rejection_cause_is_separate_safe_and_actionable) (cause='ValueError:daemon_policy_drift') ... ERROR
  test_t3_rejection_cause_is_separate_safe_and_actionable (tests.test_provenance_gate.ProvenanceGateTests.test_t3_rejection_cause_is_separate_safe_and_actionable) (cause='PermissionError:13') ... ERROR
  test_t3_rejection_cause_is_separate_safe_and_actionable (tests.test_provenance_gate.ProvenanceGateTests.test_t3_rejection_cause_is_separate_safe_and_actionable) (cause='RuntimeError') ... ERROR
test_t3_rejection_cause_is_separate_safe_and_actionable (tests.test_provenance_gate.ProvenanceGateTests.test_t3_rejection_cause_is_separate_safe_and_actionable) ... FAIL
Ran 3 tests in 0.737s
FAILED (failures=1, errors=9)
EXIT_CODE=1
```

`red-final.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\check_baseline.py' tests.test_provenance_gate -v

test_t1_same_registration_survives_whole_file_replacement (tests.test_provenance_gate.ProvenanceGateTests.test_t1_same_registration_survives_whole_file_replacement) ... 
  test_t1_same_registration_survives_whole_file_replacement (tests.test_provenance_gate.ProvenanceGateTests.test_t1_same_registration_survives_whole_file_replacement) (platform='claude', phase='reopen') ... ERROR
  test_t1_same_registration_survives_whole_file_replacement (tests.test_provenance_gate.ProvenanceGateTests.test_t1_same_registration_survives_whole_file_replacement) (platform='claude', phase='reread') ... ERROR
  test_t1_same_registration_survives_whole_file_replacement (tests.test_provenance_gate.ProvenanceGateTests.test_t1_same_registration_survives_whole_file_replacement) (platform='codex', phase='reopen') ... ERROR
  test_t1_same_registration_survives_whole_file_replacement (tests.test_provenance_gate.ProvenanceGateTests.test_t1_same_registration_survives_whole_file_replacement) (platform='codex', phase='reread') ... ERROR
test_t2_changed_registration_is_rejected_without_http (tests.test_provenance_gate.ProvenanceGateTests.test_t2_changed_registration_is_rejected_without_http) ... ok
test_t3_rejection_cause_is_separate_safe_and_actionable (tests.test_provenance_gate.ProvenanceGateTests.test_t3_rejection_cause_is_separate_safe_and_actionable) ... ERROR
Ran 3 tests in 0.696s
FAILED (errors=5)
EXIT_CODE=1
```

`green.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_provenance_gate -v

test_t1_same_registration_survives_whole_file_replacement (tests.test_provenance_gate.ProvenanceGateTests.test_t1_same_registration_survives_whole_file_replacement) ... ok
test_t2_changed_registration_is_rejected_without_http (tests.test_provenance_gate.ProvenanceGateTests.test_t2_changed_registration_is_rejected_without_http) ... ok
test_t3_rejection_cause_is_separate_safe_and_actionable (tests.test_provenance_gate.ProvenanceGateTests.test_t3_rejection_cause_is_separate_safe_and_actionable) ... ok
Ran 3 tests in 0.803s
OK
EXIT_CODE=0
```

`requested-host-config.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_host_config -v

Ran 1 test in 0.000s
FAILED (errors=1)
EXIT_CODE=1
```

`final-host.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_mcp_host_timeouts -v

Ran 38 tests in 1.225s
FAILED (failures=2, skipped=1)
EXIT_CODE=1
```

`final-control-policy.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_control_client tests.test_daemon_policy tests.test_daemon_contract -v

Ran 48 tests in 0.619s
OK
EXIT_CODE=0
```

`baseline-host.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\check_baseline.py' tests.test_mcp_host_timeouts -v

Ran 38 tests in 1.089s
OK (skipped=1)
EXIT_CODE=0
```

`baseline-unrelated.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\check_baseline.py' tests.test_dayz_test_tool tests.test_native_launcher_transaction -v

Ran 69 tests in 0.601s
FAILED (errors=8)
EXIT_CODE=1
```

`final-dayz-test-tool.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_dayz_test_tool -v

Ran 62 tests in 0.505s
OK
EXIT_CODE=0
```

`startup-offline.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_bug046_startup_deadlock.BackupTransactionAdversarialTest -v

Ran 15 tests in 0.956s
OK (skipped=1)
EXIT_CODE=0
```

`regression-test_admin_cli.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_admin_cli -v

Ran 7 tests in 0.035s
OK
EXIT_CODE=0
```

`regression-test_bad_args_messages.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_bad_args_messages -v

Ran 7 tests in 0.733s
OK
EXIT_CODE=0
```

`regression-test_bug046_audit_fault_recovery.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_bug046_audit_fault_recovery -v

Ran 45 tests in 2.405s
OK
EXIT_CODE=0
```

`regression-test_client_mode.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_client_mode -v

Ran 53 tests in 8.206s
OK
EXIT_CODE=0
```

`regression-test_client_runtime_control_composition.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_client_runtime_control_composition -v

Ran 9 tests in 0.896s
OK
EXIT_CODE=0
```

`regression-test_control_client.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_control_client -v

Ran 34 tests in 0.761s
OK
EXIT_CODE=0
```

`regression-test_daemon_contract.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_daemon_contract -v

Ran 4 tests in 0.138s
OK
EXIT_CODE=0
```

`regression-test_daemon_credential.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_daemon_credential -v

Ran 20 tests in 0.075s
OK
EXIT_CODE=0
```

`regression-test_daemon_policy.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_daemon_policy -v

Ran 10 tests in 0.071s
OK
EXIT_CODE=0
```

`regression-test_dayz_test_tool.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_dayz_test_tool -v

Ran 62 tests in 0.569s
FAILED (failures=4)
EXIT_CODE=1
```

`regression-test_dayz_test_value_error_codes.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_dayz_test_value_error_codes -v

Ran 8 tests in 0.110s
OK
EXIT_CODE=0
```

`regression-test_doctor.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_doctor -v

Ran 59 tests in 0.163s
OK
EXIT_CODE=0
```

`regression-test_lifecycle_cli.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_lifecycle_cli -v

Ran 9 tests in 0.022s
OK
EXIT_CODE=0
```

`regression-test_mcp_host_timeouts.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_mcp_host_timeouts -v

Ran 38 tests in 1.285s
FAILED (failures=2, skipped=1)
EXIT_CODE=1
```

`regression-test_native_launcher_transaction.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_native_launcher_transaction -v

Ran 7 tests in 0.145s
FAILED (errors=8)
EXIT_CODE=1
```

`regression-test_python_backlog_fixes.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_python_backlog_fixes -v

Ran 5 tests in 1.534s
OK
EXIT_CODE=0
```

`regression-test_secure_launcher.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_secure_launcher -v

Ran 21 tests in 0.636s
OK (skipped=2)
EXIT_CODE=0
```

`regression-test_server_response_truth.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_server_response_truth -v

Ran 15 tests in 1.231s
OK
EXIT_CODE=0
```

`regression-test_task7_final_lifecycle_regressions.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_task7_final_lifecycle_regressions -v

Ran 8 tests in 0.099s
OK
EXIT_CODE=0
```

`regression-test_vpp_preflight.log`

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_vpp_preflight -v

Ran 73 tests in 3.313s
OK
EXIT_CODE=0
```

`diff-check.log`

```text
git diff --check -- tools/dayz_mcp/host_config.py tools/dayz_mcp/daemon_policy.py tools/dayz_mcp/control_client.py
EXIT_CODE=0
```

## C - Hallazgos y decisiones

1. **Correccion a la causa raiz afirmada.** Las lineas citadas eran correctas al inicio: host_config.py:396/403 comparaban identidad y bytes, pero sus snapshots se crean en la misma llamada (:337-348 original, preimagen en host_config.py.before). daemon_policy.py:323-330 conserva la politica, no esos snapshots. Un rename varias horas despues no basta para explicar el incidente. T1 confirma que la reescritura real ENTRE peticiones ya pasa con el codigo viejo; sus cuatro rojos ocurren en la ventana DENTRO de la resolucion mediante doubles declarados.

2. **Cambio conservador conforme al contrato.** host_config.py:392-413 repite el mismo parser estricto sobre el handle original y el reopen, comparando _ClientRegistration. Mantiene parser JSON/TOML, claves admitidas, command local/canonico, parametros, keyfile, presupuestos por valor, consenso entre hosts, politica sellada y fail-closed ante cualquier excepcion. No se cambia _registration_from_entry (ahora :215) ni _registration_from_raw (:302). JSON/TOML invalido, incluso fuera de dayz-mcp, sigue rechazandose; no se amplia lo aceptado cuando el fichero permanece estatico. Los campos normalizados son los de _ClientRegistration; task-label ya se omitia de esa autoridad antes de este cambio.

3. **Proteccion de ruta intacta.** _PinnedConfigFile (:701) y _assert_no_reparse_parents (:668) conservan comprobaciones canonicas/no-reparse y FILE_SHARE_READ en Windows. El cambio elimina la identidad del archivo como criterio de equivalencia, tal como pide el brief. No elimina la acreditacion de la ruta ni permite entrada ausente o diferente. Dos registros validos que acuerdan otro keyfile siguen rechazados por daemon_policy_drift. T2 contiene 48 subcasos (2 hosts x 8 mutaciones x 3 ventanas) y el cambio consensuado de keyfile: todos sin HTTP, antes y despues.

4. **El motivo viaja hasta ControlClient; limite de publicacion MCP.** control_client.py:30/37 expone `policy_cause` separado de `.code`; :43 implementa el patron de tipo + errno/winerror o token ASCII acotado de _bridge_status_cause (dayz_test_tool.py:901 al cierre, publicacion :1587; ambas citas se movieron por otra lane). Nunca concatena el motivo al token de error. host_config.py:31,719,773,783 preserva winerror en apertura, seek y lectura; el error textual sigue daemon_provenance_conflict. T3 prueba el camino real de registro ausente y del lector con Win32 inyectado (32/5), ademas de conflicto, drift y excepcion con texto sensible. `hint` pide informar al host/operator para verificar registro y reconectar tras reparar; explica que el cliente no puede abrir una sesion del host.

   **FUERA DE MI ALCANCE:** server.py:1267-1300 transforma ControlClientError a ToolError de texto. El hint SI llega por esa via existente; policy_cause NO se publica hoy como campo hermano JSON en MCP. No lo he ocultado en el token ni he parcheado server.py/dayz_test_tool.py por una via indirecta. El receptor necesita cablear explicitamente ese campo en el serializador antes de afirmar cumplimiento del viaje estructurado hasta MCP. T3 acredita la API ControlClient.session_status, no ese serializador final.

5. **Dos tests existentes contradicen el nuevo contrato.** tests/test_mcp_host_timeouts.py:534 exige rechazo de identidad nueva con los mismos bytes; :731 exige rechazo al anadir solo un salto de linea. Pasan con preimagenes (baseline-host.log: 38, OK, skipped=1), fallan con la comparacion semantica (final-host.log: 38, failures=2, skipped=1). No se alteraron, suprimieron ni marcaron expectedFailure: el brief permite SOLO ANADIR casos a existentes. Quedan al receptor para reconciliar esas expectativas con la nueva autoridad aprobada. Todos los demas casos ejecutables del modulo se mantienen verdes.

6. **Nombre inexistente en el brief.** `tests.test_host_config` no existe: requested-host-config.log registra el ModuleNotFoundError literal. El modulo correcto localizado con rg es tests.test_mcp_host_timeouts. No cree un alias artificial fuera del prefijo de tests nuevos permitido.

7. **Otros rojos y arbol concurrente.** test_native_launcher_transaction produce ocho errores de setup invalid_dayz_test_path_authority en request_path_authority.py:203/323, ANTES de ejercitar las operaciones. Se reproducen con los tres ficheros originales de L6 cargados en un proceso independiente (baseline-unrelated.log); no se atribuyen a esta lane. La causa ultima de esas restricciones de rutas no se ha aislado. Cuatro fallos iniciales de test_dayz_test_tool por claves extra del resultado desaparecieron en la corrida final (62 OK); no he tocado ese modulo ni su test y el arbol cambia simultaneamente. No llamo corrupcion a ninguno de estos resultados.

8. **Evaluacion de 050e, SIN implementar excepciones de confianza.** Arreglar la comparacion evita el rechazo estrecho por representacion durante una lectura; NO demuestra que desaparezca el bloqueo de stop/lecturas observado en produccion. Una exencion para lectura ampliaria los metodos accesibles sin procedencia del host acreditada. Una exencion para stop ampliaria esa superficie a la terminacion de procesos del run; comprobar solo un owner_session_id presentado por el cliente no sustituye acreditar al llamante. Habria que conservar autenticacion, binding de identidad, propiedad real del run, lease y lifecycle guard. Ademas, quitar solo la primera comprobacion del ControlClient no basta: daemon_credential.py:65/67/133 revalida de nuevo. No se relaja ninguna de esas capas; requiere una decision de superficie de confianza separada y evidencia de extremo a extremo.

9. **Proceso y procedencia.** Trabajo solo, sin fan-out; se aplicaron criterios de gates-ledger y cierre post-session, acotados por el brief headless. No se creo worktree ni se editaron targets de otras lanes porque el brief asigna este arbol y prohibe cambios fuera de lista. Lecturas y escrituras locales realizadas con Python y relectura byte a byte/tamano; test fixtures usan directorios temporales. No se uso PowerShell para serializar JSON. Gate/manifest de esta entrega son evidencia local, no un resellado del launcher. Sin commit por prohibicion expresa y revision receptora pendiente.

## D - QUE PUEDE ESTAR MAL EN LA PREMISA DE ESTE ENCARGO

**La equivalencia entre un guardado del ecosistema y el apag?n de horas despues NO queda demostrada y el mecanismo descrito no la sostiene entre peticiones.** La politica retenida es semantica; los snapshots de archivos viven solo durante resolve_daemon_provenance. En Windows, FILE_SHARE_READ impide incluso la sustitucion concurrente de esos handles. El test real de rename entre peticiones ya pasa con el original. El ROJO de T1 con doubles demuestra la comparacion excesiva dentro de una llamada, no que un host Windows real pueda producir esa carrera en estas condiciones.

Una alternativa concreta [HIPOTESIS] es una colision con el escritor justo ANTES de abrir los handles: CreateFileW puede devolver ERROR_SHARING_VIOLATION (32), que hasta ahora se convertia al mismo daemon_provenance_conflict generico. T3 muestra el rechazo y la nueva distincion con fallo Win32 inyectado; no prueba que ese sea el fallo del incidente. Otra posibilidad es que la registracion relevante cambiase realmente, o que el runtime cargase una version distinta. Mtime/ctime solo no discrimina esas opciones. Haria falta el `policy_cause` del fallo real, hash/version del modulo cargado y observacion de la registracion acreditada en ese instante, sin registrar secretos.

Por ello esta entrega es un cambio de equivalencia semantica autorizado y una mejora de diagnostico, **NO un cierre acreditado de 49b2/c261/050e en produccion**. Mantener el fail-closed puede seguir rechazando mientras el fichero sea ilegible, falte o discrepe. No he anadido retry, bypass ni permiso de parar runs desde un cliente no acreditado para forzar el resultado esperado.

## E - LO QUE NO PUDE VERIFICAR

- Publicacion estructurada de policy_cause en la respuesta MCP: FUERA DE MI ALCANCE por ESTE brief; falta serializer server.py:1267-1300. El campo si viaja al consumidor Python de ControlClient y la remediacion textual al adaptador existente.
- Resolucion del incidente Windows historico, cambios reales del registro y versiones cargadas en sesiones afectadas: no hubo observacion autorizada del daemon vivo ni de las configs privadas. La prohibicion de tools MCP/daemon/juego de ESTE brief impide esa reproduccion.
- Una sustitucion concurrente real bajo el mismo FILE_SHARE_READ de Windows: bloqueada por el mecanismo existente; T1 usa doubles para esa ventana y handles reales entre peticiones. POSIX real no se ejecuto desde este Windows.
- Symlink real de config: test_config_reparse_points_are_rejected se omite por WinError 1314 (privilegio insuficiente). Los controles mock de reparse/camino final y el pinning Windows si se ejecutan. No se relajo el sandbox ni el gate.
- Regresion de host_config totalmente verde: dos expectativas existentes incompatibles permanecen en rojo, porque ESTE brief solo permite ANADIR tests existentes. No se maquillan como PASS.
- test_native_launcher_transaction: ocho errores reproducidos tambien con preimagenes; setup falla en autoridad de rutas. No se pudo verificar su consumidor posterior y no se cambio la autoridad fuera de alcance.
- tests.test_client_credential_rotation_e2e, tests.test_session_e2e y tests.test_client_platform_alias completos: no ejecutados porque arrancan daemons/servidores de integracion. Opcion conservadora ante la prohibicion de daemon vivo de ESTE brief. test_bug046_startup_deadlock solo se corrio en BackupTransactionAdversarialTest (15 tests; skipped=1); sus clases que crean procesos/daemons quedaron al receptor.
- Suite completa y comparacion global sin concurrencia: prohibidas por ESTE brief; las ejecutara el receptor en serie. No se atribuye un global PASS a los modulos individuales.
- Bundle nativo, launcher y smoke de juego: NO resellados, NO lanzados, NO desplegados por limites 4 y 7 de ESTE brief. host_config.py SI necesita resellado antes de despliegue coordinado. No se uso 8765 ni se consultaron/gestionaron PIDs de produccion.
- Revision independiente por Claude: pendiente del receptor indicado; sin subagentes por prohibicion expresa. DONE indica entrega escrita, no aprobacion de seguridad ni cierre del incidente.
- Memoria vault, HANDOFF compartido, pipeline_inbox/pipeline_feedback y session_status: fuera de la lista escribible o MCP prohibido por ESTE brief. Se deja este STATE y pipeline-feedback.md para transferencia; no se altera el objetivo antiguo del LIVE-STATE ni la memoria de otra lane.
