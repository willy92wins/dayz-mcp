# Revision adversarial - trabajo nocturno del 2026-09-09

Eres el revisor. Otra familia (Codex/astra) implemento esto de noche, sin nadie mirando.
Tu trabajo es **atacarlo**, no aprobarlo. Nada de esto esta commiteado todavia: tu
veredicto decide si se firma.

## Reglas de esta corrida

**No escribas NADA, en ningun sitio.** Ni en el workspace ni fuera. Nada de ficheros
auxiliares, ni scripts, ni notas. En concreto y con ruta absoluta, prohibido escribir en
`C:\Users\guill\AppData\Local\Temp`, en `C:\Users\guill\OneDrive\Documentos\DayZ Projects`
y en cualquier otro sitio del disco. Se ha medido que el modo de solo lectura frena el
workspace pero **no** el resto del disco, asi que te lo digo explicitamente: la esterilidad
es un requisito del encargo, no un efecto del modo. Voy a contar los ficheros de fuera al
recibirte.

**Tu entrega es tu mensaje final por stdout.** No hay fichero de salida.

**No ejecutes tests ni lances nada.** Lee y razona. Si crees que algo solo se decide
ejecutando, dilo y di que comando lo decidiria.

## Que hay que revisar

Cambios sin commitear en el arbol, todos de la corrida nocturna:

    tools/dayz_mcp/steam_preflight.py     +141 -16   fast path del DWORD de Steam
    tools/dayz_mcp/server.py              +43  -1    contratos de tools + lectura de inventario
    addon/scripts/5_Mission/MCPBridge.c   +9         lado Enforce de la lectura de inventario

Tests nuevos, sin commitear:

    tools/tests/test_steamfastpath_repair.py       28 tests
    tools/tests/test_night0909_inventory_inspect.py
    tools/tests/test_night0909_entity_wait.py      (apunta a un CANDIDATO, no al arbol)

Informes que afirman cosas, y que tienes que contrastar contra el codigo:

    reviews/steamfastpath-2026-09-09/{DIAGNOSIS.md,STATE.md}
    reviews/night-2026-09-09/62e3/ANSWER.md
    reviews/night-2026-09-09/6157/ANSWER.md
    reviews/night-2026-09-09/c82e/{ANSWER.md,PLAN.md,server.py.patch}
    reviews/night-2026-09-09/b256/*
    reviews/night-2026-09-09/3fc1/DIAGNOSIS.md
    reviews/night-2026-09-09/49d0/DIAGNOSIS.md

## Los cinco ataques que quiero, por orden de dano si fallan

**1. El fast path de Steam puede escribir un pid equivocado?** Es el unico cambio que
MUTA el sistema del usuario: reescribe `HKCU\Software\Valve\Steam\ActiveProcess\pid`. Si
escribe mal, el usuario se queda sin poder lanzar el juego, que es el dano que veniamos a
reparar. Comprueba: que verifica antes de escribir, si comprueba imagen ademas de
existencia, que hace con varios `steam.exe` vivos, y si de verdad no toca `ActiveUser`.
El informe declara una carrera residual entre la ultima comprobacion y la escritura:
di si esta bien acotada o si la minimiza.

**2. Los contratos de tool que reescribio, dicen lo que el codigo hace?** Los tickets
62e3 y 6157 cambiaron descripciones que lee el cliente MCP. Una descripcion que promete
mas de lo que el codigo cumple es peor que ninguna. Contrasta cada afirmacion contra su
`path:line`. En 6157 fijate especialmente en la afirmacion de que `started=true` no
acredita aceptacion ni finalizacion.

**3. La lectura de inventario (b256) es segura en el servidor?** Toca Enforce
(`MCPBridge.c`). Mira null-safety, si respeta el modelo server-authoritative del proyecto
y si puede petar con un `object_id` que no exista o una entidad sin inventario.

**4. Los tests discriminan de verdad?** Un test que pasa contra el codigo viejo no vale.
Los de Steam declaran 7 fallos y 43 errores contra la version anterior. **43 errores es
sospechoso**: mira si son discriminacion real de comportamiento o solo `AttributeError`
por metodos que aun no existen. Di cuantos son de cada clase.

**5. Los dos diagnosticos (3fc1, 49d0) sostienen su causa?** Los dos afirman un
mecanismo. Comprueba si las lineas citadas lo sostienen o si hay un salto. El de 3fc1 ya
se autoetiqueta `[HIPOTESIS]`: di si esa etiqueta esta bien puesta o si en realidad
tienen mas evidencia de la que se atribuyen. El de 49d0 habla de una conjuncion; di si
es conjuncion o correlacion.

## Contexto que te ahorra ruido

- **El juego no se puede ejecutar** ahora mismo, asi que nada de esto esta verificado en
  el motor y NO se afirma que lo este. No cuentes «sin probar in-game» como hallazgo: ya
  esta declarado. Lo que si es hallazgo es si algun informe **presume** verificacion que
  no tiene.
- `c82e` esta entregado como candidato **sin aplicar** a proposito, porque mi lista de
  ficheros escribibles le prohibia `server.py` para ese ticket. Esa parcialidad es culpa
  mia, no suya: no la cuentes como fallo. Lo que si quiero es tu opinion sobre si el
  patch es correcto para aplicarlo.
- El indice de git lleva 830 lineas de otra sesion. No es asunto tuyo, pero no te
  extranes si lo ves.

## Formato de tu respuesta

Para cada hallazgo: severidad (CRITICO / SERIO / MENOR), `path:line`, que esta mal, y
como reproducirlo o que evidencia lo cerraria. Al final, un veredicto por cada uno de los
cinco ataques: PASA / FALLA / INCONCLUSO, y en el ultimo caso que falta para decidirlo.

Si algo esta bien, dilo en una linea y sigue. No rellenes.
