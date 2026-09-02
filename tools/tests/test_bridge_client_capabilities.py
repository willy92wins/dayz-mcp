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
from pathlib import Path

from tests._addon_paths import addon_root


BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
DISPATCH_SIGNATURE = "protected void Dispatch(MCPCommand command)"
START_POLL_SIGNATURE = "protected void StartPoll()"

# Mandatory members of the client announcement (fb-20260829-023649-8f8c, step 8).
REQUIRED_CLIENT_CAPS = ("key_press", "player_respawn", "ui_dialog")

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_NAMES = 64
MAX_BYTES = 4096
# Longest single string literal anywhere in vanilla is 237 bytes
# (4_world/entities/vehicles/carscript.c:82). The census is longer than that,
# so it is written as short literals joined with `+` at comma boundaries
# (precedent 5_mission/gui/chat/chatline.c:8-10); no piece may approach the
# vanilla ceiling. A compile failure is invisible to a text gate, which is why
# the ceiling is asserted here and its control re-injects the single literal.
MAX_LITERAL_CHARS = 200

CAPS_DECL_RE = re.compile(
    r'protected const string CLIENT_POLL_CAPS = ((?:"[^"\n]*"(?: \+ )?)+);'
)
LITERAL_RE = re.compile(r'"([^"\n]*)"')
BRANCH_RE = re.compile(r'if \(command\.cmd == "([^"\n]*)"\)')
UNKNOWN_LITERAL = 'result.error = "unknown_command";'
ANNOUNCE_LINE = 'request = request + "&caps=" + EncodeQueryValue(CLIENT_POLL_CAPS);'


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


def dispatch_census(source: str) -> list[str]:
    """Every command.cmd branch of Dispatch(), in order, up to unknown_command.

    Raises AssertionError when the chain does not end in the terminal else or
    when a branch sits outside the single if/else-if chain.
    """
    body = _method_body(source, DISPATCH_SIGNATURE)
    if body.count(UNKNOWN_LITERAL) != 1:
        raise AssertionError("Dispatch must end in exactly one unknown_command branch")
    unknown_idx = body.index(UNKNOWN_LITERAL)
    matches = list(BRANCH_RE.finditer(body))
    if not matches:
        raise AssertionError("Dispatch has no command.cmd branches")
    names = [match.group(1) for match in matches]
    if matches[-1].start() > unknown_idx:
        raise AssertionError("a command.cmd branch follows unknown_command")
    # A single chain: one `if`, the rest `else if`, then the terminal `else`.
    plain_if = body.count("if (command.cmd ==")
    else_if = body.count("else if (command.cmd ==")
    if plain_if != len(names) or else_if != len(names) - 1:
        raise AssertionError("command.cmd branches must form one if/else-if chain")
    tail = body[matches[-1].end() : unknown_idx]
    if "\t\telse\n" not in tail or "command.cmd" in tail:
        raise AssertionError("the last branch must be followed by the terminal else")
    if len(set(names)) != len(names):
        raise AssertionError("duplicate command.cmd branch")
    return names


def announced_literals(source: str) -> list[str]:
    """The string literals of the CLIENT_POLL_CAPS initializer, in order."""
    match = CAPS_DECL_RE.search(source)
    if not match:
        raise AssertionError("CLIENT_POLL_CAPS declaration not found")
    pieces = LITERAL_RE.findall(match.group(1))
    if not pieces:
        raise AssertionError("CLIENT_POLL_CAPS has no string literal")
    for index, piece in enumerate(pieces):
        if len(piece) > MAX_LITERAL_CHARS:
            raise AssertionError(
                f"literal piece {index} is {len(piece)} chars, above the {MAX_LITERAL_CHARS} ceiling"
            )
        if piece == "":
            raise AssertionError(f"literal piece {index} is empty")
        last = index == len(pieces) - 1
        if not last and not piece.endswith(","):
            raise AssertionError(f"literal piece {index} must end at a comma boundary")
        if last and piece.endswith(","):
            raise AssertionError("last literal piece must not end with a comma")
    return pieces


def announced_caps(source: str) -> list[str]:
    raw = "".join(announced_literals(source))
    if len(raw.encode("ascii", "strict")) > MAX_BYTES:
        raise AssertionError("announcement exceeds 4096 bytes")
    names = raw.split(",") if raw else []
    if len(names) > MAX_NAMES:
        raise AssertionError("announcement exceeds 64 names")
    for name in names:
        if not NAME_RE.match(name):
            raise AssertionError(f"invalid capability name {name!r}")
    if len(set(names)) != len(names):
        raise AssertionError("duplicate capability name")
    if names != sorted(names):
        raise AssertionError("announcement must be sorted bytewise (canonical)")
    return names


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


def verify_poll_wire(source: str) -> None:
    start_poll = _method_body(source, START_POLL_SIGNATURE)
    for needle in ('"poll?peer=client&key="', '"&ver="', ANNOUNCE_LINE, "m_PollCtx.GET(m_PollCallback, request);"):
        if needle not in start_poll:
            raise AssertionError(f"StartPoll missing {needle!r}")
    if start_poll.index(ANNOUNCE_LINE) > start_poll.index("m_PollCtx.GET(m_PollCallback, request);"):
        raise AssertionError("caps must be appended before the GET is issued")
    if start_poll.index('"&ver="') > start_poll.index(ANNOUNCE_LINE):
        raise AssertionError("caps travel alongside ver (after it)")


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
        }
        for label, mutant in mutants.items():
            with self.subTest(mutant=label):
                self.assertNotEqual(mutant, source, "mutant anchor did not apply")
                with self.assertRaises(AssertionError):
                    verify_announcement_matches_dispatcher(mutant)
                    verify_poll_wire(mutant)


if __name__ == "__main__":
    unittest.main()
