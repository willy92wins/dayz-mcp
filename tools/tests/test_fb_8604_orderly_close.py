from __future__ import annotations

import inspect
import json
import ntpath
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from tests.steam_helpers import FakeSteamGate

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import dayz_test_request, dayz_test_tool, loopback, window_close
from dayz_mcp.process_lifecycle import (
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
)
from dayz_mcp.runtime_state import RuntimePaths
from dayz_mcp.server import LEASE_TOOL_LINE, ServerConfig, build_app
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator
from dayz_mcp.window_close import WM_CLOSE, Win32WindowFns
from tests._tiers import slow_test


IDENTITY_A = ClientIdentity("codex", 11, 1, "2026-07-15T00:00:00Z", "A", "owner")
IDENTITY_B = ClientIdentity("codex", 12, 1, "2026-07-15T00:00:01Z", "B", "other")
HASH_A = "a" * 64
HASH_B = "b" * 64
RUN_ID = "run-existing"
TERMINATION = "--- Termination successfully completed ---\n"


class AuditSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.fail_events: set[str] = set()

    def __call__(self, event: dict[str, object]) -> bool:
        self.events.append(event)
        return event.get("event") not in self.fail_events


class FakeGuard:
    def __init__(self) -> None:
        self.snapshots: dict[int, dict[str, object]] = {}
        self.snapshot_calls: list[int] = []
        self.terminate_calls: list[ProcessRecord] = []
        self.flip_after: int | None = None

    def snapshot(self, pid: int) -> dict[str, object]:
        self.snapshot_calls.append(pid)
        value = dict(self.snapshots.get(pid, {"error": "identity_unavailable"}))
        if self.flip_after is not None and len(self.snapshot_calls) > self.flip_after:
            value["creation_time_utc"] = "9999-01-01T00:00:00.0000000Z"
            value["identity_complete"] = True
        return value

    def terminate(self, record: ProcessRecord) -> dict[str, object]:
        self.terminate_calls.append(record)
        return {"terminated": True}


class FakeLauncher:
    def __call__(self, argv: list[str], cwd: str, window_style: str):
        return self

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float) -> None:
        return None

    def poll(self):
        return 0


@dataclass
class FakeWin32:
    windows: list[tuple[int, int, bool]] = field(default_factory=list)
    posts: list[tuple[int, int, int, int]] = field(default_factory=list)

    def as_fns(self) -> Win32WindowFns:
        def enum_windows(callback):
            for hwnd, _pid, _visible in list(self.windows):
                if not callback(hwnd):
                    return False
            return True

        def window_pid(hwnd: int) -> int:
            for handle, pid, _visible in self.windows:
                if handle == hwnd:
                    return pid
            return 0

        def is_visible(hwnd: int) -> bool:
            for handle, _pid, visible in self.windows:
                if handle == hwnd:
                    return visible
            return False

        def post_message(hwnd: int, msg: int, wparam: int, lparam: int) -> bool:
            self.posts.append((hwnd, msg, wparam, lparam))
            return True

        return Win32WindowFns(
            enum_windows=enum_windows,
            window_pid=window_pid,
            is_visible=is_visible,
            post_message=post_message,
        )


def process(pid: int, role: str = "client") -> ProcessRecord:
    return ProcessRecord(
        pid,
        f"2026-07-15T00:00:{pid % 60:02d}.0000000Z",
        HASH_A,
        HASH_B,
        role,
        identity_scheme="psutil-argv-v2",
    )


def snapshot(record: ProcessRecord) -> dict[str, object]:
    return {
        "pid": record.pid,
        "creation_time_utc": record.creation_time_utc,
        "executable_sha256": record.executable_sha256,
        "command_line_sha256": record.command_line_sha256,
        "identity_scheme": record.identity_scheme,
        "identity_complete": True,
    }


def _g2_call(test: unittest.TestCase, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except AttributeError as exc:
        test.fail(str(exc))
        raise


class CloseRun8604Test(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "DayZ"
        self.game.mkdir()
        for name in (
            "DayZDiag_x64.exe",
            "DayZ_BE.exe",
            "DayZ_x64.exe",
            "DayZServer_x64.exe",
        ):
            (self.game / name).write_bytes(b"")
        paths = RuntimePaths(
            self.root / "runtime",
            self.root / "runtime" / "audit",
            self.root / "runtime" / "coordination.json",
            self.root / "runtime" / "runs.json",
        )
        self.audit = AuditSink()
        self.coordinator = SessionCoordinator(
            token_fn=lambda: "token-A",
            id_fn=lambda: "lease-A",
            audit=self.audit,
        )
        status, acquired = self.coordinator.acquire(IDENTITY_A, "lifecycle")
        self.assertEqual(status, 200)
        self.token_a = acquired["lease_token"]
        self.store = RunManifestStore(paths)
        self.guard = FakeGuard()
        self.win32 = FakeWin32()
        self.lifecycle = ProcessLifecycle(
            steam_gate=FakeSteamGate(),
            coordinator=self.coordinator,
            manifest=self.store,
            audit=self.audit,
            guard=self.guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=self.game,
            launcher=FakeLauncher(),
            id_fn=lambda: "run-1",
        )
        self.lifecycle.window_fns = self.win32.as_fns()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_run(
        self,
        *records: ProcessRecord,
        owner: str | None = "A",
        state: str = "RUNNING",
    ) -> RunRecord:
        run = RunRecord(
            RUN_ID,
            owner,
            "lease-A" if owner else None,
            state,
            "same",
            "@SameMod",
            "profiles",
            "mission",
            list(records),
        )
        self.store.add(run)
        return run

    def close(self, token: str | None, run_id: object = RUN_ID):
        close_run = getattr(self.lifecycle, "close_run", None)
        if close_run is None:
            self.fail("ProcessLifecycle.close_run is missing")
        return _g2_call(self, close_run, IDENTITY_A, token, run_id)

    def manifest_bytes(self) -> bytes:
        return self.store.paths.runs_path.read_bytes()

    def _pending_close(self) -> bool:
        active = getattr(self.coordinator, "_active", None)
        if active is None:
            return False
        pending = getattr(active, "pending_authorizations", ()) or ()
        return any(getattr(item, "command", None) == "lifecycle_close" for item in pending)

    def _assert_close_not_in_flight(self) -> None:
        self.assertFalse(self._pending_close())
        active = getattr(self.coordinator, "_active", None)
        if active is None:
            return
        committed = getattr(active, "committed_commands", {})
        pins = getattr(active, "operation_pins", {})
        self.assertEqual(committed, {})
        self.assertEqual(pins, {})

    def test_8604_r3_release_before_commit_posts_nothing(self) -> None:
        record = process(91)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        self.win32.windows = [(500, record.pid, True)]

        def releasing(event: dict[str, object]) -> bool:
            if event.get("event") == "lifecycle_close":
                self.coordinator.release(IDENTITY_A, self.token_a)
                self.lifecycle.release_owner("A", "lease-A")
            return event.get("event") not in self.audit.fail_events

        self.lifecycle.audit = releasing
        try:
            result = self.close(self.token_a)
        except Exception as exc:
            self.fail(str(exc))
        self.assertEqual(result.get("error"), "lease_invalid")
        self.assertEqual(self.win32.posts, [])
        self.assertFalse(self._pending_close())

        self.coordinator._token_fn = lambda: "token-B"
        self.coordinator._id_fn = lambda: "lease-B"
        status, acquired = self.coordinator.acquire(IDENTITY_B, "lifecycle")
        self.assertEqual(status, 200)
        try:
            adopted = self.lifecycle.adopt_run(
                IDENTITY_B, acquired["lease_token"], RUN_ID
            )
        except Exception as exc:
            self.fail(str(exc))
        self.assertTrue(adopted.get("ok"))

    def test_8604_r3_exception_after_authorize_leaves_no_reservation(self) -> None:
        record = process(81)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        self.win32.windows = [(400, record.pid, True)]

        def boom(*_args, **_kwargs):
            raise RuntimeError("close boom")

        with patch.object(
            self.lifecycle, "_classify_registered_process", side_effect=boom
        ):
            try:
                result = self.close(self.token_a)
            except Exception as exc:
                self.fail(str(exc))
        self.assertEqual(result.get("error"), "close_failed")
        self.assertEqual(result.get("_http_status"), 503)
        self._assert_close_not_in_flight()

        with patch.object(
            window_close, "list_visible_top_level_windows", side_effect=boom
        ):
            try:
                result = self.close(self.token_a)
            except Exception as exc:
                self.fail(str(exc))
        self.assertEqual(result.get("error"), "close_failed")
        self.assertEqual(result.get("_http_status"), 503)
        self._assert_close_not_in_flight()
        self.assertEqual(self.win32.posts, [])

    def test_8604_r3_audit_failure_posts_nothing(self) -> None:
        record = process(71)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        self.win32.windows = [(300, record.pid, True)]
        self.audit.fail_events.add("lifecycle_close")
        try:
            result = self.close(self.token_a)
        except Exception as exc:
            self.fail(str(exc))
        self.assertEqual(result.get("error"), "audit_failed")
        self.assertEqual(self.win32.posts, [])
        self.assertFalse(self._pending_close())

    def test_8604_close_run_without_lease_is_lease_required(self) -> None:
        self.add_run(process(11))
        result = self.close(None)
        self.assertEqual(result.get("error"), "lease_required")
        self.assertEqual(self.win32.posts, [])
        self.assertEqual(self.guard.terminate_calls, [])

    def test_8604_close_run_foreign_owner_or_not_running_is_run_not_adopted(
        self,
    ) -> None:
        foreign = self.add_run(process(21), owner="B")
        result = self.close(self.token_a, foreign.run_id)
        self.assertEqual(result.get("error"), "run_not_adopted")
        starting = process(22)
        self.store.add(
            RunRecord(
                "run-starting",
                "A",
                "lease-A",
                "STARTING",
                "same",
                "@SameMod",
                "profiles",
                "mission",
                [starting],
            )
        )
        result = self.close(self.token_a, "run-starting")
        self.assertEqual(result.get("error"), "run_not_adopted")
        self.assertEqual(self.win32.posts, [])

    def test_8604_close_run_unknown_identity_posts_nothing(self) -> None:
        record = process(31)
        self.add_run(record)
        before = self.manifest_bytes()
        state = self.store.get(RUN_ID).state
        result = self.close(self.token_a)
        self.assertIn(result.get("error"), {"process_identity_mismatch", "guard_unavailable"})
        self.assertEqual(self.win32.posts, [])
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.manifest_bytes(), before)
        self.assertEqual(self.store.get(RUN_ID).state, state)

    def test_8604_close_run_posts_visible_owned_client_then_server(self) -> None:
        client = process(41, "client")
        server = process(42, "server")
        gone = process(44, "client")
        foreign = process(45, "server")
        run = self.add_run(server, client, gone, foreign)
        self.guard.snapshots[client.pid] = snapshot(client)
        self.guard.snapshots[server.pid] = snapshot(server)
        self.guard.snapshots[gone.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        self.guard.snapshots[foreign.pid] = snapshot(foreign) | {
            "creation_time_utc": "9999-01-01T00:00:00.0000000Z"
        }
        self.win32.windows = [
            (100, server.pid, True),
            (101, client.pid, True),
            (102, client.pid, False),
            (103, gone.pid, True),
            (104, foreign.pid, True),
            (105, 99999, True),
        ]
        before = self.manifest_bytes()
        state = self.store.get(run.run_id).state
        result = self.close(self.token_a)
        self.assertEqual(result.get("run_id"), run.run_id)
        self.assertEqual(
            set(result) - {"run_id"},
            {"client", "server"},
        )
        self.assertEqual(result["client"]["pid"], client.pid)
        self.assertEqual(result["client"]["windows_found"], 1)
        self.assertEqual(result["client"]["windows_posted"], 1)
        self.assertEqual(result["server"]["pid"], server.pid)
        self.assertEqual(result["server"]["windows_found"], 1)
        self.assertEqual(result["server"]["windows_posted"], 1)
        posted_hwnds = [item[0] for item in self.win32.posts]
        self.assertEqual(posted_hwnds, [101, 100])
        self.assertTrue(all(item[1] == WM_CLOSE for item in self.win32.posts))
        audit = [
            event for event in self.audit.events if event.get("event") == "lifecycle_close"
        ]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["run_id"], run.run_id)
        self.assertEqual(audit[0]["owned_pids"], [client.pid, server.pid])
        self.assertEqual(self.manifest_bytes(), before)
        self.assertEqual(self.store.get(run.run_id).state, state)
        self.assertEqual(self.guard.terminate_calls, [])

    def test_8604_close_run_identity_change_between_list_and_post_posts_nothing(
        self,
    ) -> None:
        record = process(51)
        self.add_run(record)
        self.guard.snapshots[record.pid] = snapshot(record)
        self.guard.flip_after = 1
        self.win32.windows = [(200, record.pid, True)]
        before = self.manifest_bytes()
        result = self.close(self.token_a)
        self.assertEqual(result["client"]["windows_found"], 1)
        self.assertEqual(result["client"]["windows_posted"], 0)
        self.assertEqual(self.win32.posts, [])
        self.assertEqual(self.guard.terminate_calls, [])
        self.assertEqual(self.manifest_bytes(), before)
        self.assertEqual(self.store.get(RUN_ID).state, "RUNNING")

    def test_8604_close_run_gone_and_foreign_get_nothing(self) -> None:
        gone = process(61, "client")
        foreign = process(62, "server")
        owned = process(63, "client")
        self.add_run(gone, foreign, owned)
        self.guard.snapshots[owned.pid] = snapshot(owned)
        self.guard.snapshots[gone.pid] = {
            "error": "process_not_found",
            "exit_code": 4,
        }
        self.guard.snapshots[foreign.pid] = snapshot(foreign) | {
            "creation_time_utc": "9999-01-01T00:00:00.0000000Z"
        }
        self.win32.windows = [
            (300, gone.pid, True),
            (301, foreign.pid, True),
            (302, owned.pid, True),
        ]
        result = self.close(self.token_a)
        posted_hwnds = [item[0] for item in self.win32.posts]
        self.assertEqual(posted_hwnds, [302])
        self.assertNotIn("server", result)
        self.assertEqual(self.guard.terminate_calls, [])


class FakeLifecycleClose:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, object]] = []

    def close_run(self, client, token, value):
        self.calls.append((client.session_id, token, value))
        return {"run_id": value, "client": {"pid": 1, "windows_found": 1, "windows_posted": 1}}

    def status(self, client):
        return {"runs": []}


class Route8604Test(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("key")
        self.state.coordination = SessionCoordinator(
            token_fn=lambda: "token",
            id_fn=lambda: "lease",
            audit=lambda event: True,
        )
        self.lifecycle = FakeLifecycleClose()
        self.state.lifecycle = self.lifecycle
        self.httpd = loopback.create_http_server(
            0, self.state, log_sink=lambda _message: None, reclaim_orphans=False
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2.0)

    @slow_test
    def test_8604_lifecycle_close_route_dispatches_close_run(self) -> None:
        if "/lifecycle/close" not in loopback.LIFECYCLE_ROUTES:
            self.fail("/lifecycle/close is not registered")
        import urllib.error
        import urllib.parse
        import urllib.request

        identity = {
            "platform": "codex",
            "pid": 11,
            "ppid": 1,
            "started_at_utc": "2026-07-15T00:00:00Z",
            "session_id": "A",
            "task_label": "http",
        }
        url = self.base + "/lifecycle/close?" + urllib.parse.urlencode({"key": "key"})
        req = urllib.request.Request(
            url,
            data=json.dumps(
                {"identity": identity, "lease_token": "token", "run_id": "R"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=2.0) as response:
                body = json.loads(response.read())
                status = response.status
        except urllib.error.HTTPError as exc:
            self.fail(str(exc))
        except AttributeError as exc:
            self.fail(str(exc))
        self.assertEqual(status, 200)
        self.assertEqual(body.get("run_id"), "R")
        self.assertEqual(self.lifecycle.calls[-1][2], "R")


class Client8604Test(unittest.IsolatedAsyncioTestCase):
    async def test_8604_lifecycle_close_posts_identity_token_and_run_id(self) -> None:
        from tests.test_control_client import _policy
        from dayz_mcp import control_client as control

        if not hasattr(control.ControlClient, "lifecycle_close"):
            self.fail("ControlClient.lifecycle_close is missing")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = control.ControlClient(policy=_policy(keyfile), identity=identity)
            client.active_lease_token = "lease-token"
            with patch.object(
                client,
                "_session_call",
                new=AsyncMock(return_value={"run_id": "R"}),
            ) as session_call:
                try:
                    result = await client.lifecycle_close("R")
                except AttributeError as exc:
                    self.fail(str(exc))
        self.assertEqual(result, {"run_id": "R"})
        session_call.assert_awaited_once_with(
            "/lifecycle/close",
            {"run_id": "R", "lease_token": "lease-token"},
        )


def _write_rpt(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class CloseToolRuntime:
    def __init__(
        self,
        root: Path,
        *,
        lease: str | None = "held-lease",
        roles: tuple[str, ...] = ("client", "server"),
    ) -> None:
        self.active_lease_token = lease
        self.active_ticket = None
        self.active_operation_id = None
        self.root = root
        self.client_rpt = root / "_client" / "profiles" / "client.rpt"
        self.server_rpt = root / "_server" / "profiles" / "server.rpt"
        self.roles = roles
        self.status_calls = 0
        self.close_calls = 0
        self.reap_calls = 0
        self.close_posted = 1
        self.retire_event: str | None = None
        self.reap_event: str | None = None
        self.append_on_close: str | None = None
        self.append_on_reap: str | None = None
        self.rotate_on_close = False
        self.fail_status_after_close = False
        self.profiles: str | None = None
        self.lifecycle: dict[str, object] = self._status_payload()

    def _processes(self) -> list[dict[str, object]]:
        return [{"role": role, "pid": index + 1} for index, role in enumerate(self.roles)]

    def _status_payload(self) -> dict[str, object]:
        if self.retire_event is not None:
            return {
                "runs": [],
                "retired_run_diagnostics": [
                    {
                        "run_id": RUN_ID,
                        "daemon_generation_at_launch": "",
                        "daemon_generation_current": "",
                        "generation_changed": False,
                        "event": self.retire_event,
                        "reason": "all_processes_gone_or_foreign",
                        "decision": "reaped",
                        "state": "EXITED",
                    }
                ],
            }
        return {
            "runs": [
                {
                    "run_id": RUN_ID,
                    "state": "RUNNING",
                    "profiles": (
                        self.profiles
                        if self.profiles is not None
                        else str(self.client_rpt.parent)
                    ),
                    "processes": self._processes(),
                }
            ],
            "retired_run_diagnostics": [],
        }

    async def lifecycle_status(self) -> dict[str, object]:
        self.status_calls += 1
        if self.close_calls > 0 and self.fail_status_after_close:
            raise RuntimeError("status unavailable")
        return self._status_payload()

    async def lifecycle_close(self, run_id: str) -> dict[str, object]:
        self.close_calls += 1
        if self.append_on_close:
            for path in (self.client_rpt, self.server_rpt):
                if path.is_file():
                    path.write_text(
                        path.read_text(encoding="utf-8") + self.append_on_close,
                        encoding="utf-8",
                    )
        if self.rotate_on_close:
            for path in (self.client_rpt, self.server_rpt):
                if path.is_file():
                    path.unlink()
                    path.write_text("rotated\n" + TERMINATION, encoding="utf-8")
        result: dict[str, object] = {"run_id": run_id}
        for index, role in enumerate(self.roles):
            result[role] = {
                "pid": index + 1,
                "windows_found": self.close_posted,
                "windows_posted": self.close_posted,
            }
        return result

    async def lifecycle_reap(self, run_id: str) -> dict[str, object]:
        self.reap_calls += 1
        if self.append_on_reap:
            for path in (self.client_rpt, self.server_rpt):
                if path.is_file():
                    path.write_text(
                        path.read_text(encoding="utf-8") + self.append_on_reap,
                        encoding="utf-8",
                    )
        if self.reap_event is not None:
            self.retire_event = self.reap_event
            return {"ok": True, "run_id": run_id, "state": "EXITED"}
        raise dayz_test_tool.DayzTestToolError("run_not_reapable")


def _serialized_has_host_path(payload: dict[str, object], root: Path) -> bool:
    blob = json.dumps(payload)
    text = str(root)
    if text in blob or text.replace("\\", "/") in blob:
        return True
    for value in _walk_strings(payload):
        lowered = value.casefold()
        if ".rpt" in lowered or "\\profiles" in lowered or "/profiles" in lowered:
            return True
        if len(value) >= 2 and value[1] == ":" and ("\\" in value or "/" in value):
            return True
    return False


def _walk_strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(key)
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


async def _close_tool(runtime, **kwargs):
    fn = getattr(dayz_test_tool, "execute_dayz_test_close", None)
    if fn is None:
        raise AssertionError("execute_dayz_test_close is missing")
    try:
        return await fn(runtime, RUN_ID, **kwargs)
    except AttributeError as exc:
        raise AssertionError(str(exc)) from exc


class ToolWait8604Test(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.policy = dayz_test_request.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=str(self.root),
            default_source=str(self.root),
            default_base_mods=(),
            mission_roots=(str(self.root / "_server" / "mpmissions"),),
            mod_roots=(str(self.root),),
        )
        self._policy_patcher = patch.object(
            dayz_test_tool,
            "_close_project_policy",
            lambda _run: self.policy,
            create=True,
        )
        self._policy_patcher.start()

    def tearDown(self) -> None:
        self._policy_patcher.stop()
        self.temporary.cleanup()

    def _runtime(self, **kwargs) -> CloseToolRuntime:
        runtime = CloseToolRuntime(self.root, **kwargs)
        _write_rpt(runtime.client_rpt, "boot client\n")
        _write_rpt(runtime.server_rpt, "boot server\n")
        return runtime

    async def test_8604_tool_graceful_when_new_line_and_run_reaped(self) -> None:
        runtime = self._runtime()
        runtime.append_on_close = TERMINATION
        runtime.reap_event = "run_reaped"
        result = await _close_tool(runtime, graceful_timeout_s=2)
        self.assertTrue(result["graceful"])
        self.assertTrue(result["exit_metrics_valid"])
        self.assertFalse(result["stop_required"])
        self.assertIsNone(result["reason"])
        self.assertEqual(result["stop_method"], "orderly_close")
        self.assertTrue(result["run_retired"])
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertFalse(_serialized_has_host_path(result, self.root))

    @slow_test
    async def test_8604_tool_line_without_retirement_is_process_alive(self) -> None:
        runtime = self._runtime()
        runtime.append_on_close = TERMINATION
        result = await _close_tool(runtime, graceful_timeout_s=0.2)
        self.assertFalse(result["graceful"])
        self.assertTrue(result["stop_required"])
        self.assertEqual(result["reason"], "process_alive")
        self.assertFalse(_serialized_has_host_path(result, self.root))

    @slow_test
    async def test_8604_tool_existing_termination_line_is_not_new(self) -> None:
        runtime = self._runtime()
        _write_rpt(runtime.client_rpt, "boot client\n" + TERMINATION)
        _write_rpt(runtime.server_rpt, "boot server\n" + TERMINATION)
        result = await _close_tool(runtime, graceful_timeout_s=0.2)
        self.assertFalse(result["graceful"])
        self.assertFalse(_serialized_has_host_path(result, self.root))

    @slow_test
    async def test_8604_tool_rotated_rpt_is_rpt_rotated(self) -> None:
        runtime = self._runtime()
        runtime.rotate_on_close = True
        result = await _close_tool(runtime, graceful_timeout_s=0.2)
        self.assertFalse(result["graceful"])
        self.assertEqual(result["reason"], "rpt_rotated")
        self.assertTrue(any(item["rpt_rotated"] for item in result["roles"]))
        self.assertFalse(_serialized_has_host_path(result, self.root))

    @slow_test
    async def test_8604_tool_launched_role_without_rpt_is_role_without_rpt(
        self,
    ) -> None:
        runtime = self._runtime()
        runtime.server_rpt.unlink()
        result = await _close_tool(runtime, graceful_timeout_s=0.2)
        self.assertFalse(result["graceful"])
        self.assertEqual(result["reason"], "role_without_rpt")
        self.assertFalse(_serialized_has_host_path(result, self.root))

    async def test_8604_tool_other_retirement_is_run_retired_elsewhere(self) -> None:
        runtime = self._runtime()
        runtime.append_on_close = TERMINATION
        runtime.reap_event = "lifecycle_stop_outcome"
        result = await _close_tool(runtime, graceful_timeout_s=0.2)
        self.assertFalse(result["graceful"])
        self.assertEqual(result["reason"], "run_retired_elsewhere")
        self.assertTrue(result["run_retired"])
        self.assertFalse(result["stop_required"])
        self.assertFalse(_serialized_has_host_path(result, self.root))

    async def test_8604_tool_no_window_reason(self) -> None:
        runtime = self._runtime()
        runtime.close_posted = 0
        result = await _close_tool(runtime, graceful_timeout_s=0.2)
        self.assertFalse(result["graceful"])
        self.assertEqual(result["reason"], "no_window")
        self.assertFalse(_serialized_has_host_path(result, self.root))

    async def test_8604_tool_bad_timeouts_are_bad_args(self) -> None:
        runtime = self._runtime()
        for value in (0, 121, float("nan"), "45"):
            with self.subTest(value=value):
                with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                    await _close_tool(runtime, graceful_timeout_s=value)
                self.assertEqual(caught.exception.code, "bad_args")
        self.assertEqual(runtime.close_calls, 0)
        self.assertEqual(runtime.status_calls, 0)

    async def test_8604_tool_no_lease_is_lease_required_before_daemon(self) -> None:
        runtime = self._runtime(lease=None)
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            await _close_tool(runtime, graceful_timeout_s=1)
        self.assertEqual(caught.exception.code, "lease_required")
        self.assertEqual(runtime.status_calls, 0)
        self.assertEqual(runtime.close_calls, 0)

    async def test_8604_tool_is_registered_with_lease_line(self) -> None:
        app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None))
        tools = {tool.name: tool for tool in app._tool_manager.list_tools()}
        if "dayz_test_close" not in tools:
            self.fail("dayz_test_close is not registered")
        description = tools["dayz_test_close"].description or ""
        self.assertTrue(description.startswith(LEASE_TOOL_LINE), description[:80])
        self.assertIn("dayz_test_stop", description)

    def _policy_profile_roots(self) -> tuple[str, str]:
        return (
            ntpath.normcase(
                ntpath.normpath(ntpath.join(str(self.root), "_client", "profiles"))
            ),
            ntpath.normcase(
                ntpath.normpath(ntpath.join(str(self.root), "_server", "profiles"))
            ),
        )

    def _path_inside_policy_roots(self, path_str: str) -> bool:
        norm = ntpath.normcase(ntpath.normpath(path_str))
        if not ntpath.isabs(norm):
            norm = ntpath.normcase(
                ntpath.normpath(ntpath.join(str(Path.cwd()), path_str))
            )
        for root in self._policy_profile_roots():
            if norm == root or norm.startswith(root + ntpath.sep):
                return True
        return False

    @slow_test
    async def test_8604_r3_rpt_reads_are_incremental_and_capped(self) -> None:
        runtime = self._runtime()
        chunk = 2 * 1024 * 1024
        read_sizes: list[int] = []
        growth = {"n": 0}
        after_close = {"n": 0}
        real_open = Path.open

        def counting_open(self, *args, **kwargs):
            handle = real_open(self, *args, **kwargs)
            mode = args[0] if args else kwargs.get("mode", "r")
            if "r" in str(mode) and "b" in str(mode):
                orig = handle.read

                def read(size=-1):
                    data = orig() if size is None or size < 0 else orig(size)
                    read_sizes.append(len(data))
                    return data

                handle.read = read  # type: ignore[method-assign]
            return handle

        original_status = runtime.lifecycle_status

        def append_bytes(path: Path, data: bytes) -> None:
            with real_open(path, "ab") as handle:
                handle.write(data)

        async def growing_status():
            payload = await original_status()
            if runtime.close_calls > 0 and after_close["n"] < 3:
                blob = b"x" * chunk
                for path in (runtime.client_rpt, runtime.server_rpt):
                    append_bytes(path, blob)
                    growth["n"] += chunk
                after_close["n"] += 1
                if after_close["n"] == 3:
                    term = TERMINATION.encode("utf-8")
                    for path in (runtime.client_rpt, runtime.server_rpt):
                        append_bytes(path, term)
                        growth["n"] += len(term)
                    runtime.reap_event = "run_reaped"
            return payload

        runtime.lifecycle_status = growing_status  # type: ignore[method-assign]
        with patch.object(Path, "open", counting_open):
            try:
                result = await _close_tool(runtime, graceful_timeout_s=5)
            except Exception as exc:
                self.fail(str(exc))
        self.assertTrue(result["graceful"])
        self.assertTrue(read_sizes)
        self.assertTrue(all(size <= 1024 * 1024 for size in read_sizes))
        self.assertLessEqual(sum(read_sizes), growth["n"] + 4096)

    @slow_test
    async def test_8604_r3_profiles_outside_policy_roots_open_no_file(self) -> None:
        touches: list[tuple[str, str]] = []
        real_iterdir = Path.iterdir
        real_open = Path.open
        real_stat = Path.stat

        def iterdir(self):
            touches.append(("iterdir", str(self)))
            return real_iterdir(self)

        def path_open(self, *args, **kwargs):
            touches.append(("open", str(self)))
            return real_open(self, *args, **kwargs)

        def stat(self, *args, **kwargs):
            touches.append(("stat", str(self)))
            return real_stat(self, *args, **kwargs)

        def assert_untouched() -> None:
            outside = [
                item for item in touches if not self._path_inside_policy_roots(item[1])
            ]
            self.assertEqual(outside, [])

        with TemporaryDirectory() as other:
            outside_dir = Path(other) / "_client" / "profiles"
            _write_rpt(outside_dir / "evil.rpt", "boot\n" + TERMINATION)
            _write_rpt(
                Path(other) / "_server" / "profiles" / "evil.rpt",
                "boot\n" + TERMINATION,
            )
            runtime = self._runtime()
            runtime.profiles = str(outside_dir)
            touches.clear()
            with (
                patch.object(Path, "iterdir", iterdir),
                patch.object(Path, "open", path_open),
                patch.object(Path, "stat", stat),
            ):
                try:
                    result = await _close_tool(runtime, graceful_timeout_s=0.2)
                except Exception as exc:
                    self.fail(str(exc))
            self.assertFalse(result["graceful"])
            self.assertEqual(result["reason"], "role_without_rpt")
            assert_untouched()

        runtime = self._runtime()
        runtime.profiles = r"outside_rel_8604\_client\profiles"
        touches.clear()
        with (
            patch.object(Path, "iterdir", iterdir),
            patch.object(Path, "open", path_open),
            patch.object(Path, "stat", stat),
        ):
            try:
                result = await _close_tool(runtime, graceful_timeout_s=0.2)
            except Exception as exc:
                self.fail(str(exc))
        self.assertFalse(result["graceful"])
        self.assertEqual(result["reason"], "role_without_rpt")
        assert_untouched()

    async def test_8604_r3_termination_line_after_last_read_counts_once_reaped(
        self,
    ) -> None:
        runtime = self._runtime()
        runtime.append_on_reap = TERMINATION
        runtime.reap_event = "run_reaped"
        try:
            result = await _close_tool(runtime, graceful_timeout_s=2)
        except Exception as exc:
            self.fail(str(exc))
        self.assertTrue(result["graceful"])
        self.assertIsNone(result["reason"])

    async def test_8604_r3_retirement_elsewhere_or_no_window_ends_the_wait(
        self,
    ) -> None:
        runtime = self._runtime()
        runtime.reap_event = "lifecycle_stop_outcome"
        try:
            result = await _close_tool(runtime, graceful_timeout_s=2)
        except Exception as exc:
            self.fail(str(exc))
        self.assertEqual(result["reason"], "run_retired_elsewhere")
        self.assertLessEqual(runtime.reap_calls, 2)

        runtime = self._runtime()
        runtime.close_posted = 0
        try:
            result = await _close_tool(runtime, graceful_timeout_s=2)
        except Exception as exc:
            self.fail(str(exc))
        self.assertEqual(result["reason"], "no_window")
        self.assertEqual(runtime.reap_calls, 0)

    async def test_8604_r3_status_failure_after_close_requires_stop(self) -> None:
        runtime = self._runtime()
        runtime.fail_status_after_close = True
        try:
            result = await _close_tool(runtime, graceful_timeout_s=1)
        except Exception as exc:
            self.fail(str(exc))
        self.assertTrue(result["stop_required"])
        self.assertEqual(result["reason"], "status_unavailable")
        self.assertFalse(result["graceful"])

    async def test_8604_r3_transient_read_error_is_not_rotation(self) -> None:
        runtime = self._runtime()
        runtime.append_on_close = TERMINATION
        runtime.reap_event = "run_reaped"
        reads = {"n": 0}
        real_open = Path.open

        def flaky_open(self, *args, **kwargs):
            handle = real_open(self, *args, **kwargs)
            mode = args[0] if args else kwargs.get("mode", "r")
            if "r" in str(mode) and "b" in str(mode):
                orig = handle.read

                def read(size=-1):
                    reads["n"] += 1
                    if reads["n"] == 1:
                        raise OSError("locked")
                    return orig() if size is None or size < 0 else orig(size)

                handle.read = read  # type: ignore[method-assign]
            return handle

        with patch.object(Path, "open", flaky_open):
            try:
                result = await _close_tool(runtime, graceful_timeout_s=2)
            except Exception as exc:
                self.fail(str(exc))
        self.assertTrue(result["graceful"])
        self.assertFalse(any(item["rpt_rotated"] for item in result["roles"]))

    @slow_test
    async def test_8604_r3_reap_is_rate_limited(self) -> None:
        runtime = self._runtime()
        try:
            result = await _close_tool(runtime, graceful_timeout_s=2)
        except Exception as exc:
            self.fail(str(exc))
        self.assertLessEqual(runtime.reap_calls, 3)
        self.assertFalse(result["graceful"])

    async def test_8604_r3_unknown_role_is_not_published(self) -> None:
        unknown = r"C:\Windows\System32\notepad.exe"
        runtime = self._runtime(roles=("client", unknown))
        runtime.append_on_close = TERMINATION
        runtime.reap_event = "run_reaped"
        try:
            result = await _close_tool(runtime, graceful_timeout_s=0.4)
        except Exception as exc:
            self.fail(str(exc))
        blob = json.dumps(result)
        self.assertNotIn(unknown, blob)
        self.assertNotIn("notepad.exe", blob)
        self.assertEqual(result["reason"], "role_without_rpt")
        self.assertTrue(all(item["role"] != unknown for item in result["roles"]))


CHILD_WINDOW = r'''
import ctypes
from ctypes import wintypes
import sys

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
SW_SHOW = 5
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]

class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    ]

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
kernel32.GetCurrentProcessId.restype = wintypes.DWORD
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.UpdateWindow.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostQuitMessage.argtypes = [ctypes.c_int]

@WNDPROC
def wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_CLOSE:
        user32.DestroyWindow(hwnd)
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

class_name = "DayzMcp8604Close" + str(kernel32.GetCurrentProcessId())
wndclass = WNDCLASSW()
wndclass.lpfnWndProc = wnd_proc
wndclass.hInstance = kernel32.GetModuleHandleW(None)
wndclass.lpszClassName = class_name
atom = user32.RegisterClassW(ctypes.byref(wndclass))
if not atom:
    raise SystemExit("register_failed")
hwnd = user32.CreateWindowExW(
                0, class_name, "8604", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                40, 40, 120, 80,
    None, None, wndclass.hInstance, None,
)
if not hwnd:
    raise SystemExit("create_failed")
user32.ShowWindow(hwnd, SW_SHOW)
user32.UpdateWindow(hwnd)
sys.stdout.write("ready %d\n" % int(kernel32.GetCurrentProcessId()))
sys.stdout.flush()
msg = MSG()
while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
    user32.TranslateMessage(ctypes.byref(msg))
    user32.DispatchMessageW(ctypes.byref(msg))
'''


def _start_window_child(script: Path) -> tuple[subprocess.Popen, int]:
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 8.0

    def fail(message: str) -> None:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                pass
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except Exception:
                pass
        if proc.stderr is not None:
            try:
                proc.stderr.close()
            except Exception:
                pass
        raise AssertionError(message)

    if proc.stdout is None:
        fail("child stdout is missing")

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            err = proc.stderr.read() if proc.stderr is not None else ""
            fail(f"child exited before ready: {err}")
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            break
        holder: list[object] = []

        def read_line() -> None:
            try:
                stdout = proc.stdout
                holder.append("" if stdout is None else stdout.readline())
            except Exception as exc:
                holder.append(exc)

        reader = threading.Thread(target=read_line, daemon=True)
        reader.start()
        reader.join(remaining)
        if reader.is_alive():
            fail("child did not become ready")
        if not holder:
            continue
        item = holder[0]
        if isinstance(item, Exception):
            fail(str(item))
        if not isinstance(item, str):
            continue
        parts = item.strip().split()
        if parts and parts[0] == "ready":
            real_pid = int(parts[1]) if len(parts) > 1 else proc.pid
            return proc, real_pid
    fail("child did not become ready")
    raise AssertionError("child did not become ready")


def _reap_child(proc: subprocess.Popen | None, timeout: float = 3.0) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
    if proc.stdout is not None:
        proc.stdout.close()
    if proc.stderr is not None:
        proc.stderr.close()


class RealWindow8604Test(unittest.TestCase):
    @slow_test
    def test_8604_real_window_exits_on_posted_wm_close_other_child_untouched(
        self,
    ) -> None:
        owned = None
        other = None
        with TemporaryDirectory() as directory:
            script = Path(directory) / "child_window.py"
            script.write_text(CHILD_WINDOW, encoding="utf-8", newline="\n")
            try:
                owned, owned_pid = _start_window_child(script)
                other, _other_pid = _start_window_child(script)
                time.sleep(0.3)
                deadline = time.monotonic() + 8.0
                found_total = 0
                posted_total = 0
                while time.monotonic() < deadline:
                    outcome = window_close.close_visible_windows((owned_pid,))
                    row = outcome.get(owned_pid, {})
                    found_total = int(row.get("windows_found", 0))
                    posted_total += int(row.get("windows_posted", 0))
                    if owned.poll() is not None:
                        break
                    time.sleep(0.1)
                else:
                    self.fail(
                        "owned child did not exit after WM_CLOSE "
                        f"(windows_found={found_total} posted={posted_total} "
                        f"pid={owned_pid})"
                    )
                self.assertIsNotNone(owned.poll())
                time.sleep(0.4)
                self.assertIsNone(other.poll())
            finally:
                _reap_child(owned)
                _reap_child(other)


class StopKill8604Test(unittest.IsolatedAsyncioTestCase):
    async def test_8604_dayz_test_stop_still_builds_kill_request(self) -> None:
        source = inspect.getsource(dayz_test_tool.execute_dayz_test_stop)
        self.assertIn("kill=True", source)
        self.assertIn('mode="offline"', source)
        captured: list[dict[str, object]] = []
        real = dayz_test_tool.build_run_request

        def wrapped(*args, **kwargs):
            captured.append(dict(kwargs))
            return real(*args, **kwargs)

        runtime = dayz_test_tool  # placeholder to keep patch target local
        _ = runtime
        with patch.object(dayz_test_tool, "build_run_request", wrapped):
            source_after = inspect.getsource(dayz_test_tool.execute_dayz_test_stop)
        self.assertIn("kill=True", source_after)
        self.assertEqual(captured, [])


if __name__ == "__main__":
    unittest.main()
