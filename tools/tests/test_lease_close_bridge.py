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
EMOTE_PATH = addon_root() / "scripts" / "4_World" / "MCP_Emote.c"
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

    __radd__ = __add__


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



class _CloseEntry:
    def __init__(self):
        self.name = ""
        self.state = ""
        self.reason = ""


class _CloseFloor:
    def __init__(self):
        self.net_low = 0
        self.net_high = 0
        self.conn_gen = 0
        self.move_id = 0
        self.weapon_id = 0


class _CloseCensus:
    def __init__(self):
        self.generation = ""
        self.lease_id = ""
        self.peer = ""
        self.state = ""
        self.entries = EnforceArray()
        self.move_floor = 0
        self.weapon_floor = 0
        self.conn_gen = 0
        self.net_low = 0
        self.net_high = 0
        self.floors = EnforceArray()


class LeaseCloseCensusTests(unittest.TestCase):
    """K26-K30: census builders, observer, gate, and registers (offline)."""

    def _emote_manager(self, *, playing=False, pending=False, surrendered=False, queued=False, synced=False):
        # EmoteManager double whose MCP_IsEmoteLive / MCP_CancelQueuedEmote
        # are the real MCP_Emote.c bodies, run on this fake vanilla state
        # (IsEmotePlaying = playing || surrendered || pending).
        state = {"cancelled": 0, "ended": 0}
        launcher = type("EL", (), {"WasSynced": lambda self: synced})() if queued else None
        emote_source = _read(EMOTE_PATH)
        helper_ns = {
            "IsEmotePlaying": lambda: playing or pending or surrendered,
            "m_MenuEmote": launcher,
        }

        def _clear():
            state["cancelled"] += 1
            helper_ns["m_MenuEmote"] = None

        helper_ns["ClearEmoteLauncher"] = _clear
        for sig in ("bool MCP_IsEmoteLive()", "void MCP_CancelQueuedEmote()"):
            translate(_wrap(sig, method_body(emote_source, sig)), sig, helper_ns, members=("m_MenuEmote",))
        manager = type("EM", (), {
            "IsEmotePlaying": lambda self: playing or pending or surrendered,
            "MCP_IsEmoteLive": lambda self: helper_ns["MCP_IsEmoteLive"](),
            "MCP_CancelQueuedEmote": lambda self: helper_ns["MCP_CancelQueuedEmote"](),
            "MCP_RequestEmoteEnd": lambda self: state.__setitem__("ended", state["ended"] + 1) or True,
        })()
        return manager, state

    def _client_census(self, source, *, active=(), conn_gen=7, mutant_clean=False, emote_mcp=False, emote_playing=False, mutant_emote=None, emote_pending=False, emote_queued=False, emote_no_mgr=False):
        # Translate the real client census builder against controller doubles.
        flags = {name: (name in active) for name in (
            "vehicle_drive", "input_trigger", "player_move", "weapon", "look_at",
            "camera", "camera_path", "dialog", "action_hold", "samplers", "action_input_in_flight")}
        class _Ctl:
            def __init__(self, fn):
                self._fn = fn
            def __call__(self, *a):
                return self._fn(*a)
        def ctl(name):
            return _Ctl(lambda *a, n=name: flags.get(n, False))
        manager = type("AM", (), {"MCP_HoldBusy": lambda self: flags["action_hold"]})()
        _emote_mgr_real, _emote_state = self._emote_manager(playing=emote_playing, pending=emote_pending, queued=emote_queued)
        emote_mgr = None if emote_no_mgr else _emote_mgr_real
        player = type("P", (), {
            "GetNetworkID": lambda self, low, high: None,
            "MCPConnGenValue": lambda self: conn_gen,
            "GetActionManager": lambda self: manager,
            "GetEmoteManager": lambda self: emote_mgr,
        })()
        game = type("G", (), {"GetPlayer": lambda self: player})()
        runner = type("R", (), {
            "CountExcluding": lambda self, kind: 1 if "jobs" in active else 0,
            "GetElapsedS": lambda self: 0.0,
        })()
        ns = {
            "GetGame": lambda: game,
            "PlayerBase": type("PB", (), {"Cast": staticmethod(lambda x: x)}),
            "ActionManagerClient": type("AMC", (), {"Cast": staticmethod(lambda x: x)}),
            "MCPCarDrive": type("C", (), {"s_Active": flags["vehicle_drive"], "Clear": staticmethod(lambda: None)}),
            "MCPInputTriggerControl": type("I", (), {"IsHeld": staticmethod(lambda: flags["input_trigger"]), "ReleaseAll": staticmethod(lambda cause: None)}),
            "MCPPlayerMoveControl": type("M", (), {
                "IsActive": staticmethod(lambda: flags["player_move"]),
                "ServerReleasePending": staticmethod(lambda: False),
                "Generation": staticmethod(lambda: 7),
                "ReleaseAll": staticmethod(lambda cause: None)}),
            "MCPWeaponControl": type("W", (), {"IsBusy": staticmethod(lambda: flags["weapon"]), "CloseRequestFloor": staticmethod(lambda: 4)}),
            "MCPLookAtControl": type("L", (), {"IsActive": staticmethod(lambda: flags["look_at"])}),
            "MCPHoldControl": type("H", (), {"IsActive": staticmethod(lambda: flags["action_hold"]), "Shutdown": staticmethod(lambda cause: None), "InputInFlight": staticmethod(lambda: flags.get("action_input_in_flight", False))}),
            "MCPVehicleTrace": type("V", (), {"IsActive": staticmethod(lambda: flags["samplers"]), "Abort": staticmethod(lambda cause: None)}),
            "MCPAnimTimeline": type("A", (), {"IsActive": staticmethod(lambda: False), "Abort": staticmethod(lambda cause: None)}),
            "MCPPlayerTrace": type("T", (), {"IsActive": staticmethod(lambda: False), "Abort": staticmethod(lambda cause: None)}),
            "MCPLeaseCloseCensus": _CloseCensus,
            "MCPLeaseCloseEntry": _CloseEntry,
            "MCPLeaseCloseFloor": _CloseFloor,
            "m_CameraPath": type("CP", (), {"IsPlaying": lambda self: flags["camera_path"]})(),
            "m_ActiveCam": object() if "camera" in active else None,
            "m_CameraHandoffPending": False,
            "m_PlayerSimulationDisabled": "player_simulation" in active,
            "m_ControlsSuppressed": "controls_hud" in active,
            "m_Dialog": type("D", (), {"IsOpen": lambda self: "dialog" in active})() if "dialog" in active else None,
            "m_UiPreviewRoot": object() if "ui_preview" in active else None,
            "m_JobRunner": runner,
            "m_MCPEmoteStarted": emote_mcp,
        }
        for sig, name in (
            ("protected void AddCloseEntry(MCPLeaseCloseCensus census, string name, bool active, string reason)", "AddCloseEntry"),
            ("protected void AddCloseDebt(MCPLeaseCloseCensus census, string name, bool active, string reason)", "AddCloseDebt"),
            ("protected bool LeaseCloseCensusClean(MCPLeaseCloseCensus census)", "LeaseCloseCensusClean"),
            ("protected bool LeaseCloseCensusSettled(MCPLeaseCloseCensus census)", "LeaseCloseCensusSettled"),
            ("protected MCPLeaseCloseCensus BuildLeaseCloseCensus(MCPArgs closeArgs)", "BuildLeaseCloseCensus"),
        ):
            body = method_body(source, sig)
            if mutant_clean and name == "LeaseCloseCensusClean":
                body = body.replace('census.entries.Get(i).state != "clean"', "false", 1)
            if mutant_emote == "census_ignores_emote" and name == "BuildLeaseCloseCensus":
                body = body.replace('AddCloseEntry(census, "emote", emoteActive', 'AddCloseEntry(census, "emote", false', 1)
            if mutant_emote == "census_counts_foreign" and name == "BuildLeaseCloseCensus":
                body = body.replace('if (m_MCPEmoteStarted)', 'if (true)', 1)
            if mutant_emote == "no_manager_clean" and name == "BuildLeaseCloseCensus":
                body = body.replace('if (!closeEmotes || closeEmotes.MCP_IsEmoteLive())', 'if (closeEmotes && closeEmotes.MCP_IsEmoteLive())', 1)
            translate(_wrap(sig, body), sig, ns, class_types=("MCPLeaseCloseCensus", "MCPLeaseCloseEntry", "PlayerBase", "ActionManagerClient", "EmoteManager"),
                      members=("m_CameraPath", "m_ActiveCam", "m_CameraHandoffPending", "m_PlayerSimulationDisabled",
                               "m_ControlsSuppressed", "m_Dialog", "m_UiPreviewRoot", "m_JobRunner", "m_MCPEmoteStarted"))
        args = type("Args", (), {"close_generation": "g1", "close_lease_id": "l1"})()
        self._last_client_ns = ns
        return ns["BuildLeaseCloseCensus"](args)

    def test_k26_client_census_clean_and_residue(self):
        source = _read(CLIENT_PATH)
        census = self._client_census(source)
        self.assertEqual(census.state.text, "clean")
        self.assertEqual(census.move_floor, 7)
        self.assertEqual(census.weapon_floor, 4)
        self.assertEqual(census.conn_gen, 7)
        # NEG: one controller left active names exactly that entry (kills
        # M1: a Clean that never returns false would answer clean here).
        census2 = self._client_census(source, active=("weapon",))
        residue = [e.name.text for e in census2.entries if e.state.text != "clean"]
        self.assertEqual(residue, ["weapon"])
        # §4 #263 NEG: a path still playing after the stop names only
        # camera_path; the stop itself is pinned in the dispatch order.
        census_path = self._client_census(source, active=("camera_path",))
        residue_path = [e.name.text for e in census_path.entries if e.state.text != "clean"]
        self.assertEqual(residue_path, ["camera_path"])
        self.assertEqual(census_path.state.text, "residue")
        self.assertFalse(self._last_client_ns["LeaseCloseCensusSettled"](census_path))
        self.assertEqual(census2.state.text, "residue")
        self.assertFalse(self._last_client_ns["LeaseCloseCensusSettled"](census2))
        # H3: process-lifetime input debt is unverified: census residue,
        # but the observer is settled and does not wait 25 s for it.
        census3 = self._client_census(source, active=("action_input_in_flight",))
        debt = [e for e in census3.entries if e.name.text == "action_input_in_flight"]
        self.assertEqual(len(debt), 1)
        self.assertEqual(debt[0].state.text, "unverified")
        self.assertEqual(census3.state.text, "residue")
        self.assertTrue(self._last_client_ns["LeaseCloseCensusSettled"](census3))

    def _emote_stop(self, source, *, started, playing, mutant=None, queued=False, synced=False):
        emote_mgr, emote_state = self._emote_manager(playing=playing, queued=queued, synced=synced)
        player = type("P", (), {"GetEmoteManager": lambda self: emote_mgr})()
        game = type("G", (), {"GetPlayer": lambda self: player})()
        ns = {
            "GetGame": lambda: game,
            "PlayerBase": type("PB", (), {"Cast": staticmethod(lambda x: x)}),
            "m_MCPEmoteStarted": started,
        }
        sig = "protected void StopLeaseCloseEmote()"
        body = method_body(source, sig)
        if mutant == "no_stop":
            body = body.replace("emotes.MCP_RequestEmoteEnd();", "", 1)
        if mutant == "stop_counts_foreign":
            body = body.replace("if (!m_MCPEmoteStarted)", "if (false)", 1)
        translate(_wrap(sig, body), sig, ns, class_types=("PlayerBase", "EmoteManager"),
                  members=("m_MCPEmoteStarted",))
        ns["StopLeaseCloseEmote"]()
        ns["_emote_state"] = emote_state
        return [1] * emote_state["ended"], ns

    def _emote_maintain(self, source, *, started, mutant=None, no_manager=False, **live_kwargs):
        emote_mgr, _emote_state = self._emote_manager(**live_kwargs)
        if no_manager:
            emote_mgr = None
        player = type("P", (), {"GetEmoteManager": lambda self: emote_mgr})()
        game = type("G", (), {"GetPlayer": lambda self: player})()
        ns = {
            "GetGame": lambda: game,
            "PlayerBase": type("PB", (), {"Cast": staticmethod(lambda x: x)}),
            "m_MCPEmoteStarted": started,
        }
        sig = "protected void MaintainMCPEmoteStarted()"
        body = method_body(source, sig)
        if mutant == "no_maintain":
            body = body.replace("if (!emotes.MCP_IsEmoteLive())", "if (false)", 1)
        translate(_wrap(sig, body), sig, ns, class_types=("PlayerBase", "EmoteManager"),
                  members=("m_MCPEmoteStarted",))
        ns["MaintainMCPEmoteStarted"]()
        return ns["m_MCPEmoteStarted"]

    def test_emote_close_census_pos_neg(self):
        source = _read(CLIENT_PATH)
        # POS: MCP-started emote still playing is the only residue, and
        # the observer does not settle while it plays.
        census = self._client_census(source, emote_mcp=True, emote_playing=True)
        residue = [e.name.text for e in census.entries if e.state.text != "clean"]
        self.assertEqual(residue, ["emote"])
        self.assertEqual(census.state.text, "residue")
        self.assertFalse(self._last_client_ns["LeaseCloseCensusSettled"](census))
        # POS settled: the same emote ended, so the census is clean and
        # the MCP-started mark is cleared for a later foreign emote.
        census2 = self._client_census(source, emote_mcp=True, emote_playing=False)
        self.assertEqual(census2.state.text, "clean")
        self.assertFalse(self._last_client_ns["m_MCPEmoteStarted"])
        # NEG: a foreign emote playing, none started by MCP, is no residue.
        census3 = self._client_census(source, emote_mcp=False, emote_playing=True)
        self.assertEqual(census3.state.text, "clean")
        # Mutant: a census that ignores the emote answers clean on POS.
        mutant = self._client_census(source, emote_mcp=True, emote_playing=True, mutant_emote="census_ignores_emote")
        self.assertEqual(mutant.state.text, "clean")
        # Mutant: a census without the MCP-started limit counts the foreigner.
        mutant_f = self._client_census(source, emote_mcp=False, emote_playing=True, mutant_emote="census_counts_foreign")
        self.assertEqual([e.name.text for e in mutant_f.entries if e.state.text != "clean"], ["emote"])

    def test_emote_close_stop_pos_neg(self):
        source = _read(CLIENT_PATH)
        # POS: the close ends the MCP-started emote that still plays.
        calls, _ns = self._emote_stop(source, started=True, playing=True)
        self.assertEqual(len(calls), 1)
        # NEG: a foreign emote is not ended; an MCP emote already over neither.
        calls_f, _ = self._emote_stop(source, started=False, playing=True)
        self.assertEqual(calls_f, [])
        calls_done, ns_done = self._emote_stop(source, started=True, playing=False)
        self.assertEqual(calls_done, [])
        self.assertFalse(ns_done["m_MCPEmoteStarted"])
        # Mutant: without the end call, POS sends nothing.
        calls_m, _ = self._emote_stop(source, started=True, playing=True, mutant="no_stop")
        self.assertEqual(calls_m, [])
        # Mutant: without the MCP-started guard, the foreigner is ended.
        calls_mf, _ = self._emote_stop(source, started=False, playing=True, mutant="stop_counts_foreign")
        self.assertEqual(len(calls_mf), 1)
        # Wiring: the stop runs in the client close before the census.
        body = method_body(source, "protected bool DispatchLeaseCloseClient(MCPCommand command, MCPResult result)")
        self.assertLess(body.find("StopLeaseCloseEmote()"), body.find("BuildLeaseCloseCensus(command.args)"))
        dispatch = method_body(source, "protected bool DispatchActorEmote(MCPCommand command, MCPResult result)")
        self.assertIn("m_MCPEmoteStarted = true;", dispatch)

    def test_emote_mark_falls_after_job_ends_reviewer_case(self):
        source = _read(CLIENT_PATH)
        # Wiring: the tick maintains the mark before the job runner.
        tick = method_body(source, "void OnTick(float timeslice)")
        maintain_at = tick.find("MaintainMCPEmoteStarted();")
        self.assertGreaterEqual(maintain_at, 0)
        self.assertLess(maintain_at, tick.find("m_JobRunner.Tick"))
        # POS live: queued, pending or playing keeps the mark.
        self.assertTrue(self._emote_maintain(source, started=True, queued=True))
        self.assertTrue(self._emote_maintain(source, started=True, pending=True))
        self.assertTrue(self._emote_maintain(source, started=True, playing=True))
        # Exact reviewer case: job ended by wait_s (started, then the emote
        # stops with no job), a foreign emote plays, then the close.
        flag = self._emote_maintain(source, started=True, playing=False)
        self.assertFalse(flag)
        # M4: with no manager the mark is kept (nothing can be read).
        self.assertTrue(self._emote_maintain(source, started=True, no_manager=True))
        calls, _ns = self._emote_stop(source, started=flag, playing=True)
        self.assertEqual(calls, [])
        census = self._client_census(source, emote_mcp=flag, emote_playing=True)
        self.assertEqual(census.state.text, "clean")
        # Mutant: a maintain that never drops the mark leaves it on, and
        # the same foreign emote is then ended and counted.
        flag_m = self._emote_maintain(source, started=True, playing=False, mutant="no_maintain")
        self.assertTrue(flag_m)
        calls_m, _ = self._emote_stop(source, started=flag_m, playing=True)
        self.assertEqual(len(calls_m), 1)
        census_m = self._client_census(source, emote_mcp=flag_m, emote_playing=True)
        self.assertEqual([e.name.text for e in census_m.entries if e.state.text != "clean"], ["emote"])

    def test_emote_queued_launcher_close_and_no_manager_census(self):
        self.assertNotIn("m_SyncRequestSent", _read(EMOTE_PATH))
        source = _read(CLIENT_PATH)
        # P3-1 POS: a queued, unsynced launcher is cancelled, not ended,
        # and the mark falls; a synced launcher is not cancelled here.
        calls, ns_q = self._emote_stop(source, started=True, playing=False, queued=True, synced=False)
        self.assertEqual(calls, [])
        self.assertEqual(ns_q["_emote_state"]["cancelled"], 1)
        self.assertFalse(ns_q["m_MCPEmoteStarted"])
        calls_s, ns_s = self._emote_stop(source, started=True, playing=False, queued=True, synced=True)
        self.assertEqual(ns_s["_emote_state"]["cancelled"], 0)
        # Census counts the queued launcher as live residue.
        census_q = self._client_census(source, emote_mcp=True, emote_playing=False, emote_queued=True)
        self.assertEqual([e.name.text for e in census_q.entries if e.state.text != "clean"], ["emote"])
        # E13: with no manager the census reads residue, not clean.
        census_nm = self._client_census(source, emote_mcp=True, emote_no_mgr=True)
        self.assertEqual([e.name.text for e in census_nm.entries if e.state.text != "clean"], ["emote"])
        mutant_nm = self._client_census(source, emote_mcp=True, emote_no_mgr=True, mutant_emote="no_manager_clean")
        self.assertEqual(mutant_nm.state.text, "clean")

    def test_emote_stop_then_census_stays_residue_e11(self):
        source = _read(CLIENT_PATH)
        # E11: Stop asks for the end (async) and does not clear the mark
        # while the emote still plays, so the following census is residue.
        calls, ns = self._emote_stop(source, started=True, playing=True)
        self.assertEqual(len(calls), 1)
        self.assertTrue(ns["m_MCPEmoteStarted"])
        census = self._client_census(source, emote_mcp=ns["m_MCPEmoteStarted"], emote_playing=True)
        self.assertEqual([e.name.text for e in census.entries if e.state.text != "clean"], ["emote"])

    def test_h4_inventory_liquidate_only_in_pre_send_branch(self):
        # R5/R6 (P3-3): the only Liquidate of DispatchInventoryMove sits
        # in the performError branch, before the phase==1 and Ready paths
        # that run after a Send, so a sent RPC keeps its row (§2.7).
        source = _read(SERVER_PATH)
        body = method_body(source, "protected bool DispatchInventoryMove(MCPCommand command, MCPResult result)")
        self.assertEqual(body.count("LiquidateLeaseCloseRpc("), 1)
        self.assertLess(body.find('if (performError != "")'), body.find("LiquidateLeaseCloseRpc("))
        self.assertLess(body.find("LiquidateLeaseCloseRpc("), body.find("if (clientRpc && job.phase == 1)"))

    def test_h3_settled_gates_dispatch_and_observer(self):
        # H3 (D-r2-H3): unverified debt must not hold the answer; residue must.
        # Kills N1-N3: reverting any of these three call sites to
        # LeaseCloseCensusClean reintroduces the 25 s wait for process debt.
        for path, sig in (
            (SERVER_PATH, "protected bool DispatchLeaseClose(MCPCommand command, MCPResult result)"),
            (CLIENT_PATH, "protected bool DispatchLeaseCloseClient(MCPCommand command, MCPResult result)"),
        ):
            with self.subTest(path=path.name):
                src = _read(path)
                body = method_body(src, sig)
                self.assertIn("if (LeaseCloseCensusSettled(census))", body)
                self.assertNotIn("LeaseCloseCensusClean(", body)
                self.assertEqual(src.count("return LeaseCloseCensusSettled(BuildLeaseCloseCensus(job.args));"), 1)
                self.assertNotIn("return LeaseCloseCensusClean(BuildLeaseCloseCensus(job.args));", src)

    def test_h1_h2_symbol_pins(self):
        client = _read(CLIENT_PATH)
        census = method_body(client, "protected MCPLeaseCloseCensus BuildLeaseCloseCensus(MCPArgs closeArgs)")
        self.assertIn("MCPCarDrive.s_Active", census)
        addon = addon_root() / "scripts"
        for path in sorted(addon.rglob("*.c")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("MCPCarDrive.IsActive(", text, str(path))
            self.assertNotIn(".GetFSM()", text, str(path))
        bots = _read(SCRIPTS / "MCP_BotControl.c")
        self.assertNotIn("GetState(", bots)
        self.assertNotIn("GetName(", bots)
        self.assertIn("MCP_LeaseCloseState()", bots)
        bot_state = _read(SCRIPTS.parent / "4_World" / "MCP_BotState.c")
        self.assertIn("m_ActiveFSM.GetCurrentState()", bot_state)
        self.assertIn("m_IdleState", bot_state)
        self.assertIn("modded class Bot", bot_state)

    def test_k29_client_not_in_game_gate(self):
        source = _read(CLIENT_PATH)
        dispatch = method_body(source, "protected void Dispatch(MCPCommand command)")
        self.assertLess(dispatch.find("IsClientInGame()"), dispatch.find("lease_close_client"))

    def test_k26_k28_dispatch_order_and_observer(self):
        source = _read(CLIENT_PATH)
        body = method_body(source, "protected bool DispatchLeaseCloseClient(MCPCommand command, MCPResult result)")
        steps = ["FinishLeaseClosed()", "m_UiPreviewRoot.Unlink()", "MCPCarDrive.Clear()",
                 'MCPInputTriggerControl.ReleaseAll("shutdown")', 'MCPPlayerMoveControl.ReleaseAll("shutdown")',
                 "StopCameraPath()", "RestoreGameplay()", "ReleaseCamera()", 'MCPHoldControl.Shutdown("shutdown")',
                 'MCPVehicleTrace.Abort("shutdown")', "StopLeaseCloseEmote()", "AbortAll(this)", "BuildLeaseCloseCensus(command.args)"]
        pos = -1
        for step in steps:
            nxt = body.find(step, pos + 1)
            self.assertGreater(nxt, pos, f"step out of order or missing: {step}")
            pos = nxt
        self.assertIn('job.kind = "lease_close";', body)
        self.assertIn("GetElapsedS() + 25.0", body)

    def test_k27_k30_server_census_and_registers(self):
        source = _read(SERVER_PATH)
        body = method_body(source, "protected bool DispatchLeaseClose(MCPCommand command, MCPResult result)")
        for step in ("MCPBotControl.ShutdownAll(this)", "MCPSurvivorDrive.ShutdownAll(this)", "MCPMoveCloseServer(carried.move_id, carried.conn_gen)",
                     "MCPWeaponCloseServer(carried.weapon_id, carried.conn_gen)", "PostJobTimeout(job)",
                     "LeaseCloseExtensionStop()", "BuildLeaseCloseCensus(command.args)"):
            self.assertIn(step, body, step)
        self.assertIn('job.kind != "server_perf"', body, "server_perf is kept")
        self.assertIn("RegisterDeletionRefs(job)", body, "deletion refs transfer before the job drops")
        self.assertIn('closeJob.kind = "lease_close";', body)
        self.assertIn("m_ElapsedS + 25.0", body)
        census = method_body(source, "protected MCPLeaseCloseCensus BuildLeaseCloseCensus(MCPArgs closeArgs)")
        for name in ("bots", "survivor_drive", "infected_drive", "move_copy", "weapon", "jobs", "rpc_in_flight", "deletion_pending"):
            self.assertIn(f'"{name}"', census, name)
        self.assertIn("LeaseCloseExtensionCensus(census);", census)
        # H7: registration sits at the real send, not in Dispatch.
        dispatch = method_body(source, "protected void Dispatch(MCPCommand command)")
        self.assertNotIn("RegisterLeaseCloseRpc", dispatch)
        self.assertIn('RegisterLeaseCloseRpc(job.id, "inventory_move");', method_body(source, "protected bool DispatchInventoryMove(MCPCommand command, MCPResult result)"))
        self.assertIn('RegisterLeaseCloseRpc(command.id, "hands_take");', method_body(source, "protected bool DispatchHandsTake(MCPCommand command, MCPResult result)"))
        self.assertIn('RegisterLeaseCloseRpc(command.id, "hands_attach");', method_body(source, "protected bool DispatchHandsAttach(MCPCommand command, MCPResult result)"))
        self.assertIn('RegisterLeaseCloseRpc(command.id, "notify_players");', method_body(source, "protected bool DispatchNotifyPlayers(MCPCommand command, MCPResult result)"))
        # NEG pins: the RPC register never deletes a row on a job-ready readback.
        register = method_body(source, "protected void RegisterLeaseCloseRpc(int requestId, string kind)")
        self.assertNotIn("Remove", register)
        self.assertIn("m_InfectedDebt = true;", source, "infected debt survives an empty registry")

    def test_k27_k28_hooks_and_observer_timeout(self):
        source = _read(SERVER_PATH)
        self.assertIn("protected void LeaseCloseExtensionStop()", source)
        self.assertIn("protected void LeaseCloseExtensionCensus(MCPLeaseCloseCensus census)", source)
        post = method_body(source, "protected void PostLeaseCloseJob(MCPJob job, bool timedOut)")
        self.assertIn('"observer_timeout"', post)
        client = _read(CLIENT_PATH)
        post_client = method_body(client, "protected void PostLeaseCloseJob(MCPJob job, bool timedOut)")
        self.assertIn('"observer_timeout"', post_client)
        runner = _read(SCRIPTS / "MCPJobRunner.c")
        abort = method_body(runner, "void AbortAll(MCPJobRunnerOwner owner)")
        self.assertIn("MCP_PostJobTimeout(job)", abort)


    # ---- D-r2-H4: a failure before Send leaves no RPC row ----
    def _rpc_register_ns(self, source):
        ns = {
            "_S": _LogString,
            "m_LeaseCloseRpcIds": PendingArray(),
            "m_LeaseCloseRpcKinds": PendingArray(),
        }
        for sig in (
            "protected void RegisterLeaseCloseRpc(int requestId, string kind)",
            "protected void LiquidateLeaseCloseRpc(int requestId, string kind)",
        ):
            translate(_wrap(sig, method_body(source, sig)), sig, ns,
                      members=("m_LeaseCloseRpcIds", "m_LeaseCloseRpcKinds"))
        return ns

    def _rpc_census_for(self, source, ns):
        kinds = tuple(k.text for k in ns["m_LeaseCloseRpcKinds"])
        census = self._server_census(source, rpc_kinds=kinds)
        return census, self._server_ns

    def _run_hands_tail(self, source, kind, *, sent, mutant=None):
        if kind == "hands_take":
            dispatch_sig = "protected bool DispatchHandsTake(MCPCommand command, MCPResult result)"
            tail_sig = "bool HandsTakeTail()"
        else:
            dispatch_sig = "protected bool DispatchHandsAttach(MCPCommand command, MCPResult result)"
            tail_sig = "bool HandsAttachTail()"
        body = method_body(source, dispatch_sig)
        register = f'RegisterLeaseCloseRpc(command.id, "{kind}");'
        start = body.find(register)
        self.assertGreaterEqual(start, 0, register)
        fragment = body[start:]
        liquidate = f'LiquidateLeaseCloseRpc(command.id, "{kind}");'
        if mutant == "no_liquidate":
            self.assertIn(liquidate, fragment)
            fragment = fragment.replace(liquidate, "", 1)
        if mutant == "liquidate_after_send":
            self.assertIn("\t\tresult.ok = true;", fragment)
            fragment = fragment.replace("\t\tresult.ok = true;", "\t\t" + liquidate + "\n\t\tresult.ok = true;", 1)
        ns = self._rpc_register_ns(source)
        result = type("R", (), {})()
        result.ok = False
        result.accepted = False
        result.confirmed = False
        result.error = _LogString.of("")
        result.type = _LogString.of("Bandage")
        calls = []
        if kind == "hands_take":
            player = type("P", (), {
                "MCPRequestTakeToHands": lambda self, item, typ: calls.append(1) or sent,
            })()
            command = type("C", (), {"id": 77, "args": None})()
            ns.update({"command": command, "player": player, "item": object(), "result": result})
        else:
            target = type("T", (), {"GetType": lambda self: _LogString.of("BatteryCharger")})()
            player = type("P", (), {
                "MCPRequestAttachFromHands": lambda self, tgt, tgt_type, item, item_type, slot: calls.append(1) or sent,
            })()
            args = type("A", (), {"slot": _LogString.of("battery")})()
            command = type("C", (), {"id": 77, "args": args})()
            ns.update({"command": command, "player": player, "target": target, "held": object(), "result": result})
        translate(_wrap(tail_sig, fragment), tail_sig, ns,
                  members=("m_LeaseCloseRpcIds", "m_LeaseCloseRpcKinds"))
        returned = ns[tail_sig.split("(")[0].split()[-1]]()
        return ns, result, returned, calls

    def _run_inventory_tail(self, source, *, send_error, mutant=None):
        body = method_body(source, "protected bool DispatchInventoryMove(MCPCommand command, MCPResult result)")
        register = 'RegisterLeaseCloseRpc(job.id, "inventory_move");'
        start = body.find(register)
        self.assertGreaterEqual(start, 0, register)
        if_start = body.find('if (performError != "")', start)
        self.assertGreaterEqual(if_start, 0)
        brace = body.index("{", if_start)
        depth = 0
        end = -1
        for index in range(brace, len(body)):
            if body[index] == "{":
                depth += 1
            elif body[index] == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        self.assertGreater(end, brace)
        fragment = body[start:end + 1].replace("\n\t\t}\n", "\n", 1)
        liquidate = 'LiquidateLeaseCloseRpc(job.id, "inventory_move");'
        if mutant == "no_liquidate":
            self.assertIn(liquidate, fragment)
            fragment = fragment.replace(liquidate, "", 1)
        prelude = '\t\tbool clientRpc = true;\n\t\tstring performError = "";\n\t\tm_Jobs.Insert(job.id, job);\n'
        tail_body = prelude + fragment + "\n\t\treturn false;\n"
        if mutant == "liquidate_after_send":
            tail_body = prelude + fragment + "\n\t\t" + liquidate + "\n\t\treturn false;\n"
        ns = self._rpc_register_ns(source)
        job = type("J", (), {"id": 77})()
        result = type("R", (), {})()
        result.ok = True
        result.error = _LogString.of("")
        args = type("A", (), {"slot": _LogString.of("")})()
        command = type("C", (), {"id": 77, "args": args})()
        ns.update({
            "m_Jobs": EnforceMap(),
            "job": job,
            "player": object(),
            "item": object(),
            "target": None,
            "op": _LogString.of("combine"),
            "command": command,
            "result": result,
            "MCPSendInventoryMove": lambda p, i, t, o, s, j: _LogString.of(send_error),
        })
        tail_sig = "bool InventoryMoveTail()"
        translate(_wrap(tail_sig, tail_body), tail_sig, ns,
                  members=("m_Jobs", "m_LeaseCloseRpcIds", "m_LeaseCloseRpcKinds"))
        returned = ns["InventoryMoveTail"]()
        return ns, result, returned

    def test_h4_request_bodies_fail_only_before_send(self):
        # Every return false of the three request bodies sits before its
        # single rpc.Send, so a false return means the RPC was never sent.
        weapon = _read(SCRIPTS.parent / "4_World" / "MCP_Weapon.c")
        inventory = _read(SCRIPTS.parent / "4_World" / "MCP_InventoryMove.c")
        for src, sig in (
            (weapon, "bool MCPRequestTakeToHands(EntityAI item, string validatedType)"),
            (weapon, "bool MCPRequestAttachFromHands(EntityAI target, string targetType, EntityAI item, string itemType, string slotName)"),
            (inventory, "bool MCPRequestInventoryMove(int jobId, EntityAI item, string itemType, EntityAI target, string targetType, string op, string slotName)"),
        ):
            with self.subTest(sig=sig):
                body = method_body(src, sig)
                self.assertEqual(body.count("rpc.Send("), 1)
                send_at = body.find("rpc.Send(")
                self.assertGreater(send_at, 0)
                for match in re.finditer(r"return false", body):
                    self.assertLess(match.start(), send_at, sig)
        # MCPSendInventoryMove: its three errors all precede a Send too.
        source = _read(SERVER_PATH)
        send_body = method_body(source, "protected string MCPSendInventoryMove(PlayerBase player, EntityAI item, EntityAI target, string op, string slotName, int jobId)")
        for error in ('return "not_networked";', 'return "no_identity";'):
            self.assertLess(send_body.find(error), send_body.find("MCPRequestInventoryMove("), error)
        self.assertIn('return "rpc_failed";', send_body)
        # notify_players has no failure path after registration: its Send
        # is void, so there is nothing to liquidate (same-pattern audit).
        notify = method_body(source, "protected bool DispatchNotifyPlayers(MCPCommand command, MCPResult result)")
        self.assertLess(notify.find('RegisterLeaseCloseRpc(command.id, "notify_players");'),
                        notify.find("NotificationSystem.SendNotificationToPlayerIdentityExtended("))
        self.assertNotIn("LiquidateLeaseCloseRpc", notify)

    def test_h4_never_sent_rpc_leaves_no_row_translated(self):
        source = _read(SERVER_PATH)
        # POS inventory_move: each pre-Send error leaves no row, no job,
        # and a clean census.
        for send_error in ("not_networked", "no_identity", "rpc_failed"):
            with self.subTest(kind="inventory_move", error=send_error):
                ns, result, returned = self._run_inventory_tail(source, send_error=send_error)
                self.assertTrue(returned)
                self.assertFalse(result.ok)
                self.assertEqual(ns["m_LeaseCloseRpcKinds"].Count(), 0)
                self.assertEqual(ns["m_Jobs"].Count(), 0)
                census, _cns = self._rpc_census_for(source, ns)
                self.assertEqual(census.state.text, "clean")
        # POS hands_take / hands_attach: rpc_failed (never sent) leaves
        # no row and a clean census.
        for kind in ("hands_take", "hands_attach"):
            with self.subTest(kind=kind):
                ns, result, returned, calls = self._run_hands_tail(source, kind, sent=False)
                self.assertTrue(returned)
                self.assertFalse(result.ok)
                self.assertEqual(result.error.text, "rpc_failed")
                self.assertEqual(len(calls), 1)
                self.assertEqual(ns["m_LeaseCloseRpcKinds"].Count(), 0)
                census, _cns = self._rpc_census_for(source, ns)
                self.assertEqual(census.state.text, "clean")
        # NEG inventory_move: after a Send the row stays even when the
        # job ends (its m_Jobs entry is removed, as the job paths do).
        ns, _result, returned = self._run_inventory_tail(source, send_error="")
        self.assertFalse(returned)
        self.assertEqual(ns["m_LeaseCloseRpcKinds"].Count(), 1)
        ns["m_Jobs"].Remove(77)
        self.assertEqual(ns["m_Jobs"].Count(), 0)
        self.assertEqual(ns["m_LeaseCloseRpcKinds"].Count(), 1)
        census, cns = self._rpc_census_for(source, ns)
        self.assertEqual(census.state.text, "residue")
        self.assertFalse(cns["LeaseCloseCensusClean"](census))
        self.assertTrue(cns["LeaseCloseCensusSettled"](census))
        rpc_rows = [(e.name.text, e.state.text) for e in census.entries if e.name.text == "rpc_in_flight"]
        self.assertIn(("rpc_in_flight", "unverified"), rpc_rows)
        # NEG hands: the dispatch ends the job at once (returns true),
        # and the row still stays as unverified debt.
        for kind in ("hands_take", "hands_attach"):
            with self.subTest(kind=kind, sent=True):
                ns, result, returned, calls = self._run_hands_tail(source, kind, sent=True)
                self.assertTrue(returned)
                self.assertTrue(result.ok)
                self.assertEqual(len(calls), 1)
                self.assertEqual(ns["m_LeaseCloseRpcKinds"].Count(), 1)
                self.assertEqual(ns["m_LeaseCloseRpcKinds"].Get(0).text, kind)
                census, cns = self._rpc_census_for(source, ns)
                self.assertEqual(census.state.text, "residue")
                self.assertTrue(cns["LeaseCloseCensusSettled"](census))
        # Mutant (in a copy): never liquidating keeps the POS row.
        ns_m, _r, _ret = self._run_inventory_tail(source, send_error="rpc_failed", mutant="no_liquidate")
        self.assertEqual(ns_m["m_LeaseCloseRpcKinds"].Count(), 1)
        for kind in ("hands_take", "hands_attach"):
            ns_m, _r, _ret, _c = self._run_hands_tail(source, kind, sent=False, mutant="no_liquidate")
            self.assertEqual(ns_m["m_LeaseCloseRpcKinds"].Count(), 1, kind)
        # Mutant (in a copy): liquidating also after a Send drops the NEG row.
        ns_m, _r, _ret = self._run_inventory_tail(source, send_error="", mutant="liquidate_after_send")
        self.assertEqual(ns_m["m_LeaseCloseRpcKinds"].Count(), 0)
        for kind in ("hands_take", "hands_attach"):
            ns_m, _r, _ret, _c = self._run_hands_tail(source, kind, sent=True, mutant="liquidate_after_send")
            self.assertEqual(ns_m["m_LeaseCloseRpcKinds"].Count(), 0, kind)

    # ---- H6: K27/K28 translated bodies (server) ----
    def _server_census(self, source, *, flags=None, jobs=(), rpc_kinds=(), deletion=(), infected=False):
        flags = flags or {}
        state = {"bots": bool(flags.get("bots")), "move": bool(flags.get("move")),
                 "weapon": bool(flags.get("weapon")), "hold": bool(flags.get("hold")),
                 "bots_reason": flags.get("bots_reason", "bot_stop_rejected")}

        class _Job:
            def __init__(self, kind=""):
                self.kind = kind

        class _Mgr:
            def MCP_HoldServerBusy(self):
                return state["hold"]

        class _Player:
            def GetNetworkID(self, low, high):
                return (11, 22)

            def MCPConnGenValue(self):
                return 7

            def MCPMoveServerFloor(self):
                return 5

            def MCPWeaponServerFloor(self):
                return 9

            def MCPMoveActiveNow(self):
                return state["move"]

            def MCPWeaponPendingNow(self):
                return state["weapon"]

            def GetActionManager(self):
                return _Mgr()

        player = _Player()

        class _Game:
            def GetPlayers(self, arr):
                arr.Insert(player)

        jobs_map = EnforceMap()
        for i, kind in enumerate(jobs):
            job = _Job()
            job.kind = EnforceString.of(kind) if isinstance(kind, str) else kind
            jobs_map.Insert(100 + i, job)
        ns = {
            "GetGame": lambda: _Game(),
            "PlayerBase": type("PB", (), {"Cast": staticmethod(lambda x: x)}),
            "ActionManagerServer": type("AMS", (), {"Cast": staticmethod(lambda x: x)}),
            "MCPBotControl": type("BC", (), {
                "LeaseCloseBusy": staticmethod(lambda b: state["bots"]),
                "LeaseCloseResidueReason": staticmethod(lambda b: _LogString.of(state["bots_reason"])),
            }),
            "MCPSurvivorDrive": type("SD", (), {
                "LeaseCloseBusy": staticmethod(lambda: bool(flags.get("survivor_drive"))),
                "ShutdownAll": staticmethod(lambda b: None),
            }),
            "MCPJob": _Job,
            "EntityAI": object,
            "MCPSceneEntry": type("SE", (), {}),
            "MCPLeaseCloseCensus": _CloseCensus,
            "MCPLeaseCloseEntry": _CloseEntry,
            "MCPLeaseCloseFloor": _CloseFloor,
            "this": object(),
            "m_Players": EnforceArray(),
            "m_Jobs": jobs_map,
            "m_LeaseCloseRpcKinds": EnforceArray(_LogString.of(k) for k in rpc_kinds),
            "m_LeaseCloseRpcIds": EnforceArray(),
            "m_InfectedDebt": infected,
            "m_LeaseCloseDeletion": EnforceArray(deletion),
        }
        self._server_ns = ns
        self._server_state = state
        for sig in (
            "protected void AddCloseEntry(MCPLeaseCloseCensus census, string name, bool active, string reason)",
            "protected void AddCloseDebt(MCPLeaseCloseCensus census, string name, bool active, string reason)",
            "protected bool LeaseCloseCensusClean(MCPLeaseCloseCensus census)",
            "protected bool LeaseCloseCensusSettled(MCPLeaseCloseCensus census)",
            "protected bool DeletionPendingActive()",
            "protected void RegisterDeletionRefs(MCPJob job)",
            "protected void LeaseCloseExtensionCensus(MCPLeaseCloseCensus census)",
            "protected MCPLeaseCloseCensus BuildLeaseCloseCensus(MCPArgs closeArgs)",
        ):
            body = method_body(source, sig)
            body = body.replace("MCPJob other = m_Jobs.GetElement(j);", "MCPJob other;\n\t\t\tother = m_Jobs.GetElement(j);")
            translate(_wrap(sig, body), sig, ns,
                      class_types=("MCPLeaseCloseCensus", "MCPLeaseCloseEntry", "MCPLeaseCloseFloor", "PlayerBase", "ActionManagerServer", "MCPJob", "EntityAI", "MCPSceneEntry"),
                      members=("m_Players", "m_Jobs", "m_LeaseCloseRpcKinds", "m_LeaseCloseRpcIds", "m_InfectedDebt", "m_LeaseCloseDeletion"))
        args = type("Args", (), {"close_generation": "g1", "close_lease_id": "l1"})()
        return ns["BuildLeaseCloseCensus"](args)

    def test_k27_server_census_translated(self):
        source = _read(SERVER_PATH)
        census = self._server_census(source)
        self.assertEqual(census.state.text, "clean")
        self.assertEqual(len(census.floors), 1)
        floor = census.floors.Get(0)
        self.assertEqual((floor.conn_gen, floor.move_id, floor.weapon_id), (7, 5, 9))
        # NEG rows, one at a time: each names exactly its entry (kills M6).
        cases = [
            ({"infected": True}, "infected_drive", "unverified"),
            ({"rpc_kinds": ("inventory_move",)}, "rpc_in_flight", "unverified"),
            ({"jobs": ("spawn",)}, "jobs", "residue"),
            ({"flags": {"move": True}}, "move_copy", "residue"),
            ({"flags": {"weapon": True}}, "weapon", "residue"),
            ({"flags": {"bots": True}}, "bots", "residue"),
            ({"flags": {"survivor_drive": True}}, "survivor_drive", "residue"),
            ({"deletion": (object(),)}, "deletion_pending", "residue"),
        ]
        for kwargs, name, want_state in cases:
            with self.subTest(entry=name):
                c = self._server_census(source, **kwargs)
                bad = [(e.name.text, e.state.text) for e in c.entries if e.state.text != "clean"]
                self.assertIn((name, want_state), bad)
                self.assertEqual(c.state.text, "residue")
        # Infected-only: settled at once, no 25 s observer (H3, kills M6/M2 class).
        c_inf = self._server_census(source, infected=True)
        self.assertTrue(self._server_ns["LeaseCloseCensusSettled"](c_inf))
        self.assertFalse(self._server_ns["LeaseCloseCensusClean"](c_inf))
        # D-r2-H5/N9: an unreadable bot FSM is unverified debt, not residue:
        # it settles the observer instead of holding it for 25 s.
        c_bots = self._server_census(source, flags={"bots": True, "bots_reason": "bot_state_unverified"})
        bad_bots = [(e.name.text, e.state.text) for e in c_bots.entries if e.state.text != "clean"]
        self.assertIn(("bots", "unverified"), bad_bots)
        self.assertTrue(self._server_ns["LeaseCloseCensusSettled"](c_bots))
        self.assertFalse(self._server_ns["LeaseCloseCensusClean"](c_bots))
        # RegisterDeletionRefs actually moves refs (kills M3).
        ns = self._server_ns
        job = type("J", (), {"loadout_cleared": EnforceArray([object()]), "scene_pending": EnforceArray()})()
        before = ns["m_LeaseCloseDeletion"].Count()
        ns["RegisterDeletionRefs"](job)
        self.assertEqual(ns["m_LeaseCloseDeletion"].Count(), before + 1)
        self.assertTrue(ns["DeletionPendingActive"]())

    def test_k28_observer_timeout_translated(self):
        # PostLeaseCloseJob with a stuck residue entry publishes observer_timeout.
        # D-r3-H1: an unverified-only census is Settled, so the timeout adds
        # nothing (kills N13/N14: Settled -> Clean in the timeout guard).
        for path, peer in ((SERVER_PATH, "server"), (CLIENT_PATH, "client")):
          for entry_state, want_timeout in (("residue", True), ("unverified", False)):
            with self.subTest(peer=peer, entry=entry_state):
                source = _read(path)
                posted = []
                ns = {"PostResult": lambda r: posted.append(r), "MCPLeaseCloseEntry": _CloseEntry, "MCPLeaseCloseCensus": _CloseCensus}

                class _Res:
                    pass

                def build(args, _s=entry_state):
                    c = _CloseCensus()
                    c.state = EnforceString.of("residue")
                    e = _CloseEntry()
                    e.name = EnforceString.of("weapon")
                    e.state = EnforceString.of(_s)
                    e.reason = EnforceString.of("weapon")
                    c.entries.Insert(e)
                    return c

                ns["BuildLeaseCloseCensus"] = build
                ns["MCPResult"] = _Res
                sig = "protected void PostLeaseCloseJob(MCPJob job, bool timedOut)"
                body = method_body(source, sig)
                # Census helpers are the real ones where present; AddCloseEntry real.
                for helper in (
                    "protected void AddCloseEntry(MCPLeaseCloseCensus census, string name, bool active, string reason)",
                    "protected bool LeaseCloseCensusClean(MCPLeaseCloseCensus census)",
                    "protected bool LeaseCloseCensusSettled(MCPLeaseCloseCensus census)",
                ):
                    try:
                        hb = method_body(source, helper)
                    except Exception:
                        continue
                    translate(_wrap(helper, hb), helper, ns, class_types=("MCPLeaseCloseCensus", "MCPLeaseCloseEntry"))
                translate(_wrap(sig, body), sig, ns, class_types=("MCPLeaseCloseCensus", "MCPLeaseCloseEntry", "MCPResult"))
                job = type("J", (), {"id": 42, "args": None, "tick_poll_sent": 1, "tick_poll_callback": 2, "tick_dispatch": 3})()
                ns["PostLeaseCloseJob"](job, True)
                self.assertEqual(len(posted), 1)
                names = [(e.name.text, e.reason.text) for e in posted[0].lease_close.entries]
                # D-r2-H7: settled (unverified-only) debt is not an observer timeout.
                self.assertEqual(("observer", "observer_timeout") in names, want_timeout)
                self.assertEqual(posted[0].lease_close.state.text, "residue")

    def test_bots_gone_body_is_idle_translated(self):
        bots = _read(SCRIPTS / "MCP_BotControl.c")
        sig = "static string LeaseCloseBotState(MCPBridge bridge, int objectId)"
        body = "\n".join(l for l in method_body(bots, sig).splitlines() if not l.strip().startswith("#"))
        bridge = type("B", (), {"RuntimeObjectById": staticmethod(lambda oid: None)})()
        ns = {"PlayerBase": type("PB", (), {"Cast": staticmethod(lambda x: x)}), "Object": object}
        translate(_wrap(sig, body), sig, ns, class_types=("PlayerBase", "Object"))
        self.assertEqual(ns["LeaseCloseBotState"](bridge, 99).text, "idle")
        self.assertNotIn("GetFSM", bots)
        self.assertIn("s_Initialized.Remove(goneIndex);", bots)

    def test_bot_fsm_state_translated(self):
        # D-r2-H5/N7: idle is identity with m_IdleState, not mere non-null.
        src = _read(SCRIPTS.parent / "4_World" / "MCP_BotState.c")
        sig = "int MCP_LeaseCloseState()"
        idle = object()
        busy = object()

        class _FSM:
            def __init__(self, current):
                self._current = current

            def GetCurrentState(self):
                return self._current

        for fsm, want in ((_FSM(idle), 1), (_FSM(busy), 0), (_FSM(None), -1), (None, -1)):
            with self.subTest(want=want):
                ns = {"m_ActiveFSM": fsm, "m_IdleState": idle}
                translate(_wrap(sig, method_body(src, sig)), sig, ns,
                          class_types=("BotStateBase",),
                          members=("m_ActiveFSM", "m_IdleState"))
                self.assertEqual(ns["MCP_LeaseCloseState"](), want)



    # ---- H6: K8/K17 bridge-side close bodies + conn-gen guard ----
    def test_move_weapon_close_server_translated(self):
        from types import SimpleNamespace as _NS
        move_src = _read(SCRIPTS.parent / "4_World" / "MCP_PlayerMove.c")
        weapon_src = _read(SCRIPTS.parent / "4_World" / "MCP_Weapon.c")
        # MCPMoveCloseServer (kills M5: ignoring conn_gen would clear here)
        released = []
        ns = {
            "MCPMoveServerRelease": lambda why: released.append(why),
            "m_MCPMoveLastId": 0,
            "m_MCPMoveActive": _NS(move_id=3),
            "m_MCPConnGen": 7,
        }
        sig = "bool MCPMoveCloseServer(int floorId, int connGen)"
        translate(_wrap(sig, method_body(move_src, sig)), sig, ns,
                  members=("m_MCPMoveLastId", "m_MCPMoveActive", "m_MCPConnGen"))
        self.assertFalse(ns["MCPMoveCloseServer"](10, 0))
        self.assertEqual(ns["m_MCPMoveLastId"], 0)
        self.assertFalse(ns["MCPMoveCloseServer"](10, 99))
        self.assertEqual(ns["m_MCPMoveLastId"], 0)
        self.assertTrue(ns["MCPMoveCloseServer"](10, 7))
        self.assertEqual(ns["m_MCPMoveLastId"], 10)
        self.assertEqual(len(released), 1)
        # copy above floor stays
        ns["m_MCPMoveActive"] = _NS(move_id=50)
        released.clear()
        self.assertTrue(ns["MCPMoveCloseServer"](10, 7))
        self.assertEqual(released, [])
        # MCPWeaponCloseServer (kills M4 + H5: armed raise released via override-off)
        overrides = []
        wns = {
            "MCPConnGenValue": lambda: 7,
            "MCPServerReleaseRaise": lambda: (overrides.append("release"), wns.__setitem__("m_MCPRaiseArmed", False), wns.__setitem__("m_MCPRaiseWeapon", None)),
            "m_MCPWeaponLastId": 3,
            "m_MCPFireRequested": True,
            "m_MCPFireWeapon": object(),
            "m_MCPRaiseRequested": True,
            "m_MCPRaiseArmed": True,
            "m_MCPRaiseWeapon": object(),
        }
        wsig = "bool MCPWeaponCloseServer(int floorId, int connGen)"
        translate(_wrap(wsig, method_body(weapon_src, wsig)), wsig, wns,
                  members=("m_MCPWeaponLastId", "m_MCPFireRequested", "m_MCPFireWeapon", "m_MCPRaiseRequested", "m_MCPRaiseArmed", "m_MCPRaiseWeapon"))
        self.assertFalse(wns["MCPWeaponCloseServer"](10, 99))
        self.assertTrue(wns["m_MCPFireRequested"])
        self.assertTrue(wns["MCPWeaponCloseServer"](10, 7))
        self.assertEqual(wns["m_MCPWeaponLastId"], 10)
        self.assertFalse(wns["m_MCPFireRequested"])
        self.assertEqual(overrides, ["release"])
        self.assertFalse(wns["m_MCPRaiseArmed"])
        # H4 pin: reconnect resets the weapon floor (source-level, same class)
        wbody = method_body(weapon_src, "override void OnReconnect()")
        self.assertIn("m_MCPWeaponLastId = 0;", wbody)
        self.assertIn("super.OnReconnect();", wbody)

    def test_conn_gen_onrpc_side_guard_translated(self):
        from types import SimpleNamespace as _NS
        move_src = _read(SCRIPTS.parent / "4_World" / "MCP_PlayerMove.c")
        sig = "override void OnRPC(PlayerIdentity sender, int rpc_type, ParamsReadContext ctx)"

        class _Ctx:
            def __init__(self):
                self.attempted = []

            def Read(self, value):
                self.attempted.append(value)
                return False

        # Server/dedicated must not even attempt the read (H9); only the
        # owning client reaches it. The translator cannot propagate an
        # `out` int from a method double, so the attempt is the evidence.
        for is_client, dedicated, want_attempt in ((False, False, False), (True, True, False), (True, False, True)):
            with self.subTest(is_client=is_client, dedicated=dedicated):
                game = _NS(IsClient=lambda: is_client, IsDedicatedServer=lambda: dedicated)
                ns = {
                    "GetGame": lambda: game,
                    "super": _NS(OnRPC=lambda *a: None),
                    "MCP_RPC_CONN_GEN": 78541073,
                    "m_MCPConnGen": 0,
                }
                body = method_body(move_src, sig)
                translate(_wrap(sig, body), sig, ns, members=("m_MCPConnGen",))
                ctx = _Ctx()
                ns["OnRPC"](None, 78541073, ctx)
                self.assertEqual(bool(ctx.attempted), want_attempt)
                self.assertEqual(ns["m_MCPConnGen"], 0)


class LeaseCloseDeliverable5Tests(unittest.TestCase):
    """Deliverable 5: RPC refuse, bot FSM, infected registry, deletion at
    dispatch, and action-input debt (plan §2.7 steps 1-6, §2.9)."""

    def test_inventory_refuse_liquidates_only_after_validation(self):
        source = _read(SERVER_PATH)
        body = method_body(source, "void MCPOnInventoryMoveRefused(PlayerIdentity sender, Object target, int rpc_type, ParamsReadContext ctx)")
        # POS: liquidation sits after the sender/actor validation.
        self.assertLess(body.find("sender.GetId() != job.actor.GetIdentity().GetId()"),
                        body.find('LiquidateLeaseCloseRpc(jobId, "inventory_move")'))
        # NEG: the job-ready readback never liquidates a register row.
        ready = method_body(source, "protected bool MCPInventoryMoveReady(MCPJob job)")
        self.assertNotIn("LiquidateLeaseCloseRpc", ready)
        liquidate = method_body(source, "protected void LiquidateLeaseCloseRpc(int requestId, string kind)")
        self.assertIn("m_LeaseCloseRpcIds.Remove(index);", liquidate)
        # Client side: a refusal after an emit stays silent (§2.7 step 5).
        client = _read(SCRIPTS.parent / "4_World" / "MCP_InventoryMove.c")
        self.assertIn('MCPDropInventoryMove("refuse_after_emit");', client)
        self.assertIn("m_MCPInventoryMoveEmitted = false;", client)

    def test_bots_fsm_state_and_infected_registry(self):
        bots = _read(SCRIPTS / "MCP_BotControl.c")
        self.assertIn("static string LeaseCloseBotState(MCPBridge bridge, int objectId)", bots)
        self.assertIn('return "idle";', bots)
        self.assertIn("static bool LeaseCloseBusy(MCPBridge bridge)", bots)
        server = _read(SERVER_PATH)
        census = method_body(server, "protected MCPLeaseCloseCensus BuildLeaseCloseCensus(MCPArgs closeArgs)")
        self.assertIn("MCPBotControl.LeaseCloseBusy(this)", census)
        # Mutant: an emptied registry as the bot proof fails this pin.
        self.assertNotIn("AnyRunning() ||", census)
        # Infected: recorded per specimen, released at close, never cleared.
        self.assertIn("protected ref array<DayZInfected> m_InfectedOverridden;", server)
        drive = method_body(server, "protected bool DispatchInfectedDrive(MCPCommand command, MCPResult result)")
        self.assertIn("m_InfectedOverridden.Insert(infected);", drive)
        self.assertIn("m_InfectedOverridden.RemoveItem(infected);", drive)
        close = method_body(server, "protected bool DispatchLeaseClose(MCPCommand command, MCPResult result)")
        self.assertIn("infController.OverrideMovementSpeed(false, 0.0);", close)
        self.assertIn("infController.OverrideTurnSpeed(false, 0.0);", close)
        self.assertNotIn("m_InfectedOverridden.Clear()", close)
        self.assertNotIn("m_InfectedDebt = false;", close)

    def test_deletion_refs_at_dispatch_and_action_input_debt(self):
        server = _read(SERVER_PATH)
        scene = method_body(server, "protected bool DispatchSceneClear(MCPCommand command, MCPResult result)")
        insert_at = scene.find("m_Jobs.Insert(job.id, job);")
        self.assertLess(insert_at, scene.find("RegisterDeletionRefs(job);", insert_at))
        loadout = method_body(server, "protected bool DispatchLoadoutClear(MCPCommand command, MCPResult result)")
        insert_at = loadout.find("m_Jobs.Insert(job.id, job);")
        self.assertLess(insert_at, loadout.find("RegisterDeletionRefs(job);", insert_at))
        timeout = method_body(server, "protected void PostJobTimeout(MCPJob job)")
        self.assertGreaterEqual(timeout.count("RegisterDeletionRefs(job);"), 2)
        client = _read(CLIENT_PATH)
        census = method_body(client, "protected MCPLeaseCloseCensus BuildLeaseCloseCensus(MCPArgs closeArgs)")
        self.assertIn('"action_input_in_flight"', census)
        hold = _read(SCRIPTS.parent / "4_World" / "MCP_ActionHold.c")
        self.assertIn("static bool InputInFlight()", hold)
        self.assertIn("s_InterruptSent", method_body(hold, "static bool InputInFlight()"))
        # D-r2-H5/N11: both interrupt sends mark the process-lifetime debt.
        self.assertEqual(hold.count("s_InterruptDebt = true;"), 2)
        self.assertNotIn("s_InterruptDebt = false", hold)


if __name__ == "__main__":
    unittest.main()
