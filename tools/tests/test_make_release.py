"""Release staging contract for a prebuilt DayZ_MCP PBO.

The fixture is a synthetic PBO whose build marker (mcp_build.json) names GIT_SHA:
make_release refuses a PBO built from any other commit, or one that does not say
(fb-20260819-024951-e307). The opaque bytes the fixture used to be are now one of
its entries, so the release still has to copy the PBO byte for byte.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from tests.pbo_helpers import build_pbo, marker_bytes


TOOLS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS_DIR.parent
PROJECT_FILE = TOOLS_DIR / "pyproject.toml"
BRIDGE_FILE = REPO_ROOT / "addon" / "scripts" / "5_Mission" / "MCPMessages.c"
RELEASE_DOC = REPO_ROOT / "docs" / "RELEASE.md"

OPAQUE_BYTES = b"\x00DayZ-MCP release fixture\xff\r\n"
GIT_SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
ADDON_TREE = "89abcdef0123456789abcdef0123456789abcdef"
BUILT_UTC = "2026-08-24T12:34:56Z"
BIKEY_BYTES = b"DayZ_MCP\x00synthetic-public-key"
BISIGN_BYTES = BIKEY_BYTES + b"synthetic-signature"
MOD_CPP_BYTES = b'name = "DayZ-MCP";\nauthor = "test";\n'


def _pbo(marker: bytes | None) -> bytes:
    entries = [("config.cpp", b"class CfgPatches {};\n"), ("scripts\\fixture.bin", OPAQUE_BYTES)]
    if marker is not None:
        entries.append(("mcp_build.json", marker))
    return build_pbo(entries)


PBO_BYTES = _pbo(marker_bytes(GIT_SHA, ADDON_TREE))
PROJECT_VERSION = tomllib.loads(PROJECT_FILE.read_text(encoding="utf-8"))[
    "project"
]["version"]
BRIDGE_VERSION = EXPECTED_BRIDGE_VERSION
WHEEL_BYTES = b"synthetic wheel bytes for release tests\n"
WHEEL_NAME = f"dayz_mcp_tools-{PROJECT_VERSION}-py3-none-any.whl"
LOCK_TEXT = (
    "# synthetic lock\n"
    "alpha==1.0 \\\n"
    f"    --hash=sha256:{'a' * 64} \\\n"
    f"    --hash=sha256:{'b' * 64}\n"
    "    # via beta\n"
    "beta==2.0 \\\n"
    f"    --hash=sha256:{'c' * 64}\n"
)


class MakeReleaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.release = importlib.import_module("make_release")
            cls.import_error = None
        except ModuleNotFoundError as error:
            cls.release = None
            cls.import_error = error

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()
        self.release_dir = self.root / "release"
        self.release_dir.mkdir()
        self.bikey = self.release_dir / "DayZ_MCP.bikey"
        self.bikey.write_bytes(BIKEY_BYTES)
        self.mod_cpp = self.release_dir / "mod.cpp"
        self.mod_cpp.write_bytes(MOD_CPP_BYTES)
        self.lock_path = self.release_dir / "requirements.lock.txt"
        self.lock_path.write_text(LOCK_TEXT, encoding="utf-8", newline="\n")
        self.wheel_calls: list[dict[str, object]] = []
        self.wheel_error: RuntimeError | None = None
        self.wheel_name_override: str | None = None
        build_dir = self.root / "build"
        build_dir.mkdir()
        self.pbo = build_dir / "DayZ_MCP.pbo"
        self.pbo.write_bytes(PBO_BYTES)
        self.bisign = Path(f"{self.pbo}.DayZ_MCP.bisign")
        self.bisign.write_bytes(BISIGN_BYTES)
        self.out = self.root / "dist"
        self.zip_path = self.out / f"DayZ_MCP-v{PROJECT_VERSION}-addon.zip"
        self.version_path = self.out / "VERSION.json"
        self.wheel_path = self.out / WHEEL_NAME
        self.requirements_path = self.out / "dayz-mcp-requirements.txt"
        self.sums_path = self.out / "SHA256SUMS.txt"
        self.signature_calls: list[tuple[Path, Path, dict[str, bytes]]] = []

    def require_release(self):
        if self.release is None:
            self.fail(f"make_release module is missing: {self.import_error}")
        return self.release

    def _fake_signature_check(self, addons: Path, keys: Path) -> str:
        check_root = addons.parent
        snapshot = {
            path.relative_to(check_root).as_posix(): path.read_bytes()
            for path in sorted(check_root.rglob("*"))
            if path.is_file()
        }
        self.signature_calls.append((addons, keys, snapshot))
        return f"Signature {addons / 'DayZ_MCP.pbo.DayZ_MCP.bisign'} is OK\n"

    def _fake_build_wheel(self, stage: Path, out_dir: Path) -> Path:
        stage = Path(stage)
        out_dir = Path(out_dir)
        self.wheel_calls.append(
            {
                "stage": stage,
                "out_dir": out_dir,
                "version_bytes": (stage / "dayz_mcp" / "VERSION.json").read_bytes(),
                "pyproject_bytes": (stage / "pyproject.toml").read_bytes(),
            }
        )
        if self.wheel_error is not None:
            raise self.wheel_error
        out_dir.mkdir(parents=True, exist_ok=True)
        wheel = out_dir / (self.wheel_name_override or WHEEL_NAME)
        wheel.write_bytes(WHEEL_BYTES)
        return wheel

    def stage(self, **overrides: object) -> tuple[Path, ...]:
        release = self.require_release()
        options = {
            "pbo_path": self.pbo,
            "out_dir": self.out,
            "allow_dirty": False,
            "repo_root": self.repo_root,
            "release_dir": self.release_dir,
            "git_status_fn": lambda _repo: "",
            "git_sha_fn": lambda _repo: GIT_SHA,
            "built_utc_fn": lambda: BUILT_UTC,
            "signature_check_fn": self._fake_signature_check,
        }
        options.update(overrides)
        with mock.patch.object(
            release.build_wheel, "build_wheel", self._fake_build_wheel
        ):
            return release.stage_release(**options)

    def assert_no_assets(self) -> None:
        self.assertFalse(self.zip_path.exists(), "a refused release wrote the zip")
        self.assertFalse(self.version_path.exists(), "a refused release wrote VERSION.json")
        self.assertFalse(self.wheel_path.exists(), "a refused release wrote the wheel")
        self.assertFalse(
            self.requirements_path.exists(),
            "a refused release wrote dayz-mcp-requirements.txt",
        )
        self.assertFalse(self.sums_path.exists(), "a refused release wrote SHA256SUMS.txt")

    def assert_refusal(self, code: str, **overrides: object) -> str:
        release = self.require_release()
        with self.assertRaises(release.ReleaseRefusal) as raised:
            self.stage(**overrides)
        self.assertEqual(raised.exception.code, code)
        self.assert_no_assets()
        return str(raised.exception)

    def test_good_case_zip_sums_version_and_signature_inputs(self) -> None:
        returned = self.stage()
        self.assertEqual(
            returned,
            (
                self.zip_path,
                self.version_path,
                self.wheel_path,
                self.requirements_path,
                self.sums_path,
            ),
        )

        expected_entries = [
            ("@DayZ_MCP/Addons/DayZ_MCP.pbo", PBO_BYTES),
            ("@DayZ_MCP/Addons/DayZ_MCP.pbo.DayZ_MCP.bisign", BISIGN_BYTES),
            ("@DayZ_MCP/Keys/DayZ_MCP.bikey", BIKEY_BYTES),
            ("@DayZ_MCP/mod.cpp", MOD_CPP_BYTES),
        ]
        with zipfile.ZipFile(self.zip_path) as archive:
            self.assertEqual(
                archive.namelist(),
                [name for name, _data in expected_entries] + ["@DayZ_MCP/VERSION.json"],
            )
            for name, data in expected_entries:
                self.assertEqual(archive.read(name), data)
            self.assertEqual(archive.read("@DayZ_MCP/VERSION.json"), self.version_path.read_bytes())
            for item in archive.infolist():
                self.assertEqual(item.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(item.compress_type, zipfile.ZIP_DEFLATED)
                self.assertEqual((item.external_attr >> 16) & 0o777, 0o644)

        lines = self.sums_path.read_text(encoding="ascii").splitlines()
        self.assertEqual(len(lines), 8)
        checksums = {}
        ordered_names = []
        for line in lines:
            digest, separator, name = line.partition("  ")
            self.assertEqual(separator, "  ")
            checksums[name] = digest
            ordered_names.append(name)
        self.assertEqual(
            ordered_names,
            [
                "DayZ_MCP.pbo",
                "DayZ_MCP.pbo.DayZ_MCP.bisign",
                "DayZ_MCP.bikey",
                "mod.cpp",
                self.zip_path.name,
                "VERSION.json",
                WHEEL_NAME,
                "dayz-mcp-requirements.txt",
            ],
        )
        expected = {
            "DayZ_MCP.pbo": hashlib.sha256(PBO_BYTES).hexdigest(),
            "DayZ_MCP.pbo.DayZ_MCP.bisign": hashlib.sha256(BISIGN_BYTES).hexdigest(),
            "DayZ_MCP.bikey": hashlib.sha256(BIKEY_BYTES).hexdigest(),
            "mod.cpp": hashlib.sha256(MOD_CPP_BYTES).hexdigest(),
            self.zip_path.name: hashlib.sha256(self.zip_path.read_bytes()).hexdigest(),
            "VERSION.json": hashlib.sha256(self.version_path.read_bytes()).hexdigest(),
            WHEEL_NAME: hashlib.sha256(self.wheel_path.read_bytes()).hexdigest(),
            "dayz-mcp-requirements.txt": hashlib.sha256(
                self.requirements_path.read_bytes()
            ).hexdigest(),
        }
        self.assertEqual(checksums, expected)

        self.assertEqual(len(self.signature_calls), 1)
        _addons, _keys, snapshot = self.signature_calls[0]
        self.assertEqual(
            snapshot,
            {
                "addons/DayZ_MCP.pbo": PBO_BYTES,
                "addons/DayZ_MCP.pbo.DayZ_MCP.bisign": BISIGN_BYTES,
                "keys/DayZ_MCP.bikey": BIKEY_BYTES,
            },
        )

    def test_zip_has_exact_layout_and_pbo_bytes(self) -> None:
        self.stage()

        with zipfile.ZipFile(self.zip_path) as archive:
            self.assertEqual(
                archive.namelist(),
                [
                    "@DayZ_MCP/Addons/DayZ_MCP.pbo",
                    "@DayZ_MCP/Addons/DayZ_MCP.pbo.DayZ_MCP.bisign",
                    "@DayZ_MCP/Keys/DayZ_MCP.bikey",
                    "@DayZ_MCP/mod.cpp",
                    "@DayZ_MCP/VERSION.json",
                ],
            )
            item = archive.infolist()[0]
            self.assertEqual(archive.read(item), PBO_BYTES)
            self.assertEqual(item.date_time, (1980, 1, 1, 0, 0, 0))
            self.assertEqual(item.compress_type, zipfile.ZIP_DEFLATED)
            self.assertEqual((item.external_attr >> 16) & 0o777, 0o644)

    def test_sha256sums_match_independent_recomputation(self) -> None:
        self.stage()

        checksums = {}
        for line in self.sums_path.read_text(encoding="ascii").splitlines():
            digest, separator, name = line.partition("  ")
            self.assertEqual(separator, "  ")
            checksums[name] = digest
        expected = {
            "DayZ_MCP.pbo": hashlib.sha256(PBO_BYTES).hexdigest(),
            "DayZ_MCP.pbo.DayZ_MCP.bisign": hashlib.sha256(BISIGN_BYTES).hexdigest(),
            "DayZ_MCP.bikey": hashlib.sha256(BIKEY_BYTES).hexdigest(),
            "mod.cpp": hashlib.sha256(MOD_CPP_BYTES).hexdigest(),
            self.zip_path.name: hashlib.sha256(self.zip_path.read_bytes()).hexdigest(),
            "VERSION.json": hashlib.sha256(self.version_path.read_bytes()).hexdigest(),
            WHEEL_NAME: hashlib.sha256(self.wheel_path.read_bytes()).hexdigest(),
            "dayz-mcp-requirements.txt": hashlib.sha256(
                self.requirements_path.read_bytes()
            ).hexdigest(),
        }
        self.assertEqual(checksums, expected)

    def test_version_json_matches_injected_fields(self) -> None:
        self.stage()

        self.assertEqual(
            json.loads(self.version_path.read_text(encoding="utf-8")),
            {
                "version": PROJECT_VERSION,
                "bridge_version": BRIDGE_VERSION,
                "git_sha": GIT_SHA,
                "pbo_sha256": hashlib.sha256(PBO_BYTES).hexdigest(),
                "built_utc": BUILT_UTC,
            },
        )

    def test_repeated_staging_overwrites_assets_byte_identically(self) -> None:
        self.stage()
        assets = (
            self.zip_path,
            self.version_path,
            self.sums_path,
            self.wheel_path,
            self.requirements_path,
        )
        first = {path.name: path.read_bytes() for path in assets}
        for path in assets:
            path.write_bytes(b"stale release output")

        self.stage()

        second = {path.name: path.read_bytes() for path in assets}
        self.assertEqual(second, first)

    def _wheel_line(self) -> str:
        return (
            "dayz-mcp-tools @ "
            "https://github.com/willy92wins/dayz-mcp/releases/download/"
            f"v{PROJECT_VERSION}/{WHEEL_NAME} "
            f"--hash=sha256:{hashlib.sha256(WHEEL_BYTES).hexdigest()}"
        )

    def test_wheel_and_requirements_assets(self) -> None:
        self.stage()

        self.assertEqual(self.wheel_path.name, WHEEL_NAME)
        self.assertEqual(self.wheel_path.read_bytes(), WHEEL_BYTES)
        expected_requirements = LOCK_TEXT + self._wheel_line() + "\n"
        self.assertEqual(
            self.requirements_path.read_bytes(),
            expected_requirements.encode("utf-8"),
        )

        checksums = {}
        ordered_names = []
        for line in self.sums_path.read_text(encoding="ascii").splitlines():
            digest, separator, name = line.partition("  ")
            self.assertEqual(separator, "  ")
            checksums[name] = digest
            ordered_names.append(name)
        self.assertEqual(
            ordered_names,
            [
                "DayZ_MCP.pbo",
                "DayZ_MCP.pbo.DayZ_MCP.bisign",
                "DayZ_MCP.bikey",
                "mod.cpp",
                self.zip_path.name,
                "VERSION.json",
                WHEEL_NAME,
                "dayz-mcp-requirements.txt",
            ],
        )
        expected = {
            "DayZ_MCP.pbo": hashlib.sha256(PBO_BYTES).hexdigest(),
            "DayZ_MCP.pbo.DayZ_MCP.bisign": hashlib.sha256(BISIGN_BYTES).hexdigest(),
            "DayZ_MCP.bikey": hashlib.sha256(BIKEY_BYTES).hexdigest(),
            "mod.cpp": hashlib.sha256(MOD_CPP_BYTES).hexdigest(),
            self.zip_path.name: hashlib.sha256(self.zip_path.read_bytes()).hexdigest(),
            "VERSION.json": hashlib.sha256(self.version_path.read_bytes()).hexdigest(),
            WHEEL_NAME: hashlib.sha256(WHEEL_BYTES).hexdigest(),
            "dayz-mcp-requirements.txt": hashlib.sha256(
                expected_requirements.encode("utf-8")
            ).hexdigest(),
        }
        self.assertEqual(checksums, expected)

        self.assertEqual(len(self.wheel_calls), 1)
        call = self.wheel_calls[0]
        self.assertEqual(call["version_bytes"], self.version_path.read_bytes())
        self.assertEqual(
            call["pyproject_bytes"], (TOOLS_DIR / "pyproject.toml").read_bytes()
        )
        stage_path = call["stage"]
        fake_out_dir = call["out_dir"]
        self.assertIsInstance(stage_path, Path)
        self.assertIsInstance(fake_out_dir, Path)
        self.assertFalse(stage_path.is_relative_to(self.out))
        self.assertFalse(stage_path.is_relative_to(self.repo_root))
        self.assertFalse(fake_out_dir.is_relative_to(self.out))
        self.assertFalse(fake_out_dir.is_relative_to(self.repo_root))

    def test_requirements_normalize_crlf_lock_without_final_newline(self) -> None:
        crlf_lock = LOCK_TEXT.replace("\n", "\r\n").rstrip("\r\n")
        self.lock_path.write_bytes(crlf_lock.encode("utf-8"))

        self.stage()

        requirements = self.requirements_path.read_bytes().decode("utf-8")
        self.assertNotIn("\r", requirements)
        expected = LOCK_TEXT.rstrip("\n") + "\n" + self._wheel_line() + "\n"
        self.assertEqual(requirements, expected)
        self.assertEqual(requirements.splitlines()[-1], self._wheel_line())

    def test_requirements_preserve_non_ascii_lock_comment_in_utf8(self) -> None:
        lock_text = LOCK_TEXT.replace(
            "# synthetic lock\n", "# synthetic lock\n# via pydantic (año)\n", 1
        )
        self.lock_path.write_bytes(lock_text.encode("utf-8"))

        self.stage()

        requirements_bytes = self.requirements_path.read_bytes()
        self.assertIn("# via pydantic (año)\n".encode("utf-8"), requirements_bytes)
        self.assertEqual(
            requirements_bytes,
            (lock_text + self._wheel_line() + "\n").encode("utf-8"),
        )

    def test_requirements_lock_missing_and_empty(self) -> None:
        self.lock_path.unlink()
        message = self.assert_refusal("requirements_lock_missing")
        self.assertIn(
            "; remedy: restore tools/release/requirements.lock.txt "
            "(docs/RELEASE.md, Updating the dependency lock)",
            message,
        )
        self.assertEqual(self.wheel_calls, [])

        self.lock_path.write_bytes(b"")
        self.assert_refusal("requirements_lock_missing")
        self.assertEqual(self.wheel_calls, [])

    def test_wheel_build_failure(self) -> None:
        self.wheel_error = RuntimeError("wheel_build_failed: boom")
        message = self.assert_refusal("wheel_build_failed")
        self.assertIn("boom", message)
        self.assertIn(
            "; remedy: check that pip in this Python can build tools/ "
            "(pip downloads setuptools==84.0.0 for the build), then rerun",
            message,
        )

    def test_wheel_name_unexpected(self) -> None:
        self.wheel_name_override = "dayz_mcp_tools-0.0-py3-none-any.whl"
        message = self.assert_refusal("wheel_name_unexpected")
        self.assertIn(
            "; remedy: build the wheel from tools/pyproject.toml at this commit",
            message,
        )

    def test_bisign_missing_does_not_build_wheel(self) -> None:
        self.bisign.unlink()
        self.assert_refusal("bisign_missing")
        self.assertEqual(self.wheel_calls, [])

    def test_main_prints_five_assets_in_order(self) -> None:
        release = self.require_release()
        assets = (
            self.root / "one.zip",
            self.root / "VERSION.json",
            self.root / "wheel.whl",
            self.root / "dayz-mcp-requirements.txt",
            self.root / "SHA256SUMS.txt",
        )

        def fake_stage_release(**_kwargs: object) -> tuple[Path, ...]:
            return assets

        stdout = io.StringIO()
        with mock.patch.object(release, "stage_release", fake_stage_release):
            with contextlib.redirect_stdout(stdout):
                code = release.main(
                    ["--pbo", str(self.pbo), "--out", str(self.out)]
                )
        self.assertEqual(code, 0)
        self.assertEqual(
            stdout.getvalue().splitlines(), [str(asset) for asset in assets]
        )

    def test_build_release_wheel_uses_staged_tree_and_wheelhouse(self) -> None:
        release = self.require_release()
        work_dir = self.root / "wheel-work"
        work_dir.mkdir()
        sentinel = work_dir / "sentinel.whl"
        calls: list[tuple[Path, Path]] = []

        def fake_build_wheel(stage: Path, out_dir: Path) -> Path:
            calls.append((Path(stage), Path(out_dir)))
            return sentinel

        version_bytes = b'{"version": "synthetic"}\n'
        with mock.patch.object(release.build_wheel, "build_wheel", fake_build_wheel):
            result = release.build_release_wheel(version_bytes, work_dir)

        self.assertEqual(result, sentinel)
        self.assertEqual(
            calls, [(work_dir / "stage", work_dir / "wheelhouse")]
        )
        self.assertEqual(
            (work_dir / "stage" / "dayz_mcp" / "VERSION.json").read_bytes(),
            version_bytes,
        )
        playbooks_root = REPO_ROOT / "playbooks"
        expected_playbooks = {}
        for source in sorted(playbooks_root.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(playbooks_root)
            if "__pycache__" in relative.parts or source.suffix == ".pyc":
                continue
            expected_playbooks[relative] = source.read_bytes()
        self.assertTrue(expected_playbooks)
        staged_playbooks = calls[0][0] / "dayz_mcp" / "playbooks"
        self.assertEqual(
            {
                path.relative_to(staged_playbooks): path.read_bytes()
                for path in sorted(staged_playbooks.rglob("*"))
                if path.is_file()
            },
            expected_playbooks,
        )

    def test_private_key_present_nested_under_repo_root(self) -> None:
        nested = self.repo_root / "deep" / "deeper"
        nested.mkdir(parents=True)
        (nested / "DayZ_MCP.BIPRIVATEKEY").write_bytes(b"not read")
        self.assert_refusal("private_key_present")
        self.assertEqual(self.signature_calls, [])

    def test_private_key_present_beside_pbo(self) -> None:
        (self.pbo.parent / "other.biprivatekey").write_bytes(b"not read")
        message = self.assert_refusal("private_key_present")
        self.assertIn("other.biprivatekey", message)
        self.assertEqual(self.signature_calls, [])

    def test_private_key_present_under_out(self) -> None:
        nested = self.out / "nested"
        nested.mkdir(parents=True)
        (nested / "key.biprivatekey").write_bytes(b"not read")
        self.assert_refusal("private_key_present")
        self.assertEqual(self.signature_calls, [])

    def test_release_key_missing_absent_and_empty(self) -> None:
        self.bikey.unlink()
        self.assert_refusal("release_key_missing")
        self.assertEqual(self.signature_calls, [])
        self.bikey.write_bytes(b"")
        self.assert_refusal("release_key_missing")
        self.assertEqual(self.signature_calls, [])

    def test_mod_cpp_missing_absent_and_empty(self) -> None:
        self.mod_cpp.unlink()
        self.assert_refusal("mod_cpp_missing")
        self.assertEqual(self.signature_calls, [])
        self.mod_cpp.write_bytes(b"")
        self.assert_refusal("mod_cpp_missing")
        self.assertEqual(self.signature_calls, [])

    def test_bisign_missing_and_other_name_does_not_count(self) -> None:
        self.bisign.unlink()
        self.assert_refusal("bisign_missing")
        (self.pbo.parent / "DayZ_MCP.pbo.Other.bisign").write_bytes(BISIGN_BYTES)
        self.assert_refusal("bisign_missing")
        self.assertEqual(self.signature_calls, [])

    def test_bisign_wrong_key_other_key_and_short_bisign(self) -> None:
        self.bisign.write_bytes(b"OtherKey\x00synthetic-signature")
        self.assert_refusal("bisign_wrong_key")
        self.assertEqual(self.signature_calls, [])
        self.bisign.write_bytes(BIKEY_BYTES[:4])
        self.assert_refusal("bisign_wrong_key")
        self.assertEqual(self.signature_calls, [])
        self.bisign.write_bytes(b"DayZ_MCP\x00rotated-key-signature")
        self.assert_refusal("bisign_wrong_key")
        self.assertEqual(self.signature_calls, [])

    def test_bisign_invalid_outputs(self) -> None:
        def output_fn(text_fn):
            return lambda addons, keys: text_fn(addons)

        cases = {
            "wrong": lambda addons: f"Signature {addons / 'DayZ_MCP.pbo.DayZ_MCP.bisign'} is wrong\n",
            "key_not_found": lambda addons: f"Key not found for signature {addons / 'DayZ_MCP.pbo.DayZ_MCP.bisign'}\n",
            "signature_not_found": lambda addons: "Signature not found\n",
            "empty": lambda addons: "",
            "two_ok": lambda addons: (
                f"Signature {addons / 'DayZ_MCP.pbo.DayZ_MCP.bisign'} is OK\n"
                f"Signature {addons / 'DayZ_MCP.pbo.DayZ_MCP.bisign'} is OK\n"
            ),
            "other_file": lambda addons: f"Signature {addons / 'Other.pbo.DayZ_MCP.bisign'} is OK\n",
            "ok_and_wrong": lambda addons: (
                f"Signature {addons / 'DayZ_MCP.pbo.DayZ_MCP.bisign'} is OK\n"
                f"Signature {addons / 'DayZ_MCP.pbo.DayZ_MCP.bisign'} is wrong\n"
            ),
            "ok_and_key_not_found": lambda addons: (
                f"Signature {addons / 'DayZ_MCP.pbo.DayZ_MCP.bisign'} is OK\n"
                f"Key not found for signature {addons / 'DayZ_MCP.pbo.DayZ_MCP.bisign'}\n"
            ),
            "ok_and_signature_not_found": lambda addons: (
                f"Signature {addons / 'DayZ_MCP.pbo.DayZ_MCP.bisign'} is OK\n"
                "Signature not found\n"
            ),
        }
        for name, text_fn in cases.items():
            with self.subTest(case=name):
                self.signature_calls.clear()
                self.assert_refusal(
                    "bisign_invalid", signature_check_fn=output_fn(text_fn)
                )

    def test_signature_check_unavailable_with_real_function(self) -> None:
        release = self.require_release()
        addons = self.root / "check-addons"
        keys = self.root / "check-keys"
        addons.mkdir()
        keys.mkdir()

        empty_root = self.root / "no-tools"
        empty_root.mkdir()
        with self.assertRaises(release.ReleaseRefusal) as raised:
            release.run_signature_check(addons, keys, tools_roots=[empty_root])
        self.assertEqual(raised.exception.code, "signature_check_unavailable")
        self.assertIn("install DayZ Tools or set DAYZ_TOOLS_PATH", str(raised.exception))

        tools_root = self.root / "tools-root"
        exe = tools_root / "Bin" / "DsUtils" / "DSCheckSignatures.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"fake exe")

        def raises_oserror(*_args, **_kwargs):
            raise OSError("cannot launch")

        with self.assertRaises(release.ReleaseRefusal) as raised:
            release.run_signature_check(
                addons, keys, tools_roots=[tools_root], run_fn=raises_oserror
            )
        self.assertEqual(raised.exception.code, "signature_check_unavailable")

        def raises_timeout(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("DSCheckSignatures.exe", 120)

        with self.assertRaises(release.ReleaseRefusal) as raised:
            release.run_signature_check(
                addons, keys, tools_roots=[tools_root], run_fn=raises_timeout
            )
        self.assertEqual(raised.exception.code, "signature_check_unavailable")

    def test_pbo_with_broken_final_sha1_is_unreadable_without_signature_check(self) -> None:
        broken = PBO_BYTES[:-1] + bytes([PBO_BYTES[-1] ^ 0xFF])
        self.assert_refused("pbo_unreadable", broken)
        self.assertEqual(self.signature_calls, [])

    def test_main_with_missing_bisign_exits_2(self) -> None:
        release = self.require_release()
        self.bisign.unlink()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = release.main(
                [
                    "--pbo",
                    str(self.pbo),
                    "--out",
                    str(self.root / "main-dist"),
                    "--allow-dirty",
                ]
            )
        self.assertEqual(code, 2)
        self.assertTrue(stderr.getvalue().startswith("bisign_missing"))

    def test_real_release_files_and_gitignore(self) -> None:
        release = self.require_release()
        real_bikey = TOOLS_DIR / "release" / "DayZ_MCP.bikey"
        self.assertTrue(real_bikey.is_file())
        self.assertTrue(real_bikey.read_bytes().startswith(b"DayZ_MCP\x00"))
        real_mod_cpp = TOOLS_DIR / "release" / "mod.cpp"
        self.assertIn(
            'name = "DayZ-MCP";',
            real_mod_cpp.read_text(encoding="ascii").splitlines(),
        )
        gitignore_lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("*.biprivatekey", gitignore_lines)
        self.assertTrue(hasattr(release, "ZIP_ENTRY_PATHS"))

        git = shutil.which("git")
        if git is None:
            self.skipTest("git is not available")
        completed = subprocess.run(
            [git, "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            self.skipTest("git ls-files is not available")
        listed = completed.stdout.splitlines()
        self.assertFalse(
            any(name.casefold().endswith(".biprivatekey") for name in listed)
        )

    def test_release_doc_step4_counts_match_constants(self) -> None:
        release = self.require_release()
        text = RELEASE_DOC.read_text(encoding="utf-8")
        step4 = text.split("## 4.", 1)[1].split("## 5.", 1)[0]
        lines = step4.splitlines()

        def only_count(prefix: str) -> int:
            matches = [
                line for line in lines if line.lstrip().startswith(prefix)
            ]
            self.assertEqual(
                len(matches), 1, f"step 4 must have exactly one line starting {prefix!r}"
            )
            match = re.search(r"-ne (\d+)", matches[0])
            self.assertIsNotNone(match, f"step 4 line starting {prefix!r} has no count")
            return int(match.group(1))

        self.assertEqual(
            only_count("if ($ExtractedFiles.Count -ne"), len(release.ZIP_ENTRY_PATHS)
        )
        self.assertEqual(
            only_count("if ($Expected.Count -ne"),
            len(release.SHA256SUMS_ENTRY_NAMES) + 3,
        )

    def test_dirty_tree_refusal_names_field_and_remedy(self) -> None:
        release = self.require_release()

        with self.assertRaises(release.ReleaseRefusal) as raised:
            self.stage(git_status_fn=lambda _repo: "?? local-output.txt")

        self.assertEqual(raised.exception.code, "dirty_tree")
        self.assertIn("--allow-dirty", str(raised.exception))

    def test_allow_dirty_bypasses_dirty_tree_refusal(self) -> None:
        self.stage(
            allow_dirty=True,
            git_status_fn=lambda _repo: "?? local-output.txt",
        )

        self.assertTrue(self.zip_path.is_file())

    def test_missing_pbo_refusal_names_field_and_remedy(self) -> None:
        release = self.require_release()

        with self.assertRaises(release.ReleaseRefusal) as raised:
            self.stage(
                pbo_path=self.root / "missing.pbo",
                git_status_fn=lambda _repo: self.fail(
                    "git status must not run before the PBO input is validated"
                ),
            )

        self.assertEqual(raised.exception.code, "pbo_missing")
        self.assertIn("--pbo", str(raised.exception))

    def assert_refused(self, code: str, pbo_bytes: bytes, **overrides: object) -> str:
        release = self.require_release()
        self.pbo.write_bytes(pbo_bytes)

        with self.assertRaises(release.ReleaseRefusal) as raised:
            self.stage(**overrides)

        self.assertEqual(raised.exception.code, code)
        self.assert_no_assets()
        return str(raised.exception)

    def test_pbo_built_from_another_commit_is_refused(self) -> None:
        message = self.assert_refused(
            "pbo_commit_mismatch", _pbo(marker_bytes(OTHER_SHA, ADDON_TREE))
        )

        self.assertIn(OTHER_SHA, message)
        self.assertIn(GIT_SHA, message)
        self.assertIn("tools/pack-addon.ps1", message)

    def test_pbo_built_from_a_folder_is_refused(self) -> None:
        message = self.assert_refused(
            "pbo_commit_mismatch", _pbo(marker_bytes(None, None, source="folder"))
        )

        self.assertIn("-Source", message)

    def test_allow_dirty_does_not_bypass_the_build_commit_check(self) -> None:
        self.assert_refused(
            "pbo_commit_mismatch",
            _pbo(marker_bytes(OTHER_SHA, ADDON_TREE)),
            allow_dirty=True,
            git_status_fn=lambda _repo: "?? local-output.txt",
        )

    def test_pbo_without_a_build_marker_is_refused(self) -> None:
        message = self.assert_refused("pbo_marker_missing", _pbo(None))

        self.assertIn("mcp_build.json", message)

    def test_malformed_build_marker_is_refused(self) -> None:
        self.assert_refused(
            "pbo_marker_invalid", _pbo(b'{"commit":"' + GIT_SHA.encode() + b'"}\n')
        )

    def test_bytes_that_are_not_a_pbo_are_refused(self) -> None:
        message = self.assert_refused("pbo_unreadable", OPAQUE_BYTES)

        self.assertIn("--pbo", message)

    def test_parsers_watch_real_workspace_files(self) -> None:
        release = self.require_release()

        self.assertEqual(release.read_project_version(PROJECT_FILE), PROJECT_VERSION)
        self.assertEqual(release.read_bridge_version(BRIDGE_FILE), BRIDGE_VERSION)

    def test_parsers_refuse_missing_or_duplicate_exact_lines(self) -> None:
        release = self.require_release()
        cases = (
            (
                release.read_project_version,
                'version="1.2.3"\n',
                "version_parse_failed",
            ),
            (
                release.read_project_version,
                'version = "1.2.3"\nversion = "4.5.6"\n',
                "version_parse_failed",
            ),
            (
                release.read_bridge_version,
                'const string MCP_BRIDGE_VERSION="9";\n',
                "bridge_version_parse_failed",
            ),
            (
                release.read_bridge_version,
                'const string MCP_BRIDGE_VERSION = "9";\n'
                'const string MCP_BRIDGE_VERSION = "11";\n',
                "bridge_version_parse_failed",
            ),
        )
        for index, (reader, content, code) in enumerate(cases):
            with self.subTest(index=index, code=code):
                source = self.root / f"source-{index}.txt"
                source.write_text(content, encoding="utf-8")
                with self.assertRaises(release.ReleaseRefusal) as raised:
                    reader(source)
                self.assertEqual(raised.exception.code, code)
                self.assertIn("exactly one", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
