## ITEM 855c

**Corrección vinculante: no reintentar un `start` que ya respondió `storage_recovery_required`.** El worker sustituye la respuesta que contiene la pareja por el rechazo del segundo intento. Después, la tool recupera solamente el código desde `last_start_error`, lo que explica el resultado medido.

**Base y alcance.** Árbol exportado `C:\Users\guill\dzmcp_gauntlet\base_v11f`, identificado por el encargo como main `46833d7`; sin `.git` para verificar HEAD. Cambio funcional limitado a `tools/dayz_mcp/dayz_test_worker.py`, más pruebas y lock regenerado. Sin cambios Enforce, PBO, formato de storage ni contrato público.

Se comprobó por lectura host que `process_lifecycle.py`, `loopback.py` y `dayz_test_tool.py` coinciden con la copia LIVE tras normalizar CRLF. El bundle LIVE `tools/native-launchers/dayz-test-v1/app.pyz`, SHA256 `8B932082A1664EF7734A499ABD39BA43BB4FF7A03EE9F64FD7D4E83A82D9A43C`, contiene el mismo worker y `app_main.py` que este export.

**Mecanismo y recorrido verificados.** Todos los extractos siguientes son [EXACT]; las rutas relativas parten de la base anterior.

El productor devuelve la pareja. `tools/dayz_mcp/process_lifecycle.py:4289`:

```python
4289:                         if storage_error == "storage_recovery_required":
4290:                             recovery_reason = self._last_storage_recovery_reason
4291:                             if recovery_reason is not None:
4292:                                 settled["storage_recovery_reason"] = recovery_reason
4293:                                 settled["storage_recovery_hint"] = (
4294:                                     dayz_test_storage.storage_recovery_hint(
4295:                                         recovery_reason
4296:                                     )
4297:                                 )
4298:                         return settled
```

La pareja atraviesa estas capas sin una allowlist que la elimine:

| Capa | Fuente y líneas pegadas [EXACT] |
|---|---|
| Host lifecycle | `tools/dayz_mcp/loopback.py:4819`: `result = lifecycle.start_run(client, token, request)` |
| Preparación HTTP | `tools/dayz_mcp/loopback.py:4857`: `result = dict(result)`; `:4858`: `status = int(result.pop("_http_status", 200))`; `:4872`: `self._json(status, self._with_renewed_lease(result))` |
| Serialización | `tools/dayz_mcp/loopback.py:4456`: `data = json.dumps(body, separators=(",", ":")).encode("utf-8")` |
| Transporte acreditado | `tools/dayz_mcp/accredited_daemon_transport.py:328`: `return int(response.status), response_body` |
| Hijo lifecycle sellado | `tools/native-launchers/dayz-test-v1/src/app_main.py:334`: `result = json.loads(response_body.decode("utf-8") or "{}")`; `:337`: `sys.stdout.buffer.write(_canonical(result))` |
| Relay nativo | `tools/native-launchers/dayz-test-v1/src/launcher.cpp:1425`: `const BYTE* response = child_ok ? child_result.bytes : failure;` |
| Broker Python | `tools/native-launchers/dayz-test-v1/src/app_main.py:157`: `return value`; `tools/dayz_mcp/dayz_test_worker.py:826`: `return result` |

El punto de pérdida es `tools/dayz_mcp/dayz_test_worker.py:1028`:

```python
1028:         if operation_id is not None and role == "server" and not _successful_run(
1029:             result, target_run_id, "RUNNING"
1030:         ) and _lifecycle_rejection(result) not in STEAM_PREPARATION_REJECTION_CODES:
1031:             result = await invoke_start()
```

El primer intento quedó asentado como `EXITED`, sin propietario. El segundo usa el mismo `new_run_id`; `tools/dayz_mcp/process_lifecycle.py:3963`:

```python
3963:                         or existing.state != "RUNNING"
3964:                     ):
3965:                         return self._start_rejection(
3966:                             client, authority, "launch_identity_conflict"
3967:                         )
```

La auditoría real `C:\Users\guill\AppData\Local\DayZ_MCP\audit\events.jsonl:12611` confirma la secuencia. Extractos de campos:

```text
12611: lifecycle_storage_recovery_required / journal_name_invalid / rejected
12612: lifecycle_start_outcome / storage_recovery_required / EXITED
12614: lifecycle_start_rejected / launch_identity_conflict / rejected
12618: lifecycle_stop_outcome / stopped / EXITED
```

`launch_identity_conflict` no pertenece a `LIFECYCLE_REJECTION_CODES`; el worker termina como `worker_failed`, sin pareja. Finalmente, `tools/dayz_mcp/dayz_test_tool.py:1949` rescata solamente el código:

```python
1949:             reason = failed_status.get("last_start_error")
1950:             if type(reason) is str and reason:
1951:                 terminal = replace(terminal, error_code=reason)
```

Por eso el resultado público vuelve a decir `storage_recovery_required`, pero conserva ambos diagnósticos nulos.

Las capas posteriores ya conservan una pareja válida: worker `:1051`, `:1057-1058` y cleanup `:252-253`; `app_main.py:367-368` y `:83-85`; parser `dayz_test_tool.py:780`, `:792-796`. El builder aplica la guía en `dayz_test_tool.py:1663`:

```python
1663:     if remediation is None and recovery_hint is not None:
1664:         remediation = recovery_hint
```

**Cambio mínimo [EXACT].** Sustituir exclusivamente la expresión de `dayz_test_worker.py:1030` por:

```python
        ) and _lifecycle_rejection(result) not in (
            STEAM_PREPARATION_REJECTION_CODES | {"storage_recovery_required"}
        ):
```

Así se consume la primera negativa, incluso cuando procede de un productor legacy sin diagnósticos. Se conserva el cleanup existente y el comportamiento de replay para los demás casos. No reconstruir la pareja desde auditoría ni desde `last_start_error`, que es una observación global.

Mantener #222:

- Transporte y terminal: pareja completa o ausente.
- Razón del vocabulario cerrado y hint canónico.
- Pareja malformada en respuesta lifecycle: colapso legacy sin diagnósticos; terminal malformado: `terminal_invalid`.
- Resultado público: `storage_recovery_reason` y guía canónica en `remediation`; sin nuevo campo público `storage_recovery_hint`.
- Cleanup confirmado: `run_id=null`; cleanup degradado conserva su identidad y estado actuales.

**Empaquetado.** Sí requiere lock y reseal. `tools/packaged-modules.lock.json:12` contiene:

```json
"tools/dayz_mcp/dayz_test_worker.py": "C04F755C1C0E53537ED4FFB00DC032B669AFB7D0E9A1951221E9AEFD2E092D80",
```

Regenerar con `tools/write_packaged_modules_lock.py`, comprobar `--check` y construir/resellar el launcher para la instancia de validación. La promoción compartida queda en el procedimiento del integrador.

**Prueba obligatoria [DESIGN], atravesando los joints reales.** Añadir un gate Windows que invoque la tool pública por un cliente MCP real, con daemon acreditado, host lifecycle, launcher registrado, `app.pyz`, pipes, worker, terminal y builder reales. No sustituir `Broker.invoke`, `_lifecycle_main`, transporte HTTP, `start_run`, `_worker_main` ni terminal. El test actual de `test_storage_rotation_model.py:2225-2226` devuelve la misma respuesta ficticia en ambos `start`; por ello oculta precisamente este defecto.

En una instancia exclusiva, con lease y misión de prueba admitida por policy:

1. Inventariar storage y comprobar ausencia de conflicto de nombres; plantar `STORAGE_1.modset.rotation.0123456789abcdef0123456789abcdef.json`.
2. Invocar `dayz_test_run(project="DayZ_MCP", mode="all")`.
3. Exigir `failed`, `storage_recovery_required`, `journal_name_invalid`, `remediation` literal canónico y `run_id=null` con cleanup confirmado.
4. Exigir un solo `start` de esa operación, ningún spawn DayZ y ausencia de `launch_identity_conflict`; verificar inventario de storage intacto.
5. Retirar únicamente el archivo plantado; liberar lease y comprobar `session_status`.

**Mutaciones que deben volverlo rojo:** restablecer la condición original de replay; y, por separado, suprimir las dos asignaciones de la pareja en `process_lifecycle.py:4292-4297`. Ejecutar cada mutante con artefactos válidamente construidos/acreditados: el rojo debe proceder de las aserciones funcionales, no de un seal inválido.

Añadir negativos de pareja ausente, incompleta, razón desconocida y guía incorrecta, y conservar pruebas de replay por respuesta perdida. **PASS** exige el gate real y ambos mutantes rojos; **INCONCLUSO** si no se atraviesa el launcher sellado. Esta sesión solo verificó fuentes y evidencia existente.

## ITEM 2986

**Corrección vinculante: detectar sesiones mediante marcadores técnicos del RPT y aceptar la finalización correlacionada de `[Disconnect]`, conservando la vía legacy inglesa y `[Logout]`.** `logoutTime 0` no autoriza a omitir al jugador ni confirma que haya terminado.

**Base y alcance.** `C:\Users\guill\dzmcp_gauntlet\base_v11f`, export identificado como main `46833d7`. Cambio limitado a los lectores de jugadores/logout de `tools/dayz_mcp/dayz_test_tool.py` y sus pruebas. Sin cambios de protocolo público, Enforce ni cierre de procesos.

**Evidencia [EXACT].** El detector actual depende del idioma. `tools/dayz_mcp/dayz_test_tool.py:3311`:

```python
3311: _PLAYER_CONNECTED_RE = re.compile(
3312:     br'Player "[^"\n]+" \([^)\n]*\) is connected'
3313: )
3314: _LOGOUT_FINISHED_RE = re.compile(br"\[Logout\]: Player \S+ finished\b")
3315: _PLAYER_CONNECTED_NAME_RE = re.compile(
3316:     br'Player "([^"\n]+)" \([^)\n]*\) is connected'
3317: )
```

La espera sale inmediatamente si no encontró filas. `tools/dayz_mcp/dayz_test_tool.py:4010`:

```python
4010:     rows = watch.rows()
4011:     if not rows or not wait:
4012:         return rows, 0.0, False
```

Se leyeron los RPT originales desde host. Los identificadores persistentes siguientes están enmascarados.

`C:\temp\DayZ_MCP_130_dev\_server\profiles-130\DayZDiag_x64_2026-10-08_15-36-52.RPT:558`:

```text
558: 15:37:59.763 <CREATE NEW CHAR>:
559:     charID 0
560:     playerID 0
561:     dpnid 15690878
562:     uid <hash>
564: 15:39:09.681 [Disconnect]: Start script disconnect 15690878 (dbCharacterId 0 dbPlayerId 0) logoutTime 0
565: 15:39:09.681 [Disconnect]: Finish script disconnect 15690878 (<hash>)
566: 15:39:09.681 [Disconnect]: DisconnectPlayerFinish 15690878
```

En 1.29, `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\_server\profiles\DayZDiag_x64_2026-10-08_15-41-24.RPT:1338`:

```text
1338: 15:42:24.956 Player "Dev" (steamID=<uid> pos=<12842.5, 8573.6, 6.3>) is connected
1339: 15:42:24.959 <CREATE NEW CHAR>:
1612: 15:57:41.812 [Disconnect]: Start script disconnect 12941960 (dbCharacterId 1 dbPlayerId 1) logoutTime 15
1616: 15:57:56.813 [Disconnect]: Finish script disconnect -1 (<hash>)
```

La variante de personaje existente está observada en el mismo directorio, `DayZDiag_x64_2026-10-01_01-15-49.RPT:895`:

```text
895:  1:16:56.66  <LOAD EXISTING CHAR>:
896:     charID 1
897:     playerID 1
898:     dpnid 12843847
899:     uid <hash>
```

Estos bloques aparecen después de las líneas de conexión en las muestras leídas. Su uso como señal neutral es una decisión [DESIGN]; no se presupone que entrar en `AuthPlayerLoginState` o en cola confirme una conexión.

**Detección y correlación [DESIGN].**

1. Leer eventos completos en orden de offset. `[Login]: Adding player NAME (DPNID) to login queue…` abre un intento; `[StateMachine]: Player NAME (dpnid DPNID uid UID)…` vincula nombre, identificador de conexión y UID. Admitir UID vacío durante autenticación sin inventar identidad.
2. Confirmar la sesión mediante un bloque completo `<CREATE NEW CHAR>:` o `<LOAD EXISTING CHAR>:` con `dpnid` y UID no vacío, coherentes con ese intento. No exigir `charID`/`playerID` positivos: en 1.30 ambos son cero en la evidencia.
3. Conservar la frase inglesa como fallback. Si también existe el bloque neutral, fusionar los dos eventos de la misma sesión; no duplicar `Dev` en 1.29.
4. Mantener internamente identidad y época de conexión, no solo nombre. Un nuevo login/reconnect invalida la finalización anterior; dos jugadores homónimos no comparten completion. Publicar únicamente `{player, logout_finished}`.
5. Retener líneas y bloques parciales entre lecturas. Un bloque incompleto o correlación contradictoria no confirma una sesión; si impide determinar miembros durante el cierre, producir timeout/aviso conservador, no una falsa lista vacía verificada.

Usar los mismos reconocedores neutrales en `_LogoutWatch` y `_scan_player_markers`, conservando la política existente de avisos para cierres de un solo rol. Mantener selección de logs actuales, perfiles con nombre, límites de lectura y protección frente a sustitución/truncado.

**Finalización [DESIGN].** Añadir `Finish script disconnect DPNID (UID)` como completion, correlacionada con la sesión vigente. Para `DPNID=-1`, usar UID y un `Start script disconnect` de esa sesión; nunca exigir igualdad del número final, pues rompería la muestra 1.29. Un DPNID contradictorio o UID ajeno no completa otra sesión.

Conservar `[Logout]: Player UID finished` como vía legacy. `early disconnect`, `disconnecting`, `Start script disconnect`, `Remove player info` y `Player destroy` no bastan. Tampoco aceptar `DisconnectPlayerFinish` aislado sin correlación suficiente.

Para un jugador pendiente al comenzar el cierre, la completion debe ser posterior al límite capturado **antes** de cerrar el cliente y a su conexión vigente. La protección actual está en `tools/dayz_mcp/dayz_test_tool.py:3971` [EXACT]:

```python
3971:             if token not in uids or not event.after_boundary:
3972:                 continue
3973:             if self._later(event, last):
3974:                 return True
```

Una desconexión completa inequívoca anterior al límite retira esa sesión del conjunto activo; una completion histórica nunca salva un reconnect.

`logoutTime 0` elimina cualquier espera artificial de quince segundos: seguir sondeando hasta recibir la completion correspondiente o agotar el presupuesto. No marcar `true` al leer `Start`. Conservar `min(_LOGOUT_WAIT_S, timeout_s)`, orden cliente→espera→servidor y deadline independiente de terminación. `logout_finished=true` acredita finalización observada; no prueba por sí solo persistencia durable del personaje.

**Fixtures y pruebas [DESIGN].** Guardar fixtures sanitizadas de ambas muestras con UID sintético consistente, preservando estructura, idioma, espacios y números. Incluir bloques completos; nunca sembrar directamente estructuras del watcher.

| Prueba con archivos reales | Resultado exigido | Mutación que debe volverla roja |
|---|---|---|
| 1.30: bloque neutral, conexión española; completion añadida después del límite, `logoutTime 0` | `Dev/true`; servidor cerrado después de completion | Desactivar reconocimiento neutral |
| 1.29: inglés + bloque neutral; `Finish … -1 (UID)` sin `[Logout]` | Una fila `Dev/true`, espera hasta Finish | Exigir DPNID final positivo/idéntico |
| Login/Auth sin bloque ni conexión inglesa | No conexión confirmada; incertidumbre conservadora cuando corresponda | Tratar cola/Auth como confirmación |
| Start con tiempo cero y Finish retrasado | `false` antes de Finish | Completar al leer `logoutTime 0` |
| Dos jugadores, finalización escalonada | Esperar a ambos | Terminar al primer Finish |
| Reconnect y UID ajeno/histórico | No completar sesión nueva | Eliminar comprobación de UID/época/límite |
| Línea/bloque partido entre chunk y polls | Mismo resultado que lectura continua | Eliminar carry de líneas/bloques |

Ejecutar las pruebas del consumidor público `dayz_test_close`, sin sustituir lectores, watcher ni espera; comprobar filas **y orden temporal del cierre**. Conservar las regresiones existentes de `test_fb_8604_orderly_close.py` y `test_g5fix_named_profiles.py`, incluida la vía `[Logout]`.

**Empaquetado y aceptación.** `dayz_test_tool.py` no figura en `tools/packaged-modules.lock.json` ni en `PACKAGED_MODULES` (`tools/build_native_launcher.py:57-79`): este cambio no exige regenerar lock ni reseal.

**PASS** requiere fixtures/mutantes y smoke público real en 1.29 y 1.30, con lease, lifecycle guard, liberación y `session_status`. Verificar también personaje existente en 1.30; esa variante no está demostrada por la muestra suministrada. **INCONCLUSO** si falta ese recorrido. Esta sesión fue de solo lectura; no ejecutó cierres ni aplicó cambios.

