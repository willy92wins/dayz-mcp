"""Carry one session's identity and lease across a worker recycle.

A recycled worker is a NEW process, so it mints a new ClientIdentity (server.py:1210:
getpid, getppid, now, uuid4). The coordinator compares the whole frozen dataclass by
value (session_coordination.py:3433), so a fresh identity cannot use the live lease. The
rehearsal of 2026-09-10 (reviews/council-mcpreload-2026-09-09/REHEARSAL-LEASE.md, 34/34)
settled what the replacement needs and what it must not do:

  - The whole identity crosses, or nothing does. Carrying only the session_id is worse
    than carrying nothing: the worker can then neither use the lease, nor acquire a new
    one (403 identity_mismatch, the collision guard at session_coordination.py:2766),
    nor release the one left hanging.
  - Identity and lease_token are two DISTINCT secrets. The token is
    secrets.token_urlsafe(32) (session_coordination.py:206) and never travels inside the
    identity, so it must not reach argv either -- this box's process list is visible to
    the other sessions that share it. Only the PATH of the carrier travels by env var.
  - The replacement re-types on arrival. from_payload rejects a pid that arrived as text
    (session_coordination.py:79), which is what an env var would hand over.

Everything here fails closed: any doubt about the carrier and the caller gets None,
which means "start without a lease" -- today's behaviour, and the safe one. Nothing here
grants anything. The authority on whether the carried token still owns the box stays the
daemon's coordinator, which re-validates on every authorize.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dayz_mcp.session_coordination import SESSION_TTL_S, ClientIdentity


# Names the file, never the secret. The token stays out of argv and out of the
# environment; only this path travels.
HANDOFF_ENV = "DAYZ_MCP_SESSION_HANDOFF"

HANDOFF_VERSION = 1

# A carrier older than the lease it names cannot be describing a live lease, so refuse
# it rather than hand a stale token to a fresh worker. Defence in depth only: the
# coordinator is what actually decides, and it does not consult this.
MAX_HANDOFF_AGE_S = SESSION_TTL_S

# Wall clock, not monotonic: writer and reader are different processes. A carrier
# stamped in the future is a clock that moved, so allow a little and refuse the rest.
MAX_CLOCK_SKEW_S = 5.0

MAX_SECRET_LEN = 512

_LOG = logging.getLogger(__name__)

# Tombstone schema. ``token_sha256`` is the digest of the revoked lease token.
# The token itself never goes in the side file.
_TOMBSTONE_VERSION = 1


class TombstoneStillRejects(OSError):
    """The replacement is on disk, but a tombstone still rejects that token.

    ``consume_handoff`` would return None, so the rewrite is not a persisted lease.
    """


@dataclass(frozen=True)
class SessionHandoff:
    """What one worker generation hands to the next. Both secrets, never split."""

    identity: ClientIdentity
    lease_token: str
    lease_id: str
    generation: int
    written_at: float


def carrier_path(directory: str | os.PathLike[str] | None = None) -> Path:
    """Pick a private file for the carrier.

    The token lives here at rest for as long as the lease does, so the directory has to
    be one only this user can read. tempfile.mkdtemp is that on both platforms: 0o700 on
    POSIX, and under the per-user profile on Windows, where the mode argument is ignored.
    """
    if directory is None:
        directory = tempfile.mkdtemp(prefix="dayz-mcp-handoff-")
    return Path(directory) / "session-handoff.json"


def write_handoff(
    path: str | os.PathLike[str],
    *,
    identity: ClientIdentity,
    lease_token: str,
    lease_id: str,
    generation: int,
    now: Callable[[], float] = time.time,
) -> None:
    """Persist the carrier, replacing any earlier one atomically.

    Raises on a caller mistake -- a malformed identity or an empty secret is a bug here,
    not a degraded input. Reading is the direction that forgives.
    """
    if not isinstance(identity, ClientIdentity):
        raise TypeError("handoff_identity_required")
    for name, secret in (("lease_token", lease_token), ("lease_id", lease_id)):
        if not isinstance(secret, str) or not secret or len(secret) > MAX_SECRET_LEN:
            raise ValueError(f"invalid_handoff_{name}")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ValueError("invalid_handoff_generation")

    document = {
        "version": HANDOFF_VERSION,
        "identity": identity.to_payload(),
        "lease_token": lease_token,
        "lease_id": lease_id,
        "generation": generation,
        "written_at": float(now()),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write the secret into a file only this user can open, then replace. os.replace is
    # atomic on both platforms, so a reader sees the old carrier or the new one.
    handle = os.open(
        f"{target}.tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        _unlink_quietly(f"{target}.tmp")
        raise
    os.replace(f"{target}.tmp", target)
    # A clear that could not replace this path may have left a tombstone.
    # One that names a different token can stay: the reader compares digests.
    # One that still names this token, or that cannot be classified, means
    # this rewrite is not actually inheritable.
    _retire_tombstone(target)


def _tombstone_path(path: Path) -> Path:
    """Side file that invalidates ``path`` when the carrier itself cannot be replaced."""
    return Path(f"{path}.invalid")


def consume_handoff(
    path: str | os.PathLike[str],
    *,
    now: Callable[[], float] = time.time,
    max_age_s: float = MAX_HANDOFF_AGE_S,
) -> SessionHandoff | None:
    """Read the carrier once and remove it, whatever it turned out to contain.

    A tombstone names the token a clear could not delete, by digest. The carrier
    is refused only when its token matches, or when the marker cannot be told
    apart from this file (unreadable, malformed, or with no digest). A later
    token is read normally. A matching marker stays while the carrier file does.

    Consume-once: a carrier that survived its read could be replayed by a later worker
    against a lease that has since changed hands. Removing it before returning also
    means a malformed one cannot be retried into the same failure on every restart.

    Returns None for anything at all doubtful. None is not an error: it means this
    worker starts without a lease, which is what a worker did before any of this.
    """
    target = Path(path)
    marker = _tombstone_path(target)
    try:
        raw: bytes | None = target.read_bytes()
    except OSError:
        raw = None
    if _tombstone_blocks(marker, raw):
        _unlink_quietly(target)
        if not target.exists():
            _unlink_quietly(marker)
        return None
    if raw is None:
        return None
    _unlink_quietly(target)
    # This token is not the one the marker names. Dropping the marker is
    # best-effort: a failure here must not hide the lease we just accepted.
    _unlink_quietly(marker)

    try:
        document = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    if document.get("version") != HANDOFF_VERSION:
        return None

    try:
        identity = ClientIdentity.from_payload(document.get("identity"))
    except ValueError:
        return None

    lease_token = document.get("lease_token")
    lease_id = document.get("lease_id")
    for secret in (lease_token, lease_id):
        if not isinstance(secret, str) or not secret or len(secret) > MAX_SECRET_LEN:
            return None

    generation = document.get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        return None

    written_at = document.get("written_at")
    if (
        isinstance(written_at, bool)
        or not isinstance(written_at, (int, float))
        or not math.isfinite(float(written_at))
    ):
        return None
    age = float(now()) - float(written_at)
    if age > float(max_age_s) or age < -MAX_CLOCK_SKEW_S:
        return None

    return SessionHandoff(
        identity=identity,
        lease_token=lease_token,
        lease_id=lease_id,
        generation=generation,
        written_at=float(written_at),
    )


def clear_handoff(path: str | os.PathLike[str]) -> None:
    """Drop the carrier because the lease it names is gone (released, or never held).

    A delete that fails is logged and retried once. If it still fails, the file
    is replaced with ``{}``, which consume_handoff rejects, so the released
    token cannot be inherited (fb-20260928-021501-83be). If that replace also
    fails, a side tombstone is sealed with the digest of the token still in
    the file, and consume_handoff refuses that token. A later different token
    is not the one named. The tombstone's own failure propagates.
    """
    target = Path(path)
    ok, error = _unlink_once(target)
    if ok:
        _unlink_quietly(_tombstone_path(target))
        return
    _LOG.warning("carrier unlink failed (%s); retrying delete: %s", error, target)
    ok, error = _unlink_once(target)
    if ok:
        _unlink_quietly(_tombstone_path(target))
        return
    _LOG.warning(
        "carrier unlink failed again (%s); replacing with a rejected carrier: %s",
        error,
        target,
    )
    try:
        _overwrite_rejected(target)
    except OSError:
        _seal_tombstone(target)
        return
    _unlink_quietly(_tombstone_path(target))


def _unlink_once(path: Path) -> tuple[bool, OSError | None]:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return True, None
    except OSError as exc:
        return False, exc
    return True, None


def _overwrite_rejected(path: Path) -> None:
    """Replace path with ``{}``. It has no version, so consume_handoff returns None."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = f"{path}.tmp"
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(handle, b"{}")
        os.fsync(handle)
    except BaseException:
        os.close(handle)
        _unlink_quietly(temporary)
        raise
    os.close(handle)
    os.replace(temporary, path)


def _token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _carrier_token_sha256(raw: bytes) -> str | None:
    """Digest of the lease token in ``raw``, or None when the file has none."""
    try:
        document = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    token = document.get("lease_token")
    if not isinstance(token, str) or not token or len(token) > MAX_SECRET_LEN:
        return None
    return _token_sha256(token)


def _tombstone_blocks(marker: Path, raw: bytes | None) -> bool:
    """Whether ``marker`` forbids inheriting the carrier bytes ``raw``.

    A classified digest blocks only that token. No marker blocks nothing.
    An unreadable marker, a malformed one, or one with no digest blocks the
    file beside it: those do not say which carrier was revoked, so they cannot
    be told apart from a later legitimate one.
    """
    try:
        text = marker.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError):
        return True
    try:
        payload = json.loads(text)
    except ValueError:
        return True
    if not isinstance(payload, dict):
        return True
    version = payload.get("v")
    digest = payload.get("token_sha256")
    classified = (
        not isinstance(version, bool)
        and version == _TOMBSTONE_VERSION
        and isinstance(digest, str)
        and len(digest) == 64
        and all(char in "0123456789abcdef" for char in digest)
    )
    if not classified or raw is None:
        return True
    current = _carrier_token_sha256(raw)
    if current is None:
        return True
    return current == digest


def _retire_tombstone(target: Path) -> None:
    """Remove the side marker after a carrier replace.

    A marker that still rejects ``target`` (it names this token, or it cannot
    be classified) is not success: the reader would discard the rewrite.
    A marker for some other token may stay. The reader will not apply it here.
    """
    marker = _tombstone_path(target)
    try:
        os.unlink(marker)
    except FileNotFoundError:
        return
    except OSError as exc:
        try:
            raw: bytes | None = target.read_bytes()
        except OSError:
            raw = None
        if not _tombstone_blocks(marker, raw):
            _LOG.warning(
                "stale carrier tombstone could not be removed (%s): %s",
                exc,
                marker,
            )
            return
        _LOG.warning(
            "carrier rewrite left a tombstone that still rejects %s (%s)",
            target,
            exc,
        )
        raise TombstoneStillRejects(
            f"tombstone still rejects the carrier: {target}"
        ) from exc


def _seal_tombstone(path: Path) -> None:
    """Mark the token in ``path`` rejected, without replacing the file.

    The marker stores the SHA-256 of that token, never the token. When the
    file has no readable token, the marker carries no digest and the reader
    rejects whatever still sits beside it. Raises if the mark cannot be sealed.
    """
    try:
        digest = _carrier_token_sha256(path.read_bytes())
    except OSError:
        digest = None
    body: dict[str, object] = {"v": _TOMBSTONE_VERSION}
    if digest is not None:
        body["token_sha256"] = digest
    payload = json.dumps(body, separators=(",", ":")).encode("ascii")
    marker = _tombstone_path(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = f"{marker}.tmp"
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(handle, payload)
        os.fsync(handle)
    except BaseException:
        os.close(handle)
        _unlink_quietly(temporary)
        raise
    os.close(handle)
    try:
        os.replace(temporary, marker)
    except OSError:
        _unlink_quietly(temporary)
        raise


def _unlink_quietly(path: str | os.PathLike[str]) -> None:
    try:
        os.unlink(path)
    except OSError:
        return
