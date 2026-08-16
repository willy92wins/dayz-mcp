from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import mcp_client


IDENTITY = {
    "platform": "codex",
    "pid": 41001,
    "ppid": 41000,
    "started_at_utc": "2026-07-16T20:00:00Z",
    "session_id": "task9-broker-transport",
    "task_label": "MercedesAMGLF Task 9",
}


class MCPClientSessionTransportTest(unittest.TestCase):
    def test_enqueue_transports_exact_identity_lease_and_operation_timeout(self) -> None:
        client = mcp_client.Client(
            port=8765,
            key="test-key",
            timeout_s=30.0,
            identity=IDENTITY,
            lease_token="test-lease-token",
        )
        with mock.patch.object(
            client, "request_json", return_value={"id": 73}
        ) as request_json:
            command_id = client.enqueue_cmd(
                "world_spawn",
                {"type": "ExampleCar"},
                "server",
                operation_timeout_s=45.0,
            )

        self.assertEqual(command_id, 73)
        request_json.assert_called_once_with(
            "POST",
            "/enqueue",
            {
                "cmd": "world_spawn",
                "args": {"type": "ExampleCar"},
                "peer": "server",
                "identity": IDENTITY,
                "lease_token": "test-lease-token",
                "operation_timeout_s": 45.0,
            },
        )

    def test_run_result_pins_the_lease_for_its_declared_timeout(self) -> None:
        client = mock.Mock()
        client.enqueue_cmd.return_value = 91
        client.await_result.return_value = {"ok": True}

        command_id, result = mcp_client.run_result(
            client,
            "object_delete",
            {"object_id": 5},
            timeout_s=12.5,
            peer="server",
        )

        self.assertEqual(command_id, 91)
        self.assertEqual(result, {"ok": True})
        client.enqueue_cmd.assert_called_once_with(
            "object_delete",
            {"object_id": 5},
            "server",
            operation_timeout_s=12.5,
        )
        client.await_result.assert_called_once_with(91, 12.5)

    def test_broker_await_consumes_result_and_owner_attribution(self) -> None:
        client = mcp_client.Client(
            port=8765,
            key="test-key",
            timeout_s=30.0,
            identity=IDENTITY,
            lease_token="test-lease-token",
        )
        with mock.patch.object(
            client,
            "request_json",
            return_value={"status": "done", "result": {"ok": True}},
        ) as request_json:
            result = client.await_result(85, timeout_s=5.0)

        self.assertEqual(result, {"ok": True})
        request_json.assert_called_once_with(
            "GET",
            "/await",
            query={"id": 85, "remove": 1},
        )


if __name__ == "__main__":
    unittest.main()
