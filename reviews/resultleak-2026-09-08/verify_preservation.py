"""Read-only preservation checks against the exact session preimages."""
import hashlib
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from tests.test_resultleak_pool import body, clean

for row in json.loads((OUT / "before-manifest.json").read_text(encoding="utf-8")):
    p = Path(row["path"])
    data = (OUT / (p.name + ".BEFORE")).read_bytes()
    assert len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"]
print("PASS: all three BEFORE backups retain their recorded bytes and SHA-256")
base = ROOT / "addon/scripts/5_Mission"
assert (base / "MCPClientBridge.c").read_bytes() == (OUT / "MCPClientBridge.c.BEFORE").read_bytes()
print("PASS: entire MCPClientBridge.c byte-identical, including camera/guards/poll/shutdown")
old_cb = (OUT / "MCPCallbacks.c.BEFORE").read_bytes()
new_cb = (base / "MCPCallbacks.c").read_bytes()
assert old_cb.split(b"class MCPResultCallback :")[0] == new_cb.split(b"class MCPResultCallback :")[0]
print("PASS: entire MCPPollCallback class byte-identical")
old = clean((OUT / "MCPBridge.c.BEFORE").read_text(encoding="utf-8"))
new = clean((base / "MCPBridge.c").read_text(encoding="utf-8"))
names = ["OnTick", "StartPoll", "OnPollSuccess", "DrainPending", "QueuePendingOrFail", "IsActivePollCallback", "OnPollError", "OnPollTimeout", "OnPollFail", "TryInit", "Dispatch", "ProcessJobs", "PostCommandError", "ReleaseCallback"]
for name in names:
    # First occurrence can be a call; use the real typed method signature.
    import re
    signature = re.search(r"\b(?:void|bool) " + name + r"\(", old)[0]
    assert body(old, signature) == body(new, signature), name
print("PASS: 14 server poll/admission/dispatch/drain/guard methods unchanged")
old_post = body(old, "protected void PostResult(")
new_post = body(new, "protected void PostResult(")
assert new_post.replace("MCPResultCallback cb = AcquireResultCallback();", "MCPResultCallback cb = new MCPResultCallback(this);") == old_post
print("PASS: PostResult differs only in callback acquisition; same POST path/order/body/logging")
for name in ["MCPBridge.c", "MCPCallbacks.c", "MCPClientBridge.c"]:
    p = base / name
    data = p.read_bytes()
    print(f"SHA256 {name} {len(data)} {hashlib.sha256(data).hexdigest()}")
