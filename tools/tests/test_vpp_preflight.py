"""VPP admin-tools preflight on the secure_launcher route (ficha df93).

dayz-test.ps1 put @VPPAdminTools in every server launch by default
(templates/dayz-test.ps1:42) and repaired serverDZ.cfg and the superadmin
files before starting (:454-474). The secure_launcher route inherited none
of it: the request policy of DayZ_MCP is the only one with an empty
default_base_mods, so a server started here has admin tools only when the
caller typed them into extra_mods.

These checks pin the replacement: a read-only gate in
native_launcher_transaction -- the one function both launch routes traverse --
that verifies the admin tools a request asks for before a path is accredited,
a lease is asked for or a process exists, warns when it asks for none, and
warns for the seeding the ps1 did and this route will not do.
"""

from __future__ import annotations

import ast
import hashlib
import json
import ntpath
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from dayz_mcp import (
    dayz_test_modes,
    dayz_test_request,
    dayz_test_tool,
    dayz_test_worker,
    native_launcher_transaction as transaction,
)


RUN_ID = "12345678-1234-4234-8234-1234567890ab"
# The four lines every install of this mod carries, measured on this host for
# the three live forms of the folder.
META_VPP = (
    "protocol = 1;\n"
    "publishedid = 1828439124;\n"
    'name = "VPPAdminTools";\n'
    "timestamp = 5250883055442304087;\n"
)
META_OTHER = META_VPP.replace("1828439124", "1559212036").replace(
    "VPPAdminTools", "CF"
)
PACKAGE = Path(dayz_test_tool.__file__).resolve().parent
TOOLS = PACKAGE.parent


def _policy(
    *,
    dev_root: str = r"P:\Suite",
    default_base_mods: tuple[str, ...] = (),
    mod_roots: tuple[str, ...] = (r"P:\Mods",),
) -> dayz_test_request.RequestProjectPolicy:
    return dayz_test_request.RequestProjectPolicy(
        mod="ExampleMod",
        dev_root=dev_root,
        default_source=r"P:\ExampleMod",
        default_base_mods=default_base_mods,
        mission_roots=(dev_root + r"\_server\mpmissions",),
        mod_roots=mod_roots,
    )


def _sealed(*policies: dayz_test_request.RequestProjectPolicy) -> tuple[object, ...]:
    return tuple(types.SimpleNamespace(policy=policy) for policy in policies)


def _raw(
    policy: dayz_test_request.RequestProjectPolicy, **overrides: object
) -> bytes:
    document: dict[str, object] = {
        "version": 1,
        "mod": policy.mod,
        "dev_root": policy.dev_root,
    }
    document.update(overrides)
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


class FakeFiles:
    """Read-only host seam. It has no write surface at all, on purpose."""

    def __init__(
        self,
        *,
        files: dict[str, str] | None = None,
        unreadable: tuple[str, ...] = (),
    ) -> None:
        self.files = {
            ntpath.normcase(key): value for key, value in (files or {}).items()
        }
        self.unreadable = {ntpath.normcase(item) for item in unreadable}
        self.reads: list[str] = []

    def read_text(self, path: str) -> str:
        self.reads.append(path)
        key = ntpath.normcase(path)
        if key in self.unreadable:
            raise OSError(5, "access denied")
        if key not in self.files:
            raise FileNotFoundError(path)
        return self.files[key]


def _healthy(
    policy: dayz_test_request.RequestProjectPolicy | None = None,
) -> FakeFiles:
    """A workspace that satisfies every check the ps1 would have repaired."""

    selected = policy or _policy()
    server = ntpath.join(selected.dev_root, "_server")
    vpp = ntpath.join(server, "profiles", "VPPAdminTools", "Permissions")
    return FakeFiles(
        files={
            ntpath.join(
                selected.mod_roots[0], "@VPPAdminTools", "meta.cpp"
            ): META_VPP,
            ntpath.join(server, "serverDZ.cfg"): (
                "allowFilePatching = 1;\nvppDisablePassword = 1;\n"
            ),
            ntpath.join(vpp, "SuperAdmins", "SuperAdmins.txt"): (
                "76561198141021937\n"
            ),
            ntpath.join(vpp, "credentials.txt"): "seeded\n",
        },
    )


class VppPreflightDecisionTest(unittest.TestCase):
    """The decision itself, over a canonical payload and a fake host."""

    def _result(
        self,
        *,
        policy: dayz_test_request.RequestProjectPolicy | None = None,
        files: FakeFiles | None = None,
        **overrides: object,
    ) -> object:
        selected = policy or _policy()
        return transaction.preflight_vpp_request(
            _raw(selected, **overrides),
            sealed_policies=_sealed(selected),
            files=files if files is not None else _healthy(selected),
        )

    def test_server_mode_without_the_admin_tools_is_warned_not_refused(self) -> None:
        result = self._result(mode="all", extra_mods=["@DayZ_MCP"])

        self.assertIsNone(result.error_code)
        self.assertEqual(result.missing, ())
        self.assertEqual(result.warnings, ("vpp_mod_not_requested",))
        self.assertIn("@VPPAdminTools", result.hint)
        self.assertEqual(result.hint, transaction.VPP_ABSENT_HINT)

    def test_server_only_mode_is_warned_the_same_way(self) -> None:
        result = self._result(mode="server", extra_mods=["@DayZ_MCP"])

        self.assertIsNone(result.error_code)
        self.assertEqual(result.missing, ())
        self.assertEqual(result.warnings, ("vpp_mod_not_requested",))
        self.assertIn("@VPPAdminTools", result.hint)
        self.assertEqual(result.hint, transaction.VPP_ABSENT_HINT)

    def test_admin_tools_in_extra_mods_pass_a_healthy_workspace(self) -> None:
        result = self._result(
            mode="all", extra_mods=["@DayZ_MCP", "@VPPAdminTools"]
        )

        self.assertIsNone(result.error_code)
        self.assertEqual(result.missing, ())
        self.assertEqual(result.warnings, ())

    def test_admin_tools_in_base_mods_count_as_requested(self) -> None:
        result = self._result(
            mode="all",
            base_mods=["@VPPAdminTools"],
            extra_mods=["@DayZ_MCP"],
        )

        self.assertIsNone(result.error_code)

    def test_policy_default_base_mods_count_as_requested(self) -> None:
        policy = _policy(default_base_mods=("@CF", "@VPPAdminTools"))
        result = self._result(
            policy=policy, mode="all", extra_mods=["@DayZ_MCP"]
        )

        self.assertIsNone(result.error_code)

    def test_no_base_mods_drops_a_default_that_only_lived_in_the_policy(self) -> None:
        # The canonical payload, not the public arguments, is what the worker
        # receives: no_base_mods empties base_mods at dayz_test_request.py:365.
        policy = _policy(default_base_mods=("@VPPAdminTools",))
        result = self._result(
            policy=policy,
            mode="all",
            no_base_mods=True,
            extra_mods=["@DayZ_MCP"],
        )

        self.assertIsNone(result.error_code)
        self.assertEqual(result.warnings, ("vpp_mod_not_requested",))

    def test_absolute_admin_tools_path_counts_as_requested(self) -> None:
        result = self._result(
            mode="all",
            extra_mods=["@DayZ_MCP", ntpath.join(r"P:\Mods", "@VPPAdminTools")],
        )

        self.assertIsNone(result.error_code)

    def test_the_workshop_install_path_counts_as_requested(self) -> None:
        # Six of the ten sealed policies carry VPP as the absolute Workshop
        # path, whose basename is the published id 1828439124 (meta.cpp on this
        # host: name = "VPPAdminTools"). Matching only the folder name refused
        # those live configurations.
        workshop = ntpath.join(r"P:\Mods", "1828439124")
        policy = _policy()
        files = _healthy(policy)
        files.files[ntpath.normcase(ntpath.join(workshop, "meta.cpp"))] = META_VPP
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", workshop],
        )

        self.assertIsNone(result.error_code)
        self.assertEqual(result.missing, ())

    def test_the_workshop_install_is_probed_at_its_exact_path(self) -> None:
        workshop = ntpath.join(r"P:\Mods", "1828439124")
        policy = _policy()
        files = _healthy(policy)
        files.files.clear()
        files.files[
            ntpath.normcase(ntpath.join(r"P:\Elsewhere", "1828439124", "meta.cpp"))
        ] = META_VPP
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", workshop],
        )

        self.assertIn("vpp_mod_folder", result.missing)

    def test_a_foreign_directory_with_the_right_name_is_refused(self) -> None:
        # The delta review built exactly this: a directory named after the
        # Workshop id holding some other mod. A name is not an identity.
        for entry, meta in (
            (ntpath.join(r"P:\Mods", "1828439124"), META_OTHER),
            (ntpath.join(r"P:\Mods", "@VPPAdminTools"), META_OTHER),
        ):
            with self.subTest(entry=entry):
                policy = _policy()
                files = _healthy(policy)
                files.files[ntpath.normcase(ntpath.join(entry, "meta.cpp"))] = meta
                result = self._result(
                    policy=policy,
                    files=files,
                    mode="all",
                    extra_mods=["@DayZ_MCP", entry],
                )

                self.assertEqual(
                    result.error_code, transaction.VPP_PREFLIGHT_FAILED
                )
                self.assertIn("vpp_mod_identity", result.missing)

    def test_a_directory_with_no_meta_cpp_is_refused(self) -> None:
        workshop = ntpath.join(r"P:\Mods", "1828439124")
        policy = _policy()
        files = _healthy(policy)
        files.files.pop(
            ntpath.normcase(
                ntpath.join(policy.mod_roots[0], "@VPPAdminTools", "meta.cpp")
            )
        )
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", workshop],
        )

        self.assertIn("vpp_mod_folder", result.missing)

    def test_an_unreadable_meta_cpp_is_refused(self) -> None:
        policy = _policy()
        files = _healthy(policy)
        files.unreadable = {
            ntpath.normcase(
                ntpath.join(policy.mod_roots[0], "@VPPAdminTools", "meta.cpp")
            )
        }
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertIn("vpp_mod_folder", result.missing)

    def test_requested_admin_tools_with_no_folder_on_disk_are_refused(self) -> None:
        policy = _policy()
        files = _healthy(policy)
        files.files.pop(
            ntpath.normcase(
                ntpath.join(policy.mod_roots[0], "@VPPAdminTools", "meta.cpp")
            )
        )
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertEqual(result.error_code, transaction.VPP_PREFLIGHT_FAILED)
        self.assertIn("vpp_mod_folder", result.missing)
        self.assertNotIn("vpp_mod_not_requested", result.missing)

    def test_a_relative_entry_with_several_roots_is_refused(self) -> None:
        # The worker resolves a relative entry against ONE mods_root this layer
        # cannot read (build_native_launcher.py:503 only guarantees it is one
        # of these). Round 2 turned that into a warning and launched: the delta
        # review called it fail-open against P-D1 and it was right. Unknown is
        # not authorised; the hint names the absolute form, which is what the
        # six live multi-root policies already use.
        policy = _policy(mod_roots=(r"P:\ModsRuntime", r"P:\ModsSecondary"))
        files = _healthy(policy)
        files.files[
            ntpath.normcase(r"P:\ModsSecondary\@VPPAdminTools\meta.cpp")
        ] = META_VPP
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertEqual(result.error_code, transaction.VPP_PREFLIGHT_FAILED)
        self.assertIn("vpp_mod_root_ambiguous", result.missing)

    def test_a_relative_entry_with_several_roots_names_the_candidate_paths(
        self,
    ) -> None:
        policy = _policy(mod_roots=(r"P:\ModsRuntime", r"P:\ModsSecondary"))
        runtime = ntpath.normpath(ntpath.join(r"P:\ModsRuntime", "@VPPAdminTools"))
        secondary = ntpath.normpath(
            ntpath.join(r"P:\ModsSecondary", "@VPPAdminTools")
        )
        result = self._result(
            policy=policy,
            files=_healthy(policy),
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertEqual(result.error_code, transaction.VPP_PREFLIGHT_FAILED)
        self.assertIn("vpp_mod_root_ambiguous", result.missing)
        self.assertIn(runtime, result.missing)
        self.assertIn(secondary, result.missing)
        self.assertNotIn(transaction.VPP_CANDIDATES_UNKNOWN, result.missing)
        self.assertIn(runtime, result.hint)
        self.assertIn(secondary, result.hint)
        self.assertIn("candidate roots:", result.hint)
        _assert_hint_leads_with_absolute_workshop_form(self, result.hint)

    def test_the_preflight_hint_constant_names_both_forms_in_order(self) -> None:
        _assert_hint_leads_with_absolute_workshop_form(
            self, transaction.VPP_PREFLIGHT_HINT
        )

    def test_dropping_the_absolute_form_from_the_hint_fails_the_order_pin(
        self,
    ) -> None:
        """The find()-order trap: absence of A made find(A) < find(B) true.

        Substituting VPP_PREFLIGHT_HINT in memory with a text that only
        names @VPPAdminTools used to leave the two order tests green.
        This is that mutation. Presence is required first, so the pin
        must reject the mutated hint.
        """
        mutated = (
            "the requested admin tools are not usable: put the installed "
            "mod in extra_mods as @VPPAdminTools"
        )
        with patch.object(transaction, "VPP_PREFLIGHT_HINT", mutated):
            policy = _policy(mod_roots=(r"P:\ModsRuntime", r"P:\ModsSecondary"))
            result = self._result(
                policy=policy,
                files=_healthy(policy),
                mode="all",
                extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
            )
        # Census still lists the paths. Only the recommendation lost the
        # absolute form. find() still treats that as "A leads B".
        self.assertEqual(result.hint.find("absolute Workshop path"), -1)
        self.assertGreater(result.hint.find("@VPPAdminTools"), -1)
        self.assertLess(
            result.hint.find("absolute Workshop path"),
            result.hint.find("@VPPAdminTools"),
        )
        with self.assertRaises(AssertionError):
            _assert_hint_leads_with_absolute_workshop_form(self, result.hint)

    def test_the_result_docstring_admits_paths_in_missing(self) -> None:
        text = " ".join((transaction.VppPreflightResult.__doc__ or "").split())
        self.assertNotIn("missing and warnings stay tokens", text)
        self.assertIn("missing carries tokens", text)
        self.assertIn("candidate paths", text)

    def test_a_single_root_success_does_not_name_candidate_roots(self) -> None:
        result = self._result(
            mode="all", extra_mods=["@DayZ_MCP", "@VPPAdminTools"]
        )

        self.assertIsNone(result.error_code)
        self.assertEqual(result.missing, ())
        self.assertNotIn("candidate roots", result.hint)
        self.assertNotIn(transaction.VPP_CANDIDATES_UNKNOWN, result.missing)
        self.assertNotIn(transaction.VPP_CANDIDATES_TRUNCATED, result.missing)

    def test_candidate_roots_are_capped_and_marked_truncated(self) -> None:
        cap = transaction.VPP_CANDIDATE_ROOT_CAP
        roots = tuple(rf"P:\Mods{index}" for index in range(cap + 1))
        policy = _policy(mod_roots=roots)
        shown = [
            ntpath.normpath(ntpath.join(root, "@VPPAdminTools"))
            for root in roots[:cap]
        ]
        omitted = ntpath.normpath(ntpath.join(roots[-1], "@VPPAdminTools"))
        result = self._result(
            policy=policy,
            files=_healthy(policy),
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertEqual(result.error_code, transaction.VPP_PREFLIGHT_FAILED)
        self.assertIn("vpp_mod_root_ambiguous", result.missing)
        self.assertIn(transaction.VPP_CANDIDATES_TRUNCATED, result.missing)
        for path in shown:
            self.assertIn(path, result.missing)
            self.assertIn(path, result.hint)
        self.assertNotIn(omitted, result.missing)
        self.assertNotIn(omitted, result.hint)
        self.assertIn(f"truncated at {cap}", result.hint)
        named = [item for item in result.missing if ntpath.isabs(item)]
        self.assertEqual(named, shown)

    def test_unenumerable_candidate_roots_are_not_an_empty_list(self) -> None:
        # parse_dayz_test_request rejects an empty mod_roots tuple, so this
        # call goes straight to evaluate_vpp_preflight with a raw policy.
        policy = _policy(mod_roots=())
        result = transaction.evaluate_vpp_preflight(
            {
                "mode": "all",
                "mod": policy.mod,
                "dev_root": policy.dev_root,
                "base_mods": [],
                "extra_mods": ["@DayZ_MCP", "@VPPAdminTools"],
            },
            policy,
            files=FakeFiles(),
        )

        self.assertEqual(result.error_code, transaction.VPP_PREFLIGHT_FAILED)
        self.assertIn("vpp_mod_root_ambiguous", result.missing)
        self.assertIn(transaction.VPP_CANDIDATES_UNKNOWN, result.missing)
        self.assertFalse(any(ntpath.isabs(item) for item in result.missing))
        self.assertIn("could not be enumerated", result.hint)
        self.assertNotIn("candidate roots:", result.hint)

    def test_an_absolute_entry_passes_under_a_multi_root_policy(self) -> None:
        # The escape the refusal above points at, and the shape the six live
        # multi-root policies carry.
        policy = _policy(mod_roots=(r"P:\ModsRuntime", r"P:\ModsSecondary"))
        workshop = ntpath.join(r"P:\ModsSecondary", "1828439124")
        files = _healthy(policy)
        files.files[ntpath.normcase(ntpath.join(workshop, "meta.cpp"))] = META_VPP
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", workshop],
        )

        self.assertIsNone(result.error_code)

    def test_a_single_root_resolves_a_relative_entry_exactly(self) -> None:
        policy = _policy(mod_roots=(r"P:\ModsRuntime",))
        files = _healthy(policy)
        files.files.pop(
            ntpath.normcase(r"P:\ModsRuntime\@VPPAdminTools\meta.cpp")
        )
        files.files[
            ntpath.normcase(r"P:\ModsSecondary\@VPPAdminTools\meta.cpp")
        ] = META_VPP
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertIn("vpp_mod_folder", result.missing)
        self.assertNotIn("vpp_mod_root_ambiguous", result.missing)

    def test_client_mode_does_not_require_the_admin_tools(self) -> None:
        result = self._result(
            files=FakeFiles(),
            mode="client",
            run_id=RUN_ID,
            extra_mods=["@DayZ_MCP"],
        )

        self.assertIsNone(result.error_code)
        self.assertEqual(result.missing, ())
        self.assertEqual(result.warnings, ())

    def test_offline_mode_does_not_require_the_admin_tools(self) -> None:
        result = self._result(
            files=FakeFiles(), mode="offline", extra_mods=["@DayZ_MCP"]
        )

        self.assertIsNone(result.error_code)
        self.assertEqual(result.missing, ())

    def test_a_preflight_request_fails_exactly_where_a_launch_would(self) -> None:
        # dayz_test_worker.py:547-550 states the rule for the sealed worker:
        # a preflight must fail exactly where a real launch would.
        policy = _policy()
        files = _healthy(policy)
        files.files[
            ntpath.normcase(ntpath.join(policy.dev_root, "_server", "serverDZ.cfg"))
        ] = "allowFilePatching = 1;\n"
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            preflight=True,
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertEqual(result.error_code, transaction.VPP_PREFLIGHT_FAILED)
        self.assertIn("vpp_disable_password", result.missing)

    def test_a_preflight_request_without_admin_tools_is_warned_like_a_launch(
        self,
    ) -> None:
        result = self._result(
            mode="all", preflight=True, extra_mods=["@DayZ_MCP"]
        )

        self.assertIsNone(result.error_code)
        self.assertEqual(result.warnings, ("vpp_mod_not_requested",))

    def test_a_run_that_requests_no_admin_tools_reads_nothing(self) -> None:
        files = FakeFiles()
        result = self._result(
            files=files, mode="all", extra_mods=["@DayZ_MCP"]
        )

        self.assertIsNone(result.error_code)
        self.assertEqual(result.warnings, ("vpp_mod_not_requested",))
        self.assertEqual(files.reads, [])

    def test_missing_server_config_is_refused(self) -> None:
        policy = _policy()
        files = _healthy(policy)
        files.files.pop(
            ntpath.normcase(ntpath.join(policy.dev_root, "_server", "serverDZ.cfg"))
        )
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertEqual(result.error_code, transaction.VPP_PREFLIGHT_FAILED)
        self.assertIn("server_config", result.missing)

    def test_server_config_without_vpp_disable_password_is_refused(self) -> None:
        policy = _policy()
        files = _healthy(policy)
        files.files[
            ntpath.normcase(ntpath.join(policy.dev_root, "_server", "serverDZ.cfg"))
        ] = "allowFilePatching = 1;\n"
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertEqual(result.error_code, transaction.VPP_PREFLIGHT_FAILED)
        self.assertIn("vpp_disable_password", result.missing)

    def test_vpp_disable_password_set_to_zero_is_refused(self) -> None:
        # The ps1 only checks the key is PRESENT (:468); a value of 0 leaves the
        # superadmin at the password prompt, which is the reported symptom.
        policy = _policy()
        files = _healthy(policy)
        files.files[
            ntpath.normcase(ntpath.join(policy.dev_root, "_server", "serverDZ.cfg"))
        ] = "vppDisablePassword = 0;\n"
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertIn("vpp_disable_password", result.missing)

    def test_a_key_that_only_lives_in_a_comment_does_not_pass(self) -> None:
        # The engine never reads a commented key, so this cfg is exactly the
        # reported symptom (VPP asks for a password) with a green gate.
        for body in (
            "// vppDisablePassword = 1;\nvppDisablePassword = 0;\n",
            "/* vppDisablePassword = 1; */\nvppDisablePassword = 0;\n",
            "// vppDisablePassword = 1;\n",
        ):
            with self.subTest(body=body):
                policy = _policy()
                files = _healthy(policy)
                files.files[
                    ntpath.normcase(
                        ntpath.join(policy.dev_root, "_server", "serverDZ.cfg")
                    )
                ] = body
                result = self._result(
                    policy=policy,
                    files=files,
                    mode="all",
                    extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
                )

                self.assertIn("vpp_disable_password", result.missing)

    def test_two_live_assignments_that_disagree_are_refused(self) -> None:
        policy = _policy()
        files = _healthy(policy)
        files.files[
            ntpath.normcase(ntpath.join(policy.dev_root, "_server", "serverDZ.cfg"))
        ] = "vppDisablePassword = 1;\nvppDisablePassword = 0;\n"
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertIn("vpp_disable_password", result.missing)

    def test_a_live_key_after_a_commented_one_still_passes(self) -> None:
        # Positive control for the comment stripping: it must not eat the real
        # assignment that follows a commented example.
        policy = _policy()
        files = _healthy(policy)
        files.files[
            ntpath.normcase(ntpath.join(policy.dev_root, "_server", "serverDZ.cfg"))
        ] = "// vppDisablePassword = 0;  ejemplo\nvppDisablePassword = 1;\n"
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertIsNone(result.error_code)

    def test_dead_ground_never_counts_as_a_live_key(self) -> None:
        # The three shapes the delta review executed against the regex: a key
        # inside an unterminated /* block, a key that is only the tail of
        # another identifier, and a key inside a quoted string.
        for label, body in (
            ("unterminated_block", "/* vppDisablePassword=1;"),
            ("prefixed_identifier", "notvppDisablePassword=1;\n"),
            ("inside_string", 'motd[]={"vppDisablePassword=1;"};\n'),
            (
                "unterminated_block_hides_a_live_zero",
                "vppDisablePassword=0;\n/* vppDisablePassword=1;",
            ),
        ):
            with self.subTest(label=label):
                policy = _policy()
                files = _healthy(policy)
                files.files[
                    ntpath.normcase(
                        ntpath.join(policy.dev_root, "_server", "serverDZ.cfg")
                    )
                ] = body
                result = self._result(
                    policy=policy,
                    files=files,
                    mode="all",
                    extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
                )

                self.assertIn("vpp_disable_password", result.missing)

    def test_a_live_key_after_dead_ground_still_passes(self) -> None:
        # Positive control for the scanner: it must not eat the real assignment
        # that follows a comment, a string, or a lookalike identifier.
        policy = _policy()
        files = _healthy(policy)
        files.files[
            ntpath.normcase(ntpath.join(policy.dev_root, "_server", "serverDZ.cfg"))
        ] = (
            "// vppDisablePassword = 0;\n"
            'hostname = "http://example/x";\n'
            "notvppDisablePassword=0;\n"
            "/* vppDisablePassword = 0; */\n"
            "vppDisablePassword=1;\n"
        )
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertIsNone(result.error_code)

    def test_a_config_bigger_than_the_read_cap_is_refused(self) -> None:
        # The cap made a prefix indistinguishable from the file, so a later
        # contradicting assignment was invisible. A prefix is not the file.
        policy = _policy()
        files = _healthy(policy)
        body = (
            "vppDisablePassword=1;\n"
            + "x=1;\n" * 60000
            + "vppDisablePassword=0;\n"
        )
        self.assertGreater(len(body), transaction._MAX_PREFLIGHT_READ_CHARS)
        files.files[
            ntpath.normcase(ntpath.join(policy.dev_root, "_server", "serverDZ.cfg"))
        ] = body
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertEqual(result.error_code, transaction.VPP_PREFLIGHT_FAILED)
        self.assertIn("server_config_unverifiable", result.missing)

    def test_the_host_seam_reports_the_overflow_instead_of_hiding_it(self) -> None:
        with TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "big.cfg"
            path.write_text("y" * (transaction._MAX_PREFLIGHT_READ_CHARS + 500))
            text = transaction.HostVppFiles().read_text(str(path))

        self.assertEqual(len(text), transaction._MAX_PREFLIGHT_READ_CHARS + 1)

    def test_unreadable_server_config_fails_closed(self) -> None:
        policy = _policy()
        files = _healthy(policy)
        files.unreadable = {
            ntpath.normcase(ntpath.join(policy.dev_root, "_server", "serverDZ.cfg"))
        }
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertEqual(result.error_code, transaction.VPP_PREFLIGHT_FAILED)
        self.assertIn("server_config_unreadable", result.missing)

    def test_absent_superadmins_file_only_warns(self) -> None:
        policy = _policy()
        files = _healthy(policy)
        files.files.pop(
            ntpath.normcase(
                ntpath.join(
                    policy.dev_root,
                    "_server",
                    "profiles",
                    "VPPAdminTools",
                    "Permissions",
                    "SuperAdmins",
                    "SuperAdmins.txt",
                )
            )
        )
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertIsNone(result.error_code)
        self.assertIn("vpp_superadmins_absent", result.warnings)

    def test_corrupt_superadmins_file_only_warns(self) -> None:
        # dayz-test.ps1:398 keeps only clean 17-digit SteamID64 tokens; a
        # concatenated 34-digit line is the corruption it self-heals.
        policy = _policy()
        files = _healthy(policy)
        files.files[
            ntpath.normcase(
                ntpath.join(
                    policy.dev_root,
                    "_server",
                    "profiles",
                    "VPPAdminTools",
                    "Permissions",
                    "SuperAdmins",
                    "SuperAdmins.txt",
                )
            )
        ] = "7656119814102193776561199641705881\n"
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertIsNone(result.error_code)
        self.assertIn("vpp_superadmins_absent", result.warnings)

    def test_absent_credentials_file_only_warns(self) -> None:
        policy = _policy()
        files = _healthy(policy)
        files.files.pop(
            ntpath.normcase(
                ntpath.join(
                    policy.dev_root,
                    "_server",
                    "profiles",
                    "VPPAdminTools",
                    "Permissions",
                    "credentials.txt",
                )
            )
        )
        result = self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

        self.assertIsNone(result.error_code)
        self.assertIn("vpp_credentials_absent", result.warnings)

    def test_a_client_run_reads_nothing_at_all(self) -> None:
        files = _healthy()
        transaction.preflight_vpp_request(
            _raw(_policy(), mode="client", run_id=RUN_ID, extra_mods=["@DayZ_MCP"]),
            sealed_policies=_sealed(_policy()),
            files=files,
        )

        self.assertEqual(files.reads, [])


class VppPreflightEnforcementTest(unittest.TestCase):
    def _parsed(self, policy, **overrides):
        return dayz_test_request.parse_dayz_test_request(
            _raw(policy, **overrides), policies=(policy,)
        ).payload

    def test_enforce_raises_the_declared_token(self) -> None:
        policy = _policy()
        with self.assertRaises(ValueError) as caught:
            transaction.enforce_vpp_preflight(
                self._parsed(
                    policy,
                    mode="all",
                    extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
                ),
                (policy,),
                files=FakeFiles(),
            )

        self.assertEqual(str(caught.exception), transaction.VPP_PREFLIGHT_FAILED)

    def test_enforce_returns_a_warning_result_when_no_admin_tools_are_requested(
        self,
    ) -> None:
        policy = _policy()
        result = transaction.enforce_vpp_preflight(
            self._parsed(policy, mode="all", extra_mods=["@DayZ_MCP"]),
            (policy,),
            files=_healthy(policy),
        )

        self.assertIsNone(result.error_code)
        self.assertEqual(result.warnings, ("vpp_mod_not_requested",))

    def test_enforce_returns_the_result_when_the_workspace_is_usable(self) -> None:
        policy = _policy()
        result = transaction.enforce_vpp_preflight(
            self._parsed(
                policy, mode="all", extra_mods=["@DayZ_MCP", "@VPPAdminTools"]
            ),
            (policy,),
            files=_healthy(policy),
        )

        self.assertIsNone(result.error_code)

    def test_the_refusal_writes_nothing_to_the_workspace(self) -> None:
        with TemporaryDirectory() as raw_root:
            root = ntpath.normpath(raw_root)
            policy = dayz_test_request.RequestProjectPolicy(
                mod="ExampleMod",
                dev_root=root,
                default_source=root,
                default_base_mods=(),
                mission_roots=(root,),
                mod_roots=(root,),
            )
            before = _tree_digest(Path(root))
            with self.assertRaises(ValueError):
                transaction.enforce_vpp_preflight(
                    self._parsed(
                        policy,
                        mode="all",
                        extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
                    ),
                    (policy,),
                )
            self.assertEqual(_tree_digest(Path(root)), before)

    def test_the_warning_writes_nothing_to_the_workspace(self) -> None:
        with TemporaryDirectory() as raw_root:
            root = ntpath.normpath(raw_root)
            policy = dayz_test_request.RequestProjectPolicy(
                mod="ExampleMod",
                dev_root=root,
                default_source=root,
                default_base_mods=(),
                mission_roots=(root,),
                mod_roots=(root,),
            )
            before = _tree_digest(Path(root))
            transaction.enforce_vpp_preflight(
                self._parsed(policy, mode="all", extra_mods=["@DayZ_MCP"]),
                (policy,),
            )
            self.assertEqual(_tree_digest(Path(root)), before)

    def test_the_host_seam_exposes_no_write_surface(self) -> None:
        seam = transaction.HostVppFiles()
        public = {name for name in dir(seam) if not name.startswith("_")}

        self.assertEqual(public, {"read_text"})


def _assert_hint_leads_with_absolute_workshop_form(
    test: unittest.TestCase, hint: str
) -> None:
    """Presence first: find() returning -1 is absence, not a valid order.

    The two forms are the absolute Workshop path and the relative
    recommendation `as @VPPAdminTools`. The unique-root condition is
    what stops that relative form being offered as if it always worked.
    A Windows path such as P:\\ModsRuntime\\@VPPAdminTools contains the
    folder name, so the relative form is the `as @VPPAdminTools` phrase,
    not a bare `@VPPAdminTools` substring.
    """
    absolute = "absolute Workshop path"
    relative = "as @VPPAdminTools"
    single_root = "single policy root"
    test.assertIn(absolute, hint)
    test.assertIn(relative, hint)
    test.assertIn(single_root, hint)
    absolute_at = hint.find(absolute)
    relative_at = hint.find(relative)
    # assertIn already refused absence. These two make the -1 trap
    # visible: -1 means "the subject is not there", not "it leads".
    test.assertNotEqual(absolute_at, -1)
    test.assertNotEqual(relative_at, -1)
    test.assertLess(absolute_at, relative_at)


class _CountingControlClient:
    def __init__(self) -> None:
        self.acquire_calls = 0

    async def session_acquire_wait(self, *_args: object, **_kwargs: object) -> object:
        self.acquire_calls += 1
        return {"status": "denied"}

    async def session_heartbeat(self, _token: str) -> dict[str, object]:
        return {}

    async def session_release(self, _token: str) -> dict[str, object]:
        return {}

    async def session_status(self) -> dict[str, object]:
        return {}


class VppPreflightChokepointTest(unittest.IsolatedAsyncioTestCase):
    """The transaction refuses before the lease and before the consumer runs."""

    async def _transaction(
        self,
        *,
        policy: dayz_test_request.RequestProjectPolicy | None = None,
        **overrides: object,
    ):
        policy = policy or _policy()
        client = _CountingControlClient()
        consumer_calls: list[object] = []

        async def consumer(**kwargs: object) -> int:
            consumer_calls.append(kwargs)
            return 0

        raised: BaseException | None = None
        try:
            await transaction.execute_native_launcher_transaction(
                _raw(policy, **overrides),
                sealed_policies=_sealed(policy),
                control_client=client,
                consumer=consumer,
            )
        except BaseException as error:
            raised = error
        return raised, client, consumer_calls

    async def test_a_server_request_with_unusable_admin_tools_never_takes_a_lease(
        self,
    ) -> None:
        # The transaction reads the real host (there is no file seam here), so
        # the policy is rooted in an empty temporary directory: the requested
        # mod folder and serverDZ.cfg are absent by construction, not by
        # whatever P: holds on the machine running the suite.
        with TemporaryDirectory() as raw_root:
            root = ntpath.normpath(raw_root)
            policy = dayz_test_request.RequestProjectPolicy(
                mod="ExampleMod",
                dev_root=root,
                default_source=root,
                default_base_mods=(),
                mission_roots=(root,),
                mod_roots=(root,),
            )
            raised, client, consumer_calls = await self._transaction(
                policy=policy,
                mode="all",
                extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
            )

        self.assertIsInstance(raised, ValueError)
        self.assertEqual(str(raised), transaction.VPP_PREFLIGHT_FAILED)
        self.assertEqual(client.acquire_calls, 0)
        self.assertEqual(consumer_calls, [])

    async def test_a_server_request_without_admin_tools_gets_past_the_gate(
        self,
    ) -> None:
        raised, _client, consumer_calls = await self._transaction(
            mode="all", extra_mods=["@DayZ_MCP"]
        )

        self.assertNotEqual(str(raised), transaction.VPP_PREFLIGHT_FAILED)
        self.assertEqual(consumer_calls, [])

    async def test_an_offline_request_gets_past_the_gate(self) -> None:
        # Not a happy path: the stub policies fail path accreditation further
        # down, which is where this stops. What it measures is that the gate did
        # not refuse it -- the failure that arrives is somebody else's.
        raised, _client, consumer_calls = await self._transaction(mode="offline")

        self.assertNotEqual(str(raised), transaction.VPP_PREFLIGHT_FAILED)
        self.assertEqual(consumer_calls, [])

    async def test_a_client_request_gets_past_the_gate(self) -> None:
        raised, _client, _calls = await self._transaction(
            mode="client", run_id=RUN_ID, extra_mods=["@DayZ_MCP"]
        )

        self.assertNotEqual(str(raised), transaction.VPP_PREFLIGHT_FAILED)


def _tree_digest(root: Path) -> str:
    aggregate = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        aggregate.update(path.relative_to(root).as_posix().encode("utf-8"))
        aggregate.update(b"\0")
        if path.is_file():
            aggregate.update(hashlib.sha256(path.read_bytes()).digest())
        aggregate.update(b"\n")
    return aggregate.hexdigest()


class VppPreflightInvariantTest(unittest.TestCase):
    """The server root the preflight probes is the one the worker launches."""

    def test_the_probed_paths_are_the_ones_the_worker_composes(self) -> None:
        policy = _policy()
        parsed = dayz_test_request.parse_dayz_test_request(
            _raw(policy, mode="all", extra_mods=["@DayZ_MCP", "@VPPAdminTools"]),
            policies=(policy,),
        )
        runtime = dayz_test_worker.WorkerRuntimePolicy(
            dev_root=policy.dev_root,
            mod=policy.mod,
            diag_executable=r"P:\Game\DayZDiag_x64.exe",
            game_directory=r"P:\Game",
            mission_aliases=(("chernarus", r"P:\Missions\chernarus"),),
            mods_root=policy.mod_roots[0],
            build_temp_root=r"P:\temp",
            build_source_basename=None,
        )
        core = dayz_test_worker._start_core(
            parsed.payload, runtime, role="server", run_id=None
        )
        argv = list(core["argv"])
        config = next(
            item[len("-config="):] for item in argv if item.startswith("-config=")
        )
        profiles = next(
            item[len("-profiles="):] for item in argv if item.startswith("-profiles=")
        )
        mod_string = next(
            item[len("-mod="):] for item in argv if item.startswith("-mod=")
        )

        paths = transaction.vpp_preflight_paths(parsed.payload, policy)

        self.assertEqual(paths.server_config, config)
        self.assertEqual(paths.server_profiles, profiles)
        self.assertEqual(
            tuple(
                ntpath.basename(item)
                for item in transaction.effective_mod_entries(parsed.payload)
            ),
            tuple(ntpath.basename(item) for item in mod_string.split(";")),
        )

    def test_the_server_role_comes_from_the_mode_authority(self) -> None:
        for record in dayz_test_modes.mode_records():
            expected = any(
                step.kind == "start" and step.role == "server"
                for step in record.steps
            )
            with self.subTest(mode=record.name):
                self.assertEqual(
                    transaction.mode_starts_server(record.name), expected
                )

    def test_an_unknown_mode_is_treated_as_a_server_start(self) -> None:
        self.assertTrue(transaction.mode_starts_server("not-a-mode"))


class VppPreflightCallSiteTest(unittest.TestCase):
    """One funnel into the native launcher, and the gate sits inside it."""

    @staticmethod
    def _callers(name: str) -> set[str]:
        """Call sites across the whole of tools/, not just the package.

        The first version scanned dayz_mcp/ only and missed h9_native_probe.py,
        which drives the native backend directly. Scanning wider is what turns a
        silent bypass into one this test can see.
        """
        callers: set[str] = set()
        for path in sorted(TOOLS.rglob("*.py")):
            parts = set(path.relative_to(TOOLS).parts)
            if parts & {".venv-mcp", "tests", "native-launchers", "__pycache__"}:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                called = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                if called == name:
                    callers.add(path.name)
        return callers

    def test_the_launcher_is_reached_through_one_transaction_only(self) -> None:
        # h9_native_probe.py is host-local and untracked (tests/test_lote_w_h9.py
        # :18-21 says so and skips without it), so a clean checkout has one
        # caller and this host has two. Subtracting the declared exception keeps
        # the assertion exact in both trees while a NEW caller still fails it.
        self.assertEqual(
            self._callers("launch_registered_native") - {"h9_native_probe.py"},
            {"secure_launcher.py"},
        )
        self.assertEqual(
            self._callers("execute_native_launcher_transaction"),
            {"secure_launcher.py"},
        )
        self.assertEqual(
            self._callers("execute_secure_launcher_request"),
            {"secure_launcher.py", "dayz_test_tool.py"},
        )

    def test_every_caller_of_the_backend_runs_the_gate_first(self) -> None:
        """h9_native_probe drives the backend directly, so it enforces itself.

        The census allows exactly one such caller. This asserts the one allowed
        exception is not an exception at all: it calls the same
        enforce_vpp_preflight, and calls it before the backend. The probe is
        host-local and untracked, so a tree without it skips, exactly as
        tests/test_lote_w_h9.py:18-21 does.
        """
        probe = TOOLS / "h9_native_probe.py"
        if not probe.is_file():
            self.skipTest("h9_native_probe.py is not present in this tree")
        source = probe.read_text(encoding="utf-8")
        enforce = source.index("enforce_vpp_preflight(parsed.payload")
        launch = source.index("await launch_registered_native(")

        self.assertLess(enforce, launch)

    def test_the_transaction_enforces_the_gate_before_it_accredits(self) -> None:
        source = (PACKAGE / "native_launcher_transaction.py").read_text(
            encoding="utf-8"
        )
        body = source[source.index("async def execute_native_launcher_transaction") :]
        enforce = body.index("enforce_vpp_preflight(")
        accredit = body.index("accredit_request_paths(")
        acquire = body.index("session_acquire_wait(")

        self.assertLess(enforce, accredit)
        self.assertLess(enforce, acquire)


class _Opened:
    def __init__(self) -> None:
        self.validated = False

    def __enter__(self) -> "_Opened":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def validate_native_pe(self) -> None:
        self.validated = True


class _Bundle:
    def __init__(self, sealed_policies: tuple[object, ...]) -> None:
        self.sealed_policies = sealed_policies

    def __enter__(self) -> "_Bundle":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Runtime:
    def __init__(self) -> None:
        self.active_lease_token = None
        self.active_ticket = None
        self.active_operation_id = None
        self.daemon_policy = object()
        self.lifecycle: dict[str, object] = {"runs": []}

    async def bridge_status_payload(self) -> dict[str, object]:
        return {"ready": {"ready": True, "reason": "ready"}}

    async def lifecycle_status(self) -> dict[str, object]:
        return self.lifecycle

    async def reconcile_idle_session(self) -> dict[str, object]:
        return {"reconciled": False}


class DayzTestRunVppGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_server_run_with_unusable_admin_tools_never_reaches_the_launcher(
        self,
    ) -> None:
        policy = _policy()
        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool, "preflight_vpp_request", new=_refused
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=AsyncMock(),
        ) as launch:
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
            )

        launch.assert_not_awaited()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phase"], "validating")
        self.assertEqual(result["error_code"], transaction.VPP_PREFLIGHT_FAILED)
        self.assertEqual(result["vpp_missing"], ["vpp_disable_password"])
        self.assertIn("@VPPAdminTools", result["remediation"])
        self.assertIsNone(result["run_id"])

    async def test_warnings_travel_with_a_successful_run(self) -> None:
        policy = _policy()

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            kwargs["output_sink"](
                "stdout",
                json.dumps(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            return 0

        def warned(*_args: object, **_kwargs: object) -> object:
            return transaction.VppPreflightResult(
                error_code=None,
                missing=(),
                warnings=("vpp_superadmins_absent",),
                hint=transaction.VPP_PREFLIGHT_HINT,
            )

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool, "preflight_vpp_request", new=warned
        ), patch.object(
            dayz_test_tool, "evaluate_steam_session", return_value=_steam_ok()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
            )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["vpp_warnings"], ["vpp_superadmins_absent"])
        self.assertEqual(result["vpp_missing"], [])

    async def test_the_absent_admin_tools_warning_travels_with_a_successful_run(
        self,
    ) -> None:
        policy = _policy()

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            kwargs["output_sink"](
                "stdout",
                json.dumps(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            return 0

        def warned(*_args: object, **_kwargs: object) -> object:
            return transaction.VppPreflightResult(
                error_code=None,
                missing=(),
                warnings=("vpp_mod_not_requested",),
                hint=transaction.VPP_ABSENT_HINT,
            )

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool, "preflight_vpp_request", new=warned
        ), patch.object(
            dayz_test_tool, "evaluate_steam_session", return_value=_steam_ok()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP"],
            )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["vpp_warnings"], ["vpp_mod_not_requested"])
        self.assertEqual(result["vpp_missing"], [])

    async def test_ambiguous_roots_travel_to_the_caller_dict(self) -> None:
        policy = _policy(mod_roots=(r"P:\ModsRuntime", r"P:\ModsSecondary"))
        files = _healthy(policy)
        runtime = ntpath.normpath(ntpath.join(r"P:\ModsRuntime", "@VPPAdminTools"))
        secondary = ntpath.normpath(
            ntpath.join(r"P:\ModsSecondary", "@VPPAdminTools")
        )
        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            transaction, "HostVppFiles", return_value=files
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=AsyncMock(),
        ) as launch:
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
            )

        launch.assert_not_awaited()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phase"], "validating")
        self.assertEqual(result["error_code"], transaction.VPP_PREFLIGHT_FAILED)
        self.assertIn("vpp_mod_root_ambiguous", result["vpp_missing"])
        self.assertIn(runtime, result["vpp_missing"])
        self.assertIn(secondary, result["vpp_missing"])
        self.assertIn(runtime, result["remediation"])
        self.assertIn(secondary, result["remediation"])
        _assert_hint_leads_with_absolute_workshop_form(
            self, str(result["remediation"])
        )

    async def test_a_single_root_success_does_not_carry_candidate_roots(
        self,
    ) -> None:
        policy = _policy()
        files = _healthy(policy)

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            kwargs["output_sink"](
                "stdout",
                json.dumps(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            transaction, "HostVppFiles", return_value=files
        ), patch.object(
            dayz_test_tool, "evaluate_steam_session", return_value=_steam_ok()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
            )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["vpp_missing"], [])
        self.assertNotIn("vpp_candidate_roots", result)
        self.assertNotIn(
            ntpath.normpath(ntpath.join(r"P:\Mods", "@VPPAdminTools")),
            result["vpp_missing"],
        )
        self.assertIsNone(result["remediation"])


def _refused(*_args: object, **_kwargs: object) -> object:
    return transaction.VppPreflightResult(
        error_code=transaction.VPP_PREFLIGHT_FAILED,
        missing=("vpp_disable_password",),
        warnings=(),
        hint=transaction.VPP_PREFLIGHT_HINT,
    )


def _steam_ok() -> object:
    from dayz_mcp import steam_preflight

    return steam_preflight.SteamSessionResult(
        error_code=None,
        steam_registered_pid=1,
        steam_live_pids=(1,),
        remediation=steam_preflight.REMEDIATION,
    )


if __name__ == "__main__":
    unittest.main()


class VppPreflightRound4Test(unittest.TestCase):
    """Ronda 4 (delta-2 R3-F-01 and R3-F-02): meta.cpp is scanned with the cfg
    scanner instead of grepped, and a single-quoted string is dead ground."""

    def _result(
        self,
        *,
        policy: dayz_test_request.RequestProjectPolicy | None = None,
        files: FakeFiles | None = None,
        **overrides: object,
    ) -> object:
        selected = policy or _policy()
        return transaction.preflight_vpp_request(
            _raw(selected, **overrides),
            sealed_policies=_sealed(selected),
            files=files if files is not None else _healthy(selected),
        )

    def _with_meta(self, meta: str) -> object:
        policy = _policy()
        files = _healthy(policy)
        files.files[
            ntpath.normcase(
                ntpath.join(policy.mod_roots[0], "@VPPAdminTools", "meta.cpp")
            )
        ] = meta
        return self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

    def _with_config(self, body: str) -> object:
        policy = _policy()
        files = _healthy(policy)
        files.files[
            ntpath.normcase(ntpath.join(policy.dev_root, "_server", "serverDZ.cfg"))
        ] = body
        return self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

    def test_meta_cpp_forms_that_do_not_prove_the_identity(self) -> None:
        # Every row of the delta-2 matrix that PASSED with the raw regex: the id
        # in dead ground next to a live foreign one, two live ids, a suffix, and
        # a file past the read cap (a prefix proves nothing).
        cap = transaction._MAX_PREFLIGHT_READ_CHARS
        for label, meta in (
            ("id_only_in_a_line_comment", "// publishedid = 1828439124;\n" + META_OTHER),
            ("id_only_in_a_block_comment", "/* publishedid = 1828439124; */\n" + META_OTHER),
            ("id_inside_another_key", "xpublishedid = 1828439124;\n" + META_OTHER),
            ("id_inside_a_double_quoted_string", 'name = "publishedid = 1828439124";\n' + META_OTHER),
            ("id_inside_a_single_quoted_string", "name = 'publishedid = 1828439124';\n" + META_OTHER),
            ("two_live_ids_correct_first", META_VPP + "publishedid = 1559212036;\n"),
            ("id_with_a_suffix", META_VPP.replace("1828439124;", "1828439124evil;")),
            ("past_the_read_cap", META_VPP + "// " + "x" * cap + "\n"),
        ):
            with self.subTest(label):
                result = self._with_meta(meta)
                self.assertEqual(result.error_code, transaction.VPP_PREFLIGHT_FAILED, label)
                self.assertIn("vpp_mod_identity", result.missing, label)

    def test_meta_cpp_forms_that_still_prove_the_identity(self) -> None:
        # Positive controls: the scanner must not eat the real assignment.
        for label, meta in (
            ("canonical", META_VPP),
            ("no_spaces", META_VPP.replace("publishedid = 1828439124;", "publishedid=1828439124;")),
            ("foreign_id_only_in_a_comment", "// publishedid = 1559212036;\n" + META_VPP),
            ("foreign_id_only_in_a_single_quoted_string", "name = 'publishedid = 1559212036';\n" + META_VPP),
            ("crlf", META_VPP.replace("\n", "\r\n")),
        ):
            with self.subTest(label):
                result = self._with_meta(meta)
                self.assertIsNone(result.error_code, label)

    def test_a_key_inside_a_single_quoted_string_is_dead_ground(self) -> None:
        # R3-F-02: the config language quotes with ' as well as "; the key inside
        # either is text the engine never reads as an assignment. An unterminated
        # quote swallows the rest of the file, and the gate refuses rather than
        # guesses where the engine would have stopped.
        for label, body in (
            ("only_in_single_quotes", "motd = 'vppDisablePassword = 1';\n"),
            ("single_quoted_then_live_zero", "motd = 'vppDisablePassword = 1';\nvppDisablePassword = 0;\n"),
            ("unterminated_single_quote", "motd = 'vppDisablePassword = 1;\nvppDisablePassword = 1;\n"),
        ):
            with self.subTest(label):
                result = self._with_config(body)
                self.assertIn("vpp_disable_password", result.missing, label)

    def test_a_live_key_after_a_single_quoted_string_still_passes(self) -> None:
        for label, body in (
            ("after_a_closed_string", "motd = 'no password tonight';\nvppDisablePassword = 1;\n"),
            ("apostrophe_inside_double_quotes", 'hostname = "Guillermo\'s box";\nvppDisablePassword = 1;\n'),
        ):
            with self.subTest(label):
                result = self._with_config(body)
                self.assertIsNone(result.error_code, label)


class VppPreflightRound5Test(unittest.TestCase):
    """Ronda 5 (delta-3 R4-F-01): comments between assignment tokens are live
    separators, not a reason to drop the assignment from the census."""

    def _result(
        self,
        *,
        policy: dayz_test_request.RequestProjectPolicy | None = None,
        files: FakeFiles | None = None,
        **overrides: object,
    ) -> object:
        selected = policy or _policy()
        return transaction.preflight_vpp_request(
            _raw(selected, **overrides),
            sealed_policies=_sealed(selected),
            files=files if files is not None else _healthy(selected),
        )

    def _with_meta(self, meta: str) -> object:
        policy = _policy()
        files = _healthy(policy)
        files.files[
            ntpath.normcase(
                ntpath.join(policy.mod_roots[0], "@VPPAdminTools", "meta.cpp")
            )
        ] = meta
        return self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

    def _with_config(self, body: str) -> object:
        policy = _policy()
        files = _healthy(policy)
        files.files[
            ntpath.normcase(ntpath.join(policy.dev_root, "_server", "serverDZ.cfg"))
        ] = body
        return self._result(
            policy=policy,
            files=files,
            mode="all",
            extra_mods=["@DayZ_MCP", "@VPPAdminTools"],
        )

    def test_meta_cpp_comments_between_tokens_do_not_hide_a_second_assignment(
        self,
    ) -> None:
        for label, meta in (
            (
                "duplicate_foreign_block_comment",
                "publishedid=1828439124; publishedid/*x*/=1559212036;",
            ),
            (
                "duplicate_foreign_line_comment",
                "publishedid=1828439124; publishedid//x\n=1559212036;",
            ),
            (
                "duplicate_identical_block_comment",
                "publishedid=1828439124; publishedid/*x*/=1828439124;",
            ),
            (
                "single_foreign_comments_around_equal",
                "publishedid/*x*/=/*y*/1559212036;",
            ),
            (
                "canonical_id_only_inside_the_comment_after_equal",
                "publishedid = /* 1828439124 */ 1559212036;",
            ),
        ):
            with self.subTest(label):
                result = self._with_meta(meta)
                self.assertEqual(
                    result.error_code, transaction.VPP_PREFLIGHT_FAILED, label
                )
                self.assertIn("vpp_mod_identity", result.missing, label)

    def test_meta_cpp_comments_next_to_a_single_canonical_value_still_prove_identity(
        self,
    ) -> None:
        for label, meta in (
            ("value_then_adjacent_block_comment", "publishedid=1828439124/*x*/;"),
            ("value_then_spaced_block_comment", "publishedid=1828439124 /*x*/;"),
            ("block_comment_between_key_and_equal", "publishedid/*x*/=1828439124;"),
            (
                "line_comment_between_key_and_equal",
                "publishedid // x\n = 1828439124;",
            ),
            ("semicolon_then_line_comment", "publishedid=1828439124;//tail"),
        ):
            with self.subTest(label):
                result = self._with_meta(meta)
                self.assertIsNone(result.error_code, label)

    def test_cfg_comments_between_tokens_do_not_hide_a_conflicting_assignment(
        self,
    ) -> None:
        for label, body in (
            (
                "conflict_block_comment",
                "vppDisablePassword=1; vppDisablePassword/*x*/=0;",
            ),
            (
                "conflict_line_comment_then_one",
                "vppDisablePassword//x\n=0;\nvppDisablePassword=1;",
            ),
        ):
            with self.subTest(label):
                result = self._with_config(body)
                self.assertEqual(
                    result.error_code, transaction.VPP_PREFLIGHT_FAILED, label
                )
                self.assertIn("vpp_disable_password", result.missing, label)

    def test_cfg_comments_next_to_a_live_one_still_disable_the_password(self) -> None:
        for label, body in (
            ("block_comment_between_key_and_equal", "vppDisablePassword/*x*/=1;"),
            ("value_then_adjacent_block_comment", "vppDisablePassword=1/*x*/;"),
        ):
            with self.subTest(label):
                result = self._with_config(body)
                self.assertIsNone(result.error_code, label)

    def test_contract_rules_pinned_after_the_cross_family_review(self) -> None:
        # Four rules of the ronda-5 contract that the product honoured but no
        # test pinned (review R5-A-02, 2026-09-06): a string is not dead ground
        # between the key and the `=`; a value stops at `//` as it does at `/*`;
        # an unterminated `/*` between tokens runs to the end of the file; an
        # empty value is not an assignment. Plus the delta-2 repro literally,
        # with the `;` INSIDE the single-quoted string.
        for label, meta, expected in (
            ("string_between_key_and_equal", "publishedid/*x*/'='=1828439124;", "vpp_mod_identity"),
            ("value_cut_at_line_comment_without_newline", "publishedid=1828439124//x", None),
            ("second_assignment_with_empty_value", "publishedid=1828439124;publishedid=;", None),
        ):
            with self.subTest(label):
                result = self._with_meta(meta)
                if expected is None:
                    self.assertIsNone(result.error_code, label)
                else:
                    self.assertIn(expected, result.missing, label)
        for label, body in (
            ("unterminated_block_between_equal_and_value", "vppDisablePassword=/*1;"),
            ("delta2_repro_semicolon_inside_the_string", "motd='vppDisablePassword=1;';\n"),
        ):
            with self.subTest(label):
                self.assertIn("vpp_disable_password", self._with_config(body).missing, label)
