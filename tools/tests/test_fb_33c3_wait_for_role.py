"""fb-20261026-203149-33c3: wait_for(log_matches) reads only the side role names.

wait_for(log_matches, pattern="Module: World;", role=server,
lookback_from=launch) answered satisfied with the client's line, and
scanned.files listed only the _client logs: log_matches read every side's logs
whatever role said, so a check "by role" passed on the other side's line.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import server
from dayz_mcp.server import ServerConfig, build_app
from tests.client_helpers import _fixture_client_runtime
from tests.mcp_helpers import _content_json

_CLIENT_LINE = 'SCRIPT : Module: World; loaded 2349x files defines: "DAYZ_1_30,PLATFORM_WINDOWS,DIAG"'
_SERVER_LINE = 'SCRIPT : Module: World; loaded 2351x files defines: "DAYZ_1_30,SERVER_FOR_WINDOWS,SERVER"'


class _FakeRuntime:
    """What execute_wait_for touches: a lock and one live run."""

    def __init__(self, profiles: Path) -> None:
        self.tool_lock = asyncio.Lock()
        started = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        run = {
            "run_id": "run-a",
            "mod": "@ExampleMod",
            "profiles": str(profiles),
            "processes": [{"pid": 4242, "creation_time_utc": started}],
        }

        async def lifecycle_status() -> dict:
            return {"runs": [run]}

        self.lifecycle_status = lifecycle_status

    async def call_bridge(self, cmd: str, args: dict, peer: str, timeout_s: float) -> dict:
        raise AssertionError(f"log_matches must not call the bridge (got {cmd})")


def _two_sides(directory: str) -> list[Path]:
    """The run's two profile folders, as _profile_dirs_from_runs lists them.

    Which folders a run may read (the sealed anchor, the bound leaf) is
    _profile_dirs_from_runs's own contract, pinned elsewhere; here it hands
    over both sides so the test is about the role alone.
    """

    root = Path(directory)
    server_profiles = root / "_server" / "profiles"
    client_profiles = root / "_client" / "profiles"
    server_profiles.mkdir(parents=True)
    client_profiles.mkdir(parents=True)
    (server_profiles / "script_2026-10-26_21-09-29.log").write_text(
        "SCRIPT : first\n" + _SERVER_LINE + "\n", encoding="utf-8"
    )
    (client_profiles / "script_2026-10-26_21-09-46.log").write_text(
        "SCRIPT : first\n" + _CLIENT_LINE + "\n", encoding="utf-8"
    )
    return [client_profiles, server_profiles]


def _sides(result: dict) -> set[str]:
    return {entry["name"].split("/", 1)[0] for entry in result["scanned"]["files"]}


def _public_app():
    """The registered tools, over a client runtime that reaches no daemon."""

    config = ServerConfig(
        mode="client",
        key="k",
        port=12345,
        client_platform="codex",
        log_sink=lambda _message: None,
    )
    runtime = _fixture_client_runtime(config)
    with patch.object(server, "ClientRuntime", return_value=runtime):
        app, _built = build_app(config)
    return app, runtime


class WaitForRoleTest(unittest.IsolatedAsyncioTestCase):
    async def _wait(self, dirs: list[Path], pattern: str = "Module: World;", **kwargs) -> dict:
        with patch.object(
            server, "_profile_dirs_from_runs", return_value=[str(item) for item in dirs]
        ):
            return await server.execute_wait_for(
                _FakeRuntime(dirs[-1]),
                "log_matches",
                pattern=pattern,
                timeout_s=0.6,
                poll_interval_s=0.5,
                **kwargs,
            )

    async def test_role_reads_only_that_sides_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dirs = _two_sides(directory)
            for role, line, side in (
                ("server", _SERVER_LINE, "_server"),
                ("client", _CLIENT_LINE, "_client"),
                ("offline", _CLIENT_LINE, "_client"),
            ):
                # Both orders: a scan of every side that stops at the first
                # match passes when the role's side happens to come first.
                for order in (dirs, dirs[::-1]):
                    for lookback_from in ("launch", "lines"):
                        with self.subTest(
                            role=role,
                            first=order[0].parent.name,
                            lookback_from=lookback_from,
                        ):
                            result = await self._wait(
                                order, role=role, lookback_from=lookback_from
                            )
                            self.assertTrue(result["satisfied"])
                            self.assertEqual(result["observed"], line)
                            self.assertEqual(_sides(result), {side})

    async def test_without_role_both_sides_are_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dirs = _two_sides(directory)
            result = await self._wait(
                dirs, pattern="ABSENT-ON-PURPOSE", lookback_from="launch"
            )
        self.assertFalse(result["satisfied"])
        self.assertEqual(_sides(result), {"_client", "_server"})

    async def test_a_role_the_run_has_no_logs_for_is_profile_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client_profiles = Path(directory) / "_client" / "profiles"
            client_profiles.mkdir(parents=True)
            (client_profiles / "script.log").write_text(_CLIENT_LINE + "\n", encoding="utf-8")
            with self.assertRaises(ToolError) as raised:
                await self._wait([client_profiles], role="server", lookback_from="launch")
        self.assertEqual(str(raised.exception), "profile_unresolved")

    async def test_a_side_folder_with_no_log_of_this_launch_waits(self) -> None:
        # The folder decides profile_unresolved, not its logs: a folder kept from
        # an earlier launch, or one with no log yet, is waited on (inbox 33c3).
        with tempfile.TemporaryDirectory() as directory:
            client_profiles, server_profiles = _two_sides(directory)
            old = client_profiles / "script_2026-10-26_21-09-46.log"
            stale = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
            os.utime(old, (stale, stale))
            for name, setup in (("stale", lambda: None), ("empty", old.unlink)):
                with self.subTest(folder=name):
                    setup()
                    result = await self._wait(
                        [client_profiles, server_profiles],
                        role="client",
                        lookback_from="launch",
                    )
                    self.assertFalse(result["satisfied"])
                    self.assertEqual(result["scanned"]["files"], [])

    async def test_a_marker_with_role_reads_only_that_side(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dirs = _two_sides(directory)
            client_log = dirs[0] / "script_2026-10-26_21-09-46.log"
            app, runtime = _public_app()
            fake = _FakeRuntime(dirs[-1])
            listed = [str(item) for item in dirs]
            with patch.object(runtime, "lifecycle_status", new=fake.lifecycle_status), patch.object(
                server, "_profile_dirs_from_runs", return_value=listed
            ):
                marker = _content_json(await app.call_tool("logs_since", {}))["marker"]
                with client_log.open("a", encoding="utf-8") as handle:
                    handle.write("SCRIPT : ONLY-CLIENT after the marker\n")
                for role, satisfied, sides in (
                    ("server", False, {"_server"}),
                    ("client", True, {"_client"}),
                ):
                    for lookback_from in ("launch", "lines"):
                        with self.subTest(role=role, lookback_from=lookback_from):
                            result = _content_json(
                                await app.call_tool(
                                    "wait_for",
                                    {
                                        "condition": "log_matches",
                                        "pattern": "ONLY-CLIENT",
                                        "role": role,
                                        "marker": marker,
                                        "lookback_from": lookback_from,
                                        "timeout_s": 0.6,
                                        "poll_interval_s": 0.5,
                                    },
                                )
                            )
                            self.assertEqual(result["satisfied"], satisfied)
                            self.assertEqual(_sides(result), sides)

    async def test_the_tool_without_role_reads_both_sides(self) -> None:
        # The tool's own default, not only the executor's (inbox 33c3).
        with tempfile.TemporaryDirectory() as directory:
            dirs = _two_sides(directory)
            app, runtime = _public_app()
            fake = _FakeRuntime(dirs[-1])
            for order in (dirs, dirs[::-1]):
                with self.subTest(first=order[0].parent.name):
                    with patch.object(
                        runtime, "lifecycle_status", new=fake.lifecycle_status
                    ), patch.object(
                        server,
                        "_profile_dirs_from_runs",
                        return_value=[str(item) for item in order],
                    ):
                        result = _content_json(
                            await app.call_tool(
                                "wait_for",
                                {
                                    "condition": "log_matches",
                                    "pattern": "loaded 2349x",
                                    "lookback_from": "launch",
                                    "timeout_s": 0.6,
                                    "poll_interval_s": 0.5,
                                },
                            )
                        )
                    self.assertTrue(result["satisfied"])
                    self.assertEqual(result["observed"], _CLIENT_LINE)

    async def test_an_unknown_role_is_bad_args(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dirs = _two_sides(directory)
            with self.assertRaises(ToolError) as raised:
                await self._wait(dirs, role="both")
        self.assertIn("role", str(raised.exception))
        self.assertTrue(str(raised.exception).startswith("bad_args"))

    async def test_file_matches_still_defaults_role_to_server(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dirs = _two_sides(directory)
            for kwargs, role in (({}, "server"), ({"role": "client"}, "client")):
                with self.subTest(role=role):
                    file_matches = AsyncMock(return_value={"ok": True})
                    with patch.object(server, "_execute_file_matches", file_matches):
                        await server.execute_wait_for(
                            _FakeRuntime(dirs[-1]),
                            "file_matches",
                            pattern="MARKER",
                            profile_file="mission.log",
                            timeout_s=0.6,
                            poll_interval_s=0.5,
                            **kwargs,
                        )
                    self.assertEqual(file_matches.await_args.kwargs["role"], role)


if __name__ == "__main__":
    unittest.main()
