"""Pure construction contract for the standalone daemon process."""

from __future__ import annotations

import ntpath
import sys
from pathlib import Path
from typing import Any


def daemon_runtime_cwd() -> str:
    return str(Path(__file__).resolve().parents[1])


def build_daemon_argv(config: Any, *, python: str | None = None) -> list[str]:
    """Build the daemon argv while forwarding the configured bridge policy."""
    argv = [
        python or sys.executable,
        "-m",
        "dayz_mcp",
        "--daemon",
        "--port",
        str(int(getattr(config, "port", 8765))),
    ]
    keyfile = getattr(config, "keyfile", None)
    if keyfile:
        argv += ["--keyfile", keyfile]
    expected_game_version = getattr(config, "expected_game_version", None)
    if expected_game_version:
        argv += ["--expected-game-version", expected_game_version]
    if getattr(config, "require_version", False):
        argv += ["--require-version"]
    argv += [
        "--idle-timeout",
        str(float(getattr(config, "idle_timeout_s", 1800.0))),
    ]
    if getattr(config, "enable_exec_enforce", False):
        argv += ["--enable-exec-enforce"]
        allowlist = getattr(config, "exec_allowlist", None)
        if allowlist:
            argv += ["--exec-allowlist", allowlist]
    audit_path = getattr(config, "exec_audit_path", None)
    if audit_path:
        argv += ["--exec-audit-path", audit_path]
    return argv


def argv_targets_port(argv: list[str], port: int) -> bool:
    if (
        not isinstance(argv, list)
        or any(not isinstance(argument, str) or not argument for argument in argv)
        or not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65535
    ):
        return False
    indices = [index for index, argument in enumerate(argv) if argument == "--port"]
    return (
        len(indices) == 1
        and indices[0] + 1 < len(argv)
        and argv[indices[0] + 1] == str(port)
        and not any(argument.startswith("--port=") for argument in argv)
    )


def classify_dayz_argv(argv: list[str]) -> str:
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(argument, str) or not argument for argument in argv)
    ):
        return "foreign"
    module_flags = [index for index, argument in enumerate(argv) if argument == "-m"]
    scripts = [
        index
        for index, argument in enumerate(argv)
        if ntpath.isabs(argument)
        and ntpath.basename(argument).casefold() == "p0s_daemon_bootstrap.py"
    ]
    if scripts:
        if (
            len(scripts) != 1
            or module_flags
            or argv.count("-I") != 1
            or argv.count("-B") != 1
            or argv.count("daemon") != 1
            or scripts[0] > argv.index("daemon")
        ):
            return "foreign"
        return "p0s_bootstrap_daemon"
    if (
        len(module_flags) != 1
        or module_flags[0] + 1 >= len(argv)
        or argv[module_flags[0] + 1] != "dayz_mcp"
    ):
        return "foreign"
    modes = [mode for mode in ("--client", "--daemon", "--embedded") if mode in argv]
    if modes == ["--daemon"] and argv.count("--daemon") == 1:
        return "normal_daemon"
    if modes == ["--embedded"] and argv.count("--embedded") == 1:
        return "legacy_embedded"
    if not modes:
        return "legacy_embedded"
    return "foreign"
