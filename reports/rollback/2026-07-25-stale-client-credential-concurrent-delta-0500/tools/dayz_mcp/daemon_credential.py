"""Thread-safe daemon credential refresh after one accredited HTTP 401."""

from __future__ import annotations

import hmac
import threading
from dataclasses import dataclass
from typing import Callable

from dayz_mcp import pinned_keyfile
from dayz_mcp.accredited_daemon_transport import AccreditedTransportError
from dayz_mcp.daemon_policy_contract import AccreditedDaemonPolicy


CREDENTIAL_RETRY_HEADER = "X-DayZ-MCP-Credential-Retry"
CREDENTIAL_RETRY_VALUE = "1"


class DaemonCredentialError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        request_stage: str = "post_request",
        http_bytes_sent: int = 1,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.request_stage = request_stage
        self.http_bytes_sent = http_bytes_sent


@dataclass(frozen=True, repr=False)
class _CredentialSnapshot:
    key: str
    epoch: int


@dataclass(frozen=True, repr=False)
class _AuthoritySnapshot:
    kind: str
    host: str
    port: int
    keyfile: str
    native_executable: str
    argv: tuple[str, ...]
    cwd: str
    security_build_id: str | None
    authority_sha256: str

    @classmethod
    def from_policy(cls, policy: AccreditedDaemonPolicy) -> _AuthoritySnapshot:
        return cls(
            kind=policy.kind,
            host=policy.host,
            port=policy.port,
            keyfile=policy.keyfile,
            native_executable=policy.native_executable,
            argv=policy.argv,
            cwd=policy.cwd,
            security_build_id=policy.security_build_id,
            authority_sha256=policy.authority_sha256,
        )


class RefreshingDaemonCredential:
    """Own one redacted credential snapshot and refresh it at most once per 401."""

    def __init__(
        self,
        *,
        policy: AccreditedDaemonPolicy,
        initial_key: str | None = None,
    ) -> None:
        if type(policy) is not AccreditedDaemonPolicy:
            raise ValueError("invalid_daemon_policy")
        policy.revalidate()
        self._authority = _AuthoritySnapshot.from_policy(policy)
        key = (
            initial_key
            if initial_key is not None
            else pinned_keyfile.read_pinned_keyfile(self._authority.keyfile)
        )
        if not isinstance(key, str) or not key:
            raise ValueError("invalid_daemon_credential")
        self.policy = policy
        self._lock = threading.Lock()
        self._snapshot = _CredentialSnapshot(key=key, epoch=0)

    def __repr__(self) -> str:
        with self._lock:
            epoch = self._snapshot.epoch
        return f"{type(self).__name__}(epoch={epoch}, credential=<redacted>)"

    def _current(self) -> _CredentialSnapshot:
        with self._lock:
            return self._snapshot

    def _assert_authority_unchanged(
        self,
        *,
        request_stage: str,
        http_bytes_sent: int,
    ) -> None:
        """Check the in-memory pin without rereading host configs on the fast path."""
        try:
            if (
                type(self.policy) is not AccreditedDaemonPolicy
                or _AuthoritySnapshot.from_policy(self.policy) != self._authority
            ):
                raise ValueError("daemon_policy_drift")
        except Exception:
            raise DaemonCredentialError(
                "credential_source_untrusted",
                request_stage=request_stage,
                http_bytes_sent=http_bytes_sent,
            ) from None

    def _revalidate_authority(
        self,
        *,
        request_stage: str = "post_request",
        http_bytes_sent: int = 1,
    ) -> None:
        """Run the policy's external hook and require the original authority pin."""
        try:
            if type(self.policy) is not AccreditedDaemonPolicy:
                raise ValueError("invalid_daemon_policy")
            self.policy.revalidate()
            if _AuthoritySnapshot.from_policy(self.policy) != self._authority:
                raise ValueError("daemon_policy_drift")
        except Exception:
            raise DaemonCredentialError(
                "credential_source_untrusted",
                request_stage=request_stage,
                http_bytes_sent=http_bytes_sent,
            ) from None

    def _refresh_after_401(
        self, observed: _CredentialSnapshot
    ) -> tuple[_CredentialSnapshot, bool]:
        with self._lock:
            current = self._snapshot
            if current.epoch != observed.epoch:
                return current, not hmac.compare_digest(current.key, observed.key)
            self._revalidate_authority()
            try:
                refreshed_key = pinned_keyfile.read_pinned_keyfile(
                    self._authority.keyfile
                )
            except Exception:
                raise DaemonCredentialError(
                    "stale_client_credential_refresh_failed"
                ) from None
            self._revalidate_authority()
            if not isinstance(refreshed_key, str) or not refreshed_key:
                raise DaemonCredentialError(
                    "stale_client_credential_refresh_failed"
                )
            changed = not hmac.compare_digest(refreshed_key, observed.key)
            current = _CredentialSnapshot(
                key=refreshed_key, epoch=observed.epoch + 1
            )
            self._snapshot = current
            return current, changed

    def exchange(
        self,
        *,
        request_fn: Callable[..., tuple[int, bytes]],
        method: str,
        path: str,
        query: dict[str, str] | None,
        body: bytes | None,
        headers: dict[str, str] | None,
        deadline: float,
        max_response_bytes: int | None = None,
        time_fn: Callable[[], float] | None = None,
    ) -> tuple[int, bytes]:
        self._assert_authority_unchanged(
            request_stage="pre_request",
            http_bytes_sent=0,
        )
        snapshot = self._current()
        request_kwargs: dict[str, object] = {
            "host": self._authority.host,
            "port": self._authority.port,
            "key": snapshot.key,
            "method": method,
            "path": path,
            "query": dict(query or {}),
            "body": body,
            "headers": dict(headers or {}),
            "deadline": deadline,
            "expected_executable": self._authority.native_executable,
            "expected_argv": list(self._authority.argv),
            "expected_cwd": self._authority.cwd,
        }
        if max_response_bytes is not None:
            request_kwargs["max_response_bytes"] = max_response_bytes
        if time_fn is not None:
            request_kwargs["time_fn"] = time_fn

        status, response_body = request_fn(**request_kwargs)
        if status != 401:
            return status, response_body

        refreshed, changed = self._refresh_after_401(snapshot)
        retry_kwargs = dict(request_kwargs)
        retry_kwargs["key"] = refreshed.key
        retry_headers = dict(request_kwargs["headers"])
        retry_headers[CREDENTIAL_RETRY_HEADER] = CREDENTIAL_RETRY_VALUE
        retry_kwargs["headers"] = retry_headers
        try:
            retry_status, retry_body = request_fn(**retry_kwargs)
        except AccreditedTransportError as error:
            if error.code == "daemon_identity_unverified":
                raise
            raise DaemonCredentialError(
                "stale_client_credential_retry_transport_failed"
            ) from None
        except (ConnectionError, OSError):
            raise DaemonCredentialError(
                "stale_client_credential_retry_transport_failed"
            ) from None
        if retry_status == 401:
            raise DaemonCredentialError(
                "stale_client_credential_retry_rejected"
                if changed
                else "daemon_credential_desynchronized"
            )
        return retry_status, retry_body
