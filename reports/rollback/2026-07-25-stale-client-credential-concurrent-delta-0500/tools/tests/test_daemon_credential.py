from __future__ import annotations

import hashlib
import hmac
import importlib
import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.accredited_daemon_transport import AccreditedTransportError
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
        "keyfile": str(keyfile.resolve()),
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
        keyfile=str(keyfile.resolve()),
        native_executable=r"P:\Runtime\python.exe",
        argv=tuple(authority["argv"]),
        cwd=r"P:\DayZ_MCP_dev\tools",
        security_build_id=None,
        authority_sha256=authority_sha256,
    )


def _rotate(keyfile: Path, value: str) -> None:
    replacement = keyfile.with_suffix(".next")
    replacement.write_text(value, encoding="utf-8")
    replacement.replace(keyfile)


class RefreshingDaemonCredentialTest(unittest.TestCase):
    def _types(self):
        spec = importlib.util.find_spec("dayz_mcp.daemon_credential")
        self.assertIsNotNone(
            spec, "dayz_mcp.daemon_credential must implement H12 recovery"
        )
        module = importlib.import_module("dayz_mcp.daemon_credential")
        provider_type = getattr(module, "RefreshingDaemonCredential", None)
        error_type = getattr(module, "DaemonCredentialError", None)
        self.assertIsNotNone(provider_type)
        self.assertIsNotNone(error_type)
        return module, provider_type, error_type

    @staticmethod
    def _request(provider, request_fn, **overrides):
        values = {
            "method": "POST",
            "path": "/session/status",
            "query": {"read": "1"},
            "body": b'{"identity":{"session_id":"stable-session"}}',
            "headers": {"Content-Type": "application/json"},
            "deadline": time.monotonic() + 30.0,
        }
        values.update(overrides)
        return provider.exchange(request_fn=request_fn, **values)

    def test_rotation_replays_exact_request_once_with_constant_marker(self) -> None:
        module, provider_type, _error_type = self._types()
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("credential-a", encoding="utf-8")
            provider = provider_type(policy=_policy(keyfile))
            _rotate(keyfile, "credential-b")

            def request(**kwargs: object) -> tuple[int, bytes]:
                calls.append(dict(kwargs))
                if hmac.compare_digest(str(kwargs["key"]), "credential-a"):
                    return 401, b'{"error":"unauthorized"}'
                return 200, b'{"ok":true}'

            status, body = self._request(provider, request)

        self.assertEqual((status, body), (200, b'{"ok":true}'))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["key"], "credential-a")
        self.assertEqual(calls[1]["key"], "credential-b")
        for name in (
            "host",
            "port",
            "method",
            "path",
            "query",
            "body",
            "deadline",
            "expected_executable",
            "expected_argv",
            "expected_cwd",
        ):
            self.assertEqual(calls[0][name], calls[1][name], name)
        self.assertEqual(calls[0]["headers"], {"Content-Type": "application/json"})
        self.assertEqual(
            calls[1]["headers"],
            {
                "Content-Type": "application/json",
                module.CREDENTIAL_RETRY_HEADER: module.CREDENTIAL_RETRY_VALUE,
            },
        )

    def test_changed_key_second_401_fails_after_exactly_one_retry(self) -> None:
        _module, provider_type, error_type = self._types()
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("credential-a", encoding="utf-8")
            provider = provider_type(policy=_policy(keyfile))
            _rotate(keyfile, "credential-b")

            def request(**kwargs: object) -> tuple[int, bytes]:
                calls.append(dict(kwargs))
                return 401, b'{"error":"unauthorized"}'

            with self.assertRaises(error_type) as raised:
                self._request(provider, request)

        self.assertEqual(
            raised.exception.code, "stale_client_credential_retry_rejected"
        )
        self.assertEqual(raised.exception.request_stage, "post_request")
        self.assertEqual(raised.exception.http_bytes_sent, 1)
        self.assertEqual(len(calls), 2)

    def test_unchanged_key_second_401_is_diagnostic_desynchronization(self) -> None:
        _module, provider_type, error_type = self._types()
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("credential-a", encoding="utf-8")
            provider = provider_type(policy=_policy(keyfile))

            def request(**kwargs: object) -> tuple[int, bytes]:
                calls.append(dict(kwargs))
                return 401, b'{"error":"unauthorized"}'

            with self.assertRaises(error_type) as raised:
                self._request(provider, request)

        self.assertEqual(raised.exception.code, "daemon_credential_desynchronized")
        self.assertEqual(len(calls), 2)

    def test_policy_drift_after_401_has_zero_retry_and_fails_closed(self) -> None:
        _module, provider_type, error_type = self._types()
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("credential-a", encoding="utf-8")
            policy = _policy(keyfile)
            provider = provider_type(policy=policy)

            def drift() -> None:
                raise ValueError("daemon_policy_drift")

            def request(**kwargs: object) -> tuple[int, bytes]:
                calls.append(dict(kwargs))
                object.__setattr__(policy, "_revalidation_hook", drift)
                return 401, b'{"error":"unauthorized"}'

            with self.assertRaises(error_type) as raised:
                self._request(provider, request)

        self.assertEqual(raised.exception.code, "credential_source_untrusted")
        self.assertEqual(len(calls), 1)

    def test_coherent_authority_path_drift_fails_before_any_http(self) -> None:
        _module, provider_type, error_type = self._types()
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            keyfile = root / "daemon.key"
            other_keyfile = root / "unexpected.key"
            keyfile.write_text("credential-a", encoding="utf-8")
            other_keyfile.write_text("credential-b", encoding="utf-8")
            policy = _policy(keyfile)
            provider = provider_type(policy=policy)
            replacement_policy = _policy(other_keyfile)
            object.__setattr__(policy, "keyfile", replacement_policy.keyfile)
            object.__setattr__(
                policy,
                "authority_sha256",
                replacement_policy.authority_sha256,
            )
            policy.revalidate()

            def request(**kwargs: object) -> tuple[int, bytes]:
                calls.append(dict(kwargs))
                return 200, b'{"ok":true}'

            with self.assertRaises(error_type) as raised:
                self._request(provider, request)

        self.assertEqual(raised.exception.code, "credential_source_untrusted")
        self.assertEqual(raised.exception.request_stage, "pre_request")
        self.assertEqual(raised.exception.http_bytes_sent, 0)
        self.assertEqual(calls, [])

    def test_refresh_reader_failure_has_zero_retry_and_stable_error(self) -> None:
        module, provider_type, error_type = self._types()
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("credential-a", encoding="utf-8")
            provider = provider_type(
                policy=_policy(keyfile), initial_key="credential-a"
            )

            def request(**kwargs: object) -> tuple[int, bytes]:
                calls.append(dict(kwargs))
                return 401, b'{"error":"unauthorized"}'

            with patch.object(
                module.pinned_keyfile,
                "read_pinned_keyfile",
                side_effect=ValueError("keyfile_not_canonical"),
            ), self.assertRaises(error_type) as raised:
                self._request(provider, request)

        self.assertEqual(
            raised.exception.code, "stale_client_credential_refresh_failed"
        )
        self.assertEqual(len(calls), 1)

    def test_concurrent_rotation_is_single_flight_without_loops(self) -> None:
        module, provider_type, _error_type = self._types()
        worker_count = 8
        barrier = threading.Barrier(worker_count)
        calls: list[dict[str, object]] = []
        calls_lock = threading.Lock()
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("credential-a", encoding="utf-8")
            provider = provider_type(
                policy=_policy(keyfile), initial_key="credential-a"
            )
            _rotate(keyfile, "credential-b")
            real_reader = module.pinned_keyfile.read_pinned_keyfile

            def request(**kwargs: object) -> tuple[int, bytes]:
                with calls_lock:
                    calls.append(dict(kwargs))
                if hmac.compare_digest(str(kwargs["key"]), "credential-a"):
                    barrier.wait(timeout=5.0)
                    return 401, b'{"error":"unauthorized"}'
                return 200, b'{"ok":true}'

            with patch.object(
                module.pinned_keyfile,
                "read_pinned_keyfile",
                wraps=real_reader,
            ) as read_key, ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = [
                    pool.submit(self._request, provider, request)
                    for _ in range(worker_count)
                ]
                results = [future.result(timeout=10.0) for future in futures]

        self.assertEqual(results, [(200, b'{"ok":true}')] * worker_count)
        self.assertEqual(read_key.call_count, 1)
        self.assertEqual(len(calls), worker_count * 2)
        self.assertEqual(
            sum(call["key"] == "credential-a" for call in calls), worker_count
        )
        self.assertEqual(
            sum(call["key"] == "credential-b" for call in calls), worker_count
        )

    def test_non_401_and_initial_identity_failure_are_never_retried(self) -> None:
        module, provider_type, _error_type = self._types()
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("credential-a", encoding="utf-8")
            provider = provider_type(
                policy=_policy(keyfile), initial_key="credential-a"
            )
            forbidden_reads: list[str] = []
            forbidden_retry_calls: list[dict[str, object]] = []

            with patch.object(
                module.pinned_keyfile,
                "read_pinned_keyfile",
                side_effect=forbidden_reads.append,
            ):
                status, _body = self._request(
                    provider, lambda **_kwargs: (403, b'{"error":"forbidden"}')
                )
            self.assertEqual(status, 403)
            self.assertEqual(forbidden_reads, [])

            identity_error = AccreditedTransportError(
                "daemon_identity_unverified",
                request_stage="pre_request",
                http_bytes_sent=0,
            )

            def identity_failure(**kwargs: object) -> tuple[int, bytes]:
                forbidden_retry_calls.append(dict(kwargs))
                raise identity_error

            with self.assertRaises(AccreditedTransportError) as raised:
                self._request(provider, identity_failure)

        self.assertIs(raised.exception, identity_error)
        self.assertEqual(len(forbidden_retry_calls), 1)

    def test_retry_transport_failure_is_stable_and_never_attempts_a_third_time(
        self,
    ) -> None:
        _module, provider_type, error_type = self._types()
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("credential-a", encoding="utf-8")
            provider = provider_type(policy=_policy(keyfile))
            _rotate(keyfile, "credential-b")

            def request(**kwargs: object) -> tuple[int, bytes]:
                calls.append(dict(kwargs))
                if len(calls) == 1:
                    return 401, b'{"error":"unauthorized"}'
                raise AccreditedTransportError(
                    "daemon_transport_failure",
                    request_stage="pre_request",
                    http_bytes_sent=0,
                )

            with self.assertRaises(error_type) as raised:
                self._request(provider, request)

        self.assertEqual(
            raised.exception.code,
            "stale_client_credential_retry_transport_failed",
        )
        self.assertEqual(len(calls), 2)

    def test_retry_identity_replacement_stays_distinct_without_third_attempt(
        self,
    ) -> None:
        _module, provider_type, _error_type = self._types()
        calls: list[dict[str, object]] = []
        identity_error = AccreditedTransportError(
            "daemon_identity_unverified",
            request_stage="pre_request",
            http_bytes_sent=0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("credential-a", encoding="utf-8")
            provider = provider_type(policy=_policy(keyfile))
            _rotate(keyfile, "credential-b")

            def request(**kwargs: object) -> tuple[int, bytes]:
                calls.append(dict(kwargs))
                if len(calls) == 1:
                    return 401, b'{"error":"unauthorized"}'
                raise identity_error

            with self.assertRaises(AccreditedTransportError) as raised:
                self._request(provider, request)

        self.assertIs(raised.exception, identity_error)
        self.assertEqual(raised.exception.code, "daemon_identity_unverified")
        self.assertEqual(len(calls), 2)

    def test_provider_and_errors_never_render_credentials(self) -> None:
        _module, provider_type, error_type = self._types()
        secrets = ("credential-a-secret", "credential-b-secret")
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text(secrets[0], encoding="utf-8")
            provider = provider_type(policy=_policy(keyfile))
            _rotate(keyfile, secrets[1])

            with self.assertRaises(error_type) as raised:
                self._request(
                    provider,
                    lambda **_kwargs: (401, b'{"error":"unauthorized"}'),
                )

        rendered = "\n".join(
            (str(provider), repr(provider), str(raised.exception), repr(raised.exception))
        )
        for secret in secrets:
            self.assertNotIn(secret, rendered)


if __name__ == "__main__":
    unittest.main()
