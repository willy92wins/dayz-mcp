# df93 ronda 6 — integración y recepción (2026-09-06, 20:0x-20:5x)

**Resultado**: commit `1bc7c52` en `work/df93-vpp-preflight` (sobre `a7ddbac`), 6 ficheros, 261 inserciones /
82 borrados, idéntico al `git diff --stat` medido antes de commitear (LL-354). Worktree `wt-df93` sin cambios
rastreados pendientes. (Escrito antes del merge: la rama se mergeó después, commit `4e264bb`, ver §«Merge a la
rama viva» al final.)

## Cadena de la ronda

| Paso | Qué | Evidencia |
|---|---|---|
| Copia fiel | `wt-df93` (a7ddbac) → `scratchpad/lote-df93-r6/ws`, 214 MiB, 180 módulos de test; excluidos además `_client _compile _gamemaster_h0_release _backups .superpowers` (artefactos sueltos del worktree, 870 MiB sin ellos fuera de tope) | hashes ws = rama: producto `079c6737…`, tests `46ec4ecb…` |
| Implementador | Cursor `cursor-grok-4.6-xhigh` (`"model":"Cursor Grok 4.6 Extra High"` en el stream), 20:08:27 → 20:15:56, RC=0 | `RUN1-R6.txt`, `BRIEF-R6.txt`, `STATE-R6-grok.md` |
| Write-set | solo `native_launcher_transaction.py`, `test_vpp_preflight.py`, `STATE.md` | `find -newer STARTED` |
| Entrega (LF) | producto `3ca12ca20597a09d…`, tests `a3fe333dc0b7ae93…` (el editor reescribe CRLF; diffs normalizados) | `DIFF-R6-product.patch` (79 líneas ±), `DIFF-R6-tests.patch` (163) |
| Rojo-primero (Grok, reproducido por el revisor) | 9 rojos sobre a7ddbac: 6 failures + 3 errors (`VPP_ABSENT_HINT` inexistente); 58 OK sin tocar | STATE §rojo-primero; review Q5 |
| Verde | `tests.test_vpp_preflight` 58 → 64 OK; regresión 4 módulos 129 OK (en ws por Grok, en ws por mí, en `wt-df93` tras integrar) | `recibir_r6.sh`; salida en este documento |
| Mutantes | (a) token de vuelta a `missing` → 6 rojos; (b) leer cfg en la rama de aviso → 1 rojo (T5); (c) hint equivocado → 2 rojos. Revisor: 4 mutantes muertos | STATE §Mutantes; review Q6 |
| Revisión cruzada | Anthropic (Fable 5.1, subagente ciego), 23 min, 58 herramientas: **SEGURO INTEGRAR**; H1 MEDIA (test), H2 BAJA (docstrings), H3 BAJA (observación fuera del write-set) | `REVIEW-ANTHROPIC-R6.md` (412 líneas) |
| Receptor | H1 y H2 aplicados (`integrar_r6.py`); comentarios de `dayz_test_tool.py` y 3 tests (`patch_r6_comments.py`); NO re-revisados por otra familia | escrito `b1dc613bf8567416…` (producto), `b63c8b5f66cdeeb2…` (tests) |
| EOL | `git ls-files --eol`: `i/lf w/lf` en los 6 | antes del commit |

## Hallazgos del revisor y qué se hizo

- **H1 (MEDIA, test)**: `test_a_server_request_with_unusable_admin_tools_never_takes_a_lease` leía el host real y su
  rechazo descansaba en que `P:\Suite` no exista (aquí `P:\Mods\@VPPAdminTools\meta.cpp` SÍ existe). Aplicado: la
  policy del caso se enraíza en un `TemporaryDirectory` vacío (como T8); la carpeta del mod y el cfg faltan por
  construcción. `_transaction` acepta `policy=`.
- **H2 (BAJA)**: docstring de `mode_starts_server` y docstring del módulo de tests describían un rechazo
  incondicional. Reescritos.
- **H3 (BAJA, fuera del write-set, NO aplicado)**: en la ruta CLI (`secure_launcher`) el `VppPreflightResult` se
  descarta en `execute_native_launcher_transaction` (:615): el aviso solo es observable por la ruta MCP
  (`vpp_warnings` en la respuesta de `dayz_test_run`), y en éxito no viaja el `hint`. Ya era así para
  `vpp_superadmins_absent` desde la ronda 1. Queda anotado como frontera para la resolución de la ficha.

## Corolarios de Reserva (mensaje 20:1x)

- Al no exigir VPP no hay que resellar `request-policy.json`: su rebuild dejó `request_policy_sha256` sin cambio
  (`0BFC9D30EB4CD9EEAB660F6387FDE134BE31FF1AE8D978B0417A394F61FD8900`). La ronda 6 no necesita ventana de rebuild.
- Suite completa a solas en la viva `1cb90b5`: `Ran 2847, failures=2` (centinelas de bd90). `FailedLaunchSettlementTest`
  es intermitente, no regresión (ficha `fb-20260906-172844-30c1`).

## Efecto sobre las diez policies selladas

Con `mode=server` y sus defaults (medida del lote, `lote-df93/gate/medida-policy-viva.txt`): las seis con VPP por ruta
absoluta pasaban limpias y siguen igual; las cuatro sin VPP (`DayZ_MCP`, `LF_VStorage`, `LFPowerGrid`, `SimpleGroup`)
pasaban de RECHAZO a arranque con `vpp_warnings: ["vpp_mod_not_requested"]`. Ningún proyecto cambia de resultado a
peor.

## Pendiente (decisión de Guillermo) — escrito antes del merge; el punto 1 quedó HECHO a las 20:5x (§Merge)

1. **Destino** (HECHO, `4e264bb`): merge a `work/inbox-20260830-modules`. Merge en seco (`git merge-tree --write-tree 1cb90b5
   work/df93-vpp-preflight`) limpio antes de la ronda 6 (árbol `864229e`); tres ficheros tocados por ambos lados
   (`dayz_test_tool.py`, `test_dayz_test_tool.py`, `test_lifecycle_reconcile.py`) sin conflicto textual. Tras el
   merge: suite completa contra la línea base 2847/2 de Reserva. Recomendación: mergear ya; no toca nada sellado y
   ningún proyecto empeora.
2. **G-VPP in-game**: arrancar por esta ruta CON VPP y ver el menú sin contraseña; y un arranque SIN VPP que salga
   con el aviso. Requiere sesión de Steam viva (hoy `steam_session_stale`).
3. La ficha `df93` queda ABIERTA hasta 1 y 2. Lección candidata (decisión de diseño colada como cláusula de picker
   de lotes): en `DECISION-R6.md`; se propone al cierre, no se escribe sin OK.

## Merge a la rama viva (2026-09-06 20:4x-21:xx, orden de Guillermo: «luego mergea a la rama viva»)

- **Commit de merge `4e264bb`** en `work/inbox-20260830-modules`, padres `1cb90b5` (viva) y `1bc7c52` (df93 r6); 7 ficheros,
  2206 inserciones / 2 borrados, exactamente el `git diff --stat HEAD <árbol>` medido en seco.
- **Por plumbing, no por `git merge`**: la viva tenía `decisions/decision-log.md` staged por una tercera sesión y un
  `git merge` lo habría metido en el commit. Secuencia: `git merge-tree --write-tree HEAD work/df93-vpp-preflight` →
  árbol `42187e2…` (recalculado dos veces, idéntico) → `git commit-tree` con `MERGE-MSG.txt` → `git update-ref` con
  guarda del valor viejo → `git checkout HEAD -- <7 rutas>` para sincronizar índice y árbol de trabajo solo en esas
  rutas. Después: `git diff --cached --stat` lista solo `decision-log.md` (830 líneas, ajeno, intacto); `git diff`
  vacío; `git ls-files --eol` de los 7: `i/lf w/lf`.
- **Suite completa en el árbol vivo tras el merge** (`suite_post_merge.sh`, log `suite-post-merge.log` en el
  scratchpad de la sesión): Ran 2911 tests en 260 s, FAILED (failures=2, skipped=6): solo los dos centinelas de bd90 (test_full_source_hash_is_frozen y test_removing_only_marker_lines_restores_frozen_source_hash); los 2847 de la linea base de Reserva + 64 nuevos, misma linea base (2847/2); log SUITE-post-merge-run2.log. Línea base de Reserva a solas sobre `1cb90b5`: `Ran 2847, failures=2`
  (centinelas de bd90); `FailedLaunchSettlementTest` intermitente (ficha 30c1).
- **El probe host-local, que el merge no puede traer**: la primera suite tras el merge dio `Ran 2911, failures=2,
  errors=1`: el error era `VppPreflightCallSiteTest.test_every_caller_of_the_backend_runs_the_gate_first`, que lee
  `tools/h9_native_probe.py` (NO rastreado; `test_lote_w_h9.py:18-21` lo declara host-local) y exige la llamada a
  `enforce_vpp_preflight` antes de `launch_registered_native`. El árbol vivo tenía la versión SIN la puerta
  (`142ec6f2…`, 15 266 B, 2026-09-05 02:41); la del lote (`lote-df93/host-local/h9_native_probe.py`, `a3b84fce…`,
  15 773 B, idéntica a la de `wt-df93`) la lleva en la línea 217. Aplicado lo que dice su `LEEME.txt`: copia sobre
  `tools/h9_native_probe.py` (backup del vivo en `df93-r6/host-local/h9_native_probe.py.vivo-pre-merge`);
  `tests.test_vpp_preflight` + `tests.test_lote_w_h9`: 66 OK. Log de la primera suite: `SUITE-post-merge-run1.log`.
  Con la ronda 6 la puerta en el probe solo avisa (compone `mode=server` sin VPP), así que su conducta no cambia.
- **Daemon**: bridge_status tras el merge: daemon_modules.stale = [dayz_test_tool.py, native_launcher_transaction.py] (fuente mas nueva que el daemon 484794f6, no es fallo); el re-spawn es de Reserva y no lo he tocado; una sesion MCP nueva ya carga el overlay
- La rama `work/df93-vpp-preflight` queda mergeada (no se borra: la referencian los recibos). Ficha df93 ABIERTA hasta
  el gate in-game G-VPP (con VPP: menú sin contraseña; sin VPP: arranque con `vpp_warnings`).
