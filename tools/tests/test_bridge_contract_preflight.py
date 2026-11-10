"""fb-20261102-120351-9077: a server start reads the bridge PBO's arg contract first.

The PBOs are built in memory and handed to the gate through a fake
read_bridge_pbos, so no test reads a deployed PBO. The host reader is tested on
temp folders only.
"""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dayz_mcp import (
    dayz_test_tool as tool,
    native_launcher_transaction as nlt,
    steam_preflight as steam,
)
from dayz_mcp.bridge_readiness import EXPECTED_SERVER_ARG_CONTRACT_HASH as EXPECTED
import mcp_capture
from tests import dayz_test_tool_helpers as fixtures
from tests._addon_paths import addon_root
from tests.pbo_helpers import build_pbo


# The contract the stable MCP expected in the ticket, while the PBO said 86e3c48a.
OTHER = "0401bed390754a55"
BRIDGE = r"P:\Mods\@DayZ_MCP"
_SOURCE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"


def _bridge_pbo(contract: str) -> bytes:
    """A plain PBO of the real MCPBridge.c, declaring ``contract``."""
    source = _SOURCE.read_bytes()
    assert source.count(EXPECTED.encode("ascii")) == 1
    return build_pbo(
        [
            (
                "scripts\\5_Mission\\MCPBridge.c",
                source.replace(EXPECTED.encode("ascii"), contract.encode("ascii")),
            )
        ]
    )


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "mode": "server",
        "mod": "ExampleMod",
        "build": False,
        "base_mods": [],
        "extra_mods": ["@DayZ_MCP"],
        "server_only_mods": [],
        "project_mod_override": False,
    }
    payload.update(overrides)
    return payload


class BridgeContractGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = fixtures._policy()
        self.reads: list[str] = []
        self.disk: dict[str, list[bytes]] = {BRIDGE: [_bridge_pbo(OTHER)]}
        patcher = patch.object(nlt, "read_bridge_pbos", side_effect=self._read)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _read(self, path: str) -> list[bytes]:
        self.reads.append(path)
        if path not in self.disk:
            raise FileNotFoundError(path)
        return self.disk[path]

    def gate(self, policy=None, **overrides: object) -> tuple[str, ...]:
        return nlt.bridge_contract_mismatch(
            _payload(**overrides), policy or self.policy, expected=EXPECTED
        )

    def test_the_gate_reads_the_contract_the_addon_declares(self) -> None:
        self.assertEqual(
            nlt._SERVER_ARG_CONTRACT_DECL.findall(_SOURCE.read_bytes()),
            [EXPECTED.encode("ascii")],
        )

    def test_a_server_start_names_the_contract_of_another_commit(self) -> None:
        for mode in ("server", "all"):
            with self.subTest(mode=mode):
                self.reads.clear()
                self.assertEqual(self.gate(mode=mode), (OTHER,))
                self.assertEqual(self.reads, [BRIDGE])

    def test_the_bridge_counts_from_server_only_mods_and_as_the_project(self) -> None:
        self.assertEqual(
            self.gate(extra_mods=[], server_only_mods=["@DayZ_MCP"]), (OTHER,)
        )
        self.assertEqual(self.gate(mod="DayZ_MCP", extra_mods=[]), (OTHER,))

    def test_the_expected_contract_passes(self) -> None:
        self.disk[BRIDGE] = [_bridge_pbo(EXPECTED)]
        self.assertEqual(self.gate(), ())

    def test_one_pbo_with_the_expected_contract_passes(self) -> None:
        self.disk[BRIDGE] = [_bridge_pbo(OTHER), _bridge_pbo(EXPECTED)]
        self.assertEqual(self.gate(), ())

    def test_what_cannot_be_read_refuses_nothing(self) -> None:
        cases = {
            "no folder": None,
            "no pbo": [],
            "no declaration": [build_pbo([("config.cpp", b"class CfgPatches {};")])],
        }
        for name, blobs in cases.items():
            with self.subTest(name):
                if blobs is None:
                    self.disk.pop(BRIDGE, None)
                else:
                    self.disk[BRIDGE] = blobs
                self.assertEqual(self.gate(), ())

    def test_a_start_with_no_server_reads_nothing(self) -> None:
        self.assertEqual(self.gate(mode="client"), ())
        self.assertEqual(self.reads, [])

    def test_a_run_that_builds_the_bridge_reads_nothing(self) -> None:
        self.assertEqual(self.gate(mod="DayZ_MCP", extra_mods=[], build=True), ())
        self.assertEqual(self.reads, [])

    # fb-20261109-130856-f28c: LFPowerGrid declares three mod roots, the gate
    # read no PBO for its @DayZ_MCP, and 44 launches loaded a bridge of another
    # contract. The worker uses one of the roots; the gate cannot tell which.
    def _roots(self, *roots: str):
        return dataclasses.replace(self.policy, mod_roots=roots)

    def test_a_relative_bridge_is_read_under_every_root(self) -> None:
        policy = self._roots(r"P:\Other", r"P:\Mods", r"P:\Third")
        self.assertEqual(self.gate(policy), (OTHER,))
        self.assertEqual(
            self.reads, [r"P:\Other\@DayZ_MCP", BRIDGE, r"P:\Third\@DayZ_MCP"]
        )

    def test_the_expected_contract_under_any_root_passes(self) -> None:
        self.disk[r"P:\Third\@DayZ_MCP"] = [_bridge_pbo(EXPECTED)]
        self.assertEqual(
            self.gate(self._roots(r"P:\Other", r"P:\Mods", r"P:\Third")), ()
        )

    def test_roots_with_nothing_to_read_refuse_nothing(self) -> None:
        self.disk.clear()
        self.assertEqual(self.gate(self._roots(r"P:\Other", r"P:\Mods")), ())
        self.assertEqual(self.reads, [r"P:\Other\@DayZ_MCP", BRIDGE])
        self.reads.clear()
        self.assertEqual(self.gate(self._roots()), ())
        self.assertEqual(self.reads, [])

    def test_an_absolute_bridge_is_read_only_at_its_path(self) -> None:
        policy = self._roots(r"P:\Other", r"P:\Mods")
        self.assertEqual(self.gate(policy, extra_mods=[BRIDGE]), (OTHER,))
        self.assertEqual(self.reads, [BRIDGE])


class HostBridgePboReaderTest(unittest.TestCase):
    def _addons(self, root: str, name: str = "addons") -> Path:
        addons = Path(root, "@DayZ_MCP", name)
        addons.mkdir(parents=True)
        return addons

    def test_every_pbo_of_the_addons_folder_and_nothing_else(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            addons = self._addons(root)
            (addons / "DayZ_MCP.pbo").write_bytes(b"a")
            (addons / "Extra.PBO").write_bytes(b"b")
            (addons / "DayZ_MCP.pbo.bak_pre").write_bytes(_bridge_pbo(OTHER))
            (addons / "DayZ_MCP.pbo.DayZ_MCP.bisign").write_bytes(b"d")
            self.assertEqual(
                nlt._read_bridge_pbos(str(Path(root, "@DayZ_MCP"))), [b"a", b"b"]
            )

    def test_a_missing_folder_or_an_oversize_pbo_raises(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            mod = str(Path(root, "@DayZ_MCP"))
            with self.assertRaises(OSError):
                nlt._read_bridge_pbos(mod)
            (self._addons(root) / "DayZ_MCP.pbo").write_bytes(b"x" * 17)
            with patch.object(nlt, "_MAX_BRIDGE_PBO_BYTES", 16):
                with self.assertRaises(OSError):
                    nlt._read_bridge_pbos(mod)
            with patch.object(nlt, "_MAX_BRIDGE_PBO_BYTES", 17):
                self.assertEqual(nlt._read_bridge_pbos(mod), [b"x" * 17])

    @unittest.skipUnless(os.name == "nt", "the deployed folder is Addons on NTFS")
    def test_the_deployed_addons_folder_is_found_whatever_its_case(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            (self._addons(root, "Addons") / "DayZ_MCP.pbo").write_bytes(b"a")
            self.assertEqual(
                nlt._read_bridge_pbos(str(Path(root, "@DayZ_MCP"))), [b"a"]
            )

    def test_the_test_package_reads_no_deployed_pbo(self) -> None:
        self.assertIsNot(nlt.read_bridge_pbos, nlt._read_bridge_pbos)
        with self.assertRaises(OSError):
            nlt.read_bridge_pbos(BRIDGE)


class DayzTestRunContractGateTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.policy = fixtures._policy()
        self.runtime = fixtures._Runtime({"runs": []})
        self.vpp = SimpleNamespace(error_code=None, missing=(), warnings=(), hint="")
        self.launch = AsyncMock(side_effect=self._launch)
        self.evaluate = self._patch(
            tool,
            "evaluate_steam_session",
            return_value=steam.SteamSessionResult(
                error_code=None,
                steam_registered_pid=41,
                steam_live_pids=(41,),
                remediation=steam.REMEDIATION,
            ),
        )
        self._patch(tool, "open_approved_launcher", return_value=fixtures._Opened())
        self._patch(
            tool.secure_launcher,
            "load_verified_bundle",
            return_value=fixtures._Bundle(fixtures._sealed(self.policy)),
        )
        self._patch(tool, "preflight_vpp_request", return_value=self.vpp)
        self._patch(
            tool.secure_launcher, "execute_secure_launcher_request", new=self.launch
        )
        self.desktop = self._patch(
            tool,
            "evaluate_prerun_desktop",
            return_value=mcp_capture.PrerunDesktopResult(
                error_code=None,
                desktop="unlocked",
                mean_brightness=80.0,
                nonblack_ratio=0.9,
                waited_s=0.01,
                remediation="",
            ),
        )
        self.reads: list[str] = []
        self.pbos = [_bridge_pbo(OTHER)]
        self._patch(nlt, "read_bridge_pbos", side_effect=self._read)

    def _patch(self, owner, name, **kwargs):
        patcher = patch.object(owner, name, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def _read(self, path: str) -> list[bytes]:
        self.reads.append(path)
        return self.pbos

    async def _launch(self, raw_request, **kwargs):
        request = json.loads(raw_request)
        await kwargs["execution_started_cb"]()
        kwargs["output_sink"](
            "stdout",
            fixtures._terminal(
                {
                    "cleanup_degraded": False,
                    "error_code": None,
                    "exit_code": 0,
                    "ok": True,
                    "run_id": request.get("run_id") if request["preflight"] else fixtures.RUN_ID,
                }
            ),
        )
        return 0

    async def run_tool(self, **kwargs):
        return await tool.execute_dayz_test_run(
            self.runtime, project="ExampleMod", extra_mods=["@DayZ_MCP"], **kwargs
        )

    def assert_refused(self, result: dict[str, object]) -> None:
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "arg_contract_mismatch")
        self.assertEqual(result["phase"], "validating")
        self.assertIsNone(result["run_id"])
        self.assertIn(OTHER, result["remediation"])
        self.assertIn(EXPECTED, result["remediation"])
        self.assertEqual(self.reads, [BRIDGE])
        self.launch.assert_not_awaited()
        self.evaluate.assert_not_called()
        self.desktop.assert_not_called()

    async def test_a_server_start_is_refused_before_anything_starts(self) -> None:
        for mode in ("server", "all"):
            with self.subTest(mode=mode):
                self.reads.clear()
                self.assert_refused(await self.run_tool(mode=mode))

    async def test_the_bridge_the_tool_adds_by_default_is_read(self) -> None:
        self.assert_refused(
            await tool.execute_dayz_test_run(
                self.runtime, project="ExampleMod", mode="server"
            )
        )

    async def test_a_preflight_is_refused_and_keeps_its_omissions(self) -> None:
        result = await self.run_tool(mode="server", preflight=True)
        self.assert_refused(result)
        self.assertEqual(
            result["preflight_skipped_checks"],
            ["process_launch", "readiness", "initialization_evidence"],
        )

    async def test_the_expected_contract_launches(self) -> None:
        self.pbos = [_bridge_pbo(EXPECTED)]
        result = await self.run_tool(mode="server")
        self.assertEqual(self.reads, [BRIDGE])
        self.launch.assert_awaited_once()
        self.assertEqual(result["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
