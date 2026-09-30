from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import build_native_launcher
from dayz_mcp import (
    authenticode,
    dayz_tools_paths,
    launcher_registry,
    native_bundle,
    native_child_announcement,
)
from dayz_mcp.dayz_tools_paths import addon_helper_exes
from dayz_mcp.native_broker_protocol import BrokerKind
from dayz_mcp.native_child_announcement import ChildAnnouncement, ChildAnnouncementDecoder
from dayz_mcp.request_path_authority import PathIdentity
from tests._bundle_paths import (
    requires_built_bundle,
    requires_closure_manifest,
    requires_installed_steam,
)


TOOLS_DIR = Path(__file__).resolve().parents[1]
BUNDLE_DIR = TOOLS_DIR / "native-launchers" / "dayz-test-v1"


class NativeBundleTest(unittest.TestCase):
    def test_loader_never_reads_the_private_launcher_stream_directly(self) -> None:
        source = Path(native_bundle.__file__).read_text(encoding="utf-8")
        self.assertNotIn("opened_launcher._stream", source)

    @requires_built_bundle
    def test_real_bundle_is_pinned_and_yields_the_sealed_policy(self) -> None:
        entry = launcher_registry._create_registry_entry_for_test(
            "dayz-test-v1", BUNDLE_DIR, "dayz-test-launcher.exe"
        )
        with launcher_registry._open_registry_entry_for_test(entry) as opened:
            opened.validate_native_pe()
            with native_bundle.load_verified_bundle(opened) as verified:
                self.assertTrue(1 <= len(verified.sealed_policies) <= 128)
                self.assertEqual(
                    {
                        descriptor.final_path.casefold()
                        for descriptor in (
                            verified.debug_image_authority.addon_helper_descriptors
                        )
                    },
                    {
                        str(Path(path).resolve(strict=True)).casefold()
                        for path in (
                            *addon_helper_exes(),
                        )
                    },
                )
                for item in verified.sealed_policies:
                    self.assertTrue(item.policy.mod)
                    self.assertTrue(item.policy.dev_root)
                    self.assertTrue(item.policy.mod_roots)
                self.assertTrue(verified._streams)
                held_streams = tuple(verified._streams)
                self.assertTrue(all(not stream.closed for stream in held_streams))
            self.assertTrue(all(stream.closed for stream in held_streams))

    def test_canonical_json_rejects_duplicates_noncanonical_and_constants(self) -> None:
        valid = b'{"format_version":1,"projects":[]}\n'
        self.assertEqual(
            native_bundle._canonical_json(valid, maximum=1024),
            {"format_version": 1, "projects": []},
        )
        invalid = (
            b'{"format_version":1,"format_version":1,"projects":[]}\n',
            b'{ "format_version":1,"projects":[] }\n',
            b'{"format_version":NaN,"projects":[]}\n',
            b"\xef\xbb\xbf" + valid,
        )
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaisesRegex(
                ValueError, "invalid_native_launcher_bundle"
            ):
                native_bundle._canonical_json(raw, maximum=1024)

    @requires_closure_manifest
    def test_manifest_schema_rejects_unknown_external_and_lowercase_hash(self) -> None:
        manifest = json.loads(
            (BUNDLE_DIR / "closure-manifest.json").read_text(encoding="utf-8")
        )
        bad_external = json.loads(json.dumps(manifest))
        external = next(
            item for item in bad_external["entries"] if item["kind"] == "external"
        )
        external["path"] = r"C:\Windows\System32\cmd.exe"
        with self.assertRaisesRegex(ValueError, "invalid_native_launcher_bundle"):
            native_bundle._parse_manifest(bad_external)

        bad_hash = json.loads(json.dumps(manifest))
        bad_hash["request_policy_sha256"] = bad_hash[
            "request_policy_sha256"
        ].lower()
        with self.assertRaisesRegex(ValueError, "invalid_native_launcher_bundle"):
            native_bundle._parse_manifest(bad_hash)

    def test_debug_image_authority_uses_pinned_identity_or_exact_system_directory(self) -> None:
        executable = PathIdentity(1, "01" * 16)
        pinned_dll = PathIdentity(1, "02" * 16)
        unknown = PathIdentity(1, "03" * 16)
        authority = native_bundle.DebugImageAuthority(
            process_identities=frozenset({executable}),
            module_identities=frozenset({pinned_dll}),
            system_directory=r"C:\Windows\System32",
        )
        identities = {
            11: executable,
            12: pinned_dll,
            13: unknown,
            14: unknown,
            15: unknown,
            16: unknown,
            17: unknown,
            18: unknown,
            19: unknown,
            20: unknown,
            21: unknown,
            22: unknown,
            23: unknown,
            24: unknown,
            25: unknown,
            26: unknown,
            27: unknown,
            28: unknown,
            29: unknown,
        }
        paths = {
            11: r"C:\bundle\python.exe",
            12: r"C:\bundle\python314.dll",
            13: r"C:\Windows\System32\kernel32.dll",
            14: r"C:\Windows\System32-evil\kernel32.dll",
            15: r"C:\Windows\SysWOW64\ntdll.dll",
            16: r"C:\Windows\SysWOW64-evil\ntdll.dll",
            17: r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\mscoreei.dll",
            18: r"C:\Windows\Microsoft.NET\Framework-evil\v4.0.30319\mscoreei.dll",
            19: (
                r"C:\Windows\assembly\NativeImages_v4.0.30319_32\mscorlib"
                r"\hash\mscorlib.ni.dll"
            ),
            20: (
                r"C:\Windows\assembly\NativeImages_v4.0.30319_32-evil\mscorlib"
                r"\hash\mscorlib.ni.dll"
            ),
            21: (
                r"C:\Windows\WinSxS\x86_microsoft.windows.common-controls_"
                r"6595b64144ccf1df_5.82.22621.5983_none_fbec26ba7805fa7d\comctl32.dll"
            ),
            22: (
                r"C:\Windows\WinSxS-evil\x86_microsoft.windows.common-controls_"
                r"6595b64144ccf1df_5.82.22621.5983_none_fbec26ba7805fa7d\comctl32.dll"
            ),
            23: (
                r"C:\Windows\WinSxS\x86_microsoft.windows.other-assembly_"
                r"6595b64144ccf1df_5.82.22621.5983_none_fbec26ba7805fa7d\comctl32.dll"
            ),
            24: (
                r"C:\Windows\WinSxS\x86_microsoft.windows.common-controls_"
                r"6595b64144ccf1df_5.82.22621.5983_none_fbec26ba7805fa7d\other.dll"
            ),
            25: (
                r"C:\Windows\Microsoft.NET\assembly\GAC_MSIL\Microsoft.VisualBasic"
                r"\v4.0_10.0.0.0__b03f5f7f11d50a3a\Microsoft.VisualBasic.dll"
            ),
            26: (
                r"C:\Windows\Microsoft.NET\assembly\GAC_MSIL-evil\Microsoft.VisualBasic"
                r"\v4.0_10.0.0.0__b03f5f7f11d50a3a\Microsoft.VisualBasic.dll"
            ),
            27: (
                r"C:\Windows\Microsoft.NET\assembly\GAC_MSIL\Other.Assembly"
                r"\v4.0_10.0.0.0__b03f5f7f11d50a3a\Microsoft.VisualBasic.dll"
            ),
            28: (
                r"C:\Windows\Microsoft.NET\assembly\GAC_MSIL\Microsoft.VisualBasic"
                r"\v4.0_10.0.0.1__b03f5f7f11d50a3a\Microsoft.VisualBasic.dll"
            ),
            29: (
                r"C:\Windows\WinSxS\amd64_microsoft.windows.common-controls_"
                r"6595b64144ccf1df_6.0.22621.6060_none_deadbeef\comctl32.dll"
            ),
        }
        with patch.object(
            native_bundle, "_file_identity", side_effect=lambda handle: identities[handle]
        ), patch.object(
            native_bundle, "_final_handle_path", side_effect=lambda handle: paths[handle]
        ):
            self.assertTrue(authority.approve_debug_image(11, event_kind="CREATE_PROCESS"))
            self.assertTrue(authority.approve_debug_image(12, event_kind="LOAD_DLL"))
            self.assertTrue(authority.approve_debug_image(13, event_kind="LOAD_DLL"))
            self.assertFalse(authority.approve_debug_image(14, event_kind="LOAD_DLL"))
            self.assertTrue(authority.approve_debug_image(15, event_kind="LOAD_DLL"))
            self.assertFalse(authority.approve_debug_image(16, event_kind="LOAD_DLL"))
            self.assertTrue(authority.approve_debug_image(17, event_kind="LOAD_DLL"))
            self.assertFalse(authority.approve_debug_image(18, event_kind="LOAD_DLL"))
            self.assertTrue(authority.approve_debug_image(19, event_kind="LOAD_DLL"))
            self.assertFalse(authority.approve_debug_image(20, event_kind="LOAD_DLL"))
            self.assertTrue(authority.approve_debug_image(21, event_kind="LOAD_DLL"))
            self.assertFalse(authority.approve_debug_image(22, event_kind="LOAD_DLL"))
            self.assertFalse(authority.approve_debug_image(23, event_kind="LOAD_DLL"))
            self.assertFalse(authority.approve_debug_image(24, event_kind="LOAD_DLL"))
            self.assertTrue(authority.approve_debug_image(25, event_kind="LOAD_DLL"))
            self.assertFalse(authority.approve_debug_image(26, event_kind="LOAD_DLL"))
            self.assertFalse(authority.approve_debug_image(27, event_kind="LOAD_DLL"))
            self.assertFalse(authority.approve_debug_image(28, event_kind="LOAD_DLL"))
            self.assertFalse(authority.approve_debug_image(29, event_kind="LOAD_DLL"))
            self.assertFalse(authority.approve_debug_image(11, event_kind="LOAD_DLL"))
            self.assertFalse(authority.approve_debug_image(12, event_kind="CREATE_PROCESS"))
            self.assertFalse(authority.approve_debug_image(11, event_kind="UNKNOWN"))

    def test_debug_image_authority_correlates_announcement_to_the_same_open_file(self) -> None:
        identity = PathIdentity(7, bytes(range(16)).hex().upper())
        descriptor = native_bundle.DebugProcessDescriptor(
            kind=BrokerKind.PRIVATE_WORKER,
            announced_path=r"runtime\python.exe",
            final_path=r"C:\bundle\runtime\python.exe",
            image_sha256="53" * 32,
            identity=identity,
        )
        authority = native_bundle.DebugImageAuthority(
            process_identities=frozenset({identity}),
            module_identities=frozenset(),
            system_directory=r"C:\Windows\System32",
            process_descriptors=(descriptor,),
        )
        announcement = ChildAnnouncement(
            sequence=1,
            kind=BrokerKind.PRIVATE_WORKER,
            announced_path=r"runtime\python.exe",
            image_sha256="53" * 32,
            identity=identity,
        )
        with patch.object(native_bundle, "_file_identity", return_value=identity), patch.object(
            native_bundle,
            "_final_handle_path",
            return_value=r"C:\bundle\runtime\python.exe",
        ):
            self.assertTrue(authority.approve_announced_process(11, announcement))
            self.assertFalse(
                authority.approve_announced_process(
                    11,
                    replace(announcement, image_sha256="41" * 32),
                )
            )
            self.assertFalse(
                authority.approve_announced_process(
                    11,
                    replace(announcement, kind=BrokerKind.LIFECYCLE_CLI),
                )
            )

    def test_addon_helper_authority_requires_exact_pinned_identity_and_path(self) -> None:
        identity = PathIdentity(9, "09" * 16)
        descriptor = native_bundle.DebugAddonHelperDescriptor(
            final_path=r"C:\DayZ Tools\Bin\Binarize\binarize.exe",
            identity=identity,
        )
        authority = native_bundle.DebugImageAuthority(
            process_identities=frozenset(),
            module_identities=frozenset(),
            system_directory=r"C:\Windows\System32",
            addon_helper_descriptors=(descriptor,),
        )
        identities = {11: identity, 12: PathIdentity(9, "12" * 16)}
        paths = {
            11: descriptor.final_path,
            12: descriptor.final_path,
        }
        with patch.object(
            native_bundle, "_file_identity", side_effect=lambda handle: identities[handle]
        ), patch.object(
            native_bundle, "_final_handle_path", side_effect=lambda handle: paths[handle]
        ):
            self.assertTrue(authority.approve_addon_helper_process(11))
            self.assertFalse(authority.approve_addon_helper_process(12))
            paths[11] = r"C:\other\binarize.exe"
            self.assertFalse(authority.approve_addon_helper_process(11))



class Fb19b5ToolsLayoutTest(unittest.TestCase):
    """#93 P3A: DayZ Tools off C: without DAYZ_TOOLS_PATH, and the registry's spelling."""

    F_TOOLS = Path(r"F:\SteamLibrary\steamapps\common\DayZ Tools")

    def test_external_paths_check_matches_the_builder_resolution(self) -> None:
        tools = self.F_TOOLS
        present = {
            ntpath.normcase(str(path))
            for path in (
                tools.joinpath("Bin", "AddonBuilder", "AddonBuilder.exe"),
                tools.parents[2] / "steamclient.dll",
                tools.parent / "DayZ" / "DayZDiag_x64.exe",
            )
        }

        def registry(hive: str, subkey: str, value: str) -> str | None:
            if (hive, subkey, value) == (
                "HKEY_CURRENT_USER", r"Software\Bohemia Interactive\DayZ Tools", "path"
            ):
                return str(tools)
            return None

        environ = {key: value for key, value in os.environ.items() if key != "DAYZ_TOOLS_PATH"}
        with patch.dict(os.environ, environ, clear=True), patch.object(
            dayz_tools_paths, "read_registry_string", side_effect=registry
        ), patch.object(
            Path, "is_file", autospec=True, side_effect=lambda path: ntpath.normcase(str(path)) in present
        ):
            builder = {ntpath.normcase(str(path)) for path in build_native_launcher.external_files()}
            verifier = native_bundle._external_paths()
        self.assertEqual(verifier, frozenset(builder))
        self.assertIn(
            ntpath.normcase(str(tools.joinpath("Bin", "AddonBuilder", "AddonBuilder.exe"))),
            verifier,
        )

    def test_announced_addon_builder_path_ignores_the_manifest_spelling(self) -> None:
        # The manifest keeps the registry's spelling, which can differ in case from
        # the announcement. Approval compares the two with ntpath.normcase.
        manifest_path = (
            r"c:\program files (x86)\steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\AddonBuilder.exe"
        )
        broker_path = (
            r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\AddonBuilder.exe"
        )
        identity = PathIdentity(3, "0A" * 16)
        descriptor = native_bundle.DebugProcessDescriptor(
            kind=BrokerKind.ADDON_BUILDER,
            announced_path=manifest_path,
            final_path=broker_path,
            image_sha256="AB" * 32,
            identity=identity,
        )
        authority = native_bundle.DebugImageAuthority(
            process_identities=frozenset({identity}),
            module_identities=frozenset(),
            system_directory=r"C:\Windows\System32",
            process_descriptors=(descriptor,),
        )
        announcement = ChildAnnouncement(
            sequence=1,
            kind=BrokerKind.ADDON_BUILDER,
            announced_path=broker_path,
            image_sha256="AB" * 32,
            identity=identity,
        )
        with patch.object(native_bundle, "_file_identity", return_value=identity), patch.object(
            native_bundle, "_final_handle_path", return_value=broker_path
        ):
            self.assertTrue(authority.approve_announced_process(11, announcement))
            self.assertFalse(
                authority.approve_announced_process(
                    11, replace(announcement, announced_path=r"C:\Other\AddonBuilder.exe")
                )
            )

    def _broker_frame(self, path: str, image_sha256: str, identity: PathIdentity) -> bytes:
        raw = path.encode("utf-8")
        return native_child_announcement._HEADER.pack(
            native_child_announcement._MAGIC,
            native_child_announcement._VERSION,
            int(BrokerKind.ADDON_BUILDER),
            0,
            1,
            len(raw),
            bytes.fromhex(image_sha256),
            identity.volume_serial_number,
            bytes.fromhex(identity.file_id),
        ) + raw

    def _sealed_tools(self, tools: Path | None, *, environ_tools: str | None = None):
        present: set[str] = set()
        if tools is not None:
            present.add(
                ntpath.normcase(str(tools.joinpath(*dayz_tools_paths.ADDON_BUILDER_RELATIVE)))
            )
            present.add(ntpath.normcase(str(tools.parents[2] / "steamclient.dll")))
            present.add(
                ntpath.normcase(str(tools.parent / "DayZ" / dayz_tools_paths.DIAG_NAME))
            )

        def registry(hive: str, subkey: str, value: str) -> str | None:
            if tools is not None and (hive, subkey, value) == (
                "HKEY_CURRENT_USER",
                r"Software\Bohemia Interactive\DayZ Tools",
                "path",
            ):
                return str(tools)
            return None

        environ = {key: value for key, value in os.environ.items() if key != "DAYZ_TOOLS_PATH"}
        if environ_tools is not None:
            environ["DAYZ_TOOLS_PATH"] = environ_tools
        return (
            patch.dict(os.environ, environ, clear=True),
            patch.object(dayz_tools_paths, "read_registry_string", side_effect=registry),
            patch.object(
                Path,
                "is_file",
                autospec=True,
                side_effect=lambda path: ntpath.normcase(str(path)) in present,
            ),
        )

    def test_broker_paths_follow_the_resolved_layout(self) -> None:
        tools = self.F_TOOLS
        resolved = str(tools.joinpath(*dayz_tools_paths.ADDON_BUILDER_RELATIVE))
        helpers = frozenset(
            ntpath.normcase(str(tools.joinpath(*parts)))
            for parts in dayz_tools_paths.ADDON_HELPER_RELATIVE
        )
        default = dayz_tools_paths.addon_builder_exe(environ={})
        for label, environ_tools in (
            ("registry", None),
            ("stale_env_falls_through", r"E:\missing\DayZ Tools"),
        ):
            with self.subTest(layout=label):
                env_patch, registry_patch, file_patch = self._sealed_tools(
                    tools, environ_tools=environ_tools
                )
                with env_patch, registry_patch, file_patch:
                    self.assertEqual(ntpath.normcase(native_bundle._addon_builder_path()), ntpath.normcase(resolved))
                    self.assertNotEqual(ntpath.normcase(native_bundle._addon_builder_path()), ntpath.normcase(default))
                    self.assertEqual(native_bundle._addon_helper_paths(), helpers)
                    announcement = ChildAnnouncementDecoder(addon_builder_path=resolved).feed(
                        self._broker_frame(resolved, "CD" * 32, PathIdentity(5, "0B" * 16))
                    )[0]
                    self.assertEqual(announcement.announced_path, resolved)
                    folded = "".join(
                        character.lower() if character.isupper() else character.upper()
                        for character in resolved
                    )
                    folded_announcement = ChildAnnouncementDecoder(addon_builder_path=resolved).feed(
                        self._broker_frame(folded, "CD" * 32, PathIdentity(5, "0B" * 16))
                    )[0]
                    self.assertEqual(folded_announcement.announced_path, folded)
                    with self.assertRaisesRegex(ValueError, "invalid_native_child_announcement"):
                        ChildAnnouncementDecoder(addon_builder_path=resolved).feed(
                            self._broker_frame(default, "CD" * 32, PathIdentity(5, "0B" * 16))
                        )

    def test_default_c_layout_paths_when_nothing_resolves(self) -> None:
        default = dayz_tools_paths.addon_builder_exe(environ={})
        helpers = frozenset(
            ntpath.normcase(str(dayz_tools_paths.DEFAULT_TOOLS_ROOT.joinpath(*parts)))
            for parts in dayz_tools_paths.ADDON_HELPER_RELATIVE
        )
        env_patch, registry_patch, file_patch = self._sealed_tools(None)
        with env_patch, registry_patch, file_patch:
            self.assertEqual(native_bundle._addon_builder_path(), default)
            self.assertEqual(native_bundle._addon_helper_paths(), helpers)
            announcement = ChildAnnouncementDecoder(addon_builder_path=default).feed(
                self._broker_frame(default.lower(), "CD" * 32, PathIdentity(5, "0B" * 16))
            )[0]
            self.assertEqual(ntpath.normcase(announcement.announced_path), ntpath.normcase(default))

    def test_the_cpp_addon_builder_path_comes_from_the_sealed_closure(self) -> None:
        source = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(encoding="utf-8")
        self.assertNotIn("Program Files (x86)", source)
        literals = re.findall(r'L"([^"]*AddonBuilder\.exe)"', source)
        self.assertEqual(literals, [r"\\Bin\\AddonBuilder\\AddonBuilder.exe"])
        lookup = source[
            source.index("const ClosureEntry* SealedAddonBuilderEntry") : source.index("bool BuildAddonCommand")
        ]
        self.assertIn("kClosureEntries[index]", lookup)
        self.assertIn("ClosureKind::EXTERNAL", lookup)
        self.assertIn(r'L"\\Bin\\AddonBuilder\\AddonBuilder.exe"', lookup)
        self.assertIn("if (found != nullptr) return nullptr;", lookup)
        self.assertIn("return found;", lookup)
        command = source[source.index("bool BuildAddonCommand") : source.index("bool BuildPboPath")]
        self.assertNotIn('L"C:', command)
        self.assertIn("const wchar_t* addon", command)
        launch = source[
            source.index("BOOL LaunchApprovedChild") : source.index(
                'extern "C" void __cdecl wWinMainCRTStartup'
            )
        ]
        self.assertIn("const ClosureEntry* addon_entry = SealedAddonBuilderEntry();", launch)
        self.assertIn("if (addon_entry == nullptr) return FALSE;", launch)
        self.assertLess(launch.index("addon_entry == nullptr"), launch.index("manifest_path = addon_entry->path"))
        self.assertLess(
            launch.index("manifest_path = addon_entry->path"),
            launch.index("BuildAddonCommand(addon, manifest_path,"),
        )
        self.assertLess(
            launch.index("BuildAddonCommand(addon, manifest_path,"),
            launch.index("PublishAnnouncement(kind, manifest_path)"),
        )

    @requires_built_bundle
    def test_built_bundle_consumer_chain_accepts_the_broker_under_each_variable(self) -> None:
        broker = dayz_tools_paths.addon_builder_exe(environ={})
        if not Path(broker).is_file():
            self.skipTest("DayZ Tools are not at the broker's fixed path on this host")
        entry = launcher_registry._create_registry_entry_for_test(
            "dayz-test-v1", BUNDLE_DIR, "dayz-test-launcher.exe"
        )
        clean = {key: value for key, value in os.environ.items() if key != "DAYZ_TOOLS_PATH"}
        for label, value in (
            ("unset", None),
            ("stale", r"E:\missing\DayZ Tools"),
            ("lowercase_default", str(dayz_tools_paths.DEFAULT_TOOLS_ROOT).lower()),
        ):
            env = dict(clean)
            if value is not None:
                env["DAYZ_TOOLS_PATH"] = value
            with self.subTest(dayz_tools_path=label), patch.dict(os.environ, env, clear=True):
                with launcher_registry._open_registry_entry_for_test(entry) as opened:
                    with native_bundle.load_verified_bundle(opened) as verified:
                        authority = verified.debug_image_authority
                        builders = [
                            descriptor
                            for descriptor in authority.process_descriptors
                            if descriptor.kind is BrokerKind.ADDON_BUILDER
                        ]
                        self.assertEqual(len(builders), 1)
                        self.assertEqual(len(authority.addon_helper_descriptors), 3)
                        announcement = ChildAnnouncementDecoder(
                            addon_builder_path=builders[0].announced_path
                        ).feed(
                            self._broker_frame(
                                broker, builders[0].image_sha256, builders[0].identity
                            )
                        )[0]
                        import msvcrt

                        with launcher_registry._open_pinned_read(Path(broker)) as stream:
                            handle = msvcrt.get_osfhandle(stream.fileno())
                            self.assertTrue(
                                authority.approve_announced_process(handle, announcement)
                            )


def _canonical_json(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _sealed_root(path: str) -> dict[str, object]:
    identity = {"file_id": "AB" * 16, "volume_serial_number": 1}
    return {
        "allow_root_junction": False,
        "handle_path": path,
        "identity": identity,
        "path": path,
        "resolved_identity": identity,
        "resolved_path": path,
        "root_reparse_tag": 0,
    }


def _write_loadable_bundle(root: Path, external_dir: Path) -> tuple[Path, ...]:
    """A bundle load_verified_bundle accepts, sealed against this tree's sources.

    External closure entries are fixture files under external_dir, not the
    machine's DayZ Tools. The caller patches native_bundle._external_paths to
    those paths for the duration of the load.
    """
    package = Path(native_bundle.__file__).resolve().parent
    members = {
        "__main__.py": b"\n",
        "dayz_mcp/__init__.py": (package / "__init__.py").read_bytes(),
    }
    for name in native_bundle._APP_PACKAGED_MODULES:
        members[f"dayz_mcp/{name}"] = (package / name).read_bytes()
    if set(members) != set(native_bundle._APP_MEMBERS):
        raise AssertionError("app.pyz members drifted from _APP_MEMBERS")
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir()
    app_main = b"app-main\n"
    launcher_source = b"launcher\n"
    (root / "src" / "app_main.py").write_bytes(app_main)
    (root / "src" / "launcher.cpp").write_bytes(launcher_source)
    policy = _canonical_json(
        {
            "format_version": 1,
            "projects": [
                {
                    "default_base_mods": [],
                    "default_source": _sealed_root(r"C:\Windows"),
                    "dev_root": _sealed_root(r"C:\Windows"),
                    "mission_roots": [_sealed_root(r"C:\Windows")],
                    "mod": "Example",
                    "mod_roots": [_sealed_root(r"C:\Windows")],
                }
            ],
        }
    )
    worker_runtime = _canonical_json({"format_version": 1})
    (root / "request-policy.json").write_bytes(policy)
    (root / "worker-runtime.json").write_bytes(worker_runtime)
    archive_path = root / "app.pyz"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, members[name])
    build_contract = _canonical_json(
        {
            "builder_sha256": "11" * 32,
            "dependency_lock_sha256": "22" * 32,
            "format_version": 1,
            "sources": {
                "app_main.py": _sha256(app_main),
                "launcher.cpp": _sha256(launcher_source),
            },
        }
    )
    (root / "build-contract.json").write_bytes(build_contract)
    bundle_files = {
        "app.pyz": archive_path.read_bytes(),
        "build-contract.json": build_contract,
        "request-policy.json": policy,
        "src/app_main.py": app_main,
        "src/launcher.cpp": launcher_source,
        "worker-runtime.json": worker_runtime,
    }
    entries: list[dict[str, object]] = []
    for relative, raw in bundle_files.items():
        entries.append(
            {
                "kind": "bundle",
                "path": relative,
                "sha256": _sha256(raw),
                "size": len(raw),
            }
        )
    external_dir.mkdir(parents=True, exist_ok=True)
    externals: list[Path] = []
    for name in ("fixture.exe", "fixture.dll", "fixture.txt"):
        written = external_dir / name
        written.write_bytes(b"sealed-external:" + name.encode("ascii"))
        externals.append(Path(ntpath.normpath(str(written.resolve(strict=True)))))
    for external in externals:
        info = external.stat()
        entries.append(
            {
                "identity": {
                    "file_id": native_bundle._manifest_file_id(info),
                    "volume_serial_number": int(info.st_dev),
                },
                "kind": "external",
                "path": ntpath.normpath(str(external)),
                "sha256": _sha256(external.read_bytes()),
                "size": int(info.st_size),
            }
        )
    entries.sort(key=lambda item: (str(item["kind"]), str(item["path"]).casefold()))
    source_hashes = {
        key: _sha256((package / module).read_bytes())
        for key, module in native_bundle._HASHED_MODULES.items()
    }
    manifest = _canonical_json(
        {
            "bundle_id": "dayz-test-v1",
            "entries": entries,
            "format_version": 1,
            "request_policy_sha256": _sha256(policy),
            "worker_runtime_sha256": _sha256(worker_runtime),
            **source_hashes,
        }
    )
    (root / "closure-manifest.json").write_bytes(manifest)
    marker = b"DAYZ_MCP_MANIFEST_SHA256=" + _sha256(manifest).encode("ascii")
    executable = b"MZ" + marker
    (root / "dayz-test-launcher.exe").write_bytes(executable)
    fingerprint = {
        "app_pyz_sha256": _sha256(bundle_files["app.pyz"]),
        "manifest_sha256": _sha256(manifest),
        "pe_sha256": _sha256(executable),
        "request_policy_sha256": _sha256(policy),
    }
    receipt = _canonical_json(
        {
            "build_contract_sha256": _sha256(build_contract),
            "builds": [
                {"mode": mode, **fingerprint} for mode in native_bundle._REPRODUCIBILITY_MODES
            ],
            "format_version": 2,
            "reproducible": True,
        }
    )
    (root / "reproducibility.json").write_bytes(receipt)
    return tuple(externals)


class AddonTreeSteamDllTest(unittest.TestCase):
    _STEAM = r"C:\Program Files (x86)\Steam"
    _NAMES = frozenset({"steamclient.dll", "tier0_s.dll"})
    _INSTALL_IDENTITY = PathIdentity(4, "CD" * 16)
    _FILE_IDENTITY = PathIdentity(1, "AB" * 16)

    def _authority(self, **overrides: object) -> native_bundle.DebugImageAuthority:
        fields: dict[str, object] = {
            "process_identities": frozenset(),
            "module_identities": frozenset(),
            "system_directory": r"C:\Windows\System32",
            "steam_install_directory": self._STEAM,
            "steam_client_dll_names": self._NAMES,
            "steam_install_identity": self._INSTALL_IDENTITY,
        }
        fields.update(overrides)
        return native_bundle.DebugImageAuthority(**fields)

    def _approve(
        self,
        authority: native_bundle.DebugImageAuthority,
        path: str,
        *,
        signed: bool = True,
        after_identity: PathIdentity | None = None,
        directory_identity: PathIdentity | None = None,
    ) -> bool:
        file_identity = self._FILE_IDENTITY
        with patch.object(
            native_bundle,
            "_file_identity",
            side_effect=[file_identity, after_identity or file_identity],
        ), patch.object(
            native_bundle, "_final_handle_path", return_value=path
        ), patch.object(
            native_bundle,
            "_directory_identity",
            return_value=(
                authority.steam_install_identity
                if directory_identity is None
                else directory_identity
            ),
        ), patch.object(
            native_bundle, "_path_identity", return_value=file_identity
        ), patch.object(
            native_bundle, "_valve_signature_of_handle", return_value=signed
        ):
            return authority.approve_addon_tree_module(11)

    def _approve_launcher(
        self,
        authority: native_bundle.DebugImageAuthority,
        path: str,
        *,
        signed: bool = True,
        after_identity: PathIdentity | None = None,
        directory_identity: PathIdentity | None = None,
    ) -> bool:
        file_identity = self._FILE_IDENTITY
        pinned = (
            authority.steam_install_identity
            if directory_identity is None
            else directory_identity
        )
        seen: list[str] = []

        def directory_identity(queried: str) -> PathIdentity:
            seen.append(queried)
            if ntpath.normcase(queried) == ntpath.normcase(self._STEAM):
                return pinned
            return PathIdentity(7, "77" * 16)

        with patch.object(
            native_bundle,
            "_file_identity",
            side_effect=[file_identity, after_identity or file_identity],
        ), patch.object(
            native_bundle, "_final_handle_path", return_value=path
        ), patch.object(
            native_bundle, "_directory_identity", side_effect=directory_identity
        ), patch.object(
            native_bundle, "_path_identity", return_value=file_identity
        ), patch.object(
            native_bundle, "_valve_signature_of_handle", return_value=signed
        ):
            approved = authority.approve_addon_tree_steam_launcher(11)
        if approved or seen:
            self.assertEqual(
                [ntpath.normcase(queried) for queried in seen],
                [ntpath.normcase(self._STEAM)],
            )
        return approved

    def test_measured_basenames_are_the_closed_set(self) -> None:
        self.assertEqual(
            native_bundle._ADDON_TREE_STEAM_CLIENT_DLLS,
            frozenset(
                {
                    "cserhelper.dll",
                    "gameoverlayrenderer.dll",
                    "gameoverlayrenderer64.dll",
                    "steam.dll",
                    "steamclient.dll",
                    "tier0_s.dll",
                    "vstdlib_s.dll",
                }
            ),
        )
        self.assertEqual(
            native_bundle._ADDON_TREE_STEAM_LAUNCHER_RELATIVE,
            ("bin", "x64launcher.exe"),
        )

    def test_listed_dll_in_the_steam_directory_is_approved(self) -> None:
        authority = self._authority()
        listed = self._STEAM + r"\steamclient.dll"
        self.assertTrue(self._approve(authority, listed))
        self.assertTrue(self._approve(authority, listed.upper()))
        with patch.object(
            native_bundle, "_file_identity", return_value=self._FILE_IDENTITY
        ), patch.object(native_bundle, "_final_handle_path", return_value=listed):
            self.assertFalse(authority.approve_debug_image(11, event_kind="LOAD_DLL"))
        self.assertFalse(authority.approve_addon_tree_module(0))
        self.assertFalse(authority.approve_addon_tree_module(True))  # type: ignore[arg-type]

    def test_valve_signature_and_directory_identity_are_required(self) -> None:
        authority = self._authority()
        listed = self._STEAM + r"\steamclient.dll"
        self.assertFalse(self._approve(authority, listed, signed=False))
        self.assertFalse(
            self._approve(
                authority,
                listed,
                directory_identity=PathIdentity(9, "11" * 16),
            )
        )
        self.assertFalse(
            self._approve(
                authority,
                listed,
                after_identity=PathIdentity(2, "22" * 16),
            )
        )
        self.assertFalse(
            self._approve(
                self._authority(steam_install_identity=None),
                listed,
            )
        )

    def test_unlisted_dll_in_the_steam_directory_is_rejected(self) -> None:
        authority = self._authority()
        self.assertFalse(
            self._approve(authority, self._STEAM + r"\steamclient64.dll")
        )
        self.assertFalse(self._approve(authority, self._STEAM + r"\steamclient.exe"))

    def test_listed_name_outside_the_steam_directory_is_rejected(self) -> None:
        authority = self._authority()
        outside = (
            r"C:\Users\guill\AppData\Local\Temp\steamclient.dll",
            r"C:\Program Files (x86)\SteamSibling\steamclient.dll",
            r"C:\Program Files (x86)\Steam\..\Temp\steamclient.dll",
            r"C:\Program Files (x86)\Steam\..\Steam\steamclient.dll",
            self._STEAM + r"\steamapps\steamclient.dll",
            self._STEAM + r"\bin\steamclient.dll",
        )
        for path in outside:
            with self.subTest(path=path):
                self.assertFalse(self._approve(authority, path))

    def test_every_subdirectory_including_bin_is_rejected(self) -> None:
        authority = self._authority()
        self.assertNotIn(
            "steam_client_dll_directories",
            native_bundle.DebugImageAuthority.__dataclass_fields__,
        )
        for path in (
            self._STEAM + r"\bin\steamclient.dll",
            self._STEAM + r"\bin\nested\steamclient.dll",
            self._STEAM + r"\steamapps\steamclient.dll",
        ):
            with self.subTest(path=path):
                self.assertFalse(self._approve(authority, path))
        with self.assertRaises(TypeError):
            self._authority(steam_client_dll_directories=frozenset({self._STEAM + r"\bin"}))

    def test_steam_dll_uses_the_same_directory_and_signature_rule(self) -> None:
        authority = self._authority(
            steam_client_dll_names=native_bundle._ADDON_TREE_STEAM_CLIENT_DLLS,
        )
        listed = self._STEAM + r"\steam.dll"
        self.assertTrue(self._approve(authority, listed))
        self.assertTrue(self._approve(authority, listed.upper()))
        self.assertFalse(self._approve(authority, listed, signed=False))
        self.assertFalse(
            self._approve(
                authority,
                listed,
                directory_identity=PathIdentity(9, "11" * 16),
            )
        )
        for path in (
            self._STEAM + r"\bin\steam.dll",
            self._STEAM + r"\bin\nested\steam.dll",
            self._STEAM + r"\steamapps\steam.dll",
            r"C:\Users\guill\AppData\Local\Temp\steam.dll",
        ):
            with self.subTest(path=path):
                self.assertFalse(self._approve(authority, path))
        self.assertFalse(
            self._approve(
                self._authority(steam_install_directory=None),
                listed,
            )
        )

    def test_x64launcher_is_approved_only_at_bin_of_the_pinned_directory(self) -> None:
        authority = self._authority()
        listed = self._STEAM + r"\bin\x64launcher.exe"
        self.assertTrue(self._approve_launcher(authority, listed))
        self.assertTrue(
            self._approve_launcher(authority, self._STEAM + r"\BIN\X64LAUNCHER.EXE")
        )
        self.assertFalse(self._approve_launcher(authority, listed, signed=False))
        self.assertFalse(
            self._approve_launcher(
                authority,
                listed,
                directory_identity=PathIdentity(9, "11" * 16),
            )
        )
        self.assertFalse(
            self._approve_launcher(
                authority,
                listed,
                after_identity=PathIdentity(2, "22" * 16),
            )
        )
        rejected = (
            self._STEAM + r"\x64launcher.exe",
            self._STEAM + r"\bin\win64\x64launcher.exe",
            self._STEAM + r"\bin\nested\x64launcher.exe",
            self._STEAM + r"\steamapps\bin\x64launcher.exe",
            self._STEAM + r"\bin\steam.exe",
            self._STEAM + r"\bin\x64launcher.dll",
            self._STEAM + r"\bin\..\bin\x64launcher.exe",
            r"C:\Program Files (x86)\SteamSibling\bin\x64launcher.exe",
            r"D:\Steam\bin\x64launcher.exe",
        )
        for path in rejected:
            with self.subTest(path=path):
                self.assertFalse(self._approve_launcher(authority, path))
        self.assertFalse(authority.approve_addon_tree_steam_launcher(0))
        self.assertFalse(authority.approve_addon_tree_steam_launcher(True))  # type: ignore[arg-type]
        self.assertFalse(
            self._approve(authority, listed),
        )
        self.assertFalse(
            self._approve_launcher(
                self._authority(steam_install_directory=None),
                listed,
            )
        )
        self.assertFalse(
            self._approve_launcher(
                self._authority(steam_client_dll_names=frozenset()),
                listed,
            )
        )

    def test_unresolved_steam_directory_disables_the_rule(self) -> None:
        authority = self._authority(steam_install_directory=None)
        self.assertFalse(
            self._approve(authority, self._STEAM + r"\steamclient.dll")
        )
        with patch.object(
            native_bundle, "_resolve_addon_tree_steam_directory", return_value=None
        ):
            directory, names, identity = native_bundle._addon_tree_steam_rule()
        self.assertIsNone(directory)
        self.assertEqual(names, frozenset())
        self.assertIsNone(identity)
        disabled = self._authority(
            steam_install_directory=directory,
            steam_client_dll_names=names,
            steam_install_identity=identity,
        )
        self.assertFalse(
            self._approve(disabled, self._STEAM + r"\steamclient.dll")
        )

    def test_bundle_load_stores_the_resolved_steam_rule(self) -> None:
        source = Path(native_bundle.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from dayz_mcp.steam_preflight", source)
        self.assertNotIn("import steam_preflight", source)
        pinned = native_bundle._PinnedSteamInstall(
            self._STEAM, self._INSTALL_IDENTITY
        )
        listed = self._STEAM + r"\steamclient.dll"
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "bundle"
            externals = _write_loadable_bundle(root, base / "externals")
            entry = launcher_registry._create_registry_entry_for_test(
                "dayz-test-v1", root, "dayz-test-launcher.exe"
            )
            with launcher_registry._open_registry_entry_for_test(entry) as opened:
                with patch.object(
                    native_bundle,
                    "_external_paths",
                    return_value=frozenset(
                        ntpath.normcase(str(path)) for path in externals
                    ),
                ), patch.object(
                    native_bundle,
                    "_resolve_addon_tree_steam_directory",
                    return_value=pinned,
                ):
                    with native_bundle.load_verified_bundle(opened) as verified:
                        authority = verified.debug_image_authority
                        self.assertEqual(authority.steam_install_directory, self._STEAM)
                        self.assertEqual(
                            authority.steam_client_dll_names,
                            native_bundle._ADDON_TREE_STEAM_CLIENT_DLLS,
                        )
                        self.assertEqual(
                            authority.steam_install_identity, self._INSTALL_IDENTITY
                        )
                        self.assertTrue(self._approve(authority, listed))
                        self.assertFalse(
                            self._approve(
                                authority, self._STEAM + r"\bin\steamclient.dll"
                            )
                        )
                        with patch.object(
                            native_bundle,
                            "_file_identity",
                            return_value=self._FILE_IDENTITY,
                        ), patch.object(
                            native_bundle, "_final_handle_path", return_value=listed
                        ):
                            self.assertFalse(
                                authority.approve_debug_image(
                                    11, event_kind="LOAD_DLL"
                                )
                            )

    def test_steam_resolution_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            steam_exe = directory / "steam.exe"
            steam_exe.write_bytes(b"steam-image")
            other_dir = directory / "other"
            other_dir.mkdir()
            other_exe = other_dir / "steam.exe"
            other_exe.write_bytes(b"other-steam-image")
            image = str(steam_exe)
            other = str(other_exe)
            registry = image.replace("\\", "/")

            class Provider:
                def __init__(
                    self, pids: tuple[object, ...], images: dict[int, object]
                ) -> None:
                    self._pids = pids
                    self._images = images
                    self._calls = 0

                def steam_process_pids(self) -> tuple[object, ...]:
                    if self._pids == ("raise",):
                        raise OSError("unreadable")
                    return self._pids

                def process_image_path(self, pid: int) -> object:
                    self._calls += 1
                    image_path = self._images[pid]
                    if image_path == "raise":
                        raise OSError("unreadable")
                    if type(image_path) is tuple:
                        return image_path[min(self._calls - 1, len(image_path) - 1)]
                    return image_path

            class Host:
                def __init__(self, executable: object) -> None:
                    self._executable = executable

                def steam_executable(self) -> object:
                    if self._executable == "raise":
                        raise OSError("unreadable")
                    return self._executable

            def resolve(
                pids: tuple[object, ...],
                images: dict[int, object],
                registry_value: object,
            ) -> native_bundle._PinnedSteamInstall | None:
                return native_bundle._resolve_addon_tree_steam_directory(
                    provider=Provider(pids, images),
                    host=Host(registry_value),
                )

            with patch.object(
                native_bundle, "_valve_signature_of_handle", return_value=True
            ):
                agreed = resolve((4,), {4: image}, registry)
                self.assertIsInstance(agreed, native_bundle._PinnedSteamInstall)
                assert agreed is not None
                self.assertEqual(
                    ntpath.normcase(agreed.directory),
                    ntpath.normcase(str(directory)),
                )
                self.assertEqual(
                    agreed.identity,
                    native_bundle._directory_identity(agreed.directory),
                )
                self.assertIsNone(resolve((), {}, image))
                self.assertIsNone(resolve((4,), {4: image}, None))
                self.assertIsNone(resolve((), {}, None))
                self.assertIsNone(resolve((), {}, r"C:\missing\steam.exe"))
                self.assertIsNone(resolve(("raise",), {}, image))
                self.assertIsNone(resolve((4,), {4: "raise"}, image))
                self.assertIsNone(
                    resolve((4,), {4: r"C:\Windows\notepad.exe"}, None)
                )
                self.assertIsNone(
                    resolve((4, 5), {4: image, 5: image}, image)
                )
                self.assertIsNone(resolve((4, 5), {4: image, 5: other}, None))
                self.assertIsNone(resolve((4,), {4: image}, other))
                self.assertIsNone(
                    resolve((4,), {4: (image, other)}, image)
                )
                self.assertIsNone(resolve((0,), {}, None))
                self.assertIsNone(resolve(tuple(range(1, 10)), {}, None))
                self.assertIsNone(resolve((), {}, "raise"))
                self.assertIsNone(resolve((4,), {4: image}, "raise"))
            with patch.object(
                native_bundle, "_valve_signature_of_handle", return_value=False
            ):
                self.assertIsNone(resolve((4,), {4: image}, image))
            with patch.object(
                native_bundle, "_valve_signature_of_handle", return_value=True
            ), patch.object(native_bundle, "_directory_identity", return_value=None):
                self.assertIsNone(resolve((4,), {4: image}, image))

    def test_held_steam_image_cannot_be_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            image = directory / "steam.exe"
            held = directory / "held.exe"
            image.write_bytes(b"steam-image")
            seen: dict[str, OSError | None] = {"error": None}

            def signed(file_handle: int, path: str) -> bool:
                try:
                    os.replace(image, held)
                except OSError as error:
                    seen["error"] = error
                return True

            class Provider:
                def steam_process_pids(self) -> tuple[int, ...]:
                    return (4,)

                def process_image_path(self, pid: int) -> str:
                    return str(image)

            class Host:
                def steam_executable(self) -> str:
                    return str(image)

            with patch.object(
                native_bundle, "_valve_signature_of_handle", side_effect=signed
            ):
                pinned = native_bundle._resolve_addon_tree_steam_directory(
                    provider=Provider(), host=Host()
                )
            self.assertIsInstance(seen["error"], OSError)
            assert seen["error"] is not None
            self.assertEqual(seen["error"].winerror, 32)
            self.assertTrue(image.is_file())
            self.assertFalse(held.exists())
            self.assertIsNotNone(pinned)

    def test_live_image_swap_during_the_query_does_not_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            running = directory / "steam.exe"
            held = directory / "held-cmd.exe"
            replacement = directory / "signed-steam.exe"
            shutil.copyfile(r"C:\Windows\System32\cmd.exe", running)
            replacement.write_bytes(b"replacement-not-the-running-image")
            process = subprocess.Popen(
                [str(running), "/Q", "/K"],
                cwd=directory,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                reader = native_bundle._LiveSteamProcessReader()
                ready = False
                for _ in range(50):
                    if process.poll() is not None:
                        self.fail("test process exited early")
                    try:
                        reader.process_image_path(process.pid)
                    except OSError:
                        time.sleep(0.05)
                    else:
                        ready = True
                        break
                self.assertTrue(ready)

                class Race:
                    def __init__(self) -> None:
                        self.swapped = False

                    def steam_process_pids(self) -> tuple[int, ...]:
                        return (process.pid,)

                    def process_image_path(self, pid: int) -> str:
                        path = reader.process_image_path(pid)
                        if not self.swapped:
                            os.replace(running, held)
                            os.replace(replacement, running)
                            self.swapped = True
                        return path

                class Host:
                    def steam_executable(self) -> str:
                        return str(running)

                race = Race()
                with patch.object(
                    native_bundle, "_valve_signature_of_handle", return_value=True
                ):
                    pinned = native_bundle._resolve_addon_tree_steam_directory(
                        provider=race, host=Host()
                    )
                self.assertTrue(race.swapped)
                self.assertIsNone(process.poll())
                self.assertFalse(authenticode.is_valve_signed(str(held)))
                self.assertIsNone(pinned)
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
                if process.stdin is not None:
                    process.stdin.close()

    @requires_installed_steam
    def test_unsigned_handle_is_rejected_when_the_path_is_swapped(self) -> None:
        real = Path(r"C:\Program Files (x86)\Steam\steamclient.dll")
        self.assertTrue(real.is_file())
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            module = directory / "steamclient.dll"
            held = directory / "held-unsigned.dll"
            signed = directory / "signed-copy.dll"
            module.write_bytes(b"unsigned payload in debug event")
            shutil.copyfile(real, signed)
            handle = native_bundle._open_share_read(str(module))
            self.assertIsNotNone(handle)
            assert handle is not None
            final = native_bundle._final_handle_path(handle)
            parent = ntpath.dirname(final)
            pinned = native_bundle._directory_identity(parent)
            self.assertIsInstance(pinned, PathIdentity)
            authority = self._authority(
                steam_install_directory=parent,
                steam_install_identity=pinned,
                steam_client_dll_names=frozenset({"steamclient.dll"}),
            )
            before = native_bundle._file_identity(handle)
            seen = {"calls": 0, "path_valve": False}

            def swap(file_handle: int, *, path: str) -> bool:
                seen["calls"] += 1
                os.replace(module, held)
                os.replace(signed, module)
                try:
                    seen["path_valve"] = authenticode.is_valve_signed(path)
                    return authenticode.is_valve_signed_handle(file_handle, path=path)
                finally:
                    os.replace(module, signed)
                    os.replace(held, module)

            try:
                with patch.object(
                    native_bundle, "is_valve_signed_handle", side_effect=swap
                ):
                    approved = authority.approve_addon_tree_module(handle)
                self.assertEqual(native_bundle._file_identity(handle), before)
            finally:
                native_bundle._close_kernel_handle(handle)
            self.assertFalse(approved)
            self.assertEqual(seen["calls"], 1)
            self.assertTrue(seen["path_valve"])
            self.assertFalse(authenticode.is_valve_signed(str(module)))

    def test_steam_image_rejects_unsigned_handle_when_its_path_is_swapped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            image = directory / "steam.exe"
            held = directory / "held-unsigned.exe"
            image.write_bytes(b"unsigned steam image")
            seen: dict[str, object] = {"calls": 0, "error": None}

            def swap(file_handle: int, *, path: str) -> bool:
                seen["calls"] = int(seen["calls"]) + 1
                try:
                    os.replace(image, held)
                except OSError as error:
                    seen["error"] = error
                return authenticode.is_valve_signed_handle(file_handle, path=path)

            class Provider:
                def steam_process_pids(self) -> tuple[int, ...]:
                    return (4,)

                def process_image_path(self, pid: int) -> str:
                    return str(image)

            class Host:
                def steam_executable(self) -> str:
                    return str(image)

            with patch.object(
                native_bundle, "is_valve_signed_handle", side_effect=swap
            ):
                pinned = native_bundle._resolve_addon_tree_steam_directory(
                    provider=Provider(), host=Host()
                )
            self.assertIsNone(pinned)
            self.assertEqual(seen["calls"], 1)
            self.assertIsInstance(seen["error"], OSError)
            assert isinstance(seen["error"], OSError)
            self.assertEqual(seen["error"].winerror, 32)
            self.assertTrue(image.is_file())
            self.assertFalse(held.exists())

    @requires_installed_steam
    def test_attributes_only_handle_is_approved_by_the_same_file_id(self) -> None:
        dll = self._STEAM + r"\steamclient.dll"
        pinned = native_bundle._directory_identity(self._STEAM)
        self.assertIsInstance(pinned, PathIdentity)
        authority = self._authority(
            steam_client_dll_names=native_bundle._ADDON_TREE_STEAM_CLIENT_DLLS,
            steam_install_identity=pinned,
        )
        readable = native_bundle._open_share_read(dll)
        self.assertIsNotNone(readable)
        assert readable is not None
        try:
            self.assertTrue(authority.approve_addon_tree_module(readable))
        finally:
            native_bundle._close_kernel_handle(readable)
        attributes = native_bundle._path_kernel32.CreateFileW(
            dll,
            native_bundle._FILE_READ_ATTRIBUTES,
            native_bundle._FILE_SHARE_READ
            | native_bundle._FILE_SHARE_WRITE
            | native_bundle._FILE_SHARE_DELETE,
            None,
            native_bundle._OPEN_EXISTING,
            native_bundle._FILE_ATTRIBUTE_NORMAL,
            None,
        )
        numeric = native_bundle._usable_handle(attributes)
        self.assertIsNotNone(numeric)
        assert numeric is not None
        try:
            self.assertIsNone(native_bundle._duplicate_read(numeric))
            self.assertTrue(authority.approve_addon_tree_module(numeric))
        finally:
            native_bundle._close_kernel_handle(numeric)

    @requires_installed_steam
    def test_real_steam_dll_handle_is_approved(self) -> None:
        dll = self._STEAM + r"\Steam.dll"
        self.assertTrue(Path(dll).is_file())
        pinned = native_bundle._directory_identity(self._STEAM)
        self.assertIsInstance(pinned, PathIdentity)
        authority = self._authority(
            steam_client_dll_names=native_bundle._ADDON_TREE_STEAM_CLIENT_DLLS,
            steam_install_identity=pinned,
        )
        readable = native_bundle._open_share_read(dll)
        self.assertIsNotNone(readable)
        assert readable is not None
        try:
            final = native_bundle._final_handle_path(readable)
            self.assertEqual(
                ntpath.normcase(ntpath.dirname(final)),
                ntpath.normcase(self._STEAM),
            )
            self.assertEqual(ntpath.basename(final).casefold(), "steam.dll")
            self.assertTrue(authority.approve_addon_tree_module(readable))
        finally:
            native_bundle._close_kernel_handle(readable)

    @requires_installed_steam
    def test_real_x64launcher_handle_is_approved_only_for_that_image(self) -> None:
        exe = self._STEAM + r"\bin\x64launcher.exe"
        self.assertTrue(Path(exe).is_file())
        pinned = native_bundle._directory_identity(self._STEAM)
        self.assertIsInstance(pinned, PathIdentity)
        authority = self._authority(
            steam_client_dll_names=native_bundle._ADDON_TREE_STEAM_CLIENT_DLLS,
            steam_install_identity=pinned,
        )
        readable = native_bundle._open_share_read(exe)
        self.assertIsNotNone(readable)
        assert readable is not None
        try:
            final = native_bundle._final_handle_path(readable)
            self.assertEqual(
                ntpath.normcase(final),
                ntpath.normcase(exe),
            )
            self.assertTrue(authority.approve_addon_tree_steam_launcher(readable))
            self.assertFalse(authority.approve_addon_tree_module(readable))
        finally:
            native_bundle._close_kernel_handle(readable)

    @requires_installed_steam
    def test_unsigned_x64launcher_handle_is_rejected_when_the_path_is_swapped(self) -> None:
        real = Path(self._STEAM + r"\bin\x64launcher.exe")
        self.assertTrue(real.is_file())
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            bin_dir = directory / "bin"
            bin_dir.mkdir()
            module = bin_dir / "x64launcher.exe"
            held = directory / "held-unsigned.exe"
            signed = directory / "signed-copy.exe"
            module.write_bytes(b"unsigned launcher image")
            shutil.copyfile(real, signed)
            handle = native_bundle._open_share_read(str(module))
            self.assertIsNotNone(handle)
            assert handle is not None
            final = native_bundle._final_handle_path(handle)
            install = ntpath.dirname(ntpath.dirname(final))
            pinned = native_bundle._directory_identity(install)
            self.assertIsInstance(pinned, PathIdentity)
            authority = self._authority(
                steam_install_directory=install,
                steam_install_identity=pinned,
                steam_client_dll_names=native_bundle._ADDON_TREE_STEAM_CLIENT_DLLS,
            )
            before = native_bundle._file_identity(handle)
            seen = {"calls": 0, "path_valve": False}

            def swap(file_handle: int, *, path: str) -> bool:
                seen["calls"] += 1
                os.replace(module, held)
                os.replace(signed, module)
                try:
                    seen["path_valve"] = authenticode.is_valve_signed(path)
                    return authenticode.is_valve_signed_handle(file_handle, path=path)
                finally:
                    os.replace(module, signed)
                    os.replace(held, module)

            try:
                with patch.object(
                    native_bundle, "is_valve_signed_handle", side_effect=swap
                ):
                    approved = authority.approve_addon_tree_steam_launcher(handle)
                self.assertEqual(native_bundle._file_identity(handle), before)
            finally:
                native_bundle._close_kernel_handle(handle)
            self.assertFalse(approved)
            self.assertEqual(seen["calls"], 1)
            self.assertTrue(seen["path_valve"])
            self.assertFalse(authenticode.is_valve_signed(str(module)))


if __name__ == "__main__":
    unittest.main()
