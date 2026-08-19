from __future__ import annotations

import json
import contextlib
import io
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import mcp_server


class MCPServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.key = "test-key"
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), mcp_server.Handler)
        self.httpd.state = mcp_server.ServerState(self.key)  # type: ignore[attr-defined]
        from tests.fence_helpers import INST_CLIENT, INST_SERVER, bind_both_peers

        bind_both_peers(self.httpd.state)
        self.inst_server = INST_SERVER
        self.inst_client = INST_CLIENT
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=2.0)
        self.httpd.server_close()

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        query: dict | None = None,
        include_key: bool = True,
    ) -> tuple[int, dict]:
        params = dict(query or {})
        if include_key:
            params["key"] = self.key
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                response = urllib.request.urlopen(req, timeout=2.0)
            with response:
                raw = response.read().decode("utf-8")
                return int(response.status), json.loads(raw)
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8")
                return int(exc.code), json.loads(raw)
            finally:
                exc.close()

    def test_new_commands_are_whitelisted_and_unknown_is_rejected(self) -> None:
        for cmd in ("world_spawn", "vehicle_enter", "vehicle_drive", "camera_set", "camera_get"):
            status, body = self.request("POST", "/enqueue", {"cmd": cmd, "args": {}})
            self.assertEqual(status, 200)
            self.assertIn("id", body)

        status, body = self.request("POST", "/enqueue", {"cmd": "evil", "args": {}})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "not_whitelisted"})

    def test_two_peer_routing_and_default_server_poll(self) -> None:
        status, server_body = self.request("POST", "/enqueue", {"cmd": "query_player_state", "args": {}})
        self.assertEqual(status, 200)
        status, client_body = self.request("POST", "/enqueue", {"cmd": "camera_set", "args": {"cam_mode": "orient"}})
        self.assertEqual(status, 200)

        status, body = self.request("GET", "/poll", query={"inst": self.inst_server})
        self.assertEqual(status, 200)
        self.assertEqual([command["id"] for command in body["commands"]], [server_body["id"]])

        status, body = self.request(
            "GET", "/poll", query={"peer": "client", "inst": self.inst_client}
        )
        self.assertEqual(status, 200)
        self.assertEqual([command["id"] for command in body["commands"]], [client_body["id"]])

        status, body = self.request(
            "GET", "/poll", query={"peer": "server", "inst": self.inst_server}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {"commands": []})

    def test_client_peer_fail_closed_auth_and_bad_peer(self) -> None:
        status, body = self.request("GET", "/poll", query={"peer": "client"}, include_key=False)
        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

        status, body = self.request("GET", "/poll", query={"peer": "invalid"})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_peer"})

        status, body = self.request("POST", "/enqueue", {"cmd": "camera_get", "args": {}, "peer": "server"})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_peer"})

    def test_queue_cap_returns_429(self) -> None:
        for _ in range(mcp_server.MAX_QUEUE):
            status, body = self.request("POST", "/enqueue", {"cmd": "query_player_state", "args": {}})
            self.assertEqual(status, 200)
            self.assertIn("id", body)

        status, body = self.request("POST", "/enqueue", {"cmd": "query_player_state", "args": {}})
        self.assertEqual(status, 429)
        self.assertEqual(body, {"error": "queue_full"})

    def test_args_must_be_object(self) -> None:
        status, body = self.request("POST", "/enqueue", {"cmd": "world_spawn", "args": ["not", "object"]})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_args"})


if __name__ == "__main__":
    unittest.main()
