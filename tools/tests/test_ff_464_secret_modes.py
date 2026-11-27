"""Fix-forward of #464 P1-2: the installer's key and every dayz_mcp.json that
copies it are 0600 from the open on POSIX, under umask 022.

Its own module so the Linux CI job can run it (test_install_mcp mostly tests
Windows behaviour and is not part of that job).
"""

from __future__ import annotations

import json
import os
import stat
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import install_mcp as installer
from install_mcp import InstallerExecutionError, install_runtime, parse_args


def _write_fake_elf(path: Path) -> None:
    payload = bytearray(64)
    payload[0:4] = b"\x7fELF"
    payload[4] = 2  # 64-bit
    payload[5] = 1  # little-endian
    payload[6] = 1  # ELF version 1
    path.write_bytes(payload)


@unittest.skipUnless(os.name == "posix", "POSIX file modes")
class PosixInstallerSecretModesTest(unittest.TestCase):
    """Fix-forward #464 P1-2: the key and every dayz_mcp.json are 0600 from open."""

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)

    @staticmethod
    def _mode(path: Path) -> int:
        return stat.S_IMODE(path.lstat().st_mode)

    def test_a_new_key_and_config_are_0600_under_umask_022(self) -> None:
        key = self.root / "tools" / ".dayz_mcp.key"
        config = self.root / "tools" / "_mcp_config" / "server_profiles" / "dayz_mcp.json"
        self.assertEqual(installer._load_or_create_key(key, lambda: "k-fixture"), "k-fixture")
        installer._write_config(config, installer.config_payload(18765, "k-fixture"))
        self.assertEqual(self._mode(key), 0o600)
        self.assertEqual(self._mode(config), 0o600)
        self.assertEqual(key.read_text(encoding="ascii"), "k-fixture\n")
        self.assertEqual(json.loads(config.read_text(encoding="ascii"))["key"], "k-fixture")

    def test_the_mode_is_0600_at_the_open_not_only_after_it(self) -> None:
        # No window where a new secret file exists with umask-derived bits.
        real_open = os.open
        modes: list[int] = []

        def spy_open(path, flags, mode=0o777, *args, **kwargs):
            if flags & os.O_CREAT:
                modes.append(mode)
            return real_open(path, flags, mode, *args, **kwargs)

        with patch.object(installer.os, "open", side_effect=spy_open):
            installer._load_or_create_key(self.root / "k", lambda: "k-open")
            installer._write_config(self.root / "c.json", installer.config_payload(1, "k-open"))
        self.assertEqual(modes, [0o600, 0o600])

    def test_an_existing_own_config_is_tightened_before_the_key_is_written(self) -> None:
        config = self.root / "dayz_mcp.json"
        config.write_text("{}\n", encoding="ascii")
        config.chmod(0o644)
        installer._write_config(config, installer.config_payload(18765, "k2"))
        self.assertEqual(self._mode(config), 0o600)
        self.assertEqual(json.loads(config.read_text(encoding="ascii"))["key"], "k2")

    def test_a_config_that_is_a_link_or_hard_linked_is_refused(self) -> None:
        target = self.root / "elsewhere.json"
        target.write_text("{}\n", encoding="ascii")
        target.chmod(0o644)
        link = self.root / "link.json"
        link.symlink_to(target)
        hard = self.root / "hard.json"
        os.link(target, hard)
        for path in (link, hard):
            with self.subTest(path=path.name):
                with self.assertRaises(InstallerExecutionError) as raised:
                    installer._write_config(path, installer.config_payload(18765, "k3"))
                self.assertEqual(str(raised.exception), "config_write_failed")
        self.assertEqual(target.read_text(encoding="ascii"), "{}\n")
        self.assertEqual(self._mode(target), 0o644)

    def test_an_existing_key_readable_by_group_or_others_is_refused(self) -> None:
        for mode in (0o644, 0o640, 0o604):
            with self.subTest(mode=oct(mode)):
                key = self.root / f"key-{mode:o}"
                key.write_text("old-key\n", encoding="ascii")
                key.chmod(mode)
                with self.assertRaises(InstallerExecutionError) as raised:
                    installer._load_or_create_key(key, lambda: "unused")
                self.assertEqual(str(raised.exception), "keyfile_permissions_unsafe")
        key = self.root / "key-private"
        key.write_text("old-key\n", encoding="ascii")
        key.chmod(0o600)
        self.assertEqual(installer._load_or_create_key(key, lambda: "unused"), "old-key")

    # --- round 2 (Sol P1-1): a rewrite never reuses the old inode ---------

    def test_a_descriptor_opened_while_the_config_was_0644_never_sees_the_new_key(self) -> None:
        config = self.root / "dayz_mcp.json"
        config.write_text('{"key":"old-key"}\n', encoding="ascii")
        config.chmod(0o644)
        old_inode = config.lstat().st_ino
        with open(config, "rb") as early_reader:
            installer._write_config(config, installer.config_payload(18765, "new-secret"))
            early_reader.seek(0)
            seen = early_reader.read()
        self.assertNotIn(b"new-secret", seen)
        self.assertNotEqual(config.lstat().st_ino, old_inode)
        self.assertEqual(self._mode(config), 0o600)
        self.assertEqual(json.loads(config.read_text(encoding="ascii"))["key"], "new-secret")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["dayz_mcp.json"])

    def test_a_failed_rewrite_leaves_the_old_config_and_no_temporary(self) -> None:
        config = self.root / "dayz_mcp.json"
        config.write_text("{}\n", encoding="ascii")
        config.chmod(0o600)
        with patch.object(installer.os, "replace", side_effect=OSError(5, "EIO")):
            with self.assertRaises(InstallerExecutionError):
                installer._write_config(config, installer.config_payload(1, "never"))
        self.assertEqual(config.read_text(encoding="ascii"), "{}\n")
        self.assertEqual([p.name for p in self.root.iterdir()], ["dayz_mcp.json"])

    # --- round 2 (Sol P1-2): no resolve() of the keyfile leaf ------------

    def _options_for(self, keyfile: Path):
        tools_root = self.root / "tools"
        tools_root.mkdir(exist_ok=True)
        return parse_args(["--port", "18765", "--keyfile", str(keyfile)], tools_root=tools_root)

    def test_parse_args_keeps_a_keyfile_link_and_the_link_is_refused(self) -> None:
        target = self.root / "real.key"
        target.write_text("linked-secret\n", encoding="ascii")
        target.chmod(0o600)
        link = self.root / "link.key"
        link.symlink_to(target)
        options = self._options_for(link)
        self.assertEqual(options.keyfile, link)
        with self.assertRaises(InstallerExecutionError) as raised:
            installer._load_or_create_key(options.keyfile, lambda: "unused")
        self.assertEqual(str(raised.exception), "keyfile_permissions_unsafe")

    def test_a_dangling_keyfile_link_never_creates_the_key_at_its_target(self) -> None:
        target = self.root / "elsewhere" / "planted.key"
        target.parent.mkdir()
        link = self.root / "dangling.key"
        link.symlink_to(target)
        options = self._options_for(link)
        with self.assertRaises(InstallerExecutionError):
            installer._load_or_create_key(options.keyfile, lambda: "fresh-secret")
        self.assertFalse(target.exists())
        self.assertTrue(link.is_symlink())

    def test_a_key_swapped_after_validation_is_not_read(self) -> None:
        key = self.root / "daemon.key"
        key.write_text("validated-key\n", encoding="ascii")
        key.chmod(0o600)
        swapped = {"done": False}

        def swap() -> None:
            if swapped["done"]:
                return
            swapped["done"] = True
            planted = self.root / "planted"
            planted.write_text("planted-key\n", encoding="ascii")
            planted.chmod(0o644)
            os.replace(planted, key)

        real_lstat = Path.lstat
        real_fstat = os.fstat

        def lstat_then_swap(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if Path(path) == key:
                swap()
            return info

        def fstat_then_swap(descriptor, *args, **kwargs):
            info = real_fstat(descriptor, *args, **kwargs)
            swap()
            return info

        with patch.object(Path, "lstat", lstat_then_swap), \
                patch.object(installer.os, "fstat", side_effect=fstat_then_swap):
            value = installer._load_or_create_key(key, lambda: "unused")
        self.assertTrue(swapped["done"])
        self.assertEqual(value, "validated-key")

    # --- round 3 (Sol) ----------------------------------------------------

    def test_a_dotdot_after_a_linked_directory_resolves_like_main(self) -> None:
        real_sub = self.root / "real" / "sub"
        real_sub.mkdir(parents=True)
        alias = self.root / "alias"
        alias.symlink_to(real_sub)
        options = self._options_for(Path(f"{alias}/../daemon.key"))
        self.assertEqual(options.keyfile, self.root / "real" / "daemon.key")

    def test_a_fifo_at_the_key_path_is_refused_without_hanging(self) -> None:
        import signal

        fifo = self.root / "daemon.key"
        os.mkfifo(fifo, 0o600)

        def timed_out(*_args):
            raise TimeoutError("the key read hung on a FIFO")

        previous = signal.signal(signal.SIGALRM, timed_out)
        signal.alarm(3)
        try:
            with self.assertRaises(InstallerExecutionError) as raised:
                installer._load_or_create_key(fifo, lambda: "unused")
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        self.assertEqual(str(raised.exception), "keyfile_permissions_unsafe")

    def test_new_secrets_are_exactly_0600_even_under_umask_0400(self) -> None:
        os.umask(0o400)
        key = self.root / "k.key"
        config = self.root / "dayz_mcp.json"
        config.write_text("{}\n", encoding="ascii")
        config.chmod(0o600)
        installer._load_or_create_key(key, lambda: "k-umask")
        installer._write_config(config, installer.config_payload(1, "k-umask"))
        fresh = self.root / "fresh.json"
        installer._write_config(fresh, installer.config_payload(1, "k-umask"))
        for path in (key, config, fresh):
            with self.subTest(path=path.name):
                self.assertEqual(self._mode(path), 0o600)

    # --- follow-up hardening (Auditor, round 3 on #474) -------------------

    def test_a_keyfile_ending_in_dotdot_is_refused(self) -> None:
        (self.root / "real").mkdir()
        for value in (f"{self.root}/real/..", ".."):
            with self.subTest(value=value):
                with self.assertRaises(installer.InstallerContractError) as raised:
                    self._options_for(Path(value))
                self.assertEqual(raised.exception.code, "keyfile_path_invalid")

    def test_a_failure_after_the_exclusive_create_removes_our_key(self) -> None:
        key = self.root / "k.key"
        with patch.object(installer, "_write_all", side_effect=OSError(5, "EIO")):
            with self.assertRaises(InstallerExecutionError) as raised:
                installer._load_or_create_key(key, lambda: "k-partial")
        self.assertEqual(str(raised.exception), "keyfile_write_failed")
        self.assertFalse(os.path.lexists(key))
        # The next run creates a complete key instead of reading an empty one.
        self.assertEqual(installer._load_or_create_key(key, lambda: "k-full"), "k-full")

    def test_a_replaced_key_is_never_removed_by_our_cleanup(self) -> None:
        key = self.root / "k.key"
        other = self.root / "other.key"
        other.write_text("someone-else\n", encoding="ascii")

        def swap_then_fail(_descriptor, _data):
            os.replace(other, key)
            raise OSError(5, "EIO")

        with patch.object(installer, "_write_all", side_effect=swap_then_fail):
            with self.assertRaises(InstallerExecutionError):
                installer._load_or_create_key(key, lambda: "k-partial")
        self.assertEqual(key.read_text(encoding="ascii"), "someone-else\n")

    def test_a_mode_that_fchmod_did_not_apply_is_refused(self) -> None:
        # VFAT and similar accept fchmod and keep their own mode bits.
        os.umask(0o400)
        key = self.root / "k.key"
        config = self.root / "dayz_mcp.json"
        config.write_text("{}\n", encoding="ascii")
        config.chmod(0o600)
        with patch.object(installer.os, "fchmod", lambda *_args: None):
            with self.assertRaises(InstallerExecutionError) as raised:
                installer._load_or_create_key(key, lambda: "k-vfat")
            self.assertEqual(str(raised.exception), "keyfile_write_failed")
            self.assertFalse(os.path.lexists(key))
            with self.assertRaises(InstallerExecutionError) as raised:
                installer._write_config(config, installer.config_payload(1, "k-vfat"))
            self.assertEqual(str(raised.exception), "config_write_failed")
        self.assertEqual(config.read_text(encoding="ascii"), "{}\n")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["dayz_mcp.json"])

    def test_install_runtime_under_umask_022_leaves_every_secret_0600(self) -> None:
        tools_root = self.root / "tools"
        tools_root.mkdir()
        (tools_root / "requirements-mcp.txt").write_text("mcp==1.27.2\n", encoding="utf-8")
        (tools_root / "pyproject.toml").write_text("[build-system]\nrequires=[]\n", encoding="utf-8")
        base_python = self.root / "base" / "bin" / "python3"
        base_python.parent.mkdir(parents=True)
        _write_fake_elf(base_python)
        base_python.chmod(0o755)
        server_profiles = self.root / "srv-profiles"
        server_profiles.mkdir()
        options = parse_args(
            ["--port", "18765", "--server-profiles", str(server_profiles)],
            tools_root=tools_root,
        )

        def runner(argv: list[str], **_kwargs: object) -> object:
            if argv[1:3] == ["-m", "venv"]:
                venv_python = Path(argv[-1]) / "bin" / "python"
                venv_python.parent.mkdir(parents=True)
                venv_python.write_bytes(b"#!/bin/sh\n")
            return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch.object(installer, "_is_windows_host", return_value=False):
            summary = install_runtime(
                options,
                base_python=base_python,
                runner=runner,
                token_factory=lambda: "posix-umask-token",
            )
        secrets_written = [Path(summary["keyfile"]), *map(Path, summary["configs"])]
        self.assertIn(server_profiles / "dayz_mcp.json", secrets_written)
        for path in secrets_written:
            with self.subTest(path=str(path)):
                self.assertEqual(self._mode(path), 0o600)


if __name__ == "__main__":
    unittest.main()
