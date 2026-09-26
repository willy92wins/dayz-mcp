# S-13 — autoridad cerrada para la proyección de identidad M13

Estado: mini-spec de autoridad para revisión/aprobación. No implementa S-13.
Destino: TEMP solamente; no es un successor draft ni una modificación del repositorio.

## Contrato que debe cerrarse

La ficha aprobada `plans/inbox-20260830/07-fb-20260829-024827-9b7b.md:28-30` exige que M13 defina una proyección pura desde el `ServerConfig` construido por la app: `enable_exec_enforce=False` produce `profile="standard"`, `True` produce `profile="exec_enforce"`; `client_platform` solo acredita `role="claude"` o `role="codex"`; cualquier config o role no acreditable produce identidad `unknown`; nunca se infiere identidad desde `tools`.

### Evidencia exacta releída

- `[EXACT]` `tools/dayz_mcp/server.py:499-519` define `ServerConfig`; los campos relevantes son `enable_exec_enforce: bool = False`, `client_platform: str = "unknown"` y `client_platform_raw: str = ""`.
- `[EXACT]` `tools/dayz_mcp/server.py:3820-3826` registra `exec_enforce` únicamente cuando `config.enable_exec_enforce` es verdadero.
- `[EXACT]` `tools/dayz_mcp/server.py:4303-4323` parsea la CLI, conserva `args.client_platform` solo como alias raw y construye `ServerConfig` con `client_platform=CLIENT_PLATFORM_ALIASES.get(args.client_platform, args.client_platform)`.
- `[EXACT]` `tools/dayz_mcp/server_cli.py:8-9` fija `CANONICAL_CLIENT_PLATFORMS = ("claude", "codex", "unknown")` y `CLIENT_PLATFORM_ALIASES = {"grok": "unknown"}`; `[EXACT]` `:61-62` permite esos canónicos y el alias `grok`.
- `[EXACT]` `tools/tests/fixtures/effective_schema_v5/profile_inventory.json:3-67` contiene exactamente las cuatro configuraciones positivas: `standard|claude`, `standard|codex`, `exec_enforce|claude`, `exec_enforce|codex`, con sus pares literales de campos `ServerConfig`.
- `[EXACT]` `tools/dayz_mcp/effective_schema.py:1-5` declara el módulo puro y sin consulta de aplicación; `[EXACT]` `:109-154` expone `build_payload(profile: str, role: str, instructions: str, tools: Iterable[object], tool_registry_fingerprint: str, *, catalog: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]`, valida únicamente perfiles/roles ya finalizados (`:122`) y rechaza `unknown`. `[EXACT]` `:175-183` expone el constructor de conveniencia con la misma entrada ya finalizada.

Conclusión de alcance: el código actual recibe `profile`/`role` terminados; no contiene el mapper `ServerConfig → (profile, role)`. El mapper debe ser puro, no importar `server.py` y no aceptar ni consultar `tools`.

## Representación fail-closed

En la única representación autorizada, una entrada no acreditable no se degrada a una pareja parcialmente válida: devuelve identidad completa `unknown`. Esa salida clasifica únicamente la identidad viva y su snapshot/overlay local; no suprime, reduce ni modifica el envelope público de cuatro payloads que M22/M23 materializan bajo sus contratos propios. La salida válida queda cerrada a estas cuatro parejas y la salida inválida a `unknown`/`unknown`.

Entradas inválidas obligatorias: `enable_exec_enforce` ausente o cuyo tipo exacto no sea `bool` (por tanto `0` y `1` también son inválidos), `client_platform` ausente/no-`str`, o cualquier valor distinto de los strings exactos `claude` y `codex` (incluidos `unknown`, `grok`, mayúsculas y string vacío).

## Pareja primitiva — única autoridad

Razón: es la superficie mínima, no acopla M13 a la clase ni al módulo de la aplicación y entrega directamente la pareja de identidad que consume el snapshot/overlay de M14.

`[DESIGN]` Tipo y firma propuesta:

```python
from typing import Literal, TypeAlias

ProjectedProfile: TypeAlias = Literal["standard", "exec_enforce", "unknown"]
ProjectedRole: TypeAlias = Literal["claude", "codex", "unknown"]
ProjectedIdentity: TypeAlias = tuple[ProjectedProfile, ProjectedRole]

def project_server_config_identity(
    *,
    enable_exec_enforce: object = None,
    client_platform: object = None,
) -> ProjectedIdentity:
    """Purely project two accredited ServerConfig primitives."""
```

`[DESIGN]` Retorno exacto:

- `( "standard", "claude" )`, `( "standard", "codex" )`, `( "exec_enforce", "claude" )` o `( "exec_enforce", "codex" )` para los cuatro casos en `profile_inventory.json`.
- `( "unknown", "unknown" )` si cualquiera de los dos campos no es acreditable. `None` es el sentinel inválido por defecto de ambos parámetros, de modo que omitir uno o los dos campos también devuelve esa pareja sin `TypeError`. La comprobación de bool debe ser `type(value) is bool`; la comprobación de role debe ser igualdad exacta contra `claude|codex`.
- La función no recibe `tools`, `app`, `runtime`, PID, daemon identity ni generación.

`[DESIGN]` Integración futura M22, fuera del cierre S-13: después de construir la instancia final de `ServerConfig`, M22 extrae únicamente `config.enable_exec_enforce` y `config.client_platform`, llama a esta función y conserva la pareja para la identidad viva y el snapshot/overlay que consume M14. Si la pareja es `("unknown", "unknown")`, sólo esos campos locales quedan `unknown`/`null`; no se suprime ni reduce `dayz_effective_schema`. M22/M23 mantienen, por separado, el envelope público y los cuatro payloads positivos de `profile_inventory.json` conforme a su autoridad. M13 no importa `server.py`.

## Decisión cerrada

`[DESIGN]` La pareja primitiva queda fijada como única autoridad de S-13. Su retorno es exactamente la identidad que necesita M14, su superficie no conoce `ServerConfig` ni crea ciclo de importación y el fail-closed se prueba con literales, tipos inválidos y parámetros omitidos. Este mini-spec no autoriza ninguna superficie alternativa.

## Ownership y límites

- `[DESIGN]` M13 write OWNS successor: `tools/dayz_mcp/effective_schema.py` para el mapper puro y `tools/tests/test_effective_schema.py` para sus tests contractuales, sujeto a la fila M13/addendum aprobada. Ningún otro archivo se autoriza por este mini-spec.
- `[DESIGN]` M22 owns la integración futura que lee la instancia real de `ServerConfig`, pasa los dos valores primitivos y expone la pareja a la identidad/snapshot/overlay local que consume M14. Un resultado `unknown` no altera el envelope público de cuatro payloads. M22 no puede reimplementar la tabla ni inferir desde tools.
- `[EXACT/read-only]` Para especificación y tests, `tools/dayz_mcp/server.py:499-519`, `:3820-3826`, `:4303-4323`, `tools/dayz_mcp/server_cli.py:8-9,61-62` y `tools/tests/fixtures/effective_schema_v5/profile_inventory.json:3-67` son fuentes de lectura. No se edita `server.py`, `server_cli.py`, el fixture de inventario, M14, M23, autoridad, gates ni reports.

## Viability tests cerrados

### M13 — mapper puro y cierre S-13

Todos estos oracles son literales en `tools/tests/test_effective_schema.py`; no se derivan de `_PROFILES`, `profile_inventory.json`, un helper de normalización ni del resultado de la función bajo prueba.

1. `[DESIGN][PASS]` Aserciones independientes exactas para las cuatro entradas: `project_server_config_identity(enable_exec_enforce=False, client_platform="claude") == ("standard", "claude")`; `False|codex == ("standard", "codex")`; `True|claude == ("exec_enforce", "claude")`; `True|codex == ("exec_enforce", "codex")`. Un flip de la tabla bool o role debe producir fallo.
2. `[DESIGN][FAIL]` Ausencia/config no acreditable: omitir ambos kwargs, omitir sólo `enable_exec_enforce`, omitir sólo `client_platform`, `None|claude` y `False|None` deben producir exactamente `("unknown", "unknown")`, nunca `TypeError` ni una pareja parcial.
3. `[DESIGN][FAIL]` Tipos y valores inválidos: cada caso literal `0|claude`, `1|codex`, `"false"|claude`, `False|None`, `False|0`, `False|True`, `False|"Claude"`, `False|"unknown"`, `False|"grok"`, `False|""`, `True|None`, `True|0`, `True|True`, `True|"Claude"`, `True|"unknown"`, `True|"grok"` y `True|""` debe producir exactamente `("unknown", "unknown")`. No basta comprobar un solo componente, pertenencia o presencia del string `unknown`. Esto distingue comprobación de tipo exacto y no una coerción truthy/alias, y hace fallar cualquier pareja parcialmente acreditada en las ramas `False` y `True`.
4. `[DESIGN][PASS]` Anti-tautología estructural: comprobar que la firma pública contiene exactamente `enable_exec_enforce` y `client_platform`, ambos keyword-only con default literal `None`, y no contiene `tools`. Los expected se escriben como literales en el cuerpo del test. Un parámetro `tools`, un default acreditado o expected calculado desde el retorno bajo prueba deja M13 rojo.

### M22 — integración futura, fuera del cierre S-13

- `[DESIGN]` Los oracles de adaptador, no-inferencia desde `tools`, envelope de cuatro payloads e identidad/snapshot/overlay local pertenecen a los OWNS/tests de M22, incluido `tools/tests/test_effective_schema_integration.py`; no viven en el test M13 y no se exigen para cerrar S-13.
- `[DESIGN]` Esos tests futuros deben comprobar que variar `tools` no cambia la pareja derivada de los mismos dos campos, que una identidad viva no acreditable deja su overlay en `unknown`/`null` y que el envelope público conserva exactamente los cuatro payloads positivos. No pueden usar el rechazo actual de `[EXACT] build_payload` a `unknown` como sustituto de observar el adaptador y el envelope reales.

## Criterio de aceptación de S-13

S-13 solo puede cerrarse cuando la pareja primitiva autorizada tiene implementación en el OWNS M13 y sus tests puros acreditan los cuatro positivos, parámetros omitidos, tipos/roles inválidos, identidad completa `unknown` sin excepción accidental y firma keyword-only con defaults `None` y sin `tools`. Un mutante que invierte `enable_exec_enforce`, acepta `grok`, coerce `1` a `True`, elimina un default o añade `tools` debe quedar rojo. La integración M22 y sus oracles futuros de adaptador/envelope/overlay no son requisito para cerrar S-13 y permanecen fuera de sus OWNS. Hasta completar el cierre puro, M13 permanece `REVISE`; este documento no autoriza implementar ni promocionar ningún gate.
