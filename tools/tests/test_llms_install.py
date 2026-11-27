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

    def test_versions_match_pyproject_toml(self) -> None:
        version = _project_version()
        text = _read("llms-install.md")

        venv_versions = [
            value.rstrip(".")
            for value in re.findall(r"\bvenv-([^\s\"'\\/`),;:]+)", text)
        ]
        self.assertGreaterEqual(
            len(venv_versions),
            1,
            "llms-install.md has no venv-<version> references; expected at least "
            f"one matching tools/pyproject.toml version {version!r}",
        )
        bad_venv = [value for value in venv_versions if value != version]
        self.assertEqual(
            bad_venv,
            [],
            f"llms-install.md venv-<version> references {bad_venv!r} differ from "
            f"tools/pyproject.toml [project].version {version!r}",
        )

        expected_tag = f"v{version}"
        download_tags = re.findall(r"/releases/download/([^/\s\"'`]+)/", text)
        self.assertGreaterEqual(
            len(download_tags),
            1,
            "llms-install.md has no /releases/download/v<version>/ URLs; expected "
            f"at least one matching tools/pyproject.toml tag {expected_tag!r}",
        )
        bad_tags = [tag for tag in download_tags if tag != expected_tag]
        self.assertEqual(
            bad_tags,
            [],
            f"llms-install.md /releases/download/.../ tags {bad_tags!r} differ "
            f"from tools/pyproject.toml tag {expected_tag!r}",
        )

    def test_requirements_url_matches_make_release(self) -> None:
        release_download_url = _module_string_constant(
            "tools/make_release.py", "RELEASE_DOWNLOAD_URL"
        )
        requirements_asset_name = _module_string_constant(
            "tools/make_release.py", "REQUIREMENTS_ASSET_NAME"
        )
        version = _project_version()
        expected_url = f"{release_download_url}/v{version}/{requirements_asset_name}"

        block_text = self._commands_block_text()
        targets = re.findall(r"(?<!\S)-r\s+(\S+)", block_text)
        self.assertGreaterEqual(
            len(targets),
            1,
            "llms-install.md '## Commands' block has no '-r <url>' argument; "
            f"expected {expected_url!r} from tools/make_release.py and "
            "tools/pyproject.toml",
        )
        bad_targets = [target for target in targets if target != expected_url]
        self.assertEqual(
            bad_targets,
            [],
            f"llms-install.md '## Commands' '-r' target(s) {bad_targets!r} differ "
            f"from tools/make_release.py + tools/pyproject.toml {expected_url!r}",
        )
        self.assertIsNotNone(
            re.search(rf"(?<!\S)-r {re.escape(expected_url)}(?!\S)", block_text),
            "llms-install.md '## Commands' block does not contain exact "
            f"'-r {expected_url}' from tools/make_release.py and "
            "tools/pyproject.toml",
        )

    def test_python_version_matches_requirements_lock_header(self) -> None:
        expected_py = _lock_python_version()
        block_text = self._commands_block_text()

        install_versions = re.findall(
            r"\buv\s+python\s+install\s+([^\s\"'`)]+)", block_text
        )
        self.assertGreaterEqual(
            len(install_versions),
            1,
            "llms-install.md '## Commands' block has no 'uv python install <ver>'; "
            "expected at least one matching tools/release/requirements.lock.txt "
            f"--python-version {expected_py!r}",
        )
        bad_install = [ver for ver in install_versions if ver != expected_py]
        self.assertEqual(
            bad_install,
            [],
            f"llms-install.md '## Commands' 'uv python install' version(s) "
            f"{bad_install!r} differ from tools/release/requirements.lock.txt "
            f"--python-version {expected_py!r}",
        )

        managed_versions = re.findall(
            r"--managed-python\s+([^\s\"'`)]+)", block_text
        )
        self.assertGreaterEqual(
            len(managed_versions),
            1,
            "llms-install.md '## Commands' block has no '--managed-python <ver>'; "
            "expected at least one matching tools/release/requirements.lock.txt "
            f"--python-version {expected_py!r}",
        )
        bad_managed = [ver for ver in managed_versions if ver != expected_py]
        self.assertEqual(
            bad_managed,
            [],
            f"llms-install.md '## Commands' '--managed-python' version(s) "
            f"{bad_managed!r} differ from tools/release/requirements.lock.txt "
            f"--python-version {expected_py!r}",
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

    def test_commands_block_required_flags_and_no_uv_venv(self) -> None:
        block_text = self._commands_block_text()
        required_tokens = (
            "--no-bin",
            "--no-registry",
            "os.path.realpath",
            "--without-pip",
            "--link-mode=copy",
            "--require-hashes",
            "setup --json",
        )
        for token in required_tokens:
            self.assertIn(
                token,
                block_text,
                f"llms-install.md '## Commands' block omits required token "
                f"{token!r} from the install recipe",
            )
        self.assertIsNone(
            re.search(r"\buv\s+venv\b", block_text),
            "llms-install.md '## Commands' block uses forbidden 'uv venv'; "
            "the recipe must invoke 'os.path.realpath(sys.executable)' with "
            "'-m venv --without-pip'",
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


if __name__ == "__main__":
    unittest.main()
