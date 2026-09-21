# BUG-046 — revisión adversarial final de implementación R5

Fecha: 2026-07-22  
Revisor: Codex, subagente adversarial independiente  
Alcance: plan, código y pruebas locales; no se modificó producción ni tests.  
Repositorio Git: no disponible en este árbol (`git rev-parse` no encuentra `.git`); la trazabilidad se fija por SHA-256 de archivo.

## Veredictos

**CORE BLOCKED**

**H9 BLOCKED — DESIGN_BLOCKED**

El núcleo no recibe luz verde: hay tres hallazgos HIGH reproducidos contra el snapshot, dos de seguridad/provenance y uno de recuperación persistente. El lanzador H9 queda separado: su placeholder fail-closed es coherente como contención, pero no existe aún el consumidor nativo/PowerShell aprobado que resuelva el caso operativo de `dayz-test.ps1`.

## Snapshot revisado

| Archivo | SHA-256 |
|---|---|
| `plans/2026-07-22-bug046-lease-queue-liveness-plan.md` | `149DE08FDE970E902CACDA758103AE9B34377641FFF06E86C6AB3A70FEEB424D` |
| `reviews/2026-07-22-bug046-final-implementation-r4-adversarial.md` | `08D7FB2119D763C47B3735D28A4D576630A086E3B7D981B55EE5A18097084979` |
| `tools/dayz_mcp/server.py` | `514711226E1540245C4F130E7A64CA4C6BA43D29059446692DB3626F90E56702` |
| `tools/dayz_mcp/session_coordination.py` | `51DD7DC8CC9BA808ECA779D9DDB8FD4268A1EF1842E47E011C7AABEF4471745F` |
| `tools/dayz_mcp/host_config.py` | `6248EE5E78874E4600129CEC68A37BF05C1D4722A9CB790F2657CA21527490BB` |
| `tools/dayz_mcp/security_runtime_audit.py` | `A9A633DF4EFDBD46B62FE8B809A9E34896B5B5FD9E6645CDDE4F7749C29E1639` |
| `tools/dayz_mcp/secure_launcher.py` | `2A360729EC5BBDCD7AF31E4EA725F0298E5D55F48A63DB5928406E80E7E8400C` |
| `tools/tests/test_mcp_host_timeouts.py` | `09B3A8B635BBCD5B0BF47BF81801DC0C7BCB65BB5A2BD1FFAA07704176621248` |
| `tools/tests/test_security_runtime_audit.py` | `171D369A21EEB5A9DD31890B7917721F8A51B69CA47F32087A58979DFD3D33DB` |
| `tools/tests/test_bug046_lease_queue_liveness.py` | `AB581CBE77AB07D75A7548D073D301ABC5D5A54C59DDAB53E47C737A8A6C8787` |
| `tools/approved-launchers.json` | `330B04E8D7AB06E7EE850326C1CAE180F119ED21486745DC0EC9BAAE203C653B` |
| `tools/README-mcp.md` | `1D9E88C3E12718B4454D4F40CEFA941BC858598D7323A94A6ACCEDF3FA2DE7A6` |

El plan cambió durante la ventana de revisión respecto del primer freeze; las citas y el veredicto corresponden al SHA final anterior.

## Hallazgos

### HIGH-01 — un journal `committed` puede restaurar y sobrescribir cambios posteriores

**Tipo exacto:** riesgo de corrupción/pérdida de configuración persistente; no es un crash.

**Evidencia:**

- El contrato exige que solo `committed` acepte ambos destinos exactamente en target como éxito final (`plans/2026-07-22-bug046-lease-queue-liveness-plan.md:602`) y restringe clasificación/restauración a estados no committed (`:604`).
- `_recover_if_needed` trata únicamente `restoring_original|restored` de forma separada (`tools/dayz_mcp/host_config.py:986-999`). Todos los demás estados, incluido `committed`, entran en el clasificador inicial (`:999-1017`). Si un fichero ya no es target pero todavía parece original/own-torn, cambia el journal a `restoring_original` y prepara su sobrescritura.
- Un crash en `after_commit` deja legítimamente el manifest `committed` antes del cleanup (`tools/dayz_mcp/host_config.py:1087-1093`). En ese punto ambos targets ya fueron verificados: cualquier diferencia posterior es deriva post-commit, no una escritura incompleta de la transacción.
- `tools/tests/test_mcp_host_timeouts.py:668-836` cubre commit normal, crash pre-commit, own-torn manual, reentrada de restore y drift externo, pero no `after_commit` seguido de deriva.

**Reproducción aislada en directorio temporal:** se inyectó `HostConfigCrash` en `after_commit`, se devolvió solo Claude a sus bytes originales y se invocó recovery con parada en `after_recovery_prepare`. Resultado observado:

```text
manifest_before=committed
outcome=HostConfigCrash
manifest_after=restoring_original
entered_restore=True
```

**Impacto:** una edición válida posterior al commit que coincida observacionalmente con original o una familia own-torn puede provocar que recovery sobrescriba ambos ficheros. El segundo destino, aunque todavía sea target correcto, también se restaura. Esto viola la separación entre recuperación de una transacción incompleta y cambios hechos después de una transacción ya confirmada.

**Fix requerido:** ramificar `status == "committed"` antes del clasificador: si ambos bytes son target, limpiar journal; ante cualquier otra combinación, `registration_recovery_conflict`, cero writes y journal preservado. Añadir casos `after_commit` con un destino original, own-torn y externo; en todos los drift debe conservarse byte-exacto el par observado.

### HIGH-02 — V25k puede dar falso GREEN mediante cinco bypasses AST triviales

**Tipo exacto:** degradación del gate de seguridad; la búsqueda manual no encontró hoy un bypass productivo activo, pero el auditor no acredita el invariante que afirma acreditar.

**Contrato:** el plan exige detectar aliases, reexports, `getattr` no resoluble, requests y construcción de `?key` (`plans/2026-07-22-bug046-lease-queue-liveness-plan.md:920`), y detalla además atributos/conexiones, body posicional, imports relativos y `JoinedStr|BinOp` (`:924`). V25k lo convierte en criterio de aceptación (`:357`).

**Causas en código:**

- Solo se registran conexiones asignadas a `ast.Name`, no a `self.conn` u otro atributo (`tools/dayz_mcp/security_runtime_audit.py:215-227`), y `.request()` también exige owner `ast.Name` o factory inline (`:317-325`).
- `getattr` dinámico solo se reporta cuando el nombre del atributo es constante y ya resoluble (`:233-240`, `:267-279`, `:326-331`); el caso explícitamente exigido “no resoluble” queda invisible.
- El probe busca markers en strings y body/data solo por keyword (`:281-309`); `Request(url, b"secret")` pasa.
- La propagación de reexports ignora `ImportFrom.level` y concatena `node.module` como absoluto (`:407-438`), por lo que `from .sink import send` no se resuelve.
- La query autenticada solo se detecta vía `urlencode` que contenga la constante exacta `"key"` (`:260-265`, `:311-316`); `"/status?key=" + key` o f-string aislado pasan.

**Reproducción aislada:** cinco pequeños paquetes temporales, cada uno en la clausura de `dayz_mcp.server`, devolvieron `[]`:

```text
attribute_connection []
relative_reexport []
dynamic_getattr_name []
probe_positional_body []
pure_key_concat []
```

Los fixtures existentes solo prueban reexport absoluto, nombre constante en `getattr` y `data=` keyword (`tools/tests/test_security_runtime_audit.py:191-248`), de modo que pasan sin detectar estas omisiones.

**Impacto:** una futura llamada HTTP no acreditada puede entrar en producción y superar la suite focal. Dado que este auditor es el cierre automático de H10/V25k, su falso negativo invalida el gate aunque los callsites actuales inspeccionados manualmente estén concentrados en `orphan_guard.py`.

**Fix requerido:** cubrir cada mecanismo enumerado en `:924`, incluidos alias de método ligado y `self.connection`; resolver `ImportFrom` relativo con módulo+level; reportar `getattr` HTTP no resoluble; analizar argumentos posicionales sensibles; detectar key en `JoinedStr`, `BinOp` y constantes parciales. Cada reproducción anterior debe convertirse en positivo focal, acompañada de su negativo para controlar falsos positivos.

### HIGH-03 — la proveniencia sigue name-surrogates en componentes padre

**Tipo exacto:** bypass fail-closed de la raíz nominal de confianza; no es un crash.

**Evidencia:**

- El contrato exige `CreateFileW(... no-follow/no-name-surrogate)` para ambas configs (`plans/2026-07-22-bug046-lease-queue-liveness-plan.md:916`) y reabrir cada pathname antes de devolver para comparar FileId+bytes (`:924`).
- `_PinnedConfigFile` abre el pathname con `FILE_FLAG_OPEN_REPARSE_POINT`, pero solo consulta `FILE_ATTRIBUTE_REPARSE_POINT` del handle final (`tools/dayz_mcp/host_config.py:607-637`). Un junction/symlink en un componente padre ya fue seguido al resolver el leaf y no aparece en los atributos de ese leaf.
- La revalidación final vuelve a leer identidad+bytes de los mismos handles (`tools/dayz_mcp/host_config.py:351-362`); nunca reabre los nombres canónicos ni verifica los componentes padre.
- La única prueba reparse crea un symlink en el fichero final (`tools/tests/test_mcp_host_timeouts.py:553-567`); no cubre junction de directorio padre.

**Reproducción Windows aislada:** se creó un junction `alias -> real`, se almacenaron ambas configs válidas bajo `real` y se pasó `alias/.claude.json` + `alias/config.toml` al resolver. El resultado fue:

```text
outcome=returned
port=18765
parent_is_junction=True
```

**Impacto:** el consenso puede acreditarse desde ficheros fuera del pathname nominal, contradiciendo el modelo no-name-surrogate. Esto es especialmente relevante para `~/.codex/config.toml`, cuyo directorio padre forma parte de la ruta de confianza.

**Fix requerido:** validar todos los componentes con semántica name-surrogate, verificar el path final obtenido por handle contra el pathname canónico y realizar la reapertura final de ambos pathnames exigida por el plan mientras siguen vivos los handles originales. Añadir positivos para componentes OneDrive cloud no-name-surrogate y negativos con junction/symlink padre, leaf reparse y swap de ancestro.

## Revisión de las correcciones R4

| Punto R4 | Estado R5 | Evidencia |
|---|---|---|
| HIGH-02 — wait 3.600 s vs host 1.860 s | **GREEN** | Máximo/default único 1.800 s en `tools/dayz_mcp/server.py:31,896-905`; schema y rechazo `1800.001` en `tools/tests/test_session_acquire_wait.py:257-271` y `tools/tests/test_mcp_tools.py:183-204`; relación host > funcional en `tools/tests/test_mcp_host_timeouts.py:31-34`. |
| HIGH-03 — own-torn y reentrada de restore | **GREEN para el defecto R4 concreto; BLOCKED por HIGH-01 nuevo** | Familias en `tools/dayz_mcp/host_config.py:920-956`; reentrada durable en `:986-1024`; pruebas en `tools/tests/test_mcp_host_timeouts.py:31-70,730-809`. La enumeración exhaustiva local de secuencias pequeñas no vacías pasó. |
| MEDIUM-04 — handle launcher permitía write in-place | **GREEN técnico** | `CreateFileW(GENERIC_READ, FILE_SHARE_READ)` en `tools/dayz_mcp/secure_launcher.py:148-173`; reader paralelo permitido, `r+b` denegado durante el handle y permitido tras cierre en `tools/tests/test_secure_launcher.py:147-169`. Sigue sin consumidor H9 productivo. |

## Cola FIFO, waits vivos, cancelación y release-audit

No encontré una regresión adicional en este bloque:

- release limpia `_releasing`, arma `_handoff_pending` y deja la auditoría terminal bajo fence (`tools/dayz_mcp/session_coordination.py:1948-1981`);
- el worker terminaliza WAL/fault antes de levantar el fence y notificar (`:1983-2099`);
- un audit lento conserva cola sin grant y solo un `session_wait` vivo reclama después del terminal audit; fallo terminal queda visible y bloquea (`tools/tests/test_bug046_lease_queue_liveness.py:569-655`);
- los dos tests de carrera se repitieron 30 veces cada uno: **60/60 OK**;
- el focal completo pasó: **355 tests, OK, skipped=1**.

Esto no compensa los tres HIGH anteriores: la cola puede estar sólida y el núcleo seguir sin poder declararse final por persistencia/provenance/auditoría.

## Cuatro consumidores de proveniencia

La ruta nominal está conectada en los cuatro consumidores y la suite focal pasa:

- `ClientRuntime`: `tools/dayz_mcp/server.py:270-293,640-666`;
- admin: `tools/dayz_mcp/admin_cli.py:20-49`;
- lifecycle: `tools/dayz_mcp/lifecycle_cli.py:21-48`;
- doctor: `tools/dayz_mcp/doctor.py:122-146`.

Los consumidores usan el transporte verificado y separan launch/native; no encontré un callsite productivo directo fuera de los helpers de `orphan_guard.py`. Sin embargo, HIGH-03 invalida la fuente de proveniencia antes de esos consumidores y HIGH-02 impide acreditar automáticamente que esa propiedad seguirá siendo cierta.

## Gates ejecutados

| Gate | Resultado | Interpretación |
|---|---|---|
| Suite focal del plan | `Ran 355 tests in 20.245s — OK (skipped=1)` | Verde, pero insuficiente por falsos negativos reproducidos. |
| Shake de release-audit | `Ran 60 tests in 0.930s — OK` | Verde. |
| Suite global | `Ran 851 tests in 116.563s — FAILED (errors=28, skipped=2)` | 26 errores `test_port_reclaim` coinciden nominalmente con baseline; aparecieron además 2 subtests de startup crash-boundary. |
| Rerun crash-boundary | 4/4 stages terminaron por `TimeoutExpired` a 12 s | No es baseline-clean. Había múltiples sesiones DayZ MCP reales, por lo que el candidate drain no es un fixture aislado; se clasifica como gate ambiental inconcluso, no como bug probado de esta R5. No se terminó ningún proceso real. |
| Doctor local | `DAEMON_STATUS_UNREADABLE`, exit 1 | Gate live no verde; no se intentó forzar listener ni manipular sesiones. |

El baseline histórico declara 654 tests y 26 errores conocidos; el discovery actual subió a 851, pero el conjunto no-green no es idéntico durante esta ejecución. Según Step 4 del plan, el gate global debe repetirse en una ventana aislada y compararse por ID+fingerprint antes de cierre.

## Estado separado de H9

**H9 BLOCKED — DESIGN_BLOCKED.**

- `tools/dayz_mcp/secure_launcher.py:313-320` abre una entrada aprobada y termina siempre con `native_launcher_not_configured`;
- `tools/approved-launchers.json:1-4` mantiene `launchers: []`;
- `tools/README-mcp.md:68-70` lo documenta explícitamente como pendiente, manual-only y sin copiar token;
- el hardening del handle quedó correcto, pero no existe acquire-wait, inyección temporal de env, proceso hijo, drains, heartbeat ni release porque todavía no hay consumidor aprobado.

Este estado no debe mezclarse con CORE: el placeholder fail-closed no introduce una ejecución insegura, pero tampoco resuelve la necesidad operativa comunicada por Claude. El cierre requiere una decisión de arquitectura H9 (consumidor nativo neutral frente a cambio explícito del criterio para PowerShell), implementación separada y revisión independiente.

## Condiciones mínimas para repetir R6

1. Corregir la semántica recovery de `committed` y añadir los tres negativos post-commit.
2. Implementar literalmente los mecanismos AST de `plans/...:924` y hacer pasar las cinco reproducciones.
3. Rechazar junction/symlink en cualquier componente de config y reabrir pathnames antes de devolver proveniencia.
4. Repetir focal, canaries, race shake y global contra un snapshot SHA congelado, sin sesiones reales contaminando el startup fixture.
5. Mantener H9 como veredicto separado hasta resolver su decisión de diseño.

**VERDICT: CORE BLOCKED / H9 BLOCKED**
