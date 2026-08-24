"""Stage deterministic GitHub Release assets from a prebuilt DayZ_MCP PBO."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
PROJECT_FILE = TOOLS_DIR / "pyproject.toml"
BRIDGE_FILE = REPO_ROOT / "addon" / "scripts" / "5_Mission" / "MCPMessages.c"

PBO_ASSET_NAME = "DayZ_MCP.pbo"
PBO_ARCHIVE_PATH = f"@DayZ_MCP/Addons/{PBO_ASSET_NAME}"
VERSION_ASSET_NAME = "VERSION.json"
CHECKSUM_ASSET_NAME = "SHA256SUMS.txt"

_PROJECT_VERSION_LINE = re.compile(r'^version = "([^"\r\n]+)"$', re.MULTILINE)
_BRIDGE_VERSION_LINE = re.compile(
    r'^const string MCP_BRIDGE_VERSION = "([^"\r\n]+)";$',
    re.MULTILINE,
)

GitQuery = Callable[[Path], str]
UtcClock = Callable[[], str]


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


def stage_release(
    *,
    pbo_path: Path,
    out_dir: Path,
    allow_dirty: bool = False,
    repo_root: Path = REPO_ROOT,
    git_status_fn: GitQuery = read_git_status,
    git_sha_fn: GitQuery = read_git_sha,
    built_utc_fn: UtcClock = current_built_utc,
) -> tuple[Path, Path, Path]:
    pbo_path = Path(pbo_path)
    out_dir = Path(out_dir)
    repo_root = Path(repo_root)
    if not pbo_path.is_file():
        raise ReleaseRefusal(
            "pbo_missing",
            f"PBO input is not a file: {pbo_path}",
            "pass --pbo with the built DayZ_MCP.pbo path",
        )
    if not allow_dirty and git_status_fn(repo_root).strip():
        raise ReleaseRefusal(
            "dirty_tree",
            f"git worktree is dirty: {repo_root}",
            "commit or stash the changes, or rerun with --allow-dirty",
        )

    version = read_project_version()
    bridge_version = read_bridge_version()
    git_sha = git_sha_fn(repo_root).strip()
    built_utc = built_utc_fn()
    pbo_bytes = pbo_path.read_bytes()
    pbo_sha256 = _sha256(pbo_bytes)

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"DayZ_MCP-v{version}-addon.zip"
    version_path = out_dir / VERSION_ASSET_NAME
    checksums_path = out_dir / CHECKSUM_ASSET_NAME

    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        info, data = _zip_entry(PBO_ARCHIVE_PATH, pbo_bytes)
        archive.writestr(
            info,
            data,
            compress_type=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        )

    version_bytes = _version_bytes(
        {
            "version": version,
            "bridge_version": bridge_version,
            "git_sha": git_sha,
            "pbo_sha256": pbo_sha256,
            "built_utc": built_utc,
        }
    )
    version_path.write_bytes(version_bytes)
    checksum_lines = (
        f"{pbo_sha256}  {PBO_ASSET_NAME}\n"
        f"{_sha256(zip_path.read_bytes())}  {zip_path.name}\n"
        f"{_sha256(version_bytes)}  {VERSION_ASSET_NAME}\n"
    )
    checksums_path.write_text(checksum_lines, encoding="ascii", newline="\n")
    return zip_path, version_path, checksums_path


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
