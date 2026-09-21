# Transición candidata — autoridad sucesora S15 + candidate-live

Fecha: 2026-09-01  
Estado: CANDIDATE, revisión Grok nueva pendiente  
Predecesora: `20260901-ledger-v4` / `inbox-20260901-v7` (bundle v8, `authority-bundle-v8.sha256`)  
Sucesora propuesta: `20260901-ledger-v5` / `inbox-20260901-v8`

## Decisión incorporada

La sucesora congela tres ADR bajo `plans/adr/`, producidos por un council de siete
ángulos y sometidos a doce rondas de corrección con dos revisores independientes de
familias distintas (`gpt-5.6-sol` por `codex exec`, once rondas; `claude-opus-5-thinking-high`
ciego por `cursor-cli`, siete pasadas). Ambos cerraron en **PASS** sobre los tres:

- `plans/adr/adr-s15-m15-storage.md` — contrato S15/M15 storage: orden normativo de
  parseo/acreditación/recovery/pending, matriz A–F, taxonomía del lector, matriz
  `StorageNodeReceipt` kind×operación, journal de rotación y journal `rollback-op`
  propio, mutex nombrado y gate NTFS real.
- `plans/adr/adr-candidate-live-s04-s24.md` — candidate-live con namespace, destino,
  rollback y receipt propios; arista
  `(M05.full + S-10 + M22) → candidate-live → S-04-TELEMETRY → S-24.final`.
- `plans/adr/adr-tabla-procedencia.md` — tabla de procedencia con una fila por decisión
  material, su evidencia `path:line`, su estado y el gate que la decide.

**Concede a M15 los dos OWNS que el addendum le reserva** y que v7 mantenía cerrados:
`tools/dayz_mcp/dayz_test_storage.py` y `tools/tests/test_dayz_test_storage.py`. Ninguno
existe todavía; la concesión autoriza a crearlos bajo el contrato del ADR, no aprueba
código. Esto desbloquea la arista M15 → M20 de
`plans/inbox-20260830/00-execution-dag.md:69`.

**Cierra la paradoja del discriminador anti-M24** repartiendo la carga de la prueba entre
dos actores, por el patrón que la ficha 32 ya usa para M10
(`plans/inbox-20260830/32-fb-20260830-011217-668f.md:37,48`): el unit gate de
candidate-live acredita *no haber intentado* mediante un espía externo al writer que
registra sin abrir, y el gate de integración de M24/M25 acredita *no haber cambiado*
mediante un sentinel M24-owned cuyo path no se revela a candidate-live. La conjunción de
ambos sostiene «M24 byte-idéntico y sin accesos»; ninguno de los dos acredita lo del otro.

Mantiene `candidate-live`, `S-04-TELEMETRY` y `S-24/M24` **DEFERRED, sin abrir OWNS ni
autorizar write/deploy**. La reserva física M24 del addendum se conserva íntegra: esta
sucesora no la presta ni la reduce. La cadena M20–M25 no se desbloquea por esta
transición más allá de la arista M15 → M20.

## Alcance del PASS incorporado

El PASS de los dos revisores es **documental**. Ningún gate runtime se ejecutó: ni el
gate NTFS/Win32 real, ni AddonBuilder, ni DayZ, ni pérdida de alimentación. Los propios
ADR conservan `DEFERRED` la durabilidad ante corte de alimentación y la atomicidad de un
único replace, y declaran que `REPLACEFILE_WRITE_THROUGH` no acredita write-through
porque la documentación oficial la marca como no soportada.

El FINAL `verify_successor_r3`, del que provienen los diez bloqueos S15, **no tiene
artefacto on-disk hash-pineado disponible**. Cada uno de los diez fue revalidado contra
las fuentes pineadas y esa revalidación, ítem por ítem, está escrita en el propio ADR.
Esta transición no lo simula ni lo da por acreditado.

## Frontera de autoridad

El bundle v9 candidato **no se activa por existencia en disco**. Primero se revisa el
staging exacto; después se rehashea, se aplica al repo bajo preimágenes, se prueba
igualdad repo=staging y sólo entonces se atestigua. `AUTH-PROMOTED` es el último acto.
Hasta entonces **v8 manda**. No existe todavía review manifest Grok de esta sucesora y
este documento no lo simula.

## Compatibilidad

Los 35 planes y sus 70 gates `PLAN-CODEX`/`PLAN-GROK` permanecen byte/título-idénticos, y
también los cinco gates `AUTH-*` de la sucesora no-S15 ya atestados. Sus atestaciones
acreditan lo suyo; la nueva hoja `gates/s15-successor.md` acredita sólo este delta. Los
tokens existentes no se reescriben ni se copian. `authority-bundle-v7.sha256` y
`authority-bundle-v8.sha256` se conservan en el bundle como cadena de predecesoras.

Los tres ADR se publican **sin** el prefijo `PROPUESTO-` que llevaron mientras fueron
propuesta. El contenido es byte-idéntico al que ambos revisores aprobaron; sólo cambió el
nombre al promoverse.

## Rollback

Se restauran únicamente el `GATES.md` modificado y el nombre previo de los tres ADR, y se
retiran los ficheros nuevos de esta sucesora enumerados con `state=absent`:
`plans/inbox-20260830/authority-bundle-v9.sha256`, `gates/s15-successor.md` y este
informe. `authority-bundle-v7.sha256`, `authority-bundle-v8.sha256`,
`plan-manifest.sha256`, las 35 hojas, los 35 planes, la hoja `gates/non-s15-successor.md`,
las attestations previas, producto, PBO e inbox permanecen intactos. Los dos OWNS de M15
vuelven a quedar cerrados y ningún fichero de código se crea ni se borra: no existen.
