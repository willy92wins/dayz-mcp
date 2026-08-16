from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from dayz_mcp import accredited_daemon_transport
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
            authority,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
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


class RefreshingDaemonCredentialTests(unittest.TestCase):
    def test_refresh_error_accepts_only_sanitized_stable_codes(self) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        with self.assertRaisesRegex(
            ValueError,
            "^invalid_credential_refresh_error$",
        ):
            credential_module.CredentialRefreshError(
                "fixture-secret-must-not-be-public"
            )

    def test_refresh_error_code_only_defaults_to_post_request_metadata(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        error = credential_module.CredentialRefreshError(
            "stale_client_credential_retry_transport_failed"
        )

        self.assertEqual(
            (
                getattr(error, "request_stage", None),
                getattr(error, "http_bytes_sent", None),
            ),
            ("post_request", 1),
        )

    def test_accredited_401_refreshes_same_keyfile_and_retries_once(self) -> None:
        try:
            credential_module = importlib.import_module(
                "dayz_mcp.daemon_credential"
            )
        except ModuleNotFoundError:
            self.fail("RefreshingDaemonCredential is not implemented")

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
            provider = credential_module.RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=request,
            )
            for secret in (first_key, second_key):
                self.assertNotIn(secret, repr(provider))
                self.assertNotIn(secret, repr(provider._snapshot))
            keyfile.write_text(second_key + "\n", encoding="utf-8")

            status, body = provider.request_with_refresh(
                method="POST",
                path="/session/status",
                query={"public": "value"},
                body=b'{"operation":"same"}',
                headers={"Content-Type": "application/json"},
                deadline=1234.5,
            )

        self.assertEqual((status, body), (200, b'{"status":"ok"}'))
        self.assertEqual(len(calls), 2)
        self.assertTrue(hmac.compare_digest(str(calls[0]["key"]), first_key))
        self.assertTrue(hmac.compare_digest(str(calls[1]["key"]), second_key))
        for call in calls:
            self.assertEqual(call["method"], "POST")
            self.assertEqual(call["path"], "/session/status")
            self.assertEqual(call["query"], {"public": "value"})
            self.assertEqual(call["body"], b'{"operation":"same"}')
            self.assertEqual(call["deadline"], 1234.5)
        self.assertEqual(
            calls[0]["headers"], {"Content-Type": "application/json"}
        )
        self.assertEqual(
            calls[1]["headers"],
            {
                "Content-Type": "application/json",
                "X-DayZ-MCP-Credential-Retry": "1",
            },
        )

    def test_request_envelope_is_snapshotted_once_before_the_first_attempt(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        first_key = "fixture-credential-a"
        second_key = "fixture-credential-b"
        query = {"stage": "original"}
        headers = {"X-Fixture": "original"}
        calls: list[dict[str, object]] = []

        def request(**kwargs: object) -> tuple[int, bytes]:
            calls.append(dict(kwargs))
            if len(calls) == 1:
                query["stage"] = "mutated"
                headers["X-Fixture"] = "mutated"
                return 401, b'{"error":"unauthorized"}'
            return 200, b'{"status":"ok"}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text(first_key + "\n", encoding="utf-8")
            provider = credential_module.RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=request,
            )
            keyfile.write_text(second_key + "\n", encoding="utf-8")

            result = provider.request_with_refresh(
                method="POST",
                path="/session/status",
                query=query,
                body=b'{"operation":"same"}',
                headers=headers,
                deadline=1234.5,
            )

        self.assertEqual(result, (200, b'{"status":"ok"}'))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["query"], {"stage": "original"})
        self.assertEqual(calls[1]["query"], {"stage": "original"})
        self.assertEqual(calls[0]["headers"], {"X-Fixture": "original"})
        self.assertEqual(
            calls[1]["headers"],
            {
                "X-Fixture": "original",
                "X-DayZ-MCP-Credential-Retry": "1",
            },
        )

    def test_replaced_legitimate_daemon_reaccredits_and_retries_once(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        calls: list[dict[str, object]] = []
        headers = {"X-Fixture": "same-request"}
        outcomes: list[object] = [
            (200, b'{"daemon_generation":"generation-a"}'),
            accredited_daemon_transport.AccreditedTransportError(
                "daemon_identity_unverified",
                request_stage="pre_request",
                http_bytes_sent=0,
            ),
            (200, b'{"daemon_generation":"generation-b"}'),
        ]

        def request(**kwargs: object) -> tuple[int, bytes]:
            calls.append(dict(kwargs))
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-credential\n", encoding="utf-8")
            provider = credential_module.RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=request,
            )
            first = provider.request_with_refresh(
                method="GET", path="/status", query={"view": "public"},
                body=None, headers=headers,
                deadline=1234.5,
            )
            second = provider.request_with_refresh(
                method="GET", path="/status", query={"view": "public"},
                body=None, headers=headers,
                deadline=1234.5,
            )

        self.assertEqual(first, (200, b'{"daemon_generation":"generation-a"}'))
        self.assertEqual(second, (200, b'{"daemon_generation":"generation-b"}'))
        self.assertEqual(len(calls), 3)
        for field in (
            "host", "port", "key", "method", "path", "query", "body",
            "deadline", "expected_executable", "expected_argv",
            "expected_cwd",
        ):
            self.assertEqual(calls[1][field], calls[2][field])
        self.assertEqual(calls[0]["headers"], headers)
        self.assertEqual(calls[1]["headers"], headers)
        self.assertEqual(
            calls[2]["headers"],
            {
                "X-Fixture": "same-request",
                "X-DayZ-MCP-Reaccredited": "1",
            },
        )
        self.assertEqual(headers, {"X-Fixture": "same-request"})

    def test_policy_drift_during_reaccreditation_fails_before_retry(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        calls = 0
        revalidations = 0

        def request(**_kwargs: object) -> tuple[int, bytes]:
            nonlocal calls
            calls += 1
            raise accredited_daemon_transport.AccreditedTransportError(
                "daemon_identity_unverified",
                request_stage="pre_request",
                http_bytes_sent=0,
            )

        def revalidate() -> None:
            nonlocal revalidations
            revalidations += 1
            if revalidations > 2:
                raise ValueError("daemon_policy_drift")

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-credential\n", encoding="utf-8")
            policy = _policy(keyfile)
            object.__setattr__(policy, "_revalidation_hook", revalidate)
            provider = credential_module.RefreshingDaemonCredential(
                policy=policy,
                request_fn=request,
            )
            with self.assertRaises(
                credential_module.CredentialRefreshError
            ) as raised:
                provider.request_with_refresh(
                    method="GET", path="/status", query={}, body=None,
                    headers={}, deadline=1234.5,
                )

        self.assertEqual(
            raised.exception.code,
            "client_policy_untrusted_open_new_session",
        )
        self.assertEqual(calls, 1)

    def test_illegitimate_daemon_fails_after_one_reaccreditation_attempt(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        calls = 0

        def request(**_kwargs: object) -> tuple[int, bytes]:
            nonlocal calls
            calls += 1
            raise accredited_daemon_transport.AccreditedTransportError(
                "daemon_identity_unverified",
                request_stage="pre_request",
                http_bytes_sent=0,
            )

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-credential\n", encoding="utf-8")
            provider = credential_module.RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=request,
            )
            with self.assertRaises(
                credential_module.CredentialRefreshError
            ) as raised:
                provider.request_with_refresh(
                    method="GET", path="/status", query={}, body=None,
                    headers={}, deadline=1234.5,
                )

        self.assertEqual(
            raised.exception.code,
            "daemon_reaccreditation_failed_open_new_session",
        )
        self.assertEqual(calls, 2)

    def test_reaccreditation_does_not_chain_into_a_third_auth_attempt(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        calls = 0

        def request(**_kwargs: object) -> tuple[int, bytes]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise accredited_daemon_transport.AccreditedTransportError(
                    "daemon_identity_unverified",
                    request_stage="pre_request",
                    http_bytes_sent=0,
                )
            return 401, b'{"error":"unauthorized"}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-credential\n", encoding="utf-8")
            provider = credential_module.RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=request,
            )
            with self.assertRaises(
                credential_module.CredentialRefreshError
            ) as raised:
                provider.request_with_refresh(
                    method="GET", path="/status", query={}, body=None,
                    headers={}, deadline=1234.5,
                )

        self.assertEqual(
            raised.exception.code,
            "daemon_reaccreditation_failed_open_new_session",
        )
        self.assertEqual(calls, 2)

    def test_reaccreditation_retry_uses_original_exhausted_deadline(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        deadlines: list[float] = []

        def request(**kwargs: object) -> tuple[int, bytes]:
            deadlines.append(float(kwargs["deadline"]))
            if len(deadlines) == 1:
                raise accredited_daemon_transport.AccreditedTransportError(
                    "daemon_identity_unverified",
                    request_stage="pre_request",
                    http_bytes_sent=0,
                )
            raise TimeoutError("daemon_request_deadline_exceeded")

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-credential\n", encoding="utf-8")
            provider = credential_module.RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=request,
            )
            with self.assertRaises(
                credential_module.CredentialRefreshError
            ) as raised:
                provider.request_with_refresh(
                    method="GET", path="/status", query={}, body=None,
                    headers={}, deadline=12.5,
                )

        self.assertEqual(
            raised.exception.code,
            "daemon_reaccreditation_failed_open_new_session",
        )
        self.assertEqual(deadlines, [12.5, 12.5])

    def test_replaced_policy_cannot_adopt_an_unexpected_authority(self) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        calls: list[dict[str, object]] = []

        def request(**kwargs: object) -> tuple[int, bytes]:
            calls.append(dict(kwargs))
            return 401, b'{"error":"unauthorized"}'

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_keyfile = root / "daemon.key"
            unexpected_keyfile = root / "unexpected.key"
            original_keyfile.write_text(
                "fixture-credential-a\n",
                encoding="utf-8",
            )
            unexpected_keyfile.write_text(
                "fixture-credential-b\n",
                encoding="utf-8",
            )
            provider = credential_module.RefreshingDaemonCredential(
                policy=_policy(original_keyfile),
                request_fn=request,
            )
            provider.policy = _policy(unexpected_keyfile)

            with self.assertRaisesRegex(
                credential_module.CredentialRefreshError,
                "^client_policy_untrusted_open_new_session$",
            ):
                provider.request_with_refresh(
                    method="GET",
                    path="/status",
                    query={},
                    body=None,
                    headers={},
                    deadline=1234.5,
                )

        self.assertEqual(calls, [])

    def test_policy_substitute_with_matching_authority_is_rejected(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        calls: list[dict[str, object]] = []

        class PolicySubstitute:
            pass

        def request(**kwargs: object) -> tuple[int, bytes]:
            calls.append(dict(kwargs))
            return 200, b'{"status":"ok"}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text(
                "fixture-credential\n",
                encoding="utf-8",
            )
            policy = _policy(keyfile)
            provider = credential_module.RefreshingDaemonCredential(
                policy=policy,
                request_fn=request,
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
                setattr(substitute, attribute, getattr(policy, attribute))
            provider.policy = substitute

            with self.assertRaises(
                credential_module.CredentialRefreshError
            ) as raised:
                provider.request_with_refresh(
                    method="GET",
                    path="/status",
                    query={},
                    body=None,
                    headers={},
                    deadline=1234.5,
                )

        self.assertEqual(
            raised.exception.code,
            "client_policy_untrusted_open_new_session",
        )
        self.assertEqual(calls, [])

    def test_persistent_401_has_one_retry_and_one_stable_error(self) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        for rotate in (False, True):
            calls: list[dict[str, object]] = []

            def request(**kwargs: object) -> tuple[int, bytes]:
                calls.append(dict(kwargs))
                return 401, b'{"error":"unauthorized"}'

            expected_code = (
                "stale_client_credential_retry_rejected"
                if rotate
                else "daemon_credential_desynchronized"
            )
            with self.subTest(credential_changed=rotate):
                with tempfile.TemporaryDirectory() as temporary:
                    keyfile = Path(temporary) / "daemon.key"
                    keyfile.write_text(
                        "fixture-credential-a\n",
                        encoding="utf-8",
                    )
                    provider = credential_module.RefreshingDaemonCredential(
                        policy=_policy(keyfile),
                        request_fn=request,
                    )
                    if rotate:
                        keyfile.write_text(
                            "fixture-credential-b\n",
                            encoding="utf-8",
                        )

                    with self.assertRaises(
                        credential_module.CredentialRefreshError
                    ) as raised:
                        provider.request_with_refresh(
                            method="GET",
                            path="/status",
                            query={},
                            body=None,
                            headers={},
                            deadline=1234.5,
                        )

                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(len(calls), 2)

    def test_policy_drift_after_401_fails_closed_without_retry(self) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        calls: list[dict[str, object]] = []
        revalidations = 0

        def request(**kwargs: object) -> tuple[int, bytes]:
            calls.append(dict(kwargs))
            return 401, b'{"error":"unauthorized"}'

        def revalidate() -> None:
            nonlocal revalidations
            revalidations += 1
            if revalidations > 2:
                raise ValueError("daemon_provenance_conflict")

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text(
                "fixture-credential-a\n",
                encoding="utf-8",
            )
            policy = _policy(keyfile)
            object.__setattr__(policy, "_revalidation_hook", revalidate)
            provider = credential_module.RefreshingDaemonCredential(
                policy=policy,
                request_fn=request,
            )

            try:
                provider.request_with_refresh(
                    method="GET",
                    path="/status",
                    query={},
                    body=None,
                    headers={},
                    deadline=1234.5,
                )
            except Exception as error:
                caught = error
            else:
                self.fail("policy drift did not fail closed")

        self.assertIsInstance(
            caught,
            credential_module.CredentialRefreshError,
        )
        self.assertEqual(
            str(caught),
            "client_policy_untrusted_open_new_session",
        )
        self.assertEqual(len(calls), 1)

    def test_unreadable_accredited_keyfile_fails_closed_without_retry(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        calls = 0

        def request(**_kwargs: object) -> tuple[int, bytes]:
            nonlocal calls
            calls += 1
            return 401, b'{"error":"unauthorized"}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text(
                "fixture-credential-a\n",
                encoding="utf-8",
            )
            provider = credential_module.RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=request,
            )
            keyfile.unlink()

            try:
                provider.request_with_refresh(
                    method="GET",
                    path="/status",
                    query={},
                    body=None,
                    headers={},
                    deadline=1234.5,
                )
            except Exception as error:
                caught = error
            else:
                self.fail("missing keyfile did not fail closed")

        self.assertIsInstance(
            caught,
            credential_module.CredentialRefreshError,
        )
        self.assertEqual(
            str(caught),
            "stale_client_credential_refresh_failed",
        )
        self.assertEqual(calls, 1)

    def test_retry_transport_failure_is_not_reclassified_as_unavailable(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        calls = 0

        def request(**_kwargs: object) -> tuple[int, bytes]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return 401, b'{"error":"unauthorized"}'
            raise accredited_daemon_transport.AccreditedTransportError(
                "daemon_transport_failure",
                request_stage="pre_request",
                http_bytes_sent=0,
            )

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text(
                "fixture-credential-a\n",
                encoding="utf-8",
            )
            provider = credential_module.RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=request,
            )
            keyfile.write_text(
                "fixture-credential-b\n",
                encoding="utf-8",
            )

            try:
                provider.request_with_refresh(
                    method="GET",
                    path="/status",
                    query={},
                    body=None,
                    headers={},
                    deadline=1234.5,
                )
            except Exception as error:
                caught = error
            else:
                self.fail("retry transport failure was returned as success")

        self.assertIsInstance(
            caught,
            credential_module.CredentialRefreshError,
        )
        self.assertEqual(
            str(caught),
            "stale_client_credential_retry_transport_failed",
        )
        self.assertEqual(calls, 2)

    def test_401_retry_exhausted_deadline_is_sanitized_as_transport_failure(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        calls = 0

        def request(**_kwargs: object) -> tuple[int, bytes]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return 401, b'{"error":"unauthorized"}'
            raise TimeoutError("daemon_request_deadline_exceeded")

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text(
                "fixture-credential\n",
                encoding="utf-8",
            )
            provider = credential_module.RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=request,
            )

            try:
                provider.request_with_refresh(
                    method="GET",
                    path="/status",
                    query={},
                    body=None,
                    headers={},
                    deadline=12.5,
                )
            except Exception as error:
                caught = error
            else:
                self.fail("exhausted retry deadline returned as success")

        self.assertIsInstance(
            caught,
            credential_module.CredentialRefreshError,
        )
        self.assertFalse(isinstance(caught, TimeoutError))
        self.assertEqual(
            getattr(caught, "code", None),
            "stale_client_credential_retry_transport_failed",
        )
        self.assertEqual(calls, 2)

    def test_identity_failure_never_creates_a_third_attempt(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        for fail_on_call in (1, 2):
            calls = 0

            def request(**_kwargs: object) -> tuple[int, bytes]:
                nonlocal calls
                calls += 1
                if calls == fail_on_call:
                    raise accredited_daemon_transport.AccreditedTransportError(
                        "daemon_identity_unverified",
                        request_stage="pre_request",
                        http_bytes_sent=0,
                    )
                return 401, b'{"error":"unauthorized"}'

            with self.subTest(fail_on_call=fail_on_call):
                with tempfile.TemporaryDirectory() as temporary:
                    keyfile = Path(temporary) / "daemon.key"
                    keyfile.write_text(
                        "fixture-credential-a\n",
                        encoding="utf-8",
                    )
                    provider = credential_module.RefreshingDaemonCredential(
                        policy=_policy(keyfile),
                        request_fn=request,
                    )
                    if fail_on_call == 2:
                        keyfile.write_text(
                            "fixture-credential-b\n",
                            encoding="utf-8",
                        )
                    with self.assertRaises(
                        credential_module.CredentialRefreshError
                    ) as raised:
                        provider.request_with_refresh(
                            method="GET", path="/status", query={}, body=None,
                            headers={}, deadline=1234.5,
                        )

                self.assertEqual(
                    raised.exception.code,
                    "daemon_reaccreditation_failed_open_new_session",
                )
                self.assertEqual(calls, 2)

    def test_concurrent_identity_failures_reaccredit_single_flight(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        workers = 8
        first_attempts = threading.Barrier(workers)
        attempts: dict[int, int] = {}
        attempts_lock = threading.Lock()
        revalidations = 0

        def revalidate() -> None:
            nonlocal revalidations
            with attempts_lock:
                revalidations += 1

        def request(**_kwargs: object) -> tuple[int, bytes]:
            thread_id = threading.get_ident()
            with attempts_lock:
                attempts[thread_id] = attempts.get(thread_id, 0) + 1
                attempt = attempts[thread_id]
            if attempt == 1:
                first_attempts.wait(timeout=5.0)
                raise accredited_daemon_transport.AccreditedTransportError(
                    "daemon_identity_unverified",
                    request_stage="pre_request",
                    http_bytes_sent=0,
                )
            return 200, b'{"daemon_generation":"generation-b"}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-credential\n", encoding="utf-8")
            policy = _policy(keyfile)
            object.__setattr__(policy, "_revalidation_hook", revalidate)
            provider = credential_module.RefreshingDaemonCredential(
                policy=policy,
                request_fn=request,
            )
            with ThreadPoolExecutor(max_workers=workers) as executor:
                results = list(
                    executor.map(
                        lambda _index: provider.request_with_refresh(
                            method="GET", path="/status", query={}, body=None,
                            headers={}, deadline=1234.5,
                        ),
                        range(workers),
                    )
                )

        self.assertEqual(
            results,
            [(200, b'{"daemon_generation":"generation-b"}')] * workers,
        )
        self.assertEqual(revalidations, 3)
        self.assertEqual(len(attempts), workers)
        self.assertTrue(all(count == 2 for count in attempts.values()))

    def test_concurrent_401s_publish_one_refresh_epoch(self) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        first_key = "fixture-credential-a"
        second_key = "fixture-credential-b"
        workers = 8
        first_attempts = threading.Barrier(workers)
        attempts: dict[int, int] = {}
        attempts_lock = threading.Lock()

        def request(**kwargs: object) -> tuple[int, bytes]:
            thread_id = threading.get_ident()
            with attempts_lock:
                attempts[thread_id] = attempts.get(thread_id, 0) + 1
            if hmac.compare_digest(str(kwargs["key"]), first_key):
                first_attempts.wait(timeout=5.0)
                return 401, b'{"error":"unauthorized"}'
            return 200, b'{"status":"ok"}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text(first_key + "\n", encoding="utf-8")
            provider = credential_module.RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=request,
            )
            keyfile.write_text(second_key + "\n", encoding="utf-8")
            real_reader = (
                credential_module.pinned_keyfile.read_pinned_keyfile
            )
            refresh_reads = 0

            def counted_reader(path: str) -> str:
                nonlocal refresh_reads
                refresh_reads += 1
                return real_reader(path)

            with patch.object(
                credential_module.pinned_keyfile,
                "read_pinned_keyfile",
                side_effect=counted_reader,
            ):
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    results = list(
                        executor.map(
                            lambda _index: provider.request_with_refresh(
                                method="GET",
                                path="/status",
                                query={},
                                body=None,
                                headers={},
                                deadline=1234.5,
                            ),
                            range(workers),
                        )
                    )

        self.assertEqual(
            results,
            [(200, b'{"status":"ok"}')] * workers,
        )
        self.assertEqual(refresh_reads, 1)
        self.assertEqual(len(attempts), workers)
        self.assertTrue(all(count == 2 for count in attempts.values()))
        self.assertEqual(sum(attempts.values()), workers * 2)

    def test_rotation_during_retry_fails_once_then_next_call_refreshes(
        self,
    ) -> None:
        credential_module = importlib.import_module(
            "dayz_mcp.daemon_credential"
        )
        first_key = "fixture-credential-a"
        second_key = "fixture-credential-b"
        third_key = "fixture-credential-c"
        calls: list[str] = []

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text(first_key + "\n", encoding="utf-8")

            def request(**kwargs: object) -> tuple[int, bytes]:
                supplied = str(kwargs["key"])
                calls.append(supplied)
                if supplied == second_key and calls.count(second_key) == 1:
                    keyfile.write_text(third_key + "\n", encoding="utf-8")
                if supplied == third_key:
                    return 200, b'{"status":"ok"}'
                return 401, b'{"error":"unauthorized"}'

            provider = credential_module.RefreshingDaemonCredential(
                policy=_policy(keyfile),
                request_fn=request,
            )
            keyfile.write_text(second_key + "\n", encoding="utf-8")

            with self.assertRaisesRegex(
                credential_module.CredentialRefreshError,
                "^stale_client_credential_retry_rejected$",
            ):
                provider.request_with_refresh(
                    method="GET",
                    path="/status",
                    query={},
                    body=None,
                    headers={},
                    deadline=1234.5,
                )

            result = provider.request_with_refresh(
                method="GET",
                path="/status",
                query={},
                body=None,
                headers={},
                deadline=1234.5,
            )

        self.assertEqual(result, (200, b'{"status":"ok"}'))
        self.assertEqual(
            calls,
            [first_key, second_key, second_key, third_key],
        )


if __name__ == "__main__":
    unittest.main()
