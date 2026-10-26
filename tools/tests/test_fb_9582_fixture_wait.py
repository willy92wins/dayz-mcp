"""vehicle_prepare_fixture waits for OnDebugSpawn to apply before fixture_not_ready.

fb-20261026-150310-9582: DispatchVehiclePrepareFixture called OnDebugSpawn and
read IsVehicleFixtureReady in the same tick. A CarScript whose OnDebugSpawn ends
in Fill(CarFluid.FUEL, ...) read fuel_fraction 0.0 there and the tool answered
fixture_not_ready, while a telemetry_read of the same car read 1.0 later
(LFQuad_base on DayZDiag 1.30.164200, three runs). A car that is still not ready
after OnDebugSpawn is now handed to a job that reads IsVehicleFixtureReady on
every tick. The job answers ok as soon as it reads true, or fixture_not_ready
with the observed telemetry once VEHICLE_FIXTURE_WAIT_S has passed. It only reads
and publishes: OnDebugSpawn stays in the dispatch.

File-only: reads the Enforce sources; does not import the server stack.
"""

from __future__ import annotations

import re
import unittest

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import clean, method_body


BRIDGE = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"

DISPATCH = "protected bool DispatchVehiclePrepareFixture(MCPCommand command, MCPResult result)"
IS_JOB_READY = "protected bool IsJobReady(MCPJob job)"
POST_SUCCESS = "protected void PostJobSuccess(MCPJob job)"
POST_TIMEOUT = "protected void PostJobTimeout(MCPJob job)"
POST_FIXTURE = "protected void PostVehicleFixture(MCPJob job, bool ready)"

WAIT_CONSTANT = r"protected\s+const\s+float\s+VEHICLE_FIXTURE_WAIT_S\s*=\s*1(?:\.0+)?\s*;"
NOT_READY = "if (!IsVehicleFixtureReady(car))"
KIND_GATE = 'if (job.kind == "vehicle_fixture")'
QUEUE_IN_ORDER = (
    "MCPJob job = new MCPJob();",
    "job.id = command.id;",
    'job.kind = "vehicle_fixture";',
    "job.args = command.args;",
    "job.subject = car;",
    "job.deadline_s = m_ElapsedS + VEHICLE_FIXTURE_WAIT_S;",
    "job.tick_poll_sent = result.tick_poll_sent;",
    "job.tick_poll_callback = result.tick_poll_callback;",
    "job.tick_dispatch = result.tick_dispatch;",
    "m_Jobs.Insert(job.id, job);",
    "return false;",
)
# What a job that only waits must not do (the world changes in the dispatch).
MUTATIONS = (
    "OnDebugSpawn",
    "Fill(",
    "SetHealth",
    "CreateInInventory",
    "SetPosition",
    "Delete",
    "EngineStart",
    "SetThrottle",
    "Shift",
)


def _block_after(text: str, needle: str, start: int = 0) -> tuple[int, int, int]:
    """Where `needle` starts, and the body bounds of the brace block after it."""
    at = text.index(needle, start)
    brace = text.index("{", at + len(needle))
    if text[at + len(needle) : brace].strip():
        raise AssertionError(f"text between {needle!r} and its block")
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return at, brace + 1, index
    raise AssertionError(f"unterminated block after {needle!r}")


def _depth_at(text: str, index: int) -> int:
    return text.count("{", 0, index) - text.count("}", 0, index)


def _assert_in_order(case: unittest.TestCase, text: str, tokens: tuple[str, ...]) -> None:
    position = 0
    for token in tokens:
        found = text.find(token, position)
        case.assertGreaterEqual(found, 0, f"missing or out of order: {token}")
        position = found + len(token)


class Fb9582FixtureWaitContract:
    """The contract, applied to one bridge source (the shipped one or a mutant)."""

    def assert_fixture_waits(self, case: unittest.TestCase, source: str) -> None:
        case.assertIsNotNone(re.search(WAIT_CONSTANT, clean(source)), "VEHICLE_FIXTURE_WAIT_S")
        self._assert_dispatch_queues_the_wait(case, method_body(source, DISPATCH))
        self._assert_job_only_reads(case, method_body(source, IS_JOB_READY))
        self._assert_job_routes_to_fixture_reply(
            case, method_body(source, POST_SUCCESS), "PostVehicleFixture(job, true);", "MCPResult result = new MCPResult();"
        )
        self._assert_job_routes_to_fixture_reply(
            case, method_body(source, POST_TIMEOUT), "PostVehicleFixture(job, false);", 'result.error = "timeout";'
        )
        self._assert_fixture_reply(case, method_body(source, POST_FIXTURE))

    @staticmethod
    def _assert_dispatch_queues_the_wait(case: unittest.TestCase, dispatch: str) -> None:
        # OnDebugSpawn runs once, in the dispatch, behind the first readiness read.
        case.assertEqual(dispatch.count("car.OnDebugSpawn();"), 1)
        case.assertEqual(dispatch.count(NOT_READY), 2)
        first_at, first_start, first_end = _block_after(dispatch, NOT_READY)
        case.assertEqual(dispatch[first_start:first_end].strip(), "car.OnDebugSpawn();")

        # The second read, after that block and at the method's own depth, hands
        # a car that is still not ready to the job and posts nothing now.
        second_at, second_start, second_end = _block_after(dispatch, NOT_READY, first_end)
        case.assertEqual(_depth_at(dispatch, second_at), 0)
        case.assertEqual(dispatch[first_end + 1 : second_at].strip(), "")
        queue = dispatch[second_start:second_end]
        _assert_in_order(case, queue, QUEUE_IN_ORDER)
        case.assertEqual(queue.count("return"), 1)
        case.assertNotIn("PostResult", queue)
        case.assertNotIn("OnDebugSpawn", queue)

        # A car that reads ready answers now, and only the job answers not ready.
        tail = dispatch[second_end + 1 :]
        _assert_in_order(
            case,
            tail,
            (
                "PopulateTelemetryObject(car, telemetry);",
                "result.telemetry = telemetry;",
                "result.vehicle_fixture_ready = true;",
                "result.ok = true;",
                "return true;",
            ),
        )
        case.assertNotIn("fixture_not_ready", dispatch)
        case.assertNotIn("PostResult", dispatch)

    @staticmethod
    def _assert_job_only_reads(case: unittest.TestCase, is_job_ready: str) -> None:
        gate = 'else if (job.kind == "vehicle_fixture")'
        _, start, end = _block_after(is_job_ready, gate)
        case.assertEqual(
            is_job_ready[start:end].strip(),
            "return IsVehicleFixtureReady(CarScript.Cast(job.subject));",
        )

    @staticmethod
    def _assert_job_routes_to_fixture_reply(
        case: unittest.TestCase, post: str, call: str, generic_reply: str
    ) -> None:
        gate_at, start, end = _block_after(post, KIND_GATE)
        case.assertEqual(_depth_at(post, gate_at), 0)
        body = re.sub(r"\s+", " ", post[start:end]).strip()
        case.assertEqual(body, f"{call} return;")
        case.assertLess(gate_at, post.index(generic_reply))

    @staticmethod
    def _assert_fixture_reply(case: unittest.TestCase, reply: str) -> None:
        _assert_in_order(
            case,
            reply,
            (
                "MCPResult result = new MCPResult();",
                "result.id = job.id;",
                "result.tick_poll_sent = job.tick_poll_sent;",
                "result.tick_poll_callback = job.tick_poll_callback;",
                "result.tick_dispatch = job.tick_dispatch;",
                "MCPTelemetry telemetry = new MCPTelemetry();",
                "telemetry.mode = job.args.mode;",
                "result.telemetry = telemetry;",
                "CarScript car = CarScript.Cast(job.subject);",
            ),
        )

        # The car can be gone by the time the job answers: no telemetry to read.
        gone_at, gone_start, gone_end = _block_after(reply, "if (!car)")
        case.assertEqual(_depth_at(reply, gone_at), 0)
        gone = reply[gone_start:gone_end]
        _assert_in_order(
            case,
            gone,
            (
                "telemetry.found = false;",
                "result.ok = false;",
                'result.error = "fixture_not_found";',
                "PostResult(result);",
                "return;",
            ),
        )
        case.assertNotIn("PopulateTelemetryObject", gone)

        rest = reply[gone_end + 1 :]
        _assert_in_order(
            case,
            rest,
            (
                "PopulateTelemetryObject(car, telemetry);",
                "result.vehicle_fixture_ready = ready;",
                "result.ok = ready;",
            ),
        )
        not_ready_at, not_ready_start, not_ready_end = _block_after(rest, "if (!ready)")
        case.assertEqual(_depth_at(rest, not_ready_at), 0)
        case.assertEqual(
            rest[not_ready_start:not_ready_end].strip(), 'result.error = "fixture_not_ready";'
        )
        case.assertEqual(rest.count("PostResult(result);"), 1)
        case.assertGreater(rest.index("PostResult(result);"), not_ready_end)
        for token in MUTATIONS:
            case.assertNotIn(token, reply, token)


CONTRACT = Fb9582FixtureWaitContract()


def _mutant(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise AssertionError(f"mutant anchor not found exactly once: {old!r}")
    return source.replace(old, new)


class Fb9582FixtureWaitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BRIDGE.read_text(encoding="utf-8")

    def test_fb_9582_a_car_not_ready_after_on_debug_spawn_waits_for_it(self) -> None:
        CONTRACT.assert_fixture_waits(self, self.source)

    def test_fb_9582_shapes_that_answer_too_early_or_change_the_world_fail(self) -> None:
        mutants = {
            # Main before the fix: the read right after OnDebugSpawn answers.
            "answers_in_the_same_tick": _mutant(
                self.source,
                'job.kind = "vehicle_fixture";',
                'job.kind = "spawn";',
            ),
            "no_wait": _mutant(
                self.source,
                "job.deadline_s = m_ElapsedS + VEHICLE_FIXTURE_WAIT_S;",
                "job.deadline_s = m_ElapsedS;",
            ),
            "job_spawns_again": _mutant(
                self.source,
                "return IsVehicleFixtureReady(CarScript.Cast(job.subject));",
                "CarScript.Cast(job.subject).OnDebugSpawn();\n\t\t\treturn IsVehicleFixtureReady(CarScript.Cast(job.subject));",
            ),
            "timeout_answers_bare_timeout": _mutant(
                self.source,
                "PostVehicleFixture(job, false);",
                "",
            ),
            "gone_car_is_read": _mutant(
                self.source,
                'result.error = "fixture_not_found";\n\t\t\tPostResult(result);',
                'result.error = "fixture_not_found";\n\t\t\tPopulateTelemetryObject(car, telemetry);\n\t\t\tPostResult(result);',
            ),
            "timeout_reads_ok": _mutant(
                self.source,
                "result.ok = ready;",
                "result.ok = true;",
            ),
        }
        for label, source in mutants.items():
            with self.subTest(label):
                self.assertNotEqual(source, self.source)
                with self.assertRaises(AssertionError):
                    CONTRACT.assert_fixture_waits(self, source)


if __name__ == "__main__":
    unittest.main()
