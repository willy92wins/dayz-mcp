"""Offline guards for B2 cards 2edd-2 and dae1-3.

2edd-2: dayz_test_stop / stop_run must say forced kill and that exit metrics
are not valid (Leaked / Destroying game lines were never flushed).

dae1-3: _stop_artifacts for a server-anchored run must still collect
_client\\profiles (hung-client RPTs; A4 MEDIO / 8f76).
"""

from __future__ import annotations

import unittest

from dayz_mcp import dayz_test_request, dayz_test_tool
from dayz_mcp.server import ServerConfig, build_app


def _policy() -> dayz_test_request.RequestProjectPolicy:
    return dayz_test_request.RequestProjectPolicy(
        mod="ExampleMod",
        dev_root=r"P:\ExampleMod_Suite",
        default_source=r"P:\ExampleMod",
        default_base_mods=("@CF", "@Dabs Framework"),
        mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
        mod_roots=(r"P:\Mods",),
    )


class StopArtifactsDae13Test(unittest.TestCase):
    def test_server_anchored_stop_includes_client_profiles(self) -> None:
        policy = _policy()
        server = r"P:\ExampleMod_Suite\_server\profiles"
        client = r"P:\ExampleMod_Suite\_client\profiles"
        run = {
            "profiles": server,
            "processes": [{"role": "server"}],
        }
        self.assertEqual(
            dayz_test_tool._stop_artifacts(policy, run),
            [server, client],
        )

    def test_server_anchored_with_empty_processes_includes_client(self) -> None:
        policy = _policy()
        server = r"P:\ExampleMod_Suite\_server\profiles"
        client = r"P:\ExampleMod_Suite\_client\profiles"
        self.assertEqual(
            dayz_test_tool._stop_artifacts(
                policy, {"profiles": server, "processes": []}
            ),
            [server, client],
        )

    def test_client_only_anchor_stays_single_root(self) -> None:
        policy = _policy()
        client = r"P:\ExampleMod_Suite\_client\profiles"
        self.assertEqual(
            dayz_test_tool._stop_artifacts(
                policy, {"profiles": client, "processes": [{"role": "client"}]}
            ),
            [client],
        )

    def test_both_roles_still_union(self) -> None:
        policy = _policy()
        expected = dayz_test_tool._artifact_paths(policy, "all")
        run = {
            "profiles": expected[0],
            "processes": [{"role": "server"}, {"role": "client"}],
        }
        self.assertEqual(dayz_test_tool._stop_artifacts(policy, run), expected)


class StopDocs2edd2Test(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, _runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )
        self.tools = {tool.name: tool for tool in await self.app.list_tools()}

    def test_dayz_test_stop_description_names_forced_kill_and_invalid_metrics(
        self,
    ) -> None:
        description = self.tools["dayz_test_stop"].description or ""
        self.assertIn("forced process kill", description)
        self.assertIn("exit_metrics_valid", description)
        self.assertIn("not valid", description)
        self.assertIn("Leaked", description)


if __name__ == "__main__":
    unittest.main()
