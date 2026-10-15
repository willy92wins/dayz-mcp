"""Host-policy extra arguments for the managed DayZDiag server and client.

server_extra_args and client_extra_args come only from the host launcher
policy: the builder validates them, worker-runtime.json carries them, and the
sealed worker appends them after the arguments it manages. No request field
and no dayz_test_run argument can name them.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import importlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp.exceptions import ToolError

from dayz_mcp import (
    dayz_test_readiness,
    dayz_test_request,
    dayz_test_tool,
    dayz_test_worker,
    native_broker_protocol,
    server,
)


def _policy(
    *,
    mod: str = "ExampleMod",
    dev_root: str = r"P:\ExampleMod_Suite",
    default_source: str = r"P:\ExampleMod",
    default_base_mods: tuple[str, ...] = ("@CF", "@Dabs Framework"),
) -> dayz_test_request.RequestProjectPolicy:
    return dayz_test_request.RequestProjectPolicy(
        mod=mod,
        dev_root=dev_root,
        default_source=default_source,
        default_base_mods=default_base_mods,
        mission_roots=(dev_root + r"\_server\mpmissions",),
        mod_roots=(r"P:\Mods",),
    )


def _sealed(*policies: dayz_test_request.RequestProjectPolicy) -> tuple[object, ...]:
    return tuple(types.SimpleNamespace(policy=policy) for policy in policies)


TOOLS_DIR = Path(__file__).resolve().parents[1]
EXAMPLE_POLICY = TOOLS_DIR / "launcher-policy.example.json"
POLICY = dayz_test_request.RequestProjectPolicy(
    mod="ExampleMod",
    dev_root=r"P:\ExampleMod_Suite",
    default_source=r"P:\ExampleMod",
    default_base_mods=("@CF",),
    mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
    mod_roots=(r"P:\Mods",),
)
RUNTIME = dayz_test_worker.WorkerRuntimePolicy(
    dev_root=POLICY.dev_root,
    mod="ExampleMod",
    diag_executable=r"C:\DayZ\DayZDiag_x64.exe",
    game_directory=r"C:\DayZ",
    mission_aliases=(
        ("chernarus", r"P:\ExampleMod_Suite\_server\mpmissions\dayzOffline.chernarusplus"),
        ("livonia", r"P:\ExampleMod_Suite\_server\mpmissions\dayzOffline.enoch"),
        ("sakhal", r"P:\ExampleMod_Suite\_server\mpmissions\dayzOffline.sakhal"),
    ),
    mods_root=r"P:\Mods",
    build_temp_root=r"P:\temp",
    build_source_basename=None,
)


def _raw(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "version": 1,
        "dev_root": POLICY.dev_root,
        "mod": POLICY.mod,
        "mode": "server",
    }
    value.update(overrides)
    return json.dumps(value).encode("utf-8")


class _Broker:
    def __init__(self) -> None:
        self.requests: list[native_broker_protocol.BrokerRequest] = []

    async def invoke(self, frame: bytes) -> dict[str, object]:
        request = native_broker_protocol.decode_request(frame)
        self.requests.append(request)
        command = request.payload.get("command")
        run_id = request.payload.get("run_id")
        if command == "stop":
            return {"ok": True, "state": "EXITED", "run_id": run_id}
        return {"ok": True, "state": "RUNNING", "run_id": run_id}


def _intent_template() -> dict[str, object]:
    document = json.loads(EXAMPLE_POLICY.read_text(encoding="utf-8"))
    for project in document["projects"]:
        project.pop("server_extra_args", None)
        project.pop("client_extra_args", None)
    return document


def _fixture_source(root: Path) -> dict[str, object]:
    document = _intent_template()
    project = document["projects"][0]
    missions = root / "dev" / "_server" / "mpmissions"
    for path in (root / "source", root / "mods", root / "temp"):
        path.mkdir(parents=True)
    for name in ("chernarus", "livonia", "sakhal"):
        (missions / name).mkdir(parents=True)
        project["mission_aliases"][name] = str(missions / name)
    project["default_source"]["path"] = str(root / "source")
    project["dev_root"]["path"] = str(root / "dev")
    project["mission_roots"] = [{"path": str(missions), "allow_root_junction": False}]
    project["mod_roots"] = [{"path": str(root / "mods"), "allow_root_junction": False}]
    project["mods_root"] = str(root / "mods")
    project["build_temp_root"] = str(root / "temp")
    return document


SERVER_ARGS = [
    "-doWeaponLog",
    "-freezecheck",
    "-newErrorsAreWarnings=1",
    "animdebugger=true",
]
CLIENT_ARGS = ["-doWeaponLog", "-newErrorsAreWarnings=1", "animdebugger=true"]

MANAGED = (
    "-server",
    "-config=C:\\x.cfg",
    "-profiles=C:\\p",
    "-mission=C:\\m",
    "-mod=@X",
    "-serverMod=@X",
    "-filePatching",
    "-startNavmeshDataServer",
    "-port=2402",
    "-port",
    "-connect=10.0.0.1",
    "-name=Other",
    "-window",
    "-x=640",
    "-y=480",
    "-noPause",
    "-storage=C:\\s",
    "-SERVER",
    "-MOD=@X",
    "-Profiles=C:\\p",
    "-nopause",
    "-FILEPATCHING",
    "-modded",
    "-windowed",
    "-serverProfile=C:\\p",
    "mod=@X",
    "port=2402",
    "--mod=@X",
)

MALFORMED = (
    "",
    "-",
    "=",
    "plain",
    "animdebugger",
    "animdebugger=",
    "-flag=",
    "-1flag",
    "-fl ag",
    "-flag=a b",
    "-flag=\"quoted\"",
    "\"-flag\"",
    "-flag\t",
    "-flag\n",
    "-flag\x00",
    "-flag\x7f",
    "-fl\u00e4g",
    "-flag=\u00e9",
    "-" + "a" * 65,
    "-flag=" + "a" * 251,
)


def _app_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "native-launchers"
        / "dayz-test-v1"
        / "src"
        / "app_main.py"
    )
    spec = importlib.util.spec_from_file_location("dayz_test_v1_app_extra_args", path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class ExtraArgsGrammarTest(unittest.TestCase):
    def test_accepts_dash_flags_dash_values_and_bare_key_values(self) -> None:
        self.assertTrue(dayz_test_worker.valid_extra_args(SERVER_ARGS))
        self.assertTrue(dayz_test_worker.valid_extra_args(tuple(CLIENT_ARGS)))
        self.assertTrue(dayz_test_worker.valid_extra_args([]))
        self.assertTrue(dayz_test_worker.valid_extra_args(["-cpuCount=4", "-BEpath=C:\\be"]))

    def test_rejects_every_managed_flag_in_any_case_and_prefix(self) -> None:
        for argument in MANAGED:
            with self.subTest(argument=argument):
                self.assertFalse(dayz_test_worker.valid_extra_args([argument]))

    def test_x_and_y_are_managed_only_as_whole_names(self) -> None:
        self.assertTrue(dayz_test_worker.valid_extra_args(["-xray", "-yield=1"]))
        self.assertFalse(dayz_test_worker.valid_extra_args(["-X=1"]))
        self.assertFalse(dayz_test_worker.valid_extra_args(["-Y"]))

    def test_rejects_malformed_tokens(self) -> None:
        for argument in MALFORMED:
            with self.subTest(argument=argument):
                self.assertFalse(dayz_test_worker.valid_extra_args([argument]))

    def test_rejects_non_strings_and_bad_containers(self) -> None:
        for value in (None, "-doWeaponLog", {"-a": 1}, [1], [None], [["-a"]], [True]):
            with self.subTest(value=value):
                self.assertFalse(dayz_test_worker.valid_extra_args(value))

    def test_count_length_and_duplicate_bounds(self) -> None:
        self.assertTrue(
            dayz_test_worker.valid_extra_args([f"-flag{index}" for index in range(32)])
        )
        self.assertFalse(
            dayz_test_worker.valid_extra_args([f"-flag{index}" for index in range(33)])
        )
        self.assertTrue(dayz_test_worker.valid_extra_args(["-flag=" + "a" * 250]))
        self.assertFalse(dayz_test_worker.valid_extra_args(["-a", "-A"]))
        self.assertFalse(dayz_test_worker.valid_extra_args(["-a=1", "a=2"]))


class BuilderPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = importlib.import_module("build_native_launcher")

    def _document(self, **fields: object) -> dict[str, object]:
        document = _intent_template()
        document["projects"][0].update(fields)
        return document

    def test_fields_are_optional_and_default_to_nothing(self) -> None:
        document = self._document()
        self.builder.validate_launcher_policy_source(document)
        runtime = self.builder.derive_worker_runtime(document)
        self.assertNotIn("server_extra_args", runtime["projects"][0])
        self.assertNotIn("client_extra_args", runtime["projects"][0])
        self.builder.validate_worker_runtime_document(runtime)

    def test_empty_lists_are_accepted_and_omitted_from_the_runtime(self) -> None:
        document = self._document(server_extra_args=[], client_extra_args=[])
        self.builder.validate_launcher_policy_source(document)
        runtime = self.builder.derive_worker_runtime(document)
        self.assertNotIn("server_extra_args", runtime["projects"][0])
        self.assertNotIn("client_extra_args", runtime["projects"][0])

    def test_good_lists_reach_the_worker_runtime_in_order(self) -> None:
        document = self._document(
            server_extra_args=list(SERVER_ARGS), client_extra_args=list(CLIENT_ARGS)
        )
        self.builder.validate_launcher_policy_source(document)
        runtime = self.builder.derive_worker_runtime(document)
        self.assertEqual(runtime["projects"][0]["server_extra_args"], SERVER_ARGS)
        self.assertEqual(runtime["projects"][0]["client_extra_args"], CLIENT_ARGS)
        self.builder.validate_worker_runtime_document(runtime)

    def test_extra_args_stay_out_of_the_request_policy(self) -> None:
        with TemporaryDirectory() as directory:
            source = _fixture_source(Path(directory))
            source["projects"][0]["server_extra_args"] = list(SERVER_ARGS)
            source["projects"][0]["client_extra_args"] = list(CLIENT_ARGS)
            policy_bytes, runtime_bytes = self.builder._emit_policy_documents(source)
        self.assertNotIn(b"extra_args", policy_bytes)
        runtime = json.loads(runtime_bytes)
        self.assertEqual(runtime["projects"][0]["server_extra_args"], SERVER_ARGS)

    def test_bad_values_are_schema_errors(self) -> None:
        bad_values = [
            None,
            "-doWeaponLog",
            {"-doWeaponLog": True},
            [1],
            [f"-flag{index}" for index in range(33)],
            ["-flag=" + "a" * 260],
            ["-flag\x01"],
            ["-flag\x00"],
            ["-fl\u00e4g"],
            [""],
            ["-a", "-a"],
            *([argument] for argument in MANAGED),
        ]
        for key in ("server_extra_args", "client_extra_args"):
            for value in bad_values:
                with self.subTest(key=key, value=value), self.assertRaisesRegex(
                    ValueError, "policy_source_schema"
                ):
                    self.builder.validate_launcher_policy_source(self._document(**{key: value}))

    def test_misspelled_key_is_still_a_schema_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "policy_source_schema"):
            self.builder.validate_launcher_policy_source(
                self._document(server_args=["-doWeaponLog"])
            )

    def test_runtime_document_rejects_empty_or_bad_lists(self) -> None:
        runtime = self.builder.derive_worker_runtime(
            self._document(server_extra_args=list(SERVER_ARGS))
        )
        for value in ([], ["-mod=@X"], "-doWeaponLog", [1]):
            for key in ("server_extra_args", "client_extra_args"):
                broken = json.loads(json.dumps(runtime))
                broken["projects"][0][key] = value
                with self.subTest(key=key, value=value), self.assertRaisesRegex(
                    ValueError, "worker_runtime_schema"
                ):
                    self.builder.validate_worker_runtime_document(broken)

    def test_published_example_declares_both_fields(self) -> None:
        source = self.builder.load_launcher_policy_source(EXAMPLE_POLICY)
        project = source["projects"][0]
        self.assertEqual(project["server_extra_args"], [])
        self.assertEqual(project["client_extra_args"], [])


class AppRuntimeLoaderTest(unittest.TestCase):
    def _load(self, project: dict[str, object]) -> dayz_test_worker.WorkerRuntimePolicy:
        app = _app_module()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "worker-runtime.json").write_text(
                json.dumps({"format_version": 1, "projects": [project]}), encoding="utf-8"
            )
            with patch.object(app, "_bundle_root", return_value=root):
                return app._worker_runtime(project["mod"], project["dev_root"])

    def _project(self, **fields: object) -> dict[str, object]:
        project = {
            "build_source_basename": None,
            "build_temp_root": RUNTIME.build_temp_root,
            "dev_root": RUNTIME.dev_root,
            "diag_executable": RUNTIME.diag_executable,
            "game_directory": RUNTIME.game_directory,
            "mission_aliases": dict(RUNTIME.mission_aliases),
            "mod": RUNTIME.mod,
            "mods_root": RUNTIME.mods_root,
        }
        project.update(fields)
        return project

    def test_absent_fields_load_as_empty(self) -> None:
        runtime = self._load(self._project())
        self.assertEqual(runtime.server_extra_args, ())
        self.assertEqual(runtime.client_extra_args, ())

    def test_present_fields_load_as_tuples_in_order(self) -> None:
        runtime = self._load(
            self._project(server_extra_args=SERVER_ARGS, client_extra_args=CLIENT_ARGS)
        )
        self.assertEqual(runtime.server_extra_args, tuple(SERVER_ARGS))
        self.assertEqual(runtime.client_extra_args, tuple(CLIENT_ARGS))

    def test_unknown_key_or_non_list_is_refused(self) -> None:
        for fields in (
            {"extra_args": ["-a"]},
            {"server_extra_args": "-a"},
            {"client_extra_args": {"-a": 1}},
        ):
            with self.subTest(fields=fields), self.assertRaisesRegex(
                RuntimeError, "worker_runtime_invalid"
            ):
                self._load(self._project(**fields))


class WorkerArgvTest(unittest.TestCase):
    RUNTIME = dataclasses.replace(
        RUNTIME,
        server_extra_args=tuple(SERVER_ARGS),
        client_extra_args=tuple(CLIENT_ARGS),
    )

    def _starts(self, runtime: dayz_test_worker.WorkerRuntimePolicy, **overrides: object) -> list[dict[str, object]]:
        parsed = dayz_test_request.parse_dayz_test_request(_raw(**overrides), policies=(POLICY,))
        ids = iter(
            (
                "12345678-1234-4234-8234-1234567890ab",
                "87654321-4321-4321-8321-ba0987654321",
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            )
        )

        async def readiness(_run_id: str, _port: int, _timeout: int) -> object:
            return dayz_test_readiness.ReadinessResult(ready=True, error_code=None)

        broker = _Broker()
        asyncio.run(
            dayz_test_worker.execute_dayz_test_worker(
                parsed.canonical_bytes,
                request_sha256=parsed.sha256,
                request_policies=(POLICY,),
                runtime_policy=runtime,
                broker=broker,
                id_fn=lambda: next(ids),
                readiness_probe=readiness,
                has_binarizable_assets=lambda _source: True,
            )
        )
        return [
            json.loads(item.stdin)
            for item in broker.requests
            if item.payload.get("command") == "start"
        ]

    def test_server_and_client_get_their_own_args_after_the_managed_ones(self) -> None:
        plain_server, plain_client = self._starts(RUNTIME, mode="all", server_mods=["@Srv"])
        server_start, client_start = self._starts(self.RUNTIME, mode="all", server_mods=["@Srv"])
        self.assertEqual(server_start["role"], "server")
        self.assertEqual(client_start["role"], "client")
        self.assertEqual(server_start["argv"], plain_server["argv"] + SERVER_ARGS)
        self.assertEqual(client_start["argv"], plain_client["argv"] + CLIENT_ARGS)
        self.assertTrue(plain_server["argv"][-1].startswith("-serverMod="))
        self.assertEqual(plain_client["argv"][-1], "-filePatching")

    def test_the_launch_hash_covers_the_extra_args(self) -> None:
        server_start, _client = self._starts(self.RUNTIME, mode="all")
        core = dict(server_start)
        supplied = core.pop("launch_request_sha256")
        self.assertEqual(
            supplied,
            hashlib.sha256(
                json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(core["argv"][-len(SERVER_ARGS):], SERVER_ARGS)

    def test_offline_gets_neither_list(self) -> None:
        (offline,) = self._starts(self.RUNTIME, mode="offline")
        (plain,) = self._starts(RUNTIME, mode="offline")
        self.assertEqual(offline["argv"], plain["argv"])

    def test_empty_lists_leave_argv_unchanged(self) -> None:
        empty = dataclasses.replace(RUNTIME, server_extra_args=(), client_extra_args=())
        self.assertEqual(self._starts(empty, mode="all"), self._starts(RUNTIME, mode="all"))

    def test_invalid_runtime_lists_fail_closed_before_any_launch(self) -> None:
        for fields in (
            {"server_extra_args": ("-mod=@Evil",)},
            {"client_extra_args": ("-server",)},
            {"client_extra_args": ("-connect=10.0.0.1",)},
            {"server_extra_args": ("-flag\x00",)},
            {"server_extra_args": ["-doWeaponLog"]},
            {"client_extra_args": ("-a",) * 2},
        ):
            runtime = dataclasses.replace(RUNTIME, **fields)
            parsed = dayz_test_request.parse_dayz_test_request(
                _raw(mode="all"), policies=(POLICY,)
            )
            broker = _Broker()
            with self.subTest(fields=fields), self.assertRaises(
                dayz_test_worker.DayzTestWorkerError
            ) as raised:
                asyncio.run(
                    dayz_test_worker.execute_dayz_test_worker(
                        parsed.canonical_bytes,
                        request_sha256=parsed.sha256,
                        request_policies=(POLICY,),
                        runtime_policy=runtime,
                        broker=broker,
                    )
                )
            self.assertEqual(raised.exception.code, "runtime_policy_invalid")
            self.assertEqual(broker.requests, [])


class RequestCannotCarryExtraArgsTest(unittest.TestCase):
    def test_request_v1_rejects_the_fields_as_unknown_keys(self) -> None:
        dayz_test_request.parse_dayz_test_request(_raw(), policies=(POLICY,))
        for key, value in (
            ("server_extra_args", ["-doWeaponLog"]),
            ("client_extra_args", ["-doWeaponLog"]),
            ("server_extra_args", []),
            ("extra_args", ["-doWeaponLog"]),
        ):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(
                ValueError, "invalid_dayz_test_request"
            ):
                dayz_test_request.parse_dayz_test_request(
                    _raw(**{key: value}), policies=(POLICY,)
                )

    def test_parsed_payload_never_names_them(self) -> None:
        parsed = dayz_test_request.parse_dayz_test_request(_raw(mode="all"), policies=(POLICY,))
        self.assertFalse(any("extra_args" in key for key in parsed.payload))

    def test_tool_request_builder_has_no_such_parameter(self) -> None:
        for key in ("server_extra_args", "client_extra_args"):
            with self.subTest(key=key), self.assertRaises(TypeError):
                dayz_test_tool.build_run_request(
                    _sealed(_policy()), project="ExampleMod", mode="server", **{key: ["-doWeaponLog"]}
                )


class PublicToolCannotCarryExtraArgsTest(unittest.IsolatedAsyncioTestCase):
    async def test_schema_has_no_extra_args_and_a_call_naming_them_never_runs(self) -> None:
        app, _runtime = server.build_app(server.ServerConfig(log_sink=lambda _message: None))
        tool = app._tool_manager.get_tool("dayz_test_run")
        self.assertFalse(any("extra_args" in name for name in tool.parameters["properties"]))
        with patch.object(dayz_test_tool, "execute_dayz_test_run", new=AsyncMock()) as execute:
            for key in ("server_extra_args", "client_extra_args"):
                with self.subTest(key=key), self.assertRaises(ToolError):
                    await app.call_tool("dayz_test_run", {
                        "project": "ExampleMod", "mode": "server", key: ["-doWeaponLog"],
                    })
            execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
