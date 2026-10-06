# gpt-6.1-sol review, b1_734c, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — Los fallos de solicitudes reservadas se envían al host.**  
En [tools/dayz_mcp/mcp_supervisor.py:222](C:/Users/guill/dzmcp_gauntlet/b1_734c/r1/ws/tools/dayz_mcp/mcp_supervisor.py:222), `_fail_generation` responde a todos los IDs desprendidos, incluidos `HEARTBEAT_ID` y `REPLAY_ID`. El filtro del pump solo protege las respuestas procedentes del worker.

**Escenario ejecutado:** completar el handshake; sustituir `worker.stdin.write` por `_broken_write`; enviar `reload_call(7)`. El host recibe una respuesta no solicitada con:

```text
id = "__dayz_mcp_supervisor_heartbeat__"
result.isError = true
payload.error = "worker_died"
```

También reproduje EOF después de enviar el initialize de replay: el host recibe un error `-32603` con ID `__dayz_mcp_supervisor_replay__`.

Esto incumple el requisito de mantener internos esos resultados y genera respuestas sin solicitud correspondiente en el cliente MCP. **Corrección sugerida:** conservar su fallo en el estado interno, pero excluir los IDs reservados del despacho de respuestas al host.

**F2 — P2 — Reload anuncia éxito cuando el reemplazo ya ha fallado durante el replay.**  
En [tools/dayz_mcp/mcp_supervisor.py:501](C:/Users/guill/dzmcp_gauntlet/b1_734c/r1/ws/tools/dayz_mcp/mcp_supervisor.py:501), el éxito del replay depende del retorno de `_send_worker`. Este comprueba `failed` antes de escribir, pero devuelve `True` después de `flush()` sin comprobar el fallo concurrente ([línea 199](C:/Users/guill/dzmcp_gauntlet/b1_734c/r1/ws/tools/dayz_mcp/mcp_supervisor.py:199)).

**Escenario ejecutado con `FakeWorker`:**

1. El reemplazo responde al initialize reservado.
2. Al escribir `notifications/initialized`, su stdout entrega EOF.
3. Su `flush()` espera a que el pump marque `generation.failed=True` y termina normalmente.
4. El host recibe para reload ID 7:

```json
{"status":"recycled","generation":2,"lease_heartbeat":"answered"}
```

`result.isError` es `false`, aunque la generación 2 ya está fallida. La siguiente llamada, ID 8, recibe `worker_died`.

El resultado esperado para reload es `initialize_replay_failed`. **Corrección sugerida:** comprobar el estado terminal bajo `_state` al resolver el resultado del replay.

## SPEC COVERAGE

| Requisito | Estado |
|---|---|
| Python únicamente; modificar las superficies indicadas; sin PBO/reseal | **done** |
| Registrar fallo bajo `_state`, desprender pendientes atómicamente y rechazar admisión posterior | **done** |
| `tools/call`: `isError:true`, `worker_died`, generación y finalización desconocida; otros métodos: error JSON-RPC | **done** para solicitudes del host |
| Una respuesta terminal por solicitud; carreras EOF/escritura y generaciones | **done** en los escenarios ensayados; garantías completas no verificadas |
| Resultados reservados internos; EOF no constituye éxito de heartbeat/replay | **wrong** — F1 y F2 |
| Reload explícito recupera una generación fallida; conservar `drain_timeout` para worker vivo | **wrong** — recuperación y timeout funcionan, pero existe el falso éxito F2 |
| No reintentar automáticamente herramientas interrumpidas | **done** |
| Regresiones solicitadas | **done** como casos añadidos; sus aserciones dejan pasar F1/F2 |

La cancelación conserva la solicitud pendiente: comprobé que ID 5 permanecía en `inflight`. También comprobé que fallo primero, respuesta tardía y EOF producen una sola respuesta terminal para ese ID.

## GATE GAP

- El test de heartbeat fallido, `tools/tests/test_mcp_supervisor.py:602`, ya provoca F1, pero solo comprueba la respuesta de reload; no verifica la ausencia de respuestas reservadas.
- El test de EOF del reemplazo, `tools/tests/test_mcp_supervisor.py:615`, cierra stdout antes del handshake. No cubre EOF durante el envío de `notifications/initialized`, como F2.
- El test de carrera, `tools/tests/test_mcp_supervisor.py:556`, inspecciona respuestas al recibir ID 6, sin esperar a que el pump termine de procesar una posible respuesta tardía.

Ejecuté `python -B -m unittest tests.test_mcp_supervisor` desde `tools/`: **27 tests, OK, sin skips**, con `C:\Python314\python.exe`. El PASS del orquestador es compatible con ambos defectos reproducidos.

## PREMISE

La premisa de 734c es correcta: EOF puede coexistir con launcher vivo y stdin escribible; la finalización de una herramienta interrumpida sigue siendo desconocida. El cambio corrige el escenario principal de IDs 5/6, pero todavía incumple el contrato del replay y de los IDs internos.

## NOT VERIFIED

- Cliente MCP real y procesos worker conectados por pipes del sistema operativo.
- Ejecución mediante el venv del proyecto; mi ejecución usó Python 3.14 del host.
- Suite global completa: utilicé el resultado aportado por el orquestador.
- Juego, daemon, lease y artefactos desplegados.

Revisión íntegramente en solo lectura; sin cambios, commit ni archivos de cierre.

