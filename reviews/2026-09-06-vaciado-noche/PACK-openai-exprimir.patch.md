# Propuesta de cambio para el Pack — `delegar/references/providers/openai.md` §«Exprimir el tramo final»

> **APLICADO el 2026-09-06 directamente en `~/.claude/skills/delegar/references/providers/openai.md`** (backup
> `openai.md.bak_20260906_pre_ll462`): la skill `delegar` no está en el Pack (0 ficheros trackeados, sin adjudicación) y
> `~/.claude/.gitignore` ignora `/skills/`; no hay reseal ni promoción. Verificación: el título viejo («la sesion abierta
> se termina entera») y «aqui se levantan las dos reglas» ya no aparecen; el título nuevo lleva `rev. 2026-09-06`. Lección: LL-462.

Aprobado por Guillermo el 2026-09-06 (picker: «Escribir la LL y proponer el cambio del Pack»). Motivo medido: la sesión
`codex exec` del plan M23, lanzada a las 17:13 con la ventana al 98 %, murió a las 17:43 con
`ERROR: You've hit your usage limit … try again at Sep 7th, 2026 4:27 AM` (RC=1, 197 543 tokens, sin marcador de cierre);
la sesión corta lanzada a las 17:21 terminó (RC=0). Lección en el corpus (número tomado al escribir la noche del 06-sep) y
memoria host `codex-window-cut-kills-running-session`. El hook `~/.claude/hooks/codex-quota-alert.ps1` ya dice lo medido
(commit `0a7b6c0` en `~/.claude`). Se aplica en el Pack (`C:\Users\guill\DayZ-Modding-Knowledge-Pack`) y se promociona con
`packctl` (LL-390); no editar el árbol vivo `~/.claude/skills`.

## 1. Título de la sección

REEMPLAZAR
```
### (added 2026-08-23) Exprimir el tramo final de la ventana: la sesion abierta se termina entera
```
POR
```
### (added 2026-08-23, rev. 2026-09-06) El tramo final de la ventana: la sesion abierta TAMBIEN muere al 100 %
```

## 2. La premisa (desde «**El truco** [OBSERVADO POR EL USUARIO…» hasta «…no un 1-2% de sesion.»)

REEMPLAZAR los tres párrafos (el «truco», «De ahi la jugada…» y la cita en bloque) POR:

```
**La premisa medida (2026-09-06).** Una sesion ya arrancada NO se termina entera: cada turno es una
peticion nueva y el 100 % la rechaza igual que a una sesion nueva. Medido con la ventana al 98-99 %:
la sesion corta (22 min) termino con RC=0; la larga (plan de 586 lineas) murio a los 30 min con
`ERROR: You've hit your usage limit ... try again at <reset>`, RC=1, 197k tokens dentro y sin su
marcador de cierre. Sobrevivio solo lo que ya estaba en disco, porque el brief exigia escribir por
secciones. La version anterior de este parrafo («se termina entera; el corte se aplica al abrir
sesion nueva») era una observacion del usuario sin re-medir, y estaba equivocada.

De ahi la jugada, que sigue existiendo pero es mas estrecha: el tramo final no compra una sesion
entera, compra lo que esa sesion escriba en disco antes del corte.

> Lanza SOLO encargos que escriban su entregable por partes (secciones, STATE, marcador de cierre al
> final) y que valgan truncados. Nunca la ronda final de un bucle de revision ni nada cuyo valor sea
> el ultimo turno. Al recibir, comprueba el marcador de cierre antes de dar el entregable por completo.
```

## 3. El knob «Tamano del encargo»

REEMPLAZAR el bullet que empieza por «- **Tamano del encargo.** Aqui es donde el truco choca de frente…» POR:

```
- **Tamano del encargo.** Las dos reglas de la §«Presupuesto de cuota» (nada de fan-out de
  subagentes, techo de ~150 turnos) SIGUEN vigentes en el tramo final: el fan-out solo acerca el
  corte y deja el trabajo a medias en todos los hijos a la vez. Lo que cambia es la forma del
  encargo, no su tamano: por partes y util truncado.
```

## 4. El párrafo del hook

REEMPLAZAR «…y el recordatorio de que aqui se levantan las dos reglas del presupuesto.» POR «…y el recordatorio de que
la sesion viva tambien muere al 100 %: encargos por partes, reglas del presupuesto vigentes.» y la última frase
«…es proponerle al usuario el encargo mas grande que haya en la cola.» POR «…es proponerle al usuario el encargo que
mejor sobreviva truncado, no el mas grande.»

## Verificación al aplicar

`grep -n "se termina entera\|se levantan las dos reglas" openai.md` debe devolver 0 líneas tras el cambio;
`packctl validate` PASS; recibo de promoción con el sha del fichero.
