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
  character for character, and `setdefault` keeps any value already set: every fixture child of
  the suite sets its own (`test_bug046_startup_deadlock.py:794`,
  `test_client_credential_rotation_e2e.py:496`, `test_fb_acf3_88c2_8575_4fdf_named_causes.py:853`).
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
recorder that lists each build and each read, and fails the read. Each call starts from an expired
cache. After it, each observation thread the call started is joined for up to 30 s and must have
finished, and the cache's expiry must have moved forward: the call observed.

| Id | Criterion | POS → expected | NEG → expected |
|---|---|---|---|
| C1 | The default is installed | `server.observe_app_steam_view` is `tests.no_steam_app_view`, not `steam_preflight.observe_app_steam_view`, and it returns None | M1 → C1 fails |
| C2 | No session tool builds or reads a provider | one subtest per tool: the recorder lists no build and no read, the answer has no `steam_app_view`, and the call observed | M1, M2 → C2 fails for each tool; M3 → for `session_acquire`, which did not observe; M6 → for each tool: the first observation outlives its join, and the calls after it cannot observe while it runs |
| C3 | The recorder sees a real read | with the real `observe_app_steam_view` patched back, one subtest per tool: a build with `real_registry=True` and a read of it; after the patch the default is back | M3 → C3 fails for `session_acquire`; M4 → for each tool, built and never read (C3 is also the negative control of C2) |
| C6 | A fixture child gets the view | a child with `PYTHONPATH` = the fixture site prints `tests.no_steam_app_view` | M1 → C6 fails |
| C7 | A child without the fixture keeps the real observation | `PYTHONPATH` = `tools` → it prints `dayz_mcp.steam_preflight.observe_app_steam_view` | — (C7 is the negative control of C6) |
| C8 | The modules that default `LOCALAPPDATA` load without it | a child without `LOCALAPPDATA` imports `tests.test_progressive_disclosure` and `tests.test_fn_p0_small_model_loops` with rc 0; in the same environment `from dayz_mcp import server` alone fails with `KeyError: 'LOCALAPPDATA'` | M5 → C8 fails |
| C4 | Census | before the change: 72 builds in 27 tests of 8 modules (table above); after: 0 | — |
| C5 | Whole suite | on Linux under 3.13, the same failing ids as main `2e21068`, and the passes of main plus the 3 new in-process tests | — |

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
