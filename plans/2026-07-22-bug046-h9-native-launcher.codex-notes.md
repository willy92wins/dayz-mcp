# BUG-046 / H9 — notas Codex previas a implementar A7

Fecha: 2026-07-22  
Estado: **BLOCKED / PHASE1 RED**

## Problema 1 — aprobación y atestación no cubren A7

El plan actual tiene SHA-256
`A9F54A7D8B155ED22C35BC0DA4DC9049C661D101A21D5B9FAA505DE2A37294A6`,
pero su header acredita otro digest (`plans/2026-07-22-bug046-h9-native-launcher-plan.md:4`).
La aprobación humana recibida fue `apruebo A4`; A7 añadió H4/H10, bootstrap,
file-identity, debugger/nested jobs y recovery durable.

Impacto: implementar A7 ahora eludiría el gate R22 y no tendría trazabilidad exacta.

Actualización recomendada: aprobación humana literal de A7; luego retirar el
self-hash del header, congelar bytes y emitir una atestación detached create-only.

## Problema 2 — la Fase 1 concurrente viola invariantes del plan

Hallazgos reproducidos y evidencia completa:
`reviews/2026-07-22-h9-phase1-concurrent-adversarial.md`, SHA-256
`FDC223080FB384F176AFC1DF1E708574D9529B26B1DA7DA177EBC5CD8556A102`.

Bloqueantes principales:

- cleanup antiguo cancelado puede cerrar el estado de un ticket nuevo
  (`tools/dayz_mcp/control_client.py:388-429`);
- request policy lexical acepta autorizar `P:\` completo
  (`tools/dayz_mcp/dayz_test_request.py:108-219`);
- bootstrap recibe policy/liveness, pero no acredita controller/EOF/procedencia
  (`tools/dayz_mcp/daemon_policy.py:206-460`);
- socket accreditation no autentica bytes/build antes de formar la key
  (`tools/dayz_mcp/accredited_daemon_transport.py:115-287` y
  `tools/dayz_mcp/native_process_guard.py:70-93`);
- la tool pública aún fuerza timeout a 1800 s
  (`tools/dayz_mcp/server.py:33,781-803`);
- focal y regresiones legacy están rojos.

Impacto: corrupción/degradación de liveness, bypass de frontera de proyecto, riesgo de
divulgación de secretos y compatibilidad no demostrada.

Actualización recomendada: incorporar estos casos como viability gates RED explícitos
de Fase 1, hacerlos GREEN antes de fases posteriores y repetir revisión adversarial
sobre hashes inmóviles.

## Gate de reanudación

No descargar CPython, construir PE, editar el registro, lanzar daemon/DayZ ni avanzar
fases hasta que ambos problemas estén cerrados. `tools/approved-launchers.json` debe
permanecer vacío.
