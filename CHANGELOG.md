# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- 54 typed MCP tools across world, player, object, vehicle, camera, UI, telemetry, lifecycle, and session coordination.
- Engine-native, server-authoritative control and structured observation over a loopback HTTP push/pull bridge.
- Embedded, daemon, and client modes; one daemon can serve multiple agent sessions through FIFO leases.
- Managed lifecycle tools for building, launching, inspecting, and stopping DayZ test runs.
- Structured runtime diagnostics: field- and unit-aware `bad_args`, evidence-rich `wait_for` results, readiness causes from `bridge_status`, and the read-only `python -m dayz_mcp.doctor`.
- Vehicle entry, control, telemetry, and trace tools; the Group G in-game drivability verdict is pending.
- Camera capture, UI inspection and interaction, and live `.layout` reload tools.
- `dayz_mcp.effective_schema`: resolves the tool contract FastMCP publishes after `build_app`, aliases applied, and audits it against the prose each description promises.

### Security

- Loopback-only access with API-key authentication, process accreditation, FIFO leases, and a JSONL audit trail.
- No operating-system keystroke injection, OCR, paid-service dependency, usage analytics, or integrated knowledge database.
