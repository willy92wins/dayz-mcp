# X.5 fase 2 impl Codex - F2-001 / F2-002

Timestamp: 2026-06-09 01:07 +02:00
Status: open
Scope: source-only correction. No build, no deploy, no in-game, no harness.

## Bloque A - Archivos

Archivo editado: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c`

### F2-001 - ValidateTelemetryArgs fixture_jsonl allowlist

Line range before: `MCPBridge.c:731-736`.

Before exact:

```c
			string prefix = "$mission:dayz_mcp/";
			if (!StringHasPrefix(args.path, prefix))
			{
				validation.error = "bad_args";
				return validation;
			}
```

Line range after: `MCPBridge.c:731-765`.

After exact:

```c
			string prefix = "$mission:dayz_mcp/";
			if (!StringHasPrefix(args.path, prefix))
			{
				validation.error = "bad_args";
				return validation;
			}

			int prefixLength = prefix.Length();
			int pathLeafLength = args.path.Length() - prefixLength;
			if (pathLeafLength <= 0)
			{
				validation.error = "bad_args";
				return validation;
			}

			string pathLeaf = args.path.Substring(prefixLength, pathLeafLength);
			for (int pathIndex = 0; pathIndex < pathLeaf.Length(); pathIndex = pathIndex + 1)
			{
				string pathChar = pathLeaf.Substring(pathIndex, 1);
				if (pathChar == "/" || pathChar == "\\")
				{
					validation.error = "bad_args";
					return validation;
				}

				if (pathChar == "." && pathIndex + 1 < pathLeaf.Length())
				{
					string nextPathChar = pathLeaf.Substring(pathIndex + 1, 1);
					if (nextPathChar == ".")
					{
						validation.error = "bad_args";
						return validation;
					}
				}
			}
```

### F2-002 - Empty fixture must be parse_error

Line range before: `MCPBridge.c:947-951`.

Before exact:

```c
		CloseFile(handle);
		telemetry.found = true;
		result.ok = true;
		result.telemetry = telemetry;
		return true;
```

Line range after: `MCPBridge.c:976-989`.

After exact:

```c
		CloseFile(handle);
		if (telemetry.line_count_read == 0)
		{
			telemetry.found = false;
			telemetry.parse_error = "empty_fixture";
			result.ok = false;
			result.error = "parse_error";
			result.telemetry = telemetry;
			return true;
		}
		telemetry.found = true;
		result.ok = true;
		result.telemetry = telemetry;
		return true;
```

## Bloque B - Evidencia

### API string vanilla verificada

- `string.Substring(int start, int len)` existe en `C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\1_core\proto\enstring.c:113`.
- `string.Length()` existe en `C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\1_core\proto\enstring.c:199`.
- Literal de backslash escapado `"\\"` aparece en source vanilla, por ejemplo `C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\editor\plugins\dayztools.c:7`.

No se uso `Contains` ni `IndexOf`; la deteccion de `/`, `\` y `..` se hace con scan por `Substring` sobre el leaf.

### Matriz F2-001

| Caso | Resultado por construccion |
|---|---|
| `$mission:dayz_mcp/telemetry_fixture.jsonl` | ACCEPT: pasa prefijo en `MCPBridge.c:732`, leaf no vacio en `:740`, scan `:747-765` no detecta `/`, `\` ni `..`, llega a `validation.path=args.path` / `ok=true` en `:777-778`. |
| `$mission:dayz_mcp/missing_fixture.jsonl` | ACCEPT: mismo flujo. El harness declara esta ruta en `mcp_client.py:22`; el codigo nuevo la deja llegar a `OpenFile`, que puede devolver `fixture_not_found` en `MCPBridge.c:941-948`. |
| `$mission:dayz_mcp/telemetry_bad.jsonl` | ACCEPT: mismo flujo. El harness declara esta ruta en `mcp_client.py:23`; el codigo nuevo la deja llegar al parser para producir `parse_error`. |
| `$profile:telemetry_fixture.jsonl` | REJECT `bad_args`: falla `StringHasPrefix` en `MCPBridge.c:732`, devuelve `bad_args` en `:734-735`. |
| `$mission:dayz_mcp/../telemetry_fixture.jsonl` | REJECT `bad_args`: pasa prefijo, pero el scan detecta `..` en `MCPBridge.c:756-763`. |
| `$mission:dayz_mcp/sub/telemetry_fixture.jsonl` | REJECT `bad_args`: pasa prefijo, pero el scan detecta `/` en el leaf en `MCPBridge.c:750-753`. |
| `$mission:dayz_mcp/` | REJECT `bad_args`: leaf vacio, `pathLeafLength <= 0` en `MCPBridge.c:740-743`. |
| cualquier path con `\` | REJECT `bad_args`: el scan detecta `"\\"` en `MCPBridge.c:750-753`. |

Acceptance paths del harness verificados: `mcp_client.py:21-23` define tres rutas bajo `$mission:dayz_mcp/`, y `mcp_client.py:963-970` espera `bad_args`, `fixture_not_found` y `parse_error` para los negativos.

### Matriz F2-002

| Caso | Resultado por construccion |
|---|---|
| fixture vacio existente, 0 lineas | `FGets` no incrementa `line_count_read`; tras `CloseFile` en `MCPBridge.c:976`, `line_count_read == 0` entra en `:977-984`: `found=false`, `parse_error="empty_fixture"`, `ok=false`, `error="parse_error"`. |
| positivo, 2 lineas | El bucle incrementa `line_count_read` en `MCPBridge.c:955-957`, copia el ultimo valido en `:970-972`, luego `line_count_read == 0` es falso en `:977` y mantiene exito en `:986-989`. |
| missing | Sin cambios: `OpenFile` devuelve `0`, rama `fixture_not_found` en `MCPBridge.c:941-948`. |
| bad json | Sin cambios: si `ReadFromString` falla, rama `parse_error` en `MCPBridge.c:959-967`. |

La semantica de errores del contrato permite `parse_error` como `ok=false` en `DayZ_MCP_dev\plans\2026-06-08-fase2-observacion.md:70-72`; no se invento codigo nuevo.

## Bloque C - Hallazgos

- Desajuste documental: `DayZ_MCP_dev\plans\2026-06-08-fase2-observacion.md:109-110` titula la ruta como "EXACTA", pero la misma regla dice rechazar si no empieza por `$mission:dayz_mcp/`, y el harness real usa tres rutas bajo ese prefijo (`mcp_client.py:21-23`). Recomiendo disambiguar el plan a "prefijo fijo + basename sin separadores/dotdot". No edite el plan.
- `DayZ_MCP` / `DayZ Projects` no tienen `.git` detectable con `git -C`, asi que no adjunto hash ni diff Git. La verificacion se hizo por lectura directa de rangos.

## Bloque D - Handoff a Claude

Falta por orquestar fuera de esta sesion correctiva:

1. Verificacion host-direct de Claude.
2. Re-deploy PBO `P:\Mods\@DayZ_MCP`.
3. Un run in-game agrupado: recuento de clases / `[MCP-POC] config loaded` + suite C2 sin regresion.

No se ejecuto build, deploy, in-game, AddonBuilder ni harness por restriccion explicita del prompt.