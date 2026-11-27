"""Docs truth watchdog for llms-install.md and its pointers across the repo.

Ground truth is extracted from the tree (tools/pyproject.toml, the header of
tools/release/requirements.lock.txt, and module-level string constants parsed
with ast from tools/dayz_mcp/setup_cmd.py and tools/make_release.py without
importing those modules, so this suite runs on Linux as well as Windows).
"""

from __future__ import annotations

import ast
import re
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_H2_RE = re.compile(r"^##[ \t]+")
_WORKSHOP_LINK_RE = re.compile(
    r"https://steamcommunity\.com/sharedfiles/filedetails/\?id=([^\s)\"'>`]*)"
)


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _module_string_constant(rel: str, name: str) -> str:
    path = REPO / rel
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[str] = []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            values.append(node.value.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            values.append(node.value.value)
    if len(values) != 1:
        raise AssertionError(
            f"{rel} must define module-level string constant {name} exactly once "
            f"(found {len(values)})"
        )
    return values[0]


def _project_version() -> str:
    data = tomllib.loads(_read("tools/pyproject.toml"))
    project = data.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str) or not version:
        raise AssertionError(
            "tools/pyproject.toml [project].version must be a non-empty string "
            f"(found {version!r})"
        )
    return version


def _lock_python_version() -> str:
    header_lines: list[str] = []
    for line in _read("tools/release/requirements.lock.txt").splitlines():
        if line.startswith("#"):
            header_lines.append(line)
        else:
            break
    header = "\n".join(header_lines)
    matches = re.findall(r"--python-version\s+([^\s]+)", header)
    if len(matches) != 1:
        raise AssertionError(
            "tools/release/requirements.lock.txt header must carry exactly one "
            f"--python-version flag (found {matches!r})"
        )
    return matches[0]


def _is_closing_fence(line: str, fence_char: str, fence_len: int) -> bool:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return False
    token = stripped.rstrip()
    return len(token) >= fence_len and token == fence_char * len(token)


def _extract_h2_section(text: str, heading: str, *, doc_name: str) -> list[str]:
    lines = text.splitlines()
    in_fence_char: str | None = None
    in_fence_len = 0
    starts: list[int] = []
    for idx, line in enumerate(lines):
        if in_fence_char is None:
            match = _FENCE_OPEN_RE.match(line)
            if match:
                in_fence_char = match.group(1)[0]
                in_fence_len = len(match.group(1))
                continue
            if line.rstrip() == heading:
                starts.append(idx)
        elif _is_closing_fence(line, in_fence_char, in_fence_len):
            in_fence_char = None
            in_fence_len = 0
    if len(starts) != 1:
        raise AssertionError(
            f"{doc_name} must contain heading {heading!r} exactly once "
            f"(found {len(starts)})"
        )
    start = starts[0] + 1
    in_fence_char = None
    in_fence_len = 0
    end = len(lines)
    for idx in range(start, len(lines)):
        line = lines[idx]
        if in_fence_char is None:
            match = _FENCE_OPEN_RE.match(line)
            if match:
                in_fence_char = match.group(1)[0]
                in_fence_len = len(match.group(1))
                continue
            if _H2_RE.match(line):
                end = idx
                break
        elif _is_closing_fence(line, in_fence_char, in_fence_len):
            in_fence_char = None
            in_fence_len = 0
    return lines[start:end]


def _extract_fenced_blocks(
    lines: list[str], *, context: str
) -> list[tuple[str, list[str]]]:
    blocks: list[tuple[str, list[str]]] = []
    in_fence_char: str | None = None
    in_fence_len = 0
    opener = ""
    body: list[str] = []
    for line in lines:
        if in_fence_char is None:
            match = _FENCE_OPEN_RE.match(line)
            if match:
                in_fence_char = match.group(1)[0]
                in_fence_len = len(match.group(1))
                opener = line.strip()
                body = []
        elif _is_closing_fence(line, in_fence_char, in_fence_len):
            blocks.append((opener, body))
            in_fence_char = None
            in_fence_len = 0
            opener = ""
            body = []
        else:
            body.append(line)
    if in_fence_char is not None:
        raise AssertionError(
            f"{context} has an unclosed fenced code block opened by {opener!r}"
        )
    return blocks


class LlmsInstallDocsTest(unittest.TestCase):
    """llms-install.md, its pointers, and docs/RELEASE.md must match the tree."""

    def _commands_block(self) -> tuple[str, list[str]]:
        section = _extract_h2_section(
            _read("llms-install.md"), "## Commands", doc_name="llms-install.md"
        )
        blocks = _extract_fenced_blocks(
            section, context="llms-install.md '## Commands'"
        )
        self.assertEqual(
            len(blocks),
            1,
            "llms-install.md '## Commands' section must contain exactly 1 fenced "
            f"code block, found {len(blocks)}",
        )
        return blocks[0]

    def _commands_block_text(self) -> str:
        _opener, body = self._commands_block()
        return "\n".join(body)

    def test_commands_section_has_one_powershell_block_with_bounded_lines(self) -> None:
        opener, body = self._commands_block()
        self.assertEqual(
            opener,
            "```powershell",
            "llms-install.md '## Commands' block opener is "
            f"{opener!r}; expected '```powershell'",
        )
        non_empty = [line for line in body if line.strip()]
        self.assertTrue(
            1 <= len(non_empty) <= 6,
            "llms-install.md '## Commands' powershell block has "
            f"{len(non_empty)} non-empty lines; expected between 1 and 6",
        )

    def test_commands_block_is_the_clone_path(self) -> None:
        block_text = self._commands_block_text()
        required_tokens = (
            "git clone https://github.com/willy92wins/dayz-mcp.git",
            "cd dayz-mcp\\tools",
            ".\\install-mcp.ps1",
            "-ServerProfiles",
            "-Register",
        )
        for token in required_tokens:
            self.assertIn(
                token,
                block_text,
                f"llms-install.md '## Commands' block omits required token "
                f"{token!r} from the clone-path recipe",
            )
        self.assertIsNone(
            re.search(
                r"https://github.com/willy92wins/dayz-mcp/releases/download/",
                block_text,
            ),
            "llms-install.md '## Commands' block must not advertise a "
            "releases/download URL until a release publishes that asset",
        )
        self.assertIsNone(
            re.search(r"\buv\s+venv\b", block_text),
            "llms-install.md '## Commands' block uses forbidden 'uv venv'",
        )

    def test_guide_does_not_sell_an_unpublished_no_clone_path(self) -> None:
        concrete_asset = re.compile(
            r"https://github.com/willy92wins/dayz-mcp/releases/download/"
            r"v[0-9][^\s/\"'`)]+/[A-Za-z0-9._+-]+"
        )
        for doc_name in ("llms-install.md", "README.md", "QUICKSTART.md"):
            text = _read(doc_name)
            with self.subTest(doc=doc_name):
                self.assertIsNone(
                    concrete_asset.search(text),
                    f"{doc_name} still contains a concrete releases/download "
                    "asset URL; the no-clone path is not published yet",
                )
                lowered = text.casefold()
                self.assertNotIn("no clone.", lowered)
                self.assertNotIn("without a clone", lowered)
                self.assertNotIn("sin clonar", lowered)

    def test_powershell_invokes_dayz_mcp_exe_with_dot_slash(self) -> None:
        text = _read("llms-install.md")
        self.assertIn(
            ".\\dayz-mcp.exe",
            text,
            "llms-install.md must tell a clean PowerShell to invoke "
            ".\\dayz-mcp.exe, not a bare dayz-mcp or dayz-mcp.exe",
        )
        bare = re.compile(r"(?m)(?:^|[;&|]\s*)dayz-mcp(?:\.exe)?\b")
        for opener, body in _extract_fenced_blocks(
            text.splitlines(), context="llms-install.md"
        ):
            if not opener.endswith("powershell"):
                continue
            joined = "\n".join(body)
            self.assertIsNone(
                bare.search(joined),
                "a PowerShell block invokes dayz-mcp without the .\\ prefix: "
                f"{joined!r}",
            )

    def test_exit_2_is_not_always_a_missing_list(self) -> None:
        text = _read("llms-install.md")
        self.assertRegex(
            text,
            r"exit(?:s| code)? 2.{0,160}missing",
        )
        self.assertRegex(
            text,
            r"missing.{0,120}empty.{0,80}error|error.{0,80}missing.{0,80}empty",
            "llms-install.md must say that exit 2 with an empty missing list "
            "is read from error, not treated as missing",
        )


    def test_workshop_item_id_is_digits_and_matches_all_workshop_links(self) -> None:
        workshop_id = _module_string_constant(
            "tools/dayz_mcp/setup_cmd.py", "WORKSHOP_ITEM_ID"
        )
        self.assertIsNotNone(
            re.fullmatch(r"[0-9]+", workshop_id),
            f"tools/dayz_mcp/setup_cmd.py WORKSHOP_ITEM_ID is {workshop_id!r}; "
            "expected non-empty ASCII digits",
        )
        for doc_name in ("llms-install.md", "README.md", "QUICKSTART.md"):
            ids = _WORKSHOP_LINK_RE.findall(_read(doc_name))
            if doc_name == "llms-install.md":
                self.assertGreaterEqual(
                    len(ids),
                    1,
                    "llms-install.md has no Steam Workshop item link; expected at "
                    "least one carrying tools/dayz_mcp/setup_cmd.py "
                    f"WORKSHOP_ITEM_ID {workshop_id!r}",
                )
            bad_ids = [found for found in ids if found != workshop_id]
            self.assertEqual(
                bad_ids,
                [],
                f"{doc_name} Steam Workshop link id(s) {bad_ids!r} differ from "
                f"tools/dayz_mcp/setup_cmd.py WORKSHOP_ITEM_ID {workshop_id!r}",
            )

    def test_readme_and_quickstart_link_to_llms_install(self) -> None:
        self.assertTrue(
            (REPO / "llms-install.md").is_file(),
            "llms-install.md is missing from the repository root while README.md "
            "and QUICKSTART.md link to it",
        )
        for doc_name in ("README.md", "QUICKSTART.md"):
            self.assertIn(
                "](llms-install.md)",
                _read(doc_name),
                f"{doc_name} does not link to llms-install.md with "
                "'](llms-install.md)'",
            )

    def test_release_runbook_step_1_names_llms_install(self) -> None:
        lines = _read("docs/RELEASE.md").splitlines()
        step1_indices = [
            idx for idx, line in enumerate(lines) if line.startswith("## 1.")
        ]
        step2_indices = [
            idx for idx, line in enumerate(lines) if line.startswith("## 2.")
        ]
        self.assertEqual(
            len(step1_indices),
            1,
            f"docs/RELEASE.md must have exactly one '## 1.' heading (found {len(step1_indices)})",
        )
        self.assertEqual(
            len(step2_indices),
            1,
            f"docs/RELEASE.md must have exactly one '## 2.' heading (found {len(step2_indices)})",
        )
        self.assertLess(
            step1_indices[0],
            step2_indices[0],
            f"docs/RELEASE.md '## 1.' (line {step1_indices[0] + 1}) must precede "
            f"'## 2.' (line {step2_indices[0] + 1})",
        )
        step1_text = "\n".join(lines[step1_indices[0] : step2_indices[0]])
        self.assertIn(
            "llms-install.md",
            step1_text,
            "docs/RELEASE.md step 1 ('## 1.' to '## 2.') does not name "
            "'llms-install.md' as a file to update with tools/pyproject.toml",
        )

    def test_documented_release_assets_return_http_200(self) -> None:
        """Every concrete GitHub release asset URL in the docs must exist.

        The no-clone install path advertised a requirements file that returned
        404. This GET is skipped only when GitHub cannot be reached.
        """
        import urllib.error
        import urllib.request

        doc_paths = [
            path
            for path in REPO.rglob("*.md")
            if ".git" not in path.parts and "tools/tests" not in path.as_posix()
        ]
        url_re = re.compile(
            r"https://github.com/willy92wins/dayz-mcp/releases/download/"
            r"v[0-9][^/\s\"'`)]+/[A-Za-z0-9._+-]+"
        )
        found: list[tuple[str, str]] = []
        seen: set[str] = set()
        for path in sorted(doc_paths):
            rel = path.relative_to(REPO).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeError:
                continue
            for url in url_re.findall(text):
                if url in seen:
                    continue
                seen.add(url)
                found.append((rel, url))

        probe = "https://github.com/willy92wins/dayz-mcp/releases"
        try:
            request = urllib.request.Request(
                probe,
                method="GET",
                headers={"User-Agent": "dayz-mcp-docs-asset-check"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                response.read(64)
        except (urllib.error.URLError, TimeoutError, OSError):
            self.skipTest("offline: cannot GET GitHub to verify release assets")

        failures: list[str] = []
        for rel, url in found:
            request = urllib.request.Request(
                url,
                method="GET",
                headers={"User-Agent": "dayz-mcp-docs-asset-check"},
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    status = getattr(response, "status", None) or response.getcode()
                    response.read(64)
            except urllib.error.HTTPError as error:
                failures.append(f"{rel}: GET {url} -> {error.code}")
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                failures.append(f"{rel}: GET {url} -> {error}")
                continue
            if status != 200:
                failures.append(f"{rel}: GET {url} -> {status}")
        self.assertEqual(
            failures,
            [],
            "documented release asset URL(s) did not return HTTP 200:\n"
            + "\n".join(failures),
        )




if __name__ == "__main__":
    unittest.main()
