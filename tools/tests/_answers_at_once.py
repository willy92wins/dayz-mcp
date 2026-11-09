"""Shared check for "answers at once" gates in the test process.

A gate such as ``assertLess(elapsed, 0.2)`` around an HTTP round trip, a
thread hop or an asyncio step also times whatever else runs in the process.
On the Windows CI runners that was the cyclic garbage collector: 98 % of the
stalls of 0.1 s or more seen by a thread inside the test process fell inside
a full (generation 2) collection, which holds the GIL and stops every thread,
the test's HTTP server and event loop included. They reached 1.3 s while
tests/_shard.py kept every finished case alive; with that fixed the largest
measured was 0.25 s, still over a 0.2 s gate. A collection can start in any
thread, so no check inside the process tells that gap from one the code made
(CHANGELOG.md has the measurements).

So a gate says what "at once" means in two parts:

1. **A fact load cannot change, checked on every sample.** The usual one is
   that the call never entered ``Condition.wait`` on the conditions where the
   code would block (``WaitEntries``). A mutant that waits fails here, in the
   first sample, whatever the load.
2. **The wall clock, gate unchanged, as a backstop.** A sample over the gate
   is taken again, at most ``samples`` times, and the test fails if none is
   under it. A block the code makes (a ``time.sleep``, a slow path that is
   not a condition wait) repeats in every sample and still fails; a
   collection pause does not land in three samples in a row.

Elapsed time uses ``time.perf_counter``: on Windows, Python 3.11's
``time.monotonic`` is GetTickCount64 and moves in 15.625 ms steps.
"""

from __future__ import annotations

import threading
import time
import unittest
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

# Three samples: the pauses measured on CI were single events (one failing
# test per job, never two in a row), and a real block repeats in all three.
DEFAULT_SAMPLES = 3


class WaitEntries:
    """Count blocking entries into ``Condition.wait`` on some conditions.

    ``Condition.wait`` calls ``self._release_save()`` once it has committed to
    block, before it releases the lock (CPython threading.py). Hooking that
    attribute counts real waits and nothing else: ``wait_for`` with a true
    predicate and code that only takes the lock are not counted. The hook is
    the one test_poll_long_poll.py's ``_on_wait`` already uses.
    """

    def __init__(self, *conditions: threading.Condition) -> None:
        self._conditions = conditions
        self._lock = threading.Lock()
        self._count = 0
        self._saved: list[tuple[threading.Condition, bool, object]] = []

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def __enter__(self) -> WaitEntries:
        for cv in self._conditions:
            own = "_release_save" in vars(cv)
            real = cv._release_save  # type: ignore[attr-defined]
            self._saved.append((cv, own, vars(cv).get("_release_save")))

            def release_save(real=real):
                with self._lock:
                    self._count += 1
                return real()

            cv._release_save = release_save  # type: ignore[attr-defined]
        return self

    def __exit__(self, *_exc: object) -> None:
        for cv, own, previous in reversed(self._saved):
            if own:
                cv._release_save = previous  # type: ignore[attr-defined]
            else:
                del cv._release_save  # type: ignore[attr-defined]
        self._saved.clear()


def assert_answers_at_once(
    test: unittest.TestCase,
    call: Callable[[], T],
    *,
    gate_s: float,
    no_wait_on: tuple[threading.Condition, ...] = (),
    samples: int = DEFAULT_SAMPLES,
    prepare: Callable[[], object] | None = None,
    msg: str = "",
) -> T:
    """Run ``call`` until one sample answers under ``gate_s``; return its value.

    Every sample must leave ``no_wait_on`` untouched (part 1). ``call`` runs
    again for each sample, so it has to be repeatable: a read, or a request
    whose answer does not change. ``prepare`` runs before each sample, outside
    the timing and the wait count, to rebuild what a sample consumed. A value
    the caller asserts on is the one of the sample that passed.
    """
    if samples < 1:
        raise ValueError("samples must be at least 1")
    label = f"{msg}: " if msg else ""
    slow: list[float] = []
    with WaitEntries(*no_wait_on) as waits:
        for _ in range(samples):
            if prepare is not None:
                prepare()
            before = waits.count
            started = time.perf_counter()
            value = call()
            elapsed = time.perf_counter() - started
            entered = waits.count - before
            if entered:
                test.fail(
                    f"{label}entered Condition.wait {entered} time(s): the call "
                    f"waited instead of answering at once ({elapsed:.3f}s)"
                )
            if elapsed < gate_s:
                return value
            slow.append(elapsed)
    shown = ", ".join(f"{s:.3f}s" for s in slow)
    test.fail(
        f"{label}{shown} not less than {gate_s} in every sample ({samples}): "
        "the call itself is slow, a scheduler stall does not repeat"
    )
    raise AssertionError("unreachable")  # test.fail raises


async def assert_answers_at_once_async(
    test: unittest.TestCase,
    call: Callable[[], Awaitable[T]],
    *,
    gate_s: float,
    samples: int = DEFAULT_SAMPLES,
    msg: str = "",
) -> T:
    """``assert_answers_at_once`` for a coroutine, with part 1 left to ``call``.

    An asyncio path does not block on a Condition, so the fact load cannot
    change is the caller's: ``call`` asserts it on every sample (for example
    the pauses the code asked asyncio.sleep for), and a failed assert ends the
    test at once. The gate is retried as in ``assert_answers_at_once``.
    """
    if samples < 1:
        raise ValueError("samples must be at least 1")
    label = f"{msg}: " if msg else ""
    slow: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        value = await call()
        elapsed = time.perf_counter() - started
        if elapsed < gate_s:
            return value
        slow.append(elapsed)
    shown = ", ".join(f"{s:.3f}s" for s in slow)
    test.fail(
        f"{label}{shown} not less than {gate_s} in every sample ({samples}): "
        "the call itself is slow, a scheduler stall does not repeat"
    )
    raise AssertionError("unreachable")  # test.fail raises
