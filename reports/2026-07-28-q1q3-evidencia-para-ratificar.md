# Q1–Q3 — evidencia para ratificar (no ratificadas)

**Fecha**: 2026-07-28 (sesión nocturna) · **Estado**: servidas con dato, **pendientes de ratificación del usuario**.

Las tres las adjudicó Codex en la Fase 0 (`reports/2026-07-27-fase0-baseline/arena_budget_by_mod.md:60-68`),
no el usuario. Sólo muerden en Fases 3-6. Aquí está el dato que faltaba para decidir cada una, más una
recomendación. **Nada de esto está ratificado.**

---

## Q1 — TTL del run SHARED

**Pregunta** (`spec:282-284`): ¿el `SHARED` muere solo al quedarse sin participantes (idle TTL), o vive hasta
un `stop` explícito?

**Adjudicación de Codex**: muere tras **120 s sin participantes**, configurable. Justificación: reutilizar el
TTL de identidad ya verificado en vez de introducir una constante temporal nueva sin evidencia.

**Verificado**: `SESSION_TTL_S = 120.0` existe en `dayz_mcp/session_coordination.py:15`, y es el TTL que ya
gobierna leases (`:464`, `:1229`, `:2498`) y tickets (`:1071`, `:1986`, `:3387`). La afirmación de Codex es
exacta: no es una constante inventada.

**Recomendación: RATIFICAR.** Reutilizar una constante ya en producción evita un parámetro temporal nuevo sin
evidencia, y 120 s es holgado frente al coste de arrancar la caja (~30 s server + ~30 s cliente).

**Lo que hay que mirar al ratificar**: 120 s es el tiempo que un `EXCLUSIVE` en cola espera de más cuando el
`SHARED` ya se quedó vacío. Si te parece caro, la palanca es Q2 (desalojo inmediato con 0 participantes), no
bajar el TTL — bajarlo afectaría también a leases y tickets.

---

## Q2 — Desalojo de un SHARED por un EXCLUSIVE en cola

**Pregunta** (`spec:285-286`): ¿un `EXCLUSIVE` en cola puede desalojar a un `SHARED` sin participantes, o
espera siempre al TTL?

**Adjudicación de Codex**: puede desalojar **sólo con 0 participantes**; con ≥1 espera. El desalojo nunca
precede al check de participantes.

**Recomendación: RATIFICAR**, con una condición explícita al implementarla.

**La condición**: "0 participantes" tiene que evaluarse y **consumirse atómicamente**. Entre comprobar que hay
0 y desalojar existe una ventana en la que puede entrar un participante nuevo; si no se cierra, el desalojo
mataría un run que acaba de ganar un adherido. Es la misma clase de carrera que el plan de la Fase 2 declara
como riesgo 3 para el par lease/ticket. Sin atomicidad, esta decisión es un data-loss con otro nombre.

Con Q1 y Q2 ratificadas juntas, el peor caso de espera de un `EXCLUSIVE` pasa de 120 s a ~0 s cuando el
`SHARED` está vacío, que es el caso frecuente.

---

## Q3 — Presupuesto de arena: tabla congelada vs medición por drain

**Pregunta** (`spec:287-288`): ¿el guard se mide una vez y se congela, o se mide en cada drain?

**Adjudicación de Codex**: tabla congelada, guard sobre la **cota superior**, fail-closed ante `UNKNOWN_*`, y
regenerar la tabla cuando cambien los PBOs.

### El dato que faltaba

Analizada la tabla completa (408 filas parseadas: 345 `ESTIMATED_OFFLINE` + 63 `SCAN_OK_NO_C_SOURCE`), contra
el cap de **33.554 kB** por módulo:

| Medida | % del cap (cota superior) |
|---|---|
| Mod mediano | **0,18 %** |
| Percentil 90 | 3,21 % |
| Máximo (@FrontPack2.0) | 34,05 % |

Y los stacks que **realmente** se juntarían — los 7 proyectos de `request-policy.json` más `@LFGungame`:

| Mod | Estado | Central | Cota superior |
|---|---|---:|---:|
| `@DayZ_MCP` | `ESTIMATED_OFFLINE` | 9,8 kB (0,03 %) | 13,4 kB (0,04 %) |
| `@LFPowerGrid` | `ESTIMATED_OFFLINE` | 1.459,9 kB (4,35 %) | 1.904,4 kB (5,68 %) |
| `@SUB_BRZ` | `ESTIMATED_OFFLINE` | 3,2 kB (0,01 %) | 4,7 kB (0,01 %) |
| `@MERCEDES_AMGLF` | `ESTIMATED_OFFLINE` | 4,5 kB (0,01 %) | 6,5 kB (0,02 %) |
| `@LF_VStorage` | `ESTIMATED_OFFLINE` | 788,5 kB (2,35 %) | 1.028,1 kB (3,06 %) |
| `@LFGungame` | `ESTIMATED_OFFLINE` | 81,9 kB (0,24 %) | 107,5 kB (0,32 %) |
| `@LFHeli_OH1` | `SCAN_OK_NO_C_SOURCE` | 0,0 kB | 0,0 kB |
| `@Utopia_PC` | `SCAN_OK_NO_C_SOURCE` | 0,0 kB | 0,0 kB |
| **UNIÓN** | | **2.347,8 kB (7,00 %)** | **3.064,6 kB (9,13 %)** |

**La unión completa de todos tus proyectos consume el 9,13 % del cap incluso usando la cota superior.** El
guard con cota superior **no** es fail-closed en la práctica: pasa con holgura de un orden de magnitud.

**Corrección de una lectura intermedia**: una primera pasada sobre el "Top 20 por cota superior" sugería que
con 4 mods el guard ya rechazaría todo (125 % del cap). Ese top es, por construcción, el peor caso del
workshop entero — server packs completos de terceros (`@FrontPack2.0`, `@LaFronteraCherno`, `@BANOVLF-1`), que
no son lo que se junta en un `SHARED`. Con la tabla completa la conclusión se invierte.

**Recomendación: RATIFICAR la tabla congelada con cota superior**, y tratar el margen del ±30 % como
irrelevante para este uso: sobra tanto cap que la imprecisión no cambia ninguna decisión.

### El riesgo real de Q3 no es la banda, es la caducidad

Lo dice el propio artefacto (`arena_budget_by_mod.md:64`): esta Fase 0 **no calculó hashes de contenido de
PBO**, así que **no hay detección automática de staleness**. La tabla es "una foto fechada, no una autoridad
perpetua". Un mod que crezca tras la medición pasará el guard con su número viejo y nadie se enterará.

Es exactamente el patrón que ya mordió dos veces en la sesión del 2026-07-28: un artefacto que envejece en
silencio y al que se sigue consultando como si fuera actual (los dos workspaces delegados stale).

**Condición sugerida al ratificar Q3**: que el guard exija un hash de contenido por mod (o al menos
`mtime`+tamaño agregados) y **falle cerrado si no coincide** con el registrado en la tabla. Sin eso, Q3
ratificada compra un guard que caduca sin avisar.

### Dos caveats de cobertura

- `@LFHeli_OH1` y `@Utopia_PC` figuran como `SCAN_OK_NO_C_SOURCE` con 0 kB. Para mods que sí tienen scripts,
  eso probablemente significa que su PBO no estaba desplegado en el momento de medir, no que ocupen cero.
  Conviene reverificarlos antes de fiarse de su 0.
- `@LFQuad3` no aparece con estimación (`UNKNOWN_UNREADABLE_INDEX`): cae fail-closed, como está documentado.

---

## Resumen para ratificar

| | Adjudicación de Codex | Recomendación | Condición |
|---|---|---|---|
| **Q1** | TTL 120 s sin participantes | **Ratificar** | ninguna; la constante existe y ya está en producción |
| **Q2** | Desalojo sólo con 0 participantes | **Ratificar** | el check de 0 participantes debe consumirse atómicamente |
| **Q3** | Tabla congelada, cota superior | **Ratificar** | añadir detección de staleness por hash, o el guard caduca en silencio |
