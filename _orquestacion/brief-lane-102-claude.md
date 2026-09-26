# BRIEF LANE 102 — Ticket #102 (P1): GamePeer sin caps=/ach= -> capabilities_unknown

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
  con la rama `feature/claude-102-lane-claude`.
- NO toques `main`. NO crees PRs. NO hagas `git push --force` ni toques otras
  ramas. `git push origin feature/claude-102-lane-claude` SI esta permitido
  al final (rama normal, no force).

## Ticket a resolver

GitHub issue **#102** del repo `willy92wins/dayz-mcp`:
"0878 rompio la suite: GamePeer no anuncia caps=/ach= -> capabilities_unknown
permanente (familia A, ~28 fallos)".

Lee el cuerpo del issue y su comentario "ADENDA ORQUESTADOR" con:

    gh issue view 102 -R willy92wins/dayz-mcp --comments

El issue trae: causa raiz (PR #94 / 9af5cbf anadio el gate fail-closed de
capabilities/arg-contract a `compute_bridge_ready`; el fixture `GamePeer` hace
GET /poll sin `caps=` ni `ach=`), prueba A/B, evidencia con rutas y lineas, y
el fix propuesto.

## Resumen del encargo (el issue manda; esto es solo orientacion)

1. `git switch feature/claude-102-lane-claude`.
2. Reproduce el fallo: `tools/.venv-mcp/Scripts/python.exe -m unittest discover
   -s tools/tests -p "test_client_mode.py" -k test_all_players_read` ->
   debe fallar con `capabilities_unknown` (fallo conocido, es el que arreglas).
3. Arregla la FIXTURE, no produccion: `GamePeer` (tools/tests/
   test_client_mode.py:66-111 y test_session_e2e.py:75) debe anunciar `caps=`
   y `ach=` en su GET /poll, como hace el bridge Enforce real
   (addon/scripts/5_Mission/MCPBridge.c:235) y como ya lo hace el patron de
   tools/tests/test_arg_contract_hash.py. El valor de `ach` correcto es el que
   produce `EXPECTED_SERVER_ARG_CONTRACT_HASH` de server.py; miralo en el
   arbol, no lo inventes.
4. NO toques `tools/dayz_mcp/server.py` ni `tools/dayz_mcp/loopback.py` ni
   ningun fichero de produccion. Si tras tu analisis el fix exige tocar
   produccion, PARA: escribe el hallazgo en el Bloque C con su evidencia, deja
   lo demas en tu rama, y termina el turno (es un caso raro que se escala).
5. Verifica con la suite: primero los modulos de la familia (discover
   -p "test_client_mode.py", "test_session_e2e.py", "test_mcp_tools.py",
   "test_daemon_query_all_players.py", "test_pleno_lease_and_orphans.py",
   "test_telemetry_read_modes.py"), despues la suite COMPLETA con
   `tools/.venv-mcp/Scripts/python.exe -m unittest discover -s tools/tests
   -p "test_*.py"` (tarda ~6 min; correlo completo, es el criterio de
   aceptacion). Reporta el conteo final exacto de failures/errors/skipped.
6. Commit(s) en tu rama con mensaje claro (ej. `fix(tests): GamePeer announces
   caps=/ach= like the real bridge (issue #102)`), y `git push origin
   feature/claude-102-lane-claude`.
7. Escribe el informe en
   `_orquestacion/lane-102-claude-informe.md` (en la RAIZ del repo, dentro de
   tu rama) con los bloques A/B/C/D de abajo, y incluyelo en el push.

## Como correr los tests (invocacion medida; NO uses otra)

- Un modulo:  `tools/.venv-mcp/Scripts/python.exe -m unittest discover -s tools/tests -p "test_client_mode.py"`
- Suite entera (~6 min): `tools/.venv-mcp/Scripts/python.exe -m unittest discover -s tools/tests -p "test_*.py"`
- NO existe pytest en este venv. `python -m unittest tools.tests.X` FALLA con
  ModuleNotFoundError: no lo uses.

## Contexto del entorno (verificado por sondas del orquestador)

- Windows 11, git-bash disponible; `git`, `gh` (autenticado), python del venv.
- OneDrive: tras escribir un fichero con tu herramienta de edicion, el `git
  status` puede tardar 1-2 s en reflejarlo (cache de OneDrive); si ves un
  estado raro, re-comprueba antes de concluir que algo fallo.
- Los tests importan desde el repo; corre SIEMPRE unittest desde la raiz del
  repo con el venv de arriba.
- Baseline ANTES de tu fix: 4174 tests -> 56 failures, 5 errors, 10 skipped.
- Al terminar tu fix, la meta razonable es: familia A en verde y el total de
  fallos de la suite entera por debajo de ~30 (el resto de familias los
  arreglan otras lanes en paralelo: #103 y #104; si ves fallos de esas
  familias, reportalos en el Bloque C aunque no los toques).

## Formato de entrega obligatorio (bloques canonicos)

Termina tu salida final y el informe en disco con EXACTAMENTE estos bloques:

### Bloque A - Archivos creados/modificados
<por cada fichero: ruta + lineas>

### Bloque B - Resultado de los tests / verificacion
<salida literal de la suite: la linea `Ran N tests in T` y el veredicto>
<resultado de la suite COMPLETA antes y despues, con conteos exactos>

### Bloque C - Hallazgos durante implementacion
<lista; si no hay, "Sin hallazgos">

### Bloque D - Handoff para la revision (Sol)
<estado al cierre, que toca revisar, deuda conocida>
