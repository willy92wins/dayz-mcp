from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from dayz_mcp import loopback


class Fase4BLoopbackTest(unittest.TestCase):
    def start_server(self, enable_exec_enforce: bool = False) -> None:
        self.key = "test-key"
        state = loopback.ServerState(self.key, enable_exec_enforce=enable_exec_enforce)
        from tests.fence_helpers import bind_both_peers

        bind_both_peers(state)
        self.httpd = loopback.create_http_server(0, state, log_sink=lambda _message: None)
        self.state = state
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        if hasattr(self, "httpd"):
            self.httpd.shutdown()
            self.thread.join(timeout=2.0)
            self.httpd.server_close()

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

    def test_fase4b_whitelist_extends_world_commands_and_gates_exec(self) -> None:
        self.start_server(enable_exec_enforce=False)
        for cmd in ("world_time_set", "world_weather_set"):
            status, body = self.request("POST", "/enqueue", {"cmd": cmd, "args": {}})
            self.assertEqual(status, 200)
            self.assertIn("id", body)

        status, body = self.request("POST", "/enqueue", {"cmd": "exec_enforce", "args": {"expr": "x"}})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "not_whitelisted"})

        status, body = self.request("POST", "/enqueue", {"cmd": "evil", "args": {}})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "not_whitelisted"})

    def test_exec_whitelist_enabled_without_hooks_denies_loopback_enqueue(self) -> None:
        self.start_server(enable_exec_enforce=True)
        status, body = self.request("POST", "/enqueue", {"cmd": "exec_enforce", "args": {"expr": "x"}})
        self.assertEqual(status, 403)
        self.assertEqual(body, {"error": "exec_not_allowed"})

    def test_poll_version_hook_captures_fase4b_version(self) -> None:
        self.start_server()
        status, body = self.request("GET", "/poll", query={"peer": "server", "ver": "4~1.29.x"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"commands": []})
        snapshot = self.state.status_snapshot()
        self.assertEqual(snapshot["peers"]["server"]["version"], "4~1.29.x")


if __name__ == "__main__":
    unittest.main()
