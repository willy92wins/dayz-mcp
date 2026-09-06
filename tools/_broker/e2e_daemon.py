"""End-to-end harness for the broker daemon, using the REAL binary.

Reuses the idle-E2E technique: launch `python -m dayz_mcp --daemon` as an actual
detached subprocess and drive it over HTTP. Verifies, against the real process:

  P1 bind + /status healthy
  P2 game poll + client enqueue/await round-trip
  P3 two concurrent clients both get results (F1 multi-session, offline form)
  P4 idle self-shutdown frees the port (no game, no clients)
  P5 daemon survives its immediate spawner exiting (detached; mechanism check —
     the Cowork Job-Object case is the user's in-vivo gate)

Run from tools/ with the venv python:
  .venv-mcp\\Scripts\\python.exe _broker\\e2e_daemon.py
Exit 0 = all pass. Writes evidence to _broker/e2e_result.json.
"""
from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from dayz_mcp import orphan_guard  # noqa: E402
from dayz_mcp.server import ClientRuntime, ServerConfig  # noqa: E402

HERE = Path(__file__).resolve().parent
KEY = "e2e-broker-key"
KEYFILE = HERE / ".e2e.key"
RESULT = HERE / "e2e_result.json"


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def http(base, method, path, payload=None, query=None, timeout=3.0):
    params = dict(query or {})
    params["key"] = KEY
    url = base + path + "?" + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return int(response.status), json.loads(response.read().decode("utf-8") or "{}")


class GamePoller:
    def __init__(self, base):
        self.base = base
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self):
        while not self._stop.is_set():
            try:
                _s, body = http(self.base, "GET", "/poll", query={"peer": "server"})
                for cmd in body.get("commands", []):
                    result = {"id": cmd["id"], "ok": 1, "cmd": cmd["cmd"], "state": {"pos": [1, 2, 3]}}
                    http(self.base, "POST", "/result", payload=result)
            except Exception:
                pass
            time.sleep(0.05)


def client_runtime(port: int) -> ClientRuntime:
    def no_auto_spawn() -> int | None:
        raise RuntimeError("unexpected_auto_spawn")

    return ClientRuntime(
        ServerConfig(
            mode="client",
            key=KEY,
            port=port,
            client_platform="codex",
            task_label="broker-e2e",
            log_sink=lambda _message: None,
        ),
        spawn_fn=no_auto_spawn,
    )


def client_call(runtime: ClientRuntime, cmd="query_player_state", peer="server", timeout=5.0):
    return asyncio.run(runtime.call_bridge(cmd, {}, peer, timeout))


def launch_daemon(port, idle_timeout, errlog: Path):
    argv = [
        sys.executable, "-m", "dayz_mcp", "--daemon",
        "--port", str(port), "--keyfile", str(KEYFILE), "--idle-timeout", str(idle_timeout),
    ]
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    err = open(errlog, "w", encoding="utf-8")
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=err, close_fds=True, cwd=str(_TOOLS), creationflags=flags)


def wait_healthy(port, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if orphan_guard.probe_status_healthy(port, KEY, timeout=1.0):
            return True
        time.sleep(0.2)
    return False


def wait_port_free(port, timeout=20.0):
    return orphan_guard.wait_port_free(port, timeout=timeout, interval=0.2)


def clean_exit(returncode: int | None, port_free: bool) -> bool:
    return returncode == 0 and port_free


def wait_detached_exit_code(pid: int, timeout: float = 20.0) -> int | None:
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, int(pid))
    if not handle:
        return None
    try:
        wait_result = kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        if wait_result != 0:
            return None
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        return int(exit_code.value)
    finally:
        kernel32.CloseHandle(handle)


def main() -> int:
    KEYFILE.write_text(KEY, encoding="utf-8")
    results: dict[str, object] = {}
    ok = True

    # --- Daemon A: bind + round-trip + concurrency + idle shutdown ---
    port_a = free_port()
    proc_a = launch_daemon(port_a, idle_timeout=2, errlog=HERE / "daemonA.err.log")
    base_a = f"http://127.0.0.1:{port_a}"
    try:
        p1 = wait_healthy(port_a)
        results["P1_bind_and_status_healthy"] = p1
        ok &= p1

        poller = GamePoller(base_a)
        poller.start()
        try:
            r = client_call(client_runtime(port_a))
            p2 = bool(r.get("ok")) and r.get("state", {}).get("pos") == [1, 2, 3]
            results["P2_game_client_round_trip"] = p2
            ok &= p2

            out: list[dict] = []
            errs: list[str] = []

            def worker():
                try:
                    out.append(client_call(client_runtime(port_a)))
                except Exception as exc:  # pragma: no cover - diagnostic
                    errs.append(repr(exc))

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=8.0)
            p3 = len(out) == 2 and all(x.get("ok") for x in out) and not errs
            results["P3_two_concurrent_clients"] = p3
            results["P3_errors"] = errs
            ok &= p3
        finally:
            poller.stop()

        # Idle: no game, no clients → daemon exits and frees the port (idle=2s,
        # watchdog poll floor 5s → fires within ~7s; allow margin).
        exited = False
        for _ in range(40):
            if proc_a.poll() is not None:
                exited = True
                break
            time.sleep(0.5)
        freed = wait_port_free(port_a, timeout=10.0)
        daemon_a_exit_code = proc_a.poll()
        p4 = exited and clean_exit(daemon_a_exit_code, freed)
        results["P4_idle_shutdown_frees_port"] = p4
        results["P4_daemon_exit_code"] = daemon_a_exit_code
        ok &= p4
    finally:
        # Never force-terminate a daemon in this harness. The idle watchdog owns
        # cleanup; a failure to observe natural exit is itself a failed gate.
        pass

    # --- Daemon B: survives its immediate spawner exiting (detached) ---
    port_b = free_port()
    spawner = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, r'%s');"
         "from dayz_mcp import daemon;"
         "pid=daemon.spawn_detached([sys.executable,'-m','dayz_mcp','--daemon','--port','%d',"
         "'--keyfile',r'%s','--idle-timeout','2']);print(pid)" % (str(_TOOLS), port_b, str(KEYFILE))],
        capture_output=True, text=True, cwd=str(_TOOLS), timeout=30,
    )
    spawner_exit = spawner.returncode
    try:
        daemon_b_pid = int(spawner.stdout.strip())
    except ValueError:
        daemon_b_pid = 0
    healthy_after_spawner_gone = wait_healthy(port_b, timeout=15.0)
    daemon_b_exit_code = (
        wait_detached_exit_code(daemon_b_pid, timeout=20.0)
        if daemon_b_pid > 0 and healthy_after_spawner_gone
        else None
    )
    natural_idle_cleanup = wait_port_free(port_b, timeout=10.0)
    p5 = (
        spawner_exit == 0
        and healthy_after_spawner_gone
        and clean_exit(daemon_b_exit_code, natural_idle_cleanup)
    )
    results["P5_survives_spawner_exit"] = p5
    results["P5_spawner_exit_code"] = spawner_exit
    results["P5_daemon_exit_code"] = daemon_b_exit_code
    if spawner.stderr.strip():
        results["P5_spawner_stderr"] = spawner.stderr.strip()[:500]
    ok &= p5
    results["P5_natural_idle_cleanup"] = natural_idle_cleanup

    results["ALL_PASS"] = bool(ok)
    RESULT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
