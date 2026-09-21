# BUG-046 — revisión adversarial final de implementación R9

Fecha: 2026-07-22  
Revisor: Codex, subagente adversarial independiente  
Alcance: snapshot local R9 congelado por SHA-256, inspección, unit tests focales y canarios AST sin red/lifecycle. No se modificó código ni tests.  

## Veredictos

**CORE BLOCKED**

**H9 BLOCKED — DESIGN_BLOCKED**

Los tres fallos exactos de R8 quedan corregidos: el fixture local construye `DaemonProvenance`, la rama muerta ya no borra el taint y la base local mediante alias se sigue correctamente. La suite focal también queda verde. Sin embargo, el auditor que acredita la ausencia de transportes HTTP no autorizados mantiene bypasses reproducibles con sintaxis Python corriente: orden de métodos, atributo de clase, herencia entre módulos, import tardío, `+=`, expresión condicional, unpacking y dos construcciones habituales de query. Por tanto el resultado nominal no acredita la propiedad de seguridad CORE.

## Snapshot exacto revisado

| Archivo | SHA-256 |
|---|---|
| `plans/2026-07-22-bug046-lease-queue-liveness-plan.md` | `33CCA98329436D7460783ED98B1EEC01B1EF1B474024C9FD2FACC596E20DC64D` |
| `tools/dayz_mcp/security_runtime_audit.py` | `432CC8E65483BEBB55944522585521388F8CDE8C2083B301EC6C2D112F6263B9` |
| `tools/tests/test_security_runtime_audit.py` | `5039D374868C98B59D743A6D09EEE0689254A1E541D4D5EC34792BD2F2EF03B2` |
| `tools/_session_coordination/bug046_local_liveness_gate.py` | `E79423785F81BEFA0FAFE997A35B9DC3C21A58685E1F6C8C8EAB7D148A2C478F` |
| `tools/dayz_mcp/host_config.py` | `FDE9BFDEF8464A4C5F02DBD7094742827D0B73F93FAF3ADD23DDBFC9558E7062` |
| `tools/dayz_mcp/server.py` | `7917D833A8B2FE581E10277AC716FB51E1CE2B48C2CC3FEB1385F288DE0FFA9F` |
| `tools/dayz_mcp/secure_launcher.py` | `2A360729EC5BBDCD7AF31E4EA725F0298E5D55F48A63DB5928406E80E7E8400C` |
| `tools/approved-launchers.json` | `330B04E8D7AB06E7EE850326C1CAE180F119ED21486745DC0EC9BAAE203C653B` |
| `tools/README-mcp.md` | `1D9E88C3E12718B4454D4F40CEFA941BC858598D7323A94A6ACCEDF3FA2DE7A6` |

Los cinco hashes CORE se verificaron antes y después de los canarios; no hubo drift durante la revisión.

## R8 A/B/C repetidos

### A — fixture de proveniencia: GREEN

- `_test_provenance` ya proporciona `auto_spawn_daemon=True` (`tools/_session_coordination/bug046_local_liveness_gate.py:49-60`).
- Reproducción directa, sin listener ni procesos hijo:

```text
A_gate_provenance=OK:auto_spawn_daemon=True
```

El implementador informó además `gate local PASS` y cero procesos Python propios residuales. Esta revisión no repitió el gate multiproceso porque su alcance fue unitario/sin acciones de procesos; sí verificó directamente la construcción que fallaba en R8.

### B — rama muerta: GREEN

- El análisis diferencia asignaciones directas del cuerpo y une conservadoramente valores de asignaciones anidadas (`tools/dayz_mcp/security_runtime_audit.py:510-550`).
- El canario exacto `if False: url = "safe"` produjo:

```text
B_branch_dead_safe_assignment=[('probe_listener_responsive', 'sensitive_probe', 4)]
```

- El test permanente correspondiente pasa (`tools/tests/test_security_runtime_audit.py:659-681`).

### C — alias local de base: GREEN

- Se registran aliases estáticos de clases locales (`tools/dayz_mcp/security_runtime_audit.py:368-382`) y se resuelven al construir bases (`:218-226`).
- El canario exacto `Alias = Base; class Child(Alias)` produjo:

```text
C_aliased_base=[('Child.leak', 'http_request', 9)]
```

- El test permanente pasa (`tools/tests/test_security_runtime_audit.py:683-709`).

## Hallazgos bloqueantes nuevos

### HIGH-01 — el tracking de conexiones depende del orden textual de los métodos

**Tipo exacto:** falso negativo/bypass del auditor HTTP; no es un crash.

**Evidencia:** el visitor recorre cada clase una sola vez en orden fuente (`tools/dayz_mcp/security_runtime_audit.py:218-235`). Al entrar en un método crea un scope de conexiones y visita inmediatamente sus calls (`:237-248`). Los atributos HTTP solo se registran cuando más tarde se visita su asignación (`:269-299`). No hay prepass de declaraciones ni segunda pasada.

**Canario D:**

```python
class Client:
    def leak(self):
        self.conn.request("GET", "/status")

    def __init__(self):
        self.conn = http.client.HTTPConnection("127.0.0.1")
```

Resultado real:

```text
D_method_order=[]
```

**Impacto:** el orden de métodos no altera la semántica de la clase, pero mover `__init__` debajo del consumidor elimina el finding. Es un bypass trivial de la acreditación V25k.

**Fix requerido:** separar recolección y análisis. Primero indexar factories/conexiones/aliases/bases de toda la unidad; después analizar sinks. Añadir tests con `__init__` antes y después, y orden aleatorio de métodos.

### HIGH-02 — atributos HTTP de clase y herencia entre módulos no se propagan

**Tipo exacto:** falsos negativos del auditor HTTP.

**Evidencia:**

- Una factory asignada a target `Name` se guarda solo en `connection_names` (`tools/dayz_mcp/security_runtime_audit.py:290-298`), pero al entrar en el método se empuja un set vacío (`:237-241`). Por eso un atributo de clase no llega al uso `self.conn`.
- `class_bases` y `connection_attributes` pertenecen a una instancia `_HttpVisitor` (`:123-129`). `audit_runtime_http` crea un visitor independiente por fichero (`:746-778`), de modo que no existe grafo de clases/conexiones entre módulos.
- El alias de clase rechaza expresamente nombres cualificados (`:373-375`), por lo que tampoco sigue `Alias = Namespace.Base` dentro del mismo fichero.

**Canarios:**

```text
E_class_attribute_connection=[]
H_cross_module_inheritance=[]
M_qualified_nested_base_alias=[]
N_alias_chain_positive_control=[('Child.leak', 'http_request', 10)]
```

El control positivo confirma que la nueva cadena de aliases simples sí funciona; los tres `[]` aíslan límites reales.

**Impacto:** patrones válidos y corrientes —atributo de clase o base importada— pueden enviar requests sin transporte acreditado y sin finding.

**Fix requerido:** índice de clases/conexiones a nivel del closure productivo, con nombre cualificado de módulo/clase, resolución de imports y aliases, y dos fases. Mantener negativos para clases no relacionadas y atributos homónimos no HTTP.

### HIGH-03 — el dataflow del probe omite formas normales de asignación y expresiones

**Tipo exacto:** falso negativo que permite divulgar la key desde el único `urlopen` allowlisted.

**Evidencia:**

- `_probe_contains_sensitive_http` recolecta exclusivamente `Assign` y `AnnAssign` (`tools/dayz_mcp/security_runtime_audit.py:510-525`); no procesa `AugAssign`, unpacking de targets, loops, walrus ni otras definiciones.
- `_targets` devuelve el tuple/list target como una sola expresión y los trackers solo aceptan `Name`/atributo (`:263-267`, `:542-550`).
- `_constant_text_with_bindings` solo entiende `Name`, f-string y `BinOp(Add)` (`:410-437`); una `IfExp` devuelve vacío aunque una rama contenga `?key=`.

**Canarios:**

```text
F_probe_augassign=[]
G_probe_ifexp=[]
L_tuple_assignment_probe=[]
```

Ejemplo mínimo:

```python
def probe_listener_responsive(key):
    url = base
    url += "?key=" + key
    urllib.request.urlopen(url)
```

**Impacto:** una refactorización normal de `url = base + ...` a `url += ...` convierte una fuga bloqueada en código allowlisted sin findings.

**Fix requerido:** modelar definiciones/expresiones de forma explícita y conservadora, o cambiar la frontera: que el probe permitido solo pueda llamar a un sink rígido cuya firma no acepte key/body. Añadir positivos y negativos para cada forma soportada; cualquier forma no resoluble que alcance el sink allowlisted debe fallar cerrada.

### HIGH-04 — `urlencode(dict(key=key))` y una key percent-encoded eluden el taint

**Tipo exacto:** falsos negativos de query autenticada/sensitive probe.

**Evidencia:**

- `_contains_key_literal` solo reconoce claves `ast.Constant` dentro de un literal `ast.Dict` (`tools/dayz_mcp/security_runtime_audit.py:398-408`). La forma habitual `dict(key=key)` no es `ast.Dict`, por lo que `_track_text_assignment` no propaga `key=` (`:342-366`).
- Los markers se comparan como substrings crudos `?key`/`&key` (`:557-569`), sin normalizar percent-encoding.

**Canarios:**

```text
I_probe_urlencode_dict_keyword=[]
J_probe_percent_encoded_key=[]
```

La semántica no es hipotética:

```text
urllib.parse.parse_qs('%6bey=secret') == {'key': ['secret']}
```

**Impacto:** ambas formas entregan el parámetro `key` al receptor, pero la llamada `urlopen` de `probe_listener_responsive` queda cubierta por la allowlist y no aparece ningún finding.

**Fix requerido:** reconocer kwargs estáticos de `dict`, evaluar claves estáticas concatenadas y normalizar/decodificar el nombre de parámetro antes de comparar. Ante query no resoluble en el probe allowlisted, fail-closed.

### HIGH-05 — un import válido situado después de la función elimina todo finding

**Tipo exacto:** falso negativo general de discovery HTTP.

**Evidencia:** los aliases locales se incorporan solo cuando `visit_Import`/`visit_ImportFrom` se alcanzan (`tools/dayz_mcp/security_runtime_audit.py:199-216`). Si una función aparece antes, su cuerpo se analiza inmediatamente (`:237-248`) y el import posterior aún no existe en `alias_scopes`. El prepass `_module_aliases` (`:704-743`) no precarga esos heads locales en el scope del visitor.

**Canario K:**

```python
def leak():
    request.urlopen("http://127.0.0.1/status")

import urllib.request as request
```

Resultado real:

```text
K_late_import=[]
```

El módulo es válido: cuando `leak` se invoque después de completar el import, `request` estará ligado. No es código muerto ni sintaxis dinámica.

**Impacto:** el audit puede declarar limpio un transporte productivo arbitrario solo por reordenar declaraciones.

**Fix requerido:** preindexar imports/aliases top-level antes de visitar cuerpos ejecutables, conservando correctamente shadowing local. Añadir late import positivo y shadowing negativo.

## Validación ejecutada

| Gate | Resultado |
|---|---|
| `python -m unittest tests.test_mcp_host_timeouts tests.test_security_runtime_audit tests.test_client_mode.ClientRuntimeProvenanceGateTest tests.test_bug046_lease_queue_liveness` con `.venv-mcp` | `Ran 86 tests in 1.364s — OK (skipped=1)` |
| R8 A/B/C | 3/3 corregidos |
| Canarios nuevos D–N | 9 falsos negativos; 1 control positivo detectado |
| Normalización de query estándar | `parse_qs('%6bey=secret') -> {'key': ['secret']}` |
| Gate multiproceso local | `PASS` informado por implementador; no repetido en esta revisión sin acciones de procesos |

La suite en verde demuestra ausencia de regresión en los casos codificados, no completitud del auditor. Los falsos negativos D–M son ejecuciones separadas y mínimas; ninguno depende de red ni de comportamiento del daemon.

## Estado CORE no reabierto por cambios sin drift

`host_config.py` y `server.py` conservan exactamente los hashes R8 ya revisados. Por ello se mantienen los resultados previos para recovery `committed`, handle/junction/reopen, autoridad `ClientRuntime`, autospawn y carreras unitarias de cola. No se detectó una regresión nueva en esos componentes durante los 86 tests focales.

## H9 separado

**H9 BLOCKED — DESIGN_BLOCKED.**

- `tools/dayz_mcp/secure_launcher.py:313-320` continúa fail-closed con `native_launcher_not_configured`.
- `tools/approved-launchers.json:1-4` continúa vacío.
- `tools/README-mcp.md:68-70` mantiene manual-only y prohíbe copiar el lease token a argv/logs.

La contención es segura, pero aún no existe consumidor aprobado que lance `dayz-test.ps1` heredando el token únicamente por entorno. H9 no cambia el veredicto CORE.

## Condición mínima para R10

1. Convertir el auditor de conexiones/imports/clases en al menos dos fases para eliminar dependencia de orden y cruzar módulos del closure productivo.
2. Definir explícitamente el lenguaje estático permitido del probe. Todo valor/query no demostrablemente inocuo que alcance su `urlopen` allowlisted debe fallar cerrado.
3. Añadir RED/GREEN permanentes para D–M y conservar N como control positivo, además de todos los canarios R5–R9 existentes.
4. Repetir focales, auditor productivo, hashes y gate local autorizado sobre un freeze nuevo.

**CORE BLOCKED**

**H9 BLOCKED — DESIGN_BLOCKED**
