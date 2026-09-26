<!-- ARBITRAJE DEL ORQUESTADOR — leer antes que el cuerpo -->

# Hoja de ruta DayZ-MCP — arbitrada 2026-08-17

**Qué es esto.** El cuerpo lo escribió Grok (lane de consejo, `01a010ce-b130-7743-9401-5a7105be524b`,
30 + 26 turnos, $1,42) sobre un inventario exhaustivo del buzón entero (38 sin resolver de 73), el
LIVE-STATE, los dos planes sin triar y el trabajo del día. Esta cabecera es del orquestador y dice
qué está verificado contra el disco, qué se corrigió y qué queda por comprobar. **Donde la cabecera
y el cuerpo discrepen, manda la cabecera.**

## Verificado por el orquestador contra el árbol

- `MCPClientBridge.c` cambió a las **19:39:08** (62.887 B, sha `A6C80927F85437AD…`) respecto a lo
  empaquetado a las 16:04 (62.528 B, `4108972B97F9…`), con la rama Dabs en `:1562-1569`.
  → **El PBO desplegado ya no casa con el fuente. La opción "gatear sin rebuild" no existe.**
- El pisado de las 04:02 y la disciplina que lo abortó dos veces: `HANDOFF.md:193-195`. Cita exacta.
- `LFPowerGrid\config.cpp:190` nombra a Dabs en `requiredAddons[]` como `DF_Scripts`.

## Corregido respecto a lo que el cuerpo deja abierto

El cuerpo pide, como entrada dura del ciclo 1, comprobar si el símbolo `DabsFramework` del `#ifdef`
existe de verdad — y avisa de que no se dé por vivo ni por muerto. **Esa comprobación ya está hecha,
y sale a favor de la rama:**

- El `config.cpp` de Dabs declara `class DabsFramework` como su entrada de `CfgMods`
  (`…\stack-source\Dabs_scripts\DabsFramework\Scripts\config.cpp:21`, con `dir = "DabsFramework"`).
- **Expansion ya condiciona con ese mismo símbolo** en tres sitios:
  `DayZExpansion_Core_Defines.c:91` y `ExpansionIcons.c:242` (dos copias).

O sea que la convención es el nombre de **CfgMods**, no el de CfgPatches (`DF_Scripts`), y un mod de
terceros grande lo usa exactamente así. **La rama no es código muerto** y el ciclo 1 no necesita esa
sonda previa. Queda un residuo menor, solo observable in-game: que el define alcance al módulo de
`DayZ_MCP` con el orden de carga real. El precedente de Expansion dice que sí.

## Lo que sigue sin verificar y el cuerpo declara honestamente

- El sha del PBO desplegado no se re-desempacó en esta pasada (sí se verificó entrada a entrada a
  las 16:04, pero ese artefacto ya está muerto).
- El recuento 38/73 del buzón y la economía «63% del reloj / 7,1 min» son del orquestador; el cuerpo
  se fió sin comprobarlos. Son correctos y su procedencia está en la memoria del proyecto.
- Donde el inventario decía «las 7 vistas de LFPowerGrid son ScriptView», el cuerpo contó **4 de
  producción + 3 de test**. La cifra del cuerpo está contada; la del inventario venía de la entrada
  del buzón. Manda la del cuerpo.

## Lo que el orquestador adopta sin reservas

La estructura por **ciclos** (un ciclo = todo lo que viaja junto hasta un gate) en vez de un ranking
de prioridades, porque el recurso caro medido no es el trabajo sino las sesiones in-game del usuario.
Y el §3 entero — el reparto por lanes con conjuntos de ficheros disjuntos, la lista de lo que hay que
serializar, y el techo de **dos lanes de escritura**. Ese techo no es prudencia: hoy hubo **tres**
pisadas, y la tercera fue justo un fichero que nadie tenía marcado como exclusivo moviéndose bajo un
artefacto ya medido.

## Lo que el orquestador NO ha revisado a fondo

Los ciclos 2 a 7 los he leído por encima (títulos y tesis, no criterio a criterio). Antes de ejecutar
uno, léelo entero y comprueba sus citas: el cuerpo tiene 42 KB y la disciplina de esta casa es que una
cita sin abrir no acredita.

---

# Hoja de ruta DayZ-MCP — secuencia, no ranking

Fecha: 2026-08-17. Parte del inventario de hoy. No reabre descubrimiento.

Un ciclo = todo lo que viaja junto hasta un gate. El eje no es «qué duele más»:
es qué comparte PBO, qué puede moverse sin PBO ni usuario, y qué hay que
decidir antes de tocar cualquiera de las dos cosas.

---

## DELTA 2026-08-17 noche — secciones reescritas

Hechos nuevos: `MCPClientBridge.c` 19:39:08, 62.887 B, sha `A6C80927F85437AD…`,
rama `#ifdef DabsFramework` en `:1562-1569`. El PBO de las 16:04 (sha fuente
`4108972B97F9…`, 62.528 B) ya no casa. El símbolo del `#ifdef` no está
demostrado. «Paralelo» = lanes de agentes, no «Python es barato».

Reescritas: §1 tesis · ciclo 0 (regla 1 + chequeo del símbolo) · ciclo 1 ·
ciclo 3 (3b: `logs_since`/`autospawn` son `server.py`) · ciclo 4 (una
frase) · ciclo 6 (Dabs ya no es trabajo de esta hoja) · **§3 entera** ·
§4.1 · §4.2 (una frase: A viaja en el PBO del 1) · §5 (primer bullet) ·
§6 · anexo.

---

## 1. Tesis

<!-- REESCRITA 2026-08-17 noche -->

El PBO de las 16:04 está muerto como artefacto de gate: el fuente ya no es
el que se empaquetó. El próximo pack —forzado, no elegido— se lleva la
rama Dabs del dueño sí o sí; esta hoja no la diseña ni la toca. Antes de
ese pack, una comprobación barata del símbolo `#ifdef` (no una sesión).
Después: una sesión que cubre `action_use` + `pos_real` + el clic ATM en
los dos desenlaces. Python (C+D, flake, caja) en lanes con conjuntos de
ficheros disjuntos; `server.py` es una sola lane. Auto-lease pierde. Dos
lanes de escritura, no tres.

---

## 2. La secuencia

### Ciclo 0 — Cerrar las cuatro decisiones (este documento las toma)

No es trabajo. Es el umbral de entrada de todo lo demás. Un roadmap que
deje A, auto-lease o E «a decidir sobre la marcha» fabrica el próximo
rebuild a destiempo y el próximo HANDOFF pisado.

**Qué entra.** Las cuatro respuestas de la §4, convertidas en reglas de
empaquetado:

1. <!-- REESCRITA --> El PBO de las 16:04 no se reutiliza. El próximo pack
   es de sincronización fuente→PBO, no una elección de alcance. La rama
   Dabs del dueño viaja porque ya está en el fuente; no se planifica ni
   se edita aquí. El símbolo `#ifdef` se comprueba *antes* de gatear
   (abajo), y no se da por vivo ni por muerto.
2. C y D son un solo ítem de superficie. A no se funde con ellos.
3. E se parte: se pagan dos agujeros baratos, se mitiga el resto, no se
   construye un lock de árbol. El paralelo de la §3 queda acotado a
   **2 lanes de escritura**.
4. El lease sigue explícito. Codex gana en hardening HTTP/MIME/loopback,
   no en el modelo de autoridad.

**Chequeo del símbolo `DabsFramework` — entrada dura del ciclo 1, no
una sesión.** Offline, minutos, cero Enforce nuevo. No declara la rama
rota ni buena; solo dice si el identificador *puede* existir.

1. Abrir el `config.cpp` extraído de Dabs (no el PBO de Workshop a
   ciegas). Anotar el nombre de `class CfgPatches` y el de
   `class CfgMods`.
2. Contrastar con lo ya abierto: LFPG nombra Dabs en
   `requiredAddons[]` como **`DF_Scripts`**
   (`LFPowerGrid\config.cpp:190`), no `DabsFramework`.
   El único condicional-por-mod de ese árbol es `#ifdef LBmaster_Core`
   / `#ifdef LBmaster_Groups`
   (`LFPG_BalanceProvider_LBmaster.c:8`, `LFPG_ActionPairSensor.c:84`)
   — nombres de addon. `DayZ_MCP\config.cpp:8` sigue pidiendo solo
   `DZ_Data`.
3. Contrastar con lo que *sí* usa el símbolo: Expansion,
   `#ifdef DabsFramework` en
   `_expansion_src\...\ExpansionIcons.c:242` y
   `_codex_tmp\...\DayZExpansion_Core_Defines.c:91`. LF_UILab pone
   `"DabsFramework"` en `requiredAddons[]`
   (`LF_UILab\config.cpp:10`), no `DF_Scripts`. Los dos nombres
   conviven en esta máquina. Yo **no** abrí el `config.cpp` de Dabs
   (en `P:\Mods\@Dabs Framework` solo hay PBOs + `meta.cpp` con
   `name = "Dabs Framework"`).
4. Lectura, no veredicto:
   - `CfgMods` se llama `DabsFramework` → el símbolo es el candidato
     correcto (define de CfgMods al cargar el mod). **No** prueba que
     DayZ_MCP lo vea al compilar: no depende de Dabs.
   - Ni `CfgMods` ni `CfgPatches` se llaman `DabsFramework` → la rama
     es muerta por construcción. Se empaqueta igual (ya está en el
     fuente). El clic del ciclo 1 se planifica como el escenario «no
     dispara». No se cambia el símbolo en esta hoja.
   - Cualquier otra combinación → se anota; no se adivina.

Eso no sustituye el clic. Distingue «el identificador ni existe» de
«existe y el dispatch puede fallar por otra causa» (walk, userdata de
color). Si el dueño quiere un canario `Print` dentro del `#ifdef`, lo
pone él; no se añade aquí.

**Por qué entra junto.** Cada regla cambia *qué* viaja en el pack
forzado y cuántas lanes se abren. El chequeo del símbolo es la única
cosa que hay que saber *antes* de quemar la sesión, y no cuesta
sesión.

**Entrada.** Inventario leído; este documento escrito; fuente
`MCPClientBridge.c` medido a 62.887 B / `A6C80927F85437AD…`.

**Salida.** Las cuatro reglas no se relitigan sin evidencia nueva.
El chequeo del símbolo tiene un resultado escrito (candidato /
muerto-por-construcción / no-adivinar).

**Sesión in-game.** No.

---

### Ciclo 1 — Pack forzado + gate (el PBO de las 16:04 ya no sirve)

<!-- REESCRITA 2026-08-17 noche -->

El PBO desplegado se empaquetó contra `MCPClientBridge.c` 62.528 B /
`4108972B97F9…`. El fuente vivo es 62.887 B / `A6C80927F85437AD…` (19:39:08)
y lleva la rama Dabs del dueño en
`MCPClientBridge.c:1562-1569`. Gatear el PBO viejo sería certificar un
binario que ya no es el árbol. **Hay que empaquetar.** Eso no es «meter
Dabs»: es sincronizar. La rama viaja porque está en el fuente, no porque
esta hoja la pida.

No se añade Enforce nuevo en este pack. P1-02 y `ui_dialog` fase 2
siguen fuera: no están en el fuente, y ampliar la superficie no
verificada de un pack forzado es el error que el ciclo 0 evita.

**Qué entra.**

- Rebuild `-packonly` del árbol actual + recontar las 11 entradas contra
  *este* fuente (el 11/11 de las 16:04 está caducado).
- `action_use` → `wait_for(log_matches, "[BTCOpenResponse]")` → `ui_tree`
  → `ui_set_text("EditBtcAmount")`.
- Los tres casos de `pos_real` (teleport sentado, spawn IA flags + `y=0`,
  regresiones).
- `ui_set_text` sobre etiqueta plana.
- `declared_slots` de un `CivilianSedan` (BUG-066(c) ya está en
  `MCPBridge.c:2225-2235`; el sha `0ED14AF5076A…` del export sigue
  siendo de ese fichero, no del cliente).
- El tramo del clic, **en los dos escenarios** (abajo). Un clic. No una
  caza, no un rebuild a mitad de sesión.

No entra: editar la rama Dabs, cambiar el símbolo, P1-02, `ui_dialog`,
ningún otro `.c`.

**El clic del ATM — dos escenarios, un solo criterio de cierre del
resto.**

Cadena común hasta el panel abierto y el texto puesto. Luego
`ui_click("BtnBuyBtc")`:

| | Rama dispara (`clicked` / no `no_handler`) | Rama no dispara (`no_handler` u otro error de handler) |
|---|---|---|
| Qué significa | El símbolo era vivo *y* el walk llegó a la vista. A queda medido en verde sobre este PBO. | No se distingue aquí «símbolo muerto» de «userdata de color / walk». El chequeo del ciclo 0 ya dijo si el identificador *podía* existir. |
| Qué se hace con `[BTCTxResult]` | Se espera y se lee `err=`. Cierra la cadena del HANDOFF (`:128-132`). | No se reintenta. No se reconstruye. No se edita el `#ifdef`. Se archiva el JSON del clic y se sigue con `pos_real`. |
| Qué queda de A | Cerrado en este ciclo. | Sigue siendo del dueño. Esta hoja no abre un ciclo para «arreglar Dabs». |
| El resto del gate | Idéntico. `action_use` / `pos_real` / `ui_set_text` no dependen del clic. | Idéntico. |

Tratar el `no_handler` como fallo del pack es el descarrilamiento
(§6). Tratar el `clicked` como «entonces metemos P1-02 en caliente»
también.

**Por qué entra JUNTO.** Un pack, un arranque
`dayz_test_run(project="LFPowerGrid", extra_mods=["@DayZ_MCP"])`
(`HANDOFF.md:125`), un `wait_for` de misión. Separar `pos_real` de
`action_use` es la sesión suelta. El clic viaja porque el fuente ya lo
lleva; no se reserva para un segundo PBO «de Dabs».

**Entrada.**

- Ciclo 0 cerrado, incluido el resultado escrito del símbolo.
- Hash de `MCPClientBridge.c` = `A6C80927F85437AD…` (o el actual, si el
  dueño volvió a moverlo: **re-hashear ahora**, no el de las 19:39).
- Nadie más escribe `MCPClientBridge.c` / `MCPBridge.c` / `config.cpp`
  durante pack + sesión (§3).
- `P:` montado. Cero procesos DayZ, 8765 libre. Daemon de generación
  nueva. `server.py` no se escribe en esta lane.
- Workaround: AddonBuilder `-packonly` + anclas dentro del PBO; el
  `dayz_test_run` **sin** `build` (`HANDOFF.md:428-429`).

**Salida (medible).**

- 11 entradas del PBO nuevo byte-idénticas al fuente *de este pack*.
- Servidor arranca sin error de script del bridge.
- `action_use` abre el ATM: `started:true` **y** `[BTCOpenResponse]`.
- `ui_tree` + `ui_set_text("EditBtcAmount")` ok.
- Clic archivado en uno de los dos cubos de la tabla. Ningún cubo
  bloquea `pos_real`.
- Teleport sentado: `pos_real` = posición nueva del transporte.
- Spawn flags=3108 `y=0`: `pos_real.y` ≈ superficie.
- Regresiones sedan flags=0 `y=0` y teleport a pie `y=0`.
- Sedan: `declared_slots` sin `""`.
- Centinela: las dos mitades de
  `test_task9_spawn_phase_markers.py` re-congeladas sobre el
  `MCPBridge.c` **gateado** (`:20-21`, `:78-80`). El centinela no cubre
  `MCPClientBridge.c`; la rama Dabs no lo mueve.
- Un push (195 → 206) **después** del flake 3a verde en un clon
  (`export-reds.json:8-12`).

**Sesión in-game.** Sí. Una. La única de esta hoja hasta el ciclo 6.

---

### Ciclo 2 — Superficie junior (C-P0 + C-P1 + D entero)

Un solo cambio de Python sobre `server.py` / `session_coordination.py` /
tests. Cero Enforce. Cero sesión.

**Qué entra.** Exactamente el contrato de
`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-08-17-weak-agent-consumer-ux.md:14-22`
y `:109-122`, que ya nombra los ids de D:

| Ítem del inventario | Cierre |
|---|---|
| `4cc4` catálogo 10k / junior ve la mitad | un solo verbo de lease visible; low-level fuera del `tools/list` |
| `2a2e` elige `session_acquire` | lo mismo; description de `session_acquire_wait` «preferred» |
| `5640` `wait_for` timeout `ok:true` | `_wait_for_response` hoy fuerza `"ok": True` (`server.py:1104-1111`) → `ok` solo si `satisfied` |
| `0c77` timeout nombra `query_all_players` | el error nombra `wait_for` |
| `09aa` `lease_required` mudo | texto `call session_acquire_wait(purpose=…)` |
| `aa92` / `1b14` no hay `ready` | `bridge_status.ready {ready, reason∈enum}` |
| `29a0` teleport exige lease y no lo dice | una línea en `player_teleport` / `world_spawn` (hoy el docstring de teleport no lo menciona: `server.py:1688-1703`) |
| `f282` `version_blocked` con juego apagado | `game_not_ready` + motivo, no mismatch de protocolo |
| `be93` lote de docstrings | el mismo commit de descriptions |
| P1 alias `lease_acquire` | mismo handler, segundo nombre |
| `47d4` `logs_since` tira RPTs viejos | `server.py:1026-1029`; misma lane, no 3b |
| `7d5a` / `a14f` autospawn | `server.py:631-653`; misma lane |

También entra, porque es la misma superficie y no es tool nueva:

- la frase en `FastMCP(instructions=…)` (`server.py:1227-1230`) que hoy
  solo nombra lifecycle + `bridge_status`.
- marcar resueltos en el buzón los ids que este paquete cubre. Eso es
  archivo, no trabajo.

No entra aquí:

- P2 `playbook_run` (ciclo 7).
- TTL 120 s / auto-renovar por PID (`fb-…-120511-2b00`). Eso **cambia
  D-15** (`decisions\decision-log.md:24`: recuperar a los 120 s sin
  actividad). No se cuela como UX. Decisión y forma en la §4.3 / §5.
- `ui_dialog`.
- auto-lease (Codex §7.4). Prohibido por este ciclo y por el plan
  (`weak-agent-consumer-ux.md:35-37`).

**Por qué entra JUNTO.** Es el mismo cuerpo de `server.py`: el catálogo
que el junior recorre, los `ToolError` que lee, y el JSON de
`wait_for` / `bridge_status`. Siete entradas de D más el P0 de C no son
siete PRs. Un junior midiendo a mitad de este paquete ve un estado
peor que el de hoy (lease escondido a medias, `ok` a veces mentira).
P1 (`lease_acquire`) viaja en el mismo commit porque el nombre que
qwen3.5 buscó cae en la mitad leída del listado
(`weak-agent-consumer-ux.md:22-24`); separarlo es otra sonda.

**Entrada.** Ciclo 1 cerrado o, como mínimo, `server.py` ya no es de la
sesión de UI verbs. Hash del fichero tomado justo antes de escribir.
Daemon: avisar y reiniciar después; el módulo se cachea en memoria
(`HANDOFF.md:430-431`).

**Salida.**

- Tests del propio plan (`weak-agent-consumer-ux.md:132-139`) verdes,
  incluida una fixture fresca → `ready is True` (no tautología).
- `tools/list` no incluye `session_acquire` (o su description empieza
  por `LOW-LEVEL`) y sí incluye `session_acquire_wait` y `lease_acquire`.
- Sonda **qwen3.5:27b** sin recorte, mismo `prompts/qwen35.md`: PASS
  según `weak-agent-consumer-ux.md:141-148`.
- Ids de D marcados en el buzón con el commit, no reabiertos.

**Sesión in-game.** No. El gate es Ollama local. Lifecycle bloqueado en
el harness, como en la sonda original.

---

### Ciclo 3 — Caja recuperable y suite publicable

Python. Cero PBO. Cero usuario. **No comparte ficheros calientes con el
ciclo 2.** Si se pisan `server.py` / `loopback.py`, deja de ser
paralelo (eso es E, y esta hoja no lo finge).

**3a — antes del push del ciclo 1, mismo día, otro fichero.**

- Flake `test_runtime_root_lock_has_cross_process_ownership_and_recovers`
  (`tools\tests\test_bug046_startup_deadlock.py:1050-1075`): el hijo
  hace `signal.write_text(...)` *después* de ganar la elección; el padre
  afirma el contenido en cuanto `signal.exists()`. Create-vs-write.
  Medido 1/3 en suite, 0/6 aislado; el export lo cazó
  (`export-reds.json:8-12`, `'' != '1'`). Viaja al clon. **Sin esto no
  hay push.**

**3b — después del 2, o en paralelo solo si el conjunto de ficheros
es disjunto (ver §3).**

<!-- REESCRITA parcial: logs_since y autospawn NO van aquí -->

- `dayz_test_stop` imposible cuando un PID muere fuera de banda
  (`fb-…-121035-1d6e`). `stop_run` exige identidad completa de **cada**
  proceso; si `_identity_matches` falla, el run pasa a `UNRECONCILED` y
  el stop se rechaza
  (`process_lifecycle.py:1259-1306`, predicado en `:1221-1230`). El
  reaper de D-18 (`:1852-1913`) solo retira el caso *all-dead con
  identidad coherente*. Un muerto a medias deja la caja para TTY. Eso
  no es «más lease»: es el agujero que D-18 no cubrió. Consumidor real
  (D-39): la caja queda inutilizable.
- `doctor` → `PROCESS_SCAN_FAILED` por encoding
  (`doctor.py:388-394` traga cualquier `Exception` y lo nombra como
  fallo de proceso). No verifiqué la línea cp1252 concreta; el
  wrapping sí.
- `logs_since` (`server.py:1026-1029`) y el autospawn
  (`server.py:631-653`, `fb-…-a14f`) **no son 3b**. Tocan `server.py`:
  viajan en la lane del ciclo 2, serializados con P0/P1/docstrings.
  Dejarlos aquí era fingir paralelismo sobre el fichero más caliente.
- `dayz_test_run` `succeeded` con cliente muerto y sin Mission
  (`fb-…-833f`). No localicé el predicado en esta pasada; no se
  inventa el arreglo: se reproduce y se cierra en este ciclo o se
  declara no-repro.
- `.git` roto en `DayZ_MCP_dev` (`fb-…-020639-4979`): un gate que lea
  `git status` vacío como limpio es falso verde. **No verifiqué** el
  directorio (prohibido git). Si el síntoma se confirma, el arreglo es
  local (re-init o dejar de usar ese predicado). No viaja al repo
  público.

**Por qué entra JUNTO (y por qué 3a se adelanta).** 3a bloquea el
único push que el ciclo 1 quiere hacer: es el mismo gate de
publicación, no «calidad interna» aplazable. 3b es un solo tema —el
instrumento miente sobre si la caja está viva o se puede soltar— y se
prueba offline. Mezclar 3b con el ciclo 2 mete lifecycle y catálogo en
el mismo diff: el junior-gate y el stop-gate no se diagnostican juntos.

**Entrada.** 3a: cualquier momento antes del push, con el test en rojo
reproducible bajo carga. 3b: ciclo 2 no está editando
`process_lifecycle.py` / `doctor.py` / `log_tail`.

**Salida.**

- 3a: el test del lock no lee vacío; 3 corridas de la suite completa
  en un clon aprovisionado sin ese rojo. El centinela, si el ciclo 1
  aún no re-congeló, sigue siendo el otro rojo esperado.
- 3b: un run con un PID muerto a propósito se suelta por
  `dayz_test_stop` o por `reap` sin TTY; `doctor` no reporta encoding
  como proceso; `logs_since` sin marker no devuelve ficheros con
  `mtime` anterior al launch.

**Sesión in-game.** No.

---

### Ciclo 4 — Hardening Codex que no choca (Python)

Re-verificar cada hallazgo contra el árbol **actual** (snapshot Codex =
`31040de`; el árbol se ha movido: `HANDOFF.md:25-26`). Luego, solo lo
que siga siendo cierto y no relitigue el lease.

**Qué entra.**

- P1-03: `Handler._read_json` pasa `Content-Length` a `rfile.read` sin
  suelo ni techo (`loopback.py:1512-1519`).
- P1-04: FastMCP fuerza `format="png"` salvo JPEG
  (`server.py:1987-1988`), así que un WebP viaja con MIME PNG. El
  encoder, él solo, sí declara `image/webp`
  (`tools\mcp_capture.py:116-117`).
- P1-01 restante: lo que el export de hoy **no** haya cerrado ya
  (fixtures sintéticos, manifiesto fuera del repo). Varios trozos se
  arreglaron hoy (`HANDOFF.md:70-74`); no se rehacen.
- P2-07 `save_dir` arbitrario, si sigue abierto tras re-verificar.

No entra: P1-02 (Enforce: `MCPBridge.c:160-172` acepta cualquier URL;
el cliente sí exige `http://127.0.0.1:` en
`MCPClientBridge.c:259-264`). Viaja al ciclo 6, con el PBO.
P2-05 `GetFirstHuman` (`MCPBridge.c:2475-2492`) está desfasado como
«no hay uid»: batch 6 ya resolvió por `uid` opcional
(`HANDOFF.md:213-214`). No se reabre. P2-06 (`wait_for` pattern/value)
lo absorbe el ciclo 2. §7-8 de Codex (auto-lease, dos superficies,
`scenario_run`) no entran nunca en esta hoja (§5).

**Por qué entra JUNTO.** Misma frontera HTTP/captura/instalador, mismos
tests de daemon, cero PBO. Separar MIME de `Content-Length` es dos
PRs para un solo hardening. P1-02 no viaja: unir Enforce con este
ciclo es un segundo pack Enforce. El pack del ciclo 1 ya está
forzado por el desvío del fuente; no se le cuelga P1-02 encima.

**Entrada.** Re-verificación hallazgo a hallazgo escrita. Ciclo 2 no
está a mitad de `server.py` si P1-04 se toca ahí: o se espera, o P1-04
entra en el mismo commit que el ciclo 2 (es una línea).

**Salida.** Los criterios de Codex §5 para P1-03/P1-04/P1-01 que
sigan vigentes, medidos con tests nuevos (length -1, oversize, UTF-8
inválido; WebP con MIME WebP o WebP retirado de la superficie).

**Sesión in-game.** No.

---

### Ciclo 5 — `ui_dialog` fase 1 + las dos sondas baratas

Plan ya arbitrado:
`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-08-17-ui-dialog-plan-fusionado.md`.
Fase 1 es Python-only (`:212-214`). Fase 2 es el lote Enforce del
ciclo 6.

**Qué entra.**

- Tool `ui_dialog`, validación estricta, whitelist, espera **fuera** de
  `tool_lock`. El docstring de `execute_wait_for` que hoy dice «the
  only MCP entry» (`server.py:1125-1128`) se corrige en el mismo
  commit (`ui-dialog-plan-fusionado.md:126-128`).
- Criterio de no-bloqueo ya escrito: con un diálogo de 60 s abierto,
  otra sesión ejecuta otra tool y recibe resultado antes de que el
  diálogo termine (`:214`).
- Las dos sondas que el inventario pide **antes** de la fase 2, ninguna
  con rebuild: (1) en qué punto del ciclo de misión se puede crear el
  host y si `GetWorkspace()` vale ahí; (2) si el serializador acepta un
  array de objetos como argumento de **entrada**.

Sobre (1): `ResolveUiRoot` ya llama `GetGame().GetWorkspace()` en el
dispatch de los verbos UI (`MCPClientBridge.c:1365-1370`). Eso prueba
el workspace **con la misión rodando**, no en el constructor. La sonda
es «dónde *crear* el host una vez», no «si GetWorkspace existe».
Precedente de bloqueo de input a copiar, no a heredar: ATM
`LFPG_BTCAtmView.c:1261-1265`.

Sobre (2): `MCPArgs` hoy tiene `array<float>` y `array<string>`, no
`array<ref T>` de entrada (`MCPMessages.c:12-106`). Los arrays de
objetos viven en resultados (`MCPUiNode`, `MCPAllPlayer`, …). La sonda
es un payload de dos campos *antes* de cerrar el DTO, como pide el
plan (`:266-268`).

No entra: fase 2 (layout + host + `TryFinish`), `ui_open`/`ui_close`
(aparcados por el usuario, `fb-…-151939-bb31`), linter de layout
(abajo, paralelo).

**Por qué entra JUNTO.** La fase 1 sin las sondas deja el ciclo 6 a
ciegas. Las sondas sin la fase 1 no tienen tool que las justifique.
El `tool_lock` es el hallazgo que aplica a **cualquier** verbo futuro
que espere a un humano (`HANDOFF.md:102-105`); se paga aquí, una vez,
con el patrón que `wait_for` ya enseña (`server.py:2225-2228`).

**Entrada.** Ciclo 2 cerrado (el catálogo junior no se redibuja a la
vez que se añade un verbo modal). Desconocidos de fase 2 **no**
bloquean el arranque de la fase 1; bloquean el 6.

**Salida.** Criterio de `:214` (segunda sesión no se congela) +
rechazo de claves desconocidas y de `N+1` campos, sin abrir UI + las
dos sondas con respuesta sí/no escrita. Si (2) es no, el DTO de fase 2
no usa array de objetos de entrada (el plan ya prefiere array
ordenado; se aplana a campos declarados).

**Sesión in-game.** No para la fase 1. Las sondas de (1) pueden
colarse como *un* chequeo extra en el ciclo 6, no como sesión propia.

---

### Ciclo 6 — El siguiente PBO (uno)

<!-- REESCRITA 2026-08-17 noche: Dabs ya no es trabajo de esta hoja -->

Un rebuild, una sesión, una candidata. Si el gate saca varios fallos,
se acumulan y se genera **una** segunda candidata
(`ui-dialog-plan-fusionado.md:219-220`). No un empaquetado por bug.

**Qué entra.**

- **P1-02** loopback en el bridge servidor (`MCPBridge.c:160-172`),
  el mismo contrato que el cliente (`MCPClientBridge.c:259-264`).
- **`ui_dialog` fase 2**: host pre-creado oculto, layout único,
  `TryFinish`, job `postNow=false`, cero protocolo nuevo
  (`ui-dialog-plan-fusionado.md:215`). Solo si el ciclo 5 dejó las
  sondas resueltas. Si no, este lote sale **sin** diálogo.
- Regresión de lo gateado en el ciclo 1, **incluido el clic ATM en
  el cubo que haya salido**. Si el clic fue verde, es regresión. Si
  fue `no_handler`, se vuelve a medir una vez: un cambio de
  `MCPClientBridge.c` por el host del diálogo puede pisar la rama
  del dueño. Hash-gate de ese fichero contra el dueño, no un
  «arreglo Dabs» de esta lane.

**No entra.** La rama Dabs. Ni rediseño, ni cambio de símbolo, ni
addon opcional. Es del dueño. Si el ciclo 1 dejó `no_handler`, no se
inventa aquí un plan B.

No entra tampoco: BUG-066(c) si ya se gateó. `GetFirstHuman` como
trabajo nuevo. Tools de vehículo. UI en caliente.

**Por qué entra JUNTO.** P1-02 es `MCPBridge.c` (centinela: un
toque = un re-congelado post-gate). `ui_dialog` fase 2 es
`MCPClientBridge.c` + layout. Un PBO, una sesión. El diálogo no
usa `ui_click` como aceptación (`:24`, `:245-246`).

**Entrada.**

- Ciclo 1 verde (línea base de `action_use` / `pos_real` / `ui_*`).
- Dueño de `MCPClientBridge.c` avisado: esta lane lo va a tocar si
  viaja el diálogo. Re-hash antes de escribir.
- Ciclo 5 cerrado si `ui_dialog` viaja; si las sondas fallan, el lote
  se declara sin diálogo *antes* de empaquetar.
- Workaround AddonBuilder `-packonly` + anclas dentro del PBO, no
  `build:true` (`HANDOFF.md:428-429`).
- Misma combinación LFPowerGrid + DayZ_MCP.

**Salida.**

- Módulo Mission carga (conteo de files/classes, cero error de
  script).
- Matriz de 12 casos de `ui_dialog` si viajó (`:224-247`).
- URL no loopback rechazada en **ambos** bridges.
- Regresión del ciclo 1, clic ATM en el cubo que ya se midió.
- Centinela: las dos mitades otra vez, sobre este `MCPBridge.c`
  gateado.
- Un push. No dos.

**Sesión in-game.** Sí. La segunda y última de esta hoja.

---

### Ciclo 7 — Composición, solo si el 2 pasó

**Qué entra.** `playbook_run(name, params)` sobre el runner que ya
existe (`playbooks\runner.py`, diccionario en `playbooks\README.md:1-24`).
Una tool nueva, después de que qwen3.5 sepa adquirir lease y leer
`ready`. El propio plan lo dice: el playbook cubre *secuencias*, no el
lease (`weak-agent-consumer-ux.md:32`).

No entra: `ui_open` / `ui_close`. Siguen aparcados a propósito hasta
que existan las interfaces tipo (`HANDOFF.md:106-107`). El linter de
rects no necesita verbo nuevo (paralelo, §3).

**Por qué entra solo, y después.** Añadir una tool al catálogo **antes**
de recortarlo es el error que 4cc4 midió. P2 encima de un P0 rojo
enseña al junior un tercer nombre.

**Entrada.** Ciclo 2 en PASS (sonda qwen3.5). `place_safely` sigue
DRAFT; `playbook_run` no certifica un playbook DRAFT como FROZEN.

**Salida.** Name desconocido → `bad_args` que nombra el campo.
`--fixtures` de `place_safely` sigue verde. qwen3.5, si se re-sondea,
no tiene que usarlo para pasar el gate de lease.

**Sesión in-game.** No.

---

## 3. Lanes de agentes — qué se reparte y qué no

<!-- REESCRITA 2026-08-17 noche -->

«Paralelo» aquí no es «no toca el PBO». Es: **dos agentes escribiendo
a la vez en este árbol, que no tiene exclusión ni detección**. Hoy se
pisaron tres veces: ediciones vivas, HANDOFF mergeado a mano, y
`MCPClientBridge.c` movido bajo un PBO ya verificado. Dos tareas
independientes que tocan el mismo fichero **no** son dos lanes.

### 3.1 Grupos que sí pueden ir a la vez

Conjuntos de fichero **disjuntos**. Si un path aparece en dos filas,
esas filas no se abren juntas.

| Lane | Ciclos que cubre | Escribe | No escribe |
|---|---|---|---|
| **A — pack + gate** | 0 (chequeo símbolo, solo lectura) + 1 | `DayZ_MCP\scripts\**`, `DayZ_MCP\config.cpp`, staging del PBO, `test_task9_spawn_phase_markers.py` *solo al re-congelar post-gate*, LIVE-STATE del `HANDOFF.md` para el gate | `tools\dayz_mcp\server.py`, `loopback.py`, `session_coordination.py`, `process_lifecycle.py`, `doctor.py` |
| **B — Python de un solo dueño de `server.py`** | 2 + (de 3b) `logs_since` y autospawn + P1-04 si se funde | `tools\dayz_mcp\server.py`, `session_coordination.py`, tests de esos módulos | `DayZ_MCP\**`, `MCPClientBridge.c`, `process_lifecycle.py`, `doctor.py`, `loopback.py` (salvo que P1-03 se cuelgue *después*, no a la vez) |
| **C — caja / flake, otro dueño** | 3a + 3b sin `server.py` | `tools\tests\test_bug046_startup_deadlock.py` (3a); `process_lifecycle.py`, `doctor.py` y *sus* tests (3b) | `server.py`, `DayZ_MCP\**`, `HANDOFF.md` |
| **R — solo lectura** | re-verificar Codex, marcar buzón «cubierto por P0», chequeo del símbolo Dabs | nada de producto | todo |

Comprobación de disjuntos, por par:

- A ∩ B = ∅ (Enforce vs `server.py` / `session_coordination.py`).
- A ∩ C = ∅ (Enforce vs un test / `process_lifecycle.py` / `doctor.py`).
- B ∩ C = ∅ **solo si** C no toca `server.py`. Por eso `logs_since` y
  autospawn salieron de 3b.
- A ∩ R, B ∩ R, C ∩ R = ∅ por construcción (R no escribe).

Docs (`llms.txt`, `system_prompt.md`, linter de rects en un fichero
*nuevo*) caben en R o en una lane que no toque los calientes. No son
una cuarta lane de escritura sobre producto.

### 3.2 Qué hay que serializar aunque parezca paralelo

- **Todo lo que toca `server.py`.** P0 weak-agent, P1 alias,
  docstrings `be93`, `bridge_status.ready`, `wait_for` `ok`,
  `logs_since`, autospawn, P1-04 (`server.py:1987`), `ui_dialog`
  fase 1, instructions de FastMCP. Es un fichero. Una lane. Un
  orden: primero el paquete del ciclo 2 (incluye
  `logs_since`/autospawn/P1-04 si se funden); **después** el ciclo 5.
  Lanzar 2 y 5 a la vez es el pisado de las 04:02
  (`HANDOFF.md:193-195`) con otro nombre.
- **`MCPClientBridge.c`.** Dueño de la rama Dabs vs lane A (pack) vs
  ciclo 6 (`ui_dialog` fase 2). Nunca dos a la vez. Hoy a las 19:39
  ya se demostró.
- **`MCPBridge.c`.** Lane A (gate / centinela) vs ciclo 6 (P1-02).
  Serial.
- **`HANDOFF.md`.** Un escritor. El de hoy se mergeó a mano.
- **`loopback.py`.** P1-03 (ciclo 4) no corre a la vez que B si B
  acaba tocando whitelist; si hay duda, P1-03 espera a que B cierre.
- **El daemon / 8765 / la caja DayZ.** La sonda qwen del ciclo 2 y
  el gate del ciclo 1 no conviven. Implementar B *sí* puede
  avanzar en tests offline mientras A tiene la caja; el probe qwen
  no.

### 3.3 Cuántas lanes tiene sentido abrir

**Dos de escritura, como máximo. Una de lectura, opcional.**

Motivo: no hay exclusión ni detección. Con 3-4 sesiones hoy hubo
tres pisadas en un día, la última sobre el fichero del PBO
verificado. Una tercera lane de escritura no tiene fichero caliente
libre: `server.py`, `MCPClientBridge.c` y `HANDOFF.md` ya están
asignados o serializados. Abrirla es elegir el próximo merge a mano.

Dos es el máximo que los conjuntos de 3.1 permiten sin intersección
(A+B, o A+C, o B+C). **A+B+C a la vez es tres y se prohíbe**, aunque
los conjuntos de A/B/C sean disjuntos sobre el papel: el HANDOFF, el
daemon y el hábito de «dejo un arreglo de un byte en el fichero del
otro» no caben en el papel. Si A está en sesión in-game, la segunda
lane es B *o* C, no las dos, y R puede leer.

Una sola lane es más segura y más lenta. El usuario es el cuello de
botella del reloj; dejar C (flake 3a) parado hasta que A termine el
gate retrasa el único push. Por eso dos, no una.

### 3.4 Disciplina mínima para que esas dos no se pisen

1. **Dueño de fichero por ciclo, anunciado antes de escribir.** La
   tabla de 3.1 es el contrato. Quien no es dueño no abre el fichero
   en un editor con save. El dueño de `MCPClientBridge.c` hoy es A
   (pack); el de la rama Dabs no la reedita hasta que A cierre.
2. **Se trabaja fuera del árbol compartido y se promociona por
   hash.** Copia fresca (el `/tmp/<sesión>/` de LL-106, o un staging
   bajo `%TEMP%`), no OneDrive a pelo. Diff contra el hash tomado al
   *empezar*. Si el hash del destino cambió, no se sobrescribe: se
   para. Eso es lo que no hubo a las 19:39.
3. **Re-hash justo antes de cada write**, no al arrancar la sesión.
   El desvío de `MCPClientBridge.c` cabió en una tarde.
4. **Promoción = copy + verificar hash destino = hash origen.** No
   mtime. No «el fichero es mío porque lo toqué esta mañana».
5. **`HANDOFF.md`:** un dueño (A mientras dura el gate). La otra
   lane no lo reescribe; deja una nota en su propio staging o en el
   buzón.
6. **Después de tocar Python de daemon:** avisar y reiniciar. El
   módulo en memoria no es el disco (`HANDOFF.md:430-431`).
7. **Cierre de lane:** `session_status` sin owner si usó la caja;
   lista de paths tocados + hashes finales en una línea, no un
   HANDOFF paralelo.

---

## 4. Las cuatro preguntas

### 4.1 ¿Dabs en el gate ya preparado, o segundo ciclo?

<!-- REESCRITA 2026-08-17 noche -->

La pregunta original murió a las 19:39. **«Gatear sin rebuild» ya no
existe:** el PBO de las 16:04 no es el fuente. Queda otra pregunta:
el pack forzado se lleva la rama del dueño, y el clic se trata como
los dos cubos del ciclo 1, no como trabajo de esta hoja.

Coste de empaquetar ahora (ya no es opcional): ~4 s + recontar 11
entradas + **una** sesión. Se pierde el 11/11 viejo porque ya era
mentira. No se pierde una sesión extra: el pack iba a hacer falta
igual para `action_use` / `pos_real` el día que el fuente se desvió.

Coste de fingir que el PBO viejo sigue: gatear un binario que no
contiene `:1562-1569` y publicar un árbol que sí. El centinela no
lo caza (`MCPBridge.c` ≠ cliente).

La rama no se diseña aquí. El símbolo no se da por vivo ni por
muerto: el chequeo del ciclo 0 (config de Dabs vs `DF_Scripts` vs
el `#ifdef DabsFramework` de Expansion en `ExpansionIcons.c:242`)
sale *antes* del pack. El clic del ciclo 1 cierra el resto de la
matriz aunque salga `no_handler`. Si dispara, A queda verde sin
que esta hoja haya «hecho Dabs». Si no, A sigue siendo del dueño.

### 4.2 ¿A + C(weak-agent) + D son el mismo problema?

**No. C y D sí. A no.**

C-P0 y D son el mismo cuerpo visto dos veces. El plan weak-agent ya
enlaza los ids (`weak-agent-consumer-ux.md:76-101`). Por eso el ciclo
2 es un solo ítem del roadmap y resolver D es, en su mayoría, marcar
el buzón. Estoy de acuerdo en *esa* convergencia
(`INVENTARIO.md:155-156`).

A no es ese problema. Un modelo fuerte, con el catálogo entero y el
lease bien puesto, **tampoco** pulsa el ATM: `InvokeUiClick` solo hace
`ScriptedWidgetEventHandler.Cast` sobre `GetScript` / `GetUserData`
(`MCPClientBridge.c:1542-1558`) y, si falla, `UIManager.GetMenu()`
(`:1579-1586`). El ATM no es un `UIScriptedMenu`; el userdata de Dabs
no pasa ese `Cast`. Eso es un dispatch roto, no una señal mal
redactada. Colapsar A con C/D en el roadmap empujaría a «documentar el
clic» o a enseñarle al junior otro verbo. El clic no existe.

Comparten *historia de producto* (la superficie se pensó para un
consumidor fuerte y para UI propia). No comparten arreglo, ni capa, ni
dueño. A viaja en el PBO del ciclo 1 porque el fuente ya la tiene; el
desenlace es del dueño. C+D es Python y es el 2.

Además: el ATM pisa userdata de hover con `LFPG_ColorData`
(`LFPG_BTCAtmView.c:709` en el grep de `SetUserData`). Aunque el
`#ifdef` funcione en la raíz del layout, el walk tiene que llegar a la
vista y no quedarse en el color del botón. Eso es otro motivo para no
tratar A como «el junior no entiende la UI».

### 4.3 ¿E se paga ahora, se mitiga, o se ignora?

**Se parte. Se pagan dos agujeros. Se mitiga el resto. No se ignora.
No se construye un producto de exclusión.**

E no es un bloque con dueño porque no es una cosa. El inventario lo
dice y luego lo lista como si lo fuera (`INVENTARIO.md:153-154`). Las
cuatro entradas no comparten arreglo:

| Entrada | Qué es | Qué hago |
|---|---|---|
| `c486` dos sesiones editan el árbol; HANDOFF pisado; **tercera:** `MCPClientBridge.c` a las 19:39 bajo PBO verificado | No hay exclusión de *fuentes* | Mitigar. §3: 2 lanes, hash antes de escribir, promoción desde copia. **No** un lockfile en OneDrive. D-39 veta fontanería multiagente especulativa (`decision-log.md:50`). |
| `4979` `.git` roto → `git status` vacío = verde falso | Predicado de gate mentiroso | Pagar ahora, ciclo 3b, si se confirma. Local. |
| `7d3e` dos stacks DayZDiag, input a la ventana ajena | Lease de *procesos* + BUG-067 | No se reabre. D-33 dejó BUG-067 en mitigación fail-closed (`decision-log.md:33`); D-39 mantiene el veto de plataforma. El síntoma se mitiga con disciplina de un solo `dayz_test_run` y `session_status` limpio al cerrar. Adoptar runs ajenos es Fase 2, con `rigorous-data-audit`, no este roadmap. |
| `1d6e` `dayz_test_stop` imposible si un PID muere | `UNRECONCILED` (`process_lifecycle.py:1259-1306`) | Pagar ahora, ciclo 3b. Consumidor real: caja bloqueada. No es «más coordinación»; es el caso que el reaper no cubre. |

Ignorarlo «conscientemente» entero sería mentir: hoy se pisó trabajo
tres veces y un stop roto deja la caja para un humano. Pagar E como
fase de concurrencia sería repetir julio (D-33 se tomó por eso).
Partirlo es el único partido que no contradice D-39 ni el reloj del
usuario.

TTL 120 s (`session_coordination.py:15`, heartbeat `:1091-1121`) no
es E, pero se parece. Auto-renovar «mientras vivan los PIDs» suelta
el deadman de D-15 y deja un agente muerto con DayZ arriba dueño de
la caja para siempre. No se hace. Si LFHeli necesita celdas de 30
min, TTL **configurable** en el acquire, deadman intacto. Eso puede
viajar en un ciclo 2.1 de diez líneas, no como auto-lease.

### 4.4 Codex vs «no lease implícito»

**Gana el plan weak-agent. El lease sigue explícito.** El choque se
cierra aquí, no se deja para el implementador.

Codex §7.4 (`reviews\2026-08-17-codex-technical-handoff-repo-audit.md:433-438`)
quiere que toda mutación simple adquiera y suelte sola, y que
`session_*` quede en expert. El plan weak-agent lo excluye con el
usuario delante (`weak-agent-consumer-ux.md:35-37`). No son
matices: uno esconde la autoridad de la caja, el otro la nombra en
el error.

Quién gana, y por qué no es lealtad a un documento:

- El lease es el recurso compartido de **esta** máquina (D-15,
  `decision-log.md:24`). Hoy hay 3-4 sesiones y ya hubo tres pisadas.
  Auto-lease en ese entorno es exactamente «otra sesión mutó porque
  el verbo fue amable». El incidente del input en la ventana ajena
  (`fb-…-7d3e`) es el preview.
- Codex revisó `31040de`. No vio el paquete weak-agent, ni las
  sondas qwen, ni D-39. Su §7-8 es un producto distinto (dos
  superficies, `scenario_run`, `scene_apply`). Eso choca además con
  «el multitool es el MCP; guardas = playbooks, no tools»
  (`HANDOFF.md:248-251`; el playbook se declara dato en
  `playbooks\README.md:1-6`). La cita `product-spec.md:107-108` del
  HANDOFF está **caducada**: esas líneas son G0/G1 de vehículos
  (`product-spec.md:112-113`). La decisión vive en el HANDOFF y en
  D-39, no ahí.
- D-39: trabajo por demanda de un consumidor real, no por roadmap de
  plataforma (`decision-log.md:50`). El consumidor medido hoy es
  qwen3.5 tropezando con nombres y con `ok:true`. No es «faltan
  `scenario_run` y auto-lease».

Qué de Codex **sí** gana, porque no es el choque: P1-03, P1-04, el
resto de P1-01, P1-02 (en el ciclo 6). `dayz_ready` ≡
`bridge_status.ready` del ciclo 2; no se añade una tool. Envelope con
`remediation` ≡ errores-receta del P0; no se inventa un JSON paralelo
en este tramo.

**Evidencia que reabriría auto-lease.** Una sola, y después del ciclo
2, no antes: qwen3.5 en PASS de P0 (ve `session_acquire_wait` /
`lease_acquire`, entiende `lease_required`, no declara listo un peer
stale) **y aun así** no completa una mutación *solo* porque acquire
es una llamada aparte. Entonces se habla de auto-lease **opt-in** en
una caja de una sesión, nunca como default en esta. Hasta que esa
sonda exista, §7.4 está muerto.

`dayz_test_run` ya encapsula lease de *lifecycle* (Codex lo cita).
Eso no se toca. No se extiende a `player_teleport`.

---

## 5. Lo que no haría nunca

Del inventario, no de un backlog imaginario.

- **Tratar el pack forzado como prueba de que la rama Dabs vive.**
  Viaja porque está en el fuente, no porque el símbolo esté
  comprobado. Tampoco se «arregla» el `#ifdef` en esta hoja.
- **Auto-lease por mutación** (Codex §7.4) y **dos superficies
  simple/expert** (§7.3) y **`scenario_run` / `scene_apply` como
  tools** (§8.1). Guardas = playbooks (datos). Trabajo por demanda
  (D-39). Eso no se construye «por si el junior no encadena».
- **Las cuatro tools que Gemini inventó**
  (`dayz_search_api_symbol` y compañía,
  `reviews\2026-08-17-gemini-flash46-ai-native-pack.md:4-6`). Son otro
  producto. El grano (`llms.txt`, schemas cerrados, `system_prompt.md`)
  sí; el texto no.
- **`search_tools`.** Otro verbo que hay que descubrir
  (`weak-agent-consumer-ux.md:36`).
- **UI en caliente (`ui_open`/`ui_close`) antes de las interfaces
  tipo.** El usuario lo aparcó. El linter de rects no desbloquea esos
  verbos.
- **Auto-renovar el lease solo porque los PIDs de DayZ siguen
  vivos.** Rompe D-15. TTL configurable, sí; deadman por proceso de
  juego, no.
- **Un lockfile / servicio de exclusión sobre `DayZ_MCP_dev`.** El
  fallo es real; el remedio, en OneDrive y con 3-4 agentes, es peor.
  D-39 veta esa fontanería.
- **Reabrir BUG-067 / Fase 2 / cola de runs / `vehicle_trace`.**
  Siguen donde D-33/D-39 los dejaron. Esta hoja no los usa para
  rellenar un ciclo.
- **Reescribir el historial git por P1-01** (Codex lo «valora»). Las
  rutas de la máquina autora en commits viejos no valen una
  reescritura pública.
- **Tasks / Resources / Prompts MCP** (Codex §7.5-7.7) como producto
  ahora. Tasks es experimental en la spec que él mismo cita. No hay
  consumidor pidiéndolo.
- **ControlPlane.** Congelado (`HANDOFF.md:249-250`).
- **Cookbook largo en cada description.** Infla los 10k
  (`weak-agent-consumer-ux.md:38`).
- **Rehacer como trabajo los ids de D que el ciclo 2 cierra.** Se
  marcan.
- **`dayz_test_run` con `build:true`.** Sigue el workaround
  (`HANDOFF.md:428-429`). No es ítem de esta hoja; es trampa de
  entorno.
- **Un push con el flake 3a rojo.** El repo público heredaría un
  1/3 aleatorio (`export-reds.json:8-12`).

---

## 6. El riesgo mayor de *esta* hoja

<!-- REESCRITA 2026-08-17 noche -->

Ya no es «reconstruir a mitad de sesión». El pack es inevitable.

El riesgo es **declarar el ciclo 1 rojo —o abrir una tercera lane
para «arreglar Dabs»— si el clic sale `no_handler`**, cuando el resto
(`action_use`, `pos_real`, `ui_set_text`) haya pasado. Eso quema la
sesión del usuario, pisa `MCPClientBridge.c` otra vez, y convierte
una restricción del dueño en trabajo de esta hoja.

El segundo modo: abrir A+B+C a la vez porque los conjuntos «son
disjuntos en la tabla». La tercera pisada de hoy fue exactamente
eso: un fichero que nadie tenía como exclusivo se movió bajo un
artefacto ya medido.

La hoja solo funciona si el ciclo 1 se declara **cerrado** con el
pack nuevo verificado, la matriz de `pos_real`/`action_use` verde, el
clic archivado en *uno* de los dos cubos, el flake 3a verde y un
push. Y si durante ese ciclo hay como mucho otra lane de escritura,
con hash tomado *justo antes* de promocionar.

---

## Anexo — qué se abrió y qué se dio por bueno

<!-- REESCRITO el bloque de no-verificado -->

**Abierto y citado (esta pasada, además de la anterior).**
`MCPClientBridge.c:1562-1569` (rama viva),
`LFPowerGrid\config.cpp:190` (`DF_Scripts`),
`LFPG_BalanceProvider_LBmaster.c:8`, `LFPG_ActionPairSensor.c:84`,
`DayZ_MCP\config.cpp:8`,
`LF_UILab\config.cpp:10` (`DabsFramework` en requiredAddons),
`_expansion_src\...\ExpansionIcons.c:242`,
`_codex_tmp\...\DayZExpansion_Core_Defines.c:91`,
`P:\Mods\@Dabs Framework\meta.cpp`.

**Medido por el usuario, no re-hasheado por mí.**
mtime 19:39:08, 62.887 B, sha `A6C80927F85437AD…` vs empaquetado
62.528 B / `4108972B97F9…`. Aceptado como hecho.

**Sigue sin comprobar.**
`config.cpp` extraído de Dabs (CfgMods vs CfgPatches); si el source
extraído de Dabs contiene `#ifdef DabsFramework` (el usuario dice
que no; yo no lo abrí); sha del PBO *desplegado*; `.git` roto;
economía 63 % / 7,1 min; 38/73 del buzón; `ScriptedViewBaseHandler`;
cp1252 concreto; `dayz_test_run succeeded` con cliente muerto.
