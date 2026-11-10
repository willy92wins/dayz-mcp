"""steam_update_pending: Steam's appmanifest says DayZ or DayZ Tools has an update to apply.

The development PC read StateFlags 6 for DayZ on 2026-11-05 and for DayZServer on
2026-11-09, and 4 for DayZ and DayZ Tools on 2026-11-09.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import tests
from dayz_mcp import server
from dayz_mcp import steam_update_pending as sup
from dayz_mcp.dayz_tools_paths import DayZLayout
from dayz_mcp.server import ServerConfig, build_app
from tests.client_helpers import _fixture_client_runtime
from tests.mcp_helpers import _content_json


def _acf(app_id: int, flags: str) -> str:
    # The shape Steam writes, cut to a few keys around StateFlags.
    return (
        '"AppState"\n{\n'
        f'\t"appid"\t\t"{app_id}"\n'
        '\t"universe"\t\t"1"\n'
        f'\t"StateFlags"\t\t"{flags}"\n'
        '\t"installdir"\t\t"fixture"\n'
        '\t"UserConfig"\n\t{\n\t\t"language"\t\t"english"\n\t}\n'
        "}\n"
    )


class ObservationTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.steam = Path(temp.name) / "Steam"
        self.steamapps = self.steam / "steamapps"
        self.layout = DayZLayout(
            tools=self.steamapps / "common" / "DayZ Tools",
            steam=self.steam,
            dayz=self.steamapps / "common" / "DayZ",
        )
        self.layout.tools.mkdir(parents=True)
        self.layout.dayz.mkdir(parents=True)

    def write(self, app_id: int, text: str) -> Path:
        path = self.steamapps / f"appmanifest_{app_id}.acf"
        path.write_text(text, encoding="utf-8")
        return path

    def observe(self) -> dict | None:
        return sup.observe_steam_update_pending(self.layout)

    def test_the_bits_that_mean_an_update(self) -> None:
        cases = {
            4: False,  # quiet
            68: False,  # quiet, AppRunning
            6: True,  # update to apply, read on the PC
            1026: True,  # UpdateStarted, not fully installed
            1542: True,  # update paused
            0: True,  # not fully installed
        }
        for flags, expected in cases.items():
            with self.subTest(flags=flags):
                self.assertIs(sup.update_pending(flags), expected)

    def test_quiet_installs_add_nothing(self) -> None:
        for flags in ("4", "68"):
            with self.subTest(flags=flags):
                self.write(221100, _acf(221100, flags))
                self.write(830640, _acf(830640, flags))
                self.assertIsNone(self.observe())

    def test_each_app_with_an_update_is_named(self) -> None:
        dayz = self.write(221100, _acf(221100, "6"))
        tools = self.write(830640, _acf(830640, "1026"))
        self.assertEqual(
            self.observe(),
            {
                "code": sup.STEAM_UPDATE_PENDING,
                "apps": [
                    {"app_id": 221100, "name": "DayZ", "state_flags": 6, "manifest": str(dayz)},
                    {
                        "app_id": 830640,
                        "name": "DayZ Tools",
                        "state_flags": 1026,
                        "manifest": str(tools),
                    },
                ],
                "remediation": sup.REMEDIATION_STEAM_UPDATE_PENDING,
            },
        )

    def test_only_the_app_with_an_update_is_named(self) -> None:
        self.write(221100, _acf(221100, "4"))
        tools = self.write(830640, _acf(830640, "6"))
        advice = self.observe()
        self.assertEqual(
            advice["apps"],
            [{"app_id": 830640, "name": "DayZ Tools", "state_flags": 6, "manifest": str(tools)}],
        )

    def test_nothing_readable_adds_nothing(self) -> None:
        self.assertIsNone(self.observe(), "no manifest")
        cases = {
            "no StateFlags": '"AppState"\n{\n\t"appid"\t\t"221100"\n}\n',
            "not a number": _acf(221100, "six"),
            "empty": "",
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.write(221100, text)
                self.assertIsNone(self.observe())
        (self.steamapps / "appmanifest_221100.acf").unlink()
        (self.steamapps / "appmanifest_221100.acf").mkdir()
        self.assertIsNone(self.observe(), "a folder where the manifest goes")

    def test_a_layout_outside_steamapps_common_reads_nothing(self) -> None:
        # A manifest that says 6 sits where the parent rule would look, but
        # neither install is under steamapps\common.
        root = self.steam / "loose"
        layout = DayZLayout(
            tools=root / "Tools" / "DayZ Tools", steam=self.steam, dayz=root / "Game" / "DayZ"
        )
        for folder in (layout.tools.parent.parent, layout.dayz.parent.parent):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "appmanifest_221100.acf").write_text(_acf(221100, "6"), encoding="utf-8")
            (folder / "appmanifest_830640.acf").write_text(_acf(830640, "6"), encoding="utf-8")
        self.assertIsNone(sup.observe_steam_update_pending(layout))

    def test_the_default_reads_the_resolved_layout_and_a_failure_adds_nothing(self) -> None:
        self.write(221100, _acf(221100, "6"))
        with patch.object(sup, "resolved_layout", return_value=self.layout) as resolved:
            self.assertEqual(sup.observe_steam_update_pending()["apps"][0]["app_id"], 221100)
            resolved.assert_called_once_with()
        with patch.object(sup, "resolved_layout", side_effect=RuntimeError("fixture")):
            self.assertIsNone(sup.observe_steam_update_pending())


_ADVICE = {
    "code": sup.STEAM_UPDATE_PENDING,
    "apps": [
        {
            "app_id": 221100,
            "name": "DayZ",
            "state_flags": 6,
            "manifest": r"C:\Steam\steamapps\appmanifest_221100.acf",
        }
    ],
    "remediation": sup.REMEDIATION_STEAM_UPDATE_PENDING,
}
_TOOLS = {
    "session_status": {},
    "session_acquire": {"purpose": "fixture"},
    "session_acquire_wait": {"purpose": "fixture"},
}


class SessionToolsCarryTheAdvisoryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        config = ServerConfig(
            mode="client",
            key="fixture",
            port=12345,
            client_platform="codex",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
        self.runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            self.app, _built = build_app(config)
        for tool in _TOOLS:
            answer = AsyncMock(
                side_effect=lambda *_a, **_k: {"status": "active", "warnings": ["already_here"]}
            )
            patcher = patch.object(self.runtime, tool, new=answer)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def call(self, tool: str) -> dict:
        return _content_json(await self.app.call_tool(tool, _TOOLS[tool]))

    def test_the_suite_reads_no_manifest(self) -> None:
        self.assertIs(server.observe_steam_update_pending, tests.no_steam_update_pending)
        self.assertIsNot(server.observe_steam_update_pending, sup.observe_steam_update_pending)
        self.assertIsNone(tests.no_steam_update_pending())

    async def test_each_session_tool_warns_and_keeps_its_answer(self) -> None:
        for tool in _TOOLS:
            with self.subTest(tool), patch.object(
                server, "observe_steam_update_pending", return_value=dict(_ADVICE)
            ):
                result = await self.call(tool)
                self.assertEqual(result["status"], "active")
                self.assertEqual(result["warnings"][0], "already_here")
                self.assertEqual(result["warnings"].count(sup.STEAM_UPDATE_PENDING), 1)
                self.assertEqual(result["steam_update_pending"], _ADVICE)

    async def test_a_quiet_or_failed_observation_adds_nothing(self) -> None:
        for name, behaviour in (
            ("quiet", {"return_value": None}),
            ("failed", {"side_effect": RuntimeError("fixture observe")}),
        ):
            for tool in _TOOLS:
                with self.subTest(name=name, tool=tool), patch.object(
                    server, "observe_steam_update_pending", **behaviour
                ):
                    result = await self.call(tool)
                    self.assertEqual(result["status"], "active")
                    self.assertNotIn("steam_update_pending", result)
                    self.assertNotIn(sup.STEAM_UPDATE_PENDING, result["warnings"])


if __name__ == "__main__":
    unittest.main()
