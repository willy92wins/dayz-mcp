**READY.** Los hallazgos de `SOL_v11b.md` quedan cerrados por el texto actual:

- **Preparación:** kit nuevo identificado, jugador local, postura y piernas válidas, ausencia de operaciones pendientes y comprobación visual del holograma y placement válido ([PROCEDURE.md:35](C:/Users/guill/dzmcp_gauntlet/probe_9941/ws/PROCEDURE.md:35)).
- **Cancel en offline:** el ciclo exige servidor dedicado y cliente multijugador; `role="client"` es coherente con esa restricción ([línea 3](C:/Users/guill/dzmcp_gauntlet/probe_9941/ws/PROCEDURE.md:3), [línea 26](C:/Users/guill/dzmcp_gauntlet/probe_9941/ws/PROCEDURE.md:26)).
- **Clasificación:** exige aceptación, atribución, evidencia completa y restauración. Rechazo servidor, timeout y completion cliente sin placement quedan como inconclusos ([línea 79](C:/Users/guill/dzmcp_gauntlet/probe_9941/ws/PROCEDURE.md:79)).
- **Baseline y limpieza:** obtiene IDs mediante resolución aislada, bloquea ambigüedad, conserva evidencia y exige limpieza verificada antes del siguiente trial ([línea 37](C:/Users/guill/dzmcp_gauntlet/probe_9941/ws/PROCEDURE.md:37), [línea 42](C:/Users/guill/dzmcp_gauntlet/probe_9941/ws/PROCEDURE.md:42)).
- **Recovery y lookback:** distingue restauración del flag de cierre de acción, detiene el ciclo ante lifecycle irresuelto y consulta el JSONL completo cuando la ventana pierde evidencia ([línea 46](C:/Users/guill/dzmcp_gauntlet/probe_9941/ws/PROCEDURE.md:46), [línea 29](C:/Users/guill/dzmcp_gauntlet/probe_9941/ws/PROCEDURE.md:29)).

El diff no introduce contradicciones: la nota del ray de cámara complementa la preparación; conservar la explicación del borrado de JSONL es coherente con el borrado explícito del paso 3 antes del marker.

No quedan reemplazos pendientes. Veredicto limitado a coherencia documental y cierre de hallazgos; no confirma resultados in-game ni el PBO desplegado.