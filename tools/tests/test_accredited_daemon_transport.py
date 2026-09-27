from __future__ import annotations

import ast
import importlib
import importlib.util
import ntpath
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp.native_process_guard import identity_hashes


class _FakeSocket:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", 51000)

    def getpeername(self) -> tuple[str, int]:
        return ("127.0.0.1", 8765)

    def settimeout(self, timeout: float) -> None:
        self._events.append(("settimeout", timeout))


class _FakeResponse:
    status = 200

    def read(self, size: int) -> bytes:
        return b'{"status":"ok"}'


class _FakeConnection:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.sock = _FakeSocket(events)

    def connect(self) -> None:
        self.events.append("connect")

    def request(
        self,
        method: str,
        target: str,
        *,
        body: bytes | None,
        headers: dict[str, str],
    ) -> None:
        self.events.append(("request", method, target, body, headers))

    def getresponse(self) -> _FakeResponse:
        self.events.append("getresponse")
        return _FakeResponse()

    def close(self) -> None:
        self.events.append("close")


class AccreditedDaemonTransportTests(unittest.TestCase):
    def test_request_accredits_twice_before_disclosing_key_or_body(self) -> None:
        spec = importlib.util.find_spec("dayz_mcp.accredited_daemon_transport")
        self.assertIsNotNone(
            spec, "dayz_mcp.accredited_daemon_transport is not implemented"
        )
        transport = importlib.import_module("dayz_mcp.accredited_daemon_transport")
        orphan_guard = importlib.import_module("dayz_mcp.orphan_guard")
        self.assertIs(
            orphan_guard.verified_daemon_http_request,
            transport.verified_daemon_http_request,
        )

        events: list[object] = []
        connection = _FakeConnection(events)
        executable = r"P:\Runtime\python.exe"
        argv = [
            executable,
            "-m",
            "dayz_mcp",
            "--daemon",
            "--port",
            "8765",
        ]
        hashes = identity_hashes(executable, argv)

        class Guard:
            def snapshot(self, pid: int) -> dict[str, object]:
                events.append(("snapshot", pid))
                return {
                    "pid": pid,
                    "creation_time_utc": "2026-07-22T00:00:00Z",
                    "executable_sha256": hashes["executable_sha256"],
                    "command_line_sha256": hashes["command_line_sha256"],
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                }

        established = SimpleNamespace(
            status=transport.psutil.CONN_ESTABLISHED,
            laddr=("127.0.0.1", 8765),
            raddr=("127.0.0.1", 51000),
            pid=42,
        )
        status, body = transport.verified_daemon_http_request(
            host="127.0.0.1",
            port=8765,
            key="top-secret",
            method="POST",
            path="/session/status",
            query={"view": "self"},
            body=b'{"identity":{}}',
            headers={"Content-Type": "application/json"},
            deadline=20.0,
            expected_executable=executable,
            expected_argv=argv,
            expected_cwd=r"P:\DayZ_MCP_dev\tools",
            connection_factory=lambda _host, _port, _timeout: connection,
            connections_fn=lambda: [established],
            get_executable=lambda _pid: executable,
            get_argv=lambda _pid: list(argv),
            get_cwd=lambda _pid: r"P:\DayZ_MCP_dev\tools",
            guard=Guard(),
            time_fn=lambda: 10.0,
        )

        self.assertEqual((status, body), (200, b'{"status":"ok"}'))
        request_index = next(
            index
            for index, event in enumerate(events)
            if isinstance(event, tuple) and event[0] == "request"
        )
        snapshot_indices = [
            index
            for index, event in enumerate(events)
            if isinstance(event, tuple) and event[0] == "snapshot"
        ]
        self.assertEqual(len(snapshot_indices), 2)
        self.assertLess(max(snapshot_indices), request_index)
        request_event = events[request_index]
        self.assertIn("key=top-secret", request_event[2])

    def test_foreign_owner_sends_zero_http_bytes(self) -> None:
        transport = importlib.import_module("dayz_mcp.accredited_daemon_transport")
        events: list[object] = []
        connection = _FakeConnection(events)
        with self.assertRaisesRegex(
            transport.AccreditedTransportError, "daemon_identity_unverified"
        ) as raised:
            transport.verified_daemon_http_request(
                host="127.0.0.1",
                port=8765,
                key="top-secret",
                method="POST",
                path="/session/status",
                query=None,
                body=b"{}",
                headers=None,
                deadline=20.0,
                expected_executable=r"P:\Runtime\python.exe",
                expected_argv=[
                    r"P:\Runtime\python.exe",
                    "-m",
                    "dayz_mcp",
                    "--daemon",
                    "--port",
                    "8765",
                ],
                expected_cwd=r"P:\DayZ_MCP_dev\tools",
                connection_factory=lambda _host, _port, _timeout: connection,
                connections_fn=lambda: [],
                time_fn=lambda: 10.0,
            )
        self.assertEqual(raised.exception.request_stage, "pre_request")
        self.assertEqual(raised.exception.http_bytes_sent, 0)
        self.assertNotIn("request", [event[0] if isinstance(event, tuple) else event for event in events])

    def test_relative_expected_cwd_is_rejected_before_http_bytes(self) -> None:
        transport = importlib.import_module("dayz_mcp.accredited_daemon_transport")
        events: list[object] = []
        connection = _FakeConnection(events)
        executable = r"P:\Runtime\python.exe"
        argv = [
            executable,
            "-m",
            "dayz_mcp",
            "--daemon",
            "--port",
            "8765",
        ]
        hashes = identity_hashes(executable, argv)

        class Guard:
            def snapshot(self, pid: int) -> dict[str, object]:
                return {
                    "pid": pid,
                    "creation_time_utc": "2026-07-22T00:00:00Z",
                    "executable_sha256": hashes["executable_sha256"],
                    "command_line_sha256": hashes["command_line_sha256"],
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                }

        established = SimpleNamespace(
            status=transport.psutil.CONN_ESTABLISHED,
            laddr=("127.0.0.1", 8765),
            raddr=("127.0.0.1", 51000),
            pid=42,
        )
        with self.assertRaisesRegex(ConnectionError, "daemon_identity_unverified"):
            transport.verified_daemon_http_request(
                host="127.0.0.1",
                port=8765,
                key="top-secret",
                method="POST",
                path="/session/status",
                query=None,
                body=b"{}",
                headers=None,
                deadline=20.0,
                expected_executable=executable,
                expected_argv=argv,
                expected_cwd="tools",
                connection_factory=lambda _host, _port, _timeout: connection,
                connections_fn=lambda: [established],
                get_executable=lambda _pid: executable,
                get_argv=lambda _pid: list(argv),
                get_cwd=lambda _pid: "tools",
                guard=Guard(),
                time_fn=lambda: 10.0,
            )
        self.assertFalse(
            any(isinstance(event, tuple) and event[0] == "request" for event in events)
        )

    def test_response_failure_is_marked_post_request_and_never_looks_retryable(
        self,
    ) -> None:
        transport = importlib.import_module("dayz_mcp.accredited_daemon_transport")
        events: list[object] = []

        class PostRequestFailure(_FakeConnection):
            def getresponse(self) -> _FakeResponse:
                self.events.append("getresponse")
                raise ConnectionError("response_lost")

        connection = PostRequestFailure(events)
        executable = r"P:\Runtime\python.exe"
        argv = [
            executable,
            "-m",
            "dayz_mcp",
            "--daemon",
            "--port",
            "8765",
        ]
        hashes = identity_hashes(executable, argv)

        class Guard:
            def snapshot(self, pid: int) -> dict[str, object]:
                return {
                    "pid": pid,
                    "creation_time_utc": "2026-07-22T00:00:00Z",
                    "executable_sha256": hashes["executable_sha256"],
                    "command_line_sha256": hashes["command_line_sha256"],
                    "identity_scheme": "psutil-argv-v2",
                    "identity_complete": True,
                }

        established = SimpleNamespace(
            status=transport.psutil.CONN_ESTABLISHED,
            laddr=("127.0.0.1", 8765),
            raddr=("127.0.0.1", 51000),
            pid=42,
        )
        with self.assertRaisesRegex(
            transport.AccreditedTransportError, "daemon_transport_failure"
        ) as raised:
            transport.verified_daemon_http_request(
                host="127.0.0.1",
                port=8765,
                key="top-secret",
                method="POST",
                path="/session/status",
                query=None,
                body=b"{}",
                headers=None,
                deadline=20.0,
                expected_executable=executable,
                expected_argv=argv,
                expected_cwd=r"P:\DayZ_MCP_dev\tools",
                connection_factory=lambda _host, _port, _timeout: connection,
                connections_fn=lambda: [established],
                get_executable=lambda _pid: executable,
                get_argv=lambda _pid: list(argv),
                get_cwd=lambda _pid: r"P:\DayZ_MCP_dev\tools",
                guard=Guard(),
                time_fn=lambda: 10.0,
            )
        self.assertEqual(raised.exception.request_stage, "post_request")
        self.assertGreater(raised.exception.http_bytes_sent, 0)

    def test_argv0_redirected_to_the_verified_image_is_accredited_and_nothing_wider(
        self,
    ) -> None:
        transport = importlib.import_module("dayz_mcp.accredited_daemon_transport")
        # A Python 3.11/3.12 venv (#93): the listener's argv[0] is the base
        # interpreter's own path, while the expected argv carries the venv path.
        image = r"P:\Python312\python.exe"
        cwd = r"P:\DayZ_MCP_dev\tools"
        tail = ["-m", "dayz_mcp", "--daemon", "--port", "8765"]
        expected_argv = [r"P:\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe", *tail]
        established = SimpleNamespace(
            status=transport.psutil.CONN_ESTABLISHED,
            laddr=("127.0.0.1", 8765),
            raddr=("127.0.0.1", 51000),
            pid=42,
        )

        def accredited(observed: list[str], second: list[str] | None = None) -> bool:
            hashes = identity_hashes(image, observed)
            reads = [list(observed), list(second or observed)]

            class Guard:
                def snapshot(self, pid: int) -> dict[str, object]:
                    return {
                        "pid": pid,
                        "creation_time_utc": "2026-07-22T00:00:00Z",
                        "executable_sha256": hashes["executable_sha256"],
                        "command_line_sha256": hashes["command_line_sha256"],
                        "identity_scheme": "psutil-argv-v2",
                        "identity_complete": True,
                    }

            return transport._connected_daemon_identity_verified(
                _FakeSocket([]),
                8765,
                expected_executable=image,
                expected_argv=expected_argv,
                expected_cwd=cwd,
                connections_fn=lambda: [established],
                get_executable=lambda _pid: image,
                get_argv=lambda _pid: reads.pop(0),
                get_cwd=lambda _pid: cwd,
                guard=Guard(),
            )

        self.assertTrue(accredited([image, *tail]))
        self.assertTrue(accredited(list(expected_argv)))  # 3.14 keeps the venv path
        refused = {
            "argv0_names_another_interpreter": [r"P:\Other\python.exe", *tail],
            "argv0_relative": ["python", *tail],
            "other_port": [image, "-m", "dayz_mcp", "--daemon", "--port", "8766"],
            "extra_argument": [image, *tail, "--x"],
        }
        for case, observed in refused.items():
            with self.subTest(case):
                self.assertFalse(accredited(observed))
        with self.subTest("argv_changes_between_the_two_reads"):
            self.assertFalse(accredited([image, *tail], second=list(expected_argv)))

    def test_redirected_argv0_needs_a_drive_even_where_samefile_resolves_it(
        self,
    ) -> None:
        # Python 3.11's ntpath.isabs also calls a drive-less "\path" absolute (a
        # legacy behaviour dropped in 3.13), and samefile resolves it on the
        # current drive (#112 review, F1). The legacy isabs is simulated so the
        # guard holds whatever interpreter runs the test.
        transport = importlib.import_module("dayz_mcp.accredited_daemon_transport")
        expected = [r"P:\venv\Scripts\python.exe", "-m", "dayz_mcp"]
        modern_isabs = ntpath.isabs

        def legacy_isabs(value: str) -> bool:
            return value[:1] in ("\\", "/") or modern_isabs(value)

        with tempfile.TemporaryDirectory() as root:
            image = str(Path(root).resolve() / "python.exe")
            Path(image).write_bytes(b"")
            drive, rooted = ntpath.splitdrive(image)
            previous = os.getcwd()
            os.chdir(root)
            try:
                spellings = {
                    "rooted_backslash": rooted,
                    "rooted_slash": rooted.replace("\\", "/"),
                    "drive_relative": drive + "python.exe",
                    "relative": "python.exe",
                }
                for case, first in spellings.items():
                    with self.subTest(case):
                        self.assertTrue(os.path.samefile(first, image))
                        with patch.object(transport.ntpath, "isabs", legacy_isabs):
                            self.assertFalse(
                                transport.argv_matches_redirected(
                                    [first, "-m", "dayz_mcp"], expected, image
                                )
                            )
                self.assertTrue(
                    transport.argv_matches_redirected(
                        [image, "-m", "dayz_mcp"], expected, image
                    )
                )
            finally:
                os.chdir(previous)

    def test_transport_import_graph_has_no_mutation_or_orphan_guard_dependency(
        self,
    ) -> None:
        spec = importlib.util.find_spec("dayz_mcp.accredited_daemon_transport")
        self.assertIsNotNone(spec)
        source_path = Path(spec.origin or "")
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imported_modules = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        source = source_path.read_text(encoding="utf-8").casefold()
        self.assertNotIn("dayz_mcp.orphan_guard", imported_modules)
        for forbidden in (
            "createprocess",
            "os.spawn",
            "subprocess",
            "terminateprocess",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
