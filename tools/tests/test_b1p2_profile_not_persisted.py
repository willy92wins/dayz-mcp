"""B1-P2: profile=test does not survive release, a failed prepare, or crash recovery.

The harness still writes profile=test for the life of the instance. These
tests stay on disk and in-process: no DayZ, no PBO, no external network.
The addon reads ConfigIsTestProfile once, from OnMissionStart, so putting
the installer file back does not clear godmode on a server that is still
running. That hot path is an addon change and is not implemented here.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
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
from dayz_mcp.process_lifecycle import ProcessLifecycle, RunManifestStore, RunRecord
from dayz_mcp.runtime_state import RuntimePaths
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
        "url": "http://127.0.0.1:8765/",
        "key": "live-key",
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

    def _reply(
        self,
        state: loopback.ServerState,
        action: str,
        body: dict | None = None,
        logs: list[str] | None = None,
    ) -> tuple[int, dict]:
        handler = loopback.Handler.__new__(loopback.Handler)
        handler.server = SimpleNamespace(
            state=state,
            log_sink=logs.append if logs is not None else (lambda _line: None),
        )
        replies: list[tuple[int, dict]] = []
        document = (
            body
            if body is not None
            else {"identity": _IDENTITY, "lease_token": "token", "run_id": _RUN}
        )
        handler._read_json = lambda: json.loads(json.dumps(document))  # type: ignore[method-assign]
        handler._json = lambda code, payload: replies.append(  # type: ignore[method-assign]
            (code, json.loads(json.dumps(payload)))
        )
        handler._handle_lifecycle(action)
        self.assertEqual(len(replies), 1)
        return replies[0]

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
        self.assertEqual(held["key"], original["key"])
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
        client_original = _installer(role="client")
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
        reread_failed = False

        def loads(text: str, *args: object, **kwargs: object) -> object:
            nonlocal reread_failed
            result = real(text, *args, **kwargs)
            if (
                not reread_failed
                and isinstance(result, dict)
                and result.get("profile") == "test"
                and isinstance(result.get("instance"), str)
            ):
                reread_failed = True
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
        empty.write_bytes(b"{}")
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
        self.assertEqual(empty.read_bytes(), b"{}")
        self._assert_no_tmp(profiles)
        self._assert_no_tmp(mission)

    def test_failed_prepare_deletes_a_seeded_file(self) -> None:
        profiles = self._profiles()
        state = _state(_SEED_KEY)
        real = loopback.json.loads
        reread_failed = False

        def loads(text: str, *args: object, **kwargs: object) -> object:
            nonlocal reread_failed
            result = real(text, *args, **kwargs)
            if (
                not reread_failed
                and isinstance(result, dict)
                and result.get("profile") == "test"
                and isinstance(result.get("instance"), str)
            ):
                reread_failed = True
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
        _write(
            _prior_path(profiles / "dayz_mcp.json"),
            {
                "version": 1,
                "kind": "absent",
                "prepared_sha256": hashlib.sha256(
                    (profiles / "dayz_mcp.json").read_bytes()
                ).hexdigest(),
            },
        )
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

    def test_sibling_role_root_junction_is_not_swept(self) -> None:
        root = self._tmp()
        server = self._leaf(root, "_server")
        (root / "mpmissions").mkdir()
        outside = self._tmp() / "foreign-client"
        foreign_leaf = outside / "leaf"
        foreign_cfg = foreign_leaf / "dayz_mcp.json"
        _write(foreign_cfg, {"profile": "test", "secret": "external"})
        self._junction(root / "_client", outside)
        state = _state(_SEED_KEY)
        state.prepare(_RUN, "server", str(server))
        state.retire_run(_RUN, "stopped")
        self.assertFalse((server / "dayz_mcp.json").exists())
        self.assertEqual(_load(foreign_cfg), {"profile": "test", "secret": "external"})
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
                "prepared_sha256": hashlib.sha256(client_cfg.read_bytes()).hexdigest(),
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
        config = profiles / "dayz_mcp.json"
        before = {path: path.read_bytes() for path in (config, _prior_path(config))}
        state.retire_run(_RUN, "stopped")
        document = _load(config)
        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document["profile"], "test")
        self.assertEqual(document["key"], _SEED_KEY)
        self.assertTrue(_prior_path(config).is_file())
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertEqual(mission_cfg.read_text(encoding="utf-8"), "{not json")
        self.assertTrue(any("mission_profile_unreadable" in item for item in state.profile_release_errors))
        self.assertTrue(any("profile_delete_blocked" in item for item in state.profile_release_errors))

    def test_mission_scandir_permission_blocks_seeded_delete(self) -> None:
        root = self._tmp()
        profiles = self._leaf(root)
        missions = root / "mpmissions"
        missions.mkdir()
        mission_cfg = missions / "dayzOffline.chernarusplus" / "dayz_mcp.json"
        _write(mission_cfg, {"profile": "test", "keep": "mission"})
        state = _state(_SEED_KEY)
        state.prepare(_RUN, "server", str(profiles))
        config = profiles / "dayz_mcp.json"
        before = {path: path.read_bytes() for path in (config, _prior_path(config))}
        real = os.scandir

        def deny(path: object) -> object:
            if Path(path) == missions:
                raise PermissionError(13, "Access is denied")
            return real(path)

        with patch.object(os, "scandir", deny):
            state.retire_run(_RUN, "stopped")
        self.assertTrue(config.is_file())
        self.assertEqual((_load(config) or {}).get("profile"), "test")
        self.assertTrue(_prior_path(config).is_file())
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertEqual(_load(mission_cfg), {"profile": "test", "keep": "mission"})
        self.assertTrue(any("PermissionError" in item for item in state.profile_release_errors))


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

    def test_pruned_exited_run_is_recovered_from_preprune_backup(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer(note="keep-me")
        _write(config, original)
        abandoned = _state()
        abandoned.prepare(_RUN, "server", str(profiles))
        self.assertEqual(_load(config)["profile"], "test")
        runtime = self._tmp() / "runtime"
        runtime.mkdir()
        runs_path = runtime / "runs.json"
        record = RunRecord(
            _RUN, None, None, "EXITED", "b1p2", "@DayZ_MCP", str(profiles), "", []
        )
        runs_path.write_bytes(
            json.dumps(
                {"version": 1, "runs": [dataclasses.asdict(record)]},
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )
        paths = RuntimePaths(
            runtime, runtime / "audit", runtime / "coordination.json", runs_path
        )
        store = RunManifestStore(paths)
        self.assertEqual(store.list_runs(), [])
        self.assertTrue((runtime / "runs.json.bak-preprune").is_file())
        fresh = _state()
        fresh.lifecycle = SimpleNamespace(manifest=store)
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
        before = config.read_bytes()
        sidecar = _prior_path(config)
        sidecar.write_text('{"version": 99}', encoding="utf-8")
        fresh = _state()
        errors = fresh.recover_crashed_profile(str(profiles))
        self.assertTrue(any("profile_prior_invalid" in item for item in errors))
        self.assertEqual(sidecar.read_text(encoding="utf-8"), '{"version": 99}')
        self.assertEqual(config.read_bytes(), before)
        self.assertTrue(fresh._profile_pending_messages())

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

    def test_missing_profiles_dir_still_clears_the_absolute_mission(self) -> None:
        profiles = self._tmp() / "missing-profiles"
        mission = self._tmp() / "mission"
        mission_cfg = mission / "dayz_mcp.json"
        _write(mission_cfg, {"profile": "test", "keep": "mission"})
        fresh = _state()
        errors = fresh.recover_crashed_profile(str(profiles), str(mission))
        self.assertEqual(errors, [])
        self.assertFalse(profiles.exists())
        restored = _load(mission_cfg)
        self.assertEqual(restored, {"keep": "mission"})
        self.assertNotEqual((restored or {}).get("profile"), "test")

    def test_lifecycle_recovery_clears_a_mission_when_the_profiles_dir_is_gone(self) -> None:
        profiles = self._tmp() / "missing-profiles"
        mission = self._tmp() / "mission"
        mission_cfg = mission / "dayz_mcp.json"
        _write(mission_cfg, {"profile": "test", "keep": "mission"})

        class Manifest:
            def list_runs(self) -> list[dict[str, str]]:
                return [{"profiles": str(profiles), "mission": str(mission), "state": "EXITED"}]

            def get(self, run_id: str) -> dict[str, str]:
                return {"profiles": str(profiles), "mission": str(mission)}

        fresh = _state()
        fresh.lifecycle = SimpleNamespace(manifest=Manifest())
        self.assertEqual(fresh.profile_release_errors, [])
        self.assertFalse(profiles.exists())
        restored = _load(mission_cfg)
        self.assertEqual(restored, {"keep": "mission"})
        self.assertNotEqual((restored or {}).get("profile"), "test")


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

    def test_config_that_grows_past_the_reader_limit_is_not_published(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        limit = loopback._PROFILE_PRIOR_MAX_BYTES
        original = _installer(pad="A" * (limit - 160))
        sidecar = loopback._prior_payload(original, kind="present")
        while loopback._profile_json_size(sidecar) <= limit:
            original["pad"] = str(original["pad"]) + "A"
            sidecar = loopback._prior_payload(original, kind="present")
        while loopback._profile_json_size(original) > limit:
            original["pad"] = str(original["pad"])[:-1]
            sidecar = loopback._prior_payload(original, kind="present")
        self.assertLessEqual(loopback._profile_json_size(original), limit)
        self.assertGreater(loopback._profile_json_size(sidecar), limit)
        config.write_text(
            json.dumps(original, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        before = config.read_bytes()
        state = _state()
        with self.assertRaises(BindingPrepareError) as raised:
            state.prepare(_RUN, "server", str(profiles))
        self.assertEqual(raised.exception.code, "instance_config_missing")
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse(_prior_path(config).exists())
        self.assertNotIn(b'"profile":"test"', before)

    def test_default_instance_endpoint_mismatch_does_not_leave_profile_test(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = {
            "url": "http://127.0.0.1:2402/",
            "key": "installer-key",
            "pollHz": 5,
            "note": "keep-me",
        }
        _write(config, original)
        state = _state()
        lifecycle = ProcessLifecycle.__new__(ProcessLifecycle)
        lifecycle.bindings = state
        minted, error = lifecycle._prepare_instance(_RUN, "server", str(profiles), False)
        self.assertIsNone(minted)
        self.assertEqual(error, "instance_endpoint_mismatch")
        restored = _load(config)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertNotEqual(restored.get("profile"), "test")
        self.assertEqual(restored["url"], original["url"])
        self.assertEqual(restored["key"], original["key"])
        self.assertEqual(restored["note"], "keep-me")
        self.assertEqual(state._profile_holders, {})

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

    def test_junction_sidecar_is_not_read_and_config_is_preserved_pending(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        _write(config, {"profile": "test", "keep": 9})
        before = config.read_bytes()
        target = self._tmp() / "sidecar-target"
        target.mkdir()
        (target / "secret.txt").write_text("prior-secret", encoding="utf-8")
        self._junction(_prior_path(config), target)
        fresh = _state()
        errors = fresh.recover_crashed_profile(str(profiles))
        self.assertTrue(any("profile_config_not_plain" in item for item in errors))
        self.assertEqual(config.read_bytes(), before)
        self.assertTrue(fresh._profile_pending_messages())
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

            def status(self, client: object) -> dict:
                return {"_http_status": 200, "runs": []}

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
        self.assertEqual(status, 500)
        self.assertEqual(payload.get("ok"), False)
        self.assertEqual(payload.get("error"), "profile_restore_failed")
        self.assertTrue(any("PermissionError" in item for item in payload["profile_restore_errors"]))
        self.assertTrue(any(line.startswith("PROFILE ") and "PermissionError" in line for line in logs))
        self.assertEqual(_load(config)["profile"], "test")
        self.assertTrue(_prior_path(config).is_file())

        status, payload = reply("close")
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"], "close_failed")
        self.assertEqual(payload.get("ok"), False)
        self.assertTrue(any("PermissionError" in item for item in payload["profile_restore_errors"]))

        status, payload = reply("reap")
        self.assertEqual(status, 500)
        self.assertEqual(payload.get("ok"), False)
        self.assertEqual(payload.get("error"), "profile_restore_failed")
        self.assertTrue(any("PermissionError" in item for item in payload["profile_restore_errors"]))

        status, payload = reply("start")
        self.assertEqual((status, payload), (409, {"error": "active_run_exists"}))
        self.assertIs(runtime_state.os.replace, real)

        status, payload = reply("status")
        self.assertEqual(status, 200)
        self.assertTrue(any("PermissionError" in item for item in payload["profile_restore_errors"]))

        handler = loopback.Handler.__new__(loopback.Handler)
        handler.server = SimpleNamespace(state=state, log_sink=logs.append, status_provider=None)
        status_replies: list[tuple[int, dict]] = []
        handler._json = lambda code, payload: status_replies.append(  # type: ignore[method-assign]
            (code, json.loads(json.dumps(payload)))
        )
        handler._handle_status()
        self.assertEqual(len(status_replies), 1)
        self.assertEqual(status_replies[0][0], 200)
        self.assertTrue(
            any("PermissionError" in item for item in status_replies[0][1]["profile_restore_errors"])
        )

    def test_startup_restore_errors_stay_visible_on_later_status(self) -> None:
        profiles, config, _original = self._crash_installer()
        fresh = _state()
        audit: list[dict[str, object]] = []
        fresh.audit_writer = SimpleNamespace(write=audit.append)

        def deny(src: str, dst: str) -> None:
            raise _permission(99)

        with patch.object(runtime_state.os, "replace", deny):
            errors = fresh.recover_crashed_profile(str(profiles))
        self.assertTrue(any("PermissionError" in item for item in errors))
        self.assertEqual(_load(config)["profile"], "test")
        self.assertTrue(any("profile_restore_failed" == item.get("event") for item in audit))
        snapshot = fresh.status_snapshot()
        self.assertTrue(
            any("PermissionError" in item for item in snapshot["profile_restore_errors"])
        )

        class Lifecycle:
            def status(self, client: object) -> dict:
                return {"_http_status": 200, "runs": []}

        fresh.lifecycle = Lifecycle()
        replies: list[tuple[int, dict]] = []
        handler = loopback.Handler.__new__(loopback.Handler)
        handler.server = SimpleNamespace(state=fresh, log_sink=lambda _line: None)
        handler._read_json = lambda: json.loads(  # type: ignore[method-assign]
            json.dumps({"identity": _IDENTITY, "lease_token": "token"})
        )
        handler._json = lambda code, payload: replies.append(  # type: ignore[method-assign]
            (code, json.loads(json.dumps(payload)))
        )
        handler._handle_lifecycle("status")
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0][0], 200)
        self.assertTrue(
            any("PermissionError" in item for item in replies[0][1]["profile_restore_errors"])
        )

    def test_restore_methods_do_not_add_a_second_retry_loop(self) -> None:
        for name in ("_apply_profile_prior", "_ensure_profile_prior", "_strip_profile_test"):
            source = inspect.getsource(getattr(loopback.ServerState, name))
            tree = ast.parse(textwrap.dedent(source))
            self.assertEqual(
                [node for node in ast.walk(tree) if isinstance(node, ast.While)],
                [],
            )
            writer = "_atomic_write_text" if name == "_apply_profile_prior" else "atomic_write_json"
            self.assertIn(writer, source)
        blocked = inspect.getsource(loopback.ServerState._profile_blocked)
        self.assertIn("_profile_holders", blocked)
        self.assertIn("_profile_preparing", blocked)
        self.assertIn("self._lock", blocked)


class ProfileRestoreFailureVisibilityTest(_Case):
    def _crash_installer(self) -> tuple[Path, Path, dict]:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer(note="keep-me")
        _write(config, original)
        state = _state()
        state.prepare(_RUN, "server", str(profiles))
        self.assertEqual(_load(config)["profile"], "test")
        return profiles, config, original

    def test_exited_stop_is_not_success_while_profile_test_remains(self) -> None:
        from dayz_mcp import dayz_test_worker

        _profiles, config, _original = self._crash_installer()
        state = _state()
        key = loopback._profile_config_key(config)
        state._profile_holders[key] = {(_RUN, "server")}
        state._profile_paths[key] = str(config)

        class Lifecycle:
            status_shape = "exited"

            def stop_run(self, client: object, token: object, run_id: object) -> dict:
                state.retire_run(str(run_id), "stopped")
                return {
                    "_http_status": 200,
                    "ok": True,
                    "run_id": run_id,
                    "state": "EXITED",
                    "stop_method": "no_live_owned",
                    "error": "run_processes_gone",
                }

            def status(self, client: object) -> dict:
                if self.status_shape == "absent":
                    return {"_http_status": 200, "runs": []}
                return {
                    "_http_status": 200,
                    "runs": [{"run_id": _RUN, "state": "EXITED", "processes": []}],
                }

        lifecycle = Lifecycle()
        state.lifecycle = lifecycle

        def deny(src: str, dst: str) -> None:
            raise _permission(99)

        with patch.object(runtime_state.os, "replace", deny):
            status, payload = self._reply(state, "stop")
        self.assertEqual(status, 500)
        self.assertEqual(_load(config)["profile"], "test")
        self.assertFalse(dayz_test_worker._successful_run(payload, _RUN, "EXITED"))
        self.assertFalse(dayz_test_worker._run_already_gone(payload, _RUN))
        self.assertNotEqual(payload.get("state"), "EXITED")
        self.assertNotIn("stop_method", payload)
        self.assertEqual(payload.get("error"), "profile_restore_failed")

        status, payload = self._reply(state, "status")
        self.assertEqual(status, 200)
        self.assertFalse(dayz_test_worker._status_run_already_gone(payload, _RUN))
        row = next(item for item in payload["runs"] if item.get("run_id") == _RUN)
        processes = row.get("processes")
        processes_gone = isinstance(processes, list) and not processes
        self.assertFalse(row.get("state") == "EXITED" or processes_gone)

        lifecycle.status_shape = "absent"
        _status, payload = self._reply(state, "status")
        self.assertFalse(dayz_test_worker._status_run_already_gone(payload, _RUN))
        _status, payload = self._reply(
            state,
            "status",
            {"identity": _IDENTITY, "lease_token": "token"},
        )
        self.assertFalse(dayz_test_worker._status_run_already_gone(payload, _RUN))

    def test_scandir_failure_fails_the_stop_response(self) -> None:
        root = self._tmp()
        profiles = self._leaf(root)
        missions = root / "mpmissions"
        missions.mkdir()
        mission_cfg = missions / "dayzOffline.chernarusplus" / "dayz_mcp.json"
        _write(mission_cfg, {"profile": "test", "keep": "mission"})
        state = _state(_SEED_KEY)
        state.prepare(_RUN, "server", str(profiles))
        config = profiles / "dayz_mcp.json"
        before = {path: path.read_bytes() for path in (config, _prior_path(config))}

        class Lifecycle:
            def stop_run(self, client: object, token: object, run_id: object) -> dict:
                state.retire_run(str(run_id), "stopped")
                return {
                    "_http_status": 200,
                    "ok": True,
                    "run_id": run_id,
                    "state": "EXITED",
                }

        state.lifecycle = Lifecycle()
        real = os.scandir

        def deny(path: object) -> object:
            if Path(path) == missions:
                raise PermissionError(13, "Access is denied")
            return real(path)

        with patch.object(os, "scandir", deny):
            status, payload = self._reply(state, "stop")
        self.assertEqual(status, 500)
        self.assertEqual(payload.get("ok"), False)
        self.assertEqual(payload.get("error"), "profile_restore_failed")
        self.assertTrue(
            any("PermissionError" in item for item in payload["profile_restore_errors"])
        )
        self.assertTrue(config.is_file())
        self.assertEqual((_load(config) or {}).get("profile"), "test")
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertEqual(_load(mission_cfg), {"profile": "test", "keep": "mission"})

    def test_named_endpoint_mismatch_preserves_the_whole_document_without_a_sidecar(self) -> None:
        profiles = self._leaf(self._tmp(), leaf="profiles-job5b")
        config = profiles / "dayz_mcp.json"
        original = {
            "url": "http://127.0.0.1:2402/",
            "key": "installer-key",
            "pollHz": 5,
            "note": "keep-me",
            "profile": "live",
        }
        _write(config, original)
        state = _state()
        state.instance_token = "job5b"
        lifecycle = ProcessLifecycle.__new__(ProcessLifecycle)
        lifecycle.bindings = state
        minted, error = lifecycle._prepare_instance(_RUN, "server", str(profiles), False)
        self.assertIsNone(minted)
        self.assertEqual(error, "instance_endpoint_mismatch")
        self.assertEqual(_load(config), original)
        self.assertFalse(_prior_path(config).exists())
        self.assertEqual(state._profile_holders, {})

    def test_distinct_missions_are_checked_before_the_sidecar_is_removed(self) -> None:
        profiles, config, _original = self._crash_installer()
        before = {path: path.read_bytes() for path in (config, _prior_path(config))}
        mission_a = self._tmp() / "mission-a"
        mission_b = self._tmp() / "mission-b"
        cfg_a = mission_a / "dayz_mcp.json"
        cfg_b = mission_b / "dayz_mcp.json"
        _write(cfg_a, {"profile": "test", "which": "a"})
        cfg_b.parent.mkdir(parents=True)
        cfg_b.write_text("{not json", encoding="utf-8")
        runtime = self._tmp() / "runtime"
        runtime.mkdir()
        runs_path = runtime / "runs.json"
        runs_path.write_text("{}\n", encoding="utf-8")
        backup = runs_path.with_name(runs_path.name + ".bak-preprune")
        backup.write_text(
            json.dumps(
                {
                    "runs": [
                        {
                            "state": "EXITED",
                            "profiles": str(profiles),
                            "mission": str(mission_b),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        class Manifest:
            paths = SimpleNamespace(runs_path=runs_path)

            def list_runs(self) -> list[dict[str, str]]:
                return [
                    {
                        "state": "EXITED",
                        "profiles": str(profiles),
                        "mission": str(mission_a),
                    }
                ]

        fresh = _state()
        fresh.lifecycle = SimpleNamespace(manifest=Manifest())
        self.assertNotIn("profile", _load(cfg_a) or {})
        self.assertEqual(cfg_b.read_text(encoding="utf-8"), "{not json")
        self.assertTrue(_prior_path(config).is_file())
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertTrue(
            any("mission_profile_unreadable" in item for item in fresh.profile_release_errors)
        )
        self.assertTrue(any(str(cfg_b) in item for item in fresh.profile_release_errors))

    def test_reparse_ancestor_is_not_a_clean_mission(self) -> None:
        for ancestor in ("role", "project"):
            with self.subTest(ancestor=ancestor):
                root = self._tmp()
                if ancestor == "role":
                    real_server = self._tmp() / "real-server"
                    leaf = real_server / "leaf"
                    leaf.mkdir(parents=True)
                    self._junction(root / "_server", real_server)
                    profiles = root / "_server" / "leaf"
                    mission_cfg = root / "mpmissions" / "dayzOffline.chernarusplus" / "dayz_mcp.json"
                else:
                    real = self._tmp() / "real-project"
                    leaf = real / "_server" / "leaf"
                    leaf.mkdir(parents=True)
                    mission_cfg = (
                        real / "mpmissions" / "dayzOffline.chernarusplus" / "dayz_mcp.json"
                    )
                    link = self._tmp() / "linked-project"
                    self._junction(link, real)
                    profiles = link / "_server" / "leaf"
                _write(mission_cfg, {"profile": "test", "keep": "mission"})
                state = _state(_SEED_KEY)
                with self.assertRaises(BindingPrepareError) as raised:
                    state.prepare(_RUN, "server", str(profiles))
                self.assertEqual(raised.exception.code, "instance_config_missing")
                self.assertFalse((leaf / "dayz_mcp.json").exists())
                self.assertEqual(_load(mission_cfg), {"profile": "test", "keep": "mission"})
                _write(leaf / "dayz_mcp.json", {"profile": "test", "key": _SEED_KEY})
                _write(
                    _prior_path(leaf / "dayz_mcp.json"),
                    {
                        "version": 1,
                        "kind": "absent",
                        "prepared_sha256": hashlib.sha256(
                            (leaf / "dayz_mcp.json").read_bytes()
                        ).hexdigest(),
                    },
                )
                errors = state.recover_crashed_profile(str(profiles))
                self.assertTrue((leaf / "dayz_mcp.json").is_file())
                self.assertEqual(_load(mission_cfg), {"profile": "test", "keep": "mission"})
                self.assertTrue(_prior_path(leaf / "dayz_mcp.json").is_file())
                self.assertTrue(any("mission_scan_incomplete" in item for item in errors))

    def test_invalid_preprune_backup_is_reported(self) -> None:
        profiles, config, _original = self._crash_installer()
        runtime = self._tmp() / "runtime"
        runtime.mkdir()
        runs_path = runtime / "runs.json"
        runs_path.write_text("{}\n", encoding="utf-8")

        class Manifest:
            paths = SimpleNamespace(runs_path=runs_path)

            def list_runs(self) -> list[dict[str, str]]:
                return []

        fresh = _state()
        fresh.lifecycle = SimpleNamespace(manifest=Manifest())
        self.assertEqual(fresh.profile_release_errors, [])
        self.assertEqual(_load(config)["profile"], "test")

        backup = runs_path.with_name(runs_path.name + ".bak-preprune")
        backup.write_text("{", encoding="utf-8")
        invalid = _state()
        invalid.lifecycle = SimpleNamespace(manifest=Manifest())
        self.assertEqual(_load(config)["profile"], "test")
        self.assertTrue(_prior_path(config).is_file())
        self.assertTrue(
            any("JSONDecodeError" in item for item in invalid.profile_release_errors)
        )
        self.assertTrue(any(str(backup) in item for item in invalid.profile_release_errors))
        self.assertIn("profile_restore_errors", invalid.status_snapshot())

        real_read = Path.read_text

        def deny(self: Path, *args: object, **kwargs: object) -> str:
            if self.name.endswith(".bak-preprune"):
                raise PermissionError(13, "Access is denied")
            return real_read(self, *args, **kwargs)

        backup.write_text('{"runs":[]}\n', encoding="utf-8")
        denied = _state()
        with patch.object(Path, "read_text", deny):
            denied.lifecycle = SimpleNamespace(manifest=Manifest())
        self.assertEqual(_load(config)["profile"], "test")
        self.assertTrue(
            any("PermissionError" in item for item in denied.profile_release_errors)
        )

    def test_profile_restore_audit_uses_the_runtime_writer(self) -> None:
        profiles, _config, _original = self._crash_installer()
        root = self._tmp() / "runtime"
        paths = RuntimePaths(
            root, root / "audit", root / "coordination.json", root / "runs.json"
        )
        writer = runtime_state.JsonlAuditWriter(paths, "b1p2-audit")
        fresh = _state()
        fresh.audit_writer = writer
        real_replace = runtime_state.os.replace

        def deny(src: str, dst: str) -> None:
            # The profile replace must fail. The audit file uses the same
            # os.replace; blocking that one hides the event the writer accepted.
            name = Path(dst).name
            if name == "dayz_mcp.json" or name.endswith(".profile-prior"):
                raise _permission(99)
            return real_replace(src, dst)

        with patch.object(runtime_state.os, "replace", deny):
            errors = fresh.recover_crashed_profile(str(profiles))
        self.assertTrue(any("PermissionError" in item for item in errors))
        audit_path = paths.audit_dir / "events.jsonl"
        self.assertTrue(audit_path.is_file())
        restored = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(restored)
        self.assertEqual(restored[-1]["event"], "profile_restore_failed")
        self.assertEqual(restored[-1]["reason"], "profile_restore_failed")
        self.assertEqual(restored[-1]["duration_s"], 0)
        self.assertIn("PermissionError", restored[-1]["message"])

        class Lifecycle:
            def stop_run(self, client: object, token: object, run_id: object) -> dict:
                return {"_http_status": 200, "ok": True, "run_id": run_id, "state": "EXITED"}

        fresh.lifecycle = Lifecycle()
        before = len(restored)
        with patch.object(runtime_state.os, "replace", deny):
            self._reply(fresh, "stop")
        again = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertGreater(len(again), before)
        self.assertEqual(again[-1]["reason"], "profile_restore_failed")
        self.assertEqual(again[-1]["duration_s"], 0)
        self.assertIn("PermissionError", again[-1]["message"])

    def test_recovered_mission_and_sidecar_leave_status_clean(self) -> None:
        root = self._tmp()
        profiles = self._leaf(root)
        mission_cfg = root / "mpmissions" / "dayzOffline.chernarusplus" / "dayz_mcp.json"
        mission_cfg.parent.mkdir(parents=True)
        mission_cfg.write_text("{not json", encoding="utf-8")
        state = _state(_SEED_KEY)
        state.prepare(_RUN, "server", str(profiles))
        state.retire_run(_RUN, "stopped")
        pending = state.status_snapshot().get("profile_restore_errors", [])
        self.assertTrue(any("mission_profile_unreadable" in item for item in pending))

        _write(mission_cfg, {"keep": "yes"})
        state.recover_crashed_profile(str(profiles))
        self.assertFalse((profiles / "dayz_mcp.json").exists())
        self.assertFalse(_prior_path(profiles / "dayz_mcp.json").exists())
        self.assertNotIn("profile_restore_errors", state.status_snapshot())
        handler = loopback.Handler.__new__(loopback.Handler)
        handler.server = SimpleNamespace(state=state, log_sink=lambda _line: None, status_provider=None)
        replies: list[tuple[int, dict]] = []
        handler._json = lambda code, payload: replies.append(  # type: ignore[method-assign]
            (code, json.loads(json.dumps(payload)))
        )
        handler._handle_status()
        self.assertEqual(replies[0][0], 200)
        self.assertNotIn("profile_restore_errors", replies[0][1])

    def test_unreadable_config_is_not_a_clean_stop(self) -> None:
        from dayz_mcp import dayz_test_worker

        _profiles, config, _original = self._crash_installer()
        before = config.read_bytes()
        state = _state()
        key = loopback._profile_config_key(config)
        state._profile_holders[key] = {(_RUN, "server")}
        state._profile_paths[key] = str(config)

        class Lifecycle:
            def stop_run(self, client: object, token: object, run_id: object) -> dict:
                state.retire_run(str(run_id), "stopped")
                return {
                    "_http_status": 200,
                    "ok": True,
                    "run_id": run_id,
                    "state": "EXITED",
                    "stop_method": "no_live_owned",
                    "error": "run_processes_gone",
                }

        state.lifecycle = Lifecycle()
        real_read = Path.read_text

        def deny_read(path: Path, *args: object, **kwargs: object) -> str:
            if path.name == "dayz_mcp.json":
                raise PermissionError(13, "Access is denied")
            return real_read(path, *args, **kwargs)

        def deny_replace(src: str, dst: str) -> None:
            raise _permission(99)

        with (
            patch.object(Path, "read_text", deny_read),
            patch.object(runtime_state.os, "replace", deny_replace),
        ):
            status, payload = self._reply(state, "stop")
        self.assertEqual(status, 500)
        self.assertEqual(payload.get("ok"), False)
        self.assertEqual(payload.get("error"), "profile_restore_failed")
        self.assertNotEqual(payload.get("state"), "EXITED")
        self.assertNotIn("stop_method", payload)
        self.assertFalse(dayz_test_worker._successful_run(payload, _RUN, "EXITED"))
        self.assertFalse(dayz_test_worker._run_already_gone(payload, _RUN))
        self.assertTrue(
            any("PermissionError" in item for item in payload["profile_restore_errors"])
        )
        self.assertEqual(config.read_bytes(), before)

    def test_worker_kill_does_not_accept_adopt_while_restore_is_pending(self) -> None:
        import asyncio

        from dayz_mcp import dayz_test_request, dayz_test_worker, native_broker_protocol

        profiles, config, original = self._crash_installer()
        run_id = "12345678-1234-4234-8234-1234567890ab"
        policy = dayz_test_request.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF", "@Dabs Framework", "@VPPAdminTools"),
            mission_roots=(
                r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions",
            ),
            mod_roots=(r"P:\Mods",),
        )
        runtime = dayz_test_worker.WorkerRuntimePolicy(
            dev_root=policy.dev_root,
            mod=policy.mod,
            diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
            game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
            mission_aliases=(
                (
                    "chernarus",
                    r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.chernarusplus",
                ),
                (
                    "livonia",
                    r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.enoch",
                ),
                (
                    "sakhal",
                    r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.sakhal",
                ),
            ),
            mods_root=r"P:\Mods",
            build_temp_root=r"P:\temp",
            build_source_basename=None,
        )
        fresh = _state()
        calls: list[str] = []

        class Manifest:
            def list_runs(self) -> list[dict[str, str]]:
                return [{"profiles": str(profiles), "mission": "", "state": "RUNNING_IDLE"}]

            def get(self, requested: str) -> dict[str, str]:
                return {"profiles": str(profiles), "mission": ""}

        class Lifecycle:
            manifest = Manifest()

            def adopt_run(self, client: object, token: object, requested: object) -> dict:
                calls.append("adopt")
                return {"_http_status": 409, "error": "run_processes_gone"}

            def stop_run(self, client: object, token: object, requested: object) -> dict:
                calls.append("stop")
                fresh.retire_run(str(requested), "stopped")
                return {
                    "_http_status": 200,
                    "ok": True,
                    "run_id": requested,
                    "state": "EXITED",
                    "stop_method": "no_live_owned",
                    "error": "run_processes_gone",
                }

        seen: list[dict] = []

        class Broker:
            async def invoke(self, frame: bytes) -> dict[str, object]:
                request = native_broker_protocol.decode_request(frame)
                command = request.payload.get("command")
                self_command = command if isinstance(command, str) else ""
                _status, payload = self_test._reply(
                    fresh,
                    self_command,
                    {
                        "identity": _IDENTITY,
                        "lease_token": "token",
                        "run_id": request.payload.get("run_id"),
                    },
                )
                seen.append(payload)
                return payload

        self_test = self
        raw = json.dumps(
            {
                "version": 1,
                "dev_root": policy.dev_root,
                "mod": policy.mod,
                "mode": "offline",
                "kill": True,
                "run_id": run_id,
            }
        ).encode("utf-8")
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))

        def deny_replace(src: str, dst: str) -> None:
            raise _permission(99)

        with patch.object(runtime_state.os, "replace", deny_replace):
            fresh.lifecycle = Lifecycle()
            self.assertEqual(_load(config)["profile"], "test")
            self.assertTrue(any("PermissionError" in item for item in fresh.profile_release_errors))
            with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                asyncio.run(
                    dayz_test_worker.execute_dayz_test_worker(
                        parsed.canonical_bytes,
                        request_sha256=parsed.sha256,
                        request_policies=(policy,),
                        runtime_policy=runtime,
                        broker=Broker(),
                    )
                )
        self.assertEqual(raised.exception.code, "run_not_adoptable")
        self.assertEqual(calls, ["adopt", "stop"])
        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual(seen[0].get("error"), "profile_restore_failed")
        self.assertFalse(dayz_test_worker._run_already_gone(seen[0], run_id))
        self.assertEqual(_load(config)["profile"], "test")
        self.assertEqual(original["url"], (_load(config) or {})["url"])


class Job5bProfileTransactionRegressionTest(_Case):
    def test_rejected_prepare_preserves_config_and_sidecar_bytes_without_writes(self) -> None:
        for rejection in ("oversize", "endpoint", "topology", "invalid_sidecar", "oversized_sidecar"):
            with self.subTest(rejection=rejection):
                root = self._tmp()
                profiles = self._leaf(
                    root, leaf="profiles-job5b" if rejection == "endpoint" else "leaf"
                )
                config = profiles / "dayz_mcp.json"
                sidecar = _prior_path(config)
                document = _installer(note="new-installer", profile="live")
                if rejection == "oversize":
                    document["pad"] = "A" * (1024 * 1024 + 1)
                elif rejection == "endpoint":
                    document["url"] = "http://127.0.0.1:2402/"
                config.write_bytes((json.dumps(document, indent=2) + "\n\n").encode())
                if rejection == "invalid_sidecar":
                    sidecar.write_bytes(b'{"invalid":')
                elif rejection == "oversized_sidecar":
                    _write(sidecar, {
                        "version": 1,
                        "kind": "absent",
                        "prepared_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                        "pad": "A" * (1024 * 1024 + 1),
                    })
                else:
                    sidecar.write_bytes(b'{ "version": 1, "kind": "absent" }\n')
                mission = root / "mpmissions" / "mission" / "dayz_mcp.json"
                _write(mission, {"profile": "test", "owner": "earlier-run"})
                before = {path: path.read_bytes() for path in (config, sidecar, mission)}
                state = _state()
                if rejection == "endpoint":
                    state.instance_token = "job5b"
                with (
                    patch.object(
                        loopback, "_mission_scan_blocked", return_value=rejection == "topology"
                    ),
                    patch.object(
                        runtime_state, "atomic_write_json", wraps=runtime_state.atomic_write_json
                    ) as writes,
                ):
                    with self.assertRaises(BindingPrepareError) as raised:
                        state.prepare(_RUN, "server", str(profiles))
                expected = (
                    "instance_endpoint_mismatch"
                    if rejection == "endpoint"
                    else "instance_config_missing"
                )
                self.assertEqual(raised.exception.code, expected)
                for path, contents in before.items():
                    self.assertTrue(path.is_file(), f"rejected prepare removed {path.name}")
                    self.assertEqual(path.read_bytes(), contents, str(path))
                self.assertEqual(writes.call_args_list, [])
                self.assertEqual(state._profile_holders, {})
                self._assert_no_tmp(profiles)
                self._assert_no_tmp(mission.parent)

    def test_oversized_seed_is_rejected_before_any_config_or_sidecar_write(self) -> None:
        profiles = self._profiles()
        state = _state("A" * (1024 * 1024))
        with patch.object(
            runtime_state, "atomic_write_json", wraps=runtime_state.atomic_write_json
        ) as writes:
            with self.assertRaises(BindingPrepareError) as raised:
                state.prepare(_RUN, "server", str(profiles))
        self.assertEqual(raised.exception.code, "instance_config_missing")
        self.assertEqual(writes.call_args_list, [])
        self.assertEqual(list(profiles.iterdir()), [])
        self.assertEqual(state._profile_holders, {})

    def test_prepare_reserves_sidecar_space_for_launch_failed_instance(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        limit = 1024 * 1024
        original = _installer(pad="")
        prior = {
            "version": 1,
            "kind": "present",
            "document": original,
            "prepared_sha256": "a" * 64,
        }

        def encoded(payload: object) -> bytes:
            return (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode("utf-8")

        original["pad"] = "A" * (limit - len(encoded(prior)))
        minted = "11111111-1111-4111-8111-111111111111"
        prepared = {**original, "profile": "test", "instance": minted}
        preserved_prior = {**prior, "document": {**original, "instance": minted}}
        self.assertEqual(len(encoded(prior)), limit)
        self.assertLessEqual(len(encoded(original)), limit)
        self.assertLessEqual(len(encoded(prepared)), limit)
        self.assertGreater(len(encoded(preserved_prior)), limit)
        before = encoded(original)
        config.write_bytes(before)
        state = _state()
        with patch.object(
            runtime_state, "atomic_write_json", wraps=runtime_state.atomic_write_json
        ) as writes:
            with self.assertRaises(BindingPrepareError) as raised:
                state.prepare(_RUN, "server", str(profiles))
        self.assertEqual(raised.exception.code, "instance_config_missing")
        self.assertEqual(writes.call_args_list, [])
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse(_prior_path(config).exists())
        self.assertEqual(state._profile_holders, {})
        self._assert_no_tmp(profiles)

    def test_named_prepare_with_invalid_local_endpoint_preserves_existing_files(self) -> None:
        for key, port in (("live-key", None), ("live-key", True), ("", 8765)):
            with self.subTest(key=key, port=port):
                profiles = self._leaf(self._tmp(), leaf="profiles-job5b")
                config = profiles / "dayz_mcp.json"
                _write(config, _installer(note="deployed"))
                sidecar = _prior_path(config)
                _write(sidecar, {"version": 1, "kind": "absent"})
                before = {path: path.read_bytes() for path in (config, sidecar)}
                state = _state(key, port)
                state.instance_token = "job5b"
                with patch.object(
                    runtime_state, "atomic_write_json", wraps=runtime_state.atomic_write_json
                ) as writes:
                    with self.assertRaises(BindingPrepareError) as raised:
                        state.prepare(_RUN, "server", str(profiles))
                self.assertEqual(raised.exception.code, "instance_endpoint_mismatch")
                self.assertEqual(writes.call_args_list, [])
                self.assertEqual(state._profile_holders, {})
                for path, contents in before.items():
                    self.assertEqual(path.read_bytes(), contents)
                self._assert_no_tmp(profiles)

    def test_default_prepare_preserves_a_deployed_endpoint_and_stamps_instance(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer(url="http://127.0.0.1:2402/", key="installer-key", note="keep")
        _write(config, original)
        state = _state()
        minted = state.prepare(_RUN, "server", str(profiles))
        written = _load(config)
        self.assertIsNotNone(written)
        assert written is not None
        self.assertEqual(written["instance"], minted)
        self.assertEqual({name: written[name] for name in original}, original)
        self.assertNotEqual(written.get("profile"), "test")
        self.assertEqual(state._profile_holders, {})

    def test_failed_reprepare_keeps_the_previous_config_and_recoverable_sidecar(self) -> None:
        for kind in ("absent", "present"):
            for failure in ("permission", "noop"):
                with self.subTest(kind=kind, failure=failure):
                    profiles = self._profiles()
                    config = profiles / "dayz_mcp.json"
                    original = _installer(note="before-first-run")
                    if kind == "present":
                        _write(config, original)
                    _state().prepare("dead-run", "server", str(profiles))
                    sidecar = _prior_path(config)
                    config_before = config.read_bytes()
                    prior_before = sidecar.read_bytes()
                    real_write = runtime_state.atomic_write_json
                    attempted_config = False

                    def write(path: Path, payload: object) -> None:
                        nonlocal attempted_config
                        if Path(path) == config:
                            attempted_config = True
                            if failure == "permission":
                                raise PermissionError(13, "fixture config write denied")
                            return
                        real_write(path, payload)

                    retry = _state()
                    with patch.object(runtime_state, "atomic_write_json", write):
                        with self.assertRaises((BindingPrepareError, OSError)):
                            retry.prepare(_RUN, "server", str(profiles))
                    self.assertTrue(attempted_config)
                    self.assertEqual(config.read_bytes(), config_before)
                    self.assertEqual(sidecar.read_bytes(), prior_before)
                    self.assertEqual(retry._profile_holders, {})
                    self._assert_no_tmp(profiles)

                    fresh = _state()
                    self.assertEqual(fresh.recover_crashed_profile(str(profiles)), [])
                    self.assertEqual(_load(config), original if kind == "present" else None)
                    self.assertFalse(sidecar.exists())
                    self.assertEqual(fresh._profile_pending_messages(), [])

    def test_prepare_write_that_replaces_then_raises_still_rolls_back(self) -> None:
        for kind in ("absent", "present"):
            with self.subTest(kind=kind):
                profiles = self._profiles()
                config = profiles / "dayz_mcp.json"
                original = _installer(note="before-failed-write")
                if kind == "present":
                    _write(config, original)
                real_write = runtime_state.atomic_write_json
                raised_after_write = False

                def write(path: Path, payload: object) -> None:
                    nonlocal raised_after_write
                    real_write(path, payload)
                    if Path(path) == config and not raised_after_write:
                        raised_after_write = True
                        raise OSError(5, "fixture failure after replace")

                state = _state()
                with patch.object(runtime_state, "atomic_write_json", write):
                    with self.assertRaises((BindingPrepareError, OSError)):
                        state.prepare(_RUN, "server", str(profiles))
                self.assertTrue(raised_after_write)
                self.assertEqual(_load(config), original if kind == "present" else None)
                self.assertFalse(_prior_path(config).exists())
                self.assertEqual(state._profile_holders, {})
                self.assertEqual(state.profile_release_errors, [])
                self._assert_no_tmp(profiles)

    def test_installer_replacement_after_recovery_read_is_preserved(self) -> None:
        for kind in ("absent", "present"):
            with self.subTest(kind=kind):
                profiles = self._profiles()
                config = profiles / "dayz_mcp.json"
                if kind == "present":
                    _write(config, _installer(note="old-installer"))
                _state().prepare("dead-run", "server", str(profiles))
                sidecar = _prior_path(config)
                prior_before = sidecar.read_bytes()
                replacement = (
                    json.dumps(_installer(note="new-installer"), indent=2) + "\n\n"
                ).encode("utf-8")
                fresh = _state()
                real_write = runtime_state._atomic_write_text
                real_unlink = fresh._unlink_plain
                replaced = False

                def write(path: Path, text: str, **kwargs: object) -> None:
                    nonlocal replaced
                    if Path(path) == config:
                        config.write_bytes(replacement)
                        replaced = True
                    real_write(path, text, **kwargs)

                def unlink(path: Path, **kwargs: object) -> None:
                    nonlocal replaced
                    if Path(path) == config:
                        config.write_bytes(replacement)
                        replaced = True
                    real_unlink(path, **kwargs)

                with (
                    patch.object(runtime_state, "_atomic_write_text", write),
                    patch.object(fresh, "_unlink_plain", unlink),
                ):
                    errors = fresh.recover_crashed_profile(str(profiles))
                self.assertTrue(replaced, "fixture must replace the installer during recovery")
                self.assertTrue(config.is_file())
                self.assertEqual(config.read_bytes(), replacement)
                self.assertEqual(sidecar.read_bytes(), prior_before)
                self.assertTrue(errors)
                self.assertTrue(fresh._profile_pending_messages())
                self._assert_no_tmp(profiles)

    def test_launch_failed_retirement_clears_profile_but_keeps_the_minted_instance(self) -> None:
        profiles = self._profiles()
        config = profiles / "dayz_mcp.json"
        original = _installer(note="keep")
        _write(config, original)
        state = _state()
        minted = state.prepare(_RUN, "server", str(profiles))
        state.retire_role(_RUN, "server", "launch_failed")
        self.assertEqual(_load(config), {**original, "instance": minted})
        self.assertIn(minted, state._retired_instances)
        self.assertFalse(_prior_path(config).exists())
        self.assertEqual(state.profile_release_errors, [])

    def test_launch_failed_instance_survives_restore_failure_and_later_recovery(self) -> None:
        for retry_mode in ("same_process", "new_process"):
            with self.subTest(retry_mode=retry_mode):
                profiles = self._profiles()
                config = profiles / "dayz_mcp.json"
                original = _installer(profile="live", note="before-launch")
                _write(config, original)
                state = _state()
                minted = state.prepare(_RUN, "server", str(profiles))
                prepared_before = config.read_bytes()
                real_write = runtime_state._atomic_write_text

                def write(path: Path, text: str, **kwargs: object) -> None:
                    if Path(path) == config:
                        raise PermissionError(13, "fixture config restore denied")
                    real_write(path, text, **kwargs)

                with patch.object(runtime_state, "_atomic_write_text", write):
                    state.retire_role(_RUN, "server", "launch_failed")
                self.assertEqual(config.read_bytes(), prepared_before)
                self.assertTrue(_prior_path(config).is_file())
                self.assertTrue(
                    any("PermissionError" in error for error in state.profile_release_errors)
                )
                self.assertIn(minted, state._retired_instances)

                recovery = state if retry_mode == "same_process" else _state()
                self.assertEqual(recovery.recover_crashed_profile(str(profiles)), [])
                self.assertEqual(_load(config), {**original, "instance": minted})
                self.assertFalse(_prior_path(config).exists())
                self.assertEqual(recovery._profile_pending_messages(), [])
                self._assert_no_tmp(profiles)

    def test_legacy_prior_does_not_restore_or_delete_even_a_test_profile(self) -> None:
        for kind in ("absent", "present"):
            with self.subTest(kind=kind):
                profiles = self._profiles()
                config = profiles / "dayz_mcp.json"
                config.write_bytes(b'{ "profile": "test", "instance": "legacy-run" }\n')
                prior: dict[str, object] = {"version": 1, "kind": kind}
                if kind == "present":
                    prior["document"] = _installer(note="unproven-legacy")
                sidecar = _prior_path(config)
                _write(sidecar, prior)
                before = {path: path.read_bytes() for path in (config, sidecar)}
                fresh = _state()
                errors = fresh.recover_crashed_profile(str(profiles))
                self.assertTrue(errors)
                self.assertTrue(fresh._profile_pending_messages())
                for path, contents in before.items():
                    self.assertTrue(path.is_file())
                    self.assertEqual(path.read_bytes(), contents)

    def test_matching_hash_does_not_authorize_recovery_of_invalid_json(self) -> None:
        for invalid in (b'{"profile":"test",broken', b"", b"[]"):
            with self.subTest(invalid=invalid):
                profiles = self._profiles()
                config = profiles / "dayz_mcp.json"
                config.write_bytes(invalid)
                sidecar = _prior_path(config)
                _write(sidecar, {
                    "version": 1,
                    "kind": "present",
                    "document": _installer(),
                    "prepared_sha256": hashlib.sha256(invalid).hexdigest(),
                })
                before = sidecar.read_bytes()
                fresh = _state()
                errors = fresh.recover_crashed_profile(str(profiles))
                self.assertTrue(errors)
                self.assertTrue(any(str(config) in error for error in errors))
                self.assertTrue(fresh._profile_pending_messages())
                self.assertEqual(config.read_bytes(), invalid)
                self.assertEqual(sidecar.read_bytes(), before)

    def test_failed_sidecar_unlink_does_not_reapply_a_prior_without_a_matching_config(self) -> None:
        for kind in ("absent", "present"):
            with self.subTest(kind=kind):
                profiles = self._profiles()
                config = profiles / "dayz_mcp.json"
                original = _installer(note="keep")
                if kind == "present":
                    _write(config, original)
                _state().prepare(_RUN, "server", str(profiles))
                sidecar = _prior_path(config)
                prior_before = sidecar.read_bytes()
                real_unlink = Path.unlink

                def unlink(path: Path, *args: object, **kwargs: object) -> None:
                    if path == sidecar:
                        raise PermissionError(13, "fixture access denied")
                    return real_unlink(path, *args, **kwargs)

                fresh = _state()
                with patch.object(Path, "unlink", unlink):
                    errors = fresh.recover_crashed_profile(str(profiles))
                self.assertTrue(any("PermissionError" in error for error in errors))
                self.assertTrue(any(str(sidecar) in error for error in errors))
                self.assertEqual(config.exists(), kind == "present")
                if kind == "present":
                    self.assertEqual(_load(config), original)
                restored_bytes = config.read_bytes() if config.exists() else None
                errors = fresh.recover_crashed_profile(str(profiles))
                self.assertTrue(errors, "unproven sidecar must not be silently accepted on retry")
                self.assertTrue(fresh._profile_pending_messages())
                self.assertEqual(sidecar.read_bytes(), prior_before)
                self.assertEqual(config.read_bytes() if config.exists() else None, restored_bytes)

                # An operator can remove this stale sidecar after verifying the restored file.
                sidecar.unlink()
                self.assertEqual(fresh.recover_crashed_profile(str(profiles)), [])
                self.assertNotIn("profile_restore_errors", fresh.status_snapshot())

    def test_noop_mission_restore_keeps_config_and_sidecar_pending(self) -> None:
        for kind in ("absent", "present"):
            with self.subTest(kind=kind):
                root = self._tmp()
                profiles = self._leaf(root)
                config = profiles / "dayz_mcp.json"
                if kind == "present":
                    _write(config, _installer())
                mission = root / "mpmissions" / "mission" / "dayz_mcp.json"
                _write(mission, {"profile": "test", "keep": "mission"})
                _state().prepare(_RUN, "server", str(profiles))
                before = {
                    path: path.read_bytes() for path in (config, _prior_path(config), mission)
                }
                real_write = runtime_state._atomic_write_text

                def write(path: Path, text: str, **kwargs: object) -> None:
                    if Path(path) == mission:
                        return
                    real_write(path, text, **kwargs)

                fresh = _state()
                with patch.object(runtime_state, "_atomic_write_text", write):
                    errors = fresh.recover_crashed_profile(str(profiles))
                self.assertTrue(any(str(mission) in error for error in errors))
                self.assertTrue(fresh._profile_pending_messages())
                self.assertTrue(fresh._pending_profile_test_remains())
                for path, contents in before.items():
                    self.assertEqual(path.read_bytes(), contents)

    def test_stale_prior_does_not_replace_or_delete_a_new_installer_config(self) -> None:
        for kind in ("absent", "present"):
            for fingerprinted in (False, True):
                with self.subTest(kind=kind, fingerprinted=fingerprinted):
                    profiles = self._profiles()
                    config = profiles / "dayz_mcp.json"
                    old_test = b'{"profile":"test","instance":"old-run"}\n'
                    prior: dict[str, object] = {"version": 1, "kind": kind}
                    if kind == "present":
                        prior["document"] = _installer(note="old-installer")
                    if fingerprinted:
                        prior["prepared_sha256"] = hashlib.sha256(old_test).hexdigest()
                    sidecar = _prior_path(config)
                    _write(sidecar, prior)
                    prior_before = sidecar.read_bytes()
                    replacement = (json.dumps(_installer(note="new-installer"), indent=2) + "\n\n").encode()
                    config.write_bytes(replacement)
                    fresh = _state()
                    errors = fresh.recover_crashed_profile(str(profiles))
                    self.assertTrue(config.is_file(), "stale absent prior deleted new installer")
                    self.assertEqual(config.read_bytes(), replacement)
                    self.assertTrue(errors, "stale prior must remain visible to the operator")
                    self.assertTrue(fresh._profile_pending_messages())
                    self.assertTrue(sidecar.is_file())
                    self.assertEqual(sidecar.read_bytes(), prior_before)

    def test_new_sidecar_fingerprints_the_written_config_using_the_atomic_writer(self) -> None:
        for kind in ("absent", "present"):
            with self.subTest(kind=kind):
                profiles = self._profiles()
                config = profiles / "dayz_mcp.json"
                if kind == "present":
                    _write(config, _installer(note="keep-me"))
                writes: list[tuple[Path, object]] = []
                real_write = runtime_state.atomic_write_json

                def record_write(path: Path, payload: object) -> None:
                    writes.append((Path(path), json.loads(json.dumps(payload))))
                    real_write(path, payload)

                state = _state()
                with patch.object(runtime_state, "atomic_write_json", record_write):
                    state.prepare(_RUN, "server", str(profiles))
                persisted = _prior(config)
                self.assertIsNotNone(persisted)
                assert persisted is not None
                self.assertEqual(persisted.get("kind"), kind)
                self.assertEqual(
                    persisted.get("prepared_sha256"),
                    hashlib.sha256(config.read_bytes()).hexdigest(),
                )
                self.assertIn((_prior_path(config), persisted), writes)
                self.assertEqual(_load(config)["profile"], "test")
                self._assert_no_tmp(profiles)

    def test_unreadable_config_or_mission_with_or_without_sidecar_stays_pending(self) -> None:
        for location in ("config", "mission"):
            for has_prior in (False, True):
                for failure in ("permission", "oserror", "json", "empty"):
                    with self.subTest(location=location, has_prior=has_prior, failure=failure):
                        root = self._tmp()
                        profiles = self._leaf(root)
                        config = profiles / "dayz_mcp.json"
                        mission = root / "mpmissions" / "mission" / "dayz_mcp.json"
                        _write(config, _installer())
                        _write(mission, {"profile": "live", "keep": "mission"})
                        if has_prior:
                            _state().prepare(_RUN, "server", str(profiles))
                        target = config if location == "config" else mission
                        if not has_prior or location == "mission":
                            _write(target, {"profile": "test", "keep": "unreadable"})
                        if failure == "json":
                            target.write_bytes(b'{"profile":"test",broken')
                        elif failure == "empty":
                            target.write_bytes(b"")
                        before = target.read_bytes()
                        sidecar = _prior_path(config)
                        prior_before = sidecar.read_bytes() if has_prior else None
                        real_read = Path.read_text

                        def read(path: Path, *args: object, **kwargs: object) -> str:
                            if path == target and failure in {"permission", "oserror"}:
                                if failure == "permission":
                                    raise PermissionError(13, "fixture access denied")
                                raise OSError(5, "fixture input/output failure")
                            return real_read(path, *args, **kwargs)

                        fresh = _state()
                        with patch.object(Path, "read_text", read):
                            errors = fresh.recover_crashed_profile(str(profiles))
                            self.assertTrue(errors, "an unreadable file is not a clean recovery")
                            self.assertTrue(any(str(target) in error for error in errors))
                            self.assertTrue(fresh._profile_pending_messages())
                            self.assertTrue(fresh._pending_profile_test_remains())
                        self.assertEqual(target.read_bytes(), before)
                        self.assertEqual(sidecar.exists(), has_prior)
                        if has_prior:
                            self.assertEqual(sidecar.read_bytes(), prior_before)

    def test_adopt_already_gone_is_not_success_after_a_noop_restore(self) -> None:
        from dayz_mcp import dayz_test_worker

        for answer in (
            {"error": "run_processes_gone"},
            {"error": "run_not_found"},
            {"ok": True, "run_id": _RUN, "state": "EXITED"},
            {"stop_method": "no_live_owned"},
        ):
            with self.subTest(answer=answer):
                profiles = self._profiles()
                config = profiles / "dayz_mcp.json"
                _write(config, _installer())
                state = _state()
                state.prepare(_RUN, "server", str(profiles))

                class Lifecycle:
                    def adopt_run(self, client: object, token: object, run_id: object) -> dict:
                        return dict(answer)

                state.lifecycle = Lifecycle()
                with patch.object(runtime_state, "_atomic_write_text", return_value=None):
                    state.retire_run(_RUN, "stopped")
                    status, payload = self._reply(state, "adopt")
                self.assertEqual(_load(config)["profile"], "test")
                self.assertGreaterEqual(status, 400)
                self.assertEqual(payload.get("error"), "profile_restore_failed")
                self.assertFalse(dayz_test_worker._run_already_gone(payload, _RUN))
                self.assertFalse(dayz_test_worker._successful_run(payload, _RUN, "EXITED"))

    def test_public_close_cannot_accept_retired_diagnostics_while_profile_test_remains(self) -> None:
        import asyncio

        from dayz_mcp import dayz_test_request, dayz_test_tool

        for write_mode in ("normal", "permission", "noop"):
            with self.subTest(write_mode=write_mode):
                root = self._tmp()
                profiles = self._leaf(root, leaf="profiles")
                config = profiles / "dayz_mcp.json"
                _write(config, _installer())
                state = _state()
                state.prepare(_RUN, "server", str(profiles))
                rpt = profiles / "server.rpt"
                rpt.write_text("boot server\n", encoding="utf-8")
                policy = dayz_test_request.RequestProjectPolicy(
                    mod="ExampleMod",
                    dev_root=str(root),
                    default_source=str(root),
                    default_base_mods=(),
                    mission_roots=(str(root / "mpmissions"),),
                    mod_roots=(str(root),),
                )

                class Lifecycle:
                    retired = False

                    def close_run(self, client: object, token: object, run_id: object) -> dict:
                        with rpt.open("a", encoding="utf-8") as stream:
                            stream.write("--- Termination successfully completed ---\n")
                        return {
                            "run_id": run_id,
                            "server": {"pid": 1001, "windows_found": 1, "windows_posted": 1},
                        }

                    def reap_dead_run(self, client: object, token: object, run_id: object) -> dict:
                        self.retired = True
                        state.retire_run(str(run_id), "reaped")
                        return {"ok": True, "run_id": run_id, "state": "EXITED"}

                    def status(self, client: object) -> dict:
                        if self.retired:
                            return {
                                "runs": [],
                                "retired_run_diagnostics": [{
                                    "run_id": _RUN,
                                    "daemon_generation_at_launch": "",
                                    "daemon_generation_current": "",
                                    "generation_changed": False,
                                    "event": "run_reaped",
                                    "reason": "all_processes_gone_or_foreign",
                                    "decision": "reaped",
                                    "state": "EXITED",
                                }],
                            }
                        return {
                            "runs": [{
                                "run_id": _RUN,
                                "state": "RUNNING",
                                "mod": "@ExampleMod",
                                "profiles": str(profiles),
                                "processes": [{"role": "server", "pid": 1001}],
                            }],
                            "retired_run_diagnostics": [],
                        }

                state.lifecycle = Lifecycle()
                replies: list[tuple[str, dict]] = []
                case = self

                class Runtime:
                    active_lease_token = "token"

                    async def lifecycle_status(self) -> dict:
                        _status, payload = case._reply(state, "status")
                        replies.append(("status", payload))
                        return payload

                    async def lifecycle_close(self, run_id: str) -> dict:
                        _status, payload = case._reply(state, "close")
                        replies.append(("close", payload))
                        return payload

                    async def lifecycle_reap(self, run_id: str) -> dict:
                        _status, payload = case._reply(state, "reap")
                        replies.append(("reap", payload))
                        return payload

                real_write = runtime_state._atomic_write_text

                def restore_write(path: Path, text: str, **kwargs: object) -> None:
                    if Path(path) == config and write_mode != "normal":
                        if write_mode == "permission":
                            raise PermissionError(13, "fixture access denied")
                        return
                    real_write(path, text, **kwargs)

                with (
                    patch.object(dayz_test_tool, "_close_project_policy", return_value=policy),
                    patch.object(runtime_state, "_atomic_write_text", restore_write),
                ):
                    result = asyncio.run(dayz_test_tool.execute_dayz_test_close(
                        Runtime(), _RUN, graceful_timeout_s=0.5
                    ))
                self.assertTrue(any(action == "reap" for action, _payload in replies))
                if write_mode == "normal":
                    self.assertTrue(result["graceful"], "positive control must reach graceful close")
                    self.assertFalse(result["stop_required"])
                    self.assertNotEqual(_load(config).get("profile"), "test")
                else:
                    self.assertEqual(_load(config)["profile"], "test")
                    self.assertFalse(result["graceful"])
                    self.assertFalse(result["exit_metrics_valid"])
                    self.assertTrue(result["stop_required"])
                    self.assertFalse(result["run_retired"])


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
