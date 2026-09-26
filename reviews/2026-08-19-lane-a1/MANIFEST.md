# Agujero 1 del coordinador — privilegio de tombstones

**Fecha:** 2026-08-19 · **Estado: PROMOCIONADO al árbol** (detalle al final) · **Suite en el árbol: `Ran 1639 / OK`**

## Qué arregla

Con 128 tombstones acuñados por extraños que abandonaron su espera, **el dueño
legítimo de la caja recibía `operation_tombstones_saturated` (503)** y se quedaba
sin poder tomar lease ni arrancar un run durante los 600 s de su propio claim.

La causa es de orden: el chequeo de saturación corre en `session_coordination.py:295`
(y `:719` en `enqueue`), antes del `operation_conflict` de `:301` y mucho antes del
`self._active.client == client` de `:346`. Y `session_acquire_wait` manda siempre un
`operation_id` nuevo (`control_client.py:669-678`), así que el dueño entraba
siempre por la puerta que le cerraba el paso.

**Invariante que queda escrita**: un cliente que ya tiene el claim de la caja o el
lease activo nunca recibe `operation_tombstones_saturated`. Ese 503 queda para
admisión *unseen* de quien no ocupa ninguno de los dos.

## Contrato de origen

`ObsidianVault\AI\10_Projects\DayZ_MCP\reviews\2026-08-19-triaje-nocturno\spec-coordinador.md`,
sección "Agujero 1". Se siguió el DESIGN sin improvisar: no se subió el tope de
128, no se metió LRU, no se desalojan tombstones de extraños y el código de error
no cambió de nombre.

## Contenido

| Fichero | Qué es |
|---|---|
| `delta.diff` | el parche, 4 ficheros (`sha256` primeros 16: `CD9EBD0557A6464A`) |
| `sources\` | los 4 ficheros ya parcheados, por si el diff no aplica limpio |
| `laneA1-report.md` | informe del implementador, con sus atajos declarados |
| `revision-ciega.md` | la revisión independiente, 12 mutaciones |
| `BASELINE.md` | el estado de la base antes de tocar nada, ya triado |
| `red_first.log` | los 4 tests en rojo contra el código sin tocar |
| `green_r2.log` | la suite final |
| `mutate_mayor1/mayor2/medio1.log` | las tres mutaciones de la ronda 2 |
| `brief_a1.txt`, `brief_a1_r2.txt`, `brief_revision.md` | lo que se pidió, literal |

## Tamaño

| | añadidas | borradas |
|---|---|---|
| `session_coordination.py` | 48 | 20 |
| `control_client.py` | 7 | 1 |
| `server.py` | 5 | 0 |
| `test_session_acquire_wait.py` | **241** | **0** |

Las 20 y 1 borradas son los bloques literales que el parche sustituye por la
llamada al helper. **El fichero de tests es puramente aditivo**: 2 imports y 7
métodos nuevos, ni una línea de test existente tocada.

## Qué está medido

- **Suite: `Ran 1629 tests` / `OK (skipped=6)`.** La base efectiva del árbol sin
  tocar eran 1622 verdes (ver `BASELINE.md`); 1622 + 7 tests nuevos = 1629. Ni un
  rojo añadido, ni un test perdido.
- **Rojo primero**: los 4 tests de la ronda 1 fallaron contra el código sin tocar
  antes de que existiera el arreglo (`red_first.log`).
- **Mutación**: se rompió la producción a propósito en 15 sitios (12 en la revisión
  ciega, 3 en la ronda 2). Cada una tumba el test que dice proteger y ningún otro.
- **Ningún test llega por atajo**: los 128 tombstones se acuñan llamando a
  `cancel_operation` como un cliente real, el claim se toma con
  `box_wait_touch(..., claim=True)`, y se entra por `enqueue` / `acquire` /
  `cancel_operation` públicos.
- **Propagación (DZ-R7)**: los seis manejadores de `ControlClientError` en
  producción comparan `.code`, ninguno lee `str(error)`. Añadir `: {hint}` al
  texto no rompe a ningún consumidor.
- **Aislamiento**: 541 ficheros del árbol real fotografiados antes y después.
  **0 cambiados.**

## Lo que la revisión encontró, y que ya está cerrado

Dos huecos MAYORES, los dos de cobertura y no de conducta — el código era correcto,
pero dos de sus cuatro ramas no las medía nadie y podían borrarse dejando la suite
entera en verde:

1. El privilegio en `cancel_operation` (`:801`). Cerrado por
   `test_box_claimer_cancel_of_unseen_operation_is_not_fenced_when_saturated`.
2. La mitad "lease activo" de la invariante (`:2753-2754`). Cerrado por
   `test_lease_holder_is_not_fenced_when_tombstones_saturated`.

Y un MEDIO real, arreglado: el `if error.hint` de `server.py` se evaluaba **antes**
de la allowlist de códigos, así que cualquier no-2xx con `hint` devolvía su código
crudo saltándose el saneado. Hoy no había regresión viva (ningún otro productor de
`hint` viaja en no-2xx), pero era la puerta abierta para el siguiente. Ahora la
rama exige que el código esté en `_REMOTE_ERROR_CODES` o
`_CONTROL_CLIENT_ERROR_CODES`; lo desconocido sigue saliendo como `remote_error`.
Pinado por `test_unknown_remote_code_with_hint_is_still_masked`.

## Detalle de conducta que conviene conocer antes de promocionar

El holder del lease que llega con un `operation_id` distinto del suyo ahora recibe
**409 `operation_conflict`**, no un 2xx. Eso **es** el arreglo: antes ni llegaba a
ese 409 porque el 503 de saturación disparaba primero. Recibe el error correcto en
vez del equivocado, y el correcto sigue siendo un error.

## Cómo promocionar

Esto **no necesita juego**. Es lógica del daemon cubierta por tests unitarios; no
toca el puente, ni `MCP_BRIDGE_VERSION`, ni ningún `.c`, ni el formato de
`coordination.json` / `runs.json`. No hace falta PBO ni canario.

1. Aplicar `delta.diff` sobre `DayZ_MCP_dev\tools\` (o copiar `sources\` encima).
2. Correr la suite en el árbol: desde `tools\`,
   `.\.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t .`
   Esperado: `OK`. El conteo se mueve porque otras sesiones añaden tests — era 1629
   al promocionar y 1639 minutos después. **Corre la suite con el árbol quieto**: si otra
   sesión escribe un `.py` a mitad, los tests que hacen `inspect.getsource` fallan sin
   culpa de nadie (`fb-20260819-153716-8491`).
   **Si sale rojo `DaemonStartupElectionProcessTest`,
   vuelve a correr ese módulo solo antes de acusar a nadie**: flakea bajo carga
   (`fb-20260819-140207-ec0d`), y el mecanismo es que el watchdog de idle del
   demonio de prueba (`timeout=0s`, `poll=5s`) suelta el puerto antes de que el
   cliente conecte.
3. Reiniciar el daemon para que el cambio entre en servicio. **Este paso sigue
   PENDIENTE**: se promocionó el código pero no se reinició, para no pisar el vuelo
   de la sesión vecina. El arreglo no está vivo hasta ese arranque.

**Sin choque con el fencing**: el parche del fencing no toca
`session_coordination.py` ni `control_client.py`, y en `server.py` vive en las
líneas 86-221 — a ~500 líneas del `public_error_code` que toca esto. Los dos
trabajos pueden aterrizar en cualquier orden.

⚠ **Corrección al spec**: su sección B afirma que el fencing no toca `server.py`.
Sí lo toca, en 4 hunks. No cambia la conclusión —no hay solape— pero la premisa
era falsa.

## Lo que queda abierto y no se tocó

Un MENOR que la revisión midió y el spec acepta explícitamente: un cliente
privilegiado puede acuñar tombstones sin techo (500 cancels → 628 tombstones). Es
la misma conducta que ya tenía el cancel de operación admitida. Se deja como está
porque cambiarlo sale del contrato.

## PROMOCIONADO al arbol el 2026-08-19 ~17:21 (daemon NO reiniciado)

Los cuatro ficheros estan en `DayZ_MCP_dev\tools\`, con puerta de hash antes de
escribir (los cuatro intactos respecto a la foto) y lectura byte a byte despues.
Respaldos al lado de cada original: `*.bak_pre_agujero1_20260819`.

El daemon **no** se reinicio a proposito: el disco lleva el codigo nuevo y el
proceso vivo sigue con el viejo hasta su proximo arranque, para no interferir con
el vuelo que la sesion vecina tenia encolado.

**Suite en el arbol: `Ran 1639 tests` / `OK (skipped=4)`.**

⚠ **La primera pasada dio 1 rojo y NO era de este parche.** Queda escrito porque
el modo de fallo se va a repetir: `test_registered_tool_dispatches_and_does_not_hold_lock`
hace `inspect.getsource(tool.fn)`, que toma los numeros de linea del codigo objeto
ya compilado en memoria y lee el TEXTO del fichero en disco. La sesion vecina
reescribio `server.py` a las 17:23 **con la suite en vuelo** (sha `E437FD85` ->
`4DD8B1CB`, añadiendo el tool `ui_reload_layout`), asi que la lectura aterrizo 38
lineas mas arriba, en el cuerpo de `pipeline_inbox`.

Descartado que fuera del parche, con medidas: el test pasa **en solitario** con el
parche puesto (`Ran 8 / OK`); el control pre-parche con el conjunto de tests
igualado fallo **exactamente los 7 tests nuevos y ninguno mas**; y la segunda
pasada entera, sin tocar nada, salio verde con 1639 (diez tests mas, los que la
otra sesion habia añadido entre medias). Archivado: `fb-20260819-153716-8491`.

**Leccion para el proximo que promocione**: la suite se corre con el arbol quieto,
o el rojo que salga acusara a quien no ha sido.
