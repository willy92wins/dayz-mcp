# Transición candidata — autoridad sucesora no-S15

Fecha: 2026-09-01  
Estado: CANDIDATE, revisión Grok nueva pendiente  
Predecesora: `20260831-ledger-v3` / `inbox-20260831-v6`  
Sucesora propuesta: `20260901-ledger-v4` / `inbox-20260901-v7`

## Decisión incorporada

La sucesora congela C1 para capacidades, S-04-WIRE, S-05-UI.material, el contrato fullres S-07,
el namespace S-10, el receipt S-11 V1, la mini-spec S13 SHA
`a55e17ce9765206b61679f0de65d9bfd8a3022438ff343fd2263ed345a4c5ad9` y la opción A S14 SHA
`7996e342231582c247b6ef892b42a4699677e8f2ff62cfe3c3390a3300e50aec`.

Mantiene `candidate-live`, `S-04-TELEMETRY`, `S-15/M15` y `S-24/M24` DEFERRED, sin abrir OWNS ni
autorizar write/deploy. La reserva física M24 del addendum v6 se conserva como historia/read-only;
v7 no concede esa ventana. La cadena M20–M25 no se desbloquea por esta transición.

## Frontera de autoridad

El bundle v8 candidato no se activa por existencia en disco. Primero se revisa el staging TEMP
exacto; después se rehashea, se aplica al repo bajo preimágenes, se prueba igualdad repo=staging y
sólo entonces se atestigua. `AUTH-PROMOTED` es el último acto. Hasta entonces v7 manda. No existe
todavía review manifest Grok de esta sucesora y este documento no lo simula.

## Compatibilidad

Los 35 planes y sus 70 gates `PLAN-CODEX`/`PLAN-GROK` permanecen byte/título-idénticos. Sus
atestaciones acreditan el plan base; la nueva hoja successor acredita sólo el delta. Los 73 tokens
actuales no se reescriben ni se copian. `ROOT-PLANS` continúa ligado a los manifiestos base.

## Rollback

Se restaura únicamente el `GATES.md` existente modificado y se retiran los siete ficheros nuevos
successor enumerados por `authority_apply` con `state=absent`. `authority-bundle-v7.sha256`,
`plan-manifest.sha256`, las 35 hojas, los 35 planes, attestations previas, producto, PBO e inbox
permanecen intactos.
