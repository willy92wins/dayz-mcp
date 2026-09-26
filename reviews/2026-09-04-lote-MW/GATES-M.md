# Gates: lote M — superficie pública de `server.py` (cinco productos, siete tools)

Scope: `tools/dayz_mcp/server.py` publica en el wire lo que hoy sólo vive en el cuerpo de las
funciones o en helpers que nadie reenvía: (P1) `pipeline_resolve` acepta y propaga
`evidence_ref`; (P2) `dayz_knowledge_status`/`dayz_knowledge_prepare` publican un schema de cero
argumentos cerrado y rechazan extras con `bad_args: unexpected arguments`; (P3)
`capture_screenshot` reenvía `crop_space` a `capture_dual` y devuelve `[imagen, meta JSON]` en las
dos ramas de `save_fullres`; (P4) `ui_click.mode` y `ui_reload_layout.mode` publican su enum,
`ui_click.button` es estricto y la descripción nombra `mode_not_implemented`; (P5)
`dayz_test_run.mode` publica el enum derivado de la autoridad M12 y la descripción documenta
`extra_mods`. Regla transversal (ficha ea10): toda tool tocada declara `additionalProperties:false`
y rechaza argumentos desconocidos con `bad_args: unexpected arguments`.

**Fuera de alcance a propósito**: el helper `capture_dual` (ya acepta `crop_space`), `inbox.py`
(ya persiste `evidence_ref`), `knowledge.py` (M09 cerrado y anclado por hash), la región
`dayz_test_stop`/`resolve_stop_run` (otra sesión la tiene en vuelo), el bridge Enforce y cualquier
módulo empaquetado en el bundle nativo.

Este ledger lo escribió y lo corrió la sesión orquestadora ANTES de delegar. **No lo edites.**
`gate/` está sellado por hash (`runs1/GATE-SEAL.txt`) y se comprueba al recibir.

Estado medido antes de delegar, en este workspace y con `bash gate/run.sh`:

```
PASS=16  FAIL=34  UNMET=0  de 50   (2026-09-04 04:24, árbol vivo HEAD d007838 copiado sin cambios)
PASS: P0-count, P0-required-* (7), P0-scene_raycast-alias, P1-D-legacy-two-args,
      P2-B2-inprocess-empty-ok, P2-C2-wire-empty-ok, P3-C-shape-fullres=True,
      P4-B-empty-mode-rejected, P4-C2-button-range-kept, P5-C-offline-rejected
FAIL: los 34 restantes, todos con el sujeto ausente (sin additionalProperties, sin
      evidence_ref, sin crop_space, sin enum, extras aceptados en silencio)
```

**Los PASS son la línea que no se puede romper** (recuento de tools, `required` de las siete
tools, el alias `from` de `scene_raycast`, la llamada legacy de dos argumentos a
`pipeline_resolve`, y los rechazos que el cuerpo ya hacía). Los FAIL son el trabajo. Un UNMET
sólo puede quedar si el sujeto es inalcanzable desde este workspace, y se explica en STATE.md.

## Calibración

El oráculo no importa nada de `tools/tests/` y ninguna expectativa se deriva del schema bajo
prueba: `required` está congelado como literal, el enum de `dayz_test_run.mode` se compara con
`dayz_test_modes.public_mode_names()` (módulo fuera del write-set), y el buzón se redirige a un
directorio temporal parcheando los globales que el escritor y el lector usan (`inbox.py:11-12`).
La rama `bad_crop_space` corre el helper REAL, que rechaza antes de cualquier captura
(`mcp_capture.py:671`); las ramas de reenvío usan un doble que registra los kwargs.

```gates
[ ] G1: los cinco productos, medidos por el oráculo externo
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que cubren las siete tools siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending

[ ] G3: la suite completa en el árbol vivo no pierde nada (la corre el orquestador al integrar)
  CHECK: cd tools && ./.venv-mcp/Scripts/python.exe -m unittest discover -s tests -t .
  EXPECT: Ran N tests, FAILED (failures=2) con los mismos dos rojos por nombre (centinela MCPBridge.c)
  EVIDENCE: pending

[ ] G4: revisión de otra familia (Codex, sesión nueva) sin bloqueantes con repro
  EVIDENCE: pending
```
