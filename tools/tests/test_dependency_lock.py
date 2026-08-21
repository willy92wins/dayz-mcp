from __future__ import annotations

import copy
import ast
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path, PurePosixPath


TOOLS_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = TOOLS_DIR.parent
LOCK_PATH = TOOLS_DIR / "dependency-lock.json"
REGISTRY_PATH = TOOLS_DIR / "approved-launchers.baseline.json"
SHA256_PATTERN = re.compile(r"[0-9A-F]{64}")
WINDOWS_ABSOLUTE_PATTERN = re.compile(r"[A-Z]:\\[^\x00]+")

EXPECTED_LOCK = {
    "artifacts": {
        "approved_launcher_registry": {
            "location": "project_relative",
            "path": "tools/approved-launchers.baseline.json",
            "role": "empty_registry_baseline",
            "sha256": "330B04E8D7AB06E7EE850326C1CAE180F119ED21486745DC0EC9BAAE203C653B",
            "size": 45,
        },
        "secure_launcher_source": {
            "location": "project_relative",
            "path": "tools/dayz_mcp/secure_launcher.py",
            "role": "productive_launcher_source",
            "sha256": "BD7C0FDDB23B9BA583B7BD250F16D6D720249D28A21B108AA787EA2F032DF2B2",
            "size": 8943,
        },
        "psutil_license": {
            "location": "project_relative",
            "path": "tools/vendor/psutil/LICENSE",
            "role": "vendored_license",
            "sha256": "C7ADC4D5D1337A548B967421F1FBE258B93033A0417708FD6F4E38F8ECBCEB80",
            "size": 1577,
        },
        "psutil_wheel": {
            "location": "project_relative",
            "path": "tools/vendor/psutil/psutil-7.2.2-cp37-abi3-win_amd64.whl",
            "role": "vendored_wheel",
            "sha256": "EB7E81434C8D223EC4A219B5FC1C47D0417B12BE7EA866E24FB5AD6E84B3D988",
            "size": 137737,
        },
    },
    "format_version": 1,
    "remote_artifacts": {
        "cpython_embed": {
            "architecture": "AMD64",
            "license": "PSF-2.0",
            "sha256": "AD4961A479DEDBEB7C7D113253F8DB1B1935586B73C27488712BEEC4F2C894E6",
            "size": 12023245,
            "url": "https://www.python.org/ftp/python/3.14.3/python-3.14.3-embed-amd64.zip",
            "version": "3.14.3",
        }
    },
    "toolchains": {
        "msvc": {
            "compiler_version": "19.50.35728",
            "files": {
                "c1": {"path": "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.50.35717\\bin\\Hostx64\\x64\\c1.dll", "sha256": "2D419A8A92185738B41AA46270941C789D2ADAB3073237F84B2D41FB6976FF25", "size": 3475480},
                "c1xx": {"path": "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.50.35717\\bin\\Hostx64\\x64\\c1xx.dll", "sha256": "9373032DC14BAA99DD9EBF3D0FF8FF3E691313A68ADFDC4D4B2331ACD779FBF0", "size": 12529688},
                "c2": {"path": "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.50.35717\\bin\\Hostx64\\x64\\c2.dll", "sha256": "B782BEAB246445804327C0A0937DD3751A0756F65071A9908E398A788C0E3EFD", "size": 10839584},
                "cl": {"path": "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.50.35717\\bin\\Hostx64\\x64\\cl.exe", "sha256": "194DDF4AAFCB74452218A982309A97DE30E0ADB33EDF4AF02904EE107213E782", "size": 682568},
                "dumpbin": {"path": "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.50.35717\\bin\\Hostx64\\x64\\dumpbin.exe", "sha256": "003959D2E77E464CD90F1B09BCD593E908A3922398B0E193CCEAB67D22D86DF2", "size": 22584},
                "link": {"path": "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.50.35717\\bin\\Hostx64\\x64\\link.exe", "sha256": "C988ACB4EC7EF964AE4D4E414B693A6A3782DD85F1E200100B3F896DB6B1A202", "size": 3416136},
                "libcmt": {"path": "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.50.35717\\lib\\x64\\libcmt.lib", "sha256": "E913A9FA09008C5FD9CAF7F1EAD3CE0AF91D89028BF28C53BDBA49E15CDCFD51", "size": 6531918},
                "libvcruntime": {"path": "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.50.35717\\lib\\x64\\libvcruntime.lib", "sha256": "A500FE3BDA1C254B8EB7B0EAFCF08F068AE5E1E32EC1DEF969B77DD2F5321EB2", "size": 2065690},
            },
            "root_version": "14.50.35717",
            "trees": {
                "bin_hostx64_x64": {"path": "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.50.35717\\bin\\Hostx64\\x64", "sha256": "1EAC0899D415401CBAAD04A15EB473F602D53484CE74E6B4E7677619699C561B"},
                "include": {"path": "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.50.35717\\include", "sha256": "BB750405060F340654C14A7449B771C9A17FA7A4E7AAB4AD317DB50F988750B4"},
                "lib": {"path": "C:\\Program Files (x86)\\Microsoft Visual Studio\\18\\BuildTools\\VC\\Tools\\MSVC\\14.50.35717\\lib", "sha256": "84B43E51D6E5F5B84772754C43AD9E76C7C6AB28C92205397BE20775A1111207"},
            },
        },
        "windows_sdk": {
            "files": {
                "bcrypt": {"path": "C:\\Program Files (x86)\\Windows Kits\\10\\Lib\\10.0.26100.0\\um\\x64\\bcrypt.lib", "sha256": "9FE31DF255D17B0339391FF80A71BF2C5D442CC301BD2FE6EEEA00B626F10785", "size": 14576},
                "buffer_overflow_u": {"path": "C:\\Program Files (x86)\\Windows Kits\\10\\Lib\\10.0.26100.0\\um\\x64\\BufferOverflowU.lib", "sha256": "38B7E4D7E149938954CFCDDCAE6AC380A1B3CE20D19F373ACDCEF1112E374463", "size": 385732},
                "kernel32": {"path": "C:\\Program Files (x86)\\Windows Kits\\10\\Lib\\10.0.26100.0\\um\\x64\\kernel32.lib", "sha256": "341C7D56125A03B458E4D5093E4C79B33123CCFDFD610FE236937B8E6F3134BB", "size": 311908},
            },
            "trees": {
                "include": {"path": "C:\\Program Files (x86)\\Windows Kits\\10\\Include\\10.0.26100.0", "sha256": "A1FFD7E99C13A598F11B28AF84B4CE321CDA1421C373C0D336D29A398A96585E"},
                "lib": {"path": "C:\\Program Files (x86)\\Windows Kits\\10\\Lib\\10.0.26100.0", "sha256": "F5194AC0112674B25E8317F887DE06DE3126D45A02921257168A331A9604944C"},
            },
            "version": "10.0.26100.0",
        },
    },
}


def _closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _canonical_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _require_exact_keys(value: object, expected: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{label}: keys")
    return value


def _require_sha_size_path(value: object, label: str) -> None:
    item = _require_exact_keys(value, {"path", "sha256", "size"}, label)
    if type(item["size"]) is not int or item["size"] <= 0:
        raise ValueError(f"{label}: size")
    if type(item["sha256"]) is not str or not SHA256_PATTERN.fullmatch(item["sha256"]):
        raise ValueError(f"{label}: sha256")
    if type(item["path"]) is not str or not WINDOWS_ABSOLUTE_PATTERN.fullmatch(item["path"]):
        raise ValueError(f"{label}: path")


def _validate_lock(payload: object) -> None:
    root = _require_exact_keys(payload, {"artifacts", "format_version", "remote_artifacts", "toolchains"}, "root")
    if type(root["format_version"]) is not int or root["format_version"] != 1:
        raise ValueError("format_version")
    artifacts = _require_exact_keys(root["artifacts"], set(EXPECTED_LOCK["artifacts"]), "artifacts")
    for name, raw in artifacts.items():
        item = _require_exact_keys(raw, {"location", "path", "role", "sha256", "size"}, f"artifact {name}")
        if type(item["size"]) is not int or item["size"] <= 0:
            raise ValueError(f"artifact {name}: size")
        if type(item["sha256"]) is not str or not SHA256_PATTERN.fullmatch(item["sha256"]):
            raise ValueError(f"artifact {name}: sha256")
        path = item["path"]
        if type(path) is not str:
            raise ValueError(f"artifact {name}: path")
        if item["location"] == "project_relative":
            pure = PurePosixPath(path)
            if pure.is_absolute() or ".." in pure.parts or "\\" in path or ":" in path:
                raise ValueError(f"artifact {name}: relative path")
        elif item["location"] == "windows_absolute":
            if not WINDOWS_ABSOLUTE_PATTERN.fullmatch(path) or ".." in Path(path).parts:
                raise ValueError(f"artifact {name}: absolute path")
        else:
            raise ValueError(f"artifact {name}: location")
    remote = _require_exact_keys(root["remote_artifacts"], {"cpython_embed"}, "remote")
    _require_exact_keys(remote["cpython_embed"], {"architecture", "license", "sha256", "size", "url", "version"}, "cpython_embed")
    toolchains = _require_exact_keys(root["toolchains"], {"msvc", "windows_sdk"}, "toolchains")
    for toolchain_name, toolchain in toolchains.items():
        for file_name, item in toolchain["files"].items():
            _require_sha_size_path(item, f"{toolchain_name}.{file_name}")
        for tree_name, item in toolchain["trees"].items():
            tree = _require_exact_keys(item, {"path", "sha256"}, f"{toolchain_name}.{tree_name}")
            if type(tree["path"]) is not str or not WINDOWS_ABSOLUTE_PATTERN.fullmatch(tree["path"]):
                raise ValueError(f"{toolchain_name}.{tree_name}: path")
            if type(tree["sha256"]) is not str or not SHA256_PATTERN.fullmatch(tree["sha256"]):
                raise ValueError(f"{toolchain_name}.{tree_name}: sha256")


def _tree_digest(root: Path) -> str:
    aggregate = hashlib.sha256()
    files = sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: path.relative_to(root).as_posix().lower())
    for path in files:
        relative = path.relative_to(root).as_posix().lower()
        file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        aggregate.update(f"{relative}\0{path.stat().st_size}\0{file_sha256}\n".encode("utf-8"))
    return aggregate.hexdigest().upper()


class DependencyLockTest(unittest.TestCase):
    def _payload(self) -> dict:
        raw = LOCK_PATH.read_bytes()
        payload = json.loads(raw, object_pairs_hook=_closed_object)
        self.assertEqual(raw, _canonical_bytes(payload))
        _validate_lock(payload)
        return payload

    def test_lock_pins_the_shipped_supply_chain_exactly(self) -> None:
        # artifacts and remote_artifacts ship with the repo and are pinned
        # byte-exactly. toolchains describes whichever machine builds the
        # launcher -- relock_toolchain.py rewrites it there -- so it is held
        # to the schema, not to the author's toolchain.
        payload = self._payload()
        self.assertEqual(payload["artifacts"], EXPECTED_LOCK["artifacts"])
        self.assertEqual(
            payload["remote_artifacts"], EXPECTED_LOCK["remote_artifacts"]
        )
        self.assertEqual(set(payload["toolchains"]), {"msvc", "windows_sdk"})

    def test_schema_rejects_duplicate_extra_bool_and_malformed_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate key"):
            json.loads('{"format_version":1,"format_version":1}', object_pairs_hook=_closed_object)
        mutations = []
        extra = copy.deepcopy(EXPECTED_LOCK); extra["unexpected"] = None; mutations.append(extra)
        bool_size = copy.deepcopy(EXPECTED_LOCK); bool_size["artifacts"]["psutil_license"]["size"] = True; mutations.append(bool_size)
        malformed_sha = copy.deepcopy(EXPECTED_LOCK); malformed_sha["artifacts"]["psutil_license"]["sha256"] = "abc"; mutations.append(malformed_sha)
        malformed_size = copy.deepcopy(EXPECTED_LOCK); malformed_size["toolchains"]["msvc"]["files"]["cl"]["size"] = -1; mutations.append(malformed_size)
        malformed_path = copy.deepcopy(EXPECTED_LOCK); malformed_path["artifacts"]["psutil_license"]["path"] = "../LICENSE"; mutations.append(malformed_path)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                _validate_lock(mutation)

    def test_all_existing_file_artifacts_match_live_bytes(self) -> None:
        payload = self._payload()
        checked = 0
        for name, item in payload["artifacts"].items():
            path = PROJECT_ROOT / PurePosixPath(item["path"])
            self.assertTrue(
                path.is_file(),
                f"locked project_relative artifact missing: {name} -> {item['path']}",
            )
            with self.subTest(path=str(path)):
                raw = path.read_bytes()
                self.assertEqual(len(raw), item["size"])
                self.assertEqual(hashlib.sha256(raw).hexdigest().upper(), item["sha256"])
                checked += 1
        for toolchain in payload["toolchains"].values():
            for item in toolchain["files"].values():
                path = Path(item["path"])
                if not path.is_file():
                    continue
                with self.subTest(path=str(path)):
                    raw = path.read_bytes()
                    self.assertEqual(len(raw), item["size"])
                    self.assertEqual(hashlib.sha256(raw).hexdigest().upper(), item["sha256"])
                    checked += 1
        self.assertGreater(checked, 0, "no locked artifact was present to verify")

    def test_tree_digests_match_live_bytes(self) -> None:
        checked = 0
        for toolchain in self._payload()["toolchains"].values():
            for item in toolchain["trees"].values():
                root = Path(item["path"])
                if not root.is_dir():
                    continue
                with self.subTest(path=item["path"]):
                    self.assertEqual(_tree_digest(root), item["sha256"])
                checked += 1
        if not checked:
            self.skipTest("no locked toolchain tree is present on this machine")

    def test_file_drift_is_detected(self) -> None:
        expected = EXPECTED_LOCK["artifacts"]["psutil_license"]
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "LICENSE"
            changed.write_bytes(b"X" * expected["size"])
            with self.assertRaises(AssertionError):
                self.assertEqual(hashlib.sha256(changed.read_bytes()).hexdigest().upper(), expected["sha256"])

    def test_cpython_is_declaration_only_without_local_path(self) -> None:
        cpython = self._payload()["remote_artifacts"]["cpython_embed"]
        self.assertNotIn("path", cpython)
        self.assertEqual((cpython["version"], cpython["architecture"], cpython["license"]), ("3.14.3", "AMD64", "PSF-2.0"))

    def test_registry_is_empty_and_ps1_remains_static_non_launcher_evidence(self) -> None:
        payload = self._payload()
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("historical_dayz_test_oracle", payload["artifacts"])
        self.assertFalse(
            any("test-contracts/" in item["path"] for item in payload["artifacts"].values())
        )
        self.assertEqual(registry, {"format_version": 1, "launchers": []})
        self.assertNotIn("dayz-test.ps1", REGISTRY_PATH.read_text(encoding="utf-8").casefold())
        static_inspection_exclusions = {
            "dayz_mcp/doctor.py": {"dayz-test.ps1"},
            "dayz_mcp/security_runtime_audit.py": {
                "run-poc.ps1",
                "run-fase1.ps1",
                "run-fase2.ps1",
                "run-fase3.ps1",
            },
        }
        for source_path in (TOOLS_DIR / "dayz_mcp").rglob("*.py"):
            relative = source_path.relative_to(TOOLS_DIR).as_posix()
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            parents = {
                child: parent
                for parent in ast.walk(tree)
                for child in ast.iter_child_nodes(parent)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                lowered = node.value.casefold()
                if not any(token in lowered for token in (".ps1", "powershell", "pwsh")):
                    continue
                self.assertIn(relative, static_inspection_exclusions)
                self.assertIn(lowered, static_inspection_exclusions[relative])
                ancestors = []
                current = node
                while current in parents:
                    current = parents[current]
                    ancestors.append(current)
                if relative == "dayz_mcp/doctor.py":
                    self.assertTrue(
                        any(
                            isinstance(ancestor, ast.Call)
                            and isinstance(ancestor.func, ast.Attribute)
                            and ancestor.func.attr == "rglob"
                            for ancestor in ancestors
                        )
                    )
                else:
                    self.assertTrue(
                        any(
                            isinstance(ancestor, ast.Assign)
                            and any(
                                isinstance(target, ast.Name)
                                and target.id == "RUNTIME_HTTP_EXCLUSIONS"
                                for target in ancestor.targets
                            )
                            for ancestor in ancestors
                        )
                    )


    def test_psutil_lock_pin_matches_vendored_manifest(self) -> None:
        payload = self._payload()
        wheel = payload["artifacts"]["psutil_wheel"]
        license_item = payload["artifacts"]["psutil_license"]
        manifest = json.loads(
            (TOOLS_DIR / "vendor" / "psutil" / "SHA256SUMS.json").read_text(encoding="utf-8")
        )
        by_name = {entry["filename"]: entry for entry in manifest["files"]}
        wheel_entry = by_name["psutil-7.2.2-cp37-abi3-win_amd64.whl"]
        license_entry = by_name["LICENSE"]
        self.assertEqual(wheel["path"], "tools/vendor/psutil/psutil-7.2.2-cp37-abi3-win_amd64.whl")
        self.assertEqual(wheel["size"], wheel_entry["bytes"])
        self.assertEqual(wheel["sha256"], wheel_entry["sha256"].upper())
        self.assertEqual(license_item["path"], "tools/vendor/psutil/LICENSE")
        self.assertEqual(license_item["size"], license_entry["bytes"])
        self.assertEqual(license_item["sha256"], license_entry["sha256"].upper())
        requirements = (TOOLS_DIR / "requirements-mcp.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?m)^psutil==7\.2\.2$")


if __name__ == "__main__":
    unittest.main()
