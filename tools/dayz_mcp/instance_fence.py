"""In-memory instance bindings for D-55 v1 fencing.

Bindings are not persisted. After a daemon restart the map is empty and a
live game that still sends its old inst= is unbound_after_restart.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


BINDING_STARTING = "STARTING"
BINDING_BOUND = "BOUND"
BINDING_AMBIGUOUS = "AMBIGUOUS"
BINDING_RETIRED = "RETIRED"
BINDING_LEGACY = "LEGACY_UNBOUND"
BINDING_UNREADABLE = "creation_time_unreadable"

FENCE_HINTS: dict[str, str] = {
    "legacy_unbound": (
        "Peer poll lacks a valid inst=. Relaunch via dayz_test_run so start_run "
        "writes instance into $profile:dayz_mcp.json. Do not mutate a hand-launched game."
    ),
    "instance_malformed": (
        "inst= is not a lowercase UUID4. Check the profile JSON; do not invent "
        "the field. Relaunch via dayz_test_run."
    ),
    "instance_unknown": (
        "This inst= is not in the daemon registry. If the daemon restarted, "
        "relaunch the run. adopt_run does not restore mutations."
    ),
    "unbound_after_restart": (
        "Daemon came up with a live game and no in-memory bindings. Relaunch "
        "the run; do not remint JSON under a live process (the bridge will not "
        "reload instance)."
    ),
    "instance_role_mismatch": (
        "This instance is registered for a different peer role. Stop that "
        "process and relaunch the correct role."
    ),
    "instance_ambiguous": (
        "Two processes presented the same instance. Close the extra DayZDiag "
        "that copied this profile. Mutations stay blocked until one PID remains."
    ),
    "instance_unattributed": (
        "TCP table did not map this poll to exactly one PID. Retry; if it "
        "persists, relaunch. Commands were not delivered."
    ),
    "creation_time_unreadable": (
        "Could not read the process creation time, so this poll was not "
        "accredited. Retry; if it persists, relaunch via dayz_test_run. "
        "This does not mean a second DayZ copied the profile."
    ),
    "binding_not_ready": (
        "Instance is minted but the first accredited poll has not landed. "
        "Wait for bridge_status.ready; do not enqueue yet."
    ),
    "binding_retired": (
        "Target instance was retired (stop, reap, or role replace). Relaunch "
        "that role via dayz_test_run so start_run mints a new instance. "
        "Resending this command will keep failing until the new instance is BOUND."
    ),
    "instance_peer_collision": (
        "Two live instances are bound for this peer. Stop the extra DayZDiag; "
        "mutations stay blocked until exactly one BOUND remains."
    ),
}

FENCE_ENQUEUE_CODES = frozenset(FENCE_HINTS)
FENCE_MUTATION_REJECT_CODES = (
    "legacy_unbound",
    "instance_unknown",
    "instance_ambiguous",
    "instance_unattributed",
    "binding_retired",
    "instance_role_mismatch",
    "instance_peer_collision",
    "binding_not_ready",
    "creation_time_unreadable",
)


def format_creation_time_utc(create_time: float) -> str:
    """Canonical process creation stamp: microseconds + Z, never +00:00."""
    creation_time = datetime.fromtimestamp(create_time, UTC)
    return creation_time.isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_creation_time_utc(value: object) -> str | None:
    """Re-format a stored stamp through format_creation_time_utc, or None.

    Never raises: unparseable or out-of-range stamps return None.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        stamp = parsed.timestamp()
        if not math.isfinite(stamp):
            return None
        return format_creation_time_utc(stamp)
    except (OSError, OverflowError, ValueError, TypeError):
        return None


class BindingPrepareError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class BindingRegistry(Protocol):
    def prepare(self, run_id: str, role: str, profiles_dir: str) -> str: ...
    def confirm(self, instance: str, record: object) -> None: ...
    def retire_role(self, run_id: str, role: str, reason: str) -> None: ...
    def retire_run(self, run_id: str, reason: str) -> None: ...


@dataclass
class Binding:
    instance: str
    run_id: str
    role: str
    epoch: int
    pid: int | None
    creation_time_utc: str | None
    state: str
    presented_pids: set[int] = field(default_factory=set)
    last_present_at: dict[int, float] = field(default_factory=dict)
    last_poll_at: float | None = None
    last_attributed_pid: int | None = None
    last_verified_at: float | None = None


def instance_prefix(instance: str | None) -> str | None:
    if not instance:
        return None
    try:
        parsed = uuid.UUID(instance)
    except (ValueError, AttributeError, TypeError):
        return None
    if parsed.version != 4 or str(parsed) != instance:
        return None
    return parsed.hex[:8]


def fence_error(code: str) -> tuple[int, dict[str, object]]:
    payload: dict[str, object] = {"error": code}
    hint = FENCE_HINTS.get(code)
    if hint is not None:
        payload["hint"] = hint
    return 409, payload


def classify_instance_token(value: str | None) -> tuple[str | None, str]:
    """Return (normalized_instance_or_none, class)."""
    if value is None or value == "":
        return None, BINDING_LEGACY
    if instance_prefix(value) is None:
        return None, "instance_malformed"
    return value, "instance_present"
