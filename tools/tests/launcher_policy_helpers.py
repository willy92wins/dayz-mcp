"""Launcher policy source fixture shared by the native launcher policy tests.

Moved verbatim from test_build_native_launcher_policy.py so tests do not import
each other (tests/test_suite_structure.py).
"""
from __future__ import annotations

from pathlib import Path


def make_fixture_source(root: Path, *, mods: tuple[str, ...] = ("ExampleMod",)) -> dict[str, object]:
    root = Path(root)
    source_dir = root / "source"
    dev_dir = root / "dev"
    missions = dev_dir / "_server" / "mpmissions"
    mods_dir = root / "mods"
    temp_dir = root / "temp"
    for path in (
        source_dir,
        missions / "dayzOffline.chernarusplus",
        missions / "dayzOffline.enoch",
        missions / "dayzOffline.sakhal",
        mods_dir / "@ExampleMod",
        mods_dir / "@ExampleCore",
        temp_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    def located(*parts: str) -> str:
        return str(root.joinpath(*parts))

    projects: list[dict[str, object]] = []
    for mod in mods:
        projects.append(
            {
                "mod": mod,
                "default_base_mods": ["@ExampleCore"],
                "default_source": {
                    "path": located("source"),
                    "allow_root_junction": False,
                },
                "dev_root": {
                    "path": located("dev"),
                    "allow_root_junction": False,
                },
                "mission_roots": [
                    {
                        "path": located("dev", "_server", "mpmissions"),
                        "allow_root_junction": False,
                    }
                ],
                "mod_roots": [
                    {
                        "path": located("mods"),
                        "allow_root_junction": False,
                    }
                ],
                "build_temp_root": located("temp"),
                "build_source_basename": None,
                "diag_executable": r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
                "game_directory": r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
                "mission_aliases": {
                    "chernarus": located(
                        "dev", "_server", "mpmissions", "dayzOffline.chernarusplus"
                    ),
                    "livonia": located(
                        "dev", "_server", "mpmissions", "dayzOffline.enoch"
                    ),
                    "sakhal": located(
                        "dev", "_server", "mpmissions", "dayzOffline.sakhal"
                    ),
                },
                "mods_root": located("mods"),
            }
        )
    return {"format_version": 1, "projects": projects}
