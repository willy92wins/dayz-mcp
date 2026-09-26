DICTAMEN: SEGURO INTEGRAR — la ronda 5 implementa las reglas del contrato (brief §3) sin desviación observada en 70 casos límite ejecutados, cierra R4-F-01 con su repro literal (4/4 filas rechazan, la adyacente pasa), no altera tokens ni ficheros fuera del write-set, y los dos hallazgos (BAJA) son de evidencia y de cobertura de tests, no del producto; integrar copiando los dos ficheros de ws, no aplicando `DIFF-R5-tests.patch`, que se generó contra un worktree transitoriamente sucio y muestra un borrado que Grok nunca hizo.

Revisor: Anthropic (Claude), ciego respecto al implementador (Grok/Cursor, `run1/MODEL = cursor-grok-4.6-xhigh`). Fecha: 2026-09-06. Nada de ws ni del worktree fue modificado; todos los scripts y salidas viven en este directorio (`review-r5-anthropic/`). Hallazgos: 0 ALTA, 0 MEDIA, 2 BAJA.

Rutas (abreviadas en el resto del documento):
- `<ws>` = `C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r5\ws`
- `<wt>` = `C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\e89adcdf-b230-4060-83b7-f733911e169a\scratchpad\wt-df93` (HEAD `180815efef0f98905a3b966bf5220a68a5b29ac6`, rama `work/df93-vpp-preflight`)
- `<rev>` = `C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\review-r5-anthropic`
- `<py>` = `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe` (Python 3.14.3)

# 1. Resumen de medidas (qué, cómo, resultado)

| Medida | Cómo | Resultado |
|---|---|---|
| Módulo de tests | `cd <ws>\tools && PYTHONPATH=. <py> -m unittest tests.test_vpp_preflight` | `Ran 57 tests in 2.399s` / `OK` / exit 0 |
| Regresión | mismo cwd, `-m unittest tests.test_native_launcher_transaction tests.test_dayz_test_tool tests.test_dayz_test_tool_modes tests.test_lifecycle_reconcile` | `Ran 129 tests in 1.411s` / `OK` / exit 0 (sin «acceso denegado») |
| Repro del dictamen | `PYTHONPATH=. <py> ..\SPEC-focused_repro.py` sobre el producto ws | 8 líneas idénticas al bloque DESPUÉS de `STATE.md` (diff tras normalizar CR: IDENTICAL); ver §2 |
| Repro sobre HEAD | mismo script con el blob `HEAD:tools/dayz_mcp/native_launcher_transaction.py` cargado en la copia `<rev>\mut\tools` | 8 líneas idénticas al bloque ANTES de `STATE.md` y a `SPEC-REVIEW-CODEX-DELTA3.md:103-110` |
| sha256 write-set | `sha256sum` en ws | producto `079c6737502255b01aaf89ef73e782e451abd099d404df0f550c02ca5035d3b5`, tests `c1e6a65f5b5513d75adc8b93b0d6c9f351c8b373ae019c275b4688ec57b5a391`: ambos coinciden con `STATE.md` |
| Producto ws vs HEAD | `git show HEAD:… \| diff -u - <ws>/…` comparado hunk a hunk con `DIFF-R5-product.patch` | SAME hunks (el patch de producto es fiel) |
| Tests ws vs HEAD | idem sobre `test_vpp_preflight.py` | 0 líneas borradas; solo se añade `VppPreflightRound5Test` (:1249-1371, 4 métodos, 14 subcasos) |
| Árbol ws vs worktree | `diff -rq -x .git -x __pycache__ -x .pytest_cache <wt> <ws>` | 7 líneas: los 2 ficheros del write-set; `BRIEF.txt`, `SPEC-REVIEW-CODEX-DELTA3.md`, `SPEC-focused_repro.py`, `STATE.md` solo en ws; `tools/.venv-mcp` solo en worktree. Ningún otro fichero difiere (medido a las 17:56 y a las 18:15; el fichero de tests del worktree cambió de estado entre ambas medidas, ver R5-A-01) |
| Tokens | `grep -o -F` de los 11 literales (`vpp_mod_not_requested`, `vpp_mod_root_ambiguous`, `vpp_mod_identity`, `vpp_mod_folder`, `server_config_unreadable`, `server_config_unverifiable`, `"server_config"`, `vpp_disable_password`, `vpp_superadmins_absent`, `vpp_credentials_absent`, `vpp_preflight_failed`) en ws y en el blob HEAD | recuento 1 = 1 en los once; el diff no toca ninguno |
| Referencias | `grep -rn --include=*.py '_VPP_VALUE\|_live_assignments\|_skip_non_string_dead_ground' <ws>\tools` (sin .venv) | `_VPP_VALUE` no tiene ningún otro consumidor (borrarlo es seguro); `_live_assignments` solo en `:370` y `:428` |
| Rojo-primero | tests ws sobre el producto HEAD (copia) | `ran=57 failures=10`, las mismas 10 etiquetas que lista `STATE.md` |
| Mutantes (a)(b)(c) de STATE.md | reimplementados sobre copia | 8 / 2 / 4 rojos, mismas etiquetas que `STATE.md` |
| Casos límite propios | `<rev>\probe_edges.py` (38 meta + 32 cfg) sobre ws y sobre HEAD | ws: 0 desviaciones del contrato, 5 preguntas; HEAD: 17 desviaciones (las que R4-F-01 describe) |
| Mutantes propios | 6 (`A1`-`A6`) sobre copia, hash restaurado | 4 sobreviven a los 57 tests (huecos de cobertura; producto correcto en cada entrada), 2 controles cazados |

# 2. Cierre de R4-F-01 — repro literal [EXACT]

Comando: `cd <ws>\tools && PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 "<py>" ..\SPEC-focused_repro.py` (exit 0). Salida:

```text
meta_duplicate_control: meta_values=['1828439124', '1559212036'] cfg_values=['1'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
meta_duplicate_block_comment: meta_values=['1828439124', '1559212036'] cfg_values=['1'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
meta_duplicate_line_comment: meta_values=['1828439124', '1559212036'] cfg_values=['1'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
meta_duplicate_identical: meta_values=['1828439124', '1828439124'] cfg_values=['1'] error='vpp_preflight_failed' missing=('vpp_mod_identity',) warnings=()
meta_value_comment_control: meta_values=['1828439124'] cfg_values=['1'] error=None missing=() warnings=()
meta_value_comment_adjacent: meta_values=['1828439124'] cfg_values=['1'] error=None missing=() warnings=()
cfg_conflict_control: meta_values=['1828439124'] cfg_values=['1', '0'] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
cfg_conflict_block_comment: meta_values=['1828439124'] cfg_values=['1', '0'] error='vpp_preflight_failed' missing=('vpp_disable_password',) warnings=()
```

Los tres `meta_duplicate_*` con comentario y `cfg_conflict_block_comment` rechazan; `meta_value_comment_adjacent` pasa. Es exactamente el criterio de cierre que fija el dictamen (`SPEC-REVIEW-CODEX-DELTA3.md:126`).

# 3. Contraste del diff de producto con el contrato (brief §3)

Diff leído entero (`DIFF-R5-product.patch`, 94 líneas; hunks idénticos a `git show HEAD … | diff`). Cambios: comentario de `_VPP_KEY` (`:155-161`), borrado de `_VPP_VALUE`, nueva `_skip_non_string_dead_ground` (`:279-296`), docstring y cuerpo de `_live_assignments` (`:299-360`). `_vpp_password_is_disabled` (`:363-371`) y `_proves_vpp_identity` (`:413-429`) no aparecen en el diff. Ninguna firma pública cambia.

| Regla del contrato | Código en `<ws>\tools\dayz_mcp\native_launcher_transaction.py` | Casos que la ejercen (§5) | Veredicto |
|---|---|---|---|
| Clave exacta con frontera izquierda | `:338-341` (sin cambio de lógica) | M35, C30; mutante control A6 cazado por `VppPreflightDecisionTest` | conforme |
| Terreno muerto SIN cadenas entre clave y `=` (blancos, `//` hasta LF, `/*` hasta `*/` o EOF) | `:342` → `_skip_non_string_dead_ground` `:284-295`; una comilla detiene el salto (`:295` `break`) y `:343` exige `=` | M11, M13, M30, M31, M32, M27, M28, C10, C12, C31 | conforme |
| Terreno muerto sin cadenas entre `=` y valor | `:344` | M08, M09, M12, C07, C08, C11, C28 | conforme |
| Valor = secuencia máxima hasta el primero de `;`, blanco, `/*`, `//`, EOF | `:346-353` | M01, M07, M15, M16, M17, M33, C01, C16, C29 | conforme (`1/` y `=1` son valores, como dice la regla: solo `/*` y `//` cortan) |
| Valor vacío = no hay asignación | `:355` | M05, M06, M22, C05, C14, C32 | conforme |
| Sin `=` tras clave + terreno muerto → no es asignación; sin heurísticas de recuperación | `:343` falla → `:359` `index += 1` | M02, M03, M04, M10, M38, C02, C03, C04, C09 | conforme |
| Cadenas `'`/`"` como terreno muerto fuera de asignaciones; sin cerrar ocultan lo que sigue; valor entrecomillado conserva comillas | `:324-329` (sin cambio); valor `:346-353` no trata comillas | M20, M21, M26, M35, C15, C24, C31; E1/E2 `unterminated_*_after` del dictamen siguen en verde (Round4 tests OK) | conforme |
| Docstring: cadena sin cerrar oculta lo que sigue; el gate rechaza solo si no capturó nada antes | `:314-316` | texto verificado literal | conforme (precisión pedida en R3-F-02) |
| `_proves_vpp_identity` y `_vpp_password_is_disabled` intactos | `:428-429`, `:370-371` no están en el diff | mutante Gc (`>= 1`) cazado por 4 subcasos | conforme |
| Ningún token de missing/warnings cambia | tabla §1 (11 literales, 1 = 1) | — | conforme |

No se observó ninguna excepción posible en el código nuevo: `config[cursor]` solo se lee bajo `cursor < length` (`:343`, `:346`), `config[index]` en el salto bajo `while index < length` (`:283`), y `str.startswith(_, i)` es seguro con cualquier índice. Coste lineal (el salto tras una clave fallida se vuelve a recorrer una sola vez por el bucle principal; el tope de lectura sigue en `_MAX_PREFLIGHT_READ_CHARS = 262_144`, `:150`).

# 4. Hallazgos

## R5-A-01 — BAJA — tipo: cosmetic (artefacto de evidencia: el patch de tests atribuye a Grok un borrado que no hizo)

- Qué: `DIFF-R5-tests.patch` (junto a ws) muestra como BORRADAS por la ronda 5 la fila `("delta2_repro_semicolon_inside_the_string", "motd='vppDisablePassword=1;';\n")` y su comentario de 7 líneas en `VppPreflightRound4Test`. Grok no borró nada: esas 8 líneas son una modificación SIN commit del worktree base (`<wt>\tools\tests\test_vpp_preflight.py`, mtime 17:53:43, posterior a la copia de ws), y ws se corresponde con el blob de HEAD más la clase Round5. El patch se generó contra un worktree transitoriamente sucio (cronología abajo).
- Dónde: `<wt>\tools\tests\test_vpp_preflight.py:1230-1238` (líneas sin commit); `<ws>\tools\tests\test_vpp_preflight.py` (no las contiene); `DIFF-R5-tests.patch` hunk `@@ -1227,15 +1227,7 @@`.
- Cronología medida (hora local): 17:50:10 Grok escribe el fichero de tests en ws; 17:53:43 mtime del fichero de tests del worktree con las 8 líneas sin commit; 17:54 se generan los dos `DIFF-R5-*.patch`; 17:56 esta revisión observa ` M tools/tests/test_vpp_preflight.py` y `git diff HEAD --stat` = `8 ++++++++`; 17:58:30 el fichero del worktree vuelve a ser byte-idéntico al blob HEAD (sha256 `0dcc46c4d3db745fda49460cf4f933eee8ae01776de430b5e49b32c2f1cac6bf`, `git status --untracked-files=no` vacío) por una acción ajena a esta revisión (aquí solo se ejecutaron `git show/status/diff`, `diff`, `sed`, `grep` sobre el worktree); 18:15 re-medido: `diff -u <wt>\tools\tests\test_vpp_preflight.py <ws>\...` = 0 líneas borradas.
- Repro [EXACT] (estado de las 17:56):
  - `git -C <wt> status --porcelain -- tools/tests/test_vpp_preflight.py` → ` M tools/tests/test_vpp_preflight.py`
  - `git -C <wt> diff HEAD --stat` → `tools/tests/test_vpp_preflight.py | 8 ++++++++` (solo inserciones: la fila y el comentario)
  - `git -C <wt> show HEAD:tools/tests/test_vpp_preflight.py | diff -u - <ws>/tools/tests/test_vpp_preflight.py | grep -E '^-' | grep -vE '^---'` → (vacío: 0 líneas borradas respecto a HEAD)
  - `grep -c delta2_repro <lote>/run1/out.json` → `0` (el implementador nunca vio esa fila)
  - La fila sin commit contra el producto de la ronda 5 (probe C15): `input="motd='vppDisablePassword=1;';\n" values=[] error='vpp_preflight_failed' missing=('vpp_disable_password',)` → rechaza, como exige su aserto (`assertEqual(error_code, VPP_PREFLIGHT_FAILED)` + `assertIn("vpp_disable_password", missing)` en el bucle de `:1239-1248`).
- Consecuencia: `DIFF-R5-tests.patch` no sirve como vehículo de integración ni como evidencia del delta de tests: sobre el worktree actual (limpio) `git apply` rechazaría su hunk (las líneas `-` ya no existen) y, leído como evidencia, hace creer que la ronda 5 eliminó un aserto de la ronda 4 («No borres ni cambies asertos»), cosa que no ocurrió. Integrar copiando `<ws>\tools\tests\test_vpp_preflight.py` (blob HEAD + clase Round5, puramente aditiva al final) o regenerando el patch contra `HEAD:`. La fila `delta2_repro_semicolon_inside_the_string` no está en HEAD 180815e ni en ws: si el orquestador quiere conservarla hay que añadirla aparte; el producto de la ronda 5 la rechaza como exige su aserto (C15).
- No es un defecto del producto ni del trabajo de Grok.

## R5-A-02 — BAJA — tipo: ninguno en el producto (salida verificada conforme al contrato); hueco de tests: cuatro reglas del contrato sin aserto que las fije

- Qué: cuatro mutantes propios que violan reglas explícitas del §3 sobreviven a los 57 tests (`ran=57 failures=0 errors=0 skipped=0` en los cuatro). El producto de la ronda 5 responde bien a todas las entradas (§5), pero nada impide que una edición futura rompa la regla sin poner un test en rojo.
- Dónde (regla → código ws): `:295` (una comilla no es terreno muerto entre tokens), `:349-352` (el valor corta en `//`), `:293` (`/*` sin cerrar entre tokens llega a EOF), `:355` (valor vacío no cuenta).
- Repro [EXACT] (`<rev>\mutate.py`, salida en `<rev>\mutate.out.txt`; la entrada que mata cada mutante procede de `<rev>\probe_edges.<mutante>.out.txt`):

| Mutante | Cambio sobre copia | Tests | Entrada que lo distingue del producto sano | Producto ws | Mutante |
|---|---|---|---|---|---|
| A1 `strings_skipped_between_key_and_equal` | `_skip_non_string_dead_ground` salta también `'…'`/`"…"` | `failures=0` | meta `publishedid/*x*/'='=1828439124;` (M27) | `values=[]` → rechaza | `values=['1828439124']` → **falso verde** |
| A2 `value_not_cut_at_line_comment_only` | el valor deja de cortar en `//` | `failures=0` | cfg `vppDisablePassword=1//x` (C01) | `values=['1']` → pasa | `values=['1//x']` → falso rechazo |
| A3 `unterminated_block_between_tokens_is_two_chars` | `/*` sin cerrar entre tokens salta 2 chars en vez de llegar a EOF | `failures=0` | cfg `vppDisablePassword=/*1;` (C32) | `values=[]` → rechaza | `values=['1']` → **falso verde** |
| A4 `empty_value_counted_as_assignment` | se elimina el `if value:` | `failures=0` | meta `publishedid=1828439124;publishedid=;` (M22) | `values=['1828439124']` → pasa | `values=['1828439124', '']` → falso rechazo |

- Propuesta (no bloqueante, 4 subcasos en `VppPreflightRound5Test`): meta `publishedid/*x*/'='=1828439124;` → `vpp_mod_identity`; meta `publishedid=1828439124//x` → `error_code is None`; cfg `vppDisablePassword=/*1;` → `vpp_disable_password`; meta `publishedid=1828439124;publishedid=;` → `error_code is None`.

# 5. Tabla de casos límite (producto ws) — [EXACT]

Script `<rev>\probe_edges.py`, ejecutado con `cd <ws>\tools && PYTHONPATH=. PYTHONIOENCODING=utf-8 "<py>" <rev>\probe_edges.py` (exit 0; salida íntegra en `<rev>\probe_edges.out.txt`). Cada fila se ejecuta con `_live_assignments` y con `evaluate_vpp_preflight(payload, _policy(), files=_healthy(...))` como en `SPEC-focused_repro.py` (meta: `P:\Mods\1828439124\meta.cpp` con cfg sano `vppDisablePassword=1;`; cfg: `serverDZ.cfg` con meta sano `publishedid=1828439124;`). «Contrato» = lo que decide el §3; «pregunta» = el contrato no lo decide. Resumen: 70 casos, 0 desviaciones, 5 preguntas. La misma tabla sobre HEAD (`<rev>\probe_edges.HEAD.out.txt`) da 17 desviaciones; las 5 preguntas tienen salida idéntica en HEAD y en ws (medido con diff fila a fila), luego no son regresiones de la ronda 5.

| Caso | Fichero | Entrada (repr Python) | `_live_assignments` | error / missing | Veredicto | Contrato |
|---|---|---|---|---|---|---|
| M01_line_comment_at_eof_after_value_no_lf | meta | `'publishedid=1828439124//x'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M02_line_comment_at_eof_between_key_and_equal | meta | `'publishedid//x'` | `[]` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M03_unterminated_block_between_key_and_equal | meta | `'publishedid/*x=1828439124;'` | `[]` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M04_key_at_eof | meta | `'publishedid'` | `[]` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M05_equal_at_eof | meta | `'publishedid='` | `[]` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M06_equal_then_only_comment_to_eof | meta | `'publishedid=/*x*/'` | `[]` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M07_tabs_and_cr | meta | `'publishedid\t=\r\n1828439124\r\n'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M08_semicolon_inside_comment_between_equal_and_value | meta | `'publishedid=/*;*/1828439124;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M09_quoted_value_after_comment | meta | `'publishedid=/*x*/"1828439124";'` | `['"1828439124"']` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M10_key_then_key_without_equal | meta | `'publishedid publishedid=1828439124;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M11_comment_containing_key_between_key_and_equal | meta | `'publishedid/*publishedid=1559212036*/=1828439124;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M12_two_adjacent_block_comments_after_equal | meta | `'publishedid=/*a*//*b*/1828439124;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M13_line_comment_blank_line_then_equal | meta | `'publishedid//c\n\n=1828439124;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M14_nbsp_around_equal | meta | `'publishedid\xa0=\xa01828439124;'` | `['1828439124']` | `None` `()` | PASS | pregunta (contrato no decide) |
| M15_value_without_semicolon_at_eof | meta | `'publishedid=1828439124'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M16_value_then_unterminated_block_at_eof | meta | `'publishedid=1828439124/*'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M17_value_then_single_slash | meta | `'publishedid=1828439124/'` | `['1828439124/']` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M18_double_equal | meta | `'publishedid==1828439124;'` | `['=1828439124']` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M19_slash_star_slash_is_unterminated_opener | meta | `'publishedid=/*/=1828439124;'` | `[]` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M20_single_quoted_value_keeps_quotes | meta | `"publishedid='1828439124';"` | `["'1828439124'"]` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M21_quoted_value_with_space_hides_rest | meta | `'publishedid="1828439124 ";publishedid=1559212036;'` | `['"1828439124']` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M22_second_assignment_with_empty_value | meta | `'publishedid=1828439124;publishedid=;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M23_second_key_comment_then_eof | meta | `'publishedid=1828439124;publishedid/*x*/'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M24_spaced_canonical | meta | `'publishedid = 1828439124 ;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M25_duplicate_with_comments_everywhere | meta | `'publishedid=1828439124;publishedid = /*x*/ 1559212036 /*y*/ ;'` | `['1828439124', '1559212036']` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M26_foreign_in_line_comment_then_canonical | meta | `'//publishedid=1559212036\npublishedid=1828439124;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M27_string_between_key_and_equal | meta | `"publishedid/*x*/'='=1828439124;"` | `[]` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M28_second_key_with_string_before_equal_not_counted | meta | `'publishedid=1828439124;publishedid"x"=1559212036;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M29_cr_only_line_ending_inside_line_comment_hides_second | meta | `'publishedid=1828439124;//x\rpublishedid=1559212036;'` | `['1828439124']` | `None` `()` | PASS | pregunta (contrato no decide) |
| M30_block_comment_ending_with_double_star | meta | `'publishedid/*x**/=1828439124;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M31_line_comment_containing_block_opener | meta | `'publishedid//x/*\n=1828439124;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M32_block_comment_containing_line_opener | meta | `'publishedid/*x//y*/=1828439124;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M33_value_stops_at_form_feed | meta | `'publishedid=1828439124\x0c;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M34_zero_width_space_before_value | meta | `'publishedid=\u200b1828439124;'` | `['\u200b1828439124']` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M35_key_preceded_by_closed_string | meta | `'"x"publishedid=1828439124;'` | `['1828439124']` | `None` `()` | PASS | conforme |
| M36_empty_file | meta | `''` | `[]` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M37_comment_between_key_and_equal_then_foreign_only | meta | `'publishedid/*x*/=1559212036;'` | `['1559212036']` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| M38_key_comment_no_equal_then_value | meta | `'publishedid/*x*/ 1828439124;'` | `[]` | `'vpp_preflight_failed'` `('vpp_mod_identity',)` | REFUSE | conforme |
| C01_line_comment_at_eof_after_value_no_lf | cfg | `'vppDisablePassword=1//x'` | `['1']` | `None` `()` | PASS | conforme |
| C02_line_comment_between_key_and_equal_to_eof | cfg | `'vppDisablePassword//x'` | `[]` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C03_unterminated_block_between_key_and_equal | cfg | `'vppDisablePassword/*x=1;'` | `[]` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C04_key_at_eof | cfg | `'vppDisablePassword'` | `[]` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C05_equal_at_eof | cfg | `'vppDisablePassword='` | `[]` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C06_tabs_and_cr | cfg | `'vppDisablePassword\t=\r\n1\r\n'` | `['1']` | `None` `()` | PASS | conforme |
| C07_semicolon_inside_comment_between_equal_and_value | cfg | `'vppDisablePassword=/*;*/1;'` | `['1']` | `None` `()` | PASS | conforme |
| C08_quoted_value_after_comment | cfg | `'vppDisablePassword=/*x*/"1";'` | `['"1"']` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C09_key_then_key_without_equal | cfg | `'vppDisablePassword vppDisablePassword=1;'` | `['1']` | `None` `()` | PASS | conforme |
| C10_comment_containing_key_zero_between_key_and_equal | cfg | `'vppDisablePassword/*vppDisablePassword=0*/=1;'` | `['1']` | `None` `()` | PASS | conforme |
| C11_two_adjacent_block_comments_after_equal | cfg | `'vppDisablePassword=/*a*//*b*/1;'` | `['1']` | `None` `()` | PASS | conforme |
| C12_line_comment_blank_line_then_equal | cfg | `'vppDisablePassword//c\n\n=1;'` | `['1']` | `None` `()` | PASS | conforme |
| C13_nbsp_around_equal | cfg | `'vppDisablePassword\xa0=\xa01;'` | `['1']` | `None` `()` | PASS | pregunta (contrato no decide) |
| C14_second_assignment_zero_only_inside_comment_empty_value | cfg | `'vppDisablePassword=1;vppDisablePassword=/*0*/;'` | `['1']` | `None` `()` | PASS | conforme |
| C15_worktree_uncommitted_delta2_row | cfg | `"motd='vppDisablePassword=1;';\n"` | `[]` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C16_conflict_then_line_comment_no_lf | cfg | `'vppDisablePassword=1;vppDisablePassword=0//x'` | `['1', '0']` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C17_spaced | cfg | `'vppDisablePassword = 1 ;'` | `['1']` | `None` `()` | PASS | conforme |
| C18_crlf_conflict | cfg | `'vppDisablePassword=1\r\nvppDisablePassword=0\r\n'` | `['1', '0']` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C19_zero_only_in_block_comment | cfg | `'vppDisablePassword=1;/*vppDisablePassword=0;*/'` | `['1']` | `None` `()` | PASS | conforme |
| C20_zero_only_in_line_comment | cfg | `'vppDisablePassword=1;//vppDisablePassword=0;'` | `['1']` | `None` `()` | PASS | conforme |
| C21_cr_only_separator_conflict | cfg | `'vppDisablePassword=1;\rvppDisablePassword=0;'` | `['1', '0']` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C22_cr_only_line_ending_inside_line_comment_hides_zero | cfg | `'vppDisablePassword=1;//x\rvppDisablePassword=0;'` | `['1']` | `None` `()` | PASS | pregunta (contrato no decide) |
| C23_two_identical_ones | cfg | `'vppDisablePassword=1;vppDisablePassword=1;'` | `['1', '1']` | `None` `()` | PASS | conforme |
| C24_quoted_one | cfg | `'vppDisablePassword="1";'` | `['"1"']` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C25_unicode_line_separator_conflict | cfg | `'vppDisablePassword=1\u2028vppDisablePassword=0;'` | `['1', '0']` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C26_line_comment_with_unicode_line_separator_hides_zero | cfg | `'vppDisablePassword=1;\u2028//x\u2028vppDisablePassword=0;'` | `['1']` | `None` `()` | PASS | pregunta (contrato no decide) |
| C27_conflict_zero_first_comment_between | cfg | `'vppDisablePassword/*x*/=0;\nvppDisablePassword=1;'` | `['0', '1']` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C28_conflict_zero_after_line_comment_after_equal | cfg | `'vppDisablePassword=1;\nvppDisablePassword=//x\n0;'` | `['1', '0']` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C29_value_stops_at_line_comment_zero_in_comment | cfg | `'vppDisablePassword=1//0\n'` | `['1']` | `None` `()` | PASS | conforme |
| C30_key_glued_to_previous_value_is_part_of_value | cfg | `'vppDisablePassword=1vppDisablePassword=0;'` | `['1vppDisablePassword=0']` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |
| C31_string_between_key_and_equal_not_counted | cfg | `"vppDisablePassword'x'=0;\nvppDisablePassword=1;"` | `['1']` | `None` `()` | PASS | conforme |
| C32_unterminated_block_after_equal_hides_value | cfg | `'vppDisablePassword=/*1;'` | `[]` | `'vpp_preflight_failed'` `('vpp_disable_password',)` | REFUSE | conforme |

Filas que cambian de HEAD a ws (medido con `diff` de las dos salidas): M01, M06, M08, M09, M11, M12, M13, M16, M19, M25, M30, M31, M32, M37, C01, C07, C08, C10, C11, C12, C14, C16, C27, C28, C29, C32. Todas pasan de una salida contraria al contrato a la salida del contrato; C27 (`vppDisablePassword/*x*/=0;\nvppDisablePassword=1;`) era falso verde en HEAD (`values=['1']`, `error=None`) y ahora rechaza (`values=['0', '1']`).

# 6. Mutantes

Método: copia de `<ws>\tools\dayz_mcp` y `<ws>\tools\tests` (más `h9_native_probe.py`, que un test lee del disco) en `<rev>\mut\tools`; cada mutante se escribe en la copia, se ejecuta `run_one.py` en un intérprete nuevo (`PYTHONDONTWRITEBYTECODE=1`), se restaura con los bytes de ws y se comprueba sha256. Salida literal en `<rev>\mutate.out.txt`; los ficheros mutados quedan en `<rev>\mut\mutants\`. Ni ws ni el worktree se tocaron.

```text
ORIG sha256 (ws product) = 079c6737502255b01aaf89ef73e782e451abd099d404df0f550c02ca5035d3b5
BASELINE (sane copy): ran=57 failures=0 errors=0 skipped=0
HEAD_180815e_product (rojo-primero): ran=57 failures=10 errors=0 skipped=0
  RED …Round5Test.test_cfg_comments_between_tokens_do_not_hide_a_conflicting_assignment [conflict_block_comment]
  RED …Round5Test.test_cfg_comments_between_tokens_do_not_hide_a_conflicting_assignment [conflict_line_comment_then_one]
  RED …Round5Test.test_cfg_comments_next_to_a_live_one_still_disable_the_password [block_comment_between_key_and_equal]
  RED …Round5Test.test_cfg_comments_next_to_a_live_one_still_disable_the_password [value_then_adjacent_block_comment]
  RED …Round5Test.test_meta_cpp_comments_between_tokens_do_not_hide_a_second_assignment [duplicate_foreign_block_comment]
  RED …Round5Test.test_meta_cpp_comments_between_tokens_do_not_hide_a_second_assignment [duplicate_foreign_line_comment]
  RED …Round5Test.test_meta_cpp_comments_between_tokens_do_not_hide_a_second_assignment [duplicate_identical_block_comment]
  RED …Round5Test.test_meta_cpp_comments_next_to_a_single_canonical_value_still_prove_identity [value_then_adjacent_block_comment]
  RED …Round5Test.test_meta_cpp_comments_next_to_a_single_canonical_value_still_prove_identity [block_comment_between_key_and_equal]
  RED …Round5Test.test_meta_cpp_comments_next_to_a_single_canonical_value_still_prove_identity [line_comment_between_key_and_equal]
Ga (= solo tras blancos, reimplementación del (a) de STATE.md): ran=57 failures=8  — las 8 etiquetas de STATE.md
Gb (valor no corta en /* ni //, (b)):                            ran=57 failures=2  — value_then_adjacent_block_comment (cfg y meta)
Gc (len(values) >= 1, (c)):                                      ran=57 failures=4  — Round4 two_live_ids_correct_first + 3 duplicate_* de Round5
A1 strings_skipped_between_key_and_equal:                        ran=57 failures=0  — SOBREVIVE (R5-A-02)
A2 value_not_cut_at_line_comment_only:                           ran=57 failures=0  — SOBREVIVE (R5-A-02)
A3 unterminated_block_between_tokens_is_two_chars:               ran=57 failures=0  — SOBREVIVE (R5-A-02)
A4 empty_value_counted_as_assignment:                            ran=57 failures=0  — SOBREVIVE (R5-A-02)
A5 control: // entre tokens corre hasta EOF:                     ran=57 failures=3  — conflict_line_comment_then_one, duplicate_foreign_line_comment, line_comment_between_key_and_equal
A6 control: sin frontera izquierda:                              ran=57 failures=2  — DecisionTest test_a_live_key_after_dead_ground_still_passes, test_dead_ground_never_counts_as_a_live_key [prefixed_identifier]
FINAL copy sha256=079c6737… expected=079c6737… (restaurado en los 11 casos)
```

Los cuatro supervivientes no son mutantes equivalentes: la tabla de R5-A-02 da, para cada uno, una entrada con veredicto distinto al del producto (A1 y A3 en dirección falso verde; A2 y A4 en dirección falso rechazo).

# 7. STATE.md contra lo medido

| Afirmación de STATE.md | Medida | Cuadra |
|---|---|---|
| sha256 producto `079c6737…d3b5` y tests `c1e6a65f…b391` | `sha256sum` en ws | sí |
| `Ran 57 tests` / `OK` (53 + 4 métodos) | ejecutado | sí (57 = 53 previos + 4 de Round5; ningún skip: `h9_native_probe.py` está en ws) |
| `Ran 129 tests` / `OK`, sin «acceso denegado» | ejecutado | sí |
| Rojo-primero: 10 subcasos, etiquetas listadas; «los otros 4 ya pasaban en HEAD» | tests ws sobre producto HEAD en copia | sí: 10 rojos, mismas etiquetas; 14 − 10 = 4 |
| Bloque ANTES del repro | comparado con `SPEC-REVIEW-CODEX-DELTA3.md:103-110` y con mi ejecución sobre HEAD | idéntico (8/8 líneas, tras normalizar el CR que añade Python en Windows a mi salida) |
| Bloque DESPUÉS del repro | mi ejecución sobre ws | idéntico (8/8) |
| Mutante (a) 8 rojos / (b) 2 / (c) 4, con etiquetas | reimplementados sobre copia | mismos recuentos y etiquetas; los hashes de los mutantes NO son verificables (dependen del texto exacto que escribió Grok; los míos difieren por construcción) |
| Hash restaurado tras cada mutante = `079c6737…` | en mi copia, 11 restauraciones | sí (para mi copia; para el ws el hash final coincide, que es lo que importa) |
| Líneas citadas: producto 151-161, 279-296, 299-360, 314-316, 363-371, 413-429; tests 1249-1371, 1295-1325, 1327-1342, 1344-1362, 1364-1371 | `sed -n` de cada extremo | todas cuadran (`def _skip_non_string_dead_ground` en 279, `return index` en 296, `def _live_assignments` 299, `return values` 360, docstring 314-316, `class VppPreflightRound5Test` 1249, fichero de 1371 líneas) |
| «Cada mutante se aplicó sobre una copia en memoria … y se restauró el fichero con write_bytes(orig)» | — | redacción contradictoria (memoria vs. disco), sin consecuencia: el hash final del fichero coincide |

# 8. Preguntas al contrato (no son hallazgos; salida idéntica en HEAD)

1. `str.isspace()` acepta blancos Unicode (NBSP U+00A0, U+2028…): `publishedid\u00a0=\u00a01828439124;` y `vppDisablePassword\u00a0=\u00a01;` pasan (M14, C13). El §3 dice «blancos» sin definirlos; el regex anterior (`\s`) hacía lo mismo, y el dictamen ya registró `E1/unicode_nbsp` y `E2/unicode_nbsp` en verde. Decidir si «blanco» = ASCII o Unicode.
2. `//` termina en LF, no en CR: `vppDisablePassword=1;//x\rvppDisablePassword=0;` pasa (C22; también M29 y C26 con U+2028). El §3 dice «hasta el fin de línea»; el brief que recibí dice «hasta LF». El dictamen (E2/cr_line_comment) anota que el lector host normaliza CR al abrir en modo texto, así que por la ruta real un CR aislado no llega al scanner; en el scanner puro la observación queda registrada.
3. Decididas por el contrato pero conviene saberlas: un fichero sintácticamente roto pasa si contiene UNA asignación válida y el resto no encaja en la gramática: `publishedid publishedid=1828439124;` (M10, C09), `publishedid=1828439124;publishedid"x"=1559212036;` (M28, C31), `publishedid=1828439124;publishedid=;` (M22, C14: `=/*0*/;` es valor vacío). Es la regla «ante duda, no se cuenta»; la alternativa (rechazar cualquier clave no seguida de asignación) sería una heurística que el §3 prohíbe.

# 9. LO QUE NO PUDE VERIFICAR

- Los sha256 de los tres mutantes de `STATE.md` (`db9ba8…`, `c9f62e…`, `a88c1d…`): su texto exacto no está en el workspace; solo reproduje sus recuentos y etiquetas de rojos con mi propia implementación de cada mutante.
- La suite completa de `tools`, el juego, el daemon, el launcher y cualquier lease MCP: prohibidos por el encargo; no se tocaron.
- El parser real del motor (Enforce/Config Parser) para NBSP, CR aislado, U+2028 y ficheros sintácticamente rotos (§8): fuera del alcance, igual que en el dictamen; las salidas del preflight quedan registradas, no se afirman como hechos del consumidor.
- Bundle `app.pyz`, pins de `secure_launcher.py`/`dependency-lock.json`: no reverificados en esta ronda; lo que sí se midió es que ningún fichero fuera del write-set difiere entre ws y worktree (`diff -rq`).
- El proceso interno del implementador (`run1/out.json`, 891 KB) solo se consultó con `grep -c delta2_repro` (0); no se auditó el resto.
- Los 7 «acceso denegado» del revisor anterior no se reprodujeron en esta máquina (129 OK), por lo que no puedo decir si esa fixture cubre lo mismo en su sandbox.
- Qué proceso revirtió el fichero de tests del worktree a las 17:58:30 y si la fila `delta2_repro_semicolon_inside_the_string` debía conservarse: fuera de mi ventana; solo consta que esta revisión no escribió en el worktree (hashes del write-set de ws intactos al final: `079c6737…`, `c1e6a65f…`).

Ficheros de evidencia en `<rev>`: `probe_edges.py`, `probe_edges.out.txt` (ws), `probe_edges.HEAD.out.txt`, `probe_edges.A1…A4.out.txt`, `mutate.py`, `run_one.py`, `mutate.out.txt`, `mut\mutants\*.py`, `head_native_launcher_transaction.py` (blob HEAD, sha256 `ac33f7adcf6992f0968e0eb7c07839dcdc9d2c2907c84abbb926ea8108552f05`, el mismo `SHA256_BEFORE` del dictamen), `repro_r5.out.txt`, `repro_head.out.txt`.
