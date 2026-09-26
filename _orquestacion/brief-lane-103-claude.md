# BRIEF LANE 103 — Ticket #103 (P2): disclosure 80-char vs tests de contrato

## MODO NO INTERACTIVO. EJECUTA DE PRINCIPIO A FIN.

No hay nadie al otro lado para aprobar disenos: **este brief ES la aprobacion**.
No preguntes y no pares a confirmar. Si algo es ambiguo, elige la opcion
conservadora, hazla, y anotala en el Bloque C. Una pregunta al final de tu
turno equivale a una entrega vacia. Termina siempre con los bloques pedidos y
con ficheros en disco. Si una skill de tu catalogo te pide brainstorming o modo
plan antes de actuar, esa instruccion NO aplica aqui: te despacharon para
ejecutar una tarea concreta.

## Fronteras de ejecucion (headless)

- PROHIBIDO abrir subagentes o tareas en background.
- Techo: maximo 40 turnos. Si al llegar al techo no has terminado, escribe el
  informe con lo que haya en disco y marca el estado como INCOMPLETO.
- Trabaja SOLO en `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev`
  con la rama `feature/claude-103-lane-claude`.
- NO toques `main`. NO crees PRs. NO hagas `git push --force` ni toques otras
  ramas. `git push origin feature/claude-103-lane-claude` SI esta permitido
  al final (rama normal, no force).

## Ticket a resolver

GitHub issue **#103** del repo `willy92wins/dayz-mcp`:
"Disclosure 80-char rompe tests de contrato de descripcion (colision
0b85e9c vs a81a34f) - familias B+C, ~16 fallos".

Lee el cuerpo del issue y su comentario "ADENDA ORQUESTADOR" con:

    gh issue view 103 -R willy92wins/dayz-mcp --comments

## Resumen del encargo (el issue manda; esto es solo orientacion)

Contexto: la colision es entre dos PRs que nunca se miraron entre si. `0b85e9c`
(progressive disclosure para clientes 8B) trunca descripciones a 80 chars
(`server.py` main:695 `_INITIAL_DESCRIPTION_LIMIT = 80`; main:726-727 trunca
con "...") y recorta el catalogo pre-lease. Los tests de contrato de
descripcion exigen ver sentencias clave (`session_heartbeat`, `120`,
`logs_since`) en las descripciones de `tools/list` SIN lease. La linea de
produccion es INTENCIONAL: no hay que deshacerla.

1. `git switch feature/claude-103-lane-claude`.
2. Reproduce los fallos con el venv (invocaciones abajo) sobre los modulos
   implicados: test_pleno_lease_and_orphans, test_session_status_blocked_on,
   test_wait_for_marker, test_playbook_reload, test_session_acquire_wait,
   test_telemetry_read_modes.
3. Via PREFERENTE: arregla los TESTS para que lean el catalogo COMPLETO
   (post-lease o via la fuente sin truncar). NO revertir el truncado de 80 ni
   el catalogo compacto.
4. Via alternativa (solo si la preferente no es viable para alguna sentencia):
   `_compact_initial_catalog` preserva las sentencias de contrato clave
   (`session_heartbeat`, `120`, `logs_since`) en las descripciones truncadas.
   Es produccion: si la usas, citadlo en el Bloque C con su razon, y el cambio
   debe ser minimo y acompanado de un test que siga verificando el truncado
   para clientes 8B.
5. INVARIANTE: `tools/.venv-mcp/Scripts/python.exe -m unittest discover
   -s tools/tests -p "test_progressive_disclosure.py"` debe seguir en verde
   ANTES y DESPUES de tu cambio (comportamiento 8B). Si tu fix lo rompe, el
   fix esta mal: repite el paso 3-4.
6. Verifica con los modulos de la familia y despues con la suite COMPLETA
   `tools/.venv-mcp/Scripts/python.exe -m unittest discover -s tools/tests
   -p "test_*.py"` (~6 min). Reporta el conteo final exacto de
   failures/errors/skipped.
7. Commit(s) en tu rama (ej. `fix(tests): contract tests read the full
   catalog after lease (issue #103)`) y `git push origin
   feature/claude-103-lane-claude`.
8. Escribe el informe en `_orquestacion/lane-103-claude-informe.md` con los
   bloques A/B/C/D de abajo, e incluyelo en el push.

## Como correr los tests (invocacion medida; NO uses otra)

- Un modulo:  `tools/.venv-mcp/Scripts/python.exe -m unittest discover -s tools/tests -p "test_pleno_lease_and_orphans.py"`
- Suite entera (~6 min): `tools/.venv-mcp/Scripts/python.exe -m unittest discover -s tools/tests -p "test_*.py"`
- NO existe pytest en este venv. `python -m unittest tools.tests.X` FALLA con
  ModuleNotFoundError: no lo uses.

## Contexto del entorno (verificado por sondas del orquestador)

- Windows 11, git-bash disponible; `git`, `gh` (autenticado), python del venv.
- OneDrive: tras escribir un fichero, `git status` puede tardar 1-2 s en
  reflejarlo; re-comprueba antes de concluir que algo fallo.
- Baseline ANTES de tu fix: 4174 tests -> 56 failures, 5 errors, 10 skipped.
- Otras lanes trabajan en PARALELO sobre los mismos ficheros de test:
  #102 (GamePeer caps/ach) y #104 (deriva residual). Limita tus cambios a los
  test_modulos de TU familia y, si tocas un fixture compartido, hazlo con
  cambios aditivos (no reescribas la clase). Si un fallo tuyo se explica por
  las causas raiz de #102 o #104, reportalo en el Bloque C aunque no lo toques.
- El fallo de la familia A (`capabilities_unknown` en test_client_mode) NO es
  tuyo y puede aparecer en tu corrida: no lo persigas.

## Formato de entrega obligatorio (bloques canonicos)

Termina tu salida final y el informe en disco con EXACTAMENTE estos bloques:

### Bloque A - Archivos creados/modificados
<por cada fichero: ruta + lineas>

### Bloque B - Resultado de los tests / verificacion
<salida literal: la linea `Ran N tests in T` y el veredicto>
<resultado de la suite COMPLETA antes y despues, con conteos exactos>
<test_progressive_disclosure.py: verde antes y despues, literal>

### Bloque C - Hallazgos durante implementacion
<lista; si no hay, "Sin hallazgos">

### Bloque D - Handoff para la revision (Sol)
<estado al cierre, que toca revisar, deuda conocida>
