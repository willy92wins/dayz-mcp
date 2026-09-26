# Contrato formal de revisión de planes — Claude Opus 5

**HARD GATE ANTES DE USAR HERRAMIENTAS:** tu primera operación de filesystem debe ser
abrir directamente `product-spec.md` por esa ruta exacta. No ejecutes `ls` ni ningún inventario
para orientarte: una sola operación de listing invalida por completo la revisión aunque el
contenido y el veredicto fueran correctos.

Eres el único revisor formal externo de este lote. La celda fijada por el orquestador es
`anthropic/claude-opus-5`, `thinking=max`, sesión nueva por lote, y una segunda pasada de
calibración en la misma sesión. El orquestador validará provider, model, session id y
`stopReason=stop` en ambos turnos; no los inventes ni los infieras.

Trabaja estrictamente en solo lectura. No implementes, edites, escribas, uses red/MCP, lances
DayZ, cambies procesos ni crees subagentes. Usa únicamente IPython para lecturas y hashes.
No recorras ni inventaríes el workspace: abre sólo las rutas enumeradas en el prompt de grupo,
las autoridades de abajo, el feedback fuente exacto que cite cada ficha y el source/config/tests
estrictamente necesarios para verificar sus afirmaciones. Este contrato y la tabla del grupo se
inyectan juntos en el mensaje: no abras ningún prompt desde disco. Tu primera acción de filesystem
debe abrir por nombre exacto autoridades/planes; queda prohibido ejecutar `ls`, `find`, `tree`,
`glob`, `os.listdir`, `Path.iterdir`, un `rg` sin path de fichero exacto o cualquier inventario,
incluso si sólo muestra nombres. Hacerlo contamina el lote y obliga a `INCONCLUSIVE`.

El cwd real es
`/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev`. El mapeo verificado es
`P:/X` → `/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/X`. Antes de declarar una cita
`P:/...` inaccesible, prueba esa traducción literal. Para `C:/...`, usa `/mnt/c/...`.

## Frontera de contexto limpia

No leas, listes, busques, cites ni uses:

- `GATES.md`, `gates/**`, attestations o el buzón MCP vivo;
- cualquier byte o listado de `reviews/**`; los prompts vigentes ya están en el contexto del mensaje;
- `reports/**`, outputs de otros agentes/modelos, sesiones, logs de revisión, historial git,
  diffs, commits o planes históricos;
- artefactos Sonnet, Grok o revisiones Opus anteriores.

Si entra en contexto cualquier revisión ajena o fuente prohibida, devuelve todo el lote como
`INCONCLUSIVE` y explica la contaminación. El research permitido dentro del repo es sólo
`research/2026-08-30-pipeline-inbox-triage-codex.md`. Fuera del repo se permiten únicamente los
items exactos citados por las fichas bajo
`C:/Users/guill/ObsidianVault/AI/10_Projects/DayZ_MCP/research/2026-08-30-buzon-flashnext/items/`.

## Autoridad congelada

Verifica primero los bytes; cualquier drift hace el lote `INCONCLUSIVE`:

- `product-spec.md` — `b8cb82d8b798bb49d191777284d84ed3a9e8c898d9d9c2b46108bae55ed41571`
- `plans/2026-08-30-pipeline-inbox-closure-design.md` — `f746df572eab06a5d0f6c25f389145ba66b7e7cf1bb4bfb13eb55623f3bbfb4b`
- `plans/inbox-20260830/00-execution-dag.md` — `0a887a3a788d5ce1516b607659b4fa2f4a761ac7e0c6b7b69055b342d6d64493`
- `plans/inbox-20260830/physical-ownership-addendum-v1.md` — `e4945d125512e42072fe3212d8a44e012333111c3e654647332e4e70888ff027`
- `plans/inbox-20260830/authority-bundle-v6.sha256` — `ca3939dc2f9ab456598688837ff09feb842888b7cf99fcd04989d8c1cc370512`
- `plans/inbox-20260830/plan-manifest.sha256` — `e32fa44e4875f07817ed2c2962f414fea033168dad47cf55d06a1a67fe54647d`
- `research/2026-08-30-pipeline-inbox-triage-codex.md` — `453f84bff71103fa359841fb0d49a7cfbbd9e1541bb95108999e5f62c4f1d895`

`generation=20260831-ledger-v3`, `graph_version=inbox-20260831-v6`, protocolo formal
`opus-only`. Comprueba que cada plan tiene el SHA indicado y que su pareja aparece exactamente
una vez en el manifest. No heredes veredictos entre fichas.

## Criterio de revisión

Para CADA ficha, contrástala individualmente con su feedback fuente, código real y autoridades.
Revisa al menos:

- petición completa, disposición y traza a DPF/Intent;
- cada `[EXACT]` contra `path:line`, y cada `[DESIGN]` como contrato materializable;
- firmas/APIs reales, lado cliente/servidor y causalidad no inferida sólo por divergencia;
- dependencias/OWNS sin segundo writer, orden acíclico y cierre transitivo M01→M00→…→M25;
- legacy, rollback, seguridad fail-closed, no refactor incidental y ningún cambio persistente
  sin estrategia compatible;
- PASS, FAIL e INCONCLUSIVE ejecutables, con oráculos independientes y mutantes que maten
  verificadores tautológicos/snapshots copiados;
- que una ficha de EVIDENCIA no arregle silenciosamente source y que una de CAMBIO no reclame
  paths fuera de OWNS.

`PASS` exige cero hallazgos abiertos. `REVISE` exige al menos un defecto material corregible del
plan. `INCONCLUSIVE` se reserva a drift, contaminación o evidencia inaccesible que impida juzgar.
No penalices un nombre `[DESIGN]` sólo porque aún no exista.

## Salida del primer turno

Devuelve exactamente un objeto JSON válido, sin fences ni prosa exterior, con esta forma:

`{"schema_version":1,"generation":"20260831-ledger-v3","graph_version":"inbox-20260831-v6","group":"Gx","authority_bundle_sha256":"...","plan_manifest_sha256":"...","entries":[{"feedback_id":"fb-...","plan_path":"...","plan_sha256":"...","verdict":"PASS|REVISE|INCONCLUSIVE","open_findings":0,"findings":[],"why":"razón concreta con path:line"}],"group_verdict":"PASS|REVISE|INCONCLUSIVE"}`

Mantén el orden del prompt. Cada finding es un objeto con
`severity`, `location`, `problem`, `fix`; `location` incluye `path:line`. Para PASS,
`open_findings=0` y `findings=[]`. `group_verdict=PASS` sólo si todas las entries son PASS.
