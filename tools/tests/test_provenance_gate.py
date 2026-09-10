"""L6: semantic provenance, fail-closed controls, and safe cause metadata.

The within-resolution handles are explicit race doubles: Windows' production
FILE_SHARE_READ pins forbid that race. The between-request case uses real handles.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from dayz_mcp import control_client, daemon_policy, host_config


class ProvenanceGateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="provenance-l6-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.paths = {"claude": self.root / ".claude.json",
                      "codex": self.root / "config.toml"}
        self.keyfile = self.root / "daemon.key"
        self.other_keyfile = self.root / "other.key"
        self.other_command = self.root / "other.exe"
        for path in (self.keyfile, self.other_keyfile, self.other_command):
            self._write(path, b"test-fixture-only")
        self.entries = {}
        for platform in self.paths:
            entry = {"command": str(Path(sys.executable).resolve()), "args": [
                "-m", "dayz_mcp", "--client", "--port", "18765",
                "--keyfile", str(self.keyfile), "--idle-timeout", "12.5",
                "--client-platform", platform]}
            if platform == "claude":
                entry.update(type="stdio", timeout=host_config.CLAUDE_TIMEOUT_MS)
            else:
                entry["tool_timeout_sec"] = host_config.CODEX_TIMEOUT_SECONDS
            self.entries[platform] = entry
        self._reset()
        resolve = host_config.resolve_daemon_provenance
        self.addCleanup(patch.stopall)
        patch.object(host_config, "resolve_daemon_provenance", side_effect=lambda: resolve(
            claude_path=self.paths["claude"], codex_path=self.paths["codex"])).start()
        self.policy = daemon_policy.load_normal_daemon_policy()
        identity = control_client.ControlIdentity(
            platform="codex", pid=os.getpid(), ppid=os.getppid(),
            started_at_utc="2026-09-08T00:00:00Z", session_id="provenance-l6")
        self.client = control_client.ControlClient(policy=self.policy, identity=identity)
        # The public ControlClient method runs; no sockets or daemon are used.
        self.send = patch.object(self.client._credential_provider, "request_with_refresh",
                                 return_value=(200, b'{"status":"ok"}')).start()

    def _write(self, path, raw):
        path.write_bytes(raw)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(path.stat().st_size, len(raw))

    def _raw(self, platform, entry, *, unrelated=False):
        if platform == "claude":
            obj = {"mcpServers": {"dayz-mcp": entry}}
            if unrelated:
                obj["uiState"] = {"lastOpened": "changed"}
                obj["mcpServers"]["unrelated"] = {"command": "unrelated-command"}
            return (json.dumps(obj, indent=2 if unrelated else None) + "\n").encode()
        prefix = 'model = "irrelevant-change"\n# rewritten\n' if unrelated else ''
        lines = [prefix + "[mcp_servers.dayz-mcp]"]
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in entry.items())
        if unrelated:
            lines.extend(['[mcp_servers.unrelated]', 'command = "unrelated-command"'])
        return ("\n".join(lines) + "\n").encode()

    def _reset(self):
        for platform, path in self.paths.items():
            self._write(path, self._raw(platform, self.entries[platform]))

    def _replace(self, platform, raw):
        path = self.paths[platform]
        old = path.stat()
        candidate = path.with_suffix(path.suffix + ".replacement")
        self._write(candidate, raw)
        candidate.replace(path)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(path.stat().st_size, len(raw))
        self.assertNotEqual((old.st_dev, old.st_ino),
                            (path.stat().st_dev, path.stat().st_ino))

    def _status(self):
        return asyncio.run(self.client.session_status())

    @contextmanager
    def _race(self, changes, *, reread=False):
        """Isolate content drift on one file; Windows pins forbid real concurrent writes."""
        owner = self
        opened = []
        class SnapshotHandle:
            def __init__(self, path):
                self.path = Path(path)
                self.raw = self.path.read_bytes()
                stat = self.path.stat()
                self.file_id = (stat.st_dev, stat.st_ino)
                self.closed = False
                opened.append(self)
            def read(self):
                return self.path.read_bytes() if reread else self.raw
            def identity(self):
                return self.file_id
            def close(self):
                self.closed = True
        native = host_config._local_native_executable()
        def rewrite_after_parse():
            for platform, raw in changes.items():
                path = owner.paths[platform]
                before = path.stat()
                owner._write(path, raw)
                after = path.stat()
                owner.assertEqual((before.st_dev, before.st_ino),
                                  (after.st_dev, after.st_ino))
            return native
        with patch.object(host_config, "_PinnedConfigFile", SnapshotHandle), patch.object(
            host_config, "_local_native_executable", side_effect=rewrite_after_parse
        ):
            try:
                yield
            finally:
                self.assertTrue(opened)
                self.assertTrue(all(handle.closed for handle in opened))

    def test_t1_same_registration_survives_whole_file_replacement(self):
        # This already passes on the old code: snapshots do not span requests.
        for platform in self.paths:
            self._replace(platform, self._raw(platform, self.entries[platform], unrelated=True))
        self.assertEqual(self._status(), {"status": "ok"})
        self.send.assert_called_once()
        # Within a resolution, formatting may drift but file identity must stay pinned.
        for platform in self.paths:
            for reread in (False, True):
                with self.subTest(platform=platform, phase="reread" if reread else "reopen"):
                    self._reset()
                    self.send.reset_mock()
                    raw = self._raw(platform, self.entries[platform], unrelated=True)
                    with self._race({platform: raw}, reread=reread):
                        self.assertEqual(self._status(), {"status": "ok"})
                    self.send.assert_called_once()

    def test_t2_changed_registration_is_rejected_without_http(self):
        for platform in self.paths:
            for mutation in ("command", "keyfile", "timeout", "idle_timeout", "missing",
                             "unknown_field", "malformed", "bool_timeout"):
                entry = copy.deepcopy(self.entries[platform])
                timeout_key = "timeout" if platform == "claude" else "tool_timeout_sec"
                if mutation == "command":
                    entry["command"] = str(self.other_command)
                elif mutation == "keyfile":
                    entry["args"][entry["args"].index("--keyfile") + 1] = str(self.other_keyfile)
                elif mutation == "timeout":
                    entry[timeout_key] -= 1
                elif mutation == "idle_timeout":
                    entry["args"][entry["args"].index("--idle-timeout") + 1] = "13.0"
                elif mutation == "unknown_field":
                    entry["untrusted"] = "extra"
                elif mutation == "bool_timeout":
                    entry[timeout_key] = True
                raw = self._raw(platform, entry)
                if mutation == "missing":
                    raw = b'{}' if platform == "claude" else b'# removed\n'
                elif mutation == "malformed":
                    raw = b'\xff invalid file'
                for phase in ("between_requests", "reopen", "reread"):
                    with self.subTest(platform=platform, mutation=mutation, phase=phase):
                        self._reset()
                        self.send.reset_mock()
                        with self.assertRaises(control_client.ControlClientError) as caught:
                            if phase == "between_requests":
                                self._replace(platform, raw)
                                self._status()
                            else:
                                with self._race({platform: raw}, reread=phase == "reread"):
                                    self._status()
                        self.assertEqual(caught.exception.code,
                                         "client_policy_untrusted_open_new_session")
                        self.assertEqual(caught.exception.request_stage, "pre_request")
                        self.assertEqual(caught.exception.http_bytes_sent, 0)
                        self.send.assert_not_called()
        # Both hosts may agree on a different valid key: policy drift still rejects it.
        self._reset()
        for platform in self.paths:
            entry = copy.deepcopy(self.entries[platform])
            entry["args"][entry["args"].index("--keyfile") + 1] = str(self.other_keyfile)
            self._replace(platform, self._raw(platform, entry))
        with self.assertRaises(control_client.ControlClientError):
            self._status()
        self.send.assert_not_called()

    def test_t3_rejection_cause_is_separate_safe_and_actionable(self):
        # Real loader paths: missing registration versus an I/O failure.
        self.paths["codex"].unlink()
        with self.assertRaises(control_client.ControlClientError) as caught:
            self._status()
        self.assertEqual(caught.exception.policy_cause,
                         "HostConfigError:daemon_provenance_incomplete")
        self._reset()
        if os.name == "nt":
            for api, failure, number in (
                ("CreateFileW", host_config._INVALID_HANDLE_VALUE, 32),
                ("SetFilePointerEx", 0, 5),
                ("ReadFile", 0, 5),
            ):
                with self.subTest(native_failure=api), patch.object(
                    host_config._kernel32, api, return_value=failure
                ), patch.object(host_config.ctypes, "get_last_error", return_value=number):
                    with self.assertRaises(control_client.ControlClientError) as caught:
                        self._status()
                    self.assertEqual(caught.exception.code,
                                     "client_policy_untrusted_open_new_session")
                    self.assertEqual(caught.exception.policy_cause, f"HostConfigError:{number}")
                    self.assertEqual(caught.exception.http_bytes_sent, 0)
        cases = [
            (host_config.HostConfigError("daemon_provenance_conflict"),
             "HostConfigError:daemon_provenance_conflict"),
            (host_config.HostConfigError("daemon_provenance_incomplete"),
             "HostConfigError:daemon_provenance_incomplete"),
            (ValueError("daemon_policy_drift"), "ValueError:daemon_policy_drift"),
            (PermissionError(13, "denied", str(self.keyfile)), "PermissionError:13"),
            (RuntimeError("sensitive/path and credentials"), "RuntimeError"),
        ]
        causes = []
        for exception, expected in cases:
            with self.subTest(cause=expected):
                with patch.object(host_config, "resolve_daemon_provenance", side_effect=exception):
                    with self.assertRaises(control_client.ControlClientError) as caught:
                        self._status()
                error = caught.exception
                self.assertEqual(error.code, "client_policy_untrusted_open_new_session")
                self.assertEqual(error.policy_cause, expected)
                self.assertEqual(error.request_stage, "pre_request")
                self.assertEqual(error.http_bytes_sent, 0)
                self.assertIn("host/operator", error.hint)
                self.assertIn("cannot open", error.hint)
                self.assertNotIn(str(self.keyfile), str(error))
                self.assertNotIn("sensitive", str(error))
                causes.append(error.policy_cause)
        self.assertEqual(len(set(causes)), len(cases))
        self.send.assert_not_called()



class SupervisedFlagRegistrationTests(unittest.TestCase):
    """--supervised may be registered, and widening that allowlist opened nothing else.

    The registrable options are a closed set (host_config._scan_raw_options). Adding a
    flag to it is the step that has to reach every client BEFORE any registration
    carries it: a client with the older list rejects the whole file with
    daemon_provenance_conflict, which is how a machine loses its MCP everywhere.
    Measured on 2026-09-10 by doing exactly that and reverting.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="supervised-reg-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.paths = {"claude": self.root / ".claude.json",
                      "codex": self.root / "config.toml"}
        self.keyfile = self.root / "daemon.key"
        self.keyfile.write_bytes(b"test-fixture-only")

    def _entry(self, platform, extra=()):
        entry = {"command": str(Path(sys.executable).resolve()),
                 "args": ["-m", "dayz_mcp", "--client", *extra, "--port", "18765",
                          "--keyfile", str(self.keyfile), "--idle-timeout", "12.5",
                          "--client-platform", platform]}
        if platform == "claude":
            entry.update(type="stdio", timeout=host_config.CLAUDE_TIMEOUT_MS)
        else:
            entry["tool_timeout_sec"] = host_config.CODEX_TIMEOUT_SECONDS
        return entry

    def _write_pair(self, claude_extra=(), codex_extra=()):
        claude = {"mcpServers": {"dayz-mcp": self._entry("claude", claude_extra)}}
        self.paths["claude"].write_bytes((json.dumps(claude) + chr(10)).encode())
        entry = self._entry("codex", codex_extra)
        lines = ["[mcp_servers.dayz-mcp]",
                 f"command = {json.dumps(entry['command'])}",
                 f"args = {json.dumps(entry['args'])}",
                 f"tool_timeout_sec = {host_config.CODEX_TIMEOUT_SECONDS}", ""]
        self.paths["codex"].write_bytes(chr(10).join(lines).encode())

    def _resolve(self):
        return host_config.resolve_daemon_provenance(
            claude_path=self.paths["claude"], codex_path=self.paths["codex"]
        )

    def test_the_pair_without_the_flag_is_the_control_and_resolves(self):
        self._write_pair()
        self.assertEqual(self._resolve().port, 18765)

    def test_supervised_in_both_registrations_resolves(self):
        self._write_pair(("--supervised",), ("--supervised",))
        self.assertEqual(self._resolve().port, 18765)

    def test_supervised_may_differ_between_the_two_registrations(self):
        # Deliberate, and checked because it is easy to "fix" by mistake: --supervised
        # is a client-side concern that never reaches the daemon's argv, so it is not
        # part of _ClientRegistration and the two files may disagree about it. That is
        # what makes a per-platform rollout possible -- Claude supervised, Codex not --
        # while both still pin the same daemon.
        self._write_pair(("--supervised",), ())
        self.assertEqual(self._resolve().port, 18765)
        self._write_pair((), ("--supervised",))
        self.assertEqual(self._resolve().port, 18765)

    def test_a_real_daemon_difference_between_the_two_still_conflicts(self):
        # The control for the test above: what DOES reach the daemon still has to match,
        # or the widening would have quietly turned the pair check into a formality.
        other = self.root / "other.key"
        other.write_bytes(b"test-fixture-only")
        claude = {"mcpServers": {"dayz-mcp": self._entry("claude")}}
        self.paths["claude"].write_bytes((json.dumps(claude) + chr(10)).encode())
        entry = self._entry("codex")
        entry["args"][entry["args"].index(str(self.keyfile))] = str(other)
        lines = ["[mcp_servers.dayz-mcp]",
                 f"command = {json.dumps(entry['command'])}",
                 f"args = {json.dumps(entry['args'])}",
                 f"tool_timeout_sec = {host_config.CODEX_TIMEOUT_SECONDS}", ""]
        self.paths["codex"].write_bytes(chr(10).join(lines).encode())
        with self.assertRaises(host_config.HostConfigError) as caught:
            self._resolve()
        self.assertEqual(str(caught.exception), "daemon_provenance_conflict")

    def test_the_allowlist_did_not_become_open(self):
        # LL-343: widening a filter opens the symmetric false negative. An unknown flag
        # must still be refused, or the registration stops being a closed contract.
        self._write_pair(("--totally-made-up",), ("--totally-made-up",))
        with self.assertRaises(host_config.HostConfigError):
            self._resolve()

    def test_the_flag_still_cannot_be_repeated(self):
        self._write_pair(("--supervised", "--supervised"), ("--supervised", "--supervised"))
        with self.assertRaises(host_config.HostConfigError):
            self._resolve()

if __name__ == "__main__":
    unittest.main()
