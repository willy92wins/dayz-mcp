# Comparison with dayz-agentic-modding-mcp

This is a factual comparison with [covalschi/dayz-agentic-modding-mcp](https://github.com/covalschi/dayz-agentic-modding-mcp). The projects make different trade-offs around transport, scope, and bundled tooling.

| Area | DayZ-MCP | dayz-agentic-modding-mcp |
|---|---|---|
| Transport | Typed HTTP push/pull on `127.0.0.1`. | JSON file mailbox with state published once per second. |
| Security | API key, process accreditation, FIFO leases, and JSONL audit. | No authentication. |
| Vehicles | Enter, drive, telemetry, and trace. The Group G in-game drivability verdict is pending. | No vehicle verbs. |
| Tool surface | 100% typed. | About 12 verbs plus untyped `world_exec`. |
| Multiple sessions | One daemon serves N clients. | One session per process. |
| Additional strengths | The typed live-game surface described above. | Direct build and signing with FileBank; a SQLite index of 131,000 declarations; a ViGEmBus virtual gamepad; and an integrated `.blend`-to-PAA asset pipeline. |
| Evidence culture | Documents measured limits. | Documents measured limits. |

## When to use which

**DayZ-MCP:** use it when typed loopback commands, authentication, leases, audit, and one daemon shared by multiple clients matter.
If vehicle driving is central to the workflow, the Group G in-game drivability verdict is still pending.

**dayz-agentic-modding-mcp:** use it when direct build and signing, the SQLite declaration index, the ViGEmBus gamepad, or the integrated asset pipeline are the priority.
It fits deployments that accept unauthenticated file-mailbox transport and one session per process.
