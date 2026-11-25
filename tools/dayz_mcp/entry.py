"""Console entry point for the installed dayz-mcp wheel."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None, *, platform: str | None = None) -> int:
    selected_platform = sys.platform if platform is None else platform
    if selected_platform != "win32":
        payload = {
            "error": {
                "code": "platform_unsupported",
                "remedy": "dayz-mcp setup and doctor run only on Windows; on Linux, run python -m dayz_mcp from a clone as before",
            },
            "missing": [],
            "ok": False,
        }
        print(json.dumps(payload, sort_keys=True))
        return 2
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "setup":
        from dayz_mcp import setup_cmd

        return setup_cmd.main(arguments[1:])
    if arguments and arguments[0] == "doctor":
        from dayz_mcp import doctor

        return doctor.main(arguments[1:])
    parser = argparse.ArgumentParser(prog="dayz-mcp")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("setup")
    subcommands.add_parser("doctor")
    parser.parse_args(arguments)
    parser.error("expected setup or doctor")
    return 2
