"""``window_close.Win32WindowFns`` off Windows, for ``dayz_test_close``.

``ProcessLifecycle`` closes a run by listing the windows of its PIDs and posting
``WM_CLOSE`` to each, with its identity checks around every post. On Linux a run has
no Win32 windows to enumerate from the daemon, but each role has an equivalent:

- a client (``proton_client``) is a view in the headless sway of its session; sway's
  ``kill`` asks the view to close, and Wine turns that into the game's window close;
- a server (``podman_server``) has no window; it handles SIGTERM as an orderly
  shutdown. Measured 2026-11-06 on DayZServer 1.30 as the container's PID 1: exit
  after 11.5 s, RPT ending ``--- Termination successfully completed ---``.

A handle is opaque to the caller: ``_SERVER | pid``, or ``sway_pid << 32 | con_id``.
Nothing here decides what may be closed; ``list_visible_top_level_windows`` keeps only
the PIDs the lifecycle asked for.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterator

from dayz_mcp.input_activity import Win32InputFns
from dayz_mcp.window_close import WM_CLOSE, Win32WindowFns

SERVER_IMAGE = "DayZServer"
_SERVER = 1 << 62
_CON_ID_MASK = (1 << 32) - 1
_SWAYMSG_TIMEOUT_S = 5.0


def _sway_pids() -> list[int]:
    from dayz_mcp import proton_client

    try:
        root = proton_client._state_root()
        entries = list(root.iterdir())
    except (OSError, proton_client.ProtonClientError):
        return []
    pids = []
    for entry in entries:
        if entry.name.startswith(proton_client.SESSION_PREFIX):
            pid = proton_client._read_pid(entry / "sway.pid")
            if pid is not None and proton_client._alive(pid):
                pids.append(pid)
    return pids


def _socket(sway_pid: int) -> str:
    # sway's default: $XDG_RUNTIME_DIR/sway-ipc.<uid>.<pid>.sock
    return str(Path(os.environ.get("XDG_RUNTIME_DIR", "")) / f"sway-ipc.{os.getuid()}.{sway_pid}.sock")


def _swaymsg(sway_pid: int, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["swaymsg", "-s", _socket(sway_pid), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_SWAYMSG_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _views(node: dict) -> Iterator[dict]:
    if node.get("type") in {"con", "floating_con"} and isinstance(node.get("pid"), int):
        yield node
    for key in ("nodes", "floating_nodes"):
        for child in node.get(key) or ():
            yield from _views(child)


def _session_views(sway_pid: int) -> list[dict]:
    done = _swaymsg(sway_pid, "-t", "get_tree", "-r")
    if done is None or done.returncode != 0:
        return []
    try:
        return list(_views(json.loads(done.stdout)))
    except (ValueError, AttributeError):
        return []


def _server_pids() -> list[int]:
    from dayz_mcp import orphan_guard

    snapshot = orphan_guard.snapshot_processes_with_parent([SERVER_IMAGE])
    return [int(row["pid"]) for row in snapshot.get("processes") or ()]


def _view_of(handle: int) -> dict | None:
    sway_pid, con_id = handle >> 32, handle & _CON_ID_MASK
    return next((view for view in _session_views(sway_pid) if view.get("id") == con_id), None)


def enum_windows(callback: Callable[[int], bool]) -> bool:
    for pid in _server_pids():
        if not callback(_SERVER | pid):
            return True
    for sway_pid in _sway_pids():
        for view in _session_views(sway_pid):
            if not callback(sway_pid << 32 | int(view["id"])):
                return True
    return True


def window_pid(handle: int) -> int:
    if handle & _SERVER:
        return handle & ~_SERVER
    view = _view_of(handle)
    return int(view["pid"]) if view is not None else 0


def is_visible(handle: int) -> bool:
    if handle & _SERVER:
        return True
    view = _view_of(handle)
    return view is not None and view.get("visible") is True


def post_message(handle: int, msg: int, _wparam: int, _lparam: int) -> bool:
    if msg != WM_CLOSE:
        return False
    if handle & _SERVER:
        try:
            os.kill(handle & ~_SERVER, signal.SIGTERM)
        except OSError:
            return False
        return True
    done = _swaymsg(handle >> 32, f"[con_id={handle & _CON_ID_MASK}]", "kill")
    return done is not None and done.returncode == 0


def input_fns() -> Win32InputFns:
    """``input_activity`` reads: nobody can give input to a managed game on Linux.

    Every managed game runs in a headless sway without input devices, so "no input and
    no foreground window" is the true reading, not a doubt. Without it every run with a
    client reads ``use_state`` unknown and no other session can ever adopt it.
    """

    def tick_count() -> int:
        return int(time.monotonic() * 1000) & 0xFFFFFFFF

    started = tick_count()
    return Win32InputFns(
        last_input_tick=lambda: started,
        tick_count=tick_count,
        foreground_pid=lambda: None,
    )


def install() -> None:
    """The daemon's window and input reads off Windows."""
    from dayz_mcp import input_activity, window_close

    window_close._REAL = fns()
    input_activity.real_win32 = input_fns


def fns() -> Win32WindowFns:
    return Win32WindowFns(
        enum_windows=enum_windows,
        window_pid=window_pid,
        is_visible=is_visible,
        post_message=post_message,
    )
