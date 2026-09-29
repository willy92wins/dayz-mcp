"""Post WM_CLOSE to visible top-level windows of given PIDs.

Never SendMessage, never terminate, never kill. Win32 callables are
injectable so tests can drive the helper without real windows.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

WM_CLOSE = 0x0010

EnumWindowsFn = Callable[[Callable[[int], bool]], bool]
WindowPidFn = Callable[[int], int]
IsVisibleFn = Callable[[int], bool]
PostMessageFn = Callable[[int, int, int, int], bool]


@dataclass(frozen=True)
class Win32WindowFns:
    enum_windows: EnumWindowsFn
    window_pid: WindowPidFn
    is_visible: IsVisibleFn
    post_message: PostMessageFn


_REAL: Win32WindowFns | None = None


def bind_real_win32() -> Win32WindowFns:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL

    def enum_windows(callback: Callable[[int], bool]) -> bool:
        @enum_proc
        def _cb(hwnd: int, _lparam: int) -> bool:
            return bool(callback(int(hwnd)))

        return bool(user32.EnumWindows(_cb, 0))

    def window_pid(hwnd: int) -> int:
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def is_visible(hwnd: int) -> bool:
        return bool(user32.IsWindowVisible(hwnd))

    def post_message(hwnd: int, msg: int, wparam: int, lparam: int) -> bool:
        return bool(user32.PostMessageW(hwnd, msg, wparam, lparam))

    return Win32WindowFns(
        enum_windows=enum_windows,
        window_pid=window_pid,
        is_visible=is_visible,
        post_message=post_message,
    )


def real_win32() -> Win32WindowFns:
    global _REAL
    if _REAL is None:
        _REAL = bind_real_win32()
    return _REAL


def list_visible_top_level_windows(
    pids: Iterable[int],
    *,
    fns: Win32WindowFns | None = None,
) -> list[tuple[int, int]]:
    """Return (hwnd, pid) for visible top-level windows owned by ``pids``."""
    allowed = {int(pid) for pid in pids}
    found: list[tuple[int, int]] = []
    api = fns if fns is not None else real_win32()

    def _callback(hwnd: int) -> bool:
        pid = int(api.window_pid(hwnd))
        if pid in allowed and api.is_visible(hwnd):
            found.append((int(hwnd), pid))
        return True

    api.enum_windows(_callback)
    return found


def post_wm_close(
    hwnd: int,
    *,
    fns: Win32WindowFns | None = None,
    expected_pid: int | None = None,
    before_post: Callable[[], bool] | None = None,
) -> bool:
    """Queue WM_CLOSE with PostMessageW. Never SendMessage.

    ``before_post`` runs after the Win32 PID read and immediately before
    PostMessageW. Nothing else runs between that callback and the syscall.
    A result other than True does not post. Callers that omit it keep the
    previous PID check and post.
    """
    api = fns if fns is not None else real_win32()
    observed: int | None = None
    if expected_pid is not None or before_post is not None:
        observed = int(api.window_pid(hwnd))
    if before_post is not None and before_post() is not True:
        return False
    if expected_pid is not None and observed != int(expected_pid):
        return False
    return bool(api.post_message(int(hwnd), WM_CLOSE, 0, 0))


def close_visible_windows(
    pids: Iterable[int],
    *,
    fns: Win32WindowFns | None = None,
    before_post: Callable[[int], bool] | None = None,
) -> dict[int, dict[str, int]]:
    """List visible top-level windows per PID and post WM_CLOSE.

    ``before_post(pid)`` runs immediately before each post; a false
    result skips that window. Returns windows_found and windows_posted
    per PID.
    """
    ordered = [int(pid) for pid in pids]
    unique = list(dict.fromkeys(ordered))
    api = fns if fns is not None else real_win32()
    listed = list_visible_top_level_windows(unique, fns=api)
    result: dict[int, dict[str, int]] = {
        pid: {"windows_found": 0, "windows_posted": 0} for pid in unique
    }
    for hwnd, pid in listed:
        row = result.setdefault(pid, {"windows_found": 0, "windows_posted": 0})
        row["windows_found"] += 1
        if before_post is not None and not before_post(pid):
            continue
        if post_wm_close(hwnd, fns=api, expected_pid=pid):
            row["windows_posted"] += 1
    return result


def empty_pid_result(pids: Iterable[int]) -> dict[int, Mapping[str, int]]:
    return {int(pid): {"windows_found": 0, "windows_posted": 0} for pid in pids}
