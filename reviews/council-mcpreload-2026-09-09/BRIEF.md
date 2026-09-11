# Council — Que la IA pueda aplicar codigo nuevo sin un humano

Eres uno de tres consejeros de familias distintas. Se te pide **juicio de diseno**, no
implementacion. No escribas codigo de produccion ni toques `tools/`.

## La pregunta

Un agente arregla un bug en el propio servidor MCP que esta usando. Hoy no puede aplicar su
arreglo: el proceso importo los modulos al arrancar y solo un proceso nuevo los recoge. La
cadena real acaba en un humano abriendo una sesion nueva.

**Eso no se acepta**: es una aplicacion de consumo para IA. Se busca una de estas dos:

- **(A)** que la IA pueda reiniciar el servidor de tools ella misma, o
- **(B)** que no haga falta reiniciar nada para aplicar codigo nuevo.

## Lo que YA esta refutado — no lo repropongas sin refutar la refutacion

Dos rondas de implementacion y una revision cruzada. Lee antes de opinar:

    reviews/mcpreload-2026-09-09/INFORME.md    seccion 2: por que no el hot reload
    reviews/mcpreload-2026-09-09/GROK-VERDICT.md
    reviews/mcpreload-2026-09-09/RONDA3.md     seccion 2: por que no el auto-suicidio

1. **Hot reload del registro FastMCP: NO.** Closures de schema (`server.py:1930`, `:1950`),
   `isinstance` del runtime (`:3320`), lease/ControlClient (`:1205`, `control_client.py:161`),
   heartbeat de caja (`:3568`), stdio (`:5411`) y loopback embedded (`:910`) no son hojas.
2. **Que el proceso se suicide para que el anfitrion lo reabra: NO**, por tres razones
   citadas en RONDA3 §2. La mas dura: **volver de la tool no prueba que la respuesta haya
   salido** — el SDK responde en `mcp/server/lowlevel/server.py:800` y el flush lo hace OTRO
   task en `mcp/server/stdio.py`. No hay acuse de ese flush para el handler. Ademas el SDK
   admite peticiones concurrentes (`lowlevel/server.py:678`), asi que una foto de "no hay
   nada en vuelo" no cierra la admision.
3. **Recargar una hoja SI funciona**: ya esta entregado `playbook_reload` para el runner de
   playbooks, con recibo de bytes compilados. El problema es que casi nada mas es hoja.

## Hechos del terreno que debes usar

- **El servidor `--client` YA corre como pareja padre/hijo.** Medido hoy: mismo comando,
  padre 34744 -> hijo 21388, y el mismo patron en las seis sesiones vivas. Averigua que hace
  cada uno (`tools/dayz_mcp/__main__.py`, `server.py` §run, y como se elige la rama) antes de
  proponer nada: si el padre ya sostiene algo estable, media solucion puede estar construida.
- **El daemon es OTRO proceso** (`--daemon --port 8765`), sobrevive a las sesiones, **ya
  vigila frescura de fuentes** (`daemon.py:650`) y **es donde viven los runs de juego**
  (`daemon.py:413`, `:520`, `:598`). Reiniciarlo NO lo puede hacer un agente hoy sin tirar
  partidas ajenas, pero el limite es de politica, no de arquitectura.
- El proceso de tools ya publica su propia frescura y marca los resultados servidos por
  codigo rancio (commit `5d9f356`). Detectar ya no es el problema; **aplicar** lo es.
- Arbol compartido, varias sesiones a la vez, y una caja de pruebas UNICA con FIFO.

## Direcciones que puedes evaluar (no son un menu cerrado)

1. **Supervisor / trabajador**: stdio en un padre estable y delgado; las tools en un hijo
   reciclable. El transporte deja de pertenecer al proceso que se recicla, que es justo lo
   que hoy lo impide. ¿Lo permite el SDK instalado? ¿Que se rompe con las llamadas en vuelo?
2. **Mover la ejecucion de tools tras la frontera IPC del daemon**, que ya existe y ya se
   puede reiniciar. El servidor MCP quedaria como fachada estable.
3. **Reestructurar para que lo volatil SI sea hoja**: adaptador estable + implementaciones
   recargables. ¿Cuanto del arbol actual cabe en esa forma y cuanto no?
4. **Afordancias del anfitrion o del protocolo**: ¿reabre el anfitrion un servidor MCP que
   muere? ¿existe en la especificacion MCP algun mensaje servidor->cliente para esto
   (`notifications/tools/list_changed` u otro)? Si esto lo resuelve, dilo y ahorranos lo demas.
5. Lo que se te ocurra que no este arriba.

## Lo que tienes que entregar

No un ensayo. Una recomendacion utilizable:

1. **Tu recomendacion, en una frase**, y si es (A) o (B).
2. **Por que esa y no las otras**: una linea por alternativa descartada, con su motivo.
3. **Coste y riesgo**: que hay que tocar, que se puede romper, y como se prueba que funciona.
4. **Que NO resuelve** tu propuesta. Obligatorio; una propuesta sin esta seccion no vale.
5. **El primer paso barato**: el experimento o la sonda que decide si tu via es viable,
   ejecutable en menos de una hora y sin tirar la sesion de nadie.
6. **Confianza** (alta/media/baja) y que te falto para subirla.

Cita `path:line` cuando afirmes algo del arbol, y **abre el fichero antes de citarlo**. Si
no puedes comprobar una afirmacion, marcala `[SUPUESTO]`.

Se te permite discrepar de las refutaciones de arriba, pero entonces tienes que atacar la
razon concreta, no ignorarla.
