"""Stage and build the dayz-mcp wheel without writing into the repo tree."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def stage_wheel_tree(
    tools_dir: str | Path,
    playbooks_dir: str | Path,
    stage: str | Path,
    version_json: bytes | bytearray | str | Path,
) -> Path:
    """Copy the wheel inputs into an empty staging directory and return it."""
    tools = Path(tools_dir)
    playbooks = Path(playbooks_dir)
    target = Path(stage)
    if target.exists() and any(target.iterdir()):
        raise ValueError("wheel_stage_not_empty")
    target.mkdir(parents=True, exist_ok=True)
    package_stage = target / "dayz_mcp"
    package_stage.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(tools / "pyproject.toml", target / "pyproject.toml")
    for source in sorted((tools / "dayz_mcp").glob("*.py")):
        shutil.copyfile(source, package_stage / source.name)
    shutil.copyfile(tools / "mcp_capture.py", target / "mcp_capture.py")
    shutil.copyfile(tools / "mcp-grab.ps1", target / "mcp-grab.ps1")

    playbooks_stage = package_stage / "playbooks"
    playbooks_stage.mkdir(parents=True, exist_ok=True)
    if playbooks.is_dir():
        for source in sorted(playbooks.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(playbooks)
            if "__pycache__" in relative.parts or source.suffix == ".pyc":
                continue
            destination = playbooks_stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    if isinstance(version_json, (bytes, bytearray)):
        data = bytes(version_json)
    elif isinstance(version_json, Path):
        data = version_json.read_bytes()
    else:
        data = str(version_json).encode("utf-8")
    (package_stage / "VERSION.json").write_bytes(data)
    return target


def build_wheel(
    stage: str | Path,
    out_dir: str | Path,
    python: str | Path | None = None,
    run: object = None,
) -> Path:
    """Build one wheel from a staged tree and return its path."""
    runner = subprocess.run if run is None else run
    command = [
        str(python) if python is not None else sys.executable,
        "-m",
        "pip",
        "wheel",
        "--no-deps",
        "--wheel-dir",
        str(out_dir),
        str(stage),
    ]
    result = runner(command, capture_output=True, text=True)  # type: ignore[operator]
    if result.returncode != 0:
        stderr = result.stderr or ""
        tail = "\n".join(str(stderr).splitlines()[-10:])
        raise RuntimeError(f"wheel_build_failed: {tail}")
    out = Path(out_dir)
    wheels = sorted(out.glob("*.whl")) if out.is_dir() else []
    if len(wheels) != 1:
        raise RuntimeError(
            f"wheel_build_failed: expected one wheel, found {len(wheels)}"
        )
    return wheels[0]
