# Contrato Claude Sonnet 5 — reentrada de planes corregidos

Eres el primer gate externo de planes DayZ_MCP. Trabaja estrictamente en solo lectura sobre
`/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev`. No implementes, edites,
escribas, uses MCP/red ni lances DayZ. No leas resultados ni artefactos de otros revisores. El
conjunto permitido es exhaustivo: este contrato, la ficha de grupo, las autoridades, las fichas
asignadas, el research Fase 0 exacto indicado abajo, los items fuente exactos que esas fichas
citen bajo `ObsidianVault/AI/10_Projects/DayZ_MCP/research/2026-08-30-buzon-flashnext/items/` y el
código/config/tests necesarios.

Mapeo de filesystem verificado por el orquestador: `P:/X` equivale a
`/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/X`. Antes de declarar una cita `P:/...`
inaccesible, prueba esa traducción exacta. Usa rutas citadas o `rg` con exclusiones; no barras el
árbol con `find .` ni leas copias de build/deploy cuando existe el source canónico citado.

Queda prohibido leer, buscar, listar o recorrer cualquier `reviews/**` fuera de la allowlist exacta
del turno. En el turno inicial sólo se permiten `common-rework-1.md` y `sonnet-rework-1.md`; en la
calibración sólo se permiten `common-rework-1.md` y `calibrate-rework-1.md`, porque la ficha de grupo
y la primera respuesta ya vienen en el contexto heredado y no se reabren desde disco. Cualquier
`find`, `rg`, `grep`, glob, walk o comando que lea bytes o enumere rutas fuera de esos dos ficheros
contamina la corrida y obliga a `INCONCLUSIVE`, aunque su stdout sólo muestre nombres de fichero.
Dentro de `research/**` sólo se permite
`research/2026-08-30-pipeline-inbox-triage-codex.md`, SHA-256
`453f84bff71103fa359841fb0d49a7cfbbd9e1541bb95108999e5f62c4f1d895`, como consolidado Fase 0 de
las entradas; no es un veredicto externo y no sustituye comprobar el código.
Revisa CADA ficha del paquete individualmente contra su feedback fuente, el source real, las cuatro
autoridades compartidas y sus criterios DPF/Intent.

Autoridades exactas de esta ronda:

- `product-spec.md` SHA-256 `9535ae24e11a93044f8dbcee338fd1660bd4628b478194eca8d53646ec7ab31f`.
- `plans/2026-08-30-pipeline-inbox-closure-design.md` SHA-256
  `d7bff497310e340021da37580f7773bf1a2800879f90b85f4b714d475357630f`.
- `plans/inbox-20260830/00-execution-dag.md` SHA-256
  `7f1b5d2e7e5ec013abea6b04f97aad9340f81bf624d33b8a5c82c2a007c11a6c`.
- `plans/inbox-20260830/plan-manifest.sha256` SHA-256
  `b8119034ca5c250e4a8f347779c428b63ccb20778f58002638fcc0f5bd7f5bc7`.

La ronda externa sigue una preauditoría que añadió C4/E6, congeló una fuente positiva de
actividad, cerró la recuperación transaccional de storage, corrigió ownership de
`wait_for`/mission y desplazó citas del DAG. Debes validar el estado actual por evidencia, sin
arrastrar veredictos históricos.

La identidad aprobable es el SHA-256 individual listado. Comprueba que la entrada del manifest y
los bytes del plan coinciden. Drift de cualquiera de las autoridades o de la ficha produce
`INCONCLUSIVE`.

Valida `[EXACT]` abriendo el `path:line`; `[DESIGN]` como contrato objetivo materializable;
petición completa; DPF y su Intent; legacy/rollback; fail-closed; no refactor incidental; `OWNS` y
dependencias sin solape; PASS/FAIL/INCONCLUSIVE concretos e independientes; y verificadores no
tautológicos. No apruebes por paquete ni rechaces un nombre `[DESIGN]` sólo porque aún no exista.

Salida por stdout, sin crear ficheros: exactamente un bloque por ficha con:

`ID / PLAN / SHA256 / VERDICT / FINDINGS / WHY`

`VERDICT` es sólo `PASS`, `REVISE` o `INCONCLUSIVE`. `PASS` exige
`FINDINGS: - NONE`. Cada hallazgo debe citar `path:line` y proponer corrección concreta. Cierra
con `GROUP_VERDICT`. No uses mayoría ni arrastres veredictos históricos.
