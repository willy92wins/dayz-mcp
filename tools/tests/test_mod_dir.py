from __future__ import annotations

import json
import ntpath
import unittest

from dayz_mcp import dayz_test_request, dayz_test_worker
from tests.dayz_test_worker_helpers import POLICY, RUNTIME

ISOLATED = (
    r"C:\Users\guill\AppData\Local\Temp\ah-supervisor-real-20261110"
    r"\mod-f3\@DayZ_MCP_F3"
)
WORKSHOP = (
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ"
    r"\!Workshop\@DayZ_MCP"
)


def _raw(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "version": 3,
        "dev_root": POLICY.dev_root,
        "mod": POLICY.mod,
        "mode": "server",
        "mod_dir": ISOLATED,
    }
    value.update(overrides)
    return json.dumps(value).encode("utf-8")


def _mod_arg(argv: list[str]) -> str:
    for item in argv:
        if item.startswith("-mod="):
            return item[len("-mod=") :]
    raise AssertionError("missing -mod= in argv: " + repr(argv))


class ModDirRequestTests(unittest.TestCase):
    def test_mod_dir_roundtrip_keeps_build_false(self) -> None:
        parsed = dayz_test_request.parse_dayz_test_request(
            _raw(), policies=(POLICY,)
        )
        self.assertEqual(parsed.payload["mod_dir"], ntpath.normpath(ISOLATED))
        self.assertIs(parsed.payload["build"], False)
        self.assertEqual(parsed.payload["source"], POLICY.default_source)
        self.assertEqual(parsed.payload["version"], 3)
        again = dayz_test_request.parse_dayz_test_request(
            parsed.canonical_bytes, policies=(POLICY,)
        )
        self.assertEqual(again.payload, parsed.payload)
        self.assertIs(again.payload["build"], False)

    def test_v1_document_cannot_carry_mod_dir(self) -> None:
        with self.assertRaisesRegex(ValueError, r":unknown_key$"):
            dayz_test_request.parse_dayz_test_request(
                _raw(version=1), policies=(POLICY,)
            )

    def test_mod_dir_rejects_build_and_pack_only(self) -> None:
        with self.assertRaisesRegex(
            ValueError, r"invalid_dayz_test_request:mod_dir_conflicts_with_build$"
        ):
            dayz_test_request.parse_dayz_test_request(
                _raw(build=True), policies=(POLICY,)
            )
        with self.assertRaisesRegex(
            ValueError, r"invalid_dayz_test_request:pack_only_requires_build$"
        ):
            dayz_test_request.parse_dayz_test_request(
                _raw(pack_only=True), policies=(POLICY,)
            )

    def test_mod_dir_rejects_workshop_and_default_source(self) -> None:
        with self.assertRaisesRegex(
            ValueError, r"invalid_dayz_test_request:mod_dir_in_workshop$"
        ):
            dayz_test_request.parse_dayz_test_request(
                _raw(mod_dir=WORKSHOP), policies=(POLICY,)
            )
        with self.assertRaisesRegex(
            ValueError, r"invalid_dayz_test_request:mod_dir_in_default_source$"
        ):
            dayz_test_request.parse_dayz_test_request(
                _raw(mod_dir=POLICY.default_source), policies=(POLICY,)
            )
        with self.assertRaisesRegex(
            ValueError, r"invalid_dayz_test_request:mod_dir_in_default_source$"
        ):
            dayz_test_request.parse_dayz_test_request(
                _raw(mod_dir=POLICY.default_source + r"\addon"),
                policies=(POLICY,),
            )

    def test_mod_dir_rejects_invalid_paths(self) -> None:
        for path in ("", "relative\\@Mod", r"C:nope", 123, ["C:\\x"], r"C:\foo;bar"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(
                    ValueError, r"invalid_dayz_test_request:mod_dir_invalid$"
                ):
                    dayz_test_request.parse_dayz_test_request(
                        _raw(mod_dir=path), policies=(POLICY,)
                    )


class ModDirWorkerArgvTests(unittest.TestCase):
    def test_server_argv_uses_mod_dir_and_omits_default_project(self) -> None:
        parsed = dayz_test_request.parse_dayz_test_request(
            _raw(), policies=(POLICY,)
        )
        core = dayz_test_worker._start_core(
            parsed.payload, RUNTIME, role="server", run_id=None
        )
        loaded = _mod_arg(core["argv"])
        self.assertIn(ntpath.normpath(ISOLATED), loaded.split(";"))
        implicit = ntpath.join(RUNTIME.mods_root, "@" + RUNTIME.mod)
        self.assertNotIn(implicit, loaded.split(";"))
        self.assertNotIn("!Workshop", loaded)
        self.assertNotIn(POLICY.default_source, loaded)
        self.assertIs(parsed.payload["build"], False)

    def test_mod_dir_equal_to_original_project_is_rejected_at_parse(self) -> None:
        original = ntpath.join(POLICY.mod_roots[0], "@" + POLICY.mod)
        self.assertEqual(original, ntpath.join(RUNTIME.mods_root, "@" + RUNTIME.mod))
        for path in (original, r"p:\mods\@examplemod"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(
                    ValueError,
                    r"invalid_dayz_test_request:mod_dir_is_original_project$",
                ):
                    dayz_test_request.parse_dayz_test_request(
                        _raw(mod_dir=path), policies=(POLICY,)
                    )

    def test_worker_still_rejects_mod_dir_equal_to_original_project(self) -> None:
        parsed = dayz_test_request.parse_dayz_test_request(
            _raw(), policies=(POLICY,)
        )
        payload = dict(parsed.payload)
        payload["mod_dir"] = ntpath.join(RUNTIME.mods_root, "@" + RUNTIME.mod)
        with self.assertRaisesRegex(
            ValueError, r"invalid_dayz_test_request:mod_dir_is_original_project$"
        ):
            dayz_test_worker._start_core(
                payload, RUNTIME, role="server", run_id=None
            )

    def test_omitted_mod_dir_still_loads_implicit_project(self) -> None:
        raw = json.dumps(
            {
                "version": 1,
                "dev_root": POLICY.dev_root,
                "mod": POLICY.mod,
                "mode": "server",
            }
        ).encode("utf-8")
        parsed = dayz_test_request.parse_dayz_test_request(
            raw, policies=(POLICY,)
        )
        self.assertNotIn("mod_dir", parsed.payload)
        core = dayz_test_worker._start_core(
            parsed.payload, RUNTIME, role="server", run_id=None
        )
        implicit = ntpath.join(RUNTIME.mods_root, "@" + RUNTIME.mod)
        self.assertIn(implicit, _mod_arg(core["argv"]).split(";"))


if __name__ == "__main__":
    unittest.main()
