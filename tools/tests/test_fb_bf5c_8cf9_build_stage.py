"""inbox bf5c + 8cf9 (fb-20260930-180331-bf5c, fb-20260930-214647-8cf9): the stage.

AddonBuilder hands binarize -addon="<parent of the source>", and binarize parses
every config.cpp under that folder, through junctions too. A broken fixture
next to the source failed every build from 24 to 30 September, and a
SimpleGroup build walked hundreds of sibling projects for about 4 minutes.

When binarize runs (pack_only false) the sealed worker now builds from
<stage>\\<basename>, where <stage> is a fresh folder in the launcher's private
TEMP, and only there, that holds only junctions: the mod, the vanilla roots
DayZ Tools extracts into the mod's parent, and the parent's folders the mod's
own files reference. A link inside the mod that leads out of those folders is
refused, because binarize would follow it out of the stage. These tests drive
the real worker with real junctions in temporary folders and a broker that
records the AddonBuilder frame. No fixture holds a broken config.cpp, every
junction points inside the test's own folder, and each one a test makes is
removed with os.rmdir.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import ntpath
import os
import stat
import sys
import tempfile
import types
import unittest
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest import mock

from dayz_mcp import (
    dayz_test_request,
    dayz_test_tool,
    dayz_test_worker,
    native_broker_protocol,
)
from tests.dayz_test_tool_helpers import _terminal

try:
    import _winapi
except ImportError:  # not Windows: every staging test here skips
    _winapi = None


MOD = "StageMod"
DEV_ROOT = r"P:\StageMod_Suite"
MISSIONS = r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions"
RUN_ID = "12345678-1234-4234-8234-1234567890ab"
OPERATION_ID = "87654321-4321-4321-8321-ba0987654321"
BUILT = {"ok": True, "exit_code": 0, "pbo_size": 8192}
FAILED_BUILD = {"ok": False, "exit_code": 1, "pbo_size": 0}
STAGE_PREFIX = "dayz-mcp-build-"
# The roots the stage holds for _Fixture besides the mod, in plan order.
EXPECTED_ROOTS = ("DZ", "Embedded", "LFHeli", "Other", "Scripts", "Shared")

RUNTIME = dayz_test_worker.WorkerRuntimePolicy(
    dev_root=DEV_ROOT,
    mod=MOD,
    diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
    game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
    mission_aliases=(
        ("chernarus", MISSIONS + r"\dayzOffline.chernarusplus"),
        ("livonia", MISSIONS + r"\dayzOffline.enoch"),
        ("sakhal", MISSIONS + r"\dayzOffline.sakhal"),
    ),
    mods_root=r"P:\Mods",
    build_temp_root=r"P:\temp",
    build_source_basename=None,
)


def _junctions_unavailable() -> str | None:
    if sys.platform != "win32" or _winapi is None or not hasattr(_winapi, "CreateJunction"):
        return "directory junctions exist on Windows only"
    with tempfile.TemporaryDirectory() as probe:
        target = os.path.join(probe, "target")
        link = os.path.join(probe, "link")
        os.mkdir(target)
        try:
            _winapi.CreateJunction(target, link)
        except OSError as error:
            return f"this host cannot create a junction: {error}"
        os.rmdir(link)
    return None


def _is_junction(path: str) -> bool:
    # The reparse tag itself, not the worker's helper: the test must not agree
    # with the code under test by construction.
    try:
        return os.lstat(path).st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT
    except (OSError, AttributeError):
        return False


def _link_target(path: str) -> str:
    value = os.readlink(path)
    return value[4:] if value.startswith("\\\\?\\") else value


def _real(path: object) -> str:
    """The resolved folder a stage junction points at, case-folded for comparison."""
    return ntpath.normcase(os.path.realpath(os.fspath(path)))


def _snapshot(root: Path) -> dict[str, tuple[bool, int, int]]:
    """Every name under root with its kind, size and mtime; links are not followed.

    os.lstat, not DirEntry.stat: a listing carries the folder index's copy of
    the times, which NTFS refreshes later (measured: a folder's mtime moved by
    1 ms between two listings with nothing written in between).
    """
    found: dict[str, tuple[bool, int, int]] = {".": (True, 0, os.lstat(root).st_mtime_ns)}
    pending = [str(root)]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                info = os.lstat(entry.path)
                reparse = bool(info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
                is_dir = stat.S_ISDIR(info.st_mode)
                found[os.path.relpath(entry.path, root)] = (
                    is_dir,
                    0 if is_dir else info.st_size,
                    info.st_mtime_ns,
                )
                if is_dir and not reparse:
                    pending.append(entry.path)
    return found


def _stage_view(source: str) -> dict[str, tuple[bool, str, bool]]:
    """name -> (is a junction, where it points, reaches a folder) for each entry
    of the folder that holds the source AddonBuilder is given."""
    stage = ntpath.dirname(source)
    view: dict[str, tuple[bool, str, bool]] = {}
    for name in os.listdir(stage):
        path = ntpath.join(stage, name)
        junction = _is_junction(path)
        view[name] = (
            junction,
            ntpath.normcase(_link_target(path)) if junction else "",
            os.path.isdir(path),
        )
    return view


def _stage_folders(folder: str) -> set[str]:
    """The dayz-mcp-build-* names directly in folder; none when it cannot be listed."""
    try:
        return {name for name in os.listdir(folder) if name.startswith(STAGE_PREFIX)}
    except OSError:
        return set()


@contextlib.contextmanager
def _worker_folders(temp: str | None, cwd: str) -> Iterator[None]:
    """TEMP and the working folder the worker runs with, put back afterwards.

    The launcher starts the sealed worker in its private folder, with TEMP and
    TMP set to that same folder (launcher.cpp:600-602, :1311-1312, :1337-1340).
    """
    previous_temp = os.environ.get("TEMP")
    previous_cwd = os.getcwd()
    if temp is None:
        os.environ.pop("TEMP", None)
    else:
        os.environ["TEMP"] = temp
    os.chdir(cwd)
    try:
        yield
    finally:
        os.chdir(previous_cwd)
        if previous_temp is None:
            os.environ.pop("TEMP", None)
        else:
            os.environ["TEMP"] = previous_temp


class _Fixture:
    """A mod with references, next to the kinds of sibling a real parent holds.

    projects/
      StageMod/      the source: config.cpp, data/skin.rvmat, data/model.p3d,
                     cfg/base.hpp, scripts/notes.c
      LFHeli/        referenced as \\lfheli\\... by config.cpp (other case on disk)
      Other/         referenced as /Other/... by the .rvmat
      Embedded/      referenced from inside the binary .p3d
      Shared/        referenced by an #include in the .hpp
      NotScanned/    referenced only by the .c script, which is not scanned
      Unrelated/     not referenced, holds note.txt
      Poisoned/      not referenced, holds a config.cpp (valid and inert)
      DZ/, Scripts/  vanilla roots (Scripts in another case than the list's)
      NotADir        a file named like a reference
    private-temp/    stands for the launcher's private TEMP
    decoy-temp/      what tempfile.gettempdir() answers: the worker must not use it
    elsewhere/       a working folder that is not the private TEMP
    temp-file        a file where a folder is expected
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.parent = root / "projects"
        self.source = self.parent / MOD
        self.private_temp = root / "private-temp"
        self.decoy_temp = root / "decoy-temp"
        self.elsewhere = root / "elsewhere"
        self.temp_file = root / "temp-file"
        siblings = (
            "LFHeli", "Other", "Embedded", "Shared", "NotScanned",
            "Unrelated", "Poisoned", "DZ", "Scripts",
        )
        for folder in (
            self.source / "data",
            self.source / "cfg",
            self.source / "scripts",
            self.private_temp,
            self.decoy_temp,
            self.elsewhere,
            self.parent / "LFHeli" / "models",
            *(self.parent / name for name in siblings),
        ):
            folder.mkdir(parents=True, exist_ok=True)
        self.temp_file.write_bytes(b"a file, not a folder\n")
        (self.parent / "Unrelated" / "note.txt").write_bytes(b"outside the stage\n")
        (self.parent / "Poisoned" / "config.cpp").write_text(
            "// inert test fixture: a sibling the stage must leave out\n"
            "class CfgPatches {};\n",
            encoding="ascii",
        )
        (self.parent / "NotADir").write_bytes(b"a file, not a folder\n")
        (self.source / "config.cpp").write_text(
            "class CfgVehicles\n{\n"
            '    class StageHeli { model = "\\lfheli\\models\\heli.p3d"; };\n'
            '    class StageOwn { hiddenSelectionsTextures[] = {"\\StageMod\\data\\own_co.paa"}; };\n'
            '    class StageGhost { model = "\\Ghost\\models\\ghost.p3d"; };\n'
            '    class StageFile { model = "\\NotADir\\models\\nope.p3d"; };\n'
            "};\n",
            encoding="ascii",
        )
        (self.source / "data" / "skin.rvmat").write_text(
            'class Stage1 { texture = "/Other/data/t.paa"; };\n', encoding="ascii"
        )
        (self.source / "data" / "model.p3d").write_bytes(
            b"MLOD\x01\x01\x00\x00" + b"\x00\x00\x80\x3f" * 8
            + b"\x00\x00\x00\x00Embedded\\data\\skin_co.paa\x00\x00"
            + b"\xff\xfe\x10\x20"
        )
        (self.source / "cfg" / "base.hpp").write_text(
            '#include "\\Shared\\cfg\\base.hpp"\n', encoding="ascii"
        )
        (self.source / "scripts" / "notes.c").write_text(
            'string path = "\\NotScanned\\data\\x.paa";\n', encoding="ascii"
        )

    def expected_stage(
        self, *names: str, targets: dict[str, Path] | None = None
    ) -> dict[str, tuple[bool, str, bool]]:
        """The stage for this fixture, plus the extra roots a test adds by name."""
        expected = {MOD: (True, _real(self.source), True)}
        for name in (*EXPECTED_ROOTS, *names):
            target = (targets or {}).get(name, self.parent / name)
            expected[name] = (True, _real(target), True)
        return expected

    def policy(self) -> dayz_test_request.RequestProjectPolicy:
        return dayz_test_request.RequestProjectPolicy(
            mod=MOD,
            dev_root=DEV_ROOT,
            default_source=str(self.source),
            default_base_mods=(),
            mission_roots=(MISSIONS,),
            mod_roots=(r"P:\Mods",),
        )


class _RecordingBroker:
    """Answers the AddonBuilder frame and the server start the worker sends after it.

    At the AddonBuilder frame it records the payload and what the folder of the
    source it names holds at that moment, which is when binarize walks it.
    """

    def __init__(
        self,
        *,
        build_result: dict[str, object] | None = None,
        build_error: BaseException | None = None,
    ) -> None:
        self.kinds: list[native_broker_protocol.BrokerKind] = []
        self.addon_payloads: list[dict[str, object]] = []
        self.stage_views: list[dict[str, tuple[bool, str, bool]]] = []
        self.build_result = dict(BUILT if build_result is None else build_result)
        self.build_error = build_error

    async def invoke(self, frame: bytes) -> dict[str, object]:
        request = native_broker_protocol.decode_request(frame)
        self.kinds.append(request.kind)
        if request.kind is native_broker_protocol.BrokerKind.ADDON_BUILDER:
            self.addon_payloads.append(dict(request.payload))
            self.stage_views.append(_stage_view(str(request.payload["source"])))
            if self.build_error is not None:
                raise self.build_error
            return dict(self.build_result)
        if request.payload.get("command") in {"start", "ack"}:
            return {"ok": True, "state": "RUNNING", "run_id": request.payload["run_id"]}
        return {"ok": True}


class _StageTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reason = _junctions_unavailable()
        if reason is not None:
            raise unittest.SkipTest(reason)

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory(prefix="dayz-mcp-stage-test-")
        self.addCleanup(holder.cleanup)
        self.fixture = _Fixture(Path(holder.name))
        # A worker that asked tempfile would get the decoy, never a real folder.
        patcher = mock.patch.object(tempfile, "tempdir", str(self.fixture.decoy_temp))
        patcher.start()
        self.addCleanup(patcher.stop)
        # A test process keeps the shared build lock under tempfile's folder,
        # here the decoy; the lock gets its own folder so the decoy shows only
        # a misplaced stage.
        shared_lock_root = self.fixture.root / "shared-lock"
        shared_lock_root.mkdir()
        self.enterContext(
            mock.patch.dict(os.environ, {"DAYZ_MCP_SHARED_ROOT": str(shared_lock_root)})
        )
        # The worker runs in the launcher's private folder, with TEMP set to it.
        private_temp = str(self.fixture.private_temp)
        self.enterContext(_worker_folders(private_temp, private_temp))
        self.before = _snapshot(self.fixture.parent)

    def _execute(
        self,
        broker: _RecordingBroker,
        *,
        has_assets: Callable[[str], bool] | None = None,
        **overrides: object,
    ) -> dayz_test_worker.WorkerResult:
        policy = self.fixture.policy()
        request: dict[str, object] = {
            "version": 1,
            "dev_root": DEV_ROOT,
            "mod": MOD,
            "mode": "server",
            "build": True,
            "source": str(self.fixture.source),
        }
        request.update(overrides)
        parsed = dayz_test_request.parse_dayz_test_request(
            json.dumps(request).encode("utf-8"), policies=(policy,)
        )
        ids = iter((RUN_ID, OPERATION_ID))
        # The real asset scan by default: the fixture holds a .p3d and an .rvmat.
        scan = {} if has_assets is None else {"has_binarizable_assets": has_assets}
        return asyncio.run(
            dayz_test_worker.execute_dayz_test_worker(
                parsed.canonical_bytes,
                request_sha256=parsed.sha256,
                request_policies=(policy,),
                runtime_policy=RUNTIME,
                broker=broker,
                id_fn=lambda: next(ids),
                **scan,
            )
        )

    def _stage_of(self, broker: _RecordingBroker) -> str:
        self.assertEqual(len(broker.addon_payloads), 1, broker.addon_payloads)
        return ntpath.dirname(str(broker.addon_payloads[0]["source"]))

    def _junction(self, link: Path, target: Path) -> None:
        """A junction inside the test's folder, removed with os.rmdir at the end."""
        _winapi.CreateJunction(str(target), str(link))
        self.addCleanup(os.rmdir, str(link))
        self.before = _snapshot(self.fixture.parent)

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="ascii")
        self.before = _snapshot(self.fixture.parent)

    def assert_trees_untouched(self) -> None:
        self.assertEqual(_snapshot(self.fixture.parent), self.before)

    def assert_no_stage_left(self) -> None:
        self.assertEqual(os.listdir(self.fixture.private_temp), [])
        self.assertEqual(os.listdir(self.fixture.decoy_temp), [])


class BuildStageFrameTest(_StageTestCase):
    def test_binarize_build_runs_from_a_stage_of_junctions_gone_after_the_call(self) -> None:
        broker = _RecordingBroker()

        with mock.patch.object(tempfile, "mkdtemp", wraps=tempfile.mkdtemp) as mkdtemp:
            result = self._execute(broker)

        self.assertEqual(result, dayz_test_worker.WorkerResult(0, RUN_ID))
        self.assertEqual(
            broker.kinds,
            [
                native_broker_protocol.BrokerKind.ADDON_BUILDER,
                native_broker_protocol.BrokerKind.LIFECYCLE_CLI,
                native_broker_protocol.BrokerKind.LIFECYCLE_CLI,
            ],
        )
        stage = self._stage_of(broker)
        self.assertNotEqual(ntpath.normcase(stage), ntpath.normcase(str(self.fixture.parent)))
        # <stage>\<basename>, the stage a fresh folder directly in the private
        # TEMP, made there by name and nowhere else.
        self.assertEqual(
            ntpath.normcase(ntpath.dirname(stage)),
            ntpath.normcase(str(self.fixture.private_temp)),
        )
        self.assertEqual(
            [call.kwargs.get("dir") for call in mkdtemp.call_args_list],
            [str(self.fixture.private_temp)],
        )
        payload = broker.addon_payloads[0]
        self.assertEqual(payload["source"], ntpath.join(stage, MOD))
        # Everything else in the frame is what it was.
        self.assertEqual(
            {key: value for key, value in payload.items() if key != "source"},
            {
                "clear": False,
                "pack_only": False,
                "prefix": MOD,
                "target": r"P:\Mods\@StageMod\Addons",
                "temp": r"P:\temp\StageMod",
            },
        )
        # Only junctions, each reaching its own folder: the mod, the vanilla
        # roots present and the referenced roots. Not Unrelated, not Poisoned
        # and its config.cpp, not NotScanned, not the Ghost or NotADir names.
        self.assertEqual(broker.stage_views, [self.fixture.expected_stage()])
        self.assertFalse(os.path.lexists(stage))
        self.assert_no_stage_left()
        self.assert_trees_untouched()

    def test_pack_only_builds_the_source_itself_and_makes_no_stage(self) -> None:
        for label, overrides, assets in (
            ("explicit pack_only", {"pack_only": True}, True),
            ("no binarizable assets", {}, False),
        ):
            with self.subTest(label):
                broker = _RecordingBroker()

                result = self._execute(
                    broker, has_assets=lambda _source, value=assets: value, **overrides
                )

                self.assertEqual(result, dayz_test_worker.WorkerResult(0, RUN_ID))
                self.assertEqual(len(broker.addon_payloads), 1)
                self.assertEqual(broker.addon_payloads[0]["source"], str(self.fixture.source))
                self.assertIs(broker.addon_payloads[0]["pack_only"], True)
                self.assert_no_stage_left()
                self.assert_trees_untouched()


class ReferencedRootTest(_StageTestCase):
    def test_the_plan_is_the_mod_then_the_sorted_roots_on_disk(self) -> None:
        plan = dayz_test_worker._build_stage_plan(str(self.fixture.source))

        self.assertEqual(
            [(name, ntpath.normcase(target)) for name, target in plan],
            [(MOD, _real(self.fixture.source))]
            + [(name, _real(self.fixture.parent / name)) for name in EXPECTED_ROOTS],
        )
        # The mod's own \StageMod\... reference does not add it a second time.
        self.assertEqual(len({name.casefold() for name, _target in plan}), len(plan))
        self.assert_trees_untouched()

    def test_each_reference_form_names_its_root(self) -> None:
        cases = (
            ("config.cpp", b'model = "\\LFHeli\\models\\x.p3d";\n', "lfheli"),
            ("data/a.rvmat", b'texture = "/Other/data/t.paa";\n', "other"),
            ("data/b.p3d", b"\x00\x02\x00\x00Embedded\\data\\x_co.paa\x00\x9c", "embedded"),
            ("cfg/c.hpp", b'#include "\\Shared\\cfg\\base.hpp"\n', "shared"),
            ("model.cfg", b'skeletonName = "x"; file = "P:\\DZ\\anims\\a.rtm";\n', "dz"),
        )
        for relative, data, root in cases:
            with self.subTest(relative):
                source = self.fixture.root / ("case-" + root) / MOD
                (source / relative).parent.mkdir(parents=True, exist_ok=True)
                (source / relative).write_bytes(data)
                roots, links = dayz_test_worker._scan_tree(str(source))
                self.assertEqual(roots, {root})
                self.assertEqual(links, ())

    def test_what_is_not_an_asset_path_of_a_scanned_file_names_no_root(self) -> None:
        parent = self.fixture.root / "plain"
        source = parent / MOD
        source.mkdir(parents=True)
        for name in ("Docs", "Backup", "NotScanned", "Loose"):
            (parent / name).mkdir()
        (source / "config.cpp").write_bytes(
            b"// no separator: Loose.paa\n"
            b"// not an asset suffix: \\Docs\\readme.txt\n"
            b"// a longer suffix: \\Backup\\skin.paa.bak\n"
        )
        (source / "notes.c").write_bytes(b'"\\NotScanned\\x.paa"\n')

        roots, _links = dayz_test_worker._scan_tree(str(source))

        self.assertEqual(roots & {"docs", "backup", "notscanned", "loose", "loose.paa"}, set())
        self.assertEqual(
            [(name, ntpath.normcase(target)) for name, target in
             dayz_test_worker._build_stage_plan(str(source))],
            [(MOD, _real(source))],
        )


class BuildStageCleanupTest(_StageTestCase):
    def test_a_broker_error_still_removes_the_stage(self) -> None:
        broker = _RecordingBroker(build_error=RuntimeError("pipe closed"))

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._execute(broker)

        self.assertEqual(raised.exception.code, "worker_failed")
        self.assertEqual(broker.stage_views, [self.fixture.expected_stage()])
        self.assertFalse(os.path.lexists(self._stage_of(broker)))
        self.assert_no_stage_left()
        self.assert_trees_untouched()

    def test_a_failed_build_still_removes_the_stage(self) -> None:
        broker = _RecordingBroker(build_result=FAILED_BUILD)

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._execute(broker)

        self.assertEqual(raised.exception.code, "build_failed")
        self.assertEqual(broker.kinds, [native_broker_protocol.BrokerKind.ADDON_BUILDER])
        self.assertEqual(broker.stage_views, [self.fixture.expected_stage()])
        self.assertFalse(os.path.lexists(self._stage_of(broker)))
        self.assert_no_stage_left()
        self.assert_trees_untouched()

    def test_a_cancelled_build_still_removes_the_stage(self) -> None:
        broker = _RecordingBroker(build_error=asyncio.CancelledError())

        with self.assertRaises(asyncio.CancelledError):
            self._execute(broker)

        self.assertEqual(broker.stage_views, [self.fixture.expected_stage()])
        self.assertFalse(os.path.lexists(self._stage_of(broker)))
        self.assert_no_stage_left()
        self.assert_trees_untouched()

    def _refuse_link_removal(self) -> list[str]:
        """os.rmdir refuses every junction in the private TEMP until the test ends."""
        real_rmdir = os.rmdir
        private_temp = ntpath.normcase(str(self.fixture.private_temp))
        refused: list[str] = []

        def rmdir(path: object, *args: object, **kwargs: object) -> None:
            text = os.fspath(path)
            if _is_junction(text) and ntpath.normcase(text).startswith(private_temp):
                refused.append(text)
                raise PermissionError(13, "simulated: the link is in use", text)
            real_rmdir(path, *args, **kwargs)

        def remove_leftover_stage() -> None:
            # Links first, with the real os.rmdir, which removes a link and never
            # its target; then the emptied stage.
            for stage in os.listdir(self.fixture.private_temp):
                stage_path = os.path.join(self.fixture.private_temp, stage)
                for name in os.listdir(stage_path):
                    real_rmdir(os.path.join(stage_path, name))
                real_rmdir(stage_path)

        self.addCleanup(remove_leftover_stage)
        patcher = mock.patch.object(os, "rmdir", rmdir)
        patcher.start()
        self.addCleanup(patcher.stop)
        return refused

    def test_a_link_that_cannot_be_removed_does_not_mask_a_success(self) -> None:
        refused = self._refuse_link_removal()
        broker = _RecordingBroker()

        result = self._execute(broker)

        self.assertEqual(result, dayz_test_worker.WorkerResult(0, RUN_ID))
        self.assertEqual(len(refused), 1 + len(EXPECTED_ROOTS))
        # The stage is left to its owner, the cleanup of the private TEMP.
        self.assertTrue(os.path.isdir(self._stage_of(broker)))
        self.assert_trees_untouched()

    def test_a_link_that_cannot_be_removed_does_not_mask_a_failure(self) -> None:
        refused = self._refuse_link_removal()
        broker = _RecordingBroker(build_result=FAILED_BUILD)

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._execute(broker)

        self.assertEqual(raised.exception.code, "build_failed")
        self.assertEqual(len(refused), 1 + len(EXPECTED_ROOTS))
        self.assert_trees_untouched()


class BuildStageFailsClosedTest(_StageTestCase):
    def _assert_refused_before_the_broker(
        self,
        broker: _RecordingBroker,
        error: dayz_test_worker.DayzTestWorkerError,
        code: str,
    ) -> None:
        self.assertEqual(error.code, code)
        self.assertEqual(broker.kinds, [])
        self.assert_no_stage_left()
        self.assert_trees_untouched()

    def test_a_junction_that_cannot_be_created_fails_before_the_broker(self) -> None:
        real_create = _winapi.CreateJunction
        calls: list[str] = []

        def create(target: str, link: str) -> None:
            calls.append(link)
            if len(calls) == 2:
                raise PermissionError(5, "simulated: access is denied", link)
            real_create(target, link)

        broker = _RecordingBroker()
        with mock.patch.object(_winapi, "CreateJunction", create):
            with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                self._execute(broker)

        # The mod's junction was made, the next one failed: both are gone.
        self.assertEqual(len(calls), 2)
        self._assert_refused_before_the_broker(
            broker, raised.exception, "build_stage_unavailable"
        )

    def test_a_link_that_is_not_the_junction_asked_for_fails_closed(self) -> None:
        real_create = _winapi.CreateJunction
        elsewhere = str(self.fixture.parent / "Unrelated")
        for label, create in (
            ("a plain folder", lambda _target, link: os.mkdir(link)),
            ("another target", lambda _target, link: real_create(elsewhere, link)),
        ):
            with self.subTest(label):
                broker = _RecordingBroker()
                with mock.patch.object(_winapi, "CreateJunction", create):
                    with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                        self._execute(broker)
                self._assert_refused_before_the_broker(
                    broker, raised.exception, "build_stage_unavailable"
                )

    def test_a_file_the_scan_cannot_read_fails_before_the_broker(self) -> None:
        unreadable = _real(self.fixture.source / "data" / "skin.rvmat")
        real_open = open

        def guarded_open(file: object, *args: object, **kwargs: object) -> object:
            if isinstance(file, (str, os.PathLike)) and _real(file) == unreadable:
                raise PermissionError(13, "simulated: the file is locked", os.fspath(file))
            return real_open(file, *args, **kwargs)

        broker = _RecordingBroker()
        with mock.patch("builtins.open", guarded_open):
            with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                self._execute(broker)

        self._assert_refused_before_the_broker(
            broker, raised.exception, "build_source_unavailable"
        )

    def test_a_parent_that_cannot_be_listed_fails_before_the_broker(self) -> None:
        parent = ntpath.normcase(str(self.fixture.parent))
        real_scandir = os.scandir

        def scandir(path: object = ".") -> object:
            if isinstance(path, (str, os.PathLike)) and ntpath.normcase(os.fspath(path)) == parent:
                raise PermissionError(13, "simulated: access is denied", os.fspath(path))
            return real_scandir(path)

        broker = _RecordingBroker()
        with mock.patch.object(os, "scandir", scandir):
            with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                self._execute(broker)

        # Asserted once os.scandir is real again: the snapshot lists the parent too.
        self._assert_refused_before_the_broker(
            broker, raised.exception, "build_source_unavailable"
        )


class PrivateTempTest(_StageTestCase):
    """Review R1, F1: the stage goes into the launcher's private TEMP or nowhere.

    tempfile.gettempdir() tries other folders when TEMP is unusable, and a stage
    there, with its junctions to real folders, could outlive a failed cleanup
    outside the folder its owner removes.
    """

    def _fallback_folders(self) -> set[str]:
        # Every folder tempfile would try, the working folder, and the decoy.
        return {
            *tempfile._candidate_tempdir_list(),
            os.getcwd(),
            str(self.fixture.decoy_temp),
            str(self.fixture.private_temp),
        }

    def test_an_unusable_private_temp_fails_closed_and_makes_no_stage_anywhere(self) -> None:
        private_temp = str(self.fixture.private_temp)
        cases = (
            ("TEMP unset", None, private_temp),
            ("TEMP missing", str(self.fixture.root / "no-such-temp"), private_temp),
            ("TEMP a file", str(self.fixture.temp_file), private_temp),
            ("TEMP not the working folder", private_temp, str(self.fixture.elsewhere)),
            ("TEMP not a local path", "relative-temp", private_temp),
        )
        for label, temp, cwd in cases:
            with self.subTest(label), _worker_folders(temp, cwd):
                folders = self._fallback_folders()
                before = {folder: _stage_folders(folder) for folder in folders}
                broker = _RecordingBroker()

                with mock.patch.object(tempfile, "mkdtemp", wraps=tempfile.mkdtemp) as mkdtemp:
                    with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                        self._execute(broker)

                self.assertEqual(raised.exception.code, "build_stage_unavailable")
                self.assertEqual(broker.kinds, [])
                self.assertEqual(mkdtemp.call_args_list, [])
                self.assertEqual(
                    {folder: _stage_folders(folder) - before[folder] for folder in folders},
                    {folder: set() for folder in folders},
                )
                self.assert_no_stage_left()
                self.assert_trees_untouched()

    def test_a_stage_the_private_temp_cannot_hold_fails_closed(self) -> None:
        broker = _RecordingBroker()
        refusal = PermissionError(5, "simulated: access is denied")

        with mock.patch.object(tempfile, "mkdtemp", side_effect=refusal) as mkdtemp:
            with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                self._execute(broker)

        self.assertEqual(raised.exception.code, "build_stage_unavailable")
        self.assertEqual(
            [call.kwargs.get("dir") for call in mkdtemp.call_args_list],
            [str(self.fixture.private_temp)],
        )
        self.assertEqual(broker.kinds, [])
        self.assert_no_stage_left()
        self.assert_trees_untouched()


class SourceLinkTest(_StageTestCase):
    """Review R1, F2: a link in the source leads only into the source or a staged root.

    binarize follows links, so a junction inside the mod that leads to its
    parent would put every sibling, and every config.cpp in them, back under
    <stage>\\<basename>. The asset scan is given here: Path.rglob, which decides
    pack_only, follows a junction loop until Windows' path limits, and it is not
    what these tests are about.
    """

    def _assert_refused(self, broker: _RecordingBroker) -> None:
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._execute(broker, has_assets=lambda _source: True)
        self.assertEqual(raised.exception.code, "build_source_link_outside")
        self.assertEqual(broker.kinds, [])
        self.assert_no_stage_left()
        self.assert_trees_untouched()

    def test_a_junction_to_the_parent_is_refused_before_the_broker(self) -> None:
        # The parent holds Poisoned\config.cpp and every other sibling.
        self._junction(self.fixture.source / "parent_alias", self.fixture.parent)

        self._assert_refused(_RecordingBroker())

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            dayz_test_worker._build_stage_plan(str(self.fixture.source))
        self.assertEqual(raised.exception.code, "build_source_link_outside")

    def test_a_junction_to_a_sibling_the_stage_leaves_out_is_refused(self) -> None:
        self._junction(
            self.fixture.source / "data" / "unrelated_alias",
            self.fixture.parent / "Unrelated",
        )

        self._assert_refused(_RecordingBroker())

    def test_junctions_inside_the_mod_or_into_a_staged_root_are_allowed(self) -> None:
        source, parent = self.fixture.source, self.fixture.parent
        self._junction(source / "data_alias", source / "data")
        self._junction(source / "itself", source)
        self._junction(source / "dz_alias", parent / "DZ")
        self._junction(source / "data" / "heli_models", parent / "LFHeli" / "models")
        broker = _RecordingBroker()

        result = self._execute(broker, has_assets=lambda _source: True)

        self.assertEqual(result, dayz_test_worker.WorkerResult(0, RUN_ID))
        # The links add no root: the stage is the one the mod's references make.
        self.assertEqual(broker.stage_views, [self.fixture.expected_stage()])
        self.assert_no_stage_left()
        self.assert_trees_untouched()

    def test_a_symbolic_link_out_of_the_stage_is_refused(self) -> None:
        outside = self.fixture.parent / "Unrelated"
        for label, link, target, is_dir in (
            ("to a file", self.fixture.source / "note.txt", outside / "note.txt", False),
            ("to a folder", self.fixture.source / "unrelated_dir", outside, True),
        ):
            with self.subTest(label):
                try:
                    os.symlink(str(target), str(link), target_is_directory=is_dir)
                except OSError as error:
                    self.skipTest(f"this host cannot create a symbolic link: {error}")
                try:
                    self.before = _snapshot(self.fixture.parent)
                    self._assert_refused(_RecordingBroker())
                finally:
                    # The link, never its target.
                    (os.rmdir if is_dir else os.unlink)(str(link))


class ClosureTest(_StageTestCase):
    """Review R2: every root is checked before any stage exists, and the plan is a closure.

    F1: a root that is itself a junction in the parent (Bridge -> <parent>) put
    every sibling back under the stage. F2: a link from the mod into an allowed
    root hid that root's own references (First naming \\Second\\...). The
    vanilla roots are DayZ Tools' own extraction and are never read.
    """

    def _assert_refused(self, broker: _RecordingBroker) -> None:
        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._execute(broker)
        self.assertEqual(raised.exception.code, "build_source_link_outside")
        self.assertEqual(broker.kinds, [])
        self.assert_no_stage_left()
        self.assert_trees_untouched()

    def _assert_built(self, broker: _RecordingBroker, expected: dict[str, object]) -> None:
        result = self._execute(broker)
        self.assertEqual(result, dayz_test_worker.WorkerResult(0, RUN_ID))
        self.assertEqual(broker.stage_views, [expected])
        self.assert_no_stage_left()
        self.assert_trees_untouched()

    def test_a_root_that_is_a_junction_to_the_parent_is_refused_before_the_broker(self) -> None:
        self._write(
            self.fixture.source / "cfg" / "bridge.hpp", '#include "\\Bridge\\cfg\\bridge.hpp"\n'
        )
        self._junction(self.fixture.parent / "Bridge", self.fixture.parent)

        self._assert_refused(_RecordingBroker())

    def test_a_vanilla_root_that_is_a_junction_to_the_parent_or_above_is_refused(self) -> None:
        for name, target in (("gui", self.fixture.parent), ("system", self.fixture.root)):
            with self.subTest(name):
                link = self.fixture.parent / name
                _winapi.CreateJunction(str(target), str(link))
                try:
                    self.before = _snapshot(self.fixture.parent)
                    self._assert_refused(_RecordingBroker())
                finally:
                    os.rmdir(str(link))

    def test_a_source_that_resolves_to_its_parent_is_refused(self) -> None:
        loop = self.fixture.parent / "LoopMod"
        self._junction(loop, self.fixture.parent)

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            dayz_test_worker._build_stage_plan(str(loop))

        self.assertEqual(raised.exception.code, "build_source_link_outside")
        self.assert_trees_untouched()

    def test_a_source_reached_through_a_junction_guards_the_parent_it_resolves_in(self) -> None:
        # projects\JunctionMod -> real-projects\RealMod, whose neighbours are
        # the siblings that a root resolving to real-projects would expose.
        real_parent = self.fixture.root / "real-projects"
        real_mod = real_parent / "RealMod"
        self._write(real_parent / "Neighbour" / "config.cpp", "class CfgPatches {};\n")
        self._write(real_mod / "config.cpp", 'class M { model = "\\RealParent\\m.p3d"; };\n')
        source = self.fixture.parent / "JunctionMod"
        self._junction(source, real_mod)
        self._junction(self.fixture.parent / "RealParent", real_parent)

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            dayz_test_worker._build_stage_plan(str(source))
        self.assertEqual(raised.exception.code, "build_source_link_outside")

        # Without that reference the source is staged where it resolves.
        self._write(real_mod / "config.cpp", 'class M { model = "\\JunctionMod\\m.p3d"; };\n')
        plan = dayz_test_worker._build_stage_plan(str(source))
        self.assertEqual(plan[0][0], "JunctionMod")
        self.assertEqual(ntpath.normcase(plan[0][1]), _real(real_mod))
        self.assertNotIn("RealParent", {name for name, _target in plan})
        self.assert_trees_untouched()

    def test_a_link_into_a_staged_root_brings_that_roots_own_references(self) -> None:
        source, parent = self.fixture.source, self.fixture.parent
        self._write(source / "data" / "first.rvmat", 'class F { texture = "\\First\\data\\f.paa"; };\n')
        self._write(
            parent / "First" / "config.cpp",
            'class CfgVehicles { class StageFirst { model = "\\Second\\models\\y.p3d"; }; };\n',
        )
        (parent / "Second" / "models").mkdir(parents=True)
        self._junction(source / "alias", parent / "First")

        self._assert_built(_RecordingBroker(), self.fixture.expected_stage("First", "Second"))

    def test_the_closure_follows_references_root_by_root(self) -> None:
        source, parent = self.fixture.source, self.fixture.parent
        self._write(source / "data" / "level.rvmat", 'class L { texture = "\\Level1\\data\\a.paa"; };\n')
        self._write(parent / "Level1" / "data" / "next.rvmat", 'class L { texture = "\\Level2\\b.paa"; };\n')
        self._write(parent / "Level2" / "cfg" / "next.hpp", '#include "\\Level3\\cfg\\c.hpp"\n')
        (parent / "Level3").mkdir()
        self.before = _snapshot(parent)

        self._assert_built(
            _RecordingBroker(), self.fixture.expected_stage("Level1", "Level2", "Level3")
        )

    def test_a_link_inside_a_staged_root_to_a_left_out_sibling_is_refused(self) -> None:
        self._junction(self.fixture.parent / "LFHeli" / "escape", self.fixture.parent / "Unrelated")

        self._assert_refused(_RecordingBroker())

    def test_a_root_that_is_a_junction_elsewhere_is_staged_under_its_own_name(self) -> None:
        source, parent = self.fixture.source, self.fixture.parent
        remote = self.fixture.root / "assets" / "RemoteData"
        self._write(remote / "remote.rvmat", 'class R { texture = "\\Second\\data\\r.paa"; };\n')
        (parent / "Second").mkdir()
        self._write(source / "data" / "remote.rvmat", 'class M { texture = "\\Remote\\data\\m.paa"; };\n')
        self._junction(parent / "Remote", remote)

        # Remote keeps its name and points at the folder that was checked; its
        # own files are scanned there, which brings Second.
        self._assert_built(
            _RecordingBroker(),
            self.fixture.expected_stage("Remote", "Second", targets={"Remote": remote}),
        )

    def test_each_tree_is_read_once_and_no_vanilla_root_is_read(self) -> None:
        source, parent = self.fixture.source, self.fixture.parent
        # Read, these vanilla files would bring Unrelated and Poisoned.
        self._write(parent / "DZ" / "data" / "dz.rvmat", 'class D { texture = "\\Unrelated\\u.paa"; };\n')
        self._write(parent / "Scripts" / "config.cpp", 'class CfgPatches { model = "\\Poisoned\\p.p3d"; };\n')
        # Shared is named as Shared and as SharedAlias, and a link in the mod
        # leads into it; Inner resolves inside the mod.
        self._write(parent / "Shared" / "shared.rvmat", 'class S { texture = "\\Other\\o.paa"; };\n')
        self._write(
            source / "data" / "alias.rvmat",
            'class A { texture = "\\SharedAlias\\s.paa"; other = "\\Inner\\i.paa"; };\n',
        )
        self._junction(parent / "SharedAlias", parent / "Shared")
        self._junction(parent / "Inner", source / "data")
        self._junction(source / "shared_link", parent / "Shared")
        opened: collections.Counter[str] = collections.Counter()
        real_open = open

        def counting_open(file: object, *args: object, **kwargs: object) -> object:
            if isinstance(file, (str, os.PathLike)):
                opened[_real(file)] += 1
            return real_open(file, *args, **kwargs)

        with mock.patch("builtins.open", counting_open):
            plan = dayz_test_worker._build_stage_plan(str(source))

        self.assertEqual(
            dict(opened),
            {
                _real(path): 1
                for path in (
                    source / "config.cpp",
                    source / "cfg" / "base.hpp",
                    source / "data" / "skin.rvmat",
                    source / "data" / "model.p3d",
                    source / "data" / "alias.rvmat",
                    parent / "Shared" / "shared.rvmat",
                )
            },
        )
        names = {name for name, _target in plan}
        self.assertTrue({"Shared", "SharedAlias", "Inner", "DZ", "Scripts"} <= names, names)
        self.assertFalse({"Unrelated", "Poisoned"} & names, names)
        self.assert_trees_untouched()


class LinkKindTest(unittest.TestCase):
    def test_only_junctions_and_symbolic_links_are_links(self) -> None:
        # A OneDrive placeholder is a reparse point too, with a cloud tag: SimpleGroup
        # held 22 of them on 2026-10-01, and they are files to read, not links.
        cloud = 0x9000001A
        for label, mode, tag, expected in (
            ("junction", stat.S_IFDIR, stat.IO_REPARSE_TAG_MOUNT_POINT, True),
            ("symbolic link", stat.S_IFLNK, stat.IO_REPARSE_TAG_SYMLINK, True),
            ("OneDrive file placeholder", stat.S_IFREG, cloud, False),
            ("OneDrive folder placeholder", stat.S_IFDIR, cloud | 0x1000, False),
            ("plain file", stat.S_IFREG, 0, False),
            ("plain folder", stat.S_IFDIR, 0, False),
        ):
            with self.subTest(label):
                info = types.SimpleNamespace(st_mode=mode | 0o644, st_reparse_tag=tag)
                self.assertIs(dayz_test_worker._is_link(info), expected)


class StageCodesTest(unittest.TestCase):
    def test_the_stage_codes_reach_the_caller_unchanged(self) -> None:
        for code in ("build_stage_unavailable", "build_source_link_outside"):
            with self.subTest(code):
                self.assertIn(code, dayz_test_worker.WORKER_ERROR_CODES)
                dayz_test_worker.DayzTestWorkerError(code)
                terminal = _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": code,
                        "exit_code": 2,
                        "ok": False,
                        "run_id": None,
                    }
                )
                parsed = dayz_test_tool.parse_worker_terminal(terminal, b"", 2)
                self.assertEqual(parsed.error_code, code)


if __name__ == "__main__":
    unittest.main()
