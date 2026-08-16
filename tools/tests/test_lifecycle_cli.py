from __future__ import annotations

import io
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import admin_cli, lifecycle_cli


IDENTITY = {
    "platform": "codex",
    "pid": 11,
    "ppid": 1,
    "started_at_utc": "2026-07-15T00:00:00Z",
    "session_id": "A",
    "task_label": "cli",
}


class TtyInput(io.StringIO):
    def isatty(self) -> bool:
        return True


class LifecycleCliTest(unittest.TestCase):
    def test_requires_identity_and_token_env_without_echoing_values(self) -> None:
        stderr = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch("sys.stderr", stderr):
            code = lifecycle_cli.main(["--daemon-policy", "normal", "status"])
        self.assertEqual(code, 2)
        self.assertEqual(stderr.getvalue().strip(), "missing_lifecycle_environment")

    def test_start_reads_bounded_stdin_generates_identity_and_acks(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        policy = object()

        def request(actual_policy, path, payload):
            self.assertIs(actual_policy, policy)
            calls.append((path, payload))
            if path == "/lifecycle/start":
                return 200, {"ok": True, "run_id": payload["request"]["new_run_id"], "state": "RUNNING"}
            return 200, {"ok": True, "state": "RUNNING"}

        source_without_hash = {
            "argv": ["diag.exe"],
            "new_run_id": "11111111-1111-4111-8111-111111111111",
            "launch_operation_id": "22222222-2222-4222-8222-222222222222",
        }
        source = source_without_hash | {
            "launch_request_sha256": hashlib.sha256(
                json.dumps(source_without_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        }
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(source).encode("utf-8")))
        stdout = io.StringIO()
        env = {
            "DAYZ_MCP_CLIENT_ID_JSON": json.dumps(IDENTITY),
            "DAYZ_MCP_LEASE_TOKEN": "secret-token",
        }
        argv = ["--daemon-policy", "normal", "start", "--request-stdin"]
        with patch.dict(os.environ, env, clear=True), patch.object(
            lifecycle_cli.daemon_policy,
            "load_daemon_policy",
            return_value=policy,
        ), patch.object(lifecycle_cli, "_request", request), patch("sys.stdin", stdin), patch("sys.stdout", stdout):
            code = lifecycle_cli.main(argv)
        self.assertEqual(code, 0)
        self.assertNotIn("secret-token", " ".join(argv))
        self.assertEqual([call[0] for call in calls], ["/lifecycle/start", "/lifecycle/ack"])
        request_payload = calls[0][1]["request"]
        self.assertEqual(calls[0][1]["lease_token"], "secret-token")
        self.assertEqual(request_payload, source)
        self.assertEqual(
            request_payload["launch_request_sha256"],
            hashlib.sha256(json.dumps(source_without_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        )
        self.assertEqual(calls[1][1]["run_id"], request_payload["new_run_id"])
        self.assertEqual(calls[1][1]["launch_operation_id"], request_payload["launch_operation_id"])
        self.assertNotIn("secret-token", stdout.getvalue())

    def test_start_rejects_request_file_and_stdin_over_64k_before_http(self) -> None:
        env = {
            "DAYZ_MCP_CLIENT_ID_JSON": json.dumps(IDENTITY),
            "DAYZ_MCP_LEASE_TOKEN": "secret-token",
        }
        with patch.dict(os.environ, env, clear=True), self.assertRaises(SystemExit):
            lifecycle_cli.main(["--daemon-policy", "normal", "start", "--request-file", "x"])

        stdin = io.TextIOWrapper(io.BytesIO(b"{" + b" " * 65536 + b"}"))
        stderr = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch("sys.stdin", stdin), patch("sys.stderr", stderr), patch.object(
            lifecycle_cli.daemon_policy, "load_daemon_policy", return_value=object()
        ), patch.object(lifecycle_cli, "_request", side_effect=AssertionError("http must not run")):
            self.assertEqual(
                lifecycle_cli.main(["--daemon-policy", "normal", "start", "--request-stdin"]),
                2,
            )
        self.assertEqual(stderr.getvalue().strip(), "lifecycle_request_failed")

    def test_stop_adopt_and_status_use_env_body_without_secret_output(self) -> None:
        with TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "key"
            keyfile.write_text("api-key", encoding="utf-8")
            env = {
                "DAYZ_MCP_CLIENT_ID_JSON": json.dumps(IDENTITY),
                "DAYZ_MCP_LEASE_TOKEN": "secret-token",
            }
            for command, extra, expected_path in (
                ("stop", ["--run-id", "R"], "/lifecycle/stop"),
                ("adopt", ["--run-id", "R"], "/lifecycle/adopt"),
                ("status", [], "/lifecycle/status"),
            ):
                with self.subTest(command=command):
                    calls: list[tuple[str, dict[str, object]]] = []

                    policy = object()

                    def request(actual_policy, path, payload):
                        self.assertIs(actual_policy, policy)
                        calls.append((path, payload))
                        return 200, {"ok": True}

                    stdout, stderr = io.StringIO(), io.StringIO()
                    argv = ["--daemon-policy", "normal", command, *extra]
                    with (
                        patch.dict(os.environ, env, clear=True),
                        patch.object(
                            lifecycle_cli.daemon_policy,
                            "load_daemon_policy",
                            return_value=policy,
                        ),
                        patch.object(lifecycle_cli, "_request", request),
                        patch("sys.stdout", stdout),
                        patch("sys.stderr", stderr),
                    ):
                        self.assertEqual(lifecycle_cli.main(argv), 0)
                    self.assertEqual(calls[0][0], expected_path)
                    if command in {"stop", "adopt"}:
                        self.assertEqual(calls[0][1]["run_id"], "R")
                    wire = " ".join(argv) + stdout.getvalue() + stderr.getvalue()
                    self.assertNotIn("secret-token", wire)
                    self.assertNotIn("api-key", wire)

    def test_lifecycle_env_and_identity_failures_never_echo_secrets(self) -> None:
        cases = (
            {"DAYZ_MCP_CLIENT_ID_JSON": json.dumps(IDENTITY)},
            {"DAYZ_MCP_LEASE_TOKEN": "secret-token"},
            {
                "DAYZ_MCP_CLIENT_ID_JSON": "{invalid",
                "DAYZ_MCP_LEASE_TOKEN": "secret-token",
            },
        )
        for env in cases:
            with self.subTest(env=sorted(env)):
                stdout, stderr = io.StringIO(), io.StringIO()
                with (
                    patch.dict(os.environ, env, clear=True),
                    patch.object(
                        lifecycle_cli.daemon_policy,
                        "load_daemon_policy",
                        return_value=object(),
                    ),
                    patch("sys.stdout", stdout),
                    patch("sys.stderr", stderr),
                ):
                    self.assertEqual(
                        lifecycle_cli.main(
                            ["--daemon-policy", "normal", "status"]
                        ),
                        2,
                    )
                wire = stdout.getvalue() + stderr.getvalue()
                self.assertNotIn("secret-token", wire)
                self.assertNotIn("api-key", wire)

    def test_admin_requires_tty_reason_and_exact_confirmation(self) -> None:
        with patch("sys.stdin", io.StringIO("FORCE lease-1\n")):
            self.assertEqual(
                admin_cli.main(
                    ["--daemon-policy", "normal", "release", "--reason", "incident"]
                ),
                2,
            )

        calls: list[tuple[str, dict[str, object]]] = []

        policy = object()

        def request(actual_policy, method, path, payload=None):
            self.assertIs(actual_policy, policy)
            if method == "GET":
                return 200, {"coordination": {"active": {"lease_id": "lease-1"}}}
            calls.append((path, payload or {}))
            return 200, {"released": True}

        stdin = TtyInput("wrong\n")
        with patch("sys.stdin", stdin), patch.object(
            admin_cli.daemon_policy,
            "load_daemon_policy",
            return_value=policy,
        ), patch.object(admin_cli, "_request", request):
            self.assertEqual(
                admin_cli.main(
                    ["--daemon-policy", "normal", "release", "--reason", "incident"]
                ),
                3,
            )
        self.assertEqual(calls, [])

        stdin = TtyInput("FORCE lease-1\n")
        with patch("sys.stdin", stdin), patch.object(
            admin_cli.daemon_policy,
            "load_daemon_policy",
            return_value=policy,
        ), patch.object(admin_cli, "_request", request), patch("sys.stdout", io.StringIO()):
            self.assertEqual(
                admin_cli.main(
                    ["--daemon-policy", "normal", "release", "--reason", "incident"]
                ),
                0,
            )
        self.assertEqual(calls[0][0], "/admin/release")
        self.assertEqual(calls[0][1]["reason"], "incident")

    def test_admin_reconcile_pid_and_empty_modes_require_registered_exact_targets(self) -> None:
        status_payload = {
            "lifecycle": {
                "runs": [
                    {
                        "run_id": "with-pid",
                        "state": "UNRECONCILED",
                        "processes": [{"pid": 99}],
                    },
                    {
                        "run_id": "empty",
                        "state": "UNRECONCILED",
                        "processes": [],
                    },
                    {
                        "run_id": "idle-gone",
                        "state": "RUNNING_IDLE",
                        "owner_session_id": None,
                        "owner_lease_id": None,
                        "processes": [{"pid": 88}],
                    },
                ]
            }
        }
        calls: list[tuple[str, dict[str, object]]] = []

        policy = object()

        def request(actual_policy, method, path, payload=None):
            self.assertIs(actual_policy, policy)
            if method == "GET":
                return 200, status_payload
            calls.append((path, payload or {}))
            return 200, {"reconciled": True}

        cases = (
            (
                ["reconcile", "--reason", "incident", "--run-id", "with-pid", "--pid", "99"],
                "FORCE with-pid 99\n",
                {"pid": 99},
            ),
            (
                ["reconcile", "--reason", "incident", "--run-id", "empty", "--empty"],
                "FORCE empty EMPTY\n",
                {"empty": True},
            ),
            (
                ["reconcile", "--reason", "incident", "--run-id", "idle-gone", "--pid", "88"],
                "FORCE idle-gone 88\n",
                {"pid": 88},
            ),
        )
        for args, entered, expected in cases:
            with self.subTest(args=args):
                before = len(calls)
                with (
                    patch("sys.stdin", TtyInput(entered)),
                    patch.object(
                        admin_cli.daemon_policy,
                        "load_daemon_policy",
                        return_value=policy,
                    ),
                    patch.object(admin_cli, "_request", request),
                    patch("sys.stdout", io.StringIO()),
                ):
                    self.assertEqual(
                        admin_cli.main(["--daemon-policy", "normal", *args]), 0
                    )
                self.assertEqual(len(calls), before + 1)
                for key, value in expected.items():
                    self.assertEqual(calls[-1][1][key], value)

        for args in (
            ["reconcile", "--reason", "incident", "--run-id", "missing", "--pid", "99"],
            ["reconcile", "--reason", "incident", "--run-id", "with-pid", "--empty"],
        ):
            with self.subTest(rejected=args), patch(
                "sys.stdin", TtyInput("unused\n")
            ), patch.object(
                admin_cli.daemon_policy,
                "load_daemon_policy",
                return_value=policy,
            ), patch.object(admin_cli, "_request", request), patch(
                "sys.stderr", io.StringIO()
            ):
                self.assertEqual(
                    admin_cli.main(["--daemon-policy", "normal", *args]), 2
                )

    def test_admin_rejects_blank_reason_and_status_or_post_failures(self) -> None:
        with (
            patch("sys.stdin", TtyInput()),
            patch("sys.stderr", io.StringIO()),
        ):
            self.assertEqual(
                admin_cli.main(
                    ["--daemon-policy", "normal", "release", "--reason", "   "]
                ),
                2,
            )

    def test_admin_lifecycle_recovery_repair_uses_exact_head_and_confirmation(self) -> None:
        fault_id = "11111111-1111-4111-8111-111111111111"
        head = "A" * 64
        calls: list[tuple[str, dict[str, object]]] = []

        def request(_policy, method, path, payload=None):
            if method == "GET":
                return 200, {
                    "coordination": {
                        "lifecycle_recovery_fault": {
                            "fault": {"fault_id": fault_id},
                            "pointer": {"head_event_sha256": head},
                        }
                    }
                }
            calls.append((path, payload or {}))
            return 200, {"repaired": True}

        with patch("sys.stdin", TtyInput(f"REPAIR LIFECYCLE {fault_id}\n")), patch.object(
            admin_cli.daemon_policy, "load_daemon_policy", return_value=object()
        ), patch.object(admin_cli, "_request", request), patch("sys.stdout", io.StringIO()):
            code = admin_cli.main(
                [
                    "--daemon-policy",
                    "normal",
                    "lifecycle-recovery-repair",
                    "--reason",
                    "operator reviewed exact process identity",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][0], "/admin/lifecycle-recovery-repair")
        self.assertEqual(calls[0][1]["fault_id"], fault_id)
        self.assertEqual(calls[0][1]["expected_head_sha256"], head)
        self.assertEqual(
            calls[0][1]["confirmation"], f"REPAIR LIFECYCLE {fault_id}"
        )

        with (
            patch("sys.stdin", TtyInput()),
            patch("sys.stderr", io.StringIO()),
            patch.object(
                admin_cli.daemon_policy,
                "load_daemon_policy",
                return_value=object(),
            ),
            patch.object(admin_cli, "_request", return_value=(503, {"error": "down"})),
        ):
            self.assertEqual(
                admin_cli.main(
                    ["--daemon-policy", "normal", "release", "--reason", "incident"]
                ),
                2,
            )


if __name__ == "__main__":
    unittest.main()
