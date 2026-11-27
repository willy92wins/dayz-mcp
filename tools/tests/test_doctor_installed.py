from __future__ import annotations

import hashlib
import io
import json
import os
import shlex
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import bridge_readiness, doctor, setup_cmd
from dayz_mcp.daemon_policy import AccreditedDaemonPolicy
from dayz_mcp.host_config import HostConfigError
from dayz_mcp.server_cli import reset_instance_context_for_tests


CLAUDE_GOOD = """dayz-mcp:
  Type: stdio
  Command: C:\\Python\\python.exe
  Args: -m dayz_mcp --client --keyfile C:\\DayZ MCP\\shared.key --port 8765 --client-platform claude
"""


def _codex_good() -> str:
    return json.dumps(
        {
            "name": "dayz-mcp",
            "transport": {
                "type": "stdio",
                "command": "C:\\Python\\python.exe",
                "args": [
                    "-m",
                    "dayz_mcp",
                    "--client",
                    "--keyfile",
                    "C:\\DayZ MCP\\shared.key",
                    "--port",
                    "8765",
                    "--client-platform",
                    "codex",
                ],
            },
        }
    )


CODEX_GOOD = _codex_good()
DAEMON_GOOD = (
    '"C:\\Python\\python.exe" -m dayz_mcp --daemon --port 8765 '
    '--keyfile "C:\\DayZ MCP\\shared.key" --idle-timeout 1800.0'
)
ITEM_REMEDY = "subscribe to the DayZ-MCP item in the Steam Workshop, let Steam download it, then run dayz-mcp setup --json again; or pass --item PATH"
WHEEL_REMEDY = "reinstall the dayz-mcp wheel of this release, then run dayz-mcp setup --json again"
ITEM_VERSION_REMEDY = "let Steam download the DayZ-MCP Workshop item of this release, then run dayz-mcp setup --json again"
SETUP_REMEDY = "dayz-mcp setup --json"


def daemon_argv(command_line: str) -> list[str]:
    return [
        value[1:-1]
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}
        else value
        for value in shlex.split(command_line, posix=False)
    ]


def clean_status(captured: float = 1000.0) -> dict[str, object]:
    return {
        "daemon_generation": "generation",
        "coordination": {
            "revision": 1,
            "captured_at_monotonic": captured,
            "active": None,
            "releasing": None,
            "granting": None,
            "handoff_pending": False,
            "claimable": True,
            "audit_fault": None,
            "operation_tombstones": {"count": 0, "capacity": 128, "saturated": False},
            "queue": [],
            "cleanup_workers": {"capacity": 4, "active": 0, "saturated": 0},
        },
    }


def accredited_policy(keyfile: Path) -> AccreditedDaemonPolicy:
    authority = {
        "argv": [
            r"P:\Runtime\python.exe",
            "-m",
            "dayz_mcp",
            "--daemon",
            "--port",
            "8765",
        ],
        "cwd": r"P:\DayZ_MCP_dev\tools",
        "host": "127.0.0.1",
        "keyfile": str(keyfile),
        "kind": "normal",
        "native_executable": r"P:\Runtime\python.exe",
        "port": 8765,
        "security_build_id": None,
    }
    authority_sha256 = hashlib.sha256(
        json.dumps(
            authority, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    return AccreditedDaemonPolicy(
        kind="normal",
        host="127.0.0.1",
        port=8765,
        keyfile=str(keyfile),
        native_executable=r"P:\Runtime\python.exe",
        argv=tuple(authority["argv"]),
        cwd=r"P:\DayZ_MCP_dev\tools",
        security_build_id=None,
        authority_sha256=authority_sha256,
    )


class _FakeRuntimePaths:
    @staticmethod
    def for_token(token: object) -> Path:
        del token
        return Path("runtime-paths")


def _installed_doctor_file(base: Path) -> Path:
    root = base / "venv"
    package = root / "Lib" / "site-packages" / "dayz_mcp"
    package.mkdir(parents=True)
    module = package / "doctor.py"
    module.write_text("# doctor\n", encoding="utf-8")
    (root / "pyvenv.cfg").write_text("home = python\n", encoding="utf-8")
    scripts = root / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"python")
    return module


def _default_sources(doctor_file: str, item: object = None):
    fake_guard = SimpleNamespace(snapshot=lambda pid: {})
    with TemporaryDirectory() as temporary:
        policy = accredited_policy(Path(temporary) / "shared.key")
        with mock.patch.object(
            doctor, "__file__", doctor_file
        ), mock.patch.object(
            doctor, "NativeProcessGuard", return_value=fake_guard
        ), mock.patch.object(
            doctor, "resolve_pack_dir", return_value=None
        ), mock.patch.object(
            doctor, "default_manifest_path", return_value=None
        ), mock.patch.object(
            doctor, "RuntimePaths", _FakeRuntimePaths
        ):
            if item is None:
                return doctor.default_sources(policy)
            return doctor.default_sources(policy, item=item)  # type: ignore[arg-type]


class DoctorDefaultSourcesInstalledTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_instance_context_for_tests()
        self.addCleanup(reset_instance_context_for_tests)
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.security = self.base / "security"
        self.security.mkdir()
        self.cli_dir = self.base / "clis"
        self.cli_dir.mkdir()
        self.claude_exe = self.cli_dir / "claude.exe"
        self.codex_exe = self.cli_dir / "codex.exe"
        self.claude_exe.write_bytes(b"claude")
        self.codex_exe.write_bytes(b"codex")
        self.doctor_file = _installed_doctor_file(self.base)

    def _write_manifest(self, claude_path: object, codex_path: object) -> None:
        payload = {
            "entries": {
                "CLAUDE": {"path": claude_path},
                "CODEX": {"path": codex_path},
            }
        }
        (self.security / "installer-cli-manifest-v1.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def _sources(self, item: object = None):
        env = {"DAYZ_MCP_SECURITY_DIR": str(self.security)}
        with mock.patch.dict(os.environ, env, clear=True):
            return _default_sources(str(self.doctor_file), item)

    def _calls(self, sources: object) -> list[list[str]]:
        calls: list[list[str]] = []

        def fake_run(argv: list[str]) -> tuple[int, str]:
            calls.append(list(argv))
            return 0, ""

        with mock.patch.object(doctor, "_run_command", fake_run):
            sources.claude_config()  # type: ignore[union-attr]
            sources.codex_config()  # type: ignore[union-attr]
        return calls

    def test_pinned_clis_are_used_with_the_exact_argv(self) -> None:
        self._write_manifest(str(self.claude_exe), str(self.codex_exe))
        sentinel_item = self.base / "workshop-item"
        with mock.patch.object(
            setup_cmd, "workshop_item_root", return_value=sentinel_item
        ) as workshop:
            sources = self._sources()
        self.assertEqual(
            self._calls(sources),
            [
                [str(self.claude_exe), "mcp", "get", "dayz-mcp"],
                [str(self.codex_exe), "mcp", "get", "dayz-mcp", "--json"],
            ],
        )
        workshop.assert_called_once_with()
        self.assertTrue(sources.installed)
        self.assertEqual(sources.wheel_version_path, setup_cmd._wheel_version_path())
        self.assertEqual(sources.item_root, sentinel_item)

    def test_item_argument_wins_over_the_workshop_lookup(self) -> None:
        self._write_manifest(str(self.claude_exe), str(self.codex_exe))
        asked = self.base / "asked-item"
        with mock.patch.object(setup_cmd, "workshop_item_root") as workshop:
            sources = self._sources(item=str(asked))
        workshop.assert_not_called()
        self.assertEqual(sources.item_root, asked)
        self.assertTrue(sources.installed)

    def test_without_a_manifest_both_probes_are_none(self) -> None:
        sources = self._sources()
        self.assertIsNone(sources.claude_config)
        self.assertIsNone(sources.codex_config)
        self.assertTrue(sources.installed)
        self.assertEqual(sources.wheel_version_path, setup_cmd._wheel_version_path())

    def test_a_bad_codex_path_leaves_only_the_codex_probe_none(self) -> None:
        variants = {
            "relative": "codex.exe",
            "other_name": str(self.cli_dir / "other.exe"),
            "missing_file": str(self.cli_dir / "missing" / "codex.exe"),
        }
        (self.cli_dir / "other.exe").write_bytes(b"other")
        for name, codex_path in variants.items():
            with self.subTest(name=name):
                self._write_manifest(str(self.claude_exe), codex_path)
                sources = self._sources()
                self.assertIsNotNone(sources.claude_config)
                self.assertIsNone(sources.codex_config)

    def test_relative_codex_path_is_none_even_when_cwd_has_codex(self) -> None:
        self._write_manifest(str(self.claude_exe), "codex.exe")
        previous_cwd = os.getcwd()
        os.chdir(self.cli_dir)
        self.addCleanup(os.chdir, previous_cwd)
        sources = self._sources()
        self.assertIsNotNone(sources.claude_config)
        self.assertIsNone(sources.codex_config)

    def test_manifest_that_is_not_json_leaves_both_probes_none(self) -> None:
        (self.security / "installer-cli-manifest-v1.json").write_text(
            "{not-json", encoding="utf-8"
        )
        sources = self._sources()
        self.assertIsNone(sources.claude_config)
        self.assertIsNone(sources.codex_config)

    def test_doctor_sources_defaults_are_not_installed(self) -> None:
        sources = doctor.DoctorSources(
            claude_config=None,
            codex_config=None,
            listener_pid=lambda port: None,
            process_argv=lambda pid: None,
            daemon_status=lambda port, keyfile: {},
            process_snapshot=lambda names: {"known": True, "processes": []},
            process_identity=lambda pid: {},
            runtime_paths=doctor.RuntimePaths(
                self.base / "runtime",
                self.base / "runtime" / "audit",
                self.base / "runtime" / "coordination.json",
                self.base / "runtime" / "runs.json",
            ),
            scan_roots=(),
            expected_command="python",
        )
        self.assertFalse(sources.installed)
        self.assertIsNone(sources.item_root)
        self.assertIsNone(sources.wheel_version_path)

    def test_checkout_sources_keep_todays_argv(self) -> None:
        calls: list[list[str]] = []

        def fake_run(argv: list[str]) -> tuple[int, str]:
            calls.append(list(argv))
            return 0, ""

        with mock.patch.object(doctor.shutil, "which", return_value="codex.cmd"):
            sources = _default_sources(doctor.__file__)
        with mock.patch.object(doctor, "_run_command", fake_run):
            sources.claude_config()  # type: ignore[union-attr]
            sources.codex_config()  # type: ignore[union-attr]
        self.assertEqual(
            calls,
            [
                ["claude", "mcp", "get", "dayz-mcp"],
                ["codex.cmd", "mcp", "get", "dayz-mcp", "--json"],
            ],
        )
        self.assertFalse(sources.installed)
        self.assertIsNone(sources.item_root)
        self.assertIsNone(sources.wheel_version_path)

    def test_claude_only_manifest_without_codex_config_sets_codex_not_expected(self) -> None:
        home = self.base / "claude-only-home"
        home.mkdir()
        payload = {
            "entries": {
                "CLAUDE": {"path": str(self.claude_exe)},
                "CODEX": None,
            }
        }
        (self.security / "installer-cli-manifest-v1.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        env = {
            "DAYZ_MCP_SECURITY_DIR": str(self.security),
            "USERPROFILE": str(home),
            "HOME": str(home),
        }
        with mock.patch.dict(os.environ, env, clear=True):
            sources = _default_sources(str(self.doctor_file))
        self.assertIsNotNone(sources.claude_config)
        self.assertIsNone(sources.codex_config)
        self.assertFalse(sources.codex_expected)

    def test_claude_only_manifest_with_codex_config_keeps_codex_expected(self) -> None:
        home = self.base / "codex-file-home"
        (home / ".codex").mkdir(parents=True)
        (home / ".codex" / "config.toml").write_text("", encoding="utf-8")
        payload = {
            "entries": {
                "CLAUDE": {"path": str(self.claude_exe)},
                "CODEX": None,
            }
        }
        (self.security / "installer-cli-manifest-v1.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        env = {
            "DAYZ_MCP_SECURITY_DIR": str(self.security),
            "USERPROFILE": str(home),
            "HOME": str(home),
        }
        with mock.patch.dict(os.environ, env, clear=True):
            sources = _default_sources(str(self.doctor_file))
        self.assertIsNotNone(sources.claude_config)
        self.assertIsNone(sources.codex_config)
        self.assertTrue(sources.codex_expected)

    def test_checkout_sources_without_codex_cli_or_config_sets_codex_not_expected(self) -> None:
        home = self.base / "checkout-claude-only-home"
        home.mkdir()
        env = {"USERPROFILE": str(home), "HOME": str(home)}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(doctor.shutil, "which", return_value=None),
        ):
            sources = _default_sources(doctor.__file__)
        self.assertIsNotNone(sources.claude_config)
        self.assertIsNone(sources.codex_config)
        self.assertFalse(sources.codex_expected)


class DoctorInstalledDiagnoseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.item = self.root / "item"
        (self.item / "Addons").mkdir(parents=True)
        self.pbo_bytes = b"fake-pbo-bytes"
        (self.item / "Addons" / "DayZ_MCP.pbo").write_bytes(self.pbo_bytes)
        self.pbo_sha = hashlib.sha256(self.pbo_bytes).hexdigest()
        self.wheel_version_file = self.root / "wheel_VERSION.json"
        self.write_wheel_version("1.6", self.pbo_sha)
        self.write_item_version(self.item, "1.6", self.pbo_sha)
        patcher = mock.patch.object(setup_cmd, "WORKSHOP_ITEM_ID", "")
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_wheel_version(self, version: object, sha: object) -> None:
        self.wheel_version_file.write_text(
            json.dumps({"version": version, "pbo_sha256": sha}), encoding="utf-8"
        )

    def write_item_version(self, item: Path, version: object, sha: object) -> None:
        (item / "VERSION.json").write_text(
            json.dumps({"version": version, "pbo_sha256": sha}), encoding="utf-8"
        )

    @staticmethod
    def daemon_ok() -> dict[str, object]:
        return {
            "code": "DAEMON_STATUS_OK",
            "severity": "INFO",
            "port": 8765,
            "pid": 700,
        }

    def sources(self, **overrides: object):
        def snapshot(names: list[str]) -> dict[str, object]:
            del names
            return {"known": True, "processes": []}

        values: dict[str, object] = {
            "claude_config": lambda: (0, CLAUDE_GOOD),
            "codex_config": lambda: (0, CODEX_GOOD),
            "listener_pid": lambda port: 700 if port == 8765 else None,
            "process_argv": lambda pid: daemon_argv(DAEMON_GOOD) if pid == 700 else None,
            "daemon_status": lambda port, keyfile: clean_status(),
            "process_snapshot": snapshot,
            "process_identity": lambda pid: {
                "error": "process_not_found",
                "exit_code": 4,
            },
            "runtime_paths": doctor.RuntimePaths(
                self.runtime,
                self.runtime / "audit",
                self.runtime / "coordination.json",
                self.runtime / "runs.json",
            ),
            "scan_roots": (),
            "expected_command": "C:\\Python\\python.exe",
            "installed": True,
            "item_root": self.item,
            "wheel_version_path": self.wheel_version_file,
        }
        values.update(overrides)
        return doctor.DoctorSources(**values)  # type: ignore[arg-type]

    def execute(self, **overrides: object):
        return doctor.execute(self.sources(**overrides))

    def test_no_listener_is_info_and_exit_zero_when_installed(self) -> None:
        payload, exit_code = self.execute(listener_pid=lambda port: None)
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "ok": True,
                "findings": [
                    {"code": "DAEMON_NOT_RUNNING", "severity": "INFO", "port": 8765}
                ],
                "summary": {"fail": 0, "warn": 0},
            },
        )

    def test_no_listener_is_unreadable_when_not_installed(self) -> None:
        payload, exit_code = self.execute(
            listener_pid=lambda port: None, installed=False
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "findings": [
                    {
                        "code": "DAEMON_STATUS_UNREADABLE",
                        "severity": "FAIL",
                        "scope": "listener",
                        "port": 8765,
                    }
                ],
                "summary": {"fail": 1, "warn": 0},
            },
        )

    def test_a_none_probe_is_cli_not_pinned(self) -> None:
        for platform, override in (
            ("claude", {"claude_config": None}),
            ("codex", {"codex_config": None}),
        ):
            with self.subTest(platform=platform):
                payload, exit_code = self.execute(**override)
                self.assertEqual(exit_code, 1)
                self.assertEqual(
                    payload,
                    {
                        "ok": False,
                        "findings": [
                            {
                                "code": "CLI_NOT_PINNED",
                                "severity": "FAIL",
                                "platform": platform,
                                "remedy": SETUP_REMEDY,
                            },
                            self.daemon_ok(),
                        ],
                        "summary": {"fail": 1, "warn": 0},
                    },
                )

    def test_equal_halves_add_nothing(self) -> None:
        payload, exit_code = self.execute()
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "ok": True,
                "findings": [self.daemon_ok()],
                "summary": {"fail": 0, "warn": 0},
            },
        )

    def test_claude_only_without_codex_expected_is_clean(self) -> None:
        payload, exit_code = self.execute(codex_config=None, codex_expected=False)
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "ok": True,
                "findings": [self.daemon_ok()],
                "summary": {"fail": 0, "warn": 0},
            },
        )

    def _halves_payload(self, finding: dict[str, object]) -> dict[str, object]:
        return {
            "ok": False,
            "findings": [self.daemon_ok(), finding],
            "summary": {"fail": 1, "warn": 0},
        }

    def test_halves_mismatch_item_ahead(self) -> None:
        self.write_item_version(self.item, "1.7", self.pbo_sha)
        self.write_wheel_version("1.6", "0" * 64)
        payload, exit_code = self.execute()
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "HALVES_MISMATCH",
                    "severity": "FAIL",
                    "direction": "item_ahead",
                    "item_version": "1.7",
                    "wheel_version": "1.6",
                    "remedy": "close the game, install the dayz-mcp wheel of release 1.7 and run dayz-mcp setup --json, then restart the MCP client and start the game",
                }
            ),
        )

    def test_halves_mismatch_item_behind(self) -> None:
        self.write_item_version(self.item, "1.5", self.pbo_sha)
        self.write_wheel_version("1.6", "0" * 64)
        payload, exit_code = self.execute()
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "HALVES_MISMATCH",
                    "severity": "FAIL",
                    "direction": "item_behind",
                    "item_version": "1.5",
                    "wheel_version": "1.6",
                    "remedy": "close the game, let Steam update the DayZ-MCP Workshop item, then restart the MCP client and start the game",
                }
            ),
        )

    def test_halves_mismatch_differs(self) -> None:
        self.write_wheel_version("1.6", "0" * 64)
        payload, exit_code = self.execute()
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "HALVES_MISMATCH",
                    "severity": "FAIL",
                    "direction": "differs",
                    "item_version": "1.6",
                    "wheel_version": "1.6",
                    "remedy": "close the game, install the dayz-mcp wheel and the Workshop item of the same release and run dayz-mcp setup --json, then restart the MCP client and start the game",
                }
            ),
        )

    def test_workshop_item_missing_without_an_item_root(self) -> None:
        payload, exit_code = self.execute(item_root=None)
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "WORKSHOP_ITEM_MISSING",
                    "severity": "FAIL",
                    "remedy": ITEM_REMEDY,
                }
            ),
        )

    def test_workshop_item_missing_with_an_item_root_without_a_pbo(self) -> None:
        bare = self.root / "bare-item"
        bare.mkdir()
        self.write_item_version(bare, "1.6", self.pbo_sha)
        payload, exit_code = self.execute(item_root=bare)
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "WORKSHOP_ITEM_MISSING",
                    "severity": "FAIL",
                    "path": str(bare),
                    "remedy": ITEM_REMEDY,
                }
            ),
        )

    def test_workshop_item_missing_carries_the_url(self) -> None:
        with mock.patch.object(setup_cmd, "WORKSHOP_ITEM_ID", "123"):
            payload, exit_code = self.execute(item_root=None)
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "WORKSHOP_ITEM_MISSING",
                    "severity": "FAIL",
                    "remedy": ITEM_REMEDY,
                    "url": "https://steamcommunity.com/sharedfiles/filedetails/?id=123",
                }
            ),
        )

    def test_version_json_missing_wheel(self) -> None:
        self.wheel_version_file.unlink()
        payload, exit_code = self.execute()
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "VERSION_JSON_MISSING",
                    "severity": "FAIL",
                    "which": "wheel",
                    "path": str(self.wheel_version_file),
                    "remedy": WHEEL_REMEDY,
                }
            ),
        )

    def test_version_json_missing_wheel_without_a_path(self) -> None:
        payload, exit_code = self.execute(wheel_version_path=None)
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "VERSION_JSON_MISSING",
                    "severity": "FAIL",
                    "which": "wheel",
                    "remedy": WHEEL_REMEDY,
                }
            ),
        )

    def test_version_json_invalid_wheel(self) -> None:
        self.wheel_version_file.write_text("[]", encoding="utf-8")
        payload, exit_code = self.execute()
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "VERSION_JSON_INVALID",
                    "severity": "FAIL",
                    "which": "wheel",
                    "path": str(self.wheel_version_file),
                    "remedy": WHEEL_REMEDY,
                }
            ),
        )

    def test_version_json_missing_item(self) -> None:
        (self.item / "VERSION.json").unlink()
        payload, exit_code = self.execute()
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "VERSION_JSON_MISSING",
                    "severity": "FAIL",
                    "which": "item",
                    "path": str(self.item / "VERSION.json"),
                    "remedy": ITEM_VERSION_REMEDY,
                }
            ),
        )

    def test_version_json_invalid_item(self) -> None:
        (self.item / "VERSION.json").write_text("[]", encoding="utf-8")
        payload, exit_code = self.execute()
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "VERSION_JSON_INVALID",
                    "severity": "FAIL",
                    "which": "item",
                    "path": str(self.item / "VERSION.json"),
                    "remedy": ITEM_VERSION_REMEDY,
                }
            ),
        )

    def test_workshop_item_unreadable(self) -> None:
        pbo_path = self.item / "Addons" / "DayZ_MCP.pbo"
        real_read_bytes = Path.read_bytes

        def fake_read_bytes(path: Path, *args: object, **kwargs: object) -> bytes:
            if path == pbo_path:
                raise PermissionError("the PBO is locked")
            return real_read_bytes(path, *args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(Path, "read_bytes", fake_read_bytes):
            payload, exit_code = self.execute()
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "WORKSHOP_ITEM_UNREADABLE",
                    "severity": "FAIL",
                    "path": str(pbo_path),
                    "remedy": "close whatever holds DayZ_MCP.pbo open (Steam may still be updating the item), then run dayz-mcp setup --json again",
                }
            ),
        )

    def _status_for_reason(self, reason: str) -> dict[str, object]:
        status = clean_status()
        if reason == "arg_contract_mismatch":
            status["server_peer"] = {
                "last_poll_age_s": 0.1,
                "version_state": "ok",
                "capabilities": {
                    "state": "announced",
                    "announced_arg_contract_hash": "0" * 16,
                },
            }
            status["client_peer"] = {"last_poll_age_s": 0.1, "version_state": "ok"}
        else:
            status["server_peer"] = {
                "last_poll_age_s": 0.1,
                "version_state": "version_mismatch",
                "capabilities": {"state": "match"},
            }
            status["client_peer"] = {"last_poll_age_s": 0.1, "version_state": "ok"}
        self.assertEqual(bridge_readiness.compute_bridge_ready(status)["reason"], reason)
        return status

    def test_arg_contract_reason_rides_a_distinct_halves_mismatch(self) -> None:
        status = self._status_for_reason("arg_contract_mismatch")
        self.write_wheel_version("1.6", "0" * 64)
        payload, exit_code = self.execute(
            daemon_status=lambda port, keyfile: status
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "HALVES_MISMATCH",
                    "severity": "FAIL",
                    "direction": "differs",
                    "item_version": "1.6",
                    "wheel_version": "1.6",
                    "game_reason": "arg_contract_mismatch",
                    "remedy": "close the game, install the dayz-mcp wheel and the Workshop item of the same release and run dayz-mcp setup --json, then restart the MCP client and start the game",
                }
            ),
        )

    def test_arg_contract_reason_alone_is_a_game_differs_mismatch(self) -> None:
        status = self._status_for_reason("arg_contract_mismatch")
        payload, exit_code = self.execute(
            daemon_status=lambda port, keyfile: status
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "HALVES_MISMATCH",
                    "severity": "FAIL",
                    "direction": "game_differs",
                    "game_reason": "arg_contract_mismatch",
                    "remedy": "close the game and start it again with the -mod= and -profiles= arguments that dayz-mcp setup --json prints",
                }
            ),
        )

    def test_version_mismatch_reason_rides_a_distinct_halves_mismatch(self) -> None:
        status = self._status_for_reason("version_mismatch")
        self.write_item_version(self.item, "1.5", self.pbo_sha)
        self.write_wheel_version("1.6", "0" * 64)
        payload, exit_code = self.execute(
            daemon_status=lambda port, keyfile: status
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "HALVES_MISMATCH",
                    "severity": "FAIL",
                    "direction": "item_behind",
                    "item_version": "1.5",
                    "wheel_version": "1.6",
                    "game_reason": "version_mismatch",
                    "remedy": "close the game, let Steam update the DayZ-MCP Workshop item, then restart the MCP client and start the game",
                }
            ),
        )

    def test_clean_status_no_run_adds_no_game_reason(self) -> None:
        status = clean_status()
        self.assertEqual(
            bridge_readiness.compute_bridge_ready(status)["reason"], "no_run"
        )
        self.write_wheel_version("1.6", "0" * 64)
        payload, exit_code = self.execute(
            daemon_status=lambda port, keyfile: status
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            self._halves_payload(
                {
                    "code": "HALVES_MISMATCH",
                    "severity": "FAIL",
                    "direction": "differs",
                    "item_version": "1.6",
                    "wheel_version": "1.6",
                    "remedy": "close the game, install the dayz-mcp wheel and the Workshop item of the same release and run dayz-mcp setup --json, then restart the MCP client and start the game",
                }
            ),
        )

    def test_not_installed_skips_the_halves_step(self) -> None:
        self.write_wheel_version("1.6", "0" * 64)
        payload, exit_code = self.execute(installed=False)
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "ok": True,
                "findings": [self.daemon_ok()],
                "summary": {"fail": 0, "warn": 0},
            },
        )

    def test_execute_forwards_item_to_default_sources(self) -> None:
        policy = accredited_policy(self.root / "shared.key")
        asked_item = str(self.root / "asked-item")
        payload_ok: dict[str, object] = {
            "ok": True,
            "findings": [],
            "summary": {"fail": 0, "warn": 0},
        }
        with mock.patch.object(
            doctor, "default_sources", return_value=object()
        ) as default_sources_mock, mock.patch.object(
            doctor, "_diagnose", return_value=payload_ok
        ):
            payload, exit_code = doctor.execute(policy=policy, item=asked_item)
        default_sources_mock.assert_called_once_with(policy, item=asked_item)
        self.assertEqual(payload, payload_ok)
        self.assertEqual(exit_code, 0)

    def test_halves_failure_reports_the_halves_check(self) -> None:
        with mock.patch.object(
            doctor, "_check_halves", side_effect=RuntimeError("boom")
        ):
            payload, exit_code = self.execute()
        self.assertEqual(exit_code, 2)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "error": "diagnostic_failure",
                "failed_check": "halves",
                "exception_class": "RuntimeError",
                "findings": [],
                "summary": {"fail": 0, "warn": 0},
            },
        )

    @staticmethod
    def _dayz_processes_snapshot(names: list[str]) -> dict[str, object]:
        wanted = set(names)
        running = [
            {"pid": 9372, "name": "DayZServer_x64.exe"},
            {"pid": 9373, "name": "DayZDiag_x64.exe"},
            {"pid": 49040, "name": "DayZ_x64.exe"},
            {"pid": 49041, "name": "DayZ_BE.exe"},
        ]
        return {
            "known": True,
            "processes": [item for item in running if item["name"] in wanted],
        }

    def test_installed_ignores_running_dayz_server_and_client(self) -> None:
        payload, exit_code = self.execute(
            process_snapshot=self._dayz_processes_snapshot
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "ok": True,
                "findings": [
                    self.daemon_ok(),
                    {
                        "code": "RETAIL_MANUAL_CLOSE_REQUIRED",
                        "severity": "WARN",
                        "processes": [
                            {"pid": 49040, "name": "DayZ_x64.exe"},
                            {"pid": 49041, "name": "DayZ_BE.exe"},
                        ],
                    },
                ],
                "summary": {"fail": 0, "warn": 1},
            },
        )
        self.assertNotIn(
            "PROCESS_UNREGISTERED",
            [item["code"] for item in payload["findings"]],
        )

    def test_installed_doctor_retail_names_match_the_daemon_probe(self) -> None:
        from dayz_mcp import orphan_guard

        self.assertEqual(
            list(doctor._RETAIL_NAMES),
            list(orphan_guard.RETAIL_PROCESS_NAMES),
        )
        self.assertEqual(
            list(orphan_guard.RETAIL_PROCESS_NAMES),
            ["DayZ_BE.exe", "DayZ_x64.exe"],
        )

    def test_installed_with_require_clean_fails_on_retail_quarantine(
        self,
    ) -> None:
        payload, exit_code = doctor.execute(
            self.sources(process_snapshot=self._dayz_processes_snapshot),
            require_clean=True,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "findings": [
                    self.daemon_ok(),
                    {
                        "code": "RETAIL_MANUAL_CLOSE_REQUIRED",
                        "severity": "FAIL",
                        "processes": [
                            {"pid": 49040, "name": "DayZ_x64.exe"},
                            {"pid": 49041, "name": "DayZ_BE.exe"},
                        ],
                    },
                ],
                "summary": {"fail": 1, "warn": 0},
            },
        )
        self.assertNotIn(
            "PROCESS_UNREGISTERED",
            [item["code"] for item in payload["findings"]],
        )

    def test_not_installed_reports_running_dayz_server_and_client(self) -> None:
        payload, exit_code = self.execute(
            installed=False,
            process_snapshot=self._dayz_processes_snapshot,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "findings": [
                    self.daemon_ok(),
                    {
                        "code": "PROCESS_UNREGISTERED",
                        "severity": "FAIL",
                        "pid": 9372,
                        "name": "DayZServer_x64.exe",
                    },
                    {
                        "code": "PROCESS_UNREGISTERED",
                        "severity": "FAIL",
                        "pid": 9373,
                        "name": "DayZDiag_x64.exe",
                    },
                    {
                        "code": "RETAIL_MANUAL_CLOSE_REQUIRED",
                        "severity": "WARN",
                        "processes": [
                            {"pid": 49040, "name": "DayZ_x64.exe"},
                            {"pid": 49041, "name": "DayZ_BE.exe"},
                        ],
                    },
                ],
                "summary": {"fail": 2, "warn": 1},
            },
        )

    def test_not_installed_with_require_clean_marks_retail_as_fail(self) -> None:
        payload, exit_code = doctor.execute(
            self.sources(
                installed=False,
                process_snapshot=self._dayz_processes_snapshot,
            ),
            require_clean=True,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "findings": [
                    self.daemon_ok(),
                    {
                        "code": "PROCESS_UNREGISTERED",
                        "severity": "FAIL",
                        "pid": 9372,
                        "name": "DayZServer_x64.exe",
                    },
                    {
                        "code": "PROCESS_UNREGISTERED",
                        "severity": "FAIL",
                        "pid": 9373,
                        "name": "DayZDiag_x64.exe",
                    },
                    {
                        "code": "RETAIL_MANUAL_CLOSE_REQUIRED",
                        "severity": "FAIL",
                        "processes": [
                            {"pid": 49040, "name": "DayZ_x64.exe"},
                            {"pid": 49041, "name": "DayZ_BE.exe"},
                        ],
                    },
                ],
                "summary": {"fail": 3, "warn": 0},
            },
        )

    def test_installed_still_reports_stale_run_without_process_unregistered(
        self,
    ) -> None:
        record = {
            "pid": 900,
            "creation_time_utc": "2026-07-15T00:00:00.0000000Z",
            "executable_sha256": "a" * 64,
            "command_line_sha256": "b" * 64,
            "role": "client",
            "identity_scheme": "psutil-argv-v2",
        }
        run = {
            "run_id": "run-1",
            "owner_session_id": None,
            "owner_lease_id": None,
            "state": "RUNNING_IDLE",
            "label": "fixture",
            "mod": "@Fixture",
            "profiles": "profiles",
            "mission": "mission",
            "processes": [record],
        }
        (self.runtime / "runs.json").write_text(
            json.dumps({"version": 1, "runs": [run]}), encoding="utf-8"
        )
        payload, exit_code = self.execute(
            process_snapshot=self._dayz_processes_snapshot
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "findings": [
                    self.daemon_ok(),
                    {
                        "code": "RETAIL_MANUAL_CLOSE_REQUIRED",
                        "severity": "WARN",
                        "processes": [
                            {"pid": 49040, "name": "DayZ_x64.exe"},
                            {"pid": 49041, "name": "DayZ_BE.exe"},
                        ],
                    },
                    {
                        "code": "RUN_STALE",
                        "severity": "FAIL",
                        "pid": 900,
                        "run_id": "run-1",
                    },
                ],
                "summary": {"fail": 1, "warn": 1},
            },
        )
        self.assertNotIn(
            "PROCESS_UNREGISTERED",
            [item["code"] for item in payload["findings"]],
        )

    def test_installed_still_reports_process_scan_failed(self) -> None:
        payload, exit_code = self.execute(
            process_snapshot=lambda names: {"known": False, "processes": []}
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "findings": [
                    self.daemon_ok(),
                    {
                        "code": "PROCESS_SCAN_FAILED",
                        "severity": "FAIL",
                        "scope": "retail",
                    },
                    {
                        "code": "PROCESS_SCAN_FAILED",
                        "severity": "FAIL",
                        "scope": "managed",
                    },
                    {
                        "code": "PROCESS_SCAN_FAILED",
                        "severity": "FAIL",
                        "scope": "crash_reporter",
                    },
                ],
                "summary": {"fail": 3, "warn": 0},
            },
        )


@unittest.skipUnless(sys.platform == "win32", "requires Windows")
class DoctorInstalledMainTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_instance_context_for_tests()
        self.addCleanup(reset_instance_context_for_tests)
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.home = self.base / "home"
        (self.home / ".codex").mkdir(parents=True)
        self.item = self.base / "item"
        (self.item / "Addons").mkdir(parents=True)
        pbo_bytes = b"fake-pbo-bytes"
        (self.item / "Addons" / "DayZ_MCP.pbo").write_bytes(pbo_bytes)
        pbo_sha = hashlib.sha256(pbo_bytes).hexdigest()
        version_text = json.dumps({"version": "1.6", "pbo_sha256": pbo_sha})
        (self.item / "VERSION.json").write_text(version_text, encoding="utf-8")
        self.wheel_version_file = self.base / "wheel_VERSION.json"
        self.wheel_version_file.write_text(version_text, encoding="utf-8")
        self.other_python = self.base / "other-venv" / "Scripts" / "python.exe"
        self.other_python.parent.mkdir(parents=True)
        self.other_python.write_bytes(b"python")
        self.missing_python = self.base / "missing" / "python.exe"
        self.workshop_root: Path | None = self.item
        patcher = mock.patch.object(setup_cmd, "WORKSHOP_ITEM_ID", "")
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_client_files(
        self, claude: dict[str, str], codex: dict[str, str]
    ) -> None:
        claude_payload = {
            "mcpServers": {
                name: {"command": command} for name, command in claude.items()
            }
        }
        (self.home / ".claude.json").write_text(
            json.dumps(claude_payload), encoding="utf-8"
        )
        lines: list[str] = []
        for name, command in codex.items():
            lines.append(f"[mcp_servers.{name}]")
            lines.append(f"command = {json.dumps(command)}")
            lines.append("")
        (self.home / ".codex" / "config.toml").write_text(
            "\n".join(lines), encoding="utf-8"
        )

    @staticmethod
    def elsewhere(platform: str, command: str) -> dict[str, object]:
        return {
            "code": "REGISTRATION_POINTS_ELSEWHERE",
            "severity": "FAIL",
            "platform": platform,
            "command": command,
            "remedy": SETUP_REMEDY,
        }

    @staticmethod
    def diagnostic_failure(
        exception_class: str, exception_code: str | None = None
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": False,
            "error": "diagnostic_failure",
            "failed_check": "daemon_policy",
            "exception_class": exception_class,
            "findings": [],
            "summary": {"fail": 0, "warn": 0},
        }
        if exception_code is not None:
            payload["exception_code"] = exception_code
        return payload

    def run_doctor(
        self,
        extra: list[str] | None = None,
        *,
        installed: bool = True,
        error: BaseException | None = None,
    ) -> tuple[int, dict[str, object], str]:
        raised = (
            HostConfigError("daemon_provenance_conflict") if error is None else error
        )
        buffer = io.StringIO()
        patches = [
            mock.patch.object(
                doctor.daemon_policy, "load_daemon_policy", side_effect=raised
            ),
            mock.patch.object(
                setup_cmd, "_wheel_version_path", return_value=self.wheel_version_file
            ),
            mock.patch.object(
                setup_cmd, "workshop_item_root", return_value=self.workshop_root
            ),
            mock.patch.dict(
                os.environ, {"USERPROFILE": str(self.home)}, clear=True
            ),
        ]
        if installed:
            patches.append(
                mock.patch.object(
                    doctor,
                    "installed_venv_root",
                    return_value=self.base / "venv",
                )
            )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        argv = ["--daemon-policy", "normal", "--json"] + ([] if extra is None else extra)
        with redirect_stdout(buffer):
            code = doctor.main(argv)
        text = buffer.getvalue()
        return code, json.loads(text), text

    def assert_exact(
        self,
        result: tuple[int, dict[str, object], str],
        expected_code: int,
        expected: dict[str, object],
    ) -> None:
        code, payload, text = result
        self.assertEqual(code, expected_code)
        self.assertEqual(payload, expected)
        self.assertEqual(text, doctor.render_json(expected) + "\n")

    def test_both_registrations_pointing_elsewhere_to_an_existing_python(self) -> None:
        command = str(self.other_python)
        self.write_client_files({"dayz-mcp": command}, {"dayz-mcp": command})
        self.assert_exact(
            self.run_doctor(),
            1,
            {
                "ok": False,
                "findings": [
                    self.elsewhere("claude", command),
                    self.elsewhere("codex", command),
                ],
                "summary": {"fail": 2, "warn": 0},
            },
        )

    def test_elsewhere_without_an_item_adds_the_workshop_finding(self) -> None:
        command = str(self.other_python)
        self.workshop_root = None
        self.write_client_files({"dayz-mcp": command}, {"dayz-mcp": command})
        self.assert_exact(
            self.run_doctor(),
            1,
            {
                "ok": False,
                "findings": [
                    self.elsewhere("claude", command),
                    self.elsewhere("codex", command),
                    {
                        "code": "WORKSHOP_ITEM_MISSING",
                        "severity": "FAIL",
                        "remedy": ITEM_REMEDY,
                    },
                ],
                "summary": {"fail": 3, "warn": 0},
            },
        )

    def test_both_registrations_pointing_to_a_missing_python(self) -> None:
        command = str(self.missing_python)
        self.write_client_files({"dayz-mcp": command}, {"dayz-mcp": command})
        self.assert_exact(
            self.run_doctor(),
            1,
            {
                "ok": False,
                "findings": [
                    self.elsewhere("claude", command),
                    self.elsewhere("codex", command),
                ],
                "summary": {"fail": 2, "warn": 0},
            },
        )

    def test_only_the_codex_registration_pointing_elsewhere(self) -> None:
        command = str(self.other_python)
        self.write_client_files(
            {"dayz-mcp": sys.executable}, {"dayz-mcp": command}
        )
        self.assert_exact(
            self.run_doctor(),
            1,
            {
                "ok": False,
                "findings": [self.elsewhere("codex", command)],
                "summary": {"fail": 1, "warn": 0},
            },
        )

    def test_an_instance_reads_its_own_registration_name(self) -> None:
        command = str(self.other_python)
        entries = {"dayz-mcp": sys.executable, "dayz-mcp-x": command}
        self.write_client_files(entries, entries)
        self.assert_exact(
            self.run_doctor(["--instance", "x"]),
            1,
            {
                "ok": False,
                "findings": [
                    self.elsewhere("claude", command),
                    self.elsewhere("codex", command),
                ],
                "summary": {"fail": 2, "warn": 0},
            },
        )

    def test_registrations_pointing_here_keep_the_diagnostic_failure(self) -> None:
        self.write_client_files(
            {"dayz-mcp": sys.executable}, {"dayz-mcp": sys.executable}
        )
        self.assert_exact(
            self.run_doctor(),
            2,
            self.diagnostic_failure("HostConfigError", "daemon_provenance_conflict"),
        )

    def test_missing_registrations_keep_the_diagnostic_failure(self) -> None:
        self.write_client_files({}, {})
        self.assert_exact(
            self.run_doctor(),
            2,
            self.diagnostic_failure("HostConfigError", "daemon_provenance_conflict"),
        )

    def test_another_policy_error_keeps_the_diagnostic_failure(self) -> None:
        command = str(self.other_python)
        self.write_client_files({"dayz-mcp": command}, {"dayz-mcp": command})
        self.assert_exact(
            self.run_doctor(error=ValueError("boom")),
            2,
            self.diagnostic_failure("ValueError"),
        )

    def test_from_a_clone_the_same_files_keep_the_diagnostic_failure(self) -> None:
        command = str(self.other_python)
        self.write_client_files({"dayz-mcp": command}, {"dayz-mcp": command})
        self.assert_exact(
            self.run_doctor(installed=False),
            2,
            self.diagnostic_failure("HostConfigError", "daemon_provenance_conflict"),
        )

    def test_main_forwards_item_to_execute(self) -> None:
        sentinel_policy = object()
        payload_ok: dict[str, object] = {
            "ok": True,
            "findings": [],
            "summary": {"fail": 0, "warn": 0},
        }
        asked_item = str(self.base / "asked-item")
        buffer = io.StringIO()
        with mock.patch.dict(
            os.environ, {"USERPROFILE": str(self.home)}, clear=True
        ), mock.patch.object(
            doctor.daemon_policy,
            "load_daemon_policy",
            return_value=sentinel_policy,
        ), mock.patch.object(
            doctor, "execute", return_value=(payload_ok, 0)
        ) as execute_mock, redirect_stdout(buffer):
            code = doctor.main(
                ["--daemon-policy", "normal", "--json", "--item", asked_item]
            )
        execute_mock.assert_called_once_with(
            require_clean=False, policy=sentinel_policy, item=asked_item
        )
        self.assertEqual(code, 0)
        self.assertEqual(buffer.getvalue(), doctor.render_json(payload_ok) + "\n")

    def test_main_without_item_does_not_forward_item_to_execute(self) -> None:
        sentinel_policy = object()
        payload_ok: dict[str, object] = {
            "ok": True,
            "findings": [],
            "summary": {"fail": 0, "warn": 0},
        }
        buffer = io.StringIO()
        with mock.patch.dict(
            os.environ, {"USERPROFILE": str(self.home)}, clear=True
        ), mock.patch.object(
            doctor.daemon_policy,
            "load_daemon_policy",
            return_value=sentinel_policy,
        ), mock.patch.object(
            doctor, "execute", return_value=(payload_ok, 0)
        ) as execute_mock, redirect_stdout(buffer):
            code = doctor.main(["--daemon-policy", "normal", "--json"])
        execute_mock.assert_called_once_with(
            require_clean=False, policy=sentinel_policy
        )
        self.assertEqual(code, 0)
        self.assertEqual(buffer.getvalue(), doctor.render_json(payload_ok) + "\n")

    def test_elsewhere_with_an_item_argument_uses_that_empty_item(self) -> None:
        command = str(self.other_python)
        empty_item = self.base / "empty-item"
        empty_item.mkdir()
        self.write_client_files({"dayz-mcp": command}, {"dayz-mcp": command})
        self.assert_exact(
            self.run_doctor(["--item", str(empty_item)]),
            1,
            {
                "ok": False,
                "findings": [
                    self.elsewhere("claude", command),
                    self.elsewhere("codex", command),
                    {
                        "code": "VERSION_JSON_MISSING",
                        "severity": "FAIL",
                        "which": "item",
                        "path": str(empty_item / "VERSION.json"),
                        "remedy": ITEM_VERSION_REMEDY,
                    },
                    {
                        "code": "WORKSHOP_ITEM_MISSING",
                        "severity": "FAIL",
                        "path": str(empty_item),
                        "remedy": ITEM_REMEDY,
                    },
                ],
                "summary": {"fail": 4, "warn": 0},
            },
        )

    def test_invalid_claude_json_leaves_only_the_codex_elsewhere(self) -> None:
        command = str(self.other_python)
        self.write_client_files(
            {"dayz-mcp": sys.executable}, {"dayz-mcp": command}
        )
        (self.home / ".claude.json").write_text("{not-json", encoding="utf-8")
        self.assert_exact(
            self.run_doctor(),
            1,
            {
                "ok": False,
                "findings": [self.elsewhere("codex", command)],
                "summary": {"fail": 1, "warn": 0},
            },
        )

    def test_non_string_claude_command_leaves_only_the_codex_elsewhere(
        self,
    ) -> None:
        command = str(self.other_python)
        self.write_client_files(
            {"dayz-mcp": sys.executable}, {"dayz-mcp": command}
        )
        claude_payload = {"mcpServers": {"dayz-mcp": {"command": 123}}}
        (self.home / ".claude.json").write_text(
            json.dumps(claude_payload), encoding="utf-8"
        )
        self.assert_exact(
            self.run_doctor(),
            1,
            {
                "ok": False,
                "findings": [self.elsewhere("codex", command)],
                "summary": {"fail": 1, "warn": 0},
            },
        )

    def test_unresolvable_codex_command_is_elsewhere(self) -> None:
        command = str(self.other_python)
        self.write_client_files(
            {"dayz-mcp": sys.executable}, {"dayz-mcp": command}
        )
        real_resolve = Path.resolve
        command_key = os.path.normcase(os.path.normpath(command))

        def fake_resolve(path: Path, *args: object, **kwargs: object) -> Path:
            if os.path.normcase(os.path.normpath(str(path))) == command_key:
                raise OSError("cannot resolve the registration command")
            return real_resolve(path, *args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(Path, "resolve", fake_resolve):
            result = self.run_doctor()
        self.assert_exact(
            result,
            1,
            {
                "ok": False,
                "findings": [self.elsewhere("codex", command)],
                "summary": {"fail": 1, "warn": 0},
            },
        )


if __name__ == "__main__":
    unittest.main()






