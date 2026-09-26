# BRIEF LANE 104 — Ticket #104 (P3): triaje ficha a ficha de deriva de contrato

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
  con la rama `feature/claude-104-lane-claude`.
- NO toques `main`. NO crees PRs. NO hagas `git push --force` ni toques otras
  ramas. `git push origin feature/claude-104-lane-claude` SI esta permitido
  al final (rama normal, no force).

## Ticket a resolver

GitHub issue **#104** del repo `willy92wins/dayz-mcp`:
"Deriva de contrato residual: suggested_calls/status_snapshot/lease_expired/
etc tras #94/#97/#98 (familia D, ~12 fallos)".

Lee el cuerpo del issue y su comentario "ADENDA ORQUESTADOR" con:

    gh issue view 104 -R willy92wins/dayz-mcp --comments

## Resumen del encargo (el issue manda; esto es solo orientacion)

~12 fallos residuales que no encajan en las familias A (#102 GamePeer
caps/ach) ni B/C (#103 disclosure 80-char). El ticket exige TRIAJE FICHA A
FICHA antes de tocar nada:

1. `git switch feature/claude-104-lane-claude`.
2. Por cada fallo del issue (test_fn_f1f5, test_ui_dialog,
   test_ui_error_diagnostics, test_vehicle_prepare_fixture,
   test_python_backlog_fixes, test_w3_bug_verdicts,
   test_task9_launcher_migration, test_a429_overlay):
   - Reproduce con el venv (invocaciones abajo).
   - Identifica el PR que rompio el contrato: `git log --oneline -S "<simbolo>"
     -- <fichero>` y lee el diff del PR culpable.
   - Clasifica: `test-viejo` (el test asume un contrato que un PR fusionado
     cambio de forma intencional: actualizar el test, citando el PR en el
     mensaje de commit) o `regresion-real` (produccion se rompio: NO lo
     arregles tu; escribe el hallazgo con evidencia en el Bloque C y pasa a la
     siguiente ficha; se separa a issue propio).
3. REGLA DE DUPLICADOS: si un fallo tuyo se explica por las causas raiz de
   #102 (GamePeer sin caps/ach -> capabilities_unknown) o #103 (truncado
   80-char pre-lease), anotalo como duplicado en tu informe y NO lo toques:
   esas lanes lo arreglan.
4. Fix permitido: SOLO `tools/tests/`. Ningun fichero de produccion (si el
   triaje indica regresion real, se escala; ver paso 2).
5. Verifica con los modulos de tus fichas y despues con la suite COMPLETA
   `tools/.venv-mcp/Scripts/python.exe -m unittest discover -s tools/tests
   -p "test_*.py"` (~6 min). Reporta el conteo final exacto de
   failures/errors/skipped y la tabla ficha-a-ficha (fallo -> causa ->
   clasificacion -> accion).
6. Commit(s) en tu rama (ej. `fix(tests): update stale contract tests after
   #94/#97/#98 (issue #104)`) y `git push origin
   feature/claude-104-lane-claude`.
7. Escribe el informe en `_orquestacion/lane-104-claude-informe.md` con los
   bloques A/B/C/D de abajo y la tabla de triaje, e incluyelo en el push.

## Como correr los tests (invocacion medida; NO uses otra)

- Un modulo:  `tools/.venv-mcp/Scripts/python.exe -m unittest discover -s tools/tests -p "test_fn_f1f5.py"`
- Suite entera (~6 min): `tools/.venv-mcp/Scripts/python.exe -m unittest discover -s tools/tests -p "test_*.py"`
- NO existe pytest en este venv. `python -m unittest tools.tests.X` FALLA con
  ModuleNotFoundError: no lo uses.

## Contexto del entorno (verificado por sondas del orquestador)

- Windows 11, git-bash disponible; `git`, `gh` (autenticado), python del venv.
- OneDrive: tras escribir un fichero, `git status` puede tardar 1-2 s en
  reflejarlo; re-comprueba antes de concluir que algo fallo.
- Baseline ANTES de tu fix: 4174 tests -> 56 failures, 5 errors, 10 skipped.
- Otras lanes trabajan en PARALELO sobre tests: #102 (GamePeer) y #103
  (disclosure). Si tu corrida de suite muestra fallos de esas familias,
  reportalos como ajenos: no los cuentes como tuyos ni los toques.
- OJO con `status_snapshot`: si el triaje muestra que produccion elimino un
  atributo que los tests consumen, eso huele a regresion real: clasificalo
  `regresion-real` con evidencia y NO lo parchees en tests para que pase.

## Formato de entrega obligatorio (bloques canonicos)

Termina tu salida final y el informe en disco con EXACTAMENTE estos bloques:

### Bloque A - Archivos creados/modificados
<por cada fichero: ruta + lineas>

### Bloque B - Resultado de los tests / verificacion
<salida literal: la linea `Ran N tests in T` y el veredicto>
<resultado de la suite COMPLETA antes y despues, con conteos exactos>

### Bloque C - Hallazgos durante implementacion
<lista ficha a ficha con clasificacion test-viejo | regresion-real | duplicado;
si no hay, "Sin hallazgos">

### Bloque D - Handoff para la revision (Sol)
<estado al cierre, que toca revisar, deuda conocida>
