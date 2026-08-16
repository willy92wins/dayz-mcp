from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import dayz_test_readiness, native_broker_protocol


RUN_ID = "12345678-1234-4234-8234-1234567890ab"


class _Broker:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.frames: list[native_broker_protocol.BrokerRequest] = []

    async def invoke(self, frame: bytes) -> object:
        decoded = native_broker_protocol.decode_request(frame)
        self.frames.append(decoded)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class _Psutil:
    def __init__(
        self,
        connections: list[object],
        *,
        pid_results: list[object] | None = None,
    ) -> None:
        self.connections = connections
        self.pid_results = pid_results or [True] * max(1, len(connections))

    def pid_exists(self, _pid: int) -> bool:
        result = self.pid_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return bool(result)

    def net_connections(self, *, kind: str) -> object:
        assert kind == "udp"
        result = self.connections.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _status(pid: object = 500, *, state: str = "RUNNING") -> dict[str, object]:
    return {
        "runs": [
            {
                "run_id": RUN_ID,
                "state": state,
                "processes": [{"role": "server", "pid": pid}],
            }
        ],
    }


class DayzTestReadinessTests(unittest.TestCase):
    def _run(
        self,
        responses: list[object],
        connections: list[object],
        *,
        pid_results: list[object] | None = None,
        monotonic_override=None,
        sleep_override=None,
    ) -> tuple[dayz_test_readiness.ReadinessResult, _Broker]:
        broker = _Broker(responses)
        now = [0.0]

        async def sleep(seconds: float) -> None:
            now[0] += seconds

        result = asyncio.run(
            dayz_test_readiness.wait_for_owned_udp(
                broker,
                RUN_ID,
                2302,
                5,
                psutil_module=_Psutil(
                    connections,
                    pid_results=pid_results,
                ),
                monotonic=monotonic_override or (lambda: now[0]),
                sleep=sleep_override or sleep,
            )
        )
        return result, broker

    def test_waits_then_accepts_only_exact_server_pid_binding(self) -> None:
        endpoint = lambda pid: SimpleNamespace(laddr=SimpleNamespace(port=2302), pid=pid)
        result, broker = self._run(
            [_status(), _status()],
            [[], [endpoint(500)]],
        )
        self.assertEqual(
            result,
            dayz_test_readiness.ReadinessResult(ready=True, error_code=None),
        )
        self.assertEqual(len(broker.frames), 2)
        self.assertEqual(
            [item.payload for item in broker.frames],
            [
                {
                    "command": "status",
                    "launch_operation_id": None,
                    "run_id": RUN_ID,
                },
                {
                    "command": "status",
                    "launch_operation_id": None,
                    "run_id": RUN_ID,
                },
            ],
        )

    def test_foreign_binding_and_dead_exact_pid_have_distinct_codes(self) -> None:
        endpoint = lambda pid: SimpleNamespace(laddr=("0.0.0.0", 2302), pid=pid)
        foreign, _ = self._run([_status()], [[endpoint(999), endpoint(500)]])
        dead, _ = self._run(
            [_status()],
            [[endpoint(500)]],
            pid_results=[False],
        )
        self.assertEqual(foreign.error_code, "readiness_udp_foreign_owner")
        self.assertEqual(dead.error_code, "readiness_server_pid_dead")

    def test_transient_unknown_owner_waits_for_unambiguous_exact_binding(self) -> None:
        endpoint = lambda pid: SimpleNamespace(laddr=("0.0.0.0", 2302), pid=pid)
        ready, broker = self._run(
            [_status(), _status()],
            [[endpoint(None), endpoint(500)], [endpoint(500)]],
        )
        self.assertTrue(ready.ready)
        self.assertIsNone(ready.error_code)
        self.assertEqual(len(broker.frames), 2)

        foreign, _ = self._run(
            [_status(), _status()],
            [[endpoint(None)], [endpoint(999)]],
        )
        self.assertEqual(foreign.error_code, "readiness_udp_foreign_owner")

    def test_malformed_status_and_nonrunning_run_have_distinct_codes(self) -> None:
        invalid = (
            (None, "readiness_status_invalid"),
            ({}, "readiness_status_invalid"),
            ({"runs": None}, "readiness_status_invalid"),
            ({"runs": []}, "readiness_run_not_running"),
            (
                {"error": "broker_child_failed", "runs": []},
                "readiness_status_invalid",
            ),
            ({"ok": False, "runs": []}, "readiness_status_invalid"),
            (
                {"runs": [_status()["runs"][0], _status()["runs"][0]]},
                "readiness_run_not_running",
            ),
            (_status(state="STARTING"), "readiness_run_not_running"),
        )
        for response, code in invalid:
            with self.subTest(response=response, code=code):
                result, _ = self._run([response], [[]])
                self.assertEqual(result.error_code, code)

    def test_malformed_server_process_and_udp_snapshot_have_distinct_codes(self) -> None:
        invalid_processes = (
            {"runs": [{"run_id": RUN_ID, "state": "RUNNING"}]},
            {
                "runs": [
                    {
                        "run_id": RUN_ID,
                        "state": "RUNNING",
                        "processes": None,
                    }
                ]
            },
            {"runs": [{"run_id": RUN_ID, "state": "RUNNING", "processes": []}]},
            {
                "runs": [
                    {
                        "run_id": RUN_ID,
                        "state": "RUNNING",
                        "processes": [
                            {"role": "server", "pid": 500},
                            {"role": "server", "pid": 501},
                        ],
                    }
                ]
            },
            _status(pid=True),
            _status(pid=0),
        )
        for response in invalid_processes:
            with self.subTest(response=response):
                result, _ = self._run([response], [[]])
                self.assertEqual(
                    result.error_code,
                    "readiness_server_process_invalid",
                )

        malformed_snapshot, _ = self._run([_status()], [None])
        self.assertEqual(
            malformed_snapshot.error_code,
            "readiness_udp_snapshot_invalid",
        )

    def test_timeout_is_bounded_and_input_is_strict(self) -> None:
        endpoint = SimpleNamespace(laddr=("0.0.0.0", 2302), pid=None)
        for connections in ([[], [], []], [[endpoint], [endpoint], [endpoint]]):
            with self.subTest(connections=connections):
                result, broker = self._run(
                    [_status(), _status(), _status()],
                    connections,
                )
                self.assertEqual(result.error_code, "readiness_timeout")
                self.assertEqual(len(broker.frames), 3)
        self.assertGreaterEqual(len(broker.frames), 3)

        async def invalid() -> None:
            with self.assertRaisesRegex(
                ValueError,
                "invalid_dayz_test_readiness",
            ):
                await dayz_test_readiness.wait_for_owned_udp(
                    _Broker([]), RUN_ID, True, 5, psutil_module=_Psutil([])
                )

        asyncio.run(invalid())

    def test_probe_exceptions_collapse_without_exception_text(self) -> None:
        secret = "secret probe detail"

        def raise_secret() -> float:
            raise RuntimeError(secret)

        monotonic_calls = iter((0.0, RuntimeError(secret)))

        def raise_after_deadline() -> float:
            value = next(monotonic_calls)
            if isinstance(value, BaseException):
                raise value
            return value

        async def failed_sleep(_seconds: float) -> None:
            raise RuntimeError(secret)

        cases = (
            ("initial_monotonic", lambda: self._run([_status()], [[]], monotonic_override=raise_secret)),
            ("loop_monotonic", lambda: self._run([_status()], [[]], monotonic_override=raise_after_deadline)),
            ("broker", lambda: self._run([RuntimeError(secret)], [[]])),
            (
                "pid_exists",
                lambda: self._run(
                    [_status()],
                    [[]],
                    pid_results=[RuntimeError(secret)],
                ),
            ),
            (
                "net_connections",
                lambda: self._run(
                    [_status()],
                    [RuntimeError(secret)],
                ),
            ),
            (
                "sleep",
                lambda: self._run(
                    [_status()],
                    [[]],
                    sleep_override=failed_sleep,
                ),
            ),
        )
        for label, invoke in cases:
            with self.subTest(label=label):
                result, _ = invoke()
                self.assertEqual(result.error_code, "readiness_probe_failed")
                self.assertNotIn(secret, repr(result))

        with patch.object(
            native_broker_protocol,
            "encode_request",
            side_effect=RuntimeError(secret),
        ):
            encoded, _ = self._run([_status()], [[]])
        self.assertEqual(encoded.error_code, "readiness_probe_failed")
        self.assertNotIn(secret, repr(encoded))

    def test_cancelled_broker_and_sleep_propagate_unchanged(self) -> None:
        async def cancelled_sleep(_seconds: float) -> None:
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            self._run([asyncio.CancelledError()], [[]])
        with self.assertRaises(asyncio.CancelledError):
            self._run(
                [_status()],
                [[]],
                sleep_override=cancelled_sleep,
            )

    def test_readiness_result_rejects_invalid_state_combinations(self) -> None:
        invalid = (
            (1, None),
            (None, None),
            (False, None),
            (True, "readiness_timeout"),
            (False, "unknown"),
            (False, 1),
        )
        for ready, error_code in invalid:
            with self.subTest(ready=ready, error_code=error_code):
                with self.assertRaisesRegex(
                    ValueError,
                    "invalid_readiness_result",
                ):
                    dayz_test_readiness.ReadinessResult(
                        ready=ready,
                        error_code=error_code,
                    )

    def test_readiness_error_code_set_is_exact(self) -> None:
        self.assertEqual(
            dayz_test_readiness.READINESS_ERROR_CODES,
            frozenset(
                {
                    "readiness_status_invalid",
                    "readiness_run_not_running",
                    "readiness_server_process_invalid",
                    "readiness_server_pid_dead",
                    "readiness_udp_snapshot_invalid",
                    "readiness_udp_foreign_owner",
                    "readiness_probe_failed",
                    "readiness_timeout",
                }
            ),
        )


if __name__ == "__main__":
    unittest.main()
