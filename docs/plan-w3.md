# Plan R26 — W3 máquina / tests Python (BUG-110 / 111 / 112)

PR [#28](https://github.com/willy92wins/dayz-mcp/pull/28). Ola W3 + W3b higiene de suite. Interpreter: `tools/.venv-mcp`. Worktree desde `origin/main`.

Este fichero es el artefacto versionado de los veredictos W3 (cierra W3-P2-02). No es un runbook de entorno.

## Veredictos

| Bug | Veredicto | Gate medido |
|---|---|---|
| **BUG-110** | **PASS** | `import dayz_mcp` carga este checkout, no `site-packages` ni un finder editable ajeno. `tests.test_bug046_startup_deadlock.TreeIdentityTest` + `tests.test_w3_bug_verdicts.Bug110CheckoutLoadTests`. Python global no es oráculo. |
| **BUG-111** | **PASS** | `DEFAULT_STABILITY_THRESHOLD` ausente de `tools/mcp_capture.py` (fuente y atributo). Capture unittest de la familia existente, exit 0. |
| **BUG-112** | **INCONCLUSO** | `tools/tests/test_capture_stability.py` no está en este árbol ni en `git log --all`. `choose_stable_frame` vive en `tools/mcp_capture.py`. El test existente `test_grab_stable_frame_keeps_the_chosen_frames_client_rect` usa 3 frames cuyo pick estable **es** el índice 1 = centro: no discrimina `return frames[len(frames)//2]`. No se inventó suite tautológica. |

## Fuera

Occupancy (W5). Enforce. PBO. `feedback.jsonl`. In-game. PARO `_APP_PACKAGED_MODULES`. Pin H4 de prosa (`test_h4_h9_contract…`) no es el gate de BUG-110; el runtime de H4 está en `Bug046H4GraceRuntimeTests`.

## Hecho cuando

Los tres ítems tienen PASS o INCONCLUSO localizado aquí y en `tests.test_w3_bug_verdicts`. BUG-112 no sube a PASS sin el fichero de suite y un mínimo que no sea el centro.
