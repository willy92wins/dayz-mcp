"""fb-20260819-123453-9359 (residue): a client stuck at engine start is named.

client_dead_after_ack already covers a client that dies. One that stays alive
with an RPT holding only its header, and never polls, used to read as
client_still_starting under the startup budget and client_not_polling past
it. The replacement gate now names it client_start_stalled once the header
has sat alone for PEER_STALE_S (15 s) without a poll; the budget still decides
whether the client is superseded.
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import mcp_capture
from dayz_mcp import dayz_test_tool, native_launcher_transaction, steam_preflight
from tests.dayz_test_tool_helpers import (
    RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)

T0 = 1_700_000_000.0
_HEADER = (
    b"=====================================================================\r\n"
    b"== C:\\Program Files (x86)\\Steam\\steamapps\\common\\DayZ\\DayZDiag_x64.exe\r\n"
    b"== \"C:\\Program -mod=P:\\Mods\\@DayZ_MCP -connect=127.0.0.1 -port=2302\r\n"
    b"=====================================================================\r\n"
    b"Exe timestamp: 2026/08/15 04:37:25\r\n"
    b"Current time:  2026/08/19 14:06:09\r\n"
    b"Version 1.29.163709\r\n"
    b"=====================================================================\r\n"
    b"\r\n"
)
_FIRST_LINE = b'14:06:09.484 [Inputs] Loading inputs default "bin/constants.xml"\r\n'
_STALE_PEER = {"last_poll_age_s": 41.0, "version_state": "ok"}


def _write(path: Path, data: bytes, mtime: float) -> None:
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))


class RptHeaderOnlyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.rpt = Path(self.temporary.name) / "DayZDiag_x64_2026-08-19_14-06-09.RPT"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def answer(self, data: bytes) -> bool | None:
        self.rpt.write_bytes(data)
        return dayz_test_tool._rpt_header_only(self.rpt)

    def test_the_bare_header_is_header_only(self) -> None:
        self.assertIs(self.answer(_HEADER), True)
        self.assertIs(self.answer(_HEADER + b"\r\n\r\n"), True)

    def test_one_engine_line_after_the_header_is_not(self) -> None:
        self.assertIs(self.answer(_HEADER + _FIRST_LINE), False)

    def test_a_header_still_being_written_is_no_evidence(self) -> None:
        cut = _HEADER.index(b"Exe timestamp")
        self.assertIsNone(self.answer(_HEADER[:cut]))
        self.assertIsNone(self.answer(b""))

    def test_a_file_larger_than_any_header_is_not_header_only(self) -> None:
        self.assertIs(
            self.answer(_HEADER + b"\r\n" * (dayz_test_tool._RPT_HEADER_SCAN_BYTES)),
            False,
        )

    def test_a_missing_file_is_no_evidence(self) -> None:
        self.assertIsNone(dayz_test_tool._rpt_header_only(self.rpt))


class ClientStartStalledTest(unittest.TestCase):
    """The evidence alone: a fake RPT and fake clock values on one axis."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.profiles = Path(self.temporary.name)
        self.rpt = self.profiles / "DayZDiag_x64_2026-08-19_14-06-09.RPT"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def stalled(self, now: float) -> bool | None:
        return dayz_test_tool._client_start_stalled([str(self.profiles)], T0, now)

    def test_a_bare_header_15_s_old_is_a_stall(self) -> None:
        _write(self.rpt, _HEADER, T0 + 4)
        self.assertIs(self.stalled(T0 + 19), True)

    def test_a_bare_header_younger_than_15_s_is_not_yet(self) -> None:
        _write(self.rpt, _HEADER, T0 + 4)
        self.assertIs(self.stalled(T0 + 18.9), False)

    def test_an_rpt_that_got_past_its_header_is_not_a_stall(self) -> None:
        _write(self.rpt, _HEADER + _FIRST_LINE, T0 + 4)
        self.assertIs(self.stalled(T0 + 600), False)

    def test_no_rpt_of_this_client_is_no_evidence(self) -> None:
        self.assertIsNone(self.stalled(T0 + 600))
        # An RPT of an earlier client, whatever it holds, is not this one's.
        _write(self.rpt, _HEADER, T0 - 900)
        self.assertIsNone(self.stalled(T0 + 600))


def _record(*, age_s: float | None = 30.0, alive: bool | None = True):
    return dayz_test_tool.ClientRecordProjection(True, alive, age_s, 4002)


class StartStalledDecisionTest(unittest.TestCase):
    """Where the budget logic decides client_still_starting / client_not_polling."""

    def decide(self, record, payload, stalled, **kwargs):
        decision = dayz_test_tool._decide_client_replacement(
            record, payload, start_stalled=stalled, **kwargs
        )
        return decision.replace, decision.reason

    def test_under_the_budget_a_stall_is_named_and_still_refused(self) -> None:
        self.assertEqual(
            self.decide(_record(age_s=30.0), {"client_peer": _STALE_PEER}, True),
            (False, "client_start_stalled"),
        )

    def test_past_the_budget_a_stall_is_named_and_replaced(self) -> None:
        self.assertEqual(
            self.decide(_record(age_s=3600.0), {"client_peer": _STALE_PEER}, True),
            (True, "client_start_stalled"),
        )

    def test_without_evidence_the_decision_is_exactly_the_old_one(self) -> None:
        for stalled in (None, False):
            with self.subTest(stalled=stalled):
                self.assertEqual(
                    self.decide(_record(age_s=30.0), {"client_peer": _STALE_PEER}, stalled),
                    (False, "client_still_starting"),
                )
                self.assertEqual(
                    self.decide(_record(age_s=3600.0), {"client_peer": _STALE_PEER}, stalled),
                    (True, "client_not_polling"),
                )

    def test_the_stall_never_overrides_a_stronger_answer(self) -> None:
        polling = {"client_peer": {"last_poll_age_s": 0.2, "version_state": "ok"}}
        recent = {"client_peer": {"binding_state": "AMBIGUOUS", "last_poll_age_s": 5.0}}
        cases = [
            ("a polling client", _record(age_s=3600.0), polling, (False, "client_polling")),
            ("an unreadable bridge", _record(), None, (False, "bridge_status_unknown")),
            ("an unreadable age", _record(age_s=None), {"client_peer": _STALE_PEER},
             (False, "client_record_age_unknown")),
            ("a dead pid", _record(alive=False), {"client_peer": _STALE_PEER},
             (True, "client_process_dead")),
            # A poll 5 s ago is someone polling, not a stall.
            ("a recent poll, young", _record(age_s=30.0), recent,
             (False, "client_still_starting")),
            ("a recent poll, old", _record(age_s=3600.0), recent,
             (True, "client_not_accredited")),
        ]
        for label, record, payload, expected in cases:
            with self.subTest(label):
                self.assertEqual(self.decide(record, payload, True), expected)

    def test_the_budget_override_still_decides_the_verdict(self) -> None:
        self.assertEqual(
            self.decide(
                _record(age_s=30.0), {"client_peer": _STALE_PEER}, True, budget_s=0.0
            ),
            (True, "client_start_stalled"),
        )


class ClientStartStalledGateTest(unittest.IsolatedAsyncioTestCase):
    """dayz_test_run(mode=client, run_id) over a client whose RPT sits at its header."""

    def setUp(self) -> None:
        for name, value in (
            (
                "evaluate_steam_session",
                steam_preflight.SteamSessionResult(
                    error_code=None,
                    steam_registered_pid=1,
                    steam_live_pids=(1,),
                    remediation=steam_preflight.REMEDIATION,
                ),
            ),
            (
                "evaluate_prerun_desktop",
                mcp_capture.PrerunDesktopResult(
                    error_code=None,
                    desktop="unlocked",
                    mean_brightness=80.0,
                    nonblack_ratio=0.9,
                    waited_s=0.01,
                    remediation="",
                ),
            ),
            (
                "preflight_vpp_request",
                native_launcher_transaction.VppPreflightResult(
                    error_code=None,
                    missing=(),
                    warnings=(),
                    hint=native_launcher_transaction.VPP_PREFLIGHT_HINT,
                ),
            ),
        ):
            patcher = mock.patch.object(dayz_test_tool, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        alive = mock.patch.object(dayz_test_tool, "_pid_alive", return_value=True)
        alive.start()
        self.addCleanup(alive.stop)
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.dev_root = Path(self.temporary.name) / "ExampleMod_Suite"
        self.profiles = self.dev_root / "_client" / "profiles"
        self.profiles.mkdir(parents=True)

    def status(self, client_age_s: float, *, with_client: bool = True) -> dict[str, object]:
        """A /lifecycle/status row as the daemon publishes it (creation times too)."""
        born = (
            time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - client_age_s))
            + ".000000Z"
        )
        processes: list[dict[str, object]] = [
            {"pid": 4001, "role": "server", "creation_time_utc": born}
        ]
        if with_client:
            processes.append({"pid": 4002, "role": "client", "creation_time_utc": born})
        return {
            "runs": [
                {
                    "run_id": RUN_ID,
                    "state": "RUNNING_IDLE",
                    "mod": "@ExampleMod",
                    "profiles": str(self.dev_root / "_server" / "profiles"),
                    "launch_acknowledged": True,
                    "processes": processes,
                }
            ]
        }

    def client_rpt(self, data: bytes, written_s_ago: float) -> None:
        stamp = time.time() - written_s_ago
        _write(self.profiles / "DayZDiag_x64_2026-08-19_14-06-09.RPT", data, stamp)

    async def extend(
        self, *, client_age_s: float, budget_s: float | None = None
    ) -> tuple[dict[str, object], list[bytes]]:
        before = self.status(client_age_s)
        after = self.status(client_age_s, with_client=False)
        runtime = _Runtime(before)
        reads: list[int] = []

        async def lifecycle_status() -> dict[str, object]:
            reads.append(1)
            return before if len(reads) == 1 else after

        runtime.lifecycle_status = lifecycle_status  # type: ignore[method-assign]
        runtime.bridge_payload = {"client_peer": _STALE_PEER}
        sent: list[bytes] = []

        async def launch(raw_request: bytes, **kwargs: object) -> int:
            sent.append(raw_request)
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 0

        policy = _policy(dev_root=str(self.dev_root))
        with mock.patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), mock.patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), mock.patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="client",
                run_id=RUN_ID,
                extra_mods=["@DayZ_MCP"],
                client_start_budget_s=budget_s,
            )
        return result, sent

    async def test_a_client_stuck_at_its_header_is_named_and_not_touched(self) -> None:
        self.client_rpt(_HEADER, written_s_ago=40.0)
        result, sent = await self.extend(client_age_s=45.0)
        self.assertEqual(sent, [], "the sealed request must not leave")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "client_start_stalled")
        self.assertEqual(result["client_replace_reason"], "client_start_stalled")
        self.assertEqual(result["client_terminated"], 0)
        self.assertIs(result["client_relaunched"], False)
        self.assertIn("client_start_budget_s=0", str(result["remediation"]))

    async def test_the_named_override_supersedes_the_stalled_client(self) -> None:
        self.client_rpt(_HEADER, written_s_ago=40.0)
        result, sent = await self.extend(client_age_s=45.0, budget_s=0.0)
        self.assertEqual(len(sent), 1)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["client_replace_reason"], "client_start_stalled")

    async def test_a_client_that_got_past_its_header_is_still_starting(self) -> None:
        """Negative control: the RPT line after the header is the only change."""
        self.client_rpt(_HEADER + _FIRST_LINE, written_s_ago=40.0)
        result, sent = await self.extend(client_age_s=45.0)
        self.assertEqual(sent, [])
        self.assertEqual(result["error_code"], "client_still_starting")

    async def test_no_rpt_keeps_the_old_answer(self) -> None:
        result, sent = await self.extend(client_age_s=45.0)
        self.assertEqual(sent, [])
        self.assertEqual(result["error_code"], "client_still_starting")

    async def test_past_the_budget_the_stall_is_the_replace_reason(self) -> None:
        self.client_rpt(_HEADER, written_s_ago=3000.0)
        stalled, sent = await self.extend(client_age_s=3600.0)
        self.assertEqual(len(sent), 1)
        self.assertEqual(stalled["client_replace_reason"], "client_start_stalled")

        self.client_rpt(_HEADER + _FIRST_LINE, written_s_ago=3000.0)
        hung, sent = await self.extend(client_age_s=3600.0)
        self.assertEqual(len(sent), 1)
        self.assertEqual(hung["client_replace_reason"], "client_not_polling")


if __name__ == "__main__":
    unittest.main()
