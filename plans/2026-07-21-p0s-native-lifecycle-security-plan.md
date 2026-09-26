# P0.S — Lifecycle, control y auditoría nativos sin shell

> **Estado:** plan endurecido tras Grill B; no autoriza implementación ni gates con procesos hasta revisión independiente final.  
> **Owner:** `P:\DayZ_MCP_dev`.  
> **Consumidor:** `P:\Utopia_PC_Suite\plans\2026-07-22-phase-0a-foundation-plan.md` P0.S-A/P0.S-F.  
> **Research:** `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-07-21-powershell-free-process-guard-codex.md`.

## 1. Objetivo y traza DPF

Eliminar del runtime productivo y de los tests activos todos los lanzamientos de
PowerShell, `cmd`, `.ps1`, WMI/WMIC y cualquier `shell=True`, preservando seguridad
fail-closed, ownership, lease, lifecycle y evidencia durable.

La fase traza a:

- **H6** — sólo runs registrados; revalidación PID + creation time + fingerprints;
  PID reutilizado, foreign, legacy e identidad incompleta quedan intactos:
  `P:\DayZ_MCP_dev\product-spec.md:132`.
- **H7** — auditoría sin secretos, doctor y cierre verificable:
  `P:\DayZ_MCP_dev\product-spec.md:133`.
- **F2/F5** — daemon lazy/reutilizable; un daemon sano nunca es reclamado:
  `P:\DayZ_MCP_dev\product-spec.md:97,100`.
- **E2/F3** — seguridad endurecida y fail-closed del split:
  `P:\DayZ_MCP_dev\product-spec.md:82,98`.
- **H10** — todo control HTTP autenticado acredita el owner exacto del socket antes de
  transmitir key/identidad/lease; foreign/rebind falla con cero bytes:
  `P:\DayZ_MCP_dev\product-spec.md:136`.
- **E3** — instalación y arranque con un comando documentado:
  `P:\DayZ_MCP_dev\product-spec.md:83`.

El intent preservado es un end-goal local usable con seguridad en producción, no
un mero cambio de herramienta: `P:\DayZ_MCP_dev\product-spec.md:75-77`.

## 2. Autoridad verificada y restricciones

- `ProcessRecord` tiene hoy cinco campos y carga por posición:
  `P:\DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py:32-67`.
- Lifecycle exige snapshot completo tras launch y revalida antes de stop:
  `P:\DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py:747-785,890-905`.
- La interfaz del guard consumida es `snapshot`, `terminate` y `discover`:
  `P:\DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py:324-339`.
- El runtime actual lanza PowerShell desde el guard y lo instala en daemon/doctor:
  `P:\DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py:288-339`,
  `P:\DayZ_MCP_dev\tools\dayz_mcp\daemon.py:140-164` y
  `P:\DayZ_MCP_dev\tools\dayz_mcp\doctor.py:143-157`.
- `orphan_guard.command_line_of` aún usa WMIC y PowerShell:
  `P:\DayZ_MCP_dev\tools\dayz_mcp\orphan_guard.py:373-439`.
- Las rutas HTTP canónicas son session y lifecycle:
  `P:\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:52-65`.
- `ClientRuntime` ya implementa la máquina de estado session pero importa el stack
  MCP: `P:\DayZ_MCP_dev\tools\dayz_mcp\server.py:251-418`.

Restricciones absolutas:

1. Ningún proceso se lanza con shell; todo launch usa argv list y `shell=False`.
2. Ningún gate cambia exclusiones o protecciones Defender.
3. Ningún kill por nombre, path parcial, mod, puerto aislado o parent aislado.
4. `NoSuchProcess` no se convierte en éxito de terminación; identidad incompleta,
   `AccessDenied`, drift y PID reuse conservan el proceso.
5. No se guarda key, token, command line, argv completo ni secreto en receipts/logs.
6. P0.S no modifica PBO, Enforce, misión Utopia ni assets DayZ.

## 3. Archivos previstos

### Producción

- Modify `P:\DayZ_MCP_dev\tools\requirements-mcp.txt`.
- Add `P:\DayZ_MCP_dev\tools\vendor\psutil\psutil-7.2.2-cp37-abi3-win_amd64.whl`.
- Add `P:\DayZ_MCP_dev\tools\vendor\psutil\SHA256SUMS.json`.
- Add `P:\DayZ_MCP_dev\tools\vendor\psutil\LICENSE`.
- Add `P:\DayZ_MCP_dev\tools\dayz_mcp\native_process_guard.py`.
- Modify `P:\DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py`.
- Modify `P:\DayZ_MCP_dev\tools\dayz_mcp\orphan_guard.py`.
- Modify `P:\DayZ_MCP_dev\tools\dayz_mcp\daemon.py`.
- Modify `P:\DayZ_MCP_dev\tools\dayz_mcp\doctor.py`.
- Add `P:\DayZ_MCP_dev\tools\dayz_mcp\control_client.py`.
- Modify `P:\DayZ_MCP_dev\tools\dayz_mcp\server.py`.
- Add `P:\DayZ_MCP_dev\tools\dayz_mcp\security_runtime_audit.py`.
- Add `P:\DayZ_MCP_dev\tools\p0s_daemon_bootstrap.py`.
- Add `P:\DayZ_MCP_dev\tools\p0s_gate.py`.
- Add `P:\DayZ_MCP_dev\tools\p0s_test_runner.py` como runner deny-launch
  obligatorio de P0.S.
- Add `P:\DayZ_MCP_dev\tools\install_mcp.py` como sustituto nativo del instalador
  productivo `install-mcp.ps1`.
- Modify `P:\DayZ_MCP_dev\tools\README-mcp.md` para documentar `install_mcp.py`,
  conservar el arranque normal/lazy documentado y reservar el bootstrap estricto para
  campañas Utopia.

### Tests y evidencia

- Add `tools\tests\test_native_process_guard.py`.
- Modify `tools\tests\test_process_lifecycle.py`.
- Modify `tools\tests\test_doctor.py`.
- Modify `tools\tests\test_orphan_guard.py`.
- Modify `tools\tests\test_daemon.py`.
- Add `tools\tests\test_control_client.py`.
- Modify `tools\tests\test_client_mode.py`.
- Add `tools\tests\test_security_runtime_audit.py`.
- Add `tools\tests\test_p0s_daemon_bootstrap.py`.
- Add `tools\tests\test_install_mcp.py`.
- Add `tools\tests\test_p0s_test_runner.py`.
- Modify `tools\tests\test_task9_launcher_migration.py`.
- Replace `tools\_session_coordination\process_guard_gate.py` con su versión nativa.
- Add create-only outputs bajo `P:\DayZ_MCP_dev\reports\security\`.
  Incluye `installer-cli-manifest-v1.json` y
  `installer-not-found-fixtures-v1.json`, congelados antes de cualquier registro.

`tools\process-guard.ps1`, `tools\spike0\mcp-grab.ps1` y launchers legacy pueden
permanecer como referencia estática versionada; ningún import, test dinámico ni entrypoint
activo puede ejecutarlos.

### 3.1 Contrato de instalación nativa

El instalador nuevo conserva, sin ampliar, el contrato productivo verificado del actual
`install-mcp.ps1`: parámetros `port`, `keyfile`, `server_profiles`, `client_profiles`,
`mission_path`, `expected_game_version`, `idle_timeout_seconds`, `allow_legacy` y
`register`; crea/reutiliza `tools\.venv-mcp`; instala requirements y el paquete editable;
crea/reutiliza el keyfile; escribe los tres `dayz_mcp.json` de muestra y los destinos
opcionales; construye registros CLIENT distintos para Claude/Codex y, sólo con `--register`,
reemplaza/verifica el registro efectivo. La autoridad actual está en
`P:\DayZ_MCP_dev\tools\install-mcp.ps1:1-18,319-360,363-439`.

**[DESIGN cerrado]** `install_mcp.py` usa únicamente stdlib hasta crear el venv y lanza
cada proceso mediante argv list con `subprocess.run(..., shell=False)`; todo comando que
debe triunfar exige `returncode==0`. Sólo el probe previo de registro admite nonzero, y
únicamente si coincide con el fixture local verificado de `not found`; cualquier otro
nonzero es exit 2. No
genera ni ejecuta shell text. El Python base es exclusivamente el `sys.executable` que ya
está ejecutando el instalador, resuelto con strict/canonical path antes del primer child;
no existe opción `--python`, variable ni config para sustituirlo. El venv child debe
resolver exactamente bajo `tools\.venv-mcp\Scripts\python.exe`; cualquier Python,
PowerShell/pwsh/cmd, PE renombrado o path/symlink distinto se rechaza antes de launch. El
nuevo `psutil` security-critical se instala
desde su wheel vendorizado exacto. `mcp==1.27.2` y `Pillow==12.2.0`, ya fijados en
`P:\DayZ_MCP_dev\tools\requirements-mcp.txt:1-2`, pueden resolverse por red únicamente durante la operación explícita
de instalación E3; no se ejecuta `pip --upgrade`, no se acepta versión distinta y falta de
red/dependencia produce exit 2. Después se congela el árbol completo `.venv-mcp` y ningún
gate permite acceso de red ni nueva resolución. El modo por defecto imprime comandos redactados y
no muta registros; `--register` es la única autorización de mutación externa. La
verificación parsea salidas estructuradas cuando el CLI lo permita y nunca imprime la key.

Los CLIs de registro tampoco se resuelven por PATH/nombre. `p0s_gate.py` crea antes un
`installer-cli-manifest-v1` create-only con keyset exacto
`schema_version=1,kind="dayz-mcp-installer-clis-v1",entries`; entries contiene exactamente
roles `CLAUDE|CODEX`, cada uno con `path,bytes,sha256`, path absoluto resuelto a un `.exe`
nativo regular/no-reparse y SHA uppercase. Se inventarían hashes si se hardcodearan entre
versiones: cada ejecución congela el host actual, lo revisa y el build manifest enlaza ese
manifest. `install_mcp.py --register` revalida bytes/hash inmediatamente antes de cada
child y usa ese path absoluto como argv[0]. Prohibidos PATH lookup, `.cmd`, `.bat`, `.ps1`,
wrapper, extensión distinta, hash drift o role extra; fallan antes de launch. El inventario
host 2026-07-22 verificó que existen `claude.exe` y `codex.exe` nativos, mientras PATH
también expone wrappers que no se portan.

`[DESIGN cerrado]` El subcomando productor es
`p0s_gate.py freeze-installer-clis --claude-exe <ABS> --codex-exe <ABS>`; no acepta
nombres ni resuelve PATH. Además de PE x64 regular/no-reparse y Authenticode válido con
revocación sólo desde caché, exige procedencia host por rol: Claude exactamente bajo
`%USERPROFILE%\.local\bin\claude.exe`; Codex exactamente bajo el paquete npm nativo
`%APPDATA%\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe`.
Una instalación futura con otra procedencia exige revisar el plan; no se relaja por
fallback. El productor publica create-only, relee con el mismo consumidor del instalador
y verifica bytes/hash antes de declarar el manifest congelado.

El primer inventario WindowsApps queda preservado como evidencia rechazada: el smoke
2026-07-22 devolvió `WinError 5` al crear ese child desde el host. No autoriza fallback a
alias, `.cmd` o `.ps1`; el manifest canónico usa exclusivamente el PE npm firmado y
accesible.

`[DESIGN cerrado]` `p0s_gate.py probe-installer-not-found` se ejecuta sólo después del
manifest CLI y usa sus paths revalidados para consultar el nombre fijo inexistente
`p0s-absent-fixture-do-not-create`. Publica create-only returncode/stdout/stderr acotados
por rol, SHA del manifest y SHA del ejecutable; exige nonzero en ambos y no contiene key,
argv de daemon ni entorno. `install_mcp.py` sólo acepta un probe nonzero si coincide byte a
byte con este receipt y sus vínculos siguen vigentes; cualquier otra salida es exit 2.

`--register` primero consulta Claude y Codex y conserva en memoria sus registros efectivos
anteriores (o ABSENT), con secretos redactados en cualquier log. Sólo ejecuta remove si el
registro existe. Después hace add+get+comparación exacta en ambas plataformas. Si remove,
add o verify falla en cualquiera, restaura ambos snapshots anteriores: remove del registro
nuevo si procede y add exacto del anterior; sólo una verificación del estado restaurado
termina el rollback. Fallo de rollback es exit 2 con instrucciones sanitizadas y nunca
declara E3 PASS. Tests obligatorios: fresh install ABSENT, existentes correctos/stale,
not-found, remove failure, add failure Claude/Codex, verify mismatch y rollback parcial/
completo; además arg desconocido `--python` y fixtures powershell/pwsh/cmd/PE renombrado/
symlink, CLI por nombre/PATH, `.cmd/.bat/.ps1`, manifest extra/missing y hash/path drift
fallan con intercept count cero. Ningún test invoca CLIs reales salvo el host-path smoke
autorizado con ambos `.exe` revalidados.
Exit codes: `0` éxito, `1` contrato/registro refutado y `2` entorno incompleto o dependencia
externa indisponible. El `.ps1` queda referencia estática no invocable y README no lo ofrece
como camino activo.

## 4. Dependencia reproducible

### 4.1 Pin

**[EXACT; metadatos oficiales verificados 2026-07-21]**

- paquete: `psutil==7.2.2`;
- wheel: `psutil-7.2.2-cp37-abi3-win_amd64.whl`;
- bytes: `137737`;
- SHA-256: `eb7e81434c8d223ec4a219b5fc1c47d0417b12be7ea866e24fb5ad6e84b3d988`;
- Python requerido: `>=3.6`.

La adquisición es una tarea explícita y separada. Verifica URL HTTPS oficial/PyPI,
tamaño y hash antes de crear el fichero vendor. La instalación de **psutil** es offline,
desde ese wheel exacto, sobre `tools\.venv-mcp`; queda prohibido resolver su última
versión durante implementación o gate.

El manifest P0.S incluye wheel, licencia, requirements, `psutil.__version__`, ruta real
del módulo cargado y árbol completo de `.venv-mcp`. Drift en cualquiera invalida P0.S.

## 5. Formato persistente e identidad v2

No existe alternativa sin cambio de formato que preserve H6: psutil devuelve argv
estructurado y el guard legacy hashea una command line WMI renderizada. Pretender que
ambas representaciones son iguales produciría falsos matches o falsos negativos. Por eso
se hace un cambio **aditivo y versionado**.

### 5.1 `ProcessRecord`

**[DESIGN]** Añadir al final:

```text
identity_scheme: str = "legacy-wmi-v1"
```

`from_payload` usa `legacy-wmi-v1` sólo si el campo está ausente. `validate` admite
exactamente `legacy-wmi-v1` y `psutil-argv-v2`; cualquier otro valor es corrupción y
rechaza el manifest. La posición final conserva los constructores existentes durante la
migración, pero todo constructor de producción nuevo debe pasar el campo por nombre.

### 5.2 Canonicalización nueva

**[DESIGN]** Para `psutil-argv-v2`:

- `creation_time_utc`: `datetime.fromtimestamp(create_time, UTC)` con
  `timespec="microseconds"` y sufijo `Z`.
- `executable_sha256`: SHA-256 de bytes UTF-8
  `b"psutil-exe-v2\0" + normalized_absolute_exe.casefold().encode("utf-8")`, donde
  `[DESIGN cerrado]` se valida `ntpath.isabs(exe)` y
  `normalized_absolute_exe = ntpath.normpath(exe)`. No se consulta ni resuelve el
  filesystem al canonicalizar: la identidad usa exactamente el path absoluto observado
  por psutil y no depende del cwd, existencia posterior o resolución de symlinks.
- `command_line_sha256`: SHA-256 de
  `b"psutil-argv-v2\0" + json.dumps(argv, ensure_ascii=False,
  separators=(",", ":")).encode("utf-8")`.

Se hashea identidad, no contenido del `.exe`; el nombre histórico del campo se conserva
por compatibilidad. No se reconstruye una command line con join/quoting.

### 5.3 Legacy, rollback y backup

- La fuente real se resuelve sólo mediante `RuntimePaths.from_env().runs_path`, hoy
  `%LOCALAPPDATA%\DayZ_MCP\runs.json`:
  `P:\DayZ_MCP_dev\tools\dayz_mcp\runtime_state.py:25-39`. Antes de instanciar
  `RunManifestStore` o arrancar el primer daemon remediado, `[EXACT]`
  `p0s_gate.py backup-runs-v1 --port <PORT>`
  exige secuencia exclusiva, ningún listener en el puerto aprobado y cero proceso Python
  cuyo argv nativo identifique `dayz_mcp`; `AccessDenied` o identidad parcial = STOP.
- El destino fijo es create-only
  `P:\DayZ_MCP_dev\reports\security\migration\P0S-IDENTITY-V2\runs.pre-v2.json` y el
  receipt es `runs-backup-receipt.json` en el mismo directorio. Se leen bytes crudos, se
  calcula SHA-256, se publica con `O_CREAT|O_EXCL` + flush/fsync y se re-hashea fuente y
  destino. Un segundo scan nativo debe seguir sin procesos DayZ_MCP y el hash fuente no
  puede derivar; cualquier carrera aborta antes de crear `RunManifestStore`. Si la fuente
  no existe, no se fabrica backup: el receipt create-only registra `source_absent=true`.
  Backup/receipt existentes nunca se reemplazan ni se regeneran automáticamente.
- No es un paso procedural opcional: un guard one-shot compartido corre antes de todo
  `RunManifestStore` real y antes de bind tanto en daemon normal como bootstrap. Bajo lock
  exclusivo permite como máximo el PID de startup actual, todavía sin listener/store,
  exige identidad argv/exe v2 exacta y rechaza cualquier otro `dayz_mcp`, listener, PID
  parcial o `AccessDenied`. Crea/verifica backup+receipt o valida el receipt existente;
  sólo después se puede construir el store. `install_mcp.py --register` ejecuta el mismo
  guard antes de modificar ambos registros y hace rollback de registros si falla. El
  daemon directo/lazy no puede eludirlo: receipt ausente/inválido aborta antes de bind.
- Datos pre-cambio cargan como `legacy-wmi-v1`. El loader no los reescribe. En la primera
  recuperación/operación bajo el `RunManifestStore._lock` ya existente
  (`P:\DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py:136-172`), todo run activo que
  los contenga debe auditar y transicionar durablemente a `UNRECONCILED` antes de
  responder; el runtime
  nuevo nunca los termina ni adopta automáticamente. Si audit o write falla, bloquea la
  operación y no toca el proceso. Doctor emite `legacy_process_identity_scheme` y exige
  reconciliación manual exacta.
- Datos post-cambio escriben siempre `psutil-argv-v2`.
- Rollback sólo está permitido cuando no hay runs activos con v2 y `session_status`
  confirma cierre limpio. El binario viejo ignoraría el campo aditivo, pero no se usa
  esa propiedad como seguridad.
- Históricos EXITED pueden conservar v2; antes de rollback se prueba que el lector viejo
  los carga. Si no, se restaura el backup completo, nunca se reescribe parcialmente.
- No hay migración destructiva in-place de records legacy.

## 6. `NativeProcessGuard`

### 6.1 API

**[DESIGN]** `native_process_guard.py` expone:

```text
class NativeProcessGuard:
    snapshot(pid: int) -> dict[str, object]
    terminate(expected: ProcessRecord) -> dict[str, object]
    discover(root_pid: int, allowlisted_names: set[str]) -> dict[str, object]
```

El type hint de `ProcessRecord` se importa sólo bajo `TYPE_CHECKING`; en runtime el guard
accede estructuralmente a los campos. `process_lifecycle.py` no se importa desde el guard,
evitando un ciclo guard↔lifecycle.

El shape de respuesta conserva `exit_code`, `identity_complete`, `error`, `pid`,
`creation_time_utc`, `executable_sha256`, `command_line_sha256`, `terminated` y
`processes`; añade `identity_scheme="psutil-argv-v2"`.

### 6.2 Snapshot

1. Construir un único `psutil.Process(pid)`.
2. Bajo `oneshot()`, leer `create_time`, `exe` y `cmdline`.
3. Rechazar exe vacío, argv vacío, path no absoluto, datos no-string o PID distinto.
4. Canonicalizar y devolver identidad completa.
5. `NoSuchProcess` → `process_not_found`, exit 4; `AccessDenied`, API inconsistente o
   dato parcial → `identity_unavailable`, exit 3. Nunca fallback.

### 6.3 Terminate

1. Rechazar cualquier scheme distinto de `psutil-argv-v2` antes de abrir autoridad de
   terminación.
2. Construir un `psutil.Process` y obtener el snapshot completo con ese mismo objeto.
3. Comparar exactamente los cuatro campos observables de identidad (`pid`,
   `creation_time_utc`, `executable_sha256`, `command_line_sha256`) + scheme. `role` es
   metadata de autorización: debe ser string no vacío y pertenecer a la allowlist del
   caller, pero no se inventa ni se compara contra el snapshot del SO. El caller valida
   role contra su allowlist inmutable antes del guard; role vacío/ajeno o expected omitido
   falla antes de construir `psutil.Process`. Esto conserva el callsite actual
   `terminate(record)` en
   `P:\DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py:327-330`; ProcessRecord ya
   contiene role en `P:\DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py:32-50`.
4. Sólo con match exacto llamar `process.kill()` y `process.wait(timeout=5.0)` sobre ese
   objeto. psutil revalida PID+creation time para métodos destructivos.
5. Timeout, AccessDenied, PID reuse, drift o lectura parcial → no confirmar terminación;
   devolver error fail-closed y conservar el record.

### 6.4 Discover

Enumerar descendientes desde el objeto root, breadth-first. Para cada candidato, exigir
nombre case-insensitive en allowlist y snapshot v2 completo. Desaparición se omite con
evento acotado; AccessDenied o identidad parcial de un candidato allowlisted hace fallar
toda la operación. Nunca se descubre por nombre global.

## 7. Integración lifecycle, doctor y reclaim

1. `ProcessLifecycle._record_from_snapshot` exige `identity_scheme` y crea sólo v2.
2. Daemon y doctor instalan `NativeProcessGuard`; se elimina todo import productivo de
   `PowerShellProcessGuard`.
3. `PowerShellProcessGuard` se retira del módulo productivo o se mueve a fixture
   histórico que el auditor no permite importar.
4. `orphan_guard` sustituye `command_line_of` por `command_argv_of(pid) -> list[str] | None`
   basado en psutil. El classifier distingue exactamente: `legacy_embedded` (`-m`,
   `dayz_mcp`, sin `--daemon`), `normal_daemon` (mismo módulo + token `--daemon`) y
   `p0s_bootstrap_daemon` (Python venv + `-I -B` + path absoluto
   `p0s_daemon_bootstrap.py` + `--security-manifest <ABS_BUILD_MANIFEST>` + subcomando
   `daemon` + `--port <N>` + `--keyfile <ABS>` + `--idle-timeout <VALUE>`). No se
   reconstruye/parsea una string: se comparan listas y posiciones/valores normalizados
   contra la política ya verificada. WMIC/PowerShell dejan de existir en rutas ejecutables.
5. Se reutilizan Toolhelp y `QueryFullProcessImageNameW` ya presentes sólo para
   enumeración/path cuando no conceden autoridad de kill:
   `P:\DayZ_MCP_dev\tools\dayz_mcp\orphan_guard.py:163-298`.

### 7.1 Rol dedicated de prueba registrado

Fase 0B exige terminación controlada de `DayZServer_x64.exe`; el protocolo vigente lo
declara `managed_lifecycle=false` en
`C:\Users\guill\ObsidianVault\AI\20_Runbooks\dayz-mcp-agent-session-protocol.md:13-19`,
y la allowlist actual sólo acepta `DayZDiag_x64.exe` en
`P:\DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py:479-491`. Por tanto, ningún plan
Utopia puede fingir esa capacidad ni matar el proceso por fuera del lifecycle.

**[DESIGN cerrado]** P0.S añade un rol explícito `dedicated_test` que admite únicamente
el ejecutable canónico `<game_path>\DayZServer_x64.exe` iniciado por el propio lifecycle.
La request exige `role="dedicated_test"`, `window_style="hidden"`, executable exacto y
absoluto, profile/mission/config bajo la raíz create-only del attempt, PBO/mod hashes ya
congelados por PreparedCampaignManifest Utopia y ausencia de flags retail/client. El parser rechaza
duplicados case-insensitive, path relativo, root escape, profile compartido y cualquier
ejecutable ya vivo que no pertenezca al `run_id` registrado. Los descendientes admitidos
se limitan a la allowlist explícita del rol y todos requieren snapshot v2 completo.

Start registra snapshot v2 antes de devolver éxito; stop/terminate requiere lease,
`run_id` exacto, rol exacto y revalidación completa del mismo record. PID aislado, nombre,
mod, puerto, parent o un dedicated iniciado externamente nunca conceden ownership. Un
fallo de stop queda `UNRECONCILED`, conserva el proceso y bloquea la campaña. Los tests
usan ejecutables fake bajo roots temporales para todos los rechazos; el único gate con el
binario real es el smoke autorizado y luego la campaña 0B. Tras implementación verde se
actualiza el runbook para `managed_lifecycle=true` sólo en este rol registrado; antes de
ese cambio 0B permanece INCOMPLETE.

### BUG-044 / F5

Antes de cualquier reclaim:

- una respuesta HTTP cualquiera del listener demuestra que está responsivo y lo
  preserva, aunque la key no coincida;
- una respuesta `/status` autenticada sana lo preserva;
- `normal_daemon` y `p0s_bootstrap_daemon` con parent muerto son topologías válidas, no
  orphans por ancestry;
- `try_reclaim_port` (camino embedded) devuelve siempre false ante cualquiera de esos dos
  tipos daemon; sólo puede recuperar un `legacy_embedded` sin `--daemon`, con
  parent/ancestor muerto e identidad v2 estable;
- `try_reclaim_unresponsive_listener` puede recuperar `normal_daemon` o
  `p0s_bootstrap_daemon` sólo tras exactamente dos probes no responsivos separados 500 ms,
  ownership del mismo PID sobre el puerto antes/después, imagen Python y dos snapshots v2
  A/B idénticos en los cuatro campos observables + scheme. La identidad esperada nunca se
  obtiene del listener no responsivo: el reclaiming process deriva exe+argv canónicos de
  su política local verificada. Para normal usa
  `build_daemon_argv(config, python=<APPROVED_VENV_PYTHON>)`; para Utopia usa manifest,
  bootstrap y policy ya verificados. Exe hash y command-line hash observados deben
  coincidir exactamente. El kill usa el snapshot v2 B completo mediante
   `_record_from_snapshot(snapshot_b, "daemon")`, verificado en
   `P:\DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py:838-853`; exige resultado no-null,
   valida role contra allowlist local exacta `{"daemon"}` y llama
   `NativeProcessGuard.terminate(expected_b)`. Nunca se llama con sólo role/PID ni se usa
   `kill_pid` directo;
- cualquier ambigüedad conserva el listener y devuelve E4 ocupado.

Tests obligatorios: daemon sano con key correcta, daemon sano con key distinta,
normal `--daemon` con parent muerto, bootstrap daemon con parent muerto,
embedded orphan confirmado, manifest/path/port/keyfile/timeout con un token distinto,
normal policy port/keyfile/version/timeout/exec drift, foreign listener, PID reuse o argv
drift, y callsite reclaim real con expected completo; expected omitido, conversión null,
role vacío/ajeno o cualquiera de los cuatro campos alterado conserva child,
drift entre snapshots y proceso que vuelve a responder
en el segundo probe.

## 8. Cliente de control stdlib-only

Extraer de `ClientRuntime` sólo coordinación session; no importar `server`, FastMCP,
captura ni daemon. El transporte acreditado se reutiliza como componente único de bajo
nivel; no se duplica con `urllib` ni se relaja para conservar la etiqueta stdlib-only.
La policy daemon esperada se inyecta desde configuración/manifest local verificado y no
se aprende del listener.

Para el daemon normal, `host_config.py` resuelve la provenance por consenso read-only de
las dos entradas host canónicas existentes. Cero/una config o cualquier drift de command,
args, port, keyfile o policy falla antes de conectar; no hay fallback a defaults ni nuevo
formato persistente. El bootstrap Utopia conserva policy separada ligada a su manifest.
Launcher y native image son campos distintos: la argv usa el launcher canónico y la
acreditación del owner usa exclusivamente la imagen OS local. Ambas configs se fijan por
handle y se revalidan juntas; schema/argv duplicado, defaults implícitos, symlink/reparse,
keyfile path drift o identidad incompleta fallan antes de leer el secreto o conectar. El
auditor H10 recorre la clausura productiva, no una lista manual de cuatro ficheros.

**[DESIGN; contrato exacto]**

- `ControlClientError(RuntimeError)` con `code: str`.
- `ControlIdentity(frozen=True)` con
  `platform,pid,ppid,started_at_utc,session_id,task_label` y `to_payload()`.
- `ControlClient(*, port: int, keyfile: Path, identity: ControlIdentity,
  timeout_s: float = 5.0)`; host fijo `127.0.0.1`, key leída una vez.
- Métodos async `session_acquire`, `session_wait`, `session_cancel`,
  `session_acquire_wait`, `session_heartbeat`, `session_release`, `session_status`,
  todos `-> dict[str, object]`.
- Estado lease/ticket protegido por lock y mismas reglas de stale clear de
  `server.py:299-415`.

`session_acquire_wait` extrae la semántica ya corregida de claim-on-live-wait:
operation id anterior al primer I/O, enqueue-always-ticket, slices HTTP de hasta
30 s, progreso y cancel-operation tombstone-first en toda salida no activa. La
espera sigue ligada a una request/host vivos; no añade un job durable.

`ClientRuntime` compone `ControlClient`. Un adapter dentro de `server.py` convierte
`ControlClientError.code` a `ToolError`; ninguna otra capa conoce `ToolError`. Tests de
paridad comparan request payload, paths, timeouts, códigos y transiciones byte-for-byte.

La paridad incluye también el orden de seguridad: `connect` → acreditar owner/identidad
del socket exacto → transmitir request. Payload, path, timeout y códigos siguen
byte-for-byte; una respuesta 2xx foreign no puede alcanzar el decoder.

`lifecycle_cli.py` sigue siendo el único cliente lifecycle del child; su superficie
real es `start|stop|adopt|reap|status` y exige identidad+lease por entorno:
`P:\DayZ_MCP_dev\tools\dayz_mcp\lifecycle_cli.py:38-85`.

## 9. Build identity y arranque seguro del daemon

Al cerrar S2A, después de implementar y pasar los gates offline de capture, se publica
create-only
`P:\DayZ_MCP_dev\reports\security\p0s-build-manifest.json`. Contiene clausura
productiva —incluido capture nativo—, wheel/venv, auditor y tests offline, más un
`daemon_policy` exacto. El
SHA-256 de sus bytes es `security_build_id`. El `p0s-test-manifest.json` final se publica
después de S4 y referencia ese build manifest; nunca se usa su propio hash como build id.

### 9.1 Schema y bytes canónicos

`p0s_gate.py` produce y verifica ambos manifests. El objeto raíz tiene exactamente las
claves `schema_version`, `kind`, `roots`, `entries`, `metadata`; no se admiten extras:

- `schema_version` es el entero `1`;
- `kind` es exactamente `dayz-mcp-p0s-build-v1` o `dayz-mcp-p0s-test-v1`;
- cada root tiene exactamente `{role,path}` y cada entry
  `{role,path,bytes,sha256}`; roles/paths son absolutos, únicos case-insensitive, cada
  entry queda bajo un root, bytes es entero no negativo y SHA-256 son 64 hex uppercase;
- build `metadata` tiene exactamente
  `{security_policy_version,python,dependencies,daemon_policy,offline_gates,reference_only_ps1}`.
  `python` tiene `{executable,version,isolated_flags}`; cada dependency
  `{name,version,distribution_path,distribution_sha256}`; `daemon_policy`
  `{bootstrap_path,security_manifest_path,mode,port,keyfile_path_sha256,idle_timeout_s,argv_sha256}`;
  cada gate `{name,status,receipt_path,receipt_sha256}` y cada referencia
  `{path,sha256}`. `mode="daemon"`, flags `[-I,-B]` y gates `S1/S2A=PASS` son obligatorios;
- test `metadata` tiene exactamente `{build,gates,reviews,reference_only_ps1}`. `build`
  tiene `{path,bytes,sha256,security_build_id}`; cada gate
  `{name,status,receipt_path,receipt_sha256}` y debe enumerar
  S1/S2A/S2B/S3/S4 PASS; cada review
  `{reviewer,status,artifact_path,artifact_sha256}`. `build.sha256` y
  `build.security_build_id` deben ser iguales al SHA-256 de los bytes del build manifest;
  todos los paths referenciados también aparecen como entries del test manifest.

Todo objeto/lista anidado usa ese keyset exacto y orden determinista por role/path/name.
Serialización única: `json.dumps(payload, ensure_ascii=False, sort_keys=True,
separators=(",", ":")) + "\n"`, UTF-8 sin BOM. `security_build_id` es SHA-256 uppercase
de esos bytes completos, incluido el LF final. Re-parse/re-serialize distinto, campo
extra/ausente, orden de lista distinto o drift de entry falla. El build manifest no se
auto-incluye; el test sí lo incluye como entry y vínculo. `manifest_audit.py` P0.1 valida
estos mismos dos schemas, no intenta reinterpretarlos como su schema Utopia.

### 9.2 Transporte interno del build ID Utopia

- El controller de campaña Utopia invoca `p0s_daemon_bootstrap.py` por path absoluto y le pasa el build
  manifest, keyfile y política mediante argv; el entorno mínimo no contiene secretos,
  `PYTHONPATH` ni un ID declarativo.
- El bootstrap es stdlib-only hasta terminar la verificación. Antes de modificar `sys.path` valida schema/paths,
  re-hashea toda la clausura y calcula el ID; missing/drift termina antes de importar
  `dayz_mcp` o escuchar. Expone sólo `daemon` y `probe --pid PID`. Después del gate
  añade únicamente `P:\DayZ_MCP_dev\tools`: `daemon` importa `NativeProcessGuard`, toma
  el snapshot v2 completo de su propio PID y llama `dayz_mcp.server.run` con argv daemon,
  el ID calculado y ese snapshot como autoridad esperada; `probe` devuelve sólo snapshot
  v2 JSON y nunca termina. La firma real previa es
  `run(argv: list[str] | None = None) -> int`:
  `P:\DayZ_MCP_dev\tools\dayz_mcp\server.py:1117`.
- **[DESIGN cerrado]** `ServerConfig` añade al final, sin flags públicos,
  `security_build_id: str | None = None` y
  `expected_daemon_identity: dict[str, object] | None = None`.
  `server.run` pasa a
  `run(argv: list[str] | None = None, *, security_build_id: str | None = None,
  expected_daemon_identity: dict[str, object] | None = None) -> int`; después de
  `parse_args`, exige ambos overrides internos Utopia o ninguno; cuando los recibe valida
  ID uppercase de 64 hex
  y snapshot v2 completo del PID actual y crea el config final con `dataclasses.replace`.
  Si llega sólo uno de los dos overrides falla antes de construir state o bind. No existe
  `--security-build-id`, variable de entorno ni fichero lateral que permita declararlo.
  Daemon normal `python -m dayz_mcp --daemon`, embedded y client conservan `None` y su
  comportamiento DPF actual.
- `daemon.make_status_provider` toma sólo `config.security_build_id`; `/status`
  autenticado publica `security_build_id` nullable, PID y modo daemon. El daemon Utopia
  publica exactamente el ID calculado; el normal publica `null`. El ID no se deriva de
  input HTTP ni se acepta desde CLI. `expected_daemon_identity` no se publica.
- El controller Utopia exige igualdad exacta de ID, PID, exe y argv v2. El cliente normal
  mantiene el criterio de salud actual y no exige build ID. Listener foreign, build
  distinto o status ambiguo no se mata ni se adopta.
- Arranque nuevo usa directamente el Python de `.venv-mcp`:

**[DESIGN; representación argv, no bloque ejecutable]**

```json
["P:\\DayZ_MCP_dev\\tools\\.venv-mcp\\Scripts\\python.exe", "-I", "-B",
 "P:\\DayZ_MCP_dev\\tools\\p0s_daemon_bootstrap.py", "--security-manifest",
 "P:\\DayZ_MCP_dev\\reports\\security\\p0s-build-manifest.json", "daemon", "--port", "8765",
 "--keyfile", "<ABS_KEYFILE_FROM_APPROVED_CONFIG>", "--idle-timeout", "1800.0"]
```

El directorio de trabajo es `P:\DayZ_MCP_dev\tools`; `shell=False`; stdin cerrado;
stdout/stderr van a ficheros create-only bajo `reports\security\daemon\<RunId>`.

### 9.3 Compatibilidad del lazy-spawn y camino estricto Utopia

La autoridad actual que debe migrarse es `daemon.build_daemon_argv` en
`P:\DayZ_MCP_dev\tools\dayz_mcp\daemon.py:366-384`, consumida sin adapter por
`ClientRuntime._default_spawn` en
`P:\DayZ_MCP_dev\tools\dayz_mcp\server.py:420-421`.

- **[EXACT de compatibilidad]** `build_daemon_argv(config, *, python=None)` conserva firma
  y contrato de daemon normal; hoy lo consume directamente `ClientRuntime._default_spawn`
  en `P:\DayZ_MCP_dev\tools\dayz_mcp\server.py:420-421`. Su única migración es retirar
  dependencias de PowerShell del lifecycle que alcanza. Sigue produciendo Python +
  `-m dayz_mcp --daemon` y forwardea port, keyfile, expected-game-version,
  require-version, idle-timeout, exec-enforce y exec-allowlist. No exige manifest Utopia.
- `ClientRuntime._default_spawn` sigue llamando `spawn_detached(build_daemon_argv(...))`;
  la función detached no cambia ownership ni salud. Un daemon normal sano sin build ID es
  reutilizable conforme F2/F5; listener con key distinta o identidad ambigua se preserva y
  falla cerrado, no provoca un spawn rival.
- **[DESIGN cerrado]** la argv bootstrap del bloque 9.2 la construye exclusivamente el
  `phase0_controller ensure-daemon` de Utopia. Resuelve Python venv, bootstrap y manifest
  por paths absolutos bajo roots congelados; path relativo o escape falla antes de spawn.
  Sólo ese controller exige status con ID exacto y ejecuta `probe --pid` para comparar PID,
  scheme, exe hash y command-line hash.
- `parse_args` no añade `--security-manifest`: los launchers/client configs existentes no
  necesitan conocer el path y no pueden sustituirlo. El inner argv que el bootstrap
  entrega a `server.run(..., security_build_id=...)` tampoco reacepta ese flag.
- Tests obligatorios ejercitan `_default_spawn` real (sin sustituir `spawn_fn`) parcheando
  sólo `spawn_detached`: cold start y respawn tras idle conservan exactamente la argv
  normal y aceptan `/status.security_build_id=null`. Separadamente, tests del controller
  Utopia exigen Python venv + `-I -B` + bootstrap + manifest + `daemon`; manifest
  ausente/drift, status build mismatch y daemon que muere antes de responder fallan sin
  shell ni segundo listener. README conserva el arranque normal y documenta que no sirve
  para campañas Utopia verificadas.

Para reutilizar un listener en campaña Utopia, el controller deriva primero la argv
bootstrap canónica y la identidad esperada del manifest aprobado. Sobre el socket ya
conectado acredita owner PID, exe, argv, cwd y snapshot v2 antes de enviar `/status?key=`.
Sólo después valida `security_build_id` y ejecuta el probe secundario del mismo PID.
Probe nozero, owner/rebind drift, snapshot parcial, status PID distinto o fingerprint
drift conserva el listener y falla cerrado sin transmitir secretos a un owner no acreditado.

## 10. Auditor S1/S2A/S2B

`security_runtime_audit.py`:

1. Parte de entrypoints `dayz_mcp.__main__`, daemon, doctor, server, lifecycle/admin
   CLI y `tools\mcp_capture.py`.
2. Resuelve imports locales transitivos y rechaza salida de roots allowlisted.
3. AST/string scan rechaza launch de `powershell`, `pwsh`, `cmd`, `.ps1`, Bypass,
   `shell=True`, `os.system`, `os.popen`, `os.spawn*`, `os.startfile`, subprocess shell,
   `asyncio.create_subprocess_*`, `multiprocessing` de proceso y FFI de
   `CreateProcess*`/`ShellExecute*` en toda clausura productiva/importable.
4. Distingue código productivo de docs/spikes/reference mediante allowlist versionada;
   una referencia jamás puede ser importable desde un entrypoint.
5. El runner deny-launch se instala antes de cualquier test ejecutable e intercepta
   `subprocess.Popen/run/call/check_*`, `asyncio.create_subprocess_*`, `os.system`,
   `os.popen`, `os.spawn*`, `os.startfile`, `multiprocessing` de proceso y las superficies
   Windows FFI detectadas; intento prohibido falla el test antes del launch. Sus propios
   self-tests usan fakes y demuestran cobertura sin crear procesos.
6. Los tests que hoy ejecutan PowerShell se reescriben sobre el provider nativo. Los que
   sólo validan launchers legacy quedan `skip` explícito
   `legacy_powershell_execution_forbidden` y conservan hash/assertions estáticas.

Salida: JSON create-only con roots, ficheros, hashes, findings, allowlist hash,
intercept count y veredicto. PASS exige cero finding productivo y cero launch bloqueado.

### 10.1 Contrato ejecutable del runner deny-launch

`[DESIGN cerrado]` `tools\p0s_test_runner.py` se invoca con el Python seleccionado para
el gate, `-I -B`, y una lista explícita no vacía de nombres unittest; no hace discovery
implícito. Instala el guard antes de que `unittest` importe cualquiera de esos nombres.
El guard registra únicamente el nombre estable de la superficie interceptada, nunca argv,
command line, entorno ni secretos. Exit `0` exige tests verdes e intercept count cero;
exit `1` significa fallo/error de test; exit `2`, uso inválido, fallo interno del runner o
cualquier intento interceptado aunque el test lo capture. Los self-tests ejercitan el
mecanismo con owners y funciones fake inyectados; comprueban bloqueo antes de llamada,
conteo, saneamiento, restauración y catálogo requerido sin crear procesos reales.

## 11. Viability tests y gates

### S0 — inventario estático protegido

- Guardar inventario/hashes de los callsites prohibidos, tests y del `runs.json` resuelto
  por `RuntimePaths`; todavía no reescribirlo ni arrancar daemon.
- `533/533` es sólo evidencia histórica del HANDOFF, no baseline ejecutado por P0.S.
- El primer artefacto ejecutable es el runner deny-launch y sus self-tests con fakes. Sólo
  después puede ejecutarse el subconjunto shell-free de la suite bajo ese runner; se
  registra el conteo real y nunca se presenta como baseline completa si hay tests legacy
  excluidos.
- Nunca se ejecuta el árbol actual sin protección ni se ejecuta un `.ps1`.

### S1 — dependencia y unit tests

- Wheel hash/tamaño/version/licencia correctos; wheel alterado o módulo fuera de venv
  falla.
- Snapshot feliz, argv con espacios/comillas/Unicode/lista vacía, AccessDenied,
  NoSuchProcess y PID reuse fake.
- Terminate exacto mata sólo el child registrado; cada uno de los cuatro campos
  observables o scheme forjado conserva ambos children; role vacío/no allowlisted se
  rechaza antes del guard; legacy conserva el child; timeout conserva record.
- Discover BFS sólo descendants allowlisted; sibling/global same-name no entra.
- Manifest legacy carga, queda UNRECONCILED y no se termina; v2 roundtrip exacto.
- Rollback fixtures viejo↔nuevo según §5.3.

### S2A — integración pre-manifest sin shell

- Toda la suite que no consume build identity verde bajo interceptor deny-launch, lanzada con el Python venv
  `-I -B`. El árbol `.venv-mcp` se congela después; todo proceso posterior usa `-B` para
  no crear pyc ni derivar el build manifest.
- ClientRuntime/ControlClient paridad completa.
- Doctor: healthy v2, legacy, corrupt scheme, drift, daemon build mismatch.
- Reclaim matrix de §7 verde, incluido BUG-044 para normal daemon y bootstrap mediante
  policies/fixtures locales; todavía no se ejecuta bootstrap contra un manifest real.
- `security_runtime_audit` cero findings productivos.
- `p0s_gate` cubre el schema build: roundtrip canónico, campo extra/ausente, tipo, orden,
  root escape y entry drift. `security_build_id` incluye LF.
- Antes de instanciar el primer `RunManifestStore` remediado, ejecutar el backup §5.3 y
  verificar receipt/fuente/destino; fuente ausente usa el branch explícito sin backup.
- `test_install_mcp.py` prueba print-only, `--register`, argv exacta, paths con espacios,
  key existente/nueva, fallo CLI, verificación mismatch y ausencia de shell; un host-path
  smoke desde venv limpio satisface E3 con el instalador Python.
- El guard de migración se prueba en daemon normal directo/lazy y bootstrap, con fuente
  presente/ausente, receipt válido/corrupto, dos startups en carrera, listener previo,
  identidad parcial y `AccessDenied`; ningún caso alcanza bind/store antes del receipt.
- Las suites unit/boundary/parity del plan capture y el auditor de su clausura están verdes;
  sus fuentes/tests/hashes forman parte de la clausura que se congela ahora. El gate real
  de píxel S4 todavía no se ha ejecutado y no entra en el build manifest.
- Publicar `p0s-build-manifest.json` con receipts S1/S2A y verificarlo dos veces. S2A no
  contiene su propio receipt ni prueba que dependa del manifest que está creando.

### S2B — bootstrap y vínculo post-manifest

- Probar bootstrap absent,
  corrupt, drift, policy mismatch, import-before-verify, probe read-only y build-id
  match/mismatch. Tests de `server.run` demuestran que sólo el override interno del
  bootstrap llega a `ServerConfig`, `/status` publica ese mismo ID en Utopia y `null` en
  daemon normal; daemon directo sin ID conserva lazy-spawn y bind existentes. Override
  parcial falla antes de `build_server_state`/bind. S3/S4
  quedan bloqueados hasta este punto.
- `p0s_gate` cubre ahora test→build: build hash/ID exactos, campo extra/ausente, root
  escape, entry drift y vínculo no circular. El receipt S2B se publica después de estas
  pruebas, nunca se inserta retroactivamente en el build manifest.

### S3 — estado host y Defender

Un administrador registra manualmente desde Windows Security:

- exclusiones path/process; ninguna puede cubrir PowerShell, Python, DayZ, DayZ Tools,
  `P:\DayZ_MCP_dev`, `P:\Utopia_PC_Suite` ni `P:\Mods`;
- realtime, cloud-delivered y behavior monitoring activos;
- Full Scan finalizado sin amenaza activa.

La automatización sólo puede lanzar binarios Windows directos con argv, nunca shell:

**[DESIGN]** scan:

```json
["C:\\ProgramData\\Microsoft\\Windows Defender\\Platform\\<VERIFIED_VERSION>\\MpCmdRun.exe", "-Scan", "-ScanType", "2"]
```

**[DESIGN]** eventos:

```json
["C:\\Windows\\System32\\wevtutil.exe", "qe", "Microsoft-Windows-Windows Defender/Operational", "/q:*[System[(EventID=1116 or EventID=1117)]]", "/f:xml", "/rd:true", "/c:32"]
```

Se resuelve y hashea la ruta concreta de `MpCmdRun.exe` antes de usarla. Exit distinto de
cero, XML inválido, estado no verificable o exclusión amplia = STOP, no workaround.

### S4 — único probe remediado

Sólo después de S1-S3 PASS **y** de que el consumer Utopia P0.2-C haya pasado sus tests
offline de controller/bootstrap:

1. Crear receipt/paths exclusivos y registrar timestamp + RecordId máximo 1116/1117.
2. Invocar `ensure-daemon` del controller P0.2-C para arrancar/reutilizar daemon sólo con
   `security_build_id` exacto; queda prohibido un launcher S4 alternativo.
3. Adquirir lease; un heartbeat cada 45 s.
4. Lifecycle start directo de `DayZDiag_x64.exe` hacia menú, sin mod ni misión, profiles
   exclusivos. Request exacto se congela y sólo admite `-nosplash`, `-skipIntro` y
   `-profiles=<ABS_CREATE_ONLY>`; cwd/exe canónicos verificados.
5. Exigir RUNNING con ProcessRecord v2 completo; esperar 10 s; stop del mismo `run_id` y
   exigir EXITED.
6. Release, `session_status` limpio, esperar 120 s y volver a leer eventos.

PASS: delta 1116/1117 vacío, run EXITED, cero proceso gestionado vivo, cero lease/ticket
propio, daemon status coherente y receipts/hashes completos. Cualquier pérdida de lease,
alerta, timeout o stop no confirmado = INCOMPLETE; no hay segundo intento automático.

Receipt no guarda argv: guarda SHA-256 del request, exe, scheme, PID/run_id, estados,
timestamps, ventana de eventos y hashes de outputs.

### S5 — freeze P0.S

Crear `P:\DayZ_MCP_dev\reports\security\p0s-test-manifest.json` con:

- path/bytes/SHA-256 del `p0s-build-manifest.json` y el mismo
  `security_build_id` observado en S4;
- clausura productiva y tests ejecutados;
- requirements, wheel/licencia y árbol venv;
- resultados S1/S2A/S2B/S3/S4;
- SHA de este plan y de ambos research;
- lista reference-only `.ps1`;
- revisión Codex + revisión independiente y aceptación de findings.

El gate intermedio `P0.S-A` se publica al terminar S2B: runner deny-launch instalado,
instalador/guard/control/capture nativos, suite shell-free y build manifest verificado dos
veces. Autoriza únicamente P0.1 Utopia offline; no autoriza DayZ, build Utopia ni daemon de
campaña. El owner se detiene y devuelve control: Utopia ejecuta P0.1/P0.2 offline y publica
su receipt PASS; sin él no empieza S3/S4. S3 es gate interno del owner; S4 es gate conjunto
owner+controller P0.2-C. Sólo
después de S3/S4 y
de crear/verificar dos veces el test manifest de S5 se publica `P0.S-F`, que autoriza los
procesos Utopia posteriores. P0.1 puede revalidar los schemas/hashes owner, pero nunca
produce ni sustituye P0.S-F; así no hay dependencia circular.

## 12. Orden de implementación

1. Implementar runner deny-launch y sus self-tests enteramente fake; instalarlo antes de
   ejecutar cualquier baseline o suite.
2. Crear fixtures negativos y viability tests; deben fallar por el motivo esperado sin
   ejecutar PowerShell ni crear procesos reales.
3. Vendor/pin psutil y verificar instalación offline.
4. Implementar `install_mcp.py`, sus tests y la documentación E3; retirar el `.ps1` de
   todo camino activo.
5. Implementar identidad v2 + `NativeProcessGuard`.
6. Migrar lifecycle/doctor y tests.
7. Migrar orphan/reclaim y cerrar BUG-044.
8. Extraer ControlClient y verificar paridad.
9. Añadir build identity y bootstrap estricto sólo para Utopia; conservar daemon normal.
10. Implementar capture y sus tests offline dentro de S2A; completar auditor, ejecutar
    S1/S2A, publicar build manifest, ejecutar S2B y realizar revisión doble independiente.
11. Publicar P0.S-A y PAUSAR/retornar a Utopia. Construir P0.1/P0.2-C enteramente
    offline/fake fuera de este owner; exigir receipt P0.2-C PASS enlazado al build manifest
    antes de reanudar. P0.2-B/AddonBuilder siguen prohibidos hasta P0.S-F.
12. Sólo con ese receipt y aprobación host explícita ejecutar S3/S4 y después S5/P0.S-F.

No hay refactor adyacente. Cada línea debe trazar a una sección y test de este plan.

## 13. Criterio de aceptación final

P0.S PASS únicamente si:

- cero lanzamiento productivo/test activo de shell, PowerShell, cmd, `.ps1`, WMI/WMIC;
- 100% suite verde bajo interceptor;
- H6 exacto para v2 y legacy intacto/fail-closed;
- F5 conserva todo listener sano y BUG-044 queda cubierto;
- ControlClient es stdlib-only y mantiene paridad;
- `install_mcp.py` satisface E3 de extremo a extremo y no queda ningún `.ps1` activo;
- daemon Utopia publica build verificado antes de bind; daemon normal conserva F2/F5;
- H10 verde para daemon normal y bootstrap Utopia: todo caller autenticado usa
  acreditación pre-request; fixtures foreign/rebind prueban cero bytes y cero key;
- Defender S3 sano y S4 sin nuevos 1116/1117;
- build manifest y test manifest create-only verifican dos veces y están enlazados;
- cero CRITICAL/HIGH de ambas revisiones; MEDIUM/LOW corregido o aceptado por escrito;
- cierre `own_lease=none`, `own_ticket=none`, `pending_commands=0`, cero run activo.

## 14. Commit y handoff

`P:\DayZ_MCP_dev` no contiene `.git`; no se inventa commit. El handoff usa manifest,
SHA-256 por fichero, receipts, baseline/post-suite, revisiones y `session_status` final.
Si el árbol se convierte en repo antes de implementar, se vuelve a revisar esta regla.
