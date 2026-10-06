**C1**

`tools/dayz_mcp/process_lifecycle.py`
```python
3024:         self._record_storage_rotation(provisional, result)
3025:         if not result.launch_allowed:
3026:             return "storage_recovery_required"
```

`snippet_matches_file`: CONFIRMED  
`inference_holds`: HOLDS  
El resultado se copia al provisional antes de comprobar el permiso; el llamador entrega ese provisional en `tools/dayz_mcp/process_lifecycle.py:3909`.

**C2**

`tools/dayz_mcp/process_lifecycle.py`
```python
3055:             return
3056:         provisional.storage_rotated = False
3057:         provisional.storage_backup = None
3058:         provisional.storage_reset_notice = None
```

`snippet_matches_file`: CONFIRMED  
`inference_holds`: HOLDS  
La rama falsa de `tools/dayz_mcp/process_lifecycle.py:3044` asigna esos tres valores; `RotationResult.storage_rotated` es booleano (`tools/dayz_mcp/dayz_test_storage.py:105`).  
La reutilización produce los mismos valores (`tools/dayz_mcp/dayz_test_storage.py:223`, `:665-674`).

**C3**

`tools/dayz_mcp/dayz_test_storage.py`
```python
349: def _blocked(reason: str, seal: str) -> RotationResult:
350:     return RotationResult(
351:         launch_allowed=False,
352:         storage_rotated=False,
```

`snippet_matches_file`: CONFIRMED  
`inference_holds`: HOLDS  
Los journals ilegibles o imposibles devuelven `_blocked` (`tools/dayz_mcp/dayz_test_storage.py:474`, `:486`, `:509`), propagado por `prepare_storage` (`:640-644`).  
La colisión devuelve `_blocked` en `:564-566`, mediante `rotate_storage`, cuyo resultado devuelve `prepare_storage` en `:654-662`.

**C4**

`tools/dayz_mcp/process_lifecycle.py`
```python
1819:     def _note_storage_observation_locked(self, run: RunRecord) -> None:
1820:         """Keep a measured rotation after the EXITED row is pruned.
1821: 
1822:         Unknown stays off this list. The list is bounded and keyed by run id.
1823:         """
1824:         if type(run.storage_rotated) is not bool:
1825:             return
1826:         entry = {
1827:             "run_id": run.run_id,
1828:             "storage_rotated": run.storage_rotated,
1829:             "storage_backup": run.storage_backup,
1830:             "storage_reset_notice": run.storage_reset_notice,
1831:         }
1832:         self._storage_observations = [
1833:             item
1834:             for item in self._storage_observations
1835:             if item.get("run_id") != run.run_id
1836:         ]
1837:         self._storage_observations.append(entry)
1838:         overflow = len(self._storage_observations) - _STORAGE_OBSERVATION_BOUND
1839:         if overflow > 0:
1840:             del self._storage_observations[:overflow]
```

`tools/dayz_mcp/dayz_test_tool.py`
```python
1928:     if rotated is False:
1929:         return False, None, None
```

`snippet_matches_file`: CONFIRMED  
`inference_holds`: HOLDS  
El rechazo conduce al asentamiento (`tools/dayz_mcp/process_lifecycle.py:3919-3931`), que copia el provisional y establece `EXITED` (`:3398-3405`).  
`replace` incorpora y persiste la observación (`:1878-1880`, `:1793-1794`); el worker conserva la identidad del intento fallido (`tools/dayz_mcp/dayz_test_worker.py:661-666`).  
La respuesta consulta ese intento y publica el booleano decodificado (`tools/dayz_mcp/dayz_test_tool.py:1805-1807`, `:1865-1868`, `:1548`).

**C5**

`tools/dayz_mcp/process_lifecycle.py`
```python
1803:         atomic_write_bytes(self.paths.runs_path, raw)
1804:         if self._checkpoint is not None:
1805:             try:
1806:                 self._checkpoint(raw)
1807:             except Exception:
1808:                 atomic_write_bytes(self.paths.runs_path, previous)
1809:                 try:
1810:                     self._checkpoint(previous)
1811:                 except Exception:
1812:                     pass
1813:                 raise
```

`snippet_matches_file`: CONFIRMED  
`inference_holds`: DOES NOT HOLD  
La excepción sí propaga y los llamadores restauran memoria (`tools/dayz_mcp/process_lifecycle.py:1862-1865`, `:1881-1884`, `:1754-1757`).  
Pero una escritura compensatoria puede lanzar después de reemplazar el archivo: `tools/dayz_mcp/runtime_state.py:2315` ejecuta `os.replace` y `:2336-2337` puede lanzar después.  
Por tanto, esa excepción no garantiza que el disco conserve los bytes nuevos.

**C6**

`tools/dayz_mcp/process_lifecycle.py`
```python
228: def _storage_observations_from_payload(value: object) -> list[dict[str, object]]:
229:     if value is None:
230:         return []
231:     if not isinstance(value, list) or len(value) > _STORAGE_OBSERVATION_BOUND:
232:         raise ValueError("invalid_run_manifest")
233:     observations: list[dict[str, object]] = []
234:     seen: set[str] = set()
235:     for item in value:
236:         if not isinstance(item, dict) or set(item) != {
237:             "run_id",
238:             "storage_rotated",
239:             "storage_backup",
240:             "storage_reset_notice",
241:         }:
242:             raise ValueError("invalid_run_manifest")
243:         run_id = item.get("run_id")
244:         if not isinstance(run_id, str) or not run_id or run_id in seen:
245:             raise ValueError("invalid_run_manifest")
246:         rotated = item.get("storage_rotated")
247:         backup = item.get("storage_backup")
248:         notice = item.get("storage_reset_notice")
249:         try:
250:             _validate_storage_rotation(rotated, backup, notice)
251:         except ValueError as exc:
252:             raise ValueError("invalid_run_manifest") from exc
253:         if type(rotated) is not bool:
254:             raise ValueError("invalid_run_manifest")
```

```python
1158: _STORAGE_OBSERVATION_BOUND = 32
```

`snippet_matches_file`: CONFIRMED  
`inference_holds`: HOLDS  
Los tres supuestos lanzan `invalid_run_manifest`; `_load` llama al parser sin capturar esa excepción (`tools/dayz_mcp/process_lifecycle.py:1769-1771`).  
Es validación defensiva explícita; el código citado demuestra el rechazo completo.

**C7**

`tools/dayz_mcp/daemon.py`
```python
475:         manifest = RunManifestStore.empty_for_recovery(
476:             paths,
477:             checkpoint=lifecycle_recovery_store.checkpoint_manifest,
478:         )
```

`snippet_matches_file`: CONFIRMED  
`inference_holds`: DOES NOT HOLD  
Un manifiesto inválido no garantiza que el daemon arranque: sin checkpoint lanza `lifecycle_manifest_checkpoint_missing`; con un fault incompatible lanza `lifecycle_recovery_fault_conflict` (`tools/dayz_mcp/daemon.py:459-474`).  
Cuando alcanza la rama citada, sí vacía todas las filas (`tools/dayz_mcp/process_lifecycle.py:1682-1683`); la reparación sustituye ese manifiesto (`:6998-7005`).

**C8**

`tools/dayz_mcp/dayz_test_tool.py`
```python
1544:         # null: this call did not observe a rotation (no status, another run,
1545:         # a launch that does not create storage, a legacy row). false: the
1546:         # run measured that it did not rotate. true is only that measurement.
```

```python
1805:     if terminal.attempt_run_id is not None:
1806:         storage_run_id: str | None = terminal.attempt_run_id
1807:         storage_operation = terminal.launch_operation_id
1808:     elif terminal.ok:
1809:         storage_run_id = terminal.run_id
1810:         storage_operation = None
```

`snippet_matches_file`: CONFIRMED  
`inference_holds`: HOLDS  
El cliente existente no rota y conserva el registro previo (`tools/dayz_mcp/process_lifecycle.py:2957`, `:3732-3734`); un stop exitoso devuelve su mismo identificador (`tools/dayz_mcp/dayz_test_worker.py:746-749`).  
La proyección consulta ese registro u observación persistida y devuelve `True` con su backup (`tools/dayz_mcp/dayz_test_tool.py:1967-1984`, `:1941`), publicados en `:1548-1551`.