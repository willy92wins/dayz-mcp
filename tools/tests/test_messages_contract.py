"""Structural contract tests for the M02 wire DTOs.

The parser intentionally operates on source text instead of importing an Enforce
runtime.  Expected members are literals owned by this test, and the mutation
fixtures exercise the failure modes that would otherwise make this gate
tautological.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests._addon_paths import addon_root


MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
EXPECTED_VERSION = "10"


def _without_comments(source: str) -> str:
    """Remove C/Enforce comments while retaining line/character positions."""

    out: list[str] = []
    index = 0
    state = "code"
    while index < len(source):
        char = source[index]
        nxt = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if char == "/" and nxt == "/":
                out.extend("  ")
                index += 2
                state = "line"
                continue
            if char == "/" and nxt == "*":
                out.extend("  ")
                index += 2
                state = "block"
                continue
            if char == '"':
                state = "string"
            out.append(char)
            index += 1
            continue
        if state == "line":
            if char == "\n":
                out.append(char)
                state = "code"
            else:
                out.append(" ")
            index += 1
            continue
        if state == "block":
            if char == "*" and nxt == "/":
                out.extend("  ")
                index += 2
                state = "code"
            else:
                out.append("\n" if char == "\n" else " ")
                index += 1
            continue
        # Strings are copied verbatim so a comment marker inside a literal is
        # not interpreted as a comment.  Escapes are kept intact.
        out.append(char)
        if char == "\\" and index + 1 < len(source):
            out.append(source[index + 1])
            index += 2
            continue
        if char == '"':
            state = "code"
        index += 1
    return "".join(out)


def _class_body(source: str, class_name: str) -> str:
    """Extract one exact top-level class body using balanced braces."""

    clean = _without_comments(source)
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\b", clean)
    if match is None:
        raise AssertionError(f"class {class_name} is absent")
    opening = clean.find("{", match.end())
    if opening < 0:
        raise AssertionError(f"class {class_name} has no opening brace")
    depth = 0
    for index in range(opening, len(clean)):
        if clean[index] == "{":
            depth += 1
        elif clean[index] == "}":
            depth -= 1
            if depth == 0:
                return clean[opening + 1 : index]
    raise AssertionError(f"class {class_name} has unbalanced braces")


_MEMBER_RE = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)


def _members(source: str, class_name: str) -> list[tuple[str, str]]:
    return [(match.group("type"), match.group("name")) for match in _MEMBER_RE.finditer(_class_body(source, class_name))]


def _assert_members(test: unittest.TestCase, source: str, class_name: str, expected: list[tuple[str, str]]) -> None:
    actual = _members(source, class_name)
    test.assertEqual(actual, expected, class_name)


EXPECTED_ENTITY_MEMBERS = [
    ("string", "type"),
    ("string", "classname"),
    ("bool", "has_cargo"),
    ("ref array<float>", "pos"),
    ("float", "distance"),
]
EXPECTED_ARGS_REQUIRED = [("string", "mode"), ("string", "path"), ("string", "root"), ("bool", "bubble")]
EXPECTED_ECHO = [
    ("string", "requested_path"),
    ("string", "requested_root"),
    ("string", "requested_text"),
    ("string", "matched_path"),
]
EXPECTED_RESULT_ANCHORS = [
    ("string", "type"),
    ("bool", "found"),
    ("bool", "seated"),
    ("string", "seat"),
    ("string", "classname"),
]


def _known_good_source() -> str:
    return '''
const string MCP_BRIDGE_VERSION = "10";
class MCPEntityHit { string type; string classname; bool has_cargo; ref array<float> pos; float distance; };
class MCPArgs { string mode; string path; string root; bool bubble; void MCPArgs() { path = ""; } };
class MCPUiRequestEcho { string requested_path; string requested_root; string requested_text; string matched_path; };
class MCPResult { string type; bool found; bool seated; string seat; string classname; ref MCPUiRequestEcho ui_request; };
'''


def _assert_contract(test: unittest.TestCase, source: str) -> None:
    _assert_members(test, source, "MCPEntityHit", EXPECTED_ENTITY_MEMBERS)
    args = _members(source, "MCPArgs")
    for expected in EXPECTED_ARGS_REQUIRED:
        test.assertEqual(args.count(expected), 1, expected)
    test.assertNotIn(("string", "click_mode"), args)
    test.assertNotRegex(_class_body(source, "MCPArgs"), r"\b(?:mode|root|bubble)\s*=")
    _assert_members(test, source, "MCPUiRequestEcho", EXPECTED_ECHO)
    result = _members(source, "MCPResult")
    for expected in EXPECTED_RESULT_ANCHORS:
        test.assertEqual(result.count(expected), 1, expected)
    test.assertEqual(result.count(("ref MCPUiRequestEcho", "ui_request")), 1)

    clean = _without_comments(source)
    test.assertEqual(re.findall(r"MCP_BRIDGE_VERSION\s*=\s*\"([^\"]+)\"", clean), [EXPECTED_VERSION])


class MessagesContractTest(unittest.TestCase):
    def test_known_good_fixture_satisfies_the_independent_contract(self) -> None:
        _assert_contract(self, _known_good_source())

    def test_live_source_contains_the_exact_m02_contract(self) -> None:
        _assert_contract(self, MESSAGES_PATH.read_text(encoding="utf-8"))

    def test_mutants_are_rejected_for_every_required_contract_surface(self) -> None:
        source = _known_good_source()
        mutants = {
            "has_cargo_absent": source.replace(" bool has_cargo;", ""),
            "has_cargo_wrong_type": source.replace("bool has_cargo", "string has_cargo"),
            "has_cargo_wrong_class": source.replace("class MCPEntityHit", "class OtherHit", 1),
            "has_cargo_wrong_location": source.replace(" bool has_cargo;", "", 1).replace("string root;", "string root; bool has_cargo;", 1),
            "root_absent": source.replace(" string root;", ""),
            "root_wrong_type": source.replace("string root", "bool root"),
            "bubble_absent": source.replace(" bool bubble;", ""),
            "bubble_wrong_type": source.replace("bool bubble", "string bubble"),
            "mode_renamed": source.replace("string mode", "string click_mode"),
            "mode_duplicated": source.replace("string mode;", "string mode; string mode;"),
            "mode_default_direct": source.replace("void MCPArgs() {", 'void MCPArgs() { mode = "direct"; '),
            "echo_absent": source.replace("class MCPUiRequestEcho { string requested_path; string requested_root; string requested_text; string matched_path; };\n", ""),
            "echo_member_mutated": source.replace("string requested_text", "bool requested_text"),
            "echo_fifth_member": source.replace("string matched_path;", "string matched_path; string extra;"),
            "ui_request_no_ref": source.replace("ref MCPUiRequestEcho ui_request", "MCPUiRequestEcho ui_request"),
            "ui_request_wrong_type": source.replace("ref MCPUiRequestEcho ui_request", "ref MCPUiSnapshot ui_request"),
            "telemetry_type_mutated": source.replace("class MCPResult { string type;", "class MCPResult { bool type;"),
            "telemetry_found_mutated": source.replace("string type; bool found", "string type; string found"),
            "telemetry_seated_mutated": source.replace("bool found; bool seated", "bool found; string seated"),
            "telemetry_seat_mutated": source.replace("bool seated; string seat", "bool seated; int seat"),
            "telemetry_classname_mutated": source.replace("string seat; string classname", "string seat; bool classname"),
            "version_bumped": source.replace('MCP_BRIDGE_VERSION = "10"', 'MCP_BRIDGE_VERSION = "11"'),
        }
        for name, mutant in mutants.items():
            with self.subTest(mutant=name):
                with self.assertRaises((AssertionError, IndexError)):
                    _assert_contract(self, mutant)

    def test_new_scalar_fields_have_no_constructor_defaults(self) -> None:
        source = MESSAGES_PATH.read_text(encoding="utf-8")
        body = _class_body(source, "MCPArgs")
        self.assertNotRegex(body, r"\broot\s*=")
        self.assertNotRegex(body, r"\bbubble\s*=")
        self.assertNotRegex(body, r"\bmode\s*=\s*\"direct\"")


if __name__ == "__main__":
    unittest.main()
