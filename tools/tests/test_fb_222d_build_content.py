"""Offline checks for the sealed AddonBuilder include list and $PBOPREFIX$ namespace.

The harness compiles launcher.cpp. It does not start AddonBuilder or the sealed entry point.
"""

from __future__ import annotations

import atexit
import asyncio
import ctypes
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import unittest
import unittest.mock
from contextlib import nullcontext
from pathlib import Path

from dayz_mcp import dayz_test_request, dayz_test_tool, dayz_test_worker, native_broker_protocol

import build_native_launcher

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
LAUNCHER = TOOLS / "native-launchers" / "dayz-test-v1" / "src" / "launcher.cpp"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fb_222d"
HARNESS = FIXTURES / "harness.cpp"
ROOTS = FIXTURES / "addon_roots.h"
INCLUDE = build_native_launcher.ADDONBUILDER_INCLUDE_BYTES
PATTERNS = (
    "*.c", "*.layout", "*.imageset", "*.xml", "*.csv", "*.paa", "*.rvmat", "*.json",
    "*.ogg", "*.wav", "*.wss", "*.edds", "*.bisurf", "*.ptc", "*.emat",
)
MANIFEST = b'{"format_version":1}\n'
ADDON = r"C:\DayZ Tools\Bin\AddonBuilder\AddonBuilder.exe"
SCRATCH = ROOT / "_scratch" / "fb222d"
atexit.register(lambda: shutil.rmtree(SCRATCH, ignore_errors=True))


def _sha(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _cpp_wstring(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _hex_bytes(data: bytes) -> str:
    return ",".join(f"0x{byte:02X}" for byte in data)


def _closure_header(entries: list[tuple[str, str, bytes]]) -> str:
    lines = [
        "#pragma once",
        "#include <windows.h>",
        "#include <stdint.h>",
        "enum class ClosureKind : BYTE { BUNDLE = 1, EXTERNAL = 2 };",
        "struct ClosureEntry { ClosureKind kind; const wchar_t* path; uint64_t size; BYTE sha256[32]; bool require_identity; uint64_t volume_serial_number; BYTE file_id[16]; };",
        f'#define DAYZ_MCP_MANIFEST_SHA256_HEX "{hashlib.sha256(MANIFEST).hexdigest().upper()}"',
        "inline constexpr ClosureEntry kClosureEntries[] = {",
    ]
    zeros = _hex_bytes(b"\x00" * 16)
    for kind, path, data in entries:
        lines.append(
            '    {ClosureKind::%s, L"%s", %dULL, {%s}, false, 0ULL, {%s}},'
            % (kind, _cpp_wstring(path), len(data), _hex_bytes(_sha(data)), zeros)
        )
    lines.extend([
        "};",
        "inline constexpr DWORD kClosureEntryCount = ARRAYSIZE(kClosureEntries);",
        "",
    ])
    return "\n".join(lines)


def _vcvars() -> Path:
    program_files = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(program_files) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        raise AssertionError(f"MSVC is required and vswhere.exe is missing at {vswhere}")
    completed = subprocess.run(
        [
            str(vswhere), "-latest", "-products", "*",
            "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-find", r"VC\Auxiliary\Build\vcvars64.bat",
        ],
        capture_output=True, text=True, timeout=60,
    )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or not lines or not Path(lines[0]).is_file():
        raise AssertionError("MSVC vcvars64.bat is missing; the 222d harness cannot compile")
    return Path(lines[0])


def _compile(bundle: Path, header: Path) -> Path:
    obj = bundle.parent / (bundle.name + "-obj")
    obj.mkdir(parents=True, exist_ok=True)
    bundle.mkdir(parents=True, exist_ok=True)
    script = obj / "compile.cmd"
    exe = bundle / "dayz-test-launcher.exe"
    script.write_text(
        "\r\n".join([
            "@echo off",
            f'call "{_vcvars()}"',
            "if errorlevel 1 exit /b 1",
            "cl /nologo /std:c++17 /EHsc /W3 /DUNICODE /D_UNICODE /DDAYZ_MCP_LAUNCHER_TEST "
            f'/FI "{ROOTS}" /FI "{header}" /Fo"{obj}/" /Fe"{exe}" '
            f'"{LAUNCHER}" "{HARNESS}" /link bcrypt.lib',
        ]),
        encoding="ascii",
    )
    completed = subprocess.run(
        ["cmd", "/c", str(script)],
        cwd=obj,
        capture_output=True,
        timeout=180,
    )
    if completed.returncode != 0 or not exe.is_file():
        stdout = completed.stdout.decode("utf-8", "replace") if isinstance(completed.stdout, bytes) else completed.stdout
        stderr = completed.stderr.decode("utf-8", "replace") if isinstance(completed.stderr, bytes) else completed.stderr
        raise AssertionError("harness compile failed\n" + stdout + "\n" + stderr)
    return exe


def _run(exe: Path, args: list[str], cwd: Path) -> tuple[int, dict[str, str], str]:
    completed = subprocess.run(
        [str(exe), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    values: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return completed.returncode, values, completed.stderr


def _junction(target: Path, link: Path) -> None:
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.decode("utf-8", "replace") or completed.stdout)


def _expect(source: str, target: str, namespace: str, temp: str, include: str, clear: bool, pack_only: bool) -> str:
    text = f'"{ADDON}" "{source}" "{target}" "-prefix={namespace}" "-temp={temp}"'
    if not pack_only:
        text += f' "-include={include}"'
    if clear:
        text += " -clear"
    if pack_only:
        text += " -packonly"
    return text


class IncludeListBytesTest(unittest.TestCase):
    def test_bytes_are_the_sealed_literal(self) -> None:
        self.assertEqual(INCLUDE, b";".join(pattern.encode("ascii") for pattern in PATTERNS) + b"\n")
        self.assertFalse(INCLUDE.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(INCLUDE.endswith(b"\n"))
        self.assertNotIn(b" ", INCLUDE)
        self.assertEqual(INCLUDE.decode("ascii"), INCLUDE.decode("ascii"))

    def test_dropping_one_required_pattern_is_rejected(self) -> None:
        for pattern in PATTERNS:
            with self.subTest(pattern=pattern):
                kept = [item for item in PATTERNS if item != pattern]
                mutated = (";".join(kept) + "\n").encode("ascii")
                self.assertNotEqual(mutated, INCLUDE)
                self.assertNotIn(pattern.encode("ascii"), mutated.split(b";"))

    def test_binarizable_patterns_are_outside_the_direct_copy_list(self) -> None:
        for extra in (b"*.p3d", b"config.cpp", b"model.cfg"):
            with self.subTest(extra=extra):
                mutated = INCLUDE[:-1] + b";" + extra + b"\n"
                self.assertNotEqual(mutated, INCLUDE)
                self.assertNotIn(extra, INCLUDE)

    def test_include_is_written_before_the_manifest(self) -> None:
        source = (TOOLS / "build_native_launcher.py").read_text(encoding="utf-8")
        staging = source[source.index("def _prepare_staging"):source.index("def _artifact_fingerprint")]
        self.assertLess(staging.index("write_addonbuilder_include(staging)"), staging.index("manifest = _manifest(staging)"))
        swapped = staging.replace(
            "write_addonbuilder_include(staging)\n    manifest = _manifest(staging)",
            "manifest = _manifest(staging)\n    write_addonbuilder_include(staging)",
        )
        self.assertGreater(
            swapped.index("write_addonbuilder_include(staging)"),
            swapped.index("manifest = _manifest(staging)"),
        )


class LauncherHarnessTest(unittest.TestCase):
    exe: Path
    bundle: Path
    other: dict[str, Path]

    @classmethod
    def setUpClass(cls) -> None:
        if SCRATCH.exists():
            shutil.rmtree(SCRATCH)
        SCRATCH.mkdir(parents=True)
        cls.bundle = SCRATCH / "bundle dir"
        header = SCRATCH / "good.h"
        header.write_text(
            _closure_header([("BUNDLE", "addonbuilder-include.lst", INCLUDE)]),
            encoding="ascii",
        )
        cls.exe = _compile(cls.bundle, header)
        (cls.bundle / "closure-manifest.json").write_bytes(MANIFEST)
        (cls.bundle / "addonbuilder-include.lst").write_bytes(INCLUDE)
        cls.other = {}
        altered = bytearray(INCLUDE)
        altered[2] = ord("d")
        cls.other["altered"] = cls._variant(
            "altered",
            [("BUNDLE", "addonbuilder-include.lst", bytes(altered))],
            {"addonbuilder-include.lst": bytes(altered)},
        )
        cls.other["absent"] = cls._variant(
            "absent",
            [("BUNDLE", "bundle-marker.txt", b"marker\n")],
            {"bundle-marker.txt": b"marker\n"},
        )
        cls.other["duplicate"] = cls._variant(
            "duplicate",
            [
                ("BUNDLE", "addonbuilder-include.lst", INCLUDE),
                ("BUNDLE", "addonbuilder-include.lst", INCLUDE),
            ],
            {"addonbuilder-include.lst": INCLUDE},
        )
        external = SCRATCH / "outside" / "addonbuilder-include.lst"
        external.parent.mkdir()
        external.write_bytes(INCLUDE)
        cls.other["external"] = cls._variant(
            "external",
            [("EXTERNAL", str(external), INCLUDE)],
            {},
        )
        cls.other["nested"] = cls._variant(
            "nested",
            [("BUNDLE", r"nested\addonbuilder-include.lst", INCLUDE)],
            {},
        )
        nested_dir = SCRATCH / "nested" / "nested"
        nested_dir.mkdir(parents=True)
        (nested_dir / "addonbuilder-include.lst").write_bytes(INCLUDE)

    @classmethod
    def _variant(cls, name: str, entries: list[tuple[str, str, bytes]], files: dict[str, bytes]) -> Path:
        bundle = SCRATCH / name
        header = SCRATCH / f"{name}.h"
        header.write_text(_closure_header(entries), encoding="ascii")
        exe = _compile(bundle, header)
        (bundle / "closure-manifest.json").write_bytes(MANIFEST)
        for filename, data in files.items():
            (bundle / filename).write_bytes(data)
        return exe

    @classmethod
    def tearDownClass(cls) -> None:
        # SCRATCH stays until process exit so later tests in this module can
        # reuse the compiled harness. atexit removes it.
        return None

    def _cwd(self) -> Path:
        path = SCRATCH / "cwd"
        path.mkdir(exist_ok=True)
        return path

    def test_good_closure_pins_one_include_list(self) -> None:
        code, values, err = _run(self.exe, ["validate"], self._cwd())
        self.assertEqual(err, "")
        self.assertEqual(code, 0, values)
        self.assertEqual(values["ok"], "1")
        self.assertEqual(values["include_open"], "1")

    def test_include_rejections_are_separate(self) -> None:
        for name in ("absent", "duplicate", "external", "altered", "nested"):
            with self.subTest(kind=name):
                code, values, _err = _run(self.other[name], ["validate"], self._cwd())
                self.assertNotEqual(code, 0)
                self.assertEqual(values["ok"], "0")

    def test_missing_reparse_and_hardlink_include_files_fail(self) -> None:
        missing = SCRATCH / "missing-file"
        shutil.copytree(self.bundle, missing, dirs_exist_ok=True)
        (missing / "addonbuilder-include.lst").unlink()
        code, values, _err = _run(missing / "dayz-test-launcher.exe", ["validate"], self._cwd())
        self.assertNotEqual(code, 0)
        self.assertEqual(values["ok"], "0")

        linked = SCRATCH / "hardlink"
        shutil.copytree(self.bundle, linked, dirs_exist_ok=True)
        os.link(linked / "addonbuilder-include.lst", SCRATCH / "include-alias.lst")
        code, values, _err = _run(linked / "dayz-test-launcher.exe", ["validate"], self._cwd())
        self.assertNotEqual(code, 0, values)

        reparse = SCRATCH / "reparse"
        shutil.copytree(self.bundle, reparse, dirs_exist_ok=True)
        target = SCRATCH / "reparse-target"
        target.mkdir()
        (reparse / "addonbuilder-include.lst").unlink()
        _junction(target, reparse / "addonbuilder-include.lst")
        code, values, _err = _run(reparse / "dayz-test-launcher.exe", ["validate"], self._cwd())
        self.assertNotEqual(code, 0, values)

    def test_include_stays_pinned_while_the_command_is_built(self) -> None:
        source = SCRATCH / "LFHeli"
        source.mkdir(exist_ok=True)
        (source / "$PBOPREFIX$").write_bytes(b"LFHeli\n")
        target = r"C:\out\Addons"
        temp = r"C:\tmp\build"
        code, values, err = _run(
            self.exe,
            ["pinned-build", "1", "0", "LFHeli_OH1", str(source), target, temp, ADDON],
            self._cwd(),
        )
        self.assertEqual(err, "")
        self.assertEqual(code, 0, values)
        self.assertEqual(values["include_open"], "1")
        self.assertEqual(values["prefix_open"], "1")
        include = str(self.bundle / "addonbuilder-include.lst")
        self.assertEqual(
            values["command"],
            _expect(str(source), target, "LFHeli", temp, include, True, False),
        )
        self.assertEqual(values["pbo"], target + "\\LFHeli_OH1.pbo")
        self.assertNotIn("LFHeli.pbo", values["pbo"])

        code, values, _err = _run(
            self.exe,
            ["unpinned-build", "0", "0", "LFHeli_OH1", str(source), target, temp, ADDON],
            self._cwd(),
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(values["ok"], "0")

    def test_packonly_uses_the_namespace_and_omits_include(self) -> None:
        source = SCRATCH / "packonly-mod"
        source.mkdir()
        (source / "$PBOPREFIX$").write_bytes(b"LFHeli\n")
        code, values, _err = _run(
            self.exe,
            ["pinned-build", "0", "1", "LFHeli_OH1", str(source), r"C:\out\Addons", r"C:\tmp\build", ADDON],
            self._cwd(),
        )
        self.assertEqual(code, 0, values)
        self.assertNotIn("-include=", values["command"])
        self.assertTrue(values["command"].endswith(" -packonly"))
        self.assertIn('"-prefix=LFHeli"', values["command"])
        self.assertEqual(values["pbo"], r"C:\out\Addons\LFHeli_OH1.pbo")

    def test_quoting_order_and_clear(self) -> None:
        source = SCRATCH / "quote" / "LFHeli"
        source.mkdir(parents=True, exist_ok=True)
        (source / "$PBOPREFIX$").write_bytes(b"LFHeli\n")
        target = r"C:\out dir\Addons"
        temp = r"C:\tmp dir\build"
        include = str(self.bundle / "addonbuilder-include.lst")
        for clear, pack_only in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(clear=clear, pack_only=pack_only):
                code, values, _err = _run(
                    self.exe,
                    ["pinned-build", "1" if clear else "0", "1" if pack_only else "0",
                     "LFHeli_OH1", str(source), target, temp, ADDON],
                    self._cwd(),
                )
                self.assertEqual(code, 0, values)
                expected = _expect(str(source), target, "LFHeli", temp, include, clear, pack_only)
                self.assertEqual(values["command"], expected)
                swapped = expected.replace('"-temp=', '"-prefix=', 1)
                self.assertNotEqual(values["command"], swapped)
                if not pack_only:
                    self.assertNotEqual(values["command"].replace(' "-include=', "", 1), values["command"])

    def test_valid_markers_and_fallback(self) -> None:
        cases = {
            "plain": (b"LFHeli", "LFHeli", 1),
            "bom": (b"\xef\xbb\xbfLFHeli", "LFHeli", 1),
            "lf": (b"LFHeli\n", "LFHeli", 1),
            "crlf": (b"LFHeli\r\n", "LFHeli", 1),
            "bom_crlf": (b"\xef\xbb\xbfLFHeli\r\n", "LFHeli", 1),
            "slash": (b"LFHeli/Data", "LFHeli\\Data", 1),
            "backslash": (b"LFHeli\\Data", "LFHeli\\Data", 1),
            "underscore": (b"_A", "_A", 1),
            "digit": (b"1A", "1A", 1),
            "hyphen": (b"A-b_1", "A-b_1", 1),
            "segment64": (b"A" + b"b" * 63, "A" + "b" * 63, 1),
            "total255": (
                b"A" * 64 + b"\\" + b"B" * 64 + b"\\" + b"C" * 64 + b"\\" + b"D" * 60,
                "A" * 64 + "\\" + "B" * 64 + "\\" + "C" * 64 + "\\" + "D" * 60,
                1,
            ),
            "missing": (None, "LFHeli_OH1", 2),
        }
        for name, (payload, expected, status) in cases.items():
            with self.subTest(marker=name):
                directory = SCRATCH / "valid" / name
                directory.mkdir(parents=True)
                if payload is not None:
                    (directory / "$PBOPREFIX$").write_bytes(payload)
                code, values, _err = _run(self.exe, ["namespace", "LFHeli_OH1", str(directory)], self._cwd())
                self.assertEqual(code, 0, values)
                self.assertEqual(values["status"], str(status))
                self.assertEqual(values["namespace"], expected)

    def test_invalid_markers_do_not_fall_back(self) -> None:
        segment65 = b"A" + b"b" * 64
        total256 = b"A" * 64 + b"\\" + b"B" * 64 + b"\\" + b"C" * 64 + b"\\" + b"D" * 61
        payloads = {
            "empty": b"",
            "newline": b"\n",
            "spaces": b"   ",
            "trailing_space": b"LFHeli \n",
            "too_long": b"A" * 513,
            "total256": total256,
            "segment65": segment65,
            "dotdot": b"..",
            "dot": b".",
            "traversal": b"A\\..\\B",
            "root": b"\\LFHeli",
            "unc": b"\\\\server\\share",
            "drive": b"C:\\Foo",
            "drive_relative": b"C:Foo",
            "ads": b"Foo:bar",
            "quote": b'Foo"bar',
            "space": b"Foo bar",
            "tab": b"Foo\tbar",
            "embedded_nul": b"Foo\x00bar",
            "lines": b"A\nB\n",
            "utf16": b"\xff\xfeL\x00",
            "high": b"\x80",
            "lead_hyphen": b"-A",
            "empty_segment": b"A\\\\B",
            "trailing_sep": b"A/",
        }
        for name, payload in payloads.items():
            with self.subTest(marker=name):
                directory = SCRATCH / "invalid" / name
                directory.mkdir(parents=True)
                (directory / "$PBOPREFIX$").write_bytes(payload)
                code, values, _err = _run(self.exe, ["namespace", "LFHeli_OH1", str(directory)], self._cwd())
                self.assertNotEqual(code, 0, values)
                self.assertEqual(values.get("status"), "0")
                self.assertNotEqual(values.get("namespace"), "LFHeli_OH1")

    def test_marker_reparse_and_hardlink_do_not_fall_back(self) -> None:
        target = SCRATCH / "marker-target.txt"
        target.write_bytes(b"LFHeli\n")
        linked = SCRATCH / "marker-link"
        linked.mkdir()
        os.link(target, linked / "$PBOPREFIX$")
        os.link(target, SCRATCH / "marker-alias.txt")
        code, values, _err = _run(self.exe, ["namespace", "LFHeli_OH1", str(linked)], self._cwd())
        self.assertEqual(values.get("status"), "0")
        self.assertNotEqual(code, 0)

        via = SCRATCH / "marker-junction"
        via.mkdir()
        real = SCRATCH / "marker-junction-real"
        real.mkdir()
        _junction(real, via / "$PBOPREFIX$")
        code, values, _err = _run(self.exe, ["namespace", "LFHeli_OH1", str(via)], self._cwd())
        self.assertEqual(values.get("status"), "0")
        self.assertNotEqual(code, 0)

        as_dir = SCRATCH / "marker-dir"
        (as_dir / "$PBOPREFIX$").mkdir(parents=True)
        code, values, _err = _run(self.exe, ["namespace", "LFHeli_OH1", str(as_dir)], self._cwd())
        self.assertEqual(values.get("status"), "0")

    def test_missing_source_does_not_fall_back(self) -> None:
        missing = SCRATCH / "no-such-source"
        code, values, _err = _run(self.exe, ["namespace", "LFHeli_OH1", str(missing)], self._cwd())
        self.assertNotEqual(code, 0)
        self.assertEqual(values.get("status"), "0")
        self.assertNotEqual(values.get("namespace"), "LFHeli_OH1")
        as_file = SCRATCH / "source-file"
        as_file.write_bytes(b"x")
        code, values, _err = _run(self.exe, ["namespace", "LFHeli_OH1", str(as_file)], self._cwd())
        self.assertEqual(values.get("status"), "0")

    def test_junction_source_is_accepted(self) -> None:
        real = SCRATCH / "junction-real"
        real.mkdir()
        (real / "$PBOPREFIX$").write_bytes(b"LFHeli\n")
        link = SCRATCH / "junction-link"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)], check=True, capture_output=True)
        code, values, _err = _run(self.exe, ["namespace", "LFHeli_OH1", str(link)], self._cwd())
        self.assertEqual(code, 0, values)
        self.assertEqual(values["status"], "1")
        self.assertEqual(values["namespace"], "LFHeli")

    def test_launch_path_connects_namespace_before_the_child(self) -> None:
        source = LAUNCHER.read_text(encoding="utf-8")
        launch = source[source.index("BOOL LaunchApprovedChild"):source.index('extern "C" void __cdecl wWinMainCRTStartup')]
        self.assertIn("&pbo_prefix_pin.file", launch)
        self.assertLess(launch.index("ComposeAddonCommand("), launch.index("CreateProcessW("))
        compose = source[source.index("bool ComposeAddonCommand"):source.index("bool BuildPboPath")]
        self.assertLess(compose.index("ReadPboNamespace("), compose.index("BuildAddonCommand("))
        self.assertLess(compose.index("BinarizeNamespaceMatchesSource("), compose.index("BuildAddonCommand("))
        self.assertLess(compose.index("ReadPboNamespace("), compose.index("BinarizeNamespaceMatchesSource("))
        self.assertLess(compose.index("prefix_from_marker"), compose.index("BuildAddonCommand("))
        command = source[source.index("bool BuildAddonCommand"):source.index("bool BuildPboPath")]
        self.assertLess(command.index("IncludeListStillPinned()"), command.index('"-include="'))
        self.assertLess(command.index("GetFileType(prefix_file)"), command.index('"-include="'))
        self.assertLess(launch.index("BuildPboPath("), launch.index("CreateProcessW("))
        self.assertIn("SealedAddonBuilderEntry()", launch)
        command = source[source.index("bool BuildAddonCommand"):source.index("bool BuildPboPath")]
        self.assertIn("pbo_namespace", command)
        self.assertNotIn("-packonly", command[:command.index('"-include=')])
        harness = HARNESS.read_text(encoding="utf-8")
        self.assertNotIn("wWinMainCRTStartup", harness)

    def _pinned(self, source: Path, prefix: str, pack_only: bool) -> tuple[int, dict[str, str]]:
        code, values, _err = _run(
            self.exe,
            [
                "pinned-build", "0", "1" if pack_only else "0", prefix,
                str(source), r"C:\out\Addons", r"C:\tmp\build", ADDON,
            ],
            self._cwd(),
        )
        return code, values

    def _namespace(self, source: Path, prefix: str) -> tuple[int, dict[str, str]]:
        return _run(self.exe, ["namespace", prefix, str(source)], self._cwd())[:2]

    def test_match_is_admitted_and_mismatch_is_refused(self) -> None:
        # Dropping the gate admits the mismatch.
        matched = SCRATCH / "gate" / "SimpleGroup"
        matched.mkdir(parents=True)
        (matched / "$PBOPREFIX$").write_bytes(b"SimpleGroup\n")
        code, values = self._pinned(matched, "SimpleGroup", False)
        self.assertEqual(code, 0, values)
        self.assertIn('"-prefix=SimpleGroup"', values["command"])
        mismatched = SCRATCH / "gate" / "LFHeli_OH1"
        mismatched.mkdir()
        (mismatched / "$PBOPREFIX$").write_bytes(b"LFHeli\n")
        code, values = self._pinned(mismatched, "LFHeli_OH1", False)
        self.assertNotEqual(code, 0, values)
        self.assertEqual(values["ok"], "0")
        self.assertEqual(values["command"], "")

    def test_ascii_case_differs_and_a_different_name_does_not(self) -> None:
        # A case-sensitive compare rejects simplegroup.
        folder = SCRATCH / "case" / "SimpleGroup"
        folder.mkdir(parents=True)
        (folder / "$PBOPREFIX$").write_bytes(b"simplegroup\n")
        code, values = self._pinned(folder, "SimpleGroup", False)
        self.assertEqual(code, 0, values)
        self.assertIn('"-prefix=simplegroup"', values["command"])
        other = SCRATCH / "case" / "Other"
        other.mkdir()
        (other / "$PBOPREFIX$").write_bytes(b"SimpleGroup\n")
        code, values = self._pinned(other, "OtherMod", False)
        self.assertNotEqual(code, 0)

    def test_multiseqment_namespace_is_refused_even_when_the_leaf_matches(self) -> None:
        # Comparing only the namespace's last segment would admit folder B.
        for name, payload in (("slash", b"A/B\n"), ("backslash", b"A\\B\n")):
            folder = SCRATCH / "multi" / name / "B"
            folder.mkdir(parents=True)
            (folder / "$PBOPREFIX$").write_bytes(payload)
            code, values = self._namespace(folder, "B")
            self.assertEqual(code, 0, values)
            self.assertEqual(values["namespace"], "A\\B")
            code, values = self._pinned(folder, "B", False)
            self.assertNotEqual(code, 0, values)
        single = SCRATCH / "multi" / "only" / "B"
        single.mkdir(parents=True)
        (single / "$PBOPREFIX$").write_bytes(b"B\n")
        code, values = self._pinned(single, "B", False)
        self.assertEqual(code, 0, values)

    def test_junction_source_uses_its_lexical_name(self) -> None:
        # The resolved target's basename would be OtherName and would reject.
        real = SCRATCH / "junction-gate" / "OtherName"
        real.mkdir(parents=True)
        (real / "$PBOPREFIX$").write_bytes(b"SimpleGroup\n")
        link = SCRATCH / "junction-gate" / "SimpleGroup"
        _junction(real, link)
        code, values = self._pinned(link, "SimpleGroup", False)
        self.assertEqual(code, 0, values)
        self.assertIn('"-prefix=SimpleGroup"', values["command"])

    def test_missing_marker_fallback_is_gated(self) -> None:
        # Exempting the fallback would admit a folder that is not the prefix.
        same = SCRATCH / "fallback" / "SimpleGroup"
        same.mkdir(parents=True)
        code, values = self._namespace(same, "SimpleGroup")
        self.assertEqual(values["status"], "2")
        self.assertEqual(values["namespace"], "SimpleGroup")
        code, values = self._pinned(same, "SimpleGroup", False)
        self.assertEqual(code, 0, values)
        other = SCRATCH / "fallback" / "LFHeli_OH1"
        other.mkdir()
        code, values = self._pinned(other, "SimpleGroup", False)
        self.assertNotEqual(code, 0, values)

    def test_bom_lf_and_crlf_pass_and_extra_whitespace_fails(self) -> None:
        # strip() would accept the padded markers.
        for name, payload in (
            ("bom", b"\xef\xbb\xbfLFHeli"),
            ("lf", b"LFHeli\n"),
            ("crlf", b"LFHeli\r\n"),
        ):
            folder = SCRATCH / "ws" / name / "LFHeli"
            folder.mkdir(parents=True)
            (folder / "$PBOPREFIX$").write_bytes(payload)
            code, values = self._pinned(folder, "LFHeli", False)
            self.assertEqual(code, 0, values)
        for name, payload in (("padded", b" LFHeli\n"), ("trailing", b"LFHeli \n")):
            folder = SCRATCH / "ws" / name / "LFHeli"
            folder.mkdir(parents=True)
            (folder / "$PBOPREFIX$").write_bytes(payload)
            code, values = self._namespace(folder, "LFHeli")
            self.assertEqual(values.get("status"), "0")
            code, values = self._pinned(folder, "LFHeli", False)
            self.assertNotEqual(code, 0)

    def test_packonly_keeps_a_mismatched_namespace(self) -> None:
        # Applying the gate to packonly would reject this command.
        folder = SCRATCH / "pack-gate" / "LFHeli_OH1"
        folder.mkdir(parents=True)
        (folder / "$PBOPREFIX$").write_bytes(b"A\\B\n")
        code, values = self._pinned(folder, "LFHeli_OH1", True)
        self.assertEqual(code, 0, values)
        self.assertIn('"-prefix=A\\B"', values["command"])
        self.assertTrue(values["command"].endswith(" -packonly"))
        code, values = self._pinned(folder, "LFHeli_OH1", False)
        self.assertNotEqual(code, 0, values)

    def test_root_without_a_leaf_is_refused_for_binarize(self) -> None:
        code, values = self._pinned(Path(r"C:\\"), "SimpleGroup", False)
        self.assertNotEqual(code, 0, values)


def _exclusive_open(path: Path) -> bool:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    handle = kernel.CreateFileW(
        str(path), 0x80000000, 0, None, 3, 0x80, None
    )
    invalid = ctypes.c_void_p(-1).value
    if handle is None or handle == invalid:
        return False
    kernel.CloseHandle(handle)
    return True


class WorkerNamespaceGateTest(unittest.TestCase):
    """The worker pre-check matches the compiled gate and runs before stage."""

    def _policy(self, source: Path, mod: str = "ExampleMod"):
        policy = dayz_test_request.RequestProjectPolicy(
            mod=mod,
            dev_root=r"P:\ExampleMod_Suite",
            default_source=str(source),
            default_base_mods=(),
            mission_roots=(r"C:\missions",),
            mod_roots=(r"P:\Mods",),
        )
        runtime = dayz_test_worker.WorkerRuntimePolicy(
            dev_root=policy.dev_root,
            mod=mod,
            diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
            game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
            mission_aliases=(("chernarus", r"C:\missions\c"), ("livonia", r"C:\missions\l"), ("sakhal", r"C:\missions\s")),
            mods_root=r"P:\Mods",
            build_temp_root=r"P:\temp",
            build_source_basename=None,
        )
        return policy, runtime

    def _execute(
        self,
        source: Path,
        *,
        mod: str,
        pack_only: bool = False,
        has_assets: bool = True,
        stage_calls: list[str] | None = None,
        broker_calls: list[str] | None = None,
        during_broker=None,
    ):
        policy, runtime = self._policy(source, mod)
        raw = json.dumps(
            {
                "version": 1,
                "dev_root": policy.dev_root,
                "mod": mod,
                "mode": "server",
                "build": True,
                "pack_only": pack_only,
                "source": str(source),
            }
        ).encode("utf-8")
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))

        class Broker:
            def __init__(self) -> None:
                self.requests: list = []

            async def invoke(self, frame: bytes) -> dict[str, object]:
                request = native_broker_protocol.decode_request(frame)
                self.requests.append(request)
                if request.kind is native_broker_protocol.BrokerKind.ADDON_BUILDER:
                    if broker_calls is not None:
                        broker_calls.append("broker")
                    if during_broker is not None:
                        during_broker()
                    return {"ok": True, "exit_code": 0, "pbo_size": 64}
                return {"ok": True, "state": "RUNNING", "run_id": request.payload.get("run_id")}

        def stage(path: str):
            if stage_calls is not None:
                stage_calls.append(path)
            return nullcontext(path)

        broker = Broker()
        result = asyncio.run(
            dayz_test_worker.execute_dayz_test_worker(
                parsed.canonical_bytes,
                request_sha256=parsed.sha256,
                request_policies=(policy,),
                runtime_policy=runtime,
                broker=broker,
                has_binarizable_assets=lambda _source: has_assets,
                stage_build_source=stage,
            )
        )
        return result, broker

    def test_mismatch_is_before_stage_and_broker_and_match_reaches_both(self) -> None:
        # Moving the check to after the stage would call the stage spy first.
        root = SCRATCH / "worker-order"
        bad = root / "LFHeli_OH1"
        bad.mkdir(parents=True)
        (bad / "$PBOPREFIX$").write_bytes(b"LFHeli\n")
        stages: list[str] = []
        brokers: list[str] = []
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._execute(bad, mod="LFHeli_OH1", stage_calls=stages, broker_calls=brokers)
        self.assertEqual(raised.exception.code, "build_namespace_source_mismatch")
        self.assertIsNone(raised.exception.run_id)
        self.assertFalse(raised.exception.cleanup_degraded)
        self.assertEqual(stages, [])
        self.assertEqual(brokers, [])
        good = root / "SimpleGroup"
        good.mkdir()
        (good / "$PBOPREFIX$").write_bytes(b"SimpleGroup\n")
        stages.clear()
        brokers.clear()
        result, broker = self._execute(
            good, mod="SimpleGroup", stage_calls=stages, broker_calls=brokers
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(stages), 1)
        self.assertEqual(brokers, ["broker"])
        self.assertEqual(broker.requests[0].kind, native_broker_protocol.BrokerKind.ADDON_BUILDER)

    def test_marker_stays_open_through_the_broker_and_closes_on_rejection(self) -> None:
        folder = SCRATCH / "pin" / "SimpleGroup"
        folder.mkdir(parents=True)
        marker = folder / "$PBOPREFIX$"
        marker.write_bytes(b"SimpleGroup\n")
        seen: list[bool] = []

        def during() -> None:
            seen.append(_exclusive_open(marker))

        self._execute(folder, mod="SimpleGroup", during_broker=during)
        self.assertEqual(seen, [False])
        self.assertTrue(_exclusive_open(marker))
        other = SCRATCH / "pin" / "LFHeli_OH1"
        other.mkdir()
        other_marker = other / "$PBOPREFIX$"
        other_marker.write_bytes(b"LFHeli\n")
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError):
            self._execute(other, mod="LFHeli_OH1")
        self.assertTrue(_exclusive_open(other_marker))

    def test_explicit_and_automatic_packonly_admit_a_mismatch(self) -> None:
        # Gating packonly would raise before the broker.
        folder = SCRATCH / "worker-pack" / "LFHeli_OH1"
        folder.mkdir(parents=True)
        (folder / "$PBOPREFIX$").write_bytes(b"LFHeli\n")
        brokers: list[str] = []
        result, _broker = self._execute(
            folder, mod="LFHeli_OH1", pack_only=True, has_assets=True, broker_calls=brokers
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(brokers, ["broker"])
        brokers.clear()
        result, broker = self._execute(
            folder, mod="LFHeli_OH1", pack_only=False, has_assets=False, broker_calls=brokers
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIs(broker.requests[0].payload["pack_only"], True)
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._execute(folder, mod="LFHeli_OH1", pack_only=False, has_assets=True)
        self.assertEqual(raised.exception.code, "build_namespace_source_mismatch")

    def test_worker_reproduces_native_limits_and_does_not_fall_back(self) -> None:
        # Falling back on an invalid marker would return the prefix instead.
        folder = SCRATCH / "limits" / "SimpleGroup"
        folder.mkdir(parents=True)
        (folder / "$PBOPREFIX$").write_bytes(b"")
        pin = dayz_test_worker._PrefixPin()
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            dayz_test_worker._read_pbo_namespace(str(folder), "SimpleGroup", pin)
        self.assertEqual(raised.exception.code, "build_source_unavailable")
        pin.close()
        missing = SCRATCH / "limits-missing"
        pin = dayz_test_worker._PrefixPin()
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            dayz_test_worker._read_pbo_namespace(str(missing), "SimpleGroup", pin)
        self.assertEqual(raised.exception.code, "build_source_unavailable")
        absent = SCRATCH / "limits" / "NoMarker"
        absent.mkdir()
        pin = dayz_test_worker._PrefixPin()
        self.assertEqual(
            dayz_test_worker._read_pbo_namespace(str(absent), "SimpleGroup", pin),
            "SimpleGroup",
        )
        self.assertTrue(dayz_test_worker.win32_fileinfo.invalid_handle(pin.handle))
        linked = SCRATCH / "limits-link"
        linked.mkdir()
        target = SCRATCH / "limits-link-target.txt"
        target.write_bytes(b"SimpleGroup\n")
        os.link(target, linked / "$PBOPREFIX$")
        os.link(target, SCRATCH / "limits-link-alias.txt")
        pin = dayz_test_worker._PrefixPin()
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError):
            dayz_test_worker._read_pbo_namespace(str(linked), "SimpleGroup", pin)
        via = SCRATCH / "limits-reparse"
        via.mkdir()
        real = SCRATCH / "limits-reparse-real"
        real.mkdir()
        _junction(real, via / "$PBOPREFIX$")
        pin = dayz_test_worker._PrefixPin()
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError):
            dayz_test_worker._read_pbo_namespace(str(via), "SimpleGroup", pin)

    def test_worker_and_harness_agree_on_the_corpus(self) -> None:
        # Skipping '/' normalization would decode A/B differently from the harness, and a
        # case-sensitive worker comparison would refuse the "case-*" rows the harness admits.
        exe = LauncherHarnessTest.exe
        corpus = SCRATCH / "corpus"
        cases = {
            "plain": ("LFHeli", b"LFHeli"),
            "slash": ("LFHeli", b"A/B"),
            "bom": ("LFHeli", b"\xef\xbb\xbfLFHeli\r\n"),
            "space": ("LFHeli", b"LFHeli \n"),
            "missing": ("LFHeli", None),
            "case-marker": ("LFHeli", b"lfheli\n"),
            "case-folder": ("lfheli", b"LFHeli"),
            "case-fallback": ("lfheli", None),
            "multi-leaf": ("LFHeli", b"X/LFHeli"),
            "segment-64": ("LFHeli", b"A" * 64),
            "segment-65": ("LFHeli", b"A" * 65),
            "lead-dash": ("LFHeli", b"-LFHeli"),
            "two-lines": ("LFHeli", b"LFHeli\nX"),
            "crlf-only": ("LFHeli", b"\r\n"),
            "utf16": ("LFHeli", b"\xff\xfeL\x00"),
        }
        for name, (leaf, payload) in cases.items():
            folder = corpus / name / leaf
            folder.mkdir(parents=True)
            if payload is not None:
                (folder / "$PBOPREFIX$").write_bytes(payload)
            code, values, _err = _run(exe, ["namespace", "LFHeli", str(folder)], SCRATCH / "cwd")
            pin = dayz_test_worker._PrefixPin()
            try:
                try:
                    namespace = dayz_test_worker._read_pbo_namespace(str(folder), "LFHeli", pin)
                    worker_ok = True
                except dayz_test_worker.DayzTestWorkerError as error:
                    namespace = ""
                    worker_ok = False
                    self.assertEqual(error.code, "build_source_unavailable")
            finally:
                pin.close()
            if code == 0:
                self.assertTrue(worker_ok, name)
                self.assertEqual(namespace, values["namespace"])
            else:
                self.assertFalse(worker_ok, name)
            build_code, build_values, _err = _run(
                exe,
                ["pinned-build", "0", "0", "LFHeli", str(folder), r"C:\out\Addons", r"C:\tmp\build", ADDON],
                SCRATCH / "cwd",
            )
            leaf_matches = dayz_test_worker._binarize_namespace_matches(
                namespace or "LFHeli", str(folder)
            )
            if worker_ok and leaf_matches:
                self.assertEqual(build_code, 0, build_values)
            else:
                self.assertNotEqual(build_code, 0, name)
            if name.startswith("case-"):
                # ASCII case alone never refuses a binarize, natively or in the worker.
                self.assertEqual(build_code, 0, name)
                self.assertTrue(worker_ok and leaf_matches, name)


class RemediationSelectionTest(unittest.IsolatedAsyncioTestCase):
    async def _result(self, error_code: str) -> dict[str, object]:
        body = json.dumps(
            {
                "cleanup_degraded": False,
                "error_code": error_code,
                "exit_code": 1,
                "ok": False,
                "run_id": None,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        async def execute(_raw: object, **kwargs: object) -> int:
            kwargs["output_sink"]("stdout", body)
            kwargs["output_sink"]("stderr", b"")
            return 1

        class Runtime:
            daemon_policy = None

        with unittest.mock.patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=execute,
        ):
            return await dayz_test_tool._execute_request(
                Runtime(),
                opened_launcher=None,
                verified_bundle=None,
                raw_request=b"{}",
                policy=type("Policy", (), {"mod": "ExampleMod"})(),
                public_mode="server",
                artifacts_paths=[],
                started_at=0.0,
                preflight=False,
                expected_run_id=None,
                progress_cb=None,
            )

    async def test_only_the_new_code_gets_the_remediation(self) -> None:
        # Omitting the code selection leaves remediation null for this failure.
        result = await self._result("build_namespace_source_mismatch")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "build_namespace_source_mismatch")
        self.assertIsNone(result["run_id"])
        self.assertIs(result["cleanup_degraded"], False)
        self.assertEqual(
            result["remediation"],
            dayz_test_tool._NAMESPACE_SOURCE_MISMATCH_REMEDIATION,
        )
        other = await self._result("build_failed")
        self.assertIsNone(other["remediation"])
        self.assertNotEqual(other["error_code"], "build_namespace_source_mismatch")


if __name__ == "__main__":
    unittest.main()
