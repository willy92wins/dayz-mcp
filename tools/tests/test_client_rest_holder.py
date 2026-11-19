"""The real MCPClientRestHolder / forwarder bodies, exercised (review P3 #3).

Hold/Drop/Count, Disconnect/IsConnected/IsPollActive and the poll/result
callback handlers run here as translations of the addon sources
(``tests.rest_holder_harness`` and the same subset translation the result
pool tests use), never as hand-written Python copies, so a mutation of
the Enforce text mutates the code under test:

- inverting ``Drop``'s ``== cb`` to ``!= cb`` is killed by
  ``test_two_callbacks_in_flight_completing_one_keeps_the_other`` (and by
  the foreign-callback and shared-holder tests);
- inverting ``Hold``'s dedup ``== cb`` to ``!= cb`` is killed by
  ``test_hold_dedups_the_same_callback_and_holds_a_distinct_one``.
"""
from __future__ import annotations

import re
import unittest

from tests.enforce_subset_helpers import (
    EnforceString,
    clean,
    method_body,
    python_source,
)
from tests.rest_holder_harness import WORLD_PATH, make_forwarder, make_holder

POLL_CLASS = "class MCPClientPollCallback : RestCallback"
RESULT_CLASS = "class MCPClientResultCallback : RestCallback"
POLL_SIGNATURES = (
    "void MCPClientPollCallback(MCPClientRestSink sink)",
    "void DetachSink()",
    "override void OnSuccess(string data, int dataSize)",
    "override void OnError(int errorCode)",
    "override void OnTimeout()",
)
RESULT_SIGNATURES = (
    "void MCPClientResultCallback(MCPClientRestSink sink)",
    "void AttachSink(MCPClientRestSink sink)",
    "void DetachSink()",
    "override void OnSuccess(string data, int dataSize)",
    "override void OnError(int errorCode)",
    "override void OnTimeout()",
)


class WireString(EnforceString):
    __slots__ = ()

    def __add__(self, other: object) -> "WireString":
        if isinstance(other, EnforceString):
            return WireString(self.data + other.data)
        if isinstance(other, int):
            return WireString(self.data + str(other).encode("ascii"))
        return NotImplemented


def _class_text(source: str, head: str) -> str:
    text = clean(source)
    start = text.index(head)
    end = text.index("\n};", start)
    return text[start:end]


def _compiled(source: str, signature: str) -> object:
    body = method_body(source, signature)
    text = signature + "\n{" + body + "}\n"
    members = sorted(set(re.findall(r"\bm_\w+", text)))
    name, code = python_source(text, signature, members=members)
    return compile(code, f"<enforce:{name}>", "exec")


class FakeBridge:
    """The bridge behind one forwarder: records every delivery."""

    def __init__(self) -> None:
        self.released: list = []
        self.recycled: list = []
        self.poll_success: list = []
        self.poll_error: list = []
        self.poll_timeout = 0
        self.result_success: list = []
        self.result_error: list = []
        self.result_timeout = 0
        self.active_poll: object = None

    def ReleaseCallback(self, cb) -> None:
        self.released.append(cb)

    def RecycleResultCallback(self, cb) -> None:
        self.recycled.append(cb)

    def IsActivePollCallback(self, cb) -> bool:
        return cb is self.active_poll

    def OnPollSuccess(self, data, size) -> None:
        self.poll_success.append((data, size))

    def OnPollError(self, code) -> None:
        self.poll_error.append(code)

    def OnPollTimeout(self) -> None:
        self.poll_timeout += 1

    def OnResultSuccess(self, data, size) -> None:
        self.result_success.append((data, size))

    def OnResultError(self, code) -> None:
        self.result_error.append(code)

    def OnResultTimeout(self) -> None:
        self.result_timeout += 1


class _Callback:
    """One translated 4_World callback, with fields of its own."""

    def __init__(self, codes, helpers, sink) -> None:
        self._ns: dict = dict(helpers, m_Sink=None, this=self)
        for code in codes:
            exec(code, self._ns)
        self._ctor(sink)

    @property
    def m_Sink(self):
        return self._ns["m_Sink"]

    def DetachSink(self) -> None:
        self._ns["DetachSink"]()

    def OnSuccess(self, data, size) -> None:
        self._ns["OnSuccess"](data, size)

    def OnError(self, code) -> None:
        self._ns["OnError"](code)

    def OnTimeout(self) -> None:
        self._ns["OnTimeout"]()


class PollCallback(_Callback):
    def __init__(self, codes, helpers, sink) -> None:
        super().__init__(codes, helpers, sink)

    def _ctor(self, sink) -> None:
        self._ns["MCPClientPollCallback"](sink)


class ResultCallback(_Callback):
    def _ctor(self, sink) -> None:
        self._ns["MCPClientResultCallback"](sink)

    def AttachSink(self, sink) -> None:
        self._ns["AttachSink"](sink)


class Mission:
    """One mission: a FakeBridge behind its own translated forwarder."""

    def __init__(self, harness: "HolderHarness") -> None:
        self.bridge = FakeBridge()
        self.forwarder = make_forwarder(self.bridge)
        self.harness = harness

    def poll_callback(self) -> PollCallback:
        return PollCallback(self.harness.poll_codes, self.harness.helpers, self.forwarder)

    def result_callback(self) -> ResultCallback:
        return ResultCallback(self.harness.result_codes, self.harness.helpers, self.forwarder)


class HolderHarness:
    """Translated callbacks sharing one translated holder."""

    def __init__(self, world_source: str | None = None) -> None:
        source = WORLD_PATH.read_text(encoding="utf-8") if world_source is None else world_source
        self.printed: list[str] = []
        self.holder = make_holder(source)
        self.helpers = {
            "_S": WireString,
            "MCPClientRestHolder": self.holder,
            "Print": lambda line: self.printed.append(
                line.text if isinstance(line, EnforceString) else str(line)
            ),
        }
        # The holder's translated namespace carries the subset runtime
        # helpers (translate() installed them there); reuse them so the
        # callback translations run against the same helpers.
        self.helpers.update(
            {name: self.holder._ns[name] for name in ("_string_copy", "_bounded", "_vector")}
        )
        poll_text = _class_text(source, POLL_CLASS)
        result_text = _class_text(source, RESULT_CLASS)
        self.poll_codes = [_compiled(poll_text, sig) for sig in POLL_SIGNATURES]
        self.result_codes = [_compiled(result_text, sig) for sig in RESULT_SIGNATURES]

    def mission(self) -> Mission:
        return Mission(self)


class TranslatedHolderTest(unittest.TestCase):
    def test_two_callbacks_in_flight_completing_one_keeps_the_other(self) -> None:
        # Kills the inverted Drop comparison: with `!= cb`, completing the
        # first callback drops the second and keeps the completed one.
        harness = HolderHarness()
        mission = harness.mission()
        first, second = mission.result_callback(), mission.result_callback()
        harness.holder.Hold(first)
        harness.holder.Hold(second)
        self.assertEqual(harness.holder.Count(), 2)
        first.OnSuccess(WireString.of("{}"), 2)
        self.assertEqual(mission.bridge.result_success, [(WireString.of("{}"), 2)])
        self.assertEqual(harness.holder.callbacks, [second])
        self.assertEqual(harness.holder.Count(), 1)
        second.OnSuccess(WireString.of("{}"), 2)
        self.assertEqual(harness.holder.callbacks, [])
        self.assertEqual(harness.holder.Count(), 0)

    def test_hold_dedups_the_same_callback_and_holds_a_distinct_one(self) -> None:
        # Kills the inverted Hold dedup: with `!= cb`, re-holding the same
        # callback inserts a duplicate and holding a distinct one returns
        # without inserting it.
        harness = HolderHarness()
        mission = harness.mission()
        first, second = mission.result_callback(), mission.result_callback()
        harness.holder.Hold(first)
        harness.holder.Hold(first)
        self.assertEqual(harness.holder.Count(), 1)
        self.assertEqual(harness.holder.callbacks, [first])
        harness.holder.Hold(second)
        self.assertEqual(harness.holder.Count(), 2)
        self.assertEqual(harness.holder.callbacks, [first, second])
        harness.holder.Hold(None)
        self.assertEqual(harness.holder.Count(), 2)

    def test_repeated_onerror_and_onerror_after_onsuccess_are_idempotent(self) -> None:
        harness = HolderHarness()
        mission = harness.mission()
        failed, other = mission.result_callback(), mission.result_callback()
        harness.holder.Hold(failed)
        harness.holder.Hold(other)
        failed.OnError(8)
        self.assertEqual(mission.bridge.result_error, [8])
        self.assertEqual(harness.holder.callbacks, [other])
        # OnError may repeat (restapi.c:53): no second delivery, no drop
        # of the callback still in flight.
        failed.OnError(8)
        failed.OnError(9)
        self.assertEqual(mission.bridge.result_error, [8])
        self.assertEqual(harness.holder.callbacks, [other])

        done, survivor = mission.result_callback(), mission.result_callback()
        harness.holder.Hold(done)
        harness.holder.Hold(survivor)
        done.OnSuccess(WireString.of("{}"), 2)
        self.assertEqual(harness.holder.callbacks, [other, survivor])
        # A late OnError for the completed identity drops nothing.
        done.OnError(8)
        self.assertEqual(mission.bridge.result_error, [8])
        self.assertEqual(harness.holder.callbacks, [other, survivor])
        self.assertEqual(harness.holder.Count(), 2)

    def test_a_foreign_callback_that_was_never_held_drops_nothing(self) -> None:
        harness = HolderHarness()
        mission = harness.mission()
        first, second = mission.result_callback(), mission.result_callback()
        harness.holder.Hold(first)
        harness.holder.Hold(second)
        foreign = mission.result_callback()
        foreign.DetachSink()
        foreign.OnSuccess(WireString.of("{}"), 2)
        foreign.OnError(8)
        harness.holder.Drop(foreign)
        self.assertEqual(harness.holder.callbacks, [first, second])
        self.assertEqual(harness.holder.Count(), 2)

    def test_one_holder_shared_by_two_missions(self) -> None:
        harness = HolderHarness()
        mission_a = harness.mission()
        mission_b = harness.mission()
        poll_a = mission_a.poll_callback()
        poll_b = mission_b.poll_callback()
        harness.holder.Hold(poll_a)
        harness.holder.Hold(poll_b)
        # Mission A ends: its real Disconnect cuts only its forwarder.
        mission_a.forwarder.Disconnect()
        self.assertFalse(mission_a.forwarder.IsConnected())
        self.assertTrue(mission_b.forwarder.IsConnected())
        self.assertFalse(mission_a.forwarder.IsPollActive(poll_a))
        poll_a.OnSuccess(WireString.of("x" * 57), 57)
        self.assertEqual(mission_a.bridge.poll_success, [])
        self.assertEqual(harness.holder.callbacks, [poll_b])
        self.assertEqual(
            harness.printed,
            ["[MCP-CLIENT] REST poll completed after shutdown: OnSuccess 57 bytes, dropped"],
        )
        # Mission B's in-flight poll is still held and still delivers.
        mission_b.bridge.active_poll = poll_b
        poll_b.OnSuccess(WireString.of("{}"), 2)
        self.assertEqual(len(mission_b.bridge.poll_success), 1)
        self.assertEqual(harness.holder.callbacks, [])


class TranslatedForwarderTest(unittest.TestCase):
    def test_disconnect_is_connected_and_is_poll_active_are_the_real_bodies(self) -> None:
        harness = HolderHarness()
        mission = harness.mission()
        poll = mission.poll_callback()
        self.assertTrue(mission.forwarder.IsConnected())
        self.assertFalse(mission.forwarder.IsPollActive(poll))
        mission.bridge.active_poll = poll
        self.assertTrue(mission.forwarder.IsPollActive(poll))
        mission.forwarder.Disconnect()
        self.assertFalse(mission.forwarder.IsConnected())
        self.assertFalse(mission.forwarder.IsPollActive(poll))
        self.assertIsNone(mission.forwarder.owner)


class DisconnectedLogLineTest(unittest.TestCase):
    """Item 2: one [MCP-CLIENT] line per post-Shutdown completion."""

    def test_each_disconnected_event_prints_exactly_one_line(self) -> None:
        cases = (
            ("poll", "Success", (WireString.of("{}"), 57),
             "[MCP-CLIENT] REST poll completed after shutdown: OnSuccess 57 bytes, dropped"),
            ("poll", "Error", (8,),
             "[MCP-CLIENT] REST poll completed after shutdown: OnError code=8, dropped"),
            ("poll", "Timeout", (),
             "[MCP-CLIENT] REST poll completed after shutdown: OnTimeout, dropped"),
            ("result", "Success", (WireString.of("{}"), 57),
             "[MCP-CLIENT] REST result completed after shutdown: OnSuccess 57 bytes, dropped"),
            ("result", "Error", (8,),
             "[MCP-CLIENT] REST result completed after shutdown: OnError code=8, dropped"),
            ("result", "Timeout", (),
             "[MCP-CLIENT] REST result completed after shutdown: OnTimeout, dropped"),
        )
        for kind, event, args, expected in cases:
            with self.subTest(kind=kind, event=event):
                harness = HolderHarness()
                mission = harness.mission()
                callback = mission.poll_callback() if kind == "poll" else mission.result_callback()
                harness.holder.Hold(callback)
                mission.forwarder.Disconnect()
                getattr(callback, "On" + event)(*args)
                self.assertEqual(harness.printed, [expected])
                self.assertEqual(harness.holder.callbacks, [])
                self.assertEqual(mission.bridge.released, [])

    def test_a_connected_completion_prints_no_shutdown_line(self) -> None:
        harness = HolderHarness()
        mission = harness.mission()
        callback = mission.result_callback()
        harness.holder.Hold(callback)
        callback.OnSuccess(WireString.of("{}"), 2)
        self.assertEqual(harness.printed, [])
        self.assertEqual(len(mission.bridge.result_success), 1)


class HolderMutantTest(unittest.TestCase):
    """The two reviewer mutants, applied to the source the harness reads."""

    def test_inverted_drop_comparison_fails_the_two_in_flight_test(self) -> None:
        # Only Drop's site: rebuild the source with Drop alone inverted by
        # mutating after Hold's site.
        source = WORLD_PATH.read_text(encoding="utf-8")
        drop_at = source.index("static void Drop(RestCallback cb)")
        head, tail = source[:drop_at], source[drop_at:]
        self.assertEqual(tail.count("s_Callbacks.Get(i) == cb"), 1)
        harness = HolderHarness(head + tail.replace("s_Callbacks.Get(i) == cb", "s_Callbacks.Get(i) != cb", 1))
        mission = harness.mission()
        first, second = mission.result_callback(), mission.result_callback()
        harness.holder.Hold(first)
        harness.holder.Hold(second)
        first.OnSuccess(WireString.of("{}"), 2)
        self.assertNotEqual(harness.holder.callbacks, [second])

    def test_inverted_hold_dedup_fails_the_dedup_test(self) -> None:
        source = WORLD_PATH.read_text(encoding="utf-8")
        hold_at = source.index("static void Hold(RestCallback cb)")
        drop_at = source.index("static void Drop(RestCallback cb)")
        head, mid, tail = source[:hold_at], source[hold_at:drop_at], source[drop_at:]
        self.assertEqual(mid.count("s_Callbacks.Get(i) == cb"), 1)
        harness = HolderHarness(head + mid.replace("s_Callbacks.Get(i) == cb", "s_Callbacks.Get(i) != cb", 1) + tail)
        mission = harness.mission()
        first, second = mission.result_callback(), mission.result_callback()
        harness.holder.Hold(first)
        harness.holder.Hold(first)
        harness.holder.Hold(second)
        self.assertNotEqual(harness.holder.callbacks, [first, second])


if __name__ == "__main__":
    unittest.main()
