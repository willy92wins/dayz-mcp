## FINDINGS

**R9-REG-01 — P2 — Una negativa puede publicar la rotación de una observación anterior.**

F1 conserva correctamente `null` al rechazar la clasificación (`tools/dayz_mcp/process_lifecycle.py:3044`). Pero el escritor deja intacta cualquier observación previa del mismo ID:

`tools/dayz_mcp/process_lifecycle.py:1844`:
```python
        if type(run.storage_rotated) is not bool:
            return
```

El lector tampoco considera ese `null` autoritativo; continúa hacia el log:

`tools/dayz_mcp/dayz_test_tool.py:1976`:
```python
            if "storage_rotated" in matches[0]:
                measured = _decode_storage_observation(matches[0])
                if measured[0] is not None:
                    return measured
    observations = status.get("storage_observations")
```

**Precondición:** un log obsoleto o manipulado contiene una observación del mismo `run_id` que la negativa. No requiere duplicados dentro de la lista.

Ejecuté el recorrido loader → add → negativa → replace → status → `_execute_request`: la fila quedó en `null`, pero el resultado devolvió `storage_rotated=true`, `storage_backup="storage_1.old"` y `error_code="storage_recovery_required"`. Persistió después de podar `EXITED` y recargar. También ocurrió con un `launch_operation_id` anterior añadido al log: F2 elimina esa clave extra, por lo que el fallback pierde el discriminador.

El control con el método anterior, extraído de `../base_cand`, registró `False` y sobrescribió la observación antigua. La regresión aparece al combinar el nuevo desconocido de F1 con la conservación y recuperación del log.

**Escenario mínimo ejecutado**, desde este directorio con `venv311\Scripts\python.exe -B -`:

```python
import sys
sys.path.insert(0, "tools")
from dataclasses import asdict
from unittest.mock import patch
from dayz_mcp import process_lifecycle as p
from dayz_mcp import dayz_test_tool as t, dayz_test_storage as d

R = "11111111-1111-4111-8111-111111111111"
s = p.RunManifestStore.empty_for_recovery(None)
s._storage_observations = p._storage_observations_from_payload([dict(
    run_id=R, storage_rotated=True, storage_backup="storage_1.old",
    storage_reset_notice=d.RESET_NOTICE)])
r = p.RunRecord(R, None, None, "EXITED", "", "@SameMod", "", "mission", [])
l = p.ProcessLifecycle.__new__(p.ProcessLifecycle)
refusal = d.RotationResult(
    False, False, None, None, "a"*64, True, None, "refuse", "bad")
with patch.object(s, "_persist_locked"), patch.object(
        d, "prepare_storage", return_value=refusal):
    s.add(r)
    assert l._rotate_storage_for_launch(dict(
        storage_seal="a"*64, mission="mission", mod="@SameMod"),
        R, r) == "storage_recovery_required"
    s.replace(r)
assert s.get(R).storage_rotated is None
status = dict(runs=[asdict(s.get(R))],
              storage_observations=s.storage_observations())
assert t._storage_observation_from_status(status, R) == (
    True, "storage_1.old", d.RESET_NOTICE)
```

**Fix sugerido:** invalidar la observación anterior al registrar un run nuevo sin medición y conservar esa invalidación durante la poda. Un `null` explícito de la fila tampoco debería rescatar una medición contradictoria del log.

## CHECKED

- **F2:** ejecutados valores no-lista, entradas malformadas, tipos incorrectos, claves ausentes, backups inválidos, claves extra y gemelos malformados en ambos órdenes. Los duplicados se cuentan antes del filtrado y truncado; 10 000 entradas conservan las últimas 32.
- **Persistencia:** trazados y ejecutados add, replace, serialización, recarga, poda y copias defensivas. Fallos inyectados en add/replace/prune restauran filas y observaciones; un fallo de checkpoint restaura también los bytes.
- **Recuperación:** `RUNNING → RUNNING_IDLE` y `STARTING → UNRECONCILED` mantienen los campos. Trazada la recuperación del launch intent por clonación del registro.
- **Run rows:** siguen rechazando campos storage inválidos. Ambas proyecciones lifecycle conservan los valores del registro.
- **F1:** negativa, rotación y reutilización producen respectivamente `None`, `True` y `False` en el método real.
- **F3/correlación:** seis terminales de creación —server/offline/all, ok/fail— y nueve sobre runs existentes —client/offline/stop, incluyendo cleanup degradado— dieron los campos esperados. Pasaron fallo previo al run, terminal legacy fallido, operación discrepante y filas duplicadas.

## NOT VERIFIED

- Procesos reales, daemon, DayZ, herramientas MCP y durabilidad física: las pruebas sustituyeron almacenamiento y ejecución por dobles en memoria.
- Identidad Git del commit: este directorio carece de metadatos Git utilizables.
- El hallazgo exige una observación contradictoria del mismo ID; no reproduje su aparición espontánea con UUID nuevos.
- Sin cambios ni archivos escritos; sin acceso a `%LOCALAPPDATA%`.

