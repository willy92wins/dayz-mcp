**D1**

`tools/dayz_mcp/dayz_test_storage.py:493-500`
```text
493:     if not storage_present and backup_present:
494:         # The case the design names: killed between the rename and the seal.
495:         # The journal's new_seal is the authority; the old marker is not.
496:         # The engine will create the new tree on the next start.
497:         backup, marker_backup = _finish_rotation(mission, journal_path, document)
498:         return RotationResult(
499:             launch_allowed=True,
500:             storage_rotated=True,
```

`tools/dayz_mcp/dayz_test_storage.py:458-461`
```text
458:     _publish_marker(mission, str(document["new_seal"]), str(document["project"]))
459:     _advance_phase(journal_path, document, PHASE_MARKER_PUBLISHED)
460:     _complete_journal(mission, str(document["txid"]))
461:     return str(document["storage_backup"]), published_marker_backup
```

`snippet_matches_file`: CONFIRMED  
`inference_holds`: HOLDS  
La rama completa no compara `document["new_seal"]` con `seal` (`tools/dayz_mcp/dayz_test_storage.py:493-508`).  
Publica el sello del journal mediante `_publish_marker`, que lo escribe en el marcador de la misión (`tools/dayz_mcp/dayz_test_storage.py:458`, `:339-342`).

---

**D2**

`tools/dayz_mcp/dayz_test_storage.py:639-644`
```text
639:     if active:
640:         recovered = _reconcile_journal(
641:             mission, active[0][len(JOURNAL_PREFIX) : -len(JOURNAL_SUFFIX)], seal
642:         )
643:         if recovered is not None:
644:             return recovered
```

`snippet_matches_file`: CONFIRMED  
`inference_holds`: HOLDS  
El retorno evita la lectura del marcador y la clasificación con el sello actual, situadas después (`tools/dayz_mcp/dayz_test_storage.py:649-653`).

---

**D3**

`tools/dayz_mcp/dayz_test_storage.py:222-223`
```text
222:     if marker.seal == seal:
223:         return Decision(DECISION_REUSE, "seal_matches")
```

`tools/dayz_mcp/process_lifecycle.py:3024-3031`
```text
3024:         self._record_storage_rotation(provisional, result)
3025:         if not result.launch_allowed:
3026:             return "storage_recovery_required"
3027:         # A replay of a completed journal is the same reset, not a second one.
3028:         # The run record carries it; another audit row would claim a new move.
3029:         if result.storage_rotated and result.reason != "pending_completed_rotation":
3030:             self._audit_storage_rotation(run_id, result)
3031:         return None
```

`tools/dayz_mcp/process_lifecycle.py:3979-3983`
```text
3979:                     launched = self.launcher(
3980:                         list(parsed["argv"]),
3981:                         str(parsed["cwd"]),
3982:                         str(parsed["window_style"]),
3983:                     )
```

`snippet_matches_file`: CONFIRMED  
`inference_holds`: HOLDS  
La recuperación publica A y permite B sin reclasificar (`tools/dayz_mcp/dayz_test_storage.py:458`, `:493-508`, `:639-644`).  
El lifecycle admite ese resultado y lanza los argumentos originales de B (`tools/dayz_mcp/process_lifecycle.py:3024-3031`, `:3979-3983`).  
Cuando B haya creado el almacenamiento descrito, el journal completado se omite y A obtiene `reuse` por su marcador válido (`tools/dayz_mcp/dayz_test_storage.py:529-530`, `:308-327`, `:649-653`, `:222-223`).

---

**D4**

`tools/dayz_mcp/dayz_test_tool.py:1982-1984`
```text
1982:     if len(found) != 1 or not _operation_matches(found[0], operation_id):
1983:         return None, None, None
1984:     return _decode_storage_observation(found[0])
```

`tools/dayz_mcp/dayz_test_tool.py:1911-1917`
```text
1911: def _operation_matches(row: dict[str, object], operation_id: str | None) -> bool:
1912:     """When both sides name an operation, they must be the same attempt."""
1913:     if operation_id is None:
1914:         return True
1915:     if "launch_operation_id" not in row or row.get("launch_operation_id") is None:
1916:         return True
1917:     return row.get("launch_operation_id") == operation_id
```

`tools/dayz_mcp/process_lifecycle.py:1819-1840`
```text
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

`snippet_matches_file`: CONFIRMED  
`inference_holds`: HOLDS  
Las entradas generadas omiten `launch_operation_id`; la lectura persistente exige exactamente esas cuatro claves (`tools/dayz_mcp/process_lifecycle.py:1826-1831`, `:235-242`).  
El estado las expone sin añadir el identificador (`tools/dayz_mcp/process_lifecycle.py:1842-1844`, `:7278-7280`), por lo que la llamada devuelve `True` mediante `tools/dayz_mcp/dayz_test_tool.py:1913-1916`.

---

**D5**

`tools/dayz_mcp/dayz_test_storage.py:406-409`
```text
406:         or not isinstance(document.get("storage_backup"), str)
407:         or not document["storage_backup"]
408:         or not isinstance(document.get("marker_backup"), str)
409:         or not document["marker_backup"]
```

`tools/dayz_mcp/dayz_test_storage.py:445-446`
```text
445:     marker_backup = str(document["marker_backup"])
446:     marker_backup_path = ntpath.join(mission, marker_backup)
```

`tools/dayz_mcp/dayz_test_storage.py:453-458`
```text
453:     if os.path.exists(marker_backup_path):
454:         published_marker_backup = marker_backup
455:     elif os.path.exists(marker_path):
456:         _rename_strict(marker_path, marker_backup_path)
457:         published_marker_backup = marker_backup
458:     _publish_marker(mission, str(document["new_seal"]), str(document["project"]))
```

`snippet_matches_file`: CONFIRMED  
`inference_holds`: HOLDS  
El validador completo no restringe separadores ni `..`; `storage_backup` también se une directamente a la misión (`tools/dayz_mcp/dayz_test_storage.py:385-417`, `:476-477`).  
Con `marker_backup = "..\\x.json"`, marcador existente y destino ausente, el rename apunta al directorio padre; `_rename_strict` únicamente comprueba que el destino no exista (`tools/dayz_mcp/dayz_test_storage.py:445-456`, `:301-305`).