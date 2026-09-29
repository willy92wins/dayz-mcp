from __future__ import annotations

import json
import ntpath
import os
import re
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import build_native_launcher
from dayz_mcp import dayz_tools_paths, launcher_registry, native_bundle, native_child_announcement
from dayz_mcp.dayz_tools_paths import addon_helper_exes
from dayz_mcp.native_broker_protocol import BrokerKind
from dayz_mcp.native_child_announcement import ChildAnnouncement, ChildAnnouncementDecoder
from dayz_mcp.request_path_authority import PathIdentity
from tests._bundle_paths import requires_built_bundle, requires_closure_manifest


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
                    announcement = ChildAnnouncementDecoder().feed(
                        self._broker_frame(resolved, "CD" * 32, PathIdentity(5, "0B" * 16))
                    )[0]
                    self.assertEqual(announcement.announced_path, resolved)
                    folded = "".join(
                        character.lower() if character.isupper() else character.upper()
                        for character in resolved
                    )
                    folded_announcement = ChildAnnouncementDecoder().feed(
                        self._broker_frame(folded, "CD" * 32, PathIdentity(5, "0B" * 16))
                    )[0]
                    self.assertEqual(folded_announcement.announced_path, folded)
                    with self.assertRaisesRegex(ValueError, "invalid_native_child_announcement"):
                        ChildAnnouncementDecoder().feed(
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
            announcement = ChildAnnouncementDecoder().feed(
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
                        announcement = ChildAnnouncementDecoder().feed(
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


if __name__ == "__main__":
    unittest.main()
