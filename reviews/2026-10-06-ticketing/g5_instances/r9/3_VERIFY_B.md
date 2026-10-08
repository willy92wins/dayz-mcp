### W1

`tools/dayz_mcp/server_cli.py:107-125`
```text
107: def shared_build_lock_paths(*parts: str, root: Path | None = None) -> list[Path]:
108:     """One lock file per normalized destructive resource, in acquire order."""
109:     identities = sorted(
110:         {os.path.normcase(os.path.normpath(part)) for part in parts if part}
111:     )
112:     base = (shared_root() if root is None else Path(root)) / "build-locks"
113:     return [
114:         base / (hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".lock")
115:         for identity in identities
116:     ]
117: 
118: 
119: def _lock_byte(descriptor: int) -> None:
120:     import msvcrt
121: 
122:     if os.fstat(descriptor).st_size == 0:
123:         os.write(descriptor, b"\0")
124:     os.lseek(descriptor, 0, os.SEEK_SET)
125:     msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
```

`tools/dayz_mcp/dayz_test_worker.py:806-807`
```text
806:         lock_root = _accredited_shared_root(payload)
807:         with shared_build_lock(target, temp, root=lock_root):
```

`snippet_matches_file`: **CONFIRMED**  
`inference_holds`: **UNDETERMINED**

La adquisición usa `LK_LOCK`; el contexto vuelve a lanzar cualquier excepción sin reintentar (`tools/dayz_mcp/server_cli.py:165-169`).  
La función del worker no traduce ese fallo; su siguiente `try` comienza después del build (`tools/dayz_mcp/dayz_test_worker.py:807-838`).  
El límite aproximado de diez segundos depende del runtime y no queda demostrado por el código abierto ni por una medición realizada.  
El ejecutable sí convierte la excepción genérica en un terminal `internal_failure` (`tools/native-launchers/dayz-test-v1/src/app_main.py:341-365`).

### W2

`tools/dayz_mcp/identity_migration.py:1576-1593`
```text
1576:         # Quiescence is a precondition for WRITING -- it exists so that
1577:         # nothing mutates runs while it is copied. Once the receipt is published
1578:         # and no transaction artifact remains there is nothing left to write, and
1579:         # demanding an empty machine buys no safety while making the daemon
1580:         # unbootable: the scan counts every `-m dayz_mcp` process of every open
1581:         # session as a blocker (:739-743), so the drain never converges and
1582:         # startup dies at its deadline (daemon.py:246-261). Deliberately narrow --
1583:         # anything that could still write falls through to the gate below.
1584:         settled = _settled_receipt(
1585:             source_path,
1586:             backup_path,
1587:             receipt_path,
1588:             pending_receipt_path,
1589:             marker_path,
1590:             next_path,
1591:         )
1592:         if settled is not None:
1593:             return settled
```

`snippet_matches_file`: **CONFIRMED**  
`inference_holds`: **HOLDS**

Un recibo válido sin artefactos pendientes permite retornar antes de `_assert_quiescent` (`tools/dayz_mcp/identity_migration.py:1372-1382`, `:1592-1601`).  
El escaneo se invoca dentro de ese gate omitido (`tools/dayz_mcp/identity_migration.py:1117-1127`); el arranque llama a la migración (`tools/dayz_mcp/daemon.py:257-264`, `:1619-1621`).  
La lease comprueba contención del byte bloqueado, sin escanear escritores que la omitan (`tools/dayz_mcp/instance_context.py:211-222`).  
La omisión es deliberada y protege la disponibilidad cuando la migración ya está terminada; este veredicto confirma el comportamiento descrito, sin atribuirle un bug.

### W3

`tools/install-mcp.ps1:54-58`
```text
54: foreach ($extra in @($args)) {
55:   $text = [string]$extra
56:   if ($text -match '(?i)instance' -or $text -match '(?i)game-?path' -or $text -match '(?i)gamepath') {
57:     Exit-DayZMcpSelector "unconsumed_selector_argument"
58:   }
```

`tools/install-mcp.ps1:71-80`
```text
71: if ($Instance -ne "") {
72:   $tokenMatches = [regex]::IsMatch(
73:     $Instance,
74:     '\A[a-z0-9](?:[a-z0-9-]{0,31})?\z',
75:     [System.Text.RegularExpressions.RegexOptions]::None
76:   )
77:   if (-not $tokenMatches -or $Instance.Contains('--') -or $Instance.StartsWith('-') -or $Instance.EndsWith('-') -or $Instance -ceq 'default') {
78:     Exit-DayZMcpSelector "invalid_instance_token"
79:   }
80: }
```

`snippet_matches_file`: **CONFIRMED**  
`inference_holds`: **DOES NOT HOLD**

`Instance` es un parámetro declarado de tipo string (`tools/install-mcp.ps1:25-42`, específicamente `:39`).  
Una función desechable con esa declaración rechazó `-Instance 130 -Instance 131` con `ParameterBindingException` / `ParameterAlreadyBound`, antes de ejecutar su cuerpo.  
El control con una sola aparición ejecutó el cuerpo con `130`; el último valor no prevalece.

### W4

`tools/dayz_mcp/server_cli.py:87-88`
```text
87:     if override:
88:         root = Path(override)
```

`tools/dayz_mcp/server_cli.py:107-112`
```text
107: def shared_build_lock_paths(*parts: str, root: Path | None = None) -> list[Path]:
108:     """One lock file per normalized destructive resource, in acquire order."""
109:     identities = sorted(
110:         {os.path.normcase(os.path.normpath(part)) for part in parts if part}
111:     )
112:     base = (shared_root() if root is None else Path(root)) / "build-locks"
```

`tools/dayz_mcp/dayz_test_worker.py:806-807`
```text
806:         lock_root = _accredited_shared_root(payload)
807:         with shared_build_lock(target, temp, root=lock_root):
```

`snippet_matches_file`: **CONFIRMED**  
`inference_holds`: **DOES NOT HOLD**

El productor incluye siempre `shared_lock_root`, aplicando `normpath` a `shared_root()` (`tools/dayz_mcp/dayz_test_tool.py:460`).  
Una raíz relativa queda rechazada por el parser con `shared_lock_root_invalid`, antes del build (`tools/dayz_mcp/dayz_test_request.py:385-392`).  
El worker también exige que la raíz presente sea absoluta y normalizada (`tools/dayz_mcp/dayz_test_worker.py:292-303`).  
La derivación relativa al cwd existe en el fallback cuando falta el campo (`tools/dayz_mcp/dayz_test_worker.py:290-291`, `tools/dayz_mcp/server_cli.py:112`), pero no demuestra el comportamiento afirmado para el flujo actual.