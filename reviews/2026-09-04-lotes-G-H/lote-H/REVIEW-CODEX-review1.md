## VEREDICTO

NO ES SEGURO INTEGRARLO: hay cinco incumplimientos reproducidos de H2/H3/D4; el manifiesto D2 y el camino feliz de `stop` no muestran defectos en esta revisión.

## HALLAZGOS

### MAYOR 1 — Se debilitó el fail-closed del sobre y un diagnóstico incompleto inventa tres `null`

`../ws/tools/dayz_mcp/dayz_test_tool.py:785-813`

[EXACT]

```python
def _retired_for(status: object, run_id: str) -> list[dict[str, object]]:
    if not isinstance(status, dict):
        return []
    raw = status.get("retired_run_diagnostics")
    if not isinstance(raw, list):
        return []
    return [
        item
        for item in raw
        if isinstance(item, dict) and item.get("run_id") == run_id
    ]


def _copy_generation(source: dict[str, object]) -> dict[str, object]:
    return {
        "daemon_generation_at_launch": source.get("daemon_generation_at_launch"),
        "daemon_generation_current": source.get("daemon_generation_current"),
        "generation_changed": source.get("generation_changed"),
    }
```

La cardinalidad se comprueba, pero no que el elemento sea un diagnóstico exacto ni que contenga los tres campos acreditados. `dict.get()` convierte su ausencia en valores nuevos `None`. Reproducción ejecutada con un status que contiene únicamente `{"run_id": RUN_ID}`:

```text
{'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab',
 'error_code': 'run_not_found', 'daemon_generation_at_launch': None,
 'daemon_generation_current': None, 'generation_changed': None}
```

El resultado exigido por D4/H3 es `DayzTestToolError("run_not_found")`, sin sobre ni campos inventados. Además, esto es una regresión verificable: el pre-Lote H comprobaba expresamente la presencia antes de construir el sobre en `../draft/proto/tools/dayz_mcp/dayz_test_tool.py.orig:774-815`:

[EXACT]

```python
_GENERATION_FIELDS = (
    "daemon_generation_at_launch",
    "daemon_generation_current",
    "generation_changed",
)
# ...
if source is None or any(field not in source for field in _GENERATION_FIELDS):
    return None
# ...
envelope[field] = source[field]
```

Fix sugerido: restaurar una validación explícita del registro antes del sobre. Debe exigir el conjunto exacto de ocho claves, `run_id` exacto, `state == "EXITED"`, tipos válidos y coherencia de los tres campos de generación; cualquier desviación cuenta como diagnóstico no acreditado y conserva el `ToolError` original.

### MAYOR 2 — Cinco rutas publican la retirada antes de que el manifiesto la confirme

Ejemplo representativo en `../ws/tools/dayz_mcp/process_lifecycle.py:2661-2677`:

[EXACT]

```python
run.owner_session_id = None
run.owner_lease_id = None
run.processes = []
run.state = "EXITED"
self._retire_run_bindings(run.run_id, "reaped")
self._retire_run_diagnostic(
    run,
    "run_reaped",
    "all_processes_gone_or_foreign",
    "reaped",
    "EXITED",
)
try:
    self.manifest.replace(run)
except Exception:
    return "manifest_failed"
```

El mismo orden `diagnóstico -> persistencia` aparece en:

- `begin_release_owner`: `process_lifecycle.py:2378-2390`.
- `repair_recovery_fault`: `process_lifecycle.py:2487-2502`.
- `repair_manifest_recovery`: `process_lifecycle.py:2582-2597`.
- `_reap_run_locked`: `process_lifecycle.py:2661-2677`.
- `admin_reconcile`: `process_lifecycle.py:3105-3123`.

`stop_run` sí tiene el orden correcto: persiste en `process_lifecycle.py:2152-2154` y sólo añade el diagnóstico en `:2180-2186`.

Reproducción ejecutada inyectando `OSError` en `manifest.replace()` durante un reap:

```text
REPRO1 {"reaped": [], "row_state": "RUNNING_IDLE", "diag_count": 1,
        "diag": {"event": "run_reaped", "decision": "reaped", "state": "EXITED", ...}}
```

La retirada falla y el run sigue presente, pero el anillo afirma que fue retirado. Los reintentos también consumen posiciones del anillo y pueden expulsar diagnósticos reales entre los 32 más recientes.

Fix sugerido: añadir `_retire_run_diagnostic(...)` únicamente después de que `manifest.replace(...)` termine con éxito en las cinco rutas. La mutación del anillo y la persistencia deben permanecer dentro de `_operation_lock` y conservar el orden global `_operation_lock -> manifest._lock -> _activity_lock`.

### MAYOR 3 — `repair_recovery_fault` acepta de nuevo un run ya `EXITED` y duplica su diagnóstico

`../ws/tools/dayz_mcp/process_lifecycle.py:2437-2443,2487-2503`

[EXACT]

```python
run = self.manifest.get(run_id)
if (
    run is None
    or run.launch_operation_id != operation_id
    or run.launch_acknowledged
):
    return {"terminal_safe": False, "error": "identity_ambiguous"}
# ...
run.state = "EXITED"
run.owner_session_id = None
run.owner_lease_id = None
run.processes = []
self._retire_run_bindings(run_id, "recovery_repaired")
self._retire_run_diagnostic(...)
try:
    self.manifest.replace(run)
```

No hay precondición sobre `run.state`. Tras la primera reparación, `launch_acknowledged` sigue siendo `False`, por lo que una repetición acreditada con el hash actual vuelve a devolver éxito y añade otra entrada para el mismo run.

Reproducción ejecutada:

```text
REPRO2 {"first_terminal_safe": true, "second_terminal_safe": true,
        "diag_count": 2, "states": ["EXITED", "EXITED"]}
```

Cuando ese run ya no figure en `runs`, `execute_dayz_test_stop` verá dos coincidencias en `dayz_test_tool.py:841-845` y caerá a `ToolError`, aunque existe una retirada acreditada. Viola la unicidad que H3 necesita y responde a la pregunta de doble entrada con un caso positivo reproducible.

Fix sugerido: `repair_recovery_fault` debe rechazar estados terminales y aceptar sólo los estados de recuperación previstos. Como defensa adicional, el anillo debe impedir una segunda acreditación del mismo evento lógico mientras el run siga representado por el mismo `launch_operation_id`.

### MAYOR 4 — `status()` y `public_status()` pueden mezclar un run presente con su diagnóstico retirado

`../ws/tools/dayz_mcp/process_lifecycle.py:2757-2783`

[EXACT]

```python
def status(self, client: ClientIdentity) -> dict[str, object]:
    # ...
    payload: dict[str, object] = {
        "runs": [self._projected_run(run) for run in self.manifest.list_runs()],
        # ...
        "retired_run_diagnostics": self._publish_retired_diagnostics(),
    }

def public_status(self) -> dict[str, object]:
    # ...
    runs = self.manifest.list_runs()
    active_runs = [run for run in runs if run.state in _ACTIVE_STATES]
    return {
        "runs": [self._projected_run(run) for run in active_runs],
        # ...
        "retired_run_diagnostics": self._publish_retired_diagnostics(),
    }
```

El anillo se lee bajo `_activity_lock` (`process_lifecycle.py:1005-1007`), pero la lista de runs se obtuvo antes bajo el lock privado del manifiesto y ninguno de ambos status adquiere `_operation_lock`. Un retiro completo puede caer entre las dos lecturas.

Reproducción concurrente determinista: el lector capturó la lista antes del reap, el reap terminó, y después el lector publicó el anillo.

```text
{"reaped":["race-run"],
 "published_runs":["race-run"],
 "published_diags":["race-run"]}
```

No es una retirada fallida: es un status rasgado durante una retirada exitosa. Puede hacer que el sobre clasifique `run_not_active` cuando el run ya está ausente, en vez de usar el diagnóstico `run_not_found`.

Fix sugerido: tomar la lista del manifiesto y la copia del anillo como una sola instantánea bajo `_operation_lock`, adquiriendo `_activity_lock` sólo después; no introducir el orden inverso.

### MAYOR 5 — `admin_reconcile` filtra paths y timestamps mediante el campo libre `reason`

`../ws/tools/dayz_mcp/process_lifecycle.py:3022-3023,3111-3117`

[EXACT]

```python
if not isinstance(reason, str) or not reason.strip():
    return self._error("invalid_reason", 400)
# ...
self._retire_run_diagnostic(
    run,
    "admin_reconcile",
    reason.strip() if isinstance(reason, str) else "reconciled",
    "confirmed",
    "EXITED",
)
```

El campo es texto arbitrario del operador y viaja íntegro al status público. Reproducción ejecutada:

```text
REPRO3 {"state":"EXITED",
        "public_reason":"C:\\Users\\alice\\secret\\events.jsonl @ 2026-09-04T05:00:00Z"}
```

El dataclass tiene exactamente ocho claves, pero H2 prohíbe también que un path o timestamp se cuele dentro de una de ellas. El gate sólo ejercita el motivo constante de `stop`, por lo que no discrimina este camino.

Fix sugerido: el diagnóstico público debe usar un código cerrado y sin texto libre, por ejemplo `reason="admin_reconciled"`; el motivo humano puede seguir en el audit privado, que D1 impide leer desde status.

### Controles cerrados sin hallazgo

- **D2 / manifiesto:** el campo está al final con default `None` (`process_lifecycle.py:430-444`), `from_payload` lo lee (`:446-469`), `validate` no toca validaciones anteriores y añade su comprobación (`:471-518`), `_clone` hace `asdict -> from_payload` (`:663-664`) y el escritor usa `asdict` (`:666-674`). El censo AST del producto encontró un solo constructor posicional, en `:1625`, con los 13 argumentos en orden y la generación al final (`:1642`). `quarantine_legacy_active` y `recover_after_restart` transforman clones (`:783-787` y `:854-868`), por lo que conservan el campo.
- **Compatibilidad hacia delante:** un manifiesto anterior sin el campo carga como `daemon_generation_at_launch=None`; se publica `launch=None`, `current=<generación viva>`, `changed=None` por `process_lifecycle.py:245-255`.
- **Compatibilidad hacia atrás:** se escribió un `runs.json` con esta versión y el lector pre-Lote H disponible en `../draft/proto/tools/dayz_mcp/process_lifecycle.py.orig:359-380,537-556` lo abrió sin `invalid_run_manifest`. Si esa versión anterior vuelve a escribir el registro, elimina el campo desconocido; al regresar a esta versión se degrada de forma fail-closed a `launch=None/changed=None`, sin desplazar otros campos.
- **Generación válida:** un run nuevo acredita `self._current_generation() or None` en `process_lifecycle.py:1625-1643`; los runs heredados conservan su valor porque se clonan en `:1617-1624`. El valor actual procede del `ProcessLifecycle` vivo (`:917-919,975-976`) y no se infiere desde el manifiesto.
- **Anillo, fuera de los defectos anteriores:** `deque(maxlen=32)` + `appendleft` (`process_lifecycle.py:929-931,978-998`) conserva el tope y el orden de adquisición. Todos los productores públicos llegan con `_operation_lock` y luego toman `_activity_lock`; las lecturas del anillo toman sólo `_activity_lock`. No encontré adquisición anidada `_activity_lock -> _operation_lock`.
- **Stop válido y regresiones pedidas:** `resolve_stop_run` sigue levantando (`dayz_test_tool.py:228-243`); `execute_dayz_test_stop` toma una sola lectura pre-dispatch en `:830`, copia los tres valores en `:798-813`, mantiene el dispatch feliz en `:847-866` y conserva la relectura post-ejecución para `run_stop_failed` en `:867-880`. El stop exitoso persiste antes de alimentar el anillo (`process_lifecycle.py:2152-2186`) y los stops parciales/fallidos no lo alimentan.
- **Validaciones existentes:** el diff contra las copias `.orig` no muestra debilitamiento del manifiesto ni de `resolve_stop_run`. Sí muestra el debilitamiento concreto del sobre descrito en MAYOR 1.
- **Verificación fresca:** hashes de los dos productos coinciden con el brief. El gate terminó `29 PASS / 0 FAIL / 0 UNMET`; la suite acotada terminó verde en sus 11 módulos. Esos verdes no cubren las cinco reproducciones anteriores.

## BACKLOG

- **Lifecycle del binding (Lote G, fuera de H):** las mismas cinco rutas de MAYOR 2 llaman `_retire_run_bindings` antes de confirmar `manifest.replace`; ante fallo de persistencia, el binding puede quedar retirado mientras el run continúa presente. Las copias pre-Lote H colocaban esa llamada después de persistir en recovery/reap/admin. No lo cuento en el veredicto de H porque el brief manda llevar el binding a backlog.
- **Actividad (Lote G):** el diff de `process_lifecycle.py` contiene también la instantánea/cache/tombstones de actividad. No se auditó ni se re-gateó aquí.
- **Gate/oráculo:** faltan mutantes de persistencia fallida para los cinco productores, reintento idempotente de `repair_recovery_fault`, status rasgado, diagnóstico incompleto y `admin_reconcile(reason=<path/timestamp>)`. Es deuda del gate, no un sustituto de los defectos del producto.

## NO VERIFICADO

- No se arrancó un daemon/cliente MCP real ni se ejercitó HTTP o el launcher nativo; el mecanismo se revisó y reprodujo in-process sobre los bytes exactos indicados.
- No se revisó `server.py`, por prohibición expresa de alcance. H4 sólo queda cubierto por el gate y la suite acotada proporcionados.
- No se corrió el `discover` completo de `tools/tests`; se ejecutaron los 11 módulos nominados por `gate/suite.sh`.
- La compatibilidad hacia atrás se verificó contra la copia pre-Lote H incluida en el paquete (`process_lifecycle.py.orig`), pero no hay repositorio Git ni artefacto etiquetado que permita demostrar que esos bytes fueron la última versión desplegada en producción.
- No hubo verificador independiente de otra lane: las reglas de este encargo prohíben abrir subagentes. Todas las citas y las cinco reproducciones fueron re-leídas y ejecutadas por esta única revisión.
- No se actualizó memoria externa de Obsidian: el único entregable autorizado por el encargo es este archivo en el directorio de lanzamiento.
