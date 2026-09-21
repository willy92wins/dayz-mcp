# Purga del directorio de trabajo — secuencia ejecutable (N-8)

> Analizado por Grok en solo lectura sobre el clon público, **corregido contra el árbol
> privado** por el orquestador. Cierra el bloqueante N-8 de
> [`plans/2026-08-21-hoja-de-ruta-presentacion.md`](2026-08-21-hoja-de-ruta-presentacion.md).
> Estado: **bloque A ejecutado y verificado 2026-08-21**; B y C pendientes.

## Siete correcciones al análisis

Las dos primeras salieron al leer el árbol privado; la 3 y la 4, al ejecutar el bloque A y
correr su gate. Las tres últimas tumbaron el bloque B entero y viven en su tabla de
veredictos, más abajo: `mcp_client.py` tiene un consumidor vivo, `p0s_test_runner.py` es una
CLI en uso, y los tres `_audit_*.py` no existen.

**1. `p0s_gate.py` NO se borra.** Estaba en la lista de candidatos y es un falso positivo:
lo invoca el instalador en `install_mcp.py:1089` (ruta), `:1106` (argv `backup-runs-v1`) y
`:1179` (`run_runs_backup_gate` dentro de `--register`). Borrarlo rompe `--register` con
`runs_backup_gate_missing`. El nombre es de fase; el contrato es de instalador. Se queda.

**2. `spike0/` es un taller vivo en el árbol privado, no un directorio muerto.** El clon
público solo recibe `tools/spike0/mcp-grab.ps1` (lo dice `tools/publish/boundary.py:226`),
así que Grok vio un directorio de un solo fichero. En el árbol real hay además
`mcp-grab-diag.ps1`, `mcp-grab-diag-analyze.py`, `spike0-window-enum.ps1`,
`spike0-grab.ps1`, `spike0-ping*.{ps1,py}`, `spike0-token-calib.py`, `MCPTest_Ping_*.c`,
`README.md` y `_grabdiag/`. **`spike0/` NO se borra**; solo sale de ahí el script de
producción.

Y dos de esos scripts esperan `mcp-grab.ps1` como **hermano**:
`spike0/mcp-grab-diag.ps1:22` y `spike0/spike0-window-enum.ps1:86`, ambos con
`Join-Path $PSScriptRoot 'mcp-grab.ps1'`. Mover sin tocarlos los rompe.

**3. `included.json` no se edita a mano: lo GENERA `boundary.py:437`.** El plan pedía
tocar los dos ficheros. Editar el JSON es a la vez inútil (la siguiente corrida lo pisa)
y peligroso (queda desincronizado del manifiesto que sí se revisa). Se edita `boundary.py`
y se **re-corre**; escribe `MANIFEST.md` + `included.json` y no toca el árbol del proyecto.

Y hay un segundo efecto que el plan no vio: al pasar el fichero de `spike0/` a `tools/`
raíz cae en el bucle de *loose files* (`boundary.py:256`), que lo clasifica con
`classify_tools_file` → **por defecto excluido**. Así que no basta con repuntar la ruta en
la entrada explícita: hay que meterlo en `TOOLS_IN` **y quitar la entrada explícita**, o el
mismo fichero sale listado en `included` *y* en `excluded`.

**4. `task9_build_a_smoke.py` NO se borra** (estaba en la lista B5). El commit `e106cf2`,
que es HEAD, lo trajo al repo dev junto con su test a propósito. Y no puede publicarse
nunca: hardcodea **seis rutas absolutas del autor** (`:60,63,69,72,75`) y la clase de un
mod privado (`OBJECT_TYPE = "MERCEDES_AMGLF"`, `:32`). Lo correcto no es borrarlo sino
excluirlo de la publicación — que ya lo estaba — y excluir **también su test**, cosa que
faltaba. Ver el bloque A ejecutado.

---

## Bloque A — Sacar el script de producción del spike (7 ficheros)

El árbol no puede quedar un instante sin `mcp-grab.ps1` en la ruta que `GRAB_SCRIPT`
nombra: **copiar → repuntar → borrar el origen**, nunca al revés.

| # | Acción | Fichero |
|---|---|---|
| A1 | Copiar `tools/spike0/mcp-grab.ps1` → `tools/mcp-grab.ps1` | (ambos existen un momento) |
| A2 | `GRAB_SCRIPT` pasa a `os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp-grab.ps1")` | `tools/mcp_capture.py:86` |
| A3 | Reescribir el comentario que dice «lives in spike0/…» | `tools/mcp_capture.py:82-85` |
| A4 | `Join-Path $PSScriptRoot '..\mcp-grab.ps1'` | `tools/spike0/mcp-grab-diag.ps1:22` |
| A5 | Igual | `tools/spike0/spike0-window-enum.ps1:86` |
| A6 | Repuntar la ruta publicada a `tools/mcp-grab.ps1` | `tools/publish/boundary.py:226` y `tools/publish/included.json:181` |
| A7 | Borrar el original | `tools/spike0/mcp-grab.ps1` |

Comentarios que citan la ruta vieja y conviene actualizar, sin urgencia:
`tools/dayz_mcp/server.py:3002`, `tools/tests/test_d05_capture_targets_run_client.py:10`,
`tools/spike0/mcp-grab.ps1:3` (su propia cabecera).

**Gate del bloque A**: `python -c "import mcp_capture; import os;
print(os.path.exists(mcp_capture.GRAB_SCRIPT))"` → `True`, y
`tests.test_mcp_capture` en verde (usa la constante, no la ruta literal, así que la sigue).
`tools/publish/` tiene su propia suite de export: correrla.

### Estado: EJECUTADO 2026-08-21

Aplicado en el orden copiar → repuntar → borrar, con backup de cada fichero en
`%TEMP%\...\scratchpad\_backups_fase0\blockA\`.

| Gate | Resultado |
|---|---|
| `GRAB_SCRIPT` resuelve a fichero real | `…\tools\mcp-grab.ps1`, `os.path.exists` → True |
| `tests.test_mcp_capture` + `test_d05_capture_targets_run_client` | 9/9 OK |
| `boundary.py` re-corrido, diff de `included.json` | exactamente `+ tools/mcp-grab.ps1` / `− tools/spike0/mcp-grab.ps1` |

`A6` se aplicó como dice la corrección 3: entrada en `TOOLS_IN`, entrada explícita
eliminada, `included.json` regenerado (no editado).

### Dos defectos que destapó el gate, ajenos al bloque A

**Backups del bridge dentro de la frontera de publicación.** `is_write_artifact`
(`boundary.py:35`) mira `Path.suffix` y solo entiende el marcador pegado con punto
(`MCPBridge.c.bak_pre_fencing_20260819` → suffix `.bak_…`). Los pegados con guion bajo
dejan el rabo entero dentro de `suffix` (`MCPBridge.c_bak_infdrive_20260820` → suffix
`.c_bak_…`) y se colaban. **Dos backups del bridge, 85 kB de fuente, estaban dentro de la
frontera.** Es la misma clase que el propio comentario del guard presume de haber cazado
(«27 of these were inside the boundary», 2026-08-19); simplemente conocía una sola
convención de nombre. Arreglado con `GLUED_WRITE_ARTIFACT = re.compile(r"_(bak|orig|rej)(_|$)")`
y probado contra los **cinco** backups reales de `DayZ_MCP\scripts\5_Mission\` (los 5
cazados) más seis negativos que no deben caer (`notes.backup_data`, `archive.tar_baker`,
`test_bak.py`…). Efecto medido: 246 → 244 ficheros, 4,85 → 4,76 MB, y el diff del
`included.json` son exactamente esos dos ficheros y nada más.

**El *import check* estaba roto en HEAD.** `MANIFEST.md` del 20-ago decía `- none`; al
re-correr salió `BROKEN -> tools/tests/test_task9_build_a_smoke.py imports excluded module
task9_build_a_smoke`. La regresión entró con `e106cf2` y nadie re-corrió la frontera
después. **El siguiente export habría publicado un repo cuya suite no arranca** — el fallo
exacto que esta purga viene a evitar. Cerrado metiendo el test en `TESTS_OUT` con el
mecanismo que ya existía para los tests de `tools/_*` (corrección 4).

Tras las dos: `import check: clean`, 243 ficheros, private hits 5 → **3**, portfolio
exposure 27 → 26, y el `artifact-read check` sigue en 16 — los mismos 16 de antes, ninguno
nuevo. Los 16 son deuda conocida: 11 leen el bundle `native-launchers` (excluido a
propósito, su fuente viaja aparte) y 5 son de `mcp_client.py`, que el bloque B borra.

---

## Bloque B — RETIRADO tras verificarlo contra el árbol de hoy

> Verificado 2026-08-21, fichero a fichero, con `git ls-files | grep` sobre el árbol real.
> **De los doce candidatos sobrevive uno.** El bloque original está en el backup
> `purga-plan.pre_blockB_rewrite.md`; se conserva la tabla de veredictos porque el valor
> está en por qué cada uno era un falso positivo.

El análisis de origen buscaba ficheros **sin importadores** sobre el **clon público**. Dos
sesgos, y los dos muerden:

1. **Un punto de entrada CLI nunca tiene importadores.** Se invoca, no se importa. Es el
   mismo fallo que ya había obligado a la corrección 1 con `p0s_gate.py`, y volvió a pasar
   dos veces más abajo.
2. **El clon público es un commit viejo.** Lo que HEAD añadió después no está, así que
   código con consumidores nuevos parece huérfano.

| Candidato | Veredicto | Evidencia |
|---|---|---|
| `tools/mcp_client.py` | **NO se borra** | `task9_build_a_smoke.py:3260` hace `importlib.import_module("mcp_client")` y llama a ocho APIs suyas (`:3285`, `:3298`, `:3335`, `:3352`, `:3552`, `:3558`, `:3771`, `:3798`) |
| `tools/task9_build_a_smoke.py` + su test | **NO se borran** | los trajo HEAD `e106cf2` a propósito. No publicables (corrección 4) pero vivos en el repo dev |
| `tools/p0s_test_runner.py` + su test | **NO se borra** | CLI viva: `argparse` en `:282`, `main(argv)` en `:352`, `__main__` en `:357`. Es el runner **obligatorio** del gate P0.S, citado en ocho briefs y reports de `.superpowers/sdd/` («el runner obligatorio es `tools/p0s_test_runner.py` con lista explícita de tests»). Tercera repetición del falso positivo CLI |
| `_audit_paths.py`, `_audit_imports.py`, `_audit_md_refs.py` | **no existen** | `find . -name "_audit_*"` → cero resultados en todo el árbol. El borrado era un no-op |
| B1 — quitar `RUNTIME_HTTP_EXCLUSIONS` | **NO se toca** | la exclusión existe *porque* existe `mcp_client.py` (`security_runtime_audit.py:67-81`), que se queda |
| B2 — quitar el test de la exclusión | **NO se toca** | ídem |
| `run-fase1/2/3.ps1`, `run-poc.ps1` | **bloqueados** | son la tupla `evidence` que justifica esa exclusión (`security_runtime_audit.py:75-80`). Borrarlos deja una exclusión de auditoría de seguridad **sin justificación documental**, que ante un revisor escéptico es peor que el desorden que venía a arreglar |
| `tools/run-s0-gate.ps1` | borrable | cero referencias en el árbol. Único superviviente, y no vale un bloque |

### Qué reemplaza al bloque B

El bloqueante N-8 decía «el directorio de trabajo del autor se publica». El mecanismo que
decide eso **no es borrar ficheros del árbol privado**: es `tools/publish/boundary.py`, que
ya clasifica todo y trae dos chequeos de consistencia propios. El árbol privado puede estar
tan desordenado como quiera mientras la frontera sea correcta y esté verde.

Y hoy no lo estaba, en dos sitios distintos, los dos encontrados al re-correrla (ver arriba).
Ninguno se habría visto borrando ficheros.

Así que N-8 se cierra con un **gate**, no con una purga:

1. `boundary.py` se re-corre y su *import check* sale `clean` **antes de cada export**.
   Hoy nada obliga a eso: el `MANIFEST.md` del 20-ago decía `- none` mientras el árbol ya
   estaba roto, porque nadie lo volvió a correr.
2. Ese re-run debería ser un test de la suite, no un comando que alguien recuerda. **La
   maquinaria que decide qué se publica no tiene ni un test** — `tools/publish/` está
   entero sin trackear y sin cobertura. Es el candidato número uno de la fase 1.
3. Los 16 problemas del *artifact-read check* son deuda conocida y **no** son ruido: 11
   leen el bundle `native-launchers` (excluido a propósito; su fuente viaja aparte) y 5 son
   de `mcp_client.py`, que ahora sabemos que se queda. Hay que decidirlos de una vez y
   dejarlos documentados, o el chequeo se vuelve un semáforo que nadie mira.

## Bloque C — ya no depende de B

**S-5, el tag `[MCP-POC]` → `[DayZ-MCP]`** (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:3490`).
Con el bloque B retirado, los seis `.ps1` se quedan, así que el renombrado deja de ser «espera
a que los borren» y pasa a ser un cambio coordinado de dos lados: el tag que el bridge
imprime y el patrón que cada script grepea. Sigue siendo mecánico — un `[MCP-POC]` →
`[DayZ-MCP]` en el bridge y en los mismos sitios que ya están listados:
`run-fase1.ps1:207,232,250,428`, `run-fase2.ps1:134,152,327`, `run-fase3.ps1:134,152,329,369`,
`run-poc.ps1:207,232,250,426,545,694,725`, `run-s0-gate.ps1:73` y
`spike0/mcp-grab-diag.ps1:130`. Ningún `.py` del runtime lo toca.

Los siete ficheros se cambian a la vez o no se cambia ninguno: un bridge que imprime
`[DayZ-MCP]` mientras los scripts grepean `[MCP-POC]` deja los gates de fase mudos, y el
síntoma —un `wait_for` que expira sin motivo— no señala a la causa.

**Coste de verificación**: los scripts solo se ejercitan in-game, así que el cambio es
textualmente comprobable ahora (grep de que ningún lado se quedó con el tag viejo) pero solo
se valida de verdad en la siguiente corrida in-game. Por DZ-R5 va agrupado con el resto de
lo que necesite juego, no en una corrida propia.

**Por qué merece la pena**: `[MCP-POC]` es lo que el servidor escupe a `script.log` en cada
línea. Ante un público que va buscando motivos para descartarlo, la propia herramienta
declarándose *proof of concept* en cada log es la crítica escrita por la casa.

---

## Lo que este análisis no cubre

- **El export público no se reprodujo.** Todo lo de `tools/publish/` está razonado por
  lectura; la prueba es correr el export y diffear el resultado contra el repo publicado.
- **`spike0/` en conjunto**: decidí conservarlo, pero nadie ha revisado si el resto de sus
  scripts siguen sirviendo para algo. Si no, es otra purga — pero del árbol privado, que no
  ve nadie, así que no es urgente.
- **La tercera copia de la struct Win32** vive en `tools/build_native_launcher.py`. Ya es
  `c_ubyte` (correcta), así que no era el defecto, pero sigue duplicada y ahora hay un
  módulo compartido al que podría importar: `dayz_mcp/win32_fileinfo.py`.
- **`_FILE_ATTRIBUTE_TAG_INFO`** sigue repetida en `pinned_keyfile.py` y
  `request_path_authority.py`. Idéntica en ambas, así que no es un bug; es la misma fábrica
  de duplicación que produjo S-2.
