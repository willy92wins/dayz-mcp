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
* ``StartPoll`` must append ``&caps=`` through ``EncodeQueryValue`` after
  ``ver`` and before the optional ``inst`` block, so a peer that has no
  instance still announces.

The expected census is never derived from ``loopback.SERVER_COMMANDS``, from
the app, or from the announcement itself. ``exec_enforce`` is a dispatcher
branch and therefore part of the census even though the daemon routes it
through its own set, which is one reason a derived list would be wrong. The
mutation fixtures keep the gate non-tautological.
"""

from __future__ import annotations

import re
import unittest
from urllib.parse import parse_qs, unquote

from tests._addon_paths import addon_root


BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPBridge.c"

DISPATCH_SIGNATURE = "protected void Dispatch(MCPCommand command)"
START_POLL_SIGNATURE = "protected void StartPoll()"
CAPS_WIRE = '"&caps=" + EncodeQueryValue(SERVER_CAPABILITIES)'
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
    "entities_query",
    "exec_enforce",
    "infected_drive",
    "inventory_attach",
    "inventory_give",
    "notify_players",
    "object_anim",
    "object_delete",
    "object_inspect",
    "player_teleport",
    "query_all_players",
    "query_get_in_condition",
    "query_player_state",
    "scene_raycast",
    "surface_query",
    "telemetry_read",
    "vehicle_drive",
    "vehicle_enter",
    "vehicle_prepare_fixture",
    "world_spawn",
    "world_time_set",
    "world_weather_set",
)

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')
_CAPS_DECLARATION = re.compile(r"\bconst\s+string\s+SERVER_CAPABILITIES\s*=\s*([^;]*);")
_STRING_CHUNK = re.compile(r'"([^"\\\n]*)"')
_BRANCH_HEAD = re.compile(r'(?:if|else\s+if)\s*\(\s*command\.cmd\s*==\s*"([^"]*)"\s*\)')
_FIRST_BRANCH = re.compile(r'\bif\s*\(\s*command\.cmd\s*==\s*"')


def _without_comments(source: str) -> str:
    """Blank every comment, keeping strings verbatim and newlines in place."""

    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        if text.startswith('"'):
            return text
        return re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_STRING.sub(replace, source)


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
    start_poll = _method_body(clean, START_POLL_SIGNATURE)
    test.assertEqual(start_poll.count(CAPS_WIRE), 1, "caps must go through EncodeQueryValue exactly once")
    caps_at = start_poll.index(CAPS_WIRE)
    test.assertLess(start_poll.index(VER_WIRE), caps_at, "caps must follow ver")
    test.assertLess(caps_at, start_poll.index(INSTANCE_GUARD), "caps must precede the optional inst block")
    test.assertLess(caps_at, start_poll.index(POLL_GET), "caps must be appended before the GET")
    depth = start_poll[:caps_at].count("{") - start_poll[:caps_at].count("}")
    test.assertEqual(depth, 0, "caps must be unconditional, not nested in a guard")
    test.assertNotIn('"&caps=" + SERVER_CAPABILITIES', start_poll)


def _assert_contract(test: unittest.TestCase, source: str, expected: tuple[str, ...]) -> list[str]:
    clean = _without_comments(source)
    dispatched = _walk_dispatch_chain(_method_body(clean, DISPATCH_SIGNATURE))
    test.assertEqual(len(set(dispatched)), len(dispatched), "duplicate dispatcher branch")
    announced = _capabilities_literal(clean)
    _assert_grammar(test, announced)
    test.assertEqual(announced, sorted(dispatched), "announcement must be the sorted dispatcher census")
    test.assertEqual(announced, list(expected), "announcement must equal the literal expected census")
    _assert_announced_on_every_poll(test, clean)
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

	protected void StartPoll()
	{
		MCPPollCallback cb = new MCPPollCallback(this);
		string request = "poll?key=" + m_Key;
		request = request + "&ver=" + GetPollVersion();
		request = request + "&caps=" + EncodeQueryValue(SERVER_CAPABILITIES);
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

    def test_mutants_are_rejected(self) -> None:
        source = _known_good_source()
        caps_line = '\tprotected const string SERVER_CAPABILITIES = "alpha," + "entities_query," + "zulu";\n'
        caps_wire = "\t\trequest = request + \"&caps=\" + EncodeQueryValue(SERVER_CAPABILITIES);\n"
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
        for needle in (caps_line, caps_wire, entities_branch, terminal_else):
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
