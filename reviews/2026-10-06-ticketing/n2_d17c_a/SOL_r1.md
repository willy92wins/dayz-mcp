# gpt-6.1-sol review, n2_d17c_a, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — A live replacement can inherit the old client’s death verdict.**  
[tools/dayz_mcp/loopback.py:2680](C:/Users/guill/dzmcp_gauntlet/n2_d17c_a/r1/ws/tools/dayz_mcp/loopback.py:2680), [tools/dayz_mcp/process_lifecycle.py:4292](C:/Users/guill/dzmcp_gauntlet/n2_d17c_a/r1/ws/tools/dayz_mcp/process_lifecycle.py:4292).

The probe receives only `run_id`; its result is never tied to the pinned destination’s PID/creation time or the manifest records actually observed.

Executable scenario:

1. Run `run-race` contains server PID 9100 and an old client record for PID 9101. PID 9101 has a complete, mismatching native identity.
2. Reattach client PID 9102. Production replacement retains the foreign record ([process_lifecycle.py:4397](C:/Users/guill/dzmcp_gauntlet/n2_d17c_a/r1/ws/tools/dayz_mcp/process_lifecycle.py:4397)). Production confirms the new binding **before** publishing its manifest record ([process_lifecycle.py:3741](C:/Users/guill/dzmcp_gauntlet/n2_d17c_a/r1/ws/tools/dayz_mcp/process_lifecycle.py:3741)).
3. Invoke `state.enqueue_command("camera_get", {}, peer="client")` during that interval.
4. The helper clones the old manifest. While it probes PID 9101, reattachment publishes PID 9102 and restores `RUNNING`.
5. The binding pin remains unchanged, so admission accepts the obsolete `dead` result.

A deterministic, in-memory reproduction using the production helper, replacement and confirmation methods produced:

```text
production replacement retains foreign record: ([], None) [9100, 9101]
enqueue: (409, {'error': 'client_process_gone', ...})
pin unchanged: True
current registered liveness: alive
queue and next id: [] 1
```

Expected: discard this death observation and retain existing admission for the live replacement.

**Suggested fix:** associate death evidence with the registered identities actually probed and the pinned destination. If the pinned client was absent from that snapshot, treat the observation as unknown. Add a regression covering confirmation before manifest publication; keep native probing outside the loopback lock.

**F2 — P3 — Required contract documentation is missing.**  
[product-spec.md:141](C:/Users/guill/dzmcp_gauntlet/n2_d17c_a/r1/ws/product-spec.md:141), [product-spec.md:148](C:/Users/guill/dzmcp_gauntlet/n2_d17c_a/r1/ws/product-spec.md:148).

H6/H13 remain unchanged. This is a documentary omission without an executable runtime failure scenario. The implementation report acknowledges it; the conflicting edit boundary is addressed below.

## SPEC COVERAGE

| Specification bullet | Status | Assessment |
|---|---|---|
| Read-only helper using `_classify_registered_process` | done | Reuses native identity classification; no lifecycle operation lock. |
| Any matching live record → alive | done | Implemented. |
| All relevant records gone/foreign → dead | done | Implemented for the captured manifest snapshot. |
| Otherwise → unknown | done | Guard failures and unreadable identity pass through. |
| Exact run without client/offline records has no client process | done | Represented by `none`; admission passes through. |
| Unavailable lifecycle or unreadable/missing run → unknown | done | Helper/wrapper handle these cases. |
| Ambiguous destination preserves existing refusal | done | Fence resolution precedes probing. |
| Central gate for client-peer admission | done | Covers the current 29-command census; internal cleanup bypass remains. |
| Existing argument, peer, authorization and ownership validation | done | Retained before probing. |
| Resolve and classify the same exact destination | **wrong** | Manifest evidence can describe the previous client: F1. |
| Preserve collision, ownership, retirement and unavailable-state errors | done | Fence checks run before and after probing. |
| Native probing outside `_lock`; no lifecycle operation lock under it | done | Source inspection and reproduction confirm this. |
| Revalidation prevents changed destinations inheriting death | **wrong** | Binding changes are checked, but registration changes are not: F1. |
| HTTP 409, named token, run ID and bounded reattach hint | done | Implemented. |
| Preserve token through `ToolError` translation | done | Whitelisted; isolated public-token test passed. |
| Death requires process evidence, not polling/screenshots/use state | done | Native registered identities determine the verdict. |
| No queue publication, termination, reap, relaunch or ownership change on refusal | done | Refusal precedes publication; reservation is aborted. |
| Update H6/H13 | **missing** | F2. |
| Changelog and common refusal documentation | done | Added at `CHANGELOG.md:23`. |
| Host screenshot behavior unchanged | done | Outside the changed admission surface. |
| Compatibility; no wire/PBO/reseal/manifest migration | done | No such changes in the diff. |
| Six named tests and requested controls | done | Present, but the binding-race test misses F1. |
| Later owner-authorized live check | **missing** | Deferred; not performed here. |

## GATE GAP

The supplied gate passes with **26 targeted tests, 5,613 fast-tier tests and zero new failures**; two baseline failures remain.

The new race test replaces the entire classification helper and changes the binding during the probe (`test_client_process_gone.py:328`). It cannot detect F1, where confirmation happens **before** admission captures its pin and manifest publication happens **during** native probing. The helper snapshots and revalidation then describe different registrations despite an identical binding pin.

## PREMISE

The original implementation brief requires H6/H13 updates while permitting edits only under `tools/dayz_mcp/`, `tools/tests/` and `CHANGELOG.md`. Those requirements conflict. The orchestrator should amend that boundary; the documentation omission alone is nonblocking.

## NOT VERIFIED

- Full targeted-suite execution here: the project venv imports correctly, but fixture setup fails because the read-only sandbox has no writable temporary directory. Those environment errors are not implementation failures.
- Independently rerunning the complete fast tier or full suite.
- Real native-process timing, owner-authorized closure, or in-game `camera_set`.
- The race reproduction used controlled native snapshots and in-memory manifest storage; it establishes the code defect, not its frequency in production.

No files were modified.

