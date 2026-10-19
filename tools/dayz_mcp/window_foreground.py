"""Bring a run's own DayZ client window to the front, once (inbox 8b1c).

capture_screenshot reads pixels without asking for the foreground, because
forcing it (AttachThreadInput, SW_RESTORE, BringWindowToTop, then
SetForegroundWindow) has killed the live client (fb-20260904-025027-8f76).
A client left behind other windows can keep its terrain grey until someone
brings it forward (8b1c). So this asks Windows once, with SetForegroundWindow
and nothing else, and reads GetForegroundWindow to say whether the window came
to the front and whether it stayed there. Windows grants that call only in the
cases Microsoft documents for it, one of them once the foreground lock
time-out after the user's last input has expired; a refusal is the answer, and
nothing here asks again. A minimized window is not asked for: bringing it back
takes SW_RESTORE. The windows of a process are the ones window_close lists.
Win32 callables are injectable so tests never touch the desktop.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

from dayz_mcp import window_close

# The class of DayZ's render window, as mcp-grab.ps1 selects it.
DAYZ_WINDOW_CLASS = "DayZ"
MAX_HOLD_S = 10.0
SAMPLE_INTERVAL_S = 0.25
# How long the answer may take to show in GetForegroundWindow.
SETTLE_S = 0.5


@dataclass(frozen=True)
class Win32ForegroundFns:
    class_name: Callable[[int], str]
    area: Callable[[int], int]
    is_iconic: Callable[[int], bool]
    set_foreground: Callable[[int], bool]
    foreground: Callable[[], int]


_REAL: Win32ForegroundFns | None = None


def bind_real_win32() -> Win32ForegroundFns:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND

    def class_name(hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        if not user32.GetClassNameW(hwnd, buffer, len(buffer)):
            return ""
        return buffer.value

    def area(hwnd: int) -> int:
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return 0
        return max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)

    return Win32ForegroundFns(
        class_name=class_name,
        area=area,
        is_iconic=lambda hwnd: bool(user32.IsIconic(hwnd)),
        set_foreground=lambda hwnd: bool(user32.SetForegroundWindow(hwnd)),
        foreground=lambda: int(user32.GetForegroundWindow() or 0),
    )


def real_win32() -> Win32ForegroundFns:
    global _REAL
    if _REAL is None:
        _REAL = bind_real_win32()
    return _REAL


def client_window(
    pid: int,
    *,
    windows: window_close.Win32WindowFns | None = None,
    fns: Win32ForegroundFns | None = None,
) -> int | None:
    """The largest visible DayZ window of ``pid``, as the window grab picks it."""

    api = fns if fns is not None else real_win32()
    found: list[tuple[int, int]] = []
    for hwnd, _pid in window_close.list_visible_top_level_windows((pid,), fns=windows):
        if api.class_name(hwnd) != DAYZ_WINDOW_CLASS:
            continue
        size = api.area(hwnd)
        if size > 0:
            found.append((size, hwnd))
    if not found:
        return None
    return max(found)[1]


def is_minimized(hwnd: int, *, fns: Win32ForegroundFns | None = None) -> bool:
    api = fns if fns is not None else real_win32()
    return bool(api.is_iconic(hwnd))


def set_foreground(
    hwnd: int,
    pid: int,
    *,
    windows: window_close.Win32WindowFns | None = None,
    fns: Win32ForegroundFns | None = None,
) -> bool | None:
    """Call SetForegroundWindow once while ``hwnd`` still belongs to ``pid``.

    The window's pid is read right before the call. None means it was not
    asked: the window is gone or belongs to another process now.
    """

    listing = windows if windows is not None else window_close.real_win32()
    api = fns if fns is not None else real_win32()
    if int(listing.window_pid(hwnd)) != int(pid):
        return None
    return bool(api.set_foreground(hwnd))


def watch(
    hwnd: int,
    hold_s: float,
    *,
    fns: Win32ForegroundFns | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, float]:
    """Read the foreground window for ``hold_s`` seconds after the call.

    ``foreground`` when ``hwnd`` held it throughout, ``lost`` when another
    window took it back first, and ``denied`` when it did not show within
    SETTLE_S. The seconds are how long ``hwnd`` held it.
    """

    api = fns if fns is not None else real_win32()
    start = clock()
    while api.foreground() != hwnd:
        if clock() - start >= SETTLE_S:
            return "denied", 0.0
        sleep(SAMPLE_INTERVAL_S)
    shown = clock()
    while clock() - shown < hold_s:
        sleep(SAMPLE_INTERVAL_S)
        if api.foreground() != hwnd:
            return "lost", round(clock() - shown, 2)
    return "foreground", round(clock() - shown, 2)
