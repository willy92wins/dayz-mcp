"""MCP tool-result helpers shared by many tests.

Moved verbatim from test_mcp_tools.py so tests stop importing each other
(review 2026-09-25, T1).
"""
from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.request
from typing import Any, Callable

from dayz_mcp.server import Runtime

from tests.fence_helpers import INST_CLIENT, INST_SERVER, poll_census_query


def _content_json(content: Any) -> dict[str, Any]:
    if isinstance(content, tuple):
        _blocks, structured = content
        if isinstance(structured, dict):
            return structured
        content = _blocks
    text = content[0].text
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise AssertionError(f"expected dict content, got {parsed!r}")
    return parsed


def _assert_tool_error(testcase: unittest.TestCase, exc: BaseException) -> None:
    testcase.assertEqual(type(exc).__name__, "ToolError")


class FakePeer:
    def __init__(
        self,
        runtime: Runtime,
        key: str,
        peer: str,
        version: str | None = None,
        result_delay_s: float = 0.0,
        responder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        if runtime.loopback is None or runtime.loopback.httpd is None:
            raise RuntimeError("runtime loopback not started")
        host, port = runtime.loopback.httpd.server_address
        self.base = f"http://{host}:{port}"
        self.key = key
        self.peer = peer
        self.version = version
        self.result_delay_s = result_delay_s
        self.responder = responder or self.default_responder
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.max_batch_size = 0
        self.commands_seen: list[dict[str, Any]] = []

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2.0)

    def default_responder(self, command: dict[str, Any]) -> dict[str, Any]:
        # Bridge serializes Enforce bool as int 0/1; mirror that here so the
        # ok-handling path is exercised against the real wire type, not Python bool.
        result: dict[str, Any] = {
            "id": command["id"],
            "ok": 1,
            "cmd": command["cmd"],
            "args": command.get("args", {}),
        }
        if command["cmd"] == "query_player_state":
            result["state"] = {"name": "fake", "pos": [1.0, 2.0, 3.0]}
        if command["cmd"] == "camera_get":
            result["camera"] = {"ok": 1, "viewport_moved": 1, "pos": [1.0, 2.0, 3.0]}
        if command["cmd"] == "camera_set":
            result["camera"] = {"ok": 1, "applied_mode": command.get("args", {}).get("cam_mode", "")}
        return result

    def request(self, method: str, path: str, payload: dict | None = None, query: dict | None = None) -> dict:
        params = dict(query or {})
        params["key"] = self.key
        url = self.base + path + "?" + urllib.parse.urlencode(params)
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))

    def run(self) -> None:
        while not self.stop_event.is_set():
            query = {"peer": self.peer}
            query["inst"] = INST_SERVER if self.peer == "server" else INST_CLIENT
            query.update(poll_census_query(self.peer))
            if self.version is not None:
                query["ver"] = self.version
            try:
                body = self.request("GET", "/poll", query=query)
                commands = body.get("commands", [])
                self.max_batch_size = max(self.max_batch_size, len(commands))
                for command in commands:
                    self.commands_seen.append(command)
                    if self.result_delay_s > 0.0:
                        time.sleep(self.result_delay_s)
                    result = self.responder(command)
                    self.request(
                        "POST",
                        "/result",
                        payload=result,
                        query={"inst": query["inst"]},
                    )
            except Exception:
                time.sleep(0.02)
            time.sleep(0.02)
