"""K22-K25 of plans/2026-10-24-transport-lease-end-barrier.md, stage 1.

FIFO drain, reserved close slot, and the 65-POST callback reserve on both
bridges. Bodies are translated against doubles; this is not an Enforce VM
(see enforce_subset_helpers.py). K26-K30 live in a later stage.
"""
from __future__ import annotations

import json
import re
import unittest
from typing import Any, Callable

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceArray, EnforceMap, EnforceString, method_body, translate


SCRIPTS = addon_root() / "scripts" / "5_Mission"
SERVER_PATH = SCRIPTS / "MCPBridge.c"
CLIENT_PATH = SCRIPTS / "MCPClientBridge.c"
SERVER_FLAG = "bool deferFromWorldSpawn = m_Pending.Count() > 0;"
CLIENT_FLAG = "bool deferBehindPending = m_Pending.Count() > 0;"
POLL_SIG = "void OnPollSuccess(string data, int dataSize)"
COUNT_LINE = "int count = batch.commands.Count();"
DRAIN_SIG = "protected void DrainPending()"
QUEUE_SIG = "protected void QueuePendingOrFail(MCPCommand command)"
START_SIG = "protected void StartPoll()"
OUTSTANDING_SIG = "protected int OutstandingWork()"
GATE_SIG = "void StartPollGate()"
SERVER_GUARD = (
    "m_CallbackRefs.Count() + m_Pending.Count() + m_Jobs.Count() > "
    "MAX_CALLBACK_REFS - MAX_POLL_RESULTS - 1"
)
CLIENT_GUARD = "OutstandingWork() > MAX_CALLBACK_REFS - MAX_POLL_RESULTS - 1"


class PendingArray(EnforceArray):
    """Vanilla array Remove / RemoveOrdered (enscript.c Remove vs RemoveOrdered)."""

    def Remove(self, index: int) -> None:
        if type(index) is not int or not 0 <= index < len(self):
            raise AssertionError(f"Remove({index!r}) outside an array of {len(self)}")
        last = len(self) - 1
        if index != last:
            list.__setitem__(self, index, list.__getitem__(self, last))
        del self[last]

    def RemoveOrdered(self, index: int) -> None:
        if type(index) is not int or not 0 <= index < len(self):
            raise AssertionError(f"RemoveOrdered({index!r}) outside an array of {len(self)}")
        del self[index]


class _LogString(EnforceString):
    """Enforce string + string and string + int, as the poll Log lines do."""

    __slots__ = ()

    def __add__(self, other: object) -> "_LogString":
        if isinstance(other, EnforceString):
            return _LogString(self.data + other.data)
        if type(other) is int:
            return _LogString(self.data + str(other).encode("ascii"))
        raise TypeError(f"string + {type(other).__name__}")


class Command:
    def __init__(self, ident: int, cmd: str = "work") -> None:
        self.id = ident
        self.cmd = _LogString.of(cmd)
        # MCPCommand's ref MCPArgs args, null when the JSON has none.
        self.args = None


class Batch:
    def __init__(self, commands: PendingArray | None = None) -> None:
        # MCPCommandBatch's constructor makes an empty array.
        self.commands = commands if commands is not None else PendingArray()


class _Serializer:
    """JsonSerializer.ReadFromString over the test's own JSON: id and cmd only."""

    def ReadFromString(self, batch: Batch, data: EnforceString, _error: EnforceString) -> bool:
        decoded = json.loads(data.text)
        batch.commands = PendingArray(Command(item["id"], item["cmd"]) for item in decoded["commands"])
        return True


class _JobRunner:
    def __init__(self, jobs: int) -> None:
        self._jobs = jobs

    def Count(self) -> int:
        return self._jobs


def _read(path) -> str:
    return path.read_text(encoding="utf-8")


def _constant(source: str, name: str) -> int:
    match = re.search(r"const int " + name + r"\s*=\s*(\d+)\s*;", source)
    if match is None:
        raise AssertionError(f"missing integer capacity: {name}")
    return int(match[1])


def _poll_loop_source(source: str, flag: str) -> str:
    # From the real batch count to the end, so a clip before the flag is
    # translated too; the JSON parse above it stays out.
    body = method_body(source, POLL_SIG)
    start = body.find(COUNT_LINE)
    if start < 0:
        raise AssertionError("OnPollSuccess is missing the batch count")
    if body.find(flag, start) < 0:
        raise AssertionError("OnPollSuccess is missing the defer flag")
    return "void OnPollSuccessLoop()\n{" + body[start:] + "}\n"


def _wrap(signature: str, body: str) -> str:
    return signature + "\n{" + body + "}\n"


def _gate_source(source: str, guard: str) -> str:
    # StartPoll up to the end of its admission guard, then a probe that
    # records the admission; the request it would send stays out.
    body = method_body(source, START_SIG)
    start = body.find(guard)
    if start < 0:
        raise AssertionError("StartPoll is missing the admission guard")
    end = body.index("}", body.index("{", start)) + 1
    return _wrap(GATE_SIG, body[:end] + "\n\tAdmitted();\n")


class LeaseCloseBridgeTests(unittest.TestCase):
    def _bind(
        self,
        source: str,
        flag: str,
        *,
        drain_source: str | None = None,
        loop_source: str | None = None,
        poll_source: str | None = None,
    ) -> dict[str, Any]:
        pending = PendingArray()
        dispatched: list[tuple[int, bool]] = []
        errors: list[tuple[int, str, bool]] = []
        failures: list[str] = []
        in_callback = {"v": False}

        def Dispatch(command: Command) -> None:
            dispatched.append((command.id, in_callback["v"]))

        def PostCommandError(command: Command, code: object) -> None:
            text = code.text if isinstance(code, EnforceString) else str(code)
            errors.append((command.id, text, in_callback["v"]))

        def OnPollFail(reason: object) -> None:
            failures.append(reason.text if isinstance(reason, EnforceString) else str(reason))

        def Log(_msg: object) -> None:
            return None

        ns: dict[str, Any] = {
            "_S": _LogString,
            "Dispatch": Dispatch,
            # #286 load counters: DispatchMeasured wraps Dispatch with busy_ms
            # timing; AddLoadCounter is a saturating add. Neither changes order.
            "DispatchMeasured": Dispatch,
            "AddLoadCounter": lambda total, delta: total + delta,
            "m_LastCmdsPerPoll": 0,
            "PostCommandError": PostCommandError,
            "OnPollFail": OnPollFail,
            "Log": Log,
            "MCPCommandBatch": Batch,
            "JsonSerializer": _Serializer,
            "m_Backoff": 0.0,
            "m_Configured": True,
            "m_Pending": pending,
            "m_PollInFlight": False,
            "m_RepollNow": False,
            "m_SincePollS": 0.0,
            "m_Tick": 0,
            "m_TickPollCallback": 0,
            "m_TickPollSent": 0,
            "MAX_DISPATCH_PER_TICK": _constant(source, "MAX_DISPATCH_PER_TICK"),
            "MAX_PENDING": _constant(source, "MAX_PENDING"),
            "MAX_POLL_RESULTS": _constant(source, "MAX_POLL_RESULTS"),
            "batch": None,
        }
        drain_text = drain_source if drain_source is not None else method_body(source, DRAIN_SIG)
        queue_text = method_body(source, QUEUE_SIG)
        translate(
            _wrap(QUEUE_SIG, queue_text),
            QUEUE_SIG,
            ns,
            members=("m_Pending", "MAX_PENDING"),
        )
        translate(
            _wrap(DRAIN_SIG, drain_text),
            DRAIN_SIG,
            ns,
            class_types=("MCPCommand",),
            members=("m_Pending", "MAX_DISPATCH_PER_TICK"),
        )
        loop = loop_source if loop_source is not None else _poll_loop_source(source, flag)
        translate(
            loop,
            "void OnPollSuccessLoop()",
            ns,
            class_types=("MCPCommand",),
            members=(
                "batch",
                "m_LastCmdsPerPoll",
                "m_Pending",
                "m_RepollNow",
                "m_SincePollS",
                "m_TickPollCallback",
                "m_TickPollSent",
                "MAX_DISPATCH_PER_TICK",
                "MAX_POLL_RESULTS",
            ),
        )
        if poll_source is not None:
            translate(
                poll_source,
                POLL_SIG,
                ns,
                class_types=("JsonSerializer", "MCPCommand", "MCPCommandBatch"),
                members=(
                    "m_Backoff",
                    "m_Configured",
                    "m_LastCmdsPerPoll",
                    "m_Pending",
                    "m_PollInFlight",
                    "m_RepollNow",
                    "m_SincePollS",
                    "m_Tick",
                    "m_TickPollCallback",
                    "m_TickPollSent",
                    "MAX_DISPATCH_PER_TICK",
                ),
            )
        ns["_dispatched"] = dispatched
        ns["_errors"] = errors
        ns["_failures"] = failures
        ns["_in_callback"] = in_callback
        return ns

    def _poll(self, ns: dict[str, Any], commands: list[Command]) -> None:
        ns["batch"] = Batch(PendingArray(commands))
        ns["_in_callback"]["v"] = True
        ns["OnPollSuccessLoop"]()
        ns["_in_callback"]["v"] = False

    def _poll_answer(self, ns: dict[str, Any], commands: list[Command]) -> None:
        # The whole OnPollSuccess, given the JSON of a poll answer and its size.
        data = json.dumps({"commands": [{"id": c.id, "cmd": c.cmd.text} for c in commands]})
        ns["_in_callback"]["v"] = True
        ns["OnPollSuccess"](_LogString.of(data), len(data.encode("utf-8")))
        ns["_in_callback"]["v"] = False

    def _admits(
        self,
        peer: str,
        *,
        gate_source: str | None = None,
        outstanding_source: str | None = None,
    ) -> Callable[[int, int, int], bool]:
        # The real StartPoll guard and, on the client, the real
        # OutstandingWork(), over doubles that hold each term's count.
        source = self._peer_source(peer)[0]
        guard = "if (" + (SERVER_GUARD if peer == "server" else CLIENT_GUARD) + ")"
        admitted: list[bool] = []
        ns: dict[str, Any] = {
            "Admitted": lambda: admitted.append(True),
            "MAX_CALLBACK_REFS": _constant(source, "MAX_CALLBACK_REFS"),
            "MAX_POLL_RESULTS": _constant(source, "MAX_POLL_RESULTS"),
            "m_PollCtx": object(),
        }
        if peer == "client":
            work = outstanding_source if outstanding_source is not None else method_body(source, OUTSTANDING_SIG)
            translate(
                _wrap(OUTSTANDING_SIG, work),
                OUTSTANDING_SIG,
                ns,
                members=("m_CallbackRefs", "m_JobRunner", "m_Pending"),
            )
        translate(
            gate_source if gate_source is not None else _gate_source(source, guard),
            GATE_SIG,
            ns,
            members=("m_CallbackRefs", "m_Jobs", "m_Pending", "m_PollCtx", "MAX_CALLBACK_REFS", "MAX_POLL_RESULTS"),
        )

        def admits(callbacks: int, pending: int, jobs: int) -> bool:
            ns["m_CallbackRefs"] = EnforceArray(object() for _ in range(callbacks))
            ns["m_Pending"] = PendingArray(Command(i) for i in range(pending))
            ns["m_Jobs"] = EnforceMap()
            for job in range(jobs):
                ns["m_Jobs"].Insert(job, object())
            ns["m_JobRunner"] = _JobRunner(jobs)
            admitted.clear()
            ns["StartPollGate"]()
            return admitted == [True]

        return admits

    def _drain_all(self, ns: dict[str, Any]) -> None:
        pending: PendingArray = ns["m_Pending"]
        steps = 0
        while pending.Count() > 0:
            self.assertLess(steps, 10_000, "DrainPending did not shrink m_Pending")
            before = pending.Count()
            ns["DrainPending"]()
            self.assertLess(pending.Count(), before, "DrainPending did not shrink m_Pending")
            steps += 1

    def _peer_source(self, peer: str) -> tuple[str, str, str, str, int]:
        if peer == "server":
            return _read(SERVER_PATH), SERVER_FLAG, "lease_close", "bridge_queue_full", 32
        return _read(CLIENT_PATH), CLIENT_FLAG, "lease_close_client", "client_bridge_queue_full", 16

    def test_k22_fifo_drain_order(self) -> None:
        expected_fifo = list(range(1, 17))
        expected_remove = [1, 2, 3, 4, 5, 16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6]
        for peer in ("server", "client"):
            with self.subTest(peer=peer):
                source, flag, _close, _err, _cap = self._peer_source(peer)
                self.assertIn(flag, source)
                ns = self._bind(source, flag)
                self._poll(ns, [Command(i) for i in range(1, 17)])
                self._drain_all(ns)
                ids = [item[0] for item in ns["_dispatched"]]
                self.assertEqual(len(ids), 16, "K22 dispatch count")
                self.assertEqual(ids, expected_fifo)

                drain = method_body(source, DRAIN_SIG)
                self.assertIn("RemoveOrdered(0)", drain)
                mutant = drain.replace("RemoveOrdered(0)", "Remove(0)", 1)
                self.assertIn("Remove(0)", mutant)
                ns_neg = self._bind(source, flag, drain_source=mutant)
                self._poll(ns_neg, [Command(i) for i in range(1, 17)])
                self._drain_all(ns_neg)
                ids_neg = [item[0] for item in ns_neg["_dispatched"]]
                self.assertEqual(len(ids_neg), 16, "K22 Remove mutant dispatch count")
                self.assertEqual(ids_neg, expected_remove)

    def test_k23_new_batch_queues_behind_pending(self) -> None:
        expected = list(range(1, 15))
        overtaken = [1, 2, 3, 4, 5, 6, 7, 8, 13, 14, 9, 10, 11, 12]
        for peer in ("server", "client"):
            with self.subTest(peer=peer):
                source, flag, _close, _err, _cap = self._peer_source(peer)
                self.assertIn(flag, source)
                ns = self._bind(source, flag)
                self._poll(ns, [Command(i) for i in range(1, 13)])
                ns["DrainPending"]()
                self._poll(ns, [Command(13), Command(14)])
                self._drain_all(ns)
                ids = [item[0] for item in ns["_dispatched"]]
                self.assertEqual(len(ids), 14, "K23 dispatch count")
                self.assertEqual(ids, expected)

                mutant_loop = _poll_loop_source(source, flag).replace(
                    "m_Pending.Count() > 0", "false", 1
                )
                ns_neg = self._bind(source, flag, loop_source=mutant_loop)
                self._poll(ns_neg, [Command(i) for i in range(1, 13)])
                ns_neg["DrainPending"]()
                self._poll(ns_neg, [Command(13), Command(14)])
                self._drain_all(ns_neg)
                ids_neg = [item[0] for item in ns_neg["_dispatched"]]
                self.assertEqual(len(ids_neg), 14, "K23 overtake dispatch count")
                self.assertEqual(ids_neg, overtaken)

    def test_k24_world_spawn_never_runs_in_callback(self) -> None:
        source, flag, _close, _err, _cap = self._peer_source("server")
        self.assertIn(flag, source)
        ns = self._bind(source, flag)
        pending: PendingArray = ns["m_Pending"]
        for ident in range(5, 13):
            cmd = "world_spawn" if ident == 9 else "work"
            pending.Insert(Command(ident, cmd))
        ns["DrainPending"]()
        self._poll(ns, [Command(13), Command(14)])
        in_cb = [item for item in ns["_dispatched"] if item[1]]
        self.assertEqual(len(in_cb), 0, "no command runs in the poll callback")
        spawn_before = [item for item in ns["_dispatched"] if item[0] == 9]
        self.assertEqual(len(spawn_before), 0, "world_spawn has not run yet")
        self._drain_all(ns)
        ids = [item[0] for item in ns["_dispatched"]]
        self.assertEqual(len(ids), 10, "C5-C14 dispatch count")
        self.assertEqual(ids, list(range(5, 15)))
        spawn = [item for item in ns["_dispatched"] if item[0] == 9]
        self.assertEqual(len(spawn), 1, "world_spawn ran once")
        self.assertFalse(spawn[0][1], "world_spawn ran from DrainPending")

        loop = _poll_loop_source(source, flag)
        closing = loop.rfind("}")
        mutant_loop = loop[:closing] + "\tDrainPending();\n" + loop[closing:]
        ns_neg = self._bind(source, flag, loop_source=mutant_loop)
        pending_neg: PendingArray = ns_neg["m_Pending"]
        for ident in range(5, 13):
            cmd = "world_spawn" if ident == 9 else "work"
            pending_neg.Insert(Command(ident, cmd))
        ns_neg["DrainPending"]()
        self._poll(ns_neg, [Command(13), Command(14)])
        spawn_neg = [item for item in ns_neg["_dispatched"] if item[0] == 9]
        self.assertEqual(len(spawn_neg), 1, "mutant world_spawn ran")
        self.assertTrue(spawn_neg[0][1], "mutant world_spawn ran in the callback")

        # With nothing pending, the world_spawn arm defers by itself: the
        # command before it runs in the callback, and world_spawn and the one
        # after it wait for DrainPending.
        batch = [Command(1), Command(2, "world_spawn"), Command(3)]
        ns_empty = self._bind(source, flag)
        self._poll(ns_empty, batch)
        self.assertEqual(ns_empty["_dispatched"], [(1, True)])
        self.assertEqual([command.id for command in ns_empty["m_Pending"]], [2, 3])
        self._drain_all(ns_empty)
        self.assertEqual(ns_empty["_dispatched"], [(1, True), (2, False), (3, False)])
        no_arm = loop.replace("deferFromWorldSpawn = true;", "", 1)
        self.assertNotEqual(no_arm, loop)
        ns_no_arm = self._bind(source, flag, loop_source=no_arm)
        self._poll(ns_no_arm, batch)
        self.assertIn((2, True), ns_no_arm["_dispatched"], "without the arm, world_spawn runs in the callback")

    def test_k25_reserved_slot_and_callback_budget(self) -> None:
        for peer in ("server", "client"):
            with self.subTest(peer=peer):
                source, flag, close_cmd, err_code, max_pending = self._peer_source(peer)
                self.assertIn(flag, source)
                cap = _constant(source, "MAX_CALLBACK_REFS")
                batch = _constant(source, "MAX_POLL_RESULTS")
                self.assertEqual(cap, 128)
                self.assertEqual(batch, 64)

                # Above the count only the null check reads the batch, so a
                # clip or a Resize there fails here, before the poll runs.
                body = method_body(source, POLL_SIG)
                head = body[: body.find(COUNT_LINE)]
                self.assertEqual(head.count("batch.commands"), 1, "only the null check reads the batch")

                # The whole OnPollSuccess, its parse and checks included, on a
                # valid answer of 64 ordinary commands and a close.
                full = _wrap(POLL_SIG, body)
                ns = self._bind(source, flag, poll_source=full)
                ordinary = 64
                close_id = 65
                cmds = [Command(i) for i in range(1, ordinary + 1)]
                cmds.append(Command(close_id, close_cmd))
                self._poll_answer(ns, cmds)
                pending: PendingArray = ns["m_Pending"]
                dispatched = ns["_dispatched"]
                errors = ns["_errors"]
                n_disp = len(dispatched)
                n_err = len(errors)
                n_queued = pending.Count()
                self.assertEqual(ns["_failures"], [], "a valid answer is no poll failure")
                self.assertEqual(n_disp, 4, "inline dispatch")
                if peer == "server":
                    self.assertEqual(n_queued, 32, "server queued including close")
                    self.assertEqual(n_err, 29, "server refusals")
                else:
                    self.assertEqual(n_queued, 16, "client queued including close")
                    self.assertEqual(n_err, 45, "client refusals")
                self.assertEqual(n_disp + n_queued + n_err, 65, "batch is not clipped")
                for item in errors:
                    self.assertEqual(item[1], err_code)
                self.assertEqual(pending.Get(pending.Count() - 1).id, close_id)
                self.assertEqual(pending.Get(pending.Count() - 1).cmd, _LogString.of(close_cmd))

                # After a failed poll the backoff is positive (OnPollFail): the
                # next good answer is still read whole, and it resets the backoff.
                ns_back = self._bind(source, flag, poll_source=full)
                ns_back["m_Backoff"] = 1.0
                self._poll_answer(ns_back, cmds)
                back_pending: PendingArray = ns_back["m_Pending"]
                self.assertEqual(ns_back["_failures"], [])
                back_total = len(ns_back["_dispatched"]) + back_pending.Count() + len(ns_back["_errors"])
                self.assertEqual(back_total, 65, "a positive backoff drops nothing")
                self.assertEqual(back_pending.Get(back_pending.Count() - 1).id, close_id)
                self.assertEqual(ns_back["m_Backoff"], 0.0, "a good answer resets the backoff")

                # A clip of the count, or a discard by size before it, loses the close.
                clipped = full.replace(
                    COUNT_LINE,
                    COUNT_LINE + "\n\t\tif (count > MAX_POLL_RESULTS)\n\t\t{\n\t\t\tcount = MAX_POLL_RESULTS;\n\t\t}",
                    1,
                )
                self.assertNotEqual(clipped, full)
                ns_clip = self._bind(source, flag, poll_source=clipped)
                self._poll_answer(ns_clip, cmds)
                clip_pending: PendingArray = ns_clip["m_Pending"]
                clip_total = len(ns_clip["_dispatched"]) + clip_pending.Count() + len(ns_clip["_errors"])
                self.assertEqual(clip_total, 64, "a count clipped to MAX_POLL_RESULTS")
                self.assertNotIn(close_id, [command.id for command in clip_pending])
                discarded = full.replace("if (!parsed)", "if (!parsed || dataSize > MAX_POLL_RESULTS)", 1)
                self.assertNotEqual(discarded, full)
                ns_discard = self._bind(source, flag, poll_source=discarded)
                self._poll_answer(ns_discard, cmds)
                discard_total = (
                    len(ns_discard["_dispatched"]) + ns_discard["m_Pending"].Count() + len(ns_discard["_errors"])
                )
                self.assertEqual(discard_total, 0, "an answer discarded by its size")
                self.assertEqual(ns_discard["_failures"], ["parse_failed"])

                ns_slot = self._bind(source, flag)
                for ident in range(max_pending - 1):
                    ns_slot["m_Pending"].Insert(Command(1000 + ident))
                self._poll(ns_slot, [Command(1), Command(2, close_cmd)])
                self.assertEqual(len(ns_slot["_errors"]), 1, "one ordinary refusal")
                self.assertEqual(ns_slot["_errors"][0][1], err_code)
                self.assertEqual(ns_slot["m_Pending"].Count(), max_pending, "close took the last slot")
                self.assertEqual(ns_slot["m_Pending"].Get(max_pending - 1).id, 2)

                ns_full = self._bind(source, flag)
                for ident in range(max_pending):
                    ns_full["m_Pending"].Insert(Command(2000 + ident))
                self._poll(ns_full, [Command(3, close_cmd)])
                self.assertEqual(len(ns_full["_errors"]), 1, "second close refused")
                self.assertEqual(ns_full["_errors"][0][1], err_code)
                self.assertEqual(ns_full["m_Pending"].Count(), max_pending, "full pending unchanged")

                poll = method_body(source, START_SIG)
                if peer == "server":
                    expression = SERVER_GUARD
                else:
                    expression = CLIENT_GUARD
                guard = "if (" + expression + ")"
                self.assertIn(guard, poll)
                # The real guard, and on the client the real OutstandingWork():
                # 63 outstanding admit a poll and 64 refuse it, whichever
                # terms make them up.
                admits = self._admits(peer)
                for terms in ((63, 0, 0), (0, 63, 0), (0, 0, 63), (21, 21, 21)):
                    self.assertTrue(admits(*terms), f"outstanding {terms} admits a poll")
                for terms in ((64, 0, 0), (0, 64, 0), (0, 0, 64), (21, 21, 22)):
                    self.assertFalse(admits(*terms), f"outstanding {terms} refuses a poll")

                # A count that loses a term, or returns the wrong value, admits 64.
                if peer == "client":
                    work = method_body(source, OUTSTANDING_SIG)
                    variants = [(work.replace("return total;", "return 0;", 1), (64, 0, 0))]
                    for term, terms in (
                        ("m_CallbackRefs.Count()", (64, 0, 0)),
                        ("m_Pending.Count()", (0, 64, 0)),
                        ("m_JobRunner.Count()", (0, 0, 64)),
                    ):
                        variants.append((work.replace("total = total + " + term + ";", "", 1), terms))
                    for variant, terms in variants:
                        self.assertNotEqual(variant, work)
                        self.assertTrue(
                            self._admits(peer, outstanding_source=variant)(*terms),
                            f"a wrong OutstandingWork() admits {terms}",
                        )
                else:
                    gate = _gate_source(source, guard)
                    for term, terms in (
                        ("m_CallbackRefs.Count() + ", (64, 0, 0)),
                        (" + m_Pending.Count()", (0, 64, 0)),
                        (" + m_Jobs.Count()", (0, 0, 64)),
                    ):
                        dropped = gate.replace(term, "", 1)
                        self.assertNotEqual(dropped, gate)
                        self.assertTrue(
                            self._admits(peer, gate_source=dropped)(*terms),
                            f"a guard without {term.strip(' +')} admits {terms}",
                        )


if __name__ == "__main__":
    unittest.main()
