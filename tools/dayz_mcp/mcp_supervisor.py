"""Own the host's stdio and forward it to a worker that can be replaced in place.

An MCP server process imports its tool modules once, so a fix applied to those modules
is not served until a NEW process serves it. The shape that fixes it, accredited by the
spike of 2026-09-09 (reviews/council-mcpreload-2026-09-09/SPIKE-RESULT.md, 14/14): the
process that writes to the host is not the process being replaced. The response bytes
are in this process's hands before anything is killed, and admission is the exact set of
forwarded ids rather than a snapshot of "looks idle".

Two measured facts shape what is here and what is deliberately absent:

  - Forwarding by lines is fine. The spike measured 32 MB in 15.63 s through a
    supervisor and concluded a production one needs length framing. The control it did
    not run says otherwise: the same payload WITHOUT a supervisor takes 12.154 s against
    12.543 s with it, so the curve belongs to the MCP SDK at both ends and the
    supervisor's share is 3%. Line forwarding stays; the bytes are relayed verbatim
    rather than re-serialised, which is both simpler and cheaper than what the spike did.
  - Popen.pid is not the pid that serves. Launched from a venv, Popen returns the venv
    redirector and the real interpreter is its child. Nothing here treats that number as
    a handle to the serving process: it is reported as launcher_pid, and termination
    goes through the process TREE.

A generation whose transport dies is not left to swallow requests. EOF on its
stdout outside a planned retirement, or a failed write into its stdin, marks the
generation failed under the state lock: its pending requests are detached and
answered exactly once -- a tools/call with an isError result whose payload says
the completion is unknown, anything else with a JSON-RPC -32603 error -- and
further admission into that generation is refused. The supervisor's own reserved
requests fail internally and are never answered to the host. server_reload is the way out:
it retires the failed generation and replays the handshake into a replacement.
Interrupted calls are never retried on the caller's behalf; their effects may
already have reached the daemon.

The lease is carried by dayz_mcp.session_handoff, whose contract the rehearsal of
2026-09-10 fixed (REHEARSAL-LEASE.md, 34/34). This module never reads the token: it
hands the carrier PATH to each worker generation and lets the worker write and consume
it, which also covers a worker that dies unplanned.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


# Reserved ids the supervisor sends on its own behalf. The host never issued them, so
# their responses are swallowed rather than forwarded: a client that sees a response it
# has no pending request for logs it as a protocol fault, which the spike hit.
REPLAY_ID = "__dayz_mcp_supervisor_replay__"
HEARTBEAT_ID = "__dayz_mcp_supervisor_heartbeat__"
_RESERVED_IDS = frozenset({REPLAY_ID, HEARTBEAT_ID})

RELOAD_TOOL_NAME = "server_reload"
RELOAD_TOOL = {
    "name": RELOAD_TOOL_NAME,
    "description": (
        "Replace the worker process so it serves the sources on disk now. Waits for "
        "calls in flight, carries the session lease across, and refuses rather than "
        "recycle while a call cannot be drained."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}

# The recycle runs against the lease TTL with nobody beating, so it gets a hard ceiling
# far below it. SESSION_TTL_S is 120 s and the spike's recycle took 2.24 s with a call in
# flight; 30 s is generous for the drain and still leaves the lease three quarters alive.
DRAIN_TIMEOUT_S = 30.0
STDIN_CLOSE_GRACE_S = 5.0
REPLAY_TIMEOUT_S = 15.0
HEARTBEAT_TIMEOUT_S = 5.0


class WorkerProcess(Protocol):
    """The slice of subprocess.Popen this module uses. Kept small so tests can stand in."""

    @property
    def stdin(self) -> object: ...

    @property
    def stdout(self) -> object: ...

    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


@dataclass
class _Generation:
    """One worker and the bookkeeping that belongs to it, not to the supervisor."""

    process: WorkerProcess
    number: int
    pump: threading.Thread | None = None
    inflight: dict[object, str] = field(default_factory=dict)
    # Terminal transport failure, decided under the supervisor's _state lock: no
    # further admission, no further forwarding for this generation.
    failed: bool = False
    failure_reason: str | None = None
    # Ids the failure path detached and answered; the pump swallows a late response
    # for one of these so the host never sees a second terminal answer.
    detached: set[object] = field(default_factory=set)
    # Set before a planned retirement closes stdin, so the pump reads the EOF that
    # follows as the expected end and not as a transport failure.
    retired: bool = False


def terminate_tree(process: WorkerProcess, *, log: Callable[[str], None]) -> None:
    """End the worker and anything it spawned.

    Killing Popen.pid alone is not enough from a venv: that pid is the redirector and
    the interpreter that holds the pipes is its child, which would survive as an orphan
    still owning stdio. taskkill walks the tree; elsewhere the process group does.
    """
    pid = process.pid
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                check=False,
                timeout=10.0,
            )
            return
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"taskkill failed for pid={pid}: {exc}")
    try:
        process.kill()
    except OSError as exc:
        log(f"kill failed for pid={pid}: {exc}")


class Supervisor:
    """Relay host <-> worker, publish server_reload, and replace the worker on demand."""

    def __init__(
        self,
        *,
        spawn: Callable[[], WorkerProcess],
        out_stream: object,
        log: Callable[[str], None],
        monotonic: Callable[[], float] = time.monotonic,
        drain_timeout_s: float = DRAIN_TIMEOUT_S,
        stdin_close_grace_s: float = STDIN_CLOSE_GRACE_S,
        replay_timeout_s: float = REPLAY_TIMEOUT_S,
        heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S,
    ) -> None:
        self._spawn = spawn
        self._out = out_stream
        self._log = log
        self._monotonic = monotonic
        self._drain_timeout_s = drain_timeout_s
        self._stdin_close_grace_s = stdin_close_grace_s
        self._replay_timeout_s = replay_timeout_s
        self._heartbeat_timeout_s = heartbeat_timeout_s

        self._out_lock = threading.Lock()
        self._state = threading.Condition()
        self._current: _Generation | None = None
        self._generation = 0
        self._draining = False
        # The handshake, kept verbatim, so a replacement reaches the state the host
        # believes its server is in without the host being told to do it again.
        self._initialize_line: bytes | None = None
        self._initialized_line: bytes | None = None

    # -- wire ------------------------------------------------------------------

    def _write_host(self, raw: bytes) -> None:
        with self._out_lock:
            self._out.write(raw if raw.endswith(b"\n") else raw + b"\n")
            self._out.flush()

    def _send_host(self, message: dict) -> None:
        self._write_host((json.dumps(message) + "\n").encode("utf-8"))

    def _send_worker(self, generation: _Generation, raw: bytes) -> bool:
        with self._state:
            if generation.failed:
                # The transport is known dead. Refuse rather than lose bytes into a
                # pipe that can no longer produce a response.
                return False
        try:
            generation.process.stdin.write(raw if raw.endswith(b"\n") else raw + b"\n")
            generation.process.stdin.flush()
        except (OSError, ValueError) as exc:
            self._log(f"worker generation={generation.number} write failed: {exc}")
            self._fail_generation(generation, "write_failed")
            return False
        return True

    def _fail_generation(self, generation: _Generation, reason: str) -> None:
        """Mark the transport dead, detach its pending requests, answer them once.

        Idempotent: whichever racer gets here first (EOF in the pump, a failed write)
        does the work, and later callers observe the flag and return. Detaching happens
        under the same lock hold that sets the flag, so a response that lost the race
        cannot be forwarded after the failure answer.
        """
        with self._state:
            if generation.failed:
                return
            generation.failed = True
            generation.failure_reason = reason
            pending = list(generation.inflight.items())
            generation.inflight.clear()
            generation.detached.update(ident for ident, _ in pending)
            self._state.notify_all()
        host_pending = [
            (ident, method) for ident, method in pending if ident not in _RESERVED_IDS
        ]
        self._log(
            f"worker generation={generation.number} transport failed ({reason}); "
            f"detached {len(pending)} pending request(s), answered {len(host_pending)}"
        )
        for ident, method in host_pending:
            self._answer_worker_died(ident, method, generation.number, reason)

    def _worker_died_payload(self, generation_number: int, reason: str) -> dict:
        # completion is deliberately unknown: the call may or may not have reached the
        # daemon, so nothing here may invite an automatic retry.
        return {
            "error": "worker_died",
            "generation": generation_number,
            "reason": reason,
            "completion": "unknown",
        }

    def _answer_worker_died(
        self, ident: object, method: str | None, generation_number: int, reason: str
    ) -> None:
        """Give one host request the terminal answer its dead transport owes it.

        A tools/call gets the tool-shaped isError result the host expects from a call;
        any other request method gets a JSON-RPC error, because a tools/call result
        would not be a valid answer to an initialize or a tools/list.
        """
        payload = self._worker_died_payload(generation_number, reason)
        if method == "tools/call":
            self._reply(ident, payload, is_error=True)
            return
        self._send_host(
            {
                "jsonrpc": "2.0",
                "id": ident,
                "error": {
                    "code": -32603,  # JSON-RPC internal error
                    "message": "worker transport failed; request completion is unknown",
                    "data": payload,
                },
            }
        )

    # -- worker lifecycle ------------------------------------------------------

    def start_worker(self) -> _Generation:
        process = self._spawn()
        self._generation += 1
        generation = _Generation(process=process, number=self._generation)
        with self._state:
            self._current = generation
        self._log(
            f"worker generation={generation.number} launcher_pid={process.pid} "
            "(launcher, not necessarily the serving interpreter)"
        )
        pump = threading.Thread(
            target=self._pump_worker, args=(generation,), daemon=True,
            name=f"dayz-mcp-pump-{generation.number}",
        )
        generation.pump = pump
        pump.start()
        return generation

    def _pump_worker(self, generation: _Generation) -> None:
        """Forward worker -> host and retire ids from the admission set as they answer.

        The bytes are relayed as they arrived. Parsing happens to read the id and to see
        a tools/list result worth amending; it never becomes the thing that is written,
        so a response cannot be reshaped by a round-trip through this process.
        """
        stdout = generation.process.stdout
        for raw in iter(stdout.readline, b""):
            if not raw.strip():
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                # Not JSON: not ours to interpret. Pass it through unchanged.
                self._write_host(raw)
                continue
            if not isinstance(message, dict):
                self._write_host(raw)
                continue

            ident = message.get("id")
            is_response = ident is not None and "method" not in message
            if is_response:
                with self._state:
                    generation.inflight.pop(ident, None)
                    superseded = ident in generation.detached
                    self._state.notify_all()
                if superseded:
                    # A terminal failure already answered this request; relaying this
                    # late response would give the host a second terminal answer.
                    continue
                if ident in _RESERVED_IDS:
                    # The supervisor asked for this, not the host. Forwarding it makes
                    # the client log a response with no pending request.
                    continue
                amended = self._amend_tools_list(message)
                if amended is not None:
                    self._send_host(amended)
                    continue
            self._write_host(raw)
        with self._state:
            retired = generation.retired
        if not retired:
            # EOF without a planned retirement: nothing else will ever come out of
            # this transport, so whatever is still pending is answered from here.
            self._fail_generation(generation, "stdout_eof")
        self._log(f"worker generation={generation.number} stdout closed")

    def _amend_tools_list(self, message: dict) -> dict | None:
        """Add server_reload to a worker's catalogue; it is the supervisor's own tool."""
        result = message.get("result")
        if not isinstance(result, dict):
            return None
        tools = result.get("tools")
        if not isinstance(tools, list):
            return None
        if any(
            isinstance(tool, dict) and tool.get("name") == RELOAD_TOOL_NAME
            for tool in tools
        ):
            return None
        tools.append(dict(RELOAD_TOOL))
        return message

    # -- the recycle -----------------------------------------------------------

    def recycle(self, request_id: object) -> None:
        started = self._monotonic()
        with self._state:
            generation = self._current
            if generation is None:
                self._reply(request_id, {"error": "no_worker"}, is_error=True)
                return
            if self._draining:
                self._reply(request_id, {"error": "recycle_already_running"}, is_error=True)
                return
            self._draining = True

        try:
            drained, remaining = self._drain(generation)
            if not drained:
                # Refusing is the whole point of admission: a call still in flight would
                # be answered by a process that is about to stop existing.
                self._reply(
                    request_id,
                    {"error": "drain_timeout", "still_in_flight": sorted(map(str, remaining))},
                    is_error=True,
                )
                return

            # The lease TTL runs while nobody is beating. Refresh it against the daemon
            # through the worker that still owns it, so the replacement inherits a full
            # window rather than whatever was left.
            beat = self._heartbeat(generation)

            self._retire(generation)
            replacement = self.start_worker()
            replayed = self._replay_handshake(replacement)
            if not replayed:
                self._reply(
                    request_id, {"error": "initialize_replay_failed"}, is_error=True
                )
                return
        finally:
            with self._state:
                self._draining = False

        elapsed = self._monotonic() - started
        self._reply(
            request_id,
            {
                "status": "recycled",
                "generation": replacement.number,
                "launcher_pid": replacement.process.pid,
                "supervisor_pid": os.getpid(),
                "lease_heartbeat": beat,
                "elapsed_s": round(elapsed, 3),
            },
            is_error=False,
        )
        self._send_host({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})

    def _drain(self, generation: _Generation) -> tuple[bool, list[object]]:
        deadline = self._monotonic() + self._drain_timeout_s
        with self._state:
            while generation.inflight:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return False, list(generation.inflight)
                self._state.wait(timeout=min(remaining, 1.0))
            return True, []

    def _heartbeat(self, generation: _Generation) -> str:
        """Ask the outgoing worker to refresh its lease. Best effort by design.

        A worker holding no lease answers with an error, which is not a reason to abort
        a recycle: there is simply nothing to preserve. The answer is reported so a
        failed refresh is visible rather than assumed.
        """
        request = {
            "jsonrpc": "2.0",
            "id": HEARTBEAT_ID,
            "method": "tools/call",
            "params": {"name": "session_heartbeat", "arguments": {}},
        }
        with self._state:
            if generation.failed:
                return "worker_died"
            generation.inflight[HEARTBEAT_ID] = "tools/call"
        if not self._send_worker(generation, (json.dumps(request) + "\n").encode("utf-8")):
            # The failed write marked the generation dead, which detached the heartbeat.
            return "worker_died"
        deadline = self._monotonic() + self._heartbeat_timeout_s
        with self._state:
            while True:
                if HEARTBEAT_ID in generation.detached:
                    # The transport died while waiting. EOF is not an answer.
                    return "worker_died"
                if HEARTBEAT_ID not in generation.inflight:
                    break
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    generation.inflight.pop(HEARTBEAT_ID, None)
                    return "timeout"
                self._state.wait(timeout=min(remaining, 0.5))
        return "answered"

    def _retire(self, generation: _Generation) -> None:
        """Close stdin first: the stdio contract says a worker exits when its peer goes."""
        with self._state:
            generation.retired = True
        try:
            generation.process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            generation.process.wait(timeout=self._stdin_close_grace_s)
            return
        except subprocess.TimeoutExpired:
            self._log(
                f"worker generation={generation.number} outlived stdin close; killing tree"
            )
        terminate_tree(generation.process, log=self._log)
        try:
            generation.process.wait(timeout=self._stdin_close_grace_s)
        except subprocess.TimeoutExpired:
            self._log(f"worker generation={generation.number} did not die after kill")

    def _replay_handshake(self, generation: _Generation) -> bool:
        if self._initialize_line is None:
            self._log("no initialize recorded; cannot bring the replacement up")
            return False
        try:
            replay = json.loads(self._initialize_line)
        except ValueError:
            return False
        replay["id"] = REPLAY_ID
        with self._state:
            if generation.failed:
                return False
            generation.inflight[REPLAY_ID] = "initialize"
        if not self._send_worker(generation, (json.dumps(replay) + "\n").encode("utf-8")):
            return False
        deadline = self._monotonic() + self._replay_timeout_s
        with self._state:
            while True:
                if REPLAY_ID in generation.detached:
                    # The replacement's transport died mid-replay: a failure to report,
                    # never a success to build on.
                    self._log("initialize replay lost to a dead worker transport")
                    return False
                if REPLAY_ID not in generation.inflight:
                    break
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    generation.inflight.pop(REPLAY_ID, None)
                    self._log("initialize replay timed out")
                    return False
                self._state.wait(timeout=min(remaining, 0.5))
        if self._initialized_line is not None:
            if not self._send_worker(generation, self._initialized_line):
                return False
        # The send can report success while a death is being marked on another
        # thread: the bytes were accepted, the reader is gone. Resolving the
        # outcome under the state lock keeps a failed replacement from being
        # announced as a success the very next call would contradict.
        with self._state:
            if generation.failed:
                self._log("initialize replay lost to a dead worker transport")
                return False
        return True

    def _reply(self, request_id: object, payload: dict, *, is_error: bool) -> None:
        if is_error:
            self._log(f"server_reload: {payload}")
        self._send_host(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(payload)}],
                    "isError": is_error,
                },
            }
        )

    # -- host side -------------------------------------------------------------

    def handle_host_line(self, raw: bytes) -> None:
        """Route one line from the host. Anything not understood is forwarded intact."""
        with self._state:
            generation = self._current
        if generation is None:
            return
        try:
            message = json.loads(raw)
        except ValueError:
            self._send_worker(generation, raw)
            return
        if not isinstance(message, dict):
            self._send_worker(generation, raw)
            return

        method = message.get("method")
        ident = message.get("id")

        if method == "initialize":
            self._initialize_line = raw
        elif method == "notifications/initialized":
            self._initialized_line = raw

        if ident in _RESERVED_IDS:
            # A host that used one of these would collide with the supervisor's own
            # bookkeeping. Refuse rather than let the two share a slot.
            self._reply(ident, {"error": "reserved_request_id"}, is_error=True)
            return

        params = message.get("params")
        if (
            method == "tools/call"
            and isinstance(params, dict)
            and params.get("name") == RELOAD_TOOL_NAME
        ):
            threading.Thread(
                target=self.recycle, args=(ident,), daemon=True, name="dayz-mcp-recycle"
            ).start()
            return

        if ident is not None and method is not None:
            with self._state:
                if self._draining:
                    # Admitting it now would put a call in flight against a worker that
                    # is being replaced, which is what the drain just finished ruling out.
                    self._reply(ident, {"error": "server_recycling"}, is_error=True)
                    return
                if generation.failed:
                    # The transport is dead: admitting would swallow the request. The
                    # refusal is answered from here, never forwarded.
                    self._answer_worker_died(
                        ident, method, generation.number,
                        generation.failure_reason or "worker_died",
                    )
                    return
                generation.inflight[ident] = method
        self._send_worker(generation, raw)

    def run(self, in_stream: object) -> None:
        self.start_worker()
        for raw in iter(in_stream.readline, b""):
            if not raw.strip():
                continue
            self.handle_host_line(raw)
        self._log("host stdin closed")
        with self._state:
            generation = self._current
        if generation is not None:
            self._retire(generation)
