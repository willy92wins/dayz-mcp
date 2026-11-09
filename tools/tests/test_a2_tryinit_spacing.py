"""A2: unconfigured TryInit is immediate once, then every INIT_RETRY_S.

Static contract over both bridge sources. The spacing lives in OnTick, on
m_InitAccumS, because a CallLater would still fire after ShutdownInstance.
ia stays m_InitAttempts: skipped frames must not increment it.

MCPBotControl.Tick, which the server's OnTick calls every frame, is translated
and run: with no bot running it builds no array (inbox b320, the rest of A2).
"""

from __future__ import annotations

import re
import unittest

from tests import enforce_subset_helpers as enforce
from tests._addon_paths import addon_root


_MISSION = addon_root() / "scripts" / "5_Mission"
_BRIDGES = ("MCPBridge.c", "MCPClientBridge.c")
_RETRY_DECL = "protected const float INIT_RETRY_S = 1.0;"
_ACCUM_DECL = "protected float m_InitAccumS;"
_SPACED = re.compile(
    r"m_InitAccumS = m_InitAccumS \+ timeslice;\s*"
    r"if \(m_InitAttempts == 0 \|\| m_InitAccumS >= INIT_RETRY_S\)\s*\{\s*"
    r"m_InitAccumS = 0\.0;\s*"
    r"TryInit\(\);\s*\}\s*"
    r"return;",
    re.M,
)
_ASSIGN_ATTEMPTS = re.compile(r"m_InitAttempts\s*=(?!=)")


def _source(name: str) -> str:
    return (_MISSION / name).read_text(encoding="utf-8")


def _block_after(source: str, start: int) -> str:
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated block at offset {start}")


def _method_body(source: str, signature: str) -> str:
    count = source.count(signature)
    if count != 1:
        raise AssertionError(f"{signature!r} occurs {count} times")
    return _block_after(source, source.index(signature))


def _if_body(source: str, condition: str) -> str:
    needle = f"if ({condition})"
    count = source.count(needle)
    if count != 1:
        raise AssertionError(f"{needle!r} occurs {count} times")
    return _block_after(source, source.index(needle))


def _attempt_frames(timeslices: list[float], retry: float = 1.0) -> list[int]:
    """Same predicate the sources gate on. Attempts bump only when it fires."""
    attempts = 0
    accum = 0.0
    called: list[int] = []
    for index, timeslice in enumerate(timeslices):
        accum = accum + timeslice
        if attempts == 0 or accum >= retry:
            accum = 0.0
            attempts = attempts + 1
            called.append(index)
    return called


class TryInitSpacingTest(unittest.TestCase):
    def test_both_bridges_space_tryinit_without_calllater_or_server_ifdef(self) -> None:
        for name in _BRIDGES:
            with self.subTest(bridge=name):
                text = _source(name)
                self.assertEqual(text.count(_RETRY_DECL), 1, name)
                self.assertEqual(text.count(_ACCUM_DECL), 1, name)
                self.assertNotIn("#ifdef SERVER", text)
                self.assertNotIn("#ifndef SERVER", text)

                ctor_name = "void MCPBridge()" if name == "MCPBridge.c" else "void MCPClientBridge()"
                ctor = _method_body(text, ctor_name)
                self.assertIn("m_InitAccumS = 0.0;", ctor)
                self.assertNotIn("CallLater", ctor)
                self.assertNotIn("GetCallQueue", ctor)

                on_tick = _method_body(text, "void OnTick(float timeslice)")
                unconfigured = _if_body(on_tick, "!m_Configured")
                self.assertRegex(unconfigured, _SPACED)
                self.assertEqual(unconfigured.count("TryInit();"), 1)
                self.assertEqual(unconfigured.count("return;"), 1)
                self.assertTrue(unconfigured.rstrip().endswith("return;"))
                self.assertIsNone(_ASSIGN_ATTEMPTS.search(unconfigured))
                try_init = _method_body(text, "protected void TryInit()")
                for banned in ("CallLater", "GetCallQueue", "#ifdef", "#ifndef"):
                    self.assertNotIn(banned, unconfigured, banned)
                    self.assertNotIn(banned, try_init, banned)
                # The per-frame gate itself must not allocate. TryInit still
                # builds an MCPConfig, and it now runs about once a second.
                self.assertNotIn("new ", unconfigured)
                self.assertEqual(text.count("m_InitAttempts = NextLoadCounter(m_InitAttempts);"), 1)
                self.assertIn("m_InitAttempts = NextLoadCounter(m_InitAttempts);", try_init)
                self.assertNotIn("m_InitAttempts = m_InitAttempts + 1;", text)

    def test_log_init_failure_stays_latched_across_retries(self) -> None:
        for name in _BRIDGES:
            with self.subTest(bridge=name):
                text = _source(name)
                logged = _method_body(text, "protected void LogInitFailure(string reason)")
                self.assertLess(
                    logged.index("if (m_InitFailureLogged)"),
                    logged.index("return;"),
                )
                self.assertLess(
                    logged.index("m_InitFailureLogged = true;"),
                    logged.index("Log("),
                )
                self.assertEqual(text.count("m_InitFailureLogged = false;"), 1)
                self.assertEqual(text.count("m_InitFailureLogged = true;"), 1)
                self.assertNotIn(
                    "m_InitFailureLogged",
                    _method_body(text, "void OnTick(float timeslice)"),
                )
                self.assertNotIn(
                    "m_InitFailureLogged",
                    _method_body(text, "protected void TryInit()"),
                )

    def test_client_frame_work_stays_ahead_of_the_spaced_return(self) -> None:
        on_tick = _method_body(_source("MCPClientBridge.c"), "void OnTick(float timeslice)")
        unconfigured = _if_body(on_tick, "!m_Configured")
        gate = on_tick.index("if (!m_Configured)")
        for call in (
            "MCPPlayerTrace.Tick(m_Tick);",
            "TickCameraHandoff();",
            "m_JobRunner.Tick(timeslice, this);",
        ):
            with self.subTest(call=call):
                self.assertLess(on_tick.index(call), gate)
                self.assertNotIn(call, unconfigured)
        self.assertNotIn("DrainPending();", unconfigured)
        self.assertLess(gate, on_tick.index("DrainPending();"))

    def test_server_comment_counts_attempts_not_frames(self) -> None:
        text = _source("MCPBridge.c")
        self.assertNotIn("frames that called TryInit", text)
        self.assertIn("real TryInit attempts (not frames", text)
        self.assertIn('&ia=" + m_InitAttempts', text)

    def test_retry_interval_is_one_second_after_the_immediate_attempt(self) -> None:
        for name in _BRIDGES:
            match = re.search(
                r"protected const float INIT_RETRY_S = ([0-9.]+);",
                _source(name),
            )
            self.assertIsNotNone(match, name)
            self.assertEqual(float(match.group(1)), 1.0)

        # 0.25 is exact in binary floating point, so the threshold is not a sum error.
        self.assertEqual(_attempt_frames([0.0]), [0])
        self.assertEqual(_attempt_frames([0.0, 0.25, 0.25, 0.25]), [0])
        self.assertEqual(_attempt_frames([0.0, 0.25, 0.25, 0.25, 0.25]), [0, 4])
        self.assertEqual(_attempt_frames([0.0, 2.5, 0.25]), [0, 1])
        self.assertEqual(
            _attempt_frames([0.0] + [0.5] * 8),
            [0, 2, 4, 6, 8],
        )


_BOT_TICK = "static void Tick(MCPBridge bridge, float nowS)"


class _Session:
    def __init__(self, object_id: int, deadline_s: float) -> None:
        self.object_id = object_id
        self.deadline_s = deadline_s


class _Player:
    def IsAlive(self) -> bool:
        return True


class _PlayerBase:
    @staticmethod
    def Cast(target: object) -> object:
        return target if isinstance(target, _Player) else None


class _Bridge:
    def __init__(self, objects: dict[int, object]) -> None:
        self.objects = objects

    def RuntimeObjectById(self, object_id: int) -> object:
        return self.objects.get(object_id)


class BotTickIdleTest(unittest.TestCase):
    """b320: Tick builds its array only while a bot runs."""

    def tick(self, running: object, objects: dict[int, object]) -> tuple[int, list]:
        built: list[int] = []
        released: list[tuple[int, str]] = []

        def array() -> enforce.EnforceArray:
            built.append(1)
            return enforce.EnforceArray()

        namespace = {
            "array": array,
            "s_Running": running,
            "PlayerBase": _PlayerBase,
            "Release": lambda bridge, object_id, why: released.append(
                (object_id, why.text)
            ),
        }
        tick = enforce.translate(
            _source("MCP_BotControl.c"),
            _BOT_TICK,
            namespace,
            class_types=("array", "Object", "PlayerBase"),
            members=("s_Running",),
        )
        tick(_Bridge(objects), 10.0)
        return len(built), released

    def test_no_running_bot_builds_no_array(self) -> None:
        # s_Running is created by the first bot_start and never set back to null.
        self.assertEqual(self.tick(enforce.EnforceArray(), {}), (0, []))
        self.assertEqual(self.tick(None, {}), (0, []))

    def test_a_running_bot_is_still_checked(self) -> None:
        running = enforce.EnforceArray()
        running.Insert(_Session(7, 20.0))
        self.assertEqual(self.tick(running, {7: _Player()}), (1, []))
        running.Insert(_Session(8, 5.0))
        self.assertEqual(
            self.tick(running, {7: _Player(), 8: _Player()}), (1, [(8, "ttl")])
        )


if __name__ == "__main__":
    unittest.main()
