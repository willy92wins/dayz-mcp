"""Dual-era MCP front (2026-07-28 + legacy handshake) and the skills extension wire.

Offline, in-process, over anyio memory streams: no DayZ, daemon, ports or subprocess.
Oracle frozen before the implementation (a different family than the implementer).
Shapes come from modelcontextprotocol.io/extensions/skills/overview, the ext-skills
specification (specification/beta/skills.mdx) and the 2026-07-28 spec pages for
versioning, discover, caching and stdio.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock
from typing import Any

import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from dayz_mcp import dual_era, skills_catalog
from tests._tiers import slow_test

MODERN = "2026-07-28"
LEGACY = "2025-11-25"
META = {
    "io.modelcontextprotocol/protocolVersion": MODERN,
    "io.modelcontextprotocol/clientInfo": {"name": "oracle", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
EXT = "io.modelcontextprotocol/skills"
SKILL_BODY = "# Demo\n\nRead references/guide.md\n"
GUIDE = "# Guide\n\nUse it.\n"


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class _Wire:
    """One in-process client of dual_era.serve."""

    def __init__(self, app: FastMCP, catalog: skills_catalog.SkillCatalog) -> None:
        self.app = app
        self.catalog = catalog
        self.other: list[dict[str, Any]] = []
        self._next_id = 0

    async def __aenter__(self) -> "_Wire":
        self._stack = contextlib.AsyncExitStack()
        await self._stack.__aenter__()
        # LIFO unwinding: close the client side first so serve() sees EOF, then wait for
        # the task group, and only then leave the overall deadline scope.
        self._send_stream, c2s_recv = anyio.create_memory_object_stream(64)
        s2c_send, self._recv_stream = anyio.create_memory_object_stream(64)
        self._stack.push_async_callback(self._recv_stream.aclose)
        self._stack.enter_context(anyio.fail_after(60))
        group = await self._stack.enter_async_context(anyio.create_task_group())
        self._stack.push_async_callback(self._send_stream.aclose)

        async def serve() -> None:
            await dual_era.serve(self.app, c2s_recv, s2c_send, self.catalog)

        group.start_soon(serve)
        return self

    async def __aexit__(self, *exc_info: object) -> bool | None:
        return await self._stack.__aexit__(*exc_info)

    async def _send(self, message: dict[str, Any]) -> None:
        await self._send_stream.send(SessionMessage(JSONRPCMessage.model_validate(message)))

    async def _receive(self) -> dict[str, Any]:
        item = await self._recv_stream.receive()
        return item.message.model_dump(by_alias=True, exclude_none=True)

    async def start(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        modern: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> int:
        self._next_id += 1
        request_id = self._next_id
        body = dict(params or {})
        if modern:
            body["_meta"] = {**(meta if meta is not None else META), **body.get("_meta", {})}
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": body})
        return request_id

    async def finish(self, request_id: int) -> dict[str, Any]:
        with anyio.fail_after(20):
            while True:
                for index, message in enumerate(self.other):
                    if message.get("id") == request_id and ("result" in message or "error" in message):
                        return self.other.pop(index)
                message = await self._receive()
                if message.get("id") == request_id and ("result" in message or "error" in message):
                    return message
                self.other.append(message)

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        modern: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.finish(await self.start(method, params, modern=modern, meta=meta))

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def legacy_initialize(self) -> dict[str, Any]:
        reply = await self.call(
            "initialize",
            {
                "protocolVersion": LEGACY,
                "capabilities": {},
                "clientInfo": {"name": "legacy-oracle", "version": "1"},
            },
            modern=False,
        )
        await self.notify("notifications/initialized")
        return reply


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        skill = root / "demo"
        (skill / "references").mkdir(parents=True)
        self.skill_md = (
            "---\nname: demo\ndescription: Demo skill.\nlicense: MIT\n---\n\n" + SKILL_BODY
        )
        (skill / "SKILL.md").write_bytes(self.skill_md.encode())
        (skill / "references" / "guide.md").write_bytes(GUIDE.encode())
        self.catalog = skills_catalog.SkillCatalog.load(root)
        self.app = FastMCP("oracle-server", instructions="Base instructions.")
        skills_catalog.install(self.app, self.catalog)

        @self.app.tool()
        def echo(text: str) -> str:
            """Echo the text."""
            return text

        @self.app.resource("memo://note", mime_type="text/plain")
        def note() -> str:
            return "a plain resource"

    def wire(self) -> _Wire:
        return _Wire(self.app, self.catalog)

    def assertCacheable(self, result: dict[str, Any]) -> None:
        self.assertEqual(result["resultType"], "complete")
        self.assertIsInstance(result["ttlMs"], int)
        self.assertGreaterEqual(result["ttlMs"], 0)
        self.assertIn(result["cacheScope"], ("public", "private"))


class PureHelpersTest(unittest.TestCase):
    def test_is_modern_needs_the_per_request_protocol_version(self) -> None:
        good = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": dict(META)}}
        self.assertTrue(dual_era.is_modern(good))
        for bad in (
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {}}},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": "x"}},
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": LEGACY}},
            {"jsonrpc": "2.0", "id": 1, "result": {}},
            "not a dict",
        ):
            with self.subTest(bad=bad):
                self.assertFalse(dual_era.is_modern(bad))

    def test_strip_modern_meta_keeps_other_keys_and_never_mutates(self) -> None:
        params = {
            "name": "echo",
            "_meta": {**META, "progressToken": "p1", "io.modelcontextprotocol/subscriptionId": "s"},
        }
        before = {"name": "echo", "_meta": dict(params["_meta"])}
        out = dual_era.strip_modern_meta(params)
        self.assertEqual(out, {"name": "echo", "_meta": {"progressToken": "p1"}})
        self.assertEqual(params, before)
        self.assertEqual(
            dual_era.strip_modern_meta({"_meta": dict(META)}), {}, "an empty _meta is dropped"
        )

    def test_modern_result_adds_result_type_and_cache_hints_only_where_required(self) -> None:
        for method in (
            "tools/list",
            "prompts/list",
            "resources/list",
            "resources/templates/list",
            "resources/read",
        ):
            with self.subTest(method=method):
                out = dual_era.modern_result(method, {"x": 1})
                self.assertEqual(out, {"x": 1, "resultType": "complete", "ttlMs": 0, "cacheScope": "private"})
        out = dual_era.modern_result("tools/call", {"content": []})
        self.assertEqual(out, {"content": [], "resultType": "complete"})
        kept = dual_era.modern_result(
            "resources/read", {"resultType": "complete", "ttlMs": 5, "cacheScope": "public"}
        )
        self.assertEqual(kept, {"resultType": "complete", "ttlMs": 5, "cacheScope": "public"})
        source = {"y": 2}
        dual_era.modern_result("tools/list", source)
        self.assertEqual(source, {"y": 2})

    def test_run_stdio_is_a_coroutine_taking_the_app(self) -> None:
        self.assertTrue(inspect.iscoroutinefunction(dual_era.run_stdio))
        self.assertTrue(inspect.iscoroutinefunction(dual_era.serve))
        self.assertEqual(dual_era.MODERN_VERSION, MODERN)


class ModernEraTest(_Base):
    async def test_discover_names_versions_capabilities_identity_and_instructions(self) -> None:
        async with self.wire() as wire:
            reply = await wire.call("server/discover")
        result = reply["result"]
        self.assertCacheable(result)
        self.assertIn(MODERN, result["supportedVersions"])
        self.assertIn(LEGACY, result["supportedVersions"])
        capabilities = result["capabilities"]
        self.assertIn("tools", capabilities)
        self.assertIn("resources", capabilities)
        self.assertIn(EXT, capabilities["extensions"])
        self.assertIsInstance(capabilities["extensions"][EXT], dict)
        self.assertEqual(
            result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "oracle-server"
        )
        self.assertIn("Base instructions.", result["instructions"])
        self.assertNotIn("protocolVersion", result)

    async def test_skills_list_returns_complete_entries_in_one_page(self) -> None:
        async with self.wire() as wire:
            reply = await wire.call("skills/list")
            cursor = await wire.call("skills/list", {"cursor": "bogus"})
        result = reply["result"]
        self.assertCacheable(result)
        self.assertEqual(result["skills"], self.catalog.list_entries())
        self.assertNotIn("nextCursor", result)
        self.assertEqual(cursor["error"]["code"], -32602)

    async def test_skills_get_answers_by_uri_and_rejects_everything_else(self) -> None:
        uri = "skill://demo/SKILL.md"
        async with self.wire() as wire:
            ok = await wire.call("skills/get", {"uri": uri})
            unknown = await wire.call("skills/get", {"uri": "skill://nope/SKILL.md"})
            supporting = await wire.call("skills/get", {"uri": "skill://demo/references/guide.md"})
            missing = await wire.call("skills/get", {})
            wrong_type = await wire.call("skills/get", {"uri": 7})
        self.assertCacheable(ok["result"])
        self.assertEqual(ok["result"]["skill"], self.catalog.get_entry(uri))
        self.assertNotIn("skills", ok["result"])
        for reply in (unknown, supporting, missing, wrong_type):
            self.assertEqual(reply["error"]["code"], -32602, reply)

    async def test_resources_read_serves_skill_files_that_verify_against_the_manifest(self) -> None:
        entry = self.catalog.get_entry("skill://demo/SKILL.md")
        async with self.wire() as wire:
            replies = {
                item["uri"]: await wire.call("resources/read", {"uri": item["uri"]})
                for item in entry["resources"]
            }
            unknown = await wire.call("resources/read", {"uri": "skill://demo/absent.md"})
        self.assertEqual(set(replies), {item["uri"] for item in entry["resources"]})
        for item in entry["resources"]:
            result = replies[item["uri"]]["result"]
            self.assertCacheable(result)
            self.assertEqual(len(result["contents"]), 1)
            content = result["contents"][0]
            self.assertEqual(content["uri"], item["uri"])
            self.assertEqual(content["mimeType"], "text/markdown")
            raw = content["text"].encode("utf-8")
            self.assertEqual(len(raw), item["size"])
            self.assertEqual(_sha(raw), item["digest"])
        self.assertEqual(replies["skill://demo/SKILL.md"]["result"]["contents"][0]["text"], self.skill_md)
        self.assertEqual(unknown["error"]["code"], -32602)

    async def test_non_skill_resources_and_tools_are_served_in_modern_shape(self) -> None:
        async with self.wire() as wire:
            note = await wire.call("resources/read", {"uri": "memo://note"})
            resources = await wire.call("resources/list")
            tools = await wire.call("tools/list")
            called = await wire.call("tools/call", {"name": "echo", "arguments": {"text": "hi"}})
        self.assertCacheable(note["result"])
        self.assertEqual(note["result"]["contents"][0]["text"], "a plain resource")
        self.assertCacheable(resources["result"])
        self.assertIn("memo://note", {item["uri"] for item in resources["result"]["resources"]})
        self.assertCacheable(tools["result"])
        self.assertEqual([tool["name"] for tool in tools["result"]["tools"]], ["echo"])
        self.assertEqual(called["result"]["resultType"], "complete")
        self.assertEqual(called["result"]["content"][0]["text"], "hi")
        self.assertFalse(called["result"].get("isError", False))
        self.assertEqual(wire.other, [], "no unsolicited message may reach a modern client")

    async def test_unknown_tool_error_is_not_rewritten(self) -> None:
        async with self.wire() as wire:
            reply = await wire.call("tools/call", {"name": "absent", "arguments": {}})
        body = reply.get("result") or {}
        self.assertTrue(
            "error" in reply or body.get("isError") is True,
            "the SDK's own answer for an unknown tool must survive the front",
        )

    async def test_unsupported_modern_version_is_a_recognised_modern_error(self) -> None:
        bad_meta = {**META, "io.modelcontextprotocol/protocolVersion": "1900-01-01"}
        async with self.wire() as wire:
            reply = await wire.call("tools/list", meta=bad_meta)
            discover = await wire.call("server/discover", meta=bad_meta)
        for item in (reply, discover):
            self.assertEqual(item["error"]["code"], -32022)
            data = item["error"]["data"]
            self.assertEqual(data["requested"], "1900-01-01")
            self.assertIn(MODERN, data["supported"])
            self.assertIn(LEGACY, data["supported"])

    async def test_a_legacy_version_in_modern_meta_is_not_modern(self) -> None:
        legacy_meta = {**META, "io.modelcontextprotocol/protocolVersion": LEGACY}
        async with self.wire() as wire:
            reply = await wire.call("skills/list", meta=legacy_meta)
        self.assertEqual(reply["error"]["code"], -32022)

    async def test_the_modern_era_never_leaks_into_the_backend_request(self) -> None:
        seen: list[dict[str, Any]] = []

        @self.app.tool()
        def meta_probe() -> str:
            """Report the request metadata the tool saw."""
            context = self.app.get_context()
            meta = context.request_context.meta
            seen.append(meta.model_dump() if meta is not None else {})
            return "ok"

        meta = {**META, "progressToken": "tok-1"}
        async with self.wire() as wire:
            reply = await wire.call("tools/call", {"name": "meta_probe", "arguments": {}}, meta=meta)
        self.assertEqual(reply["result"]["content"][0]["text"], "ok")
        self.assertEqual(len(seen), 1)
        self.assertFalse([key for key in seen[0] if key.startswith("io.modelcontextprotocol/")])
        self.assertEqual(seen[0].get("progressToken"), "tok-1")


class ModernTrafficTest(_Base):
    async def test_a_pending_modern_call_does_not_block_other_modern_requests(self) -> None:
        release = asyncio.Event()

        @self.app.tool()
        async def slow() -> str:
            """Wait until the test lets it go."""
            await release.wait()
            return "done"

        async with self.wire() as wire:
            pending = await wire.start("tools/call", {"name": "slow", "arguments": {}})
            skills = await wire.call("skills/list")
            tools = await wire.call("tools/list")
            self.assertEqual(skills["result"]["skills"], self.catalog.list_entries())
            self.assertIn("slow", [tool["name"] for tool in tools["result"]["tools"]])
            release.set()
            done = await wire.finish(pending)
        self.assertEqual(done["result"]["content"][0]["text"], "done")
        self.assertEqual(done["result"]["resultType"], "complete")

    async def test_unsolicited_list_changed_is_dropped_for_modern_and_kept_for_legacy(self) -> None:
        @self.app.tool()
        async def poke(ctx: Context) -> str:
            """Ask the SDK to emit tools/list_changed."""
            await ctx.session.send_tool_list_changed()
            return "poked"

        async with self.wire() as wire:
            modern = await wire.call("tools/call", {"name": "poke", "arguments": {}})
        self.assertEqual(modern["result"]["content"][0]["text"], "poked")
        self.assertEqual([m for m in wire.other if "list_changed" in str(m.get("method"))], [])

        async with self.wire() as wire:
            await wire.legacy_initialize()
            legacy = await wire.call("tools/call", {"name": "poke", "arguments": {}}, modern=False)
        self.assertEqual(legacy["result"]["content"][0]["text"], "poked")
        self.assertEqual(
            [m["method"] for m in wire.other if "list_changed" in str(m.get("method"))],
            ["notifications/tools/list_changed"],
            "legacy traffic must be forwarded untouched",
        )

    async def test_progress_notifications_reach_a_modern_client_with_its_token(self) -> None:
        @self.app.tool()
        async def step(ctx: Context) -> str:
            """Report one progress step."""
            await ctx.report_progress(1, 2)
            return "stepped"

        meta = {**META, "progressToken": "tok"}
        async with self.wire() as wire:
            reply = await wire.call("tools/call", {"name": "step", "arguments": {}}, meta=meta)
        self.assertEqual(reply["result"]["content"][0]["text"], "stepped")
        progress = [m for m in wire.other if m.get("method") == "notifications/progress"]
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0]["params"]["progressToken"], "tok")


class MalformedInputTest(_Base):
    """A bad line from the client is answered; it never ends the process or the next request."""

    BAD_IDS = ([], {}, 1.5)

    def _bad(self, request_id: Any, method: str) -> dict[str, Any]:
        # The SDK parses a request whose id is not str|int as a notification with an extra
        # `id`, which is how such a line reaches the front from stdio_server.
        return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": {"_meta": dict(META)}}

    async def _error_for_bad_line(self, wire: _Wire) -> dict[str, Any]:
        with anyio.fail_after(10):
            while True:
                for message in wire.other:
                    if "error" in message:
                        return message
                wire.other.append(await wire._receive())

    async def test_invalid_ids_get_invalid_request_and_the_next_line_is_served(self) -> None:
        for method in ("skills/list", "tools/list"):
            for bad in self.BAD_IDS:
                with self.subTest(method=method, bad_id=bad):
                    async with self.wire() as wire:
                        await wire._send(self._bad(bad, method))
                        good = await wire.call("skills/list")
                        error = await self._error_for_bad_line(wire)
                    self.assertEqual(good["result"]["skills"], self.catalog.list_entries())
                    self.assertEqual(error["error"]["code"], -32600)
                    # Never a number: a valid call with that number may still be pending.
                    self.assertIsInstance(error["id"], str)
                    self.assertTrue(error["id"].startswith("dayz-mcp:invalid-request-id:"))

    async def test_an_invalid_id_error_never_uses_the_id_of_a_pending_call(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        @self.app.tool()
        async def slow() -> str:
            """Block until released."""
            started.set()
            await release.wait()
            return "done"

        async with self.wire() as wire:
            await wire._send(
                {
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "tools/call",
                    "params": {"name": "slow", "arguments": {}, "_meta": dict(META)},
                }
            )
            with anyio.fail_after(10):
                await started.wait()
            await wire._send(self._bad([], "skills/list"))
            await wire._send(self._bad({}, "tools/list"))
            first = await self._error_for_bad_line(wire)
            self.assertEqual(first["error"]["code"], -32600)
            self.assertIsInstance(first["id"], str)
            await wire.call("skills/list")  # the second bad line was handled before this
            errors = [m for m in wire.other if "error" in m]
            self.assertEqual(len(errors), 2, wire.other)
            self.assertEqual(len({m["id"] for m in errors}), 2, "each error gets its own id")
            self.assertEqual(
                [m for m in wire.other if m.get("id") == 0 and not isinstance(m["id"], str)],
                [],
                "nothing may answer id 0 before its own result",
            )
            release.set()
            result = await wire.finish(0)
            self.assertIn("result", result)
            self.assertEqual(result["result"]["content"][0]["text"], "done")
            self.assertEqual([m for m in wire.other if m.get("id") == 0], [])

    def _blocking_tool(self) -> tuple[asyncio.Event, asyncio.Event]:
        started = asyncio.Event()
        release = asyncio.Event()

        @self.app.tool()
        async def slow() -> str:
            """Block until released."""
            started.set()
            await release.wait()
            return "done"

        return started, release

    def _answers_for(self, wire: _Wire, request_id: str) -> list[dict[str, Any]]:
        return [m for m in wire.other if m.get("id") == request_id]

    async def test_two_invalid_lines_never_repeat_an_id_and_carry_a_process_nonce(self) -> None:
        async with self.wire() as wire:
            await wire._send(self._bad([], "skills/list"))
            await wire._send(self._bad({}, "skills/list"))
            await wire.call("skills/list")
            ids = [m["id"] for m in wire.other if "error" in m]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)
        for ident in ids:
            self.assertTrue(ident.startswith(dual_era._INVALID_ID_PREFIX), ident)
        self.assertNotEqual(dual_era._INVALID_ID_PREFIX, "dayz-mcp:invalid-request-id:")
        self.assertGreaterEqual(len(dual_era._INVALID_ID_NONCE), 16)

    async def test_a_client_id_equal_to_an_old_sentinel_gets_exactly_one_answer(self) -> None:
        old = "dayz-mcp:invalid-request-id:1"
        async with self.wire() as wire:
            await wire._send(self._bad([], "skills/list"))  # the first sentinel goes out
            await wire._send(
                {"jsonrpc": "2.0", "id": old, "method": "skills/list", "params": {"_meta": dict(META)}}
            )
            answer = await wire.finish(old)
            await wire.call("skills/list")
            stray = self._answers_for(wire, old)
        self.assertIn("result", answer)
        self.assertEqual(stray, [], "the id must not be answered twice")

    async def test_a_legacy_call_pending_under_an_old_sentinel_id_is_not_answered_twice(self) -> None:
        old = "dayz-mcp:invalid-request-id:1"
        started, release = self._blocking_tool()
        async with self.wire() as wire:
            await wire.legacy_initialize()
            await wire._send(
                {
                    "jsonrpc": "2.0",
                    "id": old,
                    "method": "tools/call",
                    "params": {"name": "slow", "arguments": {}},
                }
            )
            with anyio.fail_after(10):
                await started.wait()
            await wire._send(self._bad([], "skills/list"))
            await wire.call("skills/list")
            self.assertEqual(self._answers_for(wire, old), [], "nothing may answer it yet")
            release.set()
            result = await wire.finish(old)
            await wire.call("skills/list")
            stray = self._answers_for(wire, old)
        self.assertIn("result", result)
        self.assertEqual(stray, [])

    async def test_a_request_using_an_id_inside_the_live_sentinel_prefix_is_refused_under_its_own_id(self) -> None:
        reserved = f"{dual_era._INVALID_ID_PREFIX}1"
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                async with self.wire() as wire:
                    if legacy:
                        await wire.legacy_initialize()
                    body: dict[str, Any] = {}
                    if not legacy:
                        body["_meta"] = dict(META)
                    await wire._send(
                        {"jsonrpc": "2.0", "id": reserved, "method": "skills/list", "params": body}
                    )
                    answer = await wire.finish(reserved)
                    await wire.call("skills/list")
                    stray = self._answers_for(wire, reserved)
                self.assertEqual(answer["error"]["code"], -32600)
                self.assertEqual(stray, [])

    async def test_a_legacy_initialize_refused_with_an_invalid_id_does_not_answer_as_zero(self) -> None:
        async with self.wire() as wire:
            await wire.call("tools/list")  # the modern era is now on this pipe
            await wire._send(
                {
                    "jsonrpc": "2.0",
                    "id": [],
                    "method": "initialize",
                    "params": {
                        "protocolVersion": LEGACY,
                        "capabilities": {},
                        "clientInfo": {"name": "x", "version": "1"},
                    },
                }
            )
            error = await self._error_for_bad_line(wire)
        self.assertEqual(error["error"]["code"], -32600)
        self.assertIsInstance(error["id"], str)

    async def test_a_result_that_cannot_be_serialized_is_answered_as_internal_error(self) -> None:
        # The SDK writer serializes after the front; an unserializable result used to end
        # the whole process there.
        with mock.patch.object(self.catalog, "list_entries", return_value=[{"uri": object()}]):
            async with self.wire() as wire:
                failed = await wire.call("skills/list")
                after = await wire.call("tools/list")
        self.assertEqual(failed["error"]["code"], -32603)
        self.assertIn("result", after)

    async def test_an_exception_in_a_local_skills_handler_is_answered_as_internal_error(self) -> None:
        cases = (
            ("list_entries", "skills/list", {}),
            ("get_entry", "skills/get", {"uri": "skill://demo/SKILL.md"}),
            ("read", "resources/read", {"uri": "skill://demo/SKILL.md"}),
        )
        for attribute, method, params in cases:
            with self.subTest(method=method):
                with mock.patch.object(self.catalog, attribute, side_effect=RuntimeError("boom")):
                    async with self.wire() as wire:
                        failed = await wire.call(method, params)
                        after = await wire.call("tools/list")
                self.assertEqual(failed["error"]["code"], -32603)
                self.assertIn("result", after)

    async def test_an_exception_while_bridging_is_answered_for_that_request_only(self) -> None:
        real_shape = dual_era.modern_result
        real_strip = dual_era.strip_modern_meta
        seen = {"shape": 0, "strip": 0}

        def flaky_shape(method: str, result: dict[str, Any]) -> dict[str, Any]:
            if method == "tools/list":
                seen["shape"] += 1
                if seen["shape"] == 1:
                    raise RuntimeError("boom")
            return real_shape(method, result)

        def flaky_strip(params: dict[str, Any] | None) -> dict[str, Any]:
            seen["strip"] += 1
            if seen["strip"] == 1:
                raise RuntimeError("boom")
            return real_strip(params)

        for name, replacement in (("modern_result", flaky_shape), ("strip_modern_meta", flaky_strip)):
            with self.subTest(patched=name):
                seen.update(shape=0, strip=0)
                with mock.patch.object(dual_era, name, replacement):
                    async with self.wire() as wire:
                        failed = await wire.call("tools/list")
                        after = await wire.call("tools/list")
                self.assertEqual(failed["error"]["code"], -32603)
                self.assertIn("result", after)
                self.assertEqual(after["result"]["resultType"], "complete")

    def test_one_conversation_per_pipe_is_documented(self) -> None:
        self.assertIn("one conversation per pipe", (dual_era.__doc__ or "").lower())
        changelog = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("one conversation per pipe", changelog.lower())


class ShutdownTest(_Base):
    async def test_serve_ends_on_eof_and_closes_the_client_facing_streams(self) -> None:
        c2s_send, c2s_recv = anyio.create_memory_object_stream(8)
        s2c_send, s2c_recv = anyio.create_memory_object_stream(8)
        with anyio.fail_after(30):
            async with anyio.create_task_group() as group:

                async def run() -> None:
                    await dual_era.serve(self.app, c2s_recv, s2c_send, self.catalog)

                group.start_soon(run)
                await c2s_send.send(
                    SessionMessage(
                        JSONRPCMessage.model_validate(
                            {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "skills/list",
                                "params": {"_meta": dict(META)},
                            }
                        )
                    )
                )
                first = await s2c_recv.receive()
                self.assertEqual(first.message.model_dump(exclude_none=True)["id"], 1)
                await c2s_send.aclose()
            # serve returned: stdio_server's writer only ends when the write stream is closed,
            # so a front that leaves it open keeps the process alive after stdin EOF.
            with self.assertRaises(anyio.EndOfStream):
                await s2c_recv.receive()
        await s2c_recv.aclose()


class SubprocessStdioTest(unittest.TestCase):
    TOOLS_DIR = Path(__file__).resolve().parents[1]

    @slow_test
    def test_the_real_stdio_loop_answers_and_exits_when_stdin_closes(self) -> None:
        script = textwrap.dedent(
            """
            import sys
            import anyio
            from mcp.server.fastmcp import FastMCP
            from dayz_mcp import dual_era, skills_catalog

            catalog = skills_catalog.SkillCatalog.load()
            app = FastMCP("sub", instructions="x")
            skills_catalog.install(app, catalog)
            anyio.run(dual_era.run_stdio, app, catalog)
            """
        )
        meta = json.dumps({"_meta": dict(META)})
        lines = [
            '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":%s}' % meta,
            '{"jsonrpc":"2.0","id":2,"method":"skills/list","params":%s}' % meta,
        ]
        env = dict(os.environ, PYTHONPATH=str(self.TOOLS_DIR), PYTHONDONTWRITEBYTECODE="1")
        done = subprocess.run(
            [sys.executable, "-B", "-c", script],
            input=("\n".join(lines) + "\n").encode(),
            capture_output=True,
            timeout=60,
            cwd=self.TOOLS_DIR,
            env=env,
        )
        self.assertEqual(done.returncode, 0, done.stderr.decode(errors="replace")[-2000:])
        out = [json.loads(line) for line in done.stdout.decode().splitlines() if line.strip()]
        self.assertEqual(sorted(item["id"] for item in out), [1, 2])
        by_id = {item["id"]: item for item in out}
        self.assertIn(
            "io.modelcontextprotocol/skills", by_id[1]["result"]["capabilities"]["extensions"]
        )
        self.assertTrue(by_id[2]["result"]["skills"])
        self.assertNotIn(b"Traceback", done.stderr)

    @slow_test
    def test_malformed_ids_do_not_end_the_real_stdio_process(self) -> None:
        script = textwrap.dedent(
            """
            import anyio
            from mcp.server.fastmcp import FastMCP
            from dayz_mcp import dual_era, skills_catalog

            catalog = skills_catalog.SkillCatalog.load()
            app = FastMCP("sub", instructions="x")
            skills_catalog.install(app, catalog)
            anyio.run(dual_era.run_stdio, app, catalog)
            """
        )
        meta = json.dumps({"_meta": dict(META)})
        lines = [
            '{"jsonrpc":"2.0","id":[],"method":"skills/list","params":%s}' % meta,
            '{"jsonrpc":"2.0","id":{},"method":"tools/list","params":%s}' % meta,
            '{"jsonrpc":"2.0","id":2,"method":"skills/list","params":%s}' % meta,
        ]
        env = dict(os.environ, PYTHONPATH=str(self.TOOLS_DIR), PYTHONDONTWRITEBYTECODE="1")
        done = subprocess.run(
            [sys.executable, "-B", "-c", script],
            input=("\n".join(lines) + "\n").encode(),
            capture_output=True,
            timeout=60,
            cwd=self.TOOLS_DIR,
            env=env,
        )
        self.assertEqual(done.returncode, 0, done.stderr.decode(errors="replace")[-2000:])
        out = [json.loads(line) for line in done.stdout.decode().splitlines() if line.strip()]
        errors = [item for item in out if "error" in item]
        self.assertEqual(sorted(item["error"]["code"] for item in errors), [-32600, -32600])
        self.assertTrue(all(isinstance(item["id"], str) for item in errors), errors)
        good = [item for item in out if item.get("id") == 2]
        self.assertEqual(len(good), 1)
        self.assertTrue(good[0]["result"]["skills"])
        self.assertNotIn(b"Traceback", done.stderr)


    @slow_test
    def test_an_unserializable_result_does_not_end_the_real_stdio_process(self) -> None:
        script = textwrap.dedent(
            """
            import anyio
            from mcp.server.fastmcp import FastMCP
            from dayz_mcp import dual_era, skills_catalog

            catalog = skills_catalog.SkillCatalog.load()
            app = FastMCP("sub", instructions="x")
            skills_catalog.install(app, catalog)
            catalog.list_entries = lambda: [{"uri": object()}]
            anyio.run(dual_era.run_stdio, app, catalog)
            """
        )
        meta = json.dumps({"_meta": dict(META)})
        lines = [
            '{"jsonrpc":"2.0","id":1,"method":"skills/list","params":%s}' % meta,
            '{"jsonrpc":"2.0","id":2,"method":"server/discover","params":%s}' % meta,
        ]
        env = dict(os.environ, PYTHONPATH=str(self.TOOLS_DIR), PYTHONDONTWRITEBYTECODE="1")
        done = subprocess.run(
            [sys.executable, "-B", "-c", script],
            input=("\n".join(lines) + "\n").encode(),
            capture_output=True,
            timeout=60,
            cwd=self.TOOLS_DIR,
            env=env,
        )
        self.assertEqual(done.returncode, 0, done.stderr.decode(errors="replace")[-2000:])
        out = {item["id"]: item for item in map(json.loads, done.stdout.decode().splitlines()) if "id" in item}
        self.assertEqual(out[1]["error"]["code"], -32603)
        self.assertIn("result", out[2])


class LegacyEraTest(_Base):
    async def test_legacy_handshake_is_untouched_and_resources_are_the_fallback(self) -> None:
        async with self.wire() as wire:
            init = await wire.legacy_initialize()
            tools = await wire.call("tools/list", modern=False)
            resources = await wire.call("resources/list", modern=False)
            read = await wire.call("resources/read", {"uri": "skill://demo/SKILL.md"}, modern=False)
        result = init["result"]
        self.assertEqual(result["protocolVersion"], LEGACY)
        self.assertEqual(result["serverInfo"]["name"], "oracle-server")
        self.assertEqual(result["instructions"], "Base instructions.")
        self.assertNotIn("resultType", result)
        self.assertNotIn("resultType", tools["result"], "legacy results are not rewritten")
        self.assertEqual([tool["name"] for tool in tools["result"]["tools"]], ["echo"])
        listed = {item["uri"] for item in resources["result"]["resources"]}
        self.assertTrue(set(self.catalog.uris()) <= listed)
        self.assertEqual(read["result"]["contents"][0]["text"], self.skill_md)
        self.assertNotIn("resultType", read["result"])


class DualEraTest(_Base):
    async def test_modern_after_legacy_works_on_the_same_process(self) -> None:
        async with self.wire() as wire:
            await wire.legacy_initialize()
            reply = await wire.call("tools/list")
            skills = await wire.call("skills/list")
        self.assertCacheable(reply["result"])
        self.assertEqual(skills["result"]["skills"], self.catalog.list_entries())

    async def test_legacy_initialize_after_modern_is_refused_not_corrupting(self) -> None:
        async with self.wire() as wire:
            first = await wire.call("tools/list")
            refused = await wire.legacy_initialize()
            again = await wire.call("tools/list")
        self.assertCacheable(first["result"])
        self.assertIn("error", refused)
        self.assertCacheable(again["result"])

    async def test_a_modern_skills_request_does_not_need_the_backend(self) -> None:
        # Skills requests are answered by the front itself: they must work even if the
        # wrapped server has no tools or resources at all.
        self.app = FastMCP("bare")
        async with self.wire() as wire:
            listed = await wire.call("skills/list")
            discover = await wire.call("server/discover")
        self.assertEqual(listed["result"]["skills"], self.catalog.list_entries())
        self.assertIn(EXT, discover["result"]["capabilities"]["extensions"])
        self.assertIn("resources", discover["result"]["capabilities"])


if __name__ == "__main__":
    unittest.main()
