# Gates: lote F — fichas 25 (`20be`) y 29 (`f4f2`), la mitad M22

Scope: **una sola cosa**, en `tools/dayz_mcp/server.py`. Las cuatro tools públicas de UI
(`ui_tree`, `ui_set_text`, `ui_click`, `ui_focus`) aceptan `root`, y `ui_click` acepta además
`mode` y `bubble`, con validación fail-closed ANTES de encolar.

Esto es un cableado, no una capacidad nueva. Las tres capas de abajo **ya hablan el contrato**:
Enforce lo declara (`MCPMessages.c:63,69-71`), el bridge lo resuelve
(`MCPClientBridge.c:1436-1461`, `ResolveUiRoot`) y el ingress del loopback lo acepta desde el
commit `08e707a` (`loopback.py:665-676`). La superficie de la tool MCP es el único eslabón
que corta, y por eso la capacidad está entregada y es inalcanzable.

Este ledger lo escribió y lo corrió la sesión orquestadora ANTES de delegar. **No lo edites.**

Estado medido antes de empezar, en este workspace y con este comando:

```
PASS=6  FAIL=18  UNMET=0  de 24
```

**Los 6 PASS son la línea que no se puede romper**, y son todos de preservación:
  - `F3-*` (4): sin `root`, `root` NO viaja. Ponerle un defecto `""` y mandarlo siempre
    cambia el alcance de resolución de todo llamante que hoy no manda root.
  - `F6-*` (2): las guardas de `path` vacío y `button` fuera de rango siguen vivas.

Calibración de mutantes corrida por la orquestadora: **3 de 3 cazados**, control positivo
(BASE) rojo. Los tres son errores plausibles de verdad, no muñecos: `root=""` por defecto,
ensanchar sin validar, y perder la guarda de `button` al reestructurar.

```gates
[ ] G1: las cuatro firmas publicas de UI aceptan root, y ui_click ademas mode y bubble
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que cubren la superficie UI siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

## Fuera de alcance, a propósito

- **El modo `complete`.** La propia ficha 25 lo declara INCONCLUSIVE: la viabilidad de motor
  no está acreditada y el bridge lo rechaza a propósito con `mode_not_implemented`
  (`MCPClientBridge.c:1449-1453`). La tool debe ACEPTAR `mode="complete"` como valor válido
  del enum y transmitirlo; qué haga el bridge con él no es de este lote.
- **El gate in-game.** Las dos fichas llevan `D6=true` y no cierran hoy: se acumulan para una
  sesión con el operador humano. El PBO desplegado ya lleva el resolver de UI, así que serán
  verificables.
- La suite completa (2309 tests) no es gate de este workspace: es copia parcial. La corre la
  orquestadora al integrar contra `Ran 2309 tests, FAILED (failures=2, skipped=6)`, cuyos dos
  rojos conocidos son `test_full_source_hash_is_frozen` y
  `test_removing_only_marker_lines_restores_frozen_source_hash`.
