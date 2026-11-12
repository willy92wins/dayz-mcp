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
        self.look_id = 0


class _CloseCensus:
    def __init__(self):
        self.generation = ""
        self.lease_id = ""
        self.peer = ""
        self.state = ""
        self.entries = EnforceArray()
        self.move_floor = 0
        self.weapon_floor = 0
        self.look_floor = 0
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

    def _client_census(self, source, *, active=(), conn_gen=7, mutant_clean=False, emote_mcp=False, emote_playing=False, mutant_emote=None, emote_pending=False, emote_queued=False, emote_no_mgr=False, look_release_pending=False, look_generation=3, look_floor=None, pause_queued_mark=None, pause_menu_mark=None, pause_queued=None, open_pause_menu=None):
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
        # §2.6 C14: "ui_pause" in active is MCP's queued pause still queued.
        if pause_queued_mark is None:
            pause_queued_mark = "ui_pause" in active
        if pause_queued is None:
            pause_queued = "ui_pause" in active
        mission = type("MG", (), {
            "MCP_IsPauseQueued": lambda self: pause_queued,
        })()
        # The MENU_INGAME instance open now (FindMenu), MCP's or not.
        ui = type("UI", (), {"FindMenu": lambda self, menu_id: open_pause_menu if menu_id == 2 else None})()
        game = type("G", (), {"GetPlayer": lambda self: player, "GetMission": lambda self: mission, "GetUIManager": lambda self: ui})()
        runner = type("R", (), {
            "CountExcluding": lambda self, kind: 1 if "jobs" in active else 0,
            "GetElapsedS": lambda self: 0.0,
        })()
        ns = {
            "GetGame": lambda: game,
            "PlayerBase": type("PB", (), {"Cast": staticmethod(lambda x: x)}),
            "ActionManagerClient": type("AMC", (), {"Cast": staticmethod(lambda x: x)}),
            "MissionGameplay": type("MGC", (), {"Cast": staticmethod(lambda x: x)}),
            "MCPCarDrive": type("C", (), {"s_Active": flags["vehicle_drive"], "Clear": staticmethod(lambda: None)}),
            "MCPInputTriggerControl": type("I", (), {"IsHeld": staticmethod(lambda: flags["input_trigger"]), "ReleaseAll": staticmethod(lambda cause: None)}),
            "MCPPlayerMoveControl": type("M", (), {
                "IsActive": staticmethod(lambda: flags["player_move"]),
                "ServerReleasePending": staticmethod(lambda: False),
                "Generation": staticmethod(lambda: 7),
                "ReleaseAll": staticmethod(lambda cause: None)}),
            "MCPWeaponControl": type("W", (), {"IsBusy": staticmethod(lambda: flags["weapon"]), "CloseRequestFloor": staticmethod(lambda: 4)}),
            "MCPLookAtControl": type("L", (), {
                "IsActive": staticmethod(lambda: flags["look_at"]),
                "ServerReleasePending": staticmethod(lambda: look_release_pending),
                "Generation": staticmethod(lambda: look_generation),
                "CloseFloor": staticmethod(lambda: look_generation if look_floor is None else look_floor),
            }),
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
            "m_MCPPauseQueued": pause_queued_mark,
            "m_MCPPauseMenu": pause_menu_mark,
            "MENU_INGAME": 2,
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
            translate(_wrap(sig, body), sig, ns, class_types=("MCPLeaseCloseCensus", "MCPLeaseCloseEntry", "PlayerBase", "ActionManagerClient", "EmoteManager", "MissionGameplay", "UIManager"),
                      members=("m_CameraPath", "m_ActiveCam", "m_CameraHandoffPending", "m_PlayerSimulationDisabled",
                               "m_ControlsSuppressed", "m_Dialog", "m_UiPreviewRoot", "m_JobRunner", "m_MCPEmoteStarted",
                               "m_MCPPauseQueued", "m_MCPPauseMenu"))
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
        # §2.6 C14 NEG: MCP's queued pause still queued names exactly ui_pause,
        # and so does the pause menu MCP opened while it is still open.
        census_pause = self._client_census(source, active=("ui_pause",))
        self.assertEqual([e.name.text for e in census_pause.entries if e.state.text != "clean"], ["ui_pause"])
        self.assertEqual(census_pause.state.text, "residue")
        self.assertFalse(self._last_client_ns["LeaseCloseCensusSettled"](census_pause))
        mcp_menu = object()
        census_menu = self._client_census(source, pause_menu_mark=mcp_menu, open_pause_menu=mcp_menu)
        self.assertEqual([e.name.text for e in census_menu.entries if e.state.text != "clean"], ["ui_pause"])
        # A pause MCP neither queued nor opened is not counted, nor is a pause
        # menu the person opened after MCP's closed (PX6 a: another instance).
        census_foreign = self._client_census(source, pause_queued_mark=False, pause_queued=True, open_pause_menu=object())
        self.assertEqual(census_foreign.state.text, "clean")
        census_reopened = self._client_census(source, pause_menu_mark=mcp_menu, open_pause_menu=object())
        self.assertEqual(census_reopened.state.text, "clean")
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
                 'MCPVehicleTrace.Abort("shutdown")', "StopLeaseCloseEmote()", "AbortAll(this)", "StopLeaseClosePause()",
                 "BuildLeaseCloseCensus(command.args)"]
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
                     "MCPWeaponCloseServer(carried.weapon_id, carried.conn_gen)", "MCPLookCloseServer(carried.look_id, carried.conn_gen)", "PostJobTimeout(job)",
                     "LeaseCloseExtensionStop()", "BuildLeaseCloseCensus(command.args)"):
            self.assertIn(step, body, step)
        self.assertIn('job.kind != "server_perf"', body, "server_perf is kept")
        self.assertIn("RegisterDeletionRefs(job)", body, "deletion refs transfer before the job drops")
        self.assertIn('closeJob.kind = "lease_close";', body)
        self.assertIn("m_ElapsedS + 25.0", body)
        census = method_body(source, "protected MCPLeaseCloseCensus BuildLeaseCloseCensus(MCPArgs closeArgs)")
        for name in ("bots", "survivor_drive", "infected_drive", "move_copy", "look_copy", "weapon", "jobs", "rpc_in_flight", "deletion_pending"):
            self.assertIn(f'"{name}"', census, name)
        self.assertIn("LeaseCloseExtensionCensus(census);", census)
        # H7: registration sits at the real send, not in Dispatch.
        dispatch = method_body(source, "protected void Dispatch(MCPCommand command)")
        self.assertNotIn("RegisterLeaseCloseRpc", dispatch)
        self.assertIn('RegisterLeaseCloseRpcWithActor(job.id, "inventory_move", player);', method_body(source, "protected bool DispatchInventoryMove(MCPCommand command, MCPResult result)"))
        self.assertIn('RegisterLeaseCloseRpc(command.id, "hands_take");', method_body(source, "protected bool DispatchHandsTake(MCPCommand command, MCPResult result)"))
        self.assertIn('RegisterLeaseCloseRpc(command.id, "hands_attach");', method_body(source, "protected bool DispatchHandsAttach(MCPCommand command, MCPResult result)"))
        self.assertIn('RegisterLeaseCloseRpc(command.id, "notify_players");', method_body(source, "protected bool DispatchNotifyPlayers(MCPCommand command, MCPResult result)"))
        # NEG pins: the RPC register never deletes a row on a job-ready readback.
        register = method_body(source, "protected void RegisterLeaseCloseRpc(int requestId, string kind)")
        self.assertNotIn("Remove", register)
        register_actor = method_body(source, "protected void RegisterLeaseCloseRpcWithActor(int requestId, string kind, Human actor)")
        self.assertNotIn("Remove", register_actor)
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
            "m_LeaseCloseRpcActors": PendingArray(),
            "m_LeaseCloseRpcSenderIds": PendingArray(),
        }
        rpc_members = (
            "m_LeaseCloseRpcIds",
            "m_LeaseCloseRpcKinds",
            "m_LeaseCloseRpcActors",
            "m_LeaseCloseRpcSenderIds",
        )
        with_actor_sig = "protected void RegisterLeaseCloseRpcWithActor(int requestId, string kind, Human actor)"
        if with_actor_sig in source:
            translate(_wrap(with_actor_sig, method_body(source, with_actor_sig)), with_actor_sig, ns,
                      members=rpc_members)
        for sig in (
            "protected void RegisterLeaseCloseRpc(int requestId, string kind)",
            "protected void LiquidateLeaseCloseRpc(int requestId, string kind)",
        ):
            translate(_wrap(sig, method_body(source, sig)), sig, ns,
                      members=rpc_members)
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
                  members=("m_LeaseCloseRpcIds", "m_LeaseCloseRpcKinds", "m_LeaseCloseRpcActors", "m_LeaseCloseRpcSenderIds"))
        returned = ns[tail_sig.split("(")[0].split()[-1]]()
        return ns, result, returned, calls

    def _run_inventory_tail(self, source, *, send_error, mutant=None):
        body = method_body(source, "protected bool DispatchInventoryMove(MCPCommand command, MCPResult result)")
        register = 'RegisterLeaseCloseRpcWithActor(job.id, "inventory_move", player);'
        if register not in body:
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
            "player": type("P", (), {"GetIdentity": lambda self: None})(),
            "item": object(),
            "target": None,
            "op": _LogString.of("combine"),
            "command": command,
            "result": result,
            "MCPSendInventoryMove": lambda p, i, t, o, s, j: _LogString.of(send_error),
        })
        tail_sig = "bool InventoryMoveTail()"
        translate(_wrap(tail_sig, tail_body), tail_sig, ns,
                  members=("m_Jobs", "m_LeaseCloseRpcIds", "m_LeaseCloseRpcKinds", "m_LeaseCloseRpcActors", "m_LeaseCloseRpcSenderIds"))
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
    def _server_census(self, source, *, flags=None, jobs=(), rpc_kinds=(), deletion=(), infected=False, look_floor=3):
        flags = flags or {}
        state = {"bots": bool(flags.get("bots")), "move": bool(flags.get("move")),
                 "look": bool(flags.get("look")),
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

            def MCPLookServerFloor(self):
                return look_floor

            def MCPMoveActiveNow(self):
                return state["move"]

            def MCPWeaponPendingNow(self):
                return state["weapon"]

            def MCPLookActiveNow(self):
                return state["look"]

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

    def test_look_close_server_and_stale_start_translated(self):
        from types import SimpleNamespace as _NS
        look_src = _read(SCRIPTS.parent / "4_World" / "MCP_LookAt.c")
        released = []
        verdicts = []
        clock = {"now_s": 10.0}

        def _release(why):
            text = why.text if isinstance(why, EnforceString) else str(why)
            if ns["m_MCPLookId"] > 0:
                released.append((ns["m_MCPLookId"], text))
                ns["m_MCPLookId"] = 0

        def _verdict(accepted, look_id, reason):
            text = reason.text if isinstance(reason, EnforceString) else str(reason)
            verdicts.append((accepted, look_id, text))

        ns = {
            "GetInstanceType": lambda: 1,
            "DayZPlayerInstanceType": _NS(INSTANCETYPE_SERVER=1),
            "GetGame": lambda: _NS(GetTickTime=lambda: clock["now_s"]),
            "MCPLookAtControl": _NS(
                ServerAllowed=lambda: True,
                SERVER_MIN_START_INTERVAL_S=0.1,
                SERVER_DEADMAN_MARGIN_S=0.5,
                ServerRequestRefusal=lambda t, b: EnforceString.of(""),
                TurnRefusal=lambda ts: EnforceString.of(""),
            ),
            "MCPPlayerMoveControl": _NS(ActorRefusal=lambda who: EnforceString.of("")),
            "MCPLookOwnerRefusal": lambda: EnforceString.of(""),
            "MCPLookServerRelease": _release,
            "MCPLookVerdict": _verdict,
            "this": object(),
            "m_MCPConnGen": 7,
            "m_MCPLookLastId": 0,
            "m_MCPLookLastStartS": -1.0,
            "m_MCPLookId": 4,
            "m_MCPLookTarget": (10.0, 0.0, 0.0),
            "m_MCPLookTurn": 1,
            "m_MCPLookDeadlineS": 0.0,
            "m_MCPLookPulsed": False,
            "m_MCPLookConsumed": 0,
            "m_MCPLookHandled": 0,
        }
        members = (
            "m_MCPConnGen",
            "m_MCPLookLastId",
            "m_MCPLookLastStartS",
            "m_MCPLookId",
            "m_MCPLookTarget",
            "m_MCPLookTurn",
            "m_MCPLookDeadlineS",
            "m_MCPLookPulsed",
            "m_MCPLookConsumed",
            "m_MCPLookHandled",
        )
        for sig in (
            "int MCPLookServerFloor()",
            "bool MCPLookActiveNow()",
            "bool MCPLookCloseServer(int floorId, int connGen)",
            "protected string MCPLookFreshnessRefusal(int lookId)",
            "protected void MCPLookAccept(int lookId, vector target, float budgetS, int turnSign)",
        ):
            translate(_wrap(sig, method_body(look_src, sig)), sig, ns, members=members)

        # NEG: conn_gen 0 or mismatched returns false and leaves floor and copy untouched.
        self.assertFalse(ns["MCPLookCloseServer"](10, 0))
        self.assertEqual(ns["MCPLookServerFloor"](), 0)
        self.assertTrue(ns["MCPLookActiveNow"]())
        self.assertEqual(released, [])
        self.assertFalse(ns["MCPLookCloseServer"](10, 99))
        self.assertEqual(ns["MCPLookServerFloor"](), 0)
        self.assertTrue(ns["MCPLookActiveNow"]())
        self.assertEqual(released, [])

        # POS: matching conn_gen raises lastId and releases a copy <= floorId.
        self.assertTrue(ns["MCPLookCloseServer"](10, 7))
        self.assertEqual(ns["MCPLookServerFloor"](), 10)
        self.assertFalse(ns["MCPLookActiveNow"]())
        self.assertEqual(released, [(4, "lease_close")])

        # NEG: a copy above the floor (B's new look) stays alive and lastId does not drop.
        released.clear()
        ns["m_MCPLookId"] = 15
        ns["m_MCPLookLastId"] = 15
        self.assertTrue(ns["MCPLookCloseServer"](10, 7))
        self.assertEqual(ns["MCPLookServerFloor"](), 15)
        self.assertTrue(ns["MCPLookActiveNow"]())
        self.assertEqual(released, [])

        # Delayed start after close at floor F=10: id <= F gets stale_look_id, F+1 is accepted.
        ns["m_MCPLookId"] = 0
        ns["m_MCPLookLastId"] = 0
        ns["m_MCPLookLastStartS"] = -1.0
        verdicts.clear()
        self.assertTrue(ns["MCPLookCloseServer"](10, 7))
        ns["MCPLookAccept"](9, (10.0, 0.0, 0.0), 2.0, 1)
        ns["MCPLookAccept"](10, (10.0, 0.0, 0.0), 2.0, 1)
        self.assertEqual(
            verdicts,
            [(False, 9, "stale_look_id"), (False, 10, "stale_look_id")],
        )
        self.assertFalse(ns["MCPLookActiveNow"]())
        verdicts.clear()
        ns["MCPLookAccept"](11, (10.0, 0.0, 0.0), 2.0, 1)
        self.assertEqual(verdicts, [(True, 11, "")])
        self.assertTrue(ns["MCPLookActiveNow"]())
        self.assertEqual(ns["MCPLookServerFloor"](), 11)

    def test_look_close_client_and_server_census_translated(self):
        from types import SimpleNamespace as _NS
        look_src = _read(SCRIPTS.parent / "4_World" / "MCP_LookAt.c")
        client_src = _read(CLIENT_PATH)
        server_src = _read(SERVER_PATH)

        # Translate real MCPLookAtControl owner methods to drive client census.
        channel = {"can_store": True, "sent": []}

        class _Msg:
            def __init__(self):
                self._items = []

            def Write(self, val):
                self._items.append(val)

            def Send(self):
                channel["sent"].append(list(self._items))

        owner_ns = {
            "GetGame": lambda: _NS(
                IsMultiplayer=lambda: True,
                IsClient=lambda: True,
                GetPlayer=lambda: object(),
                GetTickTime=lambda: 5.0,
            ),
            "ScriptInputUserData": _NS(
                CanStoreInputUserData=lambda: channel["can_store"],
            ),
            "MCP_INPUT_UDT_PLAYER_LOOK": 78541068,
            "MAX_CONTROL_S": 3.0,
            "OWNER_MIN_START_INTERVAL_S": 0.5,
            "StartTurnSign": lambda p, t: 1,
            "Disable": lambda p: None,
            "s_Active": False,
            "s_Generation": 0,
            "s_Player": None,
            "s_Target": (0.0, 0.0, 0.0),
            "s_BudgetS": 0.0,
            "s_ElapsedS": 0.0,
            "s_Ticks": 0,
            "s_Pulses": 0,
            "s_StableTicks": 0,
            "s_HoldTicks": 0,
            "s_InitialError": 0.0,
            "s_FinalError": 0.0,
            "s_HaveInitial": False,
            "s_Finished": False,
            "s_Converged": False,
            "s_Termination": EnforceString.of(""),
            "s_Pose": EnforceString.of(""),
            "s_CommandId": 0,
            "s_TurnSign": 0,
            "s_StartSent": False,
            "s_StartSentS": 0.0,
            "s_ServerLookId": 0,
            "s_ServerReleasePending": False,
            "s_ServerReleaseId": 0,
            "s_LastSentLookId": 0,
        }

        class _UDT(_Msg):
            @staticmethod
            def CanStoreInputUserData():
                return channel["can_store"]

        owner_ns["ScriptInputUserData"] = _UDT
        owner_members = tuple(k for k in owner_ns if k.startswith("s_") or k.isupper())
        for sig in (
            "static void Reset()",
            "static int Generation()",
            "static int CloseFloor()",
            "static bool ServerReleasePending()",
            "static bool IsActive()",
            "static void FlushServerRelease()",
            "static void EndServerLook(bool converged)",
            "static string SendServerStart(int lookId, vector target, float budgetS, int turnSign)",
            "static void Release(string why)",
            "static string Begin(PlayerBase player, vector target, float budgetS, int commandId)",
        ):
            translate(
                _wrap(sig, method_body(look_src, sig)),
                sig,
                owner_ns,
                class_types=("ScriptInputUserData",),
                members=owner_members,
            )

        err = owner_ns["Begin"](object(), (10.0, 0.0, 0.0), 2.0, 41)
        self.assertEqual(err.text, "")
        self.assertEqual(owner_ns["Generation"](), 1)
        # The start actually sent carries id 1; that is the close floor.
        self.assertEqual(owner_ns["CloseFloor"](), 1)
        channel["can_store"] = False
        owner_ns["Release"](EnforceString.of("restore"))
        self.assertFalse(owner_ns["IsActive"]())
        self.assertTrue(owner_ns["ServerReleasePending"]())
        self.assertEqual(owner_ns["Generation"](), 2)
        # The restore release writes no new id: floor stays at the sent id.
        # A CloseFloor() returning Generation() would read 2 here and fail.
        self.assertEqual(owner_ns["CloseFloor"](), 1)

        # POS client census: pending server release reports look_at residue and
        # carries the last sent id as look_floor; observer stays unsettled.
        c_pending = self._client_census(
            client_src,
            look_release_pending=owner_ns["ServerReleasePending"](),
            look_generation=owner_ns["Generation"](),
            look_floor=owner_ns["CloseFloor"](),
        )
        self.assertEqual(c_pending.look_floor, 1)
        self.assertEqual(c_pending.state.text, "residue")
        self.assertEqual(
            [e.name.text for e in c_pending.entries if e.state.text != "clean"],
            ["look_at"],
        )
        self.assertFalse(self._last_client_ns["LeaseCloseCensusSettled"](c_pending))

        # NEG client census: once the pending release flushes, census is clean.
        channel["can_store"] = True
        owner_ns["FlushServerRelease"]()
        self.assertFalse(owner_ns["ServerReleasePending"]())
        c_flushed = self._client_census(
            client_src,
            look_release_pending=owner_ns["ServerReleasePending"](),
            look_generation=owner_ns["Generation"](),
            look_floor=owner_ns["CloseFloor"](),
        )
        self.assertEqual(c_flushed.look_floor, 1)
        self.assertEqual(c_flushed.state.text, "clean")

        # POS/NEG server census: live server copy reports look_copy residue and
        # floors row carries look_id; cleared copy is clean.
        s_live = self._server_census(server_src, flags={"look": True}, look_floor=6)
        self.assertEqual(s_live.state.text, "residue")
        self.assertEqual(
            [(e.name.text, e.reason.text) for e in s_live.entries if e.state.text != "clean"],
            [("look_copy", "look_copy")],
        )
        self.assertFalse(self._server_ns["LeaseCloseCensusSettled"](s_live))
        self.assertEqual(s_live.floors.Get(0).look_id, 6)

        s_clean = self._server_census(server_src, flags={"look": False}, look_floor=6)
        self.assertEqual(s_clean.state.text, "clean")
        self.assertEqual(s_clean.floors.Get(0).look_id, 6)

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

    def test_inventory_refuse_after_job_gone_liquidates_valid_only_translated(self):
        source = _read(SERVER_PATH)

        class _Identity:
            def __init__(self, uid: str) -> None:
                self._uid = _LogString.of(uid)

            def GetId(self) -> _LogString:
                return self._uid

        class _Actor:
            def __init__(self, identity: _Identity | None) -> None:
                self._identity = identity

            def GetIdentity(self) -> _Identity | None:
                return self._identity

        class _Args:
            def __init__(self, authority: str = "client", slot: str = "") -> None:
                self.authority = _LogString.of(authority)
                self.slot = _LogString.of(slot)

        class _Job:
            def __init__(self, ident: int, actor: _Actor) -> None:
                self.id = ident
                self.kind = _LogString.of("inventory_move")
                self.args = _Args("client")
                self.actor = actor
                self.subject = None
                self.phase = 0
                self.error = _LogString.of("")
                self.ready_now = False

        ignored: list[str] = []
        posted: list[object] = []
        timed_out: list[int] = []
        ns: dict[str, Any] = {
            "_S": _LogString,
            "array": EnforceArray,
            "m_Jobs": EnforceMap(),
            "m_LeaseCloseRpcIds": PendingArray(),
            "m_LeaseCloseRpcKinds": PendingArray(),
            "m_LeaseCloseRpcActors": PendingArray(),
            "m_LeaseCloseRpcSenderIds": PendingArray(),
            "Print": lambda msg: ignored.append(msg.text if isinstance(msg, EnforceString) else str(msg)),
            "MCPInventoryMoveReady": lambda j: bool(j.ready_now),
            "PostResult": lambda r: posted.append(r),
            "RegisterDeletionRefs": lambda _j: None,
            "PostJobTimeout": lambda j: timed_out.append(j.id),
            "MCPSendInventoryMove": lambda p, i, t, o, s, j: _LogString.of(""),
            "item": object(),
            "target": None,
            "op": _LogString.of("combine"),
        }
        rpc_members = (
            "m_LeaseCloseRpcIds",
            "m_LeaseCloseRpcKinds",
            "m_LeaseCloseRpcActors",
            "m_LeaseCloseRpcSenderIds",
        )
        with_actor_sig = "protected void RegisterLeaseCloseRpcWithActor(int requestId, string kind, Human actor)"
        if with_actor_sig in source:
            translate(_wrap(with_actor_sig, method_body(source, with_actor_sig)), with_actor_sig, ns,
                      members=rpc_members)
        for sig in (
            "protected void RegisterLeaseCloseRpc(int requestId, string kind)",
            "protected void LiquidateLeaseCloseRpc(int requestId, string kind)",
            "protected string MCPInventoryMoveClientReason(string reason)",
        ):
            translate(_wrap(sig, method_body(source, sig)), sig, ns, members=rpc_members)

        dispatch_body = method_body(source, "protected bool DispatchInventoryMove(MCPCommand command, MCPResult result)")
        reg_start = dispatch_body.find("m_Jobs.Insert(job.id, job);")
        self.assertGreaterEqual(reg_start, 0)
        if_start = dispatch_body.find('if (performError != "")', reg_start)
        self.assertGreaterEqual(if_start, 0)
        brace = dispatch_body.index("{", if_start)
        depth = 0
        reg_end = -1
        for idx in range(brace, len(dispatch_body)):
            if dispatch_body[idx] == "{":
                depth += 1
            elif dispatch_body[idx] == "}":
                depth -= 1
                if depth == 0:
                    reg_end = idx
                    break
        self.assertGreater(reg_end, brace)
        reg_fragment = dispatch_body[reg_start:reg_end + 1].replace("\n\t\t}\n", "\n", 1)
        reg_sig = "bool RegisterInventoryRpc(MCPJob job, PlayerBase player, MCPCommand command, MCPResult result)"
        reg_body = '\t\tbool clientRpc = true;\n\t\tstring performError = "";\n' + reg_fragment + "\n\t\treturn false;\n"
        translate(_wrap(reg_sig, reg_body), reg_sig, ns,
                  members=("m_Jobs",) + rpc_members)

        refuse_body = method_body(
            source,
            "void MCPOnInventoryMoveRefused(PlayerIdentity sender, Object target, int rpc_type, ParamsReadContext ctx)",
        )
        refuse_start = refuse_body.index("MCPJob job = m_Jobs.Get(jobId);")
        refuse_sig = "void RefusedAfterRead(PlayerIdentity sender, Object target, int jobId, string reason)"
        translate(
            _wrap(refuse_sig, refuse_body[refuse_start:]),
            refuse_sig,
            ns,
            class_types=("MCPJob", "Object"),
            members=("m_Jobs",) + rpc_members,
        )

        close_body = method_body(source, "protected bool DispatchLeaseClose(MCPCommand command, MCPResult result)")
        abort_start = close_body.index("if (m_Jobs)")
        abort_end = close_body.index("LeaseCloseExtensionStop();", abort_start)
        abort_sig = "void AbortLeaseCloseJobs()"
        translate(
            _wrap(abort_sig, close_body[abort_start:abort_end]),
            abort_sig,
            ns,
            class_types=("array", "MCPJob"),
            members=("m_Jobs",),
        )

        def _register_move(ident: int, actor: _Actor) -> _Job:
            job = _Job(ident, actor)
            cmd = type("C", (), {"id": ident, "args": _Args("client")})()
            res = type("R", (), {"ok": True, "error": _LogString.of("")})()
            ns["RegisterInventoryRpc"](job, actor, cmd, res)
            return job

        sender_a = _Identity("uid-a")
        sender_b = _Identity("uid-b")
        actor_a = _Actor(sender_a)
        actor_b = _Actor(sender_b)

        # Live job: wrong sender is ignored; valid refusal liquidates row and marks phase 2.
        live_job = _register_move(88, actor_a)
        self.assertEqual(ns["m_LeaseCloseRpcIds"].Count(), 1)
        ns["RefusedAfterRead"](sender_b, actor_a, 88, _LogString.of("not_streamed"))
        self.assertEqual(ns["m_LeaseCloseRpcIds"].Count(), 1)
        self.assertEqual(live_job.phase, 0)
        ns["RefusedAfterRead"](sender_a, actor_a, 88, _LogString.of("not_streamed"))
        self.assertEqual(ns["m_LeaseCloseRpcIds"].Count(), 0)
        self.assertEqual(live_job.phase, 2)
        self.assertEqual(live_job.error.text, "not_streamed")
        self.assertEqual(posted, [])
        ns["m_Jobs"].Remove(88)

        # Job timed out (removed from m_Jobs while RPC row remains open):
        # NEG: wrong id, wrong sender, wrong target, or null sender/target keep the row.
        _register_move(77, actor_a)
        ns["m_Jobs"].Remove(77)
        self.assertEqual(ns["m_Jobs"].Count(), 0)
        self.assertEqual(ns["m_LeaseCloseRpcIds"].Count(), 1)
        ns["RefusedAfterRead"](sender_a, actor_a, 999, _LogString.of("not_streamed"))
        ns["RefusedAfterRead"](sender_b, actor_a, 77, _LogString.of("not_streamed"))
        ns["RefusedAfterRead"](sender_a, actor_b, 77, _LogString.of("not_streamed"))
        ns["RefusedAfterRead"](None, actor_a, 77, _LogString.of("not_streamed"))
        ns["RefusedAfterRead"](sender_a, None, 77, _LogString.of("not_streamed"))
        self.assertEqual(ns["m_LeaseCloseRpcIds"].Count(), 1)
        self.assertEqual(posted, [])

        # POS after job timeout: matching sender and target liquidates the row without posting.
        ns["RefusedAfterRead"](sender_a, actor_a, 77, _LogString.of("not_streamed"))
        self.assertEqual(ns["m_LeaseCloseRpcIds"].Count(), 0)
        self.assertEqual(ns["m_LeaseCloseRpcKinds"].Count(), 0)
        self.assertEqual(ns["m_LeaseCloseRpcActors"].Count(), 0)
        self.assertEqual(ns["m_LeaseCloseRpcSenderIds"].Count(), 0)
        self.assertEqual(posted, [])

        # POS after DispatchLeaseClose job-abort loop (nulls job.actor and removes job from m_Jobs)
        # alongside an unrelated RPC row:
        aborted_job = _register_move(78, actor_a)
        ns["RegisterLeaseCloseRpc"](79, _LogString.of("hands_take"))
        ns["AbortLeaseCloseJobs"]()
        self.assertEqual(ns["m_Jobs"].Count(), 0)
        self.assertIsNone(aborted_job.actor)
        self.assertEqual(timed_out, [78])
        self.assertEqual(ns["m_LeaseCloseRpcIds"].Count(), 2)
        # Hands row (null actor) cannot be liquidated through inventory_move refusal.
        ns["RefusedAfterRead"](sender_a, actor_a, 79, _LogString.of("not_streamed"))
        self.assertEqual(ns["m_LeaseCloseRpcIds"].Count(), 2)
        # Valid late refusal liquidates only the inventory_move row and leaves hands_take.
        ns["RefusedAfterRead"](sender_a, actor_a, 78, _LogString.of("not_streamed"))
        self.assertEqual(list(ns["m_LeaseCloseRpcIds"]), [79])
        self.assertEqual([k.text for k in ns["m_LeaseCloseRpcKinds"]], ["hands_take"])
        self.assertEqual(posted, [])

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
        # D-r2-H5/N11 + P1-3 + R3 + R4: all five interrupt-debt sites mark process-lifetime debt.
        self.assertEqual(hold.count("s_InterruptDebt = true;"), 5)
        self.assertNotIn("s_InterruptDebt = false", hold)

    def test_shutdown_interrupt_marks_debt_translated(self):
        from types import SimpleNamespace as _NS
        hold_src = _read(SCRIPTS.parent / "4_World" / "MCP_ActionHold.c")

        def _make_hold_ns(can_store: bool):
            action = object()
            data = _NS(m_Action=action, m_State=1)
            state = {"current": data, "pending": None, "ignore": False, "interrupts": 0, "local_ends": 0}

            class _Mgr:
                def MCP_HoldPending(self):
                    return state["pending"]

                def MCP_HoldCurrent(self):
                    return state["current"]

                def MCP_HoldIgnore(self):
                    return state["ignore"]

                def SetIgnoreAutomaticInputEnd(self, flag):
                    state["ignore"] = flag

                def RequestInterruptAction(self):
                    state["interrupts"] += 1

                def MCP_HoldLocalEnd(self, target_data):
                    if state["current"] is target_data:
                        state["current"] = None
                    state["local_ends"] += 1

            class _Obs:
                pass

            ns = {
                "GetGame": lambda: _NS(GetTickTime=lambda: 12.0),
                "ScriptInputUserData": _NS(CanStoreInputUserData=lambda: can_store),
                "MCPHoldObservation": _Obs,
                "s_Active": False,
                "s_Ready": False,
                "s_Manager": None,
                "s_Player": None,
                "s_Action": None,
                "s_Data": None,
                "s_PrevPending": None,
                "s_PrevCurrent": None,
                "s_PrevIgnore": False,
                "s_FlagRestored": False,
                "s_HoldId": EnforceString.of(""),
                "s_StartS": 0.0,
                "s_DeadlineS": 0.0,
                "s_DrainNormalUntilS": 0.0,
                "s_DrainForceUntilS": 0.0,
                "s_TimeoutCause": False,
                "s_Terminal": False,
                "s_EndState": EnforceString.of(""),
                "s_Reason": EnforceString.of(""),
                "s_ActionState": 0,
                "s_ActionStateKnown": False,
                "s_Cycles": 0,
                "s_PlacedKnown": False,
                "s_Placed": False,
                "s_Started": False,
                "s_InterruptPending": False,
                "s_InterruptSent": False,
                "s_InterruptDebt": False,
                "s_Observation": None,
            }
            # Seed any additional protected static fields declared on MCPHoldControl
            # so later input-debt counters default cleanly without injection.
            ctrl_block = hold_src.split("class MCPHoldControl", 1)[1].split("class MCPActionUseWatch", 1)[0]
            for ftype, fname in re.findall(r"protected static (\w+)\s+(s_\w+)\s*;", ctrl_block):
                if fname not in ns:
                    ns[fname] = False if ftype == "bool" else (0 if ftype == "int" else (0.0 if ftype == "float" else None))
            for fname in re.findall(r"protected static ref array<[^>]+>\s+(s_\w+)\s*;", ctrl_block):
                if fname not in ns:
                    ns[fname] = PendingArray()

            members = tuple(k for k in ns if k.startswith("s_"))
            for sig in (
                "static bool IsActive()",
                "static bool InputInFlight()",
                "protected static float NowS()",
                "protected static void RestoreFlag()",
                "protected static bool DataStillLocal()",
                "protected static void Adopt(ActionManagerClient manager)",
                "static void Begin(ActionManagerClient manager, PlayerBase player, ActionBase action, string holdId, float holdTimeoutS)",
                "static void AfterStart(ActionManagerClient manager)",
                "protected static void Publish()",
                "static MCPHoldObservation Take()",
                "static void Shutdown(string reason)",
            ):
                translate(
                    _wrap(sig, method_body(hold_src, sig)),
                    sig,
                    ns,
                    class_types=("ActionData", "MCPHoldObservation"),
                    members=members,
                )
            mgr = _Mgr()
            # Begin snapshots pre-existing current; simulate the start adopting data after Begin.
            state["current"] = None
            ns["Begin"](mgr, object(), action, EnforceString.of("h-1"), 10.0)
            state["current"] = data
            ns["AfterStart"](mgr)
            return ns, state, mgr, action

        # POS: CanStoreInputUserData true sends RequestInterruptAction and keeps
        # InputInFlight() true after Take() clears the active hold and even if a
        # subsequent Begin resets s_InterruptSent.
        ns_pos, state_pos, mgr_pos, act_pos = _make_hold_ns(True)
        self.assertFalse(ns_pos["InputInFlight"]())
        ns_pos["Shutdown"](EnforceString.of("shutdown"))
        obs = ns_pos["Take"]()
        self.assertIsNotNone(obs)
        self.assertEqual((obs.end_state.text, obs.reason.text), ("cancel", "shutdown"))
        self.assertFalse(ns_pos["IsActive"]())
        self.assertEqual(state_pos["interrupts"], 1)
        self.assertTrue(ns_pos["InputInFlight"]())
        ns_pos["Begin"](mgr_pos, object(), act_pos, EnforceString.of("h-2"), 10.0)
        self.assertTrue(ns_pos["InputInFlight"]())

        # NEG: CanStoreInputUserData false sends no interrupt and adds no debt.
        ns_neg, state_neg, _mgr_neg, _act_neg = _make_hold_ns(False)
        ns_neg["Shutdown"](EnforceString.of("shutdown"))
        obs_neg = ns_neg["Take"]()
        self.assertIsNotNone(obs_neg)
        self.assertFalse(ns_neg["IsActive"]())
        self.assertEqual(state_neg["interrupts"], 0)
        self.assertFalse(ns_neg["InputInFlight"]())

    def _make_action_input_harness(
        self,
        hold_src_override: str | None = None,
        client_src_override: str | None = None,
    ):
        from types import SimpleNamespace as _NS

        hold_src = (
            hold_src_override
            if hold_src_override is not None
            else _read(SCRIPTS.parent / "4_World" / "MCP_ActionHold.c")
        )
        client_src = client_src_override if client_src_override is not None else _read(CLIENT_PATH)
        UA_NONE = 0
        UA_AM_PENDING = 1
        UA_AM_ACCEPTED = 2
        UA_AM_REJECTED = 3
        UA_FINISHED = 4
        UA_START = 10
        UA_INITIALIZE = 11
        UA_PROCESSING = 12
        UA_CANCEL = 13
        UA_ERROR = 24
        SJ_ACTION_ACK_ACCEPT = 6
        SJ_ACTION_ACK_REJECT = 7
        SJ_ACTION_INTERRUPT = 2
        CMD_ACTIONINT_INTERRUPT = 2

        env: dict[str, Any] = {
            "now": 10.0,
            "mp": True,
            "is_server": False,
            "can_store": True,
            "single_slot": False,
            "possible": True,
            "condition_ok": True,
            "held_item": None,
            "sends": [],
            "server_junctures": [],
        }
        player_ref: dict[str, Any] = {"player": None}

        class _Obs:
            pass

        game = _NS(
            GetTickTime=lambda: env["now"],
            IsMultiplayer=lambda: env["mp"],
            IsServer=lambda: env["is_server"],
            GetPlayer=lambda: player_ref["player"],
        )
        script_input_ud = _NS(CanStoreInputUserData=lambda: env["can_store"])
        hold_ns: dict[str, Any] = {
            "GetGame": lambda: game,
            "PlayerBase": _NS(Cast=lambda p: p),
            "PlaceObjectActionData": _NS(Cast=lambda _d: None),
            "ScriptInputUserData": script_input_ud,
            "DayZPlayerSyncJunctures": _NS(
                SJ_ACTION_ACK_ACCEPT=SJ_ACTION_ACK_ACCEPT,
                SJ_ACTION_ACK_REJECT=SJ_ACTION_ACK_REJECT,
                SJ_ACTION_INTERRUPT=SJ_ACTION_INTERRUPT,
            ),
            "MCPHoldObservation": _Obs,
            "array": PendingArray,
            "UA_AM_PENDING": UA_AM_PENDING,
            "UA_AM_ACCEPTED": UA_AM_ACCEPTED,
            "UA_AM_REJECTED": UA_AM_REJECTED,
            "UA_FINISHED": UA_FINISHED,
            "s_Active": False,
            "s_Ready": False,
            "s_Manager": None,
            "s_Player": None,
            "s_Action": None,
            "s_Data": None,
            "s_PrevPending": None,
            "s_PrevCurrent": None,
            "s_PrevIgnore": False,
            "s_FlagRestored": False,
            "s_HoldId": EnforceString.of(""),
            "s_StartS": 0.0,
            "s_DeadlineS": 0.0,
            "s_DrainNormalUntilS": 0.0,
            "s_DrainForceUntilS": 0.0,
            "s_TimeoutCause": False,
            "s_Terminal": False,
            "s_EndState": EnforceString.of(""),
            "s_Reason": EnforceString.of(""),
            "s_ActionState": 0,
            "s_ActionStateKnown": False,
            "s_Cycles": 0,
            "s_PlacedKnown": False,
            "s_Placed": False,
            "s_Started": False,
            "s_InterruptPending": False,
            "s_InterruptSent": False,
            "s_InterruptDebt": False,
            "s_StartDebt": False,
            "s_EndDebt": False,
            "s_AckManagers": PendingArray(),
            "s_AckIds": PendingArray(),
            "s_StartedManagers": PendingArray(),
            "s_StartedData": PendingArray(),
            "s_Observation": None,
        }
        ctrl_block = hold_src.split("class MCPHoldControl", 1)[1].split("class MCPActionUseWatch", 1)[0]
        for ftype, fname in re.findall(r"protected static (\w+)\s+(s_\w+)\s*;", ctrl_block):
            if fname not in hold_ns:
                hold_ns[fname] = (
                    False if ftype == "bool" else (0 if ftype == "int" else (0.0 if ftype == "float" else None))
                )
        for fname in re.findall(r"protected static ref array<[^>]+>\s+(s_\w+)\s*;", ctrl_block):
            if fname not in hold_ns:
                hold_ns[fname] = PendingArray()

        hold_members = tuple(k for k in hold_ns if k.startswith("s_"))
        hold_sigs = (
            "static bool IsActive()",
            "static bool InputInFlight()",
            "protected static void ClearStartCapture()",
            "protected static void RecordStartedAction(ActionManagerClient manager, ActionData data)",
            "protected static bool IsStartedAction(ActionManagerClient manager, ActionData data)",
            "static bool IsStartedAction(ActionManagerClient manager, ActionData data)",
            "static bool IsStartedActionData(ActionData data)",
            "protected static void RecordStartAck(ActionManagerClient manager, int ackId)",
            "static void ArmStart(ActionManagerClient manager, ActionBase action)",
            "static void DisarmStart()",
            "static void BeforeStartCapture(ActionManagerClient manager, ActionBase action)",
            "static void AfterStartCapture(ActionManagerClient manager, ActionBase action)",
            "static void AfterSyncJuncture(ActionManagerClient manager, int junctureId, ActionData beforeData, int beforeState, int beforeAckId, ActionData afterData)",
            "static void AfterProcessInputEnd(bool mcpOwned, ActionData beforeData, bool sentBefore, bool sentAfter)",
            "static void AfterProcessRequestEnd(bool mcpOwned, ActionData beforeData, bool sentBefore, bool sentAfter)",
            "static void AfterRequestInterrupt(ActionManagerClient manager, ActionData beforeData, bool canStore)",
            "static void AfterCallbackInterrupt(bool mcpOwned, bool canSend)",
            "protected static float NowS()",
            "protected static void RestoreFlag()",
            "protected static bool DataStillLocal()",
            "protected static void Adopt(ActionManagerClient manager)",
            "static void Begin(ActionManagerClient manager, PlayerBase player, ActionBase action, string holdId, float holdTimeoutS)",
            "static void AfterStart(ActionManagerClient manager)",
            "static bool HasData()",
            "protected static void Capture(ActionData data)",
            "static void BeforeActionEnd(ActionManagerClient manager)",
            "static void AfterActionEnd(ActionManagerClient manager)",
            "protected static void RememberState()",
            "protected static bool TransmitInterrupt()",
            "protected static void Publish()",
            "static bool Ready()",
            "static MCPHoldObservation Take()",
            "protected static void Finish(string endState, string reason)",
            "static void NoteSetupFailed()",
            "static bool Cancel(string holdId)",
            "static void OnManagerDestroyed(ActionManagerClient manager)",
            "static void Shutdown(string reason)",
            "protected static void RequestForceClose()",
            "static void Maintain()",
        )
        for sig in hold_sigs:
            if sig in hold_src:
                translate(
                    _wrap(sig, method_body(hold_src, sig)),
                    sig,
                    hold_ns,
                    class_types=(
                        "ActionData",
                        "ActionManagerClient",
                        "ActionBase",
                        "PlayerBase",
                        "MCPHoldObservation",
                        "PlaceObjectActionData",
                    ),
                    members=hold_members,
                )

        hold_ctl = _NS(
            **{
                name: hold_ns[name]
                for name in (
                    "IsActive",
                    "InputInFlight",
                    "IsStartedAction",
                    "IsStartedActionData",
                    "ArmStart",
                    "DisarmStart",
                    "BeforeStartCapture",
                    "AfterStartCapture",
                    "AfterSyncJuncture",
                    "AfterProcessInputEnd",
                    "AfterProcessRequestEnd",
                    "AfterRequestInterrupt",
                    "AfterCallbackInterrupt",
                    "Begin",
                    "AfterStart",
                    "HasData",
                    "BeforeActionEnd",
                    "AfterActionEnd",
                    "Ready",
                    "Take",
                    "NoteSetupFailed",
                    "Cancel",
                    "OnManagerDestroyed",
                    "Shutdown",
                    "Maintain",
                )
                if name in hold_ns
            }
        )

        watch_ns: dict[str, Any] = {"s_Watched": None, "s_WatchedTarget": None}
        is_watched_sig = "protected static bool IsWatched(ActionBase action, ActionData action_data)"
        translate(
            _wrap(is_watched_sig, method_body(hold_src, is_watched_sig)),
            is_watched_sig,
            watch_ns,
            class_types=("ActionBase", "ActionData"),
            members=("s_Watched", "s_WatchedTarget"),
        )
        watch_state = {"starts": 0}

        def _watch_begin(action, target):
            watch_ns["s_Watched"] = action
            watch_ns["s_WatchedTarget"] = target
            watch_state["starts"] = 0

        def _watch_end():
            starts = watch_state["starts"]
            watch_ns["s_Watched"] = None
            watch_ns["s_WatchedTarget"] = None
            watch_state["starts"] = 0
            return starts

        def _watch_note_start(action, action_data):
            if watch_ns["IsWatched"](action, action_data):
                watch_state["starts"] += 1

        use_watch = _NS(Begin=_watch_begin, End=_watch_end, NoteStart=_watch_note_start)

        amc_ns: dict[str, Any] = {
            "GetGame": lambda: game,
            "ScriptInputUserData": script_input_ud,
            "MCPHoldControl": hold_ctl,
            "UA_AM_PENDING": UA_AM_PENDING,
            "UA_AM_ACCEPTED": UA_AM_ACCEPTED,
            "UA_AM_REJECTED": UA_AM_REJECTED,
            "m_PendingActionData": None,
            "m_CurrentActionData": None,
            "m_IgnoreAutoInputEnd": False,
            "m_PendingActionAcknowledgmentID": -1,
            "m_LastAcknowledgmentID": 1,
            "m_ActionInputWantEnd": False,
            "m_ActionInputWantEnd_Send": False,
            "m_ActionWantEndRequest": False,
            "m_ActionWantEndRequest_Send": False,
            "m_Interrupted": False,
        }

        def _new_action_data(action, target, item):
            return _NS(
                m_Action=action,
                m_State=UA_NONE,
                m_Player=player_ref["player"],
                m_Target=target,
                m_MainItem=item,
                m_Callback=None,
                m_ActionComponent=None,
                m_ReciveEndInput=False,
            )

        def _super_on_action_end():
            if amc_ns["m_CurrentActionData"] is not None:
                amc_ns["m_CurrentActionData"] = None

        def _vanilla_action_start(action, target, item, extra_data):
            # Vanilla DayZ 1.30 ActionManagerClient.ActionStart (:626-724).
            if amc_ns["m_CurrentActionData"] is not None or action is None or not env["possible"]:
                return
            amc_ns["m_ActionWantEndRequest_Send"] = False
            amc_ns["m_ActionInputWantEnd_Send"] = False
            amc_ns["m_ActionWantEndRequest"] = False
            amc_ns["m_ActionInputWantEnd"] = False
            amc_ns["m_Interrupted"] = False
            if env["mp"] and not action.IsLocal() and not env["can_store"]:
                return
            if not action._run_setup(player_ref["player"], target, item, extra_data):
                amc_ns["m_CurrentActionData"] = None
                return
            data = _new_action_data(action, target, item)
            amc_ns["m_CurrentActionData"] = data
            if env["mp"] and not action.IsLocal():
                if action.UseAcknowledgment():
                    data.m_State = UA_AM_PENDING
                    amc_ns["m_LastAcknowledgmentID"] += 1
                    amc_ns["m_PendingActionAcknowledgmentID"] = amc_ns["m_LastAcknowledgmentID"]
                    env["sends"].append(("START_ACK", amc_ns["m_PendingActionAcknowledgmentID"]))
                else:
                    env["sends"].append(("START_NO_ACK", None))
                    action._run_start(data)
                    if action.IsInstant():
                        mgr.OnActionEnd()
            else:
                action._run_start(data)
                if action.IsInstant():
                    mgr.OnActionEnd()

        def _super_perform_action_start(action, target, item, extra_data):
            # Vanilla DayZ 1.30 PerformActionStart (:804-820) + ActionStart (:626-724).
            if not env["mp"]:
                if amc_ns["m_PendingActionData"] is None and amc_ns["m_CurrentActionData"] is None:
                    if action._run_setup(player_ref["player"], target, item, extra_data):
                        amc_ns["m_PendingActionData"] = _new_action_data(action, target, item)
                return
            _vanilla_action_start(action, target, item, extra_data)

        def _super_on_sync_juncture(juncture_id, ctx):
            # Vanilla ActionManagerBase.OnSyncJuncture (actionmanagerbase.c:156-193).
            if juncture_id == SJ_ACTION_ACK_ACCEPT:
                ack_id = ctx.read_ack_id()
                if amc_ns["m_CurrentActionData"] is not None and ack_id == amc_ns["m_PendingActionAcknowledgmentID"]:
                    amc_ns["m_CurrentActionData"].m_State = UA_AM_ACCEPTED
            elif juncture_id == SJ_ACTION_ACK_REJECT:
                ack_id = ctx.read_ack_id()
                if amc_ns["m_CurrentActionData"] is not None and ack_id == amc_ns["m_PendingActionAcknowledgmentID"]:
                    amc_ns["m_CurrentActionData"].m_State = UA_AM_REJECTED
            elif juncture_id == SJ_ACTION_INTERRUPT:
                amc_ns["m_Interrupted"] = True

        def _super_process_input_end():
            # Vanilla ActionManagerClient.ProcessActionInputEnd (:379-413).
            cur = amc_ns["m_CurrentActionData"]
            if amc_ns["m_ActionInputWantEnd"] and cur is not None:
                if env["mp"] and not cur.m_Action.IsLocal():
                    if not amc_ns["m_ActionInputWantEnd_Send"] and env["can_store"]:
                        env["sends"].append(("INPUT_END", None))
                        if env.get("single_slot"):
                            env["can_store"] = False
                        amc_ns["m_ActionInputWantEnd_Send"] = True
                        amc_ns["m_ActionInputWantEnd"] = False
                        cur.m_Action.EndInput(cur)
                else:
                    if not amc_ns["m_ActionInputWantEnd_Send"]:
                        amc_ns["m_ActionInputWantEnd_Send"] = True
                        amc_ns["m_ActionInputWantEnd"] = False
                        cur.m_Action.EndInput(cur)

        def _super_process_request_end():
            # Vanilla ActionManagerClient.ProcessActionRequestEnd (:347-377).
            cur = amc_ns["m_CurrentActionData"]
            if amc_ns["m_ActionWantEndRequest"] and cur is not None:
                if env["mp"] and not cur.m_Action.IsLocal():
                    if not amc_ns["m_ActionWantEndRequest_Send"] and env["can_store"]:
                        env["sends"].append(("END_REQUEST", None))
                        if env.get("single_slot"):
                            env["can_store"] = False
                        amc_ns["m_ActionWantEndRequest_Send"] = True
                        amc_ns["m_ActionWantEndRequest"] = False
                        cur.m_Action.EndRequest(cur)
                else:
                    amc_ns["m_ActionWantEndRequest"] = False
                    cur.m_Action.EndRequest(cur)

        def _super_request_interrupt_action():
            # Vanilla ActionManagerClient.RequestInterruptAction (:1341-1350):
            # writes INPUT_UDT_STANDARD_ACTION_END_REQUEST + CMD_ACTIONINT_INTERRUPT
            # and calls ctx.Send(), which occupies the single ScriptInputUserData slot.
            if env["can_store"]:
                env["sends"].append(("INTERRUPT", CMD_ACTIONINT_INTERRUPT))
                if env.get("single_slot"):
                    env["can_store"] = False

        super_amc = _NS(
            PerformActionStart=_super_perform_action_start,
            OnActionEnd=_super_on_action_end,
            OnSyncJuncture=_super_on_sync_juncture,
            ProcessActionInputEnd=_super_process_input_end,
            ProcessActionRequestEnd=_super_process_request_end,
            RequestInterruptAction=_super_request_interrupt_action,
        )

        class _Mgr:
            def MCP_HoldBusy(self):
                return amc_ns["MCP_HoldBusy"]()

            def MCP_HoldPending(self):
                return amc_ns["MCP_HoldPending"]()

            def MCP_HoldCurrent(self):
                return amc_ns["MCP_HoldCurrent"]()

            def MCP_HoldIgnore(self):
                return amc_ns["MCP_HoldIgnore"]()

            def MCP_PendingAckId(self):
                if "MCP_PendingAckId" in amc_ns:
                    return amc_ns["MCP_PendingAckId"]()
                return amc_ns["m_PendingActionAcknowledgmentID"]

            def MCP_HoldLocalEnd(self, data):
                return amc_ns["MCP_HoldLocalEnd"](data)

            def GetRunningAction(self):
                cur = amc_ns["m_CurrentActionData"]
                return None if cur is None else cur.m_Action

            def ActionPossibilityCheck(self, _cmd_id):
                return bool(env["possible"])

            def SetIgnoreAutomaticInputEnd(self, state):
                amc_ns["m_IgnoreAutoInputEnd"] = bool(state)

            def EndActionInput(self):
                # Vanilla ActionManagerClient.EndActionInput (:286-298): sets flag only.
                if not amc_ns["m_ActionInputWantEnd_Send"]:
                    amc_ns["m_ActionInputWantEnd"] = True

            def RequestEndAction(self):
                # Vanilla ActionManagerClient.RequestEndAction (:278-284): sets flag only.
                if not amc_ns["m_ActionWantEndRequest_Send"]:
                    amc_ns["m_ActionWantEndRequest"] = True

            def RequestInterruptAction(self):
                if "RequestInterruptAction" in amc_ns:
                    return amc_ns["RequestInterruptAction"]()
                return _super_request_interrupt_action()

            def Interrupt(self):
                # Vanilla ActionManagerBase.Interrupt (actionmanagerbase.c:302-315).
                if amc_ns["m_CurrentActionData"] is not None:
                    if env["mp"]:
                        self.RequestInterruptAction()
                    else:
                        self.LocalInterrupt()

            def LocalInterrupt(self):
                # Vanilla ActionManagerBase.LocalInterrupt (actionmanagerbase.c:318-324).
                cur = amc_ns["m_CurrentActionData"]
                if cur is not None and cur.m_Action is not None:
                    cur.m_Action.Interrupt(cur)
                else:
                    amc_ns["m_Interrupted"] = False

            def ActionStart(self, action, target, item, extra_data=None):
                # Direct vanilla ActionStart (:626-724) used by non-MCP player/quickbar/inventory paths.
                return _vanilla_action_start(action, target, item, extra_data)

            def PerformActionStart(self, action, target, item, extra_data=None):
                return amc_ns["PerformActionStart"](action, target, item, extra_data)

            def OnActionEnd(self):
                return amc_ns["OnActionEnd"]()

            def OnSyncJuncture(self, juncture_id, ack_id):
                ctx = _NS(read_ack_id=lambda: ack_id)
                if "OnSyncJuncture" in amc_ns:
                    return amc_ns["OnSyncJuncture"](juncture_id, ctx)
                return _super_on_sync_juncture(juncture_id, ctx)

            def ProcessActionInputEnd(self):
                if "ProcessActionInputEnd" in amc_ns:
                    return amc_ns["ProcessActionInputEnd"]()
                return _super_process_input_end()

            def ProcessActionRequestEnd(self):
                if "ProcessActionRequestEnd" in amc_ns:
                    return amc_ns["ProcessActionRequestEnd"]()
                return _super_process_request_end()

            def Update(self, current_command_id=0):
                # Vanilla ActionManagerBase.Update (:112-128) + ActionManagerClient.Update (:41-115).
                if amc_ns["m_CurrentActionData"] is not None:
                    if amc_ns["m_Interrupted"]:
                        self.LocalInterrupt()
                elif amc_ns["m_Interrupted"]:
                    amc_ns["m_Interrupted"] = False

                if amc_ns["m_PendingActionData"] is not None:
                    amc_ns["m_CurrentActionData"] = amc_ns["m_PendingActionData"]
                    amc_ns["m_CurrentActionData"].m_Action._run_start(amc_ns["m_CurrentActionData"])
                    if amc_ns["m_CurrentActionData"] is not None and amc_ns["m_CurrentActionData"].m_Action.IsInstant():
                        self.OnActionEnd()
                    amc_ns["m_PendingActionData"] = None

                cur = amc_ns["m_CurrentActionData"]
                if cur is not None:
                    state = cur.m_State
                    if state == UA_AM_PENDING:
                        pass
                    elif state == UA_AM_ACCEPTED:
                        if env["possible"] and env["condition_ok"]:
                            cur.m_State = UA_START
                            cur.m_Action._run_start(cur)
                            if amc_ns["m_CurrentActionData"] is not None and cur.m_Action.IsInstant():
                                self.OnActionEnd()
                        else:
                            self.RequestInterruptAction()
                            self.OnActionEnd()
                        amc_ns["m_PendingActionAcknowledgmentID"] = -1
                    elif state == UA_AM_REJECTED:
                        self.OnActionEnd()
                        amc_ns["m_PendingActionAcknowledgmentID"] = -1
                    else:
                        self.ProcessActionRequestEnd()
                        self.ProcessActionInputEnd()

        mgr = _Mgr()
        amc_ns["this"] = mgr
        amc_ns["super"] = super_amc
        amc_members = tuple(k for k in amc_ns if k.startswith("m_"))
        for raw_sig in (
            "bool MCP_HoldBusy()",
            "ActionData MCP_HoldPending()",
            "ActionData MCP_HoldCurrent()",
            "bool MCP_HoldIgnore()",
            "int MCP_PendingAckId()",
            "void MCP_HoldLocalEnd(ActionData data)",
            "override void PerformActionStart(ActionBase action, ActionTarget target, ItemBase item, Param extra_data = NULL)",
            "override void OnSyncJuncture(int pJunctureID, ParamsReadContext pCtx)",
            "override void ProcessActionInputEnd()",
            "override void ProcessActionRequestEnd()",
            "override void RequestInterruptAction()",
            "override void OnActionEnd()",
        ):
            if raw_sig in hold_src:
                clean_sig = raw_sig.replace(" = NULL", "")
                translate(
                    _wrap(clean_sig, method_body(hold_src, raw_sig)),
                    clean_sig,
                    amc_ns,
                    class_types=("ActionData",),
                    members=amc_members,
                )

        player = _NS(
            IsAlive=lambda: True,
            GetNetworkID=lambda low, high: None,
            MCPConnGenValue=lambda: 7,
            GetActionManager=lambda: mgr,
            GetEmoteManager=lambda: None,
            GetCurrentCommandID=lambda: 0,
            GetItemInHands=lambda: env["held_item"],
            IsPlacingLocal=lambda: False,
        )
        player_ref["player"] = player

        # Translate real MCPClientBridge.DispatchActionUse (action_hold + action_use) so MCP
        # starts run through the bridge's actual ArmStart/PerformActionStart/DisarmStart path.
        dispatch_body = method_body(
            client_src,
            "protected bool DispatchActionUse(MCPCommand command, MCPResult result)",
        )
        run_match = re.search(r"\bif\s*\(\s*amc\s*\.\s*GetRunningAction\s*\(\s*\)\s*!=\s*null\s*\)", dispatch_body)
        assert run_match is not None
        dispatch_region = re.sub(
            r"^[ \t]*#ifndef DAYZ_1_29[ \t]*\n(.*?)^[ \t]*#endif[ \t]*\n",
            r"\1",
            dispatch_body[run_match.start() :],
            flags=re.M | re.S,
        )
        dispatch_sig = (
            "bool MCPDispatchTail(MCPCommand command, MCPResult result, "
            "ActionBase action, ActionTarget actionTarget, ActionManagerClient amc, PlayerBase player)"
        )
        dispatch_ns: dict[str, Any] = {
            "MCPHoldControl": hold_ctl,
            "MCPActionUseWatch": use_watch,
            "ScriptInputUserData": script_input_ud,
            "NULL": None,
            "ApplyDeployRefusal": lambda _a, _p, _r: None,
            "ApplyHoldObservation": lambda res, obs: setattr(res, "obs", obs),
            "MCPJob": lambda: _NS(
                id=0,
                kind=EnforceString.of(""),
                deadline_s=0.0,
                tick_poll_sent=0,
                tick_poll_callback=0,
                tick_dispatch=0,
            ),
            "m_JobRunner": _NS(
                GetElapsedS=lambda: env["now"],
                AddJob=lambda job: env.setdefault("jobs", []).append(job),
            ),
            "m_ActionHoldCommandId": 0,
        }
        translate(
            _wrap(dispatch_sig, dispatch_region),
            dispatch_sig,
            dispatch_ns,
            class_types=(
                "MCPCommand",
                "MCPResult",
                "ActionBase",
                "ActionTarget",
                "ActionManagerClient",
                "PlayerBase",
                "ItemBase",
                "MCPJob",
            ),
            members=("m_JobRunner", "m_ActionHoldCommandId"),
        )

        def _mcp_use(action, target=None, item=None):
            env["held_item"] = item
            cmd = _NS(
                id=1,
                cmd=EnforceString.of("action_use"),
                args=_NS(hold_id=EnforceString.of(""), hold_timeout_s=0.0),
            )
            res = _NS(
                ok=True,
                started=False,
                placing=False,
                error=EnforceString.of(""),
                obs=None,
                tick_poll_sent=0,
                tick_poll_callback=0,
                tick_dispatch=0,
            )
            dispatch_ns["MCPDispatchTail"](cmd, res, action, target, mgr, player)
            return res

        def _mcp_hold(action, hold_id: str = "hold-1", timeout_s: float = 5.0, target=None, item=None):
            env["held_item"] = item
            cmd = _NS(
                id=1,
                cmd=EnforceString.of("action_hold"),
                args=_NS(hold_id=EnforceString.of(hold_id), hold_timeout_s=float(timeout_s)),
            )
            res = _NS(
                ok=True,
                started=False,
                placing=False,
                error=EnforceString.of(""),
                obs=None,
                tick_poll_sent=0,
                tick_poll_callback=0,
                tick_dispatch=0,
            )
            dispatch_ns["MCPDispatchTail"](cmd, res, action, target, mgr, player)
            return res

        env["mcp_use"] = _mcp_use
        env["mcp_hold"] = _mcp_hold

        def _make_callback():
            # Real DayZ 1.30 ActionBaseCB (animatedactionbase.c:1-116) + modded ActionBaseCB hooks.
            cb_ns: dict[str, Any] = {
                "GetGame": lambda: game,
                "ScriptInputUserData": script_input_ud,
                "MCPHoldControl": hold_ctl,
                "m_ActionData": None,
                "m_Interrupted": False,
                "m_Canceled": False,
                "m_MCPOwned": False,
            }

            def _super_cb_set_action_data(action_data):
                cb_ns["m_ActionData"] = action_data

            def _super_cb_interrupt():
                # Vanilla ActionBaseCB.Interrupt (animatedactionbase.c:85-111).
                if env["is_server"]:
                    if env["mp"] and cb_ns["m_ActionData"] is not None:
                        env["server_junctures"].append(("SJ_ACTION_INTERRUPT", cb_ns["m_ActionData"].m_Player))
                else:
                    if env["can_store"]:
                        env["sends"].append(("CB_INTERRUPT", CMD_ACTIONINT_INTERRUPT))
                        if env.get("single_slot"):
                            env["can_store"] = False
                cb_ns["m_Interrupted"] = True

            class _ActionBaseCB:
                @property
                def m_ActionData(self):
                    return cb_ns["m_ActionData"]

                @property
                def m_Interrupted(self):
                    return cb_ns["m_Interrupted"]

                @property
                def m_Canceled(self):
                    return cb_ns["m_Canceled"]

                def SetActionData(self, action_data):
                    if "SetActionData" in cb_ns:
                        return cb_ns["SetActionData"](action_data)
                    return _super_cb_set_action_data(action_data)

                def CreateActionComponent(self):
                    cb_ns["m_ActionData"].m_ActionComponent = _NS(
                        Execute=lambda _ad: UA_PROCESSING,
                        Interrupt=lambda _ad: UA_CANCEL,
                    )

                def InitActionComponent(self):
                    if cb_ns["m_ActionData"].m_ActionComponent is None:
                        self.CreateActionComponent()

                def ProgressActionComponent(self):
                    ad = cb_ns["m_ActionData"]
                    if ad is not None and ad.m_ActionComponent is not None:
                        ad.m_State = ad.m_ActionComponent.Execute(ad)

                def EndActionComponent(self):
                    pass

                def Cancel(self):
                    cb_ns["m_Canceled"] = True

                def Interrupt(self):
                    if "Interrupt" in cb_ns:
                        return cb_ns["Interrupt"]()
                    return _super_cb_interrupt()

                def OnFinish(self, p_canceled: bool):
                    # Vanilla ActionBaseCB.OnFinish (animatedactionbase.c:27-54).
                    ad = cb_ns["m_ActionData"]
                    if ad is not None and ad.m_Player is not None:
                        if p_canceled and ad.m_ActionComponent is not None:
                            ad.m_State = ad.m_ActionComponent.Interrupt(ad)
                        ad.m_Action.End(ad)

            cb = _ActionBaseCB()
            cb_ns["this"] = cb
            cb_ns["super"] = _NS(
                SetActionData=_super_cb_set_action_data,
                Interrupt=_super_cb_interrupt,
            )
            cb_members = ("m_ActionData", "m_Interrupted", "m_Canceled", "m_MCPOwned")
            for cb_sig in (
                "override void SetActionData(ActionData action_data)",
                "override void Interrupt()",
            ):
                if cb_sig in hold_src:
                    translate(
                        _wrap(cb_sig, method_body(hold_src, cb_sig)),
                        cb_sig,
                        cb_ns,
                        class_types=("ActionData",),
                        members=cb_members,
                    )
            return cb

        def _make_action(
            *,
            setup_ok=True,
            use_ack=True,
            is_local=False,
            is_instant=False,
            end_on_refused=False,
            drop_store_in_can=False,
            animated=False,
            can_continue=True,
            end_on_input_end=False,
            end_on_request_end=False,
        ):
            act_ns: dict[str, Any] = {
                "MCPActionUseWatch": use_watch,
            }

            class _Action:
                def __init__(self):
                    self.can_continue = can_continue

                def Can(self, _pl, _tgt, _it):
                    if drop_store_in_can:
                        env["can_store"] = False
                    return True

                def CanBeStarted(self):
                    return True

                def CanContinue(self, _action_data):
                    return bool(self.can_continue)

                def UseAcknowledgment(self):
                    return use_ack

                def IsLocal(self):
                    return is_local

                def IsInstant(self):
                    return is_instant

                def CreateAndSetupActionCallback(self, action_data):
                    # Vanilla AnimatedActionBase.CreateAndSetupActionCallback (animatedactionbase.c:331-355).
                    cb = _make_callback()
                    cb.SetActionData(action_data)
                    cb.InitActionComponent()
                    action_data.m_Callback = cb

                def End(self, action_data):
                    # Vanilla ActionBase.End (:802-819) / AnimatedActionBase.End (animatedactionbase.c:458-502).
                    if action_data is None:
                        return
                    if action_data.m_Player is not None:
                        action_data.m_Player.GetActionManager().OnActionEnd()
                    elif animated and action_data.m_Callback is not None:
                        action_data.m_Callback.Interrupt()

                def Interrupt(self, data):
                    # AnimatedActionBase.Interrupt (:377-387) cancels callback (or End);
                    # ActionBase.Interrupt (:821-830) calls End -> OnActionEnd.
                    if data is None:
                        return
                    if animated:
                        if data.m_Callback is not None:
                            data.m_Callback.Cancel()
                        else:
                            self.End(data)
                    else:
                        self.End(data)

                def EndInput(self, action_data):
                    # Vanilla ActionBase.EndInput (:835-845).
                    action_data.m_ReciveEndInput = True
                    if end_on_input_end:
                        self.End(action_data)

                def EndRequest(self, action_data):
                    # Vanilla ActionBase.EndRequest (:850-859).
                    if end_on_request_end:
                        self.End(action_data)

                def Do(self, action_data, state):
                    # Vanilla AnimatedActionBase.Do (animatedactionbase.c:395-455).
                    if (
                        state == UA_ERROR
                        or action_data.m_Callback is None
                        or action_data.m_Player is None
                        or action_data.m_ActionComponent is None
                    ):
                        if action_data.m_Callback is not None and action_data.m_Player is not None:
                            action_data.m_Callback.Interrupt()
                        return
                    if state == UA_PROCESSING:
                        if self.CanContinue(action_data):
                            action_data.m_Callback.ProgressActionComponent()
                        else:
                            action_data.m_Callback.Interrupt()
                            self.Do(action_data, UA_CANCEL)
                    elif state in (UA_FINISHED, UA_CANCEL):
                        action_data.m_Callback.EndActionComponent()
                    elif state == UA_INITIALIZE:
                        if not self.CanContinue(action_data):
                            action_data.m_Callback.Interrupt()
                            self.Do(action_data, UA_CANCEL)
                        else:
                            action_data.m_Callback.Interrupt()
                    else:
                        action_data.m_Callback.Interrupt()

                def _run_setup(self, _pl, _tgt, _it, _extra):
                    if not setup_ok and end_on_refused:
                        mgr.OnActionEnd()
                    return bool(setup_ok)

                def _run_start(self, data):
                    data.m_State = UA_START
                    act_ns["Start"](data)
                    if animated and not self.IsInstant():
                        self.CreateAndSetupActionCallback(data)

            act = _Action()
            act_ns["this"] = act
            act_ns["super"] = _NS(Start=lambda _ad: None)
            start_sig = "override void Start(ActionData action_data)"
            translate(_wrap(start_sig, method_body(hold_src, start_sig)), start_sig, act_ns)
            return act

        def _build_client_census():
            c_ns: dict[str, Any] = {
                "GetGame": lambda: game,
                "PlayerBase": _NS(Cast=lambda x: x),
                "ActionManagerClient": _NS(Cast=lambda x: x),
                "MCPCarDrive": _NS(s_Active=False, Clear=lambda: None),
                "MCPInputTriggerControl": _NS(IsHeld=lambda: False, ReleaseAll=lambda _c: None),
                "MCPPlayerMoveControl": _NS(
                    IsActive=lambda: False,
                    ServerReleasePending=lambda: False,
                    Generation=lambda: 7,
                    ReleaseAll=lambda _c: None,
                ),
                "MCPWeaponControl": _NS(IsBusy=lambda: False, CloseRequestFloor=lambda: 4),
                "MCPLookAtControl": _NS(
                    IsActive=lambda: False,
                    ServerReleasePending=lambda: False,
                    Generation=lambda: 3,
                    CloseFloor=lambda: 3,
                ),
                "MCPHoldControl": hold_ctl,
                "MCPVehicleTrace": _NS(IsActive=lambda: False, Abort=lambda _c: None),
                "MCPAnimTimeline": _NS(IsActive=lambda: False, Abort=lambda _c: None),
                "MCPPlayerTrace": _NS(IsActive=lambda: False, Abort=lambda _c: None),
                "MCPLeaseCloseCensus": _CloseCensus,
                "MCPLeaseCloseEntry": _CloseEntry,
                "MCPLeaseCloseFloor": _CloseFloor,
                "m_CameraPath": _NS(IsPlaying=lambda: False),
                "m_ActiveCam": None,
                "m_CameraHandoffPending": False,
                "m_PlayerSimulationDisabled": False,
                "m_ControlsSuppressed": False,
                "m_Dialog": None,
                "m_UiPreviewRoot": None,
                "m_JobRunner": _NS(CountExcluding=lambda _k: 0, GetElapsedS=lambda: 0.0),
                "m_MCPEmoteStarted": False,
                "m_MCPPauseQueued": False,
                "m_MCPPauseMenu": None,
            }
            for sig in (
                "protected void AddCloseEntry(MCPLeaseCloseCensus census, string name, bool active, string reason)",
                "protected void AddCloseDebt(MCPLeaseCloseCensus census, string name, bool active, string reason)",
                "protected bool LeaseCloseCensusClean(MCPLeaseCloseCensus census)",
                "protected bool LeaseCloseCensusSettled(MCPLeaseCloseCensus census)",
                "protected MCPLeaseCloseCensus BuildLeaseCloseCensus(MCPArgs closeArgs)",
            ):
                translate(
                    _wrap(sig, method_body(client_src, sig)),
                    sig,
                    c_ns,
                    class_types=(
                        "MCPLeaseCloseCensus",
                        "MCPLeaseCloseEntry",
                        "PlayerBase",
                        "ActionManagerClient",
                        "EmoteManager",
                        "MissionGameplay",
                        "UIManager",
                    ),
                    members=(
                        "m_CameraPath",
                        "m_ActiveCam",
                        "m_CameraHandoffPending",
                        "m_PlayerSimulationDisabled",
                        "m_ControlsSuppressed",
                        "m_Dialog",
                        "m_UiPreviewRoot",
                        "m_JobRunner",
                        "m_MCPEmoteStarted",
                        "m_MCPPauseQueued",
                        "m_MCPPauseMenu",
                    ),
                )
            args = _NS(close_generation=EnforceString.of("g1"), close_lease_id=EnforceString.of("l1"))
            census = c_ns["BuildLeaseCloseCensus"](args)
            entries = {e.name.text: e.state.text for e in census.entries}
            return census, entries, c_ns["LeaseCloseCensusSettled"](census)

        return hold_ns, amc_ns, mgr, player, env, _make_action, _build_client_census

    def test_action_start_and_end_input_debt_by_type_translated(self):
        UA_START = 10
        SJ_ACTION_ACK_ACCEPT = 6
        SJ_ACTION_ACK_REJECT = 7

        def _make_harness():
            return self._make_action_input_harness()

        # 1. Baseline + NEG (SetupAction rejected, CanStoreInputUserData false, local instant action):
        #    none reach ctx.Send(), InputInFlight() stays False, and client census stays clean.
        h1, amc1, mgr1, player1, env1, mk_act1, census_fn1 = _make_harness()
        self.assertFalse(h1["InputInFlight"]())
        c0, e0, settled0 = census_fn1()
        self.assertEqual((c0.state.text, e0["action_input_in_flight"], settled0), ("clean", "clean", True))

        env1["mcp_use"](mk_act1(setup_ok=False, use_ack=True))
        env1["mcp_use"](mk_act1(setup_ok=False, use_ack=False))
        env1["mcp_use"](mk_act1(setup_ok=False, use_ack=False, end_on_refused=True))
        env1["can_store"] = False
        env1["mcp_use"](mk_act1(setup_ok=True, use_ack=True))
        env1["can_store"] = True
        env1["mcp_use"](mk_act1(setup_ok=True, use_ack=True, drop_store_in_can=True))
        env1["can_store"] = True
        local_res = env1["mcp_use"](mk_act1(setup_ok=True, use_ack=False, is_local=True, is_instant=True))
        self.assertTrue(local_res.ok)
        self.assertTrue(local_res.started)
        self.assertEqual(env1["sends"], [])
        self.assertFalse(h1["InputInFlight"]())
        c_neg, e_neg, _ = census_fn1()
        self.assertEqual((c_neg.state.text, e_neg["action_input_in_flight"]), ("clean", "clean"))

        # 2. POS/NEG — START with ack (action_hold): records (manager, ackId), wrong ack id leaves
        #    debt True, matching SJ_ACTION_ACK_ACCEPT clears only the START ack row without clearing
        #    the hold fence, and MCP_HoldLocalEnd adds no debt.
        hold_act = mk_act1(setup_ok=True, use_ack=True)
        env1["mcp_hold"](hold_act, hold_id="hold-ack", timeout_s=5.0)
        self.assertEqual(env1["sends"], [("START_ACK", 2)])
        self.assertTrue(h1["InputInFlight"]())
        c_ack, e_ack, settled_ack = census_fn1()
        self.assertEqual(e_ack["action_input_in_flight"], "unverified")
        self.assertEqual(e_ack["action_hold"], "residue")
        self.assertEqual(c_ack.state.text, "residue")
        self.assertFalse(settled_ack)

        # NEG: ack for a different id leaves m_State pending and debt True.
        mgr1.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 99)
        self.assertTrue(h1["InputInFlight"]())
        _, e_wrong, _ = census_fn1()
        self.assertEqual(e_wrong["action_input_in_flight"], "unverified")

        # POS: matching SJ_ACTION_ACK_ACCEPT liquidates the START ack row while hold stays active.
        mgr1.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        self.assertFalse(h1["InputInFlight"]())
        self.assertTrue(h1["IsActive"]())
        _, e_after_ack, _ = census_fn1()
        self.assertEqual(e_after_ack["action_input_in_flight"], "clean")
        self.assertEqual(e_after_ack["action_hold"], "residue")

        # MCP_HoldLocalEnd (both UA_AM_ACCEPTED -> OnActionEnd and UA_START -> ActionBase.Interrupt)
        # sends nothing to the server and adds no debt.
        mgr1.MCP_HoldLocalEnd(mgr1.MCP_HoldCurrent())
        self.assertIsNotNone(h1["Take"]())
        self.assertEqual(env1["sends"], [("START_ACK", 2)])
        self.assertFalse(h1["InputInFlight"]())

        # Second START with ack (action_use): matching SJ_ACTION_ACK_REJECT also liquidates its row.
        env1["mcp_use"](mk_act1(setup_ok=True, use_ack=True))
        self.assertEqual(env1["sends"][-1], ("START_ACK", 3))
        self.assertTrue(h1["InputInFlight"]())
        mgr1.OnSyncJuncture(SJ_ACTION_ACK_REJECT, 2)
        self.assertTrue(h1["InputInFlight"]())
        mgr1.OnSyncJuncture(SJ_ACTION_ACK_REJECT, 3)
        self.assertFalse(h1["InputInFlight"]())
        mgr1.OnActionEnd()
        c_clean_again, e_clean_again, settled_clean_again = census_fn1()
        self.assertEqual(
            (c_clean_again.state.text, e_clean_again["action_input_in_flight"], settled_clean_again),
            ("clean", "clean", True),
        )

        # 3. POS — START without ack (UseAcknowledgment() false, instant action):
        #    leaves permanent debt even after OnActionEnd clears m_CurrentActionData.
        env1["mcp_use"](mk_act1(setup_ok=True, use_ack=False, is_instant=True))
        self.assertEqual(env1["sends"][-1], ("START_NO_ACK", None))
        self.assertIsNone(mgr1.MCP_HoldCurrent())
        self.assertTrue(h1["InputInFlight"]())
        mgr1.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 3)
        self.assertTrue(h1["InputInFlight"]())
        c_no_ack, e_no_ack, settled_no_ack = census_fn1()
        self.assertEqual(
            (c_no_ack.state.text, e_no_ack["action_input_in_flight"], settled_no_ack),
            ("residue", "unverified", True),
        )

        # 4. NEG — Hold timeout EndActionInput() when ProcessActionInputEnd cannot send
        #    (CanStoreInputUserData false) and no interrupt is sent leaves no permanent END debt.
        h2, _amc2, mgr2, _player2, env2, mk_act2, census_fn2 = _make_harness()
        act2 = mk_act2(setup_ok=True, use_ack=True)
        env2["mcp_hold"](act2, hold_id="hold-end-neg", timeout_s=2.0)
        mgr2.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgr2.MCP_HoldCurrent().m_State = UA_START
        self.assertFalse(h2["InputInFlight"]())
        env2["now"] = 12.0
        env2["can_store"] = False
        h2["Maintain"]()
        mgr2.ProcessActionInputEnd()
        self.assertEqual(env2["sends"], [("START_ACK", 2)])
        env2["now"] = 18.0
        h2["Maintain"]()
        self.assertIsNotNone(h2["Take"]())
        self.assertFalse(h2["s_InterruptPending"])
        self.assertFalse(h2["InputInFlight"]())
        c_end_neg, e_end_neg, _ = census_fn2()
        self.assertEqual((c_end_neg.state.text, e_end_neg["action_input_in_flight"]), ("clean", "clean"))

        # 5. POS — Hold timeout END: Maintain() arms EndActionInput() and ProcessActionInputEnd()
        #    sends INPUT_UDT_STANDARD_ACTION_INPUT_END; debt stays True permanently even when no
        #    interrupt is sent and after drain expiry, Take(), and a later Begin().
        h3, _amc3, mgr3, player3, env3, mk_act3, census_fn3 = _make_harness()
        act3 = mk_act3(setup_ok=True, use_ack=True)
        env3["mcp_hold"](act3, hold_id="hold-end-pos", timeout_s=2.0)
        mgr3.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgr3.MCP_HoldCurrent().m_State = UA_START
        self.assertFalse(h3["InputInFlight"]())
        env3["now"] = 12.0
        h3["Maintain"]()
        self.assertNotIn(("INPUT_END", None), env3["sends"])
        mgr3.ProcessActionInputEnd()
        self.assertEqual(env3["sends"], [("START_ACK", 2), ("INPUT_END", None)])
        # Block interrupt channel through force-drain expiry so Maintain() ends locally via
        # MCP_HoldLocalEnd and clears s_InterruptPending without sending an interrupt.
        env3["can_store"] = False
        env3["now"] = 18.0
        h3["Maintain"]()
        self.assertIsNotNone(h3["Take"]())
        self.assertFalse(h3["IsActive"]())
        self.assertFalse(h3["s_InterruptSent"])
        self.assertFalse(h3["s_InterruptPending"])
        self.assertTrue(h3["InputInFlight"]())
        c_end_pos, e_end_pos, settled_end_pos = census_fn3()
        self.assertEqual(
            (c_end_pos.state.text, e_end_pos["action_input_in_flight"], settled_end_pos),
            ("residue", "unverified", True),
        )
        h3["Begin"](mgr3, player3, act3, EnforceString.of("hold-after-end"), 2.0)
        h3["NoteSetupFailed"]()
        self.assertIsNotNone(h3["Take"]())
        self.assertTrue(h3["InputInFlight"]())

        # 6. POS — Unacknowledged START promotes to s_StartDebt on OnManagerDestroyed, and
        #    RequestEndAction() -> ProcessActionRequestEnd() wire send sets permanent s_EndDebt.
        h4, _amc4, mgr4, _player4, env4, mk_act4, _census_fn4 = _make_harness()
        env4["mcp_use"](mk_act4(setup_ok=True, use_ack=True))
        self.assertEqual(env4["sends"], [("START_ACK", 2)])
        self.assertFalse(h4["s_StartDebt"])
        self.assertEqual(h4["s_AckIds"].Count(), 1)
        h4["OnManagerDestroyed"](mgr4)
        self.assertEqual(h4["s_AckIds"].Count(), 0)
        self.assertTrue(h4["s_StartDebt"])
        self.assertTrue(h4["InputInFlight"]())

        h5, _amc5, mgr5, _player5, env5, mk_act5, _census_fn5 = _make_harness()
        env5["mcp_use"](mk_act5(setup_ok=True, use_ack=True))
        mgr5.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        self.assertFalse(h5["InputInFlight"]())
        mgr5.RequestEndAction()
        self.assertFalse(h5["InputInFlight"]())
        mgr5.ProcessActionRequestEnd()
        self.assertEqual(env5["sends"], [("START_ACK", 2), ("END_REQUEST", None)])
        self.assertTrue(h5["s_EndDebt"])
        self.assertTrue(h5["InputInFlight"]())

    def test_vanilla_interrupt_after_ack_and_manager_interrupt_translated(self):
        UA_START = 10
        SJ_ACTION_ACK_ACCEPT = 6
        SJ_ACTION_INTERRUPT = 2
        CMD_ACTIONINT_INTERRUPT = 2

        # 1a. POS — action_use START with ack -> matching SJ_ACTION_ACK_ACCEPT settles the
        #     START row -> real Update() with m_ActionPossible false and channel free
        #     (single-slot buffer flips CanStoreInputUserData false inside ctx.Send()):
        #     RequestInterruptAction() sends CMD_ACTIONINT_INTERRUPT, OnActionEnd() leaves
        #     manager idle, s_InterruptDebt stays True permanently, and client census is
        #     action_input_in_flight: unverified.
        h1, _amc1, mgr1, player1, env1, mk_act1, census_fn1 = self._make_action_input_harness()
        env1["mcp_use"](mk_act1(setup_ok=True, use_ack=True))
        self.assertEqual(env1["sends"], [("START_ACK", 2)])
        mgr1.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        self.assertFalse(h1["InputInFlight"]())

        env1["possible"] = False
        env1["single_slot"] = True
        mgr1.Update()
        self.assertFalse(env1["can_store"])
        self.assertEqual(
            env1["sends"],
            [("START_ACK", 2), ("INTERRUPT", CMD_ACTIONINT_INTERRUPT)],
        )
        self.assertFalse(mgr1.MCP_HoldBusy())
        self.assertIsNone(mgr1.MCP_HoldCurrent())
        self.assertEqual(mgr1.MCP_PendingAckId(), -1)
        self.assertTrue(h1["InputInFlight"]())
        self.assertTrue(h1["s_InterruptDebt"])
        self.assertFalse(h1["s_StartDebt"])
        self.assertFalse(h1["s_EndDebt"])
        self.assertEqual(h1["s_StartedData"].Count(), 0)
        c1, e1, settled1 = census_fn1()
        self.assertEqual(
            (c1.state.text, e1["action_hold"], e1["action_input_in_flight"], settled1),
            ("residue", "clean", "unverified", True),
        )
        env1["can_store"] = True
        mgr1.Update()
        h1["Begin"](mgr1, player1, mk_act1(setup_ok=True, use_ack=True), EnforceString.of("hold-after-int"), 2.0)
        h1["NoteSetupFailed"]()
        self.assertIsNotNone(h1["Take"]())
        self.assertTrue(h1["InputInFlight"]())

        # 1b. POS — Same Update() UA_AM_ACCEPTED branch when can_be_action_done
        #     (ComputeConditionMask check, actionmanagerclient.c:77-82) is false.
        h1b, _amc1b, mgr1b, _player1b, env1b, mk_act1b, _census_fn1b = self._make_action_input_harness()
        env1b["mcp_use"](mk_act1b(setup_ok=True, use_ack=True))
        self.assertTrue(h1b["InputInFlight"]())
        mgr1b.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        self.assertFalse(h1b["InputInFlight"]())
        env1b["condition_ok"] = False
        mgr1b.Update()
        self.assertEqual(
            env1b["sends"],
            [("START_ACK", 2), ("INTERRUPT", CMD_ACTIONINT_INTERRUPT)],
        )
        self.assertFalse(mgr1b.MCP_HoldBusy())
        self.assertTrue(h1b["InputInFlight"]())

        # 2a. POS — ActionManagerBase.Interrupt() (actionmanagerbase.c:302-315) on an
        #     accepted and running MCP-started continuous action records permanent debt.
        h2, _amc2, mgr2, _player2, env2, mk_act2, census_fn2 = self._make_action_input_harness()
        env2["mcp_use"](mk_act2(setup_ok=True, use_ack=True, is_instant=False))
        mgr2.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgr2.Update()
        self.assertEqual(mgr2.MCP_HoldCurrent().m_State, UA_START)
        self.assertEqual(env2["sends"], [("START_ACK", 2)])
        self.assertFalse(h2["InputInFlight"]())

        mgr2.Interrupt()
        self.assertEqual(
            env2["sends"],
            [("START_ACK", 2), ("INTERRUPT", CMD_ACTIONINT_INTERRUPT)],
        )
        self.assertTrue(h2["InputInFlight"]())
        mgr2.OnSyncJuncture(SJ_ACTION_INTERRUPT, 0)
        mgr2.Update()
        self.assertFalse(mgr2.MCP_HoldBusy())
        self.assertIsNone(mgr2.MCP_HoldCurrent())
        self.assertEqual(h2["s_StartedData"].Count(), 0)
        self.assertTrue(h2["InputInFlight"]())
        c2, e2, settled2 = census_fn2()
        self.assertEqual(
            (c2.state.text, e2["action_hold"], e2["action_input_in_flight"], settled2),
            ("residue", "clean", "unverified", True),
        )

        # 2b. POS — Existing three MCPHoldControl interrupt sites (Cancel -> TransmitInterrupt,
        #     Shutdown, RequestForceClose, plus timeout drain) each send strictly one interrupt
        #     packet (no double send) and preserve s_InterruptDebt.
        for mode in ("cancel", "shutdown", "force_close", "timeout_drain"):
            with self.subTest(mode=mode):
                hm, _amcm, mgrm, _playerm, envm, mk_actm, _census_m = self._make_action_input_harness()
                actm = mk_actm(setup_ok=True, use_ack=True, is_instant=False)
                envm["mcp_hold"](actm, hold_id=f"hold-{mode}", timeout_s=2.0)
                mgrm.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
                mgrm.Update()
                self.assertFalse(hm["InputInFlight"]())
                if mode == "cancel":
                    self.assertTrue(hm["Cancel"](EnforceString.of("hold-cancel")))
                    mgrm.MCP_HoldLocalEnd(mgrm.MCP_HoldCurrent())
                elif mode == "shutdown":
                    hm["Shutdown"](EnforceString.of("shutdown"))
                elif mode == "force_close":
                    envm["now"] = 12.5
                    hm["RequestForceClose"]()
                else:
                    envm["now"] = 12.0
                    hm["Maintain"]()
                    envm["now"] = 13.5
                    hm["Maintain"]()
                    envm["now"] = 18.0
                    hm["Maintain"]()
                self.assertIsNotNone(hm["Take"]())
                interrupt_sends = [s for s in envm["sends"] if s[0] == "INTERRUPT"]
                self.assertEqual(interrupt_sends, [("INTERRUPT", CMD_ACTIONINT_INTERRUPT)])
                self.assertTrue(hm["InputInFlight"]())

        # 3. NEG — Channel busy (CanStoreInputUserData false): neither Update() after
        #    SJ_ACTION_ACK_ACCEPT nor ActionManagerBase.Interrupt() sends or adds debt.
        h3, _amc3, mgr3, _player3, env3, mk_act3, census_fn3 = self._make_action_input_harness()
        env3["mcp_use"](mk_act3(setup_ok=True, use_ack=True))
        mgr3.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        self.assertFalse(h3["InputInFlight"]())
        env3["possible"] = False
        env3["can_store"] = False
        mgr3.Update()
        self.assertEqual(env3["sends"], [("START_ACK", 2)])
        self.assertFalse(mgr3.MCP_HoldBusy())
        self.assertIsNone(mgr3.MCP_HoldCurrent())
        self.assertFalse(h3["InputInFlight"]())
        self.assertFalse(h3["s_InterruptDebt"])
        c3, e3, settled3 = census_fn3()
        self.assertEqual(
            (c3.state.text, e3["action_hold"], e3["action_input_in_flight"], settled3),
            ("clean", "clean", "clean", True),
        )

        h3b, _amc3b, mgr3b, _player3b, env3b, mk_act3b, _census_fn3b = self._make_action_input_harness()
        env3b["mcp_use"](mk_act3b(setup_ok=True, use_ack=True, is_instant=False))
        mgr3b.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgr3b.Update()
        env3b["can_store"] = False
        mgr3b.Interrupt()
        mgr3b.OnActionEnd()
        self.assertEqual(env3b["sends"], [("START_ACK", 2)])
        self.assertFalse(h3b["InputInFlight"]())

        # 4. NEG — Action NOT started by MCP (vanilla player/quickbar/inventory ActionStart
        #    or radial PerformActionStart) with channel free: vanilla sends
        #    CMD_ACTIONINT_INTERRUPT, but MCP adds no debt.
        h4, _amc4, mgr4, _player4, env4, mk_act4, census_fn4 = self._make_action_input_harness()
        env4["mcp_use"](mk_act4(setup_ok=True, use_ack=True, is_instant=True))
        mgr4.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgr4.Update()
        self.assertFalse(mgr4.MCP_HoldBusy())
        self.assertFalse(h4["InputInFlight"]())
        self.assertEqual(h4["s_StartedData"].Count(), 0)

        mgr4.ActionStart(mk_act4(setup_ok=True, use_ack=True), None, None)
        self.assertEqual(env4["sends"][-1], ("START_ACK", 3))
        mgr4.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 3)
        env4["possible"] = False
        mgr4.Update()
        self.assertEqual(env4["sends"][-1], ("INTERRUPT", CMD_ACTIONINT_INTERRUPT))
        self.assertFalse(mgr4.MCP_HoldBusy())
        self.assertFalse(h4["InputInFlight"]())
        self.assertFalse(h4["s_InterruptDebt"])
        c4, e4, settled4 = census_fn4()
        self.assertEqual(
            (c4.state.text, e4["action_hold"], e4["action_input_in_flight"], settled4),
            ("clean", "clean", "clean", True),
        )

        # Foreign running action + un-armed PerformActionStart + foreign Interrupt() + idle RequestInterruptAction().
        env4["possible"] = True
        mgr4.ActionStart(mk_act4(setup_ok=True, use_ack=True, is_instant=False), None, None)
        mgr4.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 4)
        mgr4.Update()
        mgr4.PerformActionStart(mk_act4(setup_ok=True, use_ack=True), None, None)
        mgr4.Interrupt()
        mgr4.OnActionEnd()
        mgr4.RequestInterruptAction()
        self.assertEqual(env4["sends"][-1], ("INTERRUPT", CMD_ACTIONINT_INTERRUPT))
        self.assertFalse(h4["InputInFlight"]())

        # 5. In-memory mutants verify both negative guards in AfterRequestInterrupt are load-bearing.
        base_hold_src = _read(SCRIPTS.parent / "4_World" / "MCP_ActionHold.c")
        mut_busy_src = base_hold_src.replace("if (!canStore || !manager)", "if (!manager)", 1)
        self.assertNotEqual(mut_busy_src, base_hold_src)
        hm_busy, _ab, mgrm_busy, _pb, envm_busy, mkm_busy, _cb = self._make_action_input_harness(mut_busy_src)
        envm_busy["mcp_use"](mkm_busy(setup_ok=True, use_ack=True))
        mgrm_busy.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        envm_busy["possible"] = False
        envm_busy["can_store"] = False
        mgrm_busy.Update()
        self.assertTrue(hm_busy["InputInFlight"]())

        mut_foreign_src = base_hold_src.replace("if (!IsStartedAction(manager, beforeData))", "if (false)", 1)
        self.assertNotEqual(mut_foreign_src, base_hold_src)
        hm_for, _af, mgrm_for, _pf, envm_for, mkm_for, _cf = self._make_action_input_harness(mut_foreign_src)
        mgrm_for.ActionStart(mkm_for(setup_ok=True, use_ack=True), None, None)
        mgrm_for.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        envm_for["possible"] = False
        mgrm_for.Update()
        self.assertTrue(hm_for["InputInFlight"]())

    def test_animated_callback_interrupt_debt_translated(self):
        UA_PROCESSING = 12
        SJ_ACTION_ACK_ACCEPT = 6
        CMD_ACTIONINT_INTERRUPT = 2

        # 1a. POS — MCP action_hold on an animated continuous action receives matching
        #     SJ_ACTION_ACK_ACCEPT (clearing the START ack row), creates a real ActionBaseCB
        #     callback, and loses CanContinue before hold timeout. Vanilla AnimatedActionBase.Do
        #     (:426) calls action_data.m_Callback.Interrupt() (:85-111) directly without calling
        #     ActionManagerClient.RequestInterruptAction(). When OnFinish(true) (:27-54) ends
        #     the action and Take() consumes the hold observation, s_InterruptDebt remains True
        #     permanently and the client census reports action_input_in_flight: unverified.
        h1, _amc1, mgr1, player1, env1, mk_act1, census_fn1 = self._make_action_input_harness()
        act1 = mk_act1(setup_ok=True, use_ack=True, is_instant=False, animated=True, can_continue=True)
        env1["mcp_hold"](act1, hold_id="hold-cb-int", timeout_s=10.0)
        self.assertEqual(env1["sends"], [("START_ACK", 2)])

        mgr1.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgr1.Update()
        data1 = mgr1.MCP_HoldCurrent()
        self.assertIsNotNone(data1)
        self.assertIsNotNone(data1.m_Callback)
        self.assertFalse(h1["InputInFlight"]())

        # First Do(UA_PROCESSING) with CanContinue == True progresses without sending.
        act1.Do(data1, UA_PROCESSING)
        self.assertEqual(env1["sends"], [("START_ACK", 2)])
        self.assertFalse(h1["InputInFlight"]())

        # Lose CanContinue before timeout; single_slot flips CanStoreInputUserData false inside Send().
        act1.can_continue = False
        env1["single_slot"] = True
        act1.Do(data1, UA_PROCESSING)
        self.assertFalse(env1["can_store"])
        self.assertTrue(data1.m_Callback.m_Interrupted)
        self.assertEqual(
            env1["sends"],
            [("START_ACK", 2), ("CB_INTERRUPT", CMD_ACTIONINT_INTERRUPT)],
        )

        # Animation completes -> OnFinish(true) -> action.End -> OnActionEnd -> Take().
        data1.m_Callback.OnFinish(True)
        obs1 = h1["Take"]()
        self.assertIsNotNone(obs1)
        self.assertEqual((obs1.end_state.text, obs1.reason.text), ("cancel", "interrupted"))
        self.assertFalse(h1["IsActive"]())
        self.assertFalse(h1["s_InterruptSent"])
        self.assertFalse(h1["s_InterruptPending"])
        self.assertFalse(mgr1.MCP_HoldBusy())
        self.assertTrue(h1["InputInFlight"]())
        self.assertTrue(h1["s_InterruptDebt"])
        c1, e1, settled1 = census_fn1()
        self.assertEqual(
            (c1.state.text, e1["action_hold"], e1["action_input_in_flight"], settled1),
            ("residue", "clean", "unverified", True),
        )
        env1["can_store"] = True
        h1["Begin"](mgr1, player1, act1, EnforceString.of("hold-after-cb"), 2.0)
        h1["NoteSetupFailed"]()
        self.assertIsNotNone(h1["Take"]())
        self.assertTrue(h1["InputInFlight"]())

        # 1b. POS — MCP action_use animated action callback interrupt also records s_InterruptDebt,
        #     and Shutdown on an animated hold cancels the callback without a second wire send.
        h1b, _amc1b, mgr1b, _player1b, env1b, mk_act1b, census_fn1b = self._make_action_input_harness()
        act1b = mk_act1b(setup_ok=True, use_ack=True, is_instant=False, animated=True, can_continue=False)
        env1b["mcp_use"](act1b)
        mgr1b.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgr1b.Update()
        cb1b = mgr1b.MCP_HoldCurrent().m_Callback
        act1b.Do(mgr1b.MCP_HoldCurrent(), UA_PROCESSING)
        cb1b.OnFinish(True)
        self.assertFalse(mgr1b.MCP_HoldBusy())
        self.assertTrue(h1b["InputInFlight"]())
        c1b, e1b, _ = census_fn1b()
        self.assertEqual((c1b.state.text, e1b["action_input_in_flight"]), ("residue", "unverified"))

        h1c, _amc1c, mgr1c, _player1c, env1c, mk_act1c, _census_fn1c = self._make_action_input_harness()
        act1c = mk_act1c(setup_ok=True, use_ack=True, is_instant=False, animated=True, can_continue=True)
        env1c["mcp_hold"](act1c, hold_id="hold-anim-shutdown", timeout_s=10.0)
        mgr1c.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgr1c.Update()
        cb1c = mgr1c.MCP_HoldCurrent().m_Callback
        h1c["Shutdown"](EnforceString.of("shutdown"))
        self.assertTrue(cb1c.m_Canceled)
        cb1c.OnFinish(True)
        self.assertIsNotNone(h1c["Take"]())
        self.assertEqual(
            env1c["sends"],
            [("START_ACK", 2), ("INTERRUPT", CMD_ACTIONINT_INTERRUPT)],
        )
        self.assertTrue(h1c["InputInFlight"]())

        # 1d. POS — Lingering callback whose Interrupt() fires after OnActionEnd() pruned
        #     s_StartedData and Take() closed the hold (e.g. animatedactionbase.c:500) still
        #     records s_InterruptDebt via m_MCPOwned captured in SetActionData.
        h1d, _amc1d, mgr1d, _player1d, env1d, mk_act1d, _census_fn1d = self._make_action_input_harness()
        act1d = mk_act1d(setup_ok=True, use_ack=True, is_instant=False, animated=True, can_continue=True)
        env1d["mcp_hold"](act1d, hold_id="hold-cb-late", timeout_s=10.0)
        mgr1d.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgr1d.Update()
        cb1d = mgr1d.MCP_HoldCurrent().m_Callback
        mgr1d.OnActionEnd()
        self.assertIsNotNone(h1d["Take"]())
        self.assertEqual(h1d["s_StartedData"].Count(), 0)
        self.assertFalse(h1d["InputInFlight"]())
        cb1d.Interrupt()
        self.assertEqual(
            env1d["sends"],
            [("START_ACK", 2), ("CB_INTERRUPT", CMD_ACTIONINT_INTERRUPT)],
        )
        self.assertTrue(h1d["InputInFlight"]())

        # 2a. NEG — Channel busy (CanStoreInputUserData false): ActionBaseCB.Interrupt() sets
        #     m_Interrupted without sending on the wire; after OnFinish + Take(), debt is False.
        h2, _amc2, mgr2, _player2, env2, mk_act2, census_fn2 = self._make_action_input_harness()
        act2 = mk_act2(setup_ok=True, use_ack=True, is_instant=False, animated=True, can_continue=False)
        env2["mcp_hold"](act2, hold_id="hold-cb-busy", timeout_s=10.0)
        mgr2.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgr2.Update()
        data2 = mgr2.MCP_HoldCurrent()
        env2["can_store"] = False
        act2.Do(data2, UA_PROCESSING)
        self.assertTrue(data2.m_Callback.m_Interrupted)
        data2.m_Callback.OnFinish(True)
        self.assertIsNotNone(h2["Take"]())
        self.assertEqual(env2["sends"], [("START_ACK", 2)])
        self.assertFalse(h2["InputInFlight"]())
        self.assertFalse(h2["s_InterruptDebt"])
        c2, e2, settled2 = census_fn2()
        self.assertEqual(
            (c2.state.text, e2["action_hold"], e2["action_input_in_flight"], settled2),
            ("clean", "clean", "clean", True),
        )

        # 2b. NEG — Server side (GetGame().IsServer() true): ActionBaseCB.Interrupt() sends
        #     SendActionInterrupt sync juncture, not client ScriptInputUserData; no client debt.
        h2s, _amc2s, mgr2s, _player2s, env2s, mk_act2s, _census_fn2s = self._make_action_input_harness()
        act2s = mk_act2s(setup_ok=True, use_ack=True, is_instant=False, animated=True, can_continue=False)
        env2s["mcp_hold"](act2s, hold_id="hold-cb-srv", timeout_s=10.0)
        mgr2s.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgr2s.Update()
        data2s = mgr2s.MCP_HoldCurrent()
        env2s["is_server"] = True
        act2s.Do(data2s, UA_PROCESSING)
        data2s.m_Callback.OnFinish(True)
        self.assertIsNotNone(h2s["Take"]())
        self.assertEqual(env2s["sends"], [("START_ACK", 2)])
        self.assertEqual(len(env2s["server_junctures"]), 1)
        self.assertFalse(h2s["InputInFlight"]())

        # 2c. NEG — Foreign/manual animated actions (both ActionStart and PerformActionStart):
        #     callback sends CB_INTERRUPT on the wire, OnFinish ends the action, zero MCP debt.
        for starter in ("ActionStart", "PerformActionStart"):
            with self.subTest(starter=starter):
                hf, _amcf, mgrf, _playerf, envf, mk_actf, census_fnf = self._make_action_input_harness()
                actf = mk_actf(setup_ok=True, use_ack=True, is_instant=False, animated=True, can_continue=False)
                getattr(mgrf, starter)(actf, None, None)
                mgrf.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
                mgrf.Update()
                dataf = mgrf.MCP_HoldCurrent()
                actf.Do(dataf, UA_PROCESSING)
                dataf.m_Callback.OnFinish(True)
                self.assertEqual(
                    envf["sends"],
                    [("START_ACK", 2), ("CB_INTERRUPT", CMD_ACTIONINT_INTERRUPT)],
                )
                self.assertFalse(mgrf.MCP_HoldBusy())
                self.assertFalse(hf["InputInFlight"]())
                cf, ef, settledf = census_fnf()
                self.assertEqual(
                    (cf.state.text, ef["action_hold"], ef["action_input_in_flight"], settledf),
                    ("clean", "clean", "clean", True),
                )

        # 3. In-memory mutants prove !canSend, !mcpOwned, and pre-super CanStoreInputUserData sampling.
        base_hold_src = _read(SCRIPTS.parent / "4_World" / "MCP_ActionHold.c")
        mut_no_cansend = base_hold_src.replace("if (!mcpOwned || !canSend)", "if (!mcpOwned)", 1)
        self.assertNotEqual(mut_no_cansend, base_hold_src)
        hm1, _, mgrm1, _, envm1, mkm1, _ = self._make_action_input_harness(mut_no_cansend)
        actm1 = mkm1(setup_ok=True, use_ack=True, is_instant=False, animated=True, can_continue=False)
        envm1["mcp_hold"](actm1, hold_id="m1", timeout_s=10.0)
        mgrm1.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgrm1.Update()
        envm1["can_store"] = False
        actm1.Do(mgrm1.MCP_HoldCurrent(), UA_PROCESSING)
        mgrm1.MCP_HoldCurrent().m_Callback.OnFinish(True)
        hm1["Take"]()
        self.assertTrue(hm1["InputInFlight"]())

        mut_no_owned = base_hold_src.replace("if (!mcpOwned || !canSend)", "if (!canSend)", 1)
        self.assertNotEqual(mut_no_owned, base_hold_src)
        hm2, _, mgrm2, _, _envm2, mkm2, _ = self._make_action_input_harness(mut_no_owned)
        actm2 = mkm2(setup_ok=True, use_ack=True, is_instant=False, animated=True, can_continue=False)
        mgrm2.PerformActionStart(actm2, None, None)
        mgrm2.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgrm2.Update()
        actm2.Do(mgrm2.MCP_HoldCurrent(), UA_PROCESSING)
        mgrm2.MCP_HoldCurrent().m_Callback.OnFinish(True)
        self.assertTrue(hm2["InputInFlight"]())

        mut_after_super = base_hold_src.replace(
            "\t\tsuper.Interrupt();\n\t\tMCPHoldControl.AfterCallbackInterrupt(mcpOwned, canSend);",
            "\t\tsuper.Interrupt();\n\t\tcanSend = ScriptInputUserData.CanStoreInputUserData();\n"
            "\t\tMCPHoldControl.AfterCallbackInterrupt(mcpOwned, canSend);",
            1,
        )
        self.assertNotEqual(mut_after_super, base_hold_src)
        hm3, _, mgrm3, _, envm3, mkm3, _ = self._make_action_input_harness(mut_after_super)
        actm3 = mkm3(setup_ok=True, use_ack=True, is_instant=False, animated=True, can_continue=False)
        envm3["mcp_hold"](actm3, hold_id="m3", timeout_s=10.0)
        mgrm3.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgrm3.Update()
        envm3["single_slot"] = True
        actm3.Do(mgrm3.MCP_HoldCurrent(), UA_PROCESSING)
        mgrm3.MCP_HoldCurrent().m_Callback.OnFinish(True)
        hm3["Take"]()
        self.assertFalse(hm3["InputInFlight"]())

    def test_manual_vs_mcp_action_attribution_translated(self):
        UA_PROCESSING = 12
        SJ_ACTION_ACK_ACCEPT = 6
        CMD_ACTIONINT_INTERRUPT = 2

        # 1. NEG — Manual player actions started BOTH by ActionStart (inputs/quickbar/inventory)
        #    AND by PerformActionStart (e.g. RadialQuickbarMenu flashlight/NVG at
        #    radialquickbarmenu.c:763, 793) never create START rows, s_StartedData entries,
        #    START debt, END debt (ProcessActionInputEnd and ProcessActionRequestEnd, whether
        #    or not EndInput/EndRequest ends the action), or interrupt debt (Update, Interrupt,
        #    and ActionBaseCB.Interrupt).
        for starter in ("ActionStart", "PerformActionStart"):
            with self.subTest(manual_starter=starter):
                hm, _amcm, mgrm, _playerm, envm, mk_actm, census_m = self._make_action_input_harness()

                # (a) Manual START without ack (instant) + manual START with ack.
                getattr(mgrm, starter)(mk_actm(setup_ok=True, use_ack=False, is_instant=True), None, None)
                self.assertEqual(envm["sends"][-1], ("START_NO_ACK", None))
                self.assertFalse(hm["InputInFlight"]())
                self.assertFalse(hm["s_StartDebt"])

                # (b) Manual continuous action releasing player input (InputsUpdate -> EndActionInput ->
                #     ProcessActionInputEnd), including EndInput synchronously calling OnActionEnd().
                act_in = mk_actm(setup_ok=True, use_ack=True, is_instant=False, end_on_input_end=True)
                getattr(mgrm, starter)(act_in, None, None)
                self.assertEqual(envm["sends"][-1], ("START_ACK", 2))
                self.assertFalse(hm["InputInFlight"]())
                self.assertEqual(hm["s_AckIds"].Count(), 0)
                self.assertEqual(hm["s_StartedData"].Count(), 0)
                mgrm.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
                mgrm.Update()
                mgrm.EndActionInput()
                mgrm.Update()
                self.assertEqual(envm["sends"][-1], ("INPUT_END", None))
                self.assertIsNone(mgrm.MCP_HoldCurrent())
                self.assertFalse(hm["s_EndDebt"])
                self.assertFalse(hm["InputInFlight"]())

                # (c) Manual action RequestEndAction -> ProcessActionRequestEnd (ending inside super).
                act_req = mk_actm(setup_ok=True, use_ack=True, is_instant=False, end_on_request_end=True)
                getattr(mgrm, starter)(act_req, None, None)
                mgrm.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 3)
                mgrm.Update()
                mgrm.RequestEndAction()
                mgrm.Update()
                self.assertEqual(envm["sends"][-1], ("END_REQUEST", None))
                self.assertIsNone(mgrm.MCP_HoldCurrent())
                self.assertFalse(hm["s_EndDebt"])
                self.assertFalse(hm["InputInFlight"]())

                # (d) Manual action interrupted via Update(), ActionManagerBase.Interrupt(), and
                #     animated ActionBaseCB.Interrupt(), then finished.
                getattr(mgrm, starter)(mk_actm(setup_ok=True, use_ack=True, is_instant=False), None, None)
                mgrm.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 4)
                envm["possible"] = False
                mgrm.Update()
                self.assertEqual(envm["sends"][-1], ("INTERRUPT", CMD_ACTIONINT_INTERRUPT))
                envm["possible"] = True

                getattr(mgrm, starter)(mk_actm(setup_ok=True, use_ack=True, is_instant=False), None, None)
                mgrm.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 5)
                mgrm.Update()
                mgrm.Interrupt()
                mgrm.OnActionEnd()
                self.assertEqual(envm["sends"][-1], ("INTERRUPT", CMD_ACTIONINT_INTERRUPT))

                act_cb = mk_actm(
                    setup_ok=True,
                    use_ack=True,
                    is_instant=False,
                    animated=True,
                    can_continue=False,
                )
                getattr(mgrm, starter)(act_cb, None, None)
                mgrm.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 6)
                mgrm.Update()
                data_cb = mgrm.MCP_HoldCurrent()
                act_cb.Do(data_cb, UA_PROCESSING)
                data_cb.m_Callback.OnFinish(True)
                self.assertEqual(envm["sends"][-1], ("CB_INTERRUPT", CMD_ACTIONINT_INTERRUPT))

                self.assertFalse(mgrm.MCP_HoldBusy())
                self.assertFalse(hm["s_StartDebt"])
                self.assertFalse(hm["s_EndDebt"])
                self.assertFalse(hm["s_InterruptDebt"])
                self.assertFalse(hm["InputInFlight"]())
                cm, em, settledm = census_m()
                self.assertEqual(
                    (cm.state.text, em["action_hold"], em["action_input_in_flight"], settledm),
                    ("clean", "clean", "clean", True),
                )

        # 2. POS — Equivalent MCP-dispatched actions (both action_use and action_hold via
        #    MCPClientBridge.DispatchActionUse) DO record START, END (even when EndInput or
        #    EndRequest calls OnActionEnd() inside super, pruning s_StartedData before super
        #    returns), and Interrupt debt, while a later manual PerformActionStart after a
        #    settled MCP action still leaves debt False.
        h_clean, _, mgr_clean, _, env_clean, mk_clean, census_clean = self._make_action_input_harness()
        env_clean["mcp_use"](mk_clean(setup_ok=True, use_ack=True, is_instant=True))
        self.assertTrue(h_clean["InputInFlight"]())
        mgr_clean.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgr_clean.Update()
        self.assertFalse(h_clean["InputInFlight"]())
        # Manual PerformActionStart right after an MCP start shows DisarmStart cleared capture.
        act_manual_after = mk_clean(setup_ok=True, use_ack=True, is_instant=False, end_on_input_end=True)
        mgr_clean.PerformActionStart(act_manual_after, None, None)
        mgr_clean.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 3)
        mgr_clean.Update()
        mgr_clean.EndActionInput()
        mgr_clean.Update()
        self.assertFalse(h_clean["InputInFlight"]())
        cc, ec, _ = census_clean()
        self.assertEqual((cc.state.text, ec["action_input_in_flight"]), ("clean", "clean"))

        # POS (ProcessActionInputEnd with EndInput ending the action inside super):
        hp_in, _, mgrp_in, _, envp_in, mkp_in, censusp_in = self._make_action_input_harness()
        actp_in = mkp_in(setup_ok=True, use_ack=True, is_instant=False, end_on_input_end=True)
        envp_in["mcp_use"](actp_in)
        self.assertTrue(hp_in["InputInFlight"]())
        mgrp_in.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgrp_in.Update()
        self.assertFalse(hp_in["InputInFlight"]())
        self.assertEqual(hp_in["s_StartedData"].Count(), 1)
        mgrp_in.EndActionInput()
        mgrp_in.Update()
        self.assertEqual(envp_in["sends"], [("START_ACK", 2), ("INPUT_END", None)])
        self.assertIsNone(mgrp_in.MCP_HoldCurrent())
        self.assertEqual(hp_in["s_StartedData"].Count(), 0)
        self.assertTrue(hp_in["s_EndDebt"])
        self.assertTrue(hp_in["InputInFlight"]())
        cp_in, ep_in, settled_in = censusp_in()
        self.assertEqual(
            (cp_in.state.text, ep_in["action_hold"], ep_in["action_input_in_flight"], settled_in),
            ("residue", "clean", "unverified", True),
        )

        # POS (ProcessActionRequestEnd with EndRequest ending the action inside super):
        hp_req, _, mgrp_req, _, envp_req, mkp_req, censusp_req = self._make_action_input_harness()
        actp_req = mkp_req(setup_ok=True, use_ack=True, is_instant=False, end_on_request_end=True)
        envp_req["mcp_use"](actp_req)
        mgrp_req.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgrp_req.Update()
        self.assertFalse(hp_req["InputInFlight"]())
        mgrp_req.RequestEndAction()
        mgrp_req.Update()
        self.assertEqual(envp_req["sends"], [("START_ACK", 2), ("END_REQUEST", None)])
        self.assertIsNone(mgrp_req.MCP_HoldCurrent())
        self.assertEqual(hp_req["s_StartedData"].Count(), 0)
        self.assertTrue(hp_req["s_EndDebt"])
        self.assertTrue(hp_req["InputInFlight"]())
        cp_req, ep_req, _ = censusp_req()
        self.assertEqual((cp_req.state.text, ep_req["action_input_in_flight"]), ("residue", "unverified"))

        # POS (MCP action_hold timeout END when EndInput ends the action inside super):
        hp_hold, _, mgrp_hold, _, envp_hold, mkp_hold, censusp_hold = self._make_action_input_harness()
        actp_hold = mkp_hold(setup_ok=True, use_ack=True, is_instant=False, end_on_input_end=True)
        envp_hold["mcp_hold"](actp_hold, hold_id="hold-f2-pos", timeout_s=2.0)
        mgrp_hold.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgrp_hold.Update()
        self.assertFalse(hp_hold["InputInFlight"]())
        envp_hold["now"] = 12.0
        hp_hold["Maintain"]()
        mgrp_hold.ProcessActionInputEnd()
        envp_hold["can_store"] = False
        envp_hold["now"] = 18.0
        hp_hold["Maintain"]()
        self.assertIsNotNone(hp_hold["Take"]())
        self.assertTrue(hp_hold["s_EndDebt"])
        self.assertTrue(hp_hold["InputInFlight"]())
        cp_hold, ep_hold, _ = censusp_hold()
        self.assertEqual((cp_hold.state.text, ep_hold["action_input_in_flight"]), ("residue", "unverified"))

        # 3. In-memory mutants verify F2 guards (ArmStart gating + pre-super ownership sampling).
        base_hold_src = _read(SCRIPTS.parent / "4_World" / "MCP_ActionHold.c")
        base_client_src = _read(CLIENT_PATH)

        mut_unarmed = base_hold_src.replace(
            "if (!s_MCPStartArmed || manager != s_MCPStartManager || action != s_MCPStartAction)",
            "if (false)",
            1,
        )
        self.assertNotEqual(mut_unarmed, base_hold_src)
        hmu, _, mgrmu, _, _envmu, mkmu, _ = self._make_action_input_harness(mut_unarmed)
        mgrmu.PerformActionStart(mkmu(setup_ok=True, use_ack=True, is_instant=False), None, None)
        mgrmu.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgrmu.Update()
        mgrmu.EndActionInput()
        mgrmu.Update()
        self.assertTrue(hmu["InputInFlight"]())

        mut_no_arm = base_client_src.replace("MCPHoldControl.ArmStart(amc, action);", "")
        self.assertNotEqual(mut_no_arm, base_client_src)
        hma, _, _mgrma, _, envma, mkma, _ = self._make_action_input_harness(None, mut_no_arm)
        envma["mcp_use"](mkma(setup_ok=True, use_ack=True, is_instant=False))
        self.assertFalse(hma["InputInFlight"]())

        mut_late_in = base_hold_src.replace(
            "\t\tbool mcpOwned = MCPHoldControl.IsStartedAction(this, beforeData);\n"
            "\t\tbool sentBefore = m_ActionInputWantEnd_Send;\n"
            "\t\tsuper.ProcessActionInputEnd();",
            "\t\tbool sentBefore = m_ActionInputWantEnd_Send;\n"
            "\t\tsuper.ProcessActionInputEnd();\n"
            "\t\tbool mcpOwned = MCPHoldControl.IsStartedAction(this, beforeData);",
            1,
        )
        self.assertNotEqual(mut_late_in, base_hold_src)
        hml1, _, mgrml1, _, envml1, mkml1, _ = self._make_action_input_harness(mut_late_in)
        envml1["mcp_use"](mkml1(setup_ok=True, use_ack=True, is_instant=False, end_on_input_end=True))
        mgrml1.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgrml1.Update()
        mgrml1.EndActionInput()
        mgrml1.Update()
        self.assertFalse(hml1["InputInFlight"]())

        mut_late_req = base_hold_src.replace(
            "\t\tbool mcpOwned = MCPHoldControl.IsStartedAction(this, beforeData);\n"
            "\t\tbool sentBefore = m_ActionWantEndRequest_Send;\n"
            "\t\tsuper.ProcessActionRequestEnd();",
            "\t\tbool sentBefore = m_ActionWantEndRequest_Send;\n"
            "\t\tsuper.ProcessActionRequestEnd();\n"
            "\t\tbool mcpOwned = MCPHoldControl.IsStartedAction(this, beforeData);",
            1,
        )
        self.assertNotEqual(mut_late_req, base_hold_src)
        hml2, _, mgrml2, _, envml2, mkml2, _ = self._make_action_input_harness(mut_late_req)
        envml2["mcp_use"](mkml2(setup_ok=True, use_ack=True, is_instant=False, end_on_request_end=True))
        mgrml2.OnSyncJuncture(SJ_ACTION_ACK_ACCEPT, 2)
        mgrml2.Update()
        mgrml2.RequestEndAction()
        mgrml2.Update()
        self.assertFalse(hml2["InputInFlight"]())


if __name__ == "__main__":
    unittest.main()
