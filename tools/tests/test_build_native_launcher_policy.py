from __future__ import annotations

import importlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dayz_mcp import (
    dayz_test_request,
    dayz_test_tool,
    native_bundle,
    request_path_authority,
)


TOOLS_DIR = Path(__file__).resolve().parents[1]
EXAMPLE_POLICY = TOOLS_DIR / "launcher-policy.example.json"


def make_fixture_source(root: Path, *, mods: tuple[str, ...] = ("ExampleMod",)) -> dict[str, object]:
    root = Path(root)
    source_dir = root / "source"
    dev_dir = root / "dev"
    missions = dev_dir / "_server" / "mpmissions"
    mods_dir = root / "mods"
    temp_dir = root / "temp"
    for path in (
        source_dir,
        missions / "dayzOffline.chernarusplus",
        missions / "dayzOffline.enoch",
        missions / "dayzOffline.sakhal",
        mods_dir,
        temp_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    def located(*parts: str) -> str:
        return str(root.joinpath(*parts))

    projects: list[dict[str, object]] = []
    for mod in mods:
        projects.append(
            {
                "mod": mod,
                "default_base_mods": ["@ExampleCore"],
                "default_source": {
                    "path": located("source"),
                    "allow_root_junction": False,
                },
                "dev_root": {
                    "path": located("dev"),
                    "allow_root_junction": False,
                },
                "mission_roots": [
                    {
                        "path": located("dev", "_server", "mpmissions"),
                        "allow_root_junction": False,
                    }
                ],
                "mod_roots": [
                    {
                        "path": located("mods"),
                        "allow_root_junction": False,
                    }
                ],
                "build_temp_root": located("temp"),
                "build_source_basename": None,
                "diag_executable": r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
                "game_directory": r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
                "mission_aliases": {
                    "chernarus": located(
                        "dev", "_server", "mpmissions", "dayzOffline.chernarusplus"
                    ),
                    "livonia": located(
                        "dev", "_server", "mpmissions", "dayzOffline.enoch"
                    ),
                    "sakhal": located(
                        "dev", "_server", "mpmissions", "dayzOffline.sakhal"
                    ),
                },
                "mods_root": located("mods"),
            }
        )
    return {"format_version": 1, "projects": projects}


def _intent_template() -> dict[str, object]:
    return {
        "format_version": 1,
        "projects": [
            {
                "mod": "ExampleMod",
                "default_base_mods": ["@ExampleCore"],
                "default_source": {
                    "path": r"C:\DayZ\ExampleMod\source",
                    "allow_root_junction": False,
                },
                "dev_root": {
                    "path": r"C:\DayZ\ExampleMod\dev",
                    "allow_root_junction": False,
                },
                "mission_roots": [
                    {
                        "path": r"C:\DayZ\ExampleMod\dev\_server\mpmissions",
                        "allow_root_junction": False,
                    }
                ],
                "mod_roots": [
                    {
                        "path": r"C:\DayZ\ExampleMod\mods",
                        "allow_root_junction": False,
                    }
                ],
                "build_temp_root": r"C:\DayZ\ExampleMod\temp",
                "build_source_basename": None,
                "diag_executable": r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
                "game_directory": r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
                "mission_aliases": {
                    "chernarus": r"C:\DayZ\ExampleMod\dev\_server\mpmissions\dayzOffline.chernarusplus",
                    "livonia": r"C:\DayZ\ExampleMod\dev\_server\mpmissions\dayzOffline.enoch",
                    "sakhal": r"C:\DayZ\ExampleMod\dev\_server\mpmissions\dayzOffline.sakhal",
                },
                "mods_root": r"C:\DayZ\ExampleMod\mods",
            }
        ],
    }


class LauncherPolicySourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = importlib.import_module("build_native_launcher")

    def test_valid_intent_document_is_accepted(self) -> None:
        self.builder.validate_launcher_policy_source(_intent_template())

    def test_extra_document_key_is_schema_error(self) -> None:
        document = _intent_template()
        document["identity"] = "nope"
        with self.assertRaisesRegex(ValueError, "policy_source_schema"):
            self.builder.validate_launcher_policy_source(document)

    def test_format_version_not_one_is_schema_error(self) -> None:
        document = _intent_template()
        document["format_version"] = 2
        with self.assertRaisesRegex(ValueError, "policy_source_schema"):
            self.builder.validate_launcher_policy_source(document)

    def test_root_intent_with_identity_is_schema_error(self) -> None:
        document = _intent_template()
        document["projects"][0]["dev_root"]["identity"] = {
            "file_id": "0" * 32,
            "volume_serial_number": 1,
        }
        with self.assertRaisesRegex(ValueError, "policy_source_schema"):
            self.builder.validate_launcher_policy_source(document)

    def test_cli_wins_over_env(self) -> None:
        with patch.dict(os.environ, {self.builder._POLICY_ENV: r"C:\from-env.json"}):
            selected = self.builder.resolve_launcher_policy_path(r"C:\from-cli.json")
        self.assertEqual(selected, Path(r"C:\from-cli.json"))

    def test_explicit_path_does_not_read_env(self) -> None:
        with patch.dict(os.environ, {self.builder._POLICY_ENV: r"C:\must-not-read.json"}):
            selected = self.builder.resolve_launcher_policy_path(Path(r"C:\explicit.json"))
        self.assertEqual(selected, Path(r"C:\explicit.json"))

    def test_empty_env_does_not_fall_through_to_default(self) -> None:
        with patch.dict(os.environ, {self.builder._POLICY_ENV: "   "}):
            with self.assertRaisesRegex(ValueError, "policy_source_unselected"):
                self.builder.resolve_launcher_policy_path()

    def test_missing_localappdata_is_unavailable(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "local_cache_unavailable"):
                self.builder.resolve_launcher_policy_path()

    def test_missing_default_file_is_policy_source_missing(self) -> None:
        with TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}, clear=True):
                selected = self.builder.resolve_launcher_policy_path()
                self.assertEqual(
                    selected,
                    Path(directory) / "DayZ_MCP" / "launcher-policy.json",
                )
                with self.assertRaisesRegex(ValueError, "policy_source_missing"):
                    self.builder.load_launcher_policy_source(selected)

    def test_symlink_host_file_is_not_regular(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "policy.json"
            real.write_bytes(self.builder.canonical_json_bytes(_intent_template()))
            link = root / "policy.link.json"
            try:
                os.symlink(real, link)
                target = link
                patcher = None
            except OSError:
                target = real
                patcher = patch.object(Path, "is_symlink", return_value=True)
                patcher.start()
            try:
                with self.assertRaisesRegex(ValueError, "policy_source_not_regular"):
                    self.builder.load_launcher_policy_source(target)
            finally:
                if patcher is not None:
                    patcher.stop()

    def test_cli_defines_policy_flag(self) -> None:
        source = Path(self.builder.__file__).read_text(encoding="utf-8")
        self.assertIn('add_argument("--policy"', source)
        self.assertIn("load_launcher_policy_source(resolve_launcher_policy_path(policy))", source)

    def test_mission_alias_outside_roots_is_schema_error(self) -> None:
        document = _intent_template()
        document["projects"][0]["mission_aliases"]["chernarus"] = r"C:\Windows"
        with self.assertRaisesRegex(ValueError, "policy_source_schema"):
            self.builder.validate_launcher_policy_source(document)

    def test_build_source_basename_accepts_null_and_a_plain_name(self) -> None:
        document = _intent_template()
        self.builder.validate_launcher_policy_source(document)
        document["projects"][0]["build_source_basename"] = "build_src"
        self.builder.validate_launcher_policy_source(document)
        runtime = self.builder.derive_worker_runtime(document)
        self.assertEqual(
            runtime["projects"][0]["build_source_basename"], "build_src"
        )
        self.builder.validate_worker_runtime_document(runtime)

    def test_build_source_basename_rejects_paths_and_non_strings(self) -> None:
        for invalid in ("", "build\\src", "build/src", "..", ".hidden", "a" * 65, 7, True):
            document = _intent_template()
            document["projects"][0]["build_source_basename"] = invalid
            with self.subTest(value=invalid), self.assertRaisesRegex(
                ValueError, "policy_source_schema"
            ):
                self.builder.validate_launcher_policy_source(document)

    def test_mods_root_must_repeat_a_mod_root(self) -> None:
        document = _intent_template()
        document["projects"][0]["mods_root"] = r"C:\DayZ\ExampleMod\other-mods"
        with self.assertRaisesRegex(ValueError, "policy_source_schema"):
            self.builder.validate_launcher_policy_source(document)

    def test_example_validates_schema_and_does_not_seal(self) -> None:
        source = self.builder.load_launcher_policy_source(EXAMPLE_POLICY)
        self.assertEqual(source["projects"][0]["mod"], "ExampleMod")
        with self.assertRaisesRegex(ValueError, "identity_open_failed:"):
            self.builder.seal_request_policy(source)

    def test_example_is_not_a_resolution_fallback(self) -> None:
        with TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}, clear=True):
                selected = self.builder.resolve_launcher_policy_path()
                self.assertNotEqual(selected.resolve(), EXAMPLE_POLICY.resolve())
                with self.assertRaisesRegex(ValueError, "policy_source_missing"):
                    self.builder.load_launcher_policy_source(selected)


class SealedRootTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = importlib.import_module("build_native_launcher")

    def test_regular_directory(self) -> None:
        with TemporaryDirectory() as directory:
            sealed = self.builder._sealed_root(directory, allow_root_junction=False)
        self.assertEqual(sealed["path"], directory)
        self.assertFalse(sealed["allow_root_junction"])
        self.assertEqual(sealed["root_reparse_tag"], 0)

    def test_missing_path_is_identity_open_failed(self) -> None:
        with self.assertRaisesRegex(ValueError, "identity_open_failed:"):
            self.builder._sealed_root(
                r"C:\DayZ\ExampleMod\does-not-exist",
                allow_root_junction=False,
            )

    def test_required_junction_missing_on_regular_directory(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "required_junction_missing:"):
                self.builder._sealed_root(directory, allow_root_junction=True)

    def test_host_p_mods_junction(self) -> None:
        junction = Path(r"P:\Mods")
        if not junction.exists() or not junction.is_junction():
            self.skipTest(r"P:\Mods exact junction is unavailable")
        with self.assertRaisesRegex(ValueError, "unexpected_root_reparse:"):
            self.builder._sealed_root(r"P:\Mods", allow_root_junction=False)
        sealed = self.builder._sealed_root(r"P:\Mods", allow_root_junction=True)
        self.assertEqual(sealed["root_reparse_tag"], 0xA0000003)
        self.assertTrue(sealed["allow_root_junction"])


class FixturePolicyBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = importlib.import_module("build_native_launcher")

    def test_unlisted_project_remains_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            source = make_fixture_source(Path(directory), mods=("ExampleMod", "OtherMod"))
            sealed = native_bundle._parse_policy(self.builder.seal_request_policy(source))
        with self.assertRaisesRegex(dayz_test_tool.DayzTestToolError, "bad_project"):
            dayz_test_tool.build_run_request(
                sealed,
                project="NotListed",
                mode="offline",
                preflight=True,
            )

    def test_listed_project_parses_and_mission_escape_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_fixture_source(root, mods=("ExampleMod",))
            sealed_document = self.builder.seal_request_policy(source)
            sealed = native_bundle._parse_policy(sealed_document)
            policies = tuple(item.policy for item in sealed)
            project = source["projects"][0]
            mission = str(
                Path(project["mission_roots"][0]["path"]) / "dayzOffline.chernarusplus"
            )
            request = {
                "version": 1,
                "dev_root": project["dev_root"]["path"],
                "mod": "ExampleMod",
                "mode": "server",
                "mission": mission,
                "source": project["default_source"]["path"],
                "base_mods": ["@ExampleCore"],
                "build": True,
            }
            raw = json.dumps(request, separators=(",", ":"), sort_keys=True).encode("utf-8")
            parsed = dayz_test_request.parse_dayz_test_request(raw, policies=policies)
            self.assertEqual(parsed.payload["mod"], "ExampleMod")

            request["mission"] = r"C:\Windows"
            escaped = json.dumps(request, separators=(",", ":"), sort_keys=True).encode("utf-8")
            with self.assertRaisesRegex(ValueError, "invalid_dayz_test_request"):
                dayz_test_request.parse_dayz_test_request(escaped, policies=policies)

    def test_relative_core_mod_parses(self) -> None:
        with TemporaryDirectory() as directory:
            source = make_fixture_source(Path(directory), mods=("ExampleMod",))
            sealed = native_bundle._parse_policy(self.builder.seal_request_policy(source))
            policies = tuple(item.policy for item in sealed)
            request = {
                "version": 1,
                "dev_root": source["projects"][0]["dev_root"]["path"],
                "mod": "ExampleMod",
                "mode": "server",
                "mission": "chernarus",
                "base_mods": ["@ExampleCore"],
                "build": False,
                "preflight": True,
            }
            raw = json.dumps(request, separators=(",", ":"), sort_keys=True).encode("utf-8")
            parsed = dayz_test_request.parse_dayz_test_request(raw, policies=policies)
        self.assertEqual(parsed.payload["mod"], "ExampleMod")
        self.assertEqual(parsed.payload["base_mods"], ["@ExampleCore"])

    def test_fixture_accredits_live_request_paths(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_fixture_source(root, mods=("ExampleMod",))
            source["projects"][0]["default_base_mods"] = []
            sealed = native_bundle._parse_policy(self.builder.seal_request_policy(source))
            policies = tuple(item.policy for item in sealed)
            project = source["projects"][0]
            request = {
                "version": 1,
                "dev_root": project["dev_root"]["path"],
                "mod": "ExampleMod",
                "mode": "server",
                "mission": project["mission_roots"][0]["path"],
                "source": project["default_source"]["path"],
                "build": True,
                "preflight": True,
            }
            raw = json.dumps(request, separators=(",", ":"), sort_keys=True).encode("utf-8")
            parsed = dayz_test_request.parse_dayz_test_request(raw, policies=policies)
            with request_path_authority.accredit_request_paths(
                parsed,
                policies=sealed,
            ) as accredited:
                self.assertGreater(accredited.handle_count, 0)
                self.assertEqual(len(accredited.identities["mission"]), 1)

    def test_policy_flag_emits_examplemod_not_author_census(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_fixture_source(root, mods=("ExampleMod", "OtherMod"))
            host = root / "launcher-policy.json"
            host.write_bytes(self.builder.canonical_json_bytes(source))
            loaded = self.builder.load_launcher_policy_source(
                self.builder.resolve_launcher_policy_path(host)
            )
            policy_bytes, runtime_bytes = self.builder._emit_policy_documents(loaded)
        sealed = json.loads(policy_bytes)
        runtime = json.loads(runtime_bytes)
        self.assertEqual(
            [project["mod"] for project in sealed["projects"]],
            ["ExampleMod", "OtherMod"],
        )
        self.assertEqual(
            [project["mod"] for project in runtime["projects"]],
            ["ExampleMod", "OtherMod"],
        )
        self.assertLessEqual(len(policy_bytes), self.builder._MAX_POLICY_BYTES)
        self.assertLessEqual(len(runtime_bytes), self.builder._MAX_POLICY_BYTES)
        self.builder.validate_request_policy_document(sealed)
        self.builder.validate_worker_runtime_document(runtime)

    def test_builder_stops_without_host_file(self) -> None:
        with TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}, clear=True):
                selected = self.builder.resolve_launcher_policy_path()
                with self.assertRaisesRegex(ValueError, "policy_source_missing"):
                    self.builder.load_launcher_policy_source(selected)


if __name__ == "__main__":
    unittest.main()
