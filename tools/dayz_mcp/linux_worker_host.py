"""Run the sealed dayz-test worker from a verified app.pyz on Linux.

On Windows the native launcher starts ``runtime\\python.exe app.pyz`` from its
bundle: the private worker, and one ``--lifecycle-child`` per lifecycle frame the
worker sends. Outside Windows this module is that interpreter's entry:

    python -I -m dayz_mcp.linux_worker_host --bundle DIR --app-sha256 HEX [--lifecycle-child]

- ``app.pyz`` is read once, its SHA-256 must be the one the launcher verified
  against the bundle manifest, and every sealed module (``dayz_mcp/<name>.py``)
  is then imported from those bytes, not from the working tree;
- ``msvcrt.open_osfhandle`` is the identity: the cancel handle is a POSIX pipe
  descriptor, and ``os.read`` on it returns when the launcher writes or closes
  (the shim exists only while the sealed module is imported);
- ``linux_compat.install()`` gives the sealed modules their POSIX behaviour;
- the sealed ``app_main`` (the archive's ``__main__.py``) runs unchanged, except
  that its bundle root is ``--bundle`` instead of ``sys.executable``'s
  grandparent (the request policy and worker runtime documents live there).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import importlib.util
import io
import os
import stat
import sys
import types
import zipfile
from pathlib import Path

_MAX_APP_BYTES = 16 * 1024 * 1024
_PACKAGE_PREFIX = "dayz_mcp/"


class _SealedModuleFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serve ``dayz_mcp.<name>`` from the verified archive when it holds that module."""

    def __init__(self, members: dict[str, bytes], origin: str) -> None:
        self._sources = {
            "dayz_mcp." + name[len(_PACKAGE_PREFIX) : -3]: data
            for name, data in members.items()
            if name.startswith(_PACKAGE_PREFIX)
            and name.endswith(".py")
            and name != _PACKAGE_PREFIX + "__init__.py"
            and "/" not in name[len(_PACKAGE_PREFIX) :]
        }
        self._origin = origin

    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001 - importlib protocol
        if fullname not in self._sources:
            return None
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
    package = sys.modules.get("dayz_mcp")
    package_init = Path(getattr(package, "__file__", "") or "")
    if package_init.read_bytes() != members.get(_PACKAGE_PREFIX + "__init__.py"):
        raise RuntimeError("package_init_mismatch")
    sealed = _SealedModuleFinder(members, str(bundle / "app.pyz"))
    already = [name for name in sealed._sources if name in sys.modules]
    if already:
        raise RuntimeError("sealed_module_preloaded")
    sys.meta_path.insert(0, sealed)

    import asyncio  # noqa: F401 - imported before the msvcrt shim exists, see _load_app_main
    import subprocess  # noqa: F401

    from dayz_mcp import linux_compat

    linux_compat.install()
    app_main = _load_app_main(members, bundle)
    sys.argv = [sys.argv[0], *(["--lifecycle-child"] if args.lifecycle_child else [])]
    return app_main.main()


if __name__ == "__main__":
    raise SystemExit(main())
