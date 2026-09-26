# Positive control for the client half of the f45d diagnosis: the daemon's fence refusal for an
# idle run (409 run_not_owned + hint) is what the MCP client turns into a bare remote_error.
import sys
sys.path.insert(0, r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools")
from dayz_mcp import server, loopback, instance_fence
status, payload = loopback.ServerState._fence_reject_response.__get__(object.__new__(loopback.ServerState))("run_not_owned") if False else instance_fence.fence_error("run_not_owned")
payload["hint"] = getattr(loopback, "_RUN_NOT_OWNED_HINT", "<no hint constant>")
print("daemon wire:", status, payload)
print("client _public_enqueue_error ->", repr(server._public_enqueue_error(payload)))
print("client _remote_error_code    ->", repr(server._remote_error_code(payload)))
for code in ["run_state_unavailable", "enqueue_cancelled", "binding_not_ready", "queue_full"]:
    print(f"{code:24s} ->", repr(server._public_enqueue_error({"error": code, "hint": "x"})))
