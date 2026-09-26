# Revisión ciega — agujero 1 del coordinador (privilegio de tombstones)

Revisas una entrega que **no has visto hacer**. No hables con quien la hizo, no
asumas que su informe dice la verdad, y no te fíes de que la suite en verde
signifique que el arreglo funciona. Tu trabajo es medir, no confirmar.

## Lo que tienes

- `C:\Users\guill\AppData\Local\Temp\mcp-laneA1-20260819\` — el espacio de trabajo.
  - `spec-coordinador.md` §"Agujero 1" (líneas 26-143) — **el contrato**.
  - `brief_a1.txt` — lo que se le pidió.
  - `laneA1-report.md` — lo que dice haber hecho. **Trátalo como una hipótesis.**
  - `red_first.log` / `green.log` — sus dos medidas.
  - `BASELINE.md` — el estado de la base antes de tocar nada, ya triado.
  - `baseline.csv` — hash de cada fichero ANTES. Cualquier fichero que difiera
    es suyo; los que no aparecen ahí, los creó él.
- El código vive en `<RAIZ>\DayZ Projects\DayZ_MCP_dev\tools\`.
- Suite: desde `...\tools`, `.\.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t .`
  Un módulo suelto: `... -m unittest tests.test_session_acquire_wait -v`

## La invariante que se compró

> Un cliente que ya tiene el claim de la caja o el lease activo nunca recibe
> `operation_tombstones_saturated`. Ese 503 queda para admisión *unseen* de
> quien no ocupa ninguno de los dos.

## Lo que de verdad tienes que averiguar

**El fallo que este proyecto se ha comido dos veces no es un assert ablandado.
Es un test que no llega al camino de producción.** Un test puede estar en verde,
con asserts duros y nombre correcto, y no medir nada — porque llega al escenario
por un atajo que producción nunca recorre, y el bug vive justo en el trozo que
el atajo se salta. Así que la pregunta que gobierna esta revisión no es «¿pasa
el test?» sino **«¿por dónde llega el test hasta el assert, y es ese el camino
que recorre un cliente real?»**.

Concretamente, para cada uno de los cuatro tests nuevos:

1. ¿Los 128 tombstones se acuñan llamando a `cancel_operation` como haría un
   cliente que abandona su espera, o se inyectan a mano en `_operation_tombstones`?
2. ¿El claim de caja se toma con `box_wait_touch(..., claim=True)`, o se escribe
   `_box_claiming` directamente?
3. ¿El test entra por `enqueue` / `acquire` públicos, o llama a un helper
   privado que se salta las guardas de arriba?

Si alguno llega por atajo, **dilo aunque esté verde**: ese test no acredita nada.

## Los tres modos de fallo del spec §6

Compruébalos tú, con una medida, no leyendo el informe:

- **(a) Privilegio demasiado ancho.** ¿El pase se da solo a `_box_claiming` y al
  `_active.client`, o también a cualquiera que esté en `_box_queue`? Si es lo
  segundo, un waiter de caja salta el tope y pisa al dueño. Escribe la sonda:
  extraño con ticket de caja pero sin claim, a 128 tombstones — ¿pasa?
- **(b) Privilegio a medias.** ¿Está en `enqueue` **y** en `acquire`?
  `session_acquire_wait` va por `enqueue`; si falta ahí, el camino real sigue
  roto y solo "funciona" el acquire viejo. No te fíes del informe: grep los dos.
- **(c) Receta que no llega.** ¿El `hint` viaja hasta lo que ve el modelo
  (`str(error)` / `ToolError`), o se queda en el JSON del HTTP? Si se queda, el
  agente sigue recibiendo el código desnudo, reintenta en bucle, y acuña
  tombstones para siempre — que es el bucle que este arreglo existe para cortar.

## Mutación: la prueba que no se puede fingir

Un test que no falla cuando rompes el código que dice proteger **no es un test**.
Para cada una de las cuatro piezas nuevas, rompe la producción a propósito y
comprueba que el test correspondiente se pone rojo. Al menos estas cuatro:

1. Quita el `_box_claiming` del helper de privilegio → debe caer el test del
   claimer.
2. Quita el privilegio de `enqueue` pero déjalo en `acquire` → debe caer el test
   de enqueue y NO el de acquire (si caen los dos, uno de ellos no mide lo que
   dice).
3. Quita el `hint` del payload → debe caer el test de la receta.
4. Invierte el privilegio (dárselo al extraño y no al dueño) → deben caer los
   dos del claimer **y seguir verdes** los tres de caracterización... y si los de
   caracterización también caen, es que el privilegio está mal encajado.

Restaura después. Informa de cada mutación con su resultado: **qué rompiste, qué
test cayó, y cuál no cayó y debería**.

## Lo que NO se puede haber tocado

`MAX_OPERATION_TOMBSTONES` (128), el TTL (120 s), el código de error
`operation_tombstones_saturated`, el 503 del extraño unseen, `_box_tombstones` /
`box_queue_saturated`, y los ficheros del fencing sin promocionar (`loopback.py`,
`process_lifecycle.py`, `daemon.py`, los `.c`). Comprueba con `baseline.csv` qué
ficheros cambiaron de verdad y contrasta con lo que el informe dice haber tocado:
**una discrepancia en cualquiera de los dos sentidos es un hallazgo**.

Los tres tests de caracterización que tenían que seguir verdes:

    test_tombstone_capacity_fences_unseen_operations_and_recovers_after_ttl
    test_tombstone_saturation_does_not_block_cleanup_of_admitted_operation
    test_timeout_tombstones_exact_operation_and_clears_state

Verifica que **siguen con el mismo cuerpo** que en el árbol original, no solo que
pasan. Un test que pasa porque le cambiaron el assert es el fallo clásico.

## Base

`BASELINE.md` deja la base en VERDE (1610 corridos con 4 rojos, los cuatro
cerrados: dos por ficheros que faltaban en la copia y dos por flake de carga
del watchdog de idle). Si ves rojo en `DaemonStartupElectionProcessTest`, vuelve
a correr ese módulo solo antes de acusar a nadie.

## Qué entregas

Un veredicto por escrito con:

1. **CONFIRMADO / NO CONFIRMADO** para la invariante, con la medida que lo
   sostiene.
2. La tabla de mutaciones: qué rompiste, qué cayó, qué no cayó.
3. Los tres modos de fallo del spec §6, cada uno con su medida.
4. Los tests que llegan por atajo, si los hay, aunque estén verdes.
5. Los ficheros realmente cambiados vs los que el informe dice.
6. Lo que no pudiste verificar y por qué.

Si encuentras algo MAYOR o CRÍTICO, dilo sin suavizar y con la línea exacta.
Si no encuentras nada, dilo también — pero solo después de haber corrido las
mutaciones, porque «he revisado y está bien» sin una mutación roja no es un
veredicto.
