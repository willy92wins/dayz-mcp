"""dayz-test-v1 on Linux: the sealed worker without the native PE.

On Windows ``dayz_test_run`` hands its accredited request to the approved native
launcher (``dayz-test-launcher.exe``): a signed PE that pins its bundle, starts the
sealed worker (``runtime\\python.exe app.pyz``) under a debugger and a job object,
and relays the worker's broker frames, starting one ``--lifecycle-child`` per
lifecycle frame. Only that child receives the lease token, the client identity and
the daemon policy; the worker never sees them.

There is no PE outside Windows. This module keeps everything else:

- the bundle is the same ``app.pyz`` (``build_native_launcher._build_app_pyz``, every
  member byte-identical to ``tools/packaged-modules.lock.json``) and the same
  ``request-policy.json`` / ``worker-runtime.json`` the builder emits from the
  launcher policy, written by ``install`` to
  ``$LOCALAPPDATA/DayZ_MCP/linux-launchers/dayz-test-v1/`` and pinned by
  ``bundle-manifest.json``; ``load_verified_bundle`` checks all of it again on every
  open, and the lock against the sealed sources in the working tree;
- ``launch_registered_linux`` is the PE's relay loop in Python: the worker and every
  lifecycle child run under the approved interpreter with ``-I``, through
  ``linux_worker_host``, which imports the sealed modules from the verified archive
  bytes; secrets go only to the lifecycle child's environment, and the cancel
  handle is a pipe the worker reads.

What the PE does and this does not: the debugger gate that approves every image a
child maps, and the job object that kills the tree with the launcher. A same-user
process can still ptrace these children; on Windows that user could debug the PE.

Command line: ``python -m dayz_mcp.linux_launcher install [--policy FILE]|status``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import select
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

LAUNCHER_ID = "dayz-test-v1"
MANIFEST_NAME = "bundle-manifest.json"
BUNDLE_FILES = ("app.pyz", "request-policy.json", "worker-runtime.json")
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_MESSAGE = 65_536
_TERMINAL_MAGIC = b"DZW1"
_BROKER_FAILURE = b'{"error":"broker_child_failed","ok":false}'
_TOOLS_DIR = Path(__file__).resolve().parent.parent
_LOCK_PATH = _TOOLS_DIR / "packaged-modules.lock.json"
_CANCEL_GRACE_S = 20.0
_EXIT_WAIT_S = 5.0
_LIFECYCLE_DEADLINE_S = {"start": 240.0}
_LIFECYCLE_DEFAULT_DEADLINE_S = 30.0
_CHILD_ENV_KEYS = ("HOME", "PATH", "LANG", "LC_ALL", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "LOCALAPPDATA")


class LinuxLauncherError(RuntimeError):
    pass


def _invalid(reason: str = "invalid_linux_launcher_bundle") -> None:
    raise ValueError(reason)


def launchers_root() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        raise ValueError("local_cache_unavailable")
    return Path(local) / "DayZ_MCP" / "linux-launchers"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _read_pinned(path: Path, maximum: int = _MAX_FILE_BYTES) -> bytes:
    """A regular, single-link file owned by this user that only this user can write."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        _invalid()
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o022
            or info.st_size > maximum
        ):
            _invalid()
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            raw = stream.read(maximum + 1)
    finally:
        os.close(descriptor)
    if len(raw) > maximum:
        _invalid()
    return raw


def _private_directory(path: Path) -> None:
    try:
        info = os.lstat(path)
    except OSError:
        _invalid()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        _invalid()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _closed_json(raw: bytes) -> dict[str, object]:
    def closed(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                _invalid()
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=closed)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _invalid()
    if type(value) is not dict:
        _invalid()
    return value


# ---------------------------------------------------------------- sealed sources


def sealed_sources() -> dict[str, bytes]:
    """The members app.pyz must hold, each checked against packaged-modules.lock.json."""
    lock = _closed_json(_LOCK_PATH.read_bytes())
    files = lock.get("files")
    if type(files) is not dict:
        _invalid("packaged_modules_lock_invalid")
    members: dict[str, bytes] = {}
    for relative, expected in files.items():
        if not relative.startswith("tools/"):
            _invalid("packaged_modules_lock_invalid")
        raw = (_TOOLS_DIR.parent / relative).read_bytes()
        if _sha256(raw) != str(expected).upper():
            raise ValueError(f"packaged_module_drift:{relative}")
        if relative.startswith("tools/dayz_mcp/"):
            members["dayz_mcp/" + relative[len("tools/dayz_mcp/") :]] = raw
        elif relative.endswith("/src/app_main.py"):
            members["__main__.py"] = raw
    return members


def _verify_app(raw: bytes, expected: dict[str, bytes]) -> None:
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if names != sorted(names) or len(names) != len(set(names)):
                _invalid()
            # Every archive member is sealed; the lock may seal more (launcher.cpp).
            for name in names:
                if name not in expected or archive.read(name) != expected[name]:
                    _invalid()
            if "__main__.py" not in names or not any(n.startswith("dayz_mcp/") for n in names):
                _invalid()
    except (OSError, KeyError, RuntimeError, zipfile.BadZipFile):
        _invalid()


# ---------------------------------------------------------------- install


def _load_policy_source(path: Path) -> dict[str, object]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise ValueError("policy_source_missing") from None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("policy_source_not_regular")
    if info.st_size > 65_536:
        raise ValueError("policy_source_schema")
    return _closed_json(path.read_bytes())


def _sealed_root_posix(path: str, *, allow_root_junction: bool) -> dict[str, object]:
    """``build_native_launcher._sealed_root`` off Windows.

    The same sealed-root document, captured by request_path_authority's POSIX
    walk: the walk the tool repeats on every request, so a root sealed here is
    one it can open again (a symbolic link is the junction counterpart).
    """
    from dayz_mcp.request_path_authority import _capture_root_for_test

    try:
        root = _capture_root_for_test(path, allow_root_junction=allow_root_junction)
    except ValueError:
        raise ValueError(
            f"required_junction_missing:{path}" if allow_root_junction else f"unexpected_root_reparse:{path}"
        ) from None
    return {
        "allow_root_junction": root.allow_root_junction,
        "handle_path": root.handle_path,
        "identity": {
            "file_id": root.identity.file_id,
            "volume_serial_number": root.identity.volume_serial_number,
        },
        "path": root.path,
        "resolved_identity": {
            "file_id": root.resolved_identity.file_id,
            "volume_serial_number": root.resolved_identity.volume_serial_number,
        },
        "resolved_path": root.resolved_path,
        "root_reparse_tag": root.root_reparse_tag,
    }


def _import_builder():
    """``build_native_launcher``, unchanged, for its policy emitters.

    The builder's bytes are part of every Windows launcher's build contract
    (``builder_sha256``), so it is not edited for Linux. It binds kernel32 with
    ``ctypes.WinDLL`` at import, which does not exist here: a stand-in whose
    calls raise is present only for that import. Its ``_sealed_root`` (Win32
    file identities) is then pointed at the POSIX capture.
    """
    import ctypes

    from dayz_mcp import linux_compat

    linux_compat.install()
    if str(_TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(_TOOLS_DIR))
    added = not hasattr(ctypes, "WinDLL")
    if added:
        ctypes.WinDLL = lambda name, use_last_error=False: linux_compat.UnavailableLibrary(name)
    try:
        import build_native_launcher as builder
    finally:
        if added:
            del ctypes.WinDLL
    builder._sealed_root = _sealed_root_posix
    return builder


def install(policy_path: Path | str | None = None, launcher_id: str = LAUNCHER_ID) -> dict[str, object]:
    """Build the bundle from the launcher policy and replace the installed one."""
    builder = _import_builder()
    source_path = builder.resolve_launcher_policy_path(policy_path)
    source = _load_policy_source(source_path)
    builder.validate_launcher_policy_source(source)
    policy_bytes, runtime_bytes = builder._emit_policy_documents(source)
    expected = sealed_sources()

    root = launchers_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _private_directory(root)
    staging = root / f".{launcher_id}.staging-{secrets.token_hex(6)}"
    staging.mkdir(mode=0o700)
    try:
        builder._build_app_pyz(staging / "app.pyz")
        (staging / "request-policy.json").write_bytes(policy_bytes)
        (staging / "worker-runtime.json").write_bytes(runtime_bytes)
        files: dict[str, dict[str, object]] = {}
        for name in BUNDLE_FILES:
            os.chmod(staging / name, 0o600)
            raw = (staging / name).read_bytes()
            files[name] = {"sha256": _sha256(raw), "size": len(raw)}
        _verify_app((staging / "app.pyz").read_bytes(), expected)
        manifest = {
            "files": files,
            "format_version": 1,
            "launcher_id": launcher_id,
            "packaged_modules_lock_sha256": _sha256(_LOCK_PATH.read_bytes()),
        }
        (staging / MANIFEST_NAME).write_bytes(_canonical(manifest))
        os.chmod(staging / MANIFEST_NAME, 0o600)
        final = root / launcher_id
        retired: Path | None = None
        if final.exists() or final.is_symlink():
            retired = root / f".{launcher_id}.retired-{secrets.token_hex(6)}"
            os.rename(final, retired)
        os.rename(staging, final)
        if retired is not None:
            shutil.rmtree(retired, ignore_errors=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "launcher_id": launcher_id,
        "root": str(final),
        "policy_source": str(source_path),
        "manifest_sha256": _sha256(_canonical(manifest)),
        "projects": [project["mod"] for project in source["projects"]],
    }


# ---------------------------------------------------------------- open / verify


@dataclass
class LinuxOpenedLauncher:
    """What ``launcher_registry`` opens on Linux: the installed bundle directory."""

    launcher_id: str
    root: Path

    def validate_native_pe(self) -> None:
        """No PE here; the bundle directory must still be private to this user."""
        _private_directory(self.root)

    def revalidate(self) -> None:
        self.validate_native_pe()

    def close(self) -> None:
        pass

    def __enter__(self) -> "LinuxOpenedLauncher":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def open_launcher(launcher_id: str = LAUNCHER_ID) -> LinuxOpenedLauncher:
    if launcher_id != LAUNCHER_ID:
        raise ValueError("unknown_launcher")
    root = launchers_root() / launcher_id
    if not (root / MANIFEST_NAME).exists():
        raise ValueError("linux_launcher_not_installed")
    opened = LinuxOpenedLauncher(launcher_id, root)
    opened.validate_native_pe()
    return opened


@dataclass
class VerifiedLinuxBundle:
    sealed_policies: tuple
    manifest_sha256: str
    app_sha256: str
    root: Path
    worker_runtime_document: object = None
    debug_image_authority: object = None
    _closed: bool = field(default=False, repr=False)

    def validated_worker_runtime(self, mod: str, dev_root: str):
        from dayz_mcp.dayz_test_worker import worker_runtime_from_document

        try:
            return worker_runtime_from_document(self.worker_runtime_document, mod, dev_root)
        except ValueError:
            _invalid()

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> "VerifiedLinuxBundle":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def load_verified_bundle(opened: object) -> VerifiedLinuxBundle:
    if type(opened) is not LinuxOpenedLauncher:
        _invalid()
    opened.revalidate()
    root = opened.root
    manifest_raw = _read_pinned(root / MANIFEST_NAME, 65_536)
    manifest = _closed_json(manifest_raw)
    files = manifest.get("files")
    if (
        set(manifest) != {"files", "format_version", "launcher_id", "packaged_modules_lock_sha256"}
        or manifest.get("format_version") != 1
        or manifest.get("launcher_id") != opened.launcher_id
        or type(files) is not dict
        or set(files) != set(BUNDLE_FILES)
    ):
        _invalid()
    if manifest["packaged_modules_lock_sha256"] != _sha256(_LOCK_PATH.read_bytes()):
        _invalid("invalid_linux_launcher_bundle__stale")
    live = {entry.name for entry in os.scandir(root)}
    if live != {*BUNDLE_FILES, MANIFEST_NAME}:
        _invalid()
    contents: dict[str, bytes] = {}
    for name in BUNDLE_FILES:
        raw = _read_pinned(root / name)
        entry = files[name]
        if type(entry) is not dict or entry.get("sha256") != _sha256(raw) or entry.get("size") != len(raw):
            _invalid()
        contents[name] = raw
    _verify_app(contents["app.pyz"], sealed_sources())

    from dayz_mcp import native_bundle

    policy = _closed_json(contents["request-policy.json"])
    sealed_policies = native_bundle._parse_policy(policy)
    runtime = _closed_json(contents["worker-runtime.json"])
    from dayz_mcp import linux_compat

    # secure_launcher imports the backend by name once the lease is granted.
    linux_compat.install_linux_launcher_backend()
    return VerifiedLinuxBundle(
        sealed_policies=sealed_policies,
        manifest_sha256=_sha256(manifest_raw),
        app_sha256=files["app.pyz"]["sha256"],
        root=root,
        worker_runtime_document=runtime,
    )


# ---------------------------------------------------------------- relay


def _child_environment(extra: dict[str, str]) -> dict[str, str]:
    env = {key: os.environ[key] for key in _CHILD_ENV_KEYS if os.environ.get(key)}
    env.update(extra)
    return env


def _host_argv(bundle: VerifiedLinuxBundle, *, lifecycle: bool) -> list[str]:
    argv = [
        sys.executable,
        "-I",
        "-m",
        "dayz_mcp.linux_worker_host",
        "--bundle",
        str(bundle.root),
        "--app-sha256",
        bundle.app_sha256,
    ]
    if lifecycle:
        argv.append("--lifecycle-child")
    return argv


def _read_exact(descriptor: int, size: int, *, deadline: float | None, stop: Callable[[], bool]) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        if stop():
            raise LinuxLauncherError("relay_stopped")
        timeout = 0.1
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise LinuxLauncherError("relay_timeout")
            timeout = min(timeout, left)
        ready, _, _ = select.select([descriptor], [], [], timeout)
        if not ready:
            continue
        chunk = os.read(descriptor, remaining)
        if not chunk:
            raise LinuxLauncherError("relay_closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]


def _lifecycle_command(frame: bytes) -> str | None:
    from dayz_mcp import native_broker_protocol

    request = native_broker_protocol.decode_request(frame)
    if request.kind is not native_broker_protocol.BrokerKind.LIFECYCLE_CLI:
        return None
    command = request.payload.get("command")
    return command if isinstance(command, str) else None


def _run_lifecycle_child(
    bundle: VerifiedLinuxBundle,
    frame: bytes,
    command: str,
    secrets_env: dict[str, str],
) -> bytes:
    deadline = time.monotonic() + _LIFECYCLE_DEADLINE_S.get(command, _LIFECYCLE_DEFAULT_DEADLINE_S)
    child = subprocess.Popen(
        _host_argv(bundle, lifecycle=True),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_child_environment(secrets_env),
        close_fds=True,
        cwd="/",
    )
    try:
        assert child.stdin is not None and child.stdout is not None
        child.stdin.write(frame)
        child.stdin.close()
        output = bytearray()
        descriptor = child.stdout.fileno()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise LinuxLauncherError("lifecycle_child_timeout")
            ready, _, _ = select.select([descriptor], [], [], min(left, 0.5))
            if not ready:
                continue
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > _MAX_MESSAGE:
                raise LinuxLauncherError("lifecycle_child_output_too_large")
        child.wait(timeout=max(deadline - time.monotonic(), 0.1))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    raw = bytes(output)
    # The PE's ResponseLooksClosed: one JSON object, nothing else.
    value = json.loads(raw.decode("utf-8"))
    if type(value) is not dict or _canonical(value) != raw:
        raise LinuxLauncherError("lifecycle_response_invalid")
    return raw


def _supervise_worker(
    bundle: VerifiedLinuxBundle,
    *,
    canonical_request: bytes,
    secrets_env: dict[str, str],
    cancel_signal: threading.Event,
    output_sink: Callable[[str, bytes], None] | None,
) -> int:
    cancel_read, cancel_write = os.pipe()
    worker: subprocess.Popen[bytes] | None = None
    stderr_thread: threading.Thread | None = None
    try:
        worker = subprocess.Popen(
            _host_argv(bundle, lifecycle=False),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_child_environment({"DAYZ_MCP_CANCEL_HANDLE": str(cancel_read)}),
            pass_fds=(cancel_read,),
            close_fds=True,
            cwd="/",
        )
        os.close(cancel_read)
        cancel_read = -1
        assert worker.stdin is not None and worker.stdout is not None and worker.stderr is not None
        stderr_stream = worker.stderr

        def drain_stderr() -> None:
            kept = 0
            for chunk in iter(lambda: stderr_stream.read1(4096), b""):
                if output_sink is not None and kept < 65_536:
                    output_sink("stderr", chunk[: 65_536 - kept])
                    kept += len(chunk)

        stderr_thread = threading.Thread(target=drain_stderr, name="dayz-linux-worker-stderr", daemon=True)
        stderr_thread.start()

        to_worker = worker.stdin.fileno()
        from_worker = worker.stdout.fileno()
        _write_all(to_worker, canonical_request)
        cancel_deadline: float | None = None
        terminal: bytes | None = None

        def note_cancel() -> bool:
            nonlocal cancel_deadline, cancel_write
            if cancel_signal.is_set() and cancel_deadline is None:
                cancel_deadline = time.monotonic() + _CANCEL_GRACE_S
                os.close(cancel_write)
                cancel_write = -1
            return False

        while True:
            note_cancel()
            try:
                size = struct.unpack(
                    "<I",
                    _read_exact(from_worker, 4, deadline=cancel_deadline, stop=note_cancel),
                )[0]
                if not 4 <= size <= _MAX_MESSAGE:
                    break
                message = _read_exact(from_worker, size, deadline=cancel_deadline, stop=note_cancel)
            except LinuxLauncherError:
                break
            if message.startswith(_TERMINAL_MAGIC):
                terminal = message[len(_TERMINAL_MAGIC) :]
                break
            response = _BROKER_FAILURE
            try:
                command = _lifecycle_command(message)
                # After a cancel only the cleanup stop still reaches the daemon.
                if command is not None and (cancel_deadline is None or command == "stop"):
                    response = _run_lifecycle_child(bundle, message, command, secrets_env)
            except Exception:
                response = _BROKER_FAILURE
            try:
                _write_all(to_worker, struct.pack("<I", len(response)) + response)
            except OSError:
                break
        try:
            worker.stdin.close()
        except OSError:
            pass
        try:
            code = worker.wait(timeout=_EXIT_WAIT_S)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()
            terminal = None
            code = 1
        if terminal is None or not terminal:
            return code if code else 1
        if output_sink is not None:
            output_sink("stdout", terminal)
        return code
    finally:
        if worker is not None and worker.poll() is None:
            worker.kill()
            worker.wait()
        if stderr_thread is not None:
            stderr_thread.join(timeout=2.0)
        for descriptor in (cancel_read, cancel_write):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


async def launch_registered_linux(
    opened_launcher: object,
    *,
    verified_bundle: object,
    identity_json: str,
    lease_token: str,
    daemon_policy_json: str,
    canonical_request: bytes,
    cancel_event: asyncio.Event,
    output_sink: Callable[[str, bytes], None] | None = None,
) -> int:
    """``native_launcher_backend.launch_registered_native`` for the Linux bundle."""
    if (
        type(opened_launcher) is not LinuxOpenedLauncher
        or type(verified_bundle) is not VerifiedLinuxBundle
        or verified_bundle._closed
        or not isinstance(cancel_event, asyncio.Event)
        or not isinstance(canonical_request, bytes)
        or len(canonical_request) > _MAX_MESSAGE
        or not isinstance(lease_token, str)
        or not lease_token
        or not isinstance(identity_json, str)
        or not isinstance(daemon_policy_json, str)
        or (output_sink is not None and not callable(output_sink))
        or lease_token.encode("utf-8") in canonical_request
        or identity_json.encode("utf-8") in canonical_request
    ):
        raise LinuxLauncherError("invalid_linux_launcher_runtime")
    opened_launcher.revalidate()
    secrets_env = {
        "DAYZ_MCP_CLIENT_ID_JSON": identity_json,
        "DAYZ_MCP_LEASE_TOKEN": lease_token,
        "DAYZ_MCP_NORMAL_POLICY_JSON": daemon_policy_json,
    }
    cancel_signal = threading.Event()
    loop = asyncio.get_running_loop()
    task = loop.run_in_executor(
        None,
        lambda: _supervise_worker(
            verified_bundle,
            canonical_request=canonical_request,
            secrets_env=secrets_env,
            cancel_signal=cancel_signal,
            output_sink=output_sink,
        ),
    )
    watcher = asyncio.ensure_future(cancel_event.wait())
    delayed_cancel: asyncio.CancelledError | None = None
    try:
        try:
            done, _ = await asyncio.wait((task, watcher), return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                cancel_signal.set()
        except asyncio.CancelledError as error:
            delayed_cancel = error
            cancel_signal.set()
        # As launch_registered_native: a cancelled caller still waits for the
        # worker's cleanup (its stop of the run it minted), then sees the cancel.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                delayed_cancel = error
                cancel_signal.set()
        value = task.result()
    finally:
        watcher.cancel()
    if delayed_cancel is not None:
        raise delayed_cancel
    return value


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m dayz_mcp.linux_launcher")
    sub = parser.add_subparsers(dest="command", required=True)
    install_parser = sub.add_parser("install", help="build and install the Linux dayz-test-v1 bundle")
    install_parser.add_argument("--policy", type=Path, default=None)
    sub.add_parser("status", help="verify the installed bundle")
    args = parser.parse_args(argv)
    from dayz_mcp import linux_compat

    linux_compat.install()
    try:
        if args.command == "install":
            result: dict[str, object] = {"ok": True, **install(args.policy)}
        else:
            with open_launcher() as opened, load_verified_bundle(opened) as bundle:
                result = {
                    "ok": True,
                    "root": str(bundle.root),
                    "manifest_sha256": bundle.manifest_sha256,
                    "projects": [item.policy.mod for item in bundle.sealed_policies],
                }
    except (OSError, ValueError) as error:
        result = {"ok": False, "error": str(error)}
    print(json.dumps(result, indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
