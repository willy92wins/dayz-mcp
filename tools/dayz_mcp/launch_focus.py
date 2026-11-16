"""Keep the foreground where a person left it while a DayZ run starts (inbox 5c8e).

DayZ, launched by the daemon for automated tests, activates its own window a few
seconds after it starts and keeps the keyboard focus, so what the person at the
PC types goes to the game (inbox 5c8e). Starting it without activation (#59) and
minimized (#168, reverted by #180) did not help, and the daemon has no foreground
right while a person uses the PC: its foreground lock of #253 was refused at every
launch and was removed by #361.

So this guard never asks Windows for the foreground and never injects input. When
a window of the launched run takes the foreground from a person working in another
window, it minimizes that window with ShowWindowAsync(SW_MINIMIZE): Windows itself
activates the next window in the Z order, best effort the one the person was using
("returned"), else another application ("elsewhere"). Every window it minimized is
shown again with ShowWindowAsync(SW_SHOWNOACTIVATE), without activation, and the
guard checks that the window really came back; a window that does not come back is
reported and the guard disarms. "Returned" is best effort because Windows chooses
which window to activate.

step() decides in this order: stop when disarmed or when the watch has elapsed;
record a mouse button or a switch key seen down; a foreground window that is not
one of the run's is not a steal and becomes the person window; a run window that
takes the front is one steal, and then intent is checked first (a switch key seen
within INTENT_S, or a mouse button seen within INTENT_S with the cursor over
another window), then a previous window to give back to, then presence (idle_s
greater than PRESENT_S), then the attempt limit, then identity (verify), and only
then the give-back. Intent and presence are heuristics: PRESENT_S is a presence
heuristic, not a promise that Windows grants the foreground.

Win32 callables are injectable so tests never touch the desktop.
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Iterable

FOCUS_MODES = ("keep", "take")
DEFAULT_FOCUS = "keep"
WATCH_S = 300.0
POLL_S = 0.025
PRESENT_S = 200.0
INTENT_S = 0.75
MAX_RETURNS = 3
SETTLE_S = 1.0
UNRESOLVED_S = 10.0
SW_MINIMIZE = 6
SW_SHOWNOACTIVATE = 4
GA_ROOT = 2
MOUSE_BUTTONS = (0x01, 0x02, 0x04, 0x05, 0x06)
SWITCH_KEYS = (0x12, 0x5B, 0x5C)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


@dataclass(frozen=True)
class FocusFns:
    foreground: Callable[[], int]
    window_pid: Callable[[int], int]
    is_window: Callable[[int], bool]
    is_iconic: Callable[[int], bool]
    minimize: Callable[[int], bool]
    show_no_activate: Callable[[int], bool]
    idle_s: Callable[[], float]
    mouse_down: Callable[[], bool]
    switch_keys_down: Callable[[], bool]
    cursor_window: Callable[[], int]


_REAL: FocusFns | None = None


def bind_real_win32() -> FocusFns:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindowAsync.restype = wintypes.BOOL
    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    user32.GetLastInputInfo.restype = wintypes.BOOL
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    kernel32.GetTickCount.argtypes = []
    kernel32.GetTickCount.restype = wintypes.DWORD

    def foreground() -> int:
        return int(user32.GetForegroundWindow() or 0)

    def window_pid(hwnd: int) -> int:
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def is_window(hwnd: int) -> bool:
        return bool(user32.IsWindow(hwnd))

    def is_iconic(hwnd: int) -> bool:
        return bool(user32.IsIconic(hwnd))

    def minimize(hwnd: int) -> bool:
        return bool(user32.ShowWindowAsync(hwnd, SW_MINIMIZE))

    def show_no_activate(hwnd: int) -> bool:
        return bool(user32.ShowWindowAsync(hwnd, SW_SHOWNOACTIVATE))

    def idle_s() -> float:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return ((int(kernel32.GetTickCount()) - int(info.dwTime)) & 0xFFFFFFFF) / 1000.0

    def keys_down(vks: tuple[int, ...]) -> bool:
        down = False
        for vk in vks:
            if int(user32.GetAsyncKeyState(vk)) & 0x8000:
                down = True
        return down

    def mouse_down() -> bool:
        return keys_down(MOUSE_BUTTONS)

    def switch_keys_down() -> bool:
        return keys_down(SWITCH_KEYS)

    def cursor_window() -> int:
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return 0
        hwnd = user32.WindowFromPoint(point)
        if not hwnd:
            return 0
        return int(user32.GetAncestor(hwnd, GA_ROOT) or 0)

    return FocusFns(
        foreground=foreground,
        window_pid=window_pid,
        is_window=is_window,
        is_iconic=is_iconic,
        minimize=minimize,
        show_no_activate=show_no_activate,
        idle_s=idle_s,
        mouse_down=mouse_down,
        switch_keys_down=switch_keys_down,
        cursor_window=cursor_window,
    )


def real_win32() -> FocusFns:
    global _REAL
    if _REAL is None:
        _REAL = bind_real_win32()
    return _REAL


class FocusGuard:
    def __init__(
        self,
        *,
        fns: FocusFns,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], None] = time.sleep,
        verify: Callable[[int], bool] | None = None,
        watch_s: float = WATCH_S,
        poll_s: float = POLL_S,
        present_s: float = PRESENT_S,
        intent_s: float = INTENT_S,
        max_returns: int = MAX_RETURNS,
        settle_s: float = SETTLE_S,
        unresolved_s: float = UNRESOLVED_S,
    ) -> None:
        self.fns = fns
        self.clock = clock
        self.sleep = sleep
        self.verify = verify
        self.watch_s = watch_s
        self.poll_s = poll_s
        self.present_s = present_s
        self.intent_s = intent_s
        self.max_returns = max_returns
        self.settle_s = settle_s
        self.unresolved_s = unresolved_s
        self._lock = threading.RLock()
        self._disarm_lock = threading.RLock()
        self._idle = threading.Event()
        self._idle.set()
        self._pids: set[int] = set()
        self._added_at: dict[int, float] = {}
        self._start: float | None = None
        self._end: float | None = None
        self._person: int | None = None
        self._last_mouse: float | None = None
        self._last_key: float | None = None
        self._in_steal = False
        self._give_back_running = False
        self._fence: dict[int, dict] = {}
        self._attempts = 0
        self._first_steal_s: float | None = None
        self._reason: str | None = None
        self._counters = {
            "steals": 0,
            "returned": 0,
            "elsewhere": 0,
            "failed": 0,
            "user_switched": 0,
            "away": 0,
            "no_previous": 0,
            "unverified": 0,
            "restore_failed": 0,
        }

    @property
    def armed(self) -> bool:
        with self._disarm_lock:
            return self._reason is None

    def disarm(self, reason: str) -> None:
        with self._disarm_lock:
            if self._reason is None:
                self._reason = reason

    def wait_idle(self, timeout_s: float) -> bool:
        return self._idle.wait(timeout_s)

    def summary(self) -> dict:
        with self._lock:
            result = {"mode": "keep"}
            result.update(self._counters)
            result["first_steal_s"] = self._first_steal_s
            with self._disarm_lock:
                result["disarmed"] = self._reason
            return result

    def add_pids(self, pids: Iterable[int], *, foreground_before: int = 0) -> None:
        now = self.clock()
        with self._lock:
            self._add_pids_locked(pids, now, foreground_before)

    def extend(self, pids: Iterable[int], *, foreground_before: int = 0) -> bool:
        now = self.clock()
        with self._lock:
            with self._disarm_lock:
                if self._reason is not None:
                    return False
                self._add_pids_locked(pids, now, foreground_before)
                self._attempts = 0
                return True

    def _add_pids_locked(
        self, pids: Iterable[int], now: float, foreground_before: int
    ) -> None:
        for pid in pids:
            try:
                value = int(pid)
            except (TypeError, ValueError):
                continue
            if value > 0:
                self._pids.add(value)
                self._added_at[value] = now
        if self._start is None:
            self._start = now
        self._end = now + self.watch_s
        if (
            self._person is not None
            and self.fns.window_pid(self._person) in self._pids
        ):
            self._person = None
        if foreground_before:
            before = int(foreground_before)
            if self.fns.window_pid(before) not in self._pids:
                self._person = before

    def step(self) -> None:
        with self._lock:
            now = self.clock()
            self._service_fence(now)
            self._refresh_idle()
            if not self.armed:
                return
            if self._end is not None and now >= self._end:
                self.disarm("window_elapsed")
                return
            if self.fns.mouse_down():
                self._last_mouse = now
            if self.fns.switch_keys_down():
                self._last_key = now
            fg = int(self.fns.foreground() or 0)
            is_run = fg != 0 and self.fns.window_pid(fg) in self._pids
            if not is_run:
                self._in_steal = False
                if fg != 0:
                    self._person = fg
                return
            if self._in_steal:
                return
            self._in_steal = True
            self._counters["steals"] += 1
            if self._first_steal_s is None:
                start = self._start if self._start is not None else now
                self._first_steal_s = round(now - start, 2)
            prev = self._person
            key_recent = (
                self._last_key is not None and now - self._last_key <= self.intent_s
            )
            mouse_recent = (
                self._last_mouse is not None and now - self._last_mouse <= self.intent_s
            )
            if key_recent or (
                mouse_recent and int(self.fns.cursor_window() or 0) != prev
            ):
                self._counters["user_switched"] += 1
                return
            if prev is None or not self.fns.is_window(prev):
                self._counters["no_previous"] += 1
                return
            if self.fns.idle_s() > self.present_s:
                self._counters["away"] += 1
                return
            if self._attempts >= self.max_returns:
                self.disarm("max_returns")
                return
            pid = self.fns.window_pid(fg)
            if not self._verified(pid):
                self._counters["unverified"] += 1
                self._pids.discard(pid)
                self._added_at.pop(pid, None)
                return
            self._attempts += 1
            pids = set(self._pids)
            self._give_back_running = True
            self._idle.clear()
        result: tuple[str, bool, bool, dict[int, dict]] | None = None
        try:
            result = self.give_back(fg, prev, pids, pid)
        finally:
            with self._lock:
                if result is None:
                    outcome, restored, departed, fenced = "failed", False, False, {}
                else:
                    outcome, restored, departed, fenced = result
                for window, info in fenced.items():
                    self._fence[window] = info
                self._give_back_running = False
                self._counters[outcome] += 1
                if departed:
                    self._in_steal = False
                if not restored:
                    self._counters["restore_failed"] += 1
                    self.disarm("restore_failed")
                self._refresh_idle()

    def _read_foreground(self) -> int:
        try:
            return int(self.fns.foreground() or 0)
        except Exception:
            return 0

    def _read_pid(self, hwnd: int) -> int:
        try:
            return int(self.fns.window_pid(hwnd))
        except Exception:
            return 0

    def _refresh_idle(self) -> None:
        if not self._give_back_running and not self._fence:
            self._idle.set()
        else:
            self._idle.clear()

    def _service_fence(self, now: float) -> None:
        for window in list(self._fence):
            info = self._fence[window]
            pid = info["pid"]
            try:
                if not self.fns.is_window(window):
                    del self._fence[window]
                    continue
                if int(self.fns.window_pid(window)) != pid:
                    del self._fence[window]
                    continue
            except Exception:
                del self._fence[window]
                continue
            if not info["shown"]:
                try:
                    iconic = bool(self.fns.is_iconic(window))
                except Exception:
                    iconic = False
                if not iconic:
                    continue
                try:
                    owned = bool(self.fns.is_window(window)) and int(
                        self.fns.window_pid(window)
                    ) == pid
                except Exception:
                    owned = False
                if not owned:
                    del self._fence[window]
                    continue
                try:
                    accepted = bool(self.fns.show_no_activate(window))
                except Exception:
                    accepted = False
                info["shown"] = True
                info["tries"] = 1
                info["accepted"] = accepted
                info["at"] = now
                continue
            try:
                iconic = bool(self.fns.is_iconic(window))
            except Exception:
                iconic = False
            if info["accepted"] and not iconic:
                del self._fence[window]
                continue
            if (not info["accepted"]) or (now - info["at"] >= self.settle_s):
                if info["tries"] < 2:
                    try:
                        owned = bool(self.fns.is_window(window)) and int(
                            self.fns.window_pid(window)
                        ) == pid
                    except Exception:
                        owned = False
                    if not owned:
                        del self._fence[window]
                        continue
                    try:
                        accepted = bool(self.fns.show_no_activate(window))
                    except Exception:
                        accepted = False
                    info["tries"] = 2
                    info["accepted"] = accepted
                    info["at"] = now
                else:
                    del self._fence[window]

    def run(
        self,
        *,
        live_pids: Callable[[], Iterable[int]] | None = None,
        check_every_s: float = 1.0,
    ) -> dict:
        last = self.clock()
        while True:
            self.step()
            with self._lock:
                fence_empty = not self._fence
            if not self.armed:
                if fence_empty:
                    return self.summary()
                self.sleep(self.poll_s)
                continue
            if live_pids is not None and self.clock() - last >= check_every_s:
                last = self.clock()
                read_at = self.clock()
                try:
                    live = {int(pid) for pid in live_pids()}
                except Exception:
                    self.disarm("alive_unknown")
                    continue
                with self._lock:
                    keep = {
                        pid
                        for pid in self._pids
                        if pid in live or self._added_at.get(pid, 0.0) >= read_at
                    }
                    self._pids = keep
                    for pid in list(self._added_at):
                        if pid not in keep:
                            del self._added_at[pid]
                    empty = not self._pids
                if empty:
                    self.disarm("run_ended")
                    continue
            self.sleep(self.poll_s)

    def give_back(
        self, fg: int, prev: int, pids: set[int], verified_pid: int
    ) -> tuple[str, bool, bool, dict[int, dict]]:
        start = self.clock()
        settle_at = start + self.settle_s
        give_up_at = start + self.unresolved_s
        posted: list[tuple[int, int]] = []
        seen: set[int] = set()
        tried: set[int] = set()
        departed = False
        target = fg
        target_pid = verified_pid

        while True:
            if target is not None and target not in tried:
                tried.add(target)
                try:
                    owner = int(self.fns.window_pid(target))
                except Exception:
                    owner = 0
                if owner == target_pid and owner in pids:
                    with self._disarm_lock:
                        if self._reason is None:
                            try:
                                posted_ok = bool(self.fns.minimize(target))
                            except Exception:
                                posted_ok = False
                            if posted_ok:
                                posted.append((target, owner))
                target = None
            before = self._read_foreground()
            if before != 0 and self._read_pid(before) not in pids:
                departed = True
            for window, pid in posted:
                if window in seen:
                    continue
                try:
                    if (
                        self.fns.is_window(window)
                        and int(self.fns.window_pid(window)) == pid
                        and self.fns.is_iconic(window)
                    ):
                        seen.add(window)
                except Exception:
                    pass
            after = self._read_foreground()
            after_pid = self._read_pid(after) if after != 0 else 0
            if after != 0 and after_pid not in pids:
                departed = True
            if (
                self.armed
                and after != 0
                and after_pid in pids
                and after not in tried
                and self.clock() < settle_at
            ):
                if self._verified(after_pid):
                    target = after
                    target_pid = after_pid
                    continue
            all_seen = all(
                window in seen or not self._owned(window, pid)
                for window, pid in posted
            )
            if all_seen and (departed or self.clock() >= settle_at):
                break
            if self.clock() >= give_up_at:
                break
            self.sleep(self.poll_s)

        restore_windows = [
            (window, pid) for window, pid in posted if window in seen
        ]
        fenced = {
            window: {
                "pid": pid,
                "shown": False,
                "accepted": None,
                "tries": 0,
                "at": 0.0,
            }
            for window, pid in posted
            if window not in seen and self._owned(window, pid)
        }
        accepted: dict[int, bool] = {}
        self._post_show(restore_windows, accepted)
        if self._settle(restore_windows, accepted, pids):
            departed = True
        still = [
            (window, pid)
            for window, pid in restore_windows
            if self._unresolved(window, pid, accepted)
        ]
        if still:
            self._post_show(still, accepted)
            if self._settle(restore_windows, accepted, pids):
                departed = True
        restored = (
            not any(
                self._unresolved(window, pid, accepted)
                for window, pid in restore_windows
            )
            and not fenced
        )
        front = self._read_foreground()
        if front != 0:
            front_pid = self._read_pid(front)
            if front_pid not in pids:
                departed = True
            if front == prev:
                outcome = "returned"
            elif front_pid not in pids:
                outcome = "elsewhere"
            else:
                outcome = "failed"
        else:
            outcome = "failed"
        return outcome, restored, departed, fenced

    def _owned(self, window: int, pid: int) -> bool:
        try:
            return bool(
                self.fns.is_window(window) and self.fns.window_pid(window) == pid
            )
        except Exception:
            return False

    def _unresolved(
        self, window: int, pid: int, accepted: dict[int, bool]
    ) -> bool:
        try:
            if not self.fns.is_window(window):
                return False
            if self.fns.window_pid(window) != pid:
                return False
            if accepted.get(window) is not True:
                return True
            return bool(self.fns.is_iconic(window))
        except Exception:
            return False

    def _post_show(
        self, windows: list[tuple[int, int]], accepted: dict[int, bool]
    ) -> None:
        for window, pid in windows:
            if not self._owned(window, pid):
                continue
            try:
                accepted[window] = bool(self.fns.show_no_activate(window))
            except Exception:
                accepted[window] = False

    def _settle(
        self,
        windows: list[tuple[int, int]],
        accepted: dict[int, bool],
        pids: set[int],
    ) -> bool:
        departed = False
        deadline = self.clock() + self.settle_s
        while True:
            front = self._read_foreground()
            if front != 0 and self._read_pid(front) not in pids:
                departed = True
            if any(value is False for value in accepted.values()):
                return departed
            if not any(
                self._unresolved(window, pid, accepted)
                for window, pid in windows
            ):
                return departed
            left = deadline - self.clock()
            if left <= 0:
                return departed
            self.sleep(min(self.poll_s, left))

    def _verified(self, pid: int) -> bool:
        if self.verify is None:
            return True
        try:
            return bool(self.verify(pid))
        except Exception:
            return False
