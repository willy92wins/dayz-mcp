# Autoridad sucesora no-S15 v1

Fecha: 2026-09-01  
Estado: **CANDIDATE — no activa hasta revisión Grok PASS y promoción atestada**  
Generation propuesta: `20260901-ledger-v4`  
Graph version propuesta: `inbox-20260901-v7`  
Review protocol: `grok-cursor-only`

## 1. Predecesora y condición de activación

La autoridad activa de preimagen es exactamente:

- generation `20260831-ledger-v3`;
- graph `inbox-20260831-v6`;
- protocolo `grok-cursor-only`;
- `plans/inbox-20260830/authority-bundle-v7.sha256` SHA-256
  `9a0679809c30831d353838165abbfdffef461c6f8531a83ca4b906026fba0b49`.

Esta sucesora sólo desplaza a la predecesora mediante esta secuencia única, sin promoción
intermedia:

1. Grok 4.6 Medium mediante Cursor revisa en sesión fresca el staging TEMP exacto identificado
   por el SHA-256 del fichero `authority-bundle-v8.sha256` y el SHA-256 de
   `non-s15-successor-authority-v1.md`, y produce `PASS`,
   `open_findings=0`, con `result_subtype=success` e `is_error=false`;
2. se rehashea el staging y se exige que ambos hashes sigan siendo los revisados;
3. después de acreditar preimágenes, se instalan byte a byte en el repo únicamente los targets del
   staging espejo;
4. se rehashean esos targets **en el repo** y se exige igualdad exacta con los bytes revisados;
5. sólo entonces se ejecuta `gate-check --status` y se atestiguan `AUTH-DECISION`, `AUTH-BYTES`,
   `AUTH-GROK` y `AUTH-ROLLBACK`; `AUTH-PROMOTED` se atestigua último y activa la v8.

Hasta completar los cinco actos, la v8 es candidate y la v7 sigue activa. Crear los ficheros
candidate, calcular sus hashes o ejecutar `gate-check --status` no autoriza materialización.

## 2. Proveniencia aprobada

La decisión del usuario fija los contratos siguientes sobre estas preimágenes informativas:

| Input | SHA-256 |
|---|---|
| `successor-authority-draft.md` | `26e32603e57f1a9f84a4c11e227ec816ea19e7b34f63d3ecfb3fc937e98f3990` |
| `non-s15-approval-brief.md` | `1e24f5309d7dba8c46e160a1d8efe266ef569254ea29e0058e43ab47555fae04` |
| `S13-authority-options.md` | `a55e17ce9765206b61679f0de65d9bfd8a3022438ff343fd2263ed345a4c5ad9` |
| `S14-authority-options.md` | `7996e342231582c247b6ef892b42a4699677e8f2ff62cfe3c3390a3300e50aec` |

Las mini-specs S13 y S14 se preservan byte-idénticas junto a este documento. Por esa preservación,
sus banners históricos todavía dicen TEMP/no-successor/no-implementa y el cierre S13 dice REVISE.
Esos banners prueban provenance y no se editan para conservar los hashes aprobados. **Sólo cuando
v8 se activa mediante §1, este wrapper supersede esos banners de estado y promociona el contenido
técnico íntegro de ambas mini-specs a autoridad normativa.** Antes de activar v8 continúan siendo
TEMP. Sus PASS previos acreditan los inputs de diseño; no se reutilizan como PASS del bundle v8.
El bundle v8 requiere una revisión Grok nueva y separada. No se afirma ni se fabrica esa revisión.

## 3. Reglas transversales

1. `reports/2026-09-01-non-s15-successor-preimages.json` acredita únicamente la aplicación de
   autoridad y sirve como referencia inicial de los leaves no-S15 enumerados; no es una preimagen
   universal de M17/M19/M22 ni congela para siempre archivos compartidos. **Cada batch material**
   captura una preimagen fresca de todos sus OWNS después de cerrar sus predecesores y justo antes
   de escribir, y la liga a su delivery manifest. En una cadena serial sobre el mismo archivo
   (`S-04-WIRE → S-05 → S-05-UI.material` o `S-06 → M17`), la preimagen esperada del sucesor es el
   hash final revisado/entregado por su predecesor, no el hash inicial de este JSON. Drift, target
   nuevo ya creado o parent no acreditable produce `INCONCLUSIVE`, sin escritura.
2. Un path fuera de los OWNS del batch es read-only. Una región excluida dentro de un archivo
   compartido también es read-only; el diff de la ventana debe demostrarlo.
3. PASS observa al consumidor real o una referencia/expected independiente. Comparar un output
   consigo mismo, con una transformación reversible propia o con expected derivado del mismo
   output no cuenta.
4. Cada batch exige un positivo, un negativo que muerda el mecanismo y un resultado
   INCONCLUSIVE/setup-failed distinguible. Un negativo que pasa porque desapareció el sujeto es
   inválido.
5. Cada entrega recibe revisión Grok/Cursor nueva sobre sus bytes finales. Los PASS de plan o de
   módulos anteriores no se heredan.
6. Rollback restaura únicamente los OWNS del batch desde sus preimágenes. No borra ni refactoriza
   adyacencias y no toca PBO publicado.

## 4. Contratos materializables

### S-03 — capacidades del poll servidor

OWNS cerrados:

- `addon/scripts/5_Mission/MCPBridge.c` sólo en la construcción del poll;
- `tools/tests/test_entities_has_cargo.py`;
- `tools/tests/test_bridge_server_capabilities.py`.

Contrato `caps-wire-v1`:

- valor lógico exacto `token(,token)*`;
- exactamente un parámetro query `caps`;
- el valor completo se query-encodea una vez y el receptor lo decodea una vez;
- ausencia de `caps` = peer legacy, capacidades `unknown`;
- `caps=` vacío = malformado, capacidades `unknown`;
- 1..64 tokens ASCII, cada uno `[a-z][a-z0-9_]{0,63}`;
- tokens únicos, en el orden exacto del dispatcher;
- valor lógico post-decode de máximo 4096 bytes.

PASS observa un único `caps` canónico y el consumidor acredita exactamente el conjunto
anunciado. Los mutantes de delimitador, doble encoding/decode, vacío, duplicado, token inválido,
orden o exceso de 4096 bytes deben quedar rojos.

Arista: `M02 → S-03 → S-06`.

### S-04-WIRE — request/response de telemetría de vehículo

OWNS cerrados:

- región request/response de vehículo de `addon/scripts/5_Mission/MCPClientBridge.c`;
- región/clase `TestVehicleTelemetryWireContract` de
  `tools/tests/test_vehicle_telemetry_contract.py`.

Transporta ambos literales `seat_index` y `expected_type` hasta el consumidor previsto. Excluye
literalmente la definición y cualquier llamada nueva de `DispatchVehicleTelemetry`, la clase live
y candidate-live. Los negativos omiten o sustituyen por separado cada campo y deben fallar.

Arista: `M02 → S-04-WIRE → S-05`.

### S-05 — capacidades del poll cliente

OWNS cerrados:

- sólo la región capability-poll de `addon/scripts/5_Mission/MCPClientBridge.c`;
- `tools/tests/test_bridge_client_capabilities.py`.

Aplica exactamente `caps-wire-v1`, sin alterar WIRE, UI ni telemetría. La ausencia, vacío,
malformación, duplicado, orden no canónico, doble encoding/decode o exceso queda `unknown`.

Arista: `S-04-WIRE → S-05`; `S-03 + S-05 → S-06`.

### S-05-UI.material — UI Enforce material

OWNS cerrados:

- sólo regiones UI de `addon/scripts/5_Mission/MCPClientBridge.c`;
- `tools/tests/test_ui_click_scriptview.py`;
- `tools/tests/test_ui_enforce_contract.py`.

Exclusiones dentro del source compartido: capability-poll S-05, request/response S-04-WIRE y
toda la región `DispatchVehicleTelemetry`. Implementa los contratos materiales de las fichas M05
agregadas `f6ac`, `7743`, `8f8c`, `47c9`, `21f5`, `20be`, `f4f2` y `2762`, incluidos
`root`, `matched_path`, `ui_request`, `complete|direct|bubble`, handlers, UID y colores exigidos.
No posee layout, fixture LFPG, wire, caps ni telemetría.

La fase live M05 permanece read-only y posterior a M22 y a un receipt S-11-EVIDENCE PASS. Sólo
ese gate live completo convierte `M05.material` en `M05.full`; `M05.full` no es precondición de
M22. `M05.material = S-05 + S-05-UI.material` sí es precondición de M22.

### S-06 — ingreso y estado de capacidades

OWNS cerrados:

- región de ingreso poll/estado caps de `tools/dayz_mcp/loopback.py`;
- región homóloga de `tools/tests/test_loopback.py`;
- región caps de `tools/tests/test_validate_command_args_table.py`.

Después de una única decode valida `caps-wire-v1`. Sólo el último poll acreditado por
`(peer,generation)` conserva `(peer,generation,caps)`. Legacy, vacío, inválido, oversized o no
acreditado queda `unknown` y nunca hereda otra generación. M22 clasifica
`match|mismatch|unknown` contra expected independiente.

Aristas: `S-03 + S-05 → S-06 → M17 → M19`.

### S-07 — full-resolution y crop cliente

OWNS cerrados:

- `tools/mcp_capture.py`;
- `tools/tests/test_mcp_capture.py`.

Campos literales:

- `fullres_path: str|null`;
- `fullres_sha256: str|null`;
- `fullres_bytes: int`.

Con `save_fullres=false`: `null`, `null`, `0` y no existe fichero fullres. Con guardado activo,
el fichero indicado se cierra y relee; hash y tamaño se calculan sobre esos bytes releídos y están
ligados a la misma `effective_surface` pre-downscale. En `crop_space=client`, `apply_crop` recibe
cero llamadas. El negativo no cambia el crop legacy de ventana fuera de ese modo.

Arista: `M00 → S-07 → M22`.

### S-10 — namespace de frescura PBO

OWNS cerrados:

- `tools/pbo_freshness.py`;
- `tools/tests/test_pbo_freshness.py`;
- `C:\Users\guill\.agents\skills\dayz-test-ingame\templates\dayz-test.ps1`;
- `C:\Users\guill\.claude\skills\dayz-test-ingame\templates\dayz-test.ps1`;
- `P:\LFQuad2_dev\tools\dayz-test.ps1`;
- `recursive-build-output` exclusivo
  `<GetTempPath>/dayz-pbo-freshness/<mod>/<txid>`.

`<mod>` es un único segmento canónico acreditado y `<txid>` son exactamente 32 hex minúsculas.
Staging, copy y cache de la transacción viven dentro de ese prefijo. S-10 nunca lee, enumera,
adopta ni reutiliza `%TEMP%\dayz-mcp-m24` ni ningún candidate/journal M24. Dos txid producen
árboles disjuntos y cleanup/rollback no cruza la transacción.

S-10 aislado no autoriza candidate, build o publish.

### S-11-SCHEMA / S-11-EVIDENCE — receipt LFPowerGrid V1

OWNS materiales cerrados:

- `P:\LFPowerGrid_dev\tools\build_guarded.py`;
- `P:\LFPowerGrid_dev\tests\test_version_gate.py`;
- `reports/2026-08-30-inbox-implementation/M11/version-gate-verdict.json`.

Inputs read-only exactos: las dos fuentes LFPG, `verify_corrective.py`, PBO LFPG publicado,
`PboViewer.exe` y `CfgConvert.exe` enumerados en el addendum.

El receipt es JSON UTF-8 canónico, sin BOM, sin whitespace no canónico, con claves de objeto
ordenadas lexicográficamente y sin claves extra recursivamente. Keyset raíz exacto:

`{schema_version,expected_version,sources,git_commit,test_result,pbo,verifier,toolchain,verdict}`.

Contrato de tipos y valores:

- `schema_version` es integer exacto `1`;
- `expected_version` es string exacto `"1.2.4"`;
- `sources` es un array de exactamente dos objetos, sin repetición:
  - `{subject:"config.cpp",path:"P:\\LFPowerGrid\\config.cpp",sha256:<64hex>,cardinality:1,matches:["1.2.4"]}`;
  - `{subject:"LFPG_Defines.c",path:"P:\\LFPowerGrid\\scripts\\3_Game\\LFPG_Defines.c",sha256:<64hex>,cardinality:1,matches:["1.2.4"]}`;
- `git_commit` es string;
- `test_result` tiene keyset exacto `{command,returncode}` con string e integer;
- `pbo` tiene keyset exacto `{path,sha256}`, path exacto
  `P:\\Mods\\@LFPowerGrid\\Addons\\LFPowerGrid.pbo` y hash 64 hex minúsculas;
- `verifier` tiene keyset exacto
  `{mode,returncode,stdout,actual_required_addons,expected_required_addons,sha256}`;
- `mode` es `"8-vs-9"|"9-vs-9"`; `8-vs-9` exige returncode 1, exactamente los dos FAIL
  calibrados y ningún otro; `9-vs-9` exige returncode 0, cero FAIL y un PASS PBO único;
- ambos arrays de addons son `array[string]` ordenados;
- `toolchain` tiene keyset exacto `{pbo_viewer_sha256,cfg_convert_sha256}`;
- todo `sha256` son 64 hex minúsculas;
- `verdict` es exactamente `"PASS"|"INCONCLUSIVE"`; `FAIL` no pertenece a V1.

El consumidor independiente rehashea fuentes, PBO y toolchain y rechaza key/tipo/orden/UTF-8,
cardinalidad, modo, returncode, expected, clave extra o hash discordante. Drift o input no
acreditable produce INCONCLUSIVE antes de construir o publicar.

Arista: `M00 → S-11-EVIDENCE`; su receipt sólo habilita los gates live que lo exigen.

### S-13 — proyección pura de identidad

Autoridad normativa completa: `plans/inbox-20260830/S13-authority-options.md`, SHA-256
`a55e17ce9765206b61679f0de65d9bfd8a3022438ff343fd2263ed345a4c5ad9`.

Resumen no sustitutivo: función pública `project_server_config_identity`, parámetros
keyword-only exactos `enable_exec_enforce` y `client_platform`, ambos default `None`, sin
parámetro `tools`; cuatro pares positivos literales; toda ausencia, tipo exacto inválido o role no
canónico devuelve exactamente `("unknown","unknown")`. OWNS únicamente
`tools/dayz_mcp/effective_schema.py` y `tools/tests/test_effective_schema.py`. La integración con
la instancia final de `ServerConfig` pertenece a M22.

Arista: `M12 → S-13 → S-14 → M22`.

### S-14 — fingerprint y autoridad por sesión, opción A

Autoridad normativa completa: `plans/inbox-20260830/S14-authority-options.md`, SHA-256
`7996e342231582c247b6ef892b42a4699677e8f2ff62cfe3c3390a3300e50aec`.

Queda elegida exclusivamente la opción A: JSON canónico único. M14 posee sólo
`tools/dayz_mcp/tool_registry_fingerprint.py` y su test. Expone la API pura, snapshot local
inmutable, parser puro de `AuthorityBundleBytes` y comparador `fresh|stale|unknown`; no abre paths,
no importa `server.py` y no crea apps.

M22 posee el seam final de `build_app`, el join uno-a-uno de metadata de cinco campos, la sentinel
test-only y el overlay público. En cada `bridge_status()` obtiene primero
`await runtime.bridge_status_payload()` y después vuelve a solicitar, sin cachear bytes ni
`AuthoritySnapshot`, exactamente los cinco paths M23:

1. `reports/2026-08-30-inbox-implementation/M23/dayz-effective-schema-v1.json`;
2. `reports/2026-08-30-inbox-implementation/M23/tool-registry-fingerprint-v1.sha256`;
3. `reports/2026-08-30-inbox-implementation/M23/effective-schema-v5-verdict.json`;
4. `reports/2026-08-30-inbox-implementation/M23/effective-schema-producers-v1.json`;
5. `reports/2026-08-30-inbox-implementation/M23/m23-receipts.jsonl`.

Cada llamada realiza cinco lecturas frescas posteriores al payload runtime. Candidate, backup,
journal, snapshot histórico, alias, reparse, sexto path o cache queda rechazado. M23 conserva la
acreditación del filesystem real y los cinco blobs exactos definidos en la mini-spec.

Aristas: `S-13 → S-14 → M22`; `(M20 + M22) → M23` sigue bloqueada por S15 vía M20.

## 5. Grafo sucesor no-S15

Frontera ya acreditada operativamente, sin nueva promoción de gates: `M00`, `M01`, `M02`, `M08`,
`M09`, `M12`, `M16`, `M18`.

Frontera materializable sólo después de activación v8:

```text
M02 → S-03
M02 → S-04-WIRE → S-05
S-05 → S-05-UI.material
S-03 + S-05 → S-06 → M17
M00 → S-07
M00 → S-10
M00 → S-11-EVIDENCE
M12 → S-13 → S-14
M16 + M17 + M18 → M19
```

M05.material se completa con `S-05 + S-05-UI.material`; no equivale a M05.full. S-06 puede
avanzar en paralelo con S-05-UI.material después de S-05.

## 6. Exclusiones vinculantes

- **S-15/M15 DEFERRED, sin promoción.** No se congela API, marker, journal, recovery ni storage.
  M20, M21, M22, M23, M24 y M25 permanecen bloqueados transitivamente donde dependan de M15.
- **candidate-live DEFERRED y sin OWNS.** No recibe namespace, candidate, build, PBO, backup,
  journal, deploy ni write.
- **S-04-TELEMETRY DEFERRED y sin OWNS.** No empieza hasta receipt candidate-live PASS aprobado
  por una ficha posterior. No se edita `DispatchVehicleTelemetry` ni la clase live.
- **S-24/M24 DEFERRED y sin nueva ventana de escritura en esta generación.** La fila física v6
  permanece como reserva histórica en el addendum base, pero v7 no la concede ni la materializa;
  no se abre worker, candidate, namespace, journal, publish ni deploy. S-10 nunca presta su
  namespace a M24.
- No se lanza DayZ, no se construye ni publica PBO y no se resuelve ninguna entrada del inbox bajo
  esta autoridad de planificación.

## 7. Gates y revisión requerida

La hoja `gates/non-s15-successor.md` añade cinco gates manuales. Ninguno cuenta por checkbox ni
por `EVIDENCE:` escrita a mano; cada uno exige `gate-check.mjs --attest` sobre su título exacto.

Antes de `AUTH-GROK`, la revisión formal usa:

- route `cursor-agent`;
- requested model `cursor-grok-4.6-medium`;
- served model exacto `Cursor Grok 4.6 Medium` en `system/init`;
- `--mode ask`, sólo lectura, sesión fresca;
- un único resultado terminal con mismo session_id, `subtype=success`, `is_error=false`;
- verdict `PASS`, `open_findings=0` sobre el SHA-256 exacto del fichero
  `authority-bundle-v8.sha256` y el SHA-256 exacto de `non-s15-successor-authority-v1.md`.

El reviewer recibe exactamente los ficheros enumerados por el bundle v8 candidate, incluidos el
`GATES.md` staged y `gates/non-s15-successor.md`, porque el ledger delta sí forma parte del objeto
revisado. No recibe otras hojas `gates/**`, otros `reviews/**`, artefactos de revisores anteriores
ni historia git. El PASS de las mini-specs TEMP no sustituye esta revisión.

## 8. Promoción y rollback

Promoción: ejecutar exactamente la secuencia de §1. La revisión ocurre sobre TEMP antes de todo
write al repo; la atestación ocurre después de comprobar que el repo contiene los mismos bytes.
`AUTH-DECISION` no se atestigua por la mera respuesta del chat ni antes del hash final: se
atestigua sobre el título ya instalado, junto al bundle final revisado. `AUTH-PROMOTED` siempre es
el último acto. Después puede ejecutarse `gate-check --reverify --root P:\DayZ_MCP_dev --cwd
P:\DayZ_MCP_dev` únicamente tras revisar/aprobar los `CHECK:` descubiertos; hoy los cinco gates
successor son manuales.

Rollback de autoridad:

- restaurar byte a byte únicamente `GATES.md` desde su preimagen;
- retirar únicamente los siete ficheros successor nuevos enumerados como `state=absent` en el
  manifest de preimágenes;
- no tocar `authority-bundle-v7.sha256`, `plan-manifest.sha256`, gates/planes base, attestations
  previas, producto, PBO, inbox ni reports M00;
- las attestations successor quedan fuera del repo; tras rollback sus paths/títulos ya no forman
  parte del ledger descubierto y no certifican la v7.

Rollback no revoca evidencia histórica: conserva el review manifest Grok como informe y registra
que la v8 fue retirada. La v7 vuelve a ser la única autoridad activa por sus bytes exactos.
