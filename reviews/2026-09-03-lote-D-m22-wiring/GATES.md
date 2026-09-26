# Gates: lote D — cablear dos capacidades entregadas y hoy inalcanzables

Scope: dos productos observables. (P1) `bridge_status()` publica cuatro campos
`tool_registry_*` como overlay LOCAL del proceso FastMCP. (P2) `dayz_test_run` acepta una
misión absoluta contenida en `mission_roots` y devuelve un fallo ESTRUCTURADO cuando la
sesión de Steam está caduca. Nada más entra en el alcance.

Este ledger lo escribió y lo corrió la sesión orquestadora ANTES de delegar. **No lo edites.**
Tu trabajo es ponerlo verde; el criterio no se negocia desde dentro.

CORRECCION 2026-09-03, ronda 1: el EXPECT original fijaba `de 11`, y ese total depende de
la topologia -- el oraculo emite UN check P2-E cuando la costura no existe y DOS cuando
existe, asi que `de 11` era inalcanzable. Defecto del orquestador, no del implementador, y
justo lo que la plantilla de este ledger prohibe. El EXPECT pasa a un marcador estable.

Estado medido antes de empezar, en este mismo workspace y con este mismo comando:

```
P1-A=FAIL  P1-D1=UNMET  P1-D2=UNMET  P1-B=PASS  P1-C=PASS
P2-A=FAIL  P2-B=PASS    P2-C=PASS    P2-D0=FAIL  P2-D=UNMET  P2-E=UNMET
```

Los FAIL son el trabajo. Los UNMET se desbloquean solos cuando el sujeto exista.
**Los PASS son la línea que no se puede romper**: son fail-closed y no-regresión.

Calibración de mutantes corrida por la orquestadora: **4 de 4 cazados**, control positivo
(BASE) rojo. Un arreglo que borre el sujeto en vez de corregirlo se pone rojo aquí.

```gates
[ ] G1: los cuatro campos tool_registry_* y su aislamiento, y el contrato de mision
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que tocan los modulos del write-set siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

## Fuera del alcance de este ledger, a propósito

- **La suite completa (2296 tests) no es un gate de este workspace.** Es una copia parcial
  del árbol y la mitad de la suite son gates de documentación que leen ficheros que aquí no
  están: daría rojos que no son tuyos. La corre la sesión orquestadora sobre el árbol vivo
  al integrar, contra la baseline `Ran 2296 tests, FAILED (failures=2, skipped=6)` cuyos dos
  rojos conocidos son `test_full_source_hash_is_frozen` y
  `test_removing_only_marker_lines_restores_frozen_source_hash`. No intentes correrla.
- **El PBO y cualquier gate in-game.** Nada de este lote toca `addon/`.
