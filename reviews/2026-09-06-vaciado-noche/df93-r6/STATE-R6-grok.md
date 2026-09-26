# STATE.md — ronda 6 lote df93

Cambio de contrato: la puerta verifica lo pedido. Un arranque de servidor sin candidato VPP avisa (`vpp_mod_not_requested` en `warnings`, `hint=VPP_ABSENT_HINT`) y no lee el host. Si hay candidato VPP, el resto de la rama queda igual.

Write-set: `tools/dayz_mcp/native_launcher_transaction.py`, `tools/tests/test_vpp_preflight.py`. Finales de linea LF conservados. Sin commits.

Recuento: partida 58 tests OK; llegada 64 tests OK (58 de partida, renombrados donde toca, mas 6 nuevos). T6 no se duplico.

## Que cambie

### `tools/dayz_mcp/native_launcher_transaction.py`

- 112-118: comentario de cabecera del bloque VPP (verifica lo pedido, avisa si no se pidio, no escribe).
- 138-143: `VPP_PREFLIGHT_HINT` arranca con "the requested admin tools are not usable:"; el resto de la frase igual.
- 144-149: constante publica nueva `VPP_ABSENT_HINT` (texto exacto del brief; contiene `@VPPAdminTools`; no contiene "refuse").
- 445-451: docstring de `evaluate_vpp_preflight`.
- 464-470: rama (b) — si `mode_starts_server` y `requested` vacio, retorno inmediato `VppPreflightResult(error_code=None, missing=(), warnings=("vpp_mod_not_requested",), hint=VPP_ABSENT_HINT)` sin leer cfg/Permissions.
- 471-516: rama (c) intacta (identidad, carpeta, raiz ambigua, server_config*, `vpp_disable_password` en missing; `vpp_superadmins_absent` / `vpp_credentials_absent` en warnings; `hint=VPP_PREFLIGHT_HINT`).
- 576-581: docstring de `enforce_vpp_preflight`. El codigo (583-586) no cambia: `ValueError(result.error_code)` solo si hay `error_code`.
- 609-614: comentario del call-site en `execute_native_launcher_transaction`.

Firmas publicas, tokens y helpers listados en el brief: sin cambios de bytes en sus cuerpos.

### `tools/tests/test_vpp_preflight.py`

- T1 153: `test_server_mode_without_the_admin_tools_is_warned_not_refused` (antes `..._is_refused`).
- T2 162: `test_server_only_mode_is_warned_the_same_way`.
- T3 197: mismo nombre; `error_code is None` y `warnings == ("vpp_mod_not_requested",)`.
- T4 409: el caso que sigue fallando pide VPP y cfg sin clave (`vpp_disable_password` in missing, `preflight=True`).
- T4-bis 428: `test_a_preflight_request_without_admin_tools_is_warned_like_a_launch`.
- T5 438: `test_a_run_that_requests_no_admin_tools_reads_nothing` (FakeFiles ya cuenta en `.reads`; cero lecturas).
- T6: no duplicado. Ya existe `VppPreflightDecisionTest.test_a_foreign_directory_with_the_right_name_is_refused` (255): VPP pedido (carpeta y Workshop id) + `META_OTHER` -> `vpp_mod_identity`. Tambien `VppPreflightRound4Test.test_meta_cpp_forms_that_do_not_prove_the_identity`.
- T7 751: `test_enforce_raises_the_declared_token` pide VPP con `FakeFiles()` vacio -> `ValueError` == `VPP_PREFLIGHT_FAILED`.
- T7-bis 766: `test_enforce_returns_a_warning_result_when_no_admin_tools_are_requested`.
- T8 791: `test_the_refusal_writes_nothing_to_the_workspace` pide VPP sobre temp vacio.
- T8-bis 814: `test_the_warning_writes_nothing_to_the_workspace`.
- T9 881: `test_a_server_request_with_unusable_admin_tools_never_takes_a_lease` (`extra_mods` con `@VPPAdminTools`; `acquire_calls == 0`, `consumer_calls == []`).
- T9-bis 893: `test_a_server_request_without_admin_tools_gets_past_the_gate`.
- T10 1118: `test_a_server_run_with_unusable_admin_tools_never_reaches_the_launcher`; stub `_refused` (1261) `missing=("vpp_disable_password",)`; aserto `vpp_missing == ["vpp_disable_password"]`.
- T10-bis 1204: `test_the_absent_admin_tools_warning_travels_with_a_successful_run`.

## FRASES REESCRITAS

### `tools/dayz_mcp/native_launcher_transaction.py:112-118`

viejo -> `The replacement refuses instead of repairing: it reads, it never writes.`

nuevo -> `The replacement verifies what was requested, warns when nothing was asked for, and still never writes.`

### `tools/dayz_mcp/native_launcher_transaction.py:138-143` (`VPP_PREFLIGHT_HINT`)

viejo -> `this mode starts a server and the admin tools are not usable:`

nuevo -> `the requested admin tools are not usable:`

(resto de la frase igual)

### `tools/dayz_mcp/native_launcher_transaction.py:445-451` (docstring `evaluate_vpp_preflight`)

viejo -> `Deterministic and cheap findings block (missing); what the ps1 only seeded best-effort warns (warnings), because this route cannot seed it and a server with no superadmin still boots.`

nuevo -> `A run that names no admin tools may start: it is warned with vpp_mod_not_requested and nothing is read. Deterministic and cheap findings about the requested tool block (missing); what the ps1 only seeded best-effort warns (warnings), because this route cannot seed it and a server with no superadmin still boots.`

### `tools/dayz_mcp/native_launcher_transaction.py:576-581` (docstring `enforce_vpp_preflight`)

viejo ->
```
Refuse a server start without usable admin tools. Fail closed.

There is no bypass parameter on this route: wiring one would have to travel
through secure_launcher.py, whose bytes are pinned by the sealed build
contract. The refusal names what is missing so the caller can fix it.
```

nuevo ->
```
Refuse a server start whose requested admin tools are not usable.

Fail closed on what was asked. A request that names no admin tool is
warned and never refused: this launcher does not require a particular
one. There is no bypass parameter on this route, and none is needed:
not requesting the tool is the legitimate way to start without it.
```

### `tools/dayz_mcp/native_launcher_transaction.py:609-614` (call-site)

viejo -> `so a server that would start without admin tools is refused on either.`

nuevo -> `so a server whose requested admin tools are unusable is refused on either; one that requests none is warned.`

Grep `refus|reject` en el modulo: no se tocaron las frases de identidad, cfg, tope de lectura ni modos desconocidos (`HostVppFiles` 181, `mode_starts_server` 221-223, `_live_assignments` 322, `_vpp_password_is_disabled` 373).

## Salida literal — rojo primero

Tests ya escritos sobre el producto anterior (sin `VPP_ABSENT_HINT`). Interprete: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe`. Desde `tools` con `PYTHONPATH=.`.

```
.E......F.........F.F............F....FF.......E...E............
======================================================================
ERROR: test_the_absent_admin_tools_warning_travels_with_a_successful_run (tests.test_vpp_preflight.DayzTestRunVppGateTest.test_the_absent_admin_tools_warning_travels_with_a_successful_run)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Python314\Lib\asyncio\runners.py", line 127, in run
    return self._loop.run_until_complete(task)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^
  File "C:\Python314\Lib\asyncio\base_events.py", line 719, in run_until_complete
    return future.result()
           ~~~~~~~~~~~~~^^
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6\ws\tools\tests\test_vpp_preflight.py", line 1249, in test_the_absent_admin_tools_warning_travels_with_a_successful_run
    result = await dayz_test_tool.execute_dayz_test_run(
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    ...<4 lines>...
    )
    ^
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6\ws\tools\dayz_mcp\dayz_test_tool.py", line 1256, in execute_dayz_test_run
    vpp = preflight_vpp_request(
        raw_request, sealed_policies=bundle.sealed_policies
    )
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6\ws\tools\tests\test_vpp_preflight.py", line 1231, in warned
    hint=transaction.VPP_ABSENT_HINT,
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^
AttributeError: module 'dayz_mcp.native_launcher_transaction' has no attribute 'VPP_ABSENT_HINT'

======================================================================
ERROR: test_enforce_returns_a_warning_result_when_no_admin_tools_are_requested (tests.test_vpp_preflight.VppPreflightEnforcementTest.test_enforce_returns_a_warning_result_when_no_admin_tools_are_requested)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6\ws\tools\tests\test_vpp_preflight.py", line 770, in test_enforce_returns_a_warning_result_when_no_admin_tools_are_requested
    result = transaction.enforce_vpp_preflight(
        self._parsed(policy, mode="all", extra_mods=["@DayZ_MCP"]),
        (policy,),
        files=_healthy(policy),
    )
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6\ws\tools\dayz_mcp\native_launcher_transaction.py", line 571, in enforce_vpp_preflight
    raise ValueError(result.error_code)
ValueError: vpp_preflight_failed

======================================================================
ERROR: test_the_warning_writes_nothing_to_the_workspace (tests.test_vpp_preflight.VppPreflightEnforcementTest.test_the_warning_writes_nothing_to_the_workspace)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6\ws\tools\tests\test_vpp_preflight.py", line 826, in test_the_warning_writes_nothing_to_the_workspace
    transaction.enforce_vpp_preflight(
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^
        self._parsed(policy, mode="all", extra_mods=["@DayZ_MCP"]),
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        (policy,),
        ^^^^^^^^^^
    )
    ^
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6\ws\tools\dayz_mcp\native_launcher_transaction.py", line 571, in enforce_vpp_preflight
    raise ValueError(result.error_code)
ValueError: vpp_preflight_failed

======================================================================
FAIL: test_a_server_request_without_admin_tools_gets_past_the_gate (tests.test_vpp_preflight.VppPreflightChokepointTest.test_a_server_request_without_admin_tools_gets_past_the_gate)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Python314\Lib\asyncio\runners.py", line 127, in run
    return self._loop.run_until_complete(task)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^
  File "C:\Python314\Lib\asyncio\base_events.py", line 719, in run_until_complete
    return future.result()
           ~~~~~~~~~~~~~^^
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6\ws\tools\tests\test_vpp_preflight.py", line 900, in test_a_server_request_without_admin_tools_gets_past_the_gate
    self.assertNotEqual(str(raised), transaction.VPP_PREFLIGHT_FAILED)
    ~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 'vpp_preflight_failed' == 'vpp_preflight_failed'

======================================================================
FAIL: test_a_preflight_request_without_admin_tools_is_warned_like_a_launch (tests.test_vpp_preflight.VppPreflightDecisionTest.test_a_preflight_request_without_admin_tools_is_warned_like_a_launch)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6\ws\tools\tests\test_vpp_preflight.py", line 435, in test_a_preflight_request_without_admin_tools_is_warned_like_a_launch
    self.assertIsNone(result.error_code)
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^
AssertionError: 'vpp_preflight_failed' is not None

======================================================================
FAIL: test_a_run_that_requests_no_admin_tools_reads_nothing (tests.test_vpp_preflight.VppPreflightDecisionTest.test_a_run_that_requests_no_admin_tools_reads_nothing)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6\ws\tools\tests\test_vpp_preflight.py", line 444, in test_a_run_that_requests_no_admin_tools_reads_nothing
    self.assertIsNone(result.error_code)
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^
AssertionError: 'vpp_preflight_failed' is not None

======================================================================
FAIL: test_no_base_mods_drops_a_default_that_only_lived_in_the_policy (tests.test_vpp_preflight.VppPreflightDecisionTest.test_no_base_mods_drops_a_default_that_only_lived_in_the_policy)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6\ws\tools\tests\test_vpp_preflight.py", line 208, in test_no_base_mods_drops_a_default_that_only_lived_in_the_policy
    self.assertIsNone(result.error_code)
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^
AssertionError: 'vpp_preflight_failed' is not None

======================================================================
FAIL: test_server_mode_without_the_admin_tools_is_warned_not_refused (tests.test_vpp_preflight.VppPreflightDecisionTest.test_server_mode_without_the_admin_tools_is_warned_not_refused)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6\ws\tools\tests\test_vpp_preflight.py", line 156, in test_server_mode_without_the_admin_tools_is_warned_not_refused
    self.assertIsNone(result.error_code)
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^
AssertionError: 'vpp_preflight_failed' is not None

======================================================================
FAIL: test_server_only_mode_is_warned_the_same_way (tests.test_vpp_preflight.VppPreflightDecisionTest.test_server_only_mode_is_warned_the_same_way)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6\ws\tools\tests\test_vpp_preflight.py", line 165, in test_server_only_mode_is_warned_the_same_way
    self.assertIsNone(result.error_code)
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^
AssertionError: 'vpp_preflight_failed' is not None

----------------------------------------------------------------------
Ran 64 tests in 2.044s

FAILED (failures=6, errors=3)
```

Rojos pedidos: T1, T2, T3, T4-bis, T5, T7-bis, T8-bis, T9-bis, T10-bis. T10-bis cayo por `AttributeError: ... has no attribute 'VPP_ABSENT_HINT'` (cuenta como rojo).

Partida antes de tocar tests:

```
..........................................................
----------------------------------------------------------------------
Ran 58 tests in 2.270s

OK
```

## Salida literal — verde final

```
................................................................
----------------------------------------------------------------------
Ran 64 tests in 2.147s

OK
```

(primera verde post-implementacion: `Ran 64 tests in 2.240s` OK; la de 2.147s es tras restaurar los mutantes.)

## Salida literal — regresion

`tests.test_native_launcher_transaction tests.test_dayz_test_tool tests.test_dayz_test_tool_modes tests.test_lifecycle_reconcile`

```
.................................................................................................................................
----------------------------------------------------------------------
Ran 129 tests in 1.285s

OK
```

Ningun metodo con "acceso denegado" en esta corrida.

## Mutantes

Fichero mutado: `tools/dayz_mcp/native_launcher_transaction.py`. Restaurado byte a byte despues de cada uno.

sha256 bueno (antes, despues de cada restore, y final):
`3ca12ca20597a09de932cb147df7d509ffc8c49125923758905b70a79fe93c05`

### (a) `vpp_mod_not_requested` otra vez en `missing`

sha256 aplicado: `a17b89e5144542de98e5b177cb7cc17d7102cfcf2c87c3d5c77dfc3a29ac3064`

```
Ran 64 tests in 2.460s
FAILED (failures=6)
```

Rojos: `test_a_preflight_request_without_admin_tools_is_warned_like_a_launch`, `test_a_run_that_requests_no_admin_tools_reads_nothing`, `test_no_base_mods_drops_a_default_that_only_lived_in_the_policy`, `test_server_mode_without_the_admin_tools_is_warned_not_refused`, `test_server_only_mode_is_warned_the_same_way`, `test_enforce_returns_a_warning_result_when_no_admin_tools_are_requested`.

### (b) rama (b) lee el cfg igual (`_read_or_absent(host, paths.server_config)` antes del aviso)

sha256 aplicado: `45755465d38e761c8749865095a8ebc08cc2209f5e0a09278d34f15b2855693d`

```
Ran 64 tests in 2.470s
FAILED (failures=1)
```

Rojo: `test_a_run_that_requests_no_admin_tools_reads_nothing` — `['P:\\Suite\\_server\\serverDZ.cfg'] != []`.

### (c) rama (b) devuelve `VPP_PREFLIGHT_HINT` en vez de `VPP_ABSENT_HINT`

sha256 aplicado: `3b2a38ddf49fc2f94df28832e0fb2d2c81468e5ee4078eccabf42b5f1cfbd88d`

```
Ran 64 tests in 2.524s
FAILED (failures=2)
```

Rojos: `test_server_mode_without_the_admin_tools_is_warned_not_refused` (T1, como pide el brief) y `test_server_only_mode_is_warned_the_same_way`.

## sha256 final del write-set

- `tools/dayz_mcp/native_launcher_transaction.py`: `3ca12ca20597a09de932cb147df7d509ffc8c49125923758905b70a79fe93c05`
- `tools/tests/test_vpp_preflight.py`: `a3fe333dc0b7ae93b94accf3bef2a7b498876eb340b5f8097c315dd58565a2b5`

## LO QUE NO PUDE VERIFICAR

- No ejecute la suite completa (el brief lo prohibe: daemons / sesiones vivas).
- No lance juego, daemon ni launcher.
- No reexecute in-game el arranque sin `@VPPAdminTools` (solo el preflight unitario y la regresion de 129).
- No vi los 7 metodos con "acceso denegado" de un revisor anterior; aqui la regresion salio 129 OK.
