"""Translated poll path of both Enforce bridges (MCPBridge.c, MCPClientBridge.c).

_bridge_harness runs the real constructor, EncodeQueryValue, the load
counters, StartPoll, the poll callback, the dispatch queue and PostResult
against a fake clock and transport. Shared by the A1 load-counter tests and
the poll URL length test; it stays here so one test module does not import
another.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceMap,
    EnforceString,
    clean,
    method_body,
    translate,
)


_MISSION = addon_root() / "scripts" / "5_Mission"


_CONST_STRING_DECL = re.compile(r"\bconst\s+string\s+([A-Za-z_]\w*)\s*=\s*([^;]+);")
_CONST_INT_DECL = re.compile(r"\bconst\s+int\s+([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*;")
_CONST_FLOAT_DECL = re.compile(r"\bconst\s+float\s+([A-Za-z_]\w*)\s*=\s*(-?\d+(?:\.\d+)?)\s*;")
_CONST_STRING_CHUNK = re.compile(r'"((?:\\.|[^"\\\n])*)"')
_ESCAPE_SEQ = re.compile(r"\\(.)")
_ESCAPE_MAP = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}
_STRING_LITERAL = re.compile(r'"(?:\\.|[^"\\\n])*"')
_DECLARED_MEMBER = re.compile(
    r"\b(?:protected|private)\s+(?:static\s+)?(?P<ref>ref\s+)?"
    r"(?P<type>[A-Za-z_]\w*(?:\s*<[^;{}()=]*>)?)\s+(?P<name>m_\w+)\s*;"
)


_GLOBAL_CONST_STRINGS: dict[str, str] | None = None


def _parse_const_string_expr(name: str, expr: str) -> str:
    chunks = [
        _ESCAPE_SEQ.sub(lambda m: _ESCAPE_MAP.get(m.group(1), m.group(1)), raw)
        for raw in _CONST_STRING_CHUNK.findall(expr)
    ]
    glue = re.sub(r"\s+", "", _CONST_STRING_CHUNK.sub("", expr))
    if not chunks or glue != "+" * (len(chunks) - 1):
        raise AssertionError(f"unsupported const string initializer for {name}: {expr!r}")
    return "".join(chunks)


def _global_const_strings() -> dict[str, str]:
    global _GLOBAL_CONST_STRINGS
    if _GLOBAL_CONST_STRINGS is None:
        found: dict[str, str] = {}
        cleaned = clean((_MISSION / "MCPMessages.c").read_text(encoding="utf-8"))
        masked = _STRING_LITERAL.sub(lambda m: '"' + (" " * (len(m.group(0)) - 2)) + '"', cleaned)
        for match in _CONST_STRING_DECL.finditer(cleaned):
            prefix = masked[: match.start()]
            if prefix.count("{") == prefix.count("}"):
                found[match.group(1)] = _parse_const_string_expr(match.group(1), match.group(2))
        if "MCP_BRIDGE_VERSION" not in found:
            raise AssertionError("MCP_BRIDGE_VERSION global const string not found in MCPMessages.c")
        _GLOBAL_CONST_STRINGS = found
    return _GLOBAL_CONST_STRINGS


def _dispatch_branches(source: str) -> list[str]:
    body = method_body(source, "protected void Dispatch(MCPCommand command)")
    first = re.search(r'\bif\s*\(\s*command\.cmd\s*==\s*"', body)
    if first is None:
        raise AssertionError("Dispatch has no command.cmd chain")
    pos = first.start()
    head_re = re.compile(r'(?:if|else\s+if)\s*\(\s*command\.cmd\s*==\s*"([^"]+)"\s*\)')
    token_re = re.compile(r'"(?:\\.|[^"\\])*"|[{}]')
    names: list[str] = []
    while True:
        head = head_re.match(body, pos)
        if head is None:
            raise AssertionError(f"broken Dispatch chain at {body[pos:pos + 40]!r}")
        names.append(head.group(1))
        brace = body.index("{", head.end())
        depth = 0
        end = brace
        for tok in token_re.finditer(body, brace):
            if tok.group() == "{":
                depth += 1
            elif tok.group() == "}":
                depth -= 1
                if depth == 0:
                    end = tok.end()
                    break
        pos = end
        while pos < len(body) and body[pos].isspace():
            pos += 1
        if not body.startswith("else", pos):
            raise AssertionError("Dispatch chain lacks terminal else")
        after_else = pos + len("else")
        while after_else < len(body) and body[after_else].isspace():
            after_else += 1
        if re.match(r"if\b", body[after_else:]):
            continue
        if 'result.error = "unknown_command";' not in body[after_else:]:
            raise AssertionError("Dispatch terminal else lacks unknown_command")
        return sorted(names)


def _int_divide(value: int, divisor: int) -> int:
    if not -2_147_483_648 <= value <= 2_147_483_647:
        raise AssertionError(f"int32 overflow before division: {value}")
    return value // divisor if value >= 0 else -(-value // divisor)


def _bridge_harness(name: str, *, source: str | None = None) -> dict:
    """Execute real constructor/encode/counter/poll/drain/post bodies with fake dispatch and clock.

    This exercises source control flow, not an Enforce compiler or the clock's
    engine resolution. Only logging, integer division, float-to-int truncation
    and g_Game.GetVersion's out parameter are lowered. Cached poll fields start
    at Enforce's empty-string default and are populated solely by running the
    translated bridge constructor.
    """
    if source is None:
        source = (_MISSION / name).read_text(encoding="utf-8")
    cleaned = clean(source)
    ctor_sig = "void MCPBridge()" if name == "MCPBridge.c" else "void MCPClientBridge()"
    ctor_name = "MCPBridge" if name == "MCPBridge.c" else "MCPClientBridge"

    class WireString(EnforceString):
        def __add__(self, other):
            if isinstance(other, int):
                return WireString(self.data + str(other).encode("ascii"))
            if isinstance(other, EnforceString):
                return WireString(self.data + other.data)
            return NotImplemented

        def __radd__(self, other):
            if isinstance(other, int):
                return WireString(str(other).encode("ascii") + self.data)
            if isinstance(other, EnforceString):
                return WireString(other.data + self.data)
            return NotImplemented

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

    class Pending(EnforceArray):
        def Remove(self, index):
            del self[index]

        def RemoveOrdered(self, index):
            del self[index]

    game = SimpleNamespace(ms=100, version="1.30", GetTime=lambda: game.ms)
    clock = SimpleNamespace(ticks=1_000_000)
    posted: list[bytes] = []
    polls: list[bytes] = []
    encode_calls: list[bytes] = []
    transport = SimpleNamespace(
        POST=lambda cb, req, body: posted.append(body.data),
        GET=lambda cb, req: polls.append(req.data),
    )
    masked = _STRING_LITERAL.sub(lambda m: '"' + (" " * (len(m.group(0)) - 2)) + '"', cleaned)
    ns: dict = {}
    for cname, cval in _global_const_strings().items():
        ns[cname] = WireString.of(cval)
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

    class Serializer:
        def ReadFromString(self, batch, data, error):
            batch.commands = EnforceArray(ns["commands"])
            return ns["parse_ok"]

        def WriteToString(self, result, pretty, body):
            body.data = result.body
            return ns["serialize_ok"]

    def dispatch(command):
        # Mission time stays fixed within synchronous dispatch; the native
        # performance counter advances independently in 100 ns ticks
        # (ensystem.c:105-106): 30,000 ticks must measure as 3 ms.
        clock.ticks += command.duration_ticks
        ns["PostResult"](command)

    ns.update(
        _S=WireString,
        array=Pending,
        map=EnforceMap,
        IntDivide=_int_divide,
        IntRemainder=lambda value, divisor: value - _int_divide(value, divisor) * divisor,
        FloatToInt=int,
        GameGetVersion=lambda: WireString.of(game.version),
        g_Game=None,
        GetGame=lambda: game,
        TickCount=lambda prev: clock.ticks - prev,
        MCPTelemetryFixtureLine=object,
        MCPOnInventoryMoveRefused=object(),
        MCPCameraPath=object,
        MCPJobRunner=lambda: SimpleNamespace(Count=lambda: 0),
        MCPCommandBatch=lambda: SimpleNamespace(commands=None),
        MCPPollCallback=lambda owner: object(),
        MCPClientPollCallback=lambda owner: object(),
        MCPClientResultCallback=lambda owner: object(),
        AcquireResultCallback=lambda: SimpleNamespace(),
        JsonSerializer=Serializer,
        QueuePendingOrFail=lambda command: ns["m_Pending"].Insert(command),
        Dispatch=dispatch,
        OnPollFail=lambda reason: None,
        this=object(),
        game=game,
        clock=clock,
        posted=posted,
        polls=polls,
        encode_calls=encode_calls,
        commands=[],
        parse_ok=True,
        serialize_ok=True,
    )

    encode_sig = "protected string EncodeQueryValue(string value)"
    encode_body = re.sub(
        r"\b(\w+)\s*/\s*(\w+)",
        r"IntDivide(\1, \2)",
        method_body(source, encode_sig),
    )
    real_encode = translate(encode_sig + "\n{" + encode_body + "}\n", encode_sig, dict(ns))
    ns["real_encode"] = real_encode

    def tracked_encode(value):
        encode_calls.append(value.data)
        return real_encode(value)

    ns["EncodeQueryValue"] = tracked_encode

    signatures = [
        ctor_sig,
        "protected int NextLoadCounter(int value)",
        "protected int AddLoadCounter(int value, int amount)",
        "protected string GetPollVersion()",
        "protected string LoadCounterQuery()",
        "protected void DispatchMeasured(MCPCommand command)",
        "protected void StartPoll()",
        "void OnPollSuccess(string data, int dataSize)",
        "protected void DrainPending()",
        "protected void PostResult(MCPResult result)",
    ]
    if name == "MCPClientBridge.c":
        signatures.extend((
            "protected int OutstandingWork()",
            "protected void EnsurePollCallback()",
            "protected void HoldPollCallback()",
        ))
    else:
        # Extension hooks the poll now calls (expansion-ai-adapter plan 2.1).
        signatures.extend((
            "protected string ServerCapabilities()",
            "protected string ExtensionPollParams()",
        ))
    for signature in signatures:
        body = method_body(source, signature)
        body = re.sub(r"\bLog\([^;\n]*\);", "", body)
        if signature == "protected string GetPollVersion()":
            body = re.sub(
                r"\bg_Game\.GetVersion\(\s*gameVersion\s*\)\s*;",
                "gameVersion = GameGetVersion();",
                body,
            )
        elif signature == "protected string LoadCounterQuery()":
            body = re.sub(r"\bint\s+ms\s*=\s*([^;]+);", r"int ms = FloatToInt(\1);", body)
        elif signature == "protected void DispatchMeasured(MCPCommand command)":
            # The shared subset refuses / and %. Lower these simple operands
            # with int32 bounds checks and Enforce truncation toward zero.
            for operator, helper in (("/", "IntDivide"), ("%", "IntRemainder")):
                body = re.sub(
                    rf"\b(TickCount\(\w+\)|\w+)\s*{operator}\s*(\w+)",
                    rf"{helper}(\1, \2)",
                    body,
                )
        translate(
            signature + "\n{" + body + "}\n",
            signature,
            ns,
            members=set(re.findall(r"\bm_\w+", body)),
            class_types=(
                "MCPCommandBatch",
                "JsonSerializer",
                "MCPCommand",
                "MCPResult",
                "MCPResultCallback",
                "MCPClientResultCallback",
                "MCPPollCallback",
                "MCPClientPollCallback",
            ),
        )

    # Execute the real bridge constructor to populate m_PollCapabilities (and
    # m_PollArgContractHash on the server) from the class constants.
    ns[ctor_name]()
    ns["ctor_encode_calls"] = list(encode_calls)
    encode_calls.clear()

    # Post-TryInit runtime state; cached poll fields are never touched here.
    ns.update(
        MAX_DISPATCH_PER_TICK=2,
        m_Configured=True,
        m_Ctx=transport,
        m_PollCtx=transport,
        m_Key=WireString.of("k"),
        m_Tick=1,
        m_PollInFlight=True,
    )
    return ns
