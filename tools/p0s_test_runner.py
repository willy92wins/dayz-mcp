from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import multiprocessing.process
import os
import subprocess
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Iterable, Sequence, TextIO


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


WINDOWS_FFI_LAUNCH_PREFIXES = ("createprocess", "shellexecute")


class LaunchDenied(RuntimeError):
    """Raised before a process-launch surface can call its original target."""


@dataclass(frozen=True, slots=True)
class LaunchSurface:
    owner: object
    attribute: str
    label: str


def default_launch_surfaces() -> tuple[LaunchSurface, ...]:
    surfaces = [
        LaunchSurface(subprocess, "Popen", "subprocess.Popen"),
        LaunchSurface(subprocess, "run", "subprocess.run"),
        LaunchSurface(subprocess, "call", "subprocess.call"),
        LaunchSurface(subprocess, "check_call", "subprocess.check_call"),
        LaunchSurface(subprocess, "check_output", "subprocess.check_output"),
        LaunchSurface(
            asyncio,
            "create_subprocess_exec",
            "asyncio.create_subprocess_exec",
        ),
        LaunchSurface(
            asyncio,
            "create_subprocess_shell",
            "asyncio.create_subprocess_shell",
        ),
        LaunchSurface(os, "system", "os.system"),
        LaunchSurface(os, "popen", "os.popen"),
        LaunchSurface(
            multiprocessing.process.BaseProcess,
            "start",
            "multiprocessing.BaseProcess.start",
        ),
    ]

    if hasattr(os, "startfile"):
        surfaces.append(LaunchSurface(os, "startfile", "os.startfile"))

    for attribute in sorted(dir(os)):
        if attribute.startswith("spawn") and callable(getattr(os, attribute)):
            surfaces.append(LaunchSurface(os, attribute, f"os.{attribute}"))

    if os.name == "nt":
        try:
            import _winapi
        except ImportError:
            pass
        else:
            for attribute in sorted(dir(_winapi)):
                if _is_windows_ffi_launch(attribute) and callable(
                    getattr(_winapi, attribute)
                ):
                    surfaces.append(
                        LaunchSurface(_winapi, attribute, f"_winapi.{attribute}")
                    )

    return tuple(surfaces)


def _is_windows_ffi_launch(name: object) -> bool:
    if not isinstance(name, str):
        return False
    normalized = name.casefold()
    return any(normalized.startswith(prefix) for prefix in WINDOWS_FFI_LAUNCH_PREFIXES)


class _BlockedFFIFunction:
    def __init__(self, guard: DenyLaunchGuard, label: str) -> None:
        self._guard = guard
        self._label = label
        self.argtypes: object = None
        self.restype: object = None
        self.errcheck: object = None

    def __call__(self, *_args: object, **_kwargs: object) -> Any:
        self._guard._deny(self._label)


class _WindowsFFILibraryProxy:
    def __init__(self, library: object, guard: DenyLaunchGuard, label: str) -> None:
        object.__setattr__(self, "_library", library)
        object.__setattr__(self, "_guard", guard)
        object.__setattr__(self, "_label", label)
        object.__setattr__(self, "_blocked", {})

    def _resolve(self, name: object) -> object:
        if _is_windows_ffi_launch(name):
            blocked: dict[str, _BlockedFFIFunction] = object.__getattribute__(
                self, "_blocked"
            )
            key = str(name)
            if key not in blocked:
                label = object.__getattribute__(self, "_label")
                guard = object.__getattribute__(self, "_guard")
                blocked[key] = _BlockedFFIFunction(guard, f"{label}.{key}")
            return blocked[key]

        library = object.__getattribute__(self, "_library")
        if isinstance(name, str):
            return getattr(library, name)
        return library[name]  # type: ignore[index]

    def __getattr__(self, name: str) -> object:
        return self._resolve(name)

    def __getitem__(self, name: object) -> object:
        return self._resolve(name)


class DenyLaunchGuard:
    def __init__(
        self,
        surfaces: Iterable[LaunchSurface] | None = None,
        *,
        include_windows_ffi: bool = True,
    ) -> None:
        self._surfaces = tuple(
            default_launch_surfaces() if surfaces is None else surfaces
        )
        self._include_windows_ffi = include_windows_ffi
        self._restorations: list[tuple[object, str, object]] = []
        self._attempts: list[str] = []
        self._installed = False

    @property
    def attempts(self) -> tuple[str, ...]:
        return tuple(self._attempts)

    @property
    def intercept_count(self) -> int:
        return len(self._attempts)

    @property
    def installed(self) -> bool:
        return self._installed

    def _deny(self, label: str) -> None:
        self._attempts.append(label)
        raise LaunchDenied(f"P0.S deny-launch intercepted: {label}")

    def _blocker(self, label: str) -> Any:
        def blocked(*_args: object, **_kwargs: object) -> Any:
            self._deny(label)

        return blocked

    def _patch(self, owner: object, attribute: str, replacement: object) -> None:
        original = getattr(owner, attribute)
        self._restorations.append((owner, attribute, original))
        setattr(owner, attribute, replacement)

    def _install_windows_ffi_guard(self) -> None:
        if os.name != "nt":
            return

        class_loader_pairs = (
            ("CDLL", "cdll"),
            ("WinDLL", "windll"),
            ("OleDLL", "oledll"),
            ("PyDLL", "pydll"),
        )
        for class_name, loader_name in class_loader_pairs:
            original_class = getattr(ctypes, class_name, None)
            if original_class is None:
                continue

            guarded_class = self._guarded_library_class(original_class, class_name)
            self._patch(ctypes, class_name, guarded_class)

            loader = getattr(ctypes, loader_name, None)
            if loader is None or not hasattr(loader, "_dlltype"):
                continue

            for attribute, value in tuple(vars(loader).items()):
                if attribute.startswith("_") or not isinstance(value, original_class):
                    continue
                self._patch(
                    loader,
                    attribute,
                    self.wrap_windows_ffi_library(
                        value,
                        f"ctypes.{loader_name}.{attribute}",
                    ),
                )
            self._patch(loader, "_dlltype", guarded_class)

    def _guarded_library_class(self, original_class: type, family: str) -> type:
        guard = self

        class GuardedLibrary(original_class):  # type: ignore[misc, valid-type]
            def __getitem__(self, name: object) -> object:
                if _is_windows_ffi_launch(name):
                    cache = self.__dict__.setdefault("_p0s_blocked_ffi", {})
                    key = str(name)
                    if key not in cache:
                        cache[key] = _BlockedFFIFunction(
                            guard,
                            f"ctypes.{family}.{key}",
                        )
                    return cache[key]
                return super().__getitem__(name)

        GuardedLibrary.__name__ = f"P0SGuarded{family}"
        GuardedLibrary.__qualname__ = GuardedLibrary.__name__
        return GuardedLibrary

    def wrap_windows_ffi_library(
        self,
        library: object,
        label: str,
    ) -> _WindowsFFILibraryProxy:
        return _WindowsFFILibraryProxy(library, self, label)

    def install(self) -> DenyLaunchGuard:
        if self._installed:
            raise RuntimeError("P0.S deny-launch guard is already installed")

        try:
            for surface in self._surfaces:
                self._patch(
                    surface.owner,
                    surface.attribute,
                    self._blocker(surface.label),
                )
            if self._include_windows_ffi:
                self._install_windows_ffi_guard()
        except BaseException:
            self._restore()
            raise

        self._installed = True
        return self

    def _restore(self) -> None:
        while self._restorations:
            owner, attribute, original = self._restorations.pop()
            setattr(owner, attribute, original)
        self._installed = False

    def uninstall(self) -> None:
        self._restore()

    def __enter__(self) -> DenyLaunchGuard:
        return self.install()

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.uninstall()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an explicit unittest allowlist under the P0.S deny-launch guard."
    )
    parser.add_argument(
        "--tests",
        nargs="+",
        required=True,
        metavar="TEST_NAME",
        help="Explicit unittest names; implicit discovery is forbidden.",
    )
    return parser


def _emit_summary(
    *,
    status: str,
    exit_code: int,
    guard: DenyLaunchGuard,
    result: unittest.TestResult | None,
) -> None:
    payload = {
        "attempts": list(guard.attempts),
        "errors": len(result.errors) if result is not None else 0,
        "exit_code": exit_code,
        "failures": len(result.failures) if result is not None else 0,
        "intercept_count": guard.intercept_count,
        "status": status,
        "tests_run": result.testsRun if result is not None else 0,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def run_named_tests(
    test_names: Sequence[str],
    *,
    guard_factory: Callable[[], DenyLaunchGuard] = DenyLaunchGuard,
    loader: unittest.TestLoader = unittest.defaultTestLoader,
    stream: TextIO | None = None,
) -> int:
    if not test_names or any(not name.strip() for name in test_names):
        guard = DenyLaunchGuard((), include_windows_ffi=False)
        _emit_summary(status="invalid_usage", exit_code=2, guard=guard, result=None)
        return 2

    guard = guard_factory()
    result: unittest.TestResult | None = None
    try:
        with guard:
            suite = loader.loadTestsFromNames(list(test_names))
            result = unittest.TextTestRunner(verbosity=2, stream=stream).run(suite)
    except Exception as error:
        if guard.intercept_count:
            status = "launch_denied"
        else:
            status = f"runner_error:{type(error).__name__}"
        _emit_summary(status=status, exit_code=2, guard=guard, result=result)
        return 2

    if guard.intercept_count:
        _emit_summary(status="launch_denied", exit_code=2, guard=guard, result=result)
        return 2
    if result is None or not result.wasSuccessful():
        _emit_summary(status="test_failed", exit_code=1, guard=guard, result=result)
        return 1

    _emit_summary(status="pass", exit_code=0, guard=guard, result=result)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run_named_tests(args.tests)


if __name__ == "__main__":
    raise SystemExit(main())
