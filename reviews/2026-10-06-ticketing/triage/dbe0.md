# Triage dbe0

Tickets: fb-20261001-175006-dbe0

Title: dayz_test_worker: el predicado de assets (Path.rglob) recorre bucles de junctions dentro del source

First pass by GLM-5.3-Flash-EXL3 on the GX10 (API direct, grep excerpts of origin/main 6671fd2, no whole-tree access). Raw model output below, unedited.

<!-- ticket dbe0 | model GLM-5.3-Flash-EXL3 | finish stop | 416s | usage {"prompt_tokens": 40614, "completion_tokens": 4546, "total_tokens": 45160, "prompt_tokens_details": {"cached_tokens": 0}, "completion_tokens_details": {"reasoning_tokens": 3391}} | grep ['_default_has_assets', 'TICKET', 'has_binarizable_assets', 'IO_REPARSE_TAG_MOUNT_POINT', 'dayz_test_worker', 'pack_only', 'contexto', 'enlace', 'predicado', 'problema', 'recorre', 'Projects', 'cuenta', 'devolver'] -->

## VERDICT
OPEN_CONFIRMED — the asset predicate on main still walks `Path(source).rglob("*")` with no junction guard, in both the worker and its `pack_only` twin, so a self-referential junction in the source still enters a cyclic traversal before any later rejection can run.

## EVIDENCE
- `tools/dayz_mcp/dayz_test_worker.py:633-637` — `_default_has_assets` iterates `Path(source).rglob("*")` filtering `path.is_file()`; no reparse-point/junction check anywhere in the walk.
- `tools/dayz_mcp/dayz_test_worker.py:638-639` — any `OSError` from that walk (e.g. the path-length limit a junction cycle eventually hits) is collapsed into `_failed("build_source_unavailable")`, masking the real cause.
- `tools/dayz_mcp/dayz_test_worker.py:724-735` — the build path calls `has_binarizable_assets(source)` (line 735) before any broker frame is composed; the only pre-check shown is the basename gate (730-734), not a link check.
- `tools/dayz_mcp/pack_only.py:16-22` — twin `has_binarizable_assets` uses the identical `Path(source).rglob("*")` walk.
- `tools/dayz_mcp/pack_only.py:25-36` — `should_pack_only` and `addon_builder_packonly_args` reuse that walk, so the native-launcher/pack-addon `-packonly` decision shares the defect.
- `tools/dayz_mcp/request_path_authority.py:441-451` — reparse-point rejection exists only along explicitly accredited descendant paths (non-leaf tag must be 0; leaf mount point only when the sealed root is a junction); it is not a recursive scan of the source tree, so it does not guard the predicate. The ticket's `build_source_link_outside` and `_is_link` are NOT SHOWN.

## ROOT CAUSE
`pathlib.rglob` recurses into NTFS junctions (reparse points), so a junction inside `source` pointing at `source` itself or an ancestor makes the traversal cyclic; it only terminates when Windows path limits raise, and the `except OSError` then misreports the failure as `build_source_unavailable` (or, per the ticket's measurement, burns seconds before returning).

## PROPOSED FIX
**Option 1 (recommended, smallest):** replace the walk in `tools/dayz_mcp/pack_only.py:has_binarizable_assets` with an iterative `os.scandir` stack walk that never descends into links, using the same link test as #195 (`_is_link`: `IO_REPARSE_TAG_MOUNT_POINT` + `IO_REPARSE_TAG_SYMLINK` only, so OneDrive placeholders are still followed); make `dayz_test_worker._default_has_assets` delegate to it (keeping the `except OSError → build_source_unavailable` contract). Before: junction cycle → hang/`build_source_unavailable`. After: junction directories are listed but not recursed into; a `.p3d` beside the junction still yields `True`; caller sees the same boolean / same error token.

**Option 2:** run the #195 junction rejection (`build_source_link_outside`) before the predicate at `dayz_test_worker.py:735`. Rejected: it leaves `pack_only.py`'s walk (used by the native launcher path) exposed and only reorders the failure.

## TEST
Unit test (Windows): create a temp source dir containing `data/model.p3d`, then a junction `link -> <source>` inside it (`_winapi.CreateJunction` or `mklink /J`). Assert `dayz_test_worker._default_has_assets(source) is True` and that the call returns promptly. Before the fix the walk cycles and the test fails (raises `DayzTestWorkerError("build_source_unavailable")` via the `OSError` path, or takes ~seconds); after the fix it passes immediately.

## SIZE / RISK
S. Main risk is deployment, not logic: `dayz_test_worker.py` is one of the 19 sources sealed into the `dayz-test-v1` launcher (per `CHANGELOG.md:65`), so the fix only takes effect at the next reseal (v1.4) — until then the sealed binary keeps the old walk. Secondary risk: the link test must match #195's tag set exactly, or cloud-placeholder trees (OneDrive) change scan results.

## NOT VERIFIED
- `build_source_link_outside` and `_is_link` (PR #195): file and symbol not in the excerpts — NOT SHOWN; I cannot confirm the rejection really runs after the predicate.
- Where `execute_dayz_test_worker` is invoked relative to path sealing in `dayz_test_tool.py` (whether accreditation could reject the junction first on a real request).
- The exact `rglob`/junction semantics on the pinned Python (3.14.3 per product-spec): 3.13+ defaults `recurse_symlinks=False`, but whether junctions count as symlinks there is not shown; I take the ticket's 1.6 s measurement on trust.
- Whether the native launcher binary embeds `pack_only` (reseal scope) and whether any other callers of `has_binarizable_assets` exist outside the shown files.