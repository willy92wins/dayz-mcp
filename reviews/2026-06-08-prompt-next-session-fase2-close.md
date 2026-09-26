# Prompt arranque sesión nueva — DayZ-MCP (cerrar fase 2 / elegir frente)

Patrón next-session bootstrap. Para pegar al abrir una sesión nueva (Claude Cowork; nota Codex al final). Generado 2026-06-08.

```
===== PROMPT INICIO =====

Sesión nueva. DayZ-MCP: cerrar fase 2 (Observación) y elegir el siguiente frente.

CARGA INICIAL MÍNIMA (no abrir más cosas todavía). Lee solo, en este orden:

1. C:\Users\guill\ObsidianVault\AI\00_System\workflow.md
2. C:\Users\guill\ObsidianVault\AI\00_System\vault-index.md
3. C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-06-08-DayZ_MCP-fase2-observacion.md
   (el handoff de la sesión anterior — sobre todo "Próximos pasos", "Bloqueos / preguntas abiertas" y "Notas para la próxima sesión").
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md
   (header vivo — léelo A MANO; el hook SessionStart no lo localiza si arrancas en el cwd padre `DayZ Projects`).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md
   (contrato: qué criterios están ✓ y cuáles faltan).
6. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md
   (BUG-008 fixed, BUG-009 open).
7. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-08-prompt-codex-sesion-27-r21-fase2.md
   (el prompt R21 ya listo a correr por Codex).

NO releas los research separados (`-claude.md`/`-codex.md`), ni los logs de los runs fallidos (`_fase2/run_2026...`), salvo contradicción concreta.

OBJETIVO DE LA SESIÓN
Cerrar fase 2 (procesar la R21) y decidir/arrancar el siguiente frente.

PRERREQUISITOS antes de cualquier generación pesada:
(a) Correr el prompt R21 (#7) por Codex; procesar sus findings: P1 → corrección antes de declarar fase 2 cerrada; P2/P3 → backlog (`bug-ledger.md`). La mitad Claude de la R21 ya está cubierta de facto (receptor read host-direct de los handlers + el PASS in-game) — NO rehacerla.
(b) Decidir CON EL USUARIO el siguiente frente: **fase 3 (Visual: cámara + window-grab)** o **fase client-peer** (conducción B3, diferida). Si se abre frente nuevo → arrancar el research dual fase 0 de ESE frente (R24), no del actual.

YA CERRADO en sesiones anteriores (NO rediscutir):
- Fase 0 + Fase 1 (B1/B2) + Fase 2 (C1/C2) PASS in-game.
- C1 primaria = `RayCastBullet` (D-09); `GetCrosshairObject` descartado (cliente-only) → from/to explícitos.
- B3 movimiento = client-authoritative (tool de conducción diferida a fase client-peer).
- BUG-008 fixed (harness usa el PBO). BUG-009 (flakiness autoconexión cliente) = reliability, no correctitud.
- **Gate de carga de un mod = clases del módulo in-game / `[MCP-POC] config loaded` vía el PBO desplegado `P:\Mods\@DayZ_MCP`, NO AddonBuilder** (LL-117). Re-desplegar el PBO tras editar source.

REGLAS QUE APLICAN
- R21: la doble revisión del código fase-2 es el gate pendiente; procesar findings antes de cerrar.
- R24: la fase 0 (research) de fase 2 YA está hecha; NO rehacerla. Si abres frente nuevo, su fase 0 sí toca.
- R2 / R2.1: cualquier API/path, cite-then-verify host-direct (Read/Grep, no bash sobre OneDrive).
- R18: ante ambigüedad (sobre todo el frente siguiente), AskUserQuestion en vez de presuponer.
- R11 / R5: conclusión arriba; agrupar pruebas in-game; diagnóstico offline antes de re-test.

ENTREGABLE
1. R21 procesada (findings aplicados/encolados) → fase 2 declarada cerrada.
2. Decisión del frente siguiente (confirmada con el usuario).
3. (Si frente nuevo) arranque del research dual fase 0 de ese frente.

PROHIBIDO en esta sesión
- Re-correr/re-testear fase 0/1/2 (ya PASS).
- Re-diagnosticar BUG-008 o rehacer el research fase-2.
- Usar AddonBuilder como gate de "compila"; lanzar fase 2 contra el source loose `P:\DayZ_MCP`.

===== PROMPT FIN =====
```

> Si la sesión nueva es **Codex** (no Claude): su `~/.codex/AGENTS.md` ya tiene R1-R24; añadir al inicio "tu AGENTS.md ya las tiene, las cito por número" y priorizar `workflow.md` (Codex no tiene reflejo de `vault-index.md`).
