# R21 adversarial - cambio de `bridge_status` / poda del manifiesto (2026-08-03)

- **Lane**: Grok, sesion NUEVA y CIEGA, solo lectura (`--tools read_file,grep,list_dir`, `--deny MCPTool`, `--no-memory`).
- **Motivo**: el cambio llevaba en produccion desde el 2026-08-03 sin revision adversarial.
- **sessionId**: `019fdd72-33d5-7301-91dd-07d0f8ebf170`
- **stopReason**: `end_turn` · turnos: 8 · tokens: 364050 · USD 0.3091

## Arbitraje del receptor

**BLOCKER 1: CONFIRMADO contra fichero.** Verificadas las tres citas abriendo el archivo:

- `process_lifecycle.py:193-194` - el constructor hace `_load()` y luego `_prune_exited_on_load()`, que persiste a disco.
- `process_lifecycle.py:189` - `self._lock = threading.RLock()`: intra-proceso, sin lock interproceso sobre `runs.json`.
- `doctor.py:703` - `RunManifestStore(sources.runtime_paths).list_runs()`: el doctor read-only construye el store y por tanto poda y escribe.

**MAJOR 2 y 3 (backup O_EXCL): PLAUSIBLES, no verificados aun** por el receptor.
**MINOR 4 (`runs_retired` no historico): plausible**, sin consumidor de produccion conocido.

Pendiente: fix del BLOCKER. Opcion minima propuesta - modo read-only en `RunManifestStore` que no pode, y que el doctor lo use.

---

## Informe integro de la lane
Revisión adversarial de solo lectura: voy a verificar el código de poda/backup, `public_status` y los consumidores reales, sin dar por bueno el contexto.## VEREDICTO
**UNSOUND** — la poda al cargar convierte un constructor de manifiesto en escritor de disco sin lock interproceso; el doctor “read-only” de producción puede pisar `runs.json` mientras el daemon tiene el estado autoritativo en memoria y perder actualizaciones de runs **activos**.

## HALLAZGOS

### 1. [BLOCKER] · Doctor “read-only” escribe y puede clobber el manifiesto vivo del daemon
`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\doctor.py:703` + `process_lifecycle.py:193-194,247-269`

```703:703:tools/dayz_mcp/doctor.py
        runs = RunManifestStore(sources.runtime_paths).list_runs()
```

```193:194:tools/dayz_mcp/process_lifecycle.py
        self._load()
        pruned = self._prune_exited_on_load()
```

```247:269:tools/dayz_mcp/process_lifecycle.py
    def _prune_exited_on_load(self) -> bool:
        retired = {
            run_id: run
            for run_id, run in self._runs.items()
            if run.state == "EXITED"
        }
        ...
        self._runs = {
            run_id: run
            for run_id, run in self._runs.items()
            if run.state != "EXITED"
        }
        try:
            self._persist_locked()
```

El propio doctor se anuncia como solo lectura (`doctor.py:1014`: `"Read-only DayZ-MCP session doctor"`) y el README manda usarlo con el daemon en marcha (`tools/README-mcp.md`: bridge_status y luego doctor).

**Escenario concreto**
1. Daemon en marcha. Disco: run `R` en `RUNNING` con procesos `[p1]` + varios `EXITED` (normal tras `reap_dead_runs`).
2. Doctor construye `RunManifestStore` → carga snapshot S0 (`R` con `[p1]` + EXITED).
3. Mientras tanto el daemon adopta/extiende `R` (p. ej. añade `p2` o cambia owner) y hace `replace` → disco = S1.
4. Doctor sigue con la vista S0, poda EXITED y hace `atomic_write_bytes` de **S0 sin EXITED** sobre S1.
5. Disco queda con `R` desactualizado; el daemon sigue con S1 en RAM. Si el daemon reinicia o cae, recupera el estado clobberado: owner/procesos perdidos, adopt/start/stop posteriores fallan por identidad o `run_not_found`/`process_identity_mismatch`.

**Impacto:** pérdida de estado lifecycle **activo** en disco (no solo tombstones EXITED). No hay lock de fichero entre procesos: el `_lock` de `RunManifestStore` es solo `threading.RLock` intra-proceso (`:189`).

---

### 2. [MAJOR] · El backup O_EXCL solo cubre la primera poda; las siguientes borron sin copia fresca
`process_lifecycle.py:218-227,259-260`

```218:227:tools/dayz_mcp/process_lifecycle.py
    def _create_preprune_backup(self, raw: bytes) -> bool:
        backup_path = self.paths.runs_path.with_name(
            self.paths.runs_path.name + ".bak-preprune"
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(backup_path, flags, 0o600)
        except FileExistsError:
            return True
```

El test lo fija a propósito (`test_process_lifecycle.py:311-325`: “second load does not overwrite”).

**Escenario concreto**
1. Arranque 1: 670 EXITED → backup = manifiesto completo, `runs.json` podado.
2. Sesión larga: reaper marca N runs nuevos como EXITED; quedan en disco/memoria.
3. Arranque 2: `FileExistsError` → `return True` **sin** reescribir backup.
4. Poda borra esos N EXITED del disco.
5. Esos N no están en `runs.json.bak-preprune` (sigue siendo el snapshot del paso 1, ya sin esos run_ids).

**Impacto:** la promesa de “backup previo a la poda” es solo para la **primera** ejecución en esa caja. Pérdidas posteriores de tombstones son irreversibles respecto a ese backup. No son runs con procesos vivos (EXITED exige `processes` vacío, `:146-149`), pero sí historial de `run_id`/auditoría de manifiesto.

---

### 3. [MAJOR] · `FileExistsError` trata cualquier backup existente como éxito fail-open (incl. truncado)
`process_lifecycle.py:225-227`

```225:227:tools/dayz_mcp/process_lifecycle.py
        except FileExistsError:
            return True
```

No comprueba tamaño, legibilidad ni que el backup sea un manifiesto v1 válido.

**Escenario concreto**
1. Primera poda: `os.open` O_EXCL crea el fichero; el proceso muere a mitad de `write`/`fsync` (kill, power loss). Queda `runs.json.bak-preprune` parcial o vacío; el `except OSError` que hace `unlink` **no** corre.
2. Siguiente arranque: existe el path → `FileExistsError` → `True`.
3. `_prune_exited_on_load` borra todos los EXITED de `runs.json` y persiste.
4. La única “copia de seguridad” es basura; no hay fail-closed.

**Impacto:** el claim de fail-closed del backup no se sostiene en el camino “backup ya existe”. Solo es fail-closed el `OSError` en el **primer** `open`/`write` (`:227-228`, `:235-244` → `return False` y no se toca `_runs` ni se llama a `_persist_locked`).

---

### 4. [MINOR] · `runs_retired` no es histórico; tras poda/reinicio queda en 0
`process_lifecycle.py:1945-1954`

```1949:1954:tools/dayz_mcp/process_lifecycle.py
        runs = self.manifest.list_runs()
        # Keep every non-terminal state: admin recovery needs STARTING and STOPPING.
        active_runs = [run for run in runs if run.state in _ACTIVE_STATES]
        return {
            "runs": [dataclasses.asdict(run) for run in active_runs],
            "runs_retired": len(runs) - len(active_runs),
```

**Escenario concreto**
1. Daemon vivo: 50 EXITED en memoria → `public_status().runs_retired == 50`.
2. Reinicio: poda al cargar elimina EXITED del store.
3. `GET /status` → `runs_retired == 0` aunque se hayan retirado cientos en la vida de la caja.

**Impacto:** no encontré consumidor de producción de `runs_retired` (solo tests). No rompe harness/`dayz_test_tool` (usan `/lifecycle/status` → `status()`, sin filtro, `:1935-1943`). Es un contador engañoso si alguien lo interpreta como total retirado.

---

## DIMENSIONES LIMPIAS

**C (estados olvidados — en lo esencial)**  
`RUN_STATES = {STARTING, RUNNING, RUNNING_IDLE, STOPPING, EXITED, UNRECONCILED}` (`:23-25`); `_ACTIVE_STATES = RUN_STATES - {EXITED}` (`:26`). Único terminal podado = `EXITED`. `UNRECONCILED` es reapable (`_REAPABLE_STATES`, `:30`) y luego pasa a EXITED; no reintroduce por sí solo el crecimiento infinito de tombstones en `public_status`. Los usos de `_ACTIVE_STATES` en start/adopt/release (`:363`, `:872`, `:1468`, `:1525`) tratan “activo” de forma coherente con no-EXITED. Residual de diseño (no bug de estados): los EXITED **siguen** en memoria/disco hasta el próximo load; solo `public_status` los oculta — `status()` a propósito no.

**D (rotura de consumidor — salvo semántica de `runs_retired`)**  
`/status` del daemon usa `public_status()` (`daemon.py:642`). `admin_cli` reconcile (`admin_cli.py:166-187`) necesita `UNRECONCILED|STARTING|STOPPING|RUNNING_IDLE` ownerless; esos siguen en `_ACTIVE_STATES` y no se podan. `dayz_test_tool` / `lifecycle_status` van a `/lifecycle/status` → `status()` sin filtrar (`process_lifecycle.py:1935-1943`, `loopback.py:1546`). No hay otro consumidor de `list_runs()` post-poda que asuma ver EXITED vía bridge.

**E (fail-closed del primer backup — parcialmente)**  
Si `os.open`/`write`/`fsync` lanzan `OSError` en la **creación** del backup: `return False`, no se muta `_runs`, no se llama `_persist_locked` (`:259-260`); el store arranca con EXITED en memoria y disco intacto (cubierto por test `:327-350`). Si `_persist_locked` falla tras backup OK: rollback de memoria y `raise` (`:268-271`). Lo que **no** es fail-closed es el ramo `FileExistsError` (hallazgos 2–3).

**A (pérdida de runs que “todavía importan” por la poda en sí)**  
La poda solo elimina `state == "EXITED"`; la validación prohíbe EXITED con owner o processes (`:146-149`). Un run RUNNING/STARTING/etc. no se borra por la poda. Crash **entre** backup completo y escritura del podado: el disco sigue con el manifiesto viejo; el siguiente load reintenta. El riesgo A real no es “podar un run vivo por estado”, sino el **clobber concurrente** (hallazgo 1) y backups incompletos/no rotados (2–3).

**B (concurrencia intra-daemon)**  
Un solo `RunManifestStore` vive en el daemon; mutaciones de lifecycle van bajo `_operation_lock` + `_lock` del store en `replace`/`add`. La poda en `__init__` es single-thread al arrancar ese proceso. El agujero de carrera es **interproceso** (doctor u otro `RunManifestStore` sobre el mismo `runs.json`), no dos threads del mismo daemon cargando a la vez.

## LO QUE NO PUDE VERIFICAR

- No ejecuté el daemon, doctor ni tests; no observé carreras reales ni midí payloads.
- No inspeccioné el `runs.json` / `runs.json.bak-preprune` reales de la caja de producción (tamaño, contenido del backup, si ya se hizo la primera poda).
- No verifiqué en runtime si el reaper escribe EXITED con la frecuencia suficiente para que doctor casi siempre entre en el path de escritura.
- No auditó el log de auditoría (`run_reaped`) como mitigación forense de tombstones perdidos en podas 2..N.
- No revisé consumidores fuera de `tools/dayz_mcp` (skills, scripts de vault, agentes externos) que pudieran parsear `lifecycle.runs` de `/status` asumiendo EXITED.
