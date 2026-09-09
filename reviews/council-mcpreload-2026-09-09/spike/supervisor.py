"""Throwaway supervisor: owns the host's stdio, forwards JSON-RPC to a replaceable worker.

The point of the shape: the process that writes to the host is not the process being
replaced. So the response bytes are in this process's hands before it decides anything,
and admission is the exact set of forwarded ids rather than a snapshot of "looks idle".

Threads, not asyncio: on Windows asyncio cannot wrap an inherited stdin pipe without
ceremony, and blocking readline on the binary buffers is both simpler and easier to
reason about for a spike whose whole question is whether the pipes hold up.

Not production code. No lease, no provenance, no orphan guard.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER = [sys.executable, "-u", str(HERE / "worker.py")]

RELOAD_TOOL = {
    "name": "server_reload",
    "description": "Replace the worker process so it imports the sources on disk now.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def log(message: str) -> None:
    """Diagnostics go to stderr; stdout belongs to the protocol."""
    print(f"[supervisor] {message}", file=sys.stderr, flush=True)


class Supervisor:
    def __init__(self):
        self.out = sys.stdout.buffer
        self.out_lock = threading.Lock()
        self.worker = None
        self.pump = None
        self.worker_generation = 0
        # Requests forwarded and not yet answered: the admission set.
        self.inflight = {}
        self.inflight_lock = threading.Condition()
        self.draining = False
        # The handshake, kept verbatim so a fresh worker can be brought to the same state.
        self.initialize_line = None
        self.initialized_line = None

    # -- plumbing ---------------------------------------------------------

    def send_host(self, obj: dict) -> None:
        raw = (json.dumps(obj) + "\n").encode("utf-8")
        with self.out_lock:
            self.out.write(raw)
            self.out.flush()

    def send_worker(self, raw: bytes) -> None:
        self.worker.stdin.write(raw)
        self.worker.stdin.flush()

    def start_worker(self) -> None:
        self.worker = subprocess.Popen(
            WORKER, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=None, cwd=str(HERE),
        )
        self.worker_generation += 1
        log(f"worker pid={self.worker.pid} generation={self.worker_generation}")
        self.pump = threading.Thread(target=self.pump_worker, args=(self.worker,), daemon=True)
        self.pump.start()

    def pump_worker(self, worker) -> None:
        """Forward worker -> host, and retire ids from the admission set as they answer."""
        for raw in iter(worker.stdout.readline, b""):
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except Exception:
                continue
            ident = message.get("id")
            method = None
            if ident is not None and "method" not in message:
                with self.inflight_lock:
                    method = self.inflight.pop(ident, None)
                    self.inflight_lock.notify_all()
            if method == "tools/list":
                tools = message.get("result", {}).get("tools")
                if isinstance(tools, list):
                    tools.append(RELOAD_TOOL)
            if ident == "__replay__":
                # The handshake this supervisor replayed on the host's behalf. The host
                # never sent it and has no pending id for it: forwarding it makes the
                # client log an unmatched response.
                continue
            self.send_host(message)
        log(f"worker pid={worker.pid} stdout closed")

    # -- the recycle ------------------------------------------------------

    def recycle(self, request_id) -> None:
        started = time.monotonic()
        with self.inflight_lock:
            self.draining = True
            while self.inflight:
                if not self.inflight_lock.wait(timeout=10.0):
                    break
            remaining = dict(self.inflight)
        if remaining:
            self.reply_error(request_id, f"drain_timeout: still in flight {sorted(remaining)}")
            with self.inflight_lock:
                self.draining = False
            return

        old = self.worker
        old_pid = old.pid
        old.stdin.close()
        try:
            old.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            old.kill()
            old.wait(timeout=5.0)
        self.start_worker()

        # Bring the new worker to the state the host believes it is in.
        if self.initialize_line is None:
            self.reply_error(request_id, "no_initialize_recorded")
            return
        with self.inflight_lock:
            self.inflight["__replay__"] = "initialize"
        replay = json.loads(self.initialize_line)
        replay["id"] = "__replay__"
        self.send_worker((json.dumps(replay) + "\n").encode("utf-8"))
        with self.inflight_lock:
            deadline = time.monotonic() + 15.0
            while "__replay__" in self.inflight and time.monotonic() < deadline:
                self.inflight_lock.wait(timeout=1.0)
            replayed = "__replay__" not in self.inflight
        if not replayed:
            self.reply_error(request_id, "initialize_replay_timeout")
            return
        if self.initialized_line:
            self.send_worker(self.initialized_line)

        with self.inflight_lock:
            self.draining = False
        elapsed = time.monotonic() - started
        payload = {
            "status": "recycled", "old_worker_pid": old_pid,
            "new_worker_pid": self.worker.pid, "supervisor_pid": os.getpid(),
            "generation": self.worker_generation, "elapsed_s": round(elapsed, 3),
        }
        self.send_host({
            "jsonrpc": "2.0", "id": request_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "isError": False,
            },
        })
        # The catalogue is unchanged here, but a real supervisor announces it anyway
        # when the new worker's tool list differs from the old one.
        self.send_host({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})

    def reply_error(self, request_id, message: str) -> None:
        log(f"recycle refused: {message}")
        self.send_host({
            "jsonrpc": "2.0", "id": request_id,
            "result": {"content": [{"type": "text", "text": message}], "isError": True},
        })

    # -- host side --------------------------------------------------------

    def run(self) -> None:
        self.start_worker()
        for raw in iter(sys.stdin.buffer.readline, b""):
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except Exception:
                self.send_worker(raw)
                continue

            method = message.get("method")
            ident = message.get("id")

            if method == "initialize":
                self.initialize_line = line.decode("utf-8")
            elif method == "notifications/initialized":
                self.initialized_line = raw

            if method == "tools/call" and message.get("params", {}).get("name") == "server_reload":
                threading.Thread(target=self.recycle, args=(ident,), daemon=True).start()
                continue

            if ident is not None and method is not None:
                with self.inflight_lock:
                    if self.draining:
                        self.send_host({
                            "jsonrpc": "2.0", "id": ident,
                            "result": {
                                "content": [{"type": "text", "text": "recycling"}],
                                "isError": True,
                            },
                        })
                        continue
                    self.inflight[ident] = method
            self.send_worker(raw)
        log("host stdin closed")


if __name__ == "__main__":
    Supervisor().run()
