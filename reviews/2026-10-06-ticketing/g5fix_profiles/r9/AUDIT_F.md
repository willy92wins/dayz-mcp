# AUDIT — g5fix named-instance profile folders: close, logout wait, concurrent runs

Method: every load-bearing claim of the diff was re-derived from the source and attacked with
executable probes (project venv `tools/.venv-mcp/Scripts/python.exe`, in-memory / temp-dir only;
scripts under `_scratch/`, removed). Probe outputs quoted below are from those runs.

## FINDINGS

### F1 — P3 — anchor-refused close collapses three distinct causes and hides connected players
`tools/dayz_mcp/dayz_test_tool.py:4228-4252`

```python
    if (
        policy is None
        or start_roots is None
        or not _profiles_match_policy(policy, run)
    ):
        missing_rpt.extend(process_roles)
```
and `tools/dayz_mcp/dayz_test_tool.py:4259-4269`

```python
        server_watch = watches.get("server")
        server_rpt = None if server_watch is None else Path(server_watch.path)
        boundary = _logout_boundary(server_rpt)
        client_close = await _checked_role_close(close_roles, run_id, ["client"])
        client_row = _close_role_row(client_close, "client")
        players, logout_wait_s, timed_out = await _wait_for_connected_logouts(
            server_rpt,
            timeout_s,
            boundary,
            wait=client_row is None or _windows_posted(client_close, "client") > 0,
        )
```
Reasoning. When the recorded anchor is refused (sealed policy unresolved, leaf/token/project
mismatch, folder unlistable, or no RPT yet), every role enters `missing_rpt`, no watch is created,
`server_rpt` is `None` and `_wait_for_connected_logouts` returns `([], 0.0, False)`
(`dayz_test_tool.py:3936-3937`), so the close posts windows to the client and then kills the server
without observing any linked logout. The envelope is honest (`graceful=false`,
`reason="role_without_rpt"`, `exit_metrics_valid=false` — verified end-to-end, see S2), so this is
not the old silent-success bug; the defect is narrower: `policy is None` (project identity
unresolvable), a rejected anchor, and a plain empty RPT folder all publish the same
`role_without_rpt`, and `logout_players` is `[]` even when the server RPT named a connected player
seconds earlier. An operator triaging a data-loss report cannot distinguish "anchor refused" from
"no RPT written", and gets no player-count evidence from the close itself.
Failure scenario (executed, `p7/p8`): bound `130`, run row recorded with legacy `profiles`, server
RPT containing `Player "Bob" (...) is connected` + uid line; `execute_dayz_test_close` returns
`{'graceful': False, 'reason': 'role_without_rpt', 'exit_metrics_valid': False, 'run_retired': True,
'stop_required': False}`, `logout: [] 0.0`, close calls `('client',), ('server',)` — server closed,
logout never observed, no `player_state_not_saved` warning (warnings are only attached on the
graceful branch, `dayz_test_tool.py:4462-4463`).

### F2 — P3 — log readers gained a runtime dependency on launcher-registry availability
`tools/dayz_mcp/launch_logs.py:136-153`

```python
    policy = _close_project_policy(run)
    if policy is None:
        return False
    return _validated_recorded_leaf(policy, run) is not None
```
with `tools/dayz_mcp/dayz_test_tool.py:3341-3347`

```python
    try:
        with open_approved_launcher("dayz-test-v1") as opened:
            opened.validate_native_pe()
            with secure_launcher.load_verified_bundle(opened) as bundle:
                policies = _semantic_policies(bundle.sealed_policies)
    except Exception:
        return None
```
Reasoning. Before this change `logs_since` / `wait_for(log_matches)` / the capture-window resolver
admitted run folders with pure path checks (`log_tail.is_allowed_profiles_dir`). Now every call —
and every `wait_for` poll (`server.py:3608`, inside the tool lock) — re-opens the approved launcher,
re-validates its PE and re-verifies the bundle closure; `_close_project_policy` swallows every
exception into `None`, so a transient registry/bundle read failure demotes a healthy live run to
`bad_profiles` (`server.py:5402-5404`) or `no_active_run` (`server.py:2408-2410`) where the
pre-change code read the logs. Measured warm cost is small (`p4`: ~0.8-0.9 ms per resolve after
first call) so this is resilience, not latency; the failure mode is availability, not wrong data.
Failure scenario (executed, `p5`): with the launcher registry unreadable in the probe environment,
`_close_project_policy` returned `None` (`ValueError: invalid_launcher_registry` raised underneath)
=> `_profile_dirs_from_runs` returns `[]` => `logs_since` answers `bad_profiles` for a live, valid
run until the registry reads again.

No P0/P1/P2 found. Every "safe because X" below survived its attack.

## SCENARIOS

Anchor validator (`_validated_recorded_leaf`, `dayz_test_tool.py:811-846`), probe `p1`, bound
`130`, policy dev\_root `C:\proj`: accepts `_server|_client\profiles-130` in any case, forward
slashes, trailing junk separators; rejects legacy `profiles` under a bound named instance, other
token `profiles-131`, other project root, drive-less, `..` (also the `..`-erased normpath form),
subpaths, non-str, and unresolvable `mod` is rejected one layer up (`_close_project_policy`,
`len(matches)!=1`). Unbound process admits exactly `profiles` — default parity, incl.
`_stop_artifacts` legacy expansion (probe `p2`: server-anchored client-retired run still expands to
the full all-set with the named leaf; `dayz_test_tool.py:1726-1728`).

S1 Happy named run, all roles. `p8`: watches created in `_server\profiles-130` /
`_client\profiles-130`; boundary from the named server RPT; client close appends
`[Logout]: Player <uid> finished`; linked logout completes (`logout_finished: True`, wait 5 ms);
termination lines on both named RPTs + `run_reaped` => `graceful True, exit_metrics_valid True,
stop_required False`. Rotation injected before reaping => `graceful False, reason rpt_rotated`
(rotation still invalidates exit metrics; watch follows `watch.path.parent`, `:4328-4339`).

S2 Anchor refused (see F1): close proceeds client-then-server, run retired, honest non-graceful
envelope; logout wait skipped by construction (`_wait_for_connected_logouts` `None` short-circuit).
Not silent: `role_without_rpt` + non-graceful are published on the wire
(`_whitelist_close_result` keys).

S3 Daemon killed between launch and close, then restarted. State roots are per token
(`runtime_state.py:211-227`, `server_cli.state_root_name`); the run row and its absolute
`profiles` string survive in `runs.json` (`process_lifecycle.py:4046-4064`, intent recheck
`:4500-4510` requires absolute); the restarted daemon rebinds the same token before serving
(`daemon.py:1553-1555`) and takes the per-root writer lease (`:1560-1564`), so the recovered row
revalidates against the same leaf and S1 applies. A restart under a different token cannot see the
row at all (different root) — no cross-instance close.

S4 Operator deletes/renames `profiles-<token>` mid-run. Close: `_list_rpt_files` OSError => None =>
`missing_rpt` => S2 envelope; the kill still happens and is reported non-graceful. `logs_since`:
`_current_launch_logs` finds nothing => empty files, marker unchanged. `stop`:
`_stop_artifacts` is pure string math on the recorded anchor (`:1699-1710`) => unaffected; the
subsequent role close carries the real state. `file_matches`: folder missing => unreadable
evidence, no cross-folder fallback (siblings only via `_sibling_profile_dirs` with the same leaf
and `is_dir()`).

S5 Two instances, one host. Same dev root, tokens 130/131: separate state roots
(`DayZ_MCP` / `DayZ_MCP_130`...), separate `runs.json`, separate registrations; each serving
process binds its own token (daemon `:1553`, server parse\_args `server.py:8714-8716`, doctor
`:1463`) and `is_allowed_profiles_dir` / `_validated_recorded_leaf` pin leaf+full path to that
token and to the sealed dev\_root — a 130 reader cannot resolve, wait on, or report a 131 path
(probe `p2`: bound 131 => `_close_role_folder` None => `profile_unresolved`; stop =>
`lifecycle_status_invalid`). Same token, different project: the anchor must equal
`normcase(normpath(policy.dev_root))...`; a row of the other project is dropped from logs
(`bad_profiles`) and refused by close/stop — same envelopes the pre-change code produced for those
rows (old stop also failed `lifecycle_status_invalid` via `len(matches)!=1`); concurrent
same-token daemons are excluded by the root writer lease. Capture disambiguation only receives
validated dirs (`server.py:7559`) => no wrong-window cross-instance pick via cmdline\_match.

S6 F3 cross-role read: server-anchored named run, `role="client"` resolves
`_client\profiles-130` through the validated anchor before role branching
(`_close_role_folder` `:3400-3409`; probe `p2`), legacy `_client\profiles` is not substituted; the
removed same-parent comparison (`server.py:2741-2745`) is subsumed by the full-anchor check.

## NOT VERIFIED

- Full repo test suite and the four repaired fixture modules executed here (anchors verified
  present by reading: `test_steam_not_running_preflight.py:152`, `test_db05_preflight_diagnostics.py:26`,
  `test_dayz_test_tool_modes.py:476`, `test_client_lifecycle_7055_9336_9efc.py:199` + `:53`);
  21 named-instance tests exist incl. logout/rotation ones.
- Junction/symlink-escape rejection in `_provision_named_profile_leaf` ran as code-read only
  (symlink privilege absent in probe); all other guards executed green (`p6`: create, missing role
  root, wrong root name, role/root mismatch, file at leaf, no-creds refusal before create, bool
  port, no ancestor creation, existing-dir reuse).
- Live daemon kill/restart, real DayZ close, resealed-launcher deployment, cold-cache launcher
  verification latency, and in-game logout evidence remain with the orchestrator (per SOL\_R3).
- `F2` warm-cost claim measured on one host only; no contention measured.
