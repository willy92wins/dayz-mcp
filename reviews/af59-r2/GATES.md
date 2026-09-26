# Gates: af59 ronda 3, lane L2

Consumidor: Claude repetira los dos arneses y run-r3.py. Alcance aprobado: brief L2,
riesgos 1-4. Solo reviews/af59-r2. No build real, daemon, juego ni suite global.

```gates
[x] G1: S1-S9 conservados y S10-S16 verdes (brief: criterio de exito)
  CHECK: powershell.exe -NoProfile -ExecutionPolicy Bypass -File test-af59.ps1 -Script dayz-test.ps1
  EXPECT: PASS=16 FAIL=0
  EVIDENCE: gate-r3.log; PASS=16 FAIL=0; EXIT_CODE=0
[x] G2: H1 conserva seis rechazos (brief: H1)
  CHECK: powershell.exe -NoProfile -ExecutionPolicy Bypass -File test-H1.ps1 -Script dayz-test.ps1
  EXPECT: PASS=6 FAIL=0
  EVIDENCE: H1-r3.log; PASS=6 FAIL=0; EXIT_CODE=0
[x] G3: cada mutacion M3-M9 mata exactamente su escenario S10-S16
  CHECK: ../../tools/.venv-mcp/Scripts/python.exe run-r3.py --mutations
  EXPECT: MUTATIONS_ISOLATED
  EVIDENCE: mutants-r3.log; seven ISOLATED=true; MUTATIONS_ISOLATED
```

Presupuesto: una implementacion y una calibracion con lista cerrada de mutaciones;
corregir fallos ejecutables dentro del techo de 60 turnos del brief, sin fan-out.
No ronda adicional de rediseno del instrumento. La revision de otra familia la hace
el receptor, no se simula en esta lane.

| Caso | Contrato medido | Mutacion prevista |
|---|---|---|
| S10 | Vers no soportado => Warn, sin falso script check OK | M3: promover NotSupported a Die |
| S11 | Payload truncado de cabecera reconocida => Die | M4: degradar InvalidData a Warn |
| S12 | .c extra fuera de scripts tambien aumenta umbral | M5: si scripts existe, contar solo ahi |
| S13 | Fuente sin scripts pero con .c se verifica | M6: exigir que exista scripts |
| S14 | PBO real completo, nueve scripts, Vers y checksum => conteo exacto | M7: rechazar Vers estandar como no soportado |
| S15 | Sin variable, carpeta normal => rechazo antes del builder | M8: omitir guard de tipo de destino |
| S16 | Sin variable, junction real preexistente => build y conteo OK | M9: rechazar tambien destinos junction |

M5 y M6 son dos reparaciones parciales plausibles del riesgo 2. Las mutaciones se
hacen en copias; el sujeto y el arnes final nunca se re-baselinean.
