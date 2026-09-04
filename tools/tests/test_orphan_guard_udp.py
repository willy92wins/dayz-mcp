"""fb-20260904-114520-6927: the UDP socket table as the second witness of the box (orphan_guard)."""
from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import orphan_guard

NETSTAT_SAMPLE_CLEAN = """
Conexiones activas

  Proto  Dirección local          Dirección remota        Estado           PID
  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       1234
  UDP    0.0.0.0:53             *:*                                    4032
  UDP    0.0.0.0:2302           *:*                                    45428
  UDP    [::]:2304              *:*                                    45428
  UDP    127.0.0.1:1900         *:*                                    n/a
"""


class NetstatUdpParserTest(unittest.TestCase):
    def test_rows_become_port_pid_pairs_including_ipv6_and_unattributable(self) -> None:
        pairs = orphan_guard._udp_holders_from_netstat_output(NETSTAT_SAMPLE_CLEAN)
        self.assertEqual(pairs, [(53, 4032), (2302, 45428), (2304, 45428), (1900, None)])

    def test_oem_bytes_do_not_raise(self) -> None:
        raw = "  UDP    0.0.0.0:2302           *:*   45428\r\n".encode("cp850")
        self.assertEqual(orphan_guard._udp_holders_from_netstat_output(raw), [(2302, 45428)])


class SnapshotUdpPortHoldersTest(unittest.TestCase):
    def test_psutil_rows_are_named_from_one_toolhelp_snapshot(self) -> None:
        rows = [
            SimpleNamespace(laddr=SimpleNamespace(ip="0.0.0.0", port=2304), pid=45428),
            SimpleNamespace(laddr=SimpleNamespace(ip="0.0.0.0", port=2302), pid=45428),
            SimpleNamespace(laddr=SimpleNamespace(ip="0.0.0.0", port=53), pid=None),
            SimpleNamespace(laddr=SimpleNamespace(ip="::", port=70000), pid=7),
        ]
        fake_psutil = SimpleNamespace(net_connections=lambda kind: rows)
        with patch.object(orphan_guard, "psutil", fake_psutil), patch.object(
            orphan_guard,
            "_snapshot_all_process_entries",
            return_value=[(45428, "DayZDiag_x64.exe"), (4032, "svchost.exe")],
        ):
            result = orphan_guard.snapshot_udp_port_holders()
        self.assertTrue(result["known"])
        self.assertEqual(
            result["holders"],
            [
                {"port": 53, "pid": None, "name": None},
                {"port": 2302, "pid": 45428, "name": "DayZDiag_x64.exe"},
                {"port": 2304, "pid": 45428, "name": "DayZDiag_x64.exe"},
            ],
        )

    def test_netstat_is_the_fallback_when_psutil_is_absent(self) -> None:
        with patch.object(orphan_guard, "psutil", None), patch.object(
            orphan_guard, "_udp_holders_via_netstat", return_value=[(2302, 45428)]
        ), patch.object(orphan_guard, "_snapshot_all_process_entries", return_value=None):
            result = orphan_guard.snapshot_udp_port_holders()
        self.assertEqual(result, {"known": True, "holders": [{"port": 2302, "pid": 45428, "name": None}]})

    def test_no_source_is_unknown_never_empty(self) -> None:
        with patch.object(orphan_guard, "psutil", None), patch.object(
            orphan_guard, "_udp_holders_via_netstat", return_value=None
        ):
            result = orphan_guard.snapshot_udp_port_holders()
        self.assertEqual(result, {"known": False, "holders": []})

    def test_psutil_failure_falls_back_to_netstat(self) -> None:
        def boom(kind: str):
            raise PermissionError("access denied")

        with patch.object(orphan_guard, "psutil", SimpleNamespace(net_connections=boom)), patch.object(
            orphan_guard, "_udp_holders_via_netstat", return_value=[(2302, None)]
        ), patch.object(orphan_guard, "_snapshot_all_process_entries", return_value=[]):
            result = orphan_guard.snapshot_udp_port_holders()
        self.assertEqual(result["holders"], [{"port": 2302, "pid": None, "name": None}])

    @unittest.skipUnless(sys.platform == "win32", "socket table read is the Windows production path")
    def test_live_socket_table_is_readable_on_this_host(self) -> None:
        result = orphan_guard.snapshot_udp_port_holders()
        self.assertTrue(result["known"], result)
        self.assertTrue(result["holders"], result)
        for row in result["holders"]:
            self.assertTrue(1 <= row["port"] <= 65535, row)
            self.assertTrue(row["pid"] is None or row["pid"] > 0, row)

    def test_dayz_image_names(self) -> None:
        for name in ("DayZDiag_x64.exe", "dayzserver_x64.EXE", "DayZ_x64.exe", "DayZ_BE.exe"):
            self.assertTrue(orphan_guard.is_dayz_image_name(name), name)
        for name in ("svchost.exe", "python.exe", None, 3):
            self.assertFalse(orphan_guard.is_dayz_image_name(name), name)


# --- round 2 (LOSS M-3, LOSS B-1, ADMIN H-04, RACE H-02) ------------------------------------------

class NetstatUdpParserRound2Test(unittest.TestCase):
    def test_a_truncated_udp_row_makes_the_whole_dump_untrusted(self) -> None:
        # LOSS M-3: a row this parser cannot read might be the holder; the
        # dump is then unknown, never "known and empty".
        for bad in (
            "  UDP    0.0.0.0:2302\n",
            "  UDP    garbage-no-port   *:*   1234\n",
            "  UDP    0.0.0.0:99999   *:*   1234\n",
        ):
            with self.subTest(bad):
                self.assertIsNone(orphan_guard._udp_holders_from_netstat_output(NETSTAT_SAMPLE_CLEAN + bad))

    def test_clean_dump_still_parses(self) -> None:
        self.assertEqual(
            orphan_guard._udp_holders_from_netstat_output(NETSTAT_SAMPLE_CLEAN),
            [(53, 4032), (2302, 45428), (2304, 45428), (1900, None)],
        )

    def test_netstat_fallback_reports_unknown_on_an_untrusted_dump(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout=b"  UDP    0.0.0.0:2302\r\n")
        with patch.object(orphan_guard, "_IS_WINDOWS", True), patch.object(
            orphan_guard.subprocess, "run", return_value=completed
        ):
            self.assertIsNone(orphan_guard._udp_holders_via_netstat())

    def test_netstat_timeout_is_bounded_to_three_seconds(self) -> None:
        seen: dict[str, object] = {}

        def fake_run(*args, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(returncode=0, stdout=b"")

        with patch.object(orphan_guard, "_IS_WINDOWS", True), patch.object(
            orphan_guard.subprocess, "run", side_effect=fake_run
        ):
            self.assertEqual(orphan_guard._udp_holders_via_netstat(), [])
        self.assertEqual(seen.get("timeout"), 3)


class SnapshotUdpPortHoldersRound2Test(unittest.TestCase):
    def test_tuple_laddr_is_accepted(self) -> None:
        rows = [SimpleNamespace(laddr=("0.0.0.0", 2302), pid=45428)]
        with patch.object(orphan_guard, "psutil", SimpleNamespace(net_connections=lambda kind: rows)), patch.object(
            orphan_guard, "_snapshot_all_process_entries", return_value=[(45428, "DayZDiag_x64.exe")]
        ):
            result = orphan_guard.snapshot_udp_port_holders()
        self.assertEqual(result["holders"], [{"port": 2302, "pid": 45428, "name": "DayZDiag_x64.exe"}])

    def test_a_pid_missing_from_the_bulk_snapshot_is_looked_up_once_more(self) -> None:
        rows = [SimpleNamespace(laddr=SimpleNamespace(ip="0.0.0.0", port=2302), pid=777)]
        with patch.object(orphan_guard, "psutil", SimpleNamespace(net_connections=lambda kind: rows)), patch.object(
            orphan_guard, "_snapshot_all_process_entries", return_value=[(1, "System")]
        ), patch.object(orphan_guard, "_toolhelp_lookup", return_value=(4, "DayZServer_x64.exe")) as lookup:
            result = orphan_guard.snapshot_udp_port_holders()
        lookup.assert_called_once_with(777)
        self.assertEqual(result["holders"], [{"port": 2302, "pid": 777, "name": "DayZServer_x64.exe"}])

    def test_a_pid_nobody_can_name_stays_none(self) -> None:
        rows = [SimpleNamespace(laddr=SimpleNamespace(ip="0.0.0.0", port=2302), pid=777)]
        with patch.object(orphan_guard, "psutil", SimpleNamespace(net_connections=lambda kind: rows)), patch.object(
            orphan_guard, "_snapshot_all_process_entries", return_value=[]
        ), patch.object(orphan_guard, "_toolhelp_lookup", return_value=None):
            result = orphan_guard.snapshot_udp_port_holders()
        self.assertEqual(result["holders"], [{"port": 2302, "pid": 777, "name": None}])


if __name__ == "__main__":
    unittest.main()
