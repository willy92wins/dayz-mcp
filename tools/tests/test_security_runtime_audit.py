from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
AUDITOR_PATH = TOOLS_DIR / "dayz_mcp" / "security_runtime_audit.py"


class SecurityRuntimeAuditTest(unittest.TestCase):
    def _auditor(self):
        if not AUDITOR_PATH.is_file():
            self.fail("security_runtime_audit_missing")
        spec = importlib.util.spec_from_file_location(
            "dayz_mcp.security_runtime_audit", AUDITOR_PATH
        )
        if spec is None or spec.loader is None:
            self.fail("security_runtime_audit_unloadable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _write(root: Path, relative: str, source: str) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path

    def test_productive_runtime_closure_has_no_unaccredited_http_path(self) -> None:
        auditor = self._auditor()
        violations = auditor.audit_runtime_http(TOOLS_DIR)
        self.assertEqual(violations, ())

    def test_aliases_and_key_query_builders_are_detected_in_new_runtime_modules(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/alias_client.py",
                """import http.client as hc
import urllib.parse as up
import urllib.request as ur
from http.client import HTTPConnection as HC
from urllib.request import urlopen as open_url

def leak(key):
    first = HC('127.0.0.1')
    first.request('GET', '/status')
    second = hc.HTTPConnection('127.0.0.1')
    second.request('GET', '/status')
    ur.urlopen('http://127.0.0.1/status')
    open_url('http://127.0.0.1/status')
    return up.urlencode({'key': key})

def assignment_alias(key):
    send = open_url
    factory = HC
    connection = factory('127.0.0.1')
    connection.request('GET', '/status')
    send('http://127.0.0.1/status')
    encode = up.urlencode
    return encode({'key': key})
""",
            )

            violations = auditor.audit_runtime_http(root)

        observed = {
            (finding.relative_path, finding.function, finding.kind)
            for finding in violations
        }
        self.assertIn(("dayz_mcp/alias_client.py", "leak", "http_request"), observed)
        self.assertIn(("dayz_mcp/alias_client.py", "leak", "urlopen"), observed)
        self.assertIn(("dayz_mcp/alias_client.py", "leak", "authenticated_query"), observed)
        self.assertIn(
            ("dayz_mcp/alias_client.py", "assignment_alias", "http_request"),
            observed,
        )
        self.assertIn(
            ("dayz_mcp/alias_client.py", "assignment_alias", "urlopen"),
            observed,
        )
        self.assertIn(
            ("dayz_mcp/alias_client.py", "assignment_alias", "authenticated_query"),
            observed,
        )

    def test_allowlist_is_function_nominal_not_file_wide(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/accredited_daemon_transport.py",
                """import http.client
import urllib.request

def verified_daemon_http_request():
    connection = http.client.HTTPConnection('127.0.0.1')
    connection.request('GET', '/status?key=inside-canonical-helper')
    urllib.request.urlopen('http://127.0.0.1/unexpected-second-sink')

def unexpected_authenticated_bypass():
    urllib.request.urlopen('http://127.0.0.1/status?key=leak')

class Shadow:
    def verified_daemon_http_request(self):
        urllib.request.urlopen('http://127.0.0.1/status?key=shadow')
""",
            )
            self._write(
                root,
                "dayz_mcp/orphan_guard.py",
                """import urllib.request

def probe_listener_responsive(port, *, timeout=1.0, host='127.0.0.1'):
    url = f'http://{host}:{int(port)}/status'
    urllib.request.urlopen(url, timeout=timeout)
""",
            )

            violations = auditor.audit_runtime_http(root)

        observed = {
            (finding.relative_path, finding.function, finding.kind)
            for finding in violations
        }
        self.assertEqual(
            observed,
            {
                (
                    "dayz_mcp/accredited_daemon_transport.py",
                    "unexpected_authenticated_bypass",
                    "urlopen",
                ),
                (
                    "dayz_mcp/accredited_daemon_transport.py",
                    "Shadow.verified_daemon_http_request",
                    "urlopen",
                ),
                (
                    "dayz_mcp/accredited_daemon_transport.py",
                    "Shadow.verified_daemon_http_request",
                    "authenticated_query",
                ),
                (
                    "dayz_mcp/accredited_daemon_transport.py",
                    "verified_daemon_http_request",
                    "urlopen",
                ),
                (
                    "dayz_mcp/accredited_daemon_transport.py",
                    "unexpected_authenticated_bypass",
                    "authenticated_query",
                ),
            },
        )

    def test_allowlist_covers_only_one_sink_of_each_kind(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/server.py",
                "from dayz_mcp import accredited_daemon_transport\n",
            )
            self._write(
                root,
                "dayz_mcp/accredited_daemon_transport.py",
                """import http.client

def verified_daemon_http_request():
    connection = http.client.HTTPConnection('127.0.0.1')
    connection.request('GET', '/status?key=first')
    connection.request('GET', '/status?key=second')
""",
            )

            violations = auditor.audit_runtime_http(root)

        observed = {(finding.function, finding.kind) for finding in violations}
        self.assertIn(("verified_daemon_http_request", "http_request"), observed)
        self.assertIn(
            ("verified_daemon_http_request", "authenticated_query"), observed
        )

    def test_legacy_orphan_guard_transport_name_has_no_allowance(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import orphan_guard\n")
            self._write(
                root,
                "dayz_mcp/orphan_guard.py",
                """import http.client

def verified_daemon_http_request():
    connection = http.client.HTTPConnection('127.0.0.1')
    connection.request('GET', '/status?key=legacy')
""",
            )
            violations = auditor.audit_runtime_http(root)

        observed = {(finding.function, finding.kind) for finding in violations}
        self.assertIn(("verified_daemon_http_request", "http_request"), observed)
        self.assertIn(
            ("verified_daemon_http_request", "authenticated_query"), observed
        )

    def test_indirect_http_transports_and_shadowed_names(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import shapes\n")
            self._write(
                root,
                "dayz_mcp/shapes.py",
                """import functools
import http.client
import urllib.request

def bound_star(*args):
    connection = http.client.HTTPConnection('127.0.0.1')
    send = connection.request
    send(*args)

def partial_alias():
    connection = http.client.HTTPConnection('127.0.0.1')
    send = functools.partial(connection.request, 'GET')
    send('/status')

def opener_alias():
    opener = urllib.request.build_opener()
    opener.open('http://127.0.0.1/status')

def opener_inline():
    urllib.request.build_opener().open('http://127.0.0.1/status')

def low_level():
    connection = http.client.HTTPConnection('127.0.0.1')
    connection.putrequest('GET', '/status')
    connection.endheaders()

def dynamic_import():
    return __import__('urllib.request')

def shadowed(urllib, http):
    urllib.request('value')
    http.client.HTTPConnection('value')
""",
            )
            violations = auditor.audit_runtime_http(root)

        observed = {(finding.function, finding.kind) for finding in violations}
        for function in ("bound_star", "partial_alias", "low_level"):
            self.assertIn((function, "http_request"), observed)
        for function in ("opener_alias", "opener_inline", "dynamic_import"):
            self.assertIn((function, "dynamic_http"), observed)
        self.assertFalse(any(function == "shadowed" for function, _kind in observed))

    def test_mcp_client_exclusion_is_narrow_owned_and_evidenced(self) -> None:
        auditor = self._auditor()
        candidates = {
            path.relative_to(TOOLS_DIR).as_posix()
            for path in auditor._runtime_sources(TOOLS_DIR)
        }
        self.assertIn("mcp_client.py", candidates)
        exclusion = auditor.RUNTIME_HTTP_EXCLUSIONS["mcp_client.py"]
        self.assertEqual(exclusion.protocol, "legacy_loopback_harness")
        self.assertEqual(exclusion.owner, "DayZ_MCP phase-gate harness")
        self.assertIn("mcp_server.py", exclusion.reason)
        self.assertEqual(
            set(exclusion.evidence),
            {
                "run-poc.ps1",
                "run-fase1.ps1",
                "run-fase2.ps1",
                "run-fase3.ps1",
            },
        )

        client_source = (TOOLS_DIR / "mcp_client.py").read_text(encoding="utf-8")
        self.assertNotIn("dayz_mcp.daemon", client_source)
        self.assertNotIn("verified_daemon_http_request", client_source)
        for name in exclusion.evidence:
            source = (TOOLS_DIR / name).read_text(encoding="utf-8")
            self.assertIn('"mcp_server.py"', source, name)
            self.assertIn('"mcp_client.py"', source, name)

    def test_exclusion_does_not_hide_same_named_package_module(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/mcp_client.py",
                """from urllib.request import urlopen as send

def bypass():
    send('http://127.0.0.1/status?key=leak')
""",
            )
            violations = auditor.audit_runtime_http(root)

        self.assertEqual(
            {finding.kind for finding in violations},
            {"urlopen", "authenticated_query"},
        )
        self.assertTrue(
            all(
                finding.relative_path == "dayz_mcp/mcp_client.py"
                for finding in violations
            )
        )

    def test_reexports_and_dynamic_getattr_cannot_hide_http_sinks(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/reexport_source.py",
                """from urllib.request import urlopen
send = urlopen
""",
            )
            self._write(
                root,
                "dayz_mcp/reexport_consumer.py",
                """from dayz_mcp.reexport_source import send
import urllib.request as request

def reexport_bypass(key):
    send(f'http://127.0.0.1/status?key={key}')

def getattr_bypass(key):
    dynamic = getattr(request, 'urlopen')
    dynamic('GET', '/status?key=' + key)
""",
            )
            observed = {
                (finding.function, finding.kind)
                for finding in auditor.audit_runtime_http(root)
            }

        self.assertIn(("reexport_bypass", "urlopen"), observed)
        self.assertIn(("getattr_bypass", "dynamic_http"), observed)

    def test_unresolved_getattr_and_bound_connection_aliases_are_blocked(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/dynamic_client.py",
                """import http.client as hc
import urllib.request as request

def bound_alias():
    connection = hc.HTTPConnection('127.0.0.1')
    send = connection.request
    forwarded = send
    forwarded('GET', '/status')

def unresolved(name):
    dynamic = getattr(request, name)
    dynamic('GET', '/status')
""",
            )

            violations = auditor.audit_runtime_http(root)

        observed = {(finding.function, finding.kind) for finding in violations}
        self.assertIn(("bound_alias", "http_request"), observed)
        self.assertIn(("unresolved", "dynamic_http"), observed)

    def test_intermediate_module_and_connection_aliases_are_propagated(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/intermediate_aliases.py",
                """import http.client as hc
import urllib.request as request

def module_alias(name):
    first = request
    second = first
    send = second.urlopen
    send('http://127.0.0.1/status')
    dynamic = getattr(second, name)
    dynamic('GET', '/status')

def connection_alias():
    factory = hc.HTTPConnection
    connection = factory('127.0.0.1')
    first = connection
    second = first
    send = second.request
    send('GET', '/status')

def negative_module_alias(obj, name):
    first = obj
    second = first
    getattr(second, name)()

def negative_connection_alias():
    connection = object()
    alias = connection
    send = alias.request
    send('not-http')
""",
            )

            violations = auditor.audit_runtime_http(root)

        observed = {(finding.function, finding.kind) for finding in violations}
        self.assertIn(("module_alias", "urlopen"), observed)
        self.assertIn(("module_alias", "dynamic_http"), observed)
        self.assertIn(("connection_alias", "http_request"), observed)
        self.assertFalse(
            any(
                function in {"negative_module_alias", "negative_connection_alias"}
                for function, _kind in observed
            )
        )

    def test_positional_and_keyword_request_bodies_are_sensitive(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/body_client.py",
                """from urllib.request import Request, urlopen

def positional(url):
    Request(url, b'secret')
    urlopen(url, b'secret')

def keyword(url):
    Request(url, data=b'secret')
    urlopen(url, data=b'secret')

def no_body(url):
    Request(url)
    urlopen(url)
""",
            )

            violations = auditor.audit_runtime_http(root)

        sensitive = [
            finding for finding in violations if finding.kind == "sensitive_body"
        ]
        self.assertEqual(
            {(finding.function, finding.kind) for finding in sensitive},
            {("positional", "sensitive_body"), ("keyword", "sensitive_body")},
        )
        self.assertEqual(len(sensitive), 4)
        self.assertFalse(
            any(
                finding.function == "no_body" and finding.kind == "sensitive_body"
                for finding in violations
            )
        )

    def test_relative_import_reexport_chain_is_resolved(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/transport.py",
                "from urllib.request import urlopen as send\n",
            )
            self._write(
                root,
                "dayz_mcp/facade.py",
                "from .transport import send as forwarded\n",
            )
            self._write(
                root,
                "dayz_mcp/client.py",
                """from .facade import forwarded

def relative_bypass():
    forwarded('http://127.0.0.1/status')
""",
            )

            violations = auditor.audit_runtime_http(root)

        self.assertIn(
            ("dayz_mcp/client.py", "relative_bypass", "urlopen"),
            {
                (finding.relative_path, finding.function, finding.kind)
                for finding in violations
            },
        )

    def test_joined_concat_and_partial_query_key_fragments_are_blocked(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/query_client.py",
                """from urllib.request import urlopen

def literal(key):
    urlopen('http://127.0.0.1/status?key=' + key)

def joined(key):
    urlopen(f'http://127.0.0.1/status&key={key}')

def partial(key):
    urlopen('http://127.0.0.1/status?' + 'ke' + 'y=' + key)
""",
            )

            violations = auditor.audit_runtime_http(root)

        authenticated = {
            finding.function
            for finding in violations
            if finding.kind == "authenticated_query"
        }
        self.assertEqual(authenticated, {"literal", "joined", "partial"})

    def test_query_taint_flows_through_assignments_and_urlencode(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/query_taint.py",
                """import urllib.parse as parse
import urllib.request as request

def assigned_fstring(key):
    target = f'http://127.0.0.1/status?key={key}'
    request.urlopen(target)

def assigned_concat(key):
    marker = '?' + 'ke' + 'y='
    target = 'http://127.0.0.1/status' + marker + key
    request.urlopen(target)

def encoded_mapping(key):
    params = {'key': key}
    encoded = parse.urlencode(params)
    target = 'http://127.0.0.1/status?' + encoded
    request.urlopen(target)

def negative_plain(value):
    target = f'http://127.0.0.1/status?page={value}'
    request.urlopen(target)

def negative_mapping(value):
    params = {'page': value}
    encoded = parse.urlencode(params)
    target = 'http://127.0.0.1/status?' + encoded
    request.urlopen(target)

def negative_key_value():
    params = {'page': 'key'}
    encoded = parse.urlencode(params)
    target = 'http://127.0.0.1/status?' + encoded
    request.urlopen(target)

def negative_key_string():
    value = 'key'
    encoded = parse.urlencode(value)
    target = 'http://127.0.0.1/status?' + encoded
    request.urlopen(target)
""",
            )

            violations = auditor.audit_runtime_http(root)

        authenticated = {
            finding.function
            for finding in violations
            if finding.kind == "authenticated_query"
        }
        self.assertEqual(
            authenticated,
            {"assigned_fstring", "assigned_concat", "encoded_mapping"},
        )

    def test_nominal_probe_allowance_rejects_secret_query_or_body(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/orphan_guard.py",
                """import urllib.request

def probe_listener_responsive(key):
    request = urllib.request.Request(
        f'http://127.0.0.1/status?key={key}', data=b'sensitive-body'
    )
    urllib.request.urlopen(request)
""",
            )
            violations = auditor.audit_runtime_http(root)

        self.assertTrue(
            any(
                finding.function == "probe_listener_responsive"
                and finding.kind == "sensitive_probe"
                for finding in violations
            )
        )

    def test_only_productive_import_closure_is_audited(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import safe\n")
            self._write(root, "dayz_mcp/safe.py", "VALUE = 1\n")
            self._write(
                root,
                "dayz_mcp/unreachable.py",
                """import urllib.request
def legacy_only():
    urllib.request.urlopen('http://127.0.0.1/legacy')
""",
            )

            violations = auditor.audit_runtime_http(root)

        self.assertEqual(violations, ())

    def test_productive_closure_blocks_all_adversarial_http_canaries(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import canaries\n")
            self._write(
                root,
                "dayz_mcp/transport.py",
                "from urllib.request import urlopen as send\n",
            )
            self._write(
                root,
                "dayz_mcp/facade.py",
                "from .transport import send as forwarded\n",
            )
            self._write(
                root,
                "dayz_mcp/canaries.py",
                """import http.client as hc
import urllib.request as request
from .facade import forwarded

class AttributeConnection:
    def __init__(self):
        self.conn = hc.HTTPConnection('127.0.0.1')

    def leak(self):
        self.conn.request('GET', '/status')

    def bound(self):
        send = self.conn.request
        send('GET', '/status')

class InheritedConnectionBase:
    def __init__(self):
        self.conn = hc.HTTPConnection('127.0.0.1')

class InheritedConnectionChild(InheritedConnectionBase):
    def leak(self):
        self.conn.request('GET', '/status')

def relative_reexport():
    forwarded('http://127.0.0.1/status')

def unresolved_getattr(name):
    dynamic = getattr(request, name)
    dynamic('GET', '/status')

def inline_connection_getattr(name):
    getattr(hc.HTTPConnection('127.0.0.1'), name)('GET', '/status')

def constant_safe_getattr():
    getattr(hc.HTTPConnection('127.0.0.1'), 'close')()

def positional_body(url):
    request.Request(url, b'sensitive')

def concatenated_query(key):
    request.urlopen('http://127.0.0.1/status?key=' + key)

def negative_unrelated(obj, name):
    getattr(obj, name)()

class NegativeUnrelatedConnection:
    def __init__(self):
        self.conn = object()

    def safe(self):
        send = self.conn.request
        send('not-http')
""",
            )

            violations = auditor.audit_runtime_http(root)

        observed = {
            (finding.function, finding.kind) for finding in violations
        }
        self.assertIn(("AttributeConnection.leak", "http_request"), observed)
        self.assertIn(("AttributeConnection.bound", "http_request"), observed)
        self.assertIn(("InheritedConnectionChild.leak", "http_request"), observed)
        self.assertIn(("relative_reexport", "urlopen"), observed)
        self.assertIn(("unresolved_getattr", "dynamic_http"), observed)
        self.assertIn(("inline_connection_getattr", "dynamic_http"), observed)
        self.assertIn(("positional_body", "sensitive_body"), observed)
        self.assertIn(("concatenated_query", "authenticated_query"), observed)
        self.assertFalse(
            any(function == "negative_unrelated" for function, _kind in observed)
        )
        self.assertFalse(
            any(function == "constant_safe_getattr" for function, _kind in observed)
        )
        self.assertFalse(
            any(
                function == "NegativeUnrelatedConnection.safe"
                for function, _kind in observed
            )
        )

    def test_nominal_probe_follows_static_query_fragments_through_variables(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/orphan_guard.py",
                """import urllib.request

def probe_listener_responsive(key):
    marker = '?' + 'ke' + 'y='
    url = 'http://127.0.0.1/status' + marker + key
    urllib.request.urlopen(url)
    marker = 'safe'
    url = 'http://127.0.0.1/status'
""",
            )

            violations = auditor.audit_runtime_http(root)

        self.assertIn(
            ("probe_listener_responsive", "sensitive_probe"),
            {(finding.function, finding.kind) for finding in violations},
        )

    def test_nominal_probe_conditional_safe_assignment_cannot_erase_taint(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/orphan_guard.py",
                """import urllib.request

def probe_listener_responsive(key):
    url = 'http://127.0.0.1/status?key=' + key
    if False:
        url = 'http://127.0.0.1/status'
    urllib.request.urlopen(url)
""",
            )

            violations = auditor.audit_runtime_http(root)

        self.assertIn(
            ("probe_listener_responsive", "sensitive_probe"),
            {(finding.function, finding.kind) for finding in violations},
        )

    def test_connection_attribute_inheritance_follows_class_alias(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/aliased_base.py",
                """import http.client as hc

class Base:
    def __init__(self):
        self.conn = hc.HTTPConnection('127.0.0.1')

Alias = Base

class Child(Alias):
    def leak(self):
        self.conn.request('GET', '/status')
""",
            )

            violations = auditor.audit_runtime_http(root)

        self.assertIn(
            ("Child.leak", "http_request"),
            {(finding.function, finding.kind) for finding in violations},
        )

    def test_two_phase_index_covers_order_class_cross_module_and_late_imports(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/server.py",
                "from dayz_mcp import canaries, child, late_import\n",
            )
            self._write(
                root,
                "dayz_mcp/canaries.py",
                """import http.client as hc

class MethodOrder:
    def leak(self):
        self.conn.request('GET', '/status')

    def __init__(self):
        self.conn = hc.HTTPConnection('127.0.0.1')

class ClassAttribute:
    conn = hc.HTTPConnection('127.0.0.1')

    def leak(self):
        self.conn.request('GET', '/status')

class Namespace:
    class Base:
        def __init__(self):
            self.conn = hc.HTTPConnection('127.0.0.1')

Alias = Namespace.Base

class NestedAliasChild(Alias):
    def leak(self):
        self.conn.request('GET', '/status')
""",
            )
            self._write(
                root,
                "dayz_mcp/base.py",
                """import http.client as hc

class Base:
    def __init__(self):
        self.conn = hc.HTTPConnection('127.0.0.1')
""",
            )
            self._write(
                root,
                "dayz_mcp/child.py",
                """from .base import Base

class CrossModuleChild(Base):
    def leak(self):
        self.conn.request('GET', '/status')
""",
            )
            self._write(
                root,
                "dayz_mcp/late_import.py",
                """def leak():
    request.urlopen('http://127.0.0.1/status')

import urllib.request as request
""",
            )

            violations = auditor.audit_runtime_http(root)

        observed = {(finding.function, finding.kind) for finding in violations}
        self.assertIn(("MethodOrder.leak", "http_request"), observed)
        self.assertIn(("ClassAttribute.leak", "http_request"), observed)
        self.assertIn(("NestedAliasChild.leak", "http_request"), observed)
        self.assertIn(("CrossModuleChild.leak", "http_request"), observed)
        self.assertIn(("leak", "urlopen"), observed)

    def test_probe_fails_closed_for_r9_assignment_and_query_forms(self) -> None:
        auditor = self._auditor()
        cases = {
            "augassign": """import urllib.request
def probe_listener_responsive(key):
    url = 'http://127.0.0.1/status'
    url += '?key=' + key
    urllib.request.urlopen(url)
""",
            "ifexp": """import urllib.request
def probe_listener_responsive(key):
    url = 'http://127.0.0.1/status' if key is None else '?key=' + key
    urllib.request.urlopen(url)
""",
            "boolop": """import urllib.request
def probe_listener_responsive(key):
    url = 'http://127.0.0.1/status' + (key and '?key=' + key)
    urllib.request.urlopen(url)
""",
            "tuple_unpack": """import urllib.request
def probe_listener_responsive(key):
    ignored, url = ('safe', 'http://127.0.0.1/status?key=' + key)
    urllib.request.urlopen(url)
""",
            "mutable_mapping": """import urllib.parse
import urllib.request
def probe_listener_responsive(key):
    params = {}
    params['key'] = key
    query = urllib.parse.urlencode(params)
    urllib.request.urlopen('http://127.0.0.1/status?' + query)
""",
            "urlencode_dict_keyword": """import urllib.parse
import urllib.request
def probe_listener_responsive(key):
    query = urllib.parse.urlencode(dict(key=key))
    urllib.request.urlopen('http://127.0.0.1/status?' + query)
""",
            "second_bound_sink": """import urllib.request
sink = transport.request
def probe_listener_responsive(port, *, timeout=1.0, host='127.0.0.1'):
    url = f'http://{host}:{int(port)}/status'
    sink('GET', '/status')
    urllib.request.urlopen(url, timeout=timeout)
""",
            "stray_query_fragment": """import urllib.request
def probe_listener_responsive(port, *, timeout=1.0, host='127.0.0.1'):
    url = f'http://{host}:{int(port)}/status'
    '?keyboard#fragment'
    urllib.request.urlopen(url, timeout=timeout)
""",
            "dict_expression": """import urllib.request
def probe_listener_responsive(port, *, timeout=1.0, host='127.0.0.1'):
    url = f'http://{host}:{int(port)}/status'
    dict(page='1')
    urllib.request.urlopen(url, timeout=timeout)
""",
        }
        for name, source in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(
                    root,
                    "dayz_mcp/server.py",
                    "from dayz_mcp import orphan_guard\n",
                )
                self._write(root, "dayz_mcp/orphan_guard.py", source)
                violations = auditor.audit_runtime_http(root)
            self.assertIn(
                ("probe_listener_responsive", "sensitive_probe"),
                {(finding.function, finding.kind) for finding in violations},
                name,
            )

    def test_probe_accepts_only_statically_safe_url_and_body(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import orphan_guard\n")
            self._write(
                root,
                "dayz_mcp/orphan_guard.py",
                """import urllib.request
def probe_listener_responsive(port, *, timeout=1.0, host='127.0.0.1'):
    url = f'http://{host}:{int(port)}/status'
    urllib.request.urlopen(url, timeout=timeout)
""",
            )
            violations = auditor.audit_runtime_http(root)
        self.assertFalse(
            any(finding.kind == "sensitive_probe" for finding in violations)
        )

    def test_syntactic_request_sinks_cover_imported_bases_and_factories(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import shapes\n")
            self._write(root, "dayz_mcp/relative_base.py", "class Base:\n    pass\n")
            self._write(root, "dayz_mcp/absolute_base.py", "class Base:\n    pass\n")
            self._write(root, "dayz_mcp/qualified_base.py", "class Base:\n    pass\n")
            self._write(
                root,
                "dayz_mcp/bound_transport.py",
                "send = provider.request\n",
            )
            self._write(
                root,
                "dayz_mcp/shapes.py",
                """from .relative_base import Base as RelativeBase
from dayz_mcp.absolute_base import Base as AbsoluteBase
import dayz_mcp.qualified_base as qualified
from .bound_transport import send as imported_send

class RelativeChild(RelativeBase):
    def leak(self):
        self.transport.request('GET', '/status')

class AbsoluteChild(AbsoluteBase):
    def leak(self):
        send = self.transport.request
        send(method='GET', url='/status')

class QualifiedChild(qualified.Base):
    def leak(self):
        self.transport.request('GET', '/status')

def conditional_factories(flag, left, right):
    first = left if flag else right
    first.request('GET', '/status')
    second = left or right
    send = second.request
    send(method='GET', url='/status')

def imported_bound_alias():
    imported_send('GET', '/status')

def non_http_one_arg(obj, name):
    dynamic = getattr(obj, name)
    dynamic('value')
    send = obj.request
    send('value')
""",
            )
            violations = auditor.audit_runtime_http(root)

        observed = {(finding.function, finding.kind) for finding in violations}
        for function in (
            "RelativeChild.leak",
            "AbsoluteChild.leak",
            "QualifiedChild.leak",
            "conditional_factories",
            "imported_bound_alias",
        ):
            self.assertIn((function, "http_request"), observed)
        self.assertFalse(
            any(function == "non_http_one_arg" for function, _kind in observed)
        )

    def test_getattr_requires_an_http_call_shape(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import shapes\n")
            self._write(
                root,
                "dayz_mcp/shapes.py",
                """def positional(obj, name):
    getattr(obj, name)('GET', '/status')

def keyword(obj, name):
    dynamic = getattr(obj, name)
    dynamic(method='GET', url='/status')

def one_arg(obj, name):
    dynamic = getattr(obj, name)
    dynamic('value')
""",
            )
            violations = auditor.audit_runtime_http(root)

        observed = {(finding.function, finding.kind) for finding in violations}
        self.assertIn(("positional", "dynamic_http"), observed)
        self.assertIn(("keyword", "dynamic_http"), observed)
        self.assertFalse(any(function == "one_arg" for function, _kind in observed))

    def test_query_key_boundary_and_indirect_builders(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import query_shapes\n")
            self._write(
                root,
                "dayz_mcp/query_shapes.py",
                """import urllib.parse
import urllib.request

def conditional(key, enabled):
    suffix = ('?key=' + key) if enabled else ''
    urllib.request.urlopen('http://127.0.0.1/status' + suffix)

def mutable_mapping(key):
    params = {}
    params['key'] = key
    query = urllib.parse.urlencode(params)
    urllib.request.urlopen('http://127.0.0.1/status?' + query)

def dict_keyword(key):
    params = dict(key=key)
    query = urllib.parse.urlencode(params)
    urllib.request.urlopen('http://127.0.0.1/status?' + query)

def similar_names():
    urllib.request.urlopen('http://127.0.0.1/status?keyboard=1&keynote=2')
""",
            )
            violations = auditor.audit_runtime_http(root)

        observed = {(finding.function, finding.kind) for finding in violations}
        for function in ("conditional", "mutable_mapping", "dict_keyword"):
            self.assertIn((function, "authenticated_query"), observed)
        self.assertNotIn(("similar_names", "authenticated_query"), observed)

    def test_probe_grammar_isolated_nested_scopes_and_audits_their_sinks(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import orphan_guard\n")
            self._write(
                root,
                "dayz_mcp/orphan_guard.py",
                """import urllib.request

def probe_listener_responsive(port, *, timeout=1.0, host='127.0.0.1'):
    url = f'http://{host}:{int(port)}/status'
    def nested_binding():
        shadow = 'http://127.0.0.1/status?key=nested'
        return shadow
    class Nested:
        def leak(self, obj, name):
            getattr(obj, name)(method='GET', url='/status')
    (lambda obj, name: getattr(obj, name)(method='GET', url='/status'))(object(), 'send')
    urllib.request.urlopen(url, timeout=timeout)
""",
            )
            violations = auditor.audit_runtime_http(root)

        self.assertIn(
            ("probe_listener_responsive", "sensitive_probe"),
            {(finding.function, finding.kind) for finding in violations},
        )
        dynamic_functions = {
            finding.function
            for finding in violations
            if finding.kind == "dynamic_http"
        }
        self.assertTrue(any("Nested" in function for function in dynamic_functions))
        self.assertTrue(any("<lambda>" in function for function in dynamic_functions))

    def test_starred_kwargs_and_walrus_preserve_all_http_carriers(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import carriers\n")
            self._write(
                root,
                "dayz_mcp/reexport.py",
                "send = provider.request\n",
            )
            self._write(
                root,
                "dayz_mcp/carriers.py",
                """from .reexport import send as imported_send

def carriers(obj, name, args, kwargs):
    obj.request(*args)
    bound = obj.request
    bound(**kwargs)
    imported_send(*args)
    dynamic = getattr(obj, name)
    dynamic(**kwargs)
    (walrus := obj.request)(*args)
    obj.request('one')
    bound('one')
    imported_send('one')
    dynamic('one')
""",
            )
            violations = auditor.audit_runtime_http(root)

        self.assertEqual(
            {
                (
                    finding.relative_path,
                    finding.function,
                    finding.line,
                    finding.kind,
                )
                for finding in violations
            },
            {
                ("dayz_mcp/carriers.py", "carriers", 4, "http_request"),
                ("dayz_mcp/carriers.py", "carriers", 6, "http_request"),
                ("dayz_mcp/carriers.py", "carriers", 7, "http_request"),
                ("dayz_mcp/carriers.py", "carriers", 9, "dynamic_http"),
                ("dayz_mcp/carriers.py", "carriers", 10, "http_request"),
            },
        )

    def test_real_tree_has_only_the_canonical_native_backend_call_site(self) -> None:
        auditor = self._auditor()
        self.assertTrue((TOOLS_DIR / "dayz_mcp" / "native_launcher_backend.py").is_file())
        findings = auditor.audit_process_creation(TOOLS_DIR)
        if findings:
            self.fail(
                "unexpected process-creation findings:\n"
                + "\n".join(
                    f"{finding.relative_path}:{finding.line}:"
                    f"{finding.function}:{finding.kind}"
                    for finding in findings
                )
            )

    def test_exact_future_native_backend_boundary_accepts_one_direct_call(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/native_launcher_backend.py",
                """import ctypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
CREATE_UNICODE_ENVIRONMENT = 0x400
CREATE_SUSPENDED = 4
DEBUG_PROCESS = 1
EXTENDED_STARTUPINFO_PRESENT = 0x80000
CREATE_FLAGS = CREATE_UNICODE_ENVIRONMENT | DEBUG_PROCESS | EXTENDED_STARTUPINFO_PRESENT

def _create_registered_launcher(opened_launcher):
    application_name = str(opened_launcher.path)
    command_line = object()
    environment_block = object()
    current_directory = object()
    startup_info = object()
    process_info = object()
    return _kernel32.CreateProcessW(
        application_name,
        command_line,
        None,
        None,
        True,
        CREATE_FLAGS,
        environment_block,
        current_directory,
        ctypes.byref(startup_info),
        ctypes.byref(process_info),
    )
""",
            )
            self.assertEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_rejects_noncanonical_createprocess_contract(self) -> None:
        auditor = self._auditor()
        canonical_call = """_kernel32.CreateProcessW(
        application_name, command_line, None, None, True, CREATE_FLAGS,
        environment_block, current_directory, ctypes.byref(startup_info),
        ctypes.byref(process_info))"""
        fixtures = {
            "call_absent": "return 0",
            "receiver_alternate": canonical_call.replace(
                "_kernel32.CreateProcessW", "api.CreateProcessW"
            ),
            "free_application": canonical_call.replace(
                "application_name", "executable", 1
            ),
            "free_command_line": canonical_call.replace(
                "command_line", "argv", 1
            ),
            "inheritance_disabled": canonical_call.replace("None, None, True,", "None, None, False,"),
            "flags_incomplete": canonical_call.replace("CREATE_FLAGS", "CREATE_SUSPENDED"),
            "free_environment": canonical_call.replace(
                "environment_block, current_directory", "env, current_directory"
            ),
            "free_current_directory": canonical_call.replace(
                "environment_block, current_directory",
                "environment_block, cwd",
            ),
            "wrong_startup_pointer": canonical_call.replace(
                "ctypes.byref(startup_info)", "startup_info"
            ),
        }
        for label, call in fixtures.items():
            source = f"""import ctypes
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
api = _kernel32
CREATE_UNICODE_ENVIRONMENT = 0x400
CREATE_SUSPENDED = 4
DEBUG_PROCESS = 1
EXTENDED_STARTUPINFO_PRESENT = 0x80000
CREATE_FLAGS = CREATE_UNICODE_ENVIRONMENT | DEBUG_PROCESS | EXTENDED_STARTUPINFO_PRESENT

def _create_registered_launcher(opened_launcher):
    application_name = str(opened_launcher.path)
    command_line = object()
    environment_block = object()
    startup_info = object()
    process_info = object()
    executable = "arbitrary.exe"
    argv = object()
    env = object()
    cwd = object()
    {call}
"""
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(root, "dayz_mcp/native_launcher_backend.py", source)
                self.assertNotEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_rejects_second_call_site_and_wrong_owner(self) -> None:
        auditor = self._auditor()
        fixtures = {
            "second_call": ("dayz_mcp/native_launcher_backend.py", "import ctypes\ndef _create_registered_launcher():\n    ctypes.windll.kernel32.CreateProcessW(None)\n    ctypes.windll.kernel32.CreateProcessW(None)\n"),
            "wrong_function": ("dayz_mcp/native_launcher_backend.py", "import ctypes\ndef create_elsewhere():\n    ctypes.windll.kernel32.CreateProcessW(None)\n"),
            "wrong_module": ("dayz_mcp/secure_launcher.py", "import ctypes\ndef _create_registered_launcher():\n    ctypes.windll.kernel32.CreateProcessW(None)\n"),
        }
        for label, (relative, source) in fixtures.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(root, relative, source)
                self.assertNotEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_rejects_alias_getattr_and_dynamic_lookup(self) -> None:
        auditor = self._auditor()
        bodies = {
            "alias": "spawn = ctypes.windll.kernel32.CreateProcessW\n    return spawn(None)",
            "getattr": "spawn = getattr(ctypes.windll.kernel32, 'CreateProcessW')\n    return spawn(None)",
            "dynamic_import": "module = __import__('subprocess')\n    return module.run(['forbidden'])",
            "dynamic_module_lookup": "module = importlib.import_module('subprocess')\n    return module.run(['forbidden'])",
        }
        for label, body in bodies.items():
            source = f"import ctypes\nimport importlib\n\ndef _create_registered_launcher():\n    {body}\n"
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(root, "dayz_mcp/native_launcher_backend.py", source)
                self.assertNotEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_rejects_alternative_process_surfaces(self) -> None:
        auditor = self._auditor()
        sources = {
            "subprocess": "import subprocess\ndef launch():\n    subprocess.run(['forbidden'])\n",
            "os_system": "import os\ndef launch():\n    os.system('forbidden')\n",
            "shell_execute": "import ctypes\ndef launch():\n    ctypes.windll.shell32.ShellExecuteW(None)\n",
            "win_exec": "import ctypes\ndef launch():\n    ctypes.windll.kernel32.WinExec(None)\n",
        }
        for label, source in sources.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(root, "dayz_mcp/secure_launcher.py", source)
                self.assertNotEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_rejects_variable_getattr_lookup(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/native_launcher_backend.py",
                """import ctypes
def _create_registered_launcher(name):
    spawn = getattr(ctypes.windll.kernel32, name)
    return spawn(None)
""",
            )
            self.assertNotEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_allows_literal_non_process_getattr(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/secure_launcher.py",
                """import ctypes
import os

def inspect_file_api():
    return (
        getattr(ctypes.windll.kernel32, "GetFileInformationByHandleEx", None),
        getattr(os, "O_NOFOLLOW", 0),
    )
""",
            )
            self.assertEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_skips_only_canonical_type_checking_imports(self) -> None:
        auditor = self._auditor()
        helper = "import subprocess\ndef launch():\n    subprocess.run(['forbidden'])\n"
        safe_root = """from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from dayz_mcp.helper import launch
"""
        rebound_root = """from typing import TYPE_CHECKING
TYPE_CHECKING = True
if TYPE_CHECKING:
    from dayz_mcp.helper import launch
"""
        for label, source, expected_clean in (
            ("canonical", safe_root, True),
            ("rebound", rebound_root, False),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(root, "dayz_mcp/secure_launcher.py", source)
                self._write(root, "dayz_mcp/helper.py", helper)
                findings = auditor.audit_process_creation(root)
                self.assertEqual(findings == (), expected_clean)

    def test_process_auditor_allows_literal_non_process_module_members(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/secure_launcher.py",
                """import os
import subprocess

def constants_only():
    return getattr(os, 'O_BINARY', 0), getattr(subprocess, 'CREATE_NO_WINDOW', 0)
""",
            )
            self.assertEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_rejects_nonliteral_dynamic_imports(self) -> None:
        auditor = self._auditor()
        bodies = {
            "import_variable": "name = 'subprocess'\n    module = __import__(name)",
            "import_concat": "module = __import__('sub' + 'process')",
            "import_module_variable": "name = 'subprocess'\n    module = importlib.import_module(name)",
            "import_module_concat": "module = importlib.import_module('sub' + 'process')",
        }
        for label, body in bodies.items():
            source = f"import importlib\n\ndef launch():\n    {body}\n    return module.run(['forbidden'])\n"
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(root, "dayz_mcp/secure_launcher.py", source)
                self.assertNotEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_rejects_module_alias_and_star_import(self) -> None:
        auditor = self._auditor()
        sources = {
            "module_alias": "import subprocess\nm = subprocess\ndef launch():\n    m.run(['forbidden'])\n",
            "star_import": "from subprocess import *\ndef launch():\n    run(['forbidden'])\n",
        }
        for label, source in sources.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(root, "dayz_mcp/secure_launcher.py", source)
                self.assertNotEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_rejects_class_method_with_reserved_name(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/native_launcher_backend.py",
                """import ctypes
class Launcher:
    def _create_registered_launcher(self):
        return ctypes.windll.kernel32.CreateProcessW(None)
""",
            )
            self.assertNotEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_rejects_subprocess_shell_helpers(self) -> None:
        auditor = self._auditor()
        for name in ("getoutput", "getstatusoutput"):
            source = f"import subprocess\ndef launch():\n    return subprocess.{name}('forbidden')\n"
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(root, "dayz_mcp/secure_launcher.py", source)
                self.assertNotEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_rejects_dynamic_code_execution(self) -> None:
        auditor = self._auditor()
        for name in ("eval", "exec", "compile"):
            source = f"def launch(source):\n    return {name}(source)\n"
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(root, "dayz_mcp/secure_launcher.py", source)
                self.assertNotEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_rejects_dynamic_windows_ffi_process_lookup(self) -> None:
        auditor = self._auditor()
        sources = {
            "cdll": "import ctypes\ndef launch():\n    ctypes.CDLL('kernel32').CreateProcessW(None)\n",
            "windll": "import ctypes\ndef launch():\n    ctypes.WinDLL('kernel32').CreateProcessW(None)\n",
            "windll_subscript": "import ctypes\ndef launch(name):\n    ctypes.windll.kernel32[name](None)\n",
            "winapi": "import _winapi\ndef launch():\n    _winapi.CreateProcess(None)\n",
        }
        for label, source in sources.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(root, "dayz_mcp/secure_launcher.py", source)
                self.assertNotEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_follows_transitive_local_import_closure(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/secure_launcher.py",
                "from dayz_mcp.helper import launch\n",
            )
            self._write(
                root,
                "dayz_mcp/helper.py",
                "import subprocess\ndef launch():\n    subprocess.run(['forbidden'])\n",
            )
            findings = auditor.audit_process_creation(root)
            self.assertTrue(
                any(finding.relative_path == "dayz_mcp/helper.py" for finding in findings)
            )

    def test_process_auditor_excludes_type_checking_only_imports(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "dayz_mcp/secure_launcher.py",
                """from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from dayz_mcp.helper import ProcessType
""",
            )
            self._write(
                root,
                "dayz_mcp/helper.py",
                "import subprocess\nsubprocess.run(['type-only'])\n",
            )
            self.assertEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_propagates_aliases_from_all_binding_forms(self) -> None:
        auditor = self._auditor()
        sources = {
            "named_expression": "import subprocess\ndef launch():\n    return (spawn := subprocess.run)(['forbidden'])\n",
            "destructuring": "import subprocess\ndef launch():\n    safe, spawn = (None, subprocess.run)\n    return spawn(['forbidden'])\n",
            "function_default": "import subprocess\ndef launch(spawn=subprocess.run):\n    return spawn(['forbidden'])\n",
            "module_alias": "import subprocess\nm = subprocess\ndef launch():\n    return m.run(['forbidden'])\n",
        }
        for label, source in sources.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(root, "dayz_mcp/secure_launcher.py", source)
                self.assertNotEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_respects_parameter_and_local_shadowing(self) -> None:
        auditor = self._auditor()
        sources = {
            "parameter": "import subprocess\ndef inspect(subprocess):\n    return subprocess.run()\n",
            "local": "import subprocess\ndef inspect(safe):\n    subprocess = safe\n    return subprocess.run()\n",
        }
        for label, source in sources.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(root, "dayz_mcp/secure_launcher.py", source)
                self.assertEqual(auditor.audit_process_creation(root), ())

    def test_process_auditor_does_not_export_comprehension_target_shadowing(self) -> None:
        auditor = self._auditor()
        source = """import subprocess as runner

def launch():
    [runner for runner in ()]
    runner.run(['forbidden'])
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/secure_launcher.py", source)
            findings = auditor.audit_process_creation(root)

        self.assertTrue(
            any(finding.kind == "forbidden_python_process_surface" for finding in findings)
        )

    def test_comprehension_targets_have_isolated_carrier_scopes(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import scopes\n")
            self._write(
                root,
                "dayz_mcp/scopes.py",
                """import urllib.request

def scopes(obj, args, safe):
    send = obj.request
    send(*args)
    [send(*args) for send in [obj.request]]
    [send(*args) for send in safe]
    {send(*args) for send in [obj.request]}
    {send(*args) for send in safe}
    {send: send(*args) for send in [obj.request]}
    {send: send(*args) for send in safe}
    (send(*args) for send in [obj.request])
    (send(*args) for send in safe)
    send(*args)
    url = 'http://127.0.0.1/status?key=outer'
    urllib.request.urlopen(url)
    [urllib.request.urlopen(url) for url in safe]
    urllib.request.urlopen(url)
""",
            )
            violations = auditor.audit_runtime_http(root)

        self.assertEqual(
            {
                (
                    finding.relative_path,
                    finding.function,
                    finding.line,
                    finding.kind,
                )
                for finding in violations
            },
            {
                ("dayz_mcp/scopes.py", "scopes", 5, "http_request"),
                ("dayz_mcp/scopes.py", "scopes", 6, "http_request"),
                ("dayz_mcp/scopes.py", "scopes", 8, "http_request"),
                ("dayz_mcp/scopes.py", "scopes", 10, "http_request"),
                ("dayz_mcp/scopes.py", "scopes", 12, "http_request"),
                ("dayz_mcp/scopes.py", "scopes", 14, "http_request"),
                ("dayz_mcp/scopes.py", "scopes", 16, "authenticated_query"),
                ("dayz_mcp/scopes.py", "scopes", 16, "urlopen"),
                ("dayz_mcp/scopes.py", "scopes", 17, "urlopen"),
                ("dayz_mcp/scopes.py", "scopes", 18, "authenticated_query"),
                ("dayz_mcp/scopes.py", "scopes", 18, "urlopen"),
            },
        )

    def test_argument_shadowing_clears_alias_and_text_in_functions_and_lambda(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import shadows\n")
            self._write(
                root,
                "dayz_mcp/shadows.py",
                """import urllib.request as request
send = request.urlopen
target = 'http://127.0.0.1/status?key=outer'

def shadow(send, target):
    send('one')
    request.urlopen(target)

shadow_lambda = lambda send, target: (
    send('one'),
    request.urlopen(target),
)
""",
            )
            violations = auditor.audit_runtime_http(root)

        self.assertEqual(
            {
                (
                    finding.relative_path,
                    finding.function,
                    finding.line,
                    finding.kind,
                )
                for finding in violations
            },
            {
                ("dayz_mcp/shadows.py", "shadow", 7, "urlopen"),
                ("dayz_mcp/shadows.py", "<lambda>", 11, "urlopen"),
            },
        )

    def test_text_starred_namedexpr_and_augassign_preserve_query_taint(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import text_carriers\n")
            self._write(
                root,
                "dayz_mcp/text_carriers.py",
                """import urllib.parse
import urllib.request

def starred_text():
    target = 'http://127.0.0.1/status?key=value'
    arguments = (target,)
    urllib.request.urlopen(*arguments)

def namedexpr_text():
    urllib.request.urlopen((target := 'http://127.0.0.1/status?key=value'))

def augassign_name(key):
    target = 'http://127.0.0.1/status?ke'
    target += 'y=' + key
    urllib.request.urlopen(target)

def augassign_mapping(key):
    params = {'key': ''}
    params['key'] += key
    query = urllib.parse.urlencode(params)
    urllib.request.urlopen('http://127.0.0.1/status?' + query)

def percent_encoded(key):
    target = 'http://127.0.0.1/status?%6bey=' + key
    urllib.request.urlopen(target)
""",
            )
            violations = auditor.audit_runtime_http(root)

        self.assertEqual(
            {
                (
                    finding.relative_path,
                    finding.function,
                    finding.line,
                    finding.kind,
                )
                for finding in violations
            },
            {
                ("dayz_mcp/text_carriers.py", "starred_text", 7, "authenticated_query"),
                ("dayz_mcp/text_carriers.py", "starred_text", 7, "urlopen"),
                ("dayz_mcp/text_carriers.py", "namedexpr_text", 10, "authenticated_query"),
                ("dayz_mcp/text_carriers.py", "namedexpr_text", 10, "urlopen"),
                ("dayz_mcp/text_carriers.py", "augassign_name", 15, "authenticated_query"),
                ("dayz_mcp/text_carriers.py", "augassign_name", 15, "urlopen"),
                ("dayz_mcp/text_carriers.py", "augassign_mapping", 21, "authenticated_query"),
                ("dayz_mcp/text_carriers.py", "augassign_mapping", 21, "urlopen"),
                ("dayz_mcp/text_carriers.py", "percent_encoded", 25, "authenticated_query"),
                ("dayz_mcp/text_carriers.py", "percent_encoded", 25, "urlopen"),
            },
        )

    def test_canonical_real_signature_detects_augassign_and_namedexpr(self) -> None:
        auditor = self._auditor()
        signature = """def verified_daemon_http_request(
    *,
    host: str,
    port: int,
    key: str,
    method: str,
    path: str,
    query: dict[str, str] | None,
    body: bytes | None,
    headers: dict[str, str] | None,
    deadline: float,
    expected_executable: str,
    expected_argv: list[str],
    expected_cwd: str,
    connection_factory: Callable[[str, int, float], object] | None = None,
    connections_fn: Callable[[], object] | None = None,
    get_executable: Callable[[int], str | None] | None = None,
    get_argv: Callable[[int], list[str] | None] | None = None,
    get_cwd: Callable[[int], str | None] | None = None,
    guard: object | None = None,
    time_fn: Callable[[], float] | None = None,
    max_response_bytes: int = MAX_AUTHENTICATED_RESPONSE_BYTES,
) -> tuple[int, bytes]:
"""
        bodies = {
            "augassign": """    first = '/status?key=' + key
    connection.request(method, first)
    second = '/status?ke'
    second += 'y=' + key
    connection.request(method, second)
""",
            "namedexpr": """    first = '/status?key=' + key
    connection.request(method, first)
    connection.request(method, (second := '/status?key=' + key))
""",
        }
        for name, body in bodies.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(
                    root,
                    "dayz_mcp/server.py",
                    "from dayz_mcp import accredited_daemon_transport\n",
                )
                source = signature + body
                self._write(
                    root,
                    "dayz_mcp/accredited_daemon_transport.py",
                    source,
                )
                violations = auditor.audit_runtime_http(root)
            second_line = max(
                line
                for line, text in enumerate(source.splitlines(), start=1)
                if "connection.request(" in text
            )
            self.assertEqual(
                {
                    (
                        finding.relative_path,
                        finding.function,
                        finding.line,
                        finding.kind,
                    )
                    for finding in violations
                },
                {
                    (
                        "dayz_mcp/accredited_daemon_transport.py",
                        "verified_daemon_http_request",
                        second_line,
                        "authenticated_query",
                    ),
                    (
                        "dayz_mcp/accredited_daemon_transport.py",
                        "verified_daemon_http_request",
                        second_line,
                        "http_request",
                    ),
                },
                name,
            )

    def test_probe_requires_sync_exact_defaults_and_rejects_only_carrier_delta(self) -> None:
        auditor = self._auditor()
        nominal = """def probe_listener_responsive(
    port: int,
    *,
    timeout: float = 1.0,
    host: str = '127.0.0.1',
) -> bool:
    url = f'http://{host}:{int(port)}/status'
    urllib.request.urlopen(url, timeout=timeout)
"""
        cases = {
            "nominal": (nominal, False),
            "async": (nominal.replace("def probe", "async def probe", 1), True),
            "remote_host": (nominal.replace("'127.0.0.1'", "'0.0.0.0'", 1), True),
            "integer_timeout": (nominal.replace("1.0", "1", 1), True),
            "positional_default": (nominal.replace("port: int,", "port: int = 8765,", 1), True),
            "augassign_only": (
                nominal.replace(
                    "    urllib.request.urlopen(url, timeout=timeout)",
                    "    url += ''\n    urllib.request.urlopen(url, timeout=timeout)",
                ),
                True,
            ),
            "namedexpr_only": (
                nominal.replace(
                    "    urllib.request.urlopen(url, timeout=timeout)",
                    "    (shadow := None)\n    urllib.request.urlopen(url, timeout=timeout)",
                ),
                True,
            ),
        }
        for name, (source, expected_sensitive) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._write(root, "dayz_mcp/server.py", "from dayz_mcp import orphan_guard\n")
                self._write(
                    root,
                    "dayz_mcp/orphan_guard.py",
                    "import urllib.request\n" + source,
                )
                violations = auditor.audit_runtime_http(root)
            self.assertEqual(
                {
                    (
                        finding.relative_path,
                        finding.function,
                        finding.line,
                        finding.kind,
                    )
                    for finding in violations
                },
                {
                    (
                        "dayz_mcp/orphan_guard.py",
                        "probe_listener_responsive",
                        2,
                        "sensitive_probe",
                    )
                }
                if expected_sensitive
                else set(),
                name,
            )

    def test_if_branches_merge_alias_and_text_from_a_common_snapshot(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import branches\n")
            self._write(
                root,
                "dayz_mcp/branches.py",
                """import urllib.request

def alias_body(obj, args, flag):
    if flag:
        send = obj.request
    else:
        send = safe
    send(*args)

def alias_no_else(obj, args, flag):
    send = obj.request
    if flag:
        send = safe
    send(*args)

def text_body(flag):
    target = 'http://127.0.0.1/status'
    if flag:
        target = 'http://127.0.0.1/status?key=value'
    else:
        target = 'http://127.0.0.1/status'
    urllib.request.urlopen(target)

def text_no_else(flag):
    target = 'http://127.0.0.1/status?key=value'
    if flag:
        target = 'http://127.0.0.1/status'
    urllib.request.urlopen(target)
""",
            )
            violations = auditor.audit_runtime_http(root)

        self.assertEqual(
            {
                (
                    finding.relative_path,
                    finding.function,
                    finding.line,
                    finding.kind,
                )
                for finding in violations
            },
            {
                ("dayz_mcp/branches.py", "alias_body", 8, "http_request"),
                ("dayz_mcp/branches.py", "alias_no_else", 14, "http_request"),
                ("dayz_mcp/branches.py", "text_body", 22, "authenticated_query"),
                ("dayz_mcp/branches.py", "text_body", 22, "urlopen"),
                ("dayz_mcp/branches.py", "text_no_else", 28, "authenticated_query"),
                ("dayz_mcp/branches.py", "text_no_else", 28, "urlopen"),
            },
        )

    def test_comprehension_walrus_exports_marker_and_text_to_parent_scope(self) -> None:
        auditor = self._auditor()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "dayz_mcp/server.py", "from dayz_mcp import walrus_scopes\n")
            self._write(
                root,
                "dayz_mcp/walrus_scopes.py",
                """import urllib.request

def list_export(obj, args):
    [(send := obj.request) for _ in [0]]
    send(*args)

def set_export(obj, args):
    {(send := obj.request) for _ in [0]}
    send(*args)

def dict_export(obj, args):
    {_: (send := obj.request) for _ in [0]}
    send(*args)

def generator_export(obj, args):
    carrier = ((send := obj.request) for _ in [0])
    next(carrier)
    send(*args)

def text_export():
    [(target := 'http://127.0.0.1/status?key=value') for _ in [0]]
    urllib.request.urlopen(target)
""",
            )
            violations = auditor.audit_runtime_http(root)

        self.assertEqual(
            {
                (
                    finding.relative_path,
                    finding.function,
                    finding.line,
                    finding.kind,
                )
                for finding in violations
            },
            {
                ("dayz_mcp/walrus_scopes.py", "list_export", 5, "http_request"),
                ("dayz_mcp/walrus_scopes.py", "set_export", 9, "http_request"),
                ("dayz_mcp/walrus_scopes.py", "dict_export", 13, "http_request"),
                ("dayz_mcp/walrus_scopes.py", "generator_export", 18, "http_request"),
                ("dayz_mcp/walrus_scopes.py", "text_export", 22, "authenticated_query"),
                ("dayz_mcp/walrus_scopes.py", "text_export", 22, "urlopen"),
            },
        )


if __name__ == "__main__":
    unittest.main()
