# Calibración adversarial Claude Opus 5 C

Contexto de filesystem para esta segunda pasada: `P:/X` =
`/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/X` (mapeo `subst` verificado).
Reabre por esa traducción toda cita P: que antes pareciera inaccesible. No abras contenido de
revisiones históricas ni uses copias de build/deploy como sustituto del source canónico.

En la MISMA sesión, relee tu propia primera respuesta desde el contexto y reabre los diez planes,
sin editar. No abras desde disco la ficha de grupo: ya está en el contexto. En `reviews/**` sólo
puedes leer este `calibrate-c.md` y `common.md`. No leas ni recibas ninguna salida Sonnet.

Intenta detectar verificadores tautológicos, expected derivados del mismo output, drift de
autoridad, paths libres o promoción de unknown. Después intenta retirar falsos positivos contra
`[DESIGN]`.

Ataques dirigidos obligatorios, derivados del source y no de revisiones históricas:

- `268a`: fija si `save_fullres`, `frame_sha256`, `backend_sha256`, `stats` y `clientStats`
  describen ventana, client area, crop adicional o inline; comprueba que el plan no pueda pasar
  mezclando superficies o frames.
- `c7ca`: exige round-trip por las tools públicas `pipeline_resolve` y `pipeline_inbox`, incluida
  la firma/schema de `evidence_ref`; no aceptes como prueba suficiente llamar sólo a helpers de
  `inbox.py`.
- `9b7b`/`103f`: demuestra cómo un proceso antiguo observa una autoridad posterior independiente.
  Si snapshot y «actual» usan el mismo helper o cada app se compara consigo misma, el gate es
  tautológico aunque existan dos procesos.
- `f201`/`141e`: pide un inventario esperado completo por profile/role y un oracle que descubra
  constraints omitidos, no sólo que valide las entradas enumeradas.
- `782b`: exige precedencia cerrada para combinaciones índice/pack presente-ausente-válido-inválido
  y separa capacidad de consulta de capacidad de prepare.
- `d366`: comprueba paths físicos exclusivos para candidate/promoted/backup, fingerprint, banco y
  manifest completo de productores; comparar un wire contra el mismo extractor no acredita nada.
- `ffc7`/`9d46`: verifica que el nuevo `dayz_test_modes.py` entra en builder, verifier, `app.pyz`,
  manifest/hash runtime y tests, y que el enum se observa por `app.list_tools()`/`tools/list`, no
  sólo en un objeto privado.

Emite de nuevo los diez bloques completos `ID / PLAN / SHA256 / VERDICT / FINDINGS / WHY` contra
los mismos hashes y termina en `GROUP_VERDICT`. No entregues un delta: esta segunda salida
sustituye íntegramente la primera.
