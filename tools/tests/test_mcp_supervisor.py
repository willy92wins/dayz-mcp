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
from tests._tiers import slow_test


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

    @slow_test
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

    @slow_test
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

def _broken_write(raw: bytes) -> None:
    raise OSError("pipe broken")


class _DyingAtInitializedWorker(FakeWorker):
    """A replacement whose stdout EOFs the moment the replay bring-up writes the
    notifications/initialized line. The write itself still succeeds."""

    def __init__(
        self, pid: int, *, death_pending: threading.Event, auto_answer: bool = True
    ) -> None:
        super().__init__(pid, auto_answer=auto_answer)
        self._death_pending = death_pending

    def record(self, raw: bytes) -> None:
        super().record(raw)
        try:
            message = json.loads(raw)
        except ValueError:
            return
        if isinstance(message, dict) and message.get("method") == "notifications/initialized":
            self._death_pending.set()
            self.stdout.close()


class _FlushWaitsForDeathStdin:
    """A stdin whose flush blocks until the transport death was marked, then returns
    normally -- but only for the write whose record closed the stdout (record runs
    before flush on the same thread, so the flag is settled by then). The bytes were
    accepted, so _send_worker has no exception to report: the death only surfaces on
    the stdout side, which is the write/EOF race."""

    def __init__(
        self, inner: _CapturingStdin, death_pending: threading.Event,
        death_marked: threading.Event,
    ) -> None:
        self._inner = inner
        self._death_pending = death_pending
        self._death_marked = death_marked

    def write(self, raw: bytes) -> None:
        self._inner.write(raw)

    def flush(self) -> None:
        if self._death_pending.is_set():
            self._death_marked.wait(timeout=DEADLINE_S)

    def close(self) -> None:
        self._inner.close()


class WorkerDeathTest(SupervisorTestBase):
    """A worker whose transport died must not swallow what the host asked it.

    stdout can EOF while the launcher lives on and stdin still accepts writes; a
    stdin write can fail while responses are still in flight. Both used to lose the
    requests: admission kept forwarding into the dead transport, and nothing was
    ever answered.
    """

    def _wait_for_log(self, fragment: str) -> None:
        deadline = time.monotonic() + DEADLINE_S
        while time.monotonic() < deadline:
            if any(fragment in line for line in self.logs):
                return
            time.sleep(0.005)
        self.fail(f"log never named {fragment!r}: {self.logs}")

    def _call(self, ident: object) -> bytes:
        return (
            json.dumps({"jsonrpc": "2.0", "id": ident, "method": "tools/call",
                        "params": {"name": "ping"}}) + "\n"
        ).encode("utf-8")

    def test_stdout_eof_answers_a_pending_call_with_worker_died(self) -> None:
        # The ticket's reproduction: poll() is None and stdin is writable, yet the
        # transport can never answer. The call must get a terminal answer anyway.
        worker = self.start()
        self.supervisor.handle_host_line(self._call(5))
        worker.wait_for_sent(lambda m: m.get("id") == 5)
        worker.stdout.close()
        message = self.host.wait_for(lambda m: m.get("id") == 5)
        self.assertTrue(message["result"]["isError"])
        payload = result_text(message)
        self.assertEqual(payload["error"], "worker_died")
        self.assertEqual(payload["generation"], 1)
        self.assertEqual(payload["reason"], "stdout_eof")
        self.assertEqual(payload["completion"], "unknown")

    def test_admission_after_eof_is_refused_not_forwarded_into_the_dead_pipe(self) -> None:
        worker = self.start()
        worker.stdout.close()
        self._wait_for_log("transport failed")
        self.supervisor.handle_host_line(self._call(6))
        message = self.host.wait_for(lambda m: m.get("id") == 6)
        self.assertTrue(message["result"]["isError"])
        self.assertEqual(result_text(message)["error"], "worker_died")
        # The launcher still accepts writes; the refusal must not have been
        # forwarded anyway, into a transport that can never answer it.
        self.assertFalse(any(m.get("id") == 6 for m in worker.sent))
        # And a notification is dropped rather than fed to the dead pipe too.
        self.supervisor.handle_host_line(
            b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":6}}\n'
        )
        self.assertEqual(len(worker.sent), 0)

    def test_eof_then_reload_replaces_the_dead_generation(self) -> None:
        # The ticket's executable scenario end to end: a call in flight when stdout
        # dies, one admitted after, then server_reload. Both calls are answered and
        # the reload brings up a replacement instead of reporting drain_timeout.
        first = self.start()
        self.handshake()
        self.supervisor.handle_host_line(self._call(5))
        first.wait_for_sent(lambda m: m.get("id") == 5)
        first.stdout.close()
        died = self.host.wait_for(lambda m: m.get("id") == 5)
        self.assertEqual(result_text(died)["error"], "worker_died")
        self.supervisor.handle_host_line(self._call(6))
        late = self.host.wait_for(lambda m: m.get("id") == 6)
        self.assertEqual(result_text(late)["error"], "worker_died")
        self.assertEqual(result_text(late)["generation"], 1)

        self.supervisor.handle_host_line(reload_call(7))
        message = self.host.wait_for(lambda m: m.get("id") == 7)
        self.assertFalse(message["result"]["isError"])
        payload = result_text(message)
        self.assertEqual(payload["status"], "recycled")
        self.assertEqual(payload["generation"], 2)
        replacement = self.workers[-1]
        replay = replacement.wait_for_sent(lambda m: m.get("method") == "initialize")
        self.assertEqual(replay["id"], REPLAY_ID)

    def test_a_failed_write_answers_the_request_and_marks_the_generation_dead(self) -> None:
        worker = self.start()
        worker.stdin.write = _broken_write
        self.supervisor.handle_host_line(self._call(5))
        message = self.host.wait_for(lambda m: m.get("id") == 5)
        self.assertTrue(message["result"]["isError"])
        payload = result_text(message)
        self.assertEqual(payload["error"], "worker_died")
        self.assertEqual(payload["reason"], "write_failed")
        self.assertEqual(payload["completion"], "unknown")
        # The generation stays refused: no further write is even attempted.
        worker.stdin.write = _CapturingStdin(worker).write
        self.supervisor.handle_host_line(self._call(6))
        late = self.host.wait_for(lambda m: m.get("id") == 6)
        self.assertEqual(result_text(late)["error"], "worker_died")
        self.assertFalse(any(m.get("id") == 6 for m in worker.sent))

    def test_a_response_racing_the_failure_never_doubles_the_terminal_answer(self) -> None:
        worker = self.start()
        self.supervisor.handle_host_line(self._call(5))
        worker.wait_for_sent(lambda m: m.get("id") == 5)
        # The worker produced its answer, but the pump has not necessarily relayed
        # it when the transport fails on the write side. Whichever racer wins, the
        # host must end up with exactly one terminal response per request.
        worker.answer(5, {"done": True})
        worker.stdin.write = _broken_write
        self.supervisor.handle_host_line(self._call(6))
        message = self.host.wait_for(lambda m: m.get("id") == 6)
        self.assertTrue(message["result"]["isError"])
        fives = [m for m in self.host.messages() if m.get("id") == 5]
        self.assertEqual(len(fives), 1)
        if not fives[0]["result"]["isError"]:
            # the real answer won the race
            self.assertEqual(fives[0]["result"]["content"][0]["text"], json.dumps({"done": True}))
        else:
            self.assertEqual(result_text(fives[0])["error"], "worker_died")

    def test_a_non_tool_request_gets_a_json_rpc_error_when_the_worker_dies(self) -> None:
        worker = self.start()
        self.supervisor.handle_host_line(b'{"jsonrpc":"2.0","id":3,"method":"tools/list"}\n')
        worker.wait_for_sent(lambda m: m.get("id") == 3)
        worker.stdout.close()
        message = self.host.wait_for(lambda m: m.get("id") == 3)
        # A tools/call result would not be a valid answer to tools/list.
        self.assertIsNone(message.get("result"))
        self.assertEqual(message["error"]["code"], -32603)
        data = message["error"]["data"]
        self.assertEqual(data["error"], "worker_died")
        self.assertEqual(data["generation"], 1)
        self.assertEqual(data["completion"], "unknown")

    def test_initialize_that_loses_its_worker_gets_a_json_rpc_error(self) -> None:
        worker = self.start()
        self.supervisor.handle_host_line(
            (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18"}}) + "\n").encode("utf-8")
        )
        worker.wait_for_sent(lambda m: m.get("id") == 1)
        worker.stdout.close()
        message = self.host.wait_for(lambda m: m.get("id") == 1)
        self.assertEqual(message["error"]["code"], -32603)
        self.assertEqual(message["error"]["data"]["error"], "worker_died")

    def test_a_heartbeat_to_a_dead_transport_is_reported_not_answered(self) -> None:
        worker = self.start()
        self.handshake()
        worker.stdin.write = _broken_write
        self.supervisor.handle_host_line(reload_call(7))
        message = self.host.wait_for(lambda m: m.get("id") == 7)
        payload = result_text(message)
        # EOF is not heartbeat success: the dead transport is reported as such and
        # the recycle still brings up the replacement that the reload asked for.
        self.assertEqual(payload["lease_heartbeat"], "worker_died")
        self.assertEqual(payload["status"], "recycled")
        self.assertEqual(payload["generation"], 2)

    def test_replacement_eof_during_replay_fails_the_reload_and_refuses_later_calls(self) -> None:
        first = self.start()
        self.handshake()

        def dying_spawn() -> FakeWorker:
            replacement = FakeWorker(2000, auto_answer=self.auto_answer)
            replacement.stdout.close()
            self.workers.append(replacement)
            return replacement

        self.supervisor._spawn = dying_spawn  # type: ignore[method-assign]
        self.supervisor.handle_host_line(reload_call(7))
        message = self.host.wait_for(lambda m: m.get("id") == 7)
        self.assertTrue(message["result"]["isError"])
        self.assertEqual(result_text(message)["error"], "initialize_replay_failed")
        # The replacement that died mid-replay is the current generation; it is
        # refused from here, never forwarded into.
        self.supervisor.handle_host_line(self._call(8))
        late = self.host.wait_for(lambda m: m.get("id") == 8)
        self.assertEqual(result_text(late)["error"], "worker_died")
        self.assertEqual(result_text(late)["generation"], 2)
        self.assertFalse(any(m.get("id") == 8 for m in self.workers[-1].sent))

    def test_a_reserved_failure_keeps_itself_off_the_host_wire(self) -> None:
        # A heartbeat whose write fails is the supervisor's own request: its failure
        # is observed by the reload that issued it and must never be answered to the
        # host, which has no pending request for a reserved id.
        worker = self.start()
        self.handshake()
        worker.stdin.write = _broken_write
        self.supervisor.handle_host_line(reload_call(7))
        message = self.host.wait_for(lambda m: m.get("id") == 7)
        payload = result_text(message)
        self.assertEqual(payload["lease_heartbeat"], "worker_died")
        self.assertEqual(payload["status"], "recycled")
        self.assertFalse(any(
            m.get("id") in (HEARTBEAT_ID, REPLAY_ID) for m in self.host.messages()
        ))

    def test_a_replay_that_loses_its_transport_fails_the_reload_off_the_host_wire(self) -> None:
        self.start()
        self.handshake()

        def mute_spawn() -> FakeWorker:
            replacement = FakeWorker(2000, auto_answer=False)
            self.workers.append(replacement)
            return replacement

        self.supervisor._spawn = mute_spawn  # type: ignore[method-assign]
        self.supervisor.handle_host_line(reload_call(7))
        deadline = time.monotonic() + DEADLINE_S
        while len(self.workers) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        replacement = self.workers[-1]
        replacement.wait_for_sent(lambda m: m.get("id") == REPLAY_ID)
        replacement.stdout.close()
        message = self.host.wait_for(lambda m: m.get("id") == 7)
        self.assertTrue(message["result"]["isError"])
        self.assertEqual(result_text(message)["error"], "initialize_replay_failed")
        # The reserved failure stays internal even when the reload's own reply has
        # already gone out: an error response with a reserved id would answer a
        # request the host never made.
        time.sleep(0.1)
        self.assertFalse(any(
            m.get("id") in (HEARTBEAT_ID, REPLAY_ID) for m in self.host.messages()
        ))

    def test_reload_does_not_announce_success_when_the_replacement_dies_mid_replay(self) -> None:
        # The replay answer arrived and the initialized notification was written
        # "successfully", but the replacement's transport died while those bytes were
        # in flight. The reload must report the bring-up as failed, not a success the
        # very next call would contradict with worker_died.
        self.start()
        self.handshake()
        death_pending = threading.Event()
        death_marked = threading.Event()
        base_log = self.logs.append

        def notice_death(line: str) -> None:
            base_log(line)
            if "transport failed" in line:
                death_marked.set()

        self.supervisor._log = notice_death

        def dying_spawn() -> FakeWorker:
            replacement = _DyingAtInitializedWorker(
                2000, death_pending=death_pending, auto_answer=self.auto_answer
            )
            replacement.stdin = _FlushWaitsForDeathStdin(
                replacement.stdin, death_pending, death_marked
            )
            self.workers.append(replacement)
            return replacement

        self.supervisor._spawn = dying_spawn  # type: ignore[method-assign]
        self.supervisor.handle_host_line(reload_call(7))
        message = self.host.wait_for(lambda m: m.get("id") == 7)
        self.assertTrue(message["result"]["isError"])
        self.assertEqual(result_text(message)["error"], "initialize_replay_failed")
        # The dead replacement is the current generation: the next call is refused.
        self.supervisor.handle_host_line(self._call(8))
        late = self.host.wait_for(lambda m: m.get("id") == 8)
        self.assertEqual(result_text(late)["error"], "worker_died")
        self.assertEqual(result_text(late)["generation"], 2)

    @slow_test
    def test_a_live_worker_drain_timeout_does_not_fail_the_generation(self) -> None:
        # A worker whose stdout stays open and whose call cannot drain keeps the
        # drain_timeout refusal; it is not mistaken for a dead transport.
        worker = self.start()
        self.handshake()
        self.supervisor.handle_host_line(self._call(50))
        worker.wait_for_sent(lambda m: m.get("id") == 50)
        self.supervisor.handle_host_line(reload_call(11))
        message = self.host.wait_for(lambda m: m.get("id") == 11)
        self.assertTrue(message["result"]["isError"])
        payload = result_text(message)
        self.assertEqual(payload["error"], "drain_timeout")
        self.assertEqual(payload["still_in_flight"], ["50"])
        # The live worker is still the one serving: a fresh call goes through.
        self.supervisor.handle_host_line(self._call(51))
        worker.wait_for_sent(lambda m: m.get("id") == 51)
        worker.answer(51, {"ok": True})
        answered = self.host.wait_for(lambda m: m.get("id") == 51)
        # relayed verbatim from the live worker, not answered from the supervisor
        self.assertEqual(answered["result"], {"ok": True})


if __name__ == "__main__":
    unittest.main()
