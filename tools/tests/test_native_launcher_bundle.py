from __future__ import annotations

import hashlib
import ast
import importlib
import inspect
import json
import os
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path, PureWindowsPath

from dayz_mcp import native_broker_protocol
from tests._bundle_paths import requires_built_bundle


TOOLS_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = TOOLS_DIR.parent
BUNDLE_DIR = TOOLS_DIR / "native-launchers" / "dayz-test-v1"
BUILD_MODULE = "build_native_launcher"


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


_ADDON_ROOT_ROW = re.compile(
    r'\{L"((?:[^"\\]|\\.)*)", L"((?:[^"\\]|\\.)*)", L"((?:[^"\\]|\\.)*)"\},'
)


def _unescape_cpp_wstring(value: str) -> str:
    return value.replace("\\\\", "\\").replace('\\"', '"')


def _parse_addon_root_rows(header: str) -> list[tuple[str, str, str]]:
    return [
        (
            _unescape_cpp_wstring(match.group(1)),
            _unescape_cpp_wstring(match.group(2)),
            _unescape_cpp_wstring(match.group(3)),
        )
        for match in _ADDON_ROOT_ROW.finditer(header)
    ]


def _same_path_text(left: str, right: str) -> bool:
    return left.casefold() == right.casefold()


def _addon_request_allowed(
    rows: list[tuple[str, str, str]],
    prefix: str,
    target: str,
    temp: str,
) -> bool:
    roots = next((row for row in rows if _same_path_text(row[0], prefix)), None)
    if roots is None:
        return False
    expected_target = roots[1] + prefix + "\\Addons"
    expected_temp = roots[2] + prefix
    return _same_path_text(target, expected_target) and _same_path_text(temp, expected_temp)


class NativeLauncherBundleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = importlib.import_module(BUILD_MODULE)

    @requires_built_bundle
    def test_request_policy_is_canonical_closed_and_sealed(self) -> None:
        path = BUNDLE_DIR / "request-policy.json"
        raw = path.read_bytes()
        payload = json.loads(raw)
        self.assertEqual(raw, _canonical_bytes(payload))
        self.builder.validate_request_policy_document(payload)
        self.assertEqual(set(payload), {"format_version", "projects"})
        self.assertEqual(payload["format_version"], 1)
        self.assertTrue(1 <= len(payload["projects"]) <= 128)
        for project in payload["projects"]:
            self.assertEqual(
                set(project),
                {
                    "default_base_mods",
                    "default_source",
                    "dev_root",
                    "mission_roots",
                    "mod",
                    "mod_roots",
                },
            )
            self.assertTrue(project["mod"])
            self.assertTrue(self.builder._valid_root(project["dev_root"]))
            self.assertTrue(self.builder._valid_root(project["default_source"]))
            for root in (*project["mission_roots"], *project["mod_roots"]):
                self.assertTrue(self.builder._valid_root(root))

    @requires_built_bundle
    def test_app_pyz_is_deterministic_and_contains_exact_request_parser(self) -> None:
        app = BUNDLE_DIR / "app.pyz"
        with zipfile.ZipFile(app) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            self.assertEqual(
                archive.read("dayz_mcp/dayz_test_request.py"),
                (TOOLS_DIR / "dayz_mcp" / "dayz_test_request.py").read_bytes(),
            )
            self.assertIn("__main__.py", names)
            self.assertEqual(
                archive.read("__main__.py"),
                (BUNDLE_DIR / "src" / "app_main.py").read_bytes(),
            )
            self.assertNotIn(b"SystemExit(125)", archive.read("__main__.py"))
            self.assertTrue(all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist()))
            self.assertFalse(any("__pycache__" in name or name.endswith((".pyc", ".pyo")) for name in names))

    @requires_built_bundle
    def test_private_runtime_path_is_closed_and_site_is_disabled(self) -> None:
        pth = (BUNDLE_DIR / "runtime" / "python314._pth").read_text(encoding="ascii")
        self.assertEqual(pth, ".\npython314.zip\n..\\app.pyz\n..\\vendor\n")
        self.assertNotIn("import site", pth.casefold())
        self.assertTrue((BUNDLE_DIR / "runtime" / "python.exe").is_file())
        self.assertTrue((BUNDLE_DIR / "runtime" / "python314.zip").is_file())
        self.assertTrue((BUNDLE_DIR / "vendor" / "psutil" / "_psutil_windows.pyd").is_file())

    def test_private_worker_uses_debugger_safe_blocking_read(self) -> None:
        source = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(encoding="utf-8")
        read_message = source[
            source.index("bool ReadWorkerMessage"):
            source.index("bool WriteWorkerResponse")
        ]
        self.assertIn("ReadExact(input", read_message)
        self.assertNotIn("ReadExactBounded", source)
        self.assertNotIn("BoundedReadThread", source)
        self.assertNotIn("CreateThread(nullptr, 0, BoundedReadThread", source)
        self.assertIn(
            "size < sizeof(kWorkerTerminalMagic) - 1",
            read_message,
        )
        loop = source[
            source.index("if (kind == LaunchKind::PRIVATE_WORKER && broker_ok)"):
            source.index("} else if (broker_ok)")
        ]
        self.assertIn("ReadWorkerMessage(parent_output", loop)
        self.assertNotIn("PeekNamedPipe(parent_output", loop)
        self.assertNotIn("bool worker_exited", loop)

    def test_worker_failure_still_emits_generic_terminal(self) -> None:
        source = (BUNDLE_DIR / "src" / "app_main.py").read_text(encoding="utf-8")
        self.assertIn("def _write_worker_terminal(", source)
        failure = source[source.index("def main()") :]
        self.assertIn("if not arguments:", failure)
        self.assertIn('error_code = "internal_failure"', failure)
        self.assertIn("_write_worker_terminal(", failure)
        self.assertNotIn("str(error)", failure)

    @requires_built_bundle
    def test_closure_manifest_is_canonical_exact_and_matches_live_bytes(self) -> None:
        manifest_path = BUNDLE_DIR / "closure-manifest.json"
        raw = manifest_path.read_bytes()
        payload = json.loads(raw)
        self.assertEqual(raw, _canonical_bytes(payload))
        self.builder.verify_bundle(BUNDLE_DIR)
        self.assertEqual(
            set(payload),
            {
                "bundle_id",
                "dayz_test_request_sha256",
                "dayz_test_readiness_sha256",
                "entries",
                "format_version",
                "native_broker_protocol_sha256",
                "request_policy_sha256",
                "dayz_test_worker_sha256",
                "worker_runtime_sha256",
            },
        )
        self.assertEqual(payload["format_version"], 1)
        self.assertEqual(payload["bundle_id"], "dayz-test-v1")
        self.assertEqual(
            payload["dayz_test_request_sha256"],
            hashlib.sha256((TOOLS_DIR / "dayz_mcp" / "dayz_test_request.py").read_bytes()).hexdigest().upper(),
        )
        self.assertEqual(
            payload["request_policy_sha256"],
            hashlib.sha256((BUNDLE_DIR / "request-policy.json").read_bytes()).hexdigest().upper(),
        )
        self.assertEqual(
            payload["native_broker_protocol_sha256"],
            hashlib.sha256((TOOLS_DIR / "dayz_mcp" / "native_broker_protocol.py").read_bytes()).hexdigest().upper(),
        )
        self.assertEqual(
            payload["dayz_test_readiness_sha256"],
            hashlib.sha256((TOOLS_DIR / "dayz_mcp" / "dayz_test_readiness.py").read_bytes()).hexdigest().upper(),
        )
        self.assertEqual(
            payload["dayz_test_worker_sha256"],
            hashlib.sha256((TOOLS_DIR / "dayz_mcp" / "dayz_test_worker.py").read_bytes()).hexdigest().upper(),
        )
        self.assertEqual(
            payload["worker_runtime_sha256"],
            hashlib.sha256((BUNDLE_DIR / "worker-runtime.json").read_bytes()).hexdigest().upper(),
        )
        identities = [(item["kind"], item["path"]) for item in payload["entries"]]
        self.assertEqual(identities, sorted(identities, key=lambda item: (item[0], item[1].casefold())))
        self.assertIn(("bundle", "build-contract.json"), identities)
        self.assertIn(("external", r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\AddonBuilder.exe"), identities)
        self.assertIn(("external", r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe"), identities)
        addon_root = r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder"
        self.assertTrue(
            {
                ("external", addon_root + "\\" + name)
                for name in (
                    "AddonBuilder.exe.config",
                    "log4net.dll",
                    "NDesk.Options.dll",
                    "SharedResources.dll",
                    "SteamHelper.dll",
                    "SteamLayerWrap.dll",
                    "steam_api.dll",
                    "Utils.dll",
                    r"en-US\SharedResources.resources.dll",
                    "logger.xml",
                    "steam_appid.txt",
                )
            }.issubset(set(identities))
        )
        helper_roots = {
            "binarize": r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\Binarize",
            "cfgconvert": r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\CfgConvert",
            "filebank": r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\PboUtils",
        }
        expected_helpers = {
            ("external", helper_roots["binarize"] + "\\binarize.exe"),
            ("external", helper_roots["binarize"] + "\\steam_api64.dll"),
            ("external", helper_roots["binarize"] + "\\bin.txt"),
            ("external", helper_roots["binarize"] + r"\bin\config.cpp"),
            ("external", helper_roots["cfgconvert"] + "\\CfgConvert.exe"),
            ("external", helper_roots["filebank"] + "\\FileBank.exe"),
            ("external", helper_roots["filebank"] + "\\NativeMethods.dll"),
            ("external", helper_roots["filebank"] + "\\log4net.dll"),
            ("external", helper_roots["filebank"] + "\\LibCommon.dll"),
            ("external", helper_roots["filebank"] + "\\exclude.lst"),
        }
        self.assertTrue(expected_helpers.issubset(set(identities)))
        self.assertTrue(
            {
                ("external", r"C:\Program Files (x86)\Steam\steamclient.dll"),
                ("external", r"C:\Program Files (x86)\Steam\Steam.dll"),
                ("external", r"C:\Program Files (x86)\Steam\CSERHelper.dll"),
                ("external", r"C:\Program Files (x86)\Steam\GameOverlayRenderer.dll"),
                ("external", r"C:\Program Files (x86)\Steam\tier0_s.dll"),
                ("external", r"C:\Program Files (x86)\Steam\vstdlib_s.dll"),
            }.issubset(set(identities))
        )
        self.assertFalse(
            any(path.casefold().endswith("\\dssignfile.exe") for _kind, path in identities)
        )

    @requires_built_bundle
    def test_build_contract_and_reproducibility_receipt_are_closed(self) -> None:
        contract_path = BUNDLE_DIR / "build-contract.json"
        contract_raw = contract_path.read_bytes()
        contract = json.loads(contract_raw)
        self.assertEqual(contract_raw, _canonical_bytes(contract))
        self.assertEqual(
            set(contract),
            {
                "builder_sha256",
                "dependency_lock_sha256",
                "format_version",
                "sources",
            },
        )
        self.assertEqual(contract["format_version"], 1)
        self.assertEqual(
            contract["builder_sha256"],
            hashlib.sha256((TOOLS_DIR / "build_native_launcher.py").read_bytes())
            .hexdigest()
            .upper(),
        )
        self.assertEqual(
            contract["dependency_lock_sha256"],
            hashlib.sha256((TOOLS_DIR / "dependency-lock.json").read_bytes())
            .hexdigest()
            .upper(),
        )
        self.assertEqual(
            contract["sources"],
            {
                name: hashlib.sha256(
                    (BUNDLE_DIR / "src" / name).read_bytes()
                ).hexdigest().upper()
                for name in ("app_main.py", "launcher.cpp")
            },
        )
        self.builder.verify_reproducibility_receipt(
            BUNDLE_DIR,
            require_reproducible=True,
        )
        self.assertFalse((BUNDLE_DIR / "generated" / "closure_manifest.h").exists())
        self.assertFalse((BUNDLE_DIR / "generated" / "addon_roots.h").exists())
        launcher_source = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(
            'L"generated\\\\closure_manifest.h"',
            launcher_source,
        )
        self.assertNotIn(
            'L"generated\\\\addon_roots.h"',
            launcher_source,
        )
        self.assertIn("bool SameBundlePathText(", launcher_source)
        self.assertIn(
            "SameBundlePathText(kClosureEntries[index].path, manifest_path)",
            launcher_source,
        )

        build_source = inspect.getsource(self.builder.build)
        receipt_source = build_source[build_source.index("receipt =") :]
        self.assertIn("_acquire_cpython(lock, offline=True)", build_source)
        self.assertNotIn("**final_fingerprint", receipt_source)

    @requires_built_bundle
    def test_closure_verifier_rejects_missing_extra_hash_and_hardlink_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "bundle"
            shutil.copytree(BUNDLE_DIR, copied)
            victim = copied / "app.pyz"
            victim.unlink()
            with self.assertRaisesRegex(ValueError, "closure_missing"):
                self.builder.verify_bundle(copied, require_pe=False)

        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "bundle"
            shutil.copytree(BUNDLE_DIR, copied)
            (copied / "runtime" / "unexpected.dll").write_bytes(b"MZ")
            with self.assertRaisesRegex(ValueError, "closure_extra"):
                self.builder.verify_bundle(copied, require_pe=False)

        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "bundle"
            shutil.copytree(BUNDLE_DIR, copied)
            victim = copied / "app.pyz"
            raw = bytearray(victim.read_bytes())
            raw[-1] ^= 1
            victim.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "closure_hash"):
                self.builder.verify_bundle(copied, require_pe=False)

        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "bundle"
            shutil.copytree(BUNDLE_DIR, copied)
            victim = copied / "app.pyz"
            alias = Path(directory) / "alias.pyz"
            victim.unlink()
            os.link(BUNDLE_DIR / "app.pyz", victim)
            os.link(victim, alias)
            with self.assertRaisesRegex(ValueError, "closure_hardlink"):
                self.builder.verify_bundle(copied, require_pe=False)

    def test_cpp_broker_has_closed_protocol_and_single_process_boundary(self) -> None:
        source = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"\bBOOL\s+LaunchApprovedChild\s*\(", source)), 1)
        self.assertEqual(source.count("CreateProcessW("), 1)
        enum = re.search(r"enum class LaunchKind[^\{]*\{([^}]*)\}", source, re.S)
        self.assertIsNotNone(enum)
        self.assertEqual(
            set(re.findall(r"\b(PRIVATE_WORKER|LIFECYCLE_CLI|ADDON_BUILDER)\b", enum.group(1))),
            {"PRIVATE_WORKER", "LIFECYCLE_CLI", "ADDON_BUILDER"},
        )
        for forbidden in ("shellexecute", "winexec", "powershell", "pwsh", "cmd.exe", "system("):
            self.assertNotIn(forbidden, source.casefold())
        self.assertIn("DZM1", source)
        self.assertIn("DZA1", source)
        self.assertIn("DZW1", source)
        self.assertIn("kBrokerVersion = 1", source)
        self.assertIn("kMaxFrameBytes = 65536", source)
        self.assertIn("ValidateClosure", source)
        self.assertIn("JOB_OBJECT_LIMIT_ACTIVE_PROCESS", source)
        self.assertIn("PublishAnnouncement(kind, manifest_path)", source)
        self.assertIn("kind == LaunchKind::LIFECYCLE_CLI", source)

    def test_cpp_child_creation_is_atomic_cancel_bounded_and_private_cwd(self) -> None:
        source = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(encoding="utf-8")
        launch = source[source.index("BOOL LaunchApprovedChild"):source.index('extern "C" void __cdecl wWinMainCRTStartup')]
        self.assertIn("PROC_THREAD_ATTRIBUTE_JOB_LIST", launch)
        self.assertIn("PROC_THREAD_ATTRIBUTE_CHILD_PROCESS_POLICY", launch)
        self.assertIn("PROCESS_CREATION_CHILD_PROCESS_RESTRICTED", launch)
        self.assertNotIn("AssignProcessToJobObject", launch)
        self.assertNotIn("WaitForSingleObject(process.hProcess, INFINITE)", launch)
        self.assertIn("CancelRequested(cancel_handle)", launch)
        self.assertIn("GetCurrentDirectoryW", launch)
        self.assertNotIn("GetTempPathW", source)
        self.assertNotIn('BundlePath(L"runtime", cwd', launch)
        self.assertIn(
            "kind == LaunchKind::ADDON_BUILDER ? 2 : 1",
            launch,
        )
        self.assertIn(
            "kind == LaunchKind::ADDON_BUILDER ||",
            launch,
        )

    def test_private_worker_inherits_liveness_and_awaits_cancel_cleanup(self) -> None:
        cpp = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(encoding="utf-8")
        launch = cpp[
            cpp.index("BOOL LaunchApprovedChild") :
            cpp.index('extern "C" void __cdecl wWinMainCRTStartup')
        ]
        app = (BUNDLE_DIR / "src" / "app_main.py").read_text(encoding="utf-8")
        self.assertIn("DuplicateHandle", launch)
        self.assertIn("GetFileType(cancel_handle) != FILE_TYPE_PIPE", launch)
        self.assertIn("GetFileType(private_cancel_handle) != FILE_TYPE_PIPE", launch)
        self.assertIn("cancel_handle == private_cancel_handle", launch)
        self.assertIn('L"DAYZ_MCP_CANCEL_HANDLE"', cpp)
        self.assertIn('L"DAYZ_MCP_WORKER_CANCEL_HANDLE"', cpp)
        self.assertIn("cancel_event=cancel_event", app)
        self.assertIn("worker_task.cancel()", app)
        self.assertIn("await worker_task", app)
        self.assertIn(
            "cancel_task = asyncio.create_task(cancel_event.wait())\n"
            "    await asyncio.sleep(0)",
            app,
        )
        pipe_broker = app[
            app.index("class _PipeBroker:") : app.index("def _bundle_root()")
        ]
        self.assertIn("return await asyncio.to_thread(self._roundtrip, frame)", pipe_broker)
        private_cancel_branch = launch[
            launch.index("if (kind == LaunchKind::PRIVATE_WORKER && broker_ok)") :
            launch.index("} else if (broker_ok)")
        ]
        self.assertNotIn("TerminateProcess(process.hProcess, ERROR_CANCELLED)", private_cancel_branch)

    def test_cpp_validates_private_and_lifecycle_payloads_byte_exactly(self) -> None:
        source = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(encoding="utf-8")
        self.assertNotIn("PayloadLooksClosed", source)
        self.assertIn("ValidatePrivatePayload", source)
        self.assertIn("ValidateLifecyclePayload", source)
        self.assertIn("ValidatePayloadByKind", source)
        announcement = source[source.index("bool PublishAnnouncement"):source.index("BOOL LaunchApprovedChild")]
        self.assertIn("FILE_ID_INFO identity", announcement)
        self.assertIn("FileIdInfo", announcement)
        self.assertIn("identity.VolumeSerialNumber", announcement)

    @requires_built_bundle
    def test_worker_runtime_is_canonical_and_closed(self) -> None:
        path = BUNDLE_DIR / "worker-runtime.json"
        raw = path.read_bytes()
        payload = json.loads(raw)
        self.assertEqual(raw, _canonical_bytes(payload))
        self.assertEqual(set(payload), {"format_version", "projects"})
        self.assertEqual(payload["format_version"], 1)
        self.assertTrue(1 <= len(payload["projects"]) <= 128)
        self.builder.validate_worker_runtime_document(payload)
        for project in payload["projects"]:
            self.assertEqual(
                set(project),
                {
                    "build_source_basename", "build_temp_root", "dev_root",
                    "diag_executable", "game_directory", "mission_aliases",
                    "mod", "mods_root",
                },
            )
            self.assertEqual(
                set(project["mission_aliases"]),
                {"chernarus", "livonia", "sakhal"},
            )

    def test_cpp_broker_preserves_full_frame_and_is_bidirectional(self) -> None:
        source = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(encoding="utf-8")
        self.assertIn("WriteAllBounded", source)
        self.assertNotIn("WriteAll(", source)
        self.assertIn("parent_input, broker_frame, broker_frame_bytes, process.hProcess", source)
        self.assertNotIn("body + header.payload_bytes", source)
        self.assertIn("CreatePipe(&parent_output, &child_output", source)
        self.assertIn("ReadWorkerMessage(parent_output", source)
        self.assertIn("response, response_bytes, process.hProcess", source)
        self.assertIn("GetStdHandle(STD_OUTPUT_HANDLE), output.bytes, output.size", source)

    def test_cpp_child_output_keeps_bytes_when_pipe_closes_before_process_signal(self) -> None:
        source = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(encoding="utf-8")
        read_output = source[
            source.index("bool ReadChildOutput"):
            source.index("bool ExpectAscii")
        ]
        self.assertIn("error == ERROR_BROKEN_PIPE || error == ERROR_NO_DATA", read_output)
        self.assertIn("WaitForSingleObject(process, 50) == WAIT_OBJECT_0", read_output)

    def test_cpp_broker_routes_exact_three_kinds_and_secrets_only_to_lifecycle(self) -> None:
        source = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(encoding="utf-8")
        app = (BUNDLE_DIR / "src" / "app_main.py").read_text(encoding="utf-8")
        self.assertIn("CaptureSecrets(&secrets)", source)
        self.assertIn('SetEnvironmentVariableW(name, nullptr)', source)
        self.assertIn("kind == LaunchKind::LIFECYCLE_CLI", source)
        self.assertIn('L"USERPROFILE", user_profile', source)
        self.assertIn('L"DAYZ_MCP_LEASE_TOKEN", secrets->token', source)
        self.assertIn('L"DAYZ_MCP_NORMAL_POLICY_JSON"', source)
        self.assertIn("load_inherited_normal_daemon_policy()", app)
        self.assertIn("kind == LaunchKind::ADDON_BUILDER", source)
        self.assertNotIn("if (kind == LaunchKind::ADDON_BUILDER) {\n        return FALSE;", source)
        self.assertIn("ParseAddonRequest", source)
        self.assertIn("BuildAddonCommand", source)
        self.assertIn("AddonResponse", source)
        self.assertIn("AddonBuilder\\\\AddonBuilder.exe", source)
        self.assertIn("AppendText(command, command_capacity, request.prefix)", source)

    def test_cpp_broker_rejects_nested_private_and_invalid_responses(self) -> None:
        source = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(encoding="utf-8")
        self.assertIn("child_header.kind != static_cast<BYTE>(LaunchKind::PRIVATE_WORKER)", source)
        self.assertIn("ResponseLooksClosed(output->bytes, output->size)", source)
        self.assertIn("SecureZero(output->bytes, output->size)", source)

    def test_cpp_addon_builder_binding_is_not_project_hardcoded(self) -> None:
        source = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(encoding="utf-8")
        parse = source[source.index("bool ParseAddonRequest"):source.index("bool AppendQuoted")]
        command = source[source.index("bool BuildAddonCommand"):source.index("void FreeChildOutput")]
        pbo_path = source[source.index("bool BuildPboPath"):source.index("bool CapturePboSnapshot")]
        response = source[source.index("bool AddonResponse"):source.index("bool ResponseLooksClosed")]
        for block in (parse, command, response):
            self.assertNotIn("ExampleMod", block)
        self.assertNotIn("P:\\", parse)
        self.assertNotIn("C:\\Users", parse)
        self.assertNotIn("IsAllowedAddonTemp", source)
        self.assertIn("FindAddonRoots(request->prefix)", parse)
        self.assertIn("roots != nullptr", parse)
        self.assertIn("roots->target_root", parse)
        self.assertIn("roots->temp_root", parse)
        self.assertIn("request.prefix", command)
        self.assertIn("request.prefix", pbo_path)
        self.assertIn("pbo_path", response)

    def test_cpp_addon_response_requires_a_fresh_regular_pbo(self) -> None:
        source = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(encoding="utf-8")
        launch = source[
            source.index("BOOL LaunchApprovedChild") :
            source.index('extern "C" void __cdecl wWinMainCRTStartup')
        ]
        capture = source[
            source.index("bool CapturePboSnapshot") :
            source.index("bool AddonResponse")
        ]
        response = source[
            source.index("bool AddonResponse") :
            source.index("bool ResponseLooksClosed")
        ]
        self.assertIn("struct PboSnapshot", source)
        self.assertIn("FileAttributeTagInfo", capture)
        self.assertIn("FILE_ATTRIBUTE_REPARSE_POINT", capture)
        self.assertIn("standard.NumberOfLinks == 1", capture)
        self.assertIn("HashHandle(file, snapshot->sha256)", capture)
        self.assertIn("PboSnapshotChanged(before, after)", response)
        self.assertIn("!SameBytes(before.sha256, after.sha256, kHashBytes)", source)
        self.assertLess(
            launch.index("CapturePboSnapshot(pbo_path, true, &pbo_before)"),
            launch.index("PublishAnnouncement(kind, manifest_path)"),
        )
        self.assertIn("AddonResponse(pbo_path, pbo_before, code, output)", launch)

    def test_generated_addon_roots_table_accepts_sealed_temp_and_rejects_foreign(self) -> None:
        try:
            policy_path = self.builder.resolve_launcher_policy_path()
        except ValueError:
            policy_path = None
        if policy_path is None or not policy_path.is_file():
            self.skipTest("host launcher policy is unavailable")
        policy = self.builder.load_launcher_policy_source(policy_path)
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            self.builder._write_addon_roots_header(staging, policy)
            header_path = staging / "generated" / "addon_roots.h"
            header = header_path.read_text(encoding="utf-8")
        self.assertIn("inline constexpr AddonRootEntry kAddonRoots[]", header)
        self.assertIn(
            "inline constexpr DWORD kAddonRootCount = ARRAYSIZE(kAddonRoots);",
            header,
        )
        rows = _parse_addon_root_rows(header)
        self.assertEqual(rows, self.builder._addon_root_rows(policy))
        self.assertGreaterEqual(len(rows), 1)

        # The row under test is DERIVED from the table instead of named:
        # naming a project couples the suite to whichever machine policy built
        # the bundle, so it could only ever pass on the author's host.
        row = rows[0]
        prefix = row[0]
        target = row[1] + prefix + "\\Addons"
        temp = row[2] + prefix
        # Every expected path is derived from the table. Spelling one out would
        # copy a sealed root of whoever built the bundle into the suite, and pin
        # it to that machine.
        self.assertTrue(temp.endswith("\\" + prefix))
        self.assertTrue(
            _addon_request_allowed(rows, prefix, target, temp),
        )

        # The prefix hung off the PARENT of its sealed root: the near miss that
        # the old hardcoded exception used to wave through.
        parent_of_root = PureWindowsPath(row[2].rstrip("\\")).parent
        foreign_temps = (
            r"C:\Windows\Temp\foreign",
            temp + r"\child",
            temp + "_other",
            str(parent_of_root / prefix),
        )
        for foreign in foreign_temps:
            with self.subTest(foreign_temp=foreign):
                self.assertFalse(
                    _addon_request_allowed(rows, prefix, target, foreign),
                )

        unknown_prefix = "NotInSealedPolicy"
        self.assertFalse(any(_same_path_text(item[0], unknown_prefix) for item in rows))
        self.assertFalse(
            _addon_request_allowed(rows, unknown_prefix, target, temp),
        )

        payload = {
            "clear": False,
            "pack_only": True,
            "prefix": prefix,
            "source": r"C:\Example\Source",
            "target": target,
            "temp": temp,
        }
        frame = native_broker_protocol.encode_request(
            native_broker_protocol.BrokerKind.ADDON_BUILDER, payload
        )
        decoded = native_broker_protocol.decode_request(frame)
        self.assertEqual(decoded.payload, payload)

        source = (BUNDLE_DIR / "src" / "launcher.cpp").read_text(encoding="utf-8")
        self.assertNotIn("P:\\", source)
        self.assertNotIn("C:\\Users", source)
        self.assertNotIn("IsAllowedAddonTemp", source)
        self.assertNotIn("kLfvExecutorTemp", source)
        for project in policy["projects"]:
            self.assertNotIn(project["mod"], source)
        lookup = source[
            source.index("const AddonRootEntry* FindAddonRoots") :
            source.index("bool ParseAddonRequest")
        ]
        self.assertIn("kAddonRoots[index].prefix", lookup)
        self.assertIn("return nullptr;", lookup)
        self.assertNotIn("P:\\Mods\\", lookup)
        self.assertNotIn("P:\\temp\\", lookup)
        parse = source[
            source.index("bool ParseAddonRequest") : source.index("bool AppendQuoted")
        ]
        self.assertIn("FindAddonRoots(request->prefix)", parse)
        self.assertIn("roots != nullptr", parse)
        self.assertIn("SamePathText(request->temp, expected_temp)", parse)
        prepare = inspect.getsource(self.builder._prepare_staging)
        self.assertIn("_write_addon_roots_header(staging, host)", prepare)
        self.assertIn('(staging / "generated" / "addon_roots.h").unlink()', prepare)
        self.assertFalse((BUNDLE_DIR / "generated" / "addon_roots.h").exists())

    @requires_built_bundle
    def test_pe_is_amd64_pe32plus_and_embeds_unique_manifest_marker(self) -> None:
        pe = (BUNDLE_DIR / "dayz-test-launcher.exe").read_bytes()
        self.assertEqual(pe[:2], b"MZ")
        pe_offset = int.from_bytes(pe[0x3C:0x40], "little")
        self.assertEqual(pe[pe_offset:pe_offset + 4], b"PE\0\0")
        self.assertEqual(int.from_bytes(pe[pe_offset + 4:pe_offset + 6], "little"), 0x8664)
        self.assertEqual(int.from_bytes(pe[pe_offset + 24:pe_offset + 26], "little"), 0x20B)
        manifest_sha = hashlib.sha256((BUNDLE_DIR / "closure-manifest.json").read_bytes()).hexdigest().upper().encode("ascii")
        marker = b"DAYZ_MCP_MANIFEST_SHA256=" + manifest_sha
        self.assertEqual(pe.count(marker), 1)


class PackagedModuleClosureTest(unittest.TestCase):
    """PACKAGED_MODULES must be closed under its own dayz_mcp imports.

    app.pyz carries exactly the modules PACKAGED_MODULES names. A packaged module
    that imports an unpackaged sibling builds fine and then raises
    ModuleNotFoundError at runtime, inside a bundle that has already shipped.

    That is not hypothetical: on 2026-08-21 the duplicated FILE_STANDARD_INFO in
    pinned_keyfile.py and request_path_authority.py was unified into a new
    win32_fileinfo.py, and pinned_keyfile -- which IS packaged -- began importing a
    module that was not. The bundle's own drift check caught the edit, but only
    because the bundle happened to be stale; a fresh build would have been quietly
    broken. This test reads source, needs no built bundle, and so also runs in a
    clone that has none.
    """

    def test_every_dayz_mcp_import_of_a_packaged_module_is_itself_packaged(self) -> None:
        builder = importlib.import_module(BUILD_MODULE)
        packaged = set(builder.PACKAGED_MODULES)
        package_dir = TOOLS_DIR / "dayz_mcp"
        missing: list[str] = []
        def runtime_nodes(tree: ast.AST):
            """Every node except the bodies of `if TYPE_CHECKING:` blocks.

            Those imports are erased at runtime, so a type-only reference to an
            unpackaged module is not a packaging hole. native_process_guard.py
            imports ProcessRecord that way and must not be reported.
            """
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.If) and _is_type_checking(node.test):
                    for branch in node.orelse:  # an `else:` under it still runs
                        yield branch
                        yield from runtime_nodes(branch)
                    continue
                yield node
                yield from runtime_nodes(node)

        def _is_type_checking(test: ast.expr) -> bool:
            return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )

        for name in sorted(packaged):
            tree = ast.parse((package_dir / name).read_text(encoding="utf-8"), filename=name)
            for node in runtime_nodes(tree):
                imported: list[str] = []
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("dayz_mcp"):
                    tail = (node.module or "").split(".")
                    if len(tail) > 1:
                        imported.append(tail[1] + ".py")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        parts = alias.name.split(".")
                        if parts[0] == "dayz_mcp" and len(parts) > 1:
                            imported.append(parts[1] + ".py")
                for sibling in imported:
                    if sibling not in packaged and (package_dir / sibling).is_file():
                        missing.append(f"{name} imports {sibling}, which is not packaged")
        self.assertEqual(missing, [], "PACKAGED_MODULES is not import-closed")


class PackagedModuleListsAgreeTest(unittest.TestCase):
    """The builder's PACKAGED_MODULES and the verifier's copy must be the same set.

    native_bundle.py is the verifier and ships INSIDE app.pyz, so it cannot import the
    builder to learn the list: the duplication is deliberate. What was missing is
    anything holding the two copies together. On 2026-08-21 win32_fileinfo.py was added
    to the builder's list only; the bundle then built fine and was rejected at install
    with a bare invalid_native_launcher_bundle, because the verifier's frozen member set
    did not contain it. The error names neither list, so the cause is invisible from the
    message alone.
    """

    def test_builder_and_verifier_agree_on_the_packaged_set(self) -> None:
        builder = importlib.import_module(BUILD_MODULE)
        from dayz_mcp import native_bundle

        self.assertEqual(
            set(builder.PACKAGED_MODULES),
            set(native_bundle._APP_PACKAGED_MODULES),
            "build_native_launcher.PACKAGED_MODULES and "
            "native_bundle._APP_PACKAGED_MODULES have drifted apart",
        )

    def test_the_member_set_is_exactly_the_modules_plus_the_two_fixed_entries(self) -> None:
        from dayz_mcp import native_bundle

        expected = {"__main__.py", "dayz_mcp/__init__.py"} | {
            f"dayz_mcp/{name}" for name in native_bundle._APP_PACKAGED_MODULES
        }
        self.assertEqual(set(native_bundle._APP_MEMBERS), expected)


if __name__ == "__main__":
    unittest.main()
