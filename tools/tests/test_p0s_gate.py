from __future__ import annotations

import json
import io
import os
import struct
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from install_mcp import (
    InstallerContractError,
    installer_cli_manifest_path,
    load_installer_cli_manifest,
)
from p0s_gate import (
    capture_installer_not_found_fixtures,
    create_installer_cli_manifest,
    host_cli_provenance,
    main,
)
from dayz_mcp.runtime_state import RuntimePaths


def write_fake_x64_pe(path: Path) -> None:
    payload = bytearray(512)
    payload[0:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", payload, 0x84, 0x8664)
    struct.pack_into("<H", payload, 0x94, 0xF0)
    struct.pack_into("<H", payload, 0x98, 0x20B)
    path.write_bytes(payload)


class InstallerCliManifestProducerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.claude = self.root / "claude.exe"
        self.codex = self.root / "codex.exe"
        self.output = self.root / "reports" / "installer-cli-manifest-v1.json"
        write_fake_x64_pe(self.claude)
        write_fake_x64_pe(self.codex)

    @staticmethod
    def allow_provenance(_role: str, _path: Path) -> bool:
        return True

    @staticmethod
    def allow_signature(_path: Path) -> bool:
        return True

    def test_create_only_manifest_round_trips_through_installer_consumer(self) -> None:
        created = create_installer_cli_manifest(
            self.output,
            {"CLAUDE": self.claude, "CODEX": self.codex},
            provenance_checker=self.allow_provenance,
            signature_checker=self.allow_signature,
        )

        self.assertEqual(created, self.output)
        payload = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(
            set(payload),
            {"schema_version", "kind", "entries"},
        )
        self.assertEqual(set(payload["entries"]), {"CLAUDE", "CODEX"})
        manifest = load_installer_cli_manifest(self.output)
        self.assertEqual(manifest.entries["CLAUDE"].path, self.claude)
        self.assertEqual(manifest.entries["CODEX"].path, self.codex)

    def test_existing_output_is_never_overwritten(self) -> None:
        self.output.parent.mkdir(parents=True)
        self.output.write_text("sentinel", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            create_installer_cli_manifest(
                self.output,
                {"CLAUDE": self.claude, "CODEX": self.codex},
                provenance_checker=self.allow_provenance,
                signature_checker=self.allow_signature,
            )

        self.assertEqual(self.output.read_text(encoding="utf-8"), "sentinel")

    def test_provenance_or_signature_failure_happens_before_publish(self) -> None:
        checks = (
            (lambda _role, _path: False, self.allow_signature),
            (self.allow_provenance, lambda _path: False),
        )
        for provenance, signature in checks:
            with self.subTest(check=(provenance, signature)):
                with self.assertRaises(InstallerContractError):
                    create_installer_cli_manifest(
                        self.output,
                        {"CLAUDE": self.claude, "CODEX": self.codex},
                        provenance_checker=provenance,
                        signature_checker=signature,
                    )
                self.assertFalse(self.output.exists())

    def test_host_provenance_rejects_pe_renamed_outside_official_roots(self) -> None:
        self.assertFalse(host_cli_provenance("CLAUDE", self.claude))
        self.assertFalse(host_cli_provenance("CODEX", self.codex))

    def _create_fake_manifest(self) -> None:
        create_installer_cli_manifest(
            self.output,
            {"CLAUDE": self.claude, "CODEX": self.codex},
            provenance_checker=self.allow_provenance,
            signature_checker=self.allow_signature,
        )

    def test_not_found_probe_is_bounded_create_only_and_manifest_bound(self) -> None:
        self._create_fake_manifest()
        fixture_path = self.root / "reports" / "installer-not-found-fixtures-v1.json"
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_runner(argv: list[str], **kwargs: object) -> object:
            calls.append((list(argv), dict(kwargs)))
            role = Path(argv[0]).stem.upper()
            return type(
                "Completed",
                (),
                {
                    "returncode": 7 if role == "CLAUDE" else 8,
                    "stdout": f"{role}-absent\n",
                    "stderr": "",
                },
            )()

        capture_installer_not_found_fixtures(
            fixture_path,
            manifest_path=self.output,
            runner=fake_runner,
        )

        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["probe_name"], "p0s-absent-fixture-do-not-create")
        self.assertEqual(set(payload["entries"]), {"CLAUDE", "CODEX"})
        self.assertEqual(payload["entries"]["CLAUDE"]["returncode"], 7)
        self.assertEqual(payload["entries"]["CODEX"]["returncode"], 8)
        self.assertTrue(all(call[1]["shell"] is False for call in calls))
        self.assertEqual(
            calls[0][0][1:],
            ["mcp", "get", "p0s-absent-fixture-do-not-create"],
        )
        self.assertEqual(
            calls[1][0][1:],
            ["mcp", "get", "p0s-absent-fixture-do-not-create", "--json"],
        )

    def test_not_found_probe_rejects_zero_or_oversized_output_before_publish(self) -> None:
        self._create_fake_manifest()
        fixture_path = self.root / "reports" / "installer-not-found-fixtures-v1.json"

        for completed in (
            type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
            type(
                "Completed",
                (),
                {"returncode": 2, "stdout": "x" * 4097, "stderr": ""},
            )(),
        ):
            with self.subTest(completed=completed):
                with self.assertRaises(InstallerContractError):
                    capture_installer_not_found_fixtures(
                        fixture_path,
                        manifest_path=self.output,
                        runner=lambda _argv, **_kwargs: completed,
                    )
                self.assertFalse(fixture_path.exists())

    def test_default_not_found_probe_follows_security_dir_env(self) -> None:
        security = self.root / "security"
        security.mkdir()
        manifest = security / "installer-cli-manifest-v1.json"
        create_installer_cli_manifest(
            manifest,
            {"CLAUDE": self.claude, "CODEX": self.codex},
            provenance_checker=self.allow_provenance,
            signature_checker=self.allow_signature,
        )
        fixture_path = security / "installer-not-found-fixtures-v1.json"
        calls: list[list[str]] = []

        def fake_runner(argv: list[str], **_kwargs: object) -> object:
            calls.append(list(argv))
            role = Path(argv[0]).stem.upper()
            return type(
                "Completed",
                (),
                {
                    "returncode": 7 if role == "CLAUDE" else 8,
                    "stdout": f"{role}-absent\n",
                    "stderr": "",
                },
            )()

        with patch.dict(os.environ, {"DAYZ_MCP_SECURITY_DIR": str(security)}):
            self.assertEqual(installer_cli_manifest_path(), manifest)
            capture_installer_not_found_fixtures(
                fixture_path,
                runner=fake_runner,
            )

        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["probe_name"], "p0s-absent-fixture-do-not-create")
        self.assertEqual(
            calls[0][1:],
            ["mcp", "get", "p0s-absent-fixture-do-not-create"],
        )
        self.assertEqual(
            calls[1][1:],
            ["mcp", "get", "p0s-absent-fixture-do-not-create", "--json"],
        )

    def test_not_found_probe_timeout_is_typed_contract_error(self) -> None:
        self._create_fake_manifest()
        fixture_path = self.root / "reports" / "installer-not-found-fixtures-v1.json"

        def timeout_runner(_argv: list[str], **_kwargs: object) -> object:
            raise subprocess.TimeoutExpired(cmd="claude.exe", timeout=30.0)

        with self.assertRaises(InstallerContractError) as raised:
            capture_installer_not_found_fixtures(
                fixture_path,
                manifest_path=self.output,
                runner=timeout_runner,
            )
        self.assertEqual(raised.exception.code, "installer_cli_probe_timeout")
        self.assertFalse(fixture_path.exists())


class RunsBackupCliGateTest(unittest.TestCase):
    def test_backup_runs_v1_uses_runtime_path_and_explicit_port(self) -> None:
        paths = RuntimePaths(
            Path(r"C:\runtime"),
            Path(r"C:\runtime\audit"),
            Path(r"C:\runtime\coordination.json"),
            Path(r"C:\runtime\runs.json"),
        )
        output = io.StringIO()
        with (
            patch.object(RuntimePaths, "from_env", return_value=paths),
            patch(
                "dayz_mcp.identity_migration.ensure_runs_v1_backup",
                return_value={"source_absent": False},
            ) as backup,
            redirect_stdout(output),
        ):
            result = main(["backup-runs-v1", "--port", "8765"])

        self.assertEqual(result, 0)
        backup.assert_called_once_with(paths, 8765)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"source_absent": False, "status": "verified"},
        )


if __name__ == "__main__":
    unittest.main()
