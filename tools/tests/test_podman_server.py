"""Offline tests of dayz_mcp.podman_server: argv, serverDZ.cfg, tree layout, bridge config.

No podman and no DayZ: a fake server install in a temporary directory.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from dayz_mcp import podman_server as ps

STOCK_CFG = """hostname = "My Server";
password = "";
passwordAdmin = "";
maxPlayers = 60;
class Missions
{
    class DayZ
    {
        template="dayzOffline.chernarusplus";
    };
};
"""


class ServerArgsTest(unittest.TestCase):
    def test_relative_mod_names_split_by_role(self):
        mods = [ps.Mod("@A", Path("/a")), ps.Mod("@B", Path("/b"), server=True), ps.Mod("@C", Path("/c"))]
        args = ps.server_args(ps.Ports(30000, 30017, 30034), mods, ["-freezecheck"])
        self.assertEqual(args[:4], ["-config=serverDZ.cfg", "-profiles=profiles", "-port=30000",
                                    "-BEpath=profiles/battleye"])
        self.assertIn("-mod=@A;@C", args)
        self.assertIn("-servermod=@B", args)
        self.assertEqual(args[-1], "-freezecheck")

    def test_no_mod_flags_without_mods(self):
        args = ps.server_args(ps.Ports(1, 2, 3), [], [])
        self.assertFalse(any(a.startswith(("-mod=", "-servermod=")) for a in args))


class ServerCfgTest(unittest.TestCase):
    def test_template_query_port_hostname_and_random_passwords(self):
        out = ps.server_cfg(STOCK_CFG, "dayzOffline.enoch", 27979)
        self.assertIn('template="dayzOffline.enoch";', out)
        self.assertIn("steamQueryPort = 27979;", out)
        self.assertIn('hostname = "[dayz-mcp] My Server";', out)
        self.assertNotIn('password = "";', out)
        self.assertNotIn('passwordAdmin = "";', out)
        self.assertIn("maxPlayers = 60;", out)

    def test_existing_query_port_is_replaced_not_duplicated(self):
        out = ps.server_cfg("steamQueryPort = 2305;\n" + STOCK_CFG, "dayzOffline.chernarusplus", 40000)
        self.assertEqual(out.count("steamQueryPort"), 1)
        self.assertIn("steamQueryPort = 40000;", out)


class BuildTreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="podman-server-test-")
        root = Path(self.tmp.name)
        self.srv = root / "DayZServer"
        (self.srv / "addons").mkdir(parents=True)
        (self.srv / "DayZServer").write_text("#!/bin/sh\n")
        (self.srv / "serverDZ.cfg").write_text(STOCK_CFG)
        (self.srv / "keys").mkdir()
        (self.srv / "keys" / "dayz.bikey").write_bytes(b"k")
        (self.srv / "battleye").mkdir()
        (self.srv / "battleye" / "BEServer_x64.so").write_bytes(b"x")
        (self.srv / "mpmissions" / "dayzOffline.chernarusplus").mkdir(parents=True)
        (self.srv / "mpmissions" / "dayzOffline.chernarusplus" / "init.c").write_text("void main() {}\n")
        (self.srv / "profiles").mkdir()
        (self.srv / "@Stale").mkdir()
        self.mod = root / "build" / "@Mine"
        (self.mod / "addons").mkdir(parents=True)
        (self.mod / "keys").mkdir()
        (self.mod / "keys" / "mine.bikey").write_bytes(b"m")
        self.tree = root / "tree"

    def tearDown(self):
        self.tmp.cleanup()

    def test_layout(self):
        spec = ps.RunSpec(server_dir=self.srv, tree=self.tree, mods=[ps.Mod("@Mine", self.mod)])
        ps.build_tree(spec, ps.Ports(30000, 30017, 30034))
        t = self.tree
        self.assertTrue((t / "addons").is_symlink())
        self.assertTrue((t / "DayZServer").is_symlink())
        self.assertFalse((t / "@Stale").exists(), "the install's own @mods are not linked")
        self.assertTrue((t / "battleye").is_dir() and not (t / "battleye").is_symlink())
        self.assertTrue((t / "battleye" / "BEServer_x64.so").is_symlink())
        self.assertEqual(sorted(p.name for p in (t / "keys").iterdir()), ["dayz.bikey", "mine.bikey"])
        mission = t / "mpmissions" / "dayzOffline.chernarusplus" / "init.c"
        self.assertTrue(mission.is_file() and not mission.is_symlink(), "the mission is a copy")
        self.assertTrue((t / "@Mine").is_symlink())
        self.assertFalse((t / "profiles").is_symlink())
        be = (t / "profiles" / "battleye" / "beserver_x64.cfg").read_text()
        self.assertIn("RConPort 30034", be)
        self.assertIn("RConIP 127.0.0.1", be)
        cfg = t / "serverDZ.cfg"
        if os.name != "nt":  # no POSIX modes on Windows
            self.assertEqual(stat.S_IMODE(os.stat(cfg).st_mode), 0o600)
        self.assertIn("steamQueryPort = 30017;", cfg.read_text())

    def test_tree_inside_install_is_refused(self):
        spec = ps.RunSpec(server_dir=self.srv, tree=self.srv / "tree")
        with self.assertRaises(ps.PodmanServerError):
            ps.build_tree(spec, ps.Ports(1, 2, 3))

    def test_bad_mod_name_is_refused(self):
        spec = ps.RunSpec(server_dir=self.srv, tree=self.tree, mods=[ps.Mod("Mine", self.mod)])
        with self.assertRaises(ps.PodmanServerError):
            ps.build_tree(spec, ps.Ports(1, 2, 3))

    def test_second_build_keeps_mission_storage(self):
        spec = ps.RunSpec(server_dir=self.srv, tree=self.tree)
        ps.build_tree(spec, ps.Ports(1, 2, 3))
        storage = self.tree / "mpmissions" / "dayzOffline.chernarusplus" / "storage_1"
        storage.mkdir()
        ps.build_tree(spec, ps.Ports(1, 2, 3))
        self.assertTrue(storage.is_dir())

    def test_bridge_config(self):
        path = ps.bridge_config(self.tree, 33680, "secret", "8c4f2a1e-0000-4000-8000-000000000000")
        doc = json.loads(path.read_text())
        self.assertEqual(doc["url"], "http://127.0.0.1:33680/")
        self.assertEqual(doc["profile"], "test")
        if os.name != "nt":  # no POSIX modes on Windows
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class MissionLoadedTest(unittest.TestCase):
    def test_storage_line_or_mission_module(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            self.assertFalse(ps.mission_loaded(p))
            (p / "DayZServer_x.RPT").write_text("[StorageDirs] :: Selected storage directory x\n")
            self.assertTrue(ps.mission_loaded(p))


class LifecycleMissionTest(unittest.TestCase):
    """An absolute -mission boots without init.c's main (DayZServer 1.30, 2026-11-06)."""

    def test_the_mission_is_mounted_into_the_tree_and_named_relative(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            server = root / "server"
            server.mkdir()
            (server / ps.SERVER_BINARY).write_text("")
            config = root / "serverDZ.cfg"
            config.write_text("")
            mission = root / "missions" / "dayzOffline.chernarusplus"
            mission.mkdir(parents=True)
            calls = []

            def fake_podman(_podman, *command, check=True):
                calls.append(command)
                return mock.Mock(returncode=1, stderr="stop here", stdout="")

            argv = [
                str(server / ps.SERVER_BINARY), "-server", f"-config={config}",
                f"-profiles={root / 'profiles'}", f"-mission={mission}", "-port=2302",
            ]
            with mock.patch.object(ps, "_runtime_image_id", return_value="image"), \
                    mock.patch.object(ps, "_state_root", return_value=root / "runs"), \
                    mock.patch.object(ps, "_remove_stale_trees"), \
                    mock.patch.object(ps, "_podman", fake_podman), \
                    self.assertRaises(ps.PodmanServerError):
                ps.launch_lifecycle_process(argv, str(server))
            run = next(command for command in calls if command[0] == "run")
            self.assertIn("-mission=mpmissions/dayzOffline.chernarusplus", run)
            self.assertFalse(any(str(item).startswith(f"-mission={root}") for item in run))
            name = run[run.index("--name") + 1]
            self.assertIn(
                f"{mission}:{root / 'runs' / name / 'mpmissions' / mission.name}", run
            )


class RemoveStaleTreesTest(unittest.TestCase):
    """Only a container podman reports absent loses its tree (``container exists`` exits 1)."""

    def test_only_an_absent_container_loses_its_tree(self):
        codes = {"absent": 1, "present": 0, "error": 125}
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            for key in codes:
                (root / (ps.CONTAINER_PREFIX + key)).mkdir()
            (root / "unrelated").mkdir()
            calls = []

            def fake_podman(_podman, *command, check=True):
                calls.append(command)
                return mock.Mock(returncode=codes[command[-1][len(ps.CONTAINER_PREFIX):]], stdout="", stderr="")

            with mock.patch.object(ps, "_podman", fake_podman):
                ps._remove_stale_trees(root, "podman")
            self.assertFalse((root / (ps.CONTAINER_PREFIX + "absent")).exists())
            self.assertTrue((root / (ps.CONTAINER_PREFIX + "present")).is_dir())
            self.assertTrue((root / (ps.CONTAINER_PREFIX + "error")).is_dir())
            self.assertTrue((root / "unrelated").is_dir())
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(command[:2] == ("container", "exists") for command in calls))


class LifecycleImageTest(unittest.TestCase):
    def test_a_missing_runtime_image_is_refused_not_built(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            server = root / "server"
            server.mkdir()
            (server / ps.SERVER_BINARY).write_text("")
            config = root / "serverDZ.cfg"
            config.write_text("")
            mission = root / "missions" / "dayzOffline.chernarusplus"
            mission.mkdir(parents=True)
            calls = []

            def fake_podman(_podman, *command, check=True):
                calls.append(command)
                return mock.Mock(returncode=1, stderr="", stdout="")

            argv = [
                str(server / ps.SERVER_BINARY), f"-config={config}",
                f"-profiles={root / 'profiles'}", f"-mission={mission}", "-port=2302",
            ]
            with mock.patch.object(ps, "_state_root", return_value=root / "runs"), \
                    mock.patch.object(ps, "_podman", fake_podman), \
                    self.assertRaisesRegex(ps.PodmanServerError, "runtime_image_missing"):
                ps.launch_lifecycle_process(argv, str(server))
            self.assertEqual([command[:2] for command in calls], [("image", "exists")])
            self.assertFalse((root / "runs").exists(), "a run tree was staged without an image")

    def test_a_podman_error_is_not_read_as_a_missing_image(self):
        def fake_podman(_podman, *command, check=True):
            if check:
                raise subprocess.CalledProcessError(125, ["podman", *command], stderr="database is locked")
            return mock.Mock(returncode=125, stderr="database is locked", stdout="")

        with mock.patch.object(ps, "_podman", fake_podman), self.assertRaises(subprocess.CalledProcessError):
            ps._runtime_image_id("podman")

    def test_an_existing_image_is_used_without_a_build(self):
        calls = []

        def fake_podman(_podman, *command, check=True):
            calls.append(command)
            return mock.Mock(returncode=0, stderr="", stdout="sha256:abc\n")

        with mock.patch.object(ps, "_podman", fake_podman):
            self.assertEqual(ps._runtime_image_id("podman"), "sha256:abc")
            self.assertEqual(ps.ensure_image("podman"), "sha256:abc")
        self.assertFalse(any(command[0] == "build" for command in calls))


class FreePortsTest(unittest.TestCase):
    def test_spacing_and_defaults(self):
        ports = ps.free_ports(3)
        self.assertEqual(len(ports), 3)
        self.assertTrue(all(b - a >= 10 for a, b in zip(ports, ports[1:])))
        self.assertFalse(any(2300 <= p <= 2310 or 27015 <= p <= 27017 for p in ports))


if __name__ == "__main__":
    unittest.main()
