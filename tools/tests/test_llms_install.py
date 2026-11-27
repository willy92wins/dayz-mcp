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

# User-facing install guides only. plans/ and reviews/ are out of scope for
# the release-asset GET, so a stray URL there cannot hide a missing one here.
USER_GUIDE_DOCS = ("llms-install.md", "README.md", "QUICKSTART.md")
RELEASE_ASSET_URL_RE = re.compile(
    r"https://github.com/willy92wins/dayz-mcp/releases/download/"
    r"v[0-9][^/\s\"'`)]+/[A-Za-z0-9._+-]+"
)
UNPUBLISHED_NO_CLONE_ASSET_RE = re.compile(
    r"https://github.com/willy92wins/dayz-mcp/releases/download/"
    r"v[0-9][^/\s\"'`)]+/(?:dayz-mcp-requirements\.txt|[^/\s\"'`)]+\.whl)"
)


def collect_documented_release_asset_urls(
    doc_texts: dict[str, str],
) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in USER_GUIDE_DOCS:
        body = doc_texts.get(name, "")
        for url in RELEASE_ASSET_URL_RE.findall(body):
            if url in seen:
                continue
            seen.add(url)
            found.append((name, url))
    return found


def is_offline_get_error(error: BaseException) -> bool:
    import urllib.error

    if isinstance(error, urllib.error.HTTPError):
        return False
    return isinstance(error, (urllib.error.URLError, TimeoutError, OSError))


def summarize_release_asset_gets(
    outcomes: list[tuple[str, str, int | None, Exception | None]],
) -> tuple[int, list[str], list[str]]:
    """Classify GET outcomes.

    HTTP 403/404 are failures. A mix of HTTP 200 and a timeout is a failure
    (the timeouts join the failure list). Offline is only when every GET
    failed with a network or timeout error.
    """
    successes = 0
    failures: list[str] = []
    offline: list[str] = []
    for rel, url, status, error in outcomes:
        if error is not None:
            if is_offline_get_error(error):
                offline.append(f"{rel}: GET {url} -> {error}")
            else:
                code = getattr(error, "code", error)
                failures.append(f"{rel}: GET {url} -> {code}")
        elif status == 200:
            successes += 1
        else:
            failures.append(f"{rel}: GET {url} -> {status}")
    if offline and (successes or failures):
        failures.extend(offline)
        offline = []
    return successes, failures, offline


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
            "-ClientProfiles",
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
        for doc_name in USER_GUIDE_DOCS:
            body = _read(doc_name)
            with self.subTest(doc=doc_name):
                self.assertIsNone(
                    UNPUBLISHED_NO_CLONE_ASSET_RE.search(body),
                    f"{doc_name} still advertises an unpublished wheel or "
                    "dayz-mcp-requirements.txt download URL",
                )
                lowered = body.casefold()
                self.assertNotIn("no clone.", lowered)
                self.assertNotIn("without a clone", lowered)
                self.assertNotIn("sin clonar", lowered)

    def test_register_requires_python_311_and_native_claude_and_codex(self) -> None:
        body = _read("llms-install.md")
        lowered = body.casefold()
        self.assertNotIn("codex is optional", lowered)
        self.assertIn("Python 3.11", body)
        self.assertIn("-Register", body)
        self.assertIn("Claude", body)
        self.assertIn("Codex", body)
        self.assertIn("native", lowered)
        self.assertIn("registration check failed for Codex", body)
        self.assertRegex(
            body,
            r"-Register.{0,160}(Claude.{0,80}Codex|Codex.{0,80}Claude)"
            r"|(Claude.{0,80}Codex|Codex.{0,80}Claude).{0,160}-Register",
            "llms-install.md must say -Register requires both Claude and Codex",
        )

    def test_guide_documents_workshop_mod_and_client_profiles(self) -> None:
        body = _read("llms-install.md")
        workshop_id = _module_string_constant(
            "tools/dayz_mcp/setup_cmd.py", "WORKSHOP_ITEM_ID"
        )
        self.assertIn(
            rf"steamapps\workshop\content\221100\{workshop_id}",
            body,
        )
        self.assertIn("-mod=", body)
        self.assertIn("-profiles=", body)
        self.assertIn("-ClientProfiles", body)
        self.assertIn("-ClientProfiles", self._commands_block_text())
        self.assertRegex(
            body,
            r"does not print.{0,40}-mod=|-mod=.{0,40}does not print",
        )
        self.assertRegex(
            body,
            r"Without `-ClientProfiles`.{0,120}dayz_mcp\.json"
            r"|without `-ClientProfiles`.{0,120}dayz_mcp\.json",
        )

    def test_doctor_check_uses_doctor_module_and_daemon_policy(self) -> None:
        body = _read("llms-install.md")
        self.assertIn(
            "-m dayz_mcp.doctor --daemon-policy normal --json",
            body,
        )
        self.assertIsNone(
            re.search(r"-m dayz_mcp doctor\b", body),
            "llms-install.md must not invoke `python -m dayz_mcp doctor` "
            "(that hits server.run and requires --keyfile)",
        )

    def test_changelog_1_6_does_not_promise_no_clone_install(self) -> None:
        changelog = _read("CHANGELOG.md")
        start = changelog.index("## [1.6]")
        end = changelog.index("## [1.5]")
        section = changelog[start:end].casefold()
        self.assertNotIn("without a clone", section)
        self.assertNotIn("from the github release with uv", section)

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

    def test_quickstart_does_not_prescribe_v15_addon_zip_for_current_clone(self) -> None:
        body = _read("QUICKSTART.md")
        self.assertNotIn(
            "DayZ_MCP-v1.5-addon.zip",
            body,
            "QUICKSTART must not prescribe the v1.5 addon zip for a current clone",
        )
        self.assertIn(
            "https://github.com/willy92wins/dayz-mcp/releases/download/v1.5/VERSION.json",
            body,
        )
        self.assertIn("git checkout v1.5", body)
        self.assertRegex(body, r"version_mismatch|bridge_version")
        self.assertRegex(body, r"(?i)historical")

    def test_quickstart_and_readme_register_require_claude_and_codex(self) -> None:
        for doc_name in ("QUICKSTART.md", "README.md", "llms-install.md"):
            body = _read(doc_name)
            with self.subTest(doc=doc_name):
                lowered = body.casefold()
                self.assertNotIn("codex is optional", lowered)
                self.assertNotIn("from claude alone", lowered)
                self.assertIn("-Register", body)
                self.assertIn("Claude", body)
                self.assertIn("Codex", body)
                self.assertIn("registration check failed for Codex", body)

    def _assert_register_precheck_docs(self, body: str, doc_name: str) -> None:
        lowered = body.casefold()
        self.assertNotIn("for the add itself", lowered)
        self.assertNotIn("shim is fine", lowered)
        self.assertNotIn("the printed lines run automatically", lowered)
        self.assertNotIn("claude.cmd", body)
        self.assertIn("pre-check", lowered)
        self.assertIn("codex.cmd", body)
        self.assertIn("claude.exe", body)
        self.assertIn("codex.exe", body)
        self.assertIn("install_mcp.py", body)
        self.assertIn("installer_cli_not_native_exe", body)
        self.assertRegex(
            body,
            r"(?s)mcp add.{0,80}(by hand|a mano|manual)"
            r"|(by hand|a mano|manual).{0,80}mcp add",
            f"{doc_name} must say the printed mcp add lines are only "
            "for doing the registration by hand",
        )

    def test_register_docs_distinguish_precheck_from_native_exe(self) -> None:
        for doc_name in ("QUICKSTART.md", "README.md"):
            body = _read(doc_name)
            with self.subTest(doc=doc_name):
                self._assert_register_precheck_docs(body, doc_name)

    def test_register_docs_reject_claude_cmd_precheck_and_automatic_printed_lines(self) -> None:
        """Mutations the looser wording test accepted must now fail."""
        precheck = r"The pre-check uses\s+`claude`\s*/\s*`codex\.cmd`"
        for doc_name in ("QUICKSTART.md", "README.md"):
            body = _read(doc_name)
            self.assertRegex(body, precheck, f"{doc_name} lost the pre-check sentence")
            with self.subTest(doc=doc_name, mutation="claude.cmd"):
                mutated, n = re.subn(
                    precheck,
                    "The pre-check uses `claude.cmd` / `codex.cmd`",
                    body,
                    count=1,
                )
                self.assertEqual(n, 1)
                with self.assertRaises(AssertionError):
                    self._assert_register_precheck_docs(mutated, doc_name)
            with self.subTest(doc=doc_name, mutation="automatic"):
                mutated = body + "\nThe printed lines run automatically.\n"
                with self.assertRaises(AssertionError):
                    self._assert_register_precheck_docs(mutated, doc_name)

    def test_documented_release_assets_return_http_200(self) -> None:
        """Every concrete GitHub release asset URL in the user guides must exist.

        The no-clone install path advertised a requirements file that returned
        404. The test fails if the guides name no asset to check. HTTP 403/404
        are failures; only a network or timeout error is treated as offline.
        """
        import urllib.error
        import urllib.request

        doc_texts = {name: _read(name) for name in USER_GUIDE_DOCS}
        found = collect_documented_release_asset_urls(doc_texts)
        self.assertGreater(
            len(found),
            0,
            "user guides document no release asset URL to verify; the GET "
            "must fail when it finds 0 URLs",
        )

        outcomes: list[tuple[str, str, int | None, Exception | None]] = []
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
                outcomes.append((rel, url, None, error))
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                outcomes.append((rel, url, None, error))
                continue
            outcomes.append((rel, url, status, None))

        successes, failures, offline = summarize_release_asset_gets(outcomes)
        if failures:
            self.fail(
                "documented release asset URL(s) did not return HTTP 200:\n"
                + "\n".join(failures)
            )
        if offline:
            # Only when every GET failed with a network or timeout error.
            self.skipTest(
                "offline: cannot GET GitHub to verify release assets: "
                + "; ".join(offline)
            )


class DocumentedReleaseAssetGetHelpersTest(unittest.TestCase):
    def test_zero_urls_is_a_failure_not_a_pass(self) -> None:
        found = collect_documented_release_asset_urls(
            {name: "no asset URLs here" for name in USER_GUIDE_DOCS}
        )
        self.assertEqual(found, [])

    def test_collects_urls_only_from_user_guides(self) -> None:
        self.assertEqual(
            USER_GUIDE_DOCS,
            ("llms-install.md", "README.md", "QUICKSTART.md"),
        )
        self.assertNotIn("plans/", USER_GUIDE_DOCS)
        self.assertNotIn("reviews/", USER_GUIDE_DOCS)
        url = (
            "https://github.com/willy92wins/dayz-mcp/releases/download/"
            "v1.5/DayZ_MCP-v1.5-addon.zip"
        )
        found = collect_documented_release_asset_urls(
            {
                "QUICKSTART.md": f"see {url}",
                "plans/secret.md": "https://github.com/willy92wins/dayz-mcp/"
                "releases/download/v1.6/dayz-mcp-requirements.txt",
                "reviews/note.md": "https://github.com/willy92wins/dayz-mcp/"
                "releases/download/v0.0/ignored.whl",
            }
        )
        self.assertEqual(found, [("QUICKSTART.md", url)])

    def test_http_403_and_404_are_not_offline(self) -> None:
        import urllib.error

        for code in (403, 404):
            error = urllib.error.HTTPError(
                url="https://example.invalid/asset",
                code=code,
                msg="gone",
                hdrs=None,
                fp=None,
            )
            with self.subTest(code=code):
                self.assertFalse(is_offline_get_error(error))
                self.assertIsInstance(error, urllib.error.URLError)

    def test_network_and_timeout_errors_are_offline(self) -> None:
        import urllib.error

        self.assertTrue(
            is_offline_get_error(urllib.error.URLError("name or service not known"))
        )
        self.assertTrue(is_offline_get_error(TimeoutError("timed out")))

    def test_mixed_200_and_timeout_is_a_failure(self) -> None:
        successes, failures, offline = summarize_release_asset_gets(
            [
                ("QUICKSTART.md", "https://example.invalid/a", 200, None),
                (
                    "QUICKSTART.md",
                    "https://example.invalid/b",
                    None,
                    TimeoutError("timed out"),
                ),
            ]
        )
        self.assertEqual(successes, 1)
        self.assertTrue(
            failures,
            "a mix of HTTP 200 and a timeout must be a failure, not a pass or skip",
        )
        self.assertEqual(offline, [])

    def test_all_timeouts_stay_offline(self) -> None:
        successes, failures, offline = summarize_release_asset_gets(
            [
                (
                    "QUICKSTART.md",
                    "https://example.invalid/a",
                    None,
                    TimeoutError("timed out"),
                )
            ]
        )
        self.assertEqual(successes, 0)
        self.assertEqual(failures, [])
        self.assertEqual(len(offline), 1)



if __name__ == "__main__":
    unittest.main()
