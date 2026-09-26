# ADR — candidate-live / S-04-TELEMETRY / S-24.final

Estado: **autoridad sucesora v9** (autoridad vigente: `20260901-ledger-v5` / `inbox-20260901-v8` / bundle `v9`; predecesor: bundle `v8`).
Candidate-live sigue **DEFERRED**. Este ADR declara explícitamente que
**NO concede OWNS** (candidate-live sigue DEFERRED, **sin OWNS**). No
enumera paths en el write-set de `20260831-ledger-v3`. No materializa, no
publica, no limpia.

[EXACT] `TEMP/successor-authority-draft.md:62`;
`plans/inbox-20260830/32-fb-20260830-011217-668f.md:6-8,15`;
`plans/inbox-20260830/physical-ownership-addendum-v1.md:26-42`.

---

## 1. Namespace candidate literal y propio

El segmento de namespace es exactamente `dayz-mcp-candidate-live`.
Prohibido cualquier literal que contenga `m24`, `pbo-freshness` o
`schema-calibration`. [DESIGN]

El namespace completo es el join acreditado, en runtime, de:

1. la base absoluta que devuelve `[IO.Path]::GetTempPath()` **en esa
   corrida** (el mismo BCL que M10;
   `plans/inbox-20260830/32-fb-20260830-011217-668f.md:35`);
2. el segmento literal `dayz-mcp-candidate-live`;
3. `<txid>` de 32 hex minúsculas, nuevo, create-only.

No se usa `%TEMP%`, `$WorkDrive`, `T`, ni
`C:\Users\guill\AppData\Local\Temp` como fuente de la base. Recorta sólo
una barra final que no deje `X:\`. Exige `_local_canonical_path`: NFC, sin
NUL, `normpath==value`, absoluta local, no `_DRIVE_REMOTE`, longitud
3..520 (`tools/dayz_mcp/request_path_authority.py:134-155`). Si la API no
devuelve una absoluta acreditable: `INCONCLUSIVE` y no se crean hijos.
Relee la API antes de cada transacción; no persiste la cadena como
autoridad de reapertura. Los `.py` pineados no exponen `GetTempPath`; no
se inventa una firma ctypes. El binding Python de esa API es **[DESIGN],
no acreditado** (M15 ya lo dice: no tiene binding Python acreditado de
`[IO.Path]::GetTempPath()`). `_local_canonical_path` sólo **valida** una
cadena (`tools/dayz_mcp/request_path_authority.py:134-155`); no la produce.
Un mutante que tome `$env:TEMP`, `$env:TMP`, `T` o
`tempfile.gettempdir()` **no es un FAIL ejecutable**: la igualdad con el
BCL de esa corrida no tiene mecanismo pineado. Queda [DESIGN]/INCONCLUSIVE
hasta que una ficha sucesora pinee ctypes o una igualdad medida.

Este namespace es **físicamente distinto de `dayz-mcp-m24`**. Se nombra
`dayz-mcp-m24` para **excluirlo**: candidate-live no reclama temp,
candidate, publish, backup ni journal de M24
(`plans/inbox-20260830/physical-ownership-addendum-v1.md:122-135`).
No lee, escribe, enumera, hashea ni limpia esos artefactos ni los tres
deletreos del prefijo temp `{GetTempPath}\dayz-mcp-m24\<txid>`,
`T\dayz-mcp-m24\<txid>` ni
`%TEMP%\dayz-mcp-m24\<txid>` (los tres nombres en conflicto de M24; ver
tabla de procedencia). Tampoco presta
`dayz-pbo-freshness` ni `dayz-mcp-schema-calibration`
(`plans/inbox-20260830/physical-ownership-addendum-v1.md:62,75,122-135`;
`plans/inbox-20260830/32-fb-20260830-011217-668f.md:15,31,35`).

Cada componente se acredita por handle, identidad y no-reparse, no por
string: `_prefixes` + `_open_directory(..., follow_root_reparse=False)`
(`tools/dayz_mcp/request_path_authority.py:54-68,188-235,241-250,414-460`).
Prohibido `allow_root_junction` en este namespace (esa excepción es de
`SealedPathRoot` de misión/mod sellados). Escape, `.`, `..`, vacío, `:`,
separador inyectado, identidad cambiante o follow de reparse:
`INCONCLUSIVE` antes de AddonBuilder, publish, live o cleanup.

El padre `{TP}\dayz-mcp-candidate-live` puede existir. El hijo `{txid}`
se crea create-only; si ya existe, hay colisión case-fold, o el padre es
reparse: `INCONCLUSIVE` sin mutar
(`plans/inbox-20260830/32-fb-20260830-011217-668f.md:35`;
`plans/inbox-20260830/physical-ownership-addendum-v1.md:125-126`).

---

## 2. Destino de mod candidate propio

[EXACT] El build sancionado del worker apunta AddonBuilder a
`runtime.mods_root\@<mod>\Addons`
(`tools/dayz_mcp/dayz_test_worker.py:554-575`), que es el directorio del
publish exclusivo `Q\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo`
(`plans/inbox-20260830/physical-ownership-addendum-v1.md:128`; mismo
árbol que `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo`,
`plans/inbox-20260830/physical-ownership-addendum-v1.md:9-11`;
`plans/inbox-20260830/00-execution-dag.md:59`). Un scratch
sibling que siga llamando a `payload["build"]` con la policy sellada
escribe el PBO publicado. H11 no acepta `dev_root`, source, ejecutable,
PID, argv ni lease; mods públicos = un segmento relativo
(`product-spec.md:146`).

[DESIGN] Con `TP` = absoluta acreditada de `GetTempPath` de esa corrida y
`txid` de 32 hex. AddonBuilder recibe un **directorio** destino, no un
path `.pbo`: el caller vigente compone `target` como
`...\@<mod>\Addons` (`tools/dayz_mcp/dayz_test_worker.py:554-575`) y la
autoridad 668f crea `candidate` y staging como directorios y pasa
`candidate` como destino (`plans/inbox-20260830/32-fb-20260830-011217-668f.md:35`).
Este ADR no sustituye ese contrato por un nombre de fichero.

| uso | ruta |
|---|---|
| scratch/staging | `{TP}\dayz-mcp-candidate-live\{txid}\staging\` |
| `candidate_dir` (destino AddonBuilder) | `{TP}\dayz-mcp-candidate-live\{txid}\candidate\` |
| PBO esperado dentro de `candidate_dir` | `{TP}\dayz-mcp-candidate-live\{txid}\candidate\DayZ_MCP.pbo` |
| `dest_dir` (NUNCA el publish M24) | `{TP}\dayz-mcp-candidate-live\{txid}\mods\@DayZ_MCP\Addons\` |
| dest PBO (overlay live) | `{TP}\dayz-mcp-candidate-live\{txid}\mods\@DayZ_MCP\Addons\DayZ_MCP.pbo` |
| `mods_root` overlay live | `{TP}\dayz-mcp-candidate-live\{txid}\mods` |
| journal activo | `{TP}\dayz-mcp-candidate-live\{txid}\candidate-live.{txid}.journal.json` |
| journal candidate CAS | `{TP}\dayz-mcp-candidate-live\{txid}\candidate-live.{txid}.journal.candidate.json` |
| journal completed | `{TP}\dayz-mcp-candidate-live\{txid}\candidate-live.{txid}.journal.completed.json` |
| receipt | `{TP}\dayz-mcp-candidate-live\{txid}\candidate-live.{txid}.receipt.json` |
| evidencia E6/build | `{TP}\dayz-mcp-candidate-live\{txid}\candidate-live.{txid}.e6-build.json` |
| setup receipt `recipes[i]`, i∈{0,1,2} | `{TP}\dayz-mcp-candidate-live\{txid}\candidate-live.{txid}.setup.{i}.receipt.json` |
| `entities_query` pre-a `recipes[i]` | `{TP}\dayz-mcp-candidate-live\{txid}\candidate-live.{txid}.query.pre-a.{i}.json` |
| `entities_query` pre-b `recipes[i]` | `{TP}\dayz-mcp-candidate-live\{txid}\candidate-live.{txid}.query.pre-b.{i}.json` |
| `entities_query` post-site `recipes[i]` | `{TP}\dayz-mcp-candidate-live\{txid}\candidate-live.{txid}.query.post-site.{i}.json` |
| `entities_query` post-last `recipes[i]` | `{TP}\dayz-mcp-candidate-live\{txid}\candidate-live.{txid}.query.post-last.{i}.json` |
| `session_status` | `{TP}\dayz-mcp-candidate-live\{txid}\candidate-live.{txid}.session_status.json` |
| backup dest (sólo si dest PBO llegó a existir) | `{TP}\dayz-mcp-candidate-live\{txid}\DayZ_MCP.dest-apart.{txid}.{sha12}.pbo` |

El nombre de fichero `DayZ_MCP.pbo` es el prefijo Enforce; la **ruta** es
distinta y separada de `Q\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo` (y de su
locator `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo`; mismo file-id). AddonBuilder
recibe exclusivamente `candidate_dir` absoluto, contenido léxica y
físicamente en `{txid}`, acreditado como directorio ordinario no-reparse
(`plans/inbox-20260830/32-fb-20260830-011217-668f.md:35`). Nunca un path
`.pbo`, nunca `Addons` publicado. El PBO esperado se reacredita **dentro**
de `candidate_dir` tras el build (basename `DayZ_MCP.pbo`, regular,
`pbo_size>0` del broker). Procedencia source→candidate (E6 /
`plans/inbox-20260830/00-execution-dag.md:241-245`) **antes** de tocar
`dest_dir`. Preimagen inicial del dest PBO: unión cerrada
`{exists:false, sha256:null}` — no se fabrica un PBO vacío y no se hashea
una ausencia.

#### Journal transaccional candidate-live

[DESIGN] create-only **antes** de la primera mutación de dest. Kind
`"dayz-mcp-candidate-live-journal"`, `schema_version=1`. Canonicalización
igual que el receipt (`TEMP/S15-authority-options.md:389-393`). Patrón de
fases del journal de rotación (`TEMP/S15-authority-options.md:504-509`).

Keyset top-level **exacto**: `kind`, `schema_version`, `txid`, `phase`,
`events`, `recovery_required`, `candidate_dir`, `expected_pbo`, `dest_dir`,
`dest_pbo`, `dest_preimage`, `candidate_pbo_sha256`, `dest_apart_name`,
`deployed_sha256`, `e6_receipt_path`, `e6_receipt_sha256`. `phase` ∈
`{prepared, deployed, rolled_back, redeployed, committed}` y **es** el
`phase` del último elemento de `events`. `dest_preimage` = `{exists, sha256}`
con la unión de §5 (`exists=false` ⇒ `sha256=null`). `candidate_pbo_sha256`
y `deployed_sha256` son `null` hasta que el objeto existe; después,
`^[0-9a-f]{64}$`. `dest_apart_name` es `null` hasta el evento `rolled_back`.
`e6_receipt_path` / `e6_receipt_sha256` son `null` hasta acreditar E6;
ambos son obligatorios **antes** del primer write en dest.
Cada CAS que **cambia** `phase` conserva txid y paths, actualiza hashes /
`dest_apart_name` / `recovery_required` **y** añade exactamente un evento.
Un replace en `prepared` que **sólo** rellena `candidate_pbo_sha256` y los
campos E6 (dest sigue ausente, `events` inalterado, `phase` inalterado) no
es cambio de fase y no añade evento. Replace del journal exige
SHA+identidad de la versión previa.

`events` es un array create-append, monotónico, longitud 1..5. Cada
elemento tiene keyset exacto `{seq, phase, prev_sha256, dest_exists,
dest_sha256, dest_apart_exists, dest_apart_sha256, candidate_pbo_sha256}`.
`seq` es integer no-bool, empieza en 0, estrictamente +1. `phase` es el
publicado por ese evento. `prev_sha256` es `null` en `seq=0`; si no,
`^[0-9a-f]{64}$` = SHA-256 del JSON canónico de `events[seq-1]`.
`dest_exists`/`dest_apart_exists` son boolean; `exists=false` ⇒ el sha
homónimo es `null`; `exists=true` ⇒ `^[0-9a-f]{64}$` de bytes reabiertos.
`candidate_pbo_sha256` del evento es el valor vigente al publicar esa
fase; `null` en `seq=0`.
El autómata cerrado es exactamente
`prepared → deployed → rolled_back → redeployed → committed`, sin saltos.
`events` es relato del **mismo** productor: acredita coherencia del
autómata, no una observación independiente del rename
(`TEMP/successor-authority-draft.md:7-9`). Tras `redeployed`,
`dest_exists=false` no es recomputable. Un journal completed sin esa
cadena no puede derivar ni la coherencia de `rollback_rehearsed`. **N5
abierto.**

Orden de mutaciones. Cada create/rename y cada CAS son **fronteras
distintas**:

1. create-only + fsync + reopen + readback del journal `prepared` con
   `events=[{seq:0,phase:"prepared",...}]`,
   `dest_preimage={exists:false,sha256:null}`; dest PBO ausente;
   `e6_receipt_sha256=null`.
2. AddonBuilder escribe **en** `candidate_dir`. Reacreditar el PBO
   esperado. Escribir create-only la evidencia E6 de §5 (locator de la
   tabla). Reabrirla, hashearla, registrar `candidate_pbo_sha256`,
   `e6_receipt_path` y `e6_receipt_sha256` por replace del journal **sin
   cambiar** `phase` ni `events` (sigue `prepared`; no es un CAS de fase).
   Dest aún ausente. Prohibido el primer write en dest si E6 no reabre,
   si `source.sha256` no casa al reopen, o si el PBO esperado no casa
   `candidate_pbo_sha256`. E6 **no** compara entradas source↔staging
   (N7 abierto; schema S-10 no pineado;
   `plans/inbox-20260830/00-execution-dag.md:241-245`).
3. create-only del dest PBO desde bytes del PBO esperado (dest era
   ausencia: no `ReplaceFileW` sobre un dest que este txid no creó).
   **Después**, CAS → `deployed` (append evento; `deployed_sha256` = SHA
   reabierto del dest PBO).
4. rename no-replace dest PBO → dest-apart. Dest léxico ausente.
   **Después**, CAS → `rolled_back`.
5. create-only dest PBO otra vez desde el PBO esperado (o desde dest-apart
   rehasheado, mismo SHA). **Después**, CAS → `redeployed`. Esto satisface
   `candidate_remains_deployed` con dest presente.
6. Reabrir dest, dest-apart, PBO esperado, E6 y journal activo. CAS →
   `committed` (append evento). Rename no-replace del journal activo al
   completed. Reabrir el completed y hashearlo. **Aún no hay receipt.**
7. create-only del receipt **sólo** ahora, con `journal.path` = journal
   completed, `journal.phase="committed"` y `journal.sha256` = SHA de esos
   bytes reabiertos.

Recovery **antes** de reutilizar el `{txid}` o de emitir receipt. Launch
live / S-04 no empiezan con journal activo no-committed. La matriz se
indexa por `(fase declarada × estado físico × candidate CAS)` —
`candidate-live.{txid}.journal.candidate.json` ∈ `{ausente, exacto,
discrepante}`. Un I/O por delante es legal: identidad+digest, publicar el
CAS omitido, continuar sin repetir la mutación. En cada replace/CAS:
candidate **exacto** ⇒ una sola acción idempotente (reabrir, acreditar el
transform esperado, publicar); **ausente** ⇒ regenerar ese transform
create-only + fsync + readback y publicar; **discrepante** ⇒ fila
residual. Candidate presente fuera de frontera CAS ⇒ residual.

| fase | estado físico | candidate CAS | acción al reentrar |
|---|---|---|---|
| `prepared` | dest ausente; dest-apart ausente; PBO esperado ausente; E6 ausente; `candidate_pbo_sha256`/`e6_receipt_path`/`e6_receipt_sha256` nulos | ausente | reanudar desde el paso 2; no inventar dest; no receipt; no adoptar artefacto create-only preexistente |
| `prepared` | dest ausente; dest-apart ausente; PBO esperado presente y hashea `candidate_pbo_sha256`; E6 reacreditada; `e6_receipt_path`/`e6_receipt_sha256` publicados | ausente | reanudar desde el paso 3 (primer write dest); no repetir E6 ni el replace; no inventar dest; no receipt |
| `prepared` | dest PBO presente y hashea el PBO esperado; dest-apart ausente; E6 reacreditada | ausente: regenerar transform `deployed`; exacto: reabrir y acreditarlo | create-hecho/CAS-no-publicado del paso 3: publicar CAS `deployed`; no recrear dest; no receipt |
| `deployed` | dest PBO existe y hashea `deployed_sha256`; dest-apart ausente | ausente | reanudar ensayo de rollback (paso 4); no receipt |
| `deployed` | dest léxico ausente; dest-apart hashea `deployed_sha256` | ausente: regenerar transform `rolled_back`; exacto: reabrir y acreditarlo | rename-hecho/CAS-no-publicado del paso 4: publicar CAS `rolled_back`; no repetir el rename; no receipt |
| `rolled_back` | dest ausente; dest-apart hashea `deployed_sha256` | ausente | reanudar redeploy (paso 5); no tratar ausencia como PASS ni como preimagen feliz sin journal |
| `rolled_back` | dest PBO presente y hashea `deployed_sha256`; dest-apart intacto | ausente: regenerar transform `redeployed`; exacto: reabrir y acreditarlo | create-hecho/CAS-no-publicado del paso 5: publicar CAS `redeployed`; no recrear dest; no receipt |
| `redeployed` | dest PBO hashea `deployed_sha256`; dest-apart intacto; journal aún activo | ausente: regenerar transform `committed`; exacto: reabrir y acreditarlo | CAS `committed` y rename a completed **antes** de crear receipt; no redeploy extra |
| `committed` | journal aún en el path activo; completed ausente; receipt ausente | ausente | rename-hecho pendiente: rename a completed, reabrir, crear receipt; no redeploy |
| `committed` | journal completed presente; receipt ausente | ausente | **no** reconstruir un receipt de campaña live: este journal no registra spawn/get-in/query/delete/teleport/release (P3 abierto). Si esas mutaciones no llegaron a ejecutarse, el receipt de dest+apart+E6+journal **no** es PASS live. Fixture/lease/jugador desplazado → `recovery_required`/`INCONCLUSIVE`; no duplicar fixtures ni declarar cleanup |
| `committed` | journal completed; receipt presente y rehashea | ausente | no reutilizar; S-04 consume el receipt |
| cualquiera | contradicción, segundo journal, dest que no casa, dest-apart ausente en `rolled_back`/`redeployed`, E6 ausente al escribir dest, dos I/O por delante, candidate **discrepante**, candidate presente fuera de frontera CAS, **o `prepared` parcial** (PBO presente/E6 ausente; E6 presente/journal nulo; journal relleno/E6 ausente o SHA distinto; colisión create-only) | discrepante u otro no cubierto | `recovery_required=true`; `INCONCLUSIVE`; sin receipt PASS |

Inyección de fallo **después de cada create/rename, después de cada CAS,
después del replace que rellena E6, y antes/después del rename a
completed** (no «tras cada uno de los seis pasos» agrupados). Incluye:
tras build del PBO esperado y antes de create E6; tras create E6 y antes
del fsync/readback; tras readback y antes del replace del journal; tras paso 2
completo (E6 en journal, dest ausente) y antes del primer write dest; tras
create/readback de `journal.candidate.json` y antes de cada replace CAS de
fase; tras create del dest y antes del CAS
`deployed`; tras rename a dest-apart y antes del CAS `rolled_back`; tras
recreate del dest y antes del CAS `redeployed`; tras CAS `committed` y
antes del rename a completed; tras completed y antes del receipt. Mutante
que emite receipt con journal no-completed, que inventa `journal.sha256`
de un artefacto futuro, o que deja dest ausente tras el ensayo sin
redeploy, = FAIL del gate candidate-live (sigue DEFERRED: no se ejecuta
aquí).

No se usa `dayz_test_run(build=true)` con la policy sellada: su `target`
es el directorio del publish M24. No se inyecta el dest como `extra_mods`:
el worker **sigue** cargando `"@" + runtime.mod` desde `mods_root` sellado
(`tools/dayz_mcp/dayz_test_worker.py:205-211`;
`tools/dayz_mcp/dayz_test_request.py:153-170`;
`tools/dayz_mcp/request_path_authority.py:575-580`) — doble carga. Overlay
de sesión, privado, no público MCP: `WorkerRuntimePolicy` con `mods_root`
= dest `mods\` acreditado, `payload["build"]=false` en el launch live,
`no_base_mods=true` (`tools/dayz_mcp/dayz_test_request.py:365`). El overlay
lo construye un módulo **nuevo** del sucesor (cero OWNS hoy); no edita
`dayz_test_worker.py`. Mutante: argv `-mod=` o build `target` cuyo locator resuelto sea el
publish M24 o su directorio `Addons` (mismo árbol `Q\` / `P:\`, no el
deletreo). El unit gate lo pone rojo por intento registrado en el espía
(§4), no por `PathIdentity` de un objeto existente. No se añade un
cuarto predicado.

La corrida live exige `no_file_patching=true`. Default
`no_file_patching=false` añade `-filePatching`
(`tools/dayz_mcp/dayz_test_request.py:292`;
`tools/dayz_mcp/dayz_test_worker.py:237-238`). Un PASS con filePatching no
acredita el dest.

El journal M24 `m24-publish.{txid}.journal.json` y el backup
`DayZ_MCP.backup.{txid}.{sha12}.pbo` cuya identidad sea la del árbol
publicado M24 (Addons / reports M24) quedan **fuera de alcance**. S-24.final no adopta el PBO candidate como input
(`TEMP/successor-authority-draft.md:66`).

---

## 3. Rollback propio

[DESIGN] patrón M10 (`plans/inbox-20260830/32-fb-20260830-011217-668f.md:44`)
y restore de registro (`tools/dayz_mcp/launcher_registry_update.py:457-521`).
M24 fallo previo deja publish byte-idéntico
(`plans/inbox-20260830/physical-ownership-addendum-v1.md:133-134`).

Rollback candidate-live (el journal de §2 ya existe en `prepared` o
posterior; **no** se inicia rollback de ensayo sin journal):

1. reacredita journal+binding+txid; recovery de §2 manda (incluida la
   fila rename-hecho/CAS-no-publicado); el ensayo **sólo** arranca si la
   fase es `deployed`. Si la fase es `redeployed`, **no** se reensaya:
   se reacreditan dest y dest-apart y se continúa a `committed` (el
   autómata no tiene arista `redeployed→rolled_back`; dest-apart ya está
   ocupado). Cualquier otra fase: no se ensaya;
2. si y sólo si la fase es `deployed` y dest PBO existe, rename
   no-overwrite a `DayZ_MCP.dest-apart.{txid}.{sha12}.pbo`; **después**
   CAS journal → `rolled_back` (no agrupar rename y CAS);
3. dest léxico queda ausente (preimagen de **esta** transacción sigue
   siendo `{exists:false, sha256:null}`; el dest-apart es postimagen del
   deploy, no la preimagen inicial);
4. candidate_dir / PBO esperado / staging / journal permanecen recuperables;
5. no unlink del dest sin rename previo;
6. no `os.replace` sobre un dest que no fue creado por este txid;
7. tras `ReplaceFileW` no se exige igualdad de `file_id` con el backup
   (`tools/dayz_mcp/launcher_registry_update.py:347-351`) — compara SHA +
   tipo;
8. el ensayo **no** termina en ausencia: el paso 5 de §2 redeploya y el
   receipt (pasos 6–7 de §2) exige dest presente con el mismo SHA, journal
   completed reabierto y cadena `events` válida. Abortar la transacción
   (no el ensayo) no añade fase ni completed compensado: el autómata de §2
   es exactamente `prepared → deployed → rolled_back → redeployed →
   committed`, sin saltos, y la matriz de reentrada reanuda `deployed` y
   `rolled_back`. Sin arista de aborto, una interrupción deliberada se
   reanuda o cae en `recovery_required`/`INCONCLUSIVE`; no produce receipt
   PASS.

Nunca abre, enumera, hashea, limpia, adopta ni reutiliza
namespace/candidate/backup/journal/publish de `dayz-mcp-m24`. Rollback de
código S-04 no forma parte de este contrato
(`TEMP/successor-authority-draft.md:64`). Cleanup reacredita path+identidad
inmediatamente antes de unlink/rmdir. Prohibido: globs; borrar el padre
`dayz-mcp-candidate-live`; borrar otro `txid`; tocar `GetTempPath`;
enumerar o hashear `dayz-mcp-m24`. El árbol `{txid}` **no** se limpia al
PASS live: el receipt debe poder rehashearse en S-24.final
(`TEMP/successor-authority-draft.md:66-68`). Ese árbol —receipt, journal
completed, E6, receipts de setup, las doce consultas, `session_status`—
vive bajo la base de `[IO.Path]::GetTempPath()` de esa corrida, que es
el temp de usuario
(`plans/inbox-20260830/physical-ownership-addendum-v1.md:24`), no un
árbol del repo. El SO y las herramientas de limpieza de disco pueden
reclamarlo entre candidate-live y S-24.final. Este ADR sólo contempla el
borrado voluntario: cleanup anticipado = `INCONCLUSIVE`, no PASS.
Reclamación del SO = receipt ausente → S-24.final `INCONCLUSIVE` antes
de construir o publicar (`TEMP/successor-authority-draft.md:66`). Si la
evidencia del receipt vive en temp o en un árbol durable lo fija la
ficha sucesora que conceda OWNS
(`TEMP/successor-authority-draft.md:62`); hoy cero OWNS.

El dest previo acreditado de esta transacción es **ausencia**
(`exists=false`, `sha256=null`), no un segundo PBO con la identidad del
publish M24. Restaurar
el publish M24 no es rollback de candidate-live. Rollback contra ausencia
deja dest PBO ausente y dest-apart (si llegó a crearse) recuperable; no se
restaura un SHA inventado. Ese resultado físico no es un estado extra del
autómata: el ensayo redeploya; abortar no tiene arista propia.

---

## 4. Discriminador: M24 queda byte-idéntico y sin accesos

[EXACT] patrón espía M10
(`plans/inbox-20260830/32-fb-20260830-011217-668f.md:37,48`); A=B de
inventario (`plans/inbox-20260830/physical-ownership-addendum-v1.md:9-11,21-24,28,32-37,41`).

«M10 no crea, abre, enumera ni hashea ningún sentinel/byte real de M24:
su unit gate usa un doble/espía de filesystem externo al writer que
registra y rechaza cualquier intento de acceso al prefijo M24 sin
abrirlo; el gate de integración posterior lo cierra M24/M25, que crea y
hashea un sentinel M24-owned antes y después de ejecutar M10 sin revelar
ese path al wrapper»
(`plans/inbox-20260830/32-fb-20260830-011217-668f.md:37`).
«el espía M10 registra cero intentos de lectura/escritura/enumeración/limpieza sobre `<GetTempPath>/dayz-mcp-m24`»;
«Separadamente, M24/M25 acredita byte-identidad de su sentinel M24-owned before/after sin que M10 lo abra o conozca»
(`plans/inbox-20260830/32-fb-20260830-011217-668f.md:48`).
Candidate-live hereda ese reparto. No se añade un cuarto predicado.
El conjunto de prefijos de M10 **no** sirve solo: un espía que sólo vigila
`dayz-mcp-m24` deja pasar el write al PBO publicado. El mutante «espía
igual que M10» queda rojo. El conjunto vigilado de candidate-live es el
de la transacción M24
(`plans/inbox-20260830/physical-ownership-addendum-v1.md:122-135`):
temp, candidate, publish, backup y journal.

Son dos gates distintos con dos dueños distintos [DESIGN]:

1. **El espía (unit gate, dueño: candidate-live).** Un doble/espía de
   filesystem **externo al writer** intercepta y **rechaza** todo intento
   de abrir, leer, enumerar, hashear o limpiar cualquiera de los
   artefactos M24 temp, candidate, publish, backup y journal, y
   **registra el intento sin ejecutarlo**. Criterio: **cero intentos
   registrados** sobre ese conjunto. Operaciones espía ∈
   `{create,open,stat,lstat,enumerate,hash,unlink,rename,replace,rmdir}`.
   El writer **no abre M24**. El espía, externo, resuelve cada locator
   **en runtime por identidad, no por cadena**, y **no entrega** esos
   locators al writer. Para un objeto existente abre y fija
   `PathIdentity` (`volume_serial_number`+`file_id`;
   `tools/dayz_mcp/request_path_authority.py:54-57,173-185`;
   `:173-185,188-203`). Para un locator ausente o futuro, el predicado
   es el nombre o prefijo canónico (sin que el writer abra el objeto
   M24): «Para targets nuevos se acredita el padre por handle y se reserva nombre completo»
   (`plans/inbox-20260830/physical-ownership-addendum-v1.md:28-34`).
   Dos hijos ausentes bajo el mismo padre no se confunden: el nombre o
   prefijo del locator decide. Eso no totaliza la pertenencia de un
   miembro **ya existente** de un árbol o prefijo M24: `PathIdentity` no
   contiene padre, nombre relativo ni pertenencia a árbol.
   **Y1/X5 abierto.** No se añade un sexto discriminador. Los deletreos conocidos alimentan esa
   resolución y no se comparan como cadenas: los tres del prefijo temp
   (`{GetTempPath}\dayz-mcp-m24`, `T\dayz-mcp-m24`,
   `%TEMP%\dayz-mcp-m24`); `Q\` y `P:\` del mismo árbol para publish y
   backup (`plans/inbox-20260830/physical-ownership-addendum-v1.md:9-11,21-24,41,128-129`);
   `R\` y `P:\DayZ_MCP_dev\` para journal (`:21,130`).
   `PathIdentity` usada **desde el writer** reabriría la paradoja; no es
   el predicado del unit gate. Un intento = FAIL (no INCONCLUSIVE).
2. **El sentinel (gate de integración, dueño: M24/M25).** M24/M25 crea
   un fichero sentinel **bajo su propio namespace**, lo hashea, ejecuta
   candidate-live, y lo vuelve a hashear. Criterio: **byte-identidad
   before/after**. Si el objeto existía antes, se exige también que
   **no haya sido reemplazado**: misma `PathIdentity`, no sólo mismos
   bytes (`tools/dayz_mcp/launcher_registry_update.py:347-351`). Un
   write-y-restaura conserva el hash y no basta. El path del sentinel
   **no se revela a candidate-live** — ni por argumento, ni por
   configuración, ni por variable de entorno. Si el publish aún no
   existe, A=B es `exists=false` en ambas; fabricar el PBO durante
   candidate-live es FAIL; crearlo y retirarlo para conservar
   `exists=false` también es intento registrado. A!=B (bytes o
   identidad) = FAIL de candidate-live (toque de M24), **no** el
   `cas_token=None` de S15. Setup que no puede hashear el
   publish/sentinel sin dárselo al writer = INCONCLUSIVE, no PASS.
3. **Reparto de la carga de la prueba.** Candidate-live acredita *no
   haber intentado*; M24/M25 acredita *no haber cambiado*. Ninguno de
   los dos acredita lo del otro. La conjunción **no** sostiene «sin
   accesos» para un miembro preexistente de un árbol o prefijo M24
   (**Y1/X5 abierto**; ver `LO QUE NO PUDE VERIFICAR`). Candidate-live
   **no puede acreditar «M24 sin accesos» por sí solo**; eso refuerza
   que siga `DEFERRED`. No se añade un sexto discriminador.
4. **Mutantes que deben ponerse rojos** (por analogía con
   `plans/inbox-20260830/32-fb-20260830-011217-668f.md:37`): uno que
   acceda a temp, candidate, publish, backup o journal de M24; uno que
   lea o hashee el publish
   (`Q\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo`); uno que escriba y restaure
   el publish; uno que cree y retire un publish inicialmente ausente;
   uno que acceda a backup o journal dejando el sentinel intacto; uno
   que intente borrar el sentinel; y uno que revele el path del
   sentinel a candidate-live. El mutante que derive la base desde
   `%TEMP%` o `$WorkDrive` en vez de `GetTempPath` **no es un FAIL
   ejecutable** de este gate: queda [DESIGN]/INCONCLUSIVE (M2).
5. **Qué queda fuera y por qué.** El gate de integración **no es de
   candidate-live**: candidate-live no puede ejecutarlo ni declararlo
   PASS por su cuenta. Eso es correcto: es una dependencia de gate hacia
   M24/M25, no un hueco. El journal candidate-live no contiene
   `PathIdentity` de un objeto M24: esa comparación no se ejecuta aquí.
   Case-fold de cadena contra `Q\`/`R\`/`P:\` no es el predicado.
   **Abierto por nombre:** source `R\addon`
   (`plans/inbox-20260830/physical-ownership-addendum-v1.md:124`) no
   está en el conjunto vigilado; no se afirma «sin accesos» sobre ese
   árbol. **Y1/X5 abierto:** pertenencia de un miembro preexistente de
   un árbol o prefijo M24; lo cierra el gate de integración M24/M25
   sobre un árbol real, no este documento. No se añade un sexto
   discriminador.

| Locator (unit: identidad en runtime sin entregar al writer; integración: sentinel M24-owned) | ¿Lo cubre el espía candidate-live? | ¿Quién lo acredita? |
|---|---|---|
| `{GetTempPath}\dayz-mcp-m24` (temp M24) | Sí | Unit gate (espía) |
| `T\dayz-mcp-m24` (temp M24) | Sí | Unit gate (espía; identidad, no cadena) |
| `%TEMP%\dayz-mcp-m24` (temp M24) | Sí | Unit gate (espía; identidad, no cadena) |
| candidate M24 (`T\dayz-mcp-m24\<txid>\DayZ_MCP.candidate.<txid>.pbo` y equivalentes bajo los otros dos prefijos temp) | Sí | Unit gate (espía; contenido en el prefijo temp) |
| `{GetTempPath}\dayz-pbo-freshness` | No (M10 es el writer allí) | Sí como negativo: candidate no es M10 |
| publish M24 (`Q\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo` y `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo`; mismo file-id) | Sí | Unit gate (espía) ∧ integración (sentinel; no-reemplazo si existía) |
| backup sibling M24 (`Q\Mods\@DayZ_MCP\Addons\DayZ_MCP.backup.` y `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.backup.`) | Sí | Unit gate (espía) ∧ integración |
| journal M24 (`R\reports\2026-08-30-inbox-implementation\M24\m24-publish.<txid>.journal.json`; prefijo padre `R\reports\2026-08-30-inbox-implementation\M24\` y `P:\DayZ_MCP_dev\reports\2026-08-30-inbox-implementation\M24\`) | Sí | Unit gate (espía) ∧ integración |
| source M24 (`R\addon`) | **No** | **Abierto**: el espía no vigila el source read-only |
| `{GetTempPath}\dayz-mcp-candidate-live` | No | No se espía a sí mismo |
| `runtime.build_temp_root` | No | Sí si el path se resuelve; uso sin acreditar disjunto = FAIL |

Este A=B de inventario **no** reutiliza el arbitraje A≠B de `storage_1`
(`TEMP/S15-authority-options.md:639-644`).

---

## 5. Receipt candidate-live: keyset y versión

[DESIGN] schema v1. [EXACT] S-24.final enlaza como preimagen el SHA-256
exacto del receipt PASS y no adopta ningún artefacto de candidate-live
(`TEMP/successor-authority-draft.md:66-68`). Canonicalización: JSON UTF-8
estricto, claves ordenadas, `allow_nan=False`, separadores compactos, sin
BOM ni newline (`TEMP/S15-authority-options.md:389-393`; convención de
formato en `tools/dayz_mcp/dayz_test_worker.py:171-178`, no importada por
el oráculo).

`receipt_sha256 = SHA-256(raw_receipt_bytes)` se calcula **fuera** del
JSON. No es una clave. Es la única preimagen que S-24.final importa.

Top-level exacto, versión `schema_version=1`, kind
`"dayz-mcp-candidate-live-receipt"`:

| clave | tipo exacto | restricción |
|---|---|---|
| `kind` | string | literal `"dayz-mcp-candidate-live-receipt"` |
| `schema_version` | integer no-bool | literal `1` |
| `verdict` | string | literal `"PASS"`; FAIL/INCONCLUSIVE no producen receipt admisible |
| `txid` | string | regex `^[0-9a-f]{32}$` |
| `source` | object | keyset exacto `{digest_kind,path,sha256}` |
| `e6_build` | object | keyset exacto `{path,sha256}` — evidencia E6 de la tabla de rutas |
| `deployed_candidate` | object | keyset exacto `{path,sha256,readback,rollback_rehearsal,readback_verified,rollback_rehearsed}` |
| `preimage` | object | keyset exacto `{path,exists,sha256}` — unión cerrada, ver abajo |
| `recipes` | array | longitud 3, orden literal de §7; **cada** elemento es objeto con el keyset cerrado de receta |
| `cleanup` | object | keyset exacto `{all_recipes_cleaned,candidate_remains_deployed,cleaned_recipes,lease_released,origin_restored,session_observed}` |
| `journal` | object | keyset exacto `{path,phase,sha256}` con `phase` literal `"committed"` |

Unión de `preimage` (mismo cierre que
`TEMP/S15-authority-options.md:500-504`): `exists=false` ⇒ `sha256=null`;
`exists=true` ⇒ `sha256` casa `^[0-9a-f]{64}$` y procede de bytes
reabiertos en `path` **antes** del primer create/replace de este txid.
En **esta** transacción la preimagen es obligatoriamente
`exists=false`, `sha256=null`, `path` byte-idéntico a
`deployed_candidate.path` (el dest PBO de §2). No hay «exactamente tres
SHA»: `source.sha256` y `deployed_candidate.sha256` son los dos SHA de
contenido obligatorios; `preimage.sha256` es nulo; `journal.sha256` y los
SHA de receta/readback son hashes de evidencia, no un tercer SHA de
preimagen.

Objetos de binding:

| objeto.clave | tipo exacto | restricción |
|---|---|---|
| `source.digest_kind` | string | literal `"s10-source-manifest-v1"` |
| `source.path` | string | path source literal fijado por la autoridad sucesora, no vacío |
| `source.sha256` | string | regex `^[0-9a-f]{64}$`; digest del manifest source PASS de S-10 |
| `e6_build.path` | string | path de evidencia E6 de la tabla de rutas; igual a `e6_receipt_path` del journal completed |
| `e6_build.sha256` | string | regex `^[0-9a-f]{64}$`; SHA de esos bytes reabiertos |
| `deployed_candidate.path` | string | dest PBO de §2, no `candidate_dir` |
| `deployed_candidate.sha256` | string | regex `^[0-9a-f]{64}$`; SHA del dest PBO reabierto en `redeployed` |
| `deployed_candidate.readback` | object | keyset exacto `{path,sha256}` |
| `deployed_candidate.readback.path` | string | byte-idéntico a `deployed_candidate.path` |
| `deployed_candidate.readback.sha256` | string | regex `^[0-9a-f]{64}$`; el **consumidor** lo recomputa reabriendo ese path |
| `deployed_candidate.rollback_rehearsal` | object | keyset exacto `{apart_path,apart_sha256,dest_after_rehearsal_exists,dest_after_redeploy_sha256,journal_phase_max}` |
| `deployed_candidate.rollback_rehearsal.apart_path` | string | dest-apart de §2, no vacío |
| `deployed_candidate.rollback_rehearsal.apart_sha256` | string | regex `^[0-9a-f]{64}$`; el consumidor lo recomputa reabriendo dest-apart |
| `deployed_candidate.rollback_rehearsal.dest_after_rehearsal_exists` | boolean | literal `false` (dest ausente en fase `rolled_back`) |
| `deployed_candidate.rollback_rehearsal.dest_after_redeploy_sha256` | string | igual a `deployed_candidate.sha256` |
| `deployed_candidate.rollback_rehearsal.journal_phase_max` | string | último `phase` de `events` del journal completed reabierto (`"committed"`); el consumidor verifica la cadena `prev_sha256` y el autómata `prepared→deployed→rolled_back→redeployed→committed`. Un único campo `phase` mutable **no** atestigua el camino |
| `deployed_candidate.readback_verified` | boolean | **derivado**, no oráculo: `true` iff SHA(reopen(path))==`deployed_candidate.sha256`==`readback.sha256`. Un `true` del productor sin esa igualdad invalida el documento |
| `deployed_candidate.rollback_rehearsed` | boolean | **derivado de coherencia del journal, no de observación independiente**: `true` iff el consumidor reabre `events` y valida cadena+autómata. **No** acredita que el rename físico ocurriera: tras `redeployed` dest vuelve a existir y `dest_exists=false` no es recomputable (`TEMP/successor-authority-draft.md:7-9`). **N5 abierto.** Un `true` del productor sin esa cadena invalida el documento |
| `preimage.path` | string | byte-idéntico a `deployed_candidate.path` |
| `preimage.exists` | boolean | literal `false` en esta transacción |
| `preimage.sha256` | null | literal `null` cuando `exists=false`; **prohibido** un SHA de ausencia |
| `journal.path` | string | journal completed de §2; **no** el path activo |
| `journal.phase` | string | literal `"committed"` |
| `journal.sha256` | string | regex `^[0-9a-f]{64}$`; SHA de los bytes completed **reabiertos** tras el rename; prohibido hashear el journal activo `redeployed` o un path que aún no existe |

Procedencia source→candidate. El objeto E6, kind
`"dayz-mcp-candidate-live-e6"`, `schema_version=1`, keyset exacto
`{kind,schema_version,txid,source,staging_dir,staging_inventory_sha256,expected_pbo,candidate_dir}`
con `source` el mismo keyset `{digest_kind,path,sha256}` del receipt y
`expected_pbo={path,sha256}` el PBO esperado **dentro** de `candidate_dir`.
`staging_inventory_sha256` es SHA-256 del inventario canónico de
`staging\` (rutas relativas POSIX ordenadas + SHA-256 de ficheros;
directorio vacío como entrada). El validador **reabre y recomputa**
source, inventario staging y PBO **por separado**: `source.sha256` ==
reopen(`source.path`); inventario de `staging_dir` ==
`staging_inventory_sha256`; reopen(`expected_pbo.path`) ==
`expected_pbo.sha256` == `candidate_pbo_sha256` ==
`deployed_candidate.sha256`. Ninguna igualdad compara el contenido
enumerado por source con las entradas de staging. **N7 permanece
abierto** hasta schema S-10 pineado. Tokens o prefijos no acreditan
(`plans/inbox-20260830/00-execution-dag.md:241-245`). `source` y
`deployed_candidate` adyacentes **sin** E6 reabierto invalidan el
documento; E6 reabierto **tampoco** cierra N7.

#### Recetas en el receipt (tres objetos, ninguna más)

Cada `recipes[i]` keyset exacto
`{recipe,seat_index,expected_type,setup_seat,setup_type,setup_classname,seat_oracle_vehicle_seat,setup_receipt_path,setup_receipt_sha256,setup_object_id}`.
`recipe`, `seat_index` y `expected_type` son **exactamente** las tres
filas de §7, en ese orden (integer no-bool el asiento; `expected_type`
con las mismas comillas). `setup_seat` y `seat_oracle_vehicle_seat`
son los de la tabla de oráculos de §7. `setup_type` y `setup_classname`
son strings iguales a `expected_type`. `setup_object_id` es integer
no-bool `>0`. `setup_receipt_path` es el locator de la tabla de rutas
para ese `i`. `setup_receipt_sha256` es `^[0-9a-f]{64}$` del JSON
canónico del receipt de setup (seated/seat/type/classname de
`plans/inbox-20260830/30-fb-20260830-002237-0de3.md:42-44`), cuyos bytes
viven en ese path y el consumidor reabre y rehashea. Path ausente, SHA
sin locator o bytes que no casan = documento inválido. Orden, valor o
longitud distintos = documento inválido. No hay cuarto elemento.

#### Cleanup: tipo e invariante por campo

| campo | tipo exacto | invariante / evidencia independiente |
|---|---|---|
| `cleaned_recipes` | array longitud 3 | cada elemento keyset exacto `{recipe,object_id,delete_ok,deleted,pre_query_a,pre_query_b,post_site_query,post_last_query,site_pos,last_pos_real,radius_site,radius_last}`; `recipe` en el mismo orden que `recipes`; `object_id` igual a `setup_object_id`; `delete_ok` boolean `true`; `deleted` integer `1` **no** acredita ausencia física; cada `*_query` es `{path,sha256}` con locator de la tabla de rutas; `site_pos` y `last_pos_real` arrays de exactamente 3 números JSON; `radius_site` integer `20`; `radius_last` integer `2` |
| `all_recipes_cleaned` | boolean | **autoafirmado**, no observación independiente del mundo: `true` iff para cada i el consumidor reabre las cuatro queries persistidas y comprueba (1) ambas pre-queries tienen `reliability="player_in_bubble"`, `count_total<=128`, exactamente una coincidencia type/class y deriva estable ≤0.05 m; (2) ambas post-queries tienen `reliability="player_in_bubble"`, `count_total<=128` y **cero** coincidencias type/class; (3) `count_total>128` en cualquiera impide `true`. Esas cuatro consultas las escribe el productor (`kind="dayz-mcp-candidate-live-entities-query"`); el consumidor verifica que existan y rehasheen, no que el mundo estuviera limpio (`TEMP/successor-authority-draft.md:8`). Misma estructura que N5/N6. **X4 abierto.** `deleted=1` con entidad aún visible, o lista cortada tratada como ausencia, invalida el documento (`plans/inbox-20260830/30-fb-20260830-002237-0de3.md:56-57,62-64`) |
| `candidate_remains_deployed` | boolean | **derivado**: `true` iff reopen(dest PBO) existe y SHA==`deployed_candidate.sha256`. El consumidor reabre el dest; el literal no es oráculo |
| `lease_released` | object | keyset exacto `{own_lease,status_path,status_sha256}`; `own_lease` literal `"none"`; `status_path` = locator `session_status` de la tabla de rutas; `status_sha256` `^[0-9a-f]{64}$` de esos bytes reabiertos |
| `origin_restored` | object | keyset exacto `{uid,origin,post}`; `uid` string no vacío; `origin` y `post` arrays de exactamente 3 números JSON. La igualdad `origin==post` **no** acredita el teleport: ambos arrays los escribe el mismo productor, y ni `entities_query` ni `session_status` localizan UID+posición post-restore. Cleanup **no** es PASS por este campo; restauración no validable = INCONCLUSIVE (`plans/inbox-20260830/30-fb-20260830-002237-0de3.md:64`). **N6 abierto.** |
| `session_observed` | object | keyset exacto `{status_path,status_sha256}`; path y SHA deben igualar `lease_released.status_path` / `status_sha256` (misma observación reabierta) |

Cada snapshot `entities_query` persistido tiene kind
`"dayz-mcp-candidate-live-entities-query"`, `schema_version=1`, keyset
exacto `{kind,schema_version,txid,recipe,role,pos,radius,limit,reliability,count_total,matches}`
con `role` ∈ `{pre-a,pre-b,post-site,post-last}`, `limit` integer `128`,
`reliability` literal `"player_in_bubble"`, `pos` array de 3 números,
`matches` array de `{type,classname,pos}`. `pre-a` y `pre-b` son las dos
preimágenes separadas 250 ms del plan. `session_status` persistido tiene
kind `"dayz-mcp-candidate-live-session-status"`, `schema_version=1`,
keyset exacto `{kind,schema_version,txid,uid,own_lease}` con
`own_lease` literal `"none"` tras `session_release`. El consumidor deriva
booleanos **sólo** tras reabrir esos artefactos.

`cleanup.candidate_remains_deployed` derivado `true` y el readback
recomputado igual a `deployed_candidate.sha256`. Toda desviación de
forma, binding físico, receta, asiento, expected, cleanup o readback
recomputado invalida el documento completo: no hay receipt PASS parcial
(`TEMP/successor-authority-draft.md:62`;
`plans/inbox-20260830/30-fb-20260830-002237-0de3.md:42-44`).

Paths de `source.path` / `deployed_candidate.path` quedan pinneados por la
ficha sucesora que **sí** conceda OWNS; hoy no existen literales aprobados
(`TEMP/successor-authority-draft.md:62`). Esa misma ficha fija si el árbol
de evidencia del receipt (hoy bajo `GetTempPath()`) vive en temp o en un
árbol durable.

---

## 6. Arista exacta (sucesora, no vigente)

Arista congelada **sólo** en la autoridad sucesora:

`(M05.full + S-10 + M22) → candidate-live[DEFERRED, sin OWNS] → S-04-TELEMETRY → S-24.final`

[EXACT] `TEMP/successor-authority-draft.md:62-70,100-108`.

El DAG vigente **no** contiene candidate-live, `M05.full` ni la división
S-04-WIRE / S-04-TELEMETRY. Da a M22 dependencia de M05 y a M24
dependencia de M05/M10/M22 (`plans/inbox-20260830/00-execution-dag.md:55-60`),
coloca `(M02+M04)→M05` antes de M22/M24
(`plans/inbox-20260830/00-execution-dag.md:64-75`) y declara que telemetría
termina antes de UI (`plans/inbox-20260830/00-execution-dag.md:77-78`).
Hasta aprobación del sucesor, esta arista es diseño, no autoridad
ejecutable. El nuevo DAG debe **sustituir** explícitamente esas líneas; no
basta complementarlas.

| eslabón | soporte sucesor | soporte DAG vigente | veredicto |
|---|---|---|---|
| `(M05.full + S-10 + M22) → candidate-live` | `TEMP/successor-authority-draft.md:62,108` | Sólo agregado `(M05+M10+M22)→M24` en `plans/inbox-20260830/00-execution-dag.md:71-75` | Refinamiento sucesor; no vigente |
| `candidate-live → S-04-TELEMETRY` | `TEMP/successor-authority-draft.md:62-64,70` | M04 antes de M05; telemetría termina antes de UI (`plans/inbox-20260830/00-execution-dag.md:67-78`) | Requiere reemplazo explícito |
| `S-04-TELEMETRY → S-24.final` | `TEMP/successor-authority-draft.md:64-70` | M24 depende de M05/M10/M22, sin gate intermedio (`plans/inbox-20260830/00-execution-dag.md:57-60`) | Dependencia nueva |
| Separación física | Candidate-live no toma OWNS M24 (`TEMP/successor-authority-draft.md:62`) | M24 reserva temp, candidate, publish, backup y journal propios (`plans/inbox-20260830/physical-ownership-addendum-v1.md:122-135`) | Cuadra como prohibición |

S-04-TELEMETRY empieza **sólo** tras receipt PASS. Futuros OWNS de esa
unidad (no concedidos aquí): región `DispatchVehicleTelemetry` de
`addon/scripts/5_Mission/MCPClientBridge.c` y
`[DESIGN] class TestVehicleTelemetryLiveContract` de
`tools/tests/test_vehicle_telemetry_contract.py`; excluye
`TestVehicleTelemetryWireContract` (`TEMP/successor-authority-draft.md:64`).

Este ADR **no** especifica rebuild/redeploy tras editar
`DispatchVehicleTelemetry`. El PBO candidate se sella antes del receipt
(§2) y la corrida live exige `no_file_patching=true`
(`tools/dayz_mcp/dayz_test_worker.py:237-238`). Las cinco filas contra
ese binario no acreditan código S-04 aún no materializado; filePatching
no cubre el hueco. **P1 permanece abierto**: una campaña transaccional
post-edición queda fuera de esta fase.

---

## 7. Tres recetas y ninguna más

[EXACT] `TEMP/successor-authority-draft.md:62`. Introducir un cuarto
`expected_type` es un fallo duro.

| recipe | seat_index | expected_type |
|---|---:|---|
| CivilianSedan/0 | 0 | `"CivilianSedan"` |
| CivilianSedan/1 | 1 | `"CivilianSedan"` |
| Boat_01_Blue/0 | 0 | `"Boat_01_Blue"` |

Cada receta exige spawn, preimagen única, selección type exacta, asiento
exacto y cleanup. `CivilianSedan` acredita conductor y no-conductor;
`Boat_01_Blue` acredita presencia no-CarScript y, tras get-out y antes de
cleanup, `CrewMemberIndex=-1`
(`plans/inbox-20260830/30-fb-20260830-002237-0de3.md:52-57`). El setup
acepta sólo `CrewMemberIndex==seat_index`, asiento esperado y receipt real
de seated/seat/type/classname
(`plans/inbox-20260830/30-fb-20260830-002237-0de3.md:42-44`).

Oráculos de asiento (receipt, no una cuarta receta) [DESIGN]:

| recipe | `seat_oracle.vehicle_seat` | `setup_receipt.seat` |
|---|---|---|
| `CivilianSedan/0` | `"VEHICLESEAT_DRIVER"` | `"driver"` |
| `CivilianSedan/1` | `"VEHICLESEAT_CODRIVER"` | `"codriver"` |
| `Boat_01_Blue/0` | `"VEHICLESEAT_DRIVER"` | `"driver"` |

Ningún expected de type/classname/asiento/presencia puede proceder de
`vehicle_telemetry` ni del helper que se está juzgando
(`plans/inbox-20260830/30-fb-20260830-002237-0de3.md:47-58,60-62`;
`TEMP/successor-authority-draft.md:64`).

---

## 8. Cinco filas S-04-TELEMETRY y gates R26

Las tres recetas físicas producen cuatro filas con transporte; la fila
sin transporte se acredita **antes** de crear fixtures. El sentinel del
mapper no sustituye ninguna fila live
(`TEMP/successor-authority-draft.md:64`;
`plans/inbox-20260830/30-fb-20260830-002237-0de3.md:46-57`). Semántica base:
`plans/inbox-20260830/00-execution-dag.md:87-95`.

**R26** [DESIGN] es el gate live de estas cinco filas. Cada fila se
clasifica exactamente como `PASS`, `FAIL` o `INCONCLUSIVE-setup-failed`
según la **regla total** siguiente (los ejemplos de la tabla son
subcasos, no enumeración exhaustiva)
(`plans/inbox-20260830/30-fb-20260830-002237-0de3.md:60-64`):

1. **PASS** sólo si se acreditan setup y transporte/observación **y** la
   observación casa **exactamente** el expected independiente de la fila
   (conjunción exacta).
2. **FAIL** si setup y transporte/observación son observables **y** hay
   **cualquier** desviación de ese expected (incluida una telemetría
   bien formada que no casa: p. ej. a-pie con `ok=true,found=true`, o
   `ok=false` con canal observable).
3. **INCONCLUSIVE-setup-failed** sólo si **no** se puede acreditar el
   setup o la observación (UID ausente, action que no arranca, snapshots
   divergentes, comando owner-client que no se forma).

No se promociona un `INCONCLUSIVE-setup-failed` a `PASS`. No hay fallback
servidor (`TEMP/successor-authority-draft.md:64`). Una respuesta semántica
incorrecta con setup acreditado **nunca** se reclasifica como setup-failed.

| fila | montaje / momento | observa en telemetry | expected independiente |
|---|---|---|---|
| conductor | `CivilianSedan/0`, sentado | `ok=true`, `found=true`, `seated=true`, `seat="driver"`, `type="CivilianSedan"`, `classname="CivilianSedan"`; métricas CarScript, incluso ceros legítimos | Literales de receta + receipt setup + preimagen `entities_query`; `query_all_players` da UID dentro; trace independiente coincide en `car_type` y ambos net-id |
| no-conductor | `CivilianSedan/1`, sentado | `ok=true`, `found=true`, `seated=true`, `seat="codriver"`, `type="CivilianSedan"`, `classname="CivilianSedan"` | `seat_index=1`; oráculos `CrewMemberIndex=1` y `GetVehicleSeat=VEHICLESEAT_CODRIVER`; receipt/preimagen y UID dentro |
| sin transporte | antes de crear cualquier fixture | `ok=true`, `found=false`, `seated=false`, `seat=""`, `type=""`, `classname=""` | Dos `query_all_players` estables para el UID con `in_vehicle=false` y literales congelados antes de telemetry |
| transporte con `CrewMemberIndex=-1` | `Boat_01_Blue/0`, después de `ActionGetOutTransport` y antes de cleanup | `ok=true`, `found=true`, `seated=false`, `seat="unknown"`, `type="Boat_01_Blue"`, `classname="Boat_01_Blue"` | Receipt/preimagen del bote conservan identidad; `query_all_players` del UID da `in_vehicle=false`; la ventana se observa live antes de borrar |
| `Transport` no-`CarScript` | `Boat_01_Blue/0`, aún sentado y antes del get-out | `ok=true`, `found=true`, `seated=true`, `seat="driver"`, `type="Boat_01_Blue"`, `classname="Boat_01_Blue"`; métricas exclusivas CarScript quedan en defaults wire y no gobiernan presencia | Literales/receipt/preimagen; `CrewMemberIndex=0`, token DRIVER y UID dentro; ningún cast CarScript decide presencia |

### Gates R26 por fila

[DESIGN] umbrales; [EXACT] criterios de
`plans/inbox-20260830/30-fb-20260830-002237-0de3.md:60-64` y
`TEMP/successor-authority-draft.md:64`.

| fila | R26 PASS | R26 FAIL | R26 INCONCLUSIVE-setup-failed |
|---|---|---|---|
| conductor (`CivilianSedan/0`) | telemetry casa type/classname/seat=driver con expected independientes; trace `car_type`+ambos net-id coinciden; UID `in_vehicle=true` | índice ignorado/forzado 0; `expected_type` ignorado; type/classname vacío o distinto del expected; oracle copiado del payload; mock | spawn/preimagen no única; receipt no acredita asiento; UID ausente/duplicado; trace no se liga |
| no-conductor (`CivilianSedan/1`) | telemetry `seat=codriver`; `CrewMemberIndex=1`; `VEHICLESEAT_CODRIVER`; type/classname=`CivilianSedan` | acaba en crew 0/driver; expected derivado de telemetry; `vehicle_enter` server | `CivilianSedan/1` no forma comando owner-client; receipt no acredita `seat_index=1` |
| sin transporte | a-pie **antes** de fixtures; `found=false,seated=false`; dos snapshots UID `in_vehicle=false` | declarar a-pie mediante cleanup; omitir la fila; sustituirla por mock | UID empieza dentro de transporte; snapshots divergentes; `query_all_players` no identifica exactamente al UID |
| `CrewMemberIndex=-1` (`Boat_01_Blue/0` post get-out) | ventana live `found=true,seated=false,seat="unknown"` **antes** de cleanup; UID fuera | contar forced-delete/eyección posterior como fila; borrar antes de congelar el veredicto; colapsar índice -1 con a-pie | action get-out no arranca; 20 sondas/2 s no observan `CrewMemberIndex=-1`; UID no está fuera antes del cleanup |
| no-`CarScript` (`Boat_01_Blue/0` sentado) | presencia/asiento/type/classname sin que métricas CarScript gobiernen; `CrewMemberIndex=0` | cast CarScript temprano; `GetSeatAnimationType` como asiento telemetry; `ResolveOwnedCar` | `Boat_01_Blue/0` no forma comando owner-client; `GetVehicleSeat()` fuera del enum |

Omitir una fila, ignorar `seat_index` o `expected_type`, sustituirla por
mock o tocar la clase wire = R26 FAIL del lote
(`TEMP/successor-authority-draft.md:64`). La transición debe congelarse
antes del cleanup (`plans/inbox-20260830/30-fb-20260830-002237-0de3.md:57-58`).
La tabla de arriba no es cerrada por enumeración: cualquier observación
válida no listada en FAIL que desvíe del expected cae en FAIL por la
regla total; cualquier imposibilidad de acreditar setup/observación no
listada cae en INCONCLUSIVE.

---

## Veredicto documental

Candidate-live permanece **DEFERRED, sin OWNS**. Este ADR congela
namespace propio bajo `GetTempPath`, `candidate_dir` distinto del PBO
esperado, dest y rollback propios con journal de fases y cadena `events`,
exclusiones explícitas de `dayz-mcp-m24`, receipt v1 (preimagen unión,
recetas objeto, locators de setup/query/session/E6), la arista sucesora y
las tres recetas. `events` no acredita rollback físico (N5);
`origin_restored` no acredita teleport (N6); E6 no cierra source→staging
(N7); `all_recipes_cleaned` no acredita mundo limpio (X4). No autoriza write, deploy, AddonBuilder, DayZ ni edición de
worker/bridge/tests. No convierte `DEFERRED` ni `INCONCLUSIVE` en `PASS`.

Ronda 15: X4 — `all_recipes_cleaned` autoafirmado (misma estructura que
N5/N6). X6 — se nombra la reclamación del SO sobre el árbol
`GetTempPath()`. **Y1/X5 abierto**: no se totaliza la pertenencia de un
miembro preexistente de un árbol o prefijo M24; no se añade un sexto
discriminador; candidate-live **no puede acreditar «M24 sin accesos» por
sí solo**, lo que refuerza que siga `DEFERRED`. X1/X2/X3 viven en S15.
Ronda 14: **V1/W1 cerrado** — el espía vigila temp, candidate, publish,
backup y journal M24 por identidad en runtime, no sólo los tres prefijos
temp; el sentinel exige no-reemplazo si el objeto existía; `R\addon`
queda abierto por nombre. **W2**: el mutante `%TEMP%`/`$WorkDrive` deja
de listarse como FAIL ejecutable. **V2**: autorreferencia
`adr-candidate-live-s04-s24.md`. **W3**: autoridad vigente
`20260901-ledger-v5` / `inbox-20260901-v8` / bundle `v9`; predecesor:
bundle `v8`. Candidate-live sigue DEFERRED, sin OWNS.
Ronda 13: **N1/M1 cerrado** — unit gate (espía, cero intentos, no abre) +
gate de integración (sentinel M24/M25, byte-identidad, path no revelado);
la conjunción sostiene «M24 byte-idéntico y sin accesos»; el gate de
integración no es de candidate-live (dependencia, no hueco).
Candidate-live sigue DEFERRED, sin OWNS. K2 no
se declara cerrado. M2: binding Python de `GetTempPath` [DESIGN]; mutante
no ejecutable.
Ronda 12: N1/M1 se declaró abierto; esta ronda lo cierra con el patrón
668f:37/:48.
Ronda 11: K2 quedó PARCIAL (existentes por `PathIdentity`; ausentes por
padre+nombre); N1/M1 lo reabre.
Ronda 10: cerrado G1 (no comparar deletreo `Q\`/`P:\`).
Ronda 9: cerrado E2 (abortar no tiene arista de completed compensado; se
reanuda por la matriz de §2 o `recovery_required`/`INCONCLUSIVE`).
Ronda 7: cerrados C3 (espía sobre los tres deletreos M24), C6
(`candidate_pbo_sha256` de `events` es vigente al publicar la fase, `null`
en `seq=0`) y C7 (retirada `TEMP/successor-authority-draft.md:60-64`).
Ronda 5: cerrados Q1, H8 (fila `prepared` ligado/dest ausente + dimensión
candidate CAS). Abiertos por retirada de afirmación, sin contrato nuevo:
N5, N6, N7, H9, H12, P1, P3, X4, Y1/X5.

La implementación es una fase posterior y requiere un prompt nuevo, hashes nuevos de
los ADR aprobados, nueva autorización de OWNS y gates propios.

---

## LO QUE NO PUDE VERIFICAR

1. **El FINAL `verify_successor_r3` no tiene artefacto on-disk hash-pineado
   disponible.** No se usó. Las decisiones de este ADR no tratan sus
   bloqueos S15 como autoridad de candidate-live.
2. `[IO.Path]::GetTempPath()`, `$env:TEMP` y la constante `T` de
   `plans/inbox-20260830/physical-ownership-addendum-v1.md:18-24` no se
   midieron en esta máquina. No afirmo igualdad ni desigualdad. El
   binding Python de esa API es [DESIGN], no acreditado; el mutante
   `$env:TEMP`/`tempfile.gettempdir()` no es FAIL ejecutable. La base
   candidate permanece definida por la API, no por el valor de `T`. Los
   tres nombres de la base M24 quedan `CONFLICT` (tabla de procedencia).
   `P:\` ≡ `Q` lo registra el addendum (`:9-11,21-24`); no ejecuté `subst`.
   El árbol de evidencia del receipt (journal completed, E6, setup,
   consultas, `session_status`) vive bajo `GetTempPath()` (temp de
   usuario, `plans/inbox-20260830/physical-ownership-addendum-v1.md:24`).
   El SO y las herramientas de limpieza pueden reclamarlo entre
   candidate-live y S-24.final; este ADR sólo nombra el borrado
   voluntario. Reclamación del SO → S-24.final `INCONCLUSIVE`. Si esa
   evidencia vive en temp o en un árbol durable lo fija la ficha
   sucesora que conceda OWNS (`TEMP/successor-authority-draft.md:62`).
   **Abierto:** source `R\addon`
   (`plans/inbox-20260830/physical-ownership-addendum-v1.md:124`) no está
   en el conjunto vigilado del espía; no se afirma «sin accesos» sobre
   ese árbol. El seam del espía y el gate de integración no se
   ejecutaron (candidate-live sigue DEFERRED).
3. No existe literal aprobado para namespace/destino/backup/journal de
   candidate-live en autoridad vigente: el borrador exige ficha separada y
   hoy le concede cero OWNS (`TEMP/successor-authority-draft.md:62`).
   `source.path` y `deployed_candidate.path` son campos exactos cuyo valor
   debe pinnearlo esa ficha.
4. Schema del manifest source PASS de S-10: no está en las fuentes
   abiertas. `digest_kind="s10-source-manifest-v1"` es [DESIGN].
5. Firma ctypes de `GetTempPathW` (buffer, MAX_PATH vs long-path): no está
   en los `.py` pineados. No se inventa. El mutante `$env:TEMP` /
   `tempfile.gettempdir()` no se presenta como FAIL acreditado.
6. `tools/dayz_mcp/win32_fileinfo.py` no forma parte de la copia staged.
7. Comportamiento de DayZ si `-mod=` apunta a un árbol bajo Temp
   (antivirus, MAX_PATH): no medido. Si el dest acreditado no es cargable,
   el resultado es INCONCLUSIVE, no un fallback a
   la identidad del publish M24.
8. Nombre de gate **R26**: no aparece en las fuentes pineadas; se congela
   aquí como el clasificador PASS / FAIL / INCONCLUSIVE-setup-failed de las
   cinco filas, con la regla total de desviación=FAIL. No se ejecutó DayZ ni se reprodujeron las filas.
   Hallazgos H6, H7, H10 de la ronda 1 siguen cerrados (no reabiertos). N4
   sigue cerrado. N3 y P2 siguen cerrados. G1 (no comparar deletreo
   `Q\`/`P:\`) permanece. **K2 no se declara cerrado.** Q1/H8: fila
   `prepared` con PBO+E6+journal ligados y dest ausente, y candidate CAS
   `{ausente,exacto,discrepante}` en cada frontera de replace. Abiertos,
   listados por número:
   - **N5** — `events` es relato del mismo productor; no hay observador
     independiente ni `dest_exists=false` recomputable tras `redeployed`.
     Un log sellado ajeno sería contrato nuevo; queda fuera.
   - **N6** — no hay locator+SHA de `query_all_players` post-restore con
     UID y posición; `origin==post` se retira como evidencia.
   - **N7** — schema hash-pineado del manifest S-10 sigue ausente; E6 no
     compara source↔staging por entrada.
   - **X4** — `all_recipes_cleaned` es autoafirmado: las cuatro
     `entities_query` las escribe el productor; el consumidor rehashea
     artefactos, no observa el mundo (`TEMP/successor-authority-draft.md:8`).
   - **Y1/X5** — el espía no totaliza pertenencia de un miembro
     preexistente de árbol o prefijo M24; la conjunción no sostiene
     «sin accesos» para ese caso; no se añade un sexto discriminador
     (ver ítem 10).
   - **H9** / **H12** — abiertos mientras N5/N6/N7/X4 no tengan observación
     independiente; el schema añadido no se declara propiedad acreditada.
   - **P1** — acreditar S-04 exigiría rebuild/redeploy tras editar
     `DispatchVehicleTelemetry`; no se escribe esa transacción aquí.
   - **P3** — estados entre journal `completed` y receipt live (spawn,
     query, cleanup, restore, release) exigirían un journal de campaña;
     no se escribe aquí.
   El broker AddonBuilder nativo sigue sin estar staged: el contrato de
   directorio se ancla al caller `tools/dayz_mcp/dayz_test_worker.py:554-575`
   y a `plans/inbox-20260830/32-fb-20260830-011217-668f.md:35`.
   El schema interno del manifest S-10 sigue sin estar en las fuentes
   abiertas: E6 exige reabrir+rehash de source/staging/PBO por separado, no
   interpreta campos no pineados de ese manifest.
9. El overlay `WorkerRuntimePolicy` puede exigir un fichero nuevo o tocar
   M20; queda a la autoridad sucesora, sin OWNS hoy. Quién instancia la
   policy en `app_main.py` no se abrió.
10. **Y1/X5 abierto.** El espía anti-M24 no totaliza la pertenencia de un
    miembro que ya existe dentro de un árbol o prefijo M24, así que la
    conjunción espía∧sentinel no sostiene «sin accesos» para ese caso.
    - **qué falta**: totalizar la pertenencia de un miembro preexistente
      de un árbol o prefijo M24, y sus negativos;
    - **por qué queda fuera**: es la quinta iteración del mismo
      discriminador (`G1` → `K2` → `N1/M1` → `V1/W1` → `Y1/X5`); cada
      una ha abierto la siguiente, y un ADR no es el sitio donde converge
      un predicado de pertenencia de filesystem — converge contra un host
      real, con el espía instrumentado;
    - **quién lo cierra y cuándo**: el gate de integración de M24/M25, al
      instrumentar el espía sobre un árbol real, no este documento;
    - **consecuencia mientras tanto**: candidate-live **no puede
      acreditar «M24 sin accesos» por sí solo**, lo que refuerza —no
      debilita— que siga `DEFERRED`.
    No se añade un sexto discriminador.

---

## FICHEROS_TOCADOS

- `adr-candidate-live-s04-s24.md` (este fichero; autoridad sucesora v9; candidate-live sigue DEFERRED, sin OWNS)

Ningún OWNS de M24, M10, M04, M15 ni worker. Ningún PBO, journal M24,
`dayz-mcp-m24`, `GATES.md`, DAG ni inbox.
