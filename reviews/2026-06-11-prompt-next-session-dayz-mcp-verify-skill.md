# Bootstrap sesión nueva — crear skill `dayz-mcp-verify` + estreno con A6_MK47

> Generado 2026-06-11 al cierre de la sesión R21-F4/X.5 de DayZ-MCP (proyecto CERRADO).
> Adjudicado con usuario: skill NUEVA (no extender dayz-test-ingame) · estreno = re-test v7
> de la Mk47. Pegar como primer mensaje de la sesión nueva.

===== PROMPT INICIO =====

Sesión nueva. Tarea: crear la skill **`dayz-mcp-verify`** (verificación in-game de mods
conduciendo DayZ vía las tools MCP de dayz-mcp) y estrenarla con un caso real: el re-test
pendiente de la **Mk47 v7** (mod `A6_MK47`). El cwd suele ser el padre
`C:\Users\guill\OneDrive\Documentos\DayZ Projects\`.

## CARGA INICIAL MÍNIMA
1. `~/.claude/skills/_shared/prompt-conventions.md` + `~/.claude/skills/skill-conventions/`
   (R12 — antes de escribir cualquier SKILL.md).
2. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md` (LIVE-STATE:
   invariantes del server MCP, 11 tools, GATE4B-LIM).
3. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\README-mcp.md`
   (orden de arranque documentado del server+bridge).
4. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\A6_MK47_dev\HANDOFF.md` (estado vivo del
   target: qué debe verificar el re-test v7).
5. SKILL.md de `dayz-test-ingame` (el launcher que esta skill COMPONE, no duplica).

## HECHOS OPERATIVOS YA VERIFICADOS (2026-06-11 — NO re-descubrir)
- `dayz-mcp` registrado en Claude Code **scope user** con `--require-version`; `√ Connected`
  verificado desde cwd arbitrario (editable install). Una sesión nueva YA ve las 11 tools
  (`exec_enforce` solo con `--enable-exec-enforce`, y su ejecución NO funciona en server
  headless — GATE4B-LIM, no intentarla).
- **Key**: el server MCP usa `DayZ_MCP_dev\tools\.dayz_mcp.key`; el bridge in-game lee
  `dayz_mcp.json` de los profiles. Samples CORRECTOS (URL+key+pollHz) en
  `tools\_mcp_config\{server_profiles,client_profiles,mpmissions\dayzOffline.chernarusplus}\`.
  OJO: `run-fase3.ps1` siembra OTRA key (harness) → para uso MCP, sembrar los json de
  `_mcp_config` en los profiles con los que se lance el diag.
- **Lanzamiento**: server+client DayZDiag con `-mod=@DayZ_MCP;@<mod-target>` (la captura es
  del CLIENTE renderizado → hacen falta ambos). Lo hace dayz-test-ingame/.ps1 — el MCP no
  lanza el juego (G-6, diseño cerrado).
- Con el juego corriendo, `bridge_status` debe dar `version_state {server: ok, client: ok}`
  (bridge 4B manda `ver=4~<game>`). `legacy_blocked`/`never polled` = el bridge no está
  polleando (config/key/arranque mal).
- **Qué verifica cada tool** (núcleo del playbook): `world_spawn` (classname del mod →
  ¿carga? ¿id?), `camera_set` free/orient + `capture_screenshot` (¿se ve? winding/texturas/
  proporciones; presupuesto ~25k tokens ≈ 240-320 px), `scene_raycast` (colisión: hit +
  object_type + normal), `telemetry_read object_at` (found, pos real, orientation, health,
  attachment_count), `world_time_set`/`world_weather_set` (condiciones de luz para capturas
  comparables), `vehicle_enter` (solo vehículos).
- **Límites conocidos**: sin acciones de player/UI (puertas, inventario interactivo — fuera);
  BUG-009 autoconexión cliente flaky (mitigación `-WaitInGameSeconds 300` + retry); BUG-024
  (un timeout de tool deja el comando en cola → al reconectar el bridge ejecuta zombies:
  tras timeouts, reconciliar con `bridge_status` antes de seguir); captura: presupuesto duro
  ~25k tokens por imagen (no pedir más resolución).

## OBJETIVO DE LA SKILL (contrato adjudicado)
- Skill nueva `dayz-mcp-verify` en `~/.claude/skills/`: orquesta (a) build/deploy/launch vía
  dayz-test-ingame CON `@DayZ_MCP` añadido y los json de `_mcp_config` sembrados; (b)
  verificación conduciendo las tools MCP; (c) reporte con evidencia (capturas + JSON de
  telemetría/raycast + veredicto por criterio).
- **Playbooks por tipo de mod** con criterios pass/fail explícitos: objeto estático
  (spawn→visible→colisión→placement), item/arma (spawn→visible desde N ángulos→proporciones
  vs referencia→attachments), edificio (colisión por raycast multi-punto, NO puertas),
  vehículo (spawn→vehicle_enter→telemetría). Declarar SIEMPRE qué NO cubre (acciones/UI).
- Triggers de description: "auto-probar mod", "verificar in-game con MCP", "smoke visual del
  mod", "probar sin tocar el juego", "re-test visual".
- La skill DECLARA sus dependencias: dayz-mcp registrado (user scope), juego lanzable vía
  dayz-test-ingame, los dos mods en P:\Mods.

## ESTRENO (eval real de la skill)
Re-test v7 de A6_MK47 (pendiente en su HANDOFF): spawn del arma, capturas multi-ángulo
(geometría post regla-de-imports: winding/orientación/proporciones), raycast de colisión,
telemetry (attachments). Las verificaciones que el HANDOFF de A6_MK47 marque como manuales
(disparo, manejo) quedan FUERA y se declaran.

## REGLAS
- R12 antes de escribir la skill; R5 (agrupar todo el test in-game en UNA sesión de juego).
- La skill COMPONE dayz-test-ingame — no duplica su lógica de build/deploy/launch.
- PROHIBIDO re-litigar el diseño de dayz-mcp (proyecto CERRADO; invariantes en su LIVE-STATE).
  Si falta una tool (p.ej. acciones de player), se ANOTA como límite — no se implementa.
- Cierre: post-session (handoff + estado A6_MK47 si el re-test concluye algo).

===== PROMPT FIN =====
