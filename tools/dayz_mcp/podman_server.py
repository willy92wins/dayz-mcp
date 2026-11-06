"""Run a DayZ dedicated server in a rootless podman container (Linux).

The way the server is started is ported from dzo (dayz-server-operator, internal/boottest
and images/runtime; same author, relicensed here under this project's licence):

- the runtime image is debian:trixie-slim with what DayZServer links against
  (libcap2, libstdc++6, libc6) and ca-certificates, nothing of this project in it;
- a private *tree* per run: one symlink per top-level entry of the Steam DayZServer
  install, real ``keys/``, ``battleye/`` (one symlink per entry), ``profiles/``, a copy of
  the mission, our own ``serverDZ.cfg`` (random passwords so nobody can join, the query
  port set), and ``@Mod`` symlinks;
- ``podman run --network host``: the tree is mounted read-write at its own path, the
  server install and every mod read-only as overlays (``:O``) at their own paths, so the
  tree's symlinks resolve and nothing is ever written into the Steam install or a build;
- ``sh -c 'ulimit -c 0 && exec "$@" < stdin'``: no 5 GB core on a crash, and the
  experimental builds' assertion prompt answered with "i" (Ignore);
- mod paths in ``-mod=``/``-servermod=`` are relative ``@name`` (the engine silently
  ignores absolute ones), ports are free for TCP and UDP and never a game default;
- stop: SIGTERM, a 15 s grace (a cleanly stopped server can hang in Steam API threads),
  then SIGKILL, then ``podman rm -f``.

What dayz-mcp adds: the container is the run's identity (name, ID, image ID, labels),
not a host PID, and ``bridge_config`` writes ``$profile:dayz_mcp.json`` for the
``@DayZ_MCP`` bridge before the start. With ``--network host`` the bridge's
``http://127.0.0.1:<port>/`` is the host's loopback.

Command line: ``python -m dayz_mcp.podman_server start|status|stop|image ...`` (JSON out).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import string
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_NAME = "localhost/dayz-mcp-runtime"
IMAGE_TAG = "1"
CONTAINER_PREFIX = "dayz-mcp-server-"
LABEL = "dayz-mcp.podman-server"
STDIN_ANSWER = "i\n"
STOP_GRACE_S = 15
_MOD_NAME = re.compile(r"^@[A-Za-z0-9_.\-]+$")
_SKIP_TOP = {"profiles", "serverDZ.cfg", "keys", "mpmissions"}

CONTAINERFILE = """\
# dayz-mcp-runtime: the image DayZServer runs in (ported from dzo's dzo-runtime).
# The server build, the mods and the tree are mounted in; nothing of dayz-mcp lives here.
# libcap2, libstdc++6 and libc6 are what DayZServer links against; ca-certificates is for
# the server's and mods' HTTPS calls.
FROM debian:trixie-slim
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \\
    && apt-get -y upgrade \\
    && apt-get -y install --no-install-recommends ca-certificates libcap2 libstdc++6 \\
    && rm -rf /var/lib/apt/lists/*
ENV LANG=C.UTF-8
"""


class PodmanServerError(RuntimeError):
    pass


@dataclass
class Mod:
    name: str  # "@Name", the folder name in the tree
    path: Path  # the mod folder (with addons/, keys/)
    server: bool = False  # -servermod instead of -mod


@dataclass
class Ports:
    game: int
    query: int
    rcon: int


@dataclass
class RunSpec:
    server_dir: Path
    tree: Path
    mission: str = "dayzOffline.chernarusplus"
    mods: list[Mod] = field(default_factory=list)
    extra_args: list[str] = field(default_factory=list)
    ports: Ports | None = None
    podman: str = "podman"


# ---------------------------------------------------------------- image


def _podman(spec_or_bin: RunSpec | str, *args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
    binary = spec_or_bin.podman if isinstance(spec_or_bin, RunSpec) else spec_or_bin
    return subprocess.run([binary, *args], check=check, capture_output=True, text=True, **kw)


def ensure_image(podman: str = "podman", rebuild: bool = False) -> str:
    """Build the runtime image once; return its image ID (sha256 of its config)."""
    ref = f"{IMAGE_NAME}:{IMAGE_TAG}"
    if not rebuild:
        found = _podman(podman, "image", "inspect", "--format", "{{.Id}}", ref, check=False)
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    import tempfile

    with tempfile.TemporaryDirectory(prefix="dayz-mcp-image-") as context:  # empty build context
        Path(context, "Containerfile").write_text(CONTAINERFILE, encoding="utf-8")
        built = _podman(podman, "build", "-t", ref, "-f", str(Path(context, "Containerfile")), context, check=False)
    if built.returncode != 0:
        raise PodmanServerError("image_build_failed: " + (built.stderr or built.stdout)[-2000:])
    return _podman(podman, "image", "inspect", "--format", "{{.Id}}", ref).stdout.strip()


# ---------------------------------------------------------------- ports


def _free(port: int) -> bool:
    for kind in (socket.SOCK_DGRAM, socket.SOCK_STREAM):
        s = socket.socket(socket.AF_INET, kind)
        try:
            s.bind(("", port))
        except OSError:
            return False
        finally:
            s.close()
    return True


def free_ports(n: int = 3, start: int = 20000, stop: int = 60000) -> list[int]:
    """n ports free for TCP and UDP, at least 10 apart, never a game/query/RCon default."""
    out: list[int] = []
    base = start + (time.time_ns() // 1000) % max(stop - start - 100, 1)
    port = base
    while len(out) < n and port < stop:
        if not (2300 <= port <= 2310 or 27015 <= port <= 27017) and _free(port):
            out.append(port)
        port += 17
    if len(out) < n:
        raise PodmanServerError("no_free_ports")
    return out


# ---------------------------------------------------------------- tree


def _password(n: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _replace_link(target: Path, link: Path) -> None:
    if link.is_symlink() or link.exists():
        if link.is_dir() and not link.is_symlink():
            raise PodmanServerError(f"tree_entry_is_real_directory:{link}")
        link.unlink()
    link.symlink_to(target)


def _link_contents(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        _replace_link(entry, dst / entry.name)


def _set_scalar(text: str, key: str, value: str, quoted: bool) -> str:
    rendered = f'"{value}"' if quoted else value
    pattern = re.compile(rf"^(\s*){re.escape(key)}\s*=\s*[^;]*;", re.M)
    if pattern.search(text):
        return pattern.sub(lambda m: f"{m.group(1)}{key} = {rendered};", text, count=1)
    return f"{key} = {rendered};\n" + text


def server_cfg(stock: str, mission: str, query_port: int) -> str:
    """The stock serverDZ.cfg with the mission template, query port, a marked hostname and
    random passwords, so that nobody can join."""
    text = re.sub(r'(template\s*=\s*")[^"]*(")', lambda m: m.group(1) + mission + m.group(2), stock, count=1)
    text = _set_scalar(text, "steamQueryPort", str(query_port), quoted=False)
    host = re.search(r'^\s*hostname\s*=\s*"([^"]*)"', text, re.M)
    text = _set_scalar(text, "hostname", "[dayz-mcp] " + (host.group(1) if host else "test"), quoted=True)
    for key in ("password", "passwordAdmin"):
        text = _set_scalar(text, key, _password(), quoted=True)
    return text


def build_tree(spec: RunSpec, ports: Ports) -> None:
    srv = spec.server_dir.resolve()
    tree = spec.tree.resolve()
    if str(tree).startswith(str(srv) + os.sep) or tree == srv:
        raise PodmanServerError("tree_inside_server_install")
    if not (srv / "DayZServer").is_file():
        raise PodmanServerError(f"no_dayzserver_in:{srv}")
    tree.mkdir(parents=True, exist_ok=True)
    for entry in srv.iterdir():
        name = entry.name
        if name == "core" or name.startswith("core.") or name.startswith("@") or name in _SKIP_TOP:
            continue
        if name == "battleye":
            _link_contents(entry, tree / name)
        else:
            _replace_link(entry, tree / name)

    (tree / "serverDZ.cfg").write_text(
        server_cfg((srv / "serverDZ.cfg").read_text(encoding="utf-8", errors="replace"), spec.mission, ports.query),
        encoding="utf-8",
    )
    os.chmod(tree / "serverDZ.cfg", 0o600)
    be = tree / "profiles" / "battleye"
    be.mkdir(parents=True, exist_ok=True)
    (be / "beserver_x64.cfg").write_text(
        f"RConPassword {_password()}\nRestrictRCon 0\nRConPort {ports.rcon}\nRConIP 127.0.0.1\n", encoding="utf-8"
    )
    os.chmod(be / "beserver_x64.cfg", 0o600)

    keys = tree / "keys"
    keys.mkdir(exist_ok=True)
    for source in [srv / "keys", *[m.path / "keys" for m in spec.mods]]:
        if source.is_dir():
            for key in source.glob("*.bikey"):
                shutil.copyfile(key, keys / key.name)

    mission = tree / "mpmissions" / spec.mission
    template = srv / "mpmissions" / spec.mission
    if not template.is_dir():
        raise PodmanServerError(f"no_mission:{template}")
    if not mission.exists():  # keep storage_1 of an earlier run on the same tree
        shutil.copytree(template, mission, symlinks=False)

    for mod in spec.mods:
        if not _MOD_NAME.match(mod.name):
            raise PodmanServerError(f"bad_mod_name:{mod.name}")
        if not mod.path.is_dir():
            raise PodmanServerError(f"no_mod_dir:{mod.path}")
        _replace_link(mod.path.resolve(), tree / mod.name)


def server_args(ports: Ports, mods: list[Mod], extra: list[str]) -> list[str]:
    args = [
        "-config=serverDZ.cfg", "-profiles=profiles", f"-port={ports.game}", "-BEpath=profiles/battleye",
        "-nosplash", "-nopause", "-dologs", "-adminlog",
    ]
    client = [m.name for m in mods if not m.server]
    server = [m.name for m in mods if m.server]
    if client:
        args.append("-mod=" + ";".join(client))
    if server:
        args.append("-servermod=" + ";".join(server))
    return args + list(extra)


def bridge_config(tree: Path, url_port: int, key: str, instance: str = "", poll_hz: float = 5.0,
                  profile: str = "test") -> Path:
    """Write $profile:dayz_mcp.json for the @DayZ_MCP bridge."""
    path = tree / "profiles" / "dayz_mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"url": f"http://127.0.0.1:{int(url_port)}/", "key": key, "pollHz": poll_hz,
                "instance": instance, "profile": profile}
    path.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


# ---------------------------------------------------------------- run / stop / status


def start(spec: RunSpec, run_id: str | None = None) -> dict:
    """Build the tree and start the server container detached. Returns the run's identity."""
    image_id = ensure_image(spec.podman)
    if spec.ports is None:
        game, query, rcon = free_ports(3)
        spec.ports = Ports(game, query, rcon)
    build_tree(spec, spec.ports)
    tree = spec.tree.resolve()
    srv = spec.server_dir.resolve()
    (tree / "stdin").write_text(STDIN_ANSWER, encoding="utf-8")
    run_id = run_id or secrets.token_hex(4)
    name = CONTAINER_PREFIX + run_id
    argv = [
        "run", "-d", "--rm", "--name", name, "--network", "host",
        "--label", f"{LABEL}=1", "--label", f"{LABEL}.run={run_id}", "--label", f"{LABEL}.tree={tree}",
        "-v", f"{tree}:{tree}", "-v", f"{srv}:{srv}:O", "-w", str(tree),
    ]
    for mod in spec.mods:
        real = mod.path.resolve()
        argv += ["-v", f"{real}:{real}:O"]
    argv += [
        f"{IMAGE_NAME}:{IMAGE_TAG}", "/bin/sh", "-c",
        'ulimit -c 0 && exec "$@" < ./stdin > ./server.out 2>&1', "sh", "./DayZServer",
        *server_args(spec.ports, spec.mods, spec.extra_args),
    ]
    started = _podman(spec, *argv, check=False)
    if started.returncode != 0:
        raise PodmanServerError("container_start_failed: " + started.stderr[-2000:])
    identity = status(name, spec.podman)
    identity.update({"run_id": run_id, "tree": str(tree), "profiles": str(tree / "profiles"),
                     "ports": {"game": spec.ports.game, "query": spec.ports.query, "rcon": spec.ports.rcon},
                     "expected_image_id": image_id})
    return identity


def status(name: str, podman: str = "podman") -> dict:
    found = _podman(podman, "container", "inspect", name, check=False)
    if found.returncode != 0:
        return {"name": name, "running": False, "exists": False}
    doc = json.loads(found.stdout)[0]
    state = doc.get("State", {})
    labels = doc.get("Config", {}).get("Labels") or {}
    return {
        "name": name, "exists": True, "running": bool(state.get("Running")),
        "container_id": doc.get("Id"), "image_id": doc.get("Image"),
        "host_pid": state.get("Pid") or None, "started_at": state.get("StartedAt"),
        "managed": labels.get(LABEL) == "1",
    }


def stop(name: str, podman: str = "podman", grace_s: int = STOP_GRACE_S) -> dict:
    """SIGTERM, grace, SIGKILL (podman stop), then make sure nothing is left (rm -f)."""
    info = status(name, podman)
    if info.get("exists") and not info.get("managed"):
        raise PodmanServerError(f"not_a_dayz_mcp_container:{name}")
    _podman(podman, "stop", "-t", str(grace_s), name, check=False, timeout=grace_s + 30)
    _podman(podman, "rm", "-f", "-t", "0", name, check=False)
    return status(name, podman)


def mission_loaded(profiles: Path) -> bool:
    """The mission's scripts started: init.c as a script module (1.29), or the economy chose
    its storage directory (1.30 logs no module for init.c)."""
    for log in profiles.glob("script_*.log"):
        if "Module: $CurrentDir:mpmissions/" in log.read_text(errors="replace"):
            return True
    for rpt in profiles.glob("*.RPT"):
        if "[StorageDirs] :: Selected storage directory" in rpt.read_text(errors="replace"):
            return True
    return False


def wait_mission(profiles: Path, timeout_s: float = 180.0, name: str | None = None,
                 podman: str = "podman") -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if mission_loaded(profiles):
            return True
        if name is not None and not status(name, podman).get("running"):
            return False
        time.sleep(2)
    return False


# ---------------------------------------------------------------- lifecycle launcher


SERVER_BINARY = "DayZServer"
EXEC_WAIT_S = 10.0


@dataclass
class LaunchedContainer:
    """What ``ProcessLifecycle`` gets back from a launch: ``pid`` is DayZServer's host PID."""

    pid: int
    name: str
    container_id: str
    image_id: str
    tree: str


def _state_root() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        raise PodmanServerError("local_cache_unavailable")
    return Path(local) / "DayZ_MCP" / "podman-runs"


def _flag_value(argv: list[str], flag: str) -> str | None:
    prefix = flag.casefold() + "="
    values = [item[len(prefix):] for item in argv if item.casefold().startswith(prefix)]
    if len(values) > 1:
        raise PodmanServerError(f"duplicate_flag:{flag}")
    return values[0] if values else None


def _absolute_path(value: str | None, what: str) -> Path:
    if not value or not os.path.isabs(value) or os.path.normpath(value) != value:
        raise PodmanServerError(f"invalid_{what}")
    return Path(value)


def _remove_stale_trees(root: Path, podman: str) -> None:
    """Trees of containers that no longer exist (a stopped run leaves its tree)."""
    if not root.is_dir():
        return
    for entry in root.iterdir():
        if entry.name.startswith(CONTAINER_PREFIX) and not status(entry.name, podman).get("exists"):
            shutil.rmtree(entry, ignore_errors=True)


def _wait_for_server_exec(pid: int, binary_name: str, deadline: float) -> None:
    """PID 1 is ``sh`` until it execs DayZServer; the identity must be taken after."""
    while time.monotonic() < deadline:
        try:
            if os.path.basename(os.readlink(f"/proc/{pid}/exe")) == binary_name:
                return
        except OSError:
            pass
        time.sleep(0.05)
    raise PodmanServerError("server_exec_not_observed")


def _cgroup_names_container(pid: int, container_id: str) -> bool:
    try:
        text = Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return f"libpod-{container_id}.scope" in text


def launch_lifecycle_process(
    argv: list[str], cwd: str, podman: str = "podman", *, server_binary: Path | None = None
) -> LaunchedContainer:
    """``ProcessLifecycle``'s launcher on Linux: the worker's server argv, in a container.

    ``server_binary`` is the native DayZServer the daemon pairs with its game path;
    without it ``argv[0]`` must be one. The sealed worker builds the argv it builds
    on Windows, with POSIX paths from the Linux launcher policy: ``<diag_executable> -server -config=<cfg>
    -profiles=<dir> -mission=<dir> -mod=<abs;...> [-filePatching] -port=<n>
    [-serverMod=<abs;...>] <server_extra_args>``. Measured on DayZServer 1.30
    (2026-11-04): absolute ``-config``/``-profiles`` work and are kept, each mounted at
    its own path (the daemon wrote ``dayz_mcp.json`` into the profiles before this call
    and reads the RPT there). An absolute ``-mission`` does not (2026-11-06): the mission
    modules load, but ``init.c`` is not found ("Mission script has no main function,
    player connect will stay disabled!"), so the mission is mounted into the tree and
    named relative to it, ``mpmissions/<name>``. Absolute mod paths are
    ignored by the engine (dzo), so each mod becomes an ``@name`` link in a private
    tree that is the working directory. ``-server`` is dropped (the binary is a
    server); ``-dologs`` is added as dzo always passes it; ``-filePatching`` is
    dropped (it hangs the Linux server, see below). ``cwd`` (the policy's
    game directory) is not used: the tree is the working directory.

    Returns once DayZServer, not the ``sh`` that starts it, is the container's
    PID 1, after checking that this host PID's cgroup is the container's and that
    the container runs the runtime image. The caller records that PID like any
    other: ``NativeProcessGuard`` identity, terminated with SIGKILL, which ends the
    container (``--rm`` removes it).
    """
    del cwd
    if server_binary is None:
        # The policy names the Linux server itself (diag_executable = .../DayZServer).
        if not argv or not os.path.isabs(argv[0]) or os.path.basename(argv[0]) != SERVER_BINARY:
            raise PodmanServerError("not_a_linux_server_launch")
        server_binary = Path(argv[0])
    elif Path(server_binary).name != SERVER_BINARY or not argv:
        raise PodmanServerError("not_a_linux_server_launch")
    server_dir = Path(server_binary).parent.resolve()
    if not (server_dir / SERVER_BINARY).is_file():
        raise PodmanServerError("no_dayzserver")
    # -filePatching: measured 2026-11-04 on DayZServer 1.30 (Linux), twice, over
    # 4 minutes: with it the main thread busy-polls /proc/self/statm and
    # /proc/meminfo after the last building type loads and the landscape never
    # finishes; without it the landscape loads in ~10 s. The sealed worker adds it
    # unless no_file_patching, so it is dropped here and the container label
    # records that it was.
    dropped_file_patching = any(item.casefold() == "-filepatching" for item in argv[1:])
    args = [item for item in argv[1:] if item.casefold() not in {"-server", "-filepatching"}]
    config = _absolute_path(_flag_value(args, "-config"), "config")
    profiles = _absolute_path(_flag_value(args, "-profiles"), "profiles")
    mission = _absolute_path(_flag_value(args, "-mission"), "mission")
    if not config.is_file() or not mission.is_dir():
        raise PodmanServerError("config_or_mission_missing")
    profiles.mkdir(parents=True, exist_ok=True)

    image_id = ensure_image(podman)
    root = _state_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _remove_stale_trees(root, podman)
    name = CONTAINER_PREFIX + secrets.token_hex(6)
    tree = root / name
    tree.mkdir(mode=0o700)
    try:
        for entry in server_dir.iterdir():
            entry_name = entry.name
            if entry_name == "core" or entry_name.startswith(("core.", "@")) or entry_name in _SKIP_TOP:
                continue
            if entry_name == "battleye":
                _link_contents(entry, tree / entry_name)
            else:
                _replace_link(entry, tree / entry_name)
        (tree / "stdin").write_text(STDIN_ANSWER, encoding="utf-8")

        mounts: list[str] = []
        key_sources = [server_dir / "keys"]
        rewritten: list[str] = []
        for item in args:
            lowered = item.casefold()
            if lowered.startswith("-mod="):
                flag_length = len("-mod=")
            elif lowered.startswith("-servermod="):
                flag_length = len("-servermod=")
            else:
                rewritten.append(item)
                continue
            names: list[str] = []
            for value in filter(None, item[flag_length:].split(";")):
                path = Path(value)
                if not path.is_absolute():
                    raise PodmanServerError(f"relative_mod_path:{value}")
                real = path.resolve()
                if not real.is_dir():
                    raise PodmanServerError(f"no_mod_dir:{value}")
                mod_name = path.name if path.name.startswith("@") else "@" + path.name
                if not _MOD_NAME.match(mod_name):
                    raise PodmanServerError(f"bad_mod_name:{mod_name}")
                if (tree / mod_name).is_symlink() or (tree / mod_name).exists():
                    raise PodmanServerError(f"duplicate_mod_name:{mod_name}")
                (tree / mod_name).symlink_to(real)
                # :O, not :ro. Measured 2026-11-04 (DayZServer 1.30): from a read-only
                # bind mount the engine skips the mod without a word ("Adding
                # package" never appears); from an overlay it loads, and whatever it
                # writes stays in the container's upper layer, not in the build.
                mounts += ["-v", f"{real}:{real}:O"]
                key_sources.append(real / "keys")
                names.append(mod_name)
            rewritten.append(item[:flag_length] + ";".join(names))
        # The mount point exists before the container starts; the mission's writes
        # (storage_1) still land in the policy's mission directory.
        mission_in_tree = tree / "mpmissions" / mission.name
        mission_in_tree.mkdir(parents=True)
        rewritten = [
            f"-mission=mpmissions/{mission.name}" if item.casefold().startswith("-mission=") else item
            for item in rewritten
        ]
        if not any(item.casefold() == "-dologs" for item in rewritten):
            rewritten.append("-dologs")

        keys = tree / "keys"
        keys.mkdir()
        for source in key_sources:
            if source.is_dir():
                for key in source.glob("*.bikey"):
                    shutil.copyfile(key, keys / key.name)
    except BaseException:
        shutil.rmtree(tree, ignore_errors=True)
        raise

    command = [
        "run", "-d", "--rm", "--name", name, "--network", "host",
        "--label", f"{LABEL}=1", "--label", f"{LABEL}.tree={tree}", "--label", f"{LABEL}.profiles={profiles}",
        "--label", f"{LABEL}.dropped-filepatching={int(dropped_file_patching)}",
        "-v", f"{tree}:{tree}", "-v", f"{server_dir}:{server_dir}:O",
        "-v", f"{config}:{config}:ro", "-v", f"{profiles}:{profiles}", "-v", f"{mission}:{mission_in_tree}",
        *mounts, "-w", str(tree),
        f"{IMAGE_NAME}:{IMAGE_TAG}", "/bin/sh", "-c",
        'ulimit -c 0 && exec "$@" < ./stdin > ./server.out 2>&1', "sh", f"./{SERVER_BINARY}",
        *rewritten,
    ]
    started = _podman(podman, *command, check=False)
    if started.returncode != 0:
        shutil.rmtree(tree, ignore_errors=True)
        raise PodmanServerError("container_start_failed: " + started.stderr[-2000:])
    try:
        info = status(name, podman)
        pid = info.get("host_pid")
        container_id = str(info.get("container_id") or "")
        if not isinstance(pid, int) or pid <= 0 or not container_id:
            raise PodmanServerError("container_pid_unavailable")
        if info.get("image_id") != image_id:
            raise PodmanServerError("container_image_mismatch")
        _wait_for_server_exec(pid, SERVER_BINARY, time.monotonic() + EXEC_WAIT_S)
        if not _cgroup_names_container(pid, container_id):
            raise PodmanServerError("container_pid_not_in_container")
    except BaseException:
        _podman(podman, "rm", "-f", "-t", "0", name, check=False)
        raise
    (tree / "container.json").write_text(
        json.dumps({"name": name, "container_id": container_id, "image_id": image_id, "host_pid": pid}) + "\n",
        encoding="utf-8",
    )
    return LaunchedContainer(pid=pid, name=name, container_id=container_id, image_id=image_id, tree=str(tree))


# ---------------------------------------------------------------- CLI


def _parse_mod(value: str, server: bool) -> Mod:
    name, sep, path = value.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError("mods are @Name=/path/to/mod")
    return Mod(name=name, path=Path(path), server=server)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dayz_mcp.podman_server")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("start", help="build a tree and start a server container")
    s.add_argument("--server-dir", required=True, type=Path, help="the Steam DayZServer install (app 223350)")
    s.add_argument("--tree", required=True, type=Path, help="private per-run directory (outside the install)")
    s.add_argument("--mission", default="dayzOffline.chernarusplus")
    s.add_argument("--mod", action="append", default=[], help="@Name=/path (client and server)")
    s.add_argument("--servermod", action="append", default=[], help="@Name=/path (server only)")
    s.add_argument("--bridge-port", type=int, help="write dayz_mcp.json for the loopback on this port")
    s.add_argument("--bridge-keyfile", type=Path, help="the daemon's keyfile (with --bridge-port)")
    s.add_argument("--bridge-instance", default="", help="instance token (UUID4) for a bound run")
    s.add_argument("--wait", type=float, default=0, help="seconds to wait for the mission to load")
    s.add_argument("--podman", default="podman")
    s.add_argument("extra", nargs="*", help="extra DayZServer arguments, after --")
    for name in ("status", "stop"):
        p = sub.add_parser(name)
        p.add_argument("name")
        p.add_argument("--podman", default="podman")
    i = sub.add_parser("image", help="build (or show) the runtime image")
    i.add_argument("--rebuild", action="store_true")
    i.add_argument("--podman", default="podman")
    args = parser.parse_args(argv)

    try:
        if args.command == "image":
            out: dict = {"image": f"{IMAGE_NAME}:{IMAGE_TAG}", "image_id": ensure_image(args.podman, args.rebuild)}
        elif args.command == "status":
            out = status(args.name, args.podman)
        elif args.command == "stop":
            out = stop(args.name, args.podman)
        else:
            mods = [_parse_mod(m, False) for m in args.mod] + [_parse_mod(m, True) for m in args.servermod]
            spec = RunSpec(server_dir=args.server_dir, tree=args.tree, mission=args.mission, mods=mods,
                           extra_args=args.extra, podman=args.podman)
            if args.bridge_port:
                if not args.bridge_keyfile:
                    parser.error("--bridge-port needs --bridge-keyfile")
                key = args.bridge_keyfile.read_text(encoding="utf-8").strip()
                spec.tree.mkdir(parents=True, exist_ok=True)
                bridge_config(spec.tree, args.bridge_port, key, args.bridge_instance)
            out = start(spec)
            if args.wait:
                out["mission_loaded"] = wait_mission(Path(out["profiles"]), args.wait, out["name"], args.podman)
    except PodmanServerError as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    print(json.dumps({"ok": True, **out}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
