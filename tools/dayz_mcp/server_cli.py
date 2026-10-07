from __future__ import annotations

import argparse
import hashlib
import tempfile
from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Iterator, Literal

import os
import re
import sys
import threading
import time


CANONICAL_CLIENT_PLATFORMS = ("claude", "codex", "unknown")
CLIENT_PLATFORM_ALIASES = {"grok": "unknown"}
_RESERVED_INSTANCE_TOKENS = frozenset({"default"})
_INSTANCE_TOKEN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,31})?$")
_instance_bound_lock = threading.Lock()
_instance_bound: tuple[str | None, str | None] | None = None


class InstanceSelectionError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_instance_token(value: object) -> str | None:
    """Return None for omission. ``default`` and any other malformed token fail."""
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value:
        raise InstanceSelectionError("invalid_instance_token")
    if value.casefold() in _RESERVED_INSTANCE_TOKENS or not _INSTANCE_TOKEN_RE.fullmatch(value):
        raise InstanceSelectionError("invalid_instance_token")
    if value.startswith("-") or value.endswith("-") or "--" in value:
        raise InstanceSelectionError("invalid_instance_token")
    return value


def profile_leaf_name(token: str | None) -> str:
    """Directory leaf for an explicit instance token.

    Omission is exactly ``profiles``. A token the instance validator accepts
    is exactly ``profiles-<token>``. The argument is the token; this function
    does not read the environment or the bound context.
    """
    validated = validate_instance_token(token)
    if validated is None:
        return "profiles"
    return "profiles-" + validated


def bound_instance_token() -> str | None:
    """The selector already published for this process, or None if none is.

    Does not bind from argv. Callers that need the daemon's selection read it
    here; a missing bind is the default instance, not a guess from the
    environment.
    """
    with _instance_bound_lock:
        if _instance_bound is None:
            return None
        return _instance_bound[0]


def validate_game_path(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or "\x00" in value or not os.path.isabs(value):
        raise InstanceSelectionError("invalid_game_path")
    return os.path.normpath(value)


def state_root_name(token: str | None) -> str:
    if token is None:
        return "DayZ_MCP"
    return "DayZ_MCP_" + token


def registration_name(token: str | None) -> str:
    if token is None:
        return "dayz-mcp"
    return "dayz-mcp-" + token


def _invocation_is_test() -> bool:
    """True for the fast tier and for a normal-tier ``python -m unittest`` run."""
    if os.environ.get("DAYZ_MCP_FAST_TESTS") == "1":
        return True
    for argument in sys.argv:
        normalized = argument.replace("\\", "/").casefold()
        if normalized == "unittest" or normalized.endswith("/unittest/__main__.py"):
            return True
    return False


class CliContractError(OSError):
    """Caller-facing contract failure with an identifier-shaped ``code``.

    ``dayz_test_run`` forwards ``code`` the same way it forwards a
    ``NativeLauncherBackendError`` code. The message is that code and nothing
    else, so a path cannot cross the wire.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class BuildLockBusy(OSError):
    """The shared build lock was still held when the wait elapsed."""

    def __init__(self) -> None:
        super().__init__("build_busy")


class BuildLockCancelled(Exception):
    """The caller cancelled while the shared build lock was still held."""


# A real AddonBuilder pass is measured in minutes. The wait sits above that
# and stays configurable so a test can force the timeout.
_DEFAULT_BUILD_LOCK_WAIT_S = 15 * 60
_BUILD_LOCK_POLL_S = 0.05


def build_lock_wait_s() -> float:
    """Seconds a contender polls for the shared build lock.

    ``DAYZ_MCP_BUILD_LOCK_WAIT_S`` overrides the default. A blank value keeps
    the default. Anything that is not a finite, non-negative number fails closed.
    """
    raw = os.environ.get("DAYZ_MCP_BUILD_LOCK_WAIT_S", "").strip()
    if not raw:
        return float(_DEFAULT_BUILD_LOCK_WAIT_S)
    try:
        value = float(raw)
    except ValueError as error:
        raise CliContractError("invalid_build_lock_wait") from error
    if value < 0 or value != value or value == float("inf"):
        raise CliContractError("invalid_build_lock_wait")
    return value


def open_lock_file(path: Path) -> int:
    """Open or create ``path`` without ``FILE_SHARE_DELETE``.

    The C runtime ``os.open`` shares delete. On this host a second process can
    then unlink the held file, create a new one at the same path and lock that
    new file while the first holder still believes it owns the name.
    """
    import ctypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    handle = kernel.CreateFileW(
        str(path),
        0x80000000 | 0x40000000,
        0x00000001 | 0x00000002,
        None,
        4,
        0x80,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if not handle or handle == invalid:
        raise OSError(ctypes.get_last_error(), "lock_file_open_failed")
    return msvcrt.open_osfhandle(handle, os.O_BINARY)


def shared_root() -> Path:
    """Cross-instance lock directory. Tests must not use the real profile.

    ``DAYZ_MCP_SHARED_ROOT`` overrides the location. A relative override is
    refused: it must not be resolved against the process cwd. A test process,
    including one that did not set ``DAYZ_MCP_FAST_TESTS``, refuses the real
    ``%LOCALAPPDATA%\\DayZ_MCP_shared`` and otherwise uses a per-process temp
    directory. Production still uses that profile when no override is set.
    """
    override = os.environ.get("DAYZ_MCP_SHARED_ROOT", "").strip()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    real = Path(local) / "DayZ_MCP_shared" if local else None
    isolated = _invocation_is_test()
    if override:
        root = Path(override)
        if not root.is_absolute():
            raise CliContractError("relative_shared_root")
    elif isolated:
        root = Path(tempfile.gettempdir()) / "dayz-mcp-test-shared" / str(os.getpid())
    else:
        if real is None:
            raise OSError("localappdata_unavailable")
        root = real
    if isolated and real is not None:
        try:
            same = os.path.normcase(str(root.resolve())) == os.path.normcase(
                str(real.resolve())
            )
        except OSError:
            same = os.path.normcase(str(root)) == os.path.normcase(str(real))
        if same:
            raise OSError("shared_root_uses_real_profile")
    return root


def shared_build_lock_paths(*parts: str, root: Path | None = None) -> list[Path]:
    """One lock file per normalized destructive resource, in acquire order."""
    identities = sorted(
        {os.path.normcase(os.path.normpath(part)) for part in parts if part}
    )
    base = (shared_root() if root is None else Path(root)) / "build-locks"
    return [
        base / (hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".lock")
        for identity in identities
    ]


def _validated_wait_s(wait_s: float | None) -> float:
    if wait_s is None:
        return build_lock_wait_s()
    if isinstance(wait_s, bool) or not isinstance(wait_s, (int, float)):
        raise CliContractError("invalid_build_lock_wait")
    limit = float(wait_s)
    if limit < 0 or limit != limit or limit == float("inf"):
        raise CliContractError("invalid_build_lock_wait")
    return limit


def _prepare_lock_byte(descriptor: int) -> None:
    if os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b"\0")


def _try_lock_byte(descriptor: int) -> bool:
    import msvcrt

    os.lseek(descriptor, 0, os.SEEK_SET)
    try:
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    return True


def _lock_byte(descriptor: int, deadline: float) -> None:
    _prepare_lock_byte(descriptor)
    while True:
        if _try_lock_byte(descriptor):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BuildLockBusy()
        time.sleep(min(_BUILD_LOCK_POLL_S, remaining))


def _unlock_byte(descriptor: int) -> None:
    import msvcrt

    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    os.close(descriptor)


@contextmanager
def shared_build_lock(
    *parts: str,
    root: Path | None = None,
    wait_s: float | None = None,
) -> Iterator[None]:
    """Serialize each shared build resource. Not box admission.

    Locks are acquired in sorted path order and released in reverse, so two
    builds that share only the target or only the temp directory still contend.
    ``root`` is the accredited shared directory from the sealed launch request.
    A held lock is polled with ``LK_NBLCK`` until ``wait_s`` (or
    ``build_lock_wait_s``) elapses, then ``BuildLockBusy`` is raised.
    """
    try:
        import msvcrt
    except ImportError as error:
        raise OSError("build_lock_unavailable") from error
    del msvcrt
    limit = _validated_wait_s(wait_s)
    paths = shared_build_lock_paths(*parts, root=root)
    if not paths:
        yield
        return
    directory = paths[0].parent
    directory.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + limit
    held: list[int] = []
    try:
        for path in paths:
            descriptor = open_lock_file(path)
            try:
                _lock_byte(descriptor, deadline)
            except BaseException:
                os.close(descriptor)
                raise
            held.append(descriptor)
        yield
    finally:
        while held:
            _unlock_byte(held.pop())


@asynccontextmanager
async def async_shared_build_lock(
    *parts: str,
    root: Path | None = None,
    wait_s: float | None = None,
    cancel: Callable[[], bool] | None = None,
) -> AsyncIterator[None]:
    """Same lock as ``shared_build_lock``, yielding between polls.

    ``time.sleep`` on the worker loop would starve the cancellation watcher.
    A true ``cancel`` releases every byte already taken and raises
    ``BuildLockCancelled`` before the caller dispatches work.
    """
    import asyncio

    try:
        import msvcrt
    except ImportError as error:
        raise OSError("build_lock_unavailable") from error
    del msvcrt
    limit = _validated_wait_s(wait_s)
    paths = shared_build_lock_paths(*parts, root=root)
    if not paths:
        if cancel is not None and cancel():
            raise BuildLockCancelled()
        yield
        return
    directory = paths[0].parent
    directory.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + limit
    held: list[int] = []
    try:
        for path in paths:
            if cancel is not None and cancel():
                raise BuildLockCancelled()
            descriptor = open_lock_file(path)
            try:
                _prepare_lock_byte(descriptor)
                while not _try_lock_byte(descriptor):
                    if cancel is not None and cancel():
                        raise BuildLockCancelled()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BuildLockBusy()
                    await asyncio.sleep(min(_BUILD_LOCK_POLL_S, remaining))
                    if cancel is not None and cancel():
                        raise BuildLockCancelled()
            except BaseException:
                os.close(descriptor)
                raise
            held.append(descriptor)
        if cancel is not None and cancel():
            raise BuildLockCancelled()
        yield
    finally:
        while held:
            _unlock_byte(held.pop())


def bind_instance_context(
    token: str | None,
    game_path: str | None = None,
    *,
    replace: bool = False,
) -> None:
    """Publish the selector once. A later conflicting bind fails closed."""
    global _instance_bound
    canonical = validate_instance_token(token)
    path = validate_game_path(game_path)
    with _instance_bound_lock:
        if (
            _instance_bound is not None
            and not replace
            and _instance_bound != (canonical, path)
        ):
            raise InstanceSelectionError("instance_context_conflict")
        _instance_bound = (canonical, path)


def reset_instance_context_for_tests() -> None:
    global _instance_bound
    with _instance_bound_lock:
        _instance_bound = None


def current_instance_token() -> str | None:
    _ensure_instance_bound()
    assert _instance_bound is not None
    return _instance_bound[0]


def current_game_path() -> str | None:
    _ensure_instance_bound()
    assert _instance_bound is not None
    return _instance_bound[1]


def _ensure_instance_bound() -> None:
    global _instance_bound
    with _instance_bound_lock:
        if _instance_bound is not None:
            return
    bind_instance_context(*_selector_from_argv(list(sys.argv)))


def _selector_from_argv(argv: list[str]) -> tuple[str | None, str | None]:
    """Flags only. An inherited environment value is not a selector."""
    reject_glued_selector_flags(argv)
    token: str | None = None
    game_path: str | None = None
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--instance":
            if index + 1 >= len(argv):
                raise InstanceSelectionError("invalid_instance_token")
            token = validate_instance_token(argv[index + 1])
            index += 2
            continue
        if argument == "--game-path":
            if index + 1 >= len(argv):
                raise InstanceSelectionError("invalid_game_path")
            game_path = validate_game_path(argv[index + 1])
            index += 2
            continue
        index += 1
    return token, game_path


_USE_BOUND_GAME_PATH = object()


def reject_conflicting_environment(
    token: str | None,
    port: int | None,
    game_path: object = _USE_BOUND_GAME_PATH,
) -> None:
    """A present env value that is not exactly the bound selector conflicts.

    The variable is never applied as a fallback. ``port is None`` skips the
    port variable, for an entry point that has no port of its own. Pass
    ``game_path`` when the selector was parsed from this entry's argv and
    must not be read back from the process-wide binding.
    """
    if "DAYZ_MCP_INSTANCE" in os.environ:
        expected = "" if token is None else token
        if os.environ.get("DAYZ_MCP_INSTANCE") != expected:
            raise InstanceSelectionError("instance_environment_conflict")
    if (
        port is not None
        and "DAYZ_MCP_PORT" in os.environ
        and os.environ.get("DAYZ_MCP_PORT") != str(port)
    ):
        raise InstanceSelectionError("instance_environment_conflict")
    if "DAYZ_MCP_GAME_PATH" in os.environ:
        if game_path is _USE_BOUND_GAME_PATH:
            expected_game = current_game_path() or ""
        else:
            expected_game = "" if game_path is None else str(game_path)
        if os.environ.get("DAYZ_MCP_GAME_PATH") != expected_game:
            raise InstanceSelectionError("instance_environment_conflict")


def validate_entry_selector(argv: list[str]) -> tuple[str | None, str | None]:
    """One selector check for every process entry point. No side effects.

    Glued ``--instance`` / ``--game-path`` forms, duplicates, a token outside
    the case-sensitive grammar, and a relative game path are rejected. An
    omitted token stays the default instance.
    """
    reject_glued_selector_flags(argv)
    return _selector_from_argv(list(argv))


def selector_from_parsed(instance: object, game_path: object) -> tuple[str | None, str | None]:
    """Validate the selector the parser stored, not a parallel scan of argv.

    An empty string is omission, the same as a missing flag. Callers compare
    this pair with ``validate_entry_selector`` and reject a disagreement:
    abbreviation must not keep one token and discard another.
    """
    token = None if instance is None or instance == "" else validate_instance_token(instance)
    path = None if game_path is None or game_path == "" else validate_game_path(game_path)
    return token, path


@dataclass(frozen=True)
class ServerCliParse:
    status: Literal["parsed", "terminal", "invalid"]
    namespace: argparse.Namespace | None = None


class _SilentTerminal(Exception):
    pass


class _SilentInvalid(Exception):
    pass


class _SilentArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message: str | None, file: object | None = None) -> None:
        del message, file

    def print_help(self, file: object | None = None) -> None:
        del file

    def print_usage(self, file: object | None = None) -> None:
        del file

    def error(self, message: str) -> None:
        raise _SilentInvalid(message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        del message
        if status == 0:
            raise _SilentTerminal()
        raise _SilentInvalid(f"parser_exit_{status}")


def _configure_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--keyfile", required=True)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--game-path", default=None)
    parser.add_argument("--expected-game-version")
    parser.add_argument("--require-version", action="store_true")
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=1800.0,
        help="Self-shutdown the loopback after N seconds with no MCP/game activity (0 disables).",
    )
    parser.add_argument("--enable-exec-enforce", action="store_true")
    parser.add_argument("--exec-allowlist")
    parser.add_argument("--exec-audit-path")
    parser.add_argument(
        "--client-platform",
        choices=(*CANONICAL_CLIENT_PLATFORMS, *CLIENT_PLATFORM_ALIASES),
        default="unknown",
    )
    parser.add_argument("--task-label", default="")
    parser.add_argument(
        "--tool-pack",
        choices=("full", "local8b"),
        default="full",
    )
    parser.add_argument(
        "--no-daemon-autospawn",
        action="store_false",
        dest="auto_spawn_daemon",
        help="Fail if the daemon is unavailable instead of spawning one.",
    )
    parser.add_argument(
        "--no-progressive-disclosure",
        action="store_false",
        dest="progressive_disclosure",
        help=(
            "In client mode, list every tool with its full description before a lease "
            "is held, for hosts that do not re-list after tools/list_changed (Claude "
            "Code). Lease-gated tools still refuse to run without a lease."
        ),
    )
    parser.add_argument(
        "--supervised",
        action="store_true",
        help=(
            "Own stdio and run the real server as a replaceable child process, so "
            "server_reload can serve sources edited after startup without the host "
            "reconnecting. Wraps a mode rather than being one."
        ),
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--client",
        action="store_const",
        dest="mode",
        const="client",
        help="Proxy bridge calls over HTTP to the broker daemon (multi-session); spawns it lazily.",
    )
    mode_group.add_argument(
        "--daemon",
        action="store_const",
        dest="mode",
        const="daemon",
        help="Run the standalone broker daemon that owns the loopback port.",
    )
    mode_group.add_argument(
        "--embedded",
        action="store_const",
        dest="mode",
        const="embedded",
        help="Bind the loopback in-process (single session; back-compat default).",
    )
    parser.set_defaults(mode="embedded", auto_spawn_daemon=True, progressive_disclosure=True)
    return parser


def build_server_parser() -> argparse.ArgumentParser:
    return _configure_parser(
        argparse.ArgumentParser(
            description="DayZ MCP stdio server",
            allow_abbrev=False,
        )
    )


def reject_glued_selector_flags(argv: list[str]) -> None:
    """`--instance` and `--game-path` take a separate argv element.

    Equals-glued forms and a repeated flag are invalid at every scanner.
    """
    if any(
        argument.startswith("--instance=") or argument.startswith("--game-path=")
        for argument in argv
    ):
        raise InstanceSelectionError("glued_instance_flag")
    if argv.count("--instance") > 1 or argv.count("--game-path") > 1:
        raise InstanceSelectionError("duplicate_instance_flag")
    for argument in argv:
        if argument.startswith("--instance") and argument != "--instance":
            raise InstanceSelectionError("glued_instance_flag")
        if argument.startswith("--game-path") and argument != "--game-path":
            raise InstanceSelectionError("glued_instance_flag")


def _canonicalize_selector(namespace: argparse.Namespace) -> None:
    try:
        namespace.instance = validate_instance_token(namespace.instance)
        namespace.game_path = validate_game_path(namespace.game_path)
    except InstanceSelectionError as error:
        raise _SilentInvalid(error.code) from error


def parse_server_tail_silent(argv: list[str]) -> ServerCliParse:
    parser = _configure_parser(
        _SilentArgumentParser(
            description="DayZ MCP stdio server",
            allow_abbrev=False,
        )
    )
    try:
        reject_glued_selector_flags(argv)
        namespace = parser.parse_args(argv)
        _canonicalize_selector(namespace)
    except _SilentTerminal:
        return ServerCliParse("terminal")
    except (_SilentInvalid, argparse.ArgumentError, InstanceSelectionError, ValueError):
        return ServerCliParse("invalid")
    return ServerCliParse("parsed", namespace)
