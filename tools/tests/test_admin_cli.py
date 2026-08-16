from __future__ import annotations

import json
import hashlib
import io
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dayz_mcp import admin_cli, doctor, lifecycle_cli  # noqa: E402
from dayz_mcp.daemon_policy import AccreditedDaemonPolicy  # noqa: E402


def _provenance(keyfile: str) -> SimpleNamespace:
    launcher = str(Path(sys.executable).resolve())
    return SimpleNamespace(
        launch_executable=launcher,
        native_executable=r"C:\native\python.exe",
        argv=(
            launcher,
            "-m",
            "dayz_mcp",
            "--daemon",
            "--port",
            "18765",
            "--keyfile",
            keyfile,
            "--idle-timeout",
            "1800.0",
        ),
        cwd=str(TOOLS_DIR.resolve()),
        port=18765,
        keyfile=keyfile,
    )


def _require_matching_keyfile(value: str, expected: str) -> str:
    if str(Path(value)) != str(Path(expected)):
        raise RuntimeError("daemon_provenance_conflict")
    return value


def _policy(keyfile: Path, *, kind: str = "normal") -> AccreditedDaemonPolicy:
    build_id = None if kind == "normal" else "a" * 64
    argv = (
        [
            r"C:\native\python.exe",
            "-m",
            "dayz_mcp",
            "--daemon",
            "--port",
            "18765",
        ]
        if kind == "normal"
        else [
            r"C:\native\python.exe",
            "-I",
            "-B",
            r"P:\DayZ_MCP_dev\tools\p0s_daemon_bootstrap.py",
            "daemon",
            "--port",
            "18765",
        ]
    )
    authority = {
        "argv": argv,
        "cwd": str(TOOLS_DIR.resolve()),
        "host": "127.0.0.1",
        "keyfile": str(keyfile.resolve()),
        "kind": kind,
        "native_executable": r"C:\native\python.exe",
        "port": 18765,
        "security_build_id": build_id,
    }
    authority_sha256 = hashlib.sha256(
        json.dumps(
            authority, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    return AccreditedDaemonPolicy(
        kind=kind,
        host="127.0.0.1",
        port=18765,
        keyfile=str(keyfile.resolve()),
        native_executable=r"C:\native\python.exe",
        argv=tuple(authority["argv"]),
        cwd=str(TOOLS_DIR.resolve()),
        security_build_id=build_id,
        authority_sha256=authority_sha256,
    )


class AuxiliaryVerifiedHttpRoutingTest(unittest.TestCase):
    def _routing_patches(
        self,
        module: object,
        response: tuple[int, bytes],
        keyfile: str,
    ):
        verified_calls: list[dict[str, object]] = []

        def verified(**kwargs: object) -> tuple[int, bytes]:
            verified_calls.append(kwargs)
            return response

        stack = (
            patch.object(
                module.pinned_keyfile,
                "read_pinned_keyfile",
                return_value="api-key",
            ),
            patch.object(
                module.transport,
                "verified_daemon_http_request",
                side_effect=verified,
            ),
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("direct_socket_forbidden"),
            ),
        )
        return stack, verified_calls

    def test_admin_preserves_method_path_body_status_and_uses_verified_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            keyfile = Path(directory) / "daemon.key"
            keyfile.write_text("api-key", encoding="utf-8")
            patches, calls = self._routing_patches(
                admin_cli, (409, b'{"error":"session_granting"}'), str(keyfile.resolve())
            )
            with patches[0], patches[1], patches[2]:
                result = admin_cli._request(
                    _policy(keyfile),
                    "POST",
                    "/admin/release",
                    {"lease_id": "lease-public"},
                )

        self.assertEqual(result, (409, {"error": "session_granting"}))
        self.assertEqual(len(calls), 1)
        request = calls[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/admin/release")
        self.assertEqual(request["query"], {})
        self.assertEqual(request["body"], b'{"lease_id":"lease-public"}')
        self.assertEqual(request["headers"], {"Content-Type": "application/json"})
        self.assertEqual(request["key"], "api-key")
        self.assertEqual(
            request["expected_argv"], list(_policy(keyfile).argv)
        )
        self.assertEqual(request["expected_executable"], r"C:\native\python.exe")

    def test_lifecycle_preserves_endpoint_and_identity_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            keyfile = Path(directory) / "daemon.key"
            keyfile.write_text("api-key", encoding="utf-8")
            patches, calls = self._routing_patches(
                lifecycle_cli,
                (202, b'{"status":"stopping"}'),
                str(keyfile.resolve()),
            )
            payload = {
                "identity": {"session_id": "session-public"},
                "lease_token": "lease-secret",
                "run_id": "run-public",
            }
            with patches[0], patches[1], patches[2]:
                result = lifecycle_cli._request(
                    _policy(keyfile), "/lifecycle/stop", payload
                )

        self.assertEqual(result, (202, {"status": "stopping"}))
        self.assertEqual(len(calls), 1)
        request = calls[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/lifecycle/stop")
        self.assertEqual(request["query"], {})
        self.assertEqual(
            json.loads(request["body"].decode("utf-8")), payload  # type: ignore[union-attr]
        )
        self.assertNotIn("lease-secret", request["path"])
        self.assertEqual(request["expected_executable"], r"C:\native\python.exe")
        self.assertEqual(request["expected_argv"], list(_policy(keyfile).argv))

    def test_doctor_status_uses_same_verified_transport_and_bounded_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            keyfile = Path(directory) / "daemon.key"
            keyfile.write_text("api-key", encoding="utf-8")
            patches, calls = self._routing_patches(
                doctor,
                (200, b'{"daemon_generation":"generation-public"}'),
                str(keyfile.resolve()),
            )
            with patches[0], patches[1], patches[2]:
                result = doctor._read_daemon_status(_policy(keyfile))

        self.assertEqual(result, {"daemon_generation": "generation-public"})
        self.assertEqual(len(calls), 1)
        request = calls[0]
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["path"], "/status")
        self.assertEqual(request["body"], None)
        self.assertEqual(request["key"], "api-key")
        self.assertEqual(
            request["max_response_bytes"],
            doctor.transport.MAX_AUTHENTICATED_RESPONSE_BYTES,
        )
        self.assertEqual(request["expected_executable"], r"C:\native\python.exe")
        self.assertEqual(request["expected_argv"], list(_policy(keyfile).argv))

    def _legacy_existing_foreign_keyfile_and_doctor_provenance_failure_precede_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "expected.key"
            foreign = Path(directory) / "foreign.key"
            expected.write_text("expected-secret", encoding="utf-8")
            foreign.write_text("foreign-secret", encoding="utf-8")

            for module, invoke in (
                (
                    admin_cli,
                    lambda: admin_cli._request(
                        18765, str(foreign.resolve()), "GET", "/status"
                    ),
                ),
                (
                    lifecycle_cli,
                    lambda: lifecycle_cli._request(
                        18765, str(foreign.resolve()), "/lifecycle/status", {}
                    ),
                ),
            ):
                key_reads: list[str] = []
                transport: list[object] = []
                with self.subTest(module=module.__name__), patch.object(
                    module,
                    "host_config",
                    SimpleNamespace(
                        HostConfigError=RuntimeError,
                        require_matching_keyfile=_require_matching_keyfile,
                        resolve_daemon_provenance=lambda: _provenance(
                            str(expected.resolve())
                        ),
                    ),
                    create=True,
                ), patch.object(
                    module,
                    "_read_key",
                    side_effect=lambda path: key_reads.append(path) or "secret",
                ), patch.object(
                    module,
                    "orphan_guard",
                    SimpleNamespace(
                        MAX_AUTHENTICATED_RESPONSE_BYTES=4 * 1024 * 1024,
                        verified_daemon_http_request=lambda **kwargs: transport.append(kwargs),
                    ),
                    create=True,
                ):
                    with self.assertRaisesRegex(RuntimeError, "daemon_provenance_conflict"):
                        invoke()
                self.assertEqual(key_reads, [])
                self.assertEqual(transport, [])

            reads: list[object] = []
            with patch.object(
                doctor,
                "host_config",
                SimpleNamespace(
                    HostConfigError=RuntimeError,
                    resolve_daemon_provenance=lambda: (_ for _ in ()).throw(
                        RuntimeError("daemon_provenance_incomplete")
                    ),
                ),
                create=True,
            ), patch.object(
                Path, "read_text", side_effect=lambda *_a, **_k: reads.append(True)
            ):
                with self.assertRaisesRegex(RuntimeError, "daemon_provenance_incomplete"):
                    doctor._read_daemon_status(18765, str(expected.resolve()))
            self.assertEqual(reads, [])

    def _legacy_provenance_or_keyfile_mismatch_happens_before_key_read_or_transport(self) -> None:
        class ConfigFailure(RuntimeError):
            pass

        verified_calls: list[object] = []
        key_reads: list[str] = []
        for module, invoke in (
            (
                admin_cli,
                lambda: admin_cli._request(
                    18765, r"C:\foreign\daemon.key", "GET", "/status"
                ),
            ),
            (
                lifecycle_cli,
                lambda: lifecycle_cli._request(
                    18765,
                    r"C:\foreign\daemon.key",
                    "/lifecycle/status",
                    {"lease_token": "secret"},
                ),
            ),
        ):
            with self.subTest(module=module.__name__), patch.object(
                module,
                "host_config",
                SimpleNamespace(
                    HostConfigError=ConfigFailure,
                    resolve_daemon_provenance=lambda: (_ for _ in ()).throw(
                        ConfigFailure("daemon_provenance_incomplete")
                    )
                ),
                create=True,
            ), patch.object(
                module,
                "orphan_guard",
                SimpleNamespace(
                    verified_daemon_http_request=lambda **_kwargs: verified_calls.append(True)
                ),
                create=True,
            ), patch.object(
                module,
                "_read_key",
                side_effect=lambda path: key_reads.append(path) or "secret",
            ):
                with self.assertRaises(ConfigFailure):
                    invoke()
        self.assertEqual(verified_calls, [])
        self.assertEqual(key_reads, [])

    def _legacy_mismatched_keyfile_is_rejected_before_read_connect_or_request(self) -> None:
        for module, invoke in (
            (
                admin_cli,
                lambda: admin_cli._request(
                    18765, r"C:\foreign\daemon.key", "GET", "/status"
                ),
            ),
            (
                lifecycle_cli,
                lambda: lifecycle_cli._request(
                    18765, r"C:\foreign\daemon.key", "/lifecycle/status", {}
                ),
            ),
        ):
            key_reads: list[str] = []
            verified_calls: list[object] = []
            with self.subTest(module=module.__name__), patch.object(
                module,
                "host_config",
                SimpleNamespace(
                    HostConfigError=RuntimeError,
                    require_matching_keyfile=_require_matching_keyfile,
                    resolve_daemon_provenance=lambda: _provenance(
                        r"C:\expected\daemon.key"
                    ),
                ),
                create=True,
            ), patch.object(
                module,
                "_read_key",
                side_effect=lambda path: key_reads.append(path) or "secret",
            ), patch.object(
                module,
                "orphan_guard",
                SimpleNamespace(
                    MAX_AUTHENTICATED_RESPONSE_BYTES=4 * 1024 * 1024,
                    verified_daemon_http_request=lambda **kwargs: (
                        verified_calls.append(kwargs) or (200, b"{}")
                    ),
                ),
                create=True,
            ), patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("direct_socket_forbidden"),
            ):
                with self.assertRaisesRegex(RuntimeError, "daemon_provenance_conflict"):
                    invoke()
            self.assertEqual(key_reads, [])
            self.assertEqual(verified_calls, [])

        doctor_reads: list[str] = []
        doctor_requests: list[object] = []
        with patch.object(
            doctor,
            "host_config",
            SimpleNamespace(
                HostConfigError=RuntimeError,
                require_matching_keyfile=_require_matching_keyfile,
                resolve_daemon_provenance=lambda: _provenance(
                    r"C:\expected\daemon.key"
                ),
            ),
            create=True,
        ), patch.object(
            Path,
            "read_text",
            side_effect=lambda *args, **kwargs: doctor_reads.append("read") or "secret",
        ), patch.object(
            doctor,
            "orphan_guard",
            SimpleNamespace(
                MAX_STATUS_BODY_BYTES=64 * 1024,
                verified_daemon_http_request=lambda **kwargs: doctor_requests.append(kwargs),
            ),
            create=True,
        ), patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("direct_socket_forbidden"),
        ):
            with self.assertRaisesRegex(RuntimeError, "daemon_provenance_conflict"):
                doctor._read_daemon_status(18765, r"C:\foreign\daemon.key")
        self.assertEqual(doctor_reads, [])
        self.assertEqual(doctor_requests, [])


class ExplicitDaemonPolicyRoutingTest(unittest.TestCase):
    def test_auxiliary_callers_share_the_pinned_bounded_key_reader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            keyfile = Path(directory) / "daemon.key"
            keyfile.write_text("api-key", encoding="utf-8")
            policy = _policy(keyfile)
            cases = (
                (
                    admin_cli,
                    lambda: admin_cli._request(policy, "GET", "/status"),
                    (200, b"{}"),
                ),
                (
                    lifecycle_cli,
                    lambda: lifecycle_cli._request(
                        policy, "/lifecycle/status", {}
                    ),
                    (200, b"{}"),
                ),
                (
                    doctor,
                    lambda: doctor._read_daemon_status(policy),
                    (200, b"{}"),
                ),
            )
            for module, invoke, response in cases:
                with (
                    self.subTest(module=module.__name__),
                    patch.object(
                        module.pinned_keyfile,
                        "read_pinned_keyfile",
                        return_value="pinned-key",
                    ) as read_key,
                    patch.object(
                        module.transport,
                        "verified_daemon_http_request",
                        return_value=response,
                    ),
                ):
                    invoke()
                    read_key.assert_called_once_with(policy.keyfile)

    def test_all_auxiliary_http_calls_receive_one_explicit_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            keyfile = Path(directory) / "daemon.key"
            keyfile.write_text("api-key", encoding="utf-8")
            policy = _policy(keyfile)
            calls: list[dict[str, object]] = []

            def verified(**kwargs: object) -> tuple[int, bytes]:
                calls.append(dict(kwargs))
                if kwargs["path"] == "/status":
                    return 200, b'{"status":"ok"}'
                return 202, b'{"status":"accepted"}'

            forbidden_host_config = SimpleNamespace(
                resolve_daemon_provenance=lambda: (_ for _ in ()).throw(
                    AssertionError("implicit_host_config_forbidden")
                )
            )
            for module in (admin_cli, lifecycle_cli, doctor):
                if hasattr(module, "host_config"):
                    patcher = patch.object(
                        module, "host_config", forbidden_host_config, create=True
                    )
                    patcher.start()
                    self.addCleanup(patcher.stop)
            with (
                patch.object(
                    admin_cli.transport,
                    "verified_daemon_http_request",
                    side_effect=verified,
                ),
                patch.object(
                    lifecycle_cli.transport,
                    "verified_daemon_http_request",
                    side_effect=verified,
                ),
                patch.object(
                    doctor.transport,
                    "verified_daemon_http_request",
                    side_effect=verified,
                ),
            ):
                self.assertEqual(
                    admin_cli._request(policy, "GET", "/status"),
                    (200, {"status": "ok"}),
                )
                self.assertEqual(
                    lifecycle_cli._request(
                        policy, "/lifecycle/status", {"identity": {}}
                    ),
                    (202, {"status": "accepted"}),
                )
                self.assertEqual(
                    doctor._read_daemon_status(policy), {"status": "ok"}
                )

        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call["port"] == policy.port for call in calls))
        self.assertTrue(all(call["key"] == "api-key" for call in calls))
        self.assertTrue(
            all(call["expected_argv"] == list(policy.argv) for call in calls)
        )

    def test_lifecycle_main_routes_policy_and_erases_identity_and_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            keyfile = Path(directory) / "daemon.key"
            keyfile.write_text("api-key", encoding="utf-8")
            policy = _policy(keyfile, kind="bootstrap")
            observed: list[tuple[object, str, dict[str, object]]] = []

            def request(
                actual_policy: object,
                path: str,
                payload: dict[str, object],
            ) -> tuple[int, dict[str, object]]:
                self.assertNotIn("DAYZ_MCP_CLIENT_ID_JSON", os.environ)
                self.assertNotIn("DAYZ_MCP_LEASE_TOKEN", os.environ)
                observed.append((actual_policy, path, payload))
                return 200, {"ok": True}

            environment = {
                "DAYZ_MCP_CLIENT_ID_JSON": json.dumps(
                    {
                        "platform": "codex",
                        "pid": 1,
                        "ppid": 0,
                        "started_at_utc": "2026-07-22T00:00:00Z",
                        "session_id": "session-public",
                        "task_label": "",
                    }
                ),
                "DAYZ_MCP_LEASE_TOKEN": "lease-secret",
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(
                    lifecycle_cli.daemon_policy,
                    "load_daemon_policy",
                    return_value=policy,
                ) as load_policy,
                patch.object(lifecycle_cli, "_request", side_effect=request),
                patch("sys.stdout", io.StringIO()),
            ):
                code = lifecycle_cli.main(
                    ["--daemon-policy", "bootstrap", "status"]
                )

        self.assertEqual(code, 0)
        load_policy.assert_called_once_with("bootstrap")
        self.assertIs(observed[0][0], policy)
        self.assertEqual(observed[0][1], "/lifecycle/status")
        self.assertEqual(observed[0][2]["lease_token"], "lease-secret")

    def test_admin_and_doctor_main_route_the_named_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            keyfile = Path(directory) / "daemon.key"
            keyfile.write_text("api-key", encoding="utf-8")
            policy = _policy(keyfile)

            def admin_request(
                actual_policy: object,
                method: str,
                path: str,
                payload: dict[str, object] | None = None,
            ) -> tuple[int, dict[str, object]]:
                self.assertIs(actual_policy, policy)
                if method == "GET":
                    return 200, {"coordination": {"active": {"lease_id": "lease-1"}}}
                return 200, {"released": True}

            with (
                patch.object(
                    admin_cli.daemon_policy,
                    "load_daemon_policy",
                    return_value=policy,
                ) as admin_load,
                patch.object(admin_cli, "_request", side_effect=admin_request),
                patch("sys.stdin", type("Tty", (io.StringIO,), {"isatty": lambda self: True})("FORCE lease-1\n")),
                patch("sys.stdout", io.StringIO()),
            ):
                admin_code = admin_cli.main(
                    ["--daemon-policy", "normal", "release", "--reason", "incident"]
                )
            admin_load.assert_called_once_with("normal")
            self.assertEqual(admin_code, 0)

            with (
                patch.object(
                    doctor.daemon_policy,
                    "load_daemon_policy",
                    return_value=policy,
                ) as doctor_load,
                patch.object(
                    doctor,
                    "execute",
                    return_value=({"ok": True, "findings": [], "summary": {"fail": 0, "warn": 0}}, 0),
                ) as execute,
                patch("sys.stdout", io.StringIO()),
            ):
                doctor_code = doctor.main(["--daemon-policy", "normal", "--json"])
            doctor_load.assert_called_once_with("normal")
            execute.assert_called_once_with(require_clean=False, policy=policy)
            self.assertEqual(doctor_code, 0)


if __name__ == "__main__":
    unittest.main()
