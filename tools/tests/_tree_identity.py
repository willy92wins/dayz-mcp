"""Compare the checkout a test was loaded from with the one a child imported.

The approved interpreter ships an editable install. Its ``.pth`` appends a
meta-path finder that maps ``dayz_mcp`` onto the live tree. PathFinder still
wins when this checkout is already on ``sys.path`` (cwd or PYTHONPATH), but a
child whose cwd is the live tree — or whose PYTHONPATH is only a fixture
site — never gets that chance: the finder is the first spec that succeeds,
and the child silently measures a different tree.

A green from that child does not accredit an edit in the copy. A red does
not accuse it. Callers compare the two roots and rebind child interpreters
so a miss fails loud instead of resolving to the live mapping. Rebind pins
a meta_path finder: sitecustomize cannot keep ``sys.path[0]`` after Python
prepends cwd, so path insertion alone does not dominate a live tools cwd.
"""

from __future__ import annotations

import os
import sys
from importlib.machinery import PathFinder
from pathlib import Path


class TreeIdentityError(AssertionError):
    """The test module and ``dayz_mcp`` were loaded from different checkouts."""


def checkout_root(path: Path | str) -> Path:
    """Return the checkout root that contains ``path``.

    A checkout is an ancestor with ``tools/dayz_mcp/__init__.py``. The
    comparison is on that root, not the file, so a test module and a package
    module from the same copy compare equal.
    """
    resolved = Path(path).resolve()
    ancestors = [resolved, *resolved.parents] if resolved.is_dir() else list(resolved.parents)
    for ancestor in ancestors:
        marker = ancestor / "tools" / "dayz_mcp" / "__init__.py"
        if marker.is_file():
            return ancestor
    raise ValueError(f"no dayz_mcp checkout contains {resolved}")


def mismatch_message(test_root: Path, package_root: Path) -> str:
    """Name both roots. A ``process_scan_incomplete`` does not."""
    return (
        "this subprocess is measuring another tree: "
        f"{test_root} vs {package_root}"
    )


def assert_same_checkout(test_file: Path | str, package_file: Path | str) -> None:
    """Fail if ``package_file`` was not loaded from the test's checkout."""
    test_root = checkout_root(test_file)
    package_root = checkout_root(package_file)
    if os.path.normcase(str(test_root)) != os.path.normcase(str(package_root)):
        raise TreeIdentityError(mismatch_message(test_root, package_root))


def editable_mapped_tools_dir() -> Path | None:
    """Return the tools directory the editable finder maps ``dayz_mcp`` onto."""
    for finder in sys.meta_path:
        module_name = getattr(finder, "__module__", None)
        if not isinstance(module_name, str) or not module_name:
            continue
        module = sys.modules.get(module_name)
        mapping = getattr(module, "MAPPING", None) if module is not None else None
        if not isinstance(mapping, dict):
            continue
        located = mapping.get("dayz_mcp")
        if not isinstance(located, str) or not located:
            continue
        root = Path(located).resolve().parent
        if (root / "dayz_mcp" / "daemon_contract.py").is_file():
            return root
    return None


class PinnedDayzMcpFinder:
    """Resolve ``dayz_mcp`` from ``tools_dir`` ahead of PathFinder.

    Sitecustomize runs during site init. Python then prepends ``''`` for
    ``-c``/``-m``, so a ``sys.path.insert(0, tools_dir)`` made there loses
    to a cwd that already contains the package. This finder is consulted
    before PathFinder and does not depend on fixture mode or path order.
    """

    def __init__(self, tools_dir: Path) -> None:
        self.tools_dir = Path(tools_dir).resolve()

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> object:
        if fullname != "dayz_mcp" and not fullname.startswith("dayz_mcp."):
            return None
        search = [str(self.tools_dir)] if fullname == "dayz_mcp" else path
        if not search:
            search = [str(self.tools_dir / "dayz_mcp")]
        return PathFinder.find_spec(fullname, search, target)


def bind_child_import_tree(tools_dir: Path | str) -> None:
    """Make ``import dayz_mcp`` resolve to ``tools_dir``, not the live mapping.

    PYTHONPATH does not beat a cwd that already contains ``dayz_mcp``:
    PathFinder searches ``sys.path[0]`` (empty string, the cwd) first. The
    historical fixture cwd *was* the editable mapping, so putting this copy
    on PYTHONPATH still loaded the live tree.

    Inserting ``tools_dir`` during sitecustomize is not enough either:
    site init finishes, then the interpreter prepends cwd. Pin a meta_path
    finder in front of PathFinder, drop the editable finder, and purge a
    foreign ``dayz_mcp`` already in ``sys.modules``. Fixture mode (eager
    import during sitecustomize) is not required for the pin to hold. A
    miss after the pin is ImportError, not a silent fallback to the live
    mapping.
    """
    resolved = Path(tools_dir).resolve()
    resolved_s = str(resolved)
    wanted = os.path.normcase(resolved_s)
    kept: list[str] = []
    for entry in sys.path:
        if not entry:
            kept.append(entry)
            continue
        try:
            if os.path.normcase(str(Path(entry).resolve())) == wanted:
                continue
        except OSError:
            kept.append(entry)
            continue
        kept.append(entry)
    sys.path[:] = [resolved_s, *kept]
    _drop_editable_dayz_mcp_finders()
    _install_pinned_dayz_mcp_finder(resolved)
    _purge_foreign_dayz_mcp(resolved)


def _install_pinned_dayz_mcp_finder(tools_dir: Path) -> None:
    sys.meta_path[:] = [
        finder
        for finder in sys.meta_path
        if not isinstance(finder, PinnedDayzMcpFinder)
    ]
    sys.meta_path.insert(0, PinnedDayzMcpFinder(tools_dir))


def _drop_editable_dayz_mcp_finders() -> None:
    remaining = []
    for finder in sys.meta_path:
        module_name = getattr(finder, "__module__", None)
        if isinstance(module_name, str) and module_name:
            module = sys.modules.get(module_name)
            mapping = getattr(module, "MAPPING", None) if module is not None else None
            if isinstance(mapping, dict) and "dayz_mcp" in mapping:
                continue
        remaining.append(finder)
    sys.meta_path[:] = remaining


def _purge_foreign_dayz_mcp(tools_dir: Path) -> None:
    expected = checkout_root(tools_dir)
    wanted = os.path.normcase(str(expected))
    names = [
        name
        for name in list(sys.modules)
        if name == "dayz_mcp" or name.startswith("dayz_mcp.")
    ]
    for name in names:
        module = sys.modules.get(name)
        loaded = getattr(module, "__file__", None) if module is not None else None
        if not isinstance(loaded, str) or not loaded:
            sys.modules.pop(name, None)
            continue
        try:
            loaded_root = checkout_root(Path(loaded))
        except ValueError:
            sys.modules.pop(name, None)
            continue
        if os.path.normcase(str(loaded_root)) != wanted:
            sys.modules.pop(name, None)
