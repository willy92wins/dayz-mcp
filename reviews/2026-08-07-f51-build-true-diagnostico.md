# F5.1 — `dayz_test_run` con `build:true`: diagnóstico acotado (NO cerrado)

> Sesión 2026-08-07 (nocturna). **Estado: espacio de causas reducido, causa NO
> confirmada.** No hubo reproducción: había un run ajeno vivo (ver §Bloqueo).
> Todo lo de aquí es lectura de código + una medición offline, con cita.

## Qué se sabía antes (del HANDOFF, medido en sesiones previas)

| Hecho | Valor |
|---|---|
| Síntoma | `ToolError("dayz_test_failed")` genérico, ~16 s, **sin escribir el PBO** |
| Reproducciones | 2, mismo resultado |
| Sin `build` | funciona (`mode=server` 5,1 s; `mode=all` 28,6 s) |
| `preflight:true` | pasa en 1,5 s |
| AddonBuilder a mano | `Build Successful`, exit 0, ~3,4 s |

## Precondición dura verificada esta sesión

**El worker sellado ES el fuente.** `dayz_test_worker.py` está en
`PACKAGED_MODULES` (`build_native_launcher.py:43,49`) y su SHA vive en el
manifest (`:846`). Comparado host-direct:

- fuente `tools\dayz_mcp\dayz_test_worker.py` → `ab736214241426af…606bb5b4`
- `native-launchers\dayz-test-v1\closure-manifest.json` → **el mismo**

Luego razonar sobre el fuente es legítimo aquí; no se está diagnosticando una
copia obsoleta ([[dayz-mcp-sealed-bundle-hides-source-edits]]).

## Descartes, con su porqué

El `except Exception` de `server.py` solo captura lo **no tipado**: un
`DayzTestToolError` sale por la rama de arriba como su `code` pelado. Por tanto:

1. **NO es el `_failed("build_failed")` del worker** (`dayz_test_worker.py:563`).
   Corre en OTRO proceso y viaja como terminal tipado; `_compact_result`
   (`dayz_test_tool.py:336-346`) lo convierte en un **dict** `status=failed` con
   `error_code`, nunca en excepción. Si el síntoma fuese un build fallido limpio,
   se vería `status=failed`, no un `ToolError`.
2. **NO son los `ValueError` de `native_process_guard`**
   (`invalid_children` `:276`, `invalid_process_name` `:285`). Están dentro de un
   `try` cuyo `except Exception` (`:288-294`) los convierte en dict de failure:
   **no se propagan**.
3. **NO es la vía del stderr.** El hijo nativo tiene `hStdError` a NUL
   (`launcher.cpp:1302`), y además el `stderr` que mira `parse_worker_terminal`
   (`dayz_test_tool.py:243`) es el buffer del lado Python, que el backend solo
   alimenta con **stdout** — el otro handle se drena como *announcements*
   (`native_launcher_backend.py:1135-1141`). Y aunque disparara, `or stderr` hace
   `_fail("terminal_invalid")`, que es **tipado**.

## Candidatos vivos, ordenados

Todos en el **proceso Python del MCP** (no en el worker), todos no tipados.
Los cuatro primeros son los que vi yo; los cinco siguientes los añadió el R22 de
Grok, y **la exhaustividad era justo el punto flojo de mi lista**:

| Candidato | Tipo | Por qué llega al genérico |
|---|---|---|
| `native_launcher_backend` | `NativeLauncherBackendError(RuntimeError)` `:168` | no es `DayzTestToolError` |
| `native_launcher_transaction` | `NativeLauncherTransactionError(RuntimeError)` `:12` | idem |
| `launcher_registry` / `open_approved_launcher` | `ValueError` | dentro de `execute_dayz_test_run` |
| `secure_launcher` | `ValueError` (`:119`/`:189`) | sin envolver |
| **`LeaseCleanupError` / `LeaseHeartbeatError`** | `RuntimeError` (`lease_supervisor.py:24-33`, verificado) | la transacción los eleva como primario o post-cleanup |
| **`OSError` de `open_approved_launcher`** | `OSError` (`launcher_registry.py:194`) | la tabla solo citaba su `ValueError` |
| **`ValueError` bare de parse/accredit** | `ValueError` | `execute_native_launcher_transaction` llama `parse_dayz_test_request` / `accredit_request_paths` sin envolver |
| **Excepción en el `finally` del redactor** | `ValueError` o tipado | `secure_launcher.py:138-142`: el `flush()` puede lanzar DESPUÉS de un exit code válido. Yo medí el redactor como coste y lo descarté; **no lo consideré como fuente de excepción en el cierre** |

★ **El mecanismo build-only más explicativo (R22, verificado contra fichero):**
con `build:true` el debug gate aprueba `AddonBuilder` y sus helpers/DLLs
(`native_launcher_backend.py:1300-1308`). Si lo rechaza →
`failure = "native_debug_gate_rejected"` + `close_job()` con `KILL_ON_JOB_CLOSE`
(`:1316-1320`). Eso explica **build-only, sin PBO escrito y con el job cerrado**
mejor que cualquier `ValueError` de registry. Y **F1.4 solo dirá el TIPO**
(`NativeLauncherBackendError`), no la cadena `failure` que lo discrimina.

**Hipótesis mía que quedó DEBILITADA, y se registra para que nadie la repita.**
Sospeché de `_IncrementalRedactor` (`secure_launcher.py:42-58`), que consume byte
a byte con `bytearray.pop(0)` — O(n) por byte. Medido offline:

| total | chunk | s | MB/s |
|---|---|---|---|
| 1 MB | 4 KB | 0,528 | 1,89 |
| 1 MB | 64 KB | 0,890 | 1,12 |
| 1 MB | 1 MB | 7,028 | 0,14 |

Es cuadrático **en el tamaño de chunk**, pero el chunk real está capado a
**16 KB** (`native_launcher_backend.py:959`), donde el coste es lineal a
~1,5-1,7 MB/s (8 MB → 5,0 s). Y sobre todo: al sink solo llega el **stdout** del
PE, que es el terminal JSON (≤4096 B), no el log de AddonBuilder. **Sigue siendo
un cuello de botella real y bloquea el event loop, pero no explica `build:true`.**

## El instrumento ya está desplegado

F1.4 hace que ese mismo fallo emita **`dayz_test_failed:<TipoDeExcepción>`**.
Una sola reproducción nombra la clase y decide entre los cuatro candidatos.
Verificado con control de mutación: revertido el fix, el mensaje vuelve a
`dayz_test_failed` pelado.

**Ojo con la caché**: `server.py` no está sellado pero el proceso cliente MCP lo
cargó al arrancar. Para que F1.4 surta efecto hay que **reiniciar el cliente MCP**
(o ejecutar `execute_dayz_test_run` desde un script con el venv, que además
captura el traceback entero en vez de solo el tipo). F2.3 ahora delata este caso
con el aviso `daemon_module_stale`.

## Bloqueo de esta sesión

No se reprodujo **a propósito**. A las 03:50 había un run ajeno vivo:
`run_id 02d2ee1a-75a4-4268-b0d1-1d2c8cd5404e`, `RUNNING_IDLE`, label
`@DayZ_MCP server`, pids 19096 (server) y 69144 (client), **sin owner** y con
ambos peers `legacy_blocked` sin haber polleado nunca. Lanzar otro run habría
chocado (1 cuenta Steam = 1 cliente ⇒ kick 179) y el lifecycle no es mío.

## Siguiente paso mínimo `[corregido por el R22 — la versión anterior era insegura]`

★ **Error que tenía este documento y que el R22 tumbó**: yo proponía
`build:true, pack_only:true` dando por hecho que «si falla no levanta el juego».
**`pack_only` no es "solo empaquetar"**: solo cambia flags de AddonBuilder
(`dayz_test_worker.py:543-554`) y, **si el build termina bien, el worker sigue al
`mode` y arranca DayZ** (`:567+`). Y yo ni siquiera fijaba `mode`. Es decir: el
experimento podía levantar el juego sin querer.

Y F1.4 **no basta como discriminador final**: pone el TIPO en el wire, pero si
sale `NativeLauncherBackendError` quedan sin separar `native_debug_gate_rejected`,
`native_job_cleanup_incomplete`, `native_launcher_start_timeout`…

**Experimento preferido — script con el venv, no la tool MCP.** Captura el
traceback entero (tipo + `failure` + `__cause__`), no depende de reiniciar el
cliente MCP, y no arrastra al host:

1. `session_status` + `bridge_status`: cero runs ajenos.
2. Desde el venv del proyecto, con un `ClientRuntime` cableado como en
   `--client`, llamar `dayz_test_tool.execute_dayz_test_run(runtime,
   project=…, mode="server", build=True, pack_only=True)` dentro de
   `try/except Exception` con `traceback.print_exc()`.
3. Observables del mismo intento: ¿aparece `AddonBuilder.exe`?, ¿cambia el hash
   del PBO?, wall-clock.
4. Interpretación:
   - dict con `error_code=build_failed` → **el descarte (a) cae** y hay que reabrirlo;
   - `DayzTestToolError("terminal_invalid")` → el PE murió sin terminal limpio;
   - `NativeLauncherBackendError("<failure>")` → **la cadena `failure` ES la causa**;
   - `LeaseCleanupError` / `OSError` / `ValueError` → candidatos 5-8.

## El «~16 s» sigue sin explicación

No hay una constante de ~16 s en el camino de build. Cercanas y descartadas como
explicación única: `DEFAULT_TOOL_TIMEOUT_S = 15.0` (`server.py:42`) **no** se usa
en `dayz_test_run`; `_LAUNCHER_START_SECONDS = 20.0`
(`native_launcher_backend.py:57`); `_DEBUG_DRAIN_SECONDS = 5.0` (`:54`), que en
cascada puede sumar ~10-20 s. **Es una pista sin constante única: medir el
wall-clock por fases en la reproducción, no darlo por explicado.**

## Sesgo reconocido

El R22 lo marca y es cierto: este documento lo escribió quien también escribió el
instrumento (F1.4), y empujaba hacia «una repro nombra una de cuatro clases» —
que es exactamente lo que ese instrumento puede dar. Los candidatos de
lease/cleanup, el `OSError`, la excepción en `finally` y el análisis del debug
gate quedaban fuera por eso.

**Hard stop del plan**: si dos intentos no dan causa, instrumentar en vez de
seguir probando.
