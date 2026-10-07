"""Named-instance profile leaves (g5fix). Paths below are literals, not the helper."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import (
    client_steam_bootstrap,
    dayz_test_attestation,
    dayz_test_request,
    dayz_test_tool,
    dayz_test_worker,
    launch_logs,
    log_tail,
    native_launcher_transaction,
    process_lifecycle,
    server,
)
from dayz_mcp.dayz_test_attestation import (
    InitializationRequirement,
    ProjectAttestation,
    initialization_rows,
    profile_directory,
    report,
)
from dayz_mcp.loopback import BindingPrepareError, ServerState
from dayz_mcp.server import ServerConfig, ToolError, build_app, execute_wait_for
from dayz_mcp.server_cli import (
    InstanceSelectionError,
    bind_instance_context,
    profile_leaf_name,
    reset_instance_context_for_tests,
)
from tests.mcp_helpers import _content_json
from tests.wait_for_helpers import PinnedClosePolicy

TOKEN = "130"
LEAF = "profiles-130"
OTHER = "profiles-131"
RUN_ID = "run-g5fix"
TERMINATION = "--- Termination successfully completed ---\n"
CONNECT = 'Player "Ada" (id=1) is connected\n'
LINK = "[StateMachine]: Player Ada (uid 76561198000000001)\n"
LOGOUT = "[Logout]: Player 76561198000000001 finished\n"
SESSION = "session-g5fix-owner"
LEASE = "lease-g5fix"


@contextmanager
def bound(token: str | None):
    bind_instance_context(token, replace=True)
    try:
        yield
    finally:
        reset_instance_context_for_tests()


def _policy(root: Path) -> dayz_test_request.RequestProjectPolicy:
    return dayz_test_request.RequestProjectPolicy(
        mod="ExampleMod",
        dev_root=str(root),
        default_source=str(root),
        default_base_mods=(),
        mission_roots=(str(root / "_server" / "mpmissions"),),
        mod_roots=(str(root),),
    )


def _roles(root: Path) -> None:
    (root / "_server").mkdir()
    (root / "_client").mkdir()


def _state(token: str | None, key: str = "named-key", port: int = 8771) -> ServerState:
    state = ServerState(key, config_port=port)
    state.instance_token = token
    return state


class ProfileLeafContractTest(unittest.TestCase):
    def test_omitted_token_is_profiles_and_a_valid_token_is_the_named_leaf(self) -> None:
        self.assertEqual(profile_leaf_name(None), "profiles")
        self.assertEqual(profile_leaf_name("130"), "profiles-130")
        with self.assertRaises(InstanceSelectionError):
            profile_leaf_name("130/../x")
        with self.assertRaises(InstanceSelectionError):
            profile_leaf_name("")


class NamedPrepareTest(unittest.TestCase):
    def test_fresh_named_prepare_creates_only_the_leaf_and_seeds_before_spawn(self) -> None:
        """Fails on the base: prepare required the leaf and refused to create it."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _roles(root)
            target = root / "_server" / LEAF
            self.assertFalse(target.exists())
            state = _state(TOKEN)
            minted = state.prepare("run-1", "server", str(target))
            self.assertTrue(target.is_dir())
            self.assertFalse((root / "_client" / LEAF).exists())
            payload = json.loads((target / "dayz_mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["url"], "http://127.0.0.1:8771/")
            self.assertEqual(payload["key"], "named-key")
            self.assertEqual(payload["instance"], minted)
            self.assertEqual(payload["pollHz"], 5)
            client = root / "_client" / LEAF
            client_id = _state(TOKEN).prepare("run-1", "client", str(client))
            self.assertTrue(client.is_dir())
            self.assertTrue(client_id)

    def test_missing_role_root_file_leaf_escape_denial_and_foreign_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = _state(TOKEN)
            missing = root / "no-such-project" / "_server" / LEAF
            with self.assertRaises(BindingPrepareError) as raised:
                state.prepare("run", "server", str(missing))
            self.assertEqual(raised.exception.code, "instance_config_missing")
            self.assertFalse((root / "no-such-project").exists())

            _roles(root)
            wrong = root / "_server" / "profiles"
            wrong.mkdir()
            with self.assertRaises(BindingPrepareError) as raised:
                state.prepare("run", "server", str(wrong))
            self.assertEqual(raised.exception.code, "instance_profile_owner_mismatch")
            self.assertFalse((root / "_server" / LEAF).exists())

            leaf_file = root / "_server" / LEAF
            leaf_file.write_text("not-a-dir", encoding="utf-8")
            with self.assertRaises(BindingPrepareError) as raised:
                state.prepare("run", "server", str(leaf_file))
            self.assertEqual(raised.exception.code, "instance_config_missing")
            self.assertFalse((leaf_file.parent / "dayz_mcp.json").exists())
            self.assertEqual(leaf_file.read_text(encoding="utf-8"), "not-a-dir")
            leaf_file.unlink()

            outside = root / "outside"
            outside.mkdir()
            link = root / "_server" / LEAF
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True,
            )
            if created.returncode == 0:
                with self.assertRaises(BindingPrepareError) as raised:
                    state.prepare("run", "server", str(link))
                self.assertEqual(raised.exception.code, "instance_config_missing")
                self.assertFalse((outside / "dayz_mcp.json").exists())
                os.rmdir(link)
            else:
                self.skipTest("junctions are not available")

            def _deny(self, mode=0o777, parents=False, exist_ok=False):
                raise PermissionError(13, "denied")

            with patch.object(Path, "mkdir", _deny):
                with self.assertRaises(BindingPrepareError) as raised:
                    state.prepare("run", "server", str(root / "_server" / LEAF))
            self.assertEqual(raised.exception.code, "instance_config_missing")
            self.assertFalse((root / "_server" / LEAF).exists())

            foreign_dir = root / "_client" / LEAF
            foreign_dir.mkdir()
            foreign = foreign_dir / "dayz_mcp.json"
            original = {"url": "http://127.0.0.1:1/", "key": "other", "pollHz": 5}
            foreign.write_text(json.dumps(original), encoding="utf-8")
            with self.assertRaises(BindingPrepareError) as raised:
                state.prepare("run", "offline", str(foreign_dir))
            self.assertEqual(raised.exception.code, "instance_endpoint_mismatch")
            self.assertEqual(json.loads(foreign.read_text(encoding="utf-8")), original)

            bare = _state(TOKEN, key="")
            with self.assertRaises(BindingPrepareError) as raised:
                bare.prepare("run", "server", str(root / "_server" / LEAF))
            self.assertEqual(raised.exception.code, "instance_config_missing")
            self.assertFalse((root / "_server" / LEAF).exists())

    def test_concurrent_create_is_kept_only_when_the_leaf_is_the_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _roles(root)
            target = root / "_server" / LEAF

            real = Path.mkdir

            def _race(self, mode=0o777, parents=False, exist_ok=False):
                leaf = os.path.normcase(str(self)) == os.path.normcase(str(target))
                if leaf and not exist_ok:
                    real(self, parents=False)
                    raise FileExistsError(self)
                return real(self, mode=mode, parents=parents, exist_ok=exist_ok)

            with patch.object(Path, "mkdir", _race):
                minted = _state(TOKEN).prepare("run", "server", str(target))
            self.assertTrue(minted)
            self.assertTrue((target / "dayz_mcp.json").is_file())

    def test_default_missing_folder_is_not_created_and_existing_bytes_keep_uuid_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _roles(root)
            missing = root / "_server" / "profiles"
            with self.assertRaises(BindingPrepareError) as raised:
                _state(None).prepare("run", "server", str(missing))
            self.assertEqual(raised.exception.code, "instance_config_missing")
            self.assertFalse(missing.exists())

            missing.mkdir()
            config = missing / "dayz_mcp.json"
            original = {
                "url": "http://127.0.0.1:8771/",
                "key": "named-key",
                "pollHz": 5,
                "instance": "11111111-1111-4111-8111-111111111111",
            }
            config.write_text(json.dumps(original), encoding="utf-8")
            before = config.read_bytes()
            minted = _state(None).prepare("run", "server", str(missing))
            after = json.loads(config.read_text(encoding="utf-8"))
            self.assertNotEqual(config.read_bytes(), before)
            self.assertEqual(after["instance"], minted)
            self.assertNotEqual(after["instance"], original["instance"])
            self.assertEqual(
                {key: after[key] for key in ("url", "key", "pollHz")},
                {key: original[key] for key in ("url", "key", "pollHz")},
            )


class ModePathParityTest(unittest.TestCase):
    def _runtime(self, root: Path) -> dayz_test_worker.WorkerRuntimePolicy:
        return dayz_test_worker.WorkerRuntimePolicy(
            dev_root=str(root),
            mod="Example",
            diag_executable=str(root / "DayZDiag_x64.exe"),
            game_directory=str(root),
            mission_aliases=(),
            mods_root=str(root / "mods"),
            build_temp_root=str(root / "temp"),
            build_source_basename=None,
        )

    def _payload(self, root: Path, mode: str) -> dict:
        mission = root / "mission.chernarusplus"
        mission.mkdir(exist_ok=True)
        return {
            "mission": str(mission),
            "base_mods": [],
            "extra_mods": [],
            "port": 2302,
            "no_file_patching": True,
            "player_name": "A",
            "width": 640,
            "height": 480,
            "navmesh_data_server": False,
            "server_mods": [],
            "mode": mode,
            "instance_token": TOKEN,
        }

    def test_token_130_launch_paths_anchor_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, bound(TOKEN):
            root = Path(temporary)
            runtime = self._runtime(root)
            policy = _policy(root)
            expected = {
                "server": str(root / "_server" / LEAF),
                "client": str(root / "_client" / LEAF),
                "offline": str(root / "_client" / LEAF),
            }
            for role in ("server", "client", "offline"):
                core = dayz_test_worker._start_core(
                    self._payload(root, role if role != "client" else "client"),
                    runtime,
                    role=role,
                    run_id=None,
                )
                profiles = str(core["profiles"])
                self.assertEqual(profiles, expected[role])
                self.assertIn("-profiles=" + profiles, core["argv"])
            all_server = dayz_test_worker._start_core(
                self._payload(root, "all"), runtime, role="server", run_id=None
            )
            all_client = dayz_test_worker._start_core(
                self._payload(root, "all"), runtime, role="client", run_id="run"
            )
            self.assertEqual(str(all_server["profiles"]), expected["server"])
            self.assertEqual(str(all_client["profiles"]), expected["client"])
            artifacts = dayz_test_tool._artifact_paths(policy, "all")
            self.assertEqual(artifacts, [expected["server"], expected["client"]])
            result = dayz_test_tool._compact_result(
                terminal=dayz_test_tool.WorkerTerminal(
                    cleanup_degraded=False,
                    error_code=None,
                    exit_code=0,
                    ok=True,
                    run_id="run",
                ),
                project="ExampleMod",
                mode="all",
                started_at=0.0,
                artifacts_paths=artifacts,
            )
            self.assertEqual(result["artifacts_paths"], artifacts)
            run = {"profiles": expected["server"], "processes": [{"role": "server"}]}
            self.assertEqual(
                dayz_test_tool._stop_artifacts(policy, run),
                [expected["server"], expected["client"]],
            )
            run["processes"] = [{"role": "client"}]
            run["profiles"] = expected["client"]
            self.assertEqual(
                dayz_test_tool._stop_artifacts(policy, run),
                [expected["client"]],
            )

    def test_default_argv_strings_stay_on_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self._payload(root, "server")
            del payload["instance_token"]
            core = dayz_test_worker._start_core(
                payload, self._runtime(root), role="server", run_id=None
            )
            profiles = str(root / "_server" / "profiles")
            self.assertEqual(str(core["profiles"]), profiles)
            self.assertIn("-profiles=" + profiles, list(core["argv"]))
            self.assertNotIn(LEAF, " ".join(str(item) for item in core["argv"]))
            reset_instance_context_for_tests()
            self.assertEqual(
                dayz_test_tool._artifact_paths(_policy(root), "server"),
                [profiles],
            )


class LogAndWaitTest(PinnedClosePolicy, unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        bind_instance_context(TOKEN, replace=True)

    def tearDown(self) -> None:
        reset_instance_context_for_tests()

    def _stamp(self) -> str:
        return (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )

    def _run(self, profiles: Path, *roles: str) -> dict:
        return {
            "run_id": "run-a",
            "state": "RUNNING",
            "mod": "@ExampleMod",
            "profiles": str(profiles),
            "owner_session": SESSION,
            "owner_session_id": SESSION,
            "owner_lease_id": LEASE,
            "processes": [
                {"role": role, "pid": index + 1, "creation_time_utc": self._stamp()}
                for index, role in enumerate(roles)
            ],
        }

    def test_named_logs_are_admitted_and_siblings_keep_the_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server_leaf = root / "_server" / LEAF
            client_leaf = root / "_client" / LEAF
            legacy_server = root / "_server" / "profiles"
            other_client = root / "_client" / OTHER
            for path in (server_leaf, client_leaf, legacy_server, other_client):
                path.mkdir(parents=True)
            (server_leaf / "script.log").write_text("NAMED-SERVER\n", encoding="utf-8")
            (client_leaf / "script.log").write_text("NAMED-CLIENT\n", encoding="utf-8")
            (legacy_server / "script.log").write_text("LEGACY\n", encoding="utf-8")
            (other_client / "script.log").write_text("OTHER-TOKEN\n", encoding="utf-8")
            self.assertTrue(log_tail.is_allowed_profiles_dir(str(server_leaf)))
            self.assertFalse(log_tail.is_allowed_profiles_dir(str(legacy_server)))
            self.assertFalse(log_tail.is_allowed_profiles_dir(str(other_client)))
            dirs = launch_logs._sibling_profile_dirs([str(server_leaf)])
            self.assertEqual(sorted(dirs), sorted([str(server_leaf), str(client_leaf)]))
            text = []
            for folder in dirs:
                for path in log_tail.resolve_log_files(folder):
                    text.extend(log_tail.read_since(path, None)["lines"])
            self.assertIn("NAMED-SERVER", text)
            self.assertIn("NAMED-CLIENT", text)
            self.assertNotIn("LEGACY", text)
            self.assertNotIn("OTHER-TOKEN", text)
            from_client = launch_logs._sibling_profile_dirs([str(client_leaf)])
            self.assertIn(str(server_leaf), from_client)
            self.assertNotIn(str(legacy_server), from_client)

    async def test_logs_since_cursor_and_log_matches_ignore_foreign_leaves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server_leaf = root / "_server" / LEAF
            client_leaf = root / "_client" / LEAF
            legacy = root / "_server" / "profiles"
            for path in (server_leaf, client_leaf, legacy):
                path.mkdir(parents=True)
            script = server_leaf / "script.log"
            script.write_text("boot\n", encoding="utf-8")
            (client_leaf / "DayZDiag_x64.rpt").write_text("client-boot\n", encoding="utf-8")
            (legacy / "script.log").write_text("LEGACY-READY\n", encoding="utf-8")
            (root / "_client" / OTHER).mkdir(parents=True)
            (root / "_client" / OTHER / "script.log").write_text("OTHER-READY\n", encoding="utf-8")
            run = self._run(server_leaf, "server", "client")

            from tests.client_helpers import _fixture_client_runtime

            config = ServerConfig(mode="client", key="k", port=1, log_sink=lambda _m: None)
            holder = _fixture_client_runtime(config)

            async def lifecycle_status():
                return {"runs": [run]}

            holder.lifecycle_status = lifecycle_status
            with patch.object(server, "ClientRuntime", return_value=holder):
                app, _built = build_app(config)
            first = _content_json(await app.call_tool("logs_since", {"run_id": "run-a"}))
            names = {Path(item["path"]).name: item["lines"] for item in first["files"]}
            self.assertEqual(names[script.name], ["boot"])
            self.assertNotIn("LEGACY-READY", str(names))
            idle = _content_json(
                await app.call_tool("logs_since", {"run_id": "run-a", "marker": first["marker"]})
            )
            self.assertEqual(idle["files"], [])
            with script.open("a", encoding="utf-8") as handle:
                handle.write("second\n")
            second = _content_json(
                await app.call_tool("logs_since", {"run_id": "run-a", "marker": idle["marker"]})
            )
            self.assertEqual(
                [line for item in second["files"] for line in item["lines"]],
                ["second"],
            )

            runtime = SimpleNamespace(
                tool_lock=asyncio.Lock(),
                lifecycle_status=lifecycle_status,
            )
            seen = await execute_wait_for(
                runtime,
                "log_matches",
                pattern="client-boot",
                role="server",
                timeout_s=1.0,
                poll_interval_s=0.2,
                lookback_lines=20,
            )
            self.assertTrue(seen["satisfied"])
            missed = await execute_wait_for(
                runtime,
                "log_matches",
                pattern="LEGACY-READY",
                role="client",
                timeout_s=0.3,
                poll_interval_s=0.2,
                lookback_lines=50,
            )
            self.assertFalse(missed["satisfied"])
            script.write_text("early LAUNCHED\nboot\n", encoding="utf-8")
            launched = await execute_wait_for(
                runtime,
                "log_matches",
                pattern="LAUNCHED",
                timeout_s=1.0,
                poll_interval_s=0.2,
                lookback_from="launch",
            )
            self.assertTrue(launched["satisfied"])

    async def test_file_matches_uses_the_named_role_and_invalid_anchors_open_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server_leaf = root / "_server" / LEAF
            client_leaf = root / "_client" / LEAF
            legacy_client = root / "_client" / "profiles"
            for path in (server_leaf, client_leaf, legacy_client):
                path.mkdir(parents=True)
            (server_leaf / "probe.txt").write_text("SERVER\n", encoding="utf-8")
            (client_leaf / "probe.txt").write_text("READY\n", encoding="utf-8")
            (legacy_client / "probe.txt").write_text("LEGACY-READY\n", encoding="utf-8")
            opened: list[str] = []
            real_open = open

            def _spy(file, *args, **kwargs):
                opened.append(str(file))
                return real_open(file, *args, **kwargs)

            policy = _policy(root)
            run = self._run(server_leaf, "server", "client")

            class Runtime:
                def __init__(self) -> None:
                    self.identity = SimpleNamespace(session_id=SESSION)
                    self.tool_lock = asyncio.Lock()
                    self._control = SimpleNamespace(
                        active_lease_token="tok", active_lease_id=LEASE
                    )

                async def lifecycle_status(self):
                    return {"runs": [run]}

                async def session_heartbeat(self, token: str):
                    return {"ok": True}

                async def session_acquire_wait(self, *_args, **_kwargs):
                    raise AssertionError("acquire")

            runtime = Runtime()
            with patch.object(dayz_test_tool, "_close_project_policy", return_value=policy):
                result = await execute_wait_for(
                    runtime,
                    "file_matches",
                    pattern="READY",
                    role="client",
                    profile_file="probe.txt",
                    timeout_s=1.0,
                    poll_interval_s=0.2,
                    lookback_lines=10,
                )
            self.assertTrue(result["satisfied"])
            self.assertEqual(result["role"], "client")
            offline_run = self._run(client_leaf, "offline")
            runtime_offline = Runtime()

            async def _offline_status():
                return {"runs": [offline_run]}

            runtime_offline.lifecycle_status = _offline_status
            with patch.object(dayz_test_tool, "_close_project_policy", return_value=policy):
                offline = await execute_wait_for(
                    runtime_offline,
                    "file_matches",
                    pattern="READY",
                    role="offline",
                    profile_file="probe.txt",
                    timeout_s=1.0,
                    poll_interval_s=0.2,
                    lookback_lines=10,
                )
            self.assertTrue(offline["satisfied"])
            bad = dict(run)
            bad["profiles"] = str(legacy_client)

            async def _bad_status():
                return {"runs": [bad]}

            runtime.lifecycle_status = _bad_status
            with patch.object(dayz_test_tool, "_close_project_policy", return_value=policy):
                with patch("builtins.open", _spy):
                    with self.assertRaises(ToolError) as raised:
                        await execute_wait_for(
                            runtime,
                            "file_matches",
                            pattern="LEGACY-READY",
                            role="client",
                            profile_file="probe.txt",
                            timeout_s=1.0,
                            poll_interval_s=0.2,
                            lookback_lines=10,
                        )
            self.assertEqual(str(raised.exception), "profile_unresolved")
            self.assertFalse(any(str(legacy_client) in item for item in opened))


class CloseAndDiagnosisTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        _roles(self.root)
        self.server_rpt = self.root / "_server" / LEAF / "server.rpt"
        self.client_rpt = self.root / "_client" / LEAF / "client.rpt"
        self.legacy_rpt = self.root / "_server" / "profiles" / "server.rpt"
        self.policy = _policy(self.root)
        self.patcher = patch.object(
            dayz_test_tool, "_close_project_policy", return_value=self.policy
        )
        self.patcher.start()
        bind_instance_context(TOKEN, replace=True)

    def tearDown(self) -> None:
        self.patcher.stop()
        reset_instance_context_for_tests()
        self.temporary.cleanup()

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _runtime(
        self, *, logout: bool, rotate_named: bool = False, append: bool = True
    ) -> SimpleNamespace:
        runtime = SimpleNamespace(
            active_lease_token="held",
            active_ticket=None,
            active_operation_id=None,
            retire_event=None,
            close_calls=0,
        )

        async def lifecycle_status():
            if runtime.retire_event is not None:
                return {
                    "runs": [],
                    "retired_run_diagnostics": [
                        {
                            "run_id": RUN_ID,
                            "daemon_generation_at_launch": "",
                            "daemon_generation_current": "",
                            "generation_changed": False,
                            "event": runtime.retire_event,
                            "reason": "all_processes_gone_or_foreign",
                            "decision": "reaped",
                            "state": "EXITED",
                        }
                    ],
                }
            return {
                "runs": [
                    {
                        "run_id": RUN_ID,
                        "state": "RUNNING",
                        "mod": "@ExampleMod",
                        "profiles": str(self.server_rpt.parent),
                        "processes": [
                            {"role": "client", "pid": 2},
                            {"role": "server", "pid": 1},
                        ],
                    }
                ],
                "retired_run_diagnostics": [],
            }

        async def lifecycle_close_roles(run_id: str, roles: list[str]):
            runtime.close_calls += 1
            for role in roles:
                path = self.client_rpt if role == "client" else self.server_rpt
                if rotate_named and path.is_file():
                    path.unlink()
                    path.write_text("rotated\n" + TERMINATION, encoding="utf-8")
                elif append and path.is_file():
                    if logout and role == "client":
                        self.server_rpt.write_text(
                            self.server_rpt.read_text(encoding="utf-8") + LOGOUT,
                            encoding="utf-8",
                        )
                    path.write_text(
                        path.read_text(encoding="utf-8") + TERMINATION,
                        encoding="utf-8",
                    )
            if self.legacy_rpt.is_file():
                self.legacy_rpt.unlink()
                self.legacy_rpt.write_text("legacy-rotated\n" + TERMINATION, encoding="utf-8")
            result = {"run_id": run_id}
            for role in roles:
                result[role] = {"pid": 1, "windows_found": 1, "windows_posted": 1}
            return result

        async def lifecycle_reap(run_id: str):
            runtime.retire_event = "run_reaped"
            return {"ok": True, "run_id": run_id, "state": "EXITED"}

        async def lifecycle_close(run_id: str):
            return await lifecycle_close_roles(run_id, ["client", "server"])

        runtime.lifecycle_status = lifecycle_status
        runtime.lifecycle_close_roles = lifecycle_close_roles
        runtime.lifecycle_reap = lifecycle_reap
        runtime.lifecycle_close = lifecycle_close
        return runtime

    async def test_named_logout_completes_and_absence_warns(self) -> None:
        self._write(self.server_rpt, "boot\n" + CONNECT + LINK)
        self._write(self.client_rpt, "boot client\n")
        self._write(self.legacy_rpt, "LEGACY " + CONNECT)
        done = await dayz_test_tool.execute_dayz_test_close(
            self._runtime(logout=True), RUN_ID, graceful_timeout_s=2
        )
        self.assertTrue(done["graceful"])
        self.assertTrue(done["exit_metrics_valid"])
        self.assertEqual(done["logout_players"], [{"player": "Ada", "logout_finished": True}])
        self.assertLess(float(done["logout_wait_s"]), 2.0)
        self.assertNotIn("warnings", done)

        self._write(self.server_rpt, "boot\n" + CONNECT + LINK)
        self._write(self.client_rpt, "boot client\n")
        waiting = await dayz_test_tool.execute_dayz_test_close(
            self._runtime(logout=False), RUN_ID, graceful_timeout_s=0.4
        )
        self.assertTrue(waiting["graceful"])
        self.assertEqual(
            waiting["logout_players"],
            [{"player": "Ada", "logout_finished": False}],
        )
        self.assertGreater(float(waiting["logout_wait_s"]), 0.0)
        self.assertIn("player_state_not_saved", str(waiting.get("warnings")))

    async def test_named_rotation_invalidates_and_legacy_rotation_does_not(self) -> None:
        self._write(self.server_rpt, "boot\n" + TERMINATION)
        self._write(self.client_rpt, "boot\n" + TERMINATION)
        stale = await dayz_test_tool.execute_dayz_test_close(
            self._runtime(logout=False, append=False), RUN_ID, graceful_timeout_s=0.3
        )
        self.assertFalse(stale["graceful"])
        self.assertFalse(stale["exit_metrics_valid"])

        self._write(self.server_rpt, "boot\n")
        self._write(self.client_rpt, "boot\n")
        self._write(self.legacy_rpt, "legacy\n")
        clean = await dayz_test_tool.execute_dayz_test_close(
            self._runtime(logout=False), RUN_ID, graceful_timeout_s=2
        )
        self.assertTrue(clean["graceful"])
        self.assertFalse(any(item["rpt_rotated"] for item in clean["roles"]))

        self._write(self.server_rpt, "boot\n")
        self._write(self.client_rpt, "boot\n")
        rotated = await dayz_test_tool.execute_dayz_test_close(
            self._runtime(logout=False, rotate_named=True), RUN_ID, graceful_timeout_s=0.4
        )
        self.assertFalse(rotated["graceful"])
        self.assertEqual(rotated["reason"], "rpt_rotated")

    def test_client_diagnosis_uses_named_roots_only(self) -> None:
        named = self.root / "_client" / LEAF
        legacy = self.root / "_client" / "profiles"
        other = self.root / "_client" / OTHER
        server_leaf = self.root / "_server" / LEAF
        for path in (named, legacy, other, server_leaf):
            path.mkdir(parents=True, exist_ok=True)
        header = ("=" * 20 + "\n") * 3
        named_rpt = named / "client.rpt"
        named_rpt.write_text(header, encoding="utf-8")
        old = time.time() - 60
        os.utime(named_rpt, (old, old))
        (legacy / "client.rpt").write_text(header + "playing\n", encoding="utf-8")
        run = {
            "profiles": str(server_leaf),
            "processes": [{"role": "client", "pid": 1}],
        }
        roots = dayz_test_tool._client_profile_roots(self.policy, "all", run)
        self.assertEqual(roots, [str(named)])
        record = dayz_test_tool.ClientRecordProjection(
            present=True, alive=True, age_s=60.0, pid=1, valid=True
        )
        self.assertTrue(dayz_test_tool._client_start_stall_evidence(record, roots))
        self.assertIsNot(
            dayz_test_tool._client_start_stalled(
                [str(legacy)], time.time() - 120, time.time()
            ),
            True,
        )
        baseline = client_steam_bootstrap.snapshot_client_dumps(roots)
        (named / "ErrorMessage_a.mdmp").write_bytes(b"a")
        (legacy / "ErrorMessage_legacy.mdmp").write_bytes(b"l")
        (server_leaf / "ErrorMessage_server.mdmp").write_bytes(b"s")
        (other / "ErrorMessage_other.mdmp").write_bytes(b"o")
        found = client_steam_bootstrap._newest_new_dump(baseline)
        self.assertIsNotNone(found)
        self.assertEqual(found.parent, named)
        ceiling = client_steam_bootstrap.snapshot_client_dumps(roots)
        (named / "ErrorMessage_b.mdmp").write_bytes(b"b")
        self.assertEqual(
            client_steam_bootstrap._newest_new_dump(baseline, ceiling),
            found,
        )


class AttestationAndVppTest(unittest.TestCase):
    def test_named_initialization_accepts_only_fresh_named_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _roles(root)
            attestation = ProjectAttestation(
                version=1,
                artifacts=(),
                initialization=(
                    InitializationRequirement(
                        id="script",
                        role="client",
                        pattern="READY",
                        source="script",
                        filename=None,
                    ),
                    InitializationRequirement(
                        id="file",
                        role="server",
                        pattern="FILED",
                        source="profile_file",
                        filename="init.txt",
                    ),
                    InitializationRequirement(
                        id="off",
                        role="offline",
                        pattern="OFF",
                        source="script",
                        filename=None,
                    ),
                ),
                timeout_s=1.0,
            )
            named_client = Path(profile_directory(str(root), "client", TOKEN))
            named_server = Path(profile_directory(str(root), "server", TOKEN))
            legacy_client = root / "_client" / "profiles"
            named_client.mkdir(parents=True)
            named_server.mkdir(parents=True)
            legacy_client.mkdir(parents=True)
            (named_client / "script.log").write_text("old READY\n", encoding="utf-8")
            boundaries = {
                "client": dayz_test_attestation.capture_log_boundaries(str(named_client)),
                "server": dayz_test_attestation.capture_log_boundaries(
                    str(named_server), ("init.txt",)
                ),
                "offline": {},
            }
            _status, rows = initialization_rows(
                attestation,
                mode="all",
                dev_root=str(root),
                boundaries_by_role=boundaries,
                pending=False,
                token=TOKEN,
            )
            by_id = {row["id"]: row["status"] for row in rows}
            self.assertEqual(by_id["script"], "failed")
            with (named_client / "script.log").open("a", encoding="utf-8") as handle:
                handle.write("READY\n")
            (legacy_client / "script.log").write_text("READY\n", encoding="utf-8")
            (named_server / "init.txt").write_text("FILED\n", encoding="utf-8")
            status, rows = initialization_rows(
                attestation,
                mode="all",
                dev_root=str(root),
                boundaries_by_role=boundaries,
                pending=False,
                token=TOKEN,
            )
            by_id = {row["id"]: row["status"] for row in rows}
            self.assertEqual(by_id["script"], "passed")
            self.assertEqual(by_id["file"], "passed")
            self.assertEqual(by_id["off"], "not_applicable")
            document = report(
                attestation,
                artifact_status="passed",
                artifacts=[],
                initialization_status=status,
                initialization=rows,
            )
            self.assertEqual(document["status"], "passed")
            off_status, off_rows = initialization_rows(
                attestation,
                mode="offline",
                dev_root=str(root),
                boundaries_by_role={"offline": {}},
                pending=False,
                token=TOKEN,
            )
            self.assertEqual(off_rows[2]["status"], "failed")
            (root / "_client" / LEAF / "script.log").write_text(
                (root / "_client" / LEAF / "script.log").read_text(encoding="utf-8") + "OFF\n",
                encoding="utf-8",
            )
            off_status, off_rows = initialization_rows(
                attestation,
                mode="offline",
                dev_root=str(root),
                boundaries_by_role={"offline": {}},
                pending=False,
                token=TOKEN,
            )
            self.assertEqual(off_status, "passed")
            self.assertEqual(off_rows[2]["status"], "passed")
            empty = dayz_test_attestation.capture_log_boundaries(
                str(root / "_client" / "missing-leaf")
            )
            self.assertEqual(empty, {})

    def test_vpp_paths_follow_the_named_leaf_and_leave_server_cfg(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = _policy(root)
            mod = root / "@VPPAdminTools"
            mod.mkdir()
            (mod / "meta.cpp").write_text(
                "protocol = 1;\npublishedid = 1828439124;\nname = \"VPPAdminTools\";\n",
                encoding="utf-8",
            )
            server_root = root / "_server"
            server_root.mkdir()
            (server_root / "serverDZ.cfg").write_text(
                "vppDisablePassword = 1;\n", encoding="utf-8"
            )
            payload = {
                "mode": "server",
                "mod": "ExampleMod",
                "base_mods": [],
                "extra_mods": [str(mod)],
                "instance_token": TOKEN,
            }
            paths = native_launcher_transaction.vpp_preflight_paths(payload, policy)
            self.assertEqual(paths.server_profiles, str(root / "_server" / LEAF))
            self.assertEqual(paths.server_config, str(server_root / "serverDZ.cfg"))
            named_permissions = (
                root / "_server" / LEAF / "VPPAdminTools" / "Permissions"
            )
            (named_permissions / "SuperAdmins").mkdir(parents=True)
            (named_permissions / "SuperAdmins" / "SuperAdmins.txt").write_text(
                "76561198141021937\n", encoding="utf-8"
            )
            (named_permissions / "credentials.txt").write_text("seeded\n", encoding="utf-8")
            named = native_launcher_transaction.evaluate_vpp_preflight(payload, policy)
            self.assertNotIn("vpp_superadmins_absent", named.warnings)
            self.assertNotIn("vpp_credentials_absent", named.warnings)
            legacy = root / "_server" / "profiles" / "VPPAdminTools" / "Permissions"
            (legacy / "SuperAdmins").mkdir(parents=True)
            (legacy / "SuperAdmins" / "SuperAdmins.txt").write_text(
                "76561198141021937\n", encoding="utf-8"
            )
            (legacy / "credentials.txt").write_text("seeded\n", encoding="utf-8")
            for item in named_permissions.rglob("*"):
                if item.is_file():
                    item.unlink()
            legacy_only = native_launcher_transaction.evaluate_vpp_preflight(payload, policy)
            self.assertIn("vpp_superadmins_absent", legacy_only.warnings)
            self.assertIn("vpp_credentials_absent", legacy_only.warnings)
            self.assertEqual(legacy_only.missing, ())

    def test_foreign_label_and_launch_intent_keep_the_recorded_leaf(self) -> None:
        label = process_lifecycle._public_profiles_label(
            r"D:\proj\_server\profiles-130"
        )
        self.assertEqual(label, "profiles-130")
        self.assertNotIn(":\\", label)
        with tempfile.TemporaryDirectory() as temporary:
            profiles = Path(temporary) / LEAF
            profiles.mkdir()
            (profiles / "dayz_mcp.json").write_text(
                json.dumps({"instance": "22222222-2222-4222-8222-222222222222"}),
                encoding="utf-8",
            )
            found = process_lifecycle.ProcessLifecycle._launch_intent_instance(
                {"profiles": str(profiles)}
            )
            self.assertEqual(found, "22222222-2222-4222-8222-222222222222")


class Round2AnchorTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        reset_instance_context_for_tests()

    def test_traversal_in_the_recorded_anchor_is_rejected_before_normpath(self) -> None:
        bind_instance_context(TOKEN, replace=True)
        approved = Path(r"C:\approved")
        policy = _policy(approved)
        run = {
            "profiles": r"C:\approved\_server\..\_client\profiles-130",
            "mod": "@ExampleMod",
        }
        self.assertIsNone(dayz_test_tool._validated_recorded_leaf(policy, run))
        self.assertIsNone(
            dayz_test_tool._close_role_folder(
                policy, "client", {"client": "_client"}, run
            )
        )

    async def test_log_readers_reject_a_foreign_project_anchor(self) -> None:
        bind_instance_context(TOKEN, replace=True)
        with tempfile.TemporaryDirectory() as temporary:
            approved = Path(temporary) / "approved"
            foreign = Path(temporary) / "unapproved"
            approved.mkdir()
            _roles(approved)
            leaf = foreign / "_server" / LEAF
            leaf.mkdir(parents=True)
            script = leaf / "script.log"
            script.write_text("FOREIGN_READY\n", encoding="utf-8")
            run = {
                "run_id": "run-a",
                "state": "RUNNING",
                "mod": "@ExampleMod",
                "profiles": str(leaf),
                "processes": [
                    {
                        "role": "server",
                        "pid": 1,
                        "creation_time_utc": (
                            datetime.now(timezone.utc) - timedelta(seconds=5)
                        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    }
                ],
            }
            policy = _policy(approved)

            async def lifecycle_status():
                return {"runs": [run]}

            from tests.client_helpers import _fixture_client_runtime

            config = ServerConfig(mode="client", key="k", port=1, log_sink=lambda _m: None)
            holder = _fixture_client_runtime(config)
            holder.lifecycle_status = lifecycle_status
            with patch.object(server, "ClientRuntime", return_value=holder):
                app, _built = build_app(config)
            with patch.object(
                dayz_test_tool, "_close_project_policy", return_value=policy
            ):
                with self.assertRaises(ToolError) as raised:
                    await app.call_tool("logs_since", {"run_id": "run-a"})
            self.assertIn("bad_profiles", str(raised.exception))
            runtime = SimpleNamespace(
                tool_lock=asyncio.Lock(),
                lifecycle_status=lifecycle_status,
            )
            with patch.object(
                dayz_test_tool, "_close_project_policy", return_value=policy
            ):
                with self.assertRaises(ToolError) as waited:
                    await execute_wait_for(
                        runtime,
                        "log_matches",
                        pattern="FOREIGN_READY",
                        timeout_s=0.4,
                        poll_interval_s=0.2,
                        lookback_from="launch",
                    )
            self.assertIn("no_active_run", str(waited.exception))

    def test_extension_rejects_an_anchor_outside_the_approved_project(self) -> None:
        bind_instance_context(TOKEN, replace=True)
        approved = Path(r"C:\approved")
        policy = _policy(approved)
        run_id = "12345678-1234-4234-8234-1234567890ab"
        status = {
            "runs": [
                {
                    "run_id": run_id,
                    "state": "RUNNING_IDLE",
                    "mod": "@ExampleMod",
                    "profiles": r"C:\unapproved\_server\profiles-130",
                }
            ]
        }
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as raised:
            dayz_test_tool.require_extension_run(status, policy, run_id)
        self.assertEqual(raised.exception.code, "lifecycle_status_invalid")


class Round3FixesTest(unittest.IsolatedAsyncioTestCase):
    """Round 3: the recorded-anchor admission has no fail-open route left."""

    def tearDown(self) -> None:
        reset_instance_context_for_tests()

    @staticmethod
    def _loader_double(policy):
        def _match(run):
            # The sealed match rule: the row's mod names exactly one policy.
            return policy if run.get("mod") == "@ExampleMod" else None

        return _match

    async def test_unresolved_project_anchor_is_rejected_and_loader_consulted(self) -> None:
        """A row whose project cannot resolve never reaches the log readers.

        Fails on the round-2 tree: a row without ``mod`` skipped the sealed
        check, so the foreign folder was read and the loader was never called.
        """
        bind_instance_context(TOKEN, replace=True)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            approved = root / "approved"
            foreign = root / "unapproved"
            (approved / "_server" / LEAF).mkdir(parents=True)
            (approved / "_server" / LEAF / "script.log").write_text(
                "APPROVED_READY\n", encoding="utf-8"
            )
            foreign_leaf = foreign / "_server" / LEAF
            foreign_leaf.mkdir(parents=True)
            (foreign_leaf / "script.log").write_text(
                "FOREIGN_READY\n", encoding="utf-8"
            )
            stamp = (
                datetime.now(timezone.utc) - timedelta(seconds=5)
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            run = {
                "run_id": "R",
                "state": "RUNNING",
                "profiles": str(foreign_leaf),
                "processes": [
                    {"role": "server", "pid": 1, "creation_time_utc": stamp}
                ],
            }
            policy = _policy(approved)
            loader = Mock(side_effect=self._loader_double(policy))

            # Missing, empty, non-string, and unmatched mods resolve to no
            # sealed project, and the loader is consulted for each row.
            with patch.object(dayz_test_tool, "_close_project_policy", new=loader):
                for mod in (None, "", 7, "@Unknown"):
                    row = dict(run)
                    if mod is not None:
                        row["mod"] = mod
                    self.assertFalse(
                        launch_logs._recorded_anchor_in_sealed_project(row)
                    )
            self.assertEqual(loader.call_count, 4)

            from tests.client_helpers import _fixture_client_runtime

            config = ServerConfig(
                mode="client", key="k", port=1, log_sink=lambda _message: None
            )
            holder = _fixture_client_runtime(config)

            async def lifecycle_status():
                return {"runs": [dict(run)]}

            holder.lifecycle_status = lifecycle_status
            with patch.object(server, "ClientRuntime", return_value=holder):
                app, _built = build_app(config)
            runtime = SimpleNamespace(
                tool_lock=asyncio.Lock(),
                lifecycle_status=lifecycle_status,
            )
            with patch.object(dayz_test_tool, "_close_project_policy", new=loader):
                with self.assertRaises(ToolError) as raised:
                    await app.call_tool("logs_since", {"run_id": "R"})
                self.assertIn("bad_profiles", str(raised.exception))
                self.assertGreater(loader.call_count, 4)
                with self.assertRaises(ToolError) as waited:
                    await execute_wait_for(
                        runtime,
                        "log_matches",
                        pattern="FOREIGN_READY",
                        timeout_s=0.4,
                        poll_interval_s=0.2,
                        lookback_from="launch",
                    )
                self.assertIn("no_active_run", str(waited.exception))
                self.assertGreater(loader.call_count, 5)

            # Control: the same run with a resolvable project and an anchor
            # inside the approved dev_root is read from the named leaf.
            named = dict(run)
            named["mod"] = "@ExampleMod"
            named["profiles"] = str(approved / "_server" / LEAF)

            async def named_status():
                return {"runs": [dict(named)]}

            holder.lifecycle_status = named_status
            runtime.lifecycle_status = named_status
            with patch.object(dayz_test_tool, "_close_project_policy", new=loader):
                first = _content_json(await app.call_tool("logs_since", {"run_id": "R"}))
                names = {
                    Path(item["path"]).name: item["lines"] for item in first["files"]
                }
                self.assertEqual(names.get("script.log"), ["APPROVED_READY"])
                seen = await execute_wait_for(
                    runtime,
                    "log_matches",
                    pattern="APPROVED_READY",
                    timeout_s=1.0,
                    poll_interval_s=0.2,
                    lookback_lines=20,
                )
                self.assertTrue(seen["satisfied"])

    def test_extension_admission_takes_the_complete_recorded_anchor(self) -> None:
        """The repaired fixture shape: a dev_root policy and the default leaf.

        The round-2 extension check requires a recorded anchor; the fixtures it
        broke now carry one. This pins the admission the repaired shape relies
        on: complete data is admitted, a missing anchor and a foreign dev_root
        are still rejected with the unchanged code.
        """
        policy = _policy(Path(r"P:\ExampleMod_Suite"))
        run_id = "12345678-1234-4234-8234-1234567890ab"
        complete = {
            "run_id": run_id,
            "state": "RUNNING_IDLE",
            "mod": "@ExampleMod",
            "profiles": "P:\\ExampleMod_Suite\\_server\\profiles",
        }
        admitted = dayz_test_tool.require_extension_run(
            {"runs": [dict(complete)]}, policy, run_id
        )
        self.assertEqual(admitted["run_id"], run_id)

        unanchored = {key: value for key, value in complete.items() if key != "profiles"}
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as missing:
            dayz_test_tool.require_extension_run(
                {"runs": [unanchored]}, policy, run_id
            )
        self.assertEqual(missing.exception.code, "lifecycle_status_invalid")

        elsewhere = dayz_test_request.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\Somewhere_Else",
            default_source=r"P:\Somewhere_Else",
            default_base_mods=(),
            mission_roots=(r"P:\Somewhere_Else\_server\mpmissions",),
            mod_roots=(r"P:\Somewhere_Else",),
        )
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as foreign_root:
            dayz_test_tool.require_extension_run(
                {"runs": [dict(complete)]}, elsewhere, run_id
            )
        self.assertEqual(foreign_root.exception.code, "lifecycle_status_invalid")
