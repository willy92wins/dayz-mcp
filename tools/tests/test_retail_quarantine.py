from __future__ import annotations

import ctypes
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, orphan_guard
from dayz_mcp.session_coordination import SessionCoordinator


IDENTITY = {
    "platform": "codex",
    "pid": 11,
    "ppid": 1,
    "started_at_utc": "2026-07-15T00:00:00Z",
    "session_id": "A",
    "task_label": "quarantine",
}


class RetailQuarantineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[dict[str, object]] = []
        self.state = loopback.ServerState("key")
        self.coordinator = SessionCoordinator(
            token_fn=lambda: "token",
            id_fn=lambda: "lease",
            audit=lambda event: self.events.append(event) or True,
            cleanup=lambda session, lease, reason, active: self.state.cleanup_owner(
                session, lease, reason, active
            ),
        )
        self.state.coordination = self.coordinator
        status, acquired = self.coordinator.acquire(
            __import__("dayz_mcp.session_coordination", fromlist=["ClientIdentity"]).ClientIdentity.from_payload(IDENTITY),
            "test",
        )
        self.assertEqual(status, 200)
        self.token = acquired["lease_token"]

    def test_toolhelp_exact_names_are_case_insensitive_and_never_grant_ownership(self) -> None:
        result = orphan_guard._retail_snapshot_from_entries(
            [(1, "dayz_be.EXE"), (2, "DayZ_x64.exe"), (3, "DayZDiag_x64.exe")]
        )
        self.assertEqual(result, {
            "known": True,
            "processes": [
                {"pid": 1, "name": "dayz_be.EXE"},
                {"pid": 2, "name": "DayZ_x64.exe"},
            ],
        })
        self.assertNotIn("owner", str(result).lower())

    def test_public_exact_name_snapshot_is_reusable_and_fail_closed(self) -> None:
        with patch.object(
            orphan_guard,
            "_snapshot_all_process_entries",
            return_value=[(7, "DAYZDIAG_X64.EXE"), (8, "python.exe")],
        ):
            self.assertEqual(
                orphan_guard.snapshot_processes_by_name(["DayZDiag_x64.exe"]),
                {
                    "known": True,
                    "processes": [{"pid": 7, "name": "DAYZDIAG_X64.EXE"}],
                },
            )
        with patch.object(
            orphan_guard, "_snapshot_all_process_entries", return_value=None
        ):
            self.assertEqual(
                orphan_guard.snapshot_processes_by_name(["DayZDiag_x64.exe"]),
                {"known": False, "processes": []},
            )
        with self.assertRaises(ValueError):
            orphan_guard.snapshot_processes_by_name([""])

    def test_toolhelp_native_failures_are_all_unknown_fail_closed(self) -> None:
        class Kernel32:
            def __init__(self, mode: str) -> None:
                self.mode = mode
                self.closed: list[int] = []

            def CreateToolhelp32Snapshot(self, _flags, _pid):
                return 0 if self.mode == "create" else 123

            def Process32First(self, _snapshot, pointer):
                if self.mode == "first":
                    return 0
                entry = pointer._obj
                entry.th32ProcessID = 1
                entry.szExeFile = b"python.exe"
                return 1

            def Process32Next(self, _snapshot, _pointer):
                if self.mode == "exception":
                    raise OSError("native failure")
                ctypes.set_last_error(5)
                return 0

            def CloseHandle(self, handle):
                self.closed.append(handle)
                return 1

        for mode in ("create", "first", "next", "exception"):
            with self.subTest(mode=mode):
                kernel = Kernel32(mode)
                with patch.object(orphan_guard, "_IS_WINDOWS", True), patch.object(
                    orphan_guard, "_k32", kernel
                ):
                    self.assertEqual(
                        orphan_guard.snapshot_processes_by_name(["DayZDiag_x64.exe"]),
                        {"known": False, "processes": []},
                    )
                if mode != "create":
                    self.assertEqual(kernel.closed, [123])

    @unittest.skipUnless(os.name == "nt", "Windows ToolHelp smoke")
    def test_public_toolhelp_smoke_is_read_only_and_known(self) -> None:
        result = orphan_guard.snapshot_processes_by_name(
            ["DayZDiag_x64.exe", "DayZ_x64.exe"]
        )
        self.assertIs(result.get("known"), True)
        self.assertIsInstance(result.get("processes"), list)

    def test_retail_present_allows_read_but_rejects_mutation_without_enqueue(self) -> None:
        self.state.retail_probe = lambda: {
            "known": True,
            "processes": [{"pid": 44, "name": "DayZ_x64.exe"}],
        }
        read_status, _ = self.state.enqueue_command(
            "camera_get", {}, "client", identity_payload=IDENTITY
        )
        mutate_status, mutate = self.state.enqueue_command(
            "world_time_set", {}, "server", identity_payload=IDENTITY, lease_token=self.token
        )
        self.assertEqual(read_status, 200)
        self.assertEqual((mutate_status, mutate["error"]), (409, "retail_quarantine"))
        self.assertEqual(self.state.status_snapshot()["peers"]["server"]["queue_depth"], 0)

    def test_snapshot_failure_rejection_audit_failure_clears_exact_reservation(self) -> None:
        probe_calls = 0

        def fail_snapshot():
            nonlocal probe_calls
            probe_calls += 1
            raise OSError("snapshot failed")

        self.state.retail_probe = fail_snapshot
        self.coordinator._audit = (
            lambda event: event.get("event") != "session_rejected"
        )
        status, result = self.state.enqueue_command(
            "world_time_set", {}, "server", identity_payload=IDENTITY, lease_token=self.token
        )
        self.assertEqual((status, result["error"]), (503, "audit_failed"))
        self.assertGreaterEqual(probe_calls, 1)
        self.assertEqual(self.state.status_snapshot()["peers"]["server"]["queue_depth"], 0)
        with self.coordinator._condition:
            self.assertEqual(self.coordinator._active.pending_authorizations, [])

        self.coordinator._audit = lambda _event: True
        self.state.retail_probe = lambda: {"known": True, "processes": []}
        retry_status, _ = self.state.enqueue_command(
            "world_time_set", {}, "server", identity_payload=IDENTITY, lease_token=self.token
        )
        self.assertEqual(retry_status, 200)

    def test_cleanup_under_quarantine_cancels_but_skips_vehicle_release(self) -> None:
        self.state.retail_probe = lambda: {"known": False, "processes": []}
        with self.coordinator._condition:
            self.coordinator._active.vehicle_active = True
        _, released = self.coordinator.release(
            __import__("dayz_mcp.session_coordination", fromlist=["ClientIdentity"]).ClientIdentity.from_payload(IDENTITY),
            self.token,
            "lease_expired",
        )
        cleanup = released["cleanup"]
        self.assertEqual(cleanup["vehicle_release_enqueued"], 0)
        self.assertIn("retail_quarantine", released["cleanup_degraded"])
        self.assertEqual(self.state.status_snapshot()["peers"]["client"]["queue_depth"], 0)


if __name__ == "__main__":
    unittest.main()
