# Tests reach the real Steam view through the session tools (bug ledger)

Base: main `2e21068`. Tests only: no product code, no sealed module, no PBO, no bridge change.

## What happens

`session_acquire`, `session_acquire_wait` and `session_status` observe this app's view of Steam
(`server.py:5111`, `5147`, `5266`). The observation runs `observe_app_steam_view()`
(`server.py:4551-4559`) on a daemon thread named `dayz-mcp-steam-app-view` (`server.py:4576-4581`),
at most once every 30 s (`_STEAM_APP_VIEW_TTL_S`, `server.py:4541`). It builds `WindowsSteamPreflightProvider(real_registry=True)`
(`steam_preflight.py:540`), which on Windows reads WMI and the process list
(`CreateToolhelp32Snapshot`). A test that calls one of those tools through the app, without
patching `server.observe_app_steam_view`, therefore reads the real Windows APIs on the Windows CI,
which the project's test rule forbids. The PC's guard stopped one at
`test_fb_2223_box_queue_offer.py:1065` (27/10).

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

## Change

- `tools/tests/__init__.py`: after the fast-tier desktop-gate guard, it replaces
  `server.observe_app_steam_view` with `no_steam_app_view`, which returns None, what the real
  observation answers when the views agree. One place for the whole suite, as the ledger asked,
  not a patch per test.
- `tools/tests/test_steam_app_view.py` is unchanged: its tests call
  `steam_preflight.observe_app_steam_view` with fakes or patch `server.observe_app_steam_view`
  themselves.

## Out of scope (bug ledger)

`test_client_credential_rotation_e2e.py` starts a real `--client` child (`:453`) and calls
`session_status` through it (`:578`, `:607`). `tests/__init__.py` does not reach a child process,
so on Windows that child reads WMI and the process list once per observation. Stopping it needs a
switch in the child's own code, which a tests-only PR does not add.

## Tests (fakes only)

New `tools/tests/test_steam_app_view_default.py`. Its fixture is the one of
`SessionToolsCarryTheAdvisoryTest`: the three tools through `build_app` in client mode, with the
runtime's answers mocked, the observation's cache expired before each call, the observation thread
joined after it, and `steam_preflight.WindowsSteamPreflightProvider` replaced by a recorder that
lists each build and fails any read.

| Id | Criterion | POS → expected | NEG → expected |
|---|---|---|---|
| C1 | The default is installed | `server.observe_app_steam_view` is `tests.no_steam_app_view`, not `steam_preflight.observe_app_steam_view`, and it returns None | M1 (the assignment removed) → C1 fails |
| C2 | No session tool builds a provider | the three calls → the recorder lists nothing, no answer has `steam_app_view`, and the cache's expiry moved forward (the observation ran) | M1 → C2 fails; M2 (the observation thread calls `steam_preflight.observe_app_steam_view` itself) → C2 fails |
| C3 | The recorder sees a real read | with the real `observe_app_steam_view` patched back, the same calls → a build with `real_registry=True`; after the patch the default is back | — (C3 is the negative control of C2) |
| C4 | Census | before the change: 72 builds in 27 tests of 8 modules (table above); after: 0 | — |
| C5 | Whole suite | on Linux under 3.13, the same failing ids as main `2e21068`, and the passes of main plus the 3 new tests | — |

Exit criteria: the new module passes with 3.13 under pytest and with 3.11 under unittest; M1 and
M2, one at a time in a copy and restored, each fail it with an AssertionError under both versions;
C4 and C5 as above.
