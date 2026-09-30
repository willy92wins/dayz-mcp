"""An honest, complete tool catalog (fb-20260924-235524-e7ef, fb-20260925-233937-b753).

e7ef: Claude Code never re-lists after tools/list_changed, so the compact
pre-lease catalog hid world_*/vehicle_*/ui_* for its whole session. A client
registered with --client-platform claude now lists what
--no-progressive-disclosure lists, from the start.

b753, for the platforms that keep progressive disclosure:
- tools/list_changed follows the catalog both ways: after a grant, after
  session_release, and after a call that finds the lease expired or lost.
  Only a change is announced, and a failed notification never fails the call.
- The reads that need no lease are in the compact catalog.
- A compact description keeps whole sentences, or is cut at a word boundary
  with an explicit marker; never inside a word.
- The listing is not an access control: a tool it leaves out still runs when
  called by name, and a mutation without a lease is still refused.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import mcp.types as mcp_types
from mcp.server.session import ServerSession
from mcp.shared.memory import create_connected_server_and_client_session

from dayz_mcp import control_client, dayz_test_tool, server
from dayz_mcp.server import ServerConfig, build_app, parse_args
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS
from tests.client_helpers import _fixture_client_runtime
from tests.daemon_helpers import DaemonHttpServer, _attach_fixture_transport, _config
from tests.lifecycle_helpers import stamp_launcher
from tests._tiers import slow_test


_MARKER = "…"
# Where a word cut may stop short of the next space: _compact_description drops
# these when they would dangle before the marker.
_DANGLING_SEPARATORS = ",;:|/-=>+&"


class _ListingRuntime:
    """Enough of a client runtime for tools/list: the config and the lease token."""

    def __init__(self, config: ServerConfig, **_kwargs: object) -> None:
        self.config = config
        self.active_lease_token = None
        self._registered_tool_names = None


def _client_config(**fields: Any) -> ServerConfig:
    base: dict[str, Any] = dict(
        mode="client", key="k", port=12345, log_sink=lambda _message: None
    )
    base.update(fields)
    return ServerConfig(**base)


def _listing_app(config: ServerConfig):
    with patch("dayz_mcp.server.ClientRuntime", _ListingRuntime):
        return build_app(config)


def _names(tools: Any) -> set[str]:
    return {tool.name for tool in tools}


async def _protocol_tools(app) -> list[Any]:
    """tools/list as an MCP host receives it, not app.list_tools()."""
    async with create_connected_server_and_client_session(app._mcp_server) as session:
        return list((await session.list_tools()).tools)


def _bridge_commands_by_tool() -> dict[str, set[str]]:
    """Every bridge command each public tool fronts, per _BRIDGE_COMMAND_TOOLS."""
    fronted: dict[str, set[str]] = {}
    for mapping in server._BRIDGE_COMMAND_TOOLS.values():
        for command, tool in mapping.items():
            if tool is not None:
                fronted.setdefault(tool, set()).add(command)
    return fronted


def _lease_free_read_tools() -> set[str]:
    """Tools whose every bridge command is read-only, plus logs_since.

    logs_since is the one READ_ONLY_COMMANDS entry no bridge tool fronts: the
    tool of that name reads host log files and never reaches the game.
    """
    fronted = _bridge_commands_by_tool()
    reads = {tool for tool, commands in fronted.items() if commands <= READ_ONLY_COMMANDS}
    unfronted = READ_ONLY_COMMANDS - set().union(*fronted.values())
    return reads | unfronted


def _mutating_bridge_tools() -> set[str]:
    return {
        tool
        for tool, commands in _bridge_commands_by_tool().items()
        if commands - READ_ONLY_COMMANDS
    }


class ClaudeFullCatalogTest(unittest.IsolatedAsyncioTestCase):
    """e7ef: the platform that never re-lists gets the whole catalog up front."""

    async def test_claude_lists_the_unfiltered_catalog_before_a_lease(self) -> None:
        app, runtime = _listing_app(_client_config(client_platform="claude"))
        self.assertIsNone(runtime.active_lease_token)

        listed = await app.list_tools()
        unfiltered = await server.FastMCP.list_tools(app)

        self.assertEqual(
            [tool.model_dump() for tool in listed],
            [tool.model_dump() for tool in unfiltered],
        )
        self.assertIn("world_spawn", _names(listed))
        self.assertTrue(any(tool.outputSchema is not None for tool in listed))

    @slow_test
    async def test_claude_protocol_list_equals_the_opt_out_list(self) -> None:
        # "Exactly what --no-progressive-disclosure gives": every tool, full
        # descriptions and outputSchema, as the host receives them.
        claude_app, _ = _listing_app(_client_config(client_platform="claude"))
        opt_out_app, _ = _listing_app(
            _client_config(client_platform="codex", progressive_disclosure=False)
        )

        def by_name(tools: list[Any]) -> dict[str, dict[str, Any]]:
            return {
                tool.name: tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                for tool in tools
            }

        claude = by_name(await _protocol_tools(claude_app))
        opt_out = by_name(await _protocol_tools(opt_out_app))

        self.assertEqual(claude, opt_out)
        self.assertEqual(
            set(claude), {tool.name for tool in claude_app._tool_manager.list_tools()}
        )
        self.assertTrue(any("outputSchema" in tool for tool in claude.values()))

    async def test_the_claude_flag_is_still_accepted(self) -> None:
        base = [
            "--client", "--keyfile", "k", "--tool-pack", "full",
            "--client-platform", "claude",
        ]
        for argv in (base, [*base, "--no-progressive-disclosure"]):
            with self.subTest(argv=argv):
                app, _ = _listing_app(parse_args(argv))
                self.assertIn("world_spawn", _names(await app.list_tools()))

    async def test_other_platforms_keep_the_compact_default_and_the_opt_out(self) -> None:
        for platform in ("codex", "unknown"):
            with self.subTest(platform=platform):
                compact_app, _ = _listing_app(_client_config(client_platform=platform))
                opt_out_app, _ = _listing_app(
                    _client_config(client_platform=platform, progressive_disclosure=False)
                )
                self.assertNotIn("world_spawn", _names(await compact_app.list_tools()))
                self.assertIn("world_spawn", _names(await opt_out_app.list_tools()))


class _ScriptedDaemon:
    """The daemon as the client runtime sees it: canned replies for one lease."""

    TOKEN = "token-a"

    def __init__(self) -> None:
        self.operation_id: str | None = None
        self.heartbeat_error: str | None = None
        self.enqueue_error: str | None = None

    def control(
        self, path: str, payload: dict[str, Any], _timeout_s: float
    ) -> dict[str, Any]:
        if path == "/session/enqueue":
            self.operation_id = payload["operation_id"]
            return {
                "status": "queued",
                "ticket": "ticket-a",
                "position": 1,
                "operation_id": self.operation_id,
            }
        if path == "/session/wait":
            return {
                "status": "active",
                "lease_token": self.TOKEN,
                "lease_id": "lease-a",
                "ticket": "ticket-a",
                "operation_id": self.operation_id,
            }
        if path == "/session/heartbeat":
            if self.heartbeat_error is not None:
                raise control_client.ControlClientError(
                    self.heartbeat_error, request_stage="post_request", http_bytes_sent=1
                )
            return {"status": "active", "lease_id": "lease-a"}
        if path == "/session/release":
            if payload.get("lease_token") != self.TOKEN:
                raise control_client.ControlClientError(
                    "lease_invalid", request_stage="post_request", http_bytes_sent=1
                )
            return {"released": True}
        raise AssertionError(f"unexpected control request {path}")

    def bridge(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        timeout: float = 5.0,
    ) -> tuple[int, dict[str, Any]]:
        if method == "POST" and path == "/enqueue" and self.enqueue_error is not None:
            return 409, {"error": self.enqueue_error}
        raise AssertionError(f"unexpected bridge request {method} {path}")


@contextlib.asynccontextmanager
async def _recording_session(app):
    """An MCP session plus the tools/list_changed notifications it received."""
    changed: list[Any] = []

    async def record(message: Any) -> None:
        if isinstance(message, mcp_types.ServerNotification) and isinstance(
            message.root, mcp_types.ToolListChangedNotification
        ):
            changed.append(message.root)

    async with create_connected_server_and_client_session(
        app._mcp_server, message_handler=record
    ) as session:
        yield session, changed


async def _listed(session) -> set[str]:
    return _names((await session.list_tools()).tools)


class CatalogChangeNoticeTest(unittest.IsolatedAsyncioTestCase):
    """b753: tools/list_changed is sent whenever the visible catalog flips."""

    def _app(self, platform: str = "codex"):
        config = _client_config(
            key="test-key", client_platform=platform, auto_spawn_daemon=False
        )
        runtime = _fixture_client_runtime(config)
        daemon = _ScriptedDaemon()
        runtime._control._request_once = daemon.control
        runtime._request_once = daemon.bridge
        with patch("dayz_mcp.server.ClientRuntime", return_value=runtime):
            app, built = build_app(config)
        self.assertIs(built, runtime)
        return app, runtime, daemon

    async def _acquire(self, session, runtime) -> None:
        acquired = await session.call_tool(
            "session_acquire_wait", {"purpose": "catalog"}
        )
        self.assertFalse(acquired.isError, acquired.content)
        self.assertEqual(runtime.active_lease_token, _ScriptedDaemon.TOKEN)

    async def test_acquire_and_release_each_announce_the_flip(self) -> None:
        app, runtime, _daemon = self._app()
        async with _recording_session(app) as (session, changed):
            before = await _listed(session)
            await self._acquire(session, runtime)
            self.assertEqual(len(changed), 1)
            during = await _listed(session)

            released = await session.call_tool(
                "session_release", {"lease_token": _ScriptedDaemon.TOKEN}
            )

            self.assertFalse(released.isError, released.content)
            self.assertIsNone(runtime.active_lease_token)
            self.assertEqual(len(changed), 2)
            after = await _listed(session)
        self.assertNotIn("world_spawn", before)
        self.assertIn("world_spawn", during)
        self.assertEqual(after, before)

    async def test_a_heartbeat_that_finds_the_lease_expired_announces_the_flip(
        self,
    ) -> None:
        app, runtime, daemon = self._app()
        async with _recording_session(app) as (session, changed):
            await self._acquire(session, runtime)
            daemon.heartbeat_error = "lease_expired"

            beat = await session.call_tool(
                "session_heartbeat", {"lease_token": _ScriptedDaemon.TOKEN}
            )

            self.assertTrue(beat.isError)
            self.assertIn("lease_expired", beat.content[0].text)
            self.assertIsNone(runtime.active_lease_token)
            self.assertEqual(len(changed), 2)
            self.assertNotIn("world_spawn", await _listed(session))

    async def test_a_verb_that_finds_the_lease_lost_announces_the_flip(self) -> None:
        for code in ("lease_expired", "lease_invalid"):
            with self.subTest(code=code):
                app, runtime, daemon = self._app()
                async with _recording_session(app) as (session, changed):
                    await self._acquire(session, runtime)
                    daemon.enqueue_error = code

                    spawned = await session.call_tool(
                        "world_spawn", {"type": "X", "pos": [1.0, 2.0, 3.0]}
                    )

                    self.assertTrue(spawned.isError)
                    self.assertIn(code, spawned.content[0].text)
                    self.assertIsNone(runtime.active_lease_token)
                    self.assertEqual(len(changed), 2)

    async def test_a_call_that_leaves_the_catalog_as_it_was_announces_nothing(
        self,
    ) -> None:
        app, runtime, _daemon = self._app()
        async with _recording_session(app) as (session, changed):
            await self._acquire(session, runtime)
            beat = await session.call_tool(
                "session_heartbeat", {"lease_token": _ScriptedDaemon.TOKEN}
            )
            refused = await session.call_tool(
                "session_release", {"lease_token": "token-b"}
            )

            self.assertFalse(beat.isError, beat.content)
            self.assertTrue(refused.isError)
            self.assertEqual(runtime.active_lease_token, _ScriptedDaemon.TOKEN)
            self.assertEqual(len(changed), 1)

    async def test_the_full_catalog_platform_is_never_told_about_a_flip(self) -> None:
        app, runtime, _daemon = self._app(platform="claude")
        async with _recording_session(app) as (session, changed):
            full = await _listed(session)
            await self._acquire(session, runtime)
            await session.call_tool(
                "session_release", {"lease_token": _ScriptedDaemon.TOKEN}
            )

            self.assertEqual(changed, [])
            self.assertEqual(await _listed(session), full)
        self.assertIn("world_spawn", full)

    async def test_a_failed_notification_does_not_fail_the_call_and_is_retried(
        self,
    ) -> None:
        app, runtime, _daemon = self._app()
        async with _recording_session(app) as (session, changed):
            with patch.object(
                ServerSession,
                "send_tool_list_changed",
                AsyncMock(side_effect=RuntimeError("stream closed")),
            ):
                await self._acquire(session, runtime)
            self.assertEqual(changed, [])

            beat = await session.call_tool(
                "session_heartbeat", {"lease_token": _ScriptedDaemon.TOKEN}
            )

            self.assertFalse(beat.isError, beat.content)
            self.assertEqual(len(changed), 1)

    @slow_test
    async def test_real_daemon_grant_and_release_each_announce_the_flip(self) -> None:
        srv = DaemonHttpServer(_config(key="ckey"))
        srv.start()
        self.addCleanup(srv.stop)
        config = _client_config(key=srv.key, port=srv.port, client_platform="codex")
        runtime = _fixture_client_runtime(config)
        _attach_fixture_transport(runtime, srv)
        # The fixture's ownerless run protects itself from any session but its
        # launcher; without this the grant is released inside the same call.
        stamp_launcher(srv.state.lifecycle, "test-run", runtime.identity)
        with patch("dayz_mcp.server.ClientRuntime", return_value=runtime):
            app, _built = build_app(config)

        async with _recording_session(app) as (session, changed):
            acquired = await session.call_tool(
                "session_acquire_wait", {"purpose": "catalog", "max_wait_s": 5.0}
            )
            self.assertFalse(acquired.isError, acquired.content)
            token = runtime.active_lease_token
            self.assertIsNotNone(token)
            self.assertEqual(len(changed), 1)
            self.assertIn("world_spawn", await _listed(session))

            released = await session.call_tool("session_release", {"lease_token": token})

            self.assertFalse(released.isError, released.content)
            self.assertIsNone(runtime.active_lease_token)
            self.assertEqual(len(changed), 2)
            self.assertNotIn("world_spawn", await _listed(session))


class _FakeLowLevelServer:
    """The two things _install_catalog_change_notice uses on app._mcp_server."""

    def __init__(self, session: Any) -> None:
        self.handler: Any = None
        self.request_context = SimpleNamespace(session=session)

    def call_tool(self, *, validate_input: bool = True):
        def register(func):
            self.handler = func
            return func

        return register


class CatalogChangeNoticeOrderTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_flip_seen_by_a_concurrent_call_is_still_announced(self) -> None:
        # A call that starts and ends on the compact view can still be the one
        # that finds the lease lost after another call announced the grant.
        # Comparing each call's own before/after would stay silent here.
        runtime = SimpleNamespace(
            config=_client_config(client_platform="codex"), active_lease_token=None
        )
        release_verb = asyncio.Event()

        async def call_tool(name: str, _arguments: dict[str, Any]) -> list[Any]:
            if name == "grant":
                runtime.active_lease_token = "token-a"
            elif name == "verb":
                await release_verb.wait()
                runtime.active_lease_token = None
            return []

        sent = AsyncMock()
        low_level = _FakeLowLevelServer(SimpleNamespace(send_tool_list_changed=sent))
        server._install_catalog_change_notice(
            SimpleNamespace(call_tool=call_tool, _mcp_server=low_level), runtime
        )

        verb = asyncio.create_task(low_level.handler("verb", {}))
        await asyncio.sleep(0)
        await low_level.handler("grant", {})
        self.assertEqual(sent.await_count, 1)
        release_verb.set()
        await verb

        self.assertEqual(sent.await_count, 2)


class LeaseFreeReadsTest(unittest.IsolatedAsyncioTestCase):
    """b753: the reads that need no lease are listed before one is held."""

    def test_the_read_list_is_the_code_partition(self) -> None:
        derived = _lease_free_read_tools()
        self.assertIn("logs_since", derived)
        self.assertIn("ui_tree", derived)
        self.assertEqual(server._INITIAL_READ_TOOL_NAMES, frozenset(derived))
        self.assertEqual(
            server._INITIAL_READ_TOOL_NAMES & _mutating_bridge_tools(), frozenset()
        )

    async def test_every_lease_free_read_is_in_the_compact_catalog(self) -> None:
        app, runtime = _listing_app(_client_config(client_platform="codex"))
        self.assertIsNone(runtime.active_lease_token)
        registered = {tool.name for tool in app._tool_manager.list_tools()}
        reads = _lease_free_read_tools()

        listed = _names(await app.list_tools())

        self.assertEqual(reads - registered, set())
        self.assertEqual(reads - listed, set())

    async def test_no_tool_that_fronts_a_mutating_command_is_listed(self) -> None:
        app, _runtime = _listing_app(_client_config(client_platform="codex"))
        mutating = _mutating_bridge_tools()
        self.assertIn("world_spawn", mutating)

        listed = _names(await app.list_tools())

        self.assertEqual(listed & mutating, set())


class CompactDescriptionTest(unittest.IsolatedAsyncioTestCase):
    """b753: a compact description never ends inside a word."""

    def _limit(self) -> int:
        return server._INITIAL_DESCRIPTION_LIMIT

    def test_the_longest_run_of_whole_sentences_is_kept_and_marked(self) -> None:
        sentence = "Read the state of one peer. "
        description = sentence * 20

        compact = server._compact_description(description)

        self.assertLessEqual(len(compact), self._limit())
        self.assertTrue(compact.endswith(" " + _MARKER), compact)
        kept = compact[: -len(" " + _MARKER)]
        self.assertTrue(description.startswith(kept + " "), compact)
        self.assertTrue(kept.endswith("."), compact)
        self.assertGreater(len(kept) + len(sentence), self._limit() - 2, compact)

    def test_a_long_first_sentence_is_cut_at_a_word_outside_brackets(self) -> None:
        description = (
            "Inspect peer liveness, version_state, and ready {ready, reason is an "
            "OPEN set (today: " + "a|" * 200 + "z)}. After a fresh launch."
        )

        compact = server._compact_description(description)

        self.assertEqual(
            compact, f"Inspect peer liveness, version_state, and ready {_MARKER}"
        )

    def test_a_separator_left_dangling_by_the_cut_is_dropped(self) -> None:
        description = "Read the weapon: " + ", ".join(
            f"field{index}" for index in range(80)
        )

        compact = server._compact_description(description)

        kept = compact[: -len(" " + _MARKER)]
        self.assertTrue(compact.endswith(" " + _MARKER), compact)
        self.assertRegex(kept, r"field\d+$")
        self.assertEqual(description[len(kept)], ",")

    def test_without_a_word_boundary_only_the_marker_is_left(self) -> None:
        self.assertEqual(
            server._compact_description("x" * (self._limit() + 1)), _MARKER
        )

    def test_an_abbreviation_is_not_a_sentence_end(self) -> None:
        description = "Name a mod, e.g. " + "the folder of one mod " * 20

        compact = server._compact_description(description)

        self.assertNotEqual(compact, f"Name a mod, e.g. {_MARKER}")
        self.assertTrue(compact.startswith("Name a mod, e.g. the folder"), compact)

    def test_a_description_within_the_limit_is_kept_whole(self) -> None:
        description = "y " * (self._limit() // 2)
        self.assertEqual(server._compact_description(description), description)

    async def test_no_compact_description_is_cut_inside_a_word(self) -> None:
        app, _runtime = _listing_app(_client_config(client_platform="codex"))
        full = {
            tool.name: tool.description or "" for tool in app._tool_manager.list_tools()
        }

        listed = await app.list_tools()

        self.assertTrue(listed)
        for tool in listed:
            compact = tool.description or ""
            original = full[tool.name]
            with self.subTest(tool=tool.name, compact=compact):
                self.assertLessEqual(len(compact), self._limit())
                if compact == original:
                    continue
                self.assertTrue(compact.endswith(_MARKER))
                kept = compact[: -len(_MARKER)].rstrip()
                self.assertTrue(original.startswith(kept))
                if kept:
                    following = original[len(kept)]
                    self.assertTrue(
                        following.isspace() or following in _DANGLING_SEPARATORS,
                        f"cut inside a word before {following!r}",
                    )

    async def test_pipeline_feedback_keeps_the_whole_kind_list(self) -> None:
        # The cut seen live: "kind must be bug | request | find…".
        app, _runtime = _listing_app(_client_config(client_platform="codex"))

        compact = {tool.name: tool for tool in await app.list_tools()}

        description = compact["pipeline_feedback"].description or ""
        self.assertIn(
            "kind must be bug | request | finding | tool_contribution.", description
        )
        self.assertNotIn("find" + _MARKER, description)


class HiddenToolsStayCallableTest(unittest.IsolatedAsyncioTestCase):
    """b753: the lease gate is the boundary, not the listing."""

    async def test_a_hidden_read_tool_called_by_name_returns_its_result(self) -> None:
        config = _client_config(
            key="test-key", client_platform="codex", auto_spawn_daemon=False
        )
        runtime = _fixture_client_runtime(config)
        with patch("dayz_mcp.server.ClientRuntime", return_value=runtime):
            app, _built = build_app(config)
        projects = {"projects": [{"name": "ExampleMod"}]}

        with patch.object(dayz_test_tool, "list_project_names", return_value=projects):
            async with create_connected_server_and_client_session(
                app._mcp_server
            ) as session:
                listed = await _listed(session)
                result = await session.call_tool("list_projects", {})

        self.assertNotIn("list_projects", listed)
        self.assertFalse(result.isError, result.content)
        self.assertEqual(json.loads(result.content[0].text), projects)

    @slow_test
    async def test_a_hidden_mutating_tool_called_by_name_still_needs_a_lease(
        self,
    ) -> None:
        srv = DaemonHttpServer(_config(key="ckey"))
        srv.start()
        self.addCleanup(srv.stop)
        config = _client_config(key=srv.key, port=srv.port, client_platform="codex")
        runtime = _fixture_client_runtime(config)
        _attach_fixture_transport(runtime, srv)
        with patch("dayz_mcp.server.ClientRuntime", return_value=runtime):
            app, _built = build_app(config)

        async with create_connected_server_and_client_session(app._mcp_server) as session:
            listed = await _listed(session)
            result = await session.call_tool(
                "world_spawn", {"type": "X", "pos": [1.0, 2.0, 3.0], "timeout_s": 2.0}
            )

        self.assertNotIn("world_spawn", listed)
        self.assertIsNone(runtime.active_lease_token)
        self.assertTrue(result.isError)
        self.assertIn("lease_required", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
