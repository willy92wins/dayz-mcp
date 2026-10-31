"""B1-P2: profile=test does not survive release, a failed prepare, or crash recovery.

The harness still writes profile=test for the life of the instance. These
tests stay on disk and in-process: no DayZ, no PBO, no external network.
The addon reads ConfigIsTestProfile once, from OnMissionStart, so putting
the installer file back does not clear godmode on a server that is still
running. That hot path is an addon change and is not implemented here.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import loopback, runtime_state
from dayz_mcp.instance_fence import BindingPrepareError
from tests._addon_paths import addon_root

_IDENTITY = {
    "platform": "codex",
    "pid": 11,
    "ppid": 1,
    "started_at_utc": "2026-07-15T00:00:00Z",
    "session_id": "A",
    "task_label": "b1p2",
}
_RUN = "run-b1"
_SEED_KEY = "seed-key-b1p2-not-for-disk"


def _state(key: str = "live-key", port: int | bool | None = 8765) -> loopback.ServerState:
    return loopback.ServerState(key, config_port=port)


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _prior_path(config: Path) -> Path:
    return config.with_name(config.name + ".profile-prior")


def _prior(config: Path) -> dict | None:
    path = _prior_path(config)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _installer(**extra: object) -> dict:
    payload: dict[str, object] = {
        "url": "http://127.0.0.1:2402/",
        "key": "installer-key",
        "pollHz": 5,
    }
    payload.update(extra)
    return payload


def _permission(winerror: int) -> PermissionError:
    error = PermissionError(13, "Access is denied")
    error.winerror = winerror
    return error


def _queued_cmds(state: loopback.ServerState) -> list[object]:
    cmds: list[object] = []
    queues = list(state._legacy_queues.values()) + list(state._bound_queues.values())
    for queue in queues:
        for item in queue:
            if isinstance(item, dict):
                cmds.append(item.get("cmd"))
    return cmds


def _tree_text(root: Path) -> str:
    parts: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        info = path.lstat()
        if getattr(info, "st_file_attributes", 0) & 0x400:
            continue
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _remove_junction(link: Path) -> None:
    try:
        if link.exists():
            os.rmdir(link)
    except OSError:
        return


class _Case(unittest.TestCase):
    def _tmp(self) -> Path:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def _profiles(self, name: str = "profiles") -> Path:
        profiles = self._tmp() / name
        profiles.mkdir()
        return profiles

    def _leaf(self, root: Path, role: str = "_server", leaf: str = "leaf") -> Path:
        profiles = root / role / leaf
        profiles.mkdir(parents=True)
        return profiles

    def _junction(self, link: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        link.parent.mkdir(parents=True, exist_ok=True)
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
        )
        detail = created.stderr.decode("utf-8", errors="replace") or created.stdout.decode(
            "utf-8", errors="replace"
        )
        self.assertEqual(created.returncode, 0, detail)
        self.addCleanup(_remove_junction, link)

    def _assert_no_tmp(self, directory: Path) -> None:
        names = [entry.name for entry in directory.iterdir()]
        self.assertEqual([name for name in names if ".tmp." in name], [])

    def _release_matches(self, state: loopback.ServerState, config: Path, original: dict) -> None:
        self.assertEqual(state.profile_release_errors, [])
        self.assertFalse(_prior_path(config).exists())
        self.assertEqual(_load(config), original)
        self.assertNotIn("player_godmode", _queued_cmds(state))
        self._assert_no_tmp(config.parent)


class ReleaseRestoresInstallerProfileTest(_Case):
    def test_profile_is_test_only_while_the_instance_is_held(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer(note="keep-me")
        _write(config, original)
        state = _state()
        minted = state.prepare(_RUN, "server", str(profiles))
        held = _load(config)
        self.assertIsNotNone(held)
        assert held is not None
        self.assertEqual(held["profile"], "test")
        self.assertEqual(held["instance"], minted)
        self.assertEqual(held["url"], original["url"])
        self.assertEqual(held["key"], "installer-key")
        self.assertEqual(held["note"], "keep-me")
        prior = _prior(config)
        self.assertIsNotNone(prior)
        assert prior is not None
        self.assertEqual(prior["version"], 1)
        self.assertEqual(prior["kind"], "present")
        self.assertNotEqual(prior["document"].get("profile"), "test")
        self.assertNotIn("instance", prior["document"])
        self.assertEqual(_prior_path(config).parent, config.parent)
        state.retire_run(_RUN, "stopped")
        self._release_matches(state, config, original)

    def test_release_restores_another_profile_and_rejects_lookalikes(self) -> None:
        samples = (
            {"profile": "live"},
            {"profile": "Test"},
            {"profile": "test "},
            {"profile": "test\u200b"},
            {"profile": True},
            {"profile": ["test"]},
            {"meta": {"profile": "test"}},
            {"banner": "profile=test"},
        )
        for extra in samples:
            with self.subTest(extra=extra):
                profiles = self._profiles()
                config = profiles / "dayz_mcp.json"
                original = _installer(**extra)
                _write(config, original)
                state = _state()
                state.prepare(_RUN, "server", str(profiles))
                self.assertEqual(_load(config)["profile"], "test")
                state.retire_run(_RUN, "stopped")
                self._release_matches(state, config, original)

    def test_retire_role_restores_when_it_is_the_last_holder(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer()
        _write(config, original)
        state = _state()
        state.prepare(_RUN, "server", str(profiles))
        state.retire_role(_RUN, "server", "stopped")
        self._release_matches(state, config, original)

    def test_one_role_release_keeps_the_other_leaf_on_test(self) -> None:
        root = self._tmp()
        server = self._leaf(root, "_server")
        client = self._leaf(root, "_client")
        server_cfg = server / "dayz_mcp.json"
        client_cfg = client / "dayz_mcp.json"
        server_original = _installer(role="server")
        client_original = _installer(role="client", url="http://127.0.0.1:2403/")
        _write(server_cfg, server_original)
        _write(client_cfg, client_original)
        state = _state()
        state.prepare(_RUN, "server", str(server))
        state.prepare(_RUN, "client", str(client))
        self.assertEqual(_load(server_cfg)["profile"], "test")
        self.assertEqual(_load(client_cfg)["profile"], "test")
        state.retire_role(_RUN, "server", "stopped")
        self.assertEqual(_load(server_cfg), server_original)
        self.assertEqual(_load(client_cfg)["profile"], "test")
        self.assertEqual(state.profile_release_errors, [])
        state.retire_role(_RUN, "client", "stopped")
        self.assertEqual(_load(client_cfg), client_original)
        self.assertEqual(state.profile_release_errors, [])

    def test_a_second_run_does_not_snapshot_profile_test(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer()
        _write(config, original)
        state = _state()
        state.prepare("run-a", "server", str(profiles))
        state.prepare("run-b", "client", str(profiles))
        prior = _prior(config)
        self.assertIsNotNone(prior)
        assert prior is not None
        self.assertNotEqual(prior["document"].get("profile"), "test")
        self.assertNotIn("instance", prior["document"])
        state.retire_run("run-a", "stopped")
        self.assertEqual(_load(config)["profile"], "test")
        self.assertEqual(state.profile_release_errors, [])
        state.retire_run("run-b", "stopped")
        self._release_matches(state, config, original)

    def test_failed_prepare_restores_the_installer_file(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer(note="keep-me")
        _write(config, original)
        state = _state()
        real = loopback.json.loads

        def loads(text: str, *args: object, **kwargs: object) -> object:
            result = real(text, *args, **kwargs)
            if (
                isinstance(result, dict)
                and result.get("profile") == "test"
                and isinstance(result.get("instance"), str)
            ):
                raise ValueError("reread failed")
            return result

        with patch.object(loopback.json, "loads", loads):
            with self.assertRaises(BindingPrepareError) as raised:
                state.prepare(_RUN, "server", str(profiles))
        self.assertEqual(raised.exception.code, "instance_config_mismatch")
        self._release_matches(state, config, original)

    def test_a_second_release_does_not_eat_the_installer_file(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer()
        _write(config, original)
        state = _state()
        state.prepare(_RUN, "server", str(profiles))
        state.retire_run(_RUN, "stopped")
        state.retire_run(_RUN, "stopped")
        self._release_matches(state, config, original)

    def test_live_holder_is_not_cleared_by_crash_recovery(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        _write(config, _installer())
        state = _state()
        state.prepare(_RUN, "server", str(profiles))
        errors = state.recover_crashed_profile(str(profiles))
        self.assertEqual(errors, [])
        self.assertEqual(_load(config)["profile"], "test")
        state.retire_run(_RUN, "stopped")
        self.assertNotEqual(_load(config).get("profile"), "test")

    def test_planted_absent_prior_does_not_delete_the_installer_file(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer()
        _write(config, original)
        _write(_prior_path(config), {"version": 1, "kind": "absent"})
        state = _state()
        state.prepare(_RUN, "server", str(profiles))
        prior = _prior(config)
        self.assertIsNotNone(prior)
        assert prior is not None
        self.assertEqual(prior["kind"], "present")
        self.assertEqual(prior["document"], original)
        state.retire_run(_RUN, "stopped")
        self._release_matches(state, config, original)

    def test_poisoned_prior_cannot_come_back_as_profile_test(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer()
        _write(config, original)
        _write(
            _prior_path(config),
            {
                "version": 1,
                "kind": "present",
                "document": {
                    "profile": "test",
                    "url": "http://evil/",
                    "key": "stolen",
                    "pollHz": 1,
                },
            },
        )
        state = _state()
        state.prepare(_RUN, "server", str(profiles))
        prior = _prior(config)
        self.assertIsNotNone(prior)
        assert prior is not None
        self.assertNotEqual(prior["document"].get("profile"), "test")
        self.assertNotIn("evil", json.dumps(prior))
        state.retire_run(_RUN, "stopped")
        self._release_matches(state, config, original)

    def test_crashed_file_with_a_poisoned_prior_is_not_restored_as_test(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        _write(config, {"profile": "test", "url": "http://127.0.0.1:1/", "key": "k", "pollHz": 5})
        _write(
            _prior_path(config),
            {
                "version": 1,
                "kind": "present",
                "document": {"profile": "test", "url": "http://evil/", "key": "stolen", "pollHz": 1},
            },
        )
        state = _state()
        state.prepare(_RUN, "server", str(profiles))
        state.retire_run(_RUN, "stopped")
        restored = _load(config)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertNotIn("profile", restored)
        self.assertNotEqual(restored.get("profile"), "test")
        self.assertEqual(state.profile_release_errors, [])


class SeededFileAndMissionCopyTest(_Case):
    def test_seeded_file_is_deleted_on_release_and_the_key_goes_with_it(self) -> None:
        root = self._tmp()
        profiles = self._leaf(root)
        (root / "mpmissions").mkdir()
        mission = root / "mpmissions" / "dayzOffline.chernarusplus"
        mission_cfg = mission / "dayz_mcp.json"
        _write(mission_cfg, {"profile": "test", "keep": "mission-field"})
        nested = mission / "extra" / "dayz_mcp.json"
        _write(nested, {"profile": "test", "keep": "nested"})
        other = root / "mpmissions" / "dayzOffline.enoch" / "dayz_mcp.json"
        _write(other, {"profile": "live", "keep": 1})
        empty = root / "mpmissions" / "empty.mission" / "dayz_mcp.json"
        empty.parent.mkdir(parents=True)
        empty.write_bytes(b"")
        state = _state(_SEED_KEY)
        state.prepare(_RUN, "server", str(profiles))
        config = profiles / "dayz_mcp.json"
        self.assertEqual(_load(config)["profile"], "test")
        self.assertEqual(_load(config)["key"], _SEED_KEY)
        self.assertEqual(_prior(config)["kind"], "absent")
        self.assertIn(_SEED_KEY, config.read_text(encoding="utf-8"))
        state.retire_run(_RUN, "stopped")
        self.assertFalse(config.exists())
        self.assertFalse(_prior_path(config).exists())
        self.assertEqual(state.profile_release_errors, [])
        self.assertNotIn(_SEED_KEY, _tree_text(root))
        mission_doc = _load(mission_cfg)
        self.assertIsNotNone(mission_doc)
        assert mission_doc is not None
        self.assertNotIn("profile", mission_doc)
        self.assertEqual(mission_doc["keep"], "mission-field")
        self.assertEqual(_load(other), {"profile": "live", "keep": 1})
        self.assertEqual(nested.read_text(encoding="utf-8"), json.dumps({"profile": "test", "keep": "nested"}))
        self.assertEqual(empty.read_bytes(), b"")
        self._assert_no_tmp(profiles)
        self._assert_no_tmp(mission)

    def test_failed_prepare_deletes_a_seeded_file(self) -> None:
        profiles = self._profiles()
        state = _state(_SEED_KEY)
        real = loopback.json.loads

        def loads(text: str, *args: object, **kwargs: object) -> object:
            result = real(text, *args, **kwargs)
            if (
                isinstance(result, dict)
                and result.get("profile") == "test"
                and isinstance(result.get("instance"), str)
            ):
                raise ValueError("reread failed")
            return result

        with patch.object(loopback.json, "loads", loads):
            with self.assertRaises(BindingPrepareError) as raised:
                state.prepare(_RUN, "server", str(profiles))
        self.assertEqual(raised.exception.code, "instance_config_mismatch")
        config = profiles / "dayz_mcp.json"
        self.assertFalse(config.exists())
        self.assertFalse(_prior_path(config).exists())
        self.assertEqual(state.profile_release_errors, [])
        self.assertNotIn(_SEED_KEY, _tree_text(profiles))

    def test_relative_mission_path_cannot_escape_the_profile_directory(self) -> None:
        root = self._tmp()
        profiles = self._leaf(root)
        outside = root / "outside"
        outside_cfg = outside / "dayz_mcp.json"
        _write(outside_cfg, {"profile": "test", "secret": "stay"})
        secret = root / "secret.txt"
        secret.write_text("do-not-touch", encoding="utf-8")
        state = _state(_SEED_KEY)
        state.prepare(_RUN, "server", str(profiles))
        state.retire_run(_RUN, "stopped")
        fresh = _state()
        _write(profiles / "dayz_mcp.json", {"profile": "test", "key": "left"})
        _write(_prior_path(profiles / "dayz_mcp.json"), {"version": 1, "kind": "absent"})
        errors = fresh.recover_crashed_profile(str(profiles), "..\\outside")
        self.assertEqual(_load(outside_cfg), {"profile": "test", "secret": "stay"})
        self.assertEqual(secret.read_text(encoding="utf-8"), "do-not-touch")
        self.assertEqual(errors, [])
        self.assertFalse((profiles / "dayz_mcp.json").exists())
        errors = fresh.recover_crashed_profile(str(profiles), str(secret))
        self.assertEqual(secret.read_text(encoding="utf-8"), "do-not-touch")
        self.assertFalse((secret.parent / "secret.txt" / "dayz_mcp.json").exists())
        self.assertEqual(errors, [])

    def test_named_mission_outside_mpmissions_loses_profile_test(self) -> None:
        root = self._tmp()
        profiles = self._leaf(root)
        (root / "mpmissions").mkdir()
        mission = root / "named-mission"
        mission_cfg = mission / "dayz_mcp.json"
        _write(mission_cfg, {"profile": "test", "keep": "yes"})
        state = _state(_SEED_KEY)
        state.prepare(_RUN, "server", str(profiles))
        # The process died. The sidecar still says the file was seeded.
        fresh = _state()
        errors = fresh.recover_crashed_profile(str(profiles), str(mission))
        self.assertEqual(errors, [])
        self.assertFalse((profiles / "dayz_mcp.json").exists())
        self.assertEqual(_load(mission_cfg), {"keep": "yes"})

    def test_junction_mission_directory_is_not_followed(self) -> None:
        root = self._tmp()
        profiles = self._leaf(root)
        missions = root / "mpmissions"
        missions.mkdir()
        plain = missions / "dayzOffline.chernarusplus" / "dayz_mcp.json"
        _write(plain, {"profile": "test", "keep": "plain"})
        target = root / "outside-target"
        target_cfg = target / "dayz_mcp.json"
        _write(target_cfg, {"profile": "test", "secret": "do-not-rewrite"})
        self._junction(missions / "evil", target)
        state = _state(_SEED_KEY)
        state.prepare(_RUN, "server", str(profiles))
        state.retire_run(_RUN, "stopped")
        self.assertFalse((profiles / "dayz_mcp.json").exists())
        self.assertNotIn("profile", _load(plain) or {})
        self.assertEqual(_load(target_cfg), {"profile": "test", "secret": "do-not-rewrite"})
        self.assertEqual(state.profile_release_errors, [])

    def test_another_profile_leaf_is_left_alone(self) -> None:
        root = self._tmp()
        profiles = self._leaf(root, leaf="leaf")
        other = self._leaf(root, leaf="other-instance")
        other_cfg = other / "dayz_mcp.json"
        _write(other_cfg, {"profile": "test", "leaf": "other"})
        state = _state(_SEED_KEY)
        state.prepare(_RUN, "server", str(profiles))
        state.retire_run(_RUN, "stopped")
        self.assertEqual(_load(other_cfg), {"profile": "test", "leaf": "other"})
        self.assertFalse((profiles / "dayz_mcp.json").exists())

    def test_sibling_role_prior_is_restored_with_the_run(self) -> None:
        root = self._tmp()
        server = self._leaf(root, "_server")
        client = self._leaf(root, "_client")
        (root / "mpmissions").mkdir()
        client_cfg = client / "dayz_mcp.json"
        _write(client_cfg, {"profile": "test", "url": "http://evil/"})
        _write(
            _prior_path(client_cfg),
            {
                "version": 1,
                "kind": "present",
                "document": {
                    "url": "http://127.0.0.1:1/",
                    "key": "client-key",
                    "pollHz": 5,
                    "profile": "live",
                },
            },
        )
        state = _state(_SEED_KEY)
        state.prepare(_RUN, "server", str(server))
        state.retire_run(_RUN, "stopped")
        self.assertFalse((server / "dayz_mcp.json").exists())
        self.assertEqual(
            _load(client_cfg),
            {"url": "http://127.0.0.1:1/", "key": "client-key", "pollHz": 5, "profile": "live"},
        )
        self.assertEqual(state.profile_release_errors, [])

    def test_sibling_without_a_prior_is_stripped_and_reported(self) -> None:
        root = self._tmp()
        server = self._leaf(root, "_server")
        client = self._leaf(root, "_client")
        (root / "mpmissions").mkdir()
        client_cfg = client / "dayz_mcp.json"
        _write(client_cfg, {"profile": "test", "url": "http://127.0.0.1:1/", "keep": 7})
        state = _state(_SEED_KEY)
        state.prepare(_RUN, "server", str(server))
        state.retire_run(_RUN, "stopped")
        restored = _load(client_cfg)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertNotIn("profile", restored)
        self.assertEqual(restored["keep"], 7)
        self.assertTrue(any("profile_prior_missing" in item for item in state.profile_release_errors))
        self.assertFalse((server / "dayz_mcp.json").exists())

    def test_corrupt_mission_file_blocks_the_seeded_delete_and_is_reported(self) -> None:
        root = self._tmp()
        profiles = self._leaf(root)
        (root / "mpmissions").mkdir()
        mission_cfg = root / "mpmissions" / "dayzOffline.chernarusplus" / "dayz_mcp.json"
        mission_cfg.parent.mkdir(parents=True)
        mission_cfg.write_text("{not json", encoding="utf-8")
        state = _state(_SEED_KEY)
        state.prepare(_RUN, "server", str(profiles))
        state.retire_run(_RUN, "stopped")
        config = profiles / "dayz_mcp.json"
        document = _load(config)
        self.assertIsNotNone(document)
        assert document is not None
        self.assertNotIn("profile", document)
        self.assertEqual(document["key"], _SEED_KEY)
        self.assertTrue(_prior_path(config).is_file())
        self.assertEqual(mission_cfg.read_text(encoding="utf-8"), "{not json")
        self.assertTrue(any("mission_profile_unreadable" in item for item in state.profile_release_errors))
        self.assertTrue(any("profile_delete_blocked" in item for item in state.profile_release_errors))


class CrashRecoveryTest(_Case):
    def test_a_new_process_restores_the_installer_file_after_a_crash(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer(note="keep-me")
        _write(config, original)
        state = _state()
        state.prepare(_RUN, "server", str(profiles))
        self.assertEqual(_load(config)["profile"], "test")
        fresh = _state()
        errors = fresh.recover_crashed_profile(str(profiles))
        self.assertEqual(errors, [])
        self.assertEqual(_load(config), original)
        self.assertFalse(_prior_path(config).exists())

    def test_crash_recovery_deletes_a_seeded_file(self) -> None:
        profiles = self._profiles()
        state = _state(_SEED_KEY)
        state.prepare(_RUN, "server", str(profiles))
        config = profiles / "dayz_mcp.json"
        self.assertEqual(_prior(config)["kind"], "absent")
        fresh = _state()
        errors = fresh.recover_crashed_profile(str(profiles))
        self.assertEqual(errors, [])
        self.assertFalse(config.exists())
        self.assertFalse(_prior_path(config).exists())
        self.assertNotIn(_SEED_KEY, _tree_text(profiles))

    def test_assigning_lifecycle_recovers_a_dead_process(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer()
        _write(config, original)
        abandoned = _state()
        abandoned.prepare(_RUN, "server", str(profiles))
        self.assertEqual(_load(config)["profile"], "test")

        class Manifest:
            def list_runs(self) -> list[dict[str, str]]:
                return [{"profiles": str(profiles), "mission": ""}]

            def get(self, run_id: str) -> dict[str, str]:
                return {"profiles": str(profiles), "mission": ""}

        fresh = _state()
        fresh.lifecycle = SimpleNamespace(manifest=Manifest())
        self.assertEqual(_load(config), original)
        self.assertEqual(fresh.profile_release_errors, [])

    def test_startup_recovery_does_not_clear_a_live_holder(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer()
        _write(config, original)
        state = _state()
        state.prepare(_RUN, "server", str(profiles))

        class Manifest:
            def list_runs(self) -> list[dict[str, str]]:
                return [{"profiles": str(profiles), "mission": ""}]

        # The bridge reads the flag once per mission. Leaving the file as
        # profile=test while this process still holds it is what keeps the
        # running server's godmode on; disk is fixed only at release.
        state.lifecycle = SimpleNamespace(manifest=Manifest())
        self.assertEqual(_load(config)["profile"], "test")
        self.assertEqual(state.profile_release_errors, [])
        state.retire_run(_RUN, "stopped")
        self.assertEqual(_load(config), original)
        self.assertNotIn("player_godmode", _queued_cmds(state))

    def test_a_manifest_that_throws_is_reported_and_leaves_the_file(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        _write(config, {"profile": "test", "keep": 1})

        class Boom:
            def list_runs(self) -> list[dict[str, str]]:
                raise RuntimeError("manifest down")

        fresh = _state()
        fresh.lifecycle = SimpleNamespace(manifest=Boom())
        self.assertEqual(_load(config), {"profile": "test", "keep": 1})
        self.assertTrue(any("RuntimeError" in item for item in fresh.profile_release_errors))

    def test_missing_prior_strips_profile_test_and_reports(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        _write(config, {"profile": "test", "url": "http://127.0.0.1:1/", "keep": 3})
        fresh = _state()
        errors = fresh.recover_crashed_profile(str(profiles))
        self.assertTrue(any("profile_prior_missing" in item for item in errors))
        restored = _load(config)
        self.assertEqual(restored, {"url": "http://127.0.0.1:1/", "keep": 3})

    def test_corrupt_prior_is_not_a_silent_success(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        _write(config, {"profile": "test", "url": "http://127.0.0.1:1/", "keep": 4})
        sidecar = _prior_path(config)
        sidecar.write_text('{"version": 99}', encoding="utf-8")
        fresh = _state()
        errors = fresh.recover_crashed_profile(str(profiles))
        self.assertTrue(any("profile_prior_invalid" in item for item in errors))
        self.assertEqual(sidecar.read_text(encoding="utf-8"), '{"version": 99}')
        restored = _load(config)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertNotEqual(restored.get("profile"), "test")
        self.assertEqual(restored.get("keep"), 4)

    def test_empty_directory_and_a_bad_path_create_nothing(self) -> None:
        profiles = self._profiles()
        fresh = _state()
        self.assertEqual(fresh.recover_crashed_profile(str(profiles)), [])
        self.assertEqual(list(profiles.iterdir()), [])
        errors = fresh.recover_crashed_profile("profiles")
        self.assertTrue(errors)
        self.assertIn("profiles_dir", errors[0])
        errors = fresh.recover_crashed_profile("")
        self.assertTrue(any("profiles_dir" in item for item in errors))
        errors = fresh.recover_crashed_profile(None)  # type: ignore[arg-type]
        self.assertTrue(errors)
        self.assertEqual(list(profiles.iterdir()), [])
        not_dir = self._tmp() / "not-a-dir"
        not_dir.write_text("x", encoding="utf-8")
        errors = fresh.recover_crashed_profile(str(not_dir))
        self.assertTrue(any("profiles_dir_not_plain" in item for item in errors))
        self.assertEqual(not_dir.read_text(encoding="utf-8"), "x")


class FailClosedAbuseTest(_Case):
    def test_relative_profiles_dir_is_refused(self) -> None:
        state = _state()
        with self.assertRaises(BindingPrepareError) as raised:
            state.prepare(_RUN, "server", "profiles")
        self.assertEqual(raised.exception.code, "instance_config_missing")

    def test_oversized_config_is_not_rewritten(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        payload = '{"k":"' + ("A" * (loopback._PROFILE_PRIOR_MAX_BYTES + 8)) + '"}'
        config.write_text(payload, encoding="utf-8")
        before = config.read_bytes()
        state = _state()
        with self.assertRaises(BindingPrepareError) as raised:
            state.prepare(_RUN, "server", str(profiles))
        self.assertEqual(raised.exception.code, "instance_config_missing")
        self.assertEqual(config.read_bytes(), before)
        self.assertNotIn(b'"profile"', before)

    def test_a_directory_named_as_the_config_is_not_followed(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        config.mkdir()
        (config / "secret.txt").write_text("stay", encoding="utf-8")
        state = _state()
        with self.assertRaises(BindingPrepareError) as raised:
            state.prepare(_RUN, "server", str(profiles))
        self.assertEqual(raised.exception.code, "instance_config_missing")
        self.assertEqual((config / "secret.txt").read_text(encoding="utf-8"), "stay")
        self.assertFalse((config / "profile").exists())

    def test_junction_profiles_dir_is_not_written_through(self) -> None:
        root = self._tmp()
        target = root / "real-profiles"
        target.mkdir()
        link = root / "linked-profiles"
        self._junction(link, target)
        state = _state(_SEED_KEY)
        with self.assertRaises(BindingPrepareError) as raised:
            state.prepare(_RUN, "server", str(link))
        self.assertEqual(raised.exception.code, "instance_config_missing")
        self.assertFalse((target / "dayz_mcp.json").exists())
        self.assertNotIn(_SEED_KEY, _tree_text(target))

    def test_junction_config_path_is_refused(self) -> None:
        profiles = self._profiles()
        target = self._tmp() / "config-target"
        target.mkdir()
        (target / "secret.txt").write_text("stay", encoding="utf-8")
        link = profiles / "dayz_mcp.json"
        self._junction(link, target)
        state = _state(_SEED_KEY)
        with self.assertRaises(BindingPrepareError) as raised:
            state.prepare(_RUN, "server", str(profiles))
        self.assertEqual(raised.exception.code, "instance_config_missing")
        self.assertEqual((target / "secret.txt").read_text(encoding="utf-8"), "stay")
        self.assertFalse((target / "dayz_mcp.json").exists())

    def test_junction_sidecar_is_not_read_and_profile_test_is_not_kept(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        _write(config, {"profile": "test", "keep": 9})
        target = self._tmp() / "sidecar-target"
        target.mkdir()
        (target / "secret.txt").write_text("prior-secret", encoding="utf-8")
        self._junction(_prior_path(config), target)
        fresh = _state()
        errors = fresh.recover_crashed_profile(str(profiles))
        self.assertTrue(any("profile_prior_invalid" in item for item in errors))
        restored = _load(config)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertNotEqual(restored.get("profile"), "test")
        self.assertEqual(restored.get("keep"), 9)
        self.assertEqual((target / "secret.txt").read_text(encoding="utf-8"), "prior-secret")

    def test_prior_write_failure_does_not_publish_profile_test(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer()
        _write(config, original)
        before = config.read_bytes()
        state = _state()
        real = runtime_state.os.replace

        def deny(src: str, dst: str) -> None:
            raise _permission(99)

        with patch.object(runtime_state.os, "replace", deny):
            with self.assertRaises(BindingPrepareError) as raised:
                state.prepare(_RUN, "server", str(profiles))
        self.assertEqual(raised.exception.code, "instance_config_missing")
        self.assertEqual(config.read_bytes(), before)
        self.assertIs(real, runtime_state.os.replace)
        self._assert_no_tmp(profiles)

    def test_no_port_and_a_boolean_port_do_not_seed(self) -> None:
        for port in (None, True):
            with self.subTest(port=port):
                profiles = self._profiles()
                state = _state(_SEED_KEY, port)
                with self.assertRaises(BindingPrepareError) as raised:
                    state.prepare(_RUN, "server", str(profiles))
                self.assertEqual(raised.exception.code, "instance_config_missing")
                self.assertFalse((profiles / "dayz_mcp.json").exists())
                self.assertNotIn(_SEED_KEY, _tree_text(profiles))

    def test_retire_of_an_unknown_run_does_not_rewrite_a_foreign_config(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        _write(config, {"profile": "test", "owner": "someone-else"})
        state = _state()
        state.retire_run("not-our-run", "stopped")
        self.assertEqual(_load(config), {"profile": "test", "owner": "someone-else"})
        self.assertEqual(state.profile_release_errors, [])

    def test_in_flight_prepare_blocks_recovery(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        _write(config, _installer())
        state = _state()
        state.prepare(_RUN, "server", str(profiles))
        state.retire_run(_RUN, "stopped")
        _write(config, {"profile": "test", "marker": 1})
        key = loopback._profile_config_key(config)
        state._profile_preparing[key] = 1
        self.assertEqual(state.recover_crashed_profile(str(profiles)), [])
        self.assertEqual(_load(config)["profile"], "test")
        state._profile_preparing.pop(key)
        errors = state.recover_crashed_profile(str(profiles))
        self.assertTrue(any("profile_prior_missing" in item for item in errors))
        self.assertNotIn("profile", _load(config))
        self.assertEqual(_load(config)["marker"], 1)


class AtomicReplaceAndReleaseOutputTest(_Case):
    def _crash_installer(self) -> tuple[Path, Path, dict]:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer(note="keep-me")
        _write(config, original)
        state = _state()
        state.prepare(_RUN, "server", str(profiles))
        self.assertEqual(_load(config)["profile"], "test")
        return profiles, config, original

    def test_restore_replaces_a_temporary_file_in_the_same_directory(self) -> None:
        profiles, config, original = self._crash_installer()
        seen: list[tuple[Path, Path]] = []
        real = runtime_state.os.replace

        def spy(src: str, dst: str) -> None:
            seen.append((Path(src), Path(dst)))
            real(src, dst)

        fresh = _state()
        with patch.object(runtime_state.os, "replace", spy):
            errors = fresh.recover_crashed_profile(str(profiles))
        self.assertEqual(errors, [])
        self.assertEqual(_load(config), original)
        self.assertTrue(seen)
        for src, dst in seen:
            self.assertEqual(src.parent, dst.parent)
            self.assertEqual(dst.parent, config.parent)
            self.assertIn(".tmp.", src.name)
            self.assertEqual(dst.name, "dayz_mcp.json")
        self._assert_no_tmp(profiles)

    def test_one_sharing_violation_is_retried_by_the_existing_replace(self) -> None:
        profiles, config, original = self._crash_installer()
        calls = {"n": 0}
        real = runtime_state.os.replace

        def replace(src: str, dst: str) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise _permission(5)
            real(src, dst)

        fresh = _state()
        with patch.object(runtime_state.os, "replace", replace):
            errors = fresh.recover_crashed_profile(str(profiles))
        self.assertEqual(errors, [])
        self.assertEqual(calls["n"], 2)
        self.assertEqual(_load(config), original)
        self._assert_no_tmp(profiles)

    def test_permission_retry_stops_and_the_error_names_permission_error(self) -> None:
        profiles, config, _original = self._crash_installer()
        calls = {"n": 0}
        sleeps: list[float] = []
        clock = {"now": 1000.0}

        def replace(src: str, dst: str) -> None:
            calls["n"] += 1
            raise _permission(5)

        def monotonic() -> float:
            clock["now"] += 5.0
            return clock["now"]

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)

        fresh = _state()
        with (
            patch.object(runtime_state.os, "replace", replace),
            patch.object(runtime_state.time, "monotonic", monotonic),
            patch.object(runtime_state.time, "sleep", sleep),
        ):
            with self.assertLogs("dayz_mcp.loopback", level="ERROR") as logged:
                errors = fresh.recover_crashed_profile(str(profiles))
        self.assertLessEqual(calls["n"], 3)
        self.assertGreaterEqual(calls["n"], 1)
        self.assertEqual(sleeps, [])
        self.assertTrue(any("PermissionError" in item for item in errors))
        self.assertTrue(any("PermissionError" in line for line in logged.output))
        self.assertEqual(_load(config)["profile"], "test")
        self.assertTrue(_prior_path(config).is_file())
        self.assertNotEqual(_prior(config)["document"].get("profile"), "test")

    def test_permission_error_is_in_the_stop_close_and_reap_body(self) -> None:
        profiles, config, _original = self._crash_installer()
        state = _state()
        # The dead process's sidecar is on disk. This process releases it.
        state._profile_holders[loopback._profile_config_key(config)] = {(_RUN, "server")}
        state._profile_paths[loopback._profile_config_key(config)] = str(config)

        class Lifecycle:
            def stop_run(self, client: object, token: object, run_id: object) -> dict:
                state.retire_run(str(run_id), "stopped")
                return {"_http_status": 200, "run_id": run_id, "state": "stopped"}

            def close_run(self, client: object, token: object, run_id: object) -> dict:
                state._note_profile_release_error(config, _permission(99))
                return {"_http_status": 500, "run_id": run_id, "error": "close_failed"}

            def reap_dead_run(self, client: object, token: object, run_id: object) -> dict:
                state._note_profile_release_error(config, _permission(99))
                return {"_http_status": 200, "run_id": run_id, "state": "reaped"}

            def start_run(self, client: object, token: object, request: object) -> dict:
                state._note_profile_release_error(config, _permission(99))
                return {"_http_status": 409, "error": "active_run_exists"}

        state.lifecycle = Lifecycle()
        logs: list[str] = []

        def reply(action: str) -> tuple[int, dict]:
            handler = loopback.Handler.__new__(loopback.Handler)
            handler.server = SimpleNamespace(state=state, log_sink=logs.append)
            replies: list[tuple[int, dict]] = []
            body = {"identity": _IDENTITY, "lease_token": "token", "run_id": _RUN}
            handler._read_json = lambda: json.loads(json.dumps(body))  # type: ignore[method-assign]
            handler._json = lambda code, payload: replies.append(  # type: ignore[method-assign]
                (code, json.loads(json.dumps(payload)))
            )
            handler._handle_lifecycle(action)
            self.assertEqual(len(replies), 1)
            return replies[0]

        real = runtime_state.os.replace

        def deny(src: str, dst: str) -> None:
            raise _permission(99)

        with patch.object(runtime_state.os, "replace", deny):
            status, payload = reply("stop")
        self.assertEqual(status, 200)
        self.assertTrue(any("PermissionError" in item for item in payload["profile_restore_errors"]))
        self.assertTrue(any(line.startswith("PROFILE ") and "PermissionError" in line for line in logs))
        self.assertEqual(_load(config)["profile"], "test")
        self.assertTrue(_prior_path(config).is_file())

        status, payload = reply("close")
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"], "close_failed")
        self.assertTrue(any("PermissionError" in item for item in payload["profile_restore_errors"]))

        status, payload = reply("reap")
        self.assertEqual(status, 200)
        self.assertTrue(any("PermissionError" in item for item in payload["profile_restore_errors"]))

        status, payload = reply("start")
        self.assertEqual((status, payload), (409, {"error": "active_run_exists"}))
        self.assertIs(runtime_state.os.replace, real)

    def test_restore_methods_do_not_add_a_second_retry_loop(self) -> None:
        for name in ("_apply_profile_prior", "_ensure_profile_prior", "_strip_profile_test"):
            source = inspect.getsource(getattr(loopback.ServerState, name))
            tree = ast.parse(textwrap.dedent(source))
            self.assertEqual(
                [node for node in ast.walk(tree) if isinstance(node, ast.While)],
                [],
            )
            self.assertIn("atomic_write_json", source)
        blocked = inspect.getsource(loopback.ServerState._profile_blocked)
        self.assertIn("_profile_holders", blocked)
        self.assertIn("_profile_preparing", blocked)
        self.assertIn("self._lock", blocked)


class DocsContractTest(unittest.TestCase):
    def test_changelog_says_the_restore_waits_for_the_next_mission(self) -> None:
        text = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("efectivo tras reinicio de misión", text)
        self.assertIn("B1-P2", text)
        self.assertNotIn("never removes it", text)

    def test_addon_reads_the_flag_once_on_mission_start(self) -> None:
        mission = (addon_root() / "scripts" / "5_Mission" / "MissionServer.c").read_text(
            encoding="utf-8"
        )
        start, update = mission.split("override void OnUpdate(float timeslice)", 1)
        self.assertEqual(start.count("ConfigIsTestProfile()"), 1)
        self.assertIn("override void OnMissionStart()", start)
        self.assertNotIn("ConfigIsTestProfile()", update)


if __name__ == "__main__":
    unittest.main()
