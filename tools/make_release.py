"""Stage deterministic GitHub Release assets from a prebuilt DayZ_MCP PBO.

The PBO must carry the build marker tools/pack-addon.ps1 writes (mcp_build.json),
and the commit it names must be the git_sha that VERSION.json records; anything
else is refused (fb-20260819-024951-e307). tools/dev/pbo_provenance.py owns the
PBO layout and the marker contract.

pip builds the wheel on every pass and it does not come out byte for byte the
same, so the wheel line in the requirements changes from one pass to another.
With the same inputs, the other files come out the same.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import build_wheel
from dayz_mcp.dayz_tools_paths import tools_root_candidates


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
PROJECT_FILE = TOOLS_DIR / "pyproject.toml"
BRIDGE_FILE = REPO_ROOT / "addon" / "scripts" / "5_Mission" / "MCPMessages.c"
PROVENANCE_TOOL = TOOLS_DIR / "dev" / "pbo_provenance.py"

PBO_ASSET_NAME = "DayZ_MCP.pbo"
BISIGN_ASSET_NAME = "DayZ_MCP.pbo.DayZ_MCP.bisign"
BIKEY_ASSET_NAME = "DayZ_MCP.bikey"
MOD_CPP_ASSET_NAME = "mod.cpp"
VERSION_ASSET_NAME = "VERSION.json"
CHECKSUM_ASSET_NAME = "SHA256SUMS.txt"
REQUIREMENTS_LOCK_NAME = "requirements.lock.txt"
REQUIREMENTS_ASSET_NAME = "dayz-mcp-requirements.txt"
WHEEL_PROJECT = "dayz-mcp-tools"
RELEASE_DOWNLOAD_URL = "https://github.com/willy92wins/dayz-mcp/releases/download"
RELEASE_DIR = TOOLS_DIR / "release"
DSCHECK_EXE_RELATIVE = Path("Bin") / "DsUtils" / "DSCheckSignatures.exe"

ZIP_ENTRY_PATHS = (
    "@DayZ_MCP/Addons/DayZ_MCP.pbo",
    "@DayZ_MCP/Addons/DayZ_MCP.pbo.DayZ_MCP.bisign",
    "@DayZ_MCP/Keys/DayZ_MCP.bikey",
    "@DayZ_MCP/mod.cpp",
    "@DayZ_MCP/VERSION.json",
)
PBO_ARCHIVE_PATH = ZIP_ENTRY_PATHS[0]
BISIGN_ARCHIVE_PATH = ZIP_ENTRY_PATHS[1]
BIKEY_ARCHIVE_PATH = ZIP_ENTRY_PATHS[2]
MOD_CPP_ARCHIVE_PATH = ZIP_ENTRY_PATHS[3]
VERSION_ARCHIVE_PATH = ZIP_ENTRY_PATHS[4]

SHA256SUMS_ENTRY_NAMES = (
    "DayZ_MCP.pbo",
    "DayZ_MCP.pbo.DayZ_MCP.bisign",
    "DayZ_MCP.bikey",
    "mod.cpp",
    "VERSION.json",
)

_PROJECT_VERSION_LINE = re.compile(r'^version = "([^"\r\n]+)"$', re.MULTILINE)
_BRIDGE_VERSION_LINE = re.compile(
    r'^const string MCP_BRIDGE_VERSION = "([^"\r\n]+)";$',
    re.MULTILINE,
)

GitQuery = Callable[[Path], str]
UtcClock = Callable[[], str]
SignatureCheck = Callable[[Path, Path], str]
ProcessRunner = Callable[..., subprocess.CompletedProcess]


class ReleaseRefusal(ValueError):
    """A named, actionable refusal safe to print from the CLI."""

    def __init__(self, code: str, detail: str, remedy: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}; remedy: {remedy}")


def _read_exact_match(
    path: Path,
    pattern: re.Pattern[str],
    *,
    code: str,
    expected_line: str,
) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReleaseRefusal(
            code,
            f"cannot read {path}: {error}",
            f"restore {path} with exactly one `{expected_line}` line",
        ) from error
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise ReleaseRefusal(
            code,
            f"found {len(matches)} exact matches in {path}, expected exactly one",
            f"restore exactly one `{expected_line}` line",
        )
    return matches[0]


def read_project_version(path: Path = PROJECT_FILE) -> str:
    return _read_exact_match(
        path,
        _PROJECT_VERSION_LINE,
        code="version_parse_failed",
        expected_line='version = "X.Y.Z"',
    )


def read_bridge_version(path: Path = BRIDGE_FILE) -> str:
    return _read_exact_match(
        path,
        _BRIDGE_VERSION_LINE,
        code="bridge_version_parse_failed",
        expected_line='const string MCP_BRIDGE_VERSION = "N";',
    )


def _run_git(repo_root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseRefusal(
            "git_query_failed",
            f"git {' '.join(arguments)} failed for {repo_root}: {error}",
            "run from a complete git checkout with git available on PATH",
        ) from error
    return completed.stdout.strip()


def read_git_status(repo_root: Path) -> str:
    return _run_git(repo_root, "status", "--porcelain")


def read_git_sha(repo_root: Path) -> str:
    return _run_git(repo_root, "rev-parse", "HEAD")


def _provenance_tool():
    if not PROVENANCE_TOOL.is_file():
        raise ReleaseRefusal(
            "provenance_tool_missing",
            f"{PROVENANCE_TOOL} is missing",
            "run from a complete checkout of this repository",
        )
    spec = importlib.util.spec_from_file_location("pbo_provenance", PROVENANCE_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_build_commit(pbo_bytes: bytes) -> str | None:
    """The commit the PBO's mcp_build.json names (None for a -Source build)."""
    provenance = _provenance_tool()
    rebuild = "rebuild the PBO from HEAD with tools/pack-addon.ps1 (docs/RELEASE.md step 2)"
    try:
        _properties, entries = provenance.read_pbo(pbo_bytes)
    except provenance.PboFormatError as error:
        raise ReleaseRefusal(
            "pbo_unreadable",
            f"the --pbo input is not a PBO: {error}",
            "pass the DayZ_MCP.pbo path that tools/pack-addon.ps1 printed",
        ) from error
    markers = [data for name, data in entries if name == provenance.MARKER_NAME]
    if len(markers) != 1:
        raise ReleaseRefusal(
            "pbo_marker_missing",
            f"the PBO carries {len(markers)} {provenance.MARKER_NAME} entries, expected exactly one",
            rebuild,
        )
    try:
        marker = provenance.parse_marker(markers[0])
    except provenance.MarkerError as error:
        raise ReleaseRefusal(
            "pbo_marker_invalid",
            f"{provenance.MARKER_NAME} is malformed: {error}",
            rebuild,
        ) from error
    return marker["commit"]


def current_built_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_entry(name: str, data: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info, data


def _version_bytes(payload: dict[str, str]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def build_release_wheel(version_bytes: bytes, work_dir: Path) -> Path:
    stage = build_wheel.stage_wheel_tree(
        TOOLS_DIR, REPO_ROOT / "playbooks", work_dir / "stage", version_bytes
    )
    return build_wheel.build_wheel(stage, work_dir / "wheelhouse")


def _bisign_path_for(pbo_path: Path) -> Path:
    return Path(f"{pbo_path}.DayZ_MCP.bisign")


def _find_private_key(repo_root: Path, pbo_path: Path, out_dir: Path) -> Path | None:
    suffix = ".biprivatekey"

    def _walk(root: Path) -> Path | None:
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                if filename.casefold().endswith(suffix):
                    return Path(dirpath) / filename
        return None

    if repo_root.is_dir():
        found = _walk(repo_root)
        if found is not None:
            return found
    pbo_dir = pbo_path.parent
    if pbo_dir.is_dir():
        for entry in pbo_dir.iterdir():
            if entry.is_file() and entry.name.casefold().endswith(suffix):
                return entry
    if out_dir.is_dir():
        return _walk(out_dir)
    return None


def _missing_or_empty(path: Path) -> bool:
    try:
        return not path.is_file() or path.stat().st_size == 0
    except OSError:
        return True


def run_signature_check(
    addons_dir: Path,
    keys_dir: Path,
    *,
    tools_roots: list[Path] | tuple[Path, ...] | None = None,
    run_fn: ProcessRunner = subprocess.run,
) -> str:
    addons_dir = Path(addons_dir).resolve()
    keys_dir = Path(keys_dir).resolve()
    roots = list(tools_root_candidates()) if tools_roots is None else list(tools_roots)
    tried: list[Path] = []
    exe_path: Path | None = None
    for root in roots:
        candidate = Path(root) / DSCHECK_EXE_RELATIVE
        tried.append(candidate)
        if candidate.is_file():
            exe_path = candidate
            break
    if exe_path is None:
        raise ReleaseRefusal(
            "signature_check_unavailable",
            "DSCheckSignatures.exe not found; tried: "
            + "; ".join(str(path) for path in tried),
            "install DayZ Tools or set DAYZ_TOOLS_PATH",
        )
    try:
        completed = run_fn(
            [str(exe_path), str(addons_dir), str(keys_dir)],
            cwd=addons_dir.parent,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseRefusal(
            "signature_check_unavailable",
            f"DSCheckSignatures.exe could not be run: {exe_path}: {error}",
            "install DayZ Tools or set DAYZ_TOOLS_PATH",
        ) from error
    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    if isinstance(stdout, str):
        stdout = stdout.encode("utf-8", errors="replace")
    if isinstance(stderr, str):
        stderr = stderr.encode("utf-8", errors="replace")
    return (stdout + stderr).decode("utf-8", errors="replace")


def _validate_signature_output(output: str) -> None:
    lines = output.splitlines()
    forbidden = ("is wrong", "key not found", "signature not found")
    ok_routes: list[str] = []
    for line in lines:
        if line.startswith("Signature ") and line.endswith(" is OK"):
            ok_routes.append(line[len("Signature ") : -len(" is OK")])
    trimmed = output.strip()[:500]
    has_forbidden = any(
        phrase in line.casefold() for line in lines for phrase in forbidden
    )
    if has_forbidden:
        raise ReleaseRefusal(
            "bisign_invalid",
            f"DSCheckSignatures reported a bad signature: {trimmed}",
            "re-sign the PBO with DSSignFile and the matching DayZ_MCP key",
        )
    if len(ok_routes) != 1:
        raise ReleaseRefusal(
            "bisign_invalid",
            f"DSCheckSignatures output has {len(ok_routes)} OK signature lines, expected exactly one: {trimmed}",
            "re-sign the PBO with DSSignFile and the matching DayZ_MCP key",
        )
    final_name = re.split(r"[\\/]", ok_routes[0])[-1]
    if final_name.casefold() != BISIGN_ASSET_NAME.casefold():
        raise ReleaseRefusal(
            "bisign_invalid",
            f"DSCheckSignatures OK line names {final_name}, expected {BISIGN_ASSET_NAME}: {trimmed}",
            "re-sign the PBO with DSSignFile and the matching DayZ_MCP key",
        )


def stage_release(
    *,
    pbo_path: Path,
    out_dir: Path,
    allow_dirty: bool = False,
    repo_root: Path = REPO_ROOT,
    release_dir: Path = RELEASE_DIR,
    git_status_fn: GitQuery = read_git_status,
    git_sha_fn: GitQuery = read_git_sha,
    built_utc_fn: UtcClock = current_built_utc,
    signature_check_fn: SignatureCheck = run_signature_check,
) -> tuple[Path, Path, Path, Path, Path]:
    pbo_path = Path(pbo_path)
    out_dir = Path(out_dir)
    repo_root = Path(repo_root)
    release_dir = Path(release_dir)
    if not pbo_path.is_file():
        raise ReleaseRefusal(
            "pbo_missing",
            f"PBO input is not a file: {pbo_path}",
            "pass --pbo with the built DayZ_MCP.pbo path",
        )
    private_key = _find_private_key(repo_root, pbo_path, out_dir)
    if private_key is not None:
        raise ReleaseRefusal(
            "private_key_present",
            f"private signing key found: {private_key}",
            "move the .biprivatekey file outside the repository, the PBO folder and --out, and keep it offline",
        )
    if not allow_dirty and git_status_fn(repo_root).strip():
        raise ReleaseRefusal(
            "dirty_tree",
            f"git worktree is dirty: {repo_root}",
            "commit or stash the changes, or rerun with --allow-dirty",
        )
    bikey_path = release_dir / BIKEY_ASSET_NAME
    if _missing_or_empty(bikey_path):
        raise ReleaseRefusal(
            "release_key_missing",
            f"release public key is missing or empty: {bikey_path}",
            "put the DayZ_MCP.bikey matching the signing key in tools/release",
        )
    mod_cpp_path = release_dir / MOD_CPP_ASSET_NAME
    if _missing_or_empty(mod_cpp_path):
        raise ReleaseRefusal(
            "mod_cpp_missing",
            f"release mod.cpp is missing or empty: {mod_cpp_path}",
            "restore tools/release/mod.cpp",
        )
    bisign_path = _bisign_path_for(pbo_path)
    if not bisign_path.is_file():
        raise ReleaseRefusal(
            "bisign_missing",
            f"PBO signature is missing: {bisign_path}",
            "sign the PBO with DSSignFile so the .bisign sits beside the PBO (docs/RELEASE.md step 2)",
        )

    version = read_project_version()
    bridge_version = read_bridge_version()
    git_sha = git_sha_fn(repo_root).strip()
    built_utc = built_utc_fn()
    pbo_bytes = pbo_path.read_bytes()
    pbo_sha256 = _sha256(pbo_bytes)
    build_commit = read_build_commit(pbo_bytes)
    if build_commit != git_sha:
        raise ReleaseRefusal(
            "pbo_commit_mismatch",
            f"the PBO was built from {build_commit or 'a folder (-Source), not a commit'}; "
            f"VERSION.json would record git_sha {git_sha}",
            "rebuild the PBO from HEAD with tools/pack-addon.ps1 (docs/RELEASE.md step 2)",
        )

    bisign_bytes = bisign_path.read_bytes()
    bikey_bytes = bikey_path.read_bytes()
    mod_cpp_bytes = mod_cpp_path.read_bytes()
    if not bisign_bytes.startswith(bikey_bytes):
        raise ReleaseRefusal(
            "bisign_wrong_key",
            f"signature {bisign_path} does not start with the release public key {bikey_path}",
            "re-sign the PBO with the private key matching tools/release/DayZ_MCP.bikey",
        )

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        check_root = Path(temporary)
        addons_dir = check_root / "addons"
        keys_dir = check_root / "keys"
        addons_dir.mkdir()
        keys_dir.mkdir()
        (addons_dir / PBO_ASSET_NAME).write_bytes(pbo_bytes)
        (addons_dir / BISIGN_ASSET_NAME).write_bytes(bisign_bytes)
        (keys_dir / BIKEY_ASSET_NAME).write_bytes(bikey_bytes)
        signature_output = signature_check_fn(addons_dir, keys_dir)
    _validate_signature_output(signature_output)

    version_bytes = _version_bytes(
        {
            "version": version,
            "bridge_version": bridge_version,
            "git_sha": git_sha,
            "pbo_sha256": pbo_sha256,
            "built_utc": built_utc,
        }
    )

    lock_path = release_dir / REQUIREMENTS_LOCK_NAME
    if _missing_or_empty(lock_path):
        raise ReleaseRefusal(
            "requirements_lock_missing",
            f"requirements lock is missing or empty: {lock_path}",
            "restore tools/release/requirements.lock.txt (docs/RELEASE.md, Updating the dependency lock)",
        )
    lock_text = lock_path.read_text(encoding="utf-8")

    try:
        with tempfile.TemporaryDirectory() as wheel_temporary:
            wheel_path_in_temp = build_release_wheel(
                version_bytes, Path(wheel_temporary)
            )
            wheel_name = wheel_path_in_temp.name
            wheel_bytes = wheel_path_in_temp.read_bytes()
    except RuntimeError as error:
        raise ReleaseRefusal(
            "wheel_build_failed",
            str(error),
            "check that pip in this Python can build tools/ (pip downloads setuptools==84.0.0 for the build), then rerun",
        ) from error

    expected_wheel_name = f"dayz_mcp_tools-{version}-py3-none-any.whl"
    if wheel_name != expected_wheel_name:
        raise ReleaseRefusal(
            "wheel_name_unexpected",
            f"wheel is named {wheel_name}, expected {expected_wheel_name}",
            "build the wheel from tools/pyproject.toml at this commit",
        )

    requirements_text = lock_text.replace("\r\n", "\n").replace("\r", "\n")
    if not requirements_text.endswith("\n"):
        requirements_text += "\n"
    requirements_text += (
        f"{WHEEL_PROJECT} @ {RELEASE_DOWNLOAD_URL}/v{version}/{wheel_name}"
        f" --hash=sha256:{_sha256(wheel_bytes)}\n"
    )
    requirements_bytes = requirements_text.encode("utf-8")

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"DayZ_MCP-v{version}-addon.zip"
    version_path = out_dir / VERSION_ASSET_NAME
    wheel_path = out_dir / wheel_name
    requirements_path = out_dir / REQUIREMENTS_ASSET_NAME
    checksums_path = out_dir / CHECKSUM_ASSET_NAME

    entry_bytes = {
        PBO_ARCHIVE_PATH: pbo_bytes,
        BISIGN_ARCHIVE_PATH: bisign_bytes,
        BIKEY_ARCHIVE_PATH: bikey_bytes,
        MOD_CPP_ARCHIVE_PATH: mod_cpp_bytes,
        VERSION_ARCHIVE_PATH: version_bytes,
    }
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for archive_path in ZIP_ENTRY_PATHS:
            info, data = _zip_entry(archive_path, entry_bytes[archive_path])
            archive.writestr(
                info,
                data,
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )

    version_path.write_bytes(version_bytes)
    wheel_path.write_bytes(wheel_bytes)
    requirements_path.write_bytes(requirements_bytes)
    content_bytes = {
        PBO_ASSET_NAME: pbo_bytes,
        BISIGN_ASSET_NAME: bisign_bytes,
        BIKEY_ASSET_NAME: bikey_bytes,
        MOD_CPP_ASSET_NAME: mod_cpp_bytes,
        VERSION_ASSET_NAME: version_bytes,
    }
    checksum_lines = ""
    for name in SHA256SUMS_ENTRY_NAMES:
        if name == VERSION_ASSET_NAME:
            checksum_lines += f"{_sha256(zip_path.read_bytes())}  {zip_path.name}\n"
        checksum_lines += f"{_sha256(content_bytes[name])}  {name}\n"
    checksum_lines += f"{_sha256(wheel_bytes)}  {wheel_name}\n"
    checksum_lines += f"{_sha256(requirements_bytes)}  {REQUIREMENTS_ASSET_NAME}\n"
    checksums_path.write_text(checksum_lines, encoding="ascii", newline="\n")
    return zip_path, version_path, wheel_path, requirements_path, checksums_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage deterministic DayZ_MCP GitHub Release assets"
    )
    parser.add_argument("--pbo", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=Path("dist"))
    parser.add_argument("--allow-dirty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    options = _parser().parse_args(argv)
    try:
        assets = stage_release(
            pbo_path=options.pbo,
            out_dir=options.out,
            allow_dirty=options.allow_dirty,
        )
    except ReleaseRefusal as error:
        print(error, file=sys.stderr)
        return 2
    except OSError as error:
        print(
            f"release_io_failed: {error}; remedy: check --pbo and --out permissions",
            file=sys.stderr,
        )
        return 2
    for asset in assets:
        print(asset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
