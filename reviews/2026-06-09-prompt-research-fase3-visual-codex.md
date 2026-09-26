# Prompt Codex — research dual fase 0 — Fase 3 (Visual)

Patrón: research-dual (R24), mitad Codex. Claude lanzará sus Explore en paralelo e independientes en la sesión fresca (anti-contaminación). Generado por Claude 2026-06-09. Copiar entre marcadores.

```
===== PROMPT INICIO =====

Tarea: RESEARCH (descubrimiento, NO implementación) de fase 0 para la Fase 3 (Visual) de DayZ-MCP: cámara server-authoritative + captura por window-grab. SOLO investiga y documenta hechos verificados con `path:line`; NO escribas código, NO toques bridge/harness/PBO, NO propongas plan todavía.

## Contexto
Fase 3 = criterios D1 (`camera_set`/`camera_get`) y D2 (`capture_screenshot` por window-grab) del product-spec. `MakeScreenshot` está roto (T165276) → la vía es window-grab externo del cliente renderizado (pasivo). El bridge es `modded MissionServer` server-side con transporte RestApi async (fases 0-2 ya cerradas y PASS in-game).

## Carga inicial obligatoria
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md   (criterios D1/D2 + Intent "Visual").
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-mcp-architecture.md   (§Visual + tool surface camera/screenshot + transporte).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\CLAUDE.md   (gotchas: MakeScreenshot roto, window-grab `Graphics.CopyFromScreen` meanB 65 validado, SetHeader solo Content-Type, *_now prohibido).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-harness-apis.md   (APIs Enforce verificadas; busca cámara/graphics/render).
5. C:\Users\guill\ObsidianVault\AI\20_Knowledge\lessons-learned.md + C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md   (LL-118: incluir estos en el sweep).

## Dimensiones a investigar (cada hecho con cita `path:line` del vanilla bajo P:\scripts\)
1. **API de cámara**: ¿cómo se posa/lee la cámara server-authoritative? `GetCamera`, free-cam, clases `Camera`/`CinematicCamera`, pose (pos+orientation+roll) + FOV. ¿Server-controlable o client-only? Si client-only, ¿cómo lo orquesta el bridge server-side? Firmas reales.
2. **Window-grab**: confirmar `Graphics.CopyFromScreen` (firma, qué captura, formato), encode a PNG, límite `ImageContent` <1MB. ¿Quién ejecuta la captura — el server Python en el host sobre la ventana del cliente DayZDiag? ¿Cómo localiza/enfoca la ventana?
3. **Gate de estabilidad de frame**: cómo asegurar que el frame está renderizado/estable antes de capturar (settle post-spawn/anim, doble buffer). ¿Señal in-engine o medida externa (diff de frames)?
4. **Prior-art MCP screenshot/camera**: patrones Blender/Unreal/Unity MCP para camera set/get + screenshot (ImageContent base64 <1MB, status-before-capture). Extraer la anatomía, NO copiar.
5. **Transporte de la imagen**: el window-grab es externo al engine → ¿la imagen viaja por el transporte async del bridge (RestApi) o por un canal lateral del server Python host? Integración con la tool surface MCP.

## Restricciones
- R2/R2.1 cite-then-verify: cada API/firma con `path:line` del vanilla real. Lo no verificable → `[ASSUMPTION]` explícito.
- NO mires el research de Claude (en paralelo independiente — anti-contaminación R24).
- NO código, NO plan, NO tocar bridge/harness/PBO. Solo el doc de research.

## Output esperado
Archivo: C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\research\2026-06-09-fase3-visual-codex.md
Estructura (research-template): Hechos verificados (path:line) · APIs candidatas por dimensión · Conflictos/incógnitas · `[ASSUMPTION]` marcados · Recomendación de enfoque (server-cam vs client-cam; canal de la imagen).

===== PROMPT FIN =====
```
