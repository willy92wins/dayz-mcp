"""The Steam session gate for a DayZ client under Proton (Linux).

On Windows the client gate reads ``HKCU\\Software\\Valve\\Steam\\ActiveProcess`` (the
registered ``steam.exe`` PID and the active user) and pins the live ``steam.exe``;
with consent a helper process repairs the PID or restarts Steam
(``steam_prepare_supervisor``). The Linux Steam client records its PID in
``~/.steam/steam.pid``, the file ``steam.sh`` itself checks. ``SteamPID`` in
``~/.steam/registry.vdf`` is not usable: it can name a start-up process that is already
gone (measured 2026-11-06: ``SteamPID`` 20624, no such process; ``steam.pid`` 20622, the
live ``ubuntu12_32/steam``). The ``ActiveProcess`` key inside a Proton prefix holds
a Wine PID that Proton's ``steam.exe`` shim writes per launch, so it says nothing about
the native Steam.

``LinuxSteamPreflightProvider`` answers the same questions from those files and
``/proc``, so ``steam_preflight.evaluate_steam_session`` and
``steam_launch_guard.coherent_identity`` apply unchanged:

- registered PID: ``steam.pid``;
- live Steam processes: this user's processes whose image is ``steam``
  (``ubuntu12_32/steam``), not the ``steam.sh`` wrapper;
- creation time: in Windows FILETIME ticks, which the identity pin expects;
- startup complete: a ``steamwebhelper`` runs under that Steam process (Windows reads a
  log marker that Linux Steam does not write);
- active user: Linux Steam does not publish who is logged in in these files. The
  snapshot reports 1 whenever ``steam.pid`` holds a PID, so a logged-out Steam passes
  this gate and the client fails at Steam initialisation instead.

``LinuxSteamPreparationGate`` has the interface ``ProcessLifecycle`` uses
(``claim``/``release``/``degraded``/``prepare``/``final_check``) and is read-only: it
never writes the registry or restarts Steam, consent or not. A Steam that is not
running is ``steam_not_running``; one whose identity cannot be pinned is
``steam_session_stale``.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from . import steam_launch_guard
from . import steam_preflight as steam

STEAM_IMAGE = "steam"
_WEBHELPER_IMAGE = "steamwebhelper"
_FILETIME_UNIX_EPOCH = 116444736000000000
REMEDIATION_LINUX = (
    "start the Steam client and log in (it must be running for the DayZ client under Proton); "
    "dayz-mcp does not start or repair Steam on Linux"
)


def _pid_path() -> Path:
    return Path.home() / ".steam" / "steam.pid"


def _proc_stat_fields(pid: int) -> list[bytes]:
    raw = Path(f"/proc/{pid}/stat").read_bytes()
    return raw[raw.rindex(b")") + 2 :].split()


def _image(pid: int) -> str:
    return os.path.basename(os.readlink(f"/proc/{pid}/exe"))


def _uid(pid: int) -> int:
    return os.stat(f"/proc/{pid}").st_uid


class LinuxSteamPreflightProvider:
    """``SteamPreflightProvider`` for the native Linux Steam client."""

    def read_active_process(self) -> steam.SteamActiveProcessSnapshot:
        text = _pid_path().read_text(encoding="ascii", errors="replace").strip()
        pid = int(text) if text.isdigit() else 0
        return steam.SteamActiveProcessSnapshot(pid=pid, active_user=1 if pid > 0 else 0)

    def process_exists(self, pid: int) -> bool:
        return os.path.isdir(f"/proc/{int(pid)}")

    def process_image_path(self, pid: int) -> str:
        return os.readlink(f"/proc/{int(pid)}/exe")

    def process_creation_ticks(self, pid: int) -> int:
        start_ticks = int(_proc_stat_fields(int(pid))[19])
        hertz = os.sysconf("SC_CLK_TCK")
        boot = None
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                boot = int(line.split()[1])
                break
        if boot is None:
            raise OSError("boot_time_unavailable")
        # 100 ns ticks since 1601, as the Windows identity pin stores them.
        return _FILETIME_UNIX_EPOCH + boot * 10_000_000 + (start_ticks * 10_000_000) // hertz

    def steam_process_pids(self) -> tuple[int, ...]:
        me = os.geteuid()
        found: list[int] = []
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            pid = int(name)
            try:
                if _image(pid) == STEAM_IMAGE and _uid(pid) == me:
                    found.append(pid)
            except OSError:
                continue
        return tuple(sorted(found))

    def steam_startup_complete(self, pid: int) -> bool:
        target = int(pid)
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            child = int(name)
            try:
                if _image(child) != _WEBHELPER_IMAGE:
                    continue
                current = child
                for _ in range(16):
                    parent = int(_proc_stat_fields(current)[1])
                    if parent == target:
                        return True
                    if parent <= 1:
                        break
                    current = parent
            except (OSError, ValueError, IndexError):
                continue
        return False


class LinuxSteamPreparationGate:
    """``ProcessLifecycle``'s Steam gate off Windows: evaluate and pin, never repair."""

    def __init__(self, provider: object | None = None) -> None:
        self._lock = threading.Lock()
        self.provider = provider if provider is not None else LinuxSteamPreflightProvider()

    def claim(self) -> bool:
        return self._lock.acquire(blocking=False)

    def release(self) -> None:
        self._lock.release()

    @property
    def degraded(self) -> bool:
        return False

    def prepare(self, *, consent, authority_active, mutation_allowed) -> steam_launch_guard.Preparation:
        del consent, mutation_allowed  # nothing here mutates Steam
        if not authority_active():
            return steam_launch_guard.Preparation(error_code="steam_prepare_cancelled")
        session = steam.evaluate_steam_session(self.provider)
        if session.error_code is not None:
            return steam_launch_guard.Preparation(error_code=session.error_code)
        identity = steam_launch_guard.coherent_identity(self.provider)
        if identity is None:
            return steam_launch_guard.Preparation(error_code=steam.STEAM_SESSION_STALE)
        try:
            started = self.provider.steam_startup_complete(identity.pid) is True
        except Exception:
            started = False
        return steam_launch_guard.Preparation(
            identity=identity, startup="observed" if started else "unavailable"
        )

    def final_check(self, prepared: steam_launch_guard.Preparation) -> bool:
        return steam_launch_guard.final_check(prepared, self.provider)
