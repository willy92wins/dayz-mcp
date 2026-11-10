"""CLI for the real supervisor package.

``--host-sample`` reads CIM/GPI on this machine (no DayZ). ``--self-test`` runs
the supervisor logic and real-adapter tests. The public stop acceptance is a
test (``tests.test_supervisor_real_adapters``), not this CLI: it needs the
product loopback stack.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .real_adapters import RealHostMetrics
from .windows_metrics import bytes_to_gib


def _host_sample(pids: list[int]) -> dict[str, object]:
    sample = RealHostMetrics().sample(pids)
    return {
        "schema": "antihack-supervisor-host-sample/v1",
        "observed_at_wall_iso": sample.observed_at_wall_iso,
        "ok": sample.ok,
        "error": sample.error,
        "free_physical_kib": sample.free_physical_kib,
        "free_physical_bytes": sample.free_physical_bytes,
        "free_physical_gib": (
            bytes_to_gib(sample.free_physical_bytes)
            if sample.free_physical_bytes is not None
            else None
        ),
        "commit_total_bytes": sample.commit_total_bytes,
        "commit_limit_bytes": sample.commit_limit_bytes,
        "physical_available_bytes": sample.physical_available_bytes,
        "gpi_available": sample.gpi_available,
        "gpi_error": sample.gpi_error,
        "private_bytes_by_pid": {
            str(pid): value
            for pid, value in sorted(sample.private_bytes_by_pid.items())
        },
        "working_set_bytes_by_pid": {
            str(pid): value
            for pid, value in sorted(sample.working_set_bytes_by_pid.items())
        },
        "units": {
            "free_physical_kib": "KiB (CIM Win32_OperatingSystem.FreePhysicalMemory)",
            "free_physical_bytes": "KiB * 1024",
            "free_physical_gib": "(KiB * 1024) / 2**30",
            "private_bytes_by_pid": "bytes (CIM Win32_Process.PrivatePageCount)",
            "working_set_bytes_by_pid": "bytes (CIM Win32_Process.WorkingSetSize)",
            "commit_*": "pages * PageSize (psapi GetPerformanceInfo)",
        },
        "hard_os_limit_implemented": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Antihack host memory supervisor (real adapters)"
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--describe-api-blockers", action="store_true")
    parser.add_argument("--host-sample", action="store_true")
    parser.add_argument(
        "--pids",
        default="",
        help="comma-separated pids for --host-sample (default: this process)",
    )
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    if args.describe_api_blockers:
        print(
            json.dumps(
                {
                    "head": "ba47dbe7c30d175d956dbe866a908a5da9472e07",
                    "STARTING_stoppable_via_public_dayz_test_stop": True,
                    "explicit_run_id_required": True,
                    "cancel_codes": [
                        "launch_cancel_pending",
                        "launch_cancel_degraded",
                    ],
                    "degraded_state": "durable row, typically UNRECONCILED",
                    "session_busy_gate": "_require_idle_session",
                    "hard_os_limit": False,
                    "G_STOP_LATENCY": "not measured by A1; runtime plan owns it",
                    "storage_rollback_claimed": False,
                },
                indent=2,
            )
        )
        return 0

    if args.host_sample:
        pids = (
            [int(item) for item in args.pids.split(",") if item.strip()]
            if args.pids
            else [os.getpid()]
        )
        payload = _host_sample(pids)
        text = json.dumps(payload, indent=2, sort_keys=True)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if payload["ok"] is True else 1

    if args.self_test:
        import unittest

        tools_dir = Path(__file__).resolve().parents[1]
        loader = unittest.TestLoader()
        suite = loader.discover(
            str(tools_dir / "tests"), pattern="test_supervisor*.py"
        )
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1

    print(
        "No action. Use --self-test, --host-sample or --describe-api-blockers.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
