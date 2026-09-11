# fb-20260909-213257-49a9 — extra_mods name-form docs

- Ticket: `fb-20260909-213257-49a9`
- Author: Auditor (Grok Bot)
- Status: PROPOSED pending Guillermo OK + Codex review
- Branch: `auditor/fb-20260909-213257-49a9-extra-mods-docs`
- Base: `work/inbox-20260830-modules` @ `d216762`

## What changed

File-only / docs + error text. No behaviour change to which entries are accepted.

- `tools/dayz_mcp/dayz_test_tool.py`
  - `bad_mod` now keeps the token as prefix and names the accepted form: a single folder name such as `@DayZ_MCP`, or an absolute path inside the project's `mod_roots`; relative paths with `\` or `/` are rejected.
  - `bridge_mod_missing` keeps `add extra_mods=['@DayZ_MCP']` and states that the folder name must be explicit in `extra_mods` or as the project mod; `base_mods` and `server_mods` do not count.
- `tools/dayz_mcp/server.py`
  - `dayz_test_run` tool description no longer says `extra_mods` "accepts any folder".
  - Same contract is published on the `extra_mods` property via `_describe_run_parameters`.
- Focused tests in `tools/tests/test_dayz_test_tool.py` and `tools/tests/test_lote_v_products.py` lock the error prefix and the published wording.

## What NOT changed

- `GATES.md`, Obsidian, inbox ticket state (not resolved / not closed).
- Tickets already in HEAD: 62e3 / 6157 / c82e.
- Validation itself (`_valid_mod_entry` / `_valid_public_mod`). Same accepts / rejects.
- Steam, ActiveProcess, filePatching, in-game / Enforce / PBO.
- No native reseal, no fingerprint artifact rewrite.

## How to verify

From `tools/`:

```text
python -m unittest tests.test_dayz_test_tool.DayzTestToolRequestTest.test_build_run_request_requires_bridge_in_effective_mods tests.test_dayz_test_tool.DayzTestToolRequestTest.test_build_run_request_rejects_unknown_project_and_public_paths tests.test_dayz_test_tool.DayzTestToolRequestTest.test_build_run_request_names_accepted_form_for_relative_mod_path tests.test_lote_v_products.PublishedExtraModsNameFormTest -v
```

Expect `OK`. Prefixes `bad_mod:` and `bridge_mod_missing:` stay matchable; the suffix names the form.

## Status

PROPOSED. Do not merge without Guillermo OK and Codex review.
