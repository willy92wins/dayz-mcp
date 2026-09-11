# Revision independiente — marcador de frescura del servidor MCP

Eres el revisor. Otra familia de modelos implemento esto; tu no lo escribiste y no tienes
que defenderlo. Se te pide juicio adversarial, no confirmacion.

## Que se entrego

Un proceso servidor MCP importa sus modulos UNA vez al arrancar. Si alguien edita el codigo
despues, las tools siguen sirviendo el codigo viejo **sin ninguna senal**. Eso ocurrio de
verdad hoy: un arreglo commiteado a las 14:30 y una llamada a las 14:56 que devolvio la
conducta anterior; se detecto solo porque faltaban dos claves que el codigo nuevo emite
siempre.

La entrega anade:

1. `tools/dayz_mcp/server_freshness.py` (nuevo): captura un SHA-256 de las fuentes de los
   modulos cargados durante el import de `server.py`, y compara despues.
2. `bridge_status.server_modules`: `server_started_at`, `server_pid`, `watched_count`,
   `stale`, `unreadable`, `unreadable_reasons`.
3. Un envoltorio sobre el manejador de tools (`app._mcp_server`, atributo PRIVADO de
   `mcp==1.27.2`) que anade a **toda** respuesta de tool un `_meta.server_code_freshness`
   **y un segundo bloque de texto** con el mismo JSON.
4. Un NO argumentado a implementar un verbo de recarga en caliente.

Lee, en este orden:

    reviews/mcpreload-2026-09-09/INFORME.md      el argumento del implementador
    tools/dayz_mcp/server_freshness.py           el modulo nuevo, entero
    tools/dayz_mcp/server.py                     :60, :679, :3263, :3333, :5333
    tools/tests/test_server_freshness.py         los tests que dice que lo prueban

Comprueba las citas `path:line` abriendo el fichero. Varias del informe pueden estar
desplazadas o ser inexactas: dilo cuando lo esten.

## Las preguntas, por orden de lo que me quita el sueno

1. **FALSO FRESCO.** Es la direccion peligrosa. Un falso "stale" es ruido; un falso "fresh"
   reproduce el bug original con una capa de confianza encima. Busca cualquier camino en el
   que el proceso ejecute codigo viejo y el marcador diga `fresh` o lo omita: modulos
   importados DESPUES de la instantanea, dependencias fuera de `dayz_mcp.*`, imports
   perezosos dentro de funciones, `.pyc` viejos, monkeypatching, C extensions, o un modulo
   que no entre en el censo. El informe reconoce algunos; busca los que no reconoce.

2. **COSTE.** Dice que hace DOS lecturas de las fuentes de ~51 modulos por CADA llamada MCP,
   con una medida local de 6,30 ms. Dato que el implementador puede no haber tenido en
   cuenta: **este arbol vive en OneDrive** (`C:\Users\guill\OneDrive\Documentos\DayZ
   Projects\`), con sincronizacion activa y ficheros que pueden estar deshidratados. Evalua
   el peor caso, no el medio. Di si hay que cachear, y que se pierde al cachear.

3. **EL SEGUNDO BLOQUE DE TEXTO.** Anade un bloque `content` extra a **todas** las
   respuestas de todas las tools. Piensa en los consumidores: codigo que hace
   `content[0]`, que asume un unico bloque, que parsea el ultimo, o que concatena. Es un
   cambio de contrato de salida global. Di si el riesgo esta justificado y si hay una forma
   menos invasiva de conseguir lo mismo.

4. **API PRIVADA.** Envuelve `app._mcp_server`, privado de `mcp==1.27.2`. Que pasa al subir
   de version: falla ruidoso o silencioso? Un fallo silencioso aqui deja el marcador muerto
   sin que nadie se entere, que es el peor desenlace posible para esta funcionalidad
   concreta.

5. **FUGA.** Publica `server_pid` y nombres de modulos en cada respuesta. Hay algo ahi que
   no deberia salir?

6. **EL NO DE T2.** Lee su argumento (seccion 2 del informe). Es solido, o descarta
   demasiado rapido un subconjunto seguro? Si crees que existe uno, nombralo con su cita.

## Formato de salida

Escribe **solo texto** en tu respuesta, sin tocar ficheros. Para cada hallazgo:

    SEVERIDAD (CRITICO|MAYOR|MENOR) — titulo en una linea
    donde: path:line
    por que importa: 1-2 frases
    como se dispara: entradas o estado concretos
    confianza: alta|media|baja, y que te falto para subirla

Termina con un veredicto de una linea: **ACEPTAR / ACEPTAR CON CAMBIOS / NO ACEPTAR**, y si
son cambios, cuales son bloqueantes.

Si no encuentras nada en una de las seis preguntas, dilo explicitamente para esa pregunta.
No inventes hallazgos para llenar; un "aqui no veo problema, y esto es lo que mire" vale.
