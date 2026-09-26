Auditoría de citas del README: primero leo el test que ya cubre esto y el propio README, luego abro cada fuente citada.El test cubre recuento de tools, Python 3.14, sparse `addon/` y citas volátiles. Ahora abro cada fuente que el README cita y no está en ese watchdog.`tools/poc-verdict.json` no está en el árbol. Sigo con el resto de citas y con lo que el test de tools no cubre.Tres citas apuntan a rutas que no existen en el árbol. Compruebo si están excluidas y abro el resto de fuentes.PowerShell rompió el one-liner; lo paso por stdin y sigo con vanilla y el resto de citas.Vanilla está en `P:\scripts`. Abro cada `.c` citado; para el clone público sigue siendo inalcanzable, pero hay que ver si la línea es cierta.Los ficheros citados existen en el árbol privado, no en esta copia. Abro esas líneas para ver si los números son ciertos aunque el clone público no pueda verlos.**Las citas internas del README no son un bloque homogéneo.** Lo que un clone público puede abrir, en general cuadra. Lo que el README presenta como evidencia in-tree y un extraño no puede abrir, es el agujero real: no es que los números estén inventados; es que **el veredicto vive fuera del árbol publicado**.

`test_docs_truth.py` (y `PublicToolCountDocsTest`) ya clavan recuento de tools, Python 3.14, sparse `addon/`, el flake `test_bug046_startup_deadlock`, y la prohibición de citar `HANDOFF.md`/`server.py` por línea. Abajo va **lo que ese watchdog no mira**.

Convención: **CITA_MOVIDA** aparece en la tabla (el encargo lo pide) y no entra en los cinco cubos del JSON.

| afirmación (cita literal, corta) | ubicacion en README | veredicto | valor real / evidencia |
|---|---|---|---|
| **0.0313 m** (`product-spec.md:42`) | L88 | **CUADRA** | `product-spec.md:42`: «bridge vs marker independiente = **0.0313 m** < 0.5 m» |
| pass line **< 0.5 m** | L88 | **CUADRA** | misma celda A1, `product-spec.md:42` |
| on-disk **0.031348823463742695 m** (`tools/poc-verdict.json:117`) | L88 | **CITA_ROTA** | el fichero **no está** en este árbol. En el árbol privado `DayZ_MCP_dev\tools\poc-verdict.json:117` sí: `"distance_m": 0.031348823463742695` |
| 0.0313 m is the player's **vertical settle** (`HANDOFF.md`, fase-0 closure note) | L88 | **CITA_ROTA** | `HANDOFF.md` **no está** aquí. Privado `HANDOFF.md:3026`: «0.0313m es el asentamiento vertical real del player» |
| earlier harness printed **0.000 m** | L88 | **SIN_FUENTE** | esa cláusula no cita nada. El `product-spec.md:42` no cuenta el 0.000; el HANDOFF privado alude al A1 tautológico, y no está publicado |
| Python held `/poll` for **600 ms** | L90 | **CUADRA** | `product-spec.md:43`: «delay 600ms». (El verdict privado tiene `"delay_ms": 600` en :135) |
| `ticks_in_flight` = **4668**, «the figure the in-tree verdict carries and **a clone can read for itself**» (`tools/poc-verdict.json:140`) | L90 | **CITA_ROTA** | fichero ausente. Privado `:140` sí tiene `"ticks_in_flight": 4668`. La frase *a clone can read for itself* es **falsa** para este árbol |
| original run recorded **4741** (`product-spec.md:43`, run_045213) | L90 | **CUADRA** | `product-spec.md:43`: «ticks_in_flight=4741 con delay 600ms; run_045213» |
| 4741 también en `reviews/2026-06-07-r21-claude-poc-code.md:40` | L90 | **CITA_ROTA** | `reviews/` **no existe** aquí. Privado `:40`: «`ticks_in_flight=4741`» |
| pass line **≥ 5** | L90 | **CUADRA** | `product-spec.md:43`: «≥ 5» |
| blocking `GET_now` would read **~0** | L90 | **SIN_FUENTE** | contrafactual; ningún fichero del árbol mide un ~0 |
| `fps_in_flight` **~7901** (`reviews/...:40-41`) | L90 | **CITA_ROTA** | review ausente. Privado `:40`: «`fps_in_flight≈7901`» (el verdict privado actual tiene **7780.0** en `:137`, cifra que el README no afirma) |
| heading **90°** → **92.1°** (error **2.1°**), run `1a1cb6e1-…`, **76.6 m**, **0.95 m**, **270°→270.1°**, **41.7 m / 46 s**, **0.02 m**, release **171.7°** / **0.25 m/s** (`test_task9_spawn_phase_markers.py:101-105`) | L92 | **CUADRA** | comentario `:101-105` del test, mismo UUID y las mismas cifras |
| 90° **(east, +X)** | L92 | **SIN_FUENTE** | el test citado **no** dice este/este/+X. Eso está en el HANDOFF privado `:1157` y en `inbox-snapshot.jsonl:245`, no citados |
| misma nota en `HANDOFF.md`, infected_drive promotion | L92 | **CITA_ROTA** | HANDOFF ausente. Privado `:1143-1166` tiene la tabla 92,1° / 76,6 m / 270,1° |
| `speed=3` measured **0.91 m/s** (`test_task9…:105`) | L104 | **CUADRA** | `:105`: «speed=3 measured 0.91 m/s» |
| misma cifra vía `HANDOFF.md` | L104 | **CITA_ROTA** | privado `:1175` |
| B3: `engine_on_server=1`, `speedo≈0`, `pos_delta≈0`, throttle **1.0**, PHYSICS (`product-spec.md:56`) | L94 | **CUADRA** | `product-spec.md:56`, misma frase |
| no drive cars from `MissionServer` (`product-spec.md:325`) | L94 | **CUADRA** | `:325`: «conducir desde el server **descartado**» |
| misma decisión (`decisions/decision-log.md:15`) | L94 | **CITA_ROTA** (+ **CITA_MOVIDA** en el privado) | `decisions/` ausente. Privado `:15` es **D-06** (2026-06-07, probe client-auth), no el descarte post-probe del 08 |
| MakeScreenshot 2×, cero `.dds`, no `ScreenShots` (`dayz-mcp-architecture.md:17-24`) | L98 | **CUADRA** | `:21-23` |
| `RenderTargetWidget` display-only (`architecture.md:28-32`) | L98 | **CUADRA** | `:28-30` |
| `Graphics.CopyFromScreen`; meanB **65**, nbRatio **0.999** (`architecture.md:41-42`) | L98 | **CUADRA** | `:41-42` |
| headless returns data, not pixels (`architecture.md:62-64`; `product-spec.md:162-163`) | L98 | **CUADRA** | ambos rangos |
| no OS keystrokes / OCR (`product-spec.md:160-161`) | L114 | **CUADRA** | `:160-161` |
| several sessions, **one** game (`product-spec.md:164-166`) | L114 | **CUADRA** | `:164-166` |
| Control is `CreateObjectEx` / `StartCommand_Vehicle` / `MissionServer` reads (`product-spec.md:160-166`) | L114 | **NO_CUADRA** | **cero** hits de `CreateObjectEx` o `StartCommand_Vehicle` en todo `product-spec.md`. `MissionServer` aparece en `:193` y `:333`, no en 160-166. Esos símbolos están en `dayz-mcp-architecture.md:79` y `:84` |
| bind **127.0.0.1**, no `0.0.0.0` (`product-spec.md:168`) | L100, L127-129, L180 | **CUADRA** | `product-spec.md:168`; código `loopback.py:3190` y `:3219` (`refusing non-loopback bind`) |
| `SetHeader` = Content-Type only (`restapi.c:135-141`; `decisions/decision-log.md:11`) | L100 | **NO_ALCANZABLE** + **CITA_ROTA** | vanilla no está en el repo; `decisions/` tampoco. En `P:\scripts\3_game\http\restapi.c:135-141` el proto es `SetHeader` «Set Content-Type header». Privado `decision-log.md:11` = D-02 «Key en query string». Corroboración in-tree: `addon\...\MCPBridge.c:200` `SetHeader("application/json")`, `:226` `"poll?key="`; `loopback.py:2476` `qs.get("key")` |
| `POST_now` thread-blocking (`restapi.c:125-128`) | L112 | **NO_ALCANZABLE** | `P:\scripts\...\restapi.c:125-128`: «thread blocking operation!» + `POST_now` |
| bridge uses callback GET/POST only (`architecture.md` §9, «Bloqueo del loop») | L112 | **CUADRA** | `architecture.md:270-273` es §9 y contiene esa viñeta. En `addon` no hay `GET_now`/`POST_now`; sí `m_Ctx.GET` / `POST` (`MCPBridge.c:232`, `:3392`) |
| `SetTimeMultiplier(0)` freezes sim, animations included (`world.c:19`; `architecture.md:185-187`) | L102 | **NO_ALCANZABLE** + **CUADRA** | vanilla `:19` es el proto, **no** dice que congele animaciones. El comportamiento está en `architecture.md:185-187` |
| `ExecuteEnforceScript` Developer-only (`game.c:776`) | L108 | **NO_ALCANZABLE** | `P:\scripts\3_game\global\game.c:776`: «Delevoper only: Executes Enforce Script…» |
| returned `false` under `NO_GUI` (`product-spec.md:171-177`; `reviews/2026-06-10-fase4b-gate-ingame.md:55-66`) | L108 | **CUADRA** + **CITA_ROTA** | `product-spec.md:171-177` sí lo dice. El review **no está** aquí; privado `:55-66` cuadra (wrapper vanilla, `NO_GUI`, `false`) |
| opt-in breakglass (`product-spec.md:167`) | L108 | **CUADRA** | `:167` |
| `ActionStartEngine` returns on `INSTANCETYPE_SERVER` when PHYSICS (`actionstartengine.c:51-58`) | L94 | **NO_ALCANZABLE** | `P:\scripts\...\actionstartengine.c:51-57`: exactamente ese early-return |
| `MakeScreenshot` proto (`proto.c:142`) | L63, L98 | **NO_ALCANZABLE** | `P:\scripts\1_core\proto\proto.c:142`: `proto native void MakeScreenshot` |
| `HumanInputController.Override*` (`human.c:234-243`) | L119 | **NO_ALCANZABLE** | `P:\scripts\3_game\human.c:234,237,240,243` — los cuatro protos, en orden |
| `AIWorld.FindPath` / `RaycastNavMesh` / `SampleNavmeshPosition` / `PGFilter.SetCost`; `private void AIWorld()` (`aiworld.c`) | L118 | **NO_ALCANZABLE** | `P:\scripts\3_game\ai\aiworld.c:67,72,98,110,122`. `SetCost` no está pegado a los otros tres (línea 67 vs 98+) |
| vanilla bot FSM under `4_world/systems/bot/` | L119 | **NO_ALCANZABLE** | el directorio existe en `P:\scripts\4_world\systems\bot\` (`botfsm.c`, etc.) |
| `wait_for` timeout → `ok: true` / `satisfied: false` (`tools/README-mcp.md:124`) | L110 | **CUADRA** | `README-mcp.md:124`; código `server.py:1888-1891` (`"ok": True` siempre) y `:2070-2075` (timeout → `satisfied=False`) |
| `pattern` substring, `lookback_from="launch"` | L110 | **CUADRA** | `README-mcp.md:126`; descripción de `wait_for` en `server.py:3665-3669` |
| chat no está en script/RPT; `.ADM` no lo lee ninguna tool (`README-mcp.md` + descripciones en `server.py`) | L110 | **CUADRA** + **CITA_MOVIDA** | `README-mcp.md:128-129` cuadra. En `server.py` el texto está en `instructions` `:2365-2366`, **no** en las `description=` de `wait_for`/`logs_since` |
| `pyproject.toml` still declares `>=3.10`, see `tools/README-mcp.md` | L125-126 | **CUADRA** | `tools/pyproject.toml:8`; `README-mcp.md:5`. (El 3.14 del instalador lo cubre `InstallerPythonDocsTest`) |
| process tests wait **12 s**; **four** crash stages | L231-233 | **CUADRA** | `test_bug046_startup_deadlock.py:819` `timeout=12.0`; `:790` `stages = ("migration", "bind", "activation", "status")` |
| a single flaky run reports up to **five** `TimeoutExpired` | L232-234 | **SIN_FUENTE** | el fichero no contiene un 5. Ese test lanza **4** `subprocess.run(..., timeout=12.0)`. Otro test del módulo hace `communicate(..., 12.0)` a **6** hijos |
| record a **20 Hz** drive trace | L24, L36 | **SIN_FUENTE** | ninguna cita. Valor real: `CONTROL_SAMPLE_HZ = 20` en `vehicle_trace.py:17`; default `sample_hz: int = 20` en `server.py:3469` |
| build output is **tens of megabytes** | L198-199 | **SIN_FUENTE** | sin cita. `.gitignore:15-16` dice «**40+ MB**» |
| Three run modes (`--client` / `--daemon` / no flag) | L171-176 | **SIN_FUENTE** | sin cita. `server_cli.py:66-88` y `set_defaults(mode="embedded")` cuadran |
| MIT — see LICENSE | L238 | **CUADRA** | `LICENSE:1` «MIT License» |
| `modded class MissionServer` … `OnUpdate` | L136 | **CUADRA** | `addon\scripts\5_Mission\MissionServer.c:1` y `:19` (este checkout **sí** trae `addon/`) |
| Native launcher host policy (link a `tools/README-mcp.md`) | L195 | **CUADRA** | sección en `README-mcp.md:90` |
| T165276 (`MakeScreenshot` roto en diag) | L63, L98 | no verificado aquí | URL externa; no la abrí. El repo la **repite** en `architecture.md:23-24` |

### Cubierto por `test_docs_truth` / `PublicToolCountDocsTest` (no repetido)
- **54 tools** + lista + fórmula `+ exec_enforce` + `--pin-clis` / «does not read that pin»: `PublicToolCountDocsTest`. AST de `server.py`: 54 `@app.tool` (incluye `exec_enforce` condicional; `lease_acquire` entra por `app.add_tool` en `:2437-2441`). El listado del README tiene 54 nombres únicos, con `lease_acquire` y sin `exec_enforce`.
- Python **3.14** vs no prometer 3.10+: `InstallerPythonDocsTest` (`install-mcp.ps1:40` `& $py.Source -3.14`).
- divulgación sparse de `addon/`: `SparseAddonDocsTest`.
- el flake se llama `test_bug046_startup_deadlock`: `FreshCloneFlakeDocsTest`.
- README no cita `HANDOFF.md:N` ni `server.py:N`: `HandoffLineCiteDocsTest` / `VolatileCiteDocsTest`.
- símbolos vanilla `file.c:N` si `P:\scripts` está montado: `VanillaSymbolCitationsDocsTest` (un clone de GitHub **no** tiene `P:`; por eso esas filas siguen como **NO_ALCANZABLE**).

### Conteos (filas de la tabla, sin lo ya cubierto por test)

| veredicto | N |
|---|---|
| CUADRA | 24 |
| NO_CUADRA | 1 |
| CITA_ROTA | 10 |
| SIN_FUENTE | 6 |
| NO_ALCANZABLE | 9 |
| CITA_MOVIDA (aparte) | 2 |

No es «35 de 35 cuadran». Tampoco es que el README mienta en las cifras que un clone **sí** puede abrir.

### Lo que un desconocido hostil tumba en 5 minutos (por daño)

1. **«a clone can read for itself (`tools/poc-verdict.json:140`)»** — el fichero no existe. Es la frase más cara: el README se presenta como *measured, not claimed* y apunta a un artefacto que el clone no tiene. (El número 4668 *sí* está en el árbol privado, línea 140.)
2. **`tools/poc-verdict.json:117`** (0.031348…) — mismo agujero. En público solo queda el 0.0313 redondeado de `product-spec.md:42`, que es el spec citándose a sí mismo: exactamente la objeción del revisor.
3. **`reviews/…` y `decisions/decision-log.md` y `HANDOFF.md`** — citados como paths de repo; **cero** de esos directorios/ficheros está en este árbol. Un `dir` de 10 segundos los mata todos. En el árbol privado las líneas, en lo sustancial, **sí dicen eso** (salvo `decision-log.md:15` = D-06 probe, no el descarte del día 8).
4. **Seguir el propio puntero a `dayz-mcp-architecture.md` (L81, «security model»)** — `architecture.md:246` sigue diciendo header `Authorization`. El README y `loopback.py:2475-2476` usan `?key=`. Contradicción interna a un clic.
5. **`product-spec.md:160-166` no contiene `CreateObjectEx` ni `StartCommand_Vehicle`** — grep de 5 segundos. Única **NO_CUADRA** de un fichero que *sí* está publicado.
6. **«up to five TimeoutExpired»** — el test tiene **4** stages, no 5.
7. **Citas vanilla `file.c:N`** — un clone de GitHub no tiene `P:\scripts`. Son ciertas *si* montas vanilla (las abrí; cuadran), pero desde el texto público son inalcanzables.
8. **20 Hz** y **(east, +X)** — números/orientación sin cita pública. El 20 Hz es real en código; el este/+X solo está en HANDOFF/inbox no publicados.

### Qué número sostendría yo en público, y cuál no

**Sí:** 54 tools; Python 3.14; **0.0313 m** y **< 0.5 m** (`product-spec.md:42`); **4741 / 600 ms / ≥ 5** (`product-spec.md:43`); las cifras de `infected_drive` en `test_task9_spawn_phase_markers.py:101-105`; bind `127.0.0.1`; `wait_for` → `ok: true` / `satisfied: false`; `speed=3` → 0.91 m/s en el mismo comentario; probe B3 en `product-spec.md:56`; probe MakeScreenshot en `architecture.md:17-24`; MIT.

**No, no sin decir “esto vive en el árbol privado”:** 4668 como “in-tree verdict”; 0.031348… como on-disk; ~7901; “un clone lo lee solo”; interpretaciones que solo están en `HANDOFF.md` (asentamiento vertical); `CreateObjectEx` anclado a `product-spec.md:160-166`; “five TimeoutExpired”.

El 20 Hz lo sostendría como default de `vehicle_trace`, no como medición citada.

### Qué no pude comprobar

- El ticket **T165276** en feedback.bistudio.com (no lo abrí).
- Comportamiento in-game (prohibido lanzar procesos/MCP/suite).
- Si GitHub HEAD omite esos ficheros **a propósito** (este checkout no trae `.git`; no hay `git ls-files`).
- Cómo unittest **presenta** N `TimeoutExpired` en una corrida flaky (leí el código, no corrí el test).
- `addon/` “tracked in git”: los ficheros están; sin `.git` aquí no vi el índice.

Los path:line de la columna “valor real” los abrí. Los del árbol privado son `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\…`, no de este checkout.

```json
{"status":"ok","summary":"Citas a ficheros publicados cuadran; el agujero es evidencia interna no publicada (poc-verdict/reviews/decisions/HANDOFF) más una NO_CUADRA real en product-spec:160-166.","counts":{"CUADRA":24,"NO_CUADRA":1,"CITA_ROTA":10,"SIN_FUENTE":6,"NO_ALCANZABLE":9},"tumbables":["clone can read tools/poc-verdict.json:140 (fichero ausente; 4668 solo en árbol privado)","tools/poc-verdict.json:117 0.031348… ausente","reviews/ + decisions/ + HANDOFF.md citados y ausentes","architecture.md:246 Authorization vs README ?key=","product-spec.md:160-166 no tiene CreateObjectEx/StartCommand_Vehicle","five TimeoutExpired vs 4 stages","vanilla file.c:N inalcanzable en un clone","20 Hz y (east, +X) sin cita pública"],"verified":["README.md entero","product-spec.md:42,43,56,160-177,325","dayz-mcp-architecture.md:17-42,62-64,79,84,185-187,244-273","tools/README-mcp.md:5,90,124-129","tools/poc-verdict.json privado :117,:135-140","reviews privados :40-41 y :55-66","decision-log.md privado :11,:15","HANDOFF.md privado :1143-1200,:3023-3026","test_task9_spawn_phase_markers.py:95-116","test_bug046_startup_deadlock.py:57-65,:790-819","server.py:1888-1891,:2365-2366,:2437-2441,:3469,:3665-3675","loopback.py:1359,:2475-2476,:3190,:3219","vehicle_trace.py:17","server_cli.py:66-88","pyproject.toml:8","install-mcp.ps1:40","LICENSE:1","MissionServer.c:1,19","MCPBridge.c:200,226,232","P:\\scripts restapi.c:103-141, proto.c:142, world.c:19, game.c:776, human.c:234-243, actionstartengine.c:51-58, aiworld.c:67-122"],"not_verified":["T165276 HTTP","in-game","git ls-files de este copy (sin .git)","presentación unittest de TimeoutExpired","manifiesto de publish que excluye HANDOFF/reviews/decisions/poc-verdict"]}
```