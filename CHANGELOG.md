# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

No unreleased product changes. Current published release is [v1.2](https://github.com/willy92wins/dayz-mcp/releases/tag/v1.2). The product is paused.

## [1.2] - 2026-09-12

### Added

- 62 typed MCP tools across world, player, object, vehicle, camera, UI, telemetry, lifecycle, knowledge, and session coordination. Count pinned to the instantiated app (`tools/tests/test_install_mcp.py::PublicToolCountDocsTest`) and `README.md`. `exec_enforce` is extra and opt-in when an allowlist is configured.
- Engine-native, server-authoritative control and structured observation over a loopback HTTP push/pull bridge.
- Embedded, daemon, and client modes; one daemon can serve multiple agent sessions through FIFO leases.
- Managed lifecycle tools for building, launching, inspecting, and stopping DayZ test runs.
- Structured runtime diagnostics: field- and unit-aware `bad_args`, evidence-rich `wait_for` results, readiness causes from `bridge_status`, and the read-only `python -m dayz_mcp.doctor`.
- Vehicle entry, control, telemetry, and trace tools. Group G in-game drivability criteria in `product-spec.md` remain ❓; they are parked with the v1.2 pause, not an Unreleased promise.
- Camera capture, UI inspection and interaction, and live `.layout` reload tools.
- `dayz_mcp.effective_schema`: resolves the tool contract FastMCP publishes after `build_app`, aliases applied, and audits it against the prose each description promises.

### Changed

- `dayz_test_run` / `dayz_test_stop`: a failure inside the native launcher backend now names its bare code after the class, `dayz_test_failed:NativeLauncherBackendError:<code>`; host detail still stays in the local log (ficha ae65, step 1).
- `entities_query` keeps `entities: []` on an empty result: result pruning no longer drops the key its description promises (ficha 59d9).

### Security

- Loopback-only access with API-key authentication, process accreditation, FIFO leases, and a JSONL audit trail.
- No operating-system keystroke injection, OCR, paid-service dependency, usage analytics, or integrated knowledge database.
