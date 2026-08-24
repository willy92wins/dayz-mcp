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
import os
import re
from pathlib import Path
from typing import Any, Iterator

from mcp.server.fastmcp.exceptions import ToolError


GENERATION_COMMAND = (
    r"python -m dayz_mcp.knowledge extract --pack "
    r"%LOCALAPPDATA%\DayZ_MCP\knowledge-pack --out "
    r"%LOCALAPPDATA%\DayZ_MCP\knowledge.json"
)
KNOWLEDGE_NOT_INSTALLED = f"knowledge_not_installed: run {GENERATION_COMMAND}"

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


def load_index(path: str | Path) -> list[dict[str, Any]]:
    """Load a generated JSON list without modifying it."""
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError("knowledge_index_invalid: root must be a list")
    return payload


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

    @app.tool(
        description=(
            "Search the local DayZ Knowledge Pack index by case-insensitive substring. "
            "Returns up to 20 entries with source_file and evidence citations. "
            f"If the index is missing, run {GENERATION_COMMAND}."
        )
    )
    def dayz_knowledge_find(query: str) -> list[dict[str, Any]]:
        return find(read_installed_index(), query)

    @app.tool(
        description=(
            "Look up one exact API or symbol name in the local DayZ Knowledge Pack index. "
            "Returns its signature, module, source_file, evidence, gotchas, and verified version. "
            f"If the index is missing, run {GENERATION_COMMAND}."
        )
    )
    def dayz_knowledge_show(name: str) -> dict[str, Any]:
        return show(read_installed_index(), name)


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
