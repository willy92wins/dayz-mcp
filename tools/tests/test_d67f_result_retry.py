"""d67f: the daemon answers a retried result POST it already has without acting on it.

The server bridge sends a failed POST /result once more with ``retry=1``. When
the first one did reach the daemon, the retry is a copy: it must not store the
row again, finish the owner's operation again, or take the discard path that
pops a stored row once the deadline has passed. A retry whose first POST never
arrived is stored like any first result.
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.parse
import urllib.request
from unittest import mock

from dayz_mcp import loopback
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from tests.fence_helpers import (
    INST_SERVER,
    PID_SERVER,
    bind_both_peers,
    poll_census_query,
)
from tests.lease_helpers import SequentialIds

IDENTITY = {
    "platform": "codex",
    "pid": 4242,
    "ppid": 10,
    "started_at_utc": "2026-07-14T20:00:01Z",
    "session_id": "session-d67f",
    "task_label": "d67f",
}
TIMEOUT_S = 30.0


class RetriedResultStoreTest(unittest.TestCase):
    """One leased world_spawn, delivered to the server peer, per test."""

    def setUp(self) -> None:
        self.now = 100.0
        self.state = loopback.ServerState("test-key", time_fn=lambda: self.now)
        bind_both_peers(self.state)
        coordinator = SessionCoordinator(
            token_fn=SequentialIds("token"),
            id_fn=SequentialIds("lease"),
            audit=lambda _event: True,
            cleanup=self.state.cleanup_owner,
        )
        self.state.coordination = coordinator
        self.state.retail_probe = lambda: {"known": True, "processes": []}
        self._poll()
        status, active = coordinator.acquire(
            ClientIdentity.from_payload(IDENTITY), "d67f"
        )
        self.assertEqual(status, 200, active)
        status, body = self.state.enqueue_command(
            "world_spawn",
            {"type": "Apple", "pos": [1.0, 2.0, 3.0], "flags": 0, "rotation": 0},
            peer="server",
            identity_payload=IDENTITY,
            lease_token=active["lease_token"],
            operation_timeout_s=TIMEOUT_S,
        )
        self.assertEqual(status, 200, body)
        self.command_id = int(body["id"])
        _status, poll = self._poll()
        self.assertEqual([item["id"] for item in poll["commands"]], [self.command_id])
        finish = mock.patch.object(
            coordinator,
            "finish_operation_exact",
            wraps=coordinator.finish_operation_exact,
        )
        self.finish = finish.start()
        self.addCleanup(finish.stop)

    def _poll(self):
        query = poll_census_query("server")
        return self.state.record_poll(
            "server",
            "12~1.29",
            instance=INST_SERVER,
            source_pid=PID_SERVER,
            caps=query["caps"],
            ach=query["ach"],
        )

    def _post(self, *, retry: bool = False, spawned: int = 1) -> tuple[int, dict]:
        # A first POST passes no retry, as every POST did before d67f.
        extra = {"retry": True} if retry else {}
        return self.state.store_result(
            {"id": self.command_id, "ok": 1, "spawned": spawned},
            instance=INST_SERVER,
            **extra,
        )

    def duplicate(self) -> tuple[int, dict]:
        return 200, {
            "ok": True,
            "id": self.command_id,
            "ok_value": None,
            "duplicate": True,
        }

    def test_a_retry_of_a_stored_result_changes_nothing(self) -> None:
        self.assertEqual(
            self._post(), (200, {"ok": True, "id": self.command_id, "ok_value": 1})
        )
        row = self.state._results[self.command_id]
        self.now += 1.0
        self.assertEqual(self._post(retry=True, spawned=0), self.duplicate())
        self.assertIs(self.state._results[self.command_id], row)
        self.assertEqual(self.finish.call_count, 1)
        self.assertEqual(self.state.take_result(self.command_id, remove=True)["spawned"], 1)

    def test_a_retry_after_the_deadline_keeps_the_stored_result(self) -> None:
        self._post()
        self.now += TIMEOUT_S + 1.0
        self.assertEqual(self._post(retry=True), self.duplicate())
        self.assertEqual(self.state.take_result(self.command_id, remove=True)["spawned"], 1)

    def test_a_retry_of_a_consumed_result_is_not_discarded_again(self) -> None:
        self._post()
        self.assertIsNotNone(self.state.take_result(self.command_id, remove=True))
        finished = self.finish.call_count
        self.assertEqual(self._post(retry=True), self.duplicate())
        self.assertEqual(self.finish.call_count, finished)
        self.assertIsNone(self.state.take_result(self.command_id))

    def test_a_second_post_without_retry_is_handled_as_before(self) -> None:
        self._post()
        row = self.state._results[self.command_id]
        self.assertEqual(
            self._post(spawned=0),
            (200, {"ok": True, "id": self.command_id, "ok_value": 1}),
        )
        self.assertIsNot(self.state._results[self.command_id], row)
        self.assertEqual(self.finish.call_count, 2)
        self.now += TIMEOUT_S + 1.0
        status, body = self._post()
        self.assertEqual((status, body.get("discarded")), (200, True))
        self.assertIsNone(self.state.take_result(self.command_id))


class RetriedResultHandlerTest(unittest.TestCase):
    """POST /result reads retry=1 from the query, as the bridge sends it."""

    def setUp(self) -> None:
        self.key = "test-key"
        self.state = loopback.ServerState(self.key)
        bind_both_peers(self.state)
        self.log: list[str] = []
        self.httpd = loopback.create_http_server(0, self.state, log_sink=self.log.append)
        thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        self.addCleanup(thread.join, 2.0)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def request(self, method: str, path: str, payload: dict | None = None, **query: str) -> dict:
        query["key"] = self.key
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path + "?" + urllib.parse.urlencode(query),
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=2.0) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read().decode("utf-8"))

    def delivered(self) -> int:
        command_id = self.request("POST", "/enqueue", {"cmd": "query_player_state", "args": {}})["id"]
        poll = self.request("GET", "/poll", inst=INST_SERVER)
        self.assertEqual([item["id"] for item in poll["commands"]], [command_id])
        return command_id

    def result_lines(self) -> list[str]:
        return [line for line in self.log if line.startswith("RESULT ")]

    def test_the_query_retry_reaches_the_store(self) -> None:
        first = self.delivered()
        body = {"id": first, "ok": 1}
        self.assertEqual(self.request("POST", "/result", body, inst=INST_SERVER), {"ok": True})
        self.assertEqual(
            self.request("POST", "/result", body, inst=INST_SERVER, retry="1"),
            {"ok": True, "duplicate": True},
        )
        second = self.delivered()
        self.request("POST", "/result", {"id": second, "ok": 1}, inst=INST_SERVER, retry="1")
        self.assertEqual(
            self.result_lines(),
            [
                f"RESULT id={first} ok=1",
                f"RESULT id={first} ok=None retry=1 duplicate=1",
                f"RESULT id={second} ok=1 retry=1",
            ],
        )

    def test_a_retry_whose_first_post_never_arrived_is_stored(self) -> None:
        command_id = self.delivered()
        self.assertEqual(
            self.request("POST", "/result", {"id": command_id, "ok": 1}, inst=INST_SERVER, retry="1"),
            {"ok": True},
        )
        done = self.request("GET", "/await", id=str(command_id))
        self.assertEqual((done["status"], done["result"]["ok"]), ("done", 1))


if __name__ == "__main__":
    unittest.main()
