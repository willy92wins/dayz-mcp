## DayZ MCP — sesión compartida
1. Adquirir lease antes de mutar o gestionar procesos.
2. Liberarlo en cuanto termine la secuencia exclusiva.
3. No matar procesos DayZ directamente; usar el lifecycle guard.
4. Ejecutar `session_status` antes del handoff y documentar cierres degradados.
Runbook: `C:\Users\guill\ObsidianVault\AI\20_Runbooks\dayz-mcp-agent-session-protocol.md`.
