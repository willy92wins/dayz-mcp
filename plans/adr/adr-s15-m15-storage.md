# ADR — S15 / M15-STORAGE-CORE

Estado: **autoridad sucesora v9** (autoridad vigente: `20260901-ledger-v5` / `inbox-20260901-v8` / bundle `v9`; predecesor: bundle `v8`).
El bundle `v9` concede a M15 exactamente los dos OWNS de la línea
siguiente, y ninguno más. Ya no son «declarados pero no concedidos».

OWNS: `tools/dayz_mcp/dayz_test_storage.py`; `tools/tests/test_dayz_test_storage.py`

Autoridades de lectura (no write-set de este ADR): plan congelado
`plans/inbox-20260830/20-fb-20260829-115147-4407.md`, ficha candidata
`TEMP/S15-authority-options.md`, H11 en `product-spec.md:146`, addendum
`plans/inbox-20260830/physical-ownership-addendum-v1.md:64`. Este texto
congela el contrato documental de M15. La implementación de esos dos
paths es una fase posterior; este texto no añade un tercer OWNS.

---

### B1 — orden normativo fingerprint / pending / recovery

**CRITICAL**

[EXACT] La matriz A–F clasifica el marker como “Marker exacto”
(`TEMP/S15-authority-options.md:544`) o `inválido/mismatch`
(`TEMP/S15-authority-options.md:552,555`). “Marker exacto” significa bytes
canónicos, keyset/schema válidos, binding y fingerprint esperados,
coherencia completa y ausencia de recovery
(`TEMP/S15-authority-options.md:544-545`).
El journal no está en conflicto: la ficha ya ordena resolverlo antes de
A–F y «nunca se reclasifica un journal como storage ausente»
(`TEMP/S15-authority-options.md:546`); el plan, «Cero permite clasificar»
(`plans/inbox-20260830/20-fb-20260829-115147-4407.md:35`). El conflicto
real es pending: §11 lo procesa sólo sobre marker exacto
(`TEMP/S15-authority-options.md:624`); un mismatch con pending vivo cae
a C/F sin pasar por §11. La coherencia interna del marker, en
`TEMP/S15-authority-options.md:464-477`. La conducta por fallo presupone
que la obligación durable sobrevive a la clasificación
(`plans/inbox-20260830/20-fb-20260829-115147-4407.md:37`).

[DESIGN] El orden nuevo no deja que ningún campo del marker elija ruta.

**DECISIÓN**: congelar este orden normativo, sin excepciones:

1. validar canonical JSON, `schema` / `keyset` / `kind`;
2. recomputar el binding desde los handles, nunca desde el marker;
3. comprobar la coherencia interna roles→fingerprint del propio marker;
4. resolver journal / recovery / pending bajo la autoridad autoconsistente
   del marker (aunque su fingerprint no sea el solicitado). Orden dentro
   del paso 4: (4a) journal activo de **rollback-op** y su recovery; (4b)
   journal activo de rotación y su recovery; (4c) `storage_recovery_required`
   del marker; (4d) pending — y, si tres `terminal` y A=B, el CAS de
   limpieza con preimagen acreditada, con independencia del fingerprint
   solicitado. Recovery de rollback-op **antes** de A–F y
   antes de clasificar storage ausente
   (`plans/inbox-20260830/20-fb-20260829-115147-4407.md:35`;
   `product-spec.md:146`);
5. sólo cuando el estado queda quiescente, comparar con el fingerprint
   *solicitado* y entrar en A–F.

El paso 4 honra la obligación durable **con el fingerprint del marker**, no
con el del caller. 4c no muta el marker current ni storage. 4d **no muta**
el marker current **salvo** el único CAS de limpieza terminal+A=B, con
independencia del fingerprint solicitado (tres observaciones `terminal`,
A=B, preimagen txid+fingerprint+pending ID+rol y SHA/identidad del
receipt): esa escritura pone `rotation_pending=false`, pending nulos y
`storage_rotated=false`. Cualquier otra clasificación en
4c–4d es read-only. 4a y 4b **sí** ejecutan las escrituras journalizadas
de recovery (renames, CAS, completed) de las matrices de rollback-op y
rotación. Toda mutación de C/F **fuera de recovery** queda
bajo el journal de rotación, create-only antes del primer rename
(`TEMP/S15-authority-options.md:564-576`;
`plans/inbox-20260830/20-fb-20260829-115147-4407.md:36`). Un marker de otro
modset con `rotation_pending=true` no es un mismatch que se archive: es un run
pendiente que este marker documenta.

Si se omite: C/F archivan por nombre fijo un marker que porta un
`pending_run_id` vivo; el aviso H11 de reset de mundo/personaje se pierde
en silencio (`product-spec.md:146`).

#### Matriz — orden normativo

| # | paso | fuente de autoridad | qué lo puede abortar | ¿puede un campo del marker elegir ruta? |
|---|---|---|---|---|
| 1 | canonical JSON + schema/keyset/kind | bytes del fichero | `FAIL/storage_marker_invalid` o, en el lector, el código de B4 | no |
| 2 | recomputar binding desde handles | handles de misión y raíz | `storage_binding_mismatch` | **no** — nunca se abre un path del marker |
| 3 | coherencia interna roles→fingerprint | el propio marker | `storage_marker_invalid` | no |
| 4 | journal / recovery / pending (rollback-op → rotación → recovery marker → pending) | autoridad autoconsistente del marker | `INCONCLUSIVE/storage_recovery_required`, `FAIL/storage_pending_run_live`, `INCONCLUSIVE/storage_process_unknown`, `INCONCLUSIVE/storage_filesystem_unverifiable`, `INCONCLUSIVE/storage_fingerprint_mismatch` | no |
| 5 | comparar con fingerprint solicitado → A–F | el caller | según celda | no |

#### Matriz — pending × recovery × fingerprint solicitado

`fp=` igual o distinto respecto al fingerprint que pide el caller. Todas las
filas se resuelven **en el paso 4**, antes de A–F.
[DESIGN] cierre; [EXACT] anclas en `TEMP/S15-authority-options.md:624-642`.

| `rotation_pending` | par pending | `storage_recovery_required` | fp solicitado | acción en el paso 4 | resultado al entrar en 5 |
|---|---|---|---|---|---|
| false | (null,null) | false | igual | nada que resolver | entra en A–F normal |
| false | (null,null) | false | distinto | nada que resolver | entra en A–F como mismatch → C/F |
| false | (null,null) | **true** | cualquiera | no autoriza launch ni proyección | `INCONCLUSIVE/storage_recovery_required` |
| true | (null,null) | **true** | cualquiera | recovery manda sobre pending; no A–F; no proyección normal; ningún write salvo el recovery acreditado | `INCONCLUSIVE/storage_recovery_required` |
| true | (null,null) | false | igual | no se limpia; token CAS con preimagen `(null,null,marker_sha)` | PASS, sin rotar otra vez |
| true | (null,null) | false | **distinto** | no se limpia; **no se muta el marker current**; `storage_1` presente: obligación al candidate y journal `prepared` de F; `storage_1` ausente: C no es resoluble (backup de storage no acreditable bajo txid fresco) | presente → entra F (tabla bajo A–F); ausente → `INCONCLUSIVE/storage_fingerprint_mismatch` ([DESIGN]; no el código de B4), marker y backup byte-idénticos, no A–F; fp igual sólo si el modset anterior es reproducible (`TEMP/S15-authority-options.md:627-628`); si no, rollback humano (`TEMP/S15-authority-options.md:747-753`) |
| true | (id,rol) | false | igual | `observe_run` → `terminal` obligatorio antes de A; tres `terminal`+A=B → 4d posee el CAS de limpieza (única escritura de 4d); ausente o inestable/A≠B → 4d **no** escribe | `live`→`FAIL/storage_pending_run_live`; `unknown`→`INCONCLUSIVE/storage_process_unknown`; A=B → entra en 5 con pending ya limpio; **ausente** → B2 ausencia estable: PASS, pending intacto, token que sólo reemplaza el par terminal (`TEMP/S15-authority-options.md:632-634`); **inestable/A≠B** → B2 fail-closed (`INCONCLUSIVE/storage_filesystem_unverifiable`, `launch_allowed=False`, `cas_token=None`, pending intacto) |
| true | (id,rol) | false | **distinto** | `observe_run` → `terminal` obligatorio; tres `terminal`+A=B → 4d posee el CAS de limpieza contra la preimagen del marker actual, con independencia del fp solicitado; ausente o inestable/A≠B → 4d **no** escribe | `live`→`FAIL/storage_pending_run_live`; `unknown`→`INCONCLUSIVE/storage_process_unknown`; A=B → entra en 5 con pending ya limpio → mismatch → F; **ausente** → no A–F, pending intacto, `INCONCLUSIVE/storage_fingerprint_mismatch` ([DESIGN]; no el código de B4), `launch_allowed=False`, `cas_token=None`; fp igual sólo si el modset anterior es reproducible (`TEMP/S15-authority-options.md:632-633`); si no, rollback humano (`TEMP/S15-authority-options.md:747-753`); **inestable/A≠B** → B2 fail-closed (`INCONCLUSIVE/storage_filesystem_unverifiable`, `launch_allowed=False`, `cas_token=None`, pending intacto) |
| true | (id,rol) | **true** | cualquiera | recovery manda sobre pending | `INCONCLUSIVE/storage_recovery_required` |

Fingerprint distinto con pending (id,rol) no quiescente (live, unknown,
ausente o inestable) no entra en C/F. Tras CAS terminal+A=B, pending ya
limpio, el paso 5 compara el fp del caller y entra en F. Fingerprint
distinto con par nulo y `storage_1` presente entra en F; con `storage_1`
ausente no entra en C: `INCONCLUSIVE/storage_fingerprint_mismatch`
([DESIGN]; el filesystem está acreditado; no es el
`storage_filesystem_unverifiable` de B4), bytes intactos. Una llamada con
el fingerprint original (fila `fp igual` → PASS;
`TEMP/S15-authority-options.md:627-628,632-633`) **sólo existe si el
caller puede reproducir el modset anterior**: `fingerprint_sha256` «es el
hash del payload v1 recomputado desde `roles` y además debe igualar el
fingerprint esperado del caller» (`TEMP/S15-authority-options.md:465-467`).
Si no puede, esa salida no es una opción del llamante; el desbloqueo
documentado es el rollback humano (`TEMP/S15-authority-options.md:747-753`),
que reinstala el par legacy y devuelve la misión a A/D.

#### Matriz A–F

[EXACT] `TEMP/S15-authority-options.md:548-555`. [DESIGN] se recorre **después**
del orden de arriba. Storage con tipo distinto de directorio real/no-reparse,
marker con tipo distinto de regular/no-reparse o drift durante snapshot no
entra en A–F: `INCONCLUSIVE/storage_filesystem_unverifiable`, sin mutación
(`TEMP/S15-authority-options.md:557-560`).

| Celda | `storage_1` | marker | Acción | `storage_rotated` / pending |
|---|---|---|---|---|
| A | ausente | ausente | journal, publicar marker nuevo | `false / false` |
| B | ausente | exacto | reutilizar bytes; ninguna mutación | conserva estado válido; normalmente `false / false` |
| C | ausente | inválido/mismatch | journal, renombrar sólo marker a backup, publicar marker nuevo | `false / false` |
| D | directorio real | ausente | journal, renombrar sólo storage a backup, publicar marker | `true / true`, pending ID/rol nulos |
| E | directorio real | exacto | reutilizar; si pending aplica §11 antes de launch | conserva; sin pending `false / false` |
| F | directorio real | inválido/mismatch | journal, storage→backup, marker→backup, publicar marker | `true / true`, pending ID/rol nulos |

Las salidas `false/false` de C y `true/true` con par nulo de F son las de
la ficha cuando el paso 4 dejó `rotation_pending=false`. Si el paso 4
conservó pending (par nulo, `storage_1` presente), F **no** se reutiliza
sin modificación; C con pending y storage ausente no se recorre
(`product-spec.md:146`;
`plans/inbox-20260830/20-fb-20260829-115147-4407.md:39,56`).

#### Supervivencia de pending al entrar en C/F (paso 4 → 5)

[DESIGN] El CAS terminal+A=B de 4d usa la preimagen del marker actual,
con independencia del fp solicitado, y deja pending limpio **antes** del
paso 5; no es la supervivencia de esta tabla. N2: 4d no sustituye el
current por un candidate de C/F. Preimagen = marker current completo
(`rotation_pending`, par, sha, identidad). Los campos que sobreviven se
copian a los bytes del candidate; el journal `prepared` los liga con
`candidate_marker_sha256`
(`TEMP/S15-authority-options.md:506-509,564-576`). Después F archiva el
marker preimagen por rename no-replace a backup y publica el candidate
por rename al current ya ausente, en el orden F ya definido
(`TEMP/S15-authority-options.md:548-555,564-576`;
`plans/inbox-20260830/20-fb-20260829-115147-4407.md:33,36-38`). Si el
journal `prepared` no se acredita, `INCONCLUSIVE`, sin mutación, pending
intacto en el current.

| Celda | campos que **sobreviven** (copiados al candidate) | campos **sustituidos** en el candidate publicado por C/F | publicación prohibida mientras H11 rija |
|---|---|---|---|
| C (storage ausente; mismatch; pending true) | no hay candidate: **no se entra en C** | — | cualquier publicación. `INCONCLUSIVE/storage_fingerprint_mismatch`; marker y backup byte-idénticos. Si el modset anterior es reproducible: fp original (fila `fp igual` → PASS; `TEMP/S15-authority-options.md:627-628,632-633`). Si no: rollback humano (`TEMP/S15-authority-options.md:747-753`); no es una opción del llamante |
| F (storage presente; mismatch) | `rotation_pending=true`; par pending `(null,null)` — misma obligación, no un pending inventado; aviso de reset | txid fresco; fingerprint/roles/binding nuevos; `storage_rotated=true`; nombres de backup **de esta** rotación; SHA del marker nuevo | `rotation_pending=false`; borrar el aviso; sustituir un par (id,rol) no quiescente (F es inalcanzable si el paso 4 no resolvió live/unknown/ausente/inestable) |

En F la postimagen `(true, null, null, sha_nuevo)` **es** el candidate
ligado por `candidate_marker_sha256` en el journal `prepared`; el marker
current no se sustituye hasta el rename de publicación, cuando el current
ya está ausente porque F lo archivó. C con pending no publica: no hay
storage que renombrar ni completed fresco que acredite el backup. Un CAS
que reemplazara el current por un candidate **antes** del journal o del
archivo dejaría storage viejo emparejado con fingerprint nuevo y haría
irrecuperable el marker legacy.

---

### B2 — conflicto A≠B, árbol ausente o inestable

**CRITICAL**

[EXACT] Lectura de la ficha (`TEMP/S15-authority-options.md:639-642`):
«Si A≠B, storage desaparece durante snapshots o el árbol es inestable pero
no reparse, se conserva pending y se permite el siguiente launch con token
que sólo podrá reemplazar el ID/rol terminal después del nuevo ACK».

[EXACT] Lectura del plan (`plans/inbox-20260830/20-fb-20260829-115147-4407.md:39`):
«ACK sin storage, run no terminal, A≠B, sidecar drift o CAS fallido conserva
pending, aviso y éxito degradado». El mismo plan, en
`plans/inbox-20260830/20-fb-20260829-115147-4407.md:56`, admite que «Si falta
storage, un run siguiente sólo sustituye el ID terminal anterior por su
UUID/rol mediante ese mismo CAS». Su lista de FAIL prohíbe «aceptar storage
vivo/inestable/alias» (`plans/inbox-20260830/20-fb-20260829-115147-4407.md:60`).
Su INCONCLUSIVE exige fail-closed cuando la identidad no se reacredita
(`plans/inbox-20260830/20-fb-20260829-115147-4407.md:61`).

Las dos lecturas en conflicto no se ocultan: la ficha permite el siguiente
launch sobre un árbol cuya identidad no se reacreditó; el plan clasifica
aceptar storage vivo/inestable/alias como FAIL y exige fail-closed cuando
la identidad no se reacredita. Un árbol cuyo digest cambia entre A y B es,
por definición, un árbol cuya identidad no se ha podido reacreditar.

**DECISIÓN**: fail-closed, y **la decisión fue del usuario** en esta sesión
(no se reabre, no se somete a votación de lanes). `A≠B`, la desaparición
del storage durante los snapshots o un árbol inestable no-reparse producen
`INCONCLUSIVE/storage_filesystem_unverifiable`, `launch_allowed=False` y
`cas_token=None`. [DESIGN] ese `error_code` (no `storage_cas_mismatch`: la
ficha lo reserva al CAS de limpieza fallido en
`TEMP/S15-authority-options.md:641-642`, y bajo fail-closed no se intenta
ese CAS). Sustituir un
`pending` terminal previo **sólo** tras acreditar **ausencia estable**,
definida como: dos observaciones consecutivas de `absent` con `observe_run`
dando `terminal` antes, entre y después, y sin ningún sibling cuya identidad
coincida con la del backup.

Si se omite: se lanza un `server|all|offline` sobre un `storage_1` que está
mutando, con un token CAS que después reemplazará el pending, y se corrompe
el storage del jugador.

#### Matriz — A≠B y estados del árbol durante los snapshots (fail-closed)

[DESIGN] resolución; [EXACT] anclas citadas arriba.

| estado observado entre A y B | ¿es reparse? | verdict | `launch_allowed` | `cas_token` | ¿se conserva pending? |
|---|---|---|---|---|---|
| A=B, tres observaciones `terminal` | no | PASS | true | según celda (fp igual → E; fp distinto → F) | se limpia por CAS **en 4d** (única excepción de write de 4d; independiente del fp solicitado) |
| **A≠B** | no | **INCONCLUSIVE**/`storage_filesystem_unverifiable` | **false** | **None** | sí, intacto |
| storage desaparece entre A y B | no | **INCONCLUSIVE**/`storage_filesystem_unverifiable` | **false** | **None** | sí, intacto |
| árbol inestable (digest oscila) | no | **INCONCLUSIVE**/`storage_filesystem_unverifiable` | **false** | **None** | sí, intacto |
| storage es reparse / tipo errado | sí | `INCONCLUSIVE/storage_filesystem_unverifiable` | false | None | sí, intacto |
| identidad de storage == identidad del backup | — | `INCONCLUSIVE/storage_filesystem_unverifiable` | false | None | sí, intacto |
| storage ausente de forma estable (**ausencia estable**) | — | PASS si fp igual; `INCONCLUSIVE/storage_fingerprint_mismatch` si fp distinto (no A–F; no es el código de B4) | true / false según fp | token que solo reemplaza el par terminal (fp igual); `None` si fp distinto | sí, hasta el ACK |

---

### B3 — recovery con marker ya publicado y fase stale

**HIGH**

[EXACT] El pre-pass está en `TEMP/S15-authority-options.md:564-602`. Para
`marker_moved` sí existe la decisión de tres vías que trata el estado físico
como `marker_published` (`TEMP/S15-authority-options.md:591-597`).
`prepared` (`TEMP/S15-authority-options.md:586-588`) y `storage_moved`
(`TEMP/S15-authority-options.md:589-590`) no tienen esa rama de tres vías.
`storage_moved` sí tiene rama para current storage ausente: «después verifica
current storage ausente y devuelve el backup storage por no-replace»
(`TEMP/S15-authority-options.md:589-590`). El plan exige que un único journal
«reconcilia fase declarada y estado físico acreditado» y que
«`storage_moved` compensa storage»
(`plans/inbox-20260830/20-fb-20260829-115147-4407.md:35`), y que recovery
converja desde las cuatro fases
(`plans/inbox-20260830/20-fb-20260829-115147-4407.md:57`).

**DECISIÓN**: extender a `prepared` y `storage_moved` la misma decisión de
tres vías que ya tiene `marker_moved`. Si el marker current es un fichero
regular, sus bytes canónicos hashean exactamente `candidate_marker_sha256`,
y todos los backups casan con sus preimágenes, entonces —sea cual sea la
fase declarada— el estado físico **es** `marker_published`: se conserva el
marker y su `pending`, se publica esa fase por CAS del journal y se
finaliza a `completed`. Nunca se restaura encima de una publicación válida.
Añadir fixtures independientes por cada fase declarada stale: `prepared`
con marker ya publicado, `storage_moved` con marker ya publicado,
`storage_moved` con **marker current original intacto** (caída justo
después de `storage_1→backup` en **F**; `RN-STO` es D,F — C tiene
`storage_1` ausente y no ejecuta ese rename), `storage_moved` con **storage y
marker currents ausentes** (celda D tras `storage→backup` y antes de
publicar marker): **compensa** — devuelve el backup storage por no-replace;
no publica el candidate; no inventa marker legacy
(`TEMP/S15-authority-options.md:589-590`;
`plans/inbox-20260830/20-fb-20260829-115147-4407.md:35`). El simétrico de cada una donde el hash **no** casa debe
bloquear. Inyección de fallo en esa frontera de primer
rename, no sólo en D/F felices.

Si se omite: un crash entre el rename del marker y la publicación de la
fase deja el journal en `prepared`/`storage_moved` con el marker nuevo ya
visible; el siguiente pre-pass restaura el backup y el jugador pierde el
storage nuevo.

#### Matriz — recovery por fase, con la rama física añadida

[DESIGN] filas marcadas nuevo; [EXACT] resto en
`TEMP/S15-authority-options.md:586-602`.

| fase declarada | estado físico observado | acción | ¿restaura? |
|---|---|---|---|
| `prepared` | sin rename | retira candidates exactos, journal → completed (compensada) | no hay qué restaurar |
| `prepared` | **marker live hashea `candidate_marker_sha256` + backups casan** | **tratar como `marker_published`**: conservar marker y pending, publicar fase por CAS, finalizar | **no** ← nuevo |
| `prepared` | storage o marker movido, hash no casa | entrar en la rama física correspondiente | sí |
| `storage_moved` | marker también movido, fase stale | restaurar marker primero, después devolver storage | sí |
| `storage_moved` | **F tras `RN-STO`: storage current ausente; marker current original intacto** (mismo path, identidad y SHA que `marker_preimage`; no hashea `candidate_marker_sha256`; no es C: C no ejecuta `storage_1→backup`) | **acreditar storage current ausente; restaurar únicamente el backup de storage por no-replace; no tocar el marker; CAS del journal y completar/compensar** | **sí, sólo storage** ← nuevo |
| `storage_moved` | **celda D: storage current ausente; marker current ausente (preimagen marker `exists=false`); backup storage = preimagen; candidate marker hashea `candidate_marker_sha256`** | **compensar D: current storage ausente ⇒ devolver el backup storage por no-replace (`TEMP/S15-authority-options.md:589-590`; `plans/inbox-20260830/20-fb-20260829-115147-4407.md:35`); no inventar marker legacy; retirar el candidate exacto (`TEMP/S15-authority-options.md:579-581`); no publicar marker; CAS y completed compensado; reentrar A–F una vez (`TEMP/S15-authority-options.md:604-614`)** | **sí, sólo storage** ← nuevo |
| `storage_moved` | **celda D: storage current ausente; marker current ausente (preimagen marker `exists=false`); backup storage acreditado; candidate ausente o SHA no casa** | **`recovery_required=true`; conservar backup; no restaurar a ciegas; no inventar marker legacy** | **no; bloquea** ← nuevo |
| `storage_moved` | **marker live hashea `candidate_marker_sha256` + backups casan** | **tratar como `marker_published`** | **no** ← nuevo |
| `marker_moved` | marker current regular y hashea el candidate | tratar como `marker_published` | no |
| `marker_moved` | marker current ausente | retirar candidate no publicado, restaurar marker y luego storage | sí |
| `marker_published` | marker SHA candidate, nombres/binding/fingerprint exactos | conservar marker con su pending, finalizar a completed | no |
| cualquiera | contradicción, segundo journal, no converge, **o fase inalcanzable para la celda reconstruida** (p. ej. C/A en `storage_moved`; C no ejecuta `RN-STO`) | `recovery_required=true` por CAS si es posible | no; bloquea |

---

### B4 — taxonomía del lector: INCONCLUSIVE del plan no se eleva a FAIL

**HIGH**

[EXACT] S15 se declara candidata y ancla el plan congelado
(`TEMP/S15-authority-options.md:3-13`). El plan enumera marker requerido
ausente/ilegible y mismatch de binding, fingerprint o run_id dentro de
`INCONCLUSIVE` (`plans/inbox-20260830/20-fb-20260829-115147-4407.md:61`).
La ficha, en el mapa del reader, marca FAIL para marker presente inválido,
binding/fingerprint mismatch y pending-run mismatch
(`TEMP/S15-authority-options.md:685-689`). S15 no tiene autoridad para
sustituir ese INCONCLUSIVE por FAIL.

[EXACT] `read_storage_projection` no recibe `StorageProcessReader` ni token
CAS (`TEMP/S15-authority-options.md:220-227`); las ramas de proceso
vivo/desconocido y CAS pertenecen al pre-pass mutador
(`TEMP/S15-authority-options.md:624-642`).

**DECISIÓN**: el plan es autoridad. En el lector, reserva
`FAIL/storage_input_invalid` exclusivamente para un argumento directo de
API sintácticamente inválido **antes** de leer disco. Clasifica como
`INCONCLUSIVE` toda falta de acreditación, estado durable inválido/no
legible, mismatch con autoridades recomputadas e I/O incierta. Conserva los
`error_code` específicos como diagnóstico; no uses su especificidad para
elevar el `verdict` a `FAIL`. Todo no-PASS conserva bytes y falla cerrado.
Los cinco códigos ajenos al lector (`storage_pending_run_live`,
`storage_namespace_collision`, `storage_process_unknown`,
`storage_cas_mismatch`, y `storage_pending_clear_deferred` como error_code)
son inalcanzables en `read_storage_projection`. Un
`storage_1.rollback-op.*` activo o fuera de sus tres gramáticas cierra el
lector como un journal de rotación bloqueante:
`INCONCLUSIVE/storage_recovery_required` o
`INCONCLUSIVE/storage_journal_blocked`. [DESIGN] la autoridad sólo
enumeraba `storage_1.rotation.*`
(`TEMP/S15-authority-options.md:531-536,661-663,695-696`).

Si se omite: el mismo estado durable recibiría dos veredictos según qué
documento implemente el consumidor.

#### Matriz — `verdict × error_code × projection` del lector

[DESIGN] Inalcanzable = un `StorageReadResult` conforme no puede emitir
esa combinación. Las ramas no-PASS preservan bytes y no autorizan launch.

| `verdict` | `error_code` | `projection` | Caso alcanzable en `read_storage_projection` | Evidencia |
|---|---|---|---|---|
| `PASS` | `None` | `StorageProjection` normal, exactamente seis campos | Marker, completed y backups requeridos quedan reacreditados; la relación con `expected_run_id` es válida; cero journals activos de rotación **y** de rollback-op. Un `storage_1.rollback-op.*` activo impide PASS. [DESIGN] la autoridad exige «cero journals activos» acotado a `storage_1.rotation.*`. `storage_pending_clear_deferred` puede ser `true` como **campo**, sin convertirse en error code. | `TEMP/S15-authority-options.md:534,661-680` |
| `FAIL` | `storage_input_invalid` | `None` | Sólo argumento directo de API inválido antes de tocar disco (p. ej. `expected_run_id` no nulo que no es UUID4 canónico). Un UUID válido distinto del pending no entra aquí. | `TEMP/S15-authority-options.md:220-227,258-262` |
| `INCONCLUSIVE` | `storage_marker_invalid` | `None` | Marker requerido ausente, ilegible o inválido en tipo, UTF-8, JSON, canonicalización, keyset, schema, kind, algoritmo, txid o coherencia interna. La rama que S15 marcaba `FAIL` se corrige por la autoridad del plan. | `plans/inbox-20260830/20-fb-20260829-115147-4407.md:61`; `TEMP/S15-authority-options.md:682-686` |
| `INCONCLUSIVE` | `storage_binding_mismatch` | `None` | Binding recomputado distinto del marker validado estructuralmente. | `plans/inbox-20260830/20-fb-20260829-115147-4407.md:40,61`; `TEMP/S15-authority-options.md:687-688` |
| `INCONCLUSIVE` | `storage_fingerprint_mismatch` | `None` | Fingerprint recomputado distinto del marker validado estructuralmente. | `plans/inbox-20260830/20-fb-20260829-115147-4407.md:40,61`; `TEMP/S15-authority-options.md:687-688` |
| — (inalcanzable) | `storage_pending_run_live` | — | El lector no recibe observador de procesos. | `TEMP/S15-authority-options.md:220-227,624-631` |
| `INCONCLUSIVE` | `storage_pending_run_mismatch` | `None` | `expected_run_id` es UUID válido, pero difiere de un pending no nulo. La rama que S15 marcaba `FAIL` se corrige por el plan. | `plans/inbox-20260830/20-fb-20260829-115147-4407.md:61`; `TEMP/S15-authority-options.md:672-680,687-689` |
| `INCONCLUSIVE` | `storage_root_not_accredited` | `None` | No hay exactamente una raíz contenedora o no se reacreditan identidad de raíz/misión y recorrido no-reparse. | `TEMP/S15-authority-options.md:338-356`; `plans/inbox-20260830/20-fb-20260829-115147-4407.md:61` |
| — (inalcanzable) | `storage_namespace_collision` | — | Reserva/colisión del txid es rama mutante; en el lector, un sibling de journal que no casa se normaliza como `storage_journal_blocked`. | `TEMP/S15-authority-options.md:528-536,693-696` |
| `INCONCLUSIVE` | `storage_filesystem_unverifiable` | `None` | No se puede crear/adquirir el mutex o el adapter no acredita handles, no-reparse o garantías filesystem. | `TEMP/S15-authority-options.md:350-364` |
| `INCONCLUSIVE` | `storage_journal_blocked` | `None` | Completed no canónico/no ligado, sibling `storage_1.rotation.*` fuera de las gramáticas cerradas, o sibling `storage_1.rollback-op.*` que no case las tres gramáticas cerradas de rollback-op. [DESIGN] la extensión a `rollback-op`; la autoridad sólo enumera `storage_1.rotation.*`. | `TEMP/S15-authority-options.md:531-536,693-696` |
| `INCONCLUSIVE` | `storage_recovery_required` | `None` | Journal activo `storage_1.rollback-op.<txid>.json`. El mismo estado durable que 4a bloquea con `launch_allowed=False`; se evalúa **antes** de la proyección limitada B5; nunca PASS ni proyección normal. [DESIGN] familia ausente de la autoridad. | `TEMP/S15-authority-options.md:531-536,661-663,690-696`; `plans/inbox-20260830/20-fb-20260829-115147-4407.md:61`; `product-spec.md:146` |
| `INCONCLUSIVE` | `storage_recovery_required` | `StorageProjection` **limitada** de seis campos | El marker supera el gate B5 y la condición de recovery está ligada a su txid; los paths sólo aparecen si además quedan reacreditados. Un `storage_1.rollback-op.*` activo no entra aquí. | `plans/inbox-20260830/20-fb-20260829-115147-4407.md:38,40-42`; `TEMP/S15-authority-options.md:690-700` |
| `INCONCLUSIVE` | `storage_recovery_required` | `None` | Hay evidencia bloqueante de recovery, pero no existe un marker que supere el gate B5 o la evidencia no se liga al mismo txid/invariantes. | `TEMP/S15-authority-options.md:690-696`; `plans/inbox-20260830/20-fb-20260829-115147-4407.md:61` |
| — (inalcanzable) | `storage_process_unknown` | — | El lector no observa procesos. | `TEMP/S15-authority-options.md:220-227,624-631` |
| — (inalcanzable) | `storage_cas_mismatch` | — | El lector es read-only y no ejecuta CAS. | `TEMP/S15-authority-options.md:220-227,641-656,661-663` |
| `INCONCLUSIVE` | `storage_io_failed` | `None` | Excepción I/O de lectura que no demuestra bytes inválidos y no queda cubierta por un código más específico. | `TEMP/S15-authority-options.md:706-722` |
| — (inalcanzable como `error_code`) | `storage_pending_clear_deferred` | — | Único error code de éxito degradado del CAS post-start. El lector lo representa en el booleano homónimo de una proyección acreditada. | `TEMP/S15-authority-options.md:652-680,706-712` |

---

### B5 — proyección acotada durante recovery, sin proyección normal ni launch

**HIGH**

[EXACT] El plan exige que el lector valide schema/keyset, txid, fingerprint,
pending y siblings antes de devolver exactamente seis campos, y permite que
recovery exponga sólo paths sibling acotados desde un marker validado
(`plans/inbox-20260830/20-fb-20260829-115147-4407.md:40-42`). S15 ya
establece que recovery nunca autoriza launch ni una proyección **normal**
(`TEMP/S15-authority-options.md:464-477`). El mapa del reader declara
«siempre `projection=None` cuando no es PASS»
(`TEMP/S15-authority-options.md:682`); esa cláusula absoluta se descarta:
el plan es autoridad (`plans/inbox-20260830/20-fb-20260829-115147-4407.md:42`).
El dataclass tiene exactamente
seis campos (`TEMP/S15-authority-options.md:157-164`); S15 limita los paths
a `public_sibling_path` y prohíbe devolver excepciones, paths arbitrarios,
contenido de sidecar/journal o listas de procesos
(`TEMP/S15-authority-options.md:698-704,720-722`).

**DECISIÓN**: el plan es autoridad. `INCONCLUSIVE/storage_recovery_required`
puede transportar una proyección de recovery limitada sin convertirse en
PASS ni autorizar launch.
La excepción sólo cuando se cumplen acumulativamente: (1) M21 ya validó el
DZW1 cerrado; (2) la misión resuelta queda ligada a exactamente una raíz
sellada reacreditada; (3) el marker es fichero regular no-reparse, UTF-8/JSON
canónico, con keyset/schema/kind/algoritmo/txid y coherencia interna exactos;
(4) binding y fingerprint recomputados casan, y el expected run satisface la
coherencia pending; (5) la condición de recovery queda ligada al mismo txid
e invariantes del marker; (6) cada path no nulo se deriva como sibling de
nombre cerrado y se reacredita físicamente. Si falla cualquiera de (1)-(5),
`projection=None`; si sólo falla la acreditación de uno de los backups en
(6), conserva la proyección limitada pero pone **ese** campo path a `null`.
Prohíbe dentro de esta proyección `mission`, `mission_path`, `root`, URI/path
libre, root derivado del sidecar, binding, roles, fingerprint, pending run
ID/rol, fase o path de journal, receipts/digests, contenido de documentos,
procesos, excepción y cualquier séptimo campo. `storage_reset_notice` no
forma parte de `StorageProjection`.

Si se omite: `storage_recovery_required` seguiría oculto al consumidor, o se
expondrían nombres que parecen backups válidos sin acreditar el objeto
físico.

#### Fuente exacta de los seis campos durante recovery

[DESIGN] La presencia de la proyección no cambia `verdict=INCONCLUSIVE`,
`error_code=storage_recovery_required` ni el bloqueo de launch.

| Campo exacto | Valor permitido en proyección limitada | Acreditación exigida | Fuente prohibida |
|---|---|---|---|
| `storage_rotated` | Booleano del marker validado, sujeto a las reglas de correlación con `expected_run_id`; si esa correlación no puede probarse, no hay proyección. | Marker completo + binding/fingerprint + pending/run coherentes. | Inferencia por existencia de backup, journal o sufijo. |
| `storage_backup` | `null`, o path público sibling derivado del `storage_backup_name` cerrado. Si el marker nombra backup pero el objeto/receipt no se reacredita, debe ser `null`. | Nombre con gramática/txid/digest concordantes y objeto físico acreditado contra evidencia del mismo txid. | Path absoluto/libre del marker, journal o sidecar. |
| `storage_marker_backup` | `null`, o path público sibling derivado del `marker_backup_name` cerrado. Si el objeto/receipt no se reacredita, debe ser `null`. | Mismas garantías que `storage_backup`, aplicadas a fichero regular y SHA. | Cualquier path copiado o concatenado desde datos persistidos. |
| `storage_pending_clear_deferred` | Exactamente el valor derivado de `rotation_pending` del marker validado. | Coherencia del par pending y expected run según el contrato del lector. | Campo duplicado, ACK/readiness, estado de proceso o journal. |
| `storage_recovery_required` | Siempre `true` en esta combinación de resultado. | Recovery declarado por marker validado o evidencia de recovery del mismo txid ligada a sus invariantes. | Copia ciega de un journal/sidecar no validado. |
| `storage_txid` | El txid de 32 hex del marker validado. | Canonicalización, keyset y txid exactos del marker. | Txid de nombre sibling, journal suelto, candidate o input del caller. |

#### Gate binario de la excepción B5

| Estado observado | Resultado cerrado |
|---|---|
| Marker supera el gate completo y recovery se liga al mismo txid/invariantes | `INCONCLUSIVE / storage_recovery_required / proyección limitada` |
| Marker supera el gate, pero un backup nombrado no se reacredita | Mismo resultado; el path afectado es `null`, los otros cinco campos permanecen acotados |
| Marker ausente, ilegible, no canónico o incoherente | `INCONCLUSIVE / storage_marker_invalid / None` |
| Binding, fingerprint o expected run no casan | `INCONCLUSIVE / código mismatch específico / None` |
| Recovery no puede ligarse al txid e invariantes del marker | `INCONCLUSIVE / storage_recovery_required / None` |
| Journal `storage_1.rollback-op.*` activo o fuera de las tres gramáticas cerradas | `INCONCLUSIVE / storage_recovery_required` (activo) o `storage_journal_blocked` (malformado) / `None`; nunca PASS |
| Cualquier intento de añadir un séptimo campo o usar un path/root libre | Rechazar la proyección; `INCONCLUSIVE / código específico / None` |

---

### B6 — gate real Windows/NTFS; DEFERRED si no se acredita

**HIGH**

[EXACT] El banco unitario usa `FakeStorageFilesystem`
(`TEMP/S15-authority-options.md:757-760`). La propia ficha exige que si el
host real no prueba create-new, flush/reopen, rename-no-replace, replace-CAS
y handles no-reparse, S15 queda `DEFERRED` con la primitiva concreta no
acreditada; no se declara PASS por el fake
(`TEMP/S15-authority-options.md:796-801`). El contrato de
`write_regular_create_only` / `rename_noreplace` / `replace_regular_cas`
convierte la falta de garantía en
`INCONCLUSIVE/storage_filesystem_unverifiable`
(`TEMP/S15-authority-options.md:359-364`). El repo ya rechaza `DRIVE_REMOTE`
en `_local_canonical_path` (`tools/dayz_mcp/request_path_authority.py:134-156`)
y ya llama create-only+fsync y `ReplaceFileW` pasando
`_REPLACEFILE_WRITE_THROUGH = 0x00000001`
(`tools/dayz_mcp/launcher_registry_update.py:35,119-129`). Eso acredita
que el repo pasa esa constante, no write-through ni durabilidad del
volumen.
[DESIGN] El gate separa CAS lógico (identidad+SHA antes y readback
después) y durabilidad. No acredita atomicidad de un único replace:
`delete(target)+rename(candidate)` deja la misma postimagen y queda
fuera de este gate; atomicidad `DEFERRED`, igual que la durabilidad.
El post-assert guarda identidades pre-call de target y candidate
(`PathIdentity`, `tools/dayz_mcp/request_path_authority.py:54-57,173-185`)
y observa (a) target con la identidad pre-call del candidate,
(b) candidate ausente y (c) identidad pre-call del target no retenida.
El repo mide que `ReplaceFileW` entrega un file-id nuevo —no el
anterior—, no que el id resultante sea el del candidate
(`tools/dayz_mcp/launcher_registry_update.py:347-351`); eso sostiene
(c), no (a). El post-assert de bytes no discrimina overwrite in-place
ni write-through; un mutante overwrite/copy in-place (identidad del
target retenida) debe ponerse rojo. (a) es postcondición [DESIGN];
su fallo con identidad observable no es `FAIL` del contrato. La
durabilidad queda `DEFERRED` hasta un mecanismo soportado con un
control que pueda ponerse rojo.

**DECISIÓN**: el fake cubre la matriz A–F y los ordinales B7; la
acreditación de cada primitiva del adapter real es un gate aparte. Toda
primitiva real empieza con un capability probe y termina con un post-assert.
Fallo del probe → `INCONCLUSIVE/storage_filesystem_unverifiable` (no
acreditada) y S15 queda `DEFERRED` con la primitiva nombrada. Fallo de
(b), (c) o bytes con probe OK → `INCONCLUSIVE` del contrato
(`storage_io_failed` o `storage_cas_mismatch` o
`storage_filesystem_unverifiable` según la fila) y S15 queda `DEFERRED`
con la primitiva nombrada. El plan es autoridad: esos tres códigos viven
en `INCONCLUSIVE`, no en `FAIL`
(`TEMP/S15-authority-options.md:713-719`;
`plans/inbox-20260830/20-fb-20260829-115147-4407.md:61`). El `FAIL` del
gate (mutante rojo) no es el `StorageVerdict`. Fallo de (a) con (c)
satisfecho e identidad observable →
`DEFERRED` (herencia de identidad del candidate no acreditada en este
host), no `FAIL`. El post-assert de replace-CAS no es
`target bytes == candidate`. Nunca `PASS` en ningún fallo. El skip del marker `real_windows` cuando
`sys.platform != "win32"` o el `TemporaryDirectory` de esa corrida no
acredita path local (`DRIVE_REMOTE` vía
`tools/dayz_mcp/request_path_authority.py:134-156`) es
`DEFERRED`, no `PASS`.

Si se omite: S15 se declara PASS con sólo el fake, contradiciendo
`TEMP/S15-authority-options.md:796-801`.

#### Matriz — suite real `_WindowsStorageFilesystem`

[DESIGN] especificación de la suite. Referentes [EXACT] citados arriba.
`path` = subdirectorio fresco de un `TemporaryDirectory` aislado
(`TEMP/S15-authority-options.md:757`; misma base que B10). No
`[IO.Path]::GetTempPath()`: M15 no tiene binding Python acreditado de esa API.

| # | Primitiva | Qué se ejecuta | Qué se observa (post-assert) | Control negativo (debe fallar) | No acreditada → | Acreditada y falló → |
|---|---|---|---|---|---|---|
| 1 | create-new | `os.open(p, O_WRONLY\|O_CREAT\|O_EXCL\|O_BINARY, 0o600)`; escribir N bytes; bucle hasta `written==N` | fichero existe, regular, `st_nlink==1`, readback==input | pre-crear destino y repetir → `EEXIST` | `O_EXCL` ignorado → `INCONCLUSIVE/storage_filesystem_unverifiable`; S15 `DEFERRED` | readback!=input o `st_nlink!=1` → `INCONCLUSIVE/storage_io_failed`; S15 `DEFERRED` |
| 2 | write completo | bucle `os.write` hasta `offset==len(raw)` | `written>0` cada vuelta, `offset==len(raw)` | short-write inyectado (fake); real: 1 MiB y readback | write 0 repetido → `INCONCLUSIVE/storage_filesystem_unverifiable`; `DEFERRED` | readback parcial → `INCONCLUSIVE/storage_io_failed`; S15 `DEFERRED` |
| 3 | flush/fsync | `os.fsync(fd)`; `os.fstat` post-fsync (mismo orden que `tools/dayz_mcp/launcher_registry_update.py:101-129`) | `fsync` **no lanza excepción** — el wrapper Python retorna `None` al éxito; el 0 es de la llamada C y **no se compara**. Acreditación posterior: `fstat` (`S_ISREG`, `st_nlink==1`) + reopen/readback==input; volumen local | fsync sobre fd cerrado → `OSError` | drive remoto o fsync raise → `INCONCLUSIVE/storage_filesystem_unverifiable`; `DEFERRED` | fsync sin excepción pero fstat/readback!=input → `INCONCLUSIVE/storage_io_failed`; S15 `DEFERRED` |
| 4 | reopen/readback | cerrar; reabrir read-only; `fstat` antes/después | identidad fstat==lexical==post-read; `S_ISREG`; `st_nlink==1`; bytes==input | reparse → `fstat!=lexical`; hardlink → `st_nlink>1` | reparse no detectado → `INCONCLUSIVE/storage_filesystem_unverifiable`; `DEFERRED` | identidad deriva → `INCONCLUSIVE/storage_filesystem_unverifiable`; S15 `DEFERRED` |
| 5 | rename-no-replace | `os.rename(src,dst)` mismo volumen, dst ausente | src ausente, dst existe, identidad dst==src previo | pre-crear dst → `FileExistsError` | `os.rename` sobrescribe dst → `INCONCLUSIVE/storage_filesystem_unverifiable`; `DEFERRED` | dst preexistente sobreescrito → `INCONCLUSIVE/storage_filesystem_unverifiable`; S15 `DEFERRED` |
| 6 | replace-CAS | guardar `PathIdentity` pre-call de target y candidate; verificar identidad+SHA; `ReplaceFileW` (el repo pasa `_REPLACEFILE_WRITE_THROUGH=0x00000001`; esa bandera no acredita write-through); releer | (b) candidate ausente; (c) identidad target pre-call no retenida; bytes==candidate. (a) target.identity==candidate.identity pre-call es [DESIGN], no medido por `:347-351` | target con SHA distinto → `storage_cas_mismatch` antes de tocar; overwrite/copy in-place (misma postimagen, identidad del target retenida) → FAIL del gate (no es el `StorageVerdict`) | identidad no observable, fallo de (a) con (c) OK, o atomicidad de un único replace no observada (`delete+rename` aceptado) → `INCONCLUSIVE/storage_filesystem_unverifiable`; `DEFERRED`. Durabilidad write-through: `DEFERRED`. Atomicidad: `DEFERRED` | (b), (c) o bytes fallan → `INCONCLUSIVE/storage_cas_mismatch`; S15 `DEFERRED`; fallo de (a) con (c) OK → `DEFERRED` |
| 7 | reparse/junction | abrir cada componente con `FILE_FLAG_OPEN_REPARSE_POINT` (`tools/dayz_mcp/request_path_authority.py:188-235,414-460`) | reparse tag==0 en intermedios; `GetFileType==FILE_TYPE_DISK`; `DeletePending==false` | junction no autorizada → tag!=0 | reparse no reportable → `INCONCLUSIVE/storage_filesystem_unverifiable`; `DEFERRED` | reparse detectado y no autorizado → `INCONCLUSIVE/storage_filesystem_unverifiable`; S15 `DEFERRED` |
| 8 | lifetime/cierre | `CloseHandle` en orden inverso | tras `close()`, handle inválido; `__exit__` cierra | handle filtrado | `CloseHandle` raise → `INCONCLUSIVE/storage_filesystem_unverifiable`; `DEFERRED` | handle no cerrado → `INCONCLUSIVE/storage_filesystem_unverifiable`; S15 `DEFERRED` |

La durabilidad ante fallo de alimentación no es acreditable por un test que
no crashea la máquina: queda `DEFERRED` y no se declara `PASS` por ella.
Pasar `_REPLACEFILE_WRITE_THROUGH` no cierra ese `DEFERRED`. La atomicidad
de un único replace tampoco: el post-assert acredita postimagen e
identidad no retenida, no que el target no haya dejado de existir.

---

### B7 — ordinales de fallo no tautológicos y oráculo independiente

**MEDIUM**

[EXACT] El mandato de fixtures independientes está en
`TEMP/S15-authority-options.md:757-760,771-773`. Expected JSON, SHA y
árboles se construyen con un serializador/oráculo de test independiente;
ningún expected llama `fingerprint_mod_roles`, parser, serializer ni walker
productivo (`TEMP/S15-authority-options.md:757-760`).

**DECISIÓN**: separar tres artefactos en cada test de fallo: (1) `seed`
físico independiente, (2) `TRANSACTION_SCRIPT` estático por `(celda, paso)`,
(3) `ORACLE_OUTCOME` calculado sólo desde seed + reglas §10/§11 y la
resolución B2, nunca desde
el retorno de `prepare_storage_for_start` ni desde métodos productivos.
Cada entrada del call-log es `(op, name, step_id)` con
`op ∈ {open_mission, write_create, write, flush, fsync, reopen, readback, rename, replace_cas, remove_candidate}`
y `step_id` ∈ `{J-PREP, RN-STO, JR-SM, RN-MRK, JR-MM, W-MRK, RN-PUB, JR-MP, RN-CMP, CAS-PRE, CAS-PST, RB-PREP, RB-STO-APART, RB-JR-SA, RB-MRK-APART, RB-JR-MA, RB-STO-RST, RB-JR-SR, RB-MRK-RST, RB-JR-MR, RB-CMP}`.
Tras un fallo inyectado, el sufijo debe estar vacío. Por cada ordinal: control
positivo, control negativo en otro ordinal, y mutante que no aborta (el test
harness debe ponerse rojo).

Si se omite: cualquier bug que omita compensación, publique tras fallo o no
bloquee launch pasa verde.

#### Catálogo de ordinales de fallo (familias WRC × paso §10)

[DESIGN] nombres de ordinal; [EXACT] pasos en
`TEMP/S15-authority-options.md:564-576`.

| ID ordinal | Paso §10 | Celdas aplicables | Sub-fallos |
|---|---|---|---|
| `WRC@J-PREP` | crear journal `prepared` | A,C,D,F | create, write, short-write, flush, fsync, reopen, readback |
| `RN-STO` | `storage_1→backup` | D,F | — |
| `JR-SM` | replace journal → `storage_moved` | D,F | create…readback en candidate journal |
| `RN-MRK` | `marker→backup` | C,F | — |
| `JR-MM` | replace journal → `marker_moved` | C,F | create…readback en candidate journal |
| `WRC@W-MRK` | crear candidate marker | A,C,D,F | create…readback |
| `RN-PUB` | publicar marker (rename a current ausente) | A,C,D,F | — |
| `JR-MP` | replace journal → `marker_published` | A,C,D,F | replace_cas |
| `RN-CMP` | journal activo → completed | A,C,D,F | — |
| `CAS-PRE` | CAS pre-start limpieza pending | E, F | replace en marker |
| `CAS-PST` | `record_storage_run_started` | D,F + token | replace marker post-ACK |
| `RB-PREP` | crear journal rollback-op `prepared` | rollback C/D/F/A | create, write, short-write, flush, fsync, reopen, readback |
| `RB-STO-APART` | current storage → sibling `.rollback` | rollback D,F; omitido si `storage_current_preimage.exists=false` | rename no-replace |
| `RB-JR-SA` | journal rollback-op → `storage_apart` | rollback D,F | replace_cas |
| `RB-MRK-APART` | current marker → sibling `.rollback` | rollback A,C,D,F si marker current existe | rename no-replace |
| `RB-JR-MA` | journal rollback-op → `marker_apart` | rollback A,C,D,F | replace_cas |
| `RB-STO-RST` | backup storage (`storage_backup_name` del marker en `marker_apart_name`, o `storage_legacy_preimage.identity`) → `storage_1` | rollback D,F | rename no-replace |
| `RB-JR-SR` | journal rollback-op → `storage_restored` | rollback D,F | replace_cas |
| `RB-MRK-RST` | backup marker (`marker_backup_name` del mismo marker, o `marker_legacy_preimage.identity`) → marker current | rollback C,F | rename no-replace |
| `RB-JR-MR` | journal rollback-op → `marker_restored` | rollback C,F | replace_cas |
| `RB-CMP` | journal rollback-op → completed | rollback A,C,D,F | rename no-replace |

Convención: `<CELDA>-<ORDINAL>-<SUB>`; ejemplo `D-WRC@J-PREP-flush`.
Para rollback: `RB-<CELDA>-<ORDINAL>-<SUB>`; ejemplo `RB-D-RB-STO-APART`.
Outcome de fallo CAS-PST [EXACT]:
`StorageCasResult("PASS","storage_pending_clear_deferred",True,None)`
(`TEMP/S15-authority-options.md:657-659`).

#### Contratos de riesgo RC-01…RC-09 (ternas PASS / FAIL / INCONCLUSIVE)

[EXACT] `TEMP/S15-authority-options.md:755-801`. RC-01 excluye alias literal
en entrada M15 (B9).

| ID | Contrato | PASS | FAIL | INCONCLUSIVE / setup-failed |
|---|---|---|---|---|
| **RC-01** API/autoridad `:762-764` | `inspect.signature`/`__all__`; dos absolutas físicamente equivalentes → mismo `mission_binding_v1`; cero/dos roots, drift, traversal, URI, drive externo, profiles señuelo | Una absoluta válida + segunda absoluta equivalente independiente → mismo binding | `resolved_mission="chernarus"`; dos roots; junction no autorizada; traversal | Fake no puede simular reparse/identidad → `storage_filesystem_unverifiable` |
| **RC-02** Fingerprint/tree `:765-767` | Orden por rol, membresía, bytes, dir vacío, symlink/reparse | Mutar orden dentro de rol cambia SHA | Sort global mutante aceptado | Drift identidad entre reads sin follow |
| **RC-03** Matriz A–F `:768-770` | Seis fixtures con sentinels externos pre-oráculo | A/B no rotan; C solo marker; D solo storage; E reutiliza; F ambos | Celda equivocada rota de más | Snapshot previo no acreditable |
| **RC-04** Fallos ordinal `:771-773` | Catálogo anterior; cada caso con terna pos/neg/mut | Al menos un ordinal por familia demuestra gate rojo con mutante | SUT continúa tras fallo sin harness FAIL | Host sin primitiva → RC-09 |
| **RC-05** Recovery `:774-779` | Fixtures independientes por fase, incluida `storage_moved` con marker current intacto; crash tras restore | Compensación completa → reentra A–F una vez (`:604-614`) | Compensación parcial | Segundo recovery en misma llamada |
| **RC-06** Pending/CAS `:780-785` | null→run, terminal, live/unknown, A≠B, CAS pre/post | Primer run registra, no limpia; terminal+A=B limpia | live → `FAIL/storage_pending_run_live`; limpiar en primer run | `unknown` → `INCONCLUSIVE/storage_process_unknown`; A≠B / desaparición / inestable → `INCONCLUSIVE/storage_filesystem_unverifiable` |
| **RC-07** Reader/mutantes `:786-792` | Cada clave mutada; completed ausente/malformado | Marker exacto → proyección PASS; reader sin llamadas mutantes | Campo extra, BOM, binding drift | recovery_required bloquea |
| **RC-08** Rollback `:793-794` | D y F restauran legacy exacto; matriz `(origen × fase declarada × estado físico)` con fila rename-hecho/CAS-no-publicado; **inyección tras cada I/O y cada CAS por separado**; reentrada con journal a medias; C/A según tabla | Conjunto legacy restaurado byte-idéntico **y** journal rollback-op completed comprometido; completed comprometido exige currents legacy reacreditados; completed compensado (abort en `prepared` sin rename) exige preimagen current intacta, apartados/candidate ausentes y aborto explícito; reentrada no clasifica A/B sobre storage apartados | Colisión/preimagen drift no mueve; mutante que omite journal, publica marker nuevo sobre `storage_apart`, restaura marker antes que storage en D/F, o completa D con `storage_1` ausente | Destino rollback no acreditable; host sin primitiva → RC-09 |
| **RC-09** Primitivas host `:796-801` | Gate focal unittest real | Fake acredita semántica Windows **y** host acredita cada primitiva; replace-CAS por (b)(c)+bytes, no sólo bytes; (a) [DESIGN] | Mutante overwrite/copy in-place (postimagen correcta, identidad del target retenida) | Host real no soporta create-new/flush/rename-no-replace/replace-CAS → S15 `DEFERRED` con primitiva nombrada; post-assert (b)(c)/bytes con probe OK → `INCONCLUSIVE/<código>` + `DEFERRED` (no `FAIL`); fallo de (a) con (c) OK → `DEFERRED`; atomicidad de un único replace → `DEFERRED` |

---

### B8 — matriz `StorageNodeReceipt` kind × operación

**MEDIUM**

[EXACT] Enum `StorageNodeKind` y dataclass en
`TEMP/S15-authority-options.md:270-279`; firmas `lstat` / `read_regular` /
`read_storage_tree` en `TEMP/S15-authority-options.md:285-291`; nulos de
preimagen en `TEMP/S15-authority-options.md:500-504`; walk
`TEMP/S15-authority-options.md:412-430`; exclusión de tipos fuera de A–F
`TEMP/S15-authority-options.md:557-560`. No existe tabla de legalidad
kind × operación en la ficha. Identidad real:
`tools/dayz_mcp/request_path_authority.py:54-57,173-185`
(`file_id` 32 hex MAYÚSCULAS). SHA-256 de contenido: 64 hex minúsculas
(`TEMP/S15-authority-options.md:419,528-529`). Fake y adapter atraviesan
el mismo filtro (`TEMP/S15-authority-options.md:757-760`).

**DECISIÓN**: adoptar la matriz de 15 celdas como contrato cerrado, con
doble enforcement: el adapter tiene prohibido emitir combinaciones
ilegales y el núcleo M15 valida todo receipt entrante contra V1–V14 antes
de interpretarlo. `absent` no es un tipo de nodo medible por
`read_regular`/`read_storage_tree`: ausencia sólo vía `lstat`.

Si se omite: el fake queda más permisivo que el adapter real y `ausente`
colapsa con `vacío`.

#### Dominio de los seis campos

| Campo | Dominio cerrado | Ancla |
|---|---|---|
| `exists` | `bool` | `TEMP/S15-authority-options.md:274` |
| `kind` | `absent`, `regular_file`, `directory`, `reparse`, `other` | `TEMP/S15-authority-options.md:270,275` |
| `identity` | `PathIdentity {volume_serial_number: int >= 0, file_id: ^[0-9A-F]{32}$}` o `None` | `TEMP/S15-authority-options.md:276,433-435`; `tools/dayz_mcp/request_path_authority.py:54-57,173-185` |
| `size` | `int >= 0` o `None` | `TEMP/S15-authority-options.md:277,419` |
| `file_sha256` | `^[0-9a-f]{64}$` o `None` | `TEMP/S15-authority-options.md:278,419,528-529` |
| `tree_sha256` | `^[0-9a-f]{64}$` o `None` | `TEMP/S15-authority-options.md:279,425-428` |

#### Combinaciones imposibles (V1–V14)

| Regla | Combinación imposible | Ancla |
|---|---|---|
| V1 | `exists=false` con `kind != "absent"` | `TEMP/S15-authority-options.md:270,500-504` |
| V2 | `kind="absent"` con `exists=true` | `TEMP/S15-authority-options.md:270,500-504` |
| V3 | `exists=false` con `identity`, `size`, `file_sha256` o `tree_sha256` no nulos | `TEMP/S15-authority-options.md:487,495,500-504` |
| V4 | `exists=true` con `identity=None` | `TEMP/S15-authority-options.md:500-504` |
| V5 | `size` no nulo fuera de `regular_file`; `size < 0`; en `regular_file` `size=None` | [DESIGN] sin ancla de autoridad previa; `TEMP/S15-authority-options.md:419` es el keyset de `StorageTreeEntryV1` (`kind="file"`, `size>=0`), no de `StorageNodeReceipt`; el dataclass declara `size` como `int` o `None` en `TEMP/S15-authority-options.md:277` sin obligar valor para fichero regular |
| V6 | `kind="directory"` con `size` o `file_sha256` no nulos | [DESIGN] sin ancla de autoridad previa; `TEMP/S15-authority-options.md:420` describe `kind="empty_directory"` de la entrada de árbol, no el receipt de `directory` |
| V7 | `kind="regular_file"` con `tree_sha256` no nulo | `TEMP/S15-authority-options.md:425-428` |
| V8 | `kind` en {`reparse`,`other`} con `size`, `file_sha256` o `tree_sha256` no nulos | [DESIGN] sin ancla de autoridad previa; `TEMP/S15-authority-options.md:557-560,632-634` excluyen `reparse`/`other` de A–F y niegan tratarlos como ausente; no fijan nulos del receipt |
| V9 | receipt producido por `lstat` con `file_sha256` o `tree_sha256` no nulos | [DESIGN] sin ancla de autoridad previa; `TEMP/S15-authority-options.md:82-86` regula mutex/handle/create-only, no hashes de `lstat` |
| V10 | `file_sha256`/`tree_sha256` no nulos que no casan `^[0-9a-f]{64}$` | `TEMP/S15-authority-options.md:419,528-529` |
| V11 | `identity` no nula con `file_id` fuera de `^[0-9A-F]{32}$` o `volume_serial_number < 0` | `TEMP/S15-authority-options.md:433-435`; `tools/dayz_mcp/request_path_authority.py:173-185` |
| V12 | `read_regular` o `read_storage_tree` que devuelven receipt con `exists=false` | [DESIGN] sin ancla de autoridad previa; `TEMP/S15-authority-options.md:684` es `INCONCLUSIVE/storage_marker_invalid` para marker ausente, no el retorno de esas lecturas |
| V13 | `read_regular` con `size != len(bytes)` o `file_sha256 != sha256(bytes)` | [DESIGN] sin ancla de autoridad previa; `TEMP/S15-authority-options.md:82-86` no fija la relación bytes/size/hash |
| V14 | `read_storage_tree` con `tree_sha256 != entries_sha256` del `StorageTreeV1` devuelto | `TEMP/S15-authority-options.md:427-428` |

V5, V6, V8, V9, V12 y V13 son [DESIGN] de este ADR, no [EXACT] de la ficha. Firmas de
las tres operaciones: [EXACT] `TEMP/S15-authority-options.md:285-291`.

#### `lstat(name) -> StorageNodeReceipt` (válida para los cinco kinds)

| Celda | ¿Válida? | Combinación legal de campos | Imposibles |
|---|---|---|---|
| CL-absent | Sí; única vía de descubrir ausencia | `exists=false, kind=absent, identity=None, size=None, file_sha256=None, tree_sha256=None` | V1–V3 |
| CL-regular_file | Sí | `exists=true, kind=regular_file, identity` válida, `size=int>=0` obligatorio, hashes `None` | V5, V9 |
| CL-directory | Sí | `exists=true, kind=directory, identity` válida, `size=None`, hashes `None` | V6, V9 |
| CL-reparse | Sí (clasifica sin seguir) | `exists=true, kind=reparse, identity` del propio reparse point, resto `None` | V8, V9; follow prohibido `TEMP/S15-authority-options.md:78-81` |
| CL-other | Sí | `exists=true, kind=other, identity` válida, resto `None` | V8, V9 |

#### `read_regular(name, *, maximum_bytes)` (válida sólo para `regular_file`)

| Celda | ¿Válida? | Combinación legal / rechazo |
|---|---|---|
| CR-absent | No | excepción interna; prohibido `(bytes, exists=false)` (V12) |
| CR-regular_file | Sí | `size=len(bytes)`, `file_sha256=sha256(bytes)` 64 hex min, `tree_sha256=None`; M15 usa `maximum_bytes=262144` (`TEMP/S15-authority-options.md:389-393`); tamaño real mayor que el tope ⇒ excepción, nunca truncar |
| CR-directory | No | excepción interna; no leer contenido de un directorio |
| CR-reparse | No | excepción interna; no seguir symlink/junction |
| CR-other | No | excepción interna; no IO de contenido sobre pipe/dispositivo |

#### `read_storage_tree(name)` (válida sólo para `directory`)

| Celda | ¿Válida? | Combinación legal / rechazo |
|---|---|---|
| CT-absent | No | prohibido árbol vacío con `exists=false` (V12); colapsaría ausente con vacío (`TEMP/S15-authority-options.md:495,500-504,550-552`) |
| CT-directory | Sí | `tree_sha256 = entries_sha256`; vacío ⇒ `entries=()` y `tree_sha256=sha256(b"[]")` (`TEMP/S15-authority-options.md:415-416,425-426`); walk interior, sin follow, entradas `file`/`empty_directory` (`TEMP/S15-authority-options.md:412-424`) |
| CT-regular_file | No | excepción interna |
| CT-reparse | No | excepción interna; un storage reparse bloquea y nunca es “ausente” (`TEMP/S15-authority-options.md:632-634`) |
| CT-other | No | excepción interna |

Homonimia: [EXACT] `StorageTreeEntryV1.kind` ∈ {`file`,`empty_directory`}
(`TEMP/S15-authority-options.md:138-143`) y `StorageNodeKind` de cinco
valores (`TEMP/S15-authority-options.md:270`). [DESIGN] no se mapean en
ambos sentidos. Proyección legal sólo dentro del walk: `regular_file` → `"file"`,
directorio vacío → `"empty_directory"`. Las citas no contienen contrato de
mapeo.

Proyección persistida [EXACT] keysets
`TEMP/S15-authority-options.md:500-504`: `marker_preimage={exists,identity,sha256←file_sha256}`,
`storage_preimage={exists,identity,tree_sha256}`; `size` y `kind` nunca
persisten.

---

### B9 — alias→absoluta no es un test unitario de M15

**LOW**

[EXACT] M15 recibe `resolved_mission` ya resuelta
(`TEMP/S15-authority-options.md:48-56,340-347`). El plan exige que alias
configurado y absoluta acreditada atraviesen `execute_dayz_test_run` hasta
worker/M15 (`plans/inbox-20260830/20-fb-20260829-115147-4407.md:31,58`).
Una misión que llegó como alias queda con `identities["mission"]=()`
(`tools/dayz_mcp/request_path_authority.py:563-573`). `_MISSION_ALIASES`
está en `tools/dayz_mcp/dayz_test_request.py:40`. `_mission` vive en
`tools/dayz_mcp/dayz_test_worker.py:188-194`.

**DECISIÓN**: confirmar B9. Pasar `"chernarus"` como `resolved_mission` a
M15 no acredita alias→absoluta; viola el contrato de entrada. Reinterpretar
«alias y absoluta equivalentes» de `TEMP/S15-authority-options.md:762-764`
en el OWNS de M15 como dos `resolved_mission` absolutas distintas que el
oráculo independiente demuestra físicamente equivalentes. Acreditación
pública alias→absoluta: M20 (`_mission`) y M19/M21 (`execute_dayz_test_run`).
Tests M15: (1) PASS con absoluta válida; (2) PASS con segunda absoluta
físicamente equivalente; (3) FAIL con string en `_MISSION_ALIASES` pasado
como `resolved_mission`; (4) FAIL con absoluta fuera de roots / cero o dos
roots / drift. No invocar `_mission` ni `runtime.mission_aliases` en
`test_dayz_test_storage.py`.

Si se omite: M15 se declara PASS sin que `_mission` exista; el consumidor
real (M20) sigue roto.

| Capa | OWNS / test | Qué prueba | Qué NO prueba |
|---|---|---|---|
| M18 | `dayz_test_request.py` | Alias en allowlist o absoluta en roots | Resolución a path launcher |
| M20 | `test_dayz_test_worker.py` | `_mission` alias→absoluta | Binding/storage marker |
| M19/M21 público | `test_dayz_test_tool.py` | `execute_dayz_test_run` alias y absoluta hasta M15 | Unit aislamiento M15 |
| M15 | `test_dayz_test_storage.py` | `resolved_mission` absoluta + roots sellados → binding; rechazo alias literal | `_mission`, `mission_aliases` |

---

### B10 — mutex nombrado: `WAIT_ABANDONED` no es flujo normal

**HIGH**

[EXACT] Tras acreditar misión, el adapter adquiere mutex
`Local\DayZMCP.Storage.v1.<sha256(canonical mission_binding_v1)>`, exclusivo
también para lectores, hasta cerrar el context. Fallo al crear/esperar
produce `INCONCLUSIVE/storage_filesystem_unverifiable` sin writes
(`TEMP/S15-authority-options.md:350-358`). El fake no puede producir
`WAIT_ABANDONED`; no acredita esta rama.

[DESIGN] `WAIT_ABANDONED` concede la propiedad del mutex y obliga a
comprobar el estado persistente **antes** de continuar. No se reabrió la
doc de Microsoft en esta sesión (ver `LO QUE NO PUDE VERIFICAR`); los
valores numéricos 0 / 0x80 / 258 / 0xFFFFFFFF se tratan como [DESIGN], no
como [EXACT] de una API inventada aquí.

**DECISIÓN**: tratar `WAIT_ABANDONED` como adquisición con estado
sospechoso: se posee el mutex, **no** se continúa el flujo normal. Con
`exclusive=True`, se ejecuta el pre-pass de recovery (reacreditar
raíz/misión, enumerar journals, reconciliar fase declarada y estado
físico, `TEMP/S15-authority-options.md:583-610`) *dentro* del mismo
context; 4a/4b sí escriben recovery journalizado; si no converge,
`INCONCLUSIVE/storage_recovery_required` sin writes fuera del recovery;
el fallo deja journal activo o `recovery_required` reacreditable. Con
`exclusive=False` (lector), **nunca** escribe: «**no** ejecuta recovery,
no crea, no reemplaza y no limpia»
(`TEMP/S15-authority-options.md:661-662`); el lector «nunca repara ni
completa datos»
(`plans/inbox-20260830/20-fb-20260829-115147-4407.md:40`).
`INCONCLUSIVE/storage_recovery_required` sin writes.
El gate real
que produce `WAIT_ABANDONED` usa un nombre de test
`Local\DayZMCP.Storage.v1.test.<uuid>` (no el mutex de producción), un hijo
que adquiere y termina sin `ReleaseMutex`, y un `TemporaryDirectory`
aislado. Si el host no es win32 local, skip → `DEFERRED`.

Si se omite: un owner que muere con el mutex tomado deja un journal a medio
publicar; el siguiente adquirente publica un marker nuevo sobre
`storage_moved` no reconciliado.

#### Matriz — wait del mutex (`WAIT_OBJECT_0` / `WAIT_ABANDONED` / `WAIT_TIMEOUT` / `WAIT_FAILED` / handle nulo / fallo de creación)

[DESIGN] resultados y acciones. [EXACT] ancla
`TEMP/S15-authority-options.md:350-358`.

| Resultado del wait | ¿Adquiere el mutex? | ¿Continúa flujo normal? | Veredicto | `error_code` | ¿Escribe? | Acción del adapter |
|---|---|---|---|---|---|---|
| `WAIT_OBJECT_0` | Sí | Sí | lo deciden A–F | `None` (pre-start) o el de la celda | Sólo si la celda A–F muta | reacreditar raíz/misión, enumerar journals, pre-pass/recovery si aplica, luego A–F |
| `WAIT_ABANDONED` | Sí (concede propiedad) | **No** — estado sospechoso | `exclusive=True`: `INCONCLUSIVE` si recovery no converge; si converge, sigue A–F. `exclusive=False`: `INCONCLUSIVE` | `storage_recovery_required` (lector siempre; mutador si no converge; si el mutador converge, el de la celda) | Sólo `exclusive=True`: recovery journalizado (4a/4b); no fuera del recovery. `exclusive=False`: **No** | `exclusive=True`: pre-pass *dentro* del context; si no converge, `INCONCLUSIVE/storage_recovery_required` sin writes fuera del recovery. `exclusive=False`: `INCONCLUSIVE/storage_recovery_required` sin writes (`TEMP/S15-authority-options.md:661-662`) |
| `WAIT_TIMEOUT` | No | No | `INCONCLUSIVE` | `storage_filesystem_unverifiable` | No | otro proceso M15 retiene el mutex; devolver sin writes |
| `WAIT_FAILED` | No | No | `INCONCLUSIVE` | `storage_filesystem_unverifiable` | No | capturar diagnóstico; abortar sin writes (`TEMP/S15-authority-options.md:719-720`) |
| Handle nulo (`CreateMutexW` no entrega handle) | No | No | `INCONCLUSIVE` | `storage_filesystem_unverifiable` | No | no se crea lock file antes del journal (`TEMP/S15-authority-options.md:356-357`) |
| Fallo de creación (excepción al crear) | No | No | `INCONCLUSIVE` | `storage_filesystem_unverifiable` | No | normalizar la excepción del seam; abortar sin writes |

`WAIT_ABANDONED` es el único resultado que adquiere el mutex sin ser
`WAIT_OBJECT_0`. `WAIT_TIMEOUT` y `WAIT_FAILED` no adquieren; continuar en
esos casos sería operar sin exclusión.

---

## Compatibilidad, legacy y rollback reversible

### Alternativa que no cambia formato (presentada primero)

[EXACT] `TEMP/S15-authority-options.md:745-746`;
`plans/inbox-20260830/20-fb-20260829-115147-4407.md:49`.

**Storage efímero por run** (sin marker, sin journal, sin rotación durable):
no introduce formato nuevo. Se **rechaza**: pierde continuidad entre runs
y no satisface H11 — el aviso de reset de mundo/personaje debe persistir
hasta acreditar storage nuevo, y el fingerprint por roles debe proteger
`<mission>/storage_1` antes de crear server/offline (`product-spec.md:146`).
Un árbol que desaparece al terminar el proceso no puede portar
`rotation_pending` ni compensar journals.

La opción elegida es la única: marker sibling + journal por txid + rotación
por rename no-replace.

### Legacy

[DESIGN] `TEMP/S15-authority-options.md:742-744`;
`plans/inbox-20260830/20-fb-20260829-115147-4407.md:46-48`.

- A/D distinguen legacy sin marker: storage legacy rota una vez; misión sin
  storage sólo recibe marker. No se anuncia reset en A.
- Marker futuro/malformado nunca se “migra” interpretando campos: C/F lo
  apartan por nombre fijo y conservan bytes.
- Código antiguo ignora siblings nuevos.
- Sidecar/journal sin binding cerrado, con keyset de otra versión o con un
  path libre nunca se interpreta como autoridad: bloquea/recupera
  fail-closed, sin fallback alias-only ni **migración destructiva**.

### Rollback reversible (sin deletes)

[DESIGN] `TEMP/S15-authority-options.md:747-753`;
`plans/inbox-20260830/20-fb-20260829-115147-4407.md:35,48,57`;
nombres `.rollback` en `TEMP/S15-authority-options.md:525-526`;
aviso H11 en `product-spec.md:146`.

Operación humana documentada, bajo el mismo lock. **No** es la secuencia
feliz D/F a secas: tiene journal durable propio, fases, compensación
idempotente y recovery **antes** de A–F. Una caída a mitad no puede
clasificarse como A (ambos ausentes → marker nuevo) ni como B (storage
ausente + marker exacto, sin mutación).

#### Journal rollback-op (create-only antes del primer rename)

[DESIGN] gramática paralela a la de rotación
(`TEMP/S15-authority-options.md:521-524,533-535`); **no** reutiliza
`storage_1.rotation.<txid>.json` (kind y recovery distintos). Kind exacto
`"dayz-test-storage-rollback-journal-v1"`. `schema_version=1`.

| Uso | Nombre exacto [DESIGN] |
|---|---|
| journal activo | `storage_1.rollback-op.<txid>.json` |
| journal completed | `storage_1.rollback-op.<txid>.completed.json` |
| candidate CAS del journal | `storage_1.rollback-op.<txid>.candidate.json` |
| storage post-cambio apartado | `storage_1.rollback.<txid>.<tree_sha256[0:12]>` ([EXACT] `:525`) |
| marker post-cambio apartado | `storage_1.modset.rollback.<txid>.<marker_sha256[0:12]>.json` ([EXACT] `:526`) |

Activo: `^storage_1\.rollback-op\.([0-9a-f]{32})\.json$`. Completed:
`^storage_1\.rollback-op\.([0-9a-f]{32})\.completed\.json$`. Candidate:
`^storage_1\.rollback-op\.([0-9a-f]{32})\.candidate\.json$`. Cualquier
sibling `storage_1.rollback-op.` que no case esas tres gramáticas bloquea.
El lector aplica las mismas tres gramáticas ([DESIGN]; la autoridad del
lector sólo cierra `storage_1.rotation.*` en
`TEMP/S15-authority-options.md:661-663,695-696`): activo →
`INCONCLUSIVE/storage_recovery_required`; fuera de gramática →
`INCONCLUSIVE/storage_journal_blocked`; completed canónico ligado no
bloquea. `read_storage_projection` no emite PASS sobre un rollback-op
activo.
Create-only: si el activo de ese txid ya existe, no se inicia un segundo
rollback; se entra en recovery. Colisión case-fold o destino `.rollback`
preexistente: `INCONCLUSIVE/storage_recovery_required`, sin mutación.

Keyset top-level **exacto** [DESIGN]: `algorithm`, `fingerprint_sha256`,
`kind`, `marker_apart_name`, `marker_current_preimage`,
`marker_legacy_preimage`, `mission_binding_v1`, `phase`,
`recovery_required`, `roles`, `schema_version`, `storage_apart_name`,
`storage_current_preimage`, `storage_legacy_preimage`, `txid`.
Preimágenes: mismos keysets que la rotación
(`TEMP/S15-authority-options.md:500-504`):
`{exists,identity,sha256}` el marker y `{exists,identity,tree_sha256}` el
storage; `exists=false` ⇒ los tres campos secundarios nulos.
`storage_apart_name` es JSON `null` cuando no hay apartado de storage
(C/A siempre; D/F si `storage_current_preimage.exists=false`). No hay
`storage_backup_name` ni `marker_backup_name` en este keyset: viven en el
marker de rotación (`TEMP/S15-authority-options.md:443-457`) y en el
journal de rotación (`TEMP/S15-authority-options.md:487,494`).
`RB-STO-RST` / `RB-MRK-RST` toman `source_name` del marker nombrado por
`marker_apart_name` (journalizado): `storage_backup_name` /
`marker_backup_name` de la rotación que se revierte. Si ese marker no es
legible, `list_siblings`+`lstat` localiza el sibling cuya `identity` casa
con `storage_legacy_preimage` / `marker_legacy_preimage`
(`TEMP/S15-authority-options.md:284-285`). El digest se reacredita contra
la preimagen; «Nunca se infiere un digest del sufijo de 12 hex ni del
marker» (`TEMP/S15-authority-options.md:671-672`). El `txid` del
rollback-op no es el de la rotación (`TEMP/S15-authority-options.md:519-520`).
Cada
transición CAS del journal conserva txid, binding, roles, fingerprint,
preimágenes y nombres; cambia sólo `phase` / `recovery_required`. El
replace exige SHA+identidad de la versión previa
(`TEMP/S15-authority-options.md:507-509`).

`phase` pertenece exactamente a
`{prepared, storage_apart, marker_apart, storage_restored, marker_restored}`.
El completed —comprometido o compensado— se alcanza por rename no-replace
del activo al nombre completed, con los mismos bytes; no se borra.

#### Orden de mutaciones y pre/postimagen

Escritura `prepared` (create + write completo + `fsync` + reopen +
readback) **antes** de cualquier rename. Después, según la celda de origen:

| Origen | secuencia (cada rename va seguido de CAS de fase) | estado committed |
|---|---|---|
| F | `prepared` → storage current→apart (omitido si `storage_current_preimage.exists=false`; `storage_apart_name=null`) → `storage_apart` → marker current→apart → `marker_apart` → backup storage→`storage_1` → `storage_restored` → backup marker→current → `marker_restored` → completed | storage+marker = legacy por identidad+digest |
| D | `prepared` → storage current→apart (omitido si `storage_current_preimage.exists=false`; `storage_apart_name=null`) → `storage_apart` → marker nuevo→apart → `marker_apart` → backup storage→`storage_1` → `storage_restored` → completed (legacy marker `exists=false`: no hay `marker_restored`) | storage = legacy; marker current **ausente** |
| C | `prepared` → marker current→apart → `marker_apart` → backup marker→current → `marker_restored` → completed | sólo marker = legacy; storage no se toca |
| A | `prepared` → marker nuevo→apart → `marker_apart` → completed | marker current ausente; no hay storage que restaurar |

Rename siempre a destino ausente (no-replace). No `os.replace` sobre un
current que este txid no apartó. No unlink sin rename previo. Si cualquier
preimagen o destino no casa, se publica `recovery_required=true` por CAS
si es posible y se detiene **sin** interpretar el formato nuevo como
compatible. Datos post-cambio quedan en siblings `.rollback`, recuperables.

#### Recovery de rollback-op (antes de A–F)

El pre-pass del paso 4a enumera journals rollback-op **antes** de los de
rotación y **antes** de clasificar `storage_1` ausente. Launch bloqueado
hasta completed o `recovery_required`. La matriz se indexa por
`(origen A/C/D/F, fase declarada, estado físico observado, candidate
CAS)`, no sólo por la fase y no por aborto/intent (K1: ese bit no está
en el keyset). `storage_1.rollback-op.<txid>.candidate.json`
∈ `{ausente, exacto, discrepante}`. Conserva el orden de la secuencia
normal de esa celda. Por cada fase existe la fila legal rename ya
ocurrió, CAS aún no publicado: se reconoce por identidad+digest, se
publica el CAS omitido y se continúa **sin repetir** esa mutación. En
ese CAS y en el que publica `recovery_required`: candidate **exacto** ⇒
reabrir y acreditar el único transform esperado, publicar; **ausente** ⇒
regenerarlo create-only + fsync + readback y publicar; **discrepante** ⇒
residual, no adoptar. Un I/O por delante es recuperable; dos o más, o
un current que no casa con ninguna preimagen, es contradicción.

Reglas de orden que ninguna fila puede violar:

- En D/F, `storage_apart` aparta el marker **antes** de restaurar storage.
- En D/F, `marker_apart` restaura storage **antes** de restaurar o
  completar el marker.
- Hay exactamente dos terminales. **Completed comprometido** (rename
  journal → completed tras restaurar): exige reacreditar cada current
  que esa celda promete como legacy: F, storage y marker por identidad+digest;
  D, sólo storage (marker current ausente, legacy `exists=false`); C,
  sólo marker (storage intacto = preimagen current); A, marker current
  ausente y storage ausente. Un journal completed comprometido con
  `storage_1` ausente en D/F es FAIL del contrato
  (`plans/inbox-20260830/20-fb-20260829-115147-4407.md:48,54-57`;
  `product-spec.md:146`).
- **Completed compensado** (abort antes del primer rename; filas
  `prepared` sin apartado): exige preimagen current intacta —no el
  legacy—, apartados y candidate ausentes, y resultado de aborto
  explícito. No restaura. Conserva fase `prepared`
  (`TEMP/S15-authority-options.md:538-540,586-588`).
  `aborto explícito` no es campo del keyset ni del índice
  `(origen × fase × estado físico × candidate CAS)`. Con
  `storage_current_preimage.exists=false` esa fila y la de continuar
  (publicar `storage_apart` omitiendo `RB-STO-APART`) son la misma
  imagen durable. Distinguirlas exige un campo nuevo o una transición
  de aborto persistida antes de compartir estado: **K1 abierto**
  (ver `LO QUE NO PUDE VERIFICAR`).

Tras cada frontera se releen current, apartados y backups.

| origen | fase declarada | estado físico observado | acción | ¿restaura? | CAS omitido | candidate CAS |
|---|---|---|---|---|---|---|
| F | `prepared` | currents = preimagen current; apartados ausentes; aborto explícito | completed compensado | no | — | ausente: compensar; exacto: retirar; discrepante: residual |
| F | `prepared` | `storage_current_preimage.exists=false`; storage current ausente; marker current = preimagen current; apartados ausentes | publicar `storage_apart` sin `RB-STO-APART`; `storage_apart_name=null`; continuar apartando marker | no aún | `storage_apart` | ausente: regenerar; exacto: reabrir+acreditar; discrepante: residual |
| F | `prepared` | storage apartado = preimagen current; marker current intacto | publicar `storage_apart`; no repetir `RB-STO-APART`; continuar apartando marker | no aún | `storage_apart` | ausente: regenerar; exacto: reabrir+acreditar; discrepante: residual |
| F | `storage_apart` | storage apartado = preimagen current, o `storage_apart_name=null` con preimagen `exists=false` y storage current ausente; marker current intacto | `RB-MRK-APART`; no restaurar storage todavía | no | — | ausente |
| F | `storage_apart` | storage y marker apartados = preimagen current, o `storage_apart_name=null` con preimagen storage `exists=false` y storage current ausente y marker apartado = preimagen current; currents ausentes | publicar `marker_apart`; no repetir `RB-MRK-APART` | no aún | `marker_apart` | ausente: regenerar; exacto: reabrir+acreditar; discrepante: residual |
| F | `marker_apart` | ambos apartados, o `storage_apart_name=null` con preimagen storage `exists=false` y storage current ausente y marker apartado = preimagen current; currents ausentes | `RB-STO-RST`; no restaurar marker todavía; no completar | sí, sólo storage | — | ausente |
| F | `marker_apart` | storage current = legacy; marker current ausente; marker apartado = preimagen current | publicar `storage_restored`; no repetir restore de storage | no | `storage_restored` | ausente: regenerar; exacto: reabrir+acreditar; discrepante: residual |
| F | `storage_restored` | storage current = legacy; marker current ausente; marker apartado = preimagen current | `RB-MRK-RST` | sí, marker | — | ausente |
| F | `storage_restored` | currents = legacy (identidad+digest) | publicar `marker_restored`; no repetir restore de marker | no | `marker_restored` | ausente: regenerar; exacto: reabrir+acreditar; discrepante: residual |
| F | `marker_restored` | currents = legacy reacreditados; apartados presentes | rename journal → completed | no | — | ausente |
| D | `prepared` | storage current = preimagen current; marker nuevo current; apartados ausentes; aborto explícito | completed compensado | no | — | ausente: compensar; exacto: retirar; discrepante: residual |
| D | `prepared` | `storage_current_preimage.exists=false`; storage current ausente; marker nuevo current; apartados ausentes | publicar `storage_apart` sin `RB-STO-APART`; `storage_apart_name=null`; continuar apartando el marker nuevo | no aún | `storage_apart` | ausente: regenerar; exacto: reabrir+acreditar; discrepante: residual |
| D | `prepared` | storage apartado = preimagen current; marker nuevo current intacto | publicar `storage_apart`; no repetir `RB-STO-APART`; continuar apartando el marker nuevo | no aún | `storage_apart` | ausente: regenerar; exacto: reabrir+acreditar; discrepante: residual |
| D | `storage_apart` | storage apartado, o `storage_apart_name=null` con preimagen `exists=false` y storage current ausente; marker nuevo current intacto | `RB-MRK-APART` del marker nuevo; no `RB-STO-RST` todavía | no | — | ausente |
| D | `storage_apart` | storage apartado, o `storage_apart_name=null` con preimagen storage `exists=false` y storage current ausente; marker current ausente; marker apartado = preimagen current (nuevo) | publicar `marker_apart`; no repetir apartado de marker | no aún | `marker_apart` | ausente: regenerar; exacto: reabrir+acreditar; discrepante: residual |
| D | `marker_apart` | storage apartado, o `storage_apart_name=null` con preimagen storage `exists=false` y storage current ausente; marker current ausente | `RB-STO-RST`; **no** completar todavía; **no** tratar ausencia de marker como D restaurado | sí, sólo storage | — | ausente |
| D | `marker_apart` | storage current = legacy; marker current ausente | publicar `storage_restored`; no repetir restore de storage | no | `storage_restored` | ausente: regenerar; exacto: reabrir+acreditar; discrepante: residual |
| D | `storage_restored` | storage current = legacy reacreditado; marker current ausente (legacy `exists=false`) | rename journal → completed; prohibido si storage no casa o el marker current reapareció | no | — | ausente |
| C | `prepared` | marker current = preimagen current; storage intacto; apartados ausentes; aborto explícito | completed compensado | no | — | ausente: compensar; exacto: retirar; discrepante: residual |
| C | `prepared` | marker apartado = preimagen current; marker current ausente; storage intacto | publicar `marker_apart`; no repetir `RB-MRK-APART` | no aún | `marker_apart` | ausente: regenerar; exacto: reabrir+acreditar; discrepante: residual |
| C | `marker_apart` | marker apartado; marker current ausente; storage intacto | `RB-MRK-RST` | sí, marker | — | ausente |
| C | `marker_apart` | marker current = legacy; storage intacto | publicar `marker_restored`; no repetir restore de marker | no | `marker_restored` | ausente: regenerar; exacto: reabrir+acreditar; discrepante: residual |
| C | `marker_restored` | marker current = legacy reacreditado; storage intacto | rename journal → completed | no | — | ausente |
| A | `prepared` | marker nuevo current; storage ausente; apartados ausentes; aborto explícito | completed compensado | no | — | ausente: compensar; exacto: retirar; discrepante: residual |
| A | `prepared` | marker apartado = preimagen current; marker current ausente; storage ausente | publicar `marker_apart`; no repetir apartado | no aún | `marker_apart` | ausente: regenerar; exacto: reabrir+acreditar; discrepante: residual |
| A | `marker_apart` | marker current ausente; marker apartado = preimagen current; storage ausente | rename journal → completed; no inventar storage ni restaurar un marker legacy `exists=false` | no | — | ausente |
| cualquiera | cualquiera | contradicción, segundo journal rollback-op, drift de preimagen, completed colisionado, dos I/O por delante, fase inalcanzable para el origen (p. ej. D en `marker_restored`, C/A en `storage_apart`), current que no casa, o candidate **discrepante** | `recovery_required=true` por CAS si es posible (ausente: regenerar transform `recovery_required`; exacto: reabrir+acreditar; discrepante: no adoptar, sin CAS) | no; bloquea launch | — | ausente / exacto / discrepante |

Un rollback-op activo **vence** a A–F y al PASS del lector: storage current
ausente con marker intacto y journal en `storage_apart` **no** es B, y
`read_storage_projection` sobre ese estado es
`INCONCLUSIVE/storage_recovery_required`, no PASS. Marker y storage ambos
ausentes con journal en `marker_apart` **no** es A.

#### Inyección de fallo

RC-08 cubre cada frontera `RB-*` (no sólo D/F felices). La caída se inyecta
**después de cada I/O y después de cada CAS por separado**, no después de
cada paso agrupado: crash tras rename y antes del CAS, crash tras create/readback
del candidate CAS y antes del replace, y crash tras el CAS
y antes del siguiente rename; también antes y después del rename a
completed. Reentrada debe converger o bloquear, nunca lanzar. Mutante que
omite el journal, que publica marker nuevo sobre un apartado, que restaura
marker antes que storage en D/F, o que completa D con `storage_1` ausente
= FAIL del gate.

Datos post-cambio recuperables. No hay migración destructiva.

---

## Gate NTFS real (obligatorio)

El gate B6/RC-09 es **obligatorio** para promover S15. Condición
`DEFERRED`: si el host real no acredita create-new, flush/reopen,
rename-no-replace, replace-CAS o handles no-reparse, S15 queda `DEFERRED`
con la primitiva concreta no acreditada
(`TEMP/S15-authority-options.md:796-801`). El fake no satisface este gate.
Un skip de plataforma no es `PASS`.

---

## Veredicto documental

S15/M15 queda **autoridad sucesora v9 fail-closed**: orden normativo B1, arbitraje B2
decidido por el usuario, recovery físico B3, taxonomía de lector B4 bajo
el plan congelado, proyección limitada B5, gate NTFS real con `DEFERRED`
B6/B10, ordinales no tautológicos B7, matriz de receipts B8, y B9
acotado al OWNS de M15. El rollback-op (H1/N1) tiene journal durable, fases,
matriz `(origen × fase × estado físico × candidate CAS)` con rename-hecho/CAS-no-publicado,
y recovery antes de A–F. El lector no emite PASS si hay rollback-op activo.
El pending de F (H3/N2) viaja en candidate y
journal `prepared`; C con pending no se recorre
(`INCONCLUSIVE/storage_fingerprint_mismatch`, bytes intactos). 4d no sustituye el marker current salvo el CAS terminal+A=B,
independiente del fp solicitado. El bundle sucesor `v9` concede a M15
exactamente `tools/dayz_mcp/dayz_test_storage.py` y
`tools/tests/test_dayz_test_storage.py`, y ninguno más. No convierte
`REVISE`, `DEFERRED`, `INCONCLUSIVE` ni `REVIEW_PENDING` en `PASS`.

Ronda 15: X1 — B6 post-assert con probe OK → `INCONCLUSIVE/<código>` +
`DEFERRED`, no `FAIL` (plan autoridad; misma disciplina que B4;
`:713-719` y `20-fb:61`). X2 — B3 celda D `storage_moved` con ambos
currents ausentes **compensa** storage (`:589-590`; `20-fb:35`), no
publica marker. X3 — la cita de coherencia del fingerprint es
`:465-467`. K1 sigue abierto.
Ronda 14: V3 — autorreferencia `adr-s15-m15-storage.md`. W3 — autoridad
vigente `20260901-ledger-v5` / `inbox-20260901-v8` / bundle `v9`;
predecesor: bundle `v8`. K1 sigue abierto.
Ronda 13: el bundle sucesor `v9` concede los dos OWNS de M15. K1 sigue
abierto.
Ronda 12: cerrados M3 (`WAIT_ABANDONED` no escribe en el lector:
`exclusive=False` → `INCONCLUSIVE/storage_recovery_required` sin writes),
M4 (`storage_moved`+marker intacto es F tras `RN-STO`, no C; residual de
fase inalcanzable) y M5 (fp original no es opción del llamante si el
modset anterior no es reproducible; desbloqueo = rollback humano).
Ronda 11: cerrados J1 (fallo de (a) con (c) OK → `DEFERRED`, no `FAIL`;
`:347-351` sólo sostiene (c)), J3 (`source_name` de `RB-STO-RST` /
`RB-MRK-RST` nombrado sin ampliar el keyset), J4 (pending+fp distinto+
storage ausente → `INCONCLUSIVE/storage_fingerprint_mismatch`, no
`storage_filesystem_unverifiable`) y K3 (el gate
acredita postimagen e identidad no retenida; atomicidad de un único
replace `DEFERRED`). **K1 abierto**: `prepared` con
`storage_current_preimage.exists=false` no distingue aborto explícito de
continuar; el keyset no tiene ese bit.
Ronda 10: cerrados H1 (replace-CAS: post-assert de identidad pre-call, no
sólo bytes; mutante overwrite/copy in-place rojo; durabilidad `DEFERRED`)
y G2 (D/F con `storage_1` ausente omiten `RB-STO-APART`,
`storage_apart_name=null`, restauran backup).
Ronda 9: cerrados F1 (replace atómico, CAS lógico y durabilidad separados;
pasar `_REPLACEFILE_WRITE_THROUGH` no acredita write-through; durabilidad
`DEFERRED`), F2 (`WAIT_ABANDONED`: writes sólo del recovery journalizado;
fallo deja journal activo o `recovery_required` reacreditable), F3 (dos
terminales: completed comprometido vs compensado), F4 (homonimia de kind
[EXACT]; prohibición de mapeo [DESIGN]), F5 (citas literales “Marker exacto”
/ `inválido/mismatch` y Si falta storage / sólo sustituye) y E1
(`error_code=storage_filesystem_unverifiable` en A≠B/desaparición/inestable
y residuales INCONCLUSIVE de B1/pending; no `storage_cas_mismatch`).
Ronda 8: cerrados D1 (lector: `rollback-op` activo →
`storage_recovery_required`, fuera de gramática → `storage_journal_blocked`;
[DESIGN] la autoridad sólo enumeraba `storage_1.rotation.*`), D2 (fila
`fp igual` enumera ausente e inestable/A≠B), D3 (V5, V6 y V8 [DESIGN] como
V9/V12/V13), D4 (B6 usa `TemporaryDirectory`, no `GetTempPath`) y D5
(`WRC@W-MRK` = `step_id` `W-MRK`).
Ronda 7: cerrados C1 (B1 acotado a pending; la frase de reclasificar journal
se ancla en `:546`, no en `20-fb:35`), C2 (B5 descarta `:682`; el plan es
autoridad vía `20-fb:42`), C4 (cinco códigos inalcanzables) y C5 (`CAS-PRE`
cubre E, F). Ronda 6: Q3 y R1 siguen cerrados. Q2, P4, P5, P6, H1, H11, N1,
N2 siguen cerrados.

La implementación es una fase posterior y requiere un prompt nuevo, hashes
de este ADR ya aprobado (`v9`) y gates propios. Los dos OWNS ya están
concedidos.

---

## LO QUE NO PUDE VERIFICAR

1. **El FINAL `verify_successor_r3` no tiene artefacto on-disk hash-pineado
   disponible.** No pude abrirlo. Los diez bloqueos y sus severidades
   (CRITICAL/HIGH/MEDIUM/LOW) llegan por el brief/handoff, no por ese
   FINAL. Ítem por ítem, revalidación contra fuente pineada en esta
   sesión:
   - **B1** — revalidado contra `TEMP/S15-authority-options.md:544-555,546,624-642`
     y `plans/inbox-20260830/20-fb-20260829-115147-4407.md:35,37`.
     Pending+fp distinto+`storage_1` ausente: `INCONCLUSIVE/storage_fingerprint_mismatch`
     ([DESIGN]; J4), no `storage_filesystem_unverifiable`. **M5**: fp original
     sólo si el modset anterior es reproducible (`:465-467`); si no, rollback
     humano (`:747-753`).
   - **B2** — revalidado contra `TEMP/S15-authority-options.md:639-642` y
     `plans/inbox-20260830/20-fb-20260829-115147-4407.md:39,56,60,61`; la
     resolución fail-closed es decisión del usuario, no del FINAL;
     `error_code=storage_filesystem_unverifiable` en A≠B/desaparición/inestable
     es [DESIGN] (no el `storage_cas_mismatch` de `:641-642`).
   - **B3** — revalidado contra `TEMP/S15-authority-options.md:586-602` y
     `plans/inbox-20260830/20-fb-20260829-115147-4407.md:35,57`. **M4**:
     `storage_moved`+marker intacto es F tras `RN-STO` (D,F), no C; residual
     de fase inalcanzable para la celda. **X2**: celda D `storage_moved`
     con ambos currents ausentes **compensa** (devuelve backup storage;
     no publica marker); `:589-590` aplica, no se sustituye por
     roll-forward.
   - **B4** — revalidado contra `TEMP/S15-authority-options.md:3-13,682-689`
     y `plans/inbox-20260830/20-fb-20260829-115147-4407.md:61`; la extensión
     del lector a `rollback-op` es [DESIGN] (autoridad sólo enumera
     `storage_1.rotation.*` en `TEMP/S15-authority-options.md:531-536,661-663,695-696`).
   - **B5** — revalidado contra `TEMP/S15-authority-options.md:157-164,464-477,682,698-704`
     y `plans/inbox-20260830/20-fb-20260829-115147-4407.md:40-42`.
   - **B6** — revalidado contra `TEMP/S15-authority-options.md:359-364,713-719,757-760,796-801`
     y `tools/dayz_mcp/launcher_registry_update.py:35,101-129`; la suite real
     usa `TemporaryDirectory` (`TEMP/S15-authority-options.md:757`), no `GetTempPath`.
     Pasar `_REPLACEFILE_WRITE_THROUGH` no acredita write-through; durabilidad
     `DEFERRED`; atomicidad de un único replace `DEFERRED` (K3). (a) es
     [DESIGN]; `:347-351` sostiene (c), no (a); fallo de (a) con (c) OK →
     `DEFERRED` (J1). **X1**: fallo de (b), (c) o bytes con probe OK →
     `INCONCLUSIVE/<código>` + `DEFERRED`, no `FAIL`
     (`TEMP/S15-authority-options.md:713-719`;
     `plans/inbox-20260830/20-fb-20260829-115147-4407.md:61`). No se ejecutó el mutante overwrite in-place ni
     `delete+rename` en este host.
   - **B7** — revalidado contra `TEMP/S15-authority-options.md:757-760,771-773`.
   - **B8** — revalidado contra `TEMP/S15-authority-options.md:270-291,412-430,500-504,757-760`.
   - **B9** — revalidado contra `TEMP/S15-authority-options.md:48-56,762-764`
     y `plans/inbox-20260830/20-fb-20260829-115147-4407.md:31,58`.
   - **B10** — revalidado contra `TEMP/S15-authority-options.md:350-358`;
     semántica numérica de `WAIT_*` **pendiente** (doc Microsoft no reabierta);
     `WAIT_ABANDONED` escribe recovery journalizado **sólo** con
     `exclusive=True`; el lector (`exclusive=False`) no escribe
     (`TEMP/S15-authority-options.md:661-662`).
   Siguen pendientes: el texto original de `verify_successor_r3`, la
   implementación M15 (los dos paths aún no existen en disco; `v9` ya
   concede esos OWNS;
   `plans/inbox-20260830/physical-ownership-addendum-v1.md:64`), y la
   corrida host de RC-09.
   Hallazgos H1, H2, H4, H5, H11, N1 y N2 siguen cerrados (no reabiertos).
   P4: añadida la celda D `storage_moved` con ambos currents ausentes.
   P5: 4a/4b exceptúan la prohibición de write. Q3: 4d posee el CAS
   terminal+A=B con preimagen del marker actual, también si el fp
   solicitado es distinto; ausente e inestable no reutilizan esa fila.
   R1: C con pending no publica; `INCONCLUSIVE/storage_fingerprint_mismatch`
   y bytes intactos (J4). No se
   escribe carry-forward/lineage de backup entre txids. P6: V5/V6/V8/V9/V12/V13
   ya no se anclan como [EXACT] a líneas que no sustentan su semántica.
   Q2: cada CAS de rollback-op (incluido `recovery_required`) indexa
   candidate `{ausente, exacto, discrepante}`. No queda hallazgo abierto
   de ronda 3 ni Q2/Q3/R1 en este ADR. J1, J3, J4 y K3 de ronda 11
   quedan cerrados en el contrato documental. M3, M4 y M5 de ronda 12
   quedan cerrados en el contrato documental. No ejecuté
   las fronteras de fallo ni `os.fsync` en este host; la
   durabilidad ante pérdida de alimentación y la atomicidad de un único
   replace siguen `DEFERRED` (B6).
   H1/G2 de ronda 10 quedan cerrados en el contrato documental, no en
   una corrida host.
2. No ejecuté tests, DayZ ni el gate NTFS. `observe_run` es un seam sin
   implementación; la definición de **ausencia estable** presupone tres
   observaciones baratas — no lo medí.
3. No reabrí `tools/dayz_mcp/win32_fileinfo.py`, `registry_lock.py` ni
   `identity_migration.py` (fuera de la copia staged). La semántica de
   identidad usada es la reexportada en
   `tools/dayz_mcp/request_path_authority.py:54-57,173-185,188-235`.
4. Predicado Win32 concreto que distingue `other` de
   `regular_file`/`directory`/`reparse`: las fuentes no lo especifican; no
   lo invento.
5. Igualdad `[IO.Path]::GetTempPath()` vs `$env:TEMP` vs constante `T`
   del addendum: no medida en esta máquina. B6 no la exige: la suite real
   usa `TemporaryDirectory` (`TEMP/S15-authority-options.md:757`), no esa API.
6. **K1 abierto.** Con journal `prepared`,
   `storage_current_preimage.exists=false`, storage current ausente,
   marker current = preimagen, apartados ausentes y candidate CAS
   ausente, las filas de aborto explícito (completed compensado) y de
   continuar (publicar `storage_apart` omitiendo `RB-STO-APART`) son la
   misma imagen durable. El keyset exacto no contiene `abort`, `outcome`
   ni `intent`; sólo `phase` y `recovery_required` pueden cambiar. Un
   campo nuevo o una transición de aborto persistida antes de compartir
   estado físico sería el discriminador, y ese borde queda fuera de este
   ADR: la implementación lo cierra contra un host real.

---

## FICHEROS_TOCADOS

- `adr-s15-m15-storage.md` (este fichero; autoridad sucesora v9; OWNS de M15 concedidos)

Ningún `.py` del write-set M15. Ningún plan, DAG, GATES, inbox ni bundle.
