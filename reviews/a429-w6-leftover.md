# W6 leftover — a429 overlay (2026-09-12)

Worktree `cursor/w6-a429-overlay-leftovers-8c97` from `origin/main` `edc7bb3` (overlay `80ed00b`). No in-game. No occupancy. No `feedback.jsonl`. H14 files untouched.

## Unittest (`plan-a429.md`)

Interpreter: dump `tools/.venv-mcp`. Cwd: this worktree `tools/`.

```
python -B -m unittest tests.test_a429_overlay tests.test_session_status_blocked_on tests.test_mcp_tools.MCPToolsTest.test_bridge_status_publishes_frozen_tool_registry_overlay tests.test_mcp_tools.MCPToolsTest.test_loopback_status_omits_tool_registry_overlay tests.test_server_freshness.ServerFreshnessTest.test_fresh_response_has_no_marker tests.test_server_freshness.ServerFreshnessTest.test_bridge_status_is_live_but_registry_fingerprint_is_frozen -v
```

**19 tests, exit 0.** Added: remediation is never a `str`; unknown signal is `{code, scope:tools}`; frozen fingerprint test locks `null` then the object.

## Live daemon (PID listening on `:8765`, `--no-daemon-autospawn`, no lease)

| Probe | Result |
|---|---|
| HTTP `GET /status` | 200. Overlay keys absent. `mutation_rejects_by_code` ints. N6 live. |
| Overlay helpers on that payload | `tool_registry_remediation is null` (`fresh`), not the legacy string. `daemon_source_remediation is null`. `fence.mutation_rejects_meta.blocks_now is false`. |
| MCP `--client` `bridge_status` / `session_status` | `isError`: `daemon_reaccreditation_failed_open_new_session`. H10/credential lane owns this (`control_client.py`, `daemon_credential.py`). Not patched here. |
| HTTP `GET /session/status` | 404 (POST-only). No identity POST (would not be cheap/read-only). |

I3 in-game was not run.

## Migration note

`tools/README-mcp.md` §MCP overlay schema (a429). No bridge bump.
