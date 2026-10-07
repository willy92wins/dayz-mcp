## ARTIFACTS

**[DESIGN] Decisión:** la recuperación debe seleccionar una transición según **fase + ubicación + contenido**, validarla antes de mutar y producir únicamente estados admitidos por esta misma tabla. El sello no demuestra que los bytes originales estén preservados.

Referencias abreviadas: `S = tools/dayz_mcp/dayz_test_storage.py`, `L = tools/dayz_mcp/process_lifecycle.py`. Las líneas corresponden al main suministrado.

| Símbolo | Artefacto actual |
|---|---|
| W | `<mission>/storage_1`; nombre en `S:46`. |
| D | Backup reservado del mundo: `storage_1.modset-<fecha>-<old_seal[:8] o legacy>`; construcción en `S:558-563`. Debe contener el árbol original completo. |
| M | Marcador canónico hermano: `storage_1.modset.json`; `S:47`, `S:318`. |
| K | Backup reservado del marcador: `<nombre D>.marker.json`; `S:564`. |
| J | Journal activo: `storage_1.modset.rotation.<txid>.json`; `S:49-54`, `S:565`. |
| C | Journal terminado: `storage_1.modset.rotation.<txid>.completed.json`; rename en `S:435-439`. Conservarlo. |
| T | Temporal hermano: `storage_1.modset.tmp-<pid>-<secuencia>`; `S:81`, `S:283-286`. No es evidencia de recuperación. El scanner lo excluye por prefijo; `S:535-542`. |

El marcador actual contiene exactamente `schema_version`, `algorithm`, `seal`, `project`; su productor está en `S:338-344` y su validación en `S:322-334`.

El journal actual contiene exactamente:

| Campo | Significado |
|---|---|
| `schema_version` | Actualmente comparte la versión 1 del marcador. |
| `txid` | Identidad de transacción, coincidente con el nombre. |
| `phase` | `prepared` —P—, `storage_moved` —S— o `marker_published` —Q—. |
| `new_seal` | Sello A de la transacción original; nunca sustituirlo por el sello actual X. |
| `old_seal` | Sello anterior válido, o `None`. |
| `project` | Proyecto de la transacción original. |
| `storage_backup` | Nombre D reservado. |
| `marker_backup` | Nombre K reservado. |

Fuente: `S:371-425`; fases en `S:56-59`. Actualmente `old_seal=None` engloba ausencia y marcador inválido, porque el productor solo registra un sello válido (`S:579`). Además, `_read_json` convierte errores de lectura en `None` (`S:256-267`).

**[DESIGN] Journal nuevo, esquema 2:** conservar esos ocho campos y añadir:

- `old_marker_state`: `absent`, `present_valid` o `present_invalid`.
- `old_marker_sha256`: hash de **todos los bytes originales**, o `null` exclusivamente cuando estaba ausente.

Separar la versión del journal de la del marcador. Un error al obtener los bytes originales bloquea antes de crear J; no constituye `present_invalid`. Un archivo vacío o JSON inválido sí es un original existente y debe preservarse. Los nuevos productores rechazan marcadores que sean directorios o enlaces antes de comenzar.

Notación: **O** = árbol original; **E** = bytes originales del marcador; **N** = bytes canónicos de `_marker_document(A, proyecto_del_journal)`; **X** = marcador de la llamada actual cuando requiere resellado; **∅** = ausencia. La igualdad E=N es admisible.

## BOUNDARIES

**[DESIGN] Fronteras vinculantes del productor.** B1–B5 añaden la captura explícita del original; los pasos posteriores conservan el orden actual de `rotate_storage` y `_finish_rotation` (`S:572-588`, `S:461-468`).

Una frontera es el estado después de cada llamada; también se prueba el corte anterior. Las operaciones opcionales se omiten, no se simulan como ejecutadas.

| Fronteras | Operaciones, en orden |
|---|---|
| B1–B5 | Inspeccionar tipo W; inspeccionar tipo M; si M existe como archivo: abrir para lectura, leer bytes, cerrar. Calcular clasificación y hash de esa misma captura. |
| B6–B9 | Comprobar ausencia de D, K, J y C, individualmente. Reservas actuales: `S:566-574`. |
| B10–B19 | Publicar J(P): crear T, escribir, flush, fsync, cerrar, comprobar destino ausente, rename T→J, abrir J, leer/verificar, cerrar. Primitiva actual: `S:287-305`. |
| B20–B21 | Comprobar D ausente; rename W→D. `S:309-313`, `S:585`. |
| B22–B30 | Avanzar J a S mediante escritura atómica, detallada abajo. `S:428-432`, `S:586`. |
| B31–B34 | Comprobar K; comprobar M cuando corresponde; comprobar destino K ausente; rename M→K **solo si el original existía y aún no está preservado**. Orden actual: `S:461-465`; condición propuesta más precisa. |
| B35–B43 | Publicar N atómicamente. `S:347-350`, `S:466`. |
| B44–B52 | Avanzar J a Q atómicamente. `S:467`. |
| B53–B54 | Comprobar C ausente; rename J→C. `S:435-439`, `S:468`. |
| B55 | Rama de error de publicación: unlink del T todavía existente; nunca W, D, M, K, J o C. Limpieza actual: `S:298-303`. |

Cada escritura atómica de nueve pasos —B22–30, B35–43 y B44–52— significa: **crear T, escribir, flush, fsync, cerrar, replace, abrir destino, leer/verificar, cerrar**. La publicación cambia respectivamente en **B27, B40 y B49**.

**[DESIGN]** Crear T exclusivamente; ante colisión elegir otro nombre, sin truncar un temporal ajeno. Actualmente se abre con `"wb"` (`S:287`).

Las lecturas previas de misión, scanner y clasificación también llevan puntos de fallo: `S:637-645`, `S:529`, `S:657-658`. No cambian artefactos; sus cortes colapsan en el estado anterior correspondiente.

Fronteras de recuperación:

- **R0.n:** enumerar; abrir/leer/cerrar J; inspeccionar W/D/M/K/C; leer M/K presentes. Validar el estado completo, sin mutaciones.
- **R1:** para P coherente sin movimiento, comprobar C y renombrar J→C(P); clasificar normalmente.
- **R2:** si W ya se movió y J=P, publicar S con los nueve pasos atómicos.
- **R3:** preservar el original pendiente mediante comprobación de destino y rename M→K.
- **R4:** publicar N, nueve pasos; omitir si N ya está publicado y la preservación está satisfecha.
- **R5:** publicar Q, nueve pasos; omitir si J=Q.
- **R6:** comprobar C y renombrar J→C(Q).
- **R7.n:** volver a comprobar ausencia W y leer/verificar N.
- **R8:** si A≠X, publicar el marcador actual, nueve pasos.
- **R9.n:** leer/verificar sello X antes de autorizar lanzamiento.

Una muerte simulada captura inmediatamente el disco y **no ejecuta cleanup ni `finally`**. Los errores retornables se prueban separadamente.

## STATE MATRIX

**[DESIGN]** Esta es la matriz del esquema 2 bajo un único escritor, sin motor activo durante la transacción. `e` significa E si existía original, ∅ si no; `k` significa E si existía original, ∅ si no.

| Estado | W | D | M | K | J / C | Cortes que lo producen | Única continuación |
|---|---|---|---|---|---|---|---|
| S0: no iniciada | O | ∅ | e | ∅ | ∅ / ∅ | Antes de B15, incluidos temporales de J | Clasificar para X; no existe transacción durable que terminar. |
| S1: preparada, intacta | O | ∅ | e | ∅ | P / ∅ | B15–B20 | R1: terminar como **abortada sin movimiento**; clasificar X. |
| S2: mundo movido, fase atrasada | ∅ | O | e | ∅ | P / ∅ | B21–B26 | R2→S3; continuar R3–R9. |
| S3: preservación pendiente | ∅ | O | E | ∅ | S / ∅ | B27–B33, original presente | R3→S4; continuar. |
| S3a: original ausente | ∅ | O | ∅ | ∅ | S / ∅ | B27–B39, original ausente | R4→S5; continuar. |
| S4: original preservado | ∅ | O | ∅ | E | S / ∅ | B34–B39 | R4→S5; continuar. |
| S5: publicación adelantada | ∅ | O | N | k | S / ∅ | B40–B48 | R5→S6; continuar. **No mover N a K.** |
| S6: publicación registrada | ∅ | O | N | k | Q / ∅ | B49–B53 | R6→S7; continuar. |
| S7: original terminada | ∅ | O | N | k | ∅ / Q | B54; R6; R8 antes de replace | Original ya terminada; resellar para X si A≠X y confirmar. |
| S8: llamada resellada | ∅ | O | X | k | ∅ / Q | Replace de R8 y lecturas posteriores | Confirmar X; original ya terminada. |
| SA: aborto registrado | O | ∅ | e | ∅ | ∅ / P | Rename de R1 | Clasificar X. Si rota, inicia otra transacción con identidad distinta. |
| SE: mundo creado después | nuevo | O | X | k | ∅ / Q | Consumidor, después de terminar/resellar | Clasificar: X reutiliza; otro sello rota y conserva este nuevo árbol. |

El aborto de S1 conserva el comportamiento especificado: main ya completa un journal cuando W permanece y D no existe (`S:497-500`), y deriva otro txid para la clasificación posterior (`S:653-656`). No describirlo como una rotación completada.

**Temporales: producto cartesiano de la tabla.** Antes de publicar, el destino conserva su estado anterior y T puede estar ausente, vacío, parcial o completo. Tras flush/fsync contiene el payload completo; tras rename/replace desaparece ese T y el destino contiene bytes completos. Las lecturas posteriores no cambian el estado. Un unlink fallido deja T; uno exitoso lo elimina. Ninguna variación de T cambia la continuación.

**Recuperación de recuperación, profundidad ≥2:**

| Acción interrumpida | Estados siguientes |
|---|---|
| R0, comprobaciones o lecturas | Mismo estado. |
| R1 | S1 antes del rename; SA después. |
| R2 | S2 antes del replace; S3/S3a después. |
| R3 | S3 antes del rename; S4 después. |
| R4 | S3a/S4 antes del replace; S5 después. |
| R5 | S5 antes del replace; S6 después. |
| R6 | S6 antes del rename; S7 después. |
| R7 | S7. |
| R8 | S7 antes del replace; S8 después. |
| R9 | S7 o S8. |

Cada salida vuelve a una fila aceptada. Aplicar esta misma tabla al segundo intento demuestra cierre por composición; no depende de “cuántas recuperaciones van”.

**Compatibilidad obligatoria con esquema 1.** No imponer retrospectivamente la matriz más estrecha del esquema 2:

| Variante legacy | Estado adicional admitido | Continuación |
|---|---|---|
| P con W movido | M=∅/N y K preservado; también M=N, K=∅ cuando `old_seal=None` | Avanzar a S; terminar conservadoramente. |
| P/S, `old_seal=None`, K=∅ | M ausente o un original de procedencia desconocida, incluido N | Si M existe, preservarlo una vez; publicar N. |
| P/S, `old_seal=None`, K presente | M=∅ o N; K puede ser original desconocido o una copia N creada por recuperación | Conservar K; publicar N si falta; avanzar y terminar. |
| Q, `old_seal=None` | M=N; K ausente o preservado de procedencia desconocida | Completar sin sobrescribir K; resellar X. |

Main entra directamente en `_finish_rotation` desde P movido (`S:501-505`), y ese helper puede respaldar N al recuperar una publicación anterior (`S:461-466`). Por eso admite fases atrasadas y K=N sintetizado.

Asimismo, main puede respaldar un **directorio** M: `read_marker` lo considera ausente (`S:319-320`), pero `_finish_rotation` usa `exists` y lo renombra (`S:463-464`). En esquema 1 con `old_seal=None`, conservar ese objeto opaco en K cuando M está ausente o contiene N. No generalizar esta excepción al esquema 2.

## REFUSALS

**[DESIGN]** Predicados explícitos; cualquier rechazo físico ocurre antes de crear T, cambiar fase o renombrar:

1. W o D presentes con tipo distinto de directorio.
2. J y C presentes simultáneamente para el mismo txid.
3. W y D ambos ausentes mientras J está activo.
4. J=S/Q y D ausente.
5. W y D simultáneamente presentes con J activo, según la decisión indicada en `OPEN QUESTIONS`.
6. Esquema 2, J=P: K presente, o M distinto del original registrado —incluida ausencia cuando debía existir—.
7. Esquema 2, original ausente: K presente; en S, M distinto de ∅ o N.
8. Esquema 2, original presente, J=S:
   - K ausente y M no coincide con el hash original;
   - K presente sin tipo/hash original;
   - K preservado y M distinto de ∅ o N.
9. Esquema 2, J=Q: M≠N; o preservación K incumplida. **Se aplica aunque `old_seal == new_seal` o E=N.**
10. Esquema 1 con `old_seal` conocido:
    - original pendiente cuyo marcador válido no contiene ese sello;
    - K presente que no es archivo válido con ese sello;
    - K presente y M distinto de ∅/N;
    - Q sin K o sin M=N.
11. Esquema 1 con `old_seal=None`, K presente y M distinto de ∅/N.
12. Alias entre nombres de artefactos autoritativos, o tipos M/K incompatibles con la rama admitida.

Resultado: `journal_state_impossible`, snapshot intacto.

Journal malformado, versión desconocida o nombres no planos: `journal_unreadable`. Aplicar antes de joins el predicado exacto de D5: string no vacío, distinto de `.`/`..`, sin `/`, `\` ni `:`; equivalente existente en `L:197-203`. El validator actual solo exige strings no vacíos (`S:414-417`).

Errores de observación no prueban imposibilidad. Bloquear con error de lectura/recuperación, sin adivinar ausencia.

**Resolución de las trampas:** M=N en S es S5 si K acredita preservación, o S5 sin K si el esquema 2 registra ausencia original. Si registra original existente y K falta, M debe preservarse primero cuando coincide con E, incluso E=N. La igualdad de sellos nunca sustituye esa evidencia.

## ORACLE

**[DESIGN]** El verificador utiliza un filesystem en memoria y fixtures independientes:

- Guardar antes de ejecutar el mapa completo de rutas relativas y bytes de O; después de cada corte exigir ese mismo árbol íntegro bajo W o D.
- Guardar E antes de ejecutar. Mientras esté pendiente, debe estar íntegro en M; después de preservar, íntegro en K. Una rotación terminada con original existente exige K, incluso si E=N.
- J, M publicados y fases deben contener documentos completos; ningún temporal parcial autoriza recuperación.
- Cada estado generado debe recuperarse sin `journal_state_impossible`, terminar o abortar coherentemente, y admitir otra recuperación después de cualquier corte.
- Al autorizar lanzamiento: ningún J activo; sin W, marcador válido con sello X; con W, clasificación normal sin resellar encima.
- Idempotencia: repetir la misma llamada terminada no cambia artefactos autoritativos ni genera otra rotación.
- Negativos independientes: violar cada predicado de rechazo y exigir snapshot completo idéntico, cero spawn.
- Consumidor D3: A→recuperación X→crear mundo X→A; conservar íntegramente el mundo X y rotarlo.

Instrumentar **cada** stat/listdir/open/read/write/flush/fsync/close/rename/replace/unlink. Para escrituras, explorar todos los prefijos de una fixture pequeña y retornos cortos; para operaciones atómicas, explorar resultado anterior y posterior. El fallo simula muerte abrupta mediante abandono del estado, no excepción con cleanup normal.

Enumeración: productor × cada corte → recuperación X × cada corte → recuperación Y × cada corte → terminación sin fallos. Deduplicar estados autoritativos; mantener cobertura separada de temporales. Repetir hasta punto fijo después de profundidad 2. Registrar witness de cada fila y arista.

Los tests actuales cuentan e inyectan solo rename/replace (`tools/tests/test_dayz_test_storage.py:572-583`, `:609-618`); no constituyen ese enumerador.

**Ejecutado aquí:** modelo abstracto del esquema 2, cinco originales —ausente, inválido, distinto, mismo sello con otros bytes y E=N—, 1.989 snapshots con profundidad 2 y 25 negativos sin mutación. También reproduje con funciones de main el backup de marcador-directorio y la copia N sintetizada desde P legacy. Esto valida razonamiento abstracto y esas reproducciones, no una implementación futura.

## CHANGES

**[DESIGN] Lista mínima:**

1. **S:** versión independiente de journal; captura única de marcador, estado y hash; validator bifurcado para esquemas 1/2. Anchors actuales: `S:44`, `S:316-334`, `S:371-425`.
2. **S:** predicado local D5 y validación de artefactos antes de mutar. Incorporar la tabla como selector puro de estado/acción.
3. **S:** reemplazar la heurística de existencia de `_finish_rotation` (`S:461-465`) por preservación acreditada. Avanzar P→S antes de preservar durante recuperación; aceptar explícitamente las excepciones legacy.
4. **S:** pasar proyecto actual a recuperación; terminar A primero, luego resellar X, confirmar y conservar el resultado de rotación. Hoy recupera con el sello del journal y retorna inmediatamente (`S:505-515`, `S:648-652`).
5. **S:** devolver resultados bloqueados para errores retornables: `recovery_finish_failed`, `recovery_seal_publish_failed`, `recovery_marker_mismatch`; conservar publicaciones ya realizadas.
6. **L:** auditar el rechazo con razón exacta; actualmente descarta esa razón al retornar `storage_recovery_required` (`L:3052-3057`). Mantener campos de éxito y degradación existente (`L:3076-3086`, `L:3110-3128`).
7. **D4 acompañante:** copiar identidad conocida en observaciones y preservarla al cargar; actualmente ambos diccionarios la omiten (`L:274-280`, `L:1854-1859`). El lector ya compara identidades conocidas (`tools/dayz_mcp/dayz_test_tool.py:1955-1961`).
8. Añadir el enumerador en memoria y regresiones independientes; actualizar `tools/packaged-modules.lock.json`, cuya entrada sellada está en `:11`. Sin cambios Enforce/PBO.

**Compatibilidad:** recuperar journals 1 con sus reglas propias; no convertirlos silenciosamente a esquema 2 ni inventar hashes históricos. Solo avanzar su fase y conservar C. Los journals 2 son incompatibles con el validator antiguo, que exige esquema y conjunto exacto (`S:396-407`): rollback del programa requiere terminar primero las transacciones 2 con el recuperador nuevo; no borrar ni degradar journals.

## OPEN QUESTIONS

1. **¿Debe admitirse J=Q con W y D presentes?** D1–D3 contempla clasificar un mundo existente, pero esa combinación no nace de los cortes del productor: J se termina antes de retornar (`S:467-468`), y lifecycle prepara storage antes de crear procesos (`L:3000-3002`). **Recomendación:** rechazar journals activos en esa combinación; limitar “completar y clasificar mundo existente” a P intacto. Si se conserva como excepción operativa, documentarla fuera del conjunto generado por crashes y probarla separadamente.

2. **¿Puede exigirse autenticación exacta del original a journals 1?** No registran hash ni presencia inequívoca. **Recomendación:** aceptar el grafo observable legacy y preservar conservadoramente; exigir autenticación por hash desde esquema 2. No afirmar que un journal 1 distingue una ausencia original de la desaparición externa de un marcador inválido.

## NOT VERIFIED

No se implementó ni modificó ningún archivo; no hay commit. Este documento es el handoff que guardará el runner.

No se verificaron implementación contra el enumerador completo, cierre exhaustivo legacy con errores de lectura, Windows/NTFS real, cortes eléctricos, auditoría/manifiesto/checkpoint ni aceptación en juego. El modelo presupone rename/replace atómicos dentro del mismo filesystem y exclusión de escritores externos; fsync del archivo y lectura posterior actuales (`S:290`, `S:304-305`) no demuestran durabilidad de directorios ante pérdida eléctrica.