"""Lease TTL on every tool result, and wait_for(file_matches) (584e, d17c)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from mcp import types

from dayz_mcp import dayz_test_tool, log_tail, mcp_supervisor, server
from dayz_mcp.lease_result_ttl import (
    LEASE_LOCAL_TOOL,
    LEASE_TTL_OBSERVE_TOOL,
    classify_status,
)
from dayz_mcp.server import ServerConfig, ToolError, build_app, execute_wait_for
from tests.lease_helpers import FakeClock, _coord, _identity


SESSION = "session-584e-owner-0001"
LEASE = "lease-584e"


def _profiles(root: Path, side: str) -> Path:
    path = root / side / "profiles"
    path.mkdir(parents=True)
    return path


def _run(root: Path, *, owner: str = SESSION, lease: str = LEASE, run_id: str = "run-1") -> dict:
    server_dir = _profiles(root, "_server")
    client_dir = _profiles(root, "_client")
    return {
        "run_id": run_id,
        "state": "RUNNING",
        "owner_session": owner,
        "owner_lease_id": lease,
        "profiles": str(server_dir),
        "profiles_by_role": {"server": str(server_dir), "client": str(client_dir), "offline": str(client_dir)},
        "processes": [{"role": "server", "pid": 1}, {"role": "client", "pid": 2}],
    }


class _Runtime:
    def __init__(self, run: dict, *, token: str | None = "tok", lease_id: str | None = LEASE) -> None:
        self.identity = SimpleNamespace(session_id=SESSION)
        self.tool_lock = asyncio.Lock()
        self._control = SimpleNamespace(active_lease_token=token, active_lease_id=lease_id)
        self.runs = [run]
        self.beats: list[str] = []
        self.acquire_calls = 0

    async def lifecycle_status(self) -> dict:
        return {"runs": list(self.runs)}

    async def session_heartbeat(self, token: str) -> dict:
        self.beats.append(token)
        if self._control.active_lease_id != LEASE:
            return {"error": "lease_invalid"}
        return {"ok": True}

    async def session_acquire_wait(self, *_args, **_kwargs):
        self.acquire_calls += 1
        raise AssertionError("file_matches must not acquire")


class _StopDeaf:
    """A stop signal that never fires: what a reader without cooperation sees.

    Regression fakes use it when the code under test passes no stop signal at
    all, so the fake blocks in a genuine ``Event.wait`` that an injected
    exception cannot interrupt.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def stopped(self) -> bool:
        return False

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


def _status(lease_id: str, ttl: float, *, state: str = "active") -> dict:
    if state != "active":
        return {"self": {"state": state}, "owner": None}
    return {
        "self": {"state": "active", "lease_id": lease_id},
        "owner": {"state": "active", "lease_id": lease_id, "expires_in_s": ttl},
    }


async def _protocol(app, name: str, arguments: dict | None = None):
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments or {}),
    )
    response = await app._mcp_server.request_handlers[types.CallToolRequest](request)
    return response.root


def _ttl_of(result) -> object:
    meta = result.meta or {}
    return meta.get("lease_ttl_s", "ABSENT")


class OwnerReadDoesNotRenewTest(unittest.TestCase):
    def test_584e_owner_bridge_read_does_not_extend_expiry(self) -> None:
        clock = FakeClock()
        coordinator = _coord(clock, attached=False)
        owner = _identity("a")
        token = coordinator.acquire(owner, "drive")[1]["lease_token"]
        clock.advance(119.0)
        read = coordinator.authorize(owner, token, "camera_get")
        self.assertTrue(read.allowed)
        self.assertEqual(read.lease_id, coordinator.status(owner)["self"]["lease_id"])
        clock.advance(1.0)
        refused = coordinator.authorize(owner, token, "world_spawn")
        self.assertEqual(refused.error, "lease_expired")
        clock2 = FakeClock()
        coordinator2 = _coord(clock2, attached=False)
        token2 = coordinator2.acquire(owner, "drive")[1]["lease_token"]
        clock2.advance(119.0)
        self.assertEqual(coordinator2.heartbeat(owner, token2)[0], 200)
        clock2.advance(119.0)
        self.assertTrue(coordinator2.authorize(owner, token2, "world_spawn").allowed)

    def test_round2_production_run_owner_and_unapproved_root(self) -> None:
        import dataclasses

        from dayz_mcp.process_lifecycle import RunRecord

        record = RunRecord(
            run_id="run-prod",
            owner_session_id=SESSION,
            owner_lease_id=LEASE,
            state="STARTING",
            label="label",
            mod="@DayZ_MCP",
            profiles=r"C:\unapproved\_server\profiles",
            mission="mission",
            processes=[],
        )
        row = dataclasses.asdict(record)
        self.assertEqual(server._one_owned_run([row], SESSION, LEASE)["run_id"], "run-prod")
        with self.assertRaises(ToolError) as raised:
            server._profiles_dir_for_role(row, "client")
        self.assertIn("profile_unresolved", str(raised.exception))

    def test_round2_inactive_caller_is_not_unknown(self) -> None:
        annotation = classify_status({
            "self": {"state": "none", "position": None},
            "owner": {"state": "active", "lease_id": "B", "expires_in_s": 120},
        }, "A")
        self.assertIsNone(annotation)


class ProtocolTtlTest(unittest.IsolatedAsyncioTestCase):
    def _build(self, platform: str = "codex"):
        app, runtime = build_app(
            ServerConfig(
                key="k", port=0, log_sink=lambda _message: None,
                auto_spawn_daemon=False, client_platform=platform,
            )
        )
        runtime._control = SimpleNamespace(
            active_lease_id=None, active_lease_token=None, session_status=None,
        )
        return app, runtime

    async def asyncSetUp(self) -> None:
        self.app, self.runtime = self._build("codex")
        self.runtime._control.active_lease_id = LEASE
        self.runtime._control.active_lease_token = "tok"
        self.runtime._control.session_status = AsyncMock(return_value=_status(LEASE, 42.0))
        self.runtime.bridge_status_payload = AsyncMock(return_value={"ok": True, "ready": False})
        self.runtime.session_heartbeat = AsyncMock(return_value={"ok": True})
        self.runtime.session_acquire_wait = AsyncMock(return_value={"ok": True, "status": "active"})

    async def test_584e_all_tool_results_carry_owned_ttl(self) -> None:
        dictionary = await _protocol(self.app, "bridge_status")
        alias = await _protocol(self.app, "lease_acquire", {"purpose": "drive"})
        with patch.object(
            server.mcp_capture,
            "capture_dual",
            return_value={
                "isError": False,
                "inline": {"data": base64.b64encode(b"\xff\xd8\xff\xd9").decode("ascii"), "mimeType": "image/jpeg"},
                "meta": {"frame_sha256": "abc"},
                "fullres_path": None,
            },
        ):
            image = await _protocol(self.app, "capture_screenshot", {"frames": 1})
        error = await _protocol(self.app, "wait_for", {"condition": "not-a-condition"})
        values = [_ttl_of(dictionary), _ttl_of(alias), _ttl_of(image), _ttl_of(error)]
        self.assertEqual(values, [42.0, 42.0, 42.0, 42.0])
        self.assertTrue(any(getattr(block, "type", None) == "image" for block in image.content))
        self.assertTrue(error.isError)
        # Another notice (server code freshness) may follow the TTL line when the
        # suite has loaded modules after the server snapshot; the contract is that
        # the error carries the TTL, not its position.
        self.assertTrue(
            any(
                "lease_ttl_s=42.000" in getattr(block, "text", "")
                for block in error.content
            ),
            [getattr(block, "text", None) for block in error.content],
        )
        parsed = json.loads(dictionary.content[0].text)
        self.assertEqual(parsed["lease_ttl_s"], 42.0)
        self.assertEqual(self.runtime.session_heartbeat.await_count, 0)

    async def test_584e_local_reads_show_declining_ttl(self) -> None:
        # capture_screenshot also reads session_status once for camera_unverified
        # (06a6) before the result decorator reads it again. The published TTL
        # is still the decorator's sample, and it still declines.
        self.runtime._control.session_status = AsyncMock(side_effect=[
            _status(LEASE, 40.0),
            _status(LEASE, 25.0),
            _status(LEASE, 10.0),
            _status(LEASE, 5.0),
        ])
        self.runtime.lifecycle_status = AsyncMock(return_value={"runs": []})
        first = await _protocol(self.app, "logs_since", {})
        with patch.object(
            server.mcp_capture, "capture_dual",
            return_value={
                "isError": False,
                "inline": {"data": base64.b64encode(b"\xff\xd8\xff\xd9").decode("ascii"), "mimeType": "image/jpeg"},
                "meta": {},
                "fullres_path": None,
            },
        ):
            second = await _protocol(self.app, "capture_screenshot", {"frames": 1})
        third = await _protocol(self.app, "bridge_status")
        self.assertGreater(_ttl_of(first), _ttl_of(second))
        self.assertGreater(_ttl_of(second), _ttl_of(third))
        self.assertEqual(self.runtime.session_heartbeat.await_count, 0)
        self.runtime.session_acquire_wait.assert_not_awaited()

    async def test_584e_foreign_or_replaced_lease_ttl_is_not_leaked(self) -> None:
        async def swapped():
            self.runtime._control.active_lease_id = "lease-other"
            return _status("lease-other", 99.0)

        self.runtime._control.session_status = swapped
        result = await _protocol(self.app, "bridge_status")
        self.assertNotIn("lease_ttl_s", result.meta or {})
        blob = " ".join(getattr(block, "text", "") for block in result.content)
        self.assertNotIn("lease_ttl_s=99", blob)
        self.assertNotIn('"lease_ttl_s": 99', blob)

        self.runtime._control.active_lease_id = LEASE
        self.runtime._control.session_status = AsyncMock(
            return_value={
                "self": {"state": "active", "lease_id": LEASE},
                "owner": {"state": "active", "lease_id": "someone-else", "expires_in_s": 77.0},
            }
        )
        foreign = await _protocol(self.app, "bridge_status")
        self.assertIsNone(_ttl_of(foreign))
        self.assertEqual((foreign.meta or {}).get("lease_ttl_status"), "unknown")
        # The freshness notice carries host paths and numbers of its own; the
        # foreign lease's 77 must not appear in anything else.
        lease_meta = {
            key: value
            for key, value in (foreign.meta or {}).items()
            if key != "server_code_freshness"
        }
        self.assertNotIn("77", json.dumps(lease_meta))

    async def test_584e_unknown_expired_pin_release_and_freshness(self) -> None:
        self.runtime._control.session_status = AsyncMock(side_effect=OSError("down"))
        unknown = await _protocol(self.app, "bridge_status")
        self.assertIsNone(unknown.meta["lease_ttl_s"])
        self.assertEqual(unknown.meta["lease_ttl_status"], "unknown")

        self.runtime._control.session_status = AsyncMock(return_value=_status(LEASE, 0.0, state="none"))
        expired = await _protocol(self.app, "bridge_status")
        self.assertNotIn("lease_ttl_s", expired.meta or {})

        self.runtime._control.session_status = AsyncMock(return_value=_status(LEASE, 250.0))
        pinned = await _protocol(self.app, "bridge_status")
        self.assertEqual(_ttl_of(pinned), 250.0)

        async def released_payload(**_kwargs):
            # The release tool drops the local lease before it returns. The
            # decorator then sees no caller lease.
            self.runtime._control.active_lease_id = None
            self.runtime._control.active_lease_token = None
            return {"released": True}

        self.runtime.bridge_status_payload = released_payload
        released = await _protocol(self.app, "bridge_status")
        self.assertNotIn("lease_ttl_s", released.meta or {})

        self.runtime._control.active_lease_id = LEASE
        self.runtime._control.session_status = AsyncMock(return_value=_status(LEASE, 11.0))
        self.runtime.bridge_status_payload = AsyncMock(return_value={"ok": True})
        listed = await self.app.list_tools()
        self.assertGreater(len(listed), 10)
        again = await _protocol(self.app, "bridge_status")
        self.assertEqual(_ttl_of(again), 11.0)
        self.assertTrue(again.content)

    async def test_584e_catalogs_full_and_compact_still_annotate(self) -> None:
        full_app, full_runtime = self._build("claude")
        full_runtime._control.active_lease_id = LEASE
        full_runtime._control.session_status = AsyncMock(return_value=_status(LEASE, 8.0))
        full_runtime.bridge_status_payload = AsyncMock(return_value={"ok": True})
        full_listed = await full_app.list_tools()
        compact_listed = await self.app.list_tools()
        self.assertGreater(len(full_listed), 0)
        self.assertGreater(len(compact_listed), 0)
        annotated = await _protocol(full_app, "bridge_status")
        compact_call = await _protocol(self.app, "bridge_status")
        self.assertEqual(_ttl_of(annotated), 8.0)
        self.assertEqual(_ttl_of(compact_call), 42.0)


class _Lines:
    def __init__(self) -> None:
        self._queue: list[bytes] = []
        self._event = threading.Event()

    def push(self, raw: bytes) -> None:
        self._queue.append(raw)
        self._event.set()

    def readline(self) -> bytes:
        while not self._queue:
            self._event.wait(0.2)
            self._event.clear()
        return self._queue.pop(0)


class _Worker:
    def __init__(self) -> None:
        self.pid = 4242
        self.stdout = _Lines()
        self.sent: list[dict] = []
        self._stdin_closed = False
        self.answer_status = True
        self.local_lease_id = LEASE
        self.hold_status = False
        self.held_status: list[dict] = []

        class _In:
            def __init__(self, outer: "_Worker") -> None:
                self._outer = outer

            def write(self, raw: bytes) -> None:
                message = json.loads(raw)
                self._outer.sent.append(message)
                name = (message.get("params") or {}).get("name")
                if name == LEASE_LOCAL_TOOL:
                    body = {
                        "content": [{"type": "text", "text": json.dumps({"local_lease_id": self._outer.local_lease_id})}],
                        "structuredContent": {"local_lease_id": self._outer.local_lease_id},
                        "isError": False,
                    }
                    self._outer.stdout.push((json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": body}) + "\n").encode())
                elif name == LEASE_TTL_OBSERVE_TOOL and self._outer.hold_status:
                    self._outer.held_status.append(message)
                elif name == LEASE_TTL_OBSERVE_TOOL and self._outer.answer_status:
                    payload = getattr(self._outer, "status_body", None) or _status(LEASE, 33.0)
                    body = {
                        "content": [{"type": "text", "text": json.dumps(payload)}],
                        "structuredContent": payload,
                        "isError": False,
                    }
                    self._outer.stdout.push((json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": body}) + "\n").encode())
                elif message.get("id") in (mcp_supervisor.REPLAY_ID, mcp_supervisor.HEARTBEAT_ID):
                    self._outer.stdout.push((json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"ok": True}}) + "\n").encode())

            def flush(self) -> None:
                return

            def close(self) -> None:
                self._outer._stdin_closed = True

        self.stdin = _In(self)

    def poll(self):
        return 0 if self._stdin_closed else None

    def wait(self, timeout=None):
        return 0

    def kill(self) -> None:
        self._stdin_closed = True


class SupervisorTtlTest(unittest.TestCase):
    def test_584e_server_reload_result_reports_ttl(self) -> None:
        host_lines: list[bytes] = []

        class _Host:
            def write(self, raw: bytes) -> None:
                host_lines.append(raw)

            def flush(self) -> None:
                return

        workers: list[_Worker] = []

        def spawn() -> _Worker:
            item = _Worker()
            workers.append(item)
            return item

        supervisor = mcp_supervisor.Supervisor(
            spawn=spawn,
            out_stream=_Host(),
            log=lambda _line: None,
            drain_timeout_s=0.5,
            stdin_close_grace_s=0.2,
            replay_timeout_s=1.0,
            heartbeat_timeout_s=1.0,
            lease_status_timeout_s=1.0,
        )
        supervisor.start_worker()
        worker = workers[-1]
        supervisor.handle_host_line(
            (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n").encode()
        )
        worker.stdout.push((json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}}) + "\n").encode())
        supervisor.handle_host_line((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode())
        supervisor.handle_host_line(
            (json.dumps({
                "jsonrpc": "2.0", "id": 9, "method": "tools/call",
                "params": {"name": "server_reload", "arguments": {}},
            }) + "\n").encode()
        )
        deadline = time.monotonic() + 5.0
        message = None
        while time.monotonic() < deadline and message is None:
            for raw in list(host_lines):
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(parsed, dict) and parsed.get("id") == 9:
                    message = parsed
            time.sleep(0.01)
        self.assertIsNotNone(message)
        result = message["result"]
        self.assertEqual(result["_meta"]["lease_ttl_s"], 33.0)
        self.assertIn("lease_ttl_s=33.000", result["content"][-1]["text"])
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["status"], "recycled")
        self.assertEqual(payload["lease_ttl_s"], 33.0)
        self.assertTrue(any(
            (item.get("params") or {}).get("name") == LEASE_TTL_OBSERVE_TOOL
            for item in workers[-1].sent
        ))

    def test_round2_failed_observation_keeps_unknown(self) -> None:
        workers: list[_Worker] = []

        def spawn() -> _Worker:
            item = _Worker()
            workers.append(item)
            return item

        supervisor = mcp_supervisor.Supervisor(
            spawn=spawn,
            out_stream=open(os.devnull, "wb"),
            log=lambda _line: None,
            lease_status_timeout_s=1.0,
        )
        supervisor.start_worker()
        first = supervisor._observe_lease_ttl(supervisor._current)
        self.assertEqual(first["lease_ttl_s"], 33.0)
        workers[-1].answer_status = False
        supervisor._lease_status_timeout_s = 0.02
        second = supervisor._observe_lease_ttl(supervisor._current)
        self.assertIsNone(second["lease_ttl_s"])
        self.assertEqual(second["lease_ttl_status"], "unknown")


class FileMatchesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.run = _run(self.root)
        self.runtime = _Runtime(self.run)
        self.server_file = Path(self.run["profiles_by_role"]["server"]) / "mission.log"
        self.client_file = Path(self.run["profiles_by_role"]["client"]) / "mission.log"

        def _folder(policy, role, _roots):
            side = "_client" if role in {"client", "offline"} else "_server"
            return str(Path(policy.dev_root) / side / "profiles")

        self._policy = patch.object(
            dayz_test_tool, "_close_project_policy",
            return_value=SimpleNamespace(dev_root=str(self.root)),
        )
        self._roots = patch.object(
            dayz_test_tool, "_start_role_roots",
            return_value={"server": "_server", "client": "_client"},
        )
        self._role_folder = patch.object(
            dayz_test_tool, "_close_role_folder", side_effect=_folder,
        )
        self._policy.start()
        self._roots.start()
        self._role_folder.start()

    async def asyncTearDown(self) -> None:
        self._role_folder.stop()
        self._roots.stop()
        self._policy.stop()
        self._tmp.cleanup()

    async def test_d17cb_profile_file_matches_and_heartbeats_each_poll(self) -> None:
        self.server_file.write_text("idle\n", encoding="utf-8")

        async def beat(token: str) -> dict:
            self.runtime.beats.append(token)
            if len(self.runtime.beats) == 3:
                with self.server_file.open("a", encoding="utf-8") as handle:
                    handle.write("ready MARKER\n")
            return {"ok": True}

        self.runtime.session_heartbeat = beat
        result = await execute_wait_for(
            self.runtime, "file_matches", pattern="MARKER", profile_file="mission.log",
            timeout_s=5.0, poll_interval_s=0.5, lookback_lines=0,
        )
        self.assertTrue(result["satisfied"])
        self.assertEqual(result["role"], "server")
        self.assertEqual(result["profile_file"], "mission.log")
        self.assertGreaterEqual(len(self.runtime.beats), 3)
        self.assertEqual(len(self.runtime.beats), result["probes"])
        self.assertEqual(self.runtime.acquire_calls, 0)

    async def test_d17cb_missing_file_still_renews(self) -> None:
        async def beat(token: str) -> dict:
            self.runtime.beats.append(token)
            if len(self.runtime.beats) == 3:
                self.server_file.write_text("late MARKER\n", encoding="utf-8")
            return {"ok": True}

        self.runtime.session_heartbeat = beat
        result = await execute_wait_for(
            self.runtime, "file_matches", pattern="MARKER", profile_file="mission.log",
            timeout_s=5.0, poll_interval_s=0.5, lookback_lines=50,
        )
        self.assertTrue(result["satisfied"])
        self.assertGreaterEqual(len(self.runtime.beats), 3)

    async def test_d17cb_requires_owned_run_and_lease(self) -> None:
        self.server_file.write_text("SECRET\n", encoding="utf-8")
        self.runtime._control.active_lease_token = None
        with self.assertRaises(ToolError) as raised:
            await execute_wait_for(
                self.runtime, "file_matches", pattern="SECRET", profile_file="mission.log",
                timeout_s=2.0, poll_interval_s=0.5,
            )
        self.assertIn("lease_required", str(raised.exception))
        self.assertEqual(self.runtime.beats, [])
        bare = _Runtime(self.run, token="tok", lease_id=LEASE)
        bare.runs = [dict(self.run, owner_session="other-session")]
        with self.assertRaises(ToolError) as raised:
            await execute_wait_for(
                bare, "file_matches", pattern="SECRET", profile_file="mission.log",
                timeout_s=2.0, poll_interval_s=0.5,
            )
        self.assertIn("no_active_run", str(raised.exception))
        self.assertEqual(bare.beats, [])

    async def test_d17cb_rejects_profile_escape(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_text("OUTSIDE-SECRET\n", encoding="utf-8")
        for relative in ("../outside.txt", "C:\\outside.txt", "notes.txt:stream", "NUL", "\\\\?\\C:\\outside.txt"):
            with self.assertRaises(ToolError):
                await execute_wait_for(
                    self.runtime, "file_matches", pattern="OUTSIDE", profile_file=relative,
                    timeout_s=1.0, poll_interval_s=0.5,
                )
        link = Path(self.run["profiles"]) / "jump"
        subprocess.check_call(
            ["cmd", "/c", "mklink", "/J", str(link), str(self.root)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        with self.assertRaises(ToolError):
            await execute_wait_for(
                self.runtime, "file_matches", pattern="OUTSIDE", profile_file="jump/outside.txt",
                timeout_s=2.0, poll_interval_s=0.5,
            )
        try:
            os.symlink(outside, Path(self.run["profiles"]) / "linked.txt")
        except OSError:
            pass
        else:
            with self.assertRaises(ToolError):
                await execute_wait_for(
                    self.runtime, "file_matches", pattern="OUTSIDE", profile_file="linked.txt",
                    timeout_s=2.0, poll_interval_s=0.5,
                )
        forged = log_tail.encode_marker({
            str(outside): log_tail.TailMarker(path=str(outside), offset=0, size=0, identity=0)
        })
        self.server_file.write_text("inside\n", encoding="utf-8")
        with self.assertRaises(ToolError) as raised:
            await execute_wait_for(
                self.runtime, "file_matches", pattern="OUTSIDE", profile_file="mission.log",
                marker=forged, timeout_s=2.0, poll_interval_s=0.5,
            )
        self.assertIn("bad_marker", str(raised.exception))
        self.assertEqual(outside.read_text(encoding="utf-8"), "OUTSIDE-SECRET\n")

    async def test_d17cb_lease_loss_aborts_without_reacquire(self) -> None:
        self.server_file.write_text("nope\n", encoding="utf-8")

        async def beat(token: str) -> dict:
            self.runtime.beats.append(token)
            if len(self.runtime.beats) == 2:
                self.runtime._control.active_lease_id = "replaced"
            return {"ok": True}

        self.runtime.session_heartbeat = beat
        with self.assertRaises(ToolError) as raised:
            await execute_wait_for(
                self.runtime, "file_matches", pattern="MARKER", profile_file="mission.log",
                timeout_s=5.0, poll_interval_s=0.5, lookback_lines=0,
            )
        self.assertIn("lease", str(raised.exception))
        self.assertEqual(len(self.runtime.beats), 2)
        self.assertEqual(self.runtime.acquire_calls, 0)

    async def test_d17cb_marker_excludes_old_result(self) -> None:
        self.server_file.write_text("old MARKER\n", encoding="utf-8")
        missed = await execute_wait_for(
            self.runtime, "file_matches", pattern="NOPE", profile_file="mission.log",
            timeout_s=0.6, poll_interval_s=0.5, lookback_lines=0,
        )
        self.assertFalse(missed["satisfied"])
        self.assertIsNotNone(missed["cursor"])
        stale = await execute_wait_for(
            self.runtime, "file_matches", pattern="MARKER", profile_file="mission.log",
            marker=missed["cursor"], timeout_s=0.6, poll_interval_s=0.5,
        )
        self.assertFalse(stale["satisfied"])
        with self.server_file.open("a", encoding="utf-8") as handle:
            handle.write("new MARKER\n")
        fresh = await execute_wait_for(
            self.runtime, "file_matches", pattern="MARKER", profile_file="mission.log",
            marker=missed["cursor"], timeout_s=2.0, poll_interval_s=0.5,
        )
        self.assertTrue(fresh["satisfied"])
        self.assertIn("new MARKER", fresh["observed"])

    async def test_d17cb_role_partial_truncation_cancel_deadline_and_lock(self) -> None:
        self.server_file.write_text("SERVER-ONLY\n", encoding="utf-8")
        self.client_file.write_text("CLIENT-ONLY\n", encoding="utf-8")
        server_hit = await execute_wait_for(
            self.runtime, "file_matches", pattern="CLIENT-ONLY", profile_file="mission.log",
            role="server", timeout_s=0.6, poll_interval_s=0.5, lookback_lines=20,
        )
        self.assertFalse(server_hit["satisfied"])
        client_hit = await execute_wait_for(
            self.runtime, "file_matches", pattern="CLIENT-ONLY", profile_file="mission.log",
            role="client", timeout_s=2.0, poll_interval_s=0.5, lookback_lines=20,
        )
        self.assertTrue(client_hit["satisfied"])

        self.server_file.write_bytes(b"nope\n" * 4000 + b"TAILMATCH\n")
        capped = await execute_wait_for(
            self.runtime, "file_matches", pattern="nope", profile_file="mission.log",
            timeout_s=0.6, poll_interval_s=0.5, lookback_lines=1,
        )
        # lookback 1 still sees the last complete line before TAILMATCH only if
        # the tail window includes nope. The last line is TAILMATCH, so nope may
        # still sit in the previous line. Assert the scan did not return every line.
        self.assertLess(capped["scanned"]["lines_total"], 4000)

        partial = Path(self.run["profiles"]) / "partial.log"
        partial.write_bytes(b"partial MARKER")
        early = await execute_wait_for(
            self.runtime, "file_matches", pattern="MARKER", profile_file="partial.log",
            timeout_s=0.6, poll_interval_s=0.5, lookback_lines=5,
        )
        self.assertFalse(early["satisfied"])
        partial.write_bytes(b"partial MARKER\n")
        later = await execute_wait_for(
            self.runtime, "file_matches", pattern="MARKER", profile_file="partial.log",
            timeout_s=2.0, poll_interval_s=0.5, lookback_lines=5,
        )
        self.assertTrue(later["satisfied"])

        self.server_file.write_text("old\n", encoding="utf-8")
        first = await execute_wait_for(
            self.runtime, "file_matches", pattern="NOPE", profile_file="mission.log",
            timeout_s=0.6, poll_interval_s=0.5, lookback_lines=0,
        )
        self.server_file.write_text("replaced MARKER\n", encoding="utf-8")
        replaced = await execute_wait_for(
            self.runtime, "file_matches", pattern="MARKER", profile_file="mission.log",
            marker=first["cursor"], timeout_s=2.0, poll_interval_s=0.5,
        )
        self.assertTrue(replaced["satisfied"])

        held = asyncio.Event()

        async def taker() -> None:
            await asyncio.sleep(0.2)
            await self.runtime.tool_lock.acquire()
            held.set()
            self.runtime.tool_lock.release()

        task = asyncio.create_task(execute_wait_for(
            self.runtime, "file_matches", pattern="NEVER", profile_file="mission.log",
            timeout_s=2.0, poll_interval_s=0.5, lookback_lines=0,
        ))
        waiter = asyncio.create_task(taker())
        await waiter
        self.assertTrue(held.is_set())
        await task

        self.runtime.beats.clear()
        slow = asyncio.create_task(execute_wait_for(
            self.runtime, "file_matches", pattern="NEVER", profile_file="mission.log",
            timeout_s=30.0, poll_interval_s=0.5, lookback_lines=0,
        ))
        await asyncio.sleep(0.2)
        slow.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await slow
        beats_at_cancel = len(self.runtime.beats)
        await asyncio.sleep(0.7)
        self.assertEqual(len(self.runtime.beats), beats_at_cancel)

        with self.assertRaises(ToolError):
            await execute_wait_for(
                self.runtime, "file_matches", pattern="X", profile_file="mission.log",
                lookback_from="launch", timeout_s=1.0, poll_interval_s=0.5,
            )
        with self.assertRaises(ToolError):
            await execute_wait_for(
                self.runtime, "file_matches", pattern="X", profile_file="mission.log",
                timeout_s=1.0, poll_interval_s=121.0,
            )

    async def test_f3_policy_budget_and_cancel_stops_reader(self) -> None:
        original = server._profiles_dir_for_role

        def slow(run, role):
            time.sleep(0.2)
            return original(run, role)

        ran = asyncio.Event()

        async def other() -> None:
            await asyncio.sleep(0.01)
            ran.set()

        task = asyncio.create_task(other())
        started = time.monotonic()
        with patch.object(server, "_profiles_dir_for_role", side_effect=slow):
            result = await execute_wait_for(
                self.runtime, "file_matches", pattern="READY", profile_file="mission.log",
                timeout_s=0.05, poll_interval_s=0.5,
            )
        elapsed = time.monotonic() - started
        await task
        self.assertFalse(result["satisfied"])
        self.assertLess(elapsed, 0.15)
        self.assertEqual(self.runtime.beats, [])
        self.assertTrue(ran.is_set())

        started_read = threading.Event()
        release = threading.Event()
        state = {"alive": None, "finished": False}

        def block(*_args, **_kwargs):
            state["alive"] = threading.current_thread()
            started_read.set()
            while not release.is_set():
                time.sleep(0.01)
            state["finished"] = True
            return {"state": "missing"}

        with patch.object(server, "_read_contained_handle", side_effect=block):
            waiting = asyncio.create_task(execute_wait_for(
                self.runtime, "file_matches", pattern="READY", profile_file="mission.log",
                timeout_s=5.0, poll_interval_s=0.5, lookback_lines=0,
            ))
            await asyncio.to_thread(started_read.wait, 2.0)
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
        self.assertFalse(state["finished"])
        self.assertFalse(state["alive"].is_alive())

    async def test_f8_read_oserror_stays_in_scan(self) -> None:
        self.server_file.write_text("READY\n", encoding="utf-8")

        def boom(*_args, **_kwargs):
            raise OSError("read unavailable")

        with patch.object(server, "_marker_rewound_handle", side_effect=boom):
            result = await execute_wait_for(
                self.runtime, "file_matches", pattern="READY", profile_file="mission.log",
                timeout_s=0.2, poll_interval_s=0.5, lookback_lines=5,
            )
        self.assertFalse(result["satisfied"])
        self.assertIn("scanned", result)
        self.assertTrue(result["scanned"].get("unreadable") or any(
            item.get("readable") is False for item in result["scanned"].get("files", [])
        ))

    async def test_f10_lease_released_during_read_aborts(self) -> None:
        self.server_file.write_text("READY\n", encoding="utf-8")

        def slow(*_args, **_kwargs):
            time.sleep(0.05)
            marker = log_tail.TailMarker(path=str(self.server_file), offset=0, size=6, identity=1)
            return {"state": "ok", "path": str(self.server_file), "lines": ["READY"], "marker": marker, "count": 1}

        async def thief() -> None:
            await asyncio.sleep(0.01)
            await self.runtime.tool_lock.acquire()
            self.runtime._control.active_lease_id = None
            self.runtime.tool_lock.release()

        thief_task = asyncio.create_task(thief())
        with patch.object(server, "_read_contained_handle", side_effect=slow):
            with self.assertRaises(ToolError) as raised:
                await execute_wait_for(
                    self.runtime, "file_matches", pattern="READY", profile_file="mission.log",
                    timeout_s=2.0, poll_interval_s=0.5, lookback_lines=0,
                )
        await thief_task
        self.assertIn("lease_expired", str(raised.exception))

    async def test_f3_blocked_reader_is_stopped_joined_or_reported(self) -> None:
        # (a) A reader blocked on the stop signal ends when the deadline or
        # the cleanup fires. An injected exception cannot run inside the
        # blocking wait, so only the cooperative signal can end it, and the
        # caller must join it before returning.
        state: dict = {"started": threading.Event()}

        def blocked_on_stop(*_args, stop=None, **_kwargs):
            state["thread"] = threading.current_thread()
            state["started"].set()
            signal = stop if stop is not None else _StopDeaf()
            while not signal.stopped:
                signal.wait(0.5)
            return {"state": "ok", "path": "x", "lines": ["READY"], "marker": None, "count": 1}

        with patch.object(server, "_read_contained_handle", side_effect=blocked_on_stop):
            began = time.monotonic()
            result = await execute_wait_for(
                self.runtime, "file_matches", pattern="READY", profile_file="mission.log",
                timeout_s=0.05, poll_interval_s=0.5, lookback_lines=0,
            )
            elapsed = time.monotonic() - began
        self.assertFalse(result["satisfied"])
        self.assertFalse(state["thread"].is_alive())
        self.assertLess(elapsed, 1.0)

        # (b) Cancelling the wait also stops and joins the reader.
        state2: dict = {"started": threading.Event()}

        def blocked_on_stop2(*_args, stop=None, **_kwargs):
            state2["thread"] = threading.current_thread()
            state2["started"].set()
            signal = stop if stop is not None else _StopDeaf()
            while not signal.stopped:
                signal.wait(0.5)
            return {"state": "ok", "path": "x", "lines": ["READY"], "marker": None, "count": 1}

        with patch.object(server, "_read_contained_handle", side_effect=blocked_on_stop2):
            task = asyncio.create_task(execute_wait_for(
                self.runtime, "file_matches", pattern="READY", profile_file="mission.log",
                timeout_s=5.0, poll_interval_s=0.5, lookback_lines=0,
            ))
            await asyncio.to_thread(state2["started"].wait, 2.0)
            began = time.monotonic()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertLess(time.monotonic() - began, 1.0)
        self.assertFalse(state2["thread"].is_alive())

        # (c) A reader that ignores the stop signal is not claimed terminated:
        # the scan diagnostics say it was still alive at return.
        state3: dict = {"started": threading.Event()}
        release = threading.Event()

        def ignores_stop(*_args, stop=None, **_kwargs):
            state3["thread"] = threading.current_thread()
            state3["started"].set()
            release.wait(5.0)
            return {"state": "ok", "path": "x", "lines": ["READY"], "marker": None, "count": 1}

        with patch.object(server, "_read_contained_handle", side_effect=ignores_stop):
            result = await execute_wait_for(
                self.runtime, "file_matches", pattern="READY", profile_file="mission.log",
                timeout_s=0.4, poll_interval_s=0.5, lookback_lines=0,
            )
        self.assertFalse(result["satisfied"])
        self.assertTrue(result["scanned"].get("reader_alive_at_return"))
        release.set()
        state3["thread"].join(6.0)
        self.assertFalse(state3["thread"].is_alive())

    async def test_f10_release_during_final_status_aborts(self) -> None:
        self.server_file.write_text("READY\n", encoding="utf-8")
        release_next = {"armed": False}
        original_status = self.runtime.lifecycle_status

        async def status_with_release() -> dict:
            if release_next["armed"]:
                release_next["armed"] = False
                # The lease is released while the final lifecycle status runs,
                # so only a recheck after that await can see it.
                self.runtime._control.active_lease_id = None
            return await original_status()

        self.runtime.lifecycle_status = status_with_release

        def match_and_arm(*_args, **_kwargs):
            release_next["armed"] = True
            marker = log_tail.TailMarker(path=str(self.server_file), offset=0, size=6, identity=1)
            return {
                "state": "ok",
                "path": str(self.server_file),
                "lines": ["READY"],
                "marker": marker,
                "count": 1,
            }

        with patch.object(server, "_read_contained_handle", side_effect=match_and_arm):
            with self.assertRaises(ToolError) as raised:
                await execute_wait_for(
                    self.runtime, "file_matches", pattern="READY", profile_file="mission.log",
                    timeout_s=2.0, poll_interval_s=0.5, lookback_lines=0,
                )
        self.assertIn("lease_expired", str(raised.exception))

    async def test_f12_client_role_requires_launched_client(self) -> None:
        server_only = dict(self.run)
        server_only["processes"] = [{"role": "server", "pid": 1}]
        with self.assertRaises(ToolError) as raised:
            server._profiles_dir_for_role(server_only, "client")
        self.assertIn("profile_unresolved", str(raised.exception))
        self.assertEqual(
            server._profiles_dir_for_role(self.run, "server"),
            self.run["profiles"],
        )


class Round3SupervisorTest(unittest.TestCase):
    def test_f5_unknown_without_prior_confirm_and_owner_mismatch(self) -> None:
        workers: list[_Worker] = []

        def spawn() -> _Worker:
            item = _Worker()
            workers.append(item)
            return item

        supervisor = mcp_supervisor.Supervisor(
            spawn=spawn, out_stream=open(os.devnull, "wb"), log=lambda _line: None,
            lease_status_timeout_s=0.05,
        )
        supervisor.start_worker()
        workers[-1].answer_status = False
        missing = supervisor._observe_lease_ttl(supervisor._current)
        self.assertIsNone(missing["lease_ttl_s"])
        self.assertEqual(missing["lease_ttl_status"], "unknown")
        # The worker still holds local lease A. A status naming another lease
        # never publishes that other lease's TTL.
        workers[-1].answer_status = True
        workers[-1].status_body = _status("B", 17.0)
        mismatch = supervisor._observe_lease_ttl(supervisor._current)
        self.assertIsNone(mismatch["lease_ttl_s"])
        self.assertEqual(mismatch["lease_ttl_status"], "unknown")
        # Matching ids do publish the worker's own TTL.
        workers[-1].status_body = _status(LEASE, 17.0)
        own = supervisor._observe_lease_ttl(supervisor._current)
        self.assertEqual(own["lease_ttl_s"], 17.0)

    def test_f5_release_between_local_read_and_status_is_unknown(self) -> None:
        host_lines: list[dict] = []

        class _Host:
            def write(self, raw: bytes) -> None:
                host_lines.append(json.loads(raw))

            def flush(self) -> None:
                return

        workers: list[_Worker] = []

        def spawn() -> _Worker:
            item = _Worker()
            workers.append(item)
            return item

        supervisor = mcp_supervisor.Supervisor(
            spawn=spawn, out_stream=_Host(), log=lambda _line: None,
            lease_status_timeout_s=2.0,
        )
        supervisor.start_worker()
        worker = workers[-1]
        worker.hold_status = True
        done = threading.Event()

        def reply() -> None:
            supervisor._reply(9, {"status": "recycled"}, is_error=True)
            done.set()

        threading.Thread(target=reply, daemon=True).start()
        # The observation reads local lease A, then blocks on the status body.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not worker.held_status:
            time.sleep(0.01)
        self.assertTrue(worker.held_status)
        # The worker releases while that status response is still in flight;
        # the delivered body was observed before the release.
        worker.local_lease_id = None
        worker.hold_status = False
        held = worker.held_status.pop(0)
        payload = _status(LEASE, 33.0)
        body = {
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "structuredContent": payload,
            "isError": False,
        }
        worker.stdout.push(
            (json.dumps({"jsonrpc": "2.0", "id": held["id"], "result": body}) + "\n").encode()
        )
        self.assertTrue(done.wait(5.0))
        result = host_lines[0]["result"]
        meta = result.get("_meta") or {}
        self.assertIsNone(meta.get("lease_ttl_s"))
        self.assertEqual(meta.get("lease_ttl_status"), "unknown")
        self.assertIn("lease_ttl_s=unknown", result["content"][-1]["text"])

    def test_f11_late_observation_cannot_satisfy_the_next(self) -> None:
        workers: list[_Worker] = []

        def spawn() -> _Worker:
            item = _Worker()
            item.hold_status = True
            workers.append(item)
            return item

        supervisor = mcp_supervisor.Supervisor(
            spawn=spawn, out_stream=open(os.devnull, "wb"), log=lambda _line: None,
            lease_status_timeout_s=0.05,
        )
        supervisor.start_worker()
        worker = workers[-1]
        first = supervisor._observe_lease_ttl(supervisor._current)
        self.assertEqual(first["lease_ttl_status"], "unknown")
        self.assertTrue(worker.held_status)
        late_id = worker.held_status[0]["id"]
        worker.held_status.clear()
        worker.local_lease_id = None
        worker.hold_status = False
        worker.status_body = {"self": {"state": "none"}, "owner": None}

        def deliver_late() -> None:
            time.sleep(0.01)
            body = {
                "content": [{"type": "text", "text": json.dumps(_status(LEASE, 33.0))}],
                "structuredContent": _status(LEASE, 33.0),
                "isError": False,
            }
            worker.stdout.push((json.dumps({
                "jsonrpc": "2.0", "id": late_id, "result": body,
            }) + "\n").encode())

        threading.Thread(target=deliver_late, daemon=True).start()
        second = supervisor._observe_lease_ttl(supervisor._current)
        self.assertIsNone(second)

    def test_f9_product_spec_mentions_ttl_and_file_matches(self) -> None:
        text = Path(__file__).resolve().parents[2].joinpath("product-spec.md").read_text(encoding="utf-8")
        self.assertIn("file_matches", text)
        self.assertIn("lease_ttl_s", text)
        self.assertIn("no renueva", text)

    def test_f5_wrapped_probe_release_during_status_is_unknown(self) -> None:
        """Real build_app handlers, not a worker that answers the local probe inline.

        The final local probe used to copy lease A and then await result-decoration
        status. Releasing during that await left the copied id in the body, and
        the supervisor published that observation's numeric TTL. The id in the
        probe response has to be read after that await.
        """

        app, runtime = build_app(ServerConfig(
            key="k", port=0, log_sink=lambda _message: None,
            auto_spawn_daemon=False, client_platform="codex",
        ))
        status_body = _status(LEASE, 33.0)
        state = {"tool": None, "local_calls": 0, "status_calls": 0}
        control = SimpleNamespace(active_lease_id=LEASE, active_lease_token="tok", session_status=None)
        runtime._control = control

        async def session_status(*_args, **_kwargs):
            state["status_calls"] += 1
            if state["tool"] == LEASE_LOCAL_TOOL and state["local_calls"] >= 2:
                control.active_lease_id = None
            return dict(status_body)

        control.session_status = session_status

        async def local_probe_fn() -> dict:
            # Same read the registered tool performs. Embedded mode cannot
            # construct ClientRuntime, so the protocol test supplies this body.
            # The result wrappers around it are the real build_app handlers.
            lease_id = control.active_lease_id
            if not isinstance(lease_id, str) or not lease_id:
                lease_id = None
            return {"local_lease_id": lease_id}

        async def status_probe_fn() -> dict:
            status = await session_status()
            if not isinstance(status, dict):
                raise server.ToolError("daemon_unavailable")
            return status

        app._tool_manager.get_tool(LEASE_LOCAL_TOOL).fn = local_probe_fn
        app._tool_manager.get_tool(LEASE_TTL_OBSERVE_TOOL).fn = status_probe_fn
        host_lines: list[dict] = []

        class _Host:
            def write(self, raw: bytes) -> None:
                host_lines.append(json.loads(raw))

            def flush(self) -> None:
                return

        class _RealWorker:
            def __init__(self) -> None:
                self.pid = 5151
                self.stdout = _Lines()
                self._stdin_closed = False

            def write(self, raw: bytes) -> None:
                message = json.loads(raw)
                name = (message.get("params") or {}).get("name")
                if name == LEASE_LOCAL_TOOL:
                    state["local_calls"] += 1
                state["tool"] = name
                try:
                    if name in (LEASE_LOCAL_TOOL, LEASE_TTL_OBSERVE_TOOL):
                        result = asyncio.run(_protocol(app, name, {}))
                        body: dict = {
                            "content": [
                                {"type": "text", "text": getattr(block, "text", "")}
                                for block in result.content
                            ],
                            "isError": bool(result.isError),
                        }
                        if isinstance(result.structuredContent, dict):
                            body["structuredContent"] = result.structuredContent
                    else:
                        body = {"content": [{"type": "text", "text": "{}"}], "isError": False}
                except Exception as exc:
                    body = {
                        "content": [{"type": "text", "text": str(exc)}],
                        "isError": True,
                    }
                finally:
                    state["tool"] = None
                self.stdout.push((json.dumps({
                    "jsonrpc": "2.0", "id": message.get("id"), "result": body,
                }) + "\n").encode())

            def flush(self) -> None:
                return

            def close(self) -> None:
                self._stdin_closed = True

            def poll(self):
                return 0 if self._stdin_closed else None

            def wait(self, timeout=None):
                return 0

            def kill(self) -> None:
                self._stdin_closed = True

            @property
            def stdin(self):
                return self

        supervisor = mcp_supervisor.Supervisor(
            spawn=_RealWorker,
            out_stream=_Host(),
            log=lambda _line: None,
            lease_status_timeout_s=5.0,
        )
        supervisor.start_worker()
        supervisor._reply(9, {"error": "synthetic"}, is_error=True)
        self.assertTrue(host_lines)
        result = host_lines[0]["result"]
        meta = result.get("_meta") or {}
        self.assertEqual(meta.get("lease_ttl_status"), "unknown")
        self.assertIsNone(meta.get("lease_ttl_s"))
        self.assertNotIn("lease_ttl_s=33", json.dumps(result))
        self.assertIsNone(runtime._control.active_lease_id)
        self.assertGreaterEqual(state["local_calls"], 2)
        self.assertGreaterEqual(state["status_calls"], 1)

    def test_f13_local_pack_keeps_observer(self) -> None:
        app, _runtime = build_app(ServerConfig(
            key="k", port=0, log_sink=open(os.devnull, "w"),
            auto_spawn_daemon=False, tool_pack="local8b",
        ))
        outcome = asyncio.run(_protocol(app, LEASE_TTL_OBSERVE_TOOL, {}))
        text = " ".join(getattr(block, "text", "") for block in outcome.content)
        self.assertNotIn("Unknown tool", text)


if __name__ == "__main__":
    unittest.main()
