# AUDIT — g5fix named-instance profile folders: provisioning and fail-closed paths (angle G)

Method: full read of the changed provisioning/reader code; every "safe because" re-derived; black-box execution of the
provisioning and admission matrix with the project venv (Python 3.14.3, Windows) against this workspace tree, temp dirs
only. No daemon, installer, git or DayZ was run.

## FINDINGS

### G1 — P3: a lost rename race between two concurrent `prepare` calls is typed only by the caller's blanket handler, and is labelled `instance_config_missing`

`tools/dayz_mcp/loopback.py:1791-1802`
```python
        try:
            # The same three fields install_mcp.py writes (install_mcp.py:977-984).
            atomic_write_json(
```
`tools/dayz_mcp/runtime_state.py:2245-2250`
```python
    if _observe_expected_target(path, expected_sha256) != expected_identity:
        raise RuntimeError("atomic_target_changed")
```
`tools/dayz_mcp/process_lifecycle.py:2260-2263`
```python
        except BindingPrepareError as exc:
            return None, exc.code
        except Exception:
            return None, "instance_config_missing"
```
Reasoning. `_seed_bridge_config` catches only `OSError` (loopback.py:1801), but `atomic_write_json` signals a lost
compare-and-replace with `RuntimeError("atomic_target_changed")`. Two threads that pass the leaf recheck
(loopback.py:1849-1850 `except FileExistsError: pass`) both find no config and both seed; the loser of the
`os.replace` raises RuntimeError out of `prepare`, untyped. The launch is correctly refused before spawn and the
winner's state is coherent, so this is mislabelling, not corruption: a benign lost race is reported as a missing/broken
config. Reachability is narrow (daemon admissions are serialized by the operation lock; the steam-gate release window
at process_lifecycle.py:3996-4006 is the plausible interleaving), and the same blanket handler is what keeps every
other untyped escape (see G2) inside the typed contract.

Executable scenario (passes; shows the loser's code path):

```python
import os, sys, threading, tempfile
sys.path.insert(0, r"<ws>\tools")
from dayz_mcp.loopback import ServerState
root = tempfile.mkdtemp()
os.makedirs(os.path.join(root, "p", "_server"))
leaf = os.path.join(root, "p", "_server", "profiles-130")
errs, ok = [], []
def run():
    s = ServerState(key="k", config_port=9210); s.instance_token = "130"
    try: ok.append(s.prepare("r", "server", leaf))
    except Exception as e: errs.append(repr(e))
t1, t2 = threading.Thread(target=run), threading.Thread(target=run)
t1.start(); t2.start(); t1.join(); t2.join()
# observed: one minted uuid in `ok`; one RuntimeError("atomic_target_changed") in `errs`
# in production that RuntimeError becomes instance_config_missing (process_lifecycle.py:2262)
```

### G2 — P3: three state-changing windows in `prepare` are unguarded inside `prepare` itself; the typed contract rests entirely on the caller's `except Exception`

`tools/dayz_mcp/loopback.py:1886-1912`
```python
            if config_path.is_file():
                try:
                    existing = json.loads(config_path.read_text(encoding="utf-8"))
```
```python
        if not config_path.is_file() and not self._seed_bridge_config(config_path):
            raise BindingPrepareError("instance_config_missing")
```
```python
        payload["instance"] = minted
        atomic_write_json(config_path, payload)
```
Reasoning. `config_path.is_file()` (1886 and 1900) and the final instance rewrite (1912) are outside any try in
`prepare`; `RuntimeError`s from the atomic writer (`atomic_temporary_unavailable`, `unsafe_runtime_path`,
`atomic_temporary_changed`, `atomic_target_changed`) and unusual `OSError`s from `is_file()` escape
`ServerState.prepare` raw. `process_lifecycle.py:2262-2263` converts every one to the typed
`instance_config_missing` and `_settle_failed_launch` (process_lifecycle.py:4174-4183) refuses the spawn, so no
launch is admitted on an unprepared folder and no credential is left half-written (the temp file is unlinked by
`_unlink_if_same`, runtime_state.py:2253-2261). Consequence is limited to error-code fidelity and to the fragility of
a contract that a future caller could bypass by calling `prepare` directly. No user-visible defect found; recorded
because the spec's "failures return the existing typed instance_config_missing" is met one layer above the code that
fails.

Executable scenario: same harness as G1, but patch `dayz_mcp.runtime_state._atomic_write_text` to raise
`RuntimeError("atomic_temporary_unavailable")` once; `s.prepare(...)` raises RuntimeError, not
`BindingPrepareError`; wrapping it as `process_lifecycle` does yields `instance_config_missing`.

Not defects, re-verified and closed: (a) non-str/empty/`"default"`/malformed bound tokens now fail typed
(`instance_config_missing`) where the old code silently defaulted or built `profiles-<raw>`; unreachable from the
daemon (server.py:8714 validates at bind) and from the worker (dayz_test_request.py:473-480 validates in the sealed
request; worker re-checks at dayz_test_worker.py:443-459) — intended per SPEC §2. (b) `_atomic_write_text`'s
`mkdir(parents=True)` cannot create ancestors: both call sites verify the parent first (loopback.py:1789, and the
provisioning check at :1824). (c) Lock hashes: sha256 of `server_cli.py`, `dayz_test_worker.py`,
`dayz_test_attestation.py` in this tree match `tools/packaged-modules.lock.json` exactly.

## FAIL-CLOSED MATRIX

All rows executed against this tree (venv win-x64, temp dirs); "typed" = `BindingPrepareError` code observed.

| Case | Observed behaviour |
|---|---|
| Mistyped `dev_root` (project root absent) | typed `instance_config_missing`; nothing created (workspace snapshot empty) |
| Project exists, role root (`_server`/`_client`) absent | typed `instance_config_missing`; no ancestor created |
| Leaf pre-existing as file | typed `instance_config_missing`; file untouched |
| Leaf is a junction to a dir outside the role root | typed `instance_config_missing`; nothing written at the target |
| Role root is a junction away from the project | typed `instance_config_missing` (resolved-parent check, loopback.py:1831-1834) |
| Dangling reparse point at leaf | typed `instance_config_missing`; no repair, no creation |
| Leaf absent, credentials invalid (empty key / bool / non-int port) | typed before any mkdir (loopback.py:1841-1846) |
| Leaf absent, credentials valid | exactly one dir created (`parents=False`); seeded `{url,key,pollHz}` + `instance`, nothing else |
| Concurrent mkdir race | single directory; both threads proceed past creation |
| Existing config, foreign url/key | typed `instance_endpoint_mismatch`; config bytes verbatim (no overwrite) |
| Existing config, matching url/key | instance appended, foreign fields preserved; reread-verified, else `instance_config_mismatch` |
| Leaf basename ≠ `profiles-<token>` / legacy `profiles` under named bind | typed `instance_profile_owner_mismatch` |
| role `offline` | provisioned under `_client`, matching mode roots |
| role-root name ≠ `_server`/`_client` (e.g. `_client2`) | typed `instance_config_missing` |
| relative `profiles` path | typed at entry (loopback.py:1868-1873) |
| Malformed bound token on the state | typed `instance_config_missing` (profile_leaf_name validates) |
| Default instance, `profiles` absent | typed `instance_config_missing`; nothing created — parity with pre-change behaviour |
| Default instance, `profiles` present | seeded/rewritten exactly as before; argv `-profiles=...\profiles` byte-identical (executed) |

Token authority: one chain only — daemon bind `validate_instance_token` (server.py:8714) → tool seals `bound[0]`
(dayz_test_tool.py:482-485) → request parser revalidates (dayz_test_request.py:473-480) → worker/VPP read the sealed
payload only (dayz_test_worker.py:443-459, native_launcher_transaction.py:292-293) → daemon `prepare` cross-checks the
argv-derived leaf against the bound token (loopback.py:1875-1884) → every reader revalidates the recorded anchor in
the sealed project before use (`_validated_recorded_leaf`, dayz_test_tool.py:811-844; `is_allowed_profiles_dir` +
`_recorded_anchor_in_sealed_project`, launch_logs.py:136-165). No env var, directory listing, newest-match or
existing-config inference exists in the changed paths (grepped).

Recorded-hostile-input truth tables (executed): `..` in any segment, relative, non-str, other-token, legacy-in-named,
foreign project root, wrong role parent, deeper nesting, trailing/mixed separators, case-only differences — each
admitted or rejected exactly as the spec requires; anchor validation runs before role selection in
`_profiles_dir_for_role` (server.py:2736-2745), close watches (dayz_test_tool.py:4228-4252), stop artifacts
(:1690-1729, server-anchor expansion retained), extension (:604) and capture (server.py:7559).

## NOT VERIFIED

- Live resealed launcher bundle, packaged import closure and builder/verifier parity: in-tree lock hashes match, the
  bundle build itself is orchestrator work (matches SOL_R3).
- Real junction behaviour inside the live daemon (adoption, recovery, idle warden) and any DayZ-side behaviour: no
  daemon/DayZ run (boundaries); the matrix covers the functions directly.
- True symbolic links (privilege-dependent on this host) — junctions only; `is_symlink()` on 3.14 treats junctions
  separately, the dangling-junction row above covers the reparse case.
- The full regression suite was not executed; the repo test files in the diff were read, not run.
- Sub-1500-word budget: this file is the deliverable; no other file was modified. `_scratch/` removed after use.
