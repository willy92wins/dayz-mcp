# Prompt de implementación — BUG-067 mitigación fail-closed (Codex, sesión 1)

Proyecto: DayZ_MCP · Patrón: implementation-handoff · Fecha: 2026-07-29
Workspace delegado: `C:\Users\guill\AppData\Local\Temp\dayz-mcp-p9-bug067\DayZ Projects\`

===== PROMPT INICIO =====

Tarea: implementar la **mitigación mínima fail-closed de BUG-067** en `process_lifecycle.py`
del proyecto DayZ_MCP. Esta sesión cubre **únicamente los pasos 1-4** de abajo: un helper nuevo,
un parámetro nuevo, un punto de inserción y su suite de tests. El fix de fondo de BUG-067
(adopción/reconciliación de runs ajenos) y la Fase 2 (cola de runs) quedan FUERA. No los
implementes aquí ni siquiera parcialmente.

## Contexto en tres líneas

`lifecycle_start` decide si admite un arranque mirando **sólo el manifest**. Un
`DayZDiag_x64.exe` lanzado fuera del lifecycle (script, arranque manual, `run-fase3.ps1`) es
invisible para la admisión: el arranque **lo pisa en silencio** y otro agente pierde su servidor.
La sonda de procesos que hace falta **ya existe y ya está inyectada en producción**; simplemente
nadie la consulta desde la admisión. Esta sesión la conecta. No escribes escaneo de procesos
nuevo: eso ya está escrito y testeado.

## Carga inicial obligatoria

Trabaja SIEMPRE dentro del workspace
`C:\Users\guill\AppData\Local\Temp\dayz-mcp-p9-bug067\DayZ Projects\`. Ya está montado y los
imports YA resuelven ahí dentro (verificado: `dayz_mcp.__file__` apunta al workspace, no a
OneDrive). Lee estos cinco antes de tocar nada:

1. `...\DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py` — **el único módulo de producto que
   vas a modificar**. Anclas que importan: `start_run` (def en :780; cadena de admisión
   :813-874), `_start_rejection` (:668-687), `_diag_snapshot_registered` (:1907-1931),
   `_diag_snapshot_empty` (:1933-1947), `_ACTIVE_STATES` (:26).
2. `...\DayZ_MCP_dev\tools\dayz_mcp\orphan_guard.py` :273-289 — contrato de retorno de la sonda
   `snapshot_processes_by_name`. **SÓLO LECTURA.**
3. `...\DayZ_MCP_dev\tools\dayz_mcp\daemon.py` :508-525 — cómo se inyecta `diag_probe` en
   producción (`lambda: orphan_guard.snapshot_processes_by_name(["DayZDiag_x64.exe"])`).
   **SÓLO LECTURA.**
4. `...\DayZ_MCP_dev\tools\dayz_mcp\dayz_test_worker.py` :24-41 y :328-333 — por qué el código
   del wire debe seguir siendo `active_run_exists`. **SÓLO LECTURA; modificarlo está PROHIBIDO**
   (razón en Restricciones §3).
5. `...\DayZ_MCP_dev\tools\tests\test_process_lifecycle.py` — la suite que vas a extender.
   Fixture que ya inyecta `diag_probe` en :225; usos en :670 y :920.

NO leas el vault (`ObsidianVault`), ni `plans\`, ni los directorios `_fase*` / `_s0` / `_poc`.
NO abras `session_coordination.py`: tocarlo está prohibido por BUG-046 y no lo necesitas.

## Alcance acotado

### Paso 1 — helper nuevo `_foreign_diag_reason`

Añádelo a `ProcessLifecycle`, **inmediatamente después de `_diag_snapshot_empty`** (tras :1947),
para que los tres consumidores de la sonda queden juntos.

Firma: `def _foreign_diag_reason(self, registered_pids: set[int]) -> str | None:`

Contrato:

- Devuelve `None` cuando la admisión PUEDE continuar.
- Devuelve un motivo de audit (string no vacío) cuando la admisión DEBE rechazarse.
- **Gates de validación idénticos a `_diag_snapshot_registered` (:1910-1928)**, en el mismo
  orden y devolviendo `"diag_snapshot_unknown"` en todos ellos: `self.diag_probe is None`; la
  llamada lanza excepción; el resultado no es `dict`; `result.get("known") is not True`;
  `processes` no es `list`; un elemento no es `dict`; un `pid` no es `int` positivo (y
  `isinstance(pid, bool)` cuenta como inválido). Cópialos, no los reinventes.
- Predicado final: sobre el conjunto `observed` de PIDs observados, si
  `observed - registered_pids` NO está vacío → devuelve `"foreign_diag_process"`. Si está vacío
  → devuelve `None`.

**Esto es lo más importante del encargo y el defecto que la revisión va a buscar primero:
el predicado es UNIDIRECCIONAL.** NO uses `observed != registered_pids` (que es lo que hace
`_diag_snapshot_registered`, correctamente, para SU caso de uso, que es otro). Un PID
registrado que ya murió y por tanto NO aparece en el snapshot es un run pendiente de reap, no
un proceso ajeno, y **no debe rechazar la admisión**. Si usas igualdad de conjuntos, cualquier
arranque posterior a un crash queda bloqueado hasta que alguien reconcilie a mano: eso es una
regresión peor que el bug que arreglas.

### Paso 2 — desacoplar el motivo de audit del código del wire en `_start_rejection`

Hoy `_start_rejection` (:668-687) usa el mismo string `reason` para el audit y para el error que
viaja al cliente. Añade un parámetro **keyword-only** `audit_reason: str | None = None`:

- La llamada a `self._audit(...)` (:674-679) pasa `audit_reason or reason` como motivo.
- **Todo lo demás sigue usando `reason` sin cambios**: el `self.coordinator.reject_reservation(...)`
  del camino de fallo de audit (:680-685) y el `self._reject_reserved(authority,
  "lifecycle_start", reason)` final (:687).
- Los diez call-sites existentes (:798, :804, :817, :820, :822, :829, :831, :844, :870, :874) NO
  se tocan: al no pasar el argumento nuevo, su comportamiento queda byte a byte idéntico.

### Paso 3 — conectar la comprobación en `start_run`

Punto de inserción **exacto**: entre la línea `                return self._start_rejection(client, authority, "active_run_exists")`
(el `elif active:` de :873-874) y `            if self._quarantined():` (:875).

Ahí:

1. Calcula el conjunto registrado a partir de la lista `active` que ya existe en el scope
   (:813): la unión de los `record.pid` de `run.processes` de **todos** los runs activos.
2. Llama a `_foreign_diag_reason` con ese conjunto.
3. Si devuelve un motivo → `return self._start_rejection(client, authority,
   "active_run_exists", audit_reason=<motivo>)`.

Por qué ese punto y no otro, y es vinculante: va **después** de toda la cadena de admisión
basada en manifest (`if supplied_run_id` / `elif new_run_id` / `elif active`), así que los
rechazos que hoy existen conservan su motivo de audit y su comportamiento exactos, y el motivo
nuevo aparece **sólo** cuando el manifest dice "vía libre" pero la caja dice lo contrario. La
superficie de regresión sobre los tests existentes es cero.

**No añadas la comprobación al retorno temprano del reintento idempotente** (:861-868, el
`return {"ok": True, "run_id": existing.run_id, ...}`). Ese camino devuelve un `RunRecord` que
ya existía y no lanza ningún proceso nuevo, así que no puede pisar nada; meterla ahí sólo
rompería reintentos legítimos. Es un límite declarado de esta mitigación, no un olvido.

### Paso 4 — tests en `tests\test_process_lifecycle.py`

Extiende el módulo existente (no crees fichero nuevo). Cobertura mínima obligatoria, **un test
por punto**:

1. **El bug, cerrado**: hay un `DayZDiag` vivo con un PID que no está en ningún run del
   manifest → `lifecycle_start` rechaza. Asevera las tres cosas: el error del wire es
   `active_run_exists`, el motivo escrito en el audit es `foreign_diag_process`, y **no se creó
   ningún `RunRecord` nuevo**.
2. **El anti-verde-falso (imprescindible)**: un run registrado cuyo PID **ya no aparece** en el
   snapshot (murió sin reapear) y ningún PID ajeno → la comprobación nueva **NO** rechaza.
   Este test es el que distingue el predicado unidireccional de la igualdad de conjuntos:
   escríbelo de forma que **falle** si alguien cambia el predicado a `!=`, y compruébalo de
   verdad (ver Bloque B).
3. **Camino limpio**: snapshot cuyos PIDs coinciden exactamente con los registrados → no
   rechaza.
4. **Sonda ausente**: `diag_probe = None` → rechaza, con motivo de audit
   `diag_snapshot_unknown`.
5. **Sonda que lanza excepción** → rechaza con `diag_snapshot_unknown`.
6. **Payload malformado**: al menos dos casos de los gates (p. ej. `known` ausente y un `pid`
   que es `bool`) → rechazan con `diag_snapshot_unknown`.
7. **Extensión de run propio**: camino `supplied_run_id` con los PIDs del propio run vivos y
   registrados → NO rechaza por la comprobación nueva.
8. **`_start_rejection` sin `audit_reason`**: el motivo del audit sigue siendo el `reason` de
   siempre (protege los diez call-sites que no tocas).

Comando literal esperado, desde
`C:\Users\guill\AppData\Local\Temp\dayz-mcp-p9-bug067\DayZ Projects\DayZ_MCP_dev\tools\`:

```
.venv-mcp\Scripts\python.exe -m unittest tests.test_process_lifecycle
```

Baseline actual de ese módulo: **54 tests, OK**. Al cerrar debe seguir en OK con exactamente
los tests que hayas añadido de más, y el conteo nuevo declarado en el Bloque B.

Y como gate de vecindad, también literal:

```
.venv-mcp\Scripts\python.exe -m unittest tests.test_dayz_test_worker tests.test_dayz_test_tool tests.test_task7_final_lifecycle_regressions
```

## Restricciones críticas (vinculantes toda la sesión)

1. **Sólo stdlib.** Cero dependencias nuevas. El módulo ya importa lo que necesitas.
2. **Un solo módulo de producto tocado: `process_lifecycle.py`.** Más el fichero de tests. Si
   crees necesitar un tercero, PARA y decláralo en el Bloque C en vez de tocarlo.
3. **PROHIBIDO tocar `dayz_test_worker.py`.** Su `PRE_ADMISSION_REJECTION_CODES` es una lista
   cerrada de la Fase 1 y ese módulo va **sellado dentro de `app.pyz`**: modificarlo obligaría a
   un rebuild del bundle y a un rollout CAS que no están autorizados en esta sesión. Por eso el
   código del wire tiene que seguir siendo `active_run_exists` y el diagnóstico viaja por el
   audit. Es una decisión ya adjudicada por el usuario, no la reabras.
4. **PROHIBIDO matar, terminar o señalizar ningún proceso.** Esta mitigación **observa y
   rechaza**, nada más. `orphan_guard.kill_pid` **no existe y no se debe crear** (invariante de
   BUG-064). Tampoco selecciones procesos por substring: la sonda ya hace match exacto.
5. **PROHIBIDO tocar `session_coordination.py`** (BUG-046) y **`orphan_guard.py`** (sólo lectura).
6. **Nada de camino de adopción.** Adoptar o reconciliar el run ajeno es el fix de fondo, está
   fuera de la Fase 1 a propósito y necesita una auditoría que aún no se ha hecho. Te va a
   tentar porque `admin_reconcile` está justo debajo del código que vas a leer y parece
   reutilizable. Resiste: en esta sesión el run ajeno se RECHAZA, no se adopta.
7. **NO improvises fuera de este encargo.** Si algo no encaja (una firma que no es la que digo,
   un test existente que se pone rojo por una razón que no esperabas), aplica la interpretación
   que preserve el comportamiento actual, sigue, y decláralo en el Bloque C con `path:línea`.
   Regla anti-parada: una ambigüedad menor no justifica detener la tanda; una contradicción de
   contrato sí, y entonces paras y la devuelves.
8. **No te autorrevises ni hagas pasadas de limpieza.** La R21 es una sesión aparte. Termina la
   implementación y para.
9. **Comentarios en inglés**, tono impersonal de cabecera vanilla (qué hace y por qué; nada de
   primera persona, fechas ni narrativa). El módulo ya tiene docstrings así: imítalos. El
   porqué del predicado unidireccional SÍ merece una línea de comentario: es justo lo que un
   lector futuro va a querer cambiar por `!=`.

## CÓMO ESCRIBIR ARCHIVOS (obligatorio — apply_patch está ROTO en este host)

- NUNCA uses apply_patch ni tu editor integrado.
- **Vía por defecto: `python open(path, "w", encoding="utf-8", newline="\n").write(...)`.**
  No añade BOM.
- `Set-Content/Add-Content -Encoding utf8` (PowerShell 5.1) **AÑADE BOM** (`EF BB BF`). Para
  Python es inocuo, pero evítalo igualmente por consistencia; si lo usas, comprueba después que
  el fichero NO empieza por `EF BB BF`.
- PROHIBIDO Base64 / `[Convert]::FromBase64String` / `[IO.FileStream]`: el filtro del proveedor
  BLOQUEA ese patrón y el archivo no llega a disco.
- Tras CADA archivo: verifica con `python -m py_compile <path>`.
- Si una escritura falla pese a todo: imprime el contenido ÍNTEGRO por stdout entre
  `===FILE <path abs> BEGIN===` / `===FILE END===` y sigue — el orquestador materializa.

## Notas de entorno (no las descubras a base de perder tiempo)

- Tarea **offline**: no necesitas la unidad `P:`, ni el juego, ni red. Si tu preflight pide
  `P:`, sáltatelo.
- El intérprete es el del venv del workspace:
  `...\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe`. Los imports ya resuelven al workspace.
- **Todos** tus outputs (código, logs, notas) van dentro del árbol del `-C`. No escribas fuera.
- Hay cuatro módulos de test no deterministas bajo carga —`test_bug046_startup_deadlock`,
  `test_task7_review_regressions`, `test_bug046_audit_fault_recovery`, `test_client_mode`—: si
  alguno sale rojo, NO es cosa tuya y NO lo arregles; menciónalo en el Bloque C y sigue.

## Output esperado al cerrar

### Bloque A — Archivos creados/modificados
Paths absolutos y tamaño. Deberían ser exactamente dos.

### Bloque B — Resultado de los tests
Output **literal** (no parafraseado) de los dos comandos de arriba. Además, y es obligatorio:
la **prueba en rojo del test anti-verde-falso** — cambia temporalmente el predicado de
`_foreign_diag_reason` a `observed != registered_pids`, ejecuta el módulo, pega el fallo que
produce ese test concreto, y **revierte el predicado**. Un test que nunca se ha visto en rojo
no acredita nada.

### Bloque C — Hallazgos durante la implementación
Fichero/sección, qué no encajaba, qué aplicaste, sugerencia. Si no hay: "Sin hallazgos."

### Bloque D — Handoff técnico
Estado al cierre, próximo paso, y qué deuda deja esta mitigación para el fix de fondo.

===== PROMPT FIN =====
