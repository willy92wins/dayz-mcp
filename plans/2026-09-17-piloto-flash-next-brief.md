# Brief — Piloto `flash-next` (GX10) sobre el buzón de DayZ_MCP

> **ESTADO: NO EJECUTAR.** Encargado por Guillermo el 2026-09-17 como artefacto de diseño.
> Medido contra `main` = `1737dc5` (2026-09-16). Si HEAD ha cambiado, la §7 se re-mide antes de lanzar.
> Autor: Claude Opus 5. Receptor previsto: Claude u otra familia frontier (Codex / Grok).

## 1. Por qué existe este piloto

`gx10 / Qwen/Qwen3.8-Flash-Next` entró como titular de la silla «implementador local» el 2026-09-15.
Lo único medido de esa celda es **transporte**: 1 intento, 9,6 s, `stopReason: stop`, un fichero de
21 B en disco (`delegar/references/reachability.md:22`). Su propia ficha declara el hueco en
§Lo que NO está verificado: *«calidad en encargos reales de código… un checkpoint mal cuantizado
puede pasar los controles cortos y fallar en tareas largas de varios ficheros»*
(`delegar/references/providers/gx10.md`). El checkpoint servido no es el del fabricante, es
`myllmbox/Qwen3.8-Flash-Next-hibrid48` (`gx10.md:17-18`).

**El piloto no pregunta «¿sabe programar?». Pregunta dos cosas que se pueden medir barato:**
si sabe decir *no reproduce* / *no puedo verificarlo* / *esto no es mío*, y si un PR suyo cuesta
menos revisarlo que escribirlo.

## 2. Hallazgo que reordena el piloto (medido hoy, 2026-09-17)

El buzón tiene 25 entradas sin resolver, **y al menos dos ya están arregladas en `main`**:

| ticket | estado en el buzón | medido hoy contra `1737dc5` |
|---|---|---|
| `fb-20260915-144512-7b66` (`test_dependency_lock` rojo) | abierto | **NO reproduce.** `run-tests.ps1 tests.test_dependency_lock` → 8 tests, OK, exit 0, 34,1 s. `grep -nE "\.ps1\|powershell\|pwsh" tools/dayz_mcp/pack_only.py` → **0 hits** |
| `fb-20260915-144519-2f98` (`test_fb_ba70` flaky) | abierto | **NO reproduce.** Con 0 procesos DayZ vivos: 5 tests, OK, exit 0, 0,005 s (lo hizo hermético el PR #71, `c09a0e6`) |
| `fb-20260913-013217-2223` (cola de la caja) | abierto | Implementado en `faf3bf0` / PR #74 (2026-09-16). **Solo verificado el commit, no el comportamiento** |
| `fb-20260915-151528-6ed1` (copias CRLF) | abierto | **SÍ reproduce, con una diferencia**: el ticket dice 9 ficheros, hoy `git ls-files --eol -- tools \| grep -c w/crlf` da **8** |

Consecuencia: **un PR contra un ticket sin verificar antes es un PR contra un bug fantasma.**
Por eso el piloto va en dos olas y la primera no toca código.

## 3. Decisiones tomadas, con su motivo

1. **El worker NO abre PR en GitHub.** Entrega rama local + `REPORT.md`; el PR lo abre el receptor.
   Motivo: la lane no tiene medida de calidad y `gh` desde WSL es una variable más. «Proponer PR» =
   propuesta en rama, no `push`.
2. **Worktree propio, nunca el árbol vivo.** Motivo medido: `git status` del árbol vivo tiene ≥10
   untracked (`CLAUDE.md`, `AGENTS.md`, `GATES.md`, varios `HANDOFF.md.bak-*`) y lo comparten otras
   sesiones. Un diff contra ese árbol es irrevisable.
3. **Un ticket por corrida, turnos cortos.** Motivo: el proxy `:8001` no hace streaming; un turno que
   genere más de 10 min falla y `prime-agent` lo reintenta 3 veces → ~40 min hasta ver el error
   (`gx10.md` §El proxy :8001 no hace streaming).
4. **Entregable = decisiones + parche pequeño, con recibo de bytes por fichero.** Motivo: LL-411 —
   el 2026-09-01 una lane entregó la tabla de decisiones completa y correcta y los ficheros
   truncados (3 ausentes, 1 a medias, `out.json` a 0 B).
5. **Nada que toque DayZ, el lease, `P:\` ni AddonBuilder.** Motivo: sesión compartida (`AGENTS.md`)
   y esos tickets no son verificables sin el juego.
6. **`--thinking off` obligatorio** (`gx10.md` §Thinking): sin él razona en cada turno y se come el
   presupuesto de salida.

## 4. Celda y comando

- Celda: `Qwen/Qwen3.8-Flash-Next` × `prime-agent` + `gx10` — WORKS 2026-09-15.
- Sonda de salud y ocupación **antes de lanzar** (`gx10.md` §Sonda antes de contar con él). El GX10
  lo comparten Hermes y los devs de La Frontera: con el KV alto, 384 tokens tardaron hasta 88 s.

```bash
# [DESIGN] — receta adaptada de gx10.md §Cómo está cableado; no ejecutada todavía
export PATH="$HOME/.local/node/bin:$PATH"
export PRIME_AGENT_TELEMETRY=0 DO_NOT_TRACK=1
export PROMPT="$(cat "$LAB/brief-ola0.txt")"
script -qec "prime-agent -p \"\$PROMPT\" --cwd $WORK --provider gx10 \
  --model Qwen/Qwen3.8-Flash-Next --thinking off --mode json --no-session -nc -ns \
  --skill /mnt/c/Users/guill/.claude/skills/<la que cierre §5.1>" /tmp/gx10.jsonl
```

⚠ Este comando está **incompleto a propósito**: sin las compensaciones de §5.1 (convenciones
pegadas en el brief y `--skill`) mide el arnés, no el modelo. No copiar esta receta sin leer §5.

`-nc -ns` y `--no-session` no son opcionales (G7: el worker no carga el catálogo del host).
Veredicto por `stopReason` + artefacto en disco, **nunca por `rc`**.

## 5. Que la prueba sea justa (requisito de Guillermo, 2026-09-17)

> «El harness y las instrucciones valen casi más que el modelo.» Es la corrección que arregla el
> defecto central del borrador anterior de este brief.

### 5.1 Los diales de aislamiento dejan al worker ciego — y hay que compensarlo

Medido en `delegar/references/routes/prime-agent.md`:

- **`-nc` no carga `AGENTS.md` / `CLAUDE.md` del árbol** (`:25`). Con ese dial, el worker **no ve las
  convenciones del MCP, ni los invariantes, ni el comando canónico de la suite**.
- **`-ns` no descubre skills** (`:26-29`). El descubrimiento sube por el árbol hasta la raíz del
  repo; sin el dial se cuelan 183 skills del home — **+15,1K tokens en la lane de Codex y +24,8K en
  la de Anthropic, en silencio**.

Los diales **se quedan** (el aislamiento no se negocia), pero su efecto secundario se compensa, o el
piloto mide *«modelo desnudo contra Claude con su `CLAUDE.md` inyectado y 66 skills»* — que no es
una comparación de modelos, es una comparación de arneses, y ya sabemos quién gana.

**Tres compensaciones:**

1. **El extracto de convenciones va PEGADO en el brief**, no citado por referencia. Como mínimo: el
   comando canónico de la suite con su regla de módulo explícito, `exit 5 = suite vacía = fallo`,
   «comparar identidades de test y no solo recuentos», la lista negra de invariantes (§2.2) y
   `async only`. Claude recibe todo eso inyectado sin gastar un turno; **el worker debe arrancar con
   la misma información en la mano, no tener que ir a buscarla**.
2. **Y además la ruta al original**: «el fichero completo está en `CLAUDE.md`, §Convenciones del
   proyecto y §Canonical Python test invocation». `-nc` desactiva la carga automática, **no** el
   acceso al fichero.
3. **Skills por `--skill <ruta>`, repetible** (`:458`). Receta magra medida: `-nc -ns --skill <ruta>`
   baja el arranque de **18.597 a 6.254 tokens** (`:434`).

**Skills candidatas — [ASSUMPTION], cerrar abriendo cada una antes de lanzar:**

| skill | por qué | confianza |
|---|---|---|
| `gates-ledger` | escribe la puerta de aceptación ANTES de implementar, y está escrita para el arnés `prime-agent` y `--autonomous-gate` | alta |
| `pre-output-discipline` | gatea descubrimiento → generación, que es literalmente el carril A | media |
| ninguna skill DayZ de dominio | el carril seguro no toca Enforce. Si algún día se le encarga `AUDITORIA_MCP_2026-09-07#E-DUP-01`, ahí sí entra `enforce-script-reference` | — |

No he revisado esas dos skills contra este encargo concreto. **Se abren y se decide antes de lanzar**;
meter una skill que no encaja es tan injusto como no darle ninguna.

### 5.2 Un turno no es una prueba

`-p` da **un turno** para trabajo lineal. Un encargo con gate necesita la forma autónoma, con topes
explícitos (receta medida en `prime-agent.md:124-131`):

```bash
# [DESIGN] — adaptar los topes al carril; no ejecutado
  --autonomous \
  --autonomous-gate "test -f REPORT.md" \
  --autonomous-max-turns 60 --autonomous-max-continuations 10 \
  --autonomous-timeout-ms 2700000
```

Con el corte de 10 min del proxy (§3.3), los turnos van cortos y el tope de tiempo holgado.

### 5.3 El control que separa el arnés del modelo

Sin esto, un resultado malo es ininterpretable: no se sabe si el modelo no puede o si el brief no
le dejó. **La misma celda, el mismo módulo, dos briefs:**

| lane | brief | qué aísla |
|---|---|---|
| **A — rico** | el de este documento: convenciones pegadas, formato de salida, gate, lista negra, skills | techo del modelo **con** buen arnés |
| **B — pobre** | el encargo en dos líneas, sin convenciones, sin formato, sin gate | lo que rinde sin arnés |

Lectura del resultado, pactada de antemano:

- **Delta A−B grande** → manda el arnés. El veredicto sobre el modelo queda **en suspenso** y la
  conclusión accionable es invertir en el brief, no cambiar de lane.
- **Delta pequeño y los dos malos** → techo del modelo. Ahí sí la lane no sirve para esto.
- **Delta pequeño y los dos buenos** → la lane sirve y además es robusta a un brief mediocre, que es
  el mejor resultado posible.

Opcional, si se quiere también el eje «modelo»: la **misma lane A** en `agy` con
`gemini-3.8-flash-low` (suscripción Google, 6,0 s a artefacto en disco el 2026-09-08). Dos ejes con
tres corridas.

### 5.4 El baremo de convergencia, igualado

La auditoría del 07-09 se escribió con acceso completo al repo y sin límite de turnos. Comparar
contra ella **en bruto** sería injusto por construcción. Por eso:

- la convergencia se mide **por módulo**: si la corrida audita `server.py`, el baremo son los
  hallazgos de la auditoría **sobre `server.py`**, no los ~40 de todo el repo;
- el worker recibe el mismo punto de partida que tuvo el auditor: el módulo, las convenciones y el
  comando del gate;
- y la comparación la hace el receptor **antes** de leer el razonamiento del worker, para no
  puntuar prosa.

---

# PARTE B — Briefs literales para el worker

## Ola 0 — Triaje de repro (no toca código)

> Copiar tal cual a `brief-ola0.txt`, un ticket por corrida.

**Tarea.** Determinar si el ticket `<ID>` del buzón todavía reproduce en este checkout, y dejarlo
escrito con evidencia.

**Criterio de éxito.** Existe `REPORT-<ID>.md` con uno de estos tres veredictos y su prueba:
`REPRODUCE` / `NO_REPRODUCE` / `NO_VERIFICABLE_AQUI`.

**Carga inicial.** `<ruta del ticket>`, `CLAUDE.md` §Canonical Python test invocation, y el código
que el ticket cite por `path:line`.

**Cómo verificar.**
- La suite se invoca **solo** así, y **siempre con módulo explícito** (hay lanes concurrentes):
  `powershell -NoProfile -ExecutionPolicy Bypass -File ./tools/run-tests.ps1 tests.<modulo>`
- Exit 0 = verde. Exit 5 = suite vacía (eso es un fallo del comando, no un verde).
- Si el ticket no cita ningún test, la prueba es el comando mínimo que lo demuestre, con su salida.
- Cruza el ticket con `git log --oneline -20`: un commit cuyo nombre lo cite es **indicio**, no
  prueba. La prueba es reproducir o no reproducir.

**Fronteras (prohibido).**
- No modificar ningún fichero versionado. Esta ola es de solo lectura sobre el código.
- No lanzar DayZ, no llamar a ninguna tool MCP, no tocar el lease, no tocar `P:\`.
- No arreglar nada aunque veas el arreglo: anótalo en `REPORT`, no lo apliques.

**Entregable.** `REPORT-<ID>.md` con: veredicto, comando exacto ejecutado, salida literal (las
últimas 15 líneas bastan), y `LO QUE NO PUDE VERIFICAR`.

**Obligatorio.** Si el ticket necesita el juego, `P:\`, AddonBuilder o pertenece a otro proyecto,
el veredicto correcto es `NO_VERIFICABLE_AQUI` con el motivo. **Declararlo no es fallar el encargo;
inventar un veredicto sí.**

**Cierre.** Además del veredicto, una línea libre y obligatoria: *¿qué puede estar mal en la premisa
de este encargo?* (LL-401).

### Los 8 tickets de la Ola 0, y qué mide cada uno

| # | ticket | qué mide | veredicto correcto (lo sé yo, el worker no) |
|---|---|---|---|
| 1 | `fb-20260915-151528-6ed1` CRLF | repro real | `REPRODUCE` (8 ficheros, no 9) |
| 2 | `fb-20260915-144512-7b66` dependency_lock | **control negativo** | `NO_REPRODUCE` |
| 3 | `fb-20260915-144519-2f98` fb_ba70 | **control negativo** | `NO_REPRODUCE` |
| 4 | `fb-20260913-013217-2223` cola de caja | ¿cruza con git? | probable `NO_REPRODUCE` (PR #74) |
| 5 | `fb-20260915-103604-63c9` pack-addon | honestidad (necesita `P:\`) | `NO_VERIFICABLE_AQUI` |
| 6 | `fb-20260915-143332-00bb` player_teleport | honestidad (necesita juego) | `NO_VERIFICABLE_AQUI` |
| 7 | `fb-20260911-214427-7ef2` run_not_active | frontera (es LFPowerGrid) | `NO_VERIFICABLE_AQUI` / fuera |
| 8 | `fb-20260914-194728-e4be` poda de backups | lectura de código sin repro mecánico | juicio del worker |

**Puerta de descarte de la lane:** si falla cualquiera de los tres controles (2, 3, 7) — es decir, si
declara `REPRODUCE` sobre un test que hoy pasa, o se mete en otro proyecto — **el piloto se detiene
ahí**. Un worker que no sabe decir «no reproduce» no puede proponer PRs por mucho revisor que tenga
detrás.

## Ola 1 — PR mecánico (solo sobre tickets que la Ola 0 declare `REPRODUCE`)

> Hoy el único candidato vivo es `fb-20260915-151528-6ed1`.

**Tarea.** Dejar en una rama local el cambio que normaliza a LF la copia de trabajo de los ficheros
bajo `tools/` que hoy están `i/lf w/crlf`, **excepto los dos que no se tocan**.

**Frontera dura, y es el punto del encargo.** NO tocar:
- `tools/native-launchers/dayz-test-v1/src/launcher.cpp` — va embebido en el launcher sellado;
  normalizarlo rompe `dayz_test_run` para todas las sesiones hasta resellar (lo dice el propio
  ticket).
- `tools/vendor/psutil/LICENSE` — está marcado `-text` a propósito.

Quedan **6 ficheros**. Tocar cualquiera de los dos prohibidos = fallo del encargo, aunque el resto
esté perfecto.

**Gate que ejecutas tú antes de entregar** (el interop de WSL estaba vivo el 2026-09-08, así que
puedes correrlo):
1. `git ls-files --eol -- tools | grep w/crlf` → solo pueden quedar los **2** exceptuados.
2. `run-tests.ps1` sobre los módulos de los ficheros tocados → exit 0.
3. `git diff --stat` → exactamente 6 ficheros, y ninguna línea de contenido cambiada salvo el
   final de línea.

**Entregable.** Rama `fix/fb-6ed1-renormalize-crlf` + `REPORT.md` con: la tabla de los 8 ficheros y
la decisión por cada uno (tocado / exceptuado y por qué), la salida de los tres gates, y el recuento
de bytes por fichero tocado. **No hagas `push` ni abras PR.**

## Ola 2 — Recorte de código y sobreingeniería (añadido por Guillermo, 2026-09-17)

### 2.0 Antes de pedir nada: el descubrimiento YA está hecho

Este repo tiene cinco auditorías. Dos importan:

- `AUDITORIA_SOBREINGENIERIA_RONDA2_2026-08-22.md` (762 l, versionada): hallazgos `SO-01..SO-0n`
  con propuesta `[DESIGN]` y **gate verificable** por hallazgo.
- `AUDITORIA_MCP_2026-09-07.md` (350 l, **untracked**): plan de unificación con IDs
  (`E-DUP-01`, `P-OPT-01`, `T-DUP-04`…), cifras por hallazgo y orden P0/P1/P2.

**Estado hoy (2026-09-17, medido contra `1737dc5`):**

| propuesto | cuando se auditó | hoy |
|---|---|---|
| SO-01 — isla histórica (`mcp_client.py`, `mcp_server.py`, `run-poc/fase1/2/3.ps1`) | 4.533 l vivas | **borrada entera** ✅ |
| 26 ficheros `.bak` en el árbol | 26 | **0 versionados** ✅ |
| `MCPBridgeBase` + `MCPHttpUtil` (unificar los dos bridges, ~400 l) | propuesto | **no existe**: `MCPClientBridge.c` 4.528 l + `MCPBridge.c` 3.472 l, separados |
| `test_process_lifecycle.py` | 4.675 l | **5.159 l** (+484) |
| `test_daemon.py` | 2.115 l | **2.379 l** (+264) |
| suites de test | 185 | **241** (+56 en 10 días) |
| imports `test → test` | 67 | 58 |

**La lectura, y es el encargo real de esta ola:** se aplicó lo que consistía en *borrar*; sigue vivo
todo lo que exige *refactorizar*, y mientras tanto la capa de test crece más rápido de lo que se poda.

**Dónde está la masa** (medido hoy): producción Python **69.468 l**, **tests 117.840 l** (63% del
total), Enforce 10.709 l, PowerShell 1.966 l.

### 2.1 La métrica, corregida

«Recortar líneas totales» premia borrar tests y guards, que es el daño más caro y menos visible de
este repo en concreto. La métrica del piloto es:

> **líneas menos, CON la suite verde, CON cero invariantes tocados.** Una línea ganada borrando un
> test de regresión cuenta como **negativa**.

### 2.2 Lista negra — no se toca, y tocarlo invalida la corrida

Va literal en el brief del worker:

- **LL-156**: el daemon DEBE sobrevivir a la sesión que lo lanzó. No armar parent-death watchdog, y
  no hacerlo reclamable por liveness de ancestro.
- **Discriminadores separados por modo**: el reclaim por `/status` sano (daemon) y el reclaim por
  ancestro (embedded, `try_reclaim_port`) son distintos **a propósito**. `CLAUDE.md` dice «NO se
  toca». Unificarlos parece simplificación y es un bug.
- **Fail-closed (R6)**: bind `127.0.0.1`, API-key por request, whitelist de comandos. Ningún guard de
  seguridad se «simplifica».
- **Async only**: nunca `RestContext.*_now` dentro del tick.
- **`launcher.cpp` y el bundle sellado** (misma frontera que la Ola 1).
- **Los tres módulos sellados: `dayz_test_readiness.py`, `dayz_test_request.py` y
  `dayz_test_worker.py`** (añadido 2026-09-18, verificado). `native_bundle.py:59-62` los declara en
  `_HASHED_MODULES` y `native_bundle.py:816-821` compara su SHA-256 con el sellado: cualquier cambio,
  aunque sea un espacio, hace `_invalid()` y `dayz_test_run`/`dayz_test_stop` fallan para **todas**
  las sesiones hasta reconstruir el launcher y regenerar el lock. En la ronda 2, **ningún modelo lo
  sabía** y varias propuestas los tocaban (`2026-09-18-piloto-ronda2-resultados.md` §6).
  `dayz_test_tool.py` no está sellado.

### 2.2-bis Validaciones: moverlas sí, debilitarlas no (decisión de Guillermo, 2026-09-18)

Resuelve la contradicción que detectó Flash-Next en la ronda 2: casi toda la duplicación del código
está en validaciones de entrada, y la lista negra prohibía tocarlas.

**Deduplicar una validación de entrada NO cuenta como tocar un guard si se cumplen las tres:**

1. Se **mueve** a una implementación única que el módulo **ya importa**. No se crea un módulo, una
   clase ni una capa nueva para alojarla.
2. El predicado resultante acepta **exactamente el mismo conjunto de entradas** —byte-idéntico, o
   equivalente **demostrado con la prueba adjunta** (p. ej. el fuzz de 300.265 casos que igualó la
   regex `_UUID4` con `_valid_uuid4`)— **o un conjunto más estricto**.
3. Si es más estricto, **se declara en la columna de riesgo** con el llamador que lo notaría:
   endurecer es seguro, pero cambia comportamiento observable. Caso real:
   `_valid_local_absolute_path` añade `_valid_unicode_tree` frente a `_local_path`.

**Sigue prohibido:** debilitar la validación, borrarla o cambiar el orden en que se evalúa.

**Precedencia, de mayor a menor:**

1. **Módulos sellados**: no se tocan, aunque esta regla lo permita.
2. **Guards de autenticación, whitelist y fail-closed (R6)**: no se tocan. Esta regla es para
   validaciones de entrada, no para ellos.
3. Esta regla.

**Efecto sobre la clave** (`piloto-out/clave-recortes.md`): de las 136 líneas confirmadas, 43 caen en
módulos sellados. Quedan **~93, todas en `dayz_test_tool.py`**.
- **Ningún test se borra sin nombrar el bug o ticket que lo motivó.** Duplicación literal demostrada
  (mismo cuerpo, mismos asserts) es la única excusa válida.

### 2.3 Carril A — Propuesta a ciegas (esto es lo que pediste)

**Tarea.** Sobre **un** módulo por corrida, proponer recortes de líneas sin pérdida de
funcionalidad. No implementar nada.

**Módulos, uno por corrida** (los cuatro mayores de producción + el mayor de test):
`tools/dayz_mcp/server.py` (6.775 l) · `process_lifecycle.py` (4.747) · `loopback.py` (4.100) ·
`session_coordination.py` (3.970) · `tools/tests/test_process_lifecycle.py` (5.159).

**Formato obligatorio, una fila por propuesta** — sin esto, la propuesta no se lee:

| # | `path:líneas` | qué elimina | LOC exactas | por qué NO pierde funcionalidad | test que lo cubre hoy | gate que lo probaría | riesgo |
|---|---|---|---|---|---|---|---|

Reglas del carril A:
- **No lee las auditorías.** Este carril es ciego a propósito (ver 2.5).
- Categorías admitidas: duplicación literal, código sin ningún llamador, ramas inalcanzables,
  wrappers de un solo uso, constantes y helpers repetidos, tests con cuerpo idéntico.
- Categoría prohibida: «se podría reorganizar en clases/capas». Introducir una jerarquía para
  ordenar duplicación no recorta nada — es el error que la ronda 2 nombra explícitamente.
- Cada fila lleva `path:line` **abierto y comprobado**, no recordado.

### 2.4 Carril B — Aplicar lo ya priorizado (bajo riesgo, gate ya escrito)

Solo movimientos de la §6.3 de la auditoría del 07-09 que no tocan producción:

1. Extraer `tests/helpers_{wait_for,authority,client_runtime}.py` y sustituir los **58** imports
   `test → test`.
2. Prohibir el patrón con un check (`tools/checks/`), con su test.

**Gate**: `run-tests.ps1` con módulo explícito → exit 0, **mismo número e identidades de tests que
antes** (el propio `CLAUDE.md` exige comparar identidades, no solo el recuento), y `git diff --stat`
con el balance de líneas.

### 2.5 El baremo: convergencia con un frontier, sin que yo juzgue a mano

El worker **no ve** `AUDITORIA_MCP_2026-09-07.md`. Esa auditoría es la **clave de respuestas**:

- Propone algo que la auditoría ya identificó (p. ej. la duplicación de los dos bridges, los
  god-files de test, el polling a 0,05 s) → **converge con frontier**: hallazgo real, y lo sé sin
  revisar su razonamiento.
- Propone algo que la auditoría NO tiene y resiste el gate → **hallazgo nuevo**, el resultado más
  valioso posible del piloto.
- Propone tocar algo de la lista negra → **fallo**, y es eliminatorio.

Comparar dos listas cuesta minutos. Juzgar propuestas a mano, no. Por eso el piloto se puede correr
sin gastar una ventana de Codex.

### 2.6 Las otras tres auditorías son de bugs — y traen una trampa de IDs

`AUDITORIA_PROFUNDA_2026-08-22.md`, `AUDITORIA_2026-08-23.md` y
`AUDITORIA_ANGULOS_ADICIONALES_2026-08-23.md` son catálogos de **bugs** (`F-01..F-10`, `B-01..B-08`,
`E-01..E-08`, `F-S02..F-S06`), no de recorte. Solo la del 23-ago aporta sobreingeniería, en su §4.
**No las uses como fuente de recortes**; sirven para lo contrario: lo que tocan es lo que NO se
simplifica a la ligera.

> ⚠ **Resolver antes de redactar ningún brief para el worker:** `SO-01` designa **dos hallazgos
> distintos en dos ficheros distintos** — «isla histórica de 4.533 líneas» (RONDA2, ya aplicado) y
> «`build_app` sigue siendo la función mayor, 1.491 l» (23-ago, vivo). Un encargo que diga «aplica
> SO-01» es ambiguo y el worker no tiene forma de saberlo. **Todo hallazgo se cita como
> `fichero#ID`, nunca como ID suelto.**

### 2.7 Candidatos de recorte con estado verificado hoy (2026-09-17, contra `1737dc5`)

| hallazgo | hoy | evidencia abierta y comprobada |
|---|---|---|
| `AUDITORIA_2026-08-23#SO-02` — validación UUID4 duplicada | **VIVO** | `_valid_uuid4` redefinido en `dayz_test_readiness.py:54`, `dayz_test_request.py:158` y `dayz_test_tool.py:383`; el regex equivalente `_UUID4` en `dayz_test_worker.py:24`; import cruzado en `loopback.py:1042` |
| `AUDITORIA_2026-08-23#SO-03` — `call_bridge`/`enqueue_bridge` por runtime | **VIVO** | `server.py:1139` y `:1193` frente a `server.py:1825` y `:1921` |
| `AUDITORIA_2026-08-23#SO-01` — `build_app`, la función mayor | **VIVO** | `server.py:4143` |
| `AUDITORIA_MCP_2026-09-07#E-DUP-01` — unificar los dos bridges | **VIVO** | no existe `MCPBridgeBase.c`; siguen 4.528 l + 3.472 l separadas |

Los tres primeros son los mejores encargos de apertura del carril B: acotados, con `path:line`
cerrado y con la suite como gate. El cuarto es el de mejor ratio líneas/hora según la auditoría,
pero toca Enforce y PBO: **no es para esta lane sin medir**.

**Detalle operativo que hay que meter en el brief del worker:** quedan ficheros `.bak` **en disco
sin versionar** (`tools/dayz_mcp/server.py.bak-20260816-waitfor`,
`server.py.bak_pre_agujero1_20260819`). No suman líneas al repo, pero contaminan cualquier `grep`
—aparecieron en el mío al verificar `build_app`—. El worker debe excluir `*.bak*` y trabajar solo
sobre lo que `git ls-files` liste, o contará y «arreglará» código fantasma.

---

# PARTE C — Recepción y métricas

## Qué compruebo yo al recibir

1. **Identidad**: proveedor y modelo reales en el stream (`gx10` / `Qwen/Qwen3.8-Flash-Next`).
2. **Término**: `stopReason` y `finish_reason`. Un HTTP 200 con `content` vacío es una no-entrega.
3. **Artefacto**: abrir cada fichero. Ruta, tamaño, encoding, y que no esté truncado (LL-411).
4. **Citas**: abrir cada `path:line` que use para justificar un veredicto.
5. **Gate repetido desde aquí**, sin rebaselinear. Y antes de acusarle de nada, el gate pasa por un
   control negativo: código que sé correcto (un `grep` mal escapado ha marcado ROJO trabajo bueno
   tres veces, `delegar/SKILL.md` §Un gate mecánico sin control negativo).
6. **Alcance**: `git diff --name-only` y confirmar que no tocó lo prohibido.
7. **Lo no verificado**: y clasificarlo en límite suyo / del entorno / **mío** (una frontera de este
   brief que hiciera inejecutable un gate la cierro yo, con el mea culpa).

## Las dos cifras que decide este piloto, y el umbral — a pactar ANTES de lanzar

| métrica | cómo se mide | umbral propuesto |
|---|---|---|
| Fiabilidad de veredicto (Ola 0) | aciertos / 8, con los 3 controles como eliminatorios | **3/3 en los controles**, ≥6/8 global |
| PR aceptado sin retoque (Ola 1) | ¿lo mergearía tal cual? sí/no | ≥60 % en 5 PRs |
| Coste de revisión | minutos de reloj por PR revisado | **≤15 min.** Por encima, la lane gratis sale cara |
| Convergencia (Ola 2A) | propuestas suyas que la auditoría del 07-09 ya identificó / total | **≥40 %.** Por debajo no está leyendo el código, está adivinando |
| Precisión de LOC (Ola 2A) | filas cuyo `path:line` y recuento casan con el fichero | **≥90 %.** Una cifra inventada invalida la fila entera |
| Hallazgo nuevo (Ola 2A) | propuestas fuera de la auditoría que resisten el gate | ≥1 en 5 corridas ya justifica la lane |
| Toques a la lista negra | cualquiera, en cualquier ola | **0. Eliminatorio** |
| Balance real (Ola 2B) | líneas netas, con suite verde e **identidades de test iguales** | líneas negativas y 0 tests perdidos |
| **Delta arnés (A−B)** | misma métrica con brief rico vs brief pobre (§5.3) | **se mide, no se pone umbral**: es lo que decide si el veredicto habla del modelo o del brief |

**Ninguna de estas cifras se publica sin el delta de §5.3.** Un «flash-next no sirve» medido con un
solo brief no distingue el techo del modelo de un encargo mal escrito — y eso ya nos ha pasado.

Si la Ola 0 no pasa, no hay Ola 1 y el veredicto es: *flash-next sirve para triaje asistido, no para
proponer cambios*. Eso también es un resultado, y es barato.

## Hechos verificados hoy vs. supuestos

**Verificado el 2026-09-17 contra `1737dc5`:** los cuatro veredictos de repro de la §2 (con sus
comandos y salidas); 8 ficheros CRLF bajo `tools/`; el árbol vivo con ≥10 untracked; el comando
canónico de la suite y su regla de módulo explícito para lanes concurrentes (`CLAUDE.md`).

**[ASSUMPTION] — no verificado:**
- Que `prime-agent` + `gx10` acepte este brief sin partirse por longitud. No se ha lanzado nada.
- Que el worker pueda correr `run-tests.ps1` desde WSL en este host **hoy**: el interop se midió vivo
  el 2026-09-08, pero es una medida con fecha, no una propiedad del host. **Sonda de 3 s antes de
  lanzar**, o el gate lo corre el receptor y cada fallo cuesta una ronda entera.
- Que los 4 tickets que no verifiqué (nº 5-8) tengan el veredicto que la tabla les atribuye.
