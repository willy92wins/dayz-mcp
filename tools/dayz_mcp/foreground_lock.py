"""Lock the foreground while DayZ starts (inbox fade, 75e7).

DayZ brings its own window to the front after it starts, whatever its first
show state: launched SW_SHOWNOACTIVATE (#59) and with the server window
minimized (#168, reverted in #180), the server still held the foreground for
2.26 s and the client for 2.5 s and then 5.0 s (2026-09-30). So each normal
launch calls LockSetForegroundWindow(LSFW_LOCK), which disables calls to
SetForegroundWindow, and LSFW_UNLOCK HOLD_S after the last launch: the takes
measured in fade came within 3 s of each start. Windows enables those calls
again itself when the user presses ALT or does something that makes Windows
change the foreground window, such as clicking a background window, so a
person can always bring DayZ forward. Whether the lock keeps DayZ behind is
not measured yet.

Microsoft documents the call for the foreground process, so hold() reports
what Windows answered. Nothing here reads a window: the lock is the only call.
"""

from __future__ import annotations

import sys
import threading
from typing import Callable

HOLD_S = 30.0
LSFW_LOCK = 1
LSFW_UNLOCK = 2


def real_lock_fn() -> Callable[[int], bool] | None:
    """LockSetForegroundWindow on Windows; None elsewhere or when it cannot bind."""

    if sys.platform != "win32":
        return None
    import ctypes

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        lock = user32.LockSetForegroundWindow
    except (AttributeError, OSError):
        return None
    lock.argtypes = [ctypes.c_uint]
    lock.restype = ctypes.c_int
    return lambda code: bool(lock(code))


class ForegroundLock:
    """The lock DayZ launches take, released HOLD_S after the last of them."""

    def __init__(
        self,
        lock_fn: Callable[[int], bool] | None,
        *,
        hold_s: float = HOLD_S,
        timer: Callable[..., threading.Timer] = threading.Timer,
    ) -> None:
        self._lock_fn = lock_fn
        self._hold_s = hold_s
        self._timer_factory = timer
        self._mutex = threading.Lock()
        self._generation = 0
        self._release_timer: threading.Timer | None = None

    def hold(self) -> bool | None:
        """Lock now and release HOLD_S from now.

        Returns what LockSetForegroundWindow answered, or None where there is
        no lock to take. When the release cannot be scheduled, it unlocks at
        once and raises, so no lock is left without a release.
        """

        if self._lock_fn is None:
            return None
        with self._mutex:
            # Windows may refuse a lock while an earlier launch's is still
            # held: a refusal says nothing of whether one is held.
            locked = self._lock_fn(LSFW_LOCK)
            self._generation += 1
            if self._release_timer is not None:
                self._release_timer.cancel()
                self._release_timer = None
            try:
                timer = self._timer_factory(
                    self._hold_s, self._release, args=(self._generation,)
                )
                timer.daemon = True
                timer.start()
            except Exception:
                self._lock_fn(LSFW_UNLOCK)
                raise
            self._release_timer = timer
        return locked

    def _release(self, generation: int) -> None:
        with self._mutex:
            # A timer that fired while a later launch was taking the lock
            # leaves that launch's release to its own timer.
            if generation != self._generation:
                return
            self._release_timer = None
            self._lock_fn(LSFW_UNLOCK)
