## VEREDICTO

BLOQUEANTES=1.

## BLOQUEANTES

### B-01 — P4: `root=null` explícito no falla cerrado y llega al límite de encolado

**Propiedad rota:** P4. Cuando `root` está presente debe ser una cadena no vacía y el rechazo debe ocurrir antes de encolar.

**Ubicación:** `tools/dayz_mcp/server.py:4192,4199-4205` (`ui_tree`), `:4216,4223-4227` (`ui_set_text`), `:4238,4251-4260` (`ui_click`) y `:4313,4318-4322` (`ui_focus`). Las cuatro firmas declaran `root: str | None = None`; por ello el schema registrado admite JSON `null`. Dentro de cada función, la guarda sólo se ejecuta cuando `root is not None`, y la inserción en `args` usa la misma condición. FastMCP convierte tanto la omisión como el `null` explícito en `None`, de modo que la función ya no puede distinguirlos. El siguiente paso es `Runtime.call_bridge`, cuyo `state.enqueue_command(...)` está en `tools/dayz_mcp/server.py:823-828`.

**Efecto concreto:** una entrada inválida se acepta como si el campo se hubiera omitido y se encola sin `root`. No es un crash ni una excepción: es degradación fail-open del contrato de validación y puede ejecutar la operación con ámbito global en vez de rechazar la petición inválida.

**Repro [EXACT, ejecutado desde `ws/tools` con el intérprete exigido]:**

```powershell
$env:PYTHONPATH='.'; @'
import asyncio
from unittest.mock import patch
from dayz_mcp.server import ServerConfig, build_app

CASES = (
    ('ui_tree', {'root': None}),
    ('ui_set_text', {'path': 'Btn', 'text': 'x', 'root': None}),
    ('ui_click', {'path': 'Btn', 'root': None}),
    ('ui_focus', {'path': 'Btn', 'root': None}),
)

async def main():
    app, runtime = build_app(ServerConfig(key='k', port=0, log_sink=lambda _m: None))
    accepted = []
    for tool, arguments in CASES:
        seen = []
        async def recorder(cmd, bridge_args, role, timeout):
            seen.append(dict(bridge_args))
            return {'ok': 1}
        error = None
        with patch.object(runtime, 'call_bridge', side_effect=recorder):
            try:
                await app.call_tool(tool, arguments)
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
        if error is None and seen:
            accepted.append((tool, seen[0]))
    assert not accepted, f'root=null accepted and enqueued: {accepted}'

asyncio.run(main())
'@ | & 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -
```

**Salida actual pegada (exit 1):**

```text
Traceback (most recent call last):
  File "<stdin>", line 30, in <module>
  File "C:\Python314\Lib\asyncio\runners.py", line 204, in run
    return runner.run(main)
           ~~~~~~~~~~^^^^^^
  File "C:\Python314\Lib\asyncio\runners.py", line 127, in run
    return self._loop.run_until_complete(task)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^
  File "C:\Python314\Lib\asyncio\base_events.py", line 719, in run_until_complete
    return future.result()
           ~~~~~~~~~~~~~^^
  File "<stdin>", line 28, in main
AssertionError: root=null accepted and enqueued: [('ui_tree', {'limit': 256}), ('ui_set_text', {'path': 'Btn', 'text': 'x'}), ('ui_click', {'path': 'Btn', 'button': 0, 'mode': 'direct', 'bubble': False}), ('ui_focus', {'path': 'Btn'})]
```

**Fix sugerido [EXACT para FastMCP 1.27.2 + Pydantic 2.13.4, verificado con una app mínima]:** declarar las cuatro firmas como `root: str = None` y conservar las guardas actuales. En esta versión, el schema resultante deja el campo opcional pero lo publica con `type: string` (sin `null`): la omisión entrega el `None` por defecto, mientras que `{"root": null}` es rechazado por Pydantic antes de entrar en la función. Añadir un caso `root=None` para cada tool que exija `sent is None` y `ToolError`.

## BACKLOG

### Resultado de P1–P4 fuera de B-01

- **P1:** verificado. El schema registrado contiene `root` en las cuatro tools y contiene `mode` y `bubble` en `ui_click`.
- **P2 para omisión real:** verificado en las cuatro tools. Cuando la clave no se manda, `root` no aparece en el `args` observado en el límite de `call_bridge`; nunca se rellena con `""`.
- **P3:** verificado. Toda llamada válida a `ui_click` lleva `mode` y `bubble`, incluidos los valores por defecto `direct` y `false`; `complete/true` también viaja sin alteración. No encontré una rama válida que omita ninguno de los dos.
- **P4 salvo B-01:** todos los rechazos medidos ocurren antes de `call_bridge`. `mode=""` da `bad_args`; `mode=3` falla en Pydantic; `bubble=1`, `bubble=0`, `bubble="true"` y `bubble=null` fallan en Pydantic; `root=""` da `bad_args`; `root=7` falla en Pydantic. Pasan `mode=direct`, `mode=complete`, `bubble=false`, `bubble=true` y cualquier `root` cadena con longitud mayor que cero (`"MiRoot"` y también `" "`, coherente con «no vacía»). `root=null` es la única excepción hallada y está en B-01.

### BL-01 — El schema de `mode` está subespecificado

`tools/dayz_mcp/server.py:4239,4247-4248` publica `mode` como cualquier cadena y aplica el enum sólo dentro de la función. La asimetría con `bubble: StrictBool` importa a un consumidor que genera llamadas desde el schema: puede proponer valores imposibles y gastar una llamada en recibir el rechazo. No rompe P1–P4 porque el rechazo es fail-closed y previo al encolado.

Conservarlo es coherente con el precedente local de `ui_reload_layout` (`tools/dayz_mcp/server.py:4274,4278-4281`), pero no es una necesidad técnica ni una convención uniforme: el mismo archivo usa `Literal[...]` en `ui_dialog` (`:4334`) y otras tools. Lo clasifico como deuda de discoverability, no como bloqueante. En una app mínima con estas versiones, `Literal["direct", "complete"]` publicó exactamente `enum: ["direct", "complete"]` y rechazó `""`/`3` antes del cuerpo.

### BL-02 — Los tests y el oráculo omiten el caso que reproduce B-01

Fuera del write-set de producto y, por instrucción, no bloqueante por sí mismo. `tools/tests/test_mcp_tools.py:967-977` cubre `root=""` y `root=7`, pero no `root=None`; `:920-933` comprueba sólo la presencia de las propiedades, no que `root` sea no-nullable ni que `mode` publique su enum. El oráculo repite el mismo hueco en `gate/oracle.py:111-118`. Esto explica que la suite y el oráculo queden verdes con el fallo actual.

### BL-03 — La guarda preexistente de `button` conserva una coerción pública engañosa

No es regresión de este cambio: el árbol entregado y el padre se comportan igual. Aunque el cuerpo comprueba `isinstance(button, int)` y excluye `bool` (`tools/dayz_mcp/server.py:4245-4246`; padre `:4232-4233`), Pydantic convierte antes `true` a `1`, `false` a `0`, `1.0` a `1` y `"1"` a `1`; los cuatro valores se encolan. Los casos fuera de rango `-1`, `3` y `9` sí se rechazan. La guarda sigue viva y no perdió estrictitud respecto del padre, que era el criterio de este lote, pero no es estricta en la superficie pública. Si se aborda en otro lote, el análogo ya usado aquí sería `StrictInt`.

### Compatibilidad aditiva comprobada

- Comparadas mediante `app.call_tool` las llamadas preexistentes contra el árbol padre. `ui_tree` (con y sin `path`), `ui_set_text` y `ui_focus` producen exactamente los mismos `args` cuando no se usa `root`.
- `ui_click({"path":"Btn"})` cambia sólo en lo exigido por P3: antes enviaba `{"path":"Btn","button":0}` y ahora añade `"mode":"direct","bubble":false`. El bridge ya normalizaba modo ausente a `direct` (`addon/scripts/5_Mission/MCPClientBridge.c:1445-1449`) y la rama `direct` no lee `bubble` (`:1409-1416`), por lo que el comportamiento consumidor se conserva.
- Las guardas públicas de `path` y `button` dieron los mismos veredictos y coerciones en el entregado y en el padre para `path=""`, `7`, `null`, `true`, `" "`, y para `button=-1`, `3`, `true`, `false`, `1.0`, `"1"`, `null`. No encontré una regresión en ellas.
- Suite prescrita, ejecutada con `PYTHONPATH=.` y el intérprete indicado: `Ran 41 tests in 7.296s` / `OK` (exit 0). Ese verde no cubre B-01.
- Hash SHA-256 de los ocho ficheros bajo `addon/`: 8/8 idénticos al árbol de referencia. También coinciden byte a byte `tools/dayz_mcp/loopback.py`, `MCPMessages.c` y `MCPClientBridge.c`; se sostienen las premisas del contrato inferior. El commit `08e707a00e341ce07aa6432fc592ad8639812380` existe con asunto `M06: open the UI ingress to the contract Enforce already speaks`.

## LO QUE NO PUDE VERIFICAR

- No ejecuté DayZ, red ni PBO, conforme al entorno impuesto. Por tanto no observé en runtime real `ResolveUiRoot`, `mode_not_implemented` ni un widget afectado; sí leí sus mecanismos en `addon/scripts/5_Mission/MCPClientBridge.c:2039-2083` y `:1428-1461`.
- No ejecuté la suite global ni las otras cuatro suites citadas por el implementador; ejecuté exactamente `tests.test_mcp_tools`, la suite prescrita para esta revisión.
- El árbol entregado contiene 298 ficheros `.py` bajo `tools/`. Pude comparar por SHA-256 276 contra el árbol vivo: 274 idénticos y exactamente los dos cambios anunciados (`tools/dayz_mcp/server.py`, `tools/tests/test_mcp_tools.py`). El sandbox denegó lectura de los 22 `.py` restantes dentro de los dos bundles `tools/native-launchers/dayz-test-v1*`, así que no pude rehacer de extremo a extremo el censo de 298 hashes del orquestador.
- No probé clientes MCP externos que generen llamadas a partir del schema; el impacto de BL-01 se deriva del schema registrado real, no de una ejecución con un cliente generativo concreto.

## PREMISAS DEL ORQUESTADOR QUE CREO FALSAS

Ninguna de las premisas del §2 quedó refutada. El fallo B-01 no contradice que las capas inferiores ya aceptaran `root`; está en la nueva frontera pública y en un valor (`null`) que las premisas no declaraban probado.
