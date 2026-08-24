from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dayz_mcp import doctor


def clean_status() -> dict[str, object]:
    return {
        "daemon_generation": "generation",
        "coordination": {
            "revision": 1,
            "captured_at_monotonic": 1000.0,
            "active": None,
            "releasing": None,
            "granting": None,
            "handoff_pending": False,
            "claimable": True,
            "audit_fault": None,
            "operation_tombstones": {
                "count": 0,
                "capacity": 128,
                "saturated": False,
            },
            "queue": [],
            "cleanup_workers": {"capacity": 4, "active": 0, "saturated": 0},
        },
    }


class DoctorKnowledgePackPairingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.scan_root = self.root / "scan"
        self.scan_root.mkdir()
        self.pack = self.root / "knowledge-pack"
        self.manifest = self.root / "knowledge-pack-skills-manifest.json"

    @staticmethod
    def codes(payload: dict[str, object]) -> list[str]:
        return [item["code"] for item in payload["findings"]]

    def sources(self, game_version: str | None = None) -> doctor.DoctorSources:
        self.assertIn(
            "knowledge_pack_dir",
            doctor.DoctorSources.__dataclass_fields__,
            "doctor does not accept the Knowledge Pack source",
        )
        self.assertIn(
            "knowledge_pack_manifest_path",
            doctor.DoctorSources.__dataclass_fields__,
            "doctor does not accept the Knowledge Pack manifest source",
        )
        version_args = (
            ["--expected-game-version", game_version] if game_version is not None else []
        )
        common_args = [
            "-m",
            "dayz_mcp",
            "--client",
            "--keyfile",
            r"C:\DayZ MCP\shared.key",
            "--port",
            "8765",
            *version_args,
        ]
        claude = "\n".join(
            (
                "dayz-mcp:",
                "  Type: stdio",
                r"  Command: C:\Python\python.exe",
                "  Args: " + " ".join(common_args + ["--client-platform", "claude"]),
            )
        )
        codex = json.dumps(
            {
                "name": "dayz-mcp",
                "transport": {
                    "type": "stdio",
                    "command": r"C:\Python\python.exe",
                    "args": common_args + ["--client-platform", "codex"],
                },
            }
        )
        daemon_argv = [
            r"C:\Python\python.exe",
            "-m",
            "dayz_mcp",
            "--daemon",
            "--port",
            "8765",
            "--keyfile",
            r"C:\DayZ MCP\shared.key",
            "--idle-timeout",
            "1800.0",
            *version_args,
        ]
        return doctor.DoctorSources(
            claude_config=lambda: (0, claude),
            codex_config=lambda: (0, codex),
            listener_pid=lambda port: 700 if port == 8765 else None,
            process_argv=lambda pid: daemon_argv if pid == 700 else None,
            daemon_status=lambda _port, _keyfile: clean_status(),
            process_snapshot=lambda _names: {"known": True, "processes": []},
            process_identity=lambda _pid: {
                "error": "process_not_found",
                "exit_code": 4,
            },
            runtime_paths=doctor.RuntimePaths(
                self.runtime,
                self.runtime / "audit",
                self.runtime / "coordination.json",
                self.runtime / "runs.json",
            ),
            scan_roots=(self.scan_root,),
            expected_command=r"C:\Python\python.exe",
            knowledge_pack_dir=self.pack,
            knowledge_pack_manifest_path=self.manifest,
        )

    def write_pack(self, build: str = "1.29.0.163451") -> None:
        (self.pack / "skills").mkdir(parents=True)
        (self.pack / "compatibility-matrix.md").write_text(
            f"# Matrix\nTarget stable build: **DayZ PC {build}** (released)\n",
            encoding="utf-8",
        )

    def test_missing_pack_is_warn_with_exact_installer_remedy(self) -> None:
        payload, exit_code = doctor.execute(self.sources())

        finding = next(
            item
            for item in payload["findings"]
            if item["code"] == "KNOWLEDGE_PACK_MISSING"
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(finding["severity"], "WARN")
        self.assertEqual(finding["path"], str(self.pack.resolve()))
        self.assertEqual(
            finding["remedy"], "python -m dayz_mcp.knowledge_pack install"
        )

    def test_pack_game_drift_warns_when_doctor_knows_the_game_version(self) -> None:
        self.write_pack()
        self.manifest.write_text("{}\n", encoding="utf-8")

        payload, exit_code = doctor.execute(self.sources("1.29.0.163709"))

        finding = next(
            item
            for item in payload["findings"]
            if item["code"] == "KNOWLEDGE_PACK_GAME_DRIFT"
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(finding["severity"], "WARN")
        self.assertEqual(finding["pack_build"], "1.29.0.163451")
        self.assertEqual(finding["game_build"], "1.29.0.163709")

    def test_present_pack_without_manifest_is_info(self) -> None:
        self.write_pack()

        payload, exit_code = doctor.execute(self.sources())

        finding = next(
            item
            for item in payload["findings"]
            if item["code"] == "PACK_SKILLS_UNREGISTERED"
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(finding["severity"], "INFO")
        self.assertEqual(finding["manifest_path"], str(self.manifest.resolve()))

    def test_game_drift_is_silent_without_a_known_game_version(self) -> None:
        self.write_pack()
        self.manifest.write_text("{}\n", encoding="utf-8")

        payload, exit_code = doctor.execute(self.sources())

        self.assertEqual(exit_code, 0)
        self.assertNotIn("KNOWLEDGE_PACK_GAME_DRIFT", self.codes(payload))


if __name__ == "__main__":
    unittest.main()
