"""Shared ExampleMod request/runtime policies for dayz-test worker tests.

Moved verbatim from test_dayz_test_worker.py so new tests stop importing
that test module (review 2026-09-25, T0; CI shard 1/4 on #365).
"""
from __future__ import annotations

from dayz_mcp import dayz_test_request, dayz_test_worker


POLICY = dayz_test_request.RequestProjectPolicy(
    mod="ExampleMod",
    dev_root=r"P:\ExampleMod_Suite",
    default_source=r"P:\ExampleMod",
    default_base_mods=("@CF", "@Dabs Framework", "@VPPAdminTools"),
    mission_roots=(r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions",),
    mod_roots=(r"P:\Mods",),
)
RUNTIME = dayz_test_worker.WorkerRuntimePolicy(
    dev_root=POLICY.dev_root,
    mod="ExampleMod",
    diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
    game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
    mission_aliases=(
        ("chernarus", r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.chernarusplus"),
        ("livonia", r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.enoch"),
        ("sakhal", r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.sakhal"),
    ),
    mods_root=r"P:\Mods",
    build_temp_root=r"P:\temp",
    build_source_basename=None,
)
