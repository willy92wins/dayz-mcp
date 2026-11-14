"""Write and check the lock of sources embedded in the sealed launcher."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import build_native_launcher

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = REPO_ROOT / "tools" / "packaged-modules.lock.json"
LINUX_LOCK_PATH = REPO_ROOT / "tools" / "packaged-modules.linux.lock.json"
LINUX_PACKAGED_MODULES = (
    "byte_lock.py",
    "linux_build.py",
    "linux_compat.py",
    "linux_worker_host.py",
)
LINUX_DRIFT_RECIPE = (
    "A source sealed only in the Linux bundle differs from "
    "tools/packaged-modules.linux.lock.json. Regenerate the Linux lock with "
    "write_packaged_modules_lock.py --linux, then reinstall the Linux bundle with "
    "python -m dayz_mcp.linux_launcher install. No Windows launcher rebuild is needed."
)
DRIFT_RECIPE = (
    "A source embedded in the sealed launcher differs from "
    "tools/packaged-modules.lock.json. Rebuild the launcher with build_native_launcher.py --offline "
    "--verify-reproducible, replace the registry with python -m dayz_mcp.launcher_registry_update "
    "replace-dayz-test-v1 --expected-sha256 <current registry sha256>, then regenerate the lock with "
    "write_packaged_modules_lock.py. Rebuilding changes the launcher for every session and needs the "
    "owner's approval."
)


def expected_paths() -> list[str]:
    paths = [f"tools/dayz_mcp/{name}" for name in build_native_launcher.PACKAGED_MODULES]
    paths.append("tools/dayz_mcp/__init__.py")
    paths.append("tools/native-launchers/dayz-test-v1/src/app_main.py")
    paths.append("tools/native-launchers/dayz-test-v1/src/launcher.cpp")
    return sorted(paths)


def normalize_crlf_to_lf(data: bytes) -> bytes:
    """Replace each CRLF with LF; lone CR bytes are left unchanged.

    git applies the same normalization under `text=auto eol=lf`, and a working
    copy with CRLF line endings is the same source.
    """
    return data.replace(b"\r\n", b"\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(normalize_crlf_to_lf(path.read_bytes()))
    return digest.hexdigest().upper()


def compute_lock(root: Path = REPO_ROOT) -> dict[str, object]:
    files = {relative: _sha256(root / relative) for relative in expected_paths()}
    return {"format_version": 1, "files": files}


def expected_linux_paths() -> list[str]:
    return sorted(f"tools/dayz_mcp/{name}" for name in LINUX_PACKAGED_MODULES)


def _dayzmake_record(root: Path = REPO_ROOT) -> dict[str, str]:
    import ast

    tree = ast.parse((root / "tools/dayz_mcp/linux_build.py").read_text(encoding="utf-8"))
    values: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"DAYZMAKE_SHA256", "DAYZMAKE_VERSION"}:
                    values[target.id] = str(node.value.value)
    return {
        "binary_sha256": values["DAYZMAKE_SHA256"].upper(),
        "version": values["DAYZMAKE_VERSION"].removeprefix("v"),
    }


def compute_linux_lock(root: Path = REPO_ROOT) -> dict[str, object]:
    files = {relative: _sha256(root / relative) for relative in expected_linux_paths()}
    return {"dayzmake_linux": _dayzmake_record(root), "files": files, "format_version": 1}


def linux_problems(committed: object, current: object) -> list[str]:
    committed_files = _files_map(committed)
    current_files = _files_map(current)
    lines: list[str] = []
    for relative in sorted(set(committed_files) | set(current_files)):
        if relative not in committed_files:
            lines.append(f"{relative} is missing from tools/packaged-modules.linux.lock.json")
        elif relative not in current_files:
            lines.append(f"{relative} is unexpected in tools/packaged-modules.linux.lock.json")
        elif committed_files[relative] != current_files[relative]:
            lines.append(f"{relative} differs from tools/packaged-modules.linux.lock.json")
    if type(committed) is dict and committed.get("dayzmake_linux") != (
        current.get("dayzmake_linux") if type(current) is dict else None
    ):
        lines.append("dayzmake_linux differs from tools/packaged-modules.linux.lock.json")
    return lines


def render(lock: object) -> bytes:
    return (json.dumps(lock, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _files_map(lock: object) -> dict[str, object]:
    if type(lock) is not dict:
        return {}
    files = lock.get("files")
    if type(files) is not dict:
        return {}
    return files


def problems(committed: object, current: object) -> list[str]:
    committed_files = _files_map(committed)
    current_files = _files_map(current)
    lines: list[str] = []
    for relative in sorted(set(committed_files) | set(current_files)):
        if relative not in committed_files:
            lines.append(f"{relative} is missing from tools/packaged-modules.lock.json")
        elif relative not in current_files:
            lines.append(f"{relative} is unexpected in tools/packaged-modules.lock.json")
        elif committed_files[relative] != current_files[relative]:
            lines.append(f"{relative} differs from tools/packaged-modules.lock.json")
    return lines


def _source_read_errors(root: Path) -> list[str]:
    lines: list[str] = []
    for relative in expected_paths():
        try:
            (root / relative).read_bytes()
        except (OSError, UnicodeDecodeError) as exc:
            lines.append(f"{relative} could not be read ({type(exc).__name__})")
    return lines


def _load_lock(lock_path: Path, lock_relative: str) -> tuple[object | None, list[str]]:
    try:
        payload = lock_path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, [f"{lock_relative} could not be read ({type(exc).__name__})"]
    try:
        return json.loads(payload), []
    except ValueError:
        return None, [f"{lock_relative} is not valid JSON"]


def _format_issues(committed: object, lock_relative: str) -> list[str]:
    if type(committed) is not dict:
        return [f"{lock_relative} is not a JSON object"]
    version = committed.get("format_version")
    if type(version) is not int or version != 1:
        return [f"{lock_relative} has format_version {version}, expected 1"]
    return []


def _write_lock_atomically(lock_path: Path, payload: bytes, lock_relative: str) -> int:
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=".packaged-modules.lock.",
            suffix=".tmp",
            dir=lock_path.parent,
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, lock_path)
    except Exception as exc:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        print(f"{lock_relative} could not be written ({type(exc).__name__})")
        return 1
    print("packaged modules lock written")
    return 0


def main(argv: list[str] | None = None, *, root: Path = REPO_ROOT) -> int:
    parser = argparse.ArgumentParser(
        description="Write or check the lock of sources embedded in the sealed launcher"
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--linux", action="store_true", help="write the Linux lock instead")
    parser.add_argument("--check-linux", action="store_true", help="check the Linux lock instead")
    options = parser.parse_args(argv)
    if options.linux or options.check_linux:
        linux_path = root / "tools" / "packaged-modules.linux.lock.json"
        linux_relative = "tools/packaged-modules.linux.lock.json"
        try:
            current_linux = compute_linux_lock(root=root)
        except Exception as exc:
            print(f"{linux_relative} could not be read ({type(exc).__name__})")
            if options.check_linux:
                print(LINUX_DRIFT_RECIPE)
            return 1
        if options.check_linux:
            committed_linux, lock_errors = _load_lock(linux_path, linux_relative)
            if lock_errors:
                for line in lock_errors:
                    print(line)
                print(LINUX_DRIFT_RECIPE)
                return 1
            format_lines = _format_issues(committed_linux, linux_relative)
            lines = linux_problems(committed_linux, current_linux)
            if not format_lines and not lines and render(committed_linux) == render(current_linux):
                print("packaged modules linux lock ok")
                return 0
            for line in format_lines + lines:
                print(line)
            print(LINUX_DRIFT_RECIPE)
            return 1
        return _write_lock_atomically(linux_path, render(current_linux), linux_relative)
    lock_path = root / "tools" / "packaged-modules.lock.json"
    lock_relative = lock_path.relative_to(root).as_posix()
    try:
        source_errors = _source_read_errors(root)
        if options.check:
            committed, lock_errors = _load_lock(lock_path, lock_relative)
            if source_errors or lock_errors:
                for line in source_errors + lock_errors:
                    print(line)
                print(DRIFT_RECIPE)
                return 1
            format_lines = _format_issues(committed, lock_relative)
            current = compute_lock(root=root)
            lines = problems(committed, current)
            if not format_lines and not lines and render(committed) == render(current):
                print("packaged modules lock ok")
                return 0
            for line in format_lines + lines:
                print(line)
            print(DRIFT_RECIPE)
            return 1
        if source_errors:
            for line in source_errors:
                print(line)
            return 1
        return _write_lock_atomically(lock_path, render(compute_lock(root=root)), lock_relative)
    except Exception as exc:
        print(f"{lock_relative} could not be read ({type(exc).__name__})")
        if options.check:
            print(DRIFT_RECIPE)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
