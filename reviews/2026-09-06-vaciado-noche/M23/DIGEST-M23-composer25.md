Voy a leer el brief en la raíz del workspace y seguir sus instrucciones al pie de la letra.
Contando citas path:line y afirmaciones sobre código en el plan para el digest.
## 1. CONTRATO

| Sección exigida (BRIEF-PLAN §3) | Estado | Evidencia en PLAN-M23.md |
|---|---|---|
| 3.1 Resumen ejecutivo (10 líneas) | PRESENTE | PLAN-M23.md:3 |
| 3.2 Inventario del antecedente | PRESENTE | PLAN-M23.md:16 |
| 3.3 Diseño por pieza (a)(b)(c) | PRESENTE | PLAN-M23.md:83 |
| 3.4 Secuencia en lotes pequeños | PRESENTE | PLAN-M23.md:380 |
| 3.5 Escenarios R8 y matriz de estado | PRESENTE | PLAN-M23.md:500 |
| 3.6 Gates y parada del bucle | PRESENTE | PLAN-M23.md:532 |
| 3.7 Preguntas para el dueño (≤5) | PRESENTE | PLAN-M23.md:562 |
| 3.8 LO QUE NO PUDE VERIFICAR | PRESENTE | PLAN-M23.md:574 |

## 2. QUE PROPONE

**(a) Promotor / autoridad de contrato**
- [DESIGN] `resolve_effective_registry(app, profile, role, public_config)` observa la app terminada sin reconstruirla ni lifespan; documento M23 con `tools` como objetos MCP íntegros. PLAN-M23.md:102-130
- [DESIGN] Canonicalización `dayz-mcp-effective-v1` + SHA-256 reproducible; claims independientes en `effective_schema_contracts.py` y auditor `audit_effective_contracts`. PLAN-M23.md:134-188
- [EXACT] Consumidor FastMCP serializa `list_tools` con `by_alias=True`, `exclude_none=True`. PLAN-M23.md:100
- [DESIGN] CLI `promote_effective_schema.py` + `registry_authority.py` con CAS/journal. PLAN-M23.md:274-355

**(b) Señal de registro desactualizado**
- [DESIGN] Snapshot inmutable de arranque + lectura viva de autoridad/PBO; `bridge_status(registry_only=False|True)` y `session_status()` con bloque `tool_registry` versionado. PLAN-M23.md:192-258
- [DESIGN] Tres identidades separadas (registro, generación daemon, PBO); `registry_state` fresh/stale/unknown; conserva campos legacy M14 sin compararlos con M23. PLAN-M23.md:250-258
- [DESIGN] Probe PBO por bytes desplegados (no mtime); `loaded_sha256` siempre null. PLAN-M23.md:260-268

**(c) Calibración / CAS / journal**
- [EXACT] Reutiliza `sorted(synonym_set)` de V8/139c9f4 para estabilidad textual; no añade hash/CAS por sí solo. PLAN-M23.md:54
- [DESIGN] Layout `%LOCALAPPDATA%/DayZ_MCP/tool-registry/<installation_id>/` con eventos JSON inmutables, `head.json` atómico, publish con `expected_head` (epoch+revision+SHA cabecera). PLAN-M23.md:276-347

**Citas path:line en §3.3:** ~18 afirmaciones sobre código existente llevan cita; ~12 bloques [DESIGN] de APIs/formatos nuevos no la requieren; 3 afirmaciones de integración/comportamiento sin cita explícita (concurrencia del probe PBO PLAN-M23.md:264; límites de profundidad/tamaño PLAN-M23.md:142; secuencia publish pasos 1-2 PLAN-M23.md:337-338).

## 3. LOTES

| Lote | Oráculo / rojo-primero | Mutantes | Comando |
|---|---|---|---|
| L0 | Arnés `test_m23_harness.py`; H0 imprime PASS falso → SETUP-FAILED | H0 | `check_m23.py --lot L0 --seed 2300 --timeout 50` PLAN-M23.md:407-416 |
| L1 | Exportador completo vs SDK directo; hash idéntico en 3 procesos/PYTHONHASHSEED | R1-R9 | `--lot L1 --seed 2301` PLAN-M23.md:418-434 |
| L2 | Enum en descripción ausente del schema; auditor viejo vacío → FAIL | C1-C8 | `--lot L2 --seed 2302` PLAN-M23.md:436-448 |
| L3 | Dos publishers mismo token sin CAS → dos commits | J1-J8 | `--lot L3 --seed 2303` PLAN-M23.md:452-462 |
| L4 | Sesión A congelada tras publish r1; B fresh; PBO bytes sin daemon restart | S1-S10 | `--lot L4 --seed 2304` PLAN-M23.md:464-479 |
| L5 | Fuente cambia post-candidato → publish rechazado | P1-P5 | `--lot L5 --seed 2305` PLAN-M23.md:481-498 |

**Lote mínimo útil:** L0+L1+L2 — entrega pieza (a) y gate real; no cierra 9b7b ni CAS/journal completo. PLAN-M23.md:450

## 4. PREGUNTAS PARA EL DUEÑO

1. «¿Cómo observar sin daemon/caja?» → **Recomendada:** `registry_only` opcional en `bridge_status`; modo normal conserva su ruta. PLAN-M23.md:568
2. «¿Qué contrato hace mecánica la prosa?» → **Recomendada:** Bloques de argumentos desde claims independientes + prueba schema/validador; efectos no decidibles explícitos. PLAN-M23.md:569
3. «¿Cómo conservar el journal?» → **Recomendada:** Eventos JSON inmutables + head atómico; sin GC automático en M23. PLAN-M23.md:570
4. «¿Cómo tratar nombres type/classname y sesiones abiertas?» → **Recomendada:** Conservar nombres públicos; coexistencia solo advisory; corregir promesas. PLAN-M23.md:571
5. «¿Qué significa detectar cambio de PBO?» → **Recomendada:** Bytes del archivo desplegado acreditado; `loaded_sha256=null`. PLAN-M23.md:572

## 5. RIESGOS Y LO NO VERIFICADO

**Tres riesgos mayores (§3.1):**
1. Gate de proyección o fixtures reconocibles aprueba snapshot inútil. PLAN-M23.md:12
2. Confundir ausencia, concurrencia o journal incompleto con fresh. PLAN-M23.md:13
3. Romper clientes o afirmar que PBO desplegado equivale a PBO cargado. PLAN-M23.md:14

**LO QUE NO PUDE VERIFICAR (resumen):** M23 y sus gates propuestos no se implementaron ni ejecutaron; solo 54 tests aislados del antecedente. No se lanzó juego/daemon/suite completa ni G6 manual. No se acreditó PBO desplegado/cargado, app.pyz ni tolerancia a corte de energía del FS. HEAD avanzó durante la lectura (baseline fijado 8ff937e; L0 debe revalidar). PLAN-M23.md:574-586

## 6. INCOHERENCIAS

**Internas:** Ninguna contradicción sin resolver explícita; `remove_tool` del brief no existe en V fijado y el plan lo documenta (PLAN-M23.md:80). Deriva de HEAD durante autoría reconocida (PLAN-M23.md:36).

**Fichas no cubiertas:**
- d366: TypeError latente con enums no escalares — AUSENTE en el plan.
- d366: escenario A-01 de renombrado inverso que uniforma las 8 tools — solo advisory PARAM-NAME-DIVERGENCE; no oráculo específico.
- 9b7b opción (c) documentar en instructions — descartada deliberadamente (señal mecánica, no solo docs).

**Afirmaciones sobre código sin path:line en §3.3:** concurrencia máxima del probe PBO (PLAN-M23.md:264); límites 128/8MiB/64MiB (PLAN-M23.md:142); pasos 1-2 de publish fuera del lock (PLAN-M23.md:337-338). El resumen §3.1 líneas 11 y 16-17 carecen de cita (exclusiones de alcance, no código).