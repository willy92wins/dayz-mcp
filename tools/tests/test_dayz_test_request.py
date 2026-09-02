from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import unittest
from dataclasses import replace


class DayzTestRequestTests(unittest.TestCase):
    def test_request_uses_current_mode_authority_for_each_parse(self) -> None:
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        modes_module = importlib.import_module("dayz_mcp.dayz_test_modes")
        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF",),
            mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
            mod_roots=(r"P:\Mods",),
        )
        raw = json.dumps(
            {"version": 1, "dev_root": r"P:\ExampleMod_Suite", "mod": "ExampleMod"}
        ).encode("utf-8")
        original_records = modes_module.MODE_RECORDS
        changed_records = tuple(
            replace(
                record,
                default_when_omitted=(record.name == "server"),
                request_visible=(False if record.name == "client" else record.request_visible),
            )
            for record in original_records
        )
        no_default_records = tuple(
            replace(record, default_when_omitted=False) for record in original_records
        )
        multiple_default_records = tuple(
            replace(
                record,
                default_when_omitted=(record.name in {"all", "server"}),
            )
            for record in original_records
        )
        client_request = json.dumps(
            {
                "version": 1,
                "dev_root": r"P:\ExampleMod_Suite",
                "mod": "ExampleMod",
                "mode": "client",
                "run_id": "12345678-1234-4234-8234-1234567890ab",
            }
        ).encode("utf-8")

        self.assertEqual(
            request_module.parse_dayz_test_request(raw, policies=(policy,)).payload["mode"],
            "all",
        )
        modes_module.MODE_RECORDS = changed_records
        try:
            self.assertEqual(
                request_module.parse_dayz_test_request(raw, policies=(policy,)).payload["mode"],
                "server",
            )
            with self.assertRaisesRegex(ValueError, "invalid_dayz_test_request"):
                request_module.parse_dayz_test_request(client_request, policies=(policy,))
            modes_module.MODE_RECORDS = no_default_records
            with self.assertRaisesRegex(ValueError, "invalid_dayz_test_request"):
                request_module.parse_dayz_test_request(raw, policies=(policy,))
            modes_module.MODE_RECORDS = multiple_default_records
            with self.assertRaisesRegex(ValueError, "invalid_dayz_test_request"):
                request_module.parse_dayz_test_request(raw, policies=(policy,))
        finally:
            modes_module.MODE_RECORDS = original_records

    def test_request_emits_closed_run_id_tokens_before_generic_validation(self) -> None:
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF",),
            mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
            mod_roots=(r"P:\Mods",),
        )
        base = {"version": 1, "dev_root": r"P:\ExampleMod_Suite", "mod": "ExampleMod"}
        valid_uuid = "12345678-1234-4234-8234-1234567890ab"
        cases = {
            "invalid_uuid_wins": ({"mode": "client", "run_id": "not-a-uuid"}, "invalid_run_id"),
            "server_invalid_uuid_wins": (
                {"mode": "server", "run_id": "not-a-uuid"},
                "invalid_run_id",
            ),
            "all_invalid_uuid_wins": (
                {"mode": "all", "run_id": "not-a-uuid"},
                "invalid_run_id",
            ),
            "client_without_id": ({"mode": "client"}, "client_requires_run_id"),
            "server_with_id": ({"mode": "server", "run_id": valid_uuid}, "server_all_forbid_run_id"),
            "all_with_id": ({"mode": "all", "run_id": valid_uuid}, "server_all_forbid_run_id"),
        }

        for preflight in (False, True):
            for label, (overrides, expected) in cases.items():
                document = {**base, **overrides, "preflight": preflight}
                with self.subTest(preflight=preflight, label=label):
                    with self.assertRaisesRegex(ValueError, f"^{expected}$"):
                        request_module.parse_dayz_test_request(
                            json.dumps(document).encode("utf-8"), policies=(policy,)
                        )

        for mode, run_id in (("server", None), ("all", None), ("client", valid_uuid)):
            document = {**base, "mode": mode, "run_id": run_id}
            with self.subTest(control_mode=mode):
                parsed = request_module.parse_dayz_test_request(
                    json.dumps(document).encode("utf-8"), policies=(policy,)
                )
                self.assertEqual(parsed.payload["mode"], mode)
                self.assertEqual(parsed.payload["run_id"], run_id)

        stop_document = {
            **base,
            "kill": True,
            "mode": "offline",
            "run_id": valid_uuid,
        }
        stop = request_module.parse_dayz_test_request(
            json.dumps(stop_document).encode("utf-8"), policies=(policy,)
        )
        self.assertIs(stop.payload["kill"], True)
        self.assertEqual(stop.payload["mode"], "offline")
        self.assertEqual(stop.payload["run_id"], valid_uuid)

        invalid_stops = {
            "kill_preflight": {
                "kill": True,
                "mode": "offline",
                "preflight": True,
                "run_id": valid_uuid,
            },
            "kill_client": {
                "kill": True,
                "mode": "client",
                "run_id": valid_uuid,
            },
            "kill_server": {
                "kill": True,
                "mode": "server",
                "run_id": valid_uuid,
            },
            "kill_all": {
                "kill": True,
                "mode": "all",
                "run_id": valid_uuid,
            },
        }
        for label, overrides in invalid_stops.items():
            with self.subTest(invalid_stop=label):
                with self.assertRaisesRegex(
                    ValueError, "^invalid_dayz_test_request$"
                ):
                    request_module.parse_dayz_test_request(
                        json.dumps({**base, **overrides}).encode("utf-8"),
                        policies=(policy,),
                    )

    def test_request_accepts_only_the_four_frozen_mission_aliases(self) -> None:
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF",),
            mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
            mod_roots=(r"P:\Mods",),
        )
        base = {"version": 1, "dev_root": r"P:\ExampleMod_Suite", "mod": "ExampleMod"}

        for mission in ("sakhal", "lfheli"):
            with self.subTest(mission=mission):
                parsed = request_module.parse_dayz_test_request(
                    json.dumps({**base, "mission": mission}).encode("utf-8"),
                    policies=(policy,),
                )
                self.assertEqual(parsed.payload["mission"], mission)

        with self.assertRaisesRegex(ValueError, "^invalid_dayz_test_request$"):
            request_module.parse_dayz_test_request(
                json.dumps({**base, "mission": "namalsk"}).encode("utf-8"),
                policies=(policy,),
            )

    def test_minimal_request_applies_policy_defaults_and_emits_canonical_bytes(
        self,
    ) -> None:
        spec = importlib.util.find_spec("dayz_mcp.dayz_test_request")
        self.assertIsNotNone(spec, "dayz_mcp.dayz_test_request is not implemented")
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")

        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF", "@Dabs Framework", "@VPPAdminTools"),
            mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
            mod_roots=(r"P:\Mods",),
        )
        raw = json.dumps(
            {
                "mod": "ExampleMod",
                "dev_root": r"P:\ExampleMod_Suite",
                "version": 1,
            },
            indent=2,
        ).encode("utf-8")

        parsed = request_module.parse_dayz_test_request(raw, policies=(policy,))
        expected_payload = {
            "base_mods": ["@CF", "@Dabs Framework", "@VPPAdminTools"],
            "build": False,
            "clean": False,
            "dev_root": r"P:\ExampleMod_Suite",
            "extra_mods": [],
            "height": 1080,
            "kill": False,
            "mission": "chernarus",
            "mod": "ExampleMod",
            "mode": "all",
            "no_base_mods": False,
            "no_file_patching": False,
            "pack_only": False,
            "player_name": "Dev",
            "port": 2302,
            "preflight": False,
            "run_id": None,
            "server_mods": [],
            "server_wait_s": 60,
            "source": r"P:\ExampleMod",
            "version": 1,
            "width": 1920,
        }
        expected_bytes = json.dumps(
            expected_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

        self.assertEqual(parsed.payload, expected_payload)
        self.assertEqual(parsed.canonical_bytes, expected_bytes)
        self.assertEqual(parsed.sha256, hashlib.sha256(expected_bytes).hexdigest())

        reparsed = request_module.parse_dayz_test_request(
            parsed.canonical_bytes, policies=(policy,)
        )
        self.assertEqual(reparsed, parsed)

    def test_request_rejects_non_closed_json_surface(self) -> None:
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF", "@Dabs Framework", "@VPPAdminTools"),
            mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
            mod_roots=(r"P:\Mods",),
        )
        invalid_documents = {
            "duplicate_key": (
                b'{"version":2,"version":1,"dev_root":"P:\\\\ExampleMod_Suite",'
                b'"mod":"ExampleMod"}'
            ),
            "extra_key": json.dumps(
                {
                    "version": 1,
                    "dev_root": r"P:\ExampleMod_Suite",
                    "mod": "ExampleMod",
                    "argv": ["unexpected"],
                }
            ).encode("utf-8"),
            "nan": (
                b'{"version":1,"dev_root":"P:\\\\ExampleMod_Suite",'
                b'"mod":"ExampleMod","port":NaN}'
            ),
        }

        for label, raw in invalid_documents.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "invalid_dayz_test_request"):
                    request_module.parse_dayz_test_request(raw, policies=(policy,))

    def test_explicit_typed_overrides_are_preserved_and_canonicalized(self) -> None:
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF", "@Dabs Framework", "@VPPAdminTools"),
            mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
            mod_roots=(r"P:\Mods",),
        )
        raw = json.dumps(
            {
                "version": 1,
                "dev_root": r"P:\ExampleMod_Suite",
                "mod": "ExampleMod",
                "mode": "offline",
                "mission": "livonia",
                "base_mods": [],
                "no_base_mods": True,
                "port": 2402,
                "width": 2560,
                "height": 1440,
                "player_name": "Utopia Dev",
                "server_wait_s": 90,
                "clean": True,
                "pack_only": True,
                "no_file_patching": True,
                "run_id": "12345678-1234-4234-8234-1234567890ab",
            }
        ).encode("utf-8")

        parsed = request_module.parse_dayz_test_request(raw, policies=(policy,))

        self.assertEqual(parsed.payload["mode"], "offline")
        self.assertEqual(parsed.payload["mission"], "livonia")
        self.assertEqual(parsed.payload["base_mods"], [])
        self.assertIs(parsed.payload["no_base_mods"], True)
        self.assertEqual(parsed.payload["port"], 2402)
        self.assertEqual(parsed.payload["width"], 2560)
        self.assertEqual(parsed.payload["height"], 1440)
        self.assertEqual(parsed.payload["player_name"], "Utopia Dev")
        self.assertEqual(parsed.payload["server_wait_s"], 90)
        self.assertIs(parsed.payload["build"], True)
        self.assertIs(parsed.payload["clean"], True)
        self.assertIs(parsed.payload["pack_only"], True)
        self.assertIs(parsed.payload["no_file_patching"], True)
        self.assertEqual(
            parsed.payload["run_id"], "12345678-1234-4234-8234-1234567890ab"
        )
        self.assertEqual(
            parsed.canonical_bytes,
            json.dumps(
                parsed.payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8"),
        )

    def test_request_rejects_invalid_types_ranges_and_combinations(self) -> None:
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF", "@Dabs Framework", "@VPPAdminTools"),
            mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
            mod_roots=(r"P:\Mods",),
        )
        base = {
            "version": 1,
            "dev_root": r"P:\ExampleMod_Suite",
            "mod": "ExampleMod",
        }
        invalid_overrides = {
            "bool_version": {"version": True},
            "bool_port": {"port": True},
            "low_port": {"port": 1023},
            "low_width": {"width": 319},
            "retail_mode": {"mode": "retail"},
            "relative_unknown_mission": {"mission": "moon"},
            "control_player_name": {"player_name": "Dev\u0001"},
            "zero_wait": {"server_wait_s": 0},
            "mods_as_delimited_string": {"extra_mods": "@CF;@Other"},
            "pack_only_without_build": {"pack_only": True},
            "source_without_build": {"source": r"P:\ExampleMod\Source"},
            "base_conflicts_with_no_base": {
                "base_mods": ["@CF"],
                "no_base_mods": True,
            },
            "kill_without_run": {"kill": True},
            "client_without_run": {"mode": "client"},
            "server_with_run": {
                "mode": "server",
                "run_id": "12345678-1234-4234-8234-1234567890ab",
            },
            "non_uuid_run": {"mode": "offline", "run_id": "run-one"},
        }

        for label, overrides in invalid_overrides.items():
            document = dict(base)
            document.update(overrides)
            expected_error = {
                "client_without_run": "client_requires_run_id",
                "server_with_run": "server_all_forbid_run_id",
                "non_uuid_run": "invalid_run_id",
            }.get(label, "invalid_dayz_test_request")
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, f"^{expected_error}$"):
                    request_module.parse_dayz_test_request(
                        json.dumps(document).encode("utf-8"), policies=(policy,)
                    )

    def test_request_rejects_invalid_encoding_size_and_unicode_form(self) -> None:
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF", "@Dabs Framework", "@VPPAdminTools"),
            mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
            mod_roots=(r"P:\Mods",),
        )
        valid = json.dumps(
            {
                "version": 1,
                "dev_root": r"P:\ExampleMod_Suite",
                "mod": "ExampleMod",
            }
        ).encode("utf-8")
        invalid_documents = {
            "oversize_whitespace": (b" " * 65_536) + valid,
            "utf8_bom": b"\xef\xbb\xbf" + valid,
            "invalid_utf8": b"\xff",
            "non_nfc": json.dumps(
                {
                    "version": 1,
                    "dev_root": r"P:\ExampleMod_Suite",
                    "mod": "ExampleMod",
                    "player_name": "Cafe\u0301",
                }
            ).encode("utf-8"),
            "surrogate": (
                b'{"version":1,"dev_root":"P:\\\\ExampleMod_Suite",'
                b'"mod":"ExampleMod","player_name":"\\ud800"}'
            ),
        }

        for label, raw in invalid_documents.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "invalid_dayz_test_request"):
                    request_module.parse_dayz_test_request(raw, policies=(policy,))

    def test_policy_table_is_closed_unique_and_absolute(self) -> None:
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF", "@Dabs Framework", "@VPPAdminTools"),
            mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
            mod_roots=(r"P:\Mods",),
        )
        raw = json.dumps(
            {
                "version": 1,
                "dev_root": r"P:\ExampleMod_Suite",
                "mod": "ExampleMod",
            }
        ).encode("utf-8")
        invalid_policy_tables = {
            "empty": (),
            "duplicate": (policy, policy),
            "relative_dev_root": (
                request_module.RequestProjectPolicy(
                    mod="ExampleMod",
                    dev_root=r"ExampleMod_Suite",
                    default_source=r"P:\ExampleMod",
                    default_base_mods=("@CF",),
                    mission_roots=(r"P:\Missions",),
                    mod_roots=(r"P:\Mods",),
                ),
            ),
            "relative_default_source": (
                request_module.RequestProjectPolicy(
                    mod="ExampleMod",
                    dev_root=r"P:\ExampleMod_Suite",
                    default_source=r"ExampleMod",
                    default_base_mods=("@CF",),
                    mission_roots=(r"P:\Missions",),
                    mod_roots=(r"P:\Mods",),
                ),
            ),
            "invalid_mod_identifier": (
                request_module.RequestProjectPolicy(
                    mod="../ExampleMod",
                    dev_root=r"P:\ExampleMod_Suite",
                    default_source=r"P:\ExampleMod",
                    default_base_mods=("@CF",),
                    mission_roots=(r"P:\Missions",),
                    mod_roots=(r"P:\Mods",),
                ),
            ),
            "relative_mission_root": (
                request_module.RequestProjectPolicy(
                    mod="ExampleMod",
                    dev_root=r"P:\ExampleMod_Suite",
                    default_source=r"P:\ExampleMod",
                    default_base_mods=("@CF",),
                    mission_roots=(r"Missions",),
                    mod_roots=(r"P:\Mods",),
                ),
            ),
            "relative_mod_root": (
                request_module.RequestProjectPolicy(
                    mod="ExampleMod",
                    dev_root=r"P:\ExampleMod_Suite",
                    default_source=r"P:\ExampleMod",
                    default_base_mods=("@CF",),
                    mission_roots=(r"P:\Missions",),
                    mod_roots=(r"Mods",),
                ),
            ),
            "unc_dev_root": (
                request_module.RequestProjectPolicy(
                    mod="ExampleMod",
                    dev_root=r"\\server\share\ExampleMod_Suite",
                    default_source=r"P:\ExampleMod",
                    default_base_mods=("@CF",),
                    mission_roots=(r"P:\Missions",),
                    mod_roots=(r"P:\Mods",),
                ),
            ),
            "device_default_source": (
                request_module.RequestProjectPolicy(
                    mod="ExampleMod",
                    dev_root=r"P:\ExampleMod_Suite",
                    default_source=r"\\?\P:\ExampleMod",
                    default_base_mods=("@CF",),
                    mission_roots=(r"P:\Missions",),
                    mod_roots=(r"P:\Mods",),
                ),
            ),
            "ads_mission_root": (
                request_module.RequestProjectPolicy(
                    mod="ExampleMod",
                    dev_root=r"P:\ExampleMod_Suite",
                    default_source=r"P:\ExampleMod",
                    default_base_mods=("@CF",),
                    mission_roots=(r"P:\Missions:stream",),
                    mod_roots=(r"P:\Mods",),
                ),
            ),
            "non_nfc_default_mod": (
                request_module.RequestProjectPolicy(
                    mod="ExampleMod",
                    dev_root=r"P:\ExampleMod_Suite",
                    default_source=r"P:\ExampleMod",
                    default_base_mods=("@Cafe\u0301",),
                    mission_roots=(r"P:\Missions",),
                    mod_roots=(r"P:\Mods",),
                ),
            ),
            "duplicate_default_mod": (
                request_module.RequestProjectPolicy(
                    mod="ExampleMod",
                    dev_root=r"P:\ExampleMod_Suite",
                    default_source=r"P:\ExampleMod",
                    default_base_mods=("@CF", "@cf"),
                    mission_roots=(r"P:\Missions",),
                    mod_roots=(r"P:\Mods",),
                ),
            ),
        }

        for label, policies in invalid_policy_tables.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "invalid_dayz_test_policy"):
                    request_module.parse_dayz_test_request(raw, policies=policies)

    def test_base_mod_defaults_and_preflight_client_are_unambiguous(self) -> None:
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF", "@Dabs Framework", "@VPPAdminTools"),
            mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
            mod_roots=(r"P:\Mods",),
        )
        cases = {
            "omitted_uses_policy": ({}, ["@CF", "@Dabs Framework", "@VPPAdminTools"]),
            "explicit_empty_stays_empty": ({"base_mods": []}, []),
            "no_base_empties_defaults": ({"no_base_mods": True}, []),
        }

        for label, (overrides, expected_base_mods) in cases.items():
            document = {
                "version": 1,
                "dev_root": r"P:\ExampleMod_Suite",
                "mod": "ExampleMod",
            }
            document.update(overrides)
            with self.subTest(label=label):
                parsed = request_module.parse_dayz_test_request(
                    json.dumps(document).encode("utf-8"), policies=(policy,)
                )
                self.assertEqual(parsed.payload["base_mods"], expected_base_mods)

    def test_request_rejects_depth_above_four_and_normalizes_recursion_errors(
        self,
    ) -> None:
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF",),
            mission_roots=(r"P:\Missions",),
            mod_roots=(r"P:\Mods",),
        )
        depth_five = (
            b'{"version":1,"dev_root":"P:\\\\ExampleMod_Suite",'
            b'"mod":"ExampleMod","extra_mods":[[[[["@CF"]]]]]}'
        )
        deeply_nested = (
            b'{"version":1,"dev_root":"P:\\\\ExampleMod_Suite",'
            b'"mod":"ExampleMod","extra_mods":'
            + (b"[" * 2_000)
            + b'"@CF"'
            + (b"]" * 2_000)
            + b"}"
        )

        for label, raw in (("depth_five", depth_five), ("recursion", deeply_nested)):
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "invalid_dayz_test_request"):
                    request_module.parse_dayz_test_request(raw, policies=(policy,))

    def test_request_paths_are_lexically_confined_to_the_selected_policy(self) -> None:
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=r"P:\ExampleMod_Suite",
            default_source=r"P:\ExampleMod",
            default_base_mods=("@CF",),
            mission_roots=(r"P:\ExampleMod_Suite\_server\mpmissions",),
            mod_roots=(r"P:\Mods",),
        )
        base = {
            "version": 1,
            "dev_root": r"P:\ExampleMod_Suite",
            "mod": "ExampleMod",
            "preflight": True,
        }
        invalid_overrides = {
            "mission_outside_root": {"mission": r"C:\Missions\evil"},
            "mission_device_path": {"mission": r"\\?\P:\Missions\evil"},
            "mission_ads": {
                "mission": r"P:\ExampleMod_Suite\_server\mpmissions:stream"
            },
            "source_outside_default": {
                "source": r"P:\OtherProject",
                "build": True,
            },
            "mod_parent_escape": {"extra_mods": [r"..\ForeignMod"]},
            "absolute_mod_outside_root": {
                "server_mods": [r"C:\Mods\@Foreign"]
            },
        }
        for label, overrides in invalid_overrides.items():
            document = dict(base)
            document.update(overrides)
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "invalid_dayz_test_request"):
                    request_module.parse_dayz_test_request(
                        json.dumps(document).encode("utf-8"), policies=(policy,)
                    )

        valid = dict(base)
        valid.update(
            {
                "mission": r"P:\ExampleMod_Suite\_server\mpmissions\custom.chernarusplus",
                "source": r"P:\ExampleMod\Source",
                "build": True,
                "extra_mods": ["@Extra"],
                "server_mods": [r"P:\Mods\@Server"],
            }
        )
        parsed = request_module.parse_dayz_test_request(
            json.dumps(valid).encode("utf-8"), policies=(policy,)
        )
        self.assertEqual(parsed.payload["mission"], valid["mission"])
        self.assertEqual(parsed.payload["source"], valid["source"])
        self.assertEqual(parsed.payload["extra_mods"], ["@Extra"])
        self.assertEqual(parsed.payload["server_mods"], [r"P:\Mods\@Server"])


if __name__ == "__main__":
    unittest.main()
