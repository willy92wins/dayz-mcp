# Cierre `vehicle_trace` v6 — prerrequisito Mercedes

Fecha: 2026-07-26  
Plan: `plans/2026-07-25-vehicle-trace-atomic-instrumentation.md`  
Veredicto: **RED — no autoriza S1**

## Conclusión

La instrumentación `vehicle_trace` queda cerrada en código, tests, policy,
launcher y PBO de control, pero el gate terminal sigue **RED**. El daemon
standalone vivo pertenece a la generación
`52947a7ba59d4b9b970137bf4ff32415` y continúa publicando bridge v5; no existe
una operación soportada de hot-reload/restart del daemon y no se mató ningún
proceso. Por tanto no se puede ejecutar sin mezcla v5/v6 el control live sobre
`CivilianSedan`, acreditar `OnContact` owner-client ni producir el artefacto
real de SC-012/SC-013.

No se lanzó Mercedes, no se abrió VPP, no se inició S1 y no se modificó producto
Mercedes.

El rehash final de los dos pines upstream tampoco coincide ya con el prompt:
plan Mercedes
`9B76EA4EB12158B8B275717FE669360D6D381D1DEC5402E4E27F4D49F2F9494C`
frente a `DB27...CACA`, e informe R21
`59400127A00C851566D587FC6EA924F1BD27463836177951AE57BB8CF04A2EEE`
frente a `3B7B...0446`. Ambos archivos conservan timestamps del 2026-07-24 y no
fueron editados en este cierre. El usuario autorizó explícitamente modificar el
plan durante la sesión; el drift queda registrado y es un motivo adicional para
no autorizar S1.

## Gates

| Gate | Resultado | Evidencia |
|---|---|---|
| DPF/spec/R22/R26 | GREEN | `product-spec.md` G3; feature spec, checklist y plan atómico revisados |
| Policy `DayZ_MCP` exacta | GREEN | RED `bad_project` 9 tests/1 failure → GREEN 9/9; proyecto no listado sigue fail-closed |
| Launcher nativo sellado | GREEN | tres builds reproducibles, suites 120/120 y 101/101, instalación CAS + receipt |
| Bridge/tool v6 offline | GREEN | Python v6 y Enforce v6; contratos y fixtures RED→GREEN |
| Revisión independiente | GREEN | 0 CRITICAL, 0 HIGH tras dos falsos verdes del course corregidos |
| Suite focal/afectada | GREEN | 94/94 y 241/241 |
| PBO PACKONLY | GREEN semántico | 11 entradas source↔dos PBO byte-idénticas por entrada; contenedor difiere sólo en timestamp |
| Compile Enforce de los bytes finales | RED / NOT RUN | el compile v6 previo fue GREEN, pero antecede al ajuste del acumulador |
| Bridge vivo v6 | RED | daemon vivo continúa en `server_version="5"` |
| Control live + `OnContact` | RED / NOT RUN | prohibido mezclar v5/v6; no hubo launch nuevo |
| Artefacto live determinista | RED / no producido | faltan trace/RPT/script-log/lifecycle del run exacto |
| Cleanup final | GREEN con degradación transitoria registrada | owner nulo, cola vacía y pending 0; varios releases devolvieron `audit_failed` transitorio y el status inmediato quedó limpio |

## Policy y launcher sellado

El test conductual se creó antes del cambio:

- RED:
  `pytest tools/tests/test_build_native_launcher_policy.py`
  → 9 tests, 1 failure exacto `bad_project`.
- GREEN:
  la misma selección → 9/9.
- Control negativo:
  `DayZ_MCP_OutsidePolicy` continúa rechazado.

Artefactos:

- `tdd-red-policy.xml`:
  SHA-256 `51AD6913FBD1F40C49F195CBCBD54A9B4E653B291DA8A7FFC0CB5B5C008E54C1`.
- `tdd-green-policy.xml`:
  SHA-256 `0B630DFCE3545A975565A2BE7C35FDBC773B9A34DB22AEA580C6D484518C1AF6`.
- suite nativa de seguridad: 120/120, SHA-256
  `3CED9B2E2E893477AFA0BBAE96EC3A5C2D4EEDD0FE7B657AADC9367136829466`.
- re-run nativo final: 101/101.

El builder contiene una única identidad `DayZ_MCP` con
`dev_root=P:\DayZ_MCP_dev`, `default_source=P:\DayZ_MCP`, cero base mods,
mission canónica y `mod_roots=[P:\Mods]`
(`tools/build_native_launcher.py:430-438,584-592`).

Bundle instalado:

- PE:
  `FE1ED970E3589B2A2A67CDB65C1EF68E9C79965BB84965D6B6130A52BCB45DE7`.
- request policy:
  `BD7C8463F89C5D2EA6C9F7D1A416CF74983CE1AC4949FFA661C5E08FDAA49EAF`.
- worker runtime:
  `96BA258ED42018E3C3ED8AE80E8525FBFEC6103CCA9638DCC58653600586C113`.
- closure manifest:
  `3C1874643E1E0040858D98CCE4562A7567EEFA35D1A6ADA627985E52D38175E1`.
- app.pyz:
  `66FA7163B699101AC81CE89753751D0F0C765F1581D990D418A8D1BFDB184BB6`.
- registry:
  `921D922FA0605B952E0BA4E6C13D691335D9D852B81E0051F290F90B36D78254`.
- receipt:
  `tools/approved-launchers.receipts/68f9059c-c657-4e2a-bda3-22c596d9bee3/committed.json`,
  SHA-256 `7A719B49A067831A2A4DC08F85BD370FCEE904B51F3C99B657A6B6DACE428821`.

## RED → GREEN de `vehicle_trace`

El contrato público exacto está en
`tools/dayz_mcp/vehicle_trace.py`; el bridge v6 se fija en
`tools/dayz_mcp/core.py` y `scripts/5_Mission/MCPMessages.c:1`.

Los fixtures demostraron, antes de código, feature ausente, campos faltantes,
gap/reloj/cadencia, readback divergente, overflow, UUID/modos/cursor/limit,
tamper de spinout/rollover/grounding, lease/cleanup y contrato Enforce.

La revisión independiente encontró seis HIGH y dos variantes adicionales del
falso verde del course. Todos quedaron cerrados:

1. El acumulador Enforce conserva el resto fraccional; `dt=0,025` a 30 Hz
   produce al menos 27 Hz.
2. El course valida forma exacta, finitud, rangos, orden y duración; cada
   transición debe aparecer en requested+applied.
3. El `course_id` queda ligado al schedule canónico inmutable
   (`vehicle_trace.py:142,892-908`); course+trace todo-cero termina
   `course_controls_canonical=STOP`.
4. Tipos enteros inválidos terminan STOP/exit 2 sin excepción Python.
5. `vehicle_release` conserva la obligación de cleanup hasta resultado exitoso
   (`session_coordination.py:1508`).
6. Cada check materializa `evidence`; schema/fixtures contienen metadata read y
   los tres tampers derivados.

Secuencia final:

- course malformado contra host previo: 10 tests, 1 failure causal.
- corrección en staging: 10/10 y 94/94.
- course+trace todo-cero contra host previo: 10 tests, 1 failure causal.
- corrección final en staging: 10/10 y 94/94.
- host tras dos copias CAS: 94/94 y 241/241.
- `py_compile` sobre los ocho `.py` modificados/afectados: exit 0.
- revisión Codex fresca, read-only: 0 CRITICAL, 0 HIGH; focal independiente
  16/16.

Hashes finales principales:

- `vehicle_trace.py`:
  `12BCB1BEEE921E794D98938310D0990DBD511A7BE83D14BF11DFFC3F7E6CDFA1`.
- `test_vehicle_trace.py`:
  `A081369934F128D504AFC61FE898917DB45F99E548F0ABDFFF4C0B6B266B491C`.
- schema:
  `D5C66985987362D3C6991FCEFF47EE396CD839150F90CDD6D459D13D3831CF88`.
- course:
  `F89E781FE093F8077F24EF59EDA2B422F9567560E3756C98E3DC21ED653C6946`.
- fixture positiva:
  `A27F74730567FB5BF7FF4A349D5278F9B799E599995055BF50C991E5650634AF`.
- mutaciones negativas:
  `94854713809FEFF79B7F8956596914139940D08F120416881B729E3856E6CBE9`.
- `MCP_CarScript.c`:
  `9BC3EC801129DF7BF947DC81AF2E1CC8A0E20FE0280B9E8209E2BFD870B4A3E1`.
- `MCPClientBridge.c`:
  `04B01FE60A67015EB86F44C667993B03516F3D318F507E569CF405BEC33D09CC`.

## Compile y PACKONLY

El primer run v6, `156e1929-d4fd-4320-9972-d8abc22e1619`, falló en compile
por un `if (` multiline de Enforce. Se demostró RED, se contrajo la expresión a
la sintaxis aceptada y el segundo run compiló todos los módulos `DayZ_MCP`.
Sólo permaneció el stack trace vanilla `PluginConfigDebugProfile`. El run se
detuvo por lifecycle exacto.

Después, la revisión corrigió el acumulador. No se relanzó DayZ: el compile
engine de esos bytes finales queda **NOT RUN** y mantiene el gate RED.

PACKONLY final, sin launch:

```text
"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\AddonBuilder.exe"
  <staging>\DayZ_MCP <staging>\output
  -temp=<staging>\addonbuilder-temp -clear -packonly
```

- build 1: 125883 bytes,
  `43776CEDC5F0C279E03BDE389874FCC954803BF8D74C6E8C70D2C40205D519CB`.
- build 2: 125883 bytes,
  `0EC25A1C243202E878056A6E3AFF253DD5F78284CEAA2458BD0F39E4E89B7901`.
- `BankRev -diff`: única diferencia `U (time) DayZ_MCP\config.cpp`.
- extracción fresca: las 11 entradas de source/build1/build2 son
  byte-idénticas.
- manifest semántico común:
  `5B5FC8E7A446461ACAE16F5CB4483E5860ABF489D8BB53C2F0AD2DE648C181AA`.
- PBO de control desplegada:
  `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo`, hash del build 1.

Incidente de verificación: un primer comando buscó erróneamente
`output\Addons`, obtuvo dos hashes nulos y publicó `ByteIdentical=True`. Se
detectó inmediatamente, se invalidó ese resultado y la repetición añadió
`Test-Path` obligatorio antes de hashear. Ningún gate depende del falso verde.

## Baseline global no verde

El discover posterior ejecutó 1227 tests en 225,749 s:
11 failures, 31 errors y 4 skips. Log:
`C:\tmp\dayz-mcp-policy-20260726-001\full-suite-post-enforce.txt`,
SHA-256 `31E3496DD7F0727F64744AC4BEA6B6E058EBD4FA4FAA3974BEB68CAD173B9B0A`.

El baseline H12 era 1224 tests, 14 failures, 29 errors y 4 skips. Los dos errors
adicionales pertenecen a fixtures legacy `SimpleNamespace`/lifecycle, no al
delta `vehicle_trace`. Al ampliar por encima del conjunto afectado también se
reprodujo aisladamente
`test_task7_final_authority_regressions...ClientRuntime._control`; no se corrigió
de forma incidental.

## Rollback

- Policy/launcher:
  `C:\tmp\dayz-mcp-policy-20260726-001\rollback\`.
- PBO previa al primer PACKONLY:
  105616 bytes,
  `23F06900AFE2E6A7E78AEBA2B021A903EDC845B9822A84AC0DC1C4157003CFB2`.
- PBO previa a la revisión:
  `C:\tmp\dayz-mcp-vehicle-trace-reviewfix-20260726-001\rollback\deployed-pbo-before-reviewfix\DayZ_MCP.pbo`,
  125804 bytes,
  `992BC6F952A037E77C0C2AF0D5F244BBD37C82EB67A0B01AC68B1D47AC2F3DAD`.
- Código/tests/plan pre-revisión:
  `C:\tmp\dayz-mcp-vehicle-trace-reviewfix-20260726-001\rollback\`.
- Las dos correcciones del course tienen rollbacks CAS independientes:
  `rollback\h2-course-schedule\` y `rollback\h2-canonical-schedule\`.

Restaurar sólo paths explícitos y verificar el SHA esperado antes y después. El
rollback de launcher usa `rollback-last` → CAS del registro → instalación
transaccional; no requiere ni autoriza matar procesos.

## Archivos cambiados

- DPF/spec/plan de `vehicle_trace`.
- `tools/build_native_launcher.py`, tests de policy/bundle y pines derivados.
- `tools/dayz_mcp/{core,server,loopback,session_coordination,vehicle_trace}.py`.
- `tools/vehicle_trace_artifact.py`, schema, course, fixtures y cuatro tests
  focales.
- `DayZ_MCP/scripts/4_World/MCP_CarScript.c`.
- `DayZ_MCP/scripts/5_Mission/{MCPMessages,MCPClientBridge}.c`.
- bundle nativo derivado, registro, receipt y PBO de control.
- este informe, HANDOFF y memoria durable.

No se tocó `MCPBridge.c`, config/modelo de DayZ, producto Mercedes, otro mod ni
skills/runbooks.

## Riesgos residuales y próxima acción

1. Esperar una generación nueva soportada del daemon que cargue Python v6; no
   usar kill ni hot-patch no soportado.
2. Confirmar `bridge_status` con server/client v6 frescos.
3. Ejecutar un único run aprobado con `CivilianSedan`, trace ≥2 s a 30 Hz,
   ownership estable y `OnContact` corporal.
4. Generar dos veces el artefacto con trace/schema/course/PBO/RPT/script-log y
   lifecycle del run exacto; exigir bytes idénticos.
5. Liberar control/lease y acreditar cola, run y procesos limpios.

Hasta cerrar los cinco puntos: **S1 DENEGADO**.

## Postflight final

- `session_status`: owner `null`, queue `[]`, self `none`, claimable, pending
  commands `0`, audit/recovery faults `null`, cleanup degraded `[]`, tombstones
  0 y daemon generation
  `52947a7ba59d4b9b970137bf4ff32415`.
- `bridge_status`: server/client `5~1.29.163451`, `server_version="5"`,
  queue depth 0, results pending 0; polls stale ~44522/~44636 s. El estado
  `version_state=ok` sólo significa que el daemon v5 acepta esos peers v5, no
  que el source v6 esté cargado.
- Lifecycle: run Mercedes
  `0540d268-9705-4e68-9de2-6305642bea50` `EXITED`, owner null,
  `processes=[]`; run `DayZ_MCP`
  `156e1929-d4fd-4320-9972-d8abc22e1619` `EXITED`, owner null,
  `processes=[]`.
- Escaneo Win32: cero `DayZ`, `DayZDiag` o `DayZServer`; existe un
  `DZSALauncher.exe` externo PID 39264, no registrado como run y no tocado.
- No queda lease propia. No se ejecutó kill, stop por PID, deploy candidato,
  VPP ni prueba live.
- `Test-Path` directo sobre los 14 punteros nuevos/críticos: 14/14 PASS.

## Git, links y mejoras de skills propuestas

No hubo commit: `P:\DayZ_MCP_dev` no es un repositorio Git
(`fatal: not a git repository`).

El auditor legacy de links no se ejecutó por la policy vigente contra `.ps1`;
los links nuevos se comprueban directamente con `Test-Path`.

Propuestas reutilizables, no aplicadas:

1. `dayz-test-ingame`: aclarar que `pack_only=true` sólo pasa `-packonly` a
   AddonBuilder; `dayz_test_run` sigue lanzando lifecycle.
2. Checklist Enforce: registrar que este parser rechaza `if (` con la expresión
   en la línea siguiente; compile RED y contracción a una línea GREEN.
3. Protocolo MCP: un bump de bridge exige generación fresca coordinada del
   daemon; no hay hot reload y nunca se mata el proceso.
4. Verificación PACKONLY: exigir existencia de cada path antes de comparar
   hashes; dos valores nulos iguales no son evidencia.

Drift observado: el path catalogado de
`superpowers:verification-before-completion` 6.1.1 no existía; se leyó y aplicó
el fallback 6.2.0. No se modificó ninguna skill.
