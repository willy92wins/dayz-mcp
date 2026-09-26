# af59 ronda 2 — STATE

> **Quién escribe esto y por qué importa.** Lo escribe el ORQUESTADOR, no el implementador.
> `gpt-6-astra` (via `prime-agent` + `openai-codex`, `--thinking ultra`) hizo el trabajo y lo
> dejó verde, pero **su kernel de IPython murió antes de poder escribir su propio `STATE.md`**
> (`Kernel has been shut down` en cada intento; modo de fallo ya documentado en
> `quemador.py`). Lo reconstruyo desde la evidencia EN DISCO y desde su narrativa del stream,
> y marco explícitamente qué he verificado yo y qué no.

## RESULTADO

`PASS=9 FAIL=0`, exit 0 — **verificado dos veces**: por el worker (`gate.log`) y **por mí,
independientemente, desde el receptor** (`test-af59.ps1 -Script dayz-test.ps1`, exit 0).

Línea base antes de tocar nada, medida por mí: `PASS=7 FAIL=0`, exit 0.

| corrida | resultado | quién |
|---|---|---|
| base (sujeto sin tocar, 7 escenarios) | `PASS=7 FAIL=0` exit 0 | yo |
| gate final (sujeto parcheado, 9 escenarios) | `PASS=9 FAIL=0` exit 0 | worker y **yo** |
| M1 — quitado el borrado del temporal | `PASS=8 FAIL=1`, cae **solo S8** | worker |
| M2 — quitado el conteo de `.c` | `PASS=8 FAIL=1`, cae **solo S9** | worker |
| `test-H1.ps1` (6 escenarios de destino) | `PASS=6 FAIL=0` | worker |

Las dos mutaciones están **aisladas**: cada escenario nuevo cae solo cuando se borra su
propio control. Es la condición E2 del encargo y es lo que convierte un verde en una medida.

## QUÉ CAMBIÓ

Diff real 108 líneas `+` / 29 `-` (el fichero además cambió de finales de línea, lo que hace
que un `diff` crudo lo muestre entero; normalizando CR el cambio es acotado).

**H1/P1 — el destino ya no se fabrica.** `Assert-ModsDestination` muere si `<WorkDrive>\Mods`
no existe (*"it will not be created here"*) y muere si existe pero no es un reparse point,
salvo `DAYZ_ALLOW_PLAIN_MODS=1`. `Invoke-BuildPreflight` la llama primero, y **`Invoke-Build`
empieza llamando a `Invoke-BuildPreflight`**: por ahí entra el camino `-BuildOnly` / `-Mode none`
que antes saltaba por encima.

Fue **más allá del encargo, y en la dirección correcta**: el `Invoke-Preflight` completo también
dejó de fabricar. Antes creaba el junction él mismo con `mklink /J` y, si el directorio existía
sin ser junction, solo avisaba (`Warn`). Ahora las dos rutas comparten `Assert-ModsDestination`.

**H2/P2 — el conteo de `.c` ya no depende del modo de empaque.** Desapareció
`-and (-not $usePackOnly)`. Corre siempre que exista `scripts\` con al menos un `.c`. Y
sustituyó el hack viejo —leer los primeros 4 MB como ASCII y contar `\.c\x00` por regex— por
`Get-PboScriptEntryCount`, un lector real de la tabla de cabecera PBO (nombres terminados en
NUL + cinco DWORD little-endian, bloque `Vers` opcional, terminador validado, cotas de payload).

## LO QUE NO ESTÁ VERIFICADO, Y QUIÉN LO DEJÓ ASÍ

- **`STATE.md` del propio worker: no existe.** Límite del ENTORNO (kernel muerto), no del
  delegado ni del encuadre. Su narrativa sí llegó por el stream y está recogida arriba.
- **`DAYZ_ALLOW_PLAIN_MODS=1` es una válvula que el arnés se concede a sí mismo.** El worker
  la introdujo para que la fixture siga corriendo sin `P:` real —que es una restricción MÍA,
  del contrato de la ronda 1— y el arnés la fija explícitamente. Consecuencia: los 9 escenarios
  miden el camino CON la válvula abierta. `test-H1.ps1` cubre los rechazos, pero **no he
  ejecutado yo esos 6 escenarios**; solo he leído su log.
- **Tres riesgos que veo yo leyendo el diff, y que no cierra ningún test de esta ronda:**
  1. `Get-PboScriptEntryCount` lanza excepción → `Die`. Un PBO binarizado legítimo que el
     parser no entienda ahora **tumba el build**; antes, en ese caso, solo había un `Warn`.
  2. `$srcC` pasó de contar `.c` en TODO `$src` a contarlos solo bajo `scripts\`. Un mod con
     `.c` fuera de `scripts\` obtiene ahora un umbral MÁS BAJO: el control se afloja.
  3. Los PBO de la fixture son bytes de prueba, no archivos de juego válidos. Lo dice el
     propio worker en `pbo-counter-notes.md`. Así que el parser nuevo está probado contra
     fixtures sintéticas, no contra un PBO real de AddonBuilder.
- **Premisa, en palabras del worker:** *"un junction no demuestra que el juego lea su destino.
  El operador debe preparar el mapping correcto."* Es correcto y no lo cierra esta ronda.

## PENDIENTE

El piloto (`kt_roadkill_armed_dev` / `MilitaryBunker_dev`, byte a byte idénticos, sha
`edfe79d8`, 23926 B) sigue **sin migrar**: la ronda 1 lo condicionó a que H1 cerrase, y H1 ha
cerrado, pero los tres riesgos de arriba se resuelven antes de tocar un árbol de mod real.

SP-380 (el bug de af59 para la skill `dayz-test-ingame`, que es quien GENERA `dayz-test.ps1`)
sigue en `skill-patches-pending.md` y no se ha promovido.

## PROCEDENCIA

- Implementación: `gpt-6-astra` × `prime-agent` + `openai-codex`, `--thinking ultra`,
  `--mode rpc`, 450,5 s, 9.469 eventos, `stopReason: stop`.
- Revisión cruzada: otra familia (Grok × `cursor-agent`, `--mode ask`), en workspace ciego.
- Gate repetido desde el receptor por el orquestador (Anthropic).
