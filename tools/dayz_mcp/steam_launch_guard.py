"""Steam lifetime readiness policy, used only within admitted launch authority.

Registry coherence and a startup marker mitigate startup races; neither proves
Steamworks availability. All host I/O in preparation runs in the helper process.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import time

from . import steam_preflight as steam

PREPARE_BUDGET_S = 215.0
CLEANUP_BUDGET_S = 5.0
RECENT_PROCESS_S = 180.0


class PreparationCancelled(BaseException):
    """Not swallowed by the legacy provider's fail-closed Exception handlers."""

    def __init__(self, code: str = "steam_prepare_cancelled"):
        self.code = code


@dataclass(frozen=True)
class SteamIdentity:
    pid: int
    creation_ticks: int

    @property
    def created_at(self) -> float:
        return (self.creation_ticks - 116444736000000000) / 10_000_000


@dataclass(frozen=True)
class Preparation:
    error_code: str | None = None
    identity: SteamIdentity | None = None
    startup: str = "unavailable"
    pid_repair: str | None = None
    restarted: bool = False
    cleanup_degraded: bool = False

    def payload(self) -> dict:
        return asdict(self)


def live_identity(provider) -> SteamIdentity | None:
    """Pin even a stale registry's sole live Steam process across independent reads."""
    try:
        pids = steam._safe_live_pids(provider)
        if pids is None or len(pids) != 1:
            return None
        pid = pids[0]
        ticks = provider.process_creation_ticks(pid)
        if type(ticks) is not int or ticks <= 116444736000000000:
            return None
        if not provider.process_exists(pid) or steam.ntpath.basename(
            provider.process_image_path(pid)
        ).casefold() != "steam.exe":
            return None
        if steam._safe_live_pids(provider) != pids or provider.process_creation_ticks(pid) != ticks:
            return None
        return SteamIdentity(pid, ticks)
    except Exception:
        return None


def coherent_identity(provider) -> SteamIdentity | None:
    before = steam._read_stable_snapshot(provider)
    identity = live_identity(provider)
    session = steam.evaluate_steam_session(provider)
    if (before is None or identity is None or session.error_code is not None
            or session.steam_registered_pid != identity.pid
            or steam._read_stable_snapshot(provider) != before
            or live_identity(provider) != identity):
        return None
    return identity


def prepare(provider, host, consent: bool, *, wall_time=time.time) -> Preparation:
    """One repair/restart attempt, one absolute budget; no retry on identity drift."""
    if type(consent) is not bool:
        return Preparation(error_code="steam_prepare_failed")
    host.checkpoint()
    initial = live_identity(provider)
    host.expected_repair_identity = initial
    started = wall_time()
    # A process recent at admission never ages into the limited old-process path.
    old_stable = initial is not None and started - initial.created_at > RECENT_PROCESS_S
    outcome = Preparation(error_code="steam_session_stale")

    def wait_startup(_provider, _host, remediated):
        nonlocal outcome
        expected = coherent_identity(provider)
        if expected is None:
            outcome = Preparation(error_code="steam_session_stale")
            return remediated
        limited = old_stable and expected == initial and not remediated.steam_restart_fallback
        while True:
            host.checkpoint()
            if coherent_identity(provider) != expected:
                outcome = Preparation(error_code="steam_identity_changed")
                return remediated
            try:
                marker = provider.steam_startup_complete(expected.pid)
            except Exception:
                marker = False
            host.checkpoint()
            if coherent_identity(provider) != expected:
                outcome = Preparation(error_code="steam_identity_changed")
                return remediated
            if marker is True or limited:
                outcome = Preparation(
                    identity=expected,
                    startup="observed" if marker is True else "old_stable_unobserved",
                    pid_repair=remediated.steam_pid_repair_reason,
                    restarted=remediated.steam_restart_fallback,
                )
                return remediated
            host.sleep(0.2)

    current = steam.evaluate_steam_session(provider)
    if current.error_code is None:
        wait_startup(provider, host, steam._remediation_result(current))
    elif consent:
        repaired = steam.remediate_stale_steam_session(
            provider, host, startup_waiter=wait_startup,
        )
        if repaired.error_code is not None:
            return Preparation(error_code="steam_session_stale",
                               pid_repair=repaired.steam_pid_repair_reason,
                               restarted=repaired.steam_restart_fallback)
    return outcome


def final_check(prepared: Preparation, provider=None) -> bool:
    provider = provider or steam.WindowsSteamPreflightProvider()
    return prepared.error_code is None and prepared.identity is not None and coherent_identity(provider) == prepared.identity
