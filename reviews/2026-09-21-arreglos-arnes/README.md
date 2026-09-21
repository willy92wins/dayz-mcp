# Dos arreglos del arnés: stdin de Codex y el stop hook en workers delegados (2026-09-21)

Salen del triaje del buzón ([2026-09-21-triage-buzon](../2026-09-21-triage-buzon/README.md)),
donde diez fichas resultaron no ser del MCP sino de las herramientas con las que trabajamos.
Estas dos eran las únicas con arreglo en nuestra mano, y las dos traían su solución ya medida
por quien las abrió.

## 1. `fb-20260918-015151-5ccc` — `codex exec` colgado para siempre

**Lo medido por la ficha:** sin TTY, `codex exec` se para en «Reading additional input from
stdin...» esperando un EOF que un stdin heredado no le da nunca. 66 minutos vivo, log de 39 B,
CPU a cero. Relanzado con `< /dev/null`: 27,7 KB de log en 20 s. Es **intermitente** — la corrida
anterior del mismo lanzador, 40 minutos antes, no se colgó.

**Por qué reincidió:** la regla ya estaba escrita desde el 2026-06-11… bajo el epígrafe
«Lanzamiento headless desde Cowork». Quien lanzaba desde Git Bash en Windows no se dio por
aludido. Mismo patrón que el resellado del launcher: **el conocimiento existía y lo que faltaba
era que estuviera donde se lee**.

**Qué se cambió:**

- `~/.claude/skills/delegar/references/routes/codex-exec.md` §Correcciones vigentes: la regla
  sube al principio del fichero, redactada para cualquier shell (`< /dev/null` en bash, `< NUL`
  en cmd, `codex exec - <flags> < prompt.txt` si el prompt va por stdin), con la medición y con
  el aviso de que es intermitente y por tanto no se diagnostica por reproducción.
- `~/.claude/skills/delegar/references/patterns/coupled-watcher.md`: transición **5 (STALL)** en
  la tabla y sección propia. Las cuatro señales que había compartían un supuesto sin nombrar —
  **todas esperan que el worker haga algo**—, y un worker bloqueado no hace nada: la ausencia de
  señal es indistinguible de «va lento». La 5 se detecta al revés, porque el log NO crece.

**Lo que NO se tocó, y por qué:** 35 de los 36 lanzadores del árbol no llevan la redirección,
pero son `.bat` de un solo uso dentro de carpetas de revisión ya ejecutadas, casi todos de
LFPowerGrid, y hay una decisión viva de que esta sesión no toca ese proyecto. El lanzador
canónico de DayZ-MCP, `tools/lote_harness/review_codex.sh:21`, ya lo llevaba.

## 2. `fb-20260918-015158-51f6` — un stop hook del host dentro de un worker delegado

**Lo medido por la ficha:** el 2026-09-18, el `thought` final de una corrida de grok-cli decía
que iba a promover una lección, editar la skill, resellar el source map y commitear. El brief le
prohibía git y le acotaba la escritura a tres ficheros. No hubo daño: ninguna skill modificada,
Pack limpio, cero commits, verificado por `find -newermt` y por el reflog.

**El mecanismo, hasta donde llega la evidencia:** el hook `promotion-gate` devuelve
`decision='block'` con un `reason`, y eso **se le entrega al modelo como instrucción**. Ese
`reason` es literalmente «promote it now, edit the skill, reseal source-map.json, commit», que es
lo que el worker parafraseó. Lo que NO está determinado es por qué camino ese texto llegó a
grok-cli, que no es Claude Code y no ejecuta este hook.

**Qué se cambió:**

- `~/.claude/hooks/promotion-gate.ps1`: guard nuevo tras el loop guard. No dispara si
  `CLAUDE_CODE_SESSION_ATTENDED` está presente y no vale `1`, ni si el `cwd` de la sesión cuelga
  de `%TEMP%`. Las dos condiciones son deliberadamente estrechas: **`ATTENDED` ausente NO cuenta
  como no atendida**, porque una sesión normal de escritorio reporta `CHILD_SESSION=1` con
  `ATTENDED=1`, y filtrar por «hija» habría apagado el gate para todas.
- `~/.claude/hooks/tests/test-promotion-gate.ps1`: prueba nueva, cuatro casos, los cuatro en
  verde. El tercero es el que importa — con `ATTENDED` ausente el gate **sigue disparando** —,
  porque el modo de fallo peligroso de este parche no es dejar pasar a un worker sino desarmar
  en silencio un control de gobernanza.
- `~/.claude/skills/delegar/SKILL.md` §Gate, paso 8: **contar al recibir los ficheros modificados
  FUERA del workspace** pasa de ser regla solo de `cursor --mode ask` a ser general.

**Reparto honesto de los dos cambios:** el guard cubre el caso de un worker que Claude Code
marque como no atendido o que corra desde un worktree en `%TEMP%`. Como el camino exacto de este
incidente no está determinado, **el guard no prueba que ese camino concreto quede cerrado**. Lo
que sí atrapa el daño, venga por donde venga, es el recuento de ficheros de fuera del workspace
al recibir: el revisor mira dentro del workspace, así que fuera no mira nadie.

## Respaldos

`promotion-gate.ps1.bak-20260921-51f6` junto al original. Los ficheros de la skill `delegar` no
están gobernados por el promotion-map del Pack (41 skills gobernadas, `delegar` no está entre
ellas), así que se editan en su árbol vivo sin pasar por el repo del Pack.
