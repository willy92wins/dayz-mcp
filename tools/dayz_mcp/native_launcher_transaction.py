"""Request-bound native launcher transaction without any process creation surface."""

from __future__ import annotations

import asyncio
import ntpath
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from dayz_mcp import (
    dayz_test_modes,
    dayz_test_request,
    lease_supervisor,
    request_path_authority,
)


class NativeLauncherTransactionError(RuntimeError):
    pass


class _ControlClient(Protocol):
    async def session_acquire_wait(
        self,
        purpose: str,
        max_wait_s: float | None = None,
        progress_cb: Callable[[float, float | None, str | None], Awaitable[None]]
        | None = None,
    ) -> dict[str, object]: ...

    async def session_heartbeat(self, lease_token: str) -> dict[str, object]: ...

    async def session_release(self, lease_token: str) -> dict[str, object]: ...

    async def session_status(self) -> dict[str, object]: ...


def _validate_grant(response: object) -> tuple[str, str, str]:
    if not isinstance(response, dict):
        raise NativeLauncherTransactionError("invalid_session_grant")
    lease_token = response.get("lease_token")
    lease_id = response.get("lease_id")
    client_identity_json = response.get("client_identity_json")
    if (
        response.get("status") != "active"
        or not isinstance(lease_token, str)
        or not lease_token
        or not isinstance(lease_id, str)
        or not lease_id
        or not isinstance(client_identity_json, str)
        or not client_identity_json
    ):
        raise NativeLauncherTransactionError("invalid_session_grant")
    return lease_token, lease_id, client_identity_json


async def _finish_task(task: asyncio.Task[object] | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception:
        return


async def _cleanup_transaction(
    *,
    consumer_task: asyncio.Task[int] | None,
    failure_task: asyncio.Task[lease_supervisor.LeaseHeartbeatError] | None,
    cancel_event: asyncio.Event,
    supervisor: lease_supervisor.LeaseHeartbeatSupervisor,
    control_client: _ControlClient,
    lease_token: str,
) -> BaseException | None:
    cancel_event.set()
    await _finish_task(failure_task)
    await _finish_task(consumer_task)
    cleanup_error: BaseException | None = None
    try:
        await supervisor.stop()
    except BaseException as error:
        cleanup_error = error
    try:
        await lease_supervisor.protected_release_and_verify(
            control_client, lease_token
        )
    except BaseException as error:
        if cleanup_error is None:
            cleanup_error = error
    return cleanup_error


# --- VPP admin-tools preflight (ficha df93) ---------------------------------
#
# dayz-test.ps1 carried @VPPAdminTools in its default mod list
# (skills/dayz-test-ingame/templates/dayz-test.ps1:42) and repaired the server
# workspace before every server launch (:454-474: serverDZ.cfg generated or
# self-healed with vppDisablePassword at :466-472, SuperAdmins.txt seeded at
# :386-401 with the ^\d{17}$ token of :398, credentials.txt at :403-421).
# This route replaced that script and inherited none of it, and DayZ_MCP is the
# only project in request-policy.json with default_base_mods: [], so a server
# could start with no admin tools at all and the symptom only showed up inside
# the game.
#
# The replacement refuses instead of repairing: it reads, it never writes. It
# lives HERE, and not in secure_launcher.py, because this is the single function
# both launch routes traverse -- and because secure_launcher.py's bytes are
# pinned by dependency-lock.json, which build-contract.json seals in turn
# (build_native_launcher.py:722, verify_bundle "build_contract_drift"), so
# editing it costs a rebuild of the sealed bundle.
VPP_MOD_FOLDER = "@VPPAdminTools"
# Six of the ten sealed policies do not name the folder at all: they carry the
# Workshop install by its absolute path, whose basename is the published id.
# Both forms only SELECT a candidate here. A basename is a name, not an
# identity: a directory an unrelated mod occupies would pass a name check. The
# identity is the published id inside the candidate's own meta.cpp, which every
# live form carries -- measured on this host for P:\Mods\@VPPAdminTools,
# <DayZ>\!Workshop\@VPPAdminTools and <workshop>\content\221100\1828439124:
# all three say publishedid = 1828439124.
VPP_WORKSHOP_ID = "1828439124"
VPP_MOD_IDENTITIES = frozenset(
    {VPP_MOD_FOLDER.casefold(), VPP_WORKSHOP_ID.casefold()}
)
_MOD_META_NAME = "meta.cpp"
# Read with the same scanner as serverDZ.cfg (ronda 4, R3-F-01): a raw regex
# took the first "publishedid = <digits>" it saw, in a comment, inside another
# key or as the prefix of 1828439124evil, and accredited a foreign directory.
_MOD_PUBLISHED_ID_KEY = "publishedid"
VPP_PREFLIGHT_FAILED = "vpp_preflight_failed"
VPP_PREFLIGHT_HINT = (
    "this mode starts a server and the admin tools are not usable: put the "
    "installed mod in extra_mods as @VPPAdminTools, or as its absolute "
    "Workshop path (the form this gate can always verify), and make sure the "
    "server serverDZ.cfg carries a live vppDisablePassword = 1"
)
SERVER_CONFIG_NAME = "serverDZ.cfg"
_PROFILES_DIR = "profiles"
_VPP_PROFILE_DIR = "VPPAdminTools"
_VPP_PERMISSIONS_DIR = "Permissions"
_SUPERADMINS_DIR = "SuperAdmins"
_SUPERADMINS_NAME = "SuperAdmins.txt"
_CREDENTIALS_NAME = "credentials.txt"
_MAX_PREFLIGHT_READ_CHARS = 262_144
# The generated cfg writes "vppDisablePassword = 1;" (dayz-test.ps1:343). The
# ps1's self-heal only checks the key is PRESENT (:468); a key set to 0 leaves
# the superadmin at the password prompt, which is the reported symptom, so the
# VALUE is what is checked -- and only where the engine would read it. A regex
# over the raw text accepted the key inside a string, inside an unterminated
# /* block, and as the tail of another identifier, so the cfg is scanned with a
# state machine instead: // to end of line, /* until */ or end of file, and
# "..." are all dead ground, and the key needs a left boundary.
_VPP_KEY = "vppDisablePassword"
_VPP_VALUE = re.compile(r"\s*=\s*([^;\s]+)")
# The config language quotes with ' as well as " (Bohemia's Config Parser reads
# both; ronda 4, R3-F-02): either delimits dead ground.
_STRING_DELIMITERS = (chr(34), chr(39))
# One clean SteamID64 per line, the same token dayz-test.ps1:398 keeps.
_STEAM_ID64 = re.compile(r"\d{17}")
_UNREADABLE = object()


class HostVppFiles:
    """The only host access this preflight has: one read-only probe.

    No write surface exists on purpose. A preflight that repaired the workspace
    would be the ps1 again; the refusal is what the ficha asked for. is_dir went
    with the folder probe: existence is now proven by reading the mod's own
    meta.cpp, and a directory that exists proves nothing about what is in it.
    """

    def read_text(self, path: str) -> str:
        """Up to the cap PLUS ONE character, so the caller can see the overflow.

        Returning exactly the cap made a truncated prefix indistinguishable
        from a whole file, and a cfg whose later assignment contradicted the
        first one passed. The extra character is the overflow sentinel.
        """
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(_MAX_PREFLIGHT_READ_CHARS + 1)


@dataclass(frozen=True, slots=True)
class VppPreflightResult:
    """Closed, path-free verdict: tokens only, so it can cross the MCP wire."""

    error_code: str | None
    missing: tuple[str, ...]
    warnings: tuple[str, ...]
    hint: str


@dataclass(frozen=True, slots=True)
class VppPreflightPaths:
    """Where a server run keeps its config and its VPP state."""

    server_root: str
    server_config: str
    server_profiles: str
    superadmins: str
    credentials: str


def mode_starts_server(mode: str) -> bool:
    """Whether this mode launches a server, per the M12 mode authority.

    An unknown name fails closed as a server start: the gate refuses rather
    than wave through a mode it cannot classify. In production the request
    layer already rejected unknown names (dayz_test_request.py:297), so this
    only covers an authority that moved underneath a parsed request.
    """
    try:
        record = dayz_test_modes.resolve_mode(mode)
    except dayz_test_modes.ModeAuthorityError:
        return True
    return any(
        step.kind == "start" and step.role == "server" for step in record.steps
    )


def effective_mod_entries(payload: dict[str, object]) -> tuple[str, ...]:
    """The -mod= list the sealed worker will build, in the order it builds it.

    Same composition as dayz_test_worker._mods (dayz_test_worker.py:205-211),
    read off the CANONICAL payload, where no_base_mods and the policy defaults
    are already resolved (dayz_test_request.py:364-387). There is exactly one
    source of the effective list: the document the launcher itself receives,
    which is the very payload this function is handed.
    """
    base = payload.get("base_mods") or []
    extra = payload.get("extra_mods") or []
    return (
        *(str(item) for item in base),
        "@" + str(payload["mod"]),
        *(str(item) for item in extra),
    )


def vpp_preflight_paths(
    payload: dict[str, object],
    policy: dayz_test_request.RequestProjectPolicy,
) -> VppPreflightPaths:
    """The server config and VPP state paths of this exact run.

    INVARIANT (DZ-R7): these are the paths dayz_test_worker._start_core
    composes for the server role (dayz_test_worker.py:222-232). The root name
    is read from the same M12 record the worker's own mode comes from, not from
    a literal here; the join is duplicated because the worker is sealed inside
    app.pyz and cannot be imported from this route. The two are pinned against
    each other in tests/test_vpp_preflight.py.
    """
    record = dayz_test_modes.resolve_mode(str(payload["mode"]))
    roots = [
        step.root
        for step in record.steps
        if step.kind == "start" and step.role == "server" and step.root
    ]
    if len(roots) != 1:
        raise ValueError(VPP_PREFLIGHT_FAILED)
    server_root = ntpath.join(policy.dev_root, str(roots[0]))
    profiles = ntpath.join(server_root, _PROFILES_DIR)
    permissions = ntpath.join(profiles, _VPP_PROFILE_DIR, _VPP_PERMISSIONS_DIR)
    return VppPreflightPaths(
        server_root=server_root,
        server_config=ntpath.join(server_root, SERVER_CONFIG_NAME),
        server_profiles=profiles,
        superadmins=ntpath.join(permissions, _SUPERADMINS_DIR, _SUPERADMINS_NAME),
        credentials=ntpath.join(permissions, _CREDENTIALS_NAME),
    )


def _live_assignments(config: str, key: str) -> list[str]:
    """Values assigned to `key` in the ground the engine actually reads.

    One left-to-right pass over config-language text (serverDZ.cfg and
    meta.cpp share it). // runs to the end of the line, /* runs to */ or,
    unterminated, to the end of the file, and a quoted string -- 'single' or
    "double", the language has both -- is dead ground too: all of them
    swallowed a key that a raw regex then counted as live. The key also needs
    a left boundary, so `notvppDisablePassword` stops matching; the `=` that
    _VPP_VALUE demands right after it is the right boundary. Escapes are not
    honoured inside strings: neither file has any, and treating a
    backslash-quote as a closing quote can only end a string early, which puts
    more text under scrutiny, never less. An unterminated string runs to the
    end of the file, so nothing after it is live: the gate then refuses
    rather than guesses where the engine would have stopped.
    """
    values: list[str] = []
    index = 0
    length = len(config)
    newline = chr(10)
    while index < length:
        quote = config[index]
        if quote in _STRING_DELIMITERS:
            index += 1
            while index < length and config[index] != quote:
                index += 1
            index += 1
            continue
        if config.startswith("//", index):
            end = config.find(newline, index)
            index = length if end == -1 else end + 1
            continue
        if config.startswith("/*", index):
            end = config.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        if config.startswith(key, index) and (
            index == 0
            or not (config[index - 1].isalnum() or config[index - 1] == "_")
        ):
            match = _VPP_VALUE.match(config, index + len(key))
            if match is not None:
                values.append(match.group(1))
                index = match.end()
                continue
        index += 1
    return values


def _vpp_password_is_disabled(config: str) -> bool:
    """True only when every LIVE assignment of the key says 1.

    Two live assignments that disagree are refused: this layer cannot rank them
    against the engine's own parser, and guessing is how a gate goes green on a
    cfg that will still ask the superadmin for a password.
    """
    values = _live_assignments(config, _VPP_KEY)
    return bool(values) and all(value.strip() == "1" for value in values)


def _read_or_absent(files: object, path: str) -> object:
    reader = files.read_text
    try:
        return reader(path)
    except FileNotFoundError:
        return None
    except OSError:
        return _UNREADABLE


def _is_vpp_candidate(entry: str) -> bool:
    """Whether this -mod= entry is worth checking. A NAME, not an identity.

    A relative entry is the folder name the ps1 used; an absolute entry is the
    Workshop install, whose last segment is the published id. Both forms only
    select a candidate: the identity is proven from the mod's own meta.cpp.
    """
    return ntpath.basename(entry).casefold() in VPP_MOD_IDENTITIES


def _resolved_mod_path(
    entry: str, policy: dayz_test_request.RequestProjectPolicy
) -> str | None:
    """The exact path the worker will hand to the engine, or None if unknowable.

    An absolute entry is exact: the worker only normalises it
    (dayz_test_worker.py:197-202). A relative entry is resolved by the worker
    against worker-runtime.json's single mods_root, which this route cannot
    read; build_native_launcher.py:503 only guarantees mods_root is ONE of the
    policy's mod_roots. With exactly one declared root the two are the same
    path; with several, which one the launch uses is unknown here.
    """
    if ntpath.isabs(entry):
        return ntpath.normpath(entry)
    if len(policy.mod_roots) != 1:
        return None
    return ntpath.join(policy.mod_roots[0], entry)


def _proves_vpp_identity(path: str, files: object) -> bool | None:
    """True/False from the mod's own meta.cpp; None when it cannot be read.

    `publishedid` is the identity Steam assigns; the folder name is not. Every
    live form of this mod carries it. The proof is exactly ONE live assignment
    of that key whose value is the canonical decimal id. A second live
    assignment, a value with a suffix (1828439124evil), the id in a comment or
    inside another key, and a file past the read cap (a prefix proves nothing)
    all read as some other mod, never as this one.
    """
    meta = _read_or_absent(files, ntpath.join(path, _MOD_META_NAME))
    if not isinstance(meta, str):
        return None
    if len(meta) > _MAX_PREFLIGHT_READ_CHARS:
        return False
    values = _live_assignments(meta, _MOD_PUBLISHED_ID_KEY)
    return len(values) == 1 and values[0] == VPP_WORKSHOP_ID


def evaluate_vpp_preflight(
    payload: dict[str, object],
    policy: dayz_test_request.RequestProjectPolicy,
    *,
    files: object | None = None,
) -> VppPreflightResult:
    """Decide, without writing anything, whether this run may start a server.

    Deterministic and cheap findings block (missing); what the ps1 only seeded
    best-effort warns (warnings), because this route cannot seed it and a
    server with no superadmin still boots.
    """
    if not mode_starts_server(str(payload["mode"])):
        return VppPreflightResult(
            error_code=None, missing=(), warnings=(), hint=VPP_PREFLIGHT_HINT
        )
    host = HostVppFiles() if files is None else files
    missing: list[str] = []
    warnings: list[str] = []

    requested = [
        entry for entry in effective_mod_entries(payload) if _is_vpp_candidate(entry)
    ]
    if not requested:
        missing.append("vpp_mod_not_requested")
    else:
        resolved = [_resolved_mod_path(entry, policy) for entry in requested]
        paths = [path for path in resolved if path is not None]
        if not paths:
            # Every candidate is a relative name under a multi-root policy.
            # Nothing can be proven about a path the launch may not even use,
            # and "unknown" is not "authorised": the hint names the form that
            # always verifies, the absolute Workshop path the six live
            # multi-root policies already use.
            missing.append("vpp_mod_root_ambiguous")
        else:
            proofs = [_proves_vpp_identity(path, host) for path in paths]
            if any(proof is True for proof in proofs):
                pass
            elif any(proof is False for proof in proofs):
                # Read, and it is some other mod. The name was never identity.
                missing.append("vpp_mod_identity")
            else:
                missing.append("vpp_mod_folder")

    paths = vpp_preflight_paths(payload, policy)
    config = _read_or_absent(host, paths.server_config)
    if config is _UNREADABLE:
        missing.append("server_config_unreadable")
    elif config is None:
        missing.append("server_config")
    elif len(str(config)) > _MAX_PREFLIGHT_READ_CHARS:
        # The read is capped, so a bigger file arrives truncated and a later
        # assignment contradicting the first one would be invisible. A prefix
        # is not the file: say so rather than validate the part we saw.
        missing.append("server_config_unverifiable")
    elif not _vpp_password_is_disabled(str(config)):
        missing.append("vpp_disable_password")

    superadmins = _read_or_absent(host, paths.superadmins)
    if not isinstance(superadmins, str) or not any(
        _STEAM_ID64.fullmatch(line.strip()) for line in superadmins.splitlines()
    ):
        warnings.append("vpp_superadmins_absent")
    if not isinstance(_read_or_absent(host, paths.credentials), str):
        warnings.append("vpp_credentials_absent")

    return VppPreflightResult(
        error_code=VPP_PREFLIGHT_FAILED if missing else None,
        missing=tuple(missing),
        warnings=tuple(warnings),
        hint=VPP_PREFLIGHT_HINT,
    )


def select_request_policy(
    payload: dict[str, object],
    policies: tuple[dayz_test_request.RequestProjectPolicy, ...],
) -> dayz_test_request.RequestProjectPolicy:
    """The one policy this payload was parsed against (mod plus dev_root)."""
    selected = next(
        (
            candidate
            for candidate in policies
            if candidate.mod == payload["mod"]
            and candidate.dev_root == payload["dev_root"]
        ),
        None,
    )
    if selected is None:
        raise ValueError("invalid_dayz_test_policy")
    return selected


def preflight_vpp_payload(
    payload: dict[str, object],
    policies: tuple[dayz_test_request.RequestProjectPolicy, ...],
    *,
    files: object | None = None,
) -> VppPreflightResult:
    return evaluate_vpp_preflight(
        payload, select_request_policy(payload, policies), files=files
    )


def preflight_vpp_request(
    raw_request: bytes,
    *,
    sealed_policies: tuple[object, ...],
    files: object | None = None,
) -> VppPreflightResult:
    """Evaluate the preflight over the request the launcher itself will parse.

    The parse is the same call with the same policies execute_native_launcher_
    transaction runs below, so the payload examined here is the payload the
    sealed worker receives. Callers that already hold the parsed payload use
    preflight_vpp_payload and do not parse twice.
    """
    semantic = tuple(item.policy for item in sealed_policies)
    parsed = dayz_test_request.parse_dayz_test_request(
        raw_request, policies=semantic
    )
    return preflight_vpp_payload(parsed.payload, semantic, files=files)


def enforce_vpp_preflight(
    payload: dict[str, object],
    policies: tuple[dayz_test_request.RequestProjectPolicy, ...],
    *,
    files: object | None = None,
) -> VppPreflightResult:
    """Refuse a server start without usable admin tools. Fail closed.

    There is no bypass parameter on this route: wiring one would have to travel
    through secure_launcher.py, whose bytes are pinned by the sealed build
    contract. The refusal names what is missing so the caller can fix it.
    """
    result = preflight_vpp_payload(payload, policies, files=files)
    if result.error_code is not None:
        raise ValueError(result.error_code)
    return result


async def execute_native_launcher_transaction(
    raw_request: bytes,
    *,
    sealed_policies: tuple[
        request_path_authority.SealedRequestProjectPolicy, ...
    ],
    control_client: _ControlClient,
    consumer: Callable[..., Awaitable[int]],
    max_wait_s: float | None = None,
    queue_progress_cb: Callable[
        [float, float | None, str | None], Awaitable[None]
    ]
    | None = None,
) -> int:
    if type(sealed_policies) is not tuple or not callable(consumer):
        raise ValueError("invalid_native_launcher_transaction")
    semantic_policies = tuple(item.policy for item in sealed_policies)
    parsed = dayz_test_request.parse_dayz_test_request(
        raw_request, policies=semantic_policies
    )
    # ficha df93. Fail closed HERE: before any path is accredited, before the
    # lease is asked for and before a process exists. Both launch routes reach
    # this function -- the CLI through secure_launcher.run_secure_launcher and
    # the MCP tool through dayz_test_tool._execute_request -- so a server that
    # would start without admin tools is refused on either.
    enforce_vpp_preflight(parsed.payload, semantic_policies)

    with request_path_authority.accredit_request_paths(
        parsed, policies=sealed_policies
    ) as accredited_paths:
        response = await control_client.session_acquire_wait(
            "dayz-test",
            max_wait_s=max_wait_s,
            progress_cb=queue_progress_cb,
        )
        try:
            lease_token, lease_id, client_identity_json = _validate_grant(response)
        except NativeLauncherTransactionError as error:
            recoverable_token = (
                response.get("lease_token")
                if isinstance(response, dict)
                and response.get("status") == "active"
                else None
            )
            if isinstance(recoverable_token, str) and recoverable_token:
                try:
                    await lease_supervisor.protected_release_and_verify(
                        control_client, recoverable_token
                    )
                except BaseException as cleanup_error:
                    error.add_note(
                        "launcher cleanup degraded: "
                        f"{type(cleanup_error).__name__}"
                    )
            raise
        supervisor = lease_supervisor.LeaseHeartbeatSupervisor(
            control_client,
            lease_token=lease_token,
            lease_id=lease_id,
        )
        cancel_event = asyncio.Event()
        consumer_task: asyncio.Task[int] | None = None
        failure_task: asyncio.Task[lease_supervisor.LeaseHeartbeatError] | None = None
        primary: BaseException | None = None
        cleanup_error: BaseException | None = None
        result: int | None = None
        supervisor.start()
        try:
            consumer_task = asyncio.create_task(
                consumer(
                    canonical_request=parsed.canonical_bytes,
                    request_sha256=parsed.sha256,
                    client_identity_json=client_identity_json,
                    lease_token=lease_token,
                    cancel_event=cancel_event,
                    accredited_paths=accredited_paths,
                    heartbeat_supervisor=supervisor,
                )
            )
            failure_task = asyncio.create_task(supervisor.wait_failed())
            done, _pending = await asyncio.wait(
                (consumer_task, failure_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if failure_task in done:
                cancel_event.set()
                await _finish_task(consumer_task)
                raise failure_task.result()
            result = consumer_task.result()
            if type(result) is not int or not 0 <= result <= 255:
                raise NativeLauncherTransactionError("invalid_consumer_exit")
            supervisor.ensure_healthy()
        except BaseException as error:
            primary = error
        finally:
            cleanup_task = asyncio.create_task(
                _cleanup_transaction(
                    consumer_task=consumer_task,
                    failure_task=failure_task,
                    cancel_event=cancel_event,
                    supervisor=supervisor,
                    control_client=control_client,
                    lease_token=lease_token,
                )
            )
            delayed_cancellation: asyncio.CancelledError | None = None
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError as error:
                    delayed_cancellation = error
            cleanup_error = cleanup_task.result()
            if delayed_cancellation is not None and primary is None:
                primary = delayed_cancellation

        if primary is not None:
            if cleanup_error is not None:
                primary.add_note(
                    f"launcher cleanup degraded: {type(cleanup_error).__name__}"
                )
            raise primary
        if cleanup_error is not None:
            raise cleanup_error
        if result is None:
            raise NativeLauncherTransactionError("invalid_consumer_exit")
        return result
