# A DayZ crash leaves CrashReporter.exe holding the box's ports (inbox 7417)

Base: main `2e844c5`. Python only: no sealed module, no PBO, no bridge change.

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

`stop_run`, path without the idle warden (`stop_guard is None`): when `guard.terminate(record)` answers `process_not_found` (exit code 4) for a record classified owned, the record counts as gone. It is removed from `survivors`, `terminated` does not grow, and the loop goes on. With every record gone or terminated the stop ends `EXITED` as today, with `stop_method` `no_live_owned` when nothing was killed (the sealed worker already maps that to success, `dayz_test_worker._run_already_gone`). Every other refusal keeps today's `partial_cleanup`. The warden path is unchanged.

### C2. Crash reporter children become records of their run

- `orphan_guard`: the ToolHelp snapshot also keeps `th32ParentProcessID`. New `snapshot_processes_with_parent(names) -> {"known": bool, "processes": [{"pid", "ppid", "name"}]}`, exact case-insensitive names like `snapshot_processes_by_name`, whose output does not change.
- `daemon.py` wires it into `ProcessLifecycle(child_probe=lambda: orphan_guard.snapshot_processes_with_parent(["CrashReporter.exe"]))`. The parameter defaults to None: a lifecycle without it registers nothing, like `port_probe`.
- `ProcessLifecycle._register_crash_reporters_locked(run) -> str | None` (caller holds `_operation_lock`; returns None or the reason the scan is unknown). A probe row becomes a record of the run only when all of these hold, and a row that fails one is neither registered nor killed:
  1. its name casefolds to `crashreporter.exe`;
  2. its `ppid` is the pid of a record of the run whose role is `server`, `client` or `offline`;
  3. that parent is the record's process or gone: `guard.snapshot(ppid)` matches the record, or answers `process_not_found`. A complete identity that does not match (the pid was reused) or an unknown one skips the row;
  4. `guard.snapshot(pid)` of the row is a complete identity whose `executable_sha256` equals `identity_hashes(str(self.game_path / "CrashReporter.exe"), [...])["executable_sha256"]`, the reporter of the install this lifecycle launches;
  5. its `creation_time_utc` is not earlier than the parent record's (both parsed, not compared as text);
  6. the run has no record with the same pid and creation time yet.
  The record is `ProcessRecord(pid, creation_time_utc, executable_sha256, command_line_sha256, role="crash_reporter", identity_scheme)` from that snapshot. The manifest is written once per call that adds a record, and an audit row `crash_reporter_registered` names the run, pid and ppid. A probe that raises or answers `known` false is the reason `child_scan_unknown`. A manifest write that fails drops the new records and is the reason `manifest_failed`, so the reaper does not retire the run on the strength of records it could not keep.
- Where it runs:
  - `stop_run`, before the classification and, on the path without the idle warden, again after the terminate loop, so a reporter born during the stop is killed in the same call (a failed second scan only adds its reason to `cleanup_degraded`). The warden authorized a fixed set of processes, so its path does not kill a reporter registered after its last check;
  - the reaper (`_reap_dead_runs_locked`) and `reap_dead_run`, before `_run_all_dead`. A registered reporter is owned, so the run is not all dead and is not retired while it lives. An unknown scan does not reap, as an unknown diag snapshot does not today.
- With the reporter registered the existing state machine is the remedy: the run stays `UNRECONCILED`, `adopt` finds an owned process, and `dayz_test_stop` kills the reporters through `guard.terminate` with their identity. The dead parent's record stays in the run until then, so `_foreign_port_reason` attributes the three ports to the run instead of `port_attribution_unknown`.

### C3. Close skips the reporters

`close_run` and `close_run_roles` leave `crash_reporter` records alone: they have no game window to close, and the stop kills them. Status and box rows list them with their role.

## Out of scope

- Launching DayZ so that its children do not inherit handles: the sockets are DayZ's and the child is started by DayZ itself.
- A run already retired before this change (the incident's): its reporters have no record. They end on their own or are closed by hand.
- Waiting for the ports after the stop: with the reporters registered the run is not closed while they hold them.

## Tests (each new one fails on main `2e844c5` and passes with the change; fakes only)

No test starts, kills or snapshots a real process, opens a real socket table or calls a real Windows API: fake guard, fake probes, fake ToolHelp entries.

- T1 (C1): one owned server record; the fake guard's `snapshot` says owned and its `terminate` answers `process_not_found`. `stop_run` answers ok, `EXITED`, terminated 0, `stop_method` `no_live_owned`; the manifest row is `EXITED` with no processes. Negative: `terminate` answering `termination_unconfirmed` still gives `partial_cleanup` and `UNRECONCILED`.
- T2 (C2, the incident): server and client records gone; the probe lists reporters 40784 (ppid = server) and 51036 (ppid = client) with complete identities, the install's reporter hash and creation times after their parents. The reaper does not retire the run and the run gains two `crash_reporter` records (one manifest write, two audit rows); `adopt` with a lease succeeds; `stop_run` calls `guard.terminate` with exactly those two records and ends `EXITED` with terminated 2.
- T2b (C2): the manifest write of the new reporter record fails once: the reaper does not retire the run and the stored run keeps only its server record.
- T3 (C2 negatives): each of these rows is not registered, and no `terminate` is called for it: another name; a ppid that is no record's pid; a parent pid alive with another identity; a reporter with another executable hash (another install); a creation time before the parent's; an incomplete child snapshot. A probe that raises, and one answering `known` false: the reaper does not retire the run, and `stop_run` reports `child_scan_unknown`.
- T4 (C2 during the stop): the reporter appears only on the second scan of `stop_run`; it is registered and terminated in the same call.
- T5 (C3): `close_run` with server, client and `crash_reporter` records posts to the two game windows only.
- T6 (`orphan_guard`): `snapshot_processes_with_parent` maps fake ToolHelp entries to pid, ppid and name with exact case-insensitive names; a failed snapshot is `known` false; `snapshot_processes_by_name` answers as before.

Plus the suites that cover the lifecycle (`test_run_reaper`, `test_bug104_reap_under_quarantine`, the stop, adopt and close tests) and the whole `tools/tests` tree give the same result as on main.

## Docs

- CHANGELOG, Fixed: what C1 to C3 change for a caller, with the inbox id.
- `tools/README-mcp.md`, Troubleshooting: a DayZ crash can leave `CrashReporter.exe` holding the run's ports; `dayz_test_stop` on that run closes it. A run retired before this release is closed by hand.
