"""inbox bf5c + 8cf9 (fb-20260930-180331-bf5c, fb-20260930-214647-8cf9): the stage.

AddonBuilder hands binarize -addon="<parent of the source>", and binarize parses
every config.cpp under that folder, through junctions too. A broken fixture
next to the source failed every build from 24 to 30 September, and a
SimpleGroup build walked hundreds of sibling projects for about 4 minutes.

When binarize runs (pack_only false) the sealed worker now builds from
<stage>\\<basename>, where <stage> is a fresh folder in its private TEMP that
holds only junctions: the mod, the vanilla roots DayZ Tools extracts into the
mod's parent, and the parent's folders the mod's own files reference. These
tests drive the real worker with real junctions in temporary folders and a
broker that records the AddonBuilder frame. No fixture holds a broken
config.cpp, and no junction ever points outside the test's own folder.
"""

from __future__ import annotations

import asyncio
import json
import ntpath
import os
import stat
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from dayz_mcp import dayz_test_request, dayz_test_worker, native_broker_protocol

try:
    import _winapi
except ImportError:  # not Windows: every test here skips
    _winapi = None


MOD = "StageMod"
DEV_ROOT = r"P:\StageMod_Suite"
MISSIONS = r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions"
RUN_ID = "12345678-1234-4234-8234-1234567890ab"
OPERATION_ID = "87654321-4321-4321-8321-ba0987654321"
BUILT = {"ok": True, "exit_code": 0, "pbo_size": 8192}
FAILED_BUILD = {"ok": False, "exit_code": 1, "pbo_size": 0}
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
      Unrelated/     not referenced
      Poisoned/      not referenced, holds a config.cpp (valid and inert)
      DZ/, Scripts/  vanilla roots (Scripts in another case than the list's)
      NotADir        a file named like a reference
    private-temp/    stands for the launcher's private TEMP
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.parent = root / "projects"
        self.source = self.parent / MOD
        self.private_temp = root / "private-temp"
        siblings = (
            "LFHeli", "Other", "Embedded", "Shared", "NotScanned",
            "Unrelated", "Poisoned", "DZ", "Scripts",
        )
        for folder in (
            self.source / "data",
            self.source / "cfg",
            self.source / "scripts",
            self.private_temp,
            self.parent / "LFHeli" / "models",
            *(self.parent / name for name in siblings),
        ):
            folder.mkdir(parents=True, exist_ok=True)
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

    def expected_stage(self) -> dict[str, tuple[bool, str, bool]]:
        expected = {MOD: (True, ntpath.normcase(str(self.source)), True)}
        for name in EXPECTED_ROOTS:
            expected[name] = (True, ntpath.normcase(str(self.parent / name)), True)
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
        # The worker's TEMP is the launcher's private folder; here it is ours.
        patcher = mock.patch.object(tempfile, "tempdir", str(self.fixture.private_temp))
        patcher.start()
        self.addCleanup(patcher.stop)
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

    def assert_trees_untouched(self) -> None:
        self.assertEqual(_snapshot(self.fixture.parent), self.before)

    def assert_private_temp_empty(self) -> None:
        self.assertEqual(os.listdir(self.fixture.private_temp), [])


class BuildStageFrameTest(_StageTestCase):
    def test_binarize_build_runs_from_a_stage_of_junctions_gone_after_the_call(self) -> None:
        broker = _RecordingBroker()

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
        # <stage>\<basename>, the stage a fresh folder directly in the worker's TEMP.
        self.assertEqual(
            ntpath.normcase(ntpath.dirname(stage)),
            ntpath.normcase(tempfile.gettempdir()),
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
        self.assert_private_temp_empty()
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
                self.assert_private_temp_empty()
                self.assert_trees_untouched()


class ReferencedRootTest(_StageTestCase):
    def test_the_plan_is_the_mod_then_the_sorted_roots_on_disk(self) -> None:
        plan = dayz_test_worker._build_stage_plan(str(self.fixture.source))

        self.assertEqual(
            plan,
            ((MOD, str(self.fixture.source)),)
            + tuple((name, str(self.fixture.parent / name)) for name in EXPECTED_ROOTS),
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
                self.assertEqual(dayz_test_worker._referenced_roots(str(source)), {root})

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

        found = dayz_test_worker._referenced_roots(str(source))

        self.assertEqual(found & {"docs", "backup", "notscanned", "loose", "loose.paa"}, set())
        self.assertEqual(dayz_test_worker._build_stage_plan(str(source)), ((MOD, str(source)),))


class BuildStageCleanupTest(_StageTestCase):
    def test_a_broker_error_still_removes_the_stage(self) -> None:
        broker = _RecordingBroker(build_error=RuntimeError("pipe closed"))

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._execute(broker)

        self.assertEqual(raised.exception.code, "worker_failed")
        self.assertEqual(broker.stage_views, [self.fixture.expected_stage()])
        self.assertFalse(os.path.lexists(self._stage_of(broker)))
        self.assert_private_temp_empty()
        self.assert_trees_untouched()

    def test_a_failed_build_still_removes_the_stage(self) -> None:
        broker = _RecordingBroker(build_result=FAILED_BUILD)

        with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
            self._execute(broker)

        self.assertEqual(raised.exception.code, "build_failed")
        self.assertEqual(broker.kinds, [native_broker_protocol.BrokerKind.ADDON_BUILDER])
        self.assertEqual(broker.stage_views, [self.fixture.expected_stage()])
        self.assertFalse(os.path.lexists(self._stage_of(broker)))
        self.assert_private_temp_empty()
        self.assert_trees_untouched()

    def test_a_cancelled_build_still_removes_the_stage(self) -> None:
        broker = _RecordingBroker(build_error=asyncio.CancelledError())

        with self.assertRaises(asyncio.CancelledError):
            self._execute(broker)

        self.assertEqual(broker.stage_views, [self.fixture.expected_stage()])
        self.assertFalse(os.path.lexists(self._stage_of(broker)))
        self.assert_private_temp_empty()
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
        self, broker: _RecordingBroker, error: dayz_test_worker.DayzTestWorkerError
    ) -> None:
        self.assertEqual(error.code, "build_source_unavailable")
        self.assertEqual(broker.kinds, [])
        self.assert_private_temp_empty()
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
        self._assert_refused_before_the_broker(broker, raised.exception)

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
                self._assert_refused_before_the_broker(broker, raised.exception)

    def test_a_file_the_scan_cannot_read_fails_before_the_broker(self) -> None:
        unreadable = ntpath.normcase(str(self.fixture.source / "data" / "skin.rvmat"))
        real_open = open

        def guarded_open(file: object, *args: object, **kwargs: object) -> object:
            if isinstance(file, (str, os.PathLike)) and ntpath.normcase(os.fspath(file)) == unreadable:
                raise PermissionError(13, "simulated: the file is locked", os.fspath(file))
            return real_open(file, *args, **kwargs)

        broker = _RecordingBroker()
        with mock.patch("builtins.open", guarded_open):
            with self.assertRaises(dayz_test_worker.DayzTestWorkerError) as raised:
                self._execute(broker)

        self._assert_refused_before_the_broker(broker, raised.exception)

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
        self._assert_refused_before_the_broker(broker, raised.exception)


if __name__ == "__main__":
    unittest.main()
