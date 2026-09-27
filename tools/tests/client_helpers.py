"""Client-mode fixtures shared by many tests.

Moved verbatim from test_client_mode.py so tests stop importing each other
(review 2026-09-25, T1).
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from dayz_mcp import core, host_config, server
from dayz_mcp.server import ServerConfig


_VALID_PEER_VERSION = f"{core.EXPECTED_BRIDGE_VERSION}~1.29.0"


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
        native = launcher
        provenance = host_config.DaemonProvenance(
            launch_executable=launcher,
            native_executable=native,
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
                host_config, "_local_launch_executable", return_value=launcher
            ),
            patch.object(
                host_config, "_local_native_executable", return_value=native
            ),
        ):
            return server.ClientRuntime(fixture_config, **kwargs)
