"""Run the sealed dayz-test worker from a verified app.pyz on Linux.

On Windows the native launcher starts ``runtime\\python.exe app.pyz`` from its
bundle: the private worker, and one ``--lifecycle-child`` per lifecycle frame the
worker sends. Outside Windows the relay starts this host's source, taken from
the verified archive, as::

    python -I -S -c <archived host source> --bundle DIR --app-sha256 HEX [--lifecycle-child]

``-S`` keeps ``site`` (and every ``.pth`` hook) out of the child. In order, the
host imports only the standard library, reads and hashes ``app.pyz``, creates
the ``dayz_mcp`` package from the archive's ``__init__.py`` with an empty
``__path__``, installs the sealed finder, and only then adds the interpreter's
site-packages directory to ``sys.path`` (without processing its ``.pth``
files) for the sealed set's one third-party dependency, ``psutil``. It then
installs the Linux overlay and runs the sealed ``app_main``.

- every sealed module (``dayz_mcp/<name>.py``) is imported from those bytes,
  not from the working tree -- including the Linux overlay (``linux_compat``,
  ``linux_build``, ``byte_lock``) and this host itself;
- ``msvcrt.open_osfhandle`` is the identity: the cancel handle is a POSIX pipe
  descriptor, and ``os.read`` on it returns when the launcher writes or closes
  (the shim exists only while the sealed module is imported);
- the sealed ``app_main`` (the archive's ``__main__.py``) runs unchanged, except
  that its bundle root is ``--bundle`` instead of ``sys.executable``'s
  grandparent (the request policy and worker runtime documents live there).

Known limit: the relay (``linux_launcher.py``), the interpreter, its standard
library and the venv's ``psutil`` run unsealed, as in the bundle design.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import io
import os
import stat
import sys
import sysconfig
import types
import zipfile
from pathlib import Path

_MAX_APP_BYTES = 16 * 1024 * 1024
_PACKAGE_PREFIX = "dayz_mcp/"


class _SealedModuleFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serve ``dayz_mcp`` and ``dayz_mcp.<name>`` from the verified archive."""

    def __init__(self, members: dict[str, bytes], origin: str) -> None:
        self._sources = {
            "dayz_mcp." + name[len(_PACKAGE_PREFIX) : -3]: data
            for name, data in members.items()
            if name.startswith(_PACKAGE_PREFIX)
            and name.endswith(".py")
            and name != _PACKAGE_PREFIX + "__init__.py"
            and "/" not in name[len(_PACKAGE_PREFIX) :]
        }
        init = members.get(_PACKAGE_PREFIX + "__init__.py")
        if init is None:
            raise RuntimeError("package_init_missing")
        self._sources["dayz_mcp"] = init
        self._origin = origin

    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001 - importlib protocol
        if fullname not in self._sources:
            return None
        if fullname == "dayz_mcp":
            spec = importlib.util.spec_from_loader(
                fullname, self, origin=f"{self._origin}/dayz_mcp/__init__.py", is_package=True
            )
            spec.submodule_search_locations = []
            return spec
        return importlib.util.spec_from_loader(
            fullname, self, origin=f"{self._origin}/{fullname.replace('.', '/')}.py"
        )

    def create_module(self, spec):  # noqa: ANN001 - importlib protocol
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        source = self._sources[module.__name__]
        module.__file__ = module.__spec__.origin
        exec(compile(source, module.__spec__.origin, "exec", dont_inherit=True), module.__dict__)


def _read_app(bundle: Path, expected_sha256: str) -> dict[str, bytes]:
    path = bundle / "app.pyz"
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_APP_BYTES:
            raise RuntimeError("app_invalid")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            raw = stream.read(_MAX_APP_BYTES + 1)
    finally:
        os.close(descriptor)
    if hashlib.sha256(raw).hexdigest().upper() != expected_sha256.upper():
        raise RuntimeError("app_hash_mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return {item.filename: archive.read(item) for item in archive.infolist()}


def _load_app_main(members: dict[str, bytes], bundle: Path) -> types.ModuleType:
    """Execute the sealed ``__main__.py`` as a module, with an ``msvcrt`` for its import.

    The shim is in ``sys.modules`` only while that module body runs: a module
    named ``msvcrt`` makes ``subprocess`` (and so ``asyncio``) take its Windows
    branch, so everything else must already be imported and it is removed again.
    """
    module = types.ModuleType("dayz_test_app_main")
    module.__file__ = str(bundle / "app.pyz" / "__main__.py")
    shim = types.ModuleType("msvcrt")
    shim.open_osfhandle = lambda handle, _flags: int(handle)
    if "msvcrt" in sys.modules:
        raise RuntimeError("msvcrt_present")
    sys.modules["msvcrt"] = shim
    sys.modules[module.__name__] = module
    try:
        exec(compile(members["__main__.py"], module.__file__, "exec", dont_inherit=True), module.__dict__)
    finally:
        del sys.modules["msvcrt"]
    module._bundle_root = lambda: bundle
    return module


def _venv_root() -> Path | None:
    """The venv root named by the ``pyvenv.cfg`` next to or above the executable.

    With ``-S``, ``site`` never runs, and on Python before 3.14 the venv is
    only detected by ``site``: ``sys.prefix`` (and so ``sysconfig``) still
    names the base installation, whose site-packages does not hold the
    venv's ``psutil``. Locate the venv the way ``site`` does, from
    ``pyvenv.cfg`` beside ``sys.executable`` or in its parent directory,
    without importing ``site`` or processing any ``.pth`` file.
    """
    executable = Path(sys.executable)
    for directory in (executable.parent, executable.parent.parent):
        if (directory / "pyvenv.cfg").is_file():
            return directory
    return None


def _add_site_packages() -> None:
    """Add site-packages for ``psutil`` without running any ``.pth`` hook."""
    candidates: list[str] = []
    venv = _venv_root()
    if venv is not None:
        version = f"python{sys.version_info.major}.{sys.version_info.minor}"
        candidates.extend(
            str(venv / relative)
            for relative in (
                Path("lib") / version / "site-packages",
                Path("lib64") / version / "site-packages",
                Path("Lib") / "site-packages",
            )
        )
        try:
            candidates.extend(
                str(path) for path in sorted(venv.glob("lib/python*/site-packages"))
            )
        except OSError:
            pass
    else:
        paths = sysconfig.get_paths()
        for key in ("purelib", "platlib"):
            candidate = paths.get(key)
            if candidate:
                candidates.append(candidate)
    for candidate in candidates:
        if candidate not in sys.path and os.path.isdir(candidate):
            sys.path.append(candidate)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dayz_mcp.linux_worker_host", allow_abbrev=False)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--app-sha256", required=True)
    parser.add_argument("--lifecycle-child", action="store_true")
    args = parser.parse_args(argv)
    bundle = Path(args.bundle)
    if not bundle.is_absolute() or os.path.normpath(args.bundle) != args.bundle:
        return 2

    members = _read_app(bundle, args.app_sha256)
    sealed = _SealedModuleFinder(members, str(bundle / "app.pyz"))
    already = [name for name in sealed._sources if name in sys.modules]
    if already:
        raise RuntimeError("sealed_module_preloaded")
    sys.meta_path.insert(0, sealed)

    import dayz_mcp  # noqa: F401 - created by the sealed finder from the archive

    _add_site_packages()

    import asyncio  # noqa: F401 - imported before the msvcrt shim exists, see _load_app_main
    import subprocess  # noqa: F401

    from dayz_mcp import linux_compat

    linux_compat.install()
    app_main = _load_app_main(members, bundle)
    sys.argv = [sys.argv[0], *(["--lifecycle-child"] if args.lifecycle_child else [])]
    return app_main.main()


if __name__ == "__main__":
    raise SystemExit(main())
