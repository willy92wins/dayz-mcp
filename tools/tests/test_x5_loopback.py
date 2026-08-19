from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dayz_mcp import loopback


def version_gate(version: str | None, require_version: bool = False) -> str:
    if version is None:
        return "legacy_blocked" if require_version else "legacy"
    if "~" not in version:
        return "version_mismatch"
    if version != "4~ok":
        return "version_mismatch"
    return "ok"


class X5LoopbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.key = "test-key"
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self) -> None:
        if hasattr(self, "httpd"):
            self.httpd.shutdown()
            self.thread.join(timeout=2.0)
            self.httpd.server_close()
        self.tmp.cleanup()

    def start_server(self, state: loopback.ServerState) -> None:
        self.state = state
        self.httpd = loopback.create_http_server(0, state, log_sink=lambda _message: None)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def request(self, method: str, path: str, payload: dict | None = None, query: dict | None = None) -> tuple[int, dict]:
        params = dict(query or {})
        params["key"] = self.key
        url = self.base + path + "?" + urllib.parse.urlencode(params)
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=2.0) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return int(exc.code), json.loads(exc.read().decode("utf-8"))
            finally:
                exc.close()

    def enqueue_query(self) -> int:
        status, body = self.request("POST", "/enqueue", {"cmd": "query_player_state", "args": {}})
        self.assertEqual(status, 200)
        return int(body["id"])

    def test_delivery_gate_blocks_mismatch_without_draining_queue(self) -> None:
        self.start_server(loopback.ServerState(self.key, version_validator=version_gate))
        self.request("GET", "/poll", query={"peer": "server", "ver": "4~ok"})
        command_id = self.enqueue_query()

        status, body = self.request("GET", "/poll", query={"peer": "server", "ver": "999~x"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"commands": []})
        snapshot = self.state.status_snapshot()
        self.assertEqual(snapshot["peers"]["server"]["version"], "999~x")
        self.assertEqual(snapshot["peers"]["server"]["queue_depth"], 1)

        status, body = self.request("GET", "/poll", query={"peer": "server", "ver": "4~ok"})
        self.assertEqual(status, 200)
        self.assertEqual([command["id"] for command in body["commands"]], [command_id])

    def test_delivery_gate_blocks_legacy_and_none_overwrites_version(self) -> None:
        self.start_server(loopback.ServerState(self.key, version_validator=lambda version: version_gate(version, require_version=True)))
        self.request("GET", "/poll", query={"peer": "server", "ver": "4~ok"})
        self.enqueue_query()

        status, body = self.request("GET", "/poll", query={"peer": "server"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"commands": []})
        snapshot = self.state.status_snapshot()
        self.assertIsNone(snapshot["peers"]["server"]["version"])
        self.assertEqual(snapshot["peers"]["server"]["queue_depth"], 1)

    def test_without_validator_mismatch_and_legacy_poll_drain_as_before(self) -> None:
        self.start_server(loopback.ServerState(self.key))
        first_id = self.enqueue_query()
        status, body = self.request("GET", "/poll", query={"peer": "server", "ver": "999~x"})
        self.assertEqual(status, 200)
        self.assertEqual([command["id"] for command in body["commands"]], [first_id])

        second_id = self.enqueue_query()
        status, body = self.request("GET", "/poll", query={"peer": "server"})
        self.assertEqual(status, 200)
        self.assertEqual([command["id"] for command in body["commands"]], [second_id])

    def test_none_version_is_not_sticky(self) -> None:
        state = loopback.ServerState(self.key)
        status, _body = state.record_poll("server", version="4~ok")
        self.assertEqual(status, 200)
        status, _body = state.record_poll("server", version=None)
        self.assertEqual(status, 200)
        self.assertIsNone(state.status_snapshot()["peers"]["server"]["version"])

    def test_malformed_ver_values_are_version_mismatch_and_do_not_drain(self) -> None:
        self.start_server(loopback.ServerState(self.key, version_validator=version_gate))
        self.enqueue_query()
        status, body = self.request("GET", "/poll", query={"peer": "server", "ver": "bad"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"commands": []})
        self.assertEqual(self.state.status_snapshot()["peers"]["server"]["version"], "bad")

        self.request("GET", "/poll", query={"peer": "server", "ver": "4~ok"})
        self.enqueue_query()
        status, body = self.request("GET", "/poll", query={"peer": "server", "ver": ""})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"commands": []})
        self.assertEqual(self.state.status_snapshot()["peers"]["server"]["version"], "")

    def test_http_exec_denied_writes_audit_jsonl(self) -> None:
        audit_path = self.tmp_path / "exec.jsonl"

        def audit(expr: str, verdict: str, main_fn: str, command_id: int | None) -> None:
            entry = {"expr": expr, "verdict": verdict, "main_fn": main_fn, "command_id": command_id}
            with audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, separators=(",", ":")) + "\n")

        self.start_server(loopback.ServerState(self.key, enable_exec_enforce=True, exec_allowlist={"allowed"}, exec_audit=audit))
        status, body = self.request("POST", "/enqueue", {"cmd": "exec_enforce", "args": {"expr": "denied", "main_fn": "main"}})
        self.assertEqual(status, 403)
        self.assertEqual(body, {"error": "exec_not_allowed"})

        lines = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lines, [{"expr": "denied", "verdict": "denied", "main_fn": "main", "command_id": None}])

    def test_http_exec_audit_failure_is_503_and_not_enqueued(self) -> None:
        def audit(_expr: str, _verdict: str, _main_fn: str, _command_id: int | None) -> None:
            raise OSError("audit locked")

        state = loopback.ServerState(self.key, enable_exec_enforce=True, exec_allowlist={"allowed"}, exec_audit=audit)
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(state)
        self.start_server(state)
        status, body = self.request("POST", "/enqueue", {"cmd": "exec_enforce", "args": {"expr": "allowed"}})
        self.assertEqual(status, 503)
        self.assertEqual(body, {"error": "audit_failed"})

        status, body = self.request("GET", "/poll", query={"peer": "server"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"commands": []})

    def test_exec_queue_full_rejects_before_allowed_audit(self) -> None:
        # F-1: a queue-full exec_enforce must 429 WITHOUT writing an "allowed" audit
        # entry for a command that never reached the game (audit ledger == dispatched).
        audited: list[tuple[str, str, int | None]] = []
        state = loopback.ServerState(
            self.key,
            enable_exec_enforce=True,
            exec_allowlist={"allowed"},
            exec_audit=lambda expr, verdict, _fn, cid: audited.append((expr, verdict, cid)),
        )
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(state)
        for _ in range(loopback.MAX_QUEUE):
            status, _ = state.enqueue_command("query_player_state", {}, peer="server")
            self.assertEqual(status, 200)

        status, body = state.enqueue_command("exec_enforce", {"expr": "allowed"}, peer="server")
        self.assertEqual(status, 429)
        self.assertEqual(body, {"error": "queue_full"})
        self.assertEqual([entry for entry in audited if entry[1] == "allowed"], [])


if __name__ == "__main__":
    unittest.main()
