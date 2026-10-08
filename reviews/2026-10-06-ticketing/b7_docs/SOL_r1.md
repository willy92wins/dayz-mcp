# gpt-6.1-sol review, b7_docs, round 1 (unedited)

VERDICT: APPROVED

## FINDINGS

No hay hallazgos P1/P2. Los siguientes P3 no bloquean la fusión documental.

- **F1 — P3 — `tools/dayz_mcp/server.py:4780`: «every phase actually displaces» es incorrecto.**  
  Escenario: con el jugador local quieto y ningún movimiento activo, llamar `player_move(speed="walk", phase="release")`. La descripción publicada promete desplazamiento para esa fase; Python genera únicamente `{"mode":"release"}` (`tools/dayz_mcp/player_move.py:151`) y el puente responde `not_held` (`addon/scripts/5_Mission/MCPClientBridge.c:2850`). Con movimiento activo, libera los overrides.  
  **Fix sugerido:** limitar la advertencia al ejemplo `walk/hold`: solicita movimiento real y puede cambiar la posición; no garantiza desplazamiento ni asentamiento. El test de `tools/tests/test_tool_description_caveats.py:76` actualmente acepta la generalización errónea.

- **F2 — P3 — `tools/tests/test_tool_description_caveats.py:159`: el test describe incorrectamente el descubrimiento previo al lease.**  
  Escenario ejecutado: pasar los metadatos publicados a `_compact_initial_catalog`. El catálogo completo contiene las cuatro herramientas; el catálogo compacto devuelve **ninguna** de ellas. El comentario afirma que también antes del lease se entrega su descripción completa. `tools/dayz_mcp/tool_catalog.py:157` las omite.  
  **Fix sugerido:** explicar que están ocultas en el catálogo compacto y reciben la descripción completa cuando se publica el catálogo completo. Corregir también la afirmación equivalente de `REPORT.md:98`. Esto no demuestra pérdida de las caveats cuando las herramientas son visibles.

## SPEC COVERAGE

| Ítem | Requisito | Estado |
|---|---|---|
| 4d7e | Editar la descripción de `capture_screenshot` | **done** — `server.py:6073`. |
| 4d7e | `fullres_path` contiene la superficie efectiva, tras selección/crop y antes del downscale; usar sus dimensiones | **done** — coincide con `mcp_capture.py:1553` y `:1601`. |
| 4d7e | Preservar resultados, nombres y errores | **done** — sin cambios funcionales en el diff. |
| 4d7e | Metadatos mediante `resolve_effective_schemas`; comportamiento separado; revisar descubrimiento | **done** — clases separadas y metadatos efectivos. Imprecisión explicativa F2. |
| c879+769c | Distinguir posición asignada de física asentada y attachment al suelo móvil | **done** — `server.py:4773`; observación atribuida y no reproducida. |
| c879+769c | Workaround exacto, movimiento real y restricción al jugador local | **done**, con la generalización **wrong** de F1. |
| c879+769c | Preservar parámetros/resultados; no añadir settle automático | **done**. |
| c879+769c | Regresión sobre descripción publicada | **done** — pasa; no detecta F1. |
| c879+769c | Diferir settle automático hasta verificar mecanismo y targeting | **done** — no se implementa ni acredita ese mecanismo. |
| 1d31 | Cross-link con `kind="key", dik=1, entry="game", phase="click"` | **done** — `server.py:5839`. |
| 1d31 | Delivery no confirma efecto; verificar con `ui_tree` | **done** — `server.py:5841`. |
| 1d31 | Preservar resultados y semántica, sin atribuir key-up a `key_press` | **done**. |
| 1d31 | Regresión de ruta y nombres publicados | **done**. |
| 1d31 | Verificar cierre real del menú de pausa | **missing** — pendiente de ejecución en juego. |
| 1d31 | Mantener diferida la observación de cámara | **done**. |
| 0eb8 | No acreditar fixture vivo duradero sin inicialización AI | **done** — `server.py:388`. |
| 0eb8 | Atribuir health-zero como reportado y causa no verificada | **done** — `server.py:390`. |
| 0eb8 | Comprobar salud antes del juicio visual; mencionar 3108 sin garantizar supervivencia | **done** — `server.py:391`; `health01` existe en `MCPBridge.c:3141`. |
| 0eb8 | Preservar máscaras y contratos de resultados/errores | **done**. |
| 0eb8 | Regresión de documentación publicada y ausencia de causalidad probada | **done**. |
| 0eb8 | Investigación causal mediante runs controlados AI/no-AI | **missing** — investigación posterior; no se presenta como realizada. |

## GATE GAP

Los checks de cadenas pueden aceptar afirmaciones semánticamente falsas, como F1. El resolver prueba el catálogo embedded; el nuevo test de descubrimiento comprueba pertenencia a una constante, sin ejecutar el transporte MCP ni una transición real de lease.

El test sintético de captura acredita un crop en espacio cliente. No cubre todas las variantes de `crop_space="window"`, selección del viewport o backend Windows. Ningún test nuevo prueba asentamiento físico, cierre efectivo del menú o supervivencia del infectado.

## PREMISE

El alcance documental y la ausencia de PBO/reseal son correctos. El diff no cambia comportamiento productivo.

`REPORT.md:160` interpreta el cierre del menú como diferido por la spec, pero esta exige explícitamente verificarlo en runtime. La aprobación permite fusionar las aclaraciones; no acredita el cierre runtime de 1d31.

## NOT VERIFIED

- Ejecución propia con el venv, desde `tools/`: **12 tests, 11 pasan y 1 error** al crear el directorio temporal bajo el sandbox de solo lectura. El Python predeterminado carece de `PIL`.
- El **12/12** y el fast tier **5599, dos fallos baseline, cero nuevos** proceden del gate suministrado por el orquestador; no repetí el fast tier.
- Confirmé que los imports ejecutados pertenecen a este checkout.
- Sin pruebas en juego, build/reseal, verificación del artefacto desplegado ni acreditación independiente de `origin/main`.
- Sin modificaciones, commit, procesos DayZ ni operaciones sobre la sesión compartida. Memoria usada únicamente para disciplina de revisión; evidencia reanclada al árbol actual.

