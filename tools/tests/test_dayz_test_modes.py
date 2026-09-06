from __future__ import annotations

import dataclasses
import importlib
import unittest


class DayzTestModesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.modes = importlib.import_module("dayz_mcp.dayz_test_modes")

    def test_canonical_records_are_immutable_and_have_external_contract(self) -> None:
        records = self.modes.mode_records()

        self.assertEqual(
            tuple(record.name for record in records),
            ("all", "server", "client", "offline"),
        )
        expected = {
            "all": {
                "public": True,
                "request_visible": True,
                "roots": ("_server", "_client"),
                "starts_client": True,
                "default": True,
                "steps": (
                    ("start", "server", "_server", "new", None),
                    ("readiness", None, None, "current", None),
                    ("start", "client", "_client", "current", None),
                ),
            },
            "server": {
                "public": True,
                "request_visible": True,
                "roots": ("_server",),
                "starts_client": False,
                "default": False,
                "steps": (("start", "server", "_server", "new", None),),
            },
            "client": {
                "public": True,
                "request_visible": True,
                "roots": ("_client",),
                "starts_client": True,
                "default": False,
                "steps": (
                    ("adopt_supplied", None, None, None, True),
                    ("start", "client", "_client", "supplied", None),
                ),
            },
            "offline": {
                "public": False,
                "request_visible": True,
                "roots": ("_client",),
                "starts_client": True,
                "default": False,
                "steps": (
                    ("adopt_supplied", None, None, None, False),
                    ("start", "offline", "_client", "supplied_or_new", None),
                ),
            },
        }
        for record in records:
            contract = expected[record.name]
            self.assertEqual(record.public, contract["public"])
            self.assertEqual(record.request_visible, contract["request_visible"])
            self.assertEqual(record.artifact_roots, contract["roots"])
            self.assertEqual(record.starts_client, contract["starts_client"])
            self.assertEqual(record.default_when_omitted, contract["default"])
            self.assertEqual(
                tuple(
                    (
                        step.kind,
                        step.role,
                        step.root,
                        step.run_id_source,
                        step.required,
                    )
                    for step in record.steps
                ),
                contract["steps"],
            )

        self.assertEqual(self.modes.public_mode_names(), ("all", "server", "client"))
        self.assertEqual(
            self.modes.request_mode_names(),
            ("all", "server", "client", "offline"),
        )
        self.assertEqual(self.modes.resolve_default_mode().name, "all")
        self.assertTrue(dataclasses.is_dataclass(records[0]))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            records[0].name = "changed"

    def test_lookup_is_exact_and_unknown_fails_closed(self) -> None:
        self.assertEqual(self.modes.resolve_mode("server").name, "server")
        with self.assertRaises(self.modes.ModeAuthorityError):
            self.modes.resolve_mode("not-a-mode")

    def test_default_resolution_rejects_zero_or_multiple_defaults(self) -> None:
        records = self.modes.mode_records()
        without_default = tuple(
            dataclasses.replace(record, default_when_omitted=False) for record in records
        )
        with self.assertRaises(self.modes.ModeAuthorityError):
            self.modes.resolve_default_mode(without_default)

        with_two_defaults = tuple(
            dataclasses.replace(
                record,
                default_when_omitted=record.name in {"all", "server"},
            )
            for record in records
        )
        with self.assertRaises(self.modes.ModeAuthorityError):
            self.modes.resolve_default_mode(with_two_defaults)

        invisible_default = tuple(
            dataclasses.replace(
                record,
                request_visible=False if record.name == "all" else record.request_visible,
            )
            for record in records
        )
        with self.assertRaises(self.modes.ModeAuthorityError):
            self.modes.resolve_default_mode(invisible_default)

    def test_views_are_read_from_supplied_records_each_call(self) -> None:
        records = self.modes.mode_records()
        alternate = tuple(
            dataclasses.replace(
                record,
                public=record.name in {"server", "offline"},
                request_visible=record.name in {"server", "offline"},
                default_when_omitted=record.name == "server",
            )
            for record in records
        )
        self.assertEqual(
            self.modes.public_mode_names(alternate),
            ("server", "offline"),
        )
        self.assertEqual(self.modes.request_mode_names(alternate), ("server", "offline"))
        self.assertEqual(self.modes.resolve_default_mode(alternate).name, "server")

    def test_supplied_view_replaces_order_steps_roots_and_run_id_source(self) -> None:
        records = self.modes.mode_records()
        altered_all = dataclasses.replace(
            records[0],
            steps=(self.modes.start("client", "_alternate", "supplied"),),
            artifact_roots=("_alternate",),
        )
        alternate = (records[1], altered_all, records[2], records[3])

        resolved = self.modes.resolve_mode("all", alternate)
        looked_up = self.modes.lookup_mode("all", alternate)
        self.assertIs(resolved, altered_all)
        self.assertIs(looked_up, altered_all)
        self.assertEqual(
            tuple(
                (step.kind, step.role, step.root, step.run_id_source, step.required)
                for step in resolved.steps
            ),
            (("start", "client", "_alternate", "supplied", None),),
        )
        self.assertEqual(resolved.artifact_roots, ("_alternate",))
        self.assertEqual(self.modes.public_mode_names(alternate), ("server", "all", "client"))

    def test_supplied_view_with_duplicate_names_fails_closed_everywhere(self) -> None:
        records = self.modes.mode_records()
        duplicate_names = records + (records[0],)
        resolvers = (
            lambda: self.modes.resolve_mode("all", duplicate_names),
            lambda: self.modes.lookup_mode("all", duplicate_names),
            lambda: self.modes.resolve_default_mode(duplicate_names),
            lambda: self.modes.public_mode_names(duplicate_names),
            lambda: self.modes.request_mode_names(duplicate_names),
        )
        for resolver in resolvers:
            with self.assertRaises(self.modes.ModeAuthorityError):
                resolver()

    def test_module_record_view_can_be_replaced_between_calls(self) -> None:
        original = self.modes.MODE_RECORDS
        alternate = tuple(
            dataclasses.replace(
                record,
                public=record.name == "offline",
                request_visible=record.name == "offline",
                default_when_omitted=record.name == "offline",
            )
            for record in original
        )
        try:
            self.modes.MODE_RECORDS = alternate
            self.assertEqual(self.modes.public_mode_names(), ("offline",))
            self.assertEqual(self.modes.resolve_default_mode().name, "offline")
        finally:
            self.modes.MODE_RECORDS = original

    def test_step_factories_produce_immutable_typed_steps(self) -> None:
        adopt = self.modes.adopt_supplied(required=False)
        start = self.modes.start("offline", "_client", "supplied_or_new")
        readiness = self.modes.readiness("current")

        self.assertEqual(adopt.kind, "adopt_supplied")
        self.assertEqual(adopt.required, False)
        self.assertEqual(start.kind, "start")
        self.assertEqual((start.role, start.root, start.run_id_source), ("offline", "_client", "supplied_or_new"))
        self.assertEqual((readiness.kind, readiness.run_id_source), ("readiness", "current"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            start.root = "_server"


if __name__ == "__main__":
    unittest.main()
