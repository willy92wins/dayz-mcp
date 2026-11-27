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
- Clasifica por `error_code`, `reason` y `queue_offer`. Solo reencola con oferta FIFO accionable (`retry.on_busy="queue"`). `port_in_use_foreign`, `box_reserved` u `own_run` con `queue_offer` nulo son terminales. El tope de 1800 s es estricto: `wait_for_box_s=min(600, remaining)`; si remaining es 0, para (no pases 0).
Detalle: la sección «La caja está ocupada» del runbook.
