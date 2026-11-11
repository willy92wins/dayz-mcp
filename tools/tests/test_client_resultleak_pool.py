"""The client's result POSTs take their callback from a pool, as the server's do.

The 9/11 in-game round counted 138/138, 50 and 394/394 MCPClientResultCallback
instances alive on the client, one new per posted result
(MCPClientBridge.c PostResult), against 1-4 on the server, whose result
callbacks come from a free list (test_resultleak_pool.py). It is not
m_CallbackRefs and not a cycle with the bridge. Not measured: native RestApi
seems to keep every distinct callback instance until the mission is cleaned.
The client now has the server's list: OnSuccess gives its callback back
detached, PostResult takes one and attaches it, OnError and OnTimeout retire
their identity, and Shutdown empties the list after its terminal POSTs.

These mirror test_resultleak_pool.py, test_leak_callback_drain.py and
test_leak_callback_lifetime.py for the client. They run a restricted
translation of the real client bodies (tests.enforce_subset_helpers):
PostResult, AcquireResultCallback, RecycleResultCallback, ReleaseCallback,
OutstandingWork, Shutdown and the MCPClientResultCallback handlers, each
callback with fields of its own. REST, serialization and Shutdown's other
collaborators are fakes, and Log lines are dropped. Not an Enforce VM or a
native retention test: the in-game run counts the live instances.
"""
from __future__ import annotations

import json
import re
import unittest
from types import SimpleNamespace

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceString,
    clean,
    method_body,
    python_source,
    translate,
)

CLIENT_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
CALLBACK_CLASS = "class MCPClientResultCallback : RestCallback"
CALLBACK_SIGNATURES = (
    "void MCPClientResultCallback(MCPClientBridge bridge)",
    "void AttachBridge(MCPClientBridge bridge)",
    "void DetachBridge()",
    "override void OnSuccess(string data, int dataSize)",
    "override void OnError(int errorCode)",
    "override void OnTimeout()",
)
POST = "protected void PostResult(MCPResult result)"
ACQUIRE = "protected MCPClientResultCallback AcquireResultCallback()"
RECYCLE = "void RecycleResultCallback(MCPClientResultCallback cb)"
BRIDGE_SIGNATURES = (
    POST,
    ACQUIRE,
    RECYCLE,
    "void OnResultSuccess(string data, int dataSize)",
    "void OnResultError(int errorCode)",
    "void OnResultTimeout()",
    "void ReleaseCallback(RestCallback cb)",
    "protected int OutstandingWork()",
    "void Shutdown()",
)
CLASS_TYPES = (
    "JsonSerializer",
    "MCPClientResultCallback",
    "MCPClientPollCallback",
    "MCPCommand",
    "MCPJob",
)
ADMISSION = "if (OutstandingWork() > MAX_CALLBACK_REFS - MAX_POLL_RESULTS - 1)"


class WireString(EnforceString):
    """An Enforce string that concatenates, for the request PostResult builds."""

    __slots__ = ()

    def __add__(self, other: object) -> "WireString":
        if isinstance(other, EnforceString):
            return WireString(self.data + other.data)
        if isinstance(other, int):
            return WireString(self.data + str(other).encode("ascii"))
        return NotImplemented


class Array(EnforceArray):
    """array<T>: Remove swaps the last element in (vanilla enscript.c:374)."""

    def Remove(self, index: int) -> None:
        self[index] = self[-1]
        self.pop()


def _class_text(source: str) -> str:
    text = clean(source)
    start = text.index(CALLBACK_CLASS)
    end = text.index("\n};", start)
    return text[start:end]


def _without_logs(source: str, signature: str) -> str:
    body = re.sub(r"\bLog\([^;\n]*\);", "", method_body(source, signature))
    return signature + "\n{" + body + "}\n"


def _members(text: str) -> list[str]:
    return sorted(set(re.findall(r"\bm_\w+", text)))


def _compiled(source: str, signature: str) -> object:
    """A callback method, compiled once and run in each callback's namespace."""
    trimmed = _without_logs(source, signature)
    name, code = python_source(trimmed, signature, members=_members(trimmed))
    return compile(code, f"<enforce:{name}>", "exec")


class Rest:
    """RestContext: POST hands a callback to native code until it completes."""

    def __init__(self, model: "ClientModel") -> None:
        self.model = model
        self.requests: list[tuple[object, str, dict]] = []
        self.live: dict[object, tuple[object, str, dict]] = {}
        self.reset_count = 0

    def POST(self, cb: object, request: WireString, payload: WireString) -> None:
        assert cb not in self.live, "a callback was reused while its POST is live"
        assert self.model.ns["m_CallbackRefs"].Find(cb) >= 0, "POST lacks script ownership"
        assert cb.m_Bridge is self.model.bridge, "reused callback was not rebound"
        row = (cb, request.text, json.loads(payload.text))
        self.requests.append(row)
        self.live[cb] = row

    def reset(self) -> None:
        self.reset_count += 1
        # Adversarial synchronous cancellation: shutdown must detach first.
        for cb in list(self.live):
            cb.OnError(8)
        self.live.clear()


class Callback:
    """One MCPClientResultCallback: its own m_Bridge and its translated handlers."""

    def __init__(self, codes: list[object], helpers: dict, owner: object) -> None:
        self._ns: dict = dict(helpers, m_Bridge=None, this=self)
        for code in codes:
            exec(code, self._ns)
        self._ns["MCPClientResultCallback"](owner)

    @property
    def m_Bridge(self) -> object:
        return self._ns["m_Bridge"]

    def AttachBridge(self, bridge: object) -> None:
        self._ns["AttachBridge"](bridge)

    def DetachBridge(self) -> None:
        self._ns["DetachBridge"]()

    def OnSuccess(self, data: object, size: int) -> None:
        self._ns["OnSuccess"](data, size)

    def OnError(self, code: int) -> None:
        self._ns["OnError"](code)

    def OnTimeout(self) -> None:
        self._ns["OnTimeout"]()


class ClientModel:
    """The client bridge's result transport, run from the real bodies."""

    def __init__(self, source: str | None = None) -> None:
        source = CLIENT_PATH.read_text(encoding="utf-8") if source is None else source
        cls = _class_text(source)
        self.callback_codes = [_compiled(cls, signature) for signature in CALLBACK_SIGNATURES]
        self.allocations: list[Callback] = []
        self.serializable = True
        self.terminal_posts: list[int] = []
        model = self

        class Factory:
            def __call__(self, owner: object) -> Callback:
                callback = Callback(model.callback_codes, model.helpers, owner)
                model.allocations.append(callback)
                return callback

            @staticmethod
            def Cast(value: object) -> Callback | None:
                return value if isinstance(value, Callback) else None

        class Serializer:
            def WriteToString(self, result: object, _pretty: bool, body: WireString) -> bool:
                body.data = json.dumps({"id": result.id, "ok": result.ok}).encode("ascii")
                return model.serializable

        def post_hold_terminal(job: object) -> None:
            model.terminal_posts.append(job.id)
            model.ns["PostResult"](SimpleNamespace(id=job.id, ok=False))

        constants = {
            name: int(value)
            for name, value in re.findall(r"protected const int (MAX_\w+) = (\d+);", source)
        }
        none = lambda *_args: None  # noqa: E731
        self.ns: dict = {
            "_S": WireString,
            "array": Array,
            "MAX_CALLBACK_REFS": constants["MAX_CALLBACK_REFS"],
            "MAX_POLL_RESULTS": constants["MAX_POLL_RESULTS"],
            "MCPClientResultCallback": Factory(),
            "MCPClientPollCallback": SimpleNamespace(Cast=lambda _value: None),
            "JsonSerializer": Serializer,
            "EncodeQueryValue": lambda value: value,
            "AddLoadCounter": lambda value, amount: value + amount,
            "MCPCommand": lambda: SimpleNamespace(cmd=WireString(b"")),
            "MCPJob": lambda: SimpleNamespace(id=0),
            "MCPHoldControl": SimpleNamespace(Shutdown=none),
            "PostActionHoldJob": post_hold_terminal,
            "PostUiDialogJob": none,
            "MCPVehicleTrace": SimpleNamespace(Abort=none),
            "MCPAnimTimeline": SimpleNamespace(Abort=none),
            "MCPPlayerTrace": SimpleNamespace(Abort=none),
            "MCPCarDrive": SimpleNamespace(Clear=none),
            "MCPInputTriggerControl": SimpleNamespace(ReleaseAll=none),
            "MCPPlayerMoveControl": SimpleNamespace(ShutdownRelease=none),
            "StopCameraPath": none,
            "RestoreGameplay": none,
            "ReleaseCamera": none,
            "m_Configured": True,
            "m_Key": WireString.of("test"),
            "m_PeerInstance": WireString(b""),
            "m_CmdBytes": 0,
            "m_CallbackRefs": Array(),
            "m_PollCallbackRefs": Array(),
            "m_PollCallback": None,
            "m_Pending": Array(),
            "m_JobRunner": SimpleNamespace(Count=lambda: 0, Clear=none),
            "m_Shutdown": False,
            "m_ShutdownReentryLogged": False,
            "m_Dialog": None,
            "m_DialogJob": None,
            "m_DialogSink": None,
            "m_DialogHostTried": False,
            "m_UiPreviewRoot": None,
            "m_ActionHoldCommandId": -1,
            "m_PollInFlight": False,
            "m_PollVersion": WireString(b""),
            "m_Accum": 0.0,
            "m_Backoff": 0.0,
        }
        ctor = method_body(source, "void MCPClientBridge()")
        if "m_ResultCallbackPool = new array<ref MCPClientResultCallback>();" in ctor:
            self.ns["m_ResultCallbackPool"] = Array()
        for signature in BRIDGE_SIGNATURES:
            if signature not in clean(source):
                continue
            trimmed = _without_logs(source, signature)
            translate(
                trimmed,
                signature,
                self.ns,
                class_types=CLASS_TYPES,
                members=_members(trimmed),
            )
        self.helpers = {
            name: self.ns[name] for name in ("_S", "_string_copy", "_bounded", "_vector")
        }
        self.ctx = Rest(self)
        self.ns["m_Ctx"] = self.ctx
        self.ns["m_PollCtx"] = self.ctx
        self.bridge = SimpleNamespace(
            ReleaseCallback=self.ns["ReleaseCallback"],
            OnResultSuccess=self.ns["OnResultSuccess"],
            OnResultError=self.ns["OnResultError"],
            OnResultTimeout=self.ns["OnResultTimeout"],
        )
        if "RecycleResultCallback" in self.ns:
            self.bridge.RecycleResultCallback = self.ns["RecycleResultCallback"]
        self.ns["this"] = self.bridge

    def post(self, identity: int) -> Callback:
        self.ns["PostResult"](SimpleNamespace(id=identity, ok=True))
        return self.ctx.requests[-1][0]

    def complete(self, callback: Callback, event: str = "Success") -> None:
        self.ctx.live.pop(callback, None)
        args = {"Success": (WireString.of('{"ok":true}'), 11), "Error": (8,), "Timeout": ()}
        getattr(callback, "On" + event)(*args[event])

    @property
    def refs(self) -> Array:
        return self.ns["m_CallbackRefs"]

    @property
    def pool(self) -> Array | None:
        return self.ns.get("m_ResultCallbackPool")

    def blocked(self) -> bool:
        ns = self.ns
        return ns["OutstandingWork"]() > ns["MAX_CALLBACK_REFS"] - ns["MAX_POLL_RESULTS"] - 1


class ClientResultPoolBehaviorTest(unittest.TestCase):
    def test_2000_successes_allocate_one_and_preserve_every_body(self) -> None:
        model = ClientModel()
        for identity in range(2000):
            model.complete(model.post(identity))
            self.assertEqual(model.refs.Count(), 0)
        self.assertEqual(len(model.allocations), 1, "one callback per result is the 9/11 leak")
        self.assertEqual([row[2]["id"] for row in model.ctx.requests], list(range(2000)))
        self.assertEqual({row[1] for row in model.ctx.requests}, {"result?key=test"})

    def test_an_idle_callback_holds_no_bridge(self) -> None:
        # MCPClientBridge is not Managed and m_Bridge is a strong ref: an
        # idle pooled callback that kept it would pin the bridge.
        model = ClientModel()
        idle = model.post(1)
        model.complete(idle)
        self.assertEqual(list(model.pool), [idle])
        self.assertIsNone(idle.m_Bridge)
        self.assertIs(model.post(2), idle)
        self.assertIs(idle.m_Bridge, model.bridge)

    def test_multiple_inflight_out_of_order_reuses_only_the_completed_slot(self) -> None:
        model = ClientModel()
        callbacks = [model.post(identity) for identity in range(8)]
        self.assertEqual(len(set(callbacks)), 8)
        model.complete(callbacks[3])
        self.assertIs(model.post(8), callbacks[3])
        self.assertEqual(set(model.ctx.live), set(callbacks))
        self.assertEqual(model.refs.Count(), 8)
        for callback in reversed(callbacks):
            model.complete(callback)
        self.assertEqual(model.refs.Count(), 0)
        self.assertEqual(len(model.allocations), 8)
        self.assertEqual([row[2]["id"] for row in model.ctx.requests], list(range(9)))

    def test_two_full_128_request_waves_reuse_peak_without_serializing(self) -> None:
        model = ClientModel()
        for wave in range(2):
            callbacks = [model.post(wave * 128 + index) for index in range(128)]
            self.assertEqual(len(set(callbacks)), 128)
            self.assertEqual(len(model.ctx.live), 128)
            for callback in callbacks[::2] + callbacks[1::2]:
                model.complete(callback)
        self.assertEqual(len(model.allocations), 128)
        self.assertEqual(model.pool.Count(), 128)
        self.assertEqual(len(model.ctx.requests), 256)

    def test_the_pool_is_bounded_by_max_callback_refs(self) -> None:
        model = ClientModel()
        callbacks = [model.post(identity) for identity in range(130)]
        for callback in callbacks:
            model.complete(callback)
        self.assertEqual(model.pool.Count(), model.ns["MAX_CALLBACK_REFS"])

    def test_failed_identity_never_recycled_and_late_events_cannot_release_new_work(self) -> None:
        for event in ("Error", "Timeout"):
            with self.subTest(event=event):
                model = ClientModel()
                old = model.post(1)
                survivor = model.post(2)
                model.complete(old, event)
                self.assertIsNone(old.m_Bridge)
                self.assertEqual(model.pool.Count(), 0)
                current = model.post(3)
                self.assertIsNot(current, old)
                for late_event in ("Error", "Error", "Success", "Timeout"):
                    model.complete(old, late_event)
                    self.assertEqual(set(model.refs), {survivor, current})
                    self.assertEqual(model.pool.Count(), 0)
                model.complete(current)
                self.assertIs(model.post(4), current)
                model.complete(survivor)
                self.assertEqual(len(model.allocations), 3)

    def test_shutdown_detaches_live_and_idle_before_native_reset_and_is_repeatable(self) -> None:
        model = ClientModel()
        idle, active = model.post(1), model.post(2)
        model.complete(idle)
        self.assertIsNone(idle.m_Bridge)
        model.ns["Shutdown"]()
        self.assertIsNone(active.m_Bridge)
        self.assertIsNone(model.pool)
        self.assertEqual(model.refs.Count(), 0)
        self.assertEqual(model.ctx.reset_count, 1)
        model.ns["Shutdown"]()
        self.assertEqual(model.ctx.reset_count, 1)
        for callback in (idle, active):
            callback.OnSuccess(WireString.of("{}"), 2)
            callback.OnError(8)
            callback.OnTimeout()
        self.assertIsNone(model.pool)

    def test_a_shutdown_terminal_post_takes_a_pooled_callback_before_the_pool_empties(self) -> None:
        # The hold terminal POST runs inside Shutdown, before the pool is
        # emptied: it reuses an idle callback, which the detach pass then
        # retires; the shared context is not reset and keeps its POST.
        model = ClientModel()
        idle = model.post(1)
        model.complete(idle)
        model.ns["m_ActionHoldCommandId"] = 5
        model.ns["Shutdown"]()
        self.assertEqual(model.terminal_posts, [5])
        terminal, _request, payload = model.ctx.requests[-1]
        self.assertIs(terminal, idle)
        self.assertEqual(payload["id"], 5)
        self.assertIsNone(terminal.m_Bridge)
        self.assertIsNone(model.pool)
        self.assertEqual(model.ctx.reset_count, 0)
        self.assertEqual(list(model.refs), [terminal])
        model.complete(terminal)
        self.assertIsNone(model.pool)
        self.assertEqual(list(model.refs), [terminal])
        self.assertEqual(len(model.allocations), 1)

    def test_serialization_failure_does_not_acquire_or_send(self) -> None:
        model = ClientModel()
        model.serializable = False
        model.ns["PostResult"](SimpleNamespace(id=7, ok=True))
        self.assertEqual(model.allocations, [])
        self.assertEqual(model.ctx.requests, [])

    def test_foreign_and_duplicate_completed_callbacks_cannot_drop_other_holds(self) -> None:
        model = ClientModel()
        first, second = model.post(1), model.post(2)
        model.ns["ReleaseCallback"](object())
        model.complete(first)
        model.complete(first)
        self.assertEqual(list(model.refs), [second])
        self.assertEqual(list(model.pool), [first])


class ClientResultDrainTest(unittest.TestCase):
    """Admission (OutstandingWork, StartPoll's guard) drains as results complete."""

    def test_success_error_and_timeout_completions_all_drain(self) -> None:
        for event in ("Success", "Error", "Timeout"):
            with self.subTest(event=event):
                model = ClientModel()
                for identity in range(150):
                    callback = model.post(identity)
                    self.assertEqual(model.refs.Count(), 1)
                    model.complete(callback, event)
                    self.assertEqual(model.refs.Count(), 0)
                    self.assertFalse(model.blocked())

    def test_backpressure_reopens_after_a_result_ack_and_idle_callbacks_owe_nothing(self) -> None:
        model = ClientModel()
        results = [model.post(identity) for identity in range(65)]
        self.assertTrue(model.blocked())
        model.complete(results[20])
        self.assertEqual(model.refs.Count(), 64)
        self.assertNotIn(results[20], list(model.refs))
        self.assertTrue(model.blocked())
        model.complete(results[21])
        self.assertFalse(model.blocked())
        for callback in results:
            model.complete(callback)
        self.assertEqual(model.refs.Count(), 0)
        self.assertEqual(model.pool.Count(), 65)
        self.assertFalse(model.blocked())


class ClientResultPoolMutationTest(unittest.TestCase):
    def _mutant(self, old: str, new: str = "") -> ClientModel:
        source = CLIENT_PATH.read_text(encoding="utf-8")
        self.assertEqual(source.count(old), 1, old)
        return ClientModel(source.replace(old, new))

    def test_omitting_the_pool_pop_is_detected_as_live_identity_reuse(self) -> None:
        model = self._mutant("m_ResultCallbackPool.Remove(last);")
        model.complete(model.post(1))
        model.post(2)
        with self.assertRaisesRegex(AssertionError, "reused while"):
            model.post(3)

    def test_omitting_the_rebind_is_detected_at_native_handoff(self) -> None:
        model = self._mutant("cb.AttachBridge(this);")
        model.complete(model.post(1))
        with self.assertRaisesRegex(AssertionError, "not rebound"):
            model.post(2)

    def test_omitting_the_recycle_allocates_one_callback_per_result(self) -> None:
        model = self._mutant("m_Bridge.RecycleResultCallback(this);")
        for identity in range(50):
            model.complete(model.post(identity))
        self.assertEqual(len(model.allocations), 50)

    def test_omitting_the_release_keeps_admission_debt(self) -> None:
        source = CLIENT_PATH.read_text(encoding="utf-8")
        start = source.index(CALLBACK_CLASS)
        success = source.index("override void OnSuccess(", start)
        release = source.index("m_Bridge.ReleaseCallback(this);", success)
        mutant = source[:release] + source[release + len("m_Bridge.ReleaseCallback(this);") :]
        model = ClientModel(mutant)
        model.complete(model.post(1))
        self.assertEqual(model.refs.Count(), 1)


def _body(text: str, signature: str) -> str:
    return method_body(text, signature)


class ClientResultPoolSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CLIENT_PATH.read_text(encoding="utf-8")
        self.clean = clean(self.source)
        self.callback = _class_text(self.source)

    def test_the_pool_state_and_methods_are_declared_in_their_classes(self) -> None:
        self.assertIn(
            "protected ref array<ref MCPClientResultCallback> m_ResultCallbackPool;", self.clean
        )
        self.assertIn(
            "m_ResultCallbackPool = new array<ref MCPClientResultCallback>();",
            _body(self.source, "void MCPClientBridge()"),
        )
        for signature in (ACQUIRE, RECYCLE):
            self.assertTrue(_body(self.source, signature).strip())
        self.assertEqual(
            _body(self.callback, "void AttachBridge(MCPClientBridge bridge)").strip(),
            "m_Bridge = bridge;",
        )
        self.assertEqual(_body(self.callback, "void DetachBridge()").strip(), "m_Bridge = null;")
        self.assertIn("protected ref MCPClientBridge m_Bridge;", self.callback)

    def test_recycle_bound_matches_admission_and_failures_do_not_recycle(self) -> None:
        recycle = _body(self.source, RECYCLE)
        self.assertIn("m_ResultCallbackPool.Count() < MAX_CALLBACK_REFS", recycle)
        self.assertIn(ADMISSION, _body(self.source, "protected void StartPoll()"))
        for event in ("Error", "Timeout"):
            handler = _body(self.callback, CALLBACK_SIGNATURES[4 if event == "Error" else 5])
            self.assertNotIn("RecycleResultCallback", handler)
            self.assertIn("DetachBridge();", handler)
        success = _body(self.callback, CALLBACK_SIGNATURES[3])
        self.assertLess(success.index("ReleaseCallback(this)"), success.index("OnResultSuccess("))
        self.assertLess(success.index("OnResultSuccess("), success.index("RecycleResultCallback(this)"))
        self.assertLess(success.index("RecycleResultCallback(this)"), success.index("DetachBridge();"))

    def test_post_takes_a_pooled_callback_and_stays_immediate_async(self) -> None:
        post = _body(self.source, POST)
        self.assertNotIn("new MCPClientResultCallback", post)
        self.assertIn("MCPClientResultCallback cb = AcquireResultCallback();", post)
        self.assertLess(post.index("m_CallbackRefs.Insert(cb);"), post.index("m_Ctx.POST(cb, resultRequest, body);"))
        for forbidden in ("POST_now", "CallLater", "Sleep", "m_Pending", "m_PollInFlight", "MAX_CALLBACK_REFS"):
            self.assertNotIn(forbidden, post)
        self.assertEqual(self.clean.count("new MCPClientResultCallback("), 1)
        self.assertIn("new MCPClientResultCallback(this)", _body(self.source, ACQUIRE))

    def test_shutdown_empties_the_pool_after_the_terminal_posts_and_detach_pass(self) -> None:
        shutdown = _body(self.source, "void Shutdown()")
        order = [
            "PostUiDialogJob(m_DialogJob);",
            "PostActionHoldJob(holdJob);",
            "resultCb.DetachBridge();",
            "m_ResultCallbackPool.Clear();",
            "m_ResultCallbackPool = null;",
            "m_Ctx.reset();",
            "m_Ctx = null;",
        ]
        positions = []
        for token in order:
            self.assertEqual(shutdown.count(token), 1, token)
            positions.append(shutdown.index(token))
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
