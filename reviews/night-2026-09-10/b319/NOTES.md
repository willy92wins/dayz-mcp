# fb-20260907-154233-b319 — server: a closed-schema rejection names the unexpected and missing argument keys

Night lane 2026-09-10. Implemented by Grok (cursor-grok-4.6-xhigh via Cursor CLI) in an
isolated worktree branched from `30f257b96b`; reviewed by Codex (gpt-5.6-sol);
gates re-run by the receiver (Claude) with `gate-check.mjs --reverify`, never taken from the
worker's report. Full-suite baseline in the worktree: 15 red ids (docs tests that read
untracked files, the launcher registry/reconcile pair, and the ticket's own red where it
applies); the gate is "no NEW red by name", not zero reds.

## What changed

Closed-schema MCP tools now reject unknown arguments with a message that keeps the prefix `bad_args: unexpected arguments`, names identifier-shaped unexpected keys (unsafe names become `<unsafe>`), lists sorted accepted schema properties, and appends sorted missing required keys when any are absent. `tests.test_lote_m_products` and `tests.test_server_freshness` are green. The full suite still has the same 15 baseline red names and no new ones. HEAD did not move; only the two allowed code files plus STATE.md changed.

Files: tools/dayz_mcp/server.py, tools/tests/test_lote_m_products.py

## Gates (receiver's run)

- [x] G1: the module holding the closed-schema tests passes, old assertions included
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=Ran 18 tests in 2.326s | OK
- [x] G2: the worker's fb_b319 tests exist and fail on the pre-fix server.py, so they discriminate
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=restored: verified | red before fix confirmed
- [x] G3: the full suite adds no red by name over the 15-id worktree baseline
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=full suite: ran 3403 tests in 312.127s; reds now 15, baseline 15 | no new reds
- [x] G4: only the two allowed files changed and nothing was committed
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=touched: ['tools/dayz_mcp/server.py', 'tools/tests/test_lote_m_products.py'] | write-set ok
- [ ] G5: the Codex Sol review of the diff leaves no P0 or P1 open
  pending

## Codex review

Verdict: APROBAR

- [P2] tools/tests/test_lote_m_products.py:31 -- La prueba del prefijo al inicio es tautológica: el helper recorta la excepción desde la primera aparición del propio prefijo, por lo que `test_fb_b319_message_starts_with_legacy_prefix` en la línea 251 pasaría aunque el payload antepusiera texto inesperado -- Un mensaje `junk bad_args: unexpected arguments: bogus` se transforma en uno que empieza por el prefijo; la prueba debe retirar solo el envoltorio conocido de FastMCP o comprobar directamente el `ToolError`.
