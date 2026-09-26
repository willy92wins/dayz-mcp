## A - Ficheros creados/modificados

Correccion R2 completada sobre la ronda 1 existente. Gate final: 42 tests, OK (skipped=1), exit 0. Los tamanos iniciales son los observados al arrancar esta ronda, no los de HEAD.

| Ruta absoluta | Bytes antes -> despues | Cambio |
|---|---:|---|
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\host_config.py` | 46293 -> 46471 | Restituye la identidad inicial en relectura/reopen; conserva el parser estricto y la equivalencia semantica del contenido. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_mcp_host_timeouts.py` | 42504 -> 44085 | Renombra el control positivo del salto de linea; anade un hermano negativo para ambos hosts y ambas lecturas finales. El test de rebind queda intacto. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_provenance_gate.py` | 12339 -> 12711 | El double de deriva modifica bytes manteniendo identidad; conserva el reemplazo real entre peticiones y comprueba una unica llamada de transporte en T1. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\host_config.py.r2-before` | 0 -> 46293 | Preimagen exacta de host_config al inicio de R2. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\test_mcp_host_timeouts.py.r2-before` | 0 -> 42504 | Preimagen exacta de los tests del host al inicio de R2. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\test_provenance_gate.py.r2-before` | 0 -> 12339 | Preimagen exacta de T1/T2/T3 al inicio de R2. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\r2-baseline.json` | 0 -> 1161 | Bytes y SHA256 iniciales de fuentes, ficheros preservados e indice. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\r2-baseline-gate.log` | 0 -> 9926 | Ejecucion previa completa: reproduce los dos fallos del brief. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\r2-gate.log` | 0 -> 8600 | Comando, entorno, salida completa y exit code del gate final. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\r2-changes.patch` | 0 -> 7060 | Diff exclusivo R1 -> R2 contra las preimagenes, sin mezclar otras lanes. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\r2-verification.log` | 0 -> 1976 | Diff-check, hashes, preservacion del indice y del test de rebind. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\STATE-r2.md` | 0 -> 12505 | Este informe A-E, escrito por tramos y verificado tras cada escritura. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\provenance-2026-09-08\DONE-r2` | 0 -> 0 | Marcador vacio de entrega completada. |

**[1] Bundle pendiente, declarado sin arreglar:** `host_config.py` esta en `PACKAGED_MODULES` (`tools/build_native_launcher.py:53-72`, entrada en :62). Necesita resellado posterior para incorporar estos bytes; NO se ejecuto el build ni se modifico el bundle. El rojo `test_closure_manifest_is_canonical_exact_and_matches_live_bytes` fue comunicado por el receptor y es coherente con su comprobacion en `tools/tests/test_native_launcher_bundle.py:161-166`; no lo he vuelto a ejecutar. Su fichero `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_native_launcher_bundle.py` permanece en 36034 -> 36034 bytes y con el mismo SHA256.

Conservados byte a byte desde el arranque: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\control_client.py` (31868 -> 31868), incluido `_policy_revalidation_cause`; `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\daemon_policy.py` (13400 -> 13400); el constructor nativo y `.git/index` (hashes en r2-verification.log). Sin git add, commit ni stash, por prohibicion expresa y revision pendiente de Claude. STATE.md/DONE y demas evidencia de R1 no se sobrescribieron; se usan los nombres R2 especificos del entregable.

## B - Resultado de las pruebas

Ejecutado dos veces, antes y despues de la correccion, mediante subprocess con este comando literal:

```text
cwd = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
PYTHONPATH = C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev
PYTHONDONTWRITEBYTECODE = 1
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -m unittest tests.test_mcp_host_timeouts tests.test_provenance_gate -v
```

Antes, salida literal de r2-baseline-gate.log (dos fallos: rebind y rechazo indebido del salto de linea):

```text
Ran 41 tests in 1.461s
FAILED (failures=2, skipped=1)
EXIT_CODE=1
```

Despues, salida literal de r2-gate.log:

```text
Ran 42 tests in 1.482s
OK (skipped=1)
EXIT_CODE=0
```

Controles relevantes, copiados de esa salida:

```text
test_ancestor_rebind_reopen_rejects_new_path_identity (tests.test_mcp_host_timeouts.DaemonProvenanceConfigTest.test_ancestor_rebind_reopen_rejects_new_path_identity) ... ok
test_both_configs_are_pinned_before_first_parse (tests.test_mcp_host_timeouts.DaemonProvenanceConfigTest.test_both_configs_are_pinned_before_first_parse) ... ok
test_final_pinned_revalidation_accepts_byte_drift_with_same_registration (tests.test_mcp_host_timeouts.DaemonProvenanceConfigTest.test_final_pinned_revalidation_accepts_byte_drift_with_same_registration) ... ok
test_final_pinned_revalidation_rejects_byte_drift_with_changed_registration (tests.test_mcp_host_timeouts.DaemonProvenanceConfigTest.test_final_pinned_revalidation_rejects_byte_drift_with_changed_registration) ... ok
test_t1_same_registration_survives_whole_file_replacement (tests.test_provenance_gate.ProvenanceGateTests.test_t1_same_registration_survives_whole_file_replacement) ... ok
test_t2_changed_registration_is_rejected_without_http (tests.test_provenance_gate.ProvenanceGateTests.test_t2_changed_registration_is_rejected_without_http) ... ok
test_t3_rejection_cause_is_separate_safe_and_actionable (tests.test_provenance_gate.ProvenanceGateTests.test_t3_rejection_cause_is_separate_safe_and_actionable) ... ok
```

Comprobacion de diff, ejecutada desde la raiz; salida vacia y exit code registrado en r2-verification.log:

```text
git diff --check -- tools/dayz_mcp/host_config.py tools/tests/test_mcp_host_timeouts.py
OUTPUT: (sin salida)
EXIT_CODE=0
```

Los logs conservan stdout/stderr completos; aqui solo se normalizan finales de linea para Markdown. Tambien se compararon los bytes escritos con lo solicitado y el tamano fisico despues de cada escritura. El test de rebind se comparo textualmente con su preimagen y esta intacto. No se ejecuto la suite completa ni test_native_launcher_bundle.

## C - Hallazgos y decisiones

1. **Cite-then-verify:** las dos premisas de fallo se reprodujeron. El test de rebind empieza realmente en `tools/tests/test_mcp_host_timeouts.py:534`; :560-592 del brief es una parte interna. El falso negativo del salto de linea estaba en :731-751 de la preimagen. El snapshot R1 y el diff exclusivo quedan guardados.
2. **Identidad por resolucion:** `tools/dayz_mcp/host_config.py:341` guarda identidades y :349 toma la del handle de la lectura inicial. :397-402 exige identidad y registro equivalentes en la relectura; :410-414 exige lo mismo en la reapertura. Se mantienen los handles, su cierre y las comprobaciones canonicas/no-reparse existentes. Se restituye ademas el chequeo del handle original, como hacia el codigo anterior a R1. Un objeto distinto durante la resolucion falla aunque contenga bytes iguales.
3. **Contenido semantico:** el positivo de `tools/tests/test_mcp_host_timeouts.py:731` acepta un salto de linea y exige llegar a las tres lecturas conservando la procedencia completa. Su hermano en :754 cambia idle_timeout de 12.5 a 13.0 con sintaxis y esquema validos: cuatro subcasos (Claude/Codex por relectura/reopen), todos rechazados con daemon_provenance_conflict. No depende de un fichero malformado ni de una identidad distinta.
4. **Ajuste imprescindible del fixture de R1:** `tools/tests/test_provenance_gate.py:97` ahora a?sla deriva de contenido, con una comprobacion explicita de identidad constante en :122. T1 (:134) mantiene la sustitucion real mediante rename entre peticiones, que cambia la identidad, y la acepta. T2 (:151) mantiene sus 48 subcasos (2 hosts x 8 mutaciones x 3 ventanas) mas el cambio consensuado de keyfile; la carrera interna ya no puede quedar rechazada solo por identidad. Se conserva su contrato de rechazo antes de transporte, http_bytes_sent=0 y send.assert_not_called. T3 (:200) y el sanitizador de causa permanecen sin cambios.
5. **Correccion al informe anterior:** STATE.md de R1, bloque C punto 3, decia que la proteccion de ruta estaba intacta. Las aperturas canonicas/no-reparse se conservaron, pero se perdio la deteccion de rebind por identidad: no son propiedades equivalentes. Esta R2 corrige esa afirmacion sin sobrescribir el artefacto historico.

## D - QUE PUEDE ESTAR MAL EN LA PREMISA DE ESTE ENCARGO

**Hay una contradiccion concreta en exigir todos los controles de R1 intactos y rechazo de rebind:** T1 original aceptaba cuatro sustituciones con identidad distinta dentro de una resolucion mediante doubles. Ese comportamiento no puede coexistir con [3]. Elegi conservar el caso real ENTRE peticiones y cambiar solo el mecanismo de las carreras internas de T1/T2 a escritura del mismo objeto. No se borro, salto ni relajo el test [3]. La evidencia es la preimagen test_provenance_gate.py.r2-before:96-142 y el diff r2-changes.patch.

**La hipotesis principal de esta R2 se sostiene para el contrato acotado**, porque las identidades solo viven durante resolve_daemon_provenance, no durante toda la sesion del cliente. `tools/dayz_mcp/daemon_policy.py:323-330` vuelve a resolver y compara politicas semanticas entre peticiones. Por tanto, un guardado temporal+rename ya completado antes de la siguiente resolucion sigue permitido: T1 lo prueba con ficheros reales. Durante una resolucion, Windows abre con FILE_SHARE_READ y el test `tools/tests/test_mcp_host_timeouts.py:600-636` confirma que una sustitucion concurrente se deniega. No he inferido como guardan realmente los CLIs del host.

**Esto no demuestra la causa del incidente original 49b2.** El salto de linea concurrente se inyecta en lecturas y el rebind se simula; las aperturas Windows bloquean la escritura concurrente normal. La ronda 1 ya documentaba que un rename entre peticiones pasaba incluso con el codigo previo. Un conflicto de comparticion al abrir sigue siendo una hipotesis distinta, no corregida ni demostrada aqui. No se a?aden reintentos ni excepciones a la acreditacion para ocultarlo.

## E - LO QUE NO PUDE VERIFICAR

- **Reparse real via symlink:** el test existente `test_config_reparse_points_are_rejected` se omite por WinError 1314, falta de privilegio para crear el symlink en este entorno. No se introdujo un skip nuevo. El test de rebind exigido y los controles Windows de pinning/reparse de ancestros si se ejecutaron y salieron ok.
- **Suite completa y revision de otra familia:** no ejecutadas en esta lane por prohibicion expresa de este brief; las hara Claude en serie. Este informe no declara verde global ni aprobacion del revisor.
- **Bundle sellado y su prueba:** no verificados por prohibicion expresa del brief; falta el resellado por el receptor y su comprobacion en la ventana adecuada. La inclusion del modulo en PACKAGED_MODULES si esta verificada en fuente.
- **Daemon/juego, configuraciones reales y causa de 49b2 en produccion:** no inspeccionados ni mutados por prohibicion expresa del brief; no se usaron tools MCP, puerto 8765 ni gestion de procesos. Los tests usan temporales y transporte simulado.
- **Rebind real de un directorio durante una apertura nativa y backend POSIX:** esta corrida Windows solo acredita el double de rebind y los otros controles nativos ejercitados, no reproduce esa carrera de directorios ni ejecuta POSIX.
- **Memoria vault, HANDOFF global, buzon pipeline_feedback y publicacion MCP del campo policy_cause:** FUERA DE MI ALCANCE por la lista exclusiva de escritura de este brief. Se deja este informe como handoff local para el receptor; no se modifica el serializador ni se afirma resuelto el limite ya descrito en R1.
