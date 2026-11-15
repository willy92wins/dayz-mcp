"""Run DayZDiag under Proton in a headless sway session (Linux).

``ProcessLifecycle._launch`` uses ``launch_client`` off Windows for every role of a
project whose policy names ``DayZDiag_x64.exe``. A DayZDiag client only joins a DayZDiag
server (the native ``DayZServer`` answers 0x00020017, measured 2026-11-06), so a server
is the same binary with ``-server``, as on Windows. It is the dayz-dev skill's headless
chain (``dayz-client-headless.sh``, same author), ported:

- a headless sway (``WLR_BACKENDS=headless``) per launch, so nothing shows on the
  user's desktop, with the game window fullscreen on its one output;
- inside it the chain Steam itself runs: Steam Linux Runtime 4 ``_v2-entry-point``
  -> ``proton run`` -> ``DayZDiag_x64.exe`` in the game's own prefix
  (``steamapps/compatdata/<app>``), not ``steam -applaunch``, which would hand the
  launch to the running Steam and its display;
- ``DayZDiag_x64.exe``, not the retail ``DayZ_x64.exe``: the daemon treats a running
  retail client as the user's own game (retail quarantine), exactly as on Windows;
- the worker's argv is kept; POSIX paths in ``-mod``, ``-profiles``, ``-mission`` and
  ``-config`` become ``Z:\\...`` (measured 2026-11-05: DayZDiag 1.30.164289 under
  GE-Proton11-1 loads a mod from ``-mod=Z:\\...\\@Mod`` and writes its RPT and logs into
  ``-profiles=Z:\\...``), and ``-nolauncher -skipintro -nosplash`` are added;
- one game per role: a running retail ``DayZ_x64.exe`` or ``DayZ_BE.exe`` refuses
  any launch, a running ``DayZDiag_x64.exe`` of the same role (``-server`` or not)
  refuses this one. The two roles of a run share the game's prefix.

``launch_client`` returns once the game process exists under this session's sway and
``NativeProcessGuard`` reads the same identity twice (Wine rewrites the process's
argv to the Windows command line after it starts). That host PID is the run's
record; stop kills it, and the session script then ends sway. Sessions whose game is
gone are cleaned up at the next launch.

Environment: ``DAYZ_MCP_PROTON`` (Proton tool name, default ``GE-Proton11-1``),
``DAYZ_MCP_STEAM_ROOT`` (default ``~/.steam/steam`` resolved). The daemon needs
``XDG_RUNTIME_DIR`` (sway's socket) and ``sway`` on ``PATH``.
"""

from __future__ import annotations

import os
import secrets
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

CLIENT_IMAGE = "DayZDiag_x64.exe"
GAME_IMAGES = ("DayZ_x64.exe", "DayZDiag_x64.exe", "DayZ_BE.exe")
DEFAULT_PROTON = "GE-Proton11-1"
SESSION_PREFIX = "dayz-mcp-client-"
APPEAR_TIMEOUT_S = 120.0
STABLE_READS = 2
_APP_IDS = {"DayZ": "221100", "DayZ Exp": "1024020"}
_PATH_FLAGS = ("-mod=", "-profiles=", "-mission=", "-config=")
_ADDED_FLAGS = ("-nolauncher", "-skipintro", "-nosplash")


class ProtonClientError(RuntimeError):
    pass


@dataclass
class LaunchedClient:
    """What ``ProcessLifecycle`` gets back: ``pid`` is the game's host PID."""

    pid: int
    session: str
    sway_pid: int
    state_dir: str


# ---------------------------------------------------------------- paths


def windows_path(value: str) -> str:
    """``/tmp/x`` -> ``Z:\\tmp\\x`` (Wine maps ``Z:`` to ``/``). Other values unchanged."""
    if value.startswith("/") and not value.startswith("//"):
        return "Z:" + os.path.normpath(value).replace("/", "\\")
    return value


def translate_args(args: list[str]) -> list[str]:
    """The worker's client arguments for the Windows image under Proton."""
    out: list[str] = []
    for item in args:
        lowered = item.casefold()
        flag = next((f for f in _PATH_FLAGS if lowered.startswith(f)), None)
        if flag is None:
            out.append(item)
            continue
        value = item[len(flag):]
        parts = value.split(";") if flag == "-mod=" else [value]
        out.append(item[: len(flag)] + ";".join(windows_path(part) for part in parts))
    present = {item.casefold() for item in out}
    return [*(flag for flag in _ADDED_FLAGS if flag not in present), *out]


def _resolution(args: list[str]) -> str:
    width, height = "1280", "720"
    for item in args:
        lowered = item.casefold()
        if lowered.startswith("-x=") and item[3:].isdigit():
            width = item[3:]
        elif lowered.startswith("-y=") and item[3:].isdigit():
            height = item[3:]
    return f"{width}x{height}"


@dataclass
class ProtonChain:
    game_dir: Path
    client: Path
    app_id: str
    compat_data: Path
    steam_root: Path
    proton: Path
    entry_point: Path


def _library_of(game_dir: Path) -> Path:
    # <library>/steamapps/common/<game>
    if game_dir.parent.name != "common" or game_dir.parent.parent.name != "steamapps":
        raise ProtonClientError(f"game_dir_not_in_a_steam_library:{game_dir}")
    return game_dir.parent.parent.parent


def resolve_chain(game_path: Path, environ: dict[str, str] | None = None) -> ProtonChain:
    """Find the client, its prefix, the Proton tool and the runtime for ``game_path``."""
    environ = os.environ if environ is None else environ
    game_dir = Path(game_path).resolve()
    app_id = _APP_IDS.get(game_dir.name)
    if app_id is None:
        raise ProtonClientError(f"unknown_dayz_app:{game_dir.name}")
    client = game_dir / CLIENT_IMAGE
    if not client.is_file():
        raise ProtonClientError(f"no_{CLIENT_IMAGE}_in:{game_dir}")
    library = _library_of(game_dir)
    steam_root = Path(
        environ.get("DAYZ_MCP_STEAM_ROOT") or (Path.home() / ".steam" / "steam")
    ).resolve()
    libraries = [library] + ([steam_root] if steam_root != library else [])
    compat_data = library / "steamapps" / "compatdata" / app_id
    if not (compat_data / "pfx").is_dir():
        raise ProtonClientError(f"proton_prefix_missing:{compat_data} (start DayZ once from Steam)")
    tool_name = environ.get("DAYZ_MCP_PROTON") or DEFAULT_PROTON
    candidates = [steam_root / "compatibilitytools.d" / tool_name] + [
        root / "steamapps" / "common" / tool_name for root in libraries
    ]
    proton = next((path / "proton" for path in candidates if (path / "proton").is_file()), None)
    if proton is None:
        raise ProtonClientError(f"proton_tool_missing:{tool_name}")
    entry = next(
        (
            root / "steamapps" / "common" / "SteamLinuxRuntime_4" / "_v2-entry-point"
            for root in libraries
            if (root / "steamapps" / "common" / "SteamLinuxRuntime_4" / "_v2-entry-point").is_file()
        ),
        None,
    )
    if entry is None:
        raise ProtonClientError("steam_linux_runtime_4_missing")
    return ProtonChain(game_dir, client, app_id, compat_data, steam_root, proton, entry)


# ---------------------------------------------------------------- processes


def _ppid(pid: int) -> int | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes()
        return int(raw[raw.rindex(b")") + 2 :].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def descends_from(pid: int, ancestor: int, *, ppid_of=None, depth: int = 32) -> bool:
    ppid_of = ppid_of or _ppid
    current = pid
    for _ in range(depth):
        parent = ppid_of(current)
        if parent is None or parent <= 1:
            return False
        if parent == ancestor:
            return True
        current = parent
    return False


def _running_games() -> list[dict[str, object]]:
    from dayz_mcp import orphan_guard

    snapshot = orphan_guard.snapshot_processes_with_parent(list(GAME_IMAGES))
    if snapshot.get("known") is not True:
        raise ProtonClientError("process_scan_unknown")
    return list(snapshot.get("processes") or [])


def _is_server_process(pid: int) -> bool:
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return any(item.lower() == b"-server" for item in argv[1:])


def conflicting_games(games: list[dict[str, object]], *, server: bool) -> list[dict[str, object]]:
    return [
        game
        for game in games
        if game.get("name") != CLIENT_IMAGE or _is_server_process(int(game["pid"])) == server
    ]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _state_root() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        raise ProtonClientError("local_cache_unavailable")
    return Path(local) / "DayZ_MCP" / "proton-clients"


def _read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def _cmdline(pid: int) -> list[bytes]:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return []


def _is_session_sway(pid: int, state: Path) -> bool:
    """The PID still runs this session's sway: its arguments name ``<state>/sway.conf``.

    A session directory outlives a reboot, and by then its PID may belong to any
    process: only the session's own sway may be signalled. argv[0] is not compared:
    a wrapper on PATH that execs sway leaves its own path there, and one that runs
    sway as its child holds the PID with sway's arguments after its own. The conf
    path carries the session's random name.
    """
    return os.fsencode(state / "sway.conf") in _cmdline(pid)[1:]


def remove_stale_sessions(root: Path, games: list[dict[str, object]]) -> None:
    """End sessions whose game is gone (their sway idles otherwise) and drop their state.

    A session with a running game is kept. Otherwise a PID that no longer runs the
    session's sway is not signalled, and its directory goes.
    """
    if not root.is_dir():
        return
    for entry in root.iterdir():
        if not entry.name.startswith(SESSION_PREFIX):
            continue
        sway = _read_pid(entry / "sway.pid")
        if sway is not None and _alive(sway):
            if any(descends_from(int(game["pid"]), sway) for game in games):
                continue
            if _is_session_sway(sway, entry):
                try:
                    os.killpg(sway, signal.SIGTERM)
                except OSError:
                    pass
                continue
        shutil.rmtree(entry, ignore_errors=True)


# ---------------------------------------------------------------- launch


def _session_files(state: Path, chain: ProtonChain, args: list[str], resolution: str) -> None:
    (state / "args").write_text("".join(f"{item}\n" for item in args), encoding="utf-8")
    q = shlex.quote
    script = f"""#!/bin/sh
# dayz-mcp: one DayZ client under Proton in this headless sway session.
echo "$WAYLAND_DISPLAY" > {q(str(state / "wayland-display"))}
export STEAM_COMPAT_DATA_PATH={q(str(chain.compat_data))}
export STEAM_COMPAT_CLIENT_INSTALL_PATH={q(str(chain.steam_root))}
export STEAM_COMPAT_INSTALL_PATH={q(str(chain.game_dir))}
export STEAM_COMPAT_APP_ID={chain.app_id} SteamAppId={chain.app_id} SteamGameId={chain.app_id}
export PROTON_ENABLE_WAYLAND=1
cd {q(str(chain.game_dir))}
set --
while IFS= read -r a; do set -- "$@" "$a"; done < {q(str(state / "args"))}
{q(str(chain.entry_point))} --verb=run -- {q(str(chain.proton))} run {q(str(chain.client))} "$@"
echo "game exited: $?"
# The session lives as long as the game: stop (a kill of the game) ends sway too.
swaymsg exit
"""
    (state / "game.sh").write_text(script, encoding="utf-8")
    os.chmod(state / "game.sh", 0o700)
    app_id = chain.client.name.casefold()
    (state / "sway.conf").write_text(
        f"output HEADLESS-1 resolution {resolution} position 0 0\n"
        "xwayland enable\n"
        f'for_window [app_id="{app_id}"] fullscreen enable\n'
        f"exec {q(str(state / 'game.sh'))} > {q(str(state / 'game.log'))} 2>&1\n",
        encoding="utf-8",
    )


def _sway_environment() -> dict[str, str]:
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if not runtime or not os.path.isdir(runtime):
        raise ProtonClientError("xdg_runtime_dir_unavailable")
    env = {
        key: os.environ[key]
        for key in ("HOME", "PATH", "LANG", "LC_ALL", "USER", "LOGNAME", "XDG_RUNTIME_DIR")
        if os.environ.get(key)
    }
    env.update({"WLR_BACKENDS": "headless", "WLR_LIBINPUT_NO_DEVICES": "1"})
    return env


def _wait_for_game(sway: subprocess.Popen, state: Path, deadline: float, guard: object) -> int:
    """The game's host PID once the guard reads the same identity ``STABLE_READS`` times."""
    from dayz_mcp import orphan_guard

    previous: tuple[int, str, str] | None = None
    stable = 0
    while time.monotonic() < deadline:
        if sway.poll() is not None:
            raise ProtonClientError("client_session_ended:" + _log_tail(state))
        candidates = [
            int(row["pid"])
            for row in orphan_guard.snapshot_processes_with_parent([CLIENT_IMAGE]).get("processes", [])
            if descends_from(int(row["pid"]), sway.pid)
        ]
        if len(candidates) > 1:
            raise ProtonClientError("client_process_ambiguous")
        if candidates:
            snapshot = guard.snapshot(candidates[0])
            if isinstance(snapshot, dict) and snapshot.get("identity_complete") is True:
                key = (
                    candidates[0],
                    str(snapshot.get("executable_sha256")),
                    str(snapshot.get("command_line_sha256")),
                )
                stable = stable + 1 if key == previous else 1
                previous = key
                if stable >= STABLE_READS:
                    return candidates[0]
        time.sleep(0.5)
    raise ProtonClientError("client_process_not_observed:" + _log_tail(state))


def _log_tail(state: Path) -> str:
    try:
        return (state / "game.log").read_text(encoding="utf-8", errors="replace")[-300:].replace("\n", " | ")
    except OSError:
        return ""


def launch_client(argv: list[str], game_path: Path, *, guard: object | None = None) -> LaunchedClient:
    """``ProcessLifecycle``'s launcher for DayZDiag on Linux, in any role.

    ``argv[0]`` (the policy's executable, already checked by the daemon's allow rule)
    is replaced by ``<game_path>/DayZDiag_x64.exe``; the rest is the worker's argv.
    """
    if not argv:
        raise ProtonClientError("empty_argv")
    server = any(item.casefold() == "-server" for item in argv[1:])
    chain = resolve_chain(Path(game_path))
    games = _running_games()
    conflicts = conflicting_games(games, server=server)
    if conflicts:
        role = "server" if server else "client"
        raise ProtonClientError(
            f"dayz_{role}_already_running:" + ",".join(f"{g['name']}:{g['pid']}" for g in conflicts)
        )
    if shutil.which("sway") is None:
        raise ProtonClientError("sway_missing")
    env = _sway_environment()
    root = _state_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    remove_stale_sessions(root, games)
    session = SESSION_PREFIX + secrets.token_hex(6)
    state = root / session
    state.mkdir(mode=0o700)
    args = translate_args(list(argv[1:]))
    _session_files(state, chain, args, _resolution(args))
    if guard is None:
        from dayz_mcp.native_process_guard import NativeProcessGuard

        guard = NativeProcessGuard()
    log = open(state / "sway.log", "wb")
    try:
        sway = subprocess.Popen(
            ["sway", "-c", str(state / "sway.conf")],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(state),
            close_fds=True,
            start_new_session=True,
        )
    finally:
        log.close()
    (state / "sway.pid").write_text(f"{sway.pid}\n", encoding="ascii")
    try:
        pid = _wait_for_game(sway, state, time.monotonic() + APPEAR_TIMEOUT_S, guard)
    except BaseException:
        try:
            os.killpg(sway.pid, signal.SIGTERM)
        except OSError:
            pass
        raise
    return LaunchedClient(pid=pid, session=session, sway_pid=sway.pid, state_dir=str(state))
