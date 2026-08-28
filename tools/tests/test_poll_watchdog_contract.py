from __future__ import annotations

import unittest

from tests._addon_paths import addon_root


CLIENT = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
SERVER = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"


def _brace_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated block: {signature}")


class PollWatchdogContractTest(unittest.TestCase):
    def test_client_on_tick_watchdog_uses_seconds_and_onpollfail(self) -> None:
        source = CLIENT.read_text(encoding="utf-8")
        self.assertIn("protected const float POLL_WATCHDOG_S = 30.0;", source)
        self.assertIn("protected float m_PollInFlightS;", source)
        self.assertNotIn("protected int m_PollGeneration;", source)
        self.assertNotIn("protected int m_PollStaleGeneration;", source)

        on_tick = _brace_body(source, "void OnTick(float timeslice)")
        in_flight = _brace_body(on_tick, "if (m_PollInFlight)")
        self.assertIn("m_PollInFlightS = m_PollInFlightS + timeslice;", in_flight)
        self.assertIn("POLL_WATCHDOG_S", in_flight)
        self.assertNotIn("new ", in_flight)
        self.assertNotIn("m_TickPollSent +", in_flight)
        self.assertLess(
            in_flight.index("AbandonInFlightPoll()"),
            in_flight.index('OnPollFail("watchdog")'),
        )

        start_poll = _brace_body(source, "protected void StartPoll()")
        self.assertIn("m_PollInFlightS = 0.0;", start_poll)
        self.assertNotIn("new MCPClientPollCallback", start_poll)
        self.assertIn("EnsurePollCallback()", start_poll)
        self.assertIn("HoldPollCallback()", start_poll)
        self.assertIn("m_PollCtx.GET(m_PollCallback, request)", start_poll)
        self.assertNotIn("m_CallbackRefs.Insert", start_poll)
        self.assertIn("poll_refs=", in_flight)
        self.assertIn("m_PollCallbackRefs.Count()", in_flight)

        ensure = _brace_body(source, "protected void EnsurePollCallback()")
        self.assertIn("if (!m_PollCallback)", ensure)
        self.assertLess(
            ensure.index("if (!m_PollCallback)"),
            ensure.index("new MCPClientPollCallback"),
        )
        before_guard = ensure[: ensure.index("if (!m_PollCallback)")]
        self.assertNotIn("new MCPClientPollCallback", before_guard)
        self.assertNotIn("SetGeneration", ensure)

        hold = _brace_body(source, "protected void HoldPollCallback()")
        self.assertIn("m_PollCallbackRefs.Insert(m_PollCallback)", hold)

        on_fail = _brace_body(source, "protected void OnPollFail(string reason)")
        self.assertIn("m_PollInFlight = false;", on_fail)
        self.assertIn("m_Backoff", on_fail)

    def test_stale_poll_callback_does_not_dispatch(self) -> None:
        source = CLIENT.read_text(encoding="utf-8")
        callback = _brace_body(source, "class MCPClientPollCallback")
        self.assertNotIn("m_Generation", callback)
        self.assertNotIn("void SetGeneration", callback)
        self.assertNotIn("ConsumeStalePollCompletion", callback)
        ctor = _brace_body(callback, "void MCPClientPollCallback(")
        self.assertIn("m_Bridge = bridge;", ctor)
        self.assertNotIn("generation", ctor)
        polarity = "if (!m_Bridge.IsActivePollCallback(this))"
        inverted = "if (m_Bridge.IsActivePollCallback(this))"
        for event_sig, dispatch in (
            ("override void OnSuccess", "OnPollSuccess("),
            ("override void OnError", "OnPollError("),
            ("override void OnTimeout", "OnPollTimeout("),
        ):
            body = _brace_body(callback, event_sig)
            self.assertIn(polarity, body)
            self.assertNotIn(inverted, body)
            self.assertLess(body.index("ReleaseCallback(this)"), body.index(polarity))
            self.assertLess(body.index(polarity), body.index("return;"))
            self.assertLess(body.index("return;"), body.index(dispatch))

    def test_callback_identity_contract(self) -> None:
        source = CLIENT.read_text(encoding="utf-8")
        self.assertNotIn("void SetGeneration", source)
        self.assertNotIn("m_PollStaleCompletions", source)
        self.assertNotIn("protected int m_PollStaleGeneration;", source)
        self.assertNotIn("bool ConsumeStalePollCompletion()", source)
        self.assertNotIn("bool IsCurrentPollGeneration(int generation)", source)
        self.assertNotIn("protected int m_PollGeneration;", source)
        self.assertIn(
            "bool IsActivePollCallback(MCPClientPollCallback cb)",
            source,
        )

        active = _brace_body(source, "bool IsActivePollCallback(MCPClientPollCallback cb)")
        self.assertIn("cb == m_PollCallback", active)
        self.assertNotIn("m_PollInFlight = false", active)
        self.assertNotIn("OnPollSuccess", active)

        abandon = _brace_body(source, "protected void AbandonInFlightPoll()")
        self.assertIn("m_PollCallback = null;", abandon)
        self.assertNotIn("m_PollCallbackRefs.Clear()", abandon)
        self.assertNotIn("m_PollStaleCompletions = m_PollStaleCompletions + 1;", abandon)
        self.assertNotIn("new ", abandon)

        ensure = _brace_body(source, "protected void EnsurePollCallback()")
        self.assertIn("if (!m_PollCallback)", ensure)
        new_idx = ensure.index("new MCPClientPollCallback")
        guard_idx = ensure.index("if (!m_PollCallback)")
        self.assertLess(guard_idx, new_idx)
        self.assertNotIn("new MCPClientPollCallback", ensure[:guard_idx])
        # Round-2 leak: unconditional new with no null guard.
        self.assertNotRegex(
            ensure.strip(),
            r"^[\s\S]*m_PollCallback = new MCPClientPollCallback[\s\S]*if \(!m_PollCallback\)",
        )

        start_poll = _brace_body(source, "protected void StartPoll()")
        self.assertNotIn("new MCPClientPollCallback", start_poll)
        self.assertIn("EnsurePollCallback()", start_poll)
        self.assertLess(
            start_poll.index("EnsurePollCallback()"),
            start_poll.index("m_PollCtx.GET(m_PollCallback, request)"),
        )

        callback = _brace_body(source, "class MCPClientPollCallback")
        ctor = _brace_body(callback, "void MCPClientPollCallback(")
        self.assertIn("m_Bridge = bridge;", ctor)
        self.assertNotIn("SetGeneration", callback)
        on_success = _brace_body(callback, "override void OnSuccess")
        self.assertIn("if (!m_Bridge.IsActivePollCallback(this))", on_success)
        self.assertLess(
            on_success.index("if (!m_Bridge.IsActivePollCallback(this))"),
            on_success.index("return;"),
        )
        self.assertLess(
            on_success.index("return;"),
            on_success.index("OnPollSuccess("),
        )

        on_tick = _brace_body(source, "void OnTick(float timeslice)")
        in_flight = _brace_body(on_tick, "if (m_PollInFlight)")
        self.assertLess(
            in_flight.index("AbandonInFlightPoll()"),
            in_flight.index('OnPollFail("watchdog")'),
        )

    def test_abandon_callback_identity_state_machine(self) -> None:
        """Semantic A/B/C/D/E: callback identity vs inverted polarity vs token vs r3."""

        class IdentityBridge:
            def __init__(self) -> None:
                self._next_request = 0
                self.active_callback: object | None = None
                self.request_callback: dict[int, object] = {}
                self.held: set[object] = set()
                self.in_flight = False
                self.dispatched: list[int] = []

            def start_poll(self) -> int:
                request_id = self._next_request
                self._next_request += 1
                if self.active_callback is None:
                    self.active_callback = object()
                self.held.add(self.active_callback)
                self.request_callback[request_id] = self.active_callback
                self.in_flight = True
                return request_id

            def abandon(self) -> None:
                self.active_callback = None

            def watchdog(self) -> None:
                self.abandon()
                self.in_flight = False

            def completion(self, request_id: int) -> bool:
                cb = self.request_callback[request_id]
                self.held.discard(cb)
                if cb is not self.active_callback:
                    return False
                self.in_flight = False
                self.dispatched.append(request_id)
                return True

        class InvertedIdentityBridge(IdentityBridge):
            def completion(self, request_id: int) -> bool:
                cb = self.request_callback[request_id]
                self.held.discard(cb)
                if cb is self.active_callback:
                    return False
                self.in_flight = False
                self.dispatched.append(request_id)
                return True

        class GenerationTokenBridge:
            def __init__(self) -> None:
                self._next_request = 0
                self.generation = 0
                self.stale_generation = 0
                self.in_flight = False
                self.dispatched: list[int] = []

            def start_poll(self) -> int:
                request_id = self._next_request
                self._next_request += 1
                self.generation += 1
                self.in_flight = True
                return request_id

            def abandon(self) -> None:
                self.stale_generation = self.generation

            def watchdog(self) -> None:
                self.abandon()
                self.in_flight = False

            def consume_stale(self) -> bool:
                if self.stale_generation != 0 and self.stale_generation == self.generation:
                    self.stale_generation = 0
                    return True
                return False

            def completion(self, request_id: int) -> bool:
                if self.consume_stale():
                    return False
                self.in_flight = False
                self.dispatched.append(request_id)
                return True

        class Round3CounterBridge:
            def __init__(self) -> None:
                self._next_request = 0
                self.generation = 0
                self.stale_completions = 0
                self.in_flight = False
                self.dispatched: list[int] = []

            def start_poll(self) -> int:
                request_id = self._next_request
                self._next_request += 1
                self.generation += 1
                self.in_flight = True
                return request_id

            def abandon(self) -> None:
                self.stale_completions = self.stale_completions + 1

            def watchdog(self) -> None:
                self.abandon()
                self.in_flight = False

            def consume_stale(self) -> bool:
                if self.stale_completions > 0:
                    self.stale_completions = self.stale_completions - 1
                    return True
                return False

            def completion(self, request_id: int) -> bool:
                if self.consume_stale():
                    return False
                self.in_flight = False
                self.dispatched.append(request_id)
                return True

        # (A) short window: StartPoll -> watchdog -> completion of that request, no new StartPoll
        a = IdentityBridge()
        hung_a = a.start_poll()
        a.watchdog()
        dropped = a.completion(hung_a)
        self.assertFalse(dropped)
        self.assertEqual(a.dispatched, [])
        self.assertFalse(a.in_flight)

        # (B) hung GET cannot eat the next live poll, 5 times (complete the live request)
        b = IdentityBridge()
        live_ids: list[int] = []
        for _ in range(5):
            hung = b.start_poll()
            hung_cb = b.request_callback[hung]
            b.watchdog()
            live = b.start_poll()
            self.assertIsNot(b.request_callback[live], hung_cb)
            allowed = b.completion(live)
            self.assertTrue(allowed)
            live_ids.append(live)
        self.assertEqual(b.dispatched, live_ids)
        self.assertEqual(len(b.dispatched), 5)

        # (C) r3 increment-on-abandon / decrement-on-any-completion eats live (B)
        r3 = Round3CounterBridge()
        r3_live: list[int] = []
        for _ in range(5):
            r3.start_poll()
            r3.watchdog()
            live = r3.start_poll()
            allowed = r3.completion(live)
            if allowed:
                r3_live.append(live)
        self.assertEqual(r3_live, [])
        self.assertNotEqual(r3_live, live_ids)
        self.assertEqual(len(b.dispatched), 5)
        self.assertEqual(len(r3.dispatched), 0)

        # (D) late stale after next StartPoll: identity discards the abandoned request;
        # generation token compares N+1 != N+2 and dispatches it (then also the live one).
        ident_d = IdentityBridge()
        hung_d = ident_d.start_poll()
        ident_d.watchdog()
        live_d = ident_d.start_poll()
        self.assertFalse(ident_d.completion(hung_d))
        self.assertTrue(ident_d.completion(live_d))
        self.assertEqual(ident_d.dispatched, [live_d])

        token_d = GenerationTokenBridge()
        hung_token = token_d.start_poll()
        token_d.watchdog()
        live_token = token_d.start_poll()
        self.assertTrue(token_d.completion(hung_token))
        self.assertTrue(token_d.completion(live_token))
        self.assertEqual(token_d.dispatched, [hung_token, live_token])
        self.assertNotEqual(token_d.dispatched, ident_d.dispatched)

        inv_d = InvertedIdentityBridge()
        hung_inv = inv_d.start_poll()
        inv_d.watchdog()
        live_inv = inv_d.start_poll()
        self.assertTrue(inv_d.completion(hung_inv))
        self.assertFalse(inv_d.completion(live_inv))
        self.assertEqual(inv_d.dispatched, [hung_inv])
        self.assertNotEqual(inv_d.dispatched, ident_d.dispatched)

        # (E) orphan that never completes does not block later cycles; two consecutive
        # watchdogs discard both late completions.
        e = IdentityBridge()
        orphan = e.start_poll()
        orphan_cb = e.request_callback[orphan]
        e.watchdog()
        later_ids: list[int] = []
        for _ in range(3):
            live = e.start_poll()
            self.assertTrue(e.completion(live))
            later_ids.append(live)
        self.assertEqual(e.dispatched, later_ids)
        self.assertNotIn(orphan, e.dispatched)
        self.assertIn(orphan_cb, e.held)

        e2 = IdentityBridge()
        first = e2.start_poll()
        e2.watchdog()
        second = e2.start_poll()
        e2.watchdog()
        third = e2.start_poll()
        self.assertFalse(e2.completion(first))
        self.assertFalse(e2.completion(second))
        self.assertTrue(e2.completion(third))
        self.assertEqual(e2.dispatched, [third])

    def test_poll_context_url_returns_url_intact(self) -> None:
        source = CLIENT.read_text(encoding="utf-8")
        body = _brace_body(source, "protected string PollContextUrl(string url)")
        self.assertIn("return url;", body)
        self.assertNotIn("Substring", body)
        self.assertNotIn('url + "/"', body)
        self.assertNotIn("url.Substring", body)

    def test_watchdog_resets_only_poll_context_and_preserves_result_refs(self) -> None:
        source = CLIENT.read_text(encoding="utf-8")
        abandon = _brace_body(source, "protected void AbandonInFlightPoll()")
        self.assertIn("m_PollCtx.reset()", abandon)
        self.assertIn("m_PollCallback = null;", abandon)
        self.assertNotIn("m_PollCallbackRefs.Clear()", abandon)
        self.assertNotIn("m_Ctx.reset()", abandon)
        self.assertNotIn("m_CallbackRefs.Clear()", abandon)
        self.assertNotIn("new ", abandon)
        self.assertLess(
            abandon.index("m_PollCallback = null;"), abandon.index("m_PollCtx.reset()")
        )
        self.assertIn("m_PollCtx != m_Ctx", abandon)
        post = _brace_body(source, "protected void PostResult(")
        self.assertIn("m_CallbackRefs.Insert(cb)", post)
        self.assertIn("m_Ctx.POST(cb, resultRequest, body)", post)
        self.assertNotIn("m_PollCtx.POST", post)

    def test_shutdown_shared_ctx_terminal_detaches_callbacks(self) -> None:
        source = CLIENT.read_text(encoding="utf-8")
        poll_cb = _brace_body(source, "class MCPClientPollCallback")
        result_cb = _brace_body(source, "class MCPClientResultCallback")
        poll_detach = _brace_body(poll_cb, "void DetachBridge()")
        result_detach = _brace_body(result_cb, "void DetachBridge()")
        self.assertIn("m_Bridge = null;", poll_detach)
        self.assertIn("m_Bridge = null;", result_detach)

        shutdown = _brace_body(source, "void Shutdown()")
        self.assertIn("bool pollDistinct = false;", shutdown)
        self.assertIn("if (m_PollCtx && m_PollCtx != m_Ctx)", shutdown)
        self.assertIn("if (pollDistinct)", shutdown)
        self.assertIn("m_PollCtx.reset()", shutdown)
        self.assertIn("if (pollDistinct || !postedTerminal)", shutdown)
        self.assertIn("m_PollCallbackRefs.Clear()", shutdown)
        self.assertIn("m_PollCallback = null;", shutdown)
        self.assertGreater(
            shutdown.index("m_PollCallback = null;"),
            shutdown.index("m_PollCtx = null;"),
        )
        self.assertGreater(
            shutdown.index("m_PollCallback = null;"),
            shutdown.index("m_Ctx = null;"),
        )
        ctx_reset = _brace_body(shutdown, "if (m_Ctx)")
        self.assertIn("if (!postedTerminal)", ctx_reset)
        self.assertIn("m_Ctx.reset()", ctx_reset)
        # Shared context + postedTerminal: reset and Clear stay gated, arrays remain.
        after_shared_null = shutdown[shutdown.index("m_Ctx = null;") :]
        result_refs = after_shared_null[after_shared_null.index("if (m_CallbackRefs)") :]
        self.assertIn("if (!postedTerminal)", result_refs)
        self.assertIn("m_CallbackRefs.Clear()", result_refs)
        self.assertLess(
            result_refs.index("if (!postedTerminal)"),
            result_refs.index("m_CallbackRefs.Clear()"),
        )
        poll_reset_idx = shutdown.index("if (pollDistinct)")
        posted_result_idx = shutdown.index("if (m_Ctx)")
        self.assertLess(poll_reset_idx, posted_result_idx)
        poll_block = shutdown[poll_reset_idx:posted_result_idx]
        self.assertNotIn("postedTerminal", poll_block)

        posted_idx = shutdown.index("postedTerminal = true;")
        detach_poll_idx = shutdown.index("pollCb.DetachBridge()")
        detach_result_idx = shutdown.index("resultCb.DetachBridge()")
        ctx_null_idx = shutdown.index("m_PollCtx = null;")
        shared_null_idx = shutdown.index("m_Ctx = null;")
        self.assertLess(posted_idx, detach_poll_idx)
        self.assertLess(posted_idx, detach_result_idx)
        self.assertLess(detach_poll_idx, ctx_null_idx)
        self.assertLess(detach_result_idx, ctx_null_idx)
        self.assertLess(detach_poll_idx, shared_null_idx)
        self.assertLess(detach_result_idx, shared_null_idx)
        self.assertIn("MCPClientPollCallback.Cast", shutdown)
        self.assertIn("MCPClientResultCallback.Cast", shutdown)
        self.assertIn("m_PollCallbackRefs.Get", shutdown)
        self.assertIn("m_CallbackRefs.Get", shutdown)
        abandon = _brace_body(source, "protected void AbandonInFlightPoll()")
        self.assertNotIn("DetachBridge", abandon)

    def test_server_bridge_has_no_poll_watchdog(self) -> None:
        source = SERVER.read_text(encoding="utf-8")
        self.assertNotIn("POLL_WATCHDOG_S", source)
        self.assertNotIn("m_PollInFlightS", source)
        on_tick = _brace_body(source, "void OnTick(float timeslice)")
        in_flight = _brace_body(on_tick, "if (m_PollInFlight)")
        self.assertNotIn("OnPollFail(", in_flight)


if __name__ == "__main__":
    unittest.main()
