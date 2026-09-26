# Resultados de la primera prueba del piloto — 2026-09-17

> ## ⚠ FE DE ERRATAS (2026-09-18) — dos errores míos en este documento
>
> **1. Recuentos inflados.** «900 llamadas» y «7.982 llamadas» contaban cada aparición de
> `"toolName":"ipython"`, un campo que se repite en todos los eventos de *streaming*
> (`toolcall_delta`, `tool_execution_update`). Las llamadas reales, contadas por `toolcall_start`:
> **53 en 46 turnos** (corrida 1) y **33 en 29 turnos** (corrida 6).
>
> **2. Diagnóstico equivocado de la corrida 6.** No era un bucle ni «no sabe parar». Las 33 llamadas
> (extraídas una a una el 2026-09-18) son una auditoría ordenada: localiza los ficheros, los lee por
> tramos, agrupa las funciones por hash del cuerpo normalizado con `ast`, construye el grafo de
> llamadas y cuenta llamadores antes de declarar nada muerto. **La cortó mi `timeout 300`** en la
> fase de verificación final, antes de escribir la tabla. La conclusión «flash-next no está listo para
> nada agéntico» **queda retirada**. La de la corrida 1 (salir al home con un encargo que no requería
> herramientas) sí se sostiene: aquello era falta de fronteras.

Celdas: `Qwen/Qwen3.8-Flash-Next` × `prime-agent`+`gx10` (local, LAN) y `gemini-3.8-flash-low` ×
`agy` (suscripción Google). Workspace: worktree limpio en `1737dc5`, venv enlazado por junction,
suite verde antes de empezar. Sonda GX10 previa: libre (0 en curso, KV 0 %), `READY` en 0,2 s.
Ninguna lane recibió `--skill`: trato idéntico.

## Las seis corridas

| # | lane | encargo | resultado |
|---|---|---|---|
| 1 | flash-next | triaje, brief **sin** fronteras | **NO ENTREGA.** 14 turnos, 900 llamadas a `ipython`, se fue a listar `C:\Users\guill` (`.claude`, `.cursor`, `.grok`…). Abortado a ~6 min |
| 2 | flash-next | triaje, **+4 líneas de frontera** | **ENTREGA. 25 s, 1 turno, 7.001 tokens, `stopReason: stop`** |
| 3 | agy | triaje | **ENTREGA. 10,8 s, 1 turno** |
| 4 | agy | recortes, `--mode plan` | **NO ENTREGA.** `denied_actions: [{action: command}]`, `response: ""` |
| 5 | agy | recortes, **+ nota de entorno** | **ENTREGA. 46,1 s**, 3 propuestas |
| 6 | flash-next | recortes, + fronteras | **NO ENTREGA.** 29 turnos, 7.982 `ipython`, `RC=124` a 275 s, 7,2 MB de stream |

**Tres de los seis fallos son míos, no de los modelos:** el brief de la 1 no tenía fronteras, y el
de la 4 no decía que en ese arné la shell está deshabilitada (LL-391: los límites del entorno son
del que encarga).

## Calidad de lo entregado — verificado, no leído por encima

Clave de respuestas (verificada por mí contra `1737dc5` antes de la prueba): `fb-…-7b66`
**NO_REPRODUCE**; `fb-…-6ed1` **REPRODUCE**, y el ticket dice 9 ficheros cuando hay 8.

| | agy (corrida 3) | flash-next (corrida 2) |
|---|---|---|
| veredicto 7b66 | NO_REPRODUCE ✅ | NO_REPRODUCE ✅ |
| veredicto 6ed1 | REPRODUCE ✅ | REPRODUCE ✅ |
| trampa del 9 vs 8 | cazada ✅ | cazada ✅ |
| finura extra | distingue que 1 de los 8 es `i/crlf`, no `i/lf` | **«los verdaderos desfases son 7, no 9»** — el análisis más fino de los tres, el mío incluido |
| «lo que no pude verificar» | 1 punto, correcto | 5 puntos, y uno **técnico y sutil**: el `grep` mira líneas crudas mientras el test recorre constantes del AST, así que un grep vacío es indicio y no prueba — «el exit code 0 sí es la prueba que manda» |

Las tres propuestas de recorte de agy (corrida 5), **verificadas una a una abriendo el código**:

| # | propuesta | verificación |
|---|---|---|
| 1 | `dayz_test_tool.py:383-391`, `_valid_uuid4` duplicado, 8 líneas | ✅ exacta. Y `dayz_test_tool` ya importa `dayz_test_request` (`:18`), como afirmaba. **Es `AUDITORIA_2026-08-23#SO-02`, que él no vio: converge con el frontier** |
| 2 | `dayz_test_tool.py:643-646`, constantes públicas redundantes | ✅ exacta. Las contrapartes `_BRIDGE_STATUS_UNKNOWN` etc. existen en `:636-641` con los mismos literales, y nadie las importa desde fuera |
| 3 | `dayz_test_worker.py:149-161`, `_local_path` duplica `_valid_local_absolute_path` | ✅ exacta. El original está en `dayz_test_request.py:188` |

**Precisión de `path:line` y de recuentos: 3/3.** Ninguna cifra inventada.

## Lo que mide esto

1. **La tesis se confirma, y con número.** Mismo modelo, misma celda, mismo encargo: **4 líneas de
   frontera son la diferencia entre 0 y una entrega mejor que la de la lane de pago.** De >6 min sin
   producir nada, a 25 s y 1 turno. El delta A−B de §5.3 del brief no es teórico.
2. **Flash-next razona muy bien sobre contexto dado y colapsa en cuanto tiene herramientas.** Las dos
   veces que falló fue por no terminar, nunca por equivocarse. En la corrida 6 **la frontera de
   alcance sí se respetó** (se quedó en los 4 ficheros); lo que no hay es condición de parada.
3. **Agy entrega siempre que el brief declare las limitaciones de su arnés**, y su calidad aguanta
   verificación al 100 %.

## Consecuencia para el piloto

- La **Ola 0 (triaje con evidencia pegada) está validada en las dos lanes**. Es trabajo que se puede
  delegar hoy.
- El **carril A (propuesta de recortes) está validado en agy**, con convergencia demostrada contra
  la auditoría del 07-09.
- **Flash-next no está listo para nada agéntico** (leer, ejecutar, iterar) mientras no se le dé una
  condición de parada que funcione. Lo siguiente que hay que probar en esa lane no es un encargo
  más grande: es `--autonomous` con `--autonomous-gate`, que es justamente el mecanismo de
  terminación que aquí faltaba.
- Y para la pregunta de partida — ¿PRs? — hoy la respuesta medida es: **sí para dictamen y
  propuesta con el contexto servido; no para trabajo con herramientas.**

## Pendiente de limpiar

Worktree en `C:\Users\guill\AppData\Local\Temp\piloto-mcp` (con junction al venv) y salidas en
`piloto-out`. `git worktree remove` cuando ya no haga falta.
