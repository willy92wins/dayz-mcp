from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = PROJECT_ROOT / "test-contracts" / "task9-protocol-docs"
RUNBOOK_PATH = Path(
    r"C:\Users\guill\ObsidianVault\AI\20_Runbooks\dayz-mcp-agent-session-protocol.md"
)

L1_BODY = """1. Adquirir lease antes de mutar o gestionar procesos.
2. Liberarlo en cuanto termine la secuencia exclusiva.
3. No matar procesos DayZ directamente; usar el lifecycle guard.
4. Ejecutar `session_status` antes del handoff y documentar cierres degradados.
Runbook: `C:\\Users\\guill\\ObsidianVault\\AI\\20_Runbooks\\dayz-mcp-agent-session-protocol.md`."""

DEPLOYMENT_PAIRS = (
    (
        "runbook",
        CONTRACT_ROOT / "dayz-mcp-agent-session-protocol.md",
        RUNBOOK_PATH,
    ),
    (
        "dayz-mcp-verify skill",
        CONTRACT_ROOT / "skills" / "dayz-mcp-verify" / "SKILL.md",
        Path.home() / ".claude" / "skills" / "dayz-mcp-verify" / "SKILL.md",
    ),
    (
        "dayz-test-ingame skill",
        CONTRACT_ROOT / "skills" / "dayz-test-ingame" / "SKILL.md",
        Path.home() / ".claude" / "skills" / "dayz-test-ingame" / "SKILL.md",
    ),
    (
        "dayz-test.ps1 template",
        CONTRACT_ROOT
        / "skills"
        / "dayz-test-ingame"
        / "templates"
        / "dayz-test.ps1",
        Path.home()
        / ".claude"
        / "skills"
        / "dayz-test-ingame"
        / "templates"
        / "dayz-test.ps1",
    ),
)

EXTERNAL_L1_DESTINATIONS = (
    Path(r"C:\Users\guill\ObsidianVault\AI\00_System\workflow.md"),
    Path.home() / ".claude" / "CLAUDE.md",
    Path.home() / ".codex" / "AGENTS.md",
)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def main() -> int:
    failures: list[str] = []

    for label, expected_path, deployed_path in DEPLOYMENT_PAIRS:
        if not expected_path.is_file():
            failures.append(f"{label}: repository contract missing: {expected_path}")
            continue
        if not deployed_path.is_file():
            failures.append(f"{label}: deployed file missing: {deployed_path}")
            continue
        expected = expected_path.read_bytes()
        deployed = deployed_path.read_bytes()
        if deployed != expected:
            failures.append(
                f"{label}: deployment drift: repo={_digest(expected)} "
                f"deployed={_digest(deployed)} path={deployed_path}"
            )

    for path in EXTERNAL_L1_DESTINATIONS:
        if not path.is_file():
            failures.append(f"L1 destination missing: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        count = text.count(L1_BODY)
        if count != 1:
            failures.append(f"L1 block count={count}, expected=1: {path}")
        heading_count = text.count("DayZ MCP \u2014 sesi\u00f3n compartida")
        if heading_count != 1:
            failures.append(
                f"L1 heading count={heading_count}, expected=1: {path}"
            )

    if failures:
        print("TASK9 DEPLOYMENT DRIFT")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("TASK9 DEPLOYMENT OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
