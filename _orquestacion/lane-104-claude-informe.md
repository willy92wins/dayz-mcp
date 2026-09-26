# Lane 104 (Claude): triaje ficha a ficha de la deriva de contrato (familia D)

Rama: `feature/claude-104-lane-claude` (worktree). Estado: **COMPLETO**. 10 de los 12 fallos
en verde. Quedan en rojo 2 fichas: 1 `regresion-real` (escalar) y 1 residuo `duplicado` de #102.
No se ha tocado ningun fichero de produccion: todos los cambios estan en `tools/tests/`.

## Nota de entorno (importante para reproducir)

En el worktree, `discover -s tools/tests` sin mas **no importa** `tests.*`
(`ModuleNotFoundError: No module named 'tests'`), y el paquete editable del venv
(`__editable__.dayz_mcp_tools-0.0.0.pth`) apunta `dayz_mcp` al checkout principal
`DayZ_MCP_dev`, no al worktree. Todas las corridas de esta lane usan
`PYTHONPATH=tools` (o `PYTHONPATH=.` con cwd `tools/` y `-s tests`), comprobado con
`dayz_mcp.__file__` -> `...\lane-104\tools\dayz_mcp\__init__.py`. Es la misma
invocacion del brief (venv con ruta absoluta y `-m unittest discover`) con ese unico anadido.

## Tabla de triaje

| # | Fallo | Causa (PR/commit culpable) | Clasificacion | Accion |
|---|---|---|---|---|
| 1-3 | `test_fn_f1f5` BridgeSuccessHintTest x3 (`suggested_calls` None) | No es la regla "max 2" de `e72ff5b`: `call_bridge` devolvia el sobre `not_ready` con `reason=capabilities_unknown`. #94 (`9af5cbf`, 0878) exige el censo de caps en `compute_bridge_ready`, y el `_ready_snapshot()` del test no traia `capabilities` | test-viejo (mismo gate que #102, pero con una fixture dict propia que #102 no cubre) | `_ready_snapshot()` anade `server_peer.capabilities={"state":"match","reason":"ok"}` |
| 4 | `test_fn_f1f5` `test_local8b_census_matches...` (`announced != match`) | #94 (`9af5cbf`) movio `_with_capability_comparison` del tool `bridge_status` a `runtime.bridge_status_payload(registered_tools=..., intended_tools=...)`. El test mockeaba `bridge_status_payload` entero y se saltaba la comparacion. Ademas, el censo sin `ach` ahora da `mismatch` | test-viejo | Mockear `runtime.status` (la entrada cruda) en lugar de `bridge_status_payload`, y anadir `announced_arg_contract_hash=EXPECTED_SERVER_ARG_CONTRACT_HASH` al peer server |
| 5 | `test_ui_dialog` `test_enqueue_stale_lease_clears_token` | `151f5f2` (P0 small-model loops, 2026-09-17): `lease_expired` pasa a `LEASE_EXPIRED_RECIPE` = `lease_expired: call session_acquire_wait(purpose=...); next_step=session_acquire_wait`. Cambio intencional (receta 8B) | test-viejo (anterior a #94/#97/#98) | Afirmar `== server.LEASE_EXPIRED_RECIPE` y `startswith("lease_expired")` |
| 6-8 | `test_ui_error_diagnostics` x3 (`AttributeError: status_snapshot`) | `e72ff5b` (fail-fast world-reads): `Runtime.call_bridge` llama a `self.status()` -> `state.status_snapshot()` antes de encolar. **El atributo NO ha desaparecido de produccion**: `ServerState.status_snapshot` sigue en `loopback.py:2901` y `server.py:1506`. Lo que falta es el metodo en el `SimpleNamespace` falso del test | test-viejo (no es regresion: descartado con evidencia) | `_fake_state` anade `status_snapshot=loopback.ServerState("k").status_snapshot` (forma real) |
| 9-10 | `test_vehicle_prepare_fixture` `FixtureNotReadyWireTest` x2 (mismo `status_snapshot`) | Misma causa: importa `_wire_error_text` de `test_ui_error_diagnostics` | test-viejo | Arreglado por la ficha 6-8 (sin tocar este fichero) |
| 11 | `test_python_backlog_fixes` `test_bug024_timeout_reaps_state...` (ToolError no lanzado) | `e72ff5b`: `query_player_state` es world-read. Sin peer que haya hecho poll, devuelve `not_ready` antes de encolar (intencional), asi que el camino de timeout/reap que prueba el test ya no se alcanzaba | test-viejo | `patch.object(server, "_world_read_not_ready", return_value=None)` solo alrededor de la llamada. El test sigue probando enqueue -> timeout -> reap |
| 12 | `test_python_backlog_fixes` `test_bug037_tool_timeout_caps_daemon_startup_poll` (tool_timeout=0.4) | `e72ff5b`: `ClientRuntime.call_bridge` (`server.py:2284-2292`) calcula `deadline` pero lanza el probe previo `bridge_status_payload(timeout_s=LIVENESS_STATUS_TIMEOUT_S)` = **1.0 s** sin acotarlo por el deadline. Una tool con timeout 0.4 s y daemon caido consume 1.0 s (el reloj falso del test mide 1.0 > 0.4). Rompe el contrato BUG-037 | **regresion-real** (P3: solo alarga timeouts < 1 s) | **NO arreglado.** Fix sugerido para el issue propio: `timeout_s=min(LIVENESS_STATUS_TIMEOUT_S, max(0.0, deadline - self._time_fn()))` y saltar el probe si no queda presupuesto |
| 13 | `test_w3_bug_verdicts` Bug111 (`NoneType` sin `__dict__`) | `e62da64` (2026-09-19) anadio el primer `@dataclass(frozen=True, slots=True)` a `mcp_capture.py`. El loader del test (`e7e23cd`) hacia `exec_module` sin registrar el modulo en `sys.modules`, y dataclasses lo necesita | test-viejo (defecto del loader del test) | Registrar en `sys.modules` antes de `exec_module` y quitarlo despues (patron de la doc de importlib) |
| 14 | `test_task9_launcher_migration` `test_registry_contains_native_launcher...` (`'.ps1'` en docs) | `cfd73cb` (2026-09-19, stdio plan B) nombra el instalador `install-mcp.ps1` en `README-mcp.md:192`. El test prohibe cualquier `.ps1` para que la doc no exponga un *launcher host* legacy. Un instalador de cliente no es eso | test-viejo | Neutralizar solo el token exacto `install-mcp.ps1` antes del chequeo. El resto de prohibiciones (`.ps1`, `powershell`, `pwsh`, `cmd.exe`, `remotesigned`) sigue igual |
| 15 | `test_a429_overlay` `test_p3_n3_ready_true_with_historical_counters` | Capa 1: #94 (`9af5cbf`) llama `bridge_status_payload(registered_tools=..., intended_tools=...)` y el wrapper del test no aceptaba kwargs (TypeError -> ToolError). Capa 2, tras arreglarla: `ready=False reason=capabilities_unknown`, porque el `FakePeer` de `tests.test_mcp_tools` hace poll sin `caps=`/`ach=` | Capa 1: test-viejo (arreglada). Capa 2: **duplicado #102** | Wrapper `with_rejects(**kwargs)`. El `FakePeer` NO se toca (es de #102). Sigue rojo hasta que #102 fusione |

## Bloque A - Archivos creados/modificados

- `tools/tests/test_fn_f1f5.py`: +13/-5 (fixtures `_announced_snapshot`/`_ready_snapshot`, mock de `status`)
- `tools/tests/test_ui_dialog.py`: +3/-1 (aserto `LEASE_EXPIRED_RECIPE`)
- `tools/tests/test_ui_error_diagnostics.py`: +4/-1 (`_fake_state.status_snapshot`, import `loopback`)
- `tools/tests/test_python_backlog_fixes.py`: +12/-7 (bypass acotado del gate world-read en bug024, import `patch`)
- `tools/tests/test_w3_bug_verdicts.py`: +7/-1 (loader registra en `sys.modules`)
- `tools/tests/test_task9_launcher_migration.py`: +7/-1 (excepcion exacta `install-mcp.ps1`)
- `tools/tests/test_a429_overlay.py`: +5/-3 (wrapper con `**kwargs`, mensaje de diagnostico en el aserto `ready`)
- `_orquestacion/lane-104-claude-informe.md`: nuevo (este informe)

Total en tests: 7 ficheros, 51 inserciones y 19 borrados. Produccion: 0 ficheros.

## Bloque B - Resultado de los tests / verificacion

Modulos de las fichas, tras el fix (venv absoluto, `PYTHONPATH=tools`):

```
test_fn_f1f5                  Ran 16 tests in 1.029s   OK
test_ui_dialog                Ran 42 tests in 0.361s   OK
test_ui_error_diagnostics     Ran 10 tests in 0.617s   OK
test_vehicle_prepare_fixture  Ran 15 tests in 0.503s   OK
test_python_backlog_fixes     Ran 9 tests in 1.108s    FAILED (failures=1)   <- bug037, regresion-real
test_w3_bug_verdicts          Ran 4 tests in 0.027s    OK
test_task9_launcher_migration Ran 5 tests in 0.254s    OK (skipped=3)       <- ver nota
test_a429_overlay             Ran 11 tests in 1.495s   FAILED (failures=1)   <- duplicado #102
```

Nota task9: en el worktree el test afectado se SALTA (`requires_installed_launcher`: el PE
y `approved-launchers.json` son artefactos de build no versionados). La logica del fix se
verifico aparte contra `README-mcp.md`: los tokens prohibidos presentes pasan de `['.ps1']`
a `[]`, y las dos cadenas obligatorias siguen presentes. Hay que confirmarlo en el checkout
principal, que si tiene el launcher instalado.

Suite COMPLETA:

- ANTES (baseline del orquestador, checkout principal): 4174 tests -> **56 failures, 5 errors, 10 skipped**.
- DESPUES (este worktree): `Ran 4173 tests in 335.623s` -> `FAILED (failures=45, errors=3, skipped=58)`.

Los conteos no son comparables 1:1. El worktree salta 48 tests mas, porque le faltan
artefactos de build (launcher, closure manifest), y descubre 1 test menos. De los
45F/3E que quedan, solo 2 son de esta lane (bug037 y a429). El resto es ajeno:

- #102 (caps/ach, `capabilities_unknown`): test_client_mode (8), test_daemon_query_all_players (3),
  test_mcp_tools (10), test_session_e2e (3), test_telemetry_read_modes (3 subtests),
  test_weak_agent_consumer_ux (2), test_playbook_runner (1), test_fn_p0_small_model_loops (1).
- #103 (disclosure / descripciones de 80 caracteres): test_pleno_lease_and_orphans (9 subtests),
  test_session_status_blocked_on (2), test_wait_for_marker (1), y
  test_mcp_tools `dayz_test_run_description...`. Los 3 ERROR (`KeyError: 'playbook_reload'`,
  `KeyError: 'session_cancel'`, y el esquema de list_tools en telemetry_read_modes) son
  herramientas que faltan en el catalogo recortado, familia #103.

No hice una corrida "antes" propia en el worktree para no gastar otros 6 min del techo.
Las 12 fichas si se reprodujeron una a una antes de cada fix.

## Bloque C - Hallazgos durante implementacion

1. test_fn_f1f5 x3 (suggested_calls): **test-viejo** (#94 `9af5cbf`). Ojo: la causa real
   NO es la regla "max 2" de `e72ff5b` que sugeria el issue, sino el gate `capabilities_unknown`
   de #94 sobre una fixture dict propia del modulo. La REGLA DE DUPLICADOS de #102 se aplica
   a GamePeer/FakePeer HTTP. Esta fixture no esta en el alcance de #102, asi que se arreglo
   aqui (decision conservadora: sin ello el criterio de aceptacion de #104 no se cumple).
2. test_fn_f1f5 census: **test-viejo** (#94 `9af5cbf`, comparacion movida a `bridge_status_payload`).
3. test_ui_dialog lease_expired: **test-viejo** (`151f5f2`).
4. test_ui_error_diagnostics x3 + test_vehicle_prepare_fixture x2 (`status_snapshot`):
   **test-viejo** (`e72ff5b`). Se descarta la sospecha de regresion del issue: produccion
   conserva `ServerState.status_snapshot`. Solo el doble del test estaba incompleto.
5. test_python_backlog_fixes bug024: **test-viejo** (`e72ff5b`).
6. test_python_backlog_fixes bug037: **regresion-real**, no arreglada. Evidencia:
   `server.py:2285` calcula `deadline = self._time_fn() + timeout_s`, pero `server.py:2288`
   llama a `bridge_status_payload(timeout_s=LIVENESS_STATUS_TIMEOUT_S)` (`server.py:119` = 1.0)
   sin acotarlo. Con timeout 0.4 s el reloj falso avanza 1.0 s. Introducida en `e72ff5b`
   (fix(mcp): fail-fast world-reads and local8b pack). Propuesta: issue propio P3.
7. test_w3_bug_verdicts Bug111: **test-viejo** (`e62da64` destapo un loader incorrecto de `e7e23cd`).
8. test_task9_launcher_migration `.ps1`: **test-viejo** (`cfd73cb`). Alternativa descartada:
   quitar `install-mcp.ps1` de `README-mcp.md` es un cambio fuera de `tools/tests/`.
9. test_a429_overlay: capa 1 **test-viejo** (#94 `9af5cbf`, arreglada). Capa 2 **duplicado #102**
   (`FakePeer` de test_mcp_tools sin caps/ach). No tocada.
10. Entorno: el `.pth` editable del venv enlaza `dayz_mcp` al checkout principal. Si una
    corrida en el worktree no fija `PYTHONPATH`, prueba produccion de otra ruta. Documentarlo
    para las lanes paralelas.

## Bloque D - Handoff para la revision (Sol)

- Estado al cierre: 1 commit en `feature/claude-104-lane-claude` con 7 ficheros de test y
  este informe, pusheado. Sin PR, como pide el brief.
- Que revisar:
  - `test_python_backlog_fixes` bug024: el bypass de `_world_read_not_ready` con `patch.object`
    es deliberado y acotado. Alternativa posible: usar un comando no world-read, pero cambia
    el contrato que fija el test.
  - `test_task9` solo se pudo verificar la logica del chequeo: hay que correrlo en
    `DayZ_MCP_dev` con el launcher instalado.
  - `test_fn_f1f5`: la decision de arreglarlo aqui y no como duplicado de #102 (Bloque C-1).
- Deuda conocida:
  - Abrir issue propio para bug037 (regresion-real, `e72ff5b`, probe de 1.0 s sin acotar).
  - `test_a429_overlay` depende de que #102 anada `caps=`/`ach=` a `FakePeer` de test_mcp_tools.
  - Criterio de aceptacion de #104 ("12 en VERDE"): cumplido 10/12. Las 2 restantes se
    escalan (bug037) o se resuelven en #102 (a429).
