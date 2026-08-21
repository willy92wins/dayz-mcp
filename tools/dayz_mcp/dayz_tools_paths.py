"""Resolve DayZ Tools, Steam, and DayZDiag roots without assuming C:\\Program Files.

Order, and only this order:
  1. ``DAYZ_TOOLS_PATH`` if set to a non-empty value;
  2. the registry, which is where a normal install records itself (see below);
  3. the historical default under ``C:\\Program Files (x86)\\Steam`` as last-resort
     fallback.

Step 2 exists because a Steam library on another drive is the common case, not the
exotic one, and the registry answers it without parsing ``libraryfolders.vdf``. The
keys below were READ on a live Windows 11 host on 2026-08-21, not inferred:

    HKCU\\Software\\Bohemia Interactive\\DayZ Tools    path         -> ...\\common\\DayZ Tools
    HKCU\\Software\\Valve\\Steam                       SteamPath    -> c:/program files (x86)/steam
    HKLM\\SOFTWARE\\WOW6432Node\\Valve\\Steam          InstallPath  -> C:\\Program Files (x86)\\Steam
    HKLM\\SOFTWARE\\Valve\\Steam                        InstallPath  -> C:\\Program Files (x86)\\Steam

The Bohemia key is tried first: it points AT the Tools, so it stays correct when the
library moves. Note ``SteamPath`` comes back lowercased with forward slashes, which is
why every value goes through ``Path`` before use.

Every registry read is best-effort by design. ``winreg`` does not exist off Windows and
a key can be missing, renamed or of the wrong type on Windows too -- a portable Steam, a
per-user install, a machine where DayZ Tools was never launched. None of that is an
error here: the step contributes nothing and the chain falls through to the default,
which is exactly what the caller would have got before this step existed.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path


TOOLS_ENV = "DAYZ_TOOLS_PATH"
# (hive, subkey, value). Order matters: the Bohemia key names the Tools directly.
REGISTRY_TOOLS_KEYS: tuple[tuple[str, str, str], ...] = (
    ("HKEY_CURRENT_USER", r"Software\Bohemia Interactive\DayZ Tools", "path"),
)
# Steam roots; the Tools live under <steam>\steamapps\common\DayZ Tools.
REGISTRY_STEAM_KEYS: tuple[tuple[str, str, str], ...] = (
    ("HKEY_CURRENT_USER", r"Software\Valve\Steam", "SteamPath"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\Valve\Steam", "InstallPath"),
)
# Last-resort fallback. Not a discovered install — the path the package used to hardcode.
DEFAULT_STEAM_ROOT = Path(r"C:\Program Files (x86)\Steam")
DEFAULT_TOOLS_ROOT = DEFAULT_STEAM_ROOT / "steamapps" / "common" / "DayZ Tools"
DEFAULT_DAYZ_ROOT = DEFAULT_STEAM_ROOT / "steamapps" / "common" / "DayZ"
DIAG_NAME = "DayZDiag_x64.exe"
ADDON_BUILDER_RELATIVE = ("Bin", "AddonBuilder", "AddonBuilder.exe")
STEAM_CLIENT_DLL = "steamclient.dll"

# Same files the builder used to pin as absolute paths under the default Tools root.
TOOLS_RELATIVE_FILES: tuple[tuple[str, ...], ...] = (
    ("Bin", "AddonBuilder", "AddonBuilder.exe"),
    ("Bin", "AddonBuilder", "AddonBuilder.exe.config"),
    ("Bin", "AddonBuilder", "log4net.dll"),
    ("Bin", "AddonBuilder", "NDesk.Options.dll"),
    ("Bin", "AddonBuilder", "SharedResources.dll"),
    ("Bin", "AddonBuilder", "SteamHelper.dll"),
    ("Bin", "AddonBuilder", "SteamLayerWrap.dll"),
    ("Bin", "AddonBuilder", "steam_api.dll"),
    ("Bin", "AddonBuilder", "Utils.dll"),
    ("Bin", "AddonBuilder", "en-US", "SharedResources.resources.dll"),
    ("Bin", "AddonBuilder", "logger.xml"),
    ("Bin", "AddonBuilder", "steam_appid.txt"),
    ("Bin", "Binarize", "binarize.exe"),
    ("Bin", "Binarize", "steam_api64.dll"),
    ("Bin", "Binarize", "bin.txt"),
    ("Bin", "Binarize", "bin", "config.cpp"),
    ("Bin", "CfgConvert", "CfgConvert.exe"),
    ("Bin", "PboUtils", "FileBank.exe"),
    ("Bin", "PboUtils", "NativeMethods.dll"),
    ("Bin", "PboUtils", "log4net.dll"),
    ("Bin", "PboUtils", "LibCommon.dll"),
    ("Bin", "PboUtils", "exclude.lst"),
)
STEAM_RELATIVE_FILES: tuple[str, ...] = (
    "steamclient.dll",
    "Steam.dll",
    "CSERHelper.dll",
    "GameOverlayRenderer.dll",
    "tier0_s.dll",
    "vstdlib_s.dll",
)
ADDON_HELPER_RELATIVE: tuple[tuple[str, ...], ...] = (
    ("Bin", "Binarize", "binarize.exe"),
    ("Bin", "CfgConvert", "CfgConvert.exe"),
    ("Bin", "PboUtils", "FileBank.exe"),
)


@dataclass(frozen=True, slots=True)
class DayZLayout:
    tools: Path
    steam: Path
    dayz: Path


def _mapping(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _env_tools(environ: Mapping[str, str] | None) -> Path | None:
    value = _mapping(environ).get(TOOLS_ENV)
    if type(value) is not str or not value.strip():
        return None
    return Path(value.strip())


def read_registry_string(hive: str, subkey: str, value: str) -> str | None:
    """One registry string, or None. Never raises -- see the module docstring."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        root = getattr(winreg, hive)
        with winreg.OpenKey(root, subkey) as handle:
            data, kind = winreg.QueryValueEx(handle, value)
    except OSError:
        return None
    except AttributeError:
        return None
    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or type(data) is not str:
        return None
    data = data.strip()
    return data or None


def _registry_tools(
    reader: Callable[[str, str, str], str | None] | None = None,
) -> list[Path]:
    """Tools roots the registry knows about, best first, no duplicates yet."""
    read = reader if reader is not None else read_registry_string
    found: list[Path] = []
    for hive, subkey, value in REGISTRY_TOOLS_KEYS:
        raw = read(hive, subkey, value)
        if raw:
            found.append(Path(raw))
    for hive, subkey, value in REGISTRY_STEAM_KEYS:
        raw = read(hive, subkey, value)
        if raw:
            found.append(Path(raw) / "steamapps" / "common" / "DayZ Tools")
    return found


def _unique(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    ordered: list[Path] = []
    for path in paths:
        folded = os.path.normcase(str(path))
        if folded in seen:
            continue
        seen.add(folded)
        ordered.append(path)
    return ordered


def _steam_root_from_tools(tools: Path) -> Path | None:
    if tools.name.casefold() != "dayz tools":
        return None
    common = tools.parent
    steamapps = common.parent
    if common.name.casefold() != "common" or steamapps.name.casefold() != "steamapps":
        return None
    return steamapps.parent


def tools_root_candidates(
    environ: Mapping[str, str] | None = None,
    registry: Callable[[str, str, str], str | None] | None = None,
) -> list[Path]:
    paths: list[Path] = []
    env_tools = _env_tools(environ)
    if env_tools is not None:
        paths.append(env_tools)
    paths.extend(_registry_tools(registry))
    paths.append(DEFAULT_TOOLS_ROOT)
    return _unique(paths)


def steam_root_candidates(tools: Path) -> list[Path]:
    paths: list[Path] = []
    inferred = _steam_root_from_tools(tools)
    if inferred is not None:
        paths.append(inferred)
    paths.append(DEFAULT_STEAM_ROOT)
    return _unique(paths)


def dayz_root_candidates(tools: Path, steam: Path) -> list[Path]:
    paths: list[Path] = []
    if tools.parent.name.casefold() == "common":
        paths.append(tools.parent / "DayZ")
    paths.append(steam / "steamapps" / "common" / "DayZ")
    paths.append(DEFAULT_DAYZ_ROOT)
    return _unique(paths)


def selected_layout(environ: Mapping[str, str] | None = None) -> DayZLayout:
    """Env if set, else the historical default. Does not touch the filesystem."""
    tools = _env_tools(environ) or DEFAULT_TOOLS_ROOT
    steam = _steam_root_from_tools(tools) or DEFAULT_STEAM_ROOT
    if tools.parent.name.casefold() == "common":
        dayz = tools.parent / "DayZ"
    else:
        dayz = steam / "steamapps" / "common" / "DayZ"
    return DayZLayout(tools=tools, steam=steam, dayz=dayz)


def _first_file(
    roots: list[Path],
    relative: Path,
    is_file: Callable[[Path], bool],
) -> Path | None:
    for root in roots:
        if is_file(root / relative):
            return root
    return None


def require_dayz_layout(
    *,
    environ: Mapping[str, str] | None = None,
    is_file: Callable[[Path], bool] | None = None,
) -> DayZLayout:
    """First candidate whose marker file exists; otherwise name every path tried."""
    exists = Path.is_file if is_file is None else is_file
    tools_tried = tools_root_candidates(environ)
    tools = _first_file(tools_tried, Path(*ADDON_BUILDER_RELATIVE), exists)
    if tools is None:
        raise ValueError(
            "dayz_tools_not_found: tried "
            + "; ".join(str(path) for path in tools_tried)
            + f"; set {TOOLS_ENV} to the DayZ Tools directory"
        )
    steam_tried = steam_root_candidates(tools)
    steam = _first_file(steam_tried, Path(STEAM_CLIENT_DLL), exists)
    if steam is None:
        raise ValueError(
            "steam_root_not_found: tried "
            + "; ".join(str(path) for path in steam_tried)
            + f"; set {TOOLS_ENV} to the DayZ Tools directory under steamapps\\common"
        )
    dayz_tried = dayz_root_candidates(tools, steam)
    dayz = _first_file(dayz_tried, Path(DIAG_NAME), exists)
    if dayz is None:
        raise ValueError(
            "dayz_diag_not_found: tried "
            + "; ".join(str(path / DIAG_NAME) for path in dayz_tried)
            + f"; set {TOOLS_ENV} so DayZDiag sits next to DayZ Tools, or install DayZDiag"
        )
    return DayZLayout(tools=tools, steam=steam, dayz=dayz)


def external_file_paths(layout: DayZLayout) -> tuple[Path, ...]:
    files = [layout.tools.joinpath(*parts) for parts in TOOLS_RELATIVE_FILES]
    files.extend(layout.steam / name for name in STEAM_RELATIVE_FILES)
    files.append(layout.dayz / DIAG_NAME)
    return tuple(files)


def addon_builder_exe(environ: Mapping[str, str] | None = None) -> str:
    return str(selected_layout(environ).tools.joinpath(*ADDON_BUILDER_RELATIVE))


def addon_helper_exes(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    tools = selected_layout(environ).tools
    return tuple(str(tools.joinpath(*parts)) for parts in ADDON_HELPER_RELATIVE)


def dayz_diag_exe(environ: Mapping[str, str] | None = None) -> str:
    return str(selected_layout(environ).dayz / DIAG_NAME)
