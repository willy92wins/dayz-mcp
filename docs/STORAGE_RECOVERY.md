# Storage rotation recovery

A launch that cannot classify `storage_1` refuses with `storage_recovery_required`.
The precise token is `storage_recovery_reason`. This page is the procedure that
token's guidance points at. The program does not repair a refused mission by
itself: it renames, and only along a state it can prove.

## Stop first

Stop retries while you inspect. Take exclusive access to the mission directory
and confirm every managed DayZ, DayZDiag and DayZServer process for that
mission is stopped. A second writer during inspection is not a recovery.

## Inventory before any repair

Copy the mission directory, or write an inventory of every name and a hash of
every file, before you move anything. You need the journal's schema, its
phase, the reserved world-backup name (D), the reserved marker-backup name (K),
and, for schema 2, `old_marker_sha256` (the hash of every original marker byte).

## Restoring a moved world

For a `storage_moved` or `marker_published` refusal caused by a renamed or
missing backup, put the **original complete tree** back at the recorded D name,
and the original marker bytes back at the recorded K name when the journal
requires that backup. Leave `storage_1` (W) absent in those moved states.

A second copy that already has the exact D name, while the original tree still
lives somewhere else, is not the same situation as W and D both present. W and
D together stay refused in the moved and final phases.

If you cannot authenticate or restore the original content, stop and escalate.
Do not fabricate a seal. Do not delete the journal or its completed record.

## Stray names

A copy such as `storage_1.modset.rotation.<txid>.json.bak` is still in the
journal family, so it blocks the launch (`journal_name_invalid` or
`journal_unreadable`). Inventory it. Quarantine only a stray you have
positively identified, and only by moving it **outside** the reserved journal
family, keeping its bytes. Do not rename an uncertain file into a canonical
active journal.

A canonically spelled name that ends in `.completed.json`, including
`storage_1.modset.rotation.garbage.completed.json`, is history, not an active
journal. Leave it.

An alias of a family name (different case, or a trailing space or period) is
not that exemption. Do not normalize it into the canonical active name.

## After restoration

Retry the launch. Keep D, K and every completed journal. `storage_rotated`
means a reset was measured. It does not mean the game has rebuilt the world.
