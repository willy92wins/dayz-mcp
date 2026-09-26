# Prompt Codex — sesión 10 · research dual DIRIGIDO fase 1 (Control)

> Patrón: research dual (R24), brazo Codex. Claude corre 4 Explore en paralelo y **consolida** (Codex no consolida).
> Generado 2026-06-07. Tema: APIs load-bearing de `world_spawn` (B1) / `vehicle_enter` (B2) / `vehicle_drive` (B3).

```
===== PROMPT INICIO =====

Fase 0 de research (R24): research dual DIRIGIDO para la fase 1 (Control) de DayZ-MCP — las APIs
load-bearing de world_spawn (B1), vehicle_enter (B2) y vehicle_drive (B3). Es SOLO research (cero
código). Yo (Claude) corro mi brazo en paralelo; tú corres el tuyo independiente; YO CONSOLIDO
(no consolides tú).

## Carga inicial obligatoria (leer antes de nada)

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md
   (grupo B = los 3 criterios verificables de fase 1 que este research sirve; cláusula de desafío).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-mcp-architecture.md
   (§2 APIs ya leídas en source, §4 tool surface, §8 plan por fases — el diseño cerrado).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-harness-apis.md
   (128 símbolos YA verificados con path:line — EXTIENDE, no re-descubras. Mira §Spawn, §Vehiculos,
   la línea 56-57 sobre HumanCommandVehicle "clase confirmada, métodos a confirmar", y el runbook
   punto 10 sobre símbolos con cero uso en vanilla = red flag).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-07-r21-consolidated.md
   (deuda P2-3 backpressure + P2-4 CreateRestApi-por-tick que fase 1 hereda — dimensión D4).
5. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\verified-apis.md
   (APIs durables ya confirmadas del proyecto — para no repetir; NO escribas aquí, ver boundaries).
6. C:\Users\guill\ObsidianVault\AI\00_System\templates\research-template.md
   (frontmatter + secciones fijas del archivo de salida).

FUENTE VANILLA (única raíz válida para afirmar "vanilla"):
C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\ (1_core, 3_game, 4_world, 5_mission).
El árbol TAMBIÉN tiene mods de terceros con su propio scripts\3_game (CodeLock, ParagonStorage,
tofu_vstorage_2, EFT_Barters, LFQuad, LFPowerGrid, Crate): NO los cites como vanilla; si usas uno
como prior-art, etiquétalo con su path completo.

## Boundaries (independencia + scope)

- NO leas el "-claude.md" del mismo tema
  (C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-06-07-fase1-control-claude.md)
  si aparece — independencia R24, la redundancia es por diseño.
- NO consolides. Produce solo tu "-codex.md" y avisa; Claude consolida ambos con CONFLICT-N.
- NO implementes nada (cero código, cero edición del bridge/config). Es research.
- NO escribas verified-apis.md ni ningún ledger — Claude promueve lo durable al consolidar
  (evita escritura concurrente del ledger; respeta la regla de Read host-direct PRE-WRITE).
- NO re-verifiques lo ya marcado VERIFIED en dayz-harness-apis.md salvo las APIs "a confirmar"
  (los métodos de HumanCommandVehicle).

## Dimensiones (dirigido > amplio — solo las incógnitas load-bearing)

D1 — Sentar al player en el coche (B2, la de mayor riesgo).
En scripts\3_game\human.c y scripts\3_game\vehicles\transport.c: leer el cuerpo real de
`class HumanCommandVehicle` (hint human.c:689) y listar TODOS sus métodos con path:line + firma
literal — confirmar/refutar IsGettingIn(), IsGettingOut(), GetVehicleSeat(), GetVehicleType(),
GetTransport(). Confirmar `StartCommand_Vehicle(Transport, int, int, bool)` (hint :1492) y qué arg
es el asiento de conductor. ¿Existe `Human.GetCommand_Vehicle()` y devuelve no-null solo sentado
(predicado de completitud)? Comparar `StartCommand_Vehicle` vs `Transport.CrewGetIn(Human, posIdx)`
(transport.c:143): ¿cuál es la vía server-side robusta? Buscar el flujo get-in canónico vanilla
(ActionGetInTransport / GetInTransport / CrewGetIn) y citar las llamadas clave. Cómo obtener el
`Human` del player server-side (cadena PlayerBase -> ... -> Human).

D2 — Conducir el coche por script (B3).
En scripts\3_game\vehicles\car.c (+ CarScript en 4_world): estado inicial de un coche recién
spawneado (handbrake/gear/engine). Secuencia mínima para moverse (hipótesis:
EngineStart -> SetHandbrake(0) -> ShiftTo(CarGear.FIRST) -> SetThrottle): confirmar orden +
precondiciones de EngineStart (fuel/batería/bujía/llave). Unidades de GetSpeedometer() (:113) y
umbral de "se mueve". ¿`SetThrottle` es autoritativo SERVER-side o la sim del coche está
client/physics-gated? (citar el entrypoint de simulación EOnSimulate/OnSimulation en car.c).
PRIOR-ART: LFQuad (C:\Users\guill\OneDrive\Documentos\DayZ Projects\LFQuad\ y LFQuad_dev\, es
CarScript) — buscar uso real de EngineStart/SetThrottle/ShiftTo/SetHandbrake y citar la secuencia
(path:line, etiquetado prior-art).

D3 — Spawn verificable + escena determinista (B1 + drift).
En scripts\3_game\global\game.c y scripts\3_game\ce\centraleconomy.c: enumerar el set completo de
flags ECE_* y RF_* (path:line + valor) y recomendar el iFlags para spawn server-side persistente,
on-surface, físico y verificable. Encontrar la API de existencia server-side:
GetObjectsAtPosition / GetObjectsAtPosition3D — firma real en game.c (path:line). Drift: el player
de test derivó ~9 m durante el run; encontrar cómo se setea la pos en spawn (CreateCharacter
missionserver.c:486; DeveloperTeleport.SetPlayerPosition developerteleport.c:115) y la causa
probable del slide; y las APIs concretas para fijar una pos estable determinista (SetPosition +
breakSync, dBody*, DisableSimulation, re-aserción por tick).

D4 — Deuda heredada para comandos con side-effects (P2-3/P2-4).
Leer el bridge real: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\
(MCPBridge.c, MissionServer.c) y el server C:\Users\guill\OneDrive\Documentos\DayZ Projects\
DayZ_MCP_dev\tools\ (mcp_server.py). (a) Punto de extensión exacto donde se añade un comando nuevo
al dispatch del tick (path:line) y cómo modela readiness/correlation-id hoy. (b) P2-4: leer
`CreateRestApi` (hint restapi.c:181) — ¿es get-or-create idempotente (singleton) o crea instancia
nueva cada llamada? ¿hay GetRestApi()? ¿DestroyRestApi() mata un singleton global? ¿RestContext.reset()
en :133? Veredicto: ¿llamarla por-tick es seguro? (c) P2-3: ¿hay tope/budget en cola/batch/results
(mcp_server.py ~:22-23,151; MCPBridge.c ~:179)? Dónde una cola/batch sin tope es problema con
comandos que mutan estado.

## Restricciones críticas

1. R2 cite-then-verify: cada hecho con path:line LEÍDO del archivo real (abre el archivo; el snippet
   de grep es hint, no prueba). Lo no verificable offline va a "Suposiciones / UNCONFIRMED", no a
   "Hechos verificados". Usa la raíz vanilla; rechaza hits bajo carpetas de mod salvo prior-art
   etiquetado.
2. R24 independiente (de tu AGENTS.md): tu brazo no lee el mío; no consolidas.
3. R11 tono: conclusión arriba, sin floritura.
4. Dirigido > amplio: foco en las 4 incógnitas; no barrido de catálogo (eso ya está en
   dayz-harness-apis.md).
5. Frontmatter de la plantilla completo (researcher: codex, date: 2026-06-07, status: draft).

## Output esperado

### Bloque A — Archivo de salida
C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-06-07-fase1-control-codex.md,
con TODAS las secciones de research-template.md rellenas. Separa "Hechos verificados" (con path:line)
de "UNCONFIRMED / needs in-game spike".

### Bloque B — Verificación de citas
Por cada API load-bearing afirmada, el path:line exacto y la firma literal. Marca explícitamente
cuáles NO pudiste confirmar offline (esp. si SetThrottle mueve el coche server-side — probable
spike in-game).

### Bloque C — Hallazgos / conflictos potenciales
Cualquier choque con el diseño actual (architecture/product-spec) o entre lo que esperabas y lo que
dice el source. Si ninguno: "Sin hallazgos".

### Bloque D — Recomendación para el plan (fase 1)
Tu voto, con razón citada, en las 4 preguntas decisivas:
a) Asiento (B2): ¿StartCommand_Vehicle o CrewGetIn? + predicado de completitud exacto.
b) Conducir (B3): ¿autoritativo server-side o necesita spike? + secuencia mínima de arranque +
   predicado de movimiento.
c) Spawn (B1): iFlags recomendado + API de existencia + mitigación de drift para escena determinista.
d) Deuda: veredicto P2-4 (CreateRestApi idempotente/seguro por-tick) + diseño de backpressure P2-3
   para comandos con side-effects.

Cuando termines tu "-codex.md", NO consolides: avisa al usuario y Claude consolidará leyendo ambos
brazos, produciendo 2026-06-07-fase1-control.md con CONFLICT-N marcados.

===== PROMPT FIN =====
```
