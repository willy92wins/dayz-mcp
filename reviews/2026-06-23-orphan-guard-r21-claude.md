# R21 orphan-guard DayZ-MCP — mitad Claude (workflow multi-ángulo, 30 agentes)

> Revisión adversarial independiente del feature orphan-proof (BUG-032). 5 dimensiones →
> verificación adversarial por hallazgo → consolidación. Cross-verificación host-direct de los
> FAIL de código por Claude (receptor) abajo. Pendiente: consolidar con la mitad Codex
> (`2026-06-23-orphan-guard-review-codex.md`).

## Veredicto: UNSOUND

El feature se vende como "orphan-proof por 2 mecanismos" pero **C1 está confirmado por 3 ángulos
independientes**: en la topología real (Claude Code → launcher venv A → server B) el watchdog vigila
A —que sobrevive a la muerte de Claude— y nunca dispara; el reclaim de la sesión siguiente ve
`parent_alive=True` y rehúsa recuperar. Ninguno previene NI recupera el orphan. Además, **dos FAIL de
código nuevos** (substring del discriminador + fail-OPEN del parent) y **tres FAIL de test** (los
integration pasan por vías que evitan la condición real). La suite 59/59 verde es consistente con un
feature roto en producción.

## Matriz consolidada (deduplicada por defecto real)

| ID | file:line | sev | resumen | fix |
|---|---|---|---|---|
| **C1** | orphan_guard.py:330,393 + server.py:564 + loopback.py:_bind_exclusive | **FAIL** | Deadlock liveness A↔B: watchdog vigila el launcher A que sobrevive a Claude → nunca dispara; reclaim ve A vivo → no recupera. Ni protege ni recupera. | Vigilar/discriminar el 1er ancestro NO-python (subir saltando el python.exe del venv), o registrar el python base sin launcher |
| **RCL-DISC** | orphan_guard.py:389 | **FAIL** | `"dayz_mcp" in cmdline.lower()` es substring del cmdline COMPLETO; el path del venv (`DayZ_MCP_dev`) ya lo contiene → cualquier python del venv lo satisface. **Verificado host-direct.** | Token discreto: exigir `-m dayz_mcp` adyacente (espejo de `_cmdline_targets_port`) |
| **RCL-FAILOPEN** | orphan_guard.py:435 | **FAIL** | `is_alive(ppid) if ppid else False` → ppid None (snapshot falla/race) ⇒ parent_alive=False ⇒ reclamable ⇒ puede matar instancia VIVA. Fail-OPEN, viola R6. **Verificado host-direct.** | `if ppid is None: return False` (indeterminación → no reclamar) |
| **TST-WD** | test_parent_watchdog.py:88-129 | **FAIL** | PASS falso: mata el launcher directo (proc.pid), nunca reproduce C1; el skip oculta el caso real. | Caso 3-niveles G→A→B matando solo G; `expectedFailure` mientras C1 abierto |
| **TST-RCL** | test_port_reclaim.py:200-205 | **FAIL** | Integration inyecta `is_alive=False`; con is_alive real el reclaim devuelve False bajo launcher → PASS falso. | xfail con is_alive real, o squatter sin capa launcher |
| **TST-COV** | tests/* | **FAIL** | Ningún test cubre el claim central "orphan-proof en topología launcher A→B"; el verde contradice C1. | Test 3-niveles asertando el comportamiento actual (no recupera) como contrato conocido-roto |
| WD-4 | server.py:551-558 + loopback.py:stop | WARN | Race TOCTOU stop_loopback sin lock entre hilo watchdog y finally del lifespan (benigno hoy por os._exit, frágil ante refactor). | flag `_shutting_down`/lock alrededor de stop_loopback |
| RCL-WMIC | orphan_guard.py:235-238 | WARN | `_wmic_command_line` devuelve solo la 1ª línea tras `CommandLine=` → pierde cmdlines multilínea (degradación: no reclama). | Acumular hasta siguiente clave/EOF, o Get-CimInstance primario |
| WD-3 | test_port_reclaim.py | WARN | Falta caso: is_alive real contra parent VIVO asertando que NO mata (preserva E4). | Squatter dayz_mcp de parent vivo → assert reclaim==False sin kill |
| RCL-PIDREUSE | orphan_guard.py:434-435,169 | PARTIAL | PID-reuse del parent no distinguido (degradación, no kill claro). | Comparar creation-time/handle en vez de re-resolver por número |
| RCL-TOCTOU | orphan_guard.py:424-453 | PARTIAL | PID fluye por ~4 snapshots (~200ms); kill por número sin handle estable (impacto acotado por las 4 puertas). | OpenProcess una vez y operar sobre ESE handle |
| Higiene | orphan_guard.py:114-122 (leak handle/hilo), loopback.py TOCTOU re-bind, exit-code os._exit vs SystemExit(2), `.sig` huérfanos en %TEMP%, netstat extra en test_instance_lock | NIT | Varios menores; ninguno bloqueante. | Ver detalle del workflow |

## Rejected (la verificación adversarial los descartó — anti-falso-positivo)

- **netstat locale**: FALSO. `netstat.exe` codifica `LISTENING`/`ESTABLISHED` en inglés en toda locale Windows (verificado en host es-ES: PID correcto). Los `ÉCOUTE`/`ÜBERWACHEN` son de `Get-NetTCPConnection`/Linux `ss`, no de netstat.exe.
- **squatter 0.0.0.0 sesgo primera-fila**: FALSO. Un squatter en `0.0.0.0:port` NO provoca EADDRINUSE al bind exclusivo `127.0.0.1:port` (verificado); nunca mata el PID equivocado por esa vía. ≤ cosmetic.

## 3 riesgos top para producción

1. **C1 — el feature NO funciona en la topología de despliegue real.** Confirmado por 3 ángulos. El único fix que ataca la causa: vigilar/discriminar el primer ancestro no-python, o eliminar la capa launcher (registrar el python base).
2. **RCL-DISC — discriminador "es nuestro" estructuralmente inválido** (substring de path). Combinado con RCL-FAILOPEN abre un vector real (estrecho) de matar un proceso ajeno del usuario.
3. **RCL-FAILOPEN — fail-OPEN ante indeterminación del parent**, contradice el fail-closed R6.

## Qué NO se pudo verificar offline (gate in-vivo)

- **End-to-end bajo el lanzamiento real de node/Claude Code** (no `subprocess.Popen`/`Start-Process`). Clave: ¿node mata a B vía job-object cascade al morir (→ el feature sería redundante, no roto), o B sobrevive reteniendo el puerto (→ C1 muerde aún más fuerte)? Hay que **matar Claude Code de verdad y observar si B sobrevive con el puerto, y si la sesión siguiente arranca o falla EADDRINUSE.**
- El fix de C1 una vez aplicado (walk de ancestros / python base) requiere re-test en topología real.
- RCL locale verificado solo en es-ES.
