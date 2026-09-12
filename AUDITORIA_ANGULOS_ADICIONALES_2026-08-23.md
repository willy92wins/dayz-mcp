> [!WARNING]
> **STALE (2026-09-12)**
> No es autoridad de producto. El HEAD actual es v1.2 / `origin/main` @ `edc7bb3`. No reabrir hallazgos sin evidencia nueva.

# Auditoría — Ángulos adicionales no cubiertos — 2026-08-23 (R2)

**Snapshot:** `1a3fd89` (`1a3fd890eab8bb0b579ad3d00757f6b077d013b3`) HEAD master 2026-08-23  
**Complementa a:** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\AUDITORIA_2026-08-23.md` (R1 — Python `tools/dayz_mcp/`, islas, rendimiento)  
**Alcance R2:** lo que R1 dejó explícitamente fuera — addon Enforce + pipeline de build, secretos/persistencia/filesystem, y docs/dependencias/calidad de tests/git. Solo lectura; este es el segundo fichero creado.  
**Método:** 3 sondas paralelas (E: Enforce+build, F: secretos+persistencia, G: docs+deps+tests) + verificación `Read`/`Grep`/`git ls-files` contra fuente viva.

## Conclusión ejecutiva

R1 no encontró ejecución remota ni fuga de credenciales; R2 confirma ese veredicto pero revela **3 fallos de severidad alta en dominios que R1 no tocó**:

| Dominio | Alta | Media | Baja | Estado |
|---|---:|---:|---:|---|
| Enforce addon + build | 2 | 2 | 1 | `SetHeader` sin nombre (`MCPBridge.c:200`) puede romper `POST /result`; `exec_enforce` sin gate local (`MCPBridge.c:516`); `IsFiniteFloat` cliente no rechaza `INF` |
| Secretos / persistencia / filesystem | 1 | 4 | 2 | Key por defecto en OneDrive (`install_mcp.py:773`) replica secreto a nube; ACLs no homogéneas; parent-reparse TOCTOU |
| Docs / deps / tests / git | 3 | 6 | 2 | `decisions/`+`plans/`+`reviews/` no versionados (110 `??`); `.venv-mcp` editable no reproducible; 18 verbos schemaless sin validación extra-keys |

Nada de lo anterior exige rollback de formato persistente. El fix de mayor impacto es migrar el keyfile fuera de OneDrive; el segundo es corregir `SetHeader` en Enforce.

## 1. Qué no cubrió R1 y por qué importa

R1 declaró fuera de alcance (AUDITORIA_2026-08-23.md §8):
- Compilación Enforce / `PACKONLY` / prueba in-game
- `P:` subst vs. addon productivo (creía sparse-excluded)
- `secrets-handling` fino + ACLs/TOCTOU de keyfile
- `OnStoreSave`-equivalente (`runtime_state` WAL, backups, quarantine) a fondo
- Docs sprawl, pinning de dependencias y calidad de los 129 ficheros de test

R2 cubre exactamente esos 4 huecos. Todo hallazgo cita `path:line` verificable con `git show` o `Read` directo.

## 2. Addon Enforce + pipeline de build (lane E)

Addon en `HEAD` **sí está versionado** (`git ls-files addon/` lista 13 ficheros: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\$PBOPREFIX$`, `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\config.cpp`, `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\include.lst`, `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\4_World\MCP_CarScript.c`, 7× `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\*.c`). El hermano `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP` no es repo git — copia desplegada bajo `P:\` (`subst P:\`) — `fc /b` dev↔`P:\` da 0 diff salvo 5 `.bak_*` en `P:\DayZ_MCP\` no trackeados.

### E-01 — [PROBABLE] Alta — `RestContext.SetHeader` sin nombre de cabecera

**Evidencia.** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPBridge.c:200` y `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPClientBridge.c:319` hacen `m_Ctx.SetHeader("application/json")`. La API Enforce es `SetHeader(string header)` donde `header` es línea completa `Name: value` (vanilla siempre `SetHeader("Content-Type: application/json")`).

**Impacto.** Servidor recibe `POST /result` sin `Content-Type: application/json` y puede no parsear el JSON (`MCPBridge.c:3392 POST result`, `MCPClientBridge.c:3317`).

**Gate.** AddonBuilder + captura en daemon del header `Content-Type` en `/result` — hoy `null_commands`/`parse_failed` silencioso.

**Fix.** `SetHeader("Content-Type: application/json")` en ambos bridges.

### E-02 — [POTENCIAL] Baja — `key` en query sin `EncodeQueryValue`

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPBridge.c:226` `poll?key="+m_Key` y `:3387` `result?key="+m_Key` (idem `MCPClientBridge.c:350`; `inst`/`ver` sí usan `EncodeQueryValue:230,3389`). Hoy safe porque `m_Key` es `secrets.token_urlsafe(32)` (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\install_mcp.py:951` — alfabeto `A-Za-z0-9-_` sin `+/\=`), pero contrato no documentado; cambio de generador rompería query. Logging sí evita fuga: `MCPBridge.c:204` `keylen=` nunca valor.

### E-03 — [CONFIRMADO] Media — `IsFiniteFloat` cliente incompleto

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPBridge.c:2425-2437` rechaza `NaN`+`INF` (`>= float.MAX || <= -float.MAX`); `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPClientBridge.c:3078-3086` solo `NaN`. `INF`/`-INF` pasan en cliente a `GetThrottle`/`SetFOV`/`ValidateFloatArray` (`MCPClientBridge.c:2942`).

**Fix.** Copiar chequeo `INF` del bridge servidor al cliente.

### E-04 — [CONFIRMADO] Alta (condicional) — `exec_enforce` sin gate local en addon

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPBridge.c:516-518` → `:1641` `GetGame().ExecuteEnforceScript(expr, main_fn)` solo con `expr != ""`. Sin allowlist local; autorización vive solo en `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\daemon_policy.py`. Si loopback queda sin esa política, el addon es ejecución arbitraria server-side. Hallazgo condicional fuera de alcance R1 deliberadamente.

### E-05 — [CONFIRMADO OK] — `CallLater`/`delete`/ternario ausentes

`grep` en `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\**\*.c` → 0 `delete`/`CallLater`/ternario ejecutables (solo comentarios `MCPMessages.c:454`, `MCPClientBridge.c:2650`). Firmas `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\4_World\MCP_CarScript.c:603` `OnInput(float dt)` y `:666` `OnContact(string zoneName, vector localPos, IEntity other, Contact data)` correctas vanilla (`dayz-physics-engine`).

### E-06 — [CONFIRMADO OK] — Sin `IsServer`/`IsClient` (diseño)

0 hits `IsServer|IsClient|IsDedicated` en `addon/scripts/**/*.c` — correcto: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\config.cpp:22` `dependencies={"World","Mission"}` + `MissionServer.c:5-28`/`MissionGameplay.c:4-28` separan carga.

### E-07 — [CONFIRMADO] Media — `$PBOPREFIX$`/`config`/`include.lst` OK pero `.bak` en `P:\`

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\$PBOPREFIX$:1`=`DayZ_MCP` coincide `config.cpp:3` `CfgPatches DayZ_MCP` y `:16` `dir="DayZ_MCP"`; `config.cpp:8` `requiredAddons={"DZ_Data"}` mínimo correcto; `model.cfg` ausente correctamente (sin `.p3d`). `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\include.lst:1`=`*.c;*.layout` evitó pack de `.bak_*` (medido 2026-08-21: sin lista 453 kB con 244 kB de 3 copias, con lista 209 kB). Residuo `P:\DayZ_MCP\` con `MCPBridge.c.bak_pre_fencing_20260819` 75 kB + 2 más — no se empaquetan hoy pero un `pack-addon.ps1:64` sin `-include` sí los embarcaría. Gate `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_addon_tree_has_no_write_artifacts.py:105-142`.

### E-08 — [CONFIRMADO OK + 1 delta] Build pipeline

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\pack-addon.ps1:42-56` exige `P:\` + `$PBOPREFIX$`; `:64-72` solo `-include` si existe; `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\pyproject.toml:8` `requires-python >=3.11` en lockstep con `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\requirements-mcp.txt:1` y `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\install_mcp.py:20` `MIN_PYTHON=(3,11)` (gate `test_packaging_declarations.py:39`); `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dependency-lock.json:2-41` + `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\build_native_launcher.py:183-198` verifican `psutil_wheel` size+sha256; `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\build_native_launcher.py:53-71` `PACKAGED_MODULES` 14 módulos (incl. `win32_fileinfo.py` añadido 2026-08-21) es lo que se zipea en `app.pyz`. Delta menor: `pyproject.toml:2` `requires=["setuptools>=64"]` sin `wheel`/`build` — no rompe (`cl.exe/link.exe` directo), pero host limpio resuelve distinto vs `.venv-mcp`.

## 3. Secretos, persistencia y filesystem (lane F)

Skill `secrets-handling` invocada. `git grep -iE "sk-|ghp_|github_pat|password=|BEGIN.*PRIVATE|Bearer"` → 0 secretos duros; no hay credencial pegada en transcript local.

### F-S02 — [CONFIRMADO] Alta — Keyfile por defecto dentro de OneDrive sync

**Evidencia.** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\install_mcp.py:773` `keyfile = canonical_tools / ".dayz_mcp.key"` y `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\install-mcp.ps1:21` `Join-Path $ToolsRoot ".dayz_mcp.key"` → `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.dayz_mcp.key` dentro de árbol sincronizado a nube Microsoft. `%LOCALAPPDATA%\DayZ_MCP\` no está en OneDrive.

**Impacto.** Secreto sale del host (G6 fail-closed). Beneficio OneDrive (backup) no compensa exfiltración.

**Fix.** Migrar default a `RuntimePaths.root / "daemon.key"` (ya raíz de `audit/`/`coordination`) con migración de legado + aviso.

### F-S03 — [PROBABLE] Media — TOCTOU parents reparse

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\pinned_keyfile.py:79-87` `_assert_no_reparse_parents` usa `GetFileAttributesW` antes de `CreateFileW:131`. Ventana race sustituir parent por symlink/junction. `FILE_FLAG_OPEN_REPARSE_POINT:131` solo protege último componente. Mitigación parcial `GetFinalPathNameByHandleW:161` detecta file-symlink pero no parent-reparse; falta open handle-based por cada parent con `FILE_FLAG_BACKUP_SEMANTICS|OPEN_REPARSE_POINT`. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\host_config.py:638` repite patrón.

### F-S04 — [CONFIRMADO OK] — Hardlink/DeletePending/Directory/BOM/size

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\pinned_keyfile.py:153-159` → `NumberOfLinks !=1`, `DeletePending`, `Directory`, `EndOfFile >4096`, `REPARSE_POINT`, `_MAX_RAW_BYTES=4096:28`, rechazo BOM `0xEFBBBF:171`, `key 1..1024 sin \0\r\n:177` — todo post-open sobre handle con `OPEN_REPARSE_POINT`.

### F-S06 — [PROBABLE] Media — ACLs no homogéneas

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\host_config.py:808-831` `_mkdir_restricted` SDDL `D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;OW)` (SYSTEM+owner) + `:836` `os.open 0o600` para journal — correcto. Pero `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\runtime_state.py:1893` `mkdir(parents=True)` de `audit/`/`coordination.json`/`runs.json` hereda ACL de `%LOCALAPPDATA%\DayZ_MCP\` sin SDDL; `tools/install_mcp.py:925` `open("x")` sin `0o600`; `install-mcp.ps1:428` `Set-Content` sin ACL. Inconsistente.

**Fix.** Centralizar `mkdir_restricted` + `os.open 0o600` para toda creación bajo `RuntimePaths.root`.

### F-P01/P02/P03 — [CONFIRMADO OK] — Atomic write + WAL + quarantine

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\runtime_state.py:1890-1948` `_atomic_write_text`: `O_CREAT|O_EXCL|O_NOFOLLOW|O_BINARY`, `token_hex(12):1905` (16 intentos), `fstat !reparse && is_reg`, `fsync`, `st_size==len`, `verify expected_sha256/identity` antes de `os.replace`, rollback `_unlink_if_same`; `stat_identity:1781` usa `dev+ino+size+mtime_ns`. `CoordinationFaultStore` CAS `runtime_state.py:366-415` con `msvcrt.LK_LOCK` sobre `.lock` `0o600` + doble `stat_identity` lstat vs fstat. `runtime_state.py:1616-1646` quarantine `os.replace` a `*.corrupt.{digest}` + re-read verify. Todo PASS.

### F-P04 — [PROBABLE] Media — `JsonlAuditWriter` TOCTOU + sin `fsync` dir

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\runtime_state.py:339-362` `write_once` escanea `events.jsonl` + 5 backups `read_text().splitlines()` sin `O_NOFOLLOW`/pinned read (symlink swap entre `exists` y `read`). Append `previous=read_text(); _atomic_write_text(previous+line)` bajo `threading.Lock` intra-proceso pero no inter-proceso (daemon single-owner aceptable). Rotación `os.replace(... .1):362` sin `fsync` dir ni check reparse destino. Inconsistente con F-P01.

### F-L01..L04 — [CONFIRMADO OK] — Redacción en logs/wire

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\runtime_state.py:1295-1307` `_redact` recursivo `SECRET_KEYS={"key","api_key","keyfile","lease_token","password","token"}:24`; `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\secure_launcher.py:27-59` `_IncrementalRedactor` maneja split across chunks `utf-8`+`utf-16le`; `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\server.py:190-214` `_log_opaque_failure` nunca vuelca key al wire (`_is_safe_error_token:244` solo `3..64 [A-Za-z][A-Za-z0-9_]*`); `MCPBridge.c:204` `keylen=` nunca valor; `loopback.py:2476-2484` `hmac.compare_digest` sin eco.

### F-F02 — [PROBABLE] Baja — `%LOCALAPPDATA%\DayZ_MCP` ACL herencia

Creación lazy `mkdir(parents=True)` sin SDDL; default Windows hereda usuario+SYSTEM (no `Everyone`) pero no verificado en deploy. Aplicar mismo SDDL que journal en primera creación.

## 4. Docs, dependencias, tests y git (lane G)

### D2 — [CONFIRMADO] Alta — `CLAUDE.md` stale engaña a agente nuevo

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\CLAUDE.md:14,18,31` dice `POC fase 0, 11 tools/6 dominios, http.server crudo, FastMCP — aún NO` vs vivo `bridge v8, 54 tools, daemon 3 modos (--client/--daemon/--embedded)`. `test_docs_truth.py:30-32` lo excluye a propósito pero agente nuevo lo lee como verdad.

**Fix.** Actualizar `CLAUDE.md` o marcar `HISTORICAL` con fecha.

### D4/D6/D7/D9/D10 — [CONFIRMADO] Media/Alta — Docs truth drifts

- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\QUICKSTART.md:14-17` no menciona `allow_root_junction` policy (`dayz_tools_paths.py:1-40`, `build_native_launcher.py:289`).
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\README.md:67-77` `lease_acquire` listado pero no en `loopback.py:46 WHITELISTED_COMMANDS` (alias MCP `server.py:2439` vs ingress; `capture_screenshot` local `server.py:3285` tampoco whitelisted — divergencia MCP vs ingress).
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-mcp-architecture.md:244-246` dice `Authorization` header vs código `?key=` (`MCPBridge.c:226`) — contradicción abierta HANDOFF #5.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\PROJECT-MAP.md:17` `Last PBO 2026-07-12` drift 42 días (HEAD 2026-08-23) — aunque `fa9c89f` ya evitó pinnear `HANDOFF` líneas/KB.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md:111-141` G0-G4 `❓` 5/5, F/G/H mixto `✓ offline` sin gate in-game; `HANDOFF.md:LIVE-STATE` confirma `gear_shift no existe` y `G lote bloqueado` — spec promete gates S0/A/B no corridos.

### DEP2 — [CONFIRMADO] Media — `dependency-lock.json` solo pinnea `psutil`

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dependency-lock.json:1` format 1, 4 artifacts `psutil_wheel 137737 B sha EB7E…` + `cpython_embed 12 MB sha AD49…` + toolchains `msvc/sdk` — `test_dependency_lock.py:182` pin byte-exacto pero `mcp`/`Pillow` (`requirements-mcp.txt:1` `mcp==1.27.2`, `Pillow==12.2.0`) sin hash (2/3 deps PyPI sin pin).

### DEP3 — [CONFIRMADO] Media — `pip install --upgrade pip` sin pin

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\install-mcp.ps1:422` `& $VenvPython -m pip install --upgrade pip` sin versión — HANDOFF abierto #3; dos clones mismo commit ≠ toolchain.

### DEP4 — [CONFIRMADO] Alta — `.venv-mcp` editable no reproducible

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\.gitignore:27` ignora `.venv-mcp`; `pip freeze` muestra `psutil @ file:///P:/DayZ_MCP_dev/tools/vendor/psutil/...#sha256=eb7e8...` + `git+https://github.com/willy92wins/dayz-mcp.git@1a3fd89#egg=...&subdirectory=tools` — exige path exacto `tools/.venv-mcp` y git; sin él `identity_migration:FileNotFoundError` (`fb-20260823-040320-8575`). Fragilidad host.

### T2/T4/T5 — [CONFIRMADO] Alta/Media — Tests

- **T2 Media:** 49 `def setUp` en `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\*.py`, 17 ficheros replican `TemporaryDirectory+RuntimePaths`, 397 `TemporaryDirectory` en 134 ficheros (134 trackeados vs 132 `git ls-files` — `test_h8_distributed_gate.py` y `test_process_job_spike.py` untracked).
- **T4 Alta:** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:100-119` `_SCHEMALESS_COMMANDS` 18 verbos (`query_player_state`…`vehicle_release`) hacen `return True:670` sin check extra-keys; `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_command_validation_coverage.py:129` solo cubre 2/20 (`object_delete`/`notify_players` rechazan extra) — 18 pasan `{"evil":1}` al bridge.
- **T1 OK:** 0 `assert True` literal, 0 tautologías `0.000` como assert.

### G1 — [CONFIRMADO] Alta — `.gitignore` incompleto → 110 `??`

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\.gitignore:1-40` no ignora `decisions/`, `plans/`, `reviews/`, `reports/`, `test-contracts/`, `*.bak*`, `_backups/`, `_fase*/`, `_poc/`, `_s0/`, `_step0/`, `tools/_*/`, `AUDITORIA_*.md`, `NEXT-SESSION-PROMPT.txt`, `*_bak_infdrive_*`, `tools/*.json` verdicts. `git status --porcelain` 110 `??`; `decisions/decision-log.md` 22 decisiones D01-D21 canónico append-only pero 0 commits (`git log --all -- decisions/` vacío); `plans/2026-06-10-fase4-mcp.md` referenciado en `product-spec.md` changelog pero clon no lo ve.

**Fix.** Añadir a `.gitignore`: `/_fase*/`, `/_poc/`, `/_s0/`, `/_compile/`, `/plans/`, `/reviews/`, `/reports/`, `/decisions/`, `/test-contracts/`, `*.bak*`, `*.bak_pre*`, `/_backups/`, `/AUDITORIA_*.md`, `/_client/`, `/_server/` y decidir si `decisions/decision-log.md` debe versionarse (recomendado sí) o moverse a `docs/`.

## 5. Recomendación priorizada (solo R2)

1. **F-S02 Alta** — migrar `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.dayz_mcp.key` → `%LOCALAPPDATA%\DayZ_MCP\daemon.key` (1 cambio + script migración legado).
2. **E-01 Alta** — `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPBridge.c:200` + `MCPClientBridge.c:319` `SetHeader("Content-Type: application/json")`.
3. **G1 Alta** — completar `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\.gitignore` y versionar `decisions/decision-log.md` (limpia 110 `??`).
4. **T4 Alta** — schemaless 18 verbos `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py:670` o bien documentar que extra-keys schemaless son intencionales y cerrar HANDOFF #6, o añadir `bad_args` y test `test_command_validation_coverage.py:129` negativo.
5. **F-S06+F-F02 Media** — homogeneizar ACLs `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\runtime_state.py:1893` + `host_config.py:808` vía `mkdir_restricted` central.
6. **DEP2+DEP3 Media** — hashear `mcp`/`Pillow` en `dependency-lock.json` y pinnear `pip` (`pip==25.1.x`) en `install-mcp.ps1:422`.

## 6. Qué sigue sin verificarse

- No se ejecutó suite completa 1.7k tests ni `PACKONLY` Enforce ni prueba in-game (R1/R2 estáticos).
- No se hizo captura de header `Content-Type` en daemon para E-01 (inferencia por firma; requiere binarizado o trace).
- No se probó `subst P:` físico para parent-reparse race (F-S03) ni ACL real de `%LOCALAPPDATA%` en esta máquina.
- No se instaló `ruff/mypy/bandit` ni `pip install --verify-reproducible` (toolchain MSVC).

---
*Generado 2026-08-23 R2 desde `1a3fd89`. Sondas: addon (`MCPBridge.c:58KB` + `pack-addon.ps1` + `include.lst`), secretos (`pinned_keyfile.py:1719` + ACLs + atomic writes), docs/deps/tests (`pyproject.toml`/`dependency-lock.json`/`test_*.py` 134 ficheros). Verificación: `git ls-files`, `grep`, `ast.parse`, `pip freeze`, `fc /b` dev↔P:\.*
