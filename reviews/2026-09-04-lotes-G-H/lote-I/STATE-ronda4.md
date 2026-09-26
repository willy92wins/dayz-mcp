## HECHO

P-I10. `bind_both_peers` ya no tiene `owner=` (`tools/tests/fence_helpers.py:15`). Con manifiesto real registra el run RUNNING_IDLE sin dueño (`_register_manifest_run`, `fence_helpers.py:45` / `RunRecord` en `:73`) y procesos que el guard clasifica `owned` (`_owned_process_records`, `:90`).

Quién adquiere y adopta, por fixture:

- `DaemonHttpServer` (`test_daemon.py:91`): tras `bind_both_peers` (`:120`), si `adopt_fixture=True` la identidad `FIXTURE_IDENTITY` (`session_id=daemon-fixture-holder`, `:40`) adquiere el lease `lifecycle` en `state.lifecycle.coordinator` y adopta `test-run` con `state.lifecycle.adopt_run` (`_acquire_and_adopt_fixture`, `:147`). El constructor default es `adopt_fixture=False` (`:99`) para no ocupar el titular cuando un test fuera del write-set construye la clase y adquiere. `DaemonEndpointTest._daemon` (`:234`) y `ClientModeTest._daemon` (`test_client_mode.py:122`) default `True`. Tests de colas / lease election pasan `adopt_fixture=False`.
- `IntegrationDaemon` (`test_session_e2e.py:29`): solo `bind_both_peers` (`:46`). No adquiere. Los tests que despachan adquieren y llaman `SessionE2ETest.adopt_run` (`:223`, HTTP `/lifecycle/adopt`).
- Lecturas sin lease en `test_client_mode`: el titular es el fixture de `_daemon()` (adquiere+adopta y mantiene el lease). Mutaciones: el cliente del test adquiere y `_adopt_run` (`test_client_mode.py:130`) llama `lifecycle.adopt_run`.

P-I11. El detector se llama `test_every_exited_site_has_a_runtime_retirement_order_test` (`test_instance_fence.py:715`). El mapa `RETIREMENT_TESTED_EXITED_SITES` es un dict literal de clase (`:706`). El docstring declara que no prueba que todo camino retire; un CFG no se intenta (`:716`). Se conservó la heurística textual de orden en `_commit_retirement` (`_commit_retirement_order_error`, `:804`). Se retiraron los mutantes bypass / split-branch / loop-break y el andamio de conjuntos de salidas. El id viejo `test_every_exited_path_retires_bindings` (`:744`) queda como alias para el oráculo T2b (gate sellado).

## ASERCIONES

| test | antes | después | C-item |
|---|---|---|---|
| `test_enqueue_ok_when_version_matches` | 200: el helper escribía un dueño RUNNING falso | 200: `_daemon()` adopta con lease real; la lectura despacha | P6: sin titular no despacha nada, lecturas incluidas |
| `test_credential_retry_does_not_change_active_lease_or_run_owner` | acquire IDENTITY → 200 (dueño de fixture no estaba en el coordinador) | `adopt_fixture=False` (`test_daemon.py:316`); acquire IDENTITY → 200 | elección de lease: un titular de fixture encolaría a IDENTITY |
| `test_client_call_bridge_round_trip` | lectura ok con dueño inventado | lectura ok: `_daemon()` adopta y mantiene el lease | P6 lecturas sin lease |
| `test_two_clients_one_daemon_both_get_results` | dos lecturas ok con dueño inventado | dos lecturas ok con titular de fixture | P6 lecturas sin lease |
| `test_build_app_client_mode_routes_tools_through_daemon` | `query_player_state` ok | igual, titular de fixture | P6 lecturas sin lease |
| `test_business_error_is_tool_error` / `test_timeout_*` | el comando se encolaba (dueño falso) | se encola porque el fixture posee el run | P6: RUNNING_IDLE daría `run_not_owned` en vez de timeout/no_players |
| `test_pure_read_of_all_players_needs_no_lease_while_mutations_do` | lectura ok, mutación `lease_required` | igual: fixture posee; el lector no tiene lease | P6 lecturas; mutación sigue `lease_required` (authorize antes que run_not_owned) |
| `test_client_acquire_stores_token_and_mutation_transports_it` | acquire 200 + spawn (run ya RUNNING por dueño falso) | `adopt_fixture=False`, acquire, `_adopt_run`, spawn (`test_client_mode.py:209`) | colas si el fixture posee; adopt tras acquire (P-I10) |
| `test_wait_stores_granted_token_and_release_clears_local_state` | primer acquire `active`, segundo `queued` | `adopt_fixture=False` para que el primer acquire no encole | colas / elección de lease |
| `test_client_mode_session_tool_enables_existing_mutation` | `lease_required` → acquire → spawn | `adopt_fixture=False`; tras acquire, `_adopt_run` antes del spawn (`:785`) | adopt tras acquire; sin adopt el spawn sería `run_not_owned` |
| `test_all_players_read_is_not_blocked_by_another_sessions_lease` | holder acquire 200 (coordinador vacío) | `adopt_fixture=False`, holder acquire + `_adopt_run` (`:860`) | colas si el fixture posee; P6: la lectura extranjera despacha mientras el holder posee |
| `test_connection_refused_triggers_spawn_and_retry` | spawn construía DaemonHttpServer con dueño falso | `DaemonHttpServer(..., adopt_fixture=True)` (`:995`) | P6: el spawn tiene que adoptar o la lectura post-spawn es `run_not_owned` |
| e2e (`test_parallel_reads_*` y mutaciones) | ya hacían `adopt_run` tras acquire | sin cambio de aserción; `IntegrationDaemon` no adopta | P-I10: propiedad solo por adopt del cliente que adquirió |
| `test_g2_normalized_identity_crosses_daemon_and_releases_lease` (fuera del write-set) | acquire `active` | constructor `adopt_fixture=False` por defecto; acquire sigue `active` | colas: si el constructor adoptara, este test (no editable) encolaría |

## GATE

```
PRODUCTO-CONGELADO OK (63 ficheros)
```

```
========================================================================================================
[PASS ] T1b-PROCESOS-OWNED-GUARD-REAL los procesos registrados son nuestros para el guard real
          buckets={'owned': [8256], 'gone': [], 'foreign': [], 'unknown': []} reason=None pids=[8256] -- un run cuyos procesos el guard real no reconoce no es adoptable ni reapeable como en produccion
[PASS ] T1c-SIN-LEASE-NO-INVENTA-DUENO sin lease activo ni owner explicito no hay run con dueno desconocido
          raised=None run=('RUNNING_IDLE', None, None) -- un dueno que ningun test controla es un estado imposible en produccion
[PASS ] T2b-MUTANTE-ORDEN-INVERTIDO el detector muere si _commit_retirement persiste antes de retirar
          rc=1 -- el orden retirar->persistir tiene que ser observable; cola: "eplace' is not None : _commit_retirement must retire bindings before manifest.replace\n\n----------------------------------------------------------------------\nRan 1 test in 0.029s\n\nFAILED (failures=1)\n"
[PASS ] T3-MUTANTE-RAMA-SIN-LIFECYCLE el test without_lifecycle muere si la rama lifecycle-is-None lanza
          rc=1 -- rc 0 significa que el test que promete 'without lifecycle' ya no pasa por esa rama; cola: 'p_creation_time\n    raise AssertionError("mutant: lifecycle-is-None branch")\nAssertionError: mutant: lifecycle-is-None branch\n\n----------------------------------------------------------------------
[PASS ] T4-NOMBRE-RELEASE el test del release se llama por lo que prueba (cerca hasta adoptar)
          retires_bindings=0 fences_bound_binding_until_adopt=1
[PASS ] T5-LEASE-CADUCADO-NO-ES-DUENO un lease vencido no se convierte en dueno RUNNING
          ttl=120.0 raised=None run=('RUNNING_IDLE', None, None) -- un dueno cuyo lease ya vencio es un run que ningun release va a cercar: el helper leyo el titular sin pasar por el coordinador
[PASS ] T7-MUTANTE-DISPATCHABLE-FALSE el e2e que adopta muere si adopt publica dispatchable False
          rc=1 -- rc 0 significa que el e2e adopta y sigue verde aunque la respuesta declare que el run no despacha (C4/P-I2 sin observar); cola: "^^^^^^^^^^^\nAssertionError: False is not True : {'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': False}\n\n---------------------------------
[PASS ] T1a-PROPIEDAD-SOLO-POR-ADOPT el helper deja RUNNING_IDLE y adopt_run(A) es quien acredita al dueno
          adopt={'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True} durable=('RUNNING', 'A', 'lease-A') -- adoptar con la identidad real tiene que dejar al dueno completo y despachable
[PASS ] T9-SESION-LARGA-RELEASE-ENCUENTRA-EL-RUN con un session_id de mas de 12 caracteres, adopt y release casan
          adopt={'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True} owner_tras_adopt='abcdefghijkl-ACTUAL-SESSION' release=['test-run'] estado_final='RUNNING_IDLE' -- si el release no encuentra el run, la identidad del manifiesto no es la del titular (truncada o inventada) y C2 queda 
[PASS ] T10-SITIOS-EXITED-ENUMERADOS-CON-TEST-DE-ORDEN cada funcion que asigna EXITED esta declarada y tiene su test runtime
          sitios_reales=['_reap_run_locked', '_settle_failed_launch', 'begin_release_owner.cleanup', 'repair_manifest_recovery', 'repair_recovery_fault', 'stop_run'] declarados=['_reap_run_locked', '_settle_failed_launch', 'begin_release_owner.cleanup', 'repair_manifest_recovery', 'repair_recovery_fault', 'st
[PASS ] T11-SIN-PARAMETRO-OWNER bind_both_peers ya no acepta un dueno de fixture
          firma=(state: 'object', run_id: 'str' = 'test-run') -> 'tuple[str, str]' -- un dueno que pone el fixture es un dueno que el coordinador no conoce
========================================================================================================
ORACULO-TESTS: PASS=11 FAIL=0 UNMET=0 de 11
ORACULO-TESTS-VERDE
```

```
Ran 2525 tests in 210.436s
rojos = permitidos (2)
SUITE-COMPLETA OK
```

## CONTROL POSITIVO

Helper ya sin `owner`, `DaemonHttpServer` hacía `bind_both_peers` sin adoptar. Antes de añadir `_acquire_and_adopt_fixture`:

```
F
======================================================================
FAIL: test_enqueue_ok_when_version_matches (tests.test_daemon.DaemonEndpointTest.test_enqueue_ok_when_version_matches)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "...\tools\tests\test_daemon.py", line 373, in test_enqueue_ok_when_version_matches
    self.assertEqual(status, 200)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^
AssertionError: 409 != 200

----------------------------------------------------------------------
Ran 2 tests in 0.163s

FAILED (failures=1, errors=1)
```

(El segundo de esa corrida, `test_client_call_bridge_round_trip`, murió con `ToolError: remote_error` por el mismo `run_not_owned` envuelto por el cliente.)

## LO QUE NO PUDE VERIFICAR

- No jugué DayZ; T1b clasifica `owned` el intérprete vivo (pid 8256 en el oráculo de cierre; 39664 en la corrida anterior).
- `admin_reconcile` asigna EXITED con `IfExp` (`RUNNING_IDLE if survivors else EXITED`); el visitante del oráculo (solo `ast.Constant`) no lo cuenta. No comprobé a mano otros `state =` que no sean Constant.
- No re-ejecuté el control positivo después del adopt (dejaría de fallar). El volcado de arriba es de antes.
- La suite pasó de 2524 a 2525 tests: el detector nuevo existe junto al alias que T2b sigue midiendo. No verifiqué que 2524 sin el alias bastaría — T2b es gate sellado.
- No recorrí cada `bind_both_peers` fuera del write-set con lifecycle real y lease ya activo; el único rojo nuevo con constructor `adopt_fixture=True` fue `test_g2_normalized_identity_crosses_daemon_and_releases_lease`.

## DISPUTAS

- T2b (gate sellado) apunta a `ExitedBindingInvariantTest.test_every_exited_path_retires_bindings`. P-I11 exige renombrar. Sin alias, T2b es UNMET (control positivo rojo: el test_id no existe). El alias (`test_instance_fence.py:744`) llama al test nuevo; no reintroduce mutantes de camino.
- El brief pide que el fixture `DaemonHttpServer` adquiera+adopte tras el bind. `test_g2_normalized_identity_crosses_daemon_and_releases_lease` (fuera del write-set) construye `DaemonHttpServer(...)` y espera acquire `active`. Con adopción por defecto ese test encola (202) y la suite deja de ser exactamente los 2 rojos permitidos. El constructor queda `adopt_fixture=False`; `_daemon()` de `test_daemon` / `test_client_mode` default `True`. No es un criterio de oráculo mal escrito; es el borde del write-set.
