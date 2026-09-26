---
date: 2026-08-30
reviewer: grok
model: grok-4.6-build
phase: plan-review-round-1
status: calibrated
promotable: false
---

# Ronda 1 Grok calibrada — 35 fichas del inbox

## Autoridad de la revisión

Todas las sesiones terminaron con `stopReason=end_turn`. Cada veredicto fue calibrado en la
misma sesión después de la revisión adversarial inicial; la calibración retiró hallazgos de
nomenclatura y conservó solo los que cambian causalidad, API real, DPF, OWNS, dependencias o
criterios verificables.

| Paquete | Sesión Grok 4.6 |
|---|---|
| A — input | `01a052df-bcb0-7e30-b8d2-2983e62e1598` |
| B — UI | `01a052df-bea7-71f3-9929-0814e26ae838` |
| C — schema | `01a052df-bdc5-7020-b87d-f2c69f59e6c2` |
| D — instructions | `01a052df-bf71-7483-b37b-63e1c8db5323` |
| E — lifecycle | `01a052e0-5705-7032-a39c-640083d3df37` |
| F — local data | `01a052e0-57c7-7eb3-a895-0102b97bf63e` |
| G — DayZ/build | `01a052e0-58a7-76b2-9fa4-d61e82e49dd1` |

Resultado: **34 REVISE, 1 PASS no promovido**. El único PASS (`1082`) debe reemitirse porque
su contrato público y trazado H13 se normalizan junto al resto. Las fichas `268a`, `c7ca` y
`782b` ya cambiaron de hash tras esta revisión; sus veredictos se conservan como evidencia
histórica, no como aprobación del hash nuevo.

## Veredictos individuales calibrados

| # | Feedback | SHA revisado | Veredicto | Hallazgos bloqueantes conservados |
|---:|---|---|---|---|
| 01 | `fb-20260828-211445-3bb4` | `7398f4f189bcff546554ad349224608eca61bc13aee939ec7d50f2ef56d0662c` | REVISE | `OnKeyPress(ESC)` entrega callback pero no causa pausa; OWNS condicional choca con M00/M12/M15; PBO sin dependencia declarada. |
| 02 | `fb-20260828-212912-f6ac` | `3eacfad2247264a886a9ce1f369e3632525fc8175a402b33b6fe9ceaffe9a252` | REVISE | Evidencia no debe poseer el resolver de `f4f2`; scope solo `GetMenu()` puede ocultar `ScriptView`; ciclo `f6ac↔47c9`; DPF debe ser B4. |
| 03 | `fb-20260828-224835-268a` | `626d673c498a60c3026b821d2a526af61e826ab15fcfea396415d505cd51b76b` | REVISE, superseded | No persistía client rect; crop vacío conservaba chrome; `apply_crop` fallaba abierto. Hash vigente posterior: `d4fb50cdd5990db8c2e3d5cd357ac35490c8d127adcc2eae4a4a27361ed346a7`. |
| 04 | `fb-20260829-022838-7743` | `5813c6c7ab2cd580a7e148bd62e19430a6b93f29ba4a444eecba320e0237d6c3` | REVISE | Exige fingerprint/reapertura sin depender de P11/1082; mezcla input y UI; DPF C3 incorrecto. |
| 05 | `fb-20260829-023649-8f8c` | `f114e3022bc07c278f04d2e3b26ef170712b1c1ddcd7fa17d71c9dea6fe2c4a6` | REVISE | OWNS solapa 7c88/a396; dependencias M09/M10 no causales; retry de todo `game_not_ready` puede reintentar incompatibilidades enmascaradas. |
| 06 | `fb-20260829-024747-55dd` | `6395d6fb56e43c0be278019ea452b0a2493ed1ab43e49484c97b8a93b02a987e` | REVISE | Mezcla UI con `instructions`; no enumera los seis fragmentos perdidos; ciclo/OWNS con `fc6e`. |
| 07 | `fb-20260829-024827-9b7b` | `4a042156f367d18dc64dd43093c5679cac13c3d617d85284e295c042812ad145` | REVISE | Señal stale no ligada a la sesión FastMCP frente al daemon; ciclo y doble ownership del helper con 103f. |
| 08 | `fb-20260829-024848-c7ca` | `af5a265b0648c31067466c78d8a681d0c1eb045d78450bd8856d445391b4f7c0` | REVISE, superseded | `evidence_ref` carecía de gramática fail-closed. Hash vigente posterior: `0d85213a994f8f9a8468f5029a9a0a433927a16348d901dd67fff69180560f64`. |
| 09 | `fb-20260829-025012-103f` | `e2acf53f3026f758d98fe945b0ed2b88df27a1564f1d8058c18b14243b3a4624` | REVISE | Ciclo/OWNS helper con 9b7b; identidad de sesión no fijada a `ControlIdentity.session_id`; fixture global al daemon no discrimina dos procesos FastMCP. |
| 10 | `fb-20260829-025502-251d` | `31390b58a92ebd7ea46970ec0044d65772cc3061e11a1f14a84146cc16ce99a0` | REVISE | Backdoor de parche sin nueva aprobación; fixture 201 líneas no ancla exactamente el borde 200. |
| 11 | `fb-20260829-025754-f201` | `7a2817cae1cf7f1da0f7b36ae0b00174f8343961d373c83a33dd0008c96859b0` | REVISE | Ciclo `f201↔d366↔141e`; inventario esperado derivado del mismo extractor sería tautológico. |
| 12 | `fb-20260829-030056-d73b` | `9b511f2e85e16364fa7640f2da364463da85b71c95071f84d01086a74dfa4db7` | REVISE | PASS era solo el PASS de 251d, sin discriminador BUG-086 propio; heredaba gates defectuosos. |
| 13 | `fb-20260829-032121-fc6e` | `5bdf239fb738283c643e2c9e652aa615fcb0c0369a3a94f6fe0971740a3c855b` | REVISE | Plan en capa UI en vez de `world_spawn`; fixtures UI incompatibles con disposición evidence/instructions; ciclo con 55dd; omitía `y==0→SurfaceY` previo a flags. |
| 14 | `fb-20260829-103347-243b` | `fa441d19d47e97fde7f2b590da719b53b7e118c20f1c919c4f578a5ad9a269d1` | REVISE | Evidencia de y=0 y `ok≠efecto` fue reescrita como UI; falta mecanismo real en bridge/server. |
| 15 | `fb-20260829-104543-47c9` | `6b4735e5a3465deda334afd69a939162bcec9a3e31232260f60926800545bb94` | REVISE | Gate in-game no materializa el `ScriptView` original; PASS heredado del paquete; DPF debe ser B4. |
| 16 | `fb-20260829-104608-4d66` | `12c2ad95b70df559dcea7ee1f2cc2d8952ed051475387ab2191a2b3d2b781189` | REVISE | Gate FIFO×generation contradice H2/H5/H12; DPF erróneo; campos `created/updated` no existen y `age_s` ya deriva de `_run_age_s`. |
| 17 | `fb-20260829-104625-7c88` | `4b23aaf6ad48c4648736dffaaeda3294ed61a74b65d9f1c89e9990e0dee994b4` | REVISE | Ciclo con a396; cita worker apunta a kill, no reattach; OWNS solapa worker/request/tool con 8f8c/a396/4407. |
| 18 | `fb-20260829-104630-141e` | `6d869cc89fe89728c20fb080f048d6d59779b22c16ef404f0e0c0468e4630995` | REVISE | Schema interno y contrato público discrepaban en mission; ciclo f201/d366; ownership solapado del artefacto. |
| 19 | `fb-20260829-111016-344d` | `bf327379863a052f98c64708a6a0ba185dafe4b7b2c9da08a5d241e41c3308d2` | REVISE | DPF B4 no cubre LFPowerGrid; gate de paridad innominado y sin mutante independiente en la ficha. |
| 20 | `fb-20260829-115147-4407` | `4f50baaae45c340239a6365ce563b9c8e1134b10bc5ab0babeba4976decfdfd3` | REVISE | Sidecar sin ubicación inequívoca fuera de `storage_1`; fingerprint omite mod principal; DPF/dependencia M04 erróneos; OWNS worker cruza M13. |
| 21 | `fb-20260829-133459-a396` | `56d21615b4a362d1da8dfdff91c424c9b454b4833370a5bf1998954cc0e60275` | REVISE | No implementa preflight real `ActiveProcess` pid/exe/ActiveUser; ciclo 7c88; mission absoluta chocaba H11; DPF erróneo. |
| 22 | `fb-20260829-135408-cc2d` | `bf19f0da4c695b3d5ebfb3b5a3be43a0687b40ab26d838ab3c8becb183a33ca1` | REVISE | DPF debe ser H6/H13; dependencia 7c88 no causal. |
| 23 | `fb-20260829-135727-782b` | `03e2e84f1d2689196f155272f04a8b5934678fff24ec3eb0d7957ded205b3440` | REVISE, superseded | Prepare podía clonar; aceptaba path libre; `load_index` no validaba entries antes de reemplazar. Hash vigente posterior: `f12fadba1012d00aa23e36a5d4e6e858641d52a248b0db1c421a72e69ac0e10f`. |
| 24 | `fb-20260829-184906-21f5` | `7cdb14a9b6aa191bf78f816d48a349ed9118a5aa6d63f325f681f1e3384b9eb9` | REVISE | Bridge ya distingue `not_handled`; ficha usurpaba transporte de b2c4; ciclo; error aplicado a verbos sin OnClick; DPF incorrecto. |
| 25 | `fb-20260829-184952-20be` | `3f493ea3af2b8057d78a1a50baa26a0fbb40d2a9ea15e2b48438e6e377762afd` | REVISE | Rompía lookup legacy a ancestro; espacio de coordenadas no discriminado; ciclos; DPF incorrecto. |
| 26 | `fb-20260829-194752-d366` | `c67a26786303fd213f9872158141538311d2bd72b98ef91bcc32f7c705e7b639` | REVISE | Promoción desde app final ocurría en M09 antes de M15; PASS de wire final irrealizable en esa ventana. |
| 27 | `fb-20260829-194823-ffc7` | `98047dc83260b4bacf56938969e7dae5c2f7341e1bcd8283b35b72e9cdc23180` | REVISE | Ciclo/OWNS con 9d46; autoridad de modos colisiona M13; capas reales omitidas; gate tautológico sin mutante de capa. |
| 28 | `fb-20260829-221423-b2c4` | `704e850e2fe0aad2421e830aef2e2bca4fe7b79e00f4c6432501ec6182697b9e` | REVISE | Matriz de errores no causal para los cuatro verbos; ciclo/OWNS con 21f5; DPF incorrecto. |
| 29 | `fb-20260829-230535-f4f2` | `9aedd6b90b02d12d10c6e7160be6c28ac9fa4eca6592656d647a7cf258d3fddf` | REVISE | Dependencias invertidas/cíclicas; scope `GetMenu()` no descubre raíz `ScriptView`; DPF incorrecto. |
| 30 | `fb-20260830-002237-0de3` | `4218255f04a1a0f38731a70b3856a9acac38d2851f3232ef52d5d5a0b6afc9d0` | REVISE | Citas API falsas; cambiar `ResolveOwnedCar` rompe control; campo nuevo cruza OWNS; seat int/string sin mapa; DPF debe ser C3. |
| 31 | `fb-20260830-010517-9d46` | `8d9b0d2b3088577efdac3146a67a02a2b0ac9a2d1cc4da87a5f098c1a41d9ce7` | REVISE | Ciclo con ffc7; módulo autoridad no reservado y ediciones colisionan M13. |
| 32 | `fb-20260830-011217-668f` | `a3256078bf9fcd2b6201ad5d5104b54fb5f7a2ea446176bb9d4b45d8ae6fa5e1` | REVISE | Mtime/hash contra destino y `PBO preexistente` contradicen staging+publish atómico; OWNS apunta al skill promocionado; DPF B4 incorrecto. |
| 33 | `fb-20260830-112422-2762` | `a5300dc545419b27147c3ea9963843b3be9ef44fa2715db9462b597cf201a174` | REVISE | Nombra tools/campos inexistentes; ciclo/OWNS del resolver con f4f2; DPF incorrecto. |
| 34 | `fb-20260830-112438-40e4` | `9ecefe5af0aceaf854f4aae30c61ad83d73a2be189a85f76008cecc7ac1dbf6b` | REVISE | Cita `HasAnyCargo` en fichero erróneo; DPF debe ser C3; falta happens-before sobre `MCPMessages.c` y schema es M15. |
| 35 | `fb-20260830-112522-1082` | `ddf40cf2ba2268ba63cb33732fc34de898a9261403f1a9141948e1502d715719` | PASS, no promovido | Ninguno tras calibración. Se reemitirá porque el contrato P11/H13 y sus dependencias se congelan de nuevo. |

## Consecuencia de gate

- No se atestigua `PLAN-GROK` para ninguna ficha en esta ronda.
- No se llama a Opus con estos hashes.
- Antes de la ronda 2 se aterrizan B4/C3/D3/E5/H13, se elimina todo ciclo físico y se
  recalculan el DAG, cada plan y el manifiesto.
