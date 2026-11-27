## DayZ MCP — sesión compartida
1. Adquirir lease antes de mutar o gestionar procesos.
2. Liberarlo en cuanto termine la secuencia exclusiva.
3. No matar procesos DayZ directamente; usar el lifecycle guard.
4. Ejecutar `session_status` antes del handoff y documentar cierres degradados.
Runbook: `C:\Users\guill\ObsidianVault\AI\20_Runbooks\dayz-mcp-agent-session-protocol.md`.

## DayZ MCP — la caja es una cola
- `session_status()` → `box.occupied`. Un daemon o arnés que solo escucha en otro puerto no ocupa la caja.
- Ocupada: `dayz_test_run(..., on_busy="queue")` y espera; no sondees huecos.
- Nunca cierres ni mates lo ajeno, ni borres `m1.espera`, ni preguntes a Guillermo si cerrarlo.
- En scripts, `active_run_exists`, `box_reserved` o una espera vencida no cuentan como intento fallido.
Detalle: la sección «La caja está ocupada» del runbook.
