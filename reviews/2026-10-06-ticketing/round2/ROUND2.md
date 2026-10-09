# Round 2 of the 2026-10-08 ticketing session: outcome

Chosen by the owner after the v11b deploy: A (855c + 2986, Python), B (hold mode for continuous actions, 9941,
Enforce), C (86a3 action cursor and look-at, Enforce). Specifications by gpt-6.1-sol (`SPEC_R2A.md`, `SPEC_R2B.md`,
`SPEC_R2C.md` = `../triage3/SPEC_86A3.md` plus the measured aim units). Grok 4.7 implemented (the GX10 was at
capacity; owner's instruction), gpt-6.1-sol reviewed every round; three loop rounds per batch, at most one
orchestrator-granted consolidation round.

| Batch | Result |
|---|---|
| A 855c | **Merged** as #226 (`4233351`): a server start that answered `storage_recovery_required` is not replayed. Sealed: deploys with the next reseal, where `tools/dev/storage_recovery_joint_gate.py` is the real-joint acceptance. |
| A 2986 | **Parked** (`2986_PARKED.md`): the same family of findings reappeared in all five rounds in the single-role warning scanner. |
| B 9941 `action_hold` | **Stopped** after 3 rounds (r3: one P1 and five P2, gate red). |
| C 86a3 `action_cursor` / `player_look_at` | **Stopped** after 3 rounds plus a consolidation round (r4: gate green, two P2). |
| Infra (owner: "infra first") | **Stopped** after 3 rounds (`SPEC_R2I.md`; r3: gate green, four P2; F4, F7 and F11 reappeared in every round). |

## Shared broker blockers in B and C
Both verbs must stop the in-game work when the caller cancels, and must fence their result to the exact client
incarnation that executed it. In broker mode (`python -m dayz_mcp --client`, the production mode) neither is
possible today:
- `ClientRuntime.abandon_bridge` is a no-op, so a cancelled call never abandons the delivered command or sends its
  release.
- `/status` exposes only an 8-character `instance_prefix`, so a same-run client replacement passes a fence.

B additionally retains F3/F15 reconciliation defects, F6 drain-state defects, F5 shutdown callback retention, and the F12 regression failure.

The owner chose to build that infrastructure first (`SPEC_R2I.md`: complete binding tokens for broker clients, an
abandon-by-id operation with a release each command type registers, `ClientRuntime` cancellation using it).
Findings decreased from 13 to 7 to 4; r3 had no P1 and the offline delta gate passed, but the review remained
CHANGES_REQUIRED, and the retention, release-catalog and receipt-attribution findings kept coming back. The owner decided to stop for the day and start the next session
by narrowing the infrastructure to its minimum (abandon by id and the complete token, without the retention and
receipt-attribution machinery), then give B and C a final round on top.

## Where the work is (not merged; kept for the next session)
- Infra: `C:\Users\guill\dzmcp_gauntlet\r2i_broker\ws`, reviews `r1..r3\SOL.md`.
- B: `C:\Users\guill\dzmcp_gauntlet\r2b_9941\ws`, reviews `r1..r3\SOL.md`.
- C: `C:\Users\guill\dzmcp_gauntlet\r2c_86a3\ws`, reviews `r1..r4\SOL.md`.
- 2986: `C:\Users\guill\dzmcp_gauntlet\r2a_855c_2986\ws` (orchestrator round: `r5_orchestrator.diff`, `SOL_r5.md`).

Grok spend for the round, summed from each batch's `PROGRESS.txt` (`cost` of every implementer run): A 6.12 USD
(4 runs), B 7.27 USD (3), C 8.03 USD (4), infrastructure 7.90 USD (3); total 29.32 USD. Triage round 3's A and B
(#223, #224) cost 0.37 and 0.12 USD.
