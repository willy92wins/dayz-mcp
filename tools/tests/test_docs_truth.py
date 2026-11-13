"""Docs truth watchdog: mechanical claims in the docs must match the tree.

Modeled on test_dayz_test_value_error_codes.py: ground truth is extracted from
the tree (AST / imports / os.stat / regex over source) and the assertion
compares doc against tree, never against a number fixed inside this file. Doc
line numbers are treated as hints; symbols are the keys. When an assertion
fails, the message names both sides and the remedy.

Two tree hooks (both optional, both documented):
  DAYZ_MCP_WATCHDOG_REPO   alternate repo root (default: parents[2] of this
                           file, i.e. the repo when installed at tools/tests/).
  DAYZ_MCP_WATCHDOG_TREE   alternate source for DOC files and server.py only
                           (ground-truth modules always come from the real
                           repo). Used to replay the watchdog against the
                           2026-08-21 pre-patch backups: every gate assertion
                           must turn red there and green against today's tree.

Out of scope, on purpose (an honest watchdog beats a pretend-complete one):
  - Prose semantics ("3 actors", "Swiss-army knife"): no mechanical truth.
  - Dated historical measurements ("Measured 2026-08-21: ..."): they are a
    record of a run, not a promise about the tree. If the sentence carries a
    date, this file does not touch it.
  - Files outside the repo: the vanilla scripts tree (named skip when absent,
    override with DAYZ_MCP_VANILLA_SCRIPTS), the vault, %LOCALAPPDATA%, and
    the sibling mod tree. product-spec's `MCP*.c:NNN` citations are NOT
    checked here: ground truth for those files is git `addon/` (not the
    sibling; fb-20260925-233932-aa11), which may be sparse-excluded.
  - In-game behaviour: the suite must run in CI without DayZ.
  - Tool counts and the README tool list: already watched by
    tests/test_install_mcp.py::PublicToolCountDocsTest. Not duplicated.
  - CLAUDE.md status claims (POC/11-tools/FastMCP): the owner keeps that file
    deliberately stale and edits it by hand; only its vanilla citations are
    checked, and they were correct when last measured.
  - NEXT-SESSION-PROMPT.txt staleness: which phase is the front is prose
    state, not a mechanical artifact.
  - QUICKSTART's bridge_status reason list (3 of a closed set named): the
    claim it makes ("reason names the cause") is true; forcing full
    enumeration is a style choice, not truth enforcement.
  - Dates/mtimes inside PROJECT-MAP.md ("touched ..."): a claim about the
    clock, not about the tree; sizes and counts are checked, dates are not.
  - Vanilla citations with no adjacent symbol: nothing to verify against.
  - HANDOFF.md line counts and KB sizes in PROJECT-MAP: the LIVE-STATE
    block is rewritten on every close, so a pinned figure is stale by
    construction. The gate checks that the map names LIVE-STATE:END and
    does not pin those numbers.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import re
import sys
import unittest
from pathlib import Path
from tests._tiers import slow_test

REPO = Path(
    os.environ.get("DAYZ_MCP_WATCHDOG_REPO")
    or Path(__file__).resolve().parents[2]
)
_ALT = os.environ.get("DAYZ_MCP_WATCHDOG_TREE")
ALT = Path(_ALT) if _ALT else None
VANILLA = Path(os.environ.get("DAYZ_MCP_VANILLA_SCRIPTS", "P:/scripts"))

_TOOLS = REPO / "tools"
if _TOOLS.is_dir() and str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

# Mojibake repair for PROJECT-MAP.md history (UTF-8 read as Latin-1 then
# saved): only used so the claim regexes match either era.
_MOJIBAKE = {
    "\u00c3\u00a1": "á", "\u00c3\u00a9": "é", "\u00c3\u00ad": "í",
    "\u00c3\u00b3": "ó", "\u00c3\u00ba": "ú", "\u00c3\u00b1": "ñ",
    "\u00c2\u00bf": "¿", "\u00c2\u00a1": "¡",
}


def _unmojibake(text: str) -> str:
    for broken, fixed in _MOJIBAKE.items():
        text = text.replace(broken, fixed)
    return text


def _doc_path(rel: str) -> Path:
    """Doc files (and server.py) come from ALT when it carries them."""
    if ALT is not None:
        alt = ALT / rel
        if alt.is_file():
            return alt
    return REPO / rel


def _doc(rel: str) -> str:
    return _doc_path(rel).read_text(encoding="utf-8", errors="replace")


def _published_docs(names: tuple[str, ...]) -> list[str]:
    """Of `names`, the ones this tree actually carries.

    Internal documents are excluded from the published tree, so a clone has only
    a subset. Checking the subset keeps the assertion meaningful there instead of
    raising FileNotFoundError on the first absent one.
    """
    return [rel for rel in names if _doc_path(rel).is_file()]


def _module_text(rel: str) -> str:
    """Ground truth: always the real tree, never the backup replay."""
    return (_TOOLS / rel).read_text(encoding="utf-8")


def _tool_names() -> set[str]:
    tree = ast.parse(_doc("tools/dayz_mcp/server.py"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "tool"):
                    names.add(node.name)
    return names


class LeaseListDocsTest(unittest.TestCase):
    """tools/README-mcp.md lease list must equal the code's own partition
    (A2). Ground truth: loopback command sets minus READ_ONLY_COMMANDS,
    intersected with the MCP tool names parsed from server.py."""

    @slow_test
    def test_lease_list_equals_code_partition(self) -> None:
        from dayz_mcp.loopback import CLIENT_COMMANDS, SERVER_COMMANDS
        from dayz_mcp.session_coordination import READ_ONLY_COMMANDS

        text = _doc("tools/README-mcp.md")
        m = re.search(r"Requires a lease \(these mutate the game\): (.*?)\.", text, re.S)
        self.assertIsNotNone(
            m, "lease-list sentence moved; update the regex, never the list")
        listed = set(re.findall(r"`([a-z_]+)`", m.group(1)))
        mutating = (SERVER_COMMANDS | CLIENT_COMMANDS) - READ_ONLY_COMMANDS
        expected = (mutating & _tool_names()) | {
            "dayz_test_run", "dayz_test_stop"}
        self.assertEqual(
            listed, expected,
            f"README-mcp omits {sorted(expected - listed)} from the lease list "
            f"(an agent planning those tools without a lease eats "
            f"`lease_required` mid-sequence) and lists {sorted(listed - expected)} "
            f"which the code does not treat as mutating bridge tools",
        )


class CanopyCalibrationDocsTest(unittest.TestCase):
    """Calibration prose must match the TOML state it describes (A1)."""

    def test_canopy_state_matches_toml(self) -> None:
        toml = (REPO / "playbooks" / "place_safely.toml").read_text(
            encoding="utf-8")
        m = re.search(r'name = "canopy_dy"', toml)
        self.assertIsNotNone(m, "canopy_dy block moved in place_safely.toml")
        state = re.search(r'state = "([a-z]+)"', toml[m.end():m.end() + 400])
        self.assertIsNotNone(state, "canopy_dy lost its state = line")
        prose = _doc("tools/README-mcp.md")
        claim = re.search(r"`canopy_dy` is `(calibrated|uncalibrated)`", prose)
        self.assertIsNotNone(
            claim, "README-mcp stopped stating canopy_dy's calibration state")
        self.assertEqual(
            claim.group(1), state.group(1),
            f"README-mcp says canopy_dy is {claim.group(1)}; place_safely.toml "
            f"says {state.group(1)} -- an S2 FAIL either stops the playbook "
            f"or is downgraded; the doc must say which the code does",
        )


class InstallerPythonDocsTest(unittest.TestCase):
    """QUICKSTART/README/README-mcp must state the Python floor declared in
    pyproject.toml and enforced by the installer (A3)."""

    def _requires_python(self) -> str:
        text = (REPO / "tools" / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'(?m)^requires-python\s*=\s*"([^"]+)"\s*$', text)
        self.assertIsNotNone(m, "requires-python vanished from pyproject.toml")
        return m.group(1)

    def _declared_floor(self) -> str:
        req = self._requires_python()
        m = re.fullmatch(r">=3\.(\d+)", req)
        self.assertIsNotNone(
            m, f"requires-python is not a simple >=3.N floor: {req!r}")
        return f"3.{m.group(1)}"

    def _installer_floor(self) -> str:
        ps1 = (REPO / "tools" / "install-mcp.ps1").read_text(encoding="utf-8")
        m = re.search(
            r'\$MinPythonVersion\s*=\s*\[version\]\s*"3\.(\d+)"', ps1)
        self.assertIsNotNone(
            m, "installer $MinPythonVersion constant moved in install-mcp.ps1")
        self.assertIsNone(
            re.search(r"& \$py\.Source -3\.\d+", ps1),
            "installer still pins an exact py -3.N; it must select >= floor")
        return f"3.{m.group(1)}"

    def _assert_floor_prose(self, rel: str) -> None:
        floor = self._installer_floor()
        self.assertEqual(self._declared_floor(), floor)
        doc = _doc(rel)
        self.assertIn(
            f"{floor} or newer", doc,
            f"{rel} must state the Python floor as '{floor} or newer'")
        self.assertNotRegex(
            doc, r"3\.10\+|3\.10 or newer",
            f"{rel} still claims 3.10 works")
        self.assertNotRegex(
            doc,
            r"not reconciled|explains the split|effective requirement",
            f"{rel} still documents a packaging/installer split")

    def test_quickstart_states_python_floor(self) -> None:
        self._assert_floor_prose("QUICKSTART.md")

    def test_readme_states_python_floor(self) -> None:
        self._assert_floor_prose("README.md")

    def test_readme_mcp_states_python_floor(self) -> None:
        self._assert_floor_prose("tools/README-mcp.md")


class SparseAddonDocsTest(unittest.TestCase):
    """When this checkout sparse-excludes addon/, the entry docs must say so
    (A4). Ground truth: .git/info/sparse-checkout + working tree state."""

    def _sparse_excludes_addon(self) -> bool:
        sparse = REPO / ".git" / "info" / "sparse-checkout"
        return (sparse.is_file()
                and "!/addon/" in sparse.read_text(encoding="utf-8")
                and not (REPO / "addon").exists())

    def test_quickstart_discloses_sparse_addon(self) -> None:
        if not self._sparse_excludes_addon():
            self.skipTest("addon/ present or not sparse-excluded (public clone)")
        self.assertIn(
            "sparse", _doc("QUICKSTART.md").lower(),
            "this tree sparse-excludes addon/ but QUICKSTART's step 3 points "
            "at P:\\addon without disclosing it; a new agent looks for a "
            "folder that is not in this checkout")

    def test_readme_discloses_sparse_addon(self) -> None:
        if not self._sparse_excludes_addon():
            self.skipTest("addon/ present or not sparse-excluded (public clone)")
        self.assertIn(
            "sparse", _doc("README.md").lower(),
            "README's addon/ row must disclose that the dev tree "
            "sparse-excludes it")


class LogsSinceTailCapDocsTest(unittest.TestCase):
    """logs_since docs (prose and tool description) must state the per-file
    tail cap with the number log_tail.py actually enforces (A5)."""

    def _max_tail_kib(self) -> int:
        m = re.search(r"MAX_TAIL_BYTES = (\d+) \* 1024",
                      _module_text("dayz_mcp/log_tail.py"))
        self.assertIsNotNone(m, "MAX_TAIL_BYTES definition moved in log_tail.py")
        return int(m.group(1))

    def test_readme_mcp_states_tail_cap(self) -> None:
        text = _doc("tools/README-mcp.md")
        m = re.search(r"tail-capped at the last (\d+) KiB per file", text)
        self.assertIsNotNone(
            m, "README-mcp logs_since sentence lost the tail-cap clause; "
            "without it the doc promises the whole current launch")
        self.assertEqual(
            int(m.group(1)), self._max_tail_kib(),
            "README-mcp tail cap disagrees with log_tail.MAX_TAIL_BYTES")

    @slow_test
    def test_tool_description_states_tail_cap(self) -> None:
        tree = ast.parse(_doc("tools/dayz_mcp/server.py"))
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.AsyncFunctionDef)
                   and n.name == "logs_since"), None)
        self.assertIsNotNone(fn, "logs_since vanished from server.py")
        description = ""
        for dec in fn.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            for kw in dec.keywords:
                if kw.arg == "description":
                    description = ast.literal_eval(kw.value)
        m = re.search(r"capped at\s+its last (\d+) KiB", description)
        self.assertIsNotNone(
            m, "the logs_since tool description lost the tail-cap sentence; "
            "'reads the current launch (not the historic dump)' was the "
            "original lie -- the code reads the LAST 256 KiB of it")
        self.assertEqual(
            int(m.group(1)), self._max_tail_kib(),
            "logs_since description cap disagrees with log_tail.MAX_TAIL_BYTES")


class FreshCloneGreenClaimDocsTest(unittest.TestCase):
    """A green fresh clone was measured under one condition, so the README that
    claims it has to state that condition (A5).

    This replaces a check that required the README to name
    `test_bug046_startup_deadlock` as a known flake. It is not one: a clone with
    its own `tools/.venv-mcp` runs the suite green. The five errors that looked
    like a flake came from driving a COPY of the tree with the development venv,
    whose editable finder maps `dayz_mcp` back to the original checkout -- the
    daemon child then imports one tree while its cwd is another, and startup
    never converges. That is a property of the measuring rig, not of the code,
    and the README should not warn readers about it.

    What survives is the reason the old check existed: the colour claim must not
    be free of preconditions."""

    _CLAIM = "passes on a fresh clone"
    _PRECONDITION = "tools/.venv-mcp"

    def test_green_claim_states_the_environment_it_needs(self) -> None:
        readme = _doc("README.md")
        if self._CLAIM not in readme:
            self.skipTest("README makes no fresh-clone colour claim")
        self.assertIn(
            self._PRECONDITION, readme,
            "README says a fresh clone passes but never says the venv must be "
            "the installer's at tools/.venv-mcp; the daemon resolves that exact "
            "path and fails at startup from anywhere else")


class ReadmePathLineCitesDocsTest(unittest.TestCase):
    """Every path:line cite in README.md must resolve in this tree.

    Bare `file.c:N` (no directory) is a vanilla DayZ script and is listed,
    not failed. Unpublished trees (HANDOFF.md, reviews/, decisions/,
    poc-verdict.json) must not be mentioned at all.
    """

    def test_readme_path_line_cites_resolve_in_tree(self) -> None:
        checker = REPO / "tools" / "checks" / "check_readme_cites.py"
        if not checker.is_file():
            self.skipTest("tools/checks/check_readme_cites.py absent")
        spec = importlib.util.spec_from_file_location(
            "check_readme_cites", checker)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result = mod.check(readme=_doc_path("README.md"), repo=REPO)
        self.assertEqual(
            result["missing"], [],
            f"README cites files a clone does not have: {result['missing']}")
        self.assertEqual(
            result["short"], [],
            f"README cites a line past EOF: {result['short']}")
        self.assertEqual(
            result["unpublished"], [],
            f"README names unpublished paths: {result['unpublished']}")


class HandoffLineCiteDocsTest(unittest.TestCase):
    """README must not cite HANDOFF.md by line number (A6): the LIVE-STATE
    block is rewritten on every session close, so a line cite in it is
    drift-by-construction. Cite the section or the stable artifact instead."""

    def test_readme_cites_handoff_without_line_numbers(self) -> None:
        if not (REPO / "HANDOFF.md").is_file():
            self.skipTest("HANDOFF.md is internal and absent from a published tree")
        handoff = (REPO / "HANDOFF.md").read_text(
            encoding="utf-8", errors="replace")
        if "LIVE-STATE" not in handoff:
            self.skipTest("HANDOFF has no LIVE-STATE block; premise void")
        hits = re.findall(r"HANDOFF\.md:\d+(?:-\d+)?", _doc("README.md"))
        self.assertEqual(
            hits, [],
            f"README cites the volatile LIVE-STATE block by line ({hits}); "
            f"those numbers move on every session close -- cite the note by "
            f"name or the stable test artifact instead")


class VolatileCiteDocsTest(unittest.TestCase):
    """README must not cite server.py by line number (A10): server.py moved
    under its citations four times (audit C6 -> 2289 -> 2360 -> ...); the
    patch convention is symbol/section anchors.

    Deliberately NOT banned: README's line citations into
    tools/README-mcp.md (:124) and dayz-mcp-architecture.md (five ranges) --
    the patched README keeps all six because they were verified correct, and
    validating doc-to-doc line cites mechanically would need keyword
    heuristics, i.e. half-mechanization. Out of scope, named here."""

    def test_readme_avoids_volatile_line_citations(self) -> None:
        hits = re.findall(r"(?:dayz_mcp/)?server\.py:\d+", _doc("README.md"))
        self.assertEqual(
            hits, [],
            f"README line-cites server.py ({hits}); the file moves under "
            f"active development -- cite the tool description or constant by "
            f"symbol instead")


class ProductSpecVolatileCiteDocsTest(unittest.TestCase):
    """product-spec must anchor server.py/loopback.py claims by symbol, not
    by line (A8). server.py moved four times under these citations; the
    mod .c citations are NOT checked here (ground truth is git `addon/`,
    not the sibling tree; fb-20260925-233932-aa11)."""

    def test_product_spec_avoids_volatile_line_citations(self) -> None:
        hits = re.findall(r"(?:server|loopback)\.py:\d+",
                          _doc("product-spec.md"))
        self.assertEqual(
            hits, [],
            f"product-spec anchors server.py/loopback.py by line ({hits}); "
            f"those files move under active development -- anchor by symbol "
            f"(`async def ui_dialog`, `MAX_TIMEOUT_S`, `CLIENT_COMMANDS`, ...)")


class VanillaSymbolCitationsDocsTest(unittest.TestCase):
    """`Symbol` file.c:NNN in public docs must have the symbol at (or beside)
    the cited line of the vanilla scripts tree (A7). A run of N symbols
    sharing one citation with N line numbers is checked pairwise -- that is
    the shape the rotated aiworld citations had. Absent tree => named skip;
    files not found (or found twice) => not checked."""

    @slow_test
    def test_symbol_line_citations_resolve(self) -> None:
        if not VANILLA.is_dir():
            self.skipTest(f"vanilla scripts tree not present: {VANILLA}")
        cite_re = re.compile(r"\b([a-z0-9_]+\.c):(\d+(?:[\/,-]\d+)*)")
        span_re = re.compile(r"`([^`\n]+)`")
        token_re = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*(?:\([^`]*\))?\Z")
        sep_re = re.compile(r"[\s,/(—–]*`?\Z")

        def _symbols(token: str) -> list[str] | None:
            """A backticked token is a symbol list: `A` / `A.B(x)` / `A/B`."""
            parts = token.split("/")
            if parts and all(token_re.match(p) for p in parts):
                return parts
            return None

        docnames = _published_docs(
            ("README.md", "dayz-mcp-architecture.md", "CLAUDE.md")
        )
        if not docnames:
            self.skipTest("none of the cited documents is present in this tree")
        offenders: list[str] = []
        for docname in docnames:
            text = _doc(docname)
            cache: dict[str, list[list[str]]] = {}
            for m in cite_re.finditer(text):
                fname, nums_raw = m.group(1), m.group(2)
                if fname not in cache:
                    rows: list[list[str]] = []
                    for hit in VANILLA.rglob(fname):
                        rows.append(hit.read_text(
                            encoding="utf-8", errors="replace").splitlines())
                    cache[fname] = rows
                rows_any = cache[fname]
                if not rows_any:
                    continue  # not a vanilla file (mod, docs, ...) -- out of scope
                # backticked spans fully before the cite (a span that CONTAINS
                # the cite is the citation itself, not a symbol)
                seg_spans = [(s.start(), s.end(), s.group(1))
                             for s in span_re.finditer(text, max(0, m.start() - 200), m.start())
                             if s.end() <= m.start()]
                # maximal trailing run of adjacent symbols, then a fallback to
                # the single nearest symbol when prose separates them
                syms: list[str] = []
                prev_start = m.start()
                while seg_spans:
                    s_start, s_end, token = seg_spans[-1]
                    parts = _symbols(token)
                    if parts is None:
                        break
                    between = text[s_end:prev_start]
                    if not sep_re.match(between):
                        break
                    syms[:0] = parts
                    prev_start = s_start
                    seg_spans.pop()
                if not syms and seg_spans:
                    near_end, _, near_token = seg_spans[-1]
                    parts = _symbols(near_token)
                    if parts is not None and m.start() - near_end <= 80:
                        syms = parts
                if not syms:
                    continue  # cite with no adjacent symbol -- out of scope
                ranges = []
                for part in re.findall(r"\d+(?:-\d+)?", nums_raw):
                    if "-" in part:
                        a, b = part.split("-")
                    else:
                        a = b = part
                    ranges.append((int(a), int(b)))
                stems = [s.split(".")[-1].split("(")[0].strip() for s in syms]

                def in_window(rows: list[str], rng: tuple[int, int], stem: str) -> bool:
                    a, b = rng
                    return any(stem.lower() in row.lower()
                               for row in rows[max(0, a - 3):b + 2])

                def resolves(stem: str, rngs: list[tuple[int, int]]) -> bool:
                    return any(in_window(rows, rng, stem)
                               for rows in rows_any for rng in rngs)

                bad = False
                if len(syms) == len(ranges):
                    bad = not all(
                        resolves(stems[i], [ranges[i]]) for i in range(len(syms)))
                else:
                    bad = not all(resolves(st, ranges) for st in stems)
                if bad:
                    offenders.append(
                        f"{docname}: `{syms}` not at {fname}:{nums_raw}")
        self.assertEqual(
            offenders, [],
            "vanilla citations that do not resolve (symbol not at/beside the "
            f"cited line): {offenders}")


class HarnessApisArithmeticDocsTest(unittest.TestCase):
    """dayz-harness-apis.md must state its own measured unique-citation count
    in the reconciliation sentence (A9). The count is computed from the file
    itself; the harvest history (122 - 4 + 12) is a dated record and is not
    checked."""

    def test_header_states_measured_unique_citations(self) -> None:
        text = _doc("dayz-harness-apis.md")
        cites = set(re.findall(
            r"\b[a-z0-9_]+\.(?:c|cpp):\d+(?:-\d+)?(?:,\d+)*\b", text))
        stated = re.search(r"más citas `path:line` únicas \((\d+)\)", text)
        self.assertIsNotNone(
            stated,
            "the reconciliation sentence vanished: the header carries a "
            "unique-symbol catalog count while the file holds a different "
            "number of unique path:line citations, and nothing bridges them; "
            "state the measured count (like '(135)') or drop the catalog count")
        self.assertEqual(
            int(stated.group(1)), len(cites),
            f"the header says {stated.group(1)} unique citations; the file "
            f"carries {len(cites)}. Cites were added without updating the "
            f"sentence -- update it")


class ProjectMapHandoffCountsDocsTest(unittest.TestCase):
    """PROJECT-MAP must send the reader to HANDOFF.md's LIVE-STATE:END
    marker (A11). The live block is rewritten on every session close, so a
    pinned line count, Read limit, or KB size is stale by construction.
    The map names the marker; it does not pin a number."""

    def test_handoff_claims_are_current(self) -> None:
        path = _doc_path("PROJECT-MAP.md")
        if not path.is_file():
            self.skipTest("PROJECT-MAP.md not present (public clone)")
        text = _unmojibake(path.read_text(encoding="utf-8", errors="replace"))
        self.assertIn(
            "LIVE-STATE:END", text,
            "PROJECT-MAP must tell the reader to stop at LIVE-STATE:END; "
            "a line/KB figure for HANDOFF.md is stale by the next close",
        )
        handoff = (REPO / "HANDOFF.md").read_text(
            encoding="utf-8", errors="replace")
        self.assertTrue(
            "LIVE-STATE:END" in handoff,
            "PROJECT-MAP points at LIVE-STATE:END but HANDOFF.md has no "
            "such marker",
        )
        self.assertIsNone(
            re.search(r"`HANDOFF\.md` tiene \*\*\d+ líneas\*\*", text),
            "PROJECT-MAP pins a HANDOFF.md line count; that number moves "
            "on every session close -- name LIVE-STATE:END instead",
        )
        self.assertIsNone(
            re.search(r"el bloque vivo termina en la \*\*línea \d+\*\*", text),
            "PROJECT-MAP pins the LIVE-STATE end line; that number moves "
            "on every session close -- name LIVE-STATE:END instead",
        )
        self.assertIsNone(
            re.search(r"Read\(HANDOFF\.md, limit: \d+\)", text),
            "PROJECT-MAP pins a HANDOFF.md Read limit; that number moves "
            "on every session close -- name LIVE-STATE:END instead",
        )
        self.assertIsNone(
            re.search(r"^- `HANDOFF\.md` - ~?\d+ (KB|B), touched", text, re.M),
            "PROJECT-MAP pins a HANDOFF.md byte size; the LIVE-STATE "
            "block grows on every close so the figure is stale by "
            "construction",
        )


class ProjectMapEncodingDocsTest(unittest.TestCase):
    """PROJECT-MAP.md must be real UTF-8 Spanish, not mojibake (A11). A bare
    U+00C3 followed by a Latin-1 accent is double-encoding; legit Spanish
    prose cannot produce it. This is also what blocked the original patch."""

    def test_file_is_valid_utf8_spanish(self) -> None:
        path = _doc_path("PROJECT-MAP.md")
        if not path.is_file():
            self.skipTest("PROJECT-MAP.md not present (public clone)")
        text = path.read_text(encoding="utf-8", errors="replace")
        self.assertNotIn(
            "\u00c3", text,
            "PROJECT-MAP.md carries mojibake (Ã...): it was saved through a "
            "Latin-1 round trip. Fix the generator's encoding and "
            "regenerate; patches cannot even anchor against it")


class ProjectMapSizesDocsTest(unittest.TestCase):
    """PROJECT-MAP's docs-section size claims must match os.stat of the
    real files, within the rounding the map uses (KB = KiB, +/- 1; B exact).
    Dates in the same lines are deliberately not checked (clock, not tree).
    HANDOFF.md and CHANGELOG.md are exempt: HANDOFF's live block grows on
    every close and CHANGELOG.md is append-only and grows on every PR, so
    a pinned size for either is stale by construction; the map names a
    marker instead, and test_changelog_size_is_not_pinned guards that."""

    def test_doc_size_claims_within_rounding(self) -> None:
        path = _doc_path("PROJECT-MAP.md")
        if not path.is_file():
            self.skipTest("PROJECT-MAP.md not present (public clone)")
        text = _unmojibake(path.read_text(encoding="utf-8", errors="replace"))
        offenders = []
        for name, value, unit in re.findall(
                r"^- `([^`]+)` - ~?(\d+) (KB|B), touched", text, re.M):
            if name in ("HANDOFF.md", "CHANGELOG.md"):
                # Size is not a stable claim (LIVE-STATE grows every close;
                # CHANGELOG.md is append-only and grows on every PR).
                # ProjectMapHandoffCountsDocsTest forbids pinning HANDOFF's,
                # and test_changelog_size_is_not_pinned forbids CHANGELOG's.
                continue
            target = REPO / name
            if not target.is_file():
                offenders.append(f"{name}: listed but absent from the tree")
                continue
            size = target.stat().st_size
            if unit == "KB":
                if abs(int(value) - size / 1024) > 1:
                    offenders.append(
                        f"{name}: claims {value} KB, file is {size} B "
                        f"({size / 1024:.1f} KiB)")
            elif int(value) != size:
                offenders.append(
                    f"{name}: claims {value} B, file is {size} B")
        self.assertEqual(
            offenders, [],
            f"PROJECT-MAP size claims drifted: {offenders}. "
            f"Update PROJECT-MAP.md by hand")

    def test_changelog_size_is_not_pinned(self) -> None:
        path = _doc_path("PROJECT-MAP.md")
        if not path.is_file():
            self.skipTest("PROJECT-MAP.md not present (public clone)")
        text = _unmojibake(path.read_text(encoding="utf-8", errors="replace"))
        self.assertTrue(
            re.search(r"^- `CHANGELOG\.md` - ", text, re.M),
            "PROJECT-MAP must list CHANGELOG.md in its Docs section; "
            "the entry is marker-only, not absent",
        )
        self.assertIsNone(
            re.search(r"^- `CHANGELOG\.md` - ~?\d+ (KB|B)", text, re.M),
            "PROJECT-MAP pins a CHANGELOG.md byte size; CHANGELOG.md "
            "grows on every PR so the figure is stale by construction "
            "-- name the append-only marker instead",
        )


class WorldSpawnNopersistFlagDocsTest(unittest.TestCase):
    """The world_spawn example flag is the sum of the two ECE constants.

    The prose states one integer. That integer is computed from core.py,
    so a copied example that drifts from the constants fails here.
    """

    def test_stated_nopersist_flag_equals_constant_sum(self) -> None:
        core = _module_text("dayz_mcp/core.py")
        place = re.search(r"^ECE_PLACE_ON_SURFACE = (\d+)$", core, re.M)
        noper = re.search(r"^ECE_NOPERSISTENCY_WORLD = (\d+)$", core, re.M)
        self.assertIsNotNone(place, "ECE_PLACE_ON_SURFACE moved in core.py")
        self.assertIsNotNone(noper, "ECE_NOPERSISTENCY_WORLD moved in core.py")
        expected = int(place.group(1)) + int(noper.group(1))
        from dayz_mcp.server import WORLD_SPAWN_FLAGS_LINE

        self.assertIn(f"flags={expected}", WORLD_SPAWN_FLAGS_LINE)
        self.assertIn(
            "ECE_PLACE_ON_SURFACE|ECE_NOPERSISTENCY_WORLD",
            WORLD_SPAWN_FLAGS_LINE,
        )
        self.assertIn("Without ECE_NOPERSISTENCY_WORLD", WORLD_SPAWN_FLAGS_LINE)
        self.assertIn("object_id is no longer valid", WORLD_SPAWN_FLAGS_LINE)
        self.assertNotIn("not verified in game", WORLD_SPAWN_FLAGS_LINE)
        tree = ast.parse(_module_text("dayz_mcp/server.py"))
        fn = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef)
                and node.name == "object_delete"
            ),
            None,
        )
        self.assertIsNotNone(fn, "object_delete vanished from server.py")
        description = ""
        for dec in fn.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            for kw in dec.keywords:
                if kw.arg == "description":
                    description = ast.literal_eval(kw.value)
        self.assertIn(
            "Spawn with ECE_NOPERSISTENCY_WORLD so the object is not saved "
            "into a later run.",
            description,
        )
        self.assertIn("does not survive the run", description)
        self.assertNotIn("not verified in game", description)


class ProjectMapEntryPointsDocsTest(unittest.TestCase):
    """Every entry point PROJECT-MAP lists must exist under tools/ (A11)."""

    def test_entry_points_exist(self) -> None:
        path = _doc_path("PROJECT-MAP.md")
        if not path.is_file():
            self.skipTest("PROJECT-MAP.md not present (public clone)")
        text = _unmojibake(path.read_text(encoding="utf-8", errors="replace"))
        m = re.search(
            r"## Build / test entry points\n(.*?)\n## ", text, re.S)
        self.assertIsNotNone(m, "entry-points section moved in PROJECT-MAP")
        names = re.findall(r"^- `([^`]+)`", m.group(1), re.M)
        self.assertNotEqual(names, [], "entry-points section emptied")
        ghosts = [n for n in names if not (_TOOLS / n).exists()]
        self.assertEqual(
            ghosts, [],
            f"PROJECT-MAP lists entry points that do not exist: {ghosts}. "
            f"Update PROJECT-MAP.md by hand or delete the stale lines")


def _tool_description(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                for kw in dec.keywords:
                    if kw.arg == "description":
                        return ast.literal_eval(kw.value)
    raise AssertionError(f"{name} description not found")


class InputDescribeExistsRuleDocsTest(unittest.TestCase):
    """exists is the engine index, not a non-null GetInputByName.

    Ground truth is DispatchInputDescribe's `inputId >= 0` guard. The tool
    description and the MCPInputDescribe contract comment state that rule,
    the measured 1.29 shared placeholder (index -1), and that the probe is
    published for the placeholder. The retracted caveat is gone from both.
    """

    def test_texts_state_the_index_rule_the_guard_implements(self) -> None:
        bridge = (
            REPO / "addon" / "scripts" / "5_Mission" / "MCPClientBridge.c"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            bridge.count("if (inputId >= 0)"),
            1,
            "exists is not gated on input.ID() >= 0 in DispatchInputDescribe",
        )
        self.assertIn("int inputId = input.ID();", bridge)
        messages = (
            REPO / "addon" / "scripts" / "5_Mission" / "MCPMessages.c"
        ).read_text(encoding="utf-8")
        description = _tool_description(
            _doc("tools/dayz_mcp/server.py"), "input_describe"
        )
        retracted = "an unknown name can also return exists true"
        for label, text in (
            ("MCPMessages.c", messages),
            ("input_describe description", description),
        ):
            self.assertIn("input.ID() >= 0", text, label)
            self.assertIn("shared placeholder whose index is -1", text, label)
            self.assertIn(
                "including that placeholder, so a caller can see why exists is false",
                text,
                label,
            )
            self.assertIn("uainput.c:25", text, label)
            self.assertNotIn(retracted, text, label)
        self.assertIn(
            "exists is true only when GetInputByName returns non-null and "
            "input.ID() >= 0",
            description,
        )


class NamespaceSourceMismatchDocsTest(unittest.TestCase):
    """222d: the new code, the tool description, and the identity/namespace split."""

    def test_code_description_and_readme_agree(self) -> None:
        from dayz_mcp import dayz_test_worker
        from dayz_mcp.dayz_test_tool import _NAMESPACE_SOURCE_MISMATCH_REMEDIATION

        self.assertIn(
            "build_namespace_source_mismatch",
            dayz_test_worker.WORKER_ERROR_CODES,
        )
        server = _doc("tools/dayz_mcp/server.py")
        description = server[server.index("async def dayz_test_run") - 2500 : server.index("async def dayz_test_run")]
        self.assertIn("build_namespace_source_mismatch", description)
        self.assertIn("ignoring ASCII case", description)
        self.assertIn("pack_only=true", description)
        self.assertIn("binarizable assets", description)
        readme = _doc("README.md")
        self.assertIn("PBO file name is not the `$PBOPREFIX$` namespace", readme)
        self.assertIn("$PBOPREFIX$", readme)
        self.assertIn("build_namespace_source_mismatch", readme)
        self.assertIn(_NAMESPACE_SOURCE_MISMATCH_REMEDIATION, readme)


_SERVERMOD_DOC = "docs/SERVERMOD.md"
_SERVERMOD_HEADING = "## Qué tools dejan de funcionar"
# The one list form the reader accepts: "- Label: `tool_a`, `tool_b`", with a
# plain-text label (so the item cannot be read as a link reference definition,
# a nested list, a heading or a quote).
_SERVERMOD_ITEM = re.compile(
    r"- [^\W_][\w ,/()-]*: `[a-z0-9_]+`(?:, `[a-z0-9_]+`)*[ \t]*"
)
# Any CommonMark list marker, bullet or ordered, so an item written in another
# form is caught instead of being skipped.
_SERVERMOD_MARKER = re.compile(r" {0,3}(?:[-*+]|[0-9]{1,9}[.)])(?:[ \t]|$)")
# A line opening or closing a fenced code block, which would turn the list into
# code.
_SERVERMOD_FENCE = re.compile(r" {0,3}(?:`{3,}|~{3,})")
# A level-1 or level-2 heading ends the section.
_SERVERMOD_SECTION_END = re.compile(r"#{1,2}[ \t]")
# Raw HTML that hides text or stops the Markdown after it from being parsed
# (CommonMark 0.31.2 HTML block types 1-5): comments, processing instructions,
# declarations and CDATA anywhere on a line, and a line that opens a script,
# pre, style or textarea block. Code spans are not read, so these markers are
# refused inside backticks too.
_SERVERMOD_HIDING_HTML = re.compile(
    r"<!--|<\?|<![A-Za-z]|<!\[CDATA\["
    r"|^ {0,3}<(?:script|pre|style|textarea)(?:[ \t>]|$)",
    re.IGNORECASE,
)
_SERVERMOD_FORM = (
    "write the list as one block of lines '- Label: `tool_a`, `tool_b`' "
    "(marker '-', a plain-text label of letters, digits, spaces and , / ( ) -, "
    "one item per line, plain tool names in single backticks, a blank line "
    "before and after the block)"
)


class ServerModClientToolsDocsTest(unittest.TestCase):
    """docs/SERVERMOD.md client-bridge tool list must match the code (964e).

    Ground truth is CLIENT_COMMANDS intersected with the MCP tool names parsed
    from server.py, plus the MCP tools whose own execution path reaches a client
    bridge command under another name (tools that only reach owner cleanup via
    the lease-expiry sweep are excluded). Like LeaseListDocsTest, the reader
    anchors the section and takes the names in backticks. It accepts one list
    form only; any other form (another marker, a label that is not plain text,
    a wrapped or continued item, an item paragraph, an indented line, a code
    fence before the end of the list, an HTML marker that can hide text, even
    inside backticks) fails naming the line and the expected form instead of
    being interpreted. The section ends at the next level-1 or level-2 heading.
    """

    def test_client_bridge_tool_list_matches_code(self) -> None:
        from dayz_mcp.loopback import CLIENT_COMMANDS

        indirect_client_tools: dict[str, str] = {
            "object_delete": (
                "_client_seat_refusal probes vehicle_telemetry on client; "
                "fails whole with vehicle_seat_unverified without client bridge"
            ),
            "playbook_run": (
                "runs playbooks whose steps call ui_dialog (ask_restart) and "
                "vehicle_telemetry (lease_spawn_prepare_trace)"
            ),
            "player_teleport": (
                "probes vehicle_telemetry on client when probeable; skips only "
                "that seat precheck without client bridge"
            ),
            "scene_clear": (
                "_client_seat_refusal probes vehicle_telemetry on client; "
                "fails whole with vehicle_seat_unverified without client bridge"
            ),
            "session_release": (
                "explicit release runs cleanup_owner: with a lease-end round "
                "the client close needs the client census (close_support_unknown "
                "leaves its row owed); without one, when vehicle_active it "
                "enqueues internal vehicle_release with no consumer until TTL "
                "or binding retirement"
            ),
        }
        lines = _doc(_SERVERMOD_DOC).split("\n")
        heads = [
            i for i, line in enumerate(lines) if line.rstrip() == _SERVERMOD_HEADING
        ]
        self.assertEqual(
            len(heads),
            1,
            f"{_SERVERMOD_DOC} must have exactly one line '{_SERVERMOD_HEADING}' "
            f"(found {len(heads)}); the list is read under that heading",
        )
        start = heads[0]
        end = next(
            (
                i
                for i in range(start + 1, len(lines))
                if _SERVERMOD_SECTION_END.match(lines[i])
            ),
            len(lines),
        )
        hiding = [
            f":{i + 1} {lines[i].strip()}"
            for i in range(end)
            if _SERVERMOD_HIDING_HTML.search(lines[i])
        ]
        self.assertEqual(
            hiding,
            [],
            f"HTML comment, processing instruction, declaration or CDATA markers "
            f"(even inside backticks: this test does not read code spans), and "
            f"lines that open a script, pre, style or textarea block, are not "
            f"supported in {_SERVERMOD_DOC} up to the end of "
            f"'{_SERVERMOD_HEADING}', because they can hide the heading or the "
            f"list, or stop them from being read as Markdown: {hiding}; remove or "
            f"rephrase them (outside backticks, '<' can be written '&lt;'), and "
            f"{_SERVERMOD_FORM}",
        )
        section = range(start + 1, end)
        indented = [
            f":{i + 1} {lines[i]}"
            for i in section
            if lines[i][:1] in (" ", "\t") and lines[i].strip()
        ]
        self.assertEqual(
            indented,
            [],
            f"indented lines are not supported under '{_SERVERMOD_HEADING}' "
            f"(they would continue a list item or open a code block): "
            f"{indented}; {_SERVERMOD_FORM}",
        )
        items = [i for i in section if _SERVERMOD_MARKER.match(lines[i])]
        self.assertNotEqual(
            items, [], f"no list under '{_SERVERMOD_HEADING}'; {_SERVERMOD_FORM}"
        )
        fences = [
            f":{i + 1} {lines[i]}"
            for i in range(start + 1, items[-1] + 1)
            if _SERVERMOD_FENCE.match(lines[i])
        ]
        self.assertEqual(
            fences,
            [],
            f"fenced code blocks (``` or ~~~) are not supported between "
            f"'{_SERVERMOD_HEADING}' and the end of its list, because they turn "
            f"the list into code: {fences}; {_SERVERMOD_FORM}",
        )
        inside = [
            f":{i + 1} {lines[i]}"
            for i in range(items[0], items[-1] + 1)
            if i not in items
        ]
        self.assertEqual(
            inside,
            [],
            f"the list under '{_SERVERMOD_HEADING}' must be one block of "
            f"consecutive item lines; found other lines inside it: {inside}; "
            f"{_SERVERMOD_FORM}",
        )
        around = [
            f":{i + 1} {lines[i]}"
            for i in (items[0] - 1, items[-1] + 1)
            if start < i < end and lines[i].strip()
        ]
        self.assertEqual(
            around,
            [],
            f"the list under '{_SERVERMOD_HEADING}' needs a blank line before "
            f"and after it; a line next to an item continues it: {around}; "
            f"{_SERVERMOD_FORM}",
        )
        malformed = [
            f":{i + 1} {lines[i]}"
            for i in items
            if not _SERVERMOD_ITEM.fullmatch(lines[i])
        ]
        self.assertEqual(
            malformed,
            [],
            f"list lines under '{_SERVERMOD_HEADING}' in an unsupported form: "
            f"{malformed}; {_SERVERMOD_FORM}",
        )
        listed = set(re.findall(r"`([a-z0-9_]+)`", "\n".join(lines[i] for i in items)))
        tool_names = _tool_names()
        self.assertTrue(
            set(indirect_client_tools) <= tool_names,
            f"indirect_client_tools names absent from server.py: "
            f"{sorted(set(indirect_client_tools) - tool_names)}",
        )
        expected = (CLIENT_COMMANDS & tool_names) | set(indirect_client_tools)
        self.assertEqual(
            listed,
            expected,
            f"docs/SERVERMOD.md omits {sorted(expected - listed)} from "
            f"'Qué tools dejan de funcionar' and lists "
            f"{sorted(listed - expected)} which do not send a client bridge "
            f"command",
        )



# PX9 P2 (#343): since bridge v13 a lease-end round's aggregated close replaces
# the per-lease vehicle_release (cleanup_owner, plan section 2.14), so
# docs/SERVERMOD.md must tie that release to a lease end without a round.
_SERVERMOD_ROUND_GATE = "if vehicle_active and not has_round:"


def _servermod_release_paragraphs(text: str) -> list[str]:
    """The paragraphs of docs/SERVERMOD.md on the internal vehicle_release."""
    return [
        block
        for block in text.split("\n\n")
        if "`vehicle_release`" in block and "interno" in block
    ]


# PX9 b, c and d: three readers of Spanish negation in turn let a wrong
# wording through or turned a true one away ("lo encola", "nunca encola", "no
# siempre lo encola", "no obstante"), so the check no longer reads the prose.
# It pins each paragraph as reviewed in PX9 to PX9 d, with whitespace folded
# and the line numbers of its path:line citations set to 0 (#386's citations
# move with every change to the files they cite). The path and the colon stay
# pinned, so a citation that loses its line numbers fails (PX9 e). A change of
# wording, true or false, fails here and goes back to review; the code side is
# pinned by test_code_gates_the_release_on_no_round.
_SERVERMOD_LINE_CITE = re.compile(r"(`[\w./-]+\.(?:py|c|md|toml)):\d+(?:-\d+)?(?=`)")
_SERVERMOD_RELEASE_SHA256 = (
    # The explicit session_release paragraph.
    "83ac75eb794189918775eab8214b1b6092f06ddab8c00c3c174b2d04515c132f",
    # The expired-lease paragraph.
    "0f6a66eed9db545b2d8df4e69c7c1ef14e40158048affcf2e67f5a428809c20f",
)


def _servermod_release_text(paragraph: str) -> str:
    """The paragraph as pinned: whitespace folded, citation line numbers 0."""
    return _SERVERMOD_LINE_CITE.sub(r"\1:0", " ".join(paragraph.split()))


def _servermod_release_digest(paragraph: str) -> str:
    return hashlib.sha256(_servermod_release_text(paragraph).encode("utf-8")).hexdigest()


class ServerModVehicleReleaseRoundDocsTest(unittest.TestCase):
    """docs/SERVERMOD.md's two paragraphs on the internal vehicle_release (an
    explicit session_release and an expired lease) must say it is enqueued only
    without a lease-end round, as cleanup_owner does since v13 (PX9 P2), in
    the wording _SERVERMOD_RELEASE_SHA256 pins (PX9 d). The
    behaviour itself is pinned by test_session_e2e
    test_lease_close_cleanup_is_enqueued_exactly_once (a round: 0 enqueued) and
    test_session_http_contract
    test_release_enqueues_internal_vehicle_release_without_wire_owner_metadata
    (no round: 1 enqueued)."""

    def test_code_gates_the_release_on_no_round(self) -> None:
        self.assertEqual(
            _module_text("dayz_mcp/loopback.py").count(_SERVERMOD_ROUND_GATE), 1
        )

    def _release_paragraphs(self) -> list[str]:
        paragraphs = _servermod_release_paragraphs(_doc(_SERVERMOD_DOC))
        self.assertEqual(len(paragraphs), 2, [p[:80] for p in paragraphs])
        return paragraphs

    def test_both_paragraphs_read_as_reviewed(self) -> None:
        for paragraph, digest in zip(self._release_paragraphs(), _SERVERMOD_RELEASE_SHA256):
            with self.subTest(digest=digest[:12]):
                self.assertEqual(
                    _servermod_release_digest(paragraph),
                    digest,
                    "a paragraph of docs/SERVERMOD.md on the internal vehicle_release "
                    "changed: check that it still says the release is enqueued only "
                    f"without a lease-end round ({_SERVERMOD_ROUND_GATE!r} in loopback.py), "
                    "then pin the digest of: " + _servermod_release_text(paragraph),
                )

    def test_a_moved_line_number_keeps_the_pin(self) -> None:
        explicit = self._release_paragraphs()[0]
        moved = explicit.replace("loopback.py:8384-8393", "loopback.py:9378-9390")
        self.assertNotEqual(moved, explicit)
        self.assertEqual(_servermod_release_digest(moved), _SERVERMOD_RELEASE_SHA256[0])

    def test_neg_a_changed_wording_fails(self) -> None:
        paragraphs = [" ".join(p.split()) for p in self._release_paragraphs()]
        without = "Sin ronda, `cleanup_owner` encola"
        mutants = (
            # PX9: an enqueue at every lease end.
            (0, without + " `vehicle_release`", "`cleanup_owner` encola `vehicle_release`"),
            # PX9 b: the round case enqueues the release.
            (0, "`cleanup_owner` no lo encola", "`cleanup_owner` lo encola"),
            # PX9 b: enqueued with and without a round.
            (
                1,
                "interno solo se encola sin ronda de cierre",
                "interno se encola con y sin ronda de cierre",
            ),
            # PX9 c: the case without a round denies the release.
            (0, without, "Sin ronda, `cleanup_owner` nunca encola"),
            # PX9 d: a partial negation added for the round case.
            (0, without, "Con ronda, `cleanup_owner` no siempre lo encola. " + without),
            # PX9 d: a true rewording fails too, and goes back to review.
            (0, without, "Sin ronda, no obstante, `cleanup_owner` encola"),
            # The release left queued with a round.
            (1, "Sin ronda, el release queda", "Con ronda, el release queda"),
            # A citation's path stays pinned; only its line numbers move.
            (0, "`tools/dayz_mcp/loopback.py:3263`", "`tools/dayz_mcp/server.py:3257`"),
            # PX9 e: a citation that loses its line numbers, in each paragraph.
            (0, "`tools/dayz_mcp/loopback.py:3263`", "`tools/dayz_mcp/loopback.py`"),
            (
                1,
                "`tools/dayz_mcp/session_coordination.py:2366-2368`",
                "`tools/dayz_mcp/session_coordination.py`",
            ),
        )
        for index, old, new in mutants:
            with self.subTest(new=new):
                paragraph = paragraphs[index]
                self.assertEqual(paragraph.count(old), 1, old)
                self.assertNotEqual(
                    _servermod_release_digest(paragraph.replace(old, new)),
                    _SERVERMOD_RELEASE_SHA256[index],
                )

    def test_neg_citations_without_line_numbers_fail(self) -> None:
        # PX9 e: every citation of a paragraph loses its line numbers.
        for index, paragraph in enumerate(self._release_paragraphs()):
            with self.subTest(index=index):
                bare, count = _SERVERMOD_LINE_CITE.subn(r"\1", paragraph)
                self.assertGreater(count, 0)
                self.assertNotEqual(
                    _servermod_release_digest(bare), _SERVERMOD_RELEASE_SHA256[index]
                )


# PX9 c P2 (#343): with a lease-end round, a reachable peer whose fresh census
# does not announce the close gets neither a close nor a debt
# (close_not_supported, the bridge 12 exemption of the plan's section 2.4), so
# product-spec H5 may not promise one or the other for every known peer.
_CLOSE_NOT_SUPPORTED_SETTLES = re.compile(
    r'if reason == "close_not_supported":\s+self\._settle_row_locked\(row\)'
)
_H5_EXEMPTION_TEST = "test_f1_client_not_supported_server_published"


def _h5_names_the_exemption(row: str) -> bool:
    return "`close_not_supported`" in row and f"`{_H5_EXEMPTION_TEST}`" in row


class ProductSpecH5LeaseEndDocsTest(unittest.TestCase):
    """product-spec H5 names the exemption and the test that pins it (PX9 c
    P2): loopback answers close_not_supported for a fresh census without the
    close name, and session_coordination settles that row with no debt, at the
    hook's report and at a deferred server publish."""

    def test_code_settles_close_not_supported(self) -> None:
        self.assertEqual(
            _module_text("dayz_mcp/loopback.py").count('return False, "close_not_supported"'),
            1,
        )
        coordination = _module_text("dayz_mcp/session_coordination.py")
        self.assertEqual(len(_CLOSE_NOT_SUPPORTED_SETTLES.findall(coordination)), 2)
        self.assertIn(
            f"def {_H5_EXEMPTION_TEST}(", _module_text("tests/test_lease_close_broker.py")
        )

    def _h5(self) -> str:
        rows = [
            line for line in _doc("product-spec.md").splitlines() if line.startswith("| H5 |")
        ]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def test_h5_names_the_exemption(self) -> None:
        self.assertTrue(_h5_names_the_exemption(self._h5()))

    def test_neg_a_close_or_debt_for_every_peer_fails(self) -> None:
        row = self._h5()
        # Back to the promise of 97f54e7: a close per known peer, or a debt.
        for old, new in (
            (
                "publican un cierre a cada peer conocido que alcanzan y cuyo censo lo anuncia; "
                "la fila de uno alcanzable cuyo censo fresco no lo anuncia se salda sin cierre "
                "ni deuda (`close_not_supported`, la exención del bridge 12, que no confirma "
                "que el juego quede limpio), y la de cualquier otro al que el cierre no llega "
                "queda debida",
                "publican un cierre por peer conocido y la fila del que no se alcanza queda debida",
            ),
            (
                ", mientras que un censo fresco sin el cierre la salda sin publicarlo "
                f"(`{_H5_EXEMPTION_TEST}`)",
                "",
            ),
        ):
            self.assertEqual(row.count(old), 1, old)
            row = row.replace(old, new)
        self.assertFalse(_h5_names_the_exemption(row))


if __name__ == "__main__":
    unittest.main()
