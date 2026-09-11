"""Offline ownership contracts. These do not execute Enforce or prove native deallocation."""
from pathlib import Path
import re
import os
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "addon/scripts/5_Mission"


def source(name):
    before = os.environ.get("DAYZ_LEAK_BEFORE_DIR")
    path = Path(before) / (name + ".BEFORE") if before else SCRIPTS / name
    return path.read_text(encoding="utf-8-sig")


def clean(text):
    # Preserve strings so an URL is not mistaken for a // comment.
    return re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/',
                  lambda m: m[0] if m[0].startswith('"') else ' ', text)


def body(text, signature):
    text = clean(text)
    start = text.find(signature)
    if start < 0:
        raise AssertionError(f"Missing lifecycle contract: {signature}")
    start = text.index("{", start)
    depth = 1
    for match in re.finditer(r'"(?:\\.|[^"\\])*"|[{}]', text[start + 1:]):
        if match[0] == "{":
            depth += 1
        elif match[0] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:start + 1 + match.start()]
    raise AssertionError(f"Unclosed body: {signature}")


class CallbackLifetimeContractTest(unittest.TestCase):
    def test_server_backlinks_are_weak_and_target_has_managed_soft_links(self):
        for filename, owner, kinds in (
            ("MCPCallbacks.c", "MCPBridge", ("MCPPollCallback", "MCPResultCallback")),
        ):
            for kind in kinds:
                with self.subTest(kind=kind):
                    cls = body(source(filename), "class " + kind + " :")
                    self.assertRegex(cls, rf"protected\s+{owner}\s+m_Bridge\s*;")
                    self.assertNotRegex(cls, rf"\bref\s+{owner}\s+m_Bridge")
                    self.assertRegex(clean(source(owner + ".c")), rf"class\s+{owner}\s*:\s*Managed\b")
                    for method in ("OnSuccess(", "OnError(", "OnTimeout("):
                        guarded = body(body(cls, "override void " + method), "if (m_Bridge)")
                        self.assertIn("m_Bridge.ReleaseCallback(this);", guarded)

    def test_client_plain_parent_requires_detach_on_each_retired_callback(self):
        text = source("MCPClientBridge.c")
        self.assertIn("class MCPClientBridge extends MCPJobRunnerOwner", text)
        for kind in ("MCPClientPollCallback", "MCPClientResultCallback"):
            cls = body(text, "class " + kind + " :")
            self.assertIn("protected ref MCPClientBridge m_Bridge;", cls)
            for event in ("Success", "Error", "Timeout"):
                if kind == "MCPClientPollCallback" and event == "Success":
                    continue  # Still reusable; the cached pointer is detached at shutdown.
                with self.subTest(kind=kind, event=event):
                    method = body(cls, "override void On" + event + "(")
                    channel = "Poll" if kind == "MCPClientPollCallback" else "Result"
                    dispatch = "m_Bridge.On" + channel + event + "("
                    self.assertIn("DetachBridge();", method[method.index(dispatch):])
                    self.assertIn("m_Bridge.ReleaseCallback(this);", method)

    def test_singletons_own_bridges_through_mission_until_explicit_shutdown(self):
        for owner in ("MCPBridge", "MCPClientBridge"):
            with self.subTest(owner=owner):
                text = source(owner + ".c")
                self.assertRegex(text, rf"static\s+ref\s+{owner}\s+m_Instance")
                self.assertIn("m_Instance = new " + owner + "();", body(text, "static " + owner + " Get()"))
                shutdown = body(text, "static void ShutdownInstance()")
                self.assertLess(shutdown.index("m_Instance.Shutdown();"), shutdown.index("m_Instance = null;"))

    def test_server_allocates_only_when_cached_poll_callback_is_absent(self):
        text = source("MCPBridge.c")
        self.assertIn("ref MCPPollCallback m_PollCallback;", text)
        start = body(text, "protected void StartPoll()")
        lazy = body(start, "if (!m_PollCallback)")
        allocation = "m_PollCallback = new MCPPollCallback(this);"
        self.assertIn(allocation, lazy)
        self.assertEqual(start.count("new MCPPollCallback"), 1)
        self.assertIn("MCPPollCallback cb = m_PollCallback;", start)
        self.assertLess(start.index("m_CallbackRefs.Insert(cb);"), start.index("m_Ctx.GET(cb, request);"))

    def test_poll_completions_release_before_rejecting_old_identity(self):
        for filename, kind in (("MCPCallbacks.c", "MCPPollCallback"), ("MCPClientBridge.c", "MCPClientPollCallback")):
            cls = body(source(filename), "class " + kind + " :")
            for event in ("Success", "Error", "Timeout"):
                with self.subTest(kind=kind, event=event):
                    method = body(cls, "override void On" + event + "(")
                    active = "if (!m_Bridge.IsActivePollCallback(this))"
                    rejection = body(method, active).strip()
                    if kind == "MCPClientPollCallback":
                        self.assertEqual(rejection, "DetachBridge();\n\t\t\t\treturn;")
                    else:
                        self.assertEqual(rejection, "return;")
                    self.assertLess(method.index("ReleaseCallback(this)"), method.index(active))
                    self.assertLess(method.index(active), method.index("m_Bridge.OnPoll" + event + "("))
        for owner in ("MCPBridge", "MCPClientBridge"):
            self.assertIn("return cb == m_PollCallback;", body(source(owner + ".c"), "bool IsActivePollCallback("))

    def test_error_and_timeout_retire_identity_before_allowing_another_request(self):
        # restapi.c documents repeated OnError. A new request must not reuse that identity.
        for owner in ("MCPBridge", "MCPClientBridge"):
            for event in ("Error", "Timeout"):
                with self.subTest(owner=owner, event=event):
                    method = body(source(owner + ".c"), "\tvoid OnPoll" + event + "(")
                    self.assertIn("m_PollCallback = null;", method)
                    self.assertLess(method.index("m_PollCallback = null;"), method.index("OnPollFail("))

    def test_shutdown_detaches_cached_callback_even_when_pending_array_is_empty(self):
        for owner in ("MCPBridge", "MCPClientBridge"):
            with self.subTest(owner=owner):
                shutdown = body(source(owner + ".c"), "\tvoid Shutdown()")
                active = body(shutdown, "if (m_PollCallback)")
                self.assertIn("m_PollCallback.DetachBridge();", active)
                self.assertLess(shutdown.index("m_PollCallback.DetachBridge();"), shutdown.index("m_Ctx.reset();"))
                self.assertIn("m_PollCallback = null;", shutdown)
        for filename, kind in (("MCPCallbacks.c", "MCPPollCallback"), ("MCPClientBridge.c", "MCPClientPollCallback")):
            self.assertEqual(body(body(source(filename), "class " + kind + " :"), "void DetachBridge()").strip(), "m_Bridge = null;")


if __name__ == "__main__":
    unittest.main()
