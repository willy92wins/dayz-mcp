"""Verify client platform aliases stop at the CLI boundary.

The daemon identity enum remains canonical while the client log retains the
declared CLI value needed to audit aliased callers.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import patch

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import control_client, host_config, server
from dayz_mcp.server import ServerConfig
from dayz_mcp.session_coordination import ClientIdentity
from tests.test_daemon import DaemonHttpServer, _config, _http


def _fixture_client_runtime(
    config: ServerConfig,
    **kwargs: object,
) -> server.ClientRuntime:
    """Construct a client without consulting live host registrations."""
    with tempfile.TemporaryDirectory() as directory:
        keyfile = Path(directory) / "daemon.key"
        keyfile.write_text(config.key or "fixture-key", encoding="utf-8")
        fixture_config = replace(config, keyfile=str(keyfile.resolve()))
        launcher = str(Path(sys.executable).resolve())
        provenance = host_config.DaemonProvenance(
            launch_executable=launcher,
            native_executable=launcher,
            argv=tuple(server.daemon.build_daemon_argv(fixture_config, python=launcher)),
            cwd=server.daemon.daemon_runtime_cwd(),
            port=fixture_config.port,
            keyfile=str(keyfile.resolve()),
            auto_spawn_daemon=fixture_config.auto_spawn_daemon,
        )
        with (
            patch.object(
                host_config,
                "resolve_daemon_provenance",
                return_value=provenance,
            ),
            patch.object(
                host_config,
                "_local_launch_executable",
                return_value=launcher,
            ),
            patch.object(
                host_config,
                "_local_native_executable",
                return_value=launcher,
            ),
        ):
            return server.ClientRuntime(fixture_config, **kwargs)


def _attach_fixture_transport(
    runtime: server.ClientRuntime,
    daemon_server: DaemonHttpServer,
) -> None:
    request = lambda method, path, payload=None, query=None, timeout=5.0: _http(
        daemon_server.base,
        method,
        path,
        daemon_server.key,
        payload=payload,
        query=query,
        timeout=timeout,
    )
    runtime._request_once = request

    def control_request(path, payload, timeout_s):
        status, response = request("POST", path, payload, None, timeout_s)
        if status not in (200, 202):
            raise control_client.ControlClientError(
                server._remote_error_code(response),
                request_stage="post_request",
                http_bytes_sent=1,
            )
        return response

    runtime._control._request_once = control_request


class ClientPlatformAliasTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.servers: list[DaemonHttpServer] = []

    def tearDown(self) -> None:
        for daemon_server in self.servers:
            daemon_server.stop()

    @staticmethod
    def _parse(platform: str) -> ServerConfig:
        return server.parse_args(
            [
                "--keyfile",
                "dummy.key",
                "--client",
                "--client-platform",
                platform,
            ]
        )

    def _daemon(self) -> DaemonHttpServer:
        daemon_server = DaemonHttpServer(_config(key="ckey"), port=0)
        daemon_server.start()
        self.servers.append(daemon_server)
        return daemon_server

    def test_g1_grok_cli_is_normalized_and_raw_value_is_preserved(self) -> None:
        config = self._parse("grok")

        self.assertEqual(config.client_platform, "unknown")
        self.assertEqual(config.client_platform_raw, "grok")

    async def test_g2_normalized_identity_crosses_daemon_and_releases_lease(self) -> None:
        daemon_server = self._daemon()
        config = replace(
            self._parse("grok"),
            key=daemon_server.key,
            port=daemon_server.port,
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)
        _attach_fixture_transport(runtime, daemon_server)

        identity_payload = runtime.identity.to_payload()
        daemon_identity = ClientIdentity.from_payload(identity_payload)
        self.assertEqual(daemon_identity.platform, "unknown")
        self.assertNotIn("client_platform_raw", identity_payload)
        self.assertNotIn("grok", identity_payload.values())

        acquired = await runtime.session_acquire("verify client platform alias")
        self.assertEqual(acquired["status"], "active")
        acquired_identity = json.loads(acquired["client_identity_json"])
        self.assertEqual(acquired_identity["platform"], "unknown")
        self.assertNotIn("client_platform_raw", acquired_identity)

        released = await runtime.session_release(acquired["lease_token"])
        self.assertTrue(released["released"])
        self.assertIsNone(runtime.active_lease_token)

    def test_g3_platform_enum_remains_fail_closed(self) -> None:
        for invalid in ("caude", "gpt"):
            with self.subTest(invalid=invalid), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    self._parse(invalid)
                self.assertEqual(raised.exception.code, 2)

        for canonical in ("claude", "codex", "unknown"):
            with self.subTest(canonical=canonical):
                config = self._parse(canonical)
                self.assertEqual(config.client_platform, canonical)
                self.assertEqual(getattr(config, "client_platform_raw", ""), "")

    def test_g4_alias_logs_raw_value_only_when_normalized(self) -> None:
        alias_logs: list[str] = []
        alias_config = replace(
            self._parse("grok"),
            key="fixture-key",
            port=12345,
            log_sink=alias_logs.append,
        )
        _fixture_client_runtime(alias_config)

        alias_audit = [
            message for message in alias_logs if "platform alias normalized" in message
        ]
        self.assertEqual(len(alias_audit), 1)
        self.assertIn("raw=grok", alias_audit[0])
        self.assertIn("canonical=unknown", alias_audit[0])

        canonical_logs: list[str] = []
        canonical_config = replace(
            self._parse("claude"),
            key="fixture-key",
            port=12345,
            log_sink=canonical_logs.append,
        )
        _fixture_client_runtime(canonical_config)

        self.assertFalse(
            any("platform alias normalized" in message for message in canonical_logs)
        )


if __name__ == "__main__":
    unittest.main()
