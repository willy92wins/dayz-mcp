# Revisión independiente Codex — lotes M y W

Fecha: 2026-09-04  
Referencia declarada: `d0078385de03a0458dd2e42ba775f1858ddd049c`  
Intérprete usado: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe`  
Objeto del dictamen: bytes actuales de `lote-M\ws` y `lote-W\ws`, incluidos los parches correctivos incorporados durante esta revisión. Los `DIFF-*.patch` quedaron anteriores a esas correcciones; no los traté como autoridad sobre el estado final.

## VEREDICTO

BLOQUEANTES=0.

Los cuatro fallos reproducibles encontrados en la primera pasada fueron corregidos por el orquestador mientras la revisión seguía abierta. Releí los parches como código nuevo y repetí los mutantes que los habían descubierto. Los cuatro pasan ahora: P5 ya no congela el enum al importar; W7 conserva la identidad booleana; W8 clasifica como FAIL un manifiesto ilegible; y la remediación W8 ya no usa el SHA del PE como CAS del registro.

## BLOQUEANTES

Ninguno con los bytes finales revisados.

## COBERTURA DEL PRODUCTO

### Lote M

- **P0 — correcto.** Las siete tools se enumeran en `tools/dayz_mcp/server.py:58-67`; `_patch_closed_tool_schema` publica `additionalProperties=false`, materializa `required=[]` y rechaza extras en `server.py:1753-1770`; los wrappers se instalan antes de congelar el registro en `server.py:4626-4630`. Un sondeo con `mcp.shared.memory.create_connected_server_and_client_session` confirmó para las siete: schema cerrado por wire y `isError=True` con `bad_args: unexpected arguments` al enviar `__bogus__`. En `dayz_knowledge_status` y `dayz_knowledge_prepare`, `properties={}` y `required=[]` sobrevivieron a `list_tools()`.

  `arguments_to_pass_directly` no constituye una entrada alternativa del cliente en FastMCP 1.27.2: `Tool.run` entrega el diccionario del cliente como `arguments_to_validate` y forma el cuarto argumento exclusivamente con el `Context` inyectado (`.venv-mcp/Lib/site-packages/mcp/server/fastmcp/tools/base.py:93-106`); la unión del contexto ocurre después de validar (`.../utilities/func_metadata.py:75-94`).

- **P1 — correcto.** `pipeline_resolve` declara `evidence_ref: Annotated[str | None, Field(max_length=240)] = None` y lo reenvía a `inbox.append_resolution` (`server.py:4581-4596`). El schema conserva `required=[feedback_id,resolution]`; omitido y `null` convergen a ausencia; `""`, rutas absolutas y valores de más de 240 caracteres se rechazan.

- **P2 — correcto.** Las dos tools sin argumentos aceptan `{}` y rechazan cualquier propiedad adicional, tanto in-process como por sesión cliente. No encontré alias ni segundo nombre público/interno en esas tools.

- **P3 — correcto.** La descripción pública ya anuncia siempre dos bloques (`server.py:3993-4001`); `crop_space` se declara y se pasa a `capture_dual` (`server.py:4003-4012,4069-4076`); el único retorno exitoso construye `[Image, json]` (`server.py:4088-4095`). El test público simula las ramas `save_fullres=False/True` y verifica `crop_space`, `window_surface`, `client_surface`, `effective_surface`, `frame_sha256` y `fullres_path` (`tools/tests/test_lote_m_products.py:123-160`). `crop_space=bogus` llega como `bad_crop_space`.

  Barrí `tools/` y `playbooks/`: no queda un consumidor activo que exija una Image suelta. `tools/gate4a_mcp_client.py:66-82` itera todos los content blocks y separa texto e imágenes. La forma vieja sólo aparece en copias `*.bak*`, no ejecutadas.

- **P4 — correcto.** Los tipos públicos están en `server.py:55-56`; `ui_click.button` es `StrictInt` y su guard mantiene 0..2 (`server.py:4301-4322`); `ui_reload_layout.mode` limita `reload/close` (`server.py:4346-4355`); la descripción de `ui_click` nombra `mode_not_implemented` (`server.py:4301-4305`). Por sesión cliente rechacé `mode=""` en ambas tools y `button=True`, `"1"`, `1.0`, `-1`, `3`; todos devolvieron `isError=True` antes del body.

- **P5 — correcto tras el parche correctivo.** `dayz_test_run.mode` vuelve a ser `str` (`server.py:2964-2967`) y `_patch_mode_enum_from_authority` obtiene `public_mode_names()` al construir la app y en cada llamada (`server.py:1773-1797`); se instala en `server.py:4626`. Con la autoridad normal el wire publica `all/server/client`, sin `offline`, y rechaza `""`/`offline`. Sustituí `MODE_RECORDS` por una autoridad pública compuesta sólo por `offline`: una app nueva publicó `enum=["offline"]` y una app ya construida rechazó su modo anteriormente válido `server`. La regresión añadida está en `tools/tests/test_lote_m_products.py:181-198`.

  El texto viejo `server|all|client` sólo permanece en validación interna de `dayz_test_tool.py` y en fixtures deliberados de `test_effective_schema.py`; no está pineado en la descripción pública de `dayz_test_run`.

### Lote W

- **W6 — correcto.** La firma real exige `daemon_policy_json` (`tools/dayz_mcp/native_launcher_backend.py:1566-1576`); `h9_native_probe.py` lo serializa y pasa (`:286-300`). `main()` separa `TypeError`, publica `probe_internal_error: TypeError: ...` y retorna 1 (`h9_native_probe.py:380-404`).

- **W7 — correcto tras el parche correctivo.** Comparé los 14 métodos y cada sitio de aserción contra `git show d007838:tools/tests/test_effective_schema.py`. Se conservaron pertenencia/no pertenencia, igualdad, desigualdad, cotas de longitud/conjuntos, `any`, `None`, mensajes y condiciones anidadas. La única conversión inicialmente debilitada, `required is True`, ahora usa identidad con `self.assertIs(True, params["from"]["required"])` (`tools/tests/test_effective_schema.py:21-26`). El mutante `required=1` vuelve a fallar el test; no se perdió ni debilitó ninguna aserción.

- **W8 — correcto tras los parches correctivos.** La costura pura filtra sólo `kind=external`, compara tamaño y SHA, emite INFO con `checked=n` si todo casa y FAIL con `drifted`/`remediation` si falta o deriva (`tools/dayz_mcp/doctor.py:441-482`). `_stat_external_file` calcula el SHA real (`:485-493`). Las fuentes inyectadas tienen `native_launcher_id=None` (`:95-109`) y retornan sin abrir registro ni añadir INFO (`:496-501`); `default_sources` fija `dayz-test-v1` (`:248-264`). `_diagnose` invoca la comprobación en `:1192-1195`; el gating efectivo está dentro de la costura.

  Un launcher/manifiesto ilegible es ahora `NATIVE_BUNDLE_MANIFEST_UNREADABLE` con severidad FAIL (`:517-521`): el repro completo por `doctor.execute` dio `rc=1`, `ok=false`. Para drift, el camino real ya no pasa `opened.sha256` (`:505-515`) y la remediación solicita explícitamente el SHA de `tools/approved-launchers.json` después de `rollback-last` (`:466-473`). El SHA del PE no apareció en el texto generado.

- **W9 — correcto tras el parche correctivo.** La derivación replica `daemon.py`: `package_file.resolve().parents[1]/.venv-mcp/Scripts/python.exe` (`tools/tests/test_interpreter_guard.py:10-16`; autoridad en `tools/dayz_mcp/daemon.py:186-198`). `verdict` resuelve ambos paths y luego usa `normcase` (`test_interpreter_guard.py:19-37`); mismatch nombra ambos y absent produce skip (`:40-55`). El intérprete exacto contra el package vivo dio `ok`. Una ruta equivalente con `..\Scripts\python.exe` también dio `ok` después de resolver.

- **W12 — correcto.** Los dos mensajes dicen ahora `Update PROJECT-MAP.md by hand` y `Update ... by hand or delete the stale lines` (`tools/tests/test_docs_truth.py:632-655`); no recomiendan regenerar el mapa.

### Transversal éxito/fallo

No encontré otra inversión de éxito/fallo causada por los diffs dentro de P0-P5/W6-W12. El caso real que sí invertía el doctor —manifiesto del bundle ilegible clasificado WARN— fue aislado en la primera pasada y ya está corregido a FAIL. Los controles negativos actuales devolvieron error antes de efectos laterales.

### Verificaciones ejecutadas sobre los bytes finales

Desde cada `<ws>\tools`, con `PYTHONPATH=.` y el intérprete obligatorio:

`[EXACT]`

```powershell
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_lote_m_products
# Ran 14 tests — OK

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_doctor tests.test_effective_schema tests.test_interpreter_guard tests.test_lote_w_h9
# Ran 76 tests — OK (skipped=1: la copia W no contiene tools/.venv-mcp)

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_mcp_tools tests.test_pipeline_feedback tests.test_weak_agent_consumer_ux tests.test_mcp_capture
# Ran 104 tests — OK
```

También ejecuté `py_compile` sobre todos los `.py` modificados/nuevos: exit 0. Los sondeos adicionales cubrieron: las siete tools P0 por wire; ambas ramas P3; nueve negativos P4/P5 por wire; sustitución de autoridad P5 en app existente y nueva; mutante de identidad W7; manifiesto ilegible W8 por `doctor.execute`; CAS/remediación W8; fuentes W8 inyectadas con `None`; y equivalencia lexical W9.

Hashes SHA-256 del snapshot revisado:

- M `server.py`: `B414A9B1BF20B151745B9F6D634E2CF2C935625234758ACA220CCD5B0DEDB9D9`
- M `test_lote_m_products.py`: `732984C0900F162E94BC70E6A4DCB396BFDA3D111857CFDA4F0B89A4ECE3291F`
- W `doctor.py`: `5CE7C76D3938027B35548792230BAA03986A61CD4F3FD7B9E421EE3C3D3829D5`
- W `test_effective_schema.py`: `B63752F0A6976B978278560DE677BC726EB56D62B707F6663BF06BED9E3493A9`
- W `test_doctor.py`: `C7328735A856216135B8FEA10FA9A8B4FC104936152C33F0A196B2C1F25953DD`

## BACKLOG

1. **P0 — composición futura alias+cierre permite el nombre interno si se instalan en el orden actual.** `_patch_closed_tool_schema` captura `allowed` antes de que `_patch_public_argument_alias` reescriba las properties (`server.py:1732-1768`). En el orden de `build_app` (`:4627-4629`), una tool sintética terminó con schema `properties=["public"]`, `additionalProperties=false`, pero aceptó y ejecutó `{"internal":7}`. No afecta hoy: las siete tools cerradas y `scene_raycast` son conjuntos disjuntos. Si alguna tool recibe ambos helpers, aplicar alias antes de capturar `allowed` o rechazar expresamente el nombre interno.

2. **W6 — el test de `main()` permite una regresión por propagación.** `tools/tests/test_lote_w_h9.py:50-73` acepta tanto la publicación correcta como `rc == "raised"`; W6 exige publicar `probe_internal_error`, no propagar. El código actual cumple, por lo que no es bloqueante. El test debería exigir `rc==1`, JSON parseable, `ok=false`, prefijo `probe_internal_error`, y ausencia de código de bundle. El censo AST de `:28-48` agrega kwargs de todas las llamadas; sería más preciso comprobar cada call individual.

3. **W8 — falta fijar el contrato CAS en la suite.** `tools/tests/test_doctor.py:1494-1502` sólo busca las palabras `rollback-last` e `install-dayz-test-v1`; no impide volver a interpolar el SHA del PE. Mi mutante adicional sí comprobó que la remediación contiene el placeholder del SHA del registro post-rollback y no el PE. Añadirlo como regresión. Además, `test_unreadable_launcher_is_a_single_warn_finding` (`:1565`) conserva un nombre obsoleto aunque ya asevera FAIL; es sólo cosmético.

4. **Artefactos de revisión desincronizados.** `DIFF-M.patch`/`DIFF-W.patch` son de las 04:52; los correctivos finales modificaron W a las 05:09 y M a las 05:10. El dictamen cubre los workspaces y hashes anteriores, no la completitud de esos patches. Los diffs/ledgers son aparato del orquestador y, por orden expresa, esto no bloquea el producto.

5. **Suite documental amplia fuera del workspace.** `tests.test_docs_truth` completo produjo 2 failures, 3 errors y 3 skips porque la copia W no contiene varios documentos raíz que `PROJECT-MAP` enumera (`dayz-harness-apis.md`, `QUICKSTART.md`, `product-spec.md`, `.gitignore`, entre otros). W12 se verificó por lectura directa. El empaquetado del workspace está fuera del alcance y no se cuenta como defecto del lote.

### Qué medí que no estaba en la medición declarada

- Mutantes de los cuatro fallos de primera pasada, repetidos después de los correctivos.
- Las siete herramientas P0, no sólo una, por sesión MCP real en memoria.
- Cambio de autoridad P5 después de construir una app, además de construir una app nueva.
- Consumidores de la forma P3 en todo `tools/` y `playbooks/`, excluyendo únicamente backups no activos.
- El resultado final `rc/ok/severity` del doctor cuando el manifest no puede abrirse.
- El token semántico de CAS en la remediación W8, no sólo presencia de palabras.
- Una regresión M ampliada de 104 tests y compilación de todos los módulos cambiados.

## LO QUE NO PUDE VERIFICAR

- No pude reproducir en este sandbox el dato host `NATIVE_BUNDLE_EXTERNALS_OK checked=23`. El doctor real desde W no pudo abrir el root registrado y devolvió `NATIVE_BUNDLE_MANIFEST_UNREADABLE` FAIL junto con estado externo no limpio (`CONFIG_PROBE_FAILED`, daemon ilegible, backup slots agotados y runs stale). No atribuyo esos findings al diff.
- No ejecuté juego, red, daemon ni launcher nativo, conforme al encargo. W6 queda probado por firma, unidad y camino de `main()`, no por creación de procesos.
- No pude crear un junction real de prueba: la política del sandbox rechazó la operación. Sí verifiqué que W9 llama `Path.resolve()` en ambos lados y que una ruta lexical con `..` converge a `ok`; el comportamiento con junction se desprende del mismo mecanismo, pero no lo presento como medición host.
- No pude dar verde a `tests.test_docs_truth` completo por los documentos ausentes de la copia; el detalle está en BACKLOG y no afecta W12.
- No actualicé memoria durable en Obsidian porque las únicas raíces de escritura autorizadas son los scratchpads. El hallazgo durable queda en este dictamen.

## PREMISAS DEL ORQUESTADOR QUE CREO FALSAS

Con los **bytes finales**, ninguna premisa funcional de P0-P5/W6-W12 queda refutada. Sí quedaron obsoletas tres descripciones del estado inicial:

1. **«P5 usa `DayzTestRunMode = Literal[*public_mode_names()]` a nivel de módulo».** Era cierto en la entrega inicial y el mutante demostró que una app nueva conservaba el enum congelado. Ya no describe el código final: el alias fue retirado y `server.py:1773-1797,4626` deriva y valida desde la autoridad al construir y en cada llamada. El mismo repro ahora pasa.

2. **«W8 puede tratar un manifest ilegible como WARN».** El código inicial invertía el resultado (`rc=0`, `ok=true` sin haber comprobado externals). Ya no se sostiene: `doctor.py:517-521` emite FAIL y el repro final devuelve `rc=1`, `ok=false`.

3. **«El SHA del ejecutable abierto sirve para `--expected-sha256` tras `rollback-last`».** Era falso: el CAS consume el SHA del registro. El camino final ya no pasa `opened.sha256` y solicita el SHA de `approved-launchers.json` posterior al rollback (`doctor.py:466-473,505-515`).

Matices que no refutan el producto:

- La frase «W8 está cableada en `_diagnose` sólo cuando `native_launcher_id` no es None» no es literal en estructura: `_diagnose` llama siempre en `doctor.py:1194`, y `_check_native_bundle_closure` retorna inmediatamente con `None` en `:499-501`. El comportamiento observable solicitado sí es exactamente el mismo; fuentes inyectadas no abren el host ni reciben INFO.
- La composición alias+cierre no es cerrada en ambos nombres en una hipotética tool compartida, pero hoy no existe esa intersección; queda en BACKLOG y no refuta P0.

El resto de las premisas atacadas se sostiene: `required=[]` cruza el wire; no hay otro consumidor activo de la Image suelta; P4 bloquea vacíos y coerciones; P5 cambia con la autoridad; W7 conserva las aserciones; W8 no inyecta INFO con `native_launcher_id=None`; W9 resuelve y normaliza paths; y W12 cambió exactamente los dos textos.
