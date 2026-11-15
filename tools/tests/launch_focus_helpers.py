"""Fakes for the launch_focus guard: a desktop, its foreground and a clock (inbox 5c8e).

Shared by the launch-focus tests so that no test module imports another.
"""
from __future__ import annotations

from dayz_mcp.launch_focus import FocusFns

PERSON = 7  # the window a person works in
OTHER = 8  # a window of another application
CLIENT = 101  # the run's DayZ client window
SERVER = 202  # the run's DayZ server window
PERSON_PID = 900
OTHER_PID = 901
CLIENT_PID = 5001
SERVER_PID = 5002


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += max(0.0, float(seconds))


class FakeDesktop:
    """Top-level windows, the foreground, the cursor, and what Windows does on
    SW_MINIMIZE (activate the next window in the Z order) and SW_SHOWNOACTIVATE."""

    def __init__(self) -> None:
        self.pids = {
            PERSON: PERSON_PID,
            OTHER: OTHER_PID,
            CLIENT: CLIENT_PID,
            SERVER: SERVER_PID,
        }
        self.front = PERSON
        self.z = [PERSON, OTHER]
        self.minimized: set[int] = set()
        self.calls: list[tuple[str, int]] = []
        self.owners_at_show: list[int] = []
        self.idle = 1.0
        self.mouse = False
        self.keys = False
        self.cursor = PERSON
        self.activates_next = True
        # A minimize that has not taken effect yet (ShowWindowAsync only posts it).
        self.minimize_pending = False
        self._pending: list[int] = []
        # What show_no_activate answers, per call, then True.
        self.show_answers: list[bool] = []
        self.restores = True
        self.restore_activates = False
        self.on_minimize = None
        self.on_show = None

    def steal(self, hwnd: int) -> None:
        self.front = hwnd
        if hwnd in self.z:
            self.z.remove(hwnd)
        self.z.insert(0, hwnd)

    def deliver_pending(self) -> None:
        for hwnd in self._pending:
            self._apply_minimize(hwnd)
        self._pending = []

    def _apply_minimize(self, hwnd: int) -> None:
        self.minimized.add(hwnd)
        if self.front == hwnd and self.activates_next:
            below = [w for w in self.z if w != hwnd and w not in self.minimized]
            self.front = below[0] if below else 0

    def foreground(self) -> int:
        return self.front

    def window_pid(self, hwnd: int) -> int:
        return self.pids.get(hwnd, 0)

    def is_window(self, hwnd: int) -> bool:
        return hwnd in self.pids

    def is_iconic(self, hwnd: int) -> bool:
        return hwnd in self.minimized

    def minimize(self, hwnd: int) -> bool:
        self.calls.append(("minimize", hwnd))
        if self.on_minimize is not None:
            self.on_minimize(hwnd)
        if self.minimize_pending:
            self._pending.append(hwnd)
        else:
            self._apply_minimize(hwnd)
        return True

    def show_no_activate(self, hwnd: int) -> bool:
        self.calls.append(("show_no_activate", hwnd))
        self.owners_at_show.append(self.pids.get(hwnd, 0))
        if self.on_show is not None:
            self.on_show(hwnd)
        answer = self.show_answers.pop(0) if self.show_answers else True
        if answer and self.restores:
            self.minimized.discard(hwnd)
            if self.restore_activates:
                self.steal(hwnd)
        return answer

    def idle_s(self) -> float:
        return self.idle

    def mouse_down(self) -> bool:
        return self.mouse

    def switch_keys_down(self) -> bool:
        return self.keys

    def cursor_window(self) -> int:
        return self.cursor

    def fns(self) -> FocusFns:
        return FocusFns(
            foreground=self.foreground,
            window_pid=self.window_pid,
            is_window=self.is_window,
            is_iconic=self.is_iconic,
            minimize=self.minimize,
            show_no_activate=self.show_no_activate,
            idle_s=self.idle_s,
            mouse_down=self.mouse_down,
            switch_keys_down=self.switch_keys_down,
            cursor_window=self.cursor_window,
        )

    def minimizes(self) -> list[int]:
        return [hwnd for name, hwnd in self.calls if name == "minimize"]

    def shows(self) -> list[int]:
        return [hwnd for name, hwnd in self.calls if name == "show_no_activate"]
