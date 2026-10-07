**V1**

`tools/dayz_mcp/host_config.py:1102-1119`
```text
1102: def _matches_in_place_overwrite(
1103:     current: bytes,
1104:     desired: bytes,
1105:     source: bytes,
1106: ) -> bool:
1107:     shared = min(len(desired), len(source))
1108:     if len(current) == len(source):
1109:         prefix = 0
1110:         while prefix < shared and current[prefix] == desired[prefix]:
1111:             prefix += 1
1112:         suffix_start = len(source)
1113:         while suffix_start > 0 and current[suffix_start - 1] == source[suffix_start - 1]:
1114:             suffix_start -= 1
1115:         return suffix_start <= prefix
1116:     return (
1117:         len(desired) > len(source)
1118:         and len(source) < len(current) <= len(desired)
1119:         and current == desired[: len(current)]
```

`tools/dayz_mcp/host_config.py:1183-1189`
```text
1183:                 for role in decoded
1184:             ):
1185:                 raise HostConfigError("registration_recovery_conflict")
1186:         else:
1187:             classes = {
1188:                 role: _classify(currents[role], decoded[role][2], decoded[role][3])
1189:                 for role in decoded
```

`snippet_matches_file`: **CONFIRMED**  
`inference_holds`: **HOLDS**

La prueba en memoria con ambos originales `b"abc"` y fuentes `b"abcdef"` devuelve `registration_recovery_conflict`; `_is_restore_progress` delega sin aceptar previamente el original completo (`tools/dayz_mcp/host_config.py:1127-1128`).  
La raíz predeterminada es común a las instancias (`tools/dayz_mcp/host_config.py:911-915`, `:1226`).  
En ese estado, el rechazo precede a la limpieza y las llamadas posteriores repiten la recuperación; las limpiezas de otros estados no eliminan este journal conflictivo (`tools/dayz_mcp/host_config.py:1172-1185`, `:1209`, `:1227-1232`).

---

**V2**

`tools/dayz_mcp/host_config.py:993-1004`
```text
993: def _load_manifest(journal: Path) -> dict[str, object]:
994:     try:
995:         raw = _manifest_path(journal).read_bytes()
996:         value = json.loads(raw)
997:     except (OSError, json.JSONDecodeError):
998:         raise HostConfigError("registration_journal_invalid") from None
999:     if (
1000:         not isinstance(value, dict)
1001:         or value.get("schema") != _JOURNAL_SCHEMA
1002:         or value.get("status")
1003:         not in {"prepared", "writing", "committed", "restoring_original", "restored"}
1004:         or not isinstance(value.get("files"), dict)
```

`tools/dayz_mcp/host_config.py:963-968`
```text
963: def _persist_manifest(journal: Path, manifest: dict[str, object]) -> None:
964:     payload = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
965:     temporary = journal / "manifest.next"
966:     _write_private(temporary, payload)
967:     os.replace(temporary, _manifest_path(journal))
968: 
```

`snippet_matches_file`: **CONFIRMED**  
`inference_holds`: **HOLDS**

En la primera publicación, el journal se crea antes del manifiesto y los archivos host se escriben después (`tools/dayz_mcp/host_config.py:1259-1269`).  
Una terminación entre `:966` y `:967` deja `manifest.next`; `_manifest_path` selecciona exclusivamente `manifest.json` (`tools/dayz_mcp/host_config.py:959-960`).  
La recuperación carga ese archivo antes de cualquier limpieza, por lo que los intentos posteriores vuelven a rechazar el journal; reproducido en memoria (`tools/dayz_mcp/host_config.py:1148-1150`, `:1227-1232`).

---

**V3**

`tools/install_mcp.py:1185-1210`
```text
1185:     if dropped and not allow_option_removal:
1186:         detail = ";".join(f"{role}:{','.join(flags)}" for role, flags in sorted(dropped.items()))
1187:         raise InstallerContractError(
1188:             "registration_would_drop_options",
1189:             f"{detail} (re-run with --allow-option-removal to drop them)",
1190:         )
1191:     try:
1192:         for role in roles:
1193:             if previous[role] is not None:
1194:                 provider.remove(role)
1195:                 touched.add(role)
1196:         for role in roles:
1197:             provider.add(role, desired[role])
1198:             touched.add(role)
1199:         for role in roles:
1200:             if provider.get(role) != desired[role]:
1201:                 raise RegistrationTransactionError("registration_verify_mismatch")
1202:         if host_configs is not None:
1203:             timeout_kwargs = {} if server_name == "dayz-mcp" else {"server_name": server_name}
1204:             apply_host_timeouts(*host_configs, **timeout_kwargs)
1205:             for role in roles:
1206:                 if provider.get(role) != desired[role]:
1207:                     raise RegistrationTransactionError("registration_verify_mismatch")
1208:     except Exception as error:
1209:         try:
1210:             _rollback_registrations(provider, previous, touched)
```

`tools/install-mcp.ps1:892-897`
```text
892:     & $CodexCmd mcp add dayz-mcp -- $VenvPython @codexArgs
893:   } else {
894:     & $CodexCmd mcp add $ServerName -- $VenvPython @codexArgs
895:   }
896:   if ($LASTEXITCODE -ne 0) {
897:     throw "codex mcp add dayz-mcp failed (exit $LASTEXITCODE). Stopped without a Codex remove."
```

`snippet_matches_file`: **CONFIRMED**  
`inference_holds`: **HOLDS**

Los registros anteriores y los roles modificados son variables locales; el rollback consume únicamente esas estructuras desde `except Exception` (`tools/install_mcp.py:1169-1175`, `:1115-1132`, `:1208-1213`).  
El journal de timeouts comienza después de los `provider.add`, por lo que no guarda los registros anteriores a esas altas (`tools/install_mcp.py:1196-1204`, `tools/dayz_mcp/host_config.py:1235-1238`).  
PowerShell añade Claude antes de Codex y el fallo de Codex termina con `throw`, sin retirada compensatoria de Claude (`tools/install-mcp.ps1:875-881`, `:891-897`).

---

**V4**

`tools/dayz_mcp/daemon.py:1430-1440`
```text
1430:                 sink = JsonlAuditWriter(
1431:                     RuntimePaths.for_token(getattr(config, "instance_token", None)),
1432:                     daemon_generation,
1433:                 )
1434:             return sink.write(event) is True
1435:         except Exception as error:
1436:             log(f"DAEMON: audit event {name!r} not recorded: {type(error).__name__}")
1437:             return False
1438: 
1439:     if budget_s is None:
1440:         return _write()
```

`snippet_matches_file`: **CONFIRMED**  
`inference_holds`: **HOLDS**

`record_daemon_event` no recibe ni define `config`; tampoco existe una definición global de ese nombre en el módulo (`tools/dayz_mcp/daemon.py:1402-1409`, `:1424-1433`).  
Con `writer=None` y una generación presente, la prueba en memoria produce `NameError: name 'config' is not defined`, sin construir el escritor.  
El manejador captura esa excepción, registra el diagnóstico y devuelve `False` antes de escribir la fila (`tools/dayz_mcp/daemon.py:1434-1437`).

---

**V5**

`tools/dayz_mcp/daemon.py:343-352`
```text
343: def _audit_path(config: Any) -> Path:
344:     configured = getattr(config, "exec_audit_path", None)
345:     if configured:
346:         return Path(configured)
347:     token = getattr(config, "instance_token", None)
348:     audit_dir = Path(__file__).resolve().parents[1] / "_audit"
349:     if not token:
350:         return audit_dir / "exec_enforce.jsonl"
351:     return audit_dir / f"exec_enforce-{token}.jsonl"
352: 
```

`tools/dayz_mcp/server.py:858-859`
```text
858:             return Path(self.config.exec_audit_path)
859:         return Path(__file__).resolve().parents[1] / "_audit" / "exec_enforce.jsonl"
```

`snippet_matches_file`: **CONFIRMED**  
`inference_holds`: **HOLDS**

Sin `exec_audit_path` explícito, el token `130` produce `exec_enforce-130.jsonl` en el daemon y `exec_enforce.jsonl` en `Runtime`; comprobado en memoria.  
Ambas rutas alimentan escritores reales: `tools/dayz_mcp/daemon.py:366-387`, `tools/dayz_mcp/core.py:302-317` y `tools/dayz_mcp/server.py:675`, `:842-854`.  
El segundo camino corresponde al runtime embedded; el cliente proxy utiliza `ClientRuntime` (`tools/dayz_mcp/server.py:3344`).

---

**V6**

`tools/dayz_mcp/instance_context.py:155-159`
```text
155:     handle = kernel.CreateFileW(
156:         str(path),
157:         0x80000000 | 0x40000000,
158:         0x1 | 0x2 | 0x4,
159:         None,
```

`tools/dayz_mcp/instance_context.py:188`
```text
188:         self._owner_id = id(self if owner is None else owner)
```

`tools/dayz_mcp/instance_context.py:209`
```text
209:         # FILE_SHARE_DELETE lets a test remove the temp root while the
```

`snippet_matches_file`: **CONFIRMED**  
`inference_holds`: **UNDETERMINED**

El código habilita compartir eliminación y usa `id()` para identificar al propietario (`tools/dayz_mcp/instance_context.py:158`, `:188`).  
La exclusión utiliza un bloqueo de byte sobre el descriptor abierto y registros locales al proceso (`tools/dayz_mcp/instance_context.py:170-173`, `:211-219`, `:235-237`).  
Eso no demuestra que Windows permita borrar y recrear ese nombre mientras permanece abierto el handle; no se ejecutó un experimento de filesystem que confirme esa parte de la inferencia.

---

**V7**

`tools/dayz_mcp/server_cli.py:83-88`
```text
83:     override = os.environ.get("DAYZ_MCP_SHARED_ROOT", "").strip()
84:     local = os.environ.get("LOCALAPPDATA", "").strip()
85:     real = Path(local) / "DayZ_MCP_shared" if local else None
86:     isolated = _invocation_is_test()
87:     if override:
88:         root = Path(override)
```

`tools/dayz_mcp/box_admission.py:27-28`
```text
27:     root.mkdir(parents=True, exist_ok=True)
28:     return root / "box-admission.lock"
```

`snippet_matches_file`: **CONFIRMED**  
`inference_holds`: **DOES NOT HOLD**

La raíz relativa sí se devuelve sin convertirla en absoluta y divide las rutas de box-admission según el cwd (`tools/dayz_mcp/server_cli.py:88`, `:104`, `tools/dayz_mcp/box_admission.py:23-28`).  
Pero el flujo de build publica esa raíz en la solicitud y exige que sea absoluta antes de adquirir locks (`tools/dayz_mcp/dayz_test_tool.py:460`, `tools/dayz_mcp/dayz_test_worker.py:284-303`, `:806-807`).  
Con una raíz relativa, esa validación defensiva devuelve `request_integrity_failed`, reproducido en memoria: ese flujo no adquiere dos locks de build distintos.