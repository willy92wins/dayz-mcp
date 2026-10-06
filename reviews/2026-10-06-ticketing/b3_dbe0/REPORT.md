## CHANGES

- `tools/dayz_mcp/pack_only.py:has_binarizable_assets` — iterative `os.scandir` walk. Entries are inspected with `follow_symlinks=False`. Symbolic links are skipped. Directories whose reparse tag has the name-surrogate bit (`0x20000000`, junctions and symlinks) are not descended into. Other reparse tags, including OneDrive cloud placeholders (`0x9000001A` has that bit clear), stay in the walk. Suffix matching is unchanged (`.p3d`, `.paa`, `.rvmat`, casefold). `OSError` from the listing is not caught.
- `tools/dayz_mcp/pack_only.py:should_pack_only` / `addon_builder_packonly_args` — unchanged. `pack_only=True` still short-circuits before the scan via `or`.
- `tools/dayz_mcp/dayz_test_worker.py:_default_has_assets` — delegates to `pack_only.has_binarizable_assets` and still maps `OSError` to `build_source_unavailable`. The worker still calls this predicate before the build frame. No source-link authorization was added or removed here.
- `tools/tests/test_pack_addon_packonly.py:test_worker_default_scan_matches_shared_predicate` — the worker source pin `".p3d", ".paa", ".rvmat"` was updated to `pack_only.has_binarizable_assets` because the suffix set now lives only in `pack_only.BINARIZABLE_SUFFIXES`. The behavioural comparison with `_default_has_assets` is unchanged.
- `tools/dayz_mcp/native_bundle.py:_APP_PACKAGED_MODULES` and `tools/build_native_launcher.py:PACKAGED_MODULES` — `pack_only.py` added so the worker import stays inside the sealed module set. The two lists are required to match.
- `tools/packaged-modules.lock.json` — regenerated with `write_packaged_modules_lock.py` so the lock hashes `dayz_test_worker.py` and includes `pack_only.py`. The launcher exe was not rebuilt.
- `CHANGELOG.md` — one Fixed line under `[Unreleased]`.
- `tools/pack-addon.ps1` was not changed. The spec says that script has its own predicate and this change is Python only. #195 is not in this snapshot, so the helper was not reconciled with that draft and the worker was not resealed.

## TESTS ADDED

Module `tools/tests/test_pack_only_junctions.py`:

- `test_self_junction_without_assets_returns_false_within_bound` — real `CreateJunction` of the source onto itself, no qualifying assets, child process timeout 5s. Fails on unmodified code: `Path.rglob` follows the junction and the child hits the timeout or dies with `RecursionError` / `OSError` instead of printing `False`.
- `test_regular_p3d_returns_true` — separate tree, nested `body.P3D`, no junction. A `.p3d` found first must not be the only proof the walk is bounded.
- `test_outside_junction_is_not_followed` — junction to a directory that holds `secret.p3d`; the source itself has only a text file, so the predicate is false. A later local `.paa` is still true.
- `test_placeholder_tag_is_not_a_name_surrogate` — cloud tag `0x9000001A` is not a name surrogate; mount-point `0xA0000003` and symlink `0xA000000C` are. The walk is patched so a real directory tagged as cloud is entered (its `.p3d` counts) and one tagged as a mount point is not (its `.rvmat` does not).

## GATE OUTPUT

```
--- tests.test_pack_only_junctions: rc=0
Ran 4 tests in 0.092s

OK
--- fast tier: ran=5591 baseline_ran=5587 failures=2 new=0
GAUNTLET_GATE: PASS
```

## DEVIATIONS

- `tools/pack-addon.ps1` still has its own scanner. The spec says that predicate is separate and this change is Python only.
- #195 is not in the tree. No reseal and no PBO, as specified. The sealed `dayz-test-launcher.exe` still embeds the previous worker.
- Delegating `_default_has_assets` makes `dayz_test_worker.py` import `pack_only.py`. That failed the packaged-module closure test and the packaged-modules lock. `pack_only.py` was added to both packaged-module lists and `tools/packaged-modules.lock.json` was regenerated. Those two files sit outside `tools/dayz_mcp/`, `tools/tests/` and `CHANGELOG.md`.
- The worker source pin in `test_pack_addon_packonly.py` was updated from the inline suffix string to `pack_only.has_binarizable_assets`. The runtime comparison with `_default_has_assets` is unchanged.
- The placeholder case classifies tags and drives the real walk through a patched `_reparse_tag`. A live OneDrive placeholder file was not created; the tag cannot be minted in a test.

## NOT VERIFIED

- The sealed launcher exe and its `closure-manifest.json` still pin the pre-change worker hash. Bundle tests that require a built bundle were not part of the failing fast tier; they were not re-run against an installed bundle.
- In-game build, the native launcher's `pack_only` flag, and `pack-addon.ps1` parity.
- Behaviour of a real dehydrated OneDrive file under `os.scandir`.
- Which exception unmodified `rglob` raises on this interpreter; the regression uses a 5s child-process timeout instead of asserting an exception type.
