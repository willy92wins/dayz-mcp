"""Recover one immutable client authority after an accredited daemon changes."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from dayz_mcp import accredited_daemon_transport, pinned_keyfile
from dayz_mcp.daemon_policy_contract import AccreditedDaemonPolicy


RETRY_HEADER_NAME = "X-DayZ-MCP-Credential-Retry"
RETRY_HEADER_VALUE = "1"
REACCREDITED_HEADER_NAME = "X-DayZ-MCP-Reaccredited"
REACCREDITED_HEADER_VALUE = "1"
REACCREDITATION_FAILED = "daemon_reaccreditation_failed_open_new_session"

# The re-accreditation step that failed, and the remedy that holds for it
# (fb-20260824-010234-acf3). The code keeps its name, which other tools grep,
# but "open a new session" cures none of these steps: a new session derives the
# same authority from the same registration and checks the same listener. Each
# stage is one raise site of REACCREDITATION_FAILED below; the replay site is
# split by what the transport reported, because that decides whether anything
# was sent. Only a stage from this closed set ever reaches a caller.
_REACCREDITATION_REMEDIES = {
    "identity_after_send": (
        "The identity check failed after request bytes may have left, so the "
        "request was not replayed and may have reached an unverified process. "
        "Call session_status before repeating a mutation: it checks the "
        "daemon's identity before it sends anything. A new MCP session is not "
        "needed."
    ),
    "replay_identity": (
        "The process on the daemon port still fails the identity check against "
        "this client's registration after one re-accreditation. Retrying, or "
        "opening a new MCP session, repeats the same check against the same "
        "process and the same registration. What changes it is a new daemon "
        "generation started from the registered argv: the daemon exits by "
        "itself after its --idle-timeout without game polls or client "
        "requests, or the host operator stops that daemon process outside "
        "MCP; the next call then starts a new one. This client can do neither. "
        "python -m dayz_mcp.doctor --daemon-policy normal --json shows what "
        "the listener fails."
    ),
    "replay_transport": (
        "The replay could not reach the daemon and sent nothing, so nothing "
        "ran. Retry the call: the next call looks for the daemon again. A new "
        "MCP session is not needed."
    ),
    "replay_deadline": (
        "The call's time budget ran out before the replay was sent, so nothing "
        "ran. Retry the call, with a longer timeout when the tool takes one. A "
        "new MCP session is not needed."
    ),
    "replay_response": (
        "The replay was sent but its answer was lost, so the request may have "
        "run. Call session_status before repeating a mutation. A new MCP "
        "session is not needed."
    ),
    "replay_unauthorized": (
        "The replacement daemon passed the identity check but refused this "
        "client's credential, so nothing ran. Retry the call once: when the "
        "daemon refuses it too, the client reads the pinned keyfile again and "
        "retries with that credential. A new MCP session is not needed."
    ),
    "refresh_retry_identity": (
        "The daemon changed while this client retried a refused request: the "
        "first attempt was refused and the retry failed its identity check "
        "before sending, so nothing ran. Retry the call once; if it fails with "
        "reaccreditation_stage=replay_identity, follow that stage's remedy. A "
        "new MCP session is not needed."
    ),
}
REACCREDITATION_STAGES = frozenset(_REACCREDITATION_REMEDIES)


def public_reaccreditation_stage(value: object) -> str | None:
    """Return value only when it is a member of REACCREDITATION_STAGES."""
    if isinstance(value, str) and value in REACCREDITATION_STAGES:
        return value
    return None


def reaccreditation_hint(stage: object) -> str | None:
    """reaccreditation_stage=<stage>. <remedy>, or None without a known stage."""
    known = public_reaccreditation_stage(stage)
    if known is None:
        return None
    return f"reaccreditation_stage={known}. {_REACCREDITATION_REMEDIES[known]}"


class CredentialRefreshError(RuntimeError):
    """Sanitized failure after a closed credential or re-accreditation path."""

    _CODES = frozenset(
        {
            "client_policy_untrusted_open_new_session",
            "daemon_credential_desynchronized",
            "daemon_reaccreditation_failed_open_new_session",
            "stale_client_credential_refresh_failed",
            "stale_client_credential_retry_rejected",
            "stale_client_credential_retry_transport_failed",
        }
    )

    def __init__(
        self,
        code: str,
        *,
        request_stage: str = "post_request",
        http_bytes_sent: int = 1,
        reaccreditation_stage: str | None = None,
    ) -> None:
        if code not in self._CODES:
            raise ValueError("invalid_credential_refresh_error")
        if reaccreditation_stage is not None and (
            code != REACCREDITATION_FAILED
            or public_reaccreditation_stage(reaccreditation_stage) is None
        ):
            raise ValueError("invalid_credential_refresh_error")
        self.code = code
        self.request_stage = request_stage
        self.http_bytes_sent = http_bytes_sent
        # Separate metadata, like request_stage: str() stays the bare code.
        self.reaccreditation_stage = reaccreditation_stage
        super().__init__(code)


def public_refresh_error(error: CredentialRefreshError) -> str:
    """The text a caller reads: the stable code, then the stage and its remedy.

    Every other code, and a re-accreditation failure without a known stage,
    stays the bare code.
    """
    hint = reaccreditation_hint(getattr(error, "reaccreditation_stage", None))
    return error.code if hint is None else f"{error.code}: {hint}"


def _replay_transport_stage(
    error: accredited_daemon_transport.AccreditedTransportError,
) -> str:
    """Name the replay step a transport error stopped (acf3).

    Only a failure reported before anything was sent is an identity, deadline
    or transport step. Anything else may have reached the daemon, so it is the
    response step, whose remedy does not assume that nothing ran.
    """
    if error.request_stage != "pre_request" or error.http_bytes_sent != 0:
        return "replay_response"
    if error.code == "daemon_identity_unverified":
        return "replay_identity"
    if error.code == "daemon_request_deadline_exceeded":
        return "replay_deadline"
    return "replay_transport"


@dataclass(frozen=True)
class _CredentialSnapshot:
    secret: str = field(repr=False)
    epoch: int


class RefreshingDaemonCredential:
    """Own the current credential for one immutable accredited authority."""

    def __init__(
        self,
        *,
        policy: AccreditedDaemonPolicy,
        request_fn: Callable[..., tuple[int, bytes]] | None = None,
    ) -> None:
        if type(policy) is not AccreditedDaemonPolicy:
            raise ValueError("invalid_daemon_policy")
        policy.revalidate()
        secret = pinned_keyfile.read_pinned_keyfile(policy.keyfile)
        policy.revalidate()
        self.policy = policy
        self._authority = (
            policy.kind,
            policy.host,
            policy.port,
            policy.keyfile,
            policy.native_executable,
            policy.argv,
            policy.cwd,
            policy.security_build_id,
            policy.authority_sha256,
        )
        self._request_fn = request_fn
        self._snapshot = _CredentialSnapshot(secret=secret, epoch=0)
        self._refresh_lock = threading.Lock()
        self._reaccredit_epoch = 0

    def _assert_authority_unchanged(
        self,
        *,
        request_stage: str,
        http_bytes_sent: int,
    ) -> None:
        if type(self.policy) is not AccreditedDaemonPolicy:
            raise CredentialRefreshError(
                "client_policy_untrusted_open_new_session",
                request_stage=request_stage,
                http_bytes_sent=http_bytes_sent,
            )
        try:
            current = (
                self.policy.kind,
                self.policy.host,
                self.policy.port,
                self.policy.keyfile,
                self.policy.native_executable,
                self.policy.argv,
                self.policy.cwd,
                self.policy.security_build_id,
                self.policy.authority_sha256,
            )
        except Exception:
            raise CredentialRefreshError(
                "client_policy_untrusted_open_new_session",
                request_stage=request_stage,
                http_bytes_sent=http_bytes_sent,
            ) from None
        if current != self._authority:
            raise CredentialRefreshError(
                "client_policy_untrusted_open_new_session",
                request_stage=request_stage,
                http_bytes_sent=http_bytes_sent,
            )

    def _revalidate_authority(
        self,
        *,
        request_stage: str,
        http_bytes_sent: int,
        allow_stale_policy: bool = False,
    ) -> None:
        self._assert_authority_unchanged(
            request_stage=request_stage,
            http_bytes_sent=http_bytes_sent,
        )
        try:
            self.policy.revalidate()
        except Exception:
            if not allow_stale_policy:
                raise CredentialRefreshError(
                    "client_policy_untrusted_open_new_session",
                    request_stage=request_stage,
                    http_bytes_sent=http_bytes_sent,
                ) from None
        self._assert_authority_unchanged(
            request_stage=request_stage,
            http_bytes_sent=http_bytes_sent,
        )

    def _send(
        self,
        *,
        secret: str,
        method: str,
        path: str,
        query: dict[str, str],
        body: bytes | None,
        headers: dict[str, str],
        deadline: float,
    ) -> tuple[int, bytes]:
        request = (
            self._request_fn
            or accredited_daemon_transport.verified_daemon_http_request
        )
        (
            _kind,
            host,
            port,
            _keyfile,
            native_executable,
            argv,
            cwd,
            _security_build_id,
            _authority_sha256,
        ) = self._authority
        return request(
            host=host,
            port=port,
            key=secret,
            method=method,
            path=path,
            query=dict(query),
            body=body,
            headers=dict(headers),
            deadline=deadline,
            expected_executable=native_executable,
            expected_argv=list(argv),
            expected_cwd=cwd,
        )

    def _retry_after_daemon_replacement(
        self,
        *,
        observed_reaccredit_epoch: int,
        method: str,
        path: str,
        query: dict[str, str],
        body: bytes | None,
        headers: dict[str, str],
        deadline: float,
        allow_stale_policy: bool = False,
    ) -> tuple[int, bytes]:
        with self._refresh_lock:
            self._assert_authority_unchanged(
                request_stage="pre_request",
                http_bytes_sent=0,
            )
            if self._reaccredit_epoch == observed_reaccredit_epoch:
                self._revalidate_authority(
                    request_stage="pre_request",
                    http_bytes_sent=0,
                    allow_stale_policy=allow_stale_policy,
                )
                self._reaccredit_epoch += 1
            current = self._snapshot

        replay_headers = dict(headers)
        replay_headers[REACCREDITED_HEADER_NAME] = REACCREDITED_HEADER_VALUE
        try:
            retry_status, retry_body = self._send(
                secret=current.secret,
                method=method,
                path=path,
                query=query,
                body=body,
                headers=replay_headers,
                deadline=deadline,
            )
        except accredited_daemon_transport.AccreditedTransportError as error:
            raise CredentialRefreshError(
                "daemon_reaccreditation_failed_open_new_session",
                request_stage=error.request_stage,
                http_bytes_sent=error.http_bytes_sent,
                reaccreditation_stage=_replay_transport_stage(error),
            ) from None
        except TimeoutError:
            raise CredentialRefreshError(
                "daemon_reaccreditation_failed_open_new_session",
                request_stage="pre_request",
                http_bytes_sent=0,
                reaccreditation_stage="replay_deadline",
            ) from None
        if retry_status == 401:
            raise CredentialRefreshError(
                "daemon_reaccreditation_failed_open_new_session",
                request_stage="post_request",
                http_bytes_sent=1,
                reaccreditation_stage="replay_unauthorized",
            )
        return retry_status, retry_body

    def request_with_refresh(
        self,
        *,
        method: str,
        path: str,
        query: dict[str, str],
        body: bytes | None,
        headers: dict[str, str],
        deadline: float,
        allow_stale_policy: bool = False,
    ) -> tuple[int, bytes]:
        self._assert_authority_unchanged(
            request_stage="pre_request",
            http_bytes_sent=0,
        )
        request_query = dict(query)
        request_headers = dict(headers)
        request_body = None if body is None else bytes(body)
        observed = self._snapshot
        observed_reaccredit_epoch = self._reaccredit_epoch
        try:
            status, response_body = self._send(
                secret=observed.secret,
                method=method,
                path=path,
                query=request_query,
                body=request_body,
                headers=request_headers,
                deadline=deadline,
            )
        except accredited_daemon_transport.AccreditedTransportError as error:
            if error.code != "daemon_identity_unverified":
                raise
            if error.request_stage != "pre_request" or error.http_bytes_sent != 0:
                raise CredentialRefreshError(
                    "daemon_reaccreditation_failed_open_new_session",
                    request_stage=error.request_stage,
                    http_bytes_sent=error.http_bytes_sent,
                    reaccreditation_stage="identity_after_send",
                ) from None
            return self._retry_after_daemon_replacement(
                observed_reaccredit_epoch=observed_reaccredit_epoch,
                method=method,
                path=path,
                query=request_query,
                body=request_body,
                headers=request_headers,
                deadline=deadline,
                allow_stale_policy=allow_stale_policy,
            )
        if status != 401:
            return status, response_body

        with self._refresh_lock:
            self._assert_authority_unchanged(
                request_stage="post_request",
                http_bytes_sent=1,
            )
            current = self._snapshot
            if current.epoch == observed.epoch:
                self._revalidate_authority(
                    request_stage="post_request",
                    http_bytes_sent=1,
                    allow_stale_policy=allow_stale_policy,
                )
                if not allow_stale_policy:
                    try:
                        refreshed = pinned_keyfile.read_pinned_keyfile(
                            self._authority[3]
                        )
                    except Exception:
                        raise CredentialRefreshError(
                            "stale_client_credential_refresh_failed",
                            request_stage="post_request",
                            http_bytes_sent=1,
                        ) from None
                    self._revalidate_authority(
                        request_stage="post_request",
                        http_bytes_sent=1,
                        allow_stale_policy=allow_stale_policy,
                    )
                    current = _CredentialSnapshot(
                        secret=refreshed,
                        epoch=current.epoch + 1,
                    )
                    self._snapshot = current

        retry_headers = dict(request_headers)
        retry_headers[RETRY_HEADER_NAME] = RETRY_HEADER_VALUE
        try:
            retry_status, retry_body = self._send(
                secret=current.secret,
                method=method,
                path=path,
                query=request_query,
                body=request_body,
                headers=retry_headers,
                deadline=deadline,
            )
        except accredited_daemon_transport.AccreditedTransportError as error:
            if error.code == "daemon_identity_unverified":
                raise CredentialRefreshError(
                    "daemon_reaccreditation_failed_open_new_session",
                    request_stage="post_request",
                    http_bytes_sent=1,
                    reaccreditation_stage="refresh_retry_identity",
                ) from None
            raise CredentialRefreshError(
                "stale_client_credential_retry_transport_failed",
                request_stage="post_request",
                http_bytes_sent=1,
            ) from None
        except OSError:
            raise CredentialRefreshError(
                "stale_client_credential_retry_transport_failed",
                request_stage="post_request",
                http_bytes_sent=1,
            ) from None
        if retry_status == 401:
            code = (
                "daemon_credential_desynchronized"
                if current.secret == observed.secret
                else "stale_client_credential_retry_rejected"
            )
            raise CredentialRefreshError(
                code,
                request_stage="post_request",
                http_bytes_sent=1,
            )
        return retry_status, retry_body
