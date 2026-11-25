from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import daemon, doctor, playbook_tool, server_freshness
from dayz_mcp.daemon_policy import AccreditedDaemonPolicy
from dayz_mcp.identity_migration import _migration_disposition
from dayz_mcp.install_layout import installed_venv_root
from dayz_mcp.instance_context import _resolved_package_dir


def _try_symlink(target: Path, link: Path, *, directory: bool = False) -> bool:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        return False
    return True


def _installed_layout(base: Path, name: str = "venv") -> tuple[Path, Path]:
    root = base / name
    package = root / "Lib" / "site-packages" / "dayz_mcp"
    package.mkdir(parents=True)
    module = package / "x.py"
    module.write_text("# module\n", encoding="utf-8")
    (root / "pyvenv.cfg").write_text("home = python\n", encoding="utf-8")
    scripts = root / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"python")
    return root, module


class InstalledVenvRootTest(unittest.TestCase):
    def test_returns_root_for_windows_venv_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, module = _installed_layout(Path(temporary))
            self.assertEqual(installed_venv_root(module), root.resolve())
            self.assertEqual(installed_venv_root(str(module)), root.resolve())

    def test_none_without_pyvenv_cfg(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, module = _installed_layout(Path(temporary))
            (root / "pyvenv.cfg").unlink()
            self.assertIsNone(installed_venv_root(module))

    def test_none_when_pyvenv_cfg_is_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, module = _installed_layout(Path(temporary))
            (root / "pyvenv.cfg").unlink()
            (root / "pyvenv.cfg").mkdir()
            self.assertIsNone(installed_venv_root(module))

    def test_none_when_pyvenv_cfg_is_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, module = _installed_layout(Path(temporary))
            config = root / "pyvenv.cfg"
            target = Path(temporary) / "elsewhere.cfg"
            target.write_text("home = python\n", encoding="utf-8")
            config.unlink()
            if not _try_symlink(target, config):
                self.skipTest("this system does not allow creating symlinks")
            self.assertIsNone(installed_venv_root(module))

    def test_none_without_lib_component(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "venv"
            package = root / "site-packages" / "dayz_mcp"
            package.mkdir(parents=True)
            module = package / "x.py"
            module.write_text("# module\n", encoding="utf-8")
            (root / "pyvenv.cfg").write_text("home = python\n", encoding="utf-8")
            self.assertIsNone(installed_venv_root(module))

    def test_none_for_other_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "venv"
            package = root / "Lib" / "site-packages" / "otro"
            package.mkdir(parents=True)
            module = package / "x.py"
            module.write_text("# module\n", encoding="utf-8")
            (root / "pyvenv.cfg").write_text("home = python\n", encoding="utf-8")
            self.assertIsNone(installed_venv_root(module))

    def test_none_for_missing_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "venv" / "Lib" / "site-packages" / "dayz_mcp" / "x.py"
            self.assertIsNone(installed_venv_root(missing))

    def test_none_for_missing_module_in_a_valid_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, _module = _installed_layout(Path(temporary))
            missing = (
                root / "Lib" / "site-packages" / "dayz_mcp" / "ausente.py"
            )
            self.assertIsNone(installed_venv_root(missing))

    def test_none_when_pyvenv_cfg_claims_to_be_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _root, module = _installed_layout(Path(temporary))
            real_is_symlink = Path.is_symlink

            def fake_is_symlink(path: Path) -> bool:
                if path.name == "pyvenv.cfg":
                    return True
                return real_is_symlink(path)

            with mock.patch.object(Path, "is_symlink", fake_is_symlink):
                self.assertIsNone(installed_venv_root(module))

    def test_none_for_checkout_daemon(self) -> None:
        checkout_daemon = _TOOLS_DIR / "dayz_mcp" / "daemon.py"
        self.assertIsNone(installed_venv_root(checkout_daemon))
        self.assertIsNone(installed_venv_root(daemon.__file__))


class InstalledPythonAdmittedTest(unittest.TestCase):
    def test_true_for_the_same_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            approved = Path(temporary).resolve() / "Scripts" / "python.exe"
            approved.parent.mkdir(parents=True)
            approved.write_bytes(b"python")
            self.assertTrue(daemon._installed_python_admitted(approved, str(approved)))

    def test_false_for_another_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            approved = Path(temporary) / "Scripts" / "python.exe"
            other = Path(temporary) / "other" / "python.exe"
            approved.parent.mkdir(parents=True)
            other.parent.mkdir(parents=True)
            approved.write_bytes(b"python")
            other.write_bytes(b"python")
            self.assertFalse(daemon._installed_python_admitted(approved, str(other)))

    def test_false_when_approved_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            approved = Path(temporary) / "Scripts" / "python.exe"
            self.assertFalse(daemon._installed_python_admitted(approved, str(approved)))

    def test_false_when_approved_is_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            approved = Path(temporary) / "Scripts" / "python.exe"
            approved.mkdir(parents=True)
            self.assertFalse(daemon._installed_python_admitted(approved, str(approved)))

    def test_false_when_approved_is_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "real" / "python.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"python")
            approved = Path(temporary) / "Scripts" / "python.exe"
            approved.parent.mkdir(parents=True)
            if not _try_symlink(target, approved):
                self.skipTest("this system does not allow creating symlinks")
            self.assertFalse(daemon._installed_python_admitted(approved, str(approved)))

    def test_false_when_a_parent_is_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            real_scripts = Path(temporary) / "real" / "Scripts"
            real_scripts.mkdir(parents=True)
            (real_scripts / "python.exe").write_bytes(b"python")
            linked_root = Path(temporary) / "linked"
            if not _try_symlink(Path(temporary) / "real", linked_root, directory=True):
                self.skipTest("this system does not allow creating symlinks")
            approved = linked_root / "Scripts" / "python.exe"
            self.assertFalse(daemon._installed_python_admitted(approved, str(approved)))

    def test_false_when_a_parent_is_a_junction_on_windows(self) -> None:
        if os.name != "nt":
            self.skipTest("junction creation is Windows-only")
        import _winapi

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real_scripts = base / "real" / "Scripts"
            real_scripts.mkdir(parents=True)
            (real_scripts / "python.exe").write_bytes(b"python")
            root = base / "venv"
            root.mkdir()
            linked_scripts = root / "Scripts"
            try:
                _winapi.CreateJunction(str(real_scripts), str(linked_scripts))
            except OSError:
                self.skipTest("this system does not allow creating junctions")
            try:
                approved = linked_scripts / "python.exe"
                self.assertFalse(
                    daemon._installed_python_admitted(approved, str(approved))
                )
            finally:
                linked_scripts.rmdir()

    def test_false_when_resolving_raises_oserror(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            approved = Path(temporary).resolve() / "Scripts" / "python.exe"
            approved.parent.mkdir(parents=True)
            approved.write_bytes(b"python")
            with mock.patch.object(
                Path, "resolve", side_effect=OSError("resolve failed")
            ):
                self.assertFalse(
                    daemon._installed_python_admitted(approved, str(approved))
                )

    def test_true_with_other_casing_on_windows(self) -> None:
        if os.name != "nt":
            self.skipTest("case-insensitive interpreter comparison is Windows-only")
        with tempfile.TemporaryDirectory() as temporary:
            approved = Path(temporary).resolve() / "Scripts" / "python.exe"
            approved.parent.mkdir(parents=True)
            approved.write_bytes(b"python")
            self.assertTrue(
                daemon._installed_python_admitted(approved, str(approved).upper())
            )


class _GatePassed(Exception):
    pass


def _daemon_layout(base: Path, *, with_pyvenv: bool = True) -> tuple[Path, Path, Path]:
    root = base.resolve() / "venv"
    package = root / "Lib" / "site-packages" / "dayz_mcp"
    package.mkdir(parents=True)
    daemon_file = package / "daemon.py"
    daemon_file.write_text("# daemon\n", encoding="utf-8")
    if with_pyvenv:
        (root / "pyvenv.cfg").write_text("home = python\n", encoding="utf-8")
    scripts = root / "Scripts"
    scripts.mkdir(parents=True)
    python_exe = scripts / "python.exe"
    python_exe.write_bytes(b"python")
    return root, daemon_file, python_exe


@unittest.skipUnless(os.name == "nt", "the installed daemon gate branch is Windows-only")
class DaemonGateInstalledTest(unittest.TestCase):
    def test_venv_python_passes_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _root, daemon_file, python_exe = _daemon_layout(Path(temporary))
            with mock.patch.object(daemon, "__file__", str(daemon_file)), mock.patch.object(
                daemon.sys, "executable", str(python_exe)
            ), mock.patch.object(
                daemon, "NativeProcessGuard", side_effect=_GatePassed
            ):
                with self.assertRaises(_GatePassed):
                    daemon._ensure_identity_migration(SimpleNamespace())

    def test_other_interpreters_are_refused_as_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, daemon_file, python_exe = _daemon_layout(base)
            other_root = base / "other-venv"
            other_python = other_root / "Scripts" / "python.exe"
            other_python.parent.mkdir(parents=True)
            other_python.write_bytes(b"python")
            base_executable = getattr(sys, "_base_executable", sys.executable)
            cases = {
                "base_executable": base_executable,
                "other_venv": str(other_python),
            }
            for label, executable in cases.items():
                with self.subTest(label):
                    with mock.patch.object(
                        daemon, "__file__", str(daemon_file)
                    ), mock.patch.object(
                        daemon.sys, "executable", executable
                    ), mock.patch.object(
                        daemon, "NativeProcessGuard"
                    ) as guard:
                        with self.assertRaises(
                            daemon.DaemonPythonNotApproved
                        ) as raised:
                            daemon._ensure_identity_migration(SimpleNamespace())
                    self.assertTrue(raised.exception.installed)
                    self.assertEqual(
                        raised.exception.approved,
                        root.resolve() / "Scripts" / "python.exe",
                    )
                    self.assertEqual(raised.exception.current, executable)
                    guard.assert_not_called()

    def test_linked_approved_python_is_refused_as_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, daemon_file, python_exe = _daemon_layout(Path(temporary))
            target = Path(temporary) / "real-python.exe"
            target.write_bytes(b"python")
            python_exe.unlink()
            if not _try_symlink(target, python_exe):
                self.skipTest("this system does not allow creating symlinks")
            with mock.patch.object(daemon, "__file__", str(daemon_file)), mock.patch.object(
                daemon.sys, "executable", str(python_exe)
            ), mock.patch.object(daemon, "NativeProcessGuard") as guard:
                with self.assertRaises(daemon.DaemonPythonNotApproved) as raised:
                    daemon._ensure_identity_migration(SimpleNamespace())
            self.assertTrue(raised.exception.installed)
            self.assertEqual(
                raised.exception.approved, root.resolve() / "Scripts" / "python.exe"
            )
            guard.assert_not_called()

    def test_junction_approved_python_is_refused_as_installed(self) -> None:
        import _winapi

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "venv"
            package = root / "Lib" / "site-packages" / "dayz_mcp"
            package.mkdir(parents=True)
            daemon_file = package / "daemon.py"
            daemon_file.write_text("# daemon\n", encoding="utf-8")
            (root / "pyvenv.cfg").write_text("home = python\n", encoding="utf-8")
            real_scripts = base / "real" / "Scripts"
            real_scripts.mkdir(parents=True)
            (real_scripts / "python.exe").write_bytes(b"python")
            linked_scripts = root / "Scripts"
            try:
                _winapi.CreateJunction(str(real_scripts), str(linked_scripts))
            except OSError:
                self.skipTest("this system does not allow creating junctions")
            try:
                python_exe = linked_scripts / "python.exe"
                with mock.patch.object(
                    daemon, "__file__", str(daemon_file)
                ), mock.patch.object(
                    daemon.sys, "executable", str(python_exe)
                ), mock.patch.object(
                    daemon, "NativeProcessGuard"
                ) as guard:
                    with self.assertRaises(
                        daemon.DaemonPythonNotApproved
                    ) as raised:
                        daemon._ensure_identity_migration(SimpleNamespace())
                self.assertTrue(raised.exception.installed)
                self.assertEqual(
                    raised.exception.approved,
                    root.resolve() / "Scripts" / "python.exe",
                )
                guard.assert_not_called()
            finally:
                linked_scripts.rmdir()

    def test_gate_resolves_approved_python_with_on_disk_casing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "venv"
            package = root / "Lib" / "site-packages" / "dayz_mcp"
            package.mkdir(parents=True)
            daemon_file = package / "daemon.py"
            daemon_file.write_text("# daemon\n", encoding="utf-8")
            (root / "pyvenv.cfg").write_text("home = python\n", encoding="utf-8")
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            python_exe = scripts / "python.exe"
            python_exe.write_bytes(b"python")
            captured: dict[str, object] = {}

            def fake_capture(identity: object, approved: object, guard: object = None) -> None:
                del identity, guard
                captured["approved"] = approved
                raise _GatePassed

            fake_guard = SimpleNamespace(
                snapshot=lambda pid: {
                    "identity_complete": True,
                    "identity_scheme": "psutil-argv-v2",
                }
            )
            with mock.patch.object(
                daemon, "__file__", str(daemon_file)
            ), mock.patch.object(
                daemon.sys, "executable", str(python_exe)
            ), mock.patch.object(
                daemon, "NativeProcessGuard", return_value=fake_guard
            ), mock.patch.object(
                daemon, "capture_launch_ancestor_identity", fake_capture
            ):
                with self.assertRaises(_GatePassed):
                    daemon._ensure_identity_migration(SimpleNamespace())
            approved_seen = captured["approved"]
            self.assertIsInstance(approved_seen, Path)
            assert isinstance(approved_seen, Path)
            self.assertEqual(str(approved_seen), str(python_exe.resolve()))
            self.assertIn("scripts", approved_seen.parts)
            self.assertNotIn("Scripts", approved_seen.parts)
            self.assertNotEqual(
                str(approved_seen),
                str(root.resolve() / "Scripts" / "python.exe"),
            )

    def test_without_pyvenv_cfg_the_clone_rule_applies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _root, daemon_file, python_exe = _daemon_layout(
                Path(temporary), with_pyvenv=False
            )
            with mock.patch.object(daemon, "__file__", str(daemon_file)), mock.patch.object(
                daemon.sys, "executable", str(python_exe)
            ), mock.patch.object(daemon, "NativeProcessGuard") as guard:
                with self.assertRaises(daemon.DaemonPythonNotApproved) as raised:
                    daemon._ensure_identity_migration(SimpleNamespace())
            self.assertFalse(raised.exception.installed)
            guard.assert_not_called()


class PythonNotApprovedLineTest(unittest.TestCase):
    def test_installed_line(self) -> None:
        approved = Path("venv") / "Scripts" / "python.exe"
        current = r"C:\Foreign\python.exe"
        error = daemon.DaemonPythonNotApproved(approved, current, installed=True)
        line = daemon._python_not_approved_line(error)
        self.assertNotIn("\n", line)
        self.assertTrue(
            line.startswith(
                "DAEMON: daemon_python_not_approved: this interpreter is "
            )
        )
        self.assertIn(str(approved), line)
        self.assertIn(current, line)
        self.assertNotIn("tools\\.venv-mcp", line)
        self.assertNotIn("README", line)
        self.assertNotIn(".ps1", line.casefold())

    def test_clone_line_is_unchanged(self) -> None:
        approved = Path("venv") / "Scripts" / "python.exe"
        current = r"C:\Foreign\python.exe"
        error = daemon.DaemonPythonNotApproved(approved, current)
        self.assertFalse(error.installed)
        expected = (
            "DAEMON: daemon_python_not_approved: this interpreter is "
            f"{current}, but the daemon runs only under {approved}, the "
            "path it checks its own identity against. Create that venv with the "
            "installer in README \"Install\" (it installs this package into "
            "tools\\.venv-mcp) and start the daemon with its python.exe."
        )
        self.assertEqual(daemon._python_not_approved_line(error), expected)


def _classifier_layout(base: Path) -> tuple[Path, Path, Path]:
    root = base / "venv"
    package = root / "Lib" / "site-packages" / "dayz_mcp"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text("", encoding="utf-8")
    (package / "instance_context.py").write_text(
        'ROOT_SELECTION_CONTRACT_ID = "97de-v1"\n', encoding="utf-8"
    )
    scripts = root / "Scripts"
    scripts.mkdir(parents=True)
    python_exe = scripts / "python.exe"
    python_exe.write_bytes(b"python")
    return root, package, python_exe


def _daemon_argv(executable: Path, token: str) -> list[str]:
    return [
        str(executable),
        "-m",
        "dayz_mcp",
        "--daemon",
        "--keyfile",
        r"C:\keys\shared.key",
        "--instance",
        token,
    ]


class ClassifierInstalledTest(unittest.TestCase):
    def test_installed_daemon_with_unreadable_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _root, package, python_exe = _classifier_layout(Path(temporary))
            other = _migration_disposition(
                _daemon_argv(python_exe, "131"),
                str(python_exe),
                cwd=None,
                state_token="130",
            )
            self.assertEqual(other, "not_writer")
            same = _migration_disposition(
                _daemon_argv(python_exe, "130"),
                str(python_exe),
                cwd=None,
                state_token="130",
            )
            self.assertEqual(same, "blocker")
            (package / "instance_context.py").write_text(
                "# no contract line\n", encoding="utf-8"
            )
            without_contract = _migration_disposition(
                _daemon_argv(python_exe, "131"),
                str(python_exe),
                cwd=None,
                state_token="130",
            )
            self.assertEqual(without_contract, "blocker")

    def test_clone_venv_still_prefers_checkout_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tools_root = Path(temporary) / "tools"
            checkout_package = tools_root / "dayz_mcp"
            checkout_package.mkdir(parents=True)
            (checkout_package / "__init__.py").write_text("", encoding="utf-8")
            scripts = tools_root / ".venv-mcp" / "Scripts"
            scripts.mkdir(parents=True)
            python_exe = scripts / "python.exe"
            python_exe.write_bytes(b"python")
            installed_package = (
                tools_root
                / ".venv-mcp"
                / "Lib"
                / "site-packages"
                / "dayz_mcp"
            )
            installed_package.mkdir(parents=True)
            (installed_package / "__init__.py").write_text("", encoding="utf-8")
            self.assertEqual(
                _resolved_package_dir(str(python_exe), None, None),
                checkout_package,
            )


class PlaybooksDirTest(unittest.TestCase):
    def test_installed_layout_uses_package_playbooks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _root, module = _installed_layout(Path(temporary))
            playbook_module = module.with_name("playbook_tool.py")
            module.rename(playbook_module)
            self.assertEqual(
                playbook_tool._playbooks_dir(playbook_module),
                playbook_module.resolve().parent / "playbooks",
            )

    def test_checkout_layout_uses_repo_playbooks(self) -> None:
        expected = Path(playbook_tool.__file__).resolve().parents[2] / "playbooks"
        self.assertEqual(playbook_tool._playbooks_dir(playbook_tool.__file__), expected)
        self.assertEqual(playbook_tool.PLAYBOOKS_DIR, expected)

    def test_freshness_shares_the_playbooks_object(self) -> None:
        self.assertIs(server_freshness._PLAYBOOKS_ROOT, playbook_tool.PLAYBOOKS_DIR)


class _FakeRuntimePaths:
    @staticmethod
    def for_token(token: object) -> Path:
        del token
        return Path("runtime-paths")


def _accredited_policy(keyfile: Path) -> AccreditedDaemonPolicy:
    authority = {
        "argv": [
            r"P:\Runtime\python.exe",
            "-m",
            "dayz_mcp",
            "--daemon",
            "--port",
            "8765",
        ],
        "cwd": r"P:\DayZ_MCP_dev\tools",
        "host": "127.0.0.1",
        "keyfile": str(keyfile),
        "kind": "normal",
        "native_executable": r"P:\Runtime\python.exe",
        "port": 8765,
        "security_build_id": None,
    }
    authority_sha256 = hashlib.sha256(
        json.dumps(
            authority, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    return AccreditedDaemonPolicy(
        kind="normal",
        host="127.0.0.1",
        port=8765,
        keyfile=str(keyfile),
        native_executable=r"P:\Runtime\python.exe",
        argv=tuple(authority["argv"]),
        cwd=r"P:\DayZ_MCP_dev\tools",
        security_build_id=None,
        authority_sha256=authority_sha256,
    )


class DoctorDefaultSourcesTest(unittest.TestCase):
    def _sources(self, module_file: str):
        fake_guard = SimpleNamespace(snapshot=lambda pid: {})
        with tempfile.TemporaryDirectory() as temporary:
            policy = _accredited_policy(Path(temporary) / "shared.key")
            with mock.patch.object(
                doctor, "__file__", module_file
            ), mock.patch.object(
                doctor, "NativeProcessGuard", return_value=fake_guard
            ), mock.patch.object(
                doctor, "resolve_pack_dir", return_value=None
            ), mock.patch.object(
                doctor, "default_manifest_path", return_value=None
            ), mock.patch.object(
                doctor, "RuntimePaths", _FakeRuntimePaths
            ):
                return doctor.default_sources(policy)

    def test_installed_layout_has_no_scan_roots_or_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _root, module = _installed_layout(Path(temporary))
            doctor_module = module.with_name("doctor.py")
            module.rename(doctor_module)
            sources = self._sources(str(doctor_module))
            self.assertEqual(sources.scan_roots, ())
            self.assertIsNone(sources.native_launcher_id)

    def test_checkout_layout_keeps_todays_sources(self) -> None:
        sources = self._sources(doctor.__file__)
        self.assertEqual(
            sources.scan_roots,
            (Path(doctor.__file__).resolve().parents[3],),
        )
        self.assertEqual(sources.native_launcher_id, "dayz-test-v1")


if __name__ == "__main__":
    unittest.main()
