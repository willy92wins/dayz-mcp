"""296b r3 (Sol r2 B1): two MCP processes, one client profile, one daemon.

Each MCP process is a separate Python interpreter (subprocess) talking to the
daemon's real loopback handler over HTTP. The daemon side is a real
ServerState with its ClientDumpRegistry; only the process lifecycle is a fake
that mints run_ids and publishes the retired rows. The reaps and the dumps
are synthetic: no DayZ runs here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dayz_mcp import loopback
from dayz_mcp.session_coordination import SessionCoordinator

_TOOLS_DIR = Path(__file__).resolve().parents[1]
_WORKER = Path(__file__).resolve().parent / "_client_dumps_mcp_process.py"
_MARKED = "MDMP\x00SteamInternal_SetMinidumpSteamID:  Caching Steam ID:  1 [API loaded no]\x00"
_GEN = "a" * 32


def _retired_row(run_id: str) -> dict[str, object]:
    return {
        "run_id": run_id,
        "daemon_generation_at_launch": _GEN,
        "daemon_generation_current": _GEN,
        "generation_changed": False,
        "event": "run_reaped",
        "reason": "all_processes_gone_or_foreign",
        "decision": "reaped",
        "state": "EXITED",
    }


class _Lifecycle:
    """Mints run_ids on /lifecycle/start; the test decides what is retired."""

    def __init__(self) -> None:
        self._count = 0
        self.retired: list[dict[str, object]] = []

    def start_run(self, client, token, value):
        self._count += 1
        run_id = f"12345678-1234-4234-8234-{self._count:012d}"
        return {"ok": True, "run_id": run_id, "state": "RUNNING"}

    def retired_run_diagnostics(self):
        return list(self.retired)


class _McpProcess:
    def __init__(self, base: str, session: str) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_TOOLS_DIR)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, "-B", str(_WORKER), base, "key", session],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            cwd=str(_TOOLS_DIR),
            env=env,
        )

    def ask(self, **command: object) -> object:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps(command) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError(f"MCP process died: exit={self.proc.wait(5)}")
        return json.loads(line)["answer"]

    def close(self) -> None:
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        if self.proc.stdout is not None:
            self.proc.stdout.close()


class CrossProcessClientDumpTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.client_root = str(Path(tmp.name) / "_client" / "profiles")
        Path(self.client_root).mkdir(parents=True)
        self._serve(loopback.ServerState("key"))

    def _serve(self, state: loopback.ServerState) -> None:
        if getattr(self, "httpd", None) is not None:
            self._stop()
        state.coordination = SessionCoordinator(
            token_fn=lambda: "token", id_fn=lambda: "lease", audit=lambda _e: True
        )
        self.lifecycle = getattr(self, "lifecycle", None) or _Lifecycle()
        state.lifecycle = self.lifecycle
        self.state = state
        self.httpd = loopback.create_http_server(
            0, state, log_sink=lambda _m: None, reclaim_orphans=False
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        if self.httpd is None:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2.0)
        self.httpd = None

    def _mcp(self, session: str) -> _McpProcess:
        process = _McpProcess(self.base, session)
        self.addCleanup(process.close)
        return process

    def test_sol_r2_b1_other_process_dump_does_not_name_the_earlier_run(self) -> None:
        a, b = self._mcp("A"), self._mcp("B")
        self.assertNotEqual(a.ask(op="pid"), b.ask(op="pid"))
        # A: run 1 is bound, dies without a dump and is retired.
        run1 = a.ask(op="launch", root=self.client_root)
        self.lifecycle.retired = [_retired_row(run1)]
        # B: run 2 on the same client profile writes a marked dump.
        run2 = b.ask(op="launch", root=self.client_root)
        b.ask(op="dump", root=self.client_root, stamp="03-05-00", body=_MARKED)
        self.assertIsNone(a.ask(op="project")[run1])
        # Once run 2 is retired too, its row names the death; run 1 stays null.
        self.lifecycle.retired = [_retired_row(run2), _retired_row(run1)]
        for reader in (a, b):
            projected = reader.ask(op="project")
            self.assertIsNone(projected[run1])
            self.assertEqual(projected[run2], "steam_bootstrap")

    def test_positive_bound_in_a_named_in_b(self) -> None:
        a, b = self._mcp("A"), self._mcp("B")
        run1 = a.ask(op="launch", root=self.client_root)
        a.ask(op="dump", root=self.client_root, stamp="03-05-00", body=_MARKED)
        self.lifecycle.retired = [_retired_row(run1)]
        self.assertEqual(b.ask(op="project")[run1], "steam_bootstrap")

    def test_a_dump_before_the_other_process_launches_stays_with_the_first_run(
        self,
    ) -> None:
        # B's pre-launch snapshot caps run 1: what A's client wrote before it
        # still names run 1, whatever B does afterwards.
        a, b = self._mcp("A"), self._mcp("B")
        run1 = a.ask(op="launch", root=self.client_root)
        a.ask(op="dump", root=self.client_root, stamp="03-05-00", body=_MARKED)
        run2 = b.ask(op="launch", root=self.client_root)
        b.ask(op="dump", root=self.client_root, stamp="03-06-00", body="MDMP\x00x\x00")
        self.lifecycle.retired = [_retired_row(run2), _retired_row(run1)]
        projected = a.ask(op="project")
        self.assertEqual(projected[run1], "steam_bootstrap")
        self.assertIsNone(projected[run2])

    def test_no_daemon_record_is_null(self) -> None:
        b = self._mcp("B")
        unknown = "12345678-1234-4234-8234-999999999999"
        b.ask(op="dump", root=self.client_root, stamp="03-05-00", body=_MARKED)
        self.lifecycle.retired = [_retired_row(unknown)]
        self.assertIsNone(b.ask(op="project")[unknown])

    def test_restarted_daemon_knows_no_run_and_publishes_null(self) -> None:
        a = self._mcp("A")
        run1 = a.ask(op="launch", root=self.client_root)
        a.ask(op="dump", root=self.client_root, stamp="03-05-00", body=_MARKED)
        self.lifecycle.retired = [_retired_row(run1)]
        self.assertEqual(a.ask(op="project")[run1], "steam_bootstrap")
        # Same port is not needed: a new MCP process reads the new daemon.
        self._serve(loopback.ServerState("key"))
        b = self._mcp("B")
        self.assertIsNone(b.ask(op="project")[run1])

    def test_a_launch_that_recorded_no_snapshot_closes_the_earlier_run(self) -> None:
        # An older MCP (or a failed open) launches without a snapshot: the
        # daemon cannot tell which dumps are its own, so run 1 is null.
        a, b = self._mcp("A"), self._mcp("B")
        run1 = a.ask(op="launch", root=self.client_root)
        b.ask(op="launch", root=self.client_root, open=False)
        b.ask(op="dump", root=self.client_root, stamp="03-05-00", body=_MARKED)
        self.lifecycle.retired = [_retired_row(run1)]
        self.assertIsNone(a.ask(op="project")[run1])

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        url = self.base + path + "?" + urllib.parse.urlencode({"key": "key"})
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5.0) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()

    def test_route_is_bounded_and_session_status_is_unchanged(self) -> None:
        identity = {
            "platform": "claude",
            "pid": 1,
            "ppid": 1,
            "started_at_utc": "2026-09-25T00:00:00Z",
            "session_id": "old",
        }
        a = self._mcp("A")
        run1 = a.ask(op="launch", root=self.client_root)
        self.lifecycle.retired = [_retired_row(run1)]
        status, payload = self._post("/session/status", {"identity": identity})
        self.assertEqual(status, 200)
        self.assertNotIn("client_dump_bindings", payload)
        self.assertEqual(
            self._post("/client-dumps", {"identity": {}, "op": "get", "run_ids": []}),
            (400, {"error": "invalid_identity"}),
        )
        self.assertEqual(
            self._post("/client-dumps", {"identity": identity, "op": "drop"}),
            (400, {"error": "invalid_op"}),
        )
        too_many = [run1] * (loopback.MAX_CLIENT_DUMP_RUN_IDS + 1)
        self.assertEqual(
            self._post(
                "/client-dumps", {"identity": identity, "op": "get", "run_ids": too_many}
            ),
            (400, {"error": "invalid_run_ids"}),
        )
        self.assertEqual(
            self._post(
                "/client-dumps",
                {"identity": identity, "op": "open", "baseline": {"roots": []}},
            ),
            (400, {"error": "invalid_baseline"}),
        )
        status, payload = self._post(
            "/client-dumps", {"identity": identity, "op": "get", "run_ids": [run1]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(set(payload["bindings"]), {run1})


class ControlClientPayloadTests(unittest.IsolatedAsyncioTestCase):
    """The MCP side sends what the daemon handler above parses."""

    async def test_open_bind_and_get_payloads(self) -> None:
        from dayz_mcp.control_client import ControlClient

        calls: list[tuple[str, object]] = []

        async def session_call(path, payload=None, **_kwargs):
            calls.append((path, payload))
            return {}

        client = ControlClient.__new__(ControlClient)
        client._session_call = session_call  # type: ignore[method-assign]
        await client.client_dumps_open({"roots": ["r"], "before": [None]})
        await client.client_dumps_bind("tok", "run")
        await client.client_dumps_get(["run"])
        self.assertEqual(
            calls,
            [
                ("/client-dumps", {"op": "open", "baseline": {"roots": ["r"], "before": [None]}}),
                ("/client-dumps", {"op": "bind", "token": "tok", "run_id": "run"}),
                ("/client-dumps", {"op": "get", "run_ids": ["run"]}),
            ],
        )
        self.assertEqual(loopback.CLIENT_DUMPS_ROUTE, "/client-dumps")


class SessionStatusBindingsTests(unittest.IsolatedAsyncioTestCase):
    """server._client_dump_bindings: anything but a clean answer is None."""

    async def test_failures_are_none_and_nothing_is_asked_without_rows(self) -> None:
        from dayz_mcp import server

        asked: list[object] = []

        class _Client:
            def __init__(self, answer: object) -> None:
                self.answer = answer

            async def client_dumps_get(self, run_ids):
                asked.append(run_ids)
                if isinstance(self.answer, Exception):
                    raise self.answer
                return self.answer

        rows = {"retired_run_diagnostics": [_retired_row("r1"), {"run_id": 7}]}
        for answer in (RuntimeError("not_found"), {"bindings": "x"}, None, {}):
            self.assertIsNone(await server._client_dump_bindings(_Client(answer), rows))
        self.assertEqual(asked[0], ["r1"])
        self.assertEqual(
            await server._client_dump_bindings(_Client({"bindings": {}}), rows), {}
        )
        asked.clear()
        for status in ({}, {"retired_run_diagnostics": None}, {"retired_run_diagnostics": []}):
            self.assertIsNone(await server._client_dump_bindings(_Client({}), status))
        self.assertIsNone(await server._client_dump_bindings(object(), rows))
        self.assertEqual(asked, [])


if __name__ == "__main__":
    unittest.main()
