"""Print or probe the installer-registered DayZ-MCP stdio --client.

This helper emits the same argv `install_mcp.build_client_args` registers for
Claude/Codex, and can run a short-lived `tools/list` against that process.
It does not observe the agent host's MCP channel. Host-only policy (when to
abandon a host transport for the rest of a session) lives in the host runbook.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import TypeVar

TOOLS_DIR = Path(__file__).resolve().parent.parent
DEFAULT_VENV_PYTHON = TOOLS_DIR / ".venv-mcp" / "Scripts" / "python.exe"
DEFAULT_KEYFILE = TOOLS_DIR / ".dayz_mcp.key"
DEFAULT_PORT = 8765
DEFAULT_IDLE_TIMEOUT_S = 1800.0
DEFAULT_PLATFORM = "claude"
PLAN_B_ATTEMPTS = 3
PLAN_B_BACKOFF_S = 2.0
PLAN_B_READ_TIMEOUT_S = 15.0
HOST_STARTUP_MARKERS = (
    "mcpstartuperror",
    "vsock",
    "failed to start mcp",
)

T = TypeVar("T")


class StdioBridgeError(RuntimeError):
    """tools/list failed; `code` is a sanitized probe class, not host policy."""

    def __init__(self, message: str, *, code: str = "stdio_probe_failed") -> None:
        super().__init__(message)
        self.code = code


def is_host_startup_error(text: str) -> bool:
    """Host-runbook helper: text looks like a host MCP channel startup fault.

    This process cannot observe vsock. Callers outside `--probe` may use this
    to decide whether to spawn the registered stdio `--client`.
    """
    lowered = text.casefold()
    return any(marker in lowered for marker in HOST_STARTUP_MARKERS)


def official_client_argv(
    *,
    python: str,
    keyfile: str,
    port: int = DEFAULT_PORT,
    platform: str = DEFAULT_PLATFORM,
    idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
    expected_game_version: str = "",
    allow_legacy: bool = False,
    tools_root: str | Path | None = None,
    instance_token: str | None = None,
    game_path: str | None = None,
) -> list[str]:
    from install_mcp import InstallerOptions, build_client_args

    options = InstallerOptions(
        port=int(port),
        keyfile=Path(keyfile),
        server_profiles=None,
        client_profiles=None,
        mission_path=None,
        expected_game_version=expected_game_version or "",
        idle_timeout_seconds=float(idle_timeout_s),
        allow_legacy=bool(allow_legacy),
        register=False,
        pin_clis=False,
        claude_exe=None,
        codex_exe=None,
        tools_root=Path(tools_root or TOOLS_DIR),
    )
    argv = [python, *build_client_args(options, platform)]
    if instance_token:
        argv += ["--instance", instance_token]
    if game_path:
        argv += ["--game-path", game_path]
    return argv


def _unwrap_error(error: BaseException) -> BaseException:
    if isinstance(error, BaseExceptionGroup) and error.exceptions:
        return _unwrap_error(error.exceptions[0])
    return error


def classify_probe_error(error: BaseException) -> tuple[str, bool, str]:
    """Return `(code, retryable, sanitized_message)` for a stdio probe failure.

    Unknown / unclassified exceptions are fail-closed: `retryable=False` with a
    sanitized `stdio_probe_failed` code. Only classified transient
    handshake/connection errors are retryable.
    """
    if isinstance(error, StdioBridgeError):
        return error.code, False, str(error)

    root = _unwrap_error(error)
    if isinstance(root, StdioBridgeError):
        return root.code, False, str(root)

    text = str(root)
    lowered = text.casefold()

    if is_host_startup_error(text):
        return (
            "host_channel",
            False,
            "host_channel: host MCP channel text; this helper cannot observe it",
        )

    if isinstance(root, (ModuleNotFoundError, ImportError)):
        return "import_error", False, "import_error: mcp import failed"

    if isinstance(root, PermissionError) or "permission" in lowered:
        return (
            "keyfile_unreadable",
            False,
            "keyfile_unreadable: installer keyfile is not readable",
        )

    if isinstance(root, FileNotFoundError):
        if "key" in lowered:
            return (
                "keyfile_missing",
                False,
                "keyfile_missing: installer keyfile path is missing",
            )
        return (
            "executable_missing",
            False,
            "executable_missing: python executable is missing",
        )

    if (
        "unauthorized" in lowered
        or "401" in lowered
        or "bad key" in lowered
        or "invalid key" in lowered
    ):
        return (
            "unauthorized",
            False,
            "unauthorized: installer keyfile was rejected",
        )

    if "daemon_unavailable" in lowered:
        return (
            "daemon_unavailable",
            False,
            "daemon_unavailable: daemon did not accept the client",
        )

    if "daemon_provenance" in lowered or "config_mismatch" in lowered:
        return (
            "config_mismatch",
            False,
            "config_mismatch: port or keyfile does not match the registered client",
        )

    timeout_types: tuple[type[BaseException], ...] = (TimeoutError,)
    try:
        import asyncio

        timeout_types = (TimeoutError, asyncio.TimeoutError)
    except ImportError:
        pass

    if isinstance(root, timeout_types) or "timed out" in lowered:
        return (
            "handshake_timeout",
            True,
            "handshake_timeout: stdio initialize/tools.list did not finish",
        )

    if isinstance(
        root,
        (ConnectionRefusedError, ConnectionResetError, BrokenPipeError, ConnectionError),
    ):
        return (
            "connection_refused",
            True,
            "connection_refused: stdio child or daemon not ready yet",
        )

    errno = getattr(root, "errno", None)
    if isinstance(root, OSError) and errno in {32, 111, 10054, 10061}:
        return (
            "connection_refused",
            True,
            "connection_refused: stdio child or daemon not ready yet",
        )

    return (
        "stdio_probe_failed",
        False,
        f"stdio_probe_failed: {type(root).__name__}",
    )


def retry_with_backoff(
    operation: Callable[[], T],
    *,
    attempts: int = PLAN_B_ATTEMPTS,
    backoff_s: float = PLAN_B_BACKOFF_S,
    sleeper: Callable[[float], None] = time.sleep,
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    if not math.isfinite(backoff_s) or backoff_s < 0:
        raise ValueError("backoff_s must be finite and >= 0")
    last_error: BaseException | None = None
    last_code = "stdio_probe_failed"
    last_message = "stdio_probe_failed"
    for index in range(attempts):
        try:
            return operation()
        except Exception as error:
            last_error = error
            last_code, retryable, last_message = classify_probe_error(error)
            if (not retryable) or index >= attempts - 1:
                break
            delay = backoff_s * float(index + 1)
            sleeper(delay)
    raise StdioBridgeError(
        f"{last_message} after {attempts} attempt(s)",
        code=last_code,
    ) from last_error


def list_tools_once(
    command: list[str],
    *,
    cwd: str | None = None,
    read_timeout_s: float = PLAN_B_READ_TIMEOUT_S,
) -> list[str]:
    import asyncio

    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as error:
        raise StdioBridgeError(
            "import_error: mcp import failed",
            code="import_error",
        ) from error

    if not math.isfinite(read_timeout_s) or read_timeout_s <= 0:
        raise ValueError("read_timeout_s must be finite and > 0")

    async def _run() -> list[str]:
        params = StdioServerParameters(
            command=command[0],
            args=list(command[1:]),
            cwd=cwd,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(
                read,
                write,
                read_timeout_seconds=timedelta(seconds=read_timeout_s),
            ) as session:
                await session.initialize()
                listed = await session.list_tools()
        return [tool.name for tool in listed.tools]

    return asyncio.run(_run())


def probe_tools_list(
    command: list[str],
    *,
    cwd: str | None = None,
    attempts: int = PLAN_B_ATTEMPTS,
    backoff_s: float = PLAN_B_BACKOFF_S,
    read_timeout_s: float = PLAN_B_READ_TIMEOUT_S,
    list_tools: Callable[[list[str]], list[str]] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[str]:
    if list_tools is None:
        caller: Callable[[list[str]], list[str]] = (
            lambda argv: list_tools_once(
                argv,
                cwd=cwd,
                read_timeout_s=read_timeout_s,
            )
        )
    else:
        caller = list_tools
    return retry_with_backoff(
        lambda: caller(command),
        attempts=attempts,
        backoff_s=backoff_s,
        sleeper=sleeper,
    )


def _resolve_python(explicit: str | None) -> str:
    if explicit:
        return str(Path(explicit))
    return str(DEFAULT_VENV_PYTHON)


def _resolve_keyfile(explicit: str | None) -> str:
    if explicit:
        return str(Path(explicit))
    return str(DEFAULT_KEYFILE)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be finite and >= 0")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and > 0")
    return parsed


def _port_number(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return parsed


def _print_command_payload(argv: list[str]) -> dict[str, object]:
    return {
        "ok": True,
        "plan": "stdio_client",
        "cwd": str(TOOLS_DIR),
        "argv": argv,
        "attempts": PLAN_B_ATTEMPTS,
        "backoff_s": PLAN_B_BACKOFF_S,
        "read_timeout_s": PLAN_B_READ_TIMEOUT_S,
        "close_at": "session_end",
        "note": (
            "Installer-registered stdio --client. --probe checks tools/list "
            "once per attempt with a finite read timeout, then exits. Keep a "
            "separate long-lived --client process for a working session."
        ),
    }


def _dump(payload: dict[str, object]) -> None:
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Print the installer-registered DayZ-MCP stdio --client argv, "
            "or probe tools/list on a short-lived spawn of that process."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--python", help="Python used to spawn -m dayz_mcp")
    parser.add_argument("--keyfile", help="Installer keyfile path, not the key")
    parser.add_argument("--port", type=_port_number, default=DEFAULT_PORT)
    parser.add_argument(
        "--idle-timeout",
        type=_non_negative_float,
        default=DEFAULT_IDLE_TIMEOUT_S,
        help="Same --idle-timeout the installer registers (default 1800)",
    )
    parser.add_argument(
        "--client-platform",
        choices=("claude", "codex"),
        default=DEFAULT_PLATFORM,
        help="Same --client-platform the installer registers (default claude)",
    )
    parser.add_argument(
        "--expected-game-version",
        default="",
        help="Optional --expected-game-version from the installer",
    )
    parser.add_argument(
        "--allow-legacy",
        action="store_true",
        help="Omit --require-version, matching install_mcp --allow-legacy",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--print-command",
        action="store_true",
        help="Print the official --client argv and exit (default)",
    )
    mode.add_argument(
        "--probe",
        action="store_true",
        help="Retry tools/list on a short-lived --client spawn",
    )
    parser.add_argument("--instance", default="")
    parser.add_argument("--game-path", default="")
    parser.add_argument("--attempts", type=_positive_int, default=PLAN_B_ATTEMPTS)
    parser.add_argument(
        "--backoff-s",
        type=_non_negative_float,
        default=PLAN_B_BACKOFF_S,
    )
    parser.add_argument(
        "--read-timeout-s",
        type=_positive_float,
        default=PLAN_B_READ_TIMEOUT_S,
        help="Per-attempt ClientSession read timeout (default 15)",
    )
    raw = list(sys.argv[1:] if argv is None else argv)
    from dayz_mcp.server_cli import (
        InstanceSelectionError,
        selector_from_parsed,
        validate_entry_selector,
    )

    try:
        token, game_path = validate_entry_selector(raw)
    except InstanceSelectionError as error:
        _dump(
            {
                "ok": False,
                "error": error.code,
                "code": error.code,
                "cwd": str(TOOLS_DIR),
            }
        )
        return 2
    args = parser.parse_args(raw)
    try:
        consumed_token, consumed_game = selector_from_parsed(args.instance, args.game_path)
        if (consumed_token, consumed_game) != (token, game_path):
            raise InstanceSelectionError("duplicate_instance_flag")
    except InstanceSelectionError as error:
        _dump(
            {
                "ok": False,
                "error": error.code,
                "code": error.code,
                "cwd": str(TOOLS_DIR),
            }
        )
        return 2
    try:
        command = official_client_argv(
            python=_resolve_python(args.python),
            keyfile=_resolve_keyfile(args.keyfile),
            port=args.port,
            platform=args.client_platform,
            idle_timeout_s=args.idle_timeout,
            expected_game_version=args.expected_game_version,
            allow_legacy=args.allow_legacy,
            instance_token=token,
            game_path=game_path,
        )
    except Exception as error:
        code, _retryable, message = classify_probe_error(error)
        _dump(
            {
                "ok": False,
                "error": message,
                "code": code,
                "cwd": str(TOOLS_DIR),
            }
        )
        return 2
    if args.probe:
        try:
            names = probe_tools_list(
                command,
                cwd=str(TOOLS_DIR),
                attempts=args.attempts,
                backoff_s=args.backoff_s,
                read_timeout_s=args.read_timeout_s,
            )
        except StdioBridgeError as error:
            _dump(
                {
                    "ok": False,
                    "error": str(error),
                    "code": error.code,
                    "argv": command,
                    "cwd": str(TOOLS_DIR),
                }
            )
            return 1
        _dump(
            {
                "ok": True,
                "plan": "stdio_client",
                "tools": names,
                "count": len(names),
                "argv": command,
                "cwd": str(TOOLS_DIR),
            }
        )
        return 0
    _dump(_print_command_payload(command))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
