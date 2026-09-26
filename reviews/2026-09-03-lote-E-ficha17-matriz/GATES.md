# Gates: lote E — ficha 17 (7c88), la matriz pública de `dayz_test_run`

Scope: tres productos observables de la ficha 17. (P1) las tres causas de rechazo llegan
nombradas al cliente en vez del opaco `bad_dayz_test_request`, en la fachada Y en la tool
pública, para los dos valores de `preflight`. (P2) ninguna fila pública válida acaba en
`terminal_invalid`; en concreto `preflight=true, mode=client, run_id=<UUID>` tiene éxito
conservando ese UUID. (P3) la descripción REGISTRADA de `dayz_test_run` documenta la
secuencia reattach y que preflight no relaja la matriz.

**Fuera de alcance a propósito**: el sobre estructurado de `dayz_test_stop` y los tres
campos de generación. Eso es la otra mitad de la ficha, va en su propio lote porque exige
superficie nueva en el **daemon** — hoy `daemon.py:792-797` cosecha runs muertos y sólo los
escribe al log, y `retired_run_diagnostic` tiene 0 ocurrencias en el paquete.

Este ledger lo escribió y lo corrió la sesión orquestadora ANTES de delegar. **No lo edites.**

Estado medido antes de empezar, en este workspace y con este comando:

```
PASS=7  FAIL=13  UNMET=0  de 20
```

Los 7 PASS son la línea que no se puede romper, y cuatro de ellos son **controles de
vacuidad**: E3-CONTROL (una fila productiva SÍ consulta Steam, si no «cero consultas» se
cumple no consultando nunca), E2-CONTROL (un terminal incoherente SIGUE rechazándose, si no
la correlación se «arregla» borrándola), E2-SERVER y E0-SELLO.

Calibración de mutantes corrida por la orquestadora: **3 de 3 cazados**, control positivo
(BASE) rojo. Los tres atacan exactamente esos controles.

```gates
[ ] G1: la matriz publica de dayz_test_run, sus causas nombradas y su documentacion
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que cubren el write-set siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

## Notas que evitan gates imposibles, todas medidas

- **La venv NO tiene pytest.** La suite es `unittest`.
- La suite completa (2303 tests) **no** es gate de este workspace: es una copia parcial y
  media suite son gates de documentación que leen ficheros que aquí no están. La corre la
  orquestadora al integrar, contra `Ran 2303 tests, FAILED (failures=2, skipped=6)` cuyos
  dos rojos conocidos son `test_full_source_hash_is_frozen` y
  `test_removing_only_marker_lines_restores_frozen_source_hash`.
- `tests/test_client_mode.py` está **sellado por hash** dentro del oráculo y prohibido en el
  write-set: de ahí sale la fixture del runtime cliente, y una vara que el candidato puede
  editar no es una vara.
