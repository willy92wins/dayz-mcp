# S-14 — opciones cerradas para fingerprint y autoridad por sesión

Estado: mini-spec de autoridad para aprobación. No implementa M14 ni M22/M23.
Destino: TEMP solamente; no es successor draft ni modifica el repositorio.

## Alcance y evidencia reacreditada

Las fichas aprobadas `plans/inbox-20260830/07-fb-20260829-024827-9b7b.md:28-33` y `plans/inbox-20260830/09-fb-20260829-025012-103f.md:26-31` exigen una huella del registro final por sesión FastMCP, ligada a `(ControlIdentity.session_id, profile, role)`, comparada únicamente con el payload de la misma pareja del commit marker M23. PID, daemon generation, `app` propia, helper recalculado, `/status`, candidates, backups y journals no son autoridad.

`[EXACT product-spec.md:90]` Este trabajo traza a DPF E5: fingerprint canónico post-registro/aliases/perfiles, señal `reopen_mcp_client` sin reiniciar daemon y mutantes/expected independientes. La separación M14→M22→M23 conserva ese Intent; no añade hot reload, reinicio ni otra autoridad.

- `[EXACT tools/dayz_mcp/server.py:2496-2537]` `build_app(config: ServerConfig) -> tuple[FastMCP, Any]` crea la app y runtime locales; `[EXACT tools/dayz_mcp/server.py:4298-4299]` aplica hoy el último alias y devuelve `(app, runtime)`. `[DESIGN]` Ese número de línea no es el seam: M22 captura dentro de `build_app`, después de todas sus altas de tools y wrappers y justo antes del único `return app, runtime`. Nunca captura después de que la función haya retornado ni en una línea intermedia anterior a la última mutación.
- `[EXACT tools/dayz_mcp/server.py:3828-3838]` la tool pública `bridge_status()` hace `runtime.touch()` y devuelve `await runtime.bridge_status_payload()`; `[EXACT tools/dayz_mcp/server.py:1412-1421]` el ClientRuntime obtiene el payload base del daemon mediante HTTP `/status`.
- `[EXACT tools/dayz_mcp/control_client.py:41-72]` `ControlIdentity` exige `platform`, `pid`, `ppid`, `started_at_utc`, `session_id` y `task_label`, valida platform y session_id y serializa todos esos campos en `to_payload()`.
- `[EXACT tools/dayz_mcp/server.py:853-869]` ClientRuntime crea `ClientIdentity` con `platform=config.client_platform` y `session_id=str(uuid.uuid4())`, y convierte esa identidad en `ControlIdentity`.
- `[EXACT tools/dayz_mcp/daemon.py:624-672]` `make_status_provider(config: Any, state: ServerState) -> Callable[[], dict]` calcula status daemon y `daemon_modules`; no contiene ni debe recibir campos `tool_registry_*`.
- `[EXACT tools/dayz_mcp/loopback.py:2908-2922]` `_handle_status()` obtiene el payload desde `status_provider()` o `state.status_snapshot()` y lo sirve como `/status`; el overlay M14 no pertenece a esta ruta.
- `[EXACT tools/dayz_mcp/effective_schema.py:109-154]` `build_payload(...) -> dict[str, Any]` exige `profile` y `role` ya finalizados, fingerprint lowercase SHA-256 de 64 caracteres y un conjunto cerrado de campos; no calcula la huella del registry.
- `[EXACT]` En este checkout no existen aún `tools/dayz_mcp/tool_registry_fingerprint.py` ni `tools/tests/test_tool_registry_fingerprint.py`; son los únicos OWNS físicos previstos por el addendum (`plans/inbox-20260830/physical-ownership-addendum-v1.md:63`).

El addendum fija como canónicos M23 `dayz-effective-schema-v1.json`, `tool-registry-fingerprint-v1.sha256`, `effective-schema-v5-verdict.json`, `effective-schema-producers-v1.json` y `m23-receipts.jsonl` (`plans/inbox-20260830/physical-ownership-addendum-v1.md:95-103`). El plan M23 describe el marker y sus enlaces como `[DESIGN]`, no como API ya existente (`plans/inbox-20260830/26-fb-20260829-194752-d366.md:31-38,47,50,61`); por eso las firmas y nombres de este documento son propuestas que requieren congelación.

## Invariantes comunes antes de elegir opción

1. El fingerprint cubre únicamente el registro público final. M22 descarta los atributos FastMCP ajenos al contrato y entrega una entrada con el keyset exacto `name`, `description`, `input_schema`, `public_constraints`, `effect_verification`. Antes de aplicar la semántica de valores de `_tool_record` (`tools/dayz_mcp/effective_schema.py:82-107`), M14 exige esos cinco campos, sin ausentes ni extras, y valida sus tipos/enum sin activar defaults históricos: `name` es `str` no vacío, `description` es `str` —vacío permitido, `None` prohibido—, `input_schema` es mapping, `public_constraints` es `list[str no vacíos y únicos]` y `effect_verification` es exactamente `"wire"|"in_game_required"`. Después proyecta esos mismos cinco campos. Un keyset o campo inválido no produce una huella parcial. No incluye PID, PPID, `started_at_utc`, `session_id`, `profile`, `role`, timestamp de captura, daemon generation ni orden incidental de registro.
2. Tras la proyección y antes de comprobar duplicados u ordenar, todos los strings —incluidas claves y valores anidados de `input_schema`— se normalizan Unicode NFC. Una colisión de `name`, de `public_constraints` o de claves de un mismo objeto después de NFC es inválida. La serialización es UTF-8 sin BOM, sin newline final y con `allow_nan=False`; todas las object keys son strings y se ordenan lexicográficamente, mientras los arrays conservan orden semántico. Los records se ordenan por el `name` ya normalizado, por code point ascendente. Cualquier tipo no JSON, NaN/Infinity, clave no string, colisión o fallo Unicode/JSON deja la captura `unknown`.
3. Los bytes exactos que entran en SHA-256 son los bytes producidos por el serializador elegido, no el texto reserializado por M23, no `repr()`, no `app.list_tools()` transformado en expected y no bytes de `/status`. La huella expuesta es lowercase hexadecimal de 64 caracteres.
4. `session_id`, `profile` y `role` ligan el snapshot local, pero no se hashean: dos sesiones distintas con la misma pareja y el mismo registro pueden tener el mismo fingerprint. Un cruce de role/profile aunque el fingerprint coincida queda `unknown`.
5. Estados cerrados del comparador: `fresh` si autoridad válida, misma pareja canónica y fingerprint igual; `stale` si autoridad válida, misma pareja canónica y fingerprint distinto; `unknown` si falta/queda inválida cualquier identidad, marker, sidecar, receipt, pareja o precondición. Nunca se usa `false`, una coincidencia sólo por fingerprint ni una autoridad desconocida para producir `fresh`/`stale`.
6. La procedencia de identidad es cerrada: M22 sólo puede pasar el mismo `session_id` que `ClientRuntime` crea en `ClientIdentity` y serializa al construir `ControlIdentity` (`tools/dayz_mcp/server.py:853-869`), junto con la pareja positiva proyectada por M13 desde el mismo `ServerConfig`. `Runtime` embedded/daemon, o cualquier session/profile/role ausente o no acreditable, se representa como snapshot local `unknown`; PID, UUID inventado por el adapter, app, daemon generation y `/status` no lo reparan. Este contrato no añade una regex UUID ni un parser ISO al contrato real de strings no vacíos de `ControlIdentity` (`tools/dayz_mcp/control_client.py:49-64`).

### Contrato cerrado del adaptador M22→M14

`[DESIGN]` En el seam único dentro de `build_app`, M22 ejecuta sin `await` exactamente `registered_tools = tuple(app._tool_manager.list_tools())`, la vista síncrona ya usada por el test real (`tools/tests/test_mcp_tools.py:231-235`) sobre el mismo manager privado que muta el alias (`tools/dayz_mcp/server.py:1526-1535`). La llamada ocurre una sola vez, después de todas las altas, aliases y wrappers M22. Para cada `tool` construye los tres campos base mediante un único mapping cerrado: `name = tool.name`, `description = tool.description` e `input_schema = tool.parameters`, los tres sin default; ausencia, excepción de acceso, name vacío/no string, description no string —incluido `None`— o parameters no mapping invalida la captura. Quedan prohibidos `await app.list_tools()`, su campo wire `inputSchema`, `extract_tool_records()` y cualquier fallback `""` o `{}` como segundo origen.

`[DESIGN]` La fuente del join vive en `server.py`, OWNS M22, como dos literales module-private: `_TOOL_REGISTRY_METADATA_BASE`, mapping completo de las 61 tools `standard`, y `_EXEC_ENFORCE_REGISTRY_METADATA`, record de metadata exclusivo de `exec_enforce`. Cada clave de la base es un nombre exacto de tool y cada valor —incluido el record aditivo— tiene exclusivamente `public_constraints` y `effect_verification`. M22 selecciona sin observar el registry: si el mismo `config.enable_exec_enforce` que gobierna el alta en `tools/dayz_mcp/server.py:3820-3826` es false, copia sólo la base; si es true, copia la base y añade exactamente la entrada `exec_enforce`. Esta selección coincide con la proyección M13 del mismo `ServerConfig`; el role no altera el inventario. Queda prohibido filtrar la tabla por `registered_tools`, construirla desde `_tool_record`/wire o derivarla del expected del test.

`[DESIGN]` Por el `name` exacto anterior a NFC, M22 hace el join uno-a-uno de los tres campos base con la tabla seleccionada. Sus nombres deben tener igualdad exacta de conjunto y cardinalidad con los nombres del registro final: clave ausente, sobrante, repetida o ambigua, metadata no mapping, tipo inválido o enum distinto de `"wire"|"in_game_required"` invalida la captura. En particular, M22 nunca completa una tool productiva sin metadata con `public_constraints=[]` ni con `effect_verification="wire"` por defecto.

`[DESIGN]` El resultado del join es una tupla concreta, no un iterator lazy, de `Mapping[str, object]` con exactamente los cinco campos `name`, `description`, `input_schema`, `public_constraints` y `effect_verification`; es el único valor `tools` permitido al invocar inmediatamente `capture_registry_snapshot()`. M14 valida keyset, tipos y enum —en particular rechaza `description is None`— antes de `_tool_record`, copia el schema durante la proyección y devuelve el snapshot inmutable; los objetos FastMCP crudos y la salida de tres campos de `extract_tool_records()` no son entrada válida. M14 aplica después NFC, colisiones y serialización de los invariantes 1-3. Si el registro o la tabla no se pueden observar por setup, M22 queda INCONCLUSIVE y publica snapshot local `unknown`; si son observables pero el mapping/join incumple el contrato, el caso negativo debe producir snapshot `unknown` y el gate falla si se acepta o se rellena silenciosamente.

`[DESIGN]` Con el pin real `mcp==1.27.2` (`tools/requirements-mcp.txt:1`), la sentinel de integración es exclusivamente test-only: nunca queda registrada por una ejecución productiva ni se añade al inventory M13. Este es un tercer positivo separado de los positivos limpios `standard`/`exec_enforce`: no exige igualdad de conjunto con el inventory M13, porque añade deliberadamente una tool fuera de ese fixture. El test usa exactamente `ServerConfig(mode="client", enable_exec_enforce=False, client_platform="claude")`; `build_app` crea por esa rama un `ClientRuntime` (`tools/dayz_mcp/server.py:2496-2497`) y M22 usa exclusivamente el mismo `runtime.identity.session_id` que ese runtime ya serializó en `ControlIdentity` (`tools/dayz_mcp/server.py:853-869`), más la pareja M13 `standard|claude`, sin inventar identidad ni llamar al daemon. El test conserva una referencia a `_patch_public_argument_alias` y la sustituye temporalmente por un wrapper que primero delega el alias real y después registra en esa sola app una función sin argumentos llamada exactamente `m22_registry_sentinel`; como `build_app` llama al alias en `tools/dayz_mcp/server.py:4298`, la alta ocurre después del alias y antes del seam real, sin nuevo hook ni edición de OWNS M13. Durante el mismo contexto, el test sustituye temporalmente `_TOOL_REGISTRY_METADATA_BASE` por una copia literal independiente de la base productiva más la entrada sentinel, deja intacta `_EXEC_ENFORCE_REGISTRY_METADATA` y restaura ambas sustituciones al salir. Su record expected queda congelado fuera del helper bajo prueba como `name="m22_registry_sentinel"`, `description="M22 registry sentinel"`, `input_schema={"properties":{},"title":"m22_registry_sentinelArguments","type":"object"}`, `public_constraints=[]` y `effect_verification="wire"`; la entrada temporal de metadata aporta explícitamente los dos últimos campos. El test compara los cinco valores, bytes y digest contra expected literales; no deriva el expected desde `tool.parameters` capturado ni reutiliza `"wire"` como fallback productivo.

## Mapping público cerrado del overlay M22

`[DESIGN]` En cada invocación, `bridge_status()` obtiene primero `payload = await runtime.bridge_status_payload()`; sólo después solicita de nuevo los cinco paths canónicos M23, construye un `AuthorityBundleBytes` nuevo, lo parsea mediante M14 y clasifica ese resultado contra el snapshot local inmutable. No se permite capturar, cachear ni reutilizar un `AuthoritySnapshot` en `build_app` ni entre invocaciones. Finalmente añade siempre las cuatro claves a la copia de `payload`. `tool_registry_remediation` vale siempre el literal `"reopen_mcp_client"`; las otras tres claves se derivan únicamente de esta tabla:

| Snapshot local | Autoridad para la pareja | `tool_registry_fingerprint` | `tool_registry_captured_at` | `tool_registry_source_stale` |
|---|---|---|---|---|
| `unknown` | cualquiera | `null` | `null` | `null` |
| `known` | ausente, inválida o de otra pareja | fingerprint local | timestamp local | `null` |
| `known` | válida, misma pareja, digest igual | fingerprint local | timestamp local | `false` |
| `known` | válida, misma pareja, digest distinto | fingerprint local | timestamp local | `true` |

`[DESIGN]` Un cruce profile/role entra en la segunda fila y el comparador devuelve `unknown`; nunca se degrada a `stale`. El fingerprint y timestamp locales no se borran sólo porque M23 aún no sea acreditable: se ponen a `null` únicamente cuando el propio snapshot local es `unknown`.

## Opción A — JSON canónico único (recomendada)

Voto: A. Es el formato más pequeño de auditar byte a byte, se puede inspeccionar sin parser propietario y permite probar explícitamente Unicode, sort y ausencia de newline.

`[DESIGN]` Tipos y firmas exactas propuestas en `tools/dayz_mcp/tool_registry_fingerprint.py`:

```python
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

Profile: TypeAlias = Literal["standard", "exec_enforce"]
Role: TypeAlias = Literal["claude", "codex"]
SnapshotStatus: TypeAlias = Literal["known", "unknown"]
AuthorityStatus: TypeAlias = Literal["fresh", "stale", "unknown"]

@dataclass(frozen=True)
class RegistrySnapshot:
    session_id: str | None
    profile: Profile | Literal["unknown"]
    role: Role | Literal["unknown"]
    captured_at_utc: str | None
    fingerprint: str | None
    canonical_bytes: bytes | None
    status: SnapshotStatus

@dataclass(frozen=True)
class AuthorityBundleBytes:
    marker: bytes | None
    fingerprint_sidecar: bytes | None
    verdict_sidecar: bytes | None
    producers_sidecar: bytes | None
    receipts: bytes | None

@dataclass(frozen=True)
class AuthoritySnapshot:
    artifact_txid: str | None
    profile: Profile | Literal["unknown"]
    role: Role | Literal["unknown"]
    fingerprint: str | None
    status: Literal["known", "unknown"]

def canonical_json_bytes(value: object) -> bytes: ...

def canonical_registry_fingerprint(
    tools: Sequence[Mapping[str, object]],
) -> tuple[bytes, str]: ...

def capture_registry_snapshot(
    *,
    session_id: object,
    profile: object,
    role: object,
    captured_at_utc: object,
    tools: Sequence[Mapping[str, object]],
) -> RegistrySnapshot: ...

def read_authority_marker(
    bundle: AuthorityBundleBytes,
    *,
    expected_profile: object,
    expected_role: object,
) -> AuthoritySnapshot: ...

def compare_snapshot_to_authority(
    local: RegistrySnapshot,
    authority: AuthoritySnapshot,
) -> AuthorityStatus: ...
```

`[DESIGN]` `canonical_json_bytes(value)` aplica la normalización/validación de los invariantes y después `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)`, codifica con `utf-8`, no antepone BOM ni añade `\n`. `canonical_registry_fingerprint(tools)` exige primero que cada elemento sea un mapping con el keyset exacto de cinco campos y valida los tipos/enum cerrados del invariante 1, incluido `type(description) is str`; sólo entonces aplica `_tool_record`, proyecta/ordena, construye `{"tools":[...]}` mediante esa función y devuelve `(canonical_bytes, sha256(canonical_bytes).hexdigest())`. Tanto `capture_registry_snapshot()` como M23 reutilizan esa función; el objeto raíz no lleva session/profile/role/timestamp, que sólo viven en `RegistrySnapshot`.

`[DESIGN]` Fail-closed A: el helper puro acepta `session_id` y `captured_at_utc` sólo como strings no vacíos y `profile`/`role` sólo como pareja canónica; M22 pasa `None` si no puede acreditar la procedencia del invariante 6. No se añade validación UUID/ISO. Si un tool no es mapping, su keyset no es exactamente los cinco nombres, cualquier campo incumple los tipos/enum previos —`description=None` incluido—, o fallan `_tool_record`, normalización, tipos JSON, unicidades o serialización, devuelve `RegistrySnapshot(session_id=None, profile="unknown", role="unknown", captured_at_utc=None, fingerprint=None, canonical_bytes=None, status="unknown")`; nunca usa los defaults de description, input_schema o public_constraints de `_tool_record` para reparar un mapping incompleto o inválido. No se acepta `tools=None`, mapping, string ni un iterator mutable: la entrada es una `Sequence` fotografiada una sola vez.

## Opción B — JSON Lines canónico por record (descartada)

`[DESIGN]` Se evaluó JSONL porque hace visibles los límites por tool, pero añade reglas de LF/final-line y un segundo parser sin aportar autoridad independiente. Para `artifact_version=5` y `schema_version=1` queda elegida exclusivamente A: no existen firmas B, no hay autodetección ni fallback. Aceptar JSONL en el futuro exigiría otra versión aprobada del schema y otra revisión; no una rama silenciosa del reader actual.

## Contrato cerrado de los cinco blobs M23 que consume M14

Esto es `[DESIGN]`, derivado de `plans/inbox-20260830/07-fb-20260829-024827-9b7b.md:31`, `09-fb-20260829-025012-103f.md:28` y `26-fb-20260829-194752-d366.md:31-38,46-51,57-62`. Para `artifact_version=5` y `schema_version=1` no queda autodetección: los tres JSON de contenido (`marker`, `verdict_sidecar`, `producers_sidecar`) usan exactamente el serializador canónico A, UTF-8 sin BOM y sin newline final. El reader parsea y reserializa con ese contrato y exige igualdad byte a byte; JSON semánticamente equivalente con otros bytes queda `unknown`.

### 1. Commit marker `dayz-effective-schema-v1.json`

`[DESIGN]` El objeto raíz tiene exactamente `artifact_version`, `schema_version`, `artifact_txid`, `payloads`, `generator`, `producers`, `fingerprint_sha256`, `verdict_sha256` y `producers_sha256`. `artifact_txid` son 32 hex lowercase; `generator` tiene exactamente `name` y `version`, strings no vacíos. `payloads` contiene exactamente estas cuatro parejas y en este orden: `standard|claude`, `standard|codex`, `exec_enforce|claude`, `exec_enforce|codex`.

`[DESIGN]` Cada payload tiene exactamente `profile`, `role`, `instructions`, `tools` y `tool_registry_fingerprint`, conforme al envelope congelado en `plans/inbox-20260830/00-execution-dag.md:151-155` y `tools/dayz_mcp/effective_schema.py:148-170`. Cada tool se proyecta al record exacto de los invariantes 1-2. `tool_registry_fingerprint` es el segundo valor de `canonical_registry_fingerprint(payload.tools)`; M14 y M23 llaman esa misma función M14, no implementaciones paralelas. `producers` es el mismo array parseado que `effective-schema-producers-v1.json.producers`, con paths únicos ordenados lexicográficamente.

### 2. `tool-registry-fingerprint-v1.sha256`

`[DESIGN]` Es un checksum manifest ASCII/UTF-8, no JSON ni un hash opaco de sí mismo. Tiene exactamente cuatro líneas, dos espacios ASCII entre digest y etiqueta, un LF final y ningún BOM, CR, tab, línea vacía ni byte extra. Cada línea casa `^[0-9a-f]{64}  <etiqueta>\n$`; las etiquetas por posición son exactamente: línea 1 `standard|claude`, línea 2 `standard|codex`, línea 3 `exec_enforce|claude`, línea 4 `exec_enforce|codex`.

`[DESIGN]` Cada digest debe coincidir tanto con `payload.tool_registry_fingerprint` como con el SHA-256 que el serializador M14 recalcula desde `payload.tools`. `marker.fingerprint_sha256` es SHA-256 de los bytes exactos de estas cuatro líneas. Esta triple igualdad sólo acredita enlace/consistencia; la corrección externa del contenido la acredita el banco PASS del verdict siguiente.

### 3. `effective-schema-v5-verdict.json`

`[DESIGN]` El objeto raíz tiene exactamente `artifact_version`, `schema_version`, `artifact_txid`, `verdict` y `bank_members`; las tres identidades/versiones coinciden con el marker y `verdict` debe ser el literal `"PASS"`. Cada elemento de `bank_members` tiene exactamente `path`, `sha256`, `expected_ids`, `results` y `verdict`; cada resultado tiene exactamente `id`, `expected`, `observed` y `verdict`. Los IDs son strings no vacíos y únicos, `results` contiene exactamente una entrada por `expected_ids`, en el mismo orden, y todos los verdicts miembro/resultado son `"PASS"`.

`[DESIGN]` El banco contiene exactamente estos cinco miembros, en este orden. `ids_sha256` es el expected externo de `sha256(canonical_json_bytes({"ids": expected_ids}))`; no se calcula desde `results`, schema ni wire:

| `path` | SHA-256 exacto del fixture M13 | IDs | `ids_sha256` externo |
|---|---|---:|---|
| `tools/tests/fixtures/effective_schema_v1/required_constraint_ids.json` | `979e395f1cad3364bbf51f622e70b306d40239989dccf64d58f42cdb9576af9a` | 9 | `8aaffe2751a511a30c166ecad33c11995f249fd25a8787507781fe4124c43ff3` |
| `tools/tests/fixtures/effective_schema_v5/instructions_required_concepts.json` | `aa4b8e3b1602a69fcdf4ef682819e888d5b4c9fbc78185ad9885b0a9f7b3c583` | 6 | `63075c8888d0c80aa8aca55f4da29a69bb6e7b1ccd61de933db10954593b9f94` |
| `tools/tests/fixtures/effective_schema_v5/profile_inventory.json` | `e7d7be819d32f59d8c160a592d66a6038e0708aaf9d06acc11ec8772bfd34a9e` | 4 | `314755d4a61b0ba382b4286ecd42f00efebdfdbee5dc8e7c4702c4e649ad0306` |
| `tools/tests/fixtures/effective_schema_v5/validator_cases.json` | `dc2bd69e325b445287c1680fda3067c676cb85f7662e61fab7d20b88b86897fc` | 18 | `2035e4558adbb877fe22f0c012c5eaeb3dc442d65ec21a403458b9048266bef6` |
| `tools/tests/fixtures/effective_schema_v5/mutation_cases.json` | `88cd0e8975a8703993203ee4775af9f81b28b4640ba9e5ac6fde16f5aef1783b` | 13 | `8f6e6c03433b6101fa3e4a408226c9c06a9436a4cdf561af34f7b407a3774ab0` |

`[DESIGN]` Para `profile_inventory`, cada ID es `profile + "|" + role`; para los otros cuatro miembros es cada string de `required_constraint_ids` o cada campo `id` de `concepts[]`/`cases[]`, conservando el orden del fixture. `ids_sha256` no es un campo autocertificado del sidecar: el reader lo recalcula desde `expected_ids` y lo compara con el literal de la tabla. Exige simultáneamente path, hash, cardinalidad, ese digest externo, cobertura exacta de resultados y PASS; así un verdict autoconsistente pero incompleto o `FAIL` nunca queda `known`. `marker.verdict_sha256` es SHA-256 de los bytes canónicos exactos de este sidecar.

### 4. `effective-schema-producers-v1.json`

`[DESIGN]` El objeto raíz tiene exactamente `artifact_version`, `schema_version`, `artifact_txid`, `producers`, `validator_sources` y `active_approval`. Las versiones/txid coinciden con el marker. Cada producer tiene exactamente `path` y `sha256`; los paths repo-relative son únicos y están ordenados lexicográficamente. `validator_sources` es el mapping cerrado de source-id a path que sigue, derivado del catálogo actual (`tools/dayz_mcp/effective_schema_catalog.py:47,55,62,68-88`); cada path del mapping debe estar presente en `producers`:

| source-id exacto | producer path exacto |
|---|---|
| `dayz_test_request.parse_dayz_test_request` | `tools/dayz_mcp/dayz_test_request.py` |
| `vehicle_get_in_client.seat_index` | `tools/dayz_mcp/server.py` |
| `vehicle_get_in_client.expected_type` | `tools/dayz_mcp/server.py` |
| `build_app.instructions` | `tools/dayz_mcp/server.py` |

`[DESIGN]` No se infiere un path por cortar strings ni se acepta un source-id adicional: cambiar el catálogo exige nueva versión/revisión del mapping. `active_approval` tiene exactamente `txid`, `prepared_path`, `committed_path` y `rolled_back_absent=true`; ambos paths pertenecen a `tools/approved-launchers.receipts/<txid>/`, están presentes en `producers` y no se fabrica hash para la ausencia de `rolled-back.json`.

`[DESIGN]` El reader exige la unión mínima completa de `plans/inbox-20260830/26-fb-20260829-194752-d366.md:38`: `tools/dayz_mcp/server.py`, `tools/dayz_mcp/knowledge.py`, `tools/dayz_mcp/effective_schema.py`, `tools/dayz_mcp/effective_schema_catalog.py`, `tools/dayz_mcp/effective_schema_runtime_validators.py`, `tools/dayz_mcp/dayz_test_modes.py`, `tools/dayz_mcp/tool_registry_fingerprint.py`, `tools/dayz_mcp/dayz_test_request.py`, los cinco fixtures anteriores, `tools/mcp_capture.py`, `tools/promote_effective_schema.py`, `tools/tests/test_effective_schema_promotion.py`, `tools/requirements-mcp.txt`, `tools/pyproject.toml`, todos los paths del mapping `validator_sources`, `tools/native-launchers/dayz-test-v1/app.pyz`, `tools/native-launchers/dayz-test-v1/closure-manifest.json`, `tools/approved-launchers.json` y los receipts prepared+committed de la única aprobación activa. El array `marker.producers` debe ser idéntico al array del sidecar. `marker.producers_sha256` es SHA-256 de los bytes canónicos exactos del sidecar. Falta, duplicado, path no canónico, source-id/path distinto, productor extra que suplante uno requerido, txid discordante o unión incompleta deja authority `unknown`.

### 5. `m23-receipts.jsonl`

`[DESIGN]` Si existe un marker, receipts debe existir: la ausencia sólo es compatible con el estado anterior a la primera publicación, donde tampoco hay marker y la autoridad es `unknown`. El prefijo legacy JSONL válido se conserva byte a byte y no se reserializa; cada línea M23 nueva es un objeto JSON canónico terminado por un LF. Una línea commit M23 nueva tiene exactamente `kind`, `operation_txid`, `artifact_txid`, `previous_schema_sha256` y `schema_sha256`; `kind="commit"`, los txid son 32 hex lowercase, `operation_txid == artifact_txid == marker.artifact_txid` y `schema_sha256` es `sha256(bundle.marker)`, los bytes exactos del marker ya canonicalizado. `previous_schema_sha256` es `null` sólo en primera publicación o 64 hex lowercase en las siguientes.

`[DESIGN]` Para que el bundle sea conocido debe existir exactamente una línea `kind="commit"` cuyo `artifact_txid` y `schema_sha256` coincidan con el marker vigente. Commits históricos, líneas legacy desconocidas y rollbacks se preservan pero no acreditan por sí solos el marker; dos matches, UTF-8/JSONL corrupto o cualquier mismatch produce `unknown`.

### Orden fail-closed del reader y provenance física

`[DESIGN]` `read_authority_marker()` valida, en este orden: presencia de los cinco blobs cuando hay marker; bytes canónicos/shape/versiones/txid; cuatro payloads; manifest fingerprint y sus recomputaciones; hashes marker→sidecars; verdict PASS y banco completo contra los expected externos; unión producers y enlaces; finalmente el único receipt commit con `schema_sha256=SHA-256(marker bytes)`. Cualquier fallo devuelve un `AuthoritySnapshot` completamente `unknown`; nunca conserva un digest parcial ni convierte consistencia interna en PASS.

`[DESIGN]` La interfaz M14 recibe `AuthorityBundleBytes`, no paths. En producción y en cada invocación de `bridge_status()` posterior a `await runtime.bridge_status_payload()`, el adapter M22 solicita de nuevo únicamente estos cinco literales repo-relative, sin raíz configurable ni fallback: `reports/2026-08-30-inbox-implementation/M23/dayz-effective-schema-v1.json`, `reports/2026-08-30-inbox-implementation/M23/tool-registry-fingerprint-v1.sha256`, `reports/2026-08-30-inbox-implementation/M23/effective-schema-v5-verdict.json`, `reports/2026-08-30-inbox-implementation/M23/effective-schema-producers-v1.json` y `reports/2026-08-30-inbox-implementation/M23/m23-receipts.jsonl`. Rechaza antes de pasar bytes cualquier sibling `*.candidate.<txid>`, `*.backup.<txid>.<sha12>`, journal, snapshot histórico, alias o reparse. Una lectura exitosa previa no acredita una posterior: no hay cache de bytes ni de `AuthoritySnapshot`. M14 sólo prueba los bytes que recibe y no puede distinguir un sibling byte-idéntico por contenido.

`[DESIGN]` Sólo en los tests OWNS M22, la operación privada de lectura queda detrás de una frontera inyectada de sólo lectura. El doble se programa por ciclos de invocación con respuestas independientes indexadas por esos cinco paths literales y, para cada solicitud, devuelve bytes o `None` junto con una clasificación independiente: archivo regular sin reparse, alias/reparse, ausente o ilegible. Registra por separado los paths solicitados en cada ciclo, rechaza cualquier sexto path y no puede remapear, resolver, crear, escribir ni borrar nada. Cada ciclo positivo exige una solicitud nueva de cada uno de los cinco literales y entrega en memoria un `AuthorityBundleBytes` coherente; un test de dos invocaciones consecutivas cambia las cinco respuestas de autoridad X a autoridad Y entre ciclos sin reconstruir la app. Los negativos prueban que candidate, backup, journal, snapshot histórico, alias, reparse o sustitución no se aceptan; ausencia o ilegibilidad canónica en cualquier ciclo produce el veredicto INCONCLUSIVE y authority `unknown` para esa invocación. Así M22 prueba PASS/FAIL/INCONCLUSIVE y la cadencia de relectura sin leer ni modificar el árbol físico M23. M23 conserva en exclusiva la acreditación sobre los cinco ficheros reales, su reapertura y sus OWNS.

## Adaptadores y ownership

- `[DESIGN]` **M14 unitario** escribe únicamente `tools/dayz_mcp/tool_registry_fingerprint.py` y `tools/tests/test_tool_registry_fingerprint.py`, según `plans/inbox-20260830/physical-ownership-addendum-v1.md:63`. Es dueño de la proyección/serializador A, snapshot inmutable, parser puro de `AuthorityBundleBytes` y comparador. No abre paths, no importa `server.py`, no crea apps y no espera artefactos M22/M23 para quedar GREEN.
- `[DESIGN]` **M22 integración** es el único writer de `server.py` y de sus tests (`plans/inbox-20260830/physical-ownership-addendum-v1.md:72`). Dentro de `build_app`, tras todas las altas/wrappers M22 y justo antes del `return`, lee el registro final una vez, hace el join total de metadata y pasa a M14 únicamente los mappings completos de cinco campos congelados arriba. En modo client pasa exclusivamente el session_id que alimentó `ControlIdentity` y la pareja M13 del mismo config; en embedded/daemon pasa identidad no acreditable y obtiene snapshot local `unknown`. Sólo el snapshot local se congela en `build_app`: no se lee ni se conserva allí la autoridad. En cada llamada, `bridge_status()` espera primero `payload = await runtime.bridge_status_payload()`, solicita y parsea de nuevo la autoridad mediante el adapter M22 y M14, la compara con el snapshot local y luego añade a esa copia las cuatro claves según la tabla cerrada.
- `[DESIGN]` **M22 adapter de autoridad** se invoca una vez por cada `bridge_status()` y, después del payload remoto, solicita de nuevo sólo los cinco paths literales M23 y entrega los bytes de esa invocación a M14. Nunca cachea bytes ni `AuthoritySnapshot` entre llamadas. En tests M22, la frontera de lectura simulada anterior acredita selección/rechazo, relectura X→Y y los tres veredictos sin depender del árbol físico; no forma parte de la API de M14 ni convierte los paths en configuración productiva.
- `[DESIGN]` **M23 promoción** produce/promueve los cinco canónicos y posee `tools/promote_effective_schema.py`, `tools/tests/test_effective_schema_promotion.py` y los artefactos M23 (`plans/inbox-20260830/physical-ownership-addendum-v1.md:73-74`). Reutiliza el serializador M14 para los cuatro digests; no reimplementa el algoritmo ni modifica fixtures M13. Sus tests acreditan sobre el filesystem real shape, banco, producers, receipts, paths, reapertura y transacción.
- `[DESIGN]` M22/M23 no pasan PID, PPID, daemon generation, `app` propia, `/status` ni `status_provider` al fingerprint; esas rutas quedan read-only y sin `tool_registry_*` (`server.py:3828-3838`, `daemon.py:624-672`, `loopback.py:2908-2922`).

La secuencia de gates respeta `M13→M14→M22→M23`: `M14-UNIT-GREEN` habilita M22; `M22-INTEGRATION-GREEN` habilita M23; `M23-AUTHORITY-GREEN` acredita los canónicos finales. Ningún gate anterior depende de que exista uno posterior.

## Viability tests discriminantes

Cada expected vive en el test del owner que observa el comportamiento y procede de un literal/fixture congelado independiente; no se calcula con el helper bajo prueba, `app.list_tools()`, schema wire/candidate ni un sidecar generado por el propio test.

### Gate `M14-UNIT-GREEN` — `tools/tests/test_tool_registry_fingerprint.py`

1. `[DESIGN][PASS]` **2×2 positivo literal**: cuatro entradas mínimas producen fingerprints literales y snapshots known para las cuatro parejas; el test no deriva las parejas del output.
2. `[DESIGN][PASS]` **Misma pareja, sesiones distintas**: X/Y conservan binding separado; autoridad Y deja X `stale` e Y `fresh`, mientras cambiar sólo session_id no cambia bytes/digest.
3. `[DESIGN][FAIL]` **Identidad/cross-pair**: session/profile/role ausente, pareja parcial, embedded/daemon representado sin `ControlIdentity.session_id`, o cruce de pareja da snapshot/comparación `unknown`; PID/generation no lo repara. Un timestamp vacío falla, pero no se añade mutante UUID/ISO ajeno al contrato.
4. `[DESIGN][PASS+FAIL]` **Proyección y bytes**: keyset exacto de cinco campos antes de `_tool_record`; omitir por turno cada campo —incluido `public_constraints`—, añadir un campo extra, pasar la salida de tres campos de `extract_tool_records()` o conservar las cinco claves con `description=None` da snapshot unknown, nunca defaults ni `""`. Después se prueban los restantes tipos/enum, mutación de cada campo, reordenación registry, NFC equivalente, colisiones post-NFC de names/constraints/keys, NaN/Infinity, claves no string, BOM/CRLF/newline y UTF-8 inválido. Los positivos comparan bytes y SHA literales externos; cada mutante real cambia digest o queda unknown.
5. `[DESIGN][PASS+FAIL]` **Cinco blobs en memoria**: un bundle literal completo queda known. Por separado fallan bytes JSON no canónicos, manifest fingerprint con orden/espacios/LF incorrectos, digest por pareja discordante, hash marker→sidecar, txid, verdict distinto de PASS, fixture/hash/ID/result omitido, producer requerido/validator source/receipt activo omitido, receipt schema hash no calculado sobre marker bytes, match duplicado o UTF-8/JSONL corrupto. Todos dan `AuthoritySnapshot.status="unknown"`.
6. `[DESIGN][PASS+FAIL]` **Comparador cerrado**: same-pair igual=`fresh`, same-pair distinto=`stale`, local/authority/cross-pair inválido=`unknown`; un candidato que copia sólo el digest sin pasar el parser completo queda rojo.
7. `[DESIGN][INCONCLUSIVE]` **Setup no acreditable**: cualquier blob ausente/ilegible se representa como `None` y produce authority/comparación `unknown`, sin excepción convertida en PASS ni inferencia de frescura. Este caso acredita la salida INCONCLUSIVE del módulo; no espera que M23 exista para poner M14 GREEN.

### Gate `M22-INTEGRATION-GREEN` — tests OWNS M22

1. `[DESIGN][PASS+FAIL+INCONCLUSIVE]` **Seam, perfiles y join únicos**: dos positivos limpios, sin wrapper sentinel ni patch de metadata, construyen por separado `standard` y `exec_enforce`, exigen respectivamente base exacta y base más la única entrada literal `exec_enforce`, y comparan en cada caso igualdad exacta de nombres con el inventory M13 read-only; invertir la selección o filtrar por registry queda rojo. Un tercer positivo independiente usa exactamente `ServerConfig(mode="client", enable_exec_enforce=False, client_platform="claude")`: el wrapper test-only del alias registra la sentinel literal y el patch simultáneo de `_TOOL_REGISTRY_METADATA_BASE` aporta por separado su entrada literal después del alias real y antes del seam final. Este tercer caso no compara igualdad de conjunto con M13; acredita sólo que el seam observa una mutación posterior al alias y que el join exige metadata independiente para esa tool. Fuera de ese contexto la sentinel no existe, la base productiva se restaura y el inventory M13 permanece intacto. El snapshot `standard|claude`, ligado al `runtime.identity.session_id` ya entregado a `ControlIdentity`, se toma dentro de `build_app` justo antes del return, incluye la sentinel con los cinco campos y casa record/bytes/digest literales externos sin llamada al daemon. Capturar antes de la sentinel, en el llamador post-return, mediante `await app.list_tools()`, desde objetos FastMCP crudos o desde `extract_tool_records()` queda rojo. El test sustituye por turno `name`, `description` o `parameters` por ausente/tipo inválido —incluido `description=None`—, elimina y altera cada metadata, añade una clave sobrante y fuerza duplicado/ambigüedad: observado pero inválido produce snapshot `unknown` y nunca default; manager/tabla no observable por setup da INCONCLUSIVE.
2. `[DESIGN][PASS]` **Tabla overlay completa y autoridad viva**: tras `await runtime.bridge_status_payload()`, fixtures de las cuatro filas asertan literalmente fingerprint/captured_at/source_stale y remediation. Dos procesos client del mismo daemon conservan snapshots/session_id distintos. Además, sobre una sola app/snapshot local X, dos llamadas consecutivas reciben el mismo payload remoto: el primer ciclo de cinco lecturas entrega autoridad X y publica `source_stale=false`; sin reconstruir app/runtime, el segundo ciclo entrega una autoridad Y válida de la misma pareja y digest distinto y publica `source_stale=true`, conservando fingerprint y captured_at locales. Reutilizar los bytes o el `AuthoritySnapshot` del primer ciclo, leer antes del payload remoto o no observar X→Y queda rojo.
3. `[DESIGN][FAIL]` **No daemon `/status`**: `daemon.make_status_provider()` y loopback `/status` carecen de los cuatro campos; mover o compartir allí el overlay queda rojo.
4. `[DESIGN][FAIL]` **Provenance de identidad**: client usa exclusivamente el session_id que alimentó `ControlIdentity` más profile/role M13; embedded/daemon y cualquier sustituto PID/app/generation quedan local unknown con fingerprint/captured_at/source_stale null.
5. `[DESIGN][PASS+FAIL]` **Paths canónicos mediante I/O simulada**: en cada una de dos llamadas consecutivas, el spy de sólo lectura recibe exactamente una solicitud nueva por cada uno de los cinco paths literales, ninguna otra; el trace queda particionado en dos ciclos de cinco y los bytes independientes de cada ciclo construyen su propio `AuthorityBundleBytes`. Los ciclos X→Y acreditan que la segunda clasificación usa la segunda lectura. Mutantes que cachean la primera lectura/authority o intentan candidate, backup, journal, alias, reparse, snapshot histórico, remapeo o fallback —aunque el doble ofrezca bytes idénticos— quedan rojos sin tocar el árbol físico M23.
6. `[DESIGN][INCONCLUSIVE]` **Autoridad/overlay no observable**: la frontera simulada devuelve ausente/ilegible para un canónico en un ciclo posterior y esa llamada produce authority `unknown`, conservando fingerprint+captured_at locales y publicando source_stale null aunque el ciclo anterior fuese válido; si no puede observarse el registro/metadata o la respuesta posterior al payload remoto, el gate es INCONCLUSIVE, nunca PASS por cache previa, inspección de `/status` ni existencia física de M23.

### Gate `M23-AUTHORITY-GREEN` — `tools/tests/test_effective_schema_promotion.py`

1. `[DESIGN][PASS]` **Publicación íntegra**: banco independiente completo PASS, cuatro payloads y manifest fingerprint exactos, unión producers completa, cinco hashes/enlaces/txid y receipt commit sobre SHA-256 de marker bytes; el marker se publica último.
2. `[DESIGN][FAIL]` **Mutantes no tautológicos**: omitir por turno fixture/ID/result/producer, marcar PASS sin ejecutar caso, usar wire↔candidate como expected, alterar cada sidecar tras verdict, duplicar commit o seleccionar sibling produce cero `os.replace`.
3. `[DESIGN][PASS+FAIL]` **Primera publicación, legacy y rollback**: ausencia pre-marker es válida pero no autoridad known; tras marker receipts existe. El prefijo legacy se preserva byte a byte; rollback conserva receipts y sólo el commit histórico único puede acreditar el marker restaurado.
4. `[DESIGN][INCONCLUSIVE]` **Setup/transacción no acreditable**: lock ajeno, journal no reconciliable, producer/fixture/preimagen drift o fallo de reapertura aborta antes del primer replace, o recovery queda INCONCLUSIVE; nunca se fabrica un conjunto known parcial.

## Decisión y criterio de cierre

`[DESIGN]` Queda seleccionada Opción A para la versión 5/1: JSON único, binding separado, checksum manifest 2×2 y parser de los cinco blobs. Opción B no es fallback ni formato aceptado.

- `M14-UNIT-GREEN`: las firmas se implementan sólo en los dos OWNS M14 y pasan sus tests puros con expected externos. No requiere `server.py`, paths ni artefactos M23 y habilita M22.
- `M22-INTEGRATION-GREEN`: acredita seam pre-return, join total de cinco campos, provenance de identidad, tabla overlay post-payload, ausencia en `/status` y selección literal mediante la frontera simulada de sólo lectura; no espera ni escribe artefactos M23 y habilita M23.
- `M23-AUTHORITY-GREEN`: acredita sobre los paths físicos reales banco PASS completo, bytes/shape/enlaces de los cinco blobs, producers, receipts, reapertura y transacción; habilita el cierre P11/M25.

Los tres gates pueden devolver INCONCLUSIVE sólo por sus condiciones de setup explícitas y nunca cuentan como PASS. Dentro de S-14 no queda un formato, campo o política de normalización alternativo por decidir; cambiar el checksum manifest, shapes JSON, fixture bank o versión exige nueva mini-spec y revisión antes de materializar.
