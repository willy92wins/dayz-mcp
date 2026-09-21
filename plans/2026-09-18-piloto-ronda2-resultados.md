# Piloto Flash-Next — ronda 2 (2026-09-18)

Celda: `Qwen/Qwen3.8-Flash-Next` × `prime-agent` + `gx10`, `--thinking off`, `-nc -ns`, **sin
`--skill`** (trato idéntico al de agy en la ronda 1). Worktree limpio en `1737dc5`.
Caja antes de empezar (`infra-ia-local/scripts/gx10-estado.sh`, solo lectura): SSH real OK, un solo
proceso vLLM, ATS, kernel pineado, 0 tareas en estado D; KV al 0 %, `READY` en 0,2 s. **No se cargó
ni se paró ningún modelo**: solo inferencia contra el servido.

Fe de erratas de la ronda 1: al principio de `2026-09-17-piloto-resultados.md`.

## En cinco líneas

1. **El mismo modelo y el mismo encargo pasan de perder todo el trabajo a entregarlo completo y
   cerrado solo, cambiando UNA instrucción del brief** (escribir por tramos). Segunda confirmación,
   más fuerte que la primera, de que el arnés pesa más que el modelo.
2. Flash-Next **sí** hace trabajo agéntico bien; la conclusión contraria de la ronda 1 era mía y
   estaba mal.
3. Su *recall* es bajo (2-5 de 18 oportunidades según corrida), en parte porque aplica la lista
   negra con más rigor que el frontier — y detectó que esa lista negra se contradecía.
4. Una corrida no basta para dictamen fino: 1 de cada 4 se equivoca en el detalle.
5. **Ningún modelo sabía que `readiness`, `request` y `worker` están sellados.** Las propuestas
   sobre ellos habrían roto `dayz_test_run` para todas las sesiones.

## Lo que se corrió

| id | qué aísla | resultado |
|---|---|---|
| P1 ×3 | varianza del triaje que en la ronda 1 salió bien (n=1) | 3 entregas; ver §2 |
| P3 | recortes con el código **servido** por `@fichero`, sin herramientas | usó ipython igual; cortado a 300 s en el recuento final; rescatado del stream |
| P2 | recortes con herramientas + `--autonomous-gate`, tabla «completa al final» | **no entregó**: tenía el informe en una variable y siguió verificando hasta el tope de 600 s |
| **P2b** | igual que P2 **+ escritura por tramos** | **✅ entregó: `REPORT.md` en disco a los ~50 s, cerrado solo a los 273 s, `agent_end` limpio** |

Más un subagente (Opus) que construyó la **clave de respuestas** completa de los 4 ficheros:
`piloto-out/clave-recortes.md` — **18 confirmadas (136 líneas)**, 25 discutibles (~115), 24
descartadas.

## 1. Un fallo del arnés que contamina las medidas

En 2 de las 3 corridas P1, `prime-agent` **sale con `rc=0` a mitad de la respuesta**: el stream acaba
en un `text_delta` suelto (el último fue `" 173"`), sin `message_end`, `turn_end` ni `agent_end`. Es
la firma de un `process.exit()` que no espera a vaciar stdout. Consecuencias:

- **El `rc=0` miente y el stream no es fiable para leer la respuesta.** Hubo que reconstruirla desde
  el último `message_update` acumulado.
- **Mitigación: el entregable va a un fichero, nunca a stdout.** Es lo que hace P2b, y por eso su
  resultado no depende del stream.

**No identificado:** P3 se cortó a los 300 s exactos con `rc=0`, con mi `timeout` en 360. La doc de
la ruta no documenta un tope de 300 s para `-p` (el único default de 300000 es el de cada ejecución
del gate autónomo). Queda abierto.

## 2. Varianza del triaje (n=4 contando la ronda 1)

| corrida | 7b66 | 6ed1 | trampa «9 ficheros» | error |
|---|---|---|---|---|
| ronda 1 | ✅ | ✅ | cazada, «7 reales» | — |
| P1-1 | ✅ | ✅ | cazada | afirma que los 8 son `i/lf`: **falso** (el LICENSE es `i/crlf`) |
| P1-2 | ✅ | ❌ | cazada, «7 reales» | veredicto híbrido fuera de formato, «NO_REPRODUCE (sobre el número)», que **invierte** la conclusión |
| P1-3 | ✅ | ✅ | cazada, «solo 7 son `i/lf`» | — |

Veredicto principal: 7b66 4/4, 6ed1 3/4. Trampa: 4/4. **Para dictamen, dos corridas y comparar**;
el n=1 de la ronda 1 sobreestimaba la fiabilidad.

## 3. Por qué no entregaba, y el arreglo

Las 33 llamadas de la ronda 1 y las 66 de P2 **no son un bucle**: son auditorías ordenadas (lectura
por tramos, agrupación de funciones por hash del cuerpo normalizado con `ast`, grafo de llamadas,
recuento de llamadores antes de declarar nada muerto). El fallo era otro: **el brief decía «escribe
la tabla completa; cuando exista, has terminado»**, lo que premia escribir solo al final. En P2
construyó el informe en una variable (`REPORT = r"""## PROPUESTAS…`, llamada 61) y siguió
verificando «las propuestas nuevas» hasta el tope.

P2b añadió cuatro líneas: *crea el fichero con la primera propuesta verificada, añade cada nueva en el
momento, cierra al llegar a 5 o a 20 llamadas*. Resultado: fichero a los ~50 s y cierre propio a los
273 s. **No respetó literalmente el «20 llamadas»** (hizo 26), pero cerró sin que hubiera que
cortarlo.

## 4. Recall contra la clave (18 confirmadas, 136 líneas)

| | confirmadas que encuentra | precisión | en disco |
|---|---|---|---|
| agy (ronda 1) | 3 — C1, C2, C3 (30 l) | 3/3; avisó de la diferencia Unicode de C3 | ✅ |
| Flash-Next P3 (servido) | **5 verificables** — C1, C4, C6, C7, C10; **hasta 9** si sus «6 wrappers» son C11-C14 | cita C10 en 55-58 (es 185-190) | ❌ rescatado |
| Flash-Next P2 | 2 — C1, C3 (+ D8 discutible) | 4/4 en lo concreto | ❌ rescatado |
| **Flash-Next P2b** | 2 — C1, C10 (+ D18 discutible) | 3/3, con C10 ya en la línea buena | ✅ |

Nadie llegó de forma verificable a la oportunidad más grande (C18: los 4 sobres de rechazo
`WorkerTerminal`, 9-39 líneas), aunque Flash-Next la estuvo investigando en la ronda 1.

**El recall bajo tiene una causa que no es capacidad.** La PREMISA de P2b lo dice: *«de las 40
líneas de duplicación literal encontradas, unas 35 están dentro de guards de validez… la lista negra
prohíbe tocar guards»*. Es cierto, y es una **contradicción de mi brief**: `_valid_uuid4` es una
validación de entrada y aun así agy, Flash-Next en otras corridas y el subagente Opus propusieron
deduplicarla sin señalar el choque. Flash-Next aplicó la regla más estrictamente — descartó a
propósito `tool.py:744` porque «mover una guarda para ahorrar 1 línea toca un guard» — y avisó del
porqué.

## 5. Lo que Flash-Next vio y el frontier no

El docstring de `_accepted_modes` (`dayz_test_tool.py:120-127`) dice que `dayz_test_request.py:71` y
`:297` son «el mismo accessor». Son una `{` suelta y una llamada a `_invalid_policy()`. **Cita rota**,
verificada abriendo las dos líneas. El subagente (24 min, 56 herramientas, 300.265 casos de fuzz)
catalogó esa misma función como wrapper de un solo uso (C12) y no vio la cita. No es un recorte;
es un defecto real.

## 6. La frontera que nadie conocía

`native_bundle.py:59-62` declara `_HASHED_MODULES` = {`dayz_test_readiness.py`,
`dayz_test_request.py`, `dayz_test_worker.py`}, y `native_bundle.py:816-821` hace
`sha256(fichero) != declarado → _invalid()`. **Verificado.** Tocar cualquiera de los tres obliga a
reconstruir el launcher y regenerar el lock; hasta entonces `dayz_test_run` y `dayz_test_stop` fallan
para todas las sesiones. **43 de las 136 líneas de la clave caen ahí.** `dayz_test_tool.py` no está
sellado.

Afectadas en estas pruebas: C3 (agy), C1+C4 de P2 (toca `request`), C6 de P3, la fila 3 de P2b
(D18). **Ninguno lo sabía porque no se lo dije**: es un límite del entorno y darlo era cosa mía
(LL-391). Ya está en la lista negra del brief (§2.2).

## Cambios al brief antes de cualquier PR

1. **Módulos sellados en la lista negra** — hecho en `2026-09-17-piloto-flash-next-brief.md` §2.2.
2. **Contradicción «deduplicar validaciones» contra «no tocar guards»: resuelta.** Aprobado por
   Guillermo el 2026-09-18: *mover* una validación a una implementación única que ya se importa
   **no** es tocar un guard si el predicado queda idéntico o más estricto; *debilitarla*, borrarla o
   reordenarla sí. Redactada como regla, con su precedencia, en el brief §2.2-bis.
3. **Escritura por tramos y entregable en fichero, siempre.** Arregla a la vez la pérdida del trabajo
   (P2) y el stream truncado (§1).
4. **Nada de «no uses herramientas» junto a «cifra exacta verificada»**: se contradicen, y el modelo
   eligió la exactitud (P3).
5. **Para dictamen fino, dos corridas y comparar** (§2).

## Veredicto para la pregunta de partida

Con el brief corregido, Flash-Next **sirve como generador de propuestas de recorte**: entrega en
disco, cierra solo, cita bien y razona sobre la premisa mejor que nadie en esta prueba. **No sirve
para abrir PRs sin revisión**: se deja la mayoría de oportunidades, varía entre corridas y, sin la
lista negra completa, propone tocar código sellado. Revisión frontier obligatoria (G7) — que es lo
que planteabas desde el principio.

## No verificado

- El recuento de C11-C14 dentro de los «6 wrappers» de P3: el tally no trae `path:line`.
- El «kwarg `vpp` ×4» de P3: no localizado en la clave; puede ser C18, sin comprobar.
- La causa del corte a 300 s de P3.
- Nada de esto se ha aplicado ni probado con la suite: son propuestas y recuentos sobre `1737dc5`.
