"""fb-20260822-191204-46b3: the bridges accept only http://127.0.0.1:<port>/.

Both bridges read $profile:dayz_mcp.json. TryInit used to keep any URL that
merely started with "http://127.0.0.1:" (StringHasPrefix); the ticket lists
:8765, :9, :abc, :8765.evil.com and :8765@host as passing. It now refuses with
LogInitFailure("config url not loopback") unless IsLoopbackBridgeUrl accepts the
URL: that exact prefix, 1 to 5 ASCII digits worth 1..65535, then one "/". The
"/" is required because the URL is the RestContext base that "poll?..." and
"result?..." are appended to (PollContextUrl, MCPClientBridge.c).

No Enforce runtime: tests.enforce_subset_helpers translates the real
IsLoopbackBridgeUrl and StringHasPrefix of each bridge and runs them against an
oracle written from the rule, not from the source. The URL producers (the
daemon's prepare, install_mcp.py and install-mcp.ps1) go through the same
translated rule, so a producer that changes its form fails here.
"""
from __future__ import annotations

import ast
import io
import json
import re
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

import install_mcp as installer
from dayz_mcp import loopback
from tests._addon_paths import addon_root
from tests._tiers import slow_test
from tests.enforce_subset_helpers import (
    EnforceString,
    SignedAsciiString,
    clean,
    method_body,
    translate,
)

SCRIPTS = addon_root() / "scripts" / "5_Mission"
BRIDGES = ("MCPBridge.c", "MCPClientBridge.c")
TOOLS = Path(__file__).resolve().parents[1]
RULE = "protected bool IsLoopbackBridgeUrl(string url)"
PREFIX = "protected bool StringHasPrefix(string value, string prefix)"
TRY_INIT = "protected void TryInit()"
GATE = "if (!IsLoopbackBridgeUrl(cfg.url))"
CANONICAL = b"http://127.0.0.1:8765/"
_ORACLE = re.compile(rb"http://127\.0\.0\.1:([0-9]{1,5})/")


def oracle(url: bytes) -> bool:
    """The rule as the brief states it, independent of the Enforce source."""
    match = _ORACLE.fullmatch(url)
    return match is not None and 1 <= int(match.group(1)) <= 65535


# The six URLs of the ticket body, all accepted by the old prefix-only check.
TICKET_CASES = {
    "http://127.0.0.1:8765": False,
    "http://127.0.0.1:8765/": True,
    "http://127.0.0.1:9": False,
    "http://127.0.0.1:abc": False,
    "http://127.0.0.1:8765.evil.com": False,
    "http://127.0.0.1:8765@host": False,
}

EXPLICIT_CASES = {
    "http://127.0.0.1:1/": True,
    "http://127.0.0.1:9/": True,
    "http://127.0.0.1:65535/": True,
    # Five digits worth 8765: inside the rule as stated (1-5 digits, 1..65535).
    "http://127.0.0.1:08765/": True,
    "http://127.0.0.1:0/": False,
    "http://127.0.0.1:00000/": False,
    "http://127.0.0.1:65536/": False,
    "http://127.0.0.1:99999/": False,
    "http://127.0.0.1:100000/": False,
    # Six digits are refused even when the value is a valid port.
    "http://127.0.0.1:065535/": False,
    "http://127.0.0.1:008765/": False,
    "http://127.0.0.1:000001/": False,
    "http://127.0.0.1:/": False,
    "http://127.0.0.1:": False,
    "http://127.0.0.1": False,
    "": False,
    "/": False,
    "http://127.0.0.1:8765/poll": False,
    "http://127.0.0.1:8765/?key=x": False,
    "http://127.0.0.1:8765/#top": False,
    "http://127.0.0.1:8765//": False,
    "http://127.0.0.1:8765\\": False,
    "http://127.0.0.1:8765/\\": False,
    "http://127.0.0.1:8765 /": False,
    " http://127.0.0.1:8765/": False,
    "http://127.0.0.1:8765/ ": False,
    "http://127.0.0.1:8765/\n": False,
    "http://127.0.0.1:\t8765/": False,
    "http://127.0.0.1:87 65/": False,
    "http://127.0.0.1:+8765/": False,
    "http://127.0.0.1:-1/": False,
    "http://127.0.0.1:1e3/": False,
    "http://127.0.0.1:0x10/": False,
    "http://user@127.0.0.1:8765/": False,
    "http://127.0.0.1@evil.com:8765/": False,
    "http://127.0.0.1:8765@evil.com/": False,
    "http://127.0.0.10:8765/": False,
    "http://127.0.0.1.evil.com:8765/": False,
    "https://127.0.0.1:8765/": False,
    "HTTP://127.0.0.1:8765/": False,
    "http://localhost:8765/": False,
    "http://[::1]:8765/": False,
    # Fullwidth digits and a fraction slash: multi-byte UTF-8, never ASCII.
    "http://127.0.0.1:８７６５/": False,
    "http://127.0.0.1:8765⁄": False,
}

PORT_SAMPLES = (
    0, 1, 2, 9, 10, 99, 100, 999, 1000, 8765, 9999, 10000, 18765,
    65534, 65535, 65536, 65537, 70000, 99999, 100000, 123456,
)

# Bytes inserted at every position of the canonical URL (the edit sweep).
INSERTED = b"09/@:. \\\x00\x7f\x80\xffa?#%"


def _port_cases() -> tuple[bytes, ...]:
    cases: list[bytes] = []
    for port in PORT_SAMPLES:
        for form in (b"%d", b"%05d", b"%06d"):
            cases.append(b"http://127.0.0.1:" + form % port + b"/")
    return tuple(cases)


def _edit_cases() -> tuple[bytes, ...]:
    """Every one-byte substitution, a byte set inserted anywhere, every deletion."""
    cases: list[bytes] = []
    for position in range(len(CANONICAL)):
        for value in range(256):
            cases.append(CANONICAL[:position] + bytes([value]) + CANONICAL[position + 1 :])
        cases.append(CANONICAL[:position] + CANONICAL[position + 1 :])
    for position in range(len(CANONICAL) + 1):
        for value in INSERTED:
            cases.append(CANONICAL[:position] + bytes([value]) + CANONICAL[position:])
    return tuple(cases)


NAMED_CASES = tuple(url.encode("utf-8") for url in (*TICKET_CASES, *EXPLICIT_CASES))
PORT_CASES = _port_cases()
EDIT_CASES = _edit_cases()


def _source(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def _rule(source: str):
    namespace: dict[str, object] = {}
    translate(source, PREFIX, namespace)
    return translate(source, RULE, namespace)


def _verdicts(rule, url: bytes) -> tuple[bool, bool]:
    """The rule on the same bytes read with unsigned and with signed ToAscii."""
    return rule(EnforceString(url)), rule(SignedAsciiString(url))


def _one_statement_block(body: str, header: str) -> str:
    return " ".join(method_body(body, header).split())


class LoopbackUrlRuleTest(unittest.TestCase):
    """The translated IsLoopbackBridgeUrl of each bridge against the oracle."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = {name: _source(name) for name in BRIDGES}
        cls.rules = {name: _rule(source) for name, source in cls.sources.items()}

    def _assert_matches_oracle(self, cases: tuple[bytes, ...]) -> None:
        for name, rule in self.rules.items():
            mismatches = []
            for url in cases:
                expected = oracle(url)
                if any(byte >= 0x80 for byte in url):
                    verdicts = _verdicts(rule, url)
                else:
                    verdicts = (rule(EnforceString(url)),)
                if any(verdict is not expected for verdict in verdicts):
                    mismatches.append(url)
            self.assertEqual(
                mismatches[:10], [], f"{name}: {len(mismatches)} verdicts differ from the rule"
            )

    def test_ticket_urls_only_the_canonical_form_passes(self) -> None:
        for name, rule in self.rules.items():
            for url, expected in TICKET_CASES.items():
                with self.subTest(bridge=name, url=url):
                    self.assertIs(rule(EnforceString.of(url)), expected)
                    self.assertIs(oracle(url.encode("utf-8")), expected)

    def test_explicit_accepts_and_refusals(self) -> None:
        for name, rule in self.rules.items():
            for url, expected in EXPLICIT_CASES.items():
                with self.subTest(bridge=name, url=url):
                    data = url.encode("utf-8")
                    self.assertEqual(_verdicts(rule, data), (expected, expected))
                    self.assertIs(oracle(data), expected)

    def test_port_digits_and_range(self) -> None:
        self._assert_matches_oracle(PORT_CASES)

    def test_every_one_byte_edit_of_the_canonical_url_follows_the_rule(self) -> None:
        # Both verdicts occur: digit edits that stay a port, everything else refused.
        self.assertGreater(sum(oracle(url) for url in EDIT_CASES), 0)
        self.assertGreater(sum(not oracle(url) for url in EDIT_CASES), 0)
        self._assert_matches_oracle(EDIT_CASES)

    @slow_test
    def test_every_port_field_of_one_to_five_digits(self) -> None:
        # Exhaustive over the port field (111110 digit strings per bridge): every
        # port a producer can write (1..65535, host_config/install_mcp bounds) passes.
        for name, rule in self.rules.items():
            mismatches = []
            for width in range(1, 6):
                for value in range(10**width):
                    url = b"http://127.0.0.1:" + str(value).zfill(width).encode("ascii") + b"/"
                    if rule(EnforceString(url)) is not (1 <= value <= 65535):
                        mismatches.append(url)
            self.assertEqual(mismatches[:10], [], f"{name}: {len(mismatches)} port fields differ")

    def test_both_bridges_carry_the_same_rule(self) -> None:
        server, client = (self.sources[name] for name in BRIDGES)
        self.assertEqual(method_body(server, RULE), method_body(client, RULE))
        self.assertEqual(method_body(server, PREFIX), method_body(client, PREFIX))

    def test_mutants_of_the_rule_disagree_with_the_oracle(self) -> None:
        corpus = NAMED_CASES + PORT_CASES + EDIT_CASES
        mutants = {
            "prefix_only_legacy": (
                "\t\tstring prefix = \"http://127.0.0.1:\";\n",
                "\t\tstring prefix = \"http://127.0.0.1:\";\n"
                "\t\tif (StringHasPrefix(url, prefix))\n\t\t{\n\t\t\treturn true;\n\t\t}\n",
            ),
            "no_final_slash": (
                'if (url.Substring(prefixLength + digitCount, 1) != "/")',
                "if (false)",
            ),
            "six_digits": ("digitCount > 5", "digitCount > 6"),
            "no_prefix": ("if (!StringHasPrefix(url, prefix))", "if (false)"),
            "any_byte_is_a_digit": ("if (code < 48 || code > 57)", "if (false)"),
            "colon_is_a_digit": ("code > 57", "code > 58"),
            "port_zero": ("port < 1 ||", "port < 0 ||"),
            "port_65536": ("port > 65535", "port > 65536"),
            "no_range": ("if (port < 1 || port > 65535)", "if (false)"),
        }
        for bridge, source in self.sources.items():
            unmutated = self.rules[bridge]
            self.assertFalse(any(unmutated(EnforceString(url)) != oracle(url) for url in corpus))
            body_start = source.index("{", source.index(RULE))
            body_end = source.index("\n\t}\n", body_start)
            for label, (old, new) in mutants.items():
                with self.subTest(bridge=bridge, mutant=label):
                    at = source.find(old, body_start, body_end)
                    self.assertGreaterEqual(at, 0, f"{label}: target not in the rule body")
                    mutant = source[:at] + new + source[at + len(old) :]
                    rule = _rule(mutant)
                    self.assertTrue(
                        any(rule(EnforceString(url)) != oracle(url) for url in corpus),
                        f"{label} is not caught",
                    )


class TryInitGateTest(unittest.TestCase):
    """The rule guards every place the bridge URL is taken from the config."""

    def test_try_init_refuses_before_taking_the_url(self) -> None:
        for name in BRIDGES:
            with self.subTest(bridge=name):
                source = _source(name)
                init = method_body(source, TRY_INIT)
                self.assertEqual(init.count(GATE), 1)
                self.assertEqual(
                    _one_statement_block(init, GATE),
                    'LogInitFailure("config url not loopback"); return;',
                )
                gate = init.index(GATE)
                self.assertLess(init.index('if (!cfg.url || cfg.url == "")'), gate)
                self.assertLess(gate, init.index("m_Url = cfg.url;"))
                self.assertLess(gate, init.index("GetRestContext("))
                self.assertNotIn("StringHasPrefix(cfg.url", clean(source))
                self.assertEqual(clean(source).count("m_Url ="), 1)

    def test_no_other_script_reads_the_config_url(self) -> None:
        readers: list[str] = []
        for path in sorted((addon_root() / "scripts").rglob("*.c")):
            text = clean(path.read_text(encoding="utf-8"))
            if not re.search(r"\.url\b", text):
                continue
            init = method_body(text, TRY_INIT) if path.name in BRIDGES else ""
            outside = len(re.findall(r"\.url\b", text)) - len(re.findall(r"\.url\b", init))
            if outside:
                readers.append(f"{path.name}: {outside} use(s) outside TryInit")
        self.assertEqual(readers, [])


class ProducerFormTest(unittest.TestCase):
    """Whatever the producers write, the bridge rule accepts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = {name: _rule(_source(name)) for name in BRIDGES}

    def _assert_accepted(self, url: str) -> None:
        for name, rule in self.rules.items():
            self.assertTrue(rule(EnforceString.of(url)), f"{name} refuses {url!r}")
        self.assertTrue(oracle(url.encode("utf-8")), url)

    def test_daemon_seeded_bridge_config_is_accepted(self) -> None:
        for port in (1, 8765, 65535):
            with self.subTest(port=port), TemporaryDirectory() as temporary:
                profiles = Path(temporary) / "profiles"
                profiles.mkdir()
                state = loopback.ServerState("seed-key", config_port=port)
                state.prepare("run-url", "client", str(profiles))
                written = json.loads((profiles / "dayz_mcp.json").read_text(encoding="utf-8"))
                self._assert_accepted(written["url"])

    def test_installer_url_template_is_accepted_and_its_port_is_bounded(self) -> None:
        tree = ast.parse((TOOLS / "install_mcp.py").read_text(encoding="utf-8"))
        values = [
            value
            for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            for key, value in zip(node.keys, node.values)
            if isinstance(key, ast.Constant) and key.value == "url"
        ]
        self.assertEqual(len(values), 1, "install_mcp.py writes the bridge url in one place")
        template = compile(ast.Expression(body=values[0]), "install_mcp.py", "eval")
        for port in (1, 8765, 65535):
            with self.subTest(port=port):
                options = installer.parse_args(["--port", str(port)])
                self._assert_accepted(eval(template, {"__builtins__": {}}, {"options": options}))
        for port in (0, 65536):
            with self.subTest(port=port), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    installer.parse_args(["--port", str(port)])

    def test_powershell_installer_url_is_accepted(self) -> None:
        script = (TOOLS / "install-mcp.ps1").read_text(encoding="utf-8")
        urls = re.findall(r'^\s*url\s*=\s*"([^"]*)"\s*$', script, re.MULTILINE)
        self.assertEqual(len(urls), 1, "install-mcp.ps1 writes the bridge url in one place")
        self.assertEqual(urls[0].count("$Port"), 1)
        for port in (1, 8765, 65535):
            with self.subTest(port=port):
                self._assert_accepted(urls[0].replace("$Port", str(port)))


if __name__ == "__main__":
    unittest.main()
