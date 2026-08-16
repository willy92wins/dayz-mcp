"""Guards for tests that need a launcher a fresh clone will not have.

Two artifacts never ship. The bundle is a build output — a compiled launcher
plus an embedded CPython, sealed against this machine's NTFS file ids — so the
repo carries its source and the builder instead. The approved-launchers
registry pins absolute paths and file ids, so only the empty baseline ships and
a clone installs onto it.

Tests that read either one have to skip where they are absent, otherwise a
clone's first run is red for a reason that is not a defect. Companion of
[_addon_paths] for the other half of the same problem: paths that differ
between the author's tree and a clone.
"""

import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
BUNDLE = TOOLS_DIR / "native-launchers" / "dayz-test-v1"
LAUNCHER_PE = BUNDLE / "dayz-test-launcher.exe"
REGISTRY = TOOLS_DIR / "approved-launchers.json"

# Keyed on a build output, not on BUNDLE.is_dir(): the directory is present in
# every clone because the launcher's own source ships inside it, so the
# directory test was true everywhere and this guard could never fire.
requires_built_bundle = unittest.skipUnless(
    LAUNCHER_PE.is_file(),
    f"native launcher bundle not built at {BUNDLE}; "
    "run build_native_launcher.py --offline first",
)

requires_installed_launcher = unittest.skipUnless(
    LAUNCHER_PE.is_file() and REGISTRY.is_file(),
    "no installed launcher: build the bundle, then "
    "python -m dayz_mcp.launcher_registry_update install-dayz-test-v1",
)
