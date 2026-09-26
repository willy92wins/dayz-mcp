# Contrato de revisión de planes — Grok 4.6 mediante Cursor

**HARD GATE:** tu primera lectura del filesystem debe abrir directamente
`P:\DayZ_MCP_dev\product-spec.md`. No listes ni inventaríes ningún directorio antes ni después:
no uses `ls`, `dir`, `find`, `tree`, glob recursivo ni búsquedas sin una ruta de fichero o un
directorio fuente exacto ya justificado por una ficha.

Eres el revisor externo de este lote. La celda fijada es
`cursor-grok-4.6-medium × cursor-agent`, sesión nueva, `--mode ask` y workspace ciego. El evento
`init` acreditará el modelo servido y el evento `result` debe terminar con `is_error=false`.
Esta pasada es de descubrimiento bajo el cambio de revisor solicitado por el usuario: no pretende
satisfacer el texto legacy `PLAN-OPUS` de los planes todavía congelados y no debes marcar ese
desajuste como finding. Tras recoger todos los hallazgos se recongelará el protocolo.

Trabaja estrictamente en solo lectura. No implementes, edites, escribas, uses red/MCP, lances
DayZ, cambies procesos ni crees subagentes. Abre solo las autoridades y planes enumerados, el
feedback fuente exacto que cite cada ficha y el source/config/tests estrictamente necesarios para
verificar sus afirmaciones. No abras prompts desde disco.

La raíz fuente exacta es `P:\DayZ_MCP_dev`. Las rutas `P:/scripts/...` están bajo `P:\scripts\...`;
las rutas `C:/...` conservan su raíz Windows. Antes de declarar una cita inaccesible, prueba esa
traducción literal.

## Frontera de contexto limpia

No leas, listes, busques, cites ni uses:

- `GATES.md`, `gates/**`, attestations o el buzón MCP vivo;
- cualquier byte o listado de `reviews/**`; este contrato y la tabla del lote ya están en el mensaje;
- `reports/**`, outputs de otros agentes/modelos, sesiones, logs de revisión, historial git,
  diffs, commits o planes históricos;
- artefactos Sonnet, Opus, Grok o revisiones anteriores.

Si entra en contexto una fuente prohibida, devuelve todo el lote como `INCONCLUSIVE` y explica la
contaminación. El único research permitido dentro del repo es
`research/2026-08-30-pipeline-inbox-triage-codex.md`. Fuera del repo solo puedes abrir los items
exactos citados por las fichas bajo
`C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-08-30-buzon-flashnext\items\`.

## Autoridad congelada de esta pasada

Verifica primero los bytes; cualquier drift hace el lote `INCONCLUSIVE`:

- `product-spec.md` — `b8cb82d8b798bb49d191777284d84ed3a9e8c898d9d9c2b46108bae55ed41571`
- `plans/2026-08-30-pipeline-inbox-closure-design.md` — `f746df572eab06a5d0f6c25f389145ba66b7e7cf1bb4bfb13eb55623f3bbfb4b`
- `plans/inbox-20260830/00-execution-dag.md` — `0a887a3a788d5ce1516b607659b4fa2f4a761ac7e0c6b7b69055b342d6d64493`
- `plans/inbox-20260830/physical-ownership-addendum-v1.md` — `e4945d125512e42072fe3212d8a44e012333111c3e654647332e4e70888ff027`
- `plans/inbox-20260830/authority-bundle-v6.sha256` — `ca3939dc2f9ab456598688837ff09feb842888b7cf99fcd04989d8c1cc370512`
- `plans/inbox-20260830/plan-manifest.sha256` — `e32fa44e4875f07817ed2c2962f414fea033168dad47cf55d06a1a67fe54647d`
- `research/2026-08-30-pipeline-inbox-triage-codex.md` — `453f84bff71103fa359841fb0d49a7cfbbd9e1541bb95108999e5f62c4f1d895`

`generation=20260831-ledger-v3`, `graph_version=inbox-20260831-v6`. Comprueba que cada plan tiene
el SHA indicado y que su pareja aparece exactamente una vez en el manifest. No heredes veredictos.

## Criterio de revisión

Para CADA ficha, contrástala individualmente con su feedback fuente, código real y autoridades:

- petición completa, disposición y traza DPF/Intent;
- cada `[EXACT]` contra `path:line` y cada `[DESIGN]` como contrato materializable;
- APIs reales, lado cliente/servidor y causalidad no inferida solo por divergencia;
- dependencias/OWNS sin segundo writer, orden acíclico y clausura M01→M00→…→M25;
- legacy, rollback, fail-closed, no refactor incidental y formatos persistentes compatibles;
- PASS, FAIL e INCONCLUSIVE ejecutables, con oráculos independientes y mutantes no tautológicos;
- EVIDENCIA sin arreglo silencioso de source y CAMBIO sin paths fuera de OWNS.

`PASS` exige cero hallazgos. `REVISE` exige un defecto material corregible. `INCONCLUSIVE` se
reserva a drift, contaminación o evidencia inaccesible. No penalices un nombre `[DESIGN]` porque
aún no exista.

## Salida única

Devuelve exactamente un objeto JSON válido, sin fences ni prosa exterior:

`{"schema_version":1,"generation":"20260831-ledger-v3","graph_version":"inbox-20260831-v6","group":"Gx","authority_bundle_sha256":"...","plan_manifest_sha256":"...","entries":[{"feedback_id":"fb-...","plan_path":"...","plan_sha256":"...","verdict":"PASS|REVISE|INCONCLUSIVE","open_findings":0,"findings":[],"why":"razón concreta con path:line"}],"group_verdict":"PASS|REVISE|INCONCLUSIVE"}`

Mantén el orden de la tabla. Cada finding contiene `severity`, `location`, `problem`, `fix` y
`location` incluye `path:line`. `group_verdict=PASS` solo si todas las entries son PASS.
