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
    ) -> None:
        if code not in self._CODES:
            raise ValueError("invalid_credential_refresh_error")
        self.code = code
        self.request_stage = request_stage
        self.http_bytes_sent = http_bytes_sent
        super().__init__(code)


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
    ) -> None:
        self._assert_authority_unchanged(
            request_stage=request_stage,
            http_bytes_sent=http_bytes_sent,
        )
        try:
            self.policy.revalidate()
        except Exception:
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
            ) from None
        except TimeoutError:
            raise CredentialRefreshError(
                "daemon_reaccreditation_failed_open_new_session",
                request_stage="pre_request",
                http_bytes_sent=0,
            ) from None
        if retry_status == 401:
            raise CredentialRefreshError(
                "daemon_reaccreditation_failed_open_new_session",
                request_stage="post_request",
                http_bytes_sent=1,
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
                ) from None
            return self._retry_after_daemon_replacement(
                observed_reaccredit_epoch=observed_reaccredit_epoch,
                method=method,
                path=path,
                query=request_query,
                body=request_body,
                headers=request_headers,
                deadline=deadline,
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
                )
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
