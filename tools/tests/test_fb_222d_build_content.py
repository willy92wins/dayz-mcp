"""Offline checks for the sealed AddonBuilder include list and $PBOPREFIX$ namespace.

The harness compiles launcher.cpp. It does not start AddonBuilder or the sealed entry point.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import unittest
from pathlib import Path

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
        if SCRATCH.exists():
            shutil.rmtree(SCRATCH, ignore_errors=True)

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
        source = SCRATCH / "mod"
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
        source = SCRATCH / "mod with space"
        source.mkdir(exist_ok=True)
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


if __name__ == "__main__":
    unittest.main()
