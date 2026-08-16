from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dayz_mcp.vehicle_trace import (
    ArtifactInputError,
    build_artifact,
    canonical_artifact_bytes,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a deterministic DayZ MCP vehicle trace artifact.")
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--course", required=True, type=Path)
    parser.add_argument("--pbo", required=True, type=Path)
    parser.add_argument("--rpt", required=True, type=Path)
    parser.add_argument("--script-log", required=True, type=Path)
    parser.add_argument("--lifecycle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        artifact = build_artifact(
            trace_path=args.trace,
            schema_path=args.schema,
            course_path=args.course,
            pbo_path=args.pbo,
            rpt_path=args.rpt,
            script_log_path=args.script_log,
            lifecycle_path=args.lifecycle,
        )
        args.output.write_bytes(canonical_artifact_bytes(artifact))
    except (ArtifactInputError, OSError):
        return 4
    return {"PASS": 0, "FAIL": 1, "STOP": 2}.get(str(artifact["status"]), 4)


if __name__ == "__main__":
    sys.exit(main())
