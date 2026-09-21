# BUG-046 — revisión adversarial final de implementación R8

Fecha: 2026-07-22  
Revisor: Codex, subagente adversarial independiente  
Alcance: freeze local R8, inspección y unit tests focales. No se modificó producción ni tests.  
Trazabilidad: el árbol no contiene `.git`; el freeze se identifica por SHA-256. Los cambios posteriores observados durante el cierre no forman parte de este veredicto.

## Veredictos

**CORE BLOCKED**

**H9 BLOCKED — DESIGN_BLOCKED**

R8 corrige los tres hallazgos de R7 en sus casos directos —orden de sentencias, herencia directa y `getattr(..., "close")` constante— y mantiene verdes recovery, pinning, autoridad dual-config, cliente y cola unitaria. No obstante, el freeze probado introduce una rotura estructural del gate local FIFO y conserva dos falsos negativos adversariales del auditor AST. Cualquiera de los tres bloquea el cierre CORE.

## Freeze exacto probado

| Archivo | SHA-256 |
|---|---|
| `plans/2026-07-22-bug046-lease-queue-liveness-plan.md` | `33CCA98329436D7460783ED98B1EEC01B1EF1B474024C9FD2FACC596E20DC64D` |
| `tools/dayz_mcp/host_config.py` | `FDE9BFDE90C96AB80DFA0C2EA254324217953B21F37AA4A9FA0A1EE19E88DB7F` |
| `tools/dayz_mcp/security_runtime_audit.py` | `91913672D17BDBF2FC14054CA039A09C13B2288DA8796332BE6519452064749B` |
| `tools/dayz_mcp/server.py` | `7917D83367E76FE4790FB36080F36CA0783D7245767F40EE53A0FF5CFAB015BD` |
| `tools/tests/test_mcp_host_timeouts.py` | `0EABD38BA7263FB0909AC9CF002C1F98B50BBD458122CD715DDA09610F764C7B` |
| `tools/tests/test_security_runtime_audit.py` | `3172497C0B890230767922C11022684430647824251ADE0DF4A2A5DA513AD056` |
| `tools/tests/test_client_mode.py` | `247B0A923861366D6EB5805FC2A18AC0B806E3B4052A7E3B8B4CA4A6D4506B9` |
| `tools/tests/test_bug046_lease_queue_liveness.py` | `AB581CBE77AB07D75A7548D073D301ABC5D5A54C59DDAB53E47C737A8A6C8787` |
| `tools/_session_coordination/bug046_local_liveness_gate.py` | `FCF1567AF11AF2EE4E754858A2DEDDADB7EFCE41171C6417C586CC75B2002597` |
| `tools/dayz_mcp/secure_launcher.py` | `2A360729EC5BBDCD7AF31E4EA725F0298E5D55F48A63DB5928406E80E7E8400C` |
| `tools/approved-launchers.json` | `330B04E8D7AB06E7EE850326C1CAE180F119ED21486745DC0EC9BAAE203C653B` |
| `tools/README-mcp.md` | `1D9E88C3E12718B4454D4F40CEFA941BC858598D7323A94A6ACCEDF3FA2DE7A6` |

## Hallazgos bloqueantes

### HIGH-01 — el gate local FIFO no puede construir la proveniencia de prueba

**Tipo exacto:** exception determinista al iniciar el gate local; la validación queda sin ejecutar. No es un crash del daemon ni un fallo de FIFO demostrado.

**Evidencia del freeze:**

- `DaemonProvenance` incorporó el campo requerido `auto_spawn_daemon` (`tools/dayz_mcp/host_config.py:39-47`).
- `_test_provenance` seguía construyendo esa dataclass con la firma anterior y no proporcionaba el campo (`tools/_session_coordination/bug046_local_liveness_gate.py:49-58`).

**Reproducción exacta A, sin lanzar listener ni procesos:**

```python
_test_provenance(18765, Path("fixture.key"))
```

Resultado real:

```text
gate_provenance=TypeError:DaemonProvenance.__init__() missing 1 required positional argument: 'auto_spawn_daemon'
```

**Impacto:** el gate aislado que debe demostrar A/B/C, posiciones FIFO y concesión solo desde `live_wait` termina antes de ejercer la propiedad. La suite focal no lo detectó porque no importa/ejecuta esta construcción.

**Fix requerido:** pasar explícitamente el valor de fixture y añadir un test de construcción o importación del gate que falle si la dataclass cambia de firma. Después debe ejecutarse el gate local completo en el entorno autorizado.

### HIGH-02 — una asignación dentro de una rama muerta sanea retroactivamente una URL sensible

**Tipo exacto:** falso negativo/bypass del gate de seguridad H10/V25k; no es un crash.

**Evidencia del freeze:**

- El análisis R8 ordena asignaciones y calls por posición, lo que corrige la reasignación posterior lineal, pero sigue actualizando bindings por recorrido léxico sin modelar alcanzabilidad/control de flujo (`tools/dayz_mcp/security_runtime_audit.py:408-455`, función `_probe_contains_sensitive_http`).
- En consecuencia, una asignación dentro de `if False` se considera una definición efectiva y puede reemplazar el valor sensible que sí alcanza el sink.

**Reproducción exacta B:**

```python
def probe_listener_responsive(key):
    marker = "?" + "ke" + "y="
    url = base + marker + key
    if False:
        url = "safe"
    urllib.request.urlopen(url)
```

Resultado real:

```text
branch_dead_safe_assignment []
```

**Impacto:** código productivo que envía una key puede pasar el auditor añadiendo una asignación no alcanzable antes del sink. El caso lineal corregido no basta para acreditar el análisis.

**Fix requerido:** análisis conservador sensible al control de flujo o unión de reaching definitions posibles. Una rama no alcanzable no puede eliminar la definición sensible; ante ramas no resolubles, cualquier definición sensible alcanzable debe producir finding. Añadir RED/GREEN para `if False`, rama dinámica y rama realmente safe.

### HIGH-03 — la herencia mediante alias de clase elude el tracking de `HTTPConnection`

**Tipo exacto:** falso negativo/bypass del auditor de request HTTP no acreditada.

**Evidencia del freeze:**

- R8 construye relaciones nominales para bases escritas directamente y ya detecta `class Child(Base)`.
- La resolución de bases no sigue una asignación alias antes de construir la relación (`tools/dayz_mcp/security_runtime_audit.py:198-220`, visitor de clases/bases del freeze).

**Reproducción exacta C:**

```python
class Base:
    def make(self):
        self.conn = http.client.HTTPConnection("127.0.0.1")

Alias = Base

class Child(Alias):
    def leak(self):
        self.conn.request("GET", "/status")
```

Resultado real:

```text
aliased_base []
```

**Impacto:** un alias local de una base permite emitir una request sin transporte acreditado y sin finding. Es una variante normal de Python, no metaprogramación exótica.

**Fix requerido:** resolver aliases locales estáticos al construir el grafo de herencia, con protección de ciclos, y mantener el negativo de clases no relacionadas. Añadir herencia directa, multinivel, alias simple, cadena de aliases, ciclo/unknown conservador y clase no relacionada.

## Correcciones confirmadas en R8

### Orden lineal, herencia directa y atributo constante seguro — GREEN en los casos exactos

- Una reasignación lineal posterior al sink ya no borra la evidencia: el canary produjo findings `sensitive_probe` y `authenticated_query`.
- `Child(Base)` directo con `self.conn.request(...)` ya produce finding.
- `getattr(HTTPConnection(...), "close")` ya no produce `dynamic_http`; el negativo seguro pasa.
- Los casos adversariales HIGH-02/HIGH-03 demuestran que las generalizaciones siguen incompletas.

### Cleanup ante fallo de constructor — GREEN

- El handle abierto se cierra ante excepción post-open (`tools/dayz_mcp/host_config.py:671-705`).
- El test focal de fallo de `_final_handle_path` pasó y comprobó un único `CloseHandle` (`tools/tests/test_mcp_host_timeouts.py:613-640`).

### Recovery `committed`, junction/reparse y reapertura nominal — GREEN

- La matriz adicional ambos roles × original/own-torn/external volvió a dar `committed_post_drift_matrix=6/6`: conflict antes de writes, bytes intactos y journal preservado.
- Los tests focales de constructor cleanup, ancestor rebind, parent junction y reapertura nominal pasaron. El leaf symlink permaneció omitido por privilegios de Windows.

### Autoridad `ClientRuntime` — GREEN en el freeze

- `ClientRuntime` resuelve proveniencia antes de key, valida port/paths/launch/native, exige igualdad exacta de `auto_spawn_daemon` y keyfile, y conserva esa autoridad para runtime (`tools/dayz_mcp/server.py:280-336`).
- Los tests de consenso, mismatch, launch/native swap, autospawn mismatch y autoridad no-autospawn pasaron (`tools/tests/test_client_mode.py:782-1040`).

### Cola unitaria y carreras release-audit — GREEN

- Los tests unitarios de FIFO, cancelación, lease wait y ambas carreras release-audit pasaron.
- Este resultado no sustituye el gate multiproceso local: HIGH-01 impidió que su fixture pudiera arrancar en este freeze.

## Validación ejecutada

| Gate | Resultado |
|---|---|
| `python -m unittest tests.test_mcp_host_timeouts tests.test_security_runtime_audit tests.test_client_mode.ClientRuntimeProvenanceGateTest tests.test_bug046_lease_queue_liveness` | `Ran 83 tests in 1.366s — OK (skipped=1)` |
| Matriz recovery committed, ambos roles × 3 estados | `committed_post_drift_matrix=6/6` |
| Orden lineal posterior al sink | detectado: `[('probe_listener_responsive', 'sensitive_probe'), ('probe_listener_responsive', 'authenticated_query')]` |
| Herencia directa | detectada |
| `getattr(HTTPConnection(...), "close")` | sin finding, correcto |
| Construcción del fixture del gate | `TypeError` por `auto_spawn_daemon` ausente |
| Rama muerta que reasigna URL | `[]`, falso negativo |
| Base heredada mediante alias | `[]`, falso negativo |

La suite existente en verde no invalida los tres canaries adversariales: A prueba una ruta no importada por los unit tests; B y C prueban generalizaciones ausentes de las fixtures nominales.

## H9 separado

**H9 BLOCKED — DESIGN_BLOCKED.**

- `tools/dayz_mcp/secure_launcher.py:313-320` sigue siendo placeholder fail-closed con `native_launcher_not_configured`.
- `tools/approved-launchers.json:1-4` mantiene el registro vacío.
- `tools/README-mcp.md:68-70` conserva el flujo manual-only y la prohibición de copiar el token.

No se ejecutaron acciones de red, listener ni lifecycle en esta revisión. H9 no degrada la seguridad del CORE, pero el problema original de lanzar `dayz-test.ps1` con el token solo en el entorno y nunca en argv/logs sigue sin consumidor aprobado.

## Condición para R9

1. Actualizar el fixture del gate local para la firma completa de `DaemonProvenance` y añadir cobertura que detecte futuras derivas.
2. Hacer conservador el dataflow del probe ante ramas, incluyendo asignaciones muertas.
3. Resolver aliases locales estáticos en el grafo de herencia sin reintroducir falsos positivos cross-class.
4. Congelar hashes nuevos, repetir los tres canaries A/B/C, focales completos, matriz committed y gate local autorizado.

**CORE BLOCKED**

**H9 BLOCKED — DESIGN_BLOCKED**
