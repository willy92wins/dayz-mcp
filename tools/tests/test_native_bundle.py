from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from dayz_mcp import launcher_registry, native_bundle
from dayz_mcp.dayz_tools_paths import addon_helper_exes
from dayz_mcp.native_broker_protocol import BrokerKind
from dayz_mcp.native_child_announcement import ChildAnnouncement
from dayz_mcp.request_path_authority import PathIdentity
from tests._bundle_paths import requires_built_bundle


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

    @requires_built_bundle
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


if __name__ == "__main__":
    unittest.main()
