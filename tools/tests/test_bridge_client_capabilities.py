"""Client capability announcement (`caps=` on every poll) vs the Enforce dispatcher.

The census the client bridge announces must be exactly the set of `command.cmd`
branches its Dispatch() handles before the terminal `unknown_command` else.
Both sides are read from MCPClientBridge.c; nothing here derives an expected
list from the Python ingress or from the registered MCP tools, and the three
names the ticket makes mandatory for the client are spelled out.
"""
from __future__ import annotations

import re
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import (
    CAPS_DECL_RE,
    LITERAL_RE,
    MAX_LITERAL_CHARS,
    announced_caps,
    announced_literals,
    dispatch_census,
)
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceMap,
    EnforceString,
    assert_no_cache_writes_or_mutations,
    clean,
    method_body,
    translate,
)


BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
CONSTRUCTOR_SIGNATURE = "void MCPClientBridge()"
START_POLL_SIGNATURE = "protected void StartPoll()"

# Mandatory members of the client announcement (fb-20260829-023649-8f8c, step 8).
REQUIRED_CLIENT_CAPS = ("key_press", "player_respawn", "ui_dialog")

CACHE_DECL = "protected string m_PollCapabilities;"
CACHE_INIT = "m_PollCapabilities = EncodeQueryValue(CLIENT_POLL_CAPS);"
ANNOUNCE_LINE = 'request = request + "&caps=" + m_PollCapabilities;'
_STRING_LITERAL_RE = re.compile(r'"(?:\\.|[^"\\\n])*"')
_CONST_STRING_DECL_RE = re.compile(r"\bconst\s+string\s+([A-Za-z_]\w*)\s*=\s*([^;]+);")
_CONST_INT_DECL_RE = re.compile(r"\bconst\s+int\s+([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*;")
_CONST_FLOAT_DECL_RE = re.compile(r"\bconst\s+float\s+([A-Za-z_]\w*)\s*=\s*(-?\d+(?:\.\d+)?)\s*;")
_DECLARED_MEMBER_RE = re.compile(
    r"\b(?:protected|private)\s+(?:static\s+)?(?P<ref>ref\s+)?"
    r"(?P<type>[A-Za-z_]\w*(?:\s*<[^;{}()=]*>)?)\s+(?P<name>m_\w+)\s*;"
)
# The cache write/mutation detector and its verified signature catalogues
# live in tests.enforce_subset_helpers (assert_no_cache_writes_or_mutations),
# shared with the server and arg-contract-hash gates so they cannot diverge.

_NON_CALLEE_KEYWORDS = frozenset({
    "case",
    "catch",
    "delete",
    "else",
    "for",
    "if",
    "in",
    "inout",
    "new",
    "out",
    "return",
    "sizeof",
    "switch",
    "throw",
    "typename",
    "while",
})

def _blank_strings(cleaned: str) -> str:
    return _STRING_LITERAL_RE.sub(lambda m: '"' + (" " * (len(m.group(0)) - 2)) + '"', cleaned)


def chunk_at_commas(names: list[str], limit: int = MAX_LITERAL_CHARS) -> list[str]:
    """Split a census into comma-terminated literal pieces no longer than `limit`."""
    pieces: list[str] = []
    current = ""
    for name in names:
        token = name + ","
        if current and len(current) + len(token) > limit:
            pieces.append(current)
            current = ""
        current += token
    pieces.append(current)
    pieces[-1] = pieces[-1][:-1]
    return pieces


def declaration(pieces: list[str]) -> str:
    return (
        "protected const string CLIENT_POLL_CAPS = "
        + " + ".join(f'"{piece}"' for piece in pieces)
        + ";"
    )


def verify_announcement_matches_dispatcher(source: str) -> None:
    census = dispatch_census(source)
    announced = announced_caps(source)
    for name in REQUIRED_CLIENT_CAPS:
        if name not in census:
            raise AssertionError(f"dispatcher census lacks mandatory {name!r}")
        if name not in announced:
            raise AssertionError(f"announcement lacks mandatory {name!r}")
    if set(announced) != set(census):
        missing = sorted(set(census) - set(announced))
        extra = sorted(set(announced) - set(census))
        raise AssertionError(f"announcement != dispatcher: missing={missing} extra={extra}")


def _execute_translated_client_poll(source: str, cleaned: str) -> list[str]:
    class WireString(EnforceString):
        def __add__(self, other):
            if isinstance(other, bool):
                return WireString(self.data + (b"true" if other else b"false"))
            if isinstance(other, (int, float)):
                return WireString(self.data + str(other).encode("ascii"))
            if isinstance(other, EnforceString):
                return WireString(self.data + other.data)
            return NotImplemented

        def __radd__(self, other):
            if isinstance(other, bool):
                return WireString((b"true" if other else b"false") + self.data)
            if isinstance(other, (int, float)):
                return WireString(str(other).encode("ascii") + self.data)
            return NotImplemented

        def __getitem__(self, index: int) -> "WireString":
            return self.Get(index)

        def Get(self, index: int) -> "WireString":
            return self.Substring(index, 1)

        def Trim(self) -> "WireString":
            return WireString(self.data.strip(b" \t\r\n"))

        def IndexOf(self, sample: EnforceString) -> int:
            return self.data.find(sample.data)

        def IndexOfFrom(self, start: int, sample: EnforceString) -> int:
            return self.data.find(sample.data, max(start, 0))

        def LastIndexOf(self, sample: EnforceString) -> int:
            return self.data.rfind(sample.data)

        def Contains(self, sample: EnforceString) -> bool:
            return sample.data in self.data

        def StartsWith(self, sample: EnforceString) -> bool:
            return self.data.startswith(sample.data)

        def EndsWith(self, sample: EnforceString) -> bool:
            return self.data.endswith(sample.data)

        def LengthUtf8(self) -> int:
            return len(self.text)

        def SubstringUtf8(self, start: int, length: int) -> "WireString":
            return WireString.of(self.text[start : start + length])

        def Hash(self) -> int:
            return zlib.crc32(self.data)

        def ToInt(self) -> int:
            m = re.match(rb"\s*[+-]?\d+", self.data)
            return int(m.group(0)) if m else 0

        def ToFloat(self) -> float:
            m = re.match(rb"\s*[+-]?\d+(?:\.\d+)?", self.data)
            return float(m.group(0)) if m else 0.0

        def Split(self, sample: EnforceString, output: EnforceArray) -> None:
            output.Clear()
            for part in (self.data.split(sample.data) if sample.data else [self.data]):
                output.Insert(WireString(part))

        def ToUpper(self) -> int:
            self.data = self.data.upper()
            return len(self.data)

        def ToLower(self) -> int:
            self.data = self.data.lower()
            return len(self.data)

        def Replace(self, sample: EnforceString, replace: EnforceString) -> int:
            if not sample.data:
                return 0
            count = self.data.count(sample.data)
            self.data = self.data.replace(sample.data, replace.data)
            return count

        def TrimInPlace(self) -> None:
            self.data = self.data.strip(b" \t\r\n")

        def Set(self, index: int, value: EnforceString) -> None:
            self.data = self.data[:index] + value.data[:1] + self.data[index + 1 :]

        def Insert(self, index: int, value: EnforceString) -> None:
            self.data = self.data[:index] + value.data + self.data[index:]

    masked = _blank_strings(cleaned)
    messages_text = clean((BRIDGE_PATH.parent / "MCPMessages.c").read_text(encoding="utf-8"))
    ver_match = re.search(r'\bconst\s+string\s+MCP_BRIDGE_VERSION\s*=\s*"([^"]+)"\s*;', messages_text)
    if ver_match is None:
        raise AssertionError("MCP_BRIDGE_VERSION missing in MCPMessages.c")
    polls: list[bytes] = []
    transport = SimpleNamespace(GET=lambda _cb, req: polls.append(req.data))
    ns: dict = {
        "_S": WireString,
        "array": EnforceArray,
        "map": EnforceMap,
        "IntDivide": lambda v, d: v // d if v >= 0 else -(-v // d),
        "g_Game": None,
        "Log": lambda *_args: None,
        "Print": lambda *_args: None,
        "PrintString": lambda *_args: None,
        "PrintToRPT": lambda *_args: None,
        "DPrint": lambda *_args: None,
        "MCP_BRIDGE_VERSION": WireString.of(ver_match.group(1)),
        "MCPCameraPath": object,
        "MCPJobRunner": lambda: SimpleNamespace(Count=lambda: 0),
        "MCPClientPollCallback": lambda _owner: object(),
        "GetPollVersion": lambda: WireString.of("12%7E1.30"),
        "LoadCounterQuery": lambda: WireString.of(""),
        "this": object(),
    }
    const_chunk_re = re.compile(r'"((?:\\.|[^"\\\n])*)"')
    escape_re = re.compile(r"\\(.)")
    escape_map = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}
    for match in _CONST_INT_DECL_RE.finditer(cleaned):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") <= 1:
            ns[match.group(1)] = int(match.group(2))
    for match in _CONST_FLOAT_DECL_RE.finditer(cleaned):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") <= 1:
            ns[match.group(1)] = float(match.group(2))
    for match in _CONST_STRING_DECL_RE.finditer(cleaned):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") <= 1:
            cname, expr = match.group(1), match.group(2)
            pieces = [
                escape_re.sub(lambda m: escape_map.get(m.group(1), m.group(1)), raw)
                for raw in const_chunk_re.findall(expr)
            ]
            glue = re.sub(r"\s+", "", const_chunk_re.sub("", expr))
            if not pieces or glue != "+" * (len(pieces) - 1):
                raise AssertionError(f"unsupported const string {cname}: {expr!r}")
            ns[cname] = WireString.of("".join(pieces))
    for match in _DECLARED_MEMBER_RE.finditer(cleaned):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") != 1:
            continue
        mtype = match.group("type").strip()
        mname = match.group("name")
        if match.group("ref") or "<" in mtype:
            ns[mname] = None
        elif mtype == "int":
            ns[mname] = 0
        elif mtype == "float":
            ns[mname] = 0.0
        elif mtype == "bool":
            ns[mname] = False
        elif mtype == "string":
            ns[mname] = WireString(b"")
        else:
            ns[mname] = None

    encode_sig = "protected string EncodeQueryValue(string value)"
    encode_body = re.sub(
        r"\b(\w+)\s*/\s*(\w+)",
        r"IntDivide(\1, \2)",
        method_body(source, encode_sig),
    )
    ns["EncodeQueryValue"] = translate(
        encode_sig + "\n{" + encode_body + "}\n", encode_sig, dict(ns)
    )
    base_sigs = (
        CONSTRUCTOR_SIGNATURE,
        "protected int NextLoadCounter(int value)",
        "protected int OutstandingWork()",
        "protected void EnsurePollCallback()",
        "protected void HoldPollCallback()",
        START_POLL_SIGNATURE,
    )
    for sig in base_sigs:
        body = method_body(source, sig)
        translate(
            sig + "\n{" + body + "}\n",
            sig,
            ns,
            members=set(re.findall(r"\bm_\w+", body)),
            class_types=("array", "MCPClientPollCallback"),
        )
    call_re = re.compile(r"(?<!\.)\b([A-Za-z_]\w*)\s*\(")
    helper_queue = [method_body(source, sig) for sig in (CONSTRUCTOR_SIGNATURE, START_POLL_SIGNATURE)]
    seen_helpers: set[str] = set()
    while helper_queue:
        body_text = helper_queue.pop(0)
        body_masked = _blank_strings(body_text)
        for call_match in call_re.finditer(body_masked):
            callee = call_match.group(1)
            if callee in ns or callee in seen_helpers or callee in _NON_CALLEE_KEYWORDS:
                continue
            if re.search(r"\bnew\s+$", body_masked[: call_match.start()]) is not None:
                continue
            seen_helpers.add(callee)
            sig_match = re.search(
                rf"((?:(?:protected|private|static|override)\s+)*[A-Za-z_]\w*\s+{re.escape(callee)}\s*\([^()]*\))\s*\{{",
                cleaned,
            )
            if sig_match is None:
                continue
            helper_sig = sig_match.group(1).strip()
            helper_body = method_body(source, helper_sig)
            translate(
                helper_sig + "\n{" + helper_body + "}\n",
                helper_sig,
                ns,
                members=set(re.findall(r"\bm_\w+", helper_body)),
                class_types=("array", "MCPClientPollCallback"),
            )
            helper_queue.append(helper_body)

    ns["MCPClientBridge"]()
    ns["m_Key"] = WireString.of("k")
    ns["m_PollCtx"] = transport
    ns["StartPoll"]()
    if len(polls) != 1:
        raise AssertionError(f"expected 1 client poll GET, got {len(polls)}")
    request = polls[0].decode("ascii")
    if not request.startswith("poll?"):
        raise AssertionError(f"unexpected client poll path: {request!r}")
    parsed = parse_qs(request[len("poll?") :], strict_parsing=True, keep_blank_values=True)
    if "caps" not in parsed or len(parsed["caps"]) != 1:
        raise AssertionError(f"missing single caps parameter in {request!r}")
    return parsed["caps"][0].split(",") if parsed["caps"][0] else []


def verify_poll_wire(source: str) -> None:
    cleaned = clean(source)
    masked = _blank_strings(cleaned)
    if cleaned.count(CACHE_DECL) != 1:
        raise AssertionError("MCPClientBridge must declare m_PollCapabilities once")
    class_at = cleaned.index("class MCPClientBridge")
    decl_at = cleaned.index(CACHE_DECL)
    if decl_at < class_at:
        raise AssertionError("m_PollCapabilities must be declared inside MCPClientBridge")
    decl_depth = masked[class_at:decl_at].count("{") - masked[class_at:decl_at].count("}")
    if decl_depth != 1 or not masked[:decl_at].rstrip().endswith(("{", ";", "}")):
        raise AssertionError("m_PollCapabilities must be a standalone class field")

    constructor = method_body(source, CONSTRUCTOR_SIGNATURE)
    constructor_masked = _blank_strings(constructor)
    if constructor.count(CACHE_INIT) != 1:
        raise AssertionError(f"MCPClientBridge() missing {CACHE_INIT!r}")
    init_at = constructor.index(CACHE_INIT)
    if constructor_masked[:init_at].count("{") != constructor_masked[:init_at].count("}"):
        raise AssertionError("m_PollCapabilities init in MCPClientBridge() must be unconditional")
    init_prefix = constructor_masked[:init_at].rstrip()
    if init_prefix != "" and not init_prefix.endswith((";", "}")):
        raise AssertionError("m_PollCapabilities init must not hang off a braceless condition")
    if re.search(r"\breturn\b", constructor_masked[:init_at]) is not None:
        raise AssertionError("no early return may precede m_PollCapabilities init")

    assert_no_cache_writes_or_mutations(masked, "m_PollCapabilities")

    start_poll = method_body(source, START_POLL_SIGNATURE)
    start_poll_masked = _blank_strings(start_poll)
    if "CLIENT_POLL_CAPS" in start_poll_masked:
        raise AssertionError("StartPoll must reuse cached m_PollCapabilities")
    for needle in ('"poll?peer=client&key="', '"&ver="', ANNOUNCE_LINE, "m_PollCtx.GET(m_PollCallback, request);"):
        if needle not in start_poll:
            raise AssertionError(f"StartPoll missing {needle!r}")
    if start_poll.count(ANNOUNCE_LINE) != 1:
        raise AssertionError("StartPoll must append m_PollCapabilities once")
    announce_at = start_poll.index(ANNOUNCE_LINE)
    if start_poll_masked[:announce_at].count("{") != start_poll_masked[:announce_at].count("}"):
        raise AssertionError("caps announcement in StartPoll must be unconditional")
    wire_prefix = start_poll_masked[:announce_at].rstrip()
    if wire_prefix != "" and not wire_prefix.endswith((";", "}")):
        raise AssertionError("caps announcement must not hang off a braceless condition")
    if announce_at > start_poll.index("m_PollCtx.GET(m_PollCallback, request);"):
        raise AssertionError("caps must be appended before the GET is issued")
    if start_poll.index('"&ver="') > announce_at:
        raise AssertionError("caps travel alongside ver (after it)")

    wire_caps = _execute_translated_client_poll(source, cleaned)
    if wire_caps != announced_caps(source):
        raise AssertionError("translated MCPClientBridge() + StartPoll() caps differ from CLIENT_POLL_CAPS")


# F04 (#305, Sol d2-r5): a by-value ErrorExString declared in another class must not
# hide the native out parameter (scripts/1_core/proto/endebug.c:84).
_FOREIGN_BY_VALUE_ERROR_EX_STRING = (
    "\nclass ReviewForeignErrors\n"
    "{\n"
    "\tvoid ErrorExString(string err, string str, int severity)\n"
    "\t{\n"
    "\t}\n"
    "}\n"
)
_SHADOWED_NATIVE_OUT = "must not be passed to out/inout parameter 1 of ErrorExString"


# F06 (#305, Sol d2-r6): a by-value Find declared in another class must not
# vouch for map.Find(key, out val) on an engine container (enscript.c:858).
_FOREIGN_BY_VALUE_FIND = (
    "\nclass ReviewForeignLookup\n"
    "{\n"
    "\tvoid Find(int key, string value)\n"
    "\t{\n"
    "\t}\n"
    "}\n"
)
_MAP_FIND_OUT = "must not be passed to out/inout parameter 1 of Find"


# F06 (#305, Sol d2-r6): on an engine receiver a source declaration vouches for
# nothing; GetProfileString(string name, out string value) is not catalogued
# (scripts/3_game/global/game.c:286), so a by-value homonym must not let it pass.
_FOREIGN_BY_VALUE_PROFILE = (
    "\nclass ReviewForeignProfile\n"
    "{\n"
    "\tvoid GetProfileString(string name, string value)\n"
    "\t{\n"
    "\t}\n"
    "}\n"
)
_UNVERIFIED_PROFILE = "passed to unverified callee GetProfileString"


# F08 (#305, Sol d2-r7): a source declaration with an out parameter vetoes the
# argument on any receiver; the global by-value Print (endebug.c:96) must not
# vouch for a static, chained or indexed Writer.Print(out string).
_FOREIGN_OUT_PRINT = (
    "\nclass ReviewCacheWriter\n"
    "{\n"
    "\tstatic void Print(out string value)\n"
    "\t{\n"
    '\t\tvalue = "broken";\n'
    "\t}\n\n"
    "\tstatic bool Contains(out string value)\n"
    "\t{\n"
    '\t\tvalue = "broken";\n'
    "\t\treturn true;\n"
    "\t}\n\n"
    "\tstatic ReviewCacheWriter GetWriter()\n"
    "\t{\n"
    "\t\treturn null;\n"
    "\t}\n"
    "}\n"
)
# Print on a receiver has no catalogued method signature (unverified);
# Contains has one by value, and the out declaration vetoes it.
_RECEIVER_OUT_PRINT = "unverified callee Print"
_RECEIVER_OUT_CONTAINS = "must not be passed to out/inout parameter 0 of Contains"
# F09: more arguments than any verified signature takes.
_TOO_MANY_ARGUMENTS = "no verified signature takes that many"


class BridgeClientCapabilitiesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = BRIDGE_PATH.read_text(encoding="utf-8")

    def test_dispatcher_census_reaches_unknown_command_and_holds_mandatory_names(self) -> None:
        census = dispatch_census(self.source)
        for name in REQUIRED_CLIENT_CAPS:
            self.assertIn(name, census)
        # ui_dialog is the last branch before the terminal else; a range that
        # stops at the header of the last branch would lose exactly this name.
        self.assertEqual(census[-1], "ui_dialog")

    def test_announcement_is_canonical_and_equals_the_dispatcher(self) -> None:
        verify_announcement_matches_dispatcher(self.source)
        announced = announced_caps(self.source)
        self.assertEqual(announced, sorted(set(announced)))
        # Only the comma separator is outside the query-unreserved set, so the
        # bridge's percent-encoder rewrites it as %2C and parse_qs restores it.
        self.assertRegex(",".join(announced), r"^[a-z0-9_,]+$")

    def test_poll_announces_caps_next_to_ver_and_inst(self) -> None:
        verify_poll_wire(self.source)

    def test_literal_pieces_stay_below_the_vanilla_ceiling(self) -> None:
        pieces = announced_literals(self.source)
        value = "".join(pieces)
        self.assertEqual(value, ",".join(announced_caps(self.source)))
        self.assertLessEqual(max(len(piece) for piece in pieces), MAX_LITERAL_CHARS)
        if len(value) > MAX_LITERAL_CHARS:
            self.assertGreaterEqual(len(pieces), 2)
        for piece in pieces[:-1]:
            self.assertTrue(piece.endswith(","), piece)
        self.assertFalse(pieces[-1].endswith(","))
        # The value the bridge announces is the concatenation, so chunking must
        # not change a single byte of it.
        self.assertEqual("".join(chunk_at_commas(announced_caps(self.source))), value)

    def test_single_literal_control_turns_the_ceiling_gate_red(self) -> None:
        decl = CAPS_DECL_RE.search(self.source)
        assert decl is not None
        pieces = announced_literals(self.source)
        value = "".join(pieces)
        single = self.source.replace(decl.group(0), declaration([value]), 1)
        self.assertNotEqual(single, self.source)
        with self.assertRaisesRegex(
            AssertionError, rf"\b{len(value)} chars, above the {MAX_LITERAL_CHARS} ceiling"
        ):
            announced_literals(single)
        # One char over the ceiling is red too; the ceiling itself is accepted.
        over = declaration(["a" * (MAX_LITERAL_CHARS + 1) + ","] + pieces[-1:])
        over_pieces = LITERAL_RE.findall(over)
        self.assertGreater(len(over_pieces[0]), MAX_LITERAL_CHARS)
        with self.assertRaisesRegex(AssertionError, "above the"):
            announced_literals(self.source.replace(decl.group(0), over, 1))
        at_limit = "a" * (MAX_LITERAL_CHARS - 1) + ","
        exact = self.source.replace(decl.group(0), declaration([at_limit, "b"]), 1)
        self.assertEqual(announced_literals(exact), [at_limit, "b"])

    def test_piece_boundary_controls(self) -> None:
        decl = CAPS_DECL_RE.search(self.source)
        assert decl is not None
        pieces = announced_literals(self.source)
        self.assertGreaterEqual(len(pieces), 2)
        mid_name = [pieces[0][:-3], pieces[0][-3:] + pieces[1]] + pieces[2:]
        trailing = pieces[:-1] + [pieces[-1] + ","]
        empty = pieces[:1] + [""] + pieces[1:]
        for label, mutant, reason in (
            ("boundary_inside_a_name", mid_name, "comma boundary"),
            ("trailing_comma_on_last_piece", trailing, "must not end with a comma"),
            ("empty_piece", empty, "is empty"),
        ):
            with self.subTest(mutant=label):
                mutated = self.source.replace(decl.group(0), declaration(mutant), 1)
                self.assertNotEqual(mutated, self.source)
                with self.assertRaisesRegex(AssertionError, reason):
                    announced_literals(mutated)

    def test_expected_is_not_derived_from_the_python_ingress(self) -> None:
        own = Path(__file__).read_text(encoding="utf-8")
        # Tokens are split so this file never contains them literally.
        forbidden = ("loop" + "back", "SERVER_" + "COMMANDS", "CLIENT_" + "COMMANDS", "list_" + "tools")
        for token in forbidden:
            self.assertNotIn(token, own)

    def test_innocuous_reads_of_cached_capabilities_are_allowed(self) -> None:
        source = self.source
        ctor_head = "\tvoid MCPClientBridge()\n"
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        init_line = "\t\t" + CACHE_INIT + "\n"
        wire_line = "\t\t" + ANNOUNCE_LINE + "\n"
        for needle in (ctor_head, try_init_head, init_line, wire_line):
            self.assertEqual(source.count(needle), 1, needle)

        observer_decl = (
            "\tprotected int ObservePollCaps(string value)\n"
            "\t{\n"
            "\t\treturn value.Length();\n"
            "\t}\n\n"
            "\tstring GetCachedPollCapabilities()\n"
            "\t{\n"
            "\t\treturn m_PollCapabilities;\n"
            "\t}\n\n"
        )
        param_transform_decl = (
            "\tprotected int ReviewParamTransform(string value)\n"
            "\t{\n"
            "\t\tvalue.ToUpper();\n"
            "\t\treturn value.Length();\n"
            "\t}\n\n"
        )
        indexed_caps_block = (
            "\t\tarray<string> reviewBox = new array<string>();\n"
            '\t\treviewBox.Insert("");\n'
            "\t\treviewBox[0] = m_PollCapabilities;\n"
            "\t\tint reviewElemLen = reviewBox[0].ToUpper();\n"
        )
        caps_insert_index_block = (
            "\t\tarray<string> reviewBox = new array<string>();\n"
            "\t\treviewBox.Insert(m_PollCapabilities);\n"
            "\t\tint reviewElemLen = reviewBox[0].ToUpper();\n"
        )
        caps_insert_get_block = (
            "\t\tarray<string> reviewBox = new array<string>();\n"
            "\t\treviewBox.Insert(m_PollCapabilities);\n"
            "\t\tint reviewElemLen = reviewBox.Get(0).ToUpper();\n"
        )
        caps_map_member_block = (
            "\t\tm_ReviewValues = new map<int, string>();\n"
            "\t\tm_ReviewValues.Insert(0, m_PollCapabilities);\n"
            "\t\tint reviewMapLen = m_ReviewValues.Get(0).ToUpper();\n"
        )
        controls = {
            "copy_to_local_in_constructor": source.replace(
                init_line, init_line + "\t\tstring capsCopy = m_PollCapabilities;\n", 1
            ),
            "copy_to_local_in_start_poll": source.replace(
                wire_line, "\t\tstring capsCopy = m_PollCapabilities;\n" + wire_line, 1
            ),
            "copy_and_length_in_try_init": source.replace(
                try_init_head,
                try_init_head
                + "\t\tstring capsCopy = m_PollCapabilities;\n"
                + "\t\tint capsLen = m_PollCapabilities.Length();\n",
                1,
            ),
            "hex_to_int_in_constructor": source.replace(
                init_line, init_line + "\t\tint capsHex = m_PollCapabilities.HexToInt();\n", 1
            ),
            "copy_then_to_upper_on_copy_in_poll": source.replace(
                wire_line,
                "\t\tstring reviewCopy = m_PollCapabilities;\n"
                "\t\treviewCopy.ToUpper();\n" + wire_line,
                1,
            ),
            "length_in_constructor_and_poll": source.replace(
                init_line, init_line + "\t\tint ctorCapsLen = m_PollCapabilities.Length();\n", 1
            ).replace(
                wire_line, "\t\tint pollCapsLen = m_PollCapabilities.Length();\n" + wire_line, 1
            ),
            "non_mutating_string_methods": source.replace(
                wire_line,
                "\t\tstring head = m_PollCapabilities.Substring(0, 4);\n"
                "\t\tstring trimmed = m_PollCapabilities.Trim();\n"
                '\t\tint commaPos = m_PollCapabilities.IndexOf("%2C");\n'
                '\t\tbool hasDialog = m_PollCapabilities.Contains("ui_dialog");\n'
                "\t\tstring firstChar = m_PollCapabilities.Get(0);\n"
                + wire_line,
                1,
            ),
            "pass_by_value_and_getter": source.replace(
                ctor_head, observer_decl + ctor_head, 1
            ).replace(
                wire_line,
                "\t\tLog(m_PollCapabilities);\n"
                "\t\tint seen = ObservePollCaps(m_PollCapabilities);\n"
                + wire_line,
                1,
            ),
            "param_transform_helper_in_poll": source.replace(
                ctor_head, param_transform_decl + ctor_head, 1
            ).replace(
                wire_line,
                "\t\tint reviewParamLen = ReviewParamTransform(m_PollCapabilities);\n"
                + wire_line,
                1,
            ),
            "indexed_assign_then_transform_element_in_poll": source.replace(
                wire_line, indexed_caps_block + wire_line, 1
            ),
            "insert_then_transform_indexed_element_in_poll": source.replace(
                wire_line, caps_insert_index_block + wire_line, 1
            ),
            "insert_then_transform_get_element_in_poll": source.replace(
                wire_line, caps_insert_get_block + wire_line, 1
            ),
            "map_member_insert_then_transform_element_in_constructor": source.replace(
                ctor_head, "\tprotected ref map<int, string> m_ReviewValues;\n\n" + ctor_head, 1
            ).replace(
                init_line, init_line + caps_map_member_block, 1
            ),
            "getter_return_then_transform_in_poll": source.replace(
                ctor_head, observer_decl + ctor_head, 1
            ).replace(
                wire_line, "\t\tint reviewGetLen = GetCachedPollCapabilities().ToUpper();\n" + wire_line, 1
            ),
        }
        for label, variant in controls.items():
            with self.subTest(control=label):
                self.assertNotEqual(variant, source)
                verify_announcement_matches_dispatcher(variant)
                verify_poll_wire(variant)

    def test_foreign_by_value_homonym_does_not_hide_native_out(self) -> None:
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        self.assertEqual(self.source.count(try_init_head), 1)
        mutant = self.source.replace(
            try_init_head, try_init_head + '\t\tErrorExString("review", m_PollCapabilities);\n', 1
        ) + _FOREIGN_BY_VALUE_ERROR_EX_STRING
        with self.assertRaisesRegex(AssertionError, _SHADOWED_NATIVE_OUT):
            verify_announcement_matches_dispatcher(mutant)
            verify_poll_wire(mutant)

    def test_foreign_by_value_find_does_not_vouch_for_map_find(self) -> None:
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        call = (
            "\t\tmap<int, string> reviewValues = new map<int, string>();\n"
            '\t\treviewValues.Insert(0, "broken");\n'
            "\t\treviewValues.Find(0, m_PollCapabilities);\n"
        )
        mutant = self.source.replace(try_init_head, try_init_head + call, 1) + _FOREIGN_BY_VALUE_FIND
        with self.assertRaisesRegex(AssertionError, _MAP_FIND_OUT):
            verify_announcement_matches_dispatcher(mutant)
            verify_poll_wire(mutant)

    def test_source_declaration_does_not_vouch_for_an_engine_receiver(self) -> None:
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        mutant = self.source.replace(try_init_head, try_init_head + '\t\tg_Game.GetProfileString("review", m_PollCapabilities);\n', 1)
        mutant += _FOREIGN_BY_VALUE_PROFILE
        with self.assertRaisesRegex(AssertionError, _UNVERIFIED_PROFILE):
            verify_announcement_matches_dispatcher(mutant)
            verify_poll_wire(mutant)

    def test_receiver_out_declaration_and_arity_are_rejected(self) -> None:
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        ctor_head = "\tvoid MCPClientBridge()\n"
        print_calls = {
            "static": "\t\tReviewCacheWriter.Print(m_PollCapabilities);\n",
            "chained": "\t\tReviewCacheWriter.GetWriter().Print(m_PollCapabilities);\n",
            "indexed": (
                "\t\tarray<ReviewCacheWriter> reviewWriters = new array<ReviewCacheWriter>();\n"
                "\t\treviewWriters[0].Print(m_PollCapabilities);\n"
            ),
        }
        for (shape, call), (method, reason) in (
            (item, pair)
            for item in print_calls.items()
            for pair in (("Print", _RECEIVER_OUT_PRINT), ("Contains", _RECEIVER_OUT_CONTAINS))
        ):
            with self.subTest(receiver=shape, method=method):
                mutant = self.source.replace(
                    try_init_head, try_init_head + call.replace(".Print(", "." + method + "("), 1
                ) + _FOREIGN_OUT_PRINT
                with self.assertRaisesRegex(AssertionError, reason):
                    verify_announcement_matches_dispatcher(mutant)
                    verify_poll_wire(mutant)
        with self.subTest(case="too_many_arguments"):
            mutant = self.source.replace(ctor_head, "\tvoid Review(string value)\n\t{\n\t}\n\n" + ctor_head, 1).replace(
                try_init_head, try_init_head + "\t\tReview(m_PollCapabilities, 1);\n", 1
            )
            with self.assertRaisesRegex(AssertionError, _TOO_MANY_ARGUMENTS):
                verify_announcement_matches_dispatcher(mutant)
                verify_poll_wire(mutant)

    def test_mutants_are_red(self) -> None:
        source = self.source
        decl = CAPS_DECL_RE.search(source)
        assert decl is not None
        names = announced_caps(source)

        def with_caps(new_names: list[str]) -> str:
            # Rebuilt with the same chunking rule, so every mutant below is
            # caught for its own reason and never for a literal-size breach.
            return source.replace(decl.group(0), declaration(chunk_at_commas(new_names)), 1)

        last_branch = (
            '\t\telse if (command.cmd == "ui_dialog")\n'
            "\t\t{\n"
            "\t\t\tpostNow = DispatchUiDialog(command, result);\n"
            "\t\t}\n"
        )
        self.assertEqual(source.count(last_branch), 1)
        terminal_else = (
            "\t\telse\n"
            "\t\t{\n"
            "\t\t\tresult.ok = false;\n"
            '\t\t\tresult.error = "unknown_command";\n'
            "\t\t}\n"
        )
        self.assertEqual(source.count(terminal_else), 1)

        ctor_head = "\tvoid MCPClientBridge()\n"
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        self.assertEqual(source.count(ctor_head), 1)
        self.assertEqual(source.count(try_init_head), 1)
        corrupt_out_decl = (
            "\tprotected void CorruptCaps(out string value)\n"
            "\t{\n"
            '\t\tvalue = "";\n'
            "\t}\n\n"
        )
        corrupt_inout_decl = (
            "\tprotected void CorruptCapsInOut(inout string value)\n"
            "\t{\n"
            '\t\tvalue = "";\n'
            "\t}\n\n"
        )

        mutants = {
            "dispatcher_range_truncated_before_last_branch": source.replace(last_branch, "", 1),
            "dispatcher_without_terminal_else": source.replace(terminal_else, "", 1),
            "announce_without_ui_dialog": with_caps([n for n in names if n != "ui_dialog"]),
            "announce_without_key_press": with_caps([n for n in names if n != "key_press"]),
            "announce_without_player_respawn": with_caps([n for n in names if n != "player_respawn"]),
            "announce_with_server_verb": with_caps(sorted(names + ["entities_query"])),
            "announce_with_unknown_verb": with_caps(sorted(names + ["zzz_not_dispatched"])),
            "announce_unsorted": with_caps([names[1], names[0]] + names[2:]),
            "announce_duplicate": with_caps(sorted(names + [names[0]])),
            "announce_uppercase": with_caps(sorted([names[0].upper()] + names[1:])),
            "announce_dropped_from_poll": source.replace("\t\t" + ANNOUNCE_LINE + "\n", "", 1),
            "cache_decl_dropped": source.replace("\t" + CACHE_DECL + "\n", "", 1),
            "cache_init_dropped_from_constructor": source.replace("\t\t" + CACHE_INIT + "\n", "", 1),
            "cache_init_not_encoded": source.replace(CACHE_INIT, "m_PollCapabilities = CLIENT_POLL_CAPS;", 1),
            "cache_to_upper_after_init": source.replace(
                CACHE_INIT, CACHE_INIT + "\n\t\tm_PollCapabilities.ToUpper();", 1
            ),
            "cache_parenthesized_to_upper": source.replace(
                CACHE_INIT, CACHE_INIT + "\n\t\t(m_PollCapabilities).ToUpper();", 1
            ),
            "cache_to_lower_in_poll": source.replace(
                "\t\t" + ANNOUNCE_LINE, "\t\tm_PollCapabilities.ToLower();\n\t\t" + ANNOUNCE_LINE, 1
            ),
            "cache_replace_after_init": source.replace(
                CACHE_INIT, CACHE_INIT + '\n\t\tm_PollCapabilities.Replace("ui_dialog", "nope");', 1
            ),
            "cache_trim_in_place_after_init": source.replace(
                CACHE_INIT, CACHE_INIT + "\n\t\tm_PollCapabilities.TrimInPlace();", 1
            ),
            "cache_set_after_init": source.replace(
                CACHE_INIT, CACHE_INIT + '\n\t\tm_PollCapabilities.Set(0, "x");', 1
            ),
            "cache_insert_after_init": source.replace(
                CACHE_INIT, CACHE_INIT + '\n\t\tm_PollCapabilities.Insert(0, "x");', 1
            ),
            "cache_method_in_poll_expr": source.replace(
                ANNOUNCE_LINE, 'request = request + "&caps=" + m_PollCapabilities.Substring(0, 4);', 1
            ),
            "cache_plus_equals_after_init": source.replace(
                CACHE_INIT, CACHE_INIT + '\n\t\tm_PollCapabilities += "%2Cextra";', 1
            ),
            "cache_passed_to_dump_stack_string_out_param": source.replace(
                CACHE_INIT, CACHE_INIT + "\n\t\tDumpStackString(m_PollCapabilities);", 1
            ),
            "cache_bridge_array_indexed_dump_stack_string": source.replace(
                try_init_head,
                try_init_head
                + "\t\tarray<ref MCPClientBridge> reviewBridges = new array<ref MCPClientBridge>;\n"
                + "\t\treviewBridges.Insert(this);\n"
                + "\t\tDumpStackString(reviewBridges[0].m_PollCapabilities);\n",
                1,
            ),
            "cache_bridge_array_get_dump_stack_string": source.replace(
                try_init_head,
                try_init_head
                + "\t\tarray<ref MCPClientBridge> reviewBridges = new array<ref MCPClientBridge>;\n"
                + "\t\treviewBridges.Insert(this);\n"
                + "\t\tDumpStackString(reviewBridges.Get(0).m_PollCapabilities);\n",
                1,
            ),
            "cache_passed_to_out_param": source.replace(
                CACHE_INIT, CACHE_INIT + "\n\t\tg_Game.GetVersion(m_PollCapabilities);", 1
            ),
            "cache_passed_to_script_out_param": source.replace(
                ctor_head, corrupt_out_decl + ctor_head, 1
            ).replace(
                CACHE_INIT, CACHE_INIT + "\n\t\tCorruptCaps(m_PollCapabilities);", 1
            ),
            "cache_passed_to_script_inout_param": source.replace(
                ctor_head, corrupt_inout_decl + ctor_head, 1
            ).replace(
                CACHE_INIT, CACHE_INIT + "\n\t\tCorruptCapsInOut(m_PollCapabilities);", 1
            ),
            "cache_shadowed_by_local_in_poll": source.replace(
                "\t\t" + ANNOUNCE_LINE, "\t\tstring m_PollCapabilities;\n\t\t" + ANNOUNCE_LINE, 1
            ),
            "cache_init_from_other_const": source.replace(
                CACHE_INIT, "m_PollCapabilities = EncodeQueryValue(MCP_BRIDGE_VERSION);", 1
            ),
            "cache_init_from_literal": source.replace(
                CACHE_INIT, 'm_PollCapabilities = EncodeQueryValue("key_press,player_respawn,ui_dialog");', 1
            ),
            "cache_init_from_preencoded_literal": source.replace(
                CACHE_INIT, 'm_PollCapabilities = "key_press%2Cplayer_respawn%2Cui_dialog";', 1
            ),
            "cache_init_conditional_in_constructor": source.replace(
                "\t\t" + CACHE_INIT + "\n",
                "\t\tif (m_Configured)\n\t\t{\n\t\t\t" + CACHE_INIT + "\n\t\t}\n",
                1,
            ),
            "cache_init_conditional_braceless": source.replace(
                "\t\t" + CACHE_INIT + "\n", "\t\tif (m_Configured)\n\t\t\t" + CACHE_INIT + "\n", 1
            ),
            "cache_init_moved_outside_constructor": source.replace(
                "\t\t" + CACHE_INIT + "\n", "", 1
            ).replace(try_init_head, try_init_head + "\t\t" + CACHE_INIT + "\n", 1),
            "cache_overwritten_in_poll": source.replace(
                "\t\t" + ANNOUNCE_LINE, '\t\tm_PollCapabilities = "";\n\t\t' + ANNOUNCE_LINE, 1
            ),
            "poll_recomputes_caps_each_time": source.replace(
                ANNOUNCE_LINE, 'request = request + "&caps=" + EncodeQueryValue(CLIENT_POLL_CAPS);', 1
            ),
        }
        for label, mutant in mutants.items():
            with self.subTest(mutant=label):
                self.assertNotEqual(mutant, source, "mutant anchor did not apply")
                with self.assertRaises(AssertionError):
                    verify_announcement_matches_dispatcher(mutant)
                    verify_poll_wire(mutant)


if __name__ == "__main__":
    unittest.main()
