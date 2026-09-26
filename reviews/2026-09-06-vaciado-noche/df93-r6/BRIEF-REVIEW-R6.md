# Revisión cruzada ciega — ronda 6 del lote df93 (alcance agnóstico)

Eres el revisor de OTRA familia (Anthropic) de una entrega implementada por Grok vía Cursor
(`run1/MODEL`). No conoces al implementador ni su razonamiento: solo su entrega y el contrato.
MODO NO INTERACTIVO: nadie contesta; no preguntes; mide y dictamina.

Escribe tu dictamen en `<rev>/REVIEW-ANTHROPIC-R6.md`. Primera línea exacta:
`DICTAMEN: SEGURO INTEGRAR` o `DICTAMEN: NO ES SEGURO INTEGRAR`, y el motivo en una frase.
Cada hallazgo lleva severidad (CRITICA/ALTA/MEDIA/BAJA), `path:line`, repro ejecutado (comando y
salida literal) y qué debería pasar. Termina con la sección `LO QUE NO PUDE VERIFICAR`.

## Rutas

- `<lote>` = `C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6`
- `<ws>` = `<lote>\ws` — la entrega (copia fiel del worktree + los cambios del implementador). NO la modifiques.
- `<wt>` = `C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\e89adcdf-b230-4060-83b7-f733911e169a\scratchpad\wt-df93` — worktree git de la rama `work/df93-vpp-preflight` en `a7ddbac`, el PUNTO DE PARTIDA. SOLO LECTURA: ni `git checkout`, ni `git stash`, ni escribir nada ahí. Para el baseline usa `git -C <wt> show HEAD:<ruta>`.
- `<rev>` = `<lote>\review-r6` — tus scripts, salidas y el dictamen. Todo lo que escribas va aquí.
- `<py>` = `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe` (ruta con espacios: entre comillas).
- Entrega del implementador: `<ws>\STATE.md`; brief que recibió: `<lote>\run1\brief.txt`; diffs LF-normalizados generados por el receptor: `<lote>\DIFF-R6-product.patch` y `<lote>\DIFF-R6-tests.patch`.
- Tests: desde `<ws>\tools` con `PYTHONPATH=.`: `& "<py>" -m unittest tests.test_vpp_preflight`. Regresión: `tests.test_native_launcher_transaction tests.test_dayz_test_tool tests.test_dayz_test_tool_modes tests.test_lifecycle_reconcile`. NO corras la suite completa (levanta daemons). No lances juego ni launcher.

## El contrato (lo único que vale; el STATE.md del implementador es una afirmación, no evidencia)

Decisión del dueño del proyecto (2026-09-06): el launcher es AGNÓSTICO respecto a la herramienta de
administración. `evaluate_vpp_preflight(payload, policy, *, files=None)` en
`tools/dayz_mcp/native_launcher_transaction.py`:

| Caso | Resultado exigido |
|---|---|
| (a) `mode_starts_server(mode)` es False | sin cambios respecto a a7ddbac: limpio, sin lecturas |
| (b) arranca servidor y la lista efectiva NO trae candidato VPP (`_is_vpp_candidate` sobre `effective_mod_entries(payload)`) | `error_code=None`, `missing=()`, `warnings=("vpp_mod_not_requested",)`, `hint=VPP_ABSENT_HINT`; **CERO lecturas del host** (ni cfg, ni Permissions, ni meta.cpp) |
| (c) arranca servidor y SÍ trae candidato VPP | **byte a byte la misma decisión que a7ddbac**: mismos tokens en `missing`/`warnings`, mismo `hint=VPP_PREFLIGHT_HINT` (salvo su arranque reescrito, ver abajo), mismas lecturas |

- `VPP_ABSENT_HINT` nueva, contiene `@VPPAdminTools`, no contiene `refuse`. `VPP_PREFLIGHT_HINT` arranca por `the requested admin tools are not usable:` y el resto no cambia.
- `enforce_vpp_preflight`: código intacto (levanta `ValueError(error_code)` solo si hay `error_code`); docstring reescrito. Call-site en `execute_native_launcher_transaction` intacto (antes de acreditar rutas y del lease).
- Ninguna firma pública cambia; ningún token se renombra; `mode_starts_server`, `effective_mod_entries`, `_is_vpp_candidate`, `_resolved_mod_path`, `_proves_vpp_identity`, `_vpp_password_is_disabled`, `_live_assignments`, `_skip_non_string_dead_ground` intactos byte a byte.
- Write-set permitido: `tools/dayz_mcp/native_launcher_transaction.py`, `tools/tests/test_vpp_preflight.py`, `STATE.md`, `BRIEF.txt`. Cualquier otro fichero de `<ws>` modificado tras `<lote>\run1\STARTED` es hallazgo ALTA.
- Tests existentes que PUEDEN cambiar, y solo como dice el brief §3 (T1..T10): `test_server_mode_without_the_admin_tools_is_refused` (renombrado), `test_server_only_mode_is_refused_the_same_way` (renombrado), `test_no_base_mods_drops_a_default_that_only_lived_in_the_policy` (dos asertos), `test_a_preflight_request_fails_exactly_where_a_launch_would` (caso), `test_enforce_raises_the_declared_token` (caso), `test_the_refusal_writes_nothing_to_the_workspace` (caso), `test_a_server_request_without_admin_tools_never_takes_a_lease` (renombrado + caso), `test_a_server_run_without_admin_tools_never_reaches_the_launcher` (renombrado + stub `_refused`). Las clases `VppPreflightInvariantTest`, `VppPreflightCallSiteTest`, `VppPreflightRound4Test`, `VppPreflightRound5Test` y el resto de métodos: asertos byte a byte como en a7ddbac. Un aserto debilitado fuera de esa lista es hallazgo ALTA.

## Preguntas que debes contestar con medida, no con lectura

- **Q1 Lecturas en la rama (b).** Ejecuta `preflight_vpp_request` con un `FakeFiles` (el del test, tiene `.reads`) y `extra_mods` sin VPP en `mode="all"` y `mode="server"`: `reads` debe quedar vacío. Repite con `preflight=True`. Traza además el código: ¿hay algún camino en el que la rama (b) llegue a `_read_or_absent`, `vpp_preflight_paths` o `_resolved_mod_path`?
- **Q2 Rama (c) inalterada.** Diff de la función `evaluate_vpp_preflight` entre `git -C <wt> show HEAD:...` y `<ws>`: fuera del retorno temprano de (b) y de docstrings/comentarios, ¿cambia alguna línea? Ejecuta al menos estos casos en los dos árboles (importando cada módulo desde su árbol, p.ej. con `PYTHONPATH` distinto en dos procesos) y compara `missing`/`warnings`/`hint` campo a campo: VPP pedido con meta.cpp ajeno (`META_OTHER`), carpeta ausente, entrada relativa con varias raíces, cfg sin la clave, cfg con la clave a 0, cfg > tope, superadmins ausentes, todo sano.
- **Q3 Modo desconocido.** `mode_starts_server("not-a-mode")` es True (fail-closed). Con la ronda 6, un modo desconocido SIN VPP ya no se rechaza: se avisa. ¿Es un hueco real o teórico? Mira `dayz_test_request.py` (el brief cita `:297` para el rechazo de nombres desconocidos en la capa de request) y decide con cita.
- **Q4 Embudo y lease.** El test del chokepoint con VPP pedido e inusable sigue demostrando `acquire_calls == 0` y `consumer_calls == []`; el nuevo test sin VPP debe pasar la puerta (lo que falle después es de otro). Ejecútalos y mira que miden lo que dicen (no un `assertNotEqual` vacío por un error anterior no relacionado: imprime `raised`).
- **Q5 Tests.** Lista cada test existente cambiado y comprueba que está en la lista permitida y en la forma dicha. Recuenta: cuántos tests tenía a7ddbac (58) y cuántos tiene la entrega; cada nuevo con su nombre. Ejecuta el rojo-primero tú mismo: copia el `test_vpp_preflight.py` de la entrega sobre un directorio temporal con el PRODUCTO de a7ddbac (obtenido con `git show`, nunca tocando `<wt>`) y mide cuántos tests caen; deben caer exactamente los que fijan el contrato nuevo (T1, T2, T3, T4-bis, T5, T7-bis, T8-bis, T9-bis, T10-bis) y ninguno del contrato viejo que no debiera.
- **Q6 Mutantes.** Sobre una COPIA del producto entregado (en `<rev>`, nunca en `<ws>`): (a) `vpp_mod_not_requested` de vuelta a `missing`; (b) leer el cfg en la rama (b) antes de devolver; (c) `VPP_PREFLIGHT_HINT` en vez de `VPP_ABSENT_HINT` en (b). Cada mutante debe dejar al menos un test rojo; anota cuál. Si un mutante sobrevive, hallazgo MEDIA (cobertura), no del producto.
- **Q7 Redacción.** `grep -n -i "refus\|reject\|no bypass\|without admin tools"` en el módulo entregado: ¿queda alguna frase que diga que la puerta rechaza cuando falta el mod? Comentarios en inglés, tono neutro, sin fechas ni primera persona (regla del proyecto).
- **Q8 Forma del diff.** Los `.patch` del receptor están LF-normalizados. Comprueba que `<ws>` conserva LF en los dos ficheros (`grep -c $'\r'` = 0) y que el diff del producto solo contiene los hunks del contrato (retorno temprano, constante nueva, dos hints, docstrings/comentarios). Un hunk fuera de eso se explica o es hallazgo.

Nada de lo que pegue el implementador en STATE.md cuenta como verificado hasta que lo reproduzcas.
