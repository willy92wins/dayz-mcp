from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Literal


CANONICAL_CLIENT_PLATFORMS = ("claude", "codex", "unknown")
CLIENT_PLATFORM_ALIASES = {"grok": "unknown"}


@dataclass(frozen=True)
class ServerCliParse:
    status: Literal["parsed", "terminal", "invalid"]
    namespace: argparse.Namespace | None = None


class _SilentTerminal(Exception):
    pass


class _SilentInvalid(Exception):
    pass


class _SilentArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message: str | None, file: object | None = None) -> None:
        del message, file

    def print_help(self, file: object | None = None) -> None:
        del file

    def print_usage(self, file: object | None = None) -> None:
        del file

    def error(self, message: str) -> None:
        raise _SilentInvalid(message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        del message
        if status == 0:
            raise _SilentTerminal()
        raise _SilentInvalid(f"parser_exit_{status}")


def _configure_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--keyfile", required=True)
    parser.add_argument("--expected-game-version")
    parser.add_argument("--require-version", action="store_true")
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=1800.0,
        help="Self-shutdown the loopback after N seconds with no MCP/game activity (0 disables).",
    )
    parser.add_argument("--enable-exec-enforce", action="store_true")
    parser.add_argument("--exec-allowlist")
    parser.add_argument("--exec-audit-path")
    parser.add_argument(
        "--client-platform",
        choices=(*CANONICAL_CLIENT_PLATFORMS, *CLIENT_PLATFORM_ALIASES),
        default="unknown",
    )
    parser.add_argument("--task-label", default="")
    parser.add_argument(
        "--no-daemon-autospawn",
        action="store_false",
        dest="auto_spawn_daemon",
        help="Fail if the daemon is unavailable instead of spawning one.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--client",
        action="store_const",
        dest="mode",
        const="client",
        help="Proxy bridge calls over HTTP to the broker daemon (multi-session); spawns it lazily.",
    )
    mode_group.add_argument(
        "--daemon",
        action="store_const",
        dest="mode",
        const="daemon",
        help="Run the standalone broker daemon that owns the loopback port.",
    )
    mode_group.add_argument(
        "--embedded",
        action="store_const",
        dest="mode",
        const="embedded",
        help="Bind the loopback in-process (single session; back-compat default).",
    )
    parser.set_defaults(mode="embedded", auto_spawn_daemon=True)
    return parser


def build_server_parser() -> argparse.ArgumentParser:
    return _configure_parser(
        argparse.ArgumentParser(
            description="DayZ MCP stdio server",
            allow_abbrev=False,
        )
    )


def parse_server_tail_silent(argv: list[str]) -> ServerCliParse:
    parser = _configure_parser(
        _SilentArgumentParser(
            description="DayZ MCP stdio server",
            allow_abbrev=False,
        )
    )
    try:
        namespace = parser.parse_args(argv)
    except _SilentTerminal:
        return ServerCliParse("terminal")
    except (_SilentInvalid, argparse.ArgumentError, ValueError):
        return ServerCliParse("invalid")
    return ServerCliParse("parsed", namespace)
