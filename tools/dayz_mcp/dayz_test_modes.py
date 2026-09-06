"""Single, dependency-free authority for DayZ test mode policy.

Consumers deliberately read :data:`MODE_RECORDS` through the accessors in this
module for every call.  The binding is replaceable in tests, while the records
and their steps are immutable values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class ModeAuthorityError(ValueError):
    """Raised when a mode authority view is unknown or internally invalid."""


@dataclass(frozen=True, slots=True)
class ModeStep:
    """One ordered operation in a mode's normal execution sequence."""

    kind: str
    role: str | None = None
    root: str | None = None
    run_id_source: str | None = None
    required: bool | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"adopt_supplied", "start", "readiness"}:
            raise ModeAuthorityError("unknown mode step")
        if self.kind == "adopt_supplied":
            if self.role is not None or self.root is not None or self.run_id_source is not None:
                raise ModeAuthorityError("adopt_supplied has unexpected fields")
            if type(self.required) is not bool:
                raise ModeAuthorityError("adopt_supplied requires a boolean")
        elif self.kind == "start":
            if self.role is None or self.root is None or self.run_id_source is None:
                raise ModeAuthorityError("start requires role, root and run_id_source")
            if self.required is not None:
                raise ModeAuthorityError("start has unexpected required field")
        else:
            if self.role is not None or self.root is not None:
                raise ModeAuthorityError("readiness has unexpected fields")
            if self.run_id_source != "current" or self.required is not None:
                raise ModeAuthorityError("readiness must use current run")


@dataclass(frozen=True, slots=True)
class ModeRecord:
    """Immutable policy record consumed by request, tool and worker layers."""

    name: str
    public: bool
    request_visible: bool
    steps: tuple[ModeStep, ...]
    artifact_roots: tuple[str, ...]
    starts_client: bool
    default_when_omitted: bool

    @property
    def mode(self) -> str:
        return self.name

    @property
    def request(self) -> bool:
        return self.request_visible

    @property
    def publicly_visible(self) -> bool:
        return self.public

    @property
    def arranca_cliente(self) -> bool:
        return self.starts_client

    @property
    def roots(self) -> tuple[str, ...]:
        return self.artifact_roots

    @property
    def sequence(self) -> tuple[ModeStep, ...]:
        return self.steps


def adopt_supplied(*, required: bool) -> ModeStep:
    return ModeStep(kind="adopt_supplied", required=required)


def start(role: str, root: str, run_id_source: str) -> ModeStep:
    return ModeStep(
        kind="start",
        role=role,
        root=root,
        run_id_source=run_id_source,
    )


def readiness(run_id_source: str = "current") -> ModeStep:
    return ModeStep(kind="readiness", run_id_source=run_id_source)


MODE_RECORDS: tuple[ModeRecord, ...] = (
    ModeRecord(
        name="all",
        public=True,
        request_visible=True,
        steps=(
            start("server", "_server", "new"),
            readiness("current"),
            start("client", "_client", "current"),
        ),
        artifact_roots=("_server", "_client"),
        starts_client=True,
        default_when_omitted=True,
    ),
    ModeRecord(
        name="server",
        public=True,
        request_visible=True,
        steps=(start("server", "_server", "new"),),
        artifact_roots=("_server",),
        starts_client=False,
        default_when_omitted=False,
    ),
    ModeRecord(
        name="client",
        public=True,
        request_visible=True,
        steps=(
            adopt_supplied(required=True),
            start("client", "_client", "supplied"),
        ),
        artifact_roots=("_client",),
        starts_client=True,
        default_when_omitted=False,
    ),
    ModeRecord(
        name="offline",
        public=False,
        request_visible=True,
        steps=(
            adopt_supplied(required=False),
            start("offline", "_client", "supplied_or_new"),
        ),
        artifact_roots=("_client",),
        starts_client=True,
        default_when_omitted=False,
    ),
)


def _validated_view(records: Iterable[ModeRecord]) -> tuple[ModeRecord, ...]:
    view = tuple(records)
    if not view:
        raise ModeAuthorityError("mode authority view is empty")
    names: set[str] = set()
    for record in view:
        if not isinstance(record, ModeRecord):
            raise ModeAuthorityError("mode authority contains an invalid record")
        if not record.name or record.name in names:
            raise ModeAuthorityError("mode authority contains duplicate or empty mode")
        names.add(record.name)
        if type(record.public) is not bool or type(record.request_visible) is not bool:
            raise ModeAuthorityError("mode visibility is invalid")
        if type(record.starts_client) is not bool or type(record.default_when_omitted) is not bool:
            raise ModeAuthorityError("mode policy flags are invalid")
        if not record.steps or not isinstance(record.steps, tuple):
            raise ModeAuthorityError("mode sequence is invalid")
        if not isinstance(record.artifact_roots, tuple) or not record.artifact_roots:
            raise ModeAuthorityError("mode artifact roots are invalid")
        if any(not isinstance(root, str) or not root for root in record.artifact_roots):
            raise ModeAuthorityError("mode artifact roots are invalid")
    return view


def mode_records() -> tuple[ModeRecord, ...]:
    """Return the current immutable record view."""

    return _validated_view(MODE_RECORDS)


def get_mode_records() -> tuple[ModeRecord, ...]:
    return mode_records()


def resolve_mode(name: str, records: Iterable[ModeRecord] | None = None) -> ModeRecord:
    """Resolve an exact mode name from the supplied/current authority view."""

    view = _validated_view(MODE_RECORDS if records is None else records)
    matches = tuple(record for record in view if record.name == name)
    if len(matches) != 1:
        raise ModeAuthorityError("unknown mode")
    return matches[0]


def lookup_mode(name: str, records: Iterable[ModeRecord] | None = None) -> ModeRecord:
    return resolve_mode(name, records)


def resolve_default_mode(records: Iterable[ModeRecord] | None = None) -> ModeRecord:
    """Resolve the sole request-visible default, failing closed otherwise."""

    view = _validated_view(MODE_RECORDS if records is None else records)
    defaults = tuple(
        record
        for record in view
        if record.default_when_omitted and record.request_visible
    )
    marked = tuple(record for record in view if record.default_when_omitted)
    if len(defaults) != 1 or len(marked) != 1:
        raise ModeAuthorityError("mode authority requires exactly one request-visible default")
    return defaults[0]


def public_mode_names(records: Iterable[ModeRecord] | None = None) -> tuple[str, ...]:
    view = _validated_view(MODE_RECORDS if records is None else records)
    return tuple(record.name for record in view if record.public)


def request_mode_names(records: Iterable[ModeRecord] | None = None) -> tuple[str, ...]:
    view = _validated_view(MODE_RECORDS if records is None else records)
    return tuple(record.name for record in view if record.request_visible)


__all__ = (
    "MODE_RECORDS",
    "ModeAuthorityError",
    "ModeRecord",
    "ModeStep",
    "adopt_supplied",
    "get_mode_records",
    "lookup_mode",
    "mode_records",
    "public_mode_names",
    "readiness",
    "request_mode_names",
    "resolve_default_mode",
    "resolve_mode",
    "start",
)
