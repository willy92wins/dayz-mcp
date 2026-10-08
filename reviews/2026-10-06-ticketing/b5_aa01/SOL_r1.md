# gpt-6.1-sol review, b5_aa01, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — Older-PBO rejection happens after mutation.**  
[tools/dayz_mcp/server.py:5335](C:/Users/guill/dzmcp_gauntlet/b5_aa01/ws/tools/dayz_mcp/server.py:5335) executes the bridge command before validating the child ID at line 5338. The validator then discards the successful receipt and raises `item_object_id_unavailable` at line 5279.

**Executable scenario:** load the base PBO, with registered owner `7` being a WoodenCrate with room for two Apples. Call twice:

```json
{"object_id":7,"classname":"Apple","dest":"cargo"}
```

Both calls report `item_object_id_unavailable`, but the crate receives two Apples. Neither item has a registered child ID. For an empty Armband slot, the first attachment similarly succeeds behind an error; retry returns `slot_occupied`.

This path is reachable: base and modified bridge versions are both `"10"`; command capabilities and argument-contract hash are unchanged. Existing readiness checks cannot distinguish the new response contract.

A Python probe with simulated older-bridge state reproduced these outputs; the base Enforce creation paths substantiate the mutation mechanism. This was not an in-game test.

**Suggested fix:** reject an unsupported response contract before enqueueing, using a verifiable version/capability signal. Alternatively, preserve the legacy successful receipt and explicitly report that creation occurred without a removable child ID. Add a regression checking mutation state and retries, rather than only checking that an exception occurs.

## SPEC COVERAGE

| Specification requirement | Status |
|---|---|
| Correct the removal gap while preserving destination-owner identity | **Done**, statically, for the updated PBO |
| Register `attachedItem` under `command.id` after attachment/cargo postconditions | **Done** — `addon/scripts/5_Mission/MCPBridge.c:2007` |
| Extend `MCPInventoryAttachReceipt` with `item_object_id` | **Done** — `addon/scripts/5_Mission/MCPMessages.c:556` |
| Preserve top-level owner ID; return child ID inside the receipt | **Done** — `MCPBridge.c:2014` and `:2020` |
| Leave `DispatchObjectDelete` unchanged; avoid a special deletion API | **Done** — method body compared against base and identical |
| Document same-run identity and owner/item distinction | **Done** — receipt comments and Python tool descriptions |
| Update registry-origin wording in inspect, hands and related consumers | **Done** — relevant descriptions and registry comments updated |
| Offline registration and distinct-field regressions | **Done** — new source/contract tests |
| PBO rebuild; no launcher reseal | Rebuild **missing**, explicitly deferred; no launcher changes |
| Attach A → inspect child → delete → owner survives → slot free → attach B; repeat for cargo and repeated deletion | **Missing**, explicitly deferred to the in-game cycle |

The additional older-PBO guard is **wrong** as a fail-closed mutation safeguard: see F1.

## GATE GAP

The older-PBO test at `tools/tests/test_inventory_attach_item_id.py:125` mocks an already-successful bridge response and checks only the exception. It cannot detect creation before rejection, occupied slots, or cargo duplication on retry.

Source-text checks also cannot prove Enforce compilation, actual JSON serialization of the new field, registry resolution, or engine deletion freeing the slot/cargo while preserving the owner.

## PREMISE

The original removal-gap and owner-ID premises are correct. Existing registry-based `ObjectDelete` is the appropriate mechanism.

The report’s “fails closed” claim is incorrect for older PBOs: it rejects the response after creation. The supplied gate establishes no new offline failures; it does not establish compilation or in-game correctness.

## NOT VERIFIED

- Enforce compilation, rebuilt/deployed PBO contents, and the decisive in-game cycle.
- Full fast-tier suite independently; its supplied result retains two baseline failures.
- Real older-PBO execution in game.

Independent focused validation passed: **48 tests, 1 skipped**, using the project venv. The initial system-Python attempt lacked `anyio`. No files were modified and no commit was made.

