# Codex execution notes — readiness reason instrumentation

## 2026-07-26 post-registry H11 amendment

### Problem

The approved rollout ran H11 before the registry transition, so all 130 tests
passed while `approved-launchers.json` still named the previous PE. An
independent post-release rerun exposed two stale canonical expectations:

- `P:\DayZ_MCP_dev\tools\tests\test_secure_launcher.py:56`
- `P:\DayZ_MCP_dev\tools\tests\test_task9_launcher_migration.py:317`

Both still require the previous PE SHA-256
`FE1ED970E3589B2A2A67CDB65C1EF68E9C79965BB84965D6B6130A52BCB45DE7`.
The canonical registry and verified bundle now require
`17B43C6726CBDAAA866F08E0873E979CD96DD81B27EC084C2B94D581EF9825EF`.
The post-registry result is therefore 128/130, with only those two exact
assertions failing.

### Impact

This is validation-fixture drift, not a launcher or registry failure. Leaving
it unresolved would make the canonical H11 suite red and would violate the
verification-before-runtime gate for the LFPowerGrid diagnostic.

### Approved-scope amendment

The user authorized autonomous, safety-first continuation of the active goal.
The smallest architecture-neutral correction is to align only the two exact
test expectations with the already committed and independently verified
registry/PE. No production code, generated bundle, registry, timeout, schema,
ownership rule, DayZ lifecycle, or LFPowerGrid artifact changes.

[EXACT]

```text
FE1ED970E3589B2A2A67CDB65C1EF68E9C79965BB84965D6B6130A52BCB45DE7
→
17B43C6726CBDAAA866F08E0873E979CD96DD81B27EC084C2B94D581EF9825EF
```

Apply the replacement once in each cited file, under a fresh DayZ MCP lease
after exact preimage verification.

### Backup and rollback

Durable preimages are preserved without overwrite under:

`C:\tmp\dayz-mcp-readiness-diagnostics-20260726T220925Z\canonical-post-registry-test-fixture-backup`

- `test_secure_launcher.py`:
  `2E35E421DF85621863A05EEBDD06D5807BF5A7B404691D2C4AB13E913FCC3667`
- `test_task9_launcher_migration.py`:
  `63AF5B0DAC10CA08BD965BBFF94FC08C5274DB78D6384B9D40E4F02288DEF6B3`

If either postimage, registry/PE identity, or validation gate fails while the
lease remains authoritative, restore both exact preimages and verify both
preimage hashes before releasing.

### Exit gates

1. Registry SHA remains
   `960EF6AE3AFE9DE13F01D0E327CE890CB1C8D1C1B4CAE0F69DD48819299CE8CF`.
2. Registered/canonical PE SHA remains
   `17B43C6726CBDAAA866F08E0873E979CD96DD81B27EC084C2B94D581EF9825EF`.
3. The old PE SHA has zero matches in the two canonical test files; the new SHA
   has exactly one match in each.
4. Focused two-test rerun is 2/2 PASS.
5. Full canonical H11 is 130/130 PASS after the registry transition.
6. Independent diff review confirms exactly two one-line expectation changes.
7. Lease release and final `session_status` are clean.
