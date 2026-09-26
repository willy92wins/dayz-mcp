# Triaje C resolve EVIDENCIA — 2026-09-11

Source: Temp/orq-dispatch/triaje-c-agy-20260911/TABLA.md (agy gemini-3.8-flash-low).
Orq path:line verify DayZ_MCP_dev: 35/35 OK. RP GO resolve EVIDENCIA sin caja/Sol.
Skip: fb-20260828-211445-3bb4 (B3 #6 vivo). No HOLD 344d / AISLADO 668f / CAMBIO cola.

| id | veredicto | path:line | reason |
| --- | --- | --- | --- |
| fb-20260828-212912-f6ac | EVIDENCIA | tools/dayz_mcp/server.py:774 | ui_click diagnosticado antes de not_handled en puente C |
| fb-20260829-024747-55dd | EVIDENCIA | tools/dayz_mcp/server.py:130 | flags=0 mapea a ECE_PLACE_ON_SURFACE permitiendo snap |
| fb-20260829-024848-c7ca | EVIDENCIA | tools/dayz_mcp/inbox.py:144 | _check_length(resolution, 2000) y evidence_ref ya validados en codigo |
| fb-20260829-025012-103f | EVIDENCIA | tools/dayz_mcp/tool_registry_fingerprint.py:196 | RegistrySnapshot ya incluye session_id como primer campo |
| fb-20260829-025502-251d | EVIDENCIA | tools/tests/test_wait_for.py:489 | test_log_matches_lookback_zero_misses_preexisting_line cubre lookback=0 |
| fb-20260829-025754-f201 | EVIDENCIA | tools/dayz_mcp/effective_schema.py:29 | resolve_effective_schemas resuelve schemas post-build_app ya implementado |
| fb-20260829-030056-d73b | DUPLICADO | tools/tests/test_wait_for.py:489 | Duplicado de fb-20260829-025502-251d (BUG-086 lookback=0) |
| fb-20260829-032121-fc6e | EVIDENCIA | tools/dayz_mcp/server.py:130 | ECE_PLACE_ON_SURFACE = 1060 definido y flags=0 mapeado |
| fb-20260829-104543-47c9 | EVIDENCIA | tools/dayz_mcp/server.py:774 | diagnostic detail in error on not_handled |
| fb-20260829-104608-4d66 | EVIDENCIA | tools/dayz_mcp/process_lifecycle.py:3860 | reap_dead_runs retires all-dead runs |
| fb-20260829-104625-7c88 | EVIDENCIA | tools/dayz_mcp/server.py:2198 | mode and run_id described on schema |
| fb-20260829-104630-141e | EVIDENCIA | tools/dayz_mcp/effective_schema.py:29 | resolve_effective_schemas inspects tool parameters |
| fb-20260829-115147-4407 | EVIDENCIA | tools/dayz_mcp/dayz_test_storage.py:46 | storage_1 modset rotation module implemented |
| fb-20260829-133459-a396 | EVIDENCIA | tools/dayz_mcp/steam_preflight.py:84 | SteamPreflightProvider requires process_exists check |
| fb-20260829-135408-cc2d | EVIDENCIA | tools/dayz_mcp/process_lifecycle.py:723 | Orphan post-restart documentado; reapea solo confirmed dead sin preemptar run ajeno |
| fb-20260829-135727-782b | EVIDENCIA | tools/dayz_mcp/knowledge.py:36 | KNOWLEDGE_REMEDY define prepare y status sin exigir comando externo manual |
| fb-20260829-184906-21f5 | EVIDENCIA | tools/dayz_mcp/server.py:774 | Docstring y diagnâ”œâ”‚stico distinguen handler que declina vs no handler |
| fb-20260829-194823-ffc7 | EVIDENCIA | tools/dayz_mcp/dayz_test_modes.py:102 | MODE_RECORDS implementa autoridad de cuatro capas cerrada e inmutable |
| fb-20260829-221423-b2c4 | EVIDENCIA | tools/dayz_mcp/server.py:767 | _UI_CLICK_DIAGNOSTIC_KEYS rescata handler, user_id y clicked en errores bridge |
| fb-20260830-010517-9d46 | EVIDENCIA | tools/dayz_mcp/dayz_test_modes.py:102 | MODE_RECORDS define 4 modos formales (all, server, client, offline) |
| fb-20260830-112422-2762 | EVIDENCIA | tools/dayz_mcp/server.py:768 | _UI_ECHO_KEYS ya captura requested_path y excluye text deliberadamente |
| fb-20260830-112438-40e4 | EVIDENCIA | tools/dayz_mcp/server.py:2335 | _normalize_entities_cargo normaliza has_cargo como bool o None |
| fb-20260830-112522-1082 | EVIDENCIA | tools/dayz_mcp/server.py:603 | key_press y player_respawn estan declaradas activas en tabla client |
