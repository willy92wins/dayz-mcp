"""Orphan-proofing for the loopback bind: parent-death watchdog + port reclaim.

The MCP server holds 127.0.0.1:<port> through an exclusive bind (E4 lock,
``ExclusiveThreadingHTTPServer`` in loopback.py). A clean shutdown frees the port
because stdin EOF returns ``app.run()`` and the lifespan calls ``stop_loopback()``.
When the parent (Claude Code / node) dies ABRUPTLY on Windows the child never sees
that EOF, ``app.run()`` blocks forever, and the orphaned process keeps the port —
the next session then fails the exclusive bind with EADDRINUSE.

Two coupled mechanisms, both stdlib-only (no psutil):

1. Parent-death watchdog (primary): capture a handle to the parent at startup and
   block a daemon thread on it; when the parent exits, free the port and hard-exit.
   Removes the cause.
2. Port auto-reclaim (belt-and-suspenders): if the exclusive bind fails because a
   confirmed-dead-parent ``dayz_mcp`` orphan still holds the port (e.g. the process
   was SIGKILLed before the watchdog could run, or a legacy build with no watchdog),
   terminate that orphan and retry the bind ONCE. A LIVE instance (parent alive) is
   never touched, so the E4 fail-closed lock is preserved.

Threading: the watchdog runs on one daemon thread blocked in WaitForSingleObject.
Platform: the Windows ctypes path is the one that matters for the real environment;
POSIX is a best-effort fallback (poll getppid / os.kill) and is untested here.
"""
from __future__ import annotations

import json
import http.client
import locale
import math
import os
import ntpath
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from dayz_mcp.native_process_guard import NativeProcessGuard, identity_hashes
from dayz_mcp.daemon_contract import argv_targets_port, classify_dayz_argv
from dayz_mcp.accredited_daemon_transport import (
    MAX_AUTHENTICATED_RESPONSE_BYTES,
    verified_daemon_http_request,
)
from dayz_mcp.native_process_snapshot import (
    command_argv_of,
    full_image_path_of,
    same_path as _same_path,
    working_directory_of,
)

try:
    import psutil
except ImportError:  # pragma: no cover - exercised through fail-closed callers.
    psutil = None  # type: ignore[assignment]

ARMED = "armed"
DISABLED = "disabled"
DAEMON_STATUS_SCHEMA = "dayz-mcp-daemon-status-v1"
DAEMON_STATUS_MARKER_KEYS = frozenset(
    {
        "schema",
        "product",
        "mode",
        "daemon_generation",
        "coordination_revision",
    }
)
MAX_STATUS_BODY_BYTES = MAX_AUTHENTICATED_RESPONSE_BYTES

# Win32 constants (kernel32). Values verified against the running kernel32:
# OpenProcess(SYNCHRONIZE) returns a usable handle; WaitForSingleObject yields
# WAIT_TIMEOUT (0x102) while the target is alive and WAIT_OBJECT_0 (0x0) once it
# exits. ERROR_INVALID_PARAMETER (87) from OpenProcess means "no such pid".
_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_INFINITE = 0xFFFFFFFF
_WAIT_OBJECT_0 = 0x00000000
_WAIT_FAILED = 0xFFFFFFFF
_TH32CS_SNAPPROCESS = 0x00000002
_ERROR_INVALID_PARAMETER = 87
_ERROR_NO_MORE_FILES = 18

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]

    class _PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    _k32.Process32First.restype = wintypes.BOOL
    _k32.Process32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32)]
    _k32.Process32Next.restype = wintypes.BOOL
    _k32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32)]
    _k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
else:  # pragma: no cover - POSIX fallback, untested on the Windows target.
    ctypes = None  # type: ignore[assignment]
    _k32 = None


def _noop_log(_message: str) -> None:
    return


# ---------------------------------------------------------------------------
# Process primitives (stdlib only).
# ---------------------------------------------------------------------------

def open_parent_wait_handle(ppid: int) -> object | None:
    """Return a wait token for ``ppid`` (live now), or None if it cannot be opened.

    Windows: a kernel32 handle opened with SYNCHRONIZE. POSIX: the initial ppid as
    a sentinel, or None when already reparented to init (ppid <= 1).
    """
    if not ppid:
        return None
    if not _IS_WINDOWS:  # pragma: no cover
        return ppid if int(ppid) > 1 else None
    handle = _k32.OpenProcess(_SYNCHRONIZE, False, int(ppid))
    return handle if handle else None


def wait_for_parent_exit(handle: object) -> int:
    """Block until the parent referenced by ``handle`` exits."""
    if not _IS_WINDOWS:  # pragma: no cover
        initial = int(handle)  # POSIX sentinel is the original ppid.
        while os.getppid() == initial:
            time.sleep(0.5)
        return _WAIT_OBJECT_0
    result = _k32.WaitForSingleObject(handle, _INFINITE)
    _k32.CloseHandle(handle)
    return int(result)


def is_pid_alive(pid: int | None) -> bool:
    """True if ``pid`` is a live process. Indeterminate access errors read as alive
    so the reclaim discriminator stays fail-closed (never kills on ambiguity)."""
    if not pid:
        return False
    if not _IS_WINDOWS:  # pragma: no cover
        try:
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True
    handle = _k32.OpenProcess(_SYNCHRONIZE, False, int(pid))
    if not handle:
        return ctypes.get_last_error() != _ERROR_INVALID_PARAMETER
    try:
        return _k32.WaitForSingleObject(handle, 0) != _WAIT_OBJECT_0
    finally:
        _k32.CloseHandle(handle)


def _toolhelp_lookup(pid: int) -> tuple[int, str] | None:
    """Return (parent_pid, image_name) for ``pid`` from a ToolHelp32 snapshot."""
    if not _IS_WINDOWS:  # pragma: no cover
        return None
    snapshot = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return None
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        ok = _k32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            if entry.th32ProcessID == int(pid):
                return int(entry.th32ParentProcessID), entry.szExeFile.decode("ascii", "replace")
            ok = _k32.Process32Next(snapshot, ctypes.byref(entry))
        return None
    finally:
        _k32.CloseHandle(snapshot)


def _retail_snapshot_from_entries(
    entries: list[tuple[int, str]],
) -> dict[str, object]:
    return _named_snapshot_from_entries(entries, ["DayZ_BE.exe", "DayZ_x64.exe"])


def _named_snapshot_from_entries(
    entries: list[tuple[int, str]], names: list[str]
) -> dict[str, object]:
    exact_names = {name.casefold() for name in names}
    processes = [
        {"pid": int(pid), "name": name}
        for pid, name in entries
        if isinstance(name, str) and name.casefold() in exact_names
    ]
    processes.sort(key=lambda item: int(item["pid"]))
    return {"known": True, "processes": processes}


def _snapshot_all_process_entries() -> list[tuple[int, str]] | None:
    if not _IS_WINDOWS:  # pragma: no cover - production target is Windows.
        return None
    snapshot = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return None
    entries: list[tuple[int, str]] = []
    failed = False
    close_ok = False
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        ctypes.set_last_error(0)
        ok = _k32.Process32First(snapshot, ctypes.byref(entry))
        if not ok:
            failed = True
        else:
            while True:
                entries.append(
                    (
                        int(entry.th32ProcessID),
                        entry.szExeFile.decode("ascii", "replace"),
                    )
                )
                ctypes.set_last_error(0)
                ok = _k32.Process32Next(snapshot, ctypes.byref(entry))
                if not ok:
                    if ctypes.get_last_error() != _ERROR_NO_MORE_FILES:
                        failed = True
                    break
    except Exception:
        failed = True
    finally:
        try:
            close_ok = bool(_k32.CloseHandle(snapshot))
        except Exception:
            close_ok = False
    if failed or not close_ok:
        return None
    return entries


def snapshot_processes_by_name(names: list[str]) -> dict[str, object]:
    """Return exact case-insensitive ToolHelp matches as PID/name observations only."""
    if (
        not isinstance(names, list)
        or not names
        or any(not isinstance(name, str) or not name for name in names)
    ):
        raise ValueError("invalid_process_names")
    entries = _snapshot_all_process_entries()
    if entries is None:
        return {"known": False, "processes": []}
    return _named_snapshot_from_entries(entries, names)


def snapshot_retail_processes() -> dict[str, object]:
    """Read-only retail wrapper; exact-name observation never attributes ownership."""
    return snapshot_processes_by_name(["DayZ_BE.exe", "DayZ_x64.exe"])


def parent_pid_of(pid: int) -> int | None:
    info = _toolhelp_lookup(pid)
    return info[0] if info else None


def image_name_of(pid: int) -> str | None:
    info = _toolhelp_lookup(pid)
    return info[1] if info else None


def walk_past_redirectors(
    first_ppid: int | None,
    redirector_path: str | None,
    *,
    get_parent: Callable[[int], int | None] | None = None,
    get_full_path: Callable[[int], str | None] | None = None,
    max_depth: int = 8,
) -> int | None:
    """Climb from ``first_ppid`` past any ancestor whose image is our own venv
    launcher (``redirector_path``) and return the first non-launcher ancestor.

    On Windows the venv ``Scripts\\python.exe`` is a launcher that spawns the base
    interpreter as a child, so the server's immediate parent is that launcher. The
    launcher OUTLIVES Claude Code (it only waits on its child) and never trips a
    watchdog bound to it, and reclaim reads it as a live parent — that is C1.
    Skipping the launcher layer makes the watchdog watch the real ancestor
    (Claude/node) and the reclaim discriminator judge the real ancestor's liveness.

    Returns None when the chain cannot be resolved (unknown parent / exhausted
    depth) so callers fail closed. Reduces to ``first_ppid`` when there is no
    launcher layer (an ancestor whose path is unknown is treated as non-launcher).
    """
    get_parent = get_parent or parent_pid_of
    get_full_path = get_full_path or full_image_path_of
    current = first_ppid
    for _ in range(max_depth):
        if not current:
            return None
        path = get_full_path(current)
        if path is not None and _same_path(path, redirector_path):
            current = get_parent(current)  # our launcher: climb past it
            continue
        return current  # first ancestor that is not our launcher (or path unknown)
    return None


def _decode_console_bytes(raw: bytes) -> str:
    """Decode console/OEM bytes without raising on undecodable octets."""
    encoding = locale.getpreferredencoding(False) or "utf-8"
    return raw.decode(encoding, errors="replace")


def _listener_pid_from_netstat_output(raw: bytes | str, port: int) -> int | None:
    """Parse a ``netstat -ano -p TCP`` dump for the 127.0.0.1 listener PID."""
    if isinstance(raw, bytes):
        text = _decode_console_bytes(raw)
    else:
        text = raw
    local_endpoint = "127.0.0.1:" + str(int(port))
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[3] != "LISTENING":
            continue
        if parts[1] != local_endpoint:
            continue
        for token in reversed(parts[4:]):
            try:
                return int(token)
            except ValueError:
                continue
    return None


def listener_pid_for_port(port: int) -> int | None:
    """PID of the process LISTENING on ``port`` via ``netstat -ano`` (None if none).

    netstat row layout (verified): ``Proto  Local  Foreign  State  PID`` — the PID
    is the last whitespace-separated column and the local address ends with ``:port``.
    Console bytes are decoded with ``errors='replace'`` so OEM code-page output
    cannot raise ``UnicodeDecodeError`` under UTF-8 mode.
    """
    if not _IS_WINDOWS:  # pragma: no cover
        return None
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return _listener_pid_from_netstat_output(result.stdout or b"", port)


def _native_listener_pid_for_port(port: int) -> int | None:
    """Unique exact-loopback listener PID without starting a helper process."""
    if (
        psutil is None
        or not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65535
    ):
        return None
    try:
        connections = psutil.net_connections(kind="tcp")
    except Exception:
        return None
    listeners: set[int] = set()
    for connection in connections:
        if getattr(connection, "status", None) != psutil.CONN_LISTEN:
            continue
        local = getattr(connection, "laddr", None)
        pid = getattr(connection, "pid", None)
        if (
            getattr(local, "ip", None) != "127.0.0.1"
            or getattr(local, "port", None) != port
            or not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
        ):
            continue
        listeners.add(pid)
    return next(iter(listeners)) if len(listeners) == 1 else None


def _port_bindable(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def wait_port_free(port: int, timeout: float = 3.0, interval: float = 0.05) -> bool:
    """Poll an exclusive probe-bind until ``port`` is free (no SO_REUSEADDR)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_bindable(port):
            return True
        time.sleep(interval)
    return _port_bindable(port)


# ---------------------------------------------------------------------------
# Part 1 — parent-death watchdog.
# ---------------------------------------------------------------------------

def decide_watchdog_action(parent_handle: object | None) -> str:
    """Pure decision: ARMED when a live parent handle was captured, DISABLED when
    the parent could not be opened (already gone / no handle) — never exit on a
    missing handle, that would tear down a healthy startup."""
    return DISABLED if parent_handle is None else ARMED


def _watch_loop(
    handle: object,
    wait_fn: Callable[[object], int],
    on_parent_death: Callable[[], None],
    log: Callable[[str], None],
) -> None:
    result = wait_fn(handle)
    if result == _WAIT_OBJECT_0:
        on_parent_death()
        return
    if result == _WAIT_FAILED:
        log("WATCHDOG: parent wait returned WAIT_FAILED; watchdog disabled")
        return
    log(f"WATCHDOG: parent wait returned {result}; watchdog disabled")


def install_parent_death_watchdog(
    on_parent_death: Callable[[], None],
    *,
    log: Callable[[str], None] = _noop_log,
    open_handle: Callable[[int], object | None] | None = None,
    wait_for_exit: Callable[[object], int] | None = None,
    image_name: Callable[[int], str | None] | None = None,
    get_parent: Callable[[int], int | None] | None = None,
    get_full_path: Callable[[int], str | None] | None = None,
    self_exe: str | None = None,
) -> str:
    """Arm a daemon thread that runs ``on_parent_death`` when the real ancestor
    process exits. Returns ARMED or DISABLED. Helpers are injectable for testing.

    The immediate parent is the venv launcher, which outlives Claude Code, so the
    watchdog walks past launcher layers and watches the first real ancestor (C1).
    """
    open_handle = open_handle or open_parent_wait_handle
    wait_for_exit = wait_for_exit or wait_for_parent_exit
    image_name = image_name or image_name_of
    self_exe = self_exe if self_exe is not None else sys.executable

    ppid = os.getppid()
    try:
        name = image_name(ppid) or "?"
    except Exception:
        name = "?"
    log(f"LOG: parent pid={ppid} name={name}")

    # The immediate parent is our venv launcher (it outlives Claude Code and would
    # never trip the watchdog); watch the first ancestor that is not our launcher.
    target = walk_past_redirectors(
        ppid, self_exe, get_parent=get_parent, get_full_path=get_full_path
    )
    if not target:
        target = ppid
    elif target != ppid:
        log(f"WATCHDOG: parent pid={ppid} is our venv launcher; watching real ancestor pid={target}")

    handle = open_handle(target)
    action = decide_watchdog_action(handle)
    if action == DISABLED:
        log("WATCHDOG: parent handle unavailable, watchdog disabled")
        return action

    thread = threading.Thread(
        target=_watch_loop,
        args=(handle, wait_for_exit, on_parent_death, log),
        name="parent-death-watchdog",
        daemon=True,
    )
    thread.start()
    log(f"WATCHDOG: armed on parent pid={target}")
    return action


# ---------------------------------------------------------------------------
# Part 2 — port reclaim discriminator + orchestration.
# ---------------------------------------------------------------------------

def try_reclaim_port(
    port: int,
    *,
    log: Callable[[str], None] = _noop_log,
    find_listener: Callable[[int], int | None] | None = None,
    get_image: Callable[[int], str | None] | None = None,
    get_argv: Callable[[int], list[str] | None] | None = None,
    get_parent: Callable[[int], int | None] | None = None,
    get_full_path: Callable[[int], str | None] | None = None,
    is_alive: Callable[[int | None], bool] | None = None,
    guard: object | None = None,
    wait_free: Callable[[int], bool] | None = None,
    is_responsive: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    self_exe: str | None = None,
    expected_executable: str | None = None,
    expected_argv: list[str] | None = None,
) -> bool:
    """Reclaim ``port`` from a confirmed-dead-ancestor ``dayz_mcp`` orphan.

    Returns True only when an orphan was found, terminated, and the port came free.
    Never terminates this process or a live instance. Liveness is judged on the
    listener's real ancestor (past the venv launcher layer, C1), not its immediate
    parent. Helpers are injectable so the discriminator and kill/free path can be
    exercised deterministically in tests.

    Ancestry is necessary but NOT sufficient (BUG-070): a holder that still answers
    HTTP is preserved even when its ancestor is gone, mirroring the daemon guard in
    Part 2b. The two discriminators stay separate per mode (LL-156) -- health is an
    additional AND here, never a replacement for the ancestry test.
    """
    from dayz_mcp.native_process_guard import NativeProcessGuard, identity_hashes
    from dayz_mcp.process_lifecycle import ProcessLifecycle

    find_listener = find_listener or listener_pid_for_port
    get_image = get_image or image_name_of
    get_argv = get_argv or command_argv_of
    get_parent = get_parent or parent_pid_of
    get_full_path = get_full_path or full_image_path_of
    is_alive = is_alive or is_pid_alive
    native_guard = guard or NativeProcessGuard()
    wait_free = wait_free or wait_port_free
    is_responsive = is_responsive or (lambda: probe_listener_responsive(port))
    self_exe = self_exe if self_exe is not None else sys.executable

    if expected_executable is None or expected_argv is None:
        return False
    try:
        expected_hashes = identity_hashes(expected_executable, expected_argv)
    except (TypeError, ValueError):
        return False
    if (
        classify_dayz_argv(expected_argv) != "legacy_embedded"
        or not argv_targets_port(expected_argv, port)
    ):
        return False

    pid_a = find_listener(port)
    if not isinstance(pid_a, int) or isinstance(pid_a, bool) or pid_a <= 0:
        log(f"RECLAIM: port {port} in use but no LISTENING pid found; not reclaiming")
        return False
    if pid_a == os.getpid():
        return False  # Never reclaim the current process.

    image = get_image(pid_a) or ""
    image_name = ntpath.basename(image).casefold()
    if not (image_name.startswith("python") and image_name.endswith(".exe")):
        return False
    observed_argv = get_argv(pid_a)
    if observed_argv is None:
        log(f"RECLAIM: pid={pid_a} image={image} argv unavailable; preserving E4")
        return False
    if (
        classify_dayz_argv(observed_argv) != "legacy_embedded"
        or observed_argv != expected_argv
    ):
        return False
    try:
        snapshot_a = native_guard.snapshot(pid_a)
    except Exception:
        return False
    immediate_parent = get_parent(pid_a)
    if immediate_parent is None:
        log(f"RECLAIM: pid={pid_a} image={image} parent_pid unavailable; preserving E4 lock")
        return False
    # The listener's immediate parent is the venv launcher, which outlives Claude
    # Code (C1); judge liveness on the real ancestor so a dead-Claude orphan whose
    # launcher is still alive is reclaimed, while a live session is preserved.
    ancestor = walk_past_redirectors(
        immediate_parent, self_exe, get_parent=get_parent, get_full_path=get_full_path
    )
    if ancestor is None:
        log(f"RECLAIM: pid={pid_a} image={image} true ancestor unresolved; preserving E4 lock")
        return False
    parent_alive = is_alive(ancestor)

    if parent_alive:
        log(
            f"RECLAIM: pid={pid_a} image={image} parent_pid={immediate_parent} ancestor={ancestor} "
            f"parent_alive={parent_alive} is not a reclaimable orphan; preserving E4 lock"
        )
        return False

    # BUG-070: ancestry alone decided the kill, so an orphan that was STILL SERVING
    # got terminated -- the symptom was a healthy endpoint dying and the bridge then
    # talking to a differently-keyed holder (poll error=5, blind backoff). The daemon
    # path never had this hole: a holder answering ANY HTTP status reads as alive.
    # Probed only here, after identity and ancestry already selected a victim, so the
    # cost is paid solely when we were about to kill. Two probes 500 ms apart, like
    # the daemon, because a single short probe misreads a loaded-but-live server.
    for attempt in range(2):
        try:
            holder_responsive = is_responsive()
        except Exception:
            return False  # fail-closed: an unusable probe never authorizes a kill
        if holder_responsive:
            log(
                f"RECLAIM: pid={pid_a} ancestor={ancestor} dead but holder still answers "
                f"HTTP (attempt {attempt + 1}/2); preserving it"
            )
            return False
        if attempt == 0:
            sleep(0.5)

    pid_b = find_listener(port)
    if pid_b != pid_a:
        return False
    try:
        snapshot_b = native_guard.snapshot(pid_b)
    except Exception:
        return False
    identity_fields = (
        "pid",
        "creation_time_utc",
        "executable_sha256",
        "command_line_sha256",
        "identity_scheme",
    )
    if (
        not isinstance(snapshot_a, dict)
        or not isinstance(snapshot_b, dict)
        or snapshot_a.get("identity_complete") is not True
        or snapshot_b.get("identity_complete") is not True
        or snapshot_a.get("identity_scheme") != "psutil-argv-v2"
        or any(snapshot_a.get(field) != snapshot_b.get(field) for field in identity_fields)
        or snapshot_b.get("pid") != pid_a
        or snapshot_b.get("executable_sha256") != expected_hashes["executable_sha256"]
        or snapshot_b.get("command_line_sha256")
        != expected_hashes["command_line_sha256"]
    ):
        return False
    record = ProcessLifecycle._record_from_snapshot(snapshot_b, "embedded")
    if record is None or record.role not in {"embedded"}:
        return False
    log(f"RECLAIM: terminating orphan dayz_mcp pid={pid_a} (ancestor {ancestor} dead) holding port {port}")
    try:
        termination = native_guard.terminate(record)
    except Exception:
        return False
    if not isinstance(termination, dict) or termination.get("terminated") is not True:
        log(f"RECLAIM: failed to terminate pid={pid_a}")
        return False
    try:
        freed = wait_free(port)
    except Exception:
        freed = False
    log(f"RECLAIM: port {port} freed={freed} after terminating pid={pid_a}")
    return freed


# ---------------------------------------------------------------------------
# Part 2b — daemon discovery + health-gated reclaim.
#
# A broker daemon is MEANT to outlive its spawning session, so parent-liveness
# (Part 2) cannot decide whether to reclaim it — a healthy detached daemon has a
# dead spawner and would be wrongly killed. The daemon discriminator is HEALTH:
# never touch a holder that answers /status; reclaim only an unresponsive one.
# ---------------------------------------------------------------------------

def _status_payload_is_exact_daemon(
    payload: object,
    *,
    expected_generation: str | None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    marker = payload.get("daemon_status")
    coordination = payload.get("coordination")
    generation = payload.get("daemon_generation")
    if (
        not isinstance(marker, dict)
        or set(marker) != DAEMON_STATUS_MARKER_KEYS
        or marker.get("schema") != DAEMON_STATUS_SCHEMA
        or marker.get("product") != "dayz_mcp"
        or marker.get("mode") != "daemon"
        or not isinstance(generation, str)
        or not generation
        or generation != generation.strip()
        or len(generation) > 128
        or marker.get("daemon_generation") != generation
        or (expected_generation is not None and generation != expected_generation)
        or not isinstance(coordination, dict)
    ):
        return False
    revision = coordination.get("revision")
    return (
        isinstance(revision, int)
        and not isinstance(revision, bool)
        and revision >= 0
        and marker.get("coordination_revision") == revision
    )


def _decode_status_json(body: bytes) -> object:
    if len(body) > MAX_STATUS_BODY_BYTES:
        raise ValueError("daemon_status_too_large")

    def closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("daemon_status_duplicate_key")
            result[name] = value
        return result

    return json.loads(body.decode("utf-8"), object_pairs_hook=closed_object)


def probe_status_healthy(
    port: int,
    key: str,
    *,
    deadline: float,
    host: str = "127.0.0.1",
    expected_generation: str | None = None,
    expected_executable: str,
    expected_argv: list[str],
    expected_cwd: str,
    request_fn: Callable[..., tuple[int, bytes]] | None = None,
) -> bool:
    """Strict authenticated daemon discovery, fail-closed before key disclosure."""
    if (
        host != "127.0.0.1"
        or not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65535
        or not isinstance(key, str)
        or not key
        or not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(float(deadline))
        or (expected_generation is not None and not expected_generation)
    ):
        return False
    request = request_fn or verified_daemon_http_request
    try:
        status, body = request(
            host=host,
            port=port,
            key=key,
            method="GET",
            path="/status",
            query={},
            body=None,
            headers={},
            deadline=float(deadline),
            expected_executable=expected_executable,
            expected_argv=expected_argv,
            expected_cwd=expected_cwd,
            max_response_bytes=MAX_STATUS_BODY_BYTES,
        )
        if not 200 <= status < 300:
            return False
        payload = _decode_status_json(body)
        return _status_payload_is_exact_daemon(
            payload, expected_generation=expected_generation
        )
    except Exception:
        return False


def probe_listener_responsive(
    port: int,
    *,
    timeout: float = 1.0,
    host: str = "127.0.0.1",
) -> bool:
    """True if something answers an HTTP request on ``port`` with ANY status code,
    i.e. a LIVE HTTP server — even one we cannot authenticate against.

    Distinct from ``probe_status_healthy`` (key-specific, demands 2xx): this treats a
    401 (a daemon answering with a DIFFERENT key) as ALIVE. Used only by the daemon
    reclaim guard so it never kills a daemon that is provably running but foreign-keyed
    (B-1) or one whose serve loop is momentarily slow. Only a refused connection /
    timeout / non-HTTP reply reads as False.
    """
    url = f"http://{host}:{int(port)}/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 100 <= int(response.status) < 600
    except urllib.error.HTTPError as exc:
        exc.close()  # answered (e.g. 401 foreign key) → a live server; do not reclaim
        return True
    except Exception:
        return False  # refused / timeout / not-yet-serving / non-HTTP → not responsive


def try_reclaim_unresponsive_listener(
    port: int,
    *,
    deadline: float,
    is_healthy: Callable[[], bool],
    log: Callable[[str], None] = _noop_log,
    find_listener: Callable[[int], int | None] | None = None,
    get_image: Callable[[int], str | None] | None = None,
    get_argv: Callable[[int], list[str] | None] | None = None,
    guard: object | None = None,
    wait_free: Callable[[int], bool] | None = None,
    is_responsive: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.monotonic,
    expected_executable: str,
    expected_argv: list[str],
) -> bool:
    """Reclaim ``port`` from an UNRESPONSIVE ``dayz_mcp`` daemon orphan.

    Health, not ancestry, is the discriminator (a daemon outlives its spawner).
    Reclaims only after two failed probes 500 ms apart, stable listener ownership,
    exact structured argv policy, and two identical native v2 snapshots.
    Returns True only when an orphan was terminated and the port came free.

    Three live-holder states are preserved (never killed), fail-closed:
    - healthy to us (``is_healthy`` answers /status 2xx);
    - alive but foreign-keyed (``is_responsive`` sees any HTTP reply incl. 401) — B-1;
    - a freshly-elected winner whose serve loop has not started answering yet, or a
      daemon momentarily busy — it flips to responsive within the re-probe window
      (A-1 / B-2). Only an all-attempts-failed holder is an orphan.

    Read-only helpers and sleep are injectable for tests; termination always goes
    through ``NativeProcessGuard.terminate`` with a complete expected record.
    """
    from dayz_mcp.native_process_guard import NativeProcessGuard, identity_hashes
    from dayz_mcp.process_lifecycle import ProcessLifecycle

    find_listener = find_listener or _native_listener_pid_for_port
    get_image = get_image or image_name_of
    get_argv = get_argv or command_argv_of
    native_guard = guard or NativeProcessGuard()
    if (
        not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(float(deadline))
        or float(deadline) <= time_fn()
    ):
        return False
    wait_free = wait_free or (
        lambda candidate_port: wait_port_free(
            candidate_port,
            timeout=max(0.0, min(3.0, float(deadline) - time_fn())),
        )
    )
    is_responsive = is_responsive or (lambda: probe_listener_responsive(port))

    if expected_executable is None or expected_argv is None:
        return False
    try:
        expected_hashes = identity_hashes(expected_executable, expected_argv)
    except (TypeError, ValueError):
        return False
    expected_type = classify_dayz_argv(expected_argv)
    if expected_type not in {"normal_daemon", "p0s_bootstrap_daemon"}:
        return False
    if not argv_targets_port(expected_argv, port):
        return False

    for attempt in range(2):
        if time_fn() >= float(deadline):
            return False
        try:
            responsive = is_healthy() or is_responsive()
        except Exception:
            return False
        if responsive:
            log(
                f"RECLAIM(daemon): port {port} holder responsive "
                f"(attempt {attempt + 1}/2); preserving it"
            )
            return False
        if attempt == 0:
            remaining = float(deadline) - time_fn()
            if remaining <= 0.0:
                return False
            sleep(min(0.5, remaining))

    if time_fn() >= float(deadline):
        return False
    pid_a = find_listener(port)
    if not isinstance(pid_a, int) or isinstance(pid_a, bool) or pid_a <= 0:
        log(f"RECLAIM(daemon): port {port} in use but no LISTENING pid found; not reclaiming")
        return False
    if pid_a == os.getpid():
        return False

    image = get_image(pid_a) or ""
    image_name = ntpath.basename(image).casefold()
    if not (image_name.startswith("python") and image_name.endswith(".exe")):
        log(f"RECLAIM(daemon): pid={pid_a} image={image} not python; preserving E4 lock")
        return False
    observed_argv = get_argv(pid_a)
    if observed_argv is None:
        log(f"RECLAIM(daemon): pid={pid_a} image={image} argv unavailable; preserving E4 lock")
        return False
    if classify_dayz_argv(observed_argv) != expected_type or observed_argv != expected_argv:
        log(f"RECLAIM(daemon): pid={pid_a} policy mismatch; preserving E4 lock")
        return False

    try:
        snapshot_a = native_guard.snapshot(pid_a)
    except Exception:
        return False
    if time_fn() >= float(deadline):
        return False
    pid_b = find_listener(port)
    if pid_b != pid_a:
        return False
    try:
        snapshot_b = native_guard.snapshot(pid_b)
    except Exception:
        return False
    identity_fields = (
        "pid",
        "creation_time_utc",
        "executable_sha256",
        "command_line_sha256",
        "identity_scheme",
    )
    if (
        not isinstance(snapshot_a, dict)
        or not isinstance(snapshot_b, dict)
        or snapshot_a.get("identity_complete") is not True
        or snapshot_b.get("identity_complete") is not True
        or snapshot_a.get("identity_scheme") != "psutil-argv-v2"
        or any(snapshot_a.get(field) != snapshot_b.get(field) for field in identity_fields)
        or snapshot_b.get("pid") != pid_a
        or snapshot_b.get("executable_sha256") != expected_hashes["executable_sha256"]
        or snapshot_b.get("command_line_sha256")
        != expected_hashes["command_line_sha256"]
    ):
        return False
    record = ProcessLifecycle._record_from_snapshot(snapshot_b, "daemon")
    if record is None or record.role not in {"daemon"}:
        return False
    if time_fn() >= float(deadline):
        return False
    log(
        f"RECLAIM(daemon): terminating unresponsive dayz_mcp pid={pid_a} "
        f"holding port {port}"
    )
    try:
        termination = native_guard.terminate(record)
    except Exception:
        return False
    if not isinstance(termination, dict) or termination.get("terminated") is not True:
        log(f"RECLAIM(daemon): failed to terminate pid={pid_a}")
        return False
    if time_fn() >= float(deadline):
        return False
    try:
        freed = wait_free(port)
    except Exception:
        freed = False
    log(f"RECLAIM(daemon): port {port} freed={freed} after terminating pid={pid_a}")
    return freed


# ---------------------------------------------------------------------------
# Part 3 — idle self-shutdown watchdog.
# ---------------------------------------------------------------------------

def compute_idle_seconds(
    now: float,
    start: float,
    last_tool: float | None,
    last_server_poll: float | None,
    last_client_poll: float | None,
) -> float:
    """Seconds since the most recent sign of life.

    Activity is the latest of: process start (baseline grace), the last MCP tool
    call, and the last server/client bridge poll. A session driving DayZ polls the
    loopback every ~0.2s, so the bridge never looks idle while a game is attached;
    idle only grows for an abandoned bridge with no game and no tool calls. All
    timestamps are time.monotonic() so they are directly comparable.
    """
    last = start
    for stamp in (last_tool, last_server_poll, last_client_poll):
        if stamp is not None and stamp > last:
            last = stamp
    return max(0.0, now - last)


def install_idle_watchdog(
    idle_seconds: Callable[[], float],
    timeout_s: float,
    on_idle: Callable[[], None],
    *,
    log: Callable[[str], None] = _noop_log,
    poll_interval: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    stop: "threading.Event | None" = None,
) -> str:
    """Arm a daemon thread that runs ``on_idle`` once the bridge has been idle for
    ``timeout_s`` seconds. Returns ARMED, or DISABLED when ``timeout_s`` <= 0.

    Complements the parent-death watchdog: that one fires when the owning Claude
    process EXITS; this one fires when the owner is still alive but hung/abandoned
    (no stdin EOF, never exits) so the loopback would otherwise hold the port
    forever. A LIVE, actively-used session keeps idle below the threshold via tool
    calls or game polls, so this never tears down a working session. ``idle_seconds``,
    ``sleep`` and ``stop`` are injectable so the loop is exercised deterministically.
    """
    if timeout_s <= 0:
        log("IDLE-WATCHDOG: disabled (timeout<=0)")
        return DISABLED

    def _loop() -> None:
        while stop is None or not stop.is_set():
            idle = idle_seconds()
            if idle >= timeout_s:
                log(f"IDLE-WATCHDOG: idle {idle:.0f}s >= {timeout_s:.0f}s; releasing port and exiting")
                on_idle()
                return
            sleep(poll_interval)

    thread = threading.Thread(target=_loop, name="idle-watchdog", daemon=True)
    thread.start()
    log(f"IDLE-WATCHDOG: armed timeout={timeout_s:.0f}s poll={poll_interval:.0f}s")
    return ARMED
