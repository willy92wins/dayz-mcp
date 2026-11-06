"""tests/_answers_at_once.py: what it counts, what it retries, what it never retries."""

from __future__ import annotations

import asyncio
import threading
import time
import unittest
from unittest import mock

from tests import _answers_at_once
from tests._answers_at_once import (
    WaitEntries,
    assert_answers_at_once,
    assert_answers_at_once_async,
)


class _Clock:
    """perf_counter that each call() moves by the next step."""

    def __init__(self, steps: list[float]) -> None:
        self.now = 100.0
        self.steps = list(steps)

    def perf_counter(self) -> float:
        return self.now

    def call(self) -> str:
        self.now += self.steps.pop(0)
        return "value"


class WaitEntriesTest(unittest.TestCase):
    def test_counts_a_blocking_wait_and_not_a_lock_or_a_true_predicate(self) -> None:
        for lock in (threading.Lock(), threading.RLock()):
            with self.subTest(lock=type(lock).__name__):
                cv = threading.Condition(lock)
                with WaitEntries(cv) as waits:
                    with cv:
                        pass
                    with cv:
                        cv.wait_for(lambda: True, timeout=1.0)
                    self.assertEqual(waits.count, 0)
                    with cv:
                        cv.wait(timeout=0.001)
                    self.assertEqual(waits.count, 1)

    def test_restores_the_condition(self) -> None:
        for lock in (threading.Lock(), threading.RLock()):
            with self.subTest(lock=type(lock).__name__):
                cv = threading.Condition(lock)
                had_own = "_release_save" in vars(cv)
                with WaitEntries(cv):
                    pass
                self.assertEqual("_release_save" in vars(cv), had_own)
                # Still a working condition.
                with cv:
                    self.assertFalse(cv.wait(timeout=0.001))


class AssertAnswersAtOnceTest(unittest.TestCase):
    def _run(self, steps: list[float], **kwargs) -> tuple[object, _Clock]:
        clock = _Clock(steps)
        with mock.patch.object(_answers_at_once.time, "perf_counter", clock.perf_counter):
            value = assert_answers_at_once(self, clock.call, gate_s=0.2, **kwargs)
        return value, clock

    def test_first_sample_under_the_gate_is_returned(self) -> None:
        value, clock = self._run([0.05, 0.05, 0.05])
        self.assertEqual(value, "value")
        self.assertEqual(clock.steps, [0.05, 0.05])

    def test_a_slow_sample_is_taken_again(self) -> None:
        value, clock = self._run([0.6, 0.6, 0.05])
        self.assertEqual(value, "value")
        self.assertEqual(clock.steps, [])

    def test_slow_in_every_sample_fails(self) -> None:
        with self.assertRaises(AssertionError) as caught:
            self._run([0.3, 0.3, 0.3])
        self.assertIn("in every sample (3)", str(caught.exception))

    def test_the_gate_itself_is_not_under(self) -> None:
        with self.assertRaises(AssertionError):
            self._run([0.2, 0.2, 0.2])

    def test_a_wait_fails_the_first_sample_even_when_fast(self) -> None:
        cv = threading.Condition()
        calls = {"n": 0}

        def waits() -> None:
            calls["n"] += 1
            with cv:
                cv.wait(timeout=0.001)

        with self.assertRaises(AssertionError) as caught:
            assert_answers_at_once(self, waits, gate_s=5.0, no_wait_on=(cv,))
        self.assertIn("entered Condition.wait 1 time", str(caught.exception))
        self.assertEqual(calls["n"], 1)
        # The hook came off although the sample failed.
        self.assertEqual(cv._release_save, cv._lock._release_save)

    def test_prepare_runs_before_each_sample(self) -> None:
        order: list[str] = []
        clock = _Clock([0.6, 0.05])

        def call() -> str:
            order.append("call")
            return clock.call()

        with mock.patch.object(_answers_at_once.time, "perf_counter", clock.perf_counter):
            assert_answers_at_once(
                self, call, gate_s=0.2, prepare=lambda: order.append("prepare")
            )
        self.assertEqual(order, ["prepare", "call", "prepare", "call"])

    def test_samples_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            assert_answers_at_once(self, lambda: None, gate_s=0.2, samples=0)


class AssertAnswersAtOnceAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_then_fails(self) -> None:
        clock = _Clock([0.6, 0.6, 0.6])

        async def call() -> str:
            await asyncio.sleep(0)
            return clock.call()

        with mock.patch.object(_answers_at_once.time, "perf_counter", clock.perf_counter):
            with self.assertRaises(AssertionError) as caught:
                await assert_answers_at_once_async(self, call, gate_s=0.2)
        self.assertIn("in every sample (3)", str(caught.exception))

    async def test_an_assert_inside_the_call_is_not_retried(self) -> None:
        calls = {"n": 0}

        async def call() -> None:
            calls["n"] += 1
            self.fail("fact")

        with self.assertRaises(AssertionError):
            await assert_answers_at_once_async(self, call, gate_s=0.2)
        self.assertEqual(calls["n"], 1)

    async def test_real_clock_fast_call_passes(self) -> None:
        started = time.perf_counter()
        await assert_answers_at_once_async(self, lambda: asyncio.sleep(0), gate_s=5.0)
        self.assertLess(time.perf_counter() - started, 5.0)


if __name__ == "__main__":
    unittest.main()
