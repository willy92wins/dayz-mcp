# MANIFEST - council type->classname

tipo: COBERTURA
run_id: council-type-classname-20260829
pregunta: Que se rompe si se renombra el kwarg `type` a `classname` en las tools del MCP.
brief_path: reviews/2026-08-29-council-type-classname/BRIEF.md
brief_sha256: 5e06bd527093de07f2f96921020a95e5b9381c8026fcaa14f8ab65a8f32b1bb9
arbitro: orquestador (Claude, esta sesion). NO es lane. Sin `arbitro=lane`.
fallback: ninguno declarado. Si una lane muere, N baja a 2 y se dice.
justificacion_n: N=3 es el paso 1 multi-lane por defecto (C2). Tres familias distintas
  porque la pregunta es de COBERTURA: lo que importa es que se nos escapa, no quien acierta.

## universo_de_cifras (C8)

- "6 tools con kwarg `type`" = censo AST sobre funciones async decoradas con @app.tool en
  DayZ_MCP_dev/tools/dayz_mcp/server.py (4380 lineas). Cruzado con regex: mismo resultado.
  Lineas: 2976 world_spawn, 3115 telemetry_read, 3160 vehicle_prepare_fixture,
  3318 object_anim, 3348 infected_drive, 3429 object_inspect.
- "2 tools con kwarg `classname`" = mismo censo. Lineas: 3398 inventory_give, 4106 action_use.
- Cifras de terceros DESCARTADAS por no cuadrar con el censo: "9 tools" (del orquestador,
  error propio al sumar 7+2) y "7 tools" (de la sesion dayz-projects-c7). Ninguna se usa.

## lanes

| id | proveedor | binario | modelo_pin | puede_leer_arbol | cuota_pre | output |
|---|---|---|---|---|---|---|
| codex  | OpenAI/Codex | run-codex-lane.cmd | gpt-5.6-sol | si | 23,0% ventana semanal @ 04:28 | lanes/codex/out.md |
| grok   | xAI          | .grok/bin/grok.exe | grok-4.6   | si | suscripcion, binario presente 140810568 B | lanes/grok/out.md |
| claude | Anthropic    | Agent tool in-process | claude-opus-5 | si | sin cuota externa | lanes/claude/out.md |

Ceguera: las tres reciben el MISMO fichero BRIEF.md por puntero. Ninguna ve la salida de
otra. Lo que varia por lane son FLAGS de invocacion, nunca el texto (C5).

## desviacion declarada

La lane `claude` NO va por kind=prime-agent del dispatcher. Motivo (pre-flight C3): esa via
ha dado ERROR las dos veces que se uso en este host (jobs skill-templates-20260828 y
skill-templates-20260829, ambos "Opus ERROR", uno por quoting de bash -lc y otro por timeout
sin out.md). Va por subagente in-process apuntando al mismo BRIEF.md, que es el patron
"un fichero, N punteros" de C5. Su out.md lo persiste el orquestador al recibir (C12).
