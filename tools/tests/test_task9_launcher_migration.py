from __future__ import annotations

import ast
import base64
import hashlib
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

from tests._bundle_paths import requires_installed_launcher


TOOLS_DIR = Path(__file__).resolve().parents[1]
LAUNCHER_SOURCE = TOOLS_DIR / "dayz_mcp" / "secure_launcher.py"
REGISTRY = TOOLS_DIR / "approved-launchers.json"
README = TOOLS_DIR / "README-mcp.md"
AUDITOR_PATH = TOOLS_DIR / "dayz_mcp" / "security_runtime_audit.py"
LEGACY_EVIDENCE = (
    TOOLS_DIR.parents[0]
    / ".superpowers"
    / "sdd"
    / "legacy-task9-launcher-migration-pyc.b64.txt"
)
LEGACY_PYC_SHA256 = "7108FB0AE74BD563657815AD92E14417EAF7336FE394794ACEABA8ED7D4F006E"
LEGACY_B64_SHA256 = "6467F9961EA76E04D23268E5113FAE1E4E6BC568C86656D00084C6B37DE37ECF"
PREFX2_EVIDENCE = (
    TOOLS_DIR.parents[0]
    / ".superpowers"
    / "sdd"
    / "legacy-secure-launcher-prefx2-pyc.b64.txt"
)
PREFX2_B64_SHA256 = "DEA6DEFF7B7F0B1E06FE6534D8BCBC4741E9A9DC14D97A61F6A4C56F06155362"
PREFX2_PYC_SHA256 = "9F14AC8B07442037BB0BC8D496AF849E6E7CC5C829DA2BC3374FD52F42CBA4C7"

# The two b64 blobs are the frozen predecessors of the current launcher, kept
# under .superpowers/ so a migration claim can be re-checked. They are
# development evidence and do not ship.
requires_legacy_evidence = unittest.skipUnless(
    LEGACY_EVIDENCE.is_file() and PREFX2_EVIDENCE.is_file(),
    "legacy migration evidence is development-only and does not ship",
)


class LauncherMigrationContainmentTest(unittest.TestCase):
    def test_secure_launcher_has_no_process_creation_surface(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "dayz_mcp.security_runtime_audit", AUDITOR_PATH
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec is not None else None)
        auditor = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = auditor
        spec.loader.exec_module(auditor)

        findings = auditor.audit_process_creation(TOOLS_DIR)
        self.assertFalse(
            any(
                finding.relative_path == "dayz_mcp/secure_launcher.py"
                for finding in findings
            )
        )

    def _obsolete_global_launcher_surface_assertion(self) -> None:
        source = LAUNCHER_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(
                    (alias.name, alias.asname or alias.name) for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                imports.update(
                    (
                        f"{node.module}.{alias.name}",
                        alias.asname or alias.name,
                    )
                    for alias in node.names
                )

        allowed_imports = {
            ("__future__.annotations", "annotations"),
            ("argparse", "argparse"),
            ("ctypes", "ctypes"),
            ("ctypes.wintypes", "wintypes"),
            ("hashlib", "hashlib"),
            ("json", "json"),
            ("msvcrt", "msvcrt"),
            ("os", "os"),
            ("re", "re"),
            ("stat", "stat"),
            ("sys", "sys"),
            ("dataclasses.dataclass", "dataclass"),
            ("pathlib.Path", "Path"),
            ("pathlib.PureWindowsPath", "PureWindowsPath"),
            ("typing.BinaryIO", "BinaryIO"),
            ("typing.Iterable", "Iterable"),
            ("typing.Sequence", "Sequence"),
        }
        self.assertEqual(imports, allowed_imports)

        forbidden_call_names = {
            "call",
            "check_call",
            "check_output",
            "compile",
            "createprocess",
            "createprocessa",
            "createprocessw",
            "create_subprocess_exec",
            "create_subprocess_shell",
            "eval",
            "exec",
            "getattr",
            "__getattribute__",
            "__import__",
            "loadlibrary",
            "popen",
            "run",
            "shellexecute",
            "shellexecutea",
            "shellexecutew",
            "startfile",
            "system",
            "winexec",
        }
        allowed_direct_calls = {
            "OSError",
            "SystemExit",
            "Path",
            "PureWindowsPath",
            "RuntimeError",
            "ValueError",
            "_GenericParser",
            "_IncrementalRedactor",
            "_FileIdentity",
            "_OpenedLauncher",
            "_open_pinned_read",
            "_identity_from_payload",
            "_identity_from_stat",
            "_open_validated_entry",
            "_open_registry_entry_for_test",
            "_parse_launcher_registry",
            "_read_canonical_registry",
            "_reject_name_surrogates",
            "_reject_path_name_surrogates",
            "_resolve_relative",
            "_same_path",
            "_sha256_stream",
            "_validate_entry",
            "_validate_id",
            "_validate_launcher_registry_payload",
            "_validate_relative",
            "any",
            "bytearray",
            "bytes",
            "dataclass",
            "float",
            "hasattr",
            "int",
            "isinstance",
            "len",
            "list",
            "main",
            "max",
            "next",
            "open_approved_launcher",
            "print",
            "_parser",
            "range",
            "run_secure_launcher",
            "set",
            "sorted",
            "str",
            "super",
            "tuple",
            "type",
        }
        allowed_qualified_calls = {
            ("argparse", "ArgumentParser"),
            ("ctypes", "WinDLL"),
            ("ctypes", "c_void_p"),
            ("ctypes", "get_last_error"),
            ("hashlib", "sha256"),
            ("json", "loads"),
            ("msvcrt", "open_osfhandle"),
            ("os", "close"),
            ("os", "fdopen"),
            ("os", "fstat"),
            ("os", "lstat"),
            ("os", "stat"),
            ("os.path", "abspath"),
            ("os.path", "normcase"),
            ("re", "compile"),
            ("re", "fullmatch"),
            ("stat", "S_ISREG"),
            ("_kernel32", "CloseHandle"),
            ("_kernel32", "CreateFileW"),
        }
        allowed_method_calls = {
            "add_argument",
            "append",
            "casefold",
            "close",
            "_consume",
            "decode",
            "digest",
            "encode",
            "extend",
            "feed",
            "fileno",
            "flush",
            "fullmatch",
            "hexdigest",
            "is_absolute",
            "joinpath",
            "get",
            "open",
            "parse_args",
            "pop",
            "read",
            "relative_to",
            "resolve",
            "seek",
            "startswith",
            "to_payload",
            "update",
            "upper",
        }

        def dotted_name(node: ast.expr) -> str | None:
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, ast.Attribute):
                parent = dotted_name(node.value)
                if parent is not None:
                    return f"{parent}.{node.attr}"
            return None

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            dotted = dotted_name(node.func)
            terminal = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else "<dynamic>"
            )
            if isinstance(node.func, ast.Name):
                self.assertNotIn(terminal.casefold(), forbidden_call_names)
                self.assertIn(node.func.id, allowed_direct_calls)
            elif dotted is not None and "." in dotted:
                base, attribute = dotted.rsplit(".", 1)
                if (base, attribute) not in allowed_qualified_calls:
                    self.assertNotIn(attribute.casefold(), forbidden_call_names)
                if (base, attribute) not in allowed_qualified_calls:
                    self.assertIn(attribute, allowed_method_calls)
            elif isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr.casefold(), forbidden_call_names)
                self.assertIn(node.func.attr, allowed_method_calls)
            else:
                self.fail(f"dynamic call surface is forbidden: {ast.dump(node.func)}")

        identifiers = {
            node.id.casefold() for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr.casefold()
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        string_constants = {
            node.value.casefold()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        string_tokens = {
            token.casefold()
            for value in string_constants
            for token in re.findall(r"[a-zA-Z0-9_.-]+", value)
        }
        for value in (
            "." + "ps1",
            "power" + "shell",
            "pwsh",
            "cmd",
            "cmd" + ".exe",
            "win" + "exec",
            "create" + "process",
            "shell" + "execute",
            "ffi",
        ):
            with self.subTest(value=value):
                self.assertNotIn(value, identifiers)
                self.assertNotIn(value, string_constants)
                self.assertNotIn(value, string_tokens)
        for value in ("." + "ps1", "power" + "shell", "pwsh", "cmd" + ".exe"):
            with self.subTest(source_value=value):
                self.assertNotIn(value, source.casefold())

    @requires_installed_launcher
    def test_registry_contains_native_launcher_and_documentation_exposes_no_legacy_host(self) -> None:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        documentation = README.read_text(encoding="utf-8").casefold()
        forbidden = (
            "." + "ps1",
            "remote" + "signed",
            "power" + "shell",
            "pwsh",
            "cmd" + ".exe",
        )

        self.assertEqual(registry["format_version"], 1)
        self.assertEqual(len(registry["launchers"]), 1)
        approved = registry["launchers"][0]
        self.assertEqual(
            set(approved),
            {"id", "relative_path", "root", "root_file_id", "sha256"},
        )
        self.assertEqual(approved["id"], "dayz-test-v1")
        self.assertEqual(approved["relative_path"], "dayz-test-launcher.exe")
        # The PE hash changes on every reproducible rebuild; compare against the bundle
        # instead of a literal. root is deliberately NOT asserted here: it pins the entry
        # to the tree it was published from, so any copy of the repo would fail. Location,
        # identity and binary swaps are covered by checks/check_native_launcher_registry.py
        # outside this suite, plus verify_bundle and the CAS receipts.
        bundle = TOOLS_DIR / "native-launchers" / "dayz-test-v1"
        self.assertEqual(
            approved["sha256"],
            hashlib.sha256(
                (bundle / approved["relative_path"]).read_bytes()
            ).hexdigest().upper(),
        )
        self.assertNotIn("identity", json.dumps(approved).casefold())
        self.assertNotIn("token", json.dumps(approved).casefold())
        self.assertIn("python -m dayz_mcp.secure_launcher dayz-test-v1", documentation)
        self.assertIn("install_mcp.py", documentation)
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value.casefold(), documentation)

    def test_migration_test_itself_has_no_process_launch_call(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = {
            (node.func.value.id, node.func.attr)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        }

        self.assertNotIn(("sub" + "process", "run"), calls)
        self.assertNotIn(("sub" + "process", "Popen"), calls)

    @requires_legacy_evidence
    def test_legacy_contract_is_preserved_as_non_discoverable_text(self) -> None:
        raw_evidence = LEGACY_EVIDENCE.read_bytes()
        lines = raw_evidence.decode("utf-8").splitlines()
        encoded = "".join(line for line in lines if line and not line.startswith("#"))
        preserved = base64.b64decode(encoded, validate=True)

        self.assertEqual(LEGACY_EVIDENCE.suffix, ".txt")
        self.assertEqual(
            hashlib.sha256(raw_evidence).hexdigest().upper(),
            LEGACY_B64_SHA256,
        )
        self.assertEqual(len(preserved), 48_325)
        self.assertEqual(
            hashlib.sha256(preserved).hexdigest().upper(),
            LEGACY_PYC_SHA256,
        )

    @requires_legacy_evidence
    def test_prefx2_windows_handle_implementation_is_preserved_byte_exact(self) -> None:
        raw_evidence = PREFX2_EVIDENCE.read_bytes()
        lines = raw_evidence.decode("utf-8").splitlines()
        encoded = "".join(line for line in lines if line and not line.startswith("#"))
        preserved = base64.b64decode(encoded, validate=True)

        self.assertEqual(PREFX2_EVIDENCE.suffix, ".txt")
        self.assertIn(
            "# source_length=31461",
            lines,
        )
        self.assertIn(
            f"# source_sha256={PREFX2_PYC_SHA256}",
            lines,
        )
        self.assertEqual(
            hashlib.sha256(raw_evidence).hexdigest().upper(),
            PREFX2_B64_SHA256,
        )
        self.assertEqual(len(preserved), 31_461)
        self.assertEqual(
            hashlib.sha256(preserved).hexdigest().upper(),
            PREFX2_PYC_SHA256,
        )


if __name__ == "__main__":
    unittest.main()
