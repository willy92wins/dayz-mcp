"""``dayz_test_run build=true`` on Linux: dayzmake in place of AddonBuilder.

On Windows the sealed worker sends an ``ADDON_BUILDER`` frame and the signed launcher
runs the sealed ``AddonBuilder.exe``. On Linux there is no DayZ Tools install to seal,
so the relay runs dayzmake (https://github.com/bzed/dayzmake) from ``PATH`` with an argv
built only from the validated frame:

- ``pack_only``: ``dayzmake pack`` (AddonBuilder ``-packonly``);
- otherwise ``dayzmake build --proton-binarize``, which brings its own ``P:`` sandbox, so
  the worker's stage of junctions (``_staged_build_source``) is not used;
- ``source`` without ``$PBOPREFIX$``: the prefix goes in as a header extension;
- the PBO is written next to its target and renamed into place; the response is the
  launcher's ``exit_code``, ``ok`` and ``pbo_size``.

The executable is pinned, unlike a bare ``PATH`` lookup: only the v1.0.0 release asset
runs, matched by ``DAYZMAKE_SHA256`` (the release record lives in
``tools/packaged-modules.linux.lock.json`` and a test keeps the two in step). The
``PATH`` result is resolved to an absolute real path, opened without following
a final symlink, and copied into a sealed ``memfd`` while it is hashed; the
child executes ``/proc/self/fd/<fd>`` for that sealed image, so the bytes that
run are the bytes that were hashed. Anything else is refused as
``dayzmake_pin_mismatch`` before it starts, unless
``DAYZ_MCP_DAYZMAKE_SHA256`` names the digest of a development build, and a
host without a usable sealed memfd refuses the build instead of falling back
to the ``PATH`` file. The child gets ``build_environment()``, the daemon's
block limited to the names dayzmake and Proton read, instead of inheriting
all of it.

``install_worker`` replaces the Win32 parts of the sealed worker's build path and the
``msvcrt`` shared build lock in the worker host. The sealed files stay byte-identical.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

# The Windows launcher's ADDON_BUILDER deadline (launcher.cpp: 2 h); a binarizing
# build under Proton is the slow case on both.
BUILD_DEADLINE_S = 7200.0
MARKER = "$PBOPREFIX$"
_ERROR_TAIL_BYTES = 8192

# The build tool is not sealed here, so it is pinned by content instead: the
# executable inside dayzmake-v1.0.0-debian-trixie-x86_64.tar.gz. The release
# record lives in packaged-modules.linux.lock.json (dayzmake_linux) and
# test_linux_build checks that the two agree.
DAYZMAKE_VERSION = "v1.0.0"
DAYZMAKE_SHA256 = "6626bf1d4ea95c43b607c4b9550dab22549777adbbe58032637e8717bd175d38"
PIN_OVERRIDE_ENV = "DAYZ_MCP_DAYZMAKE_SHA256"

# The daemon inherits the agent session; a build child does not need that block.
# dayzmake reads PROTON, DAYZ_TOOLS, STEAM_ROOT, BIOUTPUT and
# STEAM_COMPAT_CLIENT_INSTALL_PATH for the Proton binarize, and Wine needs HOME,
# a locale and the XDG directories to find Steam and its prefix.
_BUILD_ENV_KEYS = (
    "HOME",
    "PATH",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "XDG_RUNTIME_DIR",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "PROTON",
    "STEAM_ROOT",
    "DAYZ_TOOLS",
    "BIOUTPUT",
    "STEAM_COMPAT_CLIENT_INSTALL_PATH",
)


def build_environment() -> dict[str, str]:
    """The daemon's environment limited to what dayzmake and Proton read."""
    return {key: os.environ[key] for key in _BUILD_ENV_KEYS if os.environ.get(key)}


def expected_sha256() -> str:
    """The digest a build tool must have: the release's, or a pinned dev build's."""
    override = os.environ.get(PIN_OVERRIDE_ENV, "").strip().lower()
    return override or DAYZMAKE_SHA256


def _sealed_dayzmake() -> tuple[int | None, str | None, str]:
    """A sealed memfd holding the pinned dayzmake, its ``/proc`` path, or a refusal.

    Returns ``(descriptor, argv0, "")`` on success; the caller owns the
    descriptor and closes it once the child has started.
    """
    found = shutil.which("dayzmake")
    if found is None:
        return None, None, f"dayzmake not found on PATH (install the {DAYZMAKE_VERSION} release)\n"
    absolute = os.path.realpath(os.path.abspath(found))
    if not hasattr(os, "memfd_create"):
        return None, None, "dayzmake_sealed_image_unavailable: memfd_create is unavailable\n"
    source_fd: int | None = None
    sealed_fd: int | None = None
    try:
        source_fd = os.open(absolute, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(source_fd)
        if not stat.S_ISREG(info.st_mode):
            return None, None, f"dayzmake at {absolute} is not a regular file\n"
        flags = getattr(os, "MFD_CLOEXEC", 0) | getattr(os, "MFD_ALLOW_SEALING", 0)
        sealed_fd = os.memfd_create("dayzmake", flags)
        digest = hashlib.sha256()
        while True:
            block = os.read(source_fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            view = memoryview(block)
            while view:
                written = os.write(sealed_fd, view)
                view = view[written:]
        actual = digest.hexdigest()
        expected = expected_sha256()
        if actual != expected:
            os.close(sealed_fd)
            sealed_fd = None
            return None, None, (
                f"dayzmake_pin_mismatch: {absolute} is sha256 {actual}, expected {expected} "
                f"({DAYZMAKE_VERSION}); set {PIN_OVERRIDE_ENV} to pin a development build\n"
            )
        import fcntl

        seals = (
            getattr(fcntl, "F_SEAL_SHRINK", 2)
            | getattr(fcntl, "F_SEAL_GROW", 4)
            | getattr(fcntl, "F_SEAL_WRITE", 8)
            | getattr(fcntl, "F_SEAL_SEAL", 1)
        )
        fcntl.fcntl(sealed_fd, getattr(fcntl, "F_ADD_SEALS", 1033), seals)
    except OSError as error:
        if sealed_fd is not None:
            os.close(sealed_fd)
        return None, None, f"dayzmake_sealed_image_unavailable: {absolute}: {error}\n"
    finally:
        if source_fd is not None:
            os.close(source_fd)
    return sealed_fd, f"/proc/self/fd/{sealed_fd}", ""


def _response(ok: bool, size: int = 0) -> bytes:
    return json.dumps(
        {"exit_code": 0 if ok else 1, "ok": ok, "pbo_size": size if ok else 0},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def build_argv(dayzmake: str, payload: dict[str, object], output: str) -> list[str]:
    source = str(payload["source"])
    argv = [dayzmake, "pack" if payload["pack_only"] else "build", "-f"]
    if not payload["pack_only"]:
        argv.append("--proton-binarize")
    if not os.path.exists(os.path.join(source, MARKER)):
        argv += ["-e", f"prefix={payload['prefix']}"]
    return [*argv, source, output]


def run_addon_builder(
    payload: dict[str, object],
    *,
    cancelled: Callable[[], bool],
    output_sink: Callable[[str, bytes], None] | None = None,
) -> bytes:
    """The relay's answer to one validated ``ADDON_BUILDER`` frame."""
    if cancelled():
        return _response(False)
    sealed_fd, dayzmake, refusal = _sealed_dayzmake()
    if dayzmake is None or sealed_fd is None:
        if output_sink is not None:
            output_sink("stderr", refusal.encode("utf-8", "replace"))
        return _response(False)
    try:
        target = Path(str(payload["target"]))
        temp = Path(str(payload["temp"]))
        if payload["clear"]:
            shutil.rmtree(temp, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        temp.mkdir(parents=True, exist_ok=True)
        pbo = target / f"{payload['prefix']}.pbo"
        # In the target's directory: the rename below must stay on one filesystem.
        pending = target / f".{payload['prefix']}.pbo.{os.getpid()}.building"
        log = temp / "dayzmake.log"
        try:
            with open(log, "wb") as stream:
                try:
                    child = subprocess.Popen(
                        build_argv(dayzmake, payload, str(pending)),
                        stdin=subprocess.DEVNULL,
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        cwd=str(temp),
                        env=build_environment(),
                        close_fds=True,
                        pass_fds=(sealed_fd,),
                        # Its own process group: a binarizing build runs Proton under dayzmake,
                        # and a cancel must end those too (the job object does on Windows).
                        start_new_session=True,
                    )
                except OSError as error:
                    if output_sink is not None:
                        output_sink(
                            "stderr",
                            (
                                "dayzmake_sealed_image_unavailable: the sealed dayzmake "
                                f"image cannot be executed: {error}\n"
                            ).encode("utf-8", "replace"),
                        )
                    return _response(False)
                finally:
                    if sealed_fd >= 0:
                        os.close(sealed_fd)
                        sealed_fd = -1
                deadline = time.monotonic() + BUILD_DEADLINE_S
                while child.poll() is None:
                    if cancelled() or time.monotonic() > deadline:
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        child.wait()
                        return _response(False)
                    time.sleep(0.2)
            if child.returncode != 0 or not pending.is_file() or pending.stat().st_size == 0:
                if output_sink is not None:
                    output_sink("stderr", log.read_bytes()[-_ERROR_TAIL_BYTES:])
                return _response(False)
            os.replace(pending, pbo)
        finally:
            pending.unlink(missing_ok=True)
        # The native launcher also requires a changed file, by identity and times; the
        # rename gives a new inode, so an identical rebuild counts there and here.
        return _response(True, pbo.stat().st_size)
    finally:
        if sealed_fd >= 0:
            try:
                os.close(sealed_fd)
            except OSError:
                pass


def install_worker() -> None:
    """Linux implementations for the build path of the sealed worker and ``server_cli``."""
    from dayz_mcp import byte_lock, dayz_test_worker as worker, linux_compat, native_broker_protocol, server_cli

    drive_rule = native_broker_protocol._local_path
    if not getattr(drive_rule, "_accepts_posix", False):
        def frame_path_rule(value: object) -> bool:
            return linux_compat.valid_posix_local_path(value) or drive_rule(value)

        frame_path_rule._accepts_posix = True
        native_broker_protocol._local_path = frame_path_rule

    @asynccontextmanager
    async def async_shared_build_lock(
        *parts: str,
        root: Path | None = None,
        wait_s: float | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> AsyncIterator[None]:
        # server_cli.async_shared_build_lock with byte_lock for msvcrt.locking.
        import asyncio

        limit = server_cli._validated_wait_s(wait_s)
        paths = server_cli.shared_build_lock_paths(*parts, root=root)
        if not paths:
            if cancel is not None and cancel():
                raise server_cli.BuildLockCancelled()
            yield
            return
        paths[0].parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + limit
        held: list[int] = []
        try:
            for path in paths:
                descriptor = server_cli.open_lock_file(path)
                held.append(descriptor)
                server_cli._prepare_lock_byte(descriptor)
                while True:
                    if cancel is not None and cancel():
                        raise server_cli.BuildLockCancelled()
                    try:
                        byte_lock.try_lock(descriptor)
                        break
                    except OSError:
                        pass
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise server_cli.BuildLockBusy()
                    await asyncio.sleep(min(server_cli._BUILD_LOCK_POLL_S, remaining))
            if cancel is not None and cancel():
                raise server_cli.BuildLockCancelled()
            yield
        finally:
            while held:
                os.close(held.pop())  # an OFD lock ends with its open file

    def source_directory_accessible(source: str) -> bool:
        try:
            return stat.S_ISDIR(os.stat(source).st_mode)
        except OSError:
            return False

    def read_pbo_namespace(source: str, prefix: str, _pin: object) -> str:
        if not source_directory_accessible(source):
            raise worker._failed("build_source_unavailable")
        try:
            descriptor = os.open(os.path.join(source, MARKER), os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return prefix
        except OSError:
            raise worker._failed("build_source_unavailable") from None
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            limit = worker.win32_fileinfo.PBO_PREFIX_MAX_BYTES
            data = stream.read(limit + 1) if stat.S_ISREG(info.st_mode) else None
        namespace = None if data is None or len(data) > limit else worker._decode_pbo_namespace(data)
        if namespace is None:
            raise worker._failed("build_source_unavailable")
        return namespace

    @contextlib.contextmanager
    def staged_build_source(source: str) -> Iterator[str]:
        yield source  # dayzmake --proton-binarize sets up its own P: sandbox

    server_cli.async_shared_build_lock = async_shared_build_lock
    worker._source_directory_accessible = source_directory_accessible
    worker._read_pbo_namespace = read_pbo_namespace
    worker._source_leaf = lambda source: os.path.basename(source.rstrip("/")) or None
    # A keyword default, bound when the sealed function was defined.
    worker.execute_dayz_test_worker.__kwdefaults__["stage_build_source"] = staged_build_source

