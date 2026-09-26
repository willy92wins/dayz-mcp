# Plan — Acreditar mods de Steam Workshop en `dayz_test_run`

**Fecha:** 2026-08-16 · **Decidido por:** Guillermo · **Ejecuta:** Codex · **Orquesta:** Claude

## Problema

`base_mods` / `extra_mods` / `server_mods` son parámetros muertos para **todo** mod instalado
desde Steam Workshop. Cadena verificada contra fichero:

1. `dayz_test_tool.py:75-85` `_valid_public_mod` rechaza `ntpath.isabs`, `:`, `\`, `/` → desde la
   superficie MCP sólo se puede pasar un nombre de carpeta suelto.
2. `dayz_test_worker.py:183-188` `_mod_path` resuelve el nombre suelto contra `runtime.mods_root`
   (`P:\Mods` → junction a `...\common\DayZ\!Workshop`).
3. `request_path_authority.py:475` rechaza el descendiente por `item.reparse_tag != 0`, y **toda**
   carpeta `!Workshop\@Mod` es un mount-point junction (tag `0xA0000003`) hacia
   `steamapps\workshop\content\221100\<id>`.

Síntoma en cliente: `dayz_test_failed:ValueError` sin ninguna orientación.

## Dos correcciones al diagnóstico de partida

- **La ruta absoluta sola no arregla nada.** `_accredit_absolute` termina en
  `return _open_descendant(path, candidates[0])` (`request_path_authority.py:495`), el **mismo**
  gate de la línea 475. Abrir la superficie a absolutas sin tocar el gate reproduce el error.
- **"Comparar la identidad NTFS del destino contra las raíces permitidas" no sirve**: el destino
  (`steamapps\workshop\content\...`) cae **fuera** de `P:\Mods`. El discriminador correcto es el
  que el módulo ya usa para las raíces (ver Cambio 1).

## Invariante que se modifica (DZ-R7)

De: *"ningún componente descendiente de una raíz sellada puede ser un reparse point"*
A: *"el componente **hoja** de un descendiente puede ser un mount-point junction si y sólo si su
raíz sellada tiene `allow_root_junction: true`; cualquier otro reparse point, y cualquier reparse
point en un componente intermedio, siguen prohibidos."*

Symlinks siguen prohibidos en todos los casos — hay test que lo fija
(`test_nested_directory_symlink_is_not_an_approved_root_junction`).

## Cambio 1 — gate de acreditación `[DESIGN, por analogía exacta]`

**Fichero:** `tools/dayz_mcp/request_path_authority.py`, función `_open_descendant` (452-481).
**Fuera del bundle nativo** → no dispara D-40 (ver §Fuera de alcance).

Estado actual `[EXACT]` (línea 475):

```python
if item.reparse_tag != 0 or not _same_path(item.final_path, expected):
    _invalid()
```

El patrón a replicar **ya existe dos veces en el mismo módulo** — no se inventa nada:

| Paso | `_open_sealed_root` | `_capture_root_for_test` |
|---|---|---|
| Intermedios sin reparse | `:331` | `:364` |
| Hoja == MOUNT_POINT si `allow_root_junction` | `:313` | `:371` |
| Hoja sin reparse si no | `:315` | `:373` |
| Reapertura `follow_root_reparse=True` para la identidad del destino | `:340` | `:375` |

Regla a implementar en `_open_descendant`:

- Componente **no hoja**: se mantiene `reparse_tag != 0` → `_invalid()`.
- Componente **hoja**:
  - si `root.allow_root_junction` es `True` → aceptar `reparse_tag in {0, _IO_REPARSE_TAG_MOUNT_POINT}`;
  - si no → se mantiene `reparse_tag != 0` → `_invalid()`.
- `_same_path(item.final_path, expected)` se mantiene **sin cambios** en todos los casos (con
  `OPEN_REPARSE_POINT` el `final_path` del junction es el propio junction, por eso ya da `True`).
- Si la hoja resultó ser mount-point, reabrir con `_open_directory(current, follow_root_reparse=True)`
  y devolver **esa** identidad, igual que `:340` y `:375`. El handle sigue pinneado en `opened`.

**Decisión de diseño abierta (recomendación incluida):** limitar además el junction a profundidad 1
(hijo directo de la raíz) es un cinturón barato, pero **recomiendo no añadirlo**: la regla "sólo la
hoja" es idéntica a la ya existente en el módulo, y menos código nuevo es menos superficie de error.
Si Codex ve un motivo para el cinturón, que lo argumente antes de implementarlo.

## Cambio 2 — superficie MCP acepta absolutas dentro de `mod_roots` `[DESIGN]`

**Fichero:** `tools/dayz_mcp/dayz_test_tool.py` (`_valid_public_mod:75-85`, `_public_mod_list:88-93`).

- `_public_mod_list` pasa a recibir las raíces y `_valid_public_mod` acepta una ruta absoluta si
  cae dentro de `selected.mod_roots`. `RequestProjectPolicy.mod_roots` ya existe
  (`dayz_test_request.py:164`) y `_selected_policy:65-72` ya devuelve la policy antes de validar.
- Alinea la superficie con lo que el daemon **ya** acepta: `_valid_mod_entry:137-148` admite
  absoluta dentro de raíces.
- **NO tocar `dayz_test_request.py`** — está en `_HASHED_MODULES` y `_APP_PACKAGED_MODULES`
  (`native_bundle.py:55` y `:65`). No hace falta tocarlo: su validación ya es correcta.

## Cambio 3 — error diagnosticable `[DESIGN]`

Hoy `accredit_request_paths` colapsa todo en `raise ValueError("invalid_dayz_test_path_authority")`
(`request_path_authority.py:603-606`), que sube por `native_launcher_transaction.py:112` hasta
`server.py:1124-1129` y sale como `dayz_test_failed:ValueError`.

- El mensaje **no** debe destaparse: el comentario F1.4 (`server.py:1125-1128`) documenta que puede
  llevar rutas del host.
- El carril correcto ya existe: `server.py:1120-1121` emite el `code` de `DayzTestToolError` limpio.
- Traducir el fallo de acreditación a un `DayzTestToolError` con code constante y sin rutas
  (p.ej. `bad_mod_authority`), distinguible del `launcher_hash_drift` de D-40.

## Fuera de alcance

- Bundle nativo, `approved-launchers.json`, reconstrucción/repinado de sha256 (D-40): **ningún**
  fichero tocado está en `_HASHED_MODULES` ni en `_APP_PACKAGED_MODULES`.
- Tablas de política compiladas en `build_native_launcher.py` y el spec de externalización.
- Mod SimpleGroup: cerrado, no se toca.
- Renombrar nombres de mod en fixtures: hipótesis muerta (11 fallos + 3 errores), no reintentar.

## Gates de aceptación

1. **Suite completa.** Baseline: 1362 tests / 1 fallo / 4 skipped. El único fallo admitido es
   `test_native_launcher_bundle.test_request_policy_is_canonical_closed_and_sealed` (deuda
   preexistente, `%TEMP%` de julio). Cualquier otro fallo bloquea.
2. **Tests nuevos** en `tools/tests/test_request_path_authority.py`:
   - junction hoja bajo raíz `allow_root_junction:true` → acredita, y la identidad devuelta es la
     del destino;
   - el mismo junction bajo raíz `allow_root_junction:false` → rechazado;
   - symlink de directorio en la hoja → rechazado (no basta con el test existente: cubre raíz);
   - junction en componente **intermedio** → rechazado;
   - absoluta fuera de `mod_roots` desde la superficie MCP → rechazada.
3. **Censos congelados** de `tools/tests/` actualizados en el **mismo** cambio.
4. **Gate in-game:** `dayz_test_run(project=…, mode="server", base_mods=["@Dabs Framework"])`
   arranca. Verificación de que el fix es real y no un test verde: la copia `@Dabs_CodexW1` deja de
   hacer falta.
5. **Error tipado:** un mod inexistente devuelve el code nuevo, no `dayz_test_failed:ValueError`.

## Riesgo principal

Es un gate de seguridad fail-closed (G6). Un discriminador mal escrito abre la puerta a acreditar
rutas fuera de las raíces selladas. Mitigación: replicar el patrón ya existente en vez de escribir
uno nuevo, + los cuatro tests negativos del gate 2, + un pase de revisión (AGENTS-R21) antes de dar
el cambio por cerrado.

## Aviso de entorno

`P:` es un `subst` que no sobrevive a reinicio. Si falta, el daemon responde `daemon_unavailable`:

```
subst P: "C:\Users\guill\OneDrive\Documentos\DayZ Projects"
```
