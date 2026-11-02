"""100 GET /status against an ephemeral loopback daemon, one-shot then keepalive.

Not a unittest. From tools/:

    .venv-mcp/Scripts/python.exe dev/bench_accredited_keepalive.py

The listener is this process on port 0. It is not the installed daemon and it
does not use port 8765 or %LOCALAPPDATA%\\DayZ_MCP. Accreditation still walks
the real TCP table (that is the cost being measured). The process image, argv
and cwd are injected as a daemon identity, because this interpreter's own argv
is not ``--daemon``.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import psutil

from dayz_mcp import accredited_daemon_transport, accredited_keepalive, loopback
from dayz_mcp.native_process_guard import identity_hashes


EXECUTABLE = r"C:\Runtime\python.exe"
CWD = r"C:\DayZ_MCP_dev\tools"
REQUESTS = 100
_FORBIDDEN_PORT = 8765


def _argv(port: int) -> list[str]:
    return [EXECUTABLE, "-m", "dayz_mcp", "--daemon", "--port", str(port)]


def _guard(port: int):
    hashes = identity_hashes(EXECUTABLE, _argv(port))

    class Guard:
        def snapshot(self, pid: int) -> dict[str, object]:
            return {
                "pid": pid,
                "creation_time_utc": "2026-11-01T00:00:00Z",
                "executable_sha256": hashes["executable_sha256"],
                "command_line_sha256": hashes["command_line_sha256"],
                "identity_scheme": "psutil-argv-v2",
                "identity_complete": True,
            }

    return Guard()


def _run(label: str, request, *, port: int, key: str, walks: dict[str, int]) -> None:
    walks["n"] = 0
    started = time.perf_counter()
    for index in range(REQUESTS):
        status, _body = request(
            host="127.0.0.1",
            port=port,
            key=key,
            method="GET",
            path="/status",
            query=None,
            body=None,
            headers=None,
            deadline=time.monotonic() + 5.0,
            expected_executable=EXECUTABLE,
            expected_argv=_argv(port),
            expected_cwd=CWD,
            connections_fn=_counted(walks),
            get_executable=lambda _pid: EXECUTABLE,
            get_argv=lambda _pid: _argv(port),
            get_cwd=lambda _pid: CWD,
            guard=_guard(port),
            time_fn=time.monotonic,
        )
        if status != 200:
            raise SystemExit(f"{label} request {index} status {status}")
    elapsed = time.perf_counter() - started
    accreditations = walks["n"] / 3
    print(
        f"{label}: requests={REQUESTS} accreditations={accreditations:g} "
        f"connection_walks={walks['n']} seconds={elapsed:.3f}"
    )


def _counted(walks: dict[str, int]):
    def connections_fn():
        walks["n"] += 1
        return psutil.net_connections(kind="tcp")

    return connections_fn


def main() -> None:
    key = "bench-key"
    state = loopback.ServerState(key)
    httpd = loopback.create_http_server(
        0,
        state,
        log_sink=lambda _message: None,
        reclaim_orphans=False,
    )
    thread = threading.Thread(
        target=httpd.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    port = int(httpd.server_address[1])
    if port == _FORBIDDEN_PORT:
        raise SystemExit("ephemeral bind returned the installed daemon port")
    walks: dict[str, int] = {"n": 0}
    try:
        _run(
            "one-shot",
            accredited_daemon_transport.verified_daemon_http_request,
            port=port,
            key=key,
            walks=walks,
        )
        accredited_keepalive.clear_idle_connections()
        _run(
            "keepalive",
            accredited_keepalive.keepalive_daemon_http_request,
            port=port,
            key=key,
            walks=walks,
        )
    finally:
        accredited_keepalive.clear_idle_connections()
        httpd.shutdown()
        thread.join(timeout=2.0)
        httpd.server_close()


if __name__ == "__main__":
    main()
