"""Installed-layout detection for a Windows venv.

This is the layout of a Windows venv created with the standard library
``venv`` module: the package lives in ``Lib/site-packages`` under the venv
root and ``pyvenv.cfg`` sits at that root. From a source checkout this
returns ``None``.
"""

from __future__ import annotations

from pathlib import Path


def installed_venv_root(module_file: str | Path) -> Path | None:
    """Return the venv root that holds this module, or ``None``.

    Only the module path and ``pyvenv.cfg`` decide: never ``sys.prefix``,
    ``sys.executable``, ``PYTHONPATH`` or a ``.pth`` file.
    """
    try:
        resolved = Path(module_file).resolve(strict=True)
    except OSError:
        return None
    package_dir = resolved.parent
    if package_dir.name.casefold() != "dayz_mcp":
        return None
    site_packages = package_dir.parent
    if site_packages.name.casefold() != "site-packages":
        return None
    lib_dir = site_packages.parent
    if lib_dir.name.casefold() != "lib":
        return None
    root = lib_dir.parent
    config = root / "pyvenv.cfg"
    try:
        if config.is_symlink() or not config.is_file():
            return None
    except OSError:
        return None
    return root
