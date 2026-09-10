# Validation and deployment boundary

Base `1c4c0eed5cadf13b994d09f3217dd04bb9002bc8`, branch
`fix/steam-launch-admission`. Isolated implementation and base worktrees.

## Results

| Check | Result |
|---|---|
| Final directed implementation tests | 567 PASS, no skips |
| Final sealed launcher/CLI tests | 81 PASS, no skips |
| Broad suite before final busy/pending diagnostic refinement | 3351 tests; 33 skips; 11 failures + 1 error |
| Focused clean-base comparison | 217 tests; same 12 failure events, plus 2 missing-bundle checks |
| Base missing-bundle checks after building the base | 2 PASS |
| Candidate-only failures in the broad suite | None: every remaining failure ID also occurs on the clean base |
| Sealed candidate | clean-1/clean-2/offline reproducible; rebuilt after final code changes |
| Opus | Main review SOUND-with-fixes; P2/P3 fixes verified; final residual check SOUND |
| Real Steam/DayZ/VPP | Not run; candidate not deployed |

The broad suite is **not green**. Its remaining 12 failure events require files
absent from the Git base: task9's protocol documents and project CLAUDE/AGENTS,
and one H8 test's local `tools/.dayz_mcp.key`. No live key was copied and no fixture
was invented to make them pass. The initial missing editable-install errors,
old public-remediator mocks, port-probe timing fixture and static security audit
failure are resolved; the verbose final suite contains their passing results.

The 648 final directed tests ran after the last diagnostic changes. Coverage
includes strict/legacy consent, public/sealed parity, recent/old/restarted Steam,
PID reuse, cancelled/expired reservations, post-wait admission, no early run/client
mutation, final spawn identity, hung helpers, hung OS probes, persistent fences,
transport retry limits, and preserving cleanup degradation. Harmless Python
subprocesses exercise Windows helper termination; tests never repair live Steam.

## Reproduction

Use an isolated checkout with a repo-local virtualenv, install
`tools/requirements-mcp.txt`, then `pip install --no-deps -e tools` into that venv.
Run from `tools` with `.venv-mcp/Scripts/python.exe`:

```text
-m unittest -v tests.test_steam_launch_guard tests.test_dayz_test_worker tests.test_dayz_test_request tests.test_dayz_test_tool tests.test_process_lifecycle tests.test_session_coordination tests.test_lifecycle_reconcile tests.test_security_runtime_audit tests.test_db05_preflight_diagnostics
-m unittest -v tests.test_native_launcher_bundle tests.test_native_launcher_transaction tests.test_build_native_launcher_policy tests.test_dayz_test_app tests.test_lifecycle_cli tests.test_admin_cli
-m unittest discover -s tests -v
```

Build from the isolated repository root after verifying the output and its
`.previous` sibling remain inside that checkout:

```text
tools/.venv-mcp/Scripts/python.exe tools/build_native_launcher.py --output tools/native-launchers/dayz-test-v1 --offline --verify-reproducible
```

Final candidate hashes:

- app.pyz: `9FEBF31861D7A75825446BE7AFB6E3CF1118FF9CD32072AEE314FFFF6EEC1FAE`
- manifest: `62D5D01FD0ED02678B0E045AC9F5ED9B4627F78B5ACE6B663CFEF029458482FF`
- PE: `475691AEFBD570234CF1D4DBE63D521995C43F8FEB0FB24536B1FB39C02EC8C7`

Local raw evidence (kept outside the PR's tracked payload): `final-targeted.txt`,
`final-launcher-tests.txt`, `suite-final.txt`, `base-comparison.txt`,
`base-bundle-recheck.txt`, `comparison.json`, `build-final.txt`. The broad-suite
invocation header was recorded after completion from the actual execution record.
The full comparison matched failure IDs; the base's two additional failures were
missing a sealed bundle and passed when the base was built. Source hashes are in
`source-hashes.json`; review findings and dispositions are in adjacent Markdown.

## Limits and activation

Review time used: Opus 526.48 s + 338.81 s + 21.21 s, within the 17-minute pool.
The final short review verified only the residual diagnostic adjustments; Codex
closed the evidence comparison independently after those logs completed.

The installed launcher, running daemon, Steam registry and DayZ processes were
not changed. Activation requires coordinating the daemon/launcher update and
reconnecting old MCP stdio clients, then exercising real rapid close/relaunch with
CF, Dabs, VPP and LFTurret. A startup marker is not a Steamworks guarantee.

A permanently hung **read-only** OS probe retains one bounded slot; subsequent
mutation requests return `steam_probe_pending`. Readiness with already healthy
Steam still works. Wait for that probe or coordinate a daemon restart to restore
mutations. An unverified writer/helper exit retains the separate Steam fence.
Newly restarted Steam cannot use the old-stable fallback if its marker has already
rotated beyond the bounded log tail; that remains a conservative timeout.
