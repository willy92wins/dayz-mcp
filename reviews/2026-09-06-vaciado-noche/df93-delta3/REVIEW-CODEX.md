DICTAMEN: NO ES SEGURO INTEGRAR — R3-F-02 CIERRA; R3-F-01 NO CIERRA. Los repros originales ya rechazan, pero un comentario entre `publishedid` y `=` oculta una segunda asignación viva y conserva el falso verde.

# Alcance y evidencia de entrada

Revisión ciega delta-3, ronda 4 del lote df93, ejecutada sobre `fd660d2 -> 180815efef0f98905a3b966bf5220a68a5b29ac6`, rama `work/df93-vpp-preflight`. Fecha de la sesión: 2026-09-06.

Se leyeron `REVIEW-CODEX-delta2.md`, `DELTA-R4.patch`, `COMMITS.txt`, el producto y tests actuales, y `CIERRE.md` / `GATES.md` del lote. La orden actual autoriza expresamente esta ronda: la disposición histórica «sin ronda 4» de esos documentos no se interpreta como una prohibición de ejecutar este encargo.

- HEAD y ambos SHA-256 coinciden con los solicitados.
- El diff de Git coincide con `DELTA-R4.patch` al normalizar CRLF a LF. Sólo cambia los dos ficheros anunciados; `git diff --check fd660d2 HEAD`: salida vacía, exit 0.
- Revisión de una sola lane, sin subagentes, sin modificar producto salvo los tres mutantes solicitados. No se ejecutaron juego, daemon, launcher nativo ni suite completa. No se adquirió lease ni se tocó una sesión MCP.
- Se ejecutaron los cuatro cuerpos Python [EXACT] de delta-2, 91 filas E1/E2 ampliadas, tres mutantes del fichero real, rojo/verde de los tests R4 y regresión acotada. Las matrices usan configuraciones sintéticas en memoria. El primer repro original y los tests que necesitan disco usan temporales dentro de este cwd; el cuerpo original se conservó y se cambió sólo el destino global de `tempfile`.
- El runner impide crear procesos operativos y conectar a servicios. Permite únicamente el socket interno de `asyncio` creado por `socket._fallback_socketpair`; las rutas de lanzamiento de los tests usan sus dobles originales. No se parcheó el resultado del preflight para los repros ni para el oráculo VPP.

SHA-256 del árbol revisado, iniciales y finales:

```text
ac33f7adcf6992f0968e0eb7c07839dcdc9d2c2907c84abbb926ea8108552f05  tools/dayz_mcp/native_launcher_transaction.py
0dcc46c4d3db745fda49460cf4f933eee8ae01776de430b5e49b32c2f1cac6bf  tools/tests/test_vpp_preflight.py
```

# Convergencia de los hallazgos de delta-2

## R3-F-01 — NO CIERRA como familia; sus repros originales sí quedan corregidos

La implementación cumple la mecánica anunciada: `tools/dayz_mcp/native_launcher_transaction.py:390` rechaza overflow; `:392` llama al scanner; `:393` exige una sola captura y el decimal canónico exacto. El regex crudo de identidad desapareció.

Los dos repros originales de identidad, reejecutados sin cambiar sus cuerpos Python (`delta2-exact-1.py` y `delta2-exact-2.py`), dieron literalmente:

```text
foreign_marker= foreign mod
error_code= vpp_preflight_failed missing= ('vpp_mod_identity',)
exit=0
```

```text
comment_id_live_foreign: error=vpp_preflight_failed missing=('vpp_mod_identity',) meta_chars_seen=62
other_key_id_live_foreign: error=vpp_preflight_failed missing=('vpp_mod_identity',) meta_chars_seen=62
two_ids_correct_then_foreign: error=vpp_preflight_failed missing=('vpp_mod_identity',) meta_chars_seen=59
giant_late_foreign: error=vpp_preflight_failed missing=('vpp_mod_identity',) meta_chars_seen=262145
exit=0
```

También rechazan los anteriores falsos verdes E1: ID dentro de otra clave, duplicados contiguos contradictorios, sufijo `evil`, ID en terreno muerto y prefijo gigante. Sin embargo, «exactamente una captura» no equivale todavía a «exactamente una asignación viva»: el scanner omite una segunda asignación cuando su clave y `=` están separados por un comentario. R4-F-01 demuestra la falta de cierre sin depender de qué asignación priorice el motor.

## R3-F-02 — CIERRA el defecto concreto de las comillas simples

El conjunto de delimitadores incluye ambas comillas en `tools/dayz_mcp/native_launcher_transaction.py:163`; el recorrido `:299-305` conserva el delimitador con el que abrió y consume hasta su cierre o EOF. El repro exacto `motd='vppDisablePassword=1;';` ya no produce una asignación viva (`delta2-exact-3.py`):

```text
live= []
error_code= vpp_preflight_failed missing= ('vpp_disable_password',)
exit=0
```

Los controles pasan cuando una asignación viva sigue a una cadena cerrada; una comilla simple dentro de un comentario o un apóstrofo dentro de una cadena doble no ocultan esa asignación. Una cadena simple sin cerrar ANTES de la única clave devuelve `values=[]` y rechaza. M2 acredita que quitar el delimitador simple pone los tests R4 en rojo.

Precisión necesaria: «una cadena sin cerrar hace rechazar el fichero» no es verdad para cualquier posición. Si ya se capturó una asignación válida ANTES de la cadena sin cerrar, el resultado permanece verde tanto para cfg como para meta. Delta-2 ya dejó el caso doble como conjetura sobre el consumidor; aquí se reproduce también con comilla simple y se mantiene como límite, no como un nuevo bloqueo sin prueba del motor.

# Hallazgo nuevo / persistencia de la familia bloqueante

## R4-F-01 — ALTA — Los comentarios entre tokens hacen incompleto el censo de asignaciones

- Tipo concreto: **degradation / falso verde del preflight**, no crash ni excepción del producto.
- Ubicación: `tools/dayz_mcp/native_launcher_transaction.py:160`, `:314-323` y el nuevo consumidor `:392-393`. El rechazo de identidad depende de esa lista en `:432-439`; el cfg usa la misma lista en `:334-335` y `:452-453`.
- Mecanismo leído: al ver la clave, `:318` exige inmediatamente el patrón `\s*=\s*([^;\s]+)`. Un `/*...*/` o `//...\n` entre clave y `=` no encaja. El recorrido avanza, consume el comentario en `:306-313` y llega al `=` sin recordar la clave. La segunda asignación desaparece del censo. El comentario termina antes del operador y del valor: no convierte esa asignación en texto comentado.
- Consecuencia ejecutada: dos `publishedid` vivos, también dos idénticos, pueden producir una sola captura y `error=None`. Esto incumple la unicidad exigida por esta ronda, independientemente del orden de resolución del motor. Con `vppDisablePassword/*x*/=0` se oculta asimismo una asignación contradictoria y el cfg pasa.
- Relación con el delta: el falso verde de duplicados sigue existiendo desde R3; R4 corrige la forma contigua pero no la forma con comentario. La misma incoherencia introduce además un falso rechazo nuevo en meta: `publishedid=1828439124/*x*/;` pasaba en fd660d2 y ahora se captura como `1828439124/*x*/`, rechazando identidad; con un espacio antes del comentario sí pasa. Agrupo ambos efectos porque proceden del mismo lector de valores sin estados léxicos, no los cuento como familias distintas.

REPRO EJECUTADO — [EXACT], guardado en `focused_repro.py`. Ejecutar desde este cwd con `PYTHONPATH=<worktree>\tools` y el intérprete solicitado; todos los ficheros del preflight son dobles en memoria:

```python
"""Executed [EXACT] reproduction for R4-F-01. No host reads, processes or engine."""
import ntpath
from dayz_mcp import native_launcher_transaction as t
from tests.test_vpp_preflight import _policy, _healthy

p = _policy()
candidate = r"P:\Mods\1828439124"
payload = {"mode": "server", "base_mods": [], "extra_mods": [candidate], "mod": p.mod, "dev_root": p.dev_root}
meta_path = ntpath.normcase(ntpath.join(candidate, "meta.cpp"))
cfg_path = ntpath.normcase(ntpath.join(p.dev_root, "_server", "serverDZ.cfg"))
for label, meta, cfg in (
    ("meta_duplicate_control", "publishedid=1828439124; publishedid=1559212036;", "vppDisablePassword=1;"),
    ("meta_duplicate_block_comment", "publishedid=1828439124; publishedid/*x*/=1559212036;", "vppDisablePassword=1;"),
    ("meta_duplicate_line_comment", "publishedid=1828439124; publishedid//x\n=1559212036;", "vppDisablePassword=1;"),
    ("meta_duplicate_identical", "publishedid=1828439124; publishedid/*x*/=1828439124;", "vppDisablePassword=1;"),
    ("meta_value_comment_control", "publishedid=1828439124 /*x*/;", "vppDisablePassword=1;"),
    ("meta_value_comment_adjacent", "publishedid=1828439124/*x*/;", "vppDisablePassword=1;"),
    ("cfg_conflict_control", "publishedid=1828439124;", "vppDisablePassword=1; vppDisablePassword=0;"),
    ("cfg_conflict_block_comment", "publishedid=1828439124;", "vppDisablePassword=1; vppDisablePassword/*x*/=0;"),
):
    f = _healthy(p)
    f.files[meta_path] = meta
    f.files[cfg_path] = cfg
    out = t.evaluate_vpp_preflight(payload, p, files=f)
    print(f"{label}: meta_values={t._live_assignments(meta, 'publishedid')!r} cfg_values={t._live_assignments(cfg, t._VPP_KEY)!r} error={out.error_code!r} missing={out.missing!r} warnings={out.warnings!r}")
```

Salida literal de HEAD (`focused-repro.log`):

```text
meta_duplicate_control: meta_values=['1828439124', '1559212036'] cfg_values=['1'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
meta_duplicate_block_comment: meta_values=['1828439124'] cfg_values=['1'] error=None missing=() warnings=()
meta_duplicate_line_comment: meta_values=['1828439124'] cfg_values=['1'] error=None missing=() warnings=()
meta_duplicate_identical: meta_values=['1828439124'] cfg_values=['1'] error=None missing=() warnings=()
meta_value_comment_control: meta_values=['1828439124'] cfg_values=['1'] error=None missing=() warnings=()
meta_value_comment_adjacent: meta_values=['1828439124/*x*/'] cfg_values=['1'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
cfg_conflict_control: meta_values=['1828439124'] cfg_values=['1', '0'] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
cfg_conflict_block_comment: meta_values=['1828439124'] cfg_values=['1'] error=None missing=() warnings=()
```

Control temporal: mismo repro con el producto de fd660d2 cargado en memoria, sin tocar el worktree (`focused-repro-fd660d2.log`):

```text
meta_duplicate_control: meta_values=['1828439124', '1559212036'] cfg_values=['1'] error=None missing=() warnings=()
meta_duplicate_block_comment: meta_values=['1828439124'] cfg_values=['1'] error=None missing=() warnings=()
meta_duplicate_line_comment: meta_values=['1828439124'] cfg_values=['1'] error=None missing=() warnings=()
meta_duplicate_identical: meta_values=['1828439124'] cfg_values=['1'] error=None missing=() warnings=()
meta_value_comment_control: meta_values=['1828439124'] cfg_values=['1'] error=None missing=() warnings=()
meta_value_comment_adjacent: meta_values=['1828439124/*x*/'] cfg_values=['1'] error=None missing=() warnings=()
cfg_conflict_control: meta_values=['1828439124'] cfg_values=['1', '0'] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
cfg_conflict_block_comment: meta_values=['1828439124'] cfg_values=['1'] error=None missing=() warnings=()
```

Fix sugerido — [DESIGN]: reconocer comentarios como separadores de tokens también durante una asignación, conservar la clave pendiente hasta resolver `=` y su valor, y terminar el token numérico antes de un comentario. Rechazar cuando el análisis de una asignación de la clave no sea verificable. No basta con contar la lista producida por el regex actual. Los controles verificables son: ambos duplicados meta con comentario rechazan; el cfg contradictorio con comentario rechaza; el único ID canónico seguido de comentario con o sin espacio produce el mismo verde. No se aplica ningún fix en esta revisión.

No se afirma aquí que el engine elija el ID ajeno ni que arranque con una contraseña concreta: lo probado es que el preflight acredita entradas que su contrato de unicidad/conflicto exige rechazar. El bloqueo tiene repro ejecutado, no depende de esa atribución al motor.

# Respuestas E1-E2 — matrices completas y ampliaciones

Las 91 filas y sus salidas literales van a continuación. `values` es la salida del scanner; `error`, `missing` y `warnings` son el resultado completo. Los inputs exactos, incluidos BOM, NUL, caracteres Unicode, CRLF y las fronteras de tamaño, están en `matrices.py` y en `matrices.json`; `matrices-literal.txt` conserva esta salida por separado. En los cuatro casos de encoding de cada fichero, se ejecutó el lector real sobre `BytesIO`/`TextIOWrapper`, con su UTF-8, `errors="replace"` y traducción de saltos de línea originales. «Como directorio» e «ilegible» inyectan respectivamente `IsADirectoryError` y `PermissionError` en el seam.

## E1 — formas de meta.cpp

```text
E1/canonical: values=['1828439124'] error=None missing=() warnings=()
E1/canonical_spaced: values=['1828439124'] error=None missing=() warnings=()
E1/quoted_double: values=['"1828439124"'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/quoted_single: values=["'1828439124'"] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/hexadecimal: values=['0x6cfbc454'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/leading_zero: values=['01828439124'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/larger_number_suffix: values=['18284391240'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/larger_number_prefix: values=['91828439124'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/missing: values=None error='vpp_preflight_failed' missing=('vpp_mod_folder',) warnings=()
E1/comment_id_live_foreign: values=['1559212036'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/other_key_id_live_foreign: values=['1559212036'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/two_ids_correct_then_foreign: values=['1828439124', '1559212036'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/malformed_suffix: values=['1828439124evil'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/giant_late_foreign: values=['1828439124'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/utf8_bom: values=['1828439124'] error=None missing=() warnings=()
E1/uppercase_key: values=[] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/two_identical_assignments: values=['1828439124', '1828439124'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/signed_plus: values=['+1828439124'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/signed_minus: values=['-1828439124'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/tabs_newlines: values=['1828439124'] error=None missing=() warnings=()
E1/unicode_nbsp: values=['1828439124'] error=None missing=() warnings=()
E1/unicode_thin_space: values=['1828439124'] error=None missing=() warnings=()
E1/zwsp_before_value: values=['\u200b1828439124'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/comment_same_line: values=['1828439124'] error=None missing=() warnings=()
E1/comment_after_value_spaced: values=['1828439124'] error=None missing=() warnings=()
E1/comment_after_value_adjacent: values=['1828439124/*'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/single_quote_in_line_comment: values=['1828439124'] error=None missing=() warnings=()
E1/single_quote_in_block_comment: values=['1828439124'] error=None missing=() warnings=()
E1/apostrophe_in_double_string: values=['1828439124'] error=None missing=() warnings=()
E1/double_quote_in_single_string: values=['1828439124'] error=None missing=() warnings=()
E1/crlf: values=['1828439124'] error=None missing=() warnings=()
E1/single_string_only: values=[] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/double_string_only: values=[] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/unterminated_single_before: values=[] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/unterminated_single_after: values=['1828439124'] error=None missing=() warnings=()
E1/unterminated_double_after: values=['1828439124'] error=None missing=() warnings=()
E1/unterminated_block_after: values=['1828439124'] error=None missing=() warnings=()
E1/exact_cap: values=['1828439124'] error=None missing=() warnings=()
E1/over_cap: values=['1828439124'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/comment_between_key_equal_single: values=[] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
E1/duplicate_foreign_commented_key: values=['1828439124'] error=None missing=() warnings=()
E1/duplicate_identical_commented_key: values=['1828439124'] error=None missing=() warnings=()
E1/duplicate_foreign_line_comment: values=['1828439124'] error=None missing=() warnings=()
E1/duplicate_foreign_uppercase: values=['1828439124'] error=None missing=() warnings=()
E1/number_then_whitespace_suffix: values=['1828439124'] error=None missing=() warnings=()
E1/number_without_semicolon: values=['1828439124'] error=None missing=() warnings=()
E1/nul_prefix: values=['1828439124'] error=None missing=() warnings=()
E1/nested_class_only: values=['1828439124'] error=None missing=() warnings=()
E1-encoding/utf-8-sig: values=['1828439124'] error=None missing=() warnings=() bytes_hex=efbbbf7075626c697368656469643d313832383433393132343b0a
E1-encoding/utf-16: values=[] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=() bytes_hex=fffe7000750062006c0069007300680065006400690064003d0031003800320038003400330039003100320034003b000a00
E1-encoding/utf-16-le: values=[] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=() bytes_hex=7000750062006c0069007300680065006400690064003d0031003800320038003400330039003100320034003b000a00
E1-encoding/utf-16-be: values=[] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=() bytes_hex=007000750062006c0069007300680065006400690064003d0031003800320038003400330039003100320034003b000a
E1/meta_as_directory: values=None error='vpp_preflight_failed' missing=('vpp_mod_folder',) warnings=()
E1/meta_unreadable: values=None error='vpp_preflight_failed' missing=('vpp_mod_folder',) warnings=()
```

Interpretación: decimal canónico, espacios ordinarios, BOM UTF-8 y CRLF pasan; valores quoted, hexadecimales, con signo, ceros iniciales o sufijos rechazan; UTF-16 rechaza sin excepción. Dos asignaciones idénticas contiguas rechazan como pide R4. El tope exacto pasa y tope+1 rechaza. El comentario posterior al punto y coma pasa. Las variantes con comentario entre tokens se clasifican en R4-F-01.

Los verdes de mayúscula contradictoria, clase anidada, NUL, token con sufijo separado por espacio, punto y coma ausente y sintaxis sin cerrar quedan registrados, pero no se presentan como bugs adicionales sin aislar las convenciones del consumidor. Los rechazos conservadores de UTF-16/quoted/hex se conservan como límites de compatibilidad, igual que en delta-2.

## E2 — scanner del cfg y fronteras

```text
E2/block_marker_inside_double: values=['1'] error=None missing=() warnings=()
E2/unterminated_double_before: values=[] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
E2/multiline_double_closed: values=['1'] error=None missing=() warnings=()
E2/line_marker_inside_double: values=['1'] error=None missing=() warnings=()
E2/comment_inside_key: values=[] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
E2/crlf: values=['1'] error=None missing=() warnings=()
E2/utf8_bom: values=['1'] error=None missing=() warnings=()
E2/nul_before_key: values=['1'] error=None missing=() warnings=()
E2/nul_inside_key: values=[] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
E2/single_quoted_only_exact: values=[] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
E2/unterminated_double_after: values=['1'] error=None missing=() warnings=()
E2/unterminated_single_before: values=[] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
E2/unterminated_single_after: values=['1'] error=None missing=() warnings=()
E2/apostrophe_in_double: values=['1'] error=None missing=() warnings=()
E2/single_quote_in_line_comment: values=['1'] error=None missing=() warnings=()
E2/single_quote_in_block_comment: values=['1'] error=None missing=() warnings=()
E2/single_string_then_live: values=['1'] error=None missing=() warnings=()
E2/double_string_then_live: values=['1'] error=None missing=() warnings=()
E2/live_then_single_string: values=['1'] error=None missing=() warnings=()
E2/both_strings_then_live: values=['1'] error=None missing=() warnings=()
E2/single_string_then_live_zero: values=['0'] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
E2/two_live_identical: values=['1', '1'] error=None missing=() warnings=()
E2/two_live_conflicting: values=['1', '0'] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
E2/uppercase_only: values=[] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
E2/conflicting_uppercase: values=['1'] error=None missing=() warnings=()
E2/signed_plus: values=['+1'] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
E2/unicode_nbsp: values=['1'] error=None missing=() warnings=()
E2/comment_after_statement: values=['1'] error=None missing=() warnings=()
E2/comment_after_value_adjacent: values=['1/*x*/'] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
E2/conflicting_commented_key: values=['1'] error=None missing=() warnings=()
E2/exact_cap: values=['1'] error=None missing=() warnings=()
E2/over_cap: values=['1'] error='vpp_preflight_failed' missing=('server_config_unverifiable',) warnings=()
E2/cr_line_comment: values=[] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
E2-encoding/utf-8-sig: values=['1'] error=None missing=() warnings=() bytes_hex=efbbbf76707044697361626c6550617373776f72643d313b0a
E2-encoding/utf-16: values=[] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=() bytes_hex=fffe760070007000440069007300610062006c006500500061007300730077006f00720064003d0031003b000a00
E2-encoding/utf-16-le: values=[] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=() bytes_hex=760070007000440069007300610062006c006500500061007300730077006f00720064003d0031003b000a00
E2-encoding/utf-16-be: values=[] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=() bytes_hex=00760070007000440069007300610062006c006500500061007300730077006f00720064003d0031003b000a
```

Interpretación: se preservan todos los controles E2 anteriores y se corrige la cadena simple. Repetir una clave dentro de cadenas simples/dobles y fuera de ellas sólo cuenta las apariciones externas; dos valores vivos `1` pasan y `1`/`0` rechazan. El comentario entre clave y operador es la excepción bloqueante documentada en R4-F-01. El caso CR sin LF se observó directamente en el scanner; el lector host sí normaliza CR mediante su apertura de texto, por lo que esa observación aislada no demuestra una regresión de la ruta real.

# Tests nuevos, mutantes y restauración exacta

Se confirma el rojo/verde anunciado, con una precisión de conteo: `VppPreflightRound4Test` contiene **4 métodos y 18 subcasos** (11 de intención negativa + 7 controles positivos), no 11 subcasos totales. Cargados sobre el producto fd660d2 en memoria: **4 tests, 11 failures, 0 errors**; sobre HEAD: **4 tests, 18 subcasos, 0 fallos**. Los 11 rojos incluyen 8 negativos de meta, 2 controles positivos de meta y el caso de comilla simple sin cerrar; los otros dos negativos de cfg ya rechazaban en R3. Evidencia: `red-first-fd660d2.log`, `round4-current.log`, `prior.json`.

Oráculo completo inicial: **53 tests, OK**, 0 skips. Tras restaurar el último mutante: **53 tests, OK**, 0 skips. No se alteró ningún aserto ni fixture del test para ejecutar mutantes.

Los siguientes mutantes se escribieron realmente sobre el producto del worktree, uno por uno. Cada ejecución usó un intérprete nuevo y un prefijo de caché distinto con escritura de bytecode desactivada; por tanto no se reutilizó un `.pyc` del producto sano. `try/finally` restauró los bytes originales tras cada corrida; se comprobó igualdad de bytes además del SHA-256. Copia de recuperación local: `native_launcher_transaction.original.bin`.

| Mutante | Cambio [EXACT] sobre las líneas leídas | Tests ejecutados | Métodos que caen | Failures/subcasos | Errors | Exit |
|---|---|---:|---:|---:|---:|---:|
| M1 | `:393`: `len(values) == 1` → `len(values) >= 1` | 53 | 1 | 1 | 0 | 1 |
| M2 | `:163`: quitar `chr(39)` de los delimitadores | 53 | 2 | 2 | 0 | 1 |
| M3 | eliminar `:390-391`, rechazo por tope de meta | 53 | 1 | 1 | 0 | 1 |

Los tres son cazados por la clase R4. Salidas fallidas concretas:

```text
M1-duplicate-accepted: ran=53 failures=1 errors=0 skipped=0 exit=1
RED tests.test_vpp_preflight.VppPreflightRound4Test.test_meta_cpp_forms_that_do_not_prove_the_identity [two_live_ids_correct_first]
SHA256_BEFORE=ac33f7adcf6992f0968e0eb7c07839dcdc9d2c2907c84abbb926ea8108552f05
SHA256_MUTANT=7da6b5f152be8457771a7d98deeeff1c6cd22e786f3b5dd58768de8d263cfc30
SHA256_AFTER=ac33f7adcf6992f0968e0eb7c07839dcdc9d2c2907c84abbb926ea8108552f05
RESTORED_BYTE_IDENTICAL=True
```

```text
M2-single-quote-ignored: ran=53 failures=2 errors=0 skipped=0 exit=1
RED tests.test_vpp_preflight.VppPreflightRound4Test.test_a_key_inside_a_single_quoted_string_is_dead_ground [unterminated_single_quote]
RED tests.test_vpp_preflight.VppPreflightRound4Test.test_meta_cpp_forms_that_still_prove_the_identity [foreign_id_only_in_a_single_quoted_string]
SHA256_BEFORE=ac33f7adcf6992f0968e0eb7c07839dcdc9d2c2907c84abbb926ea8108552f05
SHA256_MUTANT=d9ddd66c93ddcb2b1613a38a8fcaeb03d8e8d05b521d18628dd5fdb05f7cf8d6
SHA256_AFTER=ac33f7adcf6992f0968e0eb7c07839dcdc9d2c2907c84abbb926ea8108552f05
RESTORED_BYTE_IDENTICAL=True
```

```text
M3-meta-cap-removed: ran=53 failures=1 errors=0 skipped=0 exit=1
RED tests.test_vpp_preflight.VppPreflightRound4Test.test_meta_cpp_forms_that_do_not_prove_the_identity [past_the_read_cap]
SHA256_BEFORE=ac33f7adcf6992f0968e0eb7c07839dcdc9d2c2907c84abbb926ea8108552f05
SHA256_MUTANT=61d09bdaccf005f807aecfbaa635a65a6fa378095f4cbb828d590af930fa0d94
SHA256_AFTER=ac33f7adcf6992f0968e0eb7c07839dcdc9d2c2907c84abbb926ea8108552f05
RESTORED_BYTE_IDENTICAL=True
```


Límite de cobertura detectado, sin elevarlo a otro bug: dos negativos nuevos de comilla simple no incluyen el punto y coma dentro de la cadena del repro delta-2; sin soporte de comilla simple ya rechazan porque capturan `1'`. M2 es cazado por el negativo sin cierre y por el positivo de identidad con ID ajeno dentro de cadena simple. El repro exacto con punto y coma interior se ejecutó aparte y quedó corregido.

También se reejecutó el mutante E6 [EXACT] de delta-2, que fuerza el modo servidor a False sólo en memoria:

```text
MUTANT mode_starts_server_ALWAYS_FALSE ran=53 failures=45 errors=0 skipped=0
RED tests.test_vpp_preflight.VppPreflightChokepointTest.test_a_server_request_without_admin_tools_never_takes_a_lease
RED tests.test_vpp_preflight.VppPreflightDecisionTest.test_a_config_bigger_than_the_read_cap_is_refused
RED tests.test_vpp_preflight.VppPreflightDecisionTest.test_a_directory_with_no_meta_cpp_is_refused
RED tests.test_vpp_preflight.VppPreflightDecisionTest.test_a_foreign_directory_with_the_right_name_is_refused (entry='P:\\Mods\\1828439124')
RED tests.test_vpp_preflight.VppPreflightDecisionTest.test_a_foreign_directory_with_the_right_name_is_refused (entry='P:\\Mods\\@VPPAdminTools')
RED tests.test_vpp_preflight.VppPreflightDecisionTest.test_a_key_that_only_lives_in_a_comment_does_not_pass (body='// vppDisablePassword = 1;\nvppDisablePassword = 0;\n')
RED tests.test_vpp_preflight.VppPreflightDecisionTest.test_a_key_that_only_lives_in_a_comment_does_not_pass (body='/* vppDisablePassword = 1; */\nvppDisablePassword = 0;\n')
RED tests.test_vpp_preflight.VppPreflightDecisionTest.test_a_key_that_only_lives_in_a_comment_does_not_pass (body='// vppDisablePassword = 1;\n')
RED tests.test_vpp_preflight.VppPreflightDecisionTest.test_a_preflight_request_fails_exactly_where_a_launch_would
RED tests.test_vpp_preflight.VppPreflightDecisionTest.test_a_relative_entry_with_several_roots_is_refused
RED tests.test_vpp_preflight.VppPreflightDecisionTest.test_a_single_root_resolves_a_relative_entry_exactly
RED tests.test_vpp_preflight.VppPreflightDecisionTest.test_absent_credentials_file_only_warns
exit=1
```

# Regresión acotada y diagnóstico de entorno

| Corrida | Resultado ejecutado | Evidencia |
|---|---|---|
| `tests.test_vpp_preflight`, HEAD | 53, OK | `baseline-vpp.log` |
| Mismo módulo después de restaurar | 53, OK | `restored-vpp.log` |
| Los cuatro módulos solicitados, juntos | 129 tests; 0 failures, 8 errors en 7 métodos | `regression-four.log` |
| Mismos cuatro, producto fd660d2 en memoria | 129 tests; los mismos 8 errors en los mismos 7 métodos | `regression-four-fd660d2.log` |
| `test_dayz_test_tool`, `test_dayz_test_tool_modes`, `test_lifecycle_reconcile`, separados del módulo bloqueado | 122, OK | `regression-three.log` |
| `test_dayz_test_value_error_codes` + `test_lote_w_h9` | 10, OK | `scanner-extra.log` |

**Los 129 OK declarados no son reproducibles con los permisos de esta sesión.** Los 7 métodos restantes pertenecen a `test_native_launcher_transaction`: la fixture falla al sellar sus raíces, antes de ejecutar el preflight. Traza leída: `tools/tests/test_native_launcher_transaction.py:89` → `tools/dayz_mcp/request_path_authority.py:323` → `:193-203`. El handle devuelto al abrir un ancestro es inválido. Una envoltura diagnóstica que sólo registra y relanza el error capturó literalmente:

```text
AUTHORITY_OPEN_FAILED='C:\Users\guill' winerror=5
ValueError: invalid_dayz_test_path_authority
```

El mismo resultado con fd660d2 y el fallo anterior a la ruta del preflight lo clasifican como **SETUP-FAILED**, no regresión R4. No se relajó esa autoridad ni se inventó un verde con un mock. Los 8 errores corresponden a 7 métodos porque un método tiene subcasos.

La primera versión del runner del revisor también rechazó el socket interno de `asyncio` y produjo 5/65 errores antes de ejecutar tests asíncronos. El traceback señaló `C:\Python314\Lib\socket.py:629` y el propio runner; se permitió ese único socketpair y se repitieron ambas corridas. Logs iniciales preservados en `setup-runner-vpp.log`, `setup-runner-regression.log` y `setup-runner.json`; no son resultados del producto.

Dependencias del scanner: la búsqueda de sus dos consumidores y sus helpers en todos los `.py` de `tools/tests` sitúa las comprobaciones conductuales directas en `test_vpp_preflight`, más la fixture real de `test_native_launcher_transaction`. Los otros tres módulos solicitados sustituyen el preflight en sus tests de orquestación. Se añadió el censo de tokens y la excepción H9, ambos offline. `test_secure_launcher` sustituye la transacción en sus dos rutas y no ejecuta este scanner. No se corrió la suite completa ni módulos que levantan daemons.

# Contrato y asertos de rondas anteriores

- **Tokens:** no cambia ningún token de `missing` o `warnings`, ni el error `vpp_preflight_failed` ni el texto de remediación. Comparación de AST/listas en `contract.json`, además de lectura del diff. El conjunto es:

```text
missing: vpp_mod_not_requested, vpp_mod_root_ambiguous, vpp_mod_identity, vpp_mod_folder,
         server_config_unreadable, server_config, server_config_unverifiable, vpp_disable_password
warnings: vpp_superadmins_absent, vpp_credentials_absent
```

- **Descripción de tools:** sin cambio en R4. `tools/dayz_mcp/server.py` y `tools/dayz_mcp/dayz_test_tool.py` son byte-idénticos a fd660d2. La descripción vive en `server.py:3189` y su entorno; ese fichero también es idéntico a la base 24919cb.
- **Sellado:** ningún fichero tocado en R4, ni en el lote completo sobre 24919cb, pertenece a los 17 miembros reales de `app.pyz`; R4 tampoco intersecta los 15 `PACKAGED_MODULES` leídos en `tools/build_native_launcher.py:53`. Se leyó el ZIP; no se dedujo su contenido sólo de Git. SHA-256 observado de `app.pyz`: `225b31a5e24255285174554e4b71e818619455e1169e9f312cee101a238f8efd`.
- **Pins:** `secure_launcher.py` y `dependency-lock.json` son byte-idénticos a fd660d2 y a 24919cb. El pin de `tools/dependency-lock.json:24-30` coincide con el fichero real: **9594 bytes**, SHA-256 `D4139911A21E28499E96B4FE7FA9C1CFF5B26C7683D5B3A98FF2B07283CD3705`. SHA-256 del lock: `f1c682da3d8c0dedf57be3abfffd353cde0c95d4810f953487cdc244db9176ba`.
- **Bundle completo:** la verificación read-only `tools/build_native_launcher.py:892` vuelve a terminar con `ValueError: closure_size`. Medido contra el manifiesto: `src/launcher.cpp expected=66310 actual=64784`. Es el mismo límite de checkout ya documentado en delta-2/CIERRE; este dictamen no convierte «no tocado por el delta» en «bundle íntegramente verificado».
- **Asertos anteriores:** no se debilita ninguno. El fichero completo de tests de fd660d2 es un prefijo byte-idéntico del actual; la comparación AST también preserva todos sus nodos. R4 sólo añade la clase de `tools/tests/test_vpp_preflight.py:1146`. Los 49 tests previos siguen dentro de los 53 ejecutados. Permanecen verdes los controles del tope cfg, multi-raíz, modos sin servidor, embudo y ausencia de escritura del preflight.

# Cierre y evidencia durable

| Familia | Resultado de esta ronda |
|---|---|
| R3-F-01 / identidad inequívoca | **NO CIERRA**: corrige los repros previos, pero R4-F-01 oculta duplicados con comentario |
| R3-F-02 / cadena simple como terreno muerto | **CIERRA** el defecto concreto, con repro original y mutante ejecutados |
| R2-F-03B / tope cfg | Conserva cierre: exacto pasa, tope+1 rechaza |
| R2-F-04 / multi-raíz | Conserva cierre en el oráculo anterior intacto |

Disposición: **NO INTEGRAR / ORCHESTRATOR_NEEDED**, con R4-F-01 como evidencia nueva de la familia pendiente. Esta revisión no inicia otra ronda ni aplica reparaciones.

Se adjuntan los scripts reproducibles, logs y JSON en este cwd. No se actualizó el vault ni el cierre compartido: la frontera del encargo permite escribir sólo aquí, salvo las mutaciones temporales explícitas. Este dictamen y sus evidencias son la memoria durable de la revisión para que el orquestador los promueva. El fichero de salida se escribe como `EXIT-DELTA3.txt = DONE` después de verificar la existencia del dictamen y los hashes restaurados.

# LO QUE NO PUDE VERIFICAR

1. **Los 129 tests verdes completos**: 122 pasan; 7 métodos no superan la fixture de autoridad Win32, con 8 errores por acceso denegado al ancestro `C:\Users\guill`. Se aisló y reprodujo con el producto previo. No se obtuvieron privilegios ni se sustituyó la comprobación que falla.
2. **Bundle válido de extremo a extremo**: `verify_bundle` se detiene en `closure_size` con el drift de tamaño detallado. Sí se verificaron los pins y la ausencia de cambios a módulos sellados.
3. **El parser real del engine y el comportamiento in-game**: no se lanzaron por instrucción. No quedan acreditados el tratamiento de NUL, mayúsculas, scopes de clases, whitespace Unicode, tokens inválidos o strings sin cerrar después de una asignación válida. Sus salidas del preflight están registradas; no se presentan como hechos del consumidor.
4. **La suite completa y las diez instalaciones/policies vivas**: fuera del alcance de esta corrida offline sobre fixtures. E1 se reejecutó completa en sus formas de `meta.cpp`, que es la matriz pedida; el censo operativo de diez policies de delta-2 no se actualiza. El gate in-game G-VPP sigue sin acreditarse.
