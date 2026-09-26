#!/usr/bin/env python3
from __future__ import annotations

import errno
import hmac
import json
import math
import sys
import threading
import time
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from dayz_mcp import daemon_credential, orphan_guard, pinned_keyfile, ui_dialog
from dayz_mcp.core import (
    BLOCKED_VERSION_STATES,
    EXPECTED_BRIDGE_VERSION,
)
from dayz_mcp.instance_fence import (
    BINDING_AMBIGUOUS,
    BINDING_BOUND,
    BINDING_LEGACY,
    BINDING_RETIRED,
    BINDING_STARTING,
    BINDING_UNREADABLE,
    Binding,
    BindingPrepareError,
    FENCE_MUTATION_REJECT_CODES,
    classify_instance_token,
    fence_error,
    instance_prefix,
    normalize_creation_time_utc,
)
from dayz_mcp.session_coordination import (
    ClientIdentity,
    MAX_SESSION_QUEUE,
    SessionCoordinator,
    command_requires_lease,
)


SERVER_COMMANDS = {
    "query_player_state",
    "query_all_players",
    "world_spawn",
    "object_delete",
    "notify_players",
    "vehicle_enter",
    "vehicle_drive",
    "scene_raycast",
    "telemetry_read",
    "query_get_in_condition",
    "world_time_set",
    "world_weather_set",
    "vehicle_prepare_fixture",
    # F3: server-side world/object/player verbs (MissionServer bridge).
    "surface_query",
    "player_teleport",
    "object_anim",
    "inventory_give",
    "object_inspect",
    "infected_drive",
    "entities_query",
}
CLIENT_COMMANDS = {
    "camera_set",
    "camera_get",
    "restore_gameplay",
    "drive_probe_client",
    "vehicle_get_in_client",
    "engine_set",
    "vehicle_control",
    "vehicle_telemetry",
    "vehicle_trace",
    "vehicle_release",
    "ui_tree",
    "ui_set_text",
    "ui_click",
    "ui_reload_layout",
    "ui_focus",
    "ui_dialog",
    "action_use",
}

CREDENTIAL_RECOVERY_TTL_S = 300.0
CREDENTIAL_RECOVERY_COUNT_MAX = 2_147_483_647
EXEC_COMMANDS = {"exec_enforce"}
WHITELISTED_COMMANDS = SERVER_COMMANDS | CLIENT_COMMANDS
# Whitelisted verbs that validate_command_args does NOT schema-check. Each is
# either read-only / single-arg or validated by its server.py tool. Keep in sync
# with SERVER_COMMANDS | CLIENT_COMMANDS: any whitelisted verb not in this set
# and not handled by an `if cmd == ...` branch is rejected as bad_args.
# Extra keys are not rejected here. Closing these 18 key sets means copying
# each verb's bridge/tool arg contract into this ingress; that is a separate
# change. Schemed verbs below already fail closed on unknown keys.
_SCHEMALESS_COMMANDS = {
    "query_player_state",
    "query_all_players",
    "world_spawn",
    "vehicle_enter",
    "vehicle_drive",
    "scene_raycast",
    "telemetry_read",
    "query_get_in_condition",
    "world_time_set",
    "world_weather_set",
    "camera_set",
    "camera_get",
    "drive_probe_client",
    "vehicle_get_in_client",
    "engine_set",
    "vehicle_control",
    "vehicle_telemetry",
    "vehicle_release",
}
VALID_PEERS = {"server", "client"}
SESSION_ROUTES = {
    "/session/acquire": "acquire",
    "/session/enqueue": "enqueue",
    "/session/wait": "wait",
    "/session/cancel": "cancel",
    "/session/cancel-operation": "cancel-operation",
    "/session/heartbeat": "heartbeat",
    "/session/release": "release",
    "/session/status": "status",
}
LIFECYCLE_ROUTES = {
    "/lifecycle/start": "start",
    "/lifecycle/ack": "ack",
    "/lifecycle/stop": "stop",
    "/lifecycle/adopt": "adopt",
    "/lifecycle/reap": "reap",
    "/lifecycle/status": "status",
}
ADMIN_ROUTES = {
    "/admin/release": "release",
    "/admin/reconcile": "reconcile",
    "/admin/audit-repair": "audit-repair",
    "/admin/lifecycle-recovery-repair": "lifecycle-recovery-repair",
}
MAX_QUEUE = 64
# Unconsumed /result rows. /await defaults to remove=0, so COMMAND_TTL_S
# (pending commands) does not bound this dict. 4x MAX_QUEUE holds four full
# in-flight cycles of peek-then-take; the oldest insertion is dropped first.
MAX_RESULTS = MAX_QUEUE * 4
# Worker ceiling for ExclusiveThreadingHTTPServer.process_request.
# Bind is 127.0.0.1: this is local availability, not remote exposure, so the
# number tracks the broker rather than a large internet backlog.
# The coordinator admits MAX_SESSION_QUEUE Cowork sessions against one game;
# each queued session may hold one POST /wait. Add 2 game-bridge /poll
# sockets, the active session's /enqueue+/await, /status probes, and
# headroom for a short legitimate burst while waits are held. Exceeding the
# ceiling closes the accepted socket without a worker (reject, do not queue):
# Handler.timeout already bounds each worker's duration, not concurrency.
MAX_HTTP_WORKERS = MAX_SESSION_QUEUE + 32
# Stale-command hygiene: a client that crashed/relaunched must not
# inherit commands queued for the previous session. record_poll drops commands
# older than COMMAND_TTL_S, and flushes the peer's whole queue when the gap since
# its last poll exceeds PEER_RECONNECT_GAP_S (the previous game/session is gone).
COMMAND_TTL_S = 30.0
PEER_RECONNECT_GAP_S = 10.0
RETIRED_INSTANCE_LIMIT = 64
# Ceiling for Handler._read_json. Checked against exec_enforce (short
# allowlisted expr), world_spawn (tiny), pipeline_feedback (not on this
# socket; 8 KiB inbox cap), ui_tree /result (<=512 nodes) and
# vehicle_trace /result (limit<=64 samples). None approach 1 MiB.
MAX_BODY_BYTES = 1 * 1024 * 1024

LogSink = Callable[[str], None]
VersionValidator = Callable[[str | None], str]
ExecAudit = Callable[[str, str, str, int | None], None]


def peer_for_command(cmd: str) -> str:
    if cmd in CLIENT_COMMANDS:
        return "client"
    return "server"


def _default_log_sink(message: str) -> None:
    print(message, flush=True)


def _is_real_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _box_payload(state: "ServerState") -> dict:
    """Compose the public box snapshot for /session/status only."""

    lifecycle = getattr(state, "lifecycle", None)
    occupancy: dict
    if lifecycle is not None:
        reader = getattr(lifecycle, "box_occupancy", None)
        if callable(reader):
            try:
                raw = reader()
            except Exception:
                raw = None
            occupancy = dict(raw) if isinstance(raw, dict) else {
                "occupied": True,
                "runs": [],
                "foreign": [],
                "ports_in_use": [],
            }
        else:
            occupancy = {
                "occupied": True,
                "runs": [],
                "foreign": [],
                "ports_in_use": [],
            }
    else:
        occupancy = {
            "occupied": True,
            "runs": [],
            "foreign": [],
            "ports_in_use": [],
        }
    coordination = getattr(state, "coordination", None)
    queue: list = []
    claimed = False
    if coordination is not None:
        queue_fn = getattr(coordination, "box_queue_public", None)
        if callable(queue_fn):
            try:
                public_queue = queue_fn()
            except Exception:
                public_queue = []
            if isinstance(public_queue, list):
                queue = public_queue
        claimed_fn = getattr(coordination, "box_is_claimed", None)
        if callable(claimed_fn):
            try:
                claimed = bool(claimed_fn())
            except Exception:
                claimed = True
        claim_public = getattr(coordination, "box_claim_public", None)
        if callable(claim_public):
            try:
                claim = claim_public()
            except Exception:
                claim = {"claimed": True, "claimed_s": None}
            if isinstance(claim, dict):
                if claim.get("claimed"):
                    claimed = True
                if claim.get("claimed_s") is not None:
                    occupancy["claimed_s"] = claim.get("claimed_s")
    occupancy["queue"] = queue
    if claimed:
        occupancy["occupied"] = True
    if "occupied" not in occupancy:
        occupancy["occupied"] = True
    if "runs" not in occupancy:
        occupancy["runs"] = []
    if "foreign" not in occupancy:
        occupancy["foreign"] = []
    if "ports_in_use" not in occupancy:
        occupancy["ports_in_use"] = []
    return occupancy


def _safe_operation_timeout(value: object) -> tuple[float, bool]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0, False
    try:
        seconds = float(value)
    except (OverflowError, ValueError):
        return 0.0, False
    if not math.isfinite(seconds):
        return 0.0, False
    return seconds, True


_FieldValidator = Callable[[object], bool]
_SchemaVariant = tuple[
    frozenset[str],
    frozenset[str],
    dict[str, _FieldValidator],
]
_DelegatedValidator = Callable[[dict], tuple[bool, str | None]]
_CommandSchema = tuple[tuple[_SchemaVariant, ...], _DelegatedValidator | None]


def _schema_variant(
    *,
    required: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
    validators: dict[str, _FieldValidator] | None = None,
) -> _SchemaVariant:
    return frozenset(required), frozenset(optional), validators or {}


def _command_schema(
    *variants: _SchemaVariant,
    delegated: _DelegatedValidator | None = None,
) -> _CommandSchema:
    return variants, delegated


def _is_strict_bool(value: object) -> bool:
    return isinstance(value, bool)


def _is_string(value: object) -> bool:
    return isinstance(value, str)


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _equal_to(expected: object) -> _FieldValidator:
    def validate(value: object) -> bool:
        return value == expected

    return validate


def _one_of(*accepted: object) -> _FieldValidator:
    accepted_values = frozenset(accepted)

    def validate(value: object) -> bool:
        return value in accepted_values

    return validate


def _integer_in_range(
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> _FieldValidator:
    def validate(value: object) -> bool:
        if not isinstance(value, int) or _is_strict_bool(value):
            return False
        if minimum is not None and value < minimum:
            return False
        if maximum is not None and value > maximum:
            return False
        return True

    return validate


def _real_in_range(
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    minimum_inclusive: bool = True,
) -> _FieldValidator:
    def validate(value: object) -> bool:
        if not _is_real_number(value):
            return False
        number = float(value)
        if minimum is not None:
            if minimum_inclusive and number < minimum:
                return False
            if not minimum_inclusive and number <= minimum:
                return False
        if maximum is not None and number > maximum:
            return False
        return True

    return validate


def _reject_numeric_errors(validator: _FieldValidator) -> _FieldValidator:
    def validate(value: object) -> bool:
        try:
            return validator(value)
        except (OverflowError, ValueError):
            return False

    return validate


def _is_real_vector3(value: object) -> bool:
    if not isinstance(value, list) or len(value) != 3:
        return False
    try:
        return all(_is_real_number(component) for component in value)
    except (OverflowError, ValueError):
        return False


def _is_non_empty_string_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(item, str) and item != "" for item in value)
    )


def _lower_hex(length: int) -> _FieldValidator:
    def validate(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == length
            and all(character in "0123456789abcdef" for character in value)
        )

    return validate


def _validate_ui_dialog_args(args: dict) -> tuple[bool, str | None]:
    return ui_dialog.validate_command_args(args)


_SAFE_FINITE_REAL = _reject_numeric_errors(_real_in_range())
_SAFE_POSITIVE_REAL = _reject_numeric_errors(
    _real_in_range(minimum=0.0, minimum_inclusive=False)
)
_SAFE_RADIUS_200 = _reject_numeric_errors(
    _real_in_range(minimum=0.0, maximum=200.0, minimum_inclusive=False)
)


# Command schemas keep the authenticated ingress contract in one place. Variants
# preserve alternate payload shapes without duplicating command dispatch logic.
_COMMAND_ARG_SCHEMAS: dict[str, _CommandSchema] = {
    "restore_gameplay": _command_schema(_schema_variant()),
    "vehicle_trace": _command_schema(
        _schema_variant(
            required=(
                "mode",
                "trace_id",
                "cursor",
                "limit",
                "sample_hz",
                "max_samples",
            ),
            validators={
                "mode": _one_of("start", "status", "stop", "read", "clear"),
                "trace_id": _lower_hex(32),
                "cursor": _integer_in_range(minimum=0),
                "limit": _integer_in_range(minimum=1, maximum=64),
                "sample_hz": _integer_in_range(minimum=20, maximum=60),
                "max_samples": _integer_in_range(minimum=2, maximum=8192),
            },
        )
    ),
    # Any CarScript classname is accepted; non-vehicles fail in the bridge
    # after CarScript.Cast.
    "vehicle_prepare_fixture": _command_schema(
        _schema_variant(
            required=("mode", "type", "pos", "radius"),
            validators={
                "mode": _equal_to("object_at"),
                "type": _is_non_empty_string,
                "pos": _is_real_vector3,
                "radius": _SAFE_POSITIVE_REAL,
            },
        )
    ),
    # World bounds require GetWorldSize in the bridge; non-finite coordinates
    # are rejected here before they can reach the consumer.
    "surface_query": _command_schema(
        _schema_variant(
            required=("x", "z"),
            validators={
                "x": _SAFE_FINITE_REAL,
                "z": _SAFE_FINITE_REAL,
            },
        )
    ),
    "player_teleport": _command_schema(
        _schema_variant(
            required=("pos",),
            optional=("uid",),
            validators={
                "uid": _is_string,
                "pos": _is_real_vector3,
            },
        )
    ),
    "infected_drive": _command_schema(
        _schema_variant(
            required=("type", "pos", "heading", "speed"),
            validators={
                "type": _is_non_empty_string,
                "pos": _is_real_vector3,
                "heading": _reject_numeric_errors(
                    _real_in_range(minimum=-360.0, maximum=360.0)
                ),
                "speed": _reject_numeric_errors(
                    _real_in_range(minimum=0.0, maximum=5.0)
                ),
            },
        ),
        _schema_variant(
            required=("type", "pos", "mode"),
            validators={
                "type": _is_non_empty_string,
                "pos": _is_real_vector3,
                "mode": _equal_to("release"),
            },
        ),
    ),
    # An absent phase reads the source; a present phase writes it. The second
    # variant targets by object_id from the world_spawn registry instead of
    # classname near pos (fb-20260824-133301-ecf5).
    "object_anim": _command_schema(
        _schema_variant(
            required=("type", "pos", "source"),
            optional=("phase",),
            validators={
                "type": _is_non_empty_string,
                "source": _is_non_empty_string,
                "pos": _is_real_vector3,
                "phase": _SAFE_FINITE_REAL,
            },
        ),
        _schema_variant(
            required=("object_id", "source"),
            optional=("phase",),
            validators={
                "object_id": _integer_in_range(minimum=1),
                "source": _is_non_empty_string,
                "phase": _SAFE_FINITE_REAL,
            },
        ),
    ),
    "inventory_give": _command_schema(
        _schema_variant(
            required=("classname", "dest"),
            optional=("uid",),
            validators={
                "uid": _is_string,
                "classname": _is_non_empty_string,
                "dest": _one_of("hands", "inventory"),
            },
        )
    ),
    "object_inspect": _command_schema(
        _schema_variant(
            required=("type", "pos", "want"),
            validators={
                "type": _is_non_empty_string,
                "want": _is_non_empty_string_list,
                "pos": _is_real_vector3,
            },
        ),
        _schema_variant(
            required=("object_id", "want"),
            validators={
                "object_id": _integer_in_range(minimum=1),
                "want": _is_non_empty_string_list,
            },
        ),
    ),
    # Exact keys keep authenticated socket ingress fail-closed.
    "object_delete": _command_schema(
        _schema_variant(
            required=("object_id",),
            validators={"object_id": _integer_in_range(minimum=1)},
        )
    ),
    "notify_players": _command_schema(
        _schema_variant(
            required=("show_time", "title"),
            optional=("detail", "icon", "uid"),
            validators={
                "show_time": _real_in_range(
                    minimum=0.0,
                    minimum_inclusive=False,
                ),
                "title": _is_non_empty_string,
                "detail": _is_string,
                "icon": _is_string,
                "uid": _is_string,
            },
        )
    ),
    "entities_query": _command_schema(
        _schema_variant(
            required=("pos", "radius"),
            optional=("limit",),
            validators={
                "pos": _is_real_vector3,
                "radius": _SAFE_RADIUS_200,
                "limit": _integer_in_range(minimum=1, maximum=128),
            },
        )
    ),
    "ui_tree": _command_schema(
        _schema_variant(
            optional=("path", "limit"),
            validators={
                "path": _is_string,
                "limit": _integer_in_range(minimum=1, maximum=512),
            },
        )
    ),
    "ui_set_text": _command_schema(
        _schema_variant(
            required=("path", "text"),
            validators={
                "path": _is_non_empty_string,
                "text": _is_string,
            },
        )
    ),
    "ui_click": _command_schema(
        _schema_variant(
            required=("path",),
            optional=("button",),
            validators={
                "path": _is_non_empty_string,
                "button": _integer_in_range(minimum=0, maximum=2),
            },
        )
    ),
    # This path names a layout file, not a widget. Close carries no path.
    "ui_reload_layout": _command_schema(
        _schema_variant(
            required=("path",),
            optional=("limit",),
            validators={
                "path": _is_non_empty_string,
                "limit": _integer_in_range(minimum=1, maximum=512),
            },
        ),
        _schema_variant(
            required=("mode", "path"),
            optional=("limit",),
            validators={
                "mode": _one_of("reload"),
                "path": _is_non_empty_string,
                "limit": _integer_in_range(minimum=1, maximum=512),
            },
        ),
        _schema_variant(
            required=("mode",),
            optional=("path", "limit"),
            validators={
                "mode": _one_of("close"),
                "path": _equal_to(""),
                "limit": _integer_in_range(minimum=1, maximum=512),
            },
        ),
    ),
    # Focus resolves a widget name through the same path as tree and click.
    "ui_focus": _command_schema(
        _schema_variant(
            required=("path",),
            validators={"path": _is_non_empty_string},
        )
    ),
    "ui_dialog": _command_schema(delegated=_validate_ui_dialog_args),
    "action_use": _command_schema(
        _schema_variant(
            required=("action",),
            optional=("classname", "pos", "radius"),
            validators={
                "action": _is_non_empty_string,
                "classname": _is_string,
                "pos": _is_real_vector3,
                "radius": _SAFE_RADIUS_200,
            },
        )
    ),
    # Only payload shape is checked here. Allowlisting, audit, and queue errors
    # remain in the dedicated enqueue path.
    "exec_enforce": _command_schema(
        _schema_variant(
            optional=("expr", "main_fn", "timeout_s"),
            validators={
                "expr": _is_string,
                "main_fn": _is_string,
                "timeout_s": _SAFE_POSITIVE_REAL,
            },
        )
    ),
}


def _matches_schema_variant(
    args: dict,
    keys: set[str],
    variant: _SchemaVariant,
) -> bool:
    required, optional, validators = variant
    if not required.issubset(keys) or keys - required - optional:
        return False
    for field, validator in validators.items():
        if field in args and not validator(args[field]):
            return False
    return True


def validate_command_args(cmd: str, args: dict) -> tuple[bool, str | None]:
    schema = _COMMAND_ARG_SCHEMAS.get(cmd)
    if schema is not None:
        variants, delegated = schema
        if delegated is not None:
            return delegated(args)

        keys = set(args)
        if any(_matches_schema_variant(args, keys, variant) for variant in variants):
            return True, None
        return False, "bad_args"

    if cmd in _SCHEMALESS_COMMANDS:
        return True, None

    return False, "bad_args"


class TestIdentityOverride:
    """Single test-only door for fake PID + creation_time.

    Production (prepare/confirm/Handler) never installs this. HTTP fixtures
    go through install_bound_peer, which is the only writer.
    """

    def __init__(self) -> None:
        self._pid_by_instance: dict[str, int] = {}
        self._ctime_by_pid: dict[int, str] = {}

    def bind(self, instance: str, pid: int, creation_time_utc: str) -> None:
        self._pid_by_instance[instance] = pid
        if isinstance(creation_time_utc, str) and creation_time_utc:
            self._ctime_by_pid[pid] = creation_time_utc

    def pid_for(self, instance: str | None) -> int | None:
        if not instance:
            return None
        pid = self._pid_by_instance.get(instance)
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            return pid
        return None

    def ctime_for(self, pid: int) -> str | None:
        value = self._ctime_by_pid.get(pid)
        return value if isinstance(value, str) and value else None

    def drop_instance(self, instance: str, pid: int | None) -> None:
        self._pid_by_instance.pop(instance, None)
        if isinstance(pid, int):
            self._ctime_by_pid.pop(pid, None)


class ServerState:
    def __init__(
        self,
        key: str,
        enable_exec_enforce: bool = False,
        version_validator: VersionValidator | None = None,
        exec_allowlist: set[str] | None = None,
        exec_audit: ExecAudit | None = None,
        time_fn: Callable[[], float] | None = None,
        coordination: SessionCoordinator | None = None,
        config_port: int | None = None,
    ) -> None:
        self.key = key
        # Loopback port this daemon owns. Only read to seed a bridge config
        # the deploy never wrote; None keeps prepare() failing closed.
        self.config_port = config_port
        self.enable_exec_enforce = enable_exec_enforce
        self.version_validator = version_validator
        self.exec_allowlist = set(exec_allowlist) if exec_allowlist is not None else None
        self.exec_audit = exec_audit
        self.coordination = coordination
        self.coordination_store: object | None = None
        self.coordination_fault_store: object | None = None
        self.lifecycle_recovery_fault_store: object | None = None
        self.audit_writer: object | None = None
        self.lifecycle: object | None = None
        self.retail_probe: Callable[[], dict[str, object]] | None = None
        self.daemon_generation: str | None = None
        self._lock = threading.RLock()
        self._next_id = 1
        self._legacy_queues: dict[str, list[dict]] = {"server": [], "client": []}
        self._queues = self._legacy_queues
        self._bound_queues: dict[str, list[dict]] = {}
        self._bindings: dict[str, Binding] = {}
        self._role_index: dict[tuple[str, str], str] = {}
        self._station_epoch = 0
        self._ever_bound = False
        self._seen_valid_inst_poll = False
        self._peer_last_class: dict[str, str] = {}
        self._bound_last_poll_at: dict[str, float | None] = {
            "server": None,
            "client": None,
        }
        self._command_fence: dict[int, tuple[str, int, int]] = {}
        self._test_identity_override: TestIdentityOverride | None = None
        self._retired_instances: OrderedDict[str, None] = OrderedDict()
        self._retired_roles: set[str] = set()
        self._creation_time_fn: Callable[[int], str | None] | None = None
        self._connections_fn: Callable[[], object] | None = None
        self._fence_reject_counts: dict[str, int] = {
            code: 0 for code in FENCE_MUTATION_REJECT_CODES
        }
        self._unaccredited_mutation_enqueues = 0
        self._unaccredited_poll_counts: dict[str, int] = {}
        self._exec_capacity_reserved: dict[str, int] = {"server": 0, "client": 0}
        self._enqueue_generation = 0
        self._stopping = False
        self._results: dict[int, dict] = {}
        self._enqueued_at: dict[int, float] = {}
        self._operation_deadlines: dict[int, float] = {}
        self._command_owner: dict[int, tuple[ClientIdentity, str]] = {}
        self._fire_and_forget_ids: set[int] = set()
        self._audit_degraded_count = 0
        self._poll_delay_ms = 0
        self._last_poll_at: dict[str, float | None] = {"server": None, "client": None}
        self._poll_versions: dict[str, str | None] = {"server": None, "client": None}
        # Last session-driven (client) HTTP request; feeds the daemon idle watchdog
        # so a broker daemon with no game and no client traffic can reap itself.
        self._last_client_request_at: float | None = None
        self._credential_recovery_count = 0
        self._last_credential_recovery_at: float | None = None
        # Injectable monotonic clock. Defaults to time.monotonic; tests inject a
        # fake so the stale-command TTL / reconnect-flush logic is deterministic.
        self._time_fn = time_fn or time.monotonic

    def _now(self) -> float:
        return self._time_fn()

    def _seed_bridge_config(self, config_path: Path) -> bool:
        """Write the bridge config a launched role needs when the deploy left none.

        Fencing turned `<profiles>\\dayz_mcp.json` into a launch
        precondition, but install_mcp.py writes it only into the `_mcp_config`
        templates and into roots it is explicitly pointed at, so a role it never
        saw refused to launch. Seeding is confined to a profiles directory that
        already exists: a mistyped dev_root still fails closed instead of
        receiving the key.
        """
        from dayz_mcp.runtime_state import atomic_write_json

        if not self.key or not isinstance(self.config_port, int):
            return False
        if isinstance(self.config_port, bool) or not config_path.parent.is_dir():
            return False
        try:
            # The same three fields install_mcp.py writes (install_mcp.py:977-984).
            atomic_write_json(
                config_path,
                {
                    "url": "http://127.0.0.1:" + str(self.config_port) + "/",
                    "key": self.key,
                    "pollHz": 5,
                },
            )
        except OSError:
            return False
        return config_path.is_file()

    def prepare(self, run_id: str, role: str, profiles_dir: str) -> str:
        from dayz_mcp.process_lifecycle import _valid_uuid4
        from dayz_mcp.runtime_state import atomic_write_json

        if (
            not isinstance(profiles_dir, str)
            or not profiles_dir
            or not Path(profiles_dir).is_absolute()
        ):
            raise BindingPrepareError("instance_config_missing")
        config_path = Path(profiles_dir) / "dayz_mcp.json"
        if not config_path.is_file() and not self._seed_bridge_config(config_path):
            raise BindingPrepareError("instance_config_missing")
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BindingPrepareError("instance_config_missing") from exc
        if not isinstance(payload, dict):
            raise BindingPrepareError("instance_config_missing")
        minted = str(uuid.uuid4())
        if not _valid_uuid4(minted):
            minted = str(uuid.uuid4())
        payload["instance"] = minted
        atomic_write_json(config_path, payload)
        try:
            reread = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BindingPrepareError("instance_config_mismatch") from exc
        if not isinstance(reread, dict) or reread.get("instance") != minted:
            raise BindingPrepareError("instance_config_mismatch")
        with self._lock:
            self._station_epoch += 1
            self._bindings[minted] = Binding(
                instance=minted,
                run_id=run_id,
                role=role,
                epoch=self._station_epoch,
                pid=None,
                creation_time_utc=None,
                state=BINDING_STARTING,
            )
            self._role_index[(run_id, role)] = minted
            self._bound_queues.setdefault(minted, [])
            self._ever_bound = True
        return minted

    def confirm(self, instance: str, record: object) -> None:
        pid = getattr(record, "pid", None)
        creation = getattr(record, "creation_time_utc", None)
        role = getattr(record, "role", None)
        with self._lock:
            binding = self._bindings.get(instance)
            if binding is None or binding.state != BINDING_STARTING:
                return
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                return
            if not isinstance(creation, str) or not creation:
                return
            binding.pid = pid
            binding.creation_time_utc = creation
            if isinstance(role, str) and role:
                binding.role = role
            binding.state = BINDING_BOUND
            self._retired_roles.discard(binding.role)
            if binding.role == "offline":
                self._retired_roles.discard("server")
                self._retired_roles.discard("client")

    def retire_role(self, run_id: str, role: str, reason: str) -> None:
        with self._lock:
            instance = self._role_index.pop((run_id, role), None)
            if instance is None:
                return
            self._retire_instance_locked(instance, reason)

    def retire_run(self, run_id: str, reason: str) -> None:
        with self._lock:
            keys = [key for key in self._role_index if key[0] == run_id]
            for key in keys:
                instance = self._role_index.pop(key, None)
                if instance is not None:
                    self._retire_instance_locked(instance, reason)

    def install_bound_peer(
        self,
        *,
        instance: str,
        role: str,
        pid: int,
        run_id: str = "testrun",
        creation_time_utc: str = "2026-08-18T00:00:00.000000Z",
    ) -> None:
        from dayz_mcp.process_lifecycle import _valid_uuid4

        if not _valid_uuid4(instance):
            raise ValueError("instance_malformed")
        with self._lock:
            self._station_epoch += 1
            self._bindings[instance] = Binding(
                instance=instance,
                run_id=run_id,
                role=role,
                epoch=self._station_epoch,
                pid=pid,
                creation_time_utc=creation_time_utc,
                state=BINDING_BOUND,
            )
            self._role_index[(run_id, role)] = instance
            self._bound_queues.setdefault(instance, [])
            if self._test_identity_override is None:
                self._test_identity_override = TestIdentityOverride()
            self._test_identity_override.bind(instance, pid, creation_time_utc)
            self._ever_bound = True

    def _retire_instance_locked(self, instance: str, reason: str) -> None:
        binding = self._bindings.get(instance)
        if binding is None:
            return
        self._station_epoch += 1
        queue = self._bound_queues.get(instance, [])
        discarded_exec: list[tuple[str, str, int]] = []
        finished_operations: list[tuple[ClientIdentity, str, int, str, str]] = []
        self._discard_queue(queue, "binding_retired", discarded_exec, finished_operations)
        self._retired_instances[instance] = None
        self._retired_instances.move_to_end(instance)
        while len(self._retired_instances) > RETIRED_INSTANCE_LIMIT:
            self._retired_instances.popitem(last=False)
        self._retired_roles.add(binding.role)
        if binding.role == "offline":
            self._retired_roles.update({"server", "client"})
        retired_pid = binding.pid
        self._bindings.pop(instance, None)
        self._bound_queues.pop(instance, None)
        override = self._test_identity_override
        if override is not None:
            override.drop_instance(instance, retired_pid)
        for command_id, fence in list(self._command_fence.items()):
            if fence[0] == instance:
                self._command_fence.pop(command_id, None)

    def _peer_covers(self, binding: Binding, peer: str) -> bool:
        return binding.role == peer or binding.role == "offline"

    def _active_bindings_for_peer(self, peer: str) -> list[Binding]:
        return [
            binding
            for binding in self._bindings.values()
            if binding.state != BINDING_RETIRED and self._peer_covers(binding, peer)
        ]

    def _enqueue_fence_target(
        self, peer: str, cmd: str
    ) -> tuple[str | None, list[dict] | None, str | None]:
        mutation = command_requires_lease(cmd)
        candidates = self._active_bindings_for_peer(peer)
        bound = [binding for binding in candidates if binding.state == BINDING_BOUND]
        ambiguous = [
            binding for binding in candidates if binding.state == BINDING_AMBIGUOUS
        ]
        starting = [
            binding for binding in candidates if binding.state == BINDING_STARTING
        ]
        unreadable = [
            binding for binding in candidates if binding.state == BINDING_UNREADABLE
        ]
        if len(bound) > 1:
            return "instance_peer_collision", None, None
        if mutation:
            if len(bound) == 1:
                instance = bound[0].instance
                return None, self._bound_queues.setdefault(instance, []), instance
            if ambiguous:
                return "instance_ambiguous", None, None
            if unreadable:
                return "creation_time_unreadable", None, None
            if starting:
                return "binding_not_ready", None, None
            if not candidates and (
                peer in self._retired_roles or "offline" in self._retired_roles
            ):
                return "binding_retired", None, None
            if self._seen_valid_inst_poll and not self._ever_bound:
                return "unbound_after_restart", None, None
            return "legacy_unbound", None, None
        if len(bound) == 1:
            instance = bound[0].instance
            return None, self._bound_queues.setdefault(instance, []), instance
        if ambiguous:
            return "instance_ambiguous", None, None
        if unreadable:
            return "creation_time_unreadable", None, None
        if starting:
            return "binding_not_ready", None, None
        return None, self._legacy_queues[peer], None

    def _peer_queue_len(self, peer: str) -> int:
        total = len(self._legacy_queues[peer]) + self._exec_capacity_reserved[peer]
        for instance, queue in self._bound_queues.items():
            binding = self._bindings.get(instance)
            if binding is None or binding.state == BINDING_RETIRED:
                continue
            if not self._peer_covers(binding, peer):
                continue
            total += sum(
                1
                for command in queue
                if peer_for_command(str(command.get("cmd") or "")) == peer
            )
        return total

    def _iter_mutable_queues(self):
        yield from self._legacy_queues.items()
        yield from self._bound_queues.items()

    def _seal_command(
        self, command_id: int, instance: str | None, binding: Binding | None
    ) -> None:
        if not instance:
            return
        epoch = binding.epoch if binding is not None else self._station_epoch
        pid = 0
        if binding is not None and isinstance(binding.pid, int):
            pid = binding.pid
        self._command_fence[command_id] = (instance, epoch, pid)

    def resolve_poll_pid(self, instance: str | None, sock: object) -> int | None:
        if not instance:
            return None
        override = self._test_identity_override
        if override is not None:
            forced = override.pid_for(instance)
            if forced is not None:
                return forced
        pid = self._lookup_connected_pid(sock)
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            return pid
        return None

    def _lookup_creation_time(self, pid: int | None) -> str | None:
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return None
        override = self._test_identity_override
        if override is not None:
            forced = override.ctime_for(pid)
            if forced is not None:
                return forced
        reader = self._creation_time_fn
        if callable(reader):
            try:
                value = reader(pid)
            except Exception:
                return None
            if isinstance(value, str) and value:
                return value
        lifecycle = self.lifecycle
        if lifecycle is None:
            return None
        try:
            actual = lifecycle.guard.snapshot(pid)
        except Exception:
            return None
        if not isinstance(actual, dict):
            return None
        value = actual.get("creation_time_utc")
        return value if isinstance(value, str) and value else None

    def _tcp_connections(self) -> object:
        connections_fn = self._connections_fn
        if connections_fn is None:
            try:
                import psutil
            except ImportError:
                return None

            def connections_fn() -> object:
                return psutil.net_connections(kind="tcp")
        try:
            return connections_fn()
        except Exception:
            return None

    def _lookup_connected_pid(self, sock: object) -> int | None:
        # Direct scan. A 50ms table cache never contained the
        # connection of the next request (new TCP per poll); useful hit
        # rate measured 0. A failed lookup is instance_unattributed
        # this tick only. Keep-alive on the bridge (Enforce) is out of v1.
        if sock is None:
            return None
        table = self._tcp_connections()
        if table is None:
            return None
        try:
            from dayz_mcp.accredited_daemon_transport import _connected_server_pid

            return _connected_server_pid(sock, connections_fn=lambda: table)
        except Exception:
            return None

    def _audit_fence(
        self,
        event: str,
        command_id: int,
        expected: str | None,
        origin: str | None,
        extra: dict[str, object] | None = None,
    ) -> None:
        writer = self.audit_writer
        write = getattr(writer, "write", None)
        if not callable(write):
            return
        payload: dict[str, object] = {
            "event": event,
            "command_id": command_id,
            "expected_instance_prefix": instance_prefix(expected),
            "origin_instance_prefix": instance_prefix(origin),
        }
        if extra:
            payload.update(extra)
        try:
            write(payload)
        except Exception:
            pass

    def touch_client(self) -> None:
        """Mark session-side (client) HTTP activity for the daemon idle watchdog."""
        with self._lock:
            self._last_client_request_at = self._now()

    def record_credential_recovery(self) -> None:
        with self._lock:
            self._credential_recovery_count = min(
                CREDENTIAL_RECOVERY_COUNT_MAX,
                self._credential_recovery_count + 1,
            )
            self._last_credential_recovery_at = self._now()

    def credential_recovery_snapshot(
        self,
        now: float | None = None,
    ) -> dict[str, object]:
        if now is None:
            now = self._now()
        with self._lock:
            count = self._credential_recovery_count
            recovered_at = self._last_credential_recovery_at
        age = (
            None
            if recovered_at is None
            else max(0.0, float(now) - recovered_at)
        )
        recent = age is not None and age <= CREDENTIAL_RECOVERY_TTL_S
        return {
            "recovered_count": count,
            "recent": recent,
            "last_recovered_age_s": age if recent else None,
        }

    def enqueue_command(
        self,
        cmd: str,
        args: dict,
        peer: str | None = None,
        *,
        identity_payload: object = None,
        lease_token: str | None = None,
        operation_timeout_s: float = 0.0,
        internal: bool = False,
    ) -> tuple[int, dict]:
        safe_operation_timeout_s, timeout_valid = _safe_operation_timeout(
            operation_timeout_s
        )
        # Timeout validation stays after authorize to preserve lease-first ordering.
        if self.coordination is None or internal:
            return self._enqueue_command(
                cmd,
                args,
                peer,
                owner_client=None,
                owner_lease_id=None,
                operation_timeout_s=safe_operation_timeout_s,
            )

        try:
            client = ClientIdentity.from_payload(identity_payload)
        except ValueError:
            return 400, {"error": "invalid_identity"}
        safe_cmd = cmd if isinstance(cmd, str) else ""
        safe_peer = peer if peer is None or isinstance(peer, str) else ""
        if lease_token is not None and not isinstance(lease_token, str):
            return 403, {"error": "lease_invalid"}
        safe_lease_token = lease_token

        # Authorization may fsync the durable audit. It deliberately happens before
        # taking the queue lock. Exact commit is the only authority linearization
        # point and runs under the queue lock immediately before publication.
        decision = self.coordination.authorize(
            client, safe_lease_token, safe_cmd, safe_operation_timeout_s
        )
        if not decision.allowed:
            payload: dict[str, object] = {"error": decision.error}
            if decision.cleanup_degraded:
                payload["cleanup_degraded"] = list(decision.cleanup_degraded)
            if decision.error == "lease_required":
                blocked = self._version_block_fields(safe_peer)
                if blocked:
                    payload["version_state"] = blocked.get("state")
                    payload["expected"] = blocked.get("expected")
                    payload["got"] = blocked.get("got")
                    payload["detail"] = blocked.get("detail")
            return decision.http_status, payload

        retail_quarantine_reason = (
            self._retail_quarantine_reason()
            if command_requires_lease(safe_cmd)
            else None
        )
        if retail_quarantine_reason is not None:
            owner_session_id = decision.owner_session_id
            if (
                owner_session_id is None
                or decision.lease_id is None
                or decision.reservation_id is None
            ):
                return 409, {
                    "error": "retail_quarantine",
                    "reason": retail_quarantine_reason,
                }
            rejected = self.coordination.reject_reservation(
                owner_session_id,
                decision.lease_id,
                decision.reservation_id,
                "retail_quarantine",
            )
            rejected_payload: dict[str, object] = {
                "error": rejected.error,
                "reason": retail_quarantine_reason,
            }
            if rejected.cleanup_degraded:
                rejected_payload["cleanup_degraded"] = list(
                    rejected.cleanup_degraded
                )
            return rejected.http_status, rejected_payload

        payload = {}
        cleanup_degraded = list(decision.cleanup_degraded)
        reservation_owner = (
            (
                decision.owner_session_id,
                decision.lease_id,
                decision.reservation_id,
            )
            if decision.owner_session_id is not None
            and decision.lease_id is not None
            and decision.reservation_id is not None
            and command_requires_lease(safe_cmd)
            else None
        )
        command_accepted = False
        abort_reason = "enqueue_exception"

        def commit(command_id: int) -> bool:
            if reservation_owner is None:
                return True
            return self.coordination.commit_authorization(
                reservation_owner[0],
                reservation_owner[1],
                reservation_owner[2],
                command_id,
                safe_cmd,
                args,
            )

        try:
            if not timeout_valid:
                status, payload = 400, {"error": "bad_operation_timeout"}
            else:
                # Expiry may audit and run cleanup. Keep every side effect outside
                # the queue lock; exact commit below is memory-only and revalidates
                # the lease immediately before publication.
                cleanup_degraded.extend(self.coordination.expire_due())
                status, payload = self._enqueue_command(
                    safe_cmd,
                    args,
                    safe_peer,
                    owner_client=(
                        client if decision.owner_session_id is not None else None
                    ),
                    owner_lease_id=decision.lease_id,
                    operation_timeout_s=safe_operation_timeout_s,
                    commit=commit if reservation_owner is not None else None,
                )
            if status != 200:
                error = payload.get("error")
                if isinstance(error, str):
                    abort_reason = error
                return status, payload
            command_accepted = True
            return status, payload
        finally:
            if reservation_owner is not None and not command_accepted:
                cleanup_degraded.extend(
                    self.coordination.abort_reservation(
                        reservation_owner[0],
                        reservation_owner[1],
                        reservation_owner[2],
                        abort_reason,
                    )
                )
            if cleanup_degraded:
                existing_degradation = payload.get("cleanup_degraded", [])
                payload["cleanup_degraded"] = list(
                    dict.fromkeys([*existing_degradation, *cleanup_degraded])
                )

    def _version_block_fields(self, peer: str | None) -> dict[str, object]:
        """Peer version fields when the destination is blocked. Empty if ok.

        Used both on the 409 version_blocked path and attached to a
        lease_required 423 so a missing token does not hide a PBO mismatch.
        """
        if not peer or self.version_validator is None:
            return {}
        with self._lock:
            poll_version = self._poll_versions.get(peer)
        state = self.version_validator(poll_version)
        if state not in BLOCKED_VERSION_STATES:
            return {}
        got: object = None
        if isinstance(poll_version, str) and poll_version:
            got = poll_version.partition("~")[0]
        if poll_version is None:
            detail = "poll did not include ver="
        else:
            detail = f"bridge_version {got!r} != {EXPECTED_BRIDGE_VERSION!r}"
        return {
            "state": state,
            "expected": EXPECTED_BRIDGE_VERSION,
            "got": got,
            "detail": detail,
        }

    def _enqueue_command(
        self,
        cmd: str,
        args: dict,
        peer: str | None,
        *,
        owner_client: ClientIdentity | None,
        owner_lease_id: str | None,
        operation_timeout_s: float = 0.0,
        commit: Callable[[int], bool] | None = None,
    ) -> tuple[int, dict]:
        if cmd not in self.whitelisted_commands():
            return 400, {"error": "not_whitelisted"}
        if not isinstance(args, dict):
            return 400, {"error": "bad_args"}
        args_ok, args_error = validate_command_args(cmd, args)
        if not args_ok:
            return 400, {"error": args_error or "bad_args"}

        command_peer = peer_for_command(cmd)
        if peer is None:
            peer = command_peer
        if peer not in VALID_PEERS or peer != command_peer:
            return 400, {"error": "bad_peer"}

        # Version gate at the enqueue ingress (daemon authority, d): reject before
        # queueing when the target peer is on a blocked version. Active only when a
        # validator is wired (MCP/daemon path); the bare harness shim passes none,
        # so its back-compat behavior is unchanged. record_poll keeps the delivery
        # gate independently.
        if self.version_validator is not None:
            with self._lock:
                poll_version = self._poll_versions.get(peer)
            state = self.version_validator(poll_version)
            if state in BLOCKED_VERSION_STATES:
                blocked = self._version_block_fields(peer)
                payload: dict[str, object] = {"error": "version_blocked", "state": state}
                payload.update(blocked)
                return 409, payload

        if cmd == "exec_enforce":
            return self._enqueue_exec_enforce(
                args,
                peer,
                owner_client,
                owner_lease_id,
                operation_timeout_s,
                commit,
            )

        with self._lock:
            if self._stopping:
                return 409, {"error": "enqueue_cancelled"}
            fence_error_code, queue, fence_instance = self._enqueue_fence_target(
                peer, cmd
            )
            if fence_error_code is not None or queue is None:
                code = fence_error_code or "legacy_unbound"
                self._fence_reject_counts[code] = (
                    self._fence_reject_counts.get(code, 0) + 1
                )
                return fence_error(code)
            if self._peer_queue_len(peer) >= MAX_QUEUE:
                return 429, {"error": "queue_full"}

            command_id = self._next_id
            self._next_id += 1
            command = {"id": command_id, "cmd": cmd, "args": args}
            if commit is not None and not commit(command_id):
                return 409, {"error": "lease_invalid"}
            if owner_client is not None and owner_lease_id is not None:
                self._command_owner[command_id] = (owner_client, owner_lease_id)
            queue.append(command)
            enqueued_at = self._now()
            self._enqueued_at[command_id] = enqueued_at
            if operation_timeout_s > 0.0:
                self._operation_deadlines[command_id] = (
                    enqueued_at + operation_timeout_s
                )
            binding = (
                self._bindings.get(fence_instance) if fence_instance else None
            )
            self._seal_command(command_id, fence_instance, binding)
            if command_requires_lease(cmd) and (
                fence_instance is None
                or binding is None
                or binding.state != BINDING_BOUND
            ):
                self._unaccredited_mutation_enqueues += 1

        return 200, {"id": command_id, "peer": peer, "cmd": cmd}

    def _enqueue_exec_enforce(
        self,
        args: dict,
        peer: str,
        owner_client: ClientIdentity | None,
        owner_lease_id: str | None,
        operation_timeout_s: float,
        commit: Callable[[int], bool] | None = None,
    ) -> tuple[int, dict]:
        expr = args.get("expr", "")
        main_fn = args.get("main_fn", "")
        expr_text = expr if isinstance(expr, str) else ""
        main_fn_text = main_fn if isinstance(main_fn, str) else ""

        if self.exec_allowlist is None or self.exec_audit is None:
            return 403, {"error": "exec_not_allowed"}
        if expr_text == "" or expr_text not in self.exec_allowlist:
            try:
                self.exec_audit(expr_text, "denied", main_fn_text, None)
            except Exception:
                return 503, {"error": "audit_failed"}
            return 403, {"error": "exec_not_allowed"}

        # Reserve both capacity and id before the durable "allowed" audit. Every
        # enqueue path counts this reservation, so no concurrent command can consume
        # the promised slot while audit I/O runs outside the state lock.
        with self._lock:
            if self._stopping:
                return 409, {"error": "enqueue_cancelled"}
            fence_error_code, _queue, _fence_instance = self._enqueue_fence_target(
                peer, "exec_enforce"
            )
            if fence_error_code is not None:
                self._fence_reject_counts[fence_error_code] = (
                    self._fence_reject_counts.get(fence_error_code, 0) + 1
                )
                return fence_error(fence_error_code)
            if self._peer_queue_len(peer) >= MAX_QUEUE:
                return 429, {"error": "queue_full"}
            command_id = self._next_id
            self._next_id += 1
            self._exec_capacity_reserved[peer] += 1
            enqueue_generation = self._enqueue_generation

        try:
            self.exec_audit(expr_text, "allowed", main_fn_text, command_id)
        except Exception:
            with self._lock:
                self._exec_capacity_reserved[peer] -= 1
            return 503, {"error": "audit_failed"}

        command_args = dict(args)
        command_args["expr"] = expr_text
        command_args["main_fn"] = main_fn_text
        command = {"id": command_id, "cmd": "exec_enforce", "args": command_args}
        commit_failed = False
        capacity_lost = False
        fence_lost = False
        with self._lock:
            fence_error_code, queue, fence_instance = self._enqueue_fence_target(
                peer, "exec_enforce"
            )
            self._exec_capacity_reserved[peer] -= 1
            if fence_error_code is not None or queue is None:
                fence_lost = True
                if fence_error_code is not None:
                    self._fence_reject_counts[fence_error_code] = (
                        self._fence_reject_counts.get(fence_error_code, 0) + 1
                    )
            elif self._stopping or self._enqueue_generation != enqueue_generation:
                fence_lost = True
            elif self._peer_queue_len(peer) >= MAX_QUEUE:
                capacity_lost = True
            elif commit is not None and not commit(command_id):
                commit_failed = True
            else:
                queue.append(command)
                enqueued_at = self._now()
                self._enqueued_at[command_id] = enqueued_at
                if operation_timeout_s > 0.0:
                    self._operation_deadlines[command_id] = (
                        enqueued_at + operation_timeout_s
                    )
                if owner_client is not None and owner_lease_id is not None:
                    self._command_owner[command_id] = (owner_client, owner_lease_id)
                binding = (
                    self._bindings.get(fence_instance) if fence_instance else None
                )
                self._seal_command(command_id, fence_instance, binding)
                if (
                    fence_instance is None
                    or binding is None
                    or binding.state != BINDING_BOUND
                ):
                    self._unaccredited_mutation_enqueues += 1

        if commit_failed or capacity_lost or fence_lost:
            try:
                self.exec_audit(expr_text, "discarded", main_fn_text, command_id)
            except Exception:
                pass
        if capacity_lost:
            return 429, {"error": "queue_full"}
        if fence_lost:
            return 409, {"error": "enqueue_cancelled"}
        if commit_failed:
            return 409, {"error": "lease_invalid"}

        return 200, {"id": command_id, "peer": peer, "cmd": "exec_enforce"}

    def _rollback_enqueued(self, command_id: int) -> None:
        for key, queue in self._iter_mutable_queues():
            survivors = [
                command for command in queue if command.get("id") != command_id
            ]
            if key in self._legacy_queues:
                self._legacy_queues[key] = survivors
            else:
                self._bound_queues[key] = survivors
        self._enqueued_at.pop(command_id, None)
        self._operation_deadlines.pop(command_id, None)
        self._command_owner.pop(command_id, None)
        self._command_fence.pop(command_id, None)

    def abandon_command(
        self, command_id: int, reason: str = "tool_timeout"
    ) -> bool:
        discarded_exec: list[tuple[str, str, int]] = []
        finished_operations: list[
            tuple[ClientIdentity, str, int, str, str]
        ] = []
        owner_to_finish: tuple[ClientIdentity, str, int] | None = None
        with self._lock:
            existed = (
                command_id in self._enqueued_at
                or command_id in self._results
                or command_id in self._command_owner
                or command_id in self._fire_and_forget_ids
            )
            found_queued = False
            for key, queue in self._iter_mutable_queues():
                survivors: list[dict] = []
                for command in queue:
                    if command.get("id") == command_id:
                        self._mark_discarded(
                            command,
                            reason,
                            discarded_exec,
                            finished_operations,
                        )
                        found_queued = True
                    else:
                        survivors.append(command)
                if key in self._legacy_queues:
                    self._legacy_queues[key] = survivors
                else:
                    self._bound_queues[key] = survivors
            self._command_fence.pop(command_id, None)
            had_result = command_id in self._results
            owner = self._command_owner.pop(command_id, None)
            self._results.pop(command_id, None)
            self._enqueued_at.pop(command_id, None)
            self._operation_deadlines.pop(command_id, None)
            self._fire_and_forget_ids.discard(command_id)
            if owner is not None and not found_queued and not had_result:
                owner_to_finish = (owner[0], owner[1], command_id)

        for expr, main_fn, discarded_id in discarded_exec:
            try:
                if self.exec_audit is not None:
                    self.exec_audit(expr, "discarded", main_fn, discarded_id)
            except Exception:
                pass
        self._finish_operations(finished_operations)
        if owner_to_finish is not None and self.coordination is not None:
            self.coordination.finish_operation_exact(
                owner_to_finish[0].session_id,
                owner_to_finish[1],
                owner_to_finish[2],
                command_succeeded=False,
            )
        return existed or found_queued

    def reap_expired_commands(self, now: float | None = None) -> int:
        if now is None:
            now = self._now()
        with self._lock:
            expired = [
                command_id
                for command_id, deadline in self._operation_deadlines.items()
                if now >= deadline
            ]
        return sum(
            1
            for command_id in expired
            if self.abandon_command(command_id, "tool_timeout")
        )

    def whitelisted_commands(self) -> set[str]:
        commands = set(WHITELISTED_COMMANDS)
        if self.enable_exec_enforce:
            commands.update(EXEC_COMMANDS)
        return commands

    def record_poll(
        self,
        peer: str,
        version: str | None = None,
        instance: str | None = None,
        source_pid: int | None = None,
        source_creation_time: str | None = None,
    ) -> tuple[int, dict]:
        if peer not in VALID_PEERS:
            return 400, {"error": "bad_peer"}

        discarded_exec: list[tuple[str, str, int]] = []
        finished_operations: list[
            tuple[ClientIdentity, str, int, str, str]
        ] = []
        coordination = self.coordination
        version_state = (
            self.version_validator(version)
            if self.version_validator is not None
            else None
        )
        self.reap_expired_commands(self._now())
        token, token_class = classify_instance_token(instance)
        if source_pid is not None and source_creation_time is None:
            source_creation_time = self._lookup_creation_time(source_pid)

        with self._lock:
            now = self._now()
            prev_poll = self._last_poll_at.get(peer)
            self._poll_versions[peer] = version
            self._last_poll_at[peer] = now
            bind_label = BINDING_LEGACY
            accredited = False
            queue: list[dict] | None = None
            for stale_queue in list(self._legacy_queues.values()) + list(
                self._bound_queues.values()
            ):
                self._expire_stale_commands(
                    stale_queue, now, discarded_exec, finished_operations
                )

            if token_class == "instance_malformed":
                self._peer_last_class[peer] = "instance_malformed"
                bind_label = "instance_malformed"
            elif token is not None:
                self._seen_valid_inst_poll = True
                binding = self._bindings.get(token)
                if binding is None:
                    if token in self._retired_instances:
                        bind_label = "binding_retired"
                    elif not self._ever_bound:
                        bind_label = "unbound_after_restart"
                    else:
                        bind_label = "instance_unknown"
                    self._peer_last_class[peer] = bind_label
                elif binding.state == BINDING_RETIRED:
                    bind_label = "binding_retired"
                    self._peer_last_class[peer] = bind_label
                elif not self._peer_covers(binding, peer):
                    bind_label = "instance_role_mismatch"
                    self._peer_last_class[peer] = bind_label
                elif source_pid is None:
                    # TCP miss this tick. Binding stays as-is;
                    # commands are not delivered (fail-closed).
                    bind_label = "instance_unattributed"
                    self._peer_last_class[peer] = bind_label
                elif binding.state == BINDING_STARTING:
                    bind_label = BINDING_STARTING
                    self._peer_last_class[peer] = bind_label
                else:
                    bind_label = self._note_poll_pid_locked(
                        binding, source_pid, now, source_creation_time
                    )
                    self._peer_last_class[peer] = bind_label
                    if bind_label == BINDING_BOUND:
                        accredited = True
                        self._bound_last_poll_at[peer] = now
                        queue = self._bound_queues.setdefault(token, [])
                        self._expire_stale_commands(
                            queue, now, discarded_exec, finished_operations
                        )
            else:
                self._peer_last_class[peer] = BINDING_LEGACY
                queue = self._legacy_queues[peer]
                if prev_poll is not None and (now - prev_poll) > PEER_RECONNECT_GAP_S:
                    self._discard_queue(
                        queue,
                        "peer_reconnect_flush",
                        discarded_exec,
                        finished_operations,
                    )
                self._expire_stale_commands(
                    queue, now, discarded_exec, finished_operations
                )

            if not accredited:
                self._unaccredited_poll_counts[bind_label] = (
                    self._unaccredited_poll_counts.get(bind_label, 0) + 1
                )

            if version_state in {"legacy_blocked", "version_mismatch"}:
                return 200, {"commands": [], "delay_ms": 0, "bind": bind_label}

            if queue is None:
                return 200, {"commands": [], "delay_ms": 0, "bind": bind_label}

            deliver_queue = queue
            deliver_accredited = accredited

        commands: list[dict] = []
        delay_ms = 0
        while True:
            with self._lock:
                queue_ref = deliver_queue
                snapshot = list(queue_ref)
            has_mutation = any(
                isinstance(command.get("cmd"), str)
                and command_requires_lease(command["cmd"])
                for command in snapshot
            )
            if coordination is not None and has_mutation:
                coordination.expire_due()
            retail_quarantined = (
                self._retail_quarantined()
                if coordination is not None and has_mutation
                else False
            )

            with self._lock:
                queue = deliver_queue
                if (
                    queue is not queue_ref
                    or len(queue) < len(snapshot)
                    or any(
                        queue[index] is not command
                        for index, command in enumerate(snapshot)
                    )
                ):
                    continue

                remaining: list[dict] = []
                for command in snapshot:
                    command_id = command.get("id")
                    command_name = command.get("cmd")
                    if peer_for_command(str(command_name or "")) != peer:
                        remaining.append(command)
                        continue
                    # Second layer of the same fail-closed rule, and deliberately
                    # redundant: enqueue_command already refuses a mutation from an
                    # unbound peer with 409 legacy_unbound, so this branch is not
                    # reachable through the normal path. Verified by mutation on
                    # 2026-08-20 -- disabling it leaves the whole suite green, which
                    # says "unreachable", NOT "unneeded". It is what still holds if a
                    # future ingress path forgets the check, so do not delete it as
                    # dead code.
                    if (
                        isinstance(command_name, str)
                        and command_requires_lease(command_name)
                        and not deliver_accredited
                    ):
                        remaining.append(command)
                        continue
                    if (
                        isinstance(command_id, int)
                        and isinstance(command_name, str)
                        and command_requires_lease(command_name)
                        and coordination is not None
                    ):
                        owner = self._command_owner.get(command_id)
                        discard_reason = ""
                        if retail_quarantined:
                            discard_reason = "retail_quarantine"
                        elif (
                            command_name == "vehicle_release"
                            and command_id in self._fire_and_forget_ids
                        ):
                            pass
                        elif owner is None:
                            discard_reason = "authority_missing"
                        elif not coordination.claim_dispatch(
                            owner[0].session_id,
                            owner[1],
                            command_id,
                            command_name,
                        ):
                            discard_reason = "lease_inactive"
                        if discard_reason:
                            self._mark_discarded(
                                command,
                                discard_reason,
                                discarded_exec,
                                finished_operations,
                            )
                            continue
                    wire_command = dict(command)
                    wire_command.pop("owner_session_id", None)
                    wire_command.pop("owner_lease_id", None)
                    commands.append(wire_command)
                queue[:] = remaining + queue[len(snapshot) :]
                if commands and self._poll_delay_ms > 0:
                    delay_ms = self._poll_delay_ms
                    self._poll_delay_ms = 0
                break

        for expr, main_fn, command_id in discarded_exec:
            try:
                self.exec_audit(expr, "discarded", main_fn, command_id)
            except Exception:
                pass
        self._finish_operations(finished_operations)

        return 200, {"commands": commands, "delay_ms": delay_ms, "bind": bind_label}

    def _note_poll_pid_locked(
        self,
        binding: Binding,
        source_pid: int,
        now: float,
        source_creation_time: str | None = None,
    ) -> str:
        if binding.pid is None:
            return BINDING_STARTING
        binding.last_present_at[source_pid] = now
        binding.presented_pids.add(source_pid)
        identity_mismatch = source_pid != binding.pid
        source_norm = normalize_creation_time_utc(source_creation_time)
        bound_norm = normalize_creation_time_utc(binding.creation_time_utc)
        if source_norm is None or bound_norm is None:
            if identity_mismatch:
                if binding.state != BINDING_AMBIGUOUS:
                    self._station_epoch += 1
                    binding.epoch = self._station_epoch
                binding.state = BINDING_AMBIGUOUS
                return BINDING_AMBIGUOUS
            binding.state = BINDING_UNREADABLE
            return BINDING_UNREADABLE
        if source_norm != bound_norm:
            identity_mismatch = True
        if identity_mismatch:
            if binding.state != BINDING_AMBIGUOUS:
                self._station_epoch += 1
                binding.epoch = self._station_epoch
            binding.state = BINDING_AMBIGUOUS
            return BINDING_AMBIGUOUS
        if binding.state == BINDING_UNREADABLE:
            if source_pid == binding.pid:
                binding.state = BINDING_BOUND
                binding.presented_pids = {binding.pid}
                return BINDING_BOUND
            binding.state = BINDING_AMBIGUOUS
            return BINDING_AMBIGUOUS
        if binding.state == BINDING_AMBIGUOUS:
            live = {
                pid
                for pid, seen_at in binding.last_present_at.items()
                if (now - seen_at) <= PEER_RECONNECT_GAP_S
            }
            if live == {binding.pid} and source_pid == binding.pid:
                binding.state = BINDING_BOUND
                binding.presented_pids = {binding.pid}
                return BINDING_BOUND
            return BINDING_AMBIGUOUS
        if binding.state == BINDING_BOUND and source_pid == binding.pid:
            return BINDING_BOUND
        return binding.state

    def _discard_queue(
        self,
        queue: list[dict],
        reason: str,
        discarded_exec: list[tuple[str, str, int]],
        finished_operations: list[tuple[ClientIdentity, str, int, str, str]],
    ) -> None:
        # Caller holds self._lock. Empties the queue, recording a failed result
        # per command so its enqueuer's /await resolves instead of hanging.
        for command in queue:
            self._mark_discarded(
                command, reason, discarded_exec, finished_operations
            )
        queue.clear()

    def _expire_stale_commands(
        self,
        queue: list[dict],
        now: float,
        discarded_exec: list[tuple[str, str, int]],
        finished_operations: list[tuple[ClientIdentity, str, int, str, str]],
    ) -> None:
        # Caller holds self._lock. Drops commands older than COMMAND_TTL_S in place.
        survivors: list[dict] = []
        for command in queue:
            command_id = command.get("id")
            enqueued_at = self._enqueued_at.get(command_id)
            if enqueued_at is not None and (now - enqueued_at) > COMMAND_TTL_S:
                self._mark_discarded(
                    command,
                    "stale_discarded",
                    discarded_exec,
                    finished_operations,
                )
            else:
                survivors.append(command)
        queue[:] = survivors

    def _mark_discarded(
        self,
        command: dict,
        reason: str,
        discarded_exec: list[tuple[str, str, int]],
        finished_operations: list[tuple[ClientIdentity, str, int, str, str]],
    ) -> None:
        # Caller holds self._lock. Records the failed result and, for exec_enforce,
        # collects an audit tuple to be written by the caller outside the lock.
        command_id = command.get("id")
        if command_id is None:
            return
        fire_and_forget = command_id in self._fire_and_forget_ids
        if fire_and_forget:
            self._fire_and_forget_ids.discard(command_id)
            self._results.pop(command_id, None)
        elif command_id not in self._results:
            self._results[command_id] = {"id": command_id, "ok": False, "error": reason}
            self._trim_results_locked()
        self._enqueued_at.pop(command_id, None)
        self._operation_deadlines.pop(command_id, None)
        self._command_fence.pop(command_id, None)
        # Discard is terminal for owner attribution. A leftover mapping
        # makes pending_for_owner over-count until MAX_RESULTS eviction.
        owner = self._command_owner.pop(command_id, None)
        command_name = command.get("cmd")
        if owner is not None and isinstance(command_name, str):
            finished_operations.append(
                (owner[0], owner[1], command_id, command_name, reason)
            )
        if command.get("cmd") == "exec_enforce" and self.exec_audit is not None:
            args = command.get("args", {})
            if isinstance(args, dict):
                expr = args.get("expr", "")
                main_fn = args.get("main_fn", "")
            else:
                expr = ""
                main_fn = ""
            discarded_exec.append((expr, main_fn, command_id))

    def _finish_operations(
        self,
        operations: list[tuple[ClientIdentity, str, int, str, str]],
    ) -> None:
        coordination = self.coordination
        if coordination is None:
            return
        for client, lease_id, command_id, command, reason in operations:
            degraded = coordination.discard_committed(
                client, lease_id, command_id, command, reason
            )
            if not degraded:
                continue
            with self._lock:
                result = self._results.get(command_id)
                if result is not None:
                    current = result.get("cleanup_degraded")
                    values = list(current) if isinstance(current, list) else []
                    for item in degraded:
                        if item not in values:
                            values.append(item)
                    result["cleanup_degraded"] = values
                self._audit_degraded_count += 1

    def _evict_result_locked(self, command_id: int) -> None:
        # Caller holds self._lock. Drops one stored result and the metadata
        # take_result(remove=True) would have dropped. finish_operation_exact
        # already ran at store time (or discard already queued it).
        self._results.pop(command_id, None)
        self._enqueued_at.pop(command_id, None)
        self._operation_deadlines.pop(command_id, None)
        self._command_owner.pop(command_id, None)

    def _trim_results_locked(self) -> None:
        # Caller holds self._lock. Dict insertion order is store order; the
        # oldest unread result is the first key.
        while len(self._results) > MAX_RESULTS:
            oldest_id = next(iter(self._results))
            self._evict_result_locked(oldest_id)

    def store_result(
        self, body: dict, instance: str | None = None
    ) -> tuple[int, dict]:
        try:
            command_id = int(body.get("id"))
        except (TypeError, ValueError):
            return 400, {"error": "bad_id"}

        t_result = self._now()
        stored = dict(body)
        meta: dict[str, float] = {"t_result": t_result}

        owner_operation: tuple[ClientIdentity, str, int] | None = None
        discarded = False
        with self._lock:
            fence = self._command_fence.get(command_id)
            if fence is not None:
                target_instance, target_epoch, _target_pid = fence
                presented = instance or ""
                if target_instance and presented != target_instance:
                    self._audit_fence(
                        "late_result_fenced",
                        command_id,
                        target_instance,
                        instance,
                        extra={
                            "expected_epoch": target_epoch,
                            "origin_epoch": self._station_epoch,
                        },
                    )
                    return 200, {
                        "ok": True,
                        "id": command_id,
                        "ok_value": None,
                        "discarded": True,
                    }
                if (
                    target_instance
                    and presented == target_instance
                    and target_epoch != self._station_epoch
                ):
                    self._audit_fence(
                        "late_result_same_instance",
                        command_id,
                        target_instance,
                        instance,
                        extra={
                            "expected_epoch": target_epoch,
                            "origin_epoch": self._station_epoch,
                        },
                    )
            deadline = self._operation_deadlines.get(command_id)
            known = (
                command_id in self._enqueued_at
                or command_id in self._fire_and_forget_ids
            )
            if not known or (deadline is not None and t_result >= deadline):
                discarded = True
                self._fire_and_forget_ids.discard(command_id)
                self._results.pop(command_id, None)
                self._enqueued_at.pop(command_id, None)
                self._operation_deadlines.pop(command_id, None)
                owner = self._command_owner.pop(command_id, None)
                if owner is not None:
                    owner_operation = (owner[0], owner[1], command_id)
            elif command_id in self._fire_and_forget_ids:
                self._fire_and_forget_ids.discard(command_id)
                self._results.pop(command_id, None)
                self._enqueued_at.pop(command_id, None)
                self._operation_deadlines.pop(command_id, None)
            else:
                t_enqueue = self._enqueued_at.get(command_id)
                if t_enqueue is not None:
                    meta["t_enqueue"] = t_enqueue
                    meta["rtt_s"] = t_result - t_enqueue
                stored["_server"] = meta
                self._results[command_id] = stored
                self._trim_results_locked()
                owner = self._command_owner.get(command_id)
                if owner is not None:
                    owner_operation = (owner[0], owner[1], command_id)

        if owner_operation is not None and self.coordination is not None:
            ok_value = stored.get("ok")
            self.coordination.finish_operation_exact(
                owner_operation[0].session_id,
                owner_operation[1],
                owner_operation[2],
                command_succeeded=(
                    ok_value is True
                    or (
                        isinstance(ok_value, int)
                        and not isinstance(ok_value, bool)
                        and ok_value == 1
                    )
                ),
            )

        response = {"ok": True, "id": command_id, "ok_value": stored.get("ok")}
        if discarded:
            response["discarded"] = True
        return 200, response

    def take_result(self, command_id: int, remove: bool = False) -> dict | None:
        self.reap_expired_commands()
        owner_operation: tuple[ClientIdentity, str, int] | None = None
        with self._lock:
            if remove:
                result = self._results.pop(command_id, None)
                # A polling /await?remove=1 must consume ownership/pin metadata
                # only when it actually consumes a result.  While pending, the
                # command still owns its operation pin and remains attributable
                # for owner-scoped cleanup/status.
                if result is not None:
                    self._enqueued_at.pop(command_id, None)
                    self._operation_deadlines.pop(command_id, None)
                    owner = self._command_owner.pop(command_id, None)
                    if owner is not None:
                        owner_operation = (owner[0], owner[1], command_id)
            else:
                result = self._results.get(command_id)
        if owner_operation is not None:
            self.coordination.finish_operation_exact(
                owner_operation[0].session_id,
                owner_operation[1],
                owner_operation[2],
            )
        return result

    def cancel_owner_pending(
        self, session_id: str, reason: str, lease_id: str | None = None
    ) -> dict[str, int]:
        discarded_exec: list[tuple[str, str, int]] = []
        finished_operations: list[
            tuple[ClientIdentity, str, int, str, str]
        ] = []
        cancelled = 0
        with self._lock:
            self._enqueue_generation += 1
            for key, queue in self._iter_mutable_queues():
                survivors: list[dict] = []
                for command in queue:
                    command_id = command.get("id")
                    owner = self._command_owner.get(command_id)
                    if (
                        owner is not None
                        and owner[0].session_id == session_id
                        and (lease_id is None or owner[1] == lease_id)
                    ):
                        self._mark_discarded(
                            command, reason, discarded_exec, finished_operations
                        )
                        cancelled += 1
                    else:
                        survivors.append(command)
                if key in self._legacy_queues:
                    self._legacy_queues[key] = survivors
                else:
                    self._bound_queues[key] = survivors

        for expr, main_fn, command_id in discarded_exec:
            try:
                if self.exec_audit is not None:
                    self.exec_audit(expr, "discarded", main_fn, command_id)
            except Exception:
                pass
        self._finish_operations(finished_operations)
        return {"cancelled": cancelled}

    def pending_for_owner(self, session_id: str) -> int:
        with self._lock:
            return sum(
                1
                for owner in self._command_owner.values()
                if owner[0].session_id == session_id
            )

    def cleanup_owner(
        self,
        session_id: str,
        lease_id: str,
        reason: str,
        vehicle_active: bool,
    ) -> dict[str, object]:
        coordination = self.coordination
        if not isinstance(reason, str) or not isinstance(vehicle_active, bool):
            return {
                "cancelled": 0,
                "vehicle_release_enqueued": 0,
                "cleanup_degraded": ["cleanup_invalid"],
            }
        if (
            coordination is not None
            and not coordination.cleanup_authority_active(session_id, lease_id)
        ):
            return {
                "cancelled": 0,
                "vehicle_release_enqueued": 0,
                "cleanup_degraded": ["cleanup_fenced"],
            }
        cleanup: dict[str, object] = self.cancel_owner_pending(
            session_id, reason, lease_id
        )
        cleanup["vehicle_release_enqueued"] = 0
        if not vehicle_active:
            return cleanup

        if self._retail_quarantined():
            cleanup["cleanup_degraded"] = ["retail_quarantine"]
            return cleanup

        # Keep append + fire-and-forget tracking atomic with /poll. This command is
        # internal and deliberately has no owner mapping or externally awaited result.
        with self._lock:
            if (
                coordination is not None
                and not coordination.cleanup_authority_active(session_id, lease_id)
            ):
                cleanup["cleanup_degraded"] = ["cleanup_fenced"]
                return cleanup
            status, payload = self.enqueue_command(
                "vehicle_release", {}, peer="client", internal=True
            )
            if status == 200:
                command_id = payload["id"]
                self._fire_and_forget_ids.add(command_id)
                cleanup["vehicle_release_enqueued"] = 1
            else:
                cleanup["cleanup_degraded"] = ["vehicle_release_failed"]
        return cleanup

    def _retail_quarantined(self) -> bool:
        return self._retail_quarantine_reason() is not None

    def _retail_quarantine_reason(self) -> str | None:
        probe = self.retail_probe
        if probe is None:
            if self.coordination is not None:
                return "no_probe"
            return None
        try:
            result = probe()
        except Exception:
            return "probe_error"
        if not isinstance(result, dict):
            return "probe_malformed"
        known = result.get("known")
        if known is False:
            return "probe_unknown"
        if known is not True:
            return "probe_malformed"
        processes = result.get("processes")
        if not isinstance(processes, list):
            return "probe_malformed"
        if processes:
            return "retail_present"
        return None

    def set_poll_delay(self, delay_ms: int) -> tuple[int, dict]:
        if delay_ms < 0 or delay_ms > 5000:
            return 400, {"error": "bad_ms_range"}

        with self._lock:
            self._poll_delay_ms = delay_ms

        return 200, {"ok": True, "ms": delay_ms}

    def status_snapshot(self, now: float | None = None) -> dict:
        if now is None:
            now = self._now()
        self.reap_expired_commands(now)
        with self._lock:
            last_poll_at = dict(self._last_poll_at)
            versions = dict(self._poll_versions)
            results_pending = len(self._results)
            last_client_request_at = self._last_client_request_at
            audit_degraded_count = self._audit_degraded_count
            peers = {}
            for peer in sorted(VALID_PEERS):
                poll_at = last_poll_at.get(peer)
                bind_state, prefix, bound_age, depth = self._peer_status_view(
                    peer, now
                )
                peers[peer] = {
                    "last_poll_at": poll_at,
                    "last_poll_age_s": (
                        None if poll_at is None else max(0.0, now - poll_at)
                    ),
                    "queue_depth": depth,
                    "version": versions.get(peer),
                    "binding_state": bind_state,
                    "instance_prefix": prefix,
                    "bound_last_poll_age_s": bound_age,
                }
            rejects = {code: 0 for code in FENCE_MUTATION_REJECT_CODES}
            rejects.update(
                {code: int(count) for code, count in self._fence_reject_counts.items()}
            )
            fence = {
                "mutation_rejects_by_code": rejects,
                "unaccredited_mutation_enqueues": int(
                    self._unaccredited_mutation_enqueues
                ),
                "unaccredited_polls_by_class": dict(self._unaccredited_poll_counts),
            }
        return {
            "peers": peers,
            "results_pending": results_pending,
            "last_client_request_at": last_client_request_at,
            "audit_degraded_count": audit_degraded_count,
            "fence": fence,
        }

    def _peer_status_view(
        self, peer: str, now: float
    ) -> tuple[str, str | None, float | None, int]:
        active = self._active_bindings_for_peer(peer)
        bound = [binding for binding in active if binding.state == BINDING_BOUND]
        ambiguous = [
            binding for binding in active if binding.state == BINDING_AMBIGUOUS
        ]
        starting = [
            binding for binding in active if binding.state == BINDING_STARTING
        ]
        unreadable = [
            binding for binding in active if binding.state == BINDING_UNREADABLE
        ]
        chosen: Binding | None = None
        if ambiguous:
            chosen = ambiguous[0]
            state = BINDING_AMBIGUOUS
        elif unreadable:
            chosen = unreadable[0]
            state = BINDING_UNREADABLE
        elif bound:
            chosen = bound[0]
            state = BINDING_BOUND
        elif starting:
            chosen = starting[0]
            state = BINDING_STARTING
        else:
            state = self._peer_last_class.get(peer, BINDING_LEGACY)
            chosen = None
        prefix = instance_prefix(chosen.instance) if chosen is not None else None
        bound_at = self._bound_last_poll_at.get(peer)
        bound_age = None if bound_at is None else max(0.0, now - bound_at)
        if chosen is not None and chosen.state == BINDING_BOUND:
            queue = self._bound_queues.get(chosen.instance, [])
            depth = sum(
                1
                for command in queue
                if peer_for_command(str(command.get("cmd") or "")) == peer
            )
        else:
            depth = len(self._legacy_queues.get(peer, []))
        return state, prefix, bound_age, depth

    def cancel_pending(self, reason: str = "server_stopping") -> None:
        discarded_exec: list[tuple[str, str, int]] = []
        finished_operations: list[
            tuple[ClientIdentity, str, int, str, str]
        ] = []
        with self._lock:
            self._enqueue_generation += 1
            self._stopping = True
            for _key, queue in self._iter_mutable_queues():
                self._discard_queue(
                    queue, reason, discarded_exec, finished_operations
                )
        for expr, main_fn, command_id in discarded_exec:
            try:
                if self.exec_audit is not None:
                    self.exec_audit(expr, "discarded", main_fn, command_id)
            except Exception:
                pass
        self._finish_operations(finished_operations)

    def resume_accepting(self) -> None:
        with self._lock:
            if self._stopping:
                self._enqueue_generation += 1
                self._stopping = False


class Handler(BaseHTTPRequestHandler):
    server_version = "DayZMCPPOC/0.1"
    # Socket deadline for one accepted connection. socketserver applies it with
    # connection.settimeout(), so every blocking recv/send on that socket is
    # bounded — including the request-line read that otherwise holds a worker
    # thread forever when a local client connects and sends nothing. 35s sits
    # strictly above the POST /wait long-poll ceiling so a max-length wait still
    # has headroom on the same socket.
    timeout = 35.0

    def log_message(self, format: str, *args: object) -> None:
        return

    @property
    def state(self) -> ServerState:
        return self.server.state  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        if not self._authorized(qs):
            return

        if parsed.path == "/poll":
            self._handle_poll(qs)
            return

        if parsed.path == "/await":
            self.state.touch_client()
            self._handle_await(qs)
            return

        if parsed.path == "/status":
            self.state.touch_client()
            self._handle_status()
            return

        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        if not self._authorized(qs):
            return

        if parsed.path == "/enqueue":
            self.state.touch_client()
            self._handle_enqueue()
            return

        session_action = SESSION_ROUTES.get(parsed.path)
        if session_action is not None:
            self.state.touch_client()
            self._handle_session(session_action)
            return


        lifecycle_action = LIFECYCLE_ROUTES.get(parsed.path)
        if lifecycle_action is not None:
            self.state.touch_client()
            self._handle_lifecycle(lifecycle_action)
            return

        admin_action = ADMIN_ROUTES.get(parsed.path)
        if admin_action is not None:
            self.state.touch_client()
            self._handle_admin(admin_action)
            return

        if parsed.path == "/result":
            self._handle_result(qs)
            return

        if parsed.path == "/set_poll_delay":
            self._handle_set_poll_delay()
            return

        self._json(404, {"error": "not_found"})

    def _authorized(self, qs: dict[str, list[str]]) -> bool:
        supplied = qs.get("key", [""])[0]
        if not hmac.compare_digest(supplied, self.state.key):
            self._json(401, {"error": "unauthorized"})
            return False
        if (
            self.headers.get(daemon_credential.RETRY_HEADER_NAME)
            == daemon_credential.RETRY_HEADER_VALUE
        ):
            self.state.record_credential_recovery()
        return True

    def _read_json(self) -> dict | None:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            self._json(400, {"error": "bad_content_length"})
            return None
        if length < 0:
            self._json(400, {"error": "bad_content_length"})
            return None
        if length > MAX_BODY_BYTES:
            # HTTP/1.0, no keep-alive: 413 without reading the body is safe.
            # A client still sending >1 MiB may see RST instead of the 413 body.
            self._json(413, {"error": "body_too_large"})
            return None

        raw = self.rfile.read(length)
        if len(raw) != length:
            self._json(400, {"error": "bad_body_length"})
            return None

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            self._json(400, {"error": "bad_json"})
            return None

        try:
            body = json.loads(text or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad_json"})
            return None

        if not isinstance(body, dict):
            self._json(400, {"error": "bad_json_type"})
            return None

        return body

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _log(self, message: str) -> None:
        sink = getattr(self.server, "log_sink", _default_log_sink)  # type: ignore[attr-defined]
        sink(message)

    def _handle_enqueue(self) -> None:
        body = self._read_json()
        if body is None:
            return

        cmd = body.get("cmd")
        args = body.get("args", {})
        peer = body.get("peer")
        status, payload = self.state.enqueue_command(
            cmd,
            args,
            peer,
            identity_payload=body.get("identity"),
            lease_token=body.get("lease_token"),
            operation_timeout_s=body.get("operation_timeout_s", 0.0),
        )
        payload = self._persist_coordination(payload)
        if status != 200:
            self._json(status, payload)
            return

        self._log(f"ENQUEUE id={payload['id']} peer={payload['peer']} cmd={payload['cmd']}")
        response = {"id": payload["id"]}
        if "cleanup_degraded" in payload:
            response["cleanup_degraded"] = payload["cleanup_degraded"]
        self._json(200, response)

    def _handle_session(self, action: str) -> None:
        body = self._read_json()
        if body is None:
            return
        coordination = self.state.coordination
        if coordination is None:
            self._json(404, {"error": "not_found"})
            return
        try:
            client = ClientIdentity.from_payload(body.get("identity"))
        except ValueError:
            self._json(400, {"error": "invalid_identity"})
            return

        if action == "acquire":
            purpose = body.get("purpose")
            if not isinstance(purpose, str) or not purpose.strip():
                self._json(400, {"error": "bad_purpose"})
                return
            operation_id = body.get("operation_id")
            if operation_id is not None and not isinstance(operation_id, str):
                self._json(400, {"error": "bad_operation_id"})
                return
            status, payload = coordination.acquire(
                client, purpose.strip(), operation_id
            )
        elif action == "enqueue":
            purpose = body.get("purpose")
            if not isinstance(purpose, str) or not purpose.strip():
                self._json(400, {"error": "bad_purpose"})
                return
            status, payload = coordination.enqueue(
                client, purpose.strip(), body.get("operation_id")
            )
        elif action == "wait":
            timeout_s = body.get("timeout_s", 30.0)
            if (
                not _is_real_number(timeout_s)
                or float(timeout_s) < 0.0
                or float(timeout_s) > 30.0
            ):
                self._json(400, {"error": "bad_wait_timeout"})
                return
            ticket = body.get("ticket")
            if not isinstance(ticket, str) or not ticket:
                self._json(403, {"error": "ticket_invalid"})
                return
            status, payload = coordination.wait(client, ticket, float(timeout_s))
        elif action == "cancel":
            status, payload = coordination.cancel(client, body.get("ticket"))
        elif action == "cancel-operation":
            status, payload = coordination.cancel_operation(
                client, body.get("operation_id")
            )
        elif action == "heartbeat":
            status, payload = coordination.heartbeat(client, body.get("lease_token"))
        elif action == "release":
            status, payload = coordination.release(client, body.get("lease_token"))
        else:
            status = 200
            payload = coordination.status(client)
            payload["daemon_generation"] = self.state.daemon_generation
            payload["pending_commands"] = self.state.pending_for_owner(
                client.session_id
            )
            wait_flag = body.get("box_wait") is True
            done_flag = body.get("box_wait_done") is True
            claim_flag = body.get("box_wait_claim") is True
            ticket = body.get("box_ticket")
            if wait_flag or done_flag or claim_flag or ticket:
                payload.update(
                    coordination.box_wait_touch(
                        client,
                        ticket,
                        done=done_flag,
                        claim=claim_flag,
                    )
                )
            payload["box"] = _box_payload(self.state)

        payload = self._persist_coordination(payload)
        self._log(
            "SESSION "
            f"event={action} status={status} "
            f"lease_id={payload.get('lease_id', '')} "
            f"ticket={payload.get('ticket', '')}"
        )
        self._json(status, payload)

    def _persist_coordination(self, payload: dict) -> dict:
        coordination = self.state.coordination
        store = self.state.coordination_store
        if coordination is None or store is None:
            return payload
        try:
            persisted_fn = getattr(store, "persisted_revision", None)
            if callable(persisted_fn):
                persisted = persisted_fn()
                if persisted is not None and coordination.durable_revision() <= persisted:
                    return payload
            snapshot = coordination.snapshot_payload()
            writer = getattr(store, "write_coordination")
            writer(snapshot)
            return payload
        except Exception:
            result = dict(payload)
            degraded = result.get("cleanup_degraded", [])
            if not isinstance(degraded, list):
                degraded = []
            result["cleanup_degraded"] = list(dict.fromkeys([*degraded, "snapshot_failed"]))
            return result

    def _handle_lifecycle(self, action: str) -> None:
        body = self._read_json()
        if body is None:
            return
        lifecycle = self.state.lifecycle
        if lifecycle is None:
            self._json(404, {"error": "not_found"})
            return
        try:
            client = ClientIdentity.from_payload(body.get("identity"))
        except ValueError:
            self._json(400, {"error": "invalid_identity"})
            return
        token = body.get("lease_token")
        if token is not None and not isinstance(token, str):
            self._json(403, {"error": "lease_invalid"})
            return
        if action == "start":
            result = lifecycle.start_run(client, token, body.get("request"))
        elif action == "ack":
            result = lifecycle.ack_run(
                client,
                token,
                body.get("run_id"),
                body.get("launch_operation_id"),
            )
        elif action == "stop":
            result = lifecycle.stop_run(client, token, body.get("run_id"))
        elif action == "adopt":
            result = lifecycle.adopt_run(client, token, body.get("run_id"))
        elif action == "reap":
            result = lifecycle.reap_dead_run(client, token, body.get("run_id"))
        else:
            result = lifecycle.status(client)
            requested_run_id = body.get("run_id")
            if requested_run_id is not None:
                if not isinstance(requested_run_id, str) or not requested_run_id:
                    self._json(400, {"error": "invalid_run_id"})
                    return
                result = dict(result)
                result["runs"] = [
                    run
                    for run in result.get("runs", [])
                    if isinstance(run, dict) and run.get("run_id") == requested_run_id
                ]
        result = dict(result)
        status = int(result.pop("_http_status", 200))
        result = self._persist_coordination(result)
        self._json(status, result)

    def _handle_admin(self, action: str) -> None:
        body = self._read_json()
        if body is None:
            return
        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            self._json(400, {"error": "invalid_reason"})
            return
        if action == "audit-repair":
            fault_id = body.get("fault_id")
            if not isinstance(fault_id, str) or not fault_id:
                self._json(400, {"error": "invalid_fault_id"})
                return
            if body.get("confirmation") != f"REPAIR {fault_id}":
                self._json(403, {"error": "confirmation_required"})
                return
            status, result = _repair_coordination_audit_fault(
                self.state, fault_id
            )
            self._json(status, result)
            return
        if action == "lifecycle-recovery-repair":
            fault_id = body.get("fault_id")
            expected_head = body.get("expected_head_sha256")
            if (
                not isinstance(fault_id, str)
                or not fault_id
                or not isinstance(expected_head, str)
                or len(expected_head) != 64
                or len(reason.strip()) > 160
            ):
                self._json(400, {"error": "invalid_lifecycle_recovery_repair"})
                return
            if body.get("confirmation") != f"REPAIR LIFECYCLE {fault_id}":
                self._json(403, {"error": "confirmation_required"})
                return
            status, result = _repair_lifecycle_recovery_fault(
                self.state, fault_id, expected_head
            )
            self._json(status, result)
            return
        if action == "release":
            lease_id = body.get("lease_id")
            if body.get("confirmation") != f"FORCE {lease_id}":
                self._json(403, {"error": "confirmation_required"})
                return
            coordination = self.state.coordination
            if coordination is None:
                self._json(404, {"error": "not_found"})
                return
            status, result = coordination.admin_release(lease_id, reason)
            self._json(status, self._persist_coordination(result))
            return

        lifecycle = self.state.lifecycle
        if lifecycle is None:
            self._json(404, {"error": "not_found"})
            return
        run_id, pid = body.get("run_id"), body.get("pid")
        empty = body.get("empty", False)
        if not isinstance(empty, bool):
            self._json(400, {"error": "invalid_reconcile_request"})
            return
        expected_confirmation = (
            f"FORCE {run_id} EMPTY" if empty else f"FORCE {run_id} {pid}"
        )
        if body.get("confirmation") != expected_confirmation:
            self._json(403, {"error": "confirmation_required"})
            return
        if empty:
            result = dict(
                lifecycle.admin_reconcile(run_id, pid, reason, empty=True)
            )
        else:
            result = dict(lifecycle.admin_reconcile(run_id, pid, reason))
        status = int(result.pop("_http_status", 200))
        self._json(status, result)


    def _handle_await(self, qs: dict[str, list[str]]) -> None:
        try:
            command_id = int(qs.get("id", [""])[0])
        except ValueError:
            self._json(400, {"error": "bad_id"})
            return

        # remove=1 consumes the result on delivery so client-mode proxies do not
        # leak one _results entry per command in the shared daemon. Default keeps
        # the non-destructive read for back-compat with the harness.
        remove = qs.get("remove", ["0"])[0] == "1"
        result = self.state.take_result(command_id, remove=remove)
        if result is None:
            self._json(200, {"status": "pending"})
            return

        self._json(200, {"status": "done", "result": result})

    def _handle_status(self) -> None:
        # Authenticated health/status read used by client bridge_status and by the
        # client/daemon discovery health-probe. With a status_provider wired (daemon)
        # it returns the rich version-aware status; bare loopback returns the raw
        # snapshot so the endpoint still answers for discovery.
        provider = getattr(self.server, "status_provider", None)  # type: ignore[attr-defined]
        if provider is None:
            payload = self.state.status_snapshot()
        else:
            payload = provider()
        payload = dict(payload)
        payload["credential_recovery"] = (
            self.state.credential_recovery_snapshot()
        )
        self._json(200, payload)

    def _handle_poll(self, qs: dict[str, list[str]]) -> None:
        peer = qs.get("peer", ["server"])[0] or "server"
        version = qs["ver"][0] if "ver" in qs else None
        inst_raw = qs.get("inst", [""])[0]
        instance = inst_raw if inst_raw else None
        source_pid = self.state.resolve_poll_pid(
            instance, getattr(self, "connection", None)
        )
        status, payload = self.state.record_poll(
            peer,
            version,
            instance=instance,
            source_pid=source_pid,
        )
        if status != 200:
            self._json(status, payload)
            return

        delay_ms = int(payload.get("delay_ms", 0))
        commands = payload["commands"]
        bind = payload.get("bind", "-")
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

        inst8 = instance_prefix(instance) or "-"
        self._log(
            f"HIT GET /poll peer={peer} inst8={inst8} bind={bind} "
            f"commands={len(commands)} delay_ms={delay_ms}"
        )
        self._json(200, {"commands": commands})

    def _handle_result(self, qs: dict[str, list[str]] | None = None) -> None:
        body = self._read_json()
        if body is None:
            return

        inst_raw = ""
        if qs is not None:
            inst_raw = qs.get("inst", [""])[0]
        instance = inst_raw if inst_raw else None
        status, payload = self.state.store_result(body, instance=instance)
        if status != 200:
            self._json(status, payload)
            return

        self._log(f"RESULT id={payload['id']} ok={payload.get('ok_value')}")
        response: dict[str, object] = {"ok": True}
        if payload.get("discarded"):
            response["discarded"] = True
        self._json(200, response)

    def _handle_set_poll_delay(self) -> None:
        body = self._read_json()
        if body is None:
            return

        try:
            delay_ms = int(body.get("ms"))
        except (TypeError, ValueError):
            self._json(400, {"error": "bad_ms"})
            return

        status, payload = self.state.set_poll_delay(delay_ms)
        if status != 200:
            self._json(status, payload)
            return

        self._log(f"SET_POLL_DELAY ms={delay_ms}")
        self._json(200, {"ok": True})


def _repair_lifecycle_recovery_fault(
    state: ServerState, fault_id: str, expected_head_sha256: str
) -> tuple[int, dict[str, object]]:
    coordinator = state.coordination
    store = state.lifecycle_recovery_fault_store
    lifecycle = state.lifecycle
    if coordinator is None or store is None or lifecycle is None:
        return 404, {"error": "not_found"}
    try:
        active = store.load_active()
    except (OSError, RuntimeError, ValueError):
        return 409, {"error": "lifecycle_recovery_unreadable"}
    if not isinstance(active, dict):
        return 409, {"error": "lifecycle_recovery_fault_mismatch"}
    fault = active.get("fault")
    event = active.get("event")
    pointer = active.get("pointer")
    if (
        not isinstance(fault, dict)
        or not isinstance(event, dict)
        or not isinstance(pointer, dict)
        or fault.get("fault_id") != fault_id
        or pointer.get("head_event_sha256") != expected_head_sha256
    ):
        return 409, {"error": "lifecycle_recovery_cas_conflict"}
    if event.get("state") == "repaired":
        coordinator.clear_lifecycle_recovery_fault(fault_id)
        return 200, {"repaired": True, "fault_id": fault_id}
    manifest_sha = fault.get("manifest_sha256")
    event_state = event.get("state")
    if event_state == "armed":
        try:
            repairing_head = store.transition(
                fault_id,
                expected_head_sha256,
                state="repairing",
                expected_manifest_sha256=manifest_sha,
            )
        except (OSError, RuntimeError, ValueError):
            return 409, {"error": "lifecycle_recovery_cas_conflict"}
    elif event_state == "repairing":
        # Resume from an in-progress repair. Same shape as
        # _repair_coordination_audit_fault, which continues from repairing
        # instead of demanding a fresh start. A failed re-arm leaves the
        # pointer here; refusing it made retry unrecoverable (409 on the
        # repairing head and 409 on the armed head).
        repairing_head = expected_head_sha256
    else:
        return 409, {"error": "lifecycle_recovery_cas_conflict"}
    try:
        if fault.get("scope") == "manifest":
            try:
                backup = store.read_manifest_backup(
                    fault.get("backup_receipt_sha256")
                )
            except (OSError, RuntimeError, ValueError):
                outcome = {
                    "terminal_safe": False,
                    "error": "receipt_missing",
                    "manifest_sha256": manifest_sha,
                }
            else:
                outcome = lifecycle.repair_manifest_recovery(backup)
        else:
            outcome = lifecycle.repair_recovery_fault(fault)
    except Exception:
        outcome = {"terminal_safe": False, "error": "cleanup_failed"}
    if not isinstance(outcome, dict) or outcome.get("terminal_safe") is not True:
        error = (
            str(outcome.get("error", "cleanup_failed"))
            if isinstance(outcome, dict)
            else "cleanup_failed"
        )
        error_code = (
            error
            if error
            in {
                "manifest_drift",
                "identity_ambiguous",
                "cleanup_failed",
                "receipt_missing",
            }
            else "cleanup_failed"
        )
        failure_manifest_sha = (
            outcome.get("manifest_sha256", manifest_sha)
            if isinstance(outcome, dict)
            else manifest_sha
        )
        try:
            store.transition(
                fault_id,
                repairing_head,
                state="armed",
                expected_manifest_sha256=failure_manifest_sha,
                error_code=error_code,
            )
        except Exception:
            pass
        return 409, {"error": error_code}
    current_manifest_sha = outcome.get("manifest_sha256", manifest_sha)
    try:
        receipt_sha = store.create_receipt(
            fault_id,
            {
                "manifest_sha256": current_manifest_sha,
                "all_relevant_runs_terminal": True,
            },
        )
        store.transition(
            fault_id,
            repairing_head,
            state="repaired",
            expected_manifest_sha256=current_manifest_sha,
            evidence_sha256=receipt_sha,
        )
    except (OSError, RuntimeError, ValueError):
        return 409, {"error": "lifecycle_recovery_cas_conflict"}
    if not coordinator.complete_lifecycle_recovery_fault(fault_id):
        return 409, {"error": "lifecycle_recovery_cas_conflict"}
    return 200, {"repaired": True, "fault_id": fault_id}


def _repair_coordination_audit_fault(
    state: ServerState, fault_id: str
) -> tuple[int, dict[str, object]]:
    coordinator = state.coordination
    store = state.coordination_fault_store
    writer = state.audit_writer
    if coordinator is None or store is None or writer is None:
        return 404, {"error": "not_found"}
    public_fault = coordinator.snapshot_payload().get("audit_fault")
    if not isinstance(public_fault, dict) or public_fault.get("fault_id") != fault_id:
        return 409, {"error": "audit_fault_mismatch"}
    expected_revision = coordinator.expected_repair_snapshot_revision(fault_id)
    if expected_revision is None:
        return 409, {"error": "audit_fault_mismatch"}
    try:
        marker, marker_sha = store.load_with_sha()
        if (
            not isinstance(marker, dict)
            or not isinstance(marker_sha, str)
            or marker.get("fault_id") != fault_id
        ):
            return 409, {"error": "audit_fault_unrepairable"}
        state_name = marker.get("state")
        if state_name == "fault":
            marker_sha = store.transition(
                fault_id,
                marker_sha,
                state="repairing",
                phase="repairing",
                repair_phase="compensation",
                expected_snapshot_revision=expected_revision,
            )
            marker, marker_sha = store.load_with_sha()
            state_name = marker.get("state")
        if state_name == "repairing":
            if marker.get("repair_phase") in {"none", "compensation"}:
                writer.write_once(
                    f"{fault_id}:compensation",
                    {
                        "event": (
                            "session_grant_revoked"
                            if marker.get("operation") == "grant"
                            else "session_release_reconciled"
                        ),
                        "reason": "admin_audit_repair",
                        "duration_s": 0.0,
                        "decision": "reconciled",
                        "fault_id": fault_id,
                        "lease_id": marker.get("lease_id"),
                        "ticket": marker.get("ticket_id"),
                        "operation": marker.get("operation"),
                        "client": marker.get("client"),
                    },
                )
                marker_sha = store.transition(
                    fault_id,
                    marker_sha,
                    state="repairing",
                    phase="repairing",
                    repair_phase="repair_event",
                    expected_snapshot_revision=expected_revision,
                )
                marker, marker_sha = store.load_with_sha()
            writer.write_once(
                f"{fault_id}:repaired",
                {
                    "event": "coordination_audit_repaired",
                    "reason": "admin_audit_repair",
                    "duration_s": 0.0,
                    "decision": "repaired",
                    "fault_id": fault_id,
                    "lease_id": marker.get("lease_id"),
                    "operation": marker.get("operation"),
                    "client": marker.get("client"),
                },
            )
            marker_sha = store.transition(
                fault_id,
                marker_sha,
                state="repaired",
                phase="repaired",
                repair_phase="repair_event",
                expected_snapshot_revision=expected_revision,
            )
            marker, marker_sha = store.load_with_sha()
            state_name = marker.get("state")
        if state_name == "repaired":
            current_expected = coordinator.expected_repair_snapshot_revision(fault_id)
            if current_expected is None:
                return 409, {"error": "audit_fault_mismatch"}
            if marker.get("expected_snapshot_revision") != current_expected:
                marker_sha = store.transition(
                    fault_id,
                    marker_sha,
                    state="repaired",
                    phase="repaired",
                    repair_phase="repair_event",
                    expected_snapshot_revision=current_expected,
                )
                marker, marker_sha = store.load_with_sha()
            if coordinator.finalize_audit_repair(marker, marker_sha):
                return 200, {"repaired": True, "fault_id": fault_id}
            return 503, {
                "error": "audit_repair_cleanup_pending",
                "fault_id": fault_id,
            }
        return 409, {"error": "audit_fault_unrepairable"}
    except Exception:
        return 503, {"error": "audit_repair_failed", "fault_id": fault_id}


def read_key(path: str) -> str:
    """Read the daemon auth key through the pinned local-disk contract.

    Standalone daemon and embedded loopback both authenticate the
    loopback with this secret. The previous implementation was a raw
    unbounded text read of the configured path: no size cap, no disk-type
    check, and it followed reparse points. That is hardening of a
    privileged secret load, not a demonstrated exploit -- exploitability
    depends on who can replace the configured path. Reparse substitution
    was not reproduced here (creating a symlink needs a privilege this
    process did not have). Client credentials, doctor, and the
    admin/lifecycle CLIs already use the pinned reader.

    The signed reader collapses every failure to invalid_daemon_keyfile
    and is not edited here, so this wrapper cannot name the check that
    failed. It keeps that token so existing matchers still fire, and
    lists the contract a user can inspect (local regular file, size,
    encoding) without echoing key bytes. Odd files that used to pass
    (oversize, BOM, relative path, extra hard links) are rejected; they
    already fail every other reader in this tree.
    """
    try:
        return pinned_keyfile.read_pinned_keyfile(path)
    except ValueError as error:
        if str(error) != "invalid_daemon_keyfile":
            raise
        raise ValueError(
            "invalid_daemon_keyfile: must be a local regular disk file "
            "with one hard link and no reparse points, at most 4096 bytes, "
            "UTF-8 without BOM, and a single-line key of at most 1024 "
            "characters"
        ) from None


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    # HTTPServer defaults allow_reuse_address to 1. On Windows, SO_REUSEADDR lets a
    # second process LISTEN on an already-bound port, which defeats the exclusive
    # instance lock (two loopbacks would silently split bridge polls). Exclusive
    # bind makes the second bind fail with EADDRINUSE.
    allow_reuse_address = False

    def __init__(self, *args, **kwargs):
        self._http_workers = threading.BoundedSemaphore(MAX_HTTP_WORKERS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        # Auth runs in the handler, after a worker exists. An idle TCP has
        # already cost a thread, so the ceiling is on spawn, not on /status.
        if not self._http_workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._http_workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._http_workers.release()


def _is_address_in_use(exc: OSError) -> bool:
    # Windows surfaces EADDRINUSE as errno/winerror 10048 (WSAEADDRINUSE).
    return exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048


def _bind_exclusive(port: int, log_sink: LogSink, reclaim_orphans: bool) -> "ExclusiveThreadingHTTPServer":
    try:
        return ExclusiveThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        # Only EADDRINUSE is a reclaim candidate; any other bind error propagates.
        if not reclaim_orphans or not _is_address_in_use(exc):
            raise
        original_argv = getattr(sys, "orig_argv", None)
        expected_argv = list(original_argv) if isinstance(original_argv, list) else None
        if not orphan_guard.try_reclaim_port(
            port,
            log=log_sink,
            expected_executable=sys.executable,
            expected_argv=expected_argv,
        ):
            raise
        # A confirmed-dead-parent dayz_mcp orphan was terminated and the port came
        # free; retry the exclusive bind exactly once. The exclusive lock stays intact — the bind is
        # still ExclusiveThreadingHTTPServer with allow_reuse_address False.
        return ExclusiveThreadingHTTPServer(("127.0.0.1", port), Handler)


def create_http_server(
    port: int,
    state: ServerState,
    log_sink: LogSink | None = None,
    reclaim_orphans: bool = True,
    status_provider: Callable[[], dict] | None = None,
) -> ThreadingHTTPServer:
    sink = log_sink or _default_log_sink
    httpd = _bind_exclusive(port, sink, reclaim_orphans)
    if httpd.server_address[0] != "127.0.0.1":
        httpd.server_close()
        raise RuntimeError("refusing non-loopback bind")
    httpd.state = state  # type: ignore[attr-defined]
    httpd.log_sink = sink  # type: ignore[attr-defined]
    httpd.status_provider = status_provider  # type: ignore[attr-defined]
    return httpd


class LoopbackServer:
    def __init__(
        self,
        port: int,
        key: str,
        log_sink: LogSink | None = None,
        poll_interval: float = 0.1,
        enable_exec_enforce: bool = False,
        version_validator: VersionValidator | None = None,
        exec_allowlist: set[str] | None = None,
        exec_audit: ExecAudit | None = None,
        status_provider: Callable[[], dict] | None = None,
        reclaim_orphans: bool = True,
    ) -> None:
        self.port = port
        self.key = key
        self.log_sink = log_sink or _default_log_sink
        self.poll_interval = poll_interval
        self.status_provider = status_provider
        self.reclaim_orphans = reclaim_orphans
        self.state = ServerState(
            key,
            enable_exec_enforce=enable_exec_enforce,
            version_validator=version_validator,
            exec_allowlist=exec_allowlist,
            exec_audit=exec_audit,
            config_port=port,
        )
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.httpd is not None:
            return
        self.state.resume_accepting()
        self.httpd = create_http_server(
            self.port,
            self.state,
            self.log_sink,
            reclaim_orphans=self.reclaim_orphans,
            status_provider=self.status_provider,
        )
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": self.poll_interval},
            daemon=True,
        )
        self.thread.start()
        host, port = self.httpd.server_address
        self.log_sink(f"LISTEN host={host} port={port}")

    def stop(self, timeout: float = 2.0) -> None:
        if self.httpd is None:
            return
        self.state.cancel_pending()
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=timeout)
        self.httpd = None
        self.thread = None
