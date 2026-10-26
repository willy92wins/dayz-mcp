# A DayZ crash leaves CrashReporter.exe holding the box's ports (inbox 7417)

Base: main `2e844c5`; revision 2 is on main `1f9eba7`. Python only: no sealed module, no PBO, no bridge change.

## What happened (fb-20261026-153217-7417, run `8c9b224d`, stable box)

Read on the host, read-only, 2026-10-26 15:40Z (audit, manifest, profiles, `Win32_Process`, `Get-NetUDPEndpoint`):

1. 15:30:23.888Z the server ran `object_delete` (id 117); in the same millisecond LFQuad logged its driver exit. Half a second later both DayZDiag peers crashed (`Access violation`, same first six frames, one `.mdmp` in each profile).
2. Each crashing peer started `C:\Program Files (x86)\Steam\steamapps\common\DayZ\CrashReporter.exe --input @stdin` (client 15:30:24.770Z, server .893Z). The child inherited the peer's UDP sockets. `Get-NetUDPEndpoint` keeps the creator's pid: 2302 and 2304 under the dead server pid 57116, 2316 under the dead client pid 53904. Both reporters were still alive ten minutes later.
3. 15:30:27.916Z `lifecycle_stop`: owned [57116], gone [53904]. `guard.terminate(57116)` answered `process_not_found`: the server finished dying between the classification and the kill. `stop_run` took that as a failed kill (`partial_cleanup`, `UNRECONCILED`, terminated 0), and the worker answered `run_stop_failed` with `cleanup_degraded`.
4. 15:30:34.593Z the reaper retired the run (`all_processes_gone_or_foreign`), `processes: []`.
5. The box stays occupied: the three holders carry dead pids with no image name, so `_foreign_port_reason` answers `port_attribution_unknown`, and no verb can clear it. A second `dayz_test_stop` answered `run_not_active`.

Two defects: the stop does not tolerate a process that exits by itself between the classification and the kill (C1), and nothing knows about the reporter children, so the run is retired while they hold its ports and no stop can reach them (C2, C3).

## Changes

### C1. A kill that finds the process gone is a gone process

`stop_run`, path without the idle warden (`stop_guard is None`): when `guard.terminate(record)` answers `process_not_found` (exit code 4) for a record classified owned, in the first loop or in the second (C2), the record counts as gone. It is removed from `survivors`, `terminated` does not grow, and the loop goes on. With every record gone or terminated the stop ends `EXITED` as today, with `stop_method` `no_live_owned` when nothing was killed (the worker's source maps that to success, `dayz_test_worker._run_already_gone`). Every other refusal keeps today's `partial_cleanup`. The warden path is unchanged.

### C2. Crash reporter children become records of their run

- `orphan_guard`:
  - the ToolHelp snapshot also keeps `th32ParentProcessID`. New `snapshot_processes_with_parent(names) -> {"known": bool, "processes": [{"pid", "ppid", "name"}]}`, exact case-insensitive names like `snapshot_processes_by_name`, whose output does not change;
  - new `open_process_anchor(pid) -> object | None`: on Windows `OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)`, the handle or None when it cannot be opened; None off Windows. New `close_process_anchor(token)`: `CloseHandle` on Windows, nothing off Windows; it never raises.
- `daemon.py` wires `child_probe=lambda: orphan_guard.snapshot_processes_with_parent(["CrashReporter.exe"])` and `parent_anchor=(orphan_guard.open_process_anchor, orphan_guard.close_process_anchor)` into `ProcessLifecycle`. Both parameters default to None: a lifecycle without `child_probe` registers nothing, like `port_probe`, and one without `parent_anchor` holds no anchor.
- **Parent anchors** (revision 2, H1). The lifecycle keeps `_parent_anchors`, a dict from `(run_id, pid, creation_time_utc)` of a `server`, `client` or `offline` record to an open handle on that process. Windows does not give a pid to another process while a handle to the old one is open, so while a record is anchored its pid names that process or nothing, never a later one. `_anchor_parents_locked(run)` (caller holds `_operation_lock`; it never raises): for each record of those roles with no anchor, `token = open(record.pid)`; None, an exception or no `parent_anchor` leaves it unanchored; then `guard.snapshot(record.pid)` must match the record (`_identity_matches`), or the token is closed and the record stays unanchored. It runs:
  - in `start_run`, after the manifest write that makes the run `RUNNING` with the new record (a relaunched client included);
  - in `_recover_launch_intent_locked`, after the write that adds the recovered record;
  - at the start of `_register_crash_reporters_locked`, so a daemon that restarted anchors the parents still alive at its first scan.
  `_release_parent_anchors_locked()` closes and forgets every anchor whose run is gone from the manifest or `EXITED`, or no longer has that record. It runs at the start of `_register_crash_reporters_locked` and in `_commit_retirement` after the write.
- `ProcessLifecycle._register_crash_reporters_locked(run) -> str | None` (caller holds `_operation_lock`; None, or why the run may not be stopped or reaped yet). A probe row becomes a record of the run only when all of these hold, and a row that fails one is neither registered nor killed:
  1. its name casefolds to `crashreporter.exe`;
  2. its `ppid` is the pid of a record of the run whose role is `server`, `client` or `offline` (a `crash_reporter` record is never a parent);
  3. that parent is the record's process, alive or proven: `guard.snapshot(ppid)` matches the record, or it answers `process_not_found` (exit code 4) and the record is anchored. A complete identity that does not match (the pid was reused) skips the row. A gone parent with no anchor skips the row, writes an audit row `crash_reporter_parent_unverified` (run, pid, ppid) and makes the call's reason `crash_reporter_parent_unverified`: without the anchor the pid may have been reused by another process that started this reporter and died too, so the row is never killed, and the run is not stopped or reaped while it is there;
  4. `guard.snapshot(pid)` of the row is a complete identity whose `executable_sha256` equals `identity_hashes(str(self.game_path / "CrashReporter.exe"), [...])["executable_sha256"]`, the reporter of the install this lifecycle launches (a hash of the normalized path, as every identity here, not of the file). A row that passed 1 to 3 and whose snapshot raises, is not a dict, or is not complete without answering `process_not_found` makes the call's reason `child_scan_unknown`; one that answers `process_not_found` has exited and is skipped;
  5. its `creation_time_utc` is not earlier than the parent record's (both parsed, not compared as text);
  6. the run has no record with the same pid and creation time yet.
  The record is `ProcessRecord(pid, creation_time_utc, executable_sha256, command_line_sha256, role="crash_reporter", identity_scheme)` from that snapshot. The rows that pass are written in one manifest write per call, with an audit row `crash_reporter_registered` (run, pid, ppid) for each, also when another row makes the reason unknown. A probe that raises or answers `known` false is the reason `child_scan_unknown`. A manifest write that fails drops the new records and is the reason `manifest_failed`.
- Where it runs:
  - `stop_run`, before the classification; a reason refuses the stop with that reason, as today;
  - `stop_run` without the idle warden, again after the first terminate loop, so a reporter born during the stop is killed in the same call. A reason from this second scan no longer retires the run (revision 2, H7): the stop answers `partial_cleanup` with `reason` set to it, the run goes `UNRECONCILED` and keeps its records as below. In the second loop a record whose classification is unknown does the same, with the classification's reason; a gone or foreign one is skipped. The loops remember what they handled by pid and creation time, not by pid alone (H8);
  - the reaper (`_reap_dead_runs_locked`) and `reap_dead_run`, before `_run_all_dead`. A registered reporter is owned, so the run is not all dead and is not retired while it lives. A reason does not reap, as an unknown diag snapshot does not today.
- **What a partial cleanup keeps** (revision 2, H7). On `partial_cleanup` or `partial_stop`, `run.processes` keeps, as today, the records this stop did not kill or find gone. When one of them is a `crash_reporter` record, or the partial cleanup comes from a reason of the second scan, the run also keeps its `server`, `client` and `offline` records, dead or alive, in the run's order: their pids (anchored, so not reused) stay attributed by `_foreign_port_reason`, and a later scan can still name their children. A run with no reporter keeps only those records, as `test_partial_stop_persists_only_survivors` pins.
- With the reporter registered the existing state machine is the remedy: registering does not change the run's state (`RUNNING` stays `RUNNING`, `UNRECONCILED` stays `UNRECONCILED`), `adopt` finds an owned process, and `dayz_test_stop` kills the reporters through `guard.terminate` with their identity. While a reporter record stays, the dead parent's record stays with it, so `_foreign_port_reason` attributes the three ports to the run instead of `port_attribution_unknown`.

### C3. Close skips the reporters

- `close_run` and `close_run_roles` leave `crash_reporter` records alone: they have no game window to close, and the stop kills them. The idle warden's close (`_close_with_idle_guard`) lists and posts to no `crash_reporter` window either (revision 2, H4); its authorization still checks the whole set.
- `dayz_test_close` (`dayz_test_tool._launched_roles`) leaves `crash_reporter` out of the roles it closes and of the RPT files it waits for (revision 2, H2), so a server and client run with a reporter still closes the client, waits for the logouts and then closes the server, and no reporter is `missing_rpt`. With a live reporter the run is not retired after the close; `dayz_test_stop` kills it.
- `status` lists the reporters with their role and identity. The box rows count the run; they do not list its processes.

### C4. doctor knows the reporters (revision 2, H5)

`doctor` also snapshots `CrashReporter.exe` (scope `crash_reporter`). A `crash_reporter` record whose pid is in that snapshot gets the identity check every record gets, instead of `RUN_STALE`; one whose pid is not there is `RUN_STALE`, as for the other roles. With that snapshot failed, `_safe_snapshot` reports `PROCESS_SCAN_FAILED` with scope `crash_reporter` and the `crash_reporter` records get no finding of their own. A `CrashReporter.exe` that is no record is not reported: the snapshot only serves the records.

## Out of scope

- Launching DayZ so that its children do not inherit handles: the sockets are DayZ's and the child is started by DayZ itself.
- A run already retired before this change (the incident's): its reporters have no record. They end on their own or are closed by hand.
- A parent that died with no anchor (the daemon restarted after the launch and the game crashed before the next scan, or `OpenProcess` failed): its reporter is `crash_reporter_parent_unverified`, closed by hand.
- The idle warden's stop does not scan after its kills: it kills with `TerminateProcess`, which does not run DayZ's crash handler, so a reporter can only appear there if the game crashes by itself during those effects. Such a reporter is not seen by that stop.
- Waiting for the ports after the stop: with the reporters registered the run is not closed while they hold them.

## Tests (fakes only)

No test starts, kills or snapshots a real process, opens a real socket table or calls a real Windows API: fake guard, fake probes, fake anchors, fake ToolHelp entries. They use temporary files and a real manifest store.

Baseline: on d041495 (main plus this plan) every test of the new module errors, because `ProcessLifecycle` has no `child_probe` and `orphan_guard` no `snapshot_processes_with_parent`. The behavior tests are therefore also measured with the round-1 code and one mutant each (below), and the round-1 module was measured on d041495 with only `child_probe` removed from the fixture's constructor.

- T1 (C1): one owned server record; the fake guard's `snapshot` says owned and its `terminate` answers `process_not_found`. `stop_run` answers ok, `EXITED`, terminated 0, `stop_method` `no_live_owned`; the manifest row is `EXITED` with no processes. Negative: `terminate` answering `termination_unconfirmed` still gives `partial_cleanup` and `UNRECONCILED`.
- T2 (C2, the incident): server and client records alive, a reaper pass anchors both; then both gone and the probe lists reporters 40784 (ppid = server) and 51036 (ppid = client) with complete identities, the install's reporter hash and creation times after their parents. The reaper does not retire the run and the run gains two `crash_reporter` records (one manifest write, two audit rows); `adopt` with a lease succeeds; `stop_run` calls `guard.terminate` with exactly those two records and ends `EXITED` with terminated 2; the anchors are closed after the retirement and not before.
- T2b (C2): the manifest write of the new reporter record fails once: the reaper does not retire the run and the stored run keeps only its server record.
- T3 (C2 negatives): the server record is anchored first (an alive reaper pass), so each row fails only its own gate. Each of these rows is not registered, and no `terminate` is called for it: another name; a ppid that is no record's pid; a parent pid alive with another identity; a reporter with another executable hash (another install); a creation time before the parent's. A probe that raises, and one answering `known` false: the reaper does not retire the run, and `stop_run` reports `child_scan_unknown`.
- T4 (C2 during the stop): the reporter appears only on the second scan of `stop_run`; it is registered and terminated in the same call. When its kill fails, the stop is `partial_cleanup` and the run keeps the reporter and the server record.
- T5 (C3): `close_run` with server, client and `crash_reporter` records posts to the two game windows only.
- T6 (`orphan_guard`): `snapshot_processes_with_parent` maps fake ToolHelp entries to pid, ppid and name with exact case-insensitive names; a failed snapshot is `known` false; `snapshot_processes_by_name` answers as before.
- T7 (H1, anchors):
  - a gone parent never anchored: its reporter row is not registered and not killed, the audit row `crash_reporter_parent_unverified` names it, the reaper does not retire the run and `stop_run` answers `crash_reporter_parent_unverified`;
  - `open` answering None, and a snapshot after `open` that no longer matches the record: no anchor (the token of the second is closed), and the gone parent's reporter is refused as above;
  - `start_run` with the fake launcher anchors the launched record, and the run's retirement closes that anchor;
  - a record still alive and matching is a valid parent without an anchor.
- T8 (H2): `execute_dayz_test_close` on a run whose status lists server, client and `crash_reporter` processes closes the client, waits for the logouts, then closes the server, and `crash_reporter` is in neither the close rows nor `missing_rpt`.
- T9 (H3): a row whose ppid is a live, registered `crash_reporter` record, and which passes every other gate, is not registered and not killed.
- T10 (H4): the idle warden's close of a run with server and `crash_reporter` records posts to the server's window only.
- T11 (H5): doctor with a live registered reporter in the `CrashReporter.exe` snapshot gives no `RUN_STALE` for its pid; with the reporter missing from it, `RUN_STALE`; with that snapshot failed, `PROCESS_SCAN_FAILED` scope `crash_reporter` and no `RUN_STALE` for its pid; an unregistered `CrashReporter.exe` gives no finding.
- T12 (H7):
  - a row that passes gates 1 to 3 with an incomplete identity: not registered, the reaper does not retire the run, `stop_run` answers `child_scan_unknown`;
  - the second scan raising, and its manifest write failing: `partial_cleanup` with that reason, the run `UNRECONCILED` with its records;
  - a reporter registered by the second scan whose classification is unknown in the second loop: `partial_cleanup`, the reporter stays on the run;
  - two reporters, the first unknown in the second loop and the second's kill failing: both stay, with the server and client records.
- T13 (H8): the server 57116 is killed in the first loop, and the second scan registers a reporter with pid 57116 and another creation time, child of the anchored client: the second loop kills it and the stop ends `EXITED` with terminated 2.
- T14 (H9): the reporter of the second scan answers `process_not_found` to its kill: `EXITED`, terminated 1 (the server), `stop_method` `forced_kill`.

Mutants, one at a time on a copy, each killed by the test named: M1 a gone parent accepted without an anchor (T7); M2 no snapshot check after `open` (T7); M3 no anchor in `start_run` (T7); M4 `crash_reporter` accepted as a parent role (T9); M5 `_launched_roles` keeping `crash_reporter` (T8); M6 the warden's close listing reporters (T10); M7 doctor without the reporter snapshot (T11); M8 an unknown candidate identity skipped (T12); M9 a second-scan reason retiring the run (T12); M10 an unknown classification skipped in the second loop (T12); M11 the parents dropped while a reporter stays (T4, T12); M12 handled by pid only (T13); M13 the second loop without its `process_not_found` acceptance (T14).

Plus the suites that cover the lifecycle (`test_run_reaper`, `test_bug104_reap_under_quarantine`, the stop, adopt and close tests, the doctor and `dayz_test_close` tests) and the whole `tools/tests` tree give the same result as on main.

## Docs

- CHANGELOG, Fixed: what C1 to C4 change for a caller, with the inbox id.
- `tools/README-mcp.md`, Troubleshooting: a DayZ crash can leave `CrashReporter.exe` holding the run's ports; `dayz_test_stop` on that run closes it. A run retired before this release, and a reporter answered `crash_reporter_parent_unverified`, are closed by hand.
