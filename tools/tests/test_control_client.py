from __future__ import annotations

import ast
import asyncio
import hashlib
import hmac
import importlib
import importlib.util
import inspect
import json
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock
from pathlib import Path
from unittest.mock import patch

from dayz_mcp.daemon_policy import AccreditedDaemonPolicy


def _policy(keyfile: Path) -> AccreditedDaemonPolicy:
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


def _clean_session_status(
    *,
    daemon_generation: str = "generation-a",
    owner: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "owner": owner,
        "queue": [],
        "self": {"state": "none", "position": None},
        "claimable": owner is None,
        "audit_fault": None,
        "lifecycle_recovery_fault": None,
        "operation_tombstones": {"count": 0, "capacity": 4096},
        "cleanup_degraded": [],
        "daemon_generation": daemon_generation,
        "pending_commands": 0,
    }


class ControlClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_injected_provider_requires_the_exact_policy_object(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            provider_policy = _policy(keyfile)
            equal_but_distinct_policy = _policy(keyfile)
            self.assertEqual(
                provider_policy,
                equal_but_distinct_policy,
            )
            self.assertIsNot(
                provider_policy,
                equal_but_distinct_policy,
            )
            provider = (
                control.daemon_credential.RefreshingDaemonCredential(
                    policy=provider_policy
                )
            )

            with self.assertRaisesRegex(
                ValueError,
                "^credential_provider_policy_mismatch$",
            ):
                control.ControlClient(
                    policy=equal_but_distinct_policy,
                    identity=identity,
                    credential_provider=provider,
                )

    async def test_lifecycle_status_uses_read_only_authenticated_route(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            with patch.object(
                client,
                "_session_call",
                new=AsyncMock(return_value={"runs": []}),
            ) as session_call:
                result = await client.lifecycle_status()

        self.assertEqual(result, {"runs": []})
        session_call.assert_awaited_once_with("/lifecycle/status")

    async def test_status_uses_exact_constructor_identity_and_accredited_transport(
        self,
    ) -> None:
        spec = importlib.util.find_spec("dayz_mcp.control_client")
        self.assertIsNotNone(spec, "dayz_mcp.control_client is not implemented")
        control = importlib.import_module("dayz_mcp.control_client")
        signature = inspect.signature(control.ControlClient)
        self.assertEqual(
            tuple(signature.parameters),
            (
                "policy",
                "identity",
                "timeout_s",
                "credential_provider",
            ),
        )
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="utopia-recovery",
        )
        calls: list[dict[str, object]] = []

        def request(**kwargs: object) -> tuple[int, bytes]:
            calls.append(dict(kwargs))
            return 200, b'{"pending_commands":0,"status":"ok"}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            with patch.object(
                control.transport, "verified_daemon_http_request", side_effect=request
            ):
                response = await client.session_status()

        self.assertEqual(response, {"pending_commands": 0, "status": "ok"})
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["host"], "127.0.0.1")
        self.assertEqual(call["port"], 8765)
        self.assertEqual(call["key"], "test-key")
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["path"], "/session/status")
        self.assertEqual(call["query"], {})
        self.assertEqual(call["headers"], {"Content-Type": "application/json"})
        self.assertEqual(
            json.loads(call["body"]), {"identity": identity.to_payload()}
        )

    async def test_status_recovers_rotated_credential_in_same_client(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="credential-recovery",
        )
        first_key = "fixture-credential-a"
        second_key = "fixture-credential-b"
        calls: list[dict[str, object]] = []

        def request(**kwargs: object) -> tuple[int, bytes]:
            calls.append(dict(kwargs))
            if hmac.compare_digest(str(kwargs["key"]), first_key):
                return 401, b'{"error":"unauthorized"}'
            return 200, b'{"status":"ok"}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text(first_key + "\n", encoding="utf-8")
            client = control.ControlClient(
                policy=_policy(keyfile),
                identity=identity,
            )
            client.active_lease_token = "lease-existing"
            client.active_ticket = "ticket-existing"
            client.active_operation_id = "operation-existing"
            client.state = "ACTIVE"
            before_state = (
                client.active_lease_token,
                client.active_ticket,
                client.active_operation_id,
                client.state,
            )
            keyfile.write_text(second_key + "\n", encoding="utf-8")
            with (
                patch.object(
                    control.transport,
                    "verified_daemon_http_request",
                    side_effect=request,
                ),
                patch.object(control, "_monotonic", return_value=100.0),
            ):
                try:
                    response = await client.session_status()
                except control.ControlClientError as error:
                    self.fail(
                        "live control client did not recover credential: "
                        + error.code
                    )

            after_state = (
                client.active_lease_token,
                client.active_ticket,
                client.active_operation_id,
                client.state,
            )

        self.assertEqual(response, {"status": "ok"})
        self.assertEqual(len(calls), 2)
        self.assertTrue(hmac.compare_digest(str(calls[0]["key"]), first_key))
        self.assertTrue(hmac.compare_digest(str(calls[1]["key"]), second_key))
        self.assertEqual(calls[0]["deadline"], 105.0)
        self.assertEqual(calls[1]["deadline"], 105.0)
        self.assertEqual(calls[0]["body"], calls[1]["body"])
        self.assertEqual(
            calls[1]["headers"],
            {
                "Content-Type": "application/json",
                "X-DayZ-MCP-Credential-Retry": "1",
            },
        )
        self.assertEqual(after_state, before_state)

    async def test_status_reaccredits_replaced_daemon_without_local_state_change(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="daemon-reaccreditation",
        )
        calls: list[dict[str, object]] = []
        outcomes: list[object] = [
            control.transport.AccreditedTransportError(
                "daemon_identity_unverified",
                request_stage="pre_request",
                http_bytes_sent=0,
            ),
            (200, b'{"daemon_generation":"generation-b","status":"ok"}'),
        ]

        def request(**kwargs: object) -> tuple[int, bytes]:
            calls.append(dict(kwargs))
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            client = control.ControlClient(
                policy=_policy(keyfile),
                identity=identity,
            )
            client.active_lease_token = "lease-from-generation-a"
            client.active_ticket = "ticket-from-generation-a"
            client.active_operation_id = "operation-from-generation-a"
            client.state = "ACTIVE"
            before_state = (
                client.active_lease_token,
                client.active_ticket,
                client.active_operation_id,
                client.state,
            )
            with patch.object(
                control.transport,
                "verified_daemon_http_request",
                side_effect=request,
            ), patch.object(control, "_monotonic", return_value=100.0):
                response = await client.session_status()
            after_state = (
                client.active_lease_token,
                client.active_ticket,
                client.active_operation_id,
                client.state,
            )

        self.assertEqual(
            response,
            {"daemon_generation": "generation-b", "status": "ok"},
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["deadline"], 105.0)
        self.assertEqual(calls[1]["deadline"], 105.0)
        self.assertEqual(calls[0]["body"], calls[1]["body"])
        self.assertEqual(after_state, before_state)

    async def test_transport_stage_maps_to_closed_public_errors(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        cases = (
            ("pre_request", 0, "daemon_unavailable"),
            ("post_request", 1, "daemon_response_ambiguous"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            for stage, sent, expected_code in cases:
                client = control.ControlClient(
                    policy=_policy(keyfile), identity=identity
                )
                failure = control.transport.AccreditedTransportError(
                    "daemon_transport_failure",
                    request_stage=stage,
                    http_bytes_sent=sent,
                )
                with self.subTest(stage=stage), patch.object(
                    control.transport,
                    "verified_daemon_http_request",
                    side_effect=failure,
                ):
                    with self.assertRaises(control.ControlClientError) as raised:
                        await client.session_status()
                    self.assertEqual(raised.exception.code, expected_code)
                    self.assertEqual(raised.exception.request_stage, stage)
                    self.assertEqual(raised.exception.http_bytes_sent, sent)

    async def test_raw_first_send_os_errors_map_to_closed_control_errors(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="raw-first-send-errors",
        )
        cases = (
            (TimeoutError("timed out"), "daemon_unavailable", "pre_request", 0),
            (
                ConnectionError("connection failed"),
                "daemon_response_ambiguous",
                "post_request",
                1,
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            for failure, expected_code, expected_stage, expected_sent in cases:
                calls = 0

                def request(**_kwargs: object) -> tuple[int, bytes]:
                    nonlocal calls
                    calls += 1
                    raise failure

                policy = _policy(keyfile)
                provider = control.daemon_credential.RefreshingDaemonCredential(
                    policy=policy,
                    request_fn=request,
                )
                client = control.ControlClient(
                    policy=policy,
                    identity=identity,
                    credential_provider=provider,
                )
                with self.subTest(error_type=type(failure).__name__):
                    with self.assertRaises(
                        (control.ControlClientError, OSError)
                    ) as raised:
                        await client.session_status()
                    self.assertIsInstance(
                        raised.exception, control.ControlClientError
                    )
                    self.assertEqual(
                        (
                            raised.exception.code,
                            raised.exception.request_stage,
                            raised.exception.http_bytes_sent,
                        ),
                        (expected_code, expected_stage, expected_sent),
                    )
                    self.assertEqual(calls, 1)

    async def test_policy_drift_is_not_mapped_to_spawnable_unavailable(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="credential-source-drift",
        )
        revalidations = 0
        requests = 0

        def revalidate() -> None:
            nonlocal revalidations
            revalidations += 1
            if revalidations > 3:
                raise ValueError("daemon_provenance_conflict")

        def request(**_kwargs: object) -> tuple[int, bytes]:
            nonlocal requests
            requests += 1
            return 200, b'{"status":"ok"}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            policy = _policy(keyfile)
            object.__setattr__(policy, "_revalidation_hook", revalidate)
            client = control.ControlClient(policy=policy, identity=identity)
            with patch.object(
                control.transport,
                "verified_daemon_http_request",
                side_effect=request,
            ):
                with self.assertRaises(
                    control.ControlClientError
                ) as raised:
                    await client.session_status()

        self.assertEqual(
            raised.exception.code,
            "client_policy_untrusted_open_new_session",
        )
        self.assertEqual(raised.exception.request_stage, "pre_request")
        self.assertEqual(raised.exception.http_bytes_sent, 0)
        self.assertEqual(requests, 0)

    async def test_credential_refresh_error_metadata_reaches_control_caller(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="credential-error-metadata",
        )

        class PolicySubstitute:
            pass

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")

            pre_policy = _policy(keyfile)
            pre_requests: list[dict[str, object]] = []

            def pre_request(**kwargs: object) -> tuple[int, bytes]:
                pre_requests.append(dict(kwargs))
                return 200, b'{"status":"ok"}'

            pre_provider = (
                control.daemon_credential.RefreshingDaemonCredential(
                    policy=pre_policy,
                    request_fn=pre_request,
                )
            )
            pre_client = control.ControlClient(
                policy=pre_policy,
                identity=identity,
                credential_provider=pre_provider,
            )
            substitute = PolicySubstitute()
            for attribute in (
                "kind",
                "host",
                "port",
                "keyfile",
                "native_executable",
                "argv",
                "cwd",
                "security_build_id",
                "authority_sha256",
            ):
                setattr(
                    substitute,
                    attribute,
                    getattr(pre_policy, attribute),
                )
            pre_provider.policy = substitute
            with self.assertRaises(control.ControlClientError) as pre_raised:
                await pre_client.session_status()

            post_calls = 0

            def post_request(**_kwargs: object) -> tuple[int, bytes]:
                nonlocal post_calls
                post_calls += 1
                return 401, b'{"error":"unauthorized"}'

            post_policy = _policy(keyfile)
            post_provider = (
                control.daemon_credential.RefreshingDaemonCredential(
                    policy=post_policy,
                    request_fn=post_request,
                )
            )
            post_client = control.ControlClient(
                policy=post_policy,
                identity=identity,
                credential_provider=post_provider,
            )
            with self.assertRaises(control.ControlClientError) as post_raised:
                await post_client.session_status()

        self.assertEqual(
            (
                pre_raised.exception.code,
                pre_raised.exception.request_stage,
                pre_raised.exception.http_bytes_sent,
            ),
            ("client_policy_untrusted_open_new_session", "pre_request", 0),
        )
        self.assertEqual(pre_requests, [])
        self.assertEqual(
            (
                post_raised.exception.code,
                post_raised.exception.request_stage,
                post_raised.exception.http_bytes_sent,
            ),
            ("daemon_credential_desynchronized", "post_request", 1),
        )
        self.assertEqual(post_calls, 2)

    async def test_constructor_reads_key_only_through_pinned_bounded_reader(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            with patch.object(
                control.daemon_credential.pinned_keyfile,
                "read_pinned_keyfile",
                return_value="pinned-key",
            ) as read_key:
                client = control.ControlClient(
                    policy=_policy(keyfile), identity=identity
                )
            calls: list[dict[str, object]] = []

            def request(**kwargs: object) -> tuple[int, bytes]:
                calls.append(dict(kwargs))
                return 200, b'{"status":"ok"}'

            with patch.object(
                control.transport,
                "verified_daemon_http_request",
                side_effect=request,
            ):
                await client.session_status()
        read_key.assert_called_once_with(str(keyfile))
        self.assertEqual(len(calls), 1)
        self.assertTrue(
            hmac.compare_digest(str(calls[0]["key"]), "pinned-key")
        )

    async def test_control_client_import_graph_is_strictly_non_launching(self) -> None:
        spec = importlib.util.find_spec("dayz_mcp.control_client")
        self.assertIsNotNone(spec)
        source_path = Path(spec.origin or "")
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        modules = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertTrue(
            modules.isdisjoint(
                {
                    "dayz_mcp.daemon",
                    "dayz_mcp.orphan_guard",
                    "multiprocessing",
                    "subprocess",
                }
            ),
            modules,
        )
        source = source_path.read_text(encoding="utf-8").casefold()
        for forbidden in ("_ensure_daemon", "spawn_fn", "createprocess", "terminate"):
            self.assertNotIn(forbidden, source)

    async def test_direct_session_methods_preserve_paths_payloads_and_state(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="utopia-recovery",
        )
        calls: list[tuple[str, dict[str, object], float]] = []
        responses = iter(
            (
                {"status": "active", "lease_token": "lease-one"},
                {"status": "active"},
                {"released": True},
                {"status": "queued", "ticket": "ticket-two"},
                {"cancelled": True},
            )
        )

        def request(
            path: str, payload: dict[str, object], timeout_s: float
        ) -> dict[str, object]:
            calls.append((path, payload, timeout_s))
            return next(responses)

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            with patch.object(client, "_request_once", side_effect=request):
                acquired = await client.session_acquire("dayz-test")
                operation_id = calls[0][1]["operation_id"]
                uuid.UUID(operation_id)
                self.assertEqual(client.active_lease_token, "lease-one")
                self.assertIsNone(client.active_ticket)
                self.assertEqual(client.state, "ACTIVE")
                self.assertEqual(
                    acquired["client_identity_json"],
                    json.dumps(identity.to_payload(), separators=(",", ":")),
                )

                await client.session_heartbeat("lease-one")
                await client.session_release("lease-one")
                self.assertIsNone(client.active_lease_token)
                self.assertEqual(client.state, "CLOSED")

                second = control.ControlClient(
                    policy=_policy(keyfile), identity=identity
                )
                with patch.object(second, "_request_once", side_effect=request):
                    queued = await second.session_acquire("dayz-test")
                    self.assertEqual(queued["status"], "queued")
                    self.assertEqual(second.active_ticket, "ticket-two")
                    self.assertEqual(second.state, "QUEUED")
                    await second.session_cancel("ticket-two")
                    self.assertIsNone(second.active_ticket)
                    self.assertEqual(second.state, "CLOSED")

        self.assertEqual(
            [path for path, _payload, _timeout in calls],
            [
                "/session/acquire",
                "/session/heartbeat",
                "/session/release",
                "/session/acquire",
                "/session/cancel",
            ],
        )
        self.assertEqual(calls[1][1]["lease_token"], "lease-one")
        self.assertEqual(calls[2][1]["lease_token"], "lease-one")
        self.assertEqual(calls[4][1]["ticket"], "ticket-two")
        self.assertTrue(all(call[1]["identity"] == identity.to_payload() for call in calls))

    async def test_wait_transitions_queued_to_active_and_bad_response_fails_closed(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            client.active_ticket = "ticket-one"
            client.active_operation_id = "operation-one"
            client.state = "QUEUED"
            with patch.object(
                client,
                "_request_once",
                return_value={"status": "active", "lease_token": "lease-one"},
            ) as request:
                response = await client.session_wait("ticket-one", timeout_s=30.0)
            self.assertEqual(response["status"], "active")
            self.assertEqual(client.active_lease_token, "lease-one")
            self.assertIsNone(client.active_ticket)
            self.assertEqual(client.state, "ACTIVE")
            self.assertEqual(request.call_args.args[0], "/session/wait")
            self.assertEqual(request.call_args.args[1]["ticket"], "ticket-one")
            self.assertEqual(request.call_args.args[1]["timeout_s"], 30.0)
            self.assertEqual(request.call_args.args[2], 31.0)

            invalid = control.ControlClient(policy=_policy(keyfile), identity=identity)
            with patch.object(
                invalid, "_request_once", return_value={"status": "active"}
            ):
                with self.assertRaisesRegex(
                    control.ControlClientError, "daemon_bad_session_response"
                ):
                    await invalid.session_acquire("dayz-test")

    async def test_acquire_wait_is_indefinite_by_default_and_never_exposes_queued(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        progress: list[tuple[float, float | None, str | None]] = []
        operation_id = "11111111-1111-4111-8111-111111111111"

        async def progress_cb(
            elapsed: float, maximum: float | None, message: str | None
        ) -> None:
            progress.append((elapsed, maximum, message))

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            session_call = AsyncMock(
                side_effect=(
                    {"status": "queued", "ticket": "ticket-one", "operation_id": operation_id},
                    {"status": "queued", "ticket": "ticket-one", "position": 2, "operation_id": operation_id},
                    {"status": "active", "lease_token": "lease-one", "ticket": "ticket-one", "operation_id": operation_id},
                )
            )
            with patch.object(client, "_session_call", session_call), patch.object(
                control.uuid, "uuid4", return_value=uuid.UUID(operation_id)
            ):
                response = await client.session_acquire_wait(
                    "dayz-test", progress_cb=progress_cb
                )

        self.assertEqual(response["status"], "active")
        self.assertNotEqual(response["status"], "queued")
        self.assertEqual(client.state, "ACTIVE")
        self.assertEqual(client.active_lease_token, "lease-one")
        self.assertEqual(
            [call.args[0] for call in session_call.await_args_list],
            ["/session/enqueue", "/session/wait", "/session/wait"],
        )
        for call in session_call.await_args_list[1:]:
            self.assertEqual(call.args[1]["timeout_s"], 30.0)
            self.assertEqual(call.kwargs["timeout_s"], 31.0)
        self.assertEqual(progress[0][1], None)
        self.assertIn("2", progress[0][2])

    async def test_indefinite_wait_crosses_1800_with_one_operation_and_ticket(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        progress: list[tuple[float, float | None, str | None]] = []
        operation_id = "22222222-2222-4222-8222-222222222222"

        async def progress_cb(
            elapsed: float, maximum: float | None, message: str | None
        ) -> None:
            progress.append((elapsed, maximum, message))

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            session_call = AsyncMock(
                side_effect=(
                    {"status": "queued", "ticket": "ticket-one", "position": 4, "operation_id": operation_id},
                    {"status": "queued", "ticket": "ticket-one", "position": 4, "operation_id": operation_id},
                    {"status": "queued", "ticket": "ticket-one", "position": 4, "operation_id": operation_id},
                    {"status": "active", "lease_token": "lease-one", "ticket": "ticket-one", "operation_id": operation_id},
                )
            )
            with (
                patch.object(client, "_session_call", session_call),
                patch.object(control.uuid, "uuid4", return_value=uuid.UUID(operation_id)),
                patch.object(control, "_monotonic", side_effect=(0.0, 901.0, 1801.0)),
            ):
                response = await client.session_acquire_wait(
                    "dayz-test", max_wait_s=None, progress_cb=progress_cb
                )

        self.assertEqual(response["status"], "active")
        enqueue = session_call.await_args_list[0]
        waits = session_call.await_args_list[1:]
        self.assertEqual(len(waits), 3)
        self.assertEqual(
            {call.args[1]["ticket"] for call in waits}, {"ticket-one"}
        )
        self.assertEqual(
            {call.args[1]["timeout_s"] for call in waits}, {30.0}
        )
        self.assertEqual(client.active_operation_id, enqueue.args[1]["operation_id"])
        self.assertEqual([item[1] for item in progress], [None, None, None])
        self.assertIn("4", progress[0][2])
        self.assertGreater(progress[-1][0], 1800.0)

    async def test_safe_pre_request_failure_clears_only_its_local_operation(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        safe_failure = control.ControlClientError(
            "daemon_unavailable",
            request_stage="pre_request",
            http_bytes_sent=0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            for method_name, arguments in (
                ("session_acquire", ("dayz-test",)),
                ("session_acquire_wait", ("dayz-test", None)),
            ):
                client = control.ControlClient(
                    policy=_policy(keyfile), identity=identity
                )
                session_call = AsyncMock(side_effect=safe_failure)
                with self.subTest(method=method_name), patch.object(
                    client, "_session_call", session_call
                ):
                    with self.assertRaises(control.ControlClientError) as raised:
                        await getattr(client, method_name)(*arguments)
                    self.assertIs(raised.exception, safe_failure)
                    self.assertEqual(session_call.await_count, 1)
                    self.assertEqual(client.state, "CLOSED")
                    self.assertIsNone(client.active_operation_id)
                    self.assertIsNone(client.active_ticket)
                    self.assertIsNone(client.active_lease_token)

    async def test_acquire_wait_timeout_tombstones_operation_before_return(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        operation_id = "33333333-3333-4333-8333-333333333333"
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            session_call = AsyncMock(
                side_effect=(
                    {"status": "queued", "ticket": "ticket-one", "operation_id": operation_id},
                    {"cancelled": True, "operation_id": operation_id},
                )
            )
            with (
                patch.object(client, "_session_call", session_call),
                patch.object(control.uuid, "uuid4", return_value=uuid.UUID(operation_id)),
                patch.object(
                    control,
                    "_monotonic",
                    side_effect=(10.0, 11.0, 11.0, 11.0),
                ),
            ):
                with self.assertRaisesRegex(
                    control.ControlClientError, "session_wait_timeout"
                ):
                    await client.session_acquire_wait("dayz-test", max_wait_s=0.5)

        self.assertEqual(
            [call.args[0] for call in session_call.await_args_list],
            ["/session/enqueue", "/session/cancel-operation"],
        )
        operation_id = session_call.await_args_list[0].args[1]["operation_id"]
        self.assertEqual(
            session_call.await_args_list[1].args[1]["operation_id"], operation_id
        )
        self.assertEqual(client.state, "CLOSED")
        self.assertIsNone(client.active_ticket)
        self.assertIsNone(client.active_lease_token)

    async def test_acquire_wait_rejects_response_identity_drift_before_state_mutation(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        operation_id = "44444444-4444-4444-8444-444444444444"
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        responses = iter(
            (
                {
                    "status": "queued",
                    "ticket": "ticket-one",
                    "operation_id": operation_id,
                },
                {
                    "status": "active",
                    "lease_token": "lease-drift",
                    "ticket": "ticket-other",
                    "operation_id": operation_id,
                },
                {"cancelled": True, "operation_id": operation_id},
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            session_call = AsyncMock(side_effect=lambda *_args, **_kwargs: next(responses))
            with patch.object(client, "_session_call", session_call), patch.object(
                control.uuid, "uuid4", return_value=uuid.UUID(operation_id)
            ):
                with self.assertRaisesRegex(
                    control.ControlClientError, "daemon_bad_session_response"
                ):
                    await client.session_acquire_wait("dayz-test")

        self.assertEqual(
            [call.args[0] for call in session_call.await_args_list],
            ["/session/enqueue", "/session/wait", "/session/cancel-operation"],
        )
        self.assertEqual(client.state, "CLOSED")
        self.assertIsNone(client.active_lease_token)
        self.assertIsNone(client.active_ticket)

    async def test_stale_errors_clear_only_the_matching_ticket_or_lease(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)

            client.active_ticket = "old-ticket"
            client.active_operation_id = "old-operation"
            client.state = "QUEUED"
            ticket_error = control.ControlClientError(
                "ticket_invalid", request_stage="post_request", http_bytes_sent=1
            )
            with patch.object(client, "_request_once", side_effect=ticket_error):
                with self.assertRaises(control.ControlClientError):
                    await client.session_wait("old-ticket")
            self.assertIsNone(client.active_ticket)
            self.assertIsNone(client.active_operation_id)
            self.assertEqual(client.state, "CLOSED")

            client.active_ticket = "new-ticket"
            client.active_operation_id = "new-operation"
            client.state = "QUEUED"
            with patch.object(client, "_request_once", side_effect=ticket_error):
                with self.assertRaises(control.ControlClientError):
                    await client.session_cancel("old-ticket")
            self.assertEqual(client.active_ticket, "new-ticket")
            self.assertEqual(client.active_operation_id, "new-operation")
            self.assertEqual(client.state, "QUEUED")

            client.active_lease_token = "old-lease"
            client.active_ticket = None
            client.active_operation_id = "lease-operation"
            client.state = "ACTIVE"
            lease_error = control.ControlClientError(
                "lease_expired", request_stage="post_request", http_bytes_sent=1
            )
            with patch.object(client, "_request_once", side_effect=lease_error):
                with self.assertRaises(control.ControlClientError):
                    await client.session_heartbeat("old-lease")
            self.assertIsNone(client.active_lease_token)
            self.assertIsNone(client.active_operation_id)
            self.assertEqual(client.state, "CLOSED")

    async def test_direct_acquire_ambiguity_tombstones_its_operation(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        ambiguous = control.ControlClientError(
            "daemon_response_ambiguous",
            request_stage="post_request",
            http_bytes_sent=1,
        )

        async def session_call(
            path: str,
            payload: dict[str, object] | None = None,
            **_kwargs: object,
        ) -> dict[str, object]:
            if path == "/session/acquire":
                raise ambiguous
            if path == "/session/cancel-operation":
                operation_id = (payload or {})["operation_id"]
                return {"cancelled": True, "operation_id": operation_id}
            self.fail(path)

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            session_call_mock = AsyncMock(side_effect=session_call)
            with patch.object(client, "_session_call", session_call_mock):
                with self.assertRaises(control.ControlClientError) as raised:
                    await client.session_acquire("dayz-test")

        self.assertIs(raised.exception, ambiguous)
        self.assertEqual(
            [call.args[0] for call in session_call_mock.await_args_list],
            ["/session/acquire", "/session/cancel-operation"],
        )
        operation_id = session_call_mock.await_args_list[0].args[1]["operation_id"]
        self.assertEqual(
            session_call_mock.await_args_list[1].args[1]["operation_id"],
            operation_id,
        )
        self.assertEqual(client.state, "CLOSED")

    async def test_task_cancellation_waits_for_operation_tombstone(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        enqueue_started = asyncio.Event()
        cancel_started = asyncio.Event()
        allow_tombstone = asyncio.Event()
        operation_ids: list[str] = []

        async def session_call(
            path: str,
            payload: dict[str, object] | None = None,
            **_kwargs: object,
        ) -> dict[str, object]:
            request_payload = payload or {}
            if path == "/session/enqueue":
                operation_ids.append(str(request_payload["operation_id"]))
                enqueue_started.set()
                await asyncio.Event().wait()
            if path == "/session/cancel-operation":
                self.assertEqual(
                    request_payload["operation_id"], operation_ids[0]
                )
                cancel_started.set()
                await allow_tombstone.wait()
                return {
                    "cancelled": True,
                    "operation_id": request_payload["operation_id"],
                }
            self.fail(path)

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            with patch.object(client, "_session_call", side_effect=session_call):
                task = asyncio.create_task(client.session_acquire_wait("dayz-test"))
                await enqueue_started.wait()
                task.cancel()
                await cancel_started.wait()
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                allow_tombstone.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        self.assertEqual(client.state, "CLOSED")
        self.assertIsNone(client.active_operation_id)
        self.assertIsNone(client.active_ticket)
        self.assertIsNone(client.active_lease_token)

    async def test_transition_conflict_and_stale_cleanup_preserve_current_state(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            client.active_lease_token = "lease-current"
            client.active_operation_id = "operation-current"
            client.state = "RELEASING"
            call = AsyncMock(
                return_value={"status": "active", "lease_token": "unexpected"}
            )
            with patch.object(client, "_session_call", call):
                with self.assertRaises(control.ControlClientError) as raised:
                    await client.session_acquire("second")
            self.assertEqual(raised.exception.code, "session_transition_conflict")
            call.assert_awaited_once_with("/session/status")

            client._clear_operation("operation-stale")
            self.assertEqual(client.state, "RELEASING")
            self.assertEqual(client.active_operation_id, "operation-current")
            self.assertEqual(client.active_lease_token, "lease-current")

    async def test_idempotent_reacquire_preserves_existing_operation_identity(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="codex",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        cases = (
            (
                "ACTIVE",
                "lease-current",
                None,
                {"status": "active", "lease_token": "lease-current"},
            ),
            (
                "QUEUED",
                None,
                "ticket-current",
                {"status": "queued", "ticket": "ticket-current"},
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            for state, lease, ticket, response in cases:
                with self.subTest(state=state):
                    client = control.ControlClient(
                        policy=_policy(keyfile), identity=identity
                    )
                    client.state = state
                    client.active_operation_id = "operation-current"
                    client.active_lease_token = lease
                    client.active_ticket = ticket
                    authoritative = _clean_session_status()
                    if state == "ACTIVE":
                        authoritative["self"] = {
                            "state": "active",
                            "lease_id": "lease-id-current",
                            "position": 0,
                        }
                    else:
                        authoritative["self"] = {
                            "state": "queued",
                            "ticket": "ticket-current",
                            "position": 1,
                        }
                    call = AsyncMock(
                        side_effect=(authoritative, response)
                    )
                    with patch.object(client, "_session_call", call):
                        await client.session_acquire("same-session")

                    call.assert_any_await("/session/status")
                    self.assertEqual(
                        call.await_args.args[1]["operation_id"],
                        "operation-current",
                    )
                    self.assertEqual(
                        client.active_operation_id, "operation-current"
                    )

    async def test_idempotent_reacquire_rejects_state_regression_or_identity_drift(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="codex",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        cases = (
            (
                "ACTIVE",
                "lease-current",
                None,
                {"status": "queued", "ticket": "ticket-new"},
            ),
            (
                "ACTIVE",
                "lease-current",
                None,
                {"status": "active", "lease_token": "lease-drifted"},
            ),
            (
                "QUEUED",
                None,
                "ticket-current",
                {"status": "queued", "ticket": "ticket-drifted"},
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            for state, lease, ticket, response in cases:
                with self.subTest(state=state, response=response):
                    client = control.ControlClient(
                        policy=_policy(keyfile), identity=identity
                    )
                    client.state = state
                    client.active_operation_id = "operation-current"
                    client.active_lease_token = lease
                    client.active_ticket = ticket
                    authoritative = _clean_session_status()
                    if state == "ACTIVE":
                        authoritative["self"] = {
                            "state": "active",
                            "lease_id": "lease-id-current",
                            "position": 0,
                        }
                    else:
                        authoritative["self"] = {
                            "state": "queued",
                            "ticket": "ticket-current",
                            "position": 1,
                        }
                    with patch.object(
                        client,
                        "_session_call",
                        AsyncMock(side_effect=(authoritative, response)),
                    ):
                        with self.assertRaises(control.ControlClientError) as raised:
                            await client.session_acquire("same-session")

                    self.assertEqual(
                        raised.exception.code, "daemon_bad_session_response"
                    )
                    self.assertEqual(client.state, state)
                    self.assertEqual(client.active_operation_id, "operation-current")
                    self.assertEqual(client.active_lease_token, lease)
                    self.assertEqual(client.active_ticket, ticket)

    async def test_cleanup_waits_for_terminal_tombstone_without_local_deadline(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="codex",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            client.state = "QUEUED"
            client.active_operation_id = "operation-current"
            client.active_ticket = "ticket-current"
            responses = iter(
                [{"resolving": True} for _ in range(10)]
                + [
                    {
                        "cancelled": True,
                        "operation_id": "operation-current",
                    }
                ]
            )
            session_call = AsyncMock(side_effect=lambda *_args, **_kwargs: next(responses))
            clock = iter(float(value) for value in range(100))
            with (
                patch.object(client, "_session_call", session_call),
                patch.object(control, "_monotonic", side_effect=lambda: next(clock)),
                patch.object(control.asyncio, "sleep", new=AsyncMock()),
            ):
                await client._cancel_operation_until_resolved("operation-current")

        self.assertEqual(session_call.await_count, 11)
        self.assertEqual(client.state, "CLOSED")
        self.assertIsNone(client.active_operation_id)
        self.assertIsNone(client.active_ticket)

    async def test_wait_rejects_nonmatching_ticket_without_overwriting_current_queue(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="codex",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            client.state = "QUEUED"
            client.active_operation_id = "operation-current"
            client.active_ticket = "ticket-current"
            call = AsyncMock(
                return_value={"status": "active", "lease_token": "lease-stale"}
            )
            with patch.object(client, "_session_call", call):
                with self.assertRaises(control.ControlClientError) as raised:
                    await client.session_wait("ticket-stale")

        self.assertEqual(raised.exception.code, "session_transition_conflict")
        call.assert_not_awaited()
        self.assertEqual(client.state, "QUEUED")
        self.assertEqual(client.active_operation_id, "operation-current")
        self.assertEqual(client.active_ticket, "ticket-current")
        self.assertIsNone(client.active_lease_token)

    async def test_reconcile_manual_close_fences_only_exact_local_operation(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="codex",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-24T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="sub-brz",
        )
        operation_id = "11111111-1111-4111-8111-111111111111"
        foreign_owner = {
            "lease_id": "foreign-lease-id",
            "client": {"session_id": "foreign-session"},
            "state": "active",
        }
        responses = iter(
            (
                _clean_session_status(owner=foreign_owner),
                {"cancelled": True, "operation_id": operation_id},
                _clean_session_status(owner=foreign_owner),
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            client.state = "ACTIVE"
            client.active_operation_id = operation_id
            client.active_lease_token = "stale-local-token"
            session_call = AsyncMock(
                side_effect=lambda *_args, **_kwargs: next(responses)
            )
            with patch.object(client, "_session_call", session_call):
                first = await client.reconcile_idle_session()
                second = await client.reconcile_idle_session()

        self.assertEqual(first, {"reconciled": True})
        self.assertEqual(second, {"reconciled": False})
        self.assertEqual(
            [call.args[0] for call in session_call.await_args_list],
            ["/session/status", "/session/cancel-operation", "/session/status"],
        )
        self.assertEqual(
            session_call.await_args_list[1].args[1],
            {"operation_id": operation_id},
        )
        self.assertNotIn(
            "stale-local-token", repr(session_call.await_args_list[1].args[1])
        )
        self.assertEqual(client.state, "CLOSED")
        self.assertIsNone(client.active_operation_id)
        self.assertIsNone(client.active_ticket)
        self.assertIsNone(client.active_lease_token)

    async def test_reconcile_accepts_clean_daemon_generation_change(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="codex",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-24T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="sub-brz",
        )
        operation_id = "22222222-2222-4222-8222-222222222222"
        responses = iter(
            (
                _clean_session_status(daemon_generation="generation-before"),
                {"cancelled": True, "operation_id": operation_id},
                _clean_session_status(daemon_generation="generation-after"),
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            client.state = "ACTIVE"
            client.active_operation_id = operation_id
            client.active_lease_token = "expired-local-token"
            with patch.object(
                client,
                "_session_call",
                AsyncMock(side_effect=lambda *_args, **_kwargs: next(responses)),
            ):
                result = await client.reconcile_idle_session()

        self.assertEqual(result, {"reconciled": True})
        self.assertEqual(client.state, "CLOSED")
        self.assertIsNone(client.active_operation_id)
        self.assertIsNone(client.active_lease_token)

    async def test_reconcile_fails_closed_for_real_or_unprovable_conflicts(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="codex",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-24T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="sub-brz",
        )
        active = _clean_session_status()
        active["self"] = {
            "state": "active",
            "lease_id": "authoritative-lease",
            "position": 0,
        }
        pending = _clean_session_status()
        pending["pending_commands"] = 1
        audit_fault = _clean_session_status()
        audit_fault["audit_fault"] = {"fault_id": "fault-a"}
        lifecycle_fault = _clean_session_status()
        lifecycle_fault["lifecycle_recovery_fault"] = {"fault_id": "fault-b"}
        degraded = _clean_session_status()
        degraded["cleanup_degraded"] = ["release_failed"]
        malformed_generation = _clean_session_status()
        malformed_generation["daemon_generation"] = ""
        cases = (
            ("authoritative_active", active),
            ("pending_command", pending),
            ("audit_fault", audit_fault),
            ("lifecycle_fault", lifecycle_fault),
            ("cleanup_degraded", degraded),
            ("malformed_generation", malformed_generation),
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            for label, status in cases:
                with self.subTest(label=label):
                    client = control.ControlClient(
                        policy=_policy(keyfile), identity=identity
                    )
                    client.state = "ACTIVE"
                    client.active_operation_id = (
                        "33333333-3333-4333-8333-333333333333"
                    )
                    client.active_lease_token = "local-token"
                    session_call = AsyncMock(return_value=status)
                    with patch.object(client, "_session_call", session_call):
                        with self.assertRaises(
                            control.ControlClientError
                        ) as raised:
                            await client.reconcile_idle_session()
                    self.assertEqual(
                        raised.exception.code, "session_transition_conflict"
                    )
                    session_call.assert_awaited_once_with("/session/status")
                    self.assertEqual(client.state, "ACTIVE")
                    self.assertEqual(
                        client.active_operation_id,
                        "33333333-3333-4333-8333-333333333333",
                    )
                    self.assertEqual(client.active_lease_token, "local-token")

            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            client.state = "ACTIVE"
            client.active_lease_token = "local-token-without-operation"
            session_call = AsyncMock()
            with patch.object(client, "_session_call", session_call):
                with self.assertRaises(control.ControlClientError) as raised:
                    await client.reconcile_idle_session()
            self.assertEqual(raised.exception.code, "session_transition_conflict")
            session_call.assert_not_awaited()
            self.assertEqual(client.state, "ACTIVE")
            self.assertEqual(
                client.active_lease_token, "local-token-without-operation"
            )

    async def test_reconcile_requires_exact_fence_and_final_idle_proof(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="codex",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-24T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="sub-brz",
        )
        operation_id = "66666666-6666-4666-8666-666666666666"
        authoritative_active = _clean_session_status()
        authoritative_active["self"] = {
            "state": "active",
            "lease_id": "authoritative-lease",
            "position": 0,
        }
        cases = (
            (
                "mismatched_fence",
                (
                    _clean_session_status(),
                    {
                        "cancelled": True,
                        "operation_id": "77777777-7777-4777-8777-777777777777",
                    },
                ),
                "daemon_bad_session_response",
            ),
            (
                "non_idle_final_status",
                (
                    _clean_session_status(),
                    {"cancelled": True, "operation_id": operation_id},
                    authoritative_active,
                ),
                "session_transition_conflict",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            for label, scripted, expected_code in cases:
                with self.subTest(label=label):
                    responses = iter(scripted)
                    client = control.ControlClient(
                        policy=_policy(keyfile), identity=identity
                    )
                    client.state = "ACTIVE"
                    client.active_operation_id = operation_id
                    client.active_lease_token = "local-token"
                    with patch.object(
                        client,
                        "_session_call",
                        AsyncMock(
                            side_effect=lambda *_args, **_kwargs: next(responses)
                        ),
                    ):
                        with self.assertRaises(
                            control.ControlClientError
                        ) as raised:
                            await client.reconcile_idle_session()
                    self.assertEqual(raised.exception.code, expected_code)
                    self.assertEqual(client.state, "ACTIVE")
                    self.assertEqual(client.active_operation_id, operation_id)
                    self.assertEqual(client.active_lease_token, "local-token")

    async def test_acquire_wait_recovers_expired_lease_then_uses_new_operation(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="codex",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-24T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="sub-brz",
        )
        stale_operation_id = "44444444-4444-4444-8444-444444444444"
        new_operation_id = "55555555-5555-4555-8555-555555555555"
        responses = iter(
            (
                _clean_session_status(),
                {"cancelled": True, "operation_id": stale_operation_id},
                _clean_session_status(),
                {
                    "status": "queued",
                    "ticket": "new-ticket",
                    "operation_id": new_operation_id,
                    "position": 1,
                },
                {
                    "status": "active",
                    "lease_token": "new-lease-token",
                    "ticket": "new-ticket",
                    "operation_id": new_operation_id,
                },
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            client.state = "ACTIVE"
            client.active_operation_id = stale_operation_id
            client.active_lease_token = "expired-local-token"
            session_call = AsyncMock(
                side_effect=lambda *_args, **_kwargs: next(responses)
            )
            with patch.object(client, "_session_call", session_call), patch.object(
                control.uuid, "uuid4", return_value=uuid.UUID(new_operation_id)
            ):
                result = await client.session_acquire_wait(
                    "dayz-test", max_wait_s=1.0
                )

        self.assertEqual(result["status"], "active")
        self.assertEqual(
            [call.args[0] for call in session_call.await_args_list],
            [
                "/session/status",
                "/session/cancel-operation",
                "/session/status",
                "/session/enqueue",
                "/session/wait",
            ],
        )
        self.assertEqual(
            session_call.await_args_list[1].args[1],
            {"operation_id": stale_operation_id},
        )
        self.assertEqual(
            session_call.await_args_list[3].args[1]["operation_id"],
            new_operation_id,
        )
        self.assertEqual(client.state, "ACTIVE")
        self.assertEqual(client.active_operation_id, new_operation_id)
        self.assertEqual(client.active_lease_token, "new-lease-token")

    async def test_direct_acquire_recovers_orphan_operation_before_new_request(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="codex",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-24T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="sub-brz",
        )
        stale_operation_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        new_operation_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        responses = iter(
            (
                _clean_session_status(),
                {"cancelled": True, "operation_id": stale_operation_id},
                _clean_session_status(),
                {
                    "status": "active",
                    "lease_token": "new-lease-token",
                    "operation_id": new_operation_id,
                },
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            client.state = "NEW"
            client.active_operation_id = stale_operation_id
            session_call = AsyncMock(
                side_effect=lambda *_args, **_kwargs: next(responses)
            )
            with patch.object(client, "_session_call", session_call), patch.object(
                control.uuid, "uuid4", return_value=uuid.UUID(new_operation_id)
            ):
                result = await client.session_acquire("direct")

        self.assertEqual(result["status"], "active")
        self.assertEqual(
            [call.args[0] for call in session_call.await_args_list],
            [
                "/session/status",
                "/session/cancel-operation",
                "/session/status",
                "/session/acquire",
            ],
        )
        self.assertEqual(
            session_call.await_args_list[1].args[1],
            {"operation_id": stale_operation_id},
        )
        self.assertEqual(
            session_call.await_args_list[3].args[1]["operation_id"],
            new_operation_id,
        )
        self.assertEqual(client.state, "ACTIVE")
        self.assertEqual(client.active_operation_id, new_operation_id)
        self.assertEqual(client.active_lease_token, "new-lease-token")

    async def test_direct_acquire_recovers_expired_structural_lease(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="codex",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-24T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="sub-brz",
        )
        stale_operation_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        new_operation_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        responses = iter(
            (
                _clean_session_status(),
                {"cancelled": True, "operation_id": stale_operation_id},
                _clean_session_status(),
                {
                    "status": "active",
                    "lease_token": "replacement-lease-token",
                    "operation_id": new_operation_id,
                },
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            client.state = "ACTIVE"
            client.active_operation_id = stale_operation_id
            client.active_lease_token = "expired-local-token"
            session_call = AsyncMock(
                side_effect=lambda *_args, **_kwargs: next(responses)
            )
            with patch.object(client, "_session_call", session_call), patch.object(
                control.uuid, "uuid4", return_value=uuid.UUID(new_operation_id)
            ):
                result = await client.session_acquire("direct-after-expiry")

        self.assertEqual(result["status"], "active")
        self.assertEqual(
            [call.args[0] for call in session_call.await_args_list],
            [
                "/session/status",
                "/session/cancel-operation",
                "/session/status",
                "/session/acquire",
            ],
        )
        self.assertEqual(
            session_call.await_args_list[1].args[1],
            {"operation_id": stale_operation_id},
        )
        self.assertEqual(client.state, "ACTIVE")
        self.assertEqual(client.active_operation_id, new_operation_id)
        self.assertEqual(
            client.active_lease_token, "replacement-lease-token"
        )

    async def test_ambiguous_release_is_reconciled_before_next_operation(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        identity = control.ControlIdentity(
            platform="codex",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-24T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="sub-brz",
        )
        stale_operation_id = "88888888-8888-4888-8888-888888888888"
        new_operation_id = "99999999-9999-4999-8999-999999999999"
        ambiguous = control.ControlClientError(
            "daemon_response_ambiguous",
            request_stage="post_request",
            http_bytes_sent=1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            client.state = "ACTIVE"
            client.active_operation_id = stale_operation_id
            client.active_lease_token = "prior-local-token"
            with patch.object(client, "_session_call", AsyncMock(side_effect=ambiguous)):
                with self.assertRaises(control.ControlClientError) as raised:
                    await client.session_release("prior-local-token")
            self.assertIs(raised.exception, ambiguous)
            self.assertEqual(client.state, "RELEASING")
            self.assertEqual(client.active_operation_id, stale_operation_id)

            responses = iter(
                (
                    _clean_session_status(),
                    {"cancelled": True, "operation_id": stale_operation_id},
                    _clean_session_status(),
                    {
                        "status": "queued",
                        "ticket": "replacement-ticket",
                        "operation_id": new_operation_id,
                        "position": 1,
                    },
                    {
                        "status": "active",
                        "lease_token": "replacement-token",
                        "ticket": "replacement-ticket",
                        "operation_id": new_operation_id,
                    },
                )
            )
            session_call = AsyncMock(
                side_effect=lambda *_args, **_kwargs: next(responses)
            )
            with patch.object(client, "_session_call", session_call), patch.object(
                control.uuid, "uuid4", return_value=uuid.UUID(new_operation_id)
            ):
                result = await client.session_acquire_wait("dayz-test")

        self.assertEqual(result["status"], "active")
        self.assertEqual(
            [call.args[0] for call in session_call.await_args_list],
            [
                "/session/status",
                "/session/cancel-operation",
                "/session/status",
                "/session/enqueue",
                "/session/wait",
            ],
        )
        self.assertEqual(client.state, "ACTIVE")
        self.assertEqual(client.active_operation_id, new_operation_id)
        self.assertEqual(client.active_lease_token, "replacement-token")


if __name__ == "__main__":
    unittest.main()
