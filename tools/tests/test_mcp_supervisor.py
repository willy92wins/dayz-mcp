from __future__ import annotations

import json
import queue
import sys
import threading
import time
import unittest
from pathlib import Path


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.mcp_supervisor import (  # noqa: E402
    HEARTBEAT_ID,
    RELOAD_TOOL_NAME,
    REPLAY_ID,
    Supervisor,
)


DEADLINE_S = 5.0


class _BlockingLines:
    """A stdout the pump can readline() on, fed by the test thread."""

    def __init__(self) -> None:
        self._queue: queue.Queue[bytes | None] = queue.Queue()

    def push(self, raw: bytes) -> None:
        self._queue.put(raw)

    def close(self) -> None:
        self._queue.put(None)

    def readline(self) -> bytes:
        item = self._queue.get()
        return b"" if item is None else item


class _CapturingStdin:
    def __init__(self, worker: "FakeWorker") -> None:
        self._worker = worker
        self.closed = False

    def write(self, raw: bytes) -> None:
        if self.closed:
            raise ValueError("write to closed stdin")
        self._worker.record(raw)

    def flush(self) -> None:
        return

    def close(self) -> None:
        self.closed = True
        self._worker.stdin_closed.set()


class FakeWorker:
    """A worker process double: records what it is sent, emits what the test says.

    auto_answer replies to the two requests the supervisor issues on its own behalf --
    the initialize replay and the lease heartbeat -- because a recycle cannot complete
    without them and every test that is not about them wants them out of the way.
    """

    def __init__(self, pid: int, *, auto_answer: bool = True, exit_on_stdin_close: bool = True) -> None:
        self.pid = pid
        self.stdout = _BlockingLines()
        self.stdin = _CapturingStdin(self)
        self.sent: list[dict] = []
        self.raw_sent: list[bytes] = []
        self.stdin_closed = threading.Event()
        self.killed = False
        self._auto_answer = auto_answer
        self._exit_on_stdin_close = exit_on_stdin_close
        self._lock = threading.Lock()

    # -- the WorkerProcess slice ---------------------------------------------
    def poll(self) -> int | None:
        return 0 if self.stdin_closed.is_set() and self._exit_on_stdin_close else None

    def wait(self, timeout: float | None = None) -> int:
        if self._exit_on_stdin_close and self.stdin_closed.wait(timeout or 0.0):
            self.stdout.close()
            return 0
        import subprocess

        raise subprocess.TimeoutExpired("worker", timeout or 0.0)

    def kill(self) -> None:
        self.killed = True
        self.stdin_closed.set()
        self.stdout.close()

    # -- test helpers ---------------------------------------------------------
    def record(self, raw: bytes) -> None:
        with self._lock:
            self.raw_sent.append(raw)
            try:
                message = json.loads(raw)
            except ValueError:
                return
            self.sent.append(message)
        if not self._auto_answer:
            return
        ident = message.get("id")
        if ident in (REPLAY_ID, HEARTBEAT_ID):
            self.answer(ident, {"ok": True})

    def answer(self, ident: object, result: object) -> None:
        self.stdout.push((json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}) + "\n").encode("utf-8"))

    def push_raw(self, raw: bytes) -> None:
        self.stdout.push(raw)

    def wait_for_sent(self, predicate, timeout: float = DEADLINE_S) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                for message in self.sent:
                    if predicate(message):
                        return message
            time.sleep(0.005)
        raise AssertionError(f"worker {self.pid} never received a matching message: {self.sent}")


class _HostStream:
    def __init__(self) -> None:
        self.lines: list[bytes] = []
        self._lock = threading.Lock()
        self._arrived = threading.Condition(self._lock)

    def write(self, raw: bytes) -> None:
        with self._arrived:
            self.lines.append(raw)
            self._arrived.notify_all()

    def flush(self) -> None:
        return

    def wait_for(self, predicate, timeout: float = DEADLINE_S) -> dict:
        deadline = time.monotonic() + timeout
        with self._arrived:
            while True:
                for raw in self.lines:
                    try:
                        message = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(message, dict) and predicate(message):
                        return message
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"host never saw a matching message: {self.lines}")
                self._arrived.wait(timeout=min(remaining, 0.1))

    def messages(self) -> list[dict]:
        out = []
        with self._lock:
            for raw in self.lines:
                try:
                    out.append(json.loads(raw))
                except ValueError:
                    continue
        return out


def reload_call(ident: object = 7) -> bytes:
    return (
        json.dumps(
            {"jsonrpc": "2.0", "id": ident, "method": "tools/call",
             "params": {"name": RELOAD_TOOL_NAME, "arguments": {}}}
        ) + "\n"
    ).encode("utf-8")


def result_text(message: dict) -> dict:
    content = message.get("result", {}).get("content", [])
    return json.loads(content[0]["text"]) if content else {}


class SupervisorTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.host = _HostStream()
        self.workers: list[FakeWorker] = []
        self.logs: list[str] = []
        self.auto_answer = True
        self.supervisor = Supervisor(
            spawn=self._spawn,
            out_stream=self.host,
            log=self.logs.append,
            drain_timeout_s=0.5,
            stdin_close_grace_s=0.5,
            replay_timeout_s=1.0,
            heartbeat_timeout_s=0.5,
        )

    def _spawn(self) -> FakeWorker:
        worker = FakeWorker(1000 + len(self.workers), auto_answer=self.auto_answer)
        self.workers.append(worker)
        return worker

    def start(self) -> FakeWorker:
        self.supervisor.start_worker()
        return self.workers[-1]

    def handshake(self) -> None:
        self.supervisor.handle_host_line(
            (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18"}}) + "\n").encode("utf-8")
        )
        self.workers[-1].answer(1, {"protocolVersion": "2025-06-18"})
        self.supervisor.handle_host_line(
            (json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode("utf-8")
        )


class ForwardingTest(SupervisorTestBase):
    def test_a_request_reaches_the_worker_and_its_answer_reaches_the_host(self) -> None:
        worker = self.start()
        self.supervisor.handle_host_line(
            b'{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"ping"}}\n'
        )
        worker.wait_for_sent(lambda m: m.get("id") == 5)
        worker.answer(5, {"content": [{"type": "text", "text": "pong"}]})
        self.host.wait_for(lambda m: m.get("id") == 5)

    def test_response_bytes_are_relayed_verbatim_not_re_serialised(self) -> None:
        # A round-trip through json.dumps would normalise spacing and key order. The
        # host must receive what the worker produced, byte for byte.
        worker = self.start()
        self.supervisor.handle_host_line(b'{"jsonrpc":"2.0","id":9,"method":"tools/call"}\n')
        odd = b'{"id": 9, "jsonrpc":   "2.0", "result": {"zeta": 1, "alpha": 2}}\n'
        worker.push_raw(odd)
        self.host.wait_for(lambda m: m.get("id") == 9)
        self.assertIn(odd, self.host.lines)

    def test_a_line_that_is_not_json_is_passed_through_untouched(self) -> None:
        worker = self.start()
        self.supervisor.handle_host_line(b"not json at all\n")
        self.assertIn(b"not json at all\n", worker.raw_sent)

    def test_tools_list_gains_server_reload(self) -> None:
        worker = self.start()
        self.supervisor.handle_host_line(b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n')
        worker.answer(2, {"tools": [{"name": "world_spawn"}]})
        message = self.host.wait_for(lambda m: m.get("id") == 2)
        names = [tool["name"] for tool in message["result"]["tools"]]
        self.assertEqual(names, ["world_spawn", RELOAD_TOOL_NAME])

    def test_tools_list_that_already_has_it_is_not_duplicated(self) -> None:
        worker = self.start()
        self.supervisor.handle_host_line(b'{"jsonrpc":"2.0","id":3,"method":"tools/list"}\n')
        worker.answer(3, {"tools": [{"name": RELOAD_TOOL_NAME}]})
        message = self.host.wait_for(lambda m: m.get("id") == 3)
        names = [tool["name"] for tool in message["result"]["tools"]]
        self.assertEqual(names, [RELOAD_TOOL_NAME])


class ReservedIdTest(SupervisorTestBase):
    def test_the_supervisors_own_responses_never_reach_the_host(self) -> None:
        # The spike hit this: the client logs a response it has no pending request for.
        worker = self.start()
        worker.answer(REPLAY_ID, {"ok": True})
        worker.answer(4, {"visible": True})
        self.host.wait_for(lambda m: m.get("id") == 4)
        self.assertFalse(any(m.get("id") == REPLAY_ID for m in self.host.messages()))

    def test_a_host_that_uses_a_reserved_id_is_refused_not_silently_shared(self) -> None:
        worker = self.start()
        self.supervisor.handle_host_line(
            (json.dumps({"jsonrpc": "2.0", "id": REPLAY_ID, "method": "tools/list"}) + "\n").encode("utf-8")
        )
        message = self.host.wait_for(lambda m: m.get("id") == REPLAY_ID)
        self.assertTrue(message["result"]["isError"])
        self.assertEqual(result_text(message)["error"], "reserved_request_id")
        self.assertFalse(any(m.get("id") == REPLAY_ID for m in worker.sent))


class RecycleTest(SupervisorTestBase):
    def test_a_clean_recycle_replaces_the_worker_and_announces_the_catalogue(self) -> None:
        first = self.start()
        self.handshake()
        self.supervisor.handle_host_line(reload_call(7))
        message = self.host.wait_for(lambda m: m.get("id") == 7)
        payload = result_text(message)

        self.assertFalse(message["result"]["isError"])
        self.assertEqual(payload["status"], "recycled")
        self.assertEqual(payload["generation"], 2)
        self.assertEqual(len(self.workers), 2)
        self.assertNotEqual(payload["launcher_pid"], first.pid)
        self.host.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed")

    def test_the_replacement_is_brought_to_the_state_the_host_believes_in(self) -> None:
        self.start()
        self.handshake()
        self.supervisor.handle_host_line(reload_call(8))
        self.host.wait_for(lambda m: m.get("id") == 8)
        replacement = self.workers[-1]
        replay = replacement.wait_for_sent(lambda m: m.get("method") == "initialize")
        self.assertEqual(replay["id"], REPLAY_ID)
        replacement.wait_for_sent(lambda m: m.get("method") == "notifications/initialized")

    def test_the_lease_is_refreshed_before_the_outgoing_worker_is_retired(self) -> None:
        first = self.start()
        self.handshake()
        self.supervisor.handle_host_line(reload_call(9))
        message = self.host.wait_for(lambda m: m.get("id") == 9)
        beat = first.wait_for_sent(
            lambda m: m.get("params", {}).get("name") == "session_heartbeat"
        )
        self.assertEqual(beat["id"], HEARTBEAT_ID)
        self.assertEqual(result_text(message)["lease_heartbeat"], "answered")
        # And it went to the worker that still held the lease, not the replacement.
        self.assertFalse(
            any(m.get("params", {}).get("name") == "session_heartbeat"
                for m in self.workers[-1].sent)
        )

    def test_a_worker_holding_no_lease_does_not_abort_the_recycle(self) -> None:
        self.auto_answer = False
        first = self.start()
        self.handshake()
        self.supervisor.handle_host_line(reload_call(10))
        first.wait_for_sent(lambda m: m.get("id") == HEARTBEAT_ID)
        # No answer: the heartbeat times out, which is not a reason to keep stale code.
        # The replacement still has to be brought up, so answer its replay by hand.
        deadline = time.monotonic() + DEADLINE_S
        while len(self.workers) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.workers), 2)
        replacement = self.workers[-1]
        replacement.wait_for_sent(lambda m: m.get("id") == REPLAY_ID)
        replacement.answer(REPLAY_ID, {"ok": True})
        message = self.host.wait_for(lambda m: m.get("id") == 10)
        self.assertEqual(result_text(message)["lease_heartbeat"], "timeout")
        self.assertEqual(result_text(message)["status"], "recycled")

    def test_a_call_in_flight_refuses_the_recycle_rather_than_orphaning_it(self) -> None:
        worker = self.start()
        self.handshake()
        self.supervisor.handle_host_line(
            b'{"jsonrpc":"2.0","id":50,"method":"tools/call","params":{"name":"slow"}}\n'
        )
        worker.wait_for_sent(lambda m: m.get("id") == 50)
        self.supervisor.handle_host_line(reload_call(11))
        message = self.host.wait_for(lambda m: m.get("id") == 11)
        self.assertTrue(message["result"]["isError"])
        payload = result_text(message)
        self.assertEqual(payload["error"], "drain_timeout")
        self.assertEqual(payload["still_in_flight"], ["50"])
        # And the worker is still the one serving: nothing was killed.
        self.assertEqual(len(self.workers), 1)
        self.assertFalse(worker.killed)

    def test_a_call_that_answers_during_the_drain_lets_the_recycle_through(self) -> None:
        worker = self.start()
        self.handshake()
        self.supervisor.handle_host_line(
            b'{"jsonrpc":"2.0","id":51,"method":"tools/call","params":{"name":"slow"}}\n'
        )
        worker.wait_for_sent(lambda m: m.get("id") == 51)

        def answer_soon() -> None:
            time.sleep(0.05)
            worker.answer(51, {"done": True})

        threading.Thread(target=answer_soon, daemon=True).start()
        self.supervisor.handle_host_line(reload_call(12))
        message = self.host.wait_for(lambda m: m.get("id") == 12)
        self.assertFalse(message["result"]["isError"])
        self.assertEqual(result_text(message)["status"], "recycled")

    def test_requests_arriving_mid_drain_are_refused_not_queued_into_a_dying_worker(self) -> None:
        worker = self.start()
        self.handshake()
        self.supervisor.handle_host_line(
            b'{"jsonrpc":"2.0","id":60,"method":"tools/call","params":{"name":"slow"}}\n'
        )
        worker.wait_for_sent(lambda m: m.get("id") == 60)
        threading.Thread(
            target=self.supervisor.handle_host_line, args=(reload_call(13),), daemon=True
        ).start()
        deadline = time.monotonic() + DEADLINE_S
        while time.monotonic() < deadline:
            self.supervisor.handle_host_line(
                b'{"jsonrpc":"2.0","id":61,"method":"tools/call","params":{"name":"other"}}\n'
            )
            refused = [m for m in self.host.messages() if m.get("id") == 61]
            if refused:
                self.assertEqual(result_text(refused[0])["error"], "server_recycling")
                return
            time.sleep(0.01)
        self.fail("a request during the drain was never refused")

    def test_two_recycles_at_once_do_not_both_run(self) -> None:
        worker = self.start()
        self.handshake()
        self.supervisor.handle_host_line(
            b'{"jsonrpc":"2.0","id":70,"method":"tools/call","params":{"name":"slow"}}\n'
        )
        worker.wait_for_sent(lambda m: m.get("id") == 70)
        threading.Thread(
            target=self.supervisor.handle_host_line, args=(reload_call(14),), daemon=True
        ).start()
        deadline = time.monotonic() + DEADLINE_S
        while time.monotonic() < deadline:
            self.supervisor.handle_host_line(reload_call(15))
            second = [m for m in self.host.messages() if m.get("id") == 15]
            if second and result_text(second[0]).get("error") == "recycle_already_running":
                return
            time.sleep(0.01)
        self.fail("a concurrent recycle was never refused")

    def test_a_recycle_without_a_recorded_handshake_refuses_and_says_so(self) -> None:
        self.start()
        self.supervisor.handle_host_line(reload_call(16))
        message = self.host.wait_for(lambda m: m.get("id") == 16)
        self.assertTrue(message["result"]["isError"])
        self.assertEqual(result_text(message)["error"], "initialize_replay_failed")


class RetirementTest(SupervisorTestBase):
    def test_a_worker_that_ignores_stdin_close_is_killed_by_tree(self) -> None:
        self.auto_answer = True
        stubborn = FakeWorker(9999, exit_on_stdin_close=False)
        self.workers.append(stubborn)
        self.supervisor._spawn = lambda: stubborn  # type: ignore[method-assign]
        self.supervisor.start_worker()
        self.handshake()

        killed: list[int] = []
        import dayz_mcp.mcp_supervisor as module

        original = module.terminate_tree
        module.terminate_tree = lambda process, log: (killed.append(process.pid), stubborn.kill())
        try:
            self.supervisor._spawn = self._spawn  # type: ignore[method-assign]
            self.supervisor.handle_host_line(reload_call(17))
            self.host.wait_for(lambda m: m.get("id") == 17)
        finally:
            module.terminate_tree = original
        self.assertEqual(killed, [9999])
        self.assertTrue(any("killing tree" in line for line in self.logs))


if __name__ == "__main__":
    unittest.main()
