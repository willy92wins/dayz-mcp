from __future__ import annotations

import importlib
import hashlib
import json
import shlex
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.daemon_policy import AccreditedDaemonPolicy

try:
    doctor = importlib.import_module("dayz_mcp.doctor")
except ModuleNotFoundError:
    doctor = None


CLAUDE_GOOD = """dayz-mcp:
  Type: stdio
  Command: C:\\Python\\python.exe
  Args: -m dayz_mcp --client --keyfile C:\\DayZ MCP\\shared.key --port 8765 --client-platform claude
"""


def codex_config(
    *args: str,
    transport_type: str = "stdio",
    command: str = "C:\\Python\\python.exe",
) -> str:
    return json.dumps(
        {
            "name": "dayz-mcp",
            "transport": {
                "type": transport_type,
                "command": command,
                "args": list(args),
            },
        }
    )


CODEX_GOOD = codex_config(
    "-m",
    "dayz_mcp",
    "--client",
    "--keyfile",
    "C:\\DayZ MCP\\shared.key",
    "--port",
    "8765",
    "--client-platform",
    "codex",
)

DAEMON_GOOD = (
    '"C:\\Python\\python.exe" -m dayz_mcp --daemon --port 8765 '
    '--keyfile "C:\\DayZ MCP\\shared.key" --idle-timeout 1800.0'
)


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


def public_audit_fault() -> dict[str, object]:
    return {
        "format_version": 1,
        "fault_id": "fault-1",
        "daemon_generation": "generation",
        "state": "fault",
        "operation": "grant",
        "phase": "audit_failed",
        "lease_id": "lease-1",
        "ticket_id": "ticket-1",
        "client": {
            "platform": "codex",
            "session": "session-a",
            "started_at_utc": "2026-07-22T00:00:00Z",
            "task_label": "queue wait",
        },
        "reason": "fifo_head",
        "armed_at_utc": "2026-07-22T00:00:00Z",
        "failure": "audit_failed",
        "expected_snapshot_revision": 7,
        "repair_phase": "none",
    }


class DoctorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.scan_root = self.root / "launchers"
        self.scan_root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def require_doctor(self):
        self.assertIsNotNone(doctor, "dayz_mcp.doctor is not implemented")
        return doctor

    def sources(self, **overrides):
        module = self.require_doctor()

        def snapshot(names: list[str]) -> dict[str, object]:
            return {"known": True, "processes": []}

        values = {
            "claude_config": lambda: (0, CLAUDE_GOOD),
            "codex_config": lambda: (0, CODEX_GOOD),
            "listener_pid": lambda port: 700 if port == 8765 else None,
            "process_argv": lambda pid: (
                daemon_argv(DAEMON_GOOD) if pid == 700 else None
            ),
            "daemon_status": lambda port, keyfile: clean_status(),
            "process_snapshot": snapshot,
            "process_identity": lambda pid: {"error": "process_not_found", "exit_code": 4},
            "runtime_paths": module.RuntimePaths(
                self.runtime,
                self.runtime / "audit",
                self.runtime / "coordination.json",
                self.runtime / "runs.json",
            ),
            "scan_roots": (self.scan_root,),
        }
        if "expected_command" in module.DoctorSources.__dataclass_fields__:
            values["expected_command"] = "C:\\Python\\python.exe"
        values.update(overrides)
        return module.DoctorSources(**values)

    @staticmethod
    def codes(payload: dict[str, object]) -> list[str]:
        return [item["code"] for item in payload["findings"]]

    def execute(self, **overrides):
        module = self.require_doctor()
        require_clean = bool(overrides.pop("require_clean", False))
        return module.execute(self.sources(**overrides), require_clean=require_clean)

    def write_run(
        self,
        *,
        pid: int = 900,
        state: str = "RUNNING_IDLE",
        command_hash: str = "b" * 64,
        identity_scheme: str | None = "psutil-argv-v2",
    ) -> dict[str, object]:
        record = {
            "pid": pid,
            "creation_time_utc": "2026-07-15T00:00:00.0000000Z",
            "executable_sha256": "a" * 64,
            "command_line_sha256": command_hash,
            "role": "client",
        }
        if identity_scheme is not None:
            record["identity_scheme"] = identity_scheme
        run = {
            "run_id": "run-1",
            "owner_session_id": None,
            "owner_lease_id": None,
            "state": state,
            "label": "fixture",
            "mod": "@Fixture",
            "profiles": "profiles",
            "mission": "mission",
            "processes": [record],
        }
        (self.runtime / "runs.json").write_text(
            json.dumps({"version": 1, "runs": [run]}), encoding="utf-8"
        )
        return record

    def test_clean_state_has_stable_schema_and_exit_zero(self) -> None:
        payload, exit_code = self.execute()
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "ok": True,
                "findings": [],
                "summary": {"fail": 0, "warn": 0},
            },
        )

    def test_current_key_401_is_daemon_credential_desynchronized(self) -> None:
        module = self.require_doctor()
        keyfile = self.root / "daemon.key"
        policy = accredited_policy(keyfile)
        with (
            patch.object(
                module.pinned_keyfile,
                "read_pinned_keyfile",
                return_value="fixture-current-key",
            ),
            patch.object(
                module.transport,
                "verified_daemon_http_request",
                return_value=(401, b'{"error":"unauthorized"}'),
            ),
        ):
            with self.assertRaises(module._DaemonStatusError) as captured:
                module._read_daemon_status(policy)
        self.assertEqual(captured.exception.code, "daemon_credential_desynchronized")
        self.assertNotIn("fixture-current-key", str(captured.exception))

        payload, exit_code = self.execute(
            daemon_status=lambda _port, _key: (_ for _ in ()).throw(
                module._DaemonStatusError("daemon_credential_desynchronized")
            )
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            [
                finding
                for finding in payload["findings"]
                if finding["code"] == "DAEMON_CREDENTIAL_DESYNCHRONIZED"
            ],
            [
                {
                    "code": "DAEMON_CREDENTIAL_DESYNCHRONIZED",
                    "severity": "FAIL",
                    "port": 8765,
                    "pid": 700,
                }
            ],
        )
        self.assertNotIn("DAEMON_STATUS_UNREADABLE", self.codes(payload))

    def test_doctor_revalidates_policy_after_read_before_sending_key(
        self,
    ) -> None:
        module = self.require_doctor()
        keyfile = self.root / "daemon.key"
        policy = accredited_policy(keyfile)
        revalidations = 0

        def revalidate() -> None:
            nonlocal revalidations
            revalidations += 1
            if revalidations > 1:
                raise ValueError("daemon_policy_drift")

        object.__setattr__(policy, "_revalidation_hook", revalidate)
        with (
            patch.object(
                module.pinned_keyfile,
                "read_pinned_keyfile",
                return_value="fixture-current-key",
            ),
            patch.object(
                module.transport,
                "verified_daemon_http_request",
                return_value=(401, b'{"error":"unauthorized"}'),
            ) as request,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "^daemon_policy_drift$",
            ):
                module._read_daemon_status(policy)

        request.assert_not_called()
        self.assertEqual(revalidations, 2)

    def test_recent_credential_recovery_is_sanitized_info(self) -> None:
        status = clean_status()
        status["credential_recovery"] = {
            "recovered_count": 3,
            "recent": True,
            "last_recovered_age_s": 1.25,
        }
        payload, exit_code = self.execute(
            daemon_status=lambda _port, _key: status
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(
            payload["findings"],
            [
                {
                    "code": "STALE_CLIENT_CREDENTIAL_RECOVERED",
                    "severity": "INFO",
                    "port": 8765,
                    "recovered_count": 3,
                    "last_recovered_age_s": 1.25,
                }
            ],
        )
        self.assertEqual(payload["summary"], {"fail": 0, "warn": 0})

    def test_daemon_without_credential_recovery_field_remains_compatible(self) -> None:
        payload, exit_code = self.execute(
            daemon_status=lambda _port, _key: clean_status()
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["findings"], [])

    def test_invalid_credential_recovery_schema_is_unreadable(self) -> None:
        variants = (
            None,
            {},
            {
                "recovered_count": True,
                "recent": False,
                "last_recovered_age_s": None,
            },
            {
                "recovered_count": 1,
                "recent": True,
                "last_recovered_age_s": None,
            },
            {
                "recovered_count": 1,
                "recent": False,
                "last_recovered_age_s": 1.0,
            },
            {
                "recovered_count": 1,
                "recent": True,
                "last_recovered_age_s": 301.0,
            },
            {
                "recovered_count": 1,
                "recent": True,
                "last_recovered_age_s": 1.0,
                "unexpected": "field",
            },
        )
        for recovery in variants:
            with self.subTest(recovery=recovery):
                status = clean_status()
                status["credential_recovery"] = recovery
                payload, exit_code = self.execute(
                    daemon_status=lambda _port, _key, status=status: status
                )
                self.assertEqual(exit_code, 1)
                self.assertIn("DAEMON_STATUS_UNREADABLE", self.codes(payload))

    def test_missing_client_or_embedded_registration_is_config_embedded(self) -> None:
        for platform, value in (
            ("claude", CLAUDE_GOOD.replace("--client ", "--embedded ")),
            (
                "codex",
                codex_config(
                    "-m",
                    "dayz_mcp",
                    "--embedded",
                    "--keyfile",
                    "C:\\DayZ MCP\\shared.key",
                    "--port",
                    "8765",
                    "--client-platform",
                    "codex",
                ),
            ),
        ):
            with self.subTest(platform=platform):
                override = {f"{platform}_config": lambda value=value: (0, value)}
                payload, exit_code = self.execute(**override)
                self.assertEqual(exit_code, 1)
                self.assertIn("CONFIG_EMBEDDED", self.codes(payload))

    def test_platform_and_endpoint_divergence_is_config_mismatch(self) -> None:
        bad_codex = codex_config(
            "-m",
            "dayz_mcp",
            "--client",
            "--keyfile",
            "C:\\other.key",
            "--port",
            "8766",
            "--client-platform",
            "claude",
        )
        payload, exit_code = self.execute(
            codex_config=lambda: (0, bad_codex),
            listener_pid=lambda port: {8765: 700, 8766: 701}.get(port),
            process_argv=lambda pid: daemon_argv(
                f"python -m dayz_mcp --daemon --port {pid}"
            ),
            daemon_status=lambda port, keyfile: clean_status(),
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("CONFIG_MISMATCH", self.codes(payload))
        self.assertIn("MULTIPLE_LISTENERS", self.codes(payload))

    def test_successful_probe_with_invalid_config_is_config_unreadable(self) -> None:
        for override in (
            {"claude_config": lambda: (0, "not-a-registration")},
            {"codex_config": lambda: (0, "not-json")},
        ):
            with self.subTest(override=tuple(override)):
                payload, exit_code = self.execute(**override)
                self.assertEqual(exit_code, 1)
                self.assertIn("CONFIG_UNREADABLE", self.codes(payload))
                self.assertNotIn("CONFIG_PROBE_FAILED", self.codes(payload))
                self.assertNotIn("error", payload)

    def test_failed_probe_is_distinct_actionable_fail_finding(self) -> None:
        failed_output = "DO_NOT_EXPOSE_PROBE_OUTPUT"
        payload, exit_code = self.execute(
            claude_config=lambda: (1, failed_output)
        )

        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["findings"],
            [
                {
                    "code": "CONFIG_PROBE_FAILED",
                    "severity": "FAIL",
                    "platform": "claude",
                    "message": (
                        "The CLI probe failed; this does not prove the registration "
                        "is invalid. Read the platform config file directly before "
                        "changing anything; do not re-register."
                    ),
                }
            ],
        )
        self.assertNotIn("CONFIG_UNREADABLE", self.codes(payload))
        rendered_json = self.require_doctor().render_json(payload)
        rendered_human = self.require_doctor().render_human(payload)
        self.assertNotIn(failed_output, rendered_json)
        self.assertIn("[FAIL] CONFIG_PROBE_FAILED (claude):", rendered_human)
        self.assertIn("does not prove the registration is invalid", rendered_human)
        self.assertIn("Read the platform config file directly", rendered_human)
        self.assertIn("do not re-register", rendered_human)

    def test_failed_probe_on_both_platforms_is_two_fail_findings(self) -> None:
        payload, exit_code = self.execute(
            claude_config=lambda: (1, ""),
            codex_config=lambda: (127, ""),
        )

        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["summary"], {"fail": 2, "warn": 0})
        self.assertEqual(
            [
                (finding["code"], finding["severity"], finding["platform"])
                for finding in payload["findings"]
            ],
            [
                ("CONFIG_PROBE_FAILED", "FAIL", "claude"),
                ("CONFIG_PROBE_FAILED", "FAIL", "codex"),
            ],
        )
        self.assertNotIn("CONFIG_UNREADABLE", self.codes(payload))
        rendered_human = self.require_doctor().render_human(payload)
        self.assertIn("[FAIL] CONFIG_PROBE_FAILED (claude):", rendered_human)
        self.assertIn("[FAIL] CONFIG_PROBE_FAILED (codex):", rendered_human)

    def test_two_listener_pids_are_reported_without_reclaim(self) -> None:
        codex = CODEX_GOOD.replace('"8765"', '"8766"')
        payload, exit_code = self.execute(
            codex_config=lambda: (0, codex),
            listener_pid=lambda port: {8765: 700, 8766: 701}.get(port),
            process_argv=lambda pid: daemon_argv(
                f"python -m dayz_mcp --daemon --port {pid}"
            ),
            daemon_status=lambda port, keyfile: clean_status(),
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("MULTIPLE_LISTENERS", self.codes(payload))

    def test_unreadable_listener_identity_is_process_scan_failed(self) -> None:
        payload, exit_code = self.execute(process_argv=lambda _pid: None)
        self.assertEqual(exit_code, 1)
        self.assertIn("PROCESS_SCAN_FAILED", self.codes(payload))

    def test_listener_requires_exact_daemon_mode_and_port_before_status(self) -> None:
        variants = (
            (DAEMON_GOOD, 0, None),
            (
                DAEMON_GOOD.replace("--daemon", "--embedded"),
                1,
                "PROCESS_SCAN_FAILED",
            ),
            (
                DAEMON_GOOD.replace("--daemon", "--client"),
                1,
                "PROCESS_SCAN_FAILED",
            ),
            (
                DAEMON_GOOD.replace("-m dayz_mcp", "-m dayz_mcp -m attacker"),
                1,
                "PROCESS_SCAN_FAILED",
            ),
            (
                DAEMON_GOOD + " --port 9999",
                1,
                "PROCESS_SCAN_FAILED",
            ),
        )
        for command_line, expected_exit, expected_code in variants:
            with self.subTest(command_line=command_line):
                status_calls: list[tuple[int, str]] = []
                payload, exit_code = self.execute(
                    process_argv=lambda _pid, value=command_line: daemon_argv(value),
                    daemon_status=lambda port, keyfile: (
                        status_calls.append((port, keyfile)) or clean_status()
                    ),
                )
                self.assertEqual(exit_code, expected_exit)
                self.assertEqual(len(status_calls), 1 if expected_exit == 0 else 0)
                if expected_code is None:
                    self.assertNotIn("PROCESS_SCAN_FAILED", self.codes(payload))
                else:
                    self.assertIn(expected_code, self.codes(payload))

    def test_listener_identity_and_quote_aware_argv_are_fail_closed(self) -> None:
        variants = (
            (DAEMON_GOOD, 0),
            (
                DAEMON_GOOD
                + ' --task-label "decoy --client --port 9999 --keyfile C:\\evil.key"',
                1,
            ),
            (DAEMON_GOOD.replace('"C:\\Python\\python.exe"', "fake.exe"), 1),
            (DAEMON_GOOD.replace("shared.key", "other.key"), 1),
            (DAEMON_GOOD.replace("--daemon", "--daem"), 1),
            (DAEMON_GOOD.replace("--port 8765", "--port=8765"), 1),
        )
        for command_line, expected_exit in variants:
            with self.subTest(command_line=command_line):
                status_calls: list[tuple[int, str]] = []
                payload, exit_code = self.execute(
                    process_argv=lambda _pid, value=command_line: daemon_argv(value),
                    daemon_status=lambda port, keyfile: (
                        status_calls.append((port, keyfile)) or clean_status()
                    ),
                )
                self.assertEqual(exit_code, expected_exit)
                self.assertEqual(len(status_calls), 1 if expected_exit == 0 else 0)
                if expected_exit == 0:
                    self.assertNotIn("PROCESS_SCAN_FAILED", self.codes(payload))
                else:
                    self.assertIn("PROCESS_SCAN_FAILED", self.codes(payload))

    def test_listener_none_is_fail_closed_not_clean(self) -> None:
        payload, exit_code = self.execute(listener_pid=lambda _port: None)
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("DAEMON_STATUS_UNREADABLE", self.codes(payload))

    def test_wrong_effective_transport_or_command_is_config_unreadable(self) -> None:
        variants = (
            {"claude_config": lambda: (0, CLAUDE_GOOD.replace("Type: stdio", "Type: http"))},
            {
                "claude_config": lambda: (
                    0,
                    CLAUDE_GOOD.replace(
                        "Command: C:\\Python\\python.exe",
                        "Command: C:\\Python\\python.exe.evil",
                    ),
                )
            },
            {
                "codex_config": lambda: (
                    0,
                    codex_config(
                        "-m", "dayz_mcp", "--client", "--keyfile",
                        "C:\\DayZ MCP\\shared.key", "--port", "8765",
                        "--client-platform", "codex", transport_type="http",
                    ),
                )
            },
            {
                "codex_config": lambda: (
                    0,
                    codex_config(
                        "-m", "dayz_mcp", "--client", "--keyfile",
                        "C:\\DayZ MCP\\shared.key", "--port", "8765",
                        "--client-platform", "codex",
                        command="C:\\Python\\python.exe.evil",
                    ),
                )
            },
        )
        for index, override in enumerate(variants):
            with self.subTest(index=index):
                payload, exit_code = self.execute(**override)
                self.assertEqual(exit_code, 1)
                self.assertIn("CONFIG_UNREADABLE", self.codes(payload))

    def test_claude_duplicate_type_command_or_args_field_is_unreadable(self) -> None:
        conflicts = (
            "  Type: http\n",
            "  Command: C:\\Python\\python.exe.evil\n",
            "  Args: -m dayz_mcp --embedded --keyfile C:\\evil.key --port 9999 --client-platform codex\n",
        )
        for conflict in conflicts:
            with self.subTest(conflict=conflict.strip().split(":", 1)[0]):
                payload, exit_code = self.execute(
                    claude_config=lambda conflict=conflict: (0, CLAUDE_GOOD + conflict)
                )
                self.assertEqual(exit_code, 1)
                self.assertIn("CONFIG_UNREADABLE", self.codes(payload))

    def test_claude_duplicate_or_conflicting_runtime_flags_are_rejected(self) -> None:
        replacements = (
            ("-m dayz_mcp", "-m dayz_mcp -m attacker"),
            ("--client ", "--client --client "),
            ("--client ", "--client --daemon "),
            ("--port 8765", "--port 8765 --port 9999"),
            (
                "--keyfile C:\\DayZ MCP\\shared.key",
                "--keyfile C:\\DayZ MCP\\shared.key --keyfile C:\\evil.key",
            ),
            (
                "--client-platform claude",
                "--client-platform claude --client-platform codex",
            ),
        )
        for old, new in replacements:
            with self.subTest(new=new):
                payload, exit_code = self.execute(
                    claude_config=lambda old=old, new=new: (
                        0, CLAUDE_GOOD.replace(old, new)
                    )
                )
                self.assertEqual(exit_code, 1)
                self.assertTrue(
                    {"CONFIG_UNREADABLE", "CONFIG_EMBEDDED"}
                    & set(self.codes(payload))
                )

    def test_codex_duplicate_or_conflicting_runtime_flags_are_rejected(self) -> None:
        base = [
            "-m", "dayz_mcp", "--client", "--keyfile",
            "C:\\DayZ MCP\\shared.key", "--port", "8765",
            "--client-platform", "codex",
        ]
        variants = (
            [*base, "-m", "attacker"],
            [*base, "--client"],
            [*base, "--daemon"],
            [*base, "--port", "9999"],
            [*base, "--keyfile", "C:\\evil.key"],
            [*base, "--client-platform", "claude"],
        )
        for args in variants:
            with self.subTest(args=args[-2:]):
                payload, exit_code = self.execute(
                    codex_config=lambda args=args: (0, codex_config(*args))
                )
                self.assertEqual(exit_code, 1)
                self.assertTrue(
                    {"CONFIG_UNREADABLE", "CONFIG_EMBEDDED"}
                    & set(self.codes(payload))
                )

    def test_claude_unknown_abbreviated_or_equals_options_are_rejected(self) -> None:
        options = (
            "--port=9999",
            "--keyfile=C:\\evil.key",
            "--client-platform=codex",
            "--daem",
            "--stray",
        )
        for option in options:
            with self.subTest(option=option):
                payload, exit_code = self.execute(
                    claude_config=lambda option=option: (
                        0, CLAUDE_GOOD.replace("Args: ", f"Args: {option} ")
                    )
                )
                self.assertEqual(exit_code, 1)
                self.assertIn("CONFIG_UNREADABLE", self.codes(payload))

    def test_codex_unknown_abbreviated_or_equals_options_are_rejected(self) -> None:
        base = [
            "-m", "dayz_mcp", "--client", "--keyfile",
            "C:\\DayZ MCP\\shared.key", "--port", "8765",
            "--client-platform", "codex",
        ]
        for option in (
            "--port=9999",
            "--keyfile=C:\\evil.key",
            "--client-platform=claude",
            "--daem",
            "--stray",
        ):
            with self.subTest(option=option):
                payload, exit_code = self.execute(
                    codex_config=lambda option=option: (
                        0, codex_config(option, *base)
                    )
                )
                self.assertEqual(exit_code, 1)
                self.assertIn("CONFIG_UNREADABLE", self.codes(payload))

    def test_codex_duplicate_json_keys_are_rejected(self) -> None:
        arguments = json.dumps(
            [
                "-m", "dayz_mcp", "--client", "--keyfile",
                "C:\\DayZ MCP\\shared.key", "--port", "8765",
                "--client-platform", "codex",
            ]
        )
        command = json.dumps("C:\\Python\\python.exe")
        transport = f'{{"type":"stdio","command":{command},"args":{arguments}}}'
        duplicate_payloads = (
            '{"transport":' + transport + ',"transport":' + transport + '}',
            f'{{"transport":{{"type":"stdio","type":"stdio",'
            f'"command":{command},"args":{arguments}}}}}',
            f'{{"transport":{{"type":"stdio","command":{command},'
            f'"command":{command},"args":{arguments}}}}}',
            f'{{"transport":{{"type":"stdio","command":{command},'
            f'"args":[],"args":{arguments}}}}}',
        )
        for payload_text in duplicate_payloads:
            with self.subTest(payload_text=payload_text[:48]):
                payload, exit_code = self.execute(
                    codex_config=lambda value=payload_text: (0, value)
                )
                self.assertEqual(exit_code, 1)
                self.assertIn("CONFIG_UNREADABLE", self.codes(payload))

    def test_registration_policy_divergence_is_config_mismatch(self) -> None:
        policy_options = (
            "--expected-game-version 1.29",
            "--require-version",
            "--idle-timeout 12",
            "--enable-exec-enforce",
            "--exec-allowlist C:\\allow.json",
        )
        for policy in policy_options:
            with self.subTest(policy=policy):
                payload, exit_code = self.execute(
                    claude_config=lambda policy=policy: (
                        0, CLAUDE_GOOD.replace("Args: ", f"Args: {policy} ")
                    )
                )
                self.assertEqual(exit_code, 1)
                self.assertIn("CONFIG_MISMATCH", self.codes(payload))

    def test_listener_policy_must_match_normalized_registration_policy(self) -> None:
        status_calls: list[tuple[int, str]] = []
        payload, exit_code = self.execute(
            process_argv=lambda _pid: daemon_argv(
                DAEMON_GOOD.replace(
                    "--idle-timeout 1800.0", "--idle-timeout 12"
                )
            ),
            daemon_status=lambda port, keyfile: (
                status_calls.append((port, keyfile)) or clean_status()
            ),
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("PROCESS_SCAN_FAILED", self.codes(payload))
        self.assertEqual(status_calls, [])

    def test_matching_optional_policy_is_clean_across_clients_and_listener(self) -> None:
        policy_args = (
            "--expected-game-version 1.29 --require-version --idle-timeout 12 "
            "--enable-exec-enforce --exec-allowlist C:\\DayZ MCP\\allow.json"
        )
        claude = CLAUDE_GOOD.replace(
            "--client-platform claude", f"{policy_args} --client-platform claude"
        )
        codex = codex_config(
            "-m", "dayz_mcp", "--client", "--keyfile",
            "C:\\DayZ MCP\\shared.key", "--port", "8765",
            "--expected-game-version", "1.29", "--require-version",
            "--idle-timeout", "12", "--enable-exec-enforce",
            "--exec-allowlist", "C:\\DayZ MCP\\allow.json",
            "--client-platform", "codex",
        )
        listener = (
            '"C:\\Python\\python.exe" -m dayz_mcp --daemon --port 8765 '
            '--keyfile "C:\\DayZ MCP\\shared.key" '
            '--expected-game-version 1.29 --require-version --idle-timeout 12 '
            '--enable-exec-enforce --exec-allowlist "C:\\DayZ MCP\\allow.json"'
        )
        payload, exit_code = self.execute(
            claude_config=lambda: (0, claude),
            codex_config=lambda: (0, codex),
            process_argv=lambda _pid: daemon_argv(listener),
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.codes(payload), [])

    def test_partial_coordination_status_is_unreadable(self) -> None:
        status = {"coordination": {"captured_at_monotonic": 100.0}}
        payload, exit_code = self.execute(daemon_status=lambda _port, _key: status)
        self.assertEqual(exit_code, 1)
        self.assertIn("DAEMON_STATUS_UNREADABLE", self.codes(payload))

    def test_daemon_generation_is_required_and_nonempty(self) -> None:
        variants = []
        missing = clean_status()
        missing.pop("daemon_generation")
        variants.append(missing)
        for value in ("", 42):
            status = clean_status()
            status["daemon_generation"] = value
            variants.append(status)
        for index, status in enumerate(variants):
            with self.subTest(index=index):
                payload, exit_code = self.execute(
                    daemon_status=lambda _port, _key, status=status: status
                )
                self.assertEqual(exit_code, 1)
                self.assertIn("DAEMON_STATUS_UNREADABLE", self.codes(payload))

    def test_coordination_lease_states_are_mutually_exclusive(self) -> None:
        lease = {
            "lease_id": "lease",
            "session": "session",
            "granted_at_monotonic": 100.0,
            "expires_at_monotonic": 1100.0,
        }
        for first, second in (
            ("active", "granting"),
            ("active", "releasing"),
            ("releasing", "granting"),
        ):
            with self.subTest(first=first, second=second):
                status = clean_status()
                status["coordination"][first] = dict(lease, lease_id=first)
                status["coordination"][second] = dict(lease, lease_id=second)
                payload, exit_code = self.execute(
                    daemon_status=lambda _port, _key, status=status: status
                )
                self.assertEqual(exit_code, 1)
                self.assertIn("DAEMON_STATUS_UNREADABLE", self.codes(payload))

    def test_coordination_temporal_invariants_are_fail_closed(self) -> None:
        lease_granted_after_capture = clean_status(captured=100.0)
        lease_granted_after_capture["coordination"]["active"] = {
            "lease_id": "lease",
            "session": "session",
            "granted_at_monotonic": 101.0,
            "expires_at_monotonic": 110.0,
        }
        lease_expires_before_grant = clean_status(captured=100.0)
        lease_expires_before_grant["coordination"]["active"] = {
            "lease_id": "lease",
            "session": "session",
            "granted_at_monotonic": 90.0,
            "expires_at_monotonic": 89.0,
        }
        ticket_created_after_touch = clean_status(captured=100.0)
        ticket_created_after_touch["coordination"]["queue"] = [
            {
                "ticket": "ticket",
                "session": "session",
                "created_at_monotonic": 91.0,
                "touched_at_monotonic": 90.0,
            }
        ]
        ticket_touched_after_capture = clean_status(captured=100.0)
        ticket_touched_after_capture["coordination"]["queue"] = [
            {
                "ticket": "ticket",
                "session": "session",
                "created_at_monotonic": 90.0,
                "touched_at_monotonic": 101.0,
            }
        ]
        for status in (
            lease_granted_after_capture,
            lease_expires_before_grant,
            ticket_created_after_touch,
            ticket_touched_after_capture,
        ):
            with self.subTest(status=status):
                payload, exit_code = self.execute(
                    daemon_status=lambda _port, _key, status=status: status
                )
                self.assertEqual(exit_code, 1)
                self.assertIn("DAEMON_STATUS_UNREADABLE", self.codes(payload))

    def test_duplicate_queue_ticket_ids_are_unreadable(self) -> None:
        status = clean_status(captured=100.0)
        status["coordination"]["queue"] = [
            {
                "ticket": "duplicate",
                "session": f"session-{index}",
                "created_at_monotonic": 10.0 + index,
                "touched_at_monotonic": 20.0 + index,
            }
            for index in range(2)
        ]
        payload, exit_code = self.execute(daemon_status=lambda _port, _key: status)
        self.assertEqual(exit_code, 1)
        self.assertIn("DAEMON_STATUS_UNREADABLE", self.codes(payload))

    def test_well_formed_non_null_coordination_snapshot_is_clean(self) -> None:
        status = clean_status(captured=100.0)
        status["coordination"]["active"] = {
            "lease_id": "lease",
            "session": "session",
            "granted_at_monotonic": 10.0,
            "expires_at_monotonic": 110.0,
        }
        status["coordination"]["queue"] = [
            {
                "ticket": "ticket",
                "session": "queued-session",
                "created_at_monotonic": 20.0,
                "touched_at_monotonic": 30.0,
            }
        ]
        payload, exit_code = self.execute(daemon_status=lambda _port, _key: status)
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.codes(payload), [])

    def test_public_coordination_fault_is_distinct_fail_finding(self) -> None:
        status = clean_status()
        status["coordination"]["claimable"] = False
        status["coordination"]["audit_fault"] = public_audit_fault()
        payload, exit_code = self.execute(daemon_status=lambda _port, _key: status)

        self.assertEqual(exit_code, 1)
        findings = [
            item
            for item in payload["findings"]
            if item["code"] == "COORDINATION_AUDIT_FAULT"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "FAIL")
        self.assertEqual(findings[0]["fault_id"], "fault-1")
        self.assertEqual(findings[0]["operation"], "grant")
        self.assertEqual(findings[0]["phase"], "audit_failed")
        self.assertNotIn("lease_token", repr(findings))

    def test_operation_tombstone_saturation_is_public_fail_finding(self) -> None:
        status = clean_status()
        status["coordination"]["operation_tombstones"] = {
            "count": 128,
            "capacity": 128,
            "saturated": True,
        }

        payload, exit_code = self.execute(daemon_status=lambda _port, _key: status)

        self.assertEqual(exit_code, 1)
        findings = [
            item
            for item in payload["findings"]
            if item["code"] == "OPERATION_TOMBSTONES_SATURATED"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "FAIL")
        self.assertEqual(findings[0]["count"], 128)
        self.assertEqual(findings[0]["capacity"], 128)
        self.assertNotIn("session", repr(findings))

    def test_malformed_nested_coordination_status_is_unreadable(self) -> None:
        variants: list[dict[str, object]] = []
        bad_cleanup = clean_status()
        bad_cleanup["coordination"]["cleanup_workers"]["active"] = True
        variants.append(bad_cleanup)
        bad_lease = clean_status()
        bad_lease["coordination"]["active"] = {
            "lease_id": "lease",
            "session": "session",
            "granted_at_monotonic": 1.0,
        }
        variants.append(bad_lease)
        bad_ticket = clean_status()
        bad_ticket["coordination"]["queue"] = [
            {
                "ticket": "ticket",
                "session": "",
                "created_at_monotonic": 1.0,
                "touched_at_monotonic": 2.0,
            }
        ]
        variants.append(bad_ticket)
        bad_workers = clean_status()
        bad_workers["coordination"]["cleanup_workers"] = {
            "capacity": 4,
            "active": 5,
            "saturated": 0,
        }
        variants.append(bad_workers)
        for index, status in enumerate(variants):
            with self.subTest(index=index):
                payload, exit_code = self.execute(
                    daemon_status=lambda _port, _key, status=status: status
                )
                self.assertEqual(exit_code, 1)
                self.assertIn("DAEMON_STATUS_UNREADABLE", self.codes(payload))

    def test_stale_lease_and_ticket_are_distinct_findings(self) -> None:
        status = clean_status(captured=500.0)
        status["coordination"] = {
            "revision": 1,
            "captured_at_monotonic": 500.0,
            "active": {
                "lease_id": "lease",
                "session": "session",
                "granted_at_monotonic": 100.0,
                "expires_at_monotonic": 499.0,
            },
            "releasing": None,
            "granting": None,
            "handoff_pending": False,
            "claimable": True,
            "audit_fault": None,
            "operation_tombstones": {"count": 0, "capacity": 128, "saturated": False},
            "queue": [
                {
                    "ticket": "ticket",
                    "session": "session",
                    "created_at_monotonic": 100.0,
                    "touched_at_monotonic": 379.0,
                }
            ],
            "cleanup_workers": {"capacity": 4, "active": 0, "saturated": 0},
        }
        payload, exit_code = self.execute(daemon_status=lambda port, keyfile: status)
        self.assertEqual(exit_code, 1)
        self.assertIn("LEASE_STALE", self.codes(payload))
        self.assertIn("TICKET_STALE", self.codes(payload))

    def test_retail_is_warn_normally_and_fail_when_clean_is_required(self) -> None:
        def snapshot(names: list[str]) -> dict[str, object]:
            if "DayZ_BE.exe" in names:
                return {
                    "known": True,
                    "processes": [
                        {"pid": 42, "name": "DayZ_x64.exe"},
                        {"pid": 41, "name": "DayZ_BE.exe"},
                    ],
                }
            return {"known": True, "processes": []}

        normal, normal_exit = self.execute(process_snapshot=snapshot)
        strict, strict_exit = self.execute(process_snapshot=snapshot, require_clean=True)
        self.assertEqual((normal_exit, normal["ok"]), (0, True))
        self.assertEqual((strict_exit, strict["ok"]), (1, False))
        self.assertEqual(normal["findings"][0]["code"], "RETAIL_MANUAL_CLOSE_REQUIRED")
        self.assertEqual(normal["findings"][0]["severity"], "WARN")
        self.assertEqual(strict["findings"][0]["severity"], "FAIL")
        self.assertEqual(normal["findings"][0]["processes"][0]["pid"], 41)
        self.assertNotIn("PROCESS_UNREGISTERED", self.codes(normal))

    def test_unknown_toolhelp_snapshot_is_never_clean(self) -> None:
        payload, exit_code = self.execute(
            process_snapshot=lambda names: {"known": False, "processes": []}
        )
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("PROCESS_SCAN_FAILED", self.codes(payload))

    def test_live_diag_without_strong_run_record_is_process_unregistered(self) -> None:
        def snapshot(names: list[str]) -> dict[str, object]:
            if "DayZDiag_x64.exe" in names:
                return {
                    "known": True,
                    "processes": [{"pid": 900, "name": "DayZDiag_x64.exe"}],
                }
            return {"known": True, "processes": []}

        payload, exit_code = self.execute(process_snapshot=snapshot)
        self.assertEqual(exit_code, 1)
        self.assertIn("PROCESS_UNREGISTERED", self.codes(payload))

    def test_registered_run_is_revalidated_and_mismatch_is_not_unregistered(self) -> None:
        record = self.write_run()

        def snapshot(names: list[str]) -> dict[str, object]:
            if "DayZDiag_x64.exe" in names:
                return {
                    "known": True,
                    "processes": [{"pid": 900, "name": "DayZDiag_x64.exe"}],
                }
            return {"known": True, "processes": []}

        clean, clean_exit = self.execute(
            process_snapshot=snapshot,
            process_identity=lambda pid: {**record, "identity_complete": True},
        )
        self.assertEqual((clean_exit, self.codes(clean)), (0, []))

        mismatch, mismatch_exit = self.execute(
            process_snapshot=snapshot,
            process_identity=lambda pid: {
                **record,
                "identity_complete": True,
                "command_line_sha256": "c" * 64,
            },
        )
        self.assertEqual(mismatch_exit, 1)
        self.assertIn("RUN_IDENTITY_MISMATCH", self.codes(mismatch))
        self.assertNotIn("PROCESS_UNREGISTERED", self.codes(mismatch))

    def test_legacy_process_identity_is_reported_without_native_guard_match(self) -> None:
        self.write_run(identity_scheme=None)

        def snapshot(names: list[str]) -> dict[str, object]:
            if "DayZDiag_x64.exe" in names:
                return {
                    "known": True,
                    "processes": [{"pid": 900, "name": "DayZDiag_x64.exe"}],
                }
            return {"known": True, "processes": []}

        def identity_must_not_run(_pid: int) -> dict[str, object]:
            raise AssertionError("legacy identity must not reach native matching")

        payload, exit_code = self.execute(
            process_snapshot=snapshot,
            process_identity=identity_must_not_run,
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("LEGACY_PROCESS_IDENTITY_SCHEME", self.codes(payload))
        self.assertNotIn("RUN_IDENTITY_MISMATCH", self.codes(payload))
        self.assertNotIn("PROCESS_SCAN_FAILED", self.codes(payload))

    def test_absent_registered_process_is_run_stale(self) -> None:
        self.write_run()
        payload, exit_code = self.execute()
        self.assertEqual(exit_code, 1)
        self.assertIn("RUN_STALE", self.codes(payload))
        self.assertNotIn("PROCESS_UNREGISTERED", self.codes(payload))

    def test_empty_unreconciled_run_is_stale_until_explicit_admin_resolution(self) -> None:
        run = {
            "run_id": "run-empty",
            "owner_session_id": None,
            "owner_lease_id": None,
            "state": "UNRECONCILED",
            "label": "fixture",
            "mod": "@Fixture",
            "profiles": "profiles",
            "mission": "mission",
            "processes": [],
        }
        (self.runtime / "runs.json").write_text(
            json.dumps({"version": 1, "runs": [run]}), encoding="utf-8"
        )
        payload, exit_code = self.execute()
        self.assertEqual(exit_code, 1)
        stale = [item for item in payload["findings"] if item["code"] == "RUN_STALE"]
        self.assertEqual(stale, [{"code": "RUN_STALE", "severity": "FAIL", "run_id": "run-empty"}])

    def test_empty_transitional_or_running_run_is_stale(self) -> None:
        for state in ("STARTING", "STOPPING", "RUNNING"):
            with self.subTest(state=state):
                run = {
                    "run_id": f"run-{state.casefold()}",
                    "owner_session_id": "session" if state == "RUNNING" else None,
                    "owner_lease_id": "lease" if state == "RUNNING" else None,
                    "state": state,
                    "label": "fixture",
                    "mod": "@Fixture",
                    "profiles": "profiles",
                    "mission": "mission",
                    "processes": [],
                }
                (self.runtime / "runs.json").write_text(
                    json.dumps({"version": 1, "runs": [run]}), encoding="utf-8"
                )
                payload, exit_code = self.execute()
                self.assertEqual(exit_code, 1)
                self.assertIn("RUN_STALE", self.codes(payload))

    def test_duplicate_live_pid_in_two_runs_is_identity_mismatch(self) -> None:
        record = self.write_run()
        first = json.loads((self.runtime / "runs.json").read_text(encoding="utf-8"))["runs"][0]
        second = {**first, "run_id": "run-2"}
        (self.runtime / "runs.json").write_text(
            json.dumps({"version": 1, "runs": [first, second]}), encoding="utf-8"
        )

        def snapshot(names: list[str]) -> dict[str, object]:
            if "DayZDiag_x64.exe" in names:
                return {
                    "known": True,
                    "processes": [{"pid": 900, "name": "DayZDiag_x64.exe"}],
                }
            return {"known": True, "processes": []}

        payload, exit_code = self.execute(
            process_snapshot=snapshot,
            process_identity=lambda pid: {**record, "identity_complete": True},
        )
        self.assertEqual(exit_code, 1)
        mismatch = [
            item for item in payload["findings"] if item["code"] == "RUN_IDENTITY_MISMATCH"
        ]
        self.assertEqual(mismatch[0]["pid"], 900)
        self.assertEqual(mismatch[0]["run_ids"], ["run-1", "run-2"])

    def test_invalid_manifest_is_run_manifest_unreadable(self) -> None:
        (self.runtime / "runs.json").write_text("not-json", encoding="utf-8")
        payload, exit_code = self.execute()
        self.assertEqual(exit_code, 1)
        self.assertIn("RUN_MANIFEST_UNREADABLE", self.codes(payload))

    def test_preprune_backup_slots_exhausted_is_reported_only_when_full(self) -> None:
        (self.runtime / "runs.json").write_text(
            json.dumps({"version": 1, "runs": []}), encoding="utf-8"
        )
        base = self.runtime / "runs.json.bak-preprune"
        base.write_bytes(b"slot-1\n")
        for index in range(2, 11):
            (self.runtime / f"runs.json.bak-preprune.{index}").write_bytes(
                f"slot-{index}\n".encode("utf-8")
            )

        full, full_exit = self.execute()
        self.assertEqual(full_exit, 1)
        exhausted = [
            item
            for item in full["findings"]
            if item["code"] == "RUN_PREPRUNE_BACKUP_SLOTS_EXHAUSTED"
        ]
        self.assertEqual(len(exhausted), 1)
        self.assertEqual(exhausted[0]["severity"], "FAIL")
        self.assertEqual(exhausted[0]["slots"], 10)
        self.assertEqual(Path(exhausted[0]["path"]), base.resolve())

        (self.runtime / "runs.json.bak-preprune.10").unlink()
        free, free_exit = self.execute()
        self.assertEqual(free_exit, 0)
        self.assertNotIn("RUN_PREPRUNE_BACKUP_SLOTS_EXHAUSTED", self.codes(free))

    def test_active_blind_kill_is_reported_and_backup_is_ignored(self) -> None:
        active = self.scan_root / "Active" / "dayz-test.ps1"
        backup = self.scan_root / "_BACKUPS" / "dayz-test.ps1"
        active.parent.mkdir()
        backup.parent.mkdir()
        active.write_text("Get-Process DayZ | Stop-Process -Force\n", encoding="utf-8")
        backup.write_text("Stop-Process -Id 123\n", encoding="utf-8")
        payload, exit_code = self.execute()
        self.assertEqual(exit_code, 1)
        findings = [
            item for item in payload["findings"] if item["code"] == "LEGACY_BLIND_KILL"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(Path(findings[0]["path"]), active.resolve())
        self.assertEqual(findings[0]["line"], 1)

    def test_findings_are_deterministic_and_do_not_leak_secrets(self) -> None:
        secret = "DO_NOT_PRINT_THIS_KEY"
        payload, exit_code = self.execute(
            daemon_status=lambda port, keyfile: (_ for _ in ()).throw(OSError(secret)),
        )
        rendered = self.require_doctor().render_json(payload)
        self.assertEqual(exit_code, 1)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("lease_token", rendered)
        order = [
            (item["code"], item.get("path", ""), item.get("pid", 0))
            for item in payload["findings"]
        ]
        self.assertEqual(order, sorted(order))

    def test_total_internal_failure_has_stable_exit_two(self) -> None:
        payload, exit_code = self.execute(
            claude_config=lambda: (_ for _ in ()).throw(RuntimeError("sensitive"))
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "error": "diagnostic_failure",
                "findings": [],
                "summary": {"fail": 0, "warn": 0},
            },
        )
        self.assertNotIn("sensitive", self.require_doctor().render_json(payload))

    def test_doctor_source_has_no_mutating_process_or_admin_path(self) -> None:
        module = self.require_doctor()
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "kill_pid",
            "try_reclaim",
            ".terminate(",
            "subprocess.Popen",
            '"/admin/',
            '"/lifecycle/',
        ):
            self.assertNotIn(forbidden, source)

    def test_installer_contains_exact_dual_client_registration_contract(self) -> None:
        script = (_TOOLS_DIR / "install-mcp.ps1").read_text(encoding="utf-8")
        self.assertIn(
            "$claudeArgs = $serverArgs + @('--client-platform','claude')", script
        )
        self.assertIn(
            "$codexArgs  = $serverArgs + @('--client-platform','codex')", script
        )
        self.assertIn("$CodexCmd=(Get-Command codex.cmd).Source", script)
        self.assertIn("& claude mcp add dayz-mcp -s user -- $VenvPython @claudeArgs", script)
        self.assertIn("& $CodexCmd mcp add dayz-mcp -- $VenvPython @codexArgs", script)
        self.assertIn("& $CodexCmd mcp get dayz-mcp --json", script)
        self.assertIn("Test-ClaudeRegistration", script)
        self.assertIn("Test-CodexRegistration", script)
        self.assertIn(
            "Test-ClaudeRegistration $effectiveClaude $VenvPython $KeyFile \"$Port\" $claudeArgs", script
        )
        self.assertIn(
            "Test-CodexJsonShape $effectiveCodex", script
        )
        self.assertIn(
            "Test-CodexRegistration $codexConfig $VenvPython $KeyFile \"$Port\" $codexArgs", script
        )
        self.assertLess(
            script.index("Test-CodexJsonShape $effectiveCodex"),
            script.index("$effectiveCodex | ConvertFrom-Json"),
        )
        self.assertNotIn("$effectiveClaude.Contains($KeyFile)", script)
        self.assertNotIn("[Array]::IndexOf", script)

if __name__ == "__main__":
    unittest.main()
