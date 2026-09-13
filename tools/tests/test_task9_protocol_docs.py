from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = PROJECT_ROOT / "test-contracts" / "task9-protocol-docs"
RUNBOOK = CONTRACT_ROOT / "dayz-mcp-agent-session-protocol.md"
RUNBOOK_TEXT = r"C:\Users\guill\ObsidianVault\AI\20_Runbooks\dayz-mcp-agent-session-protocol.md"
VERIFY_SKILL = CONTRACT_ROOT / "skills" / "dayz-mcp-verify" / "SKILL.md"
INGAME_SKILL = CONTRACT_ROOT / "skills" / "dayz-test-ingame" / "SKILL.md"

L1_BODY = """1. Adquirir lease antes de mutar o gestionar procesos.
2. Liberarlo en cuanto termine la secuencia exclusiva.
3. No matar procesos DayZ directamente; usar el lifecycle guard.
4. Ejecutar `session_status` antes del handoff y documentar cierres degradados.
Runbook: `C:\\Users\\guill\\ObsidianVault\\AI\\20_Runbooks\\dayz-mcp-agent-session-protocol.md`."""


class Task9ProtocolDocsTests(unittest.TestCase):
    def read_required(self, path: Path) -> str:
        if not path.is_file():
            self.skipTest(f"{path.name} not present (public clone)")
        return path.read_text(encoding="utf-8")

    def assert_contains_all(self, text: str, snippets: tuple[str, ...], source: Path) -> None:
        folded = text.casefold()
        for snippet in snippets:
            with self.subTest(source=str(source), snippet=snippet):
                self.assertIn(snippet.casefold(), folded)

    def test_required_new_documents_exist(self) -> None:
        agents = PROJECT_ROOT / "AGENTS.md"
        if not RUNBOOK.is_file() or not agents.is_file():
            self.skipTest("task9 protocol docs not present (public clone)")
        self.assertTrue(RUNBOOK.is_file(), f"canonical runbook is missing: {RUNBOOK}")
        self.assertTrue(agents.is_file(), "project AGENTS.md is missing")

    def test_l1_block_is_exactly_once_in_project_documents(self) -> None:
        destinations = (
            PROJECT_ROOT / "CLAUDE.md",
            PROJECT_ROOT / "AGENTS.md",
        )
        for path in destinations:
            with self.subTest(path=str(path)):
                text = self.read_required(path)
                self.assertEqual(text.count(L1_BODY), 1)
                self.assertEqual(text.count("DayZ MCP \u2014 sesi\u00f3n compartida"), 1)

    def test_runbook_contains_the_complete_l2_contract(self) -> None:
        text = self.read_required(RUNBOOK)
        self.assert_contains_all(
            text,
            (
                "lecturas puras",
                "mutaciones",
                "lifecycle",
                "session_acquire",
                "session_wait",
                "30 s",
                "FIFO",
                "120 s",
                "session_heartbeat",
                "DAYZ_MCP_CLIENT_ID_JSON",
                "DAYZ_MCP_LEASE_TOKEN",
                "argv",
                "run_id",
                "mismo mod no concede ownership",
                "session_release",
                "TTY",
                "session_status",
                "doctor",
                "cierre degradado",
                "shell del mismo usuario",
                "cuarentena retail",
                "manual_cleanup_required",
                "UI",
                "presencia retail",
            ),
            RUNBOOK,
        )
        closure = """1. `session_release` si existe lease propio.
2. `session_status`: `own_lease=none`, `own_ticket=none`, `pending_commands=0`.
3. Confirmar `vehicle_control=inactive` o deadman observado.
4. Declarar el run como `RUNNING_IDLE`, `EXITED` o `UNRECONCILED`.
5. Solo escribir HANDOFF si hubo cambio durable, incidente o cleanup degradado."""
        self.assertIn(closure, text)

    def test_verify_skill_links_and_enforces_the_session_protocol(self) -> None:
        text = self.read_required(VERIFY_SKILL)
        self.assert_contains_all(
            text,
            (
                RUNBOOK_TEXT,
                "session_acquire",
                "session_wait",
                "session_heartbeat",
                "session_release",
                "session_status",
                "run_id",
                "post-mutation",
                "pre-handoff",
                "cuarentena retail",
                "manual_cleanup_required",
            ),
            VERIFY_SKILL,
        )

    def test_ingame_skill_links_and_enforces_exact_run_lifecycle(self) -> None:
        text = self.read_required(INGAME_SKILL)
        self.assert_contains_all(
            text,
            (
                RUNBOOK_TEXT,
                "Diag-only",
                "run_id",
                "mismo mod no concede ownership",
                "DAYZ_MCP_CLIENT_ID_JSON",
                "DAYZ_MCP_LEASE_TOKEN",
                "cuarentena retail",
                "manual_cleanup_required",
                "UI",
            ),
            INGAME_SKILL,
        )

    def test_skills_contain_no_executable_direct_kill_or_mod_ownership_advice(self) -> None:
        forbidden = (
            re.compile(r"Get-Process[^\r\n|]*\|\s*Stop-Process", re.IGNORECASE),
            re.compile(
                r"dayz-test\.ps1(?![^\r\n]*-RunId)[^\r\n]*\s-Kill(?:\s|`|$)",
                re.IGNORECASE,
            ),
            re.compile(r"kill-stuck-dayz\.bat", re.IGNORECASE),
            re.compile(r"mata\s+DayZDiag\s+residuales", re.IGNORECASE),
            re.compile(r"cmdline[^\r\n]*@<Mod>", re.IGNORECASE),
            re.compile(r"holder[^\r\n]*@<Mod>[^\r\n]*profiles", re.IGNORECASE),
        )
        for path in (VERIFY_SKILL, INGAME_SKILL):
            text = self.read_required(path)
            for pattern in forbidden:
                with self.subTest(path=str(path), pattern=pattern.pattern):
                    self.assertIsNone(pattern.search(text))

    def test_ingame_skill_does_not_offer_an_official_retail_launcher(self) -> None:
        text = self.read_required(INGAME_SKILL)
        forbidden = (
            re.compile(r"dayz-test\.ps1[^\r\n]*-Retail", re.IGNORECASE),
            re.compile(r"(?:relaunch|relaunching|switch)\s+(?:with|to)\s+`?-Retail", re.IGNORECASE),
            re.compile(r"template[^\r\n]*(?:-Retail|\$RetailExe|DayZ_BE\.exe|DayZServer_x64\.exe)", re.IGNORECASE),
            re.compile(
                r"\b(?:launch|relaunch|start|invoke)\b[^\r\n]*DayZ_BE\.exe",
                re.IGNORECASE,
            ),
        )
        for pattern in forbidden:
            with self.subTest(pattern=pattern.pattern):
                self.assertIsNone(pattern.search(text))
        self.assert_contains_all(
            text,
            (
                "retail manual-only",
                "launcher oficial es Diag-only",
                "cuarentena retail",
            ),
            INGAME_SKILL,
        )

    def test_retail_history_is_descriptive_and_contains_no_agent_procedure(self) -> None:
        text = self.read_required(INGAME_SKILL)
        match = re.search(
            r"^## RETAIL EXTERNO MANUAL PARA MODSETS DE TERCEROS.*?"
            r"(?P<section>.*?)^## USAGE\s*$",
            text,
            re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "retail-history section is missing")
        section = match.group("section") if match is not None else ""
        forbidden = (
            re.compile(r"\.\s*Use\s+for\s+LBmaster\b", re.IGNORECASE),
            re.compile(r"[\u2014-]\s*poll\s+`Get-NetUDPEndpoint\b", re.IGNORECASE),
            re.compile(r"\bcheck\s+the\s+mtimes\b", re.IGNORECASE),
            re.compile(r"^\s*-\s+NEVER\s+remove/rename\b", re.IGNORECASE | re.MULTILINE),
            re.compile(r"^\s*-\s+\*\*Never\s+drop\b", re.IGNORECASE | re.MULTILINE),
            re.compile(r";\s*keep\s+LBmaster\b", re.IGNORECASE),
            re.compile(r"\bcan\s+be\s+staged\s+with\s+\*\*hardlinks\*\*", re.IGNORECASE),
            re.compile(r"`mklink\s+/H`", re.IGNORECASE),
        )
        for pattern in forbidden:
            with self.subTest(pattern=pattern.pattern):
                self.assertIsNone(pattern.search(section))

        self.assertIsNone(
            re.search(
                r"^\|\s*Retail server binds[^\r\n]*\|[^\r\n]*\brestore\s+the\s+dll\b",
                text,
                re.IGNORECASE | re.MULTILINE,
            )
        )

    def test_skills_reject_unmanaged_diag_launch_bypasses(self) -> None:
        cases = (
            (
                VERIFY_SKILL,
                re.compile(r"lanzar[^\r\n]*`cmd start`[^\r\n]*`\.bat`", re.IGNORECASE),
            ),
            (
                INGAME_SKILL,
                re.compile(r"Start-Process\s+\$Diag\b", re.IGNORECASE),
            ),
        )
        for path, pattern in cases:
            text = self.read_required(path)
            with self.subTest(path=str(path), pattern=pattern.pattern):
                self.assertIsNone(pattern.search(text))

    def test_execution_matrix_separates_diag_server_dedicated_and_retail(self) -> None:
        sources = (RUNBOOK, INGAME_SKILL)
        required_rows = (
            re.compile(
                r"^\|[^\r\n]*`DayZDiag_x64\.exe`[^\r\n]*`-server`[^\r\n]*`managed_lifecycle=true`[^\r\n]*\|$",
                re.IGNORECASE | re.MULTILINE,
            ),
            re.compile(
                r"^\|[^\r\n]*`DayZServer_x64\.exe`[^\r\n]*`managed_lifecycle=false`[^\r\n]*probe-gated[^\r\n]*\|$",
                re.IGNORECASE | re.MULTILINE,
            ),
            re.compile(
                r"^\|[^\r\n]*retail manual externo[^\r\n]*cuarentena[^\r\n]*\|$",
                re.IGNORECASE | re.MULTILINE,
            ),
        )
        for path in sources:
            text = self.read_required(path)
            for pattern in required_rows:
                with self.subTest(path=str(path), pattern=pattern.pattern):
                    self.assertRegex(text, pattern)


if __name__ == "__main__":
    unittest.main()
