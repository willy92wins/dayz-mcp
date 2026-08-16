from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import tomllib
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dayz_mcp.host_config import (  # noqa: E402
    CLAUDE_TIMEOUT_MS,
    CODEX_TIMEOUT_SECONDS,
    HostConfigCrash,
    HostConfigError,
    apply_host_timeouts,
    build_claude_target,
    build_codex_target,
)
import dayz_mcp.server as server  # noqa: E402
import dayz_mcp.host_config as host_config  # noqa: E402


class HostConfigRecoveryClassifierTest(unittest.TestCase):
    def test_finite_host_watchdogs_remain_after_local_wait_ceiling_removal(self) -> None:
        self.assertGreater(CLAUDE_TIMEOUT_MS, 0)
        self.assertGreater(CODEX_TIMEOUT_SECONDS, 0)
        self.assertFalse(hasattr(server, "MAX_SESSION_ACQUIRE_WAIT_S"))

    def test_overwrite_families_cover_short_equal_and_long_targets_only(self) -> None:
        for original, target in (
            (b"abcdef", b"XYZ"),
            (b"abcdef", b"UVWXYZ"),
            (b"abc", b"123456"),
        ):
            with self.subTest(original=original, target=target):
                shared = min(len(original), len(target))
                own = {
                    target[:offset] + original[offset:]
                    for offset in range(shared + 1)
                }
                own.update(
                    target[:offset]
                    for offset in range(len(original) + 1, len(target) + 1)
                )
                for current in own:
                    self.assertTrue(
                        host_config._is_own_write(current, original, target)
                    )
                self.assertFalse(
                    host_config._is_own_write(b"external", original, target)
                )

    def test_restore_progress_uses_the_durable_source_not_the_target(self) -> None:
        original = b"restored-original"
        source = b"transaction-source"
        shared = min(len(original), len(source))
        for offset in range(shared + 1):
            current = original[:offset] + source[offset:]
            self.assertTrue(
                host_config._is_restore_progress(current, original, source)
            )
        self.assertFalse(
            host_config._is_restore_progress(b"external", original, source)
        )


class DaemonProvenanceConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.claude_path = self.root / ".claude.json"
        self.codex_path = self.root / "config.toml"
        self.keyfile = (self.root / "daemon.key").resolve()
        self.keyfile.write_text("fixture-key", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _args(
        self,
        platform: str,
        *,
        idle_timeout: str = "12.5",
        auto_spawn: bool = True,
    ) -> list[str]:
        args = [
            "-m",
            "dayz_mcp",
            "--client",
            "--keyfile",
            str(self.keyfile),
            "--port",
            "18765",
            "--require-version",
            "--idle-timeout",
            idle_timeout,
            "--client-platform",
            platform,
        ]
        if not auto_spawn:
            args.append("--no-daemon-autospawn")
        return args

    def _write_claude(
        self,
        *,
        idle_timeout: str = "12.5",
        args: list[str] | None = None,
        entry_updates: dict[str, object] | None = None,
        include_entry: bool = True,
    ) -> None:
        servers: dict[str, object] = {}
        if include_entry:
            entry: dict[str, object] = {
                "type": "stdio",
                "command": str(Path(sys.executable).resolve()),
                "args": args or self._args("claude", idle_timeout=idle_timeout),
                "timeout": CLAUDE_TIMEOUT_MS,
            }
            entry.update(entry_updates or {})
            servers["dayz-mcp"] = entry
        self.claude_path.write_text(
            json.dumps(
                {
                    "mcpServers": servers
                }
            ),
            encoding="utf-8",
        )

    def _write_codex(
        self,
        *,
        idle_timeout: str = "12.5",
        auto_spawn: bool = True,
        args: list[str] | None = None,
        extra_lines: tuple[str, ...] = (),
        include_entry: bool = True,
    ) -> None:
        if not include_entry:
            self.codex_path.write_text("[mcp_servers.other]\ncommand = \"other\"\n", encoding="utf-8")
            return
        lines = [
            "[mcp_servers.dayz-mcp]",
            f"command = {json.dumps(str(Path(sys.executable).resolve()))}",
            f"args = {json.dumps(args or self._args('codex', idle_timeout=idle_timeout, auto_spawn=auto_spawn))}",
            f"tool_timeout_sec = {CODEX_TIMEOUT_SECONDS}",
            *extra_lines,
        ]
        self.codex_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _resolve(self):
        return host_config.resolve_daemon_provenance(
            claude_path=self.claude_path,
            codex_path=self.codex_path,
        )

    def test_zero_or_one_registration_fails_closed_without_defaults(self) -> None:
        with self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_unavailable$"
        ):
            self._resolve()

        self._write_claude()
        with self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_incomplete$"
        ):
            self._resolve()

    def test_zero_or_one_real_entries_ignore_merely_existing_config_files(self) -> None:
        self._write_claude(include_entry=False)
        self._write_codex(include_entry=False)
        with self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_unavailable$"
        ):
            self._resolve()

        self._write_claude()
        with self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_incomplete$"
        ):
            self._resolve()

    def test_two_registrations_with_policy_drift_are_conflict(self) -> None:
        self._write_claude()
        self._write_codex(idle_timeout="13.0")

        with self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_conflict$"
        ) as raised:
            self._resolve()
        self.assertNotIn("fixture-key", str(raised.exception))

    def test_two_registrations_with_client_runtime_drift_are_conflict(self) -> None:
        self._write_claude()
        self._write_codex(auto_spawn=False)

        with self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_conflict$"
        ):
            self._resolve()

    def test_two_equivalent_registrations_build_exact_daemon_provenance(self) -> None:
        from dayz_mcp import daemon

        self._write_claude()
        self._write_codex()

        provenance = self._resolve()

        self.assertEqual(
            getattr(provenance, "launch_executable", None),
            str(Path(sys.executable).resolve()),
        )
        self.assertEqual(
            getattr(provenance, "native_executable", None),
            host_config._local_native_executable(),
        )
        self.assertEqual(provenance.port, 18765)
        self.assertEqual(provenance.keyfile, str(self.keyfile))
        self.assertEqual(
            list(provenance.argv),
            [
                str(Path(sys.executable).resolve()),
                "-m",
                "dayz_mcp",
                "--daemon",
                "--port",
                "18765",
                "--keyfile",
                str(self.keyfile),
                "--require-version",
                "--idle-timeout",
                "12.5",
            ],
        )
        self.assertEqual(provenance.cwd, daemon.daemon_runtime_cwd())
        self.assertTrue(provenance.auto_spawn_daemon)

    def test_consensual_no_autospawn_is_published_in_provenance(self) -> None:
        self._write_claude(args=self._args("claude", auto_spawn=False))
        self._write_codex(auto_spawn=False)

        with mock.patch.object(
            host_config,
            "_local_native_executable",
            return_value=str(Path(sys.executable).resolve()),
        ):
            provenance = self._resolve()

        self.assertFalse(getattr(provenance, "auto_spawn_daemon", True))

    def test_launch_and_native_executables_are_distinct_authorities(self) -> None:
        native = self.root / "native-python-image.exe"
        native.write_bytes(b"native-image-fixture")
        self._write_claude()
        self._write_codex()

        with mock.patch.object(
            host_config,
            "_local_native_executable",
            return_value=str(native.resolve()),
            create=True,
        ):
            provenance = self._resolve()

        self.assertEqual(
            getattr(provenance, "launch_executable", None),
            str(Path(sys.executable).resolve()),
        )
        self.assertEqual(
            getattr(provenance, "native_executable", None), str(native.resolve())
        )
        self.assertEqual(provenance.argv[0], str(Path(sys.executable).resolve()))

    def test_registration_command_must_equal_the_local_canonical_launcher(self) -> None:
        foreign_launcher = self.root / "foreign-python.exe"
        foreign_launcher.write_bytes(b"foreign-launcher")
        self._write_claude(entry_updates={"command": str(foreign_launcher.resolve())})
        self._write_codex()

        with self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_conflict$"
        ):
            self._resolve()

    def test_raw_host_schema_is_closed_and_stdio_is_mandatory(self) -> None:
        forbidden_claude = (
            {"env": {"LEAK": "1"}},
            {"cwd": str(self.root)},
            {"url": "http://127.0.0.1:18765"},
            {"transport": "stdio"},
            {"unknown": True},
            {"type": "http"},
        )
        for update in forbidden_claude:
            with self.subTest(host="claude", update=update):
                self._write_claude(entry_updates=update)
                self._write_codex()
                with self.assertRaisesRegex(
                    host_config.HostConfigError, "^daemon_provenance_conflict$"
                ):
                    self._resolve()

    def test_claude_default_empty_env_is_accepted_but_cannot_carry_values(self) -> None:
        self._write_claude(entry_updates={"env": {}})
        self._write_codex()

        provenance = self._resolve()

        self.assertEqual(provenance.port, 18765)
        self.assertEqual(provenance.keyfile, str(self.keyfile))

        self._write_claude(entry_updates={"env": {"LEAK": "1"}})
        with self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_conflict$"
        ):
            self._resolve()

    def test_timeout_fields_require_exact_integers_not_numeric_equality(self) -> None:
        self._write_claude(entry_updates={"timeout": float(CLAUDE_TIMEOUT_MS)})
        self._write_codex()
        with self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_conflict$"
        ):
            self._resolve()

        self._write_claude()
        self._write_codex()
        self.codex_path.write_text(
            self.codex_path.read_text(encoding="utf-8").replace(
                f"tool_timeout_sec = {CODEX_TIMEOUT_SECONDS}",
                f"tool_timeout_sec = {float(CODEX_TIMEOUT_SECONDS)}",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_conflict$"
        ):
            self._resolve()

    def test_mixed_equals_duplicates_and_boolean_equals_are_rejected(self) -> None:
        for duplicate in (
            ["--port=18765"],
            ["--task-label", "fixture", "--task-label=fixture"],
            ["--require-version=true"],
        ):
            with self.subTest(duplicate=duplicate):
                claude_args = self._args("claude") + duplicate
                self._write_claude(args=claude_args)
                self._write_codex()
                with self.assertRaisesRegex(
                    host_config.HostConfigError, "^daemon_provenance_conflict$"
                ):
                    self._resolve()

    def test_each_optional_absence_has_an_enumerated_default(self) -> None:
        args = self._args("claude")
        args.remove("--require-version")
        entry = {
            "type": "stdio",
            "command": str(Path(sys.executable).resolve()),
            "args": args,
            "timeout": CLAUDE_TIMEOUT_MS,
        }
        registration = host_config._registration_from_entry(entry, platform="claude")
        self.assertIsNone(registration.expected_game_version)
        self.assertFalse(registration.require_version)
        self.assertFalse(registration.enable_exec_enforce)
        self.assertIsNone(registration.exec_allowlist)
        self.assertTrue(registration.auto_spawn_daemon)

        for line in (
            'env = { LEAK = "1" }',
            f"cwd = {json.dumps(str(self.root))}",
            'url = "http://127.0.0.1:18765"',
            'transport = "stdio"',
            "unknown = true",
        ):
            with self.subTest(host="codex", line=line):
                self._write_claude()
                self._write_codex(extra_lines=(line,))
                with self.assertRaisesRegex(
                    host_config.HostConfigError, "^daemon_provenance_conflict$"
                ):
                    self._resolve()

    def test_nonstandard_json_constants_and_ambiguous_toml_shapes_fail_closed(self) -> None:
        self._write_claude()
        raw = self.claude_path.read_text(encoding="utf-8")
        self.claude_path.write_text(
            raw.replace(str(CLAUDE_TIMEOUT_MS), "NaN"), encoding="utf-8"
        )
        self._write_codex()
        with self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_conflict$"
        ):
            self._resolve()

        self._write_claude()
        self._write_codex(extra_lines=("[mcp_servers.dayz-mcp.env]", 'LEAK = "1"'))
        with self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_conflict$"
        ):
            self._resolve()

    def test_required_raw_options_are_explicit_and_unique(self) -> None:
        required = {
            "--client": 0,
            "--port": 1,
            "--keyfile": 1,
            "--idle-timeout": 1,
            "--client-platform": 1,
        }
        for option, width in required.items():
            with self.subTest(option=option, defect="missing"):
                claude_args = self._args("claude")
                index = claude_args.index(option)
                del claude_args[index : index + width + 1]
                self._write_claude(args=claude_args)
                self._write_codex()
                with self.assertRaisesRegex(
                    host_config.HostConfigError, "^daemon_provenance_conflict$"
                ):
                    self._resolve()

            with self.subTest(option=option, defect="duplicate"):
                claude_args = self._args("claude")
                index = claude_args.index(option)
                duplicate = claude_args[index : index + width + 1]
                claude_args.extend(duplicate)
                self._write_claude(args=claude_args)
                self._write_codex()
                with self.assertRaisesRegex(
                    host_config.HostConfigError, "^daemon_provenance_conflict$"
                ):
                    self._resolve()

    def test_optional_raw_options_are_at_most_once_and_absence_is_enumerated(self) -> None:
        optional_samples = (
            ("--expected-game-version", "1.28"),
            ("--require-version", None),
            ("--enable-exec-enforce", None),
            ("--exec-allowlist", str(self.keyfile)),
            ("--no-daemon-autospawn", None),
            ("--task-label", "fixture"),
        )
        for option, value in optional_samples:
            with self.subTest(option=option):
                claude_args = self._args("claude")
                addition = [option] + ([] if value is None else [value])
                claude_args.extend(addition)
                claude_args.extend(addition)
                self._write_claude(args=claude_args)
                self._write_codex()
                with self.assertRaisesRegex(
                    host_config.HostConfigError, "^daemon_provenance_conflict$"
                ):
                    self._resolve()

        no_optional_claude = self._args("claude")
        no_optional_codex = self._args("codex")
        for args in (no_optional_claude, no_optional_codex):
            args.remove("--require-version")
        self._write_claude(args=no_optional_claude)
        self._write_codex(args=no_optional_codex)
        provenance = self._resolve()
        self.assertNotIn("--require-version", provenance.argv)
        self.assertNotIn("--expected-game-version", provenance.argv)
        self.assertNotIn("--enable-exec-enforce", provenance.argv)
        self.assertNotIn("--exec-allowlist", provenance.argv)

    def test_ancestor_rebind_reopen_rejects_new_path_identity(self) -> None:
        self._write_claude()
        self._write_codex()
        originals: dict[str, object] = {}
        reopen_paths: list[Path] = []

        class FakePinned:
            def __init__(
                self,
                path: Path,
                identity: tuple[object, ...],
                raw: bytes,
            ) -> None:
                self.path = Path(path)
                self._identity = identity
                self._raw = raw
                self.closed = False

            def read(self) -> bytes:
                return self._raw

            def identity(self) -> tuple[object, ...]:
                return self._identity

            def close(self) -> None:
                self.closed = True

        for platform, path in (
            ("claude", self.claude_path),
            ("codex", self.codex_path),
        ):
            originals[platform] = FakePinned(
                path,
                ("original", platform),
                path.read_bytes(),
            )

        @contextmanager
        def pinned(_paths: dict[str, Path]):
            try:
                yield originals
            finally:
                for handle in originals.values():
                    handle.close()

        def rebound(path: Path) -> FakePinned:
            reopen_paths.append(Path(path))
            return FakePinned(
                Path(path),
                ("rebound", str(path)),
                Path(path).read_bytes(),
            )

        with (
            mock.patch.object(host_config, "_open_pinned_configs", side_effect=pinned),
            mock.patch.object(host_config, "_PinnedConfigFile", side_effect=rebound),
            self.assertRaisesRegex(
                host_config.HostConfigError, "^daemon_provenance_conflict$"
            ),
        ):
            self._resolve()

        self.assertEqual(reopen_paths, [self.claude_path])
        self.assertTrue(all(handle.closed for handle in originals.values()))

    @unittest.skipUnless(os.name == "nt", "Windows handle pinning required")
    def test_both_configs_are_pinned_before_first_parse(self) -> None:
        self._write_claude()
        self._write_codex()
        replacement = self.root / "codex-replacement.toml"
        replacement.write_bytes(self.codex_path.read_bytes())
        parse_started = threading.Event()
        replacement_finished = threading.Event()
        outcome: list[BaseException | str] = []

        def replace_codex() -> None:
            self.assertTrue(parse_started.wait(2.0))
            try:
                os.replace(replacement, self.codex_path)
                outcome.append("replaced")
            except OSError as exc:
                outcome.append(exc)
            finally:
                replacement_finished.set()

        original = host_config._registration_from_entry

        def synchronized_parse(entry: object, *, platform: str):
            if platform == "claude" and not parse_started.is_set():
                parse_started.set()
                self.assertTrue(replacement_finished.wait(2.0))
            return original(entry, platform=platform)

        thread = threading.Thread(target=replace_codex, daemon=True)
        thread.start()
        with mock.patch.object(
            host_config, "_registration_from_entry", side_effect=synchronized_parse
        ):
            self._resolve()
        thread.join(timeout=2.0)

        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], OSError)

    @unittest.skipUnless(os.name == "nt", "Windows handle pinning required")
    def test_pinned_configs_are_read_only_and_handles_close_on_all_exits(self) -> None:
        self._write_claude()
        self._write_codex()
        checked = False
        original = host_config._registration_from_entry

        def inspect_sharing(entry: object, *, platform: str):
            nonlocal checked
            if not checked:
                checked = True
                for path in (self.claude_path, self.codex_path):
                    with path.open("rb") as reader:
                        self.assertTrue(reader.read(1))
                    with self.assertRaises(OSError):
                        path.open("r+b")
                    replacement = path.with_name(path.name + ".replacement")
                    replacement.write_bytes(path.read_bytes())
                    try:
                        with self.assertRaises(OSError):
                            os.replace(replacement, path)
                    finally:
                        replacement.unlink(missing_ok=True)
            return original(entry, platform=platform)

        with mock.patch.object(
            host_config, "_registration_from_entry", side_effect=inspect_sharing
        ):
            self._resolve()
        self.assertTrue(checked)
        for path in (self.claude_path, self.codex_path):
            with path.open("r+b"):
                pass

        self._write_codex(idle_timeout="13.0")
        with self.assertRaises(host_config.HostConfigError):
            self._resolve()
        for path in (self.claude_path, self.codex_path):
            with path.open("r+b"):
                pass

    @unittest.skipUnless(os.name == "nt", "Windows handle cleanup required")
    def test_final_path_failure_closes_the_new_handle(self) -> None:
        self._write_claude()
        fake_handle = 123456
        with (
            mock.patch.object(
                host_config._kernel32, "CreateFileW", return_value=fake_handle
            ),
            mock.patch.object(
                host_config._kernel32,
                "GetFileInformationByHandleEx",
                return_value=True,
            ),
            mock.patch.object(
                host_config,
                "_final_handle_path",
                side_effect=host_config.HostConfigError(
                    "daemon_provenance_conflict"
                ),
            ),
            mock.patch.object(host_config._kernel32, "CloseHandle") as close,
            self.assertRaisesRegex(
                host_config.HostConfigError, "^daemon_provenance_conflict$"
            ),
        ):
            host_config._PinnedConfigFile(self.claude_path)

        close.assert_called_once_with(fake_handle)

    def test_post_create_attribute_failure_closes_the_new_handle(self) -> None:
        self._write_claude()
        fake_handle = 123456
        with (
            mock.patch.object(
                host_config._kernel32, "CreateFileW", return_value=fake_handle
            ),
            mock.patch.object(
                host_config._kernel32,
                "GetFileInformationByHandleEx",
                return_value=False,
            ),
            mock.patch.object(host_config, "_final_handle_path") as final_path,
            mock.patch.object(host_config._kernel32, "CloseHandle") as close,
            self.assertRaisesRegex(
                host_config.HostConfigError, "^daemon_provenance_conflict$"
            ),
        ):
            host_config._PinnedConfigFile(self.claude_path)

        final_path.assert_not_called()
        close.assert_called_once_with(fake_handle)

    def test_final_pinned_revalidation_rejects_byte_drift(self) -> None:
        self._write_claude()
        self._write_codex()
        original = host_config._PinnedConfigFile.read
        reads: dict[str, int] = {}

        def drift_on_second_read(handle):
            value = original(handle)
            key = str(handle.path)
            reads[key] = reads.get(key, 0) + 1
            if handle.path == self.codex_path and reads[key] > 1:
                return value + b"\n"
            return value

        with mock.patch.object(
            host_config._PinnedConfigFile, "read", autospec=True,
            side_effect=drift_on_second_read,
        ), self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_conflict$"
        ):
            self._resolve()

    @unittest.skipUnless(os.name == "nt", "Windows no-reparse semantics required")
    def test_config_reparse_points_are_rejected(self) -> None:
        real_claude = self.root / "real-claude.json"
        self._write_claude()
        self.claude_path.replace(real_claude)
        try:
            self.claude_path.symlink_to(real_claude)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        self._write_codex()

        with self.assertRaisesRegex(
            host_config.HostConfigError, "^daemon_provenance_conflict$"
        ):
            self._resolve()

    @unittest.skipUnless(os.name == "nt", "Windows reparse semantics required")
    def test_config_parent_reparse_is_rejected_without_open_or_launch(self) -> None:
        reparse_parent = self.root

        def attributes(path: str) -> int:
            if Path(path) == reparse_parent:
                return host_config._FILE_ATTRIBUTE_REPARSE_POINT
            return 0

        with (
            mock.patch.object(
                host_config._kernel32,
                "GetFileAttributesW",
                side_effect=attributes,
            ),
            mock.patch.object(host_config._kernel32, "CreateFileW") as create,
            self.assertRaisesRegex(
                host_config.HostConfigError, "^daemon_provenance_conflict$"
            ),
        ):
            host_config._PinnedConfigFile(self.claude_path)

        create.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows file sharing semantics required")
class HostConfigTargetTest(unittest.TestCase):
    def test_builders_preserve_other_entries_and_set_exact_timeouts(self) -> None:
        claude = json.dumps(
            {
                "marker": "secret-value-that-must-not-escape",
                "mcpServers": {
                    "other": {"command": "other.exe"},
                    "dayz-mcp": {"type": "stdio", "command": "python.exe", "args": ["-m", "dayz_mcp"]},
                },
            },
            indent=2,
        ).encode()
        codex = (
            'marker = "secret-value-that-must-not-escape"\n'
            '[mcp_servers.other]\ncommand = "other.exe"\n\n'
            '[mcp_servers.dayz-mcp]\ncommand = "python.exe"\nargs = ["-m", "dayz_mcp"]\n'
        ).encode()

        claude_target = build_claude_target(claude)
        codex_target = build_codex_target(codex)

        claude_parsed = json.loads(claude_target)
        codex_parsed = tomllib.loads(codex_target.decode())
        self.assertEqual(
            claude_parsed["mcpServers"]["dayz-mcp"]["timeout"],
            CLAUDE_TIMEOUT_MS,
        )
        self.assertEqual(
            codex_parsed["mcp_servers"]["dayz-mcp"]["tool_timeout_sec"],
            CODEX_TIMEOUT_SECONDS,
        )
        self.assertEqual(claude_parsed["mcpServers"]["other"]["command"], "other.exe")
        self.assertEqual(codex_parsed["mcp_servers"]["other"]["command"], "other.exe")

    def test_builders_reject_duplicate_or_ambiguous_sections(self) -> None:
        duplicate_json = b'{"mcpServers":{"dayz-mcp":{}},"mcpServers":{"dayz-mcp":{}}}'
        duplicate_toml = (
            b'[mcp_servers.dayz-mcp]\ncommand="one"\n'
            b'[mcp_servers.dayz-mcp]\ncommand="two"\n'
        )
        for builder, value in (
            (build_claude_target, duplicate_json),
            (build_codex_target, duplicate_toml),
        ):
            with self.subTest(builder=builder.__name__):
                with self.assertRaises(HostConfigError):
                    builder(value)


@unittest.skipUnless(os.name == "nt", "Windows file sharing semantics required")
class HostConfigTransactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.claude_path = root / ".claude.json"
        self.codex_path = root / "config.toml"
        self.journal = root / "journal"
        self.claude_original = json.dumps(
            {
                "private": "claude-secret-fixture",
                "mcpServers": {
                    "dayz-mcp": {
                        "type": "stdio",
                        "command": "python.exe",
                        "args": ["-m", "dayz_mcp"],
                    }
                },
            },
            indent=2,
        ).encode()
        self.codex_original = (
            'private = "codex-secret-fixture"\n'
            '[mcp_servers.dayz-mcp]\n'
            'command = "python.exe"\n'
            'args = ["-m", "dayz_mcp"]\n'
        ).encode()
        self.claude_path.write_bytes(self.claude_original)
        self.codex_path.write_bytes(self.codex_original)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_commit_sets_both_and_removes_journal(self) -> None:
        result = apply_host_timeouts(
            self.claude_path,
            self.codex_path,
            journal_root=self.journal,
        )

        self.assertEqual(result, {"status": "committed"})
        self.assertEqual(
            json.loads(self.claude_path.read_bytes())["mcpServers"]["dayz-mcp"]["timeout"],
            CLAUDE_TIMEOUT_MS,
        )
        self.assertEqual(
            tomllib.loads(self.codex_path.read_text())["mcp_servers"]["dayz-mcp"]["tool_timeout_sec"],
            CODEX_TIMEOUT_SECONDS,
        )
        self.assertFalse(self.journal.exists())

    def test_second_exclusive_open_failure_leaves_both_byte_exact(self) -> None:
        from dayz_mcp.host_config import open_exclusive_for_test

        with open_exclusive_for_test(self.codex_path):
            with self.assertRaises(HostConfigError) as raised:
                apply_host_timeouts(
                    self.claude_path,
                    self.codex_path,
                    journal_root=self.journal,
                )
        self.assertEqual(str(raised.exception), "registration_config_busy")
        self.assertEqual(self.claude_path.read_bytes(), self.claude_original)
        self.assertEqual(self.codex_path.read_bytes(), self.codex_original)
        self.assertFalse(self.journal.exists())

    def test_normal_failure_after_first_write_restores_both_byte_exact(self) -> None:
        def fail(phase: str) -> None:
            if phase == "after_write_claude":
                raise RuntimeError("fixture-secret-must-not-escape")

        with self.assertRaises(HostConfigError) as raised:
            apply_host_timeouts(
                self.claude_path,
                self.codex_path,
                journal_root=self.journal,
                fault_injector=fail,
            )

        self.assertEqual(str(raised.exception), "registration_config_transaction_failed")
        self.assertEqual(self.claude_path.read_bytes(), self.claude_original)
        self.assertEqual(self.codex_path.read_bytes(), self.codex_original)
        self.assertFalse(self.journal.exists())
        self.assertNotIn("fixture-secret", repr(raised.exception))

    def test_crash_torn_state_is_recovered_before_next_commit(self) -> None:
        def crash(phase: str) -> None:
            if phase == "after_write_claude":
                raise HostConfigCrash()

        with self.assertRaises(HostConfigCrash):
            apply_host_timeouts(
                self.claude_path,
                self.codex_path,
                journal_root=self.journal,
                fault_injector=crash,
            )
        self.assertTrue(self.journal.is_dir())
        self.assertNotEqual(self.claude_path.read_bytes(), self.claude_original)
        self.assertEqual(self.codex_path.read_bytes(), self.codex_original)

        result = apply_host_timeouts(
            self.claude_path,
            self.codex_path,
            journal_root=self.journal,
        )

        self.assertEqual(result, {"status": "committed"})
        self.assertFalse(self.journal.exists())

    def test_committed_journal_never_restores_post_commit_drift(self) -> None:
        for drift in ("original", "own_torn", "external"):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                claude_path = root / ".claude.json"
                codex_path = root / "config.toml"
                journal = root / "journal"
                claude_path.write_bytes(self.claude_original)
                codex_path.write_bytes(self.codex_original)

                def crash(phase: str) -> None:
                    if phase == "after_commit":
                        raise HostConfigCrash()

                with self.assertRaises(HostConfigCrash):
                    apply_host_timeouts(
                        claude_path,
                        codex_path,
                        journal_root=journal,
                        fault_injector=crash,
                    )
                claude_target = claude_path.read_bytes()
                codex_target = codex_path.read_bytes()
                if drift == "original":
                    changed = self.claude_original
                elif drift == "own_torn":
                    changed = next(
                        claude_target[:cut] + self.claude_original[cut:]
                        for cut in range(1, min(len(claude_target), len(self.claude_original)))
                        if claude_target[:cut] + self.claude_original[cut:]
                        not in {claude_target, self.claude_original}
                    )
                else:
                    changed = b"externally-edited-after-commit\n"
                claude_path.write_bytes(changed)
                observed = (claude_path.read_bytes(), codex_path.read_bytes())

                with self.assertRaisesRegex(
                    HostConfigError, "^registration_recovery_conflict$"
                ):
                    apply_host_timeouts(
                        claude_path,
                        codex_path,
                        journal_root=journal,
                    )

                self.assertEqual(
                    (claude_path.read_bytes(), codex_path.read_bytes()), observed
                )
                self.assertEqual(observed[1], codex_target)
                manifest = json.loads((journal / "manifest.json").read_text())
                self.assertEqual(manifest["status"], "committed")

    def test_partial_own_write_is_recovered_before_next_commit(self) -> None:
        def crash(phase: str) -> None:
            if phase == "after_write_claude":
                raise HostConfigCrash()

        with self.assertRaises(HostConfigCrash):
            apply_host_timeouts(
                self.claude_path,
                self.codex_path,
                journal_root=self.journal,
                fault_injector=crash,
            )
        target = self.claude_path.read_bytes()
        candidates = (
            target[:cut] + self.claude_original[cut:]
            for cut in range(1, min(len(target), len(self.claude_original)))
        )
        own_partial = next(
            value for value in candidates if value not in {target, self.claude_original}
        )
        self.claude_path.write_bytes(own_partial)

        result = apply_host_timeouts(
            self.claude_path,
            self.codex_path,
            journal_root=self.journal,
        )

        self.assertEqual(result, {"status": "committed"})
        self.assertFalse(self.journal.exists())
        self.assertNotEqual(self.claude_path.read_bytes(), own_partial)

    def test_partial_restore_reenters_from_durable_recovery_source(self) -> None:
        def initial_crash(phase: str) -> None:
            if phase == "after_write_claude":
                raise HostConfigCrash()

        with self.assertRaises(HostConfigCrash):
            apply_host_timeouts(
                self.claude_path,
                self.codex_path,
                journal_root=self.journal,
                fault_injector=initial_crash,
            )
        recovery_source = self.claude_path.read_bytes()

        def recovery_crash(phase: str) -> None:
            if phase == "after_recovery_prepare":
                raise HostConfigCrash()

        with self.assertRaises(HostConfigCrash):
            apply_host_timeouts(
                self.claude_path,
                self.codex_path,
                journal_root=self.journal,
                fault_injector=recovery_crash,
            )
        manifest = json.loads((self.journal / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "restoring_original")
        self.assertEqual(set(manifest["recovery_source"]), {"claude", "codex"})

        candidates = (
            self.claude_original[:cut] + recovery_source[cut:]
            for cut in range(1, min(len(self.claude_original), len(recovery_source)))
        )
        restore_partial = next(
            value
            for value in candidates
            if value not in {self.claude_original, recovery_source}
        )
        self.claude_path.write_bytes(restore_partial)

        result = apply_host_timeouts(
            self.claude_path,
            self.codex_path,
            journal_root=self.journal,
        )

        self.assertEqual(result, {"status": "committed"})
        self.assertFalse(self.journal.exists())

    def test_external_drift_during_recovery_conflicts_without_writes(self) -> None:
        def crash(phase: str) -> None:
            if phase == "after_write_claude":
                raise HostConfigCrash()

        with self.assertRaises(HostConfigCrash):
            apply_host_timeouts(
                self.claude_path,
                self.codex_path,
                journal_root=self.journal,
                fault_injector=crash,
            )
        external = self.codex_original + b"# external edit\n"
        self.codex_path.write_bytes(external)
        claude_before = self.claude_path.read_bytes()

        with self.assertRaises(HostConfigError) as raised:
            apply_host_timeouts(
                self.claude_path,
                self.codex_path,
                journal_root=self.journal,
            )

        self.assertEqual(str(raised.exception), "registration_recovery_conflict")
        self.assertEqual(self.claude_path.read_bytes(), claude_before)
        self.assertEqual(self.codex_path.read_bytes(), external)


if __name__ == "__main__":
    unittest.main()
