# BUG-046 — revisión adversarial final de implementación R7

Fecha: 2026-07-22  
Revisor: Codex, subagente adversarial independiente  
Alcance: snapshot local, inspección y tests focales. No se modificó producción ni tests.  
Trazabilidad: el árbol no contiene `.git`; se fija el snapshot por SHA-256.

## Veredictos

**CORE BLOCKED**

**H9 BLOCKED — DESIGN_BLOCKED**

Las correcciones solicitadas para recovery, pinning de configs, canaries nominales y `ClientRuntime` están presentes y pasan. El núcleo sigue bloqueado porque el auditor AST conserva dos falsos negativos reproducibles que permiten requests no acreditadas; además mantiene un falso positivo concreto en `getattr` constante. H9 sigue separado y no interviene en este veredicto del núcleo.

## Snapshot revisado

| Archivo | SHA-256 |
|---|---|
| `plans/2026-07-22-bug046-lease-queue-liveness-plan.md` | `33CCA98329436D7460783ED98B1EEC01B1EF1B474024C9FD2FACC596E20DC64D` |
| `tools/dayz_mcp/host_config.py` | `FC1832CEDF4117FB89FDE6AA2A131A6FF574B917C8C47EED0FAC05AEA9530408` |
| `tools/dayz_mcp/security_runtime_audit.py` | `EFEFDFD0738CB292FCFD0AFF3E8D59B028BCCCAD0A9B4D6CD78465871B47C892` |
| `tools/dayz_mcp/server.py` | `2552AD4EAE7FDCB3354541D3F1C611FCBD36D959936494D86BA9B6803CEDE65F` |
| `tools/tests/test_mcp_host_timeouts.py` | `1BB34B4A6B94562F9BEFC5E9F5A054F66E7C2ED929E59001FB1A19861C9CB303` |
| `tools/tests/test_security_runtime_audit.py` | `514987CCA6A942458A54BCE53203A94838ADA344F0C3CCD9F4D4BF750A5B3E25` |
| `tools/tests/test_client_mode.py` | `86205B241C10687129A6EA5F55C5F77E1B2C4ADC71C49193891C4681180D499E` |
| `tools/tests/test_bug046_lease_queue_liveness.py` | `AB581CBE77AB07D75A7548D073D301ABC5D5A54C59DDAB53E47C737A8A6C8787` |
| `tools/_session_coordination/bug046_local_liveness_gate.py` | `FCF1567AF11AF2EE4E754858A2DEDDADB7EFCE41171C6417C586CC75B2002597` |
| `tools/dayz_mcp/secure_launcher.py` | `2A360729EC5BBDCD7AF31E4EA725F0298E5D55F48A63DB5928406E80E7E8400C` |
| `tools/approved-launchers.json` | `330B04E8D7AB06E7EE850326C1CAE180F119ED21486745DC0EC9BAAE203C653B` |
| `tools/README-mcp.md` | `1D9E88C3E12718B4454D4F40CEFA941BC858598D7323A94A6ACCEDF3FA2DE7A6` |

## Hallazgos

### HIGH-01 — una reasignación posterior borra del análisis una URL sensible ya enviada por el probe allowlisted

**Tipo exacto:** bypass del gate de seguridad H10/V25k; no es un crash.

**Evidencia:**

- El probe nominal `probe_listener_responsive` es el único `urlopen` allowlisted (`tools/dayz_mcp/security_runtime_audit.py:45-55`). Por ello, reconocer cualquier key/body dentro de esa función es la frontera que evita divulgar autenticación desde el probe.
- `_probe_contains_sensitive_http` construye un único diccionario `bindings` mediante fixed-point global sobre todas las asignaciones del método (`tools/dayz_mcp/security_runtime_audit.py:408-425`). Una reasignación posterior reemplaza el valor anterior del nombre, aunque el sink ya se haya ejecutado antes.
- Después analiza cada call usando únicamente ese valor final (`:426-451`); no conserva orden de sentencias ni el conjunto de reaching definitions posibles.
- El test nuevo solo cubre una cadena sin reasignación (`tools/tests/test_security_runtime_audit.py:506-527`).

**Reproducción focal:**

```python
def probe_listener_responsive(key):
    marker = "?" + "ke" + "y="
    url = "http://127.0.0.1/status" + marker + key
    urllib.request.urlopen(url)
    marker = "/safe"
    url = "http://127.0.0.1/status"
```

Resultado real del auditor:

```text
reassignment_after_sink []
```

**Impacto:** el probe allowlisted puede enviar una key sin ningún finding. Una simple asignación muerta después del sink oculta la evidencia, por lo que la suite puede declarar limpio un callsite productivo inseguro.

**Fix requerido:** el análisis debe ser conservador y respetar orden/control de flujo, o mantener para cada nombre la unión de todos los valores estáticos alcanzables hasta el call. Una definición posterior nunca debe sanear retroactivamente un sink anterior. Añadir RED/GREEN para reasignación antes y después del sink, ramas y negativos realmente safe.

### HIGH-02 — `HTTPConnection.request` heredado entre clases no se propaga

**Tipo exacto:** bypass del auditor de request HTTP no acreditada.

**Evidencia:**

- Los atributos de conexión se indexan por `(tuple(class_stack), attribute_name)` (`tools/dayz_mcp/security_runtime_audit.py:122,139-145`).
- La asignación `self.conn = HTTPConnection(...)` se registra solo bajo la clase en la que aparece (`:235-253`).
- `visit_ClassDef` no procesa bases ni propaga atributos a subclases (`:198-205`).
- El positivo actual pone constructor y request en la misma clase (`tools/tests/test_security_runtime_audit.py:445-454`); el negativo cross-class evita el falso positivo previo (`:475-503`), pero no hay positivo de herencia.

**Reproducción focal:**

```python
class Base:
    def make(self):
        self.conn = http.client.HTTPConnection("127.0.0.1")

class Child(Base):
    def leak(self):
        self.conn.request("GET", "/status")
```

Resultado real:

```text
inherited_connection []
```

**Impacto:** mover una llamada a una subclase basta para eludir V25k y enviar HTTP sin el transporte acreditado.

**Fix requerido:** construir el grafo nominal de herencia local y consultar atributos rastreados en ancestros, conservando el negativo de clases no relacionadas. Añadir herencia directa, multinivel, alias de base y clase no relacionada con el mismo nombre de atributo.

### MEDIUM-03 — `getattr(HTTPConnection(...), "close")` se marca como HTTP dinámico

**Tipo exacto:** falso positivo de validación; puede bloquear código seguro, no divulga datos.

**Evidencia:** para un owner que es factory inline, `_is_unresolved_http_getattr` devuelve `True` sin inspeccionar si el segundo argumento es una constante conocida (`tools/dayz_mcp/security_runtime_audit.py:378-397`). El caller lo registra como `dynamic_http` (`:258-267,480-489`).

**Reproducción:**

```text
inline_constant_close [('safe', 'dynamic_http')]
```

**Fix sugerido:** si el atributo es string constante, distinguir al menos `request` —finding— de métodos no-sink como `close`; reservar `dynamic_http` para nombre no resoluble. Añadir positivos para `"request"` y negativos para `"close"`.

## Correcciones R5/R6 confirmadas

### Journal `committed` — GREEN

- `tools/dayz_mcp/host_config.py:1053-1057` acepta únicamente ambos targets exactos; cualquier deriva lanza `registration_recovery_conflict` antes de writes y conserva journal.
- `tools/tests/test_mcp_host_timeouts.py:879-930` cubre original/own-torn/external.
- Matriz adversarial adicional sobre ambos roles: **6/6** combinaciones devolvieron conflict, preservaron ambos ficheros byte-exactos y mantuvieron `manifest.status=committed`.

### Handle de config ante cualquier excepción post-open — GREEN

- Todo el bloque posterior a `CreateFileW` está dentro de `try/except BaseException` y ejecuta `self.close()` (`tools/dayz_mcp/host_config.py:671-705`).
- El test inyecta fallo de `_final_handle_path` y exige `CloseHandle(fake_handle)` exactamente una vez (`tools/tests/test_mcp_host_timeouts.py:613-640`).
- La reproducción R6 que antes dejaba `r+b/delete` bloqueados ya queda cubierta por esta estructura.

### Parent junction, leaf reparse y reapertura nominal — GREEN

- Se inspeccionan padres antes de open, se abre leaf no-follow y se compara el path final del handle con el nombre pedido (`tools/dayz_mcp/host_config.py:634-705`).
- Antes de devolver proveniencia se releen identidad+bytes del handle original y de una reapertura nominal (`tools/dayz_mcp/host_config.py:358-379`).
- Tests focales: ancestor rebind `tools/tests/test_mcp_host_timeouts.py:468-531`; cleanup/final path `:613-640`; leaf reparse `:664-678`; junction padre `:680-716`. Todos los ejecutados aplicables pasaron; el leaf symlink quedó omitido por falta de privilegio Windows.

### Canaries R5 nominales — GREEN, pero insuficientes por HIGH-01/HIGH-02

- `self.conn.request`, alias ligado, reexport relativo, `getattr` dinámico de módulo, inline factory, body posicional y concat directa se detectan (`tools/tests/test_security_runtime_audit.py:423-504`).
- Los atributos ya se separan por clase y el negativo de clase no relacionada pasa (`tools/dayz_mcp/security_runtime_audit.py:122,139-145`; test `:475-503`).
- La variable estática fragmentada sin reasignación pasa (`tools/tests/test_security_runtime_audit.py:506-527`).

### `ClientRuntime` y autoridad dual-config — GREEN

- Resuelve la proveniencia antes de leer/aceptar key (`tools/dayz_mcp/server.py:280-314`).
- Exige port y keyfile coincidentes; `argv[0]` debe ser launch; guarda native/argv/cwd de la proveniencia para probe/request (`:283-320`, `:604-612`, `:679-692`).
- Los tests exigen consenso antes de key-read, cero key/request ante unavailable/incomplete/conflict o mismatch, y separan launch/native (`tools/tests/test_client_mode.py:775-943`). La clase focal completa pasó.

## Cola FIFO y gate local aislado

El código del gate local está bien delimitado y comprueba la propiedad correcta:

- usa runtime/key temporales y proveniencia inyectada solo para el fixture (`tools/_session_coordination/bug046_local_liveness_gate.py:308-335`);
- crea tres procesos/sesiones distintos y posiciones B=1, C=2 (`:337-357`);
- tras release de A exige owner nulo y cola intacta; cancela B antes de que C sea concedido (`:359-370`);
- exige final limpio, cero grant de B abandonado y cero FIFO grant sin `source=live_wait` (`:372-406`).

Por instrucción de ejecutar únicamente unit tests focales, esta revisión no lanzó el gate multiproceso/listener. Sí ejecutó los tests unitarios de cola y las dos carreras release-audit; pasaron. El script aislado no sustituye el gate mixto 2+2 ni H9.

## Validación ejecutada

| Gate | Resultado |
|---|---|
| Selección exacta recovery/reopen/junction/AST/ClientRuntime/release-audit | 11 tests válidos OK; 1 ID escrito erróneamente por el revisor produjo `_FailedTest`, corregido inmediatamente |
| Dos tests AST con ID correcto | `Ran 2 tests — OK` |
| Focales completos `security_runtime_audit`, `mcp_host_timeouts`, `ClientRuntimeProvenanceGateTest`, `bug046_lease_queue_liveness` | `Ran 77 tests in 1.284s — OK (skipped=1)` |
| Matriz post-commit ambos roles × 3 drift | `committed_post_drift_matrix=6/6` |
| Canaries adversariales nuevos | reasignación post-sink `[]`; herencia `[]`; `close` produjo falso positivo |

El `_FailedTest` fue un error de nombre del comando de revisión, no un fallo del producto; el test correcto pasó después. Los dos `[]` adversariales sí son fallos reales del auditor del snapshot.

## H9 separado

**H9 BLOCKED — DESIGN_BLOCKED.**

- `tools/dayz_mcp/secure_launcher.py:313-320` sigue siendo placeholder fail-closed;
- `tools/approved-launchers.json:1-4` mantiene registro vacío;
- `tools/README-mcp.md:68-70` conserva manual-only y prohíbe copiar token.

No hay regresión de seguridad en esa contención, pero tampoco existe todavía consumidor aprobado. Su decisión nativo vs PowerShell continúa fuera del cierre CORE.

## Condición para R8

1. Cambiar el dataflow del probe para que una reasignación posterior no borre evidencia anterior.
2. Propagar atributos HTTP por herencia nominal sin reintroducir el falso positivo cross-class.
3. Afinar `getattr` constante en factory inline y añadir positivos/negativos.
4. Repetir canaries adversariales, focales y revisión sobre hashes nuevos congelados.

**CORE BLOCKED**

**H9 BLOCKED — DESIGN_BLOCKED**
