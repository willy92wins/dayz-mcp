# REPORT aa01

## CHANGES

- `addon/scripts/5_Mission/MCPBridge.c:DispatchInventoryAttach` — after attachment and cargo postconditions, `m_RuntimeObjects.Insert(command.id, attachedItem)` follows the world_spawn registry insert. `result.object_id` remains `command.args.object_id` (the destination owner). `inventory_attach.item_object_id` is `command.id`.
- `addon/scripts/5_Mission/MCPBridge.c` comments on `RuntimeObjectId`, `hands_take`, and `ResolveCommandObject` — registry ids also come from `inventory_attach`, same run only, child distinct from the owner.
- `addon/scripts/5_Mission/MCPMessages.c:MCPInventoryAttachReceipt` — added `int item_object_id` and a comment that 0 means unset and that the top-level object id stays the owner.
- `addon/scripts/5_Mission/MCPBridge.c:DispatchObjectDelete` — not modified.
- `tools/dayz_mcp/server.py:inventory_attach` — description states owner vs child and same-run identity. `_require_inventory_attach_child` fails closed with `item_object_id_unavailable` when the child id is missing, not a positive int, or equal to a positive owner id (older PBO).
- `tools/dayz_mcp/server.py` descriptions for `object_delete`, `object_inspect`, `object_anim`, `object_doors`, `vehicle_door`, `hands_take`, and `weapon_state` — registry ids are no longer described as world_spawn only.
- `tools/tests/test_inventory_attach.py` — the two forward mocks now return a distinct `item_object_id`, because a successful tool call requires one. The assertions still check the bridge arguments.
- `tools/tests/test_fb_29c1_vehicle_door.py` — the `vehicle_door` description pin now includes `inventory_attach.item_object_id` next to `world_spawn`. The rest of that test is unchanged.
- `CHANGELOG.md` — one Unreleased Fixed line.

## TESTS ADDED

- `InventoryAttachItemIdContractTest.test_created_item_is_registered_after_both_postconditions` — fails on unmodified code because `Insert(command.id, attachedItem)` is absent.
- `InventoryAttachItemIdContractTest.test_receipt_carries_child_id_separately_from_owner` — fails because `item_object_id` is absent.
- `InventoryAttachItemIdContractTest.test_object_delete_is_unchanged_registry_delete` — pins `ObjectDelete` and the absence of an attach-specific delete.
- `InventoryAttachItemIdContractTest.test_registry_wording_includes_the_attached_item` — fails while comments name only world_spawn.
- `InventoryAttachItemIdToolTest.test_descriptions_distinguish_owner_and_child`
- `InventoryAttachItemIdToolTest.test_success_returns_distinct_child_id`
- `InventoryAttachItemIdToolTest.test_legacy_receipt_is_kept_so_a_retry_is_not_forced` — the base-PBO cargo reply is returned, the bridge ran once, and a raise-only retry does not run it again.
- `InventoryAttachItemIdToolTest.test_child_id_equal_to_owner_keeps_the_receipt`

## GATE OUTPUT

```
--- enforce linter: errors=2 baseline=2 new=0
--- tests.test_inventory_attach_item_id: rc=0
Ran 8 tests in 1.067s

OK
--- waiting for a free fast-tier slot (other batches are gating)
--- fast tier: ran=5595 baseline_ran=5587 failures=2 new=0
GAUNTLET_GATE: PASS
```

## DEVIATIONS

None. `DispatchObjectDelete` was left as it is. No `DeleteAttachment` path.

## ROUND 2 FIXES

- **F1** — `tools/dayz_mcp/server.py:_require_inventory_attach_child` no longer raises after `call_bridge`. Version, command census and the arg-contract hash are the same on the base PBO, so the call cannot be refused before enqueue. The bridge receipt is returned unchanged in its create fields, with `item_object_id_unavailable: true` and a `detail` that the item was created, that `object_id` is the owner, and that a retry must not be sent. `test_legacy_receipt_is_kept_so_a_retry_is_not_forced` checks the kept cargo receipt, one bridge call, and a retry policy that stops when the tool does not raise. `test_child_id_equal_to_owner_keeps_the_receipt` checks the same for a child id equal to the owner.

## NOT VERIFIED

PBO rebuild and in-game play. The orchestrator must, on a PBO built from this tree:

1. Attach garment A to the survivor Armband slot (by type+position or by the survivor's registry id). Read `inventory_attach.item_object_id` and confirm top-level `object_id` is still the owner (or absent when the owner was not addressed by id).
2. `object_inspect` the child id. `object_delete` that id. Confirm `deleted` is 1, the owner still exists, and the Armband slot is empty.
3. Attach garment B to the same slot and confirm it is not `slot_occupied`.
4. Repeat for cargo: create A in cargo, delete `item_object_id`, confirm the owner remains and the cargo no longer holds A, then create B.
5. `object_delete` the same child id again and confirm `deleted` is 0 and the owner remains.
6. On an older PBO (no `item_object_id`), one `inventory_attach` returns the receipt with `item_object_id_unavailable` true. Do not retry it. Confirm exactly one item was created and the owner was not deleted.

No DayZ process was started here.
