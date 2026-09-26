# Auditoría de cierre — 8 fichas (2026-09-04)

Auditor: sesión read-only sobre `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\`, rama `work/inbox-20260830-modules`.
Intérprete de toda medición propia: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe` (Python 3.14.3), invocado desde `tools\` con `PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1`.
No se corrió la suite completa. No se tocó git, MCP, ni procesos DayZ.

## Criterio de cierre aplicado

`plans\2026-09-03-sistema-triaje-y-tratamiento-del-buzon.md:272` (literal):

> «Criterio de cierre reutilizado sin cambios (I7, `HANDOFF.md:26-30`): (a) rama por lane y commits atribuibles; (b) suite verde MEDIDA nombrando el intérprete; (c) el `app.pyz` desplegado arranca; (d) revisión de otra familia con repro-o-backlog, tope 2 rondas, la tercera ORCHESTRATOR_NEEDED; (e) `pipeline_resolve` citando commit + suite + pyz + dictamen.»

Y en la misma línea: «**Un bug no cierra sin `G-REPRO`**: sin repro ejecutable la ficha se marca DIFERIDO con el motivo.»

Regla de autoridad del DAG, `plans\inbox-20260830\00-execution-dag.md:11-13` (literal):

> «Cada feedback conserva plan, hash y veredicto individual. Los módulos solo agrupan escritura. Un PASS de módulo no cierra automáticamente ninguna ficha y una revisión agrupada sin un bloque por ID es inválida.»

Regla anti-«verde ≠ verificado» que gobierna esta auditoría, `plans\2026-09-03-sistema-triaje-y-tratamiento-del-buzon.md:270`: «(1) Provenance = `git status` limpio + el commit + `grep` del token central; "tiene informe" y "existe el fichero" ya han contado de más.»

**Alcance de esta auditoría.** Sólo mido la dimensión sustantiva: **¿están los criterios PASS de cada ficha medidos uno a uno?** Las condiciones (a)/(c)/(d)/(e) del criterio I7 (commits, `app.pyz`, revisión cruzada, `pipeline_resolve`) quedan **fuera de mi verificación** por instrucción (no git, no MCP) y se listan en «LO QUE NO PUDE VERIFICAR».

### Medición base compartida (mis propias corridas)

| Comando (desde `tools\`) | Resultado |
|---|---|
| `-m unittest tests.test_mcp_capture` | `Ran 21 tests ... OK` |
| `-m unittest tests.test_tool_registry_fingerprint` | `Ran 20 tests ... OK` |
| `-m unittest tests.test_pipeline_feedback` | `Ran 15 tests ... OK` |
| `-m unittest tests.test_wait_for tests.test_wait_for_marker tests.test_wait_for_launch_and_contract` | `Ran 41 tests ... OK` |
| `-m unittest tests.test_dayz_test_modes tests.test_dayz_test_request tests.test_knowledge_tools tests.test_knowledge_pack_install tests.test_effective_schema tests.test_effective_schema_core tests.test_effective_schema_catalog tests.test_effective_schema_runtime_validators` | `Ran 72 tests ... OK` |
| `-m unittest tests.test_mcp_tools` | `Ran 45 tests ... OK` |

### Sonda de superficie pública (script propio, `scratchpad\probe_schema.py` / `probe3.py`)

Construye `build_app(ServerConfig())` en proceso y lee `await app.list_tools()`. Resultados literales:

- `dayz_knowledge_prepare` → `{"properties": {}, "title": "...", "type": "object"}` — **sin** `additionalProperties`, **sin** `required`.
- `dayz_knowledge_status` → idéntico.
- `await app.call_tool("dayz_knowledge_status", {"repo": "Y"})` → **NO error**; devuelve el payload de status. Los extras se ignoran.
- `await app.call_tool("dayz_knowledge_prepare", {"path": "X"})` → `ToolError ... knowledge_pack_missing`; **no** `bad_args: unexpected arguments`.
- `capture_screenshot` → propiedades `crop, fmt, frames, max_tokens, process_name, quality, save_dir, save_fullres, scale`. **No existe `crop_space`.**
- `pipeline_resolve` → `required: ["feedback_id","resolution"]`, propiedades sólo esas dos. **No existe `evidence_ref`.**
- `dayz_test_run` → `required: ['project','mode']`; `mode` es `{"title":"Mode","type":"string"}` (**sin enum**); `kill` **no** es propiedad.
- `dayz_effective_schema` → **NOT REGISTERED**. La app registra 60 tools y ninguna contiene la subcadena `schema`.

### Estado del árbol de informes

`reports\2026-08-30-inbox-implementation\` contiene: `M00 M01 M06 M07 M08 M09 M11 M12 M13 M14 M16 M17 M21 M22`.
**No existen** `M18`, `M19`, `M20`, `M23`. `M22\` contiene un único fichero, `bug086-evidence.json`.

---

## Ficha 03 — fb-20260828-224835-268a — M07-CAPTURE → M22

Plan: `plans\inbox-20260830\03-fb-20260828-224835-268a.md`.
Criterio literal, `:47`: «PASS: `capture_dual` recibe y aplica `crop_space=client`; client vacío excluye el marco antes del downscale y el spy acredita cero llamadas a `apply_crop`; crop adicional estricto usa el viewport; window conserva el wrapper legacy; `bad_crop_space` y `bad_crop` son distintos; ambas ramas incluyen `window_surface/client_surface/effective_surface`; `meta.window` conserva su forma legacy; `meta.frame_sha256 == meta.window_surface.pixel_sha256`; el hash delivered coincide con el ImageContent y delivered/fullres derivan de la misma `effective_surface` nativa.»

### Criterios y evidencia

| Criterio | Veredicto | Evidencia |
|---|---|---|
| `capture_dual` recibe y aplica `crop_space=client` **(lectura A: sólo el helper)** | MET | `tools\mcp_capture.py:644` (`crop_space: str = DEFAULT_CROP_SPACE`), `:671` (rechazo fuera del enum), `:689-696` (rama client). Test `test_client_default_selects_the_viewport_before_the_downscale` (`tools\tests\test_mcp_capture.py:339`) |
| Lo mismo **(lectura B: extremo a extremo, como exige el paso 8 del plan «el fixture demuestra que `crop_space=client` llega desde `server.py:3784-3796` a `capture_dual`»)** | NOT MET | `tools\dayz_mcp\server.py:3938-3948`: la firma pública de `capture_screenshot` no tiene `crop_space`; `:3997-4010` llama a `capture_dual` sin reenviarlo. Sonda: el schema wire no publica `crop_space` |
| Client vacío excluye el marco antes del downscale; spy con cero llamadas a `apply_crop` | MET | `tools\tests\test_mcp_capture.py:361-368` (`self.assertEqual(0, apply_spy.call_count)`, `encode_spy.call_args.args[0].size == (600,300)`) |
| Crop adicional estricto normaliza sobre el viewport | MET | Test `test_client_additional_crop_normalizes_over_the_viewport` (`test_mcp_capture.py:388`), 8 casos incl. `center:0.05` y `center:1` |
| Window conserva el wrapper legacy fail-open | MET | `tools\mcp_capture.py:698-701` (`apply_crop(window_rgb, crop)`); test `test_window_mode_keeps_the_legacy_fail_open_crop` (`test_mcp_capture.py:532`) |
| `bad_crop_space` ≠ `bad_crop`, ambos identificadores ASCII en el wire | MET | `tools\mcp_capture.py:113-115`; tests `test_bad_crop_space_is_rejected_before_any_grab_and_is_distinct_from_bad_crop` (`:512`) y `test_error_tokens_are_the_literal_wire_identifiers` (`:707`) |
| `frame_client_rect_unverified` fail-closed, sin fallback a window | MET | `tools\mcp_capture.py:690-691`; test `test_client_rejects_an_unverifiable_client_rect` (`:426`) |
| **Ambas ramas** (con y sin `save_fullres`) incluyen los tres records de superficie | NOT MET | En el helper sí: `tools\mcp_capture.py:717-736`. En la **frontera pública** no: `tools\dayz_mcp\server.py:4027-4029` devuelve `return image` a secas cuando `save_fullres=False`, sin bloque JSON. El plan `:41` exige explícitamente el segundo bloque en la ruta default |
| `meta.window` conserva forma legacy; `meta.frame_sha256 == meta.window_surface.pixel_sha256` | MET | `tools\mcp_capture.py:716,719-721`; test `test_head_identity_scenario_keeps_frame_sha256_as_the_window_hash_in_both_spaces` (`test_mcp_capture.py:724`) |
| Hash delivered mide los bytes devueltos | MET | `tools\mcp_capture.py:731-736` (`_decode_image_content(inline)`); test `test_default_jpeg_delivery_is_measured_on_the_returned_bytes` (`:642`) |
| `fullres` deriva de la misma `effective_surface` nativa | MET | `tools\mcp_capture.py:739-742` (`write_fullres(effective_native, ...)`); test `test_fullres_saves_the_native_effective_surface_not_the_chosen_frame` (`:587`) |
| Test público que construye la app y llama `app.call_tool("capture_screenshot", ...)` con `save_fullres` False y true (plan paso 7) | NOT MET | Grep de `call_tool("capture_screenshot"` en `tools\tests\`: un solo hit, `tools\tests\test_d05_capture_targets_run_client.py:83`, y es el gate D05 de targeting de ventana, no el contrato de superficies |

### Nota de provenance

`reports\2026-08-30-inbox-implementation\M07\m07-owns.json` congela `tools/mcp_capture.py` en sha256 `b06fa0ea…` / 21840 bytes; el fichero vivo es `5a55c8bf714e2ea2bcf946e152bbe03bfac82fbe5e56437b28822e16ad170281` / 33817 bytes. El propio informe lo dice: `m07-test-run.json` → `"caveat": "…en M07 la suite verde era anterior al lote"`. **El informe M07 es un snapshot pre-implementación y no acredita nada de esta ficha.** La acreditación viva es mi corrida de 21 tests.

### Veredicto de ficha

**CIERRA CON TRABAJO**: falta toda la mitad M22 — (1) `crop_space` en la firma y el reenvío en `tools\dayz_mcp\server.py:3938-3948` y `:3997-4010`; (2) devolver `[ImageContent, JSON meta]` también con `save_fullres=False` en `tools\dayz_mcp\server.py:4027-4029`; (3) el test público de round-trip por `app.call_tool` (fichero nuevo o región nueva bajo dueño M22).

---

## Ficha 07 — fb-20260829-024827-9b7b — M14-REGISTRY-FINGERPRINT → M22 → M23

Plan: `plans\inbox-20260830\07-fb-20260829-024827-9b7b.md`.
Criterio literal, `:44`: «PASS: una sesión FastMCP vieja observa por el overlay local de `bridge_status()` la autoridad exacta `reports/2026-08-30-inbox-implementation/M23/dayz-effective-schema-v1.json`, íntegra y enlazada a sus sidecars/receipt, de la misma pareja profile/role y queda stale; la nueva queda fresh aunque ambas reciban el mismo `/status`; `/status` y `status_provider` no contienen `tool_registry_*`; cruce de profile o role, identidad ausente, candidate/backup/journal o autoridad corrupta produce null/unknown. Las cuatro configs positivas aparecen exactamente una vez.»

### Criterios y evidencia

| Criterio | Veredicto | Evidencia |
|---|---|---|
| Fingerprint canónico determinista, orden independiente del registro | MET | `tools\dayz_mcp\tool_registry_fingerprint.py:358` `canonical_registry_fingerprint`, `:301` `canonical_json_bytes`, `:269` `_nfc`. Tests `test_record_order_does_not_change_digest_constraint_order_does`, `test_mutating_each_field_changes_digest`, `test_nfc_positive_and_post_nfc_collisions` (`tools\tests\test_tool_registry_fingerprint.py`) |
| Lector de autoridad fail-closed byte a byte (versión, pareja, tres hashes, productores, receipt) | MET | `tools\dayz_mcp\tool_registry_fingerprint.py:647` `_read_authority_marker`, `:701` `read_authority_marker` (captura y devuelve `_UNKNOWN_AUTHORITY`). Tests `AuthorityBundleTests` ×9, incl. `test_payload_tool_and_hash_sidecars_must_stay_linked`, `test_receipts_and_digest_only_incomplete_parser` |
| Las cuatro parejas `standard\|exec_enforce × claude\|codex` exactamente una vez | MET | Test `test_four_pairs_match_external_bytes_and_sha` y `test_literal_bundle_is_known_for_each_pair` |
| Cruce de profile/role → unknown | MET | `tool_registry_fingerprint.py:781-782` (`if (local.profile, local.role) != (authority.profile, authority.role): return "unknown"`); test `test_same_pair_distinct_registries_and_cross_pair_digest_copy` |
| Overlay fusionado **después** de `bridge_status_payload()` | MET | `tools\dayz_mcp\server.py:4049-4054`: `payload = await runtime.bridge_status_payload()` y luego `_with_tool_registry(...)`. Test `test_bridge_status_publishes_frozen_tool_registry_overlay` (`tools\tests\test_mcp_tools.py:589`) |
| Snapshot tomado al cierre de `build_app`, congelado por proceso | MET | `tools\dayz_mcp\server.py:4558` (`_tool_registry_overlay.update(_frozen_tool_registry_overlay(app, config))`), `:2745-2750`. Test `test_mcp_tools.py:603-611` (`capture.assert_not_called()` en la segunda llamada) |
| `/status` y `status_provider` sin `tool_registry_*` | MET | Grep `tool_registry` en `tools\dayz_mcp\loopback.py` y `tools\dayz_mcp\daemon.py` → 0 hits. Test `test_loopback_status_omits_tool_registry_overlay` (`tools\tests\test_mcp_tools.py:613`) |
| `tool_registry_remediation="reopen_mcp_client"` | MET | `tools\dayz_mcp\server.py:537,589`; aserción en `test_mcp_tools.py:599` |
| Snapshot ligado a `ControlIdentity.session_id` (plan `:29`, y FAIL «PID/daemon como identidad») | NOT MET | `tools\dayz_mcp\server.py:564`: `session_id=str(uuid.uuid4())`. Es un UUID nuevo por `build_app`, no el `ControlIdentity.session_id` que exige `tools\dayz_mcp\control_client.py:41-72`. El escenario «dos procesos, mismo daemon, session_id distintos» no está atado a la identidad real |
| Profile/role no acreditable → `unknown`, nunca fresh (plan `:28-29`; FAIL «role no canónico») | NOT MET | `tools\dayz_mcp\server.py:561-562`: `if profile == "unknown" or role == "unknown": profile, role = "standard", "claude"`. Es un default **fail-open** que fabrica una pareja canónica donde el plan exige `unknown` |
| «Una sesión vieja queda **stale** y la nueva **fresh**» sobre la autoridad M23 | NEEDS AUTHORITY | `tools\dayz_mcp\server.py:574-584` construye `AuthorityBundleBytes(marker=None, fingerprint_sidecar=None, verdict_sidecar=None, producers_sidecar=None, receipts=None)`: **la autoridad nunca se lee de disco**. Con eso, `compare_snapshot_to_authority` (`tool_registry_fingerprint.py:769-784`) devuelve siempre `"unknown"`. El propio test M22 lo consagra: `test_mcp_tools.py:600-602` afirma `self.assertIn(stale, (None, "unknown"))` |
| Autoridad `reports/2026-08-30-inbox-implementation/M23/dayz-effective-schema-v1.json` | NEEDS AUTHORITY | El directorio `reports\2026-08-30-inbox-implementation\M23\` **no existe** (`ls` → *No such file or directory*). Sin él no hay lado «actual» que comparar |
| «Releído read-only y fail-closed **en cada** `bridge_status`» (plan `:31`) | NOT MET | El overlay se calcula **una vez** en `server.py:4558` y se sirve congelado desde `:2745-2750`. Hay contradicción interna en el propio plan: `:31` pide relectura por llamada y `:32` pide snapshot inmutable post-registro. **Dos lecturas**: (i) sólo el *snapshot local* es inmutable y la *autoridad* se relee por llamada → hoy NOT MET, cerraría con un `read_authority_marker` dentro de `bridge_status()`; (ii) todo el overlay es inmutable → hoy MET estructuralmente, pero entonces `:31` está mal redactado. **No elijo: es una decisión del dueño del plan.** |

### Veredicto de ficha

**BLOQUEADA**: el criterio central (vieja→`stale`, nueva→`fresh`) es hoy **inalcanzable** porque la autoridad M23 no existe ni se lee. El propio plan lo clasifica: `:46` «INCONCLUSIVE: … falta una autoridad M23 verificable de la misma pareja `(profile,role)`». Además dos condiciones son **NOT MET por código**, no por falta de autoridad, y hay que arreglarlas aunque llegue M23: `server.py:564` (session_id fabricado) y `server.py:561-562` (default fail-open a `standard/claude`). Y hay una **NEEDS DECISION** sobre la contradicción `:31` vs `:32` (relectura por llamada vs overlay congelado) — la decide el dueño del plan.

---

## Ficha 08 — fb-20260829-024848-c7ca — M08-INBOX → M22

Plan: `plans\inbox-20260830\08-fb-20260829-024848-c7ca.md`.
Criterio literal, `:38`: «PASS: helpers y round-trip `pipeline_resolve→JSONL→pipeline_inbox` aceptan las cuatro raíces; doce mutantes se rechazan; schema mantiene `evidence_ref` opcional max 240 y required legado; dos resoluciones prueban last-wins/borrado; bordes 59/60/3599/3600/86399/86400 usan el `ts` original; invalid/future es null+razón y el archivo solo crece.»
FAIL literal, `:39`: «el helper pasa pero **la tool pública no acepta/propaga `evidence_ref`**, required cambia, path libre, edad derivada del `ts` de resolución, edad guardada, …»

### Criterios y evidencia

| Criterio | Veredicto | Evidencia |
|---|---|---|
| Helper acepta las cuatro raíces `reviews\|gates\|reports\|research` | MET | `tools\dayz_mcp\inbox.py:30-46` (`_validate_evidence_ref`), entrada en `:125,130,144-145`. Test `test_resolution_evidence_ref_accepts_only_the_four_durable_roots` (`tools\tests\test_pipeline_feedback.py:137`) |
| Doce mutantes rechazados (vacío, `.`, `..`, absoluto, drive, `\\`, `:`, URI, control, raíz ajena, no-ASCII, >240) | MET | Mismo test, `test_pipeline_feedback.py:137-171` (el informe M08 declara 14 mutantes: `m08-implementation.json` → `"tests": "…:137-171 (4 valid roots + 14 rejected mutants)"`) |
| Edad derivada del `ts` **original** de la entrada, nunca del de la resolución | MET | `tools\dayz_mcp\inbox.py:203` (`item.update(_age_fields(item.get('ts'), now))` sobre la entrada, no sobre la resolución); `:49-67` `_age_fields` |
| Bordes 59/60/3599/3600/86399/86400 con floor `Ns/Nm/Nh/Nd` | MET | `tools\dayz_mcp\inbox.py:59-66`; test `test_age_boundaries_are_derived_from_original_ts_without_persistence` (`test_pipeline_feedback.py:193`) |
| `invalid_timestamp` / `future_timestamp` → ambos null + razón | MET | `tools\dayz_mcp\inbox.py:51,55,57`; mismo test |
| Edad nunca persistida; el fichero sólo crece | MET | El bloque `_age_fields` sólo se aplica en la ruta de lectura `_read_inbox` (`inbox.py:203`); mismo test lee el JSONL crudo |
| Última resolución gana y **limpia** la ref anterior | MET | `tools\dayz_mcp\inbox.py:185-187` (`by_id[target].pop("evidence_ref", None)` y sólo re-añade si la nueva la trae). Tests `test_resolve_last_wins` (`:130`) y `test_latest_resolution_without_evidence_ref_clears_the_previous_ref` (`:173`) |
| Límite de `resolution` sigue en 1..2000 | MET | `tools\dayz_mcp\inbox.py` `append_resolution` (rango conservado; el informe M08 declara «working bytes not rewritten») |
| **Round-trip público `pipeline_resolve→JSONL→pipeline_inbox`** | NOT MET | `tools\dayz_mcp\server.py:4516-4519`: `async def pipeline_resolve(feedback_id: str, resolution: str)`. Sin `evidence_ref`. `:4525-4527` llama a `inbox.append_resolution(feedback_id, resolution, platform=…)` sin propagarlo. Sonda wire: `pipeline_resolve` → `required: ["feedback_id","resolution"]`, propiedades sólo esas dos |
| Schema mantiene `evidence_ref` opcional `maxLength=240` | NOT MET | Sonda wire: la propiedad **no existe**. `tools\tests\test_pipeline_feedback.py:279-281` sólo comprueba que `rs_params` tiene `feedback_id`/`resolution` |
| `required` legado `{feedback_id, resolution}` intacto | MET | Sonda wire y `test_tools_registered_signatures` (`test_pipeline_feedback.py:246`, aserción en `:281`) |

### Nota de provenance

`reports\2026-08-30-inbox-implementation\M08\m08-implementation.json` declara `"scope": "plan points 1-3 + helper tests"` y lista `tools/dayz_mcp/server.py` en `not_touched`. El hash del informe (`c7843a7bc4e023fbabc57925928d8190a5f3738515430abceaaf64334052f0a7`) **coincide** con el fichero vivo `tools\dayz_mcp\inbox.py`: la entrega M08 está anclada. Los puntos 4 y 5 del plan (M22) están declarados como no hechos por el propio implementador.

### Veredicto de ficha

**CIERRA CON TRABAJO**: falta exactamente el punto 4+5 del plan en `tools\dayz_mcp\server.py:4516-4527` — añadir `evidence_ref: Annotated[str, Field(max_length=240)] | None = None`, propagarlo a `append_resolution`, conservar `required={feedback_id,resolution}` — y su gate de round-trip público en `tools\tests\test_pipeline_feedback.py` (región M22). Nota: hoy se cumple literalmente el FAIL declarado en `:39` («el helper pasa pero la tool pública no acepta/propaga `evidence_ref`»).

---

## Ficha 10 — fb-20260829-025502-251d — M22-SERVER-INTEGRATION (BUG-086)

Plan: `plans\inbox-20260830\10-fb-20260829-025502-251d.md`. Disposición **EVIDENCIA** (`:6`); `OWNS: tools/tests/test_wait_for.py; no parche productivo salvo nueva aprobación tras FAIL` (`:14`).
Criterio literal, `:35`: «PASS: caso de 200 satisface, caso de 201 no satisface y, **sobre los mismos bytes/reloj del caso 200**, `lookback_from=lines` con 0 no recupera el needle y devuelve `satisfied=false`; ambas firmas son 200 y marker/`lookback_from=launch` mantienen su precedencia documentada.»

### Criterios y evidencia

| Criterio | Veredicto | Evidencia |
|---|---|---|
| Caso de 200 satisface | MET | `tools\tests\test_wait_for.py:314-317` (`_WINDOW_CASES` fila `("EDGE-200", 199, True, 200)`) ejecutada por `test_window_edge_is_inclusive_at_two_hundred` (`:440`); veredicto de tabla literal, no derivado de los helpers |
| Caso de 201 no satisface | MET | Misma tabla, fila `("OUTSIDE-201", 200, False, 200)`; aserción `assertIs(result["satisfied"], satisfied)` en `:470` y `scanned["lines_total"] == 200` en `:475` |
| Arm `lookback_from="lines"` + `lookback_lines=0` no recupera el needle | MET (parcial) | `test_lookback_zero_reads_the_file_and_does_not_match` (`test_wait_for.py:401`), que además exige `probes>=1` y `scanned["lines_total"]>=1` con entrada `readable` (`:433-438`) para distinguir «leí y no casó» de «nunca abrí» |
| …**sobre los mismos bytes y el mismo reloj del caso de 200** (aislar que sólo cambia la ventana) | NOT MET | El arm de 0 usa un fixture distinto: `test_wait_for.py:406-408` escribe **una** línea (`needle + "\n"`) e inyecta una línea ajena en la segunda llamada de `lifecycle_status` (`:410-417`). El caso de 200 usa `guard-line + needle + 199 fillers` (`:447-449`). No comparten bytes, y ninguno de los tres inyecta reloj: usan `timeout_s`/`poll_interval_s` sobre el reloj real. **Dos lecturas**: (i) estricta — «mismos bytes/reloj» es literal, y cierra sólo si el arm de 0 se ejecuta sobre el fixture EDGE-200 con el mismo reloj fijado; (ii) laxa — basta que exista un arm que demuestre que 0 no recupera un needle previo, y entonces MET. **No elijo.** |
| Firma interna = 200 | MET | `tools\dayz_mcp\server.py:2235` (`lookback_lines: int = 200` en `execute_wait_for`) |
| Firma pública = 200 | MET | `tools\dayz_mcp\server.py:4414` (`lookback_lines: int = 200` en la tool `wait_for`) |
| Forwarding exacto al ejecutor | MET | `tools\dayz_mcp\server.py:4423-4433`: los nueve argumentos se reenvían uno a uno a `execute_wait_for` sin transformar |
| Punto 2 del plan: «verificar **aparte** firma interna, pública y forwarding» como caso de test | NOT MET | No hay ningún test que afirme los defaults ni el forwarding por firma. Grep de `lookback_lines` + `inspect`/`parameters`/`default` en `tools\tests\` → 0 hits. Sólo hay evidencia **conductual**: `test_the_public_tool_default_also_sees_the_earlier_response` (`test_wait_for.py:355`) llama la tool pública sin `lookback_lines` |
| Precedencia (i): marker después del needle impide recuperarlo con `launch` o 200 | MET | `test_marker_does_not_match_when_the_pattern_only_precedes_it` (`tools\tests\test_wait_for_marker.py:106`), con `marker=…`, `lookback_lines=server.WAIT_FOR_LOOKBACK_MAX`, `lookback_from="launch"` → `assertFalse(result["satisfied"])`. Es un control **más fuerte** que el pedido (2000 ≥ 200) |
| Precedencia (ii): sin marker, `launch` encuentra un needle posterior al inicio del run **aunque `lookback_lines=0`** | NOT MET | `test_max_lookback_cannot_reach_it_but_launch_can` (`tools\tests\test_wait_for_launch_and_contract.py:153`) prueba `launch` frente a `lookback_lines=WAIT_FOR_LOOKBACK_MAX`, pero llama a `_wait(profiles, lookback_from="launch")` **con el default 200**, no con 0. El arm `lookback_lines=0` + `launch` no existe en el árbol (grep `lookback_from` en `tools\tests\`: 20 hits, ninguno combina `launch` con `0`) |
| Punto 4: «ante FAIL, nueva propuesta; esta ficha no autoriza parche automático» | MET | `reports\2026-08-30-inbox-implementation\M22\bug086-evidence.json` → `"owns_respected": {"written": ["tools/tests/test_wait_for.py"], "productive_code_touched": []}` |

### Nota de autoridad — el informe M22 no es de esta ficha

`reports\2026-08-30-inbox-implementation\M22\bug086-evidence.json` empieza con `"ficha": "12-fb-20260829-030056-d73b"` y `"verdict_scope": "Cierra la ficha 12 (EVIDENCIA de BUG-086). NO cierra M22 como modulo"`, y añade `"why_not_inherited": "El PASS no se hereda de la ficha 251d."`. **Ese informe acredita la ficha 12, no la 10.** Por la regla de autoridad del DAG (`00-execution-dag.md:11-13`), la 10 necesita su propio bloque medido.

Dato colateral medido: ese mismo informe declaraba el 2026-09-03 que `LaunchReadinessProjection` estaba «ausente del arbol (grep -rl en tools/ -> 0 ficheros)». **Hoy existe**: `tools\dayz_mcp\dayz_test_tool.py:447`. El bloque `m22_module_blocked` está, en ese punto, obsoleto.

### Veredicto de ficha

**CIERRA CON TRABAJO**: (1) reejecutar el arm `lookback_from="lines", lookback_lines=0` sobre los **mismos bytes** del caso EDGE-200 y con reloj fijado, en `tools\tests\test_wait_for.py` (o resolver la ambigüedad de lectura); (2) añadir el control «sin marker, `lookback_from="launch"` con `lookback_lines=0` sí encuentra el needle posterior al inicio del run»; (3) opcionalmente, el caso de firmas/forwarding que pide el punto 2 del plan — hoy sólo lo acredita mi lectura del código, no un test.

---

## Ficha 23 — fb-20260829-135727-782b — M09-KNOWLEDGE → M22 → M25

Plan: `plans\inbox-20260830\23-fb-20260829-135727-782b.md`.
Criterio literal, `:47`: «PASS: M09 distingue los dos ejes `missing\|invalid\|valid`, completa las nueve celdas y publica/repara atómicamente con los códigos y conflictos previstos mediante llamadas válidas `{}`. **Tras M09, M22 materializa schema 0-arg, wrapper raw-args y `additionalProperties=false`; `app.list_tools`, llamadas in-process con `{}`/extras y los dos negativos stdio devuelven `CallToolResult.isError is True` con `bad_args: unexpected arguments`.** En concurrencia el ganador deja índice válido, el perdedor devuelve `knowledge_prepare_conflict` y no toca su candidate; el arm de fallo conserva el índice previo byte-idéntico.»

### Criterios y evidencia

| Criterio | Veredicto | Evidencia |
|---|---|---|
| Dos operaciones públicas de cero argumentos | MET | `tools\dayz_mcp\knowledge.py:656` (`def dayz_knowledge_status()`) y `:665` (`def dayz_knowledge_prepare()`). Sonda: ambas registradas, 60 tools |
| Dos ejes independientes `index_state`/`pack_state` + `can_query`/`can_prepare` | MET | `tools\dayz_mcp\knowledge.py:533` `_index_state`, `:543` `_pack_state`, `:561-567` el payload. Test `test_status_reports_independent_index_and_pack_states_read_only` (`tools\tests\test_knowledge_tools.py:191`) |
| Matriz 3×3 completa, nueve celdas con veredicto | MET | Test `test_status_covers_the_complete_independent_three_by_three_matrix` (`tools\tests\test_knowledge_tools.py:282`) |
| `prepare` nunca llama a `ensure_pack()`/git/red | MET | `tools\dayz_mcp\knowledge.py:666-673`: sólo `knowledge_pack.resolve_pack_dir()` y `extract_pack(pack_path)`. Grep de `ensure_pack` dentro del registro de tools → 0 hits |
| Validador fuerte de entries antes de publicar | MET | `validate_index` invocado en `knowledge.py:676`; test `test_strong_validator_rejects_structural_mutants` (`test_knowledge_tools.py:546`) |
| Lock OS exclusivo no bloqueante sobre anchor sibling `<index>.prepare.lock` | MET | `tools\dayz_mcp\knowledge.py:491` `_exclusive_prepare_lock`, `:511` `msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)`, `:578` `lock_path = Path(f"{path}.prepare.lock")`. Tests `test_prepare_lock_rejects_existing_non_regular_anchor_before_open`, `test_prepare_lock_repins_identity_after_acquiring_lock` (`tools\tests\test_knowledge_pack_install.py:430,445`) |
| Candidate create-exclusive `<index>.candidate.<txid>` con `O_EXCL` | MET | `tools\dayz_mcp\knowledge.py:582-583` (`candidate = Path(f"{path}.candidate.{txid}")`, `flags = os.O_WRONLY \| os.O_CREAT \| os.O_EXCL`). Test `test_prepare_candidate_collision_is_a_conflict_without_replacing_index` (`test_knowledge_tools.py:513`) |
| Códigos exactos `knowledge_pack_missing` / `knowledge_pack_invalid` / `knowledge_prepare_conflict` | MET | `tools\dayz_mcp\knowledge.py:669-682`. Tests `test_prepare_missing_pack_preserves_existing_index` (`:403`), `test_prepare_invalid_pack_preserves_existing_index` (`:427`) |
| `knowledge_index_invalid` / `knowledge_not_installed` con la nueva receta sin shell | MET | `tools\dayz_mcp\knowledge.py:36` (`KNOWLEDGE_REMEDY = "call dayz_knowledge_status, then dayz_knowledge_prepare"`), `:38`. Test `test_find_and_show_return_exact_invalid_index_remedy` (`:385`) |
| Concurrencia de dos procesos Windows; el perdedor no borra el candidate ajeno | MET | Test `test_prepare_operations_are_exclusive_and_atomic_between_windows_processes` (`tools\tests\test_knowledge_pack_install.py:353`) y `test_prepare_preserves_foreign_candidate_and_cleans_only_own_on_replace_failure` (`test_knowledge_tools.py:481`). El informe `reports\...\M09\m09-test-run-windows.json` declara `"concurrency_flake_check": "3x OK"` |
| Arm de fallo antes del replace conserva el índice byte-idéntico | MET | Tests `test_invalid_extracted_index_is_rejected_without_replacing_previous_bytes` (`:254`) y `test_prepare_extractor_failure_is_rejected_without_replacing_index` (`:453`) |
| **M22: schema 0-arg con `additionalProperties=false` y `required=[]`** | NOT MET | Sonda: `dayz_knowledge_prepare` → `{"properties": {}, "title": "…", "type": "object"}`. No hay `additionalProperties` ni `required` |
| **M22: wrapper raw-args que devuelve `bad_args: unexpected arguments`** | NOT MET | Grep de `unexpected arguments` en todo `tools\**\*.py` → **0 hits**. El único uso del patrón `_patch_public_argument_alias` (`tools\dayz_mcp\server.py:1718-1731`) es `scene_raycast` (`:4557`) |
| **M22: llamada in-process con extras falla cerrada** | NOT MET (**medido fallando**) | Sonda: `await app.call_tool("dayz_knowledge_status", {"repo":"Y"})` → **sin error**, devuelve el payload de status. `await app.call_tool("dayz_knowledge_prepare", {"path":"X"})` → `ToolError … knowledge_pack_missing`, es decir el extra se ignoró y la tool **ejecutó**. Esto es literalmente el FAIL de `:48`: «schema 0-arg que ignora extras después de M22» |
| **M22: dos negativos por stdio (`ClientSession.call_tool`) con `isError is True`** | NOT MET | Grep de `dayz_knowledge_prepare`/`dayz_knowledge_status` en `tools\tests\test_mcp_tools.py` → 0 hits. No existe el par stdio que pide `:36` |
| `expected_tool_names` de M13 incluye las dos altas junto con `dayz_effective_schema` (plan `:29`) | NOT MET | `tools\tests\test_effective_schema_catalog.py:26,40,54,68` y `tools\tests\fixtures\effective_schema_v5\profile_inventory.json` listan `dayz_effective_schema`, pero la sonda de `app.list_tools()` mide **60 tools y ninguna con la subcadena `schema`**. Las cuatro parejas del inventario no describen la superficie viva |

### Nota de provenance

`reports\2026-08-30-inbox-implementation\M09\m09-owns.sha256` fija `tools/dayz_mcp/knowledge.py` en `087146b0a8ef53c63804c496437c504f8c07898cf858b522151d45e34aec3ea3`; el fichero vivo tiene **exactamente** ese hash. La entrega M09 está anclada.

### Veredicto de ficha

**CIERRA CON TRABAJO**: la mitad M09 está completa y medida; falta toda la materialización M22 — (1) schema `properties={}`, `required=[]`, `additionalProperties=false` para ambas tools; (2) el wrapper raw-args análogo a `_patch_public_argument_alias` (`tools\dayz_mcp\server.py:1718`) que devuelva `bad_args: unexpected arguments`; (3) el gate `app.list_tools` + in-process con `{}`/extras; (4) los dos negativos por stdio en `tools\tests\test_mcp_tools.py`. Hoy hay una condición **medida en FAIL**: los extras se aceptan y la tool ejecuta.

---

## Ficha 26 — fb-20260829-194752-d366 — M13 núcleo → M22 → M23-FINAL-SCHEMA-PROMOTION

Plan: `plans\inbox-20260830\26-fb-20260829-194752-d366.md`.
`OWNS` (`:15`): «M23 posee `tools/promote_effective_schema.py`, `tools/tests/test_effective_schema_promotion.py`, la raíz física `reports/2026-08-30-inbox-implementation/M23/` con sus cinco canónicos/candidates/backups/journals/lock y el temporal nuevo `%TEMP%/dayz-mcp-schema-calibration/<txid>`».
El criterio PASS (`:47`, párrafo único) describe **exclusivamente la corrida de promoción**: banco fijo, calibración desechable, candidates, CAS pre-replace, `os.replace` ordenado, receipts, rollback y recovery.

### Criterios y evidencia

| Criterio | Veredicto | Evidencia |
|---|---|---|
| Existe el promotor `tools/promote_effective_schema.py` | NOT MET | `ls` → *No such file or directory* |
| Existe su test `tools/tests/test_effective_schema_promotion.py` | NOT MET | `ls` → *No such file or directory* |
| Existe la raíz física `reports/2026-08-30-inbox-implementation/M23/` | NOT MET | `ls` → *No such file or directory*. Ninguno de los cinco canónicos (`dayz-effective-schema-v1.json`, `tool-registry-fingerprint-v1.sha256`, `effective-schema-v5-verdict.json`, `effective-schema-producers-v1.json`, `m23-receipts.jsonl`) existe |
| Banco fijo M13 de cinco miembros legible y hasheable | MET (parcial) | Los cinco existen: `tools\tests\fixtures\effective_schema_v1\required_constraint_ids.json` (398 B) y `tools\tests\fixtures\effective_schema_v5\{instructions_required_concepts.json, mutation_cases.json, profile_inventory.json, validator_cases.json}` |
| Núcleo M13 (extractor, catálogo, validadores) construido y verde | MET | `tools\dayz_mcp\effective_schema.py`, `effective_schema_core.py`, `effective_schema_catalog.py`, `effective_schema_runtime_validators.py`. Mi corrida: los cuatro módulos de test correspondientes van dentro de los `Ran 72 tests ... OK` |
| Paso 3: «construir una app M22 limpia … e **invocar `dayz_effective_schema`**» | NOT MET | Sonda: `dayz_effective_schema` → **NOT REGISTERED**; 60 tools, ninguna con `schema` en el nombre. Grep de `dayz_effective_schema` en `tools\**\*.py`: sólo 4 hits, todos en `tools\tests\test_effective_schema_catalog.py` (arrays esperados). La igualdad wire↔candidate del paso 3 es hoy **inejecutable** |
| `closure-manifest` con `dayz_test_modes_sha256` (pasos 1, 5, 7) | NOT MET | Grep de `dayz_test_modes_sha256` en `tools\` (`*.py`, `*.json`) → **0 hits**. `tools\dayz_mcp\native_bundle.py:46-57` (`_MANIFEST_KEYS`) y `:58-63` (`_HASHED_MODULES`) sólo llevan `dayz_test_readiness_sha256`, `dayz_test_request_sha256`, `dayz_test_worker_sha256` y `native_broker_protocol_sha256`. El plan `:47` lo declara FAIL: «aceptar manifest sin hash M12 o distinto» |
| `dayz_test_modes.py` como miembro ZIP del bundle | MET | `tools\build_native_launcher.py:57` y `tools\dayz_mcp\native_bundle.py:70` (`_APP_PACKAGED_MODULES`) |
| Calibración LL-378 bajo `%TEMP%/dayz-mcp-schema-calibration/<txid>` create-only | NOT MET | No hay promotor que la ejecute |
| CAS pre-replace, journal, backups create-only, lock OS, recovery, rollback, dos promociones + receipts monotónicos | NOT MET | Sin promotor ni raíz M23, ninguna de estas condiciones tiene sujeto |
| Provenance `anchored` y única transacción M20 activa | NEEDS AUTHORITY | El paso 1 exige `open_approved_launcher("dayz-test-v1")` + `load_verified_bundle(opened)` y una única aprobación M20 activa. No existe `reports\...\M20\` ni informe de esa transacción |

### Nota de drift

`reports\2026-08-30-inbox-implementation\M13\m13-owns.json` fija `tools/dayz_mcp/effective_schema.py` en sha256 `36fdcc00…`/8755 B. Ese hash y ese tamaño corresponden hoy a **`tools/dayz_mcp/effective_schema_core.py`**; el `effective_schema.py` vivo es `26ae37cb…`/10503 B. Hubo un renombrado/refactor posterior al informe M13 (lote M14, 2026-09-02). El informe M13 no describe el árbol vivo.

### Veredicto de ficha

**BLOQUEADA**: M23 no existe en ninguna forma — ni promotor, ni test, ni raíz física, ni canónicos. Además su paso 3 es **inejecutable** porque la tool `dayz_effective_schema` no está registrada, y el paso 1/5/7 exige un `dayz_test_modes_sha256` en el closure-manifest que no existe. **NEEDS AUTHORITY**: falta la transacción M20 activa (`prepared.json`/`committed.json`, registry+bundle anclados) sobre la que el promotor debe medir provenance; sin ella el propio plan clasifica INCONCLUSIVE (`:47`). Cero criterios de promoción medidos.

---

## Ficha 27 — fb-20260829-194823-ffc7 — M12 → M18 → M19 → M20 → M22 (M21 read-only)

Plan: `plans\inbox-20260830\27-fb-20260829-194823-ffc7.md`. Su `OWNS` (`:15`) cubre M18 **y** M19 **y** M20 **y** la parte M22; el enunciado del encargo la sitúa en M18, pero el criterio PASS (`:39`) atraviesa los cuatro boundaries. Audito los cuatro.

### Criterios y evidencia

| Criterio | Veredicto | Evidencia |
|---|---|---|
| M18 importa el **módulo** M12 (no valores) y toma una vista viva por parse | MET | `tools\dayz_mcp\dayz_test_request.py:11` (`from . import dayz_test_modes`), `:67-72` (`_request_mode_view` llama `mode_records()`/`resolve_default_mode()`/`request_mode_names()` en cada invocación), `:276` (invocado dentro de `parse_dayz_test_request`) |
| Dos parses separados observan sustitución de default y de visibilidad; cero/múltiples defaults fallan | MET | Test `test_request_uses_current_mode_authority_for_each_parse` (`tools\tests\test_dayz_test_request.py:12`): omisión raw→`all` (`:55-58`), sustituido→`server` (`:61-64`), `client` invisible→rechazo (`:65-66`), sin default→error (`:67-69`), doble default→error (`:70-72`) |
| Tokens M18 cerrados `invalid_run_id` / `client_requires_run_id` / `server_all_forbid_run_id` | MET | `tools\dayz_mcp\dayz_test_request.py:41-43`; test `test_request_emits_closed_run_id_tokens_before_generic_validation` (`test_dayz_test_request.py:76`) |
| Traducción externa a `bad_run_id` / `bad_dayz_test_request:<token>` | MET | `tools\dayz_mcp\server.py:192-193` (mapa `invalid_dayz_test_request→bad_dayz_test_request`, `invalid_run_id→bad_run_id`) y `tools\dayz_mcp\dayz_test_tool.py:181,186-187,208` |
| Los tres códigos coinciden en parser, fachada y `app.call_tool`, en normal y preflight | MET | Test `test_dayz_test_run_names_run_id_matrix_causes_on_the_wire` (`tools\tests\test_mcp_tools.py:738`): 4 filas × `preflight in (False, True)`, cada una por `await app.call_tool("dayz_test_run", args)` (`:795`), asertando el token y `assertNotIn("dayz_test_failed", message)` (`:798`) |
| **M19 sin `_PUBLIC_MODES`, `_ACCEPTED_MODES` ni membership/texto local** | NOT MET | `tools\dayz_mcp\dayz_test_tool.py:20` `_PUBLIC_MODES = frozenset({"server", "all", "client"})` y `:21` `_ACCEPTED_MODES = _PUBLIC_MODES \| {"offline"}`, usados en `:136` y `:702`. Además texto local `"bad_dayz_test_request:mode expected server\|all\|client"` en `:137` y `:703`. FAIL literal de `:40` |
| **M19 sin `public_mode in {...}`** | NOT MET | `tools\dayz_mcp\dayz_test_tool.py:649` `if public_mode in {"client", "all"}:` y `:657` `if public_mode in {"all", "offline"} …`; `:729` `if not preflight and mode in {"client", "all"}:` |
| (agravante) el texto local de modos está **congelado por un test** | NOT MET | `tools\tests\test_dayz_test_tool.py:1302` `test_run_rejects_public_offline_mode_with_expected_enum` asierta literalmente `caught.exception.code == "bad_dayz_test_request:mode expected server\|all\|client"`. Retirar el literal de M19 exige tocar también este test, que hoy lo blinda |
| M19 consulta el record/vista viva M12 en cada llamada | NOT MET | Grep de `dayz_test_modes` en `tools\dayz_mcp\dayz_test_tool.py` → 0 hits: no importa la autoridad |
| **M20 sin `mode in {"client","offline"}`; pasos/adopt/raíces derivados del record M12** | NOT MET | `tools\dayz_mcp\dayz_test_worker.py:592` `if mode in {"client", "offline"} and run_id is not None:`. Grep de `dayz_test_modes` en ese fichero → 0 hits. FAIL literal de `:40`: «M20 conserva `frozenset`, `mode in {"client","offline"}`…» |
| El record M12 define los pasos exigidos (`all=start→readiness→start`, `server=start`, `client=adopt(required)→start`, `offline=adopt(optional)→start`) | MET (lado M12) | `tools\dayz_mcp\dayz_test_modes.py:100-144`: los cuatro `ModeRecord` con `steps`, `artifact_roots`, `starts_client`, `default_when_omitted` exactamente como pide el plan |
| Fixture externa observa `['all','server','client']` por app **y** por stdio | NOT MET | Sonda: `dayz_test_run.mode` es `{"title":"Mode","type":"string"}` — **sin enum**. La lista de modos aceptados no es observable en el wire por ninguna de las dos vías |
| `offline` permanece interno (no público) | MET | `tools\dayz_mcp\dayz_test_modes.py:137` (`public=False` en el record `offline`); sonda: no aparece en ningún enum wire (no hay enum) |
| Módulo M12 en **ambas** allowlists del launcher | MET | `tools\build_native_launcher.py:57` y `tools\dayz_mcp\native_bundle.py:70` |
| Hash del módulo M12 en el manifest | NOT MET | Grep `dayz_test_modes_sha256` en `tools\` → 0 hits; `native_bundle.py:46-63` no lo contempla |
| Test de integración M22 `tools/tests/test_effective_schema_integration.py` (una única edición compartida ffc7/9d46) | NOT MET | `ls tools\tests\test_effective_schema*.py` → sólo `test_effective_schema.py`, `test_effective_schema_catalog.py`, `test_effective_schema_core.py`, `test_effective_schema_runtime_validators.py`. **El fichero de integración no existe** |
| Mutantes por boundary (visibilidad/record/permutación) que enrojecen su gate | NOT MET | Existen para M12 y M18 (`test_dayz_test_modes.py`, `test_dayz_test_request.py:12`); no existen para M19/M20 porque esos boundaries no consultan la autoridad |
| Entrega externa M21 disponible | NEEDS DECISION | `reports\...\M21\` existe, pero su `m21-owns.json` congela `tools/dayz_mcp/dayz_test_tool.py` en sha256 `c783fb51…`/23840 B, y el fichero vivo es `46de4e01604010b515c3e4520db64b7d8538636fb33743ff9a3d6443b4adb4c9`/28751 B (mtime 2026-09-03 18:21). El plan `:15` dice que M21 «no autoriza reabrir `dayz_test_tool.py` … después de M19». Hay drift sobre un path que la ficha declara predecesor read-only: **quién autorizó ese cambio y bajo qué módulo es una decisión del dueño del DAG** |

### Veredicto de ficha

**CIERRA CON TRABAJO** (lista larga, no corta): la mitad M18 está entregada y medida; **M19 y M20 no están entregados** en lo que esta ficha exige. Falta: (1) retirar `_PUBLIC_MODES`/`_ACCEPTED_MODES` y los literales de modo de `tools\dayz_mcp\dayz_test_tool.py:20-21,136-137,649,657,702-703,729` y hacer que cada llamada consulte el record M12; (2) retirar `mode in {"client","offline"}` de `tools\dayz_mcp\dayz_test_worker.py:592` y derivar adopt/dispatch/readiness/raíces del record; (3) publicar el conjunto de modos en el wire para que la fixture externa lo observe por app y por stdio; (4) añadir `dayz_test_modes_sha256` al manifest (`tools\dayz_mcp\native_bundle.py:46-63`) y reaprobar el bundle; (5) crear `tools\tests\test_effective_schema_integration.py`; (6) crear `reports\...\M18\` y `M19\`/`M20\` con su medición. **NEEDS DECISION** aparte: el drift de `dayz_test_tool.py` respecto al congelado M21.

---

## Ficha 31 — fb-20260830-010517-9d46 — M12-MODE-AUTHORITY (+ M20, M22)

Plan: `plans\inbox-20260830\31-fb-20260830-010517-9d46.md`. `OWNS` (`:15`): M12 + M20 + la parte M22; consume M18/M19 **read-only**.
Criterio literal, `:39`: «PASS: ffc7 entrega la materialización y gates directos M18/M19 … Sin editar esos paths, M22 ejecuta las celdas públicas alcanzables con `mode` presente … `dayz_test_run` sin `mode` se rechaza antes de façade/launcher, un extra se ignora sin alterar el resultado y `dayz_test_stop` acredita el kill interno `offline`+UUID. M20 obtiene membership, pasos, fuentes de ID, raíces y arranca-cliente directamente del record M12 … La fixture observa `['all', 'server', 'client']` por app/stdio …»

### Criterios y evidencia

| Criterio | Veredicto | Evidencia |
|---|---|---|
| Tabla M12 ordenada: visibilidad, fase, run_id, dispatch, raíces, arranca-cliente | MET | `tools\dayz_mcp\dayz_test_modes.py:100-144` (los cuatro `ModeRecord` completos); `:58` (`default_when_omitted: bool`) |
| Exactamente un `default_when_omitted`; cero o varios fallan cerrado | MET | `tools\dayz_mcp\dayz_test_modes.py:205-210`; test `test_default_resolution_rejects_zero_or_multiple_defaults` (`tools\tests\test_dayz_test_modes.py`) |
| La vista se lee de los records **suministrados en cada llamada**; nada se cachea | MET | Tests `test_views_are_read_from_supplied_records_each_call`, `test_module_record_view_can_be_replaced_between_calls`, `test_supplied_view_replaces_order_steps_roots_and_run_id_source` (los tres en `tools\tests\test_dayz_test_modes.py`; medidos verdes: `Ran 8 tests ... OK` en `m12-test-output.txt` y en mi corrida conjunta) |
| Nombres duplicados / record inválido → fail-closed | MET | `tools\dayz_mcp\dayz_test_modes.py:148-166` (`_validated_view`); test `test_supplied_view_with_duplicate_names_fails_closed_everywhere` |
| Steps inmutables y tipados | MET | `tools\dayz_mcp\dayz_test_modes.py:104-112` (factories `start`/`readiness`/`adopt_supplied`); test `test_step_factories_produce_immutable_typed_steps` |
| M22: celdas públicas `all/server`+UUID, `client`+null, UUID malformado, en normal y preflight | MET | Test `test_dayz_test_run_names_run_id_matrix_causes_on_the_wire` (`tools\tests\test_mcp_tools.py:738-798`) |
| `dayz_test_run` **sin `mode`** se rechaza antes de fachada/launcher | MET | Sonda: `dayz_test_run.required == ['project','mode']` — FastMCP lo rechaza en validación de argumentos, antes de entrar al cuerpo |
| `kill` **no** expuesto como argumento de `dayz_test_run` (FAIL de `:40`) | MET | Sonda: `"kill" in properties` → `False` |
| `dayz_test_stop` acredita el kill interno `offline`+UUID | NOT MET | Sonda: `dayz_test_stop` → `required: ["run_id"]`. La ruta interna `offline` existe y está probada — `tools\tests\test_dayz_test_tool.py:79-95` (`build_run_request(..., mode="offline")` → `parsed.payload["mode"] == "offline"`) — pero contra el **literal local**, no contra el record M12. Y `tools\tests\test_dayz_test_tool.py:1302` congela el rechazo público de `offline` con el texto `"bad_dayz_test_request:mode expected server\|all\|client"`, que es exactamente el hardcode que la ficha prohíbe |
| **M20 obtiene membership/pasos/fuentes de ID/raíces del record M12** | NOT MET | `tools\dayz_mcp\dayz_test_worker.py:592` `if mode in {"client", "offline"} and run_id is not None:`; grep de `dayz_test_modes` en ese fichero → 0 hits. FAIL literal de `:40` |
| Sustituir el record M12 cambia el trace real y deja rojo el hardcode | NOT MET | Imposible: el worker no lee la autoridad. No hay test de sustitución que alcance el worker |
| Fixture observa `['all','server','client']` por app **y** por stdio | NOT MET | Sonda: `mode` es `{"type":"string"}` sin enum; no hay tool que publique la lista (`dayz_effective_schema` NOT REGISTERED) |
| Módulo M12 en manifest/bundle con su hash y reaprobación no manual | NOT MET (hash) / MET (miembro) | Miembro: `tools\build_native_launcher.py:57`, `tools\dayz_mcp\native_bundle.py:70`. Hash: `dayz_test_modes_sha256` → 0 hits en todo `tools\` |
| «ffc7 entrega la materialización y gates directos M18/M19» (precondición de esta ficha) | NOT MET | Ver ficha 27: M19 conserva `_PUBLIC_MODES`/`_ACCEPTED_MODES` (`dayz_test_tool.py:20-21`). El plan `:41` lo clasifica INCONCLUSIVE: «la materialización/receipt M18/M19 de ffc7 no existe o deriva» |
| `tools/tests/test_effective_schema_integration.py` (edición única compartida) | NOT MET | No existe |
| 9d46 no crea ni modifica paths M18/M19 (FAIL de `:40`) | MET | `reports\...\M12\m12-owns.json` sólo lista `dayz_test_modes.py` + su test; el hash vivo de `dayz_test_modes.py` es `b654f8fd…` y el módulo está aislado |

### Veredicto de ficha

**CIERRA CON TRABAJO**: M12 —el módulo que la ficha nombra— está **completo y medido** (`Ran 8 tests ... OK`, verificado por mí dentro de la corrida de 72). Lo que falta está fuera de M12: (1) M20 leyendo el record M12 en `tools\dayz_mcp\dayz_test_worker.py:592`; (2) el conjunto de modos observable en el wire por app y stdio; (3) `dayz_test_modes_sha256` en `tools\dayz_mcp\native_bundle.py:46-63` + reaprobación del bundle; (4) `tools\tests\test_effective_schema_integration.py`; (5) su precondición declarada — la materialización M18/M19 de ffc7 — que hoy no está (ficha 27).

---

## Resumen

| Ficha | Veredicto | Lo que falta (una línea) |
|---|---|---|
| 03 — 268a — M07 | CIERRA CON TRABAJO | La mitad M22: `crop_space` en `server.py:3938-3948`/`:3997-4010`, bloque JSON también con `save_fullres=False` (`:4027-4029`), y el test público por `app.call_tool` |
| 07 — 9b7b — M14 | **BLOQUEADA** | La autoridad `reports\...\M23\dayz-effective-schema-v1.json` no existe y `server.py:574-584` la pasa toda a `None`: nadie puede quedar `stale`/`fresh`; además `server.py:561-562,564` son fail-open |
| 08 — c7ca — M08 | CIERRA CON TRABAJO | `evidence_ref` en la tool pública `pipeline_resolve` (`server.py:4516-4527`) y su round-trip; hoy se cumple el FAIL literal del plan `:39` |
| 10 — 251d — M22 | CIERRA CON TRABAJO | El arm `lookback_lines=0` sobre los **mismos bytes/reloj** del caso EDGE-200, y el control «`launch` con `lookback_lines=0` sí encuentra», en `tools\tests\test_wait_for.py` |
| 23 — 782b — M09 | CIERRA CON TRABAJO | La mitad M22: `additionalProperties=false`, wrapper raw-args con `bad_args: unexpected arguments`, y los dos negativos stdio — hoy **medido**: los extras se aceptan y la tool ejecuta |
| 26 — d366 — M13/M23 | **BLOQUEADA** | M23 no existe (ni promotor, ni test, ni raíz, ni canónicos); `dayz_effective_schema` NOT REGISTERED hace inejecutable el paso 3; falta `dayz_test_modes_sha256` |
| 27 — ffc7 — M18(+M19/M20) | CIERRA CON TRABAJO (lista larga) | M18 está; M19 y M20 no: `_PUBLIC_MODES`/`_ACCEPTED_MODES` (`dayz_test_tool.py:20-21`) y `mode in {"client","offline"}` (`dayz_test_worker.py:592`) siguen ahí, y falta `test_effective_schema_integration.py` |
| 31 — 9d46 — M12 | CIERRA CON TRABAJO | M12 está completo y medido; falta M20 leyendo el record, el enum de modos en el wire, `dayz_test_modes_sha256` y la precondición M18/M19 de ffc7 |

**Cierran hoy: 0 de 8.**

---

## LO QUE NO PUDE VERIFICAR

1. **Condiciones (a), (c), (d) y (e) del criterio I7** (`plans\2026-09-03-...:272`). Por instrucción no toqué git ni MCP: no verifiqué commits atribuibles (4567fe3, 492dd76, 48298d1), ni que el `app.pyz` desplegado arranque (`G-PYZ`), ni la revisión de otra familia con repro-o-backlog, ni ningún `pipeline_resolve`. **Ninguna de las 8 fichas puede declararse cerrada sin esas cuatro**, y yo sólo medí la dimensión sustantiva.
2. **`G-BASE`**: no corrí la suite completa (~2.300 tests). No puedo afirmar que la identidad cerrada de los rojos siga siendo la baseline (`Ran 2268 tests, FAILED (failures=2, skipped=6)`, los 2 del centinela de `MCPBridge.c`). Sólo medí 8 módulos concretos, todos `OK`.
3. **`G-REPRO` de la ficha 10**: no ejecuté los mutantes de `server.py` de la tabla de `bug086-evidence.json`. Habría requerido mutar código productivo; la ficha 10 es EVIDENCIA y su `OWNS` prohíbe parche productivo. Acepto la tabla de mutantes de ese informe **como declaración**, no como medición mía — y esa tabla es de la ficha **12**, no de la 10.
4. **Control positivo por test** (regla 3 de `:270`): no corrí ningún test nuevo *sin* su arreglo. Todos mis MET de tests dicen «el test existe y pasa», no «el test estuvo rojo antes». Para las fichas 03, 08, 09 y 12(M18) eso deja abierta la posibilidad de un test tautológico que yo no detectaría.
5. **Provenance por `git status`** (regla 1 de `:270`): no pude comprobar que el árbol esté limpio ni que los cambios estén en la rama `work/inbox-20260830-modules`. Usé sha256 de ficheros contra los `*-owns.json/sha256` como sustituto parcial: M08 y M09 anclan; M07, M13 y M21 **han derivado** de su informe.
6. **Comportamiento por stdio**: todas mis sondas son in-process (`build_app` + `app.list_tools` + `app.call_tool`). No levanté un `ClientSession` por stdio, que es lo que las fichas 23, 26, 27 y 31 exigen explícitamente. Si FastMCP se comportara distinto por stdio (no tengo motivo para creerlo), mis NOT MET de schema podrían matizarse — pero no los MET.
7. **`_status()` con pack real**: mi sonda de `dayz_knowledge_status` corrió con `index_state=missing, pack_state=missing` (no hay pack instalado en este entorno). Verifiqué la matriz 3×3 por los tests, no por observación directa de las nueve celdas.
8. **`dayz_test_stop` + kill interno `offline`+UUID** (ficha 31): no leí entero `tools\tests\test_dayz_test_tool.py` (43 KB); busqué por `offline` (13 hits) y por nombres de test. Confirmé la firma wire y que la ruta `offline` está probada contra el literal local, no contra el record M12, pero no ejecuté ese módulo de tests ni tracé el flujo completo de `dayz_test_stop` hasta el worker.
    **No corrí `tests.test_dayz_test_tool` ni `tests.test_dayz_test_worker`**: los NOT MET de M19/M20 los sostengo por `path:line` del código (`dayz_test_tool.py:20-21,649,657,702-703,729`; `dayz_test_worker.py:592`) y por ausencia de import de `dayz_test_modes`, no por una corrida.
9. **Contenido de `m07-implementation.json`, `m12/m13/m14/m21-implementation.json`**: los leí sólo en sus bloques de `owns` y `test-run`. No audité sus narrativas de implementación punto por punto contra el árbol; para las fichas 03, 26, 27 y 31 eso significa que pude pasar por alto una entrega parcial declarada allí.
10. **Ambigüedades que dejo abiertas, sin elegir**: (i) ficha 03, «`capture_dual` recibe y aplica `crop_space`» leído como helper vs extremo a extremo; (ii) ficha 07, `:31` («relectura en cada `bridge_status`») contra `:32` («snapshot inmutable»); (iii) ficha 10, «mismos bytes/reloj del caso 200» leído estricta o laxamente. Las tres necesitan que el dueño del plan diga cuál vale.
