# gpt-6.1-sol review, n3_docs2, round 1 (unedited)

VERDICT: APPROVED

## FINDINGS

**F1 — P3 — Unverified display-sleep mechanism stated as established behavior.**  
`tools/dayz_mcp/server.py:6069`; asserted by `tools/tests/test_tool_description_caveats_2.py:82`.

Metadata scenario: build with `ServerConfig(mode="client", client_platform="claude")`, then request MCP `tools/list`. The published `capture_screenshot` description says a display entering power-save “can still produce frame_client_all_black.”

The specification leaves that mechanism at **NEEDS_REPRO**. Source establishes the probe’s assurance gap, but does not establish display sleep as the cause of black client captures. Remove the parenthetical or label it an unreproduced hypothesis. **P3:** no executable capture failure demonstrating or disproving that mechanism was established here.

No P1/P2 findings.

## SPEC COVERAGE

| Specification requirement | Status |
|---|---|
| RV `radius=0` uses effective `0.05 m`; positive accepted values pass through | **Done**, `server.py:4515`; supported by `MCPBridge.c:2496,2523,2528`. |
| `pos` is engine-returned, without contact reconstruction | **Done**, `server.py:4519`; supported by `MCPBridge.c:2957`. |
| Bullet casts do not use radius | **Done**, `server.py:4522`; supported by `MCPBridge.c:2990`. |
| Floor interpretation limited to the reported geometry | **Done**, `server.py:4524`. |
| Preserve behavior/result shape; no PBO or reseal | **Done** in the submitted diff. Runtime implementation files match base byte-for-byte. |
| Do not implement a universal contact formula | **Done**; none added. |
| Inspect published metadata and compact/full discovery | **Done** through tests and an independent in-memory MCP protocol check. Compact pre-lease metadata truncates before the caveats; full metadata exposes them after lease acquisition and immediately for Claude. |
| Separate wording checks from behavioral checks | **Done**, separate test classes at `test_tool_description_caveats_2.py:27,116`. |
| Independent static slab, normal simulation time | **Missing**; no experiment supplied or executed. |
| Downward RV casts at `0`, `0.01`, `0.05` | **Missing**. |
| Upward underside and oblique casts | **Missing**. |
| Matching bullet casts at `0`, `0.05` | **Missing**. |
| Record positions, normals, object/component identities and measured plane height | **Missing**. |
| Desktop accessibility/brightness clarification | **Done**, `server.py:6069`. |
| No display-awake or later-capture guarantee | **Done**, same line. |
| Preserve uncertainty about sleeping-display mechanism | **Wrong** in the added parenthetical; F1. |
| No new power-state API, power-policy change, synthetic input or wake guarantee | **Done**; none added. |
| Awake control, unchanged sleeping run, independently observed display state, repeated probes/captures and wake recovery | **Missing**; no reproduction supplied or executed. |
| Keep NavMeshGenerator discovery separate | **Done**; untouched. |

The missing experiments remain follow-up evidence work; they do not block this documentation-only merge.

## GATE GAP

- Wording tests verify that sentences exist, including F1’s unsupported claim; they cannot establish those sentences’ empirical accuracy.
- Behavioral tests mock `runtime.call_bridge` (`test_tool_description_caveats_2.py:130`). They cannot detect incorrect Enforce substitution, engine geometry semantics or bullet-radius dependence.
- Compact tests call `_compact_description` directly (`test_tool_description_caveats_2.py:93,107`). They could miss incorrect protocol-handler registration or failure to restore full descriptions. My independent protocol check found neither defect.
- Neither the new tests nor the reported fast-tier gate reproduces display sleep or actual client capture behavior.

## PREMISE

The batch is a documentation clarification, not proof that either in-game mechanism has been reproduced.

“Positive values pass through” applies to **accepted** values: the bridge rejects radius greater than `5.0` (`addon/scripts/5_Mission/MCPBridge.c:20,2523`).

The supplied gate reports **zero new failures**, not a globally clean suite: two baseline failures remain.

## NOT VERIFIED

- Both specified in-game experiments, deployed PBO provenance and actual display power state.
- The full fast tier was not rerun; its supplied result was treated as orchestrator evidence.
- `REPORT.md:98,102,106` still contains pending gate, deviations and verification sections.

Locally, `python -B -m unittest tests.test_tool_description_caveats_2` passed **10/10** using the project venv. The independent MCP discovery check also passed. No files were modified.

