# GAUNTLET 2026-09-26 — tickets de causas raiz -> claude 5.5 implementa / sol 6.0 revisa

Orquestador: @hermes (GLM-5.3-Flash-EXL3). Revisor: gpt-5.6-sol (codex exec read-only).

## Entregado

| ticket | rama (pusheada) | verdicto Sol | arbitraje @hermes |
|---|---|---|---|
| #102 GamePeer caps=/ach= (P1, ~28 fallos) | feature/claude-102-lane-claude @ 48fe322 | CHANGES_REQUIRED | APRUEBO (hallazgo PREEXISTE en main: standalone import de test_arg_contract_hash; verificado en cc02221; con PYTHONPATH=tools da OK) |
| #103 disclosure 80-char vs contrato (P2, ~16) | feature/claude-103-lane-claude @ 03873ed | APPROVE | - |
| #104 deriva residual ficha a ficha (P3, ~12) | feature/claude-104-lane-claude @ 31620b5 | APPROVE | - |
| #105 bug037 regresion real (nuevo) | (issue nuevo, sin fix) | repro verificado por Sol | procede issue propio |

Suite: 4174 tests, baseline 56F/5E/10S -> tras las tres ramas ~29F (quedan: 1F familia A residual
test_release_cancels_only_owner_queued_commands [deuda C.1], bug037 [#105], a429 capa2 [duplicado #102],
y los fallos de las otras familias segun se miren las ramas por separado: al fusionar las tres la
cifra debe caer a ~3).

## Como se hizo (aprendizajes operativos)

1. 1 lane = 1 git worktree (scratch/lane-NNN). Lanzar varias claude -p sobre el mismo cwd se pisan.
2. claude -p --safe-mode necesita allowlist explicito: Read/Write/Edit/Glob/Grep + Bash(git ...:*) +
   Bash(gh issue view:*) + Bash(*venv-mcp*). Sin allowlist la lane describe el fix aunque no puede ejecutar nada.
3. El venv .venv-mcp no existe en worktrees: invocar por ruta absoluta al checkout principal (funciona).
4. El .pth editable del venv enlaza dayz_mcp al checkout principal: en worktree, los modulos sin la
   cabecera _TOOLS_DIR prueban produccion de OTRA ruta (hallazgo de la lane 104, importante).
5. El sandbox read-only de codex no tiene tmp escribible: los tests con TemporaryDirectory se bloquean;
   los veredictos usaron variantes in-memory equivalentes.
6. Sol marco CHANGES por un hallazgo preexistente en main: el arbitraje del orquestador exige reproducirlo
   en la base antes de aceptarlo como bloqueo (regla anti-falso-ROJO).
7. El "triaje previo" del orquestador tenia 2 imprecisiones que las lanes corrigieron con evidencia
   (suggested_calls no era la regla max-2 de e72ff5b sino el gate #94; telemetry 3/4 no eran disclosure):
   el triaje es hipotesis, no veredicto.

## Veredictos completos

- rev-102: scratch/rev-102/VEREDICTO.txt (backup en _orquestacion/veredictos/rev-102.txt)
- rev-103: _orquestacion/veredictos/rev-103.txt
- rev-104: _orquestacion/veredictos/rev-104.txt
- Comentarios publicados en los issues (#102/#103/#104) con el veredicto y el arbitraje.

## Pendiente (decision del dueno)

- Merge de las tres ramas a main (sin PR segun el brief; el dueno decide orden).
- Deuda: C.1 (rediseno de test_release_cancels...), C.4 (shims standalone en 2 modulos), issue #105 (bug037).
- Fase 2 de la prueba de orquestacion: probar DayZ-MCP in-game (necesita la caja libre).
