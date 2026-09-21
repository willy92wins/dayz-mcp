# Resellado del launcher nativo de DayZ-MCP (2026-09-20)

El bundle del launcher llevaba desde el **15-09** con una copia vieja de `dayz_test_worker.py` embebida.
Los PR **#86** (`e731f9f`, «treat already-gone DayZ cleanup as stop success») y **#87** (`d72a734`, «do not
treat run_not_active as already-gone») cambiaron ese módulo y nadie reselló. Resultado: **todo
`dayz_test_run` fallaba con `invalid_native_launcher_bundle`**, y dos sesiones quedaron bloqueadas desde
el 18 hasta el 20.

## Por qué costó dos días detectarlo

El gate solo salta en el verbo de **arranque**. Las lecturas (`session_status`, `bridge_status`,
`lease_acquire`) funcionan con normalidad, y el corte ocurre **antes** de llegar al demonio, así que no
deja ni un evento en el audit (`AppData\Local\DayZ_MCP\audit\events.jsonl`). Una sesión que solo lea —como
la de LFPowerGrid— no nota absolutamente nada y reporta el servidor como sano, que es justo lo que hizo.

Ninguna señal del servidor lo delata: `daemon_modules.stale=[]`, `server_modules.status="fresh"`,
`tool_registry_source_stale=false`, ambos `*_remediation=null`. Todas verdes, con el launcher caducado.

## Diagnóstico (dos medidas independientes)

Este receptor y la sesión «Subaru BRZ UI interfaces» midieron por separado los mismos dos hashes:

| Pin de `closure-manifest.json` | Sellado | En disco | |
|---|---|---|---|
| `dayz_test_readiness_sha256` | `50C4CF83…` | `50C4CF83…` | coincide |
| `dayz_test_request_sha256` | `379B32DE…` | `379B32DE…` | coincide |
| **`dayz_test_worker_sha256`** | **`2F247575…`** | **`B83F0E1A…`** | **distinto** |
| `native_broker_protocol_sha256` | `A8D97860…` | `A8D97860…` | coincide |

**Trampa al comparar:** el manifiesto guarda el hex en MAYÚSCULAS. Una comparación sensible a caja marca
las 77 `entries` como malas y manda a perseguir una corrupción que no existe.

## Qué se hizo, con el OK del dueño

1. Bundle reconstruido **offline y reproducible** desde `5fe154a`, en un directorio aparte primero
   (`build_native_launcher.py --output <staging> --offline --verify-reproducible`, rc=0).
2. Copia de seguridad del bundle anterior (57 ficheros) **antes** de tocar nada → `bundle-backup-20260920/`.
3. Instalado sobre el canónico y registro actualizado por CAS.

| Artefacto | Antes | Ahora |
|---|---|---|
| Registro `tools\approved-launchers.json` | `1CBC9ED4…` | `0D250289…` |
| PE `dayz-test-launcher.exe` | `67D974AF…` | `7D521F75…` |
| `app.pyz` | `3F13CFB8…` | `AE32634B…` |
| `closure-manifest.json` | `27BC0AD6…` | `2B9B2D24…` |
| `request-policy.json` | `0BFC9D30…` | `0BFC9D30…` (**sin cambio**) |

La política de seguridad no cambió: esto resella código, no permisos.

**`--expected-sha256` no es el hash del PE**, sino un CAS sobre el fichero de registro actual
(`launcher_registry_update.py:298-325`; si no casa, `launcher_registry_cas_mismatch`).

## Qué se verificó y qué no

- **Verificado:** los cuatro pines coinciden con el árbol tras instalar; el registro apunta al PE nuevo;
  `bridge_status` sigue limpio. Y sobre todo, el propio comando de registro **valida el PE y carga el
  bundle verificado antes de confirmar** (`_validated_entry`, `:191-199`): que saliera rc=0 significa que
  el artefacto nuevo pasa el mismo gate que rechazaba a esas sesiones.
- **Confirmado en juego (2026-09-20, cierre):** la sesión «Subaru BRZ UI interfaces», bloqueada desde el
  18, lanzó correctamente tras el resellado. Con eso el arreglo queda validado de punta a punta: hash,
  gate del registro y arranque real.

## Vuelta atrás

`bundle-backup-20260920/` (57 ficheros) restaura el bundle anterior; el módulo trae además
`launcher_registry_update rollback-last`.

## Contenido de esta carpeta

- `bundle-backup-20260920/` — el bundle anterior íntegro (PE `67D974AF…`).
- `bridge_status-after-reseal.txt` — estado del servidor tras el resellado.
- `clients-before.csv` — censo de procesos cliente vivos (34) en el momento del triaje.
- `mcp_call.py` — cliente stdio mínimo para hablar JSON-RPC con el MCP sin canal del host (plan B).
