# Gates — autoridad sucesora S15 + candidate-live

Esta hoja añade el delta de autoridad sin reescribir los 183 gates base ni los 5 de la
sucesora no-S15. Todos son manuales y requieren atestación externa; las casillas y
`EVIDENCE:` no son autoridad.

```gates
[ ] S15-AUTH-DECISION: el usuario aprobo promover los tres ADR de plans/adr conceder a M15 sus dos OWNS y cerrar la paradoja anti-M24 con el sentinel manteniendo candidate-live S-04-TELEMETRY y S24 deferred
  EVIDENCE: pending

[ ] S15-AUTH-REVIEW: dos revisores independientes de familias distintas cerraron en PASS los tres ADR sin hallazgos CRITICAL ni MAJOR abiertos y el revisor ciego rehasheo los tres byte a byte
  EVIDENCE: pending

[ ] S15-AUTH-BYTES: generation 20260901-ledger-v5 graph inbox-20260901-v8 y bundle v9 exacto incorporan el delta S15 sin alterar los 35 planes sus 70 gates de planificacion ni los 5 gates de la sucesora no-S15
  EVIDENCE: pending

[ ] S15-AUTH-GROK: Grok 4.6 Medium mediante Cursor reviso en sesion fresca el bundle v9 exacto y dio PASS sin hallazgos abiertos
  EVIDENCE: pending

[ ] S15-AUTH-OWNS: el bundle v9 concede a M15 exactamente tools/dayz_mcp/dayz_test_storage.py y tools/tests/test_dayz_test_storage.py y ningun otro path y no autoriza escribir codigo en esta fase
  EVIDENCE: pending

[ ] S15-AUTH-PROMOTED: los bytes v9 revisados fueron promovidos despues de acreditar preimagenes y la v8 quedo preservada para rollback
  EVIDENCE: pending

[ ] S15-AUTH-ROLLBACK: la receta restaura solo autoridad y retira solo ficheros successor S15 sin tocar producto PBO inbox gates base la hoja no-S15 ni attestations previas
  EVIDENCE: pending
```

## Por qué son siete y no cinco

La sucesora no-S15 usó cinco (`AUTH-DECISION`, `AUTH-BYTES`, `AUTH-GROK`,
`AUTH-PROMOTED`, `AUTH-ROLLBACK`). Esta añade dos que aquel delta no necesitaba:

- **`S15-AUTH-REVIEW`** — aquella transición congelaba fichas ya revisadas por su propio
  camino. Ésta incorpora tres documentos producidos y revisados **dentro** de la sesión
  que los promueve, así que el veredicto de esos revisores es parte del delta y se
  atestigua aparte del `PASS` de Grok sobre el bundle.
- **`S15-AUTH-OWNS`** — es el único de los dos deltas que **abre OWNS**. Una concesión de
  propiedad física merece su propia puerta: sin ella, `AUTH-BYTES` la absorbería y el
  ledger no distinguiría «los bytes son los correctos» de «se concedió permiso a M15
  sobre dos ficheros que aún no existen».
