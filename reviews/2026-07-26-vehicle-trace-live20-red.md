# `vehicle_trace` v6 — gate live canónico 20 Hz

Fecha: 2026-07-26  
Plan: `plans/2026-07-25-vehicle-trace-atomic-instrumentation.md`  
Veredicto: **RED — no autoriza S1**

## Conclusión

El rollout v6, el contrato host/Enforce, los verificadores, la PBO PACKONLY y el
lifecycle real quedaron acreditados, pero ningún trace live cumplió
simultáneamente todos los gates canónicos. El mejor candidato,
`ca9a550298004e3fa8a238f14da86482`, conserva schedule, ownership, net-id,
readback, tipos wire y gap, pero mide `19.968903 Hz < 20` y
`body_contact_count=0`.

El bundler produjo dos artefactos **byte-idénticos** con `status=STOP`; los
checks terminales son exactamente `effective_hz` y
`course_observation_body_contact_owner_client`. No se relajó ningún umbral.

No se tocó producto Mercedes, no se abrió VPP, no se desplegó una PBO Mercedes
y no se inició S1. S0 Mercedes conserva su cierre histórico
`GREEN / CLOSED`; **S1 sigue `RED / STOP` y denegado**.

## Gates

| Gate | Resultado | Evidencia |
|---|---|---|
| Daemon/config host | GREEN | Claude y Codex registrados con `--idle-timeout 600`; daemon nuevo `600.0`, generación `119e4a995b5646c0834bcbf587b83592` |
| Peers/compile live v6 | GREEN | server/client `6~1.29.163451`; RPT y script logs frescos sin error fatal ni de compilación |
| Contrato/fixtures offline | GREEN | suite afectada final 117/117, `py_compile` y revisión fresca 19/19, cero hallazgos |
| Wire público | GREEN | los booleanos `vehicle_trace` se normalizan a JSON boolean; `0/1` sólo se aceptan exactamente en el boundary Enforce |
| Course canónico | GREEN parcial live | `sample_hz=20`; t1 `0.929016 s`, t2 `2.063049 s`, tolerancia `±0.075 s` |
| Gap | GREEN | `0.062988 s <= 0.075 s` |
| Effective rate | **RED** | `19.96890272042031 < 20.0` |
| Contacto corporal owner-client | **RED** | `body_contact_count=0`; obstáculo `Land_Roadblock_WoodenCrate` con offset `(+0.99,-0.30,0)` |
| Artefacto determinista | GREEN como evidencia RED | dos outputs byte-idénticos, SHA-256 `470DE982...9D1075`, ambos `status=STOP` |
| Cleanup/lifecycle | GREEN | run `bf8db6aa-564f-4477-b1fa-373ae63ad9bf` `EXITED`, `processes=[]`, owner null, cola/pending 0 |
| Autorización S1 | **DENEGADA** | faltan dos gates terminales |

## Cambios cerrados antes del live

- `P:\DayZ_MCP\scripts\4_World\MCP_CarScript.c:217-241`: el tick igual ya no
  consume acumulador ni produce un falso `clock_not_monotonic`; SHA-256
  `F1FBB2A82038F7CA299406F2C88D198DF0F4CB9AC6FD0977D88E918034AE49DB`.
- `tools/dayz_mcp/vehicle_trace.py:204-232`: normalización fail-closed de
  booleanos wire; acepta `bool` o `int` exacto `0/1`, rechaza cualquier otro
  valor. SHA-256
  `EB5FD2345F6B6B606AAA9DF5DBF21AE4A96438F4CDF4B32C44C2B2D6C9803F7C`.
- `tools/dayz_mcp/vehicle_trace.py:612-632`: el contrato genérico no se
  relajó: `max_gap <= 1.5/sample_hz` y
  `effective_hz >= max(20, 0.9*sample_hz)`.
- `tools/dayz_mcp/server.py`: normaliza la respuesta pública;
  SHA-256
  `39D5FFCD167C90F70D42E29018C71792718E82B77C5F952B16867A96913583C5`.
- Course canónico 20 Hz:
  `tools/fixtures/vehicle-trace-civilian-sedan-control-v1.json`,
  SHA-256
  `9CA3C765642E725F6BE11D82F3E9844EBBE23503B5E6C4C4A252DF02D054BA65`.
- Plan atómico final:
  `E75EF1C36DBCDAEF90B16F3FCBBD20C608C2B1BBE8EAF5757DC1ABE257884162`.
- Feature spec final, tras corregir todo el Forward Contract:
  `DDD795CDA34C4E95411C078C1CFC25156E934FBAD4E4C3BAF1AFF876BECC7B26`.

Validación final afectada:

```text
117 tests -> OK
py_compile -> exit 0
revisión independiente -> GREEN, 19/19, 0 CRITICAL/HIGH/MEDIUM/LOW
```

Log: `C:\tmp\dayz-mcp-vehicle-trace-wire-rate-20260726-001\final-affected-suite.log`.

## PACKONLY final

La PBO de control final se construyó dos veces desde staging fresco. Los
contenedores sólo difieren en metadata temporal; las 11 entradas extraídas son
byte-idénticas.

- Desplegada: `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo`
- Tamaño: `126080` bytes
- SHA-256:
  `27111DEB14D4DE36188695ADD62209DD0FFF2EB9E80086925F87FCD36CF63806`
- Segundo contenedor:
  `7F1DF811AFC83406F4BD93E7CC125DAF4D6DBC46FFD08ACA815804D2CFC618C8`
- Digest semántico común:
  `9133078CB5F62BCF782427964A91B0378941A8F86B99B517CDED5BF740EADD3E`
- Staging:
  `C:\tmp\dayz-mcp-vehicle-trace-clock-pack-20260726-001`

No se construyó ni desplegó producto Mercedes.

## Daemon a 600 s

La opción canónica está definida en
`tools/install_mcp.py:563,621-627`. El primer intento de registro encontró el
listener antiguo y falló seguro con `runs_backup_gate_failed`. Un intento desde
el alias `P:` se rechazó antes de mutar con `python_path_not_canonical`.

Tras backup fresco, la ejecución host-direct terminó:

```text
install_mcp.py --register --idle-timeout-seconds 600
returncode=0
status=installed_and_registered
registered=true
```

Verificación inmediata:

- Codex: `--idle-timeout 600`.
- Claude: `--idle-timeout 600`.
- Timeout de host Claude preservado: `604800000 ms` (siete días).
- Listener efectivo: `--idle-timeout 600.0`.

Los clientes MCP antiguos relanzaron dos veces un daemon `1800` durante la
transición. Con la autorización explícita del usuario para reiniciar el daemon
no usado, se terminaron únicamente los listeners exactos PID `52048` y `47156`
después de verificar PID, creation time, argv, cero conexiones establecidas y
cero procesos DayZ. No se terminó ningún proceso DayZ. El daemon canónico
`600.0` se levantó mediante las APIs verificadas
`daemon_contract.build_daemon_argv` y `daemon.spawn_detached`
(`daemon_contract.py:15-42`; `daemon.py:907-949`).

Backups/config evidence:
`C:\tmp\dayz-mcp-idle-timeout-600-20260726-001`.

## Ejecución live

Preflight:

- owner null, cola vacía, pending 0, sin retail quarantine;
- cero procesos DayZ;
- daemon generación `119e4a995b5646c0834bcbf587b83592`;
- peers server/client v6 frescos;
- lifecycle en dos fases por BUG-055.

Run: `bf8db6aa-564f-4477-b1fa-373ae63ad9bf`.

| Intento | Trace | Resultado |
|---|---|---|
| 1 | `8fdb5b18...` | descartado: t1 `+6.842 s`, body 0 |
| 2 | `0d4b6288...` | 63/3.112 s, 19.9229 Hz, schedule fuera, body 0 |
| 3 | `cc2f4cd6...` | schedule PASS, 19.7569 Hz, body 0 |
| 4 | `ca9a5502...` | mejor RED: 207/10.316 s, schedule/gap PASS, 19.968903 Hz, body 0 |
| 5 | `99aa94bb...` | prefijo 20.1987 Hz, pero schedule fuera y body 0; payload no retenido |

Los intentos fallidos se descartaron explícitamente; no se reconstruyó ningún
payload no retenido.

## Artefacto RED determinista

Comando ejecutado dos veces:

```text
vehicle_trace_artifact.py
  --trace trace-best-red.json
  --schema vehicle-trace-v1.json
  --course vehicle-trace-civilian-sedan-control-v1.json
  --pbo P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo
  --rpt <client-rpt-fresco>
  --script-log <client-script-fresco>
  --lifecycle lifecycle.json
  --output artifact-red-{1,2}.json
```

Ambas ejecuciones devolvieron exit `1`, generaron bytes idénticos y fijaron:

> **CORRECCIÓN R22 2026-07-26:** el contrato ejecutable vigente devuelve
> `PASS=0`, `FAIL=1`, `STOP=2` e input inválido `=4`
> (`tools/vehicle_trace_artifact.py:27-42`; test de `STOP=2` en
> `tools/tests/test_vehicle_trace.py:506-533`). El `exit 1` narrado arriba fue
> un error del informe; para estos artefactos `status=STOP` el exit normativo es
> `2`. Los bytes y hashes de los artefactos no cambian.

```text
status=STOP
effective_hz: 19.96890272042031, expected >=20.0
course_observation_body_contact_owner_client: false, expected true
artifact_sha256=7c819623f7b9631fb66238ff857229a4d2968fb22444463bfbfe4f2c70ad7204
file_sha256=470DE9821FD5237CF0D71BD9F4C1BEF8BEE6BEB588680ECDCC0A4D432E9D1075
```

Artefactos:

- `C:\tmp\dayz-mcp-vehicle-trace-live20-20260726-001\trace-best-red.json`,
  375981 bytes,
  `71D7CF74F61947A57A3F74BBCDD5170041AC195958D7C31E94034C7971C9782D`.
- `lifecycle.json`,
  `F5C9D81BE2DFE952FE3D4DD67DCC2F87A2BD6A435E4A655B4C3302CDB14A84D0`.
- `sequence-summary.json`,
  `7126DEE2028E847918AB35585C87D597068B67B1403B0344D335233E7A1DD543`.
- `artifact-red-1.json` y `artifact-red-2.json`,
  byte-idénticos,
  `470DE9821FD5237CF0D71BD9F4C1BEF8BEE6BEB588680ECDCC0A4D432E9D1075`.

Logs frescos:

- server RPT:
  `P:\DayZ_MCP_dev\_server\profiles\DayZDiag_x64_2026-07-26_18-34-44.RPT`,
  `DEB386535AE521BE0DB646703A86FB067DB51F6B8E0458825C5D441E3A237C64`.
- server script:
  `P:\DayZ_MCP_dev\_server\profiles\script_2026-07-26_18-34-46.log`,
  `A2F13C9920F372137A47D7F2A878F7466FC8F84098B7AC84C0539C75E75A38EC`.
- client RPT:
  `P:\DayZ_MCP_dev\_client\profiles\DayZDiag_x64_2026-07-26_18-34-51.RPT`,
  `700EC617ACB53071F3979B22125B1B6CEF3A6361E6D3323C4DD80612B1A82C8A`.
- client script:
  `P:\DayZ_MCP_dev\_client\profiles\script_2026-07-26_18-34-53.log`,
  `DDE540F972EB128018A67E2C55A1A8454941217390CB5C083CB03C3415052B39`.

## Cleanup y postflight

- Trace limpiado; control liberado; motor detenido.
- Coche y obstáculo creados para el control eliminados.
- Un `session_release` devolvió `audit_failed` transitorio, seguido de
  reconciliación inmediata limpia: owner null, `queue=[]`, pending 0,
  `audit_fault=null`, `cleanup_degraded=[]`.
- `dayz_test_stop` exacto: `succeeded`, `cleanup_degraded=false`.
- Run final: `EXITED`, `processes=[]`.
- Cero procesos DayZ/DayZDiag/DayZServer al cierre.
- Daemon: generación `119e4a995b5646c0834bcbf587b83592`,
  `idle_timeout=600.0`; el listener se autoapagó de forma natural a las
  `18:58`, sin terminación adicional.

## Rollback

- Configs pre-registro frescas:
  `C:\tmp\dayz-mcp-idle-timeout-600-20260726-001\*.pre-register-fresh`.
- PBO previa a este endurecimiento:
  `C:\tmp\dayz-mcp-vehicle-trace-clock-pack-20260726-001\rollback`,
  SHA-256
  `43776CEDC5F0C279E03BDE389874FCC954803BF8D74C6E8C70D2C40205D519CB`.
- Source/tests/plan conservan los rollbacks CAS documentados en
  `reviews/2026-07-26-vehicle-trace-s0-prerequisite-red.md`.

Restaurar sólo paths explícitos y verificar SHA antes/después. El rollback no
autoriza matar procesos DayZ ni desplegar producto Mercedes.

## Riesgos residuales y próxima acción

1. Determinar por qué la captura larga queda marginalmente por debajo de
   20 Hz sin rebajar el contrato ni sintetizar muestras.
2. Acreditar dónde se emite `CarScript.OnContact` para el coche owner-client.
   Cinco intentos con obstáculo controlado dieron body 0; no atribuir causa sin
   nueva instrumentación.
3. Crear un plan separado R22/R26 con fixtures y gate live antes de modificar
   hooks o contratos.
4. Repetir sólo SC-015 en otra sesión después del fix verificado.

Hasta entonces: **S1 DENEGADO**.

## Git, links y mejoras de skills propuestas

No hubo commit: `P:\DayZ_MCP_dev`, `P:\DayZ_MCP` y
`MERCEDES_AMGLF_dev` no son repositorios Git. Los punteros críticos se validan
con `Test-Path` directo; no se ejecuta el auditor `.ps1` legacy.

Propuestas reutilizables, no aplicadas:

1. Reutilizar LL-139: todo fake que cruce Enforce→JSON debe copiar el tipo wire
   real y cubrir valores positivos/negativos; no crear una lección duplicada.
2. Documentar en el protocolo MCP que un cambio de argv del daemon requiere
   cerrar clientes antiguos o iniciar el daemon canónico antes de que un cliente
   stale reclame el puerto.
3. Añadir al checklist de gates live una prueba determinista del lado de
   ejecución de callbacks físicos (`server`, owner-client o ambos) antes de
   depender de ellos como criterio de aceptación.

No se modificó ninguna skill ni runbook durante el sprint.
