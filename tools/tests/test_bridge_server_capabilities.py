"""Server half of the capability announcement (ficha 8f8c BONUS, module M03).

``MCPBridge.StartPoll`` announces ``caps=`` on every poll: the census of the
commands its ``Dispatch`` chain handles, sorted ascending, comma separated and
percent-encoded the same way ``inst=`` is. This test owns the expected census
as a literal and checks it against the live source:

* the ``Dispatch`` chain is walked branch by branch, by balanced braces, until
  the terminal ``else`` that answers ``unknown_command``. A walk that stops at
  the header of the last branch, or a chain that never reaches that tail, is a
  failure, never a partial census;
* ``SERVER_CAPABILITIES`` is declared as a concatenation of short string
  literals joined by ``+`` (the same form vanilla uses for a ``const string``
  built from pieces). The resolved value must equal that census exactly, in
  canonical order, without duplicates, every name inside the grammar the
  daemon accepts (``[a-z][a-z0-9_]{0,63}``, at most 64 names, 4096 bytes).
  No single literal may exceed ``MAX_LITERAL_CHUNK`` characters: the longest
  single literal in the vanilla scripts is about 240 characters, and a longer
  one would only be rejected by the Enforce compiler, which this text parser
  cannot observe. Chunk boundaries fall on commas so every piece is itself a
  readable sub-list;
* ``MCPBridge()`` must encode ``SERVER_CAPABILITIES`` once through
  ``EncodeQueryValue`` into ``m_PollCapabilities`` (never reassigned elsewhere),
  and ``StartPoll`` must append ``&caps=`` from that cached member after
  ``ver`` and before the optional ``inst`` block, so a peer that has no
  instance still announces without re-encoding on every poll.

The expected census is never derived from ``loopback.SERVER_COMMANDS``, from
the app, or from the announcement itself. ``exec_enforce`` is a dispatcher
branch and therefore part of the census even though the daemon routes it
through its own set, which is one reason a derived list would be wrong. The
mutation fixtures keep the gate non-tautological.
"""

from __future__ import annotations

import re
import unittest
import zlib
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceMap,
    EnforceString,
    assert_no_cache_writes_or_mutations,
    translate,
)


BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"

DISPATCH_SIGNATURE = "protected void Dispatch(MCPCommand command)"
CONSTRUCTOR_SIGNATURE = "void MCPBridge()"
START_POLL_SIGNATURE = "protected void StartPoll()"
CAPS_CACHE_DECL = "protected string m_PollCapabilities;"
CAPS_CACHE_INIT = "m_PollCapabilities = EncodeQueryValue(SERVER_CAPABILITIES);"
CAPS_WIRE = 'request = request + "&caps=" + m_PollCapabilities;'
VER_WIRE = '"&ver=" + GetPollVersion()'
INSTANCE_GUARD = 'if (m_PeerInstance != "")'
POLL_GET = "m_Ctx.GET(cb, request);"

# Grammar the daemon side accepts after decoding (ficha 8f8c, DESIGN 9).
NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
MAX_NAMES = 64
MAX_BYTES = 4096
# Longest single string literal measured in the vanilla scripts is ~240
# characters (carscript.c); nothing shipped is known to compile above that.
MAX_LITERAL_CHUNK = 200

# Literal census of the server dispatcher, one entry per `command.cmd` branch of
# Dispatch() before its terminal unknown_command. Sorted ascending. Owned here;
# not derived from loopback, the app, or the bridge.
EXPECTED_SERVER_CAPABILITIES = (
    "action_cursor_ids",
    "bot_start",
    "bot_stop",
    "entities_query",
    "exec_enforce",
    "hands_attach",
    "hands_take",
    "infected_drive",
    "inventory_attach",
    "inventory_give",
    "inventory_move",
    "loadout_apply",
    "loadout_clear",
    "loadout_get",
    "nav_path_query",
    "notify_players",
    "object_anim",
    "object_delete",
    "object_doors",
    "object_inspect",
    "object_resolve",
    "object_set_health",
    "player_godmode",
    "player_heal",
    "player_kill",
    "player_teleport",
    "players_visibility",
    "query_all_players",
    "query_get_in_condition",
    "query_player_state",
    "scene_clear",
    "scene_entities",
    "scene_raycast",
    "server_perf",
    "surface_query",
    "survivor_drive",
    "survivor_drive_release",
    "telemetry_read",
    "vehicle_door",
    "vehicle_enter",
    "vehicle_prepare_fixture",
    "weapon_state",
    "world_spawn",
    "world_spawn_batch",
    "world_time_get",
    "world_time_set",
    "world_weather_set",
)

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_STRING_LITERAL = re.compile(r'"(?:\\.|[^"\\\n])*"')
_CAPS_DECLARATION = re.compile(r"\bconst\s+string\s+SERVER_CAPABILITIES\s*=\s*([^;]*);")
_CONST_STRING_DECL = re.compile(r"\bconst\s+string\s+([A-Za-z_]\w*)\s*=\s*([^;]+);")
_CONST_INT_DECL = re.compile(r"\bconst\s+int\s+([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*;")
_CONST_FLOAT_DECL = re.compile(r"\bconst\s+float\s+([A-Za-z_]\w*)\s*=\s*(-?\d+(?:\.\d+)?)\s*;")
_DECLARED_MEMBER = re.compile(
    r"\b(?:protected|private)\s+(?:static\s+)?(?P<ref>ref\s+)?"
    r"(?P<type>[A-Za-z_]\w*(?:\s*<[^;{}()=]*>)?)\s+(?P<name>m_\w+)\s*;"
)
_STRING_CHUNK = re.compile(r'"([^"\\\n]*)"')
_BRANCH_HEAD = re.compile(r'(?:if|else\s+if)\s*\(\s*command\.cmd\s*==\s*"([^"]*)"\s*\)')
_FIRST_BRANCH = re.compile(r'\bif\s*\(\s*command\.cmd\s*==\s*"')

# The cache write/mutation detector and its verified signature catalogues
# live in tests.enforce_subset_helpers (assert_no_cache_writes_or_mutations),
# shared with the client and arg-contract-hash gates so they cannot diverge.

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

def _without_comments(source: str) -> str:
    """Blank every comment, keeping strings verbatim and newlines in place."""

    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        if text.startswith('"'):
            return text
        return re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


def _blank_strings(clean: str) -> str:
    """Blank string literal contents in comment-stripped source, keeping offsets."""
    return _STRING_LITERAL.sub(lambda m: '"' + (" " * (len(m.group(0)) - 2)) + '"', clean)


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _block_at(text: str, pos: int) -> tuple[str, int]:
    """Return (inner, index after the closing brace) of the block that starts at pos."""
    pos = _skip_ws(text, pos)
    if pos >= len(text) or text[pos] != "{":
        raise AssertionError(f"expected a brace block at offset {pos}: {text[pos:pos + 40]!r}")
    depth = 0
    for index in range(pos, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[pos + 1 : index], index + 1
    raise AssertionError("unbalanced brace block")


def _method_body(clean: str, signature: str) -> str:
    count = clean.count(signature)
    if count != 1:
        raise AssertionError(f"{signature!r} occurs {count} times, expected exactly once")
    body, _ = _block_at(clean, clean.index(signature) + len(signature))
    return body


def _walk_dispatch_chain(body: str) -> list[str]:
    """Ordered branch names of the `command.cmd` chain; fails unless it ends in unknown_command."""
    first = _FIRST_BRANCH.search(body)
    if first is None:
        raise AssertionError("Dispatch has no command.cmd chain")
    pos = first.start()
    names: list[str] = []
    while True:
        head = _BRANCH_HEAD.match(body, pos)
        if head is None:
            raise AssertionError(f"dispatch chain broken after {names[-1] if names else 'start'!r}: {body[pos:pos + 60]!r}")
        names.append(head.group(1))
        _, after_block = _block_at(body, head.end())
        pos = _skip_ws(body, after_block)
        if not body.startswith("else", pos):
            raise AssertionError(f"dispatch chain ends after {names[-1]!r} without a terminal else")
        after_else = _skip_ws(body, pos + len("else"))
        if body.startswith("if", after_else):
            continue
        tail, _ = _block_at(body, after_else)
        if 'result.error = "unknown_command";' not in tail:
            raise AssertionError("terminal else does not answer unknown_command")
        return names


def _capabilities_chunks(clean: str) -> list[str]:
    """The string literals of the SERVER_CAPABILITIES initializer, in order.

    The initializer must be literals joined by ``+`` and nothing else; every
    literal must be non-empty, at most MAX_LITERAL_CHUNK characters, and must
    end with a comma unless it is the last one, so pieces split on names.
    """
    declarations = _CAPS_DECLARATION.findall(clean)
    if len(declarations) != 1:
        raise AssertionError(f"SERVER_CAPABILITIES declaration occurs {len(declarations)} times, expected exactly once")
    initializer = declarations[0]
    chunks = _STRING_CHUNK.findall(initializer)
    if not chunks:
        raise AssertionError("SERVER_CAPABILITIES initializer has no string literal")
    glue = _STRING_CHUNK.sub("", initializer)
    glue = re.sub(r"\s+", "", glue)
    if glue != "+" * (len(chunks) - 1):
        raise AssertionError(f"SERVER_CAPABILITIES initializer must be string literals joined by +, got {initializer.strip()!r}")
    for index, chunk in enumerate(chunks):
        if chunk == "":
            raise AssertionError(f"SERVER_CAPABILITIES literal {index} is empty")
        if len(chunk) > MAX_LITERAL_CHUNK:
            raise AssertionError(f"SERVER_CAPABILITIES literal {index} is {len(chunk)} characters, longer than {MAX_LITERAL_CHUNK}")
        if chunk.startswith(","):
            raise AssertionError(f"SERVER_CAPABILITIES literal {index} starts with a comma")
        last = index == len(chunks) - 1
        if last and chunk.endswith(","):
            raise AssertionError("SERVER_CAPABILITIES last literal ends with a comma")
        if not last and not chunk.endswith(","):
            raise AssertionError(f"SERVER_CAPABILITIES literal {index} does not end on a comma boundary")
    return chunks


def _capabilities_literal(clean: str) -> list[str]:
    return "".join(_capabilities_chunks(clean)).split(",")


def _assert_grammar(test: unittest.TestCase, names: list[str]) -> None:
    test.assertLessEqual(len(names), MAX_NAMES)
    test.assertLessEqual(len(",".join(names).encode("ascii", "strict")), MAX_BYTES)
    test.assertEqual(len(set(names)), len(names), "duplicate capability name")
    for name in names:
        test.assertIsNotNone(NAME_RE.fullmatch(name), f"capability name outside the grammar: {name!r}")


def _assert_announced_on_every_poll(test: unittest.TestCase, clean: str) -> None:
    masked = _blank_strings(clean)
    test.assertEqual(clean.count(CAPS_CACHE_DECL), 1, "m_PollCapabilities must be declared once")
    decl_at = clean.index(CAPS_CACHE_DECL)
    decl_depth = masked[:decl_at].count("{") - masked[:decl_at].count("}")
    test.assertEqual(decl_depth, 1, "m_PollCapabilities must be a class member, not a local")
    test.assertTrue(
        masked[:decl_at].rstrip().endswith(("{", ";", "}")),
        "m_PollCapabilities declaration must stand alone",
    )

    constructor = _method_body(clean, CONSTRUCTOR_SIGNATURE)
    constructor_masked = _blank_strings(constructor)
    test.assertEqual(
        constructor.count(CAPS_CACHE_INIT),
        1,
        "MCPBridge() must encode SERVER_CAPABILITIES into m_PollCapabilities once",
    )
    init_at = constructor.index(CAPS_CACHE_INIT)
    init_depth = constructor_masked[:init_at].count("{") - constructor_masked[:init_at].count("}")
    test.assertEqual(init_depth, 0, "m_PollCapabilities init must be unconditional in MCPBridge()")
    init_prefix = constructor_masked[:init_at].rstrip()
    test.assertTrue(
        init_prefix == "" or init_prefix.endswith((";", "}")),
        "m_PollCapabilities init must not hang off a braceless condition or expression",
    )
    test.assertIsNone(
        re.search(r"\breturn\b", constructor_masked[:init_at]),
        "no early return may precede m_PollCapabilities init in MCPBridge()",
    )

    assert_no_cache_writes_or_mutations(masked, "m_PollCapabilities")

    start_poll = _method_body(clean, START_POLL_SIGNATURE)
    start_poll_masked = _blank_strings(start_poll)
    test.assertNotIn("SERVER_CAPABILITIES", start_poll_masked, "StartPoll must reuse cached m_PollCapabilities")
    test.assertEqual(start_poll.count(CAPS_WIRE), 1, "caps must append m_PollCapabilities exactly once")
    caps_at = start_poll.index(CAPS_WIRE)
    test.assertLess(start_poll.index(VER_WIRE), caps_at, "caps must follow ver")
    test.assertLess(caps_at, start_poll.index(INSTANCE_GUARD), "caps must precede the optional inst block")
    test.assertLess(caps_at, start_poll.index(POLL_GET), "caps must be appended before the GET")
    depth = start_poll_masked[:caps_at].count("{") - start_poll_masked[:caps_at].count("}")
    test.assertEqual(depth, 0, "caps must be unconditional, not nested in a guard")
    wire_prefix = start_poll_masked[:caps_at].rstrip()
    test.assertTrue(
        wire_prefix == "" or wire_prefix.endswith((";", "}")),
        "caps append must not hang off a braceless condition",
    )


def _execute_translated_poll_caps(source: str, clean: str) -> list[str]:
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

    masked = _blank_strings(clean)
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
        "MCPTelemetryFixtureLine": object,
        "MCPOnInventoryMoveRefused": object(),
        "MCPPollCallback": lambda _owner: object(),
        "GetPollVersion": lambda: WireString.of("12%7E1.30"),
        "LoadCounterQuery": lambda: WireString.of(""),
        "NextLoadCounter": lambda value: value + 1,
        "this": object(),
        "m_Key": WireString.of("k"),
        "m_PeerInstance": WireString(b""),
        "m_PollCapabilities": WireString(b""),
        "m_PollArgContractHash": WireString(b""),
        "m_CallbackRefs": EnforceArray(),
        "m_Pending": EnforceArray(),
        "m_Jobs": EnforceMap(),
        "m_Polls": 0,
        "m_SincePollS": 0.0,
        "m_LastPollIntervalS": 0.0,
        "m_Accum": 0.0,
        "m_PollInFlight": False,
        "m_Tick": 0,
        "m_TickPollSent": 0,
        "m_PollCallback": None,
        "MAX_CALLBACK_REFS": 128,
        "MAX_POLL_RESULTS": 64,
    }
    const_chunk_re = re.compile(r'"((?:\\.|[^"\\\n])*)"')
    escape_re = re.compile(r"\\(.)")
    escape_map = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}
    for match in _CONST_INT_DECL.finditer(clean):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") <= 1:
            ns[match.group(1)] = int(match.group(2))
    for match in _CONST_FLOAT_DECL.finditer(clean):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") <= 1:
            ns[match.group(1)] = float(match.group(2))
    for match in _CONST_STRING_DECL.finditer(clean):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") <= 1:
            cname, expr = match.group(1), match.group(2)
            chunks = [
                escape_re.sub(lambda m: escape_map.get(m.group(1), m.group(1)), raw)
                for raw in const_chunk_re.findall(expr)
            ]
            glue = re.sub(r"\s+", "", const_chunk_re.sub("", expr))
            if not chunks or glue != "+" * (len(chunks) - 1):
                raise AssertionError(f"unsupported const string {cname}: {expr!r}")
            ns[cname] = WireString.of("".join(chunks))
    for match in _DECLARED_MEMBER.finditer(clean):
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
    if encode_sig in clean:
        encode_body = re.sub(
            r"\b(\w+)\s*/\s*(\w+)",
            r"IntDivide(\1, \2)",
            _method_body(clean, encode_sig),
        )
        ns["EncodeQueryValue"] = translate(
            encode_sig + "\n{" + encode_body + "}\n", encode_sig, dict(ns)
        )
    else:
        ns["EncodeQueryValue"] = lambda value: WireString.of(_encode_query_value(value.text))

    next_sig = "protected int NextLoadCounter(int value)"
    if next_sig in clean:
        next_body = _method_body(clean, next_sig)
        translate(
            next_sig + "\n{" + next_body + "}\n",
            next_sig,
            ns,
            members=set(re.findall(r"\bm_\w+", next_body)),
        )

    call_re = re.compile(r"(?<!\.)\b([A-Za-z_]\w*)\s*\(")
    helper_queue = [_method_body(clean, sig) for sig in (CONSTRUCTOR_SIGNATURE, START_POLL_SIGNATURE)]
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
                clean,
            )
            if sig_match is None:
                continue
            helper_sig = sig_match.group(1).strip()
            helper_body = _method_body(clean, helper_sig)
            translate(
                helper_sig + "\n{" + helper_body + "}\n",
                helper_sig,
                ns,
                members=set(re.findall(r"\bm_\w+", helper_body)),
                class_types=("array", "MCPPollCallback"),
            )
            helper_queue.append(helper_body)

    for sig in (CONSTRUCTOR_SIGNATURE, START_POLL_SIGNATURE):
        body = _method_body(clean, sig)
        translate(
            sig + "\n{" + body + "}\n",
            sig,
            ns,
            members=set(re.findall(r"\bm_\w+", body)),
            class_types=("array", "MCPPollCallback"),
        )

    ns["MCPBridge"]()
    ns["m_Key"] = WireString.of("k")
    ns["m_Ctx"] = transport
    ns["StartPoll"]()
    if len(polls) != 1:
        raise AssertionError(f"expected 1 poll GET, got {len(polls)}")
    request = polls[0].decode("ascii")
    if not request.startswith("poll?"):
        raise AssertionError(f"unexpected poll path: {request!r}")
    parsed = parse_qs(request[len("poll?") :], strict_parsing=True, keep_blank_values=True)
    if "caps" not in parsed or len(parsed["caps"]) != 1:
        raise AssertionError(f"missing single caps parameter in {request!r}")
    return parsed["caps"][0].split(",") if parsed["caps"][0] else []


def _assert_contract(test: unittest.TestCase, source: str, expected: tuple[str, ...]) -> list[str]:
    clean = _without_comments(source)
    dispatched = _walk_dispatch_chain(_method_body(clean, DISPATCH_SIGNATURE))
    test.assertEqual(len(set(dispatched)), len(dispatched), "duplicate dispatcher branch")
    announced = _capabilities_literal(clean)
    _assert_grammar(test, announced)
    test.assertEqual(announced, sorted(dispatched), "announcement must be the sorted dispatcher census")
    test.assertEqual(announced, list(expected), "announcement must equal the literal expected census")
    _assert_announced_on_every_poll(test, clean)
    wire_caps = _execute_translated_poll_caps(source, clean)
    test.assertEqual(wire_caps, list(expected), "translated MCPBridge() + StartPoll() must emit expected caps")
    return dispatched


_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _encode_query_value(value: str) -> str:
    """Python twin of MCPBridge.EncodeQueryValue: RFC 3986 unreserved kept, the rest %XX."""
    out: list[str] = []
    for char in value:
        if char in _UNRESERVED:
            out.append(char)
            continue
        code = ord(char)
        if code > 255:
            code = 63
        out.append("%%%02X" % code)
    return "".join(out)


FIXTURE_EXPECTED = ("alpha", "entities_query", "zulu")
FIXTURE_INITIALIZER = '"alpha," + "entities_query," + "zulu"'


def _known_good_source() -> str:
    return '''class MCPBridge
{
	protected const int MAX_PENDING = 32;
	// caps: one entry per Dispatch() branch, sorted ascending, short literals joined by +.
	protected const string SERVER_CAPABILITIES = "alpha," + "entities_query," + "zulu";
	protected string m_PollCapabilities;

	void MCPBridge()
	{
		m_PollCapabilities = EncodeQueryValue(SERVER_CAPABILITIES);
	}

	protected void StartPoll()
	{
		MCPPollCallback cb = new MCPPollCallback(this);
		string request = "poll?key=" + m_Key;
		request = request + "&ver=" + GetPollVersion();
		request = request + "&caps=" + m_PollCapabilities;
		if (m_PeerInstance != "")
		{
			request = request + "&inst=" + EncodeQueryValue(m_PeerInstance);
		}
		m_Ctx.GET(cb, request);
	}

	protected void Dispatch(MCPCommand command)
	{
		if (!command)
		{
			return;
		}

		MCPResult result = new MCPResult();
		bool postNow = true;
		if (command.cmd == "zulu")
		{
			if (postNow)
			{
				result.ok = true;
			}
			else
			{
				result.ok = false;
			}
		}
		else if (command.cmd == "alpha")
		{
			postNow = DispatchAlpha(command, result);
		}
		else if (command.cmd == "entities_query")
		{
			postNow = DispatchEntitiesQuery(command, result);
		}
		else
		{
			result.ok = false;
			result.error = "unknown_command";
		}

		if (postNow)
		{
			PostResult(result);
		}
	}
};
'''


def _with_initializer(source: str, initializer: str) -> str:
    assert source.count(FIXTURE_INITIALIZER) == 1
    return source.replace(FIXTURE_INITIALIZER, initializer)


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
# A class name, a chained call or an indexed element is a receiver the gate
# does not resolve (Sol d2-r8): the call is rejected whatever its name.
_RECEIVER_OUT_PRINT = r"unverified callee Print\(\); a receiver the gate cannot resolve"
_RECEIVER_OUT_CONTAINS = r"unverified callee Contains\(\); a receiver the gate cannot resolve"
# F10/F11 (Sol d2-r8) and the scope rule that replaces F04's union.
_FIXED_ARRAY_OUT = "must not be passed to out/inout parameter 0 of ReviewPad"
_UNPARSED_DECLARATION = r"ReviewOdd\(\); a declaration of ReviewOdd\(\) the gate cannot parse"
_CLASS_TYPED_RECEIVER = r"unverified callee Get\(\); a receiver the gate cannot resolve"
_FOREIGN_OUT_PRINT_ONLY = (
    "\nclass ReviewForeignPrinter\n"
    "{\n"
    "\tvoid Print(out string value)\n"
    "\t{\n"
    '\t\tvalue = "broken";\n'
    "\t}\n"
    "}\n"
)
_INHERITED_CONTAINER = "\nclass ReviewInherited : BaseContainer\n{\n}\n"
# F09: more arguments than any verified signature takes.
_TOO_MANY_ARGUMENTS = "no verified signature takes that many"


class BridgeServerCapabilitiesTest(unittest.TestCase):
    def test_expected_census_is_canonical_and_names_the_mandatory_entry(self) -> None:
        expected = list(EXPECTED_SERVER_CAPABILITIES)
        self.assertIn("entities_query", expected)
        self.assertIn("exec_enforce", expected)
        self.assertEqual(expected, sorted(expected))
        _assert_grammar(self, expected)

    def test_known_good_fixture_satisfies_the_contract(self) -> None:
        dispatched = _assert_contract(self, _known_good_source(), FIXTURE_EXPECTED)
        self.assertEqual(dispatched, ["zulu", "alpha", "entities_query"])

    def test_live_bridge_announces_the_exact_dispatcher_census(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        dispatched = _assert_contract(self, source, EXPECTED_SERVER_CAPABILITIES)
        self.assertEqual(dispatched[-1], "entities_query", "entities_query is the last branch before unknown_command")
        self.assertEqual(len(dispatched), len(EXPECTED_SERVER_CAPABILITIES))

    def test_live_declaration_is_short_literals_joined_by_plus(self) -> None:
        clean = _without_comments(BRIDGE_PATH.read_text(encoding="utf-8"))
        chunks = _capabilities_chunks(clean)
        self.assertGreaterEqual(len(chunks), 2, "the server census does not fit one short literal")
        self.assertLessEqual(max(len(chunk) for chunk in chunks), MAX_LITERAL_CHUNK)
        self.assertEqual("".join(chunks), ",".join(EXPECTED_SERVER_CAPABILITIES))

    def test_commented_out_branch_is_not_a_capability(self) -> None:
        source = _known_good_source().replace(
            '\t\telse if (command.cmd == "alpha")',
            '\t\t// else if (command.cmd == "phantom") { postNow = false; }\n'
            '\t\t/* else if (command.cmd == "ghost")\n\t\t{\n\t\t} */\n'
            '\t\telse if (command.cmd == "alpha")',
            1,
        )
        _assert_contract(self, source, FIXTURE_EXPECTED)

    def test_announcement_survives_the_query_encoding_round_trip(self) -> None:
        clean = _without_comments(BRIDGE_PATH.read_text(encoding="utf-8"))
        announced = _capabilities_literal(clean)
        joined = ",".join(announced)
        encoded = _encode_query_value(joined)
        self.assertEqual(encoded, "%2C".join(announced))
        self.assertNotIn(",", encoded)
        query = "key=k&ver=" + _encode_query_value("10~1.29.163709") + "&caps=" + encoded + "&inst=abc"
        parsed = parse_qs(query, strict_parsing=True, keep_blank_values=True)
        self.assertEqual(parsed["caps"], [joined])
        self.assertEqual(unquote(encoded).split(","), announced)
        self.assertEqual(announced, list(EXPECTED_SERVER_CAPABILITIES))

    def test_single_literal_longer_than_the_chunk_limit_is_rejected(self) -> None:
        names = ["n%03d" % index for index in range(60)]  # 60 names, 299 characters joined
        joined = ",".join(names)
        self.assertGreater(len(joined), MAX_LITERAL_CHUNK)
        one_literal = 'const string SERVER_CAPABILITIES = "%s";' % joined
        with self.assertRaisesRegex(AssertionError, "longer than"):
            _capabilities_chunks(one_literal)
        half = len(names) // 2
        two_literals = 'const string SERVER_CAPABILITIES = "%s," + "%s";' % (",".join(names[:half]), ",".join(names[half:]))
        self.assertEqual(_capabilities_literal(two_literals), names)

    def test_innocuous_reads_of_cached_capabilities_are_allowed(self) -> None:
        fixture = _known_good_source()
        fixture_init = "\t\tm_PollCapabilities = EncodeQueryValue(SERVER_CAPABILITIES);\n"
        fixture_wire = '\t\trequest = request + "&caps=" + m_PollCapabilities;\n'
        fixture_ctor = "\tvoid MCPBridge()\n"
        for needle in (fixture_init, fixture_wire, fixture_ctor):
            self.assertEqual(fixture.count(needle), 1, needle)

        observer_decl = (
            "\tprotected int ObserveCaps(string value)\n"
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
        fixture_controls = {
            "copy_to_local_in_constructor": fixture.replace(
                fixture_init, fixture_init + "\t\tstring capsCopy = m_PollCapabilities;\n", 1
            ),
            "copy_to_local_in_start_poll": fixture.replace(
                fixture_wire, "\t\tstring capsCopy = m_PollCapabilities;\n" + fixture_wire, 1
            ),
            "hex_to_int_in_constructor": fixture.replace(
                fixture_init, fixture_init + "\t\tint capsHex = m_PollCapabilities.HexToInt();\n", 1
            ),
            "copy_then_to_upper_on_copy_in_constructor": fixture.replace(
                fixture_init,
                fixture_init
                + "\t\tstring reviewCopy = m_PollCapabilities;\n"
                + "\t\treviewCopy.ToUpper();\n",
                1,
            ),
            "length_in_constructor": fixture.replace(
                fixture_init, fixture_init + "\t\tint capsLen = m_PollCapabilities.Length();\n", 1
            ),
            "length_in_start_poll": fixture.replace(
                fixture_wire, "\t\tint capsLen = m_PollCapabilities.Length();\n" + fixture_wire, 1
            ),
            "non_mutating_string_methods": fixture.replace(
                fixture_wire,
                "\t\tstring head = m_PollCapabilities.Substring(0, 5);\n"
                "\t\tstring trimmed = m_PollCapabilities.Trim();\n"
                '\t\tint commaPos = m_PollCapabilities.IndexOf("%2C");\n'
                '\t\tbool hasAlpha = m_PollCapabilities.Contains("alpha");\n'
                "\t\tstring firstChar = m_PollCapabilities.Get(0);\n"
                + fixture_wire,
                1,
            ),
            "pass_by_value_and_getter": fixture.replace(
                fixture_ctor, observer_decl + fixture_ctor, 1
            ).replace(
                fixture_wire,
                '\t\tLog("caps=" + m_PollCapabilities);\n'
                "\t\tint observed = ObserveCaps(m_PollCapabilities);\n"
                + fixture_wire,
                1,
            ),
            "param_transform_helper_in_poll": fixture.replace(
                fixture_ctor, param_transform_decl + fixture_ctor, 1
            ).replace(
                fixture_wire,
                "\t\tint reviewParamLen = ReviewParamTransform(m_PollCapabilities);\n"
                + fixture_wire,
                1,
            ),
            "indexed_assign_then_transform_element_in_poll": fixture.replace(
                fixture_wire, indexed_caps_block + fixture_wire, 1
            ),
            "insert_then_transform_indexed_element_in_poll": fixture.replace(
                fixture_wire, caps_insert_index_block + fixture_wire, 1
            ),
            "insert_then_transform_get_element_in_poll": fixture.replace(
                fixture_wire, caps_insert_get_block + fixture_wire, 1
            ),
            "map_member_insert_then_transform_element_in_constructor": fixture.replace(
                fixture_ctor, "\tprotected ref map<int, string> m_ReviewValues;\n\n" + fixture_ctor, 1
            ).replace(
                fixture_init, fixture_init + caps_map_member_block, 1
            ),
            "getter_return_then_transform_in_poll": fixture.replace(
                fixture_ctor, observer_decl + fixture_ctor, 1
            ).replace(
                fixture_wire, "\t\tint reviewGetLen = GetCachedPollCapabilities().ToUpper();\n" + fixture_wire, 1
            ),
        }
        for label, variant in fixture_controls.items():
            with self.subTest(target="fixture", control=label):
                self.assertNotEqual(variant, fixture)
                _assert_contract(self, variant, FIXTURE_EXPECTED)

        live = BRIDGE_PATH.read_text(encoding="utf-8")
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        self.assertEqual(live.count(fixture_init), 1)
        self.assertEqual(live.count(fixture_wire), 1)
        self.assertEqual(live.count(try_init_head), 1)
        live_controls = {
            "live_hex_to_int_in_constructor": live.replace(
                fixture_init, fixture_init + "\t\tint capsHex = m_PollCapabilities.HexToInt();\n", 1
            ),
            "live_copy_then_to_upper_on_copy_in_poll": live.replace(
                fixture_wire,
                "\t\tstring reviewCopy = m_PollCapabilities;\n"
                "\t\treviewCopy.ToUpper();\n" + fixture_wire,
                1,
            ),
            "live_copy_to_local_in_poll": live.replace(
                fixture_wire, "\t\tstring capsCopy = m_PollCapabilities;\n" + fixture_wire, 1
            ),
            "live_length_in_constructor_and_poll": live.replace(
                fixture_init, fixture_init + "\t\tint ctorCapsLen = m_PollCapabilities.Length();\n", 1
            ).replace(
                fixture_wire, "\t\tint pollCapsLen = m_PollCapabilities.Length();\n" + fixture_wire, 1
            ),
            "live_copy_and_length_in_try_init": live.replace(
                try_init_head,
                try_init_head
                + "\t\tstring initCapsCopy = m_PollCapabilities;\n"
                + "\t\tint initCapsLen = m_PollCapabilities.Length();\n",
                1,
            ),
            "live_substring_and_pass_by_value": live.replace(
                "\tvoid MCPBridge()\n", observer_decl + "\tvoid MCPBridge()\n", 1
            ).replace(
                fixture_wire,
                "\t\tstring prefix = m_PollCapabilities.Substring(0, 6);\n"
                "\t\tLog(m_PollCapabilities);\n"
                "\t\tint seen = ObserveCaps(m_PollCapabilities);\n"
                + fixture_wire,
                1,
            ),
            "live_param_transform_helper_in_poll": live.replace(
                "\tvoid MCPBridge()\n", param_transform_decl + "\tvoid MCPBridge()\n", 1
            ).replace(
                fixture_wire,
                "\t\tint reviewParamLen = ReviewParamTransform(m_PollCapabilities);\n"
                + fixture_wire,
                1,
            ),
            "live_indexed_assign_then_transform_element_in_poll": live.replace(
                fixture_wire, indexed_caps_block + fixture_wire, 1
            ),
            "live_insert_then_transform_indexed_element_in_poll": live.replace(
                fixture_wire, caps_insert_index_block + fixture_wire, 1
            ),
            "live_insert_then_transform_get_element_in_poll": live.replace(
                fixture_wire, caps_insert_get_block + fixture_wire, 1
            ),
            "live_map_member_insert_then_transform_element_in_constructor": live.replace(
                "\tvoid MCPBridge()\n", "\tprotected ref map<int, string> m_ReviewValues;\n\n" + "\tvoid MCPBridge()\n", 1
            ).replace(
                fixture_init, fixture_init + caps_map_member_block, 1
            ),
            "live_getter_return_then_transform_in_poll": live.replace(
                "\tvoid MCPBridge()\n", observer_decl + "\tvoid MCPBridge()\n", 1
            ).replace(
                fixture_wire, "\t\tint reviewGetLen = GetCachedPollCapabilities().ToUpper();\n" + fixture_wire, 1
            ),
        }
        for label, variant in live_controls.items():
            with self.subTest(target="live", control=label):
                self.assertNotEqual(variant, live)
                _assert_contract(self, variant, EXPECTED_SERVER_CAPABILITIES)

    def test_foreign_by_value_homonym_does_not_hide_native_out(self) -> None:
        caps_cache_init = "\t\tm_PollCapabilities = EncodeQueryValue(SERVER_CAPABILITIES);\n"
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        call = '\t\tErrorExString("review", m_PollCapabilities);\n'
        fixture = _known_good_source()
        live = BRIDGE_PATH.read_text(encoding="utf-8")
        self.assertEqual(fixture.count(caps_cache_init), 1)
        self.assertEqual(live.count(try_init_head), 1)
        cases = {
            "fixture": (
                fixture.replace(caps_cache_init, caps_cache_init + call, 1) + _FOREIGN_BY_VALUE_ERROR_EX_STRING,
                FIXTURE_EXPECTED,
            ),
            "live": (
                live.replace(try_init_head, try_init_head + call, 1) + _FOREIGN_BY_VALUE_ERROR_EX_STRING,
                EXPECTED_SERVER_CAPABILITIES,
            ),
        }
        for target, (mutant, expected) in cases.items():
            with self.subTest(target=target):
                with self.assertRaisesRegex(AssertionError, _SHADOWED_NATIVE_OUT):
                    _assert_contract(self, mutant, expected)

    def test_foreign_by_value_find_does_not_vouch_for_map_find(self) -> None:
        caps_cache_init = "\t\tm_PollCapabilities = EncodeQueryValue(SERVER_CAPABILITIES);\n"
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        call = (
            "\t\tmap<int, string> reviewValues = new map<int, string>();\n"
            '\t\treviewValues.Insert(0, "broken");\n'
            "\t\treviewValues.Find(0, m_PollCapabilities);\n"
        )
        fixture = _known_good_source()
        live = BRIDGE_PATH.read_text(encoding="utf-8")
        cases = {
            "fixture": (
                fixture.replace(caps_cache_init, caps_cache_init + call, 1) + _FOREIGN_BY_VALUE_FIND,
                FIXTURE_EXPECTED,
            ),
            "live": (
                live.replace(try_init_head, try_init_head + call, 1) + _FOREIGN_BY_VALUE_FIND,
                EXPECTED_SERVER_CAPABILITIES,
            ),
        }
        for target, (mutant, expected) in cases.items():
            with self.subTest(target=target):
                with self.assertRaisesRegex(AssertionError, _MAP_FIND_OUT):
                    _assert_contract(self, mutant, expected)

    def test_source_declaration_does_not_vouch_for_an_engine_receiver(self) -> None:
        caps_cache_init = "\t\tm_PollCapabilities = EncodeQueryValue(SERVER_CAPABILITIES);\n"
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        call = '\t\tg_Game.GetProfileString("review", m_PollCapabilities);\n'
        fixture = _known_good_source()
        live = BRIDGE_PATH.read_text(encoding="utf-8")
        cases = {
            "fixture": (
                fixture.replace(caps_cache_init, caps_cache_init + call, 1) + _FOREIGN_BY_VALUE_PROFILE,
                FIXTURE_EXPECTED,
            ),
            "live": (
                live.replace(try_init_head, try_init_head + call, 1) + _FOREIGN_BY_VALUE_PROFILE,
                EXPECTED_SERVER_CAPABILITIES,
            ),
        }
        for target, (mutant, expected) in cases.items():
            with self.subTest(target=target):
                with self.assertRaisesRegex(AssertionError, _UNVERIFIED_PROFILE):
                    _assert_contract(self, mutant, expected)

    def test_receiver_out_declaration_and_arity_are_rejected(self) -> None:
        caps_cache_init = "\t\tm_PollCapabilities = EncodeQueryValue(SERVER_CAPABILITIES);\n"
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        ctor_head = "\tvoid MCPBridge()\n"
        fixture = _known_good_source()
        live = BRIDGE_PATH.read_text(encoding="utf-8")
        print_calls = {
            "static": "\t\tReviewCacheWriter.Print(m_PollCapabilities);\n",
            "chained": "\t\tReviewCacheWriter.GetWriter().Print(m_PollCapabilities);\n",
            "indexed": (
                "\t\tarray<ReviewCacheWriter> reviewWriters = new array<ReviewCacheWriter>();\n"
                "\t\treviewWriters[0].Print(m_PollCapabilities);\n"
            ),
        }
        for target, src, anchor, expected in (
            ("fixture", fixture, caps_cache_init, FIXTURE_EXPECTED),
            ("live", live, try_init_head, EXPECTED_SERVER_CAPABILITIES),
        ):
            for (shape, call), (method, reason) in (
                (item, pair)
                for item in print_calls.items()
                for pair in (("Print", _RECEIVER_OUT_PRINT), ("Contains", _RECEIVER_OUT_CONTAINS))
            ):
                with self.subTest(target=target, receiver=shape, method=method):
                    mutant = src.replace(anchor, anchor + call.replace(".Print(", "." + method + "("), 1) + _FOREIGN_OUT_PRINT
                    with self.assertRaisesRegex(AssertionError, reason):
                        _assert_contract(self, mutant, expected)
            with self.subTest(target=target, case="too_many_arguments"):
                mutant = src.replace(ctor_head, "\tvoid Review(string value)\n\t{\n\t}\n\n" + ctor_head, 1).replace(
                    anchor, anchor + "\t\tReview(m_PollCapabilities, 1);\n", 1
                )
                with self.assertRaisesRegex(AssertionError, _TOO_MANY_ARGUMENTS):
                    _assert_contract(self, mutant, expected)

    def test_gate_resolves_only_its_subset(self) -> None:
        """Sol d2-r8: F10 fixed-size out, an unparsed declaration and a
        class-typed receiver are rejected; a foreign Print(out) is out of
        scope for the bridge's bare Print (the F04 false rejection)."""
        caps_cache_init = "\t\tm_PollCapabilities = EncodeQueryValue(SERVER_CAPABILITIES);\n"
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        ctor_head = "\tvoid MCPBridge()\n"
        fixture = _known_good_source()
        live = BRIDGE_PATH.read_text(encoding="utf-8")
        cases = {
            "fixed_array_out": ("\t\tint reviewPadding[1];\n\t\tReviewPad(m_PollCapabilities, reviewPadding);\n", _FIXED_ARRAY_OUT),
            "unparsed_declaration": ("\t\tReviewOdd(m_PollCapabilities);\n", _UNPARSED_DECLARATION),
            "class_typed_receiver": ("\t\tReviewInherited reviewReceiver;\n\t\treviewReceiver.Get(0, m_PollCapabilities);\n", _CLASS_TYPED_RECEIVER),
        }
        for target, src, anchor, expected in (
            ("fixture", fixture, caps_cache_init, FIXTURE_EXPECTED),
            ("live", live, try_init_head, EXPECTED_SERVER_CAPABILITIES),
        ):
            with_decls = src.replace(
                ctor_head,
                "\tprotected void ReviewPad(out string value, int padding[1])\n\t{\n\t}\n\n"
                    "\tprotected void ReviewOdd(string value[2][2])\n\t{\n\t}\n\n"
                    "\tprotected void ReviewNativeRead(ReviewInherited receiver)\n\t{\n\t}\n\n"
                + ctor_head,
                1,
            ) + _INHERITED_CONTAINER
            for case, (call, reason) in cases.items():
                with self.subTest(target=target, case=case):
                    mutant = with_decls.replace(anchor, anchor + call, 1)
                    with self.assertRaisesRegex(AssertionError, reason):
                        _assert_contract(self, mutant, expected)
        control = live.replace(
            try_init_head, try_init_head + "\t\tPrint(m_PollCapabilities);\n", 1
        ) + _FOREIGN_OUT_PRINT_ONLY
        _assert_contract(self, control, EXPECTED_SERVER_CAPABILITIES)

    def test_mutants_are_rejected(self) -> None:
        source = _known_good_source()
        caps_line = '\tprotected const string SERVER_CAPABILITIES = "alpha," + "entities_query," + "zulu";\n'
        caps_cache_decl = "\tprotected string m_PollCapabilities;\n"
        caps_cache_init = "\t\tm_PollCapabilities = EncodeQueryValue(SERVER_CAPABILITIES);\n"
        caps_wire = '\t\trequest = request + "&caps=" + m_PollCapabilities;\n'
        ctor_head = "\tvoid MCPBridge()\n"
        mutate_out_decl = (
            "\tprotected void MutateOut(out string value)\n"
            "\t{\n"
            '\t\tvalue = "";\n'
            "\t}\n\n"
        )
        mutate_inout_decl = (
            "\tprotected void MutateInOut(inout string value)\n"
            "\t{\n"
            '\t\tvalue = "";\n'
            "\t}\n\n"
        )
        bridge_array_try_init = (
            "\tprotected void TryInit()\n"
            "\t{\n"
            "\t\tarray<ref MCPBridge> reviewBridges = new array<ref MCPBridge>;\n"
            "\t\treviewBridges.Insert(this);\n"
            "\t\tDumpStackString(reviewBridges[0].m_PollCapabilities);\n"
            "\t}\n\n"
        )
        bridge_array_get_try_init = (
            "\tprotected void TryInit()\n"
            "\t{\n"
            "\t\tarray<ref MCPBridge> reviewBridges = new array<ref MCPBridge>;\n"
            "\t\treviewBridges.Insert(this);\n"
            "\t\tDumpStackString(reviewBridges.Get(0).m_PollCapabilities);\n"
            "\t}\n\n"
        )
        entities_branch = (
            '\t\telse if (command.cmd == "entities_query")\n'
            "\t\t{\n"
            "\t\t\tpostNow = DispatchEntitiesQuery(command, result);\n"
            "\t\t}\n"
        )
        terminal_else = (
            "\t\telse\n"
            "\t\t{\n"
            "\t\t\tresult.ok = false;\n"
            '\t\t\tresult.error = "unknown_command";\n'
            "\t\t}\n"
        )
        for needle in (caps_line, caps_cache_decl, caps_cache_init, caps_wire, ctor_head, entities_branch, terminal_else):
            self.assertEqual(source.count(needle), 1, needle)
        header_only = source.index('else if (command.cmd == "entities_query")') + len('else if (command.cmd == "entities_query")')
        mutants = {
            "caps_drops_entities_query": _with_initializer(source, '"alpha," + "zulu"'),
            "dispatcher_drops_entities_query": source.replace(entities_branch, ""),
            "caps_unsorted": _with_initializer(source, '"zulu," + "alpha," + "entities_query"'),
            "caps_duplicate": _with_initializer(source, '"alpha,alpha," + "entities_query," + "zulu"'),
            "caps_ghost_name": _with_initializer(source, '"alpha," + "entities_query,ghost," + "zulu"'),
            "caps_uppercase": _with_initializer(source, '"Alpha," + "entities_query," + "zulu"'),
            "caps_literal_absent": source.replace(caps_line, ""),
            "caps_literal_duplicated": source.replace(caps_line, caps_line + caps_line),
            "caps_chunk_splits_a_name": _with_initializer(source, '"alpha,entiti" + "es_query," + "zulu"'),
            "caps_chunk_leading_comma": _with_initializer(source, '"alpha" + ",entities_query," + "zulu"'),
            "caps_chunk_trailing_comma_last": _with_initializer(source, '"alpha," + "entities_query," + "zulu,"'),
            "caps_chunk_empty": _with_initializer(source, '"alpha," + "" + "entities_query,zulu"'),
            "caps_chunks_juxtaposed_without_plus": _with_initializer(source, '"alpha," "entities_query," "zulu"'),
            "caps_initializer_uses_a_variable": _with_initializer(source, '"alpha," + middle + "zulu"'),
            "caps_initializer_calls_a_function": _with_initializer(source, 'Join("alpha,", "entities_query,zulu")'),
            "cache_caps_decl_absent": source.replace(caps_cache_decl, ""),
            "cache_caps_init_absent": source.replace(caps_cache_init, ""),
            "cache_caps_not_encoded": source.replace(caps_cache_init, "\t\tm_PollCapabilities = SERVER_CAPABILITIES;\n"),
            "cache_caps_to_upper_after_init": source.replace(
                caps_cache_init, caps_cache_init + "\t\tm_PollCapabilities.ToUpper();\n"
            ),
            "cache_caps_parenthesized_to_upper": source.replace(
                caps_cache_init, caps_cache_init + "\t\t(m_PollCapabilities).ToUpper();\n"
            ),
            "cache_caps_to_lower_in_poll": source.replace(
                caps_wire, "\t\tm_PollCapabilities.ToLower();\n" + caps_wire
            ),
            "cache_caps_replace_after_init": source.replace(
                caps_cache_init, caps_cache_init + '\t\tm_PollCapabilities.Replace("alpha", "beta");\n'
            ),
            "cache_caps_trim_in_place_after_init": source.replace(
                caps_cache_init, caps_cache_init + "\t\tm_PollCapabilities.TrimInPlace();\n"
            ),
            "cache_caps_set_after_init": source.replace(
                caps_cache_init, caps_cache_init + '\t\tm_PollCapabilities.Set(0, "X");\n'
            ),
            "cache_caps_insert_after_init": source.replace(
                caps_cache_init, caps_cache_init + '\t\tm_PollCapabilities.Insert(0, "X");\n'
            ),
            "cache_caps_parse_string_ex_after_init": source.replace(
                caps_cache_init, caps_cache_init + "\t\tstring tok;\n\t\tm_PollCapabilities.ParseStringEx(tok);\n"
            ),
            "cache_caps_method_in_poll_expr": source.replace(
                caps_wire, '\t\trequest = request + "&caps=" + m_PollCapabilities.Substring(0, 5);\n'
            ),
            "cache_caps_plus_equals_after_init": source.replace(
                caps_cache_init, caps_cache_init + '\t\tm_PollCapabilities += "%2Cghost";\n'
            ),
            "cache_caps_indexed_assign": source.replace(
                caps_cache_init, caps_cache_init + '\t\tm_PollCapabilities[0] = "X";\n'
            ),
            "cache_caps_passed_to_out_arg": source.replace(
                ctor_head, mutate_out_decl + ctor_head
            ).replace(
                caps_cache_init, caps_cache_init + "\t\tMutateOut(m_PollCapabilities);\n"
            ),
            "cache_caps_passed_to_inout_arg": source.replace(
                ctor_head, mutate_inout_decl + ctor_head
            ).replace(
                caps_cache_init, caps_cache_init + "\t\tMutateInOut(m_PollCapabilities);\n"
            ),
            "cache_caps_passed_to_dump_stack_string_out_param": source.replace(
                caps_cache_init, caps_cache_init + "\t\tDumpStackString(m_PollCapabilities);\n"
            ),
            "cache_caps_bridge_array_indexed_dump_stack_string": source.replace(
                ctor_head, bridge_array_try_init + ctor_head
            ),
            "cache_caps_bridge_array_get_dump_stack_string": source.replace(
                ctor_head, bridge_array_get_try_init + ctor_head
            ),
            "cache_caps_passed_to_engine_out_param": source.replace(
                caps_cache_init, caps_cache_init + "\t\tg_Game.GetVersion(m_PollCapabilities);\n"
            ),
            "cache_caps_passed_with_explicit_out_kw": source.replace(
                caps_cache_init, caps_cache_init + "\t\tMutateExplicit(out m_PollCapabilities);\n"
            ),
            "cache_caps_shadowed_by_local_in_poll": source.replace(
                caps_wire, "\t\tstring m_PollCapabilities;\n" + caps_wire
            ),
            "cache_caps_init_from_other_const": source.replace(
                caps_line, caps_line + '\tprotected const string OTHER_CAPS = "alpha,entities_query,zulu";\n'
            ).replace(caps_cache_init, "\t\tm_PollCapabilities = EncodeQueryValue(OTHER_CAPS);\n"),
            "cache_caps_init_from_literal": source.replace(
                caps_cache_init, '\t\tm_PollCapabilities = EncodeQueryValue("alpha,entities_query,zulu");\n'
            ),
            "cache_caps_init_from_preencoded_literal": source.replace(
                caps_cache_init, '\t\tm_PollCapabilities = "alpha%2Centities_query%2Czulu";\n'
            ),
            "cache_caps_init_guarded": source.replace(
                caps_cache_init, "\t\tif (g_Game)\n\t\t{\n\t" + caps_cache_init + "\t\t}\n"
            ),
            "cache_caps_init_guarded_braceless": source.replace(
                caps_cache_init, "\t\tif (g_Game)\n\t" + caps_cache_init
            ),
            "cache_caps_init_after_early_return": source.replace(
                caps_cache_init, "\t\tif (!g_Game)\n\t\t{\n\t\t\treturn;\n\t\t}\n" + caps_cache_init
            ),
            "cache_caps_init_moved_outside_constructor": source.replace(caps_cache_init, "").replace(
                "\tprotected void StartPoll()\n",
                "\tprotected void TryInit()\n\t{\n" + caps_cache_init + "\t}\n\n\tprotected void StartPoll()\n",
            ),
            "cache_caps_overwritten": source.replace(caps_wire, '\t\tm_PollCapabilities = "alpha";\n' + caps_wire),
            "poll_recomputes_caps_each_time": source.replace(
                caps_wire, '\t\trequest = request + "&caps=" + EncodeQueryValue(SERVER_CAPABILITIES);\n'
            ),
            "poll_without_caps": source.replace(caps_wire, ""),
            "poll_caps_not_encoded": source.replace(caps_wire, '\t\trequest = request + "&caps=" + SERVER_CAPABILITIES;\n'),
            "poll_caps_inside_instance_guard": source.replace(caps_wire, "").replace(
                '\t\t\trequest = request + "&inst=" + EncodeQueryValue(m_PeerInstance);\n',
                '\t\t\trequest = request + "&inst=" + EncodeQueryValue(m_PeerInstance);\n' + "\t" + caps_wire,
            ),
            "poll_caps_after_get": source.replace(caps_wire, "").replace(
                "\t\tm_Ctx.GET(cb, request);\n", "\t\tm_Ctx.GET(cb, request);\n" + caps_wire
            ),
            "poll_caps_before_ver": source.replace(caps_wire, "").replace(
                '\t\trequest = request + "&ver=" + GetPollVersion();\n',
                caps_wire + '\t\trequest = request + "&ver=" + GetPollVersion();\n',
            ),
            "dispatcher_tail_not_unknown_command": source.replace('"unknown_command"', '"nope"'),
            "dispatcher_without_terminal_else": source.replace(terminal_else, ""),
            "dispatcher_chain_interrupted": source.replace(
                '\t\telse if (command.cmd == "alpha")', '\t\tLog("between");\n\t\telse if (command.cmd == "alpha")'
            ),
            "dispatcher_branch_compares_something_else": source.replace(
                'else if (command.cmd == "alpha")', 'else if (command.kind == "alpha")'
            ),
            "dispatcher_truncated_at_last_header": source[:header_only] + "\n",
            "dispatcher_absent": source.replace(DISPATCH_SIGNATURE, "protected void Route(MCPCommand command)"),
            "dispatcher_duplicated": source + "\n" + source,
        }
        for name, mutant in mutants.items():
            with self.subTest(mutant=name):
                self.assertNotEqual(mutant, source, "mutant did not change the fixture")
                with self.assertRaises(AssertionError):
                    _assert_contract(self, mutant, FIXTURE_EXPECTED)

    def test_expected_literal_mutants_are_rejected_against_the_fixture(self) -> None:
        source = _known_good_source()
        for name, expected in {
            "expected_drops_entities_query": ("alpha", "zulu"),
            "expected_reordered": ("zulu", "alpha", "entities_query"),
            "expected_extra": ("alpha", "entities_query", "extra", "zulu"),
        }.items():
            with self.subTest(mutant=name):
                with self.assertRaises(AssertionError):
                    _assert_contract(self, source, expected)


if __name__ == "__main__":
    unittest.main()
