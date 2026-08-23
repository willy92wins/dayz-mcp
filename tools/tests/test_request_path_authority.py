from __future__ import annotations

import ctypes
import importlib
import inspect
import json
import os
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

_SYMBOLIC_LINK_FLAG_DIRECTORY = 0x1
_SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE = 0x2


def _make_junction(link: Path, target: Path) -> Path:
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )
    if completed.returncode != 0 or not link.is_junction():
        detail = (completed.stderr or completed.stdout).decode(
            "oem", errors="replace"
        ).strip()
        raise OSError(detail or "mklink /J failed")
    return link


def _remove_junction(link: Path) -> None:
    if link.exists() and link.is_junction():
        link.rmdir()


def _make_directory_symlink(link: Path, target: Path) -> None:
    flags = (
        _SYMBOLIC_LINK_FLAG_DIRECTORY
        | _SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE
    )
    if ctypes.windll.kernel32.CreateSymbolicLinkW(str(link), str(target), flags):
        return
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as error:
        raise OSError(error.winerror) from error


class RequestPathAuthorityTests(unittest.TestCase):
    def _fixture(self, root: Path, *, second_mod_root: bool = False):
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        project = root / "ExampleMod_Suite"
        source = project / "source"
        missions = project / "_server" / "mpmissions"
        custom_mission = missions / "custom.chernarusplus"
        mods = root / "Mods"
        for path in (
            source,
            custom_mission,
            mods / "@CF",
            mods / "@Extra",
            mods / "@Server",
        ):
            path.mkdir(parents=True, exist_ok=True)
        mod_roots = [str(mods)]
        if second_mod_root:
            duplicate = root / "OtherMods"
            (duplicate / "@CF").mkdir(parents=True)
            mod_roots.append(str(duplicate))
        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=str(project),
            default_source=str(source),
            default_base_mods=("@CF",),
            mission_roots=(str(missions),),
            mod_roots=tuple(mod_roots),
        )
        return policy, custom_mission

    def _parse(self, policy, *, mission: Path | None = None, **overrides):
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        document = {
            "version": 1,
            "mod": policy.mod,
            "dev_root": policy.dev_root,
            "preflight": True,
        }
        if mission is not None:
            document["mission"] = str(mission)
        document.update(overrides)
        return request_module.parse_dayz_test_request(
            json.dumps(document).encode("utf-8"), policies=(policy,)
        )

    def test_regular_tree_is_pinned_until_context_exit(self) -> None:
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        with TemporaryDirectory() as temporary:
            policy, mission = self._fixture(Path(temporary))
            sealed = authority._seal_project_policy_for_test(policy)
            parsed = self._parse(
                policy,
                mission=mission,
                build=True,
                source=policy.default_source,
                extra_mods=["@Extra"],
                server_mods=[str(Path(policy.mod_roots[0]) / "@Server")],
            )

            with authority.accredit_request_paths(
                parsed, policies=(sealed,)
            ) as accredited:
                self.assertFalse(accredited.closed)
                self.assertGreater(accredited.handle_count, 0)
                self.assertEqual(
                    set(accredited.identities),
                    {
                        "base_mods",
                        "dev_root",
                        "extra_mods",
                        "mission",
                        "server_mods",
                        "source",
                    },
                )
            self.assertTrue(accredited.closed)

    def test_replaced_root_identity_fails_closed(self) -> None:
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy, _mission = self._fixture(root)
            sealed = authority._seal_project_policy_for_test(policy)
            original = Path(policy.dev_root)
            original.rename(root / "old-project")
            original.mkdir()
            parsed = self._parse(policy)

            with self.assertRaisesRegex(
                ValueError, "invalid_dayz_test_path_authority"
            ):
                authority.accredit_request_paths(parsed, policies=(sealed,))

    def test_relative_mod_must_resolve_under_exactly_one_root(self) -> None:
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        with TemporaryDirectory() as temporary:
            policy, _mission = self._fixture(
                Path(temporary), second_mod_root=True
            )
            sealed = authority._seal_project_policy_for_test(policy)
            parsed = self._parse(policy)

            with self.assertRaisesRegex(
                ValueError, "invalid_dayz_test_path_authority"
            ):
                authority.accredit_request_paths(parsed, policies=(sealed,))

    def test_semantic_policy_cannot_be_mixed_with_foreign_sealed_roots(self) -> None:
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy, _mission = self._fixture(root)
            sealed = authority._seal_project_policy_for_test(policy)
            foreign = request_module.RequestProjectPolicy(
                mod=policy.mod,
                dev_root=str(root / "ForeignProject"),
                default_source=policy.default_source,
                default_base_mods=policy.default_base_mods,
                mission_roots=policy.mission_roots,
                mod_roots=policy.mod_roots,
            )
            mixed = replace(sealed, policy=foreign)
            parsed = self._parse(policy)

            with self.assertRaisesRegex(
                ValueError, "invalid_dayz_test_path_authority"
            ):
                authority.accredit_request_paths(parsed, policies=(mixed,))

    def test_nested_directory_symlink_is_not_an_approved_root_junction(self) -> None:
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy, _mission = self._fixture(root)
            target = root / "real-linked-mod"
            target.mkdir()
            linked = Path(policy.mod_roots[0]) / "@Linked"
            try:
                os.symlink(target, linked, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symlink unavailable: {error.winerror}")
            sealed = authority._seal_project_policy_for_test(policy)
            parsed = self._parse(policy, extra_mods=["@Linked"])

            with self.assertRaisesRegex(
                ValueError, "invalid_dayz_test_path_authority"
            ):
                authority.accredit_request_paths(parsed, policies=(sealed,))

    def test_host_p_mods_junction_requires_and_preserves_exact_mount_point(self) -> None:
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        junction = Path(r"P:\Mods")
        if not junction.exists() or not junction.is_junction():
            self.skipTest(r"P:\Mods exact junction is unavailable")

        with self.assertRaisesRegex(
            ValueError, "invalid_dayz_test_path_authority"
        ):
            authority._capture_root_for_test(
                str(junction), allow_root_junction=False
            )
        sealed = authority._capture_root_for_test(
            str(junction), allow_root_junction=True
        )
        self.assertEqual(sealed.root_reparse_tag, 0xA0000003)
        self.assertNotEqual(
            os.path.normcase(sealed.path), os.path.normcase(sealed.resolved_path)
        )
        opened = authority._open_sealed_root(sealed)
        try:
            self.assertGreater(len(opened), 0)
            self.assertTrue(all(item.handle for item in opened))
        finally:
            for item in reversed(opened):
                item.close()

    def test_module_is_read_only_and_not_launch_capable(self) -> None:
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        source = inspect.getsource(authority)
        for forbidden in (
            "secure_launcher",
            "native_launcher_backend",
            "subprocess",
            "CreateProcess",
            "ShellExecute",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_leaf_mount_point_under_allowed_root_accredits_destination_identity(
        self,
    ) -> None:
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy, _mission = self._fixture(root)
            mods = Path(policy.mod_roots[0])
            real_mods = root / "real-mods"
            dest = root / "workshop-dest"
            leaf = mods / "@WorkshopMod"
            mods.rename(real_mods)
            try:
                _make_junction(mods, real_mods)
                dest.mkdir()
                _make_junction(leaf, dest)
                sealed = authority._seal_project_policy_for_test(
                    policy, allow_mod_root_junctions=(policy.mod_roots[0],)
                )
                parsed = self._parse(policy, extra_mods=["@WorkshopMod"])
                dest_identity = authority._capture_root_for_test(
                    str(dest), allow_root_junction=False
                ).resolved_identity
                unfollowed = authority._open_directory(
                    str(leaf), follow_root_reparse=False
                )
                try:
                    with authority.accredit_request_paths(
                        parsed, policies=(sealed,)
                    ) as accredited:
                        self.assertEqual(
                            accredited.identities["extra_mods"],
                            (dest_identity,),
                        )
                        self.assertNotEqual(
                            accredited.identities["extra_mods"][0],
                            unfollowed.identity,
                        )
                finally:
                    unfollowed.close()
            finally:
                _remove_junction(leaf)
                _remove_junction(mods)

    def test_leaf_mount_point_under_disallowed_root_is_rejected(self) -> None:
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy, _mission = self._fixture(root)
            dest = root / "workshop-dest"
            dest.mkdir()
            leaf = Path(policy.mod_roots[0]) / "@WorkshopMod"
            try:
                _make_junction(leaf, dest)
                sealed = authority._seal_project_policy_for_test(policy)
                parsed = self._parse(policy, extra_mods=["@WorkshopMod"])
                with self.assertRaisesRegex(
                    ValueError, "invalid_dayz_test_path_authority"
                ):
                    authority.accredit_request_paths(parsed, policies=(sealed,))
            finally:
                _remove_junction(leaf)

    def test_leaf_directory_symlink_is_rejected(self) -> None:
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy, _mission = self._fixture(root)
            mods = Path(policy.mod_roots[0])
            real_mods = root / "real-mods"
            target = root / "real-linked-mod"
            leaf = mods / "@Linked"
            mods.rename(real_mods)
            try:
                _make_junction(mods, real_mods)
                target.mkdir()
                try:
                    _make_directory_symlink(leaf, target)
                except OSError as error:
                    self.skipTest(f"directory symlink unavailable: {error}")
                sealed = authority._seal_project_policy_for_test(
                    policy, allow_mod_root_junctions=(policy.mod_roots[0],)
                )
                parsed = self._parse(policy, extra_mods=["@Linked"])
                with self.assertRaisesRegex(
                    ValueError, "invalid_dayz_test_path_authority"
                ):
                    authority.accredit_request_paths(parsed, policies=(sealed,))
            finally:
                if leaf.exists() or leaf.is_symlink():
                    leaf.unlink()
                _remove_junction(mods)

    def test_intermediate_reparse_point_is_rejected(self) -> None:
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy, _mission = self._fixture(root)
            mid_real = root / "mid-real"
            leaf_dir = mid_real / "@Leaf"
            leaf_dir.mkdir(parents=True)
            mid = Path(policy.mod_roots[0]) / "mid"
            try:
                _make_junction(mid, mid_real)
                sealed = authority._seal_project_policy_for_test(policy)
                parsed = self._parse(
                    policy,
                    extra_mods=[str(mid / "@Leaf")],
                )
                with self.assertRaisesRegex(
                    ValueError, "invalid_dayz_test_path_authority"
                ):
                    authority.accredit_request_paths(parsed, policies=(sealed,))
            finally:
                _remove_junction(mid)

    def test_mcp_absolute_mod_outside_mod_roots_is_rejected(self) -> None:
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        tool = importlib.import_module("dayz_mcp.dayz_test_tool")
        with TemporaryDirectory() as temporary:
            policy, _mission = self._fixture(Path(temporary))
            sealed = authority._seal_project_policy_for_test(policy)
            with self.assertRaisesRegex(tool.DayzTestToolError, "bad_mod"):
                tool.build_run_request(
                    (sealed,),
                    project=policy.mod,
                    mode="offline",
                    extra_mods=[r"C:\Users\Public\@Outside"],
                )

    def test_mcp_absolute_mod_inside_mod_roots_is_accepted(self) -> None:
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        tool = importlib.import_module("dayz_mcp.dayz_test_tool")
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        with TemporaryDirectory() as temporary:
            policy, _mission = self._fixture(Path(temporary))
            sealed = authority._seal_project_policy_for_test(policy)
            absolute = str(Path(policy.mod_roots[0]) / "@Extra")
            raw, _selected = tool.build_run_request(
                (sealed,),
                project=policy.mod,
                mode="offline",
                extra_mods=[absolute, "@DayZ_MCP"],
            )
            parsed = request_module.parse_dayz_test_request(
                raw, policies=(policy,)
            )
            self.assertEqual(
                parsed.payload["extra_mods"], [absolute, "@DayZ_MCP"]
            )


if __name__ == "__main__":
    unittest.main()
