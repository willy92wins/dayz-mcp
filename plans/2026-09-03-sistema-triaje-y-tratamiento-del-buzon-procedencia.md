# Procedencia — plan definitivo del sistema de triaje del buzón (v2, 2026-09-03)

Anexo de `2026-09-03-sistema-triaje-y-tratamiento-del-buzon.md`. Una fila por decisión del plan final.

**Lanes de planificación (ciegas)**: L0 Anthropic · L2 Alibaba · L4 Google · L5 OpenAI · L6 xAI · L7 Moonshot. **Árbitros de la ronda de reconciliación (ciegos entre sí, una sola ronda, los dos declararon convergencia)**: **C** = Codex `gpt-5.6-sol` (OpenAI) · **G** = Grok `grok-4.6` (xAI).

**Reglas de desempate aplicadas**: evidencia > mayoría — una postura con `path:line` que MIDE lo disputado vence a una que no lo tiene; sin evidencia decisiva por ninguna parte, no se inventa desempate y la fila va a §9 como PREGUNTA AL HUMANO con recomendación provisional; un hallazgo que el propio árbitro marca «escape adyacente» en su §E no reabre nada. `D##` = fila del `PROCEDENCIA.md` del consolidado v1; `N##` = decisión nueva forzada en esta ronda; `X##` = descartada.

**Recuento**: ADOPTADO 71 · ADOPTADO-PROVISIONAL 8 · PREGUNTA AL HUMANO 6, **las 6 DECIDIDAS POR EL HUMANO el 2026-09-03** y en las 6 con la opción recomendada (§9.2 D08 · §9.3 D48 · §9.4 D44 · §9.5 N1 · §9.1 D33 · §9.6 N23) · DESCARTADO 15. **No queda ninguna abierta.**

## §1 Alcance y versión mínima

| # | Decisión | Lanes | Evidencia | Árbitros | Estado |
|---|---|---|---|---|---|
| D01 | Registro lateral append-only + pasada idempotente; el buzón no gana campos | 6/6 | `inbox.py:11-12,106-147` re-verificado | ninguno objeta | ADOPTADO |
| D02 | Triaje por LOTE en una llamada; arreglo por ficha | L0 L5 L6 L7 | §A5 ~205 s/acción | ninguno objeta | ADOPTADO |
| D03 | No reutilizar la cadena de 5 pasos del 2026-08-30 | L0 L6 (L7 en contra) | `HANDOFF.md:21-25` re-verificado | G lo refuerza («ya tienen medición en contra, no reabrir») | ADOPTADO |
| D04 | Sin SHA del plan que reinicia, sin DAG de clausura | L0 L6 | `plans\inbox-20260830\10-…:1-9` + `GATES.md` raíz re-leído | ninguno objeta | ADOPTADO |
| D05 | Hoja markdown por ficha solo en C3/C4 | L0 (L5 siempre; L7 nunca) | argumento PB-020 B | G sí; C: «no por clase, por consumidor» — se integra: C3/C4 por defecto y C1/C2 si un revisor no puede consumir la fila + `GATES.md` | ADOPTADO-PROVISIONAL |
| D06 | El hueco de `evidence_ref` es real y hay que cerrarlo, no fingirlo | L0 L5 L6 L7 | verificado: `server.py:4257-4268` no lo pasa; `inbox.py:125,144-146` | los dos lo confirman | ADOPTADO |
| D07 | Clase de la ficha que expone `evidence_ref` | L0 «T3», L6 «R1», L7 «C1», L2 L4 L5 «fuera del MVP» | `test_pipeline_feedback.py:277-281` re-verificado por mí: `required=={feedback_id,resolution}`, no prohíbe un opcional | C: fuera del MVP, C1/C2 + flag `requiere_autorizacion`; G: C1 mecánico, autorización = pregunta, no ascenso de clase | **N1** |
| N1 | `evidence_ref` sale del MVP: ficha candidata C1 mecánica + flag `requiere_autorizacion`; el MVP cierra con `resolution` | — | los dos árbitros convergen; la cita de G mide el contrato de test | C ∧ G | ADOPTADO · **DECIDIDO POR EL HUMANO 2026-09-03 (§9.5)**: AUTORIZADO fuera del camino crítico — C1 + `requiere_autorizacion`, no bloquea el primer drenaje, y antes se mide la llamada de dos argumentos desde un cliente de cada familia |
| N2 | Autorización y clase se separan: `requiere_autorizacion` es un flag, no la clase C4 | — | G (§B3) con `PROCEDENCIA` D07 abierto; C lo llama «flag independiente» | C ∧ G | ADOPTADO |
| N3 | R-A: la disposición aprobada previa manda sobre cualquier clasificación nueva (búsqueda mecánica en `plans\inbox-*` y `gates\inbox-*`) | L0 | **V1 del orquestador**: `10-fb-…251d.md:6,14,31` abierto; 5 de 6 lanes se equivocaron en un hecho comprobable | C (§B4 «puntuar la unidad autorizada») ∧ G (§D1) | ADOPTADO |
| N4 | `COBERTURA_INCOMPLETA` prohíbe **afirmar drenaje**; se procesa y declara el subconjunto; paginación = ficha propia | L5 | `inbox.py:158,198,211` (limit 1..100, sin cursor) | C (§B11) lo exige; G no objeta | ADOPTADO |

## §2 Escalera de triaje

| # | Decisión | Lanes | Evidencia | Árbitros | Estado |
|---|---|---|---|---|---|
| D08 | Qwen local primero; Grok fuera del triaje | L2 L5 L6 L7 | §A4/§A5: G7 asigna juicio a Codex y ejecución a Grok | los dos aceptan la escalera | ADOPTADO · **DECIDIDO POR EL HUMANO 2026-09-03 (§9.2)**: se acepta la escalera del plan (gratis proponen, Codex decide, Opus siguiente peldaño) y **sustituye al ejemplo del usuario** |
| N5 | La escalera se parte en **proponentes** (PRESCORE, Qwen, Zen) y **decisor** (Codex → Opus); sin decisor no hay clase | — | `BRIEF.md:46-48`/G7: el JUICIO es de Codex — cita que mide la propiedad del juicio | **C (§B2)**; G no lo contradice (acepta la escalera) | ADOPTADO |
| N6 | Prohibida la ruta Grok→Grok (implementador y revisor de la misma familia) | — | §A5 regla «otra familia»; el v1 la abría en C1 | **C (§B7)**; G no objeta | ADOPTADO |
| N7 | Peldaño Zen deshabilitado hasta pinear su sonda: `lane_fallo=sin_sonda` y se salta, no se cuelga | — | `[NEEDS]` ya reconocido en v1 §10.8 | C ∧ G (G lo marca además «escape adyacente», no ronda nueva) | ADOPTADO |
| D09 | PRESCORE como peldaño 0 | L0 L6 | argumento: lo mecánico no puede «no estar» | C: insumo, no autoridad; G: calcula señales sí, salta el triador no | ADOPTADO-PROVISIONAL (ver N12) |
| D10 | Sonda 30 s, lease de GPU, una sesión de prime-agent | 6/6 | §A5 literal | ninguno objeta | ADOPTADO |
| D11 | «No está» enumerado y registrado en `lane_fallo` | unión L0 L5 L6 | argumento | +`sin_sonda` de G → 9 condiciones | ADOPTADO |
| D12 | Una clasificación discutible no es «no está» | L5 | argumento: calidad ≠ disponibilidad | ninguno objeta | ADOPTADO |
| D13 | Escalera caída: PRESCORE registrado, DIFERIDO, sin despacho ni `pipeline_resolve` | síntesis | ninguna cita | **C ∧ G coinciden** en la postura conservadora, y G añade «no pisar filas TRIADO» | ADOPTADO |
| D14 | `used_percent` ≥ 90 % = «no está» | L2 L6 (L0 95) | nadie midió el coste de un R21 | los dos: NO SE PUEDE DECIDIR, medir el p95 de un R21 | ADOPTADO-PROVISIONAL `[ASSUMED]` §10.2 |
| D15 | Topes 240/180 s recalibrados al p95 tras 10 corridas | L5 L2 L6 | 205 s/acción de §A5 | ninguno objeta | ADOPTADO |
| D16 | Un veredicto por `id`; un PASS global es inválido | L0 L6 | §A3 | ninguno objeta | ADOPTADO |
| D17 | `senal_que_decide` obligatoria y comprobable contra la entrada | L0 | argumento mecánico anti-vibra | C lo refuerza: validar contra señales recalculadas **fuera** del modelo | ADOPTADO |
| D18 | `malformed > 0` no se clasifica: cuenta y va al humano | L2 L5 L6 | `inbox.py:210-215` | ninguno objeta | ADOPTADO |
| N8 | El `body` va **íntegro** (1..8000); el lote se parte por presupuesto, nunca se trunca; el body es dato, no instrucción | — | **`inbox.py:101-102` re-verificado por mí**: `body` 1..8000 | **C (§B3)**; G no objeta | ADOPTADO |

## §3 Clasificación

| # | Decisión | Lanes | Evidencia | Árbitros | Estado |
|---|---|---|---|---|---|
| D19 | Señales calculables (`grep`/`ls`/`git`), no opinión | 6/6 | convergencia total | ninguno objeta | ADOPTADO |
| D20 | Fail-closed: señal no medible → valor más gravoso, no se ejecuta sola | L5 | G6 | ninguno objeta | ADOPTADO |
| D21 | Gana la clase más alta que cualquier señal active | L0 L6 | argumento asimétrico | ninguno objeta | ADOPTADO |
| D22 | Cinco clases X/C1/C2/C3/C4 | L0 L2 L6 L7 vs L4 L5 | ninguna cita mide el número | C: fusionar clases con idéntico tratamiento; G: se acepta porque X y C4 son acciones distintas | ADOPTADO (C4 pasa a ser solo data-crítica; la autorización sale a flag → N2) |
| D23 | `kind` no determina la clase | 6/6 | I1 + §A2 | ninguno objeta | ADOPTADO |
| D24 | Disposición CAMBIO/EVIDENCIA/DESCARTE ortogonal a la clase | L0 L6 | `10-…251d.md:6` y `03-…268a.md:6` re-verificados por mí | ninguno objeta | ADOPTADO |
| D25 | `fb-…-251d` es C1 por su disposición EVIDENCIA | L0 con cita; las otras 5, cinco clases distintas | **V1**: `10-…251d.md:6,14`; **re-verificado por mí**: `test_wait_for.py:37-50` es `_FakeRuntime`, sin juego | **C ∧ G coinciden**: C1 para el trabajo autorizado; el parche del default es otra ficha | ADOPTADO (ya no provisional: dos familias externas y una cita) |
| N9 | D1/D2/D6 se acotan al `OWNS`/alcance declarado, no al `grep` del token contra el árbol | — | sin ella `251d` sale C1 y C3 a la vez (D6 se enciende por la palabra `wait_for`) | **C (§B4) ∧ G (§B2)** | ADOPTADO |
| N10 | `G-CAL`: el oráculo son las 35 hojas aprobadas del 2026-08-30 (35 `Disposición:`, 22 `OWNS:`), no la tabla del propio plan; EXPECT 0 descensos | — | **medido por mí** en `plans\inbox-20260830\` | **C (§B4)** exigía corpus independiente; G (§D3) exigía la puerta antes de despachar | ADOPTADO |
| N11 | Hasta `G-CAL` verde, toda fila es DUDOSA y pasa por el decisor; el PRESCORE no despacha | — | argumento + los seis veredictos de `DISENSOS.md` §1 | **G (§B6) ∧ C (§B4)** | ADOPTADO |
| N12 | D5 queda `[NEEDS: algoritmo]` con colapso provisional calculable | — | el enum no está en §A y no tiene comando | **G (§B7)**; C no objeta | ADOPTADO-PROVISIONAL |
| D26 | Solo identidad exacta o `id` citado cierran un duplicado; similitud = candidato | L5 L2 L0 | argumento: un falso duplicado pierde un reporte | los dos lo confirman | ADOPTADO |
| D27 | Umbral de similitud (Jaccard 0,60) | L0 L6 L7 «por calibrar»; L5 L2 lo rechazan | ninguna medición | los dos: NO SE PUEDE DECIDIR el número; **coinciden en la calibración**: positivos por `fb-id` citado, negativos por `project`/`kind` distinto, FP=0 | DESCARTADO el número; ADOPTADO el protocolo de calibración (§10.10) |

## §4 Grupo de trabajo y regla de parada

| # | Decisión | Lanes | Evidencia | Árbitros | Estado |
|---|---|---|---|---|---|
| D28 | El ledger lo escribe la orquestadora antes de delegar | L0 L2 L5 (L6 al trabajador en R1) | ninguna cita mide el quién | los dos: NO SE PUEDE DECIDIR, y **los dos dan el mismo provisional**: la orquestadora fija el criterio, el worker puede materializarlo pero no cambiarlo | ADOPTADO-PROVISIONAL |
| D29 | Revisor de otra familia, sesión nueva; un relay no acredita familia | 6/6 | §A5 literal | ninguno objeta | ADOPTADO |
| D30 | Una sesión de prime-agent a la vez → el revisor va por CLI oficial | L2 L5 L6 L7 | §A5 | ninguno objeta | ADOPTADO |
| D31 | No FIFO por `age_s` | L6 | §A2: la más antigua es C4 y bloquea en el humano | G lo mantiene; C no objeta | ADOPTADO |
| D32 | 5 fichas CAMBIO in-flight, bajando a 3 si hay truncamiento | L6 L0 | §A3 «máximo 5 planes por sesión fresca» | ninguno objeta | ADOPTADO `[ASSUMED]` §10.6 |
| D33 | Tamaño del council para planificar | L0 «N=2 nunca para planificar»; L2 L4 L5 L7 «N=3» | dos reglas vigentes contradictorias | **C**: manda `workflow.md` por precedencia (`workflow.md:40-46`); **G**: son objetos distintos, PB-020 B para fichas del buzón | **DECIDIDO POR EL HUMANO 2026-09-03 §9.1**: manda `workflow.md`; PB-020 B enmendada a «N=2 salvo el paso 1 de `workflow.md:94`» (enmienda escrita en `pipeline-roadmap.md`, sin borrar la decisión original). Gana **C**. En el plan sigue N=1: las dos reglas coinciden para una ficha sin fase 0 multi-lane |
| D34 | VERDE mecánico antes de la ronda 1; repro obligatorio; tope antes; familia al revisor; 3.ª = ORCHESTRATOR_NEEDED | 6/6 | PB-020 A + `gates-ledger/SKILL.md:417-422` re-verificado por mí | los dos lo mantienen | ADOPTADO |
| N13 | `rounds_cap` **fijo por clase antes de la ronda 1** (C2 deja de ser «1; 2 solo si…») | — | un tope dinámico no es tope | **C (§B5)**; G no objeta | ADOPTADO |
| N14 | La familia se clasifica **antes** de evaluar el tope (el v1 hacía inalcanzable la clasificación en la última ronda) | — | lectura del pseudocódigo v1 `:220-231` | **C (§B5)** | ADOPTADO |
| N15 | Un repro que deja **roja una puerta de producto** impide cerrar aunque la familia sea MISMA → ORCHESTRATOR_NEEDED | — | **`gates-ledger/SKILL.md:419-422` re-verificado por mí**: el cierre con backlog exige producto VERDE | **C (§B6)**, con la cita que mide; G proponía `MISMA → cerrar` sin esa condición | ADOPTADO (evidencia > empate) |
| N16 | Si el revisor no clasifica la familia, se asume MISMA (fail-closed anti-asíntota) | — | argumento; cierra el hueco de «y si no contesta» | **G (§B8)**; compatible con N15 | ADOPTADO |
| N17 | El backlog vive en el **registro lateral**; solo se promueve a `pipeline_feedback` si es problema de producto nuevo, deduplicado y con consumidor | — | el v1 creaba un buzón recursivo y el tope de 2 escondía el tercero | **C (§B6)**; G no objeta | ADOPTADO |
| D35 | El escape adyacente se parchea dentro de la ronda, sin barrido nuevo | L5 L7 L6 L2 (L0 abría ronda 2) | `gates-ledger/SKILL.md:420,423` | **los dos coinciden**: dentro de la ronda; nunca ronda 3 | ADOPTADO (ya no «mayoría sin evidencia») |
| D36 | Artefacto de proceso: una ronda; si no converge se retira | L6 | `gates-ledger/SKILL.md:423` re-verificado por mí | ninguno objeta | ADOPTADO |
| D37 | Seguridad, corrupción o pérdida de datos sin repro completo | L5 | G6 | **C**: no cuentan como blocker de revisión (`:418` exige repro), suben a autorización | ADOPTADO con la matización de C |
| D38 | Dos revisores coincidentes no elevan la evidencia; dos repros sí | L0 L5 L6 | §A4 «coincidir es disparador, no evidencia» | ninguno objeta | ADOPTADO |
| D39 | El tope cuenta barridos de descubrimiento, no re-ejecuciones | L5 | argumento | ninguno objeta | ADOPTADO |
| N18 | Insertar «escribir `GATES.md`» como paso propio de la pasada (el v1 lo omitía en sus 8 pasos) | — | `gates-ledger/SKILL.md:4-5,20` exige escribirlo antes de implementar | **G (§B9)** | ADOPTADO |

## §5 Escalamiento al humano

| # | Decisión | Lanes | Evidencia | Árbitros | Estado |
|---|---|---|---|---|---|
| D40 | «Muy grande» sin umbral de líneas; >5 ficheros de producción o ≥3 componentes | L2 L4 L7 L5 | **medición propia** `git show --stat` del lote A: 2/3/4 ficheros, 312/31/369 líneas de producción | **G**: no tocar; **C**: los 3 commits no miden el universo de fracasos → el 5 es `[ASSUMED]` | ADOPTADO la forma (sin líneas); el número **`[ASSUMED]`** por C |
| D41 | Overrides duros: formato persistente, firma de tool MCP, D3, `tool_contribution` sin commit, pago por token, migración destructiva | 6/6 | §A1 + I5 + R9 | los dos los conservan | ADOPTADO |
| D42 | El silencio no autoriza y no degrada ninguna clase | L5 L6 | G1 + fail-closed | ninguno objeta | ADOPTADO |
| D43 | Roast: 1 por pasada, solo C3/C4 o cambios de reglas | L0 L5 L6 | argumento: la atención humana es el recurso escaso | ninguno objeta | ADOPTADO |
| D44 | Quién ejecuta el roast | L0 L2 L5 «el humano»; L6 «Fable»; L7 «Grok» | ninguna: la glosa define el qué, no el quién | **los dos: NO SE PUEDE DECIDIR** | **DECIDIDO POR EL HUMANO 2026-09-03 (§9.4)**: PRE-ROAST de una IA de otra familia que no participó en el plan, adjunto a la propuesta; el roast del humano solo para cambios de las reglas del propio sistema |
| D45 | ≤4 preguntas por pasada, ordenadas por grado de bloqueo (entero) | 5/6 | G1 en §A4 | G lo acepta explícitamente; C no objeta | ADOPTADO |
| D46 | No repreguntar hasta evidencia nueva o 3 pasadas | L5 L0 | argumento | ninguno objeta | ADOPTADO |
| D47 | Destino de la cola del humano | L0 `[NEEDS]`; nadie más lo fijaba | **`~\.claude\CLAUDE.md:71` re-verificado por mí**: el hook inyecta LIVE-STATE al arrancar | **G (§D11)** aporta la cita que mide dónde lo ve el humano; **C (§B9)** exige consumidor, correlación, acuse y transición | ADOPTADO: `reviews\triage-<run_id>\HUMANO.md` + una línea en LIVE-STATE + `pregunta_id`/`RESPUESTA`/transición + fixture |

## §6 Datos y estados

| # | Decisión | Lanes | Evidencia | Árbitros | Estado |
|---|---|---|---|---|---|
| D48 | Ubicación del registro de triaje | L0 `reviews\`; L6 L7 L4 `%LOCALAPPDATA%`; L2 L5 `plans\` | **re-verificado por mí**: `inbox.py:30-46` valida la FORMA y **no resuelve contra ninguna base** → el argumento de v1 muere | **C**: DISENSO FUERTE, no se puede decidir; **G**: split, `triage.jsonl` junto a `feedback.jsonl` (`inbox.py:11-12`) | **DECIDIDO POR EL HUMANO 2026-09-03 (§9.3)**: el split de G — `triage.jsonl` hermano de `feedback.jsonl` en `%LOCALAPPDATA%\DayZ_MCP\inbox\` (estado de ejecución fuera de git) y los citables bajo `gates\` y `reviews\` |
| D49 | JSONL append-only, no JSON reescrito | 5/6 (L4 JSON) | R8 | los dos lo aceptan | ADOPTADO |
| D50 | `ABANDON` es veredicto de gate, no estado | L0 (L2 lo hacía estado) | §A4 `gates-ledger` | ninguno objeta | ADOPTADO |
| D51 | `hash_contenido` solo para duplicados exactos, no para idempotencia | síntesis | verificado: las entradas nunca se reescriben | G lo confirma (D51 «`body_sha256` no vuelve») | ADOPTADO |
| D52 | Recuperación tras caída: manda el buzón; nunca re-resolver a ciegas | L0 | `inbox.py:182-187`; `:185` borra el `evidence_ref` previo | ninguno objeta | ADOPTADO |
| D53 | `resolution` con formato fijo commit \| suite+intérprete \| pyz \| veredicto | L2 L5 L6 L7 | `HANDOFF.md:26-30` criterio (e) | ninguno objeta | ADOPTADO |
| N19 | `pendientes` **reintenta DIFERIDO**: la resta excluye solo TRIADO/EN_CURSO/EN_VERIFICACION/BLOQUEADO_HUMANO/RESUELTO/DESCARTADO | — | contradicción interna del v1 (`:337` contra `:327`) | **G (§B1) ∧ C (§B8)** | ADOPTADO |
| N20 | Octavo estado `EN_VERIFICACION`: un commit **no** resuelve; el cierre exige la conjunción (a)-(e) | — | **`HANDOFF.md:26-30`**: cita que mide el criterio de cierre | **C (§B8)**; G aceptaba 7 estados sin citar nada en contra | ADOPTADO (evidencia > empate) |
| N21 | El registro es formato persistente nuevo: `[DESIGN]` + `[NEEDS: TRIAGE-SCHEMA]` (versión, escritor único, append acotado, cola parcial, legacy, retirada) | — | G5 y R8; el v1 lo presentaba como hecho cerrado | **C (§B10)**; G no objeta | ADOPTADO |
| N22 | Ledger del lote en `DayZ_MCP_dev\gates\triage-<run_id>\GATES.md`; no se toca el `GATES.md` v9 ni las 37 hojas `gates\inbox-*.md` | L6 proponía `gates\inbox-<run_id>\` | **medido por mí**: `gates\` tiene 37 hojas `inbox-*.md` congeladas y la raíz tiene el `GATES.md` v9 (`HANDOFF.md:21-25`) | **G (§B4)**: el ledger sin path no es escribible | ADOPTADO (prefijo `triage-` en vez de `inbox-` para no confundirlo con las 37 hojas) |

## §7 Gates · §8 Riesgos

| # | Decisión | Lanes | Evidencia | Árbitros | Estado |
|---|---|---|---|---|---|
| D54 | `gate-check.mjs` en `~\.claude\skills\gates-ledger\scripts\` | ninguna lo ubicó | verificación propia: existe ahí, sin copia en `DayZ_MCP_dev` | ninguno objeta | ADOPTADO |
| D55 | Acredita `--reverify`, no `--status` | L6 | **`gates-ledger/SKILL.md:37` re-verificado por mí**, literal | G lo mantiene | ADOPTADO |
| D56 | EXPECT = baseline con intérprete nombrado | L0 | `HANDOFF.md:31-36`: 2268 tests, 2 rojos conocidos, 7 con otro intérprete | **C**: por **identidad cerrada** de los rojos, no por conteo | ADOPTADO con la corrección de C |
| D57 | `G-REPRO` rojo en el commit base; el revisor re-ejecuta la mitad «antes» | L0 L4 L5 L7 | argumento + criterio (d) | los dos lo aceptan | ADOPTADO |
| D58 | `G-PYZ` con la forma acreditada de `HANDOFF.md:46-47` | ninguna | verificación propia | **C ∧ G**: falta la línea de comando literal → `[NEEDS]` | ADOPTADO-PROVISIONAL §10.11 |
| D59 | `G-INGAME` agrupado por lote | L0 L6 L7 | DZ-R5 | los dos lo aceptan | ADOPTADO |
| N23 | `G-INGAME` sin operador = **ABANDON de esa dimensión** y ficha `BLOQUEADO_HUMANO`; nunca RESUELTO sin PNG/JSON | — | **`gates-ledger/SKILL.md:421` re-verificado por mí** | **G (§B10)** | ADOPTADO + **DECIDIDO POR EL HUMANO 2026-09-03 §9.6**: C3/C4 se acumulan y se cierran en UN lote in-game con él; el drenaje de lo verificable sin juego NO se bloquea. Descartado priorizar la pulsación headless |
| D60 | Control positivo por test (correr los tests nuevos sin el arreglo) | L6 | argumento + gate calibrado | ninguno objeta | ADOPTADO |
| D61 | Timeout, fichero ausente o entorno roto = ABANDON, no FAIL | L5 | §A4 | ninguno objeta | ADOPTADO |
| D62 | Verificación post-resolve releyendo `pipeline_inbox` | L7 | `inbox.py:177-188`: la resolución huérfana se descarta en silencio y sin `malformed` | ninguno objeta | ADOPTADO |
| D63 | El `app.pyz` probado debe corresponder al commit revisado | L5 | argumento + `HANDOFF.md:46-47` | ninguno objeta | ADOPTADO |
| D64 | Cada riesgo con síntoma MEDIBLE y señal de que la contramedida funciona | L0 + unión de las 6 | argumento | G: ACEPTAR §8 tal cual | ADOPTADO |
| D65 | `COBERTURA_INCOMPLETA` cuando `unresolved_total` > `id` únicos | L5 | `inbox.py:198,211` | C lo endurece (N4) | ADOPTADO |
| D66 | Freno del backlog: máx 2 por ficha; 3 pasadas al alza y se para | L0 | medible con `unresolved_total` | C: el tope de 2 puede esconder el tercero → se combina con N17 | ADOPTADO con N17 |
| D67 | Señal de recaída: `REVIEW-*` o atestaciones como condición de cierre | L6 | `HANDOFF.md:21-25` | G: conservar | ADOPTADO |
| N24 | Tres riesgos nuevos en §8: cerrar con puerta de producto roja (13), cola humana sin retorno (14), pérdida por truncar el `body` (15) | — | derivados de N15, D47 y N8 | C ∧ G | ADOPTADO |

## Descartados

| # | Qué | Origen | Motivo |
|---|---|---|---|
| X1 | `node gates-ledger/gate-check.mjs` | L4 | ruta inventada; el script está en `~\.claude\skills\gates-ledger\scripts\` |
| X2 | `work/inbox-<lote>/GATES.md` | L6 | `work\` no existe; es un nombre de rama (`HANDOFF.md:37`) |
| X3 | `bai/glm-5.3-flash` como revisor «de otra familia» | L7 L2 | un relay no acredita familia (§A5) |
| X4 | `triage_registry.json` reescrito en cada pasada | L4 | bajo caída pierde el registro entero |
| X5 | Reutilizar la cadena de 5 pasos para C2 | L7 | `HANDOFF.md:21-25`: esas casillas dejaron de ser condición de cierre |
| X6 | «>400 líneas netas» / «>300 de producción» como «muy grande» | L0 / L5 | refutados por medición del lote A |
| X7 | Presupuesto de triaje de «70 s por entrada» | L4 | contradice el triaje por lote; 72 entradas = 84 min de sondas |
| X8 | Matar la corrida «tras 15 minutos» | L4 | número sin dependencia ni medición; decide el vigilante por `EXIT`/`RC=` |
| X9 | `ABANDON` como estado de la máquina | L2 | es el tercer veredicto del gate |
| X10 | Auto-vinculación de duplicados al 85 % léxico | L4 | sin calibración; un falso positivo pierde un reporte |
| X11 | `evidence_ref` como «ficha #1 C4» del MVP | consolidado v1 | los dos árbitros: scope creep y clase no decidible → N1 |
| X12 | `body_head(1200 chars)` en el contrato del triador | consolidado v1 | `inbox.py:101-102`: trunca un campo válido de hasta 8000 → N8 |
| X13 | `reviews\triage\triage.jsonl` justificado por «`reviews` es raíz de `evidence_ref`» | consolidado v1 (L0) | `inbox.py:30-46` no resuelve contra ninguna base: la justificación no medía lo disputado → §9.3 |
| X14 | «MISMA familia → CERRAR con backlog» incondicional | consolidado v1 | `gates-ledger/SKILL.md:419`: el cierre con backlog exige producto verde → N15 |
| X15 | Los disparadores de §5 metidos dentro de la clase C4 | consolidado v1 | confunde autorización con riesgo de datos → N2 |

## Escapes adyacentes declarados por los árbitros (no reabiertos)

C (§E): comandos de sonda de Zen y de `G-PYZ`, etiquetado `[NEEDS]`, umbral de tamaño, ubicación del sidecar, actor del roast y los hermanos de las familias «parada», «estado/retry» y «canal humano». G (§E): path de `GATES.md`, salto de Zen, D5 incalculable, puntero LIVE-STATE. Ninguno justifica otra ronda; todos están resueltos o listados en §10 del plan.

**No reabierto por decisión expresa**: N del council (cita en contra), cadena de 5 pasos (`HANDOFF.md:21-25`), umbrales por líneas (medición en contra).
