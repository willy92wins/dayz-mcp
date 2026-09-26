# H9 launcher nativo — revisión adversarial de diseño R1

Fecha: 2026-07-22  
Revisor: subagente independiente `bug046_plan_adversarial` (Beauvoir)  
Objeto: propuesta A de `AI/10_Projects/DayZ_MCP/research/2026-07-22-h9-native-launcher-codex.md`  
Modo: sólo lectura; ningún proceso, daemon, DayZ, PowerShell ni cambio de código.

## Veredicto

La propuesta A —launcher Python neutral, `ControlClient`, PE MSVC dedicado y port Python de `dayz-test`— es la mejor dirección, pero no es implementable de forma segura mientras permanezcan cinco High.

**DESIGN RED**

## High 1 — Clausura Python incompleta

El registro v1 sólo acredita el PE registrado (`tools/dayz_mcp/secure_launcher.py:213-229,323-345`) y el handle fijado sólo protege ese archivo (`:280-310`). Fijar además `python.exe` y el módulo principal no cubre DLL, stdlib/site, `pyvenv.cfg`, imports transitivos, `lifecycle_cli` ni AddonBuilder. Código derivado no acreditado podría ejecutar con el lease.

Cambio requerido:

- consumidor `.pyz` cerrado;
- Python con argv fijo `-I -B -S`, cwd seguro, entorno mínimo y cero `PYTHON*`;
- el PE verifica y mantiene handles sin `FILE_SHARE_WRITE|FILE_SHARE_DELETE` de toda la clausura ejecutable hasta finalizar el hijo;
- sin pass-through de argv ni imports desde cwd.

## High 2 — Arranque y cancelación contradicen los gates actuales

`run_secure_launcher` no crea procesos (`secure_launcher.py:313-320`) y el containment vigente prohíbe `Popen`, `run` y `CreateProcess*` (`tools/tests/test_task9_launcher_migration.py:35-98`). P0.S también intercepta FFI/launch en su clausura (`plans/2026-07-21-p0s-native-lifecycle-security-plan.md:617-626`). R1 no define la excepción mínima ni cómo impedir PE/Python/AddonBuilder huérfanos.

Cambio requerido:

- una sola superficie native-launch auditada, absoluta, sin shell y con backend inyectable en tests;
- pipe de liveness del padre;
- Python creado suspendido, asignado a Job Object sin breakaway y reanudado después;
- en toda salida: detener heartbeat, cerrar/esperar sólo el worker propio, liberar lease y verificar `session_status`;
- nunca terminar DayZ directamente;
- sustituir el veto temporal por una allowlist estructural exacta, sin relajar genéricamente el auditor.

## High 3 — Entrada y secretos sin contrato cerrado

La CLI sólo admite `launcher_id` y `max_wait_s` (`secure_launcher.py:442-457`; `tools/tests/test_secure_launcher.py:21-45,264-290`), pero la plantilla histórica tiene cuatro modos y 22 opciones (`C:/Users/guill/.claude/skills/dayz-test-ingame/templates/dayz-test.ps1:36-60`). El request JSON sigue siendo una suposición. Además `AdminPass` entra por parámetro y se imprime (`dayz-test.ps1:49-50,335-348`), incompatible con el contrato de secretos.

Cambio requerido:

- schema JSON cerrado, acotado, sin extras ni duplicados, fijado antes del lease y transmitido por stdin/pipe o archivo abierto/fijado;
- cero pass-through de argv;
- identidad, key, token y credenciales prohibidos en el schema;
- eliminar `AdminPass` de la superficie pública o leerlo de un almacén local aprobado sin imprimirlo;
- environment blocks construidos desde allowlist, nunca `os.environ.copy()`;
- PE/Python/lifecycle sólo reciben variables imprescindibles; AddonBuilder y otros hijos nunca reciben identidad/token;
- consumer captura y elimina ambas variables inmediatamente y sólo las repone alrededor de lifecycle;
- tests buscan secretos fragmentados en argv, requests, stdout/stderr UTF-8/UTF-16, artefactos y entornos.

## High 4 — Port completo sin sustitutos nativos definidos

La plantilla llama `cmd /c mklink` (`dayz-test.ps1:369-375`), AddonBuilder (`:420-454`) y usa cmdlets de proceso/UDP para readiness (`:488-549`). Todo debe desaparecer, pero R1 no define reemplazos ni su autoridad. También faltan fixtures completos para VPP, cfg self-heal, staging stale, pack-only, modos/run_id y retail.

Cambio requerido:

- no crear junction automáticamente: validar la exacta y fallar cerrado, salvo diseño nativo explícito;
- AddonBuilder procedente de manifest aprobado, PE x64 fijado por handle/SHA, argv-list, entorno limpio y Job Object del worker;
- readiness ligada al PID del run lifecycle exacto mediante API nativa/psutil fijado, nunca por nombre ni puerto aislado;
- matriz RED/GREEN completa de modos, switches, side effects, exit codes y requests lifecycle;
- `.ps1` sólo como especificación estática, nunca ejecutado.

## High 5 — Procedencia, build y rollback abiertos

El launcher sólo exige extensión `.exe` y hash; todavía no valida MZ/PE/x64 ni Authenticode (`secure_launcher.py:289-299,389-400`). R1 no adjudica firma ni migración del registro.

Cambio requerido:

- preferir registry v1 sin cambio persistente; compilar en el PE el hash de un manifest de clausura, de modo que cualquier cambio fuerce rebuild del PE y actualización de su SHA registrado;
- si se elige v2: especificar legacy, backup y rollback fail-closed;
- build reproducible con fuente/toolchain/flags/hashes fijados y mitigaciones `/GS`, `/DYNAMICBASE`, `/NXCOMPAT`, `/HIGHENTROPYVA`, `/guard:cf`, `/Brepro`;
- adjudicar sin fallback entre Authenticode cache-only con signer aprobado o PE local reproducible unsigned autorizado explícitamente por build/test manifest+SHA.

## Comparación

- A sigue siendo la recomendación sólo después de cerrar los cinco High.
- B (todo C++) reduce Python pero multiplica drift y duplica ControlClient/lifecycle contra P0.S.
- C (`python.exe` directo) deja un intérprete general registrado; endurecida correctamente converge hacia A.

La extracción de `ControlClient` es prerequisito correcto si conserva literalmente `plans/2026-07-21-p0s-native-lifecycle-security-plan.md:431-477` y acredita el owner del socket antes de transmitir key/identidad/lease. No puede reutilizar el acquire/wait antiguo de `LFV_D2_Executor/lfv_executor/dayz.py:276-317`.

## Gate de re-revisión

Revisar de nuevo sólo cuando el diseño R2 cierre expresamente: clausura ejecutable completa, única superficie de launch/Job Object, schema de entrada y almacén de credenciales, sustitutos nativos/paridad, y política reproducible de procedencia/rollback. `DESIGN GREEN` exige cero Critical/High.
