Cierro F-10: primero reproduzco la lectura de 200.000 bytes y localizo ambos lectores antes de tocar nada.Siguiente: leo los dos lectores y grep de consumidores para decidir el sitio del cambio sin tocar el bundle.Reproduzco el fichero de 200.000 bytes y miro cómo los tests y el daemon construyen la ruta del keyfile.Reproducido: `read_key` acepta 200.000 bytes; el lector pinned lo rechaza. Siguiente: `read_key` delega al pinned y añado el test de mutación.Verde. Ahora el gate de mutación: revierto `read_key` al `open()` crudo, corro el test y restauro.F-10 cerrado: `read_key` ya no hace `open()` crudo; un keyfile de 200.000 bytes queda rechazado.

## Re-medición (200.000 bytes)

**Antes** (fichero local `b'x'*200000`):
- `read_key` → `type=ok len=200000`
- `read_pinned_keyfile` → `ValueError('invalid_daemon_keyfile')`
- corto `b'test-key\n'` → ambos `'test-key'`

**Después** del mismo fichero:
- `read_key` → `ValueError('invalid_daemon_keyfile: must be a local regular disk file with one hard link and no reparse points, at most 4096 bytes, UTF-8 without BOM, and a single-line key of at most 1024 characters')`
- `read_pinned_keyfile` → `ValueError('invalid_daemon_keyfile')`
- corto → ambos `'test-key'`

Reproducido. No se midió symlink/reparse (el test pinned sigue en `skipped 'symlink unavailable: 1314'`), ni TOCTOU, ni exfiltración.

## Diff

`dayz_mcp/loopback.py` — import + `read_key` delega al lector pinned. `daemon.py` y `server.py` no se tocan.

```python
from dayz_mcp import daemon_credential, orphan_guard, pinned_keyfile, ui_dialog
```

```python
def read_key(path: str) -> str:
    """Read the daemon auth key through the pinned local-disk contract.
    ...
    """
    try:
        return pinned_keyfile.read_pinned_keyfile(path)
    except ValueError as error:
        if str(error) != "invalid_daemon_keyfile":
            raise
        raise ValueError(
            "invalid_daemon_keyfile: must be a local regular disk file "
            "with one hard link and no reparse points, at most 4096 bytes, "
            "UTF-8 without BOM, and a single-line key of at most 1024 "
            "characters"
        ) from None
```

Nuevo: `tests/test_startup_keyfile.py` (gate de 200k + delegación + call-sites).

No tocados: `pinned_keyfile.py`, `host_config.py`, `tests/test_bug046_startup_deadlock.py`.

## Tres decisiones

1. **El cambio va en `read_key`, no se borra.** Lo usan `daemon.py:812` y `server.py:540`. Los tests de client-mode parchean `server.read_key` como centinela de que el cliente **no** debe leer una segunda clave; borrar el nombre obliga a editar esos tests. Un solo wrapper concentra el mensaje de contrato. `admin_cli` / `doctor` / `lifecycle_cli` / `daemon_credential` ya llaman a `read_pinned_keyfile` directo; no se tocan.

2. **Mismo trato para daemon y embedded.** Los dos bindean el loopback y autentican con este secreto. El privilegio es la carga, no el modelo de vida del proceso (idle vs parent-watchdog). Client-mode ya pasa por el lector pinned vía `daemon_credential`; el agujero era exactamente estos dos arranques.

3. **Rechazar lo raro-pero-funcional es aceptable.** Oversize, BOM, ruta relativa y hardlinks extra ya fallan en doctor/admin/client. El módulo firmado colapsa todo a `invalid_daemon_keyfile` y no se puede editar, así que el wrapper **no puede nombrar el control que falló**. Conserva el token (los `assertRaisesRegex` y matchers siguen pegando) y lista el contrato, sin bytes del keyfile. Un `--keyfile` relativo en arranque manual queda fuera; las registrations de `host_config` ya canonicizan.

## Gate de mutación

`tests.test_startup_keyfile.StartupKeyfileHardeningTest.test_oversized_keyfile_is_rejected`

**Verde:**
```
test_oversized_keyfile_is_rejected ... ok
Ran 1 test in 0.013s
OK
```
(módulo entero: 4 tests OK)

**Rojo al revertir a `open()` crudo:**
```
AssertionError: ValueError not raised
FAILED (failures=1)
```

**Verde al restaurar:**
```
test_oversized_keyfile_is_rejected ... ok
Ran 1 test in 0.013s
OK
```

## Grep de consumidores

**`read_key`**
| Sitio | Qué es |
|---|---|
| `dayz_mcp/loopback.py:3145` | definición (ahora wrapper) |
| `dayz_mcp/daemon.py:44,812` | import + `run_daemon` |
| `dayz_mcp/server.py:41,540` | import + `start_loopback` |
| `tests/test_client_mode.py` | parche de `server.read_key` (el cliente no debe llamarlo) |
| `tests/test_admin_cli.py` | `_read_key` distinto; `read_key` es mock de `read_pinned_keyfile` |
| `tests/test_control_client.py:607` | mock de `read_pinned_keyfile` |
| `tests/test_startup_keyfile.py` | gate nuevo |

**`invalid_daemon_keyfile` literal:** `pinned_keyfile.py` (raises), `loopback.py` (wrapper), `tests/test_pinned_keyfile.py` (regex), `tests/test_startup_keyfile.py`. `doctor.py` no lee la cadena; llama al pinned y deja subir el `ValueError`. `empty keyfile` queda solo en `install-mcp.ps1:339` (check propio, no tocado).

## Módulos corridos

| Módulo | Resultado |
|---|---|
| `tests.test_startup_keyfile` | 4 OK |
| `tests.test_pinned_keyfile` | 3 OK (1 skipped, symlink 1314) |
| `tests.test_daemon` | 48 OK |
| `tests.test_instance_fence` | 63 OK |
| `tests.test_loopback` | 45 OK |
| `tests.test_client_credential_rotation_e2e` | 2 skipped (`project MCP runtime is not installed` — este workspace no tiene `.venv-mcp`) |
| extra: `tests.test_client_mode.ClientRuntimeProvenanceGateTest` | 8 OK (parchea `server.read_key`) |

```json
{"status":"ok","paths":["dayz_mcp/loopback.py","tests/test_startup_keyfile.py"],"summary":"read_key now delegates to the pinned local-disk reader; 200k keyfile is rejected","verified":["200k read_key accepted before and rejected after","short key still reads as test-key on both readers","mutation gate red on raw open() and green after restore","listed unittest modules except live e2e spawn"],"not_verified":["symlink/reparse (WinError 1314)","TOCTOU","exfiltration","live daemon e2e (no .venv-mcp in this workspace)","full suite"]}
```