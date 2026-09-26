# Addendum de propiedad física — revisión v2, graph `inbox-20260831-v6`

Fecha: 2026-08-31  
Estado: autoridad de planificación; pendiente de revisión única Grok 4.6 Medium mediante Cursor  
Ámbito: módulos M00–M25 del cierre de 35 feedbacks

## Identidades de raíz

- [EXACT] `subst` devuelve `P:\ = C:\Users\guill\OneDrive\Documentos\DayZ Projects` en esta
  máquina. Por tanto `P:\DayZ_MCP_dev` y
  `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev` son el mismo árbol físico.
- [EXACT] La API Win32 ya enlazada en el repo expone `GetFileInformationByHandleEx` y
  `GetFinalPathNameByHandleW` en `tools/dayz_mcp/win32_fileinfo.py:43-56`; M00 puede registrar ruta
  final, volumen y file-id sin introducir una API inventada.
- [EXACT] El inbox productivo deriva de `%LOCALAPPDATA%\DayZ_MCP\inbox\feedback.jsonl` en
  `tools/dayz_mcp/inbox.py:11-12` y sólo se modifica por append en `:28-40`.

Las abreviaturas siguientes son **constantes literales**, no variables de entorno. M00 debe
expandirlas en el JSONL y nunca persistir la abreviatura:

- `R = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev`
- `Q = C:\Users\guill\OneDrive\Documentos\DayZ Projects`
- `U = C:\Users\guill`
- `T = C:\Users\guill\AppData\Local\Temp`

## Regla de exclusión

1. [DESIGN] Cada entrada M00 contiene módulo, intención `read|create|modify|append|publish`, path
   absoluto, path final por handle, volumen, file-id cuando ya existe, SHA-256/tamaño y estado git.
2. [DESIGN] Para targets nuevos se acredita el padre por handle y se reserva nombre completo. Un
   directorio marcado `recursive-build-output` reserva exactamente ese árbol durante la ventana.
3. [DESIGN] Solape por mismo file-id, mismo path final case-folded, fichero dentro de directorio
   reservado o dos targets nuevos con mismo padre+nombre = conflicto. Sólo una arista explícita
   permite serializarlo.
4. [DESIGN] M00 captura A, espera a que todos los writers de plan/review estén quiescentes, captura B
   y exige A=B byte a byte. Salidas: 0=adoptado; 1=drift/policy; 3=setup inconcluso. Un write-set no
   inventariado después de A=B bloquea el módulo.

## Write-set físico cerrado

Los sufijos bajo `R`/`Q` se expanden por sustitución literal antes de comparar. `read` no autoriza
mutación; una ruta no listada requiere replan y reinicia sus gates.

| Módulo | Intención | Paths físicos cerrados |
|---|---|---|
| M00 | create | `R\reports\2026-08-30-inbox-implementation\M00\m00-git-status-v2.raw`; `R\reports\2026-08-30-inbox-implementation\M00\m00-git-inventory-v2.jsonl`; `R\reports\2026-08-30-inbox-implementation\M00\m00-owns-baseline.sha256`; `R\reports\2026-08-30-inbox-implementation\M00\m00-dirty-audit.json` |
| M00 | read | todos los targets existentes y padres de targets nuevos enumerados en esta tabla; `R\tools\tests\test_key_press.py`; `R\tools\tests\test_player_respawn.py`. Los recibos de 3bb4/7743/1082 viven dentro de los cuatro informes anteriores; no se crea un quinto artefacto |
| M01 | create/modify | `R\product-spec.md`; `R\plans\2026-08-30-pipeline-inbox-closure-design.md`; `R\plans\inbox-20260830\00-execution-dag.md`; este addendum; `R\plans\inbox-20260830\authority-bundle-v7.sha256`; `R\plans\inbox-20260830\plan-manifest.sha256`; `R\reports\2026-08-31-reviewer-transition-grok-cursor.md`; `R\GATES.md`; las 35 hojas gate y los 35 planes enumerados en «Set M01/M25»; `R\reviews\2026-08-31-inbox-plan-grok-v1` como `recursive-build-output`, incluido `grok-plan-review-manifest.json`; los tokens de attestation de planificación definidos en esa sección |
| M02 | modify/create | `R\addon\scripts\5_Mission\MCPMessages.c`; `R\tools\tests\test_messages_contract.py` |
| M03 | modify/create | `R\addon\scripts\5_Mission\MCPBridge.c`; `R\tools\tests\test_entities_has_cargo.py`; `R\tools\tests\test_bridge_server_capabilities.py` |
| M04 | modify/create | `R\addon\scripts\5_Mission\MCPClientBridge.c`; `R\tools\tests\test_vehicle_telemetry_contract.py` |
| M05 | modify/create | `R\addon\scripts\5_Mission\MCPClientBridge.c`; `R\tools\tests\test_ui_click_scriptview.py`; `R\tools\tests\test_ui_enforce_contract.py`; `R\tools\tests\test_bridge_client_capabilities.py` |
| M06 | modify | `R\tools\dayz_mcp\loopback.py`; `R\tools\tests\test_loopback.py`; `R\tools\tests\test_validate_command_args_table.py` |
| M07 | modify | `R\tools\mcp_capture.py`; `R\tools\tests\test_mcp_capture.py` |
| M08 | modify | `R\tools\dayz_mcp\inbox.py`; `R\tools\tests\test_pipeline_feedback.py` |
| M09 | modify | `R\tools\dayz_mcp\knowledge.py`; `R\tools\tests\test_knowledge_tools.py`; `R\tools\tests\test_knowledge_pack_install.py` |
| M10 | create/modify | `R\tools\pbo_freshness.py`; `R\tools\tests\test_pbo_freshness.py`; `U\.agents\skills\dayz-test-ingame\templates\dayz-test.ps1`; `U\.claude\skills\dayz-test-ingame\templates\dayz-test.ps1`; `Q\LFQuad2_dev\tools\dayz-test.ps1` |
| M11 | modify/create | `Q\LFPowerGrid_dev\tools\build_guarded.py`; `Q\LFPowerGrid_dev\tests\test_version_gate.py`; `R\reports\2026-08-30-inbox-implementation\M11\version-gate-verdict.json` |
| M11 | read | `Q\LFPowerGrid\config.cpp`; `Q\LFPowerGrid\scripts\3_Game\LFPG_Defines.c`; `Q\LFPowerGrid_dev\tools\verify_corrective.py`; `Q\Mods\@LFPowerGrid\Addons\LFPowerGrid.pbo`; `Q\pboviewer-cli-windows-x64\PboViewer.exe`; `C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\CfgConvert\CfgConvert.exe`. Todos son read-only y los paths/hashes/resultados quedan registrados en `version-gate-verdict.json` |
| M12 | create | `R\tools\dayz_mcp\dayz_test_modes.py`; `R\tools\tests\test_dayz_test_modes.py` |
| M13 | create | `R\tools\dayz_mcp\effective_schema.py`; `R\tools\dayz_mcp\effective_schema_catalog.py`; `R\tools\dayz_mcp\effective_schema_runtime_validators.py`; `R\tools\tests\test_effective_schema.py`; `R\tools\tests\test_effective_schema_catalog.py`; `R\tools\tests\test_effective_schema_runtime_validators.py`; `R\tools\tests\fixtures\effective_schema_v1\required_constraint_ids.json`; `R\tools\tests\fixtures\effective_schema_v5\instructions_required_concepts.json`; `R\tools\tests\fixtures\effective_schema_v5\profile_inventory.json`; `R\tools\tests\fixtures\effective_schema_v5\validator_cases.json`; `R\tools\tests\fixtures\effective_schema_v5\mutation_cases.json` |
| M13/M23 | create ephemeral | `T\dayz-mcp-schema-calibration\<txid>` nuevo por corrida, fuera del workspace candidato; `PYTHONDONTWRITEBYTECODE=1`, sin `.pyc`, eliminado sólo después de guardar el veredicto |
| M14 | create | `R\tools\dayz_mcp\tool_registry_fingerprint.py`; `R\tools\tests\test_tool_registry_fingerprint.py` |
| M15 | create | `R\tools\dayz_mcp\dayz_test_storage.py`; `R\tools\tests\test_dayz_test_storage.py` |
| M16 | create | `R\tools\dayz_mcp\steam_preflight.py`; `R\tools\tests\test_steam_preflight.py` |
| M17 | modify | `R\tools\dayz_mcp\process_lifecycle.py`; `R\tools\dayz_mcp\daemon.py`; `R\tools\dayz_mcp\loopback.py`; `R\tools\tests\test_process_lifecycle.py`; `R\tools\tests\test_lifecycle_http.py` |
| M18 | modify | `R\tools\dayz_mcp\dayz_test_request.py`; `R\tools\tests\test_dayz_test_request.py` |
| M19 | modify/create | `R\tools\dayz_mcp\dayz_test_tool.py`; `R\tools\dayz_mcp\dayz_test_readiness.py`; `R\tools\tests\test_dayz_test_tool.py`; `R\tools\tests\test_dayz_test_readiness.py`; `R\tools\tests\test_dayz_test_value_error_codes.py` |
| M20 | modify/recursive-build-output | `R\tools\dayz_mcp\dayz_test_worker.py`; `R\tools\build_native_launcher.py`; `R\tools\dayz_mcp\native_bundle.py`; `R\tools\native-launchers\dayz-test-v1`; `R\tools\approved-launchers.json`; `R\tools\approved-launchers.receipts`; `R\tools\tests\test_dayz_test_worker.py`; `R\tools\tests\test_native_launcher_bundle.py`; `R\tools\tests\test_launcher_registry_update.py`; `R\tools\dayz_mcp\dayz_tools_paths.py`; `R\tools\tests\test_dayz_tools_paths.py`; `R\tools\tests\test_native_bundle.py` |
| M20 | read | `R\tools\dayz_mcp\launcher_registry_update.py`; `R\tools\dayz_mcp\launcher_registry.py`; `R\tools\dayz_mcp\dayz_test_modes.py` producido por M12; `R\tools\dayz_mcp\dayz_test_storage.py` producido por M15; `R\tools\dayz_mcp\dayz_test_request.py` producido por M18; `R\tools\dayz_mcp\dayz_test_readiness.py` producido por M19; el baseline/receipt acreditado por esas APIs |
| M21 | modify | `R\tools\dayz_mcp\dayz_test_tool.py`; `R\tools\tests\test_dayz_test_tool.py` |
| M22 | modify/create | `R\tools\dayz_mcp\server.py`; `R\tools\dayz_mcp\result_prune.py`; `R\tools\tests\test_mcp_tools.py`; `R\tools\tests\test_result_prune.py`; `R\tools\tests\test_wait_for.py`; `R\tools\tests\test_weak_agent_consumer_ux.py`; `R\tools\tests\test_world_spawn_ground_contract.py`; `R\tools\tests\test_pipeline_feedback.py`; `R\tools\tests\test_effective_schema_integration.py`; `R\tools\tests\fixtures\bridge_capabilities_v1.json`; `R\tools\dayz_mcp\core.py` **acotado**: exclusivamente el paso a traves del bloque `capabilities` en `_peer_status`, sin tocar ningun otro campo ni la logica de version (enmienda 2026-09-03) |
| M23 | create/append/publish | `R\tools\promote_effective_schema.py`; `R\tools\tests\test_effective_schema_promotion.py`; los paths exactos de «Transacción M23» |
| M23 | read | `R\tools\dayz_mcp\identity_migration.py:406-456` como patrón de lock OS sin importarlo; `R\tools\tests\fixtures\effective_schema_v1\required_constraint_ids.json` y los cuatro fixtures v5 de M13: `instructions_required_concepts.json`, `profile_inventory.json`, `validator_cases.json`, `mutation_cases.json`; todos los productores enumerados y la app/bundle final de M20+M22 |
| M24 | create/publish | `T\dayz-mcp-m24\<txid>` como directorio nuevo reservado; `Q\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo`; backup sibling y journal exactos de «Transacción M24» |
| M24 | read | `R\addon`; inputs M10; toolchain fijado por la corrida |
| M25 | append/create/modify | `U\AppData\Local\DayZ_MCP\inbox\feedback.jsonl` sólo append; `R\GATES.md`; las 35 hojas gate del «Set M01/M25»; `R\reviews\2026-08-31-inbox-implementation-grok-v1` como `recursive-build-output`, incluido `grok-code-review-manifest.json`; los tokens de attestation de cierre definidos en esa sección; `R\reports\2026-08-30-inbox-implementation\M25\delivery-manifest.json`; `R\reports\2026-08-30-inbox-implementation\M25\final-gate-verdict.json`; `R\reports\2026-08-30-inbox-implementation\M25\inbox-resolution-receipts.jsonl`; `R\reports\2026-08-30-inbox-implementation\M25\ledger-verdict.json`; `R\reports\2026-08-30-inbox-implementation\M25\ledger-final-verdict.json`; `R\reports\2026-08-30-inbox-implementation\M25\final-session-status.json`; `R\reports\2026-08-30-inbox-implementation\M25\memory-receipt.json` |

## Solapes serializados obligatorios

- M02 y M04 no comparten paths y pueden correr en paralelo; M05 espera ambos por contrato DTO y por
  la transición M04→M05 sobre la entrega completa.
- `M04→M05` serializa `MCPClientBridge.c`.
- `M06→M17` serializa `loopback.py`.
- `M19→M21` serializa `dayz_test_tool.py` y su test.
- `M19→M20` congela `dayz_test_readiness.py` antes de que M20 lo empaquete y hashee; M20 sólo lo
  lee.
- `M08→M22` serializa `test_pipeline_feedback.py`.
- `M20→M23` serializa los productores de bundle que M23 rehashea; M23 no los escribe.
- `M22→M23` congela app/schema; `M22→M24` congela addon/servidor antes del PBO.
- `M01→M00→…→M25` serializa hojas de gate y evita que el baseline absorba una aprobación nueva.

## Transacción M23

Raíz física exacta: `R\reports\2026-08-30-inbox-implementation\M23`.

Canónicos cerrados:

- `dayz-effective-schema-v1.json` — último commit marker; único schema promovido.
- `tool-registry-fingerprint-v1.sha256`.
- `effective-schema-v5-verdict.json`.
- `effective-schema-producers-v1.json`.
- `m23-receipts.jsonl` — append-only.

[DESIGN] Para un `txid` de 32 hex generado una sola vez, los únicos temporales son
`<canonical>.candidate.<txid>`; backups create-only
`<canonical>.backup.<txid>.<sha12>`; journal
`m23-promotion.<txid>.journal.json`; lock anchor `m23-promotion.lock`. La mera existencia del anchor
no significa ownership: se abre/pinea como regular no-reparse y se bloquea con un byte-range lock
OS exclusivo que el kernel libera al cerrar o morir el proceso, siguiendo el patrón verificado en
`R\tools\dayz_mcp\identity_migration.py:406-456`. Tras adquirirlo se reconcilia primero cualquier
journal de una corrida muerta; sólo entonces puede nacer otro txid. El schema final se reemplaza último y
es el commit marker. Un lock OS actualmente retenido, un journal no reconciliable, un backup con bytes distintos, source drift o
CAS de preimagen fallido bloquean sin segundo replace a ciegas.

[DESIGN] `m23-receipts.jsonl` no forma parte del conjunto restaurable. Cada operación publica por
CAS un candidate igual a la preimagen exacta más una línea canónica y nunca trunca, reserializa ni
repone el sidecar desde backup. Un rollback restaura byte a byte sólo schema, fingerprint, verdict y
producers, añade una línea `kind=rollback` con `operation_txid` nuevo y conserva todo el prefijo de
receipts, incluidos intentos que no llegaron al commit marker.

## Transacción M24

- Source exacto read-only durante build: `R\addon`.
- Temp exacto por corrida: `T\dayz-mcp-m24\<txid>`; debe ser nuevo y quedar registrado antes de la
  primera escritura.
- Candidate: `T\dayz-mcp-m24\<txid>\DayZ_MCP.candidate.<txid>.pbo`.
- Publish único: `Q\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo`.
- Backup create-only: `Q\Mods\@DayZ_MCP\Addons\DayZ_MCP.backup.<txid>.<sha12>.pbo`.
- Journal: `R\reports\2026-08-30-inbox-implementation\M24\m24-publish.<txid>.journal.json`.

[DESIGN] El candidate se valida completamente antes de tocar publish. Se reacreditan source,
candidate y preimagen; se crea backup sin overwrite y se hace un único replace del publish. Un fallo
previo deja publish byte-idéntico; un fallo posterior exige reacreditar journal/candidate/publish. El
build nunca usa un `_compile` dentro del repo como temporal.

## Set M01/M25

El set cerrado de hojas es exactamente:

`gates/inbox-01-211445-3bb4.md`, `gates/inbox-02-212912-f6ac.md`,
`gates/inbox-03-224835-268a.md`, `gates/inbox-04-022838-7743.md`,
`gates/inbox-05-023649-8f8c.md`, `gates/inbox-06-024747-55dd.md`,
`gates/inbox-07-024827-9b7b.md`, `gates/inbox-08-024848-c7ca.md`,
`gates/inbox-09-025012-103f.md`, `gates/inbox-10-025502-251d.md`,
`gates/inbox-11-025754-f201.md`, `gates/inbox-12-030056-d73b.md`,
`gates/inbox-13-032121-fc6e.md`, `gates/inbox-14-103347-243b.md`,
`gates/inbox-15-104543-47c9.md`, `gates/inbox-16-104608-4d66.md`,
`gates/inbox-17-104625-7c88.md`, `gates/inbox-18-104630-141e.md`,
`gates/inbox-19-111016-344d.md`, `gates/inbox-20-115147-4407.md`,
`gates/inbox-21-133459-a396.md`, `gates/inbox-22-135408-cc2d.md`,
`gates/inbox-23-135727-782b.md`, `gates/inbox-24-184906-21f5.md`,
`gates/inbox-25-184952-20be.md`, `gates/inbox-26-194752-d366.md`,
`gates/inbox-27-194823-ffc7.md`, `gates/inbox-28-221423-b2c4.md`,
`gates/inbox-29-230535-f4f2.md`, `gates/inbox-30-002237-0de3.md`,
`gates/inbox-31-010517-9d46.md`, `gates/inbox-32-011217-668f.md`,
`gates/inbox-33-112422-2762.md`, `gates/inbox-34-112438-40e4.md`,
`gates/inbox-35-112522-1082.md`.

Los 35 planes M01 son exactamente:

`plans/inbox-20260830/01-fb-20260828-211445-3bb4.md`,
`plans/inbox-20260830/02-fb-20260828-212912-f6ac.md`,
`plans/inbox-20260830/03-fb-20260828-224835-268a.md`,
`plans/inbox-20260830/04-fb-20260829-022838-7743.md`,
`plans/inbox-20260830/05-fb-20260829-023649-8f8c.md`,
`plans/inbox-20260830/06-fb-20260829-024747-55dd.md`,
`plans/inbox-20260830/07-fb-20260829-024827-9b7b.md`,
`plans/inbox-20260830/08-fb-20260829-024848-c7ca.md`,
`plans/inbox-20260830/09-fb-20260829-025012-103f.md`,
`plans/inbox-20260830/10-fb-20260829-025502-251d.md`,
`plans/inbox-20260830/11-fb-20260829-025754-f201.md`,
`plans/inbox-20260830/12-fb-20260829-030056-d73b.md`,
`plans/inbox-20260830/13-fb-20260829-032121-fc6e.md`,
`plans/inbox-20260830/14-fb-20260829-103347-243b.md`,
`plans/inbox-20260830/15-fb-20260829-104543-47c9.md`,
`plans/inbox-20260830/16-fb-20260829-104608-4d66.md`,
`plans/inbox-20260830/17-fb-20260829-104625-7c88.md`,
`plans/inbox-20260830/18-fb-20260829-104630-141e.md`,
`plans/inbox-20260830/19-fb-20260829-111016-344d.md`,
`plans/inbox-20260830/20-fb-20260829-115147-4407.md`,
`plans/inbox-20260830/21-fb-20260829-133459-a396.md`,
`plans/inbox-20260830/22-fb-20260829-135408-cc2d.md`,
`plans/inbox-20260830/23-fb-20260829-135727-782b.md`,
`plans/inbox-20260830/24-fb-20260829-184906-21f5.md`,
`plans/inbox-20260830/25-fb-20260829-184952-20be.md`,
`plans/inbox-20260830/26-fb-20260829-194752-d366.md`,
`plans/inbox-20260830/27-fb-20260829-194823-ffc7.md`,
`plans/inbox-20260830/28-fb-20260829-221423-b2c4.md`,
`plans/inbox-20260830/29-fb-20260829-230535-f4f2.md`,
`plans/inbox-20260830/30-fb-20260830-002237-0de3.md`,
`plans/inbox-20260830/31-fb-20260830-010517-9d46.md`,
`plans/inbox-20260830/32-fb-20260830-011217-668f.md`,
`plans/inbox-20260830/33-fb-20260830-112422-2762.md`,
`plans/inbox-20260830/34-fb-20260830-112438-40e4.md`,
`plans/inbox-20260830/35-fb-20260830-112522-1082.md`.

Las raíces de revisión son nuevas y cerradas: M01 reserva exactamente
`R\reviews\2026-08-31-inbox-plan-grok-v1\`, incluido `grok-plan-review-manifest.json`; M25
reserva exactamente `R\reviews\2026-08-31-inbox-implementation-grok-v1\`, incluido
`grok-code-review-manifest.json`. No autorizan escribir en ninguna raíz de revisión histórica.

## Manifiestos y títulos hash-bound

- [DESIGN] `plan-manifest.sha256` contiene el hash del authority bundle y exactamente las 35
  parejas `SHA-256 + path` de plan, una vez cada una. No contiene outputs de revisión.
- [DESIGN] `grok-plan-review-manifest.json` usa
  `{schema_version,generation,authority_bundle_sha256,plan_manifest_sha256,route,
  requested_model,served_model,entries}`. Cada entry contiene
  `feedback_id,plan_path,plan_sha256,review_path,review_sha256,session_id,stream_path,
  stream_sha256,result_subtype,is_error,verdict,open_findings`. El stream JSONL bruto se conserva
  hasheado y debe demostrar un único `system/init`, modelo servido exactamente
  `Cursor Grok 4.6 Medium`, el mismo `session_id` en el evento `result`, `result_subtype=success`
  e `is_error=false`. Cada sesión es fresca, empieza leyendo directamente `product-spec.md`, se
  ejecuta con `--mode ask` y no lee raíces de reviews, reports, gates ni `GATES.md`. Sólo `PASS`
  con cero hallazgos abre implementación; no hay segunda llamada de calibración.
- [DESIGN] `delivery-manifest.json` usa
  `{schema_version,generation,authority_bundle_sha256,plan_manifest_sha256,entries}`. Hay una entry
  por ID con `feedback_id,plan_sha256,disposition,files,evidence,verification`; cada path final
  lleva SHA-256 y rol. `entry_sha256` es SHA-256 de esa entry sin el propio campo, serializada como
  JSON UTF-8 sin BOM con claves lexicográficas, arrays preservados y separadores compactos. El
  manifiesto enumera bytes finales, no un diff mutable.
- [DESIGN] `grok-code-review-manifest.json` comparte los campos de ruta, modelo servido, stream
  bruto y resultado de una sola sesión del manifiesto de plan, usa `delivery_manifest_sha256` y
  para cada ID liga `delivery_entry_sha256` a `review_path/review_sha256`. Exige una sesión fresca
  de `Cursor Grok 4.6 Medium` en `--mode ask`, `result_subtype=success`, `is_error=false`, `PASS`
  y cero hallazgos abiertos para resolver; no hay segunda llamada de calibración.

## Recibos de resolución y cierre no circular

- [DESIGN] `inbox-resolution-receipts.jsonl` contiene exactamente una línea por feedback, en el
  mismo orden estable del manifest. Cada objeto tiene
  `feedback_id,evidence_ref,delivery_entry_sha256,grok_review_sha256,feedback_preimage_sha256,
  appended_line_sha256,feedback_postimage_sha256,round_trip_status,unresolved_total_after`.
  `appended_line_sha256` hashea los bytes exactos que `pipeline_resolve` añadió a
  `feedback.jsonl`, incluido su LF final `0x0A`; no se reserializa el evento para calcularlo. La
  línea del recibo se serializa con claves lexicográficas, separadores compactos, UTF-8 sin BOM y
  exactamente un LF final; `RECEIPT_SHA` es SHA-256 de esos bytes completos, incluido ese LF.
- [DESIGN] Cada append se acredita con preimagen, sufijo añadido y postimagen; después se llama
  `pipeline_inbox(include_resolved=true)` y se exige round-trip del mismo `feedback_id`,
  `resolution` y `evidence_ref`, además del contador decreciente. Sólo entonces
  `round_trip_status="pass"`. La línea liga el delivery-entry ya revisado con el SHA del review
  Grok/Cursor exacto; un append no enlazado, una ref limpiada/normalizada de forma distinta o un contador
  no esperado bloquea `RESOLVED`.
- [DESIGN] El orden de cierre evita auto-referencia: primero se atestiguan los 175 gates hoja y
  `ROOT-RESEARCH/DPF/PLANS/IMPLEMENTATION/REVIEWS/INBOX`. Después `ledger-verdict.json` captura la
  salida esperada de `gate-check` con exactamente `181 met`, `2 unmet` y sólo
  `ROOT-VERIFY,ROOT-CLOSE` pendientes; cualquier otro estado falla. `ROOT-VERIFY` liga ese snapshot
  pre-cierre y `final-gate-verdict.json`, luego se atestigua. Tras `session_status` y memoria se
  atestigua `ROOT-CLOSE`. Finalmente se ejecuta de nuevo `gate-check`; sólo `183 met, 0 unmet`
  completa el proceso y su salida se guarda en `ledger-final-verdict.json`. Ningún título o
  attestation depende de este último fichero, por lo que no hay ciclo hash→gate→hash.

Antes de cualquier attestation, M01/M25 reemplaza los placeholders por hex lowercase exacto. Los
títulos vivos son literalmente:

- `PLAN-CODEX: plan <ID> sha256:<PLAN_SHA> congelado bajo authority-bundle sha256:<AUTH_SHA> con PASS FAIL e INCONCLUSIVE`.
- `PLAN-GROK: Grok 4.6 Medium mediante Cursor dio PASS en sesión fresca al plan <ID> sha256:<PLAN_SHA>; review sha256:<REVIEW_SHA>`.
- `IMPLEMENTED: <ID> materializado o evidenciado conforme a delivery-entry sha256:<ENTRY_SHA>`.
- `REVIEW-GROK: Grok 4.6 Medium mediante Cursor dio PASS sin hallazgos abiertos a delivery-entry <ID> sha256:<ENTRY_SHA>; review sha256:<REVIEW_SHA>`.
- `RESOLVED: pipeline_resolve conserva receipt-line <ID> sha256:<RECEIPT_SHA> y evidencia durable`.

Los títulos raíz vivos son literalmente:

- `ROOT-RESEARCH: Fase 0 sha256:<RESEARCH_SHA> citada y copiada a memoria durable`.
- `ROOT-DPF: enmienda DPF aprobada ligada a product-spec sha256:<PRODUCT_SPEC_SHA>`.
- `ROOT-PLANS: 35 planes ligados a plan-manifest sha256:<PLAN_MANIFEST_SHA> y grok-plan-review-manifest sha256:<GROK_PLAN_MANIFEST_SHA>`.
- `ROOT-IMPLEMENTATION: 35 deliveries ligados a delivery-manifest sha256:<DELIVERY_MANIFEST_SHA>`.
- `ROOT-REVIEWS: Grok 4.6 Medium mediante Cursor dio PASS a delivery-manifest sha256:<DELIVERY_MANIFEST_SHA>; grok-code-review-manifest sha256:<GROK_CODE_MANIFEST_SHA>`.
- `ROOT-INBOX: 35 receipts ligados a inbox-resolution-receipts sha256:<RECEIPTS_SHA> con open_count=0`.
- `ROOT-VERIFY: final-gate-verdict sha256:<FINAL_GATE_SHA> PASS y ledger-verdict sha256:<LEDGER_SHA> acredita 181 met con sólo ROOT-VERIFY y ROOT-CLOSE pendientes`.
- `ROOT-CLOSE: final-session-status sha256:<SESSION_SHA> y memory-receipt sha256:<MEMORY_SHA> documentan cierre limpio`.

[EXACT] `gate-check.mjs` deriva cada attestation como
`U\.unlazy\approved\attest-<sha256(real-ledger-path + NUL + gate-id)>.json` en
`C:\Users\guill\.agents\skills\gates-ledger\scripts\gate-check.mjs:583-584` y valida igualdad
exacta de path real, gate ID y título en `:603-643`; no autentica `EVIDENCE`. M01 reserva sólo
los tokens deterministas de `PLAN-CODEX` y `PLAN-GROK` de las 35 hojas, más
`ROOT-RESEARCH`, `ROOT-DPF` y `ROOT-PLANS`; M25 reserva sólo los tokens de `IMPLEMENTED`,
`REVIEW-GROK` y `RESOLVED`, más los gates raíz de implementación, revisión, inbox, verificación
y cierre. Por decisión expresa del usuario, `PLAN-SONNET` y `REVIEW-SONNET` se retiraron de las
35 hojas vivas; sus 70 registros y los hashes pre-retirada se conservan en
`reports/2026-08-31-sonnet-retirement-manifest.md`. El ledger ejecutable contiene 183 gates y no
reserva recursivamente el resto de `U\.unlazy\approved`.

## Viability del addendum

- PASS: A=B; los 25 módulos expanden a paths finales; todo solape de escritura tiene arista; un
  mutante que añade alias, parent-dir o write-set no declarado queda bloqueado.
- FAIL: dos implementadores reciben el mismo file-id o relación padre-hijo sin arista; se usa un
  glob, un temporal dentro del repo para M24, un publish alternativo o un path no inventariado.
- INCONCLUSIVE: path final/file-id no puede leerse, target parent cambia, OneDrive/reparse diverge,
  A≠B o un writer de plan/review sigue activo; no lanzar implementadores.

## Enmienda 2026-09-02 - tres paths huérfanos en el radio de M20

Aplicada la cláusula «una ruta no listada requiere replan» de §Regla de exclusión sobre tres
ficheros que ninguna fila declaraba y que M20 necesita para cerrar sus rojos heredados. Se añaden
a la fila `M20 | modify/recursive-build-output`:

| Path | Por qué entra en M20 |
|---|---|
| `R\tools\dayz_mcp\dayz_tools_paths.py` | Declara `STEAM_RELATIVE_FILES`, la lista de externos que el cierre del bundle pinnea por hash. |
| `R\tools\tests\test_dayz_tools_paths.py` | Afirmaba la cardinalidad de esa lista (`len(STEAM_RELATIVE_FILES) == 6`), así que cambia con ella. Tras el cambio afirma `== ()` **más la ausencia de los seis nombres** en `external_file_paths`: sobre una tupla vacía, una cardinalidad no distingue nada. |
| `R\tools\tests\test_native_bundle.py` | Lleva uno de los tres rojos heredados que M20 está encargado de cerrar. |

Motivo medido: de las 83 entradas del `closure-manifest.json` de `dayz-test-v1`, exactamente
cuatro habían derivado, y las cuatro son DLL del cliente de Steam - `steamclient.dll`,
`tier0_s.dll`, `vstdlib_s.dll` y `GameOverlayRenderer.dll` -, que Steam actualiza por su cuenta.
Cero deriva en el bundle del proyecto, en DayZ Tools y en `DayZDiag_x64.exe`. Un cierre que afirma
por hash binarios que se actualizan solos se pone rojo con el calendario de un tercero, y ese rojo
no distingue una actualización de una manipulación: es el rojo que nadie lee el día que importa.

Decisión: las seis DLL del cliente de Steam salen del cierre. Queda pinneado lo que el proyecto
controla o congela. Alternativas descartadas: resellar sin tocar el contrato, que deja el mismo
defecto con el reloj a cero; y partir el manifiesto en niveles `pinneado`/`observado`, que conserva
el registro forense a cambio de cambiar un formato persistente.

Esta enmienda no amplía ningún otro módulo ni crea solape: los tres paths no estaban en ninguna
fila, así que no hay arista nueva que serializar.


## Enmienda 2026-09-03 - el portador entre M06 y M22 no lo declaraba nadie

**Lo medido, no lo supuesto.** M06 publica el censo de capabilities en el snapshot de
`loopback.ServerState.status_snapshot()` (commit `60e6f1d`), y M22 tiene que compararlo contra las
tools registradas. Entre los dos hay un portador, y esta fuera de toda fila:

    snapshot loopback   -> {'state': 'announced', 'reason': 'ok', 'announced_commands': [...]}
    tras build_status   -> AUSENTE
    claves del peer     -> binding_state, bound_last_poll_age_s, instance_prefix,
                           last_poll_age_s, observed_this_generation, queue_depth,
                           version, version_detail, version_state

`core._peer_status` (`tools/dayz_mcp/core.py:50-81`) construye un dict NUEVO con nueve claves fijas,
asi que cualquier campo que M06 anada al snapshot muere ahi. Y `core.py` no aparece en ninguna fila
del write-set: ni M06, ni M22, ni ningun otro modulo lo declara. Es el mismo hueco de clase que la
ficha `fb-20260902-194902-485b`, ahora con un consumidor concreto bloqueado.

**Por que no vale ninguna via que lo evite.** `Runtime.bridge_status_payload` podria leer el
snapshot crudo desde `server.py` -que si es de M22- y funcionaria en modo embedded. Pero
`ClientRuntime` recibe el payload por HTTP desde `/status`, que lo construye el daemon con el mismo
`build_status`: el campo nunca cruzaria el cable y una sesion en modo cliente veria `unknown` para
siempre. Media solucion que ademas se ve verde es peor que la enmienda.

**Decision.** Se anade `core.py` a la fila de M22 **acotado al paso a traves de ese bloque**, con la
misma forma que ya usa la fila de M11 para marcar sus paths read-only. No se le da el fichero
entero: la logica de version, el estado por peer y todo lo demas siguen fuera del radio de M22, y
tocarlos seguiria siendo INCONCLUSIVE+replan.

**Alternativas descartadas, y por que.** (1) Escribir en `core.py` sin enmendar: rompe la atribucion
por commit de M00-v2, que exige que todo path caiga en el conjunto OWNED de un modulo. (2) Dar
`core.py` a M06: el productor no deberia poseer el transporte de su consumidor, y M06 ya cerro su
entrega. (3) Duplicar el campo desde `server.py` sin tocar `core.py`: la media solucion de arriba.
