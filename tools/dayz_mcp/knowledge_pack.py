from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Protocol, Sequence


PACK_URL = "https://github.com/willy92wins/DayZ-Modding-Knowledge-Pack"
PACK_DIR_ENV = "DAYZ_MCP_PACK_DIR"
MANIFEST_NAME = "knowledge-pack-skills-manifest.json"
MANIFEST_KIND = "dayz-mcp-knowledge-pack-skills-v1"
GIT_MISSING_REMEDY = (
    "Install Git for Windows and ensure git.exe is available on PATH."
)
INSTALLER_REMEDY = r".\install-mcp.ps1"
_TARGET_BUILD = re.compile(
    r"^Target stable build:\s+\*\*DayZ PC ([0-9]+(?:\.[0-9]+)+)\*\*(?:\s|$)",
    re.MULTILINE,
)


class KnowledgePackError(RuntimeError):
    def __init__(self, code: str, *, remedy: str | None = None) -> None:
        self.code = code
        self.remedy = remedy
        super().__init__(code)


class CommandRunner(Protocol):
    def __call__(self, argv: Sequence[str], **kwargs: object) -> object: ...


def resolve_pack_dir() -> Path:
    override = os.environ.get(PACK_DIR_ENV)
    if override is not None:
        value = override.strip()
        if not value:
            raise KnowledgePackError("pack_dir_invalid")
        return Path(value).expanduser().resolve()
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_appdata:
        raise KnowledgePackError("localappdata_missing")
    return (Path(local_appdata) / "DayZ_MCP" / "knowledge-pack").resolve()


def default_skills_dir() -> Path:
    return (Path.home() / ".agents" / "skills").resolve()


def default_manifest_path(pack_dir: Path | None = None) -> Path:
    root = resolve_pack_dir() if pack_dir is None else Path(pack_dir).resolve()
    return (root.parent / MANIFEST_NAME).resolve()


def _run_git(arguments: list[str], runner: CommandRunner) -> None:
    try:
        completed = runner(
            arguments,
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            timeout=300.0,
        )
    except FileNotFoundError as error:
        raise KnowledgePackError(
            "git_missing", remedy=GIT_MISSING_REMEDY
        ) from error
    if getattr(completed, "returncode", None) != 0:
        operation = "clone" if arguments[1:2] == ["clone"] else "pull"
        raise KnowledgePackError(f"git_{operation}_failed")


def ensure_pack(dest: Path, runner: CommandRunner) -> Path:
    destination = Path(dest).expanduser().resolve()
    if destination.exists() and not destination.is_dir():
        raise KnowledgePackError("pack_destination_invalid")
    if destination.exists():
        _run_git(
            [
                "git",
                "-C",
                str(destination),
                "pull",
                "--ff-only",
                PACK_URL,
            ],
            runner,
        )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["git", "clone", "--", PACK_URL, str(destination)], runner)
    return destination


def _pack_skill_names(pack_dir: Path) -> list[str]:
    source_root = Path(pack_dir).resolve() / "skills"
    if not source_root.is_dir():
        raise KnowledgePackError("pack_skills_missing")
    return sorted(
        entry.name for entry in source_root.iterdir() if entry.is_dir()
    )


def _validate_entry_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or Path(value).name != value
    ):
        raise KnowledgePackError("skills_manifest_invalid")
    return value


def _load_manifest(path: Path) -> tuple[Path, set[str]] | None:
    manifest = Path(path).resolve()
    if not manifest.exists():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise KnowledgePackError("skills_manifest_invalid") from error
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {"schema_version", "kind", "skills_dir", "entries"}
        or payload.get("schema_version") != 1
        or payload.get("kind") != MANIFEST_KIND
        or not isinstance(payload.get("skills_dir"), str)
        or not isinstance(payload.get("entries"), list)
    ):
        raise KnowledgePackError("skills_manifest_invalid")
    entries = [_validate_entry_name(value) for value in payload["entries"]]
    if entries != sorted(set(entries)):
        raise KnowledgePackError("skills_manifest_invalid")
    return Path(payload["skills_dir"]).resolve(), set(entries)


def _atomic_write_manifest(path: Path, skills_dir: Path, entries: set[str]) -> None:
    manifest = Path(path).resolve()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest.with_name(manifest.name + ".tmp")
    payload = {
        "schema_version": 1,
        "kind": MANIFEST_KIND,
        "skills_dir": str(skills_dir),
        "entries": sorted(entries),
    }
    text = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, manifest)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _remove_owned_entry(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def sync_skills(pack_dir: Path, skills_dir: Path, manifest_path: Path) -> list[str]:
    pack = Path(pack_dir).resolve()
    destination_root = Path(skills_dir).resolve()
    manifest = Path(manifest_path).resolve()
    source_root = pack / "skills"
    names = _pack_skill_names(pack)
    loaded = _load_manifest(manifest)
    if loaded is None:
        owned: set[str] = set()
    else:
        recorded_root, owned = loaded
        if recorded_root != destination_root:
            raise KnowledgePackError("skills_manifest_target_mismatch")

    destination_root.mkdir(parents=True, exist_ok=True)
    current_names = set(names)
    for retired in sorted(owned - current_names):
        _remove_owned_entry(destination_root / retired)
        owned.remove(retired)

    for name in names:
        source = source_root / name
        destination = destination_root / name
        destination_exists = destination.exists() or destination.is_symlink()
        if destination_exists and name not in owned:
            continue
        if destination_exists:
            _remove_owned_entry(destination)
        shutil.copytree(source, destination)
        owned.add(name)

    _atomic_write_manifest(manifest, destination_root, owned)
    return sorted(owned)


def unsync(manifest_path: Path) -> list[str]:
    manifest = Path(manifest_path).resolve()
    loaded = _load_manifest(manifest)
    if loaded is None:
        return []
    skills_dir, owned = loaded
    for name in sorted(owned):
        _remove_owned_entry(skills_dir / name)
    manifest.unlink()
    return sorted(owned)


def target_game_build(pack_dir: Path) -> str | None:
    compatibility = Path(pack_dir).resolve() / "compatibility-matrix.md"
    try:
        text = compatibility.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    matches = _TARGET_BUILD.findall(text)
    return matches[0] if len(matches) == 1 else None


def install_knowledge_pack(
    *,
    sync: bool,
    runner: CommandRunner = subprocess.run,
    pack_dir: Path | None = None,
    skills_dir: Path | None = None,
    manifest_path: Path | None = None,
    python_executable: Path | None = None,
) -> dict[str, object]:
    pack = resolve_pack_dir() if pack_dir is None else Path(pack_dir).resolve()
    skills = default_skills_dir() if skills_dir is None else Path(skills_dir).resolve()
    manifest = (
        default_manifest_path(pack)
        if manifest_path is None
        else Path(manifest_path).resolve()
    )
    python = Path(sys.executable) if python_executable is None else Path(python_executable)
    ready_pack = ensure_pack(pack, runner)
    available = _pack_skill_names(ready_pack)
    registered = sync_skills(ready_pack, skills, manifest) if sync else []
    return {
        "status": "ready",
        "pack_dir": str(ready_pack),
        "skills_dir": str(skills),
        "manifest_path": str(manifest),
        "skills_registration": "registered" if sync else "print_only",
        "skills_registered": registered,
        "skills_pending": [] if sync else available,
        "skills_skipped_unowned": (
            sorted(set(available) - set(registered)) if sync else []
        ),
        "undo": (
            f'"{python}" -m dayz_mcp.knowledge_pack unsync '
            f'--manifest-path "{manifest}"'
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install the DayZ Knowledge Pack")
    commands = parser.add_subparsers(dest="operation", required=True)
    install = commands.add_parser("install")
    install.add_argument("--sync", action="store_true")
    remove = commands.add_parser("unsync")
    remove.add_argument("--manifest-path", default="")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: CommandRunner = subprocess.run,
) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.operation == "install":
            payload = install_knowledge_pack(sync=args.sync, runner=runner)
        else:
            manifest = (
                Path(args.manifest_path).resolve()
                if args.manifest_path
                else default_manifest_path()
            )
            payload = {
                "status": "unregistered",
                "manifest_path": str(manifest),
                "skills_removed": unsync(manifest),
            }
    except (KnowledgePackError, OSError, ValueError) as error:
        code = getattr(error, "code", "knowledge_pack_failed")
        payload = {"status": "error", "error": code}
        remedy = getattr(error, "remedy", None)
        if isinstance(remedy, str) and remedy:
            payload["remedy"] = remedy
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
