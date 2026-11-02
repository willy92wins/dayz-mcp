"""Arg-contract hash gate (fb-20260924-235528-0878).

Name-only ``caps=`` census green-washed a stale @DayZ_MCP PBO that still
listed ``vehicle_prepare_fixture`` but rejected the tool's ``mode``/``radius``
shape (``bad_args``). The server peer now announces ``ach=`` (16-hex sha256
prefix of the canonical arg contract) and ``_compare_bridge_capabilities``
plus ``compute_bridge_ready`` fail closed on absent/wrong values.
"""

from __future__ import annotations

import re
import sys
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, server as server_module
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceMap,
    EnforceString,
    assert_no_cache_writes_or_mutations,
    clean,
    method_body,
    translate,
)


BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"
_STRING_LITERAL = re.compile(r'"(?:\\.|[^"\\\n])*"')
_CONST_STRING_CHUNK = re.compile(r'"((?:\\.|[^"\\\n])*)"')
_ESCAPE_SEQ = re.compile(r"\\(.)")
_ESCAPE_MAP = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}
_HASH_DECL = re.compile(
    r'\bconst\s+string\s+SERVER_ARG_CONTRACT_HASH\s*=\s*"([0-9a-f]{16})"\s*;'
)
_CONST_STRING_DECL = re.compile(r"\bconst\s+string\s+([A-Za-z_]\w*)\s*=\s*([^;]+);")
_CONST_INT_DECL = re.compile(r"\bconst\s+int\s+([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*;")
_CONST_FLOAT_DECL = re.compile(r"\bconst\s+float\s+([A-Za-z_]\w*)\s*=\s*(-?\d+(?:\.\d+)?)\s*;")
_DECLARED_MEMBER = re.compile(
    r"\b(?:protected|private)\s+(?:static\s+)?(?P<ref>ref\s+)?"
    r"(?P<type>[A-Za-z_]\w*(?:\s*<[^;{}()=]*>)?)\s+(?P<name>m_\w+)\s*;"
)
_ACH_CACHE_DECL = "protected string m_PollArgContractHash;"
_ACH_CACHE_INIT = "m_PollArgContractHash = EncodeQueryValue(SERVER_ARG_CONTRACT_HASH);"
_CAPS_WIRE = 'request = request + "&caps=" + m_PollCapabilities;'
_ACH_WIRE = 'request = request + "&ach=" + m_PollArgContractHash;'

# The cache write/mutation detector and its verified signature catalogues
# live in tests.enforce_subset_helpers (assert_no_cache_writes_or_mutations),
# shared with the server and client capability gates so they cannot diverge.

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
    return _STRING_LITERAL.sub(lambda m: '"' + (" " * (len(m.group(0)) - 2)) + '"', cleaned)


def _parse_const_string_expr(name: str, expr: str) -> str:
    chunks = [
        _ESCAPE_SEQ.sub(lambda m: _ESCAPE_MAP.get(m.group(1), m.group(1)), raw)
        for raw in _CONST_STRING_CHUNK.findall(expr)
    ]
    glue = re.sub(r"\s+", "", _CONST_STRING_CHUNK.sub("", expr))
    if not chunks or glue != "+" * (len(chunks) - 1):
        raise AssertionError(f"unsupported const string {name}: {expr!r}")
    return "".join(chunks)


def _execute_translated_poll_ach(source: str, cleaned: str, masked: str) -> str:
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
        "this": object(),
    }
    for match in _CONST_INT_DECL.finditer(cleaned):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") <= 1:
            ns[match.group(1)] = int(match.group(2))
    for match in _CONST_FLOAT_DECL.finditer(cleaned):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") <= 1:
            ns[match.group(1)] = float(match.group(2))
    for match in _CONST_STRING_DECL.finditer(cleaned):
        if masked[: match.start()].count("{") - masked[: match.start()].count("}") <= 1:
            ns[match.group(1)] = WireString.of(
                _parse_const_string_expr(match.group(1), match.group(2))
            )
    for match in _DECLARED_MEMBER.finditer(cleaned):
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
    for sig in (
        "protected int NextLoadCounter(int value)",
        "void MCPBridge()",
        "protected void StartPoll()",
    ):
        body = method_body(source, sig)
        translate(
            sig + "\n{" + body + "}\n",
            sig,
            ns,
            members=set(re.findall(r"\bm_\w+", body)),
            class_types=("array", "MCPPollCallback"),
        )
    call_re = re.compile(r"(?<!\.)\b([A-Za-z_]\w*)\s*\(")
    helper_queue = [method_body(source, sig) for sig in ("void MCPBridge()", "protected void StartPoll()")]
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
                class_types=("array", "MCPPollCallback"),
            )
            helper_queue.append(helper_body)

    ns["MCPBridge"]()
    ns["m_Key"] = WireString.of("k")
    ns["m_Ctx"] = transport
    ns["StartPoll"]()
    if len(polls) != 1:
        raise AssertionError(f"expected 1 server poll GET, got {len(polls)}")
    request = polls[0].decode("ascii")
    if not request.startswith("poll?"):
        raise AssertionError(f"unexpected server poll path: {request!r}")
    parsed = parse_qs(request[len("poll?") :], strict_parsing=True, keep_blank_values=True)
    if "ach" not in parsed or len(parsed["ach"]) != 1:
        raise AssertionError(f"missing single ach parameter in {request!r}")
    return parsed["ach"][0]


def verify_enforce_arg_contract_hash(source: str) -> None:
    cleaned = clean(source)
    masked = _blank_strings(cleaned)
    matches = list(_HASH_DECL.finditer(cleaned))
    if len(matches) != 1:
        raise AssertionError("SERVER_ARG_CONTRACT_HASH must be declared exactly once")
    if matches[0].group(1) != server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH:
        raise AssertionError("SERVER_ARG_CONTRACT_HASH disagrees with Python contract hash")
    hash_depth = masked[: matches[0].start()].count("{") - masked[: matches[0].start()].count("}")
    if hash_depth != 1:
        raise AssertionError("SERVER_ARG_CONTRACT_HASH must be a class constant on MCPBridge")

    if cleaned.count(_ACH_CACHE_DECL) != 1:
        raise AssertionError("m_PollArgContractHash must be declared exactly once")
    decl_at = cleaned.index(_ACH_CACHE_DECL)
    decl_depth = masked[:decl_at].count("{") - masked[:decl_at].count("}")
    if decl_depth != 1 or not masked[:decl_at].rstrip().endswith(("{", ";", "}")):
        raise AssertionError("m_PollArgContractHash must be a standalone class field")

    constructor = method_body(source, "void MCPBridge()")
    constructor_masked = _blank_strings(constructor)
    if constructor.count(_ACH_CACHE_INIT) != 1:
        raise AssertionError("MCPBridge() must encode SERVER_ARG_CONTRACT_HASH once")
    init_at = constructor.index(_ACH_CACHE_INIT)
    if constructor_masked[:init_at].count("{") != constructor_masked[:init_at].count("}"):
        raise AssertionError("m_PollArgContractHash init in MCPBridge() must be unconditional")
    init_prefix = constructor_masked[:init_at].rstrip()
    if init_prefix != "" and not init_prefix.endswith((";", "}")):
        raise AssertionError("m_PollArgContractHash init must not hang off a braceless condition")
    if re.search(r"\breturn\b", constructor_masked[:init_at]) is not None:
        raise AssertionError("no early return may precede m_PollArgContractHash init")

    assert_no_cache_writes_or_mutations(masked, "m_PollArgContractHash")

    start_poll = method_body(source, "protected void StartPoll()")
    start_poll_masked = _blank_strings(start_poll)
    if "SERVER_ARG_CONTRACT_HASH" in start_poll_masked:
        raise AssertionError("StartPoll must reuse cached m_PollArgContractHash")
    for needle in (_CAPS_WIRE, _ACH_WIRE, 'if (m_PeerInstance != "")', "m_Ctx.GET(cb, request);"):
        if needle not in start_poll:
            raise AssertionError(f"StartPoll missing {needle!r}")
    if start_poll.count(_ACH_WIRE) != 1:
        raise AssertionError("StartPoll must append m_PollArgContractHash once")
    ach_at = start_poll.index(_ACH_WIRE)
    if start_poll_masked[:ach_at].count("{") != start_poll_masked[:ach_at].count("}"):
        raise AssertionError("ach= announcement in StartPoll must be unconditional")
    wire_prefix = start_poll_masked[:ach_at].rstrip()
    if wire_prefix != "" and not wire_prefix.endswith((";", "}")):
        raise AssertionError("ach= announcement must not hang off a braceless condition")
    if start_poll.index(_CAPS_WIRE) > ach_at:
        raise AssertionError("ach= must follow caps=")
    if ach_at > start_poll.index('if (m_PeerInstance != "")'):
        raise AssertionError("ach= must precede optional inst= block")
    if ach_at > start_poll.index("m_Ctx.GET(cb, request);"):
        raise AssertionError("ach= must precede GET")

    wire_ach = _execute_translated_poll_ach(source, cleaned, masked)
    if wire_ach != server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH:
        raise AssertionError(
            f"translated MCPBridge() + StartPoll() emitted ach={wire_ach!r}, "
            f"expected {server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH!r}"
        )


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
# F12 (Sol d2-r9): a later `modded class X {}` keeps X's base declared in the source.
_WRITER_BASE = (
    "class ReviewWriterBase : Managed\n"
    "{\n"
    "\tprotected void Print(out string value)\n"
    "\t{\n"
    '\t\tvalue = "broken";\n'
    "\t}\n"
    "}\n"
)
_INHERITED_OUT_PRINT = "must not be passed to out/inout parameter 0 of Print"
# F13: only the vanilla container typedefs count, and a source class of that
# name hides the typedef.
_FAKE_CONTAINER = (
    "\nclass TStringArray\n"
    "{\n"
    "\tvoid Insert(out string value)\n"
    "\t{\n"
    '\t\tvalue = "broken";\n'
    "\t}\n"
    "}\n"
)
_FAKE_CONTAINER_REASON = r"unverified callee Insert\(\); a receiver the gate cannot resolve"
# F09: more arguments than any verified signature takes.
_TOO_MANY_ARGUMENTS = "no verified signature takes that many"


class ArgContractHashTest(unittest.TestCase):
    def test_python_hash_matches_canonical_fixture(self) -> None:
        self.assertEqual(
            server_module.server_arg_contract_canonical(),
            "bot_start=action,bot_ttl_s,object_id\n"
            "bot_stop=object_id\n"
            "hands_attach=object_id,slot,uid\n"
            "loadout_apply=loadout_items,object_id,uid\n"
            "loadout_clear=object_id,uid\n"
            "loadout_get=object_id,uid\n"
            "nav_path_query=from,snap_radius,to\n"
            "object_resolve=pos,radius,type\n"
            "object_set_health=health01,object_id,pos,slot,type,zone\n"
            "player_kill=uid\n"
            "players_visibility=pos,radius\n"
            "scene_clear=scene_id\n"
            "scene_entities=scene_id\n"
            "server_perf=window_s\n"
            "survivor_drive=heading,heading_set,mode,object_id,speed,speed_set\n"
            "survivor_drive_release=command_id,object_id\n"
            "vehicle_prepare_fixture=mode,pos,radius,type\n"
            "world_spawn=flags,lifetime_s,lifetime_s_set,pos,rotation,scene_id,type\n"
            "world_spawn_batch=items,scene_id",
        )
        self.assertEqual(
            server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH, "86e3c48a43e86b91"
        )
        self.assertEqual(
            server_module.server_arg_contract_hash(),
            server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )

    def test_enforce_declares_and_wires_the_same_hash(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        verify_enforce_arg_contract_hash(source)

    def test_innocuous_reads_of_cached_hash_are_allowed(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        ctor_head = "\tvoid MCPBridge()\n"
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        cache_init = "\t\t" + _ACH_CACHE_INIT + "\n"
        ach_wire = "\t\t" + _ACH_WIRE + "\n"
        for needle in (ctor_head, try_init_head, cache_init, ach_wire):
            self.assertEqual(source.count(needle), 1, needle)

        observer_decl = (
            "\tprotected int ObserveContractHash(string value)\n"
            "\t{\n"
            "\t\treturn value.Length();\n"
            "\t}\n\n"
            "\tstring GetCachedArgContractHash()\n"
            "\t{\n"
            "\t\treturn m_PollArgContractHash;\n"
            "\t}\n\n"
        )
        param_transform_decl = (
            "\tprotected int ReviewParamTransform(string value)\n"
            "\t{\n"
            "\t\tvalue.ToUpper();\n"
            "\t\treturn value.Length();\n"
            "\t}\n\n"
        )
        indexed_hash_block = (
            "\t\tarray<string> reviewBox = new array<string>();\n"
            '\t\treviewBox.Insert("");\n'
            "\t\treviewBox[0] = m_PollArgContractHash;\n"
            "\t\tint reviewElemLen = reviewBox[0].ToUpper();\n"
        )
        hash_insert_index_block = (
            "\t\tarray<string> reviewBox = new array<string>();\n"
            "\t\treviewBox.Insert(m_PollArgContractHash);\n"
            "\t\tint reviewElemLen = reviewBox[0].ToUpper();\n"
        )
        hash_insert_get_block = (
            "\t\tarray<string> reviewBox = new array<string>();\n"
            "\t\treviewBox.Insert(m_PollArgContractHash);\n"
            "\t\tint reviewElemLen = reviewBox.Get(0).ToUpper();\n"
        )
        hash_map_member_block = (
            "\t\tm_ReviewValues = new map<int, string>();\n"
            "\t\tm_ReviewValues.Insert(0, m_PollArgContractHash);\n"
            "\t\tint reviewMapLen = m_ReviewValues.Get(0).ToUpper();\n"
        )
        controls = {
            "copy_to_local_in_constructor": source.replace(
                cache_init, cache_init + "\t\tstring achCopy = m_PollArgContractHash;\n", 1
            ),
            "copy_to_local_in_start_poll": source.replace(
                ach_wire, "\t\tstring achCopy = m_PollArgContractHash;\n" + ach_wire, 1
            ),
            "copy_and_length_in_try_init": source.replace(
                try_init_head,
                try_init_head
                + "\t\tstring achCopy = m_PollArgContractHash;\n"
                + "\t\tint achLen = m_PollArgContractHash.Length();\n",
                1,
            ),
            "hex_to_int_in_constructor": source.replace(
                cache_init, cache_init + "\t\tint achHex = m_PollArgContractHash.HexToInt();\n", 1
            ),
            "copy_then_to_upper_on_copy_in_poll": source.replace(
                ach_wire,
                "\t\tstring reviewCopy = m_PollArgContractHash;\n"
                "\t\treviewCopy.ToUpper();\n" + ach_wire,
                1,
            ),
            "length_in_constructor_and_poll": source.replace(
                cache_init, cache_init + "\t\tint ctorAchLen = m_PollArgContractHash.Length();\n", 1
            ).replace(
                ach_wire, "\t\tint pollAchLen = m_PollArgContractHash.Length();\n" + ach_wire, 1
            ),
            "non_mutating_string_methods": source.replace(
                ach_wire,
                "\t\tstring prefix = m_PollArgContractHash.Substring(0, 8);\n"
                "\t\tstring trimmed = m_PollArgContractHash.Trim();\n"
                '\t\tint pos86 = m_PollArgContractHash.IndexOf("86");\n'
                '\t\tbool has86 = m_PollArgContractHash.Contains("86");\n'
                "\t\tstring firstChar = m_PollArgContractHash.Get(0);\n"
                + ach_wire,
                1,
            ),
            "pass_by_value_and_getter": source.replace(
                ctor_head, observer_decl + ctor_head, 1
            ).replace(
                ach_wire,
                "\t\tLog(m_PollArgContractHash);\n"
                "\t\tint seen = ObserveContractHash(m_PollArgContractHash);\n"
                + ach_wire,
                1,
            ),
            "param_transform_helper_in_poll": source.replace(
                ctor_head, param_transform_decl + ctor_head, 1
            ).replace(
                ach_wire,
                "\t\tint reviewParamLen = ReviewParamTransform(m_PollArgContractHash);\n"
                + ach_wire,
                1,
            ),
            "indexed_assign_then_transform_element_in_poll": source.replace(
                ach_wire, indexed_hash_block + ach_wire, 1
            ),
            "insert_then_transform_indexed_element_in_poll": source.replace(
                ach_wire, hash_insert_index_block + ach_wire, 1
            ),
            "insert_then_transform_get_element_in_poll": source.replace(
                ach_wire, hash_insert_get_block + ach_wire, 1
            ),
            "map_member_insert_then_transform_element_in_constructor": source.replace(
                ctor_head, "\tprotected ref map<int, string> m_ReviewValues;\n\n" + ctor_head, 1
            ).replace(
                cache_init, cache_init + hash_map_member_block, 1
            ),
            "getter_return_then_transform_in_poll": source.replace(
                ctor_head, observer_decl + ctor_head, 1
            ).replace(
                ach_wire, "\t\tint reviewGetLen = GetCachedArgContractHash().ToUpper();\n" + ach_wire, 1
            ),
        }
        for label, variant in controls.items():
            with self.subTest(control=label):
                self.assertNotEqual(variant, source)
                verify_enforce_arg_contract_hash(variant)

    def test_foreign_by_value_homonym_does_not_hide_native_out(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        self.assertEqual(source.count(try_init_head), 1)
        mutant = source.replace(
            try_init_head, try_init_head + '\t\tErrorExString("review", m_PollArgContractHash);\n', 1
        ) + _FOREIGN_BY_VALUE_ERROR_EX_STRING
        with self.assertRaisesRegex(AssertionError, _SHADOWED_NATIVE_OUT):
            verify_enforce_arg_contract_hash(mutant)

    def test_foreign_by_value_find_does_not_vouch_for_map_find(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        call = (
            "\t\tmap<int, string> reviewValues = new map<int, string>();\n"
            '\t\treviewValues.Insert(0, "broken");\n'
            "\t\treviewValues.Find(0, m_PollArgContractHash);\n"
        )
        mutant = source.replace(try_init_head, try_init_head + call, 1) + _FOREIGN_BY_VALUE_FIND
        with self.assertRaisesRegex(AssertionError, _MAP_FIND_OUT):
            verify_enforce_arg_contract_hash(mutant)

    def test_source_declaration_does_not_vouch_for_an_engine_receiver(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        mutant = source.replace(try_init_head, try_init_head + '\t\tg_Game.GetProfileString("review", m_PollArgContractHash);\n', 1)
        mutant += _FOREIGN_BY_VALUE_PROFILE
        with self.assertRaisesRegex(AssertionError, _UNVERIFIED_PROFILE):
            verify_enforce_arg_contract_hash(mutant)

    def test_receiver_out_declaration_and_arity_are_rejected(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        ctor_head = "\tvoid MCPBridge()\n"
        print_calls = {
            "static": "\t\tReviewCacheWriter.Print(m_PollArgContractHash);\n",
            "chained": "\t\tReviewCacheWriter.GetWriter().Print(m_PollArgContractHash);\n",
            "indexed": (
                "\t\tarray<ReviewCacheWriter> reviewWriters = new array<ReviewCacheWriter>();\n"
                "\t\treviewWriters[0].Print(m_PollArgContractHash);\n"
            ),
        }
        for (shape, call), (method, reason) in (
            (item, pair)
            for item in print_calls.items()
            for pair in (("Print", _RECEIVER_OUT_PRINT), ("Contains", _RECEIVER_OUT_CONTAINS))
        ):
            with self.subTest(receiver=shape, method=method):
                mutant = source.replace(
                    try_init_head, try_init_head + call.replace(".Print(", "." + method + "("), 1
                ) + _FOREIGN_OUT_PRINT
                with self.assertRaisesRegex(AssertionError, reason):
                    verify_enforce_arg_contract_hash(mutant)
        with self.subTest(case="too_many_arguments"):
            mutant = source.replace(ctor_head, "\tvoid Review(string value)\n\t{\n\t}\n\n" + ctor_head, 1).replace(
                try_init_head, try_init_head + "\t\tReview(m_PollArgContractHash, 1);\n", 1
            )
            with self.assertRaisesRegex(AssertionError, _TOO_MANY_ARGUMENTS):
                verify_enforce_arg_contract_hash(mutant)

    def test_gate_resolves_only_its_subset(self) -> None:
        """Sol d2-r8: see the server-capabilities test of the same name."""
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        ctor_head = "\tvoid MCPBridge()\n"
        cases = {
            "fixed_array_out": ("\t\tint reviewPadding[1];\n\t\tReviewPad(m_PollArgContractHash, reviewPadding);\n", _FIXED_ARRAY_OUT),
            "unparsed_declaration": ("\t\tReviewOdd(m_PollArgContractHash);\n", _UNPARSED_DECLARATION),
            "class_typed_receiver": ("\t\tReviewInherited reviewReceiver;\n\t\treviewReceiver.Get(0, m_PollArgContractHash);\n", _CLASS_TYPED_RECEIVER),
        }
        with_decls = source.replace(
            ctor_head,
            "\tprotected void ReviewPad(out string value, int padding[1])\n\t{\n\t}\n\n"
                    "\tprotected void ReviewOdd(string value[2][2])\n\t{\n\t}\n\n"
                    "\tprotected void ReviewNativeRead(ReviewInherited receiver)\n\t{\n\t}\n\n"
            + ctor_head,
            1,
        ) + _INHERITED_CONTAINER
        for case, (call, reason) in cases.items():
            with self.subTest(case=case):
                mutant = with_decls.replace(try_init_head, try_init_head + call, 1)
                with self.assertRaisesRegex(AssertionError, reason):
                    verify_enforce_arg_contract_hash(mutant)
        control = source.replace(
            try_init_head, try_init_head + "\t\tPrint(m_PollArgContractHash);\n", 1
        ) + _FOREIGN_OUT_PRINT_ONLY
        verify_enforce_arg_contract_hash(control)

    def test_inheritance_and_container_aliases(self) -> None:
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        head = "class MCPBridge : Managed"
        self.assertEqual(source.count(head), 1)
        self.assertEqual(source.count(try_init_head), 1)
        class_name = head.split()[1]
        inherited = _WRITER_BASE + source.replace(
            head, "class " + class_name + " : ReviewWriterBase", 1
        ).replace(
            try_init_head, try_init_head + "\t\tPrint(m_PollArgContractHash);\n", 1
        ) + "\nmodded class " + class_name + "\n{\n}\n"
        with self.subTest(case="modded_class_keeps_source_base"):
            with self.assertRaisesRegex(AssertionError, _INHERITED_OUT_PRINT):
                verify_enforce_arg_contract_hash(inherited)
        fake = source.replace(
            try_init_head,
            try_init_head
            + "\t\tTReviewArray reviewReceiver = new TReviewArray();\n"
            + "\t\treviewReceiver.Insert(m_PollArgContractHash);\n",
            1,
        )
        with self.subTest(case="unknown_type_named_like_a_typedef"):
            with self.assertRaisesRegex(AssertionError, _FAKE_CONTAINER_REASON):
                verify_enforce_arg_contract_hash(fake)
        shadow = source.replace(
            try_init_head,
            try_init_head
            + "\t\tTStringArray reviewShadow = new TStringArray();\n"
            + "\t\treviewShadow.Insert(m_PollArgContractHash);\n",
            1,
        ) + _FAKE_CONTAINER
        with self.subTest(case="source_class_hides_a_vanilla_typedef"):
            with self.assertRaisesRegex(AssertionError, _FAKE_CONTAINER_REASON):
                verify_enforce_arg_contract_hash(shadow)
        vanilla = source.replace(
            try_init_head,
            try_init_head
            + "\t\tTStringArray reviewList = new TStringArray();\n"
            + "\t\treviewList.Insert(m_PollArgContractHash);\n",
            1,
        )
        with self.subTest(case="vanilla_typedef_is_a_container"):
            verify_enforce_arg_contract_hash(vanilla)

    def test_enforce_hash_cache_mutants_are_rejected(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        decl_line = f'\tprotected const string SERVER_ARG_CONTRACT_HASH = "{server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH}";\n'
        cache_decl = "\t" + _ACH_CACHE_DECL + "\n"
        cache_init = "\t\t" + _ACH_CACHE_INIT + "\n"
        ach_wire = "\t\t" + _ACH_WIRE + "\n"
        inst_wire = '\t\t\trequest = request + "&inst=" + EncodeQueryValue(m_PeerInstance);\n'
        ctor_head = "\tvoid MCPBridge()\n"
        try_init_head = "\tprotected void TryInit()\n\t{\n"
        for needle in (decl_line, cache_decl, cache_init, ach_wire, inst_wire, ctor_head, try_init_head):
            self.assertEqual(source.count(needle), 1, needle)

        corrupt_out_decl = (
            "\tprotected void CorruptAch(out string value)\n"
            "\t{\n"
            '\t\tvalue = "";\n'
            "\t}\n\n"
        )
        corrupt_inout_decl = (
            "\tprotected void CorruptAchInOut(inout string value)\n"
            "\t{\n"
            '\t\tvalue = "";\n'
            "\t}\n\n"
        )

        mutants = {
            "ach_decl_wrong_hash": source.replace(
                decl_line,
                '\tprotected const string SERVER_ARG_CONTRACT_HASH = "deadbeefdeadbeef";\n',
                1,
            ),
            "ach_cache_decl_absent": source.replace(cache_decl, "", 1),
            "ach_cache_init_absent": source.replace(cache_init, "", 1),
            "ach_cache_not_encoded": source.replace(
                cache_init, "\t\tm_PollArgContractHash = SERVER_ARG_CONTRACT_HASH;\n", 1
            ),
            "ach_cache_to_upper_after_init": source.replace(
                cache_init, cache_init + "\t\tm_PollArgContractHash.ToUpper();\n", 1
            ),
            "ach_cache_parenthesized_to_upper": source.replace(
                cache_init, cache_init + "\t\t(m_PollArgContractHash).ToUpper();\n", 1
            ),
            "ach_cache_to_lower_in_poll": source.replace(
                ach_wire, "\t\tm_PollArgContractHash.ToLower();\n" + ach_wire, 1
            ),
            "ach_cache_replace_after_init": source.replace(
                cache_init, cache_init + '\t\tm_PollArgContractHash.Replace("86", "00");\n', 1
            ),
            "ach_cache_trim_in_place_after_init": source.replace(
                cache_init, cache_init + "\t\tm_PollArgContractHash.TrimInPlace();\n", 1
            ),
            "ach_cache_set_after_init": source.replace(
                cache_init, cache_init + '\t\tm_PollArgContractHash.Set(0, "0");\n', 1
            ),
            "ach_cache_insert_after_init": source.replace(
                cache_init, cache_init + '\t\tm_PollArgContractHash.Insert(0, "0");\n', 1
            ),
            "ach_cache_plus_equals_after_init": source.replace(
                cache_init, cache_init + '\t\tm_PollArgContractHash += "0";\n', 1
            ),
            "ach_cache_passed_to_dump_stack_string_out_param": source.replace(
                cache_init, cache_init + "\t\tDumpStackString(m_PollArgContractHash);\n", 1
            ),
            "ach_cache_bridge_array_indexed_dump_stack_string": source.replace(
                try_init_head,
                try_init_head
                + "\t\tarray<ref MCPBridge> reviewBridges = new array<ref MCPBridge>;\n"
                + "\t\treviewBridges.Insert(this);\n"
                + "\t\tDumpStackString(reviewBridges[0].m_PollArgContractHash);\n",
                1,
            ),
            "ach_cache_bridge_array_get_dump_stack_string": source.replace(
                try_init_head,
                try_init_head
                + "\t\tarray<ref MCPBridge> reviewBridges = new array<ref MCPBridge>;\n"
                + "\t\treviewBridges.Insert(this);\n"
                + "\t\tDumpStackString(reviewBridges.Get(0).m_PollArgContractHash);\n",
                1,
            ),
            "ach_cache_passed_to_out_param": source.replace(
                cache_init, cache_init + "\t\tg_Game.GetVersion(m_PollArgContractHash);\n", 1
            ),
            "ach_cache_passed_to_script_out_param": source.replace(
                ctor_head, corrupt_out_decl + ctor_head, 1
            ).replace(
                cache_init, cache_init + "\t\tCorruptAch(m_PollArgContractHash);\n", 1
            ),
            "ach_cache_passed_to_script_inout_param": source.replace(
                ctor_head, corrupt_inout_decl + ctor_head, 1
            ).replace(
                cache_init, cache_init + "\t\tCorruptAchInOut(m_PollArgContractHash);\n", 1
            ),
            "ach_cache_shadowed_by_local_in_poll": source.replace(
                ach_wire, "\t\tstring m_PollArgContractHash;\n" + ach_wire, 1
            ),
            "ach_cache_init_from_other_const": source.replace(
                cache_init, "\t\tm_PollArgContractHash = EncodeQueryValue(VISIBILITY_HEAD_BONE);\n", 1
            ),
            "ach_cache_init_from_literal": source.replace(
                cache_init, '\t\tm_PollArgContractHash = EncodeQueryValue("0000000000000000");\n', 1
            ),
            "ach_cache_init_from_preencoded_literal": source.replace(
                cache_init,
                f'\t\tm_PollArgContractHash = "{server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH}";\n',
                1,
            ),
            "ach_cache_init_conditional": source.replace(
                cache_init, "\t\tif (m_Configured)\n\t\t{\n\t" + cache_init + "\t\t}\n", 1
            ),
            "ach_cache_init_conditional_braceless": source.replace(
                cache_init, "\t\tif (m_Configured)\n\t" + cache_init, 1
            ),
            "ach_cache_init_moved_outside_constructor": source.replace(cache_init, "", 1).replace(
                try_init_head, try_init_head + cache_init, 1
            ),
            "ach_cache_overwritten_in_poll": source.replace(
                ach_wire, '\t\tm_PollArgContractHash = "deadbeefdeadbeef";\n' + ach_wire, 1
            ),
            "poll_recomputes_ach_each_time": source.replace(
                ach_wire,
                '\t\trequest = request + "&ach=" + EncodeQueryValue(SERVER_ARG_CONTRACT_HASH);\n',
                1,
            ),
            "poll_drops_ach": source.replace(ach_wire, "", 1),
            "poll_ach_inside_instance_guard": source.replace(ach_wire, "", 1).replace(
                inst_wire, inst_wire + "\t" + ach_wire, 1
            ),
        }
        for label, mutant in mutants.items():
            with self.subTest(mutant=label):
                self.assertNotEqual(mutant, source, "mutant anchor did not apply")
                with self.assertRaises(AssertionError):
                    verify_enforce_arg_contract_hash(mutant)

    def test_matching_hash_with_full_census_is_match(self) -> None:
        census = sorted(server_module._BRIDGE_COMMAND_TOOLS["server"])
        registered = frozenset(
            tool for tool in server_module._BRIDGE_COMMAND_TOOLS["server"].values() if tool
        )
        result = server_module._compare_bridge_capabilities(
            "server",
            {
                "state": "announced",
                "reason": "ok",
                "announced_commands": census,
                "announced_arg_contract_hash": server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH,
            },
            registered,
        )
        self.assertEqual(result["state"], "match")
        self.assertEqual(result["reason"], "ok")
        self.assertEqual(
            result["announced_arg_contract_hash"],
            server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )

    def test_absent_hash_on_announced_server_census_is_mismatch(self) -> None:
        census = sorted(server_module._BRIDGE_COMMAND_TOOLS["server"])
        registered = frozenset(
            tool for tool in server_module._BRIDGE_COMMAND_TOOLS["server"].values() if tool
        )
        result = server_module._compare_bridge_capabilities(
            "server",
            {
                "state": "announced",
                "reason": "ok",
                "announced_commands": census,
            },
            registered,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["reason"], "arg_contract_mismatch")
        self.assertIsNone(result["announced_arg_contract_hash"])
        self.assertEqual(
            result["expected_arg_contract_hash"],
            server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )

    def test_wrong_hash_is_mismatch(self) -> None:
        census = sorted(server_module._BRIDGE_COMMAND_TOOLS["server"])
        registered = frozenset(
            tool for tool in server_module._BRIDGE_COMMAND_TOOLS["server"].values() if tool
        )
        result = server_module._compare_bridge_capabilities(
            "server",
            {
                "state": "announced",
                "reason": "ok",
                "announced_commands": census,
                "announced_arg_contract_hash": "deadbeefdeadbeef",
            },
            registered,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["reason"], "arg_contract_mismatch")
        self.assertEqual(result["announced_arg_contract_hash"], "deadbeefdeadbeef")

    def test_client_peer_does_not_require_arg_contract_hash(self) -> None:
        census = sorted(server_module._BRIDGE_COMMAND_TOOLS["client"])
        registered = frozenset(
            tool for tool in server_module._BRIDGE_COMMAND_TOOLS["client"].values() if tool
        )
        result = server_module._compare_bridge_capabilities(
            "client",
            {
                "state": "announced",
                "reason": "ok",
                "announced_commands": census,
            },
            registered,
        )
        self.assertEqual(result["state"], "match")
        self.assertIsNone(result["expected_arg_contract_hash"])

    def test_ready_fails_closed_on_arg_contract_mismatch(self) -> None:
        status = {
            "server_peer": {
                "last_poll_age_s": 0.1,
                "bound_last_poll_age_s": 0.1,
                "binding_state": "BOUND",
                "version_state": "ok",
                "capabilities": {
                    "state": "mismatch",
                    "reason": "arg_contract_mismatch",
                    "announced_commands": [],
                },
            },
            "client_peer": {
                "last_poll_age_s": 0.1,
                "bound_last_poll_age_s": 0.1,
                "binding_state": "BOUND",
                "version_state": "ok",
                "capabilities": {"state": "match", "reason": "ok", "announced_commands": []},
            },
        }
        # Force live peers: _peer_is_live checks binding + ages. BOUND + ages set.
        ready = server_module.compute_bridge_ready(status)
        self.assertEqual(ready, {"ready": False, "reason": "arg_contract_mismatch"})
        self.assertIn("arg_contract_mismatch", server_module.READY_REASONS)

    def test_loopback_stores_ach_on_accredited_poll_view(self) -> None:
        # Minimal accredited path is heavy; unit-test the recorder + view.
        state = loopback.ServerState(key="k")
        state.daemon_generation = 1
        state._record_poll_caps_locked(
            "server",
            "entities_query,vehicle_prepare_fixture",
            True,
            ach=server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )
        view = state._capabilities_view_locked("server")
        self.assertEqual(view["state"], "announced")
        self.assertEqual(
            view["announced_arg_contract_hash"],
            server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH,
        )
        state._record_poll_caps_locked("server", "entities_query", False, ach="nope")
        view2 = state._capabilities_view_locked("server")
        self.assertEqual(view2["state"], "unknown")
        self.assertIsNone(view2["announced_arg_contract_hash"])

    def _live_peers(self, server_caps: dict) -> dict:
        return {
            "server_peer": {
                "last_poll_age_s": 0.1,
                "bound_last_poll_age_s": 0.1,
                "binding_state": "BOUND",
                "version_state": "ok",
                "capabilities": server_caps,
            },
            "client_peer": {
                "last_poll_age_s": 0.1,
                "bound_last_poll_age_s": 0.1,
                "binding_state": "BOUND",
                "version_state": "ok",
                "capabilities": {
                    "state": "match",
                    "reason": "ok",
                    "announced_commands": [],
                },
            },
        }

    def _server_registered(self) -> frozenset[str]:
        return frozenset(
            tool
            for tool in server_module._BRIDGE_COMMAND_TOOLS["server"].values()
            if tool
        )

    def _full_announced(self, ach: str | None) -> dict:
        block: dict = {
            "state": "announced",
            "reason": "ok",
            "announced_commands": sorted(
                server_module._BRIDGE_COMMAND_TOOLS["server"]
            ),
        }
        if ach is not None:
            block["announced_arg_contract_hash"] = ach
        return block

    def _announced_missing_command(self, ach: str | None, missing: str = "world_spawn") -> dict:
        """Census lacking one registered command (B2 residual repro)."""
        cmds = sorted(
            c
            for c in server_module._BRIDGE_COMMAND_TOOLS["server"]
            if c != missing
        )
        self.assertTrue(cmds, "census should still list other commands")
        self.assertNotIn(missing, cmds)
        block: dict = {
            "state": "announced",
            "reason": "ok",
            "announced_commands": cmds,
        }
        if ach is not None:
            block["announced_arg_contract_hash"] = ach
        return block

    def test_ready_false_when_capabilities_unknown(self) -> None:
        # B2: missing/malformed caps must not green-wash ready.
        status = self._live_peers(
            {
                "state": "unknown",
                "reason": "absent",
                "announced_commands": [],
                "announced_arg_contract_hash": None,
            }
        )
        ready = server_module.compute_bridge_ready(status)
        self.assertEqual(ready, {"ready": False, "reason": "capabilities_unknown"})
        self.assertIn("capabilities_unknown", server_module.READY_REASONS)

    def test_ready_false_when_capabilities_block_missing(self) -> None:
        status = self._live_peers({})
        ready = server_module.compute_bridge_ready(status)
        self.assertEqual(ready, {"ready": False, "reason": "capabilities_unknown"})

    def test_bridge_status_path_compare_before_ready_correct_hash(self) -> None:
        # B1: compare then ready ? correct ach => ready true + caps match.
        raw = self._live_peers(
            self._full_announced(server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH)
        )
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        self.assertEqual(compared["server_peer"]["capabilities"]["state"], "match")
        payload = server_module._with_ready(compared)
        self.assertTrue(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "ready")
        self.assertEqual(payload["server_peer"]["capabilities"]["state"], "match")

    def test_bridge_status_path_compare_before_ready_absent_hash(self) -> None:
        raw = self._live_peers(self._full_announced(None))
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        caps = compared["server_peer"]["capabilities"]
        self.assertEqual(caps["state"], "mismatch")
        self.assertEqual(caps["reason"], "arg_contract_mismatch")
        payload = server_module._with_ready(compared)
        self.assertFalse(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "arg_contract_mismatch")

    def test_bridge_status_path_compare_before_ready_wrong_hash(self) -> None:
        raw = self._live_peers(self._full_announced("deadbeefdeadbeef"))
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        caps = compared["server_peer"]["capabilities"]
        self.assertEqual(caps["state"], "mismatch")
        self.assertEqual(caps["reason"], "arg_contract_mismatch")
        payload = server_module._with_ready(compared)
        self.assertFalse(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "arg_contract_mismatch")

    def test_bridge_status_path_unknown_caps_ready_false(self) -> None:
        # B1+B2: unknown census after comparison => ready false.
        raw = self._live_peers(
            {
                "state": "unknown",
                "reason": "absent",
                "announced_commands": [],
            }
        )
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        self.assertEqual(compared["server_peer"]["capabilities"]["state"], "unknown")
        payload = server_module._with_ready(compared)
        self.assertFalse(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "capabilities_unknown")

    def test_ready_on_raw_announced_wrong_hash_without_comparison(self) -> None:
        # Fail-closed even if a caller skips comparison (raw announced + bad ach).
        status = self._live_peers(self._full_announced("deadbeefdeadbeef"))
        ready = server_module.compute_bridge_ready(status)
        self.assertEqual(ready, {"ready": False, "reason": "arg_contract_mismatch"})

    def test_bridge_status_missing_census_cmd_absent_ach_ready_false(self) -> None:
        # B2 residual: missing census cmd must not hide absent ach => ready=false.
        raw = self._live_peers(self._announced_missing_command(None))
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        caps = compared["server_peer"]["capabilities"]
        self.assertEqual(caps["state"], "mismatch")
        self.assertEqual(caps["reason"], "arg_contract_mismatch")
        self.assertIn("world_spawn", caps.get("registered_without_announced_command", []))
        payload = server_module._with_ready(compared)
        self.assertFalse(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "arg_contract_mismatch")

    def test_bridge_status_missing_census_cmd_wrong_ach_ready_false(self) -> None:
        # B2 residual: missing census cmd must not hide wrong ach => ready=false.
        raw = self._live_peers(self._announced_missing_command("deadbeefdeadbeef"))
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        caps = compared["server_peer"]["capabilities"]
        self.assertEqual(caps["state"], "mismatch")
        self.assertEqual(caps["reason"], "arg_contract_mismatch")
        self.assertIn("world_spawn", caps.get("registered_without_announced_command", []))
        payload = server_module._with_ready(compared)
        self.assertFalse(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "arg_contract_mismatch")

    def test_bridge_status_missing_census_cmd_correct_ach_keeps_census_reason(self) -> None:
        # Census-only disagreement with matching ach: reason stays census;
        # historical ready=true among mismatches is preserved.
        raw = self._live_peers(
            self._announced_missing_command(
                server_module.EXPECTED_SERVER_ARG_CONTRACT_HASH
            )
        )
        compared = server_module._with_capability_comparison(
            raw, self._server_registered()
        )
        caps = compared["server_peer"]["capabilities"]
        self.assertEqual(caps["state"], "mismatch")
        self.assertEqual(caps["reason"], "census_disagrees_with_registered_tools")
        payload = server_module._with_ready(compared)
        self.assertTrue(payload["ready"]["ready"])
        self.assertEqual(payload["ready"]["reason"], "ready")


if __name__ == "__main__":
    unittest.main()
