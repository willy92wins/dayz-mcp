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
    the sibling mod tree -- which is why product-spec's `MCP*.c:NNN` citations
    are NOT checked here: their ground truth is not in this checkout.
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
import os
import re
import sys
import unittest
from pathlib import Path

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


class FreshCloneFlakeDocsTest(unittest.TestCase):
    """README's fresh-clone colour claim must name the known flake the tree
    actually carries (A5)."""

    def test_readme_names_the_known_flake(self) -> None:
        if not (REPO / "tools" / "tests" / "test_bug046_startup_deadlock.py"
                ).is_file():
            self.skipTest("known-flake test absent; README claim unconstrained")
        readme = _doc("README.md")
        self.assertNotIn(
            "green rather than red", readme,
            "README promises an unqualified green fresh clone while the tree "
            "carries a known startup flake; name it or drop the promise")
        self.assertIn(
            "test_bug046_startup_deadlock", readme,
            "README discusses fresh-clone colour but does not name the known "
            "flake test the tree carries")


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
    mod .c citations are NOT checked here (ground truth lives in the sibling
    mod tree, outside this checkout)."""

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
    Dates in the same lines are deliberately not checked (clock, not tree)."""

    def test_doc_size_claims_within_rounding(self) -> None:
        path = _doc_path("PROJECT-MAP.md")
        if not path.is_file():
            self.skipTest("PROJECT-MAP.md not present (public clone)")
        text = _unmojibake(path.read_text(encoding="utf-8", errors="replace"))
        offenders = []
        for name, value, unit in re.findall(
                r"^- `([^`]+)` - ~?(\d+) (KB|B), touched", text, re.M):
            if name == "HANDOFF.md":
                # Size is not a stable claim (LIVE-STATE grows every close).
                # ProjectMapHandoffCountsDocsTest forbids pinning it.
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
            f"Regenerate the map")


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
            f"Regenerate the map or delete the stale lines")


if __name__ == "__main__":
    unittest.main()
