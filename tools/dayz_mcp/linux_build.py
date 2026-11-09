"""``dayz_test_run build=true`` on Linux: armake2 in place of AddonBuilder.

On Windows the sealed worker sends an ``ADDON_BUILDER`` frame and the signed launcher
runs the sealed ``AddonBuilder.exe``. On Linux there is no DayZ Tools install to seal,
so the relay runs armake2 (https://github.com/bzed/armake2) from ``PATH`` with an argv
built only from the validated frame:

- ``pack_only``: ``armake2 pack`` (AddonBuilder ``-packonly``);
- otherwise ``armake2 build --proton-binarize``, which brings its own ``P:`` sandbox, so
  the worker's stage of junctions (``_staged_build_source``) is not used;
- ``source`` without ``$PBOPREFIX$``: the prefix goes in as a header extension;
- the PBO is written next to its target and renamed into place; the response is the
  launcher's ``exit_code``, ``ok`` and ``pbo_size``.

``install_worker`` replaces the Win32 parts of the sealed worker's build path and the
``msvcrt`` shared build lock in the worker host. The sealed files stay byte-identical.
"""

from __future__ import annotations

import contextlib
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

BUILD_DEADLINE_S = 3600.0
MARKER = "$PBOPREFIX$"
_ERROR_TAIL_BYTES = 8192


def _response(ok: bool, size: int = 0) -> bytes:
    return json.dumps(
        {"exit_code": 0 if ok else 1, "ok": ok, "pbo_size": size if ok else 0},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def build_argv(armake2: str, payload: dict[str, object], output: str) -> list[str]:
    source = str(payload["source"])
    argv = [armake2, "pack" if payload["pack_only"] else "build", "-f"]
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
    armake2 = shutil.which("armake2")
    if armake2 is None:
        if output_sink is not None:
            output_sink("stderr", b"armake2 not found on PATH\n")
        return _response(False)
    target = Path(str(payload["target"]))
    temp = Path(str(payload["temp"]))
    if payload["clear"]:
        shutil.rmtree(temp, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    pbo = target / f"{payload['prefix']}.pbo"
    # In the target's directory: the rename below must stay on one filesystem.
    pending = target / f".{payload['prefix']}.pbo.{os.getpid()}.building"
    log = temp / "armake2.log"
    try:
        with open(log, "wb") as stream:
            child = subprocess.Popen(
                build_argv(armake2, payload, str(pending)),
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                cwd=str(temp),
                close_fds=True,
                # Its own process group: a binarizing build runs Proton under armake2,
                # and a cancel must end those too (the job object does on Windows).
                start_new_session=True,
            )
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
        yield source  # armake2 --proton-binarize sets up its own P: sandbox

    server_cli.async_shared_build_lock = async_shared_build_lock
    worker._source_directory_accessible = source_directory_accessible
    worker._read_pbo_namespace = read_pbo_namespace
    worker._source_leaf = lambda source: os.path.basename(source.rstrip("/")) or None
    # A keyword default, bound when the sealed function was defined.
    worker.execute_dayz_test_worker.__kwdefaults__["stage_build_source"] = staged_build_source

