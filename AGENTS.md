## DayZ MCP — sesión compartida
1. Adquirir lease antes de mutar o gestionar procesos.
2. Liberarlo en cuanto termine la secuencia exclusiva.
3. No matar procesos DayZ directamente; usar el lifecycle guard.
4. Ejecutar `session_status` antes del handoff y documentar cierres degradados.
Runbook: `C:\Users\guill\ObsidianVault\AI\20_Runbooks\dayz-mcp-agent-session-protocol.md`.

## DayZ MCP — la caja es una cola
- `session_status()` → `box.occupied`. Un daemon o arnés que solo escucha en otro puerto no ocupa la caja. `m1.espera` sí la ocupa aunque no haya DayZ, y nunca caduca solo.
- Ocupada: `dayz_test_run(..., on_busy="queue")` y espera; no sondees huecos.
- Nunca cierres ni mates lo ajeno, ni borres `m1.espera`, ni preguntes a Guillermo si cerrarlo.
- Clasifica por `reason` y `queue_offer`, no solo por `active_run_exists`. `port_in_use_foreign` con `queue_offer` nulo es un bloqueo terminal. El reencolado de la contención no cuenta como intento fallido, y el tiempo total está acotado a 1800 s.
Detalle: la sección «La caja está ocupada» del runbook.
