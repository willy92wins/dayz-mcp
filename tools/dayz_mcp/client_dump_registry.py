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
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from dayz_mcp.client_steam_bootstrap import (
    ClientDumpBaseline,
    baseline_from_wire,
    baseline_to_wire,
)

MAX_ENTRIES = 32
MAX_RUN_ID_CHARS = 128


@dataclass
class _Entry:
    baseline: ClientDumpBaseline
    run_id: str | None = None
    ceiling: ClientDumpBaseline | None = None
    unattributable: bool = False


def _valid_run_id(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_RUN_ID_CHARS


class ClientDumpRegistry:
    def __init__(self, limit: int = MAX_ENTRIES) -> None:
        self._limit = limit
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

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
            entry.run_id = run_id  # type: ignore[assignment]
        return True

    def note_launch(self) -> None:
        """A launch reached the daemon: close every bound, uncapped run."""
        with self._lock:
            for entry in self._entries.values():
                if entry.run_id is not None and entry.ceiling is None:
                    entry.unattributable = True

    def bindings_for(self, run_ids: object) -> dict[str, dict[str, object]]:
        """run_id -> {"baseline", "ceiling"} for the provable runs asked for."""
        if not isinstance(run_ids, (list, tuple, set, frozenset)):
            return {}
        wanted = {run_id for run_id in run_ids if _valid_run_id(run_id)}
        out: dict[str, dict[str, object]] = {}
        with self._lock:
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
        return out
