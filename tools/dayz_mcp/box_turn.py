"""One external turn on the shared DayZ box.

``python -m dayz_mcp.box_turn`` does not open an MCP client, does not read a
keyfile and does not start a daemon. It joins ``shared_box_queue`` and reads
the same probes the daemon already uses.

Probes, in production:

- DayZ by image name: ``orphan_guard.snapshot_retail_processes`` and
  ``orphan_guard.snapshot_processes_by_name`` (diag and dedicated server).
- DayZ by UDP holder: ``orphan_guard.snapshot_udp_port_holders``. A name in
  the DayZ image set occupies the box. A holder with no image name is an
  indeterminate scan. TCP listeners are not consulted.
- ``box_reservation.current_reservation`` (``m1.espera``).
- The shared queue (``shared_box_queue.read_view``).

Tests do not reach these probes through the environment. A test process
installs callables with ``install_probe_hooks`` (see
``tests/box_turn_driver.py``). Environment variables cannot hide a live DayZ
or ``m1.espera``.
"""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import time
import uuid
from typing import Callable

from dayz_mcp import box_reservation, orphan_guard, shared_box_queue


# Same image set process_lifecycle uses for a UDP holder (process_lifecycle.py:119).
_DAYZ_IMAGES = frozenset(
    {
        "dayzdiag_x64.exe",
        "dayzserver_x64.exe",
        "dayz_x64.exe",
        "dayz_be.exe",
        "dayzserver",
    }
)
_POLL_S = 0.2
_REFRESH_S = 0.5
_DEFAULT_WAIT_S = 600.0
_CHILD_STOP_S = 5.0

NameHook = Callable[[], list[dict]]
UdpHook = Callable[[], dict]
ReservationHook = Callable[[], dict | None]

_name_hook: NameHook | None = None
_udp_hook: UdpHook | None = None
_reservation_hook: ReservationHook | None = None


def install_probe_hooks(
    *,
    names: NameHook,
    udp: UdpHook,
    reservation: ReservationHook,
) -> None:
    """Replace the three read-only probes. Tests only. Not read from the environment."""

    global _name_hook, _udp_hook, _reservation_hook
    _name_hook = names
    _udp_hook = udp
    _reservation_hook = reservation


def clear_probe_hooks() -> None:
    global _name_hook, _udp_hook, _reservation_hook
    _name_hook = None
    _udp_hook = None
    _reservation_hook = None


def _emit(kind: str, code: int) -> int:
    sys.stderr.write("\n")
    print(
        json.dumps({"box_turn": kind, "exit": code}, ensure_ascii=False),
        file=sys.stderr,
        flush=True,
    )
    return code


def _name_snapshots() -> list[dict]:
    if _name_hook is not None:
        return _name_hook()
    diag_names = ["DayZDiag_x64.exe", "DayZServer_x64.exe"]
    if os.name != "nt":
        diag_names.append("DayZServer")
    return [
        orphan_guard.snapshot_retail_processes(),
        orphan_guard.snapshot_processes_by_name(diag_names),
    ]


def _udp_snapshot() -> dict:
    if _udp_hook is not None:
        return _udp_hook()
    return orphan_guard.snapshot_udp_port_holders()


def _reservation() -> dict | None:
    if _reservation_hook is not None:
        return _reservation_hook()
    return box_reservation.current_reservation()


def _name_reason(snapshot: dict) -> str | None:
    if snapshot.get("known") is not True:
        return "scan_unknown"
    processes = snapshot.get("processes")
    if not isinstance(processes, list):
        return "scan_unknown"
    if processes:
        return "dayz"
    return None


def _udp_reason(snapshot: dict) -> str | None:
    """DayZ images occupy. An unnamed holder is indeterminate. TCP is absent."""

    if snapshot.get("known") is not True:
        return "scan_unknown"
    holders = snapshot.get("holders")
    if not isinstance(holders, list):
        return "scan_unknown"
    saw_dayz = False
    saw_unknown = False
    for row in holders:
        if not isinstance(row, dict):
            return "scan_unknown"
        name = row.get("name")
        if isinstance(name, str) and name.casefold() in _DAYZ_IMAGES:
            saw_dayz = True
            continue
        if name is None:
            saw_unknown = True
    if saw_unknown:
        return "scan_unknown"
    if saw_dayz:
        return "dayz"
    return None


def probe_reason(
    name_snapshots: list[dict] | None = None,
    udp_snapshot: dict | None = None,
) -> str | None:
    """Physical reason, or None when the probes show a free box.

    ``scan_unknown`` wins over a DayZ image: an indeterminate scan is occupied
    even if another probe saw a name.
    """

    names = _name_snapshots() if name_snapshots is None else name_snapshots
    udp = _udp_snapshot() if udp_snapshot is None else udp_snapshot
    reasons = [_name_reason(item) for item in names]
    reasons.append(_udp_reason(udp))
    if "scan_unknown" in reasons:
        return "scan_unknown"
    if "dayz" in reasons:
        return "dayz"
    return None


def physical_reason() -> str | None:
    """Reservation, then the process probes. None means the hardware is free."""

    try:
        reservation = _reservation()
    except Exception:
        return "box_reserved"
    if reservation is not None:
        return "box_reserved"
    return probe_reason()


def _view() -> dict:
    try:
        return shared_box_queue.read_view(
            now=time.time(), session_id="", write_purge=False
        )
    except shared_box_queue.QueueUnreadable:
        return {"unreadable": True, "queue": []}


def collect_status() -> dict:
    """``occupied``, ``reason``, ``holder`` and ``queue`` for ``status --json``."""

    view = _view()
    if view.get("unreadable"):
        return {
            "occupied": True,
            "reason": "box_queue_unreadable",
            "holder": None,
            "queue": [],
        }
    queue = view.get("queue")
    if not isinstance(queue, list):
        queue = []
    reason = physical_reason()
    holder = None
    entries = view.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("state") == "claimed":
                holder = entry.get("holder")
                if reason is None:
                    reason = "box_claimed"
                break
        if reason is None and (view.get("occupies") is True or entries):
            head = entries[0] if entries and isinstance(entries[0], dict) else None
            if head is not None:
                holder = head.get("holder")
            reason = "box_queue_ahead"
    return {
        "occupied": reason is not None,
        "reason": reason,
        "holder": holder,
        "queue": queue,
    }


class Turn:
    def __init__(self, purpose: str, holder: str, wait_s: float, command: list[str]) -> None:
        self.purpose = purpose
        self.holder = holder
        self.wait_s = wait_s
        self.command = command
        self.session_id = f"external:{os.getpid()}"
        self.ticket: str | None = None

    def _touch(self, *, claim: bool, done: bool = False) -> dict:
        return shared_box_queue.touch(
            now=time.time(),
            session_id=self.session_id,
            holder=self.holder,
            purpose=self.purpose,
            instance="external",
            ticket=self.ticket,
            new_ticket=None if self.ticket or done else str(uuid.uuid4()),
            done=done,
            claim=claim,
            kind="external",
        )

    def _remember(self, result: dict) -> dict:
        response = result.get("response")
        if not isinstance(response, dict):
            return {}
        if response.get("box_wait_error") == "box_ticket_invalid":
            # Keep the original ticket. Minting a new claim mid-command
            # would skip the physical probes.
            return response
        ticket = response.get("box_ticket")
        if isinstance(ticket, str) and ticket:
            self.ticket = ticket
        return response

    def _leave(self) -> None:
        if self.ticket is None:
            return
        try:
            self._touch(claim=False, done=True)
        except (shared_box_queue.QueueUnreadable, OSError, TimeoutError):
            return

    def _unreadable(self, result: dict, response: dict) -> bool:
        if result.get("unreadable"):
            return True
        return response.get("box_wait_error") == "box_queue_unreadable"

    def _gate(self) -> str:
        """``free`` to claim, ``busy`` to keep waiting, ``unreadable`` to stop.

        An indeterminate scan is ``unreadable`` immediately. It is not a wait.
        """

        view = _view()
        if view.get("unreadable"):
            return "unreadable"
        if probe_reason() == "scan_unknown":
            return "unreadable"
        if physical_reason() is not None:
            return "busy"
        entries = view.get("entries")
        if not isinstance(entries, list) or not entries:
            return "free"
        head = entries[0]
        if not isinstance(head, dict):
            return "busy"
        if head.get("state") == "claimed" and head.get("ticket") != self.ticket:
            return "busy"
        if self.ticket is None:
            return "busy"
        if head.get("ticket") == self.ticket:
            return "free"
        return "busy"

    def wait_turn(self) -> str | None:
        """Claim the head, or return ``wait_expired`` / ``unreadable``."""

        deadline = time.monotonic() + self.wait_s
        while True:
            try:
                gate = self._gate()
            except (OSError, TimeoutError):
                self._leave()
                return "unreadable"
            if gate == "unreadable":
                self._leave()
                return "unreadable"
            try:
                result = self._touch(claim=gate == "free")
            except shared_box_queue.QueueUnreadable:
                self._leave()
                return "unreadable"
            response = self._remember(result)
            if self._unreadable(result, response):
                self._leave()
                return "unreadable"
            if response.get("box_claimed") is True:
                if time.monotonic() >= deadline:
                    self._leave()
                    return "wait_expired"
                return None
            if time.monotonic() >= deadline:
                self._leave()
                return "wait_expired"
            remaining = deadline - time.monotonic()
            time.sleep(min(_POLL_S, max(0.0, remaining)))

    def _attach_child(self, child: subprocess.Popen) -> None:
        """Keep the claim on the live child if the wrapper later exits."""

        pid = getattr(child, "pid", None)
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return
        try:
            snap = shared_box_queue.default_snapshot(pid)
        except Exception:
            return
        created = snap.get("creation_time_utc") if isinstance(snap, dict) else None
        if not isinstance(created, str) or not created:
            return
        try:
            shared_box_queue.rebind_owner(
                self.session_id,
                self.ticket,
                owner_pid=pid,
                owner_created=created,
                now=time.time(),
            )
        except (shared_box_queue.QueueUnreadable, OSError, TimeoutError):
            return

    def _popen(self) -> subprocess.Popen:
        """Foreground command. stdin, stdout and stderr stay the parent's.

        ``CREATE_BREAKAWAY_FROM_JOB`` lets a hard kill of this process leave
        the child running (F3-neg4) without detaching it from the console.
        """

        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_BREAKAWAY_FROM_JOB
        try:
            return subprocess.Popen(self.command, creationflags=flags)
        except OSError:
            if flags == 0:
                raise
            return subprocess.Popen(self.command)

    def _stop_child(self, child: subprocess.Popen) -> None:
        """Stop a command that survived the interrupt, then wait a bounded time."""

        if child.poll() is not None:
            return
        try:
            child.send_signal(signal.SIGTERM)
        except OSError:
            try:
                child.terminate()
            except OSError:
                return
        deadline = time.monotonic() + _CHILD_STOP_S
        while child.poll() is None and time.monotonic() < deadline:
            try:
                child.wait(timeout=0.2)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                continue

    def _refresh_while(self, child: subprocess.Popen) -> int:
        while True:
            try:
                return child.wait(timeout=_REFRESH_S)
            except subprocess.TimeoutExpired:
                try:
                    result = self._touch(claim=True)
                except shared_box_queue.QueueUnreadable:
                    continue
                response = self._remember(result)
                if response.get("box_wait_error") == "box_ticket_invalid":
                    return 70
            except KeyboardInterrupt:
                # Ctrl+C on this console reaches a foreground child. If it is
                # still alive, stop it and wait. A child that will not die
                # keeps the claim: the departed-owner purge is the backstop,
                # and the box must not read free underneath it.
                self._stop_child(child)
                if child.poll() is None:
                    raise
                code = child.returncode
                return int(code if code is not None else 1)

    def run(self) -> int:
        failed = self.wait_turn()
        if failed == "wait_expired":
            return _emit("wait_expired", 75)
        if failed is not None:
            return _emit("unreadable", 70)
        child: subprocess.Popen | None = None
        code = 1
        try:
            try:
                child = self._popen()
            except OSError:
                return _emit("unreadable", 70)
            self._attach_child(child)
            code = self._refresh_while(child)
            if code == 70 and child.poll() is None:
                return _emit("claim_lost", 70)
        finally:
            if child is not None and child.poll() is None:
                self._stop_child(child)
            if child is None or child.poll() is not None:
                self._leave()
        return _emit("ran", int(code))


def _parse_run(args: list[str]) -> tuple[Turn | None, int | None]:
    purpose: str | None = None
    holder = "external"
    wait_s = _DEFAULT_WAIT_S
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            command = args[index + 1 :]
            if purpose is None or not purpose.strip() or not holder.strip():
                return None, 2
            if not command:
                return None, 2
            if isinstance(wait_s, bool) or not math.isfinite(wait_s) or wait_s <= 0:
                return None, 2
            return Turn(purpose.strip(), holder.strip(), float(wait_s), command), None
        if token in {"--purpose", "--holder", "--wait-s"}:
            if index + 1 >= len(args):
                return None, 2
            value = args[index + 1]
            index += 2
            if token == "--purpose":
                purpose = value
            elif token == "--holder":
                holder = value
            else:
                try:
                    wait_s = float(value)
                except ValueError:
                    return None, 2
            continue
        return None, 2
    return None, 2


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _emit("bad_args", 2)
    if args[0] == "status":
        if args[1:] != ["--json"]:
            return _emit("bad_args", 2)
        json.dump(collect_status(), sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    if args[0] != "run":
        return _emit("bad_args", 2)
    turn, bad = _parse_run(args[1:])
    if turn is None or bad is not None:
        return _emit("bad_args", 2)
    return turn.run()


if __name__ == "__main__":
    sys.exit(main())
