from __future__ import annotations

import json
import unittest

from dayz_mcp import dayz_test_request, dayz_test_worker
from tests.dayz_test_worker_helpers import POLICY, RUNTIME


def _raw(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "version": 3,
        "dev_root": POLICY.dev_root,
        "mod": POLICY.mod,
        "mode": "client",
    }
    value.update(overrides)
    return json.dumps(value).encode("utf-8")


class ConnectHostRequestTests(unittest.TestCase):
    def test_omitted_connect_host_still_requires_local_run_id(self) -> None:
        with self.assertRaisesRegex(ValueError, r"^client_requires_run_id$"):
            dayz_test_request.parse_dayz_test_request(_raw(), policies=(POLICY,))

    def test_loopback_connect_host_still_requires_run_id(self) -> None:
        for host in ("127.0.0.1", "::1", "[::1]", "localhost"):
            with self.subTest(host=host):
                with self.assertRaisesRegex(ValueError, r"^client_requires_run_id$"):
                    dayz_test_request.parse_dayz_test_request(
                        _raw(connect_host=host), policies=(POLICY,)
                    )

    def test_remote_connect_host_allows_client_without_run_id(self) -> None:
        parsed = dayz_test_request.parse_dayz_test_request(
            _raw(connect_host="192.168.1.34"), policies=(POLICY,)
        )
        self.assertEqual(parsed.payload["connect_host"], "192.168.1.34")
        self.assertIsNone(parsed.payload["run_id"])
        self.assertEqual(parsed.payload["mode"], "client")
        again = dayz_test_request.parse_dayz_test_request(
            parsed.canonical_bytes, policies=(POLICY,)
        )
        self.assertEqual(again.payload, parsed.payload)

    def test_empty_connect_host_is_omitted_and_defaults_to_loopback_rule(self) -> None:
        with self.assertRaisesRegex(ValueError, r"^client_requires_run_id$"):
            dayz_test_request.parse_dayz_test_request(
                _raw(connect_host=""), policies=(POLICY,)
            )

    def test_invalid_connect_host_is_rejected(self) -> None:
        for host in (
            "192.168.1.34;calc",
            "192.168.1.34 host",
            'evil"host',
            123,
            ["192.168.1.34"],
            "not a host",
        ):
            with self.subTest(host=host):
                with self.assertRaisesRegex(
                    ValueError, r"invalid_dayz_test_request:connect_host_invalid$"
                ):
                    dayz_test_request.parse_dayz_test_request(
                        _raw(connect_host=host), policies=(POLICY,)
                    )

    def test_connect_host_on_server_or_all_is_rejected(self) -> None:
        for mode in ("server", "all"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(
                    ValueError,
                    r"invalid_dayz_test_request:connect_host_requires_client$",
                ):
                    dayz_test_request.parse_dayz_test_request(
                        _raw(mode=mode, connect_host="192.168.1.34"),
                        policies=(POLICY,),
                    )

    def test_loopback_connect_host_on_server_or_all_is_accepted(self) -> None:
        for mode in ("server", "all"):
            for host in ("127.0.0.1", "localhost", "::1", "[::1]"):
                with self.subTest(mode=mode, host=host):
                    parsed = dayz_test_request.parse_dayz_test_request(
                        _raw(mode=mode, connect_host=host),
                        policies=(POLICY,),
                    )
                    self.assertEqual(parsed.payload["connect_host"], host)
                    self.assertEqual(parsed.payload["mode"], mode)

    def test_v1_document_cannot_carry_connect_host(self) -> None:
        with self.assertRaisesRegex(ValueError, r":unknown_key$"):
            dayz_test_request.parse_dayz_test_request(
                _raw(version=1, connect_host="192.168.1.34"), policies=(POLICY,)
            )


class ConnectHostWorkerArgvTests(unittest.TestCase):
    def test_worker_client_argv_uses_connect_host(self) -> None:
        parsed = dayz_test_request.parse_dayz_test_request(
            _raw(connect_host="192.168.1.34"), policies=(POLICY,)
        )
        core = dayz_test_worker._start_core(
            parsed.payload, RUNTIME, role="client", run_id=None
        )
        self.assertIn("-connect=192.168.1.34", core["argv"])
        self.assertNotIn("-connect=127.0.0.1", core["argv"])

    def test_worker_client_argv_defaults_to_loopback(self) -> None:
        run_id = "12345678-1234-4234-8234-1234567890ab"
        parsed = dayz_test_request.parse_dayz_test_request(
            _raw(run_id=run_id), policies=(POLICY,)
        )
        self.assertNotIn("connect_host", parsed.payload)
        core = dayz_test_worker._start_core(
            parsed.payload, RUNTIME, role="client", run_id=run_id
        )
        self.assertIn("-connect=127.0.0.1", core["argv"])


if __name__ == "__main__":
    unittest.main()
