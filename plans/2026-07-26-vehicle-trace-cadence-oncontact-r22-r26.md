# Plan correctivo R22/R26 — `vehicle_trace` cadence + lado de `OnContact`

**Fecha:** 2026-07-26  
**R22:** **READY** — únicamente para ejecutar la Fase A offline/PACKONLY de este plan  
**Gate vivo vigente:** **RED / STOP** — SC-015 y S1 continúan denegados  
**Implementación de producto Mercedes:** prohibida  
**Live, deploy y lifecycle en la Fase A:** prohibidos  
**Plan precedente:** `plans/2026-07-25-vehicle-trace-atomic-instrumentation.md`  
**Evidencia causal:** `reviews/2026-07-26-vehicle-trace-live20-red.md`

## Conclusión

El plan precedente no está listo para aplicar directamente otro supuesto fix ni
para repetir el gate live: sus pruebas offline no modelan el desacople
`OnInput.dt` ↔ `GetTickTime()` que termina validándose como frecuencia efectiva,
y su source-contract demuestra que existe una llamada a `CaptureContact`, pero
no en qué proceso invoca el engine el callback.

Este plan queda **R22 READY** porque convierte ambos unknowns en fixtures,
controles y gates fail-closed antes de cambiar comportamiento. La autorización
se limita a la menor fase operable: caracterización que detecta correctamente
el STOP actual, acceptance correctiva definida y conservada RED, instrumentación
diagnóstica escalar con su source-contract RED→GREEN y PBO PACKONLY no
desplegada. El resultado terminal de esa fase es
**DIAGNOSTIC READY / STOP**, nunca SC-015 GREEN. Después se detiene y entrega
evidencia. Un live diagnóstico exige autorización posterior.

Si el probe posterior demuestra que `OnContact` se invoca solo en servidor, el
plan termina `RED / NEEDS PRODUCT DECISION`: transportar ese dato al owner client
choca con el alcance vigente y no se resolverá implícitamente mediante RPC,
SyncVar, telemetría server-side ni una señal de contacto genérica.

## 1. Trazabilidad DPF e Intent

No se añade criterio de producto ni se modifica el umbral.

| Trabajo | Contrato al que traza | Evidencia |
|---|---|---|
| Mantener un stream owner-client útil para iterar coches conducibles | Intent G: cerrar el bucle de coches conducibles vía MCP | `P:\DayZ_MCP_dev\product-spec.md:102-106` |
| Cadence larga ≥20 Hz y contacto corporal owner-client | G3 exige control live `CivilianSedan` ≥2 s, ≥20 Hz efectivos y al menos un contacto corporal owner-client | `P:\DayZ_MCP_dev\product-spec.md:115` |
| Cadence exacta, reloj y gap | SC-005 fija `(n-1)/(last-first) >= max(20, 0.9*sample_hz)` y gap `<=1.5/sample_hz` | `P:\DayZ_MCP_dev\plans\2026-07-25-vehicle-trace-feature-spec.md:56` |
| Hook sin allocations y evidencia física real | SC-007 prohíbe `new` en hooks; SC-008 define grounding desde `OnContact` no-wheel | `P:\DayZ_MCP_dev\plans\2026-07-25-vehicle-trace-feature-spec.md:58-59` |
| Gate terminal | SC-012 exige PACKONLY, 20 Hz efectivos y contacto corporal owner-client | `P:\DayZ_MCP_dev\plans\2026-07-25-vehicle-trace-feature-spec.md:63` |
| Límite arquitectónico | Telemetría server-side sigue fuera de alcance | `P:\DayZ_MCP_dev\plans\2026-07-25-vehicle-trace-feature-spec.md:73-78` |

La cláusula de desafío se activa solo si el callback es server-only: G3 pide
owner-client, mientras el alcance excluye telemetría server-side. Ese resultado
requiere adjudicación humana; no autoriza ampliar arquitectura.

## 2. Estado factual preservado

- El mejor trace retenido tiene 207 muestras en 10.316040 s:
  `19.96890272042031 Hz`, gap máximo `0.06298828125 s`,
  schedule/owner/net-id/readback PASS y `body_contact_count=0`;
  `reviews/2026-07-26-vehicle-trace-live20-red.md:10-18,34-35,140-146`.
- Los únicos checks terminales del artefacto son `effective_hz` y
  `course_observation_body_contact_owner_client`;
  `reviews/2026-07-26-vehicle-trace-live20-red.md:166-174`.
- Cinco intentos no observaron body contact; esto refuta el supuesto como gate
  satisfecho, pero no prueba todavía el lado del callback;
  `reviews/2026-07-26-vehicle-trace-live20-red.md:233-241`.
- El validador vigente calcula frecuencia desde timestamps reales y mantiene el
  floor 20 sin redondeo permisivo;
  `P:\DayZ_MCP_dev\tools\dayz_mcp\vehicle_trace.py:613-632`.
- Grounding se deriva solo de la suma de `body_contact_count`;
  `P:\DayZ_MCP_dev\tools\dayz_mcp\vehicle_trace.py:583,655-656,1049-1058`.
- `OnInput(float dt)` se documenta como ejecutado tras cada paso de input;
  `P:\scripts\3_game\vehicles\transport.c:254-262`.
- `GetTickTime()` devuelve segundos desde el inicio del juego;
  `P:\scripts\3_game\global\game.c:909-913`.
- `OnContact(string, vector, IEntity, Contact)` es la firma base;
  `P:\scripts\3_game\vehicles\transport.c:244-252`.
- El cuerpo vanilla de `CarScript.OnContact` procesa su cache bajo
  `g_Game.IsServer()`; esto es evidencia de riesgo de lado, no prueba de que el
  engine omita la invocación cliente;
  `P:\scripts\4_world\entities\vehicles\carscript.c:1453-1480`.

## 3. Gate R22 — hallazgos

### R22-01 — HIGH/GATE: el test de cadence no cubre el contrato temporal real

El productor decide con `s_AccumS += dt`, pero timestampa con
`GetTickTime()` y solo resta un intervalo por llamada:
`P:\DayZ_MCP\scripts\4_World\MCP_CarScript.c:205-241`.
El test actual usa un cálculo Python con `frame_dt_s` y divide por el mismo
tiempo sintético; nunca proporciona una segunda secuencia de reloj:
`P:\DayZ_MCP_dev\tools\tests\test_vehicle_trace_contract.py:155-171`.

**Impacto:** puede quedar GREEN aunque el productor real entregue <20 Hz bajo el
reloj que después valida el host.

**Cierre exigido:** fixture dual `input_dt_s` + `clock_s`; un test de
caracterización queda GREEN al detectar el `<20` actual, mientras un test de
acceptance separado queda RED hasta que Fase C implemente un productor
candidato. Ambos se ejecutan antes de editar el scheduler.

### R22-02 — HIGH/GATE: la presencia del hook no acredita su lado de ejecución

El test estructural solo exige que el override llame
`MCPVehicleTrace.CaptureContact(...)`:
`P:\DayZ_MCP_dev\tools\tests\test_vehicle_trace_contract.py:142-153`.
El engine declara el callback en
`P:\scripts\3_game\vehicles\transport.c:244-252`, pero vanilla limita su trabajo
a `g_Game.IsServer()` en
`P:\scripts\4_world\entities\vehicles\carscript.c:1453-1480`.

**Impacto:** un hook correcto en source puede ser inalcanzable en la VM
owner-client. El fixture host positivo inyecta manualmente un body contact
(`tools\tests\fixtures\vehicle_trace\positive_20hz.json:81-94`) y por tanto no
prueba dispatch del engine.

**Cierre exigido:** instrumentación one-shot por proceso y matriz de resultados
server/client/trace. Server-only es STOP de arquitectura.

### R22-03 — MEDIUM/EVIDENCE: exit code narrado no coincide con el contrato actual

El informe dice que los artefactos `status=STOP` devolvieron exit `1`
(`reviews\2026-07-26-vehicle-trace-live20-red.md:166-174`), mientras el CLI
vigente fija `PASS=0`, `FAIL=1`, `STOP=2`, input inválido `=4`
(`tools\vehicle_trace_artifact.py:27-42`) y el test espera 2
(`tools\tests\test_vehicle_trace.py:506-533`).

**Impacto:** el handoff podría automatizar el gate con una semántica equivocada.

**Cierre exigido:** pin offline de los cuatro exit codes sobre bytes actuales.
No cambia el contrato; corrige la evidencia.

### R22-04 — PASS: DPF, umbrales y alcance no requieren enmienda previa

G3 y SC-005/SC-012 ya contienen criterios binarios y medibles. No se autoriza
rebajar 20 Hz, redondear `19.9689` a PASS, sintetizar timestamps, sustituir
`OnContact` por wheel-contact/velocidad/raycast ni tocar Mercedes.

## 4. Hipótesis y discriminadores

Las hipótesis son mutuamente comprobables; ninguna se trata como causa cerrada.

### Cadence

| ID | Hipótesis | Evidencia actual | Discriminador |
|---|---|---|---|
| CAD-H1 | `dt` acumulado y `GetTickTime()` no avanzan idénticamente en ventanas largas | El productor usa ambos dominios (`MCP_CarScript.c:217-235`); el host mide solo clock (`vehicle_trace.py:613-631`) | Fixture dual con drift acotado: misma secuencia `dt`, dos secuencias `clock`; registrar `sum_input_dt`, span real y deuda de acumulador |
| CAD-H2 | Un `dt` grande deja backlog porque solo se emite una muestra por `OnInput` | Hay una resta y un único `CaptureNow` por llamada (`MCP_CarScript.c:233-241`) | Fixture con burst `dt >= 2*interval`; medir deuda máxima y cadence real sin fabricar muestras |
| CAD-H3 | El sesgo depende del borde inicial/final, no de pérdida sostenida | El cálculo normativo usa `n-1` sobre first↔last (`vehicle_trace.py:613-628`) | Controles de 2 s, 10 s y 30 s con distintas fases iniciales; todos deben cumplir floor 20 |

### `OnContact`

| ID | Hipótesis | Resultado discriminante |
|---|---|---|
| CON-H1 | El engine invoca `OnContact` solo en servidor | marker server >0, marker client =0 para el mismo net-id; STOP y adjudicación, sin bridge implícito |
| CON-H2 | El curso no produjo una colisión física verificable | marker server =0 y client =0; gate inconcluso/RED, corregir solo setup del probe |
| CON-H3 | El callback llega al cliente, pero el estado/filtro no lo conserva | marker client >0 y trace `contact_count=0`; bug local en hook/trace, apto para fix acotado |
| CON-H4 | Llegan contactos, pero todos se clasifican como wheel | trace `contact_count>0`, `body_contact_count=0`; revisar únicamente clasificación exacta de `zoneName`, no sustituir la fuente |
| CON-H5 | El camino vigente es viable | marker client >0 y trace body >0 para el mismo coche/ventana; no cambiar arquitectura |

## 5. R26 — fixtures y viability gates obligatorios

Todos los tests de esta tabla se escriben y ejecutan **antes** del cambio que
pretenden validar. Hay tres categorías que no se mezclan:

1. **CHAR**: el harness queda GREEN si reproduce y clasifica correctamente el
   STOP vigente.
2. **INSTR**: source-contract RED→GREEN exclusivamente por añadir
   instrumentación escalar/marker, sin cambiar cadence.
3. **ACC**: acceptance correctiva ejecutada RED en A1 y conservada RED durante
   toda la Fase A; solo puede pasar a GREEN en Fase C tras R22 corto.

Que un negativo normativo produzca `STOP` es PASS del test. A3 no exige ni
permite que los tests `ACC` queden GREEN.

| ID | Fixture/control a crear primero | RED esperado en bytes actuales | GREEN/resultado esperado tras su fase | Exit |
|---|---|---|---|---|
| VT-CAD-CHAR-01 caracterización causal | `cadence_clock_domain_cases.json`: `dt` nominal 20 Hz + clock ligeramente más lento, ventana 10 s | el replay obtiene `<20` y el test queda GREEN por detectar el defecto | continúa GREEN en Fase A; cualquier cambio del resultado exige volver a R22 | unittest 0 |
| VT-CAD-CHAR-02 control positivo | mismo fixture, `dt` y clock alineados | PASS del control; si falla, fixture inválido y STOP | continúa PASS | unittest 0 |
| VT-CAD-ACC-01 acceptance causal | misma entrada de CHAR-01, pero exige muestras físicas `>=20 Hz`, gap `<=0.075 s` y timestamps reales | RED causal | permanece RED en A1-A3; solo Fase C puede llevarlo a GREEN | unittest 1 hasta Fase C; luego 0 |
| VT-CAD-ACC-03 backlog | burst de `dt` con deuda >1 intervalo y clock monotónico | RED si el productor no cumple sin fabricar estados | permanece RED en Fase A | unittest 1 hasta Fase C, o STOP de diseño |
| VT-CAD-ACC-04 longitudes/fase | ventanas 2/10/30 s y tres offsets iniciales | al menos el caso equivalente al blocker queda RED | permanece RED en Fase A; Fase C exige todos `>=20 Hz` | unittest 1 hasta Fase C; luego 0 |
| VT-CAD-N1 anti-relax | trace exacto `19.96890272042031 Hz` con resto válido | validator `STOP` | sigue `STOP`; fórmula y `TIME_EPS_S` no se cambian | artifact 2 |
| VT-CAD-N2 30 Hz | fixture existente de request 30 con 22.5 Hz | `STOP effective_hz` | sigue `STOP` | unittest 0 / artifact 2 |
| VT-CAD-N3 no sintético | source-contract exige `sample.monotonic_s = nowS` y `sample_dt_s = nowS-s_LastSampleS` | control vigente PASS | sigue PASS; prohíbe timestamp/deadline programado como dato observado | unittest 0 |
| VT-CON-INSTR-01 source RED | exigir marker one-shot con lado robusto y net-id, sin `new` en hook | falta marker: test nuevo falla | source-contract PASS por instrumentación únicamente; cadence intacta | unittest 1→0 en Fase A |
| VT-CON-02 cuerpo positivo | `zoneName=dmgZone_chassis`, `Contact` con primitivas válidas | modelo/fixture conserva `contact=1`, `body=1` | continúa PASS | unittest 0 |
| VT-CON-03 wheel negativo | `wheel`, `Wheel`, `WHEEL` | `contact=1`, `body=0` en los tres | continúa PASS; no amplía heurística | unittest 0 |
| VT-CON-04 null/otro coche/inactivo | `data=null`, car distinto, trace inactivo | cero incrementos | continúa PASS | unittest 0 |
| VT-CON-05 no sustitución | source-contract exige que body nazca exclusivamente de `CaptureContact(..., Contact data)` | PASS vigente | sigue PASS; wheel state/raycast/velocidad no alimentan body count | unittest 0 |
| VT-CLI-01 semántica | artifact PASS/FAIL/STOP/input ilegible | fijar contrato actual | 0/1/2/4 exactos | subprocess 0/1/2/4 |
| VT-BUILD-01 | source-contract instrumental + compile/PACKONLY desde staging nuevo | N/A después de RED instrumental | dos builds inventariables, entries byte-idénticas, cero compile/config error | 0 |

Paths nuevos previstos:

- `P:\DayZ_MCP_dev\tools\tests\fixtures\vehicle_trace\cadence_clock_domain_cases.json`
- `P:\DayZ_MCP_dev\tools\tests\fixtures\vehicle_trace\contact_classification_cases.json`

Tests previstos:

- `P:\DayZ_MCP_dev\tools\tests\test_vehicle_trace_contract.py`
- `P:\DayZ_MCP_dev\tools\tests\test_vehicle_trace.py`

No se modifica `vehicle_trace.py`, el schema, el course, bridge version ni DTO
durante la Fase A. Si una fixture necesita uno de esos cambios para existir, el
plan queda RED y se vuelve a R22.

## 6. Contratos/API verificados (R2)

| Símbolo/contrato | Uso permitido | Fuente |
|---|---|---|
| `Transport.OnInput(float dt)` | hook de input y `dt` observado | `P:\scripts\3_game\vehicles\transport.c:254-262` |
| `Game.GetTickTime()` | reloj real del sample/diagnóstico | `P:\scripts\3_game\global\game.c:909-913` |
| `Transport.OnContact(string,vector,IEntity,Contact)` | única fuente física de body contact | `P:\scripts\3_game\vehicles\transport.c:244-252` |
| `Contact.Impulse`, `.Normal`, `.PenetrationDepth` | copiar primitivas; nunca retener `Contact` | `P:\scripts\1_core\physics\contact.c:9-31` |
| `Game.IsDedicatedServer()` | etiqueta robusta server/client para evidencia | `P:\scripts\3_game\global\game.c:1123-1127` |
| `Print(void)` | marker one-shot; no `PrintToRPT` por write/flush frecuente | `P:\scripts\1_core\proto\endebug.c:95-99` |
| `CarScript.OnContact` vanilla | evidencia de rama server, no prueba de dispatch | `P:\scripts\4_world\entities\vehicles\carscript.c:1453-1480` |
| frecuencia host | `(n-1)/(last-first)`, floor `max(20,0.9*hz)` | `P:\DayZ_MCP_dev\tools\dayz_mcp\vehicle_trace.py:613-632` |
| salida CLI | PASS 0, FAIL 1, STOP 2, input 4 | `P:\DayZ_MCP_dev\tools\vehicle_trace_artifact.py:27-42` |

Los índices de ruedas permanecen exactamente `0..3`; no se añade ni reordena
ningún campo del sample. Antes de escribir un símbolo adicional, reabrir su
declaración real y citar `path:line`.

## 7. Fases autorizables

### Fase A0 — baseline y staging (offline)

1. Confirmar que no hay mutación concurrente en los targets permitidos.
2. Crear staging nuevo bajo `C:\tmp`, nunca reutilizar uno anterior.
3. Manifestar path, bytes y SHA-256 de cada target antes de editar.
4. Antes de editar `MCP_CarScript.c`, leer y aplicar completos:
   - `C:\Users\guill\ObsidianVault\AI\20_Knowledge\dayz-mod-implementation-checklists.md`
     (R30; aplicar activamente E01+ y el mapa client/server);
   - la skill `dayz-mod-workflow`.
   Registrar en la evidencia de Fase A que ambas lecturas ocurrieron antes del
   primer diff Enforce.
5. Para cualquier `.py` que resuelva bajo OneDrive: editar fuera de OneDrive y
   copiar/verificar host-direct conforme al runbook vigente.
6. Si la sesión ejecutora es Opus/Claude, preservar R20: Opus orquesta y delega
   la implementación material a Codex, salvo bloqueo explícito de una
   herramienta exclusiva. Opus no absorbe silenciosamente el rol implementador
   y conserva su revisión Claude independiente.
7. No iniciar daemon, DayZ, lifecycle, lease, deploy ni producto.

### Fase A1 — caracterización GREEN + acceptance RED antes de código

1. Añadir únicamente los dos fixtures y tests VT-CAD/VT-CON/VT-CLI.
2. Separar selectores/clases `CHAR`, `INSTR` y `ACC`; ningún runner puede
   reinterpretar un RED `ACC` como fallo inesperado o excluirlo sin registrarlo.
3. Ejecutar con
   `P:\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe -m unittest -v`
   los selectores explícitos y conservar tres outputs:
   - `CHAR` + controles normativos: exit 0, reproduce `<20` y STOP actuales;
   - `INSTR`: exit 1 porque aún faltan los escalares/marker;
   - `ACC`: exit 1 causal porque aún no existe productor corregido.
4. Confirmar además que no hay import error, typo ni fixture malformed.
5. Si `CHAR` no detecta el defecto o `ACC` no queda RED causal, corregir solo
   test/fixture y repetir; no tocar producción para fabricar el resultado.

### Fase A2 — instrumentación diagnóstica mínima `[DESIGN]`

Único source productivo permitido:
`P:\DayZ_MCP\scripts\4_World\MCP_CarScript.c`.

- Añadir solo escalares de diagnóstico de cadence: suma de `dt`, span de clock,
  deuda máxima y deferrals de tick igual.
- Emitir al detener el trace una sola línea diagnóstica sin secretos.
- Añadir un marker one-shot de `OnContact` por instancia/proceso con lado
  (`IsDedicatedServer`) y net-id; no retener `Contact`, `other` ni arrays.
- Mantener `super.OnContact(...)` primero y `CaptureContact(...)` como única
  fuente del body count.
- No cambiar todavía intervalo, umbral, timestamps, clasificación, schema,
  wire, bridge version ni semántica del trace.

Todo snippet concreto de esta fase debe etiquetarse `[EXACT]` solo después de
reabrir firmas; hasta entonces esta sección es `[DESIGN]`.

### Fase A3 — pre-gate técnico + PACKONLY sin deploy

1. Ejecutar por separado:
   - `CHAR` + controles normativos: exit 0;
   - `INSTR`: exit 0 tras A2;
   - `ACC`: exit 1 con los mismos IDs causales de A1.
   Ejecutar también la suite afectada previa sin clasificar `ACC` como regresión;
   `py_compile` para Python tocado.
2. Repetir source-contract: cero `new` en `OnInput`/`OnContact`, cero retención
   de `Contact`, fórmula host intacta.
3. Justo antes de PACKONLY, leer completa y aplicar la skill
   `dayz-pbo-build`; después construir dos veces desde staging fresco.
4. Inventariar entries y demostrar cierre semántico byte-idéntico; los
   contenedores pueden diferir solo en metadata temporal ya conocida.
5. No desplegar la PBO.
6. Congelar diff, fixtures, outputs y hashes. El resultado aquí es solo
   `TECHNICAL PRE-GATE`; todavía no puede declararse `DIAGNOSTIC READY`.

### Fase A4 — R21 dual independiente y gate terminal

1. Sobre el mismo diff congelado y los mismos outputs, lanzar en paralelo:
   - revisión Codex fresh de diff/scope/fixtures/source-contract/build;
   - revisión Claude independiente de diff/scope/fixtures/source-contract/build.
2. No comunicar ni coordinar hallazgos entre revisores antes de que ambos
   emitan su veredicto completo.
3. Consolidar solo después los dos veredictos, conservando coincidencias,
   divergencias y severidad concreta.
4. Un hallazgo `HIGH` o `GATE` sin resolver produce
   **DIAGNOSTIC RED**. Si se corrige uno dentro del scope, exige nuevo
   RED→GREEN focal, rebuild si afecta Enforce/PBO y re-review de ambos sobre los
   bytes finales; no se amplía scope para cerrarlo.
5. Gate terminal:
   - `DIAGNOSTIC READY / STOP`: `CHAR`/controles/`INSTR`/build GREEN, `ACC`
     continúa RED exactamente por cadence, ambas revisiones y la consolidación
     quedan sin `HIGH/GATE` abierto; BUG-061 y SC-015 siguen abiertos;
   - `DIAGNOSTIC RED`: falso verde, `ACC` omitido o inesperadamente GREEN,
     test relajado, caracterización no reproducible, compile/config inválido,
     PBO no inventariable, rollback incompleto o gate R21 abierto.
6. **STOP obligatorio.** Pedir autorización para una única corrida diagnóstica.

### Fase B — live diagnóstico (no autorizada por este R22)

Solo tras autorización explícita y preflight MCP/lifecycle limpio:

1. Run gestionado server+client, lease just-in-time, mismo net-id en markers.
2. Un único `CivilianSedan` y obstáculo cuya colisión quede acreditada por marker,
   no por colocación supuesta.
3. Trace 20 Hz de al menos 10 s, stop/read completo y logs frescos server/client.
4. Clasificar exactamente:

| Server marker | Client marker | Trace contact/body | Veredicto |
|---:|---:|---:|---|
| >0 | 0 | 0/0 | `RED SERVER_ONLY`; volver al usuario |
| 0 | 0 | 0/0 | `RED SETUP_INCONCLUSIVE`; no tocar hook |
| >0 o 0 | >0 | 0/0 | `RED CLIENT_CAPTURE_BUG`; apto para fix local |
| >0 o 0 | >0 | >0/0 | `RED CLASSIFICATION`; revisar zone exacta |
| >0 o 0 | >0 | >0/>0 | lado viable; continuar cadence |

5. Artefacto negativo/positivo según resultado y cleanup oficial. Esta fase no
autoriza por sí misma S1.

### Fase C — corrección condicional (nuevo R22 corto)

- **Cadence:** elegir una sola modificación del productor usando la evidencia
  `sum(dt)`/clock/backlog. Debe hacer GREEN VT-CAD-ACC-01/03/04 sin tocar
  VT-CAD-N1/N2,
  sin timestamps programados y sin duplicar estados. Antes del diff, documentar
  la transformación exacta y obtener R22 corto.
- **Contacto client-reachable:** corregir solo el guard/acumulador demostrado y
  reejecutar VT-CON.
- **Contacto server-only:** detenerse. Cualquier RPC, SyncVar, command server,
  schema nuevo, bridge nuevo o reinterpretación de wheel-contact es cambio de
  arquitectura/product-spec y necesita Grill/decisión humana.

### Fase D — nuevo PACKONLY/live SC-015

Queda fuera de la menor fase operable. Requiere plan actualizado, nueva
autorización live y todos los gates previos GREEN. Solo un SC-015 completo puede
reconsiderar S1; ningún parcial autoriza Mercedes.

## 8. Archivos permitidos

### Fase A

- `P:\DayZ_MCP_dev\tools\tests\test_vehicle_trace.py`
- `P:\DayZ_MCP_dev\tools\tests\test_vehicle_trace_contract.py`
- los dos fixtures nuevos listados en §5
- `P:\DayZ_MCP\scripts\4_World\MCP_CarScript.c`
- staging nuevo `C:\tmp\...` y PBO PACKONLY de control no desplegada
- reporte/handoff/memoria de la sesión

### Prohibidos

- `MERCEDES_AMGLF*`, S1 y cualquier P3D/config/model.cfg/script del coche
- `vehicle_trace.py`, schema/course/bridge version/DTO en Fase A
- `MCPClientBridge.c`, `MCPMessages.c`, server peer, RPC y SyncVars
- validator thresholds, `TIME_EPS_S` y fórmula de frecuencia
- señales genéricas como sustituto de `OnContact`
- deploy, VPP, DayZ/lifecycle/live durante Fase A
- refactor adyacente, skills y runbooks

## 9. Criterios de parada

Declarar `RED` y parar si ocurre cualquiera:

1. `CHAR` no reproduce/clasifica el STOP actual o `ACC` no demuestra RED causal
   contra bytes actuales.
2. El control positivo falla o un negativo deja de producir STOP.
3. La única forma de obtener cadence GREEN es bajar 20 Hz, redondear, ampliar
   epsilon o fabricar timestamps/muestras.
4. La instrumentación necesita cambiar schema, bridge o transporte.
5. Build/config inválido, PBO no inventariable o cierre semántico no
   determinista.
6. Rollback byte-exacto no demostrable.
7. Un live se vuelve necesario antes de cerrar offline o no está autorizado.
8. El callback resulta server-only.
9. Aparece una contradicción material nueva de DPF/Intent.
10. Se editó Enforce sin completar R30/`dayz-mod-workflow`, se hizo PACKONLY sin
    `dayz-pbo-build`, o falta una mitad/consolidación del R21 dual.

## 10. Rollback

- Restaurar cada target existente desde la copia baseline y verificar SHA-256.
- Eliminar únicamente los dos fixtures nuevos si fueron creados en esta fase.
- No hay PBO desplegada que restaurar en Fase A; borrar solo el staging nuevo
  después de verificar que su path absoluto está bajo `C:\tmp`.
- Si cualquier byte llega a OneDrive, verificar copia host-direct origen↔destino
  antes de declarar rollback.
- No detener procesos directamente ni usar lifecycle en Fase A.

## 11. Commit y handoff

- Verificar primero si los dos árboles son repos Git. Si no lo son, registrar el
  resultado y usar manifest + SHA-256; no simular commit.
- Handoff mínimo: veredicto R22, tabla R26 ejecutada, `CHAR` GREEN,
  `INSTR` RED→GREEN, `ACC` RED conservado, archivos/hashes, PACKONLY, rollback
  dos veredictos R21 independientes, consolidación y
  autorización/denegación de Fase B. No usar «GREEN offline» sin ese desglose.
- Memoria durable: BUG-061 permanece open hasta SC-015 GREEN; no marcar
  `OnContact` owner-client ni cadence como resueltos por evidencia offline.
- La menor siguiente acción para Opus es
  **orquestar Fase A0→A4, delegar implementación a Codex y emitir su revisión
  Claude independiente → DIAGNOSTIC READY / STOP**.
