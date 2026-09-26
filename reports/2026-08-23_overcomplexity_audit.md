# Auditoría de sobrecomplejidad / sobreingeniería — DayZ_MCP_dev

**Fecha:** 2026-08-23 · **Ámbito:** `tools/dayz_mcp` (37.074 L), `tools/tests` (66.805 L), scripts y directorios de `tools/`, `publish/`.
**Método:** 3 auditores paralelos por ángulo (launcher nativo, stack sesión/daemon, superficie MCP + tooling raíz) + verificación adversarial de cada cita por el auditor principal (sed/grep contra el árbol real). Nada fue modificado.
**Relación con la auditoría general de ayer** (`reports/2026-08-22_full_audit.md`): no se re-reportan sus hallazgos (A1–A16, B1–B9, C1–C9, D1–D6, E1–E4). Donde esta auditoría profundiza sobre uno de ellos, se indica explícitamente.

---

## Resumen ejecutivo

El paquete está **sobredimensionado en tres ejes concretos**, no de manera difusa:

1. **El camino de un lanzamiento** cruza 11 frames Python + 1 hilo, con el mismo documento JSON serializado 1 vez y parseado 2 veces en el mismo proceso, y el exe/bundle hasheado ≥6 veces (trazado ~9). Ninguna de las repeticiones cierra una amenaza que la anterior no cerrara: el handle se abre `FILE_SHARE_READ` desde el primer hash.
2. **El estado de "quién tiene la sesión" vive en ≥6 representaciones** en disco/memoria, protegidas por ≥17 mecanismos de exclusión, con la identidad del daemon re-verificada ~5 veces por request HTTP. Gran parte es endurecimiento fail-closed deliberado y está comentado; lo que falta es la **consolidación y la retirada** de lo que ya hizo su trabajo.
3. **La superficie MCP y el tooling acumulan fases terminadas**: 40 de 54 handlers son dos patrones copiados; `mcp_server_step0.py` y `gate4a_mcp_client.py` son fases muertas de junio; un test productivo importa desde un directorio snapshot (`_broker/`); 12 archivos de veredicto huérfanos.

Un cuarto eje transversal: **piezas que no tienen vía de retirada** — la migración one-shot de identidad se re-valida íntegra en cada arranque del daemon para siempre, y el gate de seguridad AST de 1.751 líneas solo corre si alguien corre sus tests.

No encontré sobreingeniería *malintencionada* ni capas vacías masivas: el estilo es consistente (fail-closed, cita por línea). El problema es que **nadie borra**: cada riesgo cerrado añadió una capa permanente.

---

## Tabla consolidada

| ID | Sev | Título | Evidencia | Tipo | Estado |
|----|-----|--------|-----------|------|--------|
| OC-1 | P2 | Request JSON parseado 2× en el mismo proceso | `dayz_test_tool.py:149-157`, `native_launcher_transaction.py:108` | MEJORA | VERIFICADO |
| OC-2 | P2 | ≥6 pases de hash del exe/bundle por lanzamiento | `launcher_registry.py:332`, `dayz_test_tool.py:576`, `native_launcher_backend.py:564`, `native_bundle.py:611,638-661,829` | MEJORA | VERIFICADO (recuento ~9 trazado, no instrumentado) |
| OC-3 | P2 | 18-20 parámetros reenviados por 3 firmas consecutivas | `server.py:2504`, `dayz_test_tool.py:537`, `:94` | MEJORA | VERIFICADO |
| OC-4 | P2 | Validación semántica del request corre 2-4× por lanzamiento | `dayz_test_request.py:282-320` ×2 (via OC-1) + `dayz_test_tool.py:118-122` | MEJORA | VERIFICADO |
| OC-5 | P3 | Wrappers pass-through de una línea | `secure_launcher.py:216-223`, `dayz_test_tool.py:76-79`, `native_bundle.py:106-107`, `launcher_registry_update.py:126-127` | MEJORA | VERIFICADO |
| OC-6 | P3 | Firma especulativa: params pasados al consumer y borrados con `del` | `native_launcher_transaction.py:153-162` vs `secure_launcher.py:98-99` | MEJORA | VERIFICADO |
| OC-7 | P3 | Sistema transaccional completo para exactamente 1 launcher | `launcher_registry_update.py` (564 L; `dayz-test-v1` único id) | MEJORA / POTENCIAL | VERIFICADO (si se esperan más launchers, justificado) |
| OC-8 | P3 | `rolled-back.json` escrito con payload que nadie lee | `launcher_registry_update.py:520` escribe; `:339`,`:488` solo `.exists()` | MEJORA | VERIFICADO |
| OC-9 | P3 | Protocolos versionados con una sola versión | `native_broker_protocol.py:18`, `native_child_announcement.py:15`, `native_bundle.py:795` | MEJORA | VERIFICADO |
| OC-10 | P3 | Campos de debug solo consumidos por tests | `native_debug_state.py:61-79` (`failed`, `open_handle_count`, `continue_count`) | MEJORA | VERIFICADO |
| OC-11 | P2 | Helpers canónicos duplicados: `_valid_uuid4` ×5, `_reject_duplicate_pairs` ×6, validador de ruta Windows ×4 | defs en `dayz_test_tool.py:227`, `dayz_test_readiness.py:54`, `dayz_test_request.py:78`, `process_lifecycle.py:42`, `runtime_state.py:689` | MEJORA | VERIFICADO (corregido: ×5, no ×7) |
| OC-12 | P3 | Default author-absoluto al árbol equivocado | `task9_build_a_smoke.py:52-55` → `C:\Users\guill\OneDrive\...` | DEFECTO (config) | VERIFICADO (documentado en `publish/boundary.py:217` como conocido) |
| OC-13 | P2 | Auditoría JSONL: append implementado como read-all + rewrite atómico por evento — O(n) hasta 5 MB por evento | `runtime_state.py:340-349` | DEFECTO (degradación) | VERIFICADO |
| OC-14 | P2 | "Quién tiene la sesión" en ≥6 representaciones con writers/readers propios | ver detalle §B.1 | MEJORA | VERIFICADO |
| OC-15 | P2 | Grant en vacío: ~12 hops, Condition 6×, file lock del fault-store 4×, fence 5× | `session_coordination.py:3332-3356` (4× `_wal_finish_fence_matches_locked`), `runtime_state.py:365-415` | MEJORA (en parte estilo defensivo deliberado) | VERIFICADO |
| OC-16 | P2 | Migración one-shot sin retirada: cada boot relee y re-hashea el backup completo | `identity_migration.py:1104-1109` → `_validate_receipt` → `_read_file` `:828-836` | MEJORA | VERIFICADO |
| OC-17 | P3 | Camino muerto `build_server_state(activate_coordination=True)` | `daemon.py:352-357`; único caller `:867-871` pasa `False` | MEJORA | VERIFICADO |
| OC-18 | P2 | Identidad del daemon verificada ~5× por request HTTP | `daemon_credential.py:69-79` (9-tuple) + `:122-143` (assert→revalidate→assert); `accredited_daemon_transport.py:143-201` (snapshot_a + snapshot_b) | MEJORA | VERIFICADO |
| OC-19 | P3 | ≥17 mecanismos de exclusión; triple serialización in-proceso del fault-store | `session_coordination.py:232`, `runtime_state.py:102,421`, msvcrt `:394`, etc. | MEJORA | VERIFICADO |
| OC-20 | P3 | Re-chequeos de tombstone/identity dentro de un mismo acquire | `session_coordination.py:293,441,443,484,572`; `:284,635,678,711` | MEJORA (parte es patrón correcto post-`wait`) | VERIFICADO |
| OC-21 | P2 | Backups sin poda: `runs.json.bak-preprune` 10 slots que nunca se borran + checkpoint inmutable por contenido distinto en cada persist | `process_lifecycle.py:482-486` (creación, sin delete), `:578-585` | MEJORA / POTENCIAL | VERIFICADO el código; NO medido el crecimiento real del árbol |
| OC-22 | P2 | Doble modelo de ocupación del box sin reconciliación | `process_lifecycle.py:2436` vs `session_coordination` `_box_claiming`/`_box_queue` | POTENCIAL / NO CONFIRMADO | no se verificó divergencia en runtime |
| OC-23 | P2 | 40/54 handlers MCP son dos patrones copiados; ~700-900 L eliminables con un decorator | 33× `return await runtime.call_bridge(` + 7× `return await client.`; ejemplos `server.py:3007-3019`, `:3070-3080`, `:3120-3131` | MEJORA | VERIFICADO (profundiza A5 de la auditoría previa) |
| OC-24 | P2 | Gate de seguridad AST de 1.751 L sin entrypoint: solo corre si alguien corre sus tests | `security_runtime_audit.py` sin `__main__`/`def main`/argparse; único importador `tests/test_security_runtime_audit.py` | MEJORA | VERIFICADO |
| OC-25 | P3 | Entrypoints de fases muertas conviviendo | `mcp_server_step0.py` (fake poll, junio), `gate4a_mcp_client.py` (fase 4A), `mcp_server.py` (shim solo de `run-fase*.ps1`) | MEJORA | VERIFICADO |
| OC-26 | P3 | `boundary.py` contradictorio: 2 archivos en TOOLS_IN y TOOLS_OUT a la vez; filas OUT inalcanzables | `publish/boundary.py:111-112` vs `:147-148`; orden `:156-159` | DEFECTO (config) | VERIFICADO |
| OC-27 | P3 | 12 archivos de veredicto/evidencia huérfanos en `tools/` | solo `run-fase1.ps1:359,560` relee el suyo; ningún `.py` los consume | MEJORA | VERIFICADO (profundiza D4 previa con análisis de lectores) |
| OC-28 | P3 | Snapshots muertos en `tools/` + un test productivo importando desde un dir snapshot | `_restore/2026-08-03-*/` (copia del paquete, 0 refs), `_session_coordination/` (5.591 L de gates h8); PERO `_broker/` VIVO via `tests/test_session_e2e.py:22` | MEJORA | VERIFICADO |
| OC-29 | P3 | Motor de courses al servicio de un único curso | `vehicle_trace.py:15` `CONTROL_COURSE_ID = "civilian-sedan-control-v1"` | MEJORA / POTENCIAL | VERIFICADO (si hay cursos futuros planeados, justificado) |
| OC-30 | P3 | Monolitos no cubiertos por la auditoría previa (A5/A6 cubrían server/daemon) | `loopback.py:262` validate_command_args 389 L; `session_coordination.py:265` acquire 431 L, `:2467` 262 L; `process_lifecycle.py:1162` 276 L, `:1599` 254 L; `native_launcher_backend.py:1182` 350 L; `identity_migration.py:1255` 174 L; `loopback.py:1621` record_poll 212 L, `:1152` enqueue_command 145 L | MEJORA | VERIFICADO (medido con AST) |

**Refutado durante la verificación** (se reporta por transparencia): la claim "campo muerto `auto_spawn_daemon`" es FALSA — `server.py:1036-1040` lo lee (`_daemon_missing_error`). Un auditor lo reportó; el grep de verificación lo tiró.

---

## A. Subsistema de lanzamiento nativo

### OC-1 + OC-4 — El mismo request, parseado dos veces en el mismo proceso

`dayz_test_tool.py:149-157` construye el documento, lo serializa y lo parsea de vuelta para validar:

```python
raw = json.dumps(document, ensure_ascii=False, allow_nan=False,
                 sort_keys=True, separators=(",", ":")).encode("utf-8")
try:
    parsed = dayz_test_request.parse_dayz_test_request(
        raw, policies=_semantic_policies(sealed_policies))
```

Ese `parsed.canonical_bytes` viaja a `native_launcher_transaction.py:108`, que **vuelve a ejecutar `parse_dayz_test_request` sobre los mismos bytes** (`native_launcher_transaction.py:108`). Consecuencia: `dayz_test_request.py:282-320` (validación semántica de puerto/mission/mods) corre dos veces por lanzamiento, más los pre-chequeos de mission/mods en `dayz_test_tool.py:118-122` (tercer y cuarto pase).

**Cambio propuesto:** que `execute_secure_launcher_request` acepte el objeto `parsed` ya validado (tipado, inmutable) en lugar de re-parsear bytes. Mantener el parse completo únicamente en las fronteras de proceso (worker nativo), donde sí pertenece. Elimina OC-1 y media OC-4 sin tocar el modelo sellado.

### OC-2 — El exe se hashea una y otra vez

Trazado de un lanzamiento (sitios verificados): `launcher_registry.py:332` (hash al abrir el registro) → `dayz_test_tool.py:576` `validate_native_pe()` (hash + parse PE) → `native_bundle.py:611` y `:638-661` (hash de **todos** los entries del bundle, incl. python.exe y DLLs) → `native_bundle.py:829` (revalidate al abrir) → `native_launcher_backend.py:564` (validate_native_pe otra vez justo antes de CreateProcessW). Con los checks de marker (`:118`/`:144`) el recuento trazado llega a ~9 pases completos.

El comentario del propio código desactiva la justificación de seguridad: el handle se abre con `_FILE_SHARE_READ` (`launcher_registry.py:184-191`), así que desde el primer hash ningún writer puede modificar el contenido en vivo — los 8 pases restantes no cubren un adversario nuevo. La identidad por `st_dev/st_ino` del handle ya detecta sustitución de archivo.

**Cambio propuesto:** cachear el digest verificado por `(path, st_dev, st_ino, st_mtime_ns, st_size)` — invalidación barata y sin ventana nueva (un cambio de mtime/size con mismo ino re-hashea). El bundle completo se re-verifica **al abrir** (parte del modelo sellado, justificado); lo redundante es repetirla íntegra en cada launch: cachear por `(bundle_id, manifest_sha256)` dentro del proceso.

### OC-3 — Reenvío de 18-20 parámetros por 3 firmas

`server.py:2504` `dayz_test_run(...)` firma 20 args; `dayz_test_tool.py:537` `execute_dayz_test_run` los recibe y los reenvía casi 1:1 a `build_run_request` (`:94`). **Cambio:** construir un `@dataclass(frozen=True) RunRequest` en la frontera MCP y pasarlo; los 3 saltos pasan a 1 objeto.

### OC-5/6/8/9/10 — Micro-cargo del subsistema

- Doble wrapper literal (`secure_launcher.py:216-223`): `_load_verified_bundle` importa y delega; `load_verified_bundle` delega en el privado. Borrar uno.
- `accredited_paths`/`heartbeat_supervisor` viajan hasta el consumer que hace `del accredited_paths, heartbeat_supervisor` (`secure_launcher.py:98-99`): firma genérica sin segundo consumer. Borrar del protocolo.
- `rolled-back.json` (`launcher_registry_update.py:520`) escribe un receipt completo que jamás se lee (solo `.exists()` en `:339`/`:488`): o se lee al decidir rollback, o se escribe un marker vacío.
- `_VERSION = 1` comprobado en ambos extremos (`native_broker_protocol.py:18,228`; `native_child_announcement.py:15`): correcto mantener el campo, pero documentar que solo existe para evolución futura — no es defecto, es generalidad declarada.
- `NativeDebugState.failed/open_handle_count/continue_count` (`native_debug_state.py:61-79`): solo `tests/test_native_debug_state.py` los lee. Mover a fixtures de test o borrar.

### OC-7 — Transacciones completas para un registro de un elemento

`launcher_registry_update.py` (564 L) implementa prepared/committed/from-registry + CAS + rollback + baseline shipped — para un registro cuyo único id admitido en todo el árbol es `dayz-test-v1`. **[POTENCIAL]**: si el roadmap contempla más launchers (p.ej. el heli que ya tuvo un rollback real — `native-launchers/dayz-test-v1.lfheli-rolled-back-*`), la maquinaria está justificada y solo falta decirlo en el docstring del módulo. Si no, es el ejemplo canónico de infraestructura de más.

### OC-11 — Helpers duplicados

`def _valid_uuid4` existe en 5 módulos del paquete (citas en tabla). Ídem `_reject_duplicate_pairs` ×6 y el validador de ruta Windows ×4. La duplicación no es inocua: la auditoría de ayer (A10) ya encontró **divergencia** en una de estas copias. **Cambio:** un único `dayz_mcp/canonical.py` (o ampliar `core.py`) con estas tres familias; los módulos que hoy usan el import privado cruzado (`dayz_test_tool.py:76-79` importa `_valid_mod_entry` de `dayz_test_request`) pasan a import público.

---

## B. Stack de sesión / daemon

### B.1 Inventario de estado (OC-14) — el hallazgo estructural del eje

En disco (`%LOCALAPPDATA%\DayZ_MCP`): `coordination.json` (snapshot), `coordination-fault.json` (WAL de grant/release) + su `.lock`, cuarentenas `coordination.json.corrupt.<sha>`, `audit/events.jsonl` + 5 rotaciones, `runs.json`, `runs.json.bak-preprune[.2-.10]`, `lifecycle-recovery-active.json` (puntero CAS), `lifecycle-recovery-faults/<id>/{fault,events,receipts}` (cadena hash por evento), `lifecycle-recovery-faults/backups/<sha>/` (directorio inmutable **por cada contenido distinto de runs.json**, escrito en cada persist — `process_lifecycle.py:578-585`), `lifecycle-manifest-checkpoint.json`, `migration/P0S-IDENTITY-V2/...`, `.daemon-startup.lock`, `<profiles>/dayz_mcp.json`, keyfile.

El hecho "el cliente X posee la sesión" está representado en ≥6 formas: `_Lease` en memoria, snapshot `coordination.json` (active/releasing/granting/queue), eventos JSONL, WAL durante transición, `runs.json.owner_session_id/owner_lease_id`, y `_box_claiming`. No es que cada una sea innecesaria; es que **no existe un documento que diga cuál es la fuente de verdad y cuáles son vistas** — la reconciliación vive implícita en guards `<=` (`runtime_state.py:1203,1275`).

**Cambio propuesto (documental antes que de código):** un diagrama/hoja de "artefactos de estado: writer, reader, fuente de verdad, prune" como doc viva junto al código (la auditoría de ayer C8 ya señaló que la doc de este dominio vive fuera del repo). Con ese mapa, decidir consolidaciones: p.ej. si `events.jsonl` es trail-only, dejar de reescribirlo atómicamente (OC-13).

### OC-13 — El append que no es append

`runtime_state.py:340-349`:

```python
previous = ""
if self.current_path.exists():
    previous = self.current_path.read_text(encoding="utf-8")
_atomic_write_text(self.current_path, previous + line)
```

Cada evento de auditoría lee el JSONL completo y lo reescribe atómicamente. Con `AUDIT_MAX_BYTES` de tope (rotación a 5 archivos), el coste por evento es O(tamaño del archivo) con fsync incluido, y crece linealmente hasta rotar. Para un trail de auditoría append-only esto es degradación sin beneficio: JSONL no necesita replace-atómico por línea; necesita append + fsync.

**Cambio propuesto:** `open(path, "a", ...)` + `write` + `flush` + `os.fsync` por evento; mantener `_atomic_write_text` solo para el rename de rotación. Alternativa mínima si se quiere preservar la atomicidad de línea en Windows: mantener el diseño pero mover el append a un buffer con flush por lotes acotado — peor, no recomendado. La opción simple (append real) cubre el 100% del requisito.

### OC-15 — Ceremonia de un grant (parcialmente justificada)

Un grant en vacío: `acquire` → `_new_lease_locked` → `_write_initial_grant_audits_locked` → `_arm_wal_locked` → 2×`_audit` → `_finish_wal_after_publish_locked` → 2×`_transition_wal_locked` + `_persist_snapshot_locked` + `_clear_wal_locked` — ~12 hops; el `Condition` se suelta/retoma ≥6 veces; el file lock msvcrt del fault-store se abre/toma/cierra 4 veces (cada operación reabre el `.lock`, 2×fstat, fsync — `runtime_state.py:365-415`); `_wal_finish_fence_matches_locked` se evalúa 4 veces en una sola llamada (`session_coordination.py:3340,3344,3350,3353`).

Matiz importante (verificado leyendo el código): los re-chequeos tras cada paso del WAL son **estilo deliberado verify-after-each-step** — comprobar que ningún paso mutó la coordinación en vez de demostrar que no puede. Eso es fail-closed, no bug. Lo cuestionable es el **coste de la ceremonia** (4 lockfiles, 6 relinquishes) cuando el estilo podría lograr lo mismo con un solo arco de lock y verificaciones sobre memoria. No propongo tocarlo a la ligera — propongo medirlo: si un grant en vacío tarda <X ms, es ruido; si no, simplificar el arco de locks primero, no las verificaciones.

### OC-16 — La migración que nunca se retira

En **cada arranque** del daemon, con la migración ya settled desde hace semanas: `daemon_startup_election` (lock msvcrt) → `_ensure_identity_migration` → `ensure_runs_v1_backup` → `_exclusive_gate_lock` (segundo lock msvcrt, `identity_migration.py:405-456`) → `_settled_receipt` (`:1104-1109`) → `_validate_receipt` → **lectura completa + hash de `runs.pre-v2.json`** (`:828-836`). Para siempre, sin constante de retirada.

El propio código reconoce el patrón de riesgo en su comentario (`identity_migration.py:1304-1312`: "Once the receipt is published ... there is nothing left to write"). Falta la consecuencia lógica: si no queda nada que escribir, tampoco queda nada que re-hashear cada boot.

**Cambio propuesto:** (a) cuando el receipt está settled, validar por stat (size + mtime) en vez de re-leer+re-hashear; y (b) añadir al receipt un campo `retire_after` (fecha o versión) tras el cual `_ensure_identity_migration` se convierte en no-op con un solo stat. El archivo queda como evidencia; el coste de arranque desaparece.

### OC-18 — La identidad verificada cinco veces por request

`daemon_credential.py:69-79` copia la policy a un 9-tuple `_authority`; `_revalidate_authority` (`:122-143`) ejecuta `_assert_authority_unchanged` → `policy.revalidate()` → `_assert_authority_unchanged` por request; y `accredited_daemon_transport.py:143-201` acredita la identidad del proceso **dos veces completas por HTTP** (pasadas A y B con `guard.snapshot` + `identity_hashes` — cada `identity_hashes` re-hashea el exe).

Cinco verificaciones de lo mismo dentro de un request, con el exe re-hasheado en cada pasada. La amenaza real (el daemon muere y su PID se recicla entre request A y B del mismo cliente) la cubre la acreditación por-conexión; re-hashear el binario no añade nada que `st_dev/st_ino` del snapshot no dé.

**Cambio propuesto:** acreditar una vez por request con cache de `identity_hashes` por `(pid, st_dev, st_ino, mtime_ns, size)` idéntica a OC-2; mantener el doble assert de `_revalidate_authority` solo si hay un test rojo que demuestre la ventana que cierra (regla del gate: un chequeo que no puede ponerse en rojo no es un chequeo).

### OC-19/20/21/22 — Estado y locks

- **OC-19**: coexisten ≥17 mecanismos de exclusión (tabla en §B.1 del informe de agente, consolidada arriba). La pila triple in-proceso del fault-store (`thread lock` + `_COORDINATION_FAULT_PROCESS_LOCK` module-global + msvcrt file lock — `runtime_state.py:102,421,394`) defiende un escenario multi-escritor que `daemon_startup_election` + bind exclusivo del puerto ya excluyen antes de que exista coordinación. Como capa multi-proceso está justificada; como triple serialización in-proceso es redundante. Simplificación candidata: fusionar el module-global con el lock de instancia (un solo dueño por proceso garantizado por la elección).
- **OC-20**: los re-chequeos de `_operation_tombstoned_locked` en `:293,441,443,484,572` son en parte patrón correcto (re-evaluar el predicado tras despertar de `Condition.wait`). El único micro-dupe injustificado es el adyacente `:441`/`:443` (evalúa dos veces seguidas para elegir la razón — calcular en variable). El resto: documentar la invariant "re-check tras cada wake" en un comentario del método y dejarlo.
- **OC-21 [POTENCIAL]**: los 10 slots `bak-preprune` no se borran nunca (grep: solo creación, `process_lifecycle.py:484-486`), y cada persist escribe un backup inmutable nuevo bajo `lifecycle-recovery-faults/backups/<sha>/` (`:578-585`). **No medí el árbol real** — si cada sha-distinto vive para siempre, el directorio crece sin cota con la actividad. Acción barata: medir `%LOCALAPPDATA%\DayZ_MCP\lifecycle-recovery-faults\backups` hoy y decidir política de poda (p.ej. conservar N últimos + el receipt de la última recuperación).
- **OC-22 [POTENCIAL]**: `ProcessLifecycle._box_occupancy_uncached` (`process_lifecycle.py:2436`) y `SessionCoordinator._box_claiming/_box_queue` modelan la ocupación del box por caminos distintos. No verifiqué si pueden divergir en runtime (requiere escenario multi-cliente). Si divergen, es el clásico doble-modelo-sin-reconciliación; si no, documentar cuál manda.

---

## C. Superficie MCP y tooling raíz

### OC-23 — Dos patrones copiados en 40 de 54 handlers

Recuento grep (verificado): 54 `@app.tool(`; 33 handlers terminan exactamente en `return await runtime.call_bridge(<verb>, args, "server", _timeout(timeout_s))`; 7 en `return await client.<método>(...)`. Ejemplo literal (`server.py:3007-3019`, verbatim):

```python
async def object_anim(type: str, pos: list[float], source: str, ...):
    if not isinstance(type, str) or type == "":
        raise ToolError("bad_args")
    ...
    args: dict[str, Any] = {"type": type, "pos": _require_vec3(pos, "pos"), "source": source}
    ...
    async with runtime.tool_lock:
        return await runtime.call_bridge("object_anim", args, "server", _timeout(timeout_s))
```

`inventory_give` (`:3070-3080`) y `entities_query` (`:3120-3131`) replican la misma estructura con otros args. Profundiza A5 (auditoría previa: "build_app 1488 L"): la monoliticidad no es solo longitud — es que **~74% del cuerpo es un template**.

**Cambio propuesto:** un decorator tipo

```python
@bridge_tool("object_anim", build_args=_object_anim_args)   # build_args: valida y monta el dict
```

que registre el handler con el tail estándar. Estimación conservadora: −700/−900 líneas y un único lugar donde cambiar el manejo de errores del bridge. Los 14 handlers bespoke (`dayz_test_run`, `wait_for`, `capture_screenshot`, `ui_*`…) se quedan como están — su complejidad es real (verificado: `execute_wait_for` 174 L con lógica de lookback propia).

### OC-24 — El gate de seguridad que solo vive en sus tests

`security_runtime_audit.py` (1.751 L + 2.011 L de tests, 57 tests) no tiene `if __name__ == "__main__"`, no tiene `def main(`, no tiene argparse (grep: 0 hits — verificado). Su único importador es su propio test file. El instalador corre `p0s_gate.py` (install_mcp.py → `run_runs_backup_gate`), no este módulo. **El gate de seguridad del árbol corre exactamente cuando alguien ejecuta la suite de ese módulo** — y no hay CI (D1 previa).

Agravante de circularidad (verificado): `publish/boundary.py:113-116` mantiene `run-fase1/2/3.ps1` y `run-poc.ps1` dentro del boundary de publicación **porque** "security_runtime_audit enumera estos cuatro como la superficie auditada" — la auditoría justifica la permanencia de sus propios inputs.

**Cambio propuesto:** (1) añadir `main()` + `__main__` que corra el audit y salga con código (o `python -m dayz_mcp.security_runtime_audit --check`); (2) moverlo a `tools/checks/` junto a los otros gates — ya propuesto en `plans/2026-08-21-hoja-de-ruta-presentacion.md:217`; (3) decidir la circularidad: si los run-fase*.ps1 son solo evidencia de la exclusión, mover la evidencia a docs y despublicar los scripts.

### OC-25/26/27/28 — Fases terminadas y su evidencia

- **Entrypoints**: `mcp_server_step0.py` (fake con `POLL_BATCH` hardcodeado — fase 0, 6-jun; `run-step0.ps1` es su único usuario), `gate4a_mcp_client.py` (fase 4A, escribe a `_fase4a/`), `mcp_server.py` (shim de 21 L que solo `run-fase*.ps1` lanza). El árbol productivo es `python -m dayz_mcp` (install_mcp.py:793-801 — verificado). Decidir: archivo muerto de fase (no borrar evidencia de gates superados — la regla del repo es conservar la tupla evidencia) → mover a `_archive/` gitignored junto a `_faseN/` (E1 previa) en la misma operación de limpieza, no sueltos en `tools/`.
- **OC-26**: `publish/boundary.py` lista `p0s_gate.py` y `p0s_test_runner.py` en TOOLS_IN (`:111-112`, con test propio) **y** en TOOLS_OUT (`:147-148`, "development phase gate/runner") — filas OUT inalcanzables porque `classify_tools_file` consulta IN primero (`:156-159`). Es un DEFECTO de config menor pero embarazoso en el archivo que define qué se publica: borrar las dos filas OUT stale.
- **OC-27**: los 12 veredictos/logs/PNGs de `tools/` raíz no tienen lectores `.py`; solo `run-fase1.ps1` relee el suyo dentro del mismo run. Mismo destino: `_archive/`.
- **OC-28**: `tools/_restore/2026-08-03-status-prune-prechange/` contiene una **copia del módulo del paquete** (`process_lifecycle.py` + test) con 0 referencias — quien grepee `def _valid_uuid4` encuentra 6 hits, uno de ellos falso porque es una copia congelada. `_session_coordination/` suma 5.591 L de gates h8/spikes muertos. **Excepción verificada**: `_broker/e2e_daemon.py` está VIVO — `tests/test_session_e2e.py:22` hace `from _broker import e2e_daemon as broker_e2e`. Un test productivo importando desde un directorio-snapshot es frágil (cualquier limpieza de `_broker/` rompe la suite): mover `e2e_daemon.py` a `tests/fixtures/` (o `tools/fixtures/`) y des-snapear el resto.

### OC-29 — Courses para un curso

`vehicle_trace.py` (1.128 L) mantiene un motor de courses con schema versionado, y el único curso del árbol es `civilian-sedan-control-v1` (`:15`). La parte de contrato pinneado multi-proceso está justificada (consumers: loopback, server, session_coordination). **[POTENCIAL]**: si el roadmap (SUB_BRZ heading-up, ver memoria de sesión) añade cursos, mantener; si no, congelar el motor y documentar que la extensión es intencional.

---

## Complejidad JUSTIFICADA (no tocar)

Verificado con cita; se lista para que una futura pasada de simplificación no la confunda con sobreingeniería:

- **Loop `WaitForDebugEvent`/`ContinueDebugEvent` con gate de imágenes y correlación job-completion-port** (`native_launcher_backend.py:1182-1531`, 350 L): exigido por la Windows debug API y el anti-spoofing del árbol de procesos; el comentario `:903-906` ("Resolved statically, NOT via getattr...") documenta el porqué.
- **`_IncrementalRedactor`** (`secure_launcher.py:27-58`): streaming con secretos partidos entre chunks — necesario para el progreso MCP en vivo.
- **`registry_lock` LockFileEx cross-process + handles pineados + identidad `st_dev/st_ino`**: defensa real contra junctions/hardlinks en Windows.
- **`_atomic_write_text` con identidad pineada + temp O_EXCL + fsync + replace verificado** (`runtime_state.py:1890-1948`) y **`_read_pinned_regular_file` triple-stat** (`:1795-1831`): multi-proceso real y TOCTOU en Windows.
- **Re-verificación del bundle AL ABRIR** (no en cada launch): parte del modelo sellado.
- **Los re-chequeos de predicados tras `Condition.wait`** (la parte legítima de OC-20): patrón estándar de concurrencia.
- **`dayz_test_run` y `wait_for` bespoke** en server.py: su complejidad es funcional, no template.
- **`_broker/e2e_daemon.py`** como binario de e2e (el problema es su ubicación, no su existencia).
- **`mcp_client.py`** *(CORREGIDO en adenda §D — ver OC-31)*: ~~harness legado con tests vivos~~ — el módulo **no existe**; fue retirado deliberadamente en el commit `a5f4bb9` ("Retire the phase 0-3 harness, and the security exemption holding it up"). La claim original provenía de un subagente y no fue verificada; ver adenda.

---

## Plan de cambios propuesto

Ordenado por riesgo descendente / esfuerzo ascendente. Nada aplicado — es propuesta.

### Tanda 1 — Barato, local, sin riesgo funcional (días)
1. **OC-26**: borrar las 2 filas stale de TOOLS_OUT en `publish/boundary.py:147-148`.
2. **OC-5**: eliminar los wrappers pass-through (4 sitios).
3. **OC-6**: sacar `accredited_paths`/`heartbeat_supervisor` de la firma del consumer.
4. **OC-11**: unificar `_valid_uuid4`, `_reject_duplicate_pairs`, validador de ruta en un módulo canónico (esto también desactiva la clase de bug A10-previa).
5. **OC-8**: `rolled-back.json` → marker sin payload (o leerlo de verdad).
6. **OC-12**: `DEFAULT_TOOLS_DIR` de task9 → resolver relativo al archivo (`Path(__file__).parent`).
7. **OC-17**: borrar la rama `activate_coordination=True` muerta de `build_server_state`.
8. **OC-10**: mover campos de debug de `native_debug_state` a los tests que los usan.

### Tanda 2 — Estructural de media envergadura (1-2 semanas, con tests)
9. **OC-23**: decorator `@bridge_tool` y migración de los 33+7 handlers template (~−800 L; mantener los 14 bespoke).
10. **OC-1/OC-3/OC-4**: `RunRequest` dataclass + parse único del request en frontera MCP (OC-1 y media OC-4 caen juntos).
11. **OC-2**: cache de digest por identidad de archivo (exe y bundle) — invalidación `(st_dev, st_ino, mtime_ns, size)`.
12. **OC-13**: append real + fsync en `JsonlAuditWriter` (atomic solo en rotación). Test: durabilidad de la última línea tras kill -9 simulado.
13. **OC-16**: stat-only en receipt settled + campo `retire_after` en el receipt de migración.
14. **OC-18**: acreditación única por request + cache de `identity_hashes` (misma invalidación que OC-2).

### Tanda 3 — Consolidación de estado (necesita diseño y decisión, no precipitarse)
15. **OC-14**: documento de artefactos de estado (writer/reader/fuente-de-verdad/prune) antes de tocar código.
16. **OC-21**: medir `lifecycle-recovery-faults/backups` y slots preprune reales; definir política de poda; implementar.
17. **OC-19**: fusionar el module-global lock del fault-store con el lock de instancia (la elección de arranque ya garantiza un solo escritor por proceso).
18. **OC-22**: escribir el test de divergencia box-occupancy vs box-claiming; si divergen, decidir cuál manda (hallazgo pendiente de confirmar — ver §No verificado).
19. **OC-15**: medir latencia de grant en vacío; solo si duele, simplificar el arco de locks (no las verificaciones).

### Tanda 4 — Retirada de fases y monolitos (continua)
20. **OC-24**: `main()` para security_runtime_audit + move a `checks/` + resolver la circularidad con run-fase*.ps1.
21. **OC-25/27/28**: operación `_archive/` única (junto con E1-previa de los ~11 GB): step0, gate4a, veredictos, `_restore/`, `_session_coordination/`; **antes** mover `_broker/e2e_daemon.py` a fixtures para no romper `test_session_e2e.py`.
22. **OC-30**: partición gradual de los monolitos listados cuando se toque cada uno por otras razones (no refactor big-bang).

---

## Qué NO se verificó

- **No se ejecutó nada**: ni la suite de tests, ni el daemon, ni un lanzamiento. Todos los findings son de lectura estática con cita; los "costes" (hashes ×9, O(n) del append, ceremony del grant) son trazados del código, no mediciones instrumentadas.
- **El verificador independiente de contexto fresco no llegó a existir**: el spawn falló por cuota de subagentes. Las 24 claims fueron verificadas por el auditor principal con sed/grep/AST — misma persona que leyó los reportes de los agentes, con el consiguiente riesgo de sesgo de confirmación. Mitigado con greps mecánicos de recuento (33/7/54, 5 defs `_valid_uuid4`, 0 refs a `_restore`), y con una refutación efectiva (auto_spawn_daemon) que indica que el filtro no era de goma.
- **OC-21**: no medí el tamaño real del árbol `lifecycle-recovery-faults/backups/` ni de los slots preprune.
- **OC-22**: no construí el escenario multi-cliente para confirmar/refutar divergencia entre los dos modelos de ocupación.
- **No auditados en profundidad** (quedan como superficie futura): interior de `mcp_client.py` (1.806 L), `doctor.py` (1.136 L), `install_mcp.py`, `host_config.py` (1.197 L), `orphan_guard.py` (1.065 L) más allá de lo cubierto por los ángulos; schemas JSON; el árbol del mod en OneDrive; `tools/checks/`.
- El recuento exacto "~9 hashes por lanzamiento" es la suma de sitios verificados individualmente; no instrumenté un launch para contarlos en vivo.
