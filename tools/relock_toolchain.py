"""Re-pin the dependency lock's toolchain section to this machine.

The shipped ``tools/dependency-lock.json`` describes the toolchain of whoever
built the last launcher: absolute MSVC and Windows SDK paths with per-file and
per-tree digests. On any other machine those pins cannot match, so the launcher
build stops at ``locked_file_missing``. This tool discovers the local MSVC and
Windows SDK, hashes the same file and tree set with the same algorithms, and
rewrites ONLY the ``toolchains`` section of the lock. Every shipped supply-chain
pin (vendored wheel, embedded CPython URL and hash, project artifacts) is left
byte-identical.

Run it once before ``build_native_launcher.py`` on a fresh machine. Running it
on the machine that produced the current lock is a no-op and reports so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
LOCK_PATH = TOOLS_DIR / "dependency-lock.json"

_MSVC_FILES = {
    "c1": ("bin", "Hostx64", "x64", "c1.dll"),
    "c1xx": ("bin", "Hostx64", "x64", "c1xx.dll"),
    "c2": ("bin", "Hostx64", "x64", "c2.dll"),
    "cl": ("bin", "Hostx64", "x64", "cl.exe"),
    "dumpbin": ("bin", "Hostx64", "x64", "dumpbin.exe"),
    "libcmt": ("lib", "x64", "libcmt.lib"),
    "libvcruntime": ("lib", "x64", "libvcruntime.lib"),
    "link": ("bin", "Hostx64", "x64", "link.exe"),
}
_SDK_FILES = {
    "bcrypt": "bcrypt.lib",
    "buffer_overflow_u": "BufferOverflowU.lib",
    "kernel32": "kernel32.lib",
}
# The banner word is localised ("Version", "versión", ...), so match the
# version NUMBER token instead of any word around it.
_CL_BANNER = re.compile(r"(\d+\.\d+\.\d+(?:\.\d+)?)")
_VERSION_DIR = re.compile(r"\d+(?:\.\d+)+")


def _canonical_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _tree_digest(root: Path) -> str:
    # Byte-compatible with the digest the test suite verifies: files sorted by
    # lowercased posix-relative path, one "rel\0size\0sha\n" line each, with
    # the per-file sha in lowercase and the aggregate reported in uppercase.
    aggregate = hashlib.sha256()
    files = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix().lower(),
    )
    for path in files:
        relative = path.relative_to(root).as_posix().lower()
        file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        aggregate.update(f"{relative}\0{path.stat().st_size}\0{file_sha256}\n".encode("utf-8"))
    return aggregate.hexdigest().upper()


def _file_entry(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"toolchain_file_missing:{path}")
    return {"path": str(path), "sha256": _file_sha256(path), "size": path.stat().st_size}


def _program_files_roots() -> list[Path]:
    roots = []
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))
    return roots


def discover_msvc_root() -> Path:
    # vswhere is installed with any Visual Studio or Build Tools instance.
    candidates: list[Path] = []
    for root in _program_files_roots():
        vswhere = root / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        if not vswhere.is_file():
            continue
        completed = subprocess.run(
            [
                str(vswhere), "-products", "*", "-latest",
                "-requires", "Microsoft.VisualCpp.Tools.Host.x64",
                "-property", "installationPath",
            ],
            capture_output=True, text=True, timeout=60,
        )
        for line in completed.stdout.splitlines():
            line = line.strip()
            if line:
                candidates.append(Path(line) / "VC" / "Tools" / "MSVC")
    for root in _program_files_roots():
        base = root / "Microsoft Visual Studio"
        if base.is_dir():
            candidates.extend(base.glob("*/*/VC/Tools/MSVC"))
    versions: list[tuple[tuple[int, ...], Path]] = []
    for base in candidates:
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if entry.is_dir() and _VERSION_DIR.fullmatch(entry.name):
                versions.append((tuple(int(part) for part in entry.name.split(".")), entry))
    if not versions:
        raise ValueError("msvc_not_found: install the MSVC v143+ x64 build tools, or pass --msvc-root")
    return max(versions)[1]


def discover_sdk(sdk_root: Path) -> tuple[Path, str]:
    lib = sdk_root / "Lib"
    if not lib.is_dir():
        raise ValueError(f"windows_sdk_not_found:{sdk_root}: install a Windows 10/11 SDK, or pass --sdk-root")
    versions: list[tuple[tuple[int, ...], str]] = []
    for entry in lib.iterdir():
        if not entry.is_dir() or not _VERSION_DIR.fullmatch(entry.name):
            continue
        if all((entry / "um" / "x64" / name).is_file() for name in _SDK_FILES.values()):
            versions.append((tuple(int(part) for part in entry.name.split(".")), entry.name))
    if not versions:
        raise ValueError(f"windows_sdk_libs_missing:{lib}")
    return sdk_root, max(versions)[1]


def read_compiler_version(cl: Path) -> str:
    completed = subprocess.run(
        [str(cl)],
        capture_output=True, timeout=60, encoding="utf-8", errors="replace",
        env={"SystemRoot": os.environ["SystemRoot"], "PATH": str(cl.parent)},
    )
    match = _CL_BANNER.search(completed.stderr or "") or _CL_BANNER.search(completed.stdout or "")
    if match is None:
        raise ValueError("compiler_version_unreadable: pass --compiler-version explicitly")
    return match.group(1)


def collect_toolchains(
    msvc_root: Path,
    compiler_version: str,
    sdk_root: Path,
    sdk_version: str,
    *,
    progress: bool = False,
) -> dict[str, object]:
    def note(label: str) -> None:
        if progress:
            print(f"  hashing {label} ...", flush=True)

    msvc_files = {}
    for name, parts in _MSVC_FILES.items():
        msvc_files[name] = _file_entry(msvc_root.joinpath(*parts))
    msvc_trees = {}
    for name, sub in (
        ("bin_hostx64_x64", msvc_root / "bin" / "Hostx64" / "x64"),
        ("include", msvc_root / "include"),
        ("lib", msvc_root / "lib"),
    ):
        note(str(sub))
        msvc_trees[name] = {"path": str(sub), "sha256": _tree_digest(sub)}

    sdk_files = {}
    for name, filename in _SDK_FILES.items():
        sdk_files[name] = _file_entry(sdk_root / "Lib" / sdk_version / "um" / "x64" / filename)
    sdk_trees = {}
    for name, sub in (
        ("include", sdk_root / "Include" / sdk_version),
        ("lib", sdk_root / "Lib" / sdk_version),
    ):
        note(str(sub))
        sdk_trees[name] = {"path": str(sub), "sha256": _tree_digest(sub)}

    return {
        "msvc": {
            "compiler_version": compiler_version,
            "files": msvc_files,
            "root_version": msvc_root.name,
            "trees": msvc_trees,
        },
        "windows_sdk": {
            "files": sdk_files,
            "trees": sdk_trees,
            "version": sdk_version,
        },
    }


def rewrite_lock(lock_path: Path, toolchains: dict[str, object]) -> tuple[str, str]:
    raw = lock_path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if (
        type(payload) is not dict
        or set(payload) != {"artifacts", "format_version", "remote_artifacts", "toolchains"}
        or payload["format_version"] != 1
    ):
        raise ValueError("dependency_lock_invalid")
    before = payload["artifacts"], payload["remote_artifacts"]
    payload["toolchains"] = toolchains
    updated = _canonical_bytes(payload)
    lock_path.write_bytes(updated)
    back = json.loads(lock_path.read_bytes().decode("utf-8"))
    if (back["artifacts"], back["remote_artifacts"]) != before or back["toolchains"] != toolchains:
        raise ValueError("dependency_lock_rewrite_verification_failed")
    return (
        hashlib.sha256(raw).hexdigest().upper(),
        hashlib.sha256(updated).hexdigest().upper(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-pin dependency-lock.json's toolchains section to this machine"
    )
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    parser.add_argument("--msvc-root", type=Path, default=None)
    parser.add_argument("--compiler-version", default=None)
    parser.add_argument("--sdk-root", type=Path, default=None)
    parser.add_argument("--sdk-version", default=None)
    parser.add_argument("--dry-run", action="store_true")
    options = parser.parse_args(argv)

    msvc_root = options.msvc_root or discover_msvc_root()
    sdk_root = options.sdk_root
    if sdk_root is None:
        for root in _program_files_roots():
            if (root / "Windows Kits" / "10" / "Lib").is_dir():
                sdk_root = root / "Windows Kits" / "10"
                break
        else:
            raise ValueError("windows_sdk_not_found: pass --sdk-root")
    if options.sdk_version is None:
        sdk_root, sdk_version = discover_sdk(sdk_root)
    else:
        sdk_version = options.sdk_version
    compiler_version = options.compiler_version or read_compiler_version(
        msvc_root / "bin" / "Hostx64" / "x64" / "cl.exe"
    )

    print(f"msvc : {msvc_root}  (cl {compiler_version})")
    print(f"sdk  : {sdk_root}  ({sdk_version})")
    toolchains = collect_toolchains(
        msvc_root, compiler_version, sdk_root, sdk_version, progress=True
    )

    current = json.loads(options.lock.read_bytes().decode("utf-8"))
    if current.get("toolchains") == toolchains:
        print("lock : toolchains already match this machine; nothing to do")
        return 0
    if options.dry_run:
        print("lock : would rewrite the toolchains section (--dry-run)")
        return 0
    old_sha, new_sha = rewrite_lock(options.lock, toolchains)
    print(f"lock : {old_sha[:16]}... -> {new_sha[:16]}...  ({options.lock})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
