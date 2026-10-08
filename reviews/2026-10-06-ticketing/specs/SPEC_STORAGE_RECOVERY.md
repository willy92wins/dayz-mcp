## ITEM storage-recovery

**Decision**

[DESIGN] Un único lote corrige D1–D5 en los tres módulos indicados y añade pruebas. Recuperar la transacción antigua no autoriza lanzar su modset: antes del spawn, el marcador debe corresponder al sello completo de la llamada actual.

No borrar mundos, backups ni journals. Conservar el journal terminado mediante rename. Ante incertidumbre, rechazar con `storage_recovery_required` y razón exacta. Esta entrega es solo lectura; no se han ejecutado pruebas ni modificado archivos.

**Verified anchors**

Rutas relativas a este árbol: `S=tools/dayz_mcp/dayz_test_storage.py`, `P=tools/dayz_mcp/process_lifecycle.py`, `T=tools/dayz_mcp/dayz_test_tool.py`, `R=tools/dayz_mcp/runtime_state.py`. Fragmentos abiertos y comprobados:

- S:102–112: `class RotationResult:`; `storage_rotated: bool`; `reason: str`.
- S:216–224: `return Decision(DECISION_SEAL_ONLY, "storage_absent")`; `if marker.seal == seal:`; `return Decision(DECISION_REUSE, "seal_matches")`.
- S:275–298: `handle.flush()`; `os.fsync(handle.fileno())`; `os.replace(temporary, path)`; `if handle.read() != payload:`.
- S:301–305: `if os.path.exists(destination):`; `os.rename(source, destination)`.
- S:406–409: `or not document["storage_backup"]`; `or not document["marker_backup"]`.
- S:453–461: `_rename_strict(marker_path, marker_backup_path)`; `_publish_marker(mission, str(document["new_seal"]), str(document["project"]))`; `_complete_journal(mission, str(document["txid"]))`.
- S:475–508: `backup_present = os.path.exists(backup_path)`; `return None`; `if not storage_present and backup_present:`; `storage_seal=str(document["new_seal"])[:8]`.
- S:576–580 y 639–675: `_rename_strict(ntpath.join(mission, STORAGE_NAME), ntpath.join(mission, backup))`; `return recovered`; `marker = read_marker(mission)`; `_publish_marker(mission, seal, project)`.
- P:197–203: `and value not in {".", ".."}`; `and not any(separator in value for separator in ("/", "\\", ":"))`.
- P:1819–1840: `"run_id": run.run_id`; `"storage_reset_notice": run.storage_reset_notice`; el diccionario persistido carece de `launch_operation_id`.
- T:1911–1917 y 1982–1984: `return row.get("launch_operation_id") == operation_id`; `return _decode_storage_observation(found[0])`.
- P:2992–3030 y 3080–3090: `return "storage_recovery_required"`; `"lifecycle_storage_rotated"`; `storage_seal=getattr(result, "storage_seal", None)`.
- P:1787–1808 y 3909–3918: `payload["storage_observations"] = list(self._storage_observations)`; `atomic_write_bytes(self.paths.runs_path, raw)`; `self.manifest.replace(provisional)`.
- R:2295–2315: `written = os.write(descriptor, encoded[offset:])`; `os.fsync(descriptor)`; `os.replace(temporary, path)`.
- P:162–193: `or document.get("new_seal") != seal`; `reason="pending_completed_rotation"`.
- P:3881–3887: `"lifecycle_start_rejected"`; `run_id=run_id`; `stage="pre_launch"`.

**Required behavior**

[DESIGN] **D1–D3.** Sean A el `new_seal` del journal y X el sello actual:

1. Validar journal y estado físico antes de mutar. En recuperación, el backup del mundo debe ser un directorio; estados contradictorios rechazan con `journal_state_impossible`.
2. Con `storage_1` ausente y backup presente, terminar la transacción original: preservar el marcador anterior, publicar A con el proyecto del journal, avanzar fase y renombrar el journal a completado. No alterar retrospectivamente su `new_seal`.
3. Volver a comprobar ausencia de storage, leer marcador y clasificar para X. Si A=X, queda A. Si A≠X, publicar atómicamente X con el proyecto actual; no crear storage ni otra rotación. Confirmar mediante lectura que el marcador válido contiene X.
4. Solo entonces permitir spawn. No perder los datos de recuperación al obtener `seal_only`.

Resultado exitoso, en ambos casos:

| Campo | Valor |
|---|---|
| `launch_allowed` / `storage_recovery_required` | `true` / `false` |
| `storage_rotated` | `true` |
| `storage_backup` | Nombre del backup original |
| `storage_marker_backup` | Backup realmente existente, o `None` |
| `storage_reset_notice` | `mission_world_and_character_reset` |
| `storage_seal` / `decision` | `X[:8]` / `rotate` |
| `reason`, A=X | `recovered_storage_moved` |
| `reason`, A≠X | `recovered_storage_moved_resealed` |

Aplicar este resultado también al journal activo en fase `marker_published` cuando storage sigue ausente. Si storage ya existe, comprobar la coherencia del journal, completarlo y clasificar normalmente; nunca resellar encima de un mundo existente.

Errores recuperables de E/S rechazan antes del spawn: `recovery_finish_failed`, `recovery_seal_publish_failed` o `recovery_marker_mismatch`, según el paso. Usar resultado bloqueado; conservar todo lo ya publicado. Registrar rechazo con la razón exacta en `lifecycle_start_rejected`.

El audit exitoso `lifecycle_storage_rotated` lleva razón, decisión y backups del resultado, sello X[:8] y aviso. El run conserva `true`, backup y aviso, con su identidad de operación; no añadirle campos nuevos de sello/razón. Mantener la degradación existente si falla el audit.

Después de que el motor X cree storage: otro X clasifica `reuse`; cualquier sello distinto, incluido A cuando A≠X, clasifica `rotate`. Sin storage, cualquier sello clasifica `seal_only`.

[DESIGN] **D4.** El productor de observaciones copia `run.launch_operation_id` cuando sea conocido, sin inventarlo. Debe sobrevivir persistencia, recarga y poda del run. El lector compara las identidades conocidas: igualdad permite decodificar; desigualdad devuelve observación desconocida. Mantener los contratos de loader y nulabilidad del lote acompañante.

[DESIGN] **D5.** Validar ambos nombres antes de cualquier join basado en ellos. Regla exactamente equivalente al predicado citado: string no vacío, distinto de `.`/`..`, sin `/`, `\` ni `:`. Implementarla dentro del módulo sellado, sin importar lifecycle. Rechazo: `journal_unreadable`, sin mutaciones.

**Crash matrix**

[DESIGN] Cortar entre **cada** par de operaciones de E/S. X es el sello de la llamada interrumpida; Y≠X. `Rec(Z)` termina el journal y garantiza marcador Z; `Clas(Z)` reutiliza únicamente un mundo cuyo marcador coincide con Z, rotando los demás.

| Última publicación durable / disco | Próximo X | Próximo Y |
|---|---|---|
| Antes del journal; mundo y marcador antiguos | Clas(X) | Clas(Y) |
| Journal `prepared`; mundo aún original | Completar sin mover; Clas(X) | Completar sin mover; Clas(Y) |
| Mundo renombrado; fase todavía `prepared` | Rec(X) | Rec(Y) |
| Fase `storage_moved`; marcador antiguo | Rec(X) | Rec(Y) |
| Marcador antiguo renombrado; marcador ausente | Rec(X) | Rec(Y) |
| Marcador A publicado; fase anterior | Rec(X) | Rec(Y) |
| Fase `marker_published`; journal activo; sin storage | Rec(X) | Rec(Y) |
| Journal completado; marcador A; sin storage | Sellar X | Sellar Y |
| Resellado X; sin storage | Sellar X | Sellar Y |
| Audit emitido; manifiesto anterior | Igual; audit no autoriza spawn | Igual |
| Manifiesto sustituido, antes/después del checkpoint | Recuperar identidad persistida; aplicar filas anteriores | Igual |
| Motor X creó storage | Reuse | Rotate, preservar mundo X |

Para cada escritura atómica, subdividir en creación temporal, cada write, flush/fsync, cierre, rename/replace y lectura de comprobación. Antes del replace permanece el destino anterior; después, el nuevo. Los temporales no son evidencia de recuperación. Cortes entre lecturas no alteran disco. En manifiesto/checkpoint, aceptar estado anterior o nuevo completo, nunca atribución mezclada. Conservar el contrato acompañante de observación por llamada.

[VERIFY] Esta matriz cubre muerte del proceso; durabilidad ante corte eléctrico requiere comprobación específica del filesystem.

**Offline tests**

[DESIGN] Fixtures independientes; comprobar contenido por ruta y hash, no solo cantidades.

- **D3 end-to-end:** mundo inicial, rotación hacia A, interrupción inmediatamente después del rename; recuperar con B. Simular consumidor que escribe una carga identificable B. Lanzar A: debe rotar esa carga, nunca reutilizarla. Sin cambio falla el marcador B y la clasificación final.
- **Resultado y auditoría:** repetir recuperación A/A y A/B, incluyendo fase `marker_published`; comprobar todos los campos anteriores, audit y run pre-spawn. Sin cambio fallan sello/razón A/B y aviso en la rama ya publicada.
- **Todos los cortes:** recorrer la matriz con X e Y, reincidiendo durante recuperación y resellado; verificar conservación y marcador antes del consumidor. Sin cambio falla el corte D3 con distinto sello.
- **D4 durable:** persistir operación O1, podar run, recargar; O1 obtiene medición y O2 obtiene desconocido, tanto para `true` como `false`. Sin cambio falta identidad y O2 acepta la observación.
- **D5:** probar ambos campos con traversal, rutas absolutas, drive-relative, UNC, separadores, `.`/`..`, vacío y tipos incorrectos; snapshot interior/exterior intacto. Sin cambio el validator acepta nombres no planos.
- **Fallos de recuperación:** inyectar error en finish, resellado y lectura final; cero spawns y razones exactas. Sin cambio no existen estos rechazos específicos.

PASS exige todas las aserciones; fallo de fixture es INCONCLUSO. Revisar implementación con otra familia antes de release.

**In-game acceptance**

[VERIFY] En misión desechable, con lease y lifecycle guard: verificar bundle desplegado; realizar A→A→B→A, comprobar reuse/rotaciones, backups conservados, avisos, identidades y marcador antes del spawn. Confirmar readiness y revisar ambas firmas de poisoning citadas en S:73–75. Liberar lease y comprobar `session_status`. Sin fault injection, esto no acredita recuperación de crashes.

**Out of scope**

Cambios acompañantes de `_record_storage_rotation`, loader y nulabilidad; poda/restauración de mundos; migración de journals; Enforce/PBO; redefinir sellos; garantizar que la economía reconstruyó el mundo.