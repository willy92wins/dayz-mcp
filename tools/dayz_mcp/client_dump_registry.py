"""Daemon-side record of which client launch owns which new dumps (296b r3).

The client profile roots are shared by every MCP process of a project, so the
pre-launch baseline of a run and the later launch that caps it must live where
every client sees them: here, in the daemon. An MCP process opens an entry
with its snapshot BEFORE it asks for the launch, binds the entry to the run_id
the launch returns, and any reader gets the pair back by run_id.

A run is listed only while its ceiling is provable. Every /lifecycle/start
closes the bound runs no snapshot has capped yet: a launch that did not open
an entry here (an older MCP, a failed open) leaves no record of what it
wrote, so the earlier runs on the box are no longer attributable and publish
null. In memory only: a restarted daemon knows no run and every reader gets
null.

296b r4: the bind arrives after the launcher released the lease, so another
launch may reach the daemon in between. An entry waiting for its bind records
the run_id of every launch the daemon starts after its open (None when the
launch names no run). One dayz_test_run starts several times (the server,
its replay, the client) but always for its own run_id; the bind is
attributable only if every launch since the open was for the run_id it binds.
Any other launch (a launch without a snapshot, another run) leaves the run
unattributable. Every change also moves a revision a reader compares after
its scan: a binding it fetched is proof only while the revision has not moved.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from dayz_mcp.client_steam_bootstrap import (
    ClientDumpBaseline,
    baseline_from_wire,
    baseline_to_wire,
)

MAX_ENTRIES = 32
MAX_RUN_ID_CHARS = 128
# Launches one unbound entry keeps track of; past that it is unattributable.
MAX_LAUNCHES_PER_ENTRY = 8


@dataclass
class _Entry:
    baseline: ClientDumpBaseline
    run_id: str | None = None
    ceiling: ClientDumpBaseline | None = None
    unattributable: bool = False
    # Launch ticket -> run_id of that launch, for the launches after the open.
    launches: dict[int, str | None] = field(default_factory=dict)


def _valid_run_id(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_RUN_ID_CHARS


class ClientDumpRegistry:
    def __init__(self, limit: int = MAX_ENTRIES) -> None:
        self._limit = limit
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self._launches = 0
        # A restarted daemon starts a new instance: its revision never
        # matches one a reader fetched from the previous daemon.
        self._instance = uuid.uuid4().hex
        self._revision = 0

    def _changed(self) -> None:
        self._revision += 1

    def open(self, baseline_wire: object) -> str | None:
        """Record a pre-launch snapshot; cap every earlier entry on its roots.

        Returns the token the launcher binds to its run_id, or None when the
        snapshot is malformed (nothing is recorded or capped then).
        """
        baseline = baseline_from_wire(baseline_wire)
        if baseline is None:
            return None
        shared = set(baseline.roots)
        token = uuid.uuid4().hex
        with self._lock:
            for entry in self._entries.values():
                if entry.ceiling is not None or entry.unattributable:
                    continue
                if shared.intersection(entry.baseline.roots):
                    entry.ceiling = baseline
            self._entries[token] = _Entry(baseline=baseline)
            while len(self._entries) > self._limit:
                self._entries.popitem(last=False)
            self._changed()
        return token

    def bind(self, token: object, run_id: object) -> bool:
        if not isinstance(token, str) or not _valid_run_id(run_id):
            return False
        with self._lock:
            entry = self._entries.get(token)
            if entry is None or entry.run_id is not None:
                return False
            for other_token, other in list(self._entries.items()):
                if other.run_id == run_id:
                    # Two launches claiming one run_id: neither is provable.
                    del self._entries[other_token]
                    entry.unattributable = True
            if not entry.launches or any(
                launched != run_id for launched in entry.launches.values()
            ):
                # No launch since the snapshot, or a launch that was not this
                # run's own: what that one wrote is indistinguishable.
                entry.unattributable = True
            entry.run_id = run_id  # type: ignore[assignment]
            entry.launches.clear()
            self._changed()
        return True

    def note_launch(self, run_id: object = None) -> int:
        """A launch reached the daemon: close every bound, uncapped run.

        An entry still waiting for its bind records the launch under the
        returned ticket, with ``run_id`` (the run the launch is for, if the
        caller knows it yet); see launch_started and bind.
        """
        launched = run_id if _valid_run_id(run_id) else None
        with self._lock:
            self._launches += 1
            ticket = self._launches
            for entry in self._entries.values():
                if entry.run_id is not None:
                    if entry.ceiling is None:
                        entry.unattributable = True
                    continue
                if len(entry.launches) >= MAX_LAUNCHES_PER_ENTRY:
                    entry.unattributable = True
                    continue
                entry.launches[ticket] = launched  # type: ignore[assignment]
            self._changed()
        return ticket

    def launch_started(self, ticket: int, run_id: object) -> None:
        """Name the run of a launch noted without one (the daemon's answer)."""
        if not _valid_run_id(run_id):
            return
        with self._lock:
            for entry in self._entries.values():
                if (
                    entry.run_id is None
                    and ticket in entry.launches
                    and entry.launches[ticket] is None
                ):
                    entry.launches[ticket] = run_id  # type: ignore[assignment]
            self._changed()

    def bindings_for(self, run_ids: object) -> dict[str, dict[str, object]]:
        """run_id -> {"baseline", "ceiling"} for the provable runs asked for."""
        return self.bindings_with_revision(run_ids)[0]

    def bindings_with_revision(
        self, run_ids: object
    ) -> tuple[dict[str, dict[str, object]], str]:
        """bindings_for plus the revision they were read at, under one lock."""
        out: dict[str, dict[str, object]] = {}
        with self._lock:
            revision = f"{self._instance}:{self._revision}"
            if not isinstance(run_ids, (list, tuple, set, frozenset)):
                return out, revision
            wanted = {run_id for run_id in run_ids if _valid_run_id(run_id)}
            entries = [
                (entry.run_id, entry.baseline, entry.ceiling)
                for entry in self._entries.values()
                if entry.run_id in wanted and not entry.unattributable
            ]
        for run_id, baseline, ceiling in entries:
            baseline_wire = baseline_to_wire(baseline)
            ceiling_wire = None if ceiling is None else baseline_to_wire(ceiling)
            if baseline_wire is None or (ceiling is not None and ceiling_wire is None):
                continue
            out[run_id] = {"baseline": baseline_wire, "ceiling": ceiling_wire}  # type: ignore[index]
        return out, revision
