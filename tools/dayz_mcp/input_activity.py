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
A failed sample is bridged, so the same holds across a short gap of failed
samples. Input in other windows never counts for a run (D-81).

An identity that cannot be read is a doubt, and a doubt counts as possible
use: the lifecycle keeps such input apart and publishes the run as unknown.

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
            # No foreground window (activation in transit, a locked desktop):
            # nobody is using a run's window, which is a reading, not a failure.
            return None
        pid = wintypes.DWORD(0)
        if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            # A window whose owner cannot be read is a failed read: the sample
            # must not pass for one without a run in front (review #125 F3).
            raise OSError(ctypes.get_last_error(), "GetWindowThreadProcessId failed")
        return int(pid.value)

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
    """Attributes new input to the runs whose windows had the focus.

    ``owner_of(pid)`` answers ``(run_id, verified)`` for a registered process
    of an active run, or None when the pid is not (or no longer) one of them;
    a doubt about the identity comes back with ``verified`` False. It is only
    asked when there is new input.

    A failed sample never replaces the last good one, so a short gap is
    bridged: the next good sample is compared with the last good one. A gap
    longer than ``bridge_s`` attributes nothing; the lifecycle restarts the
    clocks of every run that may have been in use from the recovery instead.
    """

    def __init__(self, bridge_s: float = 2.0) -> None:
        self._last_ok: InputSample | None = None
        self._bridge_s = bridge_s

    def observe(
        self,
        sample: InputSample,
        owner_of: Callable[[int], tuple[str, bool] | None],
    ) -> list[tuple[str, float, bool]]:
        if not sample.ok:
            return []
        previous, self._last_ok = self._last_ok, sample
        if previous is None or sample.at - previous.at > self._bridge_s:
            return []
        if previous.last_input_tick is None or sample.last_input_tick is None:
            return []
        if not tick_advanced(previous.last_input_tick, sample.last_input_tick):
            return []
        epoch = sample.last_input_epoch
        if epoch is None:
            return []
        found: dict[str, tuple[str, float, bool]] = {}
        asked: set[int] = set()
        # Both ends count: with the focus moving from one run to another, the
        # input may belong to either, and a doubt counts (review #125 F5).
        for pid in (sample.foreground_pid, previous.foreground_pid):
            if pid is None or pid in asked:
                continue
            asked.add(pid)
            owner = owner_of(pid)
            if owner is None:
                continue
            run_id, verified = owner
            prior = found.get(run_id)
            if prior is None or (verified and not prior[2]):
                found[run_id] = (run_id, epoch, verified)
        return list(found.values())
