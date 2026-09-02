"""Build and query the deterministic DayZ Knowledge Pack index.

Generation runbook::

    python -m dayz_mcp.knowledge extract --pack DIR --out FILE

The command scans curated Markdown in the Pack, writes a deterministic JSON
list to FILE, and performs no network access. Regenerate the index whenever the
installed Pack changes; query tools only read the generated JSON.
"""

from __future__ import annotations

import argparse
import difflib
import json
import msvcrt
import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from mcp.server.fastmcp.exceptions import ToolError

from . import knowledge_pack


GENERATION_COMMAND = (
    r"python -m dayz_mcp.knowledge extract --pack "
    r"%LOCALAPPDATA%\DayZ_MCP\knowledge-pack --out "
    r"%LOCALAPPDATA%\DayZ_MCP\knowledge.json"
)
KNOWLEDGE_REMEDY = "call dayz_knowledge_status, then dayz_knowledge_prepare"
KNOWLEDGE_NOT_INSTALLED = f"knowledge_not_installed: {KNOWLEDGE_REMEDY}"
KNOWLEDGE_INDEX_INVALID = f"knowledge_index_invalid: {KNOWLEDGE_REMEDY}"


class KnowledgeIndexError(ValueError):
    """Raised when an index does not satisfy the published entry contract."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"knowledge_index_invalid: {reason}")


class KnowledgePrepareConflict(RuntimeError):
    """Raised when another process owns the prepare publication gate."""


_PREPARE_TEST_HOOK: Callable[[], None] | None = None

_TARGET_BUILD_RE = re.compile(
    # Split so the "build:" token and the "\s" escape never share a source
    # line: together they form the substring "d:\s", which trips the
    # no-literal-drive-letters package scanner.
    r"^Target stable build:"
    r"\s+\*\*DayZ PC\s+([0-9]+(?:\.[0-9]+){3})\*\*",
    re.MULTILINE,
)
_EVIDENCE_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/])?"
    r"[A-Za-z0-9_.$@+~()#-]+"
    r"(?:[\\/][A-Za-z0-9_.$@+~()#-]+)*"
    r"\.[A-Za-z][A-Za-z0-9_]*)\s*:\s*(?P<line>[0-9]+)"
)
_INLINE_CODE_RE = re.compile(r"(?<!\x60)\x60([^\x60\n]+)\x60(?!\x60)")
_CALL_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\(\))?"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\(\))?)*)\s*\("
)
_DECLARATION_RE = re.compile(
    r"^\s*(?P<signature>"
    r"(?:(?:proto|native|static|override|private|protected|ref|const|autoptr|typename)\s+)*"
    r"[A-Za-z_][A-Za-z0-9_<>,\[\]]*\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\))"
)
_PLAIN_SYMBOL_RE = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*"
)
_MODULE_RE = re.compile(
    r"(?:^|/)([1-5]_(?:core|gamelib|game|world|mission))(?:/|$)",
    re.IGNORECASE,
)
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_CALL_KEYWORDS = frozenset({"if", "for", "while", "switch", "return", "sizeof"})
_PLAIN_KEYWORDS = frozenset(
    {
        "bool",
        "false",
        "float",
        "int",
        "null",
        "string",
        "true",
        "vector",
        "void",
    }
)
_WEB_EXTENSIONS = frozenset({"com", "info", "io", "net", "org"})
_ARTIFACT_EXTENSIONS = frozenset(
    {
        "anm",
        "asi",
        "ast",
        "c",
        "cfg",
        "cpp",
        "csv",
        "h",
        "hpp",
        "json",
        "layout",
        "md",
        "p3d",
        "paa",
        "ps1",
        "py",
        "rvmat",
        "toml",
        "txa",
        "xml",
    }
)


def _pack_markdown_files(pack_dir: Path) -> list[Path]:
    files = {
        *pack_dir.glob("skills/*/SKILL.md"),
        *pack_dir.glob("skills/*/references/*.md"),
        *pack_dir.glob("knowledge/*.md"),
    }
    return sorted(
        (path for path in files if path.is_file()),
        key=lambda path: path.relative_to(pack_dir).as_posix().casefold(),
    )


def _target_build(pack_dir: Path) -> str | None:
    matrix = pack_dir / "compatibility-matrix.md"
    if not matrix.is_file():
        return None
    matches = _TARGET_BUILD_RE.findall(matrix.read_text(encoding="utf-8-sig"))
    return matches[0] if len(matches) == 1 else None


def _markdown_units(text: str) -> Iterator[str]:
    paragraph: list[str] = []
    in_fence = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("\x60\x60\x60"):
            if paragraph:
                yield "\n".join(paragraph)
                paragraph = []
            in_fence = not in_fence
            continue
        if in_fence:
            if stripped:
                yield line
            continue
        if not stripped:
            if paragraph:
                yield "\n".join(paragraph)
                paragraph = []
            continue
        if stripped.startswith("|") or stripped.startswith("#"):
            if paragraph:
                yield "\n".join(paragraph)
                paragraph = []
            yield line
            continue
        if _LIST_ITEM_RE.match(line):
            if paragraph:
                yield "\n".join(paragraph)
            paragraph = [line]
            continue
        paragraph.append(line)

    if paragraph:
        yield "\n".join(paragraph)


def _evidence_matches(unit: str) -> list[tuple[dict[str, Any], int, int]]:
    matches: list[tuple[dict[str, Any], int, int]] = []
    for match in _EVIDENCE_RE.finditer(unit):
        path = match.group("path").replace("\\", "/")
        extension = path.rsplit(".", 1)[-1].casefold()
        if extension in _WEB_EXTENSIONS:
            continue
        matches.append(
            (
                {"path": path, "line": int(match.group("line"))},
                match.start(),
                match.end(),
            )
        )
    return matches


def _candidate_symbol(raw: str) -> tuple[str, str | None] | None:
    value = raw.strip().strip("*").strip()
    if not value or _EVIDENCE_RE.search(value):
        return None

    for match in _CALL_RE.finditer(value):
        name = match.group("name").replace("()", "")
        if name.rsplit(".", 1)[-1].casefold() not in _CALL_KEYWORDS:
            return name, value.rstrip(";")

    is_array = value.endswith("[]")
    symbol = value[:-2] if is_array else value
    if "=" in symbol:
        symbol = symbol.split("=", 1)[0].strip()
    if not _PLAIN_SYMBOL_RE.fullmatch(symbol):
        return None
    if symbol.startswith("_"):
        return None
    if "." in symbol and symbol.rsplit(".", 1)[-1].casefold() in _ARTIFACT_EXTENSIONS:
        return None
    if symbol.casefold() in _PLAIN_KEYWORDS:
        return None
    looks_named = (
        is_array
        or "." in symbol
        or "_" in symbol
        or symbol[0].isupper()
        or symbol.isupper()
    )
    return (symbol, None) if looks_named else None


def _unit_candidates(unit: str) -> list[tuple[str, str | None, int, int]]:
    candidates: list[tuple[str, str | None, int, int]] = []
    seen: set[tuple[str, str | None, int]] = set()

    for match in _INLINE_CODE_RE.finditer(unit):
        parsed = _candidate_symbol(match.group(1))
        if parsed is None:
            continue
        name, signature = parsed
        key = (name, signature, match.start())
        if key not in seen:
            seen.add(key)
            candidates.append((name, signature, match.start(), match.end()))

    offset = 0
    for line in unit.splitlines(keepends=True):
        declaration = _DECLARATION_RE.match(line)
        if declaration is not None:
            signature = declaration.group("signature").strip()
            name = declaration.group("name")
            start = offset + declaration.start("signature")
            key = (name, signature, start)
            if key not in seen:
                seen.add(key)
                candidates.append(
                    (name, signature, start, offset + declaration.end("signature"))
                )
        offset += len(line)

    return candidates


def _distance(left_start: int, left_end: int, right_start: int, right_end: int) -> int:
    if left_end <= right_start:
        return right_start - left_end
    if right_end <= left_start:
        return left_start - right_end
    return 0


def _module_from_evidence(evidence: list[dict[str, Any]]) -> str | None:
    for item in evidence:
        match = _MODULE_RE.search(item["path"])
        if match is not None:
            return match.group(1).casefold()
    return None


def _entries_from_markdown(
    text: str,
    source_file: str,
    version_verified: str | None,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for unit in _markdown_units(text):
        evidence_matches = _evidence_matches(unit)
        if not evidence_matches:
            continue
        for name, signature, start, end in _unit_candidates(unit):
            closest = min(
                evidence_matches,
                key=lambda item: _distance(start, end, item[1], item[2]),
            )
            evidence = [closest[0]]
            entries.append(
                {
                    "name": name,
                    "signature": signature,
                    "module": _module_from_evidence(evidence),
                    "evidence": evidence,
                    "gotchas": [],
                    "source_file": source_file,
                    "version_verified": version_verified,
                }
            )
    return entries


def extract_pack(pack_dir: str | Path) -> list[dict[str, Any]]:
    """Extract conservatively associated API/symbol citations from a Pack."""
    root = Path(pack_dir)
    version_verified = _target_build(root)
    merged: dict[tuple[str, str], dict[str, Any]] = {}

    for markdown in _pack_markdown_files(root):
        source_file = markdown.relative_to(root).as_posix()
        extracted = _entries_from_markdown(
            markdown.read_text(encoding="utf-8-sig"),
            source_file,
            version_verified,
        )
        for entry in extracted:
            key = (entry["name"], source_file)
            current = merged.get(key)
            if current is None:
                merged[key] = entry
                continue
            if current["signature"] is None and entry["signature"] is not None:
                current["signature"] = entry["signature"]
            known = {(item["path"], item["line"]) for item in current["evidence"]}
            for evidence in entry["evidence"]:
                identity = (evidence["path"], evidence["line"])
                if identity not in known:
                    current["evidence"].append(evidence)
                    known.add(identity)
            current["evidence"].sort(
                key=lambda item: (item["path"].casefold(), item["line"])
            )
            if current["module"] is None:
                current["module"] = _module_from_evidence(current["evidence"])

    return sorted(
        merged.values(),
        key=lambda entry: (
            entry["name"].casefold(),
            entry["name"],
            entry["source_file"].casefold(),
            entry["source_file"],
        ),
    )


def validate_index(payload: object) -> list[dict[str, Any]]:
    """Validate the complete persisted index shape before it is consumed."""
    if not isinstance(payload, list) or not payload:
        raise KnowledgeIndexError("root must be a non-empty list")
    for entry in payload:
        if not isinstance(entry, dict):
            raise KnowledgeIndexError("entry must be an object")
        for field in ("name", "source_file"):
            value = entry.get(field)
            if not isinstance(value, str) or not value:
                raise KnowledgeIndexError(f"{field} must be a non-empty string")
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise KnowledgeIndexError("evidence must be a non-empty list")
        for item in evidence:
            if not isinstance(item, dict):
                raise KnowledgeIndexError("evidence item must be an object")
            path = item.get("path")
            line = item.get("line")
            if not isinstance(path, str) or not path:
                raise KnowledgeIndexError("evidence path must be a non-empty string")
            if isinstance(line, bool) or not isinstance(line, int) or line <= 0:
                raise KnowledgeIndexError("evidence line must be a positive integer")
    return payload


def load_index(path: str | Path) -> list[dict[str, Any]]:
    """Load a generated JSON list without modifying it."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise KnowledgeIndexError("JSON is invalid") from error
    return validate_index(payload)


def find(index: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Return up to twenty stable substring matches from an index."""
    needle = query.casefold()
    hits: list[dict[str, Any]] = []
    for entry in index:
        searchable = (
            entry.get("name"),
            entry.get("module"),
            entry.get("signature"),
        )
        if any(
            needle in value.casefold()
            for value in searchable
            if isinstance(value, str)
        ):
            hits.append(entry)
            if len(hits) == 20:
                break
    return hits


def show(index: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """Return the first stable exact-name entry or a typed unknown_api error."""
    for entry in index:
        if entry.get("name") == name:
            return entry

    names = list(
        dict.fromkeys(
            entry["name"]
            for entry in index
            if isinstance(entry.get("name"), str)
        )
    )
    suggestions = difflib.get_close_matches(name, names, n=3, cutoff=0.0)
    suggestion_text = ", ".join(suggestions) if suggestions else "(none)"
    raise ToolError(
        f"unknown_api: name {name!r} is not in the knowledge index; "
        f"suggestions: {suggestion_text}"
    )


def _default_knowledge_json() -> Path:
    override = os.environ.get("DAYZ_MCP_KNOWLEDGE_JSON")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "DayZ_MCP" / "knowledge.json"


def _regular_lock_identity(path: Path, descriptor: int) -> tuple[int, int]:
    opened = os.fstat(descriptor)
    named = os.lstat(path)
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or _is_reparse_stat(opened)
        or _is_reparse_stat(named)
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise KnowledgePrepareConflict("knowledge_prepare_conflict")
    return opened.st_dev, opened.st_ino


def _is_reparse_stat(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _assert_regular_path(path: Path) -> os.stat_result:
    try:
        value = path.lstat()
    except OSError as error:
        raise KnowledgePrepareConflict("knowledge_prepare_conflict") from error
    if not stat.S_ISREG(value.st_mode) or _is_reparse_stat(value):
        raise KnowledgePrepareConflict("knowledge_prepare_conflict")
    return value


def _assert_no_name_surrogates(path: Path) -> None:
    absolute = Path(os.path.abspath(str(Path(path))))
    parts = absolute.parts
    if not parts:
        raise KnowledgePrepareConflict("knowledge_prepare_conflict")
    current = Path(parts[0])
    for part in parts[1:]:
        current /= part
        try:
            value = os.stat(current, follow_symlinks=False)
        except OSError as error:
            raise KnowledgePrepareConflict("knowledge_prepare_conflict") from error
        if int(getattr(value, "st_reparse_tag", 0) or 0) & 0x20000000:
            raise KnowledgePrepareConflict("knowledge_prepare_conflict")


@contextmanager
def _exclusive_prepare_lock(lock_path: Path) -> Iterator[None]:
    """Hold a non-blocking OS lock on a regular sibling anchor."""
    _assert_no_name_surrogates(lock_path.parent)
    if os.path.lexists(lock_path):
        _assert_regular_path(lock_path)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor: int | None = None
    acquired = False
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        _regular_lock_identity(lock_path, descriptor)
        if os.fstat(descriptor).st_size == 0:
            if os.write(descriptor, b"\0") != 1:
                raise OSError("short_lock_write")
            os.fsync(descriptor)
            if os.fstat(descriptor).st_size < 1:
                raise OSError("short_lock_write")
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        acquired = True
        _regular_lock_identity(lock_path, descriptor)
    except KnowledgePrepareConflict:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except (OSError, ValueError) as error:
        if descriptor is not None:
            os.close(descriptor)
        raise KnowledgePrepareConflict("knowledge_prepare_conflict") from error
    try:
        yield
    finally:
        try:
            if acquired:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)


def _index_state(path: Path) -> tuple[str, str]:
    try:
        load_index(path)
    except FileNotFoundError:
        return "missing", "index_missing"
    except (OSError, KnowledgeIndexError, TypeError, ValueError):
        return "invalid", "index_invalid"
    return "valid", "valid"


def _pack_state() -> tuple[str, str, Path | None]:
    try:
        pack_path = knowledge_pack.resolve_pack_dir()
    except Exception:
        return "invalid", "pack_invalid", None
    if not pack_path.exists():
        return "missing", "pack_missing", pack_path
    if not pack_path.is_dir():
        return "invalid", "pack_invalid", pack_path
    try:
        entries = extract_pack(pack_path)
        validate_index(entries)
    except Exception:
        return "invalid", "pack_invalid", pack_path
    return "valid", "valid", pack_path


def _status(path: Path) -> dict[str, Any]:
    index_state, index_reason = _index_state(path)
    pack_state, pack_reason, pack_path = _pack_state()
    return {
        "index_state": index_state,
        "pack_state": pack_state,
        "can_query": index_state == "valid",
        "can_prepare": pack_state == "valid",
        "index_path": str(path.resolve()),
        "pack_path": str(pack_path.resolve()) if pack_path is not None else None,
        "index_reason": index_reason,
        "pack_reason": pack_reason,
    }


def _publish_index(path: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(f"{path}.prepare.lock")
    candidate: Path | None = None
    with _exclusive_prepare_lock(lock_path):
        txid = uuid.uuid4().hex
        candidate = Path(f"{path}.candidate.{txid}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        try:
            descriptor = os.open(candidate, flags, 0o600)
        except FileExistsError as error:
            raise KnowledgePrepareConflict("knowledge_prepare_conflict") from error
        try:
            serialized = (json.dumps(entries, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                descriptor = -1
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
            validate_index(json.loads(candidate.read_text(encoding="utf-8")))
            if _PREPARE_TEST_HOOK is not None:
                _PREPARE_TEST_HOOK()
            os.replace(candidate, path)
            candidate = None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if candidate is not None:
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
    return {"status": "published", "index_path": str(path), "txid": txid}


def register_knowledge_tools(
    app: Any,
    index_path: str | Path | None = None,
) -> None:
    """Register two read-only local Knowledge Pack query tools."""
    path = Path(index_path) if index_path is not None else _default_knowledge_json()

    def read_installed_index() -> list[dict[str, Any]]:
        try:
            return load_index(path)
        except FileNotFoundError:
            raise ToolError(KNOWLEDGE_NOT_INSTALLED) from None
        except (OSError, KnowledgeIndexError, TypeError, ValueError):
            raise ToolError(KNOWLEDGE_INDEX_INVALID) from None

    @app.tool(
        description=(
            "Search the local DayZ Knowledge Pack index by case-insensitive substring. "
            "Returns up to 20 entries with source_file and evidence citations. "
            f"If the index is missing, {KNOWLEDGE_REMEDY}."
        )
    )
    def dayz_knowledge_find(query: str) -> list[dict[str, Any]]:
        return find(read_installed_index(), query)

    @app.tool(
        description=(
            "Look up one exact API or symbol name in the local DayZ Knowledge Pack index. "
            "Returns its signature, module, source_file, evidence, gotchas, and verified version. "
            f"If the index is missing, {KNOWLEDGE_REMEDY}."
        )
    )
    def dayz_knowledge_show(name: str) -> dict[str, Any]:
        return show(read_installed_index(), name)

    @app.tool(
        description=(
            "Report independent local Knowledge Pack index and installed-pack states. "
            "This operation is read-only."
        )
    )
    def dayz_knowledge_status() -> dict[str, Any]:
        return _status(path)

    @app.tool(
        description=(
            "Prepare the local Knowledge Pack index from the already installed pack. "
            "This operation never downloads or updates the pack."
        )
    )
    def dayz_knowledge_prepare() -> dict[str, Any]:
        try:
            pack_path = knowledge_pack.resolve_pack_dir()
        except Exception:
            raise ToolError("knowledge_pack_invalid") from None
        if not pack_path.exists():
            raise ToolError("knowledge_pack_missing")
        if not pack_path.is_dir():
            raise ToolError("knowledge_pack_invalid")
        try:
            entries = extract_pack(pack_path)
            validate_index(entries)
        except Exception:
            raise ToolError("knowledge_pack_invalid") from None
        try:
            result = _publish_index(path, entries)
        except KnowledgePrepareConflict:
            raise ToolError("knowledge_prepare_conflict") from None
        except Exception:
            raise ToolError("knowledge_prepare_failed") from None
        result.update(
            {
                "pack_path": str(pack_path.resolve()),
                "index_state": "valid",
                "pack_state": "valid",
                "can_query": True,
                "can_prepare": True,
            }
        )
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    extract_parser = subparsers.add_parser(
        "extract",
        help="extract the curated Knowledge Pack Markdown to deterministic JSON",
    )
    extract_parser.add_argument("--pack", required=True, type=Path)
    extract_parser.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    entries = extract_pack(args.pack)
    serialized = json.dumps(entries, ensure_ascii=False, indent=2) + "\n"
    with args.out.open("w", encoding="utf-8", newline="\n") as output:
        output.write(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
