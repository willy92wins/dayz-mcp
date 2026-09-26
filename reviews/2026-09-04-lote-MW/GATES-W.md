# Gates: lote W-tools — cinco arreglos fuera de `server.py`

Scope: (W6) `tools/h9_native_probe.py` vuelve a poder llamar al launcher nativo: pasa todos los
argumentos keyword-only que `launch_registered_native` exige hoy (`daemon_policy_json`) y `main()`
no pliega un error interno a un código de bundle; (W7) los 14 tests estilo pytest de
`tools/tests/test_effective_schema.py` pasan a `unittest.TestCase` y entran en la suite; (W8) el
doctor comprueba las entradas `external` del closure-manifest contra el disco (sha256 + tamaño) y
nombra el remedio (`launcher_registry_update rollback-last` → `install-dayz-test-v1
--expected-sha256 <sha del REGISTRY actual>`), con una costura pura
`doctor.check_native_bundle_externals(entries, stat=...)`; (W9) un guard de intérprete en la suite:
`tools/tests/test_interpreter_guard.py` falla nombrando el intérprete aprobado
(`<árbol>/tools/.venv-mcp/Scripts/python.exe`, la misma derivación que `daemon.py:186-191`) cuando
`sys.executable` no lo es, y se salta con mensaje cuando ese intérprete no existe en el árbol;
(W12) los dos mensajes de `tools/tests/test_docs_truth.py` que aconsejan «regenerate the map» pasan
a decir que el mapa se mantiene a mano (no existe generador).

**Fuera de alcance a propósito**: `build_native_launcher.py` (su sha256 viaja dentro del contrato
del bundle: editarlo invalida el bundle desplegado), `native_bundle.py`, `launcher_registry*.py`,
todo `dayz_mcp/dayz_test_*.py`, `server.py`, el bundle y el registro aprobado.

Este ledger lo escribió y lo corrió la sesión orquestadora ANTES de delegar. **No lo edites.**
`gate/` está sellado por hash y se comprueba al recibir.

Estado medido antes de delegar, en este workspace y con `bash gate/run.sh`:

```
PASS=2  FAIL=4  UNMET=2  de 8   (2026-09-04 04:38, árbol vivo HEAD d007838 copiado sin cambios)
PASS : W6-main-does-not-fold-TypeError (main no captura TypeError hoy: preservar),
       W12-by-hand-advice (el texto "by hand" ya existe en otra parte del fichero)
FAIL : W7-collected (0 de 14), W7-green, W6-kwargs-complete (falta daemon_policy_json),
       W12-no-regenerate-advice
UNMET: W8-seam (la costura no existe), W9 (el módulo no existe)
G2 en el baseline: `test_effective_schema : Ran 0 tests NO TESTS RAN` → SUITE-ACOTADA ROJA
por el propio sujeto de W7; los otros seis módulos OK (test_docs_truth OK con skipped=2).
```

```gates
[ ] G1: los cinco productos, medidos por el oráculo externo
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test vecinos siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending

[ ] G3: la suite completa en el árbol vivo no pierde nada (la corre el orquestador al integrar)
  CHECK: cd tools && ./.venv-mcp/Scripts/python.exe -m unittest discover -s tests -t .
  EXPECT: los mismos dos rojos por nombre (centinela MCPBridge.c) y ninguno nuevo; +14 tests coleccionados
  EVIDENCE: pending

[ ] G4: revisión de otra familia (Codex, sesión nueva) sin bloqueantes con repro
  EVIDENCE: pending
```
