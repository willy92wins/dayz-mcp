# Tests reach the real Steam view through the session tools (bug ledger)

Base: main `2e21068`. Tests only: no product code, no sealed module, no PBO, no bridge change.

Scope: the default observation of the three session tools. A test that reads WMI on purpose is not
this view's and stays as it is: `test_daemon_spawn_outside_app.py` reads it through `wmi_host`
(`:341-345`) and through a real-registry provider (`:347-355`).

## What happens

`session_acquire`, `session_acquire_wait` and `session_status` observe this app's view of Steam
(`server.py:5115`, `5173`, `5268`). The observation runs `observe_app_steam_view()`
(`server.py:4551-4559`) on a daemon thread named `dayz-mcp-steam-app-view` (`server.py:4576-4581`),
at most once every 30 s (`_STEAM_APP_VIEW_TTL_S`, `server.py:4541`). It builds `WindowsSteamPreflightProvider(real_registry=True)`
(`steam_preflight.py:540`), which on Windows reads WMI and the process list
(`CreateToolhelp32Snapshot`). A test that calls one of those tools through the app, without
patching `server.observe_app_steam_view`, therefore reads the real Windows APIs on the Windows CI,
which the project's test rule forbids. The PC's guard stopped one on 27/10:
`test_2223_r2_non_string_queue_session_does_not_break_status` (`test_fb_2223_box_queue_offer.py:1065`),
whose `session_status` call is at `:1081`.

Census on Linux, 2026-10-28: a copy of `tests/__init__.py` recorded every provider built on the
observation thread, with `_STEAM_APP_VIEW_TTL_S` at 0 so that every call observes. On main
`9713894`, the whole suite built 72 providers (36 observations, each a real-registry provider and
the app's) in 27 tests of 8 modules:

| Module | Tests |
|---|---|
| `test_fb_e7ef_b753_catalog.py` | 8 |
| `test_pleno_lease_and_orphans.py` | 6 |
| `test_a429_overlay.py` | 5 |
| `test_session_status_blocked_on.py` | 3 |
| `test_fb_2223_box_queue_offer.py` | 2 |
| `test_client_acquire_wait.py` | 1 |
| `test_client_dumps_cross_process.py` | 1 |
| `test_mcp_tools.py` | 1 |

The same census of `test_fb_6310_human_input_holder.py`, the only test module `2e21068` adds,
built none. With the change below, the whole suite built none.

A census on Linux cannot see a test that skips there, or fails there before it reaches a session
tool. `test_shareconflict_windows.py`, for one, skips on Linux (`:93`) and calls `session_status`
through `build_app` in its own process (`:165-191`), so on Windows it reads WMI as well; the change
below covers it the same way.

## Children of the suite

A child whose `PYTHONPATH` starts with the fixture site `tests/fixtures/dayz_mcp` gets the same
view. Its `sitecustomize.py` calls `_install_fixture()` (`:187`), which calls
`_bind_checkout_tree()` (`:55`) before it reads the fixture mode (`:56-58`); that function imports
`tests._tree_identity` (`:47-48`), and so runs `tests/__init__.py`. The real `--client` child of
`test_client_credential_rotation_e2e.py` (`:453`) runs with that site first on its `PYTHONPATH`
(`:442`, `:498`), so its `session_status` calls (`:578`, `:607`) go through the installed view.
Round 1 said this child was out of reach and needed a switch in its code; that was wrong (Codex Sol
r1, H1). A child without the fixture site keeps the real observation. `FixtureChildSteamViewTest`
checks both in a child.

## Change

- `tools/tests/__init__.py`: after the fast-tier desktop-gate guard, it defaults `LOCALAPPDATA`,
  then replaces `server.observe_app_steam_view` with `no_steam_app_view`, which returns None, what
  the real observation answers when the views agree. One place for the whole suite, as the ledger
  asked, not a patch per test.
- Why the default: the package now imports the server before any test module's body, and
  `inbox.py:23` reads `LOCALAPPDATA` on import. `test_progressive_disclosure.py` (`:22-24`) and
  `test_fn_p0_small_model_loops.py` (`:21`) default it themselves before they import the server;
  without the same default first in the package, either module run alone by a Windows interpreter
  without `LOCALAPPDATA` would stop with a KeyError (Codex Sol r1, H2). The default is theirs,
  character for character, and `setdefault` keeps any value already set.
- Children that run with the fixture site: the daemon and client fixtures set their own
  `LOCALAPPDATA` under their temporary directory (`test_bug046_startup_deadlock.py:920-922`,
  `:998-1000`, `:1092-1094`, `:1600-1607`; `test_client_credential_rotation_e2e.py:185-187`,
  `:494-498`; `test_fb_acf3_88c2_8575_4fdf_named_causes.py:852-854`), and so does C6 below. The
  import probes of `test_bug046_startup_deadlock.py` do not: they copy the parent's environment
  (`:1384-1386`, `:1469-1470`, `:1501-1502`) and inherit its `LOCALAPPDATA`, the runner's or, when
  the suite starts without one, the package's default. They import `dayz_mcp` or
  `tests._tree_identity` and print what they loaded. The probe at `:1433-1436` runs with `-I`,
  which ignores `PYTHONPATH`, so the fixture site does not load there.
- Their Linux fakes for `ctypes.WinDLL` and `msvcrt` (`test_progressive_disclosure.py:25-44`,
  `test_fn_p0_small_model_loops.py:22-41`) now also come after the server import, and need no
  move: on main `2e21068`, on Linux, neither module imports at all, with or without
  `LOCALAPPDATA`. Their server import (`test_progressive_disclosure.py:48`) reaches
  `dayz_test_worker.py:1222` through `server.py:29` and `dayz_test_tool.py:18`, and that line reads
  `stat.IO_REPARSE_TAG_MOUNT_POINT`, which Linux lacks (measured 2026-10-28 under 3.13 and 3.11).
- `tools/tests/test_steam_app_view.py` is unchanged: its tests call
  `steam_preflight.observe_app_steam_view` with fakes or patch `server.observe_app_steam_view`
  themselves.

## Tests (fakes only)

New `tools/tests/test_steam_app_view_default.py`. Its in-process fixture is the one of
`SessionToolsCarryTheAdvisoryTest`: the three tools through `build_app` in client mode, with the
runtime's answers mocked, and `steam_preflight.WindowsSteamPreflightProvider` replaced by a
recorder that lists each build and each call of its `read_active_process`, the method the
observation reads first (`steam_preflight.py:508`, `:419`), and fails the call. Each call runs
outside any subtest, so a call that fails ends the test and no other tool runs while its
observation does. It starts from an expired cache. Each observation thread it started is joined
for up to 30 s, also when the call raises; the thread must have finished, and the cache's expiry
must have moved forward: the call observed. The patches the fixture starts
are restored in one cleanup, which keeps them all while an observation thread of the test still
runs: restored, the provider it builds next would be the real one. They then stay for the rest of
the run, so later tests that need the real provider class can fail too, as `test_steam_app_view.py`
did when it ran after this module under M6; in discover order it runs before, and
`test_steampost_readiness.py` after (it builds the provider at `:235` and calls a method the
recorder lacks at `:263`). The failure is loud, and no test reads the real registry through this
view.

C9 and C10 check that cleanup itself. They run a stalled call through unittest's own `run`, with a
result of their own, so the cleanup that `setUp` registered runs while the observation does, and
then look at what it recorded and left. The stalled call also patches `threading.Thread.join` with
a wrapper that joins and lists the thread, so the checks see the join the call made, not one the
fixture says it made. The stalled test copies that list once the call has returned or raised,
before any cleanup of its own runs, so a join left to a cleanup is not counted as the call's. The
stalled view returns only when the outer test sets its event, with no timeout of its own. The outer
test sets it in a `finally` around that run and its checks, so any exception that leaves them,
`KeyboardInterrupt` included, sets it before the threads are joined and the patches restored; a
process that ends without unwinding runs no `finally`, and the thread is a daemon. The outer test
also registers that end as a cleanup, so an end that raises, its join interrupted or the thread
still running after 30 s, is tried once more; after an end that finished, the second does nothing.
If both fail, the patches stay, `Thread.join` included, and the test has failed. A
`KeyboardInterrupt` leaves before unittest runs the cleanups, so then only the end in the `finally`
runs. Each case takes the globals the stalled call patches as it finds them, and checks them apart
against what the suite sets: the provider class and `Thread.join` of their own modules, the suite's
view, and a wait above 0. Once the view has returned, `restore` must put back what the case found.

| Id | Criterion | POS → expected | NEG → expected |
|---|---|---|---|
| C1 | The default is installed | `server.observe_app_steam_view` is `tests.no_steam_app_view`, not `steam_preflight.observe_app_steam_view`, and it returns None | M1 → C1 fails |
| C2 | No session tool builds or reads a provider | for each tool, the call observed and, in a subtest per tool, the recorder lists no build and no read and the answer has no `steam_app_view` | M1, M2 → C2 fails for each tool; M3 → at `session_acquire`, which did not observe; M6 → at the first tool, whose observation outlives its join |
| C3 | The recorder sees a real read | with the real `observe_app_steam_view` patched back, for each tool: a build with `real_registry=True` and a call of that provider's `read_active_process`; after the patch the default is back | M3 → C3 fails at `session_acquire`; M4 → for each tool, built and never read; M7 → for each tool, the method fetched and never called (C3 is also the negative control of C2) |
| C9 | An observation still running fails its call, and the cleanup `setUp` registered keeps the patches | when the case starts, each global the stalled call patches is the suite's (the provider class and `Thread.join` of their own modules, the suite's view, a wait above 0); a stalled call through unittest's `run`: a view that returns only when the test sets its event, a tool that does not wait for it and a join of 0 s; the call fails as still running, and had joined its observation thread through `Thread.join` when it failed, before any cleanup of its test; the run records one failure, from that cleanup, and while the view runs each global keeps its patch (the recorder, the stalled view, a wait of 0 s, the listing join); once it returns, `restore` puts back each one, and the cache's contents, as the case found them | M8, M10, M11, M12, M14, M15 → C9 fails |
| C10 | So does a call that raises, which joins first | the same with the runtime's `session_status` raising: the call raises the tool's error, it had joined its observation thread through `Thread.join` when it raised, and the cleanup keeps the patches until the view returns | M8, M9, M10, M11, M12, M14, M15 → C10 fails |
| C6 | A fixture child gets the view | a child with `PYTHONPATH` = the fixture site prints `tests.no_steam_app_view` | M1 → C6 fails |
| C7 | A child without the fixture keeps the real observation | `PYTHONPATH` = `tools` → it prints `dayz_mcp.steam_preflight.observe_app_steam_view` | — (C7 is the negative control of C6) |
| C8 | The modules that default `LOCALAPPDATA` load without it | a child without `LOCALAPPDATA` imports `tests.test_progressive_disclosure` and `tests.test_fn_p0_small_model_loops` with rc 0; in the same environment `from dayz_mcp import server` alone fails with `KeyError: 'LOCALAPPDATA'` | M5 → C8 fails |
| C4 | Census | before the change: 72 builds in 27 tests of 8 modules (table above); after: 0 | — |
| C5 | Whole suite | on Linux under 3.13, the same failing ids as main `2e21068`, and the passes of main plus the 5 new in-process tests | — |

C6 to C8 start children with the test's own interpreter, in a temporary directory; the children
import and print, and call no session tool. They are slow-tier and run on Windows only, as the
server does not import on Linux (above). On Linux they were run through a local harness, not in the
repo, that fakes the Windows modules in the harness and in each child before the fixture site's
`sitecustomize` runs.

Mutants, one at a time in a copy and restored:

- M1: `tests/__init__.py` installs no view.
- M2: the observation thread calls `sys.modules["dayz_mcp.steam_preflight"].observe_app_steam_view()`, past the installed view (`server.py:4553`).
- M3: `session_acquire` does not observe: `steam_view = None` (`server.py:5115`).
- M4: the real observation builds its providers and returns None before it reads (after `steam_preflight.py:544`).
- M5: the package does not default `LOCALAPPDATA`.
- M6: the installed view takes 35 s.
- M7: the real observation gets the real-registry provider's `read_active_process` without calling it, and returns None (after `steam_preflight.py:544`).
- M8: the cleanup restores the patches while an observation thread runs.
- M9: a call joins its observation threads only when it returns: the registration stays in the
  `finally`, the join moves after it.
- M10: `setUp` registers a cleanup that stops the patches without checking the observation threads;
  `restore` is unchanged.
- M11: a call skips `Thread.join` when its join is 0 s.
- M12: the stalled call's patch of `_STEAM_APP_VIEW_WAIT_S` starts without `enter`, so `restore`
  never stops it.
- M14: the wait is 0.0 when the module is imported, as another module could leave it, with M12.
- M15: a call registers its 0 s join with `addCleanup` instead of making it, so the join runs among
  the test's cleanups.

Exit criteria: the new module passes with 3.13 under pytest and with 3.11 under unittest, and its
child tests pass in the harness under both; each mutant fails the new module where the table says,
with an AssertionError, under both versions; C4 and C5 as above.

## Round 2 (Codex Sol r1 on `5517a2c`: RED, two P1 and three P2)

- H1 (P1): the `--client` child is covered, through the fixture site. Round 1's plan, body and
  commit message said it was not; this plan says how it is, and C6 and C7 check it in a child.
- H5 (P1): the title said no test reads WMI or the process list, which `test_daemon_spawn_outside_app.py`
  does on purpose. The title and the scope now name what changes: the default observation of the
  session tools.
- H2 (P2): the `LOCALAPPDATA` default, and C8. The Linux fakes need no move (Change).
- H3 (P2): C2 and C3 per tool, with the reads recorded; M3 and M4 now fail the new module.
- H4 (P2): each call's observation thread must have finished before anything is checked or
  restored; M6 fails C2.
- The cite for 27/10: the test is at `:1065`, its call at `:1081`.

## Round 3 (Codex Sol r2 on `4e460b8`: RED, one P1 and two P2)

- H6 (P1): round 2 said every fixture child of the suite sets its own `LOCALAPPDATA`. The import
  probes of `test_bug046_startup_deadlock.py` inherit the parent's, and `:794`, one of its cites,
  starts a client without the fixture site. Change now says which children set their own and
  which inherit.
- H3 (P2): the recorder counted fetching an attribute as a read, so a real observation that fetched
  `read_active_process` without calling it passed C3. It now records a call of its own
  `read_active_process`, and C3 needs one from the real-registry provider for each tool (M7).
- H4 (P2): an `is_alive` failure inside a subtest let the next tools run and the cleanups restore
  the provider and the cache while the thread ran, and a call that raised skipped the join. Calls
  now run outside the subtests and join in a `finally`, and one cleanup keeps every patch while an
  observation thread runs (C9, C10; M8, M9).

## Round 4 (Codex Sol r3 on `28f19ab`: RED, one P1 and two P2)

- H7 (P1): with the registration kept in the `finally` and only the join moved after it, a call
  that joined only when it returned passed C10, which then joined the thread itself. The fixture now
  lists each join, and C10 checks that the call joined its observation before it raised (M9 is that
  edit).
- H8 (P2): C9 and C10 called `restore` by hand and emptied the patches before the cleanup `setUp`
  registered ran, so a registered cleanup without the check passed. They now run the stalled call
  through unittest's own `run` and check the failure that cleanup records and the patches it leaves
  (M10).
- H9 (P2): the stalled view waited 30 s at most, so a runner that held the test that long ended the
  observation before the checks. It now returns only when the test sets its event.

## Round 5 (Codex Sol r4 on `46cb499`: RED, one P1 and two P2)

- H10 (P1): round 4 said the outer test's cleanup sets the event on every path. A
  `KeyboardInterrupt` after the inner run leaves unittest's `run` before it runs the outer test's
  cleanups, so the event stays unset. The outer test now sets it in a `finally` around the run and
  its checks, and the plan says what that covers.
- H11 (P2): the fixture listed each join before it made it, so a call that listed the thread and
  skipped the join when it was 0 s passed C9 and C10. The fixture no longer lists joins; the
  stalled call lists the joins `Thread.join` makes (M11).
- H12 (P2): only the provider and the view were checked after `restore`, so a wait patch started
  without `enter` stayed at 0 s and passed. C9 and C10 now check each global the stalled call
  patches while the view runs and after `restore`, and the cache's contents after it (M12).

## Round 6 (Codex Sol r5 on `c209978`: GREEN, three P2)

- H13 (P2): round 5 dropped round 4's outer cleanup, so an end that raised, its join interrupted or
  the thread still running after 30 s, left every patch in place, `Thread.join` included. The outer
  test registers the end again as a cleanup, which does nothing after an end that finished. No test
  makes the first end fail, so H13 has no mutant; a local probe whose first end sets the event and
  raises leaves each global as the case found it, where `c209978` leaves them patched.
- H14 (P2): the suite's globals were taken when the module was imported, so a wait another module
  had left at 0.0 passed with M12, and a module imported under a temporary wait failed a correct
  restore. Each case now takes them when it starts, checks them apart against what the suite sets,
  and `restore` must put back what it found (M14). A local probe that imports the module under a
  wait of 10.0, which ends before its tests run, fails both stalled-call tests on `c209978` and
  passes here.
- H15 (P2): the joins were read after the inner run, so a call that left its 0 s join to a cleanup
  passed C9 and C10. The stalled test now copies them once the call has returned or raised, before
  its cleanups run (M15).
- On `c209978`, M14 and M15 pass the new module. M1 now fails C9 and C10 as well, at the check of
  the suite's view.
