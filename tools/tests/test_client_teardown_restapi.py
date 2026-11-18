"""Client teardown REST contract: no reset() with a request in flight, and
the callbacks of requests that can outlive the mission live in 4_World.

Source contracts only, in the style of test_poll_watchdog_contract.py: they
pin the Enforce text the engine will run, not a translation of it.
"""
from __future__ import annotations

import re
import unittest

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import clean

CLIENT_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
WORLD_PATH = addon_root() / "scripts" / "4_World" / "MCP_ClientRestCallbacks.c"


def _body(source: str, signature: str) -> str:
    text = clean(source)
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1 : index]
    raise AssertionError(f"unterminated block: {signature}")


def _class(source: str, name: str) -> str:
    return _body(source, f"class {name}")


class ClientTeardownRestApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = CLIENT_PATH.read_text(encoding="utf-8")
        cls.world = WORLD_PATH.read_text(encoding="utf-8")
        cls.client_clean = clean(cls.client)
        cls.world_clean = clean(cls.world)

    def test_no_reset_is_reachable_in_client_teardown_or_anywhere(self) -> None:
        # Cleaned source: comments may name reset(), code may not call it.
        self.assertNotIn(".reset(", self.client_clean)
        for signature in ("void Shutdown()", "protected void AbandonInFlightPoll()"):
            self.assertNotIn(".reset(", _body(self.client, signature), signature)
        shutdown = _body(self.client, "void Shutdown()")
        self.assertNotIn("postedTerminal", _body(shutdown, "if (m_Ctx)"))
        self.assertIn("m_Ctx = null;", shutdown)
        self.assertIn("m_PollCtx = null;", shutdown)

    def test_poll_and_result_callbacks_are_world_classes(self) -> None:
        for name in ("MCPClientPollCallback", "MCPClientResultCallback"):
            self.assertIn(f"class {name} : RestCallback", self.world_clean, name)
            self.assertNotIn(f"class {name}", self.client_clean, name)
            cls = _class(self.world, name)
            self.assertIn("protected ref MCPClientRestSink m_Sink;", cls, name)
        self.assertIn("class MCPClientRestSink", self.world_clean)
        self.assertIn("class MCPClientRestHolder", self.world_clean)
        # 4_World names no 5_Mission type, in code or in a comment.
        self.assertNotIn("MCPClientBridge", self.world)
        self.assertNotIn("MCPClientRestForwarder", self.world)

    def test_holder_is_static_public_and_drop_is_idempotent(self) -> None:
        holder = _class(self.world, "MCPClientRestHolder")
        self.assertIn("protected static ref array<ref RestCallback> s_Callbacks;", holder)
        for signature in ("static void Hold(RestCallback cb)", "static void Drop(RestCallback cb)", "static int Count()"):
            self.assertIn(signature, holder, signature)
            # Cross-module calls must not be protected (ES-PROTECTED-CROSS-MODULE).
            self.assertNotIn("protected " + signature, holder, signature)
        hold = _body(holder, "static void Hold(RestCallback cb)")
        self.assertIn("if (s_Callbacks.Get(i) == cb)", hold)
        self.assertLess(hold.index("if (s_Callbacks.Get(i) == cb)"), hold.index("s_Callbacks.Insert(cb);"))
        drop = _body(holder, "static void Drop(RestCallback cb)")
        self.assertIn("if (!s_Callbacks || !cb)", drop)
        self.assertIn("s_Callbacks.Remove(i);", drop)
        # A missing entry is a silent no-op, so a repeated OnError is safe.
        self.assertNotIn("Insert", drop)

    def test_holder_adds_at_get_and_post_time(self) -> None:
        hold = _body(self.client, "protected void HoldPollCallback()")
        self.assertIn("MCPClientRestHolder.Hold(m_PollCallback);", hold)
        start = _body(self.client, "protected void StartPoll()")
        self.assertLess(start.index("HoldPollCallback();"), start.index("m_PollCtx.GET(m_PollCallback, request);"))
        post = _body(self.client, "protected void PostResult(MCPResult result)")
        self.assertLess(post.index("m_CallbackRefs.Insert(cb);"), post.index("MCPClientRestHolder.Hold(cb);"))
        self.assertLess(post.index("MCPClientRestHolder.Hold(cb);"), post.index("m_Ctx.POST(cb, resultRequest, body);"))

    def test_holder_drops_on_each_terminal_event_before_any_delivery(self) -> None:
        for name, events in (
            ("MCPClientPollCallback", ("OnSuccess", "OnError", "OnTimeout")),
            ("MCPClientResultCallback", ("OnSuccess", "OnError", "OnTimeout")),
        ):
            cls = _class(self.world, name)
            for event in events:
                with self.subTest(name=name, event=event):
                    body = _body(cls, f"override void {event}(")
                    self.assertIn("MCPClientRestHolder.Drop(this);", body)
                    self.assertLess(
                        body.index("MCPClientRestHolder.Drop(this);"),
                        body.index("if (!m_Sink || !m_Sink.IsConnected())"),
                    )
                    self.assertLess(
                        body.index("if (!m_Sink || !m_Sink.IsConnected())"),
                        body.index("m_Sink.SinkReleaseCallback(this);"),
                    )

    def test_disconnected_sink_delivers_nothing(self) -> None:
        for name in ("MCPClientPollCallback", "MCPClientResultCallback"):
            cls = _class(self.world, name)
            for event in ("OnSuccess", "OnError", "OnTimeout"):
                with self.subTest(name=name, event=event):
                    body = _body(cls, f"override void {event}(")
                    guard = _body(body, "if (!m_Sink || !m_Sink.IsConnected())")
                    # Only the holder drop above the guard may run.
                    self.assertEqual(guard.strip(), "return;")
        forwarder = _class(self.client, "MCPClientRestForwarder")
        self.assertIn("class MCPClientRestForwarder : MCPClientRestSink", self.client_clean)
        self.assertEqual(_body(forwarder, "void Disconnect()").strip(), "m_Owner = null;")
        self.assertIn("return m_Owner != null;", _body(forwarder, "override bool IsConnected()"))
        shutdown = _body(self.client, "void Shutdown()")
        self.assertIn("m_RestSink.Disconnect();", shutdown)
        # Terminal POSTs leave before the disconnect, never after it.
        self.assertLess(shutdown.index("PostActionHoldJob(holdJob);"), shutdown.index("m_RestSink.Disconnect();"))
        self.assertLess(shutdown.index("m_RestSink.Disconnect();"), shutdown.index("m_Ctx = null;"))

    def test_old_callback_cannot_reach_the_next_bridge(self) -> None:
        # Two independent guards: the disconnected old sink, and the poll
        # identity check against the live bridge's current callback.
        poll = _class(self.world, "MCPClientPollCallback")
        for event in ("OnSuccess", "OnError", "OnTimeout"):
            body = _body(poll, f"override void {event}(")
            self.assertIn("if (!m_Sink.IsPollActive(this))", body, event)
        forwarder = _class(self.client, "MCPClientRestForwarder")
        active = _body(forwarder, "override bool IsPollActive(MCPClientPollCallback cb)")
        self.assertIn("return m_Owner.IsActivePollCallback(cb);", active)
        bridge_active = _body(self.client, "bool IsActivePollCallback(MCPClientPollCallback cb)")
        self.assertIn("return cb == m_PollCallback;", bridge_active)
        # A new bridge builds a new forwarder; the old one stays disconnected.
        ctor = _body(self.client, "void MCPClientBridge()")
        self.assertIn("m_RestSink = new MCPClientRestForwarder(this);", ctor)
        ensure = _body(self.client, "protected void EnsurePollCallback()")
        self.assertIn("new MCPClientPollCallback(m_RestSink);", ensure)
        acquire = _body(self.client, "protected MCPClientResultCallback AcquireResultCallback()")
        self.assertIn("cb.AttachSink(m_RestSink);", acquire)
        self.assertIn("new MCPClientResultCallback(m_RestSink);", acquire)

    def test_shutdown_log_lines_and_reentry_guard_stay(self) -> None:
        shutdown = _body(self.client, "void Shutdown()")
        self.assertIn('Log("shutdown first entry");', shutdown)
        reentry = _body(shutdown, "if (m_Shutdown)")
        self.assertIn('Log("shutdown re-entered");', reentry)
        self.assertLess(shutdown.index("m_Shutdown = true;"), shutdown.index('Log("shutdown first entry");'))


if __name__ == "__main__":
    unittest.main()
