# Prompt Codex — sesión 25 — 2º error de compile fase-2 (el mod sigue dropeando in-game)

Patrón: fix handoff (build loop). El fix de `out` (sesión 24) fue necesario pero INSUFICIENTE. Generado por Claude 2026-06-08. Copiar entre marcadores.

```
===== PROMPT INICIO =====

Tarea: encontrar y arreglar el SEGUNDO error de compilación en las adiciones fase-2 del módulo 5_Mission del mod DayZ_MCP. El fix de la sesión 24 (quitar `out` de VectorToArray) está aplicado y verificado, PERO in-game el mod SIGUE cayéndose. Esta sesión cubre únicamente ese fix. NO cambies comportamiento/contrato de las tools fase 2, NO toques el harness, NO rediseñes.

## Evidencia dura (verificada host-direct por Claude tras 3 corridas in-game)

- **Gate REAL = recuento de clases del módulo Mission al cargar** (no AddonBuilder):
  - fase-1 PASS (`_fase1\run_20260608_024848`): `Module: Mission; loaded 213x files; 463x classes` → el mod compiló y cargó; el bridge logueó `[MCP-POC] config loaded` + poll + result.
  - fase-2 con el fix de `out` (`_fase2\run_20260608_180806\server_profiles\script_2026-06-08_18-08-15.log:172`): `Module: Mission; loaded 209x files; 443x classes` → **−4 archivos / −20 clases = toda la unidad de compilación del mod (los 4 .c de 5_Mission) sigue cayéndose**. Solo aparece `[MCP-POC] spawn_actual` (del init.c de la misión), NUNCA `config loaded` → el bridge no tickea.
- **NO es PBO stale**: no existe ningún `.pbo` en `DayZ_MCP\` (filepatching lee el source loose CON el fix; el fix de `out` está confirmado en `MCPBridge.c:1023` = `void VectorToArray(vector v, array<float> a)`).
- **AddonBuilder es un GATE FALSO**: en la sesión 24 dio "Build Successful" pero in-game el módulo igual se cae. NO uses AddonBuilder como prueba de que compila. **El gate válido es el recuento de clases in-game (463 vs 443) y la aparición de `[MCP-POC] config loaded`.**
- **El error de compile es OPACO**: no aparece en el RPT ni en los script logs del server/client (Claude grepeó `compile|MCPBridge|MCPMessages|...` sin resultado). DayZ no lo está logueando donde se espera.

## Sospechosos YA DESCARTADOS (no los re-persigas)
- `VectorToArray` `out` → ya arreglado (sesión 24).
- `JsonSerializer.ReadFromString(parsed, line, parseError)` → CORRECTO vs vanilla `proto bool ReadFromString(void variable_in, string jsonString, out string error)` (`scripts\3_game\gameplay.c:100`).
- `PhxInteractionLayers collisionLayerMask = PhxInteractionLayers.BUILDING|…|FENCE` → VERBATIM de vanilla (`scripts\4_world\entities\dayzplayerimplementmeleecombat.c:671`), compila.
- `IsFiniteFloat` → está DEFINIDO (`MCPBridge.c:1046`), no es función indefinida.

## Carga inicial obligatoria
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c  (handlers fase-2 + helpers).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c  (DTOs fase-2).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\_fase2\run_20260608_180806\server_profiles\script_2026-06-08_18-08-15.log  (443 classes, sin config loaded).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\_fase1\run_20260608_024848\server_profiles\script_2026-06-08_02-49-12.log  (463 classes baseline).

## Estrategia (en orden)

1. **Surfacear el error real (lo más rápido)**: lanza el DayZDiag CLIENT **VISIBLE** (no `-WindowStyle Hidden`) con `-mod=P:\DayZ_MCP -filePatching` conectando a un server local; DayZDiag muestra un **diálogo de Script Error** con `archivo.c:línea: mensaje` cuando un script no compila. Captura ese mensaje. (Alternativa: busca un log de script-errors de DayZDiag, o un flag de tu entorno que vuelque errores de compile de filepatching.) Si tu entorno no puede lanzar el juego, ve al paso 2.
2. **Bisect contra el gate real** (recuento de clases): stubea incrementalmente las adiciones fase-2 (cuerpos de `DispatchSceneRaycast`/`DispatchTelemetryRead`/`PopulateRaycastRVProxy`/`PopulateRaycastBullet`/`DispatchTelemetryObjectAt`/`DispatchTelemetryFixtureJsonl`/`PopulateTelemetryObject`/`PopulateTelemetryInventory`, y las clases nuevas de MCPMessages.c) hasta que el módulo Mission vuelva a cargar **463** clases in-game. Eso aísla el constructo que rompe. Re-activa pieza por pieza.
3. **Verificar cada constructo nuevo restante contra vanilla EXACTO (R2)** — candidatos no descartados aún:
   - usos de `out` que queden en parámetros de método script (cualquier otro además de VectorToArray).
   - `array<string>` ops (`items.Insert(...)`, `items.Count()`).
   - `Class.CastTo` / `Car.Cast` / `EntityAI.Cast` (firmas/uso).
   - `GetGame().GetObjectsAtPosition3D(vector,float,out array<Object>,out array<CargoBase>)` — tipos exactos de los out arrays que pasas (`m_ReadyObjects`/`m_ReadyProxyCargos`).
   - `SurfaceInfo.GetName()/GetSurfaceType()` (firmas reales en `scripts\3_game\surfaceinfo.c`).
   - `CarFluid.FUEL`, `car.GetFluidFraction(...)`, `WheelCountPresent()`, `GetSpeedometer()`, `EngineIsOn()`.
   - `OpenFile/FGets/FPrintln/CloseFile/FileHandle/FileMode` (firmas en `scripts\1_core\proto\ensystem.c`).
   - `GetVelocity(IEntity)`, `float.MAX`, `vector.DistanceSq/Distance`.
   - inventario: `GetInventory()`, `GameInventory.AttachmentCount()/GetAttachmentFromIndex()/GetCargo()`, `CargoBase.GetItemCount()/GetItem()`.

## Restricciones
1. Solo el fix del 2º error de compile; mantén intacta la funcionalidad fase-2 (handlers, contrato, semántica).
2. NO toques el harness (run-fase2.ps1, mcp_*.py) — funcionan.
3. R2 cite-then-verify de cada firma vanilla que toques (path:line).
4. **El gate de "compila" es in-game (Mission=463 classes + `[MCP-POC] config loaded`), NO AddonBuilder.**
5. NO rediseñes, NO te autorrevises (R21 aparte). Fix → para.

## Output esperado (A/B/C/D)
- Bloque A — archivo(s) cambiado(s) + qué.
- Bloque B — el error de compile EXACTO que encontraste (file:line:mensaje, vía diálogo de script-error o bisect) + cómo lo confirmaste.
- Bloque C — root cause (qué constructo no compilaba y por qué AddonBuilder no lo cazó).
- Bloque D — handoff para el usuario: re-correr `& "...\run-fase2.ps1" -WaitInGameSeconds 300` y confirmar **Mission=463 classes** + `[MCP-POC] config loaded` + suite C1/C2 corriendo (fase2-verdict.json).

===== PROMPT FIN =====
```
