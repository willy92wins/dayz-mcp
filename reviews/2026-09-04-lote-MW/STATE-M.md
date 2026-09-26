## HECHO

### P0 — closed schema on seven touched tools
- Added `_patch_closed_tool_schema` at `tools/dayz_mcp/server.py:1754-1770`: sets `additionalProperties: false` and rejects unknown keys with `bad_args: unexpected arguments` before pydantic validation.
- Applied to all seven tools via `_CLOSED_SCHEMA_TOOLS` loop at `server.py:4595-4596`.
- Covered by: `gate/oracle.py` P0-* checks; `tools/tests/test_lote_m_products.py::test_p0_closed_schema_rejects_extras_inprocess`, `::test_p0_closed_schema_rejects_extras_wire`.

### P1 — `pipeline_resolve.evidence_ref`
- Added optional `evidence_ref` parameter at `server.py:4552-4568` (`Annotated[str | None, Field(max_length=240)]`) and forwarded to `inbox.append_resolution(..., evidence_ref=evidence_ref)`.
- Covered by: `gate/oracle.py` P1-*; `tools/tests/test_lote_m_products.py::test_p1_evidence_ref_roundtrip`, `::test_p1_invalid_evidence_ref_rejected`; `tools/tests/test_pipeline_feedback.py:280-285` (schema asserts `evidence_ref` optional).

### P2 — knowledge tools zero-arg closed schema
- `dayz_knowledge_status` and `dayz_knowledge_prepare` receive `_patch_closed_tool_schema` after registration (tools defined in `knowledge.py`, patch applied from `server.py:4595-4596`).
- Covered by: `gate/oracle.py` P2-*; `tools/tests/test_lote_m_products.py::test_p2_knowledge_empty_args_ok`, `::test_p2_knowledge_extras_rejected`.

### P3 — `capture_screenshot.crop_space` and dual return shape
- Added `crop_space: str = mcp_capture.DEFAULT_CROP_SPACE` at `server.py:3980`; forwarded to `capture_dual` at `server.py:4043`.
- Return always `[Image, json.dumps(meta)]` with `meta = {"fullres_path": ..., **meta}` at `server.py:4063-4064` (including `save_fullres=False`).
- Covered by: `gate/oracle.py` P3-*; `tools/tests/test_lote_m_products.py::test_p3_crop_space_forwarded`, `::test_p3_bad_crop_space_rejected`.

### P4 — UI tool wire enums and strict button
- `ui_click`: `mode: UiClickMode = "direct"` (`Literal["direct","complete"]`), `button: StrictInt = 0`, description names `mode_not_implemented` at `server.py:4272-4286`.
- `ui_reload_layout`: `mode: UiReloadLayoutMode = "reload"` at `server.py:4317`.
- Covered by: `gate/oracle.py` P4-*; `tools/tests/test_lote_m_products.py::test_p4_ui_click_mode_enum_and_strict_button`, `::test_p4_ui_reload_layout_mode_enum`.

### P5 — `dayz_test_run.mode` from M12 authority
- `DayzTestRunMode = Literal[*dayz_test_modes.public_mode_names()]` at `server.py:55`; `mode: DayzTestRunMode` at `server.py:2937`.
- Description updated: `extra_mods` + `mod_roots` at `server.py:2920-2921` (removed hand-written `mode is server|all|client`).
- Covered by: `gate/oracle.py` P5-*; `tools/tests/test_lote_m_products.py::test_p5_mode_enum_from_authority`, `::test_p5_offline_rejected`.

## GATES

Shell execution was blocked by Cursor pre-hooks (`launch-ledger.ps1`, `prime-agent-skills-gate.ps1`, `gpu-lease-gate.ps1`) with bash eval syntax errors on every attempt. Gates were not run in this session.

Expected commands (not executed):
```
bash gate/run.sh
bash gate/suite.sh
```

## TESTS EXISTENTES QUE CAMBIASTE

- `tools/tests/test_mcp_tools.py:308` — `{"project": "ExampleMod", "mode": "offline"}` → `{"project": "ExampleMod", "mode": "server"}` (schema enum no longer accepts `offline`).
- `tools/tests/test_mcp_tools.py:345` — same change.
- `tools/tests/test_mcp_tools.py:375` — same change.
- `tools/tests/test_mcp_tools.py:662` — same change.
- `tools/tests/test_pipeline_feedback.py:280-285` — added asserts for optional `evidence_ref` in schema/properties.

Mock return dict at `test_mcp_tools.py:286` still uses `"mode": "offline"` (simulated execute result, not wire input).

## LO QUE NO PUDE VERIFICAR

- `bash gate/run.sh` (G1 oracle) — shell blocked; expected `ORACULO-VERDE` if implementation is correct.
- `bash gate/suite.sh` (G2 suite) — shell blocked; expected `SUITE-ACOTADA OK`.
- `tools/tests/test_lote_m_products.py` — not executed (shell blocked).
- Possible G2 regression: `tools/tests/test_weak_agent_consumer_ux.py::test_capture_webp_uses_webp_mime` calls `tool.fn()` directly and expects a single `Image`; P3 now always returns `[Image, json.dumps(meta)]`. That file is outside the write-set and was not edited.

## DISPUTAS

Ninguna.
