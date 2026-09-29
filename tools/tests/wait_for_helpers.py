"""Clock shared by wait_for tests that must not depend on the Windows tick.

Lives here so a test module can use it without importing another test module.
"""
from __future__ import annotations


class _ExactWaitClock:
    """time.monotonic that moves only by the delay wait_for sleeps.

    On Windows before 3.13, monotonic ticks about every 15.6 ms and a sleep
    shorter than one tick can wake on the same reading, still before the
    deadline, so another probe starts legitimately. Adding exactly the delay
    the loop asked for makes the probe count and the timeout bound exact.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay
