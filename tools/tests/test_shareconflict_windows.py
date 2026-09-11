"""Real Windows sharing failures, unchanged authority, and public diagnostics.

Only synthetic registrations in TemporaryDirectory are opened. A separate Python
process holds the native handle; no CreateFileW/GetLastError or loader results are
mocked. The transport and runtime constructor are isolated from all live services.
"""
from __future__ import annotations

import asyncio
import copy
import ctypes
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from dayz_mcp import control_client, daemon_policy, host_config, server

# Independent raw Win32 caller, using Microsoft's documented access/share values.
# It never imports dayz_mcp and never writes the opened file.
_HOLDER = r"""
import ctypes, json, os, sys
from ctypes import wintypes
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                              wintypes.HANDLE)
kernel.CreateFileW.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel.CloseHandle.restype = wintypes.BOOL
handle = kernel.CreateFileW(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]),
                            None, 3, 0x80, None)
error = ctypes.get_last_error()
opened = handle != ctypes.c_void_p(-1).value
print(json.dumps({"pid": os.getpid(), "opened": opened,
                  "winerror": 0 if opened else error}), flush=True)
if opened:
    try:
        sys.stdin.readline()
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
"""

# (label, requested access, granted sharing, compatible with the production pin)
_CASES = (
    ("reader_share_read", 0x80000000, 1, True),
    ("reader_share_all", 0x80000000, 7, True),
    ("reader_exclusive", 0x80000000, 0, False),
    ("writer_share_all", 0x40000000, 7, False),
    ("readwrite_share_all", 0xC0000000, 7, False),
    ("delete_share_all", 0x00010000, 7, False),
)


@contextmanager
def _held_by_child(path: Path, access: int, share: int):
    """Handshake on a pipe, not sleep/race timing; release only this child."""
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", _HOLDER, str(path), str(access), str(share)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", cwd=str(path.parent),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    ready = queue.Queue()
    threading.Thread(target=lambda: ready.put(child.stdout.readline()),
                     daemon=True).start()
    try:
        line = ready.get(timeout=15)
        if not line:
            raise AssertionError("holder did not report its native open result")
        yield json.loads(line)
    finally:
        try:
            _, errors = child.communicate(input="release\n", timeout=15)
        except subprocess.TimeoutExpired:
            # Owned test helper only; never a daemon, host CLI, or game process.
            child.kill()
            child.communicate()
            raise AssertionError("owned holder did not release on pipe input")
        if child.returncode != 0 or errors:
            raise AssertionError(f"holder exit={child.returncode}: {errors}")


@unittest.skipUnless(os.name == "nt", "real Windows sharing semantics required")
class ShareConflictWindowsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="shareconflict-m2-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.paths = {"claude": self.root / ".claude.json",
                      "codex": self.root / "config.toml"}
        self.keyfile = self.root / "fixture.key"
        self._write(self.keyfile, b"synthetic-fixture-only")
        self.entries = {}
        for platform in self.paths:
            entry = {"command": str(Path(sys.executable).resolve()), "args": [
                "-m", "dayz_mcp", "--client", "--port", "18765",
                "--keyfile", str(self.keyfile), "--idle-timeout", "12.5",
                "--client-platform", platform,
            ]}
            if platform == "claude":
                entry.update(type="stdio", timeout=host_config.CLAUDE_TIMEOUT_MS)
            else:
                entry["tool_timeout_sec"] = host_config.CODEX_TIMEOUT_SECONDS
            self.entries[platform] = entry
            self._write(self.paths[platform], self._raw(platform, entry))

        # Redirect paths only; resolution, pinning, parsing, and policy are real.
        original_resolve = host_config.resolve_daemon_provenance
        self.resolve = lambda: original_resolve(
            claude_path=self.paths["claude"], codex_path=self.paths["codex"])
        self._patch(host_config, "resolve_daemon_provenance", side_effect=self.resolve)
        self.policy = daemon_policy.load_normal_daemon_policy()
        identity = control_client.ControlIdentity(
            platform="codex", pid=os.getpid(), ppid=os.getppid(),
            started_at_utc="2026-09-08T00:00:00Z", session_id="shareconflict-offline",
        )
        self.client = control_client.ControlClient(policy=self.policy, identity=identity)
        self.send = self._patch(
            self.client._credential_provider, "request_with_refresh",
            return_value=(200, b'{"status":"ok"}'))
        self.spawn = Mock(side_effect=AssertionError("no spawn in this offline test"))

    def _patch(self, obj, name, **kwargs):
        patcher = patch.object(obj, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def _write(self, path, raw):
        path.write_bytes(raw)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(path.stat().st_size, len(raw))

    def _raw(self, platform, entry):
        if platform == "claude":
            return (json.dumps({"mcpServers": {"dayz-mcp": entry}}) + "\n").encode()
        lines = ["[mcp_servers.dayz-mcp]"]
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in entry.items())
        return ("\n".join(lines) + "\n").encode()

    def _status(self):
        return asyncio.run(self.client.session_status())

    def _assert_rejected(self, cause):
        with self.assertRaises(control_client.ControlClientError) as caught:
            self._status()
        error = caught.exception
        self.assertEqual(error.code, "client_policy_untrusted_open_new_session")
        self.assertEqual(error.policy_cause, cause)
        self.assertEqual(error.request_stage, "pre_request")
        self.assertEqual(error.http_bytes_sent, 0)
        self.send.assert_not_called()
        return error

    def _mcp_response(self):
        """Run the real registered MCP handler in memory; never start a server."""
        from mcp import types
        client, spawn = self.client, self.spawn

        class OfflineRuntime(server.ClientRuntime):
            def __init__(self, config):
                self.config = config
                self._control = client
                self.tool_lock = asyncio.Lock()
                self._ensure_daemon = spawn

        async def invoke():
            with patch.object(server, "ClientRuntime", OfflineRuntime):
                app, _ = server.build_app(server.ServerConfig(
                    mode="client", port=18765, keyfile=str(self.keyfile),
                    auto_spawn_daemon=False))
                request = types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(name="session_status", arguments={}),
                )
                response = await app._mcp_server.request_handlers[types.CallToolRequest](request)
                # The actual MCP wire-shaped payload, not exception introspection.
                return response.root.model_dump(mode="json")

        return asyncio.run(invoke())

    def _assert_public_rejection(self, cause):
        response = self._mcp_response()
        self.assertTrue(response["isError"])
        text = "\n".join(item["text"] for item in response["content"]
                         if item["type"] == "text")
        self.assertIn("client_policy_untrusted_open_new_session", text)
        self.assertIn(f"policy_cause={cause}", text)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("synthetic-fixture-only", text)
        self.send.assert_not_called()
        self.spawn.assert_not_called()
        return text

    def test_real_open_matrix_before_pin_and_after_release(self):
        expected = self.resolve()
        for platform, path in self.paths.items():
            before = path.read_bytes()
            for label, access, share, compatible in _CASES:
                with self.subTest(platform=platform, case=label):
                    self.send.reset_mock()
                    with _held_by_child(path, access, share) as native:
                        self.assertTrue(native["opened"])
                        self.assertNotEqual(native["pid"], os.getpid())
                        if compatible:
                            self.assertEqual(self.resolve(), expected)
                            self.assertEqual(self._status(), {"status": "ok"})
                            self.send.assert_called_once()
                            error_number = 0
                        else:
                            with self.assertRaises(host_config.HostConfigError) as caught:
                                self.resolve()
                            self.assertEqual(str(caught.exception), "daemon_provenance_conflict")
                            self.assertEqual(caught.exception.winerror, 32)
                            self._assert_rejected("HostConfigError:32")
                            error_number = caught.exception.winerror
                        print(f"WIN32 before_pin {platform} {label} "
                              f"holder_pid={native['pid']} gate_winerror={error_number}",
                              flush=True)
                    # No file bytes/registration changed; closing the handle is the
                    # only intervention. This is a new request, not a gate retry.
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(path.stat().st_size, len(before))
                    self.send.reset_mock()
                    self.assertEqual(self.resolve(), expected)
                    self.assertEqual(self._status(), {"status": "ok"})
                    self.send.assert_called_once()

    def test_real_open_matrix_pin_first(self):
        for platform, path in self.paths.items():
            for label, access, share, compatible in _CASES:
                with self.subTest(platform=platform, case=label):
                    pinned = host_config._PinnedConfigFile(path)
                    try:
                        with _held_by_child(path, access, share) as native:
                            self.assertEqual(native["opened"], compatible)
                            self.assertEqual(native["winerror"], 0 if compatible else 32)
                            print(f"WIN32 pin_first {platform} {label} "
                                  f"holder_pid={native['pid']} other_winerror={native['winerror']}",
                                  flush=True)
                    finally:
                        pinned.close()

    def test_real_sharing_cause_reaches_public_mcp_error(self):
        for platform, path in self.paths.items():
            with self.subTest(platform=platform), _held_by_child(
                    path, 0x40000000, 7) as native:
                self.assertTrue(native["opened"])
                text = self._assert_public_rejection("HostConfigError:32")
                print(f"MCP_REAL_SHARE {platform}: {text}", flush=True)

    def test_changed_registration_has_distinct_public_cause(self):
        for platform, path in self.paths.items():
            with self.subTest(platform=platform):
                before = path.read_bytes()
                changed = copy.deepcopy(self.entries[platform])
                args = changed["args"]
                args[args.index("--idle-timeout") + 1] = "13.0"
                self._write(path, self._raw(platform, changed))
                try:
                    self._assert_rejected("HostConfigError:daemon_provenance_conflict")
                    text = self._assert_public_rejection(
                        "HostConfigError:daemon_provenance_conflict")
                    self.assertNotIn("policy_cause=HostConfigError:32", text)
                    print(f"MCP_CHANGED_REGISTRATION {platform}: {text}", flush=True)
                finally:
                    self._write(path, before)

    def test_unrelated_temp_handle_and_completed_rename_do_not_reject(self):
        expected = self.resolve()
        for platform, path in self.paths.items():
            candidate = path.with_name(path.name + ".tmp.synthetic")
            before = path.read_bytes()
            self._write(candidate, before + b"\n")
            with _held_by_child(candidate, 0x40000000, 0) as native:
                self.assertTrue(native["opened"])
                self.assertEqual(self.resolve(), expected)
                self.assertEqual(self._status(), {"status": "ok"})
                print(f"TEMP_ONLY {platform} exclusive_writer: accepted", flush=True)
            candidate.replace(path)
            self.assertEqual(path.read_bytes(), before + b"\n")
            self.assertEqual(path.stat().st_size, len(before) + 1)
            self.assertEqual(self.resolve(), expected)
            print(f"RENAME_COMPLETED {platform}: accepted", flush=True)

    def test_raw_message_never_reaches_public_response(self):
        cases = [
            (PermissionError(13, "PRIVATE_MESSAGE", str(self.root)),
             "PermissionError:13"),
            (RuntimeError("PRIVATE_MESSAGE C:\\private\\config.toml\nsecret=fixture"),
             "RuntimeError"),
            (host_config.HostConfigError("PRIVATE_MESSAGE /private", winerror=32),
             "HostConfigError:32"),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__), patch.object(
                    host_config, "resolve_daemon_provenance", side_effect=error):
                text = self._assert_public_rejection(expected)
                self.assertNotIn("PRIVATE_MESSAGE", text)
                self.assertNotIn("private", text)
                self.assertNotIn("secret=fixture", text)


if __name__ == "__main__":
    unittest.main()
