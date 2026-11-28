"""SEP-2640 Agent Skills catalog: strict loader, digests and legacy fallback.

Stdlib only at import time; FastMCP is imported only inside ``install()``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

EXTENSION_ID = "io.modelcontextprotocol/skills"
APP_CATALOG_ATTR = "_dayz_mcp_skills_catalog"

_SKILL_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9]))*$")
_FRONTMATTER_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ALLOWED_MIME: dict[str, str] = {
    ".md": "text/markdown",
    ".txt": "text/plain",
}
_FORBIDDEN_UNQUOTED_PREFIXES = ("[", "{", "|", ">", "!", "&", "*", "%", "@", "`")
_YAML_NON_STRING_KEYWORDS = frozenset(
    {"null", "~", "true", "false", "yes", "no", "on", "off"}
)
_YAML_INT_RE = re.compile(
    r"^[+-]?(?:"
    r"0[xX][0-9a-fA-F][0-9a-fA-F_]*"
    r"|0[oO][0-7][0-7_]*"
    r"|0[bB][01][01_]*"
    r"|[0-9][0-9_]*"
    r"|[1-9][0-9_]*(?::[0-5]?[0-9])+"
    r")$"
)
_YAML_FLOAT_RE = re.compile(
    r"^[+-]?(?:"
    r"[0-9][0-9_]*\.[0-9_]*(?:[eE][+-]?[0-9][0-9_]*)?"
    r"|\.[0-9][0-9_]*(?:[eE][+-]?[0-9][0-9_]*)?"
    r"|[0-9][0-9_]*[eE][+-]?[0-9][0-9_]*"
    r"|\.(?:inf|nan)"
    r")$",
    re.IGNORECASE,
)
_YAML_DATE_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}"
    r"|\d{4}-\d{1,2}-\d{1,2}(?:[Tt]|[ \t]+)\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:[ \t]*(?:[Zz]|[+-]\d{1,2}(?::\d{2})?))?)$"
)
_MAX_SKILL_NAME_LEN = 64
_MAX_DESCRIPTION_LEN = 1024
_MAX_ENTRIES_PER_SKILL = 512
_MAX_BYTES_PER_SKILL = 16 * 1024 * 1024  # 16 MiB
_INSTRUCTIONS_MAX_CHARS = 800


class SkillError(ValueError):
    """Raised when a skill directory violates Agent Skills or SEP-2640 rules."""


def _default_skills_root() -> Path:
    return Path(__file__).resolve().parent / "skills"


def _is_skipped_name(name: str) -> bool:
    return name.startswith(".") or name == "__pycache__"


def _parse_single_quoted(raw: str, key: str) -> str:
    if len(raw) < 2 or not raw.endswith("'"):
        raise SkillError(f"unterminated single-quoted frontmatter value for {key!r}")
    inner = raw[1:-1]
    # YAML single-quoted strings escape a literal apostrophe as ''.
    if "'" in inner.replace("''", ""):
        raise SkillError(f"malformed single-quoted frontmatter value for {key!r}")
    value = inner.replace("''", "'")
    if not value:
        raise SkillError(f"empty frontmatter value for {key!r}")
    return value


def _parse_double_quoted(raw: str, key: str) -> str:
    if len(raw) < 2 or not raw.endswith('"'):
        raise SkillError(f"unterminated double-quoted frontmatter value for {key!r}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SkillError(f"malformed double-quoted frontmatter value for {key!r}") from exc
    if not isinstance(parsed, str) or not parsed:
        raise SkillError(f"empty frontmatter value for {key!r}")
    return parsed


def _is_non_string_plain_yaml(raw_val: str) -> bool:
    """True when YAML 1.1/1.2 would not resolve an unquoted scalar to a string."""
    if (
        raw_val.startswith("#")
        or " #" in raw_val
        or "\t#" in raw_val
        or ": " in raw_val
        or ":\t" in raw_val
        or raw_val.endswith(":")
        or raw_val == "?"
        or raw_val.startswith("? ")
    ):
        return True
    if raw_val.lower() in _YAML_NON_STRING_KEYWORDS:
        return True
    return bool(
        _YAML_INT_RE.fullmatch(raw_val)
        or _YAML_FLOAT_RE.fullmatch(raw_val)
        or _YAML_DATE_RE.fullmatch(raw_val)
    )


def _parse_frontmatter(text: str, skill_dir_name: str) -> dict[str, str]:
    """Parse flat ``key: value`` YAML frontmatter strictly; refuse nested/block forms."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0] != "---":
        raise SkillError(f"{skill_dir_name}/SKILL.md must open with '---'")
    closing_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index] == "---":
            closing_index = index
            break
    if closing_index is None:
        raise SkillError(f"{skill_dir_name}/SKILL.md frontmatter is missing closing '---'")

    frontmatter: dict[str, str] = {}
    for line in lines[1:closing_index]:
        if line == "":
            continue
        if line[0] in (" ", "\t"):
            raise SkillError(
                f"{skill_dir_name}/SKILL.md: indented frontmatter lines are not supported"
            )
        if line == "-" or line.startswith("- "):
            raise SkillError(
                f"{skill_dir_name}/SKILL.md: list items in frontmatter are not supported"
            )
        if ":" not in line:
            raise SkillError(
                f"{skill_dir_name}/SKILL.md: malformed frontmatter line {line!r}"
            )
        key_raw, value_raw = line.split(":", 1)
        key = key_raw.strip()
        if not key or key != key_raw or _FRONTMATTER_KEY_RE.fullmatch(key) is None:
            raise SkillError(
                f"{skill_dir_name}/SKILL.md: invalid frontmatter key {key_raw!r}"
            )
        if key in frontmatter:
            raise SkillError(
                f"{skill_dir_name}/SKILL.md: duplicate frontmatter key {key!r}"
            )
        raw_val = value_raw.strip()
        if not raw_val:
            raise SkillError(
                f"{skill_dir_name}/SKILL.md: empty frontmatter value for {key!r}"
            )
        if raw_val.startswith('"'):
            value = _parse_double_quoted(raw_val, key)
        elif raw_val.startswith("'"):
            value = _parse_single_quoted(raw_val, key)
        else:
            if (
                raw_val[0] in _FORBIDDEN_UNQUOTED_PREFIXES
                or raw_val == "-"
                or raw_val.startswith("- ")
            ):
                raise SkillError(
                    f"{skill_dir_name}/SKILL.md: unsupported frontmatter value for {key!r}"
                )
            if _is_non_string_plain_yaml(raw_val):
                raise SkillError(
                    f"{skill_dir_name}/SKILL.md: unquoted frontmatter value {raw_val!r} for {key!r} "
                    f"would not be parsed as a YAML string; quote it"
                )
            value = raw_val
        frontmatter[key] = value

    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not name:
        raise SkillError(f"{skill_dir_name}/SKILL.md: frontmatter requires 'name'")
    if not description:
        raise SkillError(f"{skill_dir_name}/SKILL.md: frontmatter requires 'description'")
    if name != skill_dir_name:
        raise SkillError(
            f"{skill_dir_name}/SKILL.md: frontmatter name {name!r} must match directory {skill_dir_name!r}"
        )
    if len(name) > _MAX_SKILL_NAME_LEN or _SKILL_NAME_RE.fullmatch(name) is None:
        raise SkillError(
            f"{skill_dir_name}/SKILL.md: invalid skill name {name!r}"
        )
    if len(description) > _MAX_DESCRIPTION_LEN:
        raise SkillError(
            f"{skill_dir_name}/SKILL.md: description exceeds {_MAX_DESCRIPTION_LEN} characters"
        )
    return frontmatter


def _collect_skill_files(skill_dir: Path) -> list[tuple[str, Path]]:
    """Walk ``skill_dir`` recursively, refusing symlinks and unsupported files."""
    collected: list[tuple[str, Path]] = []

    def _walk(current: Path, rel_parts: tuple[str, ...]) -> None:
        for child in sorted(current.iterdir(), key=lambda p: p.name):
            if _is_skipped_name(child.name):
                continue
            if child.is_symlink():
                rel = "/".join((*rel_parts, child.name))
                raise SkillError(f"{skill_dir.name}: symlink refused ({rel})")
            if child.is_dir():
                _walk(child, (*rel_parts, child.name))
            elif child.is_file():
                rel = "/".join((*rel_parts, child.name))
                collected.append((rel, child))
            else:
                rel = "/".join((*rel_parts, child.name))
                raise SkillError(f"{skill_dir.name}: unsupported entry type ({rel})")

    _walk(skill_dir, ())
    return collected


def _sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class SkillCatalog:
    """In-memory, immutable snapshot of validated Agent Skills."""

    def __init__(
        self,
        entries: list[dict[str, Any]],
        files: dict[str, tuple[bytes, str]],
    ) -> None:
        self._entries = sorted(entries, key=lambda item: item["uri"])
        self._entries_by_uri = {entry["uri"]: entry for entry in self._entries}
        self._files = dict(files)

    @classmethod
    def load(cls, root: str | Path | None = None) -> "SkillCatalog":
        skills_root = _default_skills_root() if root is None else Path(root)
        if not skills_root.exists():
            return cls([], {})
        if skills_root.is_symlink():
            raise SkillError(f"skills root must not be a symlink: {skills_root}")
        if not skills_root.is_dir():
            raise SkillError(f"skills root is not a directory: {skills_root}")

        entries: list[dict[str, Any]] = []
        files_by_uri: dict[str, tuple[bytes, str]] = {}

        for child in sorted(skills_root.iterdir(), key=lambda p: p.name):
            if _is_skipped_name(child.name):
                continue
            if child.is_symlink():
                raise SkillError(f"symlink refused in skills root: {child.name}")
            if not child.is_dir():
                continue

            skill_name = child.name
            discovered = _collect_skill_files(child)
            if len(discovered) > _MAX_ENTRIES_PER_SKILL:
                raise SkillError(
                    f"{skill_name}: manifest exceeds {_MAX_ENTRIES_PER_SKILL} entries"
                )

            skill_md_entry: tuple[str, Path] | None = None
            other_entries: list[tuple[str, Path]] = []
            for rel, path in discovered:
                if rel == "SKILL.md":
                    skill_md_entry = (rel, path)
                else:
                    other_entries.append((rel, path))
            if skill_md_entry is None:
                raise SkillError(f"{skill_name}: missing SKILL.md")

            total_bytes = 0
            skill_resources: list[dict[str, Any]] = []
            frontmatter: dict[str, str] | None = None

            ordered = [skill_md_entry] + sorted(
                other_entries, key=lambda item: f"skill://{skill_name}/{item[0]}"
            )
            for rel, path in ordered:
                mime = _ALLOWED_MIME.get(path.suffix)
                if mime is None:
                    raise SkillError(
                        f"{skill_name}: unsupported file extension for {rel!r}"
                    )
                raw = path.read_bytes()
                total_bytes += len(raw)
                if total_bytes > _MAX_BYTES_PER_SKILL:
                    raise SkillError(
                        f"{skill_name}: total size exceeds {_MAX_BYTES_PER_SKILL} bytes"
                    )
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise SkillError(
                        f"{skill_name}: {rel!r} is not valid UTF-8"
                    ) from exc

                if rel == "SKILL.md":
                    frontmatter = _parse_frontmatter(text, skill_name)

                uri = f"skill://{skill_name}/{rel}"
                if uri in files_by_uri:
                    raise SkillError(f"duplicate skill resource uri: {uri}")
                files_by_uri[uri] = (raw, mime)
                skill_resources.append(
                    {
                        "uri": uri,
                        "digest": _sha256_digest(raw),
                        "size": len(raw),
                    }
                )

            assert frontmatter is not None
            entries.append(
                {
                    "uri": f"skill://{skill_name}/SKILL.md",
                    "frontmatter": frontmatter,
                    "resources": skill_resources,
                }
            )

        return cls(entries, files_by_uri)

    def list_entries(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._entries)

    def get_entry(self, uri: str) -> dict[str, Any]:
        if not isinstance(uri, str) or uri not in self._entries_by_uri:
            raise KeyError(uri)
        return copy.deepcopy(self._entries_by_uri[uri])

    def read(self, uri: str) -> tuple[bytes, str]:
        if not isinstance(uri, str) or uri not in self._files:
            raise KeyError(uri)
        return self._files[uri]

    def uris(self) -> list[str]:
        return sorted(self._files)


def load_or_empty(root: str | Path | None = None) -> SkillCatalog:
    """Load the catalog, degrading to an empty catalog with a stderr warning on SkillError or an unreadable folder."""
    try:
        return SkillCatalog.load(root)
    except (SkillError, OSError, UnicodeError) as exc:
        print(
            f"[dayz-mcp] warning: skills catalog disabled ({type(exc).__name__}: {exc})",
            file=sys.stderr,
            flush=True,
        )
        return SkillCatalog([], {})


def _make_resource_reader(text: str) -> Callable[[], str]:
    """Zero-argument reader closure so FastMCP registers a static resource."""

    def _read_resource() -> str:
        return text

    return _read_resource


def install(app: Any, catalog: SkillCatalog | None = None) -> SkillCatalog:
    """Register every file in ``catalog`` as an ordinary FastMCP resource."""
    active = SkillCatalog.load() if catalog is None else catalog
    for entry in active.list_entries():
        head_uri = entry["uri"]
        fm = entry["frontmatter"]
        for item in entry["resources"]:
            uri = item["uri"]
            raw, mime = active.read(uri)
            text = raw.decode("utf-8")
            if uri == head_uri:
                name = fm["name"]
                description: str | None = fm["description"]
            else:
                name = uri.rsplit("/", 1)[-1]
                description = None
            app.resource(
                uri,
                name=name,
                description=description,
                mime_type=mime,
            )(_make_resource_reader(text))
    return active


def instructions_pointer(catalog: SkillCatalog) -> str:
    """Short server-instructions pointer naming every shipped SKILL.md URI."""
    entries = catalog.list_entries()
    if not entries:
        return ""
    suffix = ". Confirm with skills/get or read with resources/read."
    prefix = "Skills: "
    uris = [entry["uri"] for entry in entries]
    full = prefix + ", ".join(uris) + suffix
    if len(full) <= _INSTRUCTIONS_MAX_CHARS:
        return full
    # Keep as many complete URIs as fit within the ceiling.
    budget = _INSTRUCTIONS_MAX_CHARS - len(prefix) - len(suffix)
    kept: list[str] = []
    used = 0
    for uri in uris:
        needed = len(uri) + (2 if kept else 0)
        if used + needed > budget:
            break
        kept.append(uri)
        used += needed
    if not kept:
        return (prefix + uris[0])[:_INSTRUCTIONS_MAX_CHARS]
    return prefix + ", ".join(kept) + suffix
