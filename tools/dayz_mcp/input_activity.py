"""Who is using a managed run's windows (ficha 250f, plan v2.1 §3.1).

The daemon samples, every SAMPLE_INTERVAL_S, the interactive session's
last-input tick (``GetLastInputInfo``) and the process that owns the
foreground window. An input counts for a run when the tick advanced since the
previous sample and a window of one of the run's registered processes was in
the foreground at that sample or at the previous one. Counting too much only
delays a cut; counting too little could cut a run a person is playing, so a
doubt counts.

One case is not seen, and is declared (plan residue C1): a full focus
excursion shorter than one interval, where the run's window gains the focus,
gets input and loses it between two samples that both show another window.
Input in other windows never counts for a run (D-81).

Nothing here reads window titles, keys or any other input content: only the
tick of the last input and the pid of the foreground window.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Callable


SAMPLE_INTERVAL_S = 0.25
_TICK_MASK = 0xFFFFFFFF
_TICK_HALF = 0x80000000


@dataclass(frozen=True)
class Win32InputFns:
    """The three Win32 reads, injectable so tests never touch the desktop."""

    last_input_tick: Callable[[], int | None]
    tick_count: Callable[[], int]
    foreground_pid: Callable[[], int | None]


def bind_real_win32() -> Win32InputFns:
    from ctypes import wintypes

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
    user32.GetLastInputInfo.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.GetTickCount.argtypes = []
    kernel32.GetTickCount.restype = wintypes.DWORD

    def last_input_tick() -> int | None:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        return int(info.dwTime)

    def tick_count() -> int:
        return int(kernel32.GetTickCount())

    def foreground_pid() -> int | None:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD(0)
        if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return None
        return int(pid.value) or None

    return Win32InputFns(last_input_tick, tick_count, foreground_pid)


def real_win32() -> Win32InputFns | None:
    """The real reads on Windows; None elsewhere or when user32 cannot bind."""

    if sys.platform != "win32":
        return None
    try:
        return bind_real_win32()
    except (AttributeError, OSError):
        return None


@dataclass(frozen=True)
class InputSample:
    at: float
    ok: bool
    last_input_tick: int | None = None
    idle_ms: int | None = None
    foreground_pid: int | None = None

    @property
    def last_input_epoch(self) -> float | None:
        if not self.ok or self.idle_ms is None:
            return None
        return self.at - self.idle_ms / 1000.0


def take_sample(fns: Win32InputFns, now: float) -> InputSample:
    """One sample. Any failed or implausible read makes it not ok."""

    try:
        last = fns.last_input_tick()
        tick = fns.tick_count()
        pid = fns.foreground_pid()
    except Exception:
        return InputSample(at=now, ok=False)
    if (
        not isinstance(last, int)
        or isinstance(last, bool)
        or not isinstance(tick, int)
        or isinstance(tick, bool)
    ):
        return InputSample(at=now, ok=False)
    idle_ms = (tick - last) & _TICK_MASK
    if idle_ms >= _TICK_HALF:
        # A last input "in the future" is not a reading this module trusts.
        return InputSample(at=now, ok=False)
    foreground = (
        pid
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
        else None
    )
    return InputSample(
        at=now,
        ok=True,
        last_input_tick=last & _TICK_MASK,
        idle_ms=idle_ms,
        foreground_pid=foreground,
    )


def tick_advanced(before: int, after: int) -> bool:
    """True when ``after`` is a later GetTickCount value than ``before``."""

    delta = (after - before) & _TICK_MASK
    return 0 < delta < _TICK_HALF


class InputAttributor:
    """Attributes new input to the run whose window had the focus.

    ``owner_of(pid)`` answers the run id of a registered process whose full
    identity still matches, or None; it is only asked when there is new input.
    """

    def __init__(self) -> None:
        self._previous: InputSample | None = None

    def observe(
        self,
        sample: InputSample,
        owner_of: Callable[[int], str | None],
    ) -> tuple[str, float] | None:
        previous, self._previous = self._previous, sample
        if previous is None or not previous.ok or not sample.ok:
            return None
        if previous.last_input_tick is None or sample.last_input_tick is None:
            return None
        if not tick_advanced(previous.last_input_tick, sample.last_input_tick):
            return None
        epoch = sample.last_input_epoch
        if epoch is None:
            return None
        asked: set[int] = set()
        for pid in (sample.foreground_pid, previous.foreground_pid):
            if pid is None or pid in asked:
                continue
            asked.add(pid)
            run_id = owner_of(pid)
            if run_id is not None:
                return run_id, epoch
        return None
