"""Execute a restricted translation of the real result transport methods.

Not an Enforce VM/compiler or a native retention test. REST, serialization and
array primitives are fakes; branching, allocation, pool operations, callback
handlers and shutdown come from the selected .c source (also understands BEFORE).
Independent oracle: distinct live requests, exact bodies/order, peak allocations,
old failure identities cannot release new requests. Unknown syntax fails closed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "addon/scripts/5_Mission"
TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/')


def clean(text):
    return TOKENS.sub(lambda m: m[0] if m[0].startswith('"') else ' ', text)


def source(name):
    directory = os.environ.get("DAYZ_RESULTLEAK_BEFORE_DIR")
    path = Path(directory) / (name + ".BEFORE") if directory else SCRIPTS / name
    return clean(path.read_text(encoding="utf-8-sig"))


def body(text, signature):
    start = text.find(signature)
    if start < 0:
        raise AssertionError("Missing source method: " + signature)
    start = text.index("{", start)
    depth = 1
    for m in re.finditer(r'"(?:\\.|[^"\\])*"|[{}]', text[start + 1:]):
        depth += (m[0] == "{") - (m[0] == "}")
        if depth == 0:
            return text[start + 1:start + 1 + m.start()]
    raise AssertionError("Unclosed source block: " + signature)


class Array:
    """Vanilla enscript.c:374: non-null even empty; Remove swaps with last."""
    def __init__(self):
        self.items = []

    def Count(self):
        return len(self.items)

    def Insert(self, value):
        self.items.append(value)

    def Get(self, index):
        return self.items[index]

    def Remove(self, index):
        self.items[index] = self.items[-1]
        self.items.pop()

    def Find(self, value):
        return next((i for i, item in enumerate(self.items) if item is value), -1)

    def Clear(self):
        self.items.clear()


class Rest:
    def __init__(self, bridge):
        self.bridge = bridge
        self.requests = []
        self.live = {}
        self.reset_count = 0

    def POST(self, cb, request, payload):
        assert cb not in self.live, "a callback was reused while its POST is live"
        assert self.bridge.m_CallbackRefs.Find(cb) >= 0, "POST lacks script ownership"
        assert cb.m_Bridge is self.bridge, "reused callback was not rebound"
        row = (cb, request, json.loads(payload))
        self.requests.append(row)
        self.live[cb] = row

    def reset(self):
        self.reset_count += 1
        # Adversarial synchronous cancellation: shutdown must detach first.
        for cb in list(self.live):
            cb.OnError(8)
        self.live.clear()


def translate(text, name, parameters, method_names):
    """Translate only this fixture's simple Enforce subset; no copied pool logic."""
    chunks, buffer = [], ""
    for token in re.findall(r'"(?:\\.|[^"\\])*"|[^"{};]+|[{};]', text):
        if token in ("{", "}", ";"):
            if buffer.strip():
                chunks.append(buffer.strip())
            buffer = ""
            if token != ";":
                chunks.append(token)
        else:
            buffer += token
    assert not buffer.strip(), buffer

    def expr(value):
        value = re.sub(r"\bnew MCPResultCallback\(", "self._new_callback(", value)
        value = value.replace("MCPResultCallback.Cast(", "self._cast_callback(")
        def names(m):
            word = m[0]
            if word.startswith('"'):
                return word
            if word in {"null": "None", "true": "True", "false": "False", "this": "self"}:
                return {"null": "None", "true": "True", "false": "False", "this": "self"}[word]
            if (word.startswith("m_") or word.startswith("MAX_") or word in method_names) and (m.start() == 0 or value[m.start() - 1] != "."):
                return "self." + word
            return word
        value = re.sub(r'"(?:\\.|[^"\\])*"|\b[A-Za-z_]\w*\b', names, value)
        return re.sub(r"!(?!=)", "not ", value.replace("&&", " and ").replace("||", " or "))

    lines = ["def " + name + "(self" + (", " + parameters if parameters else "") + "):"]
    indent = 1
    for chunk in chunks:
        if chunk == "{":
            indent += 1
            continue
        if chunk == "}":
            indent -= 1
            continue
        if chunk == "else":
            rendered = "else:"
        elif chunk.startswith(("if (", "while (")):
            keyword, condition = chunk.split(" (", 1)
            assert condition.endswith(")"), chunk
            rendered = keyword + " " + expr(condition[:-1]) + ":"
        elif chunk.startswith("Log("):
            rendered = "pass"  # No transport/state effects; logging string coercion is native.
        elif chunk == "JsonSerializer serializer = new JsonSerializer()":
            rendered = "serializer = None"
        elif chunk == "bool serialized = serializer.WriteToString(result, false, body)":
            rendered = "serialized, body = self._serialize(result)"
        else:
            declaration = re.match(r"^(?:int|bool|string|MCPResultCallback) (\w+)(.*)$", chunk, re.S)
            if declaration:
                variable, tail = declaration.groups()
                rendered = variable + expr(tail) if tail.strip() else variable + " = None"
            else:
                rendered = expr(chunk)
        lines.append("    " * indent + rendered)
    assert indent == 1
    if len(lines) == 1:
        lines.append("    pass")
    namespace = {}
    exec(compile("\n".join(lines) + "\n", "<Enforce-source:" + name + ">", "exec"), {}, namespace)
    return namespace[name]


def model(bridge=None, callbacks=None):
    bridge = source("MCPBridge.c") if bridge is None else clean(bridge)
    callbacks = source("MCPCallbacks.c") if callbacks is None else clean(callbacks)
    cls = body(callbacks, "class MCPResultCallback :")
    bridge_specs = [
        ("PostResult", "result"), ("AcquireResultCallback", ""),
        ("RecycleResultCallback", "cb"), ("OnResultSuccess", "data, dataSize"),
        ("OnResultError", "errorCode"), ("OnResultTimeout", ""),
        ("ReleaseCallback", "cb"), ("Shutdown", ""),
    ]
    cb_specs = [("MCPResultCallback", "bridge"), ("AttachBridge", "bridge"),
                ("DetachBridge", ""), ("OnSuccess", "data, dataSize"),
                ("OnError", "errorCode"), ("OnTimeout", "")]
    def methods(text, specs):
        names = {name for name, _ in specs}
        result = {}
        for name, args in specs:
            match = re.search(r"\b(?:void|MCPResultCallback) " + name + r"\(", text)
            # Constructor has no return type.
            signature = match[0] if match else "void " + name + "("
            if signature not in text:
                continue
            result[name] = translate(body(text, signature), name, args, names)
        return result
    Callback = type("Callback", (), methods(cls, cb_specs))
    Bridge = type("Bridge", (), methods(bridge, bridge_specs))
    b = Bridge()
    declarations = re.findall(r"protected (?:static )?(?:ref )?[^;\n=]+?\b(m_\w+)\s*;", bridge[:bridge.index("void MCPBridge()")])
    for field in declarations:
        setattr(b, field, None)
    for field in ("m_CallbackRefs", "m_Pending", "m_Jobs"):
        setattr(b, field, Array())
    if "m_ResultCallbackPool = new array<ref MCPResultCallback>();" in body(bridge, "void MCPBridge()"):
        b.m_ResultCallbackPool = Array()
    b.MAX_CALLBACK_REFS = int(re.search(r"MAX_CALLBACK_REFS = (\d+);", bridge)[1])
    b.m_Configured = True
    b.m_Key, b.m_PeerInstance = "test", ""
    b.m_Ctx = Rest(b)
    b.allocations = []
    b.serializable = True
    def allocate(owner):
        callback = Callback()
        callback.MCPResultCallback(owner)
        b.allocations.append(callback)
        return callback
    b._new_callback = allocate
    b._cast_callback = lambda value: value if isinstance(value, Callback) else None
    b._serialize = lambda value: (b.serializable, json.dumps(vars(value)))
    b.EncodeQueryValue = lambda value: value
    return b


def post(bridge, identity):
    bridge.PostResult(SimpleNamespace(id=identity, ok=True))
    return bridge.m_Ctx.requests[-1][0]


def complete(bridge, callback, event="Success"):
    bridge.m_Ctx.live.pop(callback, None)
    args = {"Success": ('{"ok":true}', 11), "Error": (8,), "Timeout": ()}
    getattr(callback, "On" + event)(*args[event])


class ResultPoolBehaviorTest(unittest.TestCase):
    def test_2000_successes_allocate_one_and_preserve_every_body(self):
        b = model()
        for i in range(2000):
            complete(b, post(b, i))
            self.assertEqual(b.m_CallbackRefs.Count(), 0)
        self.assertEqual(len(b.allocations), 1, "successful results allocate 1:1")
        self.assertEqual([row[2]["id"] for row in b.m_Ctx.requests], list(range(2000)))

    def test_multiple_inflight_out_of_order_reuses_only_the_completed_slot(self):
        b = model()
        callbacks = [post(b, i) for i in range(8)]
        self.assertEqual(len(set(callbacks)), 8)
        complete(b, callbacks[3])
        next_cb = post(b, 8)
        self.assertIs(next_cb, callbacks[3])
        self.assertEqual(set(b.m_Ctx.live), set(callbacks))
        self.assertEqual(b.m_CallbackRefs.Count(), 8)
        for cb in reversed(callbacks):
            complete(b, cb)
        self.assertEqual(b.m_CallbackRefs.Count(), 0)
        self.assertEqual(len(b.allocations), 8)
        self.assertEqual([row[2]["id"] for row in b.m_Ctx.requests], list(range(9)))

    def test_two_full_128_request_waves_reuse_peak_without_serializing(self):
        b = model()
        for wave in range(2):
            callbacks = [post(b, wave * 128 + i) for i in range(128)]
            self.assertEqual(len(set(callbacks)), 128)
            self.assertEqual(len(b.m_Ctx.live), 128)
            for cb in callbacks[::2] + callbacks[1::2]:
                complete(b, cb)
        self.assertEqual(len(b.allocations), 128)
        self.assertEqual(b.m_ResultCallbackPool.Count(), 128)
        self.assertEqual(len(b.m_Ctx.requests), 256)

    def test_failed_identity_never_recycled_and_late_events_cannot_release_new_work(self):
        for event in ("Error", "Timeout"):
            with self.subTest(event=event):
                b = model()
                old = post(b, 1)
                survivor = post(b, 2)
                complete(b, old, event)
                self.assertIsNone(old.m_Bridge)
                current = post(b, 3)
                self.assertIsNot(current, old)
                for late_event in ("Error", "Error", "Success", "Timeout"):
                    complete(b, old, late_event)
                    self.assertEqual(set(b.m_CallbackRefs.items), {survivor, current})
                complete(b, current)
                self.assertIs(post(b, 4), current)
                complete(b, survivor)

    def test_shutdown_detaches_live_and_idle_before_native_reset_and_is_repeatable(self):
        b = model()
        idle, active = post(b, 1), post(b, 2)
        complete(b, idle)
        self.assertIsNone(idle.m_Bridge)
        ctx = b.m_Ctx
        b.Shutdown()
        self.assertIsNone(active.m_Bridge)
        self.assertIsNone(b.m_ResultCallbackPool)
        self.assertIsNone(b.m_CallbackRefs)
        self.assertEqual(ctx.reset_count, 1)
        b.Shutdown()
        for cb in (idle, active):
            cb.OnSuccess("{}", 2)
            cb.OnError(8)
            cb.OnTimeout()
        self.assertIsNone(b.m_ResultCallbackPool)

    def test_serialization_failure_does_not_acquire_or_send(self):
        b = model()
        b.serializable = False
        b.PostResult(SimpleNamespace(id=7, ok=True))
        self.assertEqual(b.allocations, [])
        self.assertEqual(b.m_Ctx.requests, [])

    def test_foreign_and_duplicate_completed_callbacks_cannot_drop_other_holds(self):
        b = model()
        first, second = post(b, 1), post(b, 2)
        b.ReleaseCallback(object())
        complete(b, first)
        complete(b, first)
        self.assertEqual(b.m_CallbackRefs.items, [second])
        self.assertTrue(hasattr(b, "m_ResultCallbackPool"), "missing success cache")
        self.assertEqual(b.m_ResultCallbackPool.items, [first])

    def test_mutation_omitting_pool_pop_is_detected_as_live_identity_reuse(self):
        src = source("MCPBridge.c")
        mutation = src.replace("m_ResultCallbackPool.Remove(last);", "")
        self.assertTrue(src != mutation, "mutation target missing from BEFORE")
        b = model(bridge=mutation)
        complete(b, post(b, 1))
        post(b, 2)
        with self.assertRaisesRegex(AssertionError, "reused while"):
            post(b, 3)

    def test_mutation_omitting_rebind_is_detected_at_native_handoff(self):
        src = source("MCPBridge.c")
        mutation = src.replace("cb.AttachBridge(this);", "")
        self.assertTrue(src != mutation, "mutation target missing from BEFORE")
        b = model(bridge=mutation)
        complete(b, post(b, 1))
        with self.assertRaisesRegex(AssertionError, "not rebound"):
            post(b, 2)

    def test_mutation_omitting_release_keeps_admission_debt(self):
        src = source("MCPCallbacks.c")
        prefix, result = src.split("class MCPResultCallback :", 1)
        b = model(callbacks=prefix + "class MCPResultCallback :" + result.replace("m_Bridge.ReleaseCallback(this);", ""))
        complete(b, post(b, 1))
        self.assertEqual(b.m_CallbackRefs.Count(), 1)


class ResultPoolSourceTest(unittest.TestCase):
    def test_new_state_and_methods_are_declared_in_their_actual_classes(self):
        bridge, cb = source("MCPBridge.c"), body(source("MCPCallbacks.c"), "class MCPResultCallback :")
        self.assertTrue("protected ref array<ref MCPResultCallback> m_ResultCallbackPool;" in bridge, "result pool member missing")
        self.assertIn("m_ResultCallbackPool = new array<ref MCPResultCallback>();", body(bridge, "void MCPBridge()"))
        for signature in ("protected MCPResultCallback AcquireResultCallback()", "void RecycleResultCallback(MCPResultCallback cb)"):
            self.assertTrue(body(bridge, signature).strip())
        for signature in ("void AttachBridge(MCPBridge bridge)", "void DetachBridge()"):
            self.assertTrue(body(cb, signature).strip())
        self.assertIn("protected MCPBridge m_Bridge;", cb)
        # Every field used by the modified helpers must be declared (not just regex-green calls).
        declared = set(re.findall(r"\b(m_\w+)\s*;", bridge[:bridge.index("void MCPBridge()")]))
        for name in ("AcquireResultCallback", "RecycleResultCallback", "Shutdown"):
            used = set(re.findall(r"\b(m_\w+)\b", body(bridge, name + "(")))
            self.assertLessEqual(used, declared)

    def test_recycle_bound_matches_existing_admission_and_failures_do_not_recycle(self):
        bridge = source("MCPBridge.c")
        recycle = body(bridge, "void RecycleResultCallback(")
        self.assertIn("m_ResultCallbackPool.Count() < MAX_CALLBACK_REFS", recycle)
        self.assertIn("m_CallbackRefs.Count() + m_Pending.Count() + m_Jobs.Count() > MAX_CALLBACK_REFS - MAX_POLL_RESULTS", body(bridge, "protected void StartPoll()"))
        cb = body(source("MCPCallbacks.c"), "class MCPResultCallback :")
        for event in ("Error", "Timeout"):
            handler = body(cb, "override void On" + event + "(")
            self.assertNotIn("RecycleResultCallback", handler)
            self.assertIn("DetachBridge();", handler)
        success = body(cb, "override void OnSuccess(")
        self.assertLess(success.index("ReleaseCallback"), success.index("RecycleResultCallback"))
        self.assertLess(success.index("RecycleResultCallback"), success.index("DetachBridge"))

    def test_post_remains_immediate_async_without_queue_or_wait(self):
        post_body = body(source("MCPBridge.c"), "protected void PostResult(")
        self.assertNotIn("new MCPResultCallback", post_body)
        self.assertIn("MCPResultCallback cb = AcquireResultCallback();", post_body)
        self.assertLess(post_body.index("m_CallbackRefs.Insert(cb);"), post_body.index("m_Ctx.POST(cb, resultRequest, body);"))
        for forbidden in ("POST_now", "CallLater", "Sleep", "m_Pending", "m_PollInFlight", "MAX_CALLBACK_REFS"):
            self.assertNotIn(forbidden, post_body)


if __name__ == "__main__":
    unittest.main()
