from __future__ import annotations

import contextlib
import io
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dayz_mcp import server_boot

# Verbatim lines and surrounding context from FACTS.md section 1.
FACTS_SECTION_1_LOG = """\
---------------------------------------------
Log ruta started at 2026-11-11 04:36:51 (2026-11-11 03:36:51 UTC)

04:36:51.817 SCRIPT       : Module: GameLib; loaded 26x files; 32x classes; used 56/2097 kB (2%) of static memory; defines: "…"; CRC32: 985a0343
04:36:51.828 SCRIPT       : Registered 387 temporary action enum(s), UAN==387
04:36:52.456 SCRIPT       : Initializing globals 'Game'
04:36:52.456  SCRIPT       : [EXTRACE] +ExpansionSHA256::Update
04:36:52.464 SCRIPT       : Module: Game; loaded 914x files; 2575x classes; used 9242/33554 kB (27%) of static memory; defines: "…"; CRC32: aef5736c
04:36:53.000 SCRIPT       : [EXTRACE]  -ExpansionRPCManager<0x0000027689BDF950>::CreateRPCID ExpansionSoundModule::RPC_Play -1753991406 0.0071 ms
04:36:54.550  SCRIPT       : [EXTRACE]  -ExpansionRPCManager<0x0000027693E85270>::CreateRPCID ExpansionMonitorModule::RPC_RequestPlayerStates -718347275 0.002 ms
04:36:54.618 SCRIPT       : Module: World; loaded 2874x files; 7264x classes; used 16871/33554 kB (50%) of static memory; defines: "…"; CRC32: 8f0f10d5
04:37:09.995  SCRIPT       : Module: Mission; loaded 671x files; 1549x classes; used 11098/33554 kB (33%) of static memory; defines: "…"; CRC32: c5bfed31
"""

# Verbatim countdown excerpt from FACTS.md section 4.
FACTS_SECTION_4_RPT = """\
 5:55:22.582 NETWORK      : server: Player dpid=2 added at playerIndex=0
 5:55:22.583 [Server] :: termination in: 10
 5:55:23.587 [Server] :: termination in: 9
 5:55:24.598 [Server] :: termination in: 8
 5:55:25.611 [Server] :: termination in: 7
 5:55:26.620 [Server] :: termination in: 6
 5:55:27.626 [Server] :: termination in: 5
 5:55:28.638 [Server] :: termination in: 4
 5:55:29.648 [Server] :: termination in: 3
 5:55:30.663 [Server] :: termination in: 2
 5:55:31.673 [Server] :: termination in: 1
 5:55:32.737 ENGINE       : Destroying game
 5:55:32.737  SCRIPT       : ~DayZGame()
 5:55:32.859  ENGINE       : Game destroyed.
 5:55:32.862 ENGINE       : Destroying game
 5:55:32.862  ENGINE       : Game destroyed.
 5:55:33.73   --- Termination successfully completed --- 
"""


def _build_pbo_header(
    entries: list[str],
    *,
    vers_props: tuple[tuple[str, str], ...] | None = (("prefix", "scripts"),),
    include_terminator: bool = True,
    data_size: int = 128,
) -> bytes:
    out = bytearray()
    if vers_props is not None:
        out.extend(b"\x00")
        out.extend(struct.pack("<5I", 0x56657273, 0, 0, 0, 0))
        for key, value in vers_props:
            out.extend(key.encode("utf-8") + b"\x00")
            out.extend(value.encode("utf-8") + b"\x00")
        out.extend(b"\x00")
    for name in entries:
        out.extend(name.encode("utf-8") + b"\x00")
        out.extend(struct.pack("<5I", 0, data_size, 0, 0, data_size))
    if include_terminator:
        out.extend(b"\x00")
        out.extend(struct.pack("<5I", 0, 0, 0, 0, 0))
    return bytes(out)


class _CountingReader:
    """Binary stream wrapper that counts every byte pulled via ``read``."""

    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._stream.read(size)
        self.bytes_read += len(chunk)
        return chunk


# A PBO tracked in the repository, read through the real file path.
_FENCE_PBO = (
    Path(__file__).resolve().parents[2]
    / "reviews"
    / "2026-08-19-lane-fence"
    / "DayZ_MCP_fence_DCC8730F.pbo"
)
_TRAILING_DATA = b"\xfe" * (1024 * 1024)


class _CountingRawFile(io.RawIOBase):
    """Raw file that records every byte the OS returns for it."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        super().__init__()
        self._file = io.FileIO(path, "rb")
        self.bytes_from_os = 0
        self.reads = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int | None:  # type: ignore[override]
        count = self._file.readinto(buffer)
        self.reads += 1
        self.bytes_from_os += count or 0
        return count

    def close(self) -> None:
        try:
            self._file.close()
        finally:
            super().close()


@contextlib.contextmanager
def _counting_open():
    """Patch the ``open`` that ``server_boot`` calls with ``open()``'s layering.

    ``buffering=0`` hands back the counting raw file itself; any other value
    wraps it in a ``BufferedReader`` the way ``open()`` does, so buffered
    read-ahead shows up as bytes the OS returned.
    """
    opened: list[_CountingRawFile] = []

    def fake_open(file, mode="r", buffering=-1, *args, **kwargs):
        if mode != "rb" or args or kwargs:
            raise AssertionError(f"unexpected open: {mode!r} {args!r} {kwargs!r}")
        raw = _CountingRawFile(file)
        opened.append(raw)
        if buffering == 0:
            return raw
        size = buffering if buffering > 1 else io.DEFAULT_BUFFER_SIZE
        return io.BufferedReader(raw, buffer_size=size)

    with mock.patch.object(server_boot, "open", new=fake_open, create=True):
        yield opened


def _name_of_length(length: int) -> str:
    """A ``4_World`` script entry of exactly ``length`` characters, else a bare name."""
    folder = "4_World\\"
    if length < len(folder) + len("x.c"):
        return "n" * length
    return folder + "f" * (length - len(folder) - len(".c")) + ".c"


def _independent_header_length(data: bytes) -> int:
    """Offset just past a PBO header's terminator entry, parsed without server_boot."""
    pos = 0
    first = True
    while True:
        name_end = data.index(b"\x00", pos)
        (mime,) = struct.unpack_from("<I", data, name_end + 1)
        empty_name = name_end == pos
        pos = name_end + 1 + 20
        if not empty_name:
            first = False
            continue
        if first and mime == 0x56657273:
            first = False
            while True:
                key_end = data.index(b"\x00", pos)
                empty_key = key_end == pos
                pos = key_end + 1
                if empty_key:
                    break
                pos = data.index(b"\x00", pos) + 1
            continue
        return pos


class ScriptLogParserTest(unittest.TestCase):
    def test_parses_four_facts_modules_and_marks_complete(self) -> None:
        report = server_boot.parse_script_log(FACTS_SECTION_1_LOG)
        self.assertEqual(
            report.modules,
            {"GameLib": 26, "Game": 914, "World": 2874, "Mission": 671},
        )
        self.assertTrue(report.complete)
        self.assertEqual(report.script_errors, 0)

    def test_without_mission_line_is_incomplete_with_three_keys(self) -> None:
        without_mission = "\n".join(
            line
            for line in FACTS_SECTION_1_LOG.splitlines()
            if "Module: Mission;" not in line
        )
        report = server_boot.parse_script_log(without_mission)
        self.assertEqual(
            report.modules,
            {"GameLib": 26, "Game": 914, "World": 2874},
        )
        self.assertFalse(report.complete)

    def test_extrace_module_rpc_line_is_ignored(self) -> None:
        line = (
            "04:36:53.000 SCRIPT       : [EXTRACE]  "
            "-ExpansionRPCManager<0x0000027689BDF950>::CreateRPCID "
            "ExpansionSoundModule::RPC_Play -1753991406 0.0071 ms"
        )
        report = server_boot.parse_script_log(line)
        self.assertEqual(report.modules, {})
        self.assertFalse(report.complete)

    def test_truncated_module_line_without_x_files_is_ignored(self) -> None:
        report = server_boot.parse_script_log("04:36:52.464 SCRIPT : Module: Game; loaded")
        self.assertEqual(report.modules, {})
        self.assertFalse(report.complete)

    def test_unknown_module_names_core_and_foo_are_ignored(self) -> None:
        log = "\n".join(
            [
                '04:36:51.000 SCRIPT       : Module: Core; loaded 58x files; 10x classes; defines: "…"',
                '04:36:51.100 SCRIPT       : Module: Foo; loaded 7x files; 2x classes; defines: "…"',
            ]
        )
        report = server_boot.parse_script_log(log)
        self.assertEqual(report.modules, {})
        self.assertFalse(report.complete)

    def test_duplicate_game_module_keeps_first_line(self) -> None:
        log = "\n".join(
            [
                '04:36:52.464 SCRIPT       : Module: Game; loaded 914x files; 2575x classes; defines: "…"',
                '04:37:12.000 SCRIPT       : Module: Game; loaded 999x files; 2575x classes; defines: "…"',
            ]
        )
        report = server_boot.parse_script_log(log)
        self.assertEqual(report.modules, {"Game": 914})

    def test_script_error_counter_matches_only_four_space_script_errors(self) -> None:
        log = "\n".join(
            [
                "04:36:52.464 SCRIPT    (E): x",
                "04:36:52.500 SCRIPT    (E): x",
                "04:36:52.600 SCRIPT (E): single space does not count",
                " 4:36:51.716 RESOURCES (E): IRenderer::CreateTextureFromImage(system/textures/perlin.edds) failed",
            ]
        )
        report = server_boot.parse_script_log(log)
        self.assertEqual(report.script_errors, 2)
        self.assertEqual(
            server_boot.parse_script_log(
                "04:36:52.600 SCRIPT (E): x\n"
                " 4:36:51.716 RESOURCES (E): IRenderer::CreateTextureFromImage(system/textures/perlin.edds) failed\n"
            ).script_errors,
            0,
        )


class RptParserTest(unittest.TestCase):
    def test_facts_section_4_rpt_detects_termination_countdown(self) -> None:
        self.assertTrue(server_boot.has_termination_countdown(FACTS_SECTION_4_RPT))

    def test_normal_termination_footer_alone_is_false(self) -> None:
        rpt = " 5:55:33.73   --- Termination successfully completed --- \n"
        self.assertFalse(server_boot.has_termination_countdown(rpt))

    def test_network_player_added_line_alone_is_false(self) -> None:
        rpt = " 5:55:22.582 NETWORK      : server: Player dpid=2 added at playerIndex=0\n"
        self.assertFalse(server_boot.has_termination_countdown(rpt))


class PboHeaderCountsTest(unittest.TestCase):
    def test_counts_module_c_entries_with_vers_prefix_scripts(self) -> None:
        header = _build_pbo_header(
            [
                r"3_Game\a.c",
                r"3_Game\sub\b.C",
                r"4_World\x.c",
                r"5_Mission\m.c",
                r"1_Core\k.c",
                r"editor\e.c",
                r"Common\c.c",
                r"x.c",
                r"config.cpp",
                r"3_Game\readme.txt",
            ],
            vers_props=(("prefix", "scripts"),),
        )
        self.assertEqual(
            server_boot.read_pbo_header_counts(io.BytesIO(header)),
            {"GameLib": 0, "Game": 2, "World": 1, "Mission": 1},
        )

    def test_counts_mod_prefixed_entry_path_in_world(self) -> None:
        header = _build_pbo_header(
            [r"MyMod\scripts\4_World\y.c", "MyMod/scripts/2_GameLib/init.c"],
            vers_props=(("prefix", "MyMod"),),
        )
        self.assertEqual(
            server_boot.read_pbo_header_counts(io.BytesIO(header)),
            {"GameLib": 1, "Game": 0, "World": 1, "Mission": 0},
        )

    def test_truncated_unterminated_and_over_entry_cap_headers_return_none(self) -> None:
        valid = _build_pbo_header([r"3_Game\a.c", r"4_World\x.c"])
        half = valid[: len(valid) // 2]
        self.assertIsNone(server_boot.read_pbo_header_counts(io.BytesIO(half)))

        unterminated = _build_pbo_header(
            [r"3_Game\a.c", r"4_World\x.c"], include_terminator=False
        )
        self.assertIsNone(server_boot.read_pbo_header_counts(io.BytesIO(unterminated)))

        single_record = b"a\x00" + b"\x00" * 20
        over_entries = single_record * 500_001 + b"\x00" * 21
        self.assertIsNone(server_boot.read_pbo_header_counts(io.BytesIO(over_entries)))

    def test_header_over_sixteen_mib_cap_returns_none(self) -> None:
        long_record = (b"x" * 90) + b"\x00" + (b"\x00" * 20)
        over_bytes = long_record * 160_000 + b"\x00" * 21
        self.assertGreater(len(over_bytes), 16 * 1024 * 1024)
        self.assertIsNone(server_boot.read_pbo_header_counts(io.BytesIO(over_bytes)))

    def test_does_not_read_past_end_of_header(self) -> None:
        header = _build_pbo_header(
            [r"3_Game\a.c", r"4_World\x.c", r"5_Mission\m.c"],
            vers_props=(("prefix", "scripts"),),
            data_size=512 * 1024,
        )
        trailing_payload = b"\xfe" * (1024 * 1024)
        reader = _CountingReader(header + trailing_payload)
        counts = server_boot.read_pbo_header_counts(reader)
        self.assertEqual(
            counts,
            {"GameLib": 0, "Game": 1, "World": 1, "Mission": 1},
        )
        self.assertEqual(reader.bytes_read, len(header))


class PboFileOsReadTest(unittest.TestCase):
    """A PBO path is read from the OS up to its header terminator and no further."""

    _NAME_LENGTHS = (1, 20, 21, 41, 42, 43, 100)
    _TWO_PROPERTIES = (("prefix", r"MyMod\scripts"), ("version", "1.30"))

    def _count_file(self, path: Path) -> tuple[dict[str, int] | None, _CountingRawFile]:
        with _counting_open() as opened:
            counts = server_boot.read_pbo_header_counts(path)
        self.assertEqual(len(opened), 1)
        return counts, opened[0]

    def test_counting_open_sees_buffered_read_ahead(self) -> None:
        # Control: the stand-in reproduces open()'s read-ahead, so a reader
        # that opened the file buffered would be caught by the tests below.
        header = _build_pbo_header([r"3_Game\a.c"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "control.pbo"
            path.write_bytes(header + _TRAILING_DATA)
            with _counting_open() as opened:
                with server_boot.open(path, "rb") as handle:
                    handle.read(21)
        self.assertGreater(opened[0].bytes_from_os, len(header))

    def test_constructed_pbos_read_exactly_their_header_from_the_os(self) -> None:
        names = [_name_of_length(length) for length in self._NAME_LENGTHS]
        self.assertEqual([len(name) for name in names], list(self._NAME_LENGTHS))
        none = {"GameLib": 0, "Game": 0, "World": 0, "Mission": 0}
        every_name = {**none, "World": sum(name.endswith(".c") for name in names)}
        cases = {
            "vers with two properties and every name length": (
                _build_pbo_header(names, vers_props=self._TWO_PROPERTIES),
                every_name,
            ),
            "only vers and the terminator": (
                _build_pbo_header([], vers_props=self._TWO_PROPERTIES),
                none,
            ),
            "vers without properties and the terminator": (
                _build_pbo_header([], vers_props=()),
                none,
            ),
            "no vers": (_build_pbo_header(names, vers_props=None), every_name),
        }
        for length, name in zip(self._NAME_LENGTHS, names):
            cases[f"vers and one name of length {length}"] = (
                _build_pbo_header([name], vers_props=self._TWO_PROPERTIES),
                {**none, "World": int(name.endswith(".c"))},
            )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.pbo"
            for label, (header, expected) in cases.items():
                with self.subTest(label):
                    path.write_bytes(header + _TRAILING_DATA)
                    counts, raw = self._count_file(path)
                    self.assertEqual(counts, expected)
                    self.assertEqual(raw.bytes_from_os, len(header))
                    # A few reads per entry, never one read per character.
                    self.assertLessEqual(raw.reads, max(8, len(header) // 10))

    def test_tracked_fence_pbo_is_read_up_to_its_terminator_only(self) -> None:
        data = _FENCE_PBO.read_bytes()
        header_length = _independent_header_length(data)
        self.assertLess(header_length, len(data))
        counts, raw = self._count_file(_FENCE_PBO)
        self.assertIsNotNone(counts)
        self.assertEqual(counts, server_boot.read_pbo_header_counts(io.BytesIO(data)))
        self.assertEqual(raw.bytes_from_os, header_length)


class MissingFilesAndCompositionTest(unittest.TestCase):
    def test_dayz_mcp_only_exact_match_reports_zero_missing(self) -> None:
        vanilla = {"GameLib": 13, "Game": 440, "World": 2322, "Mission": 219}
        mods = {"GameLib": 0, "Game": 0, "World": 12, "Mission": 13}
        loaded = {"GameLib": 13, "Game": 440, "World": 2334, "Mission": 232}
        self.assertEqual(
            server_boot.compute_missing_files(loaded, vanilla, mods),
            {"GameLib": 0, "Game": 0, "World": 0, "Mission": 0},
        )

    def test_unmounted_world_mod_files_reported_as_missing_twelve(self) -> None:
        vanilla = {"GameLib": 13, "Game": 440, "World": 2322, "Mission": 219}
        mods = {"GameLib": 0, "Game": 0, "World": 12, "Mission": 13}
        loaded = {"GameLib": 13, "Game": 440, "World": 2322, "Mission": 232}
        self.assertEqual(
            server_boot.compute_missing_files(loaded, vanilla, mods),
            {"GameLib": 0, "Game": 0, "World": 12, "Mission": 0},
        )

    def test_nine_mods_common_folder_surplus_clamps_missing_to_zero(self) -> None:
        # FACTS section 5: expected 21 / 908 / 2868 / 665, loaded 26 / 914 / 2874 / 671.
        vanilla = {"GameLib": 13, "Game": 440, "World": 2322, "Mission": 219}
        mods = {"GameLib": 8, "Game": 468, "World": 546, "Mission": 446}
        loaded = {"GameLib": 26, "Game": 914, "World": 2874, "Mission": 671}
        self.assertEqual(
            server_boot.compute_missing_files(loaded, vanilla, mods),
            {"GameLib": 0, "Game": 0, "World": 0, "Mission": 0},
        )

    def test_vanilla_none_yields_null_missing_files(self) -> None:
        mods = {"GameLib": 0, "Game": 0, "World": 12, "Mission": 13}
        loaded = {"GameLib": 13, "Game": 440, "World": 2334, "Mission": 232}
        self.assertIsNone(server_boot.compute_missing_files(loaded, None, mods))
        composed = server_boot.compose_server_boot(
            server_boot.ScriptLogReport(
                modules=loaded, complete=True, script_errors=0
            ),
            termination_countdown=False,
            vanilla_files=None,
            mod_files=mods,
        )
        self.assertIsNone(composed["vanilla_files"])
        self.assertIsNone(composed["missing_files"])


class DirectoryPboCountsTest(unittest.TestCase):
    def _write_pbo(self, path: Path, entries: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_build_pbo_header(entries))

    def test_vanilla_sums_dta_and_sakhal_and_skips_bang_and_at_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "DayZ"
            self._write_pbo(
                game / "dta" / "scripts.pbo",
                [r"2_GameLib\g.c", r"3_Game\a.c", r"4_World\w1.c", r"5_Mission\m.c"],
            )
            self._write_pbo(
                game / "sakhal" / "addons" / "data_sakhal.pbo",
                [r"scripts\4_World\sakhal_1.c", r"scripts\4_World\sakhal_2.c"],
            )
            self._write_pbo(
                game / "!Workshop" / "@X" / "addons" / "m.pbo",
                [r"MyMod\scripts\4_World\ignored_ws.c"],
            )
            self._write_pbo(
                game / "!X" / "addons" / "m.pbo",
                [r"MyMod\scripts\2_GameLib\ignored_bang.c"],
            )
            self._write_pbo(
                game / "@Y" / "addons" / "m.pbo",
                [r"MyMod\scripts\3_Game\ignored_mod.c"],
            )

            self.assertEqual(
                server_boot.count_vanilla_files(game),
                {"GameLib": 1, "Game": 1, "World": 3, "Mission": 1},
            )

    def test_corrupt_pbo_in_vanilla_addons_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "DayZ"
            self._write_pbo(game / "dta" / "scripts.pbo", [r"3_Game\a.c"])
            broken = game / "addons" / "broken.pbo"
            broken.parent.mkdir(parents=True, exist_ok=True)
            broken.write_bytes(b"corrupt-pbo")

            self.assertIsNone(server_boot.count_vanilla_files(game))

    def test_mod_count_finds_capitalized_addons_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "@MyMod"
            self._write_pbo(
                mod_dir / "Addons" / "mymod.pbo",
                [r"MyMod\scripts\4_World\w.c", r"MyMod\scripts\5_Mission\m.c"],
            )
            self.assertEqual(
                server_boot.count_mod_files([mod_dir]),
                {"GameLib": 0, "Game": 0, "World": 1, "Mission": 1},
            )


if __name__ == "__main__":
    unittest.main()
