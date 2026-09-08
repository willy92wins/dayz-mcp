"""Source-linked admission/drain model, NOT an Enforce VM or a native leak test.

Queue oracle: every completed request releases its hold, unfinished requests stay
owned, and completing work reopens admission. Mutation controls remove Release
or Remove in memory to prove that a silently accumulating queue is detected.
"""
import re
import unittest
from tests.test_leak_callback_lifetime import body, clean, source


class SourceDrain:
    def __init__(self, bridge=None, callbacks=None):
        self.bridge = clean(source("MCPBridge.c") if bridge is None else bridge)
        self.callbacks = clean(source("MCPCallbacks.c") if callbacks is None else callbacks)
        self.start = body(self.bridge, "protected void StartPoll()")
        self.release = body(self.bridge, "void ReleaseCallback(RestCallback cb)")
        self.refs = []
        self.cached = None
        self.allocations = 0
        self.sent = 0
        self.dispatched = []
        self.pending = 0
        self.jobs = 0
        self.constants = {name: int(re.search(r"\b" + name + r"\s*=\s*(\d+)\s*;", self.bridge)[1])
                          for name in ("MAX_CALLBACK_REFS", "MAX_POLL_RESULTS")}
        self.condition = re.search(r"if\s*\((m_CallbackRefs\.Count\(\)[^\n]+)\)", self.start)[1]
        self.condition = self.condition.replace("m_CallbackRefs.Count()", "callbacks").replace("m_Pending.Count()", "pending").replace("m_Jobs.Count()", "jobs")

    def blocked(self):
        return eval(self.condition, {"__builtins__": {}}, dict(self.constants, callbacks=len(self.refs), pending=self.pending, jobs=self.jobs))

    def poll(self):
        if self.blocked():
            return None
        # The original unguarded allocation and the fixed lazy allocation are
        # both understood, so the red measures 3420 objects rather than a regex.
        lazy = "if (!m_PollCallback)" in self.start
        if not lazy or self.cached is None:
            self.cached = object()
            self.allocations += 1
        cb = self.cached
        if "m_CallbackRefs.Insert(cb);" in self.start:
            self.refs.append(cb)
        self.sent += 1
        return cb

    def result(self):
        cb = object()
        post = body(self.bridge, "protected void PostResult(MCPResult result)")
        if "m_CallbackRefs.Insert(cb);" in post:
            self.refs.append(cb)
        return cb

    def complete(self, cb, event="Success", result=False):
        kind = "MCPResultCallback" if result else "MCPPollCallback"
        method = body(body(self.callbacks, "class " + kind + " :"), "override void On" + event + "(")
        release = "m_Bridge.ReleaseCallback(this);"
        if release in method and "m_CallbackRefs.Find(cb)" in self.release:
            branch = body(self.release, "if (idx >= 0)")
            if "m_CallbackRefs.Remove(idx);" in branch and cb in self.refs:
                self.refs.remove(cb)
        if "IsActivePollCallback(this)" in method and cb is not self.cached:
            return
        self.dispatched.append((cb, event))
        if not result and event in ("Error", "Timeout"):
            handler = body(self.bridge, "\tvoid OnPoll" + event + "(")
            if "m_PollCallback = null;" in handler:
                self.cached = None


class CallbackDrainTest(unittest.TestCase):
    def test_3420_successful_polls_drain_and_allocate_one_callback(self):
        model = SourceDrain()
        for i in range(3420):
            cb = model.poll()
            self.assertIsNotNone(cb, f"admission stopped at poll {i}")
            self.assertEqual(len(model.refs), 1)
            model.complete(cb)
            self.assertEqual(model.refs, [], f"completed poll {i} still consumes admission")
        self.assertEqual(model.sent, 3420)
        self.assertEqual(model.allocations, 1, "one native RestCallback allocation per poll amplifies native retention")
        self.assertFalse(model.blocked())

    def test_success_error_timeout_and_result_completions_all_drain(self):
        for event in ("Success", "Error", "Timeout"):
            model = SourceDrain()
            for _ in range(150):
                poll = model.poll()
                result = model.result()
                self.assertEqual(len(model.refs), 2)
                model.complete(poll, event)
                self.assertEqual(model.refs, [result])
                model.complete(result, event, result=True)
                self.assertEqual(model.refs, [])
                self.assertFalse(model.blocked())

    def test_backpressure_reopens_after_a_result_ack_without_dropping_other_holds(self):
        model = SourceDrain()
        results = [model.result() for _ in range(65)]
        self.assertTrue(model.blocked())
        self.assertIsNone(model.poll())
        model.complete(results[20], result=True)
        self.assertEqual(len(model.refs), 64)
        self.assertNotIn(results[20], model.refs)
        self.assertFalse(model.blocked())
        poll = model.poll()
        self.assertIsNotNone(poll)
        self.assertTrue(model.blocked())
        model.complete(poll)
        self.assertEqual(len(model.refs), 64)
        for cb in results:
            model.complete(cb, result=True)
        self.assertEqual(model.refs, [])
        self.assertIsNotNone(model.poll())

    def test_out_of_order_and_duplicate_result_acks_remove_only_their_own_hold(self):
        model = SourceDrain()
        first, second, third = [model.result() for _ in range(3)]
        model.complete(second, result=True)
        model.complete(second, result=True)
        model.complete(object(), result=True)
        self.assertEqual(set(model.refs), {first, third})
        model.complete(third, "Error", result=True)
        model.complete(first, "Timeout", result=True)
        self.assertEqual(model.refs, [])

    def test_late_error_generation_cannot_release_or_dispatch_current_poll(self):
        model = SourceDrain()
        old = model.poll()
        model.complete(old, "Error")
        current = model.poll()
        self.assertIsNot(old, current)
        before = list(model.dispatched)
        for event in ("Success", "Error", "Timeout"):
            model.complete(old, event)
            self.assertEqual(model.refs, [current])
            self.assertEqual(model.dispatched, before)
        model.complete(current)
        self.assertEqual(model.refs, [])

    def test_pending_and_jobs_reservation_is_kept_when_no_callbacks_are_held(self):
        model = SourceDrain()
        model.pending, model.jobs = 33, 32
        self.assertTrue(model.blocked())
        model.jobs -= 1
        self.assertFalse(model.blocked())
        poll = model.poll()
        self.assertIsNotNone(poll)
        model.complete(poll)
        self.assertEqual(model.refs, [])

    def test_mutations_missing_release_or_missing_remove_close_admission(self):
        # Negative controls against independent invariant: with 65 completed
        # requests left in the array, the exact production guard must close.
        for mutation in ("release", "remove"):
            bridge, callbacks = source("MCPBridge.c"), source("MCPCallbacks.c")
            if mutation == "release":
                callbacks = callbacks.replace("m_Bridge.ReleaseCallback(this);", "")
            else:
                bridge = bridge.replace("m_CallbackRefs.Remove(idx);", "")
            with self.subTest(mutation=mutation):
                model = SourceDrain(bridge, callbacks)
                for _ in range(65):
                    cb = model.poll()
                    self.assertIsNotNone(cb)
                    model.complete(cb)
                self.assertEqual(len(model.refs), 65)
                self.assertTrue(model.blocked())
                self.assertIsNone(model.poll())

    def test_never_completed_request_stays_held_and_inflight_stops_new_poll(self):
        model = SourceDrain()
        cb = model.poll()
        self.assertEqual(model.refs, [cb])
        tick = body(source("MCPBridge.c"), "void OnTick(float timeslice)")
        self.assertEqual(body(tick, "if (m_PollInFlight)").strip(), "return;")
        self.assertLess(tick.index("if (m_PollInFlight)"), tick.index("StartPoll();"))
        # Keeping this hold is required: dropping it would assume native ownership.
        self.assertEqual(model.refs, [cb])


if __name__ == "__main__":
    unittest.main()
