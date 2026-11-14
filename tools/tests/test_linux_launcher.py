"""The Linux dayz-test-v1 path: bundle, worker host, path authority, host probes."""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

POSIX = os.name != "nt"

if POSIX:
    from dayz_mcp import linux_compat

    linux_compat.install()
    from dayz_mcp import linux_launcher, orphan_guard, request_path_authority
    from dayz_mcp.process_lifecycle import ProcessLifecycle, _holder_unattributed

TOOLS = Path(__file__).resolve().parent.parent


def _write_policy(root: Path) -> Path:
    """A launcher policy whose roots are real directories under ``root``."""
    missions = root / "dev" / "_server" / "mpmissions"
    for path in (root / "source", root / "mods", root / "temp", missions):
        path.mkdir(parents=True, exist_ok=True)
    for name in ("dayzOffline.chernarusplus", "dayzOffline.enoch", "dayzOffline.sakhal"):
        (missions / name).mkdir()
    server = root / "DayZServer"
    server.mkdir()
    (server / "DayZServer").write_bytes(b"")

    def intent(path: Path) -> dict[str, object]:
        return {"path": str(path), "allow_root_junction": False}

    policy = {
        "format_version": 1,
        "projects": [
            {
                "mod": "DayZ_MCP",
                "default_base_mods": [],
                "default_source": intent(root / "source"),
                "dev_root": intent(root / "dev"),
                "mission_roots": [intent(missions)],
                "mod_roots": [intent(root / "mods")],
                "build_temp_root": str(root / "temp"),
                "build_source_basename": None,
                "diag_executable": str(server / "DayZServer"),
                "game_directory": str(server),
                "mission_aliases": {
                    "chernarus": str(missions / "dayzOffline.chernarusplus"),
                    "livonia": str(missions / "dayzOffline.enoch"),
                    "sakhal": str(missions / "dayzOffline.sakhal"),
                },
                "mods_root": str(root / "mods"),
                "server_extra_args": [],
                "client_extra_args": [],
            }
        ],
    }
    path = root / "launcher-policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    return path


@unittest.skipUnless(POSIX, "the Linux launcher bundle")
class LinuxBundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(os.path.realpath(self.temporary.name))
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        patcher = patch.dict(os.environ, {"LOCALAPPDATA": str(self.state)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.policy = _write_policy(self.root / "project")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def install_and_open(self) -> object:
        linux_launcher.install(self.policy)
        opened = linux_launcher.open_launcher()
        return linux_launcher.load_verified_bundle(opened)

    def test_installed_bundle_verifies_and_carries_the_policy(self) -> None:
        bundle = self.install_and_open()
        self.assertEqual([item.policy.mod for item in bundle.sealed_policies], ["DayZ_MCP"])
        runtime = bundle.validated_worker_runtime("DayZ_MCP", str(self.root / "project" / "dev"))
        self.assertTrue(runtime.diag_executable.endswith("/DayZServer"))

    def test_reinstall_replaces_the_bundle(self) -> None:
        first = self.install_and_open().manifest_sha256
        second = self.install_and_open().manifest_sha256
        self.assertEqual(first, second)
        self.assertEqual(
            sorted(entry.name for entry in (self.state / "DayZ_MCP" / "linux-launchers").iterdir()),
            ["dayz-test-v1"],
        )

    def test_tampered_or_extra_files_are_refused(self) -> None:
        self.install_and_open()
        root = self.state / "DayZ_MCP" / "linux-launchers" / "dayz-test-v1"
        for name in ("app.pyz", "worker-runtime.json", "request-policy.json"):
            original = (root / name).read_bytes()
            (root / name).write_bytes(original + b" ")
            with self.subTest(name=name), self.assertRaises(ValueError):
                linux_launcher.load_verified_bundle(linux_launcher.open_launcher())
            (root / name).write_bytes(original)
        (root / "extra").write_bytes(b"x")
        with self.assertRaises(ValueError):
            linux_launcher.load_verified_bundle(linux_launcher.open_launcher())

    def test_group_writable_bundle_is_refused(self) -> None:
        self.install_and_open()
        root = self.state / "DayZ_MCP" / "linux-launchers" / "dayz-test-v1"
        os.chmod(root, 0o770)
        with self.assertRaises(ValueError):
            linux_launcher.open_launcher()

    def test_worker_host_runs_the_sealed_worker_from_the_archive(self) -> None:
        bundle = self.install_and_open()
        argv = linux_launcher._host_argv(bundle, lifecycle=False)
        read, write = os.pipe()
        try:
            env = dict(os.environ, DAYZ_MCP_CANCEL_HANDLE=str(read), PYTHONPATH=str(TOOLS))
            done = subprocess.run(
                argv, input=b"", capture_output=True, env=env, pass_fds=(read,), timeout=60
            )
        finally:
            os.close(read)
            os.close(write)
        # No bootstrap frame on stdin: the sealed app_main answers with its terminal.
        size = struct.unpack("<I", done.stdout[:4])[0]
        body = done.stdout[4 : 4 + size]
        self.assertTrue(body.startswith(b"DZW1"), done.stderr[-2000:])
        terminal = json.loads(body[4:])
        self.assertEqual(terminal["error_code"], "internal_failure")
        self.assertEqual(done.returncode, 2)

    def test_worker_host_refuses_an_archive_with_another_hash(self) -> None:
        bundle = self.install_and_open()
        argv = linux_launcher._host_argv(bundle, lifecycle=False)
        argv[argv.index("--app-sha256") + 1] = "0" * 64
        done = subprocess.run(
            argv, input=b"", capture_output=True, env=dict(os.environ, PYTHONPATH=str(TOOLS)), timeout=60
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn(b"DZW1", done.stdout)

    def test_the_linux_overlay_is_sealed_inside_app_pyz(self) -> None:
        # The overlay decides how the sealed worker behaves (paths, locks, the
        # build relay). Outside app.pyz an edit would change the worker while the
        # bundle still verified, so the three modules must be members.
        bundle = self.install_and_open()
        root = self.state / "DayZ_MCP" / "linux-launchers" / "dayz-test-v1"
        import zipfile

        from dayz_mcp import linux_worker_host

        with zipfile.ZipFile(root / "app.pyz") as archive:
            members = set(archive.namelist())
        for name in ("linux_compat.py", "linux_build.py", "byte_lock.py"):
            with self.subTest(name=name):
                self.assertIn(f"dayz_mcp/{name}", members)
        # And the worker host's finder is what serves them: it is inserted at
        # meta_path[0], ahead of the working tree.
        app_members = linux_worker_host._read_app(root, bundle.app_sha256)
        finder = linux_worker_host._SealedModuleFinder(app_members, str(root / "app.pyz"))
        for name in ("linux_compat", "linux_build", "byte_lock"):
            with self.subTest(name=name):
                self.assertIsNotNone(finder.find_spec(f"dayz_mcp.{name}"))

    def test_a_drifted_overlay_is_refused_before_the_worker_starts(self) -> None:
        # Editing linux_compat.py must invalidate the bundle, not silently change
        # what the sealed worker does. sealed_sources() is the check that runs.
        lock = json.loads((TOOLS / "packaged-modules.lock.json").read_text())
        relative = "tools/dayz_mcp/linux_compat.py"
        self.assertIn(relative, lock["files"])
        lock["files"][relative] = "0" * 64
        with tempfile.TemporaryDirectory() as holder:
            drifted = Path(holder) / "packaged-modules.lock.json"
            drifted.write_text(json.dumps(lock))
            with patch.object(linux_launcher, "_LOCK_PATH", drifted):
                with self.assertRaises(ValueError) as caught:
                    linux_launcher.sealed_sources()
        self.assertIn("packaged_module_drift", str(caught.exception))
        self.assertIn(relative, str(caught.exception))


@unittest.skipUnless(POSIX, "the msvcrt shim of the Linux worker host")
class WorkerHostShimTest(unittest.TestCase):
    def test_msvcrt_exists_only_while_app_main_is_imported(self) -> None:
        from dayz_mcp import linux_worker_host

        seen: dict[str, object] = {}
        source = b"import msvcrt, sys\nSEEN = msvcrt.open_osfhandle(7, 0)\n"
        module = linux_worker_host._load_app_main({"__main__.py": source}, Path("/bundle"))
        seen["value"] = module.SEEN
        self.assertEqual(seen["value"], 7)
        self.assertNotIn("msvcrt", sys.modules)
        self.assertEqual(module._bundle_root(), Path("/bundle"))
        sys.modules.pop("dayz_test_app_main", None)


@unittest.skipUnless(POSIX, "request_path_authority on POSIX")
class PosixPathAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(os.path.realpath(self.temporary.name))
        (self.root / "root" / "a" / "b").mkdir(parents=True)
        (self.root / "other").mkdir()
        (self.root / "root" / "link").symlink_to(self.root / "other")
        (self.root / "rootlink").symlink_to(self.root / "root")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def capture(self, name: str, *, junction: bool = False) -> object:
        return request_path_authority._capture_root_for_test(
            str(self.root / name), allow_root_junction=junction
        )

    def test_descendant_is_accredited_and_pinned(self) -> None:
        root = self.capture("root")
        with request_path_authority.accredit_exact_paths((str(self.root / "root/a/b"),), (root,)) as paths:
            self.assertEqual(len(paths.identities["exact"]), 1)
            self.assertGreater(paths.handle_count, 0)

    def test_symlink_below_root_missing_path_and_outside_path_are_refused(self) -> None:
        root = self.capture("root")
        for path in ("root/link", "root/missing", "other"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                request_path_authority.accredit_exact_paths((str(self.root / path),), (root,))

    def test_symlinked_root_needs_the_junction_allowance(self) -> None:
        with self.assertRaises(ValueError):
            self.capture("rootlink")
        root = self.capture("rootlink", junction=True)
        with request_path_authority.accredit_exact_paths((str(self.root / "rootlink/a"),), (root,)):
            pass

    def test_root_replaced_after_sealing_is_refused(self) -> None:
        root = self.capture("root")
        os.rename(self.root / "root", self.root / "moved")
        (self.root / "root" / "a" / "b").mkdir(parents=True)
        with self.assertRaises(ValueError):
            request_path_authority.accredit_exact_paths((str(self.root / "root/a/b"),), (root,))


@unittest.skipUnless(POSIX, "Linux host probes")
class HostProbeTest(unittest.TestCase):
    def test_other_users_sockets_are_labelled_by_count(self) -> None:
        rows = [
            {"port": 323, "pid": None, "name": None},
            {"port": 5353, "pid": 11, "name": "brave"},
            {"port": 5353, "pid": None, "name": None},
            {"port": 9000, "pid": None, "name": None},
            {"port": 9001, "pid": None, "name": None},
            {"port": 9001, "pid": None, "name": None},
        ]
        foreign = {323: [0], 5353: [102], 9001: [0]}
        with patch.object(orphan_guard, "_udp_foreign_sockets_by_port", return_value=foreign):
            orphan_guard._label_other_users_sockets(rows)
        self.assertEqual(rows[0]["name"], "uid:0")
        self.assertEqual(rows[2]["name"], "uid:102")
        # Port 9000: no foreign socket, so it is this user's and stays unnamed.
        self.assertIsNone(rows[3]["name"])
        # Port 9001: two unnamed rows, one foreign socket: neither can be named.
        self.assertEqual([rows[4]["name"], rows[5]["name"]], [None, None])

    def test_uid_label_is_attributed_only_without_a_pid(self) -> None:
        self.assertFalse(_holder_unattributed(None, "uid:0"))
        self.assertTrue(_holder_unattributed(None, None))
        self.assertTrue(_holder_unattributed(None, "zsh"))
        self.assertTrue(_holder_unattributed(5, None))
        self.assertFalse(_holder_unattributed(5, "DayZServer"))

    def test_proc_image_name_uses_the_windows_argv0_under_wine(self) -> None:
        def fake_readlink(path: str) -> str:
            return "/usr/lib/wine/wine64-preloader"

        cmdline = b"Z:\\home\\u\\DayZ\\DayZ_x64.exe\0-connect=127.0.0.1\0"
        with patch.object(orphan_guard.os, "readlink", fake_readlink), patch(
            "builtins.open", unittest.mock.mock_open(read_data=cmdline)
        ):
            self.assertEqual(orphan_guard._proc_image_name(4242), "DayZ_x64.exe")

    def test_this_process_is_listed_with_its_parent(self) -> None:
        entries = orphan_guard._proc_process_entries()
        mine = [entry for entry in entries if entry[0] == os.getpid()]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0][1], os.getppid())

    def test_linux_server_executable_is_derived_from_the_game_path(self) -> None:
        def derived(game_path: str) -> object:
            stub = types.SimpleNamespace(
                game_path=Path(game_path), _LINUX_SERVER_APPS=ProcessLifecycle._LINUX_SERVER_APPS
            )
            return ProcessLifecycle._linux_server_executable(stub)

        self.assertEqual(
            derived("/s/steamapps/common/DayZ"), Path("/s/steamapps/common/DayZServer/DayZServer").resolve()
        )
        self.assertEqual(
            derived("/s/steamapps/common/DayZ Exp"),
            Path("/s/steamapps/common/DayZ Server Exp/DayZServer").resolve(),
        )
        self.assertIsNone(derived("/s/steamapps/common/Other"))


if __name__ == "__main__":
    unittest.main()
