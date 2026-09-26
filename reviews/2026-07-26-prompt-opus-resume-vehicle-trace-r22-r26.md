# Prompt para Opus — reanudar `vehicle_trace` R22/R26

```text
===== PROMPT INICIO =====

Sesión nueva de Claude Opus. Retoma DayZ_MCP y ejecuta únicamente la Fase
A0→A4 del plan R22/R26 final. Orquesta la sesión y delega la implementación
material a Codex conforme a R20. No inicies Fase B, live, deploy, Mercedes S1
ni ningún arreglo del coche.

Al empezar, declara exactamente:

`Retomo DayZ_MCP desde: vehicle_trace v6 offline/PACKONLY GREEN, live RED y R22 READY solo para diagnóstico · próxima acción: orquestar Fase A0→A4 y cerrar DIAGNOSTIC READY / STOP sin live ni Mercedes.`

PRE-VERIFY — antes de cualquier mutación

1. Ejecuta `session_status` mediante DayZ MCP, solo lectura. El último snapshot
   autoritativo dejó el run `bf8db6aa-564f-4477-b1fa-373ae63ad9bf` en
   `EXITED`, `processes=[]`, owner null y cola vacía. En la sesión que creó este
   handoff, `session_status` devolvió `credential_source_untrusted`; host-direct
   mostró cero listener TCP 8765, cero daemon y cero ejecutables DayZ. No
   atribuyas causalidad sin evidencia, no mates procesos y no inicies daemon.
2. Verifica que la configuración persistente de Claude usa
   `--idle-timeout 600`. En el cierre Codex, un cliente recién creado siguió
   saliendo con argv `1800`; no presupongas que el proceso vivo ya tomó la
   configuración. Si tu cliente usa otro argv, regístralo; Fase A sigue siendo
   exclusivamente offline y no usa lifecycle.
3. Verifica estos hashes:
   - plan R22/R26:
     `E1E6C7F9398D0D410D079968AB3A6DE48659722CAD5E614D9DFFC132D010082F`;
   - informe live corregido:
     `6796A488A703F63AF707585CC4151445371900A6094543286E4286178036D480`;
   - feature spec:
     `DDD795CDA34C4E95411C078C1CFC25156E934FBAD4E4C3BAF1AFF876BECC7B26`.
   Si difiere cualquiera, declara RED, no edites y pregunta.

CARGA INICIAL MÁXIMA — lee solo estos siete archivos, en este orden

1. `C:\Users\guill\ObsidianVault\AI\00_System\workflow.md`
2. `C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-07-26-DayZ_MCP-vehicle-trace-r22-r26-ready.md`
3. `P:\DayZ_MCP_dev\CLAUDE.md`
4. `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\project-brief.md`
5. `P:\DayZ_MCP_dev\plans\2026-07-26-vehicle-trace-cadence-oncontact-r22-r26.md`
6. `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md`
7. `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\verified-apis.md`

No releas research antiguo, revisiones históricas ni el log archivado de
`HANDOFF.md`. Después de la carga inicial y antes de editar, lee completos como
archivos de trabajo:

- `P:\DayZ_MCP_dev\product-spec.md`
- `P:\DayZ_MCP_dev\plans\2026-07-25-vehicle-trace-feature-spec.md`
- `P:\DayZ_MCP_dev\reviews\2026-07-26-vehicle-trace-live20-red.md`
- `P:\DayZ_MCP\scripts\4_World\MCP_CarScript.c`
- `P:\DayZ_MCP_dev\tools\tests\test_vehicle_trace.py`
- `P:\DayZ_MCP_dev\tools\tests\test_vehicle_trace_contract.py`
- `C:\Users\guill\ObsidianVault\AI\20_Runbooks\onedrive-write-safe.md`
- `C:\Users\guill\ObsidianVault\AI\20_Knowledge\dayz-mod-implementation-checklists.md`

Lee y aplica `dayz-mod-workflow` antes del primer diff Enforce y
`dayz-pbo-build` justo antes de PACKONLY. Aplica R2 cite-then-verify a cada API,
firma, selección, índice y contrato.

OBJETIVO ÚNICO

Ejecutar exactamente Fase A0→A4 del plan final y terminar con uno de estos dos
veredictos:

- `DIAGNOSTIC READY / STOP`: `CHAR` y controles GREEN, `INSTR` RED→GREEN,
  `ACC` RED causal preservado, PACKONLY doble sin deploy y R21 dual sin
  `HIGH/GATE` abierto.
- `DIAGNOSTIC RED`: cualquier falso verde, scope drift, compilación/config
  inválida, PBO no inventariable, rollback incompleto o R21 abierto.

SECUENCIA OBLIGATORIA

1. A0: staging nuevo bajo `C:\tmp`, manifest y SHA-256 baseline. Confirma que
   no hay mutación concurrente en los targets.
2. A1: crea primero los dos fixtures y los tests. Ejecuta por separado:
   - `CHAR`: exit 0 y reproduce `<20`/STOP actual;
   - `INSTR`: exit 1 porque falta instrumentación;
   - `ACC`: exit 1 causal y permanece RED durante toda Fase A.
   Un import error, typo o fixture malformed no cuenta como RED causal.
3. A2: Codex puede modificar únicamente
   `P:\DayZ_MCP\scripts\4_World\MCP_CarScript.c` para añadir los escalares
   diagnósticos y marker one-shot descritos por el plan. No cambies scheduler,
   timestamps, clasificación, schema, DTO, bridge, course ni validator.
4. A3: `INSTR` pasa a GREEN; `ACC` debe seguir RED con los mismos IDs. Ejecuta
   suite afectada, source-contract, compile y PACKONLY dos veces desde staging
   fresco. No despliegues la PBO.
5. A4: congela diff y outputs. Lanza en paralelo una revisión Codex fresca y tu
   revisión Claude independiente; no coordines hallazgos antes de ambos
   veredictos. Consolida después. Cualquier `HIGH/GATE` sin resolver produce
   `DIAGNOSTIC RED`.
6. STOP. No solicites ni ejecutes Fase B/live dentro de esta sesión.

ALCANCE PERMITIDO

- `P:\DayZ_MCP_dev\tools\tests\test_vehicle_trace.py`
- `P:\DayZ_MCP_dev\tools\tests\test_vehicle_trace_contract.py`
- los dos fixtures nuevos exactos del plan
- `P:\DayZ_MCP\scripts\4_World\MCP_CarScript.c`
- staging nuevo, PACKONLY no desplegada, reporte, memoria y handoff

PROHIBIDO

- Tocar `vehicle_trace.py`, schema, course, DTO, bridge version, RPC, SyncVar,
  `MCPClientBridge.c`, `MCPMessages.c` o server peer.
- Rebajar 20 Hz, redondear `19.9689`, ampliar epsilon, sintetizar
  muestras/timestamps o sustituir `OnContact` por señales genéricas.
- Tocar Mercedes, iniciar S1, desplegar PBO, abrir VPP, adquirir lease o lanzar
  DayZ/lifecycle/live.
- Refactor incidental, modificar skills/runbooks o absorber silenciosamente en
  Opus la implementación que corresponde a Codex.

ESCRITURA Y CIERRE

- Para cualquier `.py` bajo OneDrive, trabaja en staging fuera de OneDrive y
  copia/verifica host-direct según el runbook.
- Cada línea modificada debe trazar al plan; todo snippet en documento lleva
  `[EXACT]` o `[DESIGN]`.
- Si los árboles no son repos Git, registra el motivo y usa manifest+hashes; no
  simules commit.
- Cierra con conclusión arriba, tabla R26 real, comandos/resultados, archivos y
  hashes, PACKONLY, rollback, dos veredictos R21, consolidación, riesgos y
  denegación explícita de Fase B/S1.
- Actualiza memoria durable, `P:\DayZ_MCP_dev\HANDOFF.md` y un handoff nuevo en
  `AI\30_Sessions`; ejecuta `session_status` postflight. Si sigue degradado,
  documenta exactamente el error y no fuerces lifecycle.
- Registra mejoras de skills solo como propuestas evidence-backed; no
  modifiques skills/runbooks durante el sprint.

No declares SC-015 GREEN, no autorices S1 y no conviertas un diagnóstico
offline en evidencia del lado real de `OnContact`.

===== PROMPT FIN =====
```
