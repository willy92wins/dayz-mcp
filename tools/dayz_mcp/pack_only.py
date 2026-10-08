"""Shared AddonBuilder ``-packonly`` predicate.

AddonBuilder's binarize pass uses ``-addon=P:`` and dies if any ``config.cpp``
under ``P:\\`` fails to parse (fb-20260915-005408-bcd8). The worker, the
native launcher, and the pack-addon script therefore pass ``-packonly``
when the source tree has no binarizable assets.
"""

from __future__ import annotations

import os
from pathlib import Path

BINARIZABLE_SUFFIXES = frozenset({".p3d", ".paa", ".rvmat"})

# Microsoft reparse tag bit: junctions and symbolic links are name surrogates.
# Cloud placeholders (OneDrive) are reparse points without this bit and stay
# visible to the scan. Do not treat every reparse point as a link.
_NAME_SURROGATE_BIT = 0x20000000


def _reparse_tag(path: Path) -> int:
    """Tag of ``path`` itself. Zero when the entry is not a reparse point."""
    info = os.stat(path, follow_symlinks=False)
    return int(getattr(info, "st_reparse_tag", 0) or 0)


def _is_name_surrogate_tag(tag: int) -> bool:
    """True for a junction or symbolic link; false for a cloud placeholder."""
    return bool(int(tag) & _NAME_SURROGATE_BIT)


def has_binarizable_assets(source: str | Path) -> bool:
    """True when ``source`` contains a ``.p3d``, ``.paa`` or ``.rvmat`` file.

    Directory entries are listed without following them. Mount-point junctions
    and symbolic links are not descended into and are not counted as assets.
    Other reparse points, including OneDrive placeholders, are ordinary files
    or directories. ``OSError`` from the listing is left to the caller.
    """
    pending = [Path(source)]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if _is_name_surrogate_tag(_reparse_tag(Path(entry.path))):
                        continue
                    pending.append(Path(entry.path))
                    continue
                if (
                    entry.is_file(follow_symlinks=False)
                    and Path(entry.name).suffix.casefold() in BINARIZABLE_SUFFIXES
                ):
                    return True
    return False


def should_pack_only(source: str | Path, *, pack_only: bool = False) -> bool:
    """Same rule as ``dayz_test_worker``: explicit flag or no binarizable assets."""
    return bool(pack_only) or not has_binarizable_assets(source)


def addon_builder_packonly_args(
    source: str | Path, *, pack_only: bool = False
) -> tuple[str, ...]:
    """Extra AddonBuilder flags so ``-packonly`` is passed when appropriate."""
    if should_pack_only(source, pack_only=pack_only):
        return ("-packonly",)
    return ()
